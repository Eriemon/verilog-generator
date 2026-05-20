"""Reflection prompt generation from validation reports."""

from __future__ import annotations

import json
from typing import Any

from .artifact_graph import suspect_artifacts_from_graph
from .planning import decompose_spec
from .use_case_templates import select_use_case_template, summarize_use_case_template

ERROR_SOURCES = (
    "spec_issue",
    "dependency_issue",
    "testbench_issue",
    "current_module_issue",
    "toolchain_issue",
    "insufficient_debug",
    "needs_human_intervention",
)
ACTION_BY_SOURCE = {
    "spec_issue": "revise_plan",
    "dependency_issue": "fix_dependency",
    "testbench_issue": "fix_testbench",
    "current_module_issue": "regenerate_current",
    "toolchain_issue": "fix_toolchain",
    "insufficient_debug": "augment_tests",
    "needs_human_intervention": "ask_human",
}


def classify_report(report_text: str, trace_events: list[dict[str, Any]] | None = None) -> list[str]:
    lowered = report_text.lower()
    sources: list[str] = []

    def add(source: str) -> None:
        if source not in sources:
            sources.append(source)

    for event in trace_events or []:
        for source in event.get("error_sources", []) or []:
            if source in ERROR_SOURCES:
                add(source)

    if "toolchain_issue" in lowered or "required tool" in lowered or "failed to start" in lowered or "timed out" in lowered:
        add("toolchain_issue")
    if "spec_issue" in lowered or "expected output file is missing" in lowered or "generated path does not exist" in lowered:
        add("spec_issue")
    if (
        "dependency_issue" in lowered
        or "undefined module" in lowered
        or "undefined reference" in lowered
        or "not declared" in lowered
        or "previous subfunction" in lowered
            or "dependency" in lowered
    ):
        add("dependency_issue")
    if (
        "testbench_issue" in lowered
        or "testbench" in lowered
        or "main() entry point" in lowered
        or "pass behavior" in lowered
        or "fail behavior" in lowered
        or "verification case" in lowered
    ):
        add("testbench_issue")
    if (
        "current_module_issue" in lowered
        or "placeholder" in lowered
        or "not synthesizable" in lowered
        or "top module" in lowered
        or "failed:" in lowered
        or "pragma" in lowered
        or "cfg" in lowered
        or "reviewability" in lowered
        or "comment" in lowered
        or "fsm" in lowered
    ):
        add("current_module_issue")
    if "insufficient_debug" in lowered or "cannot pinpoint" in lowered or "limited test cases" in lowered:
        add("insufficient_debug")

    if not sources:
        add("needs_human_intervention")
    return sources


