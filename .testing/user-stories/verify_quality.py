#!/usr/bin/env python3
"""Verify the US-088 acceptance-criteria links against live fixture code."""

from __future__ import annotations

import ast
import re
import shlex
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
STORIES_DIR = HERE / "stories"
INDEX = HERE / "index.md"
STORY = STORIES_DIR / "US-088-twin-universal-runtime.md"
ENTRYPOINT = "run_fixture_validation"
RUN_COMMAND = ["python3", "-m", "scripts.twin", "validate", "--fixtures"]
REQUIRED_APPROVALS = {
    "docs/approved/twin-team-runtime-architecture.md",
    "docs/approved/twin-runtime-reentry-watchdog.md",
    "docs/approved/twin-universal-host-supervisor.md",
}
REQUIRED_FIELDS = ("ID", "Title", "As a / I want / So that", "Trace", "Risk Focus")
REQUIRED_SECTIONS = (
    "## Acceptance Criteria",
    "## Assertions",
    "## Linked Tests",
    "## Evidence",
    "## Status",
)
AC_RE = re.compile(r"^\d+\.\s+(AC-\d{3})\s+\(([^)]+)\):", re.MULTILINE)
LINK_RE = re.compile(
    r"^-\s+(AC-\d{3}):\s+`([^`]+\.py)::([A-Za-z_][A-Za-z0-9_]*)`$",
    re.MULTILINE,
)
RUN_RE = re.compile(r"^-\s+Run command:\s+`([^`]+)`$", re.MULTILINE)
APPROVAL_RE = re.compile(r"`(docs/approved/[^`]+\.md)`")


def function_graph(path: Path) -> tuple[set[str], dict[str, set[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    calls: dict[str, set[str]] = {}
    for name, node in functions.items():
        calls[name] = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
    return set(functions), calls


def reachable(entrypoint: str, calls: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    pending = [entrypoint]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(calls.get(name, set()) - seen)
    return seen


def verify_story(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if not re.search(rf"^-\s+{re.escape(field)}:", text, re.MULTILINE):
            errors.append(f"{path.name}: missing field {field}")
    for section in REQUIRED_SECTIONS:
        if section not in text:
            errors.append(f"{path.name}: missing section {section}")

    ac_rows = AC_RE.findall(text)
    ac_ids = [ac_id for ac_id, _kind in ac_rows]
    if not ac_ids or len(ac_ids) != len(set(ac_ids)):
        errors.append(f"{path.name}: AC ids must be present and unique")
    kinds = {kind for _ac_id, kind in ac_rows}
    for required_kind in ("正向", "负向", "回归"):
        if required_kind not in kinds:
            errors.append(f"{path.name}: missing {required_kind} acceptance criterion")

    for risk in ("逻辑错误", "行为回归", "安全问题", "运行时"):
        if not re.search(rf"^\s+-\s+{risk}:\s+\S", text, re.MULTILINE):
            errors.append(f"{path.name}: Risk Focus missing {risk}")

    approvals = set(APPROVAL_RE.findall(text))
    missing_approvals = REQUIRED_APPROVALS - approvals
    if missing_approvals:
        errors.append(f"{path.name}: missing approval trace {sorted(missing_approvals)}")
    for approval in approvals:
        if not (REPO_ROOT / approval).is_file():
            errors.append(f"{path.name}: approval trace does not exist: {approval}")

    links = LINK_RE.findall(text)
    linked_ac_ids = {ac_id for ac_id, _file, _function in links}
    if set(ac_ids) != linked_ac_ids:
        errors.append(
            f"{path.name}: AC/test mapping differs: ACs={sorted(ac_ids)}, linked={sorted(linked_ac_ids)}"
        )

    graphs: dict[Path, tuple[set[str], set[str]]] = {}
    for ac_id, relative, function in links:
        test_path = REPO_ROOT / relative
        if not test_path.is_file():
            errors.append(f"{path.name}: {ac_id} test file not found: {relative}")
            continue
        if test_path not in graphs:
            functions, calls = function_graph(test_path)
            graphs[test_path] = functions, reachable(ENTRYPOINT, calls)
        functions, entrypoint_reachable = graphs[test_path]
        if function not in functions:
            errors.append(f"{path.name}: {ac_id} function not found: {relative}::{function}")
        elif function not in entrypoint_reachable:
            errors.append(f"{path.name}: {ac_id} function is not reached from {ENTRYPOINT}: {function}")

    commands = RUN_RE.findall(text)
    if len(commands) != 1:
        errors.append(f"{path.name}: expected exactly one Run command")
    else:
        try:
            command = shlex.split(commands[0])
        except ValueError as exc:
            errors.append(f"{path.name}: invalid Run command: {exc}")
        else:
            if command != RUN_COMMAND:
                errors.append(f"{path.name}: Run command must execute the twin fixture suite")

    if not re.search(r"^\s*-\s+\[x\]\s+Done\s*$", text, re.MULTILINE):
        errors.append(f"{path.name}: completed implementation must have Status Done")
    return errors


def main() -> int:
    stories = sorted(STORIES_DIR.glob("US-*.md"))
    errors: list[str] = []
    if stories != [STORY]:
        errors.append(
            f"story verifier scope changed: expected {[STORY.name]}, "
            f"found {[story.name for story in stories]}"
        )
    index_text = INDEX.read_text(encoding="utf-8") if INDEX.is_file() else ""
    if STORY.name not in index_text:
        errors.append(f"{STORY.name}: missing from index.md")
    if STORY.is_file():
        try:
            errors.extend(verify_story(STORY))
        except (OSError, SyntaxError) as exc:
            errors.append(f"{STORY.name}: verification failed: {exc}")

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("story quality: PASS (US-088, all AC links reachable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
