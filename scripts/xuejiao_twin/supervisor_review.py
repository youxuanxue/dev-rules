from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from collections import Counter, deque
from pathlib import Path
from typing import Any

from .contracts import CURRENT_FILE, GOAL_FILE, HUMAN_RESPONSE_FILE, LEDGER_FILE, PERSONAS_DIR, RUN_SCHEMA, SUPERVISOR_REVIEW_SCHEMA, SUPERVISOR_STATE_FILE
from .ledger import acceptance_evidence, acceptance_focus, apply_ledger_updates, choose_next_item, item_counts, ledger_gaps
from .schema_contract import validate_schema
from .util import now_utc, read_json, write_json
from .workspace import (
    WorkspaceError,
    load_goal,
    load_human_response,
    load_ledger,
    load_state,
    load_supervisor_persona,
    render_current,
    resolve_workspace,
    validate_workspace,
    validate_workspace_readonly,
    write_ledger,
    write_state,
)


def _review_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "supervisor_review.json"


def _run_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "run.json"


def _claude_project_slug(path: Path) -> str:
    return str(path.expanduser().resolve()).replace("/", "-")


def _supervisor_transcript_path(host_root: Path, session_id: str) -> Path:
    return Path.home() / ".claude" / "projects" / _claude_project_slug(host_root) / f"{session_id}.jsonl"


def _supervisor_transcript_paths(host_root: Path, session_id: str) -> list[Path]:
    direct = _supervisor_transcript_path(host_root, session_id)
    paths: list[Path] = []
    if direct.exists():
        paths.append(direct)
    projects_root = Path.home() / ".claude" / "projects"
    try:
        candidates = sorted(projects_root.iterdir(), key=lambda item: item.name)
    except OSError:
        return paths
    for project_dir in candidates:
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate == direct or not candidate.exists():
            continue
        paths.append(candidate)
    return paths


def _gap_key(review: dict[str, Any]) -> str:
    parts = [str(item).strip() for item in review.get("remaining_gaps", []) if str(item).strip()]
    if not parts:
        parts = [str(item).strip() for item in review.get("risk_flags", []) if str(item).strip()]
    if not parts:
        return ""
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def _reset_other_failure_streaks(state: dict[str, Any], key: str) -> None:
    streaks = state.get("failure_streaks")
    if not isinstance(streaks, dict):
        state["failure_streaks"] = {}
        return
    for existing in list(streaks):
        if existing != key:
            streaks[existing] = 0


def _validate_accepted_done(goal: dict[str, Any], ledger: dict[str, Any], review: dict[str, Any], workspace: Path) -> list[str]:
    errors: list[str] = []
    if review.get("remaining_gaps"):
        errors.append("ACCEPTED_DONE cannot have remaining_gaps")
    evidence = {item["ac_id"]: list(item.get("evidence") or []) for item in acceptance_evidence(goal, ledger)}
    for ac in goal.get("acceptance_criteria", []):
        ac_id = str(ac.get("id")) if isinstance(ac, dict) else ""
        if ac_id and not evidence.get(ac_id):
            errors.append(f"missing ledger evidence for {ac_id}")
    open_items = [str(item.get("id")) for item in ledger.get("items", []) if isinstance(item, dict) and item.get("status") != "completed"]
    if open_items:
        errors.append("ledger has open items: " + ", ".join(open_items))
    errors.extend(_validate_git_state_for_accepted_done(review, workspace))
    return errors


