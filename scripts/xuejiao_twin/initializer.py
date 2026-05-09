from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE_CMD = f"PYTHONPATH={shlex.quote(str(REPO_ROOT))} python3 -m scripts.xuejiao_twin"

from . import SCHEMA_VERSION
from .util import now_utc, read_yaml_like, write_json


TWIN_GITIGNORE_PATTERN = ".xuejiao-twin*"


def load_goal(path: Path) -> dict[str, Any]:
    return read_yaml_like(path)


def ensure_twin_gitignore(project_root: Path) -> None:
    gitignore = project_root / ".gitignore"
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    if TWIN_GITIGNORE_PATTERN in lines:
        return
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(TWIN_GITIGNORE_PATTERN)
    gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")


def feature_ledger(goal: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "features": [],
        "current_focus": None,
        "last_verified_at": None,
        "planning_status": "needs_draft",
        "revision": 0,
    }


def init_workspace(goal_file: Path, persona_file: Path, out: Path | None = None) -> Path:
    goal = load_goal(goal_file)
    project_root = Path(str(goal["project_root"])).expanduser()
    workspace = out or project_root / ".xuejiao-twin"
    ensure_twin_gitignore(project_root)
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
    current = [
        "# xuejiao twin current",
        "",
        "- Status: initialized",
        f"- Goal: {goal.get('goal', '')}",
        "- Focus: none",
        "- Ledger: revision=0 completed=0 pending=0 blocked=0",
        f"- Next: {CLI_MODULE_CMD} run --workspace <workspace> --mode supervised-normal",
        "",
    ]
    (workspace / "CURRENT.md").write_text("\n".join(current), encoding="utf-8")

    runbook = [
        "# xuejiao twin runbook",
        "",
        "1. Read `goal.yaml`, `persona.lock.json`, `feature_ledger.json`, and `progress.md`.",
        "2. If `feature_ledger.json` is empty, run a plan-like ledger draft phase before implementation.",
        "3. Worker proposes ledger draft JSON from `goal.yaml`; runtime writes `feature_ledger.json` only after supervisor review and validation.",
        "4. Run `dry-run` as a single supervisor preview; run supervised modes as a multi-turn loop until a stop condition.",
        "5. Pick exactly one pending or in-progress ledger feature as current focus during implementation turns.",
        "6. Supervisor emits a JSON decision with action, instruction, feature updates, ledger updates, and reason.",
        "7. Worker produces code changes and validation evidence when supervisor action is `continue` after ledger approval.",
        "8. Runtime updates `feature_ledger.json` and appends `progress.md` after each turn.",
        "9. Worker may use Read/Edit/Write/Bash for bypass-like automation when goal allows it; ledger draft turns stay read-only.",
        "10. Runtime still injects disallowedTools for force push, reset/clean/rm, infra apply/destroy, production deploy, publish, docker push, and database drop.",
        "11. Stop for human gates: architecture, security, data, dependencies, production deploy, force push, external side effects, destructive actions.",
        "",
        "Validation commands:",
    ]
    for command in goal.get("validation_commands", []):
        runbook.append(f"- `{command}`")
    (workspace / "runbook.md").write_text("\n".join(runbook) + "\n", encoding="utf-8")
    return workspace
