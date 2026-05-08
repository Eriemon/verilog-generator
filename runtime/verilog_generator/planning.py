"""Spec decomposition helpers for Verilog workflows."""

from __future__ import annotations

import copy
from typing import Any

from .evidence import evidence_refs_for_text
from .spec import normalize_info_items, normalize_spec, normalize_subfunction


def decompose_spec(
    raw: dict[str, Any],
    target: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = normalize_spec(raw, target=target)
    plan = copy.deepcopy(spec)
    if not plan.get("subfunctions"):
        inputs, outputs = _rtl_io(plan)
        plan["subfunctions"] = [
            {
                "name": plan["name"],
                "inputs": inputs,
                "outputs": outputs,
                "behavior": normalize_info_items(plan.get("behavior"), "behavior"),
                "constraints": normalize_info_items(plan.get("constraints"), "constraints"),
                "dependencies": [],
                "source_references": [],
                "test_intent": normalize_info_items(_test_intent(plan), "test_intent"),
            }
        ]
    else:
        plan["subfunctions"] = [
            normalize_subfunction(item, index)
            for index, item in enumerate(plan["subfunctions"])
        ]
    if evidence:
        _attach_evidence(plan, evidence)
    return plan


def _rtl_io(spec: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    ports = spec.get("interfaces", {}).get("ports", [])
    inputs = [item for item in ports if isinstance(item, dict) and item.get("direction") == "input"]
    outputs = [item for item in ports if isinstance(item, dict) and item.get("direction") == "output"]
    return inputs, outputs


def _test_intent(spec: dict[str, Any]) -> list[str]:
    intents = [
        "Reset behavior drives outputs to known values.",
        "Nominal transactions match the Python reference model.",
    ]
    for item in spec.get("behavior", []) or []:
        text = item.get("text") if isinstance(item, dict) else str(item)
        intents.append(f"Verify behavior: {text}")
    return intents


def _attach_evidence(spec: dict[str, Any], evidence: dict[str, Any]) -> None:
    for subfunction in spec.get("subfunctions", []):
        for field in ("behavior", "constraints", "test_intent"):
            for item in subfunction.get(field, []):
                if isinstance(item, dict):
                    refs = evidence_refs_for_text(evidence, str(item.get("text", "")))
                    if refs:
                        item["evidence"] = refs
