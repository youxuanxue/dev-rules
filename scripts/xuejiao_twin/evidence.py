from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

RISK_MARKERS = (
    "git push --force",
    "push --force",
    "force push",
    "production deploy",
    "prod deploy",
    "terraform apply",
    "rm -rf",
    "reset --hard",
    "drop table",
    "新增依赖",
    "新依赖",
    "架构决策",
    "安全边界",
)


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


def classify_risk(text: str) -> list[str]:
    lower = text.lower()
    risks: list[str] = []
    for marker in RISK_MARKERS:
        marker_lower = marker.lower()
        if marker_lower not in lower:
            continue
        if marker in {"新增依赖", "新依赖"} and any(negated in lower for negated in (f"不{marker_lower}", f"未{marker_lower}", f"无{marker_lower}")):
            continue
        risks.append(marker)
    return risks


def validation_coverage(goal: dict[str, Any], evidence_text: str) -> float:
    commands = list(goal.get("validation_commands", []))
    if not commands:
        return 1.0
    matched = 0
    for command in commands:
        if command in evidence_text:
            matched += 1
    return matched / len(commands)
