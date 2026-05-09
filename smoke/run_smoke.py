"""Standalone smoke validator for the Verilog-only skill."""

from __future__ import annotations

import argparse
import os
import json
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.verilog_adapter import (  # noqa: E402
    render_verilog_prompt,
    run_verilog_workflow,
    validate_verilog_artifacts,
)
from runtime.verilog_generator.extractor import extract_response  # noqa: E402
from runtime.verilog_generator.config import load_settings, path_setting, remote_setting, skill_dependency_settings  # noqa: E402
from runtime.verilog_generator.interface_templates import list_interface_templates, resolve_interface_template  # noqa: E402
from runtime.verilog_generator.model_provider import _mock_erie_rtl_source_text, _mock_erie_rtl_testbench_text, _mock_vectors  # noqa: E402
from runtime.verilog_generator.requirements import apply_requirement_defaults, build_codegen_plan, validate_requirement_confirmation  # noqa: E402
from runtime.verilog_generator.vectors import vector_contract_from_payload  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Verilog-only skill smoke checks.")
    parser.add_argument("--settings", type=Path, help="Optional settings JSON path.")
    args = parser.parse_args(argv)
    settings = load_settings(args.settings)
    base = path_setting(settings, "smoke_dir")
    example_spec = path_setting(settings, "example_spec")
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    try:
        _run_markdown_ascii_gate()
        _run_skill_metadata_gate()
        _run_mock_workflow(base, example_spec)
        _run_invalid_response(base)
        _run_target_rejection(base)
        _run_dialect_rejection(base)
        _run_prompt_extract_validate(base)
        _run_interface_bus_policy_gate()
        _run_interface_template_gate(base)
        _run_verilog_only_artifact_gate(base)
        _run_dependency_config_gate(settings)
        _run_dependency_manager_gate(base, settings)
        _run_remote_selection_preflight_gate(base)
        _run_remote_vivado_activation_gate()
        _run_remote_retention_policy_gate()
        _run_remote_toolchain_selection_gate(base)
        _run_remote_fixture_gate(base)
        _run_remote_report_gate(base)
        _run_simulator_priority_gate(base)
        _run_toolchain_blocking_gate(base, example_spec)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("Verilog-only smoke checks passed.")
    return 0


def _run_markdown_ascii_gate() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.md"):
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
    assert description.startswith("Use when"), description
    for keyword in (
        "Chinese-language Verilog development requests",
        "Verilog design",
        "Verilog modification",
        "Verilog debug",
        "RTL development",
        "RTL design",
        "RTL modification",
        "RTL debug",
        "RTL troubleshooting",
    ):
        assert keyword in description, keyword
    assert "Generate, prompt, run, resume" not in description, description

    openai_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'short_description: "Generate, modify, debug, and validate Verilog RTL."' in openai_yaml
    assert 'default_prompt: "Use $erie-verilog-generator to design, modify, debug, and validate synthesizable Verilog RTL."' in openai_yaml
    assert "allow_implicit_invocation: true" in openai_yaml


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
    assert "state-task processing" not in prompt_text
    assert "main-task processing" not in prompt_text
    assert "AXI-Stream for streaming data" in prompt_text
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


def _run_dependency_config_gate(settings: dict) -> None:
    dependency_settings = skill_dependency_settings(settings)
    assert dependency_settings["install_policy"] == "ask_each_missing", dependency_settings
    assert dependency_settings["adaptation_policy"] == "required", dependency_settings
    assert str(dependency_settings["state_path"]).endswith("dependency-state.json"), dependency_settings
    assert str(settings["remote"]["server_list"]).endswith(".codex/erie-verilog-generator/server_list.local.json") or str(settings["remote"]["server_list"]).endswith(".codex\\erie-verilog-generator\\server_list.local.json"), settings["remote"]
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