def _host_repo_root(workspace: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    root = completed.stdout.strip()
    return Path(root) if root else None


def _validate_git_state_for_accepted_done(review: dict[str, Any], workspace: Path) -> list[str]:
    actions = set(review.get("actions") or [])
    if "allow_uncommitted_evidence" in actions:
        return []
    host = _host_repo_root(workspace)
    if host is None:
        return []
    try:
        completed = subprocess.run(
            ["git", "-C", str(host), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if completed.returncode != 0:
        return []
    try:
        ws_rel = workspace.resolve().relative_to(host.resolve()).as_posix()
    except ValueError:
        ws_rel = ""
    dirty: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line
        if ws_rel and (path == ws_rel or path.startswith(ws_rel + "/")):
            continue
        dirty.append(path)
    if not dirty:
        return []
    preview = ", ".join(dirty[:5])
    suffix = "" if len(dirty) <= 5 else f" (+{len(dirty) - 5} more)"
    return [
        "host repo has uncommitted changes outside the workspace; commit, stash, or set "
        f'actions: ["allow_uncommitted_evidence"] before ACCEPTED_DONE: {preview}{suffix}'
    ]


def _worker_output_without_stdin_warning(output: str) -> str:
    warning = "Warning: no stdin data received in 3s, proceeding without it..."
    return "\n".join(line for line in output.splitlines() if warning not in line).strip()


def _events_include_budget_exceeded(run: dict[str, Any]) -> bool:
    events_ref = str(run.get("events_ref") or "")
    if not events_ref:
        return False
    try:
        with Path(events_ref).open(encoding="utf-8") as handle:
            return any('"subtype": "error_max_budget_usd"' in line or '"subtype":"error_max_budget_usd"' in line for line in handle)
    except OSError:
        return False


def summarize_run_health(run: dict[str, Any]) -> dict[str, Any]:
    evidence = run.get("evidence") if isinstance(run.get("evidence"), dict) else {}
    validation = list(evidence.get("validation") or [])
    worker_output = str(evidence.get("worker_output") or "")
    changed_files = list(evidence.get("changed_files") or [])
    quality_flags = list(evidence.get("quality_flags") or [])
    if _events_include_budget_exceeded(run):
        quality_flags.append("WORKER_MAX_BUDGET_EXCEEDED")
    if _run_persona_write_violations(Path(str(run.get("workspace_ref") or ".")), run, 1):
        quality_flags.append("PERSONA_SOURCE_WRITE")
    if not validation:
        quality_flags.append("VALIDATION_EMPTY_LEGACY")
    if "Warning: no stdin data received in 3s" in worker_output:
        quality_flags.append("STDIN_WARNING")
    if not validation and len(_worker_output_without_stdin_warning(worker_output)) < 120:
        quality_flags.append("WORKER_OUTPUT_WEAK")
    quality_flags = list(dict.fromkeys(str(flag) for flag in quality_flags))
    requires_attention = any(
        flag in quality_flags
        for flag in (
            "VALIDATION_EMPTY_LEGACY",
            "VALIDATION_NOT_REPORTED",
            "STDIN_WARNING",
            "WORKER_OUTPUT_EMPTY_OR_WARNING_ONLY",
            "WORKER_OUTPUT_WEAK",
            "NO_PROGRESS_DETECTED",
            "SESSION_LOST",
            "WORKER_RETURN_CODE_NONZERO",
            "WORKER_MAX_BUDGET_EXCEEDED",
            "PERSONA_SOURCE_WRITE",
        )
    )
    recommended_actions: list[str] = []
    if requires_attention:
        recommended_actions.append("Do not accept this run as completion evidence without independent validation.")
    if any(flag in quality_flags for flag in ("STDIN_WARNING", "NO_PROGRESS_DETECTED", "WORKER_OUTPUT_EMPTY_OR_WARNING_ONLY")):
        recommended_actions.append("Consider reset_worker_session before the next worker turn.")
    if "WORKER_MAX_BUDGET_EXCEEDED" in quality_flags:
        recommended_actions.append("Increase worker max budget or narrow the next instruction before continuing.")
    if "PERSONA_SOURCE_WRITE" in quality_flags:
        recommended_actions.append("Revert persona source changes and rerun the worker with $DEV_RULES/personas treated as read-only.")
    return {
        "quality_flags": quality_flags,
        "has_validation": bool(validation) and not all(str(item).startswith("NOT_REPORTED:") for item in validation),
        "has_changed_files": bool(changed_files),
        "requires_attention": requires_attention,
        "recommended_actions": recommended_actions,
    }


def _next_instruction_guidance(focus: dict[str, Any], run_health: dict[str, Any] | None = None) -> list[str]:
    guidance: list[str] = []
    if focus.get("last_mile"):
        ac_ids = [str(ac.get("id")) for ac in focus.get("current_item_acceptance_criteria", []) if isinstance(ac, dict)]
        if ac_ids:
            guidance.append("Next instruction should name current acceptance criteria: " + ", ".join(ac_ids))
        guidance.append("Ask for entrypoint or end-to-end evidence when the remaining AC requires it.")
    if run_health and run_health.get("requires_attention"):
        guidance.append("Address run_health flags before accepting completion evidence.")
    return guidance


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("type", "subtype", "is_error", "session_id", "duration_ms", "total_cost_usd", "num_turns"):
        if key in event:
            summary[key] = event.get(key)
    result = event.get("result")
    if isinstance(result, str):
        summary["result_preview"] = result[:240]
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or "")[:120])
                elif item.get("type"):
                    parts.append(str(item.get("type")))
            if parts:
                summary["content_preview"] = " | ".join(parts)[:240]
    return summary


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_tool_path(raw_path: str, host_root: Path) -> Path | None:
    if not raw_path:
        return None
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = host_root / target
    try:
        return target.resolve()
    except OSError:
        return target.absolute()


def _tool_target_path(item: dict[str, Any], host_root: Path) -> Path | None:
    tool_input = item.get("input") if isinstance(item.get("input"), dict) else {}
    raw_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    return _resolve_tool_path(raw_path, host_root)


def _tool_use_items(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message") if isinstance(event, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict) and item.get("type") == "tool_use"]


BASH_REDIRECT_TARGET = re.compile(r"(?:^|[^>])>{1,2}\s*([^\s;&|]+)")
BASH_WRITE_COMMANDS = {"cp", "mv", "rm", "touch", "install", "tee", "sed", "perl", "python", "python3", "node", "ruby", "git"}


def _normalize_command_path(raw_path: str) -> str:
    return raw_path.replace("${DEV_RULES}", str(PERSONAS_DIR.parent)).replace("$DEV_RULES", str(PERSONAS_DIR.parent))


def _command_path_within(raw_path: str, protected_root: Path, host_root: Path) -> bool:
    target = _resolve_tool_path(_normalize_command_path(raw_path).strip("'\""), host_root)
    return target is not None and _path_within(target, protected_root)


def _bash_segment_write_targets(parts: list[str]) -> list[str]:
    if not parts:
        return []
    name = Path(parts[0]).name
    if name not in BASH_WRITE_COMMANDS:
        return []
    if name in {"cp", "mv", "install"}:
        return parts[-1:] if len(parts) >= 3 else []
    if name in {"rm", "touch"}:
        return [part for part in parts[1:] if not part.startswith("-")]
    if name == "tee":
        return [part for part in parts[1:] if not part.startswith("-")]
    if name in {"sed", "perl"}:
        if not any(part == "-i" or part.startswith("-i") for part in parts[1:]):
            return []
        return [part for part in parts[1:] if not part.startswith("-")]
    if name == "git" and len(parts) >= 3 and parts[1] in {"checkout", "restore"}:
        return [part for part in parts[2:] if not part.startswith("-")]
    return []


def _bash_write_command_targets(command: str) -> list[str]:
    targets: list[str] = []
    for segment in re.split(r"\s*(?:&&|\|\||[;|])\s*", command):
        try:
            parts = shlex.split(segment, posix=True)
        except ValueError:
            continue
        targets.extend(_bash_segment_write_targets(parts))
    return targets


def _bash_command_writes_path(command: str, protected_root: Path, host_root: Path) -> bool:
    if not command.strip():
        return False
    redirect_targets = [match.group(1) for match in BASH_REDIRECT_TARGET.finditer(command)]
    for raw_path in redirect_targets + _bash_write_command_targets(command):
        if _command_path_within(raw_path, protected_root, host_root):
            return True
    return False


def _supervisor_boundary_violations(workspace: Path, session_id: str, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not session_id.strip():
        return []
    host_root = workspace.parent.resolve()
    workspace_root = workspace.resolve()
    personas_dir = PERSONAS_DIR.expanduser().resolve()
    transcripts = _supervisor_transcript_paths(host_root, session_id.strip())
    if not transcripts:
        return []
    violations: deque[dict[str, Any]] = deque(maxlen=limit)
    for transcript in transcripts:
        try:
            with transcript.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for item in _tool_use_items(event):
                        target = _tool_target_path(item, host_root)
                        if item.get("name") == "Bash":
                            tool_input = item.get("input") if isinstance(item.get("input"), dict) else {}
                            command = str(tool_input.get("command") or "")
                            if not _bash_command_writes_path(command, personas_dir, host_root):
                                continue
                            target = personas_dir
                        elif item.get("name") not in {"Edit", "Write", "NotebookEdit"}:
                            continue
                        if target is None:
                            continue
                        violation_kind = ""
                        violation_flag = ""
                        if _path_within(target, personas_dir):
                            violation_kind = "persona_source_write"
                            violation_flag = "PERSONA_SOURCE_WRITE"
                        elif not _path_within(target, workspace_root) and _path_within(target, host_root):
                            violation_kind = "supervisor_boundary"
                            violation_flag = "SUPERVISOR_BOUNDARY_VIOLATION"
                        else:
                            continue
                        violations.append({
                            "kind": violation_kind,
                            "flag": violation_flag,
                            "session_id": session_id.strip(),
                            "transcript": str(transcript),
                            "tool": item.get("name"),
                            "path": str(target),
                            "line_number": line_number,
                            "timestamp": event.get("timestamp"),
                        })
        except OSError:
            continue
    return list(violations)


def _run_persona_write_violations(workspace: Path, run: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    events_ref = str(run.get("events_ref") or "")
    if not events_ref:
        return []
    path = Path(events_ref)
    if not path.exists():
        return []
    host_root = workspace.parent.resolve()
    personas_dir = PERSONAS_DIR.expanduser().resolve()
    violations: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for item in _tool_use_items(event):
                    target = _tool_target_path(item, host_root)
                    if item.get("name") == "Bash":
                        tool_input = item.get("input") if isinstance(item.get("input"), dict) else {}
                        command = str(tool_input.get("command") or "")
                        if not _bash_command_writes_path(command, personas_dir, host_root):
                            continue
                        target = personas_dir
                    elif item.get("name") not in {"Edit", "Write", "NotebookEdit"}:
                        continue
                    if target is None or not _path_within(target, personas_dir):
                        continue
                    violations.append({
                        "kind": "worker_persona_source_write",
                        "flag": "PERSONA_SOURCE_WRITE",
                        "run_id": run.get("run_id"),
                        "events_ref": str(path),
                        "tool": item.get("name"),
                        "path": str(target),
                        "line_number": line_number,
                    })
    except OSError:
        return []
    return list(violations)


def _events_tail_summary(path: Path, limit: int) -> dict[str, Any]:
    if limit <= 0:
        return {"path": str(path), "events": [], "warnings": []}
    if not path.exists():
        return {"path": str(path), "events": [], "warnings": ["events file missing"]}
    events: deque[dict[str, Any]] = deque(maxlen=limit)
    total = 0
    invalid = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                total += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                if isinstance(event, dict):
                    events.append(_event_summary(event))
    except OSError as exc:
        return {"path": str(path), "events": [], "warnings": [f"events file unreadable: {exc}"]}
    return {
        "path": str(path),
        "total_events": total,
        "invalid_events": invalid,
        "events": list(events),
    }


def _read_run_artifact(workspace: Path, run_id: str) -> dict[str, Any] | None:
    path = _run_path(workspace, run_id)
    if not path.exists():
        return None
    return read_json(path)


def _read_review_artifact(workspace: Path, run_id: str) -> dict[str, Any] | None:
    path = _review_path(workspace, run_id)
    if not path.exists():
        return None
    return read_json(path)


def _recent_runs(workspace: Path, limit: int) -> list[dict[str, Any]]:
    runs_root = workspace / "runs"
    if limit <= 0 or not runs_root.exists():
        return []
    runs: list[dict[str, Any]] = []
    for run_path in runs_root.glob("*/run.json"):
        try:
            run = read_json(run_path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(run, dict):
            runs.append(run)
    runs.sort(key=lambda item: str(item.get("ended_at") or item.get("started_at") or ""), reverse=True)
    return runs[:limit]


def _pending_runs(workspace: Path, limit: int) -> list[dict[str, Any]]:
    runs_root = workspace / "runs"
    if limit <= 0 or not runs_root.exists():
        return []
    pending: list[dict[str, Any]] = []
    for pending_path in runs_root.glob("*/pending.json"):
        run_json = pending_path.with_name("run.json")
        if run_json.exists():
            continue
        try:
            value = read_json(pending_path)
        except (OSError, json.JSONDecodeError):
            value = {"run_id": pending_path.parent.name, "status": "unknown"}
        if isinstance(value, dict):
            value.setdefault("run_id", pending_path.parent.name)
            value["pending_ref"] = str(pending_path)
            events_path = pending_path.with_name("events.jsonl")
            value["events_ref"] = str(events_path)
            if events_path.exists():
                value["events_size"] = events_path.stat().st_size
                value["events_mtime"] = events_path.stat().st_mtime
            else:
                value["events_size"] = 0
                value["events_mtime"] = None
            pending.append(value)
    pending.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return pending[:limit]


def _history_warnings(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flags: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    latest_by_flag: dict[str, str] = {}
    nonzero_runs: list[str] = []
    for run in runs:
        run_id = str(run.get("run_id") or "")
        outcome = str(run.get("outcome") or "")
        if outcome:
            outcomes[outcome] += 1
        evidence = run.get("evidence") if isinstance(run.get("evidence"), dict) else {}
        run_flags = {str(flag) for flag in evidence.get("quality_flags") or []}
        worker = run.get("worker") if isinstance(run.get("worker"), dict) else {}
        if int(worker.get("returncode") or 0) != 0:
            run_flags.add("WORKER_RETURN_CODE_NONZERO")
            nonzero_runs.append(run_id)
        if worker.get("session_lost"):
            run_flags.add("SESSION_LOST")
        for flag_text in run_flags:
            flags[flag_text] += 1
            latest_by_flag.setdefault(flag_text, run_id)
    warnings: list[dict[str, Any]] = []
    for flag in (
        "WORKER_RETURN_CODE_NONZERO",
        "NO_PROGRESS_DETECTED",
        "WORKER_SESSION_RESET",
        "WORKER_MAX_BUDGET_EXCEEDED",
        "SESSION_LOST",
        "VALIDATION_NOT_REPORTED",
        "WORKER_OUTPUT_WEAK",
    ):
        count = flags.get(flag, 0)
        if count:
            warnings.append({"kind": "quality_flag", "flag": flag, "count": count, "latest_run_id": latest_by_flag.get(flag, "")})
    review_required = outcomes.get("review_required", 0)
    if review_required >= 3:
        warnings.append({"kind": "history", "flag": "MANY_REVIEW_REQUIRED_RUNS", "count": review_required})
    return warnings


def build_health_report(
    workspace: Path,
    *,
    run_id: str | None = None,
    events_tail: int = 20,
    history_limit: int = 20,
    supervisor_session_id: str | None = None,
) -> dict[str, Any]:
    workspace = resolve_workspace(workspace)
    degraded_errors: list[str] = []
    try:
        goal, ledger, state = validate_workspace_readonly(workspace)
    except WorkspaceError as exc:
        degraded_errors.append(str(exc))
        goal = {}
        ledger = {"items": []}
        try:
            state_path = workspace / SUPERVISOR_STATE_FILE
            if not state_path.exists():
                raise WorkspaceError(f"missing {SUPERVISOR_STATE_FILE}; run /twin <workspace> to initialize runtime state")
            state = read_json(state_path)
        except (OSError, json.JSONDecodeError, WorkspaceError) as state_exc:
            degraded_errors.append(str(state_exc))
            state = {"status": "unknown", "current_run_id": None, "round_index": None, "next_instruction": ""}
    current_run_id = str(run_id or state.get("current_run_id") or "")
    next_item = choose_next_item(ledger)
    status = {
        "workspace": str(workspace),
        "goal": goal.get("one_liner"),
        "status": state.get("status"),
        "current_run_id": state.get("current_run_id"),
        "current_item_id": state.get("current_item_id") or (next_item.get("id") if next_item else None),
        "round_index": state.get("round_index"),
        "last_decision": state.get("last_decision"),
        "next_instruction": state.get("next_instruction"),
        "needs_human": state.get("needs_human"),
        "ledger_counts": item_counts(ledger),
        "remaining_gaps": ledger_gaps(goal, ledger),
    }
    report: dict[str, Any] = {
        "workspace": str(workspace),
        "status": status,
        "terminal_state": state.get("status") in {"accepted_done", "failed", "needs_human"},
        "current_run_id": current_run_id,
        "run_health": None,
        "current_run": None,
        "review": None,
        "events_tail_summary": None,
        "history_warnings": [],
        "artifact_paths": {
            "current": str(workspace / CURRENT_FILE),
            "goal": str(workspace / GOAL_FILE),
            "ledger": str(workspace / LEDGER_FILE),
            "state": str(workspace / SUPERVISOR_STATE_FILE),
            "human_response": str(workspace / HUMAN_RESPONSE_FILE),
        },
    }
    if degraded_errors:
        report["degraded"] = True
        report["history_warnings"].extend({"kind": "workspace_contract", "flag": "WORKSPACE_CONTRACT_INVALID", "message": error} for error in degraded_errors)
        report["run_health"] = {
            "quality_flags": ["WORKSPACE_CONTRACT_INVALID"],
            "has_validation": False,
            "has_changed_files": False,
            "requires_attention": True,
            "recommended_actions": ["Fix workspace contract errors before supervisor review."],
        }
    if current_run_id:
        run = _read_run_artifact(workspace, current_run_id)
        if run is None:
            if state.get("status") == "worker_running":
                expected_run_path = _run_path(workspace, current_run_id)
                expected_events_path = workspace / "runs" / current_run_id / "events.jsonl"
                expected_pending_path = workspace / "runs" / current_run_id / "pending.json"
                report["current_run"] = {
                    "run_id": current_run_id,
                    "outcome": "worker_running",
                    "started_at": state.get("updated_at"),
                    "ended_at": None,
                    "worker_returncode": None,
                    "session_lost": None,
                    "resume_used": None,
                    "quality_flags": [],
                    "validation_count": 0,
                    "changed_files_count": 0,
                }
                stale_worker_state = not expected_pending_path.exists() and not expected_events_path.exists()
                pending_without_events = expected_pending_path.exists() and not expected_events_path.exists()
                if not degraded_errors:
                    quality_flags = []
                    recommended_actions = ["Wait for worker-turn to finish before requesting review."]
                    requires_attention = False
                    if stale_worker_state:
                        quality_flags.append("STALE_WORKER_RUNNING")
                        requires_attention = True
                        recommended_actions = ["Worker-running state has no pending marker or live events; reset state before continuing."]
                    elif pending_without_events:
                        quality_flags.append("WORKER_STARTED_NO_EVENTS")
                        requires_attention = True
                        recommended_actions = ["Worker has a pending marker but no live events yet; inspect process health before waiting longer."]
                    report["run_health"] = {
                        "quality_flags": quality_flags,
                        "has_validation": False,
                        "has_changed_files": False,
                        "requires_attention": requires_attention,
                        "recommended_actions": recommended_actions,
                    }
                if stale_worker_state:
                    report["history_warnings"].append({
                        "kind": "stale_state",
                        "flag": "STALE_WORKER_RUNNING",
                        "run_id": current_run_id,
                        "message": "state is worker_running but no run directory, pending marker, events, or run artifact exists",
                    })
                elif pending_without_events:
                    report["history_warnings"].append({
                        "kind": "stale_state",
                        "flag": "WORKER_STARTED_NO_EVENTS",
                        "run_id": current_run_id,
                        "message": "worker has pending marker but no live events file yet",
                    })
                report["events_tail_summary"] = _events_tail_summary(expected_events_path, events_tail)
                report["artifact_paths"].update({"run": str(expected_run_path), "events": str(expected_events_path)})
            else:
                report["history_warnings"].append({"kind": "missing_artifact", "flag": "CURRENT_RUN_MISSING", "run_id": current_run_id})
        else:
            worker = run.get("worker") if isinstance(run.get("worker"), dict) else {}
            evidence = run.get("evidence") if isinstance(run.get("evidence"), dict) else {}
            report["current_run"] = {
                "run_id": run.get("run_id"),
                "outcome": run.get("outcome"),
                "started_at": run.get("started_at"),
                "ended_at": run.get("ended_at"),
                "worker_returncode": worker.get("returncode"),
                "session_lost": worker.get("session_lost"),
                "resume_used": worker.get("resume_used"),
                "quality_flags": list(evidence.get("quality_flags") or []),
                "validation_count": len(evidence.get("validation") or []),
                "changed_files_count": len(evidence.get("changed_files") or []),
            }
            report["run_health"] = summarize_run_health(run)
            events_ref = Path(str(run.get("events_ref") or workspace / "runs" / current_run_id / "events.jsonl"))
            report["events_tail_summary"] = _events_tail_summary(events_ref, events_tail)
            report["artifact_paths"].update({"run": str(_run_path(workspace, current_run_id)), "events": str(events_ref)})
            review = _read_review_artifact(workspace, current_run_id)
            report["artifact_paths"]["review"] = str(_review_path(workspace, current_run_id))
            if isinstance(review, dict):
                report["review"] = {
                    "recorded_decision": review.get("decision"),
                    "remaining_gaps": list(review.get("remaining_gaps") or []),
                    "risk_flags": list(review.get("risk_flags") or []),
                    "actions": list(review.get("actions") or []),
                    "human_question": review.get("human_question"),
                }
    history = _recent_runs(workspace, history_limit)
    pending_runs = _pending_runs(workspace, history_limit)
    supervisor_violations = _supervisor_boundary_violations(workspace, supervisor_session_id or "", history_limit)
    if supervisor_violations:
        report["supervisor_boundary_violations"] = supervisor_violations
        report["history_warnings"].extend(supervisor_violations)
        if not isinstance(report.get("run_health"), dict):
            report["run_health"] = {
                "quality_flags": [],
                "has_validation": False,
                "has_changed_files": False,
                "requires_attention": False,
                "recommended_actions": [],
            }
        flags = list(report["run_health"].get("quality_flags") or [])
        violation_flags = [str(item.get("flag") or "") for item in supervisor_violations]
        for flag in violation_flags:
            if flag and flag not in flags:
                flags.append(flag)
        report["run_health"]["quality_flags"] = flags
        report["run_health"]["requires_attention"] = True
        actions = list(report["run_health"].get("recommended_actions") or [])
        if any(item.get("flag") == "PERSONA_SOURCE_WRITE" for item in supervisor_violations):
            actions.append("Supervisor wrote inside $DEV_RULES/personas; revert persona source changes before accepting.")
        else:
            actions.append("Supervisor edited host repository files; move code changes back to worker flow before accepting.")
        report["run_health"]["recommended_actions"] = actions
    for pending_run in pending_runs:
        pending_run_id = str(pending_run.get("run_id") or "")
        if pending_run_id and pending_run_id == current_run_id and state.get("status") == "worker_running":
            continue
        flag = "PENDING_RUN_MISSING_ARTIFACT"
        if pending_run_id and pending_run_id != current_run_id:
            flag = "ABANDONED_PENDING_RUN"
        report["history_warnings"].append({
            "kind": "missing_artifact",
            "flag": flag,
            "run_id": pending_run_id,
            "started_at": pending_run.get("started_at"),
            "pending_ref": pending_run.get("pending_ref"),
        })
    report["pending_runs"] = pending_runs
    report["history_warnings"].extend(_history_warnings(history))
    report["scan_meta"] = {
        "events_tail": events_tail,
        "history_limit": history_limit,
        "history_runs_scanned": len(history),
        "pending_runs_scanned": len(pending_runs),
        "supervisor_boundary_violations_scanned": len(supervisor_violations),
    }
    return report


def build_supervisor_context(workspace: Path, run_id: str | None = None) -> dict[str, Any]:
    goal, ledger = validate_workspace(workspace)
    state = load_state(workspace)
    next_item = choose_next_item(ledger)
    focus = acceptance_focus(goal, ledger)
    context: dict[str, Any] = {
        "workspace": str(workspace),
        "goal": goal,
        "ledger": ledger,
        "supervisor_persona": load_supervisor_persona(),
        "state": state,
        "next_item": next_item,
        "remaining_gaps": ledger_gaps(goal, ledger),
        "acceptance_evidence": acceptance_evidence(goal, ledger),
        "acceptance_focus": focus,
        "artifact_paths": {
            "current": str(workspace / CURRENT_FILE),
            "state": str(workspace / SUPERVISOR_STATE_FILE),
            "human_response": str(workspace / HUMAN_RESPONSE_FILE),
        },
        "review_skeleton": {
            "decision": "<ACCEPTED_DONE|CONTINUE|NEEDS_HUMAN|FAILED>",
            "summary": "",
            "next_instruction": "",
            "remaining_gaps": [],
            "acceptance_evidence": [],
            "risk_flags": [],
            "actions": [],
            "ledger_updates": [],
            "human_question": None,
        },
    }
    human_response = load_human_response(workspace)
    if human_response:
        context["human_response"] = human_response
    if run_id:
        run_path = _run_path(workspace, run_id)
        if not run_path.exists():
            raise WorkspaceError(f"missing run artifact: {run_path}")
        run = read_json(run_path)
        run_health = summarize_run_health(run)
        context["run"] = run
        context["run_health"] = run_health
        context["review_guidance"] = run_health.get("recommended_actions", [])
        context["next_instruction_guidance"] = _next_instruction_guidance(focus, run_health)
        context["artifact_paths"]["run"] = str(run_path)
        context["artifact_paths"]["review"] = str(_review_path(workspace, run_id))
    else:
        context["next_instruction_guidance"] = _next_instruction_guidance(focus)
    return context


def build_review_context(workspace: Path, run_id: str) -> dict[str, Any]:
    return build_supervisor_context(workspace, run_id)


def _validate_review_actions(review: dict[str, Any]) -> None:
    actions = set(review.get("actions") or [])
    if "mark_ledger_gap" in actions and not review.get("ledger_updates"):
        raise WorkspaceError("mark_ledger_gap requires ledger_updates")


def apply_supervisor_review(
    workspace: Path,
    run_id: str,
    review: dict[str, Any],
    *,
    supervisor_session_id: str | None = None,
) -> dict[str, Any]:
    errors = validate_schema(review, SUPERVISOR_REVIEW_SCHEMA)
    if errors:
        raise WorkspaceError("supervisor_review schema errors: " + "; ".join(errors))
    _validate_review_actions(review)

    goal = load_goal(workspace)
    ledger = load_ledger(workspace)
    state = load_state(workspace)
    run_path = _run_path(workspace, run_id)
    if not run_path.exists():
        raise WorkspaceError(f"missing run artifact: {run_path}")
    run = read_json(run_path)

    update_errors = apply_ledger_updates(ledger, list(review.get("ledger_updates") or []))
    if update_errors:
        raise WorkspaceError("ledger update errors: " + "; ".join(update_errors))

    actions = set(review.get("actions") or [])
    if "reset_worker_session" in actions:
        state["worker_session_id"] = None

    key = _gap_key(review)
    if key:
        streaks = state.setdefault("failure_streaks", {})
        streaks[key] = int(streaks.get(key) or 0) + 1
        _reset_other_failure_streaks(state, key)
    else:
        state["failure_streaks"] = {}
    repeated_failure = bool(key and int(state.get("failure_streaks", {}).get(key) or 0) >= 3)

    decision = str(review.get("decision"))
    if decision == "ACCEPTED_DONE":
        done_errors = _validate_accepted_done(goal, ledger, review, workspace)
        if done_errors:
            raise WorkspaceError("ACCEPTED_DONE errors: " + "; ".join(done_errors))
        boundary_violations = _supervisor_boundary_violations(workspace, supervisor_session_id or "", 1)
        if boundary_violations:
            violation = boundary_violations[0]
            if violation.get("flag") == "PERSONA_SOURCE_WRITE":
                message = "ACCEPTED_DONE blocked: supervisor wrote inside $DEV_RULES/personas"
            else:
                message = "ACCEPTED_DONE blocked: supervisor wrote outside twin workspace"
            raise WorkspaceError(
                f"{message}; tool={violation.get('tool')} path={violation.get('path')}"
            )
        run_persona_write_violations = _run_persona_write_violations(workspace, run, 1)
        if run_persona_write_violations:
            violation = run_persona_write_violations[0]
            raise WorkspaceError(
                "ACCEPTED_DONE blocked: worker wrote inside $DEV_RULES/personas; "
                f"tool={violation.get('tool')} path={violation.get('path')}"
            )
        state["status"] = "accepted_done"
        state["next_instruction"] = ""
        state["needs_human"] = None
        state["failure_streaks"] = {}
        run["outcome"] = "accepted_done"
    elif decision == "CONTINUE" and repeated_failure:
        state["status"] = "needs_human"
        state["next_instruction"] = ""
        state["needs_human"] = {
            "question": "同一问题已经连续 3 轮未推进；是否调整目标、缩小 scope，还是继续让 worker 按当前方向修复？",
            "context": str(review.get("summary") or "\n".join(review.get("remaining_gaps", []))),
            "created_at": now_utc(),
        }
        run["outcome"] = "needs_human"
    elif decision == "CONTINUE":
        instruction = str(review.get("next_instruction") or "").strip()
        if not instruction:
            raise WorkspaceError("CONTINUE requires supervisor-authored next_instruction")
        state["status"] = "continue"
        state["next_instruction"] = instruction
        state["needs_human"] = None
        run["outcome"] = "review_required"
    elif decision == "NEEDS_HUMAN":
        question = str(review.get("human_question") or "").strip()
        if not question:
            raise WorkspaceError("NEEDS_HUMAN requires human_question")
        state["status"] = "needs_human"
        state["next_instruction"] = ""
        state["needs_human"] = {
            "question": question,
            "context": str(review.get("summary") or ""),
            "created_at": now_utc(),
        }
        run["outcome"] = "needs_human"
    elif decision == "FAILED":
        state["status"] = "failed"
        state["next_instruction"] = ""
        state["needs_human"] = None
        run["outcome"] = "failed"
    else:
        raise WorkspaceError(f"unknown decision: {decision}")

    state["last_decision"] = decision
    state["current_run_id"] = run_id
    next_item = choose_next_item(ledger)
    state["current_item_id"] = next_item.get("id") if next_item else None
    review_path = _review_path(workspace, run_id)
    write_json(review_path, review)
    run["review_ref"] = str(review_path)
    run_errors = validate_schema(run, RUN_SCHEMA)
    if run_errors:
        raise WorkspaceError("run schema errors after review: " + "; ".join(run_errors))
    write_json(run_path, run)
    write_ledger(workspace, ledger)
    write_state(workspace, state)
    render_current(workspace, goal, ledger, state)
    return state
