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


def _parse_diff_status(diff_output: str) -> list[str]:
    """Pure parser: extract paths gone from their pre-diff location.

    Covers two `git diff --name-status` statuses:
      - D (deletion): parts[1] is gone
      - R (rename): parts[1] is the OLD path (gone), parts[2] is the new path
    Both leave config references to the old path broken at build time.
    """
    paths: list[str] = []
    for line in diff_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith("D") and len(parts) >= 2:
            paths.append(parts[1])
        elif status.startswith("R") and len(parts) >= 3:
            paths.append(parts[1])
    return paths


def deleted_paths(base: str) -> list[str]:
    return _parse_diff_status(run_git(["diff", "--name-status", f"{base}...HEAD"]))


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


def _self_test() -> int:
    """Exercise pure functions so verify-rules.sh can mechanically validate this check.

    Covers:
      - D / R / A / M status parsing (R-001 rename gap)
      - scan_file_for_refs hit + boundary false-positive (prototype/ vs prototype/*)
    """
    import tempfile

    failures: list[str] = []

    # _parse_diff_status: D and R both yield old path; A and M are ignored.
    diff_sample = (
        "D\told/deleted.md\n"
        "R100\told/renamed.md\tnew/renamed.md\n"
        "A\tnew/added.md\n"
        "M\texisting.md\n"
    )
    got = _parse_diff_status(diff_sample)
    expected = ["old/deleted.md", "old/renamed.md"]
    if got != expected:
        failures.append(f"_parse_diff_status: expected {expected}, got {got}")

    # scan_file_for_refs: should hit on `readme = "prototype/README.md"`
    # but NOT on `prototype/*` (a glob/branch pattern, not a path).
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        cfg = root / "pyproject.toml"
        cfg.write_text(
            'readme = "prototype/README.md"\n'
            'branches = ["prototype/*", "feature/*"]\n',
            encoding="utf-8",
        )
        hits = scan_file_for_refs(cfg, ["prototype/README.md"], root)
        if len(hits) != 1 or hits[0][0] != "prototype/README.md" or hits[0][1] != 1:
            failures.append(f"scan_file_for_refs hit: expected 1 hit at line 1, got {hits}")

        # `prototype/` alone should NOT match the `prototype/*` glob pattern
        # because `*` is not in the path-token boundary class.
        no_hits = scan_file_for_refs(cfg, ["prototype/"], root)
        if no_hits:
            failures.append(f"scan_file_for_refs boundary FP: expected no hit, got {no_hits}")

    if failures:
        for f in failures:
            sys.stderr.write(f"  FAIL: {f}\n")
        return 1
    print("[check_deleted_file_refs] self-test OK (3 assertions)")
    return 0


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
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run built-in assertions on pure functions and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

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
