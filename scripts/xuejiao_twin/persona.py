from __future__ import annotations

from typing import Any

from . import SCHEMA_VERSION
from .extract import first_policy_line, support_counts
from .util import date_range

POLICY_BUCKETS = {
    "kickoff": ["kickoff_minimal"],
    "scope_control": ["scope_control"],
    "evidence_requests": ["evidence_request"],
    "review_preflight_policy": ["evidence_request"],
    "retry_policy": ["retry_stop"],
    "stop_policy": ["completion", "retry_stop"],
    "human_gate_policy": ["human_gate"],
}

DEFAULT_POLICIES = {
    "kickoff": "先要求最小可跑闭环，避免一开始扩大范围。",
    "scope_control": "明确 scope_in/scope_out，只推进当前核心目标。",
    "evidence_requests": "每轮要求可验证证据，而不是只听实现描述。",
    "review_preflight_policy": "完成前要求测试、preflight 或 review 证据。",
    "retry_policy": "失败后要求定位根因；同类失败累计 3 次停止。",
    "stop_policy": "验收满足后停止，不继续加功能。",
    "human_gate_policy": "架构、安全、数据、依赖、外部副作用交给真人。",
}


def derive_persona(index: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    turns = list(index.get("turns", []))
    counts = support_counts(turns)
    source_hashes = [str(source.get("source_hash")) for source in index.get("sources", [])]
    timestamp_range = date_range([turn.get("timestamp") for turn in turns])

    policy: dict[str, list[str]] = {}
    warnings: list[str] = []
    for bucket, labels in POLICY_BUCKETS.items():
        lines = [first_policy_line(label) for label in labels if counts.get(label, 0) > 0]
        if not lines:
            lines = [DEFAULT_POLICIES[bucket]]
            warnings.append(f"{bucket}: low support, using default dev-rules policy")
        policy[bucket] = lines

    user_turns = [turn for turn in turns if turn.get("role") == "user"]
    average_len = 0
    if user_turns:
        average_len = sum(len(str(turn.get("text_redacted", ""))) for turn in user_turns) // len(user_turns)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "derived_from": {
            "source_hashes": sorted(set(source_hashes)),
            "session_count": len(source_hashes),
            "turn_count": len(turns),
            "date_range": timestamp_range,
        },
        "style": {
            "language": "Chinese-first with terse engineering terms",
            "typical_length": "short" if average_len <= 120 else "medium",
            "directness": "direct, scope-controlling, evidence-oriented",
            "common_instruction_forms": [
                "先做最小可跑闭环",
                "不要扩大范围",
                "给出 diff summary 和验证证据",
                "遇到架构/安全/数据问题停下来问我",
            ],
        },
        "interaction_policy": policy,
        "confidence": {
            "support_counts": counts,
            "low_confidence_warnings": warnings,
        },
        "known_limits": [
            "Persona is derived from redacted local history and must not infer private facts not present in artifacts.",
            "Supervisor must stop at human gates instead of replacing xuejiao for high-risk decisions.",
        ],
    }
