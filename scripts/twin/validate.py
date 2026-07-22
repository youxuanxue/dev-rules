from __future__ import annotations

import builtins
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from . import contracts, runtime as twin_runtime, supervisor_review, util, worker as twin_worker
from .bootstrap import draft_from_files, draft_workspace, slugify_goal, write_workspace_draft
from .claude_runner import ClaudeRunResult
from .contracts import (
    ACTIVE_WORKSPACE_ENV,
    GOAL_FILE,
    GOAL_SCHEMA,
    PLAN_SCHEMA,
    PERSONAS_DIR,
    RESEARCH_FILE,
    RESEARCH_SCHEMA,
    RUN_SCHEMA,
    SUPERVISOR_REVIEW_SCHEMA,
    SUPERVISOR_STATE_SCHEMA,
    WORKER_PERSONA_PATH,
)
from .loop_harness import run_supervisor_loop_harness
from .driver import (
    handoff_supervisor_route,
    run_driver,
    submit_instruction,
    submit_review,
    workspace_driver_lock,
)
from .plan import acceptance_evidence, plan_gaps, validate_bootstrap_plan_constraints, validate_plan_semantics
from .research import load_research
from .runtime import (
    apply_supervisor_review,
    build_review_context,
    build_supervisor_context,
    continuation_action,
    record_human_response,
    start_worker_turn,
    status_workspace,
    validate_workspace,
)
from .schema_contract import validate_artifact, validate_schema
from .util import read_json, write_json, write_yaml_like
from .claude_runner import DEFAULT_WORKER_TIMEOUT_SECONDS, WORKER_TIMEOUT_ENV, default_worker_timeout_seconds, detect_session_lost, is_body_guard_rejection, run_claude_headless
from .worker import DEFAULT_WORKER_MAX_BUDGET_USD, assess_run_quality, changed_files_from_status, default_worker_max_budget_usd, should_clear_worker_session
from .local_cli import _run_process, build_local_cli_command, parse_local_cli_output
from .worker_backend import CaoWorkerBackend, LocalCliWorkerBackend, _RejectCaoRedirects
from .workspace import WorkspaceError, load_plan, load_state, status_summary, write_plan, write_state


