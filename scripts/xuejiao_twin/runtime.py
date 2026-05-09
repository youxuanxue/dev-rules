from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import SCHEMA_VERSION
from .claude_runner import ClaudeRunResult, run_claude_headless
from .evidence import collect_project_evidence, validation_command_status, validation_coverage
from .initializer import load_goal
from .privacy import PrivacyReport, redact_text, redact_value, stable_hash
from .schema_contract import load_schema, validate_schema
from .util import now_utc, read_json, write_json

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE_CMD = f"PYTHONPATH={shlex.quote(str(REPO_ROOT))} python3 -m scripts.xuejiao_twin"

Runner = Callable[..., ClaudeRunResult]
_ACTIONS = {"draft_ledger", "continue", "stop", "needs_human"}
_FEATURE_STATUSES = {"pending", "in_progress", "blocked", "completed", "deferred"}
_LEDGER_PLANNING_STATUSES = {"needs_draft", "drafted", "approved"}
_MAX_DRAFT_FEATURES = 20
_PROCESS_FEATURE_PREFIXES = ("先盘点", "盘点", "了解", "分析", "确认", "梳理", "输出缺口矩阵", "生成计划", "制定计划")
_BLOCKING_PRIVACY_FLAGS = {"secret_assignment", "bearer_token", "private_key", "sensitive_url"}
_FALLBACK_STARTS = ("先", "不要", "跑", "给", "修", "定位", "写", "NEEDS_HUMAN")
_DEFAULT_DISALLOWED_TOOLS = {
    "supervisor": [
        "Edit",
        "Write",
        "Bash(git commit *)",
        "Bash(git push *)",
        "Bash(gh pr create *)",
    ],
    "worker": [
        "Bash(git push --force *)",
        "Bash(git push -f *)",
        "Bash(git reset --hard *)",
        "Bash(git checkout -- *)",
        "Bash(git restore *)",
        "Bash(git clean *)",
        "Bash(rm -rf *)",
        "Bash(sudo rm *)",
        "Bash(chmod -R 777 *)",
        "Bash(chown -R *)",
        "Bash(terraform apply *)",
        "Bash(terraform destroy *)",
        "Bash(kubectl apply *)",
        "Bash(kubectl delete *)",
        "Bash(helm upgrade *)",
        "Bash(helm uninstall *)",
        "Bash(fly deploy *)",
        "Bash(vercel deploy *)",
        "Bash(npm publish *)",
        "Bash(pnpm publish *)",
        "Bash(yarn publish *)",
        "Bash(twine upload *)",
        "Bash(docker push *)",
        "Bash(dropdb *)",
        "Bash(psql * drop *)",
        "Bash(mysql * drop *)",
    ],
}

HUMAN_RESPONSE_FILE = "human_response.json"
SESSION_STATE_FILE = "session_state.json"
HUMAN_ACTIONS = {
    "approve_and_continue",
    "request_plan_delta",
    "defer_feature",
    "stop_session",
}
SUPERVISOR_DECISION_SCHEMA = "xuejiao_twin.supervisor_decision.schema.json"
LEDGER_DRAFT_SCHEMA = "xuejiao_twin.ledger_draft.schema.json"
WORKER_RESULT_SCHEMA = "xuejiao_twin.worker_result.schema.json"


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


def _completion_handoff_ready(ledger: dict[str, Any], last_run: dict[str, Any] | None = None) -> bool:
    if not _all_features_completed(ledger):
        return False
    if last_run is None:
        return True
    if last_run.get("outcome") == "completed":
        return True
    if last_run.get("outcome") != "needs_human":
        return False
    validation_report = last_run.get("validation_report") if isinstance(last_run.get("validation_report"), dict) else {}
    if validation_report.get("privacy_blocks") or validation_report.get("risk_markers"):
        return False
    review = last_run.get("human_review") if isinstance(last_run.get("human_review"), dict) else {}
    blocked_features = review.get("blocked_features") if isinstance(review, dict) else []
    if blocked_features:
        return False
    stop_reason = str(last_run.get("stop_reason") or "").lower()
    hard_gate_markers = (
        "privacy",
        "content filter",
        "session was silently reset",
        "ledger quality",
        "base checkout",
        "validation evidence incomplete",
        "validation gap",
    )
    return not any(marker in stop_reason for marker in hard_gate_markers)


def _feature_status_counts(ledger: dict[str, Any]) -> dict[str, int]:
    counts = {status: 0 for status in _FEATURE_STATUSES}
    for feature in ledger.get("features", []):
        status = str(feature.get("status", ""))
        if status in counts:
            counts[status] += 1
    return counts


def _ledger_is_empty(ledger: dict[str, Any]) -> bool:
    features = ledger.get("features", [])
    return not isinstance(features, list) or not features


def _ledger_planning_status(ledger: dict[str, Any]) -> str:
    status = str(ledger.get("planning_status") or "").strip()
    if status in _LEDGER_PLANNING_STATUSES:
        return status
    return "needs_draft" if _ledger_is_empty(ledger) else "approved"


def _requires_ledger_review(ledger: dict[str, Any]) -> bool:
    return _ledger_planning_status(ledger) == "drafted"


def _touch_ledger(ledger: dict[str, Any], *, field: str = "updated_at") -> None:
    ledger[field] = now_utc()
    try:
        ledger["revision"] = int(ledger.get("revision") or 0) + 1
    except (TypeError, ValueError):
        ledger["revision"] = 1


def _normalize_current_focus(ledger: dict[str, Any]) -> None:
    current = ledger.get("current_focus")
    feature = _feature_by_id(ledger, str(current or "")) if current else None
    if feature and feature.get("status") in {"pending", "in_progress", "blocked"}:
        ledger["current_focus"] = feature.get("id")
        return
    next_feature = _next_feature(ledger)
    ledger["current_focus"] = next_feature.get("id") if next_feature else None


def _next_feature_id(ledger: dict[str, Any]) -> str:
    highest = 0
    for feature in ledger.get("features", []):
        feature_id = str(feature.get("id") or "")
        if len(feature_id) == 5 and feature_id.startswith("F-") and feature_id[2:].isdigit():
            highest = max(highest, int(feature_id[2:]))
    return f"F-{highest + 1:03d}"


def _redact_string(value: Any, report: PrivacyReport) -> str:
    return redact_text(str(value or ""), report)[0].strip()


def _normalize_acceptance(value: Any, goal: dict[str, Any], report: PrivacyReport) -> list[str]:
    source = value if isinstance(value, list) and value else goal.get("acceptance", [])
    return _redact_list(source, report)


def _normalize_feature(raw: dict[str, Any], index: int, goal: dict[str, Any], report: PrivacyReport, used_ids: set[str]) -> dict[str, Any] | None:
    description = _redact_string(raw.get("description"), report)
    if not description:
        return None
    feature_id = _redact_string(raw.get("id"), report)
    if not (len(feature_id) == 5 and feature_id.startswith("F-") and feature_id[2:].isdigit()) or feature_id in used_ids:
        feature_id = f"F-{index:03d}"
        while feature_id in used_ids:
            index += 1
            feature_id = f"F-{index:03d}"
    used_ids.add(feature_id)
    blocked_reason = raw.get("blocked_reason")
    return {
        "id": feature_id,
        "description": description,
        "status": "pending",
        "acceptance": _normalize_acceptance(raw.get("acceptance"), goal, report),
        "validation_evidence": [],
        "blocked_reason": _redact_string(blocked_reason, report) if blocked_reason is not None else None,
    }


def _audit_like_goal(goal: dict[str, Any]) -> bool:
    text = "\n".join(str(goal.get(key, "")) for key in ("goal", "risk_policy", "mode"))
    return any(marker in text.lower() for marker in ("audit", "review", "盘点", "审计", "复核"))


def _same_string_list(left: Any, right: Any) -> bool:
    if not isinstance(left, list) or not isinstance(right, list):
        return False
    return [str(item).strip() for item in left] == [str(item).strip() for item in right]


def _lint_ledger(ledger: dict[str, Any], goal: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    features = ledger.get("features")
    if not isinstance(features, list):
        return ["features must be a list"]
    if not features:
        errors.append("features must not be empty after draft")
    if len(features) > _MAX_DRAFT_FEATURES:
        errors.append(f"features must not exceed {_MAX_DRAFT_FEATURES}")
    seen: set[str] = set()
    for index, feature in enumerate(features, 1):
        if not isinstance(feature, dict):
            errors.append(f"feature {index} must be an object")
            continue
        feature_id = str(feature.get("id") or "")
        if not feature_id:
            errors.append(f"feature {index} missing id")
        elif feature_id in seen:
            errors.append(f"duplicate feature id: {feature_id}")
        seen.add(feature_id)
        description = str(feature.get("description") or "").strip()
        if not description:
            errors.append(f"{feature_id or index} missing description")
        elif not _audit_like_goal(goal) and description.startswith(_PROCESS_FEATURE_PREFIXES):
            errors.append(f"{feature_id or index} is a process step, not a deliverable feature")
        elif description == str(goal.get("goal") or "").strip() or len(description) > 240:
            errors.append(f"{feature_id or index} is too broad for one feature")
        if feature.get("status") not in _FEATURE_STATUSES:
            errors.append(f"{feature_id or index} has invalid status")
        if not isinstance(feature.get("acceptance"), list) or not any(str(item).strip() for item in feature.get("acceptance", [])):
            errors.append(f"{feature_id or index} missing acceptance")
        elif _same_string_list(feature.get("acceptance"), goal.get("acceptance", [])):
            errors.append(f"{feature_id or index} copies global acceptance instead of feature-specific acceptance")
        if not isinstance(feature.get("validation_evidence"), list):
            errors.append(f"{feature_id or index} validation_evidence must be a list")
    current = ledger.get("current_focus")
    if current is not None and str(current) not in seen:
        errors.append("current_focus must reference an existing feature")
    return errors


def _parse_ledger_draft(text: str, goal: dict[str, Any], report: PrivacyReport) -> tuple[dict[str, Any] | None, list[str]]:
    parsed = _json_from_text(text)
    if parsed is None:
        return None, ["worker did not return JSON ledger draft"]
    schema_errors = _schema_errors(parsed, LEDGER_DRAFT_SCHEMA)
    if schema_errors:
        return None, schema_errors
    raw_features = parsed.get("features", [])
    if not isinstance(raw_features, list):
        return None, ["draft features must be a list"]
    if len(raw_features) > _MAX_DRAFT_FEATURES:
        return None, [f"draft features must not exceed {_MAX_DRAFT_FEATURES}"]
    used_ids: set[str] = set()
    features: list[dict[str, Any]] = []
    for index, raw_feature in enumerate(raw_features[:_MAX_DRAFT_FEATURES], 1):
        if not isinstance(raw_feature, dict):
            continue
        feature = _normalize_feature(raw_feature, index, goal, report, used_ids)
        if feature:
            features.append(feature)
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "features": features,
        "current_focus": str(parsed.get("current_focus") or (features[0]["id"] if features else "")) or None,
        "last_verified_at": None,
        "planning_status": "drafted",
        "generated_at": now_utc(),
        "updated_at": now_utc(),
        "revision": 1,
    }
    _normalize_current_focus(ledger)
    errors = _lint_ledger(ledger, goal)
    return (None, errors) if errors else (ledger, [])


