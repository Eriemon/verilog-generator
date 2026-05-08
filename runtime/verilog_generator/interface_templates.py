"""Local bus interface template selection for Verilog generation."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "interface_templates"
CATALOG_PATH = TEMPLATE_ROOT / "catalog.json"


class InterfaceTemplateError(ValueError):
    """Raised when a requested local interface template cannot be resolved."""


@lru_cache(maxsize=1)
def load_interface_template_catalog() -> dict[str, Any]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("version") != 1 or not isinstance(catalog.get("templates"), list):
        raise InterfaceTemplateError("Interface template catalog must use version=1 and include templates.")
    return catalog


def list_interface_templates() -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in load_interface_template_catalog()["templates"]]


def select_interface_template(spec: dict[str, Any]) -> dict[str, Any] | None:
    interface_family = spec.get("interface_family")
    profile = spec.get("interface_profile", {}) if isinstance(spec.get("interface_profile"), dict) else {}
    if interface_family not in {"axi_stream", "axi4", "axi4_lite", "ahb", "apb"}:
        return None
    return resolve_interface_template(str(interface_family), profile)


def resolve_interface_template(interface_family: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or {}
    requested_id = str(profile.get("template_id") or "").strip()
    role = str(profile.get("role") or "").strip().lower()
    read_write_mode = str(profile.get("read_write_mode") or "").strip().lower()
    candidates = [
        item
        for item in load_interface_template_catalog()["templates"]
        if item.get("interface_family") == interface_family
    ]
    if requested_id:
        matches = [item for item in candidates if item.get("template_id") == requested_id]
        if not matches:
            raise InterfaceTemplateError(
                f"interface_profile.template_id={requested_id!r} is not valid for interface_family={interface_family!r}."
            )
        selected = matches[0]
        reason = "selected by explicit interface_profile.template_id"
    else:
        matches = [
            item
            for item in candidates
            if _role_matches(item, role) and _mode_matches(item, read_write_mode)
        ]
        if not matches:
            raise InterfaceTemplateError(
                f"No local interface template matches interface_family={interface_family!r}, role={role!r}, read_write_mode={read_write_mode!r}."
            )
        selected = matches[0]
        reason = "selected by interface_family, role, and read_write_mode defaults"
    payload = copy.deepcopy(selected)
    path = TEMPLATE_ROOT / str(payload["path"])
    payload["content"] = path.read_text(encoding="utf-8")
    payload["selection_reason"] = reason
    payload["strict_naming_policy"] = "strict_preferred"
    return payload


def _role_matches(item: dict[str, Any], role: str) -> bool:
    template_role = str(item.get("role") or "").lower()
    if template_role == "duplex":
        return role in {"", "duplex", "master", "slave"}
    return role in {"", template_role}


def _mode_matches(item: dict[str, Any], read_write_mode: str) -> bool:
    template_mode = str(item.get("read_write_mode") or "").lower()
    if template_mode == "read_write":
        return read_write_mode in {"", "read", "write", "read_write"}
    return read_write_mode in {"", template_mode}
