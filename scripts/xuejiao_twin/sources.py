from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from . import REDACTION_VERSION, SCHEMA_VERSION
from .extract import label_text, summarize_tool_content
from .privacy import PrivacyReport, redact_text, stable_hash
from .util import date_range, normalize_timestamp

SOURCE_PATTERNS = (
    ("cursor_agent_transcript", ".cursor/projects", "**/agent-transcripts/**/*.jsonl"),
    ("claude_project_jsonl", ".claude/projects", "**/*.jsonl"),
)


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    return ""


def _line_to_turn(obj: dict[str, Any], source_ref: str, line_no: int, report: PrivacyReport) -> dict[str, Any] | None:
    typ = obj.get("type")
    message = obj.get("message")
    role = "unknown"
    if isinstance(message, dict):
        role = str(message.get("role") or "unknown")
    elif typ in {"user", "assistant", "system"}:
        role = str(typ)
    if typ not in {"user", "assistant", "system"} and role not in {"user", "assistant", "system"}:
        return None

    text = _message_text(message or obj.get("display", ""))
    redacted, flags = redact_text(text, report)
    if flags:
        report.flagged_turns += 1
    labels = label_text(redacted) if role == "user" else []
    content = message.get("content") if isinstance(message, dict) else None
    return {
        "turn_id": f"{source_ref}:{line_no}",
        "source_ref": source_ref,
        "role": role if role in {"user", "assistant", "system"} else "unknown",
        "timestamp": normalize_timestamp(obj.get("timestamp")),
        "text_redacted": redacted[:600],
        "content_hash": stable_hash(text),
        "tool_summary": summarize_tool_content(content),
        "behavior_labels": labels,
        "privacy_flags": flags,
    }


def _source_record(source_type: str, path: Path, source_ref: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    session_basis = source_ref
    return {
        "source_ref": source_ref,
        "source_type": source_type,
        "source_hash": stable_hash(str(path)),
        "session_hash": stable_hash(session_basis),
        "project_hash": stable_hash(str(path.parent)),
        "turn_count": len(turns),
        "time_range": date_range([turn.get("timestamp") for turn in turns]),
        "coverage_status": "parsed" if turns else "metadata_only",
    }


def parse_jsonl(path: Path, source_type: str, report: PrivacyReport) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_ref = stable_hash(f"{source_type}:{path}")
    turns: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                turn = _line_to_turn(obj, source_ref, line_no, report)
                if turn:
                    turns.append(turn)
    except OSError:
        pass
    return _source_record(source_type, path, source_ref, turns), turns


def parse_claude_history(path: Path, report: PrivacyReport) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_ref = stable_hash(f"claude_history:{path}")
    turns: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = str(obj.get("display") or "")
                if not text:
                    pasted = obj.get("pastedContents")
                    if isinstance(pasted, list):
                        text = "\n".join(str(item.get("content", "")) for item in pasted if isinstance(item, dict))
                redacted, flags = redact_text(text, report)
                if flags:
                    report.flagged_turns += 1
                turns.append({
                    "turn_id": f"{source_ref}:{line_no}",
                    "source_ref": source_ref,
                    "role": "user",
                    "timestamp": normalize_timestamp(obj.get("timestamp")),
                    "text_redacted": redacted[:600],
                    "content_hash": stable_hash(text),
                    "tool_summary": {"tool_count": 0, "tools": []},
                    "behavior_labels": label_text(redacted),
                    "privacy_flags": flags,
                })
    except OSError:
        pass
    return _source_record("claude_history", path, source_ref, turns), turns


def parse_cursor_store_metadata(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_ref = stable_hash(f"cursor_store_metadata:{path}")
    blob_count = 0
    meta_count = 0
    try:
        con = sqlite3.connect(str(path))
        try:
            blob_count = int(con.execute("select count(*) from blobs").fetchone()[0])
            meta_count = int(con.execute("select count(*) from meta").fetchone()[0])
        finally:
            con.close()
    except Exception:
        pass
    source = {
        "source_ref": source_ref,
        "source_type": "cursor_store_metadata",
        "source_hash": stable_hash(str(path)),
        "session_hash": stable_hash(str(path.parent)),
        "project_hash": stable_hash(str(path.parent.parent)),
        "turn_count": 0,
        "time_range": {"start": None, "end": None},
        "coverage_status": f"metadata_only:blobs={blob_count},meta={meta_count}",
    }
    return source, []


def project_needles(project: str) -> set[str]:
    if not project:
        return set()
    resolved = str(Path(project).expanduser())
    slug = resolved.strip("/").replace("/", "-")
    basename = Path(resolved).name
    return {resolved, slug, basename, stable_hash(resolved)}


def matches_project(path: Path, needles: set[str]) -> bool:
    if not needles:
        return True
    text = str(path)
    return any(needle and needle in text for needle in needles)


def fixture_paths(fixtures_dir: Path) -> list[tuple[str, Path]]:
    return [
        ("claude_project_jsonl", fixtures_dir / "claude_session.jsonl"),
        ("cursor_agent_transcript", fixtures_dir / "cursor_agent.jsonl"),
    ]


def discover_sources(home: Path, *, include_cursor_store: bool = True) -> Iterable[tuple[str, Path]]:
    for source_type, root_rel, pattern in SOURCE_PATTERNS:
        root = home / root_rel
        if root.exists():
            yield from ((source_type, path) for path in root.glob(pattern) if path.is_file())
    history = home / ".claude/history.jsonl"
    if history.exists():
        yield ("claude_history", history)
    if include_cursor_store:
        chats = home / ".cursor/chats"
        if chats.exists():
            yield from (("cursor_store_metadata", path) for path in chats.glob("**/store.db") if path.is_file())


def build_index(paths: Iterable[tuple[str, Path]], *, generated_at: str) -> dict[str, Any]:
    report = PrivacyReport()
    sources: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    for source_type, path in sorted(paths, key=lambda item: (item[0], str(item[1]))):
        if source_type == "claude_history":
            source, source_turns = parse_claude_history(path, report)
        elif source_type == "cursor_store_metadata":
            source, source_turns = parse_cursor_store_metadata(path)
        else:
            source, source_turns = parse_jsonl(path, source_type, report)
        sources.append(source)
        turns.extend(source_turns)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "redaction_version": REDACTION_VERSION,
        "sources": sources,
        "turns": turns,
        "privacy_report": report.as_dict(),
    }
