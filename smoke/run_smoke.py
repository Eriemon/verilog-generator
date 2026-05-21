"""Standalone smoke validator for the Verilog-only skill."""

from __future__ import annotations

import argparse
import os
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.verilog_adapter import (  # noqa: E402
    render_verilog_prompt,
    run_verilog_workflow,
    validate_verilog_artifacts,
)
from runtime.verilog_generator.extractor import extract_response  # noqa: E402
from runtime.verilog_generator.config import fpga_developer_routing_settings, load_settings, path_setting, remote_setting, skill_dependency_settings  # noqa: E402
from runtime.verilog_generator.interface_templates import list_interface_templates, resolve_interface_template  # noqa: E402
from runtime.verilog_generator.model_provider import _mock_erie_rtl_source_text, _mock_erie_rtl_testbench_text, _mock_vectors  # noqa: E402
from runtime.verilog_generator.reflection import generate_repair_prompt  # noqa: E402
from runtime.verilog_generator.requirements import apply_requirement_defaults, build_codegen_plan, validate_requirement_confirmation  # noqa: E402
from runtime.verilog_generator.vectors import vector_contract_from_payload  # noqa: E402
from runtime.verilog_generator import workspace as workspace_runtime  # noqa: E402
from smoke.dependency_gates import run_dependency_manager_gate, run_project_local_state_gate  # noqa: E402
from smoke.effectiveness_gates import (  # noqa: E402
    run_refined_template_gates,
    run_remote_entrypoint_gates,
    run_skill_effectiveness_gates,
)
from smoke.shared import interface_policy_spec as _interface_policy_spec  # noqa: E402
from smoke.shared import remove_tree_with_retry as _remove_tree_with_retry  # noqa: E402
from smoke.shared import rtl_smoke_spec as _rtl_smoke_spec  # noqa: E402
from smoke.shared import write_mock_rtl_artifacts as _write_mock_rtl_artifacts  # noqa: E402
from smoke.toolchain_gates import (  # noqa: E402
    run_remote_fixture_gate as _run_remote_fixture_gate,
    run_remote_report_gate as _run_remote_report_gate,
    run_remote_retention_policy_gate as _run_remote_retention_policy_gate,
    run_remote_selection_preflight_gate as _run_remote_selection_preflight_gate,
    run_remote_server_list_fallback_gate as _run_remote_server_list_fallback_gate,
    run_remote_toolchain_selection_gate as _run_remote_toolchain_selection_gate,
    run_remote_vivado_activation_gate as _run_remote_vivado_activation_gate,
    run_simulator_priority_gate as _run_simulator_priority_gate,
    run_validate_cleanup_retry_gate as _run_validate_cleanup_retry_gate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Verilog-only skill smoke checks.")
    parser.add_argument("--settings", type=Path, help="Optional settings JSON path.")
    args = parser.parse_args(argv)
    settings = load_settings(args.settings)
    smoke_root = path_setting(settings, "smoke_dir")
    base = smoke_root / f"smoke-{os.getpid()}-{int(time.time())}"
    example_spec = path_setting(settings, "example_spec")
    use_case_examples_dir = path_setting(settings, "use_case_examples_dir")
    if base.exists():
        _remove_tree_with_retry(base)
    base.mkdir(parents=True, exist_ok=True)
    try:
        _run_markdown_ascii_gate()
        _run_skill_metadata_gate()
        _run_skill_standards_gate()
        _run_skill_eval_assets_gate()
        _run_repo_governance_version_gate()
        run_skill_effectiveness_gates(ROOT, base)
        _run_mock_workflow(base, example_spec)
        _run_use_case_template_gate(base, use_case_examples_dir)
        _run_use_case_template_integrity_gate()
        run_refined_template_gates(ROOT, base)
        _run_invalid_response(base)
        _run_target_rejection(base)
        _run_dialect_rejection(base)
        _run_prompt_extract_validate(base)
        _run_interface_bus_policy_gate()
        _run_interface_template_gate(base)
        _run_static_lint_quality_gate(base)
        _run_line_comment_quality_gate(base)
        _run_comment_placement_quality_gate(base)
        _run_verilog_only_artifact_gate(base)
        _run_reference_loading_gate()
        _run_ref_style_asset_gate()
        _run_use_case_repair_prompt_gate()
        _run_verilog_lint_script_gate(base)
        _run_tb_generator_script_gate(base)
        _run_dependency_config_gate(settings)
        run_project_local_state_gate(base, ROOT)
        run_dependency_manager_gate(base, ROOT, settings)
        _run_remote_selection_preflight_gate(base, ROOT)
        _run_remote_vivado_activation_gate(ROOT)
        _run_remote_retention_policy_gate(ROOT)
        _run_remote_server_list_fallback_gate(base, ROOT)
        _run_remote_toolchain_selection_gate(base, ROOT)
        run_remote_entrypoint_gates(ROOT, base)
        _run_remote_fixture_gate(base, ROOT)
        _run_remote_report_gate(base, ROOT)
        _run_simulator_priority_gate(base, ROOT, example_spec)
        _run_validate_cleanup_retry_gate(base, ROOT)
    finally:
        _remove_tree_with_retry(base, ignore_errors=True)
    print("Verilog-only smoke checks passed.")
    return 0


def _run_markdown_ascii_gate() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.md"):
        if "_smoke_runs" in path.parts or "reports" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in {"README.md", "README-CN.md"}:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(ord(char) > 127 for char in line):
                violations.append(f"{rel}:{line_number}")
    assert not violations, "Markdown files must be ASCII-only for install safety: " + ", ".join(violations)


def _run_skill_metadata_gate() -> None:
    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text.startswith("---\n"), "SKILL.md must start with YAML frontmatter."
    _, frontmatter, _body = skill_text.split("---", 2)
    fields = [
        line.split(":", 1)[0].strip()
        for line in frontmatter.splitlines()
        if line.strip() and not line.startswith(" ") and ":" in line
    ]
    assert fields == ["name", "description"], fields
    description = frontmatter.split("description: >-", 1)[1].strip().replace("\n", " ")
    assert len(frontmatter) <= 1024, len(frontmatter)
    assert description.startswith("Use when"), description
    for keyword in (
        "Chinese-language Verilog",
        "RTL",
        "independent static lint",
        "testbench",
        "ASIC-quality review",
        "Vivado/xsim",
        "troubleshooting",
    ):
        assert keyword in description, keyword
    assert len(description) <= 500, len(description)
    assert "Generate, prompt, run, resume" not in description, description
    for token in ("requirements ->", "codegen plan", "run-workflow", "prompt --spec", "resume"):
        assert token not in description.lower(), description

    openai_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'short_description: "Generate, modify, debug, and validate Verilog RTL."' in openai_yaml
    assert 'default_prompt: "Use $erie-verilog-generator to design, modify, debug, and validate synthesizable Verilog RTL."' in openai_yaml
    assert "allow_implicit_invocation: true" in openai_yaml
    assert "ASIC quality review" in skill_text
    assert "independent static lint" in skill_text
    assert "testbench scaffold" in skill_text
    assert "optional workflow steps" in skill_text
    assert "Strict quality control is mandatory." in skill_text
    assert "Optional helper tools are inside the workflow" in skill_text
    assert "references/asic-verilog-quality.md" in skill_text
    assert "references/lint-checklist.md" in skill_text
    assert "references/testbench-patterns.md" in skill_text


def _run_skill_standards_gate() -> None:
    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    load_lines = [line.strip() for line in skill_text.splitlines() if line.strip().startswith("- Load ")]
    assert load_lines, "SKILL.md must contain progressive-disclosure Load rules."
    for line in load_lines:
        match = re.search(r"`([^`]+)`", line)
        if match:
            assert (ROOT / match.group(1)).exists(), match.group(1)

    standards_text = (ROOT / "references" / "skill-standards.md").read_text(encoding="utf-8")
    standards_lower = standards_text.lower()
    for marker in ("Tool Wrapper", "Generator", "Reviewer", "Inversion", "Pipeline"):
        assert marker in standards_text, marker
    for marker in ("progressive disclosure", "pass-rate delta", "with and without the skill"):
        assert marker in standards_lower, marker

    goals_text = (ROOT / "ENGINEERING_DESIGN_GOALS.md").read_text(encoding="utf-8")
    for marker in ("Tool Wrapper", "Generator", "Reviewer", "Inversion", "Pipeline"):
        assert marker in goals_text, marker

    assert (ROOT / "runtime" / "verilog_generator" / "evaluation.py").exists()
    assert (ROOT / "runtime" / "verilog_generator" / "eval_suite.py").exists()


def _run_skill_eval_assets_gate() -> None:
    evals_path = ROOT / "evals" / "evals.json"
    assert evals_path.exists(), evals_path
    payload = json.loads(evals_path.read_text(encoding="utf-8"))
    assert payload.get("version") == 1, payload
    cases = payload.get("cases")
    assert isinstance(cases, list) and cases, payload
    case_ids = {str(item.get("id")) for item in cases if isinstance(item, dict)}
    assert {"canonical_rtl", "spi_adc", "spi_dac", "jesd_adc", "jesd_dac", "mxfe_mixed"}.issubset(case_ids), case_ids


def _run_repo_governance_version_gate() -> None:
    if not (REPO_ROOT / "AGENTS.md").exists():
        return
    if not (REPO_ROOT / "docs").exists():
        return
    root_agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    metadata_match = re.search(r"agents_version=(v[0-9.]+); generator_version=(v[0-9.]+)", root_agents)
    assert metadata_match, root_agents
    agents_version, generator_version = metadata_match.groups()
    assert agents_version == generator_version, root_agents
    assert "default_language=" in root_agents, root_agents
    assert "All natural-language responses must use" in root_agents, root_agents

    handoff_doc = (REPO_ROOT / "docs" / "handoff" / "HANDOFF.md").read_text(encoding="utf-8")
    for marker in (
        "## Original Plan And Steps",
        "## Current Step",
        "## Problems",
        "## Resolved Problems",
        "## Remaining Problems",
        "## Next Work",
        "## Verification Evidence",
    ):
        assert marker in handoff_doc, handoff_doc


def _run_mock_workflow(base: Path, example_spec: Path) -> None:
    run_dir = base / "happy"
    result = run_verilog_workflow(
        example_spec,
        out_dir=run_dir,
        provider_name="mock",
        readiness="static",
        run_external=False,
    )
    assert result["status"] == "passed", result
    payload = json.loads((run_dir / "workflow_result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert (run_dir / "_adapter_inputs" / "requirements.json").exists()
    assert (run_dir / "_adapter_inputs" / "codegen_plan.json").exists()


def _run_use_case_template_gate(base: Path, examples_dir: Path) -> None:
    catalog_path = ROOT / "assets" / "use_case_templates" / "catalog.json"
    assert catalog_path.exists(), catalog_path
    examples = sorted(examples_dir.glob("*.json"))
    assert len(examples) == 5, examples
    expected_ids = {"spi_adc", "spi_dac", "jesd_adc", "jesd_dac", "mxfe_mixed"}
    assert {item.stem for item in examples} == expected_ids, examples

    for example in examples:
        spec = json.loads(example.read_text(encoding="utf-8"))
        template_id = spec.get("workflow", {}).get("use_case_template_id")
        assert template_id == example.stem, spec
        run_dir = base / "use-case" / template_id
        result = run_verilog_workflow(
            spec,
            out_dir=run_dir,
            provider_name="mock",
            readiness="static",
            run_external=False,
        )
        assert result["status"] == "passed", result
        requirements = json.loads((run_dir / "_adapter_inputs" / "requirements.json").read_text(encoding="utf-8"))
        assert requirements["selected_use_case_template_id"] == template_id, requirements
        assert requirements["use_case_template"]["id"] == template_id, requirements
        assert requirements["use_case_template"]["source_projects"], requirements
        plan = json.loads((run_dir / "_adapter_inputs" / "codegen_plan.json").read_text(encoding="utf-8"))
        assert plan["selected_use_case_template_id"] == template_id, plan
        assert plan["use_case_template"]["id"] == template_id, plan
        prompt_text = render_verilog_prompt(spec, run_dir / "prompt.md")["prompt"]
        assert "## Use-case template" in prompt_text, prompt_text
        assert template_id in prompt_text, prompt_text


def _run_use_case_template_integrity_gate() -> None:
    template_root = ROOT / "assets" / "use_case_templates"
    forbidden_source_prefix = "ref" + "/projects"
    for system_bd in sorted(template_root.glob("*/tcl/system_bd.tcl")):
        text = system_bd.read_text(encoding="utf-8")
        assert "file dirname [info script]" in text, system_bd
        assert "source [file join $template_dir common_bd.tcl]" in text, system_bd
        assert "../use_case_templates/" not in text, system_bd
        assert "../common/" not in text, system_bd
        assert forbidden_source_prefix not in text, system_bd

    for artifact in sorted(item for item in template_root.rglob("*") if item.is_file()):
        rel = artifact.relative_to(template_root).as_posix()
        text = artifact.read_text(encoding="utf-8", errors="ignore")
        if artifact.name in {"catalog.json", "manifest.json"}:
            continue
        assert forbidden_source_prefix not in text, rel


def _run_invalid_response(base: Path) -> None:
    spec = _rtl_smoke_spec()
    spec["workflow"] = {"mock_behavior": {"rtl": "invalid_response"}}
    result = run_verilog_workflow(spec, out_dir=base / "invalid", provider_name="mock", run_external=False)
    assert result["status"] == "invalid_response", result


def _run_target_rejection(base: Path) -> None:
    spec = _rtl_smoke_spec()
    spec["target"] = "h" + "ls"
    try:
        run_verilog_workflow(spec, out_dir=base / "bad-target", provider_name="mock")
    except ValueError as exc:
        assert "rtl" in str(exc)
    else:
        raise AssertionError("Expected unsupported target to fail.")


def _run_dialect_rejection(base: Path) -> None:
    spec = _rtl_smoke_spec()
    spec["rtl_dialect"] = "system" + "verilog"
    try:
        render_verilog_prompt(spec, base / "bad-dialect" / "prompt.md")
    except ValueError as exc:
        assert "Verilog-2001" in str(exc) or "rtl" in str(exc)
    else:
        raise AssertionError("Expected unsupported dialect to fail.")


def _run_prompt_extract_validate(base: Path) -> None:
    spec = _rtl_smoke_spec()
    prompt_path = base / "rtl" / "prompt.md"
    rendered = render_verilog_prompt(spec, prompt_path)
    prompt_text = rendered["prompt"]
    assert "RTL implementation generation" in prompt_text
    assert "Verilog-2001" in prompt_text
    assert "Avoid Verilog function/task blocks" in prompt_text
    assert "Do not create raw gated clocks" in prompt_text
    assert "Use complete combinational assignments" in prompt_text
    assert "Document CDC and reset assumptions" in prompt_text
    assert "same-line explanatory comment" in prompt_text
    assert "references/verilog-comment-placement.md" in prompt_text
    assert "Pure leading comments" in prompt_text
    assert "state-task processing" not in prompt_text
    assert "main-task processing" not in prompt_text
    assert "AXI-Stream for streaming data" in prompt_text
    assert "state_current" in prompt_text and "state_next" in prompt_text, prompt_text
    assert "`ST_`" in prompt_text or "ST_*" in prompt_text, prompt_text
    assert "_Inst" in prompt_text, prompt_text
    assert "gen_*" in prompt_text, prompt_text
    assert "AXI/AXIS/APB/AHB" in prompt_text or "AXI/AXIS/APB/AHB 绔彛" in prompt_text or "bus type" in prompt_text, prompt_text
    assert "鐗堟湰/淇鍘嗗彶" in prompt_text or "version/revision/history" in prompt_text.lower(), prompt_text
    for fragment in ("H" + "LS", "V" + "itis", "System" + "Verilog", ".s" + "v", "ap_" + "uint", "#pragma " + "H" + "LS"):
        assert fragment not in prompt_text

    generated_dir = _write_mock_rtl_artifacts(spec, base / "rtl" / "generated")
    report = validate_verilog_artifacts(
        spec,
        generated_dir,
        run_external=False,
    )
    assert report["ok"] is True, report


def _run_interface_bus_policy_gate() -> None:
    cases = [
        ("stream packets through a video pipeline", "streamable", "axi_stream"),
        ("control and status register block", "non_streamable", "axi4_lite"),
        ("DMA burst memory mapped transfer", "streamable", "axi4"),
        ("AHB platform peripheral bridge", "non_streamable", "ahb"),
        ("APB low speed peripheral register bank", "non_streamable", "apb"),
    ]
    for description, streamability, expected_family in cases:
        spec = _interface_policy_spec(description)
        enriched = apply_requirement_defaults(spec, streamability=streamability)
        assert enriched["interface_family"] == expected_family, (description, enriched)

    for family, profile, clock, reset in (
        (
            "axi4_lite",
            {"role": "slave", "read_write_mode": "read_write", "data_width": 32, "addr_width": 16},
            "i_axi_aclk",
            "i_axi_arstn",
        ),
        ("ahb", {"role": "slave", "data_width": 32, "addr_width": 16}, "i_ahb_hclk", "i_ahb_hrstn"),
        ("apb", {"role": "slave", "data_width": 32, "addr_width": 16}, "i_apb_pclk", "i_apb_prstn"),
    ):
        spec = _interface_policy_spec(f"{family} explicit interface", clock=clock, reset=reset)
        enriched = apply_requirement_defaults(
            spec,
            interface_family=family,
            interface_profile=profile,
            confirmed_by_user=True,
        )
        validate_requirement_confirmation(enriched)
        plan = build_codegen_plan(enriched)
        assert plan["interface_decision"]["family"] == family, plan
        assert plan["interface_decision"]["selected_interface_template_id"], plan
        assert any("鎬荤嚎绔彛鍒嗙粍" in item or "bus port grouping" in item.lower() for item in plan["syntax_risk_checks"]), plan


def _run_interface_template_gate(base: Path) -> None:
    templates = list_interface_templates()
    ids = {item["template_id"] for item in templates}
    assert ids == {
        "axi_stream_duplex",
        "axi4_lite_config",
        "axi4_full_master",
        "axi4_full_slave",
        "ahb_lite_config",
        "apb_config",
    }, ids

    cases = [
        (
            "axi_stream",
            {"keep_ready": True, "keep_last": True, "data_width": 32},
            "i_axis_aclk",
            "i_axis_arstn",
            "axi_stream_duplex",
            "i_s_axis_wvalid",
            ("i_axi_awaddr", "o_m_axi_awaddr", "i_s_axi_awaddr", "i_ahb_htrans", "i_apb_psel"),
        ),
        (
            "axi4_lite",
            {"role": "slave", "read_write_mode": "read_write", "data_width": 32, "addr_width": 16},
            "i_axi_aclk",
            "i_axi_arstn",
            "axi4_lite_config",
            "i_axi_awaddr",
            ("i_s_axis_wvalid", "o_m_axi_awaddr", "i_s_axi_awaddr", "i_ahb_htrans", "i_apb_psel"),
        ),
        (
            "axi4",
            {
                "axi4_variant": "axi4_full",
                "role": "master",
                "read_write_mode": "read_write",
                "data_width": 32,
                "addr_width": 32,
                "id_width": 1,
                "burst_support": True,
                "max_burst_len": 16,
            },
            "i_axi_aclk",
            "i_axi_arstn",
            "axi4_full_master",
            "o_m_axi_awaddr",
            ("i_s_axis_wvalid", "i_axi_awaddr", "i_s_axi_awaddr", "i_ahb_htrans", "i_apb_psel"),
        ),
        (
            "axi4",
            {
                "axi4_variant": "axi4_full",
                "role": "slave",
                "read_write_mode": "read_write",
                "data_width": 32,
                "addr_width": 32,
                "id_width": 1,
                "burst_support": True,
                "max_burst_len": 16,
            },
            "i_axi_aclk",
            "i_axi_arstn",
            "axi4_full_slave",
            "i_s_axi_awaddr",
            ("i_s_axis_wvalid", "i_axi_awaddr", "o_m_axi_awaddr", "i_ahb_htrans", "i_apb_psel"),
        ),
        (
            "ahb",
            {"role": "slave", "data_width": 32, "addr_width": 16},
            "i_ahb_hclk",
            "i_ahb_hrstn",
            "ahb_lite_config",
            "i_ahb_htrans",
            ("i_s_axis_wvalid", "i_axi_awaddr", "o_m_axi_awaddr", "i_s_axi_awaddr", "i_apb_psel"),
        ),
        (
            "apb",
            {"role": "slave", "data_width": 32, "addr_width": 16},
            "i_apb_pclk",
            "i_apb_prstn",
            "apb_config",
            "i_apb_psel",
            ("i_s_axis_wvalid", "i_axi_awaddr", "o_m_axi_awaddr", "i_s_axi_awaddr", "i_ahb_htrans"),
        ),
    ]
    for index, (family, profile, clock, reset, expected_id, expected_signal, absent_signals) in enumerate(cases, start=1):
        selected = resolve_interface_template(family, profile)
        assert selected["template_id"] == expected_id, selected
        spec = _interface_policy_spec(f"{family} template smoke", clock=clock, reset=reset)
        rendered = render_verilog_prompt(
            spec,
            base / "interface-templates" / f"case-{index}" / "prompt.md",
            interface_family=family,
            interface_profile=profile,
        )
        prompt_text = rendered["prompt"]
        assert "## Interface template" in prompt_text, prompt_text
        assert expected_id in prompt_text, prompt_text
        assert expected_signal in prompt_text, prompt_text
        for absent in absent_signals:
            assert absent not in prompt_text, (expected_id, absent)
        enriched = apply_requirement_defaults(
            spec,
            interface_family=family,
            interface_profile=profile,
            confirmed_by_user=True,
        )
        plan = build_codegen_plan(enriched)
        assert plan["interface_decision"]["selected_interface_template_id"] == expected_id, plan
        assert plan["interface_decision"]["port_naming_policy"] == "strict_preferred", plan


def _write_mock_rtl_artifacts(spec: dict, generated_dir: Path) -> Path:
    vectors = _mock_vectors(spec)
    vector_contract = vector_contract_from_payload({"cases": vectors})
    response = f"""```json
{{
  "target": "rtl",
  "name": "erie_adapter",
  "top": "erie_adapter",
  "files": [
    {{"path": "rtl/erie_adapter.v", "kind": "source", "language": "verilog"}},
    {{"path": "tb/erie_adapter_tb.v", "kind": "testbench", "language": "verilog"}}
  ],
  "checks": {{
    "spec_coverage": ["Mock smoke output matches the strict Erie spec."],
    "verification_plan": ["Self-checking testbench emits PASS and FAIL strings."],
    "execution_plan": ["Static validation only in smoke mode."],
    "implementation_assessment": ["Source uses the strict Erie structural template."],
    "reviewability_assessment": ["The fixed bilingual header and region comments are preserved."],
    "assumptions": [],
    "known_limitations": []
  }}
}}
```
```verilog path=rtl/erie_adapter.v
{_mock_erie_rtl_source_text(spec).rstrip()}
```
```verilog path=tb/erie_adapter_tb.v
{_mock_erie_rtl_testbench_text(spec, vectors, vector_contract["sha256"]).rstrip()}
```
"""
    extract_response(response, generated_dir)
    return generated_dir


def _run_verilog_only_artifact_gate(base: Path) -> None:
    spec = _rtl_smoke_spec()
    bad_suffix = ".s" + "v"
    bad_paths = [
        "rtl/sidecar.c",
        "rtl/sidecar.cpp",
        "rtl/notes.txt",
        "rtl/metadata.json",
        "rtl/README.md",
        "rtl/erie_adapter_extra.v",
        f"rtl/erie_adapter{bad_suffix}",
        "build/kernel.cfg",
    ]
    for index, rel_path in enumerate(bad_paths, start=1):
        generated_dir = _write_mock_rtl_artifacts(spec, base / "bad-artifacts" / f"case-{index}")
        bad_path = generated_dir / Path(rel_path)
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        if bad_path.suffix.lower() in {".v", bad_suffix}:
            bad_path.write_text("module sidecar; endmodule\n", encoding="utf-8")
        else:
            bad_path.write_text("invalid sidecar artifact\n", encoding="utf-8")
        report = validate_verilog_artifacts(spec, generated_dir, run_external=False)
        assert report["ok"] is False, (rel_path, report)
        if bad_path.suffix.lower() == ".v":
            assert any("Unexpected Verilog artifact" in item["message"] and item["path"] == rel_path for item in report["issues"]), report
        else:
            assert any("Only declared Verilog .v artifacts" in item["message"] and item["path"] == rel_path for item in report["issues"]), report


def _run_static_lint_quality_gate(base: Path) -> None:
    spec = _rtl_smoke_spec()

    generated_dir = _write_mock_rtl_artifacts(spec, base / "static-lint" / "task-function" / "generated")
    rtl_path = generated_dir / "rtl" / "erie_adapter.v"
    rtl_path.write_text(
        rtl_path.read_text(encoding="utf-8")
        + "\nfunction bad_helper; //违规函数用于验证静态lint\n    input bad_in; //函数输入信号声明\n    bad_helper = bad_in; //函数返回输入信号\nendfunction //结束违规函数\n",
        encoding="utf-8",
    )
    report = validate_verilog_artifacts(spec, generated_dir, run_external=False)
    assert report["ok"] is False, report
    assert any(item["tool"] == "erie_static_lint" and "function" in item["message"] for item in report["issues"]), report

    generated_dir = _write_mock_rtl_artifacts(spec, base / "static-lint" / "case-default" / "generated")
    rtl_path = generated_dir / "rtl" / "erie_adapter.v"
    rtl_path.write_text(
        rtl_path.read_text(encoding="utf-8")
        + "\nalways @(*) begin //组合逻辑用于验证case默认分支告警\n    case (i_in_data[0]) //根据输入低位选择临时标志\n        1'b0: flag_tmp = 1'b0; //低位为0时清除临时标志\n        1'b1: flag_tmp = 1'b1; //低位为1时置位临时标志\n    endcase //结束无default分支的case语句\nend //结束组合逻辑\n",
        encoding="utf-8",
    )
    report = validate_verilog_artifacts(spec, generated_dir, run_external=False)
    assert report["ok"] is True, report
    assert any(item["tool"] == "erie_static_lint" and item["severity"] == "warning" and "default" in item["message"] for item in report["issues"]), report

    generated_dir = _write_mock_rtl_artifacts(spec, base / "static-lint" / "raw-gated-clock" / "generated")
    rtl_path = generated_dir / "rtl" / "erie_adapter.v"
    rtl_path.write_text(
        rtl_path.read_text(encoding="utf-8")
        + "\nwire gated_clk; //违规门控时钟信号声明\nassign gated_clk = i_clk & i_in_valid; //通过组合逻辑生成违规门控时钟\n",
        encoding="utf-8",
    )
    report = validate_verilog_artifacts(spec, generated_dir, run_external=False)
    assert report["ok"] is False, report
    assert any(item["tool"] == "erie_static_lint" and "gated clock" in item["message"] for item in report["issues"]), report

    generated_dir = _write_mock_rtl_artifacts(spec, base / "static-lint" / "tb-constructs" / "generated")
    report = validate_verilog_artifacts(spec, generated_dir, run_external=False)
    assert report["ok"] is True, report
    assert not any(item["tool"] == "erie_static_lint" and item["source"] == "testbench_issue" for item in report["issues"]), report

    generated_dir = _write_mock_rtl_artifacts(spec, base / "static-lint" / "erie-style-warning" / "generated")
    rtl_path = generated_dir / "rtl" / "erie_adapter.v"
    rtl_path.write_text(
        "`timescale 1ns / 1ps //声明仿真时间单位\n"
        "module erie_adapter( //声明用于风格告警验证的模块\n"
        "\tinput i_clk, //输入时钟信号\n"
        "\tinput i_rstn, //低有效复位信号\n"
        "\tinput i_in_valid, //输入有效标志\n"
        "\tinput [7:0] i_in_data, //输入数据总线\n"
        "\toutput o_out_valid, //输出有效标志\n"
        "\toutput [7:0] o_out_data //输出数据总线\n"
        "); //结束模块端口声明\n"
        "\tlocalparam ST_IDLE = 1'b0; //空闲状态编码\n"
        "\tlocalparam ST_RUN = 1'b1; //运行状态编码\n"
        "\treg state_reg; //故意使用非标准状态寄存器名\n"
        "\treg [7:0] data_reg; //数据寄存器\n"
        "\tchild core0( //故意使用非_Inst后缀实例名\n"
        "\t\t.i_clk(i_clk), //连接子模块时钟\n"
        "\t\t.i_rstn(i_rstn) //连接子模块复位\n"
        "\t); //结束子模块例化\n"
        "\talways @(*) begin //组合逻辑计算数据寄存器\n"
        "\t\tcase (state_reg) //根据状态寄存器选择输出数据\n"
        "\t\t\tST_IDLE: data_reg = i_in_data; //空闲状态转发输入数据\n"
        "\t\t\tdefault: data_reg = 8'h00; //默认状态清零数据\n"
        "\t\tendcase //结束状态选择\n"
        "\tend //结束组合逻辑\n"
        "\talways @(posedge i_clk or negedge i_rstn) begin //时序逻辑更新状态寄存器\n"
        "\t\tif(i_rstn == 1'b0) state_reg <= ST_IDLE; //复位时进入空闲状态\n"
        "\t\telse state_reg <= ST_RUN; //复位释放后进入运行状态\n"
        "\tend //结束状态寄存器逻辑\n"
        "\tassign o_out_valid = i_in_valid; //转发输入有效标志\n"
        "\tassign o_out_data = i_in_data; //转发输入数据\n"
        "endmodule //结束风格告警验证模块\n",
        encoding="utf-8",
    )
    report = validate_verilog_artifacts(spec, generated_dir, run_external=False)
    assert report["errors"] == 0, report
    messages = [item["message"] for item in report["issues"]]
    assert any("bilingual header" in message for message in messages), messages
    assert any("state_current" in message or "state_next" in message or "ST_" in message for message in messages), messages
    assert any("_Inst" in message for message in messages), messages


def _run_line_comment_quality_gate(base: Path) -> None:
    spec = _rtl_smoke_spec()
    generated_dir = base / "line-comment-gate" / "generated"
    rtl_dir = generated_dir / "rtl"
    tb_dir = generated_dir / "tb"
    rtl_dir.mkdir(parents=True, exist_ok=True)
    tb_dir.mkdir(parents=True, exist_ok=True)
    (rtl_dir / "erie_adapter.v").write_text(
        """module erie_adapter (
    input i_clk,
    input i_rstn,
    input i_in_valid,
    input [7:0] i_in_data,
    output reg o_out_valid,
    output reg [7:0] o_out_data
);
always @(posedge i_clk or negedge i_rstn) begin
    if (i_rstn == 1'b0) begin
        o_out_valid <= 1'b0;
        o_out_data <= 8'd0;
    end else begin
        o_out_valid <= i_in_valid;
        o_out_data <= i_in_data;
    end
end
endmodule
""",
        encoding="utf-8",
    )
    (tb_dir / "erie_adapter_tb.v").write_text(
        """module erie_adapter_tb;
initial begin
    $display("PASS");
    $display("FAIL if any check fails");
end
endmodule
""",
        encoding="utf-8",
    )
    report = validate_verilog_artifacts(spec, generated_dir, run_external=False)
    assert report["ok"] is False, report
    assert report["errors"] >= 1, report
    messages = [issue["message"] for issue in report["issues"]]
    assert any("Every generated Verilog code line must have a Chinese explanatory comment" in message for message in messages), report
    issue_paths = [issue["path"] for issue in report["issues"] if issue.get("path")]
    assert "rtl/erie_adapter.v:2" in issue_paths, report
    assert report["metrics"]["line_comment_gate"]["violations"] >= 1, report

    compliant_dir = _write_mock_rtl_artifacts(spec, base / "line-comment-gate" / "compliant" / "generated")
    compliant_report = validate_verilog_artifacts(spec, compliant_dir, run_external=False)
    assert compliant_report["ok"] is True, compliant_report
    assert compliant_report["warnings"] == 0, compliant_report
    assert compliant_report["metrics"]["line_comment_gate"]["violations"] == 0, compliant_report


def _run_comment_placement_quality_gate(base: Path) -> None:
    spec = _rtl_smoke_spec()
    generic_dir = _write_comment_fixture(
        spec,
        base / "comment-placement-gate" / "generic" / "generated",
        _generic_comment_rtl(),
        _generic_comment_tb(),
    )
    generic_report = validate_verilog_artifacts(spec, generic_dir, run_external=False)
    assert generic_report["ok"] is False, generic_report
    generic_messages = [issue["message"] for issue in generic_report["issues"]]
    assert any("Generic Verilog comment" in message for message in generic_messages), generic_report
    assert generic_report["metrics"]["comment_placement_gate"]["violations"] >= 1, generic_report

    adjacent_dir = _write_comment_fixture(
        spec,
        base / "comment-placement-gate" / "adjacent" / "generated",
        _adjacent_comment_rtl(),
        _adjacent_comment_tb(),
    )
    adjacent_report = validate_verilog_artifacts(spec, adjacent_dir, run_external=False)
    assert adjacent_report["ok"] is False, adjacent_report
    adjacent_messages = [issue["message"] for issue in adjacent_report["issues"]]
    assert any("must use a same-line explanatory comment" in message for message in adjacent_messages), adjacent_report

    missing_end_dir = _write_comment_fixture(
        spec,
        base / "comment-placement-gate" / "missing-end" / "generated",
        _missing_end_comment_rtl(),
        _semantic_comment_tb(),
    )
    missing_end_report = validate_verilog_artifacts(spec, missing_end_dir, run_external=False)
    assert missing_end_report["ok"] is False, missing_end_report
    missing_end_messages = [issue["message"] for issue in missing_end_report["issues"]]
    assert any("End construct comment" in message for message in missing_end_messages), missing_end_report

    macro_dir = _write_comment_fixture(
        spec,
        base / "comment-placement-gate" / "macro" / "generated",
        _bad_multiline_macro_rtl(),
        _semantic_comment_tb(),
    )
    macro_report = validate_verilog_artifacts(spec, macro_dir, run_external=False)
    assert macro_report["ok"] is False, macro_report
    macro_messages = [issue["message"] for issue in macro_report["issues"]]
    assert any("Multiline macro" in message for message in macro_messages), macro_report

    semantic_dir = _write_comment_fixture(
        spec,
        base / "comment-placement-gate" / "semantic" / "generated",
        _semantic_comment_rtl(),
        _semantic_comment_tb(),
    )
    semantic_report = validate_verilog_artifacts(spec, semantic_dir, run_external=False)
    assert semantic_report["ok"] is True, semantic_report
    metrics = semantic_report["metrics"]["comment_placement_gate"]
    for construct in ("module", "macro", "parameter", "port", "signal", "assign", "always", "case", "branch", "instance", "generate", "testbench_task"):
        assert metrics["by_construct"].get(construct, {}).get("checked", 0) >= 1, (construct, metrics)
    assert metrics["violations"] == 0, semantic_report


def _write_comment_fixture(spec: dict, generated_dir: Path, rtl_text: str, tb_text: str) -> Path:
    generated_dir.mkdir(parents=True, exist_ok=True)
    rtl_path = generated_dir / spec["outputs"][0]["path"]
    tb_path = generated_dir / spec["outputs"][1]["path"]
    rtl_path.parent.mkdir(parents=True, exist_ok=True)
    tb_path.parent.mkdir(parents=True, exist_ok=True)
    rtl_path.write_text(rtl_text, encoding="utf-8")
    tb_path.write_text(tb_text, encoding="utf-8")
    return generated_dir


def _generic_comment_rtl() -> str:
    return """module erie_adapter( //泛泛模块注释
\tinput i_clk, //泛泛逐行中文注释
\tinput i_rstn, //泛泛逐行中文注释
\tinput i_in_valid, //泛泛逐行中文注释
\tinput [7:0] i_in_data, //泛泛逐行中文注释
\toutput o_out_valid, //泛泛逐行中文注释
\toutput [7:0] o_out_data //泛泛逐行中文注释
); //泛泛逐行中文注释
\treg state_current; //泛泛逐行中文注释
\treg state_next; //泛泛逐行中文注释
\treg [7:0] reg_data; //泛泛逐行中文注释
\treg reg_valid; //泛泛逐行中文注释
\tlocalparam ST_IDLE = 1'b0; //泛泛逐行中文注释
\tlocalparam ST_RUN = 1'b1; //泛泛逐行中文注释
\tassign o_out_valid = reg_valid; //泛泛逐行中文注释
\tassign o_out_data = reg_data; //泛泛逐行中文注释
\talways @(posedge i_clk or negedge i_rstn) begin //泛泛逐行中文注释
\t\tif (!i_rstn) begin //泛泛逐行中文注释
\t\t\tstate_current <= ST_IDLE; //泛泛逐行中文注释
\t\t\treg_data <= 8'd0; //泛泛逐行中文注释
\t\t\treg_valid <= 1'b0; //泛泛逐行中文注释
\t\tend else begin //泛泛逐行中文注释
\t\t\tstate_current <= state_next; //泛泛逐行中文注释
\t\t\treg_data <= i_in_data; //泛泛逐行中文注释
\t\t\treg_valid <= i_in_valid; //泛泛逐行中文注释
\t\tend //泛泛逐行中文注释
\tend //泛泛逐行中文注释
\talways @(*) begin //泛泛逐行中文注释
\t\tstate_next = state_current; //泛泛逐行中文注释
\t\tcase (state_current) //泛泛逐行中文注释
\t\t\tST_IDLE: if (i_in_valid) state_next = ST_RUN; //泛泛逐行中文注释
\t\t\tST_RUN: state_next = ST_RUN; //泛泛逐行中文注释
\t\t\tdefault: state_next = ST_IDLE; //泛泛逐行中文注释
\t\tendcase //泛泛逐行中文注释
\tend //泛泛逐行中文注释
endmodule //泛泛逐行中文注释
"""


def _generic_comment_tb() -> str:
    return """module erie_adapter_tb; //泛泛测试平台注释
\treg i_clk; //泛泛逐行中文注释
\treg i_rstn; //泛泛逐行中文注释
\tinitial begin //泛泛逐行中文注释
\t\t$display("PASS"); //泛泛逐行中文注释
\t\t$display("FAIL if mismatch"); //泛泛逐行中文注释
\tend //泛泛逐行中文注释
endmodule //泛泛逐行中文注释
"""


def _adjacent_comment_rtl() -> str:
    return """module erie_adapter( //模块: erie_adapter - 一级流水数据转发
\tinput i_clk,
\t//复位端口: i_rstn - 低电平清空流水状态
\tinput i_rstn,
\tinput i_in_valid, //输入端口: i_in_valid - 输入数据有效
\tinput [7:0] i_in_data, //输入端口: i_in_data - 8位输入数据
\toutput o_out_valid, //输出端口: o_out_valid - 输出数据有效
\toutput [7:0] o_out_data //输出端口: o_out_data - 8位输出数据
); //端口列表结束: erie_adapter
\tassign o_out_valid = i_in_valid; //组合连线: 输出有效直接跟随输入有效
\tassign o_out_data = i_in_data; //组合连线: 输出数据直接跟随输入数据
endmodule //结束模块: erie_adapter
"""


def _adjacent_comment_tb() -> str:
    return """module erie_adapter_tb; //测试平台: erie_adapter_tb - 相邻注释负向样例
\tinitial begin //测试阶段: 输出PASS/FAIL占位结果
\t\t$display("PASS"); //结果输出: 打印通过标记
\t\t$display("FAIL if mismatch"); //结果输出: 保留失败标记供门禁识别
\tend //结束测试阶段: 初始检查
endmodule //结束测试平台: erie_adapter_tb
"""


def _missing_end_comment_rtl() -> str:
    return _semantic_comment_rtl().replace("endmodule //结束模块: erie_adapter\n", "endmodule //模块结束\n")


def _bad_multiline_macro_rtl() -> str:
    return _semantic_comment_rtl().replace(
        "//宏定义: ERIE_PACK_VALID - 把有效位和数据打包为总线片段\n`define ERIE_PACK_VALID(valid, data) {valid, data} //宏定义: ERIE_PACK_VALID - 组合有效位和数据载荷\n",
        "`define ERIE_PACK_VALID(valid, data) {valid, \\\n\tdata} //多行宏注释放在续行尾部会破坏绑定\n",
    )


def _semantic_comment_rtl() -> str:
    return """//宏定义: ERIE_PACK_VALID - 把有效位和数据打包为总线片段
`define ERIE_PACK_VALID(valid, data) {valid, data} //宏定义: ERIE_PACK_VALID - 组合有效位和数据载荷
module erie_adapter #( //模块: erie_adapter - 一级流水数据转发
\tparameter C_DATA_WIDTH = 8 //参数: C_DATA_WIDTH - 数据通路宽度
)( //端口列表: erie_adapter - 时钟复位、输入通道和输出通道
\t//端口分组: 时钟与复位
\tinput i_clk, //输入端口: i_clk - 上升沿驱动流水寄存器
\tinput i_rstn, //输入端口: i_rstn - 低电平异步复位
\t//端口分组: 输入数据通道
\tinput i_in_valid, //输入端口: i_in_valid - 输入数据有效
\tinput [C_DATA_WIDTH - 1:0] i_in_data, //输入端口: i_in_data - 输入数据载荷
\t//端口分组: 输出数据通道
\toutput o_out_valid, //输出端口: o_out_valid - 输出数据有效
\toutput [C_DATA_WIDTH - 1:0] o_out_data //输出端口: o_out_data - 输出数据载荷
); //端口列表结束: erie_adapter

\tlocalparam ST_IDLE = 1'b0; //状态参数: ST_IDLE - 等待有效输入
\tlocalparam ST_RUN = 1'b1; //状态参数: ST_RUN - 保持流水运行
\tgenvar gen_i; //生成变量: gen_i - 生成块索引
\treg state_current; //寄存器: state_current - 当前FSM状态
\treg state_next; //寄存器: state_next - 下一拍FSM状态
\treg [C_DATA_WIDTH - 1:0] reg_data; //寄存器: reg_data - 输出数据流水寄存器
\treg reg_valid; //寄存器: reg_valid - 输出有效流水寄存器
\twire [C_DATA_WIDTH:0] packed_status; //连线: packed_status - 有效位和数据的组合视图
\tinteger idx; //变量: idx - 测试综合兼容循环索引

\tassign packed_status = `ERIE_PACK_VALID(reg_valid, reg_data); //组合连线: 打包有效位和数据便于观测
\tassign o_out_valid = packed_status[C_DATA_WIDTH]; //组合连线: 输出有效来自打包总线最高位
\tassign o_out_data = packed_status[C_DATA_WIDTH - 1:0]; //组合连线: 输出数据来自打包总线低位

\tgenerate if (C_DATA_WIDTH > 0) begin: gen_passthrough //生成块: gen_passthrough - 保留参数合法性检查结构
\t\twire gen_width_ok; //连线: gen_width_ok - 标记生成分支已启用
\t\tassign gen_width_ok = 1'b1; //组合连线: 生成分支启用时恒为真
\tend else begin: gen_zero_width //生成块: gen_zero_width - 非法宽度旁路分支
\t\twire gen_width_bad; //连线: gen_width_bad - 标记非法宽度分支
\t\tassign gen_width_bad = 1'b0; //组合连线: 非法宽度分支恒为假
\tend endgenerate //结束生成块: 数据宽度合法性分支

\t//时序块: 状态寄存器与数据流水寄存器
\talways @(posedge i_clk or negedge i_rstn) begin //时序逻辑: 复位或时钟沿更新状态和数据寄存器
\t\tif (!i_rstn) begin //复位分支: 清空状态和输出流水寄存器
\t\t\tstate_current <= ST_IDLE; //复位动作: 当前状态回到空闲
\t\t\treg_data <= {C_DATA_WIDTH{1'b0}}; //复位动作: 输出数据清零
\t\t\treg_valid <= 1'b0; //复位动作: 输出有效清零
\t\tend else begin //运行分支: 捕获输入数据并推进状态
\t\t\tstate_current <= state_next; //时序更新: 当前状态接收次态
\t\t\treg_data <= i_in_data; //时序更新: 输入数据进入输出流水
\t\t\treg_valid <= i_in_valid; //时序更新: 输入有效进入输出有效寄存器
\t\tend //结束分支: 状态和数据寄存器更新
\tend //结束时序块: 状态寄存器与数据流水寄存器

\t//组合块: FSM次态逻辑
\talways @(*) begin //组合逻辑: 根据当前状态和输入有效计算次态
\t\tstate_next = state_current; //默认赋值: 保持当前状态防止锁存
\t\tcase (state_current) //状态选择: 根据当前状态选择转移路径
\t\t\tST_IDLE: begin //状态分支: 空闲状态等待有效输入
\t\t\t\tif (i_in_valid) state_next = ST_RUN; //条件转移: 有效输入到来后进入运行状态
\t\t\tend //结束状态分支: 空闲状态
\t\t\tST_RUN: begin //状态分支: 运行状态持续保持
\t\t\t\tstate_next = ST_RUN; //状态保持: 持续运行
\t\t\tend //结束状态分支: 运行状态
\t\t\tdefault: begin //默认分支: 非法状态回到空闲
\t\t\t\tstate_next = ST_IDLE; //默认转移: 回到空闲状态
\t\t\tend //结束默认分支: 非法状态恢复
\t\tendcase //结束状态选择: state_current
\tend //结束组合块: FSM次态逻辑
endmodule //结束模块: erie_adapter
"""


def _semantic_comment_tb() -> str:
    return """module erie_adapter_tb; //测试平台: erie_adapter_tb - 验证一级流水数据转发
\treg i_clk; //测试信号: i_clk - 驱动DUT时钟
\treg i_rstn; //测试信号: i_rstn - 驱动DUT低有效复位
\treg i_in_valid; //测试信号: i_in_valid - 驱动输入有效
\treg [7:0] i_in_data; //测试信号: i_in_data - 驱动输入数据
\twire o_out_valid; //观测信号: o_out_valid - DUT输出有效
\twire [7:0] o_out_data; //观测信号: o_out_data - DUT输出数据

\t//实例: DUT_Inst - 被测一级流水模块
\terie_adapter DUT_Inst( //模块实例: erie_adapter/DUT_Inst - 连接测试平台信号
\t\t.i_clk(i_clk), //端口映射: i_clk 连接测试时钟
\t\t.i_rstn(i_rstn), //端口映射: i_rstn 连接测试复位
\t\t.i_in_valid(i_in_valid), //端口映射: i_in_valid 连接输入有效
\t\t.i_in_data(i_in_data), //端口映射: i_in_data 连接输入数据
\t\t.o_out_valid(o_out_valid), //端口映射: o_out_valid 连接输出有效观测
\t\t.o_out_data(o_out_data) //端口映射: o_out_data 连接输出数据观测
\t); //结束实例: DUT_Inst

\t//测试任务: apply_reset - 施加低有效复位并释放
\ttask apply_reset; //测试任务: apply_reset - 初始化DUT状态
\t\tbegin //任务过程: 复位时序开始
\t\t\ti_rstn = 1'b0; //激励: 拉低复位
\t\t\t#20; //延时: 保持两个时钟周期
\t\t\ti_rstn = 1'b1; //激励: 释放复位
\t\tend //结束任务过程: apply_reset
\tendtask //结束测试任务: apply_reset

\tinitial begin //测试阶段: 时钟初始化与翻转
\t\ti_clk = 1'b0; //激励: 初始化时钟为低
\t\tforever #5 i_clk = ~i_clk; //激励: 产生10ns周期时钟
\tend //结束测试阶段: 时钟生成

\tinitial begin //测试阶段: 复位、激励和结果检查
\t\ti_in_valid = 1'b0; //激励: 初始化输入有效为低
\t\ti_in_data = 8'd0; //激励: 初始化输入数据为零
\t\tapply_reset; //任务调用: 执行复位流程
\t\ti_in_valid = 1'b1; //激励: 拉高输入有效
\t\ti_in_data = 8'hA5; //激励: 发送测试数据A5
\t\t#20; //延时: 等待流水输出稳定
\t\tif (o_out_data !== 8'hA5) begin //检查: 输出数据必须匹配输入数据
\t\t\t$display("FAIL"); //结果输出: 数据不匹配时报失败
\t\tend else begin //检查分支: 输出数据匹配
\t\t\t$display("PASS"); //结果输出: 数据匹配时报通过
\t\tend //结束检查: 输出数据比较
\t\t$finish; //仿真控制: 结束测试
\tend //结束测试阶段: 复位、激励和结果检查
endmodule //结束测试平台: erie_adapter_tb
"""


def _run_reference_loading_gate() -> None:
    lint_checklist = (ROOT / "references" / "lint-checklist.md").read_text(encoding="utf-8")
    assert "Category A: Synthesis Errors" in lint_checklist, lint_checklist
    assert "CDC" in lint_checklist and "RDC" in lint_checklist, lint_checklist
    assert "verilator" in lint_checklist and "verible" in lint_checklist and "slang" in lint_checklist, lint_checklist

    testbench_patterns = (ROOT / "references" / "testbench-patterns.md").read_text(encoding="utf-8")
    assert "Simple Directed Testbench" in testbench_patterns, testbench_patterns
    assert "Self-Checking Testbench" in testbench_patterns, testbench_patterns
    assert "class-based verification environments" in testbench_patterns, testbench_patterns
    assert "out of the current skill boundary" in testbench_patterns, testbench_patterns

    comment_placement = (ROOT / "references" / "verilog-comment-placement.md").read_text(encoding="utf-8")
    assert "Placement Matrix" in comment_placement, comment_placement
    assert "Multiline backslash macros" in comment_placement, comment_placement
    assert "Testbenches may use helpers" in comment_placement, comment_placement


def _run_use_case_repair_prompt_gate() -> None:
    spec = json.loads((ROOT / "assets" / "examples" / "use_case_templates" / "spi_adc.json").read_text(encoding="utf-8"))
    repair_prompt = generate_repair_prompt(
        "current_module_issue: placeholder output",
        spec,
        validation_json={"issues": [{"severity": "error", "source": "current_module_issue", "message": "placeholder output"}]},
    )
    assert "## Use-case template context" in repair_prompt, repair_prompt
    assert '"id": "spi_adc"' in repair_prompt, repair_prompt


def _run_ref_style_asset_gate() -> None:
    style_reference = (ROOT / "references" / "erie-ref-style.md").read_text(encoding="utf-8")
    assert "Naming Rules" in style_reference, style_reference
    assert "State Machine Rules" in style_reference, style_reference
    assert "Module Instantiation Rules" in style_reference, style_reference
    assert "AXI/AXIS/APB/AHB" in style_reference, style_reference
    assert "convolution-domain examples" in style_reference.lower(), style_reference
    assert "not universal defaults" in style_reference.lower(), style_reference

    style_assets = {
        "rtl_header_bilingual.tpl",
        "rtl_region_order.tpl",
        "rtl_fsm.tpl",
        "rtl_instantiation.tpl",
        "rtl_bus_grouping.tpl",
    }
    asset_dir = ROOT / "assets" / "style_templates"
    found = {item.name for item in asset_dir.iterdir() if item.is_file()}
    assert style_assets.issubset(found), found


def _run_verilog_lint_script_gate(base: Path) -> None:
    rtl_path = base / "lint-script" / "bad_rtl.v"
    rtl_path.parent.mkdir(parents=True, exist_ok=True)
    rtl_path.write_text(
        "module bad_rtl(input wire i_clk, input wire i_rstn, output reg o_flag);\n"
        "always @(i_clk) begin\n"
        "    o_flag = 1'b0;\n"
        "end\n"
        "function bad_helper;\n"
        "    input bad_in;\n"
        "    bad_helper = bad_in;\n"
        "endfunction\n"
        "endmodule\n",
        encoding="utf-8",
    )
    lint_cli = subprocess.run(
        [sys.executable, "scripts/verilog_lint.py", str(rtl_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert lint_cli.returncode != 0, lint_cli.stdout
    lint_output = lint_cli.stdout + lint_cli.stderr
    assert "NO_TASK_FUNCTION" in lint_output, lint_output
    assert "ALWAYS_STAR" in lint_output, lint_output
    assert "UTF-8" in lint_output, lint_output

    tb_path = base / "lint-script" / "good_tb_tb.v"
    tb_path.write_text(
        "module good_tb_tb;\n"
        "initial begin\n"
        "    #10;\n"
        "    $display(\"PASS\");\n"
        "end\n"
        "endmodule\n",
        encoding="utf-8",
    )
    tb_cli = subprocess.run(
        [sys.executable, "scripts/verilog_lint.py", str(tb_path), "--mode", "tb"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tb_cli.returncode == 0, tb_cli.stdout + tb_cli.stderr


def _run_tb_generator_script_gate(base: Path) -> None:
    rtl_path = base / "tb-generator" / "sample_module.v"
    rtl_path.parent.mkdir(parents=True, exist_ok=True)
    rtl_path.write_text(
        "module sample_module( //模块: sample_module - 转发输入数据到输出\n"
        "    input wire i_clk, //输入端口: i_clk - 驱动测试平台时钟识别\n"
        "    input wire i_rstn, //输入端口: i_rstn - 驱动测试平台复位识别\n"
        "    input wire [7:0] i_data, //输入端口: i_data - 8位样例输入数据\n"
        "    output reg [7:0] o_data //输出端口: o_data - 8位寄存输出数据\n"
        "); //端口列表结束: sample_module\n"
        "always @(posedge i_clk or negedge i_rstn) begin //时序逻辑: 在时钟沿更新输出数据寄存器\n"
        "    if (!i_rstn) begin //复位分支: 清空输出数据寄存器\n"
        "        o_data <= 8'd0; //复位动作: 输出数据清零\n"
        "    end else begin //运行分支: 采样输入数据\n"
        "        o_data <= i_data; //时序更新: 输出数据接收输入数据\n"
        "    end //结束分支: 输出寄存器更新\n"
        "end //结束时序逻辑: 输出数据寄存器\n"
        "endmodule //结束模块: sample_module\n",
        encoding="utf-8",
    )
    out_path = base / "tb-generator" / "tb_sample_module.v"
    tb_cli = subprocess.run(
        [sys.executable, "scripts/tb_generator.py", str(rtl_path), "--output", str(out_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tb_cli.returncode == 0, tb_cli.stdout + tb_cli.stderr
    tb_text = out_path.read_text(encoding="utf-8")
    assert "Reference vector hash placeholder" in tb_text, tb_text
    assert "PASS" in tb_text and "FAIL" in tb_text, tb_text
    assert "always #(CLK_PERIOD/2)" in tb_text, tb_text
    assert "task apply_reset;" in tb_text, tb_text
    validation_root = base / "tb-generator" / "validation"
    (validation_root / "rtl").mkdir(parents=True, exist_ok=True)
    (validation_root / "tb").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rtl_path, validation_root / "rtl" / "sample_module.v")
    shutil.copyfile(out_path, validation_root / "tb" / "tb_sample_module.v")
    tb_report = validate_verilog_artifacts(_tb_generator_spec("sample_module"), validation_root, run_external=False)
    assert tb_report["ok"] is True, tb_report

    body_style_path = base / "tb-generator" / "body_style_module.v"
    body_style_path.write_text(
        "module body_style_module(i_clk, i_rstn, i_data, o_data); //模块: body_style_module - body风格端口声明样例\n"
        "input i_clk; //输入端口: i_clk - 测试时钟\n"
        "input i_rstn; //输入端口: i_rstn - 测试复位\n"
        "input [15:0] i_data; //输入端口: i_data - 16位输入数据\n"
        "output [15:0] o_data; //输出端口: o_data - 16位输出数据\n"
        "assign o_data = i_data; //组合连线: 输出数据跟随输入数据\n"
        "endmodule //结束模块: body_style_module\n",
        encoding="utf-8",
    )
    body_out = base / "tb-generator" / "tb_body_style_module.v"
    body_cli = subprocess.run(
        [sys.executable, "scripts/tb_generator.py", str(body_style_path), "--output", str(body_out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert body_cli.returncode == 0, body_cli.stdout + body_cli.stderr
    body_text = body_out.read_text(encoding="utf-8")
    assert "[15:0] i_data;" in body_text, body_text
    assert "module tb_body_style_module;" in body_text, body_text


def _tb_generator_spec(module_name: str) -> dict:
    return {
        "name": module_name,
        "target": "rtl",
        "design_requirements": {
            "target": "rtl",
            "pipeline_required": False,
            "streamability": "non_streamable",
            "interface_family": "native",
            "interface_profile": {},
            "confirmed_by_user": True,
            "confirmation_notes": "Validate tb_generator semantic comment placement.",
        },
        "streamability": "non_streamable",
        "interface_family": "native",
        "interface_profile": {},
        "pipeline_required": False,
        "codegen_plan_required": True,
        "description": "tb_generator comment placement smoke example.",
        "interfaces": {
            "ports": [
                {"name": "i_clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "i_rstn", "direction": "input", "width": 1, "role": "reset"},
                {"name": "i_data", "direction": "input", "width": 8},
                {"name": "o_data", "direction": "output", "width": 8},
            ]
        },
        "behavior": ["Register input data to output data."],
        "clock": {"name": "i_clk", "edge": "posedge", "frequency_mhz": 100},
        "reset": {"name": "i_rstn", "active": "low", "synchronous": False},
        "constraints": ["Use synthesizable Verilog-2001."],
        "outputs": [
            {"path": f"rtl/{module_name}.v", "kind": "source", "language": "verilog"},
            {"path": f"tb/tb_{module_name}.v", "kind": "testbench", "language": "verilog"},
        ],
        "notes": [],
        "subfunctions": [],
        "workflow": {},
        "performance": {},
    }


def _run_dependency_config_gate(settings: dict) -> None:
    dependency_settings = skill_dependency_settings(settings)
    routing_settings = fpga_developer_routing_settings(settings)
    workspace_root = workspace_runtime.require_workspace_root(purpose="smoke dependency config gate")
    assert dependency_settings["install_policy"] == "ask_each_missing", dependency_settings
    assert dependency_settings["adaptation_policy"] == "required", dependency_settings
    assert str(dependency_settings["state_path"]).endswith("dependency-state.json"), dependency_settings
    assert str(routing_settings["state_path"]).endswith("dependency-state.json"), routing_settings
    assert routing_settings["selection_policy"] == "ask_on_first_fpga_workflow", routing_settings
    assert routing_settings["persist_selection"] is True, routing_settings
    assert routing_settings["fpga_agent_required_when_developer_present"] is False, routing_settings
    assert routing_settings["vendors"]["amd_xilinx"]["skills"] == ["vivado-developer", "vitis-developer"], routing_settings
    assert routing_settings["vendors"]["pangomicro"]["skills"] == ["pds-developer"], routing_settings
    assert Path(remote_setting(settings, "server_list")) == workspace_root / ".erie-verilog-generator-state" / "server_list.local.json", settings["remote"]
    assert "server" not in settings["remote"], settings["remote"]
    assert "server_name" not in settings["remote"], settings["remote"]
    assert "SKILL.md" not in settings["validation"]["legacy_term_allowlist"], settings["validation"]["legacy_term_allowlist"]
    assert "config/defaults.json" not in settings["validation"]["legacy_term_allowlist"], settings["validation"]["legacy_term_allowlist"]
    required = dependency_settings["required"]
    recommended = dependency_settings["recommended"]
    assert {item["url"] for item in required} == {
        "https://github.com/Eriemon/remote-ssh.git",
        "https://github.com/adeleempurpled290/FPGA-Agent-skills.git",
    }, required
    assert {item["url"] for item in recommended} == {
        "https://github.com/obra/superpowers.git",
        "https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git",
    }, recommended
    fpga = next(item for item in required if item["id"] == "fpga-agent-skills")
    remote = next(item for item in required if item["id"] == "erie-remote-ssh")
    assert remote["install_specs"] == [{"skill": "erie-remote-ssh", "source_path": ".", "dest_name": "erie-remote-ssh"}], remote
    assert fpga["skills"] == [
        "vivado-tcl",
        "vivado-sim",
        "vivado-synth",
        "vivado-impl",
        "vivado-analysis",
        "vivado-constraints",
        "vivado-debug",
        "vitis-hls-synthesis",
    ], fpga
    assert fpga["install_specs"][-1] == {"skill": "vitis-hls-synthesis", "source_path": "vitis-hls-synthesis"}, fpga


if __name__ == "__main__":
    raise SystemExit(main())