def _run_dependency_manager_gate(base: Path, settings: dict) -> None:
    module = _load_dependency_manager_module()
    empty_root = base / "empty-skills"
    empty_root.mkdir()
    empty_state = base / "empty-state.json"
    empty_report = module.check_dependencies(settings, skills_root=empty_root, plugin_cache=base / "empty-plugin-cache", state_path=empty_state)
    assert empty_report["ok"] is False, empty_report
    assert empty_report["required_ok"] is False, empty_report
    assert empty_report["recommended_ok"] is False, empty_report
    assert [item["id"] for item in empty_report["missing_required"]] == ["erie-remote-ssh", "fpga-agent-skills"], empty_report
    assert [item["id"] for item in empty_report["missing_recommended"]] == ["superpowers", "context-engineering"], empty_report
    prompt = module.prompt_for_missing(empty_report)
    assert "required dependency" in prompt, prompt
    assert "recommended dependency" in prompt, prompt
    assert "https://github.com/Eriemon/remote-ssh.git" in prompt, prompt
    module.record_skip(settings, "superpowers", state_path=empty_state)
    skipped_report = module.check_dependencies(settings, skills_root=empty_root, plugin_cache=base / "empty-plugin-cache", state_path=empty_state)
    assert "superpowers" not in [item["id"] for item in skipped_report["missing_recommended"]], skipped_report
    assert "superpowers" in skipped_report["skipped_recommended"], skipped_report
    changed_settings = json.loads(json.dumps(settings))
    changed_superpowers = next(item for item in changed_settings["skill_dependencies"]["recommended"] if item["id"] == "superpowers")
    changed_superpowers["skills"].append("verification-before-completion")
    changed_report = module.check_dependencies(changed_settings, skills_root=empty_root, plugin_cache=base / "empty-plugin-cache", state_path=empty_state)
    assert "superpowers" in [item["id"] for item in changed_report["missing_recommended"]], changed_report
    fake_installer = base / "fake-install-skill-from-github.py"
    fake_installer.write_text("# fake installer\n", encoding="utf-8")
    old_run = module.subprocess.run
    commands: list[list[str]] = []

    def fake_run(command, check):
        commands.append([str(item) for item in command])
        return subprocess.CompletedProcess(command, 0)

    module.subprocess.run = fake_run
    try:
        installed_remote = module.install_missing(settings, empty_report, "erie-remote-ssh", installer=fake_installer)
    finally:
        module.subprocess.run = old_run
    assert installed_remote["installed"] == ["erie-remote-ssh"], installed_remote
    assert len(commands) == 1, commands
    assert commands[0][-4:] == ["--path", ".", "--name", "erie-remote-ssh"], commands
    commands = []
    module.subprocess.run = fake_run
    try:
        installed_context = module.install_missing(settings, empty_report, "context-engineering", installer=fake_installer)
    finally:
        module.subprocess.run = old_run
    assert "advanced-evaluation" in installed_context["installed"], installed_context
    assert "tool-design" in installed_context["installed"], installed_context
    assert all("--path" in command and "--name" not in command for command in commands), commands
    assert any("skills/context-fundamentals" in command for command in commands), commands

    skills_root = base / "installed-skills"
    _write_fake_skill(skills_root / "erie-remote-ssh")
    remote_helper = skills_root / "erie-remote-ssh" / "scripts" / "remote_ssh.py"
    remote_helper.parent.mkdir(parents=True, exist_ok=True)
    remote_helper.write_text("# fake remote helper\n", encoding="utf-8")
    remote_settings = skills_root / "erie-remote-ssh" / "config" / "defaults.json"
    remote_settings.parent.mkdir(parents=True, exist_ok=True)
    remote_settings.write_text('{"version": 1}\n', encoding="utf-8")
    for name in (
        "vivado-tcl",
        "vivado-sim",
        "vivado-synth",
        "vivado-impl",
        "vivado-analysis",
        "vivado-constraints",
        "vivado-debug",
        "context-engineering",
    ):
        _write_fake_skill(skills_root / name)
    plugin_skills = base / "plugins" / "cache" / "superpowers-dev" / "superpowers" / "1.0.0" / "skills"
    for name in ("using-superpowers", "writing-plans", "executing-plans", "test-driven-development"):
        _write_fake_skill(plugin_skills / name)
    full_state = base / "full-state.json"
    partial_report = module.check_dependencies(settings, skills_root=skills_root, plugin_cache=base / "plugins" / "cache", state_path=full_state)
    missing_fpga = next(item for item in partial_report["missing_required"] if item["id"] == "fpga-agent-skills")
    assert missing_fpga["missing_skills"] == ["vitis-hls-synthesis"], partial_report
    commands = []
    module.subprocess.run = fake_run
    try:
        installed_fpga = module.install_missing(settings, partial_report, "fpga-agent-skills", installer=fake_installer)
    finally:
        module.subprocess.run = old_run
    assert installed_fpga["installed"] == ["vitis-hls-synthesis"], installed_fpga
    assert len(commands) == 1 and commands[0][-2:] == ["--path", "vitis-hls-synthesis"], commands
    _write_fake_skill(skills_root / "vitis-hls-synthesis")
    full_report = module.check_dependencies(settings, skills_root=skills_root, plugin_cache=base / "plugins" / "cache", state_path=full_state)
    assert full_report["ok"] is True, full_report
    upstream_context_root = base / "upstream-context-skills"
    for path in skills_root.iterdir():
        if path.is_dir() and path.name != "context-engineering":
            shutil.copytree(path, upstream_context_root / path.name)
    for name in (
        "advanced-evaluation",
        "bdi-mental-states",
        "context-compression",
        "context-degradation",
        "context-fundamentals",
        "context-optimization",
        "evaluation",
        "filesystem-context",
        "hosted-agents",
        "latent-briefing",
        "memory-systems",
        "multi-agent-patterns",
        "project-development",
        "tool-design",
    ):
        _write_fake_skill(upstream_context_root / name)
    upstream_report = module.check_dependencies(settings, skills_root=upstream_context_root, plugin_cache=base / "plugins" / "cache", state_path=base / "upstream-state.json")
    upstream_context = next(item for item in upstream_report["recommended"] if item["id"] == "context-engineering")
    assert upstream_context["present"] is True, upstream_context
    assert "context-fundamentals" in upstream_context["selected_skill_set"], upstream_context
    partial_context_root = base / "partial-context-skills"
    for path in skills_root.iterdir():
        if path.is_dir() and path.name != "context-engineering":
            shutil.copytree(path, partial_context_root / path.name)
    for name in (
        "advanced-evaluation",
        "bdi-mental-states",
        "context-compression",
        "context-degradation",
        "context-fundamentals",
        "context-optimization",
        "evaluation",
        "filesystem-context",
        "hosted-agents",
        "latent-briefing",
        "memory-systems",
        "multi-agent-patterns",
        "project-development",
    ):
        _write_fake_skill(partial_context_root / name)
    partial_context_report = module.check_dependencies(settings, skills_root=partial_context_root, plugin_cache=base / "plugins" / "cache", state_path=base / "partial-context-state.json")
    partial_context = next(item for item in partial_context_report["missing_recommended"] if item["id"] == "context-engineering")
    assert partial_context["missing_skills"] == ["tool-design"], partial_context
    commands = []
    module.subprocess.run = fake_run
    try:
        installed_partial_context = module.install_missing(settings, partial_context_report, "context-engineering", installer=fake_installer)
    finally:
        module.subprocess.run = old_run
    assert installed_partial_context["installed"] == ["tool-design"], installed_partial_context
    assert len(commands) == 1 and "skills/tool-design" in commands[0], commands
    adapted = module.adapt_dependencies(settings, skills_root=skills_root, plugin_cache=base / "plugins" / "cache", state_path=full_state)
    assert adapted["adapted"] == ["erie-remote-ssh"], adapted
    state = json.loads(full_state.read_text(encoding="utf-8"))
    assert state["adaptations"]["remote"]["helper"] == str(remote_helper.resolve()), state
    assert state["adaptations"]["remote"]["settings"] == str(remote_settings.resolve()), state
    stale_state = base / "stale-state.json"
    stale_state.write_text(
        json.dumps({"version": 1, "adaptations": {"remote": {"helper": str(base / "missing-helper.py"), "settings": str(base / "missing-settings.json")}}}),
        encoding="utf-8",
    )
    stale_settings = json.loads(json.dumps(settings))
    stale_settings["skill_dependencies"]["state_path"] = str(stale_state)
    stale_settings["remote"]["helper"] = "fallback-helper.py"
    stale_settings["remote"]["settings"] = "fallback-settings.json"
    assert remote_setting(stale_settings, "helper") == "fallback-helper.py", stale_settings
    assert remote_setting(stale_settings, "settings") == "fallback-settings.json", stale_settings
    space_settings = base / "space-settings.json"
    space_settings.write_text(
        json.dumps(
            {
                "version": 1,
                "paths": {"space_dir": "~/Codex Skill Space/output"},
                "workflow": {},
                "skill_dependencies": {
                    "state_path": str(base / "dependency-state.json"),
                    "install_policy": "ask_each_missing",
                    "adaptation_policy": "required",
                    "required": [{"id": "x", "url": "https://github.com/example/x.git", "skills": ["x"], "install_specs": [{"skill": "x", "source_path": "x"}]}],
                    "recommended": [{"id": "y", "url": "https://github.com/example/y.git", "skills": ["y"], "install_specs": [{"skill": "y", "source_path": "y"}]}],
                },
            }
        ),
        encoding="utf-8",
    )
    loaded_space = load_settings(space_settings)
    assert not str(path_setting(loaded_space, "space_dir")).startswith("~"), loaded_space


