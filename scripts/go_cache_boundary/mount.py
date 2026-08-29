"""Idempotent DevCache mount helper. Passphrase never appears in argv."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable

Run = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class MountResult:
    ok: bool
    noop: bool


def ensure_mounted(
    *,
    volume_uuid: str,
    mount_path: str,
    passphrase: str | None,
    observed: dict[str, object],
    run: Run,
) -> MountResult:
    if (
        observed.get("mounted")
        and observed.get("volume_uuid") == volume_uuid
        and observed.get("mount_path") == mount_path
    ):
        return MountResult(ok=True, noop=True)

    if passphrase is None:
        return MountResult(ok=False, noop=False)

    completed = run(
        ["diskutil", "apfs", "unlockVolume", volume_uuid, "-stdinpassphrase"],
        input=passphrase.encode(),
        capture_output=True,
    )
    return MountResult(ok=completed.returncode == 0, noop=False)
