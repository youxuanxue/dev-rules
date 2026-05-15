from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .supervisor_review import apply_supervisor_review, build_review_context
from .worker import Runner, start_worker_turn
from .workspace import WorkspaceError, load_state, worker_running_diagnostics

TERMINAL_STATUSES = {"accepted_done", "needs_human", "failed"}

ReviewFn = Callable[[dict[str, Any]], dict[str, Any]]


def run_supervisor_loop_harness(
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
    initial_instruction = instruction.strip()

    runs: list[str] = []
    for _round in range(max_rounds):
        state = load_state(workspace)
        status = str(state.get("status") or "")
        if status in TERMINAL_STATUSES:
            return {"status": status, "runs": runs, "state": state}
        if status == "worker_running":
            return {"status": status, "runs": runs, "state": state, "worker": worker_running_diagnostics(workspace, state)}
        if status == "review_required":
            run_id = str(state.get("current_run_id") or "")
            if not run_id:
                raise WorkspaceError("review_required state missing current_run_id")
            context = build_review_context(workspace, run_id)
            review = review_fn(context)
            state = apply_supervisor_review(workspace, run_id, review)
            status = str(state.get("status") or "")
            if status in TERMINAL_STATUSES:
                return {"status": status, "runs": runs, "state": state}
            if status != "continue":
                raise WorkspaceError(f"unexpected loop state: {status}")
        state = load_state(workspace)
        status = str(state.get("status") or "")
        if status == "idle":
            current_instruction = initial_instruction
        elif status == "continue":
            current_instruction = str(state.get("next_instruction") or "").strip()
        else:
            raise WorkspaceError(f"unexpected loop state: {status}")
        if not current_instruction:
            raise WorkspaceError("supervisor loop requires an instruction")
        run = start_worker_turn(workspace, current_instruction, runner=runner)
        runs.append(str(run["run_id"]))
    raise WorkspaceError(f"supervisor loop exceeded max_rounds={max_rounds}")
