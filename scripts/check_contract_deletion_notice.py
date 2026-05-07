#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

DEFAULT_PATTERNS = [
    r"^docs/agent_integration\.md$",
    r"^docs/openapi(?:/.*|\.ya?ml|\.json)?$",
    r"^openapi(?:/.*|\.ya?ml|\.json)?$",
    r"^api/(?:openapi|contract)(?:/.*|\.ya?ml|\.json)?$",
    r"^schemas?/.*$",
]

NOTICE_PATTERNS = [
    r"contract[-_ ]deletion[-_ ]notice",
    r"contract[-_ ]deletion",
    r"breaking[-_ ]contract",
    r"contract[-_ ]removed",
]


def run_git(args: list[str]) -> str:
    res = subprocess.run(["git", *args], check=True, text=True, capture_output=True)
    return res.stdout


def load_patterns(path: pathlib.Path | None) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    contract_raw = list(DEFAULT_PATTERNS)
    notice_raw = list(NOTICE_PATTERNS)

    if path and path.is_file():
        text = path.read_text(encoding="utf-8")
        in_contract = False
        in_notice = False
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s == "[contract_paths]":
                in_contract = True
                in_notice = False
                continue
            if s == "[notice_tokens]":
                in_contract = False
                in_notice = True
                continue
            if in_contract:
                contract_raw.append(s)
            elif in_notice:
                notice_raw.append(s)

    return [re.compile(p) for p in contract_raw], [re.compile(p, re.IGNORECASE) for p in notice_raw]


def pick_base(base_arg: str | None) -> str:
    if base_arg:
        return base_arg
    return "origin/main"


def deleted_paths(base: str) -> list[str]:
    out = run_git(["diff", "--name-status", f"{base}...HEAD"])
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status == "D" and len(parts) >= 2:
            paths.append(parts[1])
            continue
        if status.startswith("R") and len(parts) >= 3:
            # rename is not a deletion, keep migration path intact
            continue
    return paths


def collect_commit_text(base: str) -> str:
    subject_body = run_git(["log", "--format=%s%n%b", f"{base}..HEAD"])
    return subject_body.lower()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require explicit notice token when public contract files are deleted."
    )
    parser.add_argument("--base", help="diff base, default origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/contract-deletion-notice.conf",
        help="optional rules file with [contract_paths] and [notice_tokens]",
    )
    args = parser.parse_args()

    base = pick_base(args.base)
    rules_path = pathlib.Path(args.rules)
    contract_re, notice_re = load_patterns(rules_path if rules_path.exists() else None)

    try:
        deleted = deleted_paths(base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    contract_deleted = [
        p for p in deleted if any(rx.search(p) for rx in contract_re)
    ]
    if not contract_deleted:
        print("[check_contract_deletion_notice] no contract deletion detected")
        return 0

    try:
        text = collect_commit_text(base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    if any(rx.search(text) for rx in notice_re):
        print("[check_contract_deletion_notice] notice token present")
        return 0

    sys.stderr.write("[check_contract_deletion_notice] contract deletion requires notice token\n")
    sys.stderr.write("Deleted contract paths:\n")
    for p in contract_deleted:
        sys.stderr.write(f"  - {p}\n")
    sys.stderr.write(
        "Missing notice token in commits between base..HEAD. "
        "Add one token in commit subject/body, e.g. 'contract-deletion-notice'.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
