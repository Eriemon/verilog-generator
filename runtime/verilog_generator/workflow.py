"""End-to-end workflow runner for staged prompt generation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .extractor import ExtractionError, extract_response
from .interface_contract import audit_interface
from .model_provider import (
    GenerationContext,
    ManualResponseRequired,
    ModelProviderError,
    build_model_provider,
)
from .optimizer import build_prompt_memory
from .planning import decompose_spec
from .prompt import _manifest_for, _stage_manifest_for, render_prompt
from .requirements import (
    build_codegen_plan,
    build_requirements_payload,
    validate_codegen_plan_payload,
    validate_requirement_confirmation,
)
from .reference_contract import audit_reference
from .reflection import build_diagnosis, build_intervention, build_repair_plan, generate_repair_prompt
from .spec import SpecError, read_spec, write_spec
from .trace import append_trace_event, read_trace, safe_path, spec_summary
from .validation import validate_generated
from .vectors import audit_vectors
from .verifier import verify_stage
from .workspace import require_workspace_path, require_workspace_path_from, require_write_path, update_workflow_state, write_json, write_text

WORKFLOW_STATUSES = (
    "passed",
    "failed",
    "blocked_human",
    "blocked_toolchain",
    "max_attempts",
    "invalid_response",
)
DEFAULT_STAGES = {
    "rtl": ["requirements", "codegen_plan", "python", "rtl"],
}
FINAL_STAGE = {"rtl": "rtl"}


class WorkflowError(ValueError):
    """Raised when workflow configuration or resume state is invalid."""


def run_workflow(
    *,
    spec_path: Path | None = None,
    target: str | None = None,
    out_dir: Path | None = None,
    resume_dir: Path | None = None,
    decision_path: Path | None = None,
    evidence_path: Path | None = None,
    provider_name: str = "manual",
    provider_command: str | None = None,
    readiness: str = "static",
    max_attempts: int = 3,
    stop_on_human: bool = True,
    run_external: bool = True,
    comment_language: str = "zh",
    model_timeout_s: int = 120,
    state_updates: bool = True,
) -> dict[str, Any]:
    """Execute or resume a staged Spec2RTL workflow."""

    if resume_dir is not None:
        return _resume_workflow(
            resume_dir=resume_dir,
            decision_path=decision_path,
            stop_on_human=stop_on_human,
            run_external=run_external,
            comment_language=comment_language,
            model_timeout_s=model_timeout_s,
            state_updates=state_updates,
        )
    if spec_path is None or out_dir is None:
        raise WorkflowError("New workflow runs require both spec_path and out_dir.")

    spec_file = require_workspace_path(spec_path, purpose="spec path", must_exist=True)
    run_dir = require_write_path(out_dir, purpose="workflow output directory")
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.jsonl"
    state_path = run_dir / "workflow-state.json"
    result_path = run_dir / "workflow_result.json"
    config_path = run_dir / "workflow_config.json"
    plan_path = run_dir / "plan.json"

    raw_spec = read_spec(spec_file, target=target)
    validate_requirement_confirmation(raw_spec)
    external_codegen_plan = _resolve_external_codegen_plan(raw_spec, spec_file)
    evidence = _read_json(evidence_path) if evidence_path else None
    plan = decompose_spec(raw_spec, target=target, evidence=evidence)
    write_spec(plan_path, plan)

    config = _workflow_config(
        plan,
        provider_name=provider_name,
        provider_command=provider_command,
        readiness=readiness,
        max_attempts=max_attempts,
        stop_on_human=stop_on_human,
        run_external=run_external,
        comment_language=comment_language,
        external_codegen_plan=external_codegen_plan,
        model_timeout_s=model_timeout_s,
    )
    write_json(config_path, config)

    result = {
        "version": 1,
        "name": plan["name"],
        "target": plan["target"],
        "status": "failed",
        "plan_path": "plan.json",
        "workflow_config": "workflow_config.json",
        "trace_path": "trace.jsonl",
        "attempts": [],
    }
    _write_result(result_path, result)
    _record_state(
        state_path,
        "run_workflow",
        {"out_dir": run_dir, "target": plan["target"], "name": plan["name"]},
        enabled=state_updates,
    )
    return _execute_workflow(
        run_dir=run_dir,
        plan=plan,
        config=config,
        result=result,
        result_path=result_path,
        trace_path=trace_path,
        state_path=state_path,
        decision=_read_json(decision_path) if decision_path else None,
        state_updates=state_updates,
    )


def _resume_workflow(
    *,
    resume_dir: Path,
    decision_path: Path | None,
    stop_on_human: bool,
    run_external: bool,
    comment_language: str,
    model_timeout_s: int,
    state_updates: bool,
) -> dict[str, Any]:
    run_dir = require_workspace_path(resume_dir, purpose="workflow resume directory", must_exist=True)
    config_path = require_workspace_path(run_dir / "workflow_config.json", purpose="workflow config", must_exist=True)
    result_path = require_workspace_path(run_dir / "workflow_result.json", purpose="workflow result", must_exist=True)
    plan_path = require_workspace_path(run_dir / "plan.json", purpose="workflow plan", must_exist=True)
    trace_path = require_write_path(run_dir / "trace.jsonl", purpose="workflow trace")
    state_path = require_write_path(run_dir / "workflow-state.json", purpose="workflow state")

    config = _read_json(config_path)
    result = _read_json(result_path)
    plan = read_spec(plan_path, target=str(config.get("target") or None) or None)
    decision = _read_json(decision_path) if decision_path else None

    if result.get("status") == "blocked_human" and decision is None:
        raise WorkflowError("Resuming a blocked_human workflow requires a decision JSON file.")

    config["stop_on_human"] = stop_on_human
    config["run_external"] = run_external
    config["comment_language"] = comment_language or config.get("comment_language", "zh")
    config["model_timeout_s"] = model_timeout_s or int(config.get("model_timeout_s", 120))
    write_json(config_path, config)
    _record_state(
        state_path,
        "resume_workflow",
        {"resume_dir": run_dir, "decision": decision_path},
        enabled=state_updates,
    )
    return _execute_workflow(
        run_dir=run_dir,
        plan=plan,
        config=config,
        result=result,
        result_path=result_path,
        trace_path=trace_path,
        state_path=state_path,
        decision=decision,
        state_updates=state_updates,
    )


def _execute_workflow(
    *,
    run_dir: Path,
    plan: dict[str, Any],
    config: dict[str, Any],
    result: dict[str, Any],
    result_path: Path,
    trace_path: Path,
    state_path: Path,
    decision: dict[str, Any] | None,
    state_updates: bool,
) -> dict[str, Any]:
    provider = build_model_provider(
        str(config["provider"]["name"]),
        command=config["provider"].get("command"),
        timeout_s=int(config.get("model_timeout_s", 120)),
        config=config,
    )
    stages = [str(item) for item in config.get("stages", []) or DEFAULT_STAGES[plan["target"]]]
    max_attempts = int(config.get("max_attempts", 3))

    while len(result.get("attempts", [])) < max_attempts:
        attempt_number = len(result.get("attempts", [])) + 1
        attempt_id = f"attempt-{attempt_number:03d}"
        attempt_dir = require_write_path(run_dir / attempt_id, purpose="attempt directory")
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_record = _new_attempt_record(attempt_id, FINAL_STAGE[plan["target"]], provider.name)
        result.setdefault("attempts", []).append(attempt_record)
        _write_result(result_path, result)

        if len(result["attempts"]) > 1 and trace_path.exists():
            memory = build_prompt_memory(trace_path, plan)
            memory_path = attempt_dir / "prompt_memory.json"
            write_json(memory_path, memory)
            attempt_record["memory_path"] = safe_path(memory_path)
        else:
            memory = None
            memory_path = None

        stage_outputs: dict[str, dict[str, Any]] = {}
        active_codegen_plan: dict[str, Any] | None = None
        try:
            for stage in stages:
                stage_output = _run_generation_stage(
                    run_dir=run_dir,
                    attempt_dir=attempt_dir,
                    attempt_id=attempt_id,
                    plan=plan,
                    stage=stage,
                    provider=provider,
                    config=config,
                    memory=memory if stage in {"python", "rtl"} else None,
                    decision=decision,
                    previous_stage=stage_outputs.get(_previous_stage(stage, stages)),
                    active_codegen_plan=active_codegen_plan,
                    trace_path=trace_path,
                    state_path=state_path,
                    state_updates=state_updates,
                )
                stage_outputs[stage] = stage_output
                attempt_record.setdefault("stage_outputs", {})[stage] = stage_output["summary"]
                if stage == "codegen_plan":
                    plan["codegen_plan_path"] = stage_output["summary"]["artifact_path"]
                    codegen_plan = stage_output.get("codegen_plan")
                    if codegen_plan:
                        active_codegen_plan = codegen_plan
                        if not codegen_plan.get("ready_for_generation", False) or codegen_plan.get("open_questions"):
                            intervention_path = attempt_dir / "intervention.json"
                            intervention = {
                                "version": 1,
                                "action": "ask_human",
                                "primary_source": "needs_human_intervention",
                                "question": str((codegen_plan.get("open_questions") or ["Confirm the remaining design requirements."])[0]),
                                "observations": codegen_plan.get("open_questions", []),
                                "attempted_actions": ["requirements normalization", "code generation planning"],
                                "expected_answer_format": {
                                    "decision": "one concise design decision",
                                    "evidence": "requirement source or design rationale",
                                    "constraints": "any interface or pipeline constraints to preserve",
                                },
                            }
                            write_json(intervention_path, intervention)
                            attempt_record["intervention_path"] = safe_path(intervention_path)
                            attempt_record["status"] = "blocked_human"
                            result["status"] = "blocked_human"
                            _write_result(result_path, result)
                            _record_state(
                                state_path,
                                "human_intervention",
                                {"output": intervention_path, "attempt_id": attempt_id, "primary_source": "needs_human_intervention"},
                                enabled=state_updates,
                            )
                            append_trace_event(
                                trace_path,
                                {
                                    "event": "human_intervention",
                                    "attempt_id": attempt_id,
                                    "output": intervention_path,
                                    "primary_source": "needs_human_intervention",
                                    "provider": provider.name,
                                },
                            )
                            return result
                if stage == FINAL_STAGE[plan["target"]]:
                    attempt_record["prompt_path"] = stage_output["summary"]["prompt_path"]
                    attempt_record["response_path"] = stage_output["summary"]["response_path"]
                    attempt_record["artifact_dir"] = stage_output["summary"]["artifact_dir"]
                    attempt_record["stage"] = stage
                    result["last_attempt_id"] = attempt_id
                    _write_result(result_path, result)
        except ManualResponseRequired as exc:
            attempt_record["status"] = "invalid_response"
            attempt_record["error"] = str(exc)
            result["status"] = "invalid_response"
            _write_result(result_path, result)
            return result
        except (ExtractionError, ModelProviderError, SpecError, ValueError) as exc:
            attempt_record["status"] = "invalid_response" if isinstance(exc, ExtractionError) else "failed"
            attempt_record["error"] = str(exc)
            result["status"] = attempt_record["status"]
            _write_result(result_path, result)
            return result

        final_stage = FINAL_STAGE[plan["target"]]
        final_output = stage_outputs[final_stage]

        validation_report = validate_generated(
            plan,
            final_output["artifact_dir"],
            target=plan["target"],
            run_external=bool(config.get("run_external", True)),
            readiness=str(config.get("readiness", "execute")),
            comment_language=str(config.get("comment_language", "zh")),
            reference_contract=stage_outputs.get("python", {}).get("reference_contract"),
        )
        validation_json_path = attempt_dir / "validation.json"
        write_json(validation_json_path, validation_report.to_dict())
        attempt_record["validation_json"] = safe_path(validation_json_path)
        _record_state(
            state_path,
            "validate",
            {
                "path": final_output["artifact_dir"],
                "output": validation_json_path,
                "readiness": config.get("readiness"),
                "ok": validation_report.ok(),
            },
            enabled=state_updates,
        )
        error_sources = sorted(
            {
                issue.source
                for issue in validation_report.issues
                if issue.severity in {"error", "warning", "skip"}
            }
        )
        append_trace_event(
            trace_path,
            {
                "event": "validate",
                "attempt_id": attempt_id,
                "target": plan["target"],
                "readiness": config.get("readiness"),
                "path": final_output["artifact_dir"],
                "ok": validation_report.ok(),
                "errors": validation_report.errors,
                "warnings": validation_report.warnings,
                "skips": validation_report.skips,
                "error_sources": error_sources,
                "report_json": validation_json_path,
                "metrics": validation_report.metrics or {},
                "issues": [issue.to_dict() for issue in validation_report.issues],
                "comment_language": config.get("comment_language"),
                "provider": provider.name,
                "semantic_ready": (validation_report.metrics or {}).get("semantic_execution", {}).get("semantic_ready")
                if isinstance((validation_report.metrics or {}).get("semantic_execution"), dict)
                else None,
            },
        )

        contract_paths = dict(final_output["contract_paths"])
        interface_gate = _interface_gate(plan, stage_outputs, final_output, attempt_dir, trace_path)
        if interface_gate is not None:
            contract_paths["interface_gate"] = safe_path(interface_gate["path"])
        semantic_gate = _semantic_gate(plan, validation_report, stage_outputs, attempt_dir, trace_path)
        if semantic_gate is not None:
            contract_paths["semantic_gate"] = safe_path(semantic_gate["path"])
        combined_gate = _combine_gate_results(interface_gate["result"] if interface_gate else None, semantic_gate["result"] if semantic_gate else None)
        effective_gate = combined_gate
        if any(issue.source == "spec_issue" for issue in validation_report.issues):
            effective_gate = None
        stage_verification_path = None
        if combined_gate:
            stage_verification_path = attempt_dir / "stage_verification.json"
            write_json(stage_verification_path, combined_gate)
            contract_paths["stage_verification"] = safe_path(stage_verification_path)
            _record_state(
                state_path,
                "verify_stage",
                {"output": stage_verification_path, "ready": combined_gate.get("ready")},
                enabled=state_updates,
            )

        attempt_record["contract_paths"] = contract_paths
        if validation_report.ok() and (combined_gate is None or combined_gate.get("ready", True)):
            attempt_record["status"] = "passed"
            result["status"] = "passed"
            result["last_attempt_id"] = attempt_id
            _write_result(result_path, result)
            _record_state(
                state_path,
                "workflow_attempt",
                {"attempt_id": attempt_id, "status": "passed", "validation_json": validation_json_path},
                enabled=state_updates,
            )
            return result

        report_text = validation_report.format()
        repair_prompt_path = attempt_dir / "repair_prompt.md"
        repair_plan_path = attempt_dir / "repair_plan.json"
        diagnosis_path = attempt_dir / "diagnosis.json"
        repair_prompt = generate_repair_prompt(
            report_text,
            plan,
            read_trace(trace_path),
            validation_report.to_dict(),
            None,
            effective_gate,
        )
        write_text(repair_prompt_path, repair_prompt)
        repair_plan = build_repair_plan(
            report_text,
            plan,
            read_trace(trace_path),
            validation_report.to_dict(),
            None,
            effective_gate,
        )
        diagnosis = build_diagnosis(plan, read_trace(trace_path), validation_report.to_dict(), effective_gate)
        write_json(repair_plan_path, repair_plan)
        write_json(diagnosis_path, diagnosis)
        attempt_record["repair_plan"] = safe_path(repair_plan_path)
        attempt_record["diagnosis_path"] = safe_path(diagnosis_path)
        _record_state(
            state_path,
            "reflect",
            {"output": repair_prompt_path, "repair_plan": repair_plan_path, "diagnosis": diagnosis_path},
            enabled=state_updates,
        )
        append_trace_event(
            trace_path,
            {
                "event": "reflect",
                "attempt_id": attempt_id,
                "output": repair_prompt_path,
                "repair_plan": repair_plan_path,
                "error_sources": repair_plan.get("error_sources", []),
                "action": repair_plan.get("action"),
                "diagnosis": diagnosis,
                "auto_debug_before_human": diagnosis.get("auto_debug_before_human"),
            },
        )

        if repair_plan.get("action") == "ask_human" and bool(config.get("stop_on_human", True)):
            intervention_path = attempt_dir / "intervention.json"
            write_json(intervention_path, build_intervention(repair_plan, report_text, validation_report.to_dict()))
            attempt_record["intervention_path"] = safe_path(intervention_path)
            attempt_record["status"] = "blocked_human"
            result["status"] = "blocked_human"
            _write_result(result_path, result)
            _record_state(
                state_path,
                "human_intervention",
                {"output": intervention_path, "attempt_id": attempt_id, "primary_source": repair_plan.get("primary_source")},
                enabled=state_updates,
            )
            append_trace_event(
                trace_path,
                {
                    "event": "human_intervention",
                    "attempt_id": attempt_id,
                    "output": intervention_path,
                    "primary_source": repair_plan.get("primary_source"),
                    "provider": provider.name,
                },
            )
            return result

        if repair_plan.get("primary_source") == "toolchain_issue":
            attempt_record["status"] = "blocked_toolchain"
            result["status"] = "blocked_toolchain"
            _write_result(result_path, result)
            return result

        if len(result.get("attempts", [])) >= max_attempts:
            attempt_record["status"] = "max_attempts"
            result["status"] = "max_attempts"
            _write_result(result_path, result)
            return result

        attempt_record["status"] = "failed"
        result["status"] = "failed"
        _write_result(result_path, result)

    result["status"] = "max_attempts"
    _write_result(result_path, result)
    return result


def _run_generation_stage(
    *,
    run_dir: Path,
    attempt_dir: Path,
    attempt_id: str,
    plan: dict[str, Any],
    stage: str,
    provider: Any,
    config: dict[str, Any],
    memory: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    previous_stage: dict[str, Any] | None,
    active_codegen_plan: dict[str, Any] | None,
    trace_path: Path,
    state_path: Path,
    state_updates: bool,
) -> dict[str, Any]:
    stage_dir = require_write_path(attempt_dir / stage, purpose="stage directory")
    stage_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = stage_dir / f"{stage}_prompt.md"
    response_path = stage_dir / f"{stage}_response.md"
    artifact_dir = require_write_path(stage_dir / "generated", purpose="artifact directory")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = _stage_manifest(plan, stage)
    if stage == "requirements":
        return _run_internal_json_stage(
            attempt_id=attempt_id,
            plan=plan,
            stage=stage,
            manifest=manifest,
            stage_dir=stage_dir,
            artifact_dir=artifact_dir,
            trace_path=trace_path,
            state_path=state_path,
            state_updates=state_updates,
            payload=build_requirements_payload(plan),
        )
    if stage == "codegen_plan":
        payload = config.get("external_codegen_plan") or build_codegen_plan(plan)
        return _run_internal_json_stage(
            attempt_id=attempt_id,
            plan=plan,
            stage=stage,
            manifest=manifest,
            stage_dir=stage_dir,
            artifact_dir=artifact_dir,
            trace_path=trace_path,
            state_path=state_path,
            state_updates=state_updates,
            payload=payload,
            payload_key="codegen_plan",
        )
    context_manifest = previous_stage.get("manifest") if previous_stage else None
    context_dir = previous_stage.get("artifact_dir") if previous_stage else None
    vector_contract = previous_stage.get("vector_contract") if previous_stage else None
    prompt_text = render_prompt(
        plan,
        target=plan["target"],
        stage=stage,
        context_manifest=context_manifest,
        context_dir=context_dir,
        memory=memory,
        comment_language=str(config.get("comment_language", "zh")),
        vector_contract=vector_contract,
        codegen_plan=active_codegen_plan,
        budget=_stage_budget(config, stage),
        decision=decision,
    )
    write_text(prompt_path, prompt_text)
    prompt_stats = _prompt_stats(
        prompt_text,
        stage=stage,
        budget=_stage_budget(config, stage),
        subfunction=None,
        context_manifest=context_manifest,
        context_dir=context_dir,
        vector_contract=vector_contract,
        decision=decision,
    )
    _record_state(
        state_path,
        "prompt",
        {"output": prompt_path, "stage": stage, "budget": _stage_budget(config, stage)},
        enabled=state_updates,
    )
    append_trace_event(
        trace_path,
        {
            "event": "prompt",
            "attempt_id": attempt_id,
            "target": plan["target"],
            "stage": stage,
            "spec": spec_summary(plan),
            "output": prompt_path,
            "context_manifest": previous_stage.get("manifest_path") if previous_stage else None,
            "context_dir": context_dir,
            "memory": previous_stage.get("memory_path") if previous_stage else None,
            "comment_language": config.get("comment_language"),
            "vector_contract": safe_path(previous_stage["vector_contract_path"]) if previous_stage and previous_stage.get("vector_contract_path") else None,
            "decision": decision is not None,
            "subfunction": None,
            "budget": _stage_budget(config, stage),
            "prompt_stats": prompt_stats,
            "provider": provider.name,
        },
    )

    response_text = provider.generate(
        prompt_text,
        GenerationContext(
            attempt_id=attempt_id,
            stage=stage,
            prompt_path=prompt_path,
            response_path=response_path,
            run_dir=run_dir,
            attempt_dir=attempt_dir,
            spec=plan,
            manifest=manifest,
            workflow_config=config,
            vector_contract=vector_contract,
            comment_language=str(config.get("comment_language", "zh")),
        ),
    )
    write_text(response_path, response_text)
    _record_state(
        state_path,
        "model_generate",
        {"output": response_path, "provider": provider.name, "stage": stage},
        enabled=state_updates,
    )
    append_trace_event(
        trace_path,
        {
            "event": "model_generate",
            "attempt_id": attempt_id,
            "stage": stage,
            "provider": provider.name,
            "prompt_path": prompt_path,
            "response_path": response_path,
        },
    )

    written = extract_response(response_text, artifact_dir)
    _record_state(
        state_path,
        "extract",
        {"response": response_path, "out_dir": artifact_dir, "written_files": written},
        enabled=state_updates,
    )
    append_trace_event(
        trace_path,
        {
            "event": "extract",
            "attempt_id": attempt_id,
            "response": response_path,
            "out_dir": artifact_dir,
            "written_files": [safe_path(path) for path in written],
        },
    )

    output = {
        "stage": stage,
        "prompt_path": prompt_path,
        "response_path": response_path,
        "artifact_dir": artifact_dir,
        "manifest": manifest,
        "manifest_path": response_path,
        "contract_paths": {},
        "summary": {
            "prompt_path": safe_path(prompt_path),
            "response_path": safe_path(response_path),
            "artifact_dir": safe_path(artifact_dir),
        },
    }
    if stage == "python":
        reference_contract = audit_reference(artifact_dir)
        reference_contract_path = stage_dir / "reference_contract.json"
        write_json(reference_contract_path, reference_contract)
        python_contract = audit_interface("python", artifact_dir)
        python_contract_path = stage_dir / "python_interface.json"
        write_json(python_contract_path, python_contract)
        vector_path = next(
            (path for path in written if path.name.endswith("_vectors.json")),
            None,
        )
        vector_contract = audit_vectors(vector_path) if vector_path is not None else None
        vector_contract_path = stage_dir / "vector_contract.json" if vector_contract is not None else None
        if vector_contract_path is not None:
            write_json(vector_contract_path, vector_contract)
        output["reference_contract"] = reference_contract
        output["python_contract"] = python_contract
        output["vector_contract"] = vector_contract
        output["vector_contract_path"] = vector_contract_path
        output["contract_paths"].update(
            {
                "reference_contract": safe_path(reference_contract_path),
                "python_interface": safe_path(python_contract_path),
            }
        )
        if vector_contract_path is not None:
            output["contract_paths"]["vector_contract"] = safe_path(vector_contract_path)
        _record_state(
            state_path,
            "audit_reference",
            {"path": artifact_dir, "output": reference_contract_path, "case_count": reference_contract.get("case_count")},
            enabled=state_updates,
        )
        _record_state(
            state_path,
            "audit_interface",
            {"target": "python", "path": artifact_dir, "output": python_contract_path},
            enabled=state_updates,
        )
        append_trace_event(
            trace_path,
            {
                "event": "audit_reference",
                "attempt_id": attempt_id,
                "path": artifact_dir,
                "output": reference_contract_path,
                "case_count": reference_contract.get("case_count"),
                "case_ids": reference_contract.get("case_ids", []),
                "sha256": reference_contract.get("sha256"),
            },
        )
        append_trace_event(
            trace_path,
            {
                "event": "audit_interface",
                "attempt_id": attempt_id,
                "target": "python",
                "path": artifact_dir,
                "output": python_contract_path,
                "interface_sha256": python_contract.get("interface_sha256"),
                "top": python_contract.get("top"),
                "case_ids": python_contract.get("case_ids", []),
                "vector_hashes": python_contract.get("vector_hashes", []),
            },
        )
    elif stage == "rtl":
        interface_contract = audit_interface(stage, artifact_dir)
        interface_contract_path = stage_dir / f"{stage}_interface.json"
        write_json(interface_contract_path, interface_contract)
        output["interface_contract"] = interface_contract
        output["contract_paths"][f"{stage}_interface"] = safe_path(interface_contract_path)
        _record_state(
            state_path,
            "audit_interface",
            {"target": stage, "path": artifact_dir, "output": interface_contract_path},
            enabled=state_updates,
        )
        append_trace_event(
            trace_path,
            {
                "event": "audit_interface",
                "attempt_id": attempt_id,
                "target": stage,
                "path": artifact_dir,
                "output": interface_contract_path,
                "interface_sha256": interface_contract.get("interface_sha256"),
                "top": interface_contract.get("top"),
                "case_ids": interface_contract.get("case_ids", []),
                "vector_hashes": interface_contract.get("vector_hashes", []),
            },
        )
    return output


def _interface_gate(
    plan: dict[str, Any],
    stage_outputs: dict[str, dict[str, Any]],
    final_output: dict[str, Any],
    attempt_dir: Path,
    trace_path: Path,
) -> dict[str, Any] | None:
    python_contract = stage_outputs.get("python", {}).get("python_contract")
    interface_contract = final_output.get("interface_contract")
    if not python_contract or not interface_contract:
        return None
    result = verify_stage(plan, python_contract, interface_contract)
    path = attempt_dir / "interface_gate.json"
    write_json(path, result)
    append_trace_event(
        trace_path,
        {
            "event": "verify_stage",
            "attempt_id": attempt_dir.name,
            "from_contract": stage_outputs.get("python", {}).get("contract_paths", {}).get("python_interface"),
            "to_contract": final_output.get("contract_paths", {}),
            "output": path,
            "ready": result.get("ready"),
            "error_sources": result.get("error_sources", []),
            "recommended_action": result.get("recommended_action"),
            "issues": result.get("issues", []),
            "semantic_ready": result.get("semantic_ready"),
            "mismatched_cases": result.get("mismatched_cases", []),
            "checkpoint_drift": result.get("checkpoint_drift", []),
            "localization_confidence": result.get("localization_confidence"),
        },
    )
    return {"path": path, "result": result}


def _semantic_gate(
    plan: dict[str, Any],
    validation_report: Any,
    stage_outputs: dict[str, dict[str, Any]],
    attempt_dir: Path,
    trace_path: Path,
) -> dict[str, Any] | None:
    reference_contract = stage_outputs.get("python", {}).get("reference_contract")
    if not reference_contract or not validation_report.metrics:
        return None
    result = verify_stage(
        plan,
        reference_contract,
        {
            "metrics": validation_report.metrics,
            "case_ids": reference_contract.get("case_ids", []),
        },
    )
    path = attempt_dir / "semantic_gate.json"
    write_json(path, result)
    append_trace_event(
        trace_path,
        {
            "event": "verify_stage",
            "attempt_id": attempt_dir.name,
            "from_contract": stage_outputs.get("python", {}).get("contract_paths", {}).get("reference_contract"),
            "to_contract": path,
            "output": path,
            "ready": result.get("ready"),
            "error_sources": result.get("error_sources", []),
            "recommended_action": result.get("recommended_action"),
            "issues": result.get("issues", []),
            "semantic_ready": result.get("semantic_ready"),
            "mismatched_cases": result.get("mismatched_cases", []),
            "checkpoint_drift": result.get("checkpoint_drift", []),
            "localization_confidence": result.get("localization_confidence"),
        },
    )
    return {"path": path, "result": result}


def _combine_gate_results(
    interface_gate: dict[str, Any] | None,
    semantic_gate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if interface_gate is None and semantic_gate is None:
        return None
    issues: list[dict[str, Any]] = []
    error_sources: list[str] = []
    recommended_action = None
    ready = True
    for gate in [interface_gate, semantic_gate]:
        if not gate:
            continue
        for issue in gate.get("issues", []) or []:
            if issue not in issues:
                issues.append(issue)
        for source in gate.get("error_sources", []) or []:
            if source not in error_sources:
                error_sources.append(source)
        if gate.get("ready") is False:
            ready = False
            if recommended_action is None:
                recommended_action = gate.get("recommended_action")
    return {
        "version": 1,
        "ready": ready,
        "issues": issues,
        "error_sources": error_sources,
        "recommended_action": recommended_action or "regenerate_current",
        "semantic_ready": semantic_gate.get("semantic_ready") if semantic_gate else interface_gate.get("semantic_ready") if interface_gate else None,
        "mismatched_cases": semantic_gate.get("mismatched_cases", []) if semantic_gate else [],
        "checkpoint_drift": semantic_gate.get("checkpoint_drift", []) if semantic_gate else [],
        "failed_cases": semantic_gate.get("failed_cases", []) if semantic_gate else [],
        "localization_confidence": semantic_gate.get("localization_confidence") if semantic_gate else None,
    }


def _run_internal_json_stage(
    *,
    attempt_id: str,
    plan: dict[str, Any],
    stage: str,
    manifest: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    trace_path: Path,
    state_path: Path,
    state_updates: bool,
    payload: dict[str, Any],
    payload_key: str | None = None,
) -> dict[str, Any]:
    prompt_path = stage_dir / f"{stage}_prompt.md"
    response_path = stage_dir / f"{stage}_response.md"
    write_text(prompt_path, f"# Internal {stage} stage\n\nThis stage is synthesized from confirmed inputs and local planning rules.\n")
    files = [entry for entry in manifest.get("files", []) if isinstance(entry, dict) and entry.get("path")]
    if len(files) != 1:
        raise WorkflowError(f"Internal stage {stage!r} expects exactly one manifest file.")
    artifact_rel_path = str(files[0]["path"])
    artifact_path = artifact_dir / Path(*Path(artifact_rel_path).parts)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(artifact_path, payload)
    response_text = _internal_stage_response_text(manifest, artifact_rel_path, payload)
    write_text(response_path, response_text)
    _record_state(
        state_path,
        "prompt",
        {"output": prompt_path, "stage": stage, "budget": "internal"},
        enabled=state_updates,
    )
    _record_state(
        state_path,
        "extract",
        {"response": response_path, "out_dir": artifact_dir, "written_files": [artifact_path]},
        enabled=state_updates,
    )
    append_trace_event(
        trace_path,
        {
            "event": "prompt",
            "attempt_id": attempt_id,
            "target": plan["target"],
            "stage": stage,
            "spec": spec_summary(plan),
            "output": prompt_path,
            "budget": "internal",
            "provider": "internal",
        },
    )
    append_trace_event(
        trace_path,
        {
            "event": "extract",
            "attempt_id": attempt_id,
            "response": response_path,
            "out_dir": artifact_dir,
            "written_files": [safe_path(artifact_path)],
        },
    )
    output = {
        "stage": stage,
        "prompt_path": prompt_path,
        "response_path": response_path,
        "artifact_dir": artifact_dir,
        "manifest": manifest,
        "manifest_path": response_path,
        "contract_paths": {},
        "summary": {
            "prompt_path": safe_path(prompt_path),
            "response_path": safe_path(response_path),
            "artifact_dir": safe_path(artifact_dir),
            "artifact_path": safe_path(artifact_path),
        },
    }
    if payload_key:
        output[payload_key] = payload
    else:
        output[stage] = payload
    return output


def _internal_stage_response_text(manifest: dict[str, Any], artifact_rel_path: str, payload: dict[str, Any]) -> str:
    response_manifest = {
        **manifest,
        "checks": {
            "spec_coverage": [f"Internal {manifest.get('stage')} stage synthesized from confirmed inputs."],
            "verification_plan": ["No model generation was used for this planning stage."],
            "execution_plan": ["This planning artifact is consumed by later generation stages."],
            "implementation_assessment": ["The internal planning payload was generated locally."],
            "reviewability_assessment": ["The planning payload is fully structured JSON."],
            "assumptions": [],
            "known_limitations": [],
        },
    }
    return (
        "```json\n"
        + json.dumps(response_manifest, indent=2, ensure_ascii=False)
        + "\n```\n"
        + f"```json path={artifact_rel_path}\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n```\n"
    )


def _workflow_config(
    plan: dict[str, Any],
    *,
    provider_name: str,
    provider_command: str | None,
    readiness: str,
    max_attempts: int,
    stop_on_human: bool,
    run_external: bool,
    comment_language: str,
    external_codegen_plan: dict[str, Any] | None,
    model_timeout_s: int,
) -> dict[str, Any]:
    provider_config = {
        "name": provider_name,
        "command": provider_command,
    }
    return {
        "version": 1,
        "name": plan["name"],
        "target": plan["target"],
        "rtl_dialect": plan.get("rtl_dialect"),
        "rtl_style_profile": plan.get("rtl_style_profile"),
        "design_requirements": copy.deepcopy(plan.get("design_requirements", {})) if isinstance(plan.get("design_requirements"), dict) else {},
        "streamability": plan.get("streamability"),
        "interface_family": plan.get("interface_family"),
        "interface_profile": copy.deepcopy(plan.get("interface_profile", {})) if isinstance(plan.get("interface_profile"), dict) else {},
        "pipeline_required": bool(plan.get("pipeline_required", True)),
        "codegen_plan_required": bool(plan.get("codegen_plan_required", True)),
        "codegen_plan_path": plan.get("codegen_plan_path"),
        "stages": list(DEFAULT_STAGES[plan["target"]]),
        "readiness": readiness,
        "max_attempts": max_attempts,
        "stop_on_human": stop_on_human,
        "run_external": run_external,
        "comment_language": comment_language,
        "external_codegen_plan": copy.deepcopy(external_codegen_plan) if isinstance(external_codegen_plan, dict) else None,
        "model_timeout_s": model_timeout_s,
        "provider": provider_config,
        "budgets": {stage: "normal" for stage in DEFAULT_STAGES[plan["target"]]},
        "mock_behavior": (plan.get("workflow") or {}).get("mock_behavior"),
    }


def _stage_manifest(plan: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage:
        return _stage_manifest_for(plan, stage)
    return _manifest_for(plan)


def _stage_budget(config: dict[str, Any], stage: str) -> str:
    budgets = config.get("budgets", {})
    if isinstance(budgets, dict) and stage in budgets:
        return str(budgets[stage])
    return "normal"


def _new_attempt_record(attempt_id: str, stage: str, provider: str) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "stage": stage,
        "prompt_path": None,
        "response_path": None,
        "artifact_dir": None,
        "validation_json": None,
        "contract_paths": {},
        "repair_plan": None,
        "status": "failed",
        "provider": provider,
    }


def _write_result(path: Path, result: dict[str, Any]) -> None:
    if result.get("status") not in WORKFLOW_STATUSES and result.get("attempts"):
        raise WorkflowError(f"Workflow status must be one of {', '.join(WORKFLOW_STATUSES)}.")
    write_json(path, result)


def _previous_stage(stage: str, stages: list[str]) -> str | None:
    try:
        index = stages.index(stage)
    except ValueError:
        return None
    if index <= 0:
        return None
    return stages[index - 1]


def _prompt_stats(
    output: str,
    *,
    stage: str,
    budget: str,
    subfunction: str | None,
    context_manifest: dict[str, Any] | None,
    context_dir: Path | None,
    vector_contract: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> dict[str, Any]:
    manifest_artifacts = len(context_manifest.get("files", []) if isinstance(context_manifest, dict) else [])
    context_artifacts = manifest_artifacts + (1 if context_dir else 0)
    return {
        "version": 1,
        "chars": len(output),
        "approx_tokens": max(1, len(output) // 4),
        "context_artifacts": context_artifacts,
        "has_vector_contract": bool(vector_contract),
        "has_decision": bool(decision),
        "budget": budget,
        "subfunction": subfunction,
        "stage": stage,
    }


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    json_path = require_workspace_path(path, purpose="JSON path", must_exist=True)
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Invalid JSON in {json_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError(f"Expected JSON object in {json_path}.")
    return data


def _record_state(state_path: Path, event: str, payload: dict[str, Any], *, enabled: bool) -> None:
    update_workflow_state(state_path, event, payload, enabled=enabled)


def _resolve_external_codegen_plan(spec: dict[str, Any], spec_file: Path) -> dict[str, Any] | None:
    raw_path = spec.get("codegen_plan_path")
    if not raw_path:
        return None
    plan_path = require_workspace_path_from(
        spec_file,
        Path(str(raw_path)),
        purpose="codegen plan path",
        must_exist=True,
    )
    payload = _read_json(plan_path)
    validate_codegen_plan_payload(spec, payload, require_ready=False)
    return payload

