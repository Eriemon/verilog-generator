"""Local ADC/DAC board-level use-case template selection for Verilog generation."""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "use_case_templates"
CATALOG_PATH = TEMPLATE_ROOT / "catalog.json"
_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


class UseCaseTemplateError(ValueError):
    """Raised when a requested use-case template cannot be resolved."""


@lru_cache(maxsize=1)
def load_use_case_template_catalog() -> dict[str, Any]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    templates = catalog.get("templates")
    if catalog.get("version") != 1 or not isinstance(templates, list):
        raise UseCaseTemplateError("Use-case template catalog must use version=1 and include templates.")
    for item in templates:
        _validate_catalog_entry(item)
    return catalog


def list_use_case_templates() -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in load_use_case_template_catalog()["templates"]]


def validate_use_case_template_id(template_id: str) -> str:
    normalized = str(template_id or "").strip()
    if not normalized:
        raise UseCaseTemplateError("workflow.use_case_template_id must be a non-empty string when provided.")
    if not _ID_PATTERN.fullmatch(normalized):
        raise UseCaseTemplateError("workflow.use_case_template_id must use lowercase letters, digits, and underscores only.")
    matches = [item for item in load_use_case_template_catalog()["templates"] if item.get("template_id") == normalized]
    if not matches:
        raise UseCaseTemplateError(
            "workflow.use_case_template_id="
            + repr(normalized)
            + " is not valid. Expected one of: "
            + ", ".join(item["template_id"] for item in load_use_case_template_catalog()["templates"])
            + "."
        )
    return normalized


def select_use_case_template(spec: dict[str, Any]) -> dict[str, Any] | None:
    workflow = spec.get("workflow", {}) if isinstance(spec.get("workflow"), dict) else {}
    requested_id = workflow.get("use_case_template_id")
    if requested_id in (None, ""):
        return None
    return resolve_use_case_template(validate_use_case_template_id(str(requested_id)))


def resolve_use_case_template(template_id: str) -> dict[str, Any]:
    normalized = validate_use_case_template_id(template_id)
    entry = next(
        item for item in load_use_case_template_catalog()["templates"]
        if item.get("template_id") == normalized
    )
    template_dir = TEMPLATE_ROOT / str(entry["directory"])
    manifest_path = template_dir / "manifest.json"
    if not manifest_path.exists():
        raise UseCaseTemplateError(f"Use-case template {normalized!r} is missing manifest.json.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise UseCaseTemplateError(f"Use-case template {normalized!r} manifest must use version=1.")
    if manifest.get("template_id") != normalized:
        raise UseCaseTemplateError(f"Use-case template {normalized!r} manifest template_id does not match catalog.")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise UseCaseTemplateError(f"Use-case template {normalized!r} manifest must contain artifacts.")

    payload = copy.deepcopy(entry)
    payload["selection_reason"] = "selected by explicit workflow.use_case_template_id"
    payload["template_root"] = str(template_dir)
    payload["manifest_path"] = str(manifest_path)
    payload["display_name"] = manifest.get("display_name", entry.get("display_name"))
    payload["applicable_scenarios"] = copy.deepcopy(manifest.get("applicable_scenarios", []))
    payload["parameterization_points"] = copy.deepcopy(manifest.get("parameterization_points", entry.get("parameterization_points", [])))
    payload["source_projects"] = copy.deepcopy(manifest.get("source_projects", entry.get("source_projects", [])))
    payload["summary"] = str(manifest.get("summary") or entry.get("summary") or "")
    payload["artifacts"] = [_load_artifact(template_dir, item) for item in artifacts]
    return payload


def summarize_use_case_template(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {
            "id": None,
            "display_name": None,
            "summary": "",
            "source_projects": [],
            "parameterization_points": [],
            "selection_reason": "no explicit use-case template selected",
            "artifacts": [],
        }
    return {
        "id": payload.get("template_id"),
        "display_name": payload.get("display_name"),
        "summary": payload.get("summary", ""),
        "source_projects": copy.deepcopy(payload.get("source_projects", [])),
        "parameterization_points": copy.deepcopy(payload.get("parameterization_points", [])),
        "selection_reason": payload.get("selection_reason"),
        "artifacts": [
            {
                "kind": item.get("kind"),
                "path": item.get("relative_path"),
                "status": item.get("status"),
                "summary": item.get("summary"),
            }
            for item in payload.get("artifacts", [])
        ],
    }


def _validate_catalog_entry(item: Any) -> None:
    if not isinstance(item, dict):
        raise UseCaseTemplateError("Use-case template catalog entries must be JSON objects.")
    for key in ("template_id", "directory", "display_name", "source_projects", "parameterization_points"):
        if key not in item:
            raise UseCaseTemplateError(f"Use-case template catalog entry missing {key}.")
    _validate_template_field(str(item["template_id"]), "template_id")
    if not isinstance(item["directory"], str) or not item["directory"]:
        raise UseCaseTemplateError(f"Use-case template {item['template_id']!r} directory must be a non-empty string.")
    if not isinstance(item["source_projects"], list) or not item["source_projects"]:
        raise UseCaseTemplateError(f"Use-case template {item['template_id']!r} source_projects must be a non-empty list.")
    if not isinstance(item["parameterization_points"], list) or not item["parameterization_points"]:
        raise UseCaseTemplateError(f"Use-case template {item['template_id']!r} parameterization_points must be a non-empty list.")


def _validate_template_field(value: str, field_name: str) -> None:
    if not _ID_PATTERN.fullmatch(value):
        raise UseCaseTemplateError(f"Use-case template {field_name} must use lowercase letters, digits, and underscores only: {value!r}.")


def _load_artifact(template_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise UseCaseTemplateError("Use-case template artifacts must be JSON objects.")
    for key in ("kind", "path", "status", "summary"):
        if key not in item:
            raise UseCaseTemplateError(f"Use-case template artifact missing {key}.")
    artifact_path = template_dir / str(item["path"])
    if not artifact_path.exists():
        raise UseCaseTemplateError(f"Use-case template artifact is missing: {artifact_path}")
    payload = copy.deepcopy(item)
    payload["absolute_path"] = str(artifact_path)
    payload["relative_path"] = artifact_path.relative_to(SKILL_ROOT).as_posix()
    payload["content"] = artifact_path.read_text(encoding="utf-8")
    return payload
