"""Prompt rendering for Verilog-2001 generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .intervention import decision_applies
from .interface_templates import InterfaceTemplateError, select_interface_template
from .planning import decompose_spec
from .reference_contract import REFERENCE_RESULT_TAG
from .spec import normalize_spec
from .vectors import VECTOR_HASH_TAG

PROMPT_STAGES = (
    "summarize",
    "decompose",
    "augment",
    "review",
    "requirements",
    "codegen_plan",
    "tests",
    "pseudocode",
    "python",
    "rtl",
)
COMMENT_LANGUAGES = ("zh", "en")
PROMPT_BUDGETS = ("normal", "compact", "repair")


def render_prompt(
    spec: dict[str, Any],
    target: str | None = None,
    stage: str | None = None,
    *,
    context_manifest: dict[str, Any] | None = None,
    context_dir: Path | None = None,
    evidence: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    comment_language: str = "zh",
    vector_contract: dict[str, Any] | None = None,
    codegen_plan: dict[str, Any] | None = None,
    subfunction: str | None = None,
    budget: str = "normal",
    decision: dict[str, Any] | None = None,
    **_: Any,
) -> str:
    normalized = normalize_spec(spec, target=target)
    comment_language = require_comment_language(comment_language)
    budget = require_prompt_budget(budget)
    if stage:
        return _render_staged_prompt(
            normalized,
            _require_stage(stage),
            context_manifest=context_manifest,
            context_dir=context_dir,
            evidence=evidence,
            memory=memory,
            comment_language=comment_language,
            vector_contract=vector_contract,
            codegen_plan=codegen_plan,
            subfunction=subfunction,
            budget=budget,
            decision=decision,
        )
    return _render_rtl_prompt(normalized, comment_language, decision=decision)


def require_comment_language(comment_language: str) -> str:
    normalized = comment_language.lower()
    if normalized not in COMMENT_LANGUAGES:
        raise ValueError(f"Comment language must be one of {', '.join(COMMENT_LANGUAGES)}.")
    return normalized


def require_prompt_budget(budget: str) -> str:
    normalized = budget.lower()
    if normalized not in PROMPT_BUDGETS:
        raise ValueError(f"Prompt budget must be one of {', '.join(PROMPT_BUDGETS)}.")
    return normalized


def _require_stage(stage: str) -> str:
    normalized = stage.lower()
    if normalized not in PROMPT_STAGES:
        raise ValueError(f"Stage must be one of {', '.join(PROMPT_STAGES)}.")
    return normalized


def _render_rtl_prompt(spec: dict[str, Any], comment_language: str, *, decision: dict[str, Any] | None = None) -> str:
    manifest = _manifest_for(spec)
    return _append_optional_sections(
        _base_prompt(
            spec=spec,
            title="Verilog RTL generation task",
            target_line="Generate synthesizable Verilog-2001 RTL.",
            rules=[
                "Implement the top module named exactly as spec.name.",
                "Declare every interface port with explicit direction and bit width.",
                "Use only Verilog-2001 syntax in design and testbench files.",
                "Prefer standardized buses when choosing interfaces: AXI-Stream for streaming data, AXI4-Lite for control/status registers, AXI4 for memory-mapped bulk transfers, and AHB/APB when the platform requires them.",
                "When a design cannot naturally use a standard memory/control bus but still needs interface unification, extend AXI-Stream with explicit sideband metadata in interface_profile.",
                "Use edge-triggered sequential logic for clocked state.",
                "Honor the reset object exactly; if reset.synchronous is true, do not put reset in an always-block sensitivity list.",
                "Separate registered state updates from combinational next-state logic when that makes the design clearer.",
                "Use complete combinational assignments with safe defaults before if/case decisions to avoid unintended latches.",
                "Use explicit default branches in case/casex/casez statements unless the spec proves a complete one-hot or binary decode.",
                "Do not create raw gated clocks with logic operators; use clock-enable RTL unless an approved clock-gating wrapper is explicitly specified.",
                "Document CDC and reset assumptions in manifest checks when more than one clock/reset domain or asynchronous reset behavior is present.",
                "Keep datapath and control structure timing-reviewable by naming pipeline registers, avoiding hidden feedback, and keeping high-fanout enables visible.",
                "Avoid #delay controls, force/release, multiple drivers, unintended latches, and non-synthesizable constructs in RTL source files.",
                "Avoid Verilog function/task blocks in generated Verilog, especially synthesizable RTL; prefer explicit always/assign logic and inline testbench checks for easier waveform debugging.",
                "Keep simulation-only system tasks inside testbench files.",
                "Include a focused self-checking testbench when requested by outputs.",
                "Cover reset, normal operation, boundary conditions, and every behavior item in the testbench.",
                "Do not leave TODO, FIXME, ellipses, placeholder text, undefined modules, or missing testbench entry modules.",
                *_rtl_style_rules(spec, comment_language),
                *_comment_rules_for("rtl", comment_language),
            ],
            manifest=manifest,
            interface_template=_interface_template_context(spec),
        ),
        decision=decision,
    )


def _base_prompt(
    *,
    spec: dict[str, Any],
    title: str,
    target_line: str,
    rules: list[str],
    manifest: dict[str, Any],
    interface_template: dict[str, Any] | None = None,
) -> str:
    spec_json = json.dumps(spec, indent=2, ensure_ascii=False)
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    rules_text = "\n".join(f"- {rule}" for rule in rules)
    interface_template_text = _format_interface_template_section(interface_template)
    return f"""# {title}

