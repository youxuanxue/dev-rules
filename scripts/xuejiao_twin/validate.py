from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .claude_runner import ClaudeRunResult
from .initializer import init_workspace
from .privacy import assert_no_private_leak
from .evidence import classify_risk
from .runtime import run_workspace, write_human_response
from .util import read_json, write_json

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_schema(name: str) -> dict[str, Any]:
    return read_json(SCHEMAS_DIR / name)


def _check_type(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches_type(value, item) for item in expected):
            errors.append(f"{path}: expected one of {expected}, got {type(value).__name__}")
            return
    elif isinstance(expected, str) and not _matches_type(value, expected):
        errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}: missing {required}")
        props = schema.get("properties", {})
        for key, child in props.items():
            if key in value:
                _check_type(value[key], child, f"{path}.{key}", errors)
        additional = schema.get("additionalProperties", True)
        if isinstance(additional, dict):
            for key, child_value in value.items():
                if key not in props:
                    _check_type(child_value, additional, f"{path}.{key}", errors)
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _check_type(item, item_schema, f"{path}[{index}]", errors)
    if isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: shorter than minLength")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_schema(value: Any, schema_name: str) -> list[str]:
    errors: list[str] = []
    _check_type(value, _load_schema(schema_name), "$", errors)
    return errors


def validate_artifact(path: Path, schema_name: str) -> list[str]:
    value = read_json(path)
    return validate_schema(value, schema_name) + [f"privacy leak: {leak}" for leak in assert_no_private_leak(value)]


