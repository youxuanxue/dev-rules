from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .contracts import DEV_RULES_ROOT
from .privacy import stable_hash
from .runtime import continuation_action
from .supervisor_review import apply_supervisor_review, build_review_context, build_supervisor_context
from .util import now_utc
from .worker import start_worker_turn
from .workspace import WorkspaceError, append_workspace_event, load_state, render_current, validate_workspace, write_state


DRIVER_PROTOCOL_VERSION = 1
ALLOWED_SUPERVISOR_ROUTES = ("host/claude", "host/codex", "host/antigravity")
LOCK_DIR_ENV = "TWIN_DRIVER_LOCK_DIR"
MAX_DRIVER_STEPS = 8
MAX_CONTEXT_STRING_CHARS = 12_000
MAX_CONTEXT_LIST_ITEMS = 50
MAX_CONTEXT_CHARS = 64_000


def _resolve_route(route: str) -> str:
    value = route.strip()
    if value not in ALLOWED_SUPERVISOR_ROUTES:
        supported = ", ".join(ALLOWED_SUPERVISOR_ROUTES)
        raise WorkspaceError(f"unsupported supervisor route {value!r}; supported routes: {supported}")
    return value


def _lock_path(workspace: Path) -> Path:
    lock_root = Path(os.environ.get(LOCK_DIR_ENV) or Path.home() / ".twin" / "locks").expanduser().resolve()
    lock_root.mkdir(parents=True, exist_ok=True)
    workspace_hash = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()
    return lock_root / f"{workspace_hash}.lock"


@contextmanager
def workspace_driver_lock(workspace: Path) -> Iterator[None]:
    target = _lock_path(workspace)
    descriptor = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError(f"another twin driver is already active for {workspace}") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _bounded_value(value: Any, budget: list[int] | None = None) -> Any:
    if budget is None:
        budget = [MAX_CONTEXT_CHARS]
    if budget[0] <= 0:
        return "<context budget exhausted>"
    if isinstance(value, str):
        allowed = min(MAX_CONTEXT_STRING_CHARS, budget[0])
        budget[0] -= min(len(value), allowed)
        if len(value) <= allowed:
            return value
        omitted = len(value) - allowed
        return value[:allowed] + f"\n<trimmed {omitted} chars>"
    if isinstance(value, list):
        bounded: list[Any] = []
        for item in value[:MAX_CONTEXT_LIST_ITEMS]:
            if budget[0] <= 0:
                break
            bounded.append(_bounded_value(item, budget))
        if len(value) > MAX_CONTEXT_LIST_ITEMS:
            bounded.append({"trimmed_items": len(value) - MAX_CONTEXT_LIST_ITEMS})
        return bounded
    if isinstance(value, dict):
        bounded_dict: dict[str, Any] = {}
        for key, item in value.items():
            if budget[0] <= 0:
                bounded_dict["context_trimmed"] = True
                break
            key_text = str(key)
            budget[0] -= len(key_text)
            bounded_dict[key_text] = _bounded_value(item, budget)
        return bounded_dict
    budget[0] -= len(str(value))
    return value


def _instruction_context(workspace: Path) -> dict[str, Any]:
    context = build_supervisor_context(workspace)
    selected = {
        "supervisor_persona": context.get("supervisor_persona"),
        "goal": context.get("goal"),
        "current_plan_item": context.get("next_item"),
        "remaining_gaps": context.get("remaining_gaps"),
        "acceptance_evidence": context.get("acceptance_evidence"),
        "acceptance_focus": context.get("acceptance_focus"),
        "human_response": context.get("human_response"),
        "artifact_paths": context.get("artifact_paths"),
    }
    return _bounded_value({key: value for key, value in selected.items() if value is not None})


def _review_context(workspace: Path, run_id: str) -> dict[str, Any]:
    context = build_review_context(workspace, run_id)
    selected = {
        "supervisor_persona": context.get("supervisor_persona"),
        "goal": context.get("goal"),
        "current_plan_item": context.get("next_item"),
        "run": context.get("run"),
        "remaining_gaps": context.get("remaining_gaps"),
        "acceptance_evidence": context.get("acceptance_evidence"),
        "acceptance_focus": context.get("acceptance_focus"),
        "review_skeleton": context.get("review_skeleton"),
        "artifact_paths": context.get("artifact_paths"),
        "plan": context.get("plan"),
    }
    return _bounded_value({key: value for key, value in selected.items() if value is not None})


def _submit_argv(
    *,
    workspace: Path,
    route: str,
    pending: dict[str, Any],
) -> list[str]:
    common = [
        "--workspace",
        str(workspace),
        "--supervisor",
        route,
        "--state-revision",
        str(pending["state_revision"]),
        "--action-token",
        str(pending["token"]),
    ]
    if pending["kind"] == "supervisor_instruction":
        return ["twin", "submit-instruction", *common, "--instruction-file", "-"]
    return [
        "twin",
        "submit-review",
        *common,
        "--run-id",
        str(pending["run_id"]),
        "--review-file",
        "-",
    ]


