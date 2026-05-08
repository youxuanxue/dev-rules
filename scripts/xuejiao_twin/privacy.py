from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import REDACTION_VERSION

_SECRET_PATTERNS = (
    ("secret_assignment", re.compile(r"(?i)\b(token|api[_-]?key|secret|password)\s*=\s*[^\s,;]+")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
)
_URL_WITH_SECRET_RE = re.compile(r"https?://[^\s\"'`]+")
_URL_SECRET_RE = re.compile(r"(?i)([?&](token|key|api[_-]?key|secret|password)=|://[^/\s:]+:[^/\s@]+@)")


@dataclass
class PrivacyReport:
    redaction_version: str = REDACTION_VERSION
    redactions: dict[str, int] = field(default_factory=dict)
    flagged_turns: int = 0

    def add(self, flag: str, count: int = 1) -> None:
        self.redactions[flag] = self.redactions.get(flag, 0) + count

    def as_dict(self) -> dict[str, Any]:
        return {
            "redaction_version": self.redaction_version,
            "redactions": dict(sorted(self.redactions.items())),
            "flagged_turns": self.flagged_turns,
        }


def stable_hash(value: Any, *, length: int = 16) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def redact_text(text: str, report: PrivacyReport | None = None) -> tuple[str, list[str]]:
    flags: list[str] = []
    redacted = text or ""

    for flag, pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn(f"[{flag.upper()}_REDACTED]", redacted)
        if count:
            flags.append(flag)
            if report:
                report.add(flag, count)

    def redact_url(match: re.Match[str]) -> str:
        url = match.group(0)
        if _URL_SECRET_RE.search(url):
            flags.append("sensitive_url")
            if report:
                report.add("sensitive_url")
            return "[URL_REDACTED]"
        return url

    redacted = _URL_WITH_SECRET_RE.sub(redact_url, redacted)

    return redacted, sorted(set(flags))


def redact_value(value: Any, report: PrivacyReport | None = None) -> Any:
    if isinstance(value, str):
        return redact_text(value, report)[0]
    if isinstance(value, list):
        return [redact_value(item, report) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_value(child, report) for key, child in value.items()}
    return value


def assert_no_private_leak(value: Any) -> list[str]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    leaks: list[str] = []
    for flag, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            leaks.append(flag)
    if any(_URL_SECRET_RE.search(match.group(0)) for match in _URL_WITH_SECRET_RE.finditer(text)):
        leaks.append("sensitive_url")
    return sorted(set(leaks))
