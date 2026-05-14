#!/usr/bin/env python3
"""Validate Agent Skill manifests against schemas/skill.schema.json.

Scans `.cursor/skills/**/skill.json` (the canonical edit point) and any
explicit manifest paths passed in. Self-skips when no manifest is found.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from _schema_lite import validate

DEV_RULES = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = DEV_RULES / "schemas" / "skill.schema.json"

DEFAULT_GLOBS = [
    ".cursor/skills/**/skill.json",
    "agent-skills/**/skill.json",
    "skills/**/skill.json",
]


def discover(globs: list[str]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for pattern in globs:
        found.extend(pathlib.Path(".").glob(pattern))
    # de-dup, sorted
    return sorted({p.resolve() for p in found})


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Skill manifests against skill.schema.json.")
    parser.add_argument(
        "--glob",
        action="append",
        help="glob pattern (repeat to add more); defaults cover .cursor/skills + agent-skills + skills",
    )
    parser.add_argument("--paths", nargs="*", help="explicit manifest files to validate")
    args = parser.parse_args()

    if args.paths:
        files = [pathlib.Path(p) for p in args.paths]
    else:
        files = discover(args.glob or DEFAULT_GLOBS)
        if not files:
            print("[check_skill_manifest] skip: no skill.json manifest found")
            return 0

    if not SCHEMA.is_file():
        sys.stderr.write(f"[check_skill_manifest] missing schema at {SCHEMA}\n")
        return 2

    failures = 0
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"[check_skill_manifest] {path}: cannot parse JSON ({exc})\n")
            failures += 1
            continue
        errs = validate(value, SCHEMA)
        if errs:
            sys.stderr.write(f"[check_skill_manifest] {path}:\n")
            for e in errs:
                sys.stderr.write(f"  - {e}\n")
            failures += 1

    if failures:
        return 1
    print(f"[check_skill_manifest] {len(files)} manifest(s) valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
