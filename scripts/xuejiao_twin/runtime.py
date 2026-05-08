from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import SCHEMA_VERSION
from .claude_runner import ClaudeRunResult, run_claude_headless
from .evidence import classify_risk, collect_project_evidence, validation_coverage
from .initializer import load_goal
from .privacy import PrivacyReport, redact_text, redact_value, stable_hash
from .util import now_utc, read_json, write_json

Runner = Callable[..., ClaudeRunResult]
_ACTIONS = {"continue", "stop", "needs_human"}
_FEATURE_STATUSES = {"pending", "in_progress", "blocked", "completed", "deferred"}
_BLOCKING_PRIVACY_FLAGS = {"secret_assignment", "bearer_token", "private_key", "sensitive_url"}
_FALLBACK_STARTS = ("先", "不要", "跑", "给", "修", "定位", "写", "NEEDS_HUMAN")


def _next_feature(ledger: dict[str, Any]) -> dict[str, Any] | None:
    current = ledger.get("current_focus")
    for feature in ledger.get("features", []):
        if feature.get("id") == current and feature.get("status") in {"pending", "in_progress"}:
            return feature
    for feature in ledger.get("features", []):
        if feature.get("status") == "pending":
            return feature
    return None


def _feature_by_id(ledger: dict[str, Any], feature_id: str | None) -> dict[str, Any] | None:
    for feature in ledger.get("features", []):
        if feature.get("id") == feature_id:
            return feature
    return None


def _all_features_completed(ledger: dict[str, Any]) -> bool:
    features = ledger.get("features", [])
    return bool(features) and all(feature.get("status") == "completed" for feature in features)


def _feature_status_counts(ledger: dict[str, Any]) -> dict[str, int]:
    counts = {status: 0 for status in _FEATURE_STATUSES}
    for feature in ledger.get("features", []):
        status = str(feature.get("status", ""))
        if status in counts:
            counts[status] += 1
    return counts


