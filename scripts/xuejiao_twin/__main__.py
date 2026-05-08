from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .initializer import init_workspace
from .replay import replay_run
from .runtime import run_workspace
from .validate import run_fixture_validation, validate_run_dir


def _cmd_init(args: argparse.Namespace) -> int:
    workspace = init_workspace(Path(args.goal_file), Path(args.persona), Path(args.out) if args.out else None)
    print(workspace)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace) if args.workspace else Path(args.project).expanduser() / ".xuejiao-twin"
    run = run_workspace(workspace, mode=args.mode, out=Path(args.out) if args.out else None)
    print(f"run_id={run['run_id']} outcome={run['outcome']} stop_reason={run['stop_reason']}")
    return 0 if run["outcome"] in {"completed", "dry_run", "needs_human"} else 1


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
    p_run.set_defaults(func=_cmd_run)

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