def _pending_payload(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    route = str(state.get("supervisor_route") or "")
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise WorkspaceError("driver state has no pending supervisor action")
    action = str(pending.get("kind") or "")
    run_id = str(pending.get("run_id") or "")
    current = continuation_action(workspace)
    if current.get("action") != action:
        raise WorkspaceError(
            f"pending action {action!r} does not match current workspace action {current.get('action')!r}"
        )
    if action == "review_run" and current.get("current_run_id") != run_id:
        raise WorkspaceError("pending review run no longer matches the current run")
    argv = _submit_argv(workspace=workspace, route=route, pending=pending)
    if action == "supervisor_instruction":
        context = _instruction_context(workspace)
        expected_output: dict[str, Any] = {
            "media_type": "text/plain",
            "description": "A non-empty supervisor-authored worker instruction on stdin.",
        }
    elif action == "review_run":
        context = _review_context(workspace, run_id)
        expected_output = {
            "media_type": "application/json",
            "schema": str(DEV_RULES_ROOT / "schemas" / "twin.supervisor_review.schema.json"),
            "description": "One supervisor review JSON object on stdin.",
        }
    else:
        raise WorkspaceError(f"unsupported pending action: {action}")
    return {
        "driver_protocol_version": DRIVER_PROTOCOL_VERSION,
        "action": action,
        "workspace": str(workspace),
        "supervisor_route": route,
        "status": state.get("status"),
        "state_revision": pending["state_revision"],
        "action_token": pending["token"],
        "run_id": pending.get("run_id"),
        "context": context,
        "expected_output": expected_output,
        "submit": {
            "argv": argv,
            "command": shlex.join(argv),
            "stdin": expected_output["media_type"],
        },
        "instruction": "Make only the requested judgment, then submit it through the exact command above.",
    }


def _bind_route(workspace: Path, state: dict[str, Any], route: str) -> dict[str, Any]:
    bound = state.get("supervisor_route")
    if bound is not None and bound != route:
        raise WorkspaceError(
            f"supervisor route drift rejected: workspace is bound to {bound!r}, requested {route!r}"
        )
    if bound is None:
        state["supervisor_route"] = route
        write_state(workspace, state)
        state = load_state(workspace)
    return state


def _issue_pending_action(
    workspace: Path,
    state: dict[str, Any],
    *,
    kind: str,
    run_id: str | None,
) -> dict[str, Any]:
    if state.get("pending_action") is not None:
        return _pending_payload(workspace, state)
    next_revision = int(state.get("state_revision") or 0) + 1
    state["pending_action"] = {
        "kind": kind,
        "token": secrets.token_urlsafe(24),
        "state_revision": next_revision,
        "run_id": run_id,
        "issued_at": now_utc(),
    }
    write_state(workspace, state)
    stored = load_state(workspace)
    if stored.get("state_revision") != next_revision:
        raise WorkspaceError("driver failed to persist the expected state revision")
    return _pending_payload(workspace, stored)


def _non_judgment_payload(
    workspace: Path,
    state: dict[str, Any],
    route: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "driver_protocol_version": DRIVER_PROTOCOL_VERSION,
        "action": action.get("action"),
        "workspace": str(workspace),
        "supervisor_route": route,
        "status": action.get("status"),
        "state_revision": state.get("state_revision"),
        "run_id": action.get("current_run_id"),
        "next": action.get("next"),
    }
    for key in ("needs_human", "worker"):
        if action.get(key) is not None:
            payload[key] = _bounded_value(action[key])
    if action.get("action") in {"watch_worker", "ask_human"}:
        payload["resume_command"] = shlex.join(
            ["twin", "run", str(workspace), "--supervisor", route, "--json"]
        )
    return payload


def run_driver(
    workspace: Path | str,
    supervisor_route: str,
    *,
    max_budget_usd: float | None = None,
    worker_turn_fn: Callable[..., dict[str, Any]] = start_worker_turn,
) -> dict[str, Any]:
    workspace_path = Path(workspace).expanduser().resolve()
    route = _resolve_route(supervisor_route)
    with workspace_driver_lock(workspace_path):
        validate_workspace(workspace_path)
        state = _bind_route(workspace_path, load_state(workspace_path), route)
        if state.get("pending_action") is not None:
            return _pending_payload(workspace_path, state)
        for _step in range(MAX_DRIVER_STEPS):
            action = continuation_action(workspace_path)
            kind = str(action.get("action") or "")
            state = load_state(workspace_path)
            if kind == "supervisor_instruction":
                return _issue_pending_action(workspace_path, state, kind=kind, run_id=None)
            if kind == "review_run":
                run_id = str(action.get("current_run_id") or "")
                if not run_id:
                    raise WorkspaceError("review action is missing current_run_id")
                return _issue_pending_action(workspace_path, state, kind=kind, run_id=run_id)
            if kind in {"worker_turn", "recover_worker_turn"}:
                instruction = str(action.get("next_instruction") or "").strip()
                if not instruction:
                    raise WorkspaceError(f"{kind} requires a persisted next_instruction")
                worker_turn_fn(workspace_path, instruction, max_budget_usd=max_budget_usd)
                continue
            if kind in {"watch_worker", "ask_human", "done", "failed"}:
                return _non_judgment_payload(workspace_path, state, route, action)
            raise WorkspaceError(f"unsupported driver action: {kind or '<empty>'}")
    raise WorkspaceError("twin driver exceeded its deterministic step bound")


def _validate_submission(
    workspace: Path,
    *,
    route: str,
    state_revision: int,
    action_token: str,
    kind: str,
    run_id: str | None,
) -> dict[str, Any]:
    validate_workspace(workspace)
    state = load_state(workspace)
    if state.get("supervisor_route") != route:
        raise WorkspaceError(
            f"supervisor route mismatch: workspace is bound to {state.get('supervisor_route')!r}, submitted {route!r}"
        )
    if state.get("state_revision") != state_revision:
        raise WorkspaceError(
            f"stale state revision: current={state.get('state_revision')} submitted={state_revision}"
        )
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise WorkspaceError("no pending supervisor action; token is stale or already consumed")
    if pending.get("kind") != kind:
        raise WorkspaceError(f"wrong action submission: pending={pending.get('kind')} submitted={kind}")
    if pending.get("state_revision") != state_revision:
        raise WorkspaceError("pending action revision does not match supervisor state")
    if not hmac.compare_digest(str(pending.get("token") or ""), action_token):
        raise WorkspaceError("action token mismatch")
    pending_run_id = pending.get("run_id")
    if pending_run_id != run_id:
        raise WorkspaceError(f"wrong run submission: pending={pending_run_id!r} submitted={run_id!r}")
    current = continuation_action(workspace)
    if current.get("action") != kind:
        raise WorkspaceError(f"workspace action changed: current={current.get('action')!r} submitted={kind!r}")
    if run_id is not None and current.get("current_run_id") != run_id:
        raise WorkspaceError("current run changed before supervisor submission")
    return state


def submit_instruction(
    workspace: Path | str,
    supervisor_route: str,
    *,
    state_revision: int,
    action_token: str,
    instruction: str,
) -> dict[str, Any]:
    workspace_path = Path(workspace).expanduser().resolve()
    route = _resolve_route(supervisor_route)
    instruction_text = instruction.strip()
    if not instruction_text:
        raise WorkspaceError("supervisor instruction is required")
    with workspace_driver_lock(workspace_path):
        state = _validate_submission(
            workspace_path,
            route=route,
            state_revision=state_revision,
            action_token=action_token,
            kind="supervisor_instruction",
            run_id=None,
        )
        state["status"] = "continue"
        state["next_instruction"] = instruction_text
        state["pending_action"] = None
        write_state(workspace_path, state)
        goal, plan = validate_workspace(workspace_path)
        stored = load_state(workspace_path)
        render_current(workspace_path, goal, plan, stored)
        append_workspace_event(
            workspace_path,
            {
                "event": "supervisor_action_submitted",
                "kind": "supervisor_instruction",
                "supervisor_route": route,
                "submitted_state_revision": state_revision,
                "stored_state_revision": stored.get("state_revision"),
                "action_token_hash": stable_hash(action_token),
                "instruction_hash": stable_hash(instruction_text),
                "instruction_chars": len(instruction_text),
            },
        )
        return {
            "driver_protocol_version": DRIVER_PROTOCOL_VERSION,
            "status": stored.get("status"),
            "workspace": str(workspace_path),
            "supervisor_route": route,
            "state_revision": stored.get("state_revision"),
            "next_command": shlex.join(
                ["twin", "run", str(workspace_path), "--supervisor", route, "--json"]
            ),
        }


def submit_review(
    workspace: Path | str,
    supervisor_route: str,
    *,
    state_revision: int,
    action_token: str,
    run_id: str,
    review: dict[str, Any],
) -> dict[str, Any]:
    workspace_path = Path(workspace).expanduser().resolve()
    route = _resolve_route(supervisor_route)
    if not run_id.strip():
        raise WorkspaceError("run_id is required")
    with workspace_driver_lock(workspace_path):
        _validate_submission(
            workspace_path,
            route=route,
            state_revision=state_revision,
            action_token=action_token,
            kind="review_run",
            run_id=run_id,
        )
        state = apply_supervisor_review(
            workspace_path,
            run_id,
            review,
            clear_pending_action=True,
        )
        append_workspace_event(
            workspace_path,
            {
                "event": "supervisor_action_submitted",
                "kind": "review_run",
                "supervisor_route": route,
                "run_id": run_id,
                "review_status": review.get("status"),
                "submitted_state_revision": state_revision,
                "stored_state_revision": state.get("state_revision"),
                "action_token_hash": stable_hash(action_token),
            },
        )
        return {
            "driver_protocol_version": DRIVER_PROTOCOL_VERSION,
            "status": state.get("status"),
            "workspace": str(workspace_path),
            "supervisor_route": route,
            "state_revision": state.get("state_revision"),
            "run_id": run_id,
            "next_command": shlex.join(
                ["twin", "run", str(workspace_path), "--supervisor", route, "--json"]
            ),
        }
