"""JSONL trace logging for staged generation workflows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_trace_event(trace_path: Path | None, event: dict[str, Any], *, cwd: Path | None = None) -> None:
    if trace_path is None:
        return
    root = (cwd or Path.cwd()).resolve()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **_sanitize_value(event, root),
    }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_trace(trace_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_path.exists():
        return events
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def spec_summary(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": spec.get("name"),
        "target": spec.get("target"),
        "subfunctions": [item.get("name") for item in spec.get("subfunctions", []) if isinstance(item, dict)],
        "outputs": [item.get("path") for item in spec.get("outputs", []) if isinstance(item, dict)],
    }


def safe_path(path: Path | str, root: Path | None = None) -> str:
    base = (root or Path.cwd()).resolve()
    candidate = Path(path)
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate.absolute()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return f"<external>/{candidate.name}"


def _sanitize_value(value: Any, root: Path) -> Any:
    if isinstance(value, Path):
        return safe_path(value, root)
    if isinstance(value, dict):
        return {key: _sanitize_value(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, root) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item, root) for item in value]
    return value

