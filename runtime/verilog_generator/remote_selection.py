"""Project-local remote server selection helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import remote_setting


def remote_server_selection_path(settings: dict[str, Any]) -> Path:
    """Return the resolved project-local remote server selection path."""

    return Path(remote_setting(settings, "server_selection_path"))


def load_confirmed_remote_server(path: Path) -> dict[str, Any] | None:
    """Load one confirmed remote server selection from project-local state."""

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Remote server selection must be a JSON object: {path}")
    server_id = payload.get("server_id")
    if not isinstance(server_id, str) or not server_id.strip():
        return None
    if payload.get("confirmed_by_user") is not True:
        return None
    return {
        "server_id": server_id.strip(),
        "confirmed_by_user": True,
        "updated_at": payload.get("updated_at"),
        "source": str(path),
    }


def resolve_confirmed_remote_server(settings: dict[str, Any]) -> dict[str, Any] | None:
    """Return the active confirmed server from settings or project-local state."""

    remote = settings.get("remote", {})
    if isinstance(remote, dict) and remote.get("server_confirmed") is True:
        server_id = remote.get("server")
        if isinstance(server_id, str) and server_id.strip():
            return {
                "server_id": server_id.strip(),
                "confirmed_by_user": True,
                "updated_at": None,
                "source": "settings.remote.server",
            }
    return load_confirmed_remote_server(remote_server_selection_path(settings))


def write_confirmed_remote_server(path: Path, server_id: str) -> Path:
    """Persist a user-confirmed server id without copying sensitive host details."""

    normalized = server_id.strip()
    if not normalized:
        raise ValueError("Confirmed remote server id must not be empty.")
    payload = {
        "version": 1,
        "server_id": normalized,
        "confirmed_by_user": True,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
