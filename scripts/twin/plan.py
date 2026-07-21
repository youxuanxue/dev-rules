from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contracts import ITEM_STATUSES
from .local_cli import LOCAL_CLI_PROVIDERS


_BOUNDARY_MARKERS = ("只", "仅", "不", "不得", "边界", "范围", "non-goal", "non-goals", "scope")
_BUDGET_MARKERS = ("证据预算", "evidence budget", "最多", "只跑", "仅跑", "不跑全量")
_STOP_MARKERS = ("停止条件", "stop condition", "转 review", "进入 review", "needs_human", "暂停", "停下")
_FINAL_MARKERS = ("final", "最终", "验收", "preflight", "summary", "acceptance")


def ac_ids(goal: dict[str, Any]) -> set[str]:
    return {str(item.get("id")) for item in goal.get("acceptance_criteria", []) if isinstance(item, dict) and item.get("id")}


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _normalized(text: Any) -> str:
    return str(text or "").strip()


def validate_bootstrap_plan_constraints(goal: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    items = plan.get("items", [])
    if not isinstance(items, list):
        return ["bootstrap plan.items must be a list"]

    acceptance_count = len([item for item in goal.get("acceptance_criteria", []) if isinstance(item, dict)])
    if acceptance_count > 1 and len(items) == 1:
        errors.append("bootstrap plan must split multi-AC goals into multiple bounded items")

    raw_goal_texts = {
        _normalized(goal.get("one_liner")),
        _normalized(goal.get("core_goal")),
    } - {""}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"bootstrap items[{index}] must be object")
            continue
        item_id = _normalized(item.get("id")) or f"items[{index}]"
        deliverable = _normalized(item.get("deliverable"))
        scope = _normalized(item.get("scope"))
        evidence_text = "\n".join(str(entry or "") for entry in item.get("evidence_plan", []))
        next_action = _normalized(item.get("next_action"))
        status = _normalized(item.get("status"))

        if deliverable in raw_goal_texts:
            errors.append(f"{item_id}: bootstrap deliverable must not copy the raw goal")
        if not _contains_any(scope, _BOUNDARY_MARKERS):
            errors.append(f"{item_id}: bootstrap scope must state a boundary or non-goal")
        if not _contains_any(evidence_text, _BUDGET_MARKERS):
            errors.append(f"{item_id}: evidence_plan must include an explicit evidence budget")
        if not _contains_any(evidence_text + "\n" + next_action, _STOP_MARKERS):
            errors.append(f"{item_id}: evidence_plan or next_action must include a stop/review condition")
        if status in {"blocked", "deferred"} and not _normalized(item.get("blocked_reason")):
            errors.append(f"{item_id}: blocked/deferred bootstrap item must include blocked_reason")
        if len(items) > 1 and _contains_any(deliverable + "\n" + scope, _FINAL_MARKERS) and not item.get("depends_on"):
            errors.append(f"{item_id}: final acceptance/check item must depend on earlier deliverables")

    return errors


