from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ClaudeRunResult:
    session_id: str
    output_text: str
    returncode: int
    raw_events: list[dict[str, Any]]


def parse_stream_json(text: str) -> tuple[str, str, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    parts: list[str] = []
    session_id = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parts.append(line)
            continue
        events.append(event)
        session_id = str(event.get("session_id") or event.get("sessionId") or session_id)
        if isinstance(event.get("result"), str):
            parts.append(event["result"])
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
    return session_id, "\n".join(part for part in parts if part).strip(), events


def run_claude_headless(
    prompt: str,
    *,
    cwd: Path,
    allowed_tools: list[str],
    max_budget_usd: float,
    session_id: str = "",
    dry_run: bool = False,
    timeout_seconds: int = 3600,
    disallowed_tools: list[str] | None = None,
    permission_mode: str = "",
) -> ClaudeRunResult:
    if dry_run:
        return ClaudeRunResult(
            session_id=session_id or "dry-run-session",
            output_text="DRY RUN: " + prompt[:1000],
            returncode=0,
            raw_events=[],
        )
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        ",".join(allowed_tools),
        "--max-budget-usd",
        str(max_budget_usd),
    ]
    if disallowed_tools:
        cmd.extend(["--disallowedTools", ",".join(disallowed_tools)])
    if permission_mode:
        cmd.extend(["--permission-mode", permission_mode])
    if session_id:
        cmd.extend(["--resume", session_id])
    env = os.environ.copy()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return ClaudeRunResult(
            session_id=session_id,
            output_text=(str(output).strip() + f"\nTIMEOUT after {timeout_seconds}s").strip(),
            returncode=124,
            raw_events=[],
        )
    parsed_session, output, events = parse_stream_json(proc.stdout)
    if proc.stderr.strip():
        output = (output + "\n" + proc.stderr.strip()).strip()
    return ClaudeRunResult(
        session_id=parsed_session or session_id,
        output_text=output,
        returncode=proc.returncode,
        raw_events=events,
    )
