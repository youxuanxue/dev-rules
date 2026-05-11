from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .worker import DEFAULT_WORKER_MAX_BUDGET_USD, WORKER_MAX_BUDGET_ENV
from .runtime import (
    apply_supervisor_review,
    build_review_context,
    build_supervisor_context,
    health_workspace,
    record_human_response,
    start_worker_turn,
    status_workspace,
)
from .validate import run_fixture_validation, validate_path
from .workspace import WorkspaceError


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _workspace_arg(args: argparse.Namespace) -> Path:
    value = getattr(args, "workspace", "") or ""
    if not value:
        raise WorkspaceError("workspace is required")
    return Path(value)


def _print_needs_human(status: dict[str, object]) -> None:
    needs_human = status.get("needs_human")
    if not isinstance(needs_human, dict):
        return
    workspace = Path(str(status["workspace"]))
    run_id = status.get("current_run_id") or "<run_id>"
    print("NEEDS_HUMAN")
    print(f"question={needs_human.get('question') or ''}")
    print(f"context={needs_human.get('context') or ''}")
    print(f"current={workspace / 'CURRENT.md'}")
    print(f"state={workspace / 'supervisor_state.json'}")
    print(f"run={workspace / 'runs' / str(run_id) / 'run.json'}")
    print(f"review={workspace / 'runs' / str(run_id) / 'supervisor_review.json'}")
    print(f"respond=python3 -m scripts.xuejiao_twin respond --workspace {workspace} --text '<answer>'")


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = status_workspace(_workspace_arg(args))
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json(status)
    else:
        print(f"workspace={status['workspace']}")
        print(f"status={status['status']}")
        print(f"goal={status['goal']}")
        print(f"current_item_id={status['current_item_id'] or 'none'}")
        print(f"round_index={status['round_index']}")
        print(f"next_instruction={status['next_instruction'] or 'none'}")
        print(f"current_run_id={status.get('current_run_id') or 'none'}")
        print(f"current={status['current']}")
        _print_needs_human(status)
    return 0


def _cmd_respond(args: argparse.Namespace) -> int:
    try:
        target = record_human_response(_workspace_arg(args), args.text)
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"human_response_written={target}")
    print(f"next=当前 Claude Code supervisor 读取 supervisor-context 后生成下一条 worker instruction")
    return 0


def _cmd_supervisor_context(args: argparse.Namespace) -> int:
    try:
        context = build_supervisor_context(_workspace_arg(args), getattr(args, "run_id", None))
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_json(context)
    return 0


def _cmd_worker_turn(args: argparse.Namespace) -> int:
    try:
        run = start_worker_turn(_workspace_arg(args), args.instruction, max_budget_usd=args.max_budget_usd)
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json(run)
    else:
        print(f"run_id={run['run_id']} outcome={run['outcome']} resume_used={run['worker']['resume_used']}")
        print(f"run={Path(args.workspace).expanduser().resolve() / 'runs' / run['run_id'] / 'run.json'}")
    return 0 if run["outcome"] == "review_required" else 1


