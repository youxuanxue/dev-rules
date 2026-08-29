"""Read-only diskutil probe. Never unlocks or creates a Volume."""

from __future__ import annotations

import plistlib
import subprocess
from typing import Callable

Run = Callable[..., subprocess.CompletedProcess]


def parse_volume_plist(data: bytes) -> dict[str, object]:
    parsed = plistlib.loads(data)
    mount_path = str(parsed.get("MountPoint") or "").strip()
    return {
        "volume_uuid": str(parsed.get("VolumeUUID") or ""),
        "encrypted": bool(parsed.get("FileVault") or parsed.get("Encrypted")),
        "quota_bytes": int(parsed.get("CapacityQuota") or 0),
        "reserve_bytes": int(parsed.get("CapacityReserve") or 0),
        "mounted": bool(mount_path),
        "mount_path": mount_path,
    }


def probe_volume(
    volume_uuid: str,
    *,
    run: Run = subprocess.run,
) -> dict[str, object]:
    completed = run(
        ["diskutil", "info", "-plist", volume_uuid],
        capture_output=True,
    )
    if completed.returncode != 0:
        return {
            "volume_uuid": "",
            "encrypted": False,
            "quota_bytes": 0,
            "reserve_bytes": 0,
            "mounted": False,
            "mount_path": "",
        }
    stdout = completed.stdout
    if isinstance(stdout, str):
        stdout = stdout.encode()
    return parse_volume_plist(stdout)
