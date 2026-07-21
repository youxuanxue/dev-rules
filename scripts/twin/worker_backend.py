from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .claude_runner import ClaudeRunResult, run_claude_headless
from .contracts import DEV_RULES_ROOT
from .local_cli import local_cli_spec, run_local_cli


CAO_BASE_URL_ENV = "TWIN_CAO_BASE_URL"
CAO_AUTH_TOKEN_ENV = "CAO_AUTH_LOCAL_TOKEN"
DEFAULT_CAO_BASE_URL = "http://127.0.0.1:9889"


@dataclass(frozen=True)
class BackendIdentity:
    backend: str
    provider: str
    agent: str
    permission_mode: str


class WorkerBackend(Protocol):
    identity: BackendIdentity
    supports_resume: bool
    supports_budget: bool

    def run_turn(
        self,
        prompt: str,
        *,
        cwd: Path,
        allowed_tools: list[str],
        disallowed_tools: list[str],
        max_budget_usd: float,
        session_id: str,
        timeout_seconds: int,
        stream_output_path: Path,
    ) -> ClaudeRunResult: ...


class ClaudeHeadlessBackend:
    supports_resume = True
    supports_budget = True

    def __init__(self, runner: Callable[..., ClaudeRunResult] = run_claude_headless) -> None:
        self.runner = runner
        self.identity = BackendIdentity(
            backend="claude_headless",
            provider="claude_code",
            agent="twin_worker",
            permission_mode="bypassPermissions",
        )

    def run_turn(
        self,
        prompt: str,
        *,
        cwd: Path,
        allowed_tools: list[str],
        disallowed_tools: list[str],
        max_budget_usd: float,
        session_id: str,
        timeout_seconds: int,
        stream_output_path: Path,
    ) -> ClaudeRunResult:
        return self.runner(
            prompt,
            cwd=cwd,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            max_budget_usd=max_budget_usd,
            session_id=session_id,
            timeout_seconds=timeout_seconds,
            permission_mode=self.identity.permission_mode,
            role="worker",
            extra_env={"DEV_RULES": str(DEV_RULES_ROOT)},
            stream_output_path=stream_output_path,
        )