def validate_plan_semantics(goal: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if plan.get("goal_id") != goal.get("id"):
        errors.append("plan.goal_id must match goal.id")

    execution = plan.get("execution")
    if isinstance(execution, dict):
        backend = execution.get("backend")
        if backend == "cao":
            if not str(execution.get("provider") or "").strip():
                errors.append("plan.execution.provider is required for the cao backend")
            if not str(execution.get("agent") or "").strip():
                errors.append("plan.execution.agent is required for the cao backend")
        elif backend == "local_cli":
            provider = str(execution.get("provider") or "").strip()
            if not provider:
                errors.append("plan.execution.provider is required for the local_cli backend")
            elif provider not in LOCAL_CLI_PROVIDERS:
                errors.append(
                    "plan.execution.provider must be one of: " + ", ".join(LOCAL_CLI_PROVIDERS)
                )
            if "agent" in execution:
                errors.append("plan.execution.agent is only valid for the cao backend")
        elif backend == "claude_headless":
            for key in ("provider", "agent"):
                if key in execution:
                    errors.append(f"plan.execution.{key} is only valid for the cao backend")

    known_ac = ac_ids(goal)
    items = plan.get("items", [])
    if not isinstance(items, list):
        return errors + ["plan.items must be a list"]

    item_ids: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    covered_ac: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"items[{index}] must be object")
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            errors.append(f"items[{index}].id is required")
            continue
        if item_id in item_ids:
            errors.append(f"duplicate plan item id: {item_id}")
        item_ids.add(item_id)
        status = item.get("status")
        if status not in ITEM_STATUSES:
            errors.append(f"{item_id}: invalid status {status!r}")
        for ac_id in item.get("covers_ac", []):
            if ac_id not in known_ac:
                errors.append(f"{item_id}: unknown AC id {ac_id}")
            else:
                covered_ac.add(str(ac_id))
        forbidden_text = "\n".join(str(item.get(key, "")) for key in ("deliverable", "scope", "next_action"))
        for ac in goal.get("acceptance_criteria", []):
            if isinstance(ac, dict) and str(ac.get("statement") or "") and str(ac.get("statement")) in forbidden_text:
                errors.append(f"{item_id}: repeats AC statement instead of referencing AC id")
        dependencies[item_id] = [str(dep) for dep in item.get("depends_on", [])]

    for ac_id in sorted(known_ac - covered_ac):
        errors.append(f"acceptance criterion not covered by plan: {ac_id}")

    for item_id, deps in dependencies.items():
        for dep in deps:
            if dep not in item_ids:
                errors.append(f"{item_id}: unknown dependency {dep}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str, path: list[str]) -> None:
        if item_id in visited:
            return
        if item_id in visiting:
            errors.append("dependency cycle: " + " -> ".join(path + [item_id]))
            return
        visiting.add(item_id)
        for dep in dependencies.get(item_id, []):
            visit(dep, path + [item_id])
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in dependencies:
        visit(item_id, [])
    return errors


def item_counts(plan: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in plan.get("items", []):
        if isinstance(item, dict):
            counts[str(item.get("status") or "unknown")] += 1
    return dict(counts)


def choose_next_item(plan: dict[str, Any]) -> dict[str, Any] | None:
    items = [item for item in plan.get("items", []) if isinstance(item, dict)]
    item_by_id = {str(item.get("id")): item for item in items}
    for item in items:
        if item.get("status") == "in_progress":
            return item
    for item in items:
        if item.get("status") != "pending":
            continue
        deps = [item_by_id.get(str(dep)) for dep in item.get("depends_on", [])]
        if all(dep and dep.get("status") == "completed" for dep in deps):
            return item
    for item in items:
        if item.get("status") == "blocked":
            return item
    return None


def apply_plan_updates(plan: dict[str, Any], updates: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    items = plan.get("items", [])
    item_by_id = {str(item.get("id")): item for item in items if isinstance(item, dict)}
    for update in updates:
        item_id = str(update.get("item_id") or "")
        item = item_by_id.get(item_id)
        if item is None:
            errors.append(f"unknown plan update item_id: {item_id}")
            continue
        if "status" in update:
            item["status"] = update["status"]
        if "actual_evidence" in update:
            evidence = update.get("actual_evidence") or []
            if isinstance(evidence, list):
                existing = list(item.get("actual_evidence") or [])
                for entry in evidence:
                    if entry not in existing:
                        existing.append(str(entry))
                item["actual_evidence"] = existing
        if "next_action" in update:
            item["next_action"] = str(update.get("next_action") or "")
        if "blocked_reason" in update:
            item["blocked_reason"] = update.get("blocked_reason")
    return errors


def ac_evidence_map(plan: dict[str, Any]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = defaultdict(list)
    for item in plan.get("items", []):
        if not isinstance(item, dict):
            continue
        for ac_id in item.get("covers_ac", []):
            for entry in item.get("actual_evidence", []):
                if entry not in evidence[str(ac_id)]:
                    evidence[str(ac_id)].append(str(entry))
    return dict(evidence)


def plan_gaps(goal: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    evidence = ac_evidence_map(plan)
    for ac in goal.get("acceptance_criteria", []):
        if not isinstance(ac, dict):
            continue
        ac_id = str(ac.get("id") or "")
        if ac_id and not evidence.get(ac_id):
            gaps.append(f"{ac_id}: missing accepted evidence")

    items = [item for item in plan.get("items", []) if isinstance(item, dict)]
    item_by_id = {str(item.get("id")): item for item in items}
    for item in items:
        item_id = str(item.get("id") or "")
        status = str(item.get("status") or "")
        if status == "completed":
            continue
        if status == "blocked":
            reason = str(item.get("blocked_reason") or item.get("next_action") or "blocked")
            gaps.append(f"{item_id}: blocked - {reason}")
            continue
        deps = [str(dep) for dep in item.get("depends_on", [])]
        open_deps = [dep for dep in deps if item_by_id.get(dep, {}).get("status") != "completed"]
        if open_deps:
            gaps.append(f"{item_id}: waiting for dependencies {', '.join(open_deps)}")
        else:
            gaps.append(f"{item_id}: {status or 'unknown'} - {item.get('next_action') or item.get('deliverable')}")
    return gaps


def acceptance_evidence(goal: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = ac_evidence_map(plan)
    result: list[dict[str, Any]] = []
    for ac in goal.get("acceptance_criteria", []):
        if isinstance(ac, dict) and ac.get("id"):
            result.append({"ac_id": str(ac["id"]), "evidence": evidence.get(str(ac["id"]), [])})
    return result


def acceptance_focus(goal: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    next_item = choose_next_item(plan)
    evidence = ac_evidence_map(plan)
    criteria_by_id = {
        str(ac.get("id")): ac
        for ac in goal.get("acceptance_criteria", [])
        if isinstance(ac, dict) and ac.get("id")
    }
    open_acceptance_criteria = [
        {
            "id": ac_id,
            "statement": str(ac.get("statement") or ""),
            "evidence_type": str(ac.get("evidence_type") or ""),
        }
        for ac_id, ac in criteria_by_id.items()
        if not evidence.get(ac_id)
    ]
    covered_ids = [str(ac_id) for ac_id in next_item.get("covers_ac", [])] if isinstance(next_item, dict) else []
    current_item_acceptance_criteria = [
        {
            "id": ac_id,
            "statement": str(criteria_by_id.get(ac_id, {}).get("statement") or ""),
            "evidence_type": str(criteria_by_id.get(ac_id, {}).get("evidence_type") or ""),
            "has_evidence": bool(evidence.get(ac_id)),
        }
        for ac_id in covered_ids
        if ac_id in criteria_by_id
    ]
    open_items = [
        item
        for item in plan.get("items", [])
        if isinstance(item, dict) and item.get("status") != "completed"
    ]
    open_ac_ids = {str(ac["id"]) for ac in open_acceptance_criteria}
    covered_ac_ids = set(covered_ids)
    last_mile = bool(next_item) and (
        len(open_items) == 1 or bool(open_ac_ids and open_ac_ids.issubset(covered_ac_ids))
    )
    return {
        "next_item": next_item,
        "open_acceptance_criteria": open_acceptance_criteria,
        "current_item_acceptance_criteria": current_item_acceptance_criteria,
        "remaining_gaps": plan_gaps(goal, plan),
        "last_mile": last_mile,
    }
