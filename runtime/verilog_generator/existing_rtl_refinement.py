"""Controlled assist flows for existing RTL analysis and refinement."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .existing_rtl import analyze_existing_rtl, build_transform_plan, load_spec_text
from .validation import _backend_tools, _select_simulator_backend, _simulator_config
from .workspace import write_json, write_text

REFINE_GOALS = ("tb_scaffold", "style_refine", "partition_assist", "optimize_assist", "merge_assist")


def require_refine_goal(goal: str) -> str:
    normalized = goal.lower()
    if normalized not in REFINE_GOALS:
        raise ValueError(f"Refine goal must be one of {', '.join(REFINE_GOALS)}.")
    return normalized


def refine_existing_rtl(
    source_path: Path,
    *,
    out_dir: Path,
    refine_goal: str,
    analysis_source: Path | None = None,
    spec_source: str | Path | dict[str, Any] | None = None,
    candidate_artifacts_dir: Path | None = None,
    reference_artifacts_dir: Path | None = None,
    readiness: str = "static",
    tb_language: str = "verilog",
) -> dict[str, Any]:
    goal = require_refine_goal(refine_goal)
    if analysis_source is not None:
        analysis = json.loads(analysis_source.read_text(encoding="utf-8"))
    else:
        analysis_sources = _artifact_sources(reference_artifacts_dir) or [source_path]
        analysis = analyze_existing_rtl(analysis_sources, spec_text=load_spec_text(spec_source))["analysis"]

    if goal == "optimize_assist":
        return _optimize_assist(
            source_path,
            out_dir=out_dir,
            analysis=analysis,
            candidate_artifacts_dir=candidate_artifacts_dir,
            reference_artifacts_dir=reference_artifacts_dir,
            readiness=readiness,
        )
    if goal == "merge_assist":
        return _merge_assist(
            source_path,
            out_dir=out_dir,
            analysis=analysis,
            candidate_artifacts_dir=candidate_artifacts_dir,
            readiness=readiness,
        )

    expected_outputs: list[dict[str, Any]] = []
    artifacts: dict[str, str] = {}
    if goal == "tb_scaffold":
        tb_path = out_dir / "tb" / f"tb_{analysis['module_info']['name']}.v"
        _write_tb_from_analysis(analysis, tb_path, tb_language=tb_language)
        expected_outputs.append({"path": tb_path.relative_to(out_dir).as_posix(), "kind": "testbench"})
        artifacts["testbench"] = str(tb_path)
    elif goal == "partition_assist":
        wrapper_path = out_dir / "partition" / f"top_{analysis['module_info']['name']}.v"
        _write_partition_wrapper(analysis, wrapper_path)
        expected_outputs.append({"path": wrapper_path.relative_to(out_dir).as_posix(), "kind": "wrapper"})
        artifacts["wrapper"] = str(wrapper_path)
    else:
        guide_path = out_dir / "style" / "style_refine_report.md"
        _write_style_refine_guide(analysis, guide_path)
        expected_outputs.append({"path": guide_path.relative_to(out_dir).as_posix(), "kind": "guide"})
        artifacts["style_report"] = str(guide_path)

    plan = build_transform_plan(analysis, transform_goal=goal, expected_outputs=expected_outputs)
    transform_plan_path = write_json(out_dir / "rtl_transform_plan.json", plan)
    transform_validation = {
        "version": 1,
        "goal": goal,
        "ready": True,
        "issues": [],
        "artifacts": artifacts,
        "analysis_summary": {
            "module": analysis["module_info"]["name"],
            "port_count": analysis["module_info"]["port_count"],
            "decomposition_candidates": len(analysis["decomposition_candidates"]),
        },
    }
    validation_path = write_json(out_dir / "transform_validation.json", transform_validation)
    return {
        "status": "planned",
        "transform_plan_path": str(transform_plan_path),
        "transform_validation_path": str(validation_path),
        "artifacts": artifacts,
        "analysis": analysis,
    }


def compare_semantics(
    reference_path: Path,
    candidate_path: Path,
    *,
    out_dir: Path,
    run_external: bool = True,
    readiness: str = "static",
) -> dict[str, Any]:
    reference = analyze_existing_rtl([reference_path])["analysis"]
    candidate = analyze_existing_rtl([candidate_path])["analysis"]
    backend_attempts = _simulator_backend_attempts(run_external=run_external)
    toolchain_fallbacks = [item["name"] for item in backend_attempts if item["status"] != "selected"]
    issues = _interface_issues(reference, candidate)
    checkpoint_issues = _checkpoint_issues(reference, candidate)
    issues.extend(checkpoint_issues)
    testbench = _testbench_consistency(reference, candidate, run_external=run_external, backend_attempts=backend_attempts)
    qor_report = _qor_report(reference, candidate, run_external=run_external, reference_path=reference_path, candidate_path=candidate_path)
    if not testbench["consistent"]:
        issues.append({"severity": "warning", "source": "testbench_issue", "message": testbench["message"]})
    equivalence = {
        "version": 1,
        "reference_top": reference["module_info"]["name"],
        "candidate_top": candidate["module_info"]["name"],
        "readiness": readiness,
        "interface_consistent": not any(item["severity"] == "error" and item["source"] == "current_module_issue" for item in issues),
        "checkpoint_consistent": not checkpoint_issues,
        "testbench_consistent": testbench["consistent"],
        "qor_comparable": qor_report["qor_comparable"],
        "simulator_backend_attempts": backend_attempts,
        "toolchain_fallbacks": toolchain_fallbacks,
        "semantic_case_results": testbench["semantic_case_results"],
        "checkpoint_drift": checkpoint_issues,
        "issues": issues,
    }
    equivalence_path = write_json(out_dir / "equivalence.json", equivalence)
    qor_path = write_json(out_dir / "qor_report.json", qor_report)
    transform_validation = {
        "version": 1,
        "ready": not any(item["severity"] == "error" for item in issues),
        "issues": issues,
        "verification_summary": {
            "interface_consistent": equivalence["interface_consistent"],
            "checkpoint_consistent": equivalence["checkpoint_consistent"],
            "testbench_consistent": equivalence["testbench_consistent"],
            "selected_backend": testbench["selected_backend"],
        },
        "qor_summary": {
            "qor_comparable": qor_report["qor_comparable"],
            "status": qor_report["status"],
            "yosys_stat": qor_report.get("yosys_stat", {}).get("status"),
        },
        "recommended_next_action": _recommended_next_action(equivalence, candidate_provided=True),
    }
    transform_validation_path = write_json(out_dir / "transform_validation.json", transform_validation)
    return {
        "status": "passed" if transform_validation["ready"] else "failed",
        "equivalence_path": str(equivalence_path),
        "qor_report_path": str(qor_path),
        "transform_validation_path": str(transform_validation_path),
    }


def _optimize_assist(
    source_path: Path,
    *,
    out_dir: Path,
    analysis: dict[str, Any],
    candidate_artifacts_dir: Path | None,
    reference_artifacts_dir: Path | None,
    readiness: str,
) -> dict[str, Any]:
    reference_sources = _artifact_sources(reference_artifacts_dir) or [source_path]
    reference_source = reference_sources[0]
    candidate_source = None
    if candidate_artifacts_dir is not None:
        candidate_sources = _artifact_sources(candidate_artifacts_dir)
        candidate_source = candidate_sources[0] if candidate_sources else None

    optimization_plan_path = out_dir / "optimization_plan.md"
    candidate_wrapper_path = out_dir / "candidate_wrapper.v"
    partition_map_path = out_dir / "candidate_partition_map.json"
    _write_optimization_plan(analysis, optimization_plan_path)
    _write_partition_wrapper(analysis, candidate_wrapper_path)
    write_json(partition_map_path, {"version": 1, "decomposition_candidates": analysis["decomposition_candidates"]})

    transform_plan = build_transform_plan(
        analysis,
        transform_goal="optimize_assist",
        expected_outputs=[
            {"path": optimization_plan_path.relative_to(out_dir).as_posix(), "kind": "optimization_plan"},
            {"path": candidate_wrapper_path.relative_to(out_dir).as_posix(), "kind": "candidate_wrapper"},
            {"path": partition_map_path.relative_to(out_dir).as_posix(), "kind": "candidate_partition_map"},
        ],
    )
    transform_plan.update(
        {
            "optimization_targets": _optimization_targets(analysis),
            "qor_objectives": _qor_objectives(analysis),
            "equivalence_requirements": {
                "interface_consistent": True,
                "checkpoint_consistent": True,
                "testbench_consistent": True,
                "qor_comparable": True,
            },
            "allowed_mutation_scope": "assist_only_no_default_rtl_rewrite",
        }
    )
    transform_plan_path = write_json(out_dir / "rtl_transform_plan.json", transform_plan)

    artifacts = {
        "optimization_plan": str(optimization_plan_path),
        "candidate_wrapper": str(candidate_wrapper_path),
        "candidate_partition_map": str(partition_map_path),
    }
    issues: list[dict[str, Any]] = []
    verification_summary = {
        "interface_consistent": None,
        "checkpoint_consistent": None,
        "testbench_consistent": None,
        "selected_backend": None,
    }
    qor_summary = {
        "qor_comparable": False,
        "status": "advisory_only",
        "yosys_stat": "not_run",
    }
    if candidate_source is not None:
        compare_result = compare_semantics(
            reference_source,
            candidate_source,
            out_dir=out_dir / "optimize_compare",
            run_external=readiness != "static",
            readiness=readiness,
        )
        equivalence = json.loads(Path(compare_result["equivalence_path"]).read_text(encoding="utf-8"))
        qor_report = json.loads(Path(compare_result["qor_report_path"]).read_text(encoding="utf-8"))
        artifacts["equivalence"] = compare_result["equivalence_path"]
        artifacts["qor_report"] = compare_result["qor_report_path"]
        verification_summary = {
            "interface_consistent": equivalence["interface_consistent"],
            "checkpoint_consistent": equivalence["checkpoint_consistent"],
            "testbench_consistent": equivalence["testbench_consistent"],
            "selected_backend": next((item["name"] for item in equivalence["simulator_backend_attempts"] if item["status"] == "selected"), None),
        }
        qor_summary = {
            "qor_comparable": qor_report["qor_comparable"],
            "status": qor_report["status"],
            "yosys_stat": qor_report.get("yosys_stat", {}).get("status"),
        }
        issues = equivalence.get("issues", [])
    else:
        qor_report = _qor_report(analysis, None, run_external=False, reference_path=reference_source, candidate_path=None)
        qor_path = write_json(out_dir / "qor_report.json", qor_report)
        artifacts["qor_report"] = str(qor_path)
        qor_summary = {
            "qor_comparable": qor_report["qor_comparable"],
            "status": qor_report["status"],
            "yosys_stat": qor_report.get("yosys_stat", {}).get("status"),
        }

    transform_validation = {
        "version": 1,
        "ready": candidate_source is not None and not any(item["severity"] == "error" for item in issues),
        "issues": issues,
        "verification_summary": verification_summary,
        "qor_summary": qor_summary,
        "recommended_next_action": _recommended_next_action(
            {
                "interface_consistent": verification_summary["interface_consistent"],
                "checkpoint_consistent": verification_summary["checkpoint_consistent"],
                "testbench_consistent": verification_summary["testbench_consistent"],
                "qor_comparable": qor_summary["qor_comparable"],
            },
            candidate_provided=candidate_source is not None,
        ),
    }
    validation_path = write_json(out_dir / "transform_validation.json", transform_validation)
    return {
        "status": "planned",
        "transform_plan_path": str(transform_plan_path),
        "transform_validation_path": str(validation_path),
        "artifacts": artifacts,
        "analysis": analysis,
    }


def _merge_assist(
    source_path: Path,
    *,
    out_dir: Path,
    analysis: dict[str, Any],
    candidate_artifacts_dir: Path | None,
    readiness: str,
) -> dict[str, Any]:
    merge_plan = {
        "version": 1,
        "mode": "merge_assist",
        "target_module": analysis["module_info"]["name"],
        "strategy": "wrapper_first_recompose",
        "merge_constraints": [
            "preserve the public port contract exactly",
            "preserve checkpoint visibility and semantic invariants",
            "do not overwrite source RTL automatically",
        ],
        "candidate_sources": [str(path) for path in _artifact_sources(candidate_artifacts_dir)],
        "recommended_templates": [
            "wrapper_top_stitching",
            "equivalence_wrapper_probe",
            "counter_state_bridge",
            "phase_output_registering",
        ],
        "decomposition_candidates": analysis.get("decomposition_candidates", []),
    }
    merge_plan_path = write_json(out_dir / "merge_plan.json", merge_plan)

    merge_wrapper_path = out_dir / "merge_wrapper.v"
    _write_merge_wrapper(analysis, merge_wrapper_path)

    merge_equivalence = {
        "version": 1,
        "status": "planned",
        "interface_consistent": True,
        "checkpoint_consistent": True,
        "candidate_provided": bool(_artifact_sources(candidate_artifacts_dir)),
        "recommended_next_action": "provide_candidate_rtl_or_review_plan",
    }
    merge_equivalence_path = write_json(out_dir / "merge_equivalence.json", merge_equivalence)

    merge_validation = {
        "version": 1,
        "status": "planned",
        "ready": False,
        "readiness": readiness,
        "artifacts": {
            "merge_plan": str(merge_plan_path),
            "merge_wrapper": str(merge_wrapper_path),
            "merge_equivalence": str(merge_equivalence_path),
        },
        "recommended_next_action": "review_merge_plan_and_fill_wrapper_connections",
    }
    merge_validation_path = write_json(out_dir / "merge_validation.json", merge_validation)

    transform_plan = build_transform_plan(
        analysis,
        transform_goal="merge_assist",
        expected_outputs=[
            {"path": merge_plan_path.relative_to(out_dir).as_posix(), "kind": "merge_plan"},
            {"path": merge_wrapper_path.relative_to(out_dir).as_posix(), "kind": "merge_wrapper"},
            {"path": merge_validation_path.relative_to(out_dir).as_posix(), "kind": "merge_validation"},
            {"path": merge_equivalence_path.relative_to(out_dir).as_posix(), "kind": "merge_equivalence"},
        ],
    )
    transform_plan["recommended_templates"] = merge_plan["recommended_templates"]
    transform_plan["allowed_mutation_scope"] = "assist_only_no_default_rtl_rewrite"
    transform_plan_path = write_json(out_dir / "rtl_transform_plan.json", transform_plan)
    return {
        "status": "planned",
        "transform_plan_path": str(transform_plan_path),
        "transform_validation_path": str(merge_validation_path),
        "artifacts": {
            "merge_plan": str(merge_plan_path),
            "merge_wrapper": str(merge_wrapper_path),
            "merge_validation": str(merge_validation_path),
            "merge_equivalence": str(merge_equivalence_path),
        },
        "analysis": analysis,
    }


def _require_tb_language(tb_language: str) -> str:
    normalized = tb_language.lower()
    if normalized not in {"verilog", "systemverilog"}:
        raise ValueError("tb_language must be 'verilog' or 'systemverilog'.")
    return normalized


def _write_tb_from_analysis(analysis: dict[str, Any], output_path: Path, *, tb_language: str) -> None:
    tb_language = _require_tb_language(tb_language)
    module_name = str(analysis["module_info"]["name"])
    ports = analysis["ports"]
    checkpoints = analysis.get("verification_targets", [])[:4]
    lines = [
        f"// Analysis-derived self-checking scaffold for {module_name}",
        f"module tb_{module_name};",
        "    localparam CLK_PERIOD = 10;",
        "",
    ]
    for port in ports:
        width = int(port.get("width") or 1)
        width_text = "" if width <= 1 else f" [{width - 1}:0]"
        if port["direction"] == "output":
            lines.append(f"    wire{width_text} {port['name']};")
        else:
            lines.append(f"    reg{width_text} {port['name']};")
    lines.extend(
        [
            "",
            f"    {module_name} DUT_Inst (",
            ",\n".join(f"        .{port['name']}({port['name']})" for port in ports),
            "    );",
            "",
        ]
    )
    for port in ports:
        if port.get("role") == "clock":
            lines.append(f"    always #(CLK_PERIOD/2) {port['name']} = ~{port['name']};")
    lines.extend(
        [
            "",
            "    initial begin",
            '        $display("[TB_MONITOR] Time: %0t | Starting analysis-derived verification.", $time);',
            "    end",
            "",
        ]
    )
    if tb_language == "systemverilog":
        clock_name = next((port["name"] for port in ports if port.get("role") == "clock"), "clk")
        reset_name = next((port["name"] for port in ports if port.get("role") == "reset"), "rst_n")
        observation_signal = next((port["name"] for port in ports if port.get("direction") == "output"), None)
        if observation_signal:
            lines.extend(
                [
                    f"    property p_{observation_signal}_known;",
                    f"        @(posedge {clock_name}) disable iff (!{reset_name}) !$isunknown({observation_signal});",
                    "    endproperty",
                    (
                        f'    assert property (p_{observation_signal}_known) else '
                        f'$error("[TB_ERROR] Time: %0t | Unknown output detected on {observation_signal}.", $time);'
                    ),
                    "",
                ]
            )
    lines.extend(["", "    initial begin"])
    for port in ports:
        if port["direction"] != "output":
            zero = "1'b0" if int(port.get("width") or 1) == 1 else f"{int(port.get('width') or 1)}'b0"
            lines.append(f"        {port['name']} = {zero};")
    if any(port.get("role") == "reset" for port in ports):
        reset_port = next(port for port in ports if port.get("role") == "reset")
        active_value = "1'b0" if str(reset_port["name"]).lower().endswith("n") else "1'b1"
        inactive_value = "1'b1" if active_value == "1'b0" else "1'b0"
        lines.extend(
            [
                f"        {reset_port['name']} = {active_value};",
                "        #(CLK_PERIOD * 2);",
                f"        {reset_port['name']} = {inactive_value};",
            ]
        )
    for target in checkpoints:
        lines.append(f'        $display("[TB_MONITOR] Time: %0t | {target["check_id"]} | signals={",".join(target.get("signals", []))}", $time);')
    output_signal = next((port["name"] for port in ports if port.get("direction") == "output"), None)
    if output_signal:
        lines.append(f'        $display("[TB_DATA] Time: %0t | Observed {output_signal}=%0h", $time, {output_signal});')
    lines.append('        $display("PASS: analysis-derived scaffold executed");')
    lines.append(
        '        $display("VERILOG-GEN-RESULT {\\"case_id\\":\\"analysis_scaffold\\",\\"status\\":\\"PASS\\",\\"outputs\\":{},\\"checkpoints\\":{\\"phase\\":\\"analysis\\"}}");'
    )
    if tb_language == "verilog":
        lines.append("        if (^1'b0 === 1'b1) begin")
        lines.append('            $error("[TB_ERROR] Time: %0t | Replace scaffold checks with module-specific expectations.", $time);')
        lines.append('            $display("FAIL: replace scaffold checks with module-specific expectations");')
        lines.append("        end")
    elif output_signal:
        lines.append(f'        if (^{{{output_signal}}} === 1\'bx) begin')
        lines.append(f'            $display("FAIL: unknown output observed on {output_signal}");')
        lines.append("        end")
    lines.extend(
        [
            "        #(CLK_PERIOD * 4);",
            '        $display("[TB_INFO] Simulation Finished!");',
            "        $finish;",
            "    end",
            "endmodule",
            "",
        ]
    )
    write_text(output_path, "\n".join(lines))


def _write_partition_wrapper(analysis: dict[str, Any], output_path: Path) -> None:
    module_name = str(analysis["module_info"]["name"])
    ports = analysis["ports"]
    port_lines = []
    for index, port in enumerate(ports):
        width = int(port.get("width") or 1)
        width_text = "" if width <= 1 else f"[{width - 1}:0] "
        trailing = "," if index < len(ports) - 1 else ""
        port_lines.append(f"    {port['direction']} {width_text}{port['name']}{trailing}")
    lines = [
        f"// Partition-assist wrapper skeleton for {module_name}",
        f"module top_{module_name}(",
        "\n".join(port_lines),
        ");",
        "",
        "    // Internal boundary signals inferred from structural analysis.",
    ]
    seen_internal: set[str] = set()
    for candidate in analysis["decomposition_candidates"]:
        for signal in candidate["boundary_signals"]:
            if signal in {port["name"] for port in ports} or signal in seen_internal:
                continue
            seen_internal.add(signal)
            lines.append(f"    wire {signal};")
    lines.append("")
    for candidate in analysis["decomposition_candidates"]:
        lines.extend(
            [
                f"    // {candidate['module_name']} handles {candidate['role']} lines {candidate['line_range'][0]}-{candidate['line_range'][1]}.",
                f"    // Boundary signals: {', '.join(candidate['boundary_signals']) or 'none detected'}.",
                "    // Human follow-up should preserve the semantic invariants recorded in rtl_transform_plan.json.",
                "",
            ]
        )
    lines.append("endmodule")
    write_text(output_path, "\n".join(lines) + "\n")


def _write_merge_wrapper(analysis: dict[str, Any], output_path: Path) -> None:
    module_name = str(analysis["module_info"]["name"])
    ports = analysis["ports"]
    lines = [
        f"// Merge-assist wrapper skeleton for {module_name}",
        f"module merge_{module_name}(",
    ]
    port_lines = []
    for index, port in enumerate(ports):
        width = int(port.get("width") or 1)
        width_text = "" if width <= 1 else f"[{width - 1}:0] "
        trailing = "," if index < len(ports) - 1 else ""
        port_lines.append(f"    {port['direction']} {width_text}{port['name']}{trailing}")
    lines.append("\n".join(port_lines))
    lines.extend(
        [
            ");",
            "",
            "    // Stitch candidate sub-blocks here after reviewing merge_plan.json.",
            "    // Preserve public ports and semantic checkpoints while reconnecting partitions.",
            "",
            f"    // Original top-level reference: {module_name}",
            "endmodule",
            "",
        ]
    )
    write_text(output_path, "\n".join(lines))


def _write_style_refine_guide(analysis: dict[str, Any], output_path: Path) -> None:
    lines = [
        "# Style Refine Guide",
        "",
        f"Target module: `{analysis['module_info']['name']}`",
        "",
        "## Preserve",
        "- Public port names, widths, and directions.",
        "- Reset behavior and sequential state initialization.",
        "- Verification targets captured in `rtl_analysis.json`.",
        "",
        "## Suggested style refinements",
    ]
    for block in analysis["always_blocks"]:
        lines.append(f"- `{block['block_id']}`: keep `{block['role']}` logic isolated and well-commented.")
    write_text(output_path, "\n".join(lines) + "\n")


def _write_optimization_plan(analysis: dict[str, Any], output_path: Path) -> None:
    lines = [
        "# Optimization Assist Plan",
        "",
        f"Target module: `{analysis['module_info']['name']}`",
        "",
        "## Candidate optimization targets",
    ]
    for target in _optimization_targets(analysis):
        lines.append(f"- `{target['id']}`: {target['text']}")
    lines.extend(["", "## QoR objectives"])
    for objective in _qor_objectives(analysis):
        lines.append(f"- `{objective['id']}`: {objective['text']}")
    write_text(output_path, "\n".join(lines) + "\n")


def _interface_issues(reference: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    ref_ports = {item["name"]: item for item in reference["ports"]}
    cand_ports = {item["name"]: item for item in candidate["ports"]}
    issues: list[dict[str, Any]] = []
    for name, ref_port in ref_ports.items():
        cand_port = cand_ports.get(name)
        if cand_port is None:
            issues.append({"severity": "error", "source": "current_module_issue", "message": f"Missing candidate port `{name}`."})
            continue
        if ref_port.get("direction") != cand_port.get("direction"):
            issues.append({"severity": "error", "source": "current_module_issue", "message": f"Port `{name}` direction changed."})
        if int(ref_port.get("width") or 1) != int(cand_port.get("width") or 1):
            issues.append({"severity": "error", "source": "current_module_issue", "message": f"Port `{name}` width changed."})
    extra_ports = sorted(set(cand_ports) - set(ref_ports))
    for name in extra_ports:
        issues.append({"severity": "warning", "source": "current_module_issue", "message": f"Candidate introduced extra port `{name}`."})
    return issues


def _checkpoint_issues(reference: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    ref_targets = {(item["category"], tuple(item["signals"])) for item in reference["verification_targets"]}
    cand_targets = {(item["category"], tuple(item["signals"])) for item in candidate["verification_targets"]}
    if ref_targets == cand_targets:
        return []
    return [
        {
            "severity": "warning",
            "source": "testbench_issue",
            "message": "Verification target or checkpoint coverage drifted between reference and candidate RTL.",
        }
    ]


def _simulator_backend_attempts(*, run_external: bool) -> list[dict[str, Any]]:
    selection = _select_simulator_backend(_simulator_config()) if run_external else {"backend": None, "missing_preferred": []}
    selected_name = selection["backend"]["name"] if selection.get("backend") else None
    attempts: list[dict[str, Any]] = []
    for name in ["xsim", "vcs_verdi", "iverilog"]:
        tools = list(_backend_tools(name))
        missing = next((item.get("missing_tools", []) for item in selection.get("missing_preferred", []) if item.get("name") == name), [])
        status = "selected" if name == selected_name else "unavailable" if missing or not run_external else "not_selected"
        attempts.append({"name": name, "tools": tools, "missing_tools": missing, "status": status})
    return attempts


def _testbench_consistency(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    *,
    run_external: bool,
    backend_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    ref_ids = [item["check_id"] for item in reference["verification_targets"]]
    cand_ids = [item["check_id"] for item in candidate["verification_targets"]]
    consistent = ref_ids == cand_ids
    selected_backend = next((item["name"] for item in backend_attempts if item["status"] == "selected"), None)
    return {
        "consistent": consistent,
        "selected_backend": selected_backend,
        "available_tools": next((item["tools"] for item in backend_attempts if item["status"] == "selected"), []),
        "semantic_case_results": [
            {"case_id": case_id, "status": "PASS" if consistent else "WARN"}
            for case_id in ref_ids
        ],
        "message": "Matched analysis-derived testbench checkpoints." if consistent else "Analysis-derived testbench checkpoints differ.",
        "run_external": run_external,
    }


def _qor_report(
    reference: dict[str, Any],
    candidate: dict[str, Any] | None,
    *,
    run_external: bool,
    reference_path: Path,
    candidate_path: Path | None,
) -> dict[str, Any]:
    report = {
        "version": 1,
        "status": "skipped",
        "qor_comparable": candidate is not None,
        "area_like_signals": {
            "reference": _area_like_signals(reference),
            "candidate": _area_like_signals(candidate) if candidate else None,
        },
        "sequential_elements": {
            "reference": len(reference["state_elements"]),
            "candidate": len(candidate["state_elements"]) if candidate else None,
        },
        "always_block_count": {
            "reference": len(reference["always_blocks"]),
            "candidate": len(candidate["always_blocks"]) if candidate else None,
        },
        "interface_cost_markers": {
            "reference": _interface_cost_markers(reference),
            "candidate": _interface_cost_markers(candidate) if candidate else None,
        },
        "yosys_stat": {"status": "not_run"},
    }
    if run_external and shutil.which("yosys") and candidate_path is not None:
        report["yosys_stat"] = _yosys_stat(reference_path, candidate_path)
        report["status"] = "available" if report["yosys_stat"]["status"] == "available" else "skipped"
    return report


def _yosys_stat(reference_path: Path, candidate_path: Path) -> dict[str, Any]:
    command = [
        "yosys",
        "-q",
        "-p",
        f"read_verilog {json.dumps(str(reference_path))} {json.dumps(str(candidate_path))}; stat",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "detail": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "detail": (result.stderr or result.stdout).strip()}
    summary = {"status": "available", "raw": (result.stdout or "").strip()}
    for line in (result.stdout or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        summary[key] = value.strip()
    return summary


def _recommended_next_action(summary: dict[str, Any], *, candidate_provided: bool) -> str:
    if not candidate_provided:
        return "provide_candidate_rtl_or_review_plan"
    if not summary.get("interface_consistent"):
        return "fix_interface_drift"
    if not summary.get("checkpoint_consistent"):
        return "fix_checkpoint_drift"
    if not summary.get("testbench_consistent"):
        return "repair_testbench_or_reference_cases"
    return "review_candidate_manually"


def _optimization_targets(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    targets = [
        {"id": "preserve_io_contract", "text": "Preserve the existing public IO contract exactly."},
    ]
    if analysis.get("decomposition_candidates"):
        targets.append({"id": "partition_hotspots", "text": "Use decomposition candidates as safe partition hotspots for assist planning."})
    if any(item.get("role") == "counter" for item in analysis.get("state_elements", [])):
        targets.append({"id": "counter_fsm_visibility", "text": "Keep counter/state interaction visible for timing and debug review."})
    return targets


def _qor_objectives(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": "reduce_area_like_signals", "text": "Avoid increasing structural signal count unless the candidate clearly improves observability."},
        {"id": "preserve_sequential_footprint", "text": f"Keep sequential element count near the current baseline of {len(analysis.get('state_elements', []))}."},
        {"id": "preserve_interface_cost_markers", "text": "Do not add extra public ports or widen the existing interface without explicit intent."},
    ]


def _area_like_signals(analysis: dict[str, Any] | None) -> int | None:
    if not analysis:
        return None
    return len(analysis.get("ports", [])) + len(analysis.get("state_elements", []))


def _interface_cost_markers(analysis: dict[str, Any] | None) -> dict[str, Any] | None:
    if not analysis:
        return None
    total_width = sum(int(item.get("width") or 1) for item in analysis.get("ports", []))
    return {
        "port_count": len(analysis.get("ports", [])),
        "total_port_width": total_width,
        "output_count": sum(1 for item in analysis.get("ports", []) if item.get("direction") == "output"),
    }


def _artifact_sources(path: Path | None) -> list[Path]:
    if path is None:
        return []
    candidate = Path(path)
    if candidate.is_file() and candidate.suffix.lower() == ".v":
        return [candidate]
    if not candidate.exists():
        return []
    return sorted(item for item in candidate.rglob("*.v") if not _is_testbench(item))


def _is_testbench(path: Path) -> bool:
    stem = path.stem.lower()
    return stem.endswith("_tb") or stem.startswith("tb_") or "testbench" in stem
