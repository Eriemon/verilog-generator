"""Smoke gates for skill-effectiveness, refined templates, and remote entrypoints."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from integration.verilog_adapter import render_verilog_prompt
from runtime.verilog_generator.config import load_settings, remote_setting
from runtime.verilog_generator.refined_templates import list_refined_templates, select_refined_templates
from runtime.verilog_generator.remote_selection import (
    load_confirmed_remote_server,
    remote_server_selection_path,
    resolve_confirmed_remote_server,
    write_confirmed_remote_server,
)
from runtime.verilog_generator.requirements import build_requirements_payload
from runtime.verilog_generator.skill_effectiveness import evaluate_skill_effectiveness


def run_skill_effectiveness_gates(root: Path, base: Path) -> None:
    evals_path = root / "evals" / "evals.json"
    remote_report = {
        "runs": [
            {
                "run": "run-20260518T232841",
                "remote_execute": {
                    "available": True,
                    "ok": True,
                    "selected_simulator_backend": "xsim",
                },
                "fixtures": [
                    {"name": "comb_parity_mux", "ok": True},
                    {"name": "pipeline_delay", "ok": True},
                    {"name": "ready_valid_slice", "ok": True},
                ],
            }
        ]
    }
    report = evaluate_skill_effectiveness(evals_path, base / "skill-effectiveness.json", remote_runs_report=remote_report)
    assert report["summary"]["ok"] is True, report
    assert report["summary"]["passed_cases"] == report["summary"]["case_count"], report
    assert report["summary"]["improved_cases"] == report["summary"]["case_count"], report


def run_refined_template_gates(root: Path, base: Path) -> None:
    templates = list_refined_templates()
    template_ids = [item["template_id"] for item in templates]
    assert template_ids == [
        "axi4_lite_csr_shell",
        "axis_ready_valid_slice",
        "axi_interconnect_port_groups",
        "conv_load_store_pipeline",
    ], template_ids

    examples_dir = root / "assets" / "examples" / "refined_verilog_templates"
    expected = {
        "axi4_lite_csr": ["axi4_lite_csr_shell"],
        "axis_ready_valid": ["axis_ready_valid_slice"],
        "axi_interconnect_dma": ["axi_interconnect_port_groups"],
        "conv_load_store": ["conv_load_store_pipeline"],
    }
    for stem, expected_ids in expected.items():
        spec = json.loads((examples_dir / f"{stem}.json").read_text(encoding="utf-8"))
        selected = [item["template_id"] for item in select_refined_templates(spec)]
        assert selected == expected_ids, (stem, selected)
        payload = build_requirements_payload(spec)
        assert payload["selected_refined_template_ids"] == expected_ids, payload
        prompt = render_verilog_prompt(spec, base / f"{stem}-prompt.md")["prompt"]
        assert "## Refined Verilog design patterns" in prompt, prompt
        for template_id in expected_ids:
            assert template_id in prompt, prompt


def run_remote_entrypoint_gates(root: Path, base: Path) -> None:
    settings = load_settings(root / "config" / "defaults.json")
    state_path = remote_server_selection_path(settings)
    assert state_path.name == "remote_server_selection.json", state_path

    selection_path = base / "remote_server_selection.json"
    write_confirmed_remote_server(selection_path, "server_1")
    loaded = load_confirmed_remote_server(selection_path)
    assert loaded and loaded["server_id"] == "server_1", loaded

    local_settings = {
        "remote": {
            "helper": "helper.py",
            "settings": "settings.json",
            "server_list": ".erie-verilog-generator-state/server_list.local.json",
            "server_selection_path": str(selection_path),
            "server_confirmed": False,
            "python": "python3",
            "remote_root": ".erie-verilog-generator-validation",
            "toolchain_config": ".erie-verilog-generator-state/remote_toolchain_selection.json",
        }
    }
    resolved = resolve_confirmed_remote_server(local_settings)
    assert resolved and resolved["server_id"] == "server_1", resolved

    validate_module = _load_module(root / "scripts" / "validate_verilog_skill.py", "validate_verilog_skill")
    remote_command = validate_module.build_remote_validation_command(root / "config" / "defaults.json", "server_1")
    assert remote_command[-2:] == ["--server", "server_1"], remote_command

    remote_validate_module = _load_module(root / "scripts" / "remote_validate_verilog_skill.py", "remote_validate_verilog_skill")
    assert remote_validate_module.resolve_server_from_selection(local_settings)["server_id"] == "server_1"


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