def _write_fake_skill(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("SKILL.md").write_text(
        f"---\nname: {path.name}\ndescription: fake skill for smoke tests\n---\n\n# {path.name}\n",
        encoding="utf-8",
    )


def _run_remote_selection_preflight_gate(base: Path) -> None:
    with _fake_tool_path(base, "preflight-no-vivado", ()):
        cli = subprocess.run(
            [
                sys.executable,
                "scripts/preflight_verilog_toolchain.py",
                "--settings",
                "config/defaults.json",
                "--readiness",
                "execute",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert cli.returncode == 0, cli.stderr
    report = json.loads(cli.stdout)
    assert report["remote_selection_required"] is True, report
    assert report["remote"]["recommended_server"] == "<selected-server>", report
    assert report["remote"]["recommended_server_name"] == "<user-selected-server>", report
    forbidden_server_id = "server" + "_2"
    forbidden_server_name = "FPGA" + "-Server-U50"
    assert forbidden_server_id not in json.dumps(report), report
    assert forbidden_server_name not in json.dumps(report), report
    assert report["remote"]["server_confirmed"] is False, report
    assert "erie-remote-ssh discover and choices" in report["required_action"], report

    with _fake_tool_path(base, "preflight-static", ()):
        cli = subprocess.run(
            [
                sys.executable,
                "scripts/preflight_verilog_toolchain.py",
                "--settings",
                "config/defaults.json",
                "--readiness",
                "static",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert cli.returncode == 0, cli.stderr
    report = json.loads(cli.stdout)
    assert report["remote_selection_required"] is False, report


def _run_remote_vivado_activation_gate() -> None:
    script_path = ROOT / "scripts" / "remote_validate_verilog_skill.py"
    spec = importlib.util.spec_from_file_location("remote_validate_verilog_skill", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    command = module.remote_validation_command(".remote/run/erie-verilog-generator", "python3")
    settings_index = command.index("settings64.sh")
    tool_scan_index = command.index("for tool in xvlog")
    workflow_index = command.index("-m runtime.verilog_generator run-workflow")
    assert settings_index < tool_scan_index < workflow_index, command
    assert "${XILINX_VIVADO:-}/settings64.sh" in command, command
    assert "/tools/Xilinx/Vivado/*/settings64.sh" in command, command
    assert "remote_outputs_retained=_smoke_runs workflow-state.json" in command, command
    cleanup_command = module.remote_validation_command(".remote/run/erie-verilog-generator", "python3", cleanup_outputs=True)
    assert "rm -rf _smoke_runs workflow-state.json" in cleanup_command, cleanup_command


def _run_remote_retention_policy_gate() -> None:
    module = _load_remote_validate_module()
    default_args = argparse.Namespace(cleanup_remote=False, keep_remote=False)
    keep_args = argparse.Namespace(cleanup_remote=False, keep_remote=True)
    cleanup_args = argparse.Namespace(cleanup_remote=True, keep_remote=False)
    assert module.cleanup_remote_requested(default_args) is False
    assert module.cleanup_remote_requested(keep_args) is False
    assert module.cleanup_remote_requested(cleanup_args) is True
    location_lines = module.remote_location_lines(
        ".erie-verilog-generator-validation/run-20260508T010203",
        ".erie-verilog-generator-validation/run-20260508T010203/erie-verilog-generator",
        False,
    )
    assert location_lines[0] == "remote_parent: .erie-verilog-generator-validation/run-20260508T010203", location_lines
    assert location_lines[1].endswith("/erie-verilog-generator"), location_lines
    assert location_lines[2] == "remote_cleanup_requested: False", location_lines


def _run_remote_toolchain_selection_gate(base: Path) -> None:
    module = _load_remote_validate_module()
    config_path = base / "user-home" / "remote_toolchain_selection.json"
    selection = {
        "simulator_backend": "xsim",
        "vivado_settings64": "/tools/Xilinx/Vivado/2023.2/settings64.sh",
        "confirmed_by_user": True,
        "updated_at": "2026-05-08T00:00:00Z",
    }
    selected_server = "selected-server"
    module.write_toolchain_selection(config_path, selected_server, selection)
    loaded = module.load_toolchain_selection(config_path, selected_server)
    assert loaded["simulator_backend"] == "xsim", loaded
    assert loaded["vivado_settings64"] == "/tools/Xilinx/Vivado/2023.2/settings64.sh", loaded

    command = module.remote_validation_command(
        ".remote/run/erie-verilog-generator",
        "python3",
        toolchain_selection=loaded,
        toolchain_config_path=config_path,
    )
    assert "selected_vivado_settings='/tools/Xilinx/Vivado/2023.2/settings64.sh'" in command, command
    assert "Multiple Vivado installations were detected" in command, command
    assert str(config_path) in command, command
    assert "configured_simulator_backend='xsim'" in command, command
    assert 'export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"' in command, command

    iverilog_command = module.remote_validation_command(
        ".remote/run/erie-verilog-generator",
        "python3",
        toolchain_selection={"simulator_backend": "iverilog", "confirmed_by_user": True},
        toolchain_config_path=config_path,
    )
    assert "vivado_settings=not_required_for_selected_backend" in iverilog_command, iverilog_command
    assert "configured_simulator_backend='iverilog'" in iverilog_command, iverilog_command

    try:
        module.require_remote_absolute_file_path("../bad/settings64.sh", "bad")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected unsafe remote Vivado path to fail.")


def _run_remote_fixture_gate(base: Path) -> None:
    module = _load_remote_validate_module()
    command = module.remote_validation_command(".remote/run/erie-verilog-generator", "python3")
    for fixture in module.REMOTE_FIXTURES:
        assert f"assets/examples/remote_fixtures\") / name / \"spec.json\"" in command, command
        assert fixture in command, command
        fixture_root = ROOT / "assets" / "examples" / "remote_fixtures" / fixture
        for verilog_file in fixture_root.glob("generated/**/*.v"):
            text = verilog_file.read_text(encoding="utf-8")
            assert not re.search(r"\b(?:task|function)\b", text), (fixture, verilog_file)
        spec = json.loads((fixture_root / "spec.json").read_text(encoding="utf-8"))
        report = validate_verilog_artifacts(
            spec,
            fixture_root / "generated",
            run_external=False,
            readiness="execute",
        )
        assert report["ok"] is True, (fixture, report)
        assert report["warnings"] == 0, (fixture, report)
    assert "_smoke_runs/remote_fixtures/summary.json" in command, command
    assert "--report-json" in command, command
    assert "xvlog\", \"xelab\", \"xsim" in command, command


def _run_remote_report_gate(base: Path) -> None:
    module = _load_remote_validate_module()
    parsed = module.parse_json_output('prefix\n{"entries":[{"name":"run-2","type":"dir"},{"name":"note.txt","type":"file"}]}\n')
    assert parsed["entries"][0]["name"] == "run-2", parsed

    validation = {
        "ok": True,
        "metrics": {
            "selected_simulator_backend": "xsim",
            "executed_tools": ["xvlog", "xelab", "xsim"],
        },
    }
    summary = module.summarize_validation_report(
        validation,
        rtl_path=".erie-verilog-generator-validation/run-x/erie-verilog-generator/generated/rtl/top.v",
        testbench_path=".erie-verilog-generator-validation/run-x/erie-verilog-generator/generated/tb/top_tb.v",
        validation_json=".erie-verilog-generator-validation/run-x/erie-verilog-generator/validation.json",
    )
    assert summary["selected_simulator_backend"] == "xsim", summary
    assert summary["rtl_path"].endswith("top.v"), summary
    fixture_summary = module.summarize_fixture_report(
        {
            "fixtures": [
                {
                    "name": "comb_parity_mux",
                    "ok": True,
                    "selected_simulator_backend": "xsim",
                    "executed_tools": ["xvlog", "xelab", "xsim"],
                    "rtl_path": "assets/examples/remote_fixtures/comb_parity_mux/generated/rtl/comb_parity_mux.v",
                    "testbench_path": "assets/examples/remote_fixtures/comb_parity_mux/generated/tb/comb_parity_mux_tb.v",
                    "validation_json": "_smoke_runs/remote_fixtures/comb_parity_mux/validation.json",
                }
            ]
        }
    )
    assert fixture_summary[0]["name"] == "comb_parity_mux", fixture_summary
    assert fixture_summary[0]["executed_tools"] == ["xvlog", "xelab", "xsim"], fixture_summary

    download_dir = base / "remote-report"
    download_dir.mkdir(parents=True, exist_ok=True)
    old_run_helper = module.run_helper

    def fake_run_helper(helper, args, *, allow_failure=False, quiet_on_failure=False):
        if args[0] == "file-list":
            stdout = json.dumps(
                {
                    "path": ".erie-verilog-generator-validation",
                    "type": "dir",
                    "entries": [
                        {"name": "run-20260508T010203", "type": "dir"},
                        {"name": "run-20260508T010204", "type": "dir"},
                    ],
                }
            )
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if args[0] == "file-download":
            remote = args[args.index("--remote") + 1]
            local = download_dir / (remote.replace("/", "_") + ".json")
            if remote.endswith("_smoke_runs/remote_fixtures/summary.json"):
                local.write_text(
                    json.dumps(
                        {
                            "fixtures": [
                                {
                                    "name": "pipeline_delay",
                                    "ok": True,
                                    "selected_simulator_backend": "xsim",
                                    "executed_tools": ["xvlog", "xelab", "xsim"],
                                    "rtl_path": "assets/examples/remote_fixtures/pipeline_delay/generated/rtl/pipeline_delay.v",
                                    "testbench_path": "assets/examples/remote_fixtures/pipeline_delay/generated/tb/pipeline_delay_tb.v",
                                    "validation_json": "_smoke_runs/remote_fixtures/pipeline_delay/validation.json",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                local.write_text(json.dumps(validation), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout=f"downloaded: {local}\n", stderr="")
        raise AssertionError(args)

    module.run_helper = fake_run_helper
    try:
        report = module.report_remote_runs(Path("helper.py"), Path("settings.json"), Path("servers.json"), "selected-server", ".erie-verilog-generator-validation", 1)
    finally:
        module.run_helper = old_run_helper
    assert report["status"] == "ok", report
    assert len(report["runs"]) == 1, report
    assert report["runs"][0]["run"] == "run-20260508T010204", report
    assert report["runs"][0]["remote_execute"]["selected_simulator_backend"] == "xsim", report
    assert report["runs"][0]["fixtures"][0]["name"] == "pipeline_delay", report


def _load_remote_validate_module():
    script_path = ROOT / "scripts" / "remote_validate_verilog_skill.py"
    spec = importlib.util.spec_from_file_location("remote_validate_verilog_skill", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_dependency_manager_module():
    script_path = ROOT / "scripts" / "manage_skill_dependencies.py"
    spec = importlib.util.spec_from_file_location("manage_skill_dependencies", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_simulator_priority_gate(base: Path) -> None:
    spec = _rtl_smoke_spec()
    cases = [
        ("xsim", ("xvlog", "xelab", "xsim", "vcs", "verdi", "iverilog", "vvp"), "xsim", ("xvlog", "xelab", "xsim"), ("vcs", "iverilog")),
        ("vcs-verdi", ("vcs", "verdi", "iverilog", "vvp"), "vcs_verdi", ("verdi", "vcs"), ("iverilog", "vvp")),
        ("vcs-without-verdi", ("vcs", "iverilog", "vvp"), "iverilog", ("iverilog", "vvp"), ("vcs", "verdi")),
        ("iverilog", ("iverilog", "vvp"), "iverilog", ("iverilog", "vvp"), ("vcs", "verdi")),
    ]
    for case_name, tools, expected_backend, expected_calls, unexpected_calls in cases:
        generated_dir = _write_mock_rtl_artifacts(spec, base / "sim-priority" / case_name / "generated")
        with _fake_tool_path(base, case_name, tools) as log_path:
            report = validate_verilog_artifacts(spec, generated_dir, run_external=True, readiness="execute")
        assert report["ok"] is True, (case_name, report)
        metrics = report["metrics"]
        assert metrics["selected_simulator_backend"] == expected_backend, (case_name, metrics)
        calls = _tool_calls(log_path)
        for tool in expected_calls:
            assert tool in calls, (case_name, calls)
        for tool in unexpected_calls:
            assert tool not in calls, (case_name, calls)
        if expected_backend == "iverilog":
            assert "xsim" in metrics["missing_preferred_backends"], metrics

    generated_dir = _write_mock_rtl_artifacts(spec, base / "sim-priority" / "compile-only" / "generated")
    with _fake_tool_path(base, "compile-only", ("xvlog", "xelab", "xsim")) as log_path:
        report = validate_verilog_artifacts(spec, generated_dir, run_external=True, readiness="compile")
    assert report["ok"] is True, report
    assert report["metrics"]["selected_simulator_backend"] == "xsim", report
    calls = _tool_calls(log_path)
    assert "xvlog" in calls and "xelab" in calls and "xsim" not in calls, calls

    generated_dir = _write_mock_rtl_artifacts(spec, base / "sim-priority" / "env-priority" / "generated")
    old_priority = os.environ.get("VERILOG_GENERATOR_SIMULATOR_PRIORITY")
    os.environ["VERILOG_GENERATOR_SIMULATOR_PRIORITY"] = "iverilog"
    try:
        with _fake_tool_path(base, "env-priority", ("xvlog", "xelab", "xsim", "iverilog", "vvp")) as log_path:
            report = validate_verilog_artifacts(spec, generated_dir, run_external=True, readiness="execute")
    finally:
        if old_priority is None:
            os.environ.pop("VERILOG_GENERATOR_SIMULATOR_PRIORITY", None)
        else:
            os.environ["VERILOG_GENERATOR_SIMULATOR_PRIORITY"] = old_priority
    assert report["ok"] is True, report
    assert report["metrics"]["selected_simulator_backend"] == "iverilog", report
    calls = _tool_calls(log_path)
    assert "iverilog" in calls and "vvp" in calls, calls
    assert "xvlog" not in calls and "xelab" not in calls and "xsim" not in calls, calls

    generated_dir = _write_mock_rtl_artifacts(spec, base / "sim-priority" / "no-external" / "generated")
    with _fake_tool_path(base, "no-external", ("xvlog", "xelab", "xsim", "iverilog", "vvp")) as log_path:
        report = validate_verilog_artifacts(spec, generated_dir, run_external=False, readiness="execute")
    assert report["ok"] is True, report
    assert report["metrics"]["selected_simulator_backend"] is None, report
    assert report["metrics"]["executed_tools"] == [], report
    assert _tool_calls(log_path) == [], report

    with _fake_tool_path(base, "no-simulator", ()):
        result = run_verilog_workflow(
            spec,
            out_dir=base / "blocked-no-simulator",
            provider_name="mock",
            readiness="execute",
            run_external=True,
        )
    assert result["status"] == "blocked_toolchain", result


def _run_toolchain_blocking_gate(base: Path, example_spec: Path) -> None:
    spec = _rtl_smoke_spec()
    with _fake_tool_path(base, "blocked-implement", ("iverilog", "vvp")):
        result = run_verilog_workflow(
            spec,
            out_dir=base / "blocked-toolchain",
            provider_name="mock",
            readiness="implement",
            run_external=True,
        )
    assert result["status"] == "blocked_toolchain", result
    validation = json.loads((base / "blocked-toolchain" / "attempt-001" / "validation.json").read_text(encoding="utf-8"))
    assert validation["metrics"]["selected_simulator_backend"] == "iverilog", validation
    assert any(item["tool"] == "yosys" and item["severity"] == "error" and item["source"] == "toolchain_issue" for item in validation["issues"]), validation

    generated_dir = _write_mock_rtl_artifacts(spec, base / "cli-toolchain" / "generated")
    with _fake_tool_path(base, "cli-blocked-implement", ("iverilog", "vvp")):
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "runtime.verilog_generator",
                "validate",
                "--spec",
                str(example_spec),
                "--path",
                str(generated_dir),
                "--readiness",
                "implement",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    assert cli.returncode != 0, cli.stdout
    cli_output = cli.stdout + cli.stderr
    assert "yosys" in cli_output, cli_output


@contextmanager
def _fake_tool_path(base: Path, case_name: str, tools: tuple[str, ...]):
    tool_dir = base / "fake-tools" / case_name
    tool_dir.mkdir(parents=True, exist_ok=True)
    log_path = tool_dir / "tool-calls.log"
    for tool in tools:
        _write_fake_tool(tool_dir, tool)
    old_path = os.environ.get("PATH", "")
    old_tool_log = os.environ.get("TOOL_LOG")
    os.environ["PATH"] = str(tool_dir)
    os.environ["TOOL_LOG"] = str(log_path)
    try:
        yield log_path
    finally:
        os.environ["PATH"] = old_path
        if old_tool_log is None:
            os.environ.pop("TOOL_LOG", None)
        else:
            os.environ["TOOL_LOG"] = old_tool_log


def _write_fake_tool(tool_dir: Path, tool: str) -> None:
    if os.name == "nt":
        script = tool_dir / f"{tool}.cmd"
        script.write_text(f"@echo off\r\n>>\"%TOOL_LOG%\" echo {tool} %*\r\nexit /b 0\r\n", encoding="utf-8")
        return
    script = tool_dir / tool
    script.write_text(
        f"#!/bin/sh\nprintf '%s' '{tool}' >> \"$TOOL_LOG\"\nfor arg in \"$@\"; do printf ' %s' \"$arg\" >> \"$TOOL_LOG\"; done\nprintf '\\n' >> \"$TOOL_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _tool_calls(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    calls: list[str] = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split()
        if parts:
            calls.append(parts[0])
    return calls


def _rtl_smoke_spec() -> dict:
    return {
        "name": "erie_adapter",
        "target": "rtl",
        "rtl_style_profile": "erie_strict",
        "design_requirements": {
            "target": "rtl",
            "pipeline_required": True,
            "streamability": "non_streamable",
            "interface_family": "native",
            "interface_profile": {},
            "confirmed_by_user": True,
            "confirmation_notes": "Use the native RTL port set with a pipelined Erie-style implementation.",
        },
        "streamability": "non_streamable",
        "interface_family": "native",
        "interface_profile": {},
        "pipeline_required": True,
        "codegen_plan_required": True,
        "description": "Strict Erie RTL smoke example.",
        "interfaces": {
            "ports": [
                {"name": "i_clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "i_rstn", "direction": "input", "width": 1, "role": "reset"},
                {"name": "i_in_valid", "direction": "input", "width": 1},
                {"name": "i_in_data", "direction": "input", "width": 8},
                {"name": "o_out_valid", "direction": "output", "width": 1},
                {"name": "o_out_data", "direction": "output", "width": 8},
            ]
        },
        "behavior": ["Forward the input data and valid signal by one cycle."],
        "clock": {"name": "i_clk", "edge": "posedge", "frequency_mhz": 100},
        "reset": {"name": "i_rstn", "active": "low", "synchronous": False},
        "constraints": ["Use synthesizable Verilog-2001."],
        "outputs": [
            {"path": "rtl/erie_adapter.v", "kind": "source", "language": "verilog"},
            {"path": "tb/erie_adapter_tb.v", "kind": "testbench", "language": "verilog"},
        ],
        "notes": [],
        "subfunctions": [],
        "workflow": {},
        "performance": {},
    }


def _interface_policy_spec(description: str, *, clock: str = "i_clk", reset: str = "i_rstn") -> dict:
    return {
        "name": "interface_policy_smoke",
        "target": "rtl",
        "rtl_dialect": "verilog",
        "pipeline_required": True,
        "codegen_plan_required": True,
        "description": description,
        "interfaces": {
            "ports": [
                {"name": clock, "direction": "input", "width": 1, "role": "clock"},
                {"name": reset, "direction": "input", "width": 1, "role": "reset"},
                {"name": "i_data", "direction": "input", "width": 32},
                {"name": "o_data", "direction": "output", "width": 32},
            ]
        },
        "behavior": [description],
        "clock": {"name": clock, "edge": "posedge", "frequency_mhz": 100},
        "reset": {"name": reset, "active": "low", "synchronous": False},
        "constraints": ["Use synthesizable Verilog-2001."],
        "outputs": [
            {"path": "rtl/interface_policy_smoke.v", "kind": "source", "language": "verilog"},
            {"path": "tb/interface_policy_smoke_tb.v", "kind": "testbench", "language": "verilog"},
        ],
        "notes": [],
        "subfunctions": [],
        "workflow": {},
        "performance": {},
    }


if __name__ == "__main__":
    raise SystemExit(main())
