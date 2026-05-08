from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .initializer import init_workspace
from .privacy import assert_no_private_leak
from .runtime import run_workspace
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


def run_fixture_validation() -> list[str]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="xuejiao-twin-") as tmp:
        tmp_path = Path(tmp)
        persona_path = tmp_path / "persona.json"
        write_json(persona_path, fixture_persona())
        workspace = init_workspace(FIXTURES_DIR / "goal.yaml", persona_path, out=tmp_path / "workspace")
        ledger = read_json(workspace / "feature_ledger.json")
        errors.extend(validate_schema(ledger, "xuejiao_twin.ledger.schema.json"))
        run = run_workspace(workspace, mode="dry-run", out=tmp_path / "run.json")
        errors.extend(validate_schema(run, "xuejiao_twin.run.schema.json"))
        errors.extend(f"run privacy leak: {leak}" for leak in assert_no_private_leak(run))
    return errors


def validate_run_dir(path: Path) -> list[str]:
    run_path = path / "run.json" if path.is_dir() else path
    if not run_path.exists():
        return [f"missing run artifact: {run_path}"]
    return validate_artifact(run_path, "xuejiao_twin.run.schema.json")
