"""Configuration loading and path expansion for the Verilog skill."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .workspace import require_workspace_root

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "defaults.json"
_TOKEN_RE = re.compile(r"\$\{([^}]+)\}")


def skill_root() -> Path:
    """Return the skill root directory."""

    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """Return the Git/project root containing the skill folder."""

    return skill_root().parent


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    """Load a settings JSON file and expand supported path placeholders."""

    settings_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    settings_path = settings_path.expanduser()
    if not settings_path.is_absolute():
        settings_path = (Path.cwd() / settings_path).resolve()
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Settings must be a JSON object: {settings_path}")
    raw.setdefault("version", 1)
    context = {
        "skill_dir": skill_root(),
        "project_root": project_root(),
        "settings_dir": settings_path.parent,
        "home": Path.home(),
    }
    return _expand_value(raw, context)


def workflow_defaults(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return workflow defaults from settings."""

    payload = settings or load_settings()
    workflow = payload.get("workflow", {})
    if not isinstance(workflow, dict):
        raise ValueError("settings.workflow must be an object.")
    return deepcopy(workflow)


def path_setting(settings: dict[str, Any], key: str) -> Path:
    """Return one configured path from settings.paths."""

    paths = settings.get("paths", {})
    if not isinstance(paths, dict) or key not in paths:
        raise KeyError(f"Missing settings.paths.{key}")
    return Path(str(paths[key]))


def remote_setting(settings: dict[str, Any], key: str) -> str:
    """Return one configured remote setting."""

    remote = settings.get("remote", {})
    adapted_remote = _adapted_remote_settings(settings)
    if key in adapted_remote:
        return str(adapted_remote[key])
    if not isinstance(remote, dict) or key not in remote:
        raise KeyError(f"Missing settings.remote.{key}")
    value = remote[key]
    if key in {"server_list", "toolchain_config", "server_selection_path"}:
        return str(_resolve_project_local_path(value, purpose=f"settings.remote.{key}"))
    return str(value)


def skill_dependency_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return validated skill dependency settings."""

    payload = settings or load_settings()
    dependencies = payload.get("skill_dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("settings.skill_dependencies must be an object.")
    result = deepcopy(dependencies)
    state_path = result.get("state_path")
    if not isinstance(state_path, (str, Path)) or not str(state_path):
        raise ValueError("settings.skill_dependencies.state_path must be a non-empty path.")
    result["state_path"] = _resolve_project_local_path(state_path, purpose="settings.skill_dependencies.state_path")
    for list_name in ("required", "recommended"):
        items = result.get(list_name)
        if not isinstance(items, list) or not items:
            raise ValueError(f"settings.skill_dependencies.{list_name} must be a non-empty list.")
        for item in items:
            _validate_dependency_item(item, list_name)
    if result.get("install_policy") != "ask_each_missing":
        raise ValueError("settings.skill_dependencies.install_policy must be ask_each_missing.")
    if result.get("adaptation_policy") != "required":
        raise ValueError("settings.skill_dependencies.adaptation_policy must be required.")
    return result


def fpga_developer_routing_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return validated FPGA developer skill routing settings."""

    payload = settings or load_settings()
    routing = payload.get("fpga_developer_routing")
    if not isinstance(routing, dict):
        raise ValueError("settings.fpga_developer_routing must be an object.")
    result = deepcopy(routing)
    state_path = result.get("state_path")
    if not isinstance(state_path, (str, Path)) or not str(state_path):
        raise ValueError("settings.fpga_developer_routing.state_path must be a non-empty path.")
    result["state_path"] = _resolve_project_local_path(state_path, purpose="settings.fpga_developer_routing.state_path")
    if result.get("selection_policy") != "ask_on_first_fpga_workflow":
        raise ValueError("settings.fpga_developer_routing.selection_policy must be ask_on_first_fpga_workflow.")
    if result.get("persist_selection") is not True:
        raise ValueError("settings.fpga_developer_routing.persist_selection must be true.")
    if result.get("fpga_agent_required_when_developer_present") is not False:
        raise ValueError("settings.fpga_developer_routing.fpga_agent_required_when_developer_present must be false.")
    vendors = result.get("vendors")
    if not isinstance(vendors, dict) or not vendors:
        raise ValueError("settings.fpga_developer_routing.vendors must be a non-empty object.")
    for vendor_id, vendor in vendors.items():
        if not isinstance(vendor_id, str) or not vendor_id:
            raise ValueError("settings.fpga_developer_routing vendor ids must be non-empty strings.")
        if not isinstance(vendor, dict):
            raise ValueError(f"settings.fpga_developer_routing.vendors.{vendor_id} must be an object.")
        label = vendor.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(f"settings.fpga_developer_routing.vendors.{vendor_id}.label must be non-empty.")
        skills = vendor.get("skills")
        if not isinstance(skills, list) or not skills or not all(isinstance(skill, str) and skill for skill in skills):
            raise ValueError(f"settings.fpga_developer_routing.vendors.{vendor_id}.skills must be non-empty strings.")
    return result


