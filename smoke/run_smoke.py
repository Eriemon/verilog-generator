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

    development_doc = (REPO_ROOT / "docs" / "development" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    assert "## Current Progress" in development_doc, development_doc

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
        "module sample_module(\n"
        "    input wire i_clk,\n"
        "    input wire i_rstn,\n"
        "    input wire [7:0] i_data,\n"
        "    output wire [7:0] o_data\n"
        ");\n"
        "assign o_data = i_data;\n"
        "endmodule\n",
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

    body_style_path = base / "tb-generator" / "body_style_module.v"
    body_style_path.write_text(
        "module body_style_module(i_clk, i_rstn, i_data, o_data);\n"
        "input i_clk;\n"
        "input i_rstn;\n"
        "input [15:0] i_data;\n"
        "output [15:0] o_data;\n"
        "assign o_data = i_data;\n"
        "endmodule\n",
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
