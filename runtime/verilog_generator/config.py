"""Configuration loading and path expansion for the Verilog skill."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

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
    if not isinstance(remote, dict) or key not in remote:
        raise KeyError(f"Missing settings.remote.{key}")
    return str(remote[key])


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
    return str(Path(expanded).expanduser()) if _looks_like_path(expanded) else expanded


def _looks_like_path(value: str) -> bool:
    return (
        "/" in value
        or "\\" in value
        or value.startswith("~")
        or re.match(r"^[A-Za-z]:", value) is not None
    )
