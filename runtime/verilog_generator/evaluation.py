"""Trace-based workflow evaluation metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .trace import read_trace


def evaluate_trace(trace_path: Path) -> dict[str, Any]:
    return evaluate_events(read_trace(trace_path))


def evaluate_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    attempt_ids = {event.get("attempt_id") for event in events if event.get("attempt_id")}
    validate_events = [event for event in events if event.get("event") == "validate"]
    readiness_counts: dict[str, dict[str, int]] = {}
    error_sources: dict[str, int] = {}
    subfunction_failures: dict[str, int] = {}
    human_interventions = 0
    qor_violation_count = 0
    performance_events = 0
    performance_passes = 0
    resolved_interventions = 0
    unresolved_interventions = 0
    prompt_tokens_by_budget: dict[str, list[int]] = {}
    verify_stage_events = 0
    semantic_events = 0
    semantic_passes = 0
    failed_case_counts: list[int] = []
    localization_hits = 0
    localization_attempts = 0
    auto_debug_before_human = 0
    human_escalations = 0

    for event in events:
        for source in event.get("error_sources", []) or []:
            error_sources[source] = error_sources.get(source, 0) + 1
            if source == "needs_human_intervention":
                human_interventions += 1
        if event.get("action") == "ask_human":
            human_interventions += 1
            unresolved_interventions += 1
            human_escalations += 1
            if event.get("auto_debug_before_human"):
                auto_debug_before_human += 1
        if event.get("event") == "resolve_intervention" or event.get("status") == "resolved":
            resolved_interventions += 1
        if event.get("event") == "verify_stage":
            verify_stage_events += 1
            if event.get("ready") is True:
                semantic_ready = event.get("semantic_ready")
                if semantic_ready is False:
                    localization_attempts += 1
                for issue in event.get("issues", []) or []:
                    if isinstance(issue, dict) and issue.get("source") == "insufficient_debug":
                        localization_attempts += 1
        if event.get("ok") is False or event.get("error_sources"):
            subfunction = str(event.get("subfunction") or event.get("stage") or event.get("event") or "unknown")
            subfunction_failures[subfunction] = subfunction_failures.get(subfunction, 0) + 1
        issues = event.get("issues", []) or []
        event_qor_violations = [
            issue
            for issue in issues
            if isinstance(issue, dict) and "qor violation" in str(issue.get("message", "")).lower()
        ]
        qor_violation_count += len(event_qor_violations)
        metrics = event.get("metrics") or {}
        if metrics or any("performance" in str(issue).lower() or "qor" in str(issue).lower() for issue in issues):
            performance_events += 1
            if not event_qor_violations and event.get("ok") is not False:
                performance_passes += 1
        prompt_stats = event.get("prompt_stats") if isinstance(event.get("prompt_stats"), dict) else {}
        if prompt_stats:
            budget = str(prompt_stats.get("budget") or event.get("budget") or "unknown")
            tokens = prompt_stats.get("approx_tokens")
            if isinstance(tokens, (int, float)):
                prompt_tokens_by_budget.setdefault(budget, []).append(int(tokens))
        diagnosis = event.get("diagnosis") if isinstance(event.get("diagnosis"), dict) else {}
        if diagnosis:
            localization_attempts += 1
            if diagnosis.get("localization_hit"):
                localization_hits += 1
        semantic_ready = event.get("semantic_ready")
        metrics = event.get("metrics") or {}
        semantic_metrics = metrics.get("semantic_execution") if isinstance(metrics, dict) and isinstance(metrics.get("semantic_execution"), dict) else {}
        if event.get("event") == "validate" and semantic_ready is not None:
            semantic_events += 1
            if semantic_ready:
                semantic_passes += 1
        elif event.get("event") == "validate" and semantic_metrics:
            semantic_events += 1
            if semantic_metrics.get("semantic_ready"):
                semantic_passes += 1
        if event.get("event") == "validate" and semantic_metrics:
            failed_cases = semantic_metrics.get("failed_cases", []) or []
            mismatched_cases = semantic_metrics.get("mismatched_cases", []) or []
            failed_case_counts.append(len(failed_cases) + len(mismatched_cases))

    for event in validate_events:
        readiness = str(event.get("readiness", "static"))
        bucket = readiness_counts.setdefault(readiness, {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        if event.get("ok"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1

    total_validations = len(validate_events)
    passed_validations = sum(1 for event in validate_events if event.get("ok"))
    prompt_events = [event for event in events if event.get("event") == "prompt"]
    attempts = len(attempt_ids) if attempt_ids else len(events)
    all_prompt_tokens = [token for tokens in prompt_tokens_by_budget.values() for token in tokens]
    readiness_pass_rates = {
        readiness: (bucket["passed"] / bucket["total"] if bucket["total"] else None)
        for readiness, bucket in readiness_counts.items()
    }
    noise_events = [
        event
        for event in events
        if "noise" in str(event).lower() or "insufficient_debug" in (event.get("error_sources", []) or [])
    ]
    noise_passes = [
        event
        for event in noise_events
        if event.get("event") == "validate" and event.get("ok") is True
    ]
    return {
        "events": len(events),
        "attempts": attempts,
        "coding_attempts": len(prompt_events),
        "event_counts": _event_counts(events),
        "readiness": readiness_counts,
        "readiness_pass_rates": readiness_pass_rates,
        "readiness_pass_rate": (passed_validations / total_validations) if total_validations else None,
        "correctness": any(event.get("ok") and event.get("readiness") in {"execute", "implement"} for event in validate_events),
        "correct": any(event.get("ok") and event.get("readiness") in {"execute", "implement"} for event in validate_events),
        "error_source_distribution": error_sources,
        "human_intervention_count": human_interventions,
        "interventions": human_interventions,
        "intervention_resolved_count": resolved_interventions,
        "intervention_unresolved_count": max(0, unresolved_interventions - resolved_interventions),
        "average_prompt_tokens": (sum(all_prompt_tokens) / len(all_prompt_tokens)) if all_prompt_tokens else None,
        "prompt_tokens_by_budget": {
            budget: {
                "count": len(tokens),
                "average": sum(tokens) / len(tokens),
            }
            for budget, tokens in sorted(prompt_tokens_by_budget.items())
        },
        "repair_budget_savings": _repair_budget_savings(prompt_tokens_by_budget),
        "attempts_per_verified_stage": attempts / max(1, verify_stage_events),
        "semantic_pass_rate": (semantic_passes / semantic_events) if semantic_events else None,
        "gate_false_negative_markers": _gate_false_negative_markers(events),
        "localization_hit_rate": (localization_hits / localization_attempts) if localization_attempts else None,
        "average_failed_cases_per_attempt": (sum(failed_case_counts) / len(failed_case_counts)) if failed_case_counts else None,
        "auto_debug_before_human_rate": (auto_debug_before_human / human_escalations) if human_escalations else None,
        "performance_pass_rate": (performance_passes / performance_events) if performance_events else None,
        "qor_violation_count": qor_violation_count,
        "subfunction_failure_hotspots": subfunction_failures,
        "average_attempts_per_subfunction": attempts / max(1, len({event.get("subfunction") for event in events if event.get("subfunction")})),
        "noise_recovery": {
            "total_noise_markers": len(noise_events),
            "recovered": bool(noise_passes),
        },
    }


def write_eval_metrics(trace_path: Path, out_path: Path) -> dict[str, Any]:
    metrics = evaluate_trace(trace_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return metrics


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_name = str(event.get("event", "unknown"))
        counts[event_name] = counts.get(event_name, 0) + 1
    return counts


def _repair_budget_savings(tokens_by_budget: dict[str, list[int]]) -> float | None:
    normal = tokens_by_budget.get("normal") or []
    repair = tokens_by_budget.get("repair") or []
    if not normal or not repair:
        return None
    return (sum(normal) / len(normal)) - (sum(repair) / len(repair))


def _gate_false_negative_markers(events: list[dict[str, Any]]) -> int:
    verified_ready: set[str] = set()
    markers = 0
    for event in events:
        attempt_id = str(event.get("attempt_id") or "")
        if event.get("event") == "verify_stage" and event.get("ready") is True and attempt_id:
            verified_ready.add(attempt_id)
            continue
        if event.get("event") != "validate" or not attempt_id or attempt_id not in verified_ready:
            continue
        semantic_ready = event.get("semantic_ready")
        semantic_metrics = (event.get("metrics") or {}).get("semantic_execution", {})
        failed_semantically = semantic_ready is False or (isinstance(semantic_metrics, dict) and semantic_metrics.get("semantic_ready") is False)
        if failed_semantically:
            markers += 1
    return markers

