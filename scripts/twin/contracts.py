from __future__ import annotations

import os
from pathlib import Path

SCHEMA_VERSION = 1

GOAL_SCHEMA = "twin.goal.schema.json"
PLAN_SCHEMA = "twin.plan.schema.json"
RESEARCH_SCHEMA = "twin.research.schema.json"
RUN_SCHEMA = "twin.run.schema.json"
SUPERVISOR_STATE_SCHEMA = "twin.supervisor_state.schema.json"
SUPERVISOR_REVIEW_SCHEMA = "twin.supervisor_review.schema.json"
HUMAN_RESPONSE_SCHEMA = "twin.human_response.schema.json"
ACTIVE_WORKSPACE_ENV = "TWIN_ACTIVE_WORKSPACE_FILE"

ITEM_STATUSES = {"pending", "in_progress", "blocked", "completed", "deferred"}

GOAL_FILE = "goal.yaml"
PLAN_FILE = "plan.yaml"
RESEARCH_FILE = "research.yaml"
LEGACY_PLAN_FILES = ("feature_ledger.yaml", "feature_ledger.json")
SUPERVISOR_PERSONA_FILE = "supervisor-persona.md"
WORKER_PERSONA_FILE = "worker-persona.md"
DEV_RULES_ROOT = Path(os.environ.get("DEV_RULES") or Path(__file__).resolve().parents[2]).expanduser().resolve()
PERSONAS_DIR = DEV_RULES_ROOT / "personas"
SUPERVISOR_PERSONA_PATH = PERSONAS_DIR / SUPERVISOR_PERSONA_FILE
WORKER_PERSONA_PATH = PERSONAS_DIR / WORKER_PERSONA_FILE
SUPERVISOR_STATE_FILE = "supervisor_state.json"
HUMAN_RESPONSE_FILE = "human_response.json"
WORKSPACE_EVENTS_FILE = "workspace_events.jsonl"
CURRENT_FILE = "CURRENT.md"
RUNS_DIR = "runs"
