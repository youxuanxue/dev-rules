from __future__ import annotations

import json
from pathlib import Path

from .util import read_json


def _blocked_from_ledger(run_path: Path, run: dict[str, object]) -> list[dict[str, object]]:
    ledger_ref = Path(str(run.get("ledger_ref", "")))
    if not ledger_ref.is_absolute():
        ledger_ref = run_path.parent / ledger_ref
    if not ledger_ref.exists():
        return []
    ledger = read_json(ledger_ref)
    if not isinstance(ledger, dict):
        return []
    items: list[dict[str, object]] = []
    for feature in ledger.get("features", []):
        if not isinstance(feature, dict):
            continue
        if feature.get("status") not in {"blocked", "deferred"}:
            continue
        items.append({
            "id": feature.get("id"),
            "description": feature.get("description"),
            "blocked_reason": feature.get("blocked_reason"),
        })
    return items


def replay_run(run_path: Path) -> str:
    run = read_json(run_path)
    lines = [
        f"run_id: {run.get('run_id')}",
        f"outcome: {run.get('outcome')}",
        f"stop_reason: {run.get('stop_reason')}",
        f"supervisor_session_id: {run.get('supervisor_session_id')}",
        f"worker_session_id: {run.get('worker_session_id')}",
        "events:",
    ]
    events_ref = Path(str(run.get("events_ref", "")))
    if not events_ref.is_absolute():
        events_ref = run_path.parent / events_ref
    if events_ref.exists():
        for raw in events_ref.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            event = json.loads(raw)
            text = str(event.get("text_redacted", "")).replace("\n", " ")
            lines.append(f"- {event.get('type')}: {text[:240]}")
    else:
        lines.append("- events file missing")

    review = run.get("human_review")
    lines.append("decision_summary:")
    if isinstance(review, dict):
        lines.append(f"- needed: {review.get('needed')}")
        lines.append(f"- trigger: {review.get('trigger')}")
        lines.append(f"- current_focus: {review.get('current_focus')}")
        lines.append(f"- summary: {review.get('summary')}")
        blocked = review.get("blocked_features")
        if isinstance(blocked, list) and blocked:
            lines.append("- blocked_features:")
            for item in blocked:
                if not isinstance(item, dict):
                    continue
                lines.append(f"  - {item.get('id')}: {item.get('description')}")
                reason = str(item.get("blocked_reason") or "")
                if reason:
                    lines.append(f"    reason: {reason}")
    else:
        blocked = _blocked_from_ledger(run_path, run)
        lines.append("- needed: unknown")
        lines.append(f"- trigger: {run.get('outcome')}")
        lines.append(f"- summary: {run.get('stop_reason')}")
        if blocked:
            lines.append("- blocked_features:")
            for item in blocked:
                lines.append(f"  - {item.get('id')}: {item.get('description')}")
                reason = str(item.get("blocked_reason") or "")
                if reason:
                    lines.append(f"    reason: {reason}")
    return "\n".join(lines) + "\n"
