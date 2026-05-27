"""Structured reporting helpers for existing RTL verify-repair flows."""

from __future__ import annotations

from typing import Any


def simulation_slice_payload(*, compile_log: str, simulation_log: str, executed: bool, tb_contract: dict[str, Any], excerpt_fn: Any) -> dict[str, Any]:
    tb_tags = [tag for tag in tb_contract.get("log_tags", []) if tag in simulation_log]
    return {
        "version": 1,
        "executed": executed,
        "compile_excerpt": excerpt_fn(compile_log),
        "simulation_excerpt": excerpt_fn(simulation_log),
        "observed_tags": tb_tags,
        "transcript_prefix": tb_contract.get("transcript_prefix"),
    }


def timing_diagnostic_payload(
    diagnosis: dict[str, Any],
    *,
    validation_report: Any,
    verification_plan: dict[str, Any],
) -> dict[str, Any]:
    bug_class = {
        "compile_error": "compile",
        "assertion_fail": "protocol_or_logic",
        "protocol_violation": "protocol_or_timing",
        "timeout": "liveness",
        "pass": "none",
        "not_run": "not_run",
    }.get(str(diagnosis.get("outcome")), "unknown")
    return {
        "version": 1,
        "outcome": diagnosis.get("outcome"),
        "bug_class": bug_class,
        "validation_ok": bool(validation_report.ok()),
        "focus_signals": verification_plan.get("focus_signals", []),
        "findings": diagnosis.get("findings", []),
    }


def expected_trace_markdown(analysis: dict[str, Any], verification_plan: dict[str, Any]) -> str:
    module_name = str(analysis["module_info"]["name"])
    checkpoints = verification_plan.get("verification_targets", [])
    lines = [
        f"# Expected Trace: {module_name}",
        "",
        "This trace is analysis-derived and used as a stable semantic checkpoint summary.",
        "",
        "| Step | Checkpoint | Signals | Expectation |",
        "| --- | --- | --- | --- |",
    ]
    for index, checkpoint in enumerate(checkpoints, start=1):
        signals = ", ".join(checkpoint.get("signals", [])) or "n/a"
        expectation = str(checkpoint.get("description") or checkpoint.get("name") or "analysis-derived behavior")
        lines.append(f"| {index} | {checkpoint.get('check_id', f'checkpoint_{index}')} | {signals} | {expectation} |")
    if len(lines) == 5:
        lines.append("| 1 | no_checkpoints | n/a | No verification checkpoints were inferred. |")
    lines.append("")
    return "\n".join(lines)


def waveform_diff_payload(diagnosis: dict[str, Any], *, verification_plan: dict[str, Any], executed: bool) -> dict[str, Any]:
    return {
        "version": 1,
        "executed": executed,
        "status": "pass" if diagnosis.get("outcome") == "pass" else "pending_review",
        "focus_signals": verification_plan.get("focus_signals", []),
        "summary": diagnosis.get("findings", []),
    }


def testcase_matrix_payload(verification_plan: dict[str, Any], *, tb_contract: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    cases = []
    for index, target in enumerate(verification_plan.get("verification_targets", []), start=1):
        cases.append(
            {
                "case_id": target.get("check_id", f"checkpoint_{index}"),
                "category": target.get("category", "behavior"),
                "signals": target.get("signals", []),
                "expectation": target.get("description") or target.get("name") or "analysis-derived verification target",
                "log_tags": tb_contract.get("log_tags", []),
                "status": "covered" if diagnosis.get("outcome") != "compile_error" else "blocked_by_compile",
            }
        )
    return {"version": 1, "tb_mode": tb_contract.get("tb_mode"), "cases": cases}


def run_summary_payload(
    *,
    diagnosis: dict[str, Any],
    validation_report: Any,
    tb_contract: dict[str, Any],
    rtl_mutation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": 1,
        "status": "completed",
        "outcome": diagnosis.get("outcome"),
        "validation_ok": bool(validation_report.ok()),
        "tb_mode": tb_contract.get("tb_mode"),
        "tb_language": tb_contract.get("tb_language"),
        "rtl_patch_applied": bool(rtl_mutation.get("applied")),
        "confirmation_required": bool(rtl_mutation.get("confirmation_required")),
    }


def synth_readiness_payload(validation_report: Any, *, readiness: str) -> dict[str, Any]:
    metrics = validation_report.to_dict().get("metrics", {})
    return {
        "version": 1,
        "requested_readiness": readiness,
        "selected_simulator_backend": metrics.get("selected_simulator_backend"),
        "executed_tools": metrics.get("executed_tools", []),
        "missing_preferred_backends": metrics.get("missing_preferred_backends", []),
        "selection_policy": metrics.get("selection_policy"),
        "implement_requested": readiness == "implement",
        "validation_ok": bool(validation_report.ok()),
    }


def terminal_status_payload(
    *,
    diagnosis: dict[str, Any],
    validation_report: Any,
    tb_mutation: dict[str, Any],
    rtl_mutation: dict[str, Any],
) -> dict[str, Any]:
    success = diagnosis.get("outcome") == "pass" and validation_report.ok()
    return {
        "version": 1,
        "success": success,
        "outcome": diagnosis.get("outcome"),
        "validation_ok": bool(validation_report.ok()),
        "tb_mutation_applied": bool(tb_mutation.get("applied")),
        "rtl_mutation_applied": bool(rtl_mutation.get("applied")),
        "message": "Verification loop reached a clean PASS state." if success else "Verification loop did not reach a terminal PASS state.",
    }
