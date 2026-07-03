#!/usr/bin/env python3
"""Require an approval anchor (docs/approved/* or commit token) for high-risk changes.

Stage semantics — the check runs at three points with different knowledge:

  pre-commit   sees committed range (base...HEAD) + staged paths (--cached),
               but structurally CANNOT read the pending commit message
               (COMMIT_EDITMSG still holds the previous one at this stage).
               Staged-only findings therefore WARN and exit 0 — hard-failing
               would deadlock the legitimate token-in-message workflow.
  commit-msg   the only local stage where BOTH the staged paths and the
               pending message are known; install-hooks.sh wires this hook to
               pass --commit-msg-file "$1", and the check hard-fails here.
  CI / manual  committed range only (index clean) — hard gate, unchanged.

Staged paths are ignored while a merge is in progress (MERGE_HEAD exists):
merging upstream stages paths whose approval lives in their own history.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from preflight_common import (
    changed_paths,
    cli_fail,
    commit_text,
    compile_patterns,
    matches_any,
    parse_ini_sections,
    staged_paths,
)

DEFAULTS = {
    "high_risk_paths": [
        r"^migrations?/",
        r"^db/migrations?/",
        r"^schema/",
    ],
    "anchor_paths": [
        r"^docs/approved/.*\.md$",
    ],
    "anchor_tokens": [
        r"high[-_ ]risk[-_ ]anchor",
        r"approved[-_ ]anchor",
    ],
}


def merge_in_progress() -> bool:
    res = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


def read_pending_message(path: pathlib.Path) -> str:
    """Read a commit message file, dropping git's `#` comment lines
    (they never survive default --cleanup, so tokens there don't count)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(line for line in lines if not line.startswith("#"))


def evaluate(
    committed: list[str],
    staged: list[str],
    committed_text: str,
    pending_text: str | None,
    cfg: dict[str, list[str]],
) -> tuple[str, list[str]]:
    """Pure decision core. pending_text=None means "message unknowable at
    this stage" (pre-commit), NOT "empty message". Verdicts:

      pass-no-risky     no high-risk path in committed ∪ staged
      pass-anchor-path  docs/approved evidence in committed ∪ staged
      pass-token        anchor token in committed log or pending message
      warn-staged-only  risky paths exist only in the staged set and the
                        pending message is unknowable — caller warns, exit 0
      fail              anchor missing with full knowledge
    """
    high_re = compile_patterns(cfg["high_risk_paths"])
    anchor_re = compile_patterns(cfg["anchor_paths"])
    token_re = compile_patterns(cfg["anchor_tokens"], ignore_case=True)

    all_paths = list(dict.fromkeys([*committed, *staged]))
    risky = [p for p in all_paths if matches_any(p, high_re)]
    if not risky:
        return "pass-no-risky", []

    if any(matches_any(p, anchor_re) for p in all_paths):
        return "pass-anchor-path", risky

    text = committed_text if pending_text is None else f"{committed_text}\n{pending_text}"
    if matches_any(text, token_re):
        return "pass-token", risky

    committed_set = set(committed)
    if pending_text is None and not any(p in committed_set for p in risky):
        return "warn-staged-only", risky
    return "fail", risky


def _self_test() -> int:
    failures: list[str] = []

    cases = [
        # (name, committed, staged, committed_text, pending_text, expected)
        ("clean tree", ["src/app.py"], [], "feat: x", None, "pass-no-risky"),
        (
            "committed risky + doc anchor",
            ["migrations/001.sql", "docs/approved/db.md"],
            [],
            "feat: db",
            None,
            "pass-anchor-path",
        ),
        (
            "committed risky + token",
            ["migrations/001.sql"],
            [],
            "feat: db\n\nhigh-risk-anchor: docs/approved/db.md",
            None,
            "pass-token",
        ),
        ("committed risky bare", ["migrations/001.sql"], [], "feat: db", None, "fail"),
        # THE pre-commit blind spot: first commit on branch, base...HEAD empty.
        # Was "pass-no-risky" (silent green); must now surface as a warning.
        (
            "staged-only risky, message unknowable",
            [],
            ["migrations/001.sql"],
            "",
            None,
            "warn-staged-only",
        ),
        (
            "staged-only risky + pending token (commit-msg stage)",
            [],
            ["migrations/001.sql"],
            "",
            "feat: db\n\nHigh-Risk-Anchor: docs/approved/db.md",
            "pass-token",
        ),
        (
            "staged-only risky + pending message w/o token (commit-msg stage)",
            [],
            ["migrations/001.sql"],
            "",
            "feat: db",
            "fail",
        ),
        (
            "staged doc anchor covers staged risky",
            [],
            ["db/migrations/001.sql", "docs/approved/db.md"],
            "",
            None,
            "pass-anchor-path",
        ),
        (
            "committed risky bare + staged clean stays hard",
            ["schema/x.sql"],
            ["src/y.py"],
            "feat: schema",
            None,
            "fail",
        ),
    ]
    for name, committed, staged, ctext, pending, want in cases:
        got, _ = evaluate(committed, staged, ctext, pending, DEFAULTS)
        if got != want:
            failures.append(f"evaluate[{name}]: expected {want}, got {got}")

    failures += _git_fixture_failures()

    if failures:
        for f in failures:
            sys.stderr.write(f"  FAIL: {f}\n")
        return 1
    print("[check_high_risk_anchor] self-test OK")
    return 0


def _git_fixture_failures() -> list[str]:
    """Real-git regression for the blind spot: first commit on a branch has
    an empty base...HEAD range while the index carries the risky file."""
    import tempfile

    failures: list[str] = []
    # Scrub hook-context env (GIT_DIR/GIT_INDEX_FILE/…) so the fixture repo
    # is independent of any outer repo when this self-test runs under a hook.
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("GIT_")}
    cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as td:
            def git(*a: str) -> None:
                subprocess.run(
                    ["git", "-C", td, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            git("init", "-q")
            git("commit", "--allow-empty", "-m", "base")
            mig = pathlib.Path(td) / "migrations"
            mig.mkdir()
            (mig / "001.sql").write_text("create table t (x int);\n", encoding="utf-8")
            git("add", "migrations/001.sql")

            os.chdir(td)
            # base==HEAD emulates the branch's first commit under pre-commit
            if changed_paths("HEAD") != []:
                failures.append("fixture: expected empty base...HEAD on first commit")
            if staged_paths() != ["migrations/001.sql"]:
                failures.append(f"fixture: staged_paths() = {staged_paths()}")

            verdict, risky = evaluate(changed_paths("HEAD"), staged_paths(), commit_text("HEAD"), None, DEFAULTS)
            if (verdict, risky) != ("warn-staged-only", ["migrations/001.sql"]):
                failures.append(f"fixture: pre-commit verdict = {verdict}, risky = {risky}")

            msg = pathlib.Path(td) / "COMMIT_EDITMSG"
            msg.write_text("feat: db\n\n# comment lines must not count\n", encoding="utf-8")
            verdict, _ = evaluate(
                changed_paths("HEAD"), staged_paths(), commit_text("HEAD"), read_pending_message(msg), DEFAULTS
            )
            if verdict != "fail":
                failures.append(f"fixture: commit-msg w/o token verdict = {verdict}")

            msg.write_text("feat: db\n\nhigh-risk-anchor: docs/approved/db.md\n", encoding="utf-8")
            verdict, _ = evaluate(
                changed_paths("HEAD"), staged_paths(), commit_text("HEAD"), read_pending_message(msg), DEFAULTS
            )
            if verdict != "pass-token":
                failures.append(f"fixture: commit-msg with token verdict = {verdict}")
    finally:
        os.chdir(cwd)
        os.environ.update(saved)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Require approval anchor for high-risk changes.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/high-risk-anchor.conf",
        help="optional rules file with [high_risk_paths], [anchor_paths], [anchor_tokens]",
    )
    parser.add_argument(
        "--commit-msg-file",
        default=os.environ.get("PREFLIGHT_COMMIT_MSG_FILE", ""),
        help="pending commit message file (commit-msg hook passes $1; "
        "env PREFLIGHT_COMMIT_MSG_FILE is the fallback)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    cfg_path = pathlib.Path(args.rules)
    cfg = parse_ini_sections(cfg_path, DEFAULTS)

    if not cfg["high_risk_paths"]:
        if cfg_path.is_file():
            sys.stderr.write("[check_high_risk_anchor] config error: [high_risk_paths] is empty\n")
            return 2
        print("[check_high_risk_anchor] skip: no high-risk path patterns configured")
        return 0

    pending: str | None = None
    if args.commit_msg_file:
        msg_path = pathlib.Path(args.commit_msg_file)
        if not msg_path.is_file():
            sys.stderr.write(
                f"[check_high_risk_anchor] config error: commit message file not found: {msg_path}\n"
            )
            return 2
        pending = read_pending_message(msg_path)

    try:
        committed = changed_paths(args.base)
        staged = [] if merge_in_progress() else staged_paths()
        text = commit_text(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    verdict, risky = evaluate(committed, staged, text, pending, cfg)

    if verdict == "pass-no-risky":
        print("[check_high_risk_anchor] no high-risk paths changed (committed or staged)")
        return 0
    if verdict == "pass-anchor-path":
        print("[check_high_risk_anchor] anchor present (docs/approved evidence)")
        return 0
    if verdict == "pass-token":
        print("[check_high_risk_anchor] anchor present (commit token)")
        return 0
    if verdict == "warn-staged-only":
        print("[check_high_risk_anchor] WARN: staged high-risk changes have no approval anchor yet:")
        for p in risky:
            print(f"    - {p}")
        print(
            "[check_high_risk_anchor] WARN: pre-commit cannot read the pending commit message — "
            "stage docs/approved/*.md evidence or put an anchor token (e.g. 'high-risk-anchor') "
            "in the commit message; the commit-msg hook / CI will hard-fail without it"
        )
        return 0

    return cli_fail(
        "check_high_risk_anchor",
        "high-risk changes require approval anchor (docs/approved/* or commit token like 'high-risk-anchor')",
        *risky,
    )


if __name__ == "__main__":
    sys.exit(main())
