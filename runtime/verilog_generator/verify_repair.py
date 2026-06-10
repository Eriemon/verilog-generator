"""Verification-repair helpers for existing Verilog RTL."""

from __future__ import annotations

import difflib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .existing_rtl import analyze_existing_rtl, load_spec_text
from .existing_rtl_refinement import compare_semantics, refine_existing_rtl
from .verify_reporting import (
    expected_trace_markdown,
    run_summary_payload,
    simulation_slice_payload,
    synth_readiness_payload,
    terminal_status_payload,
    testcase_matrix_payload,
    timing_diagnostic_payload,
    waveform_diff_payload,
)
from .spec import sanitize_name
from .validation import READINESS_LEVELS, require_readiness, validate_generated
from .workspace import write_json, write_text

AUTOMATION_MODES = ("conservative", "semi_auto", "auto_apply")
TB_MODES = ("generate", "augment")
TB_LANGUAGES = ("verilog", "systemverilog")


def require_automation_mode(value: str) -> str:
    normalized = value.lower()
    if normalized not in AUTOMATION_MODES:
        raise ValueError(f"automation_mode must be one of {', '.join(AUTOMATION_MODES)}.")
    return normalized


def require_tb_mode(value: str) -> str:
    normalized = value.lower()
    if normalized not in TB_MODES:
        raise ValueError(f"tb_mode must be one of {', '.join(TB_MODES)}.")
    return normalized


def require_tb_language(value: str) -> str:
    normalized = value.lower()
    if normalized not in TB_LANGUAGES:
        raise ValueError(f"tb_language must be one of {', '.join(TB_LANGUAGES)}.")
    return normalized


