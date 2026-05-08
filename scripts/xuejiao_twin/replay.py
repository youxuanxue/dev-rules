from __future__ import annotations

import json
from pathlib import Path

from .util import read_json


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
    return "\n".join(lines) + "\n"
