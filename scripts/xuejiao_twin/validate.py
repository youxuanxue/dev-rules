from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .claude_runner import ClaudeRunResult
from .contracts import (
    GOAL_SCHEMA,
    LEDGER_SCHEMA,
    RUN_SCHEMA,
    SUPERVISOR_REVIEW_SCHEMA,
    SUPERVISOR_STATE_SCHEMA,
)
from .ledger import acceptance_evidence, ledger_gaps, validate_ledger_semantics
from .runtime import (
    apply_supervisor_review,
    build_review_context,
    build_supervisor_context,
    record_human_response,
    start_worker_turn,
    status_workspace,
    validate_workspace,
)
from .schema_contract import validate_artifact, validate_schema
from .util import read_json
from .workspace import WorkspaceError, load_state


class FakeRunner:
    def __init__(self, *, session_lost_once: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.session_lost_once = session_lost_once

    def __call__(self, prompt: str, **kwargs: Any) -> ClaudeRunResult:
        self.calls.append({"prompt": prompt, **kwargs})
        requested_session = str(kwargs.get("session_id") or "")
        if self.session_lost_once and requested_session:
            self.session_lost_once = False
            return ClaudeRunResult(session_id=requested_session, output_text="", returncode=0, raw_events=[], session_lost=True)
        session = requested_session or "worker-session-1"
        return ClaudeRunResult(
            session_id=session,
            output_text="Summary:\n- fixture worker ran\n\nEvidence:\n- tests: fixture pass\n\nRemaining:\n- none",
            returncode=0,
            raw_events=[{"type": "system", "session_id": session}],
        )


def _goal() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "fixture-goal",
        "one_liner": "交付 fixture 目标",
        "core_goal": "让 fixture 主流程闭环。",
        "acceptance_criteria": [
            {"id": "AC1", "statement": "fixture 有测试证据", "evidence_type": "tests/preflight"},
            {"id": "AC2", "statement": "fixture 有 PR 或 diff 证据", "evidence_type": "diff"},
        ],
        "non_goals": ["不测试旧 init/run/replan 兼容"],
    }


def _ledger(*, completed: bool = False) -> dict[str, Any]:
    evidence = ["tests: fixture pass", "diff: fixture stat"] if completed else []
    return {
        "schema_version": 1,
        "goal_id": "fixture-goal",
        "items": [
            {
                "id": "F1",
                "deliverable": "完成 fixture 主流程",
                "scope": "只覆盖 greenfield worker turn",
                "covers_ac": ["AC1", "AC2"],
                "evidence_plan": ["tests pass", "diff summary"],
                "actual_evidence": evidence,
                "depends_on": [],
                "status": "completed" if completed else "pending",
                "next_action": "" if completed else "运行 worker fixture",
            }
        ],
    }


def _review(decision: str, *, gaps: list[str] | None = None, question: str | None = None, actions: list[str] | None = None) -> dict[str, Any]:
    return {
        "decision": decision,
        "summary": "fixture supervisor review",
        "next_instruction": "继续 fixture" if decision == "CONTINUE" else "",
        "remaining_gaps": gaps or [],
        "acceptance_evidence": [
            {"ac_id": "AC1", "evidence": ["tests: fixture pass"]},
            {"ac_id": "AC2", "evidence": ["diff: fixture stat"]},
        ],
        "risk_flags": [],
        "actions": actions or [],
        "ledger_updates": [
            {"item_id": "F1", "status": "completed", "actual_evidence": ["tests: fixture pass", "diff: fixture stat"], "next_action": ""}
        ],
        "human_question": question,
    }