You are an expert hardware design generator. {target_line}
Think through the design internally before writing files, but do not output that analysis.

## Generation spec

```json
{spec_json}
```

## Design rules

{rules_text}

{interface_template_text}

## Output contract

Return only fenced code blocks: first the manifest JSON, then one file block per manifest file.
Do not add prose, Markdown headings, explanations, bullet lists, or analysis outside code fences.

The manifest must preserve the `files` array exactly as requested and may fill the `checks` arrays with concise strings.

```json
{manifest_json}
```

Then return one fenced code block for every manifest file, and no extra file blocks. Put the exact relative file path in the fence info as `path=<relative/path>`.

Path rules:

- Every manifest path must have exactly one matching code fence.
- Every code fence path must appear in the manifest.
- Paths must be relative, unique, case-exact, slash-exact, and must not contain `..`.
- Optional partial regeneration must use manifest `patches` entries with `path` and `marker`; patch fences must include `path=<relative/path> patch=<marker>` and target regions must be bounded by `VERILOG-GEN-PATCH-BEGIN <marker>` / `VERILOG-GEN-PATCH-END <marker>` comments.
"""


def _render_staged_prompt(
    spec: dict[str, Any],
    stage: str,
    *,
    context_manifest: dict[str, Any] | None = None,
    context_dir: Path | None = None,
    evidence: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    comment_language: str = "zh",
    vector_contract: dict[str, Any] | None = None,
    codegen_plan: dict[str, Any] | None = None,
    subfunction: str | None = None,
    budget: str = "normal",
    decision: dict[str, Any] | None = None,
) -> str:
    plan = decompose_spec(spec)
    scoped_plan = _scope_plan(plan, subfunction)
    manifest = _stage_manifest_for(scoped_plan, stage)
    stage_title, stage_goal, stage_rules = _stage_guidance(scoped_plan, stage, comment_language, vector_contract)
    context = _artifact_context(context_manifest, context_dir, budget=budget)
    memory_constraints = _memory_constraints(memory, stage, subfunction=subfunction, budget=budget)
    decision_context = decision if decision_applies(decision, subfunction) else {}
    spec_json = json.dumps(scoped_plan, indent=2, ensure_ascii=False)
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    context_json = json.dumps(context, indent=2, ensure_ascii=False)
    evidence_json = json.dumps(_evidence_context(evidence, scoped_plan, budget), indent=2, ensure_ascii=False)
    memory_json = json.dumps(memory_constraints, indent=2, ensure_ascii=False)
    vector_json = json.dumps(vector_contract or {}, indent=2, ensure_ascii=False)
    requirements_json = json.dumps(_design_requirements_context(scoped_plan), indent=2, ensure_ascii=False)
    codegen_plan_json = json.dumps(codegen_plan or {}, indent=2, ensure_ascii=False)
    decision_json = json.dumps(decision_context, indent=2, ensure_ascii=False)
    interface_template_text = _format_interface_template_section(_interface_template_context(scoped_plan))
    rules_text = "\n".join(f"- {rule}" for rule in stage_rules)
    return f"""# {stage_title}

