"""Minimal stdlib JSON-Schema subset validator.

Supports the keywords used by dev-rules schemas (twin.*, review, skill):
type / enum / const / required / properties / additionalProperties /
items / minItems / maxItems / minimum / maximum / minLength / maxLength /
pattern / format(date) / allOf-if-then-else / oneOf.

Out of scope: $ref, $defs, dependentRequired, contains, anyOf, not.
Add only when a dev-rules schema actually needs it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def _condition_holds(value: Any, schema: dict[str, Any]) -> bool:
    # Treat the "if" subschema as a predicate: it holds when value satisfies
    # every keyword inside. Only the keywords actually used in our schemas
    # are inspected here (const, enum, type, properties.*.const).
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "type" in schema and not _matches_type(value, schema["type"]):
        return False
    if isinstance(value, dict):
        for key, sub in schema.get("properties", {}).items():
            if key in value and not _condition_holds(value[key], sub):
                return False
    return True


def _check(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches_type(value, item) for item in expected):
            errors.append(f"{path}: expected one of {expected}")
            return
    elif isinstance(expected, str) and not _matches_type(value, expected):
        errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
        return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")

    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}: missing required '{required}'")
        props = schema.get("properties", {})
        for key, child in props.items():
            if key in value:
                _check(value[key], child, f"{path}.{key}", errors)
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        elif isinstance(additional, dict):
            for key, child_value in value.items():
                if key not in props:
                    _check(child_value, additional, f"{path}.{key}", errors)

    elif isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: fewer than minItems ({schema['minItems']})")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: more than maxItems ({schema['maxItems']})")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _check(item, item_schema, f"{path}[{index}]", errors)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum ({schema['minimum']})")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum ({schema['maximum']})")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength ({schema['minLength']})")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength ({schema['maxLength']})")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: does not match pattern {pattern!r}")
        fmt = schema.get("format")
        if fmt == "date" and not _DATE_RE.match(value):
            errors.append(f"{path}: not an ISO date")

    for branch in schema.get("allOf", []):
        if not isinstance(branch, dict):
            continue
        cond = branch.get("if")
        then = branch.get("then")
        else_ = branch.get("else")
        if isinstance(cond, dict):
            if _condition_holds(value, cond):
                if isinstance(then, dict):
                    _check(value, then, path, errors)
            elif isinstance(else_, dict):
                _check(value, else_, path, errors)
        else:
            _check(value, branch, path, errors)

    for branch in schema.get("oneOf", []):
        if not isinstance(branch, dict):
            continue
        sub_errs: list[str] = []
        _check(value, branch, path, sub_errs)
        if not sub_errs:
            break
    else:
        if "oneOf" in schema:
            errors.append(f"{path}: no oneOf branch matched")


def validate(value: Any, schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    _check(value, schema, "$", errors)
    return errors
