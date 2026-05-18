#!/usr/bin/env python3
"""Detect drift between dev-rules check_*.py inventory and consumer preflight scripts.

Problem (R-004): dev-rules `templates/preflight.sh` defines stages that consumers
SHOULD adopt; consumer projects (using "方案 A vendored") maintain their own
`scripts/preflight_common.sh` by hand. When dev-rules adds a new check_*.py,
nothing tells the consumer they have new upstream tooling to wire up.

This check inventories dev-rules's check scripts and reports, per consumer,
which ones are NOT referenced from the consumer's preflight scripts.

Not a hard fail by default — consumers may legitimately curate a subset.
Run as `sync.sh --check-preflight-drift` for cross-consumer sweep.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

DEV_RULES_HOME = pathlib.Path(__file__).resolve().parent.parent


def list_check_scripts(scripts_dir: pathlib.Path) -> list[str]:
    """Return sorted list of check_*.py script basenames in dev-rules/scripts/."""
    if not scripts_dir.is_dir():
        return []
    return sorted(p.name for p in scripts_dir.glob("check_*.py") if p.is_file())


def consumer_preflight_files(project_dir: pathlib.Path) -> list[pathlib.Path]:
    """Return consumer-side preflight shell files to scan for check references."""
    candidates = [
        project_dir / "scripts" / "preflight.sh",
        project_dir / "scripts" / "preflight_common.sh",
    ]
    return [p for p in candidates if p.is_file()]


_REF_RE = re.compile(r"dev-rules/scripts/(check_[a-zA-Z0-9_]+\.py)")


def referenced_scripts(file_text: str) -> set[str]:
    """Extract `dev-rules/scripts/check_*.py` references from preflight text.

    The `dev-rules/scripts/` prefix is required to avoid (a) matching script
    names mentioned in comments and (b) confusing consumer's own project-local
    `scripts/check_*.py` files (which are project tools, not dev-rules tools).
    """
    return set(_REF_RE.findall(file_text))


def detect_drift(
    dev_rules_root: pathlib.Path, project_dir: pathlib.Path
) -> tuple[list[str], list[str], int]:
    """Return (not_wired, dangling, available_count) for one consumer.

    not_wired:        scripts in dev-rules/scripts/check_*.py but not referenced
                      in consumer's preflight files.
    dangling:         scripts referenced in consumer's preflight files but not
                      present in dev-rules/scripts/.
    available_count:  total dev-rules check_*.py available (denominator for
                      reporting; saved as third tuple element to spare callers
                      re-globbing).
    """
    available = set(list_check_scripts(dev_rules_root / "scripts"))
    referenced: set[str] = set()
    for pf in consumer_preflight_files(project_dir):
        referenced |= referenced_scripts(pf.read_text(encoding="utf-8", errors="replace"))
    not_wired = sorted(available - referenced)
    dangling = sorted(referenced - available)
    return not_wired, dangling, len(available)


def _self_test() -> int:
    import tempfile

    failures: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)

        # Mock dev-rules structure
        (root / "scripts").mkdir()
        (root / "scripts" / "check_foo.py").write_text("# foo")
        (root / "scripts" / "check_bar.py").write_text("# bar")
        (root / "scripts" / "_schema_lite.py").write_text("# not a check")

        scripts = list_check_scripts(root / "scripts")
        if scripts != ["check_bar.py", "check_foo.py"]:
            failures.append(f"list_check_scripts: got {scripts}")

        # Mock consumer: wires check_foo + a dangling check_ghost.
        # Note: the comment "check_bar.py" is intentional — it must NOT be
        # counted as a reference, since only `dev-rules/scripts/check_X.py`
        # qualifies (avoids false negatives on prose mentions and project-local
        # scripts/check_*.py).
        consumer = root / "consumer"
        (consumer / "scripts").mkdir(parents=True)
        (consumer / "scripts" / "preflight_common.sh").write_text(
            'section "foo"\n'
            'python3 dev-rules/scripts/check_foo.py\n'
            '# does not wire check_bar.py (comment-only mention)\n'
            'python3 dev-rules/scripts/check_ghost.py  # dangling\n'
            'python3 scripts/check_local_only.py  # project-local, ignore\n'
        )

        not_wired, dangling, available_count = detect_drift(root, consumer)
        if not_wired != ["check_bar.py"]:
            failures.append(f"not_wired: expected ['check_bar.py'], got {not_wired}")
        if dangling != ["check_ghost.py"]:
            failures.append(f"dangling: expected ['check_ghost.py'], got {dangling}")
        if available_count != 2:
            failures.append(f"available_count: expected 2, got {available_count}")

        # Consumer with no preflight files at all
        empty_consumer = root / "empty"
        empty_consumer.mkdir()
        not_wired, dangling, available_count = detect_drift(root, empty_consumer)
        if not_wired != ["check_bar.py", "check_foo.py"]:
            failures.append(f"empty consumer not_wired: got {not_wired}")
        if dangling != []:
            failures.append(f"empty consumer dangling: got {dangling}")
        if available_count != 2:
            failures.append(f"empty consumer available_count: got {available_count}")

    if failures:
        for f in failures:
            sys.stderr.write(f"  FAIL: {f}\n")
        return 1
    print("[check_preflight_stage_drift] self-test OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report dev-rules check_*.py scripts not wired into a consumer's "
            "preflight. Informational by default (exit 0); use --strict to "
            "exit non-zero when not_wired or dangling sets are non-empty."
        )
    )
    parser.add_argument(
        "--project",
        default=".",
        help="consumer project directory (default cwd)",
    )
    parser.add_argument(
        "--dev-rules-root",
        default=str(DEV_RULES_HOME),
        help="dev-rules canonical mirror root",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when drift detected (default: warn only, exit 0)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    dev_rules_root = pathlib.Path(args.dev_rules_root).resolve()
    project_dir = pathlib.Path(args.project).resolve()

    not_wired, dangling, available_count = detect_drift(dev_rules_root, project_dir)

    if not not_wired and not dangling:
        print(
            f"[check_preflight_stage_drift] OK: {project_dir.name} preflight "
            f"references all {available_count} dev-rules check scripts "
            f"(or none with deliberate omission)"
        )
        return 0

    print(f"[check_preflight_stage_drift] drift detected in {project_dir.name}:")
    if not_wired:
        print(f"  not wired ({len(not_wired)} check(s) available in dev-rules but unreferenced):")
        for s in not_wired:
            print(f"    - {s}")
    if dangling:
        print(f"  dangling ({len(dangling)} check(s) referenced but missing in dev-rules):")
        for s in dangling:
            print(f"    - {s}")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
