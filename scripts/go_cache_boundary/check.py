"""Read-only checker. Missing manifest means not installed, not drift."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scripts.go_cache_boundary.manifest import QUOTA_BYTES, load_manifest, manifest_path

VolumeProbe = Callable[[str], dict[str, object]]


@dataclass(frozen=True)
class CheckResult:
    installed: bool
    ok: bool
    problems: tuple[str, ...]


def check_boundary(*, home: Path, probe: VolumeProbe) -> CheckResult:
    if not manifest_path(home).is_file():
        return CheckResult(installed=False, ok=True, problems=())

    manifest = load_manifest(home)
    observed = probe(str(manifest["volume_uuid"]))
    problems: list[str] = []
    if observed.get("volume_uuid") != manifest["volume_uuid"]:
        problems.append("uuid mismatch")
    if not observed.get("encrypted"):
        problems.append("encryption missing")
    if int(observed.get("quota_bytes") or 0) != QUOTA_BYTES:
        problems.append("quota drift")
    if int(observed.get("reserve_bytes") or 0) != 0:
        problems.append("reserve must be unset")
    return CheckResult(installed=True, ok=not problems, problems=tuple(problems))
