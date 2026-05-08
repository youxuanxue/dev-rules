from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .claude_runner import run_claude_headless
from .evidence import classify_risk, collect_project_evidence, validation_coverage
from .initializer import load_goal
from .privacy import PrivacyReport, redact_text, redact_value, stable_hash
from .util import now_utc, read_json, write_json


def _next_feature(ledger: dict[str, Any]) -> dict[str, Any] | None:
    current = ledger.get("current_focus")
    for feature in ledger.get("features", []):
        if feature.get("id") == current and feature.get("status") in {"pending", "in_progress"}:
            return feature
    for feature in ledger.get("features", []):
        if feature.get("status") == "pending":
            return feature
    return None


def _supervisor_prompt(goal: dict[str, Any], persona: dict[str, Any], ledger: dict[str, Any], evidence: dict[str, Any]) -> str:
    feature = _next_feature(ledger)
    focus = feature.get("description") if feature else goal.get("goal")
    return json.dumps({
        "role": "xuejiao supervisor",
        "contract": "Return one concise Chinese instruction for the worker. Do not edit code. Stop for architecture/security/data/dependency/external side effects.",
        "goal": goal.get("goal"),
        "scope_in": goal.get("scope_in", []),
        "scope_out": goal.get("scope_out", []),
        "acceptance": goal.get("acceptance", []),
        "current_focus": focus,
        "persona_policy": persona.get("interaction_policy", {}),
        "project_evidence": evidence,
        "output_contract": "Plain text only, one short instruction. If human input is required, start with NEEDS_HUMAN:",
    }, ensure_ascii=False, indent=2)


def _fallback_instruction(goal: dict[str, Any], ledger: dict[str, Any]) -> str:
    feature = _next_feature(ledger)
    if feature:
        return f"先聚焦 {feature['description']}，只做最小可验证改动，完成后给 diff summary 和验证结果。"
    return f"目标看起来已完成，跑验证命令并给出最终 diff summary：{goal.get('goal', '')}"


def _worker_prompt(instruction: str, goal: dict[str, Any]) -> str:
    return json.dumps({
        "role": "worker code agent",
        "instruction_from_xuejiao_supervisor": instruction,
        "goal": goal.get("goal"),
        "scope_in": goal.get("scope_in", []),
        "scope_out": goal.get("scope_out", []),
        "acceptance": goal.get("acceptance", []),
        "validation_commands": goal.get("validation_commands", []),
        "hard_rules": [
            "Do not push, deploy, create PRs, comment externally, or run destructive git commands.",
            "Do not introduce dependencies unless explicitly approved.",
            "Stop and report if architecture/security/data decisions are required.",
            "Produce evidence: changed files, tests/preflight attempted, failures, and next blocker if any.",
        ],
    }, ensure_ascii=False, indent=2)


def _allowed_tools(goal: dict[str, Any], role: str) -> list[str]:
    configured = goal.get("allowed_tools", {})
    if isinstance(configured, dict):
        tools = configured.get(role)
        if isinstance(tools, list) and tools:
            return [str(tool) for tool in tools]
    if role == "supervisor":
        return ["Read", "Bash(git status *)", "Bash(git diff *)"]
    return ["Read", "Edit", "Write", "Bash(git status *)", "Bash(git diff *)"]


def run_workspace(workspace: Path, *, mode: str, out: Path | None = None) -> dict[str, Any]:
    goal = load_goal(workspace / "goal.yaml")
    persona = read_json(workspace / "persona.lock.json")
    ledger = read_json(workspace / "feature_ledger.json")
    project_root = Path(str(goal["project_root"])).expanduser()
    run_id = f"run-{stable_hash(now_utc() + ':' + str(workspace) + ':' + uuid.uuid4().hex, length=10)}"
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"

    report = PrivacyReport()
    project_evidence_raw = collect_project_evidence(project_root) if project_root.exists() else {"project_missing": str(project_root)}
    project_evidence = redact_value(project_evidence_raw, report)
    supervisor_prompt = _supervisor_prompt(goal, persona, ledger, project_evidence)
    supervisor_dry = mode == "dry-run"
    supervisor_result = run_claude_headless(
        supervisor_prompt,
        cwd=project_root if project_root.exists() else workspace,
        allowed_tools=_allowed_tools(goal, "supervisor"),
        max_budget_usd=float(goal.get("limits", {}).get("max_budget_usd", 1.0)),
        dry_run=supervisor_dry,
    )
    instruction = supervisor_result.output_text.strip()
    if supervisor_dry or not instruction.startswith(("先", "不要", "跑", "给", "修", "定位", "写", "NEEDS_HUMAN")):
        instruction = _fallback_instruction(goal, ledger)

    instruction_redacted, instruction_flags = redact_text(instruction, report)
    supervisor_session_hash = stable_hash(supervisor_result.session_id) if supervisor_result.session_id else ""
    events = [{
        "timestamp": now_utc(),
        "type": "supervisor_instruction",
        "session_hash": supervisor_session_hash,
        "text_redacted": instruction_redacted,
        "privacy_flags": instruction_flags,
    }]

    worker_result = None
    worker_text = ""
    if mode != "dry-run" and not instruction.startswith("NEEDS_HUMAN"):
        worker_result = run_claude_headless(
            _worker_prompt(instruction, goal),
            cwd=project_root,
            allowed_tools=_allowed_tools(goal, "worker"),
            max_budget_usd=float(goal.get("limits", {}).get("max_budget_usd", 1.0)),
        )
        worker_text = worker_result.output_text
        worker_redacted, worker_flags = redact_text(worker_text, report)
        events.append({
            "timestamp": now_utc(),
            "type": "worker_result",
            "session_hash": stable_hash(worker_result.session_id) if worker_result.session_id else "",
            "returncode": worker_result.returncode,
            "text_redacted": worker_redacted[:2000],
            "privacy_flags": worker_flags,
        })

    for event in events:
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    evidence_text = instruction + "\n" + worker_text + "\n" + json.dumps(project_evidence, ensure_ascii=False)
    risks = classify_risk(evidence_text)
    outcome = "dry_run" if mode == "dry-run" else "needs_human" if risks or instruction.startswith("NEEDS_HUMAN") else "agent_failed" if worker_result and worker_result.returncode else "completed"
    stop_reason = "dry-run generated supervisor instruction" if mode == "dry-run" else "risk markers require human review" if risks else "worker completed one supervised turn"
    run_path = out or run_dir / "run.json"
    try:
        events_ref = os.path.relpath(events_path, run_path.parent)
    except ValueError:
        events_ref = str(events_path)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "goal_ref": "goal.yaml",
        "persona_ref": "persona.lock.json",
        "ledger_ref": "feature_ledger.json",
        "events_ref": events_ref,
        "supervisor_session_id": supervisor_session_hash,
        "worker_session_id": stable_hash(worker_result.session_id) if worker_result and worker_result.session_id else "",
        "outcome": outcome,
        "stop_reason": stop_reason,
        "metrics": {
            "turns": len(events),
            "human_gate_count": 1 if risks or instruction.startswith("NEEDS_HUMAN") else 0,
            "clarification_count": 1 if instruction.startswith("NEEDS_HUMAN") else 0,
            "retry_count": 0,
            "blocked_risky_actions": len(risks),
            "validation_coverage": validation_coverage(goal, evidence_text),
        },
        "privacy_report": report.as_dict(),
        "validation_report": {"risk_markers": risks, "mode": mode},
    }
    write_json(run_path, run)
    return run
