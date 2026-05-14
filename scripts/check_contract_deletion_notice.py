#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

from preflight_common import (
    cli_fail,
    commit_text,
    compile_patterns,
    matches_any,
    parse_ini_sections,
    run_git,
)

DEFAULTS = {
    "contract_paths": [
        r"^docs/agent_integration\.md$",
        r"^docs/openapi(?:/.*|\.ya?ml|\.json)?$",
        r"^openapi(?:/.*|\.ya?ml|\.json)?$",
        r"^api/(?:openapi|contract)(?:/.*|\.ya?ml|\.json)?$",
        r"^schemas?/.*$",
    ],
    "notice_tokens": [
        r"contract[-_ ]deletion[-_ ]notice",
        r"contract[-_ ]deletion",
        r"breaking[-_ ]contract",
        r"contract[-_ ]removed",
    ],
}


def deleted_paths(base: str) -> list[str]:
    out = run_git(["diff", "--name-status", f"{base}...HEAD"])
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if parts and parts[0] == "D" and len(parts) >= 2:
            paths.append(parts[1])
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require explicit notice token when public contract files are deleted."
    )
    parser.add_argument("--base", default="origin/main", help="diff base, default origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/contract-deletion-notice.conf",
        help="optional rules file with [contract_paths] and [notice_tokens]",
    )
    args = parser.parse_args()

    cfg = parse_ini_sections(pathlib.Path(args.rules), DEFAULTS)
    contract_re = compile_patterns(cfg["contract_paths"])
    notice_re = compile_patterns(cfg["notice_tokens"], ignore_case=True)

    try:
        deleted = deleted_paths(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    contract_deleted = [p for p in deleted if matches_any(p, contract_re)]
    if not contract_deleted:
        print("[check_contract_deletion_notice] no contract deletion detected")
        return 0

    try:
        text = commit_text(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    if matches_any(text, notice_re):
        print("[check_contract_deletion_notice] notice token present")
        return 0

    return cli_fail(
        "check_contract_deletion_notice",
        "contract deletion requires notice token (e.g. 'contract-deletion-notice')",
        *contract_deleted,
    )


if __name__ == "__main__":
    sys.exit(main())
