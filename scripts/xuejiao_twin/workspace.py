from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import (
    CURRENT_FILE,
    GOAL_FILE,
    GOAL_SCHEMA,
    HUMAN_RESPONSE_FILE,
    HUMAN_RESPONSE_SCHEMA,
    LEDGER_FILE,
    LEDGER_SCHEMA,
    RUNS_DIR,
    SCHEMA_VERSION,
    SUPERVISOR_PERSONA_FILE,
    SUPERVISOR_PERSONA_PATH,
    SUPERVISOR_STATE_FILE,
    SUPERVISOR_STATE_SCHEMA,
    WORKER_PERSONA_FILE,
    WORKER_PERSONA_PATH,
)
from .ledger import choose_next_item, item_counts, ledger_gaps, validate_ledger_semantics
from .schema_contract import validate_schema
from .util import now_utc, read_json, read_yaml_like, write_json, write_yaml_like


class WorkspaceError(ValueError):
    pass


def resolve_workspace(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def ledger_path(workspace: Path) -> Path:
    path = workspace / LEDGER_FILE
    legacy_json = workspace / "feature_ledger.json"
    if legacy_json.exists():
        raise WorkspaceError("feature_ledger.json is not supported; use feature_ledger.yaml")
    if path.exists():
        return path
    raise WorkspaceError("missing feature_ledger.yaml; run Claude Code plan mode first to prepare the ledger")


def load_goal(workspace: Path) -> dict[str, Any]:
    path = workspace / GOAL_FILE
    if not path.exists():
        raise WorkspaceError("missing goal.yaml; run Claude Code plan mode first to prepare goal.yaml and feature_ledger")
    goal = read_yaml_like(path)
    errors = validate_schema(goal, GOAL_SCHEMA)
    if errors:
        raise WorkspaceError("goal.yaml schema errors: " + "; ".join(errors))
    return goal


def load_ledger(workspace: Path) -> dict[str, Any]:
    ledger = read_yaml_like(ledger_path(workspace))
    errors = validate_schema(ledger, LEDGER_SCHEMA)
    if errors:
        raise WorkspaceError("feature_ledger schema errors: " + "; ".join(errors))
    return ledger


def write_ledger(workspace: Path, ledger: dict[str, Any]) -> None:
    write_yaml_like(workspace / LEDGER_FILE, ledger)


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
    forbidden = [name for name in (SUPERVISOR_PERSONA_FILE, WORKER_PERSONA_FILE) if (workspace / name).exists()]
    if forbidden:
        raise WorkspaceError(
            "persona files must not live in the target workspace; use "
            f"{SUPERVISOR_PERSONA_PATH} and {WORKER_PERSONA_PATH} directly: " + ", ".join(forbidden)
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
        "last_decision": None,
        "needs_human": None,
        "failure_streaks": {},
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
    ledger = load_ledger(workspace)
    validate_persona_contract(workspace)
    semantic_errors = validate_ledger_semantics(goal, ledger)
    if semantic_errors:
        raise WorkspaceError("feature_ledger semantic errors: " + "; ".join(semantic_errors))
    state_path = workspace / SUPERVISOR_STATE_FILE
    if not state_path.exists():
        raise WorkspaceError(f"missing {SUPERVISOR_STATE_FILE}; run /twin <workspace> to initialize runtime state")
    state = read_json(state_path)
    errors = validate_schema(state, SUPERVISOR_STATE_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_state schema errors: " + "; ".join(errors))
    return goal, ledger, state


def validate_workspace(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = resolve_workspace(workspace)
    if not workspace.exists() or not workspace.is_dir():
        raise WorkspaceError(f"workspace does not exist: {workspace}")
    goal = load_goal(workspace)
    ledger = load_ledger(workspace)
    validate_persona_contract(workspace)
    semantic_errors = validate_ledger_semantics(goal, ledger)
    if semantic_errors:
        raise WorkspaceError("feature_ledger semantic errors: " + "; ".join(semantic_errors))
    (workspace / RUNS_DIR).mkdir(exist_ok=True)
    load_state(workspace)
    return goal, ledger


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


def render_current(workspace: Path, goal: dict[str, Any], ledger: dict[str, Any], state: dict[str, Any]) -> None:
    counts = item_counts(ledger)
    gaps = ledger_gaps(goal, ledger)
    next_item = choose_next_item(ledger)
    current_item_id = state.get("current_item_id") or (next_item.get("id") if next_item else None)
    lines = [
        "# xuejiao twin current",
        "",
        f"- Status: {state.get('status')}",
        f"- Goal: {goal.get('one_liner')}",
        f"- Current item: {current_item_id or 'none'}",
        f"- Round: {state.get('round_index')}",
        f"- Ledger: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())),
        f"- Last decision: {state.get('last_decision') or 'none'}",
        f"- Next instruction: {state.get('next_instruction') or 'none'}",
        "",
        "## Ledger gaps",
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


def status_summary(workspace: Path) -> dict[str, Any]:
    goal, ledger, state = validate_workspace_readonly(workspace)
    workspace = resolve_workspace(workspace)
    next_item = choose_next_item(ledger)
    return {
        "workspace": str(workspace),
        "goal": goal.get("one_liner"),
        "status": state.get("status"),
        "current_run_id": state.get("current_run_id"),
        "current_item_id": state.get("current_item_id") or (next_item.get("id") if next_item else None),
        "round_index": state.get("round_index"),
        "next_instruction": state.get("next_instruction"),
        "needs_human": state.get("needs_human"),
        "ledger_counts": item_counts(ledger),
        "remaining_gaps": ledger_gaps(goal, ledger),
        "current": str(workspace / CURRENT_FILE),
    }
