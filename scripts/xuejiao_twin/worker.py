from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable

from .claude_runner import ClaudeRunResult, run_claude_headless
from .contracts import DEV_RULES_ROOT, HUMAN_RESPONSE_FILE, PERSONAS_DIR, RUNS_DIR, RUN_SCHEMA, SCHEMA_VERSION, SUPERVISOR_PERSONA_PATH, WORKER_PERSONA_PATH
from .privacy import PrivacyReport, redact_text, stable_hash
from .schema_contract import validate_schema
from .util import now_utc, read_json, write_json
from .workspace import (
    WorkspaceError,
    ledger_path,
    load_goal,
    load_human_response,
    load_ledger,
    load_state,
    load_worker_persona,
    render_current,
    validate_workspace,
    write_state,
)

Runner = Callable[..., ClaudeRunResult]

WORKER_ALLOWED_TOOLS = [
    "Bash",
    "Read",
    "Edit",
    "Write",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
]

WORKER_MAX_BUDGET_ENV = "XUEJIAO_TWIN_WORKER_MAX_BUDGET_USD"
DEFAULT_WORKER_MAX_BUDGET_USD = 20.0


def worker_disallowed_tools() -> list[str]:
    personas_pattern = str(PERSONAS_DIR / "**")
    return [
        f"Edit({personas_pattern})",
        f"Write({personas_pattern})",
        f"NotebookEdit({personas_pattern})",
        "Bash(*$DEV_RULES/personas*)",
        "Bash(*${DEV_RULES}/personas*)",
        f"Bash(*{PERSONAS_DIR}*)",
    ]


