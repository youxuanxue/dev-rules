from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .initializer import init_workspace
from .persona import derive_persona
from .replay import replay_run
from .runtime import run_workspace
from .sources import build_index, discover_sources, fixture_paths, matches_project, project_needles
from .util import now_utc, read_json, write_json
from .validate import FIXTURES_DIR, run_fixture_validation, validate_run_dir


def _since_cutoff(value: str) -> datetime | None:
    if not value:
        return None
    if value.endswith("d") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(value[:-1]))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"invalid --since value: {value}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _filter_since(paths: list[tuple[str, Path]], since: str) -> list[tuple[str, Path]]:
    cutoff = _since_cutoff(since)
    if cutoff is None:
        return paths
    return [(typ, path) for typ, path in paths if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= cutoff]


def _cmd_index(args: argparse.Namespace) -> int:
    if args.fixtures:
        paths = fixture_paths(FIXTURES_DIR)
    else:
        paths = list(discover_sources(Path.home(), include_cursor_store=not args.no_cursor_store))
        if args.project:
            needles = project_needles(args.project)
            paths = [(typ, path) for typ, path in paths if matches_project(path, needles)]
        paths = _filter_since(paths, args.since)
    index = build_index(paths, generated_at=now_utc())
    write_json(Path(args.out), index)
    print(f"indexed sources={len(index['sources'])} turns={len(index['turns'])} out={args.out}")
    return 0


def _cmd_derive(args: argparse.Namespace) -> int:
    index_path = Path(args.index).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    if index_path == out_path:
        raise SystemExit("derive --out must differ from --index; write persona to a separate file")
    index = read_json(index_path)
    persona = derive_persona(index, generated_at=now_utc())
    write_json(out_path, persona)
    print(f"derived persona out={args.out}")
    return 0


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

    p_index = sub.add_parser("index")
    p_index.add_argument("--out", required=True)
    p_index.add_argument("--since", default="")
    p_index.add_argument("--project", default="")
    p_index.add_argument("--fixtures", action="store_true")
    p_index.add_argument("--no-cursor-store", action="store_true")
    p_index.set_defaults(func=_cmd_index)

    p_derive = sub.add_parser("derive")
    p_derive.add_argument("--index", required=True)
    p_derive.add_argument("--out", required=True)
    p_derive.set_defaults(func=_cmd_derive)

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
