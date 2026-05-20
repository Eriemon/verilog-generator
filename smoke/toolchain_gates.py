"""Remote and toolchain smoke gates."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from integration.verilog_adapter import run_verilog_workflow, validate_verilog_artifacts

from .shared import fake_tool_path, load_module, rtl_smoke_spec, temporary_cwd, tool_calls, write_mock_rtl_artifacts


def run_remote_selection_preflight_gate(base: Path, root: Path) -> None:
    with fake_tool_path(base, "preflight-no-vivado", ()):
        cli = subprocess.run(
            [
                os.sys.executable,
                "scripts/preflight_verilog_toolchain.py",
                "--settings",
                "config/defaults.json",
                "--readiness",
                "execute",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    assert cli.returncode == 0, cli.stderr
    report = json.loads(cli.stdout)
    assert report["remote_selection_required"] is True, report
    assert report["remote"]["recommended_server_name"] is None, report
    if report["remote"]["server_confirmed"] is True:
        assert report["remote"]["recommended_server"] == "server_1", report
        assert "confirmed project-local remote server" in report["required_action"], report
    else:
        assert report["remote"]["recommended_server"] is None, report
        assert "erie-remote-ssh discover and choices" in report["required_action"], report

    with fake_tool_path(base, "preflight-static", ()):
        cli = subprocess.run(
            [
                os.sys.executable,
                "scripts/preflight_verilog_toolchain.py",
                "--settings",
                "config/defaults.json",
                "--readiness",
                "static",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    assert cli.returncode == 0, cli.stderr
    report = json.loads(cli.stdout)
    assert report["remote_selection_required"] is False, report


def run_remote_vivado_activation_gate(root: Path) -> None:
    module = load_module(root / "scripts" / "remote_validate_verilog_skill.py", "remote_validate_verilog_skill")
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


def run_remote_retention_policy_gate(root: Path) -> None:
    module = load_module(root / "scripts" / "remote_validate_verilog_skill.py", "remote_validate_verilog_skill")
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


def run_remote_server_list_fallback_gate(base: Path, root: Path) -> None:
    module = load_module(root / "scripts" / "remote_validate_verilog_skill.py", "remote_validate_verilog_skill")
    fallback_root = base / "remote-server-list-fallback"
    remote_settings = fallback_root / "remote-ssh" / "config" / "defaults.json"
    installed_server_list = fallback_root / "remote-ssh" / "config" / "server_list.local.json"
    remote_settings.parent.mkdir(parents=True, exist_ok=True)
    installed_server_list.write_text('{"version":1,"servers":[]}\n', encoding="utf-8")
    remote_settings.write_text(
        json.dumps({"version": 1, "paths": {"default_server_list": "${skill_dir}/config/server_list.local.json"}}),
        encoding="utf-8",
    )
    configured_missing = fallback_root / "workspace" / ".erie-verilog-generator-state" / "server_list.local.json"
    resolved = module.resolve_server_list_path(configured_missing, remote_settings)
    assert resolved == installed_server_list.resolve(), resolved

    configured_present = fallback_root / "workspace-present" / ".erie-verilog-generator-state" / "server_list.local.json"
    configured_present.parent.mkdir(parents=True, exist_ok=True)
    configured_present.write_text('{"version":1,"servers":[{"id":"server_1"}]}\n', encoding="utf-8")
    resolved_present = module.resolve_server_list_path(configured_present, remote_settings)
    assert resolved_present == configured_present.resolve(), resolved_present


def run_remote_toolchain_selection_gate(base: Path, root: Path) -> None:
    module = load_module(root / "scripts" / "remote_validate_verilog_skill.py", "remote_validate_verilog_skill")
    config_path = base / "user-home" / "remote_toolchain_selection.json"
    selection = {
        "simulator_backend": "xsim",
        "vivado_settings64": "/tools/Xilinx/Vivado/2023.2/settings64.sh",
        "confirmed_by_user": True,
        "updated_at": "2026-05-08T00:00:00Z",
    }
    module.write_toolchain_selection(config_path, "selected-server", selection)
    loaded = module.load_toolchain_selection(config_path, "selected-server")
    assert loaded["simulator_backend"] == "xsim", loaded
    assert loaded["vivado_settings64"] == "/tools/Xilinx/Vivado/2023.2/settings64.sh", loaded

    command = module.remote_validation_command(
        ".remote/run/erie-verilog-generator",
        "python3",
        toolchain_selection=loaded,
        toolchain_config_path=config_path,
    )
    assert "selected_vivado_settings='/tools/Xilinx/Vivado/2023.2/settings64.sh'" in command, command
    assert "Multiple Xilinx toolchain settings64.sh candidates were detected" in command, command
    assert str(config_path) in command, command
    assert "configured_simulator_backend='xsim'" in command, command
    assert 'export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"' in command, command

    vitis_command = module.remote_validation_command(
        ".remote/run/erie-verilog-generator",
        "python3",
        toolchain_selection={
            "simulator_backend": "xsim",
            "vivado_settings64": "/tools/Xilinx/Vitis/2022.2/settings64.sh",
            "confirmed_by_user": True,
        },
        toolchain_config_path=config_path,
    )
    assert "/tools/Xilinx/Vitis/*/settings64.sh" in vitis_command, vitis_command
    assert "Configured Xilinx settings64.sh was not found on the remote server" in vitis_command, vitis_command
    assert "Multiple Xilinx toolchain settings64.sh candidates were detected" in vitis_command, vitis_command
    assert "selected_vivado_settings='/tools/Xilinx/Vitis/2022.2/settings64.sh'" in vitis_command, vitis_command

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

    project_local_root = base / "project-local-remote-root"
    nested_project_local = project_local_root / "nested" / "cwd"
    nested_project_local.mkdir(parents=True)
    (project_local_root / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    project_local_settings = {
        "remote": {
            "helper": "helper.py",
            "settings": "settings.json",
            "server_list": ".erie-verilog-generator-state/server_list.local.json",
            "server_confirmed": False,
            "python": "python3",
            "remote_root": ".erie-verilog-generator-validation",
            "toolchain_config": ".erie-verilog-generator-state/remote_toolchain_selection.json",
        },
        "skill_dependencies": {
            "state_path": ".erie-verilog-generator-state/dependency-state.json",
            "install_policy": "ask_each_missing",
            "adaptation_policy": "required",
            "required": [{"id": "x", "url": "https://github.com/example/x.git", "skills": ["x"], "install_specs": [{"skill": "x", "source_path": "x"}]}],
            "recommended": [{"id": "y", "url": "https://github.com/example/y.git", "skills": ["y"], "install_specs": [{"skill": "y", "source_path": "y"}]}],
        },
    }
    with temporary_cwd(nested_project_local):
        resolved_config = module.resolve_toolchain_config(project_local_settings, None)
        assert resolved_config == project_local_root.resolve() / ".erie-verilog-generator-state" / "remote_toolchain_selection.json", resolved_config
        module.write_toolchain_selection(resolved_config, "selected-server", selection)
        assert resolved_config.exists(), resolved_config
        assert not (nested_project_local / ".erie-verilog-generator-state" / "remote_toolchain_selection.json").exists()


def run_remote_fixture_gate(base: Path, root: Path) -> None:
    module = load_module(root / "scripts" / "remote_validate_verilog_skill.py", "remote_validate_verilog_skill")
    command = module.remote_validation_command(".remote/run/erie-verilog-generator", "python3")
    for fixture in module.REMOTE_FIXTURES:
        assert f"assets/examples/remote_fixtures\") / name / \"spec.json\"" in command, command
        assert fixture in command, command
        fixture_root = root / "assets" / "examples" / "remote_fixtures" / fixture
        for verilog_file in fixture_root.glob("generated/**/*.v"):
            text = verilog_file.read_text(encoding="utf-8")
            assert "task" not in text and "function" not in text, (fixture, verilog_file)
        spec = json.loads((fixture_root / "spec.json").read_text(encoding="utf-8"))
        report = validate_verilog_artifacts(spec, fixture_root / "generated", run_external=False, readiness="execute")
        assert report["ok"] is True, (fixture, report)
        assert report["warnings"] == 0, (fixture, report)
    assert "_smoke_runs/remote_fixtures/summary.json" in command, command
    assert "--report-json" in command, command
    assert 'xvlog", "xelab", "xsim' in command, command


def run_remote_report_gate(base: Path, root: Path) -> None:
    module = load_module(root / "scripts" / "remote_validate_verilog_skill.py", "remote_validate_verilog_skill")
    parsed = module.parse_json_output('prefix\n{"entries":[{"name":"run-2","type":"dir"},{"name":"note.txt","type":"file"}]}\n')
    assert parsed["entries"][0]["name"] == "run-2", parsed

    validation = {"ok": True, "metrics": {"selected_simulator_backend": "xsim", "executed_tools": ["xvlog", "xelab", "xsim"]}}
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


def run_validate_cleanup_retry_gate(base: Path, root: Path) -> None:
    module = load_module(root / "scripts" / "validate_verilog_skill.py", "validate_verilog_skill")
    target = root / "_smoke_runs" / "cleanup-retry"
    target.mkdir(parents=True, exist_ok=True)
    target.joinpath("locked.pyc").write_text("x", encoding="utf-8")
    original_rmtree = module.shutil.rmtree
    calls = {"count": 0}

    def flaky_rmtree(path, *args, **kwargs):
        if Path(path) == target.resolve() and calls["count"] == 0:
            calls["count"] += 1
            raise OSError(145, "directory not empty")
        return original_rmtree(path, *args, **kwargs)

    module.shutil.rmtree = flaky_rmtree
    try:
        module.remove_inside_skill(target)
    finally:
        module.shutil.rmtree = original_rmtree
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    assert calls["count"] == 1, calls
    assert not target.exists(), target


def run_simulator_priority_gate(base: Path, root: Path, example_spec: Path) -> None:
    spec = rtl_smoke_spec()
    cases = [
        ("xsim", ("xvlog", "xelab", "xsim", "vcs", "verdi", "iverilog", "vvp"), "xsim", ("xvlog", "xelab", "xsim"), ("vcs", "iverilog")),
        ("vcs-verdi", ("vcs", "verdi", "iverilog", "vvp"), "vcs_verdi", ("verdi", "vcs"), ("iverilog", "vvp")),
        ("vcs-without-verdi", ("vcs", "iverilog", "vvp"), "iverilog", ("iverilog", "vvp"), ("vcs", "verdi")),
        ("iverilog", ("iverilog", "vvp"), "iverilog", ("iverilog", "vvp"), ("vcs", "verdi")),
    ]
    for case_name, tools, expected_backend, expected_calls, unexpected_calls in cases:
        generated_dir = write_mock_rtl_artifacts(spec, base / "sim-priority" / case_name / "generated")
        with fake_tool_path(base, case_name, tools) as log_path:
            report = validate_verilog_artifacts(spec, generated_dir, run_external=True, readiness="execute")
        assert report["ok"] is True, (case_name, report)
        metrics = report["metrics"]
        assert metrics["selected_simulator_backend"] == expected_backend, (case_name, metrics)
        calls = tool_calls(log_path)
        for tool in expected_calls:
            assert tool in calls, (case_name, calls)
        for tool in unexpected_calls:
            assert tool not in calls, (case_name, calls)
        if expected_backend == "iverilog":
            assert "xsim" in metrics["missing_preferred_backends"], metrics

    generated_dir = write_mock_rtl_artifacts(spec, base / "sim-priority" / "compile-only" / "generated")
    with fake_tool_path(base, "compile-only", ("xvlog", "xelab", "xsim")) as log_path:
        report = validate_verilog_artifacts(spec, generated_dir, run_external=True, readiness="compile")
    assert report["ok"] is True, report
    assert report["metrics"]["selected_simulator_backend"] == "xsim", report
    calls = tool_calls(log_path)
    assert "xvlog" in calls and "xelab" in calls and "xsim" not in calls, calls

    generated_dir = write_mock_rtl_artifacts(spec, base / "sim-priority" / "env-priority" / "generated")
    old_priority = os.environ.get("VERILOG_GENERATOR_SIMULATOR_PRIORITY")
    os.environ["VERILOG_GENERATOR_SIMULATOR_PRIORITY"] = "iverilog"
    try:
        with fake_tool_path(base, "env-priority", ("xvlog", "xelab", "xsim", "iverilog", "vvp")) as log_path:
            report = validate_verilog_artifacts(spec, generated_dir, run_external=True, readiness="execute")
    finally:
        if old_priority is None:
            os.environ.pop("VERILOG_GENERATOR_SIMULATOR_PRIORITY", None)
        else:
            os.environ["VERILOG_GENERATOR_SIMULATOR_PRIORITY"] = old_priority
    assert report["ok"] is True, report
    assert report["metrics"]["selected_simulator_backend"] == "iverilog", report
    calls = tool_calls(log_path)
    assert "iverilog" in calls and "vvp" in calls, calls
    assert "xvlog" not in calls and "xelab" not in calls and "xsim" not in calls, calls

    generated_dir = write_mock_rtl_artifacts(spec, base / "sim-priority" / "no-external" / "generated")
    with fake_tool_path(base, "no-external", ("xvlog", "xelab", "xsim", "iverilog", "vvp")) as log_path:
        report = validate_verilog_artifacts(spec, generated_dir, run_external=False, readiness="execute")
    assert report["ok"] is True, report
    assert report["metrics"]["selected_simulator_backend"] is None, report
    assert report["metrics"]["executed_tools"] == [], report
    assert tool_calls(log_path) == [], report

    with fake_tool_path(base, "no-simulator", ()):
        result = run_verilog_workflow(spec, out_dir=base / "blocked-no-simulator", provider_name="mock", readiness="execute", run_external=True)
    assert result["status"] == "blocked_toolchain", result

    with fake_tool_path(base, "blocked-implement", ("iverilog", "vvp")):
        result = run_verilog_workflow(spec, out_dir=base / "blocked-toolchain", provider_name="mock", readiness="implement", run_external=True)
    assert result["status"] == "blocked_toolchain", result
    validation = json.loads((base / "blocked-toolchain" / "attempt-001" / "validation.json").read_text(encoding="utf-8"))
    assert validation["metrics"]["selected_simulator_backend"] == "iverilog", validation
    assert any(item["tool"] == "yosys" and item["severity"] == "error" and item["source"] == "toolchain_issue" for item in validation["issues"]), validation

    generated_dir = write_mock_rtl_artifacts(spec, base / "cli-toolchain" / "generated")
    with fake_tool_path(base, "cli-blocked-implement", ("iverilog", "vvp")):
        cli = subprocess.run(
            [
                os.sys.executable,
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
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    assert cli.returncode != 0, cli.stdout
    cli_output = cli.stdout + cli.stderr
    assert "yosys" in cli_output, cli_output
