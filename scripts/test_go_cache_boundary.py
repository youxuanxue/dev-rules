#!/usr/bin/env python3
"""Behavioral tests for the local Go cache boundary helpers."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.go_cache_boundary.check import CheckResult, check_boundary
from scripts.go_cache_boundary.goflags import merge_trimpath
from scripts.go_cache_boundary.install import InstallError, InstallRequest, plan_install
from scripts.go_cache_boundary.manifest import MANIFEST_NAME, QUOTA_BYTES, load_manifest


class MergeGoflagsTest(unittest.TestCase):
    def test_adds_exactly_one_trimpath_when_empty(self) -> None:
        self.assertEqual(merge_trimpath(""), "-trimpath")

    def test_preserves_existing_flags_and_dedups_trimpath(self) -> None:
        self.assertEqual(
            merge_trimpath("-gcflags=all=-dwarf=false -trimpath -trimpath"),
            "-gcflags=all=-dwarf=false -trimpath",
        )

    def test_does_not_treat_similar_tokens_as_trimpath(self) -> None:
        self.assertEqual(
            merge_trimpath("-trimpath=off"),
            "-trimpath=off -trimpath",
        )


class ManifestCheckTest(unittest.TestCase):
    def test_missing_manifest_is_not_installed_not_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = check_boundary(home=Path(tmp), probe=Mock(side_effect=AssertionError))
        self.assertEqual(result, CheckResult(installed=False, ok=True, problems=()))

    def test_quota_mismatch_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._write_manifest(home)
            probe = Mock(
                return_value={
                    "volume_uuid": "vol-1",
                    "encrypted": True,
                    "quota_bytes": QUOTA_BYTES // 2,
                    "reserve_bytes": 0,
                    "mounted": True,
                    "mount_path": "/Volumes/DevCache",
                }
            )
            result = check_boundary(home=home, probe=probe)
        self.assertTrue(result.installed)
        self.assertFalse(result.ok)
        self.assertTrue(any("quota" in problem for problem in result.problems))

    def test_reserve_present_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._write_manifest(home)
            probe = Mock(
                return_value={
                    "volume_uuid": "vol-1",
                    "encrypted": True,
                    "quota_bytes": QUOTA_BYTES,
                    "reserve_bytes": 1,
                    "mounted": True,
                    "mount_path": "/Volumes/DevCache",
                }
            )
            result = check_boundary(home=home, probe=probe)
        self.assertFalse(result.ok)
        self.assertTrue(any("reserve" in problem for problem in result.problems))

    def test_missing_guard_symlink_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            volume = home / "DevCache"
            self._write_manifest(home, volume=volume)
            (home / "Library/Caches/dev-go/build").unlink()
            (home / "Library/Caches/dev-go/build").mkdir()
            probe = Mock(
                return_value={
                    "volume_uuid": "vol-1",
                    "encrypted": True,
                    "quota_bytes": QUOTA_BYTES,
                    "reserve_bytes": 0,
                    "mounted": True,
                    "mount_path": str(volume),
                }
            )
            result = check_boundary(
                home=home,
                probe=probe,
                go_env={"GOCACHE": str(home / "Library/Caches/dev-go/build"), "GOMODCACHE": str(home / "Library/Caches/dev-go/mod"), "GOTMPDIR": str(home / "Library/Caches/dev-go/tmp")}.get,
            )
        self.assertFalse(result.ok)
        self.assertTrue(any("guard" in problem for problem in result.problems))

    def test_go_env_mismatch_is_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            volume = home / "DevCache"
            self._write_manifest(home, volume=volume)
            probe = Mock(
                return_value={
                    "volume_uuid": "vol-1",
                    "encrypted": True,
                    "quota_bytes": QUOTA_BYTES,
                    "reserve_bytes": 0,
                    "mounted": True,
                    "mount_path": str(volume),
                }
            )
            result = check_boundary(
                home=home,
                probe=probe,
                go_env=lambda name: "/tmp/go-build" if name == "GOCACHE" else str(home / "Library/Caches/dev-go" / name.lower().replace("gomodcache", "mod").replace("gotmpdir", "tmp")),
            )
        self.assertFalse(result.ok)
        self.assertTrue(any("GOCACHE" in problem for problem in result.problems))

    def _write_manifest(self, home: Path, volume: Path | None = None) -> None:
        path = home / "Library" / "Application Support" / "dev-rules" / MANIFEST_NAME
        path.parent.mkdir(parents=True)
        mount_path = str(volume) if volume is not None else "/Volumes/DevCache"
        path.write_text(
            json.dumps(
                {
                    "container_uuid": "ctr-1",
                    "volume_uuid": "vol-1",
                    "mount_path": mount_path,
                    "quota_bytes": QUOTA_BYTES,
                    "guard_paths": {
                        "build": str(home / "Library/Caches/dev-go/build"),
                        "mod": str(home / "Library/Caches/dev-go/mod"),
                        "tmp": str(home / "Library/Caches/dev-go/tmp"),
                    },
                    "real_go_binary": "/opt/homebrew/bin/go",
                }
            ),
            encoding="utf-8",
        )
        if volume is not None:
            identity = volume / ".dev-go-vol-1"
            for name in ("build", "mod", "tmp"):
                (identity / name).mkdir(parents=True)
                guard = home / "Library/Caches/dev-go" / name
                guard.parent.mkdir(parents=True, exist_ok=True)
                if guard.exists() or guard.is_symlink():
                    continue
                guard.symlink_to(identity / name)
        self.assertEqual(load_manifest(home)["volume_uuid"], "vol-1")


class InstallerSafetyTest(unittest.TestCase):
    def test_without_apply_does_not_call_diskutil(self) -> None:
        diskutil = Mock(side_effect=AssertionError("diskutil must not run"))
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_install(
                InstallRequest(home=Path(tmp), apply=False),
                diskutil=diskutil,
            )
        self.assertFalse(plan.applied)
        self.assertEqual(plan.quota_bytes, QUOTA_BYTES)
        self.assertEqual(plan.reserve_bytes, 0)
        diskutil.assert_not_called()

    def test_foreign_go_shim_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shim = home / ".local/bin/go"
            shim.parent.mkdir(parents=True)
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            with self.assertRaises(InstallError) as raised:
                plan_install(InstallRequest(home=home, apply=False), diskutil=Mock())
        self.assertIn("foreign", str(raised.exception))

    def test_foreign_go_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shim = home / ".local/bin/go"
            shim.parent.mkdir(parents=True)
            target = home / "other-go"
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o755)
            shim.symlink_to(target)
            with self.assertRaises(InstallError) as raised:
                plan_install(InstallRequest(home=home, apply=False), diskutil=Mock())
        self.assertIn("foreign", str(raised.exception))

    def test_owned_dev_go_shim_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            dest_go = home / ".local/bin/dev-go"
            dest_go.parent.mkdir(parents=True)
            dest_go.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            dest_go.chmod(0o755)
            shim = home / ".local/bin/go"
            shim.symlink_to(dest_go)
            plan = plan_install(InstallRequest(home=home, apply=False), diskutil=Mock())
        self.assertFalse(plan.applied)

    def test_uuid_mismatch_fails_closed_and_does_not_apply(self) -> None:
        diskutil = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            existing = (
                home / "Library" / "Application Support" / "dev-rules" / MANIFEST_NAME
            )
            existing.parent.mkdir(parents=True)
            existing.write_text(
                json.dumps(
                    {
                        "container_uuid": "ctr-old",
                        "volume_uuid": "vol-old",
                        "mount_path": "/Volumes/DevCache",
                        "quota_bytes": QUOTA_BYTES,
                        "guard_paths": {},
                        "real_go_binary": "/opt/homebrew/bin/go",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(InstallError) as raised:
                plan_install(
                    InstallRequest(
                        home=home,
                        apply=True,
                        observed_volume_uuid="vol-new",
                    ),
                    diskutil=diskutil,
                )
        self.assertIn("uuid", str(raised.exception).lower())
        diskutil.assert_not_called()


class DevGoDoctorTest(unittest.TestCase):
    def test_passthrough_keeps_caller_cwd(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "global" / "bin" / "dev-go"
        with tempfile.TemporaryDirectory() as tmp:
            caller = Path(tmp) / "project"
            caller.mkdir()
            fake_go = Path(tmp) / "go"
            fake_go.write_text("#!/bin/sh\npwd\n", encoding="utf-8")
            fake_go.chmod(0o755)
            env = os.environ.copy()
            env["HOME"] = tmp
            env["DEV_RULES_ROOT"] = str(repo)
            env["DEV_GO_REAL_BIN"] = str(fake_go)
            completed = subprocess.run(
                [str(launcher), "version"],
                check=False,
                capture_output=True,
                text=True,
                cwd=caller,
                env=env,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            Path(completed.stdout.strip()).resolve(),
            caller.resolve(),
        )

    def test_doctor_reports_not_installed_without_manifest(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "global" / "bin" / "dev-go"
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["HOME"] = tmp
            env["DEV_RULES_ROOT"] = str(repo)
            completed = subprocess.run(
                [str(launcher), "doctor"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("not installed", completed.stdout)


VOLUME_PLIST = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>VolumeUUID</key>
  <string>vol-1</string>
  <key>VolumeName</key>
  <string>DevCache</string>
  <key>MountPoint</key>
  <string>/Volumes/DevCache</string>
  <key>FileVault</key>
  <true/>
  <key>CapacityQuota</key>
  <integer>68719476736</integer>
  <key>CapacityReserve</key>
  <integer>0</integer>
</dict>
</plist>
"""


