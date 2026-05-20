"""Deterministic skill-effectiveness harness for the Verilog skill."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from integration.verilog_adapter import render_verilog_prompt, run_verilog_workflow, validate_verilog_artifacts

from .config import skill_root
from .refined_templates import summarize_refined_templates
from .workspace import write_json

SKILL_ROOT = skill_root()


def evaluate_skill_effectiveness(
    evals_path: Path,
    out_path: Path,
    *,
    remote_runs_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = json.loads(evals_path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"Skill eval cases must be a non-empty list: {evals_path}")

    temp_root = SKILL_ROOT / "_smoke_runs" / f"skill-effectiveness-{os.getpid()}-{int(time.time())}"
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        with _pushd(SKILL_ROOT):
            case_reports = [_evaluate_case(case, temp_root) for case in cases]
        remote_report = _evaluate_remote_runs(remote_runs_report)
        overall = {
            "case_count": len(case_reports),
            "passed_cases": sum(1 for item in case_reports if item["passed"]),
            "improved_cases": sum(1 for item in case_reports if item["comparison"]["improved"]),
            "stable_cases": sum(1 for item in case_reports if item["with_skill"]["stable"]),
            "remote_verified": remote_report["ok"],
        }
        overall["ok"] = (
            overall["case_count"] == overall["passed_cases"]
            and overall["case_count"] == overall["improved_cases"]
            and overall["case_count"] == overall["stable_cases"]
            and remote_report["ok"]
        )
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


def _evaluate_case(case: dict[str, Any], temp_root: Path) -> dict[str, Any]:
    case_id = str(case.get("id") or "")
    if not case_id:
        raise ValueError(f"Eval case is missing id: {case}")
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
    generated_dir = Path(attempt["artifact_dir"])
    if not generated_dir.is_absolute():
        generated_dir = SKILL_ROOT / generated_dir
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


def _evaluate_remote_runs(remote_runs_report: dict[str, Any] | None) -> dict[str, Any]:
    if not remote_runs_report:
        return {"ok": True, "checked": False, "reason": "no remote run report provided"}
    runs = remote_runs_report.get("runs", []) if isinstance(remote_runs_report, dict) else []
    if not isinstance(runs, list) or not runs:
        return {"ok": False, "checked": True, "reason": "remote run report did not contain any retained runs"}
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
