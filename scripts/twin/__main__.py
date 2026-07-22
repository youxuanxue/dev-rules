from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from .bootstrap import draft_from_files, draft_workspace, write_workspace_draft
from .contracts import DEV_RULES_ROOT, SUPERVISOR_PERSONA_PATH, WORKER_PERSONA_PATH
from .driver import (
    ALLOWED_SUPERVISOR_ROUTES,
    handoff_supervisor_route,
    run_driver,
    submit_instruction,
    submit_review,
)
from .worker import DEFAULT_WORKER_MAX_BUDGET_USD, WORKER_MAX_BUDGET_ENV
from .runtime import (
    apply_supervisor_review,
    build_review_context,
    build_supervisor_context,
    continuation_action,
    record_human_response,
    start_worker_turn,
    status_workspace,
)
from .validate import run_fixture_validation, validate_path
from .workspace import WorkspaceError, load_active_workspace, remember_active_workspace
from .local_cli import local_cli_doctor
from .worktree import WorktreeIsolationError, resolve_wtree_script


COMMAND_VISIBILITY_PUBLIC = "public"
COMMAND_VISIBILITY_ACTION = "action-only"
COMMAND_VISIBILITY_INTERNAL = "internal"
EXPORTED_COMMAND_VISIBILITIES = frozenset(
    {COMMAND_VISIBILITY_PUBLIC, COMMAND_VISIBILITY_ACTION}
)


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _workspace_arg(args: argparse.Namespace, *, remember: bool = False) -> Path:
    value = getattr(args, "workspace", "") or ""
    workspace = Path(value) if value else load_active_workspace()
    return remember_active_workspace(workspace) if remember else workspace


def _print_needs_human(status: dict[str, object]) -> None:
    needs_human = status.get("needs_human")
    if not isinstance(needs_human, dict):
        return
    workspace = Path(str(status["workspace"]))
    run_id = status.get("current_run_id") or "<run_id>"
    print("status=needs_human")
    print(f"question={needs_human.get('question') or ''}")
    context = str(needs_human.get('context') or '')
    if context:
        print(f"context={context}")
    print(f"respond=twin respond <answer>")
    print(f"evidence_current={workspace / 'CURRENT.md'}")
    print(f"evidence_state={workspace / 'supervisor_state.json'}")
    print(f"evidence_run={workspace / 'runs' / str(run_id) / 'run.json'}")
    print(f"evidence_review={workspace / 'runs' / str(run_id) / 'run.json'}::review")


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = status_workspace(_workspace_arg(args, remember=True))
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json(status)
    else:
        display = status.get("display") if isinstance(status.get("display"), dict) else {}
        evidence_paths = display.get("evidence_paths") if isinstance(display.get("evidence_paths"), dict) else {}
        print(f"Goal: {status['goal']}")
        print(f"Status: {display.get('label') or status['status']} ({status['status']})")
        print(f"Summary: {display.get('summary') or ''}")
        print(f"Current item: {status['current_item_id'] or 'none'}")
        print(f"Round: {status['round_index']}")
        print(f"Supervisor route: {status.get('supervisor_route') or 'unbound'}")
        print(f"State revision: {status.get('state_revision', 0)}")
        pending = status.get("pending_action")
        if isinstance(pending, dict):
            print(f"Pending action: {pending.get('kind')}; revision={pending.get('state_revision')}")
        print(f"Next command: {display.get('next_command') or 'none'}")
        worker = display.get("worker") if isinstance(display.get("worker"), dict) else None
        if worker:
            last_activity = worker.get("last_activity_seconds")
            last_activity_text = f"{last_activity}s" if last_activity is not None else "unknown"
            print(
                "Worker: "
                f"{worker.get('state')}; run={worker.get('run_id') or 'none'}; "
                f"last_activity={last_activity_text}; events={worker.get('events_bytes', 0)}B"
            )
            print(f"Worker note: {worker.get('note')}")
        print(f"Workspace: {status['workspace']}")
        print(f"Current: {status['current']}")
        for key, value in evidence_paths.items():
            if value and key != "current":
                print(f"Evidence {key}: {value}")
        _print_needs_human(status)
    return 0


