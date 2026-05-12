from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .supervisor_review import build_review_context
from .worker import Runner, start_worker_turn
from .workspace import WorkspaceError, load_state

TERMINAL_STATUSES = {"accepted_done", "needs_human", "failed"}

ReviewFn = Callable[[dict[str, Any]], dict[str, Any]]


def run_supervisor_loop(
    workspace: Path,
    *,
    instruction: str,
    review_fn: ReviewFn,
    runner: Runner,
    max_rounds: int = 10,
) -> dict[str, Any]:
    if max_rounds <= 0:
        raise WorkspaceError("max_rounds must be greater than 0")
    workspace = workspace.expanduser().resolve()
    current_instruction = instruction.strip()
    if not current_instruction:
        state = load_state(workspace)
        current_instruction = str(state.get("next_instruction") or "").strip()
    if not current_instruction:
        raise WorkspaceError("supervisor loop requires an initial instruction")

    runs: list[str] = []
    for _round in range(max_rounds):
        run = start_worker_turn(workspace, current_instruction, runner=runner)
        runs.append(str(run["run_id"]))
        context = build_review_context(workspace, str(run["run_id"]))
        review = review_fn(context)
        from .supervisor_review import apply_supervisor_review

        state = apply_supervisor_review(workspace, str(run["run_id"]), review)
        status = str(state.get("status") or "")
        if status in TERMINAL_STATUSES:
            return {"status": status, "runs": runs, "state": state}
        if status != "continue":
            raise WorkspaceError(f"unexpected loop state: {status}")
        current_instruction = str(state.get("next_instruction") or "").strip()
        if not current_instruction:
            raise WorkspaceError("continue state missing next_instruction")
    raise WorkspaceError(f"supervisor loop exceeded max_rounds={max_rounds}")
