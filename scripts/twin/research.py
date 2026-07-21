from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import RESEARCH_SCHEMA
from .privacy import assert_no_private_leak
from .schema_contract import validate_schema
from .util import read_yaml_like
from .workspace import WorkspaceError


def load_research(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise WorkspaceError(f"research artifact does not exist: {resolved}")
    try:
        value = read_yaml_like(resolved)
    except Exception as exc:
        raise WorkspaceError(f"cannot read research artifact {resolved}: {exc}") from exc
    errors = validate_schema(value, RESEARCH_SCHEMA)
    errors.extend(f"privacy leak: {leak}" for leak in assert_no_private_leak(value))
    if errors:
        raise WorkspaceError("research artifact errors: " + "; ".join(errors))
    return value