def _cmd_next(args: argparse.Namespace) -> int:
    try:
        action = continuation_action(_workspace_arg(args, remember=True))
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json(action)
    else:
        print(f"action={action['action']}")
        print(f"status={action['status']}")
        if action.get("current_run_id"):
            print(f"run_id={action['current_run_id']}")
        if action.get("command"):
            print(f"command={action['command']}")
        print(f"next={action.get('next') or 'none'}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    workspace = _workspace_arg(args, remember=True)
    deadline = time.monotonic() + max(0.0, float(args.max_wait_seconds))
    latest: dict[str, object] | None = None
    while True:
        try:
            latest = continuation_action(workspace)
        except WorkspaceError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if latest.get("action") != "watch_worker":
            if args.json:
                _print_json(latest)
            else:
                print(f"action={latest.get('action')}")
                print(f"status={latest.get('status')}")
                print(f"next={latest.get('next') or 'none'}")
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timeout = {**latest, "action": "worker_quiet_timeout", "next": f"twin status {Path(workspace).expanduser().resolve()}"}
            if args.json:
                _print_json(timeout)
            else:
                print("action=worker_quiet_timeout")
                print(f"status={timeout.get('status')}")
                print(f"next={timeout.get('next')}")
            return 1
        time.sleep(min(max(0.0, float(args.poll_interval_seconds)), remaining))


def _cmd_respond(args: argparse.Namespace) -> int:
    try:
        target = record_human_response(_workspace_arg(args, remember=True), args.text)
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"human_response_written={target}")
    print("next=twin run <workspace> --supervisor <host/provider> resumes the supervisor loop")
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
        workspace = Path(args.workspace).expanduser().resolve()
        print(f"run_id={run['run_id']} status={run['status']} resume_used={run['worker']['resume_used']}")
        print(f"run={workspace / 'runs' / run['run_id'] / 'run.json'}")
        if run["status"] == "review_required":
            print(f"next=twin run {workspace}")
    return 0 if run["status"] == "review_required" else 1


def _cmd_review_context(args: argparse.Namespace) -> int:
    try:
        context = build_review_context(_workspace_arg(args), args.run_id)
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_json(context)
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


def _read_input(inline: str | None, file_name: str | None, *, label: str) -> str:
    if inline is not None:
        return inline
    if file_name == "-":
        return sys.stdin.read()
    if file_name:
        return Path(file_name).read_text(encoding="utf-8")
    raise WorkspaceError(f"{label} is required")


def _print_driver_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        _print_json(result)
        return
    print(f"action={result.get('action') or 'submitted'}")
    print(f"status={result.get('status')}")
    print(f"workspace={result.get('workspace')}")
    print(f"supervisor_route={result.get('supervisor_route')}")
    print(f"state_revision={result.get('state_revision')}")
    submit = result.get("submit")
    if isinstance(submit, dict):
        print(f"submit={submit.get('command')}")
    if result.get("next_command"):
        print(f"next={result.get('next_command')}")
    elif result.get("resume_command"):
        print(f"next={result.get('resume_command')}")
    elif result.get("next") is not None:
        print(f"next={result.get('next')}")


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        workspace = _workspace_arg(args, remember=True)
        result = run_driver(
            workspace,
            args.supervisor,
            max_budget_usd=args.max_budget_usd,
        )
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_driver_result(result, as_json=args.json)
    return 0


def _cmd_handoff(args: argparse.Namespace) -> int:
    try:
        result = handoff_supervisor_route(
            _workspace_arg(args, remember=True),
            args.supervisor,
        )
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_driver_result(result, as_json=args.json)
    return 0


