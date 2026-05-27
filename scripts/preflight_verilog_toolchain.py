"""Report whether local Verilog tool validation needs a remote server choice."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.verilog_generator.config import load_settings, policy_setting, remote_setting
from runtime.verilog_generator.remote_selection import remote_runtime_config_relpath, resolve_confirmed_remote_server
from runtime.verilog_generator.validation import READINESS_LEVELS, readiness_at_least, require_readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight local Verilog validation tools.")
    parser.add_argument("--settings", type=Path, default=SKILL_ROOT / "config" / "defaults.json")
    parser.add_argument("--readiness", choices=READINESS_LEVELS, default="static")
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    readiness = require_readiness(args.readiness)
    report = build_report(settings, readiness)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def build_report(settings: dict, readiness: str) -> dict:
    vivado = _tool("vivado")
    xsim_tools = {tool: _tool(tool) for tool in ("xvlog", "xelab", "xsim")}
    xsim_available = all(item["found"] for item in xsim_tools.values())
    requires_external = readiness_at_least(readiness, "compile")
    remote_first = bool(policy_setting(settings, "prefer_remote_for_external_validation", True))
    remote_blocking = bool(policy_setting(settings, "block_when_remote_unconfigured", True))
    allow_implicit_local_external = bool(policy_setting(settings, "allow_implicit_local_external_validation", False))
    remote_selection_required = requires_external and (remote_first or remote_blocking or not allow_implicit_local_external)
    confirmed = resolve_confirmed_remote_server(settings)
    server_list_path = Path(remote_setting(settings, "server_list"))
    server_list_exists = server_list_path.exists()
    runtime_config = remote_runtime_config_relpath(settings)
    meta = settings.get("__verilog_settings_meta__", {})
    legacy_remote_state = meta.get("legacy_remote_state", []) if isinstance(meta, dict) else []
    local_settings_loaded = bool(meta.get("local_settings_loaded")) if isinstance(meta, dict) else False

    report = {
        "version": 1,
        "readiness": readiness,
        "local": {
            "vivado": vivado,
            "xsim": {
                "available": xsim_available,
                "tools": xsim_tools,
            },
        },
        "remote_selection_required": remote_selection_required,
        "remote": {
            "recommended_server": confirmed["server_id"] if confirmed else None,
            "recommended_server_name": None,
            "server_confirmed": confirmed is not None,
            "server_list_path": str(server_list_path),
            "server_list_exists": server_list_exists,
            "remote_runtime_config": runtime_config,
        },
    }
    if remote_selection_required:
        report["reason"] = "External readiness uses a remote-first policy; local Vivado/xsim availability does not auto-enable local external validation."
        if legacy_remote_state and not local_settings_loaded:
            report["required_action"] = "Legacy .erie-verilog-generator-state remote settings were detected. Migrate the selected server into .settings/remote-selection.local.json and regenerate .settings/server_list.local.json before remote validation."
        elif not server_list_exists:
            report["required_action"] = f"Create or refresh .settings/server_list.local.json through erie-remote-ssh before remote validation. Expected path: {server_list_path}"
        elif report["remote"]["server_confirmed"]:
            report["required_action"] = f"Use the selected remote server {confirmed['server_id']} and ensure the remote workdir contains {runtime_config} before remote validation."
        else:
            report["required_action"] = f"Select a remote server in .settings/remote-selection.local.json and ensure {runtime_config} exists on the remote workdir before remote validation."
    else:
        report["reason"] = "Local static validation does not require Vivado/xsim, or local Vivado/xsim is available."
        report["required_action"] = None
    return report


def _tool(name: str) -> dict:
    path = shutil.which(name)
    return {
        "found": path is not None,
        "path": path,
    }


if __name__ == "__main__":
    raise SystemExit(main())
