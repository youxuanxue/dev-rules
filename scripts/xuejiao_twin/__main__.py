from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .initializer import feature_ledger, init_workspace, load_goal
from .replay import replay_run
from .runtime import HUMAN_ACTIONS, run_workspace, write_human_response
from .validate import run_fixture_validation, validate_run_dir
from .util import now_utc, read_json, write_json


def _cmd_init(args: argparse.Namespace) -> int:
    workspace = init_workspace(Path(args.goal_file), Path(args.persona), Path(args.out) if args.out else None)
    print(workspace)
    return 0


def _print_human_review(run: dict[str, object]) -> None:
    review = run.get("human_review")
    if not isinstance(review, dict) or not bool(review.get("needed")):
        return
    print("Human Review:")
    print(f"- trigger: {review.get('trigger', '')}")
    print(f"- current_focus: {review.get('current_focus', '')}")
    print(f"- summary: {review.get('summary', '')}")
    blocked = review.get("blocked_features")
    if isinstance(blocked, list) and blocked:
        print("- blocked_features:")
        for item in blocked:
            if not isinstance(item, dict):
                continue
            print(f"  - {item.get('id', '')}: {item.get('description', '')}")
            reason = str(item.get("blocked_reason") or "")
            if reason:
                print(f"    reason: {reason}")
    actions = review.get("suggested_actions")
    if isinstance(actions, list) and actions:
        print("- suggested_actions:")
        for index, item in enumerate(actions, 1):
            if not isinstance(item, dict):
                continue
            print(f"  {index}. {item.get('id', '')} - {item.get('label', '')}")


def _cmd_run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace) if args.workspace else Path(args.project).expanduser() / ".xuejiao-twin"
    run = run_workspace(workspace, mode=args.mode, out=Path(args.out) if args.out else None)
    if args.json:
        import json

        print(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"run_id={run['run_id']} outcome={run['outcome']} stop_reason={run['stop_reason']}")
        if not args.no_human_hints:
            _print_human_review(run)
    return 0 if run["outcome"] in {"completed", "dry_run", "needs_human"} else 1


def _cmd_respond(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace) if args.workspace else Path(args.project).expanduser() / ".xuejiao-twin"
    try:
        target = write_human_response(
            workspace,
            action=str(args.action),
            feature_id=str(args.feature),
            run_id=str(args.run_id),
            note=str(args.note),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"human_response_written={target}")
    return 0


def _cmd_replan(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser() if args.workspace else Path(args.project).expanduser() / ".xuejiao-twin"
    goal = load_goal(workspace / "goal.yaml")
    stamp = now_utc().replace(":", "").replace("-", "")
    archive_dir = workspace / "runs" / "archive"

    ledger_path = workspace / "feature_ledger.json"
    if ledger_path.exists() and not args.no_archive:
        write_json(archive_dir / f"feature_ledger-{stamp}.json", read_json(ledger_path))
    write_json(ledger_path, feature_ledger(goal))

    progress_path = workspace / "progress.md"
    if progress_path.exists() and not args.no_archive:
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / f"progress-{stamp}.md").write_text(progress_path.read_text(encoding="utf-8"), encoding="utf-8")
    progress = [
        "# xuejiao twin progress",
        "",
        f"Replanned: {now_utc()}",
        f"Goal: {goal.get('goal', '')}",
        "",
        "## Current state",
        "- Status: replanned",
        "- Ledger: empty dynamic ledger, planning_status=needs_draft",
        "- Next action: run supervised mode to draft and review a new ledger",
        "",
    ]
    progress_path.write_text("\n".join(progress), encoding="utf-8")

    current = [
        "# xuejiao twin current",
        "",
        "- Status: replanned",
        f"- Goal: {goal.get('goal', '')}",
        "- Focus: none",
        "- Ledger: revision=0 completed=0 pending=0 blocked=0",
        f"- Next: python3 -m scripts.xuejiao_twin run --workspace {workspace} --mode supervised-normal",
        "",
    ]
    (workspace / "CURRENT.md").write_text("\n".join(current), encoding="utf-8")

    response_path = workspace / "human_response.json"
    response_path.unlink(missing_ok=True)
    print(f"feature_ledger_reset={ledger_path}")
    print(f"current={workspace / 'CURRENT.md'}")
    print(f"next=python3 -m scripts.xuejiao_twin run --workspace {workspace} --mode supervised-normal")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    print(replay_run(Path(args.run)), end="")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    errors = run_fixture_validation() if args.fixtures else validate_run_dir(Path(args.path))
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("xuejiao_twin validation: PASS")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.xuejiao_twin")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--goal-file", required=True)
    p_init.add_argument("--persona", required=True)
    p_init.add_argument("--out", default="")
    p_init.set_defaults(func=_cmd_init)

    p_run = sub.add_parser("run")
    p_run.add_argument("--project", default="")
    p_run.add_argument("--workspace", default="")
    p_run.add_argument("--mode", default="dry-run", choices=["dry-run", "supervised-low", "supervised-normal", "supervised-high"])
    p_run.add_argument("--out", default="")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--no-human-hints", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    p_respond = sub.add_parser("respond")
    p_respond.add_argument("--project", default="")
    p_respond.add_argument("--workspace", default="")
    p_respond.add_argument("--action", required=True, choices=sorted(HUMAN_ACTIONS))
    p_respond.add_argument("--feature", default="")
    p_respond.add_argument("--run-id", default="")
    p_respond.add_argument("--note", default="")
    p_respond.set_defaults(func=_cmd_respond)

    p_replan = sub.add_parser("replan")
    p_replan.add_argument("--project", default="")
    p_replan.add_argument("--workspace", default="")
    p_replan.add_argument("--no-archive", action="store_true")
    p_replan.set_defaults(func=_cmd_replan)

    p_replay = sub.add_parser("replay")
    p_replay.add_argument("run")
    p_replay.set_defaults(func=_cmd_replay)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("path", nargs="?", default="")
    p_validate.add_argument("--fixtures", action="store_true")
    p_validate.set_defaults(func=_cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