def fixture_persona() -> dict[str, Any]:
    return {
        "schema_version": "fixture",
        "core_persona": {
            "mission": "作为 OPC 模式下的人类分身，监督 code agent 聚焦核心、要求证据、自动化固化。",
            "highest_priority_preferences": ["乔布斯偏好", "OPC 偏好"],
        },
        "decision_policy": {
            "start_task": ["先识别核心目标和验收标准"],
            "during_task": ["要求 diff summary 和验证证据"],
            "human_gates": ["架构、安全、数据、依赖、外部副作用停给真人"],
        },
    }


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, prompt: str, **kwargs: Any) -> ClaudeRunResult:
        role = "worker" if "worker code agent" in prompt else "supervisor"
        session_id = str(kwargs.get("session_id") or f"{role}-session")
        self.calls.append({
            "role": role,
            "session_id": str(kwargs.get("session_id") or ""),
            "dry_run": bool(kwargs.get("dry_run")),
            "allowed_tools": list(kwargs.get("allowed_tools") or []),
            "disallowed_tools": list(kwargs.get("disallowed_tools") or []),
            "permission_mode": str(kwargs.get("permission_mode") or ""),
        })
        if kwargs.get("dry_run"):
            return ClaudeRunResult(session_id=session_id, output_text="先 dry-run fixture", returncode=0, raw_events=[])
        if role == "supervisor":
            turn = sum(1 for call in self.calls if call["role"] == "supervisor")
            if turn == 1:
                output = {
                    "action": "continue",
                    "current_focus": "F-001",
                    "instruction": "读取项目状态并运行 git status --short，返回验证证据。",
                    "feature_updates": [],
                    "reason": "开始第一个 feature",
                }
            elif turn == 2:
                output = {
                    "action": "continue",
                    "current_focus": "F-001",
                    "instruction": "继续补充验证证据并回传。",
                    "feature_updates": [],
                    "reason": "继续收集验证证据",
                }
            else:
                output = {
                    "action": "stop",
                    "current_focus": "F-001",
                    "instruction": "",
                    "feature_updates": [{"id": "F-001", "status": "completed", "validation_evidence": ["npm test", "./scripts/preflight.sh"]}],
                    "reason": "fixture evidence complete",
                }
            return ClaudeRunResult(session_id="supervisor-session", output_text=json_dumps(output), returncode=0, raw_events=[])
        return ClaudeRunResult(
            session_id="worker-session",
            output_text="changed files: none\nvalidation: git status --short\nnpm test\n./scripts/preflight.sh",
            returncode=0,
            raw_events=[],
        )


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def run_fixture_validation() -> list[str]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="xuejiao-twin-") as tmp:
        tmp_path = Path(tmp)
        persona_path = tmp_path / "persona.json"
        project_root = tmp_path / "fixture-project"
        project_root.mkdir()
        goal_path = tmp_path / "goal.yaml"
        goal_path.write_text(
            (FIXTURES_DIR / "goal.yaml").read_text(encoding="utf-8").replace(
                "project_root: /tmp/xuejiao-twin-fixture-project",
                f"project_root: {project_root}",
            ),
            encoding="utf-8",
        )
        write_json(persona_path, fixture_persona())
        workspace = init_workspace(goal_path, persona_path, out=tmp_path / "workspace")
        gitignore = (project_root / ".gitignore").read_text(encoding="utf-8")
        if ".xuejiao-twin*" not in gitignore.splitlines():
            errors.append("fixture init did not add .xuejiao-twin* to .gitignore")
        ledger = read_json(workspace / "feature_ledger.json")
        errors.extend(validate_schema(ledger, "xuejiao_twin.ledger.schema.json"))
        dry_run = run_workspace(workspace, mode="dry-run", out=tmp_path / "dry-run.json", runner=FakeRunner())
        errors.extend(validate_schema(dry_run, "xuejiao_twin.run.schema.json"))
        errors.extend(f"dry-run privacy leak: {leak}" for leak in assert_no_private_leak(dry_run))

        runner = FakeRunner()
        run = run_workspace(workspace, mode="supervised-normal", out=tmp_path / "run.json", runner=runner)
        errors.extend(validate_schema(run, "xuejiao_twin.run.schema.json"))
        errors.extend(f"run privacy leak: {leak}" for leak in assert_no_private_leak(run))
        ledger = read_json(workspace / "feature_ledger.json")
        errors.extend(validate_schema(ledger, "xuejiao_twin.ledger.schema.json"))
        if run.get("outcome") != "completed":
            errors.append(f"fixture outcome: expected completed, got {run.get('outcome')}")
        if run.get("metrics", {}).get("supervisor_turns", 0) < 2:
            errors.append("fixture did not run multiple supervisor turns")
        if run.get("metrics", {}).get("worker_turns", 0) < 2:
            errors.append("fixture did not run multiple worker turns")
        review = run.get("human_review", {})
        if not isinstance(review, dict):
            errors.append("fixture run missing human_review object")
        else:
            actions = review.get("suggested_actions", [])
            if not isinstance(actions, list) or len(actions) < 4:
                errors.append("fixture run missing suggested human actions")
        if not any(call["role"] == "supervisor" and call["session_id"] == "supervisor-session" for call in runner.calls):
            errors.append("fixture did not resume supervisor session")
        if not any(call["role"] == "worker" and call["session_id"] == "worker-session" for call in runner.calls):
            errors.append("fixture did not resume worker session")
        worker_calls = [call for call in runner.calls if call["role"] == "worker"]
        if not worker_calls or worker_calls[0]["allowed_tools"] != ["Read", "Edit", "Write", "Bash"]:
            errors.append("fixture worker did not receive bypass-like allowed tools")
        if not worker_calls or "Bash(git push --force *)" not in worker_calls[0]["disallowed_tools"]:
            errors.append("fixture worker did not receive force-push disallowed tool")
        if not worker_calls or "Bash(git reset --hard *)" not in worker_calls[0]["disallowed_tools"]:
            errors.append("fixture worker did not receive destructive git disallowed tool")
        if not worker_calls or "Bash(dropdb *)" not in worker_calls[0]["disallowed_tools"]:
            errors.append("fixture worker did not receive database drop disallowed tool")
        if any(feature.get("id") == "F-001" and feature.get("status") == "completed" for feature in ledger.get("features", [])) is False:
            errors.append("fixture ledger did not mark F-001 completed")
        if "turn 2" not in (workspace / "progress.md").read_text(encoding="utf-8"):
            errors.append("fixture progress did not record multiple turns")
        if classify_risk("不新增依赖，禁止 force push，do not production deploy"):
            errors.append("risk classifier flagged negated risk markers")
        if "force push" not in classify_risk("agent requested force push"):
            errors.append("risk classifier missed force push")

        write_human_response(
            workspace,
            action="defer_feature",
            feature_id="F-001",
            note="fixture defer",
        )
        deferred_run = run_workspace(workspace, mode="supervised-normal", out=tmp_path / "run-defer.json", runner=runner)
        if deferred_run.get("outcome") not in {"completed", "needs_human", "no_progress", "failed_validation"}:
            errors.append(f"fixture defer run unexpected outcome: {deferred_run.get('outcome')}")
    return errors


def validate_run_dir(path: Path) -> list[str]:
    run_path = path / "run.json" if path.is_dir() else path
    if not run_path.exists():
        return [f"missing run artifact: {run_path}"]
    return validate_artifact(run_path, "xuejiao_twin.run.schema.json")
