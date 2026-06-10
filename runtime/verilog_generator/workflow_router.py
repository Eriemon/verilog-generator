"""Read-only entry routing for Verilog workflow requests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROUTE_ENTRY_MODES = (
    "spec-first generation",
    "plan-seeded generation",
    "existing-RTL assist/repair",
    "evidence-first debug/repair",
)

DIAGNOSIS_ROUTES = (
    "local_rtl_issue",
    "spec_ambiguity",
    "dut_tb_contract_drift",
    "toolchain_issue",
    "needs_external_validation",
    "unknown_or_mixed",
)


def route_verilog_entry(
    *,
    request_summary: str = "",
    spec: str | Path | dict[str, Any] | None = None,
    codegen_plan: str | Path | dict[str, Any] | None = None,
    rtl: str | Path | list[str | Path] | None = None,
    testbench: str | Path | list[str | Path] | None = None,
    logs: str | Path | list[str | Path] | None = None,
    waveform: str | Path | list[str | Path] | None = None,
    validation: str | Path | dict[str, Any] | None = None,
    artifact_dir: str | Path | None = None,
    remote_validation_requested: bool = False,
) -> dict[str, Any]:
    """Return a routing decision without mutating source or run artifacts."""

    summary = str(request_summary or "")
    spec_requested = spec is not None or _artifact_exists(artifact_dir, ("spec.json", "_adapter_inputs/spec.json"))
    spec_present = _value_present(spec) or _artifact_exists(artifact_dir, ("spec.json", "_adapter_inputs/spec.json"))
    plan_artifact_present = _artifact_exists(artifact_dir, ("codegen_plan.json", "_adapter_inputs/codegen_plan.json"))
    plan_payload = _load_mapping(codegen_plan) or _load_artifact_mapping(artifact_dir, ("codegen_plan.json", "_adapter_inputs/codegen_plan.json"))
    plan_requested = codegen_plan is not None or plan_artifact_present
    plan_present = _value_present(codegen_plan) or plan_artifact_present
    plan_ready = bool(plan_payload.get("ready_for_generation")) and not plan_payload.get("open_questions")
    rtl_paths = _path_list(rtl)
    tb_paths = _path_list(testbench)
    log_paths = _path_list(logs)
    wave_paths = _path_list(waveform)
    existing_rtl_paths = _existing_paths(rtl_paths)
    existing_tb_paths = _existing_paths(tb_paths)
    existing_log_paths = _existing_paths(log_paths)
    existing_wave_paths = _existing_paths(wave_paths)
    validation_payload = _load_mapping(validation)
    validation_present = _value_present(validation) or bool(validation_payload)
    missing_artifacts = _missing_artifact_paths(
        spec=spec,
        codegen_plan=codegen_plan,
        rtl_paths=rtl_paths,
        tb_paths=tb_paths,
        log_paths=log_paths,
        wave_paths=wave_paths,
        validation=validation,
    )
    risk_flags = _risk_flags(
        summary=summary,
        logs=existing_log_paths,
        validation=validation_payload,
        remote_validation_requested=remote_validation_requested,
        missing_artifacts=missing_artifacts,
    )

    if remote_validation_requested or log_paths or wave_paths or validation is not None:
        entry_mode = "evidence-first debug/repair"
        recommended_flow = "verify_existing_verilog"
        safe_recovery_hint = "inspect_diagnostics_before_mutation"
    elif rtl_paths or tb_paths:
        entry_mode = "existing-RTL assist/repair"
        recommended_flow = "verify_existing_verilog" if spec_requested else "analyze_existing_verilog"
        safe_recovery_hint = "preserve_sources_and_choose_explicit_automation_mode"
    elif spec_requested and plan_requested and plan_ready:
        entry_mode = "plan-seeded generation"
        recommended_flow = "run_verilog_workflow"
        safe_recovery_hint = "resume_requirements_if_plan_drift_is_detected"
    else:
        entry_mode = "spec-first generation"
        recommended_flow = "run_verilog_workflow"
        safe_recovery_hint = "complete_requirements_before_generation"

    required_inputs = _required_inputs(entry_mode, remote_validation_requested=remote_validation_requested)
    present_inputs = _present_inputs(
        spec_present=spec_present,
        plan_present=plan_present,
        rtl_paths=existing_rtl_paths,
        tb_paths=existing_tb_paths,
        log_paths=existing_log_paths,
        wave_paths=existing_wave_paths,
        validation_present=validation_present,
        remote_validation_requested=remote_validation_requested,
    )
    missing_inputs = [item for item in required_inputs if item not in present_inputs]

    if entry_mode == "spec-first generation" and not plan_present:
        missing_inputs.append("codegen_plan")
    if entry_mode == "evidence-first debug/repair" and remote_validation_requested:
        for item in ("remote_selection", "remote_workspace_settings"):
            if item not in missing_inputs and item not in present_inputs:
                missing_inputs.append(item)

    return {
        "version": 1,
        "recommended_flow": recommended_flow,
        "entry_mode": entry_mode,
        "required_inputs": required_inputs,
        "missing_inputs": _dedupe(missing_inputs),
        "next_action": _next_action(entry_mode, remote_validation_requested=remote_validation_requested, plan_ready=plan_ready),
        "safe_recovery_hint": safe_recovery_hint,
        "risk_flags": _dedupe(risk_flags),
        "provenance_policy": {
            "reference_material": "abstract_principles_only",
            "copy_policy": "no_reference_text_code_templates_or_schemas",
            "runtime_dependency": "none",
        },
    }


def classify_diagnosis_route(
    *,
    diagnosis: dict[str, Any] | None = None,
    validation_report: Any | None = None,
    tb_contract: dict[str, Any] | None = None,
) -> str:
    """Classify verify-repair evidence into a stable routing summary."""

    diagnosis = diagnosis or {}
    outcome = str(diagnosis.get("outcome") or "")
    report = validation_report.to_dict() if hasattr(validation_report, "to_dict") else validation_report
    report = report if isinstance(report, dict) else {}
    issues = report.get("issues", []) if isinstance(report.get("issues", []), list) else []
    issue_sources = {str(item.get("source") or "") for item in issues if isinstance(item, dict)}
    issue_stages = {str(item.get("stage") or "") for item in issues if isinstance(item, dict)}

    if outcome == "not_run":
        return "needs_external_validation"
    if "spec_issue" in issue_sources:
        return "spec_ambiguity"
    if outcome == "compile_error" or "toolchain_issue" in issue_sources or "compile" in issue_stages:
        return "toolchain_issue"
    if outcome in {"assertion_fail", "protocol_violation", "timeout"}:
        if tb_contract and tb_contract.get("tb_mode") == "augment":
            return "dut_tb_contract_drift"
        return "local_rtl_issue"
    if outcome == "pass":
        return "unknown_or_mixed"
    return "unknown_or_mixed"


def _required_inputs(entry_mode: str, *, remote_validation_requested: bool) -> list[str]:
    if entry_mode == "spec-first generation":
        return ["spec"]
    if entry_mode == "plan-seeded generation":
        return ["spec", "codegen_plan"]
    if entry_mode == "existing-RTL assist/repair":
        return ["rtl"]
    if remote_validation_requested:
        return ["validation_artifacts", "remote_selection", "remote_workspace_settings"]
    return ["logs"]


def _present_inputs(
    *,
    spec_present: bool,
    plan_present: bool,
    rtl_paths: list[Path],
    tb_paths: list[Path],
    log_paths: list[Path],
    wave_paths: list[Path],
    validation_present: bool,
    remote_validation_requested: bool,
) -> set[str]:
    present: set[str] = set()
    if spec_present:
        present.add("spec")
    if plan_present:
        present.add("codegen_plan")
    if rtl_paths:
        present.add("rtl")
    if tb_paths:
        present.add("testbench")
    if log_paths:
        present.add("logs")
    if wave_paths:
        present.add("waveform")
    if validation_present:
        present.add("validation_artifacts")
    if remote_validation_requested:
        present.add("remote_validation_request")
    return present


def _next_action(entry_mode: str, *, remote_validation_requested: bool, plan_ready: bool) -> str:
    if remote_validation_requested:
        return "Resolve erie-remote-ssh server selection and remote workspace settings before any external validation claim."
    if entry_mode == "spec-first generation":
        return "Normalize requirements, build a Verilog-2001 codegen_plan, and run the mandatory validation gate."
    if entry_mode == "plan-seeded generation":
        if plan_ready:
            return "Use the plan as seed only; preserve requirements confirmation and the validation gate before RTL use."
        return "Review open codegen_plan questions before any RTL emission."
    if entry_mode == "existing-RTL assist/repair":
        return "Analyze or verify existing RTL with an explicit automation mode before mutation."
    return "Classify logs or validation evidence before selecting repair or rerun."


def _risk_flags(
    *,
    summary: str,
    logs: list[Path],
    validation: dict[str, Any],
    remote_validation_requested: bool,
    missing_artifacts: list[Path],
) -> list[str]:
    flags: list[str] = []
    lower_summary = summary.lower()
    if remote_validation_requested or "remote" in lower_summary:
        flags.append("remote_validation_requested")
    if missing_artifacts:
        flags.append("missing_artifact_inputs")
    combined_logs = "\n".join(_safe_read_text(path) for path in logs).lower()
    if any(token in combined_logs for token in ("syntax error", "compile error", "** error", "fatal")):
        flags.append("compile_failure")
    if "timeout" in combined_logs:
        flags.append("sim_timeout")
    if any(token in combined_logs for token in ("[tb_error]", "protocol violation", "mismatch")):
        flags.append("dut_tb_contract_risk")
    issues = validation.get("issues", []) if isinstance(validation.get("issues", []), list) else []
    if any(isinstance(item, dict) and item.get("source") == "toolchain_issue" for item in issues):
        flags.append("toolchain_issue")
    return flags


def _path_list(value: str | Path | list[str | Path] | None) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, list):
        return [Path(item) for item in value]
    return [Path(value)]


def _load_mapping(value: str | Path | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    path = Path(value)
    if not path.exists() or not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_artifact_mapping(artifact_dir: str | Path | None, names: tuple[str, ...]) -> dict[str, Any]:
    if artifact_dir is None:
        return {}
    root = Path(artifact_dir)
    for name in names:
        loaded = _load_mapping(root / name)
        if loaded:
            return loaded
    return {}


def _value_present(value: str | Path | dict[str, Any] | None) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return True
    path = Path(value)
    return path.exists()


def _existing_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists()]


def _missing_artifact_paths(
    *,
    spec: str | Path | dict[str, Any] | None,
    codegen_plan: str | Path | dict[str, Any] | None,
    rtl_paths: list[Path],
    tb_paths: list[Path],
    log_paths: list[Path],
    wave_paths: list[Path],
    validation: str | Path | dict[str, Any] | None,
) -> list[Path]:
    missing: list[Path] = []
    for item in (spec, codegen_plan, validation):
        if isinstance(item, (str, Path)) and not Path(item).exists():
            missing.append(Path(item))
    for path in [*rtl_paths, *tb_paths, *log_paths, *wave_paths]:
        if not path.exists():
            missing.append(path)
    return missing


def _artifact_exists(artifact_dir: str | Path | None, names: tuple[str, ...]) -> bool:
    if artifact_dir is None:
        return False
    root = Path(artifact_dir)
    return any((root / name).exists() for name in names)


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