You are implementing a staged Spec-to-Verilog workflow.
Stage goal: {stage_goal}
Use the subfunction plan as the source of truth. Think internally, but output only the requested fenced blocks.
Prompt budget: {budget}. Target subfunction: {subfunction or "all"}.

## Subfunction implementation plan

```json
{spec_json}
```

## Stage rules

{rules_text}

## Evidence context

```json
{evidence_json}
```

## Prior artifact context

```json
{context_json}
```

## Prompt memory constraints

```json
{memory_json}
```

## Design requirements

```json
{requirements_json}
```

{interface_template_text}

## Code generation plan

```json
{codegen_plan_json}
```

## Reference vector contract

When this object is non-empty, generated downstream testbenches must mirror these cases and include the exact comment `{VECTOR_HASH_TAG} <sha256>`.

```json
{vector_json}
```

## Human decision constraints

```json
{decision_json}
```

## Output contract

Return only fenced code blocks: first the manifest JSON, then one file block per manifest file.
The manifest must preserve the `files` array exactly as requested and fill `checks` with concise evidence for spec coverage, verification, execution, implementation feasibility, and reviewability.

```json
{manifest_json}
```

Every file block must use `path=<relative/path>`, and every path must match the manifest exactly.
"""


def _stage_guidance(
    spec: dict[str, Any],
    stage: str,
    comment_language: str = "zh",
    vector_contract: dict[str, Any] | None = None,
) -> tuple[str, str, list[str]]:
    common = [
        "Carry forward every subfunction dependency and interface from the plan.",
        "Do not use TODO, FIXME, ellipses, or placeholder text.",
        "For each subfunction, state how its behavior will be verified by generated artifacts.",
        "Make the stage output verifiable, executable where applicable, and ready for the next stage.",
    ]
    if stage == "requirements":
        return (
            "Confirmed requirement normalization",
            "Normalize user-confirmed Verilog design requirements into a stable pre-generation contract.",
            [
                "Summarize target, pipeline requirement, streamability classification, interface family, and confirmed profile.",
                "Do not invent missing confirmation data; record unresolved requirements as open questions.",
            ],
        )
    if stage == "codegen_plan":
        return (
            "Pre-generation code plan",
            "Produce a structured implementation plan before any Verilog code is generated.",
            [
                "Create `requirements_summary`, `interface_decision`, `pipeline_strategy`, `module_partition`, `signal_width_strategy`, `reset_clock_strategy`, `verification_strategy`, `syntax_risk_checks`, `open_questions`, and `ready_for_generation`.",
                "If confirmation data is incomplete, put the blocker in `open_questions` and keep `ready_for_generation` false.",
            ],
        )
    if stage == "python":
        return (
            "Executable Python reference model generation",
            "Create an executable Python model that serves as the golden reference for testbench comparison.",
            [
                "Generate deterministic Python functions/classes for every subfunction.",
                "Expose `run_tests()` and a command-line entry that exits 0 on PASS and nonzero on FAIL.",
                "Expose `run_case(case)` that accepts one reference-vector case object and returns normalized outputs as plain JSON-compatible Python values.",
                "Expose optional `collect_checkpoints(case)` that returns JSON-compatible intermediate observables for localization.",
                f"Include deterministic `REFERENCE_VECTORS` and write the same cases to `model/{spec['name']}_vectors.json` when requested by the manifest.",
                "Avoid external dependencies and random behavior unless seeded deterministically.",
                *common,
            ],
        )
    if stage == "rtl":
        return (
            "RTL implementation generation",
            "Create synthesizable Verilog-2001 artifacts using the reference behavior as the semantic contract.",
            [
                f"Treat `model/{spec['name']}_model.py` as the prior-stage reference artifact and mirror its observable behavior.",
                "Implement the top module and submodule structure with explicit ports and reset behavior.",
                "Honor the confirmed pipeline requirement. If pipeline_required is true, do not emit a non-pipelined design.",
                "Generate a self-checking Verilog testbench with explicit PASS/FAIL behavior that mirrors the Python reference model's verification vectors.",
                f"The testbench must emit one machine-readable transcript line per case using the prefix `{REFERENCE_RESULT_TAG}` followed by one JSON object with `case_id`, `status`, `outputs`, and optional `checkpoints`.",
                *_vector_contract_rules(vector_contract),
                "Prefer standardized buses when choosing interfaces: AXI-Stream for streaming data, AXI4-Lite for control/status registers, AXI4 for memory-mapped bulk transfers, and AHB/APB when the platform requires them.",
                "When a design cannot naturally use a standard memory/control bus but still needs interface unification, extend AXI-Stream with explicit sideband metadata in interface_profile.",
                "Use complete combinational assignments with safe defaults before if/case decisions to avoid unintended latches.",
                "Use explicit default branches in case/casex/casez statements unless the spec proves a complete one-hot or binary decode.",
                "Do not create raw gated clocks with logic operators; use clock-enable RTL unless an approved clock-gating wrapper is explicitly specified.",
                "Document CDC and reset assumptions in manifest checks when more than one clock/reset domain or asynchronous reset behavior is present.",
                "Keep datapath and control structure timing-reviewable by naming pipeline registers, avoiding hidden feedback, and keeping high-fanout enables visible.",
                "Avoid Verilog function/task blocks in generated Verilog, especially synthesizable RTL; prefer explicit always/assign logic and inline testbench checks for easier waveform debugging.",
                "Keep simulation-only constructs out of RTL source files; they are allowed only in testbench files.",
                "Make outputs suitable for static, compile, execute, and implement readiness validation.",
                *_rtl_style_rules(spec, comment_language),
                *_comment_rules_for("rtl", comment_language),
                *common,
            ],
        )
    titles = {
        "summarize": "Specification evidence summarization",
        "decompose": "Subfunction decomposition planning",
        "augment": "Information dictionary augmentation",
        "review": "Plan verifier review",
        "tests": "Semantic test oracle generation",
        "pseudocode": "Pseudocode reference behavior generation",
    }
    return (
        titles.get(stage, "Verilog planning stage"),
        "Prepare evidence-backed planning artifacts for Verilog generation.",
        common,
    )


def _stage_manifest_for(spec: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage == "requirements":
        files = [{"path": f"plan/{spec['name']}_requirements.json", "kind": "requirements", "language": "json"}]
    elif stage == "codegen_plan":
        files = [{"path": f"plan/{spec['name']}_codegen_plan.json", "kind": "codegen_plan", "language": "json"}]
    elif stage == "python":
        files = [
            {"path": f"model/{spec['name']}_model.py", "kind": "reference_model", "language": "python"},
            {"path": f"model/{spec['name']}_vectors.json", "kind": "reference_vectors", "language": "json"},
        ]
    elif stage == "tests":
        files = [{"path": f"plan/{spec['name']}_test_plan.json", "kind": "test_plan", "language": "json"}]
    elif stage == "decompose":
        files = [{"path": f"plan/{spec['name']}_decomposition.json", "kind": "decomposition", "language": "json"}]
    elif stage == "augment":
        files = [{"path": f"plan/{spec['name']}_information_dictionary.json", "kind": "information_dictionary", "language": "json"}]
    elif stage == "review":
        files = [{"path": f"review/{spec['name']}_plan_review.md", "kind": "plan_review", "language": "markdown"}]
    elif stage == "pseudocode":
        files = [{"path": f"plan/{spec['name']}_pseudocode.md", "kind": "pseudocode", "language": "markdown"}]
    else:
        files = [
            {
                "path": output["path"],
                "kind": output.get("kind", "source"),
                "language": output.get("language", _language_from_path(output["path"])),
            }
            for output in spec["outputs"]
        ]
    return {
        "target": "rtl",
        "name": spec["name"],
        "stage": stage,
        "top": spec["name"],
        "files": files,
        "checks": _checks_template(),
    }


def _manifest_for(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": "rtl",
        "name": spec["name"],
        "top": spec["name"],
        "files": [
            {
                "path": output["path"],
                "kind": output.get("kind", "source"),
                "language": output.get("language", _language_from_path(output["path"])),
            }
            for output in spec["outputs"]
        ],
        "checks": _checks_template(),
    }


def _checks_template() -> dict[str, list[str]]:
    return {
        "spec_coverage": [],
        "verification_plan": [],
        "execution_plan": [],
        "implementation_assessment": [],
        "reviewability_assessment": [],
        "assumptions": [],
        "known_limitations": [],
    }


def _comment_rules_for(target: str, comment_language: str) -> list[str]:
    del target
    if comment_language == "zh":
        language_rule = "Use Chinese comments by default; signal names, protocol names, tool names, and identifiers may remain in English."
        rtl_labels = "`状态寄存器`, `次态逻辑`, and `输出逻辑`"
    else:
        language_rule = "Use English comments only; do not use Chinese prose in generated comments."
        rtl_labels = "`State register`, `Next-state logic`, and `Output logic`"
    return [
        language_rule,
        "Make the RTL reviewable: every port signal definition, parameter/localparam definition, wire/reg signal definition, always block, assign statement, state machine definition, and module instantiation must have an adjacent explanatory comment.",
        f"If the RTL uses an FSM, it must use a three-block FSM style with fixed comment labels {rtl_labels}.",
        "Use the manifest `checks.reviewability_assessment` field to summarize comment coverage, FSM structure, and any reviewability limitation.",
    ]


def _rtl_style_rules(spec: dict[str, Any], comment_language: str) -> list[str]:
    if str(spec.get("rtl_style_profile") or "").lower() != "erie_strict":
        return []
    inline_language = "Chinese" if comment_language == "zh" else "English"
    return [
        "Apply the `erie_strict` RTL style profile as a hard generation constraint.",
        "Use Tab characters for all RTL indentation; do not use four-space indentation for code blocks.",
        "Use a fixed bilingual header template: preserve both the English and Chinese header sections in the generated RTL source.",
        f"Use `{inline_language}` as the default language for inline explanatory prose outside the fixed bilingual template header.",
        "Use low-active reset naming and conventions such as `i_rstn`, `i_axi_arstn`, `i_axis_arstn`, `i_ahb_hrstn`, and `i_apb_prstn` according to the bus type.",
        "Use clock naming conventions such as `i_clk`, `i_axi_aclk`, `i_axis_aclk`, `i_ahb_hclk`, and `i_apb_pclk` according to the bus type.",
        "Never declare module ports with `reg` or `wire` keywords; drive outputs through internal `_o` signals plus explicit `assign` statements.",
        "Use `i_` for input ports, `o_` for output ports, `io_` for bidirectional ports, and group port declarations into annotated interface regions.",
        "Use `C_` uppercase names for module parameters, `ST_` uppercase names for state parameters, and uppercase names for other localparams.",
        "Use the required signal prefixes: `reg_`, `cnt_`, `state_`, `flag_`, `enc_`, `dec_`, and the `_o` suffix for internal output logic signals. Do not duplicate prefixes.",
        "Split sequential logic so that each `always` block assigns exactly one reg signal.",
        "Do not use `wire xxx = ...;`; declare the wire first and use a separate `assign` statement.",
        "Follow the exact 18-region source order: parameters, state parameters, instantiation signals, counters, state signals, regs, flags, encoders, decoders, other signals, output signals, other assigns, output assigns, output processing, FSM, state transition processing, main datapath processing, module instantiation.",
        "Place all module instantiations in the final source region, after every assign and always block.",
        "If an FSM is present, implement a three-block state machine that matches the template structure and comment labels exactly.",
    ]


def _design_requirements_context(spec: dict[str, Any]) -> dict[str, Any]:
    interface_template = _interface_template_context(spec)
    return {
        "design_requirements": spec.get("design_requirements", {}),
        "pipeline_required": spec.get("pipeline_required"),
        "streamability": spec.get("streamability"),
        "interface_family": spec.get("interface_family"),
        "interface_profile": spec.get("interface_profile", {}),
        "selected_interface_template_id": interface_template.get("template_id") if interface_template else None,
        "rtl_dialect": "verilog",
        "rtl_style_profile": spec.get("rtl_style_profile"),
    }


def _interface_template_context(spec: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return select_interface_template(spec)
    except InterfaceTemplateError as exc:
        return {
            "template_id": None,
            "interface_family": spec.get("interface_family"),
            "selection_error": str(exc),
            "content": "",
        }


def _format_interface_template_section(interface_template: dict[str, Any] | None) -> str:
    if not interface_template:
        return ""
    if interface_template.get("selection_error"):
        return (
            "## Interface template\n\n"
            f"Local interface template selection failed: {interface_template['selection_error']}\n"
            "Do not generate RTL until the interface_profile.template_id, role, or read_write_mode is corrected.\n"
        )
    content = str(interface_template.get("content") or "").rstrip()
    if not content:
        return ""
    metadata = {
        "template_id": interface_template.get("template_id"),
        "interface_family": interface_template.get("interface_family"),
        "role": interface_template.get("role"),
        "read_write_mode": interface_template.get("read_write_mode"),
        "path": interface_template.get("path"),
        "selection_reason": interface_template.get("selection_reason"),
        "strict_naming_policy": interface_template.get("strict_naming_policy"),
        "clock": interface_template.get("clock"),
        "reset": interface_template.get("reset"),
        "parameters": interface_template.get("parameters", []),
    }
    metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False)
    return f"""## Interface template

