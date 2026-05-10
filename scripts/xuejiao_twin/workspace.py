from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    CURRENT_FILE,
    GOAL_FILE,
    GOAL_SCHEMA,
    HUMAN_RESPONSE_FILE,
    HUMAN_RESPONSE_SCHEMA,
    LEDGER_JSON_FILE,
    LEDGER_SCHEMA,
    LEDGER_YAML_FILE,
    RUNS_DIR,
    SCHEMA_VERSION,
    SUPERVISOR_PERSONA_FILE,
    SUPERVISOR_STATE_FILE,
    SUPERVISOR_STATE_SCHEMA,
    WORKER_PERSONA_FILE,
)
from .ledger import choose_next_item, item_counts, ledger_gaps, validate_ledger_semantics
from .schema_contract import validate_schema
from .util import now_utc, read_json, read_yaml_like, write_json


class WorkspaceError(ValueError):
    pass


def resolve_workspace(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def ledger_path(workspace: Path) -> Path:
    json_path = workspace / LEDGER_JSON_FILE
    yaml_path = workspace / LEDGER_YAML_FILE
    if json_path.exists() and yaml_path.exists():
        raise WorkspaceError("feature_ledger.json and feature_ledger.yaml cannot both exist")
    if json_path.exists():
        return json_path
    if yaml_path.exists():
        return yaml_path
    raise WorkspaceError("missing feature_ledger.json or feature_ledger.yaml; run Claude Code plan mode first to prepare the ledger")


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
    path = ledger_path(workspace)
    ledger = read_json(path) if path.suffix == ".json" else read_yaml_like(path)
    errors = validate_schema(ledger, LEDGER_SCHEMA)
    if errors:
        raise WorkspaceError("feature_ledger schema errors: " + "; ".join(errors))
    return ledger


def write_ledger(workspace: Path, ledger: dict[str, Any]) -> None:
    path = ledger_path(workspace)
    if path.suffix == ".json":
        write_json(path, ledger)
    else:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - PyYAML is expected in normal envs
            raise WorkspaceError("writing YAML feature_ledger requires PyYAML") from exc
        path.write_text(yaml.safe_dump(ledger, allow_unicode=True, sort_keys=False), encoding="utf-8")


def read_text_file(workspace: Path, name: str) -> str:
    path = workspace / name
    if not path.exists():
        raise WorkspaceError(f"missing {name}")
    return path.read_text(encoding="utf-8")


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


def validate_workspace(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = resolve_workspace(workspace)
    if not workspace.exists() or not workspace.is_dir():
        raise WorkspaceError(f"workspace does not exist: {workspace}")
    goal = load_goal(workspace)
    ledger = load_ledger(workspace)
    for name in (SUPERVISOR_PERSONA_FILE, WORKER_PERSONA_FILE):
        if not (workspace / name).exists():
            raise WorkspaceError(f"missing {name}; copy the persona snapshot into the target workspace before running twin")
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
    lines = [
        "# xuejiao twin current",
        "",
        f"- Status: {state.get('status')}",
        f"- Goal: {goal.get('one_liner')}",
        f"- Current item: {state.get('current_item_id') or 'none'}",
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
    goal, ledger = validate_workspace(workspace)
    state = load_state(workspace)
    render_current(workspace, goal, ledger, state)
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
