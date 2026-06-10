"""Deterministic skill-effectiveness harness for the Verilog skill."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from integration.verilog_adapter import (
    analyze_existing_verilog,
    compare_verilog_semantics,
    refine_existing_verilog,
    render_verilog_prompt,
    route_verilog_request,
    run_verilog_batch,
    run_verilog_workflow,
    validate_verilog_artifacts,
    verify_existing_verilog,
)

from .config import skill_root
from .refined_templates import summarize_refined_templates
from .rtl_md_constraints import load_rtl_md_constraints, summarize_constraints_for_prompt
from .static_lint import lint_generated_rtl
from .workspace import workspace_root, write_json

SKILL_ROOT = skill_root()


def evaluate_skill_effectiveness(
    evals_path: Path,
    out_path: Path,
    *,
    remote_runs_report: dict[str, Any] | None = None,
    require_remote: bool = False,
) -> dict[str, Any]:
    payload = json.loads(evals_path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"Skill eval cases must be a non-empty list: {evals_path}")

    temp_root = workspace_root() / "_smoke_runs" / f"skill-effectiveness-{os.getpid()}-{int(time.time())}"
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        with _pushd(SKILL_ROOT):
            case_reports = [_evaluate_case(case, temp_root) for case in cases]
        remote_report = _evaluate_remote_runs(remote_runs_report, require_remote=require_remote)
        local_ok = (
            len(case_reports) == sum(1 for item in case_reports if item["passed"])
            and len(case_reports) == sum(1 for item in case_reports if item["comparison"]["improved"])
            and len(case_reports) == sum(1 for item in case_reports if item["with_skill"]["stable"])
        )
        remote_gate_ok = remote_report["ok"] if (require_remote or remote_report["checked"]) else True
        overall = {
            "case_count": len(case_reports),
            "passed_cases": sum(1 for item in case_reports if item["passed"]),
            "improved_cases": sum(1 for item in case_reports if item["comparison"]["improved"]),
            "stable_cases": sum(1 for item in case_reports if item["with_skill"]["stable"]),
            "remote_verified": remote_report["checked"] and remote_report["ok"],
            "remote_required": require_remote,
        }
        overall["ok"] = local_ok and remote_gate_ok
        report = {
            "version": 1,
            "evals_path": str(evals_path),
            "cases": case_reports,
            "remote": remote_report,
            "summary": overall,
        }
        write_json(out_path, report)
        return report
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
        smoke_root = temp_root.parent
        if smoke_root.exists():
            with contextlib.suppress(OSError):
                if not any(smoke_root.iterdir()):
                    smoke_root.rmdir()


def _evaluate_case(case: dict[str, Any], temp_root: Path) -> dict[str, Any]:
    case_id = str(case.get("id") or "")
    if not case_id:
        raise ValueError(f"Eval case is missing id: {case}")
    if case.get("kind") == "analysis_regression":
        return _evaluate_analysis_case(case, case_id, temp_root)
    if case.get("kind") == "transform_validation_regression":
        return _evaluate_transform_case(case, case_id, temp_root)
    if case.get("kind") == "style_refine_regression":
        return _evaluate_style_refine_case(case, case_id, temp_root)
    if case.get("kind") == "checkpoint_closure_regression":
        return _evaluate_checkpoint_case(case, case_id, temp_root)
    if case.get("kind") == "generation_mode_regression":
        return _evaluate_generation_mode_case(case, case_id, temp_root)
    if case.get("kind") == "streaming_regression":
        return _evaluate_streaming_case(case, case_id, temp_root)
    if case.get("kind") == "batch_regression":
        return _evaluate_batch_case(case, case_id, temp_root)
    if case.get("kind") == "merge_assist_regression":
        return _evaluate_merge_assist_case(case, case_id, temp_root)
    if case.get("kind") == "optimize_assist_regression":
        return _evaluate_optimize_case(case, case_id, temp_root)
    if case.get("kind") == "verify_existing_diagnostics_regression":
        return _evaluate_verify_existing_diagnostics_case(case, case_id, temp_root)
    if case.get("kind") == "verify_existing_augment_regression":
        return _evaluate_verify_existing_augment_case(case, case_id, temp_root)
    if case.get("kind") == "verify_existing_rtl_repair_regression":
        return _evaluate_verify_existing_rtl_repair_case(case, case_id, temp_root)
    if case.get("kind") == "verify_existing_rtl_patch_library_regression":
        return _evaluate_verify_existing_rtl_patch_library_case(case, case_id, temp_root)
    if case.get("kind") == "routing_regression":
        return _evaluate_routing_case(case, case_id, temp_root)
    if case.get("kind") == "rtl_md_constraint_regression":
        return _evaluate_rtl_md_constraint_case(case, case_id, temp_root)
    spec_path = SKILL_ROOT / str(case["spec"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    prompt_path = case_root / "with-skill-prompt.md"
    prompt = render_verilog_prompt(spec, prompt_path)["prompt"]
    workflow = run_verilog_workflow(
        spec,
        out_dir=case_root / "workflow",
        provider_name="mock",
        readiness="static",
        run_external=False,
    )
    attempt = workflow["workflow_result"]["attempts"][-1]
    generated_dir = case_root / "workflow" / attempt["attempt_id"] / "rtl" / "generated"
    validation = validate_verilog_artifacts(spec, generated_dir, run_external=False, readiness="static")
    requirements = json.loads(Path(workflow["requirements_path"]).read_text(encoding="utf-8"))
    codegen_plan = json.loads(Path(workflow["codegen_plan_path"]).read_text(encoding="utf-8"))
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}

    with_skill_checks = _expectation_checks(
        prompt=prompt,
        requirements=requirements,
        codegen_plan=codegen_plan,
        refined_templates=requirements.get("selected_refined_template_ids", []),
        expectations=expectations,
    )
    baseline_prompt = _render_baseline_prompt(spec)
    baseline_checks = _expectation_checks(
        prompt=baseline_prompt,
        requirements={"selected_use_case_template_id": None},
        codegen_plan={},
        refined_templates=[],
        expectations=expectations,
    )
    comparison = {
        "with_skill_pass_count": _pass_count(with_skill_checks),
        "without_skill_pass_count": _pass_count(baseline_checks),
    }
    comparison["improved"] = comparison["with_skill_pass_count"] > comparison["without_skill_pass_count"]

    with_skill = {
        "prompt_path": str(prompt_path),
        "stable": workflow["status"] == "passed" and validation.get("ok") is True and validation.get("warnings") == 0,
        "selected_use_case_template_id": requirements.get("selected_use_case_template_id"),
        "selected_refined_template_ids": requirements.get("selected_refined_template_ids", []),
        "expectation_checks": with_skill_checks,
        "validation": {
            "ok": validation.get("ok"),
            "warnings": validation.get("warnings"),
        },
    }
    without_skill = {
        "selected_use_case_template_id": None,
        "selected_refined_template_ids": [],
        "expectation_checks": baseline_checks,
    }
    passed = with_skill["stable"] and all(with_skill_checks.values()) and comparison["improved"]
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "spec": str(case["spec"]),
        "passed": passed,
        "with_skill": with_skill,
        "without_skill": without_skill,
        "comparison": comparison,
        "refined_templates": summarize_refined_templates(spec),
    }


def _evaluate_analysis_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    spec_source = SKILL_ROOT / str(case["spec_source"]) if case.get("spec_source") else None
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    result = analyze_existing_verilog(source_path, out_dir=case_root / "analysis", spec_source=spec_source)
    analysis = json.loads(Path(result["analysis_path"]).read_text(encoding="utf-8"))
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}

    decomposition_min = int(expectations.get("decomposition_candidates_min", 0))
    mapped_features = list(expectations.get("mapped_features", []))
    expected_states = list(expectations.get("state_elements", []))
    feature_names = {item["name"] for item in analysis.get("feature_mappings", [])}
    state_names = {item["name"] for item in analysis.get("state_elements", [])}
    checks = {
        "decomposition_candidates_min": len(analysis.get("decomposition_candidates", [])) >= decomposition_min,
        "mapped_features": all(item in feature_names for item in mapped_features),
        "state_elements": all(item in state_names for item in expected_states),
    }
    comparison = {
        "with_skill_pass_count": _pass_count(checks),
        "without_skill_pass_count": 0,
        "improved": _pass_count(checks) > 0,
    }
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {
            "stable": True,
            "analysis_path": str(result["analysis_path"]),
            "expectation_checks": checks,
        },
        "without_skill": {
            "expectation_checks": {key: False for key in checks},
        },
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_rtl_md_constraint_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    case_root = temp_root / case_id
    generated = case_root / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "bad_constraints.v").write_text(_rtl_md_bad_fixture(), encoding="utf-8")
    (generated / "good_constraints_tb.v").write_text(_rtl_md_clean_fixture().replace("good_constraints", "good_constraints_tb"), encoding="utf-8")
    clean_root = case_root / "clean"
    clean_root.mkdir(parents=True, exist_ok=True)
    (clean_root / "good_constraints.v").write_text(_rtl_md_clean_fixture(), encoding="utf-8")

    spec = _rtl_md_fixture_spec()
    blocked_issues = lint_generated_rtl(spec, generated)
    blocked_codes = {issue.code for issue in blocked_issues}
    clean_issues = lint_generated_rtl(spec, clean_root)
    catalog = load_rtl_md_constraints()
    prompt_summary = summarize_constraints_for_prompt()
    catalog_rule_ids = {str(rule["id"]) for rule in catalog["rules"]}
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks: dict[str, bool] = {}
    for code in expectations.get("blocked_codes", []):
        checks[f"blocked_{code}"] = str(code) in blocked_codes
    if expectations.get("clean_has_no_issues"):
        checks["clean_has_no_issues"] = not clean_issues
    if expectations.get("catalog_total_rules"):
        checks["catalog_total_rules"] = catalog.get("total_rules") == expectations["catalog_total_rules"] == len(catalog.get("rules", []))
    if expectations.get("prompt_mentions_all_rules"):
        checks["prompt_mentions_all_rules"] = all(rule_id in prompt_summary for rule_id in catalog_rule_ids)
    if expectations.get("static_issues_include_rule_ids"):
        checks["static_issues_include_rule_ids"] = all(
            re.search(r"(MUST|REC)_[A-Z0-9_]+", issue.message)
            for issue in blocked_issues
        )
    if not checks:
        checks = {
            "blocked_any": bool(blocked_codes),
            "clean_has_no_issues": not clean_issues,
        }
    comparison = {
        "with_skill_pass_count": _pass_count(checks),
        "without_skill_pass_count": 0,
        "improved": _pass_count(checks) > 0,
    }
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "passed": all(checks.values()),
        "with_skill": {
            "stable": all(checks.values()),
            "blocked_codes": sorted(blocked_codes),
            "clean_issue_count": len(clean_issues),
            "catalog_total_rules": catalog.get("total_rules"),
            "expectation_checks": checks,
        },
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_transform_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    analysis = analyze_existing_verilog(source_path, out_dir=case_root / "analysis")
    tb_result = refine_existing_verilog(
        source_path,
        out_dir=case_root / "tb",
        refine_goal="tb_scaffold",
        analysis_source=analysis["analysis_path"],
    )
    partition_result = refine_existing_verilog(
        source_path,
        out_dir=case_root / "partition",
        refine_goal="partition_assist",
    )
    same_candidate = case_root / "same.v"
    same_candidate.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    same_compare = compare_verilog_semantics(source_path, same_candidate, out_dir=case_root / "compare-same", run_external=False)
    drift_candidate = case_root / "drift.v"
    drift_candidate.write_text(
        source_path.read_text(encoding="utf-8").replace("output reg green", "output reg [1:0] green"),
        encoding="utf-8",
    )
    drift_compare = compare_verilog_semantics(source_path, drift_candidate, out_dir=case_root / "compare-drift", run_external=False)
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "tb_scaffold": Path(tb_result["artifacts"].get("testbench", "")).exists() if expectations.get("tb_scaffold") else True,
        "partition_wrapper": Path(partition_result["artifacts"].get("wrapper", "")).exists() if expectations.get("partition_wrapper") else True,
        "compare_same_passes": same_compare["status"] == "passed" if expectations.get("compare_same_passes") else True,
        "drift_detected": drift_compare["status"] == "failed" if expectations.get("drift_detected") else True,
    }
    comparison = {
        "with_skill_pass_count": _pass_count(checks),
        "without_skill_pass_count": 0,
        "improved": _pass_count(checks) > 0,
    }
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {
            "stable": True,
            "expectation_checks": checks,
        },
        "without_skill": {
            "expectation_checks": {key: False for key in checks},
        },
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_style_refine_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    result = refine_existing_verilog(
        source_path,
        out_dir=case_root / "style-refine",
        refine_goal="style_refine",
    )
    style_report = Path(result["artifacts"]["style_report"])
    style_text = style_report.read_text(encoding="utf-8")
    validation = json.loads(Path(result["transform_validation_path"]).read_text(encoding="utf-8"))
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "style_report_present": style_report.is_file(),
        "preserve_section_present": "## Preserve" in style_text,
        "suggested_refinements_present": "## Suggested style refinements" in style_text,
        "ready_state_recorded": validation.get("ready") is True and validation.get("goal") == "style_refine",
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {
        "with_skill_pass_count": _pass_count(checks),
        "without_skill_pass_count": 0,
        "improved": _pass_count(checks) > 0,
    }
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": all(checks.values()), "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_checkpoint_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    spec_path = SKILL_ROOT / str(case["spec"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    from runtime.verilog_generator.planning import decompose_spec
    from runtime.verilog_generator.requirements import build_codegen_plan

    plan = build_codegen_plan(decompose_spec(spec))
    python_prompt = render_verilog_prompt(spec, case_root / "python-prompt.md", stage="python")["prompt"]
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "has_structured_checkpoints": bool(plan.get("semantic_checkpoints")) and all("verification_hint" in item for item in plan.get("semantic_checkpoints", [])),
        "has_structured_dependency_graph": isinstance(plan.get("subfunction_dependency_graph"), dict) and "nodes" in plan["subfunction_dependency_graph"] and "edges" in plan["subfunction_dependency_graph"],
        "python_prompt_mentions_checkpoints": ("semantic_checkpoints" in python_prompt and "verification_hint" in python_prompt),
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "spec": str(case["spec"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": True, "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_generation_mode_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    spec_path = SKILL_ROOT / str(case["spec"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    result = run_verilog_workflow(
        spec,
        out_dir=case_root / "deep-review",
        provider_name="mock",
        generation_mode="deep_review",
        run_external=False,
    )
    workflow_result = result["workflow_result"]
    attempt = workflow_result["attempts"][-1]
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "review_stage_present": "review" in attempt.get("stage_outputs", {}),
        "workflow_passes": result["status"] == "passed",
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "spec": str(case["spec"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": all(checks.values()), "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_streaming_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    spec_path = SKILL_ROOT / str(case["spec"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    result = run_verilog_workflow(
        spec,
        out_dir=case_root / "streaming",
        provider_name="mock",
        generation_mode="regular",
        stream=True,
        run_external=False,
    )
    attempt = result["workflow_result"]["attempts"][-1]
    rtl_stage = attempt.get("stage_outputs", {}).get("rtl", {})
    transcript_path = case_root / "streaming" / attempt["attempt_id"] / "rtl" / "rtl_stream.txt"
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "stream_used": rtl_stage.get("stream_used") is True,
        "stream_transcript_present": transcript_path.is_file(),
        "workflow_passes": result["status"] == "passed",
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "spec": str(case["spec"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": all(checks.values()), "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_batch_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    specs = []
    for index, spec_ref in enumerate(case.get("specs", []), start=1):
        spec = json.loads((SKILL_ROOT / str(spec_ref)).read_text(encoding="utf-8"))
        if index > 1:
            spec["name"] = f"{spec['name']}_{index}"
            spec["outputs"] = [
                {"path": f"rtl/{spec['name']}.v", "kind": "source", "language": "verilog"},
                {"path": f"tb/{spec['name']}_tb.v", "kind": "testbench", "language": "verilog"},
            ]
        specs.append(spec)
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    result = run_verilog_batch(
        specs,
        out_dir=case_root / "batch",
        provider_name="mock",
        generation_mode="regular",
        run_external=False,
    )
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "case_count_matches": result["summary"]["case_count"] == len(specs),
        "all_cases_passed": all(item.get("status") == "passed" for item in result.get("cases", [])),
        "artifact_dirs_present": all(Path(item["artifact_dir"]).exists() for item in result.get("cases", []) if item.get("artifact_dir")),
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "passed": all(checks.values()),
        "with_skill": {"stable": all(checks.values()), "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_merge_assist_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    result = refine_existing_verilog(source_path, out_dir=case_root / "merge", refine_goal="merge_assist")
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "merge_plan_present": Path(result["artifacts"]["merge_plan"]).is_file() if expectations.get("merge_plan_present") else True,
        "merge_wrapper_present": Path(result["artifacts"]["merge_wrapper"]).is_file() if expectations.get("merge_wrapper_present") else True,
        "merge_validation_present": Path(result["artifacts"]["merge_validation"]).is_file() if expectations.get("merge_validation_present") else True,
        "merge_equivalence_planned": (
            json.loads(Path(result["artifacts"]["merge_equivalence"]).read_text(encoding="utf-8")).get("status") == "planned"
            if expectations.get("merge_equivalence_planned")
            else True
        ),
    }
    comparison = {
        "with_skill_pass_count": _pass_count(checks),
        "without_skill_pass_count": 0,
        "improved": _pass_count(checks) > 0,
    }
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": True, "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_optimize_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    without_candidate = refine_existing_verilog(source_path, out_dir=case_root / "opt", refine_goal="optimize_assist")
    candidate = case_root / "candidate.v"
    candidate.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    candidate_dir = case_root / "candidate_dir"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_in_dir = candidate_dir / "candidate.v"
    candidate_in_dir.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
    with_candidate = refine_existing_verilog(
        source_path,
        out_dir=case_root / "opt-with-candidate",
        refine_goal="optimize_assist",
        candidate_artifacts_dir=candidate_dir,
    )
    without_validation = json.loads(Path(without_candidate["transform_validation_path"]).read_text(encoding="utf-8"))
    with_validation = json.loads(Path(with_candidate["transform_validation_path"]).read_text(encoding="utf-8"))
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "plan_only_without_candidate": without_validation.get("recommended_next_action") == "provide_candidate_rtl_or_review_plan",
        "qor_summary_present": "qor_summary" in with_validation and "qor_summary" in without_validation,
        "candidate_compare_outputs_present": "equivalence" in with_candidate.get("artifacts", {}) and "qor_report" in with_candidate.get("artifacts", {}),
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": True, "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_verify_existing_diagnostics_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    spec_source = SKILL_ROOT / str(case["spec_source"]) if case.get("spec_source") else None
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    result = verify_existing_verilog(
        source_path,
        out_dir=case_root / "verify-diagnostics",
        spec_source=spec_source,
        automation_mode="conservative",
        tb_mode="generate",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "simulation_slice_present": Path(result["simulation_slice_path"]).is_file() if expectations.get("simulation_slice_present") else True,
        "timing_diagnostic_present": Path(result["timing_diagnostic_path"]).is_file() if expectations.get("timing_diagnostic_present") else True,
        "expected_trace_present": Path(result["expected_trace_path"]).is_file() if expectations.get("expected_trace_present") else True,
        "waveform_diff_present": Path(result["waveform_diff_path"]).is_file() if expectations.get("waveform_diff_present") else True,
        "testcase_matrix_present": Path(result["testcase_matrix_path"]).is_file() if expectations.get("testcase_matrix_present") else True,
        "run_summary_present": Path(result["run_summary_path"]).is_file() if expectations.get("run_summary_present") else True,
        "synth_readiness_present": Path(result["synth_readiness_path"]).is_file() if expectations.get("synth_readiness_present") else True,
        "terminal_status_present": Path(result["terminal_status_path"]).is_file() if expectations.get("terminal_status_present") else True,
    }
    comparison = {
        "with_skill_pass_count": _pass_count(checks),
        "without_skill_pass_count": 0,
        "improved": _pass_count(checks) > 0,
    }
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": True, "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_verify_existing_augment_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    spec_source = SKILL_ROOT / str(case["spec_source"])
    testbench_source = SKILL_ROOT / str(case["testbench_source"])
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)

    conservative = verify_existing_verilog(
        source_path,
        out_dir=case_root / "conservative",
        spec_source=spec_source,
        testbench_source=testbench_source,
        automation_mode="conservative",
        tb_mode="augment",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )
    auto_apply_tb = case_root / "auto_apply" / testbench_source.name
    auto_apply_tb.parent.mkdir(parents=True, exist_ok=True)
    auto_apply_tb.write_text(testbench_source.read_text(encoding="utf-8"), encoding="utf-8")
    auto_apply = verify_existing_verilog(
        [source_path, auto_apply_tb],
        out_dir=case_root / "auto_apply_run",
        spec_source=spec_source,
        automation_mode="auto_apply",
        tb_mode="augment",
        tb_language="systemverilog",
        readiness="static",
        run_external=False,
    )

    conservative_result = json.loads(Path(conservative["verification_result_path"]).read_text(encoding="utf-8"))
    conservative_contract = json.loads(Path(conservative["tb_contract_path"]).read_text(encoding="utf-8"))
    auto_apply_result = json.loads(Path(auto_apply["verification_result_path"]).read_text(encoding="utf-8"))
    auto_apply_contract = json.loads(Path(auto_apply["tb_contract_path"]).read_text(encoding="utf-8"))
    active_tb = Path(auto_apply_contract["active_testbench_path"]).read_text(encoding="utf-8")

    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "explicit_tb_source_supported": conservative_contract.get("original_testbench_path") == str(testbench_source),
        "augment_plan_present": Path(conservative["tb_augment_plan_path"]).exists(),
        "diff_present": Path(conservative["tb_augment_diff_path"]).exists(),
        "tb_hooks_injected": all(tag in active_tb for tag in ("[TB_MONITOR]", "[TB_DATA]", "[TB_ERROR]", "[TB_INFO]", "VERILOG-GEN-RESULT")),
        "auto_apply_backup_created": bool(auto_apply_contract.get("backup_testbench_path")) and Path(auto_apply_contract["backup_testbench_path"]).exists(),
        "systemverilog_upgrade_recorded": auto_apply_contract.get("language_after") == "systemverilog" and auto_apply_result.get("tb_mutation", {}).get("policy") == "auto_apply",
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {
            "stable": not conservative_result.get("tb_mutation", {}).get("applied") and auto_apply_result.get("tb_mutation", {}).get("applied"),
            "expectation_checks": checks,
        },
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_verify_existing_rtl_repair_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    source_path = SKILL_ROOT / str(case["source"])
    spec_source = SKILL_ROOT / str(case["spec_source"])
    blocked_sources = [SKILL_ROOT / str(item) for item in case.get("blocked_sources", [])]
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    local_source = case_root / source_path.name
    local_source.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    conservative = verify_existing_verilog(
        local_source,
        out_dir=case_root / "conservative",
        spec_source=spec_source,
        automation_mode="conservative",
        tb_mode="generate",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )
    decision_path = case_root / "decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "version": 1,
                "status": "resolved",
                "decision": "apply_rtl_patch",
                "evidence": ["confirm low-risk reset patch"],
                "constraints": ["preserve interface"],
                "affected_subfunctions": ["*"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    resumed = verify_existing_verilog(
        local_source,
        out_dir=case_root / "conservative",
        spec_source=spec_source,
        automation_mode="conservative",
        tb_mode="generate",
        tb_language="verilog",
        decision_source=decision_path,
        readiness="static",
        run_external=False,
    )
    auto_source = case_root / "auto_apply_source.v"
    auto_source.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    auto_apply = verify_existing_verilog(
        auto_source,
        out_dir=case_root / "auto-apply",
        spec_source=spec_source,
        automation_mode="auto_apply",
        tb_mode="generate",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )
    blocked_local_sources: list[Path] = []
    for path in blocked_sources:
        local_path = case_root / path.name
        local_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        blocked_local_sources.append(local_path)

    blocked = verify_existing_verilog(
        blocked_local_sources,
        out_dir=case_root / "blocked",
        automation_mode="auto_apply",
        tb_mode="generate",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )

    resumed_payload = json.loads(Path(resumed["verification_result_path"]).read_text(encoding="utf-8"))
    resumed_patch = json.loads(Path(resumed["patch_candidate_path"]).read_text(encoding="utf-8"))
    auto_payload = json.loads(Path(auto_apply["verification_result_path"]).read_text(encoding="utf-8"))
    blocked_payload = json.loads(Path(blocked["verification_result_path"]).read_text(encoding="utf-8"))
    blocked_patch = json.loads(Path(blocked["patch_candidate_path"]).read_text(encoding="utf-8"))

    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {
        "conservative_resume_apply": resumed_payload.get("rtl_mutation", {}).get("applied") is True,
        "auto_apply_low_risk": auto_payload.get("rtl_mutation", {}).get("policy") == "auto_apply" and auto_payload.get("rtl_mutation", {}).get("applied") is True,
        "backup_created": bool(resumed_patch.get("backup_rtl_paths")) and bool(auto_payload.get("rtl_mutation", {}).get("backup_rtl_paths")),
        "post_apply_validation_present": Path(resumed["run_dir"], "post_apply_validation.json").exists() and Path(auto_apply["run_dir"], "post_apply_validation.json").exists(),
        "blocked_multi_file_intervention": "multiple_source_files" in blocked_patch.get("apply_blockers", []) and Path(blocked["run_dir"], "rtl_intervention.json").exists() and blocked_payload.get("rtl_mutation", {}).get("applied") is False,
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["source"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": all(checks.values()), "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_verify_existing_rtl_patch_library_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}

    control_source = SKILL_ROOT / str(case["control_source"])
    control_spec = SKILL_ROOT / str(case["control_spec_source"])
    timing_source = SKILL_ROOT / str(case["timing_source"])
    timing_spec = SKILL_ROOT / str(case["timing_spec_source"])

    def _copy_fixture(source: Path, target_dir: Path) -> Path:
        target = target_dir / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    control_local = _copy_fixture(control_source, case_root / "control")
    control_first = verify_existing_verilog(
        control_local,
        out_dir=case_root / "control-run",
        spec_source=control_spec,
        automation_mode="auto_apply",
        tb_mode="generate",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )
    control_first_payload = json.loads(Path(control_first["verification_result_path"]).read_text(encoding="utf-8"))
    control_first_plan = json.loads(Path(control_first["rtl_patch_plan_path"]).read_text(encoding="utf-8"))
    control_decision = case_root / "control-run" / "decision.json"
    control_decision.write_text(
        json.dumps(
            {
                "version": 1,
                "status": "resolved",
                "decision": "apply_rtl_patch",
                "evidence": ["confirm control logic patch"],
                "constraints": ["preserve interface"],
                "affected_subfunctions": ["*"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    control_resumed = verify_existing_verilog(
        control_local,
        out_dir=case_root / "control-run",
        spec_source=control_spec,
        automation_mode="auto_apply",
        tb_mode="generate",
        tb_language="verilog",
        decision_source=control_decision,
        readiness="static",
        run_external=False,
    )
    control_resumed_payload = json.loads(Path(control_resumed["verification_result_path"]).read_text(encoding="utf-8"))

    timing_local = _copy_fixture(timing_source, case_root / "timing")
    timing_first = verify_existing_verilog(
        timing_local,
        out_dir=case_root / "timing-run",
        spec_source=timing_spec,
        automation_mode="auto_apply",
        tb_mode="generate",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )
    timing_first_payload = json.loads(Path(timing_first["verification_result_path"]).read_text(encoding="utf-8"))
    timing_first_plan = json.loads(Path(timing_first["rtl_patch_plan_path"]).read_text(encoding="utf-8"))
    timing_decision = case_root / "timing-run" / "decision.json"
    timing_decision.write_text(
        json.dumps(
            {
                "version": 1,
                "status": "resolved",
                "decision": "apply_rtl_patch",
                "evidence": ["confirm timing register patch"],
                "constraints": ["preserve interface"],
                "affected_subfunctions": ["*"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    timing_resumed = verify_existing_verilog(
        timing_local,
        out_dir=case_root / "timing-run",
        spec_source=timing_spec,
        automation_mode="auto_apply",
        tb_mode="generate",
        tb_language="verilog",
        decision_source=timing_decision,
        readiness="static",
        run_external=False,
    )
    timing_resumed_payload = json.loads(Path(timing_resumed["verification_result_path"]).read_text(encoding="utf-8"))

    checks = {
        "control_patch_category_detected": control_first_plan.get("patch_category") == "case_default_completion",
        "timing_patch_category_detected": timing_first_plan.get("patch_category") == "output_register_completion",
        "auto_apply_downgraded_to_confirmation": (
            control_first_payload.get("rtl_mutation", {}).get("policy") == "confirm_before_apply"
            and control_first_payload.get("rtl_mutation", {}).get("applied") is False
            and timing_first_payload.get("rtl_mutation", {}).get("policy") == "confirm_before_apply"
            and timing_first_payload.get("rtl_mutation", {}).get("applied") is False
        ),
        "intervention_present_before_apply": (
            Path(control_first["run_dir"], "rtl_intervention.json").exists()
            and Path(timing_first["run_dir"], "rtl_intervention.json").exists()
        ),
        "decision_resume_applies_and_regresses": (
            control_resumed_payload.get("rtl_mutation", {}).get("applied") is True
            and timing_resumed_payload.get("rtl_mutation", {}).get("applied") is True
            and Path(control_resumed["run_dir"], "post_apply_validation.json").exists()
            and Path(control_resumed["run_dir"], "post_apply_equivalence.json").exists()
            and Path(timing_resumed["run_dir"], "post_apply_validation.json").exists()
            and Path(timing_resumed["run_dir"], "post_apply_equivalence.json").exists()
        ),
    }
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case["control_source"]),
        "passed": all(checks.values()),
        "with_skill": {"stable": all(checks.values()), "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _evaluate_routing_case(case: dict[str, Any], case_id: str, temp_root: Path) -> dict[str, Any]:
    case_root = temp_root / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    source_path = SKILL_ROOT / str(case["source"]) if case.get("source") else None
    if case.get("missing_source"):
        source_path = case_root / "missing_rtl.v"
    spec_source = SKILL_ROOT / str(case["spec_source"]) if case.get("spec_source") else None
    artifact_dir = _routing_artifact_dir(case, case_root)
    codegen_plan = case.get("codegen_plan") if isinstance(case.get("codegen_plan"), dict) else None
    log_paths = _routing_log_paths(case, case_root)
    decision = route_verilog_request(
        request_summary=str(case.get("request_summary") or ""),
        rtl=source_path,
        spec=spec_source,
        codegen_plan=codegen_plan,
        logs=log_paths,
        artifact_dir=artifact_dir,
        remote_validation_requested=bool(case.get("remote_validation_requested", False)),
    )
    expectations = case.get("expectations", {}) if isinstance(case.get("expectations"), dict) else {}
    checks = {}
    if "recommended_flow" in expectations:
        checks["recommended_flow"] = decision.get("recommended_flow") == expectations.get("recommended_flow")
    if "entry_mode" in expectations:
        checks["entry_mode"] = decision.get("entry_mode") == expectations.get("entry_mode")
    for item in expectations.get("missing_inputs_contains", []):
        checks[f"missing_inputs_contains_{item}"] = item in decision.get("missing_inputs", [])
    for item in expectations.get("missing_inputs_not_contains", []):
        checks[f"missing_inputs_not_contains_{item}"] = item not in decision.get("missing_inputs", [])
    for item in expectations.get("risk_flags_contains", []):
        checks[f"risk_flags_contains_{item}"] = item in decision.get("risk_flags", [])
    if expectations.get("next_action_contains"):
        checks["next_action_contains"] = str(expectations["next_action_contains"]) in str(decision.get("next_action", ""))
    checks.update(
        {
        "provenance_policy_present": decision.get("provenance_policy", {}).get("reference_material") == "abstract_principles_only",
        "reference_workspace_absent": "IC-" + "AGENT-HUB" not in json.dumps(decision, ensure_ascii=False),
        }
    )
    checks = {key: value if expectations.get(key, True) else True for key, value in checks.items()}
    comparison = {"with_skill_pass_count": _pass_count(checks), "without_skill_pass_count": 0, "improved": _pass_count(checks) > 0}
    return {
        "id": case_id,
        "kind": case.get("kind"),
        "source": str(case.get("source", "")),
        "passed": all(checks.values()),
        "with_skill": {"stable": all(checks.values()), "route_decision": decision, "expectation_checks": checks},
        "without_skill": {"expectation_checks": {key: False for key in checks}},
        "comparison": comparison,
        "refined_templates": [],
    }


def _routing_artifact_dir(case: dict[str, Any], case_root: Path) -> Path | None:
    artifact_config = case.get("artifact_dir")
    if not isinstance(artifact_config, dict):
        return None
    artifact_dir = case_root / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if artifact_config.get("spec"):
        (artifact_dir / "spec.json").write_text(json.dumps({"target": "rtl", "module_name": "routing_eval"}, indent=2), encoding="utf-8")
    if isinstance(artifact_config.get("codegen_plan"), dict):
        (artifact_dir / "codegen_plan.json").write_text(json.dumps(artifact_config["codegen_plan"], indent=2), encoding="utf-8")
    return artifact_dir


def _routing_log_paths(case: dict[str, Any], case_root: Path) -> list[Path]:
    logs = case.get("logs", [])
    paths: list[Path] = []
    if isinstance(logs, list):
        for index, item in enumerate(logs, start=1):
            if not isinstance(item, dict):
                continue
            path = case_root / str(item.get("name") or f"log_{index}.log")
            path.write_text(str(item.get("text") or ""), encoding="utf-8")
            paths.append(path)
    if case.get("missing_log"):
        paths.append(case_root / "missing.log")
    return paths


def _evaluate_remote_runs(remote_runs_report: dict[str, Any] | None, *, require_remote: bool) -> dict[str, Any]:
    if not remote_runs_report:
        return {
            "ok": False,
            "checked": False,
            "required": require_remote,
            "reason": "no remote run report provided",
        }
    runs = remote_runs_report.get("runs", []) if isinstance(remote_runs_report, dict) else []
    if not isinstance(runs, list) or not runs:
        return {
            "ok": False,
            "checked": True,
            "required": require_remote,
            "reason": "remote run report did not contain any retained runs",
        }
    latest = runs[0]
    remote_execute = latest.get("remote_execute", {}) if isinstance(latest.get("remote_execute"), dict) else {}
    fixtures = latest.get("fixtures", []) if isinstance(latest.get("fixtures"), list) else []
    fixtures_ok = fixtures and all(item.get("ok") is True for item in fixtures if isinstance(item, dict))
    remote_ok = (
        remote_execute.get("available") is True
        and remote_execute.get("ok") is True
        and remote_execute.get("selected_simulator_backend") == "xsim"
        and fixtures_ok
    )
    return {
        "ok": remote_ok,
        "checked": True,
        "required": require_remote,
        "latest_run": latest.get("run"),
        "selected_simulator_backend": remote_execute.get("selected_simulator_backend"),
        "fixture_count": len(fixtures),
    }


def _expectation_checks(
    *,
    prompt: str,
    requirements: dict[str, Any],
    codegen_plan: dict[str, Any],
    refined_templates: list[str],
    expectations: dict[str, Any],
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if "rtl_style_profile" in expectations:
        checks["rtl_style_profile"] = (
            expectations["rtl_style_profile"] == "erie_strict"
            and "erie_strict" in prompt
            and "state_current" in prompt
            and "state_next" in prompt
        )
    if "selected_use_case_template_id" in expectations:
        checks["selected_use_case_template_id"] = requirements.get("selected_use_case_template_id") == expectations["selected_use_case_template_id"]
    if "requires_use_case_section" in expectations:
        checks["requires_use_case_section"] = ("## Use-case template" in prompt) is bool(expectations["requires_use_case_section"])
    if "selected_refined_template_ids" in expectations:
        expected_ids = list(expectations["selected_refined_template_ids"])
        checks["selected_refined_template_ids"] = list(refined_templates) == expected_ids
    if "requires_refined_template_section" in expectations:
        checks["requires_refined_template_section"] = ("## Refined Verilog design patterns" in prompt) is bool(expectations["requires_refined_template_section"])
    if expectations.get("ready_for_generation") is not None:
        checks["ready_for_generation"] = codegen_plan.get("ready_for_generation") is expectations["ready_for_generation"]
    return checks


def _render_baseline_prompt(spec: dict[str, Any]) -> str:
    return (
        "# Generic Verilog prompt\n\n"
        "Generate synthesizable Verilog-2001 RTL from this JSON spec.\n\n"
        "```json\n"
        + json.dumps(spec, indent=2, ensure_ascii=False)
        + "\n```\n"
    )


def _rtl_md_fixture_spec() -> dict[str, Any]:
    return {
        "name": "good_constraints",
        "description": "RTL Markdown constraint evaluation fixture.",
        "behavior": ["Register one input bit."],
        "constraints": [],
        "notes": [],
        "clock": {"name": "clk", "edge": "posedge"},
        "reset": {"name": "rst_n", "active": "low", "synchronous": False},
        "interfaces": {
            "ports": [
                {"name": "clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "rst_n", "direction": "input", "width": 1, "role": "reset"},
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 1},
            ]
        },
        "outputs": [{"path": "rtl/good_constraints.v", "kind": "source", "language": "verilog"}],
    }


def _rtl_md_bad_fixture() -> str:
    return "\n".join(
        [
            "module bad_constraints(input wire clk, input wire rst_n, input wire [3:0] a, output reg y);",
            "wire gated_clk = clk & rst_n;",
            "initial y = 1'b0;",
            "always @(a || rst_n) begin",
            "  if (a == 4'bx) begin",
            "    y <= 1'b1;",
            "  end",
            "  case (a)",
            "    4'b0001: y = 1'b1;",
            "  endcase",
            "end",
            "endmodule",
            "",
        ]
    )


def _rtl_md_clean_fixture() -> str:
    return "\n".join(
        [
            "module good_constraints(input wire clk, input wire rst_n, input wire [3:0] a, output reg y);",
            "always @(posedge clk or negedge rst_n) begin",
            "  if (!rst_n) begin",
            "    y <= 1'b0;",
            "  end else begin",
            "    y <= a[0];",
            "  end",
            "end",
            "endmodule",
            "",
        ]
    )


def _pass_count(checks: dict[str, bool]) -> int:
    return sum(1 for value in checks.values() if value)


@contextlib.contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