def _cmd_review_context(args: argparse.Namespace) -> int:
    try:
        context = build_review_context(_workspace_arg(args), args.run_id)
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_json(context)
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    try:
        report = health_workspace(
            _workspace_arg(args),
            run_id=args.run_id,
            events_tail=args.events_tail,
            history_limit=args.history_limit,
        )
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json(report)
    else:
        status = report.get("status") if isinstance(report.get("status"), dict) else {}
        run = report.get("current_run") if isinstance(report.get("current_run"), dict) else {}
        health = report.get("run_health") if isinstance(report.get("run_health"), dict) else {}
        events = report.get("events_tail_summary") if isinstance(report.get("events_tail_summary"), dict) else {}
        tail_events = events.get("events") if isinstance(events.get("events"), list) else []
        last_event = tail_events[-1] if tail_events else {}
        print(f"workspace={report['workspace']}")
        print(f"status={status.get('status')}")
        print(f"current_run_id={report.get('current_run_id') or 'none'}")
        print(f"run_outcome={run.get('outcome') or 'none'}")
        print(f"requires_attention={health.get('requires_attention')}")
        print(f"quality_flags={','.join(health.get('quality_flags') or []) or 'none'}")
        print(f"events_last={last_event.get('type', 'none')}:{last_event.get('subtype', 'none')}")
        warnings = report.get("history_warnings") if isinstance(report.get("history_warnings"), list) else []
        print(f"history_warnings={len(warnings)}")
        for warning in warnings[:5]:
            print(f"warning={warning.get('flag') or warning.get('kind')} count={warning.get('count', '')} latest={warning.get('latest_run_id', '')}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    try:
        review = json.loads(Path(args.review_file).read_text(encoding="utf-8"))
        state = apply_supervisor_review(_workspace_arg(args), args.run_id, review)
        status = status_workspace(_workspace_arg(args))
    except (OSError, json.JSONDecodeError, WorkspaceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json(state)
    else:
        print(f"status={state['status']}")
        print(f"next_instruction={state.get('next_instruction') or 'none'}")
        _print_needs_human(status)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    errors = run_fixture_validation() if args.fixtures else validate_path(Path(args.path))
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("xuejiao_twin validation: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.xuejiao_twin")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("workspace_pos", nargs="?")
    p_status.add_argument("--workspace", dest="workspace")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=lambda args: _cmd_status(_merge_workspace(args)))

    p_respond = sub.add_parser("respond")
    p_respond.add_argument("text_pos", nargs="*")
    p_respond.add_argument("--workspace", required=True)
    p_respond.add_argument("--text", default="")
    p_respond.set_defaults(func=lambda args: _cmd_respond(_merge_text(args)))

    p_context = sub.add_parser("supervisor-context")
    p_context.add_argument("--workspace", required=True)
    p_context.add_argument("--run-id")
    p_context.set_defaults(func=_cmd_supervisor_context)

    p_worker = sub.add_parser("worker-turn")
    p_worker.add_argument("--workspace", required=True)
    p_worker.add_argument("--instruction", required=True)
    p_worker.add_argument(
        "--max-budget-usd",
        type=float,
        default=None,
        help=f"Worker max budget in USD (default: {DEFAULT_WORKER_MAX_BUDGET_USD}, override with {WORKER_MAX_BUDGET_ENV})",
    )
    p_worker.add_argument("--json", action="store_true")
    p_worker.set_defaults(func=_cmd_worker_turn)

    p_review_context = sub.add_parser("review-context")
    p_review_context.add_argument("--workspace", required=True)
    p_review_context.add_argument("--run-id", required=True)
    p_review_context.add_argument("--json", action="store_true")
    p_review_context.set_defaults(func=_cmd_review_context)

    p_health = sub.add_parser("health")
    p_health.add_argument("--workspace", required=True)
    p_health.add_argument("--run-id")
    p_health.add_argument("--events-tail", type=int, default=20)
    p_health.add_argument("--history-limit", type=int, default=20)
    p_health.add_argument("--json", action="store_true")
    p_health.set_defaults(func=_cmd_health)

    p_review = sub.add_parser("review")
    p_review.add_argument("--workspace", required=True)
    p_review.add_argument("--run-id", required=True)
    p_review.add_argument("--review-file", required=True)
    p_review.add_argument("--json", action="store_true")
    p_review.set_defaults(func=_cmd_review)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("path", nargs="?", default="")
    p_validate.add_argument("--fixtures", action="store_true")
    p_validate.set_defaults(func=_cmd_validate)

    return parser


def _merge_workspace(args: argparse.Namespace) -> argparse.Namespace:
    if not args.workspace:
        args.workspace = args.workspace_pos
    return args


def _merge_text(args: argparse.Namespace) -> argparse.Namespace:
    if not args.text and args.text_pos:
        args.text = " ".join(args.text_pos)
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
