# Workflow Contracts

## Table of Contents

- [Run Directory](#run-directory)
- [Entry Routing](#entry-routing)
- [Script Entry Points](#script-entry-points)
- [Model Providers](#model-providers)
- [Fixed Generation Pipeline](#fixed-generation-pipeline)
- [Delivery Gate Matrix](#delivery-gate-matrix)
- [Existing RTL Assist Flows](#existing-rtl-assist-flows)
- [Terminal Statuses](#terminal-statuses)
- [Resume Behavior](#resume-behavior)
- [Optional Tools](#optional-tools)
- [Language Governance](#language-governance)
- [Trace Semantics](#trace-semantics)

## Run Directory

Every `run_verilog_workflow(...)` execution writes a self-contained run directory with:

- `plan.json`
- `workflow_config.json`
- `workflow_result.json`
- `workflow-state.json`
- `trace.jsonl`

The adapter also materializes preflight inputs under `_adapter_inputs/`:

- `spec.json`
- `requirements.json`
- `codegen_plan.json`
- optional `evidence.json`
- optional `decision.json`

Each attempt lives under `attempt-001/`, `attempt-002/`, and so on. Stage outputs are separated by stage, for example `python/generated/...`, `rtl/generated/...`, `validation.json`, `repair_plan.json`, and `intervention.json`.

## Entry Routing

`route_verilog_entry(...)` is a read-only classifier. It may inspect caller-provided specs, codegen plans, RTL paths, testbench paths, logs, validation summaries, waveform clues, or an artifact directory, but it must not write source artifacts, start model calls, run validation, create backups, or trigger remote commands.

The stable route decision fields are:

- `recommended_flow`
- `entry_mode`
- `required_inputs`
- `missing_inputs`
- `next_action`
- `safe_recovery_hint`
- `blocking_findings`
- `provenance_policy`

Entry modes are limited to:

- `spec-first generation`
- `plan-seeded generation`
- `existing-RTL assist/repair`
- `evidence-first debug/repair`

`plan-seeded generation` only means an existing codegen plan can seed the workflow. It does not bypass requirement confirmation, Verilog-2001 target checks, Python semantic generation, extraction, validation, or downstream quality gates.

Route summaries may be copied into `workflow_config.json` and `workflow_result.json` as evidence. They are advisory and must not change `WORKFLOW_STATUSES`.

## Script Entry Points

Script implementations live under `scripts/python/<function>/`. The supported function buckets are `validation`, `quality`, `generation`, `remote`, `toolchain`, and `corpus`.

The old top-level script wrappers remain v0.3.x compatibility wrappers. They must forward to the canonical implementation while preserving CLI arguments, stdout/stderr behavior, and exit codes. New workflow documentation and tests should prefer the canonical paths.

Canonical examples:

- `scripts/python/validation/validate_verilog_skill.py`
- `scripts/python/remote/remote_validate_verilog_skill.py`
- `python -m scripts.python.quality.verilog_lint`
- `python -m scripts.python.generation.tb_generator`
- `python -m scripts.python.validation.validate_verilog_skill`
- `python -m scripts.python.remote.remote_validate_verilog_skill`

Runtime modules may call `runtime` and `scripts/python/*` helpers as needed, but runtime modules must not import the legacy host integration facade. The public Python facade lives at `scripts/python/facade/verilog_api.py`.

## Model Providers

`run-workflow` and `run-batch` accept `--model-provider mock|manual|command`; the default is `manual`. `mock` is deterministic test support. `manual` reads the response file already staged for the current generation step and does not call a model. `command` is the real non-interactive provider and requires `--model-command`; omitting that command fails closed with a non-zero exit instead of falling back to `mock` or `manual`.

Use `--model-timeout` to bound command-provider execution. `--stream` and `--no-stream` select streamed or buffered consumption when the provider supports it. Provider selection never relaxes requirement confirmation, extraction, validation, retry, or delivery gates.

## Fixed Generation Pipeline

The staged generation workflow uses:

1. `requirements`
2. `codegen_plan`
3. `python`
4. `rtl`

The workflow does not enter prompt-driven code generation until the confirmed requirement contract is complete. If planning finds unresolved requirements, it stops with `blocked_human`.

`deep_review` inserts a `review` stage between `python` and `rtl`. The stage must write a non-empty `*plan_review.md` artifact that covers interface, reset, timing/pipeline, handshake or FSM behavior, width, synthesis, testbench coverage, and risk. Empty JSON, placeholder text, or missing coverage keeps the attempt failed.

## Delivery Gate Matrix

`run_verilog_deliverable_gate(...)` returns the public delivery contract. The stable top-level fields are:

- `delivery_ready`
- `repair_required`
- `rerun_required_after_repair`
- `delivery_issues_by_rule`
- `checks`

The `checks` object must contain exactly these eight public gates:

- `compile`: local static syntax, parser, and basic lint evidence. It does not mean xsim, VCS, iverilog, or any external simulator compile ran.
- `ast`: formatter AST coverage, parse-error evidence, and formatter-template consistency. Any non-empty `formatter_violations` count fails this gate and contributes to top-level `errors`.
- `readability`: Verilog readable quality gate evidence.
- `comment`: semantic comment placement and comment coverage evidence.
- `naming`: naming and prefix-related rule evidence.
- `profile`: style profile, rulebook, wrapper profile, and project profile evidence.
- `testbench`: testbench static/readability evidence when explicitly included; otherwise `not_requested`.
- `toolchain`: optional simulator, synthesis, or remote validation evidence. Local deliverable gate reports this as `not_requested` unless validation readiness supplied external evidence.

In strict mode, any blocker or warning in a delivery-relevant gate means:

- `delivery_ready=false`
- `repair_required=true`
- `rerun_required_after_repair=true`
- a non-zero CLI exit code

Repair is complete only when the Verilog is readable and syntax/AST checks pass. For workflow execution, the attempt may be marked `passed` only when validation is OK, stage gates are ready, and all eight delivery matrix entries are ready. A `toolchain_issue` remains an external environment blocker; it must not consume local repair attempts, but the workflow also must not claim simulation, synthesis, or remote validation passed.

## Existing RTL Assist Flows

Existing-RTL helper flows do not use the staged generation pipeline. They are separate stable subflows exposed through the facade and CLI:

- `analyze_existing_verilog(...)` writes `rtl_analysis.json` and `project_analysis.json`
- `analyze_existing_verilog(...)` also writes `design_explanation.md`
- `improve_existing_verilog(..., improve_goal="tb_scaffold"|"style_improve"|"partition_assist"|"merge_assist"|"optimize_assist")` writes `rtl_transform_plan.json`, `transform_validation.json`, and goal-specific helper artifacts
- `compare_verilog_semantics(...)` writes `equivalence.json`, `qor_report.json`, and `transform_validation.json`
- `verify_existing_verilog(...)` writes `verification_plan.json`, `tb_contract.json`, `log_diagnosis.json`, `patch_candidate.json`, `verification_result.json`, and `loop_state.json`
  The same run also writes `simulation_slice.json`, `timing_diagnostic.json`, `expected_trace.md`, `waveform_diff.json`, `testcase_matrix.json`, `run_summary.json`, `synth_readiness.json`, and `terminal_status.json`.

`optimize_assist` is assist-only by default. Without a candidate RTL, it produces optimization plans, wrapper/probe artifacts, partition maps, and advisory QoR summaries. With a candidate RTL, it additionally emits semantic-compare evidence. It does not implicitly rewrite or accept RTL.

`merge_assist` is assist-only by default. It produces a merge plan, wrapper skeleton, validation summary, and equivalence-review contract so repartition or recompose work remains explicit and reviewable.

`verify_existing_verilog(...)` is a verification loop entrypoint rather than a fresh RTL generator. It stages source RTL into a project-local verification workspace, emits a log-driven scaffold testbench or augments an existing one, normalizes diagnosis results, and records the selected automation boundary. The caller must provide the automation mode explicitly. Existing source RTL uses diagnostic comment gates: comment-density and placement failures are warnings so compile, semantic, interface, and high-confidence lint errors stay visible.

Existing RTL, target, and context modes require real caller-provided assets. Missing source RTL, missing target RTL, or missing contextual evidence must fail with `TARGET_OR_CODE_REQUIRED` or the nearest equivalent rule. The skill must not invent placeholder RTL, replacement assets, or directly replaceable template code for comment/report-only requests. Comment and report flows may locate issues and describe findings, but generated code patches require explicit modify/repair intent and real target files.

Existing-RTL verify-repair reports may add `diagnosis_route` to `run_summary.json` and `terminal_status.json`. The allowed values are `local_rtl_issue`, `spec_ambiguity`, `dut_tb_contract_drift`, `toolchain_issue`, `needs_external_validation`, and `unknown_or_mixed`. This field is an advisory routing summary and must not change terminal success semantics.

Existing RTL CLI commands use strict exit semantics. If a generated report contains a blocker, warning, failed status, or a linked terminal summary with `toolchain_issue`, `needs_external_validation`, `blocked`, `failed`, or equivalent failure routing, the command returns non-zero. A report being written successfully is not the same as command success.

For `tb_mode="augment"`, the run directory also writes:

- `tb_augment_plan.json`
- `tb_augment_diff.txt`

`tb_contract.json` records `original_testbench_path`, `backup_testbench_path`, `active_testbench_path`, `language_before`, `language_after`, and `augmentation_actions`.

When an RTL patch candidate is available, the same run directory also writes:

- `rtl_patch_plan.json`
- `rtl_patch_diff.txt`
- `rtl_intervention.json` when confirmation is required
- `post_apply_validation.json` after an approved or automatic apply
- `post_apply_equivalence.json` after an approved or automatic apply

`patch_candidate.json` records candidate/backup/active RTL paths, compare evidence, equivalence readiness, apply blockers, patch category, line hints, and root-cause evidence. `rtl_patch_plan.json` records the selected patch category plus `risk_level`, `target_line_hints`, `root_cause_evidence`, and `apply_gate` details so the caller can distinguish true `auto_apply` cases from categories that must be resumed through `decision.json`. `decision.json` may be supplied on a later `verify-existing` run to resume a confirmation-gated RTL apply.

## Terminal Statuses

`workflow_result.json` only uses:

- `passed`
- `failed`
- `blocked_human`
- `blocked_toolchain`
- `max_attempts`
- `invalid_response`

Unsupported targets are rejected as input errors before the workflow starts.

## Resume Behavior

When the workflow stops at `blocked_human`, it writes `intervention.json`. A host can later supply `decision.json` and resume through the facade or CLI. Resume appends a new attempt and preserves trace history.

## Optional Tools

The workflow may call these environment-provided tools when readiness requires them:

- Vivado xsim backend: `xvlog`, `xelab`, `xsim`
- VCS+Verdi backend: `vcs`, `verdi`
- iverilog backend: `iverilog`, `vvp`
- Implementation readiness: `yosys`

VCS+Verdi coverage in this skill means scripted backend selection, availability checks, compile, and simulation execution. It does not imply complete Verdi GUI/session automation, waveform-debug orchestration, or arbitrary Verdi feature coverage.

Simulation backend selection uses the configured fallback order: xsim, then VCS+Verdi, then
iverilog/vvp. Missing higher-priority simulators are recorded in validation metrics and warnings,
but they do not block if a lower-priority backend actually runs. If no simulator backend is
available, validation reports a `toolchain_issue` error and workflow execution stops at
`blocked_toolchain`. Do not claim compile, execute, or implementation validation unless the
reported tool actually ran. Use `--no-external` only for static readiness; if a caller asks for
compile, execute, or implement readiness with external execution disabled, validation reports a
`toolchain_issue` error and separates `static_passed`, `compile_not_run`, `sim_not_run`, and
remote-required status in metrics.

That fallback order is a general compatibility capability, not this repository's release acceptance
matrix. The current release gate is fixed to `server_1` with xsim; VCS+Verdi remains an optional
compatible backend and is not a required release condition.

Verification testbenches remain Verilog-2001 `.v` artifacts. Source RTL and generated
verification files use the same Verilog-only boundary.

## Language Governance

Any implementation change that creates, edits, reviews, refactors, or repairs Python must first use `readable-python-generator`. The agent must classify the task as generate/modify/explain/review/repair, state the functional intent contract, follow the current-project Chinese style, and pass the readable-python strict gates before claiming VerilogGenerator readiness.

Any implementation change that creates, edits, reviews, refactors, or repairs bat/cmd, shell/bash, PowerShell, or Tcl must first use `readable-script-generator`. The agent must identify the script language, classify the task, state the script intent contract, and pass the readable-script strict gates before claiming VerilogGenerator readiness. Comment-only script changes must prove executable source order and AST/CST equivalence when the language tooling supports it.

Mixed Python and script-family changes must run both governance paths. Installed skill contents under Codex home remain read-only unless the user explicitly requests installation or replacement.

## Trace Semantics

`trace.jsonl` is append-only and records prompt rendering, model generation, extraction, validation, interface/reference audits, verifier gates, reflection, and human intervention markers.
