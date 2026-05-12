from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import CURRENT_FILE, HUMAN_RESPONSE_FILE, RUN_SCHEMA, SUPERVISOR_REVIEW_SCHEMA, SUPERVISOR_STATE_FILE
from .ledger import acceptance_evidence, acceptance_focus, apply_ledger_updates, choose_next_item, ledger_gaps
from .schema_contract import validate_schema
from .util import now_utc, read_json, write_json
from .workspace import (
    WorkspaceError,
    load_goal,
    load_human_response,
    load_ledger,
    load_state,
    load_supervisor_persona,
    render_current,
    validate_workspace,
    write_ledger,
    write_state,
)


def _review_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "supervisor_review.json"


def _run_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "run.json"


def _validate_accepted_done(goal: dict[str, Any], ledger: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if review.get("remaining_gaps"):
        errors.append("ACCEPTED_DONE cannot have remaining_gaps")
    evidence = {item["ac_id"]: list(item.get("evidence") or []) for item in acceptance_evidence(goal, ledger)}
    for ac in goal.get("acceptance_criteria", []):
        ac_id = str(ac.get("id")) if isinstance(ac, dict) else ""
        if ac_id and not evidence.get(ac_id):
            errors.append(f"missing ledger evidence for {ac_id}")
    open_items = [str(item.get("id")) for item in ledger.get("items", []) if isinstance(item, dict) and item.get("status") != "completed"]
    if open_items:
        errors.append("ledger has open items: " + ", ".join(open_items))
    return errors


def _validate_review_actions(review: dict[str, Any]) -> None:
    actions = set(review.get("actions") or [])
    if "mark_ledger_gap" in actions and not review.get("ledger_updates"):
        raise WorkspaceError("mark_ledger_gap requires ledger_updates")


def build_supervisor_context(workspace: Path, run_id: str | None = None) -> dict[str, Any]:
    goal, ledger = validate_workspace(workspace)
    state = load_state(workspace)
    next_item = choose_next_item(ledger)
    focus = acceptance_focus(goal, ledger)
    context: dict[str, Any] = {
        "workspace": str(workspace),
        "goal": goal,
        "ledger": ledger,
        "supervisor_persona": load_supervisor_persona(),
        "state": state,
        "next_item": next_item,
        "remaining_gaps": ledger_gaps(goal, ledger),
        "acceptance_evidence": acceptance_evidence(goal, ledger),
        "acceptance_focus": focus,
        "artifact_paths": {
            "current": str(workspace / CURRENT_FILE),
            "state": str(workspace / SUPERVISOR_STATE_FILE),
            "human_response": str(workspace / HUMAN_RESPONSE_FILE),
        },
        "review_skeleton": {
            "decision": "<ACCEPTED_DONE|CONTINUE|NEEDS_HUMAN|FAILED>",
            "summary": "",
            "next_instruction": "",
            "remaining_gaps": [],
            "acceptance_evidence": [],
            "risk_flags": [],
            "actions": [],
            "ledger_updates": [],
            "human_question": None,
        },
    }
    human_response = load_human_response(workspace)
    if human_response:
        context["human_response"] = human_response
    if run_id:
        run_path = _run_path(workspace, run_id)
        if not run_path.exists():
            raise WorkspaceError(f"missing run artifact: {run_path}")
        context["run"] = read_json(run_path)
        context["artifact_paths"]["run"] = str(run_path)
        context["artifact_paths"]["review"] = str(_review_path(workspace, run_id))
    return context


def build_review_context(workspace: Path, run_id: str) -> dict[str, Any]:
    return build_supervisor_context(workspace, run_id)


def apply_supervisor_review(workspace: Path, run_id: str, review: dict[str, Any]) -> dict[str, Any]:
    errors = validate_schema(review, SUPERVISOR_REVIEW_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_review schema errors: " + "; ".join(errors))
    _validate_review_actions(review)

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

    decision = str(review.get("decision"))
    if decision == "ACCEPTED_DONE":
        done_errors = _validate_accepted_done(goal, ledger, review)
        if done_errors:
            raise WorkspaceError("ACCEPTED_DONE errors: " + "; ".join(done_errors))
        state["status"] = "accepted_done"
        state["next_instruction"] = ""
        state["needs_human"] = None
        run["outcome"] = "accepted_done"
    elif decision == "CONTINUE":
        instruction = str(review.get("next_instruction") or "").strip()
        if not instruction:
            raise WorkspaceError("CONTINUE requires supervisor-authored next_instruction")
        state["status"] = "continue"
        state["next_instruction"] = instruction
        state["needs_human"] = None
        run["outcome"] = "continued"
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
    next_item = choose_next_item(ledger)
    state["current_item_id"] = next_item.get("id") if next_item else None
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
