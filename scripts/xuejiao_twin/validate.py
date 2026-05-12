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
from .claude_runner import ClaudeRunResult
from .contracts import (
    GOAL_SCHEMA,
    LEDGER_SCHEMA,
    PERSONAS_DIR,
    RUN_SCHEMA,
    SUPERVISOR_REVIEW_SCHEMA,
    SUPERVISOR_STATE_SCHEMA,
    WORKER_PERSONA_PATH,
)
from .ledger import acceptance_evidence, ledger_gaps, validate_ledger_semantics
from .runtime import (
    apply_supervisor_review,
    build_review_context,
    build_supervisor_context,
    health_workspace,
    record_human_response,
    start_worker_turn,
    status_workspace,
    validate_workspace,
)
from .schema_contract import validate_artifact, validate_schema
from .util import read_json
from .claude_runner import DEFAULT_WORKER_TIMEOUT_SECONDS, WORKER_TIMEOUT_ENV, default_worker_timeout_seconds
from .worker import DEFAULT_WORKER_MAX_BUDGET_USD, assess_run_quality, changed_files_from_status, default_worker_max_budget_usd
from .workspace import WorkspaceError, load_ledger, load_state, write_ledger, write_state


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
  - 不测试旧命令兼容路径
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
    old_budget_env = os.environ.pop("XUEJIAO_TWIN_WORKER_MAX_BUDGET_USD", None)
    try:
        if DEFAULT_WORKER_MAX_BUDGET_USD != 20.0 or default_worker_max_budget_usd() != 20.0:
            errors.append("default worker budget should be 20 USD")
        os.environ["XUEJIAO_TWIN_WORKER_MAX_BUDGET_USD"] = "5"
        if default_worker_max_budget_usd() != 5.0:
            errors.append("worker budget env override should be honored")
    finally:
        if old_budget_env is None:
            os.environ.pop("XUEJIAO_TWIN_WORKER_MAX_BUDGET_USD", None)
        else:
            os.environ["XUEJIAO_TWIN_WORKER_MAX_BUDGET_USD"] = old_budget_env
    old_timeout_env = os.environ.pop(WORKER_TIMEOUT_ENV, None)
    try:
        if DEFAULT_WORKER_TIMEOUT_SECONDS != 3600 or default_worker_timeout_seconds() != 3600:
            errors.append("default worker timeout should be 3600 seconds")
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
    errors: list[str] = _schema_errors() + _semantic_errors() + _behavior_helper_errors()
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
        focus = supervisor_context.get("acceptance_focus", {})
        if not focus.get("last_mile") or not focus.get("current_item_acceptance_criteria"):
            errors.append("supervisor context should expose acceptance focus for the current item")
        skeleton = supervisor_context.get("review_skeleton", {})
        if skeleton.get("decision") != "<ACCEPTED_DONE|CONTINUE|NEEDS_HUMAN|FAILED>":
            errors.append("supervisor context should expose an undecided review skeleton")
        if not ledger_gaps(_goal(), _ledger()):
            errors.append("pending ledger should have gaps")
        if any(item["evidence"] for item in acceptance_evidence(_goal(), _ledger())):
            errors.append("empty ledger should not produce acceptance evidence")
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
            if fallback_context.get("ledger", {}).get("items", [{}])[0].get("scope") != "只覆盖 greenfield worker turn":
                errors.append("fallback yaml parser should preserve ledger item mapping fields")
            ledger_roundtrip = load_ledger(workspace)
            ledger_roundtrip["items"][0]["actual_evidence"] = ["第一行\n第二行"]
            ledger_roundtrip["items"][0]["next_action"] = "多行\n下一步"
            write_ledger(workspace, ledger_roundtrip)
            reloaded_ledger = load_ledger(workspace)
            if reloaded_ledger["items"][0].get("actual_evidence") != ["第一行\n第二行"]:
                errors.append("yaml fallback parser should preserve list block scalars")
            if reloaded_ledger["items"][0].get("next_action") != "多行\n下一步":
                errors.append("yaml fallback writer should round-trip multiline ledger values")
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

        legacy_json = _write_workspace(root / "legacy-json")
        (legacy_json / "feature_ledger.json").write_text("{}\n", encoding="utf-8")
        try:
            validate_workspace(legacy_json)
            errors.append("feature_ledger.json should fail even when feature_ledger.yaml exists")
        except WorkspaceError:
            pass

        nonzero_workspace = _write_workspace(root / "nonzero")
        nonzero_run = start_worker_turn(nonzero_workspace, "触发 worker 非零退出 fixture", runner=FakeRunner(returncode=1))
        if nonzero_run["outcome"] != "review_required":
            errors.append("nonzero worker exit should still require supervisor review")
        nonzero_state = load_state(nonzero_workspace)
        if nonzero_state.get("status") != "review_required":
            errors.append("nonzero worker exit should leave workspace in review_required state")
        try:
            start_worker_turn(nonzero_workspace, "不应跳过 review", runner=FakeRunner())
            errors.append("nonzero worker exit should still block next turn until review")
        except WorkspaceError:
            pass
        nonzero_health = health_workspace(nonzero_workspace, history_limit=5)
        if not any(item.get("flag") == "WORKER_RETURN_CODE_NONZERO" for item in nonzero_health.get("history_warnings", [])):
            errors.append("health should summarize historical nonzero worker exits")

        stale_workspace = _write_workspace(root / "stale-running")
        stale_state = load_state(stale_workspace)
        stale_state["status"] = "worker_running"
        stale_state["current_run_id"] = "run-stale"
        stale_state["next_instruction"] = "fixture instruction from abandoned worker"
        write_state(stale_workspace, stale_state)
        stale_runner = FakeRunner()
        stale_run = start_worker_turn(stale_workspace, "恢复 stale worker_running", runner=stale_runner)
        if stale_run.get("outcome") != "review_required" or len(stale_runner.calls) != 1:
            errors.append("stale worker_running without artifacts should allow a fresh worker turn")
        stale_after = load_state(stale_workspace)
        if stale_after.get("current_run_id") == "run-stale" or stale_after.get("status") != "review_required":
            errors.append("stale worker_running recovery should replace the abandoned run id")
        if (stale_workspace / "runs" / "run-stale").exists():
            errors.append("stale worker_running detection should not create an empty abandoned run directory")

        nested_root = root / "nested-host"
        nested_workspace = _write_workspace(nested_root / "plans" / "feature")
        git_init = subprocess.run(["git", "init"], cwd=nested_root, capture_output=True, text=True, check=False)
        if git_init.returncode == 0:
            nested_runner = FakeRunner()
            nested_run = start_worker_turn(nested_workspace, "嵌套 workspace fixture", runner=nested_runner)
            if nested_runner.calls[-1].get("cwd") != nested_root.resolve():
                errors.append("worker should run from git root when workspace is nested")
            (nested_root / "app.py").write_text("print('dirty')\n", encoding="utf-8")
            try:
                apply_supervisor_review(nested_workspace, nested_run["run_id"], _review("ACCEPTED_DONE"))
                errors.append("ACCEPTED_DONE should inspect git root when workspace is nested")
            except WorkspaceError:
                pass

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
        run = start_worker_turn(workspace, "推进 ledger item F1", runner=runner)
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
        if f"# {WORKER_PERSONA_PATH}" not in prompt or "fixture-goal" not in prompt or "推进 ledger item F1" not in prompt:
            errors.append("worker prompt missing persona/goal/supervisor-authored instruction")
        if "supervisor persona" in prompt:
            errors.append("worker prompt should not include supervisor persona")
        if "acceptance_focus.json" in prompt or "worker completion contract" in prompt:
            errors.append("worker prompt should not include harness-generated completion contracts")
        if "supervisor_session_id" in json.dumps(run):
            errors.append("run artifact should not contain supervisor session fields")
        current_path = workspace / "CURRENT.md"
        current_text = current_path.read_text(encoding="utf-8")
        if "Status: review_required" not in current_text or f"Current item: {run['run_id']}" in current_text:
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
        if context.get("review_skeleton", {}).get("decision") != "<ACCEPTED_DONE|CONTINUE|NEEDS_HUMAN|FAILED>":
            errors.append("review context should not preselect a decision")
        if not context.get("run_health") or "next_instruction_guidance" not in context:
            errors.append("review context should expose run health and next instruction guidance")
        budget_run = read_json(workspace / "runs" / run["run_id"] / "run.json")
        events_path = workspace / "runs" / run["run_id"] / "events.jsonl"
        budget_run["events_ref"] = str(events_path)
        budget_run["evidence"]["quality_flags"] = []
        events_path.write_text('{"type":"result","subtype":"error_max_budget_usd","is_error":true}\n', encoding="utf-8")
        from .util import write_json
        write_json(workspace / "runs" / run["run_id"] / "run.json", budget_run)
        budget_context = build_review_context(workspace, run["run_id"])
        if "WORKER_MAX_BUDGET_EXCEEDED" not in budget_context.get("run_health", {}).get("quality_flags", []):
            errors.append("review context should infer budget-exceeded flag from run events")
        health_state_text = state_path.read_text(encoding="utf-8")
        health_run_text = (workspace / "runs" / run["run_id"] / "run.json").read_text(encoding="utf-8")
        health = health_workspace(workspace, run_id=run["run_id"], events_tail=1, history_limit=5)
        if health.get("current_run", {}).get("run_id") != run["run_id"]:
            errors.append("health should report the requested current run")
        if "WORKER_MAX_BUDGET_EXCEEDED" not in health.get("run_health", {}).get("quality_flags", []):
            errors.append("health should infer budget-exceeded flag from run events")
        events_path.write_text(
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": str(PERSONAS_DIR / "worker-persona.md"), "content": "polluted"}},
                    ]
                },
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        persona_worker_context = build_review_context(workspace, run["run_id"])
        if "PERSONA_SOURCE_WRITE" not in persona_worker_context.get("run_health", {}).get("quality_flags", []):
            errors.append("review context should flag worker writes inside $DEV_RULES/personas")
        if not persona_worker_context.get("run_health", {}).get("requires_attention"):
            errors.append("worker writes inside $DEV_RULES/personas should require attention")
        for write_command in (
            "printf polluted > $DEV_RULES/personas/worker-persona.md",
            "printf polluted | tee $DEV_RULES/personas/worker-persona.md",
        ):
            events_path.write_text(
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash", "input": {"command": write_command}},
                        ]
                    },
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            persona_bash_context = build_review_context(workspace, run["run_id"])
            if "PERSONA_SOURCE_WRITE" not in persona_bash_context.get("run_health", {}).get("quality_flags", []):
                errors.append("review context should flag worker shell writes inside $DEV_RULES/personas")
        for read_only_command in (
            "grep -R PERSONA_SOURCE_WRITE $DEV_RULES/personas",
            "cat $DEV_RULES/personas/worker-persona.md > /tmp/persona-copy.txt",
            "sed -n '1p' $DEV_RULES/personas/worker-persona.md",
        ):
            events_path.write_text(
                json.dumps({
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash", "input": {"command": read_only_command}},
                        ]
                    },
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            persona_read_context = build_review_context(workspace, run["run_id"])
            if "PERSONA_SOURCE_WRITE" in persona_read_context.get("run_health", {}).get("quality_flags", []):
                errors.append("review context should not flag read-only shell commands inside $DEV_RULES/personas")
        write_json(workspace / "runs" / run["run_id"] / "run.json", budget_run)
        events_path.write_text('{"type":"result","subtype":"error_max_budget_usd","is_error":true}\n', encoding="utf-8")
        if health.get("events_tail_summary", {}).get("events", [{}])[-1].get("subtype") != "error_max_budget_usd":
            errors.append("health should summarize events tail")
        if state_path.read_text(encoding="utf-8") != health_state_text or (workspace / "runs" / run["run_id"] / "run.json").read_text(encoding="utf-8") != health_run_text:
            errors.append("health should not rewrite state or run artifacts")
        write_json(workspace / "runs" / run["run_id"] / "run.json", run)
        running_workspace = _write_workspace(root / "running")
        running_state = load_state(running_workspace)
        running_state["status"] = "worker_running"
        running_state["current_run_id"] = "run-pending"
        running_state["next_instruction"] = "fixture instruction still running"
        write_state(running_workspace, running_state)
        running_dir = running_workspace / "runs" / "run-pending"
        running_dir.mkdir(parents=True, exist_ok=True)
        (running_dir / "pending.json").write_text(
            '{"schema_version":1,"run_id":"run-pending","started_at":"2026-05-11T00:00:00Z","status":"worker_running"}\n',
            encoding="utf-8",
        )
        (running_dir / "events.jsonl").write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"fixture running"}]}}\n', encoding="utf-8")
        running_health = health_workspace(running_workspace, history_limit=5)
        if running_health.get("current_run", {}).get("outcome") != "worker_running":
            errors.append("health should expose pending current_run while worker is still running")
        running_warning_flags = [item.get("flag") for item in running_health.get("history_warnings", [])]
        if "CURRENT_RUN_MISSING" in running_warning_flags or "PENDING_RUN_MISSING_ARTIFACT" in running_warning_flags:
            errors.append("worker_running without run artifact should not be reported as missing while current pending marker exists")
        if running_health.get("events_tail_summary", {}).get("events", [{}])[-1].get("type") != "assistant":
            errors.append("health should summarize live running events")
        if not str(running_health.get("artifact_paths", {}).get("run", "")).endswith("runs/run-pending/run.json"):
            errors.append("running health should expose the pending run artifact path")
        missing_workspace = _write_workspace(root / "missing-run")
        missing_state = load_state(missing_workspace)
        missing_state["status"] = "review_required"
        missing_state["current_run_id"] = "run-missing"
        write_state(missing_workspace, missing_state)
        missing_health = health_workspace(missing_workspace, history_limit=5)
        if not any(item.get("flag") == "CURRENT_RUN_MISSING" for item in missing_health.get("history_warnings", [])):
            errors.append("non-running state with missing run artifact should report CURRENT_RUN_MISSING")
        no_events_workspace = _write_workspace(root / "pending-no-events")
        no_events_state = load_state(no_events_workspace)
        no_events_state["status"] = "worker_running"
        no_events_state["current_run_id"] = "run-no-events"
        write_state(no_events_workspace, no_events_state)
        no_events_pending = no_events_workspace / "runs" / "run-no-events" / "pending.json"
        no_events_pending.parent.mkdir(parents=True, exist_ok=True)
        no_events_pending.write_text(
            '{"schema_version":1,"run_id":"run-no-events","started_at":"2026-05-11T00:00:00Z","status":"worker_running","instruction_hash":"abc"}\n',
            encoding="utf-8",
        )
        no_events_health = health_workspace(no_events_workspace, history_limit=5)
        if "WORKER_STARTED_NO_EVENTS" not in no_events_health.get("run_health", {}).get("quality_flags", []):
            errors.append("current pending worker without events should require process inspection")
        abandoned_workspace = _write_workspace(root / "abandoned-pending")
        abandoned_state = load_state(abandoned_workspace)
        abandoned_state["status"] = "worker_running"
        abandoned_state["current_run_id"] = "run-current"
        write_state(abandoned_workspace, abandoned_state)
        abandoned_pending = abandoned_workspace / "runs" / "run-abandoned" / "pending.json"
        abandoned_pending.parent.mkdir(parents=True, exist_ok=True)
        abandoned_pending.write_text(
            '{"schema_version":1,"run_id":"run-abandoned","started_at":"2026-05-11T00:00:00Z","status":"worker_running"}\n',
            encoding="utf-8",
        )
        abandoned_health = health_workspace(abandoned_workspace, history_limit=5)
        if not any(item.get("flag") == "ABANDONED_PENDING_RUN" and item.get("run_id") == "run-abandoned" for item in abandoned_health.get("history_warnings", [])):
            errors.append("health should report abandoned pending worker starts")
        if not abandoned_health.get("pending_runs"):
            errors.append("health should expose pending run markers")
        if any("instruction" in item for item in abandoned_health.get("pending_runs", [])):
            errors.append("pending run markers should not carry full next_instruction text")
        boundary_workspace = _write_workspace(root / "boundary")
        load_state(boundary_workspace)
        project_slug = str(boundary_workspace.parent.resolve()).replace("/", "-")
        transcript_dir = Path.home() / ".claude" / "projects" / project_slug
        transcript_dir.mkdir(parents=True, exist_ok=True)
        boundary_session = "fixture-supervisor-boundary"
        transcript_path = transcript_dir / f"{boundary_session}.jsonl"
        transcript_path.write_text(
            json.dumps({
                "timestamp": "2026-05-11T00:00:00Z",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": str(boundary_workspace / "CURRENT.md"), "content": "ok"}},
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": str(boundary_workspace.parent / "app.py"), "old_string": "a", "new_string": "b"}},
                    ]
                },
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        boundary_health = health_workspace(boundary_workspace, supervisor_session_id=boundary_session, history_limit=5)
        boundary_violations = boundary_health.get("supervisor_boundary_violations") or []
        if len(boundary_violations) != 1 or not str(boundary_violations[0].get("path", "")).endswith("app.py"):
            errors.append("health should report supervisor edits to host files outside the twin workspace")
        if "SUPERVISOR_BOUNDARY_VIOLATION" not in boundary_health.get("run_health", {}).get("quality_flags", []):
            errors.append("supervisor boundary violations should require attention")
        persona_workspace = _write_workspace(root / "persona-boundary")
        load_state(persona_workspace)
        persona_session = "fixture-supervisor-persona-boundary"
        persona_transcript = transcript_dir / f"{persona_session}.jsonl"
        persona_transcript.write_text(
            json.dumps({
                "timestamp": "2026-05-11T00:00:00Z",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": str(PERSONAS_DIR / "worker-persona.md"), "content": "polluted"}},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "printf polluted > $DEV_RULES/personas/supervisor-persona.md"}},
                    ]
                },
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        persona_health = health_workspace(persona_workspace, supervisor_session_id=persona_session, history_limit=5)
        persona_violations = persona_health.get("supervisor_boundary_violations") or []
        if not persona_violations or any(item.get("flag") != "PERSONA_SOURCE_WRITE" for item in persona_violations):
            errors.append("health should report supervisor writes inside $DEV_RULES/personas")
        if "PERSONA_SOURCE_WRITE" not in persona_health.get("run_health", {}).get("quality_flags", []):
            errors.append("persona source writes should require attention")
        fallback_workspace = _write_workspace(root / "boundary-fallback")
        load_state(fallback_workspace)
        fallback_session = "fixture-supervisor-boundary-fallback"
        fallback_transcript_dir = Path.home() / ".claude" / "projects" / "-tmp-fixture-other-project"
        fallback_transcript_dir.mkdir(parents=True, exist_ok=True)
        (fallback_transcript_dir / f"{fallback_session}.jsonl").write_text(
            json.dumps({
                "timestamp": "2026-05-11T00:00:00Z",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": str(fallback_workspace.parent / "host.py"), "old_string": "a", "new_string": "b"}},
                    ]
                },
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        fallback_health = health_workspace(fallback_workspace, supervisor_session_id=fallback_session, history_limit=5)
        fallback_violations = fallback_health.get("supervisor_boundary_violations") or []
        if len(fallback_violations) != 1 or not str(fallback_violations[0].get("transcript", "")).endswith(f"{fallback_session}.jsonl"):
            errors.append("health should find supervisor transcripts outside the host project slug")
        bad_contract_workspace = _write_workspace(root / "bad-contract")
        load_state(bad_contract_workspace)
        (bad_contract_workspace / "feature_ledger.yaml").write_text(
            """schema_version: 1
goal_id: fixture-goal
items:
  - id: F1
    deliverable: 完成 fixture 主流程
    scope: 只覆盖 greenfield worker turn
    covers_ac: [AC1, AC2]
    evidence_plan: [tests pass]
    actual_evidence:
      - path: docs/bugs/report.md
    depends_on: []
    status: completed
    next_action: ""
""",
            encoding="utf-8",
        )
        bad_contract_health = health_workspace(bad_contract_workspace, history_limit=5)
        if not bad_contract_health.get("degraded"):
            errors.append("health should return degraded report for invalid workspace contract")
        if not any(item.get("flag") == "WORKSPACE_CONTRACT_INVALID" for item in bad_contract_health.get("history_warnings", [])):
            errors.append("degraded health should expose workspace contract errors")
        if not bad_contract_health.get("run_health", {}).get("requires_attention"):
            errors.append("degraded health should require attention")
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.xuejiao_twin",
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
        health_cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.xuejiao_twin",
                "health",
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
        if health_cli.returncode != 0:
            errors.append(f"health --json should be accepted by CLI: {health_cli.stderr.strip()}")
        else:
            try:
                parsed_health = json.loads(health_cli.stdout)
            except json.JSONDecodeError as exc:
                errors.append(f"health --json should emit JSON: {exc}")
            else:
                if "run_health" not in parsed_health or "history_warnings" not in parsed_health:
                    errors.append("health --json should expose run_health and history_warnings")

        continue_review = _review("CONTINUE", gaps=["F1 missing"], actions=["fix_drift", "validate_more"])
        state = apply_supervisor_review(workspace, run["run_id"], continue_review)
        if state.get("status") != "continue" or state.get("next_instruction") != "继续 fixture":
            errors.append("CONTINUE review should store supervisor-authored next instruction unchanged")
        continued_run_artifact = read_json(workspace / "runs" / run["run_id"] / "run.json")
        if continued_run_artifact.get("outcome") != "continued":
            errors.append("CONTINUE review should mark reviewed run outcome as continued")

        second = start_worker_turn(workspace, "继续 F1", runner=runner)
        if not runner.calls[-1].get("session_id"):
            errors.append("second worker turn should resume saved worker session")
        if not second["worker"]["resume_used"]:
            errors.append("second run artifact should mark resume_used")
        apply_supervisor_review(workspace, second["run_id"], _review("CONTINUE"))

        reset_action_run = start_worker_turn(workspace, "测试显式 reset action", runner=runner)
        reset_action_state = apply_supervisor_review(
            workspace,
            reset_action_run["run_id"],
            _review("CONTINUE", actions=["reset_worker_session"]),
        )
        if reset_action_state.get("worker_session_id") is not None:
            errors.append("reset_worker_session action should clear worker_session_id")
        after_reset = start_worker_turn(workspace, "reset 后 fresh start", runner=runner)
        if runner.calls[-1].get("session_id"):
            errors.append("worker turn after reset_worker_session should not resume")
        apply_supervisor_review(workspace, after_reset["run_id"], _review("CONTINUE"))

        reset_runner = FakeRunner(session_lost_once=True)
        reset = start_worker_turn(workspace, "测试 session reset", runner=reset_runner)
        if len(reset_runner.calls) != 2:
            errors.append("session lost should retry fresh once")
        if reset_runner.calls[-1].get("session_id"):
            errors.append("session lost retry should be fresh")
        if reset["worker"]["resume_used"]:
            errors.append("fresh retry should not mark resume_used")
        apply_supervisor_review(workspace, reset["run_id"], _review("CONTINUE"))

        warning_retry_runner = FakeRunner(warning_only_once=True)
        warning_retry = start_worker_turn(workspace, "测试 warning-only resume", runner=warning_retry_runner)
        if len(warning_retry_runner.calls) != 2:
            errors.append("warning-only resume should retry fresh once")
        if warning_retry_runner.calls[-1].get("session_id"):
            errors.append("warning-only retry should be fresh")
        if warning_retry["worker"]["resume_used"]:
            errors.append("warning-only fresh retry should not mark resume_used")
        apply_supervisor_review(workspace, warning_retry["run_id"], _review("CONTINUE"))

        needs_run = start_worker_turn(workspace, "等人确认 fixture", runner=runner)
        needs_human = _review("NEEDS_HUMAN", question="请确认 fixture")
        state = apply_supervisor_review(workspace, needs_run["run_id"], needs_human)
        if state.get("status") != "needs_human" or not state.get("needs_human"):
            errors.append("NEEDS_HUMAN review did not update state")
        status = status_workspace(workspace)
        if not status.get("needs_human") or status.get("current_run_id") != needs_run["run_id"]:
            errors.append("status should expose needs_human and the latest run")
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
        apply_supervisor_review(workspace, resumed["run_id"], _review("CONTINUE"))
        post_consume = start_worker_turn(workspace, "再跑一轮 fixture", runner=runner)
        if "human_response.json" in str(runner.calls[-1].get("prompt")):
            errors.append("consumed human_response.json should not be re-injected into worker prompt")
        apply_supervisor_review(workspace, post_consume["run_id"], _review("CONTINUE"))

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
        record_human_response(repeat_workspace, "改方向：先补 AC2 证据")
        repeat_after_respond = load_state(repeat_workspace)
        if repeat_after_respond.get("failure_streaks"):
            errors.append("respond should reset failure_streaks so the next round is not immediately escalated")

        weak_workspace = _write_workspace(root / "weak-output")
        weak_runner = FakeRunner(output_text="我会继续收敛 F6/AC1")
        weak_run = start_worker_turn(weak_workspace, "弱输出 fixture", runner=weak_runner)
        weak_flags = weak_run.get("evidence", {}).get("quality_flags", [])
        for flag in ("VALIDATION_NOT_REPORTED", "WORKER_OUTPUT_WEAK"):
            if flag not in weak_flags:
                errors.append(f"weak worker output should record {flag}")
        weak_context = build_review_context(weak_workspace, weak_run["run_id"])
        if not weak_context.get("run_health", {}).get("requires_attention"):
            errors.append("weak run health should require attention")
        if weak_context.get("review_skeleton", {}).get("decision") != "<ACCEPTED_DONE|CONTINUE|NEEDS_HUMAN|FAILED>":
            errors.append("weak run context should not preselect a decision")

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

        dirty_root = root / "dirty-host"
        dirty_workspace = _write_workspace(dirty_root, completed=True)
        git_init = subprocess.run(["git", "init"], cwd=dirty_root, capture_output=True, text=True, check=False)
        if git_init.returncode == 0:
            (dirty_root / "app.py").write_text("print('dirty')\n", encoding="utf-8")
            dirty_runner = FakeRunner()
            dirty_run = start_worker_turn(dirty_workspace, "final", runner=dirty_runner)
            try:
                apply_supervisor_review(dirty_workspace, dirty_run["run_id"], _review("ACCEPTED_DONE"))
                errors.append("ACCEPTED_DONE should fail when host repo has uncommitted non-workspace changes")
            except WorkspaceError:
                pass
            allowed_dirty = _review("ACCEPTED_DONE", actions=["allow_uncommitted_evidence"])
            state = apply_supervisor_review(dirty_workspace, dirty_run["run_id"], allowed_dirty)
            if state.get("status") != "accepted_done":
                errors.append("allow_uncommitted_evidence should permit explicit dirty-host handoff")

        done = _review("ACCEPTED_DONE")
        state = apply_supervisor_review(done_workspace, done_run["run_id"], done)
        if state.get("status") != "accepted_done":
            errors.append("ACCEPTED_DONE review did not update state")
        if state.get("current_item_id"):
            errors.append("ACCEPTED_DONE state should clear current_item_id once no items remain")
        run_artifact = read_json(done_workspace / "runs" / done_run["run_id"] / "run.json")
        if run_artifact.get("outcome") != "accepted_done":
            errors.append("accepted run artifact outcome not updated")
        status = status_workspace(done_workspace)
        if status.get("status") != "accepted_done" or status.get("remaining_gaps"):
            errors.append("status_workspace did not report clean accepted_done")
        if status.get("current_item_id"):
            errors.append("status_workspace should not surface a completed item as current after ACCEPTED_DONE")
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