def default_worker_max_budget_usd() -> float:
    raw = os.environ.get(WORKER_MAX_BUDGET_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_WORKER_MAX_BUDGET_USD
    try:
        value = float(raw)
    except ValueError as exc:
        raise WorkspaceError(f"{WORKER_MAX_BUDGET_ENV} must be a number") from exc
    if value <= 0:
        raise WorkspaceError(f"{WORKER_MAX_BUDGET_ENV} must be greater than 0")
    return value


def _run_command(args: list[str], cwd: Path, *, timeout: int = 30) -> str:
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return f"error: {exc}"
    return (proc.stdout + proc.stderr).rstrip()


def _repo_root(workspace: Path) -> Path:
    return workspace.parent


def git_status(workspace: Path) -> str:
    return _run_command(["git", "status", "--short"], _repo_root(workspace))


def git_diff_stat(workspace: Path) -> str:
    return _run_command(["git", "diff", "--stat"], _repo_root(workspace))


STDIN_WARNING_TEXT = "Warning: no stdin data received in 3s, proceeding without it..."
VALIDATION_NOT_REPORTED = "NOT_REPORTED: worker did not report tests, lint, preflight, or validation evidence"
VALIDATION_KEYWORDS = re.compile(
    r"\b(pytest|ruff|preflight|scripts/preflight\.sh|passed|failed|PASS|FAIL)\b",
    re.IGNORECASE,
)
STRUCTURED_OUTPUT_HEADINGS = ("DIFF:", "TESTS:", "PREFLIGHT:", "REMAINING:")


def changed_files_from_status(status: str) -> list[str]:
    files: list[str] = []
    for line in status.splitlines():
        text = line.rstrip()
        if not text.strip() or text.lstrip().startswith("error:") or len(text) < 4:
            continue
        path = text[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path not in files:
            files.append(path)
    return files


def _without_stdin_warning(worker_output: str) -> str:
    lines = [line for line in worker_output.splitlines() if STDIN_WARNING_TEXT not in line]
    return "\n".join(lines).strip()


def _is_warning_only(worker_output: str) -> bool:
    return not _without_stdin_warning(worker_output).strip()


def extract_validation_evidence(worker_output: str) -> list[str]:
    evidence: list[str] = []
    for line in worker_output.splitlines():
        text = line.strip()
        if not text or STDIN_WARNING_TEXT in text:
            continue
        if VALIDATION_KEYWORDS.search(text) and text not in evidence:
            evidence.append(text)
    return evidence


def _has_structured_output(worker_output: str) -> bool:
    upper = worker_output.upper()
    return all(heading in upper for heading in STRUCTURED_OUTPUT_HEADINGS)


def assess_run_quality(
    *,
    worker_output: str,
    validation: list[str],
    returncode: int,
    session_lost: bool,
    resume_used: bool,
    pre_git_status: str,
    post_git_status: str,
    pre_git_diff_stat: str,
    post_git_diff_stat: str,
    worker_session_reset: bool = False,
    raw_events: list[dict[str, Any]] | None = None,
) -> list[str]:
    flags: list[str] = []
    output_without_warning = _without_stdin_warning(worker_output)
    weak_output = not _has_structured_output(worker_output) and not validation
    if STDIN_WARNING_TEXT in worker_output:
        flags.append("STDIN_WARNING")
    if not output_without_warning:
        flags.append("WORKER_OUTPUT_EMPTY_OR_WARNING_ONLY")
    elif weak_output:
        flags.append("WORKER_OUTPUT_WEAK")
    if not validation:
        flags.append("VALIDATION_NOT_REPORTED")
    if returncode != 0:
        flags.append("WORKER_RETURN_CODE_NONZERO")
    if raw_events and any(event.get("subtype") == "error_max_budget_usd" for event in raw_events):
        flags.append("WORKER_MAX_BUDGET_EXCEEDED")
    if session_lost:
        flags.append("SESSION_LOST")
    if (
        resume_used
        and weak_output
        and not session_lost
        and pre_git_status == post_git_status
        and pre_git_diff_stat == post_git_diff_stat
    ):
        flags.append("NO_PROGRESS_DETECTED")
    if worker_session_reset:
        flags.append("WORKER_SESSION_RESET")
    return flags


def build_worker_prompt(workspace: Path, instruction: str) -> str:
    worker_persona = load_worker_persona()
    goal = (workspace / "goal.yaml").read_text(encoding="utf-8")
    ledger_file = ledger_path(workspace)
    ledger = ledger_file.read_text(encoding="utf-8")
    parts = [
        f"# {WORKER_PERSONA_PATH}",
        worker_persona.strip(),
        "# goal.yaml",
        goal.strip(),
        f"# {ledger_file.name}",
        ledger.strip(),
    ]
    human_response = load_human_response(workspace)
    if human_response and not human_response.get("consumed_by_run_id"):
        parts.extend(["# human_response.json", json.dumps(human_response, ensure_ascii=False, indent=2, sort_keys=True)])
    parts.extend(["# supervisor next_instruction", instruction.strip()])
    return "\n\n".join(parts).strip()


def _run_dir(workspace: Path, run_id: str) -> Path:
    path = workspace / RUNS_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_path(workspace: Path, run_id: str) -> Path:
    return _run_dir(workspace, run_id) / "pending.json"


def _events_path(workspace: Path, run_id: str) -> Path:
    return _run_dir(workspace, run_id) / "events.jsonl"


def _is_stale_worker_running_state(workspace: Path, state: dict[str, Any]) -> bool:
    run_id = str(state.get("current_run_id") or "")
    if state.get("status") != "worker_running" or not run_id:
        return False
    run_dir = workspace / RUNS_DIR / run_id
    return not any(
        path.exists()
        for path in (
            run_dir / "pending.json",
            run_dir / "events.jsonl",
            run_dir / "run.json",
        )
    )


def _write_events(workspace: Path, run_id: str, events: list[dict[str, Any]]) -> None:
    path = _events_path(workspace, run_id)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _consume_human_response(workspace: Path, run_id: str) -> None:
    path = workspace / HUMAN_RESPONSE_FILE
    if not path.exists():
        return
    response = read_json(path)
    if response.get("consumed_by_run_id"):
        return
    response["consumed_by_run_id"] = run_id
    write_json(path, response)


def start_worker_turn(
    workspace: Path,
    instruction: str = "",
    *,
    runner: Runner = run_claude_headless,
    retry_on_session_lost: bool = True,
    max_budget_usd: float | None = None,
) -> dict[str, Any]:
    if max_budget_usd is None:
        max_budget_usd = default_worker_max_budget_usd()
    workspace = workspace.expanduser().resolve()
    validate_workspace(workspace)
    state = load_state(workspace)
    if state.get("status") == "needs_human" and state.get("needs_human"):
        raise WorkspaceError("workspace is waiting for human response")
    if state.get("status") in {"accepted_done", "failed"}:
        raise WorkspaceError(f"workspace is terminal: {state.get('status')}")
    if state.get("status") == "review_required":
        raise WorkspaceError(
            "previous worker turn requires supervisor review before the next worker turn"
        )
    if state.get("status") == "worker_running":
        if _is_stale_worker_running_state(workspace, state):
            state["status"] = "continue"
            state["worker_session_id"] = None
            write_state(workspace, state)
        else:
            raise WorkspaceError("worker is already running for this workspace")

    instruction = instruction.strip()
    if not instruction:
        raise WorkspaceError("worker-turn requires supervisor-authored --instruction")
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    started_at = now_utc()
    previous_session_id = str(state.get("worker_session_id") or "")
    prompt = build_worker_prompt(workspace, instruction)

    state["status"] = "worker_running"
    state["current_run_id"] = run_id
    state["next_instruction"] = instruction
    write_json(_pending_path(workspace, run_id), {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "workspace_ref": str(workspace),
        "state_ref": str(workspace / "supervisor_state.json"),
        "started_at": started_at,
        "status": "worker_running",
        "instruction_hash": stable_hash(instruction),
    })
    write_state(workspace, state)
    pre_git_status = git_status(workspace)
    pre_git_diff_stat = git_diff_stat(workspace)

    result = runner(
        prompt,
        cwd=_repo_root(workspace),
        allowed_tools=WORKER_ALLOWED_TOOLS,
        disallowed_tools=worker_disallowed_tools(),
        max_budget_usd=max_budget_usd,
        session_id=previous_session_id,
        permission_mode="bypassPermissions",
        role="worker",
        extra_env={"DEV_RULES": str(DEV_RULES_ROOT)},
        stream_output_path=_events_path(workspace, run_id),
    )
    resume_used = bool(previous_session_id)
    worker_session_reset = False
    if retry_on_session_lost and result.session_lost:
        state = load_state(workspace)
        state["worker_session_id"] = None
        write_state(workspace, state)
        result = runner(
            prompt,
            cwd=_repo_root(workspace),
            allowed_tools=WORKER_ALLOWED_TOOLS,
            disallowed_tools=worker_disallowed_tools(),
            max_budget_usd=max_budget_usd,
            session_id="",
            permission_mode="bypassPermissions",
            role="worker",
            extra_env={"DEV_RULES": str(DEV_RULES_ROOT)},
            stream_output_path=_events_path(workspace, run_id),
        )
        resume_used = False
        worker_session_reset = True
    elif retry_on_session_lost and resume_used and _is_warning_only(result.output_text):
        state = load_state(workspace)
        state["worker_session_id"] = None
        write_state(workspace, state)
        result = runner(
            prompt,
            cwd=_repo_root(workspace),
            allowed_tools=WORKER_ALLOWED_TOOLS,
            disallowed_tools=worker_disallowed_tools(),
            max_budget_usd=max_budget_usd,
            session_id="",
            permission_mode="bypassPermissions",
            role="worker",
            extra_env={"DEV_RULES": str(DEV_RULES_ROOT)},
            stream_output_path=_events_path(workspace, run_id),
        )
        resume_used = False
        worker_session_reset = True

    post_git_status = git_status(workspace)
    post_git_diff_stat = git_diff_stat(workspace)
    privacy = PrivacyReport()
    worker_output, _flags = redact_text(result.output_text, privacy)
    validation = extract_validation_evidence(worker_output)
    quality_flags = assess_run_quality(
        worker_output=worker_output,
        validation=validation,
        returncode=result.returncode,
        session_lost=result.session_lost,
        resume_used=resume_used,
        pre_git_status=pre_git_status,
        post_git_status=post_git_status,
        pre_git_diff_stat=pre_git_diff_stat,
        post_git_diff_stat=post_git_diff_stat,
        worker_session_reset=worker_session_reset,
        raw_events=result.raw_events,
    )
    clear_session_after_run = resume_used and "NO_PROGRESS_DETECTED" in quality_flags
    if clear_session_after_run and "WORKER_SESSION_RESET" not in quality_flags:
        quality_flags.append("WORKER_SESSION_RESET")
    evidence_validation = validation or [VALIDATION_NOT_REPORTED]

    state = load_state(workspace)
    state["round_index"] = int(state.get("round_index") or 0) + 1
    state["current_run_id"] = run_id
    state["status"] = "failed" if result.session_lost else "review_required"
    state["worker_session_id"] = None if clear_session_after_run else (result.session_id or None)
    state["next_instruction"] = ""
    write_state(workspace, state)

    run_dir = _run_dir(workspace, run_id)
    _write_events(workspace, run_id, result.raw_events)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "workspace_ref": str(workspace),
        "goal_ref": str(workspace / "goal.yaml"),
        "ledger_ref": str(ledger_path(workspace)),
        "worker_persona_ref": str(WORKER_PERSONA_PATH),
        "supervisor_persona_ref": str(SUPERVISOR_PERSONA_PATH),
        "state_ref": str(workspace / "supervisor_state.json"),
        "events_ref": str(_events_path(workspace, run_id)),
        "started_at": started_at,
        "ended_at": now_utc(),
        "worker": {
            "session_hash": stable_hash(result.session_id) if result.session_id else "",
            "resume_used": resume_used,
            "permission_mode": "bypassPermissions",
            "returncode": result.returncode,
            "session_lost": result.session_lost,
        },
        "instruction": instruction,
        "evidence": {
            "worker_output": worker_output,
            "changed_files": changed_files_from_status(post_git_status),
            "validation": evidence_validation,
            "quality_flags": quality_flags,
            "git_status": post_git_status,
            "git_diff_stat": post_git_diff_stat,
        },
        "review_ref": None,
        "outcome": "failed" if result.session_lost else "review_required",
    }
    errors = validate_schema(run, RUN_SCHEMA)
    if errors:
        raise WorkspaceError("run schema errors: " + "; ".join(errors))
    write_json(run_dir / "run.json", run)
    pending_path = _pending_path(workspace, run_id)
    if pending_path.exists():
        pending_path.unlink()
    render_current(workspace, load_goal(workspace), load_ledger(workspace), state)
    _consume_human_response(workspace, run_id)
    return run
