#!/usr/bin/env python3
"""Require explicit notice token when public contract files are deleted.

Stage semantics mirror check_high_risk_anchor.py (same pre-commit blind spot:
a branch's first commit has an empty base...HEAD range, so staged contract
deletions used to pass silently until CI):

  pre-commit   sees committed deletions + staged deletions, but structurally
               CANNOT read the pending commit message — staged-only findings
               WARN and exit 0 (hard-failing would deadlock the legitimate
               token-in-message workflow).
  commit-msg   install-hooks.sh wires --commit-msg-file "$1"; with full
               knowledge the check hard-fails here.
  CI / manual  committed range only — hard gate, unchanged.

Staged deletions are ignored while a merge is in progress (MERGE_HEAD).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from preflight_common import (
    cli_fail,
    commit_text,
    compile_patterns,
    deleted_paths,
    matches_any,
    merge_in_progress,
    parse_ini_sections,
    read_pending_message,
    staged_deleted_paths,
)

DEFAULTS = {
    "contract_paths": [
        r"^docs/agent_integration\.md$",
        r"^docs/openapi(?:/.*|\.ya?ml|\.json)?$",
        r"^openapi(?:/.*|\.ya?ml|\.json)?$",
        r"^api/(?:openapi|contract)(?:/.*|\.ya?ml|\.json)?$",
        r"^schemas?/.*$",
    ],
    "notice_tokens": [
        r"contract[-_ ]deletion[-_ ]notice",
        r"contract[-_ ]deletion",
        r"breaking[-_ ]contract",
        r"contract[-_ ]removed",
    ],
}


def evaluate(
    committed_deleted: list[str],
    staged_deleted: list[str],
    committed_text: str,
    pending_text: str | None,
    cfg: dict[str, list[str]],
) -> tuple[str, list[str]]:
    """Pure decision core. pending_text=None means "message unknowable at
    this stage" (pre-commit), NOT "empty message". Verdicts:

      pass-no-deletion  no contract file deleted (committed ∪ staged)
      pass-token        notice token in committed log or pending message
      warn-staged-only  contract deletions exist only in the staged set and
                        the pending message is unknowable — caller warns, exit 0
      fail              notice missing with full knowledge
    """
    contract_re = compile_patterns(cfg["contract_paths"])
    notice_re = compile_patterns(cfg["notice_tokens"], ignore_case=True)

    all_deleted = list(dict.fromkeys([*committed_deleted, *staged_deleted]))
    hits = [p for p in all_deleted if matches_any(p, contract_re)]
    if not hits:
        return "pass-no-deletion", []

    text = committed_text if pending_text is None else f"{committed_text}\n{pending_text}"
    if matches_any(text, notice_re):
        return "pass-token", hits

    committed_set = set(committed_deleted)
    if pending_text is None and not any(p in committed_set for p in hits):
        return "warn-staged-only", hits
    return "fail", hits


def _self_test() -> int:
    failures: list[str] = []

    cases = [
        # (name, committed_deleted, staged_deleted, committed_text, pending_text, expected)
        ("no deletion", [], [], "feat: x", None, "pass-no-deletion"),
        ("non-contract deletion", ["src/old.py"], [], "chore: cleanup", None, "pass-no-deletion"),
        (
            "committed contract deletion + token",
            ["schemas/old.json"],
            [],
            "refactor: drop schema\n\ncontract-deletion-notice: replaced by v2",
            None,
            "pass-token",
        ),
        ("committed contract deletion bare", ["schemas/old.json"], [], "refactor: drop", None, "fail"),
        # THE pre-commit blind spot: first commit on branch, base...HEAD empty.
        (
            "staged-only contract deletion, message unknowable",
            [],
            ["docs/openapi/api.yaml"],
            "",
            None,
            "warn-staged-only",
        ),
        (
            "staged-only deletion + pending token (commit-msg stage)",
            [],
            ["docs/openapi/api.yaml"],
            "",
            "refactor: drop api\n\nContract-Deletion-Notice: moved to v2",
            "pass-token",
        ),
        (
            "staged-only deletion + pending message w/o token (commit-msg stage)",
            [],
            ["docs/openapi/api.yaml"],
            "",
            "refactor: drop api",
            "fail",
        ),
        (
            "committed bare + staged clean stays hard",
            ["openapi.yaml"],
            [],
            "refactor: drop",
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
    print("[check_contract_deletion_notice] self-test OK")
    return 0


def _git_fixture_failures() -> list[str]:
    """Real-git regression for the blind spot: base...HEAD is empty on the
    branch's first commit while the index carries the contract deletion."""
    import tempfile

    failures: list[str] = []
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
            schemas = pathlib.Path(td) / "schemas"
            schemas.mkdir()
            (schemas / "old.json").write_text("{}\n", encoding="utf-8")
            git("add", "schemas/old.json")
            git("commit", "-m", "base")
            git("rm", "-q", "schemas/old.json")

            os.chdir(td)
            if deleted_paths("HEAD") != []:
                failures.append("fixture: expected empty base...HEAD deletions on first commit")
            if staged_deleted_paths() != ["schemas/old.json"]:
                failures.append(f"fixture: staged_deleted_paths() = {staged_deleted_paths()}")

            verdict, hits = evaluate(
                deleted_paths("HEAD"), staged_deleted_paths(), commit_text("HEAD"), None, DEFAULTS
            )
            if (verdict, hits) != ("warn-staged-only", ["schemas/old.json"]):
                failures.append(f"fixture: pre-commit verdict = {verdict}, hits = {hits}")

            msg = pathlib.Path(td) / "COMMIT_EDITMSG"
            msg.write_text("refactor: drop schema\n\n# comment lines must not count\n", encoding="utf-8")
            verdict, _ = evaluate(
                deleted_paths("HEAD"), staged_deleted_paths(), commit_text("HEAD"),
                read_pending_message(msg), DEFAULTS,
            )
            if verdict != "fail":
                failures.append(f"fixture: commit-msg w/o token verdict = {verdict}")

            msg.write_text("refactor: drop schema\n\ncontract-deletion-notice: v2\n", encoding="utf-8")
            verdict, _ = evaluate(
                deleted_paths("HEAD"), staged_deleted_paths(), commit_text("HEAD"),
                read_pending_message(msg), DEFAULTS,
            )
            if verdict != "pass-token":
                failures.append(f"fixture: commit-msg with token verdict = {verdict}")

            # `git commit -v`: token inside the below-scissors diff body must NOT count.
            msg.write_text(
                "refactor: drop schema\n\n"
                "# ------------------------ >8 ------------------------\n"
                "+doc line mentioning contract-deletion-notice inside the diff\n",
                encoding="utf-8",
            )
            verdict, _ = evaluate(
                deleted_paths("HEAD"), staged_deleted_paths(), commit_text("HEAD"),
                read_pending_message(msg), DEFAULTS,
            )
            if verdict != "fail":
                failures.append(f"fixture: verbose-diff token leaked through scissors, verdict = {verdict}")
    finally:
        os.chdir(cwd)
        os.environ.update(saved)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require explicit notice token when public contract files are deleted."
    )
    parser.add_argument("--base", default="origin/main", help="diff base, default origin/main")
    parser.add_argument(
        "--rules",
        default=".preflight/contract-deletion-notice.conf",
        help="optional rules file with [contract_paths] and [notice_tokens]",
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

    cfg = parse_ini_sections(pathlib.Path(args.rules), DEFAULTS, replace_defaults=False)

    pending: str | None = None
    if args.commit_msg_file:
        msg_path = pathlib.Path(args.commit_msg_file)
        if not msg_path.is_file():
            sys.stderr.write(
                f"[check_contract_deletion_notice] config error: commit message file not found: {msg_path}\n"
            )
            return 2
        pending = read_pending_message(msg_path)

    try:
        committed_deleted = deleted_paths(args.base)
        staged_deleted = [] if merge_in_progress() else staged_deleted_paths()
        text = commit_text(args.base)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr)
        return 2

    verdict, hits = evaluate(committed_deleted, staged_deleted, text, pending, cfg)

    if verdict == "pass-no-deletion":
        print("[check_contract_deletion_notice] no contract deletion detected (committed or staged)")
        return 0
    if verdict == "pass-token":
        print("[check_contract_deletion_notice] notice token present")
        return 0
    if verdict == "warn-staged-only":
        print("[check_contract_deletion_notice] WARN: staged contract deletion(s) have no notice token yet:")
        for p in hits:
            print(f"    - {p}")
        print(
            "[check_contract_deletion_notice] WARN: pre-commit cannot read the pending commit message — "
            "put a notice token (e.g. 'contract-deletion-notice') in the commit message; "
            "the commit-msg hook / CI will hard-fail without it"
        )
        return 0

    return cli_fail(
        "check_contract_deletion_notice",
        "contract deletion requires notice token (e.g. 'contract-deletion-notice')",
        *hits,
    )


if __name__ == "__main__":
    sys.exit(main())
