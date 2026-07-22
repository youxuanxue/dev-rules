from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .claude_runner import ClaudeRunResult


LOCAL_CLI_PROVIDERS = ("claude", "codex", "gemini")


@dataclass(frozen=True)
class LocalCliSpec:
    provider: str
    executable: str
    supports_resume: bool
    supports_budget: bool
    permission_mode: str


LOCAL_CLI_SPECS: dict[str, LocalCliSpec] = {
    "claude": LocalCliSpec("claude", "claude", True, True, "bypassPermissions"),
    "codex": LocalCliSpec("codex", "codex", True, False, "workspace-write/no-approval"),
    "gemini": LocalCliSpec("gemini", "gemini", True, False, "sandbox/yolo"),
}


def local_cli_spec(provider: str) -> LocalCliSpec:
    try:
        return LOCAL_CLI_SPECS[provider]
    except KeyError as exc:
        supported = ", ".join(LOCAL_CLI_PROVIDERS)
        raise ValueError(f"unsupported local_cli provider {provider!r}; supported providers: {supported}") from exc


def build_local_cli_command(
    provider: str,
    prompt: str,
    *,
    cwd: Path,
    session_id: str = "",
) -> list[str]:
    """Build a deterministic, non-interactive command for one local provider."""
    spec = local_cli_spec(provider)
    if provider == "claude":
        # The actual Claude invocation reuses run_claude_headless so budget and
        # tool-filter flags stay identical to the legacy backend. This basic
        # shape is still exposed for diagnostics and command contract tests.
        command = [
            spec.executable,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            spec.permission_mode,
        ]
        if session_id:
            command.extend(["--resume", session_id])
        return command
    if provider == "codex":
        command = [spec.executable]
        if session_id:
            command.extend([
                "exec",
                "resume",
                session_id,
                "-c",
                'sandbox_mode="workspace-write"',
                "-c",
                'approval_policy="never"',
            ])
        else:
            command.extend(["exec"])
        command.append("--json")
        if not session_id:
            command.extend([
                "--sandbox",
                "workspace-write",
                "-c",
                'approval_policy="never"',
                "--cd",
                str(cwd),
            ])
        command.append(prompt)
        return command
    command = [
        spec.executable,
        "--prompt",
        prompt,
        "--output-format",
        "stream-json",
        "--approval-mode",
        "yolo",
        "--sandbox",
        "--skip-trust",
        "--include-directories",
        str(cwd),
    ]
    if session_id:
        command.extend(["--resume", session_id])
    return command


