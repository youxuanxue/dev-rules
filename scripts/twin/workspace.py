from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .contracts import (
    ACTIVE_WORKSPACE_ENV,
    CURRENT_FILE,
    GOAL_FILE,
    GOAL_SCHEMA,
    HUMAN_RESPONSE_FILE,
    HUMAN_RESPONSE_SCHEMA,
    PLAN_SCHEMA,
    WORKSPACE_EVENTS_FILE,
    LEGACY_PLAN_FILES,
    PLAN_FILE,
    RUNS_DIR,
    SCHEMA_VERSION,
    SUPERVISOR_PERSONA_FILE,
    SUPERVISOR_PERSONA_PATH,
    SUPERVISOR_STATE_FILE,
    SUPERVISOR_STATE_SCHEMA,
    WORKER_PERSONA_FILE,
    WORKER_PERSONA_PATH,
)
from .plan import choose_next_item, item_counts, plan_gaps, validate_plan_semantics
from .schema_contract import validate_schema
from .util import now_utc, read_json, read_yaml_like, write_json, write_yaml_like


class WorkspaceError(ValueError):
    pass


def active_workspace_file() -> Path:
    """Path to the active-twin-workspace pointer for the current project.

    Layout: ``~/.claude/twin-active-workspaces/<id>`` where ``<id>`` is the
    first 16 hex chars of ``sha256(resolved cwd)``. File content is a single
    line — the absolute path of the workspace last touched by ``/twin``,
    ``/twin status <ws>`` or ``/twin bootstrap``. ``TWIN_ACTIVE_WORKSPACE_FILE``
    overrides this resolution for tests and isolated runs.
    """
    override = os.environ.get(ACTIVE_WORKSPACE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    project_id = hashlib.sha256(str(Path.cwd().resolve()).encode("utf-8")).hexdigest()[:16]
    return Path.home() / ".claude" / "twin-active-workspaces" / project_id


def remember_active_workspace(workspace: Path | str) -> Path:
    resolved = resolve_workspace(workspace)
    target = active_workspace_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(str(resolved) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return resolved


def load_active_workspace() -> Path:
    target = active_workspace_file()
    if not target.exists():
        raise WorkspaceError("workspace is required; run /twin <workspace> or /twin status <workspace> first")
    text = target.read_text(encoding="utf-8").strip()
    if not text:
        raise WorkspaceError("active twin workspace is empty; run /twin <workspace> or /twin status <workspace> first")
    resolved = resolve_workspace(text)
    if not (resolved / GOAL_FILE).exists():
        raise WorkspaceError(
            f"active twin workspace no longer exists at {resolved}; "
            "run /twin <workspace> or /twin status <workspace> to set a new one"
        )
    return resolved


def resolve_workspace(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def plan_path(workspace: Path) -> Path:
    path = workspace / PLAN_FILE
    legacy = [name for name in LEGACY_PLAN_FILES if (workspace / name).exists()]
    if legacy:
        raise WorkspaceError(
            f"legacy twin plan file is not supported ({', '.join(legacy)}); rename it to {PLAN_FILE}"
        )
    if path.exists():
        return path
    raise WorkspaceError(f"missing {PLAN_FILE}; run /twin \"<one-liner>\" or Claude Code plan mode first")


def load_goal(workspace: Path) -> dict[str, Any]:
    path = workspace / GOAL_FILE
    if not path.exists():
        raise WorkspaceError("missing goal.yaml; run Claude Code plan mode first to prepare goal.yaml and plan")
    goal = read_yaml_like(path)
    errors = validate_schema(goal, GOAL_SCHEMA)
    if errors:
        raise WorkspaceError("goal.yaml schema errors: " + "; ".join(errors))
    return goal


def load_plan(workspace: Path) -> dict[str, Any]:
    plan = read_yaml_like(plan_path(workspace))
    errors = validate_schema(plan, PLAN_SCHEMA)
    if errors:
        raise WorkspaceError("plan schema errors: " + "; ".join(errors))
    return plan


def write_plan(workspace: Path, plan: dict[str, Any]) -> None:
    write_yaml_like(workspace / PLAN_FILE, plan)


def read_persona_file(path: Path) -> str:
    if not path.exists():
        raise WorkspaceError(f"missing twin persona: {path}")
    if not path.is_file():
        raise WorkspaceError(f"twin persona is not a file: {path}")
    return path.read_text(encoding="utf-8")


def load_supervisor_persona() -> str:
    return read_persona_file(SUPERVISOR_PERSONA_PATH)


def load_worker_persona() -> str:
    return read_persona_file(WORKER_PERSONA_PATH)


def validate_persona_contract(workspace: Path) -> None:
    persona_names = {SUPERVISOR_PERSONA_FILE, WORKER_PERSONA_FILE}
    forbidden = [str(path.relative_to(workspace)) for path in workspace.rglob("*.md") if path.name in persona_names]
    if forbidden:
        raise WorkspaceError(
            "persona files must not live in the target workspace; use "
            f"{SUPERVISOR_PERSONA_PATH} and {WORKER_PERSONA_PATH} directly: " + ", ".join(sorted(forbidden))
        )
    load_supervisor_persona()
    load_worker_persona()


def default_state(workspace: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace.name or str(workspace),
        "status": "idle",
        "round_index": 0,
        "current_run_id": None,
        "current_item_id": None,
        "worker_session_id": None,
        "next_instruction": "",
        "last_review_status": None,
        "needs_human": None,
        "updated_at": now_utc(),
    }


def load_state(workspace: Path) -> dict[str, Any]:
    path = workspace / SUPERVISOR_STATE_FILE
    if not path.exists():
        state = default_state(workspace)
        write_state(workspace, state)
        return state
    state = read_json(path)
    errors = validate_schema(state, SUPERVISOR_STATE_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_state schema errors: " + "; ".join(errors))
    return state


def write_state(workspace: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_utc()
    errors = validate_schema(state, SUPERVISOR_STATE_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_state schema errors: " + "; ".join(errors))
    write_json(workspace / SUPERVISOR_STATE_FILE, state)


def validate_workspace_readonly(workspace: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    workspace = resolve_workspace(workspace)
    if not workspace.exists() or not workspace.is_dir():
        raise WorkspaceError(f"workspace does not exist: {workspace}")
    goal = load_goal(workspace)
    plan = load_plan(workspace)
    validate_persona_contract(workspace)
    semantic_errors = validate_plan_semantics(goal, plan)
    if semantic_errors:
        raise WorkspaceError("plan semantic errors: " + "; ".join(semantic_errors))
    state_path = workspace / SUPERVISOR_STATE_FILE
    if not state_path.exists():
        raise WorkspaceError(f"missing {SUPERVISOR_STATE_FILE}; run /twin <workspace> to initialize runtime state")
    state = read_json(state_path)
    errors = validate_schema(state, SUPERVISOR_STATE_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_state schema errors: " + "; ".join(errors))
    return goal, plan, state


def validate_workspace(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = resolve_workspace(workspace)
    if not workspace.exists() or not workspace.is_dir():
        raise WorkspaceError(f"workspace does not exist: {workspace}")
    goal = load_goal(workspace)
    plan = load_plan(workspace)
    validate_persona_contract(workspace)
    semantic_errors = validate_plan_semantics(goal, plan)
    if semantic_errors:
        raise WorkspaceError("plan semantic errors: " + "; ".join(semantic_errors))
    (workspace / RUNS_DIR).mkdir(exist_ok=True)
    load_state(workspace)
    return goal, plan


def write_human_response(workspace: Path, text: str) -> Path:
    if not text.strip():
        raise WorkspaceError("response text is required")
    response = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": now_utc(),
        "text": text.strip(),
        "consumed_by_run_id": None,
    }
    errors = validate_schema(response, HUMAN_RESPONSE_SCHEMA)
    if errors:
        raise WorkspaceError("human_response schema errors: " + "; ".join(errors))
    target = workspace / HUMAN_RESPONSE_FILE
    write_json(target, response)
    return target


def load_human_response(workspace: Path) -> dict[str, Any] | None:
    path = workspace / HUMAN_RESPONSE_FILE
    if not path.exists():
        return None
    value = read_json(path)
    errors = validate_schema(value, HUMAN_RESPONSE_SCHEMA)
    if errors:
        raise WorkspaceError("human_response schema errors: " + "; ".join(errors))
    return value


def status_display(workspace: Path, goal: dict[str, Any], plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    workspace = resolve_workspace(workspace)
    status = str(state.get("status") or "unknown")
    next_item = choose_next_item(plan)
    current_item_id = state.get("current_item_id") or (next_item.get("id") if next_item else None)
    labels = {
        "idle": "ready",
        "worker_running": "working",
        "review_required": "reviewing",
        "continue": "ready for next turn",
        "needs_human": "waiting for you",
        "accepted_done": "done",
        "failed": "failed",
    }
    summaries = {
        "idle": "Workspace is ready for the supervisor loop.",
        "worker_running": "Worker is running; wait for the run artifact before reviewing.",
        "review_required": "Worker finished; supervisor review is required before another turn.",
        "continue": "Supervisor has a next instruction ready for the next worker turn.",
        "needs_human": "Supervisor needs a human answer before continuing.",
        "accepted_done": "Supervisor accepted the workspace as done.",
        "failed": "Supervisor marked the workspace as failed.",
    }
    next_commands = {
        "idle": f"/twin {workspace}",
        "worker_running": f"/twin status {workspace}",
        "review_required": f"/twin {workspace}",
        "continue": f"/twin {workspace}",
        "needs_human": "/twin respond <answer>",
        "accepted_done": "none",
        "failed": "inspect CURRENT.md and latest run evidence",
    }
    current_run_id = state.get("current_run_id")
    run_ref = str(workspace / RUNS_DIR / str(current_run_id) / "run.json") if current_run_id else None
    return {
        "label": labels.get(status, status),
        "summary": summaries.get(status, "Workspace state is available in supervisor_state.json."),
        "next_command": next_commands.get(status, f"/twin status {workspace}"),
        "current_item_id": current_item_id,
        "evidence_paths": {
            "current": str(workspace / CURRENT_FILE),
            "state": str(workspace / SUPERVISOR_STATE_FILE),
            "workspace_events": str(workspace / WORKSPACE_EVENTS_FILE),
            "run": run_ref,
            "review": f"{run_ref}::review" if run_ref else None,
        },
    }


def render_current(workspace: Path, goal: dict[str, Any], plan: dict[str, Any], state: dict[str, Any]) -> None:
    counts = item_counts(plan)
    gaps = plan_gaps(goal, plan)
    display = status_display(workspace, goal, plan, state)
    lines = [
        "# twin current",
        "",
        f"- Goal: {goal.get('one_liner')}",
        f"- Status: {display['label']} ({state.get('status')})",
        f"- Summary: {display['summary']}",
        f"- Current item: {display.get('current_item_id') or 'none'}",
        f"- Round: {state.get('round_index')}",
        f"- Plan: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())),
        f"- Last review status: {state.get('last_review_status') or 'none'}",
        f"- Next command: {display['next_command']}",
        f"- Next instruction: {state.get('next_instruction') or 'none'}",
        "",
        "## Evidence",
        *(f"- {key}: {value or 'none'}" for key, value in display["evidence_paths"].items()),
        "",
        "## Plan gaps",
        *(f"- {gap}" for gap in (gaps or ["none"])),
    ]
    needs_human = state.get("needs_human")
    if isinstance(needs_human, dict):
        lines.extend([
            "",
            "## NEEDS_HUMAN",
            f"- Question: {needs_human.get('question') or ''}",
            f"- Context: {needs_human.get('context') or ''}",
        ])
    lines.append("")
    (workspace / CURRENT_FILE).write_text("\n".join(lines), encoding="utf-8")


def append_workspace_event(workspace: Path, event: dict[str, Any]) -> Path:
    workspace = resolve_workspace(workspace)
    record = {"schema_version": SCHEMA_VERSION, "recorded_at": now_utc(), **event}
    target = workspace / WORKSPACE_EVENTS_FILE
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return target


def status_summary(workspace: Path) -> dict[str, Any]:
    goal, plan, state = validate_workspace_readonly(workspace)
    workspace = resolve_workspace(workspace)
    display = status_display(workspace, goal, plan, state)
    return {
        "workspace": str(workspace),
        "goal": goal.get("one_liner"),
        "status": state.get("status"),
        "current_run_id": state.get("current_run_id"),
        "current_item_id": display.get("current_item_id"),
        "round_index": state.get("round_index"),
        "next_instruction": state.get("next_instruction"),
        "needs_human": state.get("needs_human"),
        "plan_counts": item_counts(plan),
        "remaining_gaps": plan_gaps(goal, plan),
        "current": str(workspace / CURRENT_FILE),
        "display": display,
    }
