from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

MUTATING_TOOLS = {"Edit", "Write", "NotebookEdit"}
DANGEROUS_BASH_PATTERNS = (
    r"\bgit\s+push\s+(?:[^\n;&|]*\s)?(?:--force|-f)\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+checkout\s+--\b",
    r"\bgit\s+restore\b",
    r"\brm\s+-[^\n;&|]*r[^\n;&|]*f\b",
    r"\bsudo\s+rm\b",
    r"\bchmod\s+-R\s+777\b",
    r"\bchown\s+-R\b",
    r"\bterraform\s+(?:apply|destroy)\b",
    r"\bkubectl\s+(?:apply|delete)\b",
    r"\bhelm\s+(?:upgrade|uninstall)\b",
    r"\b(?:fly|vercel)\s+deploy\b",
    r"\b(?:npm|pnpm|yarn)\s+publish\b",
    r"\btwine\s+upload\b",
    r"\bdocker\s+push\b",
    r"\bdropdb\b",
    r"\b(?:psql|mysql)\b.*\bdrop\b",
)


def _payload() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _role() -> str:
    return os.environ.get("XUEJIAO_TWIN_ROLE", "").strip()


def _worker_root() -> Path | None:
    value = os.environ.get("XUEJIAO_TWIN_WORKER_ROOT", "").strip()
    return Path(value).resolve() if value else None


def _run_events_path() -> Path | None:
    value = os.environ.get("XUEJIAO_TWIN_RUN_EVENTS", "").strip()
    return Path(value) if value else None


def _workspace_path() -> Path | None:
    value = os.environ.get("XUEJIAO_TWIN_WORKSPACE", "").strip()
    return Path(value) if value else None


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _redact_inline(text: str) -> str:
    if not text:
        return ""
    redacted = re.sub(r"(?i)\b(token|api[_-]?key|secret|password)\s*=\s*[^\s,;]+", r"\1=[REDACTED]", text)
    redacted = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}", "bearer [REDACTED]", redacted)
    redacted = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "[PRIVATE_KEY_REDACTED]", redacted, flags=re.S)

    def redact_url(match: re.Match[str]) -> str:
        url = match.group(0)
        if re.search(r"(?i)([?&](token|key|api[_-]?key|secret|password)=|://[^/\s:]+:[^/\s@]+@)", url):
            return "[URL_REDACTED]"
        return url

    return re.sub(r"https?://[^\s\"'`]+", redact_url, redacted)


def _truncate(value: Any, max_chars: int = 1000) -> Any:
    if isinstance(value, str):
        text = _redact_inline(value)
        return text if len(text) <= max_chars else text[:max_chars] + "..."
    if isinstance(value, list):
        return [_truncate(item, max_chars) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _truncate(child, max_chars) for key, child in list(value.items())[:30]}
    return value


