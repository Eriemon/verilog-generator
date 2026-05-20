"""Load and select refined local Verilog design templates."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .config import skill_root

TEMPLATE_ROOT = skill_root() / "assets" / "refined_verilog_templates"
CATALOG_PATH = TEMPLATE_ROOT / "catalog.json"


def load_refined_template_catalog() -> dict[str, Any]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Refined template catalog must be a JSON object: {CATALOG_PATH}")
    templates = payload.get("templates")
    if not isinstance(templates, list) or not templates:
        raise ValueError("Refined template catalog must contain a non-empty templates list.")
    return payload


def list_refined_templates() -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in load_refined_template_catalog()["templates"]]


def select_refined_templates(spec: dict[str, Any]) -> list[dict[str, Any]]:
    selected_ids = _match_template_ids(spec)
    results: list[dict[str, Any]] = []
    for template in load_refined_template_catalog()["templates"]:
        template_id = str(template.get("template_id") or "")
        if template_id not in selected_ids:
            continue
        rel_path = str(template.get("path") or "")
        path = TEMPLATE_ROOT / rel_path
        payload = copy.deepcopy(template)
        payload["path"] = path
        payload["content"] = path.read_text(encoding="utf-8")
        results.append(payload)
    return results


def summarize_refined_templates(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "template_id": item["template_id"],
            "title": item.get("title"),
            "path": str(item["path"]),
            "selection_reason": item.get("selection_reason"),
        }
        for item in select_refined_templates(spec)
    ]


def _match_template_ids(spec: dict[str, Any]) -> list[str]:
    interface_family = str(
        spec.get("interface_family")
        or (spec.get("design_requirements") or {}).get("interface_family")
        or ""
    ).lower()
    text = _spec_text(spec)
    selected: list[str] = []

    if interface_family == "axi4_lite" or any(token in text for token in ("csr", "register bank", "control register", "status register")):
        selected.append("axi4_lite_csr_shell")
    if interface_family == "axi_stream" or any(token in text for token in ("ready valid", "ready/valid", "ready-valid", "tvalid", "tready")):
        selected.append("axis_ready_valid_slice")
    if interface_family == "axi4" or any(token in text for token in ("axi interconnect", "crossbar", "dma", "memory mapped", "burst transfer", "m_axi_")):
        selected.append("axi_interconnect_port_groups")
    if any(token in text for token in ("conv1d", "convolution", "ifm", "ofm", "line buffer", "sliding window", "weight buffer")):
        selected.append("conv_load_store_pipeline")
    return selected


def _spec_text(spec: dict[str, Any]) -> str:
    fragments: list[str] = []
    for key in ("name", "description"):
        value = spec.get(key)
        if isinstance(value, str):
            fragments.append(value)
    for key in ("behavior", "constraints", "notes"):
        for item in spec.get(key, []) or []:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, dict):
                fragments.extend(str(value) for value in item.values())
    interfaces = spec.get("interfaces", {}) if isinstance(spec.get("interfaces"), dict) else {}
    for port in interfaces.get("ports", []) or []:
        if isinstance(port, dict):
            fragments.extend(str(value) for value in port.values())
    return " ".join(fragment.lower() for fragment in fragments)