def _supervisor_prompt(
    goal: dict[str, Any],
    persona: dict[str, Any],
    ledger: dict[str, Any],
    evidence: dict[str, Any],
    turn_index: int,
    run_history: list[dict[str, Any]] | None = None,
) -> str:
    feature = _next_feature(ledger)
    focus = feature.get("description") if feature else goal.get("goal")
    return json.dumps({
        "role": "xuejiao supervisor",
        "contract": "Return JSON only. Do not edit code. Stop for architecture/security/data/dependency/production deploy/force push/destructive/external side effects.",
        "decision_contract": {
            "action": "continue | stop | needs_human",
            "current_focus": "feature id such as F-001",
            "instruction": "one concise Chinese worker instruction",
            "feature_updates": [{
                "id": "feature id",
                "status": "in_progress | blocked | completed | deferred",
                "validation_evidence": ["commands or evidence observed"],
                "blocked_reason": None,
            }],
            "reason": "short Chinese reason",
        },
        "goal": goal.get("goal"),
        "scope_in": goal.get("scope_in", []),
        "scope_out": goal.get("scope_out", []),
        "acceptance": goal.get("acceptance", []),
        "turn_index": turn_index,
        "current_focus": focus,
        "feature_ledger": ledger,
        "persona_policy": persona.get("interaction_policy", {}),
        "project_evidence": evidence,
        "run_history": run_history or [],
        "branch_policy": "Never auto-commit or push on main/master. Non-main branch commit/push/PR/local deployment is allowed only when goal/tools explicitly allow it.",
        "output_contract": "JSON only. If human input is required, use action=needs_human and explain reason.",
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
            "Do not modify or push main/master.",
            "Non-main branch commit/push/PR/local deployment is allowed only when goal/tools explicitly allow it.",
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


def _json_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    parsed_objects: list[dict[str, Any]] = []
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed_objects.append(parsed)
    for parsed in reversed(parsed_objects):
        if "action" in parsed:
            return parsed
    return parsed_objects[-1] if parsed_objects else None


def _parse_supervisor_decision(text: str, goal: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    stripped = text.strip()
    feature = _next_feature(ledger)
    fallback_focus = feature.get("id") if feature else ledger.get("current_focus")
    if stripped.startswith("NEEDS_HUMAN"):
        return {
            "action": "needs_human",
            "current_focus": fallback_focus,
            "instruction": "",
            "feature_updates": [],
            "reason": stripped,
        }
    parsed = _json_from_text(stripped)
    if parsed is None:
        instruction = stripped if stripped else _fallback_instruction(goal, ledger)
        return {
            "action": "continue",
            "current_focus": fallback_focus,
            "instruction": instruction,
            "feature_updates": [],
            "reason": "plain text supervisor instruction",
        }
    action = str(parsed.get("action") or "continue").strip().lower()
    if action not in _ACTIONS:
        action = "continue"
    current_focus = parsed.get("current_focus") or fallback_focus
    if current_focus is not None:
        current_focus = str(current_focus)
    if _feature_by_id(ledger, current_focus) is None:
        current_focus = fallback_focus
    updates = parsed.get("feature_updates", [])
    if not isinstance(updates, list):
        updates = []
    instruction = str(parsed.get("instruction") or "").strip()
    if action == "continue" and not instruction:
        instruction = _fallback_instruction(goal, ledger)
    return {
        "action": action,
        "current_focus": current_focus,
        "instruction": instruction,
        "feature_updates": [update for update in updates if isinstance(update, dict)],
        "reason": str(parsed.get("reason") or ""),
    }


def _redact_list(values: Any, report: PrivacyReport) -> list[str]:
    if not isinstance(values, list):
        return []
    redacted: list[str] = []
    for value in values:
        text, _ = redact_text(str(value), report)
        if text:
            redacted.append(text)
    return redacted


def _apply_feature_updates(ledger: dict[str, Any], decision: dict[str, Any], report: PrivacyReport) -> bool:
    before = stable_hash(ledger)
    focus = _feature_by_id(ledger, decision.get("current_focus")) or _next_feature(ledger)
    updates = list(decision.get("feature_updates", []))
    if not updates and focus and decision.get("action") == "continue" and focus.get("status") == "pending":
        updates = [{"id": focus.get("id"), "status": "in_progress"}]
    if not updates and focus and decision.get("action") == "needs_human":
        updates = [{"id": focus.get("id"), "status": "blocked", "blocked_reason": decision.get("reason") or "needs human"}]

    for update in updates:
        update_id = update.get("id") or decision.get("current_focus")
        feature = _feature_by_id(ledger, str(update_id or ""))
        if not feature:
            continue
        status = str(update.get("status") or "").strip()
        if decision.get("action") == "continue" and status == "completed":
            status = "in_progress"
        if status in _FEATURE_STATUSES:
            feature["status"] = status
        evidence = feature.setdefault("validation_evidence", [])
        for item in _redact_list(update.get("validation_evidence", []), report):
            if item not in evidence:
                evidence.append(item)
        if feature.get("status") == "completed":
            feature["blocked_reason"] = None
            verified_at = now_utc()
            feature["last_verified_at"] = verified_at
            ledger["last_verified_at"] = verified_at
        elif feature.get("status") in {"blocked", "deferred"}:
            reason = update.get("blocked_reason") or decision.get("reason") or feature.get("blocked_reason")
            if reason:
                feature["blocked_reason"] = redact_text(str(reason), report)[0]

    if focus and focus.get("status") in {"pending", "in_progress", "blocked"}:
        ledger["current_focus"] = focus.get("id")
    else:
        next_feature = _next_feature(ledger)
        ledger["current_focus"] = next_feature.get("id") if next_feature else None
    return stable_hash(ledger) != before


def _collect_project_evidence(project_root: Path, report: PrivacyReport) -> dict[str, Any]:
    raw = collect_project_evidence(project_root) if project_root.exists() else {"project_missing": str(project_root)}
    return redact_value(raw, report)


def _record_event(events: list[dict[str, Any]], events_path: Path, event: dict[str, Any]) -> None:
    events.append(event)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _blocking_privacy(flags: list[str]) -> list[str]:
    return sorted(flag for flag in set(flags) if flag in _BLOCKING_PRIVACY_FLAGS)


def _budget_failure(text: str) -> bool:
    lower = text.lower()
    return "budget" in lower or "max_budget" in lower


def _failure_outcome(result: ClaudeRunResult) -> str:
    return "budget_exceeded" if _budget_failure(result.output_text) else "agent_failed"


def _remaining_timeout(started_at: float, max_wall_seconds: int) -> int:
    remaining = max_wall_seconds - int(time.monotonic() - started_at)
    return max(1, min(3600, remaining))


def _append_progress(
    workspace: Path,
    *,
    run_id: str,
    turn_index: int,
    decision: dict[str, Any] | None,
    worker_result: ClaudeRunResult | None,
    coverage: float,
    stop_reason: str,
) -> None:
    if not turn_index:
        return
    action = decision.get("action") if decision else "none"
    focus = decision.get("current_focus") if decision else "none"
    worker_code = "not_run" if worker_result is None else str(worker_result.returncode)
    lines = [
        "",
        f"## {run_id} turn {turn_index}",
        f"- focus: {focus}",
        f"- supervisor_action: {action}",
        f"- worker_returncode: {worker_code}",
        f"- validation_coverage: {coverage:.2f}",
        f"- stop_or_next: {stop_reason or 'continue'}",
    ]
    with (workspace / "progress.md").open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _events_ref(events_path: Path, run_path: Path) -> str:
    try:
        return os.path.relpath(events_path, run_path.parent)
    except ValueError:
        return str(events_path)


def run_workspace(
    workspace: Path,
    *,
    mode: str,
    out: Path | None = None,
    runner: Runner = run_claude_headless,
) -> dict[str, Any]:
    goal = load_goal(workspace / "goal.yaml")
    persona = read_json(workspace / "persona.lock.json")
    ledger = read_json(workspace / "feature_ledger.json")
    project_root = Path(str(goal["project_root"])).expanduser()
    limits = goal.get("limits", {}) if isinstance(goal.get("limits", {}), dict) else {}
    max_turns = max(1, int(limits.get("max_turns", 1) or 1))
    max_wall_seconds = max(1, int(float(limits.get("max_wall_minutes", 30) or 30) * 60))
    max_budget_usd = float(limits.get("max_budget_usd", 1.0))
    run_id = f"run-{stable_hash(now_utc() + ':' + str(workspace) + ':' + uuid.uuid4().hex, length=10)}"
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"
    events_path.touch(exist_ok=True)
    run_path = out or run_dir / "run.json"

    report = PrivacyReport()
    events: list[dict[str, Any]] = []
    evidence_parts: list[str] = []
    risk_markers: list[str] = []
    privacy_blocks: list[str] = []
    supervisor_session_id = ""
    worker_session_id = ""
    supervisor_turns = 0
    worker_turns = 0
    agent_call_count = 0
    human_gate_count = 0
    clarification_count = 0
    no_progress_count = 0
    last_progress_signature = ""
    outcome = "agent_failed"
    stop_reason = "worker completed one supervised turn"
    final_coverage = 0.0
    last_decision: dict[str, Any] | None = None
    last_worker_result: ClaudeRunResult | None = None
    run_history: list[dict[str, Any]] = []
    started_at = time.monotonic()

    if mode == "dry-run":
        project_evidence = _collect_project_evidence(project_root, report)
        supervisor_result = runner(
            _supervisor_prompt(goal, persona, ledger, project_evidence, 1, []),
            cwd=project_root if project_root.exists() else workspace,
            allowed_tools=_allowed_tools(goal, "supervisor"),
            max_budget_usd=max_budget_usd,
            dry_run=True,
            timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
        )
        supervisor_turns = 1
        agent_call_count = 1
        supervisor_session_id = supervisor_result.session_id
        instruction = supervisor_result.output_text.strip()
        if not instruction.startswith(_FALLBACK_STARTS):
            instruction = _fallback_instruction(goal, ledger)
        instruction_redacted, instruction_flags = redact_text(instruction, report)
        _record_event(events, events_path, {
            "timestamp": now_utc(),
            "type": "supervisor_instruction",
            "turn_index": 1,
            "action": "continue",
            "session_hash": stable_hash(supervisor_session_id) if supervisor_session_id else "",
            "text_redacted": instruction_redacted,
            "privacy_flags": instruction_flags,
        })
        evidence_parts.append(instruction)
        outcome = "dry_run"
        stop_reason = "dry-run generated supervisor instruction"
    elif not project_root.exists():
        project_evidence = _collect_project_evidence(project_root, report)
        evidence_parts.append(json.dumps(project_evidence, ensure_ascii=False))
        outcome = "agent_failed"
        stop_reason = "project root missing"
    elif max_budget_usd <= 0:
        outcome = "budget_exceeded"
        stop_reason = "max_budget_usd must be greater than zero"
    else:
        for turn_index in range(1, max_turns + 1):
            if time.monotonic() - started_at > max_wall_seconds:
                outcome = "no_progress"
                stop_reason = "max_wall_minutes exceeded"
                _append_progress(
                    workspace,
                    run_id=run_id,
                    turn_index=turn_index,
                    decision=last_decision,
                    worker_result=last_worker_result,
                    coverage=final_coverage,
                    stop_reason=stop_reason,
                )
                break

            project_evidence = _collect_project_evidence(project_root, report)
            evidence_parts.append(json.dumps(project_evidence, ensure_ascii=False, sort_keys=True))
            supervisor_result = runner(
                _supervisor_prompt(goal, persona, ledger, project_evidence, turn_index, run_history[-8:]),
                cwd=project_root,
                allowed_tools=_allowed_tools(goal, "supervisor"),
                max_budget_usd=max_budget_usd,
                session_id=supervisor_session_id,
                timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
            )
            supervisor_turns += 1
            agent_call_count += 1
            if supervisor_result.session_id:
                supervisor_session_id = supervisor_result.session_id
            supervisor_text = supervisor_result.output_text.strip()
            evidence_parts.append(supervisor_text)
            decision = _parse_supervisor_decision(supervisor_text, goal, ledger)
            last_decision = decision
            instruction_redacted, instruction_flags = redact_text(decision.get("instruction") or supervisor_text, report)
            reason_redacted, reason_flags = redact_text(str(decision.get("reason") or ""), report)
            event_flags = sorted(set(instruction_flags + reason_flags))
            privacy_blocks.extend(_blocking_privacy(event_flags))
            _record_event(events, events_path, {
                "timestamp": now_utc(),
                "type": "supervisor_instruction",
                "turn_index": turn_index,
                "action": decision.get("action"),
                "current_focus": decision.get("current_focus"),
                "reason_redacted": reason_redacted,
                "session_hash": stable_hash(supervisor_session_id) if supervisor_session_id else "",
                "text_redacted": instruction_redacted,
                "privacy_flags": event_flags,
                "returncode": supervisor_result.returncode,
            })
            run_history.append({
                "turn_index": turn_index,
                "type": "supervisor_instruction",
                "action": decision.get("action"),
                "current_focus": decision.get("current_focus"),
                "reason_redacted": reason_redacted,
                "text_redacted": instruction_redacted[:1000],
                "returncode": supervisor_result.returncode,
            })

            if privacy_blocks:
                outcome = "privacy_blocked"
                stop_reason = "privacy markers require human review"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                break
            if supervisor_result.returncode:
                outcome = _failure_outcome(supervisor_result)
                stop_reason = "supervisor failed"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                break

            ledger_changed = _apply_feature_updates(ledger, decision, report)
            write_json(workspace / "feature_ledger.json", ledger)
            risk_markers.extend(classify_risk(supervisor_text + "\n" + json.dumps(project_evidence, ensure_ascii=False)))
            final_coverage = validation_coverage(goal, "\n".join(evidence_parts + [json.dumps(ledger, ensure_ascii=False)]))

            if risk_markers:
                human_gate_count += 1
                outcome = "needs_human"
                stop_reason = "risk markers require human review"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                break
            if decision.get("action") == "needs_human":
                human_gate_count += 1
                clarification_count += 1
                outcome = "needs_human"
                stop_reason = redact_text(str(decision.get("reason") or "supervisor requested human input"), report)[0]
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                break
            if (decision.get("action") == "stop" or _all_features_completed(ledger)) and worker_turns > 0:
                outcome = "completed" if final_coverage >= 1.0 else "failed_validation"
                stop_reason = "all features completed" if outcome == "completed" else "validation evidence incomplete"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                break
            if (decision.get("action") == "stop" or _all_features_completed(ledger)) and worker_turns == 0:
                decision["action"] = "continue"
                decision["instruction"] = decision.get("instruction") or "只读复核 supervisor 已收集的证据，运行允许的验证命令并返回结果。"

            worker_result = runner(
                _worker_prompt(str(decision.get("instruction") or ""), goal),
                cwd=project_root,
                allowed_tools=_allowed_tools(goal, "worker"),
                max_budget_usd=max_budget_usd,
                session_id=worker_session_id,
                timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
            )
            worker_turns += 1
            agent_call_count += 1
            if worker_result.session_id:
                worker_session_id = worker_result.session_id
            last_worker_result = worker_result
            worker_text = worker_result.output_text.strip()
            evidence_parts.append(worker_text)
            worker_redacted, worker_flags = redact_text(worker_text, report)
            privacy_blocks.extend(_blocking_privacy(worker_flags))
            _record_event(events, events_path, {
                "timestamp": now_utc(),
                "type": "worker_result",
                "turn_index": turn_index,
                "session_hash": stable_hash(worker_session_id) if worker_session_id else "",
                "returncode": worker_result.returncode,
                "text_redacted": worker_redacted[:2000],
                "privacy_flags": worker_flags,
            })
            run_history.append({
                "turn_index": turn_index,
                "type": "worker_result",
                "text_redacted": worker_redacted[:1000],
                "returncode": worker_result.returncode,
            })

            final_coverage = validation_coverage(goal, "\n".join(evidence_parts + [json.dumps(ledger, ensure_ascii=False)]))
            if privacy_blocks:
                outcome = "privacy_blocked"
                stop_reason = "privacy markers require human review"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                break
            if worker_result.returncode:
                outcome = _failure_outcome(worker_result)
                stop_reason = "worker failed"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                break
            worker_risks = classify_risk(worker_text)
            if worker_risks:
                risk_markers.extend(worker_risks)
                human_gate_count += 1
                outcome = "needs_human"
                stop_reason = "risk markers require human review"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                break

            progress_signature = stable_hash({
                "focus": ledger.get("current_focus"),
                "ledger": ledger,
                "project_evidence": project_evidence,
            })
            if not ledger_changed and progress_signature == last_progress_signature:
                no_progress_count += 1
            else:
                no_progress_count = 0
            last_progress_signature = progress_signature
            if no_progress_count >= 3:
                outcome = "no_progress"
                stop_reason = "same focus and evidence repeated without ledger progress"
                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                break
            _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason="continue")
        else:
            outcome = "no_progress"
            stop_reason = f"max_turns reached: {max_turns}"

    write_json(workspace / "feature_ledger.json", ledger)
    final_evidence_text = "\n".join(evidence_parts + [json.dumps(ledger, ensure_ascii=False, sort_keys=True)])
    final_coverage = validation_coverage(goal, final_evidence_text)
    counts = _feature_status_counts(ledger)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "goal_ref": "goal.yaml",
        "persona_ref": "persona.lock.json",
        "ledger_ref": "feature_ledger.json",
        "events_ref": _events_ref(events_path, run_path),
        "supervisor_session_id": stable_hash(supervisor_session_id) if supervisor_session_id else "",
        "worker_session_id": stable_hash(worker_session_id) if worker_session_id else "",
        "outcome": outcome,
        "stop_reason": stop_reason,
        "metrics": {
            "turns": len(events),
            "agent_call_count": agent_call_count,
            "supervisor_turns": supervisor_turns,
            "worker_turns": worker_turns,
            "human_gate_count": human_gate_count,
            "clarification_count": clarification_count,
            "retry_count": no_progress_count,
            "blocked_risky_actions": len(set(risk_markers)),
            "validation_coverage": final_coverage,
            "completed_feature_count": counts["completed"],
            "pending_feature_count": counts["pending"],
            "blocked_feature_count": counts["blocked"],
            "in_progress_feature_count": counts["in_progress"],
            "event_count": len(events),
        },
        "privacy_report": report.as_dict(),
        "validation_report": {
            "risk_markers": sorted(set(risk_markers)),
            "mode": mode,
            "privacy_blocks": sorted(set(privacy_blocks)),
        },
    }
    write_json(run_path, run)
    return run
