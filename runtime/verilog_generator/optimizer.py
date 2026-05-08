"""Prompt patch generation from trace history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .planning import decompose_spec
from .trace import read_trace


def optimize_prompt_from_trace(trace_path: Path, plan: dict[str, Any]) -> str:
    events = read_trace(trace_path)
    normalized_plan = decompose_spec(plan)
    constraints = _derive_constraints(events)
    lines = [
        f"# Prompt patch: {normalized_plan['name']}",
        "",
        "Apply these incremental constraints to the next staged generation prompt.",
        "",
        "## Targeted Constraints",
        "",
    ]
    if constraints:
        lines.extend(f"- {constraint}" for constraint in constraints)
    else:
        lines.append("- No concrete failure pattern was found; keep the existing staged prompt contract unchanged.")
    lines.extend(
        [
            "",
            "## Context",
            "",
            f"- Trace events analyzed: {len(events)}",
            f"- Subfunctions: {', '.join(item['name'] for item in normalized_plan.get('subfunctions', []))}",
            "- Preserve exact manifest/code-fence output contract.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_prompt_memory(trace_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    events = read_trace(trace_path)
    normalized_plan = decompose_spec(plan)
    return {
        "version": 1,
        "name": normalized_plan["name"],
        "target": normalized_plan["target"],
        "entries": _memory_entries(events, normalized_plan),
    }


def _derive_constraints(events: list[dict[str, Any]]) -> list[str]:
    constraints: list[str] = []
    joined = "\n".join(str(event).lower() for event in events)

    def add(text: str) -> None:
        if text not in constraints:
            constraints.append(text)

    if any(source in joined for source in ("dependency_issue", "undefined module", "undefined reference", "not declared")):
        add("Re-check subfunction interface compatibility, dimensions, and dependency outputs before regenerating the failing module.")
    if "testbench" in joined or "main() entry point" in joined:
        add("Generate or repair a self-checking testbench with explicit PASS/FAIL behavior and coverage for every behavior item.")
    if "toolchain_issue" in joined or "required tool" in joined or "failed to start" in joined or "timed out" in joined:
        add("Separate toolchain availability/configuration failures from code edits; only rewrite code when the tool output identifies code errors.")
    if "placeholder" in joined or "todo" in joined or "fixme" in joined or "ellipsis" in joined:
        add("Remove all placeholders and produce complete executable code for every manifest file.")
    if "spec_issue" in joined or "expected output file is missing" in joined:
        add("Audit the plan outputs and evidence before code regeneration; missing requested files indicate a plan or manifest coverage issue.")
    if "reviewability" in joined or "comment" in joined:
        add("Preserve the requested comment language and add adjacent explanatory comments for ports, signals, always blocks, assigns, and testbench checks.")
    if "fsm" in joined or "state register" in joined or "next-state" in joined:
        add("For RTL regeneration, use a three-block FSM with explicit state register, next-state logic, and output logic sections.")
    if "reference model" in joined or "run_tests" in joined:
        add("Preserve the Python reference model API and mirror its deterministic verification vectors in the downstream testbench.")
    if "semantic output drift" in joined or "wrong_final_output" in joined:
        add("When a final output mismatches the Python oracle, restate the exact output contract and regenerate only the logic responsible for those drift keys.")
    if "checkpoint drift" in joined or "checkpoint_divergence" in joined:
        add("Preserve and compare intermediate checkpoints so the next attempt can localize the mismatch before the final output stage.")
    if "case order drift" in joined or "case_order_drift" in joined:
        add("Keep case ids and transcript order stable across Python oracle, vectors, and Verilog testbench output.")
    if "weak_test_oracle" in joined or "augment_tests" in joined:
        add("Strengthen boundary cases, negative cases, and checkpoint coverage before escalating to human debugging.")
    if "ambiguous_spec_rule" in joined:
        add("Call out the conflicting spec rule explicitly and preserve the ambiguity for structured human resolution instead of guessing.")
    if "needs_human_intervention" in joined or "ask_human" in joined:
        add("Summarize the unresolved ambiguity as a precise hardware-design question before another generation attempt.")

    return constraints


def _memory_entries(events: list[dict[str, Any]], plan: dict[str, Any]) -> list[dict[str, Any]]:
    subfunctions = [item.get("name") for item in plan.get("subfunctions", []) if isinstance(item, dict)] or ["*"]
    entries: list[dict[str, Any]] = []
    for event in events:
        joined = str(event).lower()
        signatures = _event_signatures(event, joined)
        for signature, constraint in signatures:
            entries.append(
                {
                    "subfunction": event.get("subfunction") or subfunctions[0],
                    "stage": event.get("stage") or event.get("readiness") or event.get("event") or "unknown",
                    "attempt_id": event.get("attempt_id"),
                    "error_signature": signature,
                    "constraint": constraint,
                }
            )
    return _dedupe_entries(entries)


def _event_signatures(event: dict[str, Any], joined: str) -> list[tuple[str, str]]:
    signatures: list[tuple[str, str]] = []
    sources = set(event.get("error_sources", []) or [])
    if "dependency_issue" in sources or "undefined module" in joined or "interface" in joined:
        signatures.append(
            (
                "interface_or_dependency_mismatch",
                "Reconfirm upstream/downstream port names, dimensions, and subfunction dependency outputs before code generation.",
            )
        )
    if "testbench_issue" in sources or "testbench" in joined or "pass behavior" in joined or "fail behavior" in joined:
        signatures.append(
            (
                "testbench_or_reference_vector_gap",
                "Generate self-checking PASS/FAIL tests that mirror the reference model vectors and mention required verification cases.",
            )
        )
    if "reference model" in joined or "run_tests" in joined:
        signatures.append(
            (
                "reference_model_contract_gap",
                "Preserve `run_tests()` and the Python CLI entrypoint, then mirror its vectors downstream.",
            )
        )
    if "reviewability" in joined or "comment" in joined:
        signatures.append(
            (
                "comment_reviewability_gap",
                "Use the requested comment language and add adjacent comments for every required RTL declaration, block, assign, and testbench case check.",
            )
        )
    if "fsm" in joined or "state register" in joined or "next-state" in joined:
        signatures.append(
            (
                "rtl_fsm_structure_gap",
                "Use three-block RTL FSM style with state register, next-state logic, and output logic labels.",
            )
        )
    if "toolchain_issue" in sources or "required tool" in joined:
        signatures.append(
            (
                "toolchain_unavailable_or_failed",
                "Separate tool availability failures from code edits and rerun with the required readiness tool installed.",
            )
        )
    if "spec_issue" in sources or "evidence" in joined:
        signatures.append(
            (
                "spec_or_evidence_gap",
                "Audit evidence coverage and requested outputs before regenerating implementation files.",
            )
        )
    if "semantic output drift" in joined or "wrong_final_output" in joined:
        signatures.append(
            (
                "wrong_final_output",
                "Reconfirm case outputs against the Python oracle and focus regeneration on the drift keys reported by semantic validation.",
            )
        )
    if "checkpoint drift" in joined or "checkpoint_divergence" in joined:
        signatures.append(
            (
                "checkpoint_divergence",
                "Preserve intermediate checkpoints and use them to localize where the Verilog behavior diverges from the Python oracle.",
            )
        )
    if "case order drift" in joined or "case_order_drift" in joined:
        signatures.append(
            (
                "case_order_drift",
                "Keep case ordering and case ids stable between the reference contract and the Verilog transcript.",
            )
        )
    if "weak_test_oracle" in joined or "augment_tests" in joined:
        signatures.append(
            (
                "weak_test_oracle",
                "Add stronger boundary, negative, and checkpoint cases before escalating to a human.",
            )
        )
    if "ambiguous_spec_rule" in joined:
        signatures.append(
            (
                "ambiguous_spec_rule",
                "Preserve the spec conflict explicitly and route it into structured human intervention rather than inferring behavior.",
            )
        )
    return signatures


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    deduped: list[dict[str, Any]] = []
    for entry in entries:
        key = (entry.get("subfunction"), entry.get("stage"), entry.get("error_signature"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped

