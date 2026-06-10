"""Stable facade for Verilog-only generation workflows."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime.verilog_generator.config import load_settings, workflow_defaults
from runtime.verilog_generator.existing_rtl import analyze_existing_rtl, load_spec_text
from runtime.verilog_generator.existing_rtl_refinement import compare_semantics, refine_existing_rtl as refine_existing_rtl_runtime
from runtime.verilog_generator.prompt import render_prompt
from runtime.verilog_generator.requirements import (
    apply_requirement_defaults,
    build_codegen_plan,
    build_requirements_payload,
    validate_requirement_confirmation,
)
from runtime.verilog_generator.spec import normalize_spec, read_spec, write_spec
from runtime.verilog_generator.validation import readiness_at_least, validate_generated
from runtime.verilog_generator.verify_repair import verify_existing as verify_existing_runtime
from runtime.verilog_generator.workflow import run_workflow
from runtime.verilog_generator.workflow_router import route_verilog_entry
from runtime.verilog_generator.workspace import use_workspace_root

__all__ = [
    "run_verilog_workflow",
    "run_verilog_batch",
    "render_verilog_prompt",
    "validate_verilog_artifacts",
    "analyze_existing_verilog",
    "refine_existing_verilog",
    "compare_verilog_semantics",
    "verify_existing_verilog",
    "route_verilog_request",
    "load_default_workflow_config",
    "load_workflow_result",
]


def load_default_workflow_config() -> dict[str, Any]:
    """Return the recommended workflow defaults shipped with the skill."""

    return workflow_defaults(load_settings())


def load_workflow_result(run_dir: str | Path) -> dict[str, Any]:
    """Load `workflow_result.json` from a workflow run directory."""

    result_path = Path(run_dir) / "workflow_result.json"
    return json.loads(result_path.read_text(encoding="utf-8"))


def route_verilog_request(
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
    """Classify the safest Verilog workflow entry without executing it."""

    return route_verilog_entry(
        request_summary=request_summary,
        spec=spec,
        codegen_plan=codegen_plan,
        rtl=rtl,
        testbench=testbench,
        logs=logs,
        waveform=waveform,
        validation=validation,
        artifact_dir=artifact_dir,
        remote_validation_requested=remote_validation_requested,
    )


def analyze_existing_verilog(
    source: str | Path | list[str | Path],
    *,
    out_dir: str | Path,
    spec_source: str | Path | dict[str, Any] | None = None,
    module_name: str | None = None,
) -> dict[str, Any]:
    """Analyze existing Verilog RTL into a stable JSON contract."""

    run_dir = Path(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    sources = _resolve_sources(source)
    with use_workspace_root(run_dir):
        result = analyze_existing_rtl(
            sources,
            spec_text=load_spec_text(spec_source),
            module_name=module_name,
            out_dir=run_dir,
        )
    return {
        "status": "analyzed",
        "run_dir": str(run_dir),
        "analysis_path": str(result["analysis_path"]),
        "project_analysis_path": str(result["project_analysis_path"]),
        "design_explanation_path": str(result["design_explanation_path"]),
        "analysis": result["analysis"],
        "project_analysis": result["project_analysis"],
    }


def refine_existing_verilog(
    source: str | Path,
    *,
    out_dir: str | Path,
    refine_goal: str,
    analysis_source: str | Path | None = None,
    spec_source: str | Path | dict[str, Any] | None = None,
    candidate_artifacts_dir: str | Path | None = None,
    reference_artifacts_dir: str | Path | None = None,
    readiness: str = "static",
    tb_language: str = "verilog",
) -> dict[str, Any]:
    """Plan a controlled refinement flow for existing RTL."""

    run_dir = Path(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = Path(analysis_source) if analysis_source is not None else None
    with use_workspace_root(run_dir):
        return refine_existing_rtl_runtime(
            Path(source),
            out_dir=run_dir,
            refine_goal=refine_goal,
            analysis_source=analysis_path,
            spec_source=spec_source,
            candidate_artifacts_dir=Path(candidate_artifacts_dir) if candidate_artifacts_dir is not None else None,
            reference_artifacts_dir=Path(reference_artifacts_dir) if reference_artifacts_dir is not None else None,
            readiness=readiness,
            tb_language=tb_language,
        )


def compare_verilog_semantics(
    reference: str | Path,
    candidate: str | Path,
    *,
    out_dir: str | Path,
    run_external: bool = True,
    readiness: str = "static",
    external_target: str = "remote",
) -> dict[str, Any]:
    """Compare two RTL implementations for interface and checkpoint drift."""

    run_dir = Path(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    with use_workspace_root(run_dir):
        return compare_semantics(
            Path(reference),
            Path(candidate),
            out_dir=run_dir,
            run_external=_resolve_external_run(run_external, readiness=readiness, external_target=external_target, allow_static_external=True),
            readiness=readiness,
        )


def verify_existing_verilog(
    source: str | Path | list[str | Path],
    *,
    out_dir: str | Path,
    spec_source: str | Path | dict[str, Any] | None = None,
    module_name: str | None = None,
    testbench_source: str | Path | None = None,
    decision_source: str | Path | None = None,
    tb_mode: str = "generate",
    tb_language: str = "verilog",
    automation_mode: str,
    readiness: str = "static",
    run_external: bool = True,
    external_target: str = "remote",
) -> dict[str, Any]:
    """Run the existing RTL verify-repair workflow and emit stable artifacts."""

    run_dir = Path(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    sources = _resolve_sources(source)
    with use_workspace_root(run_dir):
        return verify_existing_runtime(
            sources,
            out_dir=run_dir,
            spec_source=spec_source,
            module_name=module_name,
            testbench_source=Path(testbench_source) if testbench_source is not None else None,
            decision_source=Path(decision_source) if decision_source is not None else None,
            tb_mode=tb_mode,
            tb_language=tb_language,
            automation_mode=automation_mode,
            readiness=readiness,
            run_external=_resolve_external_run(run_external, readiness=readiness, external_target=external_target, allow_static_external=True),
        )


def run_verilog_workflow(
    spec: str | Path | dict[str, Any] | None = None,
    *,
    out_dir: str | Path | None = None,
    resume_dir: str | Path | None = None,
    workflow_config: str | Path | dict[str, Any] | None = None,
    evidence: str | Path | dict[str, Any] | None = None,
    decision: str | Path | dict[str, Any] | None = None,
    provider_name: str | None = None,
    provider_command: str | None = None,
    generation_mode: str | None = None,
    stream: bool | None = None,
    target: str | None = None,
    design_requirements: str | Path | dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: str | Path | dict[str, Any] | None = None,
    readiness: str | None = None,
    max_attempts: int | None = None,
    stop_on_human: bool | None = None,
    run_external: bool | None = None,
    external_target: str = "remote",
    comment_language: str | None = None,
    model_timeout_s: int | None = None,
) -> dict[str, Any]:
    """Run or resume a Verilog workflow and return the parsed result payload."""

    defaults = load_default_workflow_config()
    overrides = _workflow_overrides(_load_optional_json(workflow_config) or {})
    merged = {**defaults, **overrides}
    resolved_target = _resolve_target(target, spec, merged)
    resolved_readiness = readiness or merged.get("readiness", "static")
    resolved_attempts = int(max_attempts or merged.get("max_attempts", 3))
    resolved_stop_on_human = bool(stop_on_human) if stop_on_human is not None else bool(merged.get("stop_on_human", True))
    requested_run_external = bool(run_external) if run_external is not None else bool(merged.get("run_external", True))
    resolved_run_external = _resolve_external_run(requested_run_external, readiness=resolved_readiness, external_target=external_target)
    resolved_comment_language = comment_language or str(merged.get("comment_language", "zh"))
    resolved_provider_name = provider_name or str(merged.get("model_provider", "command"))
    resolved_timeout = int(model_timeout_s or merged.get("model_timeout_s", 120))
    resolved_generation_mode = str(generation_mode or merged.get("generation_mode", "regular"))
    resolved_stream = bool(stream) if stream is not None else bool(merged.get("stream", False))

    if resume_dir is not None:
        run_dir = Path(resume_dir)
        decision_path = _materialize_optional_json(decision, run_dir / "_adapter_inputs" / "decision.json")
        with use_workspace_root(run_dir):
            result = run_workflow(
                resume_dir=run_dir,
                decision_path=decision_path,
                generation_mode=generation_mode,
                stream=stream,
                stop_on_human=resolved_stop_on_human,
                run_external=resolved_run_external,
                comment_language=resolved_comment_language,
                model_timeout_s=resolved_timeout,
            )
        return {
            "status": result["status"],
            "run_dir": str(run_dir),
            "result_path": str(run_dir / "workflow_result.json"),
            "workflow_result": result,
        }

    if spec is None or out_dir is None:
        raise ValueError("New workflow runs require both `spec` and `out_dir`.")

    run_dir = Path(out_dir)
    inputs_dir = run_dir / "_adapter_inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    prepared_spec = _prepare_facade_spec(
        spec,
        target=resolved_target,
        design_requirements=_load_optional_json(design_requirements),
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=_load_optional_json(interface_profile),
    )
    requirements_payload = build_requirements_payload(prepared_spec)
    requirements_path = _write_json_object(inputs_dir / "requirements.json", requirements_payload)
    codegen_plan = build_codegen_plan(prepared_spec)
    codegen_plan_path = _write_json_object(inputs_dir / "codegen_plan.json", codegen_plan)
    prepared_spec["codegen_plan_path"] = codegen_plan_path.relative_to(run_dir).as_posix()
    spec_path = _materialize_spec(prepared_spec, inputs_dir / "spec.json", target=resolved_target)
    evidence_path = _materialize_optional_json(evidence, inputs_dir / "evidence.json")
    decision_path = _materialize_optional_json(decision, inputs_dir / "decision.json")

    with use_workspace_root(run_dir):
        result = run_workflow(
            spec_path=spec_path,
            target=resolved_target,
            out_dir=run_dir,
            decision_path=decision_path,
            evidence_path=evidence_path,
            provider_name=resolved_provider_name,
            provider_command=provider_command,
            generation_mode=resolved_generation_mode,
            stream=resolved_stream,
            readiness=resolved_readiness,
            max_attempts=resolved_attempts,
            stop_on_human=resolved_stop_on_human,
            run_external=resolved_run_external,
            comment_language=resolved_comment_language,
            model_timeout_s=resolved_timeout,
        )
    return {
        "status": result["status"],
        "run_dir": str(run_dir),
        "result_path": str(run_dir / "workflow_result.json"),
        "requirements_path": str(requirements_path),
        "codegen_plan_path": str(codegen_plan_path),
        "workflow_result": result,
    }


def run_verilog_batch(
    specs: list[str | Path | dict[str, Any]],
    *,
    out_dir: str | Path,
    workflow_config: str | Path | dict[str, Any] | None = None,
    evidence: str | Path | dict[str, Any] | None = None,
    provider_name: str | None = None,
    provider_command: str | None = None,
    generation_mode: str | None = None,
    stream: bool | None = None,
    target: str | None = None,
    design_requirements: str | Path | dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: str | Path | dict[str, Any] | None = None,
    readiness: str | None = None,
    max_attempts: int | None = None,
    stop_on_human: bool | None = None,
    run_external: bool | None = None,
    external_target: str = "remote",
    comment_language: str | None = None,
    model_timeout_s: int | None = None,
) -> dict[str, Any]:
    """Run multiple spec-to-RTL workflow cases and summarize their outcomes."""

    if not specs:
        raise ValueError("run_verilog_batch requires at least one spec.")

    batch_root = Path(out_dir)
    batch_root.mkdir(parents=True, exist_ok=True)
    case_results: list[dict[str, Any]] = []
    passed_cases = 0

    for index, spec in enumerate(specs, start=1):
        case_id = _batch_case_id(spec, index)
        case_run_dir = batch_root / f"{index:03d}-{case_id}"
        result = run_verilog_workflow(
            spec,
            out_dir=case_run_dir,
            workflow_config=workflow_config,
            evidence=evidence,
            provider_name=provider_name,
            provider_command=provider_command,
            generation_mode=generation_mode,
            stream=stream,
            target=target,
            design_requirements=design_requirements,
            pipeline_required=pipeline_required,
            streamability=streamability,
            interface_family=interface_family,
            interface_profile=interface_profile,
            readiness=readiness,
            max_attempts=max_attempts,
            stop_on_human=stop_on_human,
            run_external=run_external,
            external_target=external_target,
            comment_language=comment_language,
            model_timeout_s=model_timeout_s,
        )
        case_summary = _batch_case_summary(case_id, case_run_dir, result)
        case_results.append(case_summary)
        if case_summary["status"] == "passed":
            passed_cases += 1

    status = "passed" if passed_cases == len(case_results) else "failed"
    return {
        "status": status,
        "run_dir": str(batch_root),
        "summary": {
            "case_count": len(case_results),
            "passed_cases": passed_cases,
            "failed_cases": len(case_results) - passed_cases,
            "generation_mode": generation_mode or "regular",
        },
        "cases": case_results,
    }


def render_verilog_prompt(
    spec: str | Path | dict[str, Any],
    out_path: str | Path,
    *,
    target: str | None = None,
    design_requirements: str | Path | dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: str | Path | dict[str, Any] | None = None,
    stage: str | None = None,
    context_manifest: str | Path | dict[str, Any] | None = None,
    context_dir: str | Path | None = None,
    evidence: str | Path | dict[str, Any] | None = None,
    memory: str | Path | dict[str, Any] | None = None,
    comment_language: str = "zh",
    vector_contract: str | Path | dict[str, Any] | None = None,
    subfunction: str | None = None,
    budget: str = "normal",
    decision: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a Verilog prompt and write it to disk."""

    resolved_spec = _prepare_facade_spec(
        spec,
        target=_resolve_target(target, spec, {}),
        design_requirements=_load_optional_json(design_requirements),
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=_load_optional_json(interface_profile),
    )
    resolved_codegen_plan = build_codegen_plan(resolved_spec)
    prompt_text = render_prompt(
        resolved_spec,
        target="rtl",
        stage=stage or "rtl",
        context_manifest=_load_optional_json(context_manifest),
        context_dir=Path(context_dir) if context_dir is not None else None,
        evidence=_load_optional_json(evidence),
        memory=_load_optional_json(memory),
        comment_language=comment_language,
        vector_contract=_load_optional_json(vector_contract),
        codegen_plan=resolved_codegen_plan,
        subfunction=subfunction,
        budget=budget,
        decision=_load_optional_json(decision),
    )
    output_path = Path(out_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_text, encoding="utf-8")
    return {"path": str(output_path), "prompt": prompt_text}