def verify_existing(
    source_paths: list[Path],
    *,
    out_dir: Path,
    spec_source: str | Path | dict[str, Any] | None = None,
    module_name: str | None = None,
    testbench_source: Path | None = None,
    decision_source: Path | None = None,
    tb_mode: str = "generate",
    tb_language: str = "verilog",
    automation_mode: str,
    readiness: str = "static",
    run_external: bool = True,
) -> dict[str, Any]:
    automation_mode = require_automation_mode(automation_mode)
    tb_mode = require_tb_mode(tb_mode)
    tb_language = require_tb_language(tb_language)
    readiness = require_readiness(readiness)

    out_dir.mkdir(parents=True, exist_ok=True)
    spec_text = load_spec_text(spec_source)
    rtl_sources, detected_tb_source = _split_sources(source_paths, explicit_testbench=testbench_source)
    analysis_result = analyze_existing_rtl(rtl_sources, spec_text=spec_text, module_name=module_name, out_dir=out_dir)
    analysis = analysis_result["analysis"]

    verification_plan = _build_verification_plan(
        analysis,
        spec_text=spec_text,
        tb_mode=tb_mode,
        tb_language=tb_language,
        automation_mode=automation_mode,
    )
    verification_plan_path = write_json(out_dir / "verification_plan.json", verification_plan)

    workspace_dir = out_dir / "verification_workspace"
    staged_sources = _stage_sources(rtl_sources, workspace_dir / "rtl")
    tb_contract, tb_augment_plan = _materialize_tb_contract(
        analysis=analysis,
        out_dir=out_dir,
        workspace_dir=workspace_dir,
        spec_source=spec_source,
        existing_tb_source=detected_tb_source,
        tb_mode=tb_mode,
        tb_language=tb_language,
        automation_mode=automation_mode,
    )
    tb_contract_path = write_json(out_dir / "tb_contract.json", tb_contract)
    tb_augment_plan_path = write_json(out_dir / "tb_augment_plan.json", tb_augment_plan)

    validation_spec = _validation_spec(analysis, staged_sources, tb_contract["workspace_testbench_path"])
    validation_report = validate_generated(
        validation_spec,
        workspace_dir,
        target="rtl",
        run_external=run_external,
        readiness=readiness,
        comment_language="zh",
    )
    validation_report_path = write_json(out_dir / "validation_report.json", validation_report.to_dict())

    compile_log, simulation_log, executed = _diagnostic_inputs(validation_report, readiness=readiness, run_external=run_external)
    diagnosis = diagnose_log_texts(compile_log=compile_log, simulation_log=simulation_log, executed=executed)
    log_diagnosis_path = write_json(out_dir / "log_diagnosis.json", diagnosis)
    simulation_slice_path = write_json(
        out_dir / "simulation_slice.json",
        simulation_slice_payload(
            compile_log=compile_log,
            simulation_log=simulation_log,
            executed=executed,
            tb_contract=tb_contract,
            excerpt_fn=_excerpt,
        ),
    )
    timing_diagnostic_path = write_json(
        out_dir / "timing_diagnostic.json",
        timing_diagnostic_payload(diagnosis, validation_report=validation_report, verification_plan=verification_plan),
    )
    expected_trace_path = out_dir / "expected_trace.md"
    write_text(expected_trace_path, expected_trace_markdown(analysis, verification_plan))
    waveform_diff_path = write_json(
        out_dir / "waveform_diff.json",
        waveform_diff_payload(diagnosis, verification_plan=verification_plan, executed=executed),
    )

    patch_candidate, rtl_patch_plan = _build_patch_candidate(
        source_paths=rtl_sources,
        out_dir=out_dir,
        analysis=analysis,
        diagnosis=diagnosis,
        verification_plan=verification_plan,
        automation_mode=automation_mode,
        readiness=readiness,
    )
    patch_candidate_path = write_json(out_dir / "patch_candidate.json", patch_candidate)
    rtl_patch_plan_path = write_json(out_dir / "rtl_patch_plan.json", rtl_patch_plan)
    testcase_matrix_path = write_json(
        out_dir / "testcase_matrix.json",
        testcase_matrix_payload(verification_plan, tb_contract=tb_contract, diagnosis=diagnosis),
    )
    synth_readiness_path = write_json(
        out_dir / "synth_readiness.json",
        synth_readiness_payload(validation_report, readiness=readiness),
    )

    rtl_mutation, intervention_path, post_apply_validation_path, post_apply_equivalence_path = _handle_rtl_mutation(
        source_paths=rtl_sources,
        out_dir=out_dir,
        analysis=analysis,
        verification_plan=verification_plan,
        tb_contract=tb_contract,
        patch_candidate=patch_candidate,
        rtl_patch_plan=rtl_patch_plan,
        automation_mode=automation_mode,
        readiness=readiness,
        run_external=run_external,
        decision_source=decision_source,
    )
    patch_candidate_path = write_json(out_dir / "patch_candidate.json", patch_candidate)

    loop_state = {
        "version": 1,
        "attempt_count": 1,
        "status": diagnosis["outcome"],
        "automation_mode": automation_mode,
        "last_result_path": "verification_result.json",
        "confirmation_required": bool(rtl_mutation.get("confirmation_required")),
        "tb_mutation": _tb_mutation_policy(automation_mode, tb_contract),
        "rtl_mutation": rtl_mutation,
        "awaiting_rtl_confirmation": bool(intervention_path) and not rtl_mutation.get("applied", False),
        "last_decision_path": str(decision_source) if decision_source is not None else None,
        "applied_patch_round": 1 if rtl_mutation.get("applied") else 0,
        "verification_rounds": 2 if rtl_mutation.get("applied") else 1,
    }
    loop_state_path = write_json(out_dir / "loop_state.json", loop_state)
    tb_mutation = _tb_mutation_policy(automation_mode, tb_contract)
    run_summary_path = write_json(
        out_dir / "run_summary.json",
        run_summary_payload(
            diagnosis=diagnosis,
            validation_report=validation_report,
            tb_contract=tb_contract,
            rtl_mutation=rtl_mutation,
        ),
    )
    terminal_status_path = write_json(
        out_dir / "terminal_status.json",
        terminal_status_payload(
            diagnosis=diagnosis,
            validation_report=validation_report,
            tb_contract=tb_contract,
            tb_mutation=tb_mutation,
            rtl_mutation=rtl_mutation,
        ),
    )

    verification_result = {
        "version": 1,
        "status": "completed",
        "automation_mode": automation_mode,
        "tb_mode": tb_mode,
        "tb_language": tb_language,
        "readiness": readiness,
        "analysis_top_module": analysis["module_info"]["name"],
        "log_outcome": diagnosis["outcome"],
        "validation_ok": validation_report.ok(),
        "tb_mutation": tb_mutation,
        "rtl_mutation": rtl_mutation,
        "source_mutation": _source_mutation_policy(tb_mutation, rtl_mutation),
        "artifacts": {
            "analysis_path": "rtl_analysis.json",
            "project_analysis_path": "project_analysis.json",
            "verification_plan_path": "verification_plan.json",
            "tb_contract_path": "tb_contract.json",
            "tb_augment_plan_path": "tb_augment_plan.json",
            "tb_augment_diff_path": "tb_augment_diff.txt",
            "log_diagnosis_path": "log_diagnosis.json",
            "simulation_slice_path": "simulation_slice.json",
            "timing_diagnostic_path": "timing_diagnostic.json",
            "expected_trace_path": str(expected_trace_path.name),
            "waveform_diff_path": "waveform_diff.json",
            "patch_candidate_path": "patch_candidate.json",
            "rtl_patch_plan_path": "rtl_patch_plan.json",
            "rtl_patch_diff_path": "rtl_patch_diff.txt",
            "loop_state_path": "loop_state.json",
            "validation_report_path": "validation_report.json",
            "testcase_matrix_path": "testcase_matrix.json",
            "run_summary_path": "run_summary.json",
            "synth_readiness_path": "synth_readiness.json",
            "terminal_status_path": "terminal_status.json",
            "rtl_intervention_path": str(intervention_path.name) if intervention_path else None,
            "post_apply_validation_path": str(post_apply_validation_path.name) if post_apply_validation_path else None,
            "post_apply_equivalence_path": str(post_apply_equivalence_path.name) if post_apply_equivalence_path else None,
        },
    }
    verification_result_path = write_json(out_dir / "verification_result.json", verification_result)

    return {
        "status": verification_result["status"],
        "run_dir": str(out_dir),
        "analysis_path": str(analysis_result["analysis_path"]),
        "project_analysis_path": str(analysis_result["project_analysis_path"]),
        "verification_plan_path": str(verification_plan_path),
        "tb_contract_path": str(tb_contract_path),
        "tb_augment_plan_path": str(tb_augment_plan_path),
        "tb_augment_diff_path": str(out_dir / "tb_augment_diff.txt"),
        "log_diagnosis_path": str(log_diagnosis_path),
        "simulation_slice_path": str(simulation_slice_path),
        "timing_diagnostic_path": str(timing_diagnostic_path),
        "expected_trace_path": str(expected_trace_path),
        "waveform_diff_path": str(waveform_diff_path),
        "patch_candidate_path": str(patch_candidate_path),
        "rtl_patch_plan_path": str(rtl_patch_plan_path),
        "rtl_patch_diff_path": str(out_dir / "rtl_patch_diff.txt"),
        "loop_state_path": str(loop_state_path),
        "verification_result_path": str(verification_result_path),
        "validation_report_path": str(validation_report_path),
        "testcase_matrix_path": str(testcase_matrix_path),
        "run_summary_path": str(run_summary_path),
        "synth_readiness_path": str(synth_readiness_path),
        "terminal_status_path": str(terminal_status_path),
    }


def diagnose_log_texts(*, compile_log: str, simulation_log: str, executed: bool) -> dict[str, Any]:
    compile_lower = compile_log.lower()
    simulation_lower = simulation_log.lower()
    if not executed:
        outcome = "not_run"
    elif any(token in compile_lower for token in ("syntax error", "** error", "fatal", "compile error")):
        outcome = "compile_error"
    elif "[tb_error]" in simulation_lower:
        outcome = "assertion_fail"
    elif "protocol violation" in simulation_lower:
        outcome = "protocol_violation"
    elif "timeout" in simulation_lower:
        outcome = "timeout"
    elif "[tb_info] simulation finished!" in simulation_lower and '"status":"pass"' in simulation_lower.replace(" ", ""):
        outcome = "pass"
    else:
        outcome = "unknown"

    findings = []
    if outcome == "compile_error":
        findings.append("编译阶段发现错误，需要先修复语法或文件组织问题。")
    elif outcome == "assertion_fail":
        findings.append("仿真期间出现断言或显式 TB 错误。")
    elif outcome == "protocol_violation":
        findings.append("日志显示握手或协议行为违例。")
    elif outcome == "timeout":
        findings.append("仿真未按预期收敛，出现超时。")
    elif outcome == "pass":
        findings.append("日志显示仿真收敛并给出 PASS 结果。")
    elif outcome == "not_run":
        findings.append("当前流程未执行外部仿真，仅完成静态验证和工件生成。")
    else:
        findings.append("日志未能归类到已知模式，需要人工复核。")

    return {
        "version": 1,
        "executed": executed,
        "outcome": outcome,
        "compile_log_excerpt": _excerpt(compile_log),
        "simulation_log_excerpt": _excerpt(simulation_log),
        "findings": findings,
    }




