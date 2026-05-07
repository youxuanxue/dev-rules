#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

DEFAULT_HIGH_RISK_PATTERNS = [
    r"^migrations?/",
    r"^db/migrations?/",
    r"^schema/",
]

DEFAULT_ANCHOR_PATTERNS = [
    r"^docs/approved/.*\.md$",
]

DEFAULT_COMMIT_TOKENS = [
    r"high[-_ ]risk[-_ ]anchor",
    r"approved[-_ ]anchor",
]


def run_git(args: list[str]) -> str:
    res = subprocess.run(["git", *args], check=True, text=True, capture_output=True)
    return res.stdout


def parse_config(path: pathlib.Path | None) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]], list[re.Pattern[str]]]:
    high = list(DEFAULT_HIGH_RISK_PATTERNS)
    anchors = list(DEFAULT_ANCHOR_PATTERNS)
    tokens = list(DEFAULT_COMMIT_TOKENS)

    if path and path.is_file():
        text = path.read_text(encoding="utf-8")
        mode = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s == "[high_risk_paths]":
                mode = "high"
                high = []
                continue
            if s == "[anchor_paths]":
                mode = "anchor"
                anchors = []
                continue
            if s == "[anchor_tokens]":
                mode = "token"
                tokens = []
                continue
            if mode == "high":
                high.append(s)
            elif mode == "anchor":
                anchors.append(s)
            elif mode == "token":
                tokens.append(s)

    return (
        [re.compile(p) for p in high],
        [re.compile(p) for p in anchors],
        [re.compile(p, re.IGNORECASE) for p in tokens],
    )


def changed_paths(base: str) -> list[str]:
    out = run_git(["diff", "--name-only", f"{base}...HEAD"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def commit_text(base: str) -> str:
    return run_git(["log", "--format=%s%n%b", f"{base}..HEAD"]).lower()


def main() -> int:
    parser = argparse.ArgumentParser(description="Require approval anchor for high-risk changes.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/high-risk-anchor.conf",
        help="optional rules file with [high_risk_paths], [anchor_paths], [anchor_tokens]",
    )
    args = parser.parse_args()

    cfg = pathlib.Path(args.rules)
    high_re, anchor_re, token_re = parse_config(cfg if cfg.exists() else None)

    if not high_re:
        if cfg.exists():
            sys.stderr.write("[check_high_risk_anchor] config error: [high_risk_paths] is empty\n")
            return 2
        print("[check_high_risk_anchor] skip: no high-risk path patterns configured")
        return 0

    try:
        paths = changed_paths(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    risky = [p for p in paths if any(rx.search(p) for rx in high_re)]
    if not risky:
        print("[check_high_risk_anchor] no high-risk paths changed")
        return 0

    has_anchor_file = any(any(rx.search(p) for rx in anchor_re) for p in paths)

    try:
        text = commit_text(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    has_anchor_token = any(rx.search(text) for rx in token_re)

    if has_anchor_file or has_anchor_token:
        print("[check_high_risk_anchor] anchor present")
        return 0

    sys.stderr.write("[check_high_risk_anchor] high-risk changes require approval anchor\n")
    sys.stderr.write("High-risk changed paths:\n")
    for p in risky:
        sys.stderr.write(f"  - {p}\n")
    sys.stderr.write(
        "No anchor found. Add docs/approved/* evidence or commit token like 'high-risk-anchor'.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
