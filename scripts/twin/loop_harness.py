from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .supervisor_review import apply_supervisor_review, build_review_context
from .worker import Runner, start_worker_turn
from .workspace import WorkspaceError, load_state, worker_running_diagnostics

TERMINAL_STATUSES = {"accepted_done", "needs_human", "failed"}

ReviewFn = Callable[[dict[str, Any]], dict[str, Any]]
SleepFn = Callable[[float], None]


def run_supervisor_loop_harness(
    workspace: Path,
    *,
    instruction: str,
    review_fn: ReviewFn,
    runner: Runner,
    max_rounds: int = 10,
    max_wait_seconds: float = 0.0,
    poll_interval_seconds: float = 10.0,
    sleep_fn: SleepFn = time.sleep,
) -> dict[str, Any]:
    if max_rounds <= 0:
        raise WorkspaceError("max_rounds must be greater than 0")
    workspace = workspace.expanduser().resolve()
    initial_instruction = instruction.strip()

    runs: list[str] = []
    wait_deadline = time.monotonic() + max(0.0, max_wait_seconds)
    for _round in range(max_rounds):
        state = load_state(workspace)
        status = str(state.get("status") or "")
        if status in TERMINAL_STATUSES:
            return {"status": status, "runs": runs, "state": state}
        while status == "worker_running":
            worker = worker_running_diagnostics(workspace, state) or {}
            worker_action = str(worker.get("recommended_action") or "watch_worker")
            if worker_action == "review_run":
                state["status"] = "review_required"
                status = "review_required"
                break
            if worker_action == "recover_worker_turn":
                state["status"] = "continue"
                status = "continue"
                break
            remaining = wait_deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "worker_quiet_timeout", "runs": runs, "state": state, "worker": worker}
            sleep_fn(min(max(0.0, poll_interval_seconds), remaining))
            state = load_state(workspace)
            status = str(state.get("status") or "")
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