def _apply_ledger_updates(ledger: dict[str, Any], decision: dict[str, Any], goal: dict[str, Any], report: PrivacyReport) -> bool:
    before = stable_hash(ledger)
    updates = decision.get("ledger_updates", {})
    if not isinstance(updates, dict):
        return False
    features = ledger.setdefault("features", [])
    if not isinstance(features, list):
        ledger["features"] = []
        features = ledger["features"]
    used_ids = {str(feature.get("id")) for feature in features if isinstance(feature, dict) and feature.get("id")}
    for raw_feature in updates.get("add_features", []) if isinstance(updates.get("add_features", []), list) else []:
        if not isinstance(raw_feature, dict):
            continue
        feature = _normalize_feature(raw_feature, len(features) + 1, goal, report, used_ids)
        if feature:
            features.append(feature)
    for raw_update in updates.get("update_features", []) if isinstance(updates.get("update_features", []), list) else []:
        if not isinstance(raw_update, dict):
            continue
        feature = _feature_by_id(ledger, str(raw_update.get("id") or ""))
        if not feature or feature.get("status") == "completed":
            continue
        if "description" in raw_update:
            description = _redact_string(raw_update.get("description"), report)
            if description:
                feature["description"] = description
        if "acceptance" in raw_update:
            acceptance = _normalize_acceptance(raw_update.get("acceptance"), goal, report)
            if acceptance:
                feature["acceptance"] = acceptance
        status = str(raw_update.get("status") or "").strip()
        if status in _FEATURE_STATUSES:
            feature["status"] = status
        if "blocked_reason" in raw_update:
            reason = raw_update.get("blocked_reason")
            feature["blocked_reason"] = _redact_string(reason, report) if reason is not None else None
    current_focus = updates.get("current_focus")
    if current_focus is not None:
        ledger["current_focus"] = _redact_string(current_focus, report) or None
    _normalize_current_focus(ledger)
    changed = stable_hash(ledger) != before
    if changed:
        _touch_ledger(ledger)
    return changed


def _approve_ledger_if_ready(ledger: dict[str, Any]) -> bool:
    if _requires_ledger_review(ledger):
        ledger["planning_status"] = "approved"
        ledger["reviewed_at"] = now_utc()
        _touch_ledger(ledger)
        return True
    return False


def _persona_instruction_policy(persona: dict[str, Any], turn_index: int) -> dict[str, Any]:
    interaction = persona.get("interaction_policy", {})
    if not isinstance(interaction, dict):
        interaction = {}
    decision = persona.get("decision_policy", {})
    if not isinstance(decision, dict):
        decision = {}
    if turn_index == 1:
        turn_policy = interaction.get("first_turn_policy") or interaction.get("first_turn_instruction") or decision.get("start_task")
    else:
        turn_policy = interaction.get("subsequent_turn_policy") or interaction.get("next_turn_policy") or decision.get("during_task")
    return {
        "turn": "first" if turn_index == 1 else "subsequent",
        "worker_instruction_style": interaction.get("worker_instruction_style") or interaction.get("instruction_style") or "",
        "all_turns_policy": interaction.get("all_turns_policy") or interaction.get("instruction_policy") or "",
        "turn_policy": turn_policy or "",
    }


def _supervisor_system(goal: dict[str, Any], persona: dict[str, Any]) -> str:
    payload = {
        "role": "xuejiao supervisor",
        "contract": "Return exactly one JSON object matching output_schema. No markdown, no prose. Do not edit code. Actions: draft_ledger | continue | stop | needs_human. Stop for human gates; otherwise keep work moving.",
        "output_schema_name": SUPERVISOR_DECISION_SCHEMA,
        "output_schema": load_schema(SUPERVISOR_DECISION_SCHEMA),
        "goal": goal.get("goal"),
        "acceptance": goal.get("acceptance", []),
        "validation_commands": goal.get("validation_commands", []),
        "scope_out": goal.get("scope_out", []),
        "persona_policy": persona.get("interaction_policy", {}),
    }
    return "xuejiao supervisor stable contract:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _supervisor_user(
    ledger: dict[str, Any],
    evidence: dict[str, Any],
    turn_index: int,
    run_history: list[dict[str, Any]] | None = None,
    validation_gap: bool = False,
) -> str:
    feature = _next_feature(ledger)
    planning_status = _ledger_planning_status(ledger)
    if validation_gap:
        phase = "validation_gap: completed ledger lacks goal validation evidence; add ledger feature(s) or needs_human."
    elif _ledger_is_empty(ledger) or planning_status == "needs_draft":
        phase = "needs_draft: use draft_ledger before implementation."
    elif planning_status == "drafted":
        phase = "drafted: review ledger; continue approves it, draft_ledger revises it."
    else:
        phase = "approved: continue implementation; use ledger_updates only for real structural gaps."
    return json.dumps({
        "phase": phase,
        "output_schema_name": SUPERVISOR_DECISION_SCHEMA,
        "turn_index": turn_index,
        "current_focus": feature.get("id") if feature else None,
        "ledger_planning_status": planning_status,
        "feature_ledger": ledger,
        "project_evidence": evidence,
        "run_history": run_history or [],
    }, ensure_ascii=False, indent=2)


def _supervisor_prompt(
    goal: dict[str, Any],
    persona: dict[str, Any],
    ledger: dict[str, Any],
    evidence: dict[str, Any],
    turn_index: int,
    run_history: list[dict[str, Any]] | None = None,
    validation_gap: bool = False,
) -> tuple[str, str]:
    return (
        _supervisor_system(goal, persona),
        _supervisor_user(ledger, evidence, turn_index, run_history, validation_gap),
    )


def _fallback_instruction(goal: dict[str, Any], ledger: dict[str, Any]) -> str:
    if _ledger_is_empty(ledger) or _ledger_planning_status(ledger) == "needs_draft":
        return "请基于 goal.yaml 和只读项目证据生成 feature_ledger 草案 JSON，不要改代码，不要写文件。"
    feature = _next_feature(ledger)
    if feature:
        return f"请直接实现：{feature['description']}。做最小可验证改动，不要先写计划或等确认；完成后给 diff summary 和验证结果。"
    return f"请直接验证目标已完成，运行验证命令并给出最终 diff summary：{goal.get('goal', '')}"


def _ledger_draft_system(goal: dict[str, Any]) -> str:
    payload = {
        "role": "worker ledger planner",
        "contract": "Read only. No edits, no file writes. Return exactly one JSON object matching output_schema. No markdown, no prose.",
        "output_schema_name": LEDGER_DRAFT_SCHEMA,
        "output_schema": load_schema(LEDGER_DRAFT_SCHEMA),
        "goal": goal.get("goal"),
        "scope_in": goal.get("scope_in", []),
        "scope_out": goal.get("scope_out", []),
        "acceptance": goal.get("acceptance", []),
        "validation_commands": goal.get("validation_commands", []),
        "rules": [
            "features are deliverables, not planning/inventory steps",
            "each feature is independently verifiable",
            "no secrets or private raw data",
        ],
    }
    return "xuejiao twin ledger planner stable contract:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _ledger_draft_user(
    instruction: str,
    ledger: dict[str, Any],
    evidence: dict[str, Any],
    run_history: list[dict[str, Any]],
) -> str:
    return json.dumps({
        "instruction": instruction,
        "output_schema_name": LEDGER_DRAFT_SCHEMA,
        "current_ledger": ledger,
        "project_evidence": evidence,
        "run_history": run_history,
    }, ensure_ascii=False, indent=2)


def _ledger_draft_prompt(
    instruction: str,
    goal: dict[str, Any],
    ledger: dict[str, Any],
    evidence: dict[str, Any],
    run_history: list[dict[str, Any]],
) -> tuple[str, str]:
    return (
        _ledger_draft_system(goal),
        _ledger_draft_user(instruction, ledger, evidence, run_history),
    )


