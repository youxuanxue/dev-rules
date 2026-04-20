#!/usr/bin/env python3
"""Validate frontmatter of every docs/approved/*.md.

Status-vocabulary invariants shared across all dev-rules consumer projects
(Jobs minimalism + OPC automation; see dev-rules/rules/product-dev.mdc §完成自检
and rules/dev-rules-convention.mdc §approved-doc status vocabulary).

Rules:
  R1. Frontmatter MUST exist (--- ... ---) at file head.
  R2. status MUST be one of {draft, pending, approved, shipped, archived}.
        - draft     : early sketch, may freely change
        - pending   : design proposed, awaiting human approval
        - approved  : human-approved baseline, change requires GATE/PR review
                      (used by zw-brain GATE model)
        - shipped   : implementation merged, status flipped from pending/approved
        - archived  : superseded or no longer authoritative
  R3. status == "pending" AND (related_prs OR related_commits) non-empty
      → "shipped under pending" smell. Implementation has landed but the
        approval status has not been flipped — refuse to merge until either
        the status is bumped or the PR/commit list is removed.
  R4. status == "shipped" MUST list at least one of related_prs / related_commits
      (otherwise the audit trail is broken).

Out of scope (intentionally not enforced here):
  - approved_by: pending — branch-specific check, lives in
    dev-rules/templates/preflight.sh § 7 R5 (only blocks on main/master).
  - GATE numbering — project-specific extension; consumer projects can layer
    additional checks via their own scripts/preflight.sh wrapper.

Exit non-zero on any violation. Reads docs/approved/ relative to cwd, which
preflight.sh sets to the project repo root.
"""
from __future__ import annotations

import pathlib
import re
import sys

ALLOWED_STATUS = {"draft", "pending", "approved", "shipped", "archived"}
APPROVED_DIR = pathlib.Path("docs/approved")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def is_listish_nonempty(v: str | None) -> bool:
    if not v:
        return False
    s = v.strip()
    if s in ("[]", ""):
        return False
    return True


def check(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm is None:
        return [f"{path}: R1 missing frontmatter (--- ... ---) at file head"]
    errs: list[str] = []
    status = fm.get("status", "")
    if status not in ALLOWED_STATUS:
        errs.append(
            f"{path}: R2 status='{status}' not in {sorted(ALLOWED_STATUS)}"
        )
    has_prs = is_listish_nonempty(fm.get("related_prs"))
    has_commits = is_listish_nonempty(fm.get("related_commits"))
    if status == "pending" and (has_prs or has_commits):
        errs.append(
            f"{path}: R3 status=pending but related_prs/related_commits non-empty — "
            "implementation appears to have shipped without flipping status. "
            "Either bump status to 'shipped'/'approved' or remove the PR/commit list."
        )
    if status == "shipped" and not (has_prs or has_commits):
        errs.append(
            f"{path}: R4 status=shipped but no related_prs and no related_commits listed"
        )
    return errs


def main() -> int:
    if not APPROVED_DIR.exists():
        return 0
    errs: list[str] = []
    for p in sorted(APPROVED_DIR.glob("*.md")):
        errs.extend(check(p))
    if errs:
        sys.stderr.write("\n".join(errs) + "\n")
        sys.stderr.write(
            f"\n[check_approved_docs] FAILED ({len(errs)} issue(s))\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
