"""Workspace path safety and workflow-state indexing."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator

from .spec import SpecError

DEFAULT_STATE_PATH = Path("workflow-state.json")
PROTECTED_WRITE_DIRS = {"Spec2RTL-Agent"}
_WORKSPACE_ROOT_OVERRIDE: ContextVar[Path | None] = ContextVar("verilog_generator_workspace_root", default=None)


def workspace_root() -> Path:
    override = _WORKSPACE_ROOT_OVERRIDE.get()
    if override is not None:
        return override.resolve()
    return Path.cwd().resolve()


@contextmanager
def use_workspace_root(root: Path) -> Iterator[Path]:
    resolved = Path(root).resolve()
    token = _WORKSPACE_ROOT_OVERRIDE.set(resolved)
    try:
        yield resolved
    finally:
        _WORKSPACE_ROOT_OVERRIDE.reset(token)


def require_workspace_path(path: Path, *, purpose: str = "path", must_exist: bool = False) -> Path:
    root = workspace_root()
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        raise SpecError(f"{purpose} does not exist: {path}") from None
    except OSError as exc:
        raise SpecError(f"Could not resolve {purpose}: {path}: {exc}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SpecError(f"{purpose} must stay inside the current workspace: {path}") from exc
    return resolved


def require_workspace_path_from(
    anchor: Path,
    path: Path,
    *,
    purpose: str = "path",
    must_exist: bool = False,
) -> Path:
    base = anchor if anchor.is_dir() else anchor.parent
    if path.is_absolute():
        candidate = path
    else:
        candidate = base / path
        if must_exist and not candidate.exists():
            root = workspace_root()
            search_roots = [base, *base.parents]
            for search_root in search_roots:
                try:
                    search_root.resolve().relative_to(root)
                except ValueError:
                    continue
                resolved_candidate = search_root / path
                if resolved_candidate.exists():
                    candidate = resolved_candidate
                    break
    return require_workspace_path(candidate, purpose=purpose, must_exist=must_exist)


def require_write_path(path: Path, *, purpose: str = "output path") -> Path:
    resolved = require_workspace_path(path, purpose=purpose, must_exist=False)
    _reject_protected_write(resolved, purpose)
    return resolved


def require_relative_artifact_path(path: str, *, purpose: str = "artifact path") -> str:
    if "\\" in path:
        raise SpecError(f"{purpose} must use forward slashes: {path!r}")
    posix = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise SpecError(f"{purpose} must be relative: {path!r}")
    if any(part in ("", ".", "..") for part in posix.parts):
        raise SpecError(f"{purpose} contains an unsafe path segment: {path!r}")
    if posix.parts and posix.parts[0] in PROTECTED_WRITE_DIRS:
        raise SpecError(f"{purpose} must not target protected reference directories: {path!r}")
    return path


def write_text(path: Path, text: str) -> Path:
    output = require_write_path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return output


def write_json(path: Path, data: dict[str, Any]) -> Path:
    return write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def update_workflow_state(
    state_path: Path | None,
    event: str,
    payload: dict[str, Any],
    *,
    enabled: bool = True,
) -> None:
    if not enabled:
        return
    path = require_write_path(state_path or DEFAULT_STATE_PATH, purpose="workflow state path")
    state = _read_state(path)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **_sanitize(payload),
    }
    state.setdefault("events", []).append(record)
    _index_payload(state, event, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "evidence": [],
            "summaries": [],
            "plans": [],
            "artifact_manifests": [],
            "validation_reports": [],
            "traces": [],
            "prompt_memory": [],
            "human_interventions": [],
            "events": [],
        }
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"Invalid workflow state JSON in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SpecError(f"Workflow state must be a JSON object: {path}")
    loaded.setdefault("version", 1)
    for key in (
        "evidence",
        "summaries",
        "plans",
        "artifact_manifests",
        "validation_reports",
        "traces",
        "prompt_memory",
        "human_interventions",
        "events",
    ):
        loaded.setdefault(key, [])
    return loaded


def _index_payload(state: dict[str, Any], event: str, record: dict[str, Any]) -> None:
    mapping = {
        "ingest_spec": "evidence",
        "decompose": "plans",
        "prompt": "artifact_manifests",
        "model_generate": "artifact_manifests",
        "extract": "artifact_manifests",
        "validate": "validation_reports",
        "reflect": "plans",
        "optimize_prompt": "prompt_memory",
        "eval": "validation_reports",
        "eval_suite": "validation_reports",
        "human_intervention": "human_interventions",
        "resolve_intervention": "human_interventions",
        "audit_interface": "artifact_manifests",
        "audit_reference": "artifact_manifests",
        "verify_stage": "validation_reports",
        "run_workflow": "plans",
        "resume_workflow": "plans",
        "workflow_attempt": "validation_reports",
    }
    bucket = mapping.get(event)
    if bucket:
        state.setdefault(bucket, []).append(record)
    if record.get("trace"):
        state.setdefault("traces", []).append(record["trace"])


def _reject_protected_write(path: Path, purpose: str) -> None:
    root = workspace_root()
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    if parts and parts[0] in PROTECTED_WRITE_DIRS:
        raise SpecError(f"{purpose} must not write into protected reference directory {parts[0]!r}.")


def _sanitize(value: Any) -> Any:
    if isinstance(value, Path):
        return _safe_path(value)
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return value


def _safe_path(path: Path) -> str:
    root = workspace_root()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return f"<external>/{path.name}"

