# Capability Distillation Checklist

This checklist records which external capability families were distilled into
the current skill. It is a release-time review artifact only. It is not a
runtime dependency and it must remain valid after the temporary comparison
workspace is deleted.

## Reference Families

| Family label | Distilled capability | Current skill surface | Evidence |
| --- | --- | --- | --- |
| `family_interactive_repair` | Log driven verify and repair loop | `verify_existing_verilog(...)`, diagnostics pack, `run_summary.json`, `terminal_status.json` | unit tests, smoke, eval |
| `family_structural_assist` | Spec to pin mapping, testbench generation, partition and merge assist | `analyze_existing_verilog(...)`, `tb_scaffold`, `partition_assist`, `merge_assist` | unit tests, smoke, eval |
| `family_staged_rtl_flow` | Staged orchestration, debug discipline, testbench and style refinement | staged workflow, `tb_scaffold`, `style_refine`, static lint, diagnostics gates | unit tests, smoke, eval |
| `family_timing_diagnosis` | Golden model timing semantics, checkpoint aware comparison, waveform and timing diagnosis | staged workflow, semantic checkpoints, `timing_diagnostic.json`, `waveform_diff.json`, `testcase_matrix.json`, `expected_trace.md` | unit tests, smoke, eval, remote gate |

## Included Capabilities

| Capability | Current status | Evidence chain |
| --- | --- | --- |
| `design_explanation.md` | Included | tests, smoke |
| `tb_scaffold` | Included | tests, smoke, eval |
| `style_refine` | Included | tests, smoke, eval |
| `partition_assist` | Included | tests, smoke, eval |
| `merge_assist` | Included | tests, smoke, eval |
| `optimize_assist` | Included | tests, smoke, eval |
| `timing_diagnostic.json` | Included | tests, smoke, eval |
| `waveform_diff.json` | Included | tests, smoke, eval |
| `testcase_matrix.json` | Included | tests, smoke, eval |
| `run_summary.json` | Included | tests, smoke, eval |
| `synth_readiness.json` | Included | tests, smoke, eval |
| `terminal_status.json` | Included | tests, smoke, eval |

## Explicit Boundary Choices

| Reference concept | Decision | Reason |
| --- | --- | --- |
| Python coroutine simulation as a mandatory path | Not included as required capability | The skill remains Verilog only by default and must not require an extra coroutine simulation stack to validate core behavior. |
| Post-processed waveform tooling as a required dependency | Not included as required capability | Timing and waveform diagnosis are preserved as generated artifacts without forcing a new mandatory tool stack. |
| Direct reuse of the temporary comparison workspace | Rejected | Release artifacts must not depend on temporary local comparison paths or local reference paths. |

## Review Rule

If a capability remains public in `SKILL.md`, at least one of tests, smoke, or
eval must exercise it directly. If that evidence chain is removed, either add a
replacement gate or remove the public capability claim.