class FakeRunner:
    _twin_allow_shared_checkout_for_tests = True

    def __init__(
        self,
        *,
        session_lost_once: bool = False,
        warning_only_once: bool = False,
        body_guard_once: bool = False,
        returncode: int = 0,
        output_text: str | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.session_lost_once = session_lost_once
        self.warning_only_once = warning_only_once
        self.body_guard_once = body_guard_once
        self.returncode = returncode
        self.output_text = output_text or "Summary:\n- fixture worker ran\n\nEvidence:\n- tests: fixture pass\n\nRemaining:\n- none"

    def __call__(self, prompt: str, **kwargs: Any) -> ClaudeRunResult:
        stream_output_path = kwargs.get("stream_output_path")
        if isinstance(stream_output_path, Path):
            stream_output_path.parent.mkdir(parents=True, exist_ok=True)
            stream_output_path.write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"fixture streamed"}]}}\n', encoding="utf-8")
        self.calls.append({"prompt": prompt, **kwargs})
        requested_session = str(kwargs.get("session_id") or "")
        if self.session_lost_once and requested_session:
            self.session_lost_once = False
            return ClaudeRunResult(session_id=requested_session, output_text="", returncode=0, raw_events=[], session_lost=True)
        if self.body_guard_once and requested_session:
            self.body_guard_once = False
            # Real production rejections emit only an error result event (no model turn),
            # so detect_session_lost() returns True. Mirror that here so the fixture exercises
            # the realistic session_lost retry path rather than the defensive elif branch.
            return ClaudeRunResult(
                session_id=requested_session,
                output_text="Request body 10062361 bytes exceeded TokenKey pre-flight limit",
                returncode=1,
                raw_events=[{"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "request too large"}],
                session_lost=True,
            )
        if self.warning_only_once and requested_session:
            self.warning_only_once = False
            return ClaudeRunResult(
                session_id=requested_session,
                output_text="Warning: no stdin data received in 3s, proceeding without it...",
                returncode=0,
                raw_events=[],
            )
        session = requested_session or "worker-session-1"
        return ClaudeRunResult(
            session_id=session,
            output_text=self.output_text,
            returncode=self.returncode,
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
        "non_goals": ["不测试旧命令兼容路径"],
    }


def _research() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "read_only",
        "question": "如何交付 fixture 目标？",
        "facts": [
            {"claim": "fixture 使用 twin workspace 契约", "source": "scripts/twin/validate.py", "confidence": "high"}
        ],
        "options": [
            {"name": "bounded plan", "summary": "拆成短交付", "tradeoffs": ["需要逐项验收"]}
        ],
        "risks": ["研究结论可能过期"],
        "unknowns": [],
        "recommended_direction": "由 supervisor 把 repo facts 转成 AC 和 bounded plan。",
    }


def _write_research(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / RESEARCH_FILE
    write_yaml_like(target, _research())
    return target


def _plan(*, completed: bool = False) -> dict[str, Any]:
    evidence = ["tests: fixture pass", "diff: fixture stat"] if completed else []
    return {
        "schema_version": 1,
        "goal_id": "fixture-goal",
        "items": [
            {
                "id": "F1",
                "deliverable": "完成 fixture 主流程测试证据",
                "scope": "只覆盖 greenfield worker turn，不扩展旧命令兼容路径",
                "covers_ac": ["AC1"],
                "evidence_plan": [
                    "证据预算：只跑 fixture worker turn 测试，不跑全量 preflight",
                    "停止条件：产出测试证据后转 review",
                ],
                "actual_evidence": [entry for entry in evidence if entry.startswith("tests:")],
                "depends_on": [],
                "status": "completed" if completed else "pending",
                "next_action": "" if completed else "运行 worker fixture 并在测试证据产出后转 review",
            },
            {
                "id": "F2",
                "deliverable": "完成 fixture diff 证据",
                "scope": "仅记录 fixture diff 摘要，不处理相邻代码质量问题",
                "covers_ac": ["AC2"],
                "evidence_plan": [
                    "证据预算：只收集一条 diff summary，不跑额外测试",
                    "停止条件：diff 证据写入后转 review",
                ],
                "actual_evidence": [entry for entry in evidence if entry.startswith("diff:")],
                "depends_on": ["F1"],
                "status": "completed" if completed else "pending",
                "next_action": "" if completed else "在 F1 完成后记录 diff 证据并转 review",
            },
        ],
    }


def _review(status: str, *, gaps: list[str] | None = None, question: str | None = None, actions: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "summary": "fixture supervisor review",
        "next_instruction": "继续 fixture" if status == "continue" else "",
        "remaining_gaps": gaps or [],
        "acceptance_evidence": [
            {"ac_id": "AC1", "evidence": ["tests: fixture pass"]},
            {"ac_id": "AC2", "evidence": ["diff: fixture stat"]},
        ],
        "risk_flags": [],
        "actions": actions or [],
        "plan_updates": [
            {"item_id": "F1", "status": "completed", "actual_evidence": ["tests: fixture pass"], "next_action": ""},
            {"item_id": "F2", "status": "completed", "actual_evidence": ["diff: fixture stat"], "next_action": ""},
        ],
        "human_question": question,
    }


def _write_workspace(root: Path, *, completed: bool = False, plan_evidence: bool | None = None) -> Path:
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
  - 不测试旧命令兼容路径
""",
        encoding="utf-8",
    )
    status = "completed" if completed else "pending"
    has_evidence = completed if plan_evidence is None else plan_evidence
    evidence_f1 = '\n      - "tests: fixture pass"' if has_evidence else " []"
    evidence_f2 = '\n      - "diff: fixture stat"' if has_evidence else " []"
    next_action_f1 = '""' if completed else "运行 worker fixture 并在测试证据产出后转 review"
    next_action_f2 = '""' if completed else "在 F1 完成后记录 diff 证据并转 review"
    (workspace / "plan.yaml").write_text(
        f"""schema_version: 1
goal_id: fixture-goal
items:
  - id: F1
    deliverable: 完成 fixture 主流程测试证据
    scope: 只覆盖 greenfield worker turn，不扩展旧命令兼容路径
    covers_ac:
      - AC1
    evidence_plan:
      - 证据预算：只跑 fixture worker turn 测试，不跑全量 preflight
      - 停止条件：产出测试证据后转 review
    actual_evidence:{evidence_f1}
    depends_on: []
    status: {status}
    next_action: {next_action_f1}
  - id: F2
    deliverable: 完成 fixture diff 证据
    scope: 仅记录 fixture diff 摘要，不处理相邻代码质量问题
    covers_ac:
      - AC2
    evidence_plan:
      - 证据预算：只收集一条 diff summary，不跑额外测试
      - 停止条件：diff 证据写入后转 review
    actual_evidence:{evidence_f2}
    depends_on:
      - F1
    status: {status}
    next_action: {next_action_f2}
""",
        encoding="utf-8",
    )
    return workspace


def _schema_errors() -> list[str]:
    errors: list[str] = []
    for stale_name in ("TERMINAL_STATUSES", "DECISIONS"):
        if hasattr(contracts, stale_name):
            errors.append(f"stale contract constant should not be public: {stale_name}")
    for stale_name in ("normalize_timestamp", "date_range"):
        if hasattr(util, stale_name):
            errors.append(f"stale util helper should not be public: {stale_name}")
    for value, schema in [
        (_goal(), GOAL_SCHEMA),
        (_plan(), PLAN_SCHEMA),
        (_research(), RESEARCH_SCHEMA),
        (_review("continue", actions=["fix_drift", "validate_more", "mark_plan_gap"]), SUPERVISOR_REVIEW_SCHEMA),
    ]:
        errors.extend(validate_schema(value, schema))
    old_goal = {**_goal(), "project_root": "/tmp/project", "allowed_tools": {}}
    if not validate_schema(old_goal, GOAL_SCHEMA):
        errors.append("old goal fields should be rejected")
    old_review = {**_review("continue"), "supervisor_session_id": "legacy"}
    if not validate_schema(old_review, SUPERVISOR_REVIEW_SCHEMA):
        errors.append("old supervisor review fields should be rejected")
    legacy_action_review = {**_review("continue"), "actions": ["reset_worker_session"]}
    if not validate_schema(legacy_action_review, SUPERVISOR_REVIEW_SCHEMA):
        errors.append("legacy supervisor actions should be rejected by trimmed enum")
    return errors


def _runtime_reentry_errors(root: Path) -> list[str]:
    errors: list[str] = []
    workspace = _write_workspace(root / "worker-diagnostics")
    state = load_state(workspace)
    state["status"] = "worker_running"
    state["current_run_id"] = "run-active"
    write_state(workspace, state)
    run_dir = workspace / "runs" / "run-active"
    run_dir.mkdir(parents=True)
    write_json(run_dir / "pending.json", {"schema_version": 1, "run_id": "run-active"})
    (run_dir / "events.jsonl").write_text('{"type":"assistant"}\n', encoding="utf-8")
    before_state = load_state(workspace)
    status = status_summary(workspace)
    worker = status.get("display", {}).get("worker", {})
    if worker.get("state") != "active" or worker.get("events_bytes", 0) <= 0:
        errors.append(f"worker_running status should expose active diagnostics: {worker!r}")
    if load_state(workspace) != before_state:
        errors.append("worker_running diagnostics must not mutate state")
    action = continuation_action(workspace)
    if action.get("action") != "watch_worker" or action.get("worker", {}).get("state") != "active":
        errors.append(f"active worker_running continuation should enter watchdog: {action!r}")

    stale_no_run = _write_workspace(root / "worker-stale-no-run")
    stale_no_run_state = load_state(stale_no_run)
    stale_no_run_state["status"] = "worker_running"
    stale_no_run_state["current_run_id"] = None
    write_state(stale_no_run, stale_no_run_state)
    stale_no_run_action = continuation_action(stale_no_run)
    if stale_no_run_action.get("action") != "recover_worker_turn":
        errors.append(f"worker_running without current_run_id should recover: {stale_no_run_action!r}")

    stale = _write_workspace(root / "worker-stale")
    stale_state = load_state(stale)
    stale_state["status"] = "worker_running"
    stale_state["current_run_id"] = "run-missing"
    write_state(stale, stale_state)
    stale_status = status_summary(stale)
    stale_worker = stale_status.get("display", {}).get("worker", {})
    if stale_worker.get("state") != "stale_no_artifacts":
        errors.append(f"stale worker_running should be diagnosed without repair: {stale_worker!r}")
    if load_state(stale).get("status") != "worker_running":
        errors.append("stale status diagnostics should not repair worker_running")
    stale_action = continuation_action(stale)
    if stale_action.get("action") != "recover_worker_turn":
        errors.append(f"stale worker_running continuation should recover: {stale_action!r}")

    completed_artifact = _write_workspace(root / "worker-completed-artifact")
    completed_state = load_state(completed_artifact)
    completed_state["status"] = "worker_running"
    completed_state["current_run_id"] = "run-done"
    write_state(completed_artifact, completed_state)
    completed_dir = completed_artifact / "runs" / "run-done"
    completed_dir.mkdir(parents=True)
    write_json(completed_dir / "run.json", {"schema_version": 1, "run_id": "run-done", "status": "review_required"})
    completed_worker = status_summary(completed_artifact).get("display", {}).get("worker", {})
    if completed_worker.get("state") != "completed_artifact_present":
        errors.append(f"completed artifact should be surfaced for reentry: {completed_worker!r}")
    completed_action = continuation_action(completed_artifact)
    if completed_action.get("action") != "review_run":
        errors.append(f"completed worker artifact should route to review: {completed_action!r}")

    quiet = _write_workspace(root / "worker-quiet")
    quiet_state = load_state(quiet)
    quiet_state["status"] = "worker_running"
    quiet_state["current_run_id"] = "run-quiet"
    write_state(quiet, quiet_state)
    quiet_dir = quiet / "runs" / "run-quiet"
    quiet_dir.mkdir(parents=True)
    write_json(quiet_dir / "pending.json", {"schema_version": 1, "run_id": "run-quiet"})
    old_time = 1
    os.utime(quiet_dir / "pending.json", (old_time, old_time))
    quiet_action = continuation_action(quiet)
    if quiet_action.get("action") != "watch_worker" or quiet_action.get("worker", {}).get("state") != "quiet":
        errors.append(f"quiet worker should route to bounded watchdog: {quiet_action!r}")

    for expected_status, expected_action in [
        ("idle", "supervisor_instruction"),
        ("continue", "worker_turn"),
        ("review_required", "review_run"),
        ("needs_human", "ask_human"),
        ("accepted_done", "done"),
        ("failed", "failed"),
    ]:
        action_ws = _write_workspace(root / f"next-{expected_status}")
        action_state = load_state(action_ws)
        action_state["status"] = expected_status
        if expected_status in {"continue", "review_required"}:
            action_state["next_instruction"] = "继续 fixture"
            action_state["current_run_id"] = "run-action"
        if expected_status == "needs_human":
            action_state["needs_human"] = {"question": "继续吗？", "context": "fixture", "created_at": "2026-01-01T00:00:00Z"}
        write_state(action_ws, action_state)
        action = continuation_action(action_ws)
        if action.get("action") != expected_action:
            errors.append(f"{expected_status} continuation action should be {expected_action}: {action!r}")

    respond_ws = _write_workspace(root / "next-after-human-response")
    respond_state = load_state(respond_ws)
    respond_state["status"] = "needs_human"
    respond_state["needs_human"] = {
        "question": "继续吗？",
        "context": "fixture",
        "created_at": "2026-01-01T00:00:00Z",
    }
    write_state(respond_ws, respond_state)
    record_human_response(respond_ws, "继续")
    respond_action = continuation_action(respond_ws)
    if respond_action.get("action") != "supervisor_instruction":
        errors.append(f"human response should reenter the supervisor before another worker turn: {respond_action!r}")
    respond_context = build_supervisor_context(respond_ws)
    if respond_context.get("human_response", {}).get("text") != "继续":
        errors.append("supervisor reentry should receive the unconsumed human response artifact")

    review_ws = _write_workspace(root / "loop-review-required")
    review_run = start_worker_turn(review_ws, "生成待 review run", runner=FakeRunner())
    if load_state(review_ws).get("status") != "review_required":
        errors.append("fixture worker should leave review_required before loop resume")
    resumed = run_supervisor_loop_harness(
        review_ws,
        instruction="",
        runner=FakeRunner(),
        max_rounds=2,
        review_fn=lambda _context: _review("accepted_done"),
    )
    if resumed.get("status") != "accepted_done" or resumed.get("runs"):
        errors.append(f"loop harness should resume review_required without starting another run: {resumed!r}, first={review_run['run_id']}")

    running_done_ws = _write_workspace(root / "loop-worker-running-done")
    done_run = start_worker_turn(running_done_ws, "生成 worker_running run artifact", runner=FakeRunner())
    done_state = load_state(running_done_ws)
    done_state["status"] = "worker_running"
    write_state(running_done_ws, done_state)
    running_done = run_supervisor_loop_harness(
        running_done_ws,
        instruction="",
        runner=FakeRunner(),
        max_rounds=2,
        review_fn=lambda _context: _review("accepted_done"),
    )
    if running_done.get("status") != "accepted_done" or running_done.get("runs"):
        errors.append(f"loop harness should review completed worker_running artifact: {running_done!r}, run={done_run['run_id']}")

    stale_loop = _write_workspace(root / "loop-worker-stale")
    stale_loop_state = load_state(stale_loop)
    stale_loop_state["status"] = "worker_running"
    stale_loop_state["current_run_id"] = "run-stale-loop"
    stale_loop_state["next_instruction"] = "恢复 stale worker fixture"
    write_state(stale_loop, stale_loop_state)
    stale_result = run_supervisor_loop_harness(
        stale_loop,
        instruction="",
        runner=FakeRunner(),
        max_rounds=2,
        review_fn=lambda _context: _review("accepted_done"),
    )
    if stale_result.get("status") != "accepted_done" or len(stale_result.get("runs") or []) != 1:
        errors.append(f"loop harness should recover stale worker_running with one fresh run: {stale_result!r}")

    quiet_result = run_supervisor_loop_harness(
        quiet,
        instruction="",
        runner=FakeRunner(),
        max_rounds=1,
        max_wait_seconds=0,
        review_fn=lambda _context: _review("accepted_done"),
    )
    if quiet_result.get("status") != "worker_quiet_timeout":
        errors.append(f"loop harness should return explicit worker_quiet_timeout: {quiet_result!r}")
    return errors


def _driver_protocol_errors(root: Path) -> list[str]:
    errors: list[str] = []
    runner = FakeRunner()

    def fake_worker_turn(workspace: Path, instruction: str, **_kwargs: Any) -> dict[str, Any]:
        return start_worker_turn(
            workspace,
            instruction,
            runner=runner,
            driver_authorized=bool(_kwargs.get("driver_authorized")),
        )

    legacy = _write_workspace(root / "driver-legacy")
    legacy_state = load_state(legacy)
    for key in ("state_revision", "supervisor_route", "pending_action"):
        legacy_state.pop(key, None)
    write_json(legacy / "supervisor_state.json", legacy_state)
    normalized = load_state(legacy)
    if normalized.get("state_revision") != 0 or normalized.get("supervisor_route") is not None:
        errors.append("legacy supervisor state should normalize additively in memory")
    if "state_revision" in read_json(legacy / "supervisor_state.json"):
        errors.append("legacy supervisor state should not mutate during a read")

    first = run_driver(legacy, "host/codex", worker_turn_fn=fake_worker_turn)
    repeated = run_driver(legacy, "host/codex", worker_turn_fn=fake_worker_turn)
    if first.get("action") != "supervisor_instruction":
        errors.append(f"Codex host should first request a supervisor instruction: {first!r}")
        return errors
    if first.get("action_token") != repeated.get("action_token"):
        errors.append("repeated twin run should return the same pending action token")
    stored = load_state(legacy)
    if stored.get("supervisor_route") != "host/codex" or not isinstance(stored.get("state_revision"), int):
        errors.append("legacy workspace should lazily bind route and revision on first universal run")
    if first.get("state_revision") != stored.get("state_revision"):
        errors.append("driver action revision should match persisted state revision")
    submit_command = first.get("submit", {}).get("command", "")
    if "twin submit-instruction" not in submit_command or "--instruction-file -" not in submit_command:
        errors.append("instruction action should expose an exact stdin submit command")
    try:
        start_worker_turn(legacy, "bypass pending instruction", runner=runner)
        errors.append("low-level worker-turn must not bypass a pending supervisor action")
    except WorkspaceError:
        pass

    revision = int(first["state_revision"])
    token = str(first["action_token"])
    try:
        submit_instruction(
            legacy,
            "host/codex",
            state_revision=revision + 1,
            action_token=token,
            instruction="stale instruction",
        )
        errors.append("stale instruction revision should be rejected")
    except WorkspaceError:
        pass
    try:
        submit_instruction(
            legacy,
            "host/claude",
            state_revision=revision,
            action_token=token,
            instruction="wrong route",
        )
        errors.append("supervisor route drift should be rejected")
    except WorkspaceError:
        pass
    try:
        submit_instruction(
            legacy,
            "host/codex",
            state_revision=revision,
            action_token="wrong-token",
            instruction="wrong token",
        )
        errors.append("wrong instruction token should be rejected")
    except WorkspaceError:
        pass
    try:
        submit_review(
            legacy,
            "host/codex",
            state_revision=revision,
            action_token=token,
            run_id="run-wrong-action",
            review=_review("continue"),
        )
        errors.append("review submission against an instruction action should be rejected")
    except WorkspaceError:
        pass

    other = _write_workspace(root / "driver-wrong-workspace")
    other_action = run_driver(other, "host/codex", worker_turn_fn=fake_worker_turn)
    try:
        submit_instruction(
            other,
            "host/codex",
            state_revision=int(other_action["state_revision"]),
            action_token=token,
            instruction="cross workspace",
        )
        errors.append("cross-workspace action token should be rejected")
    except WorkspaceError:
        pass

    handoff_workspace = _write_workspace(root / "driver-handoff")
    handoff_action = run_driver(handoff_workspace, "host/codex", worker_turn_fn=fake_worker_turn)
    try:
        handoff_supervisor_route(handoff_workspace, "host/claude")
        errors.append("supervisor handoff must reject a workspace with a pending action")
    except WorkspaceError:
        pass
    submit_instruction(
        handoff_workspace,
        "host/codex",
        state_revision=int(handoff_action["state_revision"]),
        action_token=str(handoff_action["action_token"]),
        instruction="prepare handoff fixture",
    )
    before_handoff = load_state(handoff_workspace)
    try:
        start_worker_turn(handoff_workspace, "bypass route-bound driver", runner=runner)
        errors.append("route-bound worker-turn must fail closed without driver authorization")
    except WorkspaceError:
        pass
    handed_off = handoff_supervisor_route(handoff_workspace, "host/claude")
    handed_off_state = load_state(handoff_workspace)
    if handed_off.get("action") != "supervisor_route_handoff":
        errors.append(f"explicit supervisor handoff should report the transition: {handed_off!r}")
    if handed_off_state.get("supervisor_route") != "host/claude":
        errors.append("explicit supervisor handoff should persist the new route")
    if int(handed_off_state.get("state_revision") or 0) <= int(before_handoff.get("state_revision") or 0):
        errors.append("explicit supervisor handoff should advance the state revision")
    handoff_events = (handoff_workspace / "workspace_events.jsonl").read_text(encoding="utf-8")
    if '"event": "supervisor_route_handoff"' not in handoff_events:
        errors.append("explicit supervisor handoff should append an audit event")
    unchanged = handoff_supervisor_route(handoff_workspace, "host/claude")
    if unchanged.get("action") != "supervisor_route_unchanged":
        errors.append("same-route supervisor handoff should be idempotent")
    if load_state(handoff_workspace).get("state_revision") != handed_off_state.get("state_revision"):
        errors.append("same-route supervisor handoff should not advance the revision")
    try:
        run_driver(handoff_workspace, "host/codex", worker_turn_fn=fake_worker_turn)
        errors.append("the previous supervisor route must be rejected after handoff")
    except WorkspaceError:
        pass
    try:
        submit_instruction(
            handoff_workspace,
            "host/codex",
            state_revision=int(handoff_action["state_revision"]),
            action_token=str(handoff_action["action_token"]),
            instruction="stale route and token",
        )
        errors.append("the previous supervisor route and token must stay invalid after handoff")
    except WorkspaceError:
        pass
    post_handoff_review = run_driver(
        handoff_workspace,
        "host/claude",
        worker_turn_fn=fake_worker_turn,
    )
    if post_handoff_review.get("action") != "review_run":
        errors.append("the new supervisor route should resume the existing workspace")

    submitted = submit_instruction(
        legacy,
        "host/codex",
        state_revision=revision,
        action_token=token,
        instruction="实现 Codex host fixture",
    )
    if submitted.get("status") != "continue" or load_state(legacy).get("pending_action") is not None:
        errors.append("instruction submission should atomically clear pending action and continue")
    try:
        submit_instruction(
            legacy,
            "host/codex",
            state_revision=revision,
            action_token=token,
            instruction="duplicate",
        )
        errors.append("duplicate instruction token should be rejected")
    except WorkspaceError:
        pass
    event_text = (legacy / "workspace_events.jsonl").read_text(encoding="utf-8")
    if "实现 Codex host fixture" in event_text:
        errors.append("supervisor instruction event must not persist instruction text")

    review_action = run_driver(legacy, "host/codex", worker_turn_fn=fake_worker_turn)
    if review_action.get("action") != "review_run" or not review_action.get("run_id"):
        errors.append(f"Codex host should auto-run worker then request review: {review_action!r}")
        return errors
    review_revision = int(review_action["state_revision"])
    review_token = str(review_action["action_token"])
    run_id = str(review_action["run_id"])
    if "twin submit-review" not in review_action.get("submit", {}).get("command", ""):
        errors.append("review action should expose an exact submit command")
    try:
        apply_supervisor_review(legacy, run_id, _review("continue"))
        errors.append("low-level review must not bypass a pending review token")
    except WorkspaceError:
        pass
    try:
        submit_review(
            legacy,
            "host/codex",
            state_revision=review_revision,
            action_token=review_token,
            run_id="run-wrong",
            review=_review("continue"),
        )
        errors.append("review submission for the wrong run should be rejected")
    except WorkspaceError:
        pass
    try:
        submit_instruction(
            legacy,
            "host/codex",
            state_revision=review_revision,
            action_token=review_token,
            instruction="wrong action",
        )
        errors.append("instruction submission against a review action should be rejected")
    except WorkspaceError:
        pass

    submit_review(
        legacy,
        "host/codex",
        state_revision=review_revision,
        action_token=review_token,
        run_id=run_id,
        review=_review("continue"),
    )
    try:
        apply_supervisor_review(legacy, run_id, _review("continue"))
        errors.append("route-bound low-level review must fail closed without a pending action")
    except WorkspaceError:
        pass
    try:
        submit_review(
            legacy,
            "host/codex",
            state_revision=review_revision,
            action_token=review_token,
            run_id=run_id,
            review=_review("continue"),
        )
        errors.append("duplicate review token should be rejected")
    except WorkspaceError:
        pass
    final_review_action = run_driver(legacy, "host/codex", worker_turn_fn=fake_worker_turn)
    final_review = _review("accepted_done")
    submit_review(
        legacy,
        "host/codex",
        state_revision=int(final_review_action["state_revision"]),
        action_token=str(final_review_action["action_token"]),
        run_id=str(final_review_action["run_id"]),
        review=final_review,
    )
    terminal = run_driver(legacy, "host/codex", worker_turn_fn=fake_worker_turn)
    if terminal.get("action") != "done" or terminal.get("status") != "accepted_done":
        errors.append("Codex host protocol should reach accepted_done end to end")
    if terminal.get("resume_command"):
        errors.append("terminal driver actions must not expose a contradictory resume command")

    needs_workspace = _write_workspace(root / "driver-needs-human")
    needs_action = run_driver(needs_workspace, "host/codex", worker_turn_fn=fake_worker_turn)
    submit_instruction(
        needs_workspace,
        "host/codex",
        state_revision=int(needs_action["state_revision"]),
        action_token=str(needs_action["action_token"]),
        instruction="run needs_human fixture",
    )
    needs_review_action = run_driver(needs_workspace, "host/codex", worker_turn_fn=fake_worker_turn)
    submit_review(
        needs_workspace,
        "host/codex",
        state_revision=int(needs_review_action["state_revision"]),
        action_token=str(needs_review_action["action_token"]),
        run_id=str(needs_review_action["run_id"]),
        review=_review("needs_human", question="请确认 driver fixture"),
    )
    human_gate = run_driver(needs_workspace, "host/codex", worker_turn_fn=fake_worker_turn)
    if human_gate.get("action") != "ask_human":
        errors.append("driver should stop identically at needs_human")
    record_human_response(needs_workspace, "继续")
    resumed_action = run_driver(needs_workspace, "host/codex", worker_turn_fn=fake_worker_turn)
    if resumed_action.get("action") != "supervisor_instruction":
        errors.append("driver should resume with supervisor instruction after twin respond")

    lock_workspace = _write_workspace(root / "driver-lock")
    lock_holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                "from scripts.twin.driver import workspace_driver_lock; "
                "lock=workspace_driver_lock(Path(sys.argv[1])); lock.__enter__(); "
                "print('locked', flush=True); sys.stdin.read(1); lock.__exit__(None, None, None)"
            ),
            str(lock_workspace),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert lock_holder.stdout is not None
    assert lock_holder.stdin is not None
    if lock_holder.stdout.readline().strip() != "locked":
        errors.append("driver lock fixture failed to acquire child-process lock")
    else:
        try:
            with workspace_driver_lock(lock_workspace):
                pass
            errors.append("concurrent twin driver acquisition should fail closed")
        except WorkspaceError:
            pass
    lock_holder.stdin.write("x")
    lock_holder.stdin.flush()
    lock_holder.wait(timeout=10)

    launcher = Path(__file__).resolve().parents[2] / "global" / "bin" / "twin"
    launcher_env = {**os.environ, ACTIVE_WORKSPACE_ENV: str(root / "driver-launcher-active")}
    launcher_result = subprocess.run(
        [str(launcher), "status", str(needs_workspace), "--json"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        env=launcher_env,
    )
    if launcher_result.returncode != 0 or str(needs_workspace) not in launcher_result.stdout:
        errors.append(f"real twin launcher smoke failed: {launcher_result.stderr.strip()}")

    scaffold_workspace = root / "driver-launcher-scaffold"
    scaffold_result = subprocess.run(
        [
            str(launcher),
            "scaffold",
            "真实 launcher scaffold fixture",
            "--workspace",
            str(scaffold_workspace),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        env=launcher_env,
    )
    if scaffold_result.returncode != 0:
        errors.append(f"real twin scaffold failed: {scaffold_result.stderr.strip()}")
    elif not (scaffold_workspace / "goal.yaml").is_file() or not (scaffold_workspace / "plan.yaml").is_file():
        errors.append("real twin scaffold should create editable goal.yaml and plan.yaml files")
    else:
        scaffold_status = subprocess.run(
            [str(launcher), "status", str(scaffold_workspace), "--json"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=30,
            env=launcher_env,
        )
        if scaffold_status.returncode != 0:
            errors.append(f"scaffolded workspace should pass twin status: {scaffold_status.stderr.strip()}")
        scaffold_run = subprocess.run(
            [
                str(launcher),
                "run",
                str(scaffold_workspace),
                "--supervisor",
                "host/codex",
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=30,
            env=launcher_env,
        )
        try:
            scaffold_action = json.loads(scaffold_run.stdout)
        except json.JSONDecodeError:
            scaffold_action = {}
        if scaffold_run.returncode != 0 or scaffold_action.get("action") != "supervisor_instruction":
            errors.append(f"scaffolded workspace should enter twin run: {scaffold_run.stderr.strip()}")

    help_result = subprocess.run(
        [str(launcher), "--help"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        env=launcher_env,
    )
    help_lines = help_result.stdout.splitlines()
    for visible_command in ("run", "handoff", "status", "respond", "submit-instruction"):
        if not any(line.startswith(f"    {visible_command}") for line in help_lines):
            errors.append(f"twin help should expose {visible_command!r}")
    for internal_command in ("next", "watch", "supervisor-context", "worker-turn", "review-context", "validate"):
        if any(line.startswith(f"    {internal_command}") for line in help_lines):
            errors.append(f"twin help must hide internal command {internal_command!r}")
    from scripts.export_agent_contract import _twin_cli_rows

    contract_commands = {row[0] for row in _twin_cli_rows()}
    expected_contract_commands = {
        "twin bootstrap",
        "twin doctor",
        "twin handoff",
        "twin respond",
        "twin run",
        "twin scaffold",
        "twin status",
        "twin submit-instruction",
        "twin submit-review",
    }
    if contract_commands != expected_contract_commands:
        errors.append(
            "generated Agent contract command surface drifted: "
            f"expected={sorted(expected_contract_commands)!r} actual={sorted(contract_commands)!r}"
        )
    return errors


def _worktree_cleanup_errors(root: Path) -> list[str]:
    errors: list[str] = []
    workspace = _write_workspace(root / "cleanup-evidence")
    target = root / "fixture-worktree"
    target.mkdir()
    original_path = supervisor_review.worktree_path
    original_remove = supervisor_review.remove_worktree
    supervisor_review.worktree_path = lambda _repo, _workspace: target
    try:
        supervisor_review.remove_worktree = lambda _repo, _workspace: False
        supervisor_review._cleanup_terminal_worktree(workspace)
        events = [
            json.loads(line)
            for line in (workspace / "workspace_events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not events or events[-1].get("outcome") != "preserved":
            errors.append("terminal cleanup should record preserved worktrees with unsaved changes")

        def fail_cleanup(_repo: Path, _workspace: Path) -> bool:
            raise supervisor_review.WorktreeIsolationError("fixture cleanup failure")

        supervisor_review.remove_worktree = fail_cleanup
        supervisor_review._cleanup_terminal_worktree(workspace)
        failed_event = json.loads(
            (workspace / "workspace_events.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        )
        if failed_event.get("outcome") != "failed" or "fixture cleanup failure" not in failed_event.get("error", ""):
            errors.append("terminal cleanup failures should remain nonfatal and observable in workspace evidence")
    finally:
        supervisor_review.worktree_path = original_path
        supervisor_review.remove_worktree = original_remove
    return errors


def _behavior_helper_errors() -> list[str]:
    errors: list[str] = []
    if slugify_goal("纯中文目标") != "twin-goal-fc22c137e7":
        errors.append("Chinese-only goal slugs must be stable across Python processes")
    changed = changed_files_from_status(
        " M docs/agent_integration.md\n"
        " M pyproject.toml\n"
        " M tests/test_entry_runtimes.py\n"
        "?? .wtree-session.json\n"
        "?? zw_brain/shared/iaf_oidc.py\n"
        "R  old/path.py -> new/path.py\n"
    )
    expected = [
        "docs/agent_integration.md",
        "pyproject.toml",
        "tests/test_entry_runtimes.py",
        "zw_brain/shared/iaf_oidc.py",
        "new/path.py",
    ]
    if changed != expected:
        errors.append(f"changed_files_from_status should preserve paths: {changed!r}")
    first_unstaged = changed_files_from_status(" M scripts/preflight_common.sh\n")[0]
    if first_unstaged != "scripts/preflight_common.sh":
        errors.append(f"changed_files_from_status should preserve first unstaged path: {first_unstaged!r}")
    non_git_status = "fatal: not a git repository (or any of the parent directories): .git\n"
    if changed_files_from_status(non_git_status):
        errors.append("git diagnostics must not be misreported as changed files")
    # A resume with no real model turn is a lost session regardless of exit
    # code: a body-guard / oversized-request rejection emits only an error
    # event, and re-resuming it would replay the doomed 10MB request forever.
    # An error result must not count as a live turn, only a genuine one.
    text_turn = [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "real worker turn"}]}}]
    error_result = [{"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "API Error: request too large"}]
    if not detect_session_lost(requested_session="s1", parsed_session="s1", events=[]):
        errors.append("resume with no events should be session_lost (silent rejection)")
    if not detect_session_lost(requested_session="s1", parsed_session="s1", events=error_result):
        errors.append("resume whose only output is an error result should be session_lost (body-guard rejection)")
    if detect_session_lost(requested_session="s1", parsed_session="s1", events=text_turn):
        errors.append("resume with a real model turn should not be session_lost")
    if detect_session_lost(requested_session="s1", parsed_session="s1", events=[{"type": "result", "subtype": "success", "result": "done"}]):
        errors.append("resume with a success result should not be session_lost")
    if not detect_session_lost(requested_session="s1", parsed_session="s2", events=text_turn):
        errors.append("resume that forked into a new session id should be session_lost")
    if detect_session_lost(requested_session="", parsed_session="", events=[]):
        errors.append("fresh run (no requested session) should never be session_lost")
    if not is_body_guard_rejection("Request body 10062361 bytes exceeded TokenKey pre-flight limit", []):
        errors.append("body-guard rejection text should be detected (empty-events fallback)")
    if is_body_guard_rejection("real worker turn completed", []):
        errors.append("normal worker output should not look like body-guard rejection")
    if not is_body_guard_rejection(
        "request too large",
        [{"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "request too large"}],
    ):
        errors.append("error event carrying body-guard text should be detected")
    worker_content_event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "讨论 body-guard / request too large 时的兜底逻辑"}]},
    }
    if is_body_guard_rejection(
        "讨论 body-guard / request too large 时的兜底逻辑",
        [{"type": "system", "session_id": "abc"}, worker_content_event],
    ):
        errors.append("worker content mentioning body-guard text must not be flagged as a rejection")
    no_progress_flags = assess_run_quality(
        worker_output="继续推进但没有任何可验收的 diff 或运行结果",
        validation=[],
        returncode=0,
        session_lost=False,
        resume_used=False,
        pre_git_status="",
        post_git_status="",
        pre_git_diff_stat="",
        post_git_diff_stat="",
    )
    if "NO_PROGRESS_DETECTED" not in no_progress_flags:
        errors.append("NO_PROGRESS should not require resume_used")
    if not should_clear_worker_session(
        result=ClaudeRunResult(session_id="doomed", output_text="", returncode=1, raw_events=[], session_lost=True),
        quality_flags=["SESSION_LOST"],
    ):
        errors.append("session_lost should clear persisted worker session")
    if not should_clear_worker_session(
        result=ClaudeRunResult(session_id="fresh-retry", output_text="done", returncode=0, raw_events=[], session_lost=False),
        quality_flags=["BODY_GUARD_REJECTION", "WORKER_SESSION_RESET"],
    ):
        errors.append("BODY_GUARD_REJECTION in quality_flags (after a successful retry) should still clear the persisted session")
    if should_clear_worker_session(
        result=ClaudeRunResult(session_id="keep-fresh", output_text="done", returncode=0, raw_events=[], session_lost=False),
        quality_flags=["SESSION_LOST", "WORKER_SESSION_RESET"],
    ):
        errors.append("plain SESSION_LOST (no body-guard) on a successful retry should keep the fresh session id")
    if should_clear_worker_session(
        result=ClaudeRunResult(session_id="keep-me", output_text="done", returncode=0, raw_events=[]),
        quality_flags=["VALIDATION_NOT_REPORTED"],
    ):
        errors.append("validation-only weak flags should not clear worker session by default")
    weak_flags = assess_run_quality(
        worker_output="我会继续收敛 F6/AC1",
        validation=[],
        returncode=0,
        session_lost=False,
        resume_used=True,
        pre_git_status="",
        post_git_status="",
        pre_git_diff_stat="",
        post_git_diff_stat="",
    )
    for flag in ("VALIDATION_NOT_REPORTED", "WORKER_OUTPUT_WEAK", "NO_PROGRESS_DETECTED"):
        if flag not in weak_flags:
            errors.append(f"weak resumed run should flag {flag}")
    warning_flags = assess_run_quality(
        worker_output="Warning: no stdin data received in 3s, proceeding without it...",
        validation=[],
        returncode=0,
        session_lost=False,
        resume_used=True,
        pre_git_status="",
        post_git_status="",
        pre_git_diff_stat="",
        post_git_diff_stat="",
    )
    for flag in ("STDIN_WARNING", "WORKER_OUTPUT_EMPTY_OR_WARNING_ONLY"):
        if flag not in warning_flags:
            errors.append(f"warning-only run should flag {flag}")
    budget_flags = assess_run_quality(
        worker_output="partial",
        validation=[],
        returncode=1,
        session_lost=False,
        resume_used=True,
        pre_git_status="",
        post_git_status=" M file.py",
        pre_git_diff_stat="",
        post_git_diff_stat=" file.py | 1 +",
        raw_events=[{"type": "result", "subtype": "error_max_budget_usd", "is_error": True}],
    )
    if "WORKER_MAX_BUDGET_EXCEEDED" not in budget_flags:
        errors.append("budget-exceeded run should record WORKER_MAX_BUDGET_EXCEEDED")
    old_budget_env = os.environ.pop("TWIN_WORKER_MAX_BUDGET_USD", None)
    try:
        if DEFAULT_WORKER_MAX_BUDGET_USD != 50.0 or default_worker_max_budget_usd() != 50.0:
            errors.append("default worker budget should be 50 USD")
        os.environ["TWIN_WORKER_MAX_BUDGET_USD"] = "5"
        if default_worker_max_budget_usd() != 5.0:
            errors.append("worker budget env override should be honored")
    finally:
        if old_budget_env is None:
            os.environ.pop("TWIN_WORKER_MAX_BUDGET_USD", None)
        else:
            os.environ["TWIN_WORKER_MAX_BUDGET_USD"] = old_budget_env
    old_timeout_env = os.environ.pop(WORKER_TIMEOUT_ENV, None)
    try:
        if DEFAULT_WORKER_TIMEOUT_SECONDS != 10800 or default_worker_timeout_seconds() != 10800:
            errors.append("default worker timeout should be 10800 seconds")
        os.environ[WORKER_TIMEOUT_ENV] = "120"
        if default_worker_timeout_seconds() != 120:
            errors.append("worker timeout env override should be honored")
        os.environ[WORKER_TIMEOUT_ENV] = "0"
        try:
            default_worker_timeout_seconds()
            errors.append("worker timeout env must reject non-positive values")
        except ValueError:
            pass
    finally:
        if old_timeout_env is None:
            os.environ.pop(WORKER_TIMEOUT_ENV, None)
        else:
            os.environ[WORKER_TIMEOUT_ENV] = old_timeout_env
    return errors


def _worker_backend_errors(root: Path) -> list[str]:
    errors: list[str] = []
    calls: list[tuple[Any, float]] = []

    codex_command = build_local_cli_command("codex", "fixture prompt", cwd=root)
    if codex_command[:3] != ["codex", "exec", "--json"] or "--sandbox" not in codex_command:
        errors.append(f"Codex local CLI command should be explicit and non-interactive: {codex_command!r}")
    if "--ephemeral" in codex_command or "workspace-write" not in codex_command:
        errors.append(f"Codex local CLI command should include isolated sandbox controls: {codex_command!r}")
    resumed_codex = build_local_cli_command("codex", "resume prompt", cwd=root, session_id="thread-1")
    if resumed_codex[:4] != ["codex", "exec", "resume", "thread-1"] or "--ephemeral" in resumed_codex:
        errors.append(f"Codex resume command should use exec resume without fresh-session flags: {resumed_codex!r}")
    if 'sandbox_mode="workspace-write"' not in resumed_codex or 'approval_policy="never"' not in resumed_codex:
        errors.append(f"Codex resume should reassert sandbox and approval policy: {resumed_codex!r}")
    gemini_command = build_local_cli_command("gemini", "fixture prompt", cwd=root)
    if "--sandbox" not in gemini_command or gemini_command[gemini_command.index("--approval-mode") + 1] != "yolo":
        errors.append(f"Gemini local CLI command should combine sandbox isolation with yolo approvals: {gemini_command!r}")

    codex_session, codex_output, codex_events = parse_local_cli_output(
        "codex",
        '{"type":"thread.started","thread_id":"thread-1"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"tests: codex fixture pass"}}\n',
    )
    if codex_session != "thread-1" or codex_output != "tests: codex fixture pass" or len(codex_events) != 2:
        errors.append(f"Codex JSONL should normalize session and assistant output: {codex_session!r}, {codex_output!r}")
    gemini_session, gemini_output, _ = parse_local_cli_output(
        "gemini",
        '{"type":"message","role":"assistant","session_id":"gemini-1","content":"tests: gemini fixture pass"}\n',
    )
    if gemini_session != "gemini-1" or gemini_output != "tests: gemini fixture pass":
        errors.append(f"Gemini JSONL should normalize session and assistant output: {gemini_session!r}, {gemini_output!r}")
    _error_session, gemini_error, gemini_error_events = parse_local_cli_output(
        "gemini",
        '{"type":"result","status":"error","error":{"type":"FatalError","message":"fixture terminal failure"}}\n',
    )
    if gemini_error != "fixture terminal failure" or len(gemini_error_events) != 1:
        errors.append(f"Gemini terminal errors should preserve their actionable message: {gemini_error!r}")

    missing = _run_process(
        ["twin-provider-binary-does-not-exist"],
        cwd=root,
        timeout_seconds=5,
        stream_output_path=root / "missing-cli-events.jsonl",
        parse=lambda stdout, stderr: parse_local_cli_output("codex", stdout, stderr),
        session_id="",
    )
    if missing.returncode != 127 or "unavailable" not in missing.output_text:
        errors.append(f"missing local provider should fail closed with an actionable result: {missing!r}")

    malformed_script = root / "fake-malformed-provider.sh"
    malformed_script.write_text("#!/bin/sh\nprintf '%s\\n' 'not-json'\n", encoding="utf-8")
    malformed_script.chmod(0o755)
    malformed = _run_process(
        [str(malformed_script)],
        cwd=root,
        timeout_seconds=5,
        stream_output_path=root / "malformed-cli-events.jsonl",
        parse=lambda stdout, stderr: parse_local_cli_output("codex", stdout, stderr),
        session_id="",
    )
    if malformed.returncode != 1 or "not-json" not in malformed.output_text or not any(
        event.get("type") == "malformed_output" for event in malformed.raw_events
    ):
        errors.append(f"malformed local provider output should remain visible and marked: {malformed!r}")

    terminal_error_script = root / "fake-terminal-error-provider.sh"
    terminal_error_script.write_text(
        "#!/bin/sh\nprintf '%s\\n' "
        "'{\"type\":\"result\",\"status\":\"error\",\"error\":{\"message\":\"fixture terminal failure\"}}'\n",
        encoding="utf-8",
    )
    terminal_error_script.chmod(0o755)
    terminal_error = _run_process(
        [str(terminal_error_script)],
        cwd=root,
        timeout_seconds=5,
        stream_output_path=root / "terminal-error-cli-events.jsonl",
        parse=lambda stdout, stderr: parse_local_cli_output("gemini", stdout, stderr),
        session_id="",
    )
    if terminal_error.returncode != 1 or "fixture terminal failure" not in terminal_error.output_text:
        errors.append(f"provider terminal errors should fail closed even after a zero process exit: {terminal_error!r}")

    timeout_script = root / "fake-timeout-provider.sh"
    timeout_script.write_text(
        "#!/bin/sh\n(sleep 2; printf 'orphaned' > \"$1\") &\nwait\n",
        encoding="utf-8",
    )
    timeout_script.chmod(0o755)
    orphan_marker = root / "orphan-marker"
    timed_out = _run_process(
        [str(timeout_script), str(orphan_marker)],
        cwd=root,
        timeout_seconds=1,
        stream_output_path=root / "timeout-cli-events.jsonl",
        parse=lambda stdout, stderr: parse_local_cli_output("codex", stdout, stderr),
        session_id="",
    )
    if timed_out.returncode != 124 or "TIMEOUT after 1s" not in timed_out.output_text:
        errors.append(f"local provider timeout should return the standard timeout result: {timed_out!r}")
    if not any(event.get("type") == "process_timeout" for event in timed_out.raw_events):
        errors.append("local provider timeout should persist a structured terminal event")
    time.sleep(2.2)
    if orphan_marker.exists():
        errors.append("local provider timeout should terminate tool subprocesses in the provider process group")

    fake_bin = root / "fake-claude-bin"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n(sleep 1; printf 'orphaned' > \"$2\") &\nwait\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    stream_marker = root / "claude-stream-orphan-marker"
    captured_marker = root / "claude-captured-orphan-marker"
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(fake_bin) + os.pathsep + original_path
    try:
        stream_timeout = run_claude_headless(
            str(stream_marker),
            cwd=root,
            timeout_seconds=0.2,
            stream_output_path=root / "claude-timeout-events.jsonl",
        )
        captured_timeout = run_claude_headless(
            str(captured_marker),
            cwd=root,
            timeout_seconds=0.2,
        )
    finally:
        os.environ["PATH"] = original_path
    if stream_timeout.returncode != 124 or captured_timeout.returncode != 124:
        errors.append("Claude provider timeout should return the standard timeout result")
    time.sleep(1.1)
    if stream_marker.exists() or captured_marker.exists():
        errors.append("Claude provider timeout should terminate tool subprocesses in every runner mode")

    fake_calls: list[dict[str, Any]] = []

    def fake_local_runner(provider: str, prompt: str, **kwargs: Any) -> ClaudeRunResult:
        fake_calls.append({"provider": provider, "prompt": prompt, **kwargs})
        return ClaudeRunResult(
            session_id="codex-fixture-session",
            output_text="tests: local cli fixture pass",
            returncode=0,
            raw_events=[{"type": "thread.started", "thread_id": "codex-fixture-session"}],
            cwd=str(root),
        )

    local_backend = LocalCliWorkerBackend(provider="codex", local_runner=fake_local_runner)
    local_result = local_backend.run_turn(
        "local fixture",
        cwd=root,
        allowed_tools=["Read"],
        disallowed_tools=["Write(personas)"],
        max_budget_usd=1.0,
        session_id="",
        timeout_seconds=60,
        stream_output_path=root / "local-cli-events.jsonl",
    )
    if local_backend.identity.backend != "local_cli" or local_backend.identity.provider != "codex":
        errors.append(f"local backend identity should preserve provider routing: {local_backend.identity!r}")
    if local_result.returncode != 0 or not fake_calls or fake_calls[0]["provider"] != "codex":
        errors.append(f"local backend should invoke the selected provider adapter: {local_result!r}")

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({
                "terminal_id": "term-fixture",
                "last_message": "tests: cao fixture pass",
                "status": "completed",
            }).encode("utf-8")

    def opener(request: Any, *, timeout: float) -> FakeResponse:
        calls.append((request, timeout))
        return FakeResponse()

    backend = CaoWorkerBackend(
        provider="codex",
        agent="developer",
        base_url="http://127.0.0.1:9889",
        auth_token="fixture-token",
        opener=opener,
    )
    event_path = root / "cao-events.jsonl"
    result = backend.run_turn(
        "implement fixture",
        cwd=root,
        allowed_tools=["Bash", "Read"],
        disallowed_tools=[],
        max_budget_usd=1.0,
        session_id="ignored-session",
        timeout_seconds=60,
        stream_output_path=event_path,
    )
    if result.returncode != 0 or result.output_text != "tests: cao fixture pass":
        errors.append(f"CAO backend should normalize a successful run-step response: {result!r}")
    if backend.supports_resume or result.session_id != "term-fixture":
        errors.append("CAO backend should expose the terminal for evidence without persisting resume")
    if not calls:
        errors.append("CAO backend should issue one run-step request")
    else:
        request, timeout = calls[0]
        payload = json.loads(request.data.decode("utf-8"))
        if request.full_url != "http://127.0.0.1:9889/terminals/run-step":
            errors.append(f"CAO backend used the wrong endpoint: {request.full_url}")
        if payload.get("provider") != "codex" or payload.get("agent") != "developer":
            errors.append(f"CAO backend lost provider routing: {payload!r}")
        if payload.get("working_directory") != str(root) or payload.get("teardown") is not True:
            errors.append(f"CAO backend should pass isolated cwd and teardown each turn: {payload!r}")
        if "allowed_tools" in payload:
            errors.append("CAO backend should let the selected profile resolve CAO tool permissions")
        if request.get_header("Authorization") != "Bearer fixture-token":
            errors.append("CAO backend should forward the local bearer token only as a header")
        if timeout != 90:
            errors.append(f"CAO backend HTTP timeout should include control-plane grace: {timeout}")
    if not event_path.exists() or "fixture-token" in event_path.read_text(encoding="utf-8"):
        errors.append("CAO backend event evidence should exist without leaking the bearer token")
    try:
        CaoWorkerBackend(
            provider="codex",
            agent="developer",
            base_url="http://cao.example.invalid",
            auth_token="fixture-token",
        )
        errors.append("CAO bearer auth should reject plaintext HTTP outside loopback")
    except ValueError as exc:
        if "requires HTTPS outside loopback" not in str(exc):
            errors.append(f"CAO plaintext auth rejection should be actionable: {exc}")
    try:
        CaoWorkerBackend(
            provider="codex",
            agent="developer",
            base_url="file:///tmp/cao",
            auth_token="",
        )
        errors.append("CAO base URL should reject non-HTTP schemes")
    except ValueError:
        pass
    try:
        _RejectCaoRedirects().redirect_request(
            request=type("RedirectRequest", (), {"full_url": "https://cao.example.invalid/terminals/run-step"})(),
            file_pointer=None,
            code=302,
            _message="found",
            headers={},
            _new_url="https://attacker.example.invalid/collect",
        )
        errors.append("CAO HTTP client should reject redirects before forwarding bearer headers")
    except HTTPError:
        pass
    except Exception as exc:
        errors.append(f"CAO redirect rejection should raise HTTPError: {exc}")
    default_cao = CaoWorkerBackend(
        provider="codex",
        agent="developer",
        base_url="http://127.0.0.1:9889",
        auth_token="fixture-token",
    )
    opener_owner = getattr(default_cao.opener, "__self__", None)
    if not any(isinstance(handler, _RejectCaoRedirects) for handler in getattr(opener_owner, "handlers", [])):
        errors.append("CAO default HTTP opener should install the redirect-rejection handler")

    def failing_opener(_request: Any, *, timeout: float) -> Any:
        del timeout
        raise URLError("connection refused")

    failed = CaoWorkerBackend(
        provider="codex",
        agent="developer",
        opener=failing_opener,
    ).run_turn(
        "fail fixture",
        cwd=root,
        allowed_tools=[],
        disallowed_tools=[],
        max_budget_usd=1.0,
        session_id="",
        timeout_seconds=1,
        stream_output_path=root / "failed-events.jsonl",
    )
    if failed.returncode == 0 or not failed.session_lost or "unavailable" not in failed.output_text:
        errors.append(f"CAO transport failure should fail closed with an actionable result: {failed!r}")

    class MalformedResponse(FakeResponse):
        def read(self) -> bytes:
            return b'{"terminal_id":"term-fixture","status":"completed"}'

    def malformed_opener(_request: Any, *, timeout: float) -> MalformedResponse:
        del timeout
        return MalformedResponse()

    malformed = CaoWorkerBackend(
        provider="codex",
        agent="developer",
        opener=malformed_opener,
    ).run_turn(
        "malformed fixture",
        cwd=root,
        allowed_tools=[],
        disallowed_tools=[],
        max_budget_usd=1.0,
        session_id="",
        timeout_seconds=1,
        stream_output_path=root / "malformed-events.jsonl",
    )
    if malformed.returncode == 0 or not malformed.session_lost or "missing: last_message" not in malformed.output_text:
        errors.append(f"malformed CAO success responses should fail closed: {malformed!r}")
    return errors


def _semantic_errors() -> list[str]:
    errors: list[str] = []
    bad_plan = _plan()
    bad_plan["items"][0]["covers_ac"] = ["NOPE"]
    if not validate_plan_semantics(_goal(), bad_plan):
        errors.append("unknown AC reference should fail")
    uncovered = _plan()
    uncovered["items"][1]["covers_ac"] = ["AC1"]
    if not validate_plan_semantics(_goal(), uncovered):
        errors.append("uncovered AC should fail")
    repeated = _plan()
    repeated["items"][0]["deliverable"] = "fixture 有测试证据"
    if not validate_plan_semantics(_goal(), repeated):
        errors.append("plan repeating AC statement should fail")
    cyclic = _plan()
    cyclic["items"][1]["depends_on"] = ["F1"]
    cyclic["items"][0]["depends_on"] = ["F2"]
    if not validate_plan_semantics(_goal(), cyclic):
        errors.append("dependency cycle should fail")
    cao_plan = _plan()
    cao_plan["execution"] = {"backend": "cao", "provider": "codex", "agent": "developer"}
    if validate_schema(cao_plan, PLAN_SCHEMA) or validate_plan_semantics(_goal(), cao_plan):
        errors.append("valid CAO execution routing should pass plan contracts")
    incomplete_cao = _plan()
    incomplete_cao["execution"] = {"backend": "cao"}
    if not validate_plan_semantics(_goal(), incomplete_cao):
        errors.append("CAO execution routing should require provider and agent")
    claude_plan = _plan()
    claude_plan["execution"] = {"backend": "claude_headless"}
    if validate_schema(claude_plan, PLAN_SCHEMA) or validate_plan_semantics(_goal(), claude_plan):
        errors.append("explicit Claude headless routing should pass plan contracts")
    ambiguous_claude = _plan()
    ambiguous_claude["execution"] = {
        "backend": "claude_headless",
        "provider": "codex",
        "agent": "developer",
    }
    if not validate_plan_semantics(_goal(), ambiguous_claude):
        errors.append("Claude headless routing should reject ignored CAO provider fields")
    local_plan = _plan()
    local_plan["execution"] = {"backend": "local_cli", "provider": "codex"}
    if validate_schema(local_plan, PLAN_SCHEMA) or validate_plan_semantics(_goal(), local_plan):
        errors.append("valid local CLI execution routing should pass plan contracts")
    invalid_local = _plan()
    invalid_local["execution"] = {"backend": "local_cli", "provider": "unknown"}
    if not validate_plan_semantics(_goal(), invalid_local):
        errors.append("unknown local CLI provider should fail semantic validation")
    local_with_agent = _plan()
    local_with_agent["execution"] = {"backend": "local_cli", "provider": "codex", "agent": "developer"}
    if not validate_plan_semantics(_goal(), local_with_agent):
        errors.append("local CLI execution should reject CAO-only agent fields")

    if validate_bootstrap_plan_constraints(_goal(), _plan()):
        errors.append("valid bootstrap plan constraints should pass")
    single_item = _plan()
    single_item["items"] = single_item["items"][:1]
    if not validate_bootstrap_plan_constraints(_goal(), single_item):
        errors.append("multi-AC bootstrap plan should reject single-item plans")
    copied_goal = _plan()
    copied_goal["items"][0]["deliverable"] = _goal()["one_liner"]
    if not validate_bootstrap_plan_constraints(_goal(), copied_goal):
        errors.append("bootstrap plan should reject raw goal as deliverable")
    missing_budget = _plan()
    missing_budget["items"][0]["evidence_plan"] = ["tests pass", "停止条件：产出测试证据后转 review"]
    if not validate_bootstrap_plan_constraints(_goal(), missing_budget):
        errors.append("bootstrap plan should reject missing evidence budget")
    missing_stop = _plan()
    missing_stop["items"][0]["evidence_plan"] = ["证据预算：只跑 fixture worker turn 测试"]
    missing_stop["items"][0]["next_action"] = "运行 worker fixture"
    if not validate_bootstrap_plan_constraints(_goal(), missing_stop):
        errors.append("bootstrap plan should reject missing stop/review condition")
    blocked = _plan()
    blocked["items"][0]["status"] = "blocked"
    blocked["items"][0].pop("blocked_reason", None)
    if not validate_bootstrap_plan_constraints(_goal(), blocked):
        errors.append("bootstrap plan should reject blocked item without blocked_reason")
    blocked["items"][0]["blocked_reason"] = "等待人工确认测试边界"
    if validate_bootstrap_plan_constraints(_goal(), blocked):
        errors.append("bootstrap plan should accept blocked item with blocked_reason")
    return errors


def _run_fixture_validation_impl() -> list[str]:
    errors: list[str] = _schema_errors() + _semantic_errors() + _behavior_helper_errors()
    with tempfile.TemporaryDirectory(prefix="twin-greenfield-") as tmp:
        root = Path(tmp)
        errors.extend(_worker_backend_errors(root))
        cao_budget_workspace = _write_workspace(root / "cao-budget")
        cao_budget_plan = load_plan(cao_budget_workspace)
        cao_budget_plan["execution"] = {"backend": "cao", "provider": "codex", "agent": "developer"}
        write_plan(cao_budget_workspace, cao_budget_plan)
        try:
            start_worker_turn(
                cao_budget_workspace,
                "reject unsupported CAO budget",
                backend=CaoWorkerBackend(provider="codex", agent="developer"),
                max_budget_usd=1.0,
            )
            errors.append("CAO worker turns should reject unsupported explicit cost budgets")
        except WorkspaceError as exc:
            if "not supported by the CAO run-step contract" not in str(exc):
                errors.append(f"CAO budget rejection should be actionable: {exc}")
        bootstrap_draft = draft_workspace("交付 bootstrap fixture", root / "bootstrap-workspace")
        bootstrap_workspace = write_workspace_draft(bootstrap_draft)
        try:
            validate_workspace(bootstrap_workspace)
        except WorkspaceError as exc:
            errors.append(f"bootstrap workspace failed validation: {exc}")
        if not (bootstrap_workspace / "goal.yaml").exists() or not (bootstrap_workspace / "plan.yaml").exists():
            errors.append("bootstrap should write goal.yaml and plan.yaml")
        errors.extend(_driver_protocol_errors(root))
        errors.extend(_runtime_reentry_errors(root))
        errors.extend(_worktree_cleanup_errors(root))
        authored_source = _write_workspace(root / "supervisor-authored-source")
        authored_draft = draft_from_files(
            root / "supervisor-authored-target",
            authored_source / "goal.yaml",
            authored_source / "plan.yaml",
            _write_research(root / "supervisor-authored-research"),
        )
        authored_workspace = write_workspace_draft(authored_draft)
        try:
            validate_workspace(authored_workspace)
        except WorkspaceError as exc:
            errors.append(f"supervisor-authored bootstrap artifacts should validate: {exc}")
        if load_research(authored_workspace / RESEARCH_FILE) != _research():
            errors.append("bootstrap should preserve the validated research artifact")
        invalid_research = _research()
        invalid_research["facts"][0].pop("source")
        invalid_research_path = root / "invalid-research.yaml"
        write_yaml_like(invalid_research_path, invalid_research)
        try:
            load_research(invalid_research_path)
            errors.append("research validation should reject facts without provenance")
        except WorkspaceError:
            pass
        try:
            write_workspace_draft(authored_draft)
            errors.append("bootstrap should reject existing workspace inputs without overwrite")
        except WorkspaceError:
            pass
        bad_plan_source = _write_workspace(root / "bad-plan-source")
        bad_plan = load_plan(bad_plan_source)
        bad_plan["goal_id"] = "wrong-goal"
        write_plan(bad_plan_source, bad_plan)
        try:
            draft_from_files(root / "bad-plan-target", bad_plan_source / "goal.yaml", bad_plan_source / "plan.yaml")
            errors.append("bootstrap should reject supervisor-authored plan with mismatched goal_id")
        except WorkspaceError:
            pass

        workspace = _write_workspace(root)
        try:
            validate_workspace(workspace)
        except WorkspaceError as exc:
            errors.append(f"valid workspace failed: {exc}")
        errors.extend(validate_artifact(workspace / "supervisor_state.json", SUPERVISOR_STATE_SCHEMA))
        supervisor_context = build_supervisor_context(workspace)
        if supervisor_context.get("next_item", {}).get("id") != "F1" or not supervisor_context.get("remaining_gaps"):
            errors.append("supervisor context should expose next item and gaps without generating instruction")
        focus = supervisor_context.get("acceptance_focus", {})
        if focus.get("last_mile") or not focus.get("current_item_acceptance_criteria"):
            errors.append("supervisor context should expose non-last-mile acceptance focus for the first bounded item")
        skeleton = supervisor_context.get("review_skeleton", {})
        if skeleton.get("status") != "<accepted_done|continue|needs_human|failed>":
            errors.append("supervisor context should expose an undecided review skeleton")
        if not plan_gaps(_goal(), _plan()):
            errors.append("pending plan should have gaps")
        if any(item["evidence"] for item in acceptance_evidence(_goal(), _plan())):
            errors.append("empty plan should not produce acceptance evidence")
        original_import = builtins.__import__

        def block_yaml_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "yaml":
                raise ModuleNotFoundError("yaml blocked by fixture")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = block_yaml_import
        try:
            fallback_context = build_supervisor_context(workspace)
            if str(fallback_context.get("goal", {}).get("core_goal") or "").strip() != _goal()["core_goal"].strip():
                errors.append("fallback yaml parser should preserve block-scalar goal text")
            if fallback_context.get("plan", {}).get("items", [{}])[0].get("scope") != "只覆盖 greenfield worker turn，不扩展旧命令兼容路径":
                errors.append("fallback yaml parser should preserve plan item mapping fields")
            plan_roundtrip = load_plan(workspace)
            plan_roundtrip["items"][0]["actual_evidence"] = ["第一行\n第二行"]
            plan_roundtrip["items"][0]["next_action"] = "多行\n下一步"
            write_plan(workspace, plan_roundtrip)
            reloaded_plan = load_plan(workspace)
            if reloaded_plan["items"][0].get("actual_evidence") != ["第一行\n第二行"]:
                errors.append("yaml fallback parser should preserve list block scalars")
            if reloaded_plan["items"][0].get("next_action") != "多行\n下一步":
                errors.append("yaml fallback writer should round-trip multiline plan values")
        finally:
            builtins.__import__ = original_import

        missing = root / "missing"
        missing.mkdir()
        try:
            validate_workspace(missing)
            errors.append("missing inputs should fail")
        except WorkspaceError:
            pass

        workspace_persona = _write_workspace(root / "workspace-persona")
        (workspace_persona / "worker-persona.md").write_text("goal-specific worker persona", encoding="utf-8")
        (workspace_persona / "nested").mkdir()
        (workspace_persona / "nested" / "supervisor-persona.md").write_text("goal-specific supervisor persona", encoding="utf-8")
        try:
            validate_workspace(workspace_persona)
            errors.append("workspace-local persona files should fail")
        except WorkspaceError as exc:
            message = str(exc)
            if "persona files must not live in the target workspace" not in message:
                errors.append(f"workspace-local persona failure should name the contract: {exc}")
            if "worker-persona.md" not in message or "nested/supervisor-persona.md" not in message:
                errors.append(f"workspace-local persona failure should include root and nested paths: {exc}")
        try:
            build_supervisor_context(workspace_persona)
            errors.append("supervisor context should reject workspace-local persona files")
        except WorkspaceError:
            pass

        legacy_workspace = _write_workspace(root / "legacy-plan")
        (legacy_workspace / "plan.yaml").unlink()
        (legacy_workspace / "feature_ledger.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        try:
            validate_workspace(legacy_workspace)
            errors.append("legacy feature_ledger.yaml should fail with a plan.yaml hint")
        except WorkspaceError as exc:
            if "plan.yaml" not in str(exc):
                errors.append(f"legacy plan failure should mention plan.yaml: {exc}")

        nonzero_workspace = _write_workspace(root / "nonzero")
        nonzero_run = start_worker_turn(nonzero_workspace, "触发 worker 非零退出 fixture", runner=FakeRunner(returncode=1))
        if nonzero_run["status"] != "review_required":
            errors.append("nonzero worker exit should still require supervisor review")
        nonzero_state = load_state(nonzero_workspace)
        if nonzero_state.get("status") != "review_required":
            errors.append("nonzero worker exit should leave workspace in review_required state")
        try:
            start_worker_turn(nonzero_workspace, "不应跳过 review", runner=FakeRunner())
            errors.append("nonzero worker exit should still block next turn until review")
        except WorkspaceError:
            pass

        artifact_failure_workspace = _write_workspace(root / "run-artifact-failure")
        original_worker_write_json = twin_worker.write_json

        def fail_run_artifact(path: Path, value: Any) -> None:
            if path.name == "run.json":
                raise OSError("fixture run artifact write failure")
            original_worker_write_json(path, value)

        twin_worker.write_json = fail_run_artifact
        try:
            try:
                start_worker_turn(
                    artifact_failure_workspace,
                    "触发 run artifact 写失败 fixture",
                    runner=FakeRunner(),
                )
                errors.append("run artifact write failure should fail the worker turn")
            except OSError:
                pass
        finally:
            twin_worker.write_json = original_worker_write_json
        artifact_failure_state = load_state(artifact_failure_workspace)
        if artifact_failure_state.get("status") != "worker_running":
            errors.append("run artifact failure must not publish review_required before evidence exists")
        failed_run_id = str(artifact_failure_state.get("current_run_id") or "")
        if not failed_run_id or (artifact_failure_workspace / "runs" / failed_run_id / "run.json").exists():
            errors.append("run artifact failure fixture should retain only the in-progress run state")

        stale_workspace = _write_workspace(root / "stale-running")
        stale_state = load_state(stale_workspace)
        stale_state["status"] = "worker_running"
        stale_state["current_run_id"] = "run-stale"
        stale_state["next_instruction"] = "fixture instruction from abandoned worker"
        write_state(stale_workspace, stale_state)
        stale_runner = FakeRunner()
        stale_run = start_worker_turn(stale_workspace, "恢复 stale worker_running", runner=stale_runner)
        if stale_run.get("status") != "review_required" or len(stale_runner.calls) != 1:
            errors.append("stale worker_running without artifacts should allow a fresh worker turn")
        stale_after = load_state(stale_workspace)
        if stale_after.get("current_run_id") == "run-stale" or stale_after.get("status") != "review_required":
            errors.append("stale worker_running recovery should replace the abandoned run id")
        if (stale_workspace / "runs" / "run-stale").exists():
            errors.append("stale worker_running detection should not create an empty abandoned run directory")

        active_workspace = _write_workspace(root / "active-running")
        active_state = load_state(active_workspace)
        active_state["status"] = "worker_running"
        active_state["current_run_id"] = "run-active"
        write_state(active_workspace, active_state)
        active_pending = active_workspace / "runs" / "run-active" / "pending.json"
        active_pending.parent.mkdir(parents=True, exist_ok=True)
        active_pending.write_text(
            '{"schema_version":1,"run_id":"run-active","started_at":"2026-05-11T00:00:00Z","status":"worker_running"}\n',
            encoding="utf-8",
        )
        try:
            start_worker_turn(active_workspace, "不应抢占真实 running", runner=FakeRunner())
            errors.append("worker_running with pending artifact should still block a fresh worker turn")
        except WorkspaceError:
            pass

        runner = FakeRunner()
        try:
            start_worker_turn(workspace, "", runner=runner)
            errors.append("worker-turn should require supervisor-authored instruction")
        except WorkspaceError:
            pass
        run = start_worker_turn(workspace, "推进 plan item F1", runner=runner)
        errors.extend(validate_schema(run, RUN_SCHEMA))
        if run.get("worker", {}).get("backend") != "claude_headless" or run.get("worker", {}).get("provider") != "claude_code":
            errors.append("worker run should record backend and provider identity")
        if not run.get("evidence", {}).get("validation"):
            errors.append("worker run should record validation evidence")
        events_text = (workspace / "runs" / run["run_id"] / "events.jsonl").read_text(encoding="utf-8")
        if "fixture streamed" not in events_text:
            errors.append("worker turn should preserve streamed events instead of overwriting them at completion")
        if "quality_flags" not in run.get("evidence", {}):
            errors.append("worker run should record quality_flags")
        call = runner.calls[0]
        if call.get("cwd") != workspace.resolve().parent:
            errors.append("worker should run from repo root, not the twin workspace directory")
        if call.get("session_id"):
            errors.append("first worker turn should not resume")
        if call.get("permission_mode") != "bypassPermissions":
            errors.append("worker permission mode should be bypassPermissions")
        if call.get("extra_env", {}).get("DEV_RULES") != str(contracts.DEV_RULES_ROOT):
            errors.append("worker should receive DEV_RULES env pointing at persona source")
        allowed = call.get("allowed_tools") or []
        if "Bash" not in allowed or "Read" not in allowed:
            errors.append("worker should receive explicit allowed tools")
        disallowed = call.get("disallowed_tools") or []
        expected_persona_denies = {
            f"Edit({PERSONAS_DIR / '**'})",
            f"Write({PERSONAS_DIR / '**'})",
            f"NotebookEdit({PERSONAS_DIR / '**'})",
            "Bash(*$DEV_RULES/personas*)",
            "Bash(*${DEV_RULES}/personas*)",
            f"Bash(*{PERSONAS_DIR}*)",
        }
        if not expected_persona_denies.issubset(set(disallowed)):
            errors.append("worker should disallow writes inside $DEV_RULES/personas")
        prompt = str(call.get("prompt") or "")
        if f"# {WORKER_PERSONA_PATH}" not in prompt or "fixture-goal" not in prompt or "推进 plan item F1" not in prompt:
            errors.append("worker prompt missing persona/goal/supervisor-authored instruction")
        if "supervisor persona" in prompt:
            errors.append("worker prompt should not include supervisor persona")
        if "acceptance_focus.json" in prompt or "worker completion contract" in prompt:
            errors.append("worker prompt should not include harness-generated completion contracts")
        if "supervisor_session_id" in json.dumps(run):
            errors.append("run artifact should not contain supervisor session fields")
        current_path = workspace / "CURRENT.md"
        current_text = current_path.read_text(encoding="utf-8")
        if "Status: reviewing (review_required)" not in current_text or f"Current item: {run['run_id']}" in current_text:
            errors.append("worker turn should refresh CURRENT.md after completion")
        current_mtime = current_path.stat().st_mtime_ns
        state_path = workspace / "supervisor_state.json"
        run_path = workspace / "runs" / run["run_id"] / "run.json"
        state_text = state_path.read_text(encoding="utf-8")
        run_text = run_path.read_text(encoding="utf-8")
        status_workspace(workspace)
        if current_path.stat().st_mtime_ns != current_mtime:
            errors.append("status should not rewrite CURRENT.md")
        if state_path.read_text(encoding="utf-8") != state_text or run_path.read_text(encoding="utf-8") != run_text:
            errors.append("status should not rewrite state or run artifacts")

        try:
            start_worker_turn(workspace, "强行再启动", runner=runner)
            errors.append("review_required state should block next worker turn")
        except WorkspaceError:
            pass

        context = build_review_context(workspace, run["run_id"])
        if "xuejiao supervisor persona" not in context.get("supervisor_persona", ""):
            errors.append("review context should include supervisor persona")
        if context.get("review_skeleton", {}).get("status") != "<accepted_done|continue|needs_human|failed>":
            errors.append("review context should not preselect a status")
        if not isinstance(context.get("run"), dict):
            errors.append("review context should expose the raw run artifact for supervisor judgment")

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.twin",
                "review-context",
                "--workspace",
                str(workspace),
                "--run-id",
                run["run_id"],
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if cli.returncode != 0:
            errors.append(f"review-context --json should be accepted by CLI: {cli.stderr.strip()}")

        continue_review = _review("continue", gaps=["F1 missing"], actions=["fix_drift", "validate_more"])
        state = apply_supervisor_review(workspace, run["run_id"], continue_review)
        if state.get("status") != "continue" or state.get("next_instruction") != "继续 fixture":
            errors.append("continue review should store supervisor-authored next instruction unchanged")
        continued_run_artifact = read_json(workspace / "runs" / run["run_id"] / "run.json")
        if continued_run_artifact.get("status") != "continue" or continued_run_artifact.get("review", {}).get("status") != "continue":
            errors.append("continue review should mark reviewed run status as continue and inline review")

        second = start_worker_turn(workspace, "继续 F1", runner=runner)
        if not runner.calls[-1].get("session_id"):
            errors.append("second worker turn should resume saved worker session")
        if not second["worker"]["resume_used"]:
            errors.append("second run artifact should mark resume_used")
        apply_supervisor_review(workspace, second["run_id"], _review("continue"))

        reset_runner = FakeRunner(session_lost_once=True)
        reset = start_worker_turn(workspace, "测试 session reset", runner=reset_runner)
        if len(reset_runner.calls) != 2:
            errors.append("session lost should retry fresh once")
        if reset_runner.calls[-1].get("session_id"):
            errors.append("session lost retry should be fresh")
        if reset["worker"]["resume_used"]:
            errors.append("fresh retry should not mark resume_used")
        apply_supervisor_review(workspace, reset["run_id"], _review("continue"))

        warning_retry_runner = FakeRunner(warning_only_once=True)
        warning_retry = start_worker_turn(workspace, "测试 warning-only resume", runner=warning_retry_runner)
        if len(warning_retry_runner.calls) != 2:
            errors.append("warning-only resume should retry fresh once")
        if warning_retry_runner.calls[-1].get("session_id"):
            errors.append("warning-only retry should be fresh")
        if warning_retry["worker"]["resume_used"]:
            errors.append("warning-only fresh retry should not mark resume_used")
        apply_supervisor_review(workspace, warning_retry["run_id"], _review("continue"))

        body_guard_runner = FakeRunner(body_guard_once=True)
        body_guard = start_worker_turn(workspace, "测试 body-guard resume", runner=body_guard_runner)
        if len(body_guard_runner.calls) != 2:
            errors.append("body-guard resume should retry fresh once")
        if body_guard_runner.calls[-1].get("session_id"):
            errors.append("body-guard retry should be fresh")
        body_guard_flags = body_guard.get("evidence", {}).get("quality_flags", [])
        if "BODY_GUARD_REJECTION" not in body_guard_flags:
            errors.append("body-guard resume should record BODY_GUARD_REJECTION")
        if "SESSION_LOST" not in body_guard_flags:
            errors.append("body-guard resume via session_lost branch should also record SESSION_LOST")
        if "WORKER_SESSION_RESET" not in body_guard_flags:
            errors.append("body-guard resume should record WORKER_SESSION_RESET")
        if load_state(workspace).get("worker_session_id") is not None:
            errors.append("body-guard resume should clear persisted worker_session_id even on successful retry")
        apply_supervisor_review(workspace, body_guard["run_id"], _review("continue"))

        no_progress_workspace = _write_workspace(root / "no-progress-clear")
        np_state = load_state(no_progress_workspace)
        np_state["worker_session_id"] = "doomed-session-id"
        np_state["status"] = "continue"
        np_state["next_instruction"] = "继续"
        write_state(no_progress_workspace, np_state)
        weak_runner = FakeRunner(output_text="继续推进但没有任何可验收的 diff 或运行结果")
        np_run = start_worker_turn(no_progress_workspace, "无进展 fixture", runner=weak_runner)
        np_after = load_state(no_progress_workspace)
        if np_after.get("worker_session_id") is not None:
            errors.append("NO_PROGRESS should clear worker_session_id even after fresh retry")
        if "NO_PROGRESS_DETECTED" not in np_run.get("evidence", {}).get("quality_flags", []):
            errors.append("weak fresh worker turn should flag NO_PROGRESS_DETECTED")

        needs_run = start_worker_turn(workspace, "等人确认 fixture", runner=runner)
        needs_human = _review("needs_human", question="请确认 fixture")
        state = apply_supervisor_review(workspace, needs_run["run_id"], needs_human)
        if state.get("status") != "needs_human" or not state.get("needs_human"):
            errors.append("needs_human review did not update state")
        status = status_workspace(workspace)
        display = status.get("display", {})
        if not status.get("needs_human") or status.get("current_run_id") != needs_run["run_id"]:
            errors.append("status should expose needs_human and the latest run")
        if display.get("label") != "waiting for you" or display.get("next_command") != "twin respond <answer>":
            errors.append("status display should make needs_human actionable for humans")
        if not isinstance(display.get("evidence_paths"), dict) or not display["evidence_paths"].get("workspace_events"):
            errors.append("status display should expose workspace event evidence path")
        active_env = {**os.environ, ACTIVE_WORKSPACE_ENV: str(root / "active-workspace")}
        needs_cli = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "status", str(workspace)],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=30,
            env=active_env,
        )
        if len(needs_cli.stdout.encode("utf-8")) > 4096:
            errors.append("needs_human status CLI should stay compact and not expand workspace artifacts")
        if "Status: waiting for you (needs_human)" not in needs_cli.stdout or "Next command: twin respond <answer>" not in needs_cli.stdout:
            errors.append("needs_human CLI should lead with human-friendly status and next command")
        if "respond=twin respond <answer>" not in needs_cli.stdout or "evidence_review=" not in needs_cli.stdout:
            errors.append("needs_human CLI should still include the question and twin respond path")
        isolated_env = {key: value for key, value in os.environ.items() if key != ACTIVE_WORKSPACE_ENV}
        isolated_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        isolated_project = root / "active-workspace-isolated-project"
        isolated_project.mkdir()
        isolated_cli = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "respond", "继续"],
            cwd=isolated_project,
            capture_output=True,
            text=True,
            timeout=30,
            env=isolated_env,
        )
        if isolated_cli.returncode == 0:
            errors.append("active workspace should be scoped by current project cwd")
        elif "workspace is required" not in isolated_cli.stderr:
            errors.append(f"isolated active workspace failure should be actionable: {isolated_cli.stderr.strip()}")
        # Multi-project happy path: cwd-hash scoping must let two projects
        # keep independent active workspaces. Override HOME so the cwd-hash
        # path resolves under a tmp dir and the test runner's real
        # ~/.claude is left alone; ACTIVE_WORKSPACE_ENV is NOT set so the
        # cwd-hash branch in active_workspace_file() is exercised.
        multi_home = root / "multi-home"
        multi_home.mkdir()
        project_a = root / "multi-project-a"
        project_a.mkdir()
        project_b = root / "multi-project-b"
        project_b.mkdir()
        workspace_a = _write_workspace(root / "multi-workspace-a")
        workspace_b = _write_workspace(root / "multi-workspace-b")
        validate_workspace(workspace_a)
        validate_workspace(workspace_b)
        multi_env = {key: value for key, value in os.environ.items() if key != ACTIVE_WORKSPACE_ENV}
        multi_env["HOME"] = str(multi_home)
        multi_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        seed_a = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "status", str(workspace_a), "--json"],
            cwd=project_a, capture_output=True, text=True, timeout=30, env=multi_env,
        )
        if seed_a.returncode != 0:
            errors.append(f"multi-project A seed status failed: {seed_a.stderr.strip()}")
        seed_b = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "status", str(workspace_b), "--json"],
            cwd=project_b, capture_output=True, text=True, timeout=30, env=multi_env,
        )
        if seed_b.returncode != 0:
            errors.append(f"multi-project B seed status failed: {seed_b.stderr.strip()}")
        neutral_pointer_dir = multi_home / ".twin" / "active-workspaces"
        if not neutral_pointer_dir.is_dir() or len(list(neutral_pointer_dir.iterdir())) != 2:
            errors.append("active workspace pointers should use the provider-neutral ~/.twin path")
        multi_a = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "status", "--json"],
            cwd=project_a, capture_output=True, text=True, timeout=30, env=multi_env,
        )
        if multi_a.returncode != 0:
            errors.append(f"multi-project A should resolve workspace_a via cwd hash: {multi_a.stderr.strip()}")
        elif str(workspace_a) not in multi_a.stdout:
            errors.append("multi-project A active workspace did not resolve to workspace_a")
        elif str(workspace_b) in multi_a.stdout:
            errors.append("multi-project A leaked workspace_b — cwd-scoping is not isolating projects")
        legacy_project = root / "legacy-active-project"
        legacy_project.mkdir()
        legacy_workspace = _write_workspace(root / "legacy-active-workspace")
        validate_workspace(legacy_workspace)
        legacy_id = hashlib.sha256(str(legacy_project.resolve()).encode("utf-8")).hexdigest()[:16]
        legacy_pointer = multi_home / ".claude" / "twin-active-workspaces" / legacy_id
        legacy_pointer.parent.mkdir(parents=True, exist_ok=True)
        legacy_pointer.write_text(str(legacy_workspace) + "\n", encoding="utf-8")
        legacy_active = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "status", "--json"],
            cwd=legacy_project, capture_output=True, text=True, timeout=30, env=multi_env,
        )
        if legacy_active.returncode != 0 or str(legacy_workspace) not in legacy_active.stdout:
            errors.append("provider-neutral active workspace lookup should retain legacy ~/.claude fallback")
        # Stale pointer: workspace_a's pointer still resolves, but the
        # workspace itself is gone. The user-visible error must name the
        # path AND the recovery action, not bubble up a schema failure
        # from a downstream load_goal.
        (workspace_a / GOAL_FILE).unlink()
        stale_a = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "respond", "继续"],
            cwd=project_a, capture_output=True, text=True, timeout=30, env=multi_env,
        )
        if stale_a.returncode == 0:
            errors.append("stale active workspace should not succeed silently")
        elif "no longer exists" not in stale_a.stderr:
            errors.append(f"stale active workspace error should be directed: {stale_a.stderr.strip()}")
        for blocked_status in ("idle", "continue", "review_required", "accepted_done", "failed"):
            blocked_workspace = _write_workspace(root / f"respond-blocked-{blocked_status}", completed=blocked_status == "accepted_done")
            blocked_state = load_state(blocked_workspace)
            blocked_state["status"] = blocked_status
            if blocked_status == "continue":
                blocked_state["next_instruction"] = "继续 fixture"
            write_state(blocked_workspace, blocked_state)
            try:
                record_human_response(blocked_workspace, "不应接受")
                errors.append(f"respond should reject {blocked_status} state")
            except WorkspaceError:
                pass
            if (blocked_workspace / "human_response.json").exists():
                errors.append(f"respond should not write human_response.json for {blocked_status} state")
            if (blocked_workspace / "workspace_events.jsonl").exists():
                errors.append(f"respond should not write workspace events for {blocked_status} state")
            if load_state(blocked_workspace).get("status") != blocked_status:
                errors.append(f"respond should not mutate {blocked_status} state")

        concurrent_respond_workspace = _write_workspace(root / "concurrent-respond")
        concurrent_respond_state = load_state(concurrent_respond_workspace)
        concurrent_respond_state["status"] = "needs_human"
        concurrent_respond_state["needs_human"] = {
            "question": "请选择唯一答案",
            "context": "concurrent respond fixture",
            "created_at": "2026-07-22T00:00:00Z",
        }
        write_state(concurrent_respond_workspace, concurrent_respond_state)
        response_write_started = threading.Event()
        release_response_write = threading.Event()
        first_response_errors: list[BaseException] = []
        original_write_human_response = twin_runtime.write_human_response

        def block_first_response(workspace_path: Path, text: str) -> Path:
            target = original_write_human_response(workspace_path, text)
            if text == "first-answer":
                response_write_started.set()
                if not release_response_write.wait(timeout=5):
                    raise RuntimeError("concurrent respond fixture timed out")
            return target

        def record_first_response() -> None:
            try:
                record_human_response(concurrent_respond_workspace, "first-answer")
            except BaseException as exc:
                first_response_errors.append(exc)

        twin_runtime.write_human_response = block_first_response
        first_response_thread = threading.Thread(target=record_first_response)
        first_response_thread.start()
        try:
            if not response_write_started.wait(timeout=5):
                errors.append("concurrent respond fixture did not enter the first mutation")
            else:
                try:
                    record_human_response(concurrent_respond_workspace, "second-answer")
                    errors.append("concurrent respond calls must not both succeed")
                except WorkspaceError as exc:
                    if "another twin driver is already active" not in str(exc):
                        errors.append(f"concurrent respond should fail on the workspace lock: {exc}")
        finally:
            release_response_write.set()
            first_response_thread.join(timeout=5)
            twin_runtime.write_human_response = original_write_human_response
        if first_response_thread.is_alive():
            errors.append("concurrent respond fixture did not release the first mutation")
        if first_response_errors:
            errors.append(f"first concurrent respond should succeed: {first_response_errors[0]}")
        concurrent_response = read_json(concurrent_respond_workspace / "human_response.json")
        if concurrent_response.get("text") != "first-answer":
            errors.append("rejected concurrent respond must not overwrite the accepted response")
        concurrent_events_path = concurrent_respond_workspace / "workspace_events.jsonl"
        concurrent_events = concurrent_events_path.read_text(encoding="utf-8").splitlines()
        if len(concurrent_events) != 1:
            errors.append("concurrent respond should append exactly one audit event")

        try:
            start_worker_turn(workspace, "不应启动", runner=runner)
            errors.append("worker should not start while needs_human pending")
        except WorkspaceError:
            pass
        user_respond_cli = subprocess.run(
            [sys.executable, "-m", "scripts.twin", "respond", "继续"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=30,
            env=active_env,
        )
        if user_respond_cli.returncode != 0:
            errors.append(f"/twin respond user path should use active workspace: {user_respond_cli.stderr.strip()}")
        state = load_state(workspace)
        if state.get("status") != "continue" or state.get("needs_human") is not None:
            errors.append("respond CLI user path did not clear needs_human state")
        workspace_events = (workspace / "workspace_events.jsonl").read_text(encoding="utf-8")
        if "继续" in workspace_events:
            errors.append("respond workspace event should not record human response text")
        event_records = [json.loads(line) for line in workspace_events.splitlines() if line.strip()]
        if not event_records or event_records[-1].get("event") != "human_response_recorded":
            errors.append("respond should append a human_response_recorded workspace event")
        else:
            event = event_records[-1]
            if event.get("previous_status") != "needs_human" or event.get("new_status") != "continue":
                errors.append("respond workspace event should record the state transition")
            if event.get("artifact_ref") != "human_response.json" or event.get("response_chars") != len("继续"):
                errors.append("respond workspace event should record artifact and response length only")
        resumed = start_worker_turn(workspace, "继续 fixture", runner=runner)
        consumed = read_json(workspace / "human_response.json")
        if consumed.get("consumed_by_run_id") != resumed["run_id"]:
            errors.append("worker turn should consume human_response.json")
        if "human_response.json" not in str(runner.calls[-1].get("prompt")):
            errors.append("worker prompt should include unconsumed human response")
        apply_supervisor_review(workspace, resumed["run_id"], _review("continue"))
        post_consume = start_worker_turn(workspace, "再跑一轮 fixture", runner=runner)
        if "human_response.json" in str(runner.calls[-1].get("prompt")):
            errors.append("consumed human_response.json should not be re-injected into worker prompt")
        apply_supervisor_review(workspace, post_consume["run_id"], _review("continue"))

        loop_workspace = _write_workspace(root / "loop")
        loop_runner = FakeRunner()
        review_count = {"value": 0}

        def loop_review(_context: dict[str, Any]) -> dict[str, Any]:
            review_count["value"] += 1
            return _review("continue" if review_count["value"] == 1 else "accepted_done")

        loop_result = run_supervisor_loop_harness(
            loop_workspace,
            instruction="执行 loop fixture",
            review_fn=loop_review,
            runner=loop_runner,
            max_rounds=3,
        )
        if loop_result.get("status") != "accepted_done" or len(loop_result.get("runs") or []) != 2:
            errors.append("supervisor loop harness should continue automatically until terminal status")
        try:
            run_supervisor_loop_harness(
                _write_workspace(root / "loop-limit"),
                instruction="执行 loop limit fixture",
                review_fn=lambda _context: _review("continue"),
                runner=FakeRunner(),
                max_rounds=1,
            )
            errors.append("supervisor loop harness should fail when max_rounds is exceeded")
        except WorkspaceError:
            pass
        try:
            run_supervisor_loop_harness(
                _write_workspace(root / "loop-missing-instruction"),
                instruction="执行 missing instruction fixture",
                review_fn=lambda _context: {**_review("continue"), "next_instruction": ""},
                runner=FakeRunner(),
                max_rounds=2,
            )
            errors.append("supervisor loop harness should fail when continue lacks next_instruction")
        except WorkspaceError:
            pass
        needs_loop = run_supervisor_loop_harness(
            _write_workspace(root / "loop-needs-human"),
            instruction="执行 needs_human loop fixture",
            review_fn=lambda _context: _review("needs_human", question="请确认 loop fixture"),
            runner=FakeRunner(),
            max_rounds=2,
        )
        if needs_loop.get("status") != "needs_human":
            errors.append("supervisor loop harness should stop on needs_human")

        weak_workspace = _write_workspace(root / "weak-output")
        weak_runner = FakeRunner(output_text="我会继续收敛 F6/AC1")
        weak_run = start_worker_turn(weak_workspace, "弱输出 fixture", runner=weak_runner)
        weak_flags = weak_run.get("evidence", {}).get("quality_flags", [])
        for flag in ("VALIDATION_NOT_REPORTED", "WORKER_OUTPUT_WEAK"):
            if flag not in weak_flags:
                errors.append(f"weak worker output should record {flag}")
        weak_context = build_review_context(weak_workspace, weak_run["run_id"])
        if weak_context.get("review_skeleton", {}).get("status") != "<accepted_done|continue|needs_human|failed>":
            errors.append("weak run context should not preselect a status")

        done_missing_evidence_workspace = _write_workspace(root / "done-missing-evidence", completed=True, plan_evidence=False)
        done_missing_evidence_runner = FakeRunner()
        done_missing_evidence_run = start_worker_turn(done_missing_evidence_workspace, "final", runner=done_missing_evidence_runner)
        review_only_evidence = _review("accepted_done")
        review_only_evidence["plan_updates"] = []
        try:
            apply_supervisor_review(done_missing_evidence_workspace, done_missing_evidence_run["run_id"], review_only_evidence)
            errors.append("accepted_done should fail when only review has evidence and plan actual_evidence is empty")
        except WorkspaceError:
            pass

        done_workspace = _write_workspace(root / "done", completed=True)
        done_runner = FakeRunner()
        done_run = start_worker_turn(done_workspace, "final", runner=done_runner)
        bad_done = _review("accepted_done", gaps=["still missing"])
        try:
            apply_supervisor_review(done_workspace, done_run["run_id"], bad_done)
            errors.append("accepted_done with remaining gaps should fail")
        except WorkspaceError:
            pass

        done = _review("accepted_done")
        state = apply_supervisor_review(done_workspace, done_run["run_id"], done)
        if state.get("status") != "accepted_done":
            errors.append("accepted_done review did not update state")
        if state.get("current_item_id"):
            errors.append("accepted_done state should clear current_item_id once no items remain")
        run_artifact = read_json(done_workspace / "runs" / done_run["run_id"] / "run.json")
        if run_artifact.get("status") != "accepted_done" or run_artifact.get("review", {}).get("status") != "accepted_done":
            errors.append("accepted run artifact status/review not updated")
        status = status_workspace(done_workspace)
        if status.get("status") != "accepted_done" or status.get("remaining_gaps"):
            errors.append("status_workspace did not report clean accepted_done")
        if status.get("current_item_id"):
            errors.append("status_workspace should not surface a completed item as current after accepted_done")
    return errors


def run_fixture_validation() -> list[str]:
    original = os.environ.get("TWIN_WORKTREE_ISOLATION")
    os.environ["TWIN_WORKTREE_ISOLATION"] = "0"
    try:
        return _run_fixture_validation_impl()
    finally:
        if original is None:
            os.environ.pop("TWIN_WORKTREE_ISOLATION", None)
        else:
            os.environ["TWIN_WORKTREE_ISOLATION"] = original


def validate_path(path: Path) -> list[str]:
    if not path:
        return ["path is required unless --fixtures is set"]
    path = path.expanduser().resolve()
    if path.name == RESEARCH_FILE:
        try:
            load_research(path)
        except WorkspaceError as exc:
            return [str(exc)]
        return []
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
        return ["supervisor_review.json is legacy; reviews are now embedded in run.json::review"]
    return [f"unsupported validation path: {path}"]
