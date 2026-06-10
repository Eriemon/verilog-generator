"""Distilled RTL Markdown constraint catalog helpers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ASSET_PATH = Path(__file__).resolve().parents[2] / "assets" / "rtl_md_constraints.json"


@lru_cache(maxsize=1)
def load_rtl_md_constraints() -> dict[str, Any]:
    """Load the stable distilled Verilog RTL constraint catalog."""
    payload = json.loads(ASSET_PATH.read_text(encoding="utf-8"))
    _validate_catalog(payload)
    return payload


def summarize_constraints_for_prompt(*, max_rules_per_group: int = 5) -> str:
    """Return compact guidance suitable for prompt injection."""
    del max_rules_per_group
    catalog = load_rtl_md_constraints()
    rules = list(catalog["rules"])
    topic_order = sorted({str(rule["topic"]) for rule in rules})
    lines = [
        "RTL Markdown constraints:",
        "MUST rules are blocking error constraints. High-confidence MUST rules are also checked by static lint.",
        "REC rules are default warning-level preferences. Record any REC deviation in manifest checks with a concrete reason.",
        "Manifest checks must record implementation_assessment or reviewability_assessment evidence for every relevant REC deviation.",
        f"Coverage: {catalog['total_rules']} rules, {catalog['required_rules']} MUST/error rules, {catalog['advisory_rules']} REC/warning rules.",
    ]
    for topic in topic_order:
        topic_rules = [rule for rule in rules if rule["topic"] == topic]
        rendered = ", ".join(f"{rule['id']}({rule['severity']}/{rule['enforcement']})" for rule in topic_rules)
        lines.append(f"- {topic}: {rendered}")
    return "\n".join(lines)


def automated_constraint_ids() -> set[str]:
    """Return rule ids enforced by automated static checks."""
    catalog = load_rtl_md_constraints()
    return {
        str(rule["id"])
        for rule in catalog["rules"]
        if str(rule.get("enforcement", "")).startswith("automated_")
    }


def _validate_catalog(payload: dict[str, Any]) -> None:
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("RTL constraint catalog must contain a non-empty rules array.")
    if payload.get("total_rules") != len(rules):
        raise ValueError("RTL constraint catalog total_rules does not match rules length.")
    required = sum(1 for rule in rules if rule.get("severity") == "error")
    advisory = sum(1 for rule in rules if rule.get("severity") == "warning")
    if payload.get("required_rules") != required or payload.get("advisory_rules") != advisory:
        raise ValueError("RTL constraint catalog severity counts are inconsistent.")
    ids = [str(rule.get("id") or "") for rule in rules]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ValueError("RTL constraint catalog rule ids must be non-empty and unique.")
    if payload.get("shuffle_seed") != 20260609:
        raise ValueError("RTL constraint catalog must preserve the shuffled package seed.")
    if payload.get("semantic_rule_names") is not True:
        raise ValueError("RTL constraint catalog must use semantic rule names.")
    allowed_enforcement = {"automated_error", "automated_warning", "prompt_warning", "review_error"}
    enforcement_counts: dict[str, int] = {}
    for rule in rules:
        for banned_field in ("section", "小节", "章节"):
            if banned_field in rule:
                raise ValueError(f"RTL constraint rule {rule.get('id')} still contains old numbering metadata.")
        summary = str(rule.get("summary") or "")
        if len(summary) < 8:
            raise ValueError(f"RTL constraint rule {rule.get('id')} has an incomplete summary.")
        enforcement = str(rule.get("enforcement") or "")
        if enforcement not in allowed_enforcement:
            raise ValueError(f"RTL constraint rule {rule.get('id')} has unknown enforcement {enforcement!r}.")
        if rule.get("severity") == "error" and enforcement == "prompt_warning":
            raise ValueError(f"RTL constraint rule {rule.get('id')} downgrades an error rule to prompt warning.")
        enforcement_counts[enforcement] = enforcement_counts.get(enforcement, 0) + 1
    if payload.get("enforcement_counts") != enforcement_counts:
        raise ValueError("RTL constraint catalog enforcement_counts are inconsistent.")
