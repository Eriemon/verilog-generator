# Integration Guide

## Facade

Use the Verilog-only facade:

```python
from integration.verilog_adapter import (
    run_verilog_workflow,
    render_verilog_prompt,
    validate_verilog_artifacts,
)
```

Use `run_verilog_workflow(...)` for full staged execution and resume. Use `render_verilog_prompt(...)` when the host already owns the model call. Use `validate_verilog_artifacts(...)` to gate generated `.v` files before downstream use.

## Required Inputs

Specs keep `target="rtl"` for compatibility. Calls may omit `target`; the facade resolves it to `rtl`. Any other target is rejected before prompt generation or workflow execution.

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
