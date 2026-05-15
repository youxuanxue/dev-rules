from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import contracts, util
from .bootstrap import draft_from_files, draft_workspace, write_workspace_draft
from .claude_runner import ClaudeRunResult
from .contracts import (
    ACTIVE_WORKSPACE_ENV,
    GOAL_FILE,
    GOAL_SCHEMA,
    PLAN_SCHEMA,
    PERSONAS_DIR,
    RUN_SCHEMA,
    SUPERVISOR_REVIEW_SCHEMA,
    SUPERVISOR_STATE_SCHEMA,
    WORKER_PERSONA_PATH,
)
from .loop_harness import run_supervisor_loop_harness
from .plan import acceptance_evidence, plan_gaps, validate_bootstrap_plan_constraints, validate_plan_semantics
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
from .util import read_json, write_json
from .claude_runner import DEFAULT_WORKER_TIMEOUT_SECONDS, WORKER_TIMEOUT_ENV, default_worker_timeout_seconds
from .worker import DEFAULT_WORKER_MAX_BUDGET_USD, assess_run_quality, changed_files_from_status, default_worker_max_budget_usd
from .workspace import WorkspaceError, load_plan, load_state, status_summary, write_plan, write_state


class FakeRunner:
    def __init__(
        self,
        *,
        session_lost_once: bool = False,
        warning_only_once: bool = False,
        returncode: int = 0,
        output_text: str | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.session_lost_once = session_lost_once
        self.warning_only_once = warning_only_once
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
    if action.get("action") != "wait_worker" or action.get("worker", {}).get("state") != "active":
        errors.append(f"worker_running continuation should wait with diagnostics: {action!r}")

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
    return errors


def _behavior_helper_errors() -> list[str]:
    errors: list[str] = []
    changed = changed_files_from_status(
        " M docs/agent_integration.md\n"
        " M pyproject.toml\n"
        " M tests/test_entry_runtimes.py\n"
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


def run_fixture_validation() -> list[str]:
    errors: list[str] = _schema_errors() + _semantic_errors() + _behavior_helper_errors()
    with tempfile.TemporaryDirectory(prefix="twin-greenfield-") as tmp:
        root = Path(tmp)
        bootstrap_draft = draft_workspace("交付 bootstrap fixture", root / "bootstrap-workspace")
        bootstrap_workspace = write_workspace_draft(bootstrap_draft)
        try:
            validate_workspace(bootstrap_workspace)
        except WorkspaceError as exc:
            errors.append(f"bootstrap workspace failed validation: {exc}")
        if not (bootstrap_workspace / "goal.yaml").exists() or not (bootstrap_workspace / "plan.yaml").exists():
            errors.append("bootstrap should write goal.yaml and plan.yaml")
        errors.extend(_runtime_reentry_errors(root))
        authored_source = _write_workspace(root / "supervisor-authored-source")
        authored_draft = draft_from_files(
            root / "supervisor-authored-target",
            authored_source / "goal.yaml",
            authored_source / "plan.yaml",
        )
        authored_workspace = write_workspace_draft(authored_draft)
        try:
            validate_workspace(authored_workspace)
        except WorkspaceError as exc:
            errors.append(f"supervisor-authored bootstrap artifacts should validate: {exc}")
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

        needs_run = start_worker_turn(workspace, "等人确认 fixture", runner=runner)
        needs_human = _review("needs_human", question="请确认 fixture")
        state = apply_supervisor_review(workspace, needs_run["run_id"], needs_human)
        if state.get("status") != "needs_human" or not state.get("needs_human"):
            errors.append("needs_human review did not update state")
        status = status_workspace(workspace)
        display = status.get("display", {})
        if not status.get("needs_human") or status.get("current_run_id") != needs_run["run_id"]:
            errors.append("status should expose needs_human and the latest run")
        if display.get("label") != "waiting for you" or display.get("next_command") != "/twin respond <answer>":
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
        if "Status: waiting for you (needs_human)" not in needs_cli.stdout or "Next command: /twin respond <answer>" not in needs_cli.stdout:
            errors.append("needs_human CLI should lead with human-friendly status and next command")
        if "respond=/twin respond <answer>" not in needs_cli.stdout or "evidence_review=" not in needs_cli.stdout:
            errors.append("needs_human CLI should still include the question and /twin respond path")
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
        return ["supervisor_review.json is legacy; reviews are now embedded in run.json::review"]
    return [f"unsupported validation path: {path}"]
