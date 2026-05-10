from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .contracts import RUN_SCHEMA, SUPERVISOR_REVIEW_SCHEMA
from .ledger import acceptance_evidence, apply_ledger_updates, choose_next_item, ledger_gaps
from .schema_contract import validate_schema
from .util import now_utc, read_json, write_json
from .contracts import CURRENT_FILE, HUMAN_RESPONSE_FILE, SUPERVISOR_STATE_FILE
from .workspace import (
    WorkspaceError,
    load_goal,
    load_human_response,
    load_ledger,
    load_state,
    read_text_file,
    render_current,
    write_ledger,
    write_state,
)


def _review_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "supervisor_review.json"


def _run_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "run.json"


def _gap_key(review: dict[str, Any]) -> str:
    parts = [str(item).strip() for item in review.get("remaining_gaps", []) if str(item).strip()]
    if not parts:
        parts = [str(item).strip() for item in review.get("risk_flags", []) if str(item).strip()]
    if not parts:
        return ""
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def _reset_other_failure_streaks(state: dict[str, Any], key: str) -> None:
    streaks = state.get("failure_streaks")
    if not isinstance(streaks, dict):
        state["failure_streaks"] = {}
        return
    for existing in list(streaks):
        if existing != key:
            streaks[existing] = 0


def _validate_accepted_done(goal: dict[str, Any], ledger: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if review.get("remaining_gaps"):
        errors.append("ACCEPTED_DONE cannot have remaining_gaps")
    evidence = {item["ac_id"]: list(item.get("evidence") or []) for item in acceptance_evidence(goal, ledger)}
    for item in review.get("acceptance_evidence", []):
        if isinstance(item, dict):
            ac_id = str(item.get("ac_id") or "")
            evidence.setdefault(ac_id, [])
            for entry in item.get("evidence", []):
                if entry not in evidence[ac_id]:
                    evidence[ac_id].append(str(entry))
    for ac in goal.get("acceptance_criteria", []):
        ac_id = str(ac.get("id")) if isinstance(ac, dict) else ""
        if ac_id and not evidence.get(ac_id):
            errors.append(f"missing evidence for {ac_id}")
    open_items = [str(item.get("id")) for item in ledger.get("items", []) if isinstance(item, dict) and item.get("status") != "completed"]
    if open_items:
        errors.append("ledger has open items: " + ", ".join(open_items))
    return errors


def build_next_instruction(workspace: Path) -> str:
    goal = load_goal(workspace)
    ledger = load_ledger(workspace)
    state = load_state(workspace)
    if state.get("status") == "needs_human" and state.get("needs_human"):
        raise WorkspaceError("workspace is waiting for human response")
    if state.get("status") in {"accepted_done", "failed"}:
        raise WorkspaceError(f"workspace is terminal: {state.get('status')}")
    existing = str(state.get("next_instruction") or "").strip()
    if existing:
        return existing

    item = choose_next_item(ledger)
    gaps = ledger_gaps(goal, ledger)
    if item is None:
        return "对照 goal.yaml 与 feature_ledger 做最终验收；如证据不足，补齐测试、preflight、diff/PR 证据后更新 ledger。"

    covered = ", ".join(str(ac_id) for ac_id in item.get("covers_ac", []))
    evidence_plan = "; ".join(str(entry) for entry in item.get("evidence_plan", [])) or "按 goal/ledger 补齐可验收证据"
    gap_text = "\n".join(f"- {gap}" for gap in gaps[:10]) or "- 无已知 gap；做最终确认。"
    return "\n".join([
        f"推进 ledger item {item.get('id')}: {item.get('deliverable')}",
        f"Scope: {item.get('scope')}",
        f"Covers AC: {covered}",
        f"Expected evidence: {evidence_plan}",
        f"Next action: {item.get('next_action') or '完成该 deliverable 并更新 feature_ledger actual_evidence/status'}",
        "Current ledger gaps:",
        gap_text,
        "完成后必须留下 tests/preflight/diff/PR 等证据；不要把 worker 自述当最终验收。",
    ]).strip()


def build_review_context(workspace: Path, run_id: str) -> dict[str, Any]:
    goal = load_goal(workspace)
    ledger = load_ledger(workspace)
    state = load_state(workspace)
    run_path = _run_path(workspace, run_id)
    if not run_path.exists():
        raise WorkspaceError(f"missing run artifact: {run_path}")
    context = {
        "workspace": str(workspace),
        "goal": goal,
        "ledger": ledger,
        "supervisor_persona": read_text_file(workspace, "supervisor-persona.md"),
        "state": state,
        "run": read_json(run_path),
        "remaining_gaps": ledger_gaps(goal, ledger),
        "acceptance_evidence": acceptance_evidence(goal, ledger),
        "artifact_paths": {
            "current": str(workspace / CURRENT_FILE),
            "state": str(workspace / SUPERVISOR_STATE_FILE),
            "run": str(run_path),
            "review": str(_review_path(workspace, run_id)),
            "human_response": str(workspace / HUMAN_RESPONSE_FILE),
        },
    }
    human_response = load_human_response(workspace)
    if human_response:
        context["human_response"] = human_response
    return context


def review_template(context: dict[str, Any]) -> dict[str, Any]:
    next_instruction = str(context.get("state", {}).get("next_instruction") or "")
    gaps = [str(item) for item in context.get("remaining_gaps", [])]
    decision = "ACCEPTED_DONE" if not gaps else "CONTINUE"
    if decision == "CONTINUE" and not next_instruction:
        next_instruction = "补齐 remaining_gaps 中列出的验收证据，并更新 feature_ledger。"
    return {
        "decision": decision,
        "summary": "",
        "next_instruction": "" if decision == "ACCEPTED_DONE" else next_instruction,
        "remaining_gaps": gaps,
        "acceptance_evidence": context.get("acceptance_evidence", []),
        "risk_flags": [],
        "actions": [],
        "ledger_updates": [],
        "human_question": None,
    }


def _apply_review_actions(review: dict[str, Any]) -> None:
    actions = set(review.get("actions") or [])
    if "mark_ledger_gap" in actions and not review.get("ledger_updates"):
        raise WorkspaceError("mark_ledger_gap requires ledger_updates")
    if review.get("decision") != "CONTINUE":
        return
    instruction = str(review.get("next_instruction") or "").strip()
    prefixes: list[str] = []
    if "fix_drift" in actions:
        prefixes.append("先纠偏：回到 goal.yaml、feature_ledger 和 non-goals，撤回或修正偏离目标的改动。")
    if "validate_more" in actions:
        prefixes.append("先补验证：运行相关测试/preflight，并把证据写入 feature_ledger actual_evidence。")
    if prefixes:
        review["next_instruction"] = "\n".join(prefixes + ([instruction] if instruction else []))


def apply_supervisor_review(workspace: Path, run_id: str, review: dict[str, Any]) -> dict[str, Any]:
    errors = validate_schema(review, SUPERVISOR_REVIEW_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_review schema errors: " + "; ".join(errors))
    _apply_review_actions(review)

    goal = load_goal(workspace)
    ledger = load_ledger(workspace)
    state = load_state(workspace)
    run_path = _run_path(workspace, run_id)
    if not run_path.exists():
        raise WorkspaceError(f"missing run artifact: {run_path}")
    run = read_json(run_path)

    update_errors = apply_ledger_updates(ledger, list(review.get("ledger_updates") or []))
    if update_errors:
        raise WorkspaceError("ledger update errors: " + "; ".join(update_errors))

    key = _gap_key(review)
    if key:
        streaks = state.setdefault("failure_streaks", {})
        streaks[key] = int(streaks.get(key) or 0) + 1
        _reset_other_failure_streaks(state, key)
    else:
        state["failure_streaks"] = {}
    repeated_failure = bool(key and int(state.get("failure_streaks", {}).get(key) or 0) >= 3)

    decision = str(review.get("decision"))
    if decision == "ACCEPTED_DONE":
        done_errors = _validate_accepted_done(goal, ledger, review)
        if done_errors:
            raise WorkspaceError("ACCEPTED_DONE errors: " + "; ".join(done_errors))
        state["status"] = "accepted_done"
        state["next_instruction"] = ""
        state["needs_human"] = None
        state["failure_streaks"] = {}
        run["outcome"] = "accepted_done"
    elif decision == "CONTINUE" and repeated_failure:
        state["status"] = "needs_human"
        state["next_instruction"] = ""
        state["needs_human"] = {
            "question": "同一问题已经连续 3 轮未推进；是否调整目标、缩小 scope，还是继续让 worker 按当前方向修复？",
            "context": str(review.get("summary") or "\n".join(review.get("remaining_gaps", []))),
            "created_at": now_utc(),
        }
        run["outcome"] = "needs_human"
    elif decision == "CONTINUE":
        instruction = str(review.get("next_instruction") or "").strip()
        if not instruction:
            raise WorkspaceError("CONTINUE requires next_instruction")
        streak = int(state.get("failure_streaks", {}).get(key) or 0) if key else 0
        if streak >= 2 and "改变策略" not in instruction:
            instruction = "改变策略：不要重复上一轮做法；先定位最小未满足证据或失败根因，再用不同路径补齐。\n" + instruction
        state["status"] = "continue"
        state["next_instruction"] = instruction
        state["needs_human"] = None
        run["outcome"] = "review_required"
    elif decision == "NEEDS_HUMAN":
        question = str(review.get("human_question") or "").strip()
        if not question:
            raise WorkspaceError("NEEDS_HUMAN requires human_question")
        state["status"] = "needs_human"
        state["next_instruction"] = ""
        state["needs_human"] = {
            "question": question,
            "context": str(review.get("summary") or ""),
            "created_at": now_utc(),
        }
        run["outcome"] = "needs_human"
    elif decision == "FAILED":
        state["status"] = "failed"
        state["next_instruction"] = ""
        state["needs_human"] = None
        run["outcome"] = "failed"
    else:
        raise WorkspaceError(f"unknown decision: {decision}")

    state["last_decision"] = decision
    state["current_run_id"] = run_id
    if review.get("ledger_updates"):
        first = review["ledger_updates"][0]
        if isinstance(first, dict):
            state["current_item_id"] = first.get("item_id")
    elif choose_next_item(ledger):
        state["current_item_id"] = choose_next_item(ledger).get("id")
    else:
        state["current_item_id"] = None
    review_path = _review_path(workspace, run_id)
    write_json(review_path, review)
    run["review_ref"] = str(review_path)
    run_errors = validate_schema(run, RUN_SCHEMA)
    if run_errors:
        raise WorkspaceError("run schema errors after review: " + "; ".join(run_errors))
    write_json(run_path, run)
    write_ledger(workspace, ledger)
    write_state(workspace, state)
    render_current(workspace, goal, ledger, state)
    return state
