"""Extract generated files from model responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


class ExtractionError(ValueError):
    """Raised when response extraction fails."""


@dataclass(frozen=True)
class FencedBlock:
    info: str
    content: str

    @property
    def path(self) -> str | None:
        return path_from_info(self.info)


FENCE_RE = re.compile(
    r"^```(?P<info>[^\n`]*)\n(?P<content>.*?)(?:\n)?^```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)


def parse_fenced_blocks(text: str) -> list[FencedBlock]:
    return [
        FencedBlock(match.group("info").strip(), match.group("content"))
        for match in FENCE_RE.finditer(text)
    ]


def parse_manifest(text: str) -> dict[str, Any]:
    for block in parse_fenced_blocks(text):
        language = block.info.split(maxsplit=1)[0].lower() if block.info else ""
        if language != "json":
            continue
        if block.path is not None or patch_marker_from_info(block.info) is not None:
            continue
        try:
            candidate = json.loads(block.content)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("files"), list):
            return candidate
    raise ExtractionError("Response does not contain a JSON manifest with a files list.")


def extract_response(text: str, out_dir: Path) -> list[Path]:
    _reject_text_outside_fences(text)
    manifest = parse_manifest(text)
    blocks = parse_fenced_blocks(text)
    manifest_paths = _manifest_paths(manifest)
    patch_entries = _manifest_patches(manifest)
    blocks_by_path, patch_blocks = _classify_file_blocks(blocks, manifest_paths, patch_entries)

    written: list[Path] = []
    for rel_path in manifest_paths:
        block = blocks_by_path.get(rel_path)
        if block is None:
            raise ExtractionError(f"Missing fenced code block for manifest path {rel_path!r}.")
        output_path = safe_output_path(out_dir, rel_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(block.content.rstrip() + "\n", encoding="utf-8")
        written.append(output_path)

    for patch in patch_entries:
        key = (patch["path"], patch["marker"])
        block = patch_blocks.get(key)
        if block is None:
            raise ExtractionError(
                f"Missing fenced patch block for manifest patch path {patch['path']!r} marker {patch['marker']!r}."
            )
        output_path = safe_output_path(out_dir, patch["path"])
        _apply_patch_block(output_path, patch["marker"], block.content)
        if output_path not in written:
            written.append(output_path)

    return written


def _manifest_paths(manifest: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for file_entry in manifest["files"]:
        if not isinstance(file_entry, dict) or not file_entry.get("path"):
            raise ExtractionError("Every manifest file entry must contain a path.")
        rel_path = normalize_manifest_path(str(file_entry["path"]))
        if rel_path in seen:
            raise ExtractionError(f"Duplicate manifest path {rel_path!r}.")
        seen.add(rel_path)
        paths.append(rel_path)
    return paths


def _manifest_patches(manifest: dict[str, Any]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    patches: list[dict[str, str]] = []
    raw_patches = manifest.get("patches", [])
    if raw_patches is None:
        return patches
    if not isinstance(raw_patches, list):
        raise ExtractionError("Manifest patches must be a list when present.")
    for patch in raw_patches:
        if not isinstance(patch, dict) or not patch.get("path") or not patch.get("marker"):
            raise ExtractionError("Every manifest patch entry must contain path and marker.")
        rel_path = normalize_manifest_path(str(patch["path"]))
        marker = normalize_patch_marker(str(patch["marker"]))
        key = (rel_path, marker)
        if key in seen:
            raise ExtractionError(f"Duplicate manifest patch path {rel_path!r} marker {marker!r}.")
        seen.add(key)
        patches.append({"path": rel_path, "marker": marker})
    return patches


def _classify_file_blocks(
    blocks: list[FencedBlock],
    manifest_paths: list[str],
    patch_entries: list[dict[str, str]],
) -> tuple[dict[str, FencedBlock], dict[tuple[str, str], FencedBlock]]:
    manifest_set = set(manifest_paths)
    patch_set = {(patch["path"], patch["marker"]) for patch in patch_entries}
    by_path: dict[str, FencedBlock] = {}
    patches_by_key: dict[tuple[str, str], FencedBlock] = {}
    for block in blocks:
        language = block.info.split(maxsplit=1)[0].lower() if block.info else ""
        if language == "json" and not block.path and not patch_marker_from_info(block.info):
            continue
        block_path = block.path
        if not block_path:
            raise ExtractionError(
                f"File code block is missing a path=<relative/path> fence info: {block.info!r}."
            )
        rel_path = normalize_manifest_path(block_path)
        patch_marker = patch_marker_from_info(block.info)
        if patch_marker:
            key = (rel_path, normalize_patch_marker(patch_marker))
            if key in patches_by_key:
                raise ExtractionError(f"Duplicate code fence patch path {rel_path!r} marker {key[1]!r}.")
            if key not in patch_set:
                raise ExtractionError(f"Code fence patch path {rel_path!r} marker {key[1]!r} is not declared in manifest.")
            patches_by_key[key] = block
            continue
        if rel_path in by_path:
            raise ExtractionError(f"Duplicate code fence path {rel_path!r}.")
        if rel_path not in manifest_set:
            raise ExtractionError(f"Code fence path {rel_path!r} is not declared in manifest.")
        by_path[rel_path] = block
    return by_path, patches_by_key


def _reject_text_outside_fences(text: str) -> None:
    cursor = 0
    outside_parts: list[str] = []
    for match in FENCE_RE.finditer(text):
        outside_parts.append(text[cursor : match.start()])
        cursor = match.end()
    outside_parts.append(text[cursor:])

    outside = "".join(outside_parts).strip()
    if outside:
        first_line = outside.splitlines()[0].strip()
        raise ExtractionError(f"Response contains prose outside fenced code blocks: {first_line!r}.")


def path_from_info(info: str) -> str | None:
    return _value_from_info(info, "path")


def patch_marker_from_info(info: str) -> str | None:
    return _value_from_info(info, "patch")


def _value_from_info(info: str, key: str) -> str | None:
    if not info:
        return None
    for token in info.split():
        if token.startswith(f"{key}="):
            return token.split("=", 1)[1].strip("\"'")
    return None


def normalize_manifest_path(path: str | None) -> str:
    if not path:
        raise ExtractionError("Path is required.")
    if "\\" in path:
        raise ExtractionError(f"Path must use forward slashes, got {path!r}.")
    raw = path.strip()
    if not raw:
        raise ExtractionError("Path must not be empty.")
    return raw


def normalize_patch_marker(marker: str | None) -> str:
    if not marker:
        raise ExtractionError("Patch marker is required.")
    cleaned = marker.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", cleaned):
        raise ExtractionError(f"Patch marker contains unsupported characters: {marker!r}.")
    return cleaned


def safe_output_path(out_dir: Path, relative_path: str) -> Path:
    normalized = normalize_manifest_path(relative_path)
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ExtractionError(f"Refusing absolute output path {relative_path!r}.")
    if any(part in ("", ".", "..") for part in posix.parts):
        raise ExtractionError(f"Refusing unsafe output path {relative_path!r}.")

    root = out_dir.resolve()
    candidate = (root / Path(*posix.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ExtractionError(f"Refusing path outside output directory: {relative_path!r}.") from exc
    return candidate


def _apply_patch_block(path: Path, marker: str, content: str) -> None:
    if not path.exists():
        raise ExtractionError(f"Patch target file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    begin_token = f"VERILOG-GEN-PATCH-BEGIN {marker}"
    end_token = f"VERILOG-GEN-PATCH-END {marker}"
    begin_indices = [index for index, line in enumerate(lines) if begin_token in line]
    end_indices = [index for index, line in enumerate(lines) if end_token in line]
    if len(begin_indices) != 1 or len(end_indices) != 1:
        raise ExtractionError(f"Patch marker {marker!r} must appear exactly once as begin and end markers in {path.name}.")
    begin = begin_indices[0]
    end = end_indices[0]
    if begin >= end:
        raise ExtractionError(f"Patch marker {marker!r} has an invalid begin/end order in {path.name}.")
    replacement = content.rstrip().splitlines()
    updated = [*lines[: begin + 1], *replacement, *lines[end:]]
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")

