from __future__ import annotations

from pathlib import Path
from typing import Any

from .supervisor_review import apply_supervisor_review, build_next_instruction, build_review_context, review_template
from .worker import start_worker_turn
from .workspace import (
    WorkspaceError,
    load_goal,
    load_ledger,
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
    validate_workspace(workspace_path)
    target = write_human_response(workspace_path, text)
    goal = load_goal(workspace_path)
    ledger = load_ledger(workspace_path)
    state = load_state(workspace_path)
    state["status"] = "continue"
    state["needs_human"] = None
    state["next_instruction"] = "Human response received; read human_response.json, update the ledger if it changes direction, then continue toward goal.yaml acceptance criteria."
    write_state(workspace_path, state)
    render_current(workspace_path, goal, ledger, state)
    return target


__all__ = [
    "WorkspaceError",
    "apply_supervisor_review",
    "build_next_instruction",
    "build_review_context",
    "record_human_response",
    "review_template",
    "start_worker_turn",
    "status_workspace",
    "validate_workspace",
]
