"""Dependency and project-local-state smoke gates."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from runtime.verilog_generator import workspace as workspace_runtime
from runtime.verilog_generator.config import fpga_developer_routing_settings, load_settings, remote_setting, skill_dependency_settings

from .shared import load_module, temporary_cwd, write_fake_skill


def run_project_local_state_gate(base: Path, root: Path) -> None:
    state_dir_name = ".erie-verilog-generator-state"
    repo_root = base / "workspace-root"
    nested_root = repo_root / "child" / "grandchild"
    nested_root.mkdir(parents=True)
    (repo_root / ".git").mkdir()

    agents_root = base / "agents-root"
    nested_agents = agents_root / "nested"
    nested_agents.mkdir(parents=True)
    (agents_root / "AGENTS.md").write_text("# root\n", encoding="utf-8")

    outer_root = base / "outer-root"
    inner_root = outer_root / "inner-root"
    deep_inner = inner_root / "deeper"
    deep_inner.mkdir(parents=True)
    (outer_root / ".git").mkdir()
    (inner_root / "AGENTS.md").write_text("# inner\n", encoding="utf-8")

    assert workspace_runtime.find_workspace_root(nested_root) == repo_root.resolve()
    assert workspace_runtime.find_workspace_root(nested_agents) == agents_root.resolve()
    assert workspace_runtime.find_workspace_root(deep_inner) == inner_root.resolve()

    state_settings_path = repo_root / "settings.json"
    state_settings_path.write_text(
        json.dumps(
            {
                "version": 1,
                "paths": {"quick_validate": str(root / "scripts" / "tb_generator.py")},
                "workflow": {},
                "skill_dependencies": {
                    "state_path": f"{state_dir_name}/dependency-state.json",
                    "install_policy": "ask_each_missing",
                    "adaptation_policy": "required",
                    "required": [{"id": "x", "url": "https://github.com/example/x.git", "skills": ["x"], "install_specs": [{"skill": "x", "source_path": "x"}]}],
                    "recommended": [{"id": "y", "url": "https://github.com/example/y.git", "skills": ["y"], "install_specs": [{"skill": "y", "source_path": "y"}]}],
                },
                "fpga_developer_routing": {
                    "state_path": f"{state_dir_name}/dependency-state.json",
                    "selection_policy": "ask_on_first_fpga_workflow",
                    "persist_selection": True,
                    "fpga_agent_required_when_developer_present": False,
                    "vendors": {"amd_xilinx": {"label": "AMD-Xilinx", "skills": ["vivado-developer"]}},
                },
                "remote": {
                    "helper": "helper.py",
                    "settings": "settings.json",
                    "server_list": f"{state_dir_name}/server_list.local.json",
                    "server_selection_path": f"{state_dir_name}/remote_server_selection.json",
                    "server_confirmed": False,
                    "python": "python3",
                    "remote_root": ".erie-verilog-generator-validation",
                    "toolchain_config": f"{state_dir_name}/remote_toolchain_selection.json",
                    "timeout_s": 120,
                },
            }
        ),
        encoding="utf-8",
    )

    with temporary_cwd(nested_root):
        loaded = load_settings(state_settings_path)
        dependency_settings = skill_dependency_settings(loaded)
        routing_settings = fpga_developer_routing_settings(loaded)
        assert dependency_settings["state_path"] == repo_root.resolve() / state_dir_name / "dependency-state.json", dependency_settings
        assert routing_settings["state_path"] == repo_root.resolve() / state_dir_name / "dependency-state.json", routing_settings
        assert Path(remote_setting(loaded, "server_list")) == repo_root.resolve() / state_dir_name / "server_list.local.json", loaded["remote"]
        assert Path(remote_setting(loaded, "server_selection_path")) == repo_root.resolve() / state_dir_name / "remote_server_selection.json", loaded["remote"]
        assert Path(remote_setting(loaded, "toolchain_config")) == repo_root.resolve() / state_dir_name / "remote_toolchain_selection.json", loaded["remote"]

    with temporary_cwd(root):
        loaded_defaults = load_settings(root / "config" / "defaults.json")
        dependency_defaults = skill_dependency_settings(loaded_defaults)
        workspace_root = workspace_runtime.require_workspace_root(purpose="default project-local state gate")
        assert dependency_defaults["state_path"] == workspace_root / state_dir_name / "dependency-state.json", dependency_defaults
        assert Path(remote_setting(loaded_defaults, "server_list")) == workspace_root / state_dir_name / "server_list.local.json", loaded_defaults["remote"]
        assert Path(remote_setting(loaded_defaults, "server_selection_path")) == workspace_root / state_dir_name / "remote_server_selection.json", loaded_defaults["remote"]
        assert Path(remote_setting(loaded_defaults, "toolchain_config")) == workspace_root / state_dir_name / "remote_toolchain_selection.json", loaded_defaults["remote"]

    with tempfile.TemporaryDirectory(prefix="erie-verilog-generator-no-root-") as orphan_dir:
        orphan_root = Path(orphan_dir) / "deep"
        orphan_root.mkdir(parents=True)
        with temporary_cwd(orphan_root):
            load_settings(state_settings_path)
            try:
                workspace_runtime.require_workspace_root()
            except Exception as exc:  # noqa: BLE001
                assert "project root" in str(exc) or "workspace root" in str(exc), exc
            else:
                raise AssertionError("Expected missing workspace root to fail.")


def run_dependency_manager_gate(base: Path, root: Path, settings: dict) -> None:
    module = load_module(root / "scripts" / "manage_skill_dependencies.py", "manage_skill_dependencies")
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
    assert empty_report["developer_skills"]["available_vendors"] == [], empty_report
    assert empty_report["active_fpga_dependency_mode"] == "fpga_agent_required", empty_report
    assert empty_report["fpga_agent_skipped_by_developer_skill"] is False, empty_report
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
    write_fake_skill(skills_root / "erie-remote-ssh")
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
        write_fake_skill(skills_root / name)
    plugin_skills = base / "plugins" / "cache" / "superpowers-dev" / "superpowers" / "1.0.0" / "skills"
    for name in ("using-superpowers", "writing-plans", "executing-plans", "test-driven-development"):
        write_fake_skill(plugin_skills / name)
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
    assert installed_fpga["installed"] == [], installed_fpga
    assert installed_fpga["skipped"] == [{"dependency_id": "fpga-agent-skills", "reason": "manual fallback approval required"}], installed_fpga
    assert commands == [], commands
    commands = []
    module.subprocess.run = fake_run
    try:
        installed_fpga_fallback = module.install_missing(
            settings,
            partial_report,
            "fpga-agent-skills",
            installer=fake_installer,
            allow_fpga_agent_fallback=True,
        )
    finally:
        module.subprocess.run = old_run
    assert installed_fpga_fallback["installed"] == ["vitis-hls-synthesis"], installed_fpga_fallback
    assert len(commands) == 1 and commands[0][-2:] == ["--path", "vitis-hls-synthesis"], commands
    write_fake_skill(skills_root / "vitis-hls-synthesis")
    full_report = module.check_dependencies(settings, skills_root=skills_root, plugin_cache=base / "plugins" / "cache", state_path=full_state)
    assert full_report["ok"] is True, full_report
    developer_root = base / "developer-skills"
    shutil.copytree(skills_root / "erie-remote-ssh", developer_root / "erie-remote-ssh")
    shutil.copytree(skills_root / "context-engineering", developer_root / "context-engineering")
    for name in ("vivado-developer", "vitis-developer", "pds-developer"):
        write_fake_skill(developer_root / name)
    developer_state = base / "developer-state.json"
    developer_report = module.check_dependencies(settings, skills_root=developer_root, plugin_cache=base / "plugins" / "cache", state_path=developer_state)
    assert developer_report["required_ok"] is True, developer_report
    assert "fpga-agent-skills" not in [item["id"] for item in developer_report["missing_required"]], developer_report
    assert developer_report["active_fpga_dependency_mode"] == "developer_skill", developer_report
    assert developer_report["fpga_agent_skipped_by_developer_skill"] is True, developer_report
    assert developer_report["developer_skills"]["available_vendors"] == ["amd_xilinx", "pangomicro"], developer_report
    assert developer_report["developer_skills"]["selection_required"] is True, developer_report
    developer_prompt = module.prompt_for_missing(developer_report)
    assert "AMD-Xilinx" in developer_prompt and "PangoMicro" in developer_prompt, developer_prompt
    commands = []
    module.subprocess.run = fake_run
    try:
        skipped_fpga = module.install_missing(settings, developer_report, "fpga-agent-skills", installer=fake_installer)
    finally:
        module.subprocess.run = old_run
    assert skipped_fpga["installed"] == [], skipped_fpga
    assert skipped_fpga["skipped"] == [{"dependency_id": "fpga-agent-skills", "reason": "developer skill is installed"}], skipped_fpga
    assert commands == [], commands
    cleanup_root = base / "cleanup-skills"
    cleanup_backup = base / "cleanup-backups"
    for name in (
        "vivado-tcl",
        "vivado-sim",
        "vivado-synth",
        "vivado-impl",
        "vivado-analysis",
        "vivado-constraints",
        "vivado-debug",
        "vitis-hls-synthesis",
        "vivado-developer",
        "vitis-developer",
    ):
        write_fake_skill(cleanup_root / name)
    try:
        module.cleanup_fpga_agent_skills(settings, skills_root=cleanup_root, backup_root=cleanup_backup, yes=False)
    except ValueError as exc:
        assert "--yes" in str(exc), exc
    else:
        raise AssertionError("cleanup-fpga-agent-skills must require explicit --yes.")
    cleanup_result = module.cleanup_fpga_agent_skills(settings, skills_root=cleanup_root, backup_root=cleanup_backup, yes=True)
    assert set(cleanup_result["moved"]) == {
        "vivado-tcl",
        "vivado-sim",
        "vivado-synth",
        "vivado-impl",
        "vivado-analysis",
        "vivado-constraints",
        "vivado-debug",
        "vitis-hls-synthesis",
    }, cleanup_result
    assert (cleanup_root / "vivado-developer").is_dir(), cleanup_result
    assert (cleanup_root / "vitis-developer").is_dir(), cleanup_result
    assert not (cleanup_root / "vivado-tcl").exists(), cleanup_result
    assert Path(cleanup_result["backup_dir"]).is_dir(), cleanup_result
    amd_selection = module.select_fpga_vendor(settings, "amd_xilinx", skills_root=developer_root, plugin_cache=base / "plugins" / "cache", state_path=developer_state)
    assert amd_selection["selected_vendor"] == "amd_xilinx", amd_selection
    amd_route = module.fpga_route(settings, skills_root=developer_root, plugin_cache=base / "plugins" / "cache", state_path=developer_state)
    assert amd_route["status"] == "ready", amd_route
    assert amd_route["selected_vendor"] == "amd_xilinx", amd_route
    assert amd_route["selected_skill"] == "vivado-developer", amd_route
    stale_developer_root = base / "stale-developer-skills"
    shutil.copytree(developer_root, stale_developer_root)
    shutil.rmtree(stale_developer_root / "vivado-developer")
    shutil.rmtree(stale_developer_root / "vitis-developer")
    stale_route = module.fpga_route(settings, skills_root=stale_developer_root, plugin_cache=base / "plugins" / "cache", state_path=developer_state)
    assert stale_route["status"] == "selection_stale", stale_route
    pango_state = base / "pango-state.json"
    pango_root = base / "pango-skills"
    shutil.copytree(developer_root / "erie-remote-ssh", pango_root / "erie-remote-ssh")
    shutil.copytree(developer_root / "pds-developer", pango_root / "pds-developer")
    module.select_fpga_vendor(settings, "pangomicro", skills_root=pango_root, plugin_cache=base / "plugins" / "cache", state_path=pango_state)
    pango_route = module.fpga_route(settings, skills_root=pango_root, plugin_cache=base / "plugins" / "cache", state_path=pango_state)
    assert pango_route["status"] == "ready", pango_route
    assert pango_route["selected_skill"] == "pds-developer", pango_route
    fallback_route = module.fpga_route(settings, skills_root=skills_root, plugin_cache=base / "plugins" / "cache", state_path=full_state)
    assert fallback_route["status"] == "fpga_agent", fallback_route
    assert "vivado-tcl" in fallback_route["fallback_skills"], fallback_route
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
        write_fake_skill(upstream_context_root / name)
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
        write_fake_skill(partial_context_root / name)
    partial_context_report = module.check_dependencies(settings, skills_root=partial_context_root, plugin_cache=base / "plugins" / "cache", state_path=base / "partial-context-state.json")
    partial_context = next(item for item in partial_context_report["recommended"] if item["id"] == "context-engineering")
    assert partial_context["present"] is False, partial_context
    assert partial_context["selected_skill_set"] == [
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
    ], partial_context