def generate_repair_prompt(
    report_text: str,
    plan: dict[str, Any],
    trace_events: list[dict[str, Any]] | None = None,
    validation_json: dict[str, Any] | None = None,
    artifact_graph: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> str:
    normalized_plan = decompose_spec(plan)
    repair_plan = build_repair_plan(report_text, normalized_plan, trace_events or [], validation_json, artifact_graph, stage_verification)
    diagnosis = build_diagnosis(normalized_plan, trace_events or [], validation_json, stage_verification)
    plan_json = json.dumps(normalized_plan, indent=2, ensure_ascii=False)
    classification_json = json.dumps(repair_plan, indent=2, ensure_ascii=False)
    diagnosis_json = json.dumps(diagnosis, indent=2, ensure_ascii=False)
    trace_json = json.dumps(_trajectory_summary(trace_events or []), indent=2, ensure_ascii=False)
    validation_json_text = json.dumps(validation_json or {}, indent=2, ensure_ascii=False)
    graph_json_text = json.dumps(_graph_summary(artifact_graph), indent=2, ensure_ascii=False)
    gate_json_text = json.dumps(stage_verification or {}, indent=2, ensure_ascii=False)
    use_case_template_json = json.dumps(_use_case_template_context(normalized_plan), indent=2, ensure_ascii=False)
    return f"""# Repair prompt

You are repairing a staged Verilog generation result. Use the implementation plan, validation report, and error-source classification below.

## Error-source classification

```json
{classification_json}
```

## Differential diagnosis

```json
{diagnosis_json}
```

## Implementation plan

```json
{plan_json}
```

## Generation trajectory summary

```json
{trace_json}
```

## Structured validation report

```json
{validation_json_text}
```

## Artifact graph summary

```json
{graph_json_text}
```

## Verifier gate result

```json
{gate_json_text}
```

## Use-case template context

```json
{use_case_template_json}
```

## Validation report

```text
{report_text.rstrip()}
```

## Repair instructions

- If the source is `spec_issue`, revise the implementation plan or requested outputs before regenerating code.
- If the source is `dependency_issue`, inspect dependent subfunctions and interface compatibility before editing the current module.
- If the source is `testbench_issue`, repair the self-checking testbench and reference-vector comparison before changing design logic.
- If the source is `current_module_issue`, regenerate only the failing module or testbench when possible.
- If the source is `toolchain_issue`, fix tool availability/configuration first; do not rewrite code unless the tool output points to code errors.
- If a Verifier gate result is present, prioritize its `recommended_action`, interface-drift issues, vector-hash issues, and dependency mismatches over textual guesses.
- If a use-case template is selected, preserve its family-specific board-level guidance, parameterization points, and provenance unless the repair explicitly proves they caused the failure.
- If semantic drift is visible but localization is weak, strengthen cases or checkpoints before escalating to a human.
- If the source is `needs_human_intervention`, summarize the unresolved ambiguity and ask a precise hardware-design question.
- Preserve the original output contract: manifest JSON first, then exact `path=<relative/path>` code fences.
- Keep the repaired output verifiable, executable, and implementable.
"""


def _use_case_template_context(plan: dict[str, Any]) -> dict[str, Any]:
    return summarize_use_case_template(select_use_case_template(plan))


def resolution_action(sources: list[str]) -> str:
    if not sources:
        return "ask_human"
    return ACTION_BY_SOURCE.get(sources[0], "ask_human")


def build_diagnosis(
    plan: dict[str, Any],
    trace_events: list[dict[str, Any]] | None = None,
    validation_json: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    semantic = _semantic_summary(validation_json, stage_verification)
    suspect_subfunctions = _suspect_subfunctions(plan, stage_verification, semantic)
    prior_augments = any(
        event.get("event") == "reflect" and event.get("action") == "augment_tests"
        for event in trace_events or []
    )
    weak_test_oracle = bool(semantic.get("mismatched_cases")) and not semantic.get("checkpoint_drift")
    ambiguous_spec_rule = "spec_issue" in (_sources_from_stage_verification(stage_verification) or _sources_from_validation_json(validation_json))
    if ambiguous_spec_rule:
        recommended_next_action = "ask_human"
    elif weak_test_oracle and not prior_augments:
        recommended_next_action = "augment_tests"
    elif weak_test_oracle and prior_augments:
        recommended_next_action = "ask_human"
    elif "dependency_issue" in (_sources_from_stage_verification(stage_verification) or _sources_from_validation_json(validation_json)):
        recommended_next_action = "fix_dependency"
    elif semantic.get("mismatched_cases"):
        recommended_next_action = "regenerate_current"
    else:
        recommended_next_action = "regenerate_current"
    failing_cases = [str(item.get("case_id")) for item in semantic.get("mismatched_cases", []) if isinstance(item, dict)]
    for case_id in semantic.get("failed_cases", []) or []:
        value = str(case_id)
        if value not in failing_cases:
            failing_cases.append(value)
    drift_keys = sorted(
        {
            key
            for item in [*semantic.get("mismatched_cases", []), *semantic.get("checkpoint_drift", [])]
            if isinstance(item, dict)
            for key in item.get("drift_keys", []) or []
        }
    )
    dependencies = _dependency_names(plan)
    return {
        "version": 1,
        "semantic_ready": semantic.get("semantic_ready"),
        "failing_cases": failing_cases,
        "checkpoint_drift": semantic.get("checkpoint_drift", []),
        "drift_keys": drift_keys,
        "suspect_subfunctions": suspect_subfunctions or dependencies or [plan.get("name")],
        "localization_confidence": semantic.get("localization_confidence"),
        "weak_test_oracle": weak_test_oracle,
        "ambiguous_spec_rule": ambiguous_spec_rule,
        "recommended_next_action": recommended_next_action,
        "suggested_case_ids": failing_cases,
        "suggested_checkpoints": drift_keys or dependencies,
        "auto_debug_exhausted": weak_test_oracle and prior_augments,
        "auto_debug_before_human": weak_test_oracle and prior_augments,
        "localization_hit": bool(semantic.get("checkpoint_drift")) or (bool(suspect_subfunctions) and len(suspect_subfunctions) == 1),
    }


def _repair_action(
    sources: list[str],
    diagnosis: dict[str, Any],
    stage_verification: dict[str, Any] | None,
) -> str:
    if diagnosis.get("recommended_next_action") == "ask_human":
        return "ask_human"
    if diagnosis.get("recommended_next_action") == "augment_tests":
        return "augment_tests"
    if isinstance(stage_verification, dict) and stage_verification.get("recommended_action") and stage_verification.get("recommended_action") != "ask_human":
        return str(stage_verification["recommended_action"])
    return resolution_action(sources)


def build_repair_plan(
    report_text: str,
    plan: dict[str, Any],
    trace_events: list[dict[str, Any]] | None = None,
    validation_json: dict[str, Any] | None = None,
    artifact_graph: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diagnosis = build_diagnosis(plan, trace_events or [], validation_json, stage_verification)
    sources = _sources_from_stage_verification(stage_verification) or _sources_from_validation_json(validation_json) or classify_report(report_text, trace_events)
    primary = sources[0] if sources else "needs_human_intervention"
    action = _repair_action(sources, diagnosis, stage_verification)
    suspect_artifacts = _suspect_artifacts(validation_json, trace_events or [], artifact_graph, stage_verification)
    repair_plan = {
        "error_sources": sources,
        "primary_source": primary,
        "action": action,
        "suspect_artifacts": suspect_artifacts,
        "regeneration_scope": "tests_and_checkpoints" if action == "augment_tests" else _regeneration_scope(primary, suspect_artifacts),
        "required_context": _required_context(primary),
        "human_question": _human_question(primary, validation_json, report_text) if action == "ask_human" else None,
        "needs_human_intervention": action == "ask_human",
        "plan_name": plan.get("name"),
        "stage_ready": stage_verification.get("ready") if isinstance(stage_verification, dict) else None,
        "diagnosis": diagnosis,
    }
    return repair_plan


def build_intervention(repair_plan: dict[str, Any], report_text: str, validation_json: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "action": "ask_human",
        "primary_source": repair_plan.get("primary_source"),
        "question": repair_plan.get("human_question") or "Please clarify the unresolved hardware-design ambiguity.",
        "observations": _issue_messages(validation_json) or [line for line in report_text.splitlines() if line.strip()][:8],
        "attempted_actions": repair_plan.get("required_context", []),
        "expected_answer_format": {
            "decision": "one concise design decision or debugging direction",
            "evidence": "spec section, waveform observation, or tool report line when available",
            "constraints": "any new interface/timing/resource constraints",
        },
    }


def _trajectory_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for event in events[-12:]:
        summary.append(
            {
                "event": event.get("event"),
                "attempt_id": event.get("attempt_id"),
                "stage": event.get("stage"),
                "readiness": event.get("readiness"),
                "ok": event.get("ok"),
                "errors": event.get("errors"),
                "warnings": event.get("warnings"),
                "error_sources": event.get("error_sources", []),
                "action": event.get("action"),
            }
        )
    return summary


def _sources_from_validation_json(validation_json: dict[str, Any] | None) -> list[str]:
    if not validation_json:
        return []
    error_sources: list[str] = []
    other_sources: list[str] = []
    for issue in validation_json.get("issues", []) or []:
        if not isinstance(issue, dict):
            continue
        source = issue.get("source")
        if source not in ERROR_SOURCES:
            continue
        severity = str(issue.get("severity", "")).lower()
        bucket = error_sources if severity == "error" else other_sources
        if source not in bucket:
            bucket.append(source)
    return error_sources + [source for source in other_sources if source not in error_sources]


def _sources_from_stage_verification(stage_verification: dict[str, Any] | None) -> list[str]:
    if not stage_verification:
        return []
    sources: list[str] = []
    for source in stage_verification.get("error_sources", []) or []:
        if source in ERROR_SOURCES and source not in sources:
            sources.append(source)
    warning_sources: list[str] = []
    for issue in stage_verification.get("issues", []) or []:
        if not isinstance(issue, dict):
            continue
        source = issue.get("source")
        if source not in ERROR_SOURCES:
            continue
        severity = str(issue.get("severity", "")).lower()
        if severity == "error":
            if source not in sources:
                sources.append(source)
        elif source not in sources and source not in warning_sources:
            warning_sources.append(source)
    return sources + warning_sources


def _suspect_artifacts(
    validation_json: dict[str, Any] | None,
    trace_events: list[dict[str, Any]],
    artifact_graph: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> list[str]:
    artifacts: list[str] = []
    for artifact in suspect_artifacts_from_graph(artifact_graph):
        if artifact not in artifacts:
            artifacts.append(artifact)
    for issue in (validation_json or {}).get("issues", []) or []:
        if isinstance(issue, dict) and issue.get("path") and issue["path"] not in artifacts:
            artifacts.append(str(issue["path"]))
    for issue in (stage_verification or {}).get("issues", []) or []:
        if isinstance(issue, dict) and issue.get("path") and issue["path"] not in artifacts:
            artifacts.append(str(issue["path"]))
    for event in trace_events[-8:]:
        for key in ("output", "path", "report"):
            value = event.get(key)
            if value and str(value) not in artifacts:
                artifacts.append(str(value))
    return artifacts


def _graph_summary(artifact_graph: dict[str, Any] | None) -> dict[str, Any]:
    if not artifact_graph:
        return {}
    return {
        "name": artifact_graph.get("name"),
        "target": artifact_graph.get("target"),
        "node_count": len(artifact_graph.get("nodes", []) or []),
        "edge_count": len(artifact_graph.get("edges", []) or []),
        "suspect_artifacts": suspect_artifacts_from_graph(artifact_graph),
    }


def _regeneration_scope(primary_source: str, suspect_artifacts: list[str]) -> str:
    if primary_source == "spec_issue":
        return "plan_and_requested_outputs"
    if primary_source == "dependency_issue":
        return "dependency_subfunctions"
    if primary_source == "testbench_issue":
        return "testbench_and_reference_vectors"
    if primary_source == "toolchain_issue":
        return "toolchain_configuration"
    if primary_source in {"insufficient_debug", "needs_human_intervention"}:
        return "blocked_until_human_guidance"
    if suspect_artifacts:
        return "current_module_only"
    return "current_stage"


def _required_context(primary_source: str) -> list[str]:
    return {
        "spec_issue": ["evidence.json", "plan.json", "audit.md"],
        "dependency_issue": ["upstream manifests", "subfunction interfaces", "failing case ids"],
        "testbench_issue": ["reference vectors", "testbench file", "validation report"],
        "current_module_issue": ["current source file", "prior-stage artifact", "tool output"],
        "toolchain_issue": ["tool path", "cfg file", "environment setup"],
        "insufficient_debug": ["waveform/logs", "failing vectors", "suspect dependency list"],
        "needs_human_intervention": ["spec evidence", "attempt history", "precise open question"],
    }.get(primary_source, ["validation report", "trace"])


def _human_question(primary_source: str, validation_json: dict[str, Any] | None, report_text: str) -> str:
    if primary_source == "insufficient_debug":
        return "The current tests cannot pinpoint the failing subfunction. Which additional waveform, intermediate signal, or reference checkpoint should be used?"
    if primary_source == "spec_issue":
        return "Which source requirement or interface constraint should take precedence for the missing or conflicting spec item?"
    messages = _issue_messages(validation_json)
    if messages:
        return f"Please resolve this blocker: {messages[0]}"
    first_line = next((line.strip() for line in report_text.splitlines() if line.strip()), "")
    return first_line or "Please clarify the unresolved hardware-design ambiguity."


def _issue_messages(validation_json: dict[str, Any] | None) -> list[str]:
    messages: list[str] = []
    for issue in (validation_json or {}).get("issues", []) or []:
        if isinstance(issue, dict) and issue.get("message"):
            messages.append(str(issue["message"]))
    return messages


def _semantic_summary(validation_json: dict[str, Any] | None, stage_verification: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(stage_verification, dict):
        for key in ("semantic_ready", "mismatched_cases", "checkpoint_drift", "failed_cases", "localization_confidence"):
            if key in stage_verification:
                return {
                    "semantic_ready": stage_verification.get("semantic_ready"),
                    "mismatched_cases": stage_verification.get("mismatched_cases", []),
                    "checkpoint_drift": stage_verification.get("checkpoint_drift", []),
                    "failed_cases": stage_verification.get("failed_cases", []),
                    "localization_confidence": stage_verification.get("localization_confidence"),
                }
    metrics = validation_json.get("metrics", {}) if isinstance(validation_json, dict) else {}
    semantic = metrics.get("semantic_execution", {}) if isinstance(metrics, dict) else {}
    if not isinstance(semantic, dict):
        return {}
    return {
        "semantic_ready": semantic.get("semantic_ready"),
        "mismatched_cases": semantic.get("mismatched_cases", []),
        "checkpoint_drift": semantic.get("checkpoint_drift", []),
        "failed_cases": semantic.get("failed_cases", []),
        "localization_confidence": semantic.get("localization_confidence"),
    }


def _dependency_names(plan: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for subfunction in plan.get("subfunctions", []) or []:
        if not isinstance(subfunction, dict):
            continue
        for dependency in subfunction.get("dependencies", []) or []:
            value = str(dependency)
            if value not in names:
                names.append(value)
    return names


def _suspect_subfunctions(
    plan: dict[str, Any],
    stage_verification: dict[str, Any] | None,
    semantic: dict[str, Any],
) -> list[str]:
    suspects: list[str] = []
    issues = stage_verification.get("issues", []) if isinstance(stage_verification, dict) else []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        message = str(issue.get("message", ""))
        for subfunction in plan.get("subfunctions", []) or []:
            if not isinstance(subfunction, dict) or not subfunction.get("name"):
                continue
            name = str(subfunction["name"])
            if name in message and name not in suspects:
                suspects.append(name)
    if suspects:
        return suspects
    if semantic.get("checkpoint_drift"):
        return [str(plan.get("name"))]
    return []