def validate_verilog_artifacts(
    spec: str | Path | dict[str, Any],
    artifacts_path: str | Path,
    *,
    target: str | None = None,
    design_requirements: str | Path | dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None,
    interface_family: str | None = None,
    interface_profile: str | Path | dict[str, Any] | None = None,
    run_external: bool = True,
    external_target: str = "remote",
    readiness: str = "static",
    comment_language: str = "zh",
    reference_contract: str | Path | dict[str, Any] | None = None,
    report_json: str | Path | None = None,
) -> dict[str, Any]:
    """Validate generated Verilog artifacts and optionally persist the report."""

    resolved_spec = _prepare_facade_spec(
        spec,
        target=_resolve_target(target, spec, {}),
        design_requirements=_load_optional_json(design_requirements),
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=_load_optional_json(interface_profile),
    )
    report = validate_generated(
        resolved_spec,
        Path(artifacts_path),
        target="rtl",
        run_external=_resolve_external_run(run_external, readiness=readiness, external_target=external_target),
        readiness=readiness,
        comment_language=comment_language,
        reference_contract=_load_optional_json(reference_contract),
    )
    payload = report.to_dict()
    if report_json is not None:
        out_path = Path(report_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _resolve_target(
    target: str | None,
    spec: str | Path | dict[str, Any] | None,
    config: dict[str, Any],
) -> str:
    raw_target = None
    if spec is not None:
        raw_target = _load_raw_spec(spec).get("target")
    resolved = str(target or raw_target or config.get("target") or "rtl").lower()
    if resolved != "rtl":
        raise ValueError("Only target 'rtl' is supported.")
    return "rtl"


def _materialize_spec(spec: str | Path | dict[str, Any], out_path: Path, *, target: str | None) -> Path:
    if isinstance(spec, (str, Path)):
        normalized = read_spec(Path(spec), target=target)
    else:
        normalized = normalize_spec(spec, target=target)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_spec(out_path, normalized)
    return out_path


def _prepare_facade_spec(
    spec: str | Path | dict[str, Any],
    *,
    target: str | None,
    design_requirements: dict[str, Any] | None,
    pipeline_required: bool | None,
    streamability: str | None,
    interface_family: str | None,
    interface_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = _load_raw_spec(spec)
    _resolve_target(target, raw, {})
    if raw.get("rtl_dialect") not in (None, "", "verilog"):
        raise ValueError("Only Verilog-2001 is supported.")
    raw["target"] = "rtl"
    raw["rtl_dialect"] = "verilog"
    enriched = apply_requirement_defaults(
        raw,
        design_requirements=design_requirements,
        pipeline_required=pipeline_required,
        streamability=streamability,
        interface_family=interface_family,
        interface_profile=interface_profile,
        confirmed_by_user=True if any(
            value is not None
            for value in (design_requirements, pipeline_required, streamability, interface_family, interface_profile)
        ) else None,
    )
    normalized = normalize_spec(enriched, target="rtl")
    validate_requirement_confirmation(normalized)
    return normalized


def _write_json_object(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _materialize_optional_json(value: str | Path | dict[str, Any] | None, out_path: Path) -> Path | None:
    if value is None:
        return None
    if isinstance(value, (str, Path)):
        return Path(value)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def _load_optional_json(value: str | Path | dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    path = Path(value)
    return json.loads(path.read_text(encoding="utf-8"))


def _workflow_overrides(value: dict[str, Any]) -> dict[str, Any]:
    workflow = value.get("workflow")
    if isinstance(workflow, dict):
        return workflow
    return value


def _load_raw_spec(spec: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(spec, dict):
        return deepcopy(spec)
    path = Path(spec)
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_sources(source: str | Path | list[str | Path]) -> list[Path]:
    if isinstance(source, list):
        return [Path(item) for item in source]
    return [Path(source)]


def _batch_case_id(spec: str | Path | dict[str, Any], index: int) -> str:
    if isinstance(spec, dict):
        return str(spec.get("name") or f"case_{index}")
    return Path(spec).stem or f"case_{index}"


def _batch_case_summary(case_id: str, case_run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    workflow_result = result.get("workflow_result", {}) if isinstance(result, dict) else {}
    attempts = workflow_result.get("attempts", []) if isinstance(workflow_result, dict) else []
    latest_attempt = attempts[-1] if attempts else {}
    artifact_dir = latest_attempt.get("artifact_dir")
    validation_ok = False
    semantic_gate_ready = None

    validation_path = _resolve_result_path(case_run_dir, latest_attempt.get("validation_json"))
    if validation_path is not None and validation_path.exists():
        validation_payload = json.loads(validation_path.read_text(encoding="utf-8"))
        validation_ok = bool(validation_payload.get("ok"))

    stage_verification_path = _resolve_result_path(
        case_run_dir,
        (latest_attempt.get("contract_paths") or {}).get("stage_verification") if isinstance(latest_attempt, dict) else None,
    )
    if stage_verification_path is not None and stage_verification_path.exists():
        semantic_gate_ready = json.loads(stage_verification_path.read_text(encoding="utf-8")).get("ready")

    return {
        "case_id": case_id,
        "status": str(result.get("status") or "failed"),
        "run_dir": str(case_run_dir),
        "artifact_dir": _resolve_result_path(case_run_dir, artifact_dir).as_posix() if _resolve_result_path(case_run_dir, artifact_dir) is not None else None,
        "validation_ok": validation_ok,
        "semantic_gate_ready": semantic_gate_ready,
        "result_path": str(case_run_dir / "workflow_result.json"),
    }


def _resolve_result_path(run_dir: Path, value: Any) -> Path | None:
    if not value or not isinstance(value, str):
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    if value.startswith("<external>/"):
        return None
    if path.exists():
        return path.resolve()
    return run_dir / path


def _resolve_external_run(run_external: bool, *, readiness: str, external_target: str, allow_static_external: bool = False) -> bool:
    if not run_external:
        return False
    if not allow_static_external and not readiness_at_least(readiness, "compile"):
        return False
    if external_target != "local":
        raise ValueError(
            "External validation is remote-first. Use the remote validation flow, or pass external_target='local' only after the user explicitly approves local external validation."
        )
    return True
