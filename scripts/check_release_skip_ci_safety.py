#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys

SKIP_MARKERS = ("[skip ci]", "[ci skip]")
RELEASE_BRANCH_PATTERNS = (
    r"^main$",
    r"^master$",
    r"^release/",
    r"^hotfix/",
)
RELEASE_TAG_PATTERNS = (
    r"^v\d+\.\d+\.\d+",
)


def run_git(args: list[str]) -> str:
    res = subprocess.run(["git", *args], check=True, text=True, capture_output=True)
    return res.stdout.strip()


def is_release_context(branch: str, tags: list[str], force: bool) -> bool:
    if force:
        return True
    if any(re.search(p, branch) for p in RELEASE_BRANCH_PATTERNS):
        return True
    for t in tags:
        if any(re.search(p, t) for p in RELEASE_TAG_PATTERNS):
            return True
    return False


def collect_messages(base: str) -> str:
    text = run_git(["log", "--format=%s%n%b", f"{base}..HEAD"])
    if not text:
        text = run_git(["log", "-1", "--format=%s%n%b"])
    return text.lower()


def main() -> int:
    parser = argparse.ArgumentParser(description="Block skip-ci markers in release-sensitive context.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--force", action="store_true", help="force release-context check")
    args = parser.parse_args()

    try:
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        tags_text = run_git(["tag", "--points-at", "HEAD"])
        tags = [t.strip() for t in tags_text.splitlines() if t.strip()]
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    if not is_release_context(branch, tags, args.force):
        print("[check_release_skip_ci_safety] skip: non-release context")
        return 0

    try:
        msg = collect_messages(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    hit = [m for m in SKIP_MARKERS if m in msg]
    if hit:
        sys.stderr.write("[check_release_skip_ci_safety] skip-ci marker forbidden in release context\n")
        sys.stderr.write(f"branch={branch}, tags={','.join(tags) if tags else '(none)'}\n")
        for marker in hit:
            sys.stderr.write(f"  - found marker: {marker}\n")
        return 1

    print("[check_release_skip_ci_safety] pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
