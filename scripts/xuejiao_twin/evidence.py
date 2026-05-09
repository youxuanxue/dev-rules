from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def collect_project_evidence(project_root: Path) -> dict[str, Any]:
    status_code, status = _run(["git", "status", "--short"], project_root)
    diff_code, diff = _run(["git", "diff", "--stat"], project_root)
    return {
        "commands_observed": ["git status --short", "git diff --stat"],
        "git_status": status[:4000],
        "git_status_exit": status_code,
        "git_diff_stat": diff[:4000],
        "git_diff_stat_exit": diff_code,
    }


def _iter_json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def _iter_validation_items(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in objects:
        if isinstance(item.get("validation"), list):
            items.extend(child for child in item["validation"] if isinstance(child, dict))
        if "command" in item:
            items.append(item)
    return items


def _command_passed(command: str, evidence_text: str, objects: list[dict[str, Any]]) -> tuple[str, str]:
    for item in _iter_validation_items(objects):
        if str(item.get("command") or "").strip() != command:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in {"passed", "failed", "attempted", "missing"}:
            return status, f"structured status={status}"
        returncode = item.get("returncode")
        if returncode == 0 or str(returncode) == "0":
            return "passed", "structured returncode=0"
        if returncode is not None:
            return "failed", f"structured returncode={returncode}"
        return "attempted", "structured command observed"
    escaped = re.escape(command)
    pass_patterns = (
        rf"(?im)^\s*PASS\s+{escaped}\s*$",
        rf"(?im)^\s*{escaped}\s*(?:->|:|=)\s*(?:0|pass|passed)\s*$",
        rf"(?im)^\s*validation:\s*{escaped}\s*(?:->|:|=)\s*(?:0|pass|passed)\s*$",
    )
    if any(re.search(pattern, evidence_text) for pattern in pass_patterns):
        return "passed", "text pass marker"
    fail_patterns = (
        rf"(?im)^\s*FAIL\s+{escaped}\s*$",
        rf"(?im)^\s*{escaped}\s*(?:->|:|=)\s*(?:[1-9][0-9]*|fail|failed)\s*$",
        rf"(?im)^\s*validation:\s*{escaped}\s*(?:->|:|=)\s*(?:[1-9][0-9]*|fail|failed)\s*$",
    )
    if any(re.search(pattern, evidence_text) for pattern in fail_patterns):
        return "failed", "text fail marker"
    return "missing", "not observed"


def validation_command_status(goal: dict[str, Any], evidence_text: str) -> list[dict[str, str]]:
    objects = _iter_json_objects(evidence_text)
    statuses: list[dict[str, str]] = []
    for command in list(goal.get("validation_commands", [])):
        status, evidence = _command_passed(str(command), evidence_text, objects)
        statuses.append({"command": str(command), "status": status, "evidence": evidence})
    return statuses


def validation_coverage(goal: dict[str, Any], evidence_text: str) -> float:
    statuses = validation_command_status(goal, evidence_text)
    if not statuses:
        return 1.0
    passed = sum(1 for item in statuses if item["status"] == "passed")
    return passed / len(statuses)
