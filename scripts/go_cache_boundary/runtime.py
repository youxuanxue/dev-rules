"""Task-owned cold cache and activity lock. Recovery never races a live build."""

from __future__ import annotations

import fcntl
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class RecoveryError(RuntimeError):
    pass


def activity_lock_path(home: Path) -> Path:
    return home / "Library" / "Caches" / "dev-go" / "activity.lock"


def guard_cache_env(manifest: dict) -> dict[str, str]:
    guards = manifest["guard_paths"]
    return {
        "GOCACHE": str(guards["build"]),
        "GOMODCACHE": str(guards["mod"]),
        "GOTMPDIR": str(guards["tmp"]),
    }


class ActivityLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def shared(self) -> Iterator[None]:
        with self.path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def try_exclusive(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        with self.path.open("a+") as handle:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    return
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise RecoveryError(
                            "shared cache recovery blocked by active go"
                        ) from exc
                    time.sleep(0.01)


@contextmanager
def cold_workspace(volume: Path) -> Iterator[dict[str, str]]:
    task = volume / f"cold-{os.getpid()}-{uuid.uuid4().hex}"
    build = task / "build"
    mod = task / "mod"
    tmp = task / "tmp"
    for path in (build, mod, tmp):
        path.mkdir(parents=True)
    try:
        yield {
            "GOCACHE": str(build),
            "GOMODCACHE": str(mod),
            "GOTMPDIR": str(tmp),
        }
    finally:
        shutil.rmtree(task)