def _write_workspace(root: Path, *, completed: bool = False, ledger_evidence: bool | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "goal.yaml").write_text(
        """schema_version: 1
id: fixture-goal
one_liner: 交付 fixture 目标
core_goal: |
  让 fixture 主流程闭环。
acceptance_criteria:
  - id: AC1
    statement: fixture 有测试证据
    evidence_type: tests/preflight
  - id: AC2
    statement: fixture 有 PR 或 diff 证据
    evidence_type: diff
non_goals:
  - 不测试旧 init/run/replan 兼容
""",
        encoding="utf-8",
    )
    status = "completed" if completed else "pending"
    has_evidence = completed if ledger_evidence is None else ledger_evidence
    evidence = '\n      - "tests: fixture pass"\n      - "diff: fixture stat"' if has_evidence else " []"
    next_action = '""' if completed else "运行 worker fixture"
    (workspace / "feature_ledger.yaml").write_text(
        f"""schema_version: 1
goal_id: fixture-goal
items:
  - id: F1
    deliverable: 完成 fixture 主流程
    scope: 只覆盖 greenfield worker turn
    covers_ac:
      - AC1
      - AC2
    evidence_plan:
      - tests pass
      - diff summary
    actual_evidence:{evidence}
    depends_on: []
    status: {status}
    next_action: {next_action}
""",
        encoding="utf-8",
    )
    (workspace / "supervisor-persona.md").write_text("supervisor persona", encoding="utf-8")
    (workspace / "worker-persona.md").write_text("worker persona", encoding="utf-8")
    return workspace


def _schema_errors() -> list[str]:
    errors: list[str] = []
    for value, schema in [
        (_goal(), GOAL_SCHEMA),
        (_ledger(), LEDGER_SCHEMA),
        (_review("CONTINUE", actions=["fix_drift", "validate_more", "mark_ledger_gap"]), SUPERVISOR_REVIEW_SCHEMA),
    ]:
        errors.extend(validate_schema(value, schema))
    old_goal = {**_goal(), "project_root": "/tmp/project", "allowed_tools": {}}
    if not validate_schema(old_goal, GOAL_SCHEMA):
        errors.append("old goal fields should be rejected")
    old_review = {**_review("CONTINUE"), "supervisor_session_id": "legacy"}
    if not validate_schema(old_review, SUPERVISOR_REVIEW_SCHEMA):
        errors.append("old supervisor review fields should be rejected")
    return errors


def _semantic_errors() -> list[str]:
    errors: list[str] = []
    bad_ledger = _ledger()
    bad_ledger["items"][0]["covers_ac"] = ["NOPE"]
    if not validate_ledger_semantics(_goal(), bad_ledger):
        errors.append("unknown AC reference should fail")
    uncovered = _ledger()
    uncovered["items"][0]["covers_ac"] = ["AC1"]
    if not validate_ledger_semantics(_goal(), uncovered):
        errors.append("uncovered AC should fail")
    repeated = _ledger()
    repeated["items"][0]["deliverable"] = "fixture 有测试证据"
    if not validate_ledger_semantics(_goal(), repeated):
        errors.append("ledger repeating AC statement should fail")
    cyclic = _ledger()
    cyclic["items"].append({**cyclic["items"][0], "id": "F2", "depends_on": ["F1"]})
    cyclic["items"][0]["depends_on"] = ["F2"]
    if not validate_ledger_semantics(_goal(), cyclic):
        errors.append("dependency cycle should fail")
    return errors


