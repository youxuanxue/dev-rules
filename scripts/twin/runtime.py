from __future__ import annotations

from pathlib import Path
from typing import Any

from .supervisor_review import apply_supervisor_review, build_review_context, build_supervisor_context
from .worker import start_worker_turn
from .contracts import HUMAN_RESPONSE_FILE
from .workspace import (
    WorkspaceError,
    append_workspace_event,
    load_state,
    render_current,
    status_summary,
    validate_workspace,
    worker_running_diagnostics,
    write_human_response,
    write_state,
)


def status_workspace(workspace: Path | str) -> dict[str, Any]:
    return status_summary(Path(workspace).expanduser().resolve())


def continuation_action(workspace: Path | str) -> dict[str, Any]:
    workspace_path = Path(workspace).expanduser().resolve()
    validate_workspace(workspace_path)
    state = load_state(workspace_path)
    status = str(state.get("status") or "")
    base = {
        "workspace": str(workspace_path),
        "status": status,
        "current_run_id": state.get("current_run_id"),
        "next_instruction": state.get("next_instruction") or "",
    }
    if status == "idle":
        return {
            **base,
            "action": "supervisor_instruction",
            "command": f"python3 -m scripts.twin supervisor-context --workspace {workspace_path}",
            "next": f"/twin {workspace_path}",
        }
    if status == "continue":
        if not base["next_instruction"]:
            return {
                **base,
                "action": "supervisor_instruction",
                "command": f"python3 -m scripts.twin supervisor-context --workspace {workspace_path}",
                "next": f"/twin {workspace_path}",
            }
        return {
            **base,
            "action": "worker_turn",
            "command": f"python3 -m scripts.twin worker-turn --workspace {workspace_path} --instruction <next_instruction>",
            "next": f"/twin {workspace_path}",
        }
    if status == "worker_running":
        worker = worker_running_diagnostics(workspace_path, state) or {}
        worker_action = str(worker.get("recommended_action") or "watch_worker")
        if worker_action == "review_run":
            run_id = str(state.get("current_run_id") or "")
            return {
                **base,
                "action": "review_run",
                "worker": worker,
                "command": f"python3 -m scripts.twin review-context --workspace {workspace_path} --run-id {run_id} --json",
                "next": f"/twin {workspace_path}",
            }
        if worker_action == "recover_worker_turn":
            return {
                **base,
                "action": "recover_worker_turn",
                "worker": worker,
                "command": f"python3 -m scripts.twin worker-turn --workspace {workspace_path} --instruction <next_instruction>",
                "next": f"/twin {workspace_path}",
            }
        return {
            **base,
            "action": "watch_worker",
            "worker": worker,
            "command": f"python3 -m scripts.twin watch --workspace {workspace_path} --json",
            "next": f"/twin status {workspace_path}",
        }
    if status == "review_required":
        run_id = str(state.get("current_run_id") or "")
        return {
            **base,
            "action": "review_run",
            "command": f"python3 -m scripts.twin review-context --workspace {workspace_path} --run-id {run_id} --json",
            "next": f"/twin {workspace_path}",
        }
    if status == "needs_human":
        return {**base, "action": "ask_human", "needs_human": state.get("needs_human"), "next": "/twin respond <answer>"}
    if status == "accepted_done":
        return {**base, "action": "done", "next": "none"}
    if status == "failed":
        return {**base, "action": "failed", "next": "inspect CURRENT.md and latest run evidence"}
    return {**base, "action": "unknown", "next": f"/twin status {workspace_path}"}


def record_human_response(workspace: Path | str, text: str) -> Path:
    workspace_path = Path(workspace).expanduser().resolve()
    goal, plan = validate_workspace(workspace_path)
    state = load_state(workspace_path)
    if state.get("status") != "needs_human" or not isinstance(state.get("needs_human"), dict):
        raise WorkspaceError(f"workspace is not waiting for human response: {state.get('status')}")
    current_run_id = state.get("current_run_id")
    target = write_human_response(workspace_path, text)
    state["status"] = "continue"
    state["needs_human"] = None
    state["next_instruction"] = ""
    write_state(workspace_path, state)
    render_current(workspace_path, goal, plan, state)
    append_workspace_event(
        workspace_path,
        {
            "event": "human_response_recorded",
            "previous_status": "needs_human",
            "new_status": "continue",
            "current_run_id": current_run_id,
            "next_instruction_present": False,
            "artifact_ref": HUMAN_RESPONSE_FILE,
            "response_chars": len(text.strip()),
        },
    )
    return target


__all__ = [
    "WorkspaceError",
    "apply_supervisor_review",
    "build_review_context",
    "build_supervisor_context",
    "continuation_action",
    "record_human_response",
    "start_worker_turn",
    "status_workspace",
    "validate_workspace",
]
