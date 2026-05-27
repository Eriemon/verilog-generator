"""Analyze existing Verilog RTL into stable JSON contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .workspace import write_json

MODULE_RE = re.compile(
    r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\((?P<params>.*?)\))?\s*\((?P<ports>.*?)\)\s*;",
    re.DOTALL,
)
DECL_RE = re.compile(r"\b(reg|wire|integer)\b\s*(\[[^\]]+\]\s*)?([^;]+);")
PORT_DECL_RE = re.compile(r"\b(input|output|inout)\b\s*(?:reg\s+|wire\s+)?(\[[^\]]+\]\s*)?([^;,\)]+)")
ALWAYS_RE = re.compile(r"(?m)^\s*always\s*@\s*\((.*?)\)")
ASSIGN_RE = re.compile(r"(?m)^\s*assign\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")
ASSIGNED_SIGNAL_RE = re.compile(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:<=|=)")
INSTANTIATION_RE = re.compile(
    r"(?m)^\s*(?!module\b|endmodule\b|if\b|for\b|while\b|case\b|assign\b|always\b)"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\(.*?\))?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.DOTALL,
)
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
KEYWORD_BLACKLIST = {
    "if",
    "else",
    "case",
    "endcase",
    "begin",
    "end",
    "assign",
    "always",
    "module",
    "endmodule",
    "posedge",
    "negedge",
    "or",
}


def analyze_existing_rtl(
    source_paths: list[Path],
    *,
    spec_text: str | None = None,
    module_name: str | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    texts = {path.resolve(): path.read_text(encoding="utf-8", errors="ignore") for path in source_paths}
    project_analysis = _project_analysis(texts, module_name=module_name)
    selected_module = _select_module(texts, project_analysis["selected_top_module"])
    if selected_module is None:
        raise ValueError("No Verilog module declaration was found in the provided source files.")

    analysis = _build_analysis_payload(
        selected_module["path"],
        selected_module["name"],
        selected_module["text"],
        spec_text=spec_text,
    )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        analysis_path = write_json(out_dir / "rtl_analysis.json", analysis)
        project_analysis_path = write_json(out_dir / "project_analysis.json", project_analysis)
        design_explanation_path = out_dir / "design_explanation.md"
        design_explanation_path.write_text(_design_explanation_markdown(analysis, project_analysis), encoding="utf-8")
        return {
            "analysis": analysis,
            "analysis_path": analysis_path,
            "project_analysis": project_analysis,
            "project_analysis_path": project_analysis_path,
            "design_explanation_path": design_explanation_path,
        }
    return {"analysis": analysis, "project_analysis": project_analysis}


def load_spec_text(spec_source: str | Path | dict[str, Any] | None) -> str | None:
    if spec_source is None:
        return None
    if isinstance(spec_source, dict):
        return json.dumps(spec_source, indent=2, ensure_ascii=False)
    path = Path(spec_source)
    return path.read_text(encoding="utf-8", errors="ignore")


def build_transform_plan(
    analysis: dict[str, Any],
    *,
    transform_goal: str,
    expected_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    module_name = str(analysis["module_info"]["name"])
    outputs = [item["name"] for item in analysis.get("ports", []) if item.get("direction") == "output"]
    state_signals = [item["name"] for item in analysis.get("state_elements", []) if item.get("role") in {"fsm_state", "counter"}]
    semantic_invariants = [
        {
            "id": "port_contract",
            "text": f"Preserve the public port contract of `{module_name}` exactly, including widths and directions.",
        },
        {
            "id": "reset_behavior",
            "text": "Preserve reset-driven initialization behavior for outputs and sequential state.",
        },
    ]
    if outputs:
        semantic_invariants.append(
            {
                "id": "observable_outputs",
                "text": "Keep these observable outputs stable and reviewable: " + ", ".join(outputs) + ".",
            }
        )
    if state_signals:
        semantic_invariants.append(
            {
                "id": "state_progression",
                "text": "Preserve state/counter progression for " + ", ".join(state_signals) + ".",
            }
        )
    return {
        "version": 1,
        "mode": "refine_existing",
        "source_artifacts": [str(item) for item in analysis["provenance"]["source_paths"]],
        "transform_goal": transform_goal,
        "semantic_invariants": semantic_invariants,
        "expected_outputs": expected_outputs,
        "verification_strategy": {
            "interface_consistency": True,
            "checkpoint_consistency": True,
            "testbench_consistency": True,
            "preferred_simulator_order": ["xsim", "vcs_verdi", "iverilog"],
            "optional_qor_tool": "yosys",
        },
    }


def _build_analysis_payload(source_path: Path, module_name: str, text: str, *, spec_text: str | None) -> dict[str, Any]:
    stripped = _strip_comments(text)
    ports = _extract_ports(module_name, stripped)
    declarations = _extract_declarations(stripped)
    always_blocks = _extract_always_blocks(text)
    clock_signals = [item["name"] for item in ports if item.get("role") == "clock"]
    reset_signals = [item["name"] for item in ports if item.get("role") == "reset"]
    state_elements = _extract_state_elements(declarations, always_blocks)
    feature_candidates = _feature_candidates(module_name, ports, state_elements, always_blocks)
    feature_mappings = _feature_mappings(
        ports=ports,
        state_elements=state_elements,
        always_blocks=always_blocks,
        feature_candidates=feature_candidates,
        spec_text=spec_text,
    )
    verification_targets = _verification_targets(module_name, ports, state_elements, feature_mappings)
    decomposition_candidates = _decomposition_candidates(always_blocks, ports, state_elements)
    return {
        "version": 1,
        "mode": "analyze_existing",
        "module_info": {
            "name": module_name,
            "parameter_count": len(_extract_parameters(module_name, stripped)),
            "port_count": len(ports),
        },
        "clock_reset_info": {
            "clock_signals": clock_signals,
            "reset_signals": reset_signals,
        },
        "ports": ports,
        "state_elements": state_elements,
        "always_blocks": always_blocks,
        "feature_candidates": feature_candidates,
        "feature_mappings": feature_mappings,
        "verification_targets": verification_targets,
        "decomposition_candidates": decomposition_candidates,
        "provenance": {
            "source_paths": [str(source_path)],
            "spec_source_provided": bool(spec_text),
            "analyzer": "existing_rtl",
        },
    }


def _select_module(texts: dict[Path, str], module_name: str | None) -> dict[str, Any] | None:
    for path, text in texts.items():
        for match in MODULE_RE.finditer(_strip_comments(text)):
            name = match.group(1)
            if module_name and name != module_name:
                continue
            if not module_name and name.lower().endswith("_tb"):
                continue
            return {"path": path, "name": name, "text": text}
    return None


def _project_analysis(texts: dict[Path, str], *, module_name: str | None = None) -> dict[str, Any]:
    modules: list[dict[str, Any]] = []
    module_names: set[str] = set()
    for path, text in texts.items():
        stripped = _strip_comments(text)
        for match in MODULE_RE.finditer(stripped):
            name = match.group(1)
            if name.lower().endswith("_tb"):
                continue
            module_names.add(name)
            modules.append(
                {
                    "name": name,
                    "path": str(path),
                    "text": text,
                    "stripped": stripped,
                }
            )
    if not modules:
        raise ValueError("No Verilog module declaration was found in the provided source files.")

    edges: list[dict[str, str]] = []
    instantiated_children: set[str] = set()
    for module in modules:
        for child in _instantiated_modules(module["stripped"], module_names):
            edges.append({"parent": module["name"], "child": child})
            instantiated_children.add(child)

    top_candidates = [
        module["name"]
        for module in modules
        if module["name"] not in instantiated_children
    ]
    if module_name:
        selected_top = module_name
    else:
        ranked = sorted(
            top_candidates or [module["name"] for module in modules],
            key=lambda name: (
                -sum(1 for edge in edges if edge["parent"] == name),
                -next((len(_extract_ports(name, module["stripped"])) for module in modules if module["name"] == name), 0),
                name,
            ),
        )
        selected_top = ranked[0]

    return {
        "version": 1,
        "module_count": len(modules),
        "selected_top_module": selected_top,
        "top_candidates": sorted(top_candidates or [module["name"] for module in modules]),
        "modules": [
            {
                "name": module["name"],
                "path": module["path"],
                "port_count": len(_extract_ports(module["name"], module["stripped"])),
            }
            for module in modules
        ],
        "instantiation_edges": edges,
    }


def _instantiated_modules(stripped: str, module_names: set[str]) -> list[str]:
    instantiated: list[str] = []
    for match in INSTANTIATION_RE.finditer(stripped):
        child = match.group(1)
        if child in module_names and child not in instantiated:
            instantiated.append(child)
    return instantiated


def _design_explanation_markdown(analysis: dict[str, Any], project_analysis: dict[str, Any]) -> str:
    module_name = str(analysis["module_info"]["name"])
    feature_lines = [
        f"- `{item['name']}`: {item.get('description', '').strip() or 'derived from ports, state, and always blocks.'}"
        for item in analysis.get("feature_mappings", [])
    ]
    verification_lines = [
        f"- `{item['check_id']}`: {item.get('description', '').strip() or 'analysis-derived verification target.'}"
        for item in analysis.get("verification_targets", [])
    ]
    decomposition_lines = [
        f"- `{item['module_name']}` lines {item['line_range'][0]}-{item['line_range'][1]}: {item['role']}"
        for item in analysis.get("decomposition_candidates", [])
    ]
    return "\n".join(
        [
            f"# Design Explanation: {module_name}",
            "",
            "## Project Topology",
            f"- Selected top module: `{project_analysis['selected_top_module']}`",
            f"- Module count: {project_analysis['module_count']}",
            "",
            "## Interface Summary",
            *[
                f"- `{port['direction']} {port['name']}` width={int(port.get('width') or 1)} role={port.get('role', 'data')}"
                for port in analysis.get("ports", [])
            ],
            "",
            "## Feature Mapping",
            *(feature_lines or ["- No explicit feature mapping was inferred."]),
            "",
            "## Verification Targets",
            *(verification_lines or ["- No verification targets were inferred."]),
            "",
            "## Decomposition Candidates",
            *(decomposition_lines or ["- No decomposition candidates were inferred."]),
            "",
        ]
    ) + "\n"


def _extract_parameters(module_name: str, text: str) -> list[str]:
    match = _module_match(module_name, text)
    params = (match.group("params") or "").strip()
    if not params:
        return []
    return [item for item in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", params) if item not in {"parameter", "localparam"}]


def _extract_ports(module_name: str, text: str) -> list[dict[str, Any]]:
    match = _module_match(module_name, text)
    header = match.group("ports")
    ports: dict[str, dict[str, Any]] = {}
    for port_match in PORT_DECL_RE.finditer(header):
        direction = port_match.group(1)
        width = _width_from_range(port_match.group(2))
        names = [item for item in _split_names(port_match.group(3)) if item]
        for name in names:
            ports[name] = {
                "name": name,
                "direction": direction,
                "width": width,
                "role": _port_role(name),
            }
    return list(ports.values())


def _extract_declarations(text: str) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for match in DECL_RE.finditer(text):
        kind = match.group(1)
        width = _width_from_range(match.group(2))
        for name in _split_names(match.group(3)):
            if not name or name in KEYWORD_BLACKLIST:
                continue
            declarations.append(
                {
                    "name": name,
                    "kind": kind,
                    "width": width,
                    "role": _signal_role(name),
                }
            )
    return declarations


def _extract_always_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    matches = list(ALWAYS_RE.finditer(text))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else text.rfind("endmodule")
        if end == -1:
            end = len(text)
        block_text = text[start:end].strip()
        sensitivity = match.group(1).strip()
        assigned = []
        for assignment in ASSIGNED_SIGNAL_RE.finditer(block_text):
            signal = assignment.group(1)
            if signal not in assigned and signal not in KEYWORD_BLACKLIST:
                assigned.append(signal)
        referenced = []
        for token in IDENT_RE.findall(block_text):
            if token in KEYWORD_BLACKLIST or token in assigned:
                continue
            if token not in referenced:
                referenced.append(token)
        start_line = text[:start].count("\n") + 1
        end_line = text[:end].count("\n") + 1
        kind = "sequential" if ("posedge" in sensitivity or "negedge" in sensitivity) else "combinational"
        blocks.append(
            {
                "block_id": f"always_{index + 1}",
                "kind": kind,
                "sensitivity": sensitivity,
                "line_range": [start_line, end_line],
                "assigned_signals": assigned,
                "referenced_signals": referenced,
                "role": _always_block_role(assigned),
            }
        )
    return blocks


def _extract_state_elements(declarations: list[dict[str, Any]], always_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sequential_assigned = {
        signal
        for block in always_blocks
        if block["kind"] == "sequential"
        for signal in block["assigned_signals"]
    }
    state_elements: list[dict[str, Any]] = []
    for declaration in declarations:
        if declaration["name"] not in sequential_assigned:
            continue
        state_elements.append(declaration)
    return state_elements


def _feature_candidates(
    module_name: str,
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
    always_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outputs = [item["name"] for item in ports if item.get("direction") == "output"]
    candidates = [
        {
            "feature_id": "FC001",
            "name": f"{module_name} reset behavior",
            "description": "Reset initializes outputs and sequential state to known values.",
            "signals": [item["name"] for item in ports if item.get("role") == "reset"] + outputs,
        }
    ]
    for index, block in enumerate(always_blocks, start=1):
        candidates.append(
            {
                "feature_id": f"FC{index + 1:03d}",
                "name": f"{block['role'].replace('_', ' ')} block {index}",
                "description": f"{block['kind']} logic covering {', '.join(block['assigned_signals']) or 'no detected assignments'}.",
                "signals": block["assigned_signals"],
            }
        )
    if any(item["role"] == "counter" for item in state_elements):
        candidates.append(
            {
                "feature_id": "FC900",
                "name": "counter progression",
                "description": "Counter/state timing progression remains observable through public outputs.",
                "signals": [item["name"] for item in state_elements if item["role"] == "counter"] + outputs,
            }
        )
    return candidates


def _feature_mappings(
    *,
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
    always_blocks: list[dict[str, Any]],
    feature_candidates: list[dict[str, Any]],
    spec_text: str | None,
) -> list[dict[str, Any]]:
    known_signals = ports + state_elements
    if not spec_text:
        return [
            {
                "feature_id": candidate["feature_id"],
                "name": candidate["name"],
                "pin_assignments": [
                    {
                        "pin_name": signal,
                        "role": _mapping_role(signal, ports, state_elements),
                        "assignment": "preserve current behavior",
                        "note": "Derived from structural analysis without an external specification.",
                    }
                    for signal in candidate["signals"]
                ],
                "stimulus_strategy": "Use reset, nominal transitions, and observable outputs to confirm the structural behavior.",
                "expected_outputs": _expected_outputs(candidate["signals"], ports, always_blocks),
            }
            for candidate in feature_candidates
        ]
    mappings: list[dict[str, Any]] = []
    for index, feature in enumerate(_parse_spec_features(spec_text), start=1):
        matched = _match_feature_signals(feature["description"], known_signals)
        if not matched and "reset" in feature["description"].lower():
            matched = [item["name"] for item in ports if item.get("role") == "reset"]
        mappings.append(
            {
                "feature_id": f"FM{index:03d}",
                "name": feature["name"],
                "pin_assignments": [
                    {
                        "pin_name": signal,
                        "role": _mapping_role(signal, ports, state_elements),
                        "assignment": "drive or observe according to the described scenario",
                        "note": "Matched from the supplied behavioral notes.",
                    }
                    for signal in matched
                ],
                "stimulus_strategy": "Drive reset/control inputs, then observe the mapped public outputs and counters.",
                "expected_outputs": _expected_outputs(matched, ports, always_blocks),
            }
        )
    return mappings


def _verification_targets(
    module_name: str,
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
    feature_mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outputs = [item["name"] for item in ports if item.get("direction") == "output"]
    targets = [
        {
            "check_id": "reset_outputs_known",
            "category": "reset",
            "signals": [item["name"] for item in ports if item.get("role") == "reset"] + outputs,
            "description": f"Verify `{module_name}` drives known output values after reset release.",
        }
    ]
    if any(item["role"] == "counter" for item in state_elements):
        targets.append(
            {
                "check_id": "counter_progression",
                "category": "checkpoint",
                "signals": [item["name"] for item in state_elements if item["role"] == "counter"],
                "description": "Verify timer/counter progression across phase transitions.",
            }
        )
    for mapping in feature_mappings:
        if not mapping["expected_outputs"]:
            continue
        targets.append(
            {
                "check_id": mapping["feature_id"].lower(),
                "category": "feature_mapping",
                "signals": [item["pin_name"] for item in mapping["pin_assignments"]],
                "description": mapping["name"],
            }
        )
    return targets


def _decomposition_candidates(
    always_blocks: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    public_names = {item["name"] for item in ports}
    state_names = {item["name"] for item in state_elements}
    candidates: list[dict[str, Any]] = []
    for index, block in enumerate(always_blocks, start=1):
        boundary_signals = sorted(
            {
                signal
                for signal in [*block["assigned_signals"], *block["referenced_signals"]]
                if signal in public_names or signal in state_names
            }
        )
        candidates.append(
            {
                "candidate_id": f"DC{index:03d}",
                "module_name": f"u_block_{index}",
                "role": block["role"],
                "kind": block["kind"],
                "line_range": block["line_range"],
                "boundary_signals": boundary_signals,
            }
        )
    return candidates


def _parse_spec_features(spec_text: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    heading_pattern = re.compile(r"(?m)^##\s+(.+?)\s*$")
    matches = list(heading_pattern.finditer(spec_text))
    if not matches:
        normalized = " ".join(line.strip() for line in spec_text.splitlines() if line.strip())
        return [{"name": "Provided specification", "description": normalized}]
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(spec_text)
        description = " ".join(line.strip() for line in spec_text[start:end].splitlines() if line.strip())
        sections.append({"name": match.group(1).strip(), "description": description})
    return sections


def _match_feature_signals(description: str, known_signals: list[dict[str, Any]]) -> list[str]:
    lowered = description.lower()
    matched: list[str] = []
    for signal in known_signals:
        name = signal["name"]
        tokens = {name.lower(), name.lower().replace("_", " ")}
        role = str(signal.get("role") or "")
        if any(token in lowered for token in tokens) or _role_keyword(role, lowered):
            if name not in matched:
                matched.append(name)
    return matched


def _role_keyword(role: str, lowered: str) -> bool:
    role_keywords = {
        "clock": ["clock"],
        "reset": ["reset", "rst"],
        "counter": ["count", "timer", "clock output"],
        "fsm_state": ["state", "phase", "sequence"],
        "output_register": ["output", "observe"],
        "control": ["request", "enable", "control"],
    }
    return any(keyword in lowered for keyword in role_keywords.get(role, []))


def _expected_outputs(signals: list[str], ports: list[dict[str, Any]], always_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output_names = {item["name"] for item in ports if item.get("direction") == "output"}
    expected: list[dict[str, Any]] = []
    for signal in signals:
        if signal in output_names:
            expected.append(
                {
                    "pin_name": signal,
                    "expected_value": "observe behavioral change at the public output",
                    "check_timing": "after the next relevant clock or control event",
                }
            )
    if not expected and output_names:
        role_to_outputs = [item for item in always_blocks if any(signal in output_names for signal in item["assigned_signals"])]
        for block in role_to_outputs:
            for signal in block["assigned_signals"]:
                if signal in output_names:
                    expected.append(
                        {
                            "pin_name": signal,
                            "expected_value": f"follow {block['role']} semantics",
                            "check_timing": "after the associated sequential update",
                        }
                    )
    return expected


def _module_match(module_name: str, text: str) -> re.Match[str]:
    for match in MODULE_RE.finditer(text):
        if match.group(1) == module_name:
            return match
    raise ValueError(f"Module {module_name!r} was not found in the provided Verilog source.")


def _split_names(chunk: str) -> list[str]:
    cleaned = re.sub(r"\b(?:signed|unsigned|wire|reg|logic)\b", " ", chunk)
    parts = [part.strip() for part in cleaned.split(",")]
    names: list[str] = []
    for part in parts:
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", part)
        if match:
            names.append(match.group(1))
    return names


def _width_from_range(raw: str | None) -> int | None:
    if not raw:
        return 1
    match = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", raw)
    if not match:
        return None
    return abs(int(match.group(1)) - int(match.group(2))) + 1


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n\r]*", "", text)


def _port_role(name: str) -> str:
    lowered = name.lower()
    if "clk" in lowered or "clock" in lowered:
        return "clock"
    if "rst" in lowered or "reset" in lowered:
        return "reset"
    if any(token in lowered for token in ("valid", "ready", "done", "status")):
        return "status"
    if any(token in lowered for token in ("req", "request", "enable", "mode", "sel")):
        return "control"
    if any(token in lowered for token in ("data", "addr", "clock")):
        return "data"
    return "signal"


def _signal_role(name: str) -> str:
    lowered = name.lower()
    if "state" in lowered:
        return "fsm_state"
    if any(token in lowered for token in ("cnt", "count", "timer")):
        return "counter"
    if lowered.startswith("p_") or any(token in lowered for token in ("red", "yellow", "green")):
        return "output_register"
    return "register"


def _always_block_role(assigned_signals: list[str]) -> str:
    lowered = " ".join(signal.lower() for signal in assigned_signals)
    if "state" in lowered:
        return "state_transition"
    if any(token in lowered for token in ("cnt", "count", "timer")):
        return "counter_update"
    if any(token in lowered for token in ("red", "yellow", "green", "valid", "data")):
        return "output_update"
    return "logic_partition"


def _mapping_role(signal: str, ports: list[dict[str, Any]], state_elements: list[dict[str, Any]]) -> str:
    for item in ports:
        if item["name"] == signal:
            if item.get("direction") == "input":
                return "control" if item.get("role") in {"reset", "control"} else "data_input"
            if item.get("direction") == "output":
                return "status" if item.get("role") in {"status", "signal"} else "data_output"
    for item in state_elements:
        if item["name"] == signal:
            return item.get("role", "internal_state")
    return "internal_state"