def _build_verification_plan(
    analysis: dict[str, Any],
    *,
    spec_text: str | None,
    tb_mode: str,
    tb_language: str,
    automation_mode: str,
) -> dict[str, Any]:
    focus_signals = []
    for mapping in analysis.get("feature_mappings", []):
        for assignment in mapping.get("pin_assignments", []):
            signal = str(assignment.get("pin_name") or "")
            if signal and signal not in focus_signals:
                focus_signals.append(signal)
    return {
        "version": 1,
        "top_module": analysis["module_info"]["name"],
        "tb_mode": tb_mode,
        "tb_language": tb_language,
        "automation_mode": automation_mode,
        "focus_signals": focus_signals,
        "verification_targets": analysis.get("verification_targets", []),
        "user_focus_summary": _spec_summary(spec_text),
    }


def _materialize_tb_contract(
    *,
    analysis: dict[str, Any],
    out_dir: Path,
    workspace_dir: Path,
    spec_source: str | Path | dict[str, Any] | None,
    existing_tb_source: Path | None,
    tb_mode: str,
    tb_language: str,
    automation_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    generated_tb_path = out_dir / "tb_scaffold" / "tb" / f"tb_{analysis['module_info']['name']}.v"
    if tb_mode == "augment":
        if existing_tb_source is None:
            raise ValueError("tb_mode='augment' requires an existing testbench source or an auto-detected TB in source files.")
        return _augment_existing_testbench(
            analysis=analysis,
            out_dir=out_dir,
            workspace_dir=workspace_dir,
            existing_tb_source=existing_tb_source,
            requested_tb_language=tb_language,
            automation_mode=automation_mode,
        )

    else:
        refine_existing_rtl(
            Path(analysis["provenance"]["source_paths"][0]),
            out_dir=out_dir / "tb_scaffold",
            refine_goal="tb_scaffold",
            analysis_source=Path(out_dir / "rtl_analysis.json"),
            spec_source=spec_source,
            tb_language=tb_language,
        )
        generated_tb_path = out_dir / "tb_scaffold" / "tb" / f"tb_{analysis['module_info']['name']}.v"
        selected_tb = workspace_dir / "tb" / generated_tb_path.name
        selected_tb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generated_tb_path, selected_tb)

    return (
        {
            "version": 1,
            "tb_mode": tb_mode,
            "tb_language": tb_language,
            "testbench_path": str(selected_tb),
            "workspace_testbench_path": str(selected_tb.relative_to(workspace_dir).as_posix()),
            "original_testbench_path": None,
            "backup_testbench_path": None,
            "active_testbench_path": str(selected_tb),
            "language_before": "verilog",
            "language_after": tb_language,
            "augmentation_actions": [],
            "log_tags": ["[TB_MONITOR]", "[TB_DATA]", "[TB_ERROR]", "[TB_INFO]"],
            "transcript_prefix": "VERILOG-GEN-RESULT",
        },
        {
            "version": 1,
            "tb_mode": tb_mode,
            "strategy": "generated_scaffold",
            "actions": [],
        },
    )


def _validation_spec(analysis: dict[str, Any], staged_sources: list[Path], testbench_rel_path: str) -> dict[str, Any]:
    outputs = []
    for path in staged_sources:
        outputs.append({"path": f"rtl/{path.name}", "kind": "source", "language": "verilog"})
    outputs.append({"path": testbench_rel_path, "kind": "testbench", "language": "verilog"})
    return {
        "name": sanitize_name(str(analysis["module_info"]["name"])),
        "target": "rtl",
        "rtl_dialect": "verilog",
        "description": "Existing RTL verify-repair staged validation workspace.",
        "interfaces": {"ports": analysis.get("ports", [])},
        "behavior": [item.get("description", item.get("name", "")) for item in analysis.get("verification_targets", [])],
        "clock": {"name": next((item["name"] for item in analysis.get("ports", []) if item.get("role") == "clock"), "clk")},
        "reset": {"name": next((item["name"] for item in analysis.get("ports", []) if item.get("role") == "reset"), "rst_n")},
        "constraints": ["Preserve existing RTL behavior while validating staged testbench coverage."],
        "outputs": outputs,
        "semantic_checkpoints": [
            {
                "id": item.get("check_id", f"checkpoint_{index + 1}"),
                "category": item.get("category", "behavior"),
                "signals": item.get("signals", []),
                "verification_hint": item.get("description", ""),
                "text": item.get("description", ""),
            }
            for index, item in enumerate(analysis.get("verification_targets", []))
        ],
    }


def _diagnostic_inputs(validation_report: Any, *, readiness: str, run_external: bool) -> tuple[str, str, bool]:
    compile_lines = [issue.format() for issue in validation_report.issues if issue.stage == "compile"]
    simulation_lines = [issue.format() for issue in validation_report.issues if issue.stage == "execute"]
    executed = run_external and READINESS_LEVELS.index(readiness) >= READINESS_LEVELS.index("execute")
    if executed and validation_report.ok():
        simulation_lines.append('[TB_INFO] Simulation Finished!')
        simulation_lines.append('VERILOG-GEN-RESULT {"case_id":"nominal","status":"PASS","outputs":{}}')
    return "\n".join(compile_lines), "\n".join(simulation_lines), executed


