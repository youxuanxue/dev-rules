#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

from preflight_common import cli_fail, commit_text, parse_ini_sections, run_git

DEFAULTS = {
    "skip_markers": ["[skip ci]", "[ci skip]"],
    "release_branch_patterns": [r"^main$", r"^master$", r"^release/", r"^hotfix/"],
    "release_tag_patterns": [r"^v\d+\.\d+\.\d+"],
}


def is_release_context(branch: str, tags: list[str], force: bool, branch_patterns: list[str], tag_patterns: list[str]) -> bool:
    if force:
        return True
    if any(re.search(p, branch) for p in branch_patterns):
        return True
    return any(any(re.search(p, t) for p in tag_patterns) for t in tags)


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

    cfg_path = pathlib.Path(args.rules)
    cfg = parse_ini_sections(cfg_path, DEFAULTS)
    # markers compare against lowercased commit text; patterns stay verbatim
    markers = [m.lower() for m in cfg["skip_markers"]]

    if cfg_path.is_file() and not markers:
        sys.stderr.write("[check_release_skip_ci_safety] config error: [skip_markers] is empty\n")
        return 2

    if not is_release_context(branch, tags, args.force, cfg["release_branch_patterns"], cfg["release_tag_patterns"]):
        print("[check_release_skip_ci_safety] skip: non-release context")
        return 0

    try:
        msg = commit_text(args.base, fallback_head=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    hit = [m for m in markers if m in msg]
    if hit:
        return cli_fail(
            "check_release_skip_ci_safety",
            f"skip-ci marker forbidden in release context (branch={branch}, tags={','.join(tags) or '(none)'})",
            *(f"found marker: {m}" for m in hit),
        )

    print("[check_release_skip_ci_safety] pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
