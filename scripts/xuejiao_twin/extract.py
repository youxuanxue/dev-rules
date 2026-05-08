from __future__ import annotations

from collections import Counter
from typing import Any

LABEL_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kickoff_minimal", ("先做", "最小", "闭环", "骨架", "demo", "可跑")),
    ("scope_control", ("别铺太大", "不要", "不引入", "不重构", "只", "收回来", "核心")),
    ("evidence_request", ("测试", "preflight", "验证", "diff", "summary", "截图", "跑完")),
    ("steering", ("偏了", "重做", "修复", "继续", "停下来", "问我")),
    ("human_gate", ("架构", "安全", "数据", "审批", "问我", "依赖", "外部")),
    ("completion", ("可以了", "merge", "先到这", "OK")),
    ("retry_stop", ("连续", "失败", "三次", "3 次", "停")),
)


def label_text(text: str) -> list[str]:
    labels: list[str] = []
    for label, keywords in LABEL_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            labels.append(label)
    return labels


def summarize_tool_content(content: Any) -> dict[str, Any]:
    names: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"tool_use", "tool_result"}:
                name = item.get("name") or item.get("tool_name") or item.get("id") or item.get("type")
                names.append(str(name))
    return {"tool_count": len(names), "tools": sorted(set(names))}


def support_counts(turns: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for turn in turns:
        if turn.get("role") == "user":
            counter.update(turn.get("behavior_labels", []))
    return dict(sorted(counter.items()))


def first_policy_line(label: str) -> str:
    lines = {
        "kickoff_minimal": "优先要求最小可跑闭环，先证明主路径成立。",
        "scope_control": "压缩范围，明确不做项，避免引入非核心改动。",
        "evidence_request": "要求用测试、preflight、diff summary 或可观察结果证明进展。",
        "steering": "发现偏航时直接纠偏，并让 agent 回到当前目标。",
        "human_gate": "遇到架构、安全、数据、依赖或外部副作用时停止并交给真人。",
        "completion": "验收满足后要求最终证据并停止，不继续扩展。",
        "retry_stop": "同一问题连续失败 3 次后停止，记录阻塞原因。",
    }
    return lines.get(label, f"根据 {label} 历史信号执行监督。")
