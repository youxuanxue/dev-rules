#!/usr/bin/env python3
"""Flag silent-error-swallow patterns introduced on added diff lines.

Hardens the prose rule in /xj-review ("`|| true` 类静默吞错") into a
deterministic scan so a reviewer never misses a swallow site by eye.

Warn-only by default (exit 0): legitimate cleanup (`rm -rf "$tmp" || true`)
is everywhere, so this is a review *signal*, not a hard gate — the model
still judges whether each site masks a real failure. Pass --strict to fail.
An inline `# preflight-allow: swallow` (or `# noqa: swallow`) on the same
line suppresses that occurrence deterministically — a better escape hatch
than `--no-verify`.

Only ADDED lines (`git diff base...HEAD`) in code files are scanned, so the
rule's own prose mention of `|| true` in a .md doc never self-trips.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

from preflight_common import cli_fail, compile_patterns, parse_ini_sections, run_git

# Files where a swallow pattern is real code, not prose. .md/.txt excluded so
# this very check's documentation cannot self-trip.
CODE_SUFFIXES = {".sh", ".bash", ".zsh", ".bats", ".py", ".yml", ".yaml", ".mk"}
CODE_BASENAMES = {"Makefile", "makefile", "GNUmakefile"}

DEFAULTS = {
    # Each pattern targets a high-signal "swallow the failure" form. Conservative
    # on purpose: 2>/dev/null is excluded (overwhelmingly legitimate), so this
    # does not drown the signal.
    "swallow_patterns": [
        r"\|\|\s*true\b",            # cmd || true
        r"\|\|\s*:\s*(?:#.*)?$",      # cmd || :
        r"--no-verify\b",            # git commit --no-verify (bypass hooks)
        r"\bset\s+\+e\b",            # disable errexit
        r"continue-on-error:\s*true",  # GitHub Actions step swallow
        r"except\s*:\s*pass\b",       # bare except: pass
        r"except\s+[\w.]+(?:\s+as\s+\w+)?\s*:\s*pass\b",  # except X: pass
    ],
}

_ALLOW_RE = re.compile(r"#\s*(?:preflight-allow|noqa):\s*swallow\b")


def _is_code_file(path: str) -> bool:
    p = pathlib.PurePosixPath(path)
    return p.suffix in CODE_SUFFIXES or p.name in CODE_BASENAMES


def parse_unified_diff(diff_text: str) -> list[tuple[str, int, str]]:
    """Return (path, new_line_number, text) for every added line in code files.

    Parses unified diff hunk headers to track the new-file line number so the
    finding can cite file:line. Expects --unified=0 output (no context lines).
    Metadata lines (`diff --git`, `index`, `--- a/…`) only advance a counter
    that the next `@@` header resets, so they never corrupt a real line number.
    """
    results: list[tuple[str, int, str]] = []
    cur_path = ""
    new_lineno = 0
    in_code = False
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            # "+++ b/path" or "+++ /dev/null"
            target = raw[4:].strip()
            cur_path = target[2:] if target.startswith("b/") else target
            in_code = cur_path != "/dev/null" and _is_code_file(cur_path)
            continue
        m = hunk_re.match(raw)
        if m:
            new_lineno = int(m.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            if in_code:
                results.append((cur_path, new_lineno, raw[1:]))
            new_lineno += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            # removed line: does not advance the new-file counter
            continue
        elif not raw.startswith("\\"):
            new_lineno += 1
    return results


def added_lines(base: str) -> list[tuple[str, int, str]]:
    return parse_unified_diff(run_git(["diff", "--unified=0", f"{base}...HEAD"]))


def find_swallows(
    lines: list[tuple[str, int, str]], patterns: list[re.Pattern[str]]
) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path, lineno, text in lines:
        if _ALLOW_RE.search(text):
            continue
        if any(rx.search(text) for rx in patterns):
            findings.append((path, lineno, text.strip()))
    return findings


def _self_test() -> int:
    failures: list[str] = []
    patterns = compile_patterns(DEFAULTS["swallow_patterns"])

    sample = [
        ("a.sh", 10, 'risky_cmd || true'),
        ("b.sh", 3, 'cleanup_tmp || true  # preflight-allow: swallow'),
        ("c.py", 7, '    except: pass'),
        ("d.py", 8, '    except ValueError as e: pass'),
        ("e.sh", 1, 'git commit --no-verify'),
        ("f.yml", 2, '    continue-on-error: true'),
        ("g.sh", 5, 'real_cmd && echo ok'),  # clean
        ("h.sh", 6, 'command -v foo 2>/dev/null'),  # 2>/dev/null not flagged
    ]
    found = find_swallows(sample, patterns)
    found_paths = sorted(p for p, _, _ in found)
    expected = ["a.sh", "c.py", "d.py", "e.sh", "f.yml"]
    if found_paths != expected:
        failures.append(f"find_swallows: expected {expected}, got {found_paths}")

    # added_lines hunk parsing: prose .md must not be scanned even with || true.
    diff = (
        "diff --git a/x.md b/x.md\n"
        "--- a/x.md\n+++ b/x.md\n"
        "@@ -0,0 +1 @@\n+see `cmd || true` in docs\n"
        "diff --git a/y.sh b/y.sh\n"
        "--- a/y.sh\n+++ b/y.sh\n"
        "@@ -0,0 +1,2 @@\n+do_thing || true\n+second_line\n"
    )
    parsed = parse_unified_diff(diff)
    md_hits = [t for p, _, t in parsed if p == "x.md"]
    if md_hits:
        failures.append(f"added_lines scanned .md prose: {md_hits}")
    ysh = [(ln, t) for p, ln, t in parsed if p == "y.sh"]
    if ysh != [(1, "do_thing || true"), (2, "second_line")]:
        failures.append(f"added_lines y.sh line numbers wrong: {ysh}")

    if failures:
        for f in failures:
            sys.stderr.write(f"  FAIL: {f}\n")
        return 1
    print("[check_silent_error_swallow] self-test OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List silent-error-swallow patterns (|| true, --no-verify, except: "
            "pass, continue-on-error, set +e) added in the diff. Warn-only "
            "(exit 0) by default; --strict exits non-zero on any finding."
        )
    )
    parser.add_argument("--base", default="origin/main", help="diff base, default origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/silent-error-swallow.conf",
        help="optional rules file with a [swallow_patterns] section",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when any swallow site is found (default: warn only)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    cfg = parse_ini_sections(pathlib.Path(args.rules), DEFAULTS)
    patterns = compile_patterns(cfg["swallow_patterns"])

    try:
        lines = added_lines(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    findings = find_swallows(lines, patterns)
    if not findings:
        print("[check_silent_error_swallow] no silent-error-swallow added in diff")
        return 0

    detail = [f"{path}:{lineno}  {text}" for path, lineno, text in findings]
    if args.strict:
        return cli_fail(
            "check_silent_error_swallow",
            "silent-error-swallow added (suppress legit cleanup with '# preflight-allow: swallow')",
            *detail,
        )
    print(
        f"[check_silent_error_swallow] {len(findings)} swallow site(s) added "
        f"— review each (suppress legit cleanup with '# preflight-allow: swallow'):"
    )
    for d in detail:
        print(f"    - {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
