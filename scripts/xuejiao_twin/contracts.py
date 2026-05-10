from __future__ import annotations

SCHEMA_VERSION = 1

GOAL_SCHEMA = "xuejiao_twin.goal.schema.json"
LEDGER_SCHEMA = "xuejiao_twin.ledger.schema.json"
RUN_SCHEMA = "xuejiao_twin.run.schema.json"
SUPERVISOR_STATE_SCHEMA = "xuejiao_twin.supervisor_state.schema.json"
SUPERVISOR_REVIEW_SCHEMA = "xuejiao_twin.supervisor_review.schema.json"
HUMAN_RESPONSE_SCHEMA = "xuejiao_twin.human_response.schema.json"

ITEM_STATUSES = {"pending", "in_progress", "blocked", "completed", "deferred"}

GOAL_FILE = "goal.yaml"
LEDGER_FILE = "feature_ledger.yaml"
SUPERVISOR_PERSONA_FILE = "supervisor-persona.md"
WORKER_PERSONA_FILE = "worker-persona.md"
SUPERVISOR_STATE_FILE = "supervisor_state.json"
HUMAN_RESPONSE_FILE = "human_response.json"
CURRENT_FILE = "CURRENT.md"
RUNS_DIR = "runs"
