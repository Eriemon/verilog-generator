"""Project-local and remote runtime selection helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import local_remote_selection_path, remote_runtime_settings_relpath, remote_setting


def remote_runtime_config_relpath(settings: dict[str, Any] | None = None) -> str:
    """Return the fixed remote runtime config path relative to the remote workdir."""

    if settings is not None:
        try:
            return remote_setting(settings, "remote_runtime_config")
        except KeyError:
            pass
    return remote_runtime_settings_relpath()


def load_confirmed_remote_server(path: Path) -> dict[str, Any] | None:
    """Load the selected remote server from `.settings/remote-selection.local.json`."""

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Remote selection settings must be a JSON object: {path}")
    server_id = payload.get("server_id")
    if not isinstance(server_id, str) or not server_id.strip():
        return None
    return {
        "server_id": server_id.strip(),
        "confirmed_by_user": True,
        "updated_at": payload.get("updated_at"),
        "source": str(path),
    }


def resolve_confirmed_remote_server(settings: dict[str, Any]) -> dict[str, Any] | None:
    """Return the active selected server from project-local selection state."""

    try:
        selection_path = remote_setting(settings, "selection_path")
    except KeyError:
        selection_path = ""
    if isinstance(selection_path, str) and selection_path:
        return load_confirmed_remote_server(Path(selection_path))
    meta = settings.get("__verilog_settings_meta__", {})
    local_path = meta.get("local_selection_path") if isinstance(meta, dict) else None
    if isinstance(local_path, str) and local_path:
        return load_confirmed_remote_server(Path(local_path))
    try:
        return load_confirmed_remote_server(local_remote_selection_path())
    except Exception:
        return None


def load_remote_runtime_config(path: Path) -> dict[str, Any]:
    """Load `.settings/verilog.remote.json` from a remote workdir or downloaded copy."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Remote runtime config must be a JSON object: {path}")
    remote = payload.get("remote", {})
    if not isinstance(remote, dict):
        raise ValueError(f"Remote runtime config missing remote object: {path}")
    toolchain = remote.get("toolchain", {})
    if not isinstance(toolchain, dict):
        raise ValueError(f"Remote runtime config toolchain must be an object: {path}")
    backend = toolchain.get("simulator_backend")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError(f"Remote runtime config missing remote.toolchain.simulator_backend: {path}")
    resolved_toolchain = {
        "simulator_backend": backend.strip(),
    }
    vivado_settings = toolchain.get("vivado_settings64")
    if isinstance(vivado_settings, str) and vivado_settings.strip():
        resolved_toolchain["vivado_settings64"] = vivado_settings.strip()
    return {
        "toolchain": resolved_toolchain,
        "env": remote.get("env", {}) if isinstance(remote.get("env", {}), dict) else {},
        "tools": remote.get("tools", {}) if isinstance(remote.get("tools", {}), dict) else {},
        "source": str(path),
    }


def write_confirmed_remote_server(path: Path, server_id: str) -> Path:
    """Persist the selected remote server into `.settings/remote-selection.local.json`."""

    normalized = server_id.strip()
    if not normalized:
        raise ValueError("Confirmed remote server id must not be empty.")
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Remote selection settings must be a JSON object: {path}")
    else:
        payload = {"version": 1}
    payload["server_id"] = normalized
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
