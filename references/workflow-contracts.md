# Workflow Contracts

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

## Fixed Pipeline

The Verilog-only workflow uses:

1. `requirements`
2. `codegen_plan`
3. `python`
4. `rtl`

The workflow does not enter prompt-driven code generation until the confirmed requirement contract is complete. If planning finds unresolved requirements, it stops with `blocked_human`.

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
