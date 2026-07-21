"""Programmatic twin worktree lifecycle backed by the shared wtree.py engine."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


WTREE_SCRIPT_ENV = "TWIN_WTREE_SCRIPT"
SESSION_STATE_FILE = ".wtree-session.json"


class WorktreeIsolationError(RuntimeError):
    pass


def isolation_enabled() -> bool:
    """Default ON. Explicit opt-out is retained for read-only/local debugging."""
    value = os.environ.get("TWIN_WORKTREE_ISOLATION", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def _workspace_id(workspace: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "-", workspace.name) or "ws"
    digest = hashlib.sha1(str(workspace.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def worktree_path(repo_root: Path, workspace: Path) -> Path:
    repo_root = repo_root.resolve()
    return repo_root.parent / f"{repo_root.name}-twin-{_workspace_id(workspace)}"


def worktree_branch(workspace: Path) -> str:
    return f"twin/{_workspace_id(workspace)}"


def resolve_wtree_script() -> Path:
    explicit = os.environ.get(WTREE_SCRIPT_ENV, "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_file():
            return path
        raise WorktreeIsolationError(f"{WTREE_SCRIPT_ENV} does not point to wtree.py: {path}")

    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root / ".cursor" / "skills" / "git-worktree-submodule" / "scripts" / "wtree.py",
        repo_root.parent / ".cursor" / "skills" / "git-worktree-submodule" / "scripts" / "wtree.py",
        Path.home() / ".cursor" / "skills" / "git-worktree-submodule" / "scripts" / "wtree.py",
        Path.home() / ".claude" / "skills" / "git-worktree-submodule" / "scripts" / "wtree.py",
        Path.home() / ".codex" / "skills" / "git-worktree-submodule" / "scripts" / "wtree.py",
        Path.home() / "Codes" / "agent-skills" / "git-worktree-submodule" / "scripts" / "wtree.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise WorktreeIsolationError(
        "cannot find git-worktree-submodule/scripts/wtree.py; run dev-rules/sync.sh "
        f"or set {WTREE_SCRIPT_ENV}"
    )


def _run_wtree(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    script = resolve_wtree_script()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"}
    }
    try:
        completed = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeIsolationError(f"wtree.py failed to run: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise WorktreeIsolationError(f"wtree.py {' '.join(args[:1])} failed: {detail}")
    return completed


def _head_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    commit = completed.stdout.strip()
    if completed.returncode != 0 or not commit:
        raise WorktreeIsolationError(
            "cannot resolve the current approved HEAD for twin worktree isolation: "
            + (completed.stderr.strip() or "git rev-parse failed")
        )
    return commit


def _current_branch(target: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(target), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode not in {0, 1}:
        raise WorktreeIsolationError(
            f"cannot inspect twin worktree branch at {target}: "
            + (completed.stderr.strip() or "git symbolic-ref failed")
        )
    return completed.stdout.strip()


def ensure_worktree(repo_root: Path, workspace: Path) -> Path:
    """Create or reuse a branch-backed worktree from the current approved HEAD."""
    repo_root = repo_root.resolve()
    workspace = workspace.resolve()
    target = worktree_path(repo_root, workspace)
    result = _run_wtree(
        [
            "add",
            "--repo",
            str(repo_root),
            "--path",
            str(target),
            "--branch",
            worktree_branch(workspace),
            "--base",
            _head_commit(repo_root),
            "--reuse-branch",
            "--no-open-workspace",
            "--json",
        ]
    )
    try:
        payload = json.loads(result.stdout)
        resolved = Path(str(payload["worktree"])).resolve()
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise WorktreeIsolationError("wtree.py returned an invalid JSON worktree result") from exc
    if resolved != target.resolve():
        raise WorktreeIsolationError(f"wtree.py returned unexpected worktree {resolved}; expected {target}")
    expected_branch = worktree_branch(workspace)
    actual_branch = _current_branch(resolved)
    if actual_branch != expected_branch:
        label = actual_branch or "detached HEAD"
        raise WorktreeIsolationError(
            f"existing twin worktree {resolved} uses {label}, expected branch {expected_branch}; "
            "preserve any changes, then remove the stale worktree with wtree.py before retrying"
        )
    _run_wtree(["session-check", "--expected", str(resolved), "--json"], cwd=resolved)
    return resolved


def _has_unsaved_changes(target: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain=v1", "--untracked-files=all", "-z"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        return True
    entries = [entry for entry in completed.stdout.decode("utf-8", errors="replace").split("\0") if entry]
    for entry in entries:
        path = entry[3:] if len(entry) >= 4 else entry
        if path != SESSION_STATE_FILE:
            return True
    return False


def remove_worktree(repo_root: Path, workspace: Path) -> bool:
    """Remove only a clean, twin-owned worktree; preserve unsaved worker changes."""
    target = worktree_path(repo_root, workspace)
    if not target.exists():
        return False
    if _has_unsaved_changes(target):
        return False
    # wtree.py records .wtree-session.json in the worktree. The explicit
    # pre-check above proves it is the only dirty path before allowing force.
    _run_wtree(["remove", "--repo", str(repo_root.resolve()), "--path", str(target), "--force"])
    return True


def worker_cwd(
    repo_root: Path,
    workspace: Path,
    *,
    allow_shared_checkout_for_tests: bool = False,
) -> Path:
    if not isolation_enabled():
        if allow_shared_checkout_for_tests:
            return repo_root.resolve()
        raise WorktreeIsolationError(
            "TWIN_WORKTREE_ISOLATION=0 cannot run a writable worker in the shared checkout"
        )
    return ensure_worktree(repo_root, workspace)


def _selftest() -> int:
    import tempfile

    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(f"  {'PASS' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    first = _workspace_id(Path("/x/ws-foo"))
    second = _workspace_id(Path("/y/ws-foo"))
    check("workspace ids resist same-basename collisions", first != second)
    check("branch is stable", worktree_branch(Path("/x/ws-foo")) == f"twin/{first}")

    for value, expected in [("1", True), ("0", False), ("false", False), ("off", False), ("", True)]:
        os.environ["TWIN_WORKTREE_ISOLATION"] = value
        check(f"isolation_enabled({value!r})", isolation_enabled() == expected)
    os.environ["TWIN_WORKTREE_ISOLATION"] = "0"
    try:
        worker_cwd(Path("/repo"), Path("/workspace"))
        rejects_writable_shared_checkout = False
    except WorktreeIsolationError:
        rejects_writable_shared_checkout = True
    check("disabled isolation rejects writable workers", rejects_writable_shared_checkout)
    check(
        "fixture-only shared checkout bypass is explicit",
        worker_cwd(
            Path("/repo"),
            Path("/workspace"),
            allow_shared_checkout_for_tests=True,
        )
        == Path("/repo"),
    )
    os.environ.pop("TWIN_WORKTREE_ISOLATION", None)

    try:
        resolve_wtree_script()
        shared_engine_available = True
    except WorktreeIsolationError:
        shared_engine_available = False
    if not shared_engine_available:
        print("  SKIP shared engine integration (wtree.py is not installed beside this checkout)")
    else:
        with tempfile.TemporaryDirectory(prefix="twin-worktree-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            workspace = repo / ".twin" / "fixture"
            repo.mkdir()
            workspace.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "twin@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Twin Selftest"], cwd=repo, check=True)
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

            created = ensure_worktree(repo, workspace)
            check("shared engine creates expected path", created == worktree_path(repo, workspace))
            branch = subprocess.run(
                ["git", "-C", str(created), "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            check("twin worktree is branch-backed", branch == worktree_branch(workspace))
            check("ensure is idempotent", ensure_worktree(repo, workspace) == created)

            unsaved = created / "unsaved.txt"
            unsaved.write_text("keep me\n", encoding="utf-8")
            check("cleanup preserves unsaved changes", not remove_worktree(repo, workspace) and created.exists())
            unsaved.unlink()
            check("cleanup removes metadata-only worktree", remove_worktree(repo, workspace) and not created.exists())

            _run_wtree(
                [
                    "add",
                    "--repo",
                    str(repo),
                    "--path",
                    str(created),
                    "--detach",
                    "--base",
                    _head_commit(repo),
                    "--no-open-workspace",
                    "--json",
                ]
            )
            try:
                ensure_worktree(repo, workspace)
                rejects_wrong_branch = False
            except WorktreeIsolationError:
                rejects_wrong_branch = True
            check("reuse rejects a detached legacy worktree", rejects_wrong_branch)
            _run_wtree(["remove", "--repo", str(repo), "--path", str(created), "--force"])

    original = os.environ.get(WTREE_SCRIPT_ENV)
    os.environ[WTREE_SCRIPT_ENV] = "/definitely/missing/wtree.py"
    try:
        try:
            resolve_wtree_script()
            fail_closed = False
        except WorktreeIsolationError:
            fail_closed = True
        check("explicit missing helper fails closed", fail_closed)
    finally:
        if original is None:
            os.environ.pop(WTREE_SCRIPT_ENV, None)
        else:
            os.environ[WTREE_SCRIPT_ENV] = original

    if failures:
        print(f"twin.worktree selftest: {len(failures)} FAILED", file=sys.stderr)
        return 1
    print("ok: twin.worktree selftest passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