def _cmd_submit_instruction(args: argparse.Namespace) -> int:
    try:
        instruction = _read_input(args.instruction, args.instruction_file, label="instruction")
        result = submit_instruction(
            _workspace_arg(args, remember=True),
            args.supervisor,
            state_revision=args.state_revision,
            action_token=args.action_token,
            instruction=instruction,
        )
    except (OSError, WorkspaceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_driver_result(result, as_json=args.json)
    return 0


def _cmd_submit_review(args: argparse.Namespace) -> int:
    try:
        review_text = _read_input(args.review_json, args.review_file, label="review")
        review = json.loads(review_text)
        if not isinstance(review, dict):
            raise WorkspaceError("review must be a JSON object")
        result = submit_review(
            _workspace_arg(args, remember=True),
            args.supervisor,
            state_revision=args.state_revision,
            action_token=args.action_token,
            run_id=args.run_id,
            review=review,
        )
    except (OSError, json.JSONDecodeError, WorkspaceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_driver_result(result, as_json=args.json)
    return 0


def _cmd_scaffold(args: argparse.Namespace) -> int:
    try:
        draft = draft_workspace(args.goal, Path(args.workspace) if args.workspace else None)
        draft["workspace"] = str(write_workspace_draft(draft))
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json(draft)
    else:
        print(f"workspace={draft['workspace']}")
        print(f"goal={draft['goal']['one_liner']}")
        print("files=goal.yaml, plan.yaml")
        print("next=edit this scaffold or use supervisor-authored bootstrap artifacts")
    return 0


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    try:
        if not args.workspace or not args.goal_file or not args.plan_file:
            raise WorkspaceError("bootstrap requires --workspace, --goal-file, and --plan-file")
        draft = draft_from_files(
            Path(args.workspace),
            Path(args.goal_file),
            Path(args.plan_file),
            Path(args.research_file) if args.research_file else None,
        )
        workspace = remember_active_workspace(write_workspace_draft(draft, overwrite=args.overwrite))
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print_json({"workspace": str(workspace), "status": "written"})
    else:
        print(f"workspace={workspace}")
        print("status=written")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    errors = run_fixture_validation() if args.fixtures else validate_path(Path(args.path))
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("twin validation: PASS")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    required: list[dict[str, object]] = []
    required.append({
        "name": "python",
        "available": sys.version_info >= (3, 9),
        "detail": sys.version.split()[0],
    })
    required.append({
        "name": "dev_rules",
        "available": DEV_RULES_ROOT.is_dir(),
        "path": str(DEV_RULES_ROOT),
    })
    for name, path in (
        ("supervisor_persona", SUPERVISOR_PERSONA_PATH),
        ("worker_persona", WORKER_PERSONA_PATH),
    ):
        required.append({"name": name, "available": path.is_file(), "path": str(path)})
    canonical_launcher = DEV_RULES_ROOT / "global" / "bin" / "twin"
    required.append({
        "name": "twin_launcher",
        "available": canonical_launcher.is_file() and os.access(canonical_launcher, os.X_OK),
        "path": str(canonical_launcher),
        "installed_path": shutil.which("twin") or "",
    })
    try:
        wtree = resolve_wtree_script()
    except WorktreeIsolationError as exc:
        required.append({"name": "wtree", "available": False, "detail": str(exc)})
    else:
        required.append({"name": "wtree", "available": True, "path": str(wtree)})
    statuses = local_cli_doctor()
    report = {
        "ok": all(bool(check.get("available")) for check in required),
        "required": required,
        "local_cli": statuses,
        "supervisor_routes": list(ALLOWED_SUPERVISOR_ROUTES),
    }
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 1
    for check in required:
        availability = "ok" if check["available"] else "missing"
        detail = check.get("path") or check.get("detail") or ""
        print(f"{check['name']}: {availability}; {detail}")
    for status in statuses:
        availability = "installed" if status["available"] else "missing"
        version = f"; version={status['version']}" if status.get("version") else ""
        print(f"{status['provider']}: {availability}; executable={status['executable']}{version}")
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="twin")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_command(
        name: str,
        *,
        visibility: str,
        help_text: str,
    ) -> argparse.ArgumentParser:
        help_kwargs = (
            {"help": help_text}
            if visibility in EXPORTED_COMMAND_VISIBILITIES
            else {}
        )
        command_parser = sub.add_parser(name, **help_kwargs)
        setattr(command_parser, "twin_visibility", visibility)
        return command_parser

    p_run = add_command(
        "run",
        visibility=COMMAND_VISIBILITY_PUBLIC,
        help_text="Advance a workspace through the bound host-supervisor loop",
    )
    p_run.add_argument("workspace")
    p_run.add_argument("--supervisor", choices=ALLOWED_SUPERVISOR_ROUTES, required=True)
    p_run.add_argument(
        "--max-budget-usd",
        type=float,
        default=None,
        help=f"Worker max budget in USD (default: {DEFAULT_WORKER_MAX_BUDGET_USD}, override with {WORKER_MAX_BUDGET_ENV})",
    )
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    p_handoff = add_command(
        "handoff",
        visibility=COMMAND_VISIBILITY_PUBLIC,
        help_text="Explicitly transfer a workspace to another host supervisor",
    )
    p_handoff.add_argument("workspace")
    p_handoff.add_argument("--supervisor", choices=ALLOWED_SUPERVISOR_ROUTES, required=True)
    p_handoff.add_argument("--json", action="store_true")
    p_handoff.set_defaults(func=_cmd_handoff)

    p_submit_instruction = add_command(
        "submit-instruction",
        visibility=COMMAND_VISIBILITY_ACTION,
        help_text="Submit a token-bound supervisor instruction action",
    )
    p_submit_instruction.add_argument("--workspace", required=True)
    p_submit_instruction.add_argument("--supervisor", choices=ALLOWED_SUPERVISOR_ROUTES, required=True)
    p_submit_instruction.add_argument("--state-revision", type=int, required=True)
    p_submit_instruction.add_argument("--action-token", required=True)
    instruction_input = p_submit_instruction.add_mutually_exclusive_group(required=True)
    instruction_input.add_argument("--instruction")
    instruction_input.add_argument("--instruction-file")
    p_submit_instruction.add_argument("--json", action="store_true")
    p_submit_instruction.set_defaults(func=_cmd_submit_instruction)

    p_submit_review = add_command(
        "submit-review",
        visibility=COMMAND_VISIBILITY_ACTION,
        help_text="Submit a token-bound supervisor review action",
    )
    p_submit_review.add_argument("--workspace", required=True)
    p_submit_review.add_argument("--supervisor", choices=ALLOWED_SUPERVISOR_ROUTES, required=True)
    p_submit_review.add_argument("--state-revision", type=int, required=True)
    p_submit_review.add_argument("--action-token", required=True)
    p_submit_review.add_argument("--run-id", required=True)
    review_input = p_submit_review.add_mutually_exclusive_group(required=True)
    review_input.add_argument("--review-json")
    review_input.add_argument("--review-file")
    p_submit_review.add_argument("--json", action="store_true")
    p_submit_review.set_defaults(func=_cmd_submit_review)

    p_status = add_command(
        "status",
        visibility=COMMAND_VISIBILITY_PUBLIC,
        help_text="Show workspace status and the next user action",
    )
    p_status.add_argument("workspace_pos", nargs="?")
    p_status.add_argument("--workspace", dest="workspace")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=lambda args: _cmd_status(_merge_workspace(args)))

    p_next = add_command(
        "next",
        visibility=COMMAND_VISIBILITY_INTERNAL,
        help_text="Compatibility action derivation",
    )
    p_next.add_argument("--workspace", required=True)
    p_next.add_argument("--json", action="store_true")
    p_next.set_defaults(func=_cmd_next)

    p_watch = add_command(
        "watch",
        visibility=COMMAND_VISIBILITY_INTERNAL,
        help_text="Compatibility worker watcher",
    )
    p_watch.add_argument("--workspace", required=True)
    p_watch.add_argument("--max-wait-seconds", type=float, default=900.0)
    p_watch.add_argument("--poll-interval-seconds", type=float, default=10.0)
    p_watch.add_argument("--json", action="store_true")
    p_watch.set_defaults(func=_cmd_watch)

    p_respond = add_command(
        "respond",
        visibility=COMMAND_VISIBILITY_PUBLIC,
        help_text="Record the answer to a human decision gate",
    )
    p_respond.add_argument("text_pos", nargs="*")
    p_respond.add_argument("--workspace")
    p_respond.add_argument("--text", default="")
    p_respond.set_defaults(func=lambda args: _cmd_respond(_merge_text(args)))

    p_context = add_command(
        "supervisor-context",
        visibility=COMMAND_VISIBILITY_INTERNAL,
        help_text="Compatibility supervisor context export",
    )
    p_context.add_argument("--workspace", required=True)
    p_context.add_argument("--run-id")
    p_context.set_defaults(func=_cmd_supervisor_context)

    p_worker = add_command(
        "worker-turn",
        visibility=COMMAND_VISIBILITY_INTERNAL,
        help_text="Compatibility low-level worker mutation",
    )
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

    p_review_context = add_command(
        "review-context",
        visibility=COMMAND_VISIBILITY_INTERNAL,
        help_text="Compatibility review context export",
    )
    p_review_context.add_argument("--workspace", required=True)
    p_review_context.add_argument("--run-id", required=True)
    p_review_context.add_argument("--json", action="store_true")
    p_review_context.set_defaults(func=_cmd_review_context)

    p_review = add_command(
        "review",
        visibility=COMMAND_VISIBILITY_INTERNAL,
        help_text="Compatibility low-level review mutation",
    )
    p_review.add_argument("--workspace", required=True)
    p_review.add_argument("--run-id", required=True)
    p_review.add_argument("--review-file", required=True)
    p_review.add_argument("--json", action="store_true")
    p_review.set_defaults(func=_cmd_review)

    p_scaffold = add_command(
        "scaffold",
        visibility=COMMAND_VISIBILITY_PUBLIC,
        help_text="Create editable goal and plan drafts",
    )
    p_scaffold.add_argument("goal")
    p_scaffold.add_argument("--workspace")
    p_scaffold.add_argument("--json", action="store_true")
    p_scaffold.set_defaults(func=_cmd_scaffold)

    p_bootstrap = add_command(
        "bootstrap",
        visibility=COMMAND_VISIBILITY_PUBLIC,
        help_text="Initialize a workspace from approved goal and plan files",
    )
    p_bootstrap.add_argument("--workspace", required=True)
    p_bootstrap.add_argument("--goal-file", required=True)
    p_bootstrap.add_argument("--plan-file", required=True)
    p_bootstrap.add_argument("--research-file")
    p_bootstrap.add_argument("--overwrite", action="store_true")
    p_bootstrap.add_argument("--json", action="store_true")
    p_bootstrap.set_defaults(func=_cmd_bootstrap)

    p_validate = add_command(
        "validate",
        visibility=COMMAND_VISIBILITY_INTERNAL,
        help_text="Internal contract and fixture validation",
    )
    p_validate.add_argument("path", nargs="?", default="")
    p_validate.add_argument("--fixtures", action="store_true")
    p_validate.set_defaults(func=_cmd_validate)

    p_doctor = add_command(
        "doctor",
        visibility=COMMAND_VISIBILITY_PUBLIC,
        help_text="Check twin runtime and local provider availability",
    )
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=_cmd_doctor)

    visible_commands = [
        name
        for name, command_parser in sub.choices.items()
        if getattr(command_parser, "twin_visibility", None) in EXPORTED_COMMAND_VISIBILITIES
    ]
    sub.metavar = "{" + ",".join(visible_commands) + "}"
    return parser


def _merge_workspace(args: argparse.Namespace) -> argparse.Namespace:
    if not args.workspace and args.workspace_pos:
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
