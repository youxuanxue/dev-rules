from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from collections.abc import Callable, Sequence


def run_git(args: Sequence[str], *, strip: bool = False) -> str:
    res = subprocess.run(["git", *args], check=True, text=True, capture_output=True)
    return res.stdout.strip() if strip else res.stdout


def changed_paths(base: str) -> list[str]:
    out = run_git(["diff", "--name-only", f"{base}...HEAD"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def staged_paths() -> list[str]:
    """Paths staged in the index — the pre-commit view of the pending commit.

    `git diff --cached` diffs index vs HEAD; inside git hooks GIT_INDEX_FILE
    points at the temporary index for pathspec/partial commits, so this
    reflects exactly what is about to be committed. On a branch's first
    commit `base...HEAD` is empty while this is not — checks that only read
    the committed range silently pass staged high-risk changes.
    """
    out = run_git(["diff", "--cached", "--name-only"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _deleted_from_name_status(out: str) -> list[str]:
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if parts and parts[0] == "D" and len(parts) >= 2:
            paths.append(parts[1])
    return paths


def deleted_paths(base: str) -> list[str]:
    """Committed deletions (D status) in base...HEAD."""
    return _deleted_from_name_status(run_git(["diff", "--name-status", f"{base}...HEAD"]))


def staged_deleted_paths() -> list[str]:
    """Staged deletions (D status, index vs HEAD) — same pre-commit blind-spot
    rationale as staged_paths()."""
    return _deleted_from_name_status(run_git(["diff", "--cached", "--name-status"]))


def merge_in_progress() -> bool:
    """True while MERGE_HEAD exists. Staged paths are ignored then: merging
    upstream stages paths whose approval/notice lives in their own history."""
    res = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


def read_pending_message(path: pathlib.Path) -> str:
    """Read a commit message file (commit-msg hook's $1), dropping git's `#`
    comment lines — they never survive default --cleanup, so tokens there
    don't count."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(line for line in lines if not line.startswith("#"))


def commit_text(base: str, *, fallback_head: bool = False) -> str:
    text = run_git(["log", "--format=%s%n%b", f"{base}..HEAD"])
    if fallback_head and not text.strip():
        text = run_git(["log", "-1", "--format=%s%n%b"])
    return text.lower()


def compile_patterns(patterns: Sequence[str], *, ignore_case: bool = False) -> list[re.Pattern[str]]:
    flags = re.IGNORECASE if ignore_case else 0
    return [re.compile(p, flags) for p in patterns]


def matches_any(value: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(rx.search(value) for rx in patterns)


def parse_ini_sections(
    path: pathlib.Path | None,
    defaults: dict[str, Sequence[str]],
    *,
    transform: Callable[[str], str] | None = None,
    replace_defaults: bool = True,
) -> dict[str, list[str]]:
    """Parse the lightweight INI dialect used by .preflight/*.conf files.

    - `[section]` headers switch the active section.
    - By default, values inside known sections REPLACE defaults; pass
      `replace_defaults=False` for legacy append-on-top-of-defaults sections.
    - Unknown sections are ignored, comments (`#`) and blank lines skipped.

    Defaults are copied so callers can mutate the result safely.
    """
    out: dict[str, list[str]] = {k: list(v) for k, v in defaults.items()}
    if path is None or not path.is_file():
        return out
    section: str | None = None
    cleared: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            key = s[1:-1]
            section = key if key in defaults else None
            continue
        if section is None:
            continue
        if replace_defaults and section not in cleared:
            out[section] = []
            cleared.add(section)
        out[section].append(transform(s) if transform else s)
    return out


def cli_fail(prefix: str, message: str, *details: str) -> int:
    """Standard one-line stderr failure used by every check_*.py script."""
    sys.stderr.write(f"[{prefix}] {message}\n")
    for d in details:
        sys.stderr.write(f"  - {d}\n")
    return 1