def _event_text(event: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    event_type = str(event.get("type") or "")
    item = event.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type") or "")
        if item_type in {"agent_message", "assistant_message", "message"}:
            for key in ("text", "content", "message"):
                value = item.get(key)
                parts.extend(_value_text(value))
    if event_type in {"message", "assistant", "result"}:
        role = str(event.get("role") or "assistant")
        if role in {"assistant", "model"}:
            for key in ("text", "result", "content", "message"):
                value = event.get(key)
                parts.extend(_value_text(value))
    if _is_terminal_error(event):
        parts.extend(_value_text(event.get("error")))
    return parts


def _value_text(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list):
        return _content_text(value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "message", "parts", "content"):
            if key in value:
                parts.extend(_value_text(value[key]))
        return parts
    return []


def _content_text(value: list[Any]) -> list[str]:
    parts: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            parts.append(item)
        elif isinstance(item, dict):
            item_type = str(item.get("type") or "")
            if item_type in {"text", "output_text", "input_text"}:
                parts.extend(_value_text(item.get("text")))
            elif not item_type:
                parts.extend(_value_text(item))
    return parts


def _is_terminal_error(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or "")
    if event_type == "result":
        return str(event.get("status") or "").lower() in {"error", "failed"}
    return event_type in {"turn.failed", "turn_failed"}


def parse_local_cli_output(provider: str, stdout_text: str, stderr_text: str = "") -> tuple[str, str, list[dict[str, Any]]]:
    """Normalize provider JSONL into the same evidence shape as Claude."""
    local_cli_spec(provider)
    events: list[dict[str, Any]] = []
    output: list[str] = []
    session_id = ""
    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Keep human-readable provider diagnostics instead of discarding
            # them, while still marking the event stream as malformed.
            events.append({"type": "malformed_output", "text": line})
            output.append(line)
            continue
        if not isinstance(event, dict):
            events.append({"type": "malformed_output", "value": event})
            continue
        events.append(event)
        for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id"):
            candidate = event.get(key)
            if candidate:
                session_id = str(candidate)
                break
        output.extend(_event_text(event))
    if stderr_text.strip():
        output.append(stderr_text.strip())
        events.append({"type": "stderr", "text": stderr_text.strip()})
    return session_id, "\n".join(part.strip() for part in output if part.strip()).strip(), events


def _terminate_process_group(process: subprocess.Popen[str], *, grace_seconds: int = 5) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=grace_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        if process.poll() is None:
            process.kill()
            process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.poll() is None:
        process.wait()


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    stream_output_path: Path,
    parse: Callable[[str, str], tuple[str, str, list[dict[str, Any]]]],
    session_id: str,
    extra_env: dict[str, str] | None = None,
) -> ClaudeRunResult:
    stream_output_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    try:
        environment = os.environ.copy()
        if extra_env:
            environment.update(extra_env)
        popen_options: dict[str, Any] = {}
        if os.name == "posix":
            popen_options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **popen_options,
        )
    except FileNotFoundError as exc:
        message = f"local_cli provider executable unavailable: {command[0]} ({exc})"
        stream_output_path.write_text(json.dumps({"type": "process_error", "error": message}) + "\n", encoding="utf-8")
        return ClaudeRunResult(session_id=session_id, output_text=message, returncode=127, raw_events=[], cwd=str(cwd))
    except OSError as exc:
        message = f"local_cli provider failed to start: {command[0]} ({exc})"
        stream_output_path.write_text(json.dumps({"type": "process_error", "error": message}) + "\n", encoding="utf-8")
        return ClaudeRunResult(session_id=session_id, output_text=message, returncode=1, raw_events=[], cwd=str(cwd))

    assert process.stdout is not None
    assert process.stderr is not None
    with stream_output_path.open("w", encoding="utf-8") as stream_file:
        def drain_stdout() -> None:
            for line in process.stdout:
                stdout_parts.append(line)
                stream_file.write(line)
                stream_file.flush()

        def drain_stderr() -> None:
            for line in process.stderr:
                stderr_parts.append(line)
                stream_file.write(json.dumps({"type": "stderr", "text": line.rstrip("\n")}, ensure_ascii=False, sort_keys=True) + "\n")
                stream_file.flush()

        stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            parsed_session, output, events = parse("".join(stdout_parts), "".join(stderr_parts))
            timeout_event = {"type": "process_timeout", "timeout_seconds": timeout_seconds}
            events.append(timeout_event)
            stream_file.write(json.dumps(timeout_event, ensure_ascii=False, sort_keys=True) + "\n")
            stream_file.flush()
            return ClaudeRunResult(
                session_id=parsed_session or session_id,
                output_text=(output + f"\nTIMEOUT after {timeout_seconds}s").strip(),
                returncode=124,
                raw_events=events,
                cwd=str(cwd),
            )
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
    parsed_session, output, events = parse("".join(stdout_parts), "".join(stderr_parts))
    malformed_output = any(event.get("type") == "malformed_output" for event in events)
    terminal_error = any(_is_terminal_error(event) for event in events)
    if malformed_output and process.returncode == 0:
        output = (output + "\nlocal_cli provider emitted malformed JSONL").strip()
    if terminal_error and process.returncode == 0 and not output:
        output = "local_cli provider reported a terminal error"
    session_lost = False
    if session_id:
        extracted_turn = any(_event_text(event) for event in events if not _is_terminal_error(event))
        session_lost = bool((parsed_session and parsed_session != session_id) or not extracted_turn)
    normalized_returncode = process.returncode
    if normalized_returncode == 0 and (malformed_output or terminal_error):
        normalized_returncode = 1
    return ClaudeRunResult(
        session_id=parsed_session or session_id,
        output_text=output,
        returncode=normalized_returncode,
        raw_events=events,
        cwd=str(cwd),
        session_lost=session_lost,
    )


def run_local_cli(
    provider: str,
    prompt: str,
    *,
    cwd: Path,
    session_id: str,
    timeout_seconds: int,
    stream_output_path: Path,
    extra_env: dict[str, str] | None = None,
) -> ClaudeRunResult:
    spec = local_cli_spec(provider)
    if provider == "claude":
        raise ValueError("claude local_cli execution must use the Claude runner adapter")
    command = build_local_cli_command(provider, prompt, cwd=cwd, session_id=session_id)
    return _run_process(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        stream_output_path=stream_output_path,
        parse=lambda stdout, stderr: parse_local_cli_output(spec.provider, stdout, stderr),
        session_id=session_id,
        extra_env=extra_env,
    )


def local_cli_doctor() -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for provider in LOCAL_CLI_PROVIDERS:
        spec = local_cli_spec(provider)
        executable = shutil.which(spec.executable)
        status: dict[str, Any] = {
            "provider": provider,
            "executable": spec.executable,
            "path": executable or "",
            "available": bool(executable),
            "supports_resume": spec.supports_resume,
            "supports_budget": spec.supports_budget,
            "permission_mode": spec.permission_mode,
            "version": "",
        }
        if executable:
            try:
                result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
                status["version"] = (result.stdout + result.stderr).strip().splitlines()[0] if (result.stdout + result.stderr).strip() else ""
                status["version_returncode"] = result.returncode
            except (OSError, subprocess.SubprocessError) as exc:
                status["version_error"] = str(exc)
        statuses.append(status)
    return statuses
