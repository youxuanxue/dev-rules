"""twin/worktree.py — per-workspace git worktree isolation for twin workers.

Root cause this fixes: every twin worker is spawned with `cwd = repo_root`,
i.e. the single shared primary checkout. N workers (or a worker + the
interactive session) sharing one checkout share one mutable HEAD/index/working
tree, so one `git checkout` moves HEAD out from under another and lands commits
on the wrong branch. The only real isolation is a worktree per worker.

Design:
  * One worktree per WORKSPACE (stable across the workspace's many resumed
    turns), NOT per turn — a per-turn worktree would discard the worker's
    uncommitted work between turns.
  * Placed as a SIBLING of the repo (`<parent>/<repo>-twin-<id>`) so a
    relative cross-repo path like sub2api's go.mod `replace => ../../new-api`
    resolves natively without a symlink (deep `.claude/worktrees/` nesting is
    what breaks it).
  * Bootstrapped via dev-rules/templates/worktree-bootstrap.sh (submodule init
    + project hook) so it is turnkey.
  * Gated by TWIN_WORKTREE_ISOLATION (default ON). Any failure falls back to
    the shared repo_root — isolation is a safety improvement and must never
    itself break a worker turn.
  * Removed on terminal workspace status (accepted_done / failed).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

BOOTSTRAP = Path(__file__).resolve().parents[2] / "templates" / "worktree-bootstrap.sh"


def isolation_enabled() -> bool:
    """Default ON. Set TWIN_WORKTREE_ISOLATION=0/false/off to opt out."""
    val = os.environ.get("TWIN_WORKTREE_ISOLATION", "1").strip().lower()
    return val not in {"0", "false", "off", "no"}


def _workspace_id(workspace: Path) -> str:
    """Stable, filesystem-safe id for a workspace: its basename plus a short
    hash of the absolute path (so two workspaces with the same basename in
    different dirs never collide on the same worktree)."""
    base = re.sub(r"[^A-Za-z0-9._-]", "-", workspace.name) or "ws"
    digest = hashlib.sha1(str(workspace.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def worktree_path(repo_root: Path, workspace: Path) -> Path:
    """Deterministic sibling worktree path for a workspace (pure function)."""
    repo_root = repo_root.resolve()
    return repo_root.parent / f"{repo_root.name}-twin-{_workspace_id(workspace)}"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    # Strip inherited git worktree-context env. When invoked from inside a git
    # hook (e.g. pre-commit running this selftest), git exports GIT_DIR /
    # GIT_INDEX_FILE / GIT_WORK_TREE; those bind our `git worktree` calls to the
    # outer repo's index and break add/remove. Each call already targets the
    # right repo via cwd, so these vars must not leak in.
    env = {k: v for k, v in os.environ.items()
           if k not in {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"}}
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, env=env,
        capture_output=True, text=True, check=False, timeout=120,
    )


def _is_registered_worktree(repo_root: Path, wt: Path) -> bool:
    proc = _run(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
    target = str(wt.resolve())
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            if Path(line[len("worktree "):].strip()).resolve() == Path(target):
                return True
    return False


def ensure_worktree(repo_root: Path, workspace: Path) -> Path:
    """Idempotently ensure a bootstrapped worktree exists for the workspace and
    return its path. On ANY failure, return repo_root unchanged (never break
    the worker turn over isolation)."""
    repo_root = repo_root.resolve()
    wt = worktree_path(repo_root, workspace)
    try:
        if _is_registered_worktree(repo_root, wt) and wt.exists():
            return wt
        if wt.exists():
            # Path exists but is not a registered worktree → unsafe to reuse.
            return repo_root
        add = _run(["git", "worktree", "add", "--detach", str(wt), "HEAD"], cwd=repo_root)
        if add.returncode != 0:
            sys.stderr.write(f"[twin.worktree] add failed, using shared checkout: {add.stderr.strip()}\n")
            return repo_root
        if BOOTSTRAP.exists():
            boot = _run(["bash", str(BOOTSTRAP), str(wt)])
            if boot.returncode != 0:
                sys.stderr.write(f"[twin.worktree] bootstrap failed: {boot.stderr.strip()}\n")
        return wt
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"[twin.worktree] ensure error, using shared checkout: {exc}\n")
        return repo_root


def remove_worktree(repo_root: Path, workspace: Path) -> None:
    """Best-effort removal of a workspace's worktree (terminal cleanup)."""
    repo_root = repo_root.resolve()
    wt = worktree_path(repo_root, workspace)
    try:
        if _is_registered_worktree(repo_root, wt):
            _run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo_root)
    except (OSError, subprocess.SubprocessError):
        pass


def worker_cwd(repo_root: Path, workspace: Path) -> Path:
    """The cwd a worker turn should run in: an isolated worktree when enabled,
    else the shared repo root."""
    if not isolation_enabled():
        return repo_root
    return ensure_worktree(repo_root, workspace)


# --------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile
    failed = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failed
        print(f"  {'PASS' if cond else 'FAIL'} {name}")
        if not cond:
            failed += 1

    # pure: id stability + collision resistance
    a = _workspace_id(Path("/x/ws-foo"))
    b = _workspace_id(Path("/y/ws-foo"))
    check("same-basename different-dir → different id", a != b)
    check("id is filesystem-safe", re.fullmatch(r"[A-Za-z0-9._-]+", a) is not None)

    # pure: env gate
    # empty string = "not an explicit off token" → default ON (only 0/false/off/no disable)
    for v, expect in [("1", True), ("0", False), ("false", False), ("off", False), ("", True), ("yes-please", True)]:
        os.environ["TWIN_WORKTREE_ISOLATION"] = v
        check(f"isolation_enabled({v!r})=={expect}", isolation_enabled() == expect)
    os.environ.pop("TWIN_WORKTREE_ISOLATION", None)

    # integration: real git repo, ensure → reuse → remove → fallback
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        for args in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                     ["git", "config", "user.name", "t"]):
            _run(args, cwd=repo)
        (repo / "f.txt").write_text("x\n")
        _run(["git", "add", "."], cwd=repo)
        _run(["git", "commit", "-q", "-m", "init"], cwd=repo)
        ws = Path(tmp) / "ws1"
        ws.mkdir()

        wt1 = ensure_worktree(repo, ws)
        check("ensure creates a sibling worktree", wt1 != repo and wt1.exists())
        check("worktree is sibling of repo", wt1.parent == repo.resolve().parent)
        check("worktree registered", _is_registered_worktree(repo, wt1))
        wt2 = ensure_worktree(repo, ws)
        check("ensure is idempotent (same path reused)", wt1 == wt2)
        remove_worktree(repo, ws)
        check("remove unregisters worktree", not _is_registered_worktree(repo, wt1))

    if failed:
        print(f"twin.worktree selftest: {failed} FAILED", file=sys.stderr)
        return 1
    print("ok: twin.worktree selftest passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
