# Workflow Contracts

## Table of Contents

- [Run Directory](#run-directory)
- [Entry Routing](#entry-routing)
- [Fixed Generation Pipeline](#fixed-generation-pipeline)
- [Existing RTL Assist Flows](#existing-rtl-assist-flows)
- [Terminal Statuses](#terminal-statuses)
- [Resume Behavior](#resume-behavior)
- [Optional Tools](#optional-tools)
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
- `risk_flags`
- `provenance_policy`

Entry modes are limited to:

- `spec-first generation`
- `plan-seeded generation`
- `existing-RTL assist/repair`
- `evidence-first debug/repair`

`plan-seeded generation` only means an existing codegen plan can seed the workflow. It does not bypass requirement confirmation, Verilog-2001 target checks, Python reference generation, extraction, validation, or downstream quality gates.

Route summaries may be copied into `workflow_config.json` and `workflow_result.json` as evidence. They are advisory and must not change `WORKFLOW_STATUSES`.

## Fixed Generation Pipeline

The staged generation workflow uses:

1. `requirements`
2. `codegen_plan`
3. `python`
4. `rtl`

The workflow does not enter prompt-driven code generation until the confirmed requirement contract is complete. If planning finds unresolved requirements, it stops with `blocked_human`.

## Existing RTL Assist Flows

Existing-RTL helper flows do not use the staged generation pipeline. They are separate stable subflows exposed through the facade and CLI:

- `analyze_existing_verilog(...)` writes `rtl_analysis.json` and `project_analysis.json`
- `analyze_existing_verilog(...)` also writes `design_explanation.md`
- `refine_existing_verilog(..., refine_goal="tb_scaffold"|"style_refine"|"partition_assist"|"merge_assist"|"optimize_assist")` writes `rtl_transform_plan.json`, `transform_validation.json`, and goal-specific helper artifacts
- `compare_verilog_semantics(...)` writes `equivalence.json`, `qor_report.json`, and `transform_validation.json`
- `verify_existing_verilog(...)` writes `verification_plan.json`, `tb_contract.json`, `log_diagnosis.json`, `patch_candidate.json`, `verification_result.json`, and `loop_state.json`
  The same run also writes `simulation_slice.json`, `timing_diagnostic.json`, `expected_trace.md`, `waveform_diff.json`, `testcase_matrix.json`, `run_summary.json`, `synth_readiness.json`, and `terminal_status.json`.

`optimize_assist` is assist-only by default. Without a candidate RTL, it produces optimization plans, wrapper/probe artifacts, partition maps, and advisory QoR summaries. With a candidate RTL, it additionally emits semantic-compare evidence. It does not implicitly rewrite or accept RTL.

`merge_assist` is assist-only by default. It produces a merge plan, wrapper skeleton, validation summary, and equivalence-review contract so repartition or recompose work remains explicit and reviewable.

`verify_existing_verilog(...)` is a verification loop entrypoint rather than a fresh RTL generator. It stages source RTL into a project-local verification workspace, emits a log-driven scaffold testbench or augments an existing one, normalizes diagnosis results, and records the selected automation boundary. The caller must provide the automation mode explicitly.

Existing-RTL verify-repair reports may add `diagnosis_route` to `run_summary.json` and `terminal_status.json`. The allowed values are `local_rtl_issue`, `spec_ambiguity`, `dut_tb_contract_drift`, `toolchain_issue`, `needs_external_validation`, and `unknown_or_mixed`. This field is an advisory routing summary and must not change terminal success semantics.

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
reported tool actually ran. Use `--no-external` only when the caller intentionally wants static
validation without local tool execution.

## Trace Semantics

`trace.jsonl` is append-only and records prompt rendering, model generation, extraction, validation, interface/reference audits, verifier gates, reflection, and human intervention markers.
