---
name: erie-verilog-generator
description: >-
  Use when Codex needs verilog开发, verilog设计, verilog修改, verilog debug, verilog调试, RTL开发, RTL设计, RTL修改, RTL debug, or RTL调试 for a Verilog-target design, including synthesizable Verilog-2001 RTL modules, self-checking Verilog testbenches, compile/simulation/validation with local or remote Vivado/xsim, artifact extraction, or workflow trace diagnosis.
---

# Erie Verilog Generator

Use this skill for Verilog-2001 RTL generation backed by the bundled `runtime/verilog_generator` Python workflow. Keep all generated design artifacts as Verilog `.v` files and use the stable facade in `integration/verilog_adapter.py`.

## Workflow

1. Confirm the design intent before generation: module name, ports, clock/reset, behavior, pipeline expectation, interface family, and verification cases.
2. Use the staged pipeline: `requirements -> codegen_plan -> python -> rtl`.
3. Generate a Python reference model before RTL when running the workflow; use it as the semantic contract for the Verilog testbench.
4. Generate only synthesizable Verilog-2001 RTL and self-checking Verilog testbenches.
5. Prefer standardized interfaces: AXI-Stream for streaming data, AXI4-Lite for control/status registers, AXI4 for memory-mapped bulk transfers, and AHB/APB when a platform requires them. If a custom shape still needs bus unification, extend AXI-Stream with explicit sideband metadata in `interface_profile`.
6. Use the local standard bus templates in `assets/interface_templates` whenever `interface_family` is `axi_stream`, `axi4_lite`, `axi4`, `ahb`, or `apb`. Treat their port names, parameter names, and Chinese comments as strict-preferred defaults; only adapt them when the confirmed spec explicitly conflicts, and record the adaptation reason in the generated checks.
7. Avoid Verilog `function` and `task` blocks in generated Verilog, especially synthesizable RTL; prefer explicit always/assign logic and inline testbench checks for easier waveform debugging.
8. Validate with static checks by default; when external simulation is requested, select the highest available backend in this order: Vivado xsim, VCS+Verdi, then iverilog/vvp. Use `yosys` only for implement readiness.

## Remote Vivado Fallback

When the user requests compile, simulation, execute, or implement readiness and the local host does not provide Vivado (`vivado`) or the xsim toolchain (`xvlog`, `xelab`, `xsim`), do not continue as if local Vivado validation is possible. First run the `erie-remote-ssh` discovery and choice flow:

```powershell
python <erie-remote-ssh>\scripts\remote_ssh.py discover --settings <remote-settings> --config <server-list>
python <erie-remote-ssh>\scripts\remote_ssh.py choices --settings <remote-settings> --config <server-list>
```

Ask the user to select a remote server unless they already named one in the current request. A configured default server is only a recommendation; it is not user confirmation. After selection, use `erie-remote-ssh` for `check`, `scan-software`, `workspace-check`, request creation, and `run-request --execute`. If remote discovery sees multiple Vivado `settings64.sh` candidates, stop and ask the user which version to use; persist that confirmed choice in the user-level toolchain config before development or validation continues. Remote validation directories are retained by default under `.erie-verilog-generator-validation/run-YYYYMMDDTHHMMSS/erie-verilog-generator`, including `_smoke_runs` outputs; use `--cleanup-remote` only when the user wants the validation directory deleted. The remote gate must execute the canonical workflow plus the fixed RTL fixtures in `assets/examples/remote_fixtures` and retain each fixture `validation.json`. Use `--report-runs` for a read-only summary of retained remote runs. Do not add direct `ssh` or `scp` commands to this skill.

## Host Integration

Use `integration.verilog_adapter`:

- `run_verilog_workflow(...)` for full staged execution or resume.
- `render_verilog_prompt(...)` when the host owns the model call.
- `validate_verilog_artifacts(...)` before downstream use.

For GUI-hosted Code Design sessions, return artifacts through the host artifact protocol when requested. Do not require local external tools for GUI admission; local tool checks are optional diagnostics.

## Local Commands

Run smoke validation from this skill root:

```powershell
python .\scripts\validate_verilog_skill.py --settings .\config\defaults.json
```

Record a user-confirmed remote toolchain selection in the user folder:

```powershell
python .\scripts\remote_validate_verilog_skill.py --settings .\config\defaults.json --server <server-id> --write-toolchain-selection --simulator-backend xsim --vivado-settings /tools/Xilinx/Vivado/<version>/settings64.sh
```

Run the underlying CLI from this skill root:

```powershell
python -m runtime.verilog_generator scaffold --name erie_adapter --out .\reports\verilog\spec.json
python -m runtime.verilog_generator prompt --spec .\reports\verilog\spec.json --out .\reports\verilog\prompt.md
python -m runtime.verilog_generator validate --spec .\reports\verilog\spec.json --path .\reports\verilog\generated --no-external
```

## Reference Loading

- Load `ENGINEERING_DESIGN_GOALS.md` when changing this skill's architecture or scope.
- Load `references/configuration.md` when changing paths, validation gates, remote SSH settings, or temporary run locations.
- Load `references/integration.md` when wiring the facade into another host.
- Load `references/workflow-contracts.md` when handling run directories, statuses, resume behavior, or traces.
- Use `assets/examples/rtl_erie_verilog_spec.json` as the canonical Verilog-only fixture.
- Use `assets/interface_templates/catalog.json` and the referenced `.vinc` snippets when verifying or updating standard AXI-Stream, AXI4-Lite, AXI4, AHB, or APB interface guidance.
- Use `assets/examples/remote_fixtures` when verifying real-style remote xsim coverage across combinational logic, sequential pipeline logic, and ready/valid handshakes.

## Boundaries

- Do not generate non-Verilog hardware flows, C/C++ kernels, or alternate RTL dialects.
- Do not claim external tool validation happened unless the tool actually ran.
- Do not add direct SSH/SCP logic; use `erie-remote-ssh` and configured JSON for remote validation.
- Keep workflow outputs in caller-selected run directories such as `reports/`; do not store generated run artifacts inside the skill.
