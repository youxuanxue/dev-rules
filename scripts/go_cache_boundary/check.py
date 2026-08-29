"""Read-only checker. Missing manifest means not installed, not drift."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scripts.go_cache_boundary.manifest import QUOTA_BYTES, load_manifest, manifest_path

VolumeProbe = Callable[[str], dict[str, object]]
GoEnv = Callable[[str], str]


@dataclass(frozen=True)
class CheckResult:
    installed: bool
    ok: bool
    problems: tuple[str, ...]


def _resolved_mount(mount_path: str) -> str:
    path = Path(mount_path)
    return os.path.realpath(path) if path.exists() else str(path)


def check_boundary(
    *,
    home: Path,
    probe: VolumeProbe,
    go_env: GoEnv | None = None,
) -> CheckResult:
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
    if not observed.get("mounted") or observed.get("mount_path") != manifest["mount_path"]:
        problems.append("mount mismatch")
    mount_root = _resolved_mount(str(manifest["mount_path"]))
    for name, raw in (manifest.get("guard_paths") or {}).items():
        path = Path(raw)
        if not path.is_symlink():
            problems.append(f"guard {name} missing")
            continue
        if not os.path.realpath(path).startswith(mount_root):
            problems.append(f"guard {name} target drift")
    if go_env is not None:
        expected = {
            "GOCACHE": str(manifest["guard_paths"]["build"]),
            "GOMODCACHE": str(manifest["guard_paths"]["mod"]),
            "GOTMPDIR": str(manifest["guard_paths"]["tmp"]),
        }
        for name, want in expected.items():
            if go_env(name) != want:
                problems.append(f"{name} drift")
    return CheckResult(installed=True, ok=not problems, problems=tuple(problems))
