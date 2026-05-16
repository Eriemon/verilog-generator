"""Check, prompt for, install, and adapt Verilog skill dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.verilog_generator.config import fpga_developer_routing_settings, load_settings, skill_dependency_settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.settings)
    skills_root = args.skills_root or default_skills_root()
    plugin_cache = args.plugin_cache or default_plugin_cache()
    try:
        state_path = args.state_path or skill_dependency_settings(settings)["state_path"]
    except ValueError as exc:
        parser.error(str(exc))
        raise AssertionError("unreachable") from exc

    if args.command == "check":
        print_json(check_dependencies(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path))
        return 0
    if args.command == "prompt":
        print(prompt_for_missing(check_dependencies(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path)))
        return 0
    if args.command == "skip":
        record_skip(settings, args.dependency_id, state_path=state_path)
        print_json({"skipped": args.dependency_id, "state_path": str(state_path)})
        return 0
    if args.command == "select-fpga-vendor":
        print_json(
            select_fpga_vendor(
                settings,
                args.vendor_id,
                skills_root=skills_root,
                plugin_cache=plugin_cache,
                state_path=state_path,
            )
        )
        return 0
    if args.command == "fpga-route":
        print_json(fpga_route(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path))
        return 0
    if args.command == "adapt":
        print_json(adapt_dependencies(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path))
        return 0
    if args.command == "cleanup-fpga-agent-skills":
        print_json(
            cleanup_fpga_agent_skills(
                settings,
                skills_root=skills_root,
                plugin_cache=plugin_cache,
                backup_root=args.backup_root,
                yes=args.yes,
            )
        )
        return 0
    if args.command == "install":
        if not args.yes:
            parser.error("install requires --yes after the user confirms installation.")
        report = check_dependencies(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path)
        print_json(
            install_missing(
                settings,
                report,
                args.dependency_id,
                installer=args.installer,
                allow_fpga_agent_fallback=args.allow_fpga_agent_fallback,
            )
        )
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage erie-verilog-generator skill dependencies.")
    _add_common_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_common_args(subparsers.add_parser("check", help="Check installed dependency skills and print JSON."))
    _add_common_args(subparsers.add_parser("prompt", help="Render a user-facing installation prompt."))
    adapt = subparsers.add_parser("adapt", help="Persist discovered dependency adaptations.")
    _add_common_args(adapt)
    adapt.set_defaults(command="adapt")
    skip = subparsers.add_parser("skip", help="Record a recommended dependency as skipped.")
    _add_common_args(skip)
    skip.add_argument("dependency_id")
    select_vendor = subparsers.add_parser("select-fpga-vendor", help="Persist the user-selected FPGA vendor for developer skill routing.")
    _add_common_args(select_vendor)
    select_vendor.add_argument("vendor_id", choices=("amd_xilinx", "pangomicro"))
    route = subparsers.add_parser("fpga-route", help="Report the selected FPGA developer skill route.")
    _add_common_args(route)
    route.set_defaults(command="fpga-route")
    cleanup = subparsers.add_parser("cleanup-fpga-agent-skills", help="Move legacy FPGA-Agent Vivado/Vitis skills to a backup directory.")
    _add_common_args(cleanup)
    cleanup.add_argument("--backup-root", type=Path, help="Override backup root for moved FPGA-Agent skills.")
    cleanup.add_argument("--yes", action="store_true", help="Required confirmation to move legacy FPGA-Agent skills.")
    install = subparsers.add_parser("install", help="Install missing dependencies after user confirmation.")
    _add_common_args(install)
    install.add_argument("--dependency-id", help="Install only one dependency id.")
    install.add_argument("--installer", type=Path, help="Override skill-installer helper script.")
    install.add_argument("--yes", action="store_true", help="Required confirmation that the user approved installation.")
    install.add_argument("--allow-fpga-agent-fallback", action="store_true", help="Explicitly allow FPGA-Agent-Skills fallback installation when no developer skill exists.")
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--settings", type=Path, default=SKILL_ROOT / "config" / "defaults.json")
    parser.add_argument("--skills-root", type=Path, help="Override Codex skills root for checks.")
    parser.add_argument("--plugin-cache", type=Path, help="Override Codex plugin cache root for checks.")
    parser.add_argument("--state-path", type=Path, help="Override dependency state path.")


def check_dependencies(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    dependency_settings = skill_dependency_settings(settings)
    skills_root = (skills_root or default_skills_root()).expanduser()
    plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()
    state_path = (state_path or dependency_settings["state_path"]).expanduser()
    state = read_state(state_path)
    skipped = _active_skipped_recommended(state, dependency_settings, settings.get("version"))

    developer_skills = fpga_developer_status(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path)
    developer_present = bool(developer_skills["available_vendors"])
    fpga_agent_skipped = developer_present and not developer_skills["fpga_agent_required_when_developer_present"]

    required = []
    for item in dependency_settings["required"]:
        status = _dependency_status(item, "required", skills_root, plugin_cache)
        if item["id"] == "fpga-agent-skills" and fpga_agent_skipped:
            status["present"] = True
            status["missing_skills"] = []
            status["skipped_by_developer_skill"] = True
        required.append(status)
    recommended_all = [_dependency_status(item, "recommended", skills_root, plugin_cache) for item in dependency_settings["recommended"]]
    recommended = [item for item in recommended_all if item["id"] not in skipped]

    missing_required = [item for item in required if not item["present"]]
    missing_recommended = [item for item in recommended if not item["present"]]
    return {
        "version": 1,
        "ok": not missing_required and not missing_recommended,
        "required_ok": not missing_required,
        "recommended_ok": not missing_recommended,
        "skills_root": str(skills_root),
        "plugin_cache": str(plugin_cache),
        "state_path": str(state_path),
        "developer_skills": developer_skills,
        "active_fpga_dependency_mode": "developer_skill" if developer_present else "fpga_agent_required",
        "fpga_agent_skipped_by_developer_skill": fpga_agent_skipped,
        "required": required,
        "recommended": recommended_all,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "skipped_recommended": sorted(skipped),
    }


def prompt_for_missing(report: dict) -> str:
    missing_required = report.get("missing_required", [])
    missing_recommended = report.get("missing_recommended", [])
    developer_skills = report.get("developer_skills", {})
    selection_required = bool(developer_skills.get("selection_required"))
    if not missing_required and not missing_recommended and not selection_required:
        return "All erie-verilog-generator skill dependencies are installed. Run adapt after a fresh install to refresh project-local helper paths."
    lines = [
        "erie-verilog-generator dependency check found missing skills.",
        "",
    ]
    if selection_required:
        lines.append("Multiple FPGA developer vendors are available. Ask the user which vendor to use for this FPGA workflow:")
        for vendor_id in developer_skills.get("available_vendors", []):
            vendor = developer_skills.get("vendors", {}).get(vendor_id, {})
            lines.append(f"- {vendor.get('label', vendor_id)}: select-fpga-vendor {vendor_id}")
        lines.append("")
    if missing_required:
        lines.append("Missing required dependency groups. These block remote/Vivado-related workflows until resolved:")
        for item in missing_required:
            if item["id"] == "fpga-agent-skills":
                lines.append(
                    "- fpga-agent-skills: manual fallback only. Prefer installing or enabling vivado-developer, "
                    "vitis-developer, or pds-developer instead of installing FPGA-Agent Vivado/Vitis child skills."
                )
            else:
                lines.append(f"- {item['id']}: {item['url']} ({', '.join(item['missing_skills'])})")
        lines.append("")
    if missing_recommended:
        lines.append("Missing recommended dependency groups. Ask the user whether to install or skip them for this version:")
        for item in missing_recommended:
            lines.append(f"- {item['id']}: {item['url']} ({', '.join(item['missing_skills'])})")
        lines.append("")
    lines.append("Install only after the user confirms. After installation, tell the user to restart Codex so new skills are discovered.")
    return "\n".join(lines)


def record_skip(settings: dict, dependency_id: str, *, state_path: Path | None = None) -> dict:
    dependency_settings = skill_dependency_settings(settings)
    known = {item["id"]: item for item in dependency_settings["recommended"]}
    if dependency_id not in known:
        raise ValueError(f"Only recommended dependencies can be skipped: {dependency_id}")
    state_path = (state_path or dependency_settings["state_path"]).expanduser()
    state = read_state(state_path)
    skipped = set(state.get("skipped_recommended", []))
    skipped.add(dependency_id)
    state["skipped_recommended"] = sorted(skipped)
    fingerprints = state.setdefault("skipped_recommended_fingerprints", {})
    fingerprints[dependency_id] = _dependency_fingerprint(known[dependency_id], settings.get("version"))
    state.setdefault("version", 1)
    state["updated_at"] = utc_now()
    write_state(state_path, state)
    return state


def adapt_dependencies(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    dependency_settings = skill_dependency_settings(settings)
    skills_root = (skills_root or default_skills_root()).expanduser()
    plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()
    state_path = (state_path or dependency_settings["state_path"]).expanduser()
    report = check_dependencies(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path)
    if report["missing_required"]:
        return {"adapted": [], "blocked": [item["id"] for item in report["missing_required"]], "state_path": str(state_path)}

    state = read_state(state_path)
    adaptations = state.setdefault("adaptations", {})
    adapted: list[str] = []
    remote_status = next((item for item in report["required"] if item["id"] == "erie-remote-ssh"), None)
    if remote_status and remote_status.get("present"):
        skill_path = Path(remote_status["skill_paths"]["erie-remote-ssh"])
        helper = skill_path / "scripts" / "remote_ssh.py"
        remote_settings = skill_path / "config" / "defaults.json"
        if helper.is_file() and remote_settings.is_file():
            adaptations["remote"] = {
                "helper": str(helper.resolve()),
                "settings": str(remote_settings.resolve()),
            }
            adapted.append("erie-remote-ssh")
        else:
            return {
                "adapted": [],
                "blocked": ["erie-remote-ssh"],
                "reason": "Installed erie-remote-ssh is missing scripts/remote_ssh.py or config/defaults.json.",
                "state_path": str(state_path),
            }
    state.setdefault("version", 1)
    state["updated_at"] = utc_now()
    write_state(state_path, state)
    return {"adapted": adapted, "blocked": [], "state_path": str(state_path)}


def fpga_developer_status(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    routing = fpga_developer_routing_settings(settings)
    skills_root = (skills_root or default_skills_root()).expanduser()
    plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()
    state_path = (state_path or routing["state_path"]).expanduser()
    state = read_state(state_path)
    selected = _fpga_selection_from_state(state)
    vendors: dict[str, dict] = {}
    available_vendors: list[str] = []
    for vendor_id, vendor in routing["vendors"].items():
        skill_paths: dict[str, str] = {}
        for skill in vendor["skills"]:
            found = find_skill(skill, skills_root, plugin_cache)
            if found:
                skill_paths[skill] = str(found)
        selected_skill = next((skill for skill in vendor["skills"] if skill in skill_paths), None)
        present = selected_skill is not None
        if present:
            available_vendors.append(vendor_id)
        vendors[vendor_id] = {
            "label": vendor["label"],
            "skills": vendor["skills"],
            "present": present,
            "selected_skill": selected_skill,
            "skill_paths": skill_paths,
        }
    selected_vendor = selected.get("vendor") if isinstance(selected, dict) else None
    selection_valid = bool(selected_vendor in available_vendors)
    selection_stale = bool(selected_vendor and not selection_valid)
    selection_required = len(available_vendors) > 1 and not selection_valid
    return {
        "state_path": str(state_path),
        "available_vendors": available_vendors,
        "vendors": vendors,
        "selected_vendor": selected_vendor if selection_valid else None,
        "selection_required": selection_required,
        "selection_stale": selection_stale,
        "fpga_agent_required_when_developer_present": routing["fpga_agent_required_when_developer_present"],
    }


def select_fpga_vendor(
    settings: dict,
    vendor_id: str,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    status = fpga_developer_status(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path)
    vendor = status["vendors"].get(vendor_id)
    if not vendor:
        raise ValueError(f"Unknown FPGA vendor: {vendor_id}")
    if not vendor["present"]:
        raise ValueError(f"FPGA vendor {vendor_id} has no installed developer skill.")
    path = Path(state_path or status["state_path"]).expanduser()
    state = read_state(path)
    state["fpga_developer_selection"] = {
        "vendor": vendor_id,
        "skill": vendor["selected_skill"],
        "updated_at": utc_now(),
    }
    state.setdefault("version", 1)
    state["updated_at"] = utc_now()
    write_state(path, state)
    return {
        "selected_vendor": vendor_id,
        "selected_skill": vendor["selected_skill"],
        "state_path": str(path),
    }


def fpga_route(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    status = fpga_developer_status(settings, skills_root=skills_root, plugin_cache=plugin_cache, state_path=state_path)
    available = status["available_vendors"]
    if not available:
        dependency_settings = skill_dependency_settings(settings)
        fpga = next(item for item in dependency_settings["required"] if item["id"] == "fpga-agent-skills")
        return {"status": "fpga_agent", "fallback_skills": fpga["skills"]}
    if status["selection_stale"]:
        return {"status": "selection_stale", "available_vendors": available}
    if status["selection_required"]:
        return {"status": "selection_required", "available_vendors": available}
    selected_vendor = status["selected_vendor"] or available[0]
    vendor = status["vendors"][selected_vendor]
    return {
        "status": "ready",
        "selected_vendor": selected_vendor,
        "selected_skill": vendor["selected_skill"],
        "skill_path": vendor["skill_paths"][vendor["selected_skill"]],
    }


FPGA_AGENT_CHILD_SKILLS = (
    "vivado-tcl",
    "vivado-sim",
    "vivado-synth",
    "vivado-impl",
    "vivado-analysis",
    "vivado-constraints",
    "vivado-debug",
    "vitis-hls-synthesis",
)


def install_missing(
    settings: dict,
    report: dict,
    dependency_id: str | None = None,
    *,
    installer: Path | None = None,
    allow_fpga_agent_fallback: bool = False,
) -> dict:
    dependency_settings = skill_dependency_settings(settings)
    dependencies = {item["id"]: item for item in [*dependency_settings["required"], *dependency_settings["recommended"]]}
    missing = [*report.get("missing_required", []), *report.get("missing_recommended", [])]
    if dependency_id:
        missing = [item for item in missing if item["id"] == dependency_id]
    if not missing:
        if dependency_id == "fpga-agent-skills" and report.get("fpga_agent_skipped_by_developer_skill"):
            return {"installed": [], "skipped": [{"dependency_id": "fpga-agent-skills", "reason": "developer skill is installed"}], "restart_required": False}
        return {"installed": [], "message": "No missing dependencies selected."}
    installer = installer or default_installer_script()
    if not installer.is_file():
        raise FileNotFoundError(f"Missing skill installer helper: {installer}")
    installed: list[str] = []
    skipped: list[dict] = []
    for status in missing:
        if status["id"] == "fpga-agent-skills" and report.get("fpga_agent_skipped_by_developer_skill"):
            skipped.append({"dependency_id": "fpga-agent-skills", "reason": "developer skill is installed"})
            continue
        if status["id"] == "fpga-agent-skills" and not allow_fpga_agent_fallback:
            skipped.append({"dependency_id": "fpga-agent-skills", "reason": "manual fallback approval required"})
            continue
        dependency = dependencies[status["id"]]
        repo = github_repo_slug(dependency["url"])
        selected_specs = _selected_install_specs(dependency, status["missing_skills"])
        for spec in selected_specs:
            command = [sys.executable, str(installer), "--repo", repo, "--path", str(spec["source_path"])]
            if spec.get("dest_name"):
                command.extend(["--name", str(spec["dest_name"])])
            subprocess.run(command, check=True)
            installed.append(str(spec["skill"]))
    return {"installed": installed, "skipped": skipped, "restart_required": bool(installed)}


def cleanup_fpga_agent_skills(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    backup_root: Path | None = None,
    yes: bool = False,
) -> dict:
    if not yes:
        raise ValueError("cleanup-fpga-agent-skills requires --yes.")
    skills_root = (skills_root or default_skills_root()).expanduser()
    plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()
    backup_root = (backup_root or (default_skills_root().parent / "skill-backups")).expanduser()
    developer_status = fpga_developer_status(settings, skills_root=skills_root, plugin_cache=plugin_cache)
    if not developer_status["available_vendors"]:
        raise ValueError("Refusing cleanup because no FPGA developer skill is installed.")
    resolved_skills_root = skills_root.resolve()
    resolved_backup_root = backup_root.resolve()
    backup_dir = backup_root / f"fpga-agent-skills.bak.{time.strftime('%Y%m%dT%H%M%S')}"
    moved: list[str] = []
    for skill in FPGA_AGENT_CHILD_SKILLS:
        source = skills_root / skill
        if not source.exists():
            continue
        source.resolve().relative_to(resolved_skills_root)
        if not (source / "SKILL.md").is_file():
            raise ValueError(f"Refusing to move unexpected skill directory without SKILL.md: {source}")
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / skill
        target.parent.resolve().relative_to(resolved_backup_root)
        if target.exists():
            raise ValueError(f"Backup target already exists: {target}")
        source.rename(target)
        moved.append(skill)
    return {
        "moved": moved,
        "backup_dir": str(backup_dir),
        "skills_root": str(skills_root),
        "plugin_cache": str(plugin_cache),
        "developer_vendors": developer_status["available_vendors"],
    }


def _fpga_selection_from_state(state: dict) -> dict:
    selection = state.get("fpga_developer_selection", {})
    return selection if isinstance(selection, dict) else {}


def _install_specs_by_skill(dependency: dict) -> dict[str, dict]:
    specs = dependency.get("install_specs", [])
    return {str(item["skill"]): item for item in specs if isinstance(item, dict) and item.get("skill")}


def _selected_install_specs(dependency: dict, missing_skills: list[str]) -> list[dict]:
    specs = _install_specs_by_skill(dependency)
    if not missing_skills:
        return []
    if all(skill in specs for skill in missing_skills):
        return [specs[skill] for skill in missing_skills]
    if dependency.get("alternative_skill_sets"):
        return list(specs.values())
    missing = [skill for skill in missing_skills if skill not in specs]
    raise ValueError(f"Missing install spec for {', '.join(missing)} in dependency {dependency['id']!r}.")


def _dependency_status(item: dict, kind: str, skills_root: Path, plugin_cache: Path) -> dict:
    skill_paths, missing = _resolve_skill_set(item["skills"], skills_root, plugin_cache)
    selected_skill_set = item["skills"]
    if missing:
        for alternative in item.get("alternative_skill_sets", []):
            alt_paths, alt_missing = _resolve_skill_set(alternative, skills_root, plugin_cache)
            if not alt_missing:
                skill_paths = alt_paths
                missing = []
                selected_skill_set = alternative
                break
            if len(alt_missing) < len(missing) or (alt_paths and len(alt_missing) == len(missing)):
                missing = alt_missing
                skill_paths = alt_paths
                selected_skill_set = alternative
    return {
        "id": item["id"],
        "kind": kind,
        "url": item["url"],
        "purpose": item.get("purpose", ""),
        "present": not missing,
        "skills": item["skills"],
        "selected_skill_set": selected_skill_set,
        "missing_skills": missing,
        "skill_paths": skill_paths,
    }


def _resolve_skill_set(skills: list[str], skills_root: Path, plugin_cache: Path) -> tuple[dict[str, str], list[str]]:
    skill_paths: dict[str, str] = {}
    missing: list[str] = []
    for skill in skills:
        found = find_skill(skill, skills_root, plugin_cache)
        if found:
            skill_paths[skill] = str(found)
        else:
            missing.append(skill)
    return skill_paths, missing


def find_skill(skill: str, skills_root: Path, plugin_cache: Path) -> Path | None:
    direct = skills_root / skill
    if (direct / "SKILL.md").is_file():
        return direct.resolve()
    if plugin_cache.exists():
        for candidate in plugin_cache.rglob(skill):
            if candidate.is_dir() and (candidate / "SKILL.md").is_file() and candidate.parent.name == "skills":
                return candidate.resolve()
    return None


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "skipped_recommended": [], "skipped_recommended_fingerprints": {}, "adaptations": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Dependency state must be a JSON object: {path}")
    data.setdefault("version", 1)
    data.setdefault("skipped_recommended", [])
    data.setdefault("skipped_recommended_fingerprints", {})
    data.setdefault("adaptations", {})
    if not isinstance(data["skipped_recommended"], list):
        data["skipped_recommended"] = []
    if not isinstance(data["skipped_recommended_fingerprints"], dict):
        data["skipped_recommended_fingerprints"] = {}
    return data


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def default_skills_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills"
    return Path.home() / ".codex" / "skills"


def default_plugin_cache() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "plugins" / "cache"
    return Path.home() / ".codex" / "plugins" / "cache"


def default_installer_script() -> Path:
    return default_skills_root() / ".system" / "skill-installer" / "scripts" / "install-skill-from-github.py"


def github_repo_slug(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2 or parsed.netloc.lower() != "github.com":
        raise ValueError(f"Unsupported GitHub dependency URL: {url}")
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{parts[0]}/{repo}"


def _active_skipped_recommended(state: dict, dependency_settings: dict, settings_version: object) -> set[str]:
    skipped = set(state.get("skipped_recommended", []))
    fingerprints = state.get("skipped_recommended_fingerprints", {})
    active: set[str] = set()
    for item in dependency_settings["recommended"]:
        dependency_id = item["id"]
        if dependency_id in skipped and fingerprints.get(dependency_id) == _dependency_fingerprint(item, settings_version):
            active.add(dependency_id)
    return active


def _dependency_fingerprint(item: dict, settings_version: object) -> str:
    payload = {
        "settings_version": settings_version,
        "id": item.get("id"),
        "url": item.get("url"),
        "skills": item.get("skills"),
        "alternative_skill_sets": item.get("alternative_skill_sets", []),
        "install_specs": item.get("install_specs", []),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
