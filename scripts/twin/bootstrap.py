from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contracts import GOAL_FILE, PLAN_FILE, SCHEMA_VERSION
from .plan import validate_bootstrap_plan_constraints, validate_plan_semantics
from .schema_contract import validate_schema
from .util import read_yaml_like, write_yaml_like
from .workspace import WorkspaceError, load_state, render_current, validate_workspace


def slugify_goal(goal: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", goal.lower())
    if words:
        return "-".join(words[:6])[:48].strip("-") or "twin-goal"
    digest = abs(hash(goal)) % 100000
    return f"twin-goal-{digest:05d}"


def draft_workspace(goal: str, workspace: Path | None = None) -> dict[str, Any]:
    text = goal.strip()
    if not text:
        raise WorkspaceError("goal text is required")
    slug = slugify_goal(text)
    target = workspace or Path(".twin") / slug
    goal_doc = {
        "schema_version": SCHEMA_VERSION,
        "id": slug,
        "one_liner": text,
        "core_goal": text,
        "acceptance_criteria": [
            {
                "id": "AC1",
                "statement": f"{text} 的核心结果可以被验证。",
                "evidence_type": "tests/preflight or equivalent run evidence",
            }
        ],
        "non_goals": ["不扩展到未确认的相邻需求"],
    }
    plan_doc = {
        "schema_version": SCHEMA_VERSION,
        "goal_id": slug,
        "items": [
            {
                "id": "F1",
                "deliverable": "最小可验收目标切片",
                "scope": "只确认该目标的第一条可交付边界；不扩展相邻需求或最终验收大包",
                "covers_ac": ["AC1"],
                "evidence_plan": [
                    "证据预算：只收集一组最小 diff 摘要和一项针对性验证，不跑全量验收",
                    "停止条件：完成最小验证证据后转 review；范围不清时 needs_human",
                ],
                "actual_evidence": [],
                "depends_on": [],
                "status": "pending",
                "next_action": "定位最小可交付边界，产出一项验证证据后转 review",
            }
        ],
    }
    errors = validate_schema(goal_doc, "twin.goal.schema.json")
    errors.extend(validate_schema(plan_doc, "twin.plan.schema.json"))
    errors.extend(validate_plan_semantics(goal_doc, plan_doc))
    errors.extend(validate_bootstrap_plan_constraints(goal_doc, plan_doc))
    if errors:
        raise WorkspaceError("bootstrap draft schema errors: " + "; ".join(errors))
    return {
        "workspace": str(target),
        "goal": goal_doc,
        "plan": plan_doc,
    }


def draft_from_files(workspace: Path, goal_file: Path, plan_file: Path) -> dict[str, Any]:
    goal_doc = read_yaml_like(goal_file.expanduser().resolve())
    plan_doc = read_yaml_like(plan_file.expanduser().resolve())
    errors = validate_schema(goal_doc, "twin.goal.schema.json")
    errors.extend(validate_schema(plan_doc, "twin.plan.schema.json"))
    errors.extend(validate_plan_semantics(goal_doc, plan_doc))
    errors.extend(validate_bootstrap_plan_constraints(goal_doc, plan_doc))
    if errors:
        raise WorkspaceError("supervisor-authored bootstrap artifacts are invalid: " + "; ".join(errors))
    return {
        "workspace": str(workspace),
        "goal": goal_doc,
        "plan": plan_doc,
    }


def write_workspace_draft(draft: dict[str, Any], *, overwrite: bool = False) -> Path:
    workspace = Path(str(draft.get("workspace") or "")).expanduser().resolve()
    if not workspace:
        raise WorkspaceError("draft workspace is required")
    if workspace.exists() and not overwrite:
        existing = [name for name in (GOAL_FILE, PLAN_FILE) if (workspace / name).exists()]
        if existing:
            raise WorkspaceError(f"workspace already has twin inputs: {workspace} ({', '.join(existing)})")
    workspace.mkdir(parents=True, exist_ok=True)
    write_yaml_like(workspace / GOAL_FILE, dict(draft.get("goal") or {}))
    write_yaml_like(workspace / PLAN_FILE, dict(draft.get("plan") or {}))
    goal, plan = validate_workspace(workspace)
    render_current(workspace, goal, plan, load_state(workspace))
    return workspace
