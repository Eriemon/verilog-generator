"""Preflight requirement confirmation and code-generation planning helpers."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from .interface_templates import InterfaceTemplateError, resolve_interface_template, select_interface_template
from .refined_templates import summarize_refined_templates
from .use_case_templates import select_use_case_template, summarize_use_case_template

STREAMABILITY_VALUES = ("streamable", "non_streamable", "unknown")
INTERFACE_FAMILIES = ("native", "axi_stream", "axi4", "axi4_lite", "ahb", "apb", "custom")
AXI4_VARIANTS = ("axi4_full", "axi4_lite")
AXI4_ROLES = ("master", "slave")
AXI4_MODES = ("read", "write", "read_write")
INTERFACE_TEMPLATE_PROFILE_KEYS = ("template_id",)
AXI_STREAM_PROFILE_KEYS = ("keep_ready", "keep_last", "data_width", "clock_reset_domain", *INTERFACE_TEMPLATE_PROFILE_KEYS)
AXI4_PROFILE_KEYS = (
    "axi4_variant",
    "role",
    "read_write_mode",
    "data_width",
    "addr_width",
    "id_width",
    "burst_support",
    "max_burst_len",
    "clock_reset_domain",
    *INTERFACE_TEMPLATE_PROFILE_KEYS,
)
AHB_PROFILE_KEYS = ("role", "data_width", "addr_width", "clock_reset_domain", *INTERFACE_TEMPLATE_PROFILE_KEYS)
APB_PROFILE_KEYS = ("role", "data_width", "addr_width", "clock_reset_domain", *INTERFACE_TEMPLATE_PROFILE_KEYS)
NATIVE_FORBIDDEN_PROFILE_KEYS = frozenset((*AXI_STREAM_PROFILE_KEYS, *AXI4_PROFILE_KEYS, *AHB_PROFILE_KEYS, *APB_PROFILE_KEYS))
STREAM_KEYWORDS = (
    "stream",
    "packet",
    "frame",
    "sample",
    "line",
    "token",
    "vector",
    "sequence",
    "sliding window",
    "throughput",
    "ii",
    "pipeline",
    "valid",
    "ready",
    "last",
    "data",
    "keep",
    "user",
)


def detect_streamability(spec: dict[str, Any], evidence: dict[str, Any] | None = None) -> str:
    """Heuristically classify whether the task looks streamable."""

    explicit = spec.get("streamability")
    if explicit in STREAMABILITY_VALUES:
        return str(explicit)
    design_requirements = spec.get("design_requirements")
    if isinstance(design_requirements, dict):
        explicit = design_requirements.get("streamability")
        if explicit in STREAMABILITY_VALUES:
            return str(explicit)

    fragments: list[str] = []
    for key in ("description",):
        value = spec.get(key)
        if isinstance(value, str):
            fragments.append(value)
    for key in ("behavior", "constraints", "notes"):
        for item in spec.get(key, []) or []:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, dict) and item.get("text"):
                fragments.append(str(item["text"]))
    interfaces = spec.get("interfaces", {}) if isinstance(spec.get("interfaces"), dict) else {}
    for item in interfaces.get("ports", []) or []:
        if isinstance(item, dict):
            fragments.extend(str(value) for value in item.values())
    for item in interfaces.get("arguments", []) or []:
        if isinstance(item, dict):
            fragments.extend(str(value) for value in item.values())
    if evidence:
        for item in evidence.get("items", [])[:12]:
            if isinstance(item, dict) and item.get("text"):
                fragments.append(str(item["text"]))

    blob = " ".join(fragment.lower() for fragment in fragments)
    if "m_axi" in blob or "axis" in blob or "axi-stream" in blob:
        return "streamable"
    if "for each index below length" in blob or "ii=1" in blob or "pipeline" in blob:
        return "streamable"
    if any(keyword in blob for keyword in STREAM_KEYWORDS):
        return "streamable"
    return "non_streamable"


def detect_interface_family(spec: dict[str, Any], streamability: str | None = None, evidence: dict[str, Any] | None = None) -> str | None:
    """Choose the default bus family from confirmed text when none is supplied."""

    explicit = spec.get("interface_family")
    if explicit in INTERFACE_FAMILIES:
        return str(explicit)
    design_requirements = spec.get("design_requirements")
    if isinstance(design_requirements, dict) and design_requirements.get("interface_family") in INTERFACE_FAMILIES:
        return str(design_requirements["interface_family"])

    blob = _spec_text_blob(spec, evidence)
    if "apb" in blob:
        return "apb"
    if "ahb" in blob:
        return "ahb"
    if "axi4-lite" in blob or "axi lite" in blob or "axi4_lite" in blob or "axil" in blob:
        return "axi4_lite"
    if "axi4" in blob or "m_axi" in blob or "memory mapped" in blob or "burst" in blob or "dma" in blob:
        return "axi4"
    if "axi-stream" in blob or "axis" in blob or "tvalid" in blob or "tready" in blob or streamability == "streamable":
        return "axi_stream"
    if any(keyword in blob for keyword in ("register", "control", "status", "configuration", "csr")):
        return "axi4_lite"
    return None


def _spec_text_blob(spec: dict[str, Any], evidence: dict[str, Any] | None = None) -> str:
    fragments: list[str] = []
    for key in ("description",):
        value = spec.get(key)
        if isinstance(value, str):
            fragments.append(value)
    for key in ("behavior", "constraints", "notes"):
        for item in spec.get(key, []) or []:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, dict) and item.get("text"):
                fragments.append(str(item["text"]))
    interfaces = spec.get("interfaces", {}) if isinstance(spec.get("interfaces"), dict) else {}
    for item in interfaces.get("ports", []) or []:
        if isinstance(item, dict):
            fragments.extend(str(value) for value in item.values())
    if evidence:
        for item in evidence.get("items", [])[:12]:
            if isinstance(item, dict) and item.get("text"):
                fragments.append(str(item["text"]))
    return " ".join(fragment.lower() for fragment in fragments)


def apply_requirement_defaults(
    raw_spec: dict[str, Any],
    *,
    design_requirements: dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: dict[str, Any] | None = None,
    confirmation_notes: str | None = None,
    confirmed_by_user: bool | None = None,
) -> dict[str, Any]:
    """Merge call-site requirement inputs into a spec-shaped payload."""

    spec = copy.deepcopy(raw_spec)
    target = str(spec.get("target") or "")
    base = copy.deepcopy(spec.get("design_requirements", {})) if isinstance(spec.get("design_requirements"), dict) else {}
    if design_requirements:
        base.update(copy.deepcopy(design_requirements))

    resolved_streamability = (
        streamability
        or base.get("streamability")
        or spec.get("streamability")
        or detect_streamability(spec)
    )
    resolved_interface_family = (
        interface_family
        or base.get("interface_family")
        or spec.get("interface_family")
        or detect_interface_family(spec, str(resolved_streamability))
    )
    resolved_interface_profile = copy.deepcopy(spec.get("interface_profile", {})) if isinstance(spec.get("interface_profile"), dict) else {}
    if isinstance(base.get("interface_profile"), dict):
        resolved_interface_profile.update(copy.deepcopy(base["interface_profile"]))
    if interface_profile:
        resolved_interface_profile.update(copy.deepcopy(interface_profile))
    resolved_pipeline_required = (
        pipeline_required
        if pipeline_required is not None
        else base.get("pipeline_required", spec.get("pipeline_required", True))
    )
    resolved_confirmed = (
        bool(confirmed_by_user)
        if confirmed_by_user is not None
        else bool(base.get("confirmed_by_user", False))
    )
    resolved_notes = (
        confirmation_notes
        if confirmation_notes is not None
        else str(base.get("confirmation_notes", spec.get("confirmation_notes", "")) or "")
    )

    if resolved_interface_family in {"axi_stream", "axi4", "axi4_lite", "ahb", "apb"}:
        resolved_interface_profile = _apply_interface_defaults(
            resolved_interface_family,
            resolved_interface_profile,
        )

    spec["pipeline_required"] = bool(resolved_pipeline_required)
    spec["streamability"] = str(resolved_streamability)
    spec["interface_family"] = resolved_interface_family
    spec["interface_profile"] = resolved_interface_profile
    spec["codegen_plan_required"] = bool(spec.get("codegen_plan_required", True))
    spec["codegen_plan_path"] = spec.get("codegen_plan_path")
    spec["design_requirements"] = {
        "target": target,
        "pipeline_required": bool(resolved_pipeline_required),
        "streamability": str(resolved_streamability),
        "interface_family": resolved_interface_family,
        "interface_profile": resolved_interface_profile,
        "confirmed_by_user": resolved_confirmed,
        "confirmation_notes": resolved_notes,
    }
    return spec


def validate_requirement_confirmation(spec: dict[str, Any]) -> None:
    """Reject generation when the confirmed requirement contract is incomplete."""

    issues = _requirement_confirmation_issues(spec, require_confirmed=True)
    if issues:
        raise ValueError(issues[0])


def require_codegen_plan_enabled(spec: dict[str, Any]) -> None:
    if spec.get("codegen_plan_required") is not True:
        raise ValueError("This v1 staged flow requires codegen_plan_required=true.")


def validate_codegen_plan_payload(
    spec: dict[str, Any],
    payload: dict[str, Any],
    *,
    require_ready: bool,
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Explicit codegen_plan_path must point to a JSON object.")
    if payload.get("version") != 1:
        raise ValueError("Explicit codegen plan must use version=1.")
    if payload.get("name") != spec.get("name"):
        raise ValueError("Explicit codegen plan name must match spec.name.")
    if payload.get("target") != spec.get("target"):
        raise ValueError("Explicit codegen plan target must match spec.target.")
    for field in ("interface_decision", "pipeline_strategy", "verification_strategy"):
        if not isinstance(payload.get(field), dict):
            raise ValueError(f"Explicit codegen plan must include object field `{field}`.")
    if not isinstance(payload.get("open_questions", []), list):
        raise ValueError("Explicit codegen plan open_questions must be a list.")
    if not isinstance(payload.get("ready_for_generation"), bool):
        raise ValueError("Explicit codegen plan ready_for_generation must be a boolean.")
    if require_ready and (
        payload.get("ready_for_generation") is not True or payload.get("open_questions")
    ):
        blockers = payload.get("open_questions", []) or ["Confirm the remaining design requirements."]
        raise ValueError(
            "Explicit codegen plan is not ready for generation: "
            + "; ".join(str(item) for item in blockers)
        )


def build_requirements_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the structured requirements artifact written before code generation."""

    requirements = copy.deepcopy(spec.get("design_requirements", {})) if isinstance(spec.get("design_requirements"), dict) else {}
    template = _interface_template_summary(spec)
    use_case_template = _use_case_template_summary(spec)
    return {
        "version": 1,
        "name": spec.get("name"),
        "target": spec.get("target"),
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "interface_family": spec.get("interface_family"),
        "interface_profile": copy.deepcopy(spec.get("interface_profile", {})) if isinstance(spec.get("interface_profile"), dict) else {},
        "requirements_summary": _requirements_summary(spec),
        "design_requirements": requirements,
        "confirmed_by_user": requirements.get("confirmed_by_user") is True,
        "selected_interface_template_id": template["selected_template_id"],
        "interface_template": template,
        "selected_use_case_template_id": use_case_template["id"],
        "use_case_template": use_case_template,
        "selected_refined_template_ids": [item["template_id"] for item in summarize_refined_templates(spec)],
        "refined_templates": summarize_refined_templates(spec),
    }


