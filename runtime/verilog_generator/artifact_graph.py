"""Artifact graph construction from workflow trace events."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .trace import read_trace


def build_artifact_graph(trace_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    events = read_trace(trace_path)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    previous_by_attempt: dict[str, str] = {}
    previous_by_subfunction: dict[str, str] = {}

    for index, event in enumerate(events):
        event_id = f"event:{index}:{event.get('event', 'unknown')}"
        subfunction = str(event.get("subfunction") or event.get("stage") or "global")
        attempt = str(event.get("attempt_id") or "attempt-unknown")
        nodes[event_id] = {
            "id": event_id,
            "type": "event",
            "event": event.get("event"),
            "attempt_id": event.get("attempt_id"),
            "stage": event.get("stage") or event.get("readiness"),
            "subfunction": subfunction,
            "ok": event.get("ok"),
            "error_sources": event.get("error_sources", []),
        }
        if attempt in previous_by_attempt:
            edges.append({"from": previous_by_attempt[attempt], "to": event_id, "kind": "attempt_sequence"})
        previous_by_attempt[attempt] = event_id
        if subfunction in previous_by_subfunction:
            edges.append({"from": previous_by_subfunction[subfunction], "to": event_id, "kind": "subfunction_sequence"})
        previous_by_subfunction[subfunction] = event_id

        for key in ("output", "path", "report", "report_json", "repair_plan", "context_manifest", "context_dir", "memory"):
            value = event.get(key)
            if value:
                artifact_id = _artifact_id(value)
                nodes.setdefault(artifact_id, {"id": artifact_id, "type": "artifact", "path": str(value)})
                edges.append({"from": event_id, "to": artifact_id, "kind": key})
        for value in event.get("written_files", []) or []:
            artifact_id = _artifact_id(value)
            nodes.setdefault(artifact_id, {"id": artifact_id, "type": "artifact", "path": str(value)})
            edges.append({"from": event_id, "to": artifact_id, "kind": "written_file"})
        for source in event.get("error_sources", []) or []:
            source_id = f"error_source:{source}"
            nodes.setdefault(source_id, {"id": source_id, "type": "error_source", "name": source})
            edges.append({"from": event_id, "to": source_id, "kind": "has_error_source"})
        semantic = (event.get("metrics") or {}).get("semantic_execution", {}) if isinstance(event.get("metrics"), dict) else {}
        for item in semantic.get("checkpoint_drift", []) or []:
            if not isinstance(item, dict):
                continue
            for drift_key in item.get("drift_keys", []) or []:
                checkpoint_id = f"checkpoint:{drift_key}"
                nodes.setdefault(checkpoint_id, {"id": checkpoint_id, "type": "checkpoint", "name": str(drift_key)})
                edges.append({"from": event_id, "to": checkpoint_id, "kind": "checkpoint_drift"})

    for subfunction in plan.get("subfunctions", []) or []:
        if not isinstance(subfunction, dict):
            continue
        node_id = f"subfunction:{subfunction.get('name')}"
        nodes.setdefault(node_id, {"id": node_id, "type": "subfunction", "name": subfunction.get("name")})
        for dependency in subfunction.get("dependencies", []) or []:
            dependency_id = f"subfunction:{dependency}"
            nodes.setdefault(dependency_id, {"id": dependency_id, "type": "subfunction", "name": dependency})
            edges.append({"from": dependency_id, "to": node_id, "kind": "dependency"})

    return {
        "version": 1,
        "name": plan.get("name"),
        "target": plan.get("target"),
        "nodes": list(nodes.values()),
        "edges": edges,
        "suspect_artifacts": suspect_artifacts_from_graph({"nodes": list(nodes.values()), "edges": edges}),
    }


def suspect_artifacts_from_graph(graph: dict[str, Any] | None) -> list[str]:
    if not graph:
        return []
    event_nodes = {
        node.get("id")
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and node.get("type") == "event" and node.get("error_sources")
    }
    artifacts: list[str] = []
    for edge in graph.get("edges", []) or []:
        if not isinstance(edge, dict) or edge.get("from") not in event_nodes:
            continue
        target = str(edge.get("to", ""))
        if not target.startswith("artifact:"):
            continue
        path = target.removeprefix("artifact:")
        if path and path not in artifacts:
            artifacts.append(path)
    return artifacts


def _artifact_id(value: Any) -> str:
    text = str(value).replace("\\", "/")
    return f"artifact:{text}"