class DiskutilProbeTest(unittest.TestCase):
    def test_parses_quota_encryption_and_mount_from_plist(self) -> None:
        from scripts.go_cache_boundary.probe import parse_volume_plist

        observed = parse_volume_plist(VOLUME_PLIST)
        self.assertEqual(observed["volume_uuid"], "vol-1")
        self.assertTrue(observed["encrypted"])
        self.assertEqual(observed["quota_bytes"], QUOTA_BYTES)
        self.assertEqual(observed["reserve_bytes"], 0)
        self.assertTrue(observed["mounted"])
        self.assertEqual(observed["mount_path"], "/Volumes/DevCache")

    def test_unmounted_volume_is_not_treated_as_mounted(self) -> None:
        from scripts.go_cache_boundary.probe import parse_volume_plist

        observed = parse_volume_plist(
            VOLUME_PLIST.replace(b"/Volumes/DevCache", b"")
        )
        self.assertFalse(observed["mounted"])
        self.assertEqual(observed["mount_path"], "")

    def test_live_probe_is_read_only(self) -> None:
        from scripts.go_cache_boundary.probe import probe_volume

        runner = Mock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=VOLUME_PLIST, stderr=b""
            )
        )
        observed = probe_volume("vol-1", run=runner)
        self.assertEqual(observed["volume_uuid"], "vol-1")
        args = runner.call_args.args[0]
        self.assertEqual(args[:3], ["diskutil", "info", "-plist"])
        self.assertNotIn("unlockVolume", args)
        self.assertNotIn("addVolume", args)