class CaoWorkerBackend:
    """Stateless twin turn through CAO's stable run-step HTTP contract."""

    supports_resume = False
    supports_budget = False

    def __init__(
        self,
        *,
        provider: str,
        agent: str,
        base_url: str | None = None,
        auth_token: str | None = None,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.identity = BackendIdentity(
            backend="cao",
            provider=provider,
            agent=agent,
            permission_mode="cao_profile",
        )
        self.base_url = (base_url or os.environ.get(CAO_BASE_URL_ENV) or DEFAULT_CAO_BASE_URL).rstrip("/")
        self.auth_token = auth_token if auth_token is not None else os.environ.get(CAO_AUTH_TOKEN_ENV, "").strip()
        self.opener = opener

    def _request(self, payload: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        request = Request(
            f"{self.base_url}/terminals/run-step",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with self.opener(request, timeout=timeout_seconds + 30) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("CAO run-step response must be an object")
        missing = sorted({"terminal_id", "last_message", "status"} - value.keys())
        if missing:
            raise ValueError(f"CAO run-step response is missing: {', '.join(missing)}")
        if not isinstance(value["terminal_id"], str) or not value["terminal_id"].strip():
            raise ValueError("CAO run-step response has an invalid terminal_id")
        if not isinstance(value["last_message"], str):
            raise ValueError("CAO run-step response has an invalid last_message")
        if value["status"] != "completed":
            raise ValueError(f"CAO run-step response has unexpected status: {value['status']!r}")
        return value

    @staticmethod
    def _http_failure(exc: HTTPError) -> tuple[str, str, str]:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return f"CAO HTTP {exc.code}: {body or exc.reason}", "http_error", ""
        detail = parsed.get("detail") if isinstance(parsed, dict) else None
        if isinstance(detail, dict):
            return (
                str(detail.get("message") or f"CAO HTTP {exc.code}"),
                str(detail.get("kind") or "http_error"),
                str(detail.get("terminal_id") or ""),
            )
        return f"CAO HTTP {exc.code}: {detail or body}", "http_error", ""

    def run_turn(
        self,
        prompt: str,
        *,
        cwd: Path,
        allowed_tools: list[str],
        disallowed_tools: list[str],
        max_budget_usd: float,
        session_id: str,
        timeout_seconds: int,
        stream_output_path: Path,
    ) -> ClaudeRunResult:
        # Claude tool names are not CAO's tool vocabulary. Omitting the field
        # lets CAO resolve permissions from the selected agent profile.
        del allowed_tools, disallowed_tools, max_budget_usd, session_id
        payload = {
            "provider": self.identity.provider,
            "agent": self.identity.agent,
            "prompt": prompt,
            "teardown": True,
            "timeout": float(timeout_seconds),
            "working_directory": str(cwd),
        }
        try:
            response = self._request(payload, timeout_seconds=timeout_seconds)
            terminal_id = str(response.get("terminal_id") or "")
            output = str(response.get("last_message") or "")
            status = str(response.get("status") or "")
            events = [{
                "type": "cao_run_step",
                "terminal_id": terminal_id,
                "provider": self.identity.provider,
                "agent": self.identity.agent,
                "status": status,
            }]
            stream_output_path.parent.mkdir(parents=True, exist_ok=True)
            stream_output_path.write_text(
                json.dumps(events[0], ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return ClaudeRunResult(
                session_id=terminal_id,
                output_text=output,
                returncode=0,
                raw_events=events,
                cwd=str(cwd),
            )
        except HTTPError as exc:
            message, kind, terminal_id = self._http_failure(exc)
            return ClaudeRunResult(
                session_id=terminal_id,
                output_text=message,
                returncode=124 if kind == "timeout" else 1,
                raw_events=[{"type": "cao_run_step", "subtype": kind, "status": "failed"}],
                cwd=str(cwd),
                session_lost=kind != "timeout",
            )
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return ClaudeRunResult(
                session_id="",
                output_text=f"CAO run-step unavailable: {exc}",
                returncode=1,
                raw_events=[{"type": "cao_run_step", "subtype": "transport_error", "status": "failed"}],
                cwd=str(cwd),
                session_lost=True,
            )


class LocalCliWorkerBackend:
    """Run an installed provider CLI directly, without a CAO server."""

    def __init__(
        self,
        *,
        provider: str,
        claude_runner: Callable[..., ClaudeRunResult] = run_claude_headless,
        local_runner: Callable[..., ClaudeRunResult] = run_local_cli,
    ) -> None:
        spec = local_cli_spec(provider)
        self.identity = BackendIdentity(
            backend="local_cli",
            provider=provider,
            agent="",
            permission_mode=spec.permission_mode,
        )
        self.supports_resume = spec.supports_resume
        self.supports_budget = spec.supports_budget
        self.claude_runner = claude_runner
        self.local_runner = local_runner

    def run_turn(
        self,
        prompt: str,
        *,
        cwd: Path,
        allowed_tools: list[str],
        disallowed_tools: list[str],
        max_budget_usd: float,
        session_id: str,
        timeout_seconds: int,
        stream_output_path: Path,
    ) -> ClaudeRunResult:
        if self.identity.provider == "claude":
            return self.claude_runner(
                prompt,
                cwd=cwd,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
                max_budget_usd=max_budget_usd,
                session_id=session_id,
                timeout_seconds=timeout_seconds,
                permission_mode=self.identity.permission_mode,
                role="worker",
                extra_env={"DEV_RULES": str(DEV_RULES_ROOT)},
                stream_output_path=stream_output_path,
            )
        # Codex and Gemini do not expose Claude's tool allow/deny vocabulary.
        # Their explicit sandbox/approval modes are part of the adapter command;
        # the worker persona remains the policy layer for provider-neutral rules.
        del allowed_tools, disallowed_tools, max_budget_usd
        result = self.local_runner(
            self.identity.provider,
            prompt,
            cwd=cwd,
            session_id=session_id,
            timeout_seconds=timeout_seconds,
            stream_output_path=stream_output_path,
            extra_env={"DEV_RULES": str(DEV_RULES_ROOT)},
        )
        policy_event = {
            "type": "local_cli_policy",
            "provider": self.identity.provider,
            "permission_mode": self.identity.permission_mode,
            "tool_filters": "provider_default; twin persona restrictions are prompt-enforced",
            "budget": "unsupported; explicit twin budget overrides fail closed",
        }
        result.raw_events.insert(0, policy_event)
        stream_output_path.parent.mkdir(parents=True, exist_ok=True)
        with stream_output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(policy_event, ensure_ascii=False, sort_keys=True) + "\n")
        return result


def resolve_worker_backend(
    plan: dict[str, Any],
    *,
    runner: Callable[..., ClaudeRunResult] | None = None,
) -> WorkerBackend:
    if runner is not None:
        return ClaudeHeadlessBackend(runner)
    execution = plan.get("execution")
    if not isinstance(execution, dict) or not execution:
        return ClaudeHeadlessBackend()
    backend = str(execution.get("backend") or "claude_headless")
    if backend == "claude_headless":
        return ClaudeHeadlessBackend()
    if backend == "local_cli":
        return LocalCliWorkerBackend(provider=str(execution.get("provider") or ""))
    if backend == "cao":
        return CaoWorkerBackend(
            provider=str(execution.get("provider") or ""),
            agent=str(execution.get("agent") or ""),
        )
    raise ValueError(f"unsupported twin worker backend: {backend}")
