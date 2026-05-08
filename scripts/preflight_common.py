from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence


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
