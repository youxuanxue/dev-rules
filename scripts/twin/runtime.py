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
    write_human_response,
    write_state,
)


def status_workspace(workspace: Path | str) -> dict[str, Any]:
    return status_summary(Path(workspace).expanduser().resolve())


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
    "record_human_response",
    "start_worker_turn",
    "status_workspace",
    "validate_workspace",
]