def _validate_dependency_item(item: Any, list_name: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"settings.skill_dependencies.{list_name} entries must be objects.")
    for key in ("id", "url", "skills"):
        if key not in item:
            raise ValueError(f"settings.skill_dependencies.{list_name} entry missing {key}.")
    if not isinstance(item["id"], str) or not item["id"]:
        raise ValueError(f"settings.skill_dependencies.{list_name} id must be non-empty.")
    if not isinstance(item["url"], str) or not item["url"].startswith("https://github.com/"):
        raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} url must be a GitHub HTTPS URL.")
    if not isinstance(item["skills"], list) or not item["skills"] or not all(isinstance(skill, str) and skill for skill in item["skills"]):
        raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} skills must be non-empty strings.")
    _validate_install_specs(item, list_name)
    alternatives = item.get("alternative_skill_sets", [])
    if alternatives and (
        not isinstance(alternatives, list)
        or not all(isinstance(group, list) and group and all(isinstance(skill, str) and skill for skill in group) for group in alternatives)
    ):
        raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} alternative_skill_sets must contain non-empty string lists.")


def _validate_install_specs(item: dict[str, Any], list_name: str) -> None:
    specs = item.get("install_specs")
    if not isinstance(specs, list) or not specs:
        raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} install_specs must be a non-empty list.")
    for spec in specs:
        if not isinstance(spec, dict):
            raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} install_specs entries must be objects.")
        if not isinstance(spec.get("skill"), str) or not spec["skill"]:
            raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} install_specs.skill must be non-empty.")
        if not isinstance(spec.get("source_path"), str) or not spec["source_path"]:
            raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} install_specs.source_path must be non-empty.")
        dest_name = spec.get("dest_name")
        if dest_name is not None and (not isinstance(dest_name, str) or not dest_name):
            raise ValueError(f"settings.skill_dependencies.{list_name}.{item['id']} install_specs.dest_name must be non-empty when present.")


def _adapted_remote_settings(settings: dict[str, Any]) -> dict[str, Any]:
    try:
        dependency_settings = skill_dependency_settings(settings)
    except (ValueError, KeyError):
        return {}
    state_path = dependency_settings["state_path"]
    if not isinstance(state_path, Path) or not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    adaptations = data.get("adaptations", {}) if isinstance(data, dict) else {}
    remote = adaptations.get("remote", {}) if isinstance(adaptations, dict) else {}
    if not isinstance(remote, dict):
        return {}
    return {
        key: value
        for key, value in remote.items()
        if isinstance(value, str) and _adapted_remote_path_valid(key, value)
    }


def _resolve_project_local_path(value: str | Path, *, purpose: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (require_workspace_root(purpose=purpose) / path).resolve()


def _adapted_remote_path_valid(key: str, value: str) -> bool:
    if key in {"helper", "settings"}:
        return Path(value).expanduser().is_file()
    if key == "server_list":
        return Path(value).expanduser().exists()
    return True


def _expand_value(value: Any, context: dict[str, Path]) -> Any:
    if isinstance(value, dict):
        return {key: _expand_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_value(item, context) for item in value]
    if isinstance(value, str):
        return _expand_string(value, context)
    return value


def _expand_string(value: str, context: dict[str, Path]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token.startswith("env:"):
            return os.environ.get(token[4:], "")
        if token in context:
            return str(context[token])
        return match.group(0)

    expanded = _TOKEN_RE.sub(replace, value)
    return str(Path(expanded).expanduser()) if _looks_like_path(expanded, original=value) else expanded


def _looks_like_path(value: str, *, original: str | None = None) -> bool:
    raw = original or value
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw):
        return False
    return (
        re.match(r"^\$\{(?:skill_dir|project_root|settings_dir|home)\}([/\\]|$)", raw) is not None
        or (raw.startswith("${env:") and _absolute_or_user_path(value))
        or _absolute_or_user_path(raw)
        or _absolute_or_user_path(value)
    )


def _absolute_or_user_path(value: str) -> bool:
    return (
        value.startswith("/")
        or "\\" in value
        or value.startswith("~")
        or re.match(r"^[A-Za-z]:", value) is not None
    )
