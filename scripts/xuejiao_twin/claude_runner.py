from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ClaudeRunResult:
    session_id: str
    output_text: str
    returncode: int
    raw_events: list[dict[str, Any]]
    cwd: str = ""
    session_lost: bool = False


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
        candidate = event.get("session_id") or event.get("sessionId")
        if candidate:
            session_id = str(candidate)
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
    allowed_tools: list[str] | None = None,
    max_budget_usd: float = 1.0,
    session_id: str = "",
    dry_run: bool = False,
    timeout_seconds: int = 3600,
    disallowed_tools: list[str] | None = None,
    permission_mode: str = "",
    role: str = "",
    extra_env: dict[str, str] | None = None,
    append_system_prompt: str = "",
    setting_sources: str = "project,local",
    strict_mcp_config: bool = True,
    stream_output_path: Path | None = None,
) -> ClaudeRunResult:
    if dry_run:
        return ClaudeRunResult(
            session_id=session_id or "dry-run-session",
            output_text="DRY RUN: " + prompt[:1000],
            returncode=0,
            raw_events=[],
            cwd=str(cwd),
        )
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-budget-usd",
        str(max_budget_usd),
    ]
    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])
    if setting_sources:
        cmd.extend(["--setting-sources", setting_sources])
    if strict_mcp_config:
        cmd.append("--strict-mcp-config")
    if append_system_prompt:
        cmd.extend(["--append-system-prompt", append_system_prompt])
    if disallowed_tools:
        cmd.extend(["--disallowedTools", ",".join(disallowed_tools)])
    if permission_mode:
        cmd.extend(["--permission-mode", permission_mode])
    if session_id:
        cmd.extend(["--resume", session_id])
    env = os.environ.copy()
    if role:
        env["XUEJIAO_TWIN_ROLE"] = role
    if extra_env:
        env.update(extra_env)
    if stream_output_path is None:
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_seconds, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "")
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            return ClaudeRunResult(
                session_id=session_id,
                output_text=(str(output).strip() + f"\nTIMEOUT after {timeout_seconds}s").strip(),
                returncode=124,
                raw_events=[],
                cwd=str(cwd),
            )
        stdout_text = proc.stdout
        stderr_text = proc.stderr
    else:
        stream_output_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        with stream_output_path.open("w", encoding="utf-8") as stream_file:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            def drain_stdout() -> None:
                assert proc.stdout is not None
                for line in proc.stdout:
                    stdout_parts.append(line)
                    stream_file.write(line)
                    stream_file.flush()

            def drain_stderr() -> None:
                assert proc.stderr is not None
                for line in proc.stderr:
                    stderr_parts.append(line)
                    stream_file.write(json.dumps({"type": "stderr", "text": line.rstrip("\n")}, ensure_ascii=False, sort_keys=True) + "\n")
                    stream_file.flush()

            stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
            stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)
                output = ("".join(stdout_parts) + "".join(stderr_parts)).strip()
                return ClaudeRunResult(
                    session_id=session_id,
                    output_text=(output + f"\nTIMEOUT after {timeout_seconds}s").strip(),
                    returncode=124,
                    raw_events=[],
                    cwd=str(cwd),
                )
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
        stdout_text = "".join(stdout_parts)
        stderr_text = "".join(stderr_parts)
    parsed_session, output, events = parse_stream_json(stdout_text)
    if stderr_text.strip():
        output = (output + "\n" + stderr_text.strip()).strip()
    session_lost = bool(session_id) and ((bool(parsed_session) and parsed_session != session_id) or (proc.returncode == 0 and not output.strip()))
    return ClaudeRunResult(
        session_id=parsed_session or session_id,
        output_text=output,
        returncode=proc.returncode,
        raw_events=events,
        cwd=str(cwd),
        session_lost=session_lost,
    )
