#!/usr/bin/env python3
"""Behavior tests for the sync.sh --pull canonical-branch guard."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = REPO_ROOT / "sync.sh"
INSTALL_LAUNCH_AGENT = REPO_ROOT / "templates" / "install-launchagent.sh"


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


class SyncPullMainGuardTest(unittest.TestCase):
    def make_checkout(self, root: Path, branch: str) -> tuple[Path, dict[str, str]]:
        home = root / "home"
        origin = root / "origin.git"
        canonical = home / "Codes" / "dev-rules"
        home.mkdir(parents=True)

        self.assertEqual(run("git", "init", "--bare", str(origin), cwd=root).returncode, 0)
        self.assertEqual(run("git", "clone", str(origin), str(canonical), cwd=root).returncode, 0)
        self.assertEqual(run("git", "switch", "-c", branch, cwd=canonical).returncode, 0)
        self.assertEqual(run("git", "config", "user.name", "sync-test", cwd=canonical).returncode, 0)
        self.assertEqual(
            run("git", "config", "user.email", "sync-test@example.invalid", cwd=canonical).returncode,
            0,
        )

        for relative in ("rules", "commands", "global", "personas", ".cursor/skills"):
            directory = canonical / relative
            directory.mkdir(parents=True, exist_ok=True)
            (directory / ".keep").write_text("fixture\n", encoding="utf-8")
        (canonical / ".registered-projects").write_text("", encoding="utf-8")
        (canonical / ".local-projects").write_text("", encoding="utf-8")

        self.assertEqual(run("git", "add", ".", cwd=canonical).returncode, 0)
        self.assertEqual(run("git", "commit", "-m", "fixture", cwd=canonical).returncode, 0)
        self.assertEqual(run("git", "push", "-u", "origin", branch, cwd=canonical).returncode, 0)

        env = os.environ.copy()
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "CODEX_HOME",
            "ANTIGRAVITY_HOME",
        ):
            env.pop(name, None)
        env["HOME"] = str(home)
        env["DEV_RULES_HOME"] = str(canonical)
        return canonical, env

    def make_linked_main_checkout(self, root: Path) -> tuple[Path, dict[str, str]]:
        home = root / "home"
        origin = root / "origin.git"
        admin = root / "admin"
        canonical = home / "Codes" / "dev-rules"
        home.mkdir(parents=True)

        self.assertEqual(run("git", "init", "--bare", str(origin), cwd=root).returncode, 0)
        self.assertEqual(run("git", "clone", str(origin), str(admin), cwd=root).returncode, 0)
        self.assertEqual(run("git", "switch", "-c", "main", cwd=admin).returncode, 0)
        self.assertEqual(run("git", "config", "user.name", "sync-test", cwd=admin).returncode, 0)
        self.assertEqual(
            run("git", "config", "user.email", "sync-test@example.invalid", cwd=admin).returncode,
            0,
        )
        for relative in ("rules", "commands", "global", "personas", ".cursor/skills"):
            directory = admin / relative
            directory.mkdir(parents=True, exist_ok=True)
            (directory / ".keep").write_text("fixture\n", encoding="utf-8")
        (admin / ".registered-projects").write_text("", encoding="utf-8")
        (admin / ".local-projects").write_text("", encoding="utf-8")
        self.assertEqual(run("git", "add", ".", cwd=admin).returncode, 0)
        self.assertEqual(run("git", "commit", "-m", "fixture", cwd=admin).returncode, 0)
        self.assertEqual(run("git", "push", "-u", "origin", "main", cwd=admin).returncode, 0)
        self.assertEqual(run("git", "switch", "-c", "holding", cwd=admin).returncode, 0)
        canonical.parent.mkdir(parents=True)
        self.assertEqual(
            run("git", "worktree", "add", str(canonical), "main", cwd=admin).returncode,
            0,
        )

        env = os.environ.copy()
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "CODEX_HOME",
            "ANTIGRAVITY_HOME",
        ):
            env.pop(name, None)
        env["HOME"] = str(home)
        env["DEV_RULES_HOME"] = str(canonical)
        return canonical, env

    def test_pull_refuses_non_main_before_fanout(self) -> None:
        """Catches a non-main canonical checkout distributing stale rules."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical, env = self.make_checkout(root, "feature/stale-rules")

            result = run("bash", str(SYNC_SCRIPT), "--pull", cwd=canonical, env=env)

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("requires canonical branch main", result.stdout)
            self.assertFalse((Path(env["HOME"]) / ".cursor").exists(), result.stdout)

    def test_pull_allows_main_and_reaches_fanout(self) -> None:
        """Catches a guard that blocks the intended main-branch sync path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical, env = self.make_checkout(root, "main")

            result = run("bash", str(SYNC_SCRIPT), "--pull", cwd=canonical, env=env)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((Path(env["HOME"]) / ".cursor").is_dir(), result.stdout)

    def test_pull_allows_main_in_a_linked_worktree(self) -> None:
        """Catches treating a linked worktree's .git file as a non-repository."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical, env = self.make_linked_main_checkout(root)

            result = run("bash", str(SYNC_SCRIPT), "--pull", cwd=canonical, env=env)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((Path(env["HOME"]) / ".cursor").is_dir(), result.stdout)

    def test_status_recognizes_a_linked_canonical_worktree(self) -> None:
        """Catches status reporting a valid linked worktree as non-Git."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical, env = self.make_linked_main_checkout(root)

            result = run("bash", str(SYNC_SCRIPT), "--status", cwd=canonical, env=env)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("not a git checkout", result.stdout)

    def test_launchagent_keeps_the_selected_main_worktree_as_canonical(self) -> None:
        """Catches the agent invoking main's script but syncing another checkout."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            canonical = root / "dev-rules-main"
            fake_bin = root / "bin"
            home.mkdir()
            canonical.mkdir()
            fake_bin.mkdir()

            sync = canonical / "sync.sh"
            sync.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sync.chmod(0o755)
            (fake_bin / "uname").write_text("#!/bin/sh\necho Darwin\n", encoding="utf-8")
            (fake_bin / "launchctl").write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = list ]; then echo '1 0 local.dev-rules.sync'; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            (fake_bin / "uname").chmod(0o755)
            (fake_bin / "launchctl").chmod(0o755)

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["DEV_RULES_HOME"] = str(canonical)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = run("bash", str(INSTALL_LAUNCH_AGENT), cwd=REPO_ROOT, env=env)

            self.assertEqual(result.returncode, 0, result.stdout)
            plist_path = home / "Library" / "LaunchAgents" / "local.dev-rules.sync.plist"
            plist_bytes = plist_path.read_bytes()
            plist = plistlib.loads(re.sub(rb"<!--.*?-->", b"", plist_bytes, flags=re.DOTALL))
            self.assertEqual(
                plist["ProgramArguments"][2],
                f"{canonical}/sync.sh --pull",
            )
            self.assertIn("DEV_RULES_HOME", plist["EnvironmentVariables"])
            self.assertEqual(plist["EnvironmentVariables"]["DEV_RULES_HOME"], str(canonical))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that sync.sh --pull only fans out from canonical main."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run isolated Git fixture tests",
    )
    args = parser.parse_args()
    if not args.self_test:
        parser.error("--self-test is required")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyncPullMainGuardTest)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