def run_fixture_validation() -> list[str]:
    errors: list[str] = _schema_errors() + _semantic_errors()
    with tempfile.TemporaryDirectory(prefix="xuejiao-twin-greenfield-") as tmp:
        root = Path(tmp)
        workspace = _write_workspace(root)
        try:
            validate_workspace(workspace)
        except WorkspaceError as exc:
            errors.append(f"valid workspace failed: {exc}")
        errors.extend(validate_artifact(workspace / "supervisor_state.json", SUPERVISOR_STATE_SCHEMA))
        supervisor_context = build_supervisor_context(workspace)
        if supervisor_context.get("next_item", {}).get("id") != "F1" or not supervisor_context.get("remaining_gaps"):
            errors.append("supervisor context should expose next item and gaps without generating instruction")
        skeleton = supervisor_context.get("review_skeleton", {})
        if skeleton.get("decision") != "<ACCEPTED_DONE|CONTINUE|NEEDS_HUMAN|FAILED>":
            errors.append("supervisor context should expose an undecided review skeleton")
        if not ledger_gaps(_goal(), _ledger()):
            errors.append("pending ledger should have gaps")
        if any(item["evidence"] for item in acceptance_evidence(_goal(), _ledger())):
            errors.append("empty ledger should not produce acceptance evidence")

        missing = root / "missing"
        missing.mkdir()
        try:
            validate_workspace(missing)
            errors.append("missing inputs should fail")
        except WorkspaceError:
            pass

        legacy_json = _write_workspace(root / "legacy-json")
        (legacy_json / "feature_ledger.json").write_text("{}\n", encoding="utf-8")
        try:
            validate_workspace(legacy_json)
            errors.append("feature_ledger.json should fail even when feature_ledger.yaml exists")
        except WorkspaceError:
            pass

        runner = FakeRunner()
        try:
            start_worker_turn(workspace, "", runner=runner)
            errors.append("worker-turn should require supervisor-authored instruction")
        except WorkspaceError:
            pass
        run = start_worker_turn(workspace, "推进 ledger item F1", runner=runner)
        errors.extend(validate_schema(run, RUN_SCHEMA))
        call = runner.calls[0]
        if call.get("session_id"):
            errors.append("first worker turn should not resume")
        if call.get("permission_mode") != "bypass":
            errors.append("worker permission mode should be bypass")
        allowed = call.get("allowed_tools") or []
        if "Bash" not in allowed or "Read" not in allowed:
            errors.append("worker should receive explicit allowed tools")
        prompt = str(call.get("prompt") or "")
        if "worker persona" not in prompt or "fixture-goal" not in prompt or "推进 ledger item F1" not in prompt:
            errors.append("worker prompt missing persona/goal/supervisor-authored instruction")
        if "supervisor persona" in prompt:
            errors.append("worker prompt should not include supervisor persona")
        if "supervisor_session_id" in json.dumps(run):
            errors.append("run artifact should not contain supervisor session fields")

        context = build_review_context(workspace, run["run_id"])
        if "supervisor persona" not in context.get("supervisor_persona", ""):
            errors.append("review context should include supervisor persona")
        if context.get("review_skeleton", {}).get("decision") != "<ACCEPTED_DONE|CONTINUE|NEEDS_HUMAN|FAILED>":
            errors.append("review context should not preselect a decision")

        continue_review = _review("CONTINUE", gaps=["F1 missing"], actions=["fix_drift", "validate_more"])
        state = apply_supervisor_review(workspace, run["run_id"], continue_review)
        if state.get("status") != "continue" or state.get("next_instruction") != "继续 fixture":
            errors.append("CONTINUE review should store supervisor-authored next instruction unchanged")

        second = start_worker_turn(workspace, "继续 F1", runner=runner)
        if not runner.calls[-1].get("session_id"):
            errors.append("second worker turn should resume saved worker session")
        if not second["worker"]["resume_used"]:
            errors.append("second run artifact should mark resume_used")

        reset_runner = FakeRunner(session_lost_once=True)
        reset = start_worker_turn(workspace, "测试 session reset", runner=reset_runner)
        if len(reset_runner.calls) != 2:
            errors.append("session lost should retry fresh once")
        if reset_runner.calls[-1].get("session_id"):
            errors.append("session lost retry should be fresh")
        if reset["worker"]["resume_used"]:
            errors.append("fresh retry should not mark resume_used")

        needs_human = _review("NEEDS_HUMAN", question="请确认 fixture")
        state = apply_supervisor_review(workspace, run["run_id"], needs_human)
        if state.get("status") != "needs_human" or not state.get("needs_human"):
            errors.append("NEEDS_HUMAN review did not update state")
        status = status_workspace(workspace)
        if not status.get("needs_human") or not status.get("current_run_id"):
            errors.append("status should expose needs_human and current run")
        try:
            start_worker_turn(workspace, "不应启动", runner=runner)
            errors.append("worker should not start while needs_human pending")
        except WorkspaceError:
            pass
        record_human_response(workspace, "继续")
        state = load_state(workspace)
        if state.get("status") != "continue" or state.get("needs_human") is not None:
            errors.append("respond did not clear needs_human state")
        resumed = start_worker_turn(workspace, "继续 fixture", runner=runner)
        consumed = read_json(workspace / "human_response.json")
        if consumed.get("consumed_by_run_id") != resumed["run_id"]:
            errors.append("worker turn should consume human_response.json")
        if "human_response.json" not in str(runner.calls[-1].get("prompt")):
            errors.append("worker prompt should include unconsumed human response")

        repeat_review = _review("CONTINUE", gaps=["same gap"], actions=[])
        repeat_workspace = _write_workspace(root / "repeat")
        repeat_runner = FakeRunner()
        repeat_run = start_worker_turn(repeat_workspace, "重复 gap fixture", runner=repeat_runner)
        apply_supervisor_review(repeat_workspace, repeat_run["run_id"], repeat_review)
        repeat_run = start_worker_turn(repeat_workspace, "重复 gap fixture", runner=repeat_runner)
        second_repeat_state = apply_supervisor_review(repeat_workspace, repeat_run["run_id"], repeat_review)
        if second_repeat_state.get("next_instruction") != "继续 fixture":
            errors.append("second same gap should keep supervisor-authored instruction unchanged")
        repeat_run = start_worker_turn(repeat_workspace, "重复 gap fixture", runner=repeat_runner)
        repeat_state = apply_supervisor_review(repeat_workspace, repeat_run["run_id"], repeat_review)
        if repeat_state.get("status") != "needs_human":
            errors.append("same gap for three rounds should require human")

        done_missing_evidence_workspace = _write_workspace(root / "done-missing-evidence", completed=True, ledger_evidence=False)
        done_missing_evidence_runner = FakeRunner()
        done_missing_evidence_run = start_worker_turn(done_missing_evidence_workspace, "final", runner=done_missing_evidence_runner)
        review_only_evidence = _review("ACCEPTED_DONE")
        review_only_evidence["ledger_updates"] = []
        try:
            apply_supervisor_review(done_missing_evidence_workspace, done_missing_evidence_run["run_id"], review_only_evidence)
            errors.append("ACCEPTED_DONE should fail when only review has evidence and ledger actual_evidence is empty")
        except WorkspaceError:
            pass

        done_workspace = _write_workspace(root / "done", completed=True)
        done_runner = FakeRunner()
        done_run = start_worker_turn(done_workspace, "final", runner=done_runner)
        bad_done = _review("ACCEPTED_DONE", gaps=["still missing"])
        try:
            apply_supervisor_review(done_workspace, done_run["run_id"], bad_done)
            errors.append("ACCEPTED_DONE with remaining gaps should fail")
        except WorkspaceError:
            pass
        done = _review("ACCEPTED_DONE")
        state = apply_supervisor_review(done_workspace, done_run["run_id"], done)
        if state.get("status") != "accepted_done":
            errors.append("ACCEPTED_DONE review did not update state")
        run_artifact = read_json(done_workspace / "runs" / done_run["run_id"] / "run.json")
        if run_artifact.get("outcome") != "accepted_done":
            errors.append("accepted run artifact outcome not updated")
        status = status_workspace(done_workspace)
        if status.get("status") != "accepted_done" or status.get("remaining_gaps"):
            errors.append("status_workspace did not report clean accepted_done")
    return errors


def validate_path(path: Path) -> list[str]:
    if not path:
        return ["path is required unless --fixtures is set"]
    path = path.expanduser().resolve()
    if path.is_dir() and (path / "goal.yaml").exists():
        try:
            validate_workspace(path)
        except WorkspaceError as exc:
            return [str(exc)]
        return []
    if path.is_dir() and (path / "run.json").exists():
        return validate_artifact(path / "run.json", RUN_SCHEMA)
    if path.name == "run.json":
        return validate_artifact(path, RUN_SCHEMA)
    if path.name == "supervisor_review.json":
        return validate_artifact(path, SUPERVISOR_REVIEW_SCHEMA)
    return [f"unsupported validation path: {path}"]
