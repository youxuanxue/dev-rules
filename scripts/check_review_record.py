#!/usr/bin/env python3
"""Validate `.reviews/*.json` records against schemas/review.schema.json.

Only triggers when the project actually persists review records (default off
per commands/xj-review.md). Self-skips otherwise.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from _schema_lite import validate

DEV_RULES = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = DEV_RULES / "schemas" / "review.schema.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate .reviews/*.json against review.schema.json.")
    parser.add_argument("--dir", default=".reviews", help="directory with review JSON records")
    parser.add_argument(
        "--paths",
        nargs="*",
        help="explicit JSON files to validate instead of scanning --dir",
    )
    args = parser.parse_args()

    if args.paths:
        files = [pathlib.Path(p) for p in args.paths]
    else:
        review_dir = pathlib.Path(args.dir)
        if not review_dir.is_dir():
            print(f"[check_review_record] skip: {args.dir}/ not present")
            return 0
        files = sorted(review_dir.glob("*.json"))
        if not files:
            print(f"[check_review_record] skip: no JSON records in {args.dir}/")
            return 0

    if not SCHEMA.is_file():
        sys.stderr.write(f"[check_review_record] missing schema at {SCHEMA}\n")
        return 2

    failures = 0
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"[check_review_record] {path}: cannot parse JSON ({exc})\n")
            failures += 1
            continue
        errs = validate(value, SCHEMA)
        if errs:
            sys.stderr.write(f"[check_review_record] {path}:\n")
            for e in errs:
                sys.stderr.write(f"  - {e}\n")
            failures += 1

    if failures:
        return 1
    print(f"[check_review_record] {len(files)} record(s) valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