def build_codegen_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """Produce a deterministic structured planning artifact before code generation."""

    requirements = build_requirements_payload(spec)
    open_questions = _codegen_open_questions(spec)
    ready = not open_questions
    template = requirements["interface_template"]
    use_case_template = requirements["use_case_template"]
    refined_templates = requirements["refined_templates"]
    plan = {
        "version": 1,
        "name": spec.get("name"),
        "target": spec.get("target"),
        "requirements_summary": requirements["requirements_summary"],
        "selected_use_case_template_id": use_case_template["id"],
        "use_case_template": use_case_template,
        "selected_refined_template_ids": [item["template_id"] for item in refined_templates],
        "refined_templates": refined_templates,
        "interface_decision": {
            "family": spec.get("interface_family"),
            "profile": copy.deepcopy(spec.get("interface_profile", {})) if isinstance(spec.get("interface_profile"), dict) else {},
            "confirmed": bool((spec.get("design_requirements") or {}).get("confirmed_by_user")),
            "selected_interface_template_id": template["selected_template_id"],
            "template_selection_reason": template["selection_reason"],
            "template_path": template["path"],
            "port_naming_policy": template["port_naming_policy"],
        },
        "pipeline_strategy": {
            "required": bool(spec.get("pipeline_required", True)),
            "strategy": "pipeline_required" if spec.get("pipeline_required", True) else "pipeline_optional",
            "notes": "Use a pipelined implementation unless the user explicitly disables it.",
        },
        "module_partition": {
            "top": spec.get("name"),
            "subfunctions": [item.get("name") for item in spec.get("subfunctions", []) if isinstance(item, dict)] or [spec.get("name")],
            "decomposition_strategy": "follow the normalized subfunction plan and keep interface boundaries explicit",
        },
        "signal_width_strategy": {
            "policy": "infer from the reference model range and preserve parameterized widths where practical",
            "rtl_style_profile": spec.get("rtl_style_profile"),
        },
        "reset_clock_strategy": {
            "clock": copy.deepcopy(spec.get("clock", {})) if isinstance(spec.get("clock"), dict) else {},
            "reset": copy.deepcopy(spec.get("reset", {})) if isinstance(spec.get("reset"), dict) else {},
        },
        "verification_strategy": {
            "python_reference_required": True,
            "self_checking_testbench_required": True,
            "readiness_target": "static",
        },
        "syntax_risk_checks": _syntax_risk_checks(spec),
        "open_questions": open_questions,
        "ready_for_generation": ready,
    }
    override = (spec.get("workflow") or {}).get("codegen_plan_override") if isinstance(spec.get("workflow"), dict) else None
    if isinstance(override, dict):
        plan.update(copy.deepcopy(override))
        if "open_questions" not in override:
            plan["open_questions"] = open_questions
        if "ready_for_generation" not in override:
            plan["ready_for_generation"] = not plan.get("open_questions")
    return plan


