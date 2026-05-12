from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .privacy import assert_no_private_leak
from .util import read_json

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    return read_json(SCHEMAS_DIR / name)


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
        if additional is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected property {key}")
        elif isinstance(additional, dict):
            for key, child_value in value.items():
                if key not in props:
                    _check_type(child_value, additional, f"{path}.{key}", errors)
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: more than maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _check_type(item, item_schema, f"{path}[{index}]", errors)
    if isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: does not match pattern")


def validate_schema(value: Any, schema_name: str) -> list[str]:
    errors: list[str] = []
    _check_type(value, load_schema(schema_name), "$", errors)
    return errors


def validate_artifact(path: Path, schema_name: str) -> list[str]:
    value = read_json(path)
    return validate_schema(value, schema_name) + [f"privacy leak: {leak}" for leak in assert_no_private_leak(value)]
