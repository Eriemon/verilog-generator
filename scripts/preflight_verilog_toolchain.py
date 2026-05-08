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

from runtime.verilog_generator.config import load_settings  # noqa: E402
from runtime.verilog_generator.validation import READINESS_LEVELS, readiness_at_least, require_readiness  # noqa: E402


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
    remote_selection_required = requires_external and (not vivado["found"] or not xsim_available)
    remote = settings.get("remote", {}) if isinstance(settings.get("remote", {}), dict) else {}

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
            "recommended_server": remote.get("server"),
            "recommended_server_name": remote.get("server_name"),
            "server_confirmed": remote.get("server_confirmed") is True,
        },
    }
    if remote_selection_required:
        report["reason"] = "Local Vivado or xsim is unavailable for external readiness."
        report["required_action"] = "Run erie-remote-ssh discover and choices, then ask the user to select a server before remote validation."
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