def _worker_system(goal: dict[str, Any]) -> str:
    payload = {
        "role": "worker code agent",
        "contract": "Implement now. Do not ask for confirmation or write a separate plan unless blocked by hard rules. Return exactly one JSON object matching output_schema. No markdown, no prose.",
        "output_schema_name": WORKER_RESULT_SCHEMA,
        "output_schema": load_schema(WORKER_RESULT_SCHEMA),
        "goal": goal.get("goal"),
        "scope_out": goal.get("scope_out", []),
        "validation_commands": goal.get("validation_commands", []),
        "hard_rules": [
            "Do not modify or push main/master.",
            "Do not introduce dependencies unless explicitly approved.",
            "Stop and report architecture/security/data decisions.",
            "Keep moving through the next deliverable; runtime owns authoritative validation commands.",
            "Return changed files, lightweight validation hints, failures, and blockers.",
        ],
    }
    return "xuejiao twin worker stable contract:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _worker_user(instruction: str, ledger: dict[str, Any] | None = None, run_history: list[dict[str, Any]] | None = None) -> str:
    payload: dict[str, Any] = {
        "instruction": instruction,
        "output_schema_name": WORKER_RESULT_SCHEMA,
    }
    if ledger is not None:
        payload["feature_ledger"] = ledger
        payload["current_focus"] = ledger.get("current_focus")
        payload["next_feature"] = _next_feature(ledger)
    if run_history:
        payload["recent_run_history"] = run_history[-6:]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _worker_prompt(instruction: str, goal: dict[str, Any], ledger: dict[str, Any] | None = None, run_history: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    return _worker_system(goal), _worker_user(instruction, ledger, run_history)


def _role_tools(goal: dict[str, Any], field: str, role: str) -> list[str]:
    configured = goal.get(field, {})
    if isinstance(configured, dict):
        tools = configured.get(role)
        if isinstance(tools, list):
            return [str(tool) for tool in tools if str(tool)]
    return []


def _allowed_tools(goal: dict[str, Any], role: str) -> list[str]:
    tools = _role_tools(goal, "allowed_tools", role)
    if tools:
        return tools
    if role == "supervisor":
        return ["Read", "Bash(git status *)", "Bash(git diff *)"]
    return ["Read", "Edit", "Write", "Bash"]


def _disallowed_tools(goal: dict[str, Any], role: str) -> list[str]:
    tools: list[str] = []
    for tool in _DEFAULT_DISALLOWED_TOOLS.get(role, []):
        if tool not in tools:
            tools.append(tool)
    for tool in _role_tools(goal, "disallowed_tools", role):
        if tool not in tools:
            tools.append(tool)
    return tools


def _permission_mode(goal: dict[str, Any], role: str) -> str:
    configured = goal.get("permission_mode", "")
    if isinstance(configured, dict):
        return str(configured.get(role) or "")
    return str(configured or "")


def _ledger_draft_allowed_tools(goal: dict[str, Any]) -> list[str]:
    return [tool for tool in _allowed_tools(goal, "supervisor") if tool not in {"Edit", "Write"}]


def _ledger_draft_disallowed_tools(goal: dict[str, Any]) -> list[str]:
    tools = list(_disallowed_tools(goal, "worker"))
    for tool in ["Edit", "Write"]:
        if tool not in tools:
            tools.append(tool)
    return tools


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
    for parsed in reversed(parsed_objects):
        if isinstance(parsed.get("features"), list):
            return parsed
    for parsed in reversed(parsed_objects):
        if "summary" in parsed and isinstance(parsed.get("validation"), list):
            return parsed
    return parsed_objects[-1] if parsed_objects else None


def _schema_errors(value: dict[str, Any] | None, schema_name: str) -> list[str]:
    if value is None:
        return ["missing JSON object"]
    return validate_schema(value, schema_name)


def _schema_error_text(schema_name: str, errors: list[str]) -> str:
    return f"{schema_name}: " + "; ".join(errors[:5])


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
            "ledger_updates": {},
            "reason": stripped,
        }
    parsed = _json_from_text(stripped)
    force_draft = _ledger_is_empty(ledger) or _ledger_planning_status(ledger) == "needs_draft"
    if parsed is None:
        instruction = stripped if stripped else _fallback_instruction(goal, ledger)
        return {
            "action": "draft_ledger" if force_draft else "continue",
            "current_focus": fallback_focus,
            "instruction": instruction,
            "feature_updates": [],
            "ledger_updates": {},
            "reason": "plain text supervisor instruction",
            "schema_errors": ["missing JSON object"],
        }
    errors = _schema_errors(parsed, SUPERVISOR_DECISION_SCHEMA)
    if errors:
        return {
            "action": "needs_human" if "NEEDS_HUMAN" in stripped else "stop",
            "current_focus": fallback_focus,
            "instruction": "",
            "feature_updates": [],
            "ledger_updates": {"add_features": [], "update_features": [], "current_focus": None},
            "reason": _schema_error_text(SUPERVISOR_DECISION_SCHEMA, errors),
            "schema_errors": errors,
        }
    action = str(parsed.get("action") or "continue").strip().lower()
    if action not in _ACTIONS:
        action = "continue"
    if force_draft and action not in {"draft_ledger", "needs_human", "stop"}:
        action = "draft_ledger"
    current_focus = parsed.get("current_focus") or fallback_focus
    if current_focus is not None:
        current_focus = str(current_focus)
    if _feature_by_id(ledger, current_focus) is None:
        current_focus = fallback_focus
    updates = parsed.get("feature_updates", [])
    if not isinstance(updates, list):
        updates = []
    ledger_updates = parsed.get("ledger_updates", {})
    if not isinstance(ledger_updates, dict):
        ledger_updates = {}
    instruction = str(parsed.get("instruction") or "").strip()
    if action in {"continue", "draft_ledger"} and not instruction:
        instruction = _fallback_instruction(goal, ledger)
    return {
        "action": action,
        "current_focus": current_focus,
        "instruction": instruction,
        "feature_updates": [update for update in updates if isinstance(update, dict)],
        "ledger_updates": ledger_updates,
        "reason": str(parsed.get("reason") or ""),
        "schema_errors": [],
    }


