"""Structured Verilog generation spec handling."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

TARGETS = ("rtl",)
RTL_DIALECTS = ("verilog",)
SPEC_FIELDS = (
    "name",
    "target",
    "rtl_dialect",
    "rtl_style_profile",
    "design_requirements",
    "streamability",
    "interface_family",
    "interface_profile",
    "pipeline_required",
    "codegen_plan_required",
    "codegen_plan_path",
    "description",
    "interfaces",
    "behavior",
    "clock",
    "reset",
    "constraints",
    "outputs",
    "notes",
    "semantic_checkpoints",
    "subfunctions",
    "workflow",
    "performance",
)
SUBFUNCTION_FIELDS = (
    "name",
    "inputs",
    "outputs",
    "behavior",
    "constraints",
    "dependencies",
    "source_references",
    "test_intent",
    "semantic_checkpoints",
)
INFO_DICTIONARY_FIELDS = ("behavior", "constraints", "test_intent")


class SpecError(ValueError):
    """Raised when a generation spec is invalid."""


def sanitize_name(name: str) -> str:
    cleaned = re.sub(r"\W+", "_", name.strip())
    cleaned = cleaned.strip("_")
    if not cleaned:
        return "generated_design"
    if cleaned[0].isdigit():
        cleaned = f"design_{cleaned}"
    return cleaned


def _rtl_defaults(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "target": "rtl",
        "rtl_dialect": "verilog",
        "rtl_style_profile": None,
        "design_requirements": {},
        "streamability": "unknown",
        "interface_family": None,
        "interface_profile": {},
        "pipeline_required": True,
        "codegen_plan_required": True,
        "codegen_plan_path": None,
        "description": "Implement a synthesizable Verilog-2001 RTL module.",
        "interfaces": {
            "ports": [
                {"name": "clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "rst_n", "direction": "input", "width": 1, "role": "reset"},
                {"name": "in_valid", "direction": "input", "width": 1},
                {"name": "in_data", "direction": "input", "width": 8},
                {"name": "out_valid", "direction": "output", "width": 1},
                {"name": "out_data", "direction": "output", "width": 8},
            ]
        },
        "behavior": [
            "Describe cycle-by-cycle behavior, latency, handshakes, and corner cases here."
        ],
        "clock": {"name": "clk", "edge": "posedge", "frequency_mhz": 100},
        "reset": {"name": "rst_n", "active": "low", "synchronous": True},
        "constraints": [
            "Use synthesizable Verilog-2001.",
            "Use synchronous sequential logic and explicit reset behavior.",
            "Avoid delays, system tasks, force/release, dynamic constructs, and multiple drivers.",
        ],
        "outputs": [
            {"path": f"rtl/{name}.v", "kind": "source", "language": "verilog"},
            {"path": f"tb/{name}_tb.v", "kind": "testbench", "language": "verilog"},
        ],
        "notes": [],
        "semantic_checkpoints": [],
        "subfunctions": [],
        "workflow": {},
        "performance": {},
    }


def scaffold_spec(target: str = "rtl", name: str | None = None) -> dict[str, Any]:
    _require_target(target)
    spec_name = sanitize_name(name or "rtl_module")
    return _rtl_defaults(spec_name)


def normalize_spec(raw: dict[str, Any], target: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SpecError("Spec must be a JSON object.")

    requested_target = _require_target(str(target or raw.get("target") or "rtl"))
    raw_target = raw.get("target")
    if raw_target and str(raw_target).lower() != requested_target:
        raise SpecError("Spec target must be 'rtl'.")

    name = sanitize_name(str(raw.get("name") or scaffold_spec("rtl")["name"]))
    spec = scaffold_spec("rtl", name=name)
    for key, value in raw.items():
        if key in SPEC_FIELDS:
            spec[key] = copy.deepcopy(value)

    spec["name"] = sanitize_name(str(spec["name"]))
    spec["target"] = "rtl"
    spec["rtl_dialect"] = _normalize_rtl_dialect(spec.get("rtl_dialect"))
    spec["rtl_style_profile"] = _normalize_rtl_style_profile(spec.get("rtl_style_profile"))
    _apply_rtl_output_defaults(
        spec,
        outputs_explicit="outputs" in raw,
        description_explicit="description" in raw,
        constraints_explicit="constraints" in raw,
    )
    spec["design_requirements"] = _normalize_design_requirements(spec.get("design_requirements"))
    spec["streamability"] = _normalize_streamability(spec.get("streamability"))
    spec["interface_family"] = _normalize_interface_family(spec.get("interface_family"))
    spec["interface_profile"] = _normalize_interface_profile(spec.get("interface_profile"))
    spec["pipeline_required"] = _normalize_pipeline_required(spec.get("pipeline_required"))
    spec["codegen_plan_required"] = _normalize_codegen_plan_required(spec.get("codegen_plan_required"))
    spec["codegen_plan_path"] = _normalize_codegen_plan_path(spec.get("codegen_plan_path"))
    spec["semantic_checkpoints"] = normalize_checkpoint_items(spec.get("semantic_checkpoints"))
    spec["subfunctions"] = [
        normalize_subfunction(item, index)
        for index, item in enumerate(spec.get("subfunctions", []))
    ]
    _validate_shape(spec)
    return spec


def normalize_subfunction(subfunction: dict[str, Any], index: int = 0) -> dict[str, Any]:
    if not isinstance(subfunction, dict):
        raise SpecError("Each subfunction must be a JSON object.")
    name = sanitize_name(str(subfunction.get("name") or f"subfunction_{index + 1}"))
    normalized = {
        "name": name,
        "inputs": _as_list(subfunction.get("inputs")),
        "outputs": _as_list(subfunction.get("outputs")),
        "behavior": normalize_info_items(subfunction.get("behavior"), "behavior"),
        "constraints": normalize_info_items(subfunction.get("constraints"), "constraints"),
        "dependencies": [sanitize_name(str(item)) for item in _as_list(subfunction.get("dependencies"))],
        "source_references": _as_list(subfunction.get("source_references")),
        "test_intent": normalize_info_items(subfunction.get("test_intent"), "test_intent"),
        "semantic_checkpoints": normalize_checkpoint_items(subfunction.get("semantic_checkpoints")),
    }
    return normalized


def normalize_checkpoint_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(value), start=1):
        if isinstance(item, dict):
            payload = copy.deepcopy(item)
            payload.setdefault("id", f"checkpoint_{index}")
            payload.setdefault("category", "behavior")
            payload.setdefault("signals", [])
            payload.setdefault("verification_hint", "")
            payload.setdefault("text", str(payload.get("text") or payload.get("description") or payload["id"]))
            items.append(payload)
        else:
            items.append(
                {
                    "id": f"checkpoint_{index}",
                    "category": "behavior",
                    "signals": [],
                    "verification_hint": "",
                    "text": str(item),
                }
            )
    return items


def normalize_info_items(value: Any, field: str) -> list[dict[str, Any]]:
    return [_normalize_info_item(item, field, index) for index, item in enumerate(_as_list(value))]


def _normalize_info_item(item: Any, field: str, index: int) -> dict[str, Any]:
    if isinstance(item, dict):
        text = str(item.get("text") or item.get("description") or "")
        payload = copy.deepcopy(item)
        payload["text"] = text
        payload.setdefault("id", f"{field}_{index + 1}")
        payload.setdefault("evidence", [])
        payload.setdefault("verification_cases", [])
        return payload
    return {
        "id": f"{field}_{index + 1}",
        "text": str(item),
        "evidence": [],
        "verification_cases": [],
    }


def read_spec(path: Path, target: str | None = None) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"Invalid JSON spec {path}: {exc}") from exc
    return normalize_spec(raw, target=target)


def write_spec(path: Path, spec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _require_target(target: str) -> str:
    normalized = target.lower()
    if normalized != "rtl":
        raise SpecError("Only target 'rtl' is supported.")
    return normalized


def _normalize_rtl_dialect(value: Any) -> str:
    if value in (None, "", "verilog"):
        return "verilog"
    raise SpecError("Only Verilog-2001 is supported.")


def _normalize_rtl_style_profile(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).lower()
    if normalized != "erie_strict":
        raise SpecError("Unknown rtl_style_profile. Supported value: erie_strict.")
    return normalized


def _normalize_design_requirements(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _normalize_streamability(value: Any) -> str:
    normalized = str(value or "unknown")
    if normalized not in {"streamable", "non_streamable", "unknown"}:
        raise SpecError("streamability must be streamable, non_streamable, or unknown.")
    return normalized


def _normalize_interface_family(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value)
    if normalized not in {"native", "axi_stream", "axi4", "axi4_lite", "ahb", "apb", "custom"}:
        raise SpecError("interface_family must be native, axi_stream, axi4, axi4_lite, ahb, apb, or custom.")
    return normalized


def _normalize_interface_profile(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _normalize_pipeline_required(value: Any) -> bool:
    return True if value is None else bool(value)


def _normalize_codegen_plan_required(value: Any) -> bool:
    return True if value is None else bool(value)


def _normalize_codegen_plan_path(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _apply_rtl_output_defaults(
    spec: dict[str, Any],
    *,
    outputs_explicit: bool,
    description_explicit: bool,
    constraints_explicit: bool,
) -> None:
    name = spec["name"]
    if not outputs_explicit:
        spec["outputs"] = [
            {"path": f"rtl/{name}.v", "kind": "source", "language": "verilog"},
            {"path": f"tb/{name}_tb.v", "kind": "testbench", "language": "verilog"},
        ]
    if not description_explicit:
        spec["description"] = "Implement a synthesizable Verilog-2001 RTL module."
    if not constraints_explicit:
        spec["constraints"] = _rtl_defaults(name)["constraints"]


def _validate_shape(spec: dict[str, Any]) -> None:
    if spec["target"] != "rtl":
        raise SpecError("Spec target must be 'rtl'.")
    if spec["rtl_dialect"] != "verilog":
        raise SpecError("Only Verilog-2001 is supported.")
    if not isinstance(spec.get("interfaces"), dict):
        raise SpecError("Spec interfaces must be an object.")
    ports = spec["interfaces"].get("ports")
    if not isinstance(ports, list) or not ports:
        raise SpecError("Spec interfaces.ports must be a non-empty list.")
    for port in ports:
        _validate_port(port)
    if not isinstance(spec.get("outputs"), list) or not spec["outputs"]:
        raise SpecError("Spec outputs must be a non-empty list.")
    for output in spec["outputs"]:
        _validate_output(output)
    for key in ("behavior", "constraints", "notes", "subfunctions"):
        if not isinstance(spec.get(key), list):
            raise SpecError(f"Spec {key} must be a list.")
    if not isinstance(spec.get("workflow"), dict):
        raise SpecError("Spec workflow must be an object.")
    _validate_workflow(spec.get("workflow", {}))
    if not isinstance(spec.get("performance"), dict):
        raise SpecError("Spec performance must be an object.")


def _validate_port(port: Any) -> None:
    if not isinstance(port, dict):
        raise SpecError("Each port must be an object.")
    if not port.get("name"):
        raise SpecError("Each port requires a name.")
    if port.get("direction") not in {"input", "output", "inout"}:
        raise SpecError("Port direction must be input, output, or inout.")
    try:
        width = int(port.get("width", 1))
    except (TypeError, ValueError):
        raise SpecError("Port width must be a positive integer.") from None
    if width <= 0:
        raise SpecError("Port width must be a positive integer.")


def _validate_output(output: Any) -> None:
    if not isinstance(output, dict):
        raise SpecError("Each output must be an object.")
    path = str(output.get("path") or "")
    if not path:
        raise SpecError("Each output requires a path.")
    if "\\" in path or path.startswith("/") or ".." in path.split("/"):
        raise SpecError(f"Output path must be safe and relative: {path!r}")
    if not path.lower().endswith(".v"):
        raise SpecError("Verilog-only outputs must use .v paths.")
    if output.get("language") and str(output["language"]).lower() != "verilog":
        raise SpecError("Output language must be verilog.")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _validate_workflow(workflow: dict[str, Any]) -> None:
    use_case_template_id = workflow.get("use_case_template_id")
    if use_case_template_id in (None, ""):
        return
    if not isinstance(use_case_template_id, str):
        raise SpecError("workflow.use_case_template_id must be a string when provided.")
    from .use_case_templates import UseCaseTemplateError, validate_use_case_template_id

    try:
        validate_use_case_template_id(use_case_template_id)
    except UseCaseTemplateError as exc:
        raise SpecError(str(exc)) from exc