def _requirements_summary(spec: dict[str, Any]) -> dict[str, Any]:
    template = _interface_template_summary(spec)
    use_case_template = _use_case_template_summary(spec)
    return {
        "target": spec.get("target"),
        "rtl_dialect": spec.get("rtl_dialect"),
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "interface_family": spec.get("interface_family"),
        "selected_interface_template_id": template["selected_template_id"],
        "selected_use_case_template_id": use_case_template["id"],
        "selected_refined_template_ids": [item["template_id"] for item in summarize_refined_templates(spec)],
        "confirmation_notes": (spec.get("design_requirements") or {}).get("confirmation_notes", ""),
    }


def _interface_template_summary(spec: dict[str, Any]) -> dict[str, Any]:
    try:
        selected = select_interface_template(spec)
    except InterfaceTemplateError as exc:
        return {
            "selected_template_id": None,
            "selection_reason": str(exc),
            "path": None,
            "port_naming_policy": "strict_preferred",
        }
    if not selected:
        return {
            "selected_template_id": None,
            "selection_reason": "no standard local interface template is required for this interface family",
            "path": None,
            "port_naming_policy": "not_applicable",
        }
    return {
        "selected_template_id": selected["template_id"],
        "selection_reason": selected["selection_reason"],
        "path": str(selected["path"]),
        "port_naming_policy": selected["strict_naming_policy"],
    }


