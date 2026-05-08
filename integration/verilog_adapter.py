"""Stable facade for Verilog-only generation workflows."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime.verilog_generator.config import load_settings, workflow_defaults
from runtime.verilog_generator.prompt import render_prompt
from runtime.verilog_generator.requirements import (
    apply_requirement_defaults,
    build_codegen_plan,
    build_requirements_payload,
    validate_requirement_confirmation,
)
from runtime.verilog_generator.spec import normalize_spec, read_spec, write_spec
from runtime.verilog_generator.validation import validate_generated
from runtime.verilog_generator.workflow import run_workflow
from runtime.verilog_generator.workspace import use_workspace_root

__all__ = [
    "run_verilog_workflow",
    "render_verilog_prompt",
    "validate_verilog_artifacts",
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
    resolved_run_external = bool(run_external) if run_external is not None else bool(merged.get("run_external", True))
    resolved_comment_language = comment_language or str(merged.get("comment_language", "zh"))
    resolved_provider_name = provider_name or str(merged.get("model_provider", "command"))
    resolved_timeout = int(model_timeout_s or merged.get("model_timeout_s", 120))

    if resume_dir is not None:
        run_dir = Path(resume_dir)
        decision_path = _materialize_optional_json(decision, run_dir / "_adapter_inputs" / "decision.json")
        with use_workspace_root(run_dir):
            result = run_workflow(
                resume_dir=run_dir,
                decision_path=decision_path,
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
        run_external=run_external,
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
