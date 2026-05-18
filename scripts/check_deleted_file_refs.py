#!/usr/bin/env python3
"""Block file deletions that leave dangling references in build/packaging config.

Trigger: a commit/diff deletes file X, but build-config or doc frontmatter still
references X by path. preflight passes locally won't catch this — the wheel
builder / package manager will at CI / install time.

Rationale: see dev-rules/global/CLAUDE.md §5 hardening principle.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

from preflight_common import cli_fail, run_git

# Files whose content typically references source paths by string.
# Globbed from repo root.
CONFIG_GLOBS: list[str] = [
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "MANIFEST.in",
    "package.json",
    "Cargo.toml",
    "Dockerfile",
    "Dockerfile.*",
    "*.Dockerfile",
    ".dockerignore",
    ".gitattributes",
    "tox.ini",
    "pre-commit-config.yaml",
    ".pre-commit-config.yaml",
]


def deleted_paths(base: str) -> list[str]:
    out = run_git(["diff", "--name-status", f"{base}...HEAD"])
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if parts and parts[0].startswith("D") and len(parts) >= 2:
            paths.append(parts[1])
    return paths


def find_config_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for pattern in CONFIG_GLOBS:
        # Only check top-level + one level deep to avoid scanning node_modules etc.
        found.extend(repo_root.glob(pattern))
        found.extend(repo_root.glob(f"*/{pattern}"))
    # Dedup, exclude .git
    seen: set[pathlib.Path] = set()
    out: list[pathlib.Path] = []
    for p in found:
        if ".git/" in str(p) or "node_modules/" in str(p) or ".venv" in str(p):
            continue
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        out.append(p)
    return out


def find_frontmatter_md_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Markdown files that may contain related_docs: frontmatter referencing paths."""
    candidates: list[pathlib.Path] = []
    for sub in ("docs", ".experiences", ".testing"):
        d = repo_root / sub
        if d.is_dir():
            candidates.extend(d.rglob("*.md"))
    return [p for p in candidates if p.is_file()]


def scan_file_for_refs(
    config_path: pathlib.Path, deleted: list[str], repo_root: pathlib.Path
) -> list[tuple[str, int, str]]:
    """Return (deleted_path, lineno, line_text) for each match in config_path."""
    hits: list[tuple[str, int, str]] = []
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    lines = text.splitlines()
    for deleted_path in deleted:
        # Match the deleted path as a whole token (surrounded by quotes / whitespace /
        # comma / colon / start-end) to avoid false positives like prototype/ matching
        # prototype/* (a branch pattern, not a path).
        pat = re.compile(
            r"""(^|[\s"',:=\[\(])""" + re.escape(deleted_path) + r"""(\s|["',:\]\)]|$)"""
        )
        for i, line in enumerate(lines, start=1):
            if pat.search(line):
                hits.append((deleted_path, i, line.strip()))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Block file deletions that leave dangling references in build/packaging "
            "config or doc frontmatter. Hardens lesson: preflight PASS != CI PASS "
            "when packaging metadata references the deleted path."
        )
    )
    parser.add_argument("--base", default="origin/main", help="diff base, default origin/main")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="repo root, default cwd",
    )
    args = parser.parse_args()

    repo_root = pathlib.Path(args.repo_root).resolve()

    try:
        deleted = deleted_paths(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    if not deleted:
        print("[check_deleted_file_refs] no file deletions in diff")
        return 0

    config_files = find_config_files(repo_root) + find_frontmatter_md_files(repo_root)

    all_hits: list[tuple[pathlib.Path, str, int, str]] = []
    for cf in config_files:
        for deleted_path, lineno, line_text in scan_file_for_refs(cf, deleted, repo_root):
            # Only report if the config file is NOT itself deleted in this diff.
            rel = str(cf.relative_to(repo_root))
            if rel in deleted:
                continue
            all_hits.append((cf, deleted_path, lineno, line_text))

    if not all_hits:
        print(
            f"[check_deleted_file_refs] OK: {len(deleted)} deletion(s) checked against "
            f"{len(config_files)} config/doc files, no dangling references"
        )
        return 0

    details: list[str] = []
    for cf, deleted_path, lineno, line_text in all_hits:
        rel = cf.relative_to(repo_root)
        details.append(f"{rel}:{lineno}: references deleted '{deleted_path}': {line_text}")

    return cli_fail(
        "check_deleted_file_refs",
        (
            "deleted files still referenced in build/packaging config or doc "
            "frontmatter — fix the reference(s) or restore the file"
        ),
        *details,
    )


if __name__ == "__main__":
    sys.exit(main())
