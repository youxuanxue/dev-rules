from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import CURRENT_FILE, HUMAN_RESPONSE_FILE, RUN_SCHEMA, SUPERVISOR_REVIEW_SCHEMA, SUPERVISOR_STATE_FILE
from .plan import acceptance_evidence, acceptance_focus, apply_plan_updates, choose_next_item, plan_gaps
from .schema_contract import validate_schema
from .util import now_utc, read_json, write_json
from .worker import _repo_root
from .worktree import remove_worktree
from .workspace import (
    WorkspaceError,
    load_goal,
    load_human_response,
    load_plan,
    load_state,
    load_supervisor_persona,
    render_current,
    validate_workspace,
    write_plan,
    write_state,
)


def _run_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "run.json"


def _validate_accepted_done(goal: dict[str, Any], plan: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if review.get("remaining_gaps"):
        errors.append("accepted_done cannot have remaining_gaps")
    evidence = {item["ac_id"]: list(item.get("evidence") or []) for item in acceptance_evidence(goal, plan)}
    for ac in goal.get("acceptance_criteria", []):
        ac_id = str(ac.get("id")) if isinstance(ac, dict) else ""
        if ac_id and not evidence.get(ac_id):
            errors.append(f"missing plan evidence for {ac_id}")
    open_items = [str(item.get("id")) for item in plan.get("items", []) if isinstance(item, dict) and item.get("status") != "completed"]
    if open_items:
        errors.append("plan has open items: " + ", ".join(open_items))
    return errors


def _validate_review_actions(review: dict[str, Any]) -> None:
    actions = set(review.get("actions") or [])
    if "mark_plan_gap" in actions and not review.get("plan_updates"):
        raise WorkspaceError("mark_plan_gap requires plan_updates")


def build_supervisor_context(workspace: Path, run_id: str | None = None) -> dict[str, Any]:
    goal, plan = validate_workspace(workspace)
    state = load_state(workspace)
    next_item = choose_next_item(plan)
    focus = acceptance_focus(goal, plan)
    context: dict[str, Any] = {
        "workspace": str(workspace),
        "goal": goal,
        "plan": plan,
        "supervisor_persona": load_supervisor_persona(),
        "state": state,
        "next_item": next_item,
        "remaining_gaps": plan_gaps(goal, plan),
        "acceptance_evidence": acceptance_evidence(goal, plan),
        "acceptance_focus": focus,
        "artifact_paths": {
            "current": str(workspace / CURRENT_FILE),
            "state": str(workspace / SUPERVISOR_STATE_FILE),
            "human_response": str(workspace / HUMAN_RESPONSE_FILE),
        },
        "review_skeleton": {
            "status": "<accepted_done|continue|needs_human|failed>",
            "summary": "",
            "next_instruction": "",
            "remaining_gaps": [],
            "acceptance_evidence": [],
            "risk_flags": [],
            "actions": [],
            "plan_updates": [],
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
        context["artifact_paths"]["review"] = str(run_path) + "::review"
    return context


def build_review_context(workspace: Path, run_id: str) -> dict[str, Any]:
    return build_supervisor_context(workspace, run_id)


def apply_supervisor_review(workspace: Path, run_id: str, review: dict[str, Any]) -> dict[str, Any]:
    errors = validate_schema(review, SUPERVISOR_REVIEW_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_review schema errors: " + "; ".join(errors))
    _validate_review_actions(review)

    goal = load_goal(workspace)
    plan = load_plan(workspace)
    state = load_state(workspace)
    run_path = _run_path(workspace, run_id)
    if not run_path.exists():
        raise WorkspaceError(f"missing run artifact: {run_path}")
    run = read_json(run_path)

    update_errors = apply_plan_updates(plan, list(review.get("plan_updates") or []))
    if update_errors:
        raise WorkspaceError("plan update errors: " + "; ".join(update_errors))

    status = str(review.get("status"))
    if status == "accepted_done":
        done_errors = _validate_accepted_done(goal, plan, review)
        if done_errors:
            raise WorkspaceError("accepted_done errors: " + "; ".join(done_errors))
        state["status"] = "accepted_done"
        state["next_instruction"] = ""
        state["needs_human"] = None
        run["status"] = "accepted_done"
    elif status == "continue":
        instruction = str(review.get("next_instruction") or "").strip()
        if not instruction:
            raise WorkspaceError("continue requires supervisor-authored next_instruction")
        state["status"] = "continue"
        state["next_instruction"] = instruction
        state["needs_human"] = None
        run["status"] = "continue"
    elif status == "needs_human":
        question = str(review.get("human_question") or "").strip()
        if not question:
            raise WorkspaceError("needs_human requires human_question")
        state["status"] = "needs_human"
        state["next_instruction"] = ""
        state["needs_human"] = {
            "question": question,
            "context": str(review.get("summary") or ""),
            "created_at": now_utc(),
        }
        run["status"] = "needs_human"
    elif status == "failed":
        state["status"] = "failed"
        state["next_instruction"] = ""
        state["needs_human"] = None
        run["status"] = "failed"
    else:
        raise WorkspaceError(f"unknown review status: {status}")

    # Terminal status → tear down this workspace's isolated worker worktree
    # (best-effort; no-op when isolation was disabled or never created one).
    if status in {"accepted_done", "failed"}:
        try:
            remove_worktree(_repo_root(workspace), workspace)
        except Exception:  # cleanup must never break review application
            pass

    state["last_review_status"] = status
    state["current_run_id"] = run_id
    next_item = choose_next_item(plan)
    state["current_item_id"] = next_item.get("id") if next_item else None
    run["review"] = review
    run_errors = validate_schema(run, RUN_SCHEMA)
    if run_errors:
        raise WorkspaceError("run schema errors after review: " + "; ".join(run_errors))
    write_json(run_path, run)
    write_plan(workspace, plan)
    write_state(workspace, state)
    render_current(workspace, goal, plan, state)
    return state