def _use_case_template_summary(spec: dict[str, Any]) -> dict[str, Any]:
    return summarize_use_case_template(select_use_case_template(spec))


def _apply_interface_defaults(interface_family: str, profile: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(profile)
    if interface_family == "axi_stream":
        payload.setdefault("clock_reset_domain", {"clock": "i_axis_aclk", "reset": "i_axis_arstn"})
    elif interface_family == "axi4":
        payload.setdefault("clock_reset_domain", {"clock": "i_axi_aclk", "reset": "i_axi_arstn"})
    elif interface_family == "axi4_lite":
        payload.setdefault("axi4_variant", "axi4_lite")
        payload.setdefault("burst_support", False)
        payload.setdefault("clock_reset_domain", {"clock": "i_axi_aclk", "reset": "i_axi_arstn"})
    elif interface_family == "ahb":
        payload.setdefault("clock_reset_domain", {"clock": "i_ahb_hclk", "reset": "i_ahb_hrstn"})
    elif interface_family == "apb":
        payload.setdefault("clock_reset_domain", {"clock": "i_apb_pclk", "reset": "i_apb_prstn"})
    return payload


def _validate_axi_stream_profile(profile: Any) -> None:
    if not isinstance(profile, dict):
        raise ValueError("AXI-Stream interface_profile must be an object.")
    required_bool_keys = ("keep_ready", "keep_last")
    for key in required_bool_keys:
        if not isinstance(profile.get(key), bool):
            raise ValueError(f"AXI-Stream interface_profile requires boolean `{key}`.")
    if not isinstance(profile.get("data_width"), int) or int(profile["data_width"]) <= 0:
        raise ValueError("AXI-Stream interface_profile requires a positive integer `data_width`.")


def _validate_axi4_profile(profile: Any) -> None:
    if not isinstance(profile, dict):
        raise ValueError("AXI4 interface_profile must be an object.")
    if profile.get("axi4_variant") not in AXI4_VARIANTS:
        raise ValueError(f"AXI4 interface_profile requires `axi4_variant` in {', '.join(AXI4_VARIANTS)}.")
    if profile.get("role") not in AXI4_ROLES:
        raise ValueError(f"AXI4 interface_profile requires `role` in {', '.join(AXI4_ROLES)}.")
    if profile.get("read_write_mode") not in AXI4_MODES:
        raise ValueError(f"AXI4 interface_profile requires `read_write_mode` in {', '.join(AXI4_MODES)}.")
    for key in ("data_width", "addr_width"):
        if not isinstance(profile.get(key), int) or int(profile[key]) <= 0:
            raise ValueError(f"AXI4 interface_profile requires a positive integer `{key}`.")
    if profile.get("axi4_variant") == "axi4_full":
        if not isinstance(profile.get("id_width"), int) or int(profile["id_width"]) <= 0:
            raise ValueError("AXI4 full interface_profile requires a positive integer `id_width`.")
    if not isinstance(profile.get("burst_support"), bool):
        raise ValueError("AXI4 interface_profile requires boolean `burst_support`.")
    if profile.get("burst_support") and (not isinstance(profile.get("max_burst_len"), int) or int(profile["max_burst_len"]) <= 0):
        raise ValueError("AXI4 interface_profile requires positive integer `max_burst_len` when burst_support=true.")


def _validate_axi4_lite_profile(profile: Any) -> None:
    if not isinstance(profile, dict):
        raise ValueError("AXI4-Lite interface_profile must be an object.")
    if profile.get("role") not in AXI4_ROLES:
        raise ValueError(f"AXI4-Lite interface_profile requires `role` in {', '.join(AXI4_ROLES)}.")
    if profile.get("read_write_mode") not in AXI4_MODES:
        raise ValueError(f"AXI4-Lite interface_profile requires `read_write_mode` in {', '.join(AXI4_MODES)}.")
    for key in ("data_width", "addr_width"):
        if not isinstance(profile.get(key), int) or int(profile[key]) <= 0:
            raise ValueError(f"AXI4-Lite interface_profile requires a positive integer `{key}`.")


def _validate_simple_bus_profile(profile: Any, family: str) -> None:
    if not isinstance(profile, dict):
        raise ValueError(f"{family.upper()} interface_profile must be an object.")
    if profile.get("role") not in AXI4_ROLES:
        raise ValueError(f"{family.upper()} interface_profile requires `role` in {', '.join(AXI4_ROLES)}.")
    for key in ("data_width", "addr_width"):
        if not isinstance(profile.get(key), int) or int(profile[key]) <= 0:
            raise ValueError(f"{family.upper()} interface_profile requires a positive integer `{key}`.")


def _requirement_confirmation_issues(
    spec: dict[str, Any],
    *,
    require_confirmed: bool,
) -> list[str]:
    requirements = spec.get("design_requirements")
    if not isinstance(requirements, dict):
        return ["Generation calls require a `design_requirements` object."] if require_confirmed else []

    issues: list[str] = []
    if requirements.get("target") != spec.get("target"):
        issues.append("design_requirements.target must match spec.target.")
    if require_confirmed and requirements.get("confirmed_by_user") is not True:
        issues.append("Generation calls require design_requirements.confirmed_by_user=true.")
    issues.extend(_requirement_contract_issues(spec, requirements, strict_profile_validation=require_confirmed))
    return issues


def _requirement_contract_issues(
    spec: dict[str, Any],
    requirements: dict[str, Any],
    *,
    strict_profile_validation: bool,
) -> list[str]:
    issues: list[str] = []
    if spec.get("codegen_plan_required") is not True:
        issues.append("This v1 staged flow requires codegen_plan_required=true.")
    if not isinstance(requirements.get("pipeline_required"), bool):
        issues.append("design_requirements.pipeline_required must be a boolean.")
    elif bool(requirements["pipeline_required"]) != bool(spec.get("pipeline_required", True)):
        issues.append("design_requirements.pipeline_required must match spec.pipeline_required.")

    streamability = requirements.get("streamability")
    if streamability not in STREAMABILITY_VALUES:
        issues.append(f"streamability must be one of {', '.join(STREAMABILITY_VALUES)}.")
    elif str(streamability) != str(spec.get("streamability")):
        issues.append("design_requirements.streamability must match spec.streamability.")

    interface_family = requirements.get("interface_family")
    if interface_family is not None and interface_family not in INTERFACE_FAMILIES:
        issues.append(f"interface_family must be one of {', '.join(INTERFACE_FAMILIES)}.")
    elif interface_family != spec.get("interface_family"):
        issues.append("design_requirements.interface_family must match spec.interface_family.")

    profile = requirements.get("interface_profile", {})
    if not isinstance(profile, dict):
        issues.append("design_requirements.interface_profile must be an object.")
        return issues
    if profile != spec.get("interface_profile", {}):
        issues.append("design_requirements.interface_profile must match spec.interface_profile.")

    if streamability == "streamable" and not interface_family:
        issues.append("Streamable tasks require an explicit interface_family confirmation before generation.")

    issues.extend(_interface_family_semantic_issues(interface_family, profile, strict_profile_validation))
    issues.extend(_clock_reset_domain_issues(spec))
    return issues


def _interface_family_semantic_issues(
    interface_family: Any,
    profile: dict[str, Any],
    strict_profile_validation: bool,
) -> list[str]:
    issues: list[str] = []
    if interface_family == "custom" and not profile:
        issues.append("Custom interfaces require a non-empty interface_profile.")
    if interface_family == "native":
        forbidden = sorted(key for key in profile if key in NATIVE_FORBIDDEN_PROFILE_KEYS)
        if forbidden:
            issues.append(
                "Native interfaces must not use AXI-specific interface_profile keys: "
                + ", ".join(forbidden)
                + "."
            )
    if strict_profile_validation and interface_family == "axi_stream":
        try:
            _validate_axi_stream_profile(profile)
        except ValueError as exc:
            issues.append(str(exc))
    if strict_profile_validation and interface_family == "axi4":
        try:
            _validate_axi4_profile(profile)
        except ValueError as exc:
            issues.append(str(exc))
    if strict_profile_validation and interface_family == "axi4_lite":
        try:
            _validate_axi4_lite_profile(profile)
        except ValueError as exc:
            issues.append(str(exc))
    if strict_profile_validation and interface_family in {"ahb", "apb"}:
        try:
            _validate_simple_bus_profile(profile, str(interface_family))
        except ValueError as exc:
            issues.append(str(exc))
    if strict_profile_validation and interface_family in {"axi_stream", "axi4", "axi4_lite", "ahb", "apb"}:
        try:
            resolve_interface_template(str(interface_family), profile)
        except InterfaceTemplateError as exc:
            issues.append(str(exc))
    return issues


def _clock_reset_domain_issues(spec: dict[str, Any]) -> list[str]:
    if spec.get("target") != "rtl":
        return []
    interface_profile = spec.get("interface_profile", {})
    if not isinstance(interface_profile, dict):
        return []
    domain = interface_profile.get("clock_reset_domain")
    if domain in (None, ""):
        return []
    if not isinstance(domain, dict):
        return ["interface_profile.clock_reset_domain must be an object when provided."]
    issues: list[str] = []
    ports = {
        str(item.get("name")): item
        for item in spec.get("interfaces", {}).get("ports", [])
        if isinstance(item, dict) and item.get("name")
    }
    for field in ("clock", "reset"):
        signal_name = str(domain.get(field) or "")
        if not signal_name:
            issues.append(f"interface_profile.clock_reset_domain.{field} must not be empty.")
            continue
        port = ports.get(signal_name)
        if not port:
            issues.append(f"interface_profile.clock_reset_domain.{field}={signal_name!r} must exist in interfaces.ports.")
            continue
        if str(port.get("direction") or "").lower() != "input":
            issues.append(f"interface_profile.clock_reset_domain.{field}={signal_name!r} must be an input port.")
        try:
            width = int(port.get("width", 1) or 1)
        except (TypeError, ValueError):
            width = -1
        if width != 1:
            issues.append(f"interface_profile.clock_reset_domain.{field}={signal_name!r} must have width 1.")
    declared_clock = str((spec.get("clock") or {}).get("name") or "")
    declared_reset = str((spec.get("reset") or {}).get("name") or "")
    if declared_clock and str(domain.get("clock") or "") and declared_clock != str(domain["clock"]):
        issues.append("interface_profile.clock_reset_domain.clock must match spec.clock.name.")
    if declared_reset and str(domain.get("reset") or "") and declared_reset != str(domain["reset"]):
        issues.append("interface_profile.clock_reset_domain.reset must match spec.reset.name.")
    return issues


def _codegen_open_questions(spec: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    requirements = spec.get("design_requirements", {}) if isinstance(spec.get("design_requirements"), dict) else {}
    if spec.get("target") == "rtl" and spec.get("rtl_dialect") != "verilog":
        questions.append("Confirm the design is intended for Verilog-2001.")
    if not requirements.get("confirmed_by_user"):
        questions.append("Confirm the target, pipeline requirement, and interface choice with the user.")
    if spec.get("streamability") == "streamable" and not spec.get("interface_family"):
        questions.append("Confirm whether the streamable task should use AXI-Stream, AXI4, AXI4-Lite, AHB, APB, native, or custom interfaces.")
    if spec.get("interface_family") == "axi_stream":
        profile = spec.get("interface_profile", {}) if isinstance(spec.get("interface_profile"), dict) else {}
        if "keep_ready" not in profile:
            questions.append("Confirm whether AXI-Stream ready handshake should be retained.")
        if "keep_last" not in profile:
            questions.append("Confirm whether AXI-Stream last should be retained.")
        if "data_width" not in profile:
            questions.append("Confirm the AXI-Stream data width.")
    if spec.get("interface_family") == "axi4":
        profile = spec.get("interface_profile", {}) if isinstance(spec.get("interface_profile"), dict) else {}
        required = ("axi4_variant", "role", "read_write_mode", "data_width", "addr_width", "burst_support")
        for key in required:
            if key not in profile:
                questions.append(f"Confirm the AXI4 configuration field `{key}`.")
        if profile.get("axi4_variant") == "axi4_full" and "id_width" not in profile:
            questions.append("Confirm the AXI4 full id width.")
        if profile.get("burst_support") is True and "max_burst_len" not in profile:
            questions.append("Confirm the AXI4 maximum burst length.")
    if spec.get("interface_family") == "axi4_lite":
        profile = spec.get("interface_profile", {}) if isinstance(spec.get("interface_profile"), dict) else {}
        for key in ("role", "read_write_mode", "data_width", "addr_width"):
            if key not in profile:
                questions.append(f"Confirm the AXI4-Lite configuration field `{key}`.")
    if spec.get("interface_family") in {"ahb", "apb"}:
        family = str(spec.get("interface_family")).upper()
        profile = spec.get("interface_profile", {}) if isinstance(spec.get("interface_profile"), dict) else {}
        for key in ("role", "data_width", "addr_width"):
            if key not in profile:
                questions.append(f"Confirm the {family} configuration field `{key}`.")
    for issue in _requirement_confirmation_issues(spec, require_confirmed=False):
        if issue not in questions:
            questions.append(issue)
    return questions


def _syntax_risk_checks(spec: dict[str, Any]) -> list[str]:
    checks = [
        "Prevent placeholder text, undefined symbols, and missing output artifacts before code generation.",
        "Keep the implementation aligned with the executable Python reference model and the staged verification flow.",
    ]
    use_case_template = select_use_case_template(spec)
    if spec.get("pipeline_required", True):
        checks.append("Reject non-pipelined implementations unless the user explicitly disables the pipeline requirement.")
    if spec.get("interface_family") == "axi_stream":
        checks.append("Do not silently add or remove AXI-Stream ready/last semantics; use the confirmed interface profile.")
    if spec.get("interface_family") == "axi4":
        checks.append("Preserve the confirmed AXI4 variant, role, widths, and burst policy across the generated interface.")
        checks.append("Preserve Erie-style bus port grouping for AXI4 channels instead of flattening the interface declaration.")
    if spec.get("interface_family") == "axi4_lite":
        checks.append("Preserve the confirmed AXI4-Lite role, read/write mode, and register-map widths across the generated interface.")
        checks.append("Preserve Erie-style bus port grouping for AXI4-Lite channels instead of flattening the interface declaration.")
    if spec.get("interface_family") in {"ahb", "apb"}:
        checks.append(f"Preserve the confirmed {str(spec.get('interface_family')).upper()} role, widths, and clock/reset domain across the generated interface.")
        checks.append(f"Preserve Erie-style bus port grouping for {str(spec.get('interface_family')).upper()} channels instead of flattening the interface declaration.")
    if spec.get("interface_family") == "axi_stream":
        checks.append("Preserve Erie-style bus port grouping for AXI-Stream channels instead of flattening the interface declaration.")
    if use_case_template:
        checks.append(
            "Preserve the selected ADC/DAC use-case template family `"
            + str(use_case_template.get("template_id"))
            + "` and keep its provenance, parameterization points, and board-level sideband intent visible in generated artifacts."
        )
    refined_templates = summarize_refined_templates(spec)
    if refined_templates:
        checks.append(
            "Preserve the selected refined Verilog pattern hints: "
            + ", ".join(item["template_id"] for item in refined_templates)
            + "."
        )
    if str(spec.get("rtl_style_profile") or "").lower() == "erie_strict":
        checks.append("Preserve Erie strict RTL style rules, including single-reg always blocks, strict naming, and region order.")
        checks.append("Preserve the Erie bilingual header with version, revision date, and revision history blocks.")
        checks.append("When an FSM is present, use `state_current`, `state_next`, and `ST_*` naming consistently.")
        checks.append("Preserve Erie module instance naming with `_Inst` suffixes and `gen_*` generate labels.")
        checks.append("Keep AXI/AXIS/APB/AHB ports grouped by channel and role instead of flattening the bus declaration list.")
    return checks

