"""Human-intervention resolution and decision memory helpers."""

from __future__ import annotations

from typing import Any


def resolve_intervention(intervention: dict[str, Any], answer: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_answer(answer)
    affected = [str(item) for item in _as_list(answer.get("affected_subfunctions"))] or ["*"]
    decision = {
        "version": 1,
        "status": "resolved",
        "decision": str(answer["decision"]),
        "evidence": _as_list(answer.get("evidence", [])),
        "constraints": _as_list(answer.get("constraints", [])),
        "affected_subfunctions": affected,
        "source_intervention": {
            "primary_source": intervention.get("primary_source"),
            "question": intervention.get("question"),
        },
    }
    memory = {
        "version": 1,
        "entries": [
            {
                "subfunction": subfunction,
                "stage": "*",
                "error_signature": "human_decision",
                "constraint": _constraint_text(decision),
                "decision": decision["decision"],
            }
            for subfunction in affected
        ],
    }
    return decision, memory


def decision_applies(decision: dict[str, Any] | None, subfunction: str | None) -> bool:
    if not decision:
        return False
    affected = [str(item) for item in decision.get("affected_subfunctions", []) or []]
    return not affected or "*" in affected or subfunction is None or str(subfunction) in affected


def _validate_answer(answer: dict[str, Any]) -> None:
    if not isinstance(answer, dict):
        raise ValueError("Human intervention answer must be a JSON object.")
    missing = [field for field in ("decision", "evidence", "constraints", "affected_subfunctions") if field not in answer]
    if missing:
        raise ValueError("Human intervention answer is missing required fields: " + ", ".join(missing))
    if not str(answer.get("decision", "")).strip():
        raise ValueError("Human intervention answer decision must not be empty.")


def _constraint_text(decision: dict[str, Any]) -> str:
    constraints = "; ".join(str(item) for item in decision.get("constraints", []) if str(item).strip())
    if constraints:
        return f"Human decision: {decision['decision']}. Constraints: {constraints}."
    return f"Human decision: {decision['decision']}."


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]