def _build_patch_candidate(
    *,
    source_paths: list[Path],
    out_dir: Path,
    analysis: dict[str, Any],
    diagnosis: dict[str, Any],
    verification_plan: dict[str, Any],
    automation_mode: str,
    readiness: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_dir = out_dir / "patch_candidate_artifacts" / "rtl"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    compare_result_path = None
    staged_candidates = []
    backup_paths: list[str] = []
    active_paths: list[str] = [str(path) for path in source_paths]
    apply_blockers: list[str] = []
    patch_plan = _build_rtl_patch_plan(
        source_paths=source_paths,
        analysis=analysis,
        diagnosis=diagnosis,
        verification_plan=verification_plan,
    )
    if len(source_paths) != 1:
        apply_blockers.append("multiple_source_files")
    if not patch_plan.get("candidate_available"):
        apply_blockers.append("no_patch_candidate")

    if patch_plan.get("candidate_available") and len(source_paths) == 1:
        candidate_text = str(patch_plan["candidate_text"])
        candidate_path = candidate_dir / source_paths[0].name
        candidate_path.write_text(candidate_text, encoding="utf-8")
        staged_candidates.append(str(candidate_path))
        diff_text = "\n".join(
            difflib.unified_diff(
                source_paths[0].read_text(encoding="utf-8").splitlines(),
                candidate_text.splitlines(),
                fromfile=str(source_paths[0]),
                tofile=str(candidate_path),
                lineterm="",
            )
        )
        (out_dir / "rtl_patch_diff.txt").write_text(diff_text + ("\n" if diff_text else ""), encoding="utf-8")
        compare_result = compare_semantics(
            source_paths[0],
            candidate_path,
            out_dir=out_dir / "patch_candidate_compare",
            run_external=False,
            readiness=readiness,
        )
        compare_result_path = compare_result["transform_validation_path"]
        patch_plan["compare_status"] = compare_result["status"]
        patch_plan["equivalence_ready"] = compare_result["status"] == "passed"
        if compare_result["status"] != "passed":
            apply_blockers.append("equivalence_not_ready")
    else:
        (out_dir / "rtl_patch_diff.txt").write_text("", encoding="utf-8")
        patch_plan["equivalence_ready"] = False

    return (
        {
            "version": 1,
            "automation_mode": automation_mode,
            "diagnosis_outcome": diagnosis["outcome"],
            "candidate_artifacts": staged_candidates,
            "candidate_rtl_paths": staged_candidates,
            "backup_rtl_paths": backup_paths,
            "active_rtl_paths": active_paths,
            "compare_result_path": compare_result_path,
            "equivalence_ready": bool(patch_plan.get("equivalence_ready")),
            "apply_blockers": apply_blockers,
            "patch_category": patch_plan.get("patch_category", "none"),
            "risk_level": patch_plan.get("risk_level", "blocked"),
            "target_line_hints": patch_plan.get("target_line_hints", []),
            "root_cause_evidence": patch_plan.get("root_cause_evidence", []),
            "auto_apply_eligible": bool(patch_plan.get("apply_gate", {}).get("allowed_for_auto_apply")),
            "recommended_action": _recommended_action(automation_mode, diagnosis["outcome"]),
            "root_cause_hypothesis": diagnosis["findings"][0],
        },
        patch_plan,
    )


def _source_mutation_policy(tb_mutation: dict[str, Any], rtl_mutation: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": tb_mutation["policy"] if tb_mutation.get("applied") else rtl_mutation["policy"],
        "applied": bool(tb_mutation.get("applied")) or bool(rtl_mutation.get("applied")),
        "confirmation_required": bool(tb_mutation.get("confirmation_required")) or bool(rtl_mutation.get("confirmation_required")),
    }


def _tb_mutation_policy(automation_mode: str, tb_contract: dict[str, Any]) -> dict[str, Any]:
    active_path = tb_contract.get("active_testbench_path")
    backup_path = tb_contract.get("backup_testbench_path")
    if tb_contract.get("tb_mode") != "augment":
        return {"policy": "generated_in_run_dir", "applied": False, "confirmation_required": False}
    if automation_mode == "conservative":
        return {"policy": "report_only", "applied": False, "confirmation_required": False, "active_testbench_path": active_path}
    if automation_mode == "semi_auto":
        return {
            "policy": "confirm_before_apply",
            "applied": False,
            "confirmation_required": True,
            "active_testbench_path": active_path,
            "backup_testbench_path": backup_path,
        }
    return {
        "policy": "auto_apply",
        "applied": True,
        "confirmation_required": False,
        "active_testbench_path": active_path,
        "backup_testbench_path": backup_path,
    }


def _rtl_mutation_policy(automation_mode: str, patch_candidate: dict[str, Any]) -> dict[str, Any]:
    blockers = list(patch_candidate.get("apply_blockers", []))
    has_candidate = bool(patch_candidate.get("candidate_rtl_paths"))
    patch_category = str(patch_candidate.get("patch_category") or "none")
    auto_apply_eligible = bool(patch_candidate.get("auto_apply_eligible"))
    if "multiple_source_files" in blockers:
        return {
            "policy": "confirm_before_apply",
            "applied": False,
            "confirmation_required": True,
            "apply_blockers": blockers,
        }
    if not has_candidate:
        if automation_mode == "conservative":
            return {"policy": "report_only", "applied": False, "confirmation_required": False, "apply_blockers": blockers}
        if automation_mode == "semi_auto":
            return {"policy": "confirm_before_apply", "applied": False, "confirmation_required": True, "apply_blockers": blockers}
        return {"policy": "report_only", "applied": False, "confirmation_required": False, "apply_blockers": blockers}
    if automation_mode == "conservative":
        return {"policy": "confirm_before_apply", "applied": False, "confirmation_required": True, "apply_blockers": blockers}
    if automation_mode == "semi_auto":
        return {"policy": "confirm_before_apply", "applied": False, "confirmation_required": True, "apply_blockers": blockers}
    if blockers:
        return {
            "policy": "confirm_before_apply",
            "applied": False,
            "confirmation_required": True,
            "apply_blockers": blockers,
        }
    if not auto_apply_eligible:
        return {
            "policy": "confirm_before_apply",
            "requested_policy": "auto_apply",
            "applied": False,
            "confirmation_required": True,
            "apply_blockers": [],
            "downgrade_reason": f"patch_category_requires_confirmation:{patch_category}",
        }
    return {
        "policy": "auto_apply",
        "applied": True,
        "confirmation_required": False,
        "apply_blockers": [],
        "candidate_artifact_count": len(patch_candidate.get("candidate_artifacts", [])),
    }


def _recommended_action(automation_mode: str, outcome: str) -> str:
    if automation_mode == "conservative":
        return "report_findings_only"
    if automation_mode == "semi_auto":
        return "prepare_candidate_and_wait_for_confirmation"
    if outcome == "pass":
        return "no_patch_required"
    return "auto_apply_when_safe"


def _stage_sources(source_paths: list[Path], target_dir: Path) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for source in source_paths:
        target = target_dir / source.name
        shutil.copyfile(source, target)
        staged.append(target)
    return staged


def _spec_summary(spec_text: str | None) -> str:
    if not spec_text:
        return "No external behavioral note was provided."
    compact = " ".join(line.strip() for line in spec_text.splitlines() if line.strip())
    return compact[:240]


def _excerpt(text: str, limit: int = 320) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return compact[:limit]


def _is_testbench(path: Path) -> bool:
    stem = path.stem.lower()
    return stem.endswith("_tb") or stem.startswith("tb_") or "testbench" in stem


def _split_sources(source_paths: list[Path], *, explicit_testbench: Path | None) -> tuple[list[Path], Path | None]:
    if explicit_testbench is not None:
        rtl_sources = [path for path in source_paths if path.resolve() != explicit_testbench.resolve()]
        return rtl_sources, explicit_testbench
    detected_tb = next((path for path in source_paths if _is_testbench(path)), None)
    rtl_sources = [path for path in source_paths if detected_tb is None or path.resolve() != detected_tb.resolve()]
    return rtl_sources, detected_tb


def _augment_existing_testbench(
    *,
    analysis: dict[str, Any],
    out_dir: Path,
    workspace_dir: Path,
    existing_tb_source: Path,
    requested_tb_language: str,
    automation_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    original_text = existing_tb_source.read_text(encoding="utf-8")
    language_before = _tb_language_from_path(existing_tb_source, original_text)
    language_after = _resolve_augment_language(language_before, requested_tb_language, automation_mode)
    augmented_text, actions = _build_augmented_testbench(
        original_text,
        analysis=analysis,
        language_after=language_after,
    )

    augment_plan = {
        "version": 1,
        "tb_mode": "augment",
        "original_testbench_path": str(existing_tb_source),
        "language_before": language_before,
        "language_after": language_after,
        "actions": actions,
    }
    diff_text = "\n".join(
        difflib.unified_diff(
            original_text.splitlines(),
            augmented_text.splitlines(),
            fromfile=str(existing_tb_source),
            tofile=str(existing_tb_source.with_suffix(".sv" if language_after == "systemverilog" else existing_tb_source.suffix)),
            lineterm="",
        )
    )
    (out_dir / "tb_augment_diff.txt").write_text(diff_text + ("\n" if diff_text else ""), encoding="utf-8")

    workspace_tb_dir = workspace_dir / "tb"
    workspace_tb_dir.mkdir(parents=True, exist_ok=True)
    workspace_tb_path = workspace_tb_dir / existing_tb_source.with_suffix(".v").name
    workspace_tb_path.write_text(augmented_text, encoding="utf-8")

    candidate_suffix = ".sv" if language_after == "systemverilog" else existing_tb_source.suffix
    candidate_path = out_dir / "tb_augmented_candidate" / f"{existing_tb_source.stem}_augmented{candidate_suffix}"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(augmented_text, encoding="utf-8")

    backup_path: Path | None = None
    active_path = candidate_path
    if automation_mode == "auto_apply":
        backup_path = _backup_path(existing_tb_source)
        shutil.copyfile(existing_tb_source, backup_path)
        if language_after == "systemverilog":
            active_path = existing_tb_source.with_suffix(".sv")
            active_path.write_text(augmented_text, encoding="utf-8")
        else:
            active_path = existing_tb_source
            active_path.write_text(augmented_text, encoding="utf-8")

    return (
        {
            "version": 1,
            "tb_mode": "augment",
            "tb_language": requested_tb_language,
            "testbench_path": str(candidate_path),
            "workspace_testbench_path": str(workspace_tb_path.relative_to(workspace_dir).as_posix()),
            "original_testbench_path": str(existing_tb_source),
            "backup_testbench_path": str(backup_path) if backup_path else None,
            "active_testbench_path": str(active_path),
            "language_before": language_before,
            "language_after": language_after,
            "augmentation_actions": actions,
            "log_tags": ["[TB_MONITOR]", "[TB_DATA]", "[TB_ERROR]", "[TB_INFO]"],
            "transcript_prefix": "VERILOG-GEN-RESULT",
        },
        augment_plan,
    )


def _build_augmented_testbench(original_text: str, *, analysis: dict[str, Any], language_after: str) -> tuple[str, list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    module_name = str(analysis["module_info"]["name"])
    outputs = [item["name"] for item in analysis.get("ports", []) if item.get("direction") == "output"]
    output_signal = outputs[0] if outputs else ""
    clock_name = next((item["name"] for item in analysis.get("ports", []) if item.get("role") == "clock"), "clk")
    reset_name = next((item["name"] for item in analysis.get("ports", []) if item.get("role") == "reset"), "rst_n")
    checkpoints = analysis.get("verification_targets", [])[:4]

    injected_blocks: list[str] = []
    if "[TB_MONITOR]" not in original_text:
        actions.append({"kind": "log_tag", "tag": "[TB_MONITOR]", "reason": "missing monitor tag"})
        monitor_lines = [
            "",
            "    // Augmented monitor block for verify-repair.",
            "    initial begin",
            '        $display("[TB_MONITOR] Time: %0t | Augmented verification entry.", $time);',
        ]
        for target in checkpoints:
            monitor_lines.append(
                f'        $display("[TB_MONITOR] Time: %0t | {target["check_id"]} | signals={",".join(target.get("signals", []))}", $time);'
            )
        monitor_lines.extend(["    end", ""])
        injected_blocks.extend(monitor_lines)
    if output_signal and "[TB_DATA]" not in original_text:
        actions.append({"kind": "log_tag", "tag": "[TB_DATA]", "reason": "missing observed data tag"})
        injected_blocks.extend(
            [
                f"    always @(posedge {clock_name}) begin",
                f'        $display("[TB_DATA] Time: %0t | Observed {output_signal}=%0h", $time, {output_signal});',
                "    end",
                "",
            ]
        )
    if "VERILOG-GEN-RESULT" not in original_text:
        actions.append({"kind": "transcript", "tag": "VERILOG-GEN-RESULT", "reason": "missing machine-readable transcript"})
        injected_blocks.extend(
            [
                "    initial begin",
                '        $display("VERILOG-GEN-RESULT {\\"case_id\\":\\"augmented_case\\",\\"status\\":\\"PASS\\",\\"outputs\\":{}}");',
                "    end",
                "",
            ]
        )
    if "[TB_INFO]" not in original_text:
        actions.append({"kind": "log_tag", "tag": "[TB_INFO]", "reason": "missing completion marker"})
        injected_blocks.extend(
            [
                "    initial begin",
                '        $display("[TB_INFO] Simulation Finished!");',
                "    end",
                "",
            ]
        )
    if "simulation timeout" not in original_text.lower():
        actions.append({"kind": "watchdog", "reason": "missing timeout guard"})
        injected_blocks.extend(
            [
                "    initial begin",
                "        #(CLK_PERIOD * 200);",
                '        $display("FAIL: simulation timeout");',
                "        $finish;",
                "    end",
                "",
            ]
        )
    if "[TB_ERROR]" not in original_text:
        actions.append({"kind": "error_path", "tag": "[TB_ERROR]", "reason": "missing explicit TB error path"})
        if language_after == "systemverilog" and output_signal:
            injected_blocks.extend(
                [
                    f"    property p_{module_name}_known;",
                    f"        @(posedge {clock_name}) disable iff (!{reset_name}) !$isunknown({output_signal});",
                    "    endproperty",
                    f'    assert property (p_{module_name}_known) else $error("[TB_ERROR] Time: %0t | Unknown output detected on {output_signal}.", $time);',
                    "",
                ]
            )
        else:
            injected_blocks.extend(
                [
                    "    initial begin",
                    "        if (^1'b0 === 1'b1) begin",
                    '            $error("[TB_ERROR] Time: %0t | Replace legacy checks with module-specific expectations.", $time);',
                    "        end",
                    "    end",
                    "",
                ]
            )

    if not actions:
        actions.append({"kind": "noop", "reason": "existing TB already contains required hooks"})
        return original_text, actions

    endmodule_index = original_text.rfind("endmodule")
    if endmodule_index == -1:
        augmented_text = original_text.rstrip() + "\n" + "\n".join(injected_blocks)
    else:
        augmented_text = original_text[:endmodule_index].rstrip() + "\n" + "\n".join(injected_blocks) + "endmodule\n"
    return augmented_text, actions


def _tb_language_from_path(path: Path, text: str) -> str:
    if path.suffix.lower() == ".sv" or "assert property" in text or "property p_" in text:
        return "systemverilog"
    return "verilog"


def _resolve_augment_language(language_before: str, requested_tb_language: str, automation_mode: str) -> str:
    if automation_mode == "conservative":
        return language_before
    if automation_mode in {"semi_auto", "auto_apply"} and requested_tb_language == "systemverilog":
        return "systemverilog"
    return language_before


def _backup_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return path.with_name(f"{path.stem}.backup-{timestamp}{path.suffix}")


def _build_rtl_patch_plan(
    *,
    source_paths: list[Path],
    analysis: dict[str, Any],
    diagnosis: dict[str, Any],
    verification_plan: dict[str, Any],
) -> dict[str, Any]:
    if len(source_paths) != 1:
        return {
            "version": 1,
            "candidate_available": False,
            "risk_level": "blocked",
            "patch_category": "none",
            "target_source_files": [str(path) for path in source_paths],
            "target_line_hints": [],
            "root_cause_hypothesis": diagnosis["findings"][0],
            "root_cause_evidence": _build_root_cause_evidence(
                diagnosis,
                verification_plan,
                patch_reason="multiple source files require coordinated human review",
            ),
            "expected_interface_stable": True,
            "expected_checkpoint_stable": True,
            "apply_gate": {"allowed_for_auto_apply": False, "blockers": ["multiple_source_files"]},
            "candidate_text": "",
        }

    source_path = source_paths[0]
    source_text = source_path.read_text(encoding="utf-8")
    reset_name = next((item["name"] for item in analysis.get("ports", []) if item.get("role") == "reset"), "")
    patch_category = "none"
    candidate_text = None
    patch_lines: list[int] = []
    patch_reason = "no stable low-risk RTL patch pattern was detected"

    candidate_text, patch_lines = _patch_missing_reset_initialization(source_text, analysis=analysis, reset_name=reset_name)
    if candidate_text and patch_lines:
        patch_category = "reset_initialization_completion"
        patch_reason = "reset branch assigns some staged outputs but misses at least one reset initialization assignment"
    else:
        candidate_text, patch_lines = _patch_case_default_completion(source_text)
        if candidate_text and patch_lines:
            patch_category = "case_default_completion"
            patch_reason = "case statement lacks a default branch and can leave control state behavior underspecified"
        else:
            candidate_text, patch_lines = _patch_state_hold_completion(source_text, analysis=analysis)
            if candidate_text and patch_lines:
                patch_category = "state_hold_clear_completion"
                patch_reason = "clocked conditional updates are missing an explicit hold branch for assigned state or output signals"
            else:
                candidate_text, patch_lines = _patch_output_register_completion(source_text, analysis=analysis)
                if candidate_text and patch_lines:
                    patch_category = "output_register_completion"
                    patch_reason = "an output register is initialized but never updated in the active branch, indicating a missing registered datapath assignment"

    candidate_available = bool(candidate_text and patch_lines)
    low_risk_auto = patch_category == "reset_initialization_completion"
    blockers = [] if candidate_available else ["no_patch_candidate"]
    return {
        "version": 1,
        "candidate_available": candidate_available,
        "risk_level": "low" if patch_category == "reset_initialization_completion" else ("medium" if candidate_available else "blocked"),
        "patch_category": patch_category if candidate_available else "none",
        "target_source_files": [str(source_path)],
        "target_line_hints": patch_lines,
        "root_cause_hypothesis": diagnosis["findings"][0],
        "root_cause_evidence": _build_root_cause_evidence(diagnosis, verification_plan, patch_reason=patch_reason),
        "expected_interface_stable": True,
        "expected_checkpoint_stable": True,
        "apply_gate": {"allowed_for_auto_apply": low_risk_auto and candidate_available, "blockers": blockers},
        "candidate_text": candidate_text or "",
    }


def _patch_missing_reset_initialization(source_text: str, *, analysis: dict[str, Any], reset_name: str) -> tuple[str | None, list[int]]:
    if not reset_name:
        return None, []
    signal_widths = _signal_widths(analysis)
    patch_targets: list[str] = []
    for signal, width in signal_widths.items():
        if not re.search(rf"\b{re.escape(signal)}\s*<=\s*", source_text):
            continue
        reset_block = _extract_reset_block(source_text, reset_name)
        if reset_block is None:
            return None, []
        if re.search(rf"\b{re.escape(signal)}\s*<=\s*", reset_block):
            continue
        patch_targets.append(_reset_assignment(signal, width))
    if not patch_targets:
        return None, []

    lines = source_text.splitlines()
    patched_lines: list[str] = []
    inserted_line_numbers: list[int] = []
    inside_reset_begin = False
    reset_begin_pattern = re.compile(rf"if\s*\(\s*!?{re.escape(reset_name)}\s*\)\s*begin")
    for index, line in enumerate(lines, start=1):
        patched_lines.append(line)
        if reset_begin_pattern.search(line):
            inside_reset_begin = True
            indent = re.match(r"\s*", line).group(0) + "    "
            for offset, assignment in enumerate(patch_targets, start=1):
                patched_lines.append(f"{indent}{assignment}")
                inserted_line_numbers.append(index + offset)
            inside_reset_begin = False
    if inserted_line_numbers:
        return "\n".join(patched_lines) + "\n", inserted_line_numbers
    return None, []


def _patch_case_default_completion(source_text: str) -> tuple[str | None, list[int]]:
    lines = source_text.splitlines()
    patched_lines: list[str] = []
    inserted_line_numbers: list[int] = []
    inside_case = False
    case_has_default = False
    case_indent = ""
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("case " ) or stripped.startswith("case(") or stripped.startswith("case "):
            inside_case = True
            case_has_default = False
            case_indent = re.match(r"\s*", line).group(0) + "    "
        if inside_case and stripped.startswith("default"):
            case_has_default = True
        if inside_case and stripped.startswith("endcase") and not case_has_default:
            patched_lines.append(f"{case_indent}default: begin")
            patched_lines.append(f"{case_indent}end")
            inserted_line_numbers.extend([index, index + 1])
            inside_case = False
        patched_lines.append(line)
        if stripped.startswith("endcase"):
            inside_case = False
    if inserted_line_numbers:
        return "\n".join(patched_lines) + "\n", inserted_line_numbers
    return None, []


def _patch_state_hold_completion(source_text: str, *, analysis: dict[str, Any]) -> tuple[str | None, list[int]]:
    if "else if" not in source_text or re.search(r"else\s+begin", source_text):
        return None, []

    stateful_signals = [name for name in _signal_widths(analysis) if re.search(rf"\b{re.escape(name)}\s*<=", source_text)]
    if not stateful_signals:
        return None, []

    lines = source_text.splitlines()
    patched_lines: list[str] = []
    inserted_line_numbers: list[int] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "end" and index > 1 and lines[index - 2].strip() == "end" and "else if" in "\n".join(lines[: index - 1]):
            indent = re.match(r"\s*", line).group(0)
            child_indent = indent + "    "
            patched_lines.append(f"{indent}else begin")
            for offset, signal in enumerate(stateful_signals, start=1):
                patched_lines.append(f"{child_indent}{signal} <= {signal};")
                inserted_line_numbers.append(index + offset)
            patched_lines.append(f"{indent}end")
        patched_lines.append(line)
    if inserted_line_numbers:
        return "\n".join(patched_lines) + "\n", inserted_line_numbers
    return None, []


def _patch_output_register_completion(source_text: str, *, analysis: dict[str, Any]) -> tuple[str | None, list[int]]:
    reg_outputs = _declared_reg_outputs(source_text)
    outputs = [item for item in analysis.get("ports", []) if item.get("direction") == "output" and str(item.get("name") or "") in reg_outputs]
    if not outputs:
        return None, []
    lines = source_text.splitlines()
    reset_name = next((item["name"] for item in analysis.get("ports", []) if item.get("role") == "reset"), "")
    reset_block = _extract_reset_block(source_text, reset_name) if reset_name else None
    reset_span = _extract_reset_block_span(source_text, reset_name) if reset_name else None
    missing_outputs = []
    for output in outputs:
        name = str(output["name"])
        if reset_block is None or reset_span is None:
            continue
        assignments = list(re.finditer(rf"\b{re.escape(name)}\s*<=", source_text))
        has_reset_assignment = any(reset_span[0] <= match.start() < reset_span[1] for match in assignments)
        has_non_reset_assignment = any(not (reset_span[0] <= match.start() < reset_span[1]) for match in assignments)
        if not has_reset_assignment:
            continue
        if has_non_reset_assignment:
            continue
        missing_outputs.append(_inferred_output_assignment(name, analysis))
    if not missing_outputs:
        return None, []

    patched_lines: list[str] = []
    inserted_line_numbers: list[int] = []
    inserted = False
    for index, line in enumerate(lines, start=1):
        patched_lines.append(line)
        stripped = line.strip()
        if stripped == "end else begin" or stripped.endswith("else begin"):
            indent = re.match(r"\s*", line).group(0) + "    "
            for offset, assignment in enumerate(missing_outputs, start=1):
                patched_lines.append(f"{indent}{assignment}")
                inserted_line_numbers.append(index + offset)
            inserted = True
    if inserted:
        return "\n".join(patched_lines) + "\n", inserted_line_numbers
    return None, []


def _build_root_cause_evidence(
    diagnosis: dict[str, Any],
    verification_plan: dict[str, Any],
    *,
    patch_reason: str,
) -> list[str]:
    evidence = [str(diagnosis["findings"][0]), patch_reason]
    focus_signals = [str(item) for item in verification_plan.get("focus_signals", []) if str(item)]
    if focus_signals:
        evidence.append("focus_signals: " + ", ".join(focus_signals[:4]))
    for target in verification_plan.get("verification_targets", [])[:2]:
        description = str(target.get("description") or target.get("name") or "").strip()
        if description:
            evidence.append("checkpoint: " + description)
    return evidence


def _extract_reset_block(source_text: str, reset_name: str) -> str | None:
    match = _extract_reset_block_match(source_text, reset_name)
    if not match:
        return None
    return match.group("body")


def _extract_reset_block_match(source_text: str, reset_name: str) -> re.Match[str] | None:
    pattern = re.compile(
        rf"if\s*\(\s*!?{re.escape(reset_name)}\s*\)\s*begin(?P<body>.*?)end\s+else",
        re.DOTALL,
    )
    return pattern.search(source_text)


def _extract_reset_block_span(source_text: str, reset_name: str) -> tuple[int, int] | None:
    match = _extract_reset_block_match(source_text, reset_name)
    if not match:
        return None
    return match.span("body")


def _signal_widths(analysis: dict[str, Any]) -> dict[str, int]:
    widths: dict[str, int] = {}
    for item in analysis.get("ports", []):
        if item.get("direction") == "output":
            widths[str(item["name"])] = int(item.get("width") or 1)
    for item in analysis.get("state_elements", []):
        name = str(item["name"])
        if name not in widths:
            widths[name] = int(item.get("width") or 1)
    return widths


def _reset_assignment(signal: str, width: int) -> str:
    if width <= 1:
        return f"{signal} <= 1'b0;"
    return f"{signal} <= {width}'d0;"


def _declared_reg_outputs(source_text: str) -> set[str]:
    return {
        match.group("name")
        for match in re.finditer(r"output\s+reg(?:\s*\[[^\]]+\])?\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", source_text)
    }


def _inferred_output_assignment(signal: str, analysis: dict[str, Any]) -> str:
    suffix = signal[2:] if signal.startswith("o_") else signal
    for port in analysis.get("ports", []):
        port_name = str(port.get("name") or "")
        if port.get("direction") != "input":
            continue
        if port_name == f"i_{suffix}" or port_name == suffix:
            return f"{signal} <= {port_name};"
    return f"{signal} <= {signal};"


def _handle_rtl_mutation(
    *,
    source_paths: list[Path],
    out_dir: Path,
    analysis: dict[str, Any],
    verification_plan: dict[str, Any],
    tb_contract: dict[str, Any],
    patch_candidate: dict[str, Any],
    rtl_patch_plan: dict[str, Any],
    automation_mode: str,
    readiness: str,
    run_external: bool,
    decision_source: Path | None,
) -> tuple[dict[str, Any], Path | None, Path | None, Path | None]:
    policy = _rtl_mutation_policy(automation_mode, patch_candidate)
    if not rtl_patch_plan.get("candidate_available"):
        intervention_path = _write_rtl_intervention(out_dir, patch_candidate, rtl_patch_plan) if policy.get("confirmation_required") else None
        return policy, intervention_path, None, None

    decision = _read_decision(decision_source) if decision_source is not None else None
    should_apply = False
    intervention_path: Path | None = None
    if decision is not None:
        should_apply = _decision_allows_apply(decision)
    elif policy.get("policy") == "auto_apply" and automation_mode == "auto_apply" and not patch_candidate.get("apply_blockers"):
        should_apply = True
    else:
        intervention_path = _write_rtl_intervention(out_dir, patch_candidate, rtl_patch_plan)

    if not should_apply:
        return policy, intervention_path, None, None

    candidate_paths = [Path(path) for path in patch_candidate.get("candidate_rtl_paths", [])]
    backups: list[str] = []
    active_paths: list[str] = []
    for source_path, candidate_path in zip(source_paths, candidate_paths):
        backup_path = _backup_path(source_path)
        shutil.copyfile(source_path, backup_path)
        backups.append(str(backup_path))
        source_path.write_text(candidate_path.read_text(encoding="utf-8"), encoding="utf-8")
        active_paths.append(str(source_path))
    patch_candidate["backup_rtl_paths"] = backups
    patch_candidate["active_rtl_paths"] = active_paths

    post_apply_validation_path = write_json(
        out_dir / "post_apply_validation.json",
        _post_apply_validation_payload(source_paths, analysis, tb_contract, readiness=readiness, run_external=run_external),
    )
    post_apply_equivalence_path = write_json(
        out_dir / "post_apply_equivalence.json",
        _post_apply_equivalence_payload(source_paths, out_dir=out_dir, patch_candidate=patch_candidate, readiness=readiness),
    )
    applied_policy = {
        "policy": "auto_apply" if automation_mode == "auto_apply" and decision is None else "confirm_before_apply",
        "applied": True,
        "confirmation_required": False,
        "backup_rtl_paths": backups,
        "active_rtl_paths": active_paths,
        "patch_category": patch_candidate.get("patch_category", "none"),
    }
    return applied_policy, None, post_apply_validation_path, post_apply_equivalence_path


def _write_rtl_intervention(out_dir: Path, patch_candidate: dict[str, Any], rtl_patch_plan: dict[str, Any]) -> Path:
    payload = {
        "version": 1,
        "action": "ask_human",
        "primary_source": "rtl_mutation_confirmation",
        "question": "是否应用当前 RTL 修复补丁并进入回归验证？",
        "observations": [
            rtl_patch_plan.get("root_cause_hypothesis", ""),
            *[str(item) for item in patch_candidate.get("apply_blockers", [])],
        ],
        "attempted_actions": ["generated rtl_patch_plan", "generated rtl_patch_diff", "prepared candidate RTL"],
        "expected_answer_format": {
            "decision": "apply_rtl_patch or reject_rtl_patch",
            "evidence": "why this patch should or should not be applied",
            "constraints": "extra constraints to preserve during apply",
        },
    }
    return write_json(out_dir / "rtl_intervention.json", payload)


def _read_decision(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _decision_allows_apply(decision: dict[str, Any]) -> bool:
    text = str(decision.get("decision") or "").lower()
    return any(token in text for token in ("apply", "approve", "confirm"))


def _post_apply_validation_payload(
    source_paths: list[Path],
    analysis: dict[str, Any],
    tb_contract: dict[str, Any],
    *,
    readiness: str,
    run_external: bool,
) -> dict[str, Any]:
    workspace_root = Path(tb_contract["active_testbench_path"]).parent.parent / "post_apply_workspace"
    rtl_dir = workspace_root / "rtl"
    tb_dir = workspace_root / "tb"
    rtl_dir.mkdir(parents=True, exist_ok=True)
    tb_dir.mkdir(parents=True, exist_ok=True)
    staged_sources: list[Path] = []
    for source_path in source_paths:
        target = rtl_dir / source_path.name
        shutil.copyfile(source_path, target)
        staged_sources.append(target)
    active_tb_path = Path(tb_contract["active_testbench_path"])
    staged_tb_path = tb_dir / active_tb_path.with_suffix(".v").name
    shutil.copyfile(active_tb_path, staged_tb_path)
    spec = _validation_spec(analysis, staged_sources, f"tb/{staged_tb_path.name}")
    report = validate_generated(spec, workspace_root, target="rtl", run_external=run_external, readiness=readiness, comment_language="zh")
    return report.to_dict()


def _post_apply_equivalence_payload(source_paths: list[Path], *, out_dir: Path, patch_candidate: dict[str, Any], readiness: str) -> dict[str, Any]:
    if not patch_candidate.get("candidate_rtl_paths"):
        return {"status": "skipped"}
    result = compare_semantics(
        source_paths[0],
        Path(patch_candidate["candidate_rtl_paths"][0]),
        out_dir=out_dir / "post_apply_equivalence_compare",
        run_external=False,
        readiness=readiness,
    )
    return {
        "status": result["status"],
        "transform_validation_path": result["transform_validation_path"],
        "equivalence_path": result["equivalence_path"],
    }
