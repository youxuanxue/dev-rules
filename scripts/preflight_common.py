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