def _append_event(event: dict[str, Any]) -> None:
    path = _run_events_path()
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _tool_path(tool_input: dict[str, Any]) -> Path | None:
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return Path(value).expanduser().resolve()
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _current_branch(cwd: str) -> str:
    import subprocess

    try:
        proc = subprocess.run(["git", "branch", "--show-current"], cwd=cwd or None, capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _block(message: str) -> int:
    print(f"XUEJIAO_TWIN_HOOK_BLOCKED: {message}", file=sys.stderr)
    return 2


def _bash_block_reason(command: str, cwd: str) -> str:
    lowered = command.lower()
    for pattern in DANGEROUS_BASH_PATTERNS:
        if re.search(pattern, lowered):
            return f"dangerous command blocked: {command[:200]}"
    if re.search(r"\bgit\s+(?:commit|push)\b", lowered):
        branch = _current_branch(cwd)
        if branch in {"main", "master"}:
            return f"git commit/push blocked on protected branch {branch}"
    return ""


def _handle_pre_tool_use(payload: dict[str, Any]) -> int:
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cwd = str(payload.get("cwd") or "")
    role = _role()

    if role == "supervisor" and tool_name in MUTATING_TOOLS:
        return _block(f"supervisor cannot use mutating tool {tool_name}")

    if tool_name in MUTATING_TOOLS:
        path = _tool_path(tool_input)
        worker_root = _worker_root()
        if path is not None:
            if ".git" in path.parts:
                return _block(f"writes under .git are blocked: {path}")
            if worker_root is not None and not _inside(path, worker_root):
                return _block(f"write outside worker root blocked: {path}")

    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        reason = _bash_block_reason(command, cwd)
        if reason:
            return _block(reason)

    return 0


def _handle_post_tool_use(payload: dict[str, Any]) -> int:
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tool_response = payload.get("tool_response")

    redacted_input = _truncate(tool_input)
    response_returncode: Any = None
    response_summary = ""
    if isinstance(tool_response, dict):
        response_returncode = tool_response.get("returncode") if "returncode" in tool_response else tool_response.get("exit_code")
        for key in ("stdout", "output", "result", "content", "stderr"):
            value = tool_response.get(key)
            if isinstance(value, str) and value:
                response_summary = _truncate(value, 1000)
                break
        if not response_summary:
            try:
                response_summary = _truncate(json.dumps(tool_response, ensure_ascii=False)[:1000])
            except Exception:
                response_summary = ""
    elif isinstance(tool_response, str):
        response_summary = _truncate(tool_response, 1000)

    _append_event({
        "timestamp": _now_utc(),
        "type": "post_tool_use",
        "role": _role(),
        "tool_name": tool_name,
        "tool_input": redacted_input,
        "tool_returncode": response_returncode,
        "tool_response_summary": response_summary,
    })
    return 0


def _handle_session_start(payload: dict[str, Any]) -> int:
    role = _role()
    workspace = _workspace_path()
    lines: list[str] = ["xuejiao-twin runtime is supervising this session."]
    if role:
        lines.append(f"Active role: {role}")
    if workspace is not None:
        ledger_path = workspace / "feature_ledger.json"
        if ledger_path.exists():
            try:
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            except Exception:
                ledger = None
            if isinstance(ledger, dict):
                focus = ledger.get("current_focus")
                if focus:
                    lines.append(f"Current focus: {focus}")
                planning = ledger.get("planning_status")
                if planning:
                    lines.append(f"Ledger planning_status: {planning}")
                features = ledger.get("features")
                if isinstance(features, list):
                    completed = sum(1 for feature in features if isinstance(feature, dict) and feature.get("status") == "completed")
                    lines.append(f"Ledger features: {len(features)} total, {completed} completed")
    lines.extend([
        "Hard rules: no main/master push, no force push, no destructive commands, no production deploy.",
        "Output contract: return one JSON object matching the schema for your role.",
        "Worker schema: xuejiao_twin.worker_result.schema.json. Planner schema: xuejiao_twin.ledger_draft.schema.json.",
    ])
    additional = "\n".join(lines)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    _append_event({
        "timestamp": _now_utc(),
        "type": "session_start",
        "role": role,
        "source": str(payload.get("source") or ""),
    })
    return 0


def _handle_pre_compact(payload: dict[str, Any]) -> int:
    _append_event({
        "timestamp": _now_utc(),
        "type": "pre_compact",
        "role": _role(),
        "trigger": str(payload.get("trigger") or ""),
    })
    return 0


def main() -> int:
    payload = _payload()
    event_name = str(payload.get("hook_event_name") or "PreToolUse")
    if event_name == "PostToolUse":
        return _handle_post_tool_use(payload)
    if event_name == "SessionStart":
        return _handle_session_start(payload)
    if event_name == "PreCompact":
        return _handle_pre_compact(payload)
    return _handle_pre_tool_use(payload)


if __name__ == "__main__":
    raise SystemExit(main())