Use this local standard interface template as the preferred port contract for the selected bus. Preserve these signal names, parameter names, and Chinese comments unless the spec explicitly conflicts with an existing confirmed port list; record any adaptation in the manifest reviewability checks and codegen plan. If `read_write_mode` is `read` or `write`, use this full template as the naming reference and generate only the confirmed direction's required logic.

```json
{metadata_json}
```

```verilog
{content}
```
"""


def _vector_contract_rules(vector_contract: dict[str, Any] | None) -> list[str]:
    if not vector_contract:
        return []
    return [
        f"Mirror the reference vector contract exactly: case_count={vector_contract.get('case_count')}, case_ids={vector_contract.get('case_ids')}.",
        f"Every generated testbench must include an adjacent comment `{VECTOR_HASH_TAG} {vector_contract.get('sha256')}` and use the same case ids.",
    ]


def _append_optional_sections(
    prompt: str,
    *,
    decision: dict[str, Any] | None = None,
    **_: Any,
) -> str:
    if not decision:
        return prompt
    return prompt + "\n## Human decision constraints\n\n```json\n" + json.dumps(decision, indent=2, ensure_ascii=False) + "\n```\n"


def _language_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".v":
        return "verilog"
    if suffix == ".py":
        return "python"
    if suffix == ".json":
        return "json"
    if suffix == ".md":
        return "markdown"
    return "text"


def _scope_plan(plan: dict[str, Any], subfunction: str | None) -> dict[str, Any]:
    if not subfunction:
        return plan
    scoped = dict(plan)
    scoped["subfunctions"] = [
        item for item in plan.get("subfunctions", [])
        if isinstance(item, dict) and item.get("name") == subfunction
    ]
    return scoped


def _artifact_context(
    manifest: dict[str, Any] | None,
    context_dir: Path | None,
    *,
    budget: str = "normal",
) -> dict[str, Any]:
    if not manifest or not context_dir:
        return {}
    files: list[dict[str, Any]] = []
    limit = 1200 if budget == "compact" else 5000
    for entry in manifest.get("files", []) or []:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        path = context_dir / str(entry["path"])
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            files.append({"path": entry["path"], "kind": entry.get("kind"), "text": text[:limit]})
    return {"files": files}


def _evidence_context(evidence: dict[str, Any] | None, plan: dict[str, Any], budget: str) -> dict[str, Any]:
    del plan
    if not evidence:
        return {}
    limit = 6 if budget == "compact" else 16
    items = evidence.get("items", []) if isinstance(evidence, dict) else []
    return {"items": items[:limit]}


def _memory_constraints(
    memory: dict[str, Any] | None,
    stage: str,
    *,
    subfunction: str | None,
    budget: str,
) -> dict[str, Any]:
    del stage, subfunction, budget
    if not memory:
        return {}
    return memory
