#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

from preflight_common import commit_text, run_git

DEFAULT_SKIP_MARKERS = ("[skip ci]", "[ci skip]")
DEFAULT_RELEASE_BRANCH_PATTERNS = (
    r"^main$",
    r"^master$",
    r"^release/",
    r"^hotfix/",
)
DEFAULT_RELEASE_TAG_PATTERNS = (
    r"^v\d+\.\d+\.\d+",
)


def parse_config(path: pathlib.Path | None) -> tuple[list[str], list[str], list[str]]:
    markers = list(DEFAULT_SKIP_MARKERS)
    branches = list(DEFAULT_RELEASE_BRANCH_PATTERNS)
    tags = list(DEFAULT_RELEASE_TAG_PATTERNS)

    if path and path.is_file():
        text = path.read_text(encoding="utf-8")
        mode = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s == "[skip_markers]":
                mode = "markers"
                markers = []
                continue
            if s == "[release_branch_patterns]":
                mode = "branches"
                branches = []
                continue
            if s == "[release_tag_patterns]":
                mode = "tags"
                tags = []
                continue
            if mode == "markers":
                markers.append(s.lower())
            elif mode == "branches":
                branches.append(s)
            elif mode == "tags":
                tags.append(s)

    return markers, branches, tags


def is_release_context(branch: str, tags: list[str], force: bool, branch_patterns: list[str], tag_patterns: list[str]) -> bool:
    if force:
        return True
    if any(re.search(p, branch) for p in branch_patterns):
        return True
    for t in tags:
        if any(re.search(p, t) for p in tag_patterns):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Block skip-ci markers in release-sensitive context.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--force", action="store_true", help="force release-context check")
    parser.add_argument(
        "--rules",
        default=".preflight/release-skip-ci.conf",
        help="optional rules file with [skip_markers], [release_branch_patterns], [release_tag_patterns]",
    )
    args = parser.parse_args()

    try:
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], strip=True)
        tags_text = run_git(["tag", "--points-at", "HEAD"], strip=True)
        tags = [t.strip() for t in tags_text.splitlines() if t.strip()]
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    cfg = pathlib.Path(args.rules)
    markers, branch_patterns, tag_patterns = parse_config(cfg if cfg.exists() else None)

    if cfg.exists() and not markers:
        sys.stderr.write("[check_release_skip_ci_safety] config error: [skip_markers] is empty\n")
        return 2

    if not is_release_context(branch, tags, args.force, branch_patterns, tag_patterns):
        print("[check_release_skip_ci_safety] skip: non-release context")
        return 0

    try:
        msg = commit_text(args.base, fallback_head=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    hit = [m for m in markers if m in msg]
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
