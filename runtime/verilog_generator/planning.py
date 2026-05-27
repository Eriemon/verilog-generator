"""Spec decomposition helpers for Verilog workflows."""

from __future__ import annotations

import copy
from typing import Any

from .evidence import evidence_refs_for_text
from .spec import normalize_checkpoint_items, normalize_info_items, normalize_spec, normalize_subfunction


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
                "semantic_checkpoints": _semantic_checkpoints(plan, outputs, explicit=plan.get("semantic_checkpoints")),
            }
        ]
    else:
        normalized_subfunctions = []
        for index, item in enumerate(plan["subfunctions"]):
            normalized = normalize_subfunction(item, index)
            normalized["semantic_checkpoints"] = _semantic_checkpoints(
                {"name": normalized["name"], "behavior": normalized.get("behavior", []), "interfaces": {"ports": normalized.get("outputs", [])}},
                normalized.get("outputs", []),
                explicit=normalized.get("semantic_checkpoints"),
            )
            normalized_subfunctions.append(normalized)
        plan["subfunctions"] = normalized_subfunctions
    plan["semantic_checkpoints"] = _semantic_checkpoints(plan, _rtl_io(plan)[1], explicit=plan.get("semantic_checkpoints"))
    plan["subfunction_dependency_graph"] = _dependency_graph(plan)
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


def _semantic_checkpoints(spec: dict[str, Any], outputs: list[Any], *, explicit: Any = None) -> list[dict[str, Any]]:
    if explicit:
        return normalize_checkpoint_items(explicit)
    checkpoints: list[dict[str, Any]] = [
        {
            "id": "reset_known_state",
            "category": "reset",
            "signals": [str((spec.get("reset") or {}).get("name") or "rst_n")],
            "verification_hint": "Check reset-driven initialization before nominal traffic.",
            "text": "Outputs and sequential state settle to known values after reset.",
        }
    ]
    for index, item in enumerate(spec.get("behavior", []) or [], start=1):
        text = item.get("text") if isinstance(item, dict) else str(item)
        checkpoints.append(
            {
                "id": f"behavior_checkpoint_{index}",
                "category": "behavior",
                "signals": [item["name"] for item in outputs[:2] if isinstance(item, dict) and item.get("name")],
                "verification_hint": "Emit this checkpoint from collect_checkpoints(case) and mirror it in the RTL transcript.",
                "text": text,
            }
        )
    for output in outputs[:4]:
        if isinstance(output, dict) and output.get("name"):
            checkpoints.append(
                {
                    "id": f"observe_{output['name']}",
                    "category": "observable_output",
                    "signals": [output["name"]],
                    "verification_hint": f"Observe `{output['name']}` in both reference checkpoints and transcript output payloads.",
                    "text": f"Observe public output `{output['name']}` for expected behavior changes.",
                }
            )
    return checkpoints


def _dependency_graph(spec: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for subfunction in spec.get("subfunctions", []):
        if not isinstance(subfunction, dict) or not subfunction.get("name"):
            continue
        nodes.append(
            {
                "id": str(subfunction["name"]),
                "name": str(subfunction["name"]),
                "outputs": [item.get("name") if isinstance(item, dict) else str(item) for item in subfunction.get("outputs", []) or []],
                "semantic_checkpoints": [item.get("id") for item in subfunction.get("semantic_checkpoints", []) if isinstance(item, dict)],
            }
        )
        for dependency in subfunction.get("dependencies", []) or []:
            edges.append({"from": str(dependency), "to": str(subfunction["name"]), "kind": "subfunction_dependency"})
    return {"nodes": nodes, "edges": edges}


def _attach_evidence(spec: dict[str, Any], evidence: dict[str, Any]) -> None:
    for subfunction in spec.get("subfunctions", []):
        for field in ("behavior", "constraints", "test_intent"):
            for item in subfunction.get(field, []):
                if isinstance(item, dict):
                    refs = evidence_refs_for_text(evidence, str(item.get("text", "")))
                    if refs:
                        item["evidence"] = refs