def _parse_worker_result(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    parsed = _json_from_text(text)
    if parsed is None:
        return None, ["worker did not return JSON result"]
    errors = _schema_errors(parsed, WORKER_RESULT_SCHEMA)
    return (None, errors) if errors else (parsed, [])


def _worker_repair_prompt(raw_text: str) -> str:
    return json.dumps({
        "instruction": "上一轮 worker 输出不符合 schema。不要改代码，不要运行工具，只基于上一轮实际结果返回一个符合 worker_result schema 的 JSON 对象。",
        "output_schema_name": WORKER_RESULT_SCHEMA,
        "previous_output_excerpt": raw_text[-4000:],
    }, ensure_ascii=False, indent=2)


def _worker_evidence_text(worker: dict[str, Any] | None, raw_text: str) -> str:
    if worker is None:
        return raw_text
    return json.dumps(worker, ensure_ascii=False, sort_keys=True)


def _redact_list(values: Any, report: PrivacyReport) -> list[str]:
    if not isinstance(values, list):
        return []
    redacted: list[str] = []
    for value in values:
        text, _ = redact_text(str(value), report)
        if text:
            redacted.append(text)
    return redacted


def _complete_focus_from_worker(ledger: dict[str, Any], decision: dict[str, Any], worker: dict[str, Any] | None, report: PrivacyReport) -> bool:
    before = stable_hash(ledger)
    focus = _feature_by_id(ledger, decision.get("current_focus")) or _next_feature(ledger)
    if not focus:
        return False
    focus["status"] = "completed"
    focus["blocked_reason"] = None
    verified_at = now_utc()
    focus["last_verified_at"] = verified_at
    ledger["last_verified_at"] = verified_at
    evidence = focus.setdefault("validation_evidence", [])
    if worker:
        summary = str(worker.get("summary") or "").strip()
        if summary:
            evidence.append(redact_text(f"worker summary: {summary}", report)[0])
        for item in worker.get("validation", []) if isinstance(worker.get("validation"), list) else []:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command") or "").strip()
            status = str(item.get("status") or "").strip()
            if command and status:
                evidence.append(redact_text(f"{command}: {status}", report)[0])
    if len(evidence) > 20:
        del evidence[:-20]
    next_feature = _next_feature(ledger)
    ledger["current_focus"] = next_feature.get("id") if next_feature else None
    changed = stable_hash(ledger) != before
    if changed:
        _touch_ledger(ledger)
    return changed


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
        if len(evidence) > 20:
            del evidence[:-20]
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


def _git_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    return env


def _run_git(args: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=_git_subprocess_env())
    except Exception as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _is_git_repo(path: Path) -> bool:
    code, _ = _run_git(["rev-parse", "--is-inside-work-tree"], path)
    return code == 0


def _current_branch(path: Path) -> str:
    code, text = _run_git(["branch", "--show-current"], path)
    return text.strip() if code == 0 else ""


def _safe_base_dirty(project_root: Path) -> tuple[bool, str]:
    code, status = _run_git(["status", "--short"], project_root)
    if code != 0:
        return False, status
    unsafe: list[str] = []
    for line in status.splitlines():
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if path in {".gitignore"}:
            continue
        if path.startswith(".xuejiao-twin"):
            continue
        unsafe.append(line)
    return not unsafe, "\n".join(unsafe)


def _hook_settings_path(workspace: Path) -> Path:
    return workspace / "hooks" / "settings.json"


def _hook_settings() -> dict[str, Any]:
    command = f"python3 {shlex.quote(str(REPO_ROOT / 'scripts' / 'xuejiao_twin' / 'hook_gate.py'))}"
    tool_matcher = [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]
    plain = [{"hooks": [{"type": "command", "command": command}]}]
    return {
        "hooks": {
            "PreToolUse": tool_matcher,
            "PostToolUse": tool_matcher,
            "SessionStart": plain,
            "PreCompact": plain,
        }
    }


def _write_hook_settings(workspace: Path) -> Path:
    path = _hook_settings_path(workspace)
    write_json(path, _hook_settings())
    return path


def _install_project_hook_settings(worker_root: Path) -> Path:
    path = worker_root / ".claude" / "settings.local.json"
    write_json(path, _hook_settings())
    return path


def _worker_claude_md(goal: dict[str, Any]) -> str:
    scope_out = list(goal.get("scope_out", []) or [])
    validation_commands = list(goal.get("validation_commands", []) or [])
    lines = [
        "# xuejiao twin worker contract",
        "",
        "This directory is a xuejiao-twin managed worker worktree.",
        "A supervisor process is monitoring this session.",
        "",
        "## Hard rules",
        "",
        "- Do not modify or push main/master.",
        "- Do not introduce dependencies unless explicitly approved.",
        "- Stop and report architecture, security, data, dependency, production deploy, external side-effect, or destructive decisions.",
        "- Return exactly one JSON object matching the schema for the role you are in. No markdown, no prose.",
        "",
        "## Output schemas",
        "",
        "- Worker turns: `xuejiao_twin.worker_result.schema.json`",
        "- Ledger planner turns (read only): `xuejiao_twin.ledger_draft.schema.json`",
        "",
        "## Goal",
        "",
        f"- {goal.get('goal', '')}",
    ]
    if scope_out:
        lines.extend(["", "## Scope out", ""])
        for item in scope_out:
            lines.append(f"- {item}")
    if validation_commands:
        lines.extend(["", "## Validation commands", ""])
        for command in validation_commands:
            lines.append(f"- `{command}`")
    return "\n".join(lines) + "\n"


def _install_worker_claude_md(worker_root: Path, goal: dict[str, Any]) -> Path:
    path = worker_root / ".claude" / "CLAUDE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_worker_claude_md(goal), encoding="utf-8")
    return path


def _session_state_path(workspace: Path) -> Path:
    return workspace / SESSION_STATE_FILE


def _read_session_state(workspace: Path) -> dict[str, Any]:
    path = _session_state_path(workspace)
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_session_state(workspace: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_utc()
    write_json(_session_state_path(workspace), state)


def _worker_isolation_mode(goal: dict[str, Any]) -> str:
    configured = goal.get("worker_isolation", {})
    if isinstance(configured, dict):
        mode = str(configured.get("mode") or "auto")
    else:
        mode = str(configured or "auto")
    return mode if mode in {"auto", "required", "off"} else "auto"


def _ensure_worker_root(workspace: Path, project_root: Path, goal: dict[str, Any], state: dict[str, Any]) -> tuple[Path, str, str, str]:
    mode = _worker_isolation_mode(goal)
    if mode == "off":
        return project_root, "in_place_off", _current_branch(project_root), ""
    if not _is_git_repo(project_root):
        if mode == "required":
            return project_root, "needs_human", "", "worker_isolation required but project_root is not a git repo"
        return project_root, "in_place_non_git", "", ""
    safe, dirty = _safe_base_dirty(project_root)
    if not safe:
        return project_root, "needs_human", _current_branch(project_root), "base checkout has existing changes:\n" + dirty
    worker_root = Path(str(state.get("worker_cwd") or workspace / "worktrees" / "worker"))
    branch = str(state.get("worker_branch") or f"xuejiao-twin/{stable_hash(str(workspace), length=10)}")
    if worker_root.exists():
        return worker_root, "worktree", branch, ""
    code, output = _run_git(["worktree", "add", "-B", branch, str(worker_root), "HEAD"], project_root, timeout=120)
    if code != 0:
        return project_root, "needs_human", branch, "failed to create worker worktree: " + output
    return worker_root, "worktree", branch, ""


def _collect_project_evidence(project_root: Path, report: PrivacyReport) -> dict[str, Any]:
    raw = collect_project_evidence(project_root) if project_root.exists() else {"project_missing": str(project_root)}
    return redact_value(raw, report)


def _record_event(events: list[dict[str, Any]], events_path: Path, event: dict[str, Any]) -> None:
    events.append(event)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _run_validation_commands(goal: dict[str, Any], cwd: Path, report: PrivacyReport) -> tuple[list[dict[str, Any]], str]:
    commands = [str(command) for command in goal.get("validation_commands", []) if str(command).strip()]
    statuses: list[dict[str, Any]] = []
    evidence_lines: list[str] = []
    for command in commands:
        try:
            proc = subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True, timeout=300, env=_git_subprocess_env())
            output = (proc.stdout + proc.stderr).strip()
            returncode = proc.returncode
        except Exception as exc:
            output = str(exc)
            returncode = 1
        status = "passed" if returncode == 0 else "failed"
        redacted_output, flags = redact_text(output, report)
        evidence = redacted_output[-1000:]
        item = {
            "command": command,
            "status": status,
            "returncode": returncode,
            "evidence": evidence,
            "privacy_flags": flags,
        }
        statuses.append(item)
        evidence_lines.append(json.dumps({"command": command, "status": status, "returncode": returncode, "evidence": evidence}, ensure_ascii=False, sort_keys=True))
    return statuses, "\n".join(evidence_lines)


def _count_hook_events(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return counts
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = str(event.get("type") or "").strip()
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def _blocking_privacy(flags: list[str]) -> list[str]:
    return sorted(flag for flag in set(flags) if flag in _BLOCKING_PRIVACY_FLAGS)


def _budget_failure(text: str) -> bool:
    lower = text.lower()
    return "budget" in lower or "max_budget" in lower


def _content_filter_failure(text: str) -> bool:
    lower = text.lower()
    return "sensitive_words_detected" in lower or "content filter" in lower


def _failure_outcome(result: ClaudeRunResult) -> str:
    if _budget_failure(result.output_text):
        return "budget_exceeded"
    if _content_filter_failure(result.output_text):
        return "needs_human"
    return "agent_failed"


def _failure_reason(result: ClaudeRunResult, role: str) -> str:
    if _content_filter_failure(result.output_text):
        return f"{role} API content filter blocked request; inspect prompt/run artifact before retrying"
    return f"{role} failed"


def _remaining_timeout(started_at: float, max_wall_seconds: int) -> int:
    remaining = max_wall_seconds - int(time.monotonic() - started_at)
    return max(1, min(3600, remaining))


def _write_current(
    workspace: Path,
    *,
    status: str,
    goal: dict[str, Any],
    ledger: dict[str, Any],
    run: dict[str, Any] | None = None,
    next_action: str = "",
    validation_statuses: list[dict[str, str]] | None = None,
    worker_cwd: str = "",
) -> None:
    focus_id = ledger.get("current_focus")
    focus = _feature_by_id(ledger, str(focus_id or "")) if focus_id else None
    counts = _feature_status_counts(ledger)
    lines = [
        "# xuejiao twin current",
        "",
        f"- Status: {status}",
        f"- Goal: {goal.get('goal', '')}",
        f"- Focus: {focus_id or 'none'}" + (f" — {focus.get('description')}" if focus else ""),
        f"- Ledger: revision={ledger.get('revision', 'n/a')} completed={counts['completed']} pending={counts['pending']} in_progress={counts['in_progress']} blocked={counts['blocked']} deferred={counts['deferred']}",
    ]
    if run:
        lines.extend([
            f"- Last run: {run.get('run_id', '')}",
            f"- Outcome: {run.get('outcome', '')}",
            f"- Stop reason: {run.get('stop_reason', '')}",
            f"- Events: {run.get('events_ref', '')}",
        ])
    headless = run.get("headless", {}) if isinstance(run, dict) else {}
    worker_path = worker_cwd or (str(headless.get("worker_cwd") or "") if isinstance(headless, dict) else "")
    if worker_path:
        lines.append(f"- Worker cwd: {worker_path}")
    review = run.get("human_review") if isinstance(run, dict) else None
    if isinstance(review, dict) and bool(review.get("needed")):
        lines.append("- Human decision:")
        lines.append(f"  - trigger: {review.get('trigger', '')}")
        lines.append(f"  - summary: {review.get('summary', '')}")
        blocked = review.get("blocked_features")
        if isinstance(blocked, list) and blocked:
            lines.append("  - blocked_features:")
            for item in blocked:
                if not isinstance(item, dict):
                    continue
                reason = str(item.get("blocked_reason") or "")
                lines.append(f"    - {item.get('id', '')}: {item.get('description', '')}")
                if reason:
                    lines.append(f"      reason: {reason}")
        focus_for_hint = str(review.get("current_focus") or ledger.get("current_focus") or "")
        actions = review.get("suggested_actions")
        if isinstance(actions, list) and actions:
            lines.append("  - respond_commands:")
            for item in actions:
                if not isinstance(item, dict):
                    continue
                action_id = str(item.get("id") or "")
                if not action_id:
                    continue
                command = f"{CLI_MODULE_CMD} respond --workspace {shlex.quote(str(workspace))} --action {action_id}"
                if focus_for_hint:
                    command += f" --feature {focus_for_hint}"
                command += " --note '<你的决策说明>'"
                lines.append(f"    - {action_id}: {command}")
    if validation_statuses:
        lines.append("- Validation:")
        for item in validation_statuses:
            lines.append(f"  - {item['command']}: {item['status']} ({item['evidence']})")
    blockers = []
    for feature in ledger.get("features", []):
        if isinstance(feature, dict) and feature.get("status") in {"blocked", "deferred"}:
            blockers.append(f"{feature.get('id')}: {feature.get('blocked_reason') or feature.get('description')}")
    if blockers:
        lines.append("- Blockers:")
        lines.extend(f"  - {item}" for item in blockers)
    if next_action:
        lines.append(f"- Next: {next_action}")
    (workspace / "CURRENT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_progress(
    workspace: Path,
    *,
    run_id: str,
    turn_index: int,
    decision: dict[str, Any] | None,
    worker_result: ClaudeRunResult | None,
    coverage: float,
    stop_reason: str,
    ledger: dict[str, Any] | None = None,
    validation_statuses: list[dict[str, str]] | None = None,
) -> None:
    if not turn_index:
        return
    if ledger is None:
        try:
            ledger = read_json(workspace / "feature_ledger.json")
        except Exception:
            ledger = None
    if validation_statuses is None and ledger is not None:
        validation_statuses = []
    action = decision.get("action") if decision else "none"
    focus = decision.get("current_focus") if decision else "none"
    worker_code = "not_run" if worker_result is None else str(worker_result.returncode)
    ledger_line = "unknown"
    if ledger:
        counts = _feature_status_counts(ledger)
        ledger_line = f"focus={ledger.get('current_focus')} revision={ledger.get('revision', 'n/a')} completed={counts['completed']} pending={counts['pending']} blocked={counts['blocked']}"
    validation_line = "none"
    if validation_statuses:
        validation_line = "; ".join(f"{item['command']}={item['status']}" for item in validation_statuses)
    changed_line = "unknown"
    if worker_result and worker_result.output_text:
        for line in worker_result.output_text.splitlines():
            lower = line.lower()
            if "changed files" in lower or "changed_files" in lower:
                changed_line = line.strip()[:300]
                break
    lines = [
        "",
        f"## {run_id} turn {turn_index}",
        f"- focus: {focus}",
        f"- supervisor_action: {action}",
        f"- worker_returncode: {worker_code}",
        f"- changed: {changed_line}",
        f"- ledger: {ledger_line}",
        f"- validation: {validation_line}",
        f"- validation_coverage: {coverage:.2f}",
        f"- next: {stop_reason or 'continue'}",
    ]
    with (workspace / "progress.md").open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _events_ref(events_path: Path, run_path: Path) -> str:
    try:
        return os.path.relpath(events_path, run_path.parent)
    except ValueError:
        return str(events_path)


def _human_response_path(workspace: Path) -> Path:
    return workspace / HUMAN_RESPONSE_FILE


def write_human_response(
    workspace: Path,
    *,
    action: str,
    feature_id: str = "",
    run_id: str = "",
    note: str = "",
) -> Path:
    normalized = action.strip()
    if normalized not in HUMAN_ACTIONS:
        allowed = ", ".join(sorted(HUMAN_ACTIONS))
        raise ValueError(f"unsupported action: {action}. allowed: {allowed}")
    payload = {
        "action": normalized,
        "feature_id": feature_id.strip() or None,
        "run_id": run_id.strip() or None,
        "note": note.strip() or None,
        "recorded_at": now_utc(),
    }
    target = _human_response_path(workspace)
    write_json(target, payload)
    return target


def _read_human_response(workspace: Path) -> dict[str, Any] | None:
    path = _human_response_path(workspace)
    if not path.exists():
        return None
    try:
        data = read_json(path)
    except Exception:
        path.unlink(missing_ok=True)
        return None
    if not isinstance(data, dict):
        path.unlink(missing_ok=True)
        return None
    action = str(data.get("action") or "").strip()
    if action not in HUMAN_ACTIONS:
        path.unlink(missing_ok=True)
        return None
    path.unlink(missing_ok=True)
    return {
        "action": action,
        "feature_id": str(data.get("feature_id") or "").strip(),
        "run_id": str(data.get("run_id") or "").strip(),
        "note": str(data.get("note") or "").strip(),
        "recorded_at": str(data.get("recorded_at") or "").strip(),
    }


def _consume_human_response(
    workspace: Path,
    ledger: dict[str, Any],
    report: PrivacyReport,
) -> tuple[dict[str, Any] | None, bool]:
    response = _read_human_response(workspace)
    if response is None:
        return None, False
    action = response.get("action", "")
    note = response.get("note", "")
    focus = _feature_by_id(ledger, ledger.get("current_focus"))
    feature_id = response.get("feature_id") or (focus.get("id") if focus else "")
    feature = _feature_by_id(ledger, feature_id) if feature_id else focus
    changed = False
    if action == "defer_feature" and feature:
        feature["status"] = "deferred"
        if note:
            feature["blocked_reason"] = redact_text(note, report)[0]
        next_feature = _next_feature(ledger)
        ledger["current_focus"] = next_feature.get("id") if next_feature else None
        changed = True
    elif action in {"approve_and_continue", "request_plan_delta"} and feature and feature.get("status") == "blocked":
        feature["status"] = "in_progress"
        feature["blocked_reason"] = None
        changed = True
    return response, changed


def _latest_run(workspace: Path) -> dict[str, Any] | None:
    runs = sorted((workspace / "runs").glob("run-*/run.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in runs:
        try:
            data = read_json(path)
        except Exception:
            continue
        if isinstance(data, dict):
            data.setdefault("run_path", str(path))
            return data
    return None


def _ledger_quality_errors(ledger: dict[str, Any], goal: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    features = ledger.get("features", [])
    if features and not ledger.get("planning_status"):
        errors.append("legacy ledger missing planning_status")
    goal_acceptance = goal.get("acceptance", [])
    copied_acceptance = 0
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, dict):
            continue
        if _same_string_list(feature.get("acceptance"), goal_acceptance):
            copied_acceptance += 1
        description = str(feature.get("description") or "")
        if description == str(goal.get("goal") or "").strip() or len(description) > 240:
            errors.append(f"{feature.get('id')}: feature is too broad")
    if features and copied_acceptance == len(features):
        errors.append("all features copy global acceptance")
    return errors


def _has_unresolved_human_gate(run: dict[str, Any] | None, ledger: dict[str, Any]) -> bool:
    if not run:
        return False
    if run.get("outcome") in {"needs_human", "privacy_blocked"}:
        return True
    for feature in ledger.get("features", []):
        if isinstance(feature, dict) and feature.get("status") == "blocked":
            return True
    return False


def _last_event_summary(workspace: Path, last_run: dict[str, Any]) -> str:
    ref = str(last_run.get("events_ref") or "")
    if not ref:
        return ""
    run_path = Path(str(last_run.get("run_path") or ""))
    base = run_path.parent if run_path else workspace
    path = (base / ref) if not Path(ref).is_absolute() else Path(ref)
    if not path.exists():
        return ""
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return ""
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        reason = str(event.get("reason_redacted") or "").strip()
        body = str(event.get("text_redacted") or "").strip()
        if reason and reason not in {"plain text supervisor instruction"}:
            return reason[:300]
        if body:
            return body[:300]
        if reason:
            return reason[:300]
    return ""


def _blocked_latch_result(workspace: Path, goal: dict[str, Any], ledger: dict[str, Any], last_run: dict[str, Any]) -> dict[str, Any]:
    report = PrivacyReport()
    counts = _feature_status_counts(ledger)
    focus = _feature_by_id(ledger, ledger.get("current_focus"))
    guidance = _last_event_summary(workspace, last_run) or str(last_run.get("stop_reason") or "waiting for human response")
    if focus and focus.get("status") in {"blocked", "deferred"}:
        feature_reason = str(focus.get("blocked_reason") or "").strip()
        if feature_reason:
            guidance = f"{guidance} | focus {focus.get('id')}: {feature_reason}"
    completed_handoff = _completion_handoff_ready(ledger, last_run)
    outcome = "completed" if completed_handoff else "needs_human"
    stop_reason = (
        f"completed handoff: all ledger features are completed; previous run {last_run.get('run_id', 'previous run')} requested delivery review"
        if completed_handoff
        else f"blocked latch: waiting for human_response.json after {last_run.get('run_id', 'previous run')} ({last_run.get('outcome', 'unknown')}: {guidance})"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": f"blocked-latch-{stable_hash(str(last_run.get('run_id') or last_run.get('run_path') or workspace), length=10)}",
        "goal_ref": "goal.yaml",
        "persona_ref": "persona.lock.json",
        "ledger_ref": "feature_ledger.json",
        "events_ref": str(last_run.get("events_ref") or ""),
        "supervisor_session_id": str(last_run.get("supervisor_session_id") or ""),
        "worker_session_id": str(last_run.get("worker_session_id") or ""),
        "outcome": outcome,
        "stop_reason": stop_reason,
        "human_review": _human_review_summary(
            outcome=outcome,
            stop_reason=guidance,
            ledger=ledger,
            risk_markers=[],
            privacy_blocks=[],
            report=report,
        ),
        "metrics": {
            "turns": 0,
            "agent_call_count": 0,
            "supervisor_turns": 0,
            "worker_turns": 0,
            "human_gate_count": 0 if completed_handoff else 1,
            "clarification_count": 0,
            "retry_count": 0,
            "blocked_risky_actions": 0,
            "validation_coverage": validation_coverage(goal, json.dumps(ledger, ensure_ascii=False)),
            "completed_feature_count": counts["completed"],
            "pending_feature_count": counts["pending"],
            "blocked_feature_count": counts["blocked"],
            "in_progress_feature_count": counts["in_progress"],
            "event_count": 0,
        },
        "privacy_report": report.as_dict(),
        "validation_report": {
            "risk_markers": [],
            "mode": "completed-handoff-latch" if completed_handoff else "blocked-latch",
            "privacy_blocks": [],
            "validation_commands": validation_command_status(goal, json.dumps(ledger, ensure_ascii=False)),
        },
    }


def _human_review_summary(
    *,
    outcome: str,
    stop_reason: str,
    ledger: dict[str, Any],
    risk_markers: list[str],
    privacy_blocks: list[str],
    report: PrivacyReport,
) -> dict[str, Any]:
    blocked_features: list[dict[str, Any]] = []
    for feature in ledger.get("features", []):
        if feature.get("status") not in {"blocked", "deferred"}:
            continue
        blocked_features.append({
            "id": str(feature.get("id") or ""),
            "description": str(feature.get("description") or ""),
            "blocked_reason": str(feature.get("blocked_reason") or ""),
            "acceptance": [str(item) for item in feature.get("acceptance", []) if str(item)],
            "validation_evidence": [str(item) for item in feature.get("validation_evidence", []) if str(item)],
        })
    needed = outcome == "needs_human" or bool(blocked_features)
    trigger = "none"
    if privacy_blocks:
        trigger = "privacy_blocks"
    elif risk_markers:
        trigger = "risk_markers"
    elif outcome == "needs_human":
        trigger = "needs_human"
    summary_text = stop_reason if needed else "no human review required"
    summary_redacted, _ = redact_text(summary_text, report)
    return {
        "needed": needed,
        "trigger": trigger,
        "current_focus": ledger.get("current_focus"),
        "summary": summary_redacted,
        "blocked_features": blocked_features,
        "suggested_actions": [
            {
                "id": "approve_and_continue",
                "label": "确认方案并继续",
                "effect": "清除当前 blocked 并继续 supervised loop",
                "cli_hint": f"{CLI_MODULE_CMD} respond --workspace <workspace> --action approve_and_continue --feature <feature-id> --note '<审批结论>'",
            },
            {
                "id": "request_plan_delta",
                "label": "要求最小改动清单",
                "effect": "先让 supervisor 输出最小改动方案，再继续",
                "cli_hint": f"{CLI_MODULE_CMD} respond --workspace <workspace> --action request_plan_delta --feature <feature-id> --note '<要求补充项>'",
            },
            {
                "id": "defer_feature",
                "label": "延期当前 feature",
                "effect": "将当前 feature 标记为 deferred 并切换下一项",
                "cli_hint": f"{CLI_MODULE_CMD} respond --workspace <workspace> --action defer_feature --feature <feature-id> --note '<延期原因>'",
            },
            {
                "id": "stop_session",
                "label": "停止本次会话",
                "effect": "保持阻塞状态，等待下一次人工决策",
                "cli_hint": f"{CLI_MODULE_CMD} respond --workspace <workspace> --action stop_session --feature <feature-id> --note '<停止原因>'",
            },
        ],
    }


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
    if mode != "dry-run" and not _human_response_path(workspace).exists():
        latest_run = _latest_run(workspace)
        if _has_unresolved_human_gate(latest_run, ledger):
            result = _blocked_latch_result(workspace, goal, ledger, latest_run or {})
            current_status = "completed_waiting_handoff" if result.get("outcome") == "completed" else "needs_human"
            next_action = "review worker diff and validation evidence; ship if acceptable" if result.get("outcome") == "completed" else f"write human_response.json via `{CLI_MODULE_CMD} respond ...`"
            _write_current(workspace, status=current_status, goal=goal, ledger=ledger, run=result, next_action=next_action)
            return result
    quality_errors = _ledger_quality_errors(ledger, goal)
    if mode != "dry-run" and quality_errors and _ledger_planning_status(ledger) == "approved":
        report = PrivacyReport()
        result = {
            "schema_version": SCHEMA_VERSION,
            "run_id": f"bad-ledger-{stable_hash(quality_errors, length=10)}",
            "goal_ref": "goal.yaml",
            "persona_ref": "persona.lock.json",
            "ledger_ref": "feature_ledger.json",
            "events_ref": "",
            "supervisor_session_id": "",
            "worker_session_id": "",
            "outcome": "needs_human",
            "stop_reason": "ledger quality is poor; run replan before continuing: " + "; ".join(quality_errors[:3]),
            "human_review": _human_review_summary(outcome="needs_human", stop_reason="run replan before continuing", ledger=ledger, risk_markers=[], privacy_blocks=[], report=report),
            "metrics": {"turns": 0, "agent_call_count": 0, "supervisor_turns": 0, "worker_turns": 0, "human_gate_count": 1, "clarification_count": 0, "retry_count": 0, "blocked_risky_actions": 0, "validation_coverage": validation_coverage(goal, json.dumps(ledger, ensure_ascii=False)), "event_count": 0},
            "privacy_report": report.as_dict(),
            "validation_report": {"risk_markers": [], "mode": "bad-ledger", "privacy_blocks": [], "validation_commands": validation_command_status(goal, json.dumps(ledger, ensure_ascii=False)), "ledger_quality_errors": quality_errors},
        }
        _write_current(workspace, status="needs_replan", goal=goal, ledger=ledger, run=result, next_action=f"{CLI_MODULE_CMD} replan --workspace <workspace>")
        return result
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
    session_state = _read_session_state(workspace)
    supervisor_session_id = str(session_state.get("supervisor_session_id") or "")
    worker_session_id = str(session_state.get("worker_session_id") or "")
    worker_root = project_root
    worker_isolation = "not_started"
    worker_branch = ""

    report = PrivacyReport()
    events: list[dict[str, Any]] = []
    evidence_parts: list[str] = []
    risk_markers: list[str] = []
    privacy_blocks: list[str] = []
    pending_supervisor_note = ""
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
    final_validation_statuses: list[dict[str, Any]] = []
    runtime_validation_evidence = ""
    last_decision: dict[str, Any] | None = None
    last_worker_result: ClaudeRunResult | None = None
    run_history: list[dict[str, Any]] = []
    validation_gap_count = 0
    started_at = time.monotonic()

    if mode == "dry-run":
        project_evidence = _collect_project_evidence(project_root, report)
        supervisor_system, supervisor_user_text = _supervisor_prompt(goal, persona, ledger, project_evidence, 1, [])
        supervisor_result = runner(
            supervisor_user_text,
            cwd=project_root if project_root.exists() else workspace,
            allowed_tools=_allowed_tools(goal, "supervisor"),
            max_budget_usd=max_budget_usd,
            dry_run=True,
            timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
            disallowed_tools=_disallowed_tools(goal, "supervisor"),
            permission_mode=_permission_mode(goal, "supervisor"),
            append_system_prompt=supervisor_system,
            setting_sources="project,local",
            strict_mcp_config=True,
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
        worker_root, worker_isolation, worker_branch, isolation_error = _ensure_worker_root(workspace, project_root, goal, session_state)
        if isolation_error:
            outcome = "needs_human"
            stop_reason = isolation_error
            human_gate_count += 1
        else:
            session_state.update({
                "worker_cwd": str(worker_root),
                "worker_branch": worker_branch,
                "worker_isolation": worker_isolation,
                "resume_used": bool(supervisor_session_id or worker_session_id),
                "last_run_id": run_id,
            })
            _write_session_state(workspace, session_state)
        hook_settings_ref = str(_write_hook_settings(workspace))
        project_hook_settings = str(_install_project_hook_settings(worker_root)) if worker_isolation == "worktree" else ""
        worker_claude_md_ref = str(_install_worker_claude_md(worker_root, goal)) if worker_isolation == "worktree" else ""
        session_state["hook_settings_ref"] = hook_settings_ref
        session_state["project_hook_settings"] = project_hook_settings
        session_state["worker_claude_md_ref"] = worker_claude_md_ref
        _write_session_state(workspace, session_state)
        runner_env = {
            "XUEJIAO_TWIN_WORKSPACE": str(workspace),
            "XUEJIAO_TWIN_PROJECT_ROOT": str(project_root),
            "XUEJIAO_TWIN_WORKER_ROOT": str(worker_root),
            "XUEJIAO_TWIN_RUN_EVENTS": str(run_dir / "hook_events.jsonl"),
            "XUEJIAO_TWIN_RUN_ID": run_id,
        }
        response, response_changed = _consume_human_response(workspace, ledger, report)
        if response_changed:
            write_json(workspace / "feature_ledger.json", ledger)
        if response is not None:
            action = response.get("action")
            note = response.get("note")
            if action == "stop_session":
                outcome = "needs_human"
                stop_reason = redact_text(note or "human selected stop_session", report)[0]
            elif action == "request_plan_delta":
                pending_supervisor_note = note or "先输出 F-003 最小改动清单（文件+一行改动+验证命令），不要改代码。"
        if pending_supervisor_note:
            run_history.append({
                "turn_index": 0,
                "type": "human_response",
                "action": "request_plan_delta",
                "text_redacted": redact_text(pending_supervisor_note, report)[0][:1000],
                "returncode": 0,
            })
        if outcome != "needs_human":
            for turn_index in range(1, max_turns + 1):
                if time.monotonic() - started_at > max_wall_seconds:
                    outcome = "no_progress"
                    stop_reason = "max_wall_minutes exceeded"
                    _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=last_decision, worker_result=last_worker_result, coverage=final_coverage, stop_reason=stop_reason)
                    break

                project_evidence = _collect_project_evidence(worker_root, report)
                evidence_parts.append(json.dumps(project_evidence, ensure_ascii=False, sort_keys=True))
                validation_gap = False
                needs_supervisor_turn = turn_index == 1 or _ledger_planning_status(ledger) != "approved" or _ledger_is_empty(ledger) or bool(pending_supervisor_note)
                ledger_changed = False

                if needs_supervisor_turn:
                    supervisor_system, supervisor_user_text = _supervisor_prompt(goal, persona, ledger, project_evidence, turn_index, run_history[-8:], validation_gap=validation_gap)
                    requested_supervisor_session = supervisor_session_id
                    supervisor_result = runner(
                        supervisor_user_text,
                        cwd=worker_root,
                        allowed_tools=_allowed_tools(goal, "supervisor"),
                        max_budget_usd=max_budget_usd,
                        session_id=supervisor_session_id,
                        timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
                        disallowed_tools=_disallowed_tools(goal, "supervisor"),
                        permission_mode=_permission_mode(goal, "supervisor"),
                        role="supervisor",
                        extra_env=runner_env,
                        append_system_prompt=supervisor_system,
                        setting_sources="project,local",
                        strict_mcp_config=True,
                    )
                    supervisor_turns += 1
                    agent_call_count += 1
                    if supervisor_result.session_lost:
                        _record_event(events, events_path, {
                            "timestamp": now_utc(),
                            "type": "session_lost",
                            "role": "supervisor",
                            "turn_index": turn_index,
                            "requested_session_hash": stable_hash(requested_supervisor_session) if requested_supervisor_session else "",
                            "actual_session_hash": stable_hash(supervisor_result.session_id) if supervisor_result.session_id else "",
                        })
                        outcome = "needs_human"
                        stop_reason = "supervisor session was silently reset by Claude Code; run replan or replay before continuing"
                        human_gate_count += 1
                        _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=None, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                        break
                    if supervisor_result.session_id:
                        supervisor_session_id = supervisor_result.session_id
                        session_state["supervisor_session_id"] = supervisor_session_id
                        session_state["last_run_id"] = run_id
                        _write_session_state(workspace, session_state)
                    supervisor_text = supervisor_result.output_text.strip()
                    evidence_parts.append(supervisor_text)
                    decision = _parse_supervisor_decision(supervisor_text, goal, ledger)
                    schema_errors = list(decision.get("schema_errors") or [])
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
                        "schema_name": SUPERVISOR_DECISION_SCHEMA,
                        "schema_valid": not schema_errors,
                        "schema_errors": schema_errors,
                    })
                    run_history.append({
                        "turn_index": turn_index,
                        "type": "supervisor_instruction",
                        "action": decision.get("action"),
                        "current_focus": decision.get("current_focus"),
                        "reason_redacted": reason_redacted,
                        "text_redacted": instruction_redacted[:1000],
                        "returncode": supervisor_result.returncode,
                        "schema_errors": schema_errors,
                    })

                    if schema_errors:
                        if supervisor_result.returncode:
                            outcome = _failure_outcome(supervisor_result)
                            stop_reason = _failure_reason(supervisor_result, "supervisor")
                        else:
                            outcome = "needs_human" if decision.get("action") == "needs_human" else "agent_failed"
                            stop_reason = str(decision.get("reason") or "supervisor schema validation failed")
                        if outcome == "needs_human":
                            human_gate_count += 1
                        _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                        break
                    if privacy_blocks:
                        outcome = "privacy_blocked"
                        stop_reason = "privacy markers require human review"
                        _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                        break
                    if supervisor_result.returncode:
                        outcome = _failure_outcome(supervisor_result)
                        stop_reason = _failure_reason(supervisor_result, "supervisor")
                        _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                        break

                    ledger_changed = _apply_ledger_updates(ledger, decision, goal, report)
                    if decision.get("action") == "continue":
                        ledger_changed = _approve_ledger_if_ready(ledger) or ledger_changed
                    ledger_changed = _apply_feature_updates(ledger, decision, report) or ledger_changed
                    write_json(workspace / "feature_ledger.json", ledger)
                    final_coverage = validation_coverage(goal, "\n".join(evidence_parts + [runtime_validation_evidence, json.dumps(ledger, ensure_ascii=False)]))

                    if decision.get("action") == "needs_human":
                        human_gate_count += 1
                        clarification_count += 1
                        outcome = "needs_human"
                        stop_reason = redact_text(str(decision.get("reason") or "supervisor requested human input"), report)[0]
                        _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                        break
                    if decision.get("action") == "draft_ledger":
                        planner_system, planner_user_text = _ledger_draft_prompt(str(decision.get("instruction") or _fallback_instruction(goal, ledger)), goal, ledger, project_evidence, run_history[-8:])
                        requested_worker_session = worker_session_id
                        draft_result = runner(
                            planner_user_text,
                            cwd=worker_root,
                            allowed_tools=_ledger_draft_allowed_tools(goal),
                            max_budget_usd=max_budget_usd,
                            session_id=worker_session_id,
                            timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
                            disallowed_tools=_ledger_draft_disallowed_tools(goal),
                            permission_mode=_permission_mode(goal, "worker"),
                            role="worker",
                            extra_env=runner_env,
                            append_system_prompt=planner_system,
                            setting_sources="project,local",
                            strict_mcp_config=True,
                        )
                        worker_turns += 1
                        agent_call_count += 1
                        if draft_result.session_lost:
                            _record_event(events, events_path, {
                                "timestamp": now_utc(),
                                "type": "session_lost",
                                "role": "ledger_planner",
                                "turn_index": turn_index,
                                "requested_session_hash": stable_hash(requested_worker_session) if requested_worker_session else "",
                                "actual_session_hash": stable_hash(draft_result.session_id) if draft_result.session_id else "",
                            })
                            outcome = "needs_human"
                            stop_reason = "ledger planner session was silently reset by Claude Code; run replan or replay before continuing"
                            human_gate_count += 1
                            _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=draft_result, coverage=final_coverage, stop_reason=stop_reason)
                            break
                        if draft_result.session_id:
                            worker_session_id = draft_result.session_id
                            session_state["worker_session_id"] = worker_session_id
                            session_state["last_run_id"] = run_id
                            _write_session_state(workspace, session_state)
                        last_worker_result = draft_result
                        draft_text = draft_result.output_text.strip()
                        evidence_parts.append(draft_text)
                        draft_redacted, draft_flags = redact_text(draft_text, report)
                        privacy_blocks.extend(_blocking_privacy(draft_flags))
                        new_ledger, draft_errors = _parse_ledger_draft(draft_text, goal, report)
                        if new_ledger is not None:
                            ledger.clear()
                            ledger.update(new_ledger)
                            write_json(workspace / "feature_ledger.json", ledger)
                            ledger_changed = True
                        _record_event(events, events_path, {
                            "timestamp": now_utc(),
                            "type": "ledger_draft",
                            "turn_index": turn_index,
                            "session_hash": stable_hash(worker_session_id) if worker_session_id else "",
                            "returncode": draft_result.returncode,
                            "text_redacted": draft_redacted[:2000],
                            "privacy_flags": draft_flags,
                            "errors": draft_errors,
                            "schema_name": LEDGER_DRAFT_SCHEMA,
                            "schema_valid": not draft_errors,
                            "schema_errors": draft_errors,
                        })
                        run_history.append({
                            "turn_index": turn_index,
                            "type": "ledger_draft",
                            "text_redacted": draft_redacted[:1000],
                            "errors": draft_errors,
                            "returncode": draft_result.returncode,
                        })
                        final_coverage = validation_coverage(goal, "\n".join(evidence_parts + [runtime_validation_evidence, json.dumps(ledger, ensure_ascii=False)]))
                        if privacy_blocks:
                            outcome = "privacy_blocked"
                            stop_reason = "privacy markers require human review"
                            _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=draft_result, coverage=final_coverage, stop_reason=stop_reason)
                            break
                        if draft_result.returncode:
                            outcome = _failure_outcome(draft_result)
                            stop_reason = _failure_reason(draft_result, "ledger draft worker")
                            _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=draft_result, coverage=final_coverage, stop_reason=stop_reason)
                            break
                        if draft_errors:
                            no_progress_count += 1
                            if no_progress_count >= 3:
                                outcome = "no_progress"
                                stop_reason = "ledger draft validation failed repeatedly"
                                _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=draft_result, coverage=final_coverage, stop_reason=stop_reason)
                                break
                        else:
                            no_progress_count = 0
                        _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=draft_result, coverage=final_coverage, stop_reason="ledger draft generated")
                        continue
                else:
                    feature = _next_feature(ledger)
                    decision = {
                        "action": "continue" if feature else "stop",
                        "current_focus": feature.get("id") if feature else ledger.get("current_focus"),
                        "instruction": _fallback_instruction(goal, ledger),
                        "feature_updates": [],
                        "ledger_updates": {},
                        "reason": "worker-led supervised turn",
                        "schema_errors": [],
                    }
                    last_decision = decision
                    ledger_changed = _apply_feature_updates(ledger, decision, report)
                    write_json(workspace / "feature_ledger.json", ledger)
                    final_coverage = validation_coverage(goal, "\n".join(evidence_parts + [runtime_validation_evidence, json.dumps(ledger, ensure_ascii=False)]))
                    _record_event(events, events_path, {
                        "timestamp": now_utc(),
                        "type": "worker_led_instruction",
                        "turn_index": turn_index,
                        "action": decision.get("action"),
                        "current_focus": decision.get("current_focus"),
                        "text_redacted": redact_text(str(decision.get("instruction") or ""), report)[0][:2000],
                    })
                    run_history.append({
                        "turn_index": turn_index,
                        "type": "worker_led_instruction",
                        "action": decision.get("action"),
                        "current_focus": decision.get("current_focus"),
                        "text_redacted": str(decision.get("instruction") or "")[:1000],
                        "returncode": 0,
                        "schema_errors": [],
                    })

                if (decision.get("action") == "stop" or _all_features_completed(ledger)) and worker_turns > 0:
                    final_validation_statuses, runtime_validation_evidence = _run_validation_commands(goal, worker_root, report)
                    if runtime_validation_evidence:
                        evidence_parts.append(runtime_validation_evidence)
                    for item in final_validation_statuses:
                        _record_event(events, events_path, {
                            "timestamp": now_utc(),
                            "type": "runtime_validation",
                            "turn_index": turn_index,
                            "command": item.get("command"),
                            "status": item.get("status"),
                            "returncode": item.get("returncode"),
                            "evidence": item.get("evidence"),
                            "privacy_flags": item.get("privacy_flags", []),
                        })
                        privacy_blocks.extend(_blocking_privacy(list(item.get("privacy_flags", []))))
                    final_coverage = validation_coverage(goal, "\n".join(evidence_parts + [json.dumps(ledger, ensure_ascii=False)]))
                    if privacy_blocks:
                        outcome = "privacy_blocked"
                        stop_reason = "privacy markers require human review"
                    elif final_coverage >= 1.0:
                        outcome = "completed"
                        stop_reason = "all features completed"
                    else:
                        validation_gap_count += 1
                        outcome = "failed_validation" if validation_gap_count >= 2 else "no_progress"
                        stop_reason = "validation evidence incomplete"
                    _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=None, coverage=final_coverage, stop_reason=stop_reason)
                    break
                if (decision.get("action") == "stop" or _all_features_completed(ledger)) and worker_turns == 0:
                    decision["action"] = "continue"
                    decision["instruction"] = decision.get("instruction") or "只读复核 supervisor 已收集的证据，运行允许的验证命令并返回结果。"

                worker_system, worker_user_text = _worker_prompt(str(decision.get("instruction") or ""), goal, ledger, run_history)
                requested_worker_session = worker_session_id
                worker_result = runner(
                    worker_user_text,
                    cwd=worker_root,
                    allowed_tools=_allowed_tools(goal, "worker"),
                    max_budget_usd=max_budget_usd,
                    session_id=worker_session_id,
                    timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
                    disallowed_tools=_disallowed_tools(goal, "worker"),
                    permission_mode=_permission_mode(goal, "worker"),
                    role="worker",
                    extra_env=runner_env,
                    append_system_prompt=worker_system,
                    setting_sources="project,local",
                    strict_mcp_config=True,
                )
                worker_turns += 1
                agent_call_count += 1
                if worker_result.session_lost:
                    _record_event(events, events_path, {
                        "timestamp": now_utc(),
                        "type": "session_lost",
                        "role": "worker",
                        "turn_index": turn_index,
                        "requested_session_hash": stable_hash(requested_worker_session) if requested_worker_session else "",
                        "actual_session_hash": stable_hash(worker_result.session_id) if worker_result.session_id else "",
                    })
                    last_worker_result = worker_result
                    outcome = "needs_human"
                    stop_reason = "worker session was silently reset by Claude Code; run replan or replay before continuing"
                    human_gate_count += 1
                    _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                    break
                if worker_result.session_id:
                    worker_session_id = worker_result.session_id
                    session_state["worker_session_id"] = worker_session_id
                    session_state["last_run_id"] = run_id
                    _write_session_state(workspace, session_state)
                last_worker_result = worker_result
                worker_text = worker_result.output_text.strip()
                parsed_worker, worker_schema_errors = _parse_worker_result(worker_text)
                worker_evidence = _worker_evidence_text(parsed_worker, worker_text)
                evidence_parts.append(worker_evidence)
                worker_redacted, worker_flags = redact_text(worker_evidence, report)
                privacy_blocks.extend(_blocking_privacy(worker_flags))
                _record_event(events, events_path, {
                    "timestamp": now_utc(),
                    "type": "worker_result",
                    "turn_index": turn_index,
                    "session_hash": stable_hash(worker_session_id) if worker_session_id else "",
                    "returncode": worker_result.returncode,
                    "text_redacted": worker_redacted[:2000],
                    "privacy_flags": worker_flags,
                    "schema_name": WORKER_RESULT_SCHEMA,
                    "schema_valid": not worker_schema_errors,
                    "schema_errors": worker_schema_errors,
                })
                run_history.append({
                    "turn_index": turn_index,
                    "type": "worker_result",
                    "text_redacted": worker_redacted[:1000],
                    "returncode": worker_result.returncode,
                    "schema_errors": worker_schema_errors,
                })

                final_coverage = validation_coverage(goal, "\n".join(evidence_parts + [runtime_validation_evidence, json.dumps(ledger, ensure_ascii=False)]))
                if worker_schema_errors and worker_result.returncode == 0:
                    no_progress_count += 1
                    repair_result = runner(
                        _worker_repair_prompt(worker_text),
                        cwd=worker_root,
                        allowed_tools=[],
                        max_budget_usd=max_budget_usd,
                        session_id=worker_session_id,
                        timeout_seconds=_remaining_timeout(started_at, max_wall_seconds),
                        disallowed_tools=_disallowed_tools(goal, "worker"),
                        permission_mode=_permission_mode(goal, "worker"),
                        role="worker",
                        extra_env=runner_env,
                        append_system_prompt=_worker_system(goal),
                        setting_sources="project,local",
                        strict_mcp_config=True,
                    )
                    agent_call_count += 1
                    worker_turns += 1
                    if repair_result.session_id:
                        worker_session_id = repair_result.session_id
                        session_state["worker_session_id"] = worker_session_id
                        session_state["last_run_id"] = run_id
                        _write_session_state(workspace, session_state)
                    last_worker_result = repair_result
                    repair_text = repair_result.output_text.strip()
                    parsed_worker, worker_schema_errors = _parse_worker_result(repair_text)
                    worker_evidence = _worker_evidence_text(parsed_worker, repair_text)
                    evidence_parts.append(worker_evidence)
                    worker_redacted, worker_flags = redact_text(worker_evidence, report)
                    privacy_blocks.extend(_blocking_privacy(worker_flags))
                    _record_event(events, events_path, {
                        "timestamp": now_utc(),
                        "type": "worker_result_repair",
                        "turn_index": turn_index,
                        "session_hash": stable_hash(worker_session_id) if worker_session_id else "",
                        "returncode": repair_result.returncode,
                        "text_redacted": worker_redacted[:2000],
                        "privacy_flags": worker_flags,
                        "schema_name": WORKER_RESULT_SCHEMA,
                        "schema_valid": not worker_schema_errors,
                        "schema_errors": worker_schema_errors,
                    })
                    run_history.append({
                        "turn_index": turn_index,
                        "type": "worker_result_repair",
                        "text_redacted": worker_redacted[:1000],
                        "returncode": repair_result.returncode,
                        "schema_errors": worker_schema_errors,
                    })
                if worker_schema_errors:
                    outcome = "agent_failed"
                    stop_reason = _schema_error_text(WORKER_RESULT_SCHEMA, worker_schema_errors)
                    if worker_result.returncode == 0 and not worker_text:
                        stop_reason += "; worker returned empty output with returncode 0, likely stale/resumed session"
                    _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=last_worker_result, coverage=final_coverage, stop_reason=stop_reason)
                    break
                if parsed_worker and parsed_worker.get("needs_human"):
                    stop_reason = "; ".join(str(item) for item in parsed_worker.get("blockers", []) if str(item)) or "worker requested human review"
                    if not parsed_worker.get("blockers") and _completion_handoff_ready(ledger):
                        outcome = "completed"
                        stop_reason = f"all features completed; {stop_reason}"
                    else:
                        human_gate_count += 1
                        outcome = "needs_human"
                    _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                    break
                if privacy_blocks:
                    outcome = "privacy_blocked"
                    stop_reason = "privacy markers require human review"
                    _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                    break
                if worker_result.returncode:
                    outcome = _failure_outcome(worker_result)
                    stop_reason = _failure_reason(worker_result, "worker")
                    _append_progress(workspace, run_id=run_id, turn_index=turn_index, decision=decision, worker_result=worker_result, coverage=final_coverage, stop_reason=stop_reason)
                    break
                ledger_changed = _complete_focus_from_worker(ledger, decision, parsed_worker, report) or ledger_changed
                write_json(workspace / "feature_ledger.json", ledger)
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
    final_evidence_text = "\n".join(evidence_parts + [runtime_validation_evidence, json.dumps(ledger, ensure_ascii=False, sort_keys=True)])
    if not final_validation_statuses:
        final_validation_statuses = validation_command_status(goal, final_evidence_text)
    final_coverage = validation_coverage(goal, final_evidence_text)
    counts = _feature_status_counts(ledger)
    hook_events_path = run_dir / "hook_events.jsonl"
    hook_event_counts = _count_hook_events(hook_events_path)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "goal_ref": "goal.yaml",
        "persona_ref": "persona.lock.json",
        "ledger_ref": "feature_ledger.json",
        "events_ref": _events_ref(events_path, run_path),
        "session_state_ref": SESSION_STATE_FILE,
        "supervisor_session_id": stable_hash(supervisor_session_id) if supervisor_session_id else "",
        "worker_session_id": stable_hash(worker_session_id) if worker_session_id else "",
        "headless": {
            "resume_used": bool(session_state.get("resume_used")),
            "worker_cwd": str(worker_root),
            "worker_branch": worker_branch,
            "worker_isolation": worker_isolation,
            "hook_settings_ref": str(session_state.get("hook_settings_ref") or ""),
            "project_hook_settings": str(session_state.get("project_hook_settings") or ""),
            "worker_claude_md_ref": str(session_state.get("worker_claude_md_ref") or ""),
            "hook_events_ref": _events_ref(hook_events_path, run_path) if hook_events_path.exists() else "",
            "supervisor_session_hash": stable_hash(supervisor_session_id) if supervisor_session_id else "",
            "worker_session_hash": stable_hash(worker_session_id) if worker_session_id else "",
        },
        "outcome": outcome,
        "stop_reason": stop_reason,
        "human_review": _human_review_summary(
            outcome=outcome,
            stop_reason=stop_reason,
            ledger=ledger,
            risk_markers=risk_markers,
            privacy_blocks=privacy_blocks,
            report=report,
        ),
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
            "tool_call_events": hook_event_counts.get("post_tool_use", 0),
            "session_start_events": hook_event_counts.get("session_start", 0),
            "compaction_events": hook_event_counts.get("pre_compact", 0),
        },
        "privacy_report": report.as_dict(),
        "validation_report": {
            "risk_markers": sorted(set(risk_markers)),
            "mode": mode,
            "privacy_blocks": sorted(set(privacy_blocks)),
            "validation_commands": final_validation_statuses,
        },
    }
    write_json(run_path, run)
    next_action = ""
    current_status = outcome
    if outcome == "needs_human":
        next_action = f"{CLI_MODULE_CMD} respond --workspace <workspace> --action <approve_and_continue|request_plan_delta|defer_feature|stop_session>"
    elif outcome in {"failed_validation", "no_progress", "privacy_blocked", "agent_failed"}:
        next_action = "inspect run artifact and decide whether to respond, replan, or fix environment"
    elif outcome == "completed":
        current_status = "completed_waiting_handoff"
        next_action = "review worker diff and validation evidence; ship if acceptable"
    _write_current(workspace, status=current_status, goal=goal, ledger=ledger, run=run, next_action=next_action, validation_statuses=final_validation_statuses, worker_cwd=str(worker_root))
    return run
