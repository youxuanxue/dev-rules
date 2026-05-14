#!/usr/bin/env python3
"""Flag Python tests whose every assertion only proves a file exists / is
non-empty / has some line count — i.e. existence assertions instead of
behavior assertions. test-philosophy.mdc §3 bans these.

Scope: Python only (`tests/**/test_*.py`, `**/*_test.py`, `**/test_*.py`).
A test function is flagged when every `assert ...` inside it is judged
"existence-only" by the heuristics in `_is_existence_only_check`. A single
real behavior assertion (equality, return-value comparison, subprocess
output check, etc.) is enough to save the function.

Stdlib only — uses `ast`. Self-skips when no test files are found.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys

from preflight_common import cli_fail

DEFAULT_GLOBS = [
    "tests/**/test_*.py",
    "tests/**/*_test.py",
    "**/tests/**/test_*.py",
    "**/tests/**/*_test.py",
    "test/**/test_*.py",
    "test/**/*_test.py",
]

EXISTENCE_ATTRS = {"exists", "is_file", "is_dir", "is_symlink"}
EXISTENCE_FUNCS = {"exists", "isfile", "isdir", "getsize", "islink", "lexists"}


def _is_existence_call(node: ast.AST) -> bool:
    """`Path(x).exists()`, `os.path.exists(x)`, etc."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr in EXISTENCE_ATTRS:
            return True
        if func.attr in EXISTENCE_FUNCS:  # os.path.exists, os.path.getsize
            return True
    if isinstance(func, ast.Name) and func.id in EXISTENCE_FUNCS:
        return True
    return False


def _is_read_truthy(node: ast.AST) -> bool:
    """Bare `x.read()` / `x.read_text()` used as a truthiness assertion."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in {"read", "read_text", "read_bytes"}


def _is_len_existence(node: ast.AST) -> bool:
    """`len(x) > 0`, `len(x) >= 1`, `len(x) != 0`."""
    if not isinstance(node, ast.Compare):
        return False
    left = node.left
    if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == "len"):
        return False
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    op = node.ops[0]
    comp = node.comparators[0]
    if not isinstance(comp, ast.Constant) or not isinstance(comp.value, int):
        return False
    if isinstance(op, ast.Gt) and comp.value == 0:
        return True
    if isinstance(op, ast.GtE) and comp.value == 1:
        return True
    if isinstance(op, ast.NotEq) and comp.value == 0:
        return True
    return False


def _is_existence_only_check(test: ast.AST) -> bool:
    """True when the asserted expression only proves existence/non-emptiness."""
    if _is_existence_call(test):
        return True
    if _is_read_truthy(test):
        return True
    if _is_len_existence(test):
        return True
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _is_existence_only_check(test.operand)
    if isinstance(test, ast.BoolOp):
        return all(_is_existence_only_check(v) for v in test.values)
    return False


def _function_asserts(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Assert]:
    return [n for n in ast.walk(func) if isinstance(n, ast.Assert)]


def scan_file(path: pathlib.Path) -> list[str]:
    """Return one error per test function whose every assert is existence-only."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return [f"{path}: parse error ({exc})"]

    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        asserts = _function_asserts(node)
        if not asserts:
            continue
        if all(_is_existence_only_check(a.test) for a in asserts):
            findings.append(
                f"{path}:{node.lineno}: {node.name} has only existence-style assertions "
                f"({len(asserts)} assert(s)) — replace with behavior checks"
            )
    return findings


def discover(globs: list[str]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    cwd = pathlib.Path(".")
    for pattern in globs:
        found.extend(cwd.glob(pattern))
    return sorted({p.resolve() for p in found if p.is_file()})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flag Python tests whose every assertion only proves file existence / non-emptiness."
    )
    parser.add_argument("--glob", action="append", help="extra glob pattern (repeatable)")
    parser.add_argument("--paths", nargs="*", help="explicit test files to scan")
    args = parser.parse_args()

    if args.paths:
        files = [pathlib.Path(p) for p in args.paths]
    else:
        files = discover(args.glob or DEFAULT_GLOBS)
        if not files:
            print("[check_existence_only_tests] skip: no Python test files found")
            return 0

    findings: list[str] = []
    for path in files:
        findings.extend(scan_file(path))

    if findings:
        return cli_fail("check_existence_only_tests", "existence-only test(s) banned by test-philosophy.mdc", *findings)
    print(f"[check_existence_only_tests] {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
