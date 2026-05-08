from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .util import now_utc, read_yaml_like, write_json


def load_goal(path: Path) -> dict[str, Any]:
    return read_yaml_like(path)


def feature_ledger(goal: dict[str, Any]) -> dict[str, Any]:
    acceptance = list(goal.get("acceptance", []))
    features = []
    scope_items = list(goal.get("scope_in", [])) or [goal.get("goal", "Complete requested goal")]
    for index, item in enumerate(scope_items, 1):
        features.append({
            "id": f"F-{index:03d}",
            "description": str(item),
            "status": "pending",
            "acceptance": acceptance,
            "validation_evidence": [],
            "blocked_reason": None,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "features": features,
        "current_focus": features[0]["id"] if features else None,
        "last_verified_at": None,
    }


def init_workspace(goal_file: Path, persona_file: Path, out: Path | None = None) -> Path:
    goal = load_goal(goal_file)
    project_root = Path(str(goal["project_root"])).expanduser()
    workspace = out or project_root / ".xuejiao-twin"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "runs").mkdir(exist_ok=True)

    shutil.copyfile(goal_file, workspace / "goal.yaml")
    shutil.copyfile(persona_file, workspace / "persona.lock.json")
    write_json(workspace / "feature_ledger.json", feature_ledger(goal))

    progress = [
        "# xuejiao twin progress",
        "",
        f"Initialized: {now_utc()}",
        f"Goal: {goal.get('goal', '')}",
        "",
        "## Current state",
        "- Status: initialized",
        "- Next action: run supervisor in dry-run or supervised mode",
        "",
    ]
    (workspace / "progress.md").write_text("\n".join(progress), encoding="utf-8")

    runbook = [
        "# xuejiao twin runbook",
        "",
        "1. Read `goal.yaml`, `persona.lock.json`, `feature_ledger.json`, and `progress.md`.",
        "2. Run `dry-run` as a single supervisor preview; run supervised modes as a multi-turn loop until a stop condition.",
        "3. Pick exactly one pending or in-progress ledger feature as current focus each turn.",
        "4. Supervisor emits a JSON decision with action, instruction, feature updates, and reason.",
        "5. Worker produces code changes and validation evidence when supervisor action is `continue`.",
        "6. Update `feature_ledger.json` and append `progress.md` after each turn.",
        "7. Stop for human gates: architecture, security, data, dependencies, production deploy, force push, external side effects, destructive actions.",
        "",
        "Validation commands:",
    ]
    for command in goal.get("validation_commands", []):
        runbook.append(f"- `{command}`")
    (workspace / "runbook.md").write_text("\n".join(runbook) + "\n", encoding="utf-8")
    return workspace
