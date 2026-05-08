"""Source document ingestion and evidence matching."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .spec import SpecError, sanitize_name

SUPPORTED_SOURCE_SUFFIXES = (".md", ".txt", ".tex")


def ingest_sources(
    sources: list[Path],
    *,
    root: Path | None = None,
    sidecars: list[Path] | None = None,
) -> dict[str, Any]:
    """Convert local text-like specification sources into evidence blocks."""
    base = (root or Path.cwd()).resolve()
    expanded = _expand_sources(sources, base)
    if not expanded:
        raise SpecError("No source files matched.")

    documents: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for source in expanded:
        resolved = _require_inside_root(source, base)
        suffix = resolved.suffix.lower()
        if suffix not in SUPPORTED_SOURCE_SUFFIXES:
            raise SpecError(
                f"Unsupported source type {suffix!r}; expected one of {', '.join(SUPPORTED_SOURCE_SUFFIXES)}."
            )
        document_id = _unique_document_id(resolved, used_ids)
        rel_path = resolved.relative_to(base).as_posix()
        documents.append({"source_id": document_id, "path": rel_path, "kind": suffix.lstrip(".")})
        for index, block in enumerate(_paragraph_blocks(resolved), start=1):
            items.append(
                {
                    "source_id": f"{document_id}:p{index:03d}",
                    "document_id": document_id,
                    "location": f"{rel_path}:{block['start_line']}-{block['end_line']}",
                    "text": block["text"],
                }
            )
    sidecar_items = _load_sidecar_items(sidecars or [], base)
    items.extend(sidecar_items)
    return {"version": 2, "sources": documents, "items": items}


def evidence_refs_for_text(evidence: dict[str, Any] | None, text: str, *, limit: int = 2) -> list[dict[str, str]]:
    """Return stable evidence references whose text overlaps with the requirement."""
    if not evidence:
        return []
    query = _tokens(text)
    if not query:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in evidence.get("items", []):
        if not isinstance(item, dict):
            continue
        overlap = len(query.intersection(_tokens(str(item.get("text", "")))))
        if overlap:
            scored.append((overlap, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("source_id", ""))))
    refs: list[dict[str, str]] = []
    for _, item in scored[:limit]:
        refs.append(
            {
                "source_id": str(item.get("source_id", "")),
                "location": str(item.get("location", "")),
                "kind": str(item.get("kind", "text")),
            }
        )
    return refs


def _expand_sources(sources: list[Path], root: Path) -> list[Path]:
    expanded: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        raw = str(source)
        if any(char in raw for char in "*?[]"):
            matches = sorted(root.glob(raw))
        else:
            matches = [source if source.is_absolute() else root / source]
        for match in matches:
            resolved = match.resolve()
            if resolved not in seen:
                seen.add(resolved)
                expanded.append(resolved)
    return expanded


def _require_inside_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SpecError(f"Source path is outside the current workspace: {path}") from exc
    if not resolved.is_file():
        raise SpecError(f"Source path is not a file: {path}")
    return resolved


def _unique_document_id(path: Path, used_ids: set[str]) -> str:
    base = sanitize_name(path.stem).lower()
    candidate = base
    index = 2
    while candidate in used_ids:
        candidate = f"{base}_{index}"
        index += 1
    used_ids.add(candidate)
    return candidate


def _paragraph_blocks(path: Path) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: list[str] = []
    start_line = 1
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        cleaned = _clean_line(line, path.suffix.lower())
        if not cleaned:
            if current:
                blocks.append(_make_block(current, start_line, line_no - 1))
                current = []
            continue
        if not current:
            start_line = line_no
        current.append(cleaned)
    if current:
        blocks.append(_make_block(current, start_line, start_line + len(current) - 1))
    return blocks


def _make_block(lines: list[str], start_line: int, end_line: int) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return {"start_line": start_line, "end_line": end_line, "text": text}


def _clean_line(line: str, suffix: str) -> str:
    text = line.strip()
    if suffix == ".tex":
        text = re.sub(r"(?<!\\)%.*", "", text).strip()
        text = re.sub(r"\\(?:sub)*section\*?\{([^{}]+)\}", r"section: \1", text)
        for _ in range(3):
            text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", text)
        text = re.sub(r"\\[A-Za-z]+\*?", "", text)
        text = text.replace("{", "").replace("}", "")
    elif suffix == ".md":
        text = re.sub(r"^#+\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[A-Za-z0-9_]+", text.lower()) if len(token) > 2}


def _load_sidecar_items(sidecars: list[Path], root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sidecar in sidecars:
        resolved = _require_inside_root(sidecar if sidecar.is_absolute() else root / sidecar, root)
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SpecError(f"Invalid evidence sidecar JSON in {sidecar}: {exc}") from exc
        raw_items = payload.get("items", payload if isinstance(payload, list) else [])
        if not isinstance(raw_items, list):
            raise SpecError("Evidence sidecar must be a list or an object with an items list.")
        for index, raw in enumerate(raw_items, start=1):
            if not isinstance(raw, dict):
                raise SpecError("Every evidence sidecar item must be an object.")
            source_id = str(raw.get("source_id") or f"{resolved.stem}:sidecar{index:03d}")
            if source_id in seen:
                raise SpecError(f"Duplicate evidence sidecar source_id {source_id!r}.")
            seen.add(source_id)
            item = {
                "source_id": source_id,
                "document_id": str(raw.get("document_id") or resolved.stem),
                "location": str(raw.get("location") or f"{resolved.name}:item{index}"),
                "kind": str(raw.get("kind") or "text"),
                "text": str(raw.get("text") or ""),
            }
            ref_path = raw.get("ref_path")
            if ref_path:
                item["ref_path"] = _safe_ref_path(str(ref_path), root)
            if not item["text"]:
                raise SpecError(f"Evidence sidecar item {source_id!r} must include text.")
            items.append(item)
    return items


def _safe_ref_path(ref_path: str, root: Path) -> str:
    candidate = Path(ref_path)
    resolved = candidate if candidate.is_absolute() else root / candidate
    try:
        safe = resolved.resolve().relative_to(root)
    except ValueError as exc:
        raise SpecError(f"Evidence ref_path must stay inside the current workspace: {ref_path}") from exc
    return safe.as_posix()

