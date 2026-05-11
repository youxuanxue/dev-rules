from __future__ import annotations

from pathlib import Path
from typing import Any

from .supervisor_review import apply_supervisor_review, build_health_report, build_review_context, build_supervisor_context
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


def health_workspace(workspace: Path | str, *, run_id: str | None = None, events_tail: int = 20, history_limit: int = 20) -> dict[str, Any]:
    return build_health_report(
        Path(workspace).expanduser().resolve(),
        run_id=run_id,
        events_tail=events_tail,
        history_limit=history_limit,
    )


def record_human_response(workspace: Path | str, text: str) -> Path:
    workspace_path = Path(workspace).expanduser().resolve()
    validate_workspace(workspace_path)
    target = write_human_response(workspace_path, text)
    goal = load_goal(workspace_path)
    ledger = load_ledger(workspace_path)
    state = load_state(workspace_path)
    state["status"] = "continue"
    state["needs_human"] = None
    state["next_instruction"] = ""
    write_state(workspace_path, state)
    render_current(workspace_path, goal, ledger, state)
    return target


__all__ = [
    "WorkspaceError",
    "apply_supervisor_review",
    "build_review_context",
    "build_supervisor_context",
    "health_workspace",
    "record_human_response",
    "start_worker_turn",
    "status_workspace",
    "validate_workspace",
]
