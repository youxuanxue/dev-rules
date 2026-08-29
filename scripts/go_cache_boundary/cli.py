"""CLI for dest-go doctor / check / cold / run. Does not create or mutate a Volume."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from scripts.go_cache_boundary.check import check_boundary
from scripts.go_cache_boundary.goflags import merge_trimpath
from scripts.go_cache_boundary.manifest import load_manifest
from scripts.go_cache_boundary.mount import ensure_mounted
from scripts.go_cache_boundary.probe import probe_volume
from scripts.go_cache_boundary.runtime import (
    ActivityLock,
    activity_lock_path,
    cold_workspace,
    guard_cache_env,
)


def _print_drift(result) -> None:
    print("dev-go: Go cache boundary drift", file=sys.stderr)
    for problem in result.problems:
        print(f"  {problem}", file=sys.stderr)


def _run_cold(home: Path, go_args: list[str], result) -> int:
    if not result.installed:
        print("dev-go: Go cache boundary not installed", file=sys.stderr)
        return 1
    if not result.ok:
        _print_drift(result)
        return 1
    manifest = load_manifest(home)
    volume = Path(manifest["mount_path"])
    if not volume.is_dir():
        print("dev-go: DevCache is not mounted", file=sys.stderr)
        return 1
    user_pwd = os.environ.get("DEV_GO_USER_PWD") or os.getcwd()
    env = os.environ.copy()
    env["GOFLAGS"] = merge_trimpath(env.get("GOFLAGS", ""))
    with cold_workspace(volume) as cache_env:
        env.update(cache_env)
        completed = subprocess.run(
            [str(manifest["real_go_binary"]), *go_args],
            cwd=user_pwd,
            env=env,
            check=False,
        )
    return completed.returncode


def _resolve_real_go(home: Path, installed: bool) -> str:
    override = os.environ.get("DEV_GO_REAL_BIN")
    if override:
        return override
    if installed:
        return str(load_manifest(home)["real_go_binary"])
    homebrew = Path("/opt/homebrew/bin/go")
    if homebrew.is_file():
        return str(homebrew)
    return "go"


def _run_passthrough(home: Path, go_args: list[str], result) -> int:
    user_pwd = os.environ.get("DEV_GO_USER_PWD") or os.getcwd()
    real_go = _resolve_real_go(home, result.installed)
    if not result.installed:
        completed = subprocess.run(
            [real_go, *go_args],
            cwd=user_pwd,
            check=False,
        )
        return completed.returncode
    if not result.ok:
        _print_drift(result)
        return 1
    manifest = load_manifest(home)
    mounted = ensure_mounted(
        volume_uuid=str(manifest["volume_uuid"]),
        mount_path=str(manifest["mount_path"]),
        passphrase=None,
        observed=probe_volume(str(manifest["volume_uuid"])),
        run=subprocess.run,
    )
    if not mounted.ok:
        print("dev-go: DevCache is not mounted", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env["GOFLAGS"] = merge_trimpath(env.get("GOFLAGS", ""))
    env.update(guard_cache_env(manifest))
    with ActivityLock(activity_lock_path(home)).shared():
        completed = subprocess.run(
            [real_go, *go_args],
            cwd=user_pwd,
            env=env,
            check=False,
        )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev-go")
    parser.add_argument("command", choices=("doctor", "check", "cold", "run"))
    parser.add_argument("go_args", nargs="*")
    args = parser.parse_args(argv)
    home = Path.home()
    result = check_boundary(home=home, probe=probe_volume)
    if args.command == "cold":
        return _run_cold(home, args.go_args, result)
    if args.command == "run":
        return _run_passthrough(home, args.go_args, result)
    if not result.installed:
        print("dev-go: Go cache boundary not installed")
        return 0
    if result.ok:
        print("dev-go: Go cache boundary ok")
        return 0
    print("dev-go: Go cache boundary drift")
    for problem in result.problems:
        print(f"  {problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
