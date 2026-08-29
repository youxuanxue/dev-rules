"""Non-secret local manifest for an installed DevCache boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_NAME = "go-cache-boundary.json"
QUOTA_BYTES = 64 * 1024**3


def manifest_path(home: Path) -> Path:
    return home / "Library" / "Application Support" / "dev-rules" / MANIFEST_NAME


def load_manifest(home: Path) -> dict[str, Any]:
    return json.loads(manifest_path(home).read_text(encoding="utf-8"))