class MountHelperTest(unittest.TestCase):
    def test_already_mounted_matching_volume_is_noop(self) -> None:
        from scripts.go_cache_boundary.mount import ensure_mounted

        runner = Mock(side_effect=AssertionError("diskutil must not run"))
        result = ensure_mounted(
            volume_uuid="vol-1",
            mount_path="/Volumes/DevCache",
            passphrase="secret-pass",
            observed={
                "volume_uuid": "vol-1",
                "mounted": True,
                "mount_path": "/Volumes/DevCache",
            },
            run=runner,
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.noop)
        runner.assert_not_called()

    def test_unlock_sends_passphrase_only_on_stdin(self) -> None:
        from scripts.go_cache_boundary.mount import ensure_mounted

        def run(args, **kwargs):
            self.assertNotIn("secret-pass", args)
            self.assertEqual(kwargs.get("input"), b"secret-pass")
            self.assertIn("-stdinpassphrase", args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")

        result = ensure_mounted(
            volume_uuid="vol-1",
            mount_path="/Volumes/DevCache",
            passphrase="secret-pass",
            observed={"volume_uuid": "vol-1", "mounted": False, "mount_path": ""},
            run=run,
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.noop)

    def test_unmounted_without_passphrase_does_not_unlock(self) -> None:
        from scripts.go_cache_boundary.mount import ensure_mounted

        runner = Mock(side_effect=AssertionError("diskutil must not run"))
        result = ensure_mounted(
            volume_uuid="vol-1",
            mount_path="/Volumes/DevCache",
            passphrase=None,
            observed={"volume_uuid": "vol-1", "mounted": False, "mount_path": ""},
            run=runner,
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.noop)
        runner.assert_not_called()


class LaunchAgentPlistTest(unittest.TestCase):
    def test_one_shot_login_plist_has_no_secret_or_keepalive(self) -> None:
        from scripts.go_cache_boundary.launchagent import render_mount_plist

        plist = render_mount_plist(
            helper=["/usr/bin/python3", "-m", "scripts.go_cache_boundary.mount"],
            label="local.dev-rules.devcache-mount",
        )
        self.assertIn("local.dev-rules.devcache-mount", plist)
        self.assertIn("RunAtLoad", plist)
        self.assertNotIn("KeepAlive", plist)
        self.assertNotIn("passphrase", plist.lower())
        self.assertNotIn("secret", plist.lower())


class ColdWorkspaceTest(unittest.TestCase):
    def _write_installed_home(self, home: Path, volume: Path) -> None:
        support = home / "Library" / "Application Support" / "dev-rules"
        support.mkdir(parents=True)
        (support / MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "container_uuid": "ctr-1",
                    "volume_uuid": "vol-1",
                    "mount_path": str(volume),
                    "quota_bytes": QUOTA_BYTES,
                    "guard_paths": {
                        "build": str(home / "Library/Caches/dev-go/build"),
                        "mod": str(home / "Library/Caches/dev-go/mod"),
                        "tmp": str(home / "Library/Caches/dev-go/tmp"),
                    },
                    "real_go_binary": str(home / "bin" / "go"),
                }
            ),
            encoding="utf-8",
        )
        identity = volume / ".dev-go-vol-1"
        for name in ("build", "mod", "tmp"):
            (identity / name).mkdir(parents=True, exist_ok=True)
            guard = home / "Library/Caches/dev-go" / name
            guard.parent.mkdir(parents=True, exist_ok=True)
            if not guard.exists() and not guard.is_symlink():
                guard.symlink_to(identity / name)
        bindir = home / "bin"
        bindir.mkdir()
        plist = VOLUME_PLIST.replace(b"/Volumes/DevCache", str(volume).encode())
        diskutil = bindir / "diskutil"
        diskutil.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = info ] && [ \"$2\" = -plist ]; then\n"
            f"  printf '%s' '{plist.decode()}'\n"
            "  exit 0\n"
            "fi\n"
            "echo 'unexpected diskutil' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        diskutil.chmod(0o755)
        fake_go = bindir / "go"
        fake_go.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$GOCACHE" > "$HOME/gocache-path"\n',
            encoding="utf-8",
        )
        fake_go.chmod(0o755)

    def test_cold_workspace_is_task_owned_and_removed_on_exit(self) -> None:
        from scripts.go_cache_boundary.runtime import cold_workspace

        with tempfile.TemporaryDirectory() as tmp:
            volume = Path(tmp) / "DevCache"
            volume.mkdir()
            with cold_workspace(volume) as env:
                self.assertTrue(Path(env["GOCACHE"]).is_dir())
                self.assertTrue(str(volume) in env["GOCACHE"])
                self.assertNotEqual(env["GOCACHE"], str(volume / "build"))
                marker = Path(env["GOCACHE"]) / "marker"
                marker.write_text("x", encoding="utf-8")
            leftovers = list(volume.rglob("*"))
        self.assertEqual(leftovers, [])

    def test_dev_go_cold_runs_then_deletes_task_cache_when_installed(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "global" / "bin" / "dev-go"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            volume = home / "DevCache"
            volume.mkdir()
            self._write_installed_home(home, volume)
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["DEV_RULES_ROOT"] = str(repo)
            env["PATH"] = f"{home / 'bin'}:{env['PATH']}"
            completed = subprocess.run(
                [str(launcher), "cold", "env"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            recorded = (home / "gocache-path").read_text(encoding="utf-8").strip()
            self.assertTrue(recorded.startswith(str(volume)))
            self.assertFalse(Path(recorded).exists())
            self.assertEqual(
                [path.name for path in volume.iterdir() if path.name.startswith("cold-")],
                [],
            )

    def test_dev_go_cold_fails_closed_when_not_installed(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "global" / "bin" / "dev-go"
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["HOME"] = tmp
            env["DEV_RULES_ROOT"] = str(repo)
            completed = subprocess.run(
                [str(launcher), "cold", "env"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("not installed", completed.stderr + completed.stdout)

    def test_passthrough_holds_shared_lock_and_uses_guard_cache(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "global" / "bin" / "dev-go"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            volume = home / "DevCache"
            volume.mkdir()
            self._write_installed_home(home, volume)
            fake_go = home / "bin" / "go"
            fake_go.write_text(
                "#!/bin/sh\n"
                "python3 - <<'PY'\n"
                "import fcntl\n"
                "from pathlib import Path\n"
                "lock = Path.home() / 'Library/Caches/dev-go/activity.lock'\n"
                "try:\n"
                "    with lock.open('a+') as handle:\n"
                "        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "        print('unlocked')\n"
                "except BlockingIOError:\n"
                "    print('shared-held')\n"
                "PY\n"
                'printf "%s\\n" "$GOCACHE" > "$HOME/gocache-path"\n',
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["DEV_RULES_ROOT"] = str(repo)
            env["PATH"] = f"{home / 'bin'}:{env['PATH']}"
            completed = subprocess.run(
                [str(launcher), "version"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "shared-held")
            self.assertEqual(
                (home / "gocache-path").read_text(encoding="utf-8").strip(),
                str(home / "Library/Caches/dev-go/build"),
            )

    def test_passthrough_fails_closed_when_not_mounted_without_unlock(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "global" / "bin" / "dev-go"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            volume = home / "DevCache"
            volume.mkdir()
            self._write_installed_home(home, volume)
            plist = VOLUME_PLIST.replace(b"/Volumes/DevCache", b"")
            (home / "bin" / "diskutil").write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = info ] && [ \"$2\" = -plist ]; then\n"
                f"  printf '%s' '{plist.decode()}'\n"
                "  exit 0\n"
                "fi\n"
                "echo unlock-attempted >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["DEV_RULES_ROOT"] = str(repo)
            env["PATH"] = f"{home / 'bin'}:{env['PATH']}"
            completed = subprocess.run(
                [str(launcher), "version"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue(
                "not mounted" in completed.stderr or "mount mismatch" in completed.stderr
            )
            self.assertNotIn("unlock-attempted", completed.stderr)
            self.assertFalse((home / "gocache-path").exists())

    def test_passthrough_without_install_does_not_create_activity_lock(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        launcher = repo / "global" / "bin" / "dev-go"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fake_go = home / "go"
            fake_go.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            fake_go.chmod(0o755)
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["DEV_RULES_ROOT"] = str(repo)
            env["DEV_GO_REAL_BIN"] = str(fake_go)
            completed = subprocess.run(
                [str(launcher), "version"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse((home / "Library/Caches/dev-go/activity.lock").exists())


class ActivityLockTest(unittest.TestCase):
    def test_exclusive_recovery_waits_out_active_shared_holders(self) -> None:
        from scripts.go_cache_boundary.runtime import ActivityLock, RecoveryError

        with tempfile.TemporaryDirectory() as tmp:
            lock = ActivityLock(Path(tmp) / "activity.lock")
            with lock.shared():
                with self.assertRaises(RecoveryError):
                    lock.try_exclusive(timeout_seconds=0.05)


if __name__ == "__main__":
    unittest.main()
