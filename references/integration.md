# Integration Guide

## Facade

Use the Verilog-only facade:

```python
from integration.verilog_adapter import (
    analyze_existing_verilog,
    compare_verilog_semantics,
    refine_existing_verilog,
    verify_existing_verilog,
    run_verilog_workflow,
    render_verilog_prompt,
    validate_verilog_artifacts,
)
```

Use `run_verilog_workflow(...)` for full staged execution and resume. Use `render_verilog_prompt(...)` when the host already owns the model call. Use `validate_verilog_artifacts(...)` to gate generated `.v` files before downstream use. Use `analyze_existing_verilog(...)` to build `rtl_analysis.json`, `project_analysis.json`, and `design_explanation.md` from an existing design, `refine_existing_verilog(...)` to create `rtl_transform_plan.json` plus controlled helper artifacts, `compare_verilog_semantics(...)` to emit `equivalence.json`, `qor_report.json`, and `transform_validation.json`, and `verify_existing_verilog(...)` to run the log-driven verify-repair flow with stable run artifacts plus a diagnostics pack.

## Required Inputs

Specs keep `target="rtl"` for compatibility. Calls may omit `target`; the facade resolves it to `rtl`. Any other target is rejected before prompt generation or workflow execution.

Existing RTL analysis/refinement flows do not require a generation spec. They operate on existing `.v` files, optionally accept a Markdown/text behavioral note source, and always write stable JSON evidence in the caller-selected output directory.

`verify_existing_verilog(...)` requires an explicit `automation_mode`. The host must ask or otherwise surface that choice; the runtime must not silently default the mode. Supported modes are:

- `conservative`: report findings only
- `semi_auto`: generate candidate artifacts but require confirmation before source overwrite
- `auto_apply`: caller explicitly allows automatic source mutation when the workflow has safe evidence

When `tb_mode="augment"`, the host may pass `testbench_source` explicitly. If omitted, the runtime may fall back to auto-detecting a TB from the provided source list, but explicit `testbench_source` always wins.
When resuming an RTL repair confirmation, the host may pass `decision_source` so the same `verify_existing_verilog(...)` entrypoint applies the approved patch and immediately runs post-apply verification.

`refine_existing_verilog(..., refine_goal="merge_assist")` is also assist-only. It emits `merge_plan.json`, `merge_wrapper.v`, `merge_validation.json`, and `merge_equivalence.json` so a host can guide wrapper-first repartition or recompose work without automatically mutating the source RTL.

`refine_existing_verilog(..., refine_goal="optimize_assist")` is an assist-only flow. Without a candidate RTL, it produces an optimization plan, wrapper/probe artifacts, partition maps, and advisory QoR summaries. With a candidate RTL, it additionally runs semantic compare reporting. It does not implicitly rewrite or accept a candidate design.

`verify_existing_verilog(...)` keeps RTL generation scope unchanged: source RTL remains Verilog-2001. The verification testbench may be `verilog` or `systemverilog` depending on the selected `tb_language`, but this does not widen the design RTL boundary. In augment mode, the runtime preserves the original TB body, emits an augment plan and diff artifact, and records original/backup/active TB paths in `tb_contract.json`. In RTL repair mode, the runtime emits `rtl_patch_plan.json`, `rtl_patch_diff.txt`, `rtl_intervention.json`, and post-apply verification artifacts while keeping backup/active RTL paths inside `patch_candidate.json` and `verification_result.json`. Every run also emits `simulation_slice.json`, `timing_diagnostic.json`, `expected_trace.md`, `waveform_diff.json`, `testcase_matrix.json`, `run_summary.json`, `synth_readiness.json`, and `terminal_status.json` so hosts can consume structured closure state instead of scraping logs. The patch planner now classifies low-risk repair candidates such as `reset_initialization_completion`, `case_default_completion`, `state_hold_clear_completion`, and `output_register_completion`; only reset-initialization completion remains eligible for immediate `auto_apply`, while the newer categories intentionally downgrade to confirmation-driven resume.

Before generation, provide a confirmed requirement contract:

- `design_requirements.target = "rtl"`
- `design_requirements.pipeline_required`
- `design_requirements.streamability`
- `design_requirements.interface_family`
- `design_requirements.interface_profile`
- `design_requirements.confirmed_by_user = true`
- `design_requirements.confirmation_notes`

When the task is streamable, explicitly choose `native`, `axi_stream`, `axi4`, `axi4_lite`, `ahb`, `apb`, or `custom`. Do not let the runtime infer a bus protocol from vague wording when the user has already expressed a bus preference.

`interface_profile.template_id` is optional. If omitted, the runtime selects a local template from `assets/interface_templates/catalog.json` for standard bus families and records it as `selected_interface_template_id` in the requirements payload and codegen plan. Supported template IDs are `axi_stream_duplex`, `axi4_lite_config`, `axi4_full_master`, `axi4_full_slave`, `ahb_lite_config`, and `apb_config`. Treat these snippets as strict-preferred port contracts, not standalone synthesizable modules.

`workflow.use_case_template_id` is optional. When provided, it must be one of `spi_adc`, `spi_dac`, `jesd_adc`, `jesd_dac`, or `mxfe_mixed`. The runtime resolves the matching bundle from `assets/use_case_templates/`, records it as `selected_use_case_template_id` plus `use_case_template` in the requirements payload and codegen plan, and injects the selected Verilog or Tcl or XDC guidance into prompts as board-level context.

When `rtl_style_profile=erie_strict`, the runtime also layers in ref-derived Erie style constraints from `references/erie-ref-style.md` and `assets/style_templates/`. Hosts should expect stronger prompt guidance around bilingual headers, FSM naming, instance naming, generate labels, and grouped standard-bus port declarations.

## Run Directories

When dict inputs are passed to the workflow runner, the facade materializes stable inputs under `<out_dir>/_adapter_inputs/`:

- `spec.json`
- `requirements.json`
- `codegen_plan.json`
- optional `evidence.json`
- optional `decision.json`

The host can inspect these files even when the workflow later blocks for a human decision.

## Host Profile

In GUI-hosted Code Design sessions, the GUI owns artifact writing, formatter/parser checks, server staging, server-side Vivado admission, and final project insertion. The model turn should return host artifacts rather than running the standalone workflow unless the host explicitly asks for facade operations.

Local external tools are optional diagnostics unless the host explicitly requests compile,
execute, or implement readiness. If requested tools are missing, report the missing tools and
block the standalone readiness gate instead of claiming external validation succeeded.

## Boundary

Keep host-specific behavior in `integration/`. Avoid patching `runtime/verilog_generator/` unless intentionally changing the shared Verilog workflow.
