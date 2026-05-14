#!/usr/bin/env python3
"""Scan .github/workflows/*.yml for two failure modes that pass YAML lint
but break execution silently:

R1 (hard): job-level `if:` references `env.*`. The `env.*` context is not
   bound at job-level evaluation time → GitHub parses the workflow as
   HTTP 422 and refuses to start any job. Step-level `if: env.*` is fine.

R2 (hard): `claude -p` invocation missing `--allowedTools`. In `claude -p`
   default permission mode under CI, the agent rejects every tool call
   and exits 0 with zero output — the worst possible failure mode.

R3 (hard): `claude -p` invocation using `--output` flag, which does not
   exist (correct form: `2>&1 | tee /tmp/out.txt` + `set -o pipefail`).

Self-skips when .github/workflows/ has no files. Stdlib only — no PyYAML.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

from preflight_common import cli_fail

JOB_NAME_RE = re.compile(r"^  ([A-Za-z_][\w-]*):\s*$")
JOB_IF_RE = re.compile(r"^    if:\s*(.+?)\s*$")
STEPS_RE = re.compile(r"^    steps:\s*$")
ENV_REF_RE = re.compile(r"\benv\.[A-Za-z_][\w]*")

CLAUDE_INVOKE_RE = re.compile(r"\bclaude\s+-p\b")
ALLOWED_TOOLS_RE = re.compile(r"--allowedTools\b")
OUTPUT_FLAG_RE = re.compile(r"--output\b")


def find_workflows(root: pathlib.Path) -> list[pathlib.Path]:
    base = root / ".github" / "workflows"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.suffix in {".yml", ".yaml"})


def scan_job_if_env(path: pathlib.Path) -> list[str]:
    """Return one error per job-level `if:` that references env.*."""
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    in_jobs = False
    job: str | None = None
    in_steps = False
    for lineno, raw in enumerate(lines, start=1):
        if raw.rstrip() == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        # any line that is non-indented (or starts at column 0 and is non-blank)
        # ends the jobs block
        if raw and not raw.startswith(" ") and not raw.startswith("\t"):
            in_jobs = False
            continue
        m_job = JOB_NAME_RE.match(raw)
        if m_job:
            job = m_job.group(1)
            in_steps = False
            continue
        if job is None:
            continue
        if STEPS_RE.match(raw):
            in_steps = True
            continue
        if in_steps:
            continue
        m_if = JOB_IF_RE.match(raw)
        if m_if and ENV_REF_RE.search(m_if.group(1)):
            errors.append(
                f"{path}:{lineno}: job '{job}' has job-level `if:` referencing env.* — "
                "GitHub rejects the workflow with HTTP 422 and no job starts"
            )
    return errors


def scan_claude_invocations(path: pathlib.Path) -> list[str]:
    """Lightweight scan: every `claude -p` line should have --allowedTools
    nearby and must never use --output. Continuations via `\\` extend the
    inspection window forward; YAML multiline run-blocks already collapse
    onto one logical line as far as this regex is concerned.
    """
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for lineno, raw in enumerate(lines, start=1):
        if not CLAUDE_INVOKE_RE.search(raw):
            continue
        window = [raw]
        idx = lineno
        # extend window through trailing backslash continuations
        while window[-1].rstrip().endswith("\\") and idx < len(lines):
            window.append(lines[idx])
            idx += 1
        text = " ".join(window)
        if not ALLOWED_TOOLS_RE.search(text):
            errors.append(
                f"{path}:{lineno}: `claude -p` invocation missing --allowedTools "
                "(CI permission-mode default exits 0 with zero output)"
            )
        if OUTPUT_FLAG_RE.search(text):
            errors.append(
                f"{path}:{lineno}: `claude -p` uses --output (no such flag); "
                "use `2>&1 | tee /tmp/out.txt` with `set -o pipefail`"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan .github/workflows/*.yml for job-level if=env.*, claude -p without --allowedTools, "
        "and claude -p --output (which does not exist)."
    )
    parser.add_argument("--repo-root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args()

    root = pathlib.Path(args.repo_root)
    workflows = find_workflows(root)
    if not workflows:
        print("[check_workflow_yaml] skip: no .github/workflows/*.yml")
        return 0

    errors: list[str] = []
    for path in workflows:
        errors.extend(scan_job_if_env(path))
        errors.extend(scan_claude_invocations(path))

    if errors:
        return cli_fail("check_workflow_yaml", "workflow YAML has hard failure patterns", *errors)
    print(f"[check_workflow_yaml] {len(workflows)} workflow(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
