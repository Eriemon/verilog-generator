"""Deterministic local workflow evaluation fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .evaluation import evaluate_events
from .workspace import write_json


def run_eval_suite(out_path: Path) -> dict[str, Any]:
    scenarios = [
        _dependency_recovery(),
        _toolchain_blocker(),
        _interface_drift(),
        _human_decision_feedback(),
        _semantic_output_drift(),
    ]
    payload = {
        "version": 1,
        "description": "Verilog-only scenarios cover dependency recovery, interface drift, tool blockers, and human feedback.",
        "scenarios": scenarios,
    }
    payload["summary"] = {
        "scenario_count": len(scenarios),
        "passed": sum(1 for item in scenarios if item["metrics"].get("final_status") in {"passed", "blocked_toolchain", "blocked_human"}),
    }
    write_json(out_path, payload)
    return payload


def _scenario(name: str, description: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "description": description, "events": events, "metrics": evaluate_events(events)}


def _dependency_recovery() -> dict[str, Any]:
    return _scenario(
        "dependency_recovery",
        "A failed first Verilog attempt is repaired by carrying prompt memory into the next attempt.",
        [
            {"event": "prompt", "attempt_id": "dep-a1", "stage": "rtl", "subfunction": "add_round_key"},
            {"event": "validate", "attempt_id": "dep-a1", "readiness": "static", "ok": False, "error_sources": ["dependency_issue"]},
            {"event": "reflect", "attempt_id": "dep-a1", "error_sources": ["dependency_issue"], "action": "regenerate"},
            {"event": "prompt", "attempt_id": "dep-a2", "stage": "rtl", "subfunction": "add_round_key", "budget": "repair"},
            {"event": "validate", "attempt_id": "dep-a2", "readiness": "static", "ok": True, "error_sources": []},
            {"event": "workflow_attempt", "attempt_id": "dep-a2", "status": "passed"},
        ],
    )


def _toolchain_blocker() -> dict[str, Any]:
    return _scenario(
        "toolchain_blocker",
        "Implementation readiness stops when an external Verilog tool is unavailable.",
        [
            {"event": "prompt", "attempt_id": "tool-a1", "stage": "rtl", "subfunction": "top"},
            {"event": "validate", "attempt_id": "tool-a1", "readiness": "implement", "ok": False, "error_sources": ["toolchain_issue"]},
            {"event": "reflect", "attempt_id": "tool-a1", "error_sources": ["toolchain_issue"], "action": "ask_human"},
            {"event": "workflow_attempt", "attempt_id": "tool-a1", "status": "blocked_toolchain"},
        ],
    )


def _interface_drift() -> dict[str, Any]:
    return _scenario(
        "interface_drift",
        "A Python-to-Verilog interface drift is caught before final admission.",
        [
            {"event": "prompt", "attempt_id": "iface-a1", "stage": "rtl", "subfunction": "load", "budget": "compact"},
            {"event": "verify_stage", "attempt_id": "iface-a1", "ready": False, "issues": [{"source": "dependency_issue"}]},
            {"event": "reflect", "attempt_id": "iface-a1", "error_sources": ["dependency_issue"], "action": "regenerate"},
            {"event": "prompt", "attempt_id": "iface-a2", "stage": "rtl", "subfunction": "load", "budget": "repair"},
            {"event": "verify_stage", "attempt_id": "iface-a2", "ready": True, "issues": []},
            {"event": "workflow_attempt", "attempt_id": "iface-a2", "status": "passed"},
        ],
    )


def _human_decision_feedback() -> dict[str, Any]:
    return _scenario(
        "human_decision_feedback",
        "A missing interface decision blocks, then resume uses the user's decision.",
        [
            {"event": "validate", "attempt_id": "human-a1", "readiness": "static", "ok": False, "error_sources": ["needs_human_intervention"]},
            {"event": "human_intervention", "attempt_id": "human-a1", "primary_source": "needs_human_intervention"},
            {"event": "resume_workflow", "attempt_id": "human-a2", "decision": "decision.json"},
            {"event": "validate", "attempt_id": "human-a2", "readiness": "static", "ok": True, "error_sources": []},
            {"event": "workflow_attempt", "attempt_id": "human-a2", "status": "passed"},
        ],
    )


def _semantic_output_drift() -> dict[str, Any]:
    return _scenario(
        "semantic_output_drift",
        "Reference-vector mismatch is classified as a testbench/current module issue.",
        [
            {"event": "validate", "attempt_id": "sem-a1", "readiness": "execute", "ok": False, "error_sources": ["current_module_issue", "testbench_issue"]},
            {"event": "reflect", "attempt_id": "sem-a1", "error_sources": ["current_module_issue", "testbench_issue"], "action": "regenerate"},
            {"event": "validate", "attempt_id": "sem-a2", "readiness": "execute", "ok": True, "error_sources": []},
            {"event": "workflow_attempt", "attempt_id": "sem-a2", "status": "passed"},
        ],
    )
