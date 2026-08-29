"""Install planner. Default is plan-only; --apply is a later approval gate."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scripts.go_cache_boundary.manifest import QUOTA_BYTES, load_manifest, manifest_path


def _owned_go_shim(shim: Path) -> bool:
    return shim.is_symlink() and Path(os.path.realpath(shim)).name == "dev-go"


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallRequest:
    home: Path
    apply: bool
    observed_volume_uuid: str | None = None


@dataclass(frozen=True)
class InstallPlan:
    applied: bool
    quota_bytes: int
    reserve_bytes: int


def plan_install(
    request: InstallRequest,
    *,
    diskutil: Callable[..., object],
) -> InstallPlan:
    shim = request.home / ".local" / "bin" / "go"
    if shim.exists() and not _owned_go_shim(shim):
        raise InstallError("foreign ~/.local/bin/go is present; refuse to overwrite")

    if manifest_path(request.home).is_file():
        existing = load_manifest(request.home)
        observed = request.observed_volume_uuid
        if observed and observed != existing["volume_uuid"]:
            raise InstallError("volume uuid does not match the existing manifest")

    if request.apply:
        raise InstallError("apply is a separate approval gate; not implemented here")

    return InstallPlan(
        applied=False,
        quota_bytes=QUOTA_BYTES,
        reserve_bytes=0,
    )
