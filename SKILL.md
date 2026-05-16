---
name: erie-verilog-generator
description: >-
  Use when Codex needs Chinese-language Verilog development requests, Verilog design, Verilog modification, Verilog debug, RTL development, RTL design, RTL modification, RTL debug, RTL troubleshooting, independent static lint, testbench scaffold generation, or ASIC quality review for a Verilog-target design, including synthesizable Verilog-2001 RTL modules, self-checking Verilog testbenches, compile/simulation/validation with local or remote Vivado/xsim, artifact extraction, or workflow trace diagnosis.
---

# Erie Verilog Generator

Use this skill for Verilog-2001 RTL generation backed by the bundled `runtime/verilog_generator` Python workflow. Keep all generated design artifacts as Verilog `.v` files and use the stable facade in `integration/verilog_adapter.py`.

The same skill also exposes two local helper scripts for independent static lint and testbench scaffold generation without widening the skill beyond Verilog-2001:

- `scripts/verilog_lint.py` for independent static lint
- `scripts/tb_generator.py` for a self-checking Verilog testbench scaffold

These helper tools are optional workflow steps. They are part of the skill execution flow when the request benefits from them, but they are not mandatory entry gates.

## Dependency Preflight

On first use in a Codex installation, and before any remote/Vivado/Vitis-related workflow, run the dependency check from this skill root:

```powershell
python .\scripts\manage_skill_dependencies.py check --settings .\config\defaults.json
```

If required dependencies are missing, run `prompt` and ask the user whether to install each missing dependency before continuing:

```powershell
python .\scripts\manage_skill_dependencies.py prompt --settings .\config\defaults.json
```

Install only after the user confirms, then run `adapt` and tell the user to restart Codex so newly installed skills are discovered. Do not install `fpga-agent-skills` during normal preflight. If FPGA developer tooling is missing, prefer `vivado-developer`, `vitis-developer`, or `pds-developer`; FPGA-Agent-Skills is manual fallback only. If the user declines required dependency installation, continue only with local static Verilog generation/validation and block remote SSH, Vivado, Vitis, execute, and implement readiness paths. If recommended dependencies are missing, ask the user whether to install or skip them; use `skip <dependency-id>` only for a user-declined recommended dependency.

## FPGA Developer Routing

Before FPGA simulation, synthesis, implementation, constraints, debug, project creation, or other vendor tool work, inspect the developer routing state:

```powershell
python .\scripts\manage_skill_dependencies.py fpga-route --settings .\config\defaults.json
```

If both AMD-Xilinx and PangoMicro developer skills are available and no current selection exists, ask the user which vendor to use for this workflow. Persist only the user-confirmed vendor choice:

```powershell
python .\scripts\manage_skill_dependencies.py select-fpga-vendor --settings .\config\defaults.json amd_xilinx
python .\scripts\manage_skill_dependencies.py select-fpga-vendor --settings .\config\defaults.json pangomicro
```

Developer routing preference is: `vivado-developer`, then `vitis-developer`, for AMD-Xilinx work; `pds-developer` for PangoMicro work. When any of these developer skills is installed, do not install the FPGA-Agent-Skills Vivado/Vitis group. If no developer skill is installed, treat FPGA-Agent-Skills as a manual fallback that requires explicit user direction and the `--allow-fpga-agent-fallback` install flag.

## Workflow

1. Confirm the design intent before generation: module name, ports, clock/reset, behavior, pipeline expectation, interface family, and verification cases.
2. Use the staged pipeline: `requirements -> codegen_plan -> python -> rtl`.
3. Generate a Python reference model before RTL when running the workflow; use it as the semantic contract for the Verilog testbench.
4. Run the mandatory quality gate before claiming usable output. `validate_verilog_artifacts(...)` and `scripts/validate_verilog_skill.py` are required quality-control steps; skipping optional helpers does not bypass this gate.
5. Use optional helper tools only when they add value to the request:
   - Run `scripts/verilog_lint.py` when the user asks for independent static lint, standalone review findings, or a quick local lint pass on existing RTL or testbench files.
   - Run `scripts/tb_generator.py` when the user asks for a fast Verilog-2001 self-checking testbench scaffold or when a repair starts from module ports rather than the full staged workflow.
6. If the request includes compile, execute, or implement readiness, continue into the local or remote backend validation path. Prefer Vivado xsim first, then VCS+Verdi, then iverilog/vvp. Use `yosys` only for implement readiness.

## Strict Quality Policy

Strict quality control is mandatory. The required quality chain is:

1. Generate only synthesizable Verilog-2001 RTL and self-checking Verilog testbenches.
2. Prefer standardized interfaces: AXI-Stream for streaming data, AXI4-Lite for control/status registers, AXI4 for memory-mapped bulk transfers, and AHB/APB when a platform requires them. If a custom shape still needs bus unification, extend AXI-Stream with explicit sideband metadata in `interface_profile`.
3. Use the local standard bus templates in `assets/interface_templates` whenever `interface_family` is `axi_stream`, `axi4_lite`, `axi4`, `ahb`, or `apb`. Treat their port names, parameter names, and Chinese comments as strict-preferred defaults; only adapt them when the confirmed spec explicitly conflicts, and record the adaptation reason in the generated checks.
4. Avoid Verilog `function` and `task` blocks in generated Verilog, especially synthesizable RTL; prefer explicit always/assign logic and inline testbench checks for easier waveform debugging.
5. Apply ASIC quality review rules for generated RTL: complete combinational assignments, case defaults, no raw gated clocks, documented CDC/reset assumptions, and timing-reviewable datapath/control structure. Load `references/asic-verilog-quality.md` for detailed review guidance.
6. Validate with static checks by default; when external simulation is requested, select the highest available backend in this order: Vivado xsim, VCS+Verdi, then iverilog/vvp. Use `yosys` only for implement readiness.

Optional helper tools are inside the workflow, but strict quality control is the only mandatory gate.

## Remote Vivado Fallback

When the user requests compile, simulation, execute, or implement readiness and the local host does not provide Vivado (`vivado`) or the xsim toolchain (`xvlog`, `xelab`, `xsim`), do not continue as if local Vivado validation is possible. First run the `erie-remote-ssh` discovery and choice flow:

```powershell
python <erie-remote-ssh>\scripts\remote_ssh.py discover --settings <remote-settings> --config <server-list>
python <erie-remote-ssh>\scripts\remote_ssh.py choices --settings <remote-settings> --config <server-list>
```

Ask the user to select a remote server unless they already named one in the current request. A configured default server is only a recommendation; it is not user confirmation. After selection, use `erie-remote-ssh` for `check`, `scan-software`, `workspace-check`, request creation, and `run-request --execute`. If remote discovery sees multiple Vivado `settings64.sh` candidates, stop and ask the user which version to use; persist that confirmed choice in the project-local toolchain config before development or validation continues. Remote validation directories are retained by default under `.erie-verilog-generator-validation/run-YYYYMMDDTHHMMSS/erie-verilog-generator`, including `_smoke_runs` outputs; use `--cleanup-remote` only when the user wants the validation directory deleted. The remote gate must execute the canonical workflow plus the fixed RTL fixtures in `assets/examples/remote_fixtures` and retain each fixture `validation.json`. Use `--report-runs` for a read-only summary of retained remote runs. Do not add direct `ssh` or `scp` commands to this skill.

For Vivado/Vitis project creation, Tcl execution, synthesis/implementation strategy, timing analysis, constraints, debug, simulation, or Vitis HLS work, follow FPGA developer routing first. Use `vivado-developer` or `vitis-developer` for AMD-Xilinx and use `pds-developer` for PangoMicro. Do not install or route to FPGA-Agent-Skills Vivado/Vitis child skills unless the user explicitly requests that manual fallback.

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

Run optional helper tools only when the request benefits from them:

```powershell
python .\scripts\verilog_lint.py .\reports\verilog\generated\rtl\erie_adapter.v
python .\scripts\tb_generator.py .\reports\verilog\generated\rtl\erie_adapter.v --output .\reports\verilog\tb_erie_adapter.v
```

Check, prompt for, install approved dependencies, or adapt dependency skills:

```powershell
python .\scripts\manage_skill_dependencies.py check --settings .\config\defaults.json
python .\scripts\manage_skill_dependencies.py prompt --settings .\config\defaults.json
python .\scripts\manage_skill_dependencies.py install --settings .\config\defaults.json --dependency-id erie-remote-ssh --yes
python .\scripts\manage_skill_dependencies.py adapt --settings .\config\defaults.json
python .\scripts\manage_skill_dependencies.py fpga-route --settings .\config\defaults.json
python .\scripts\manage_skill_dependencies.py select-fpga-vendor --settings .\config\defaults.json amd_xilinx
python .\scripts\manage_skill_dependencies.py cleanup-fpga-agent-skills --settings .\config\defaults.json --yes
```

Record a user-confirmed remote toolchain selection in the project-local state folder:

```powershell
python .\scripts\remote_validate_verilog_skill.py --settings .\config\defaults.json --server <selected-server> --write-toolchain-selection --simulator-backend xsim --vivado-settings /tools/Xilinx/Vivado/<version>/settings64.sh
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
- Load `references/asic-verilog-quality.md` when reviewing RTL for ASIC quality, static lint findings, reset/CDC assumptions, raw gated clocks, latch risk, or timing-reviewable structure.
- Load `references/lint-checklist.md` when running independent static lint, preparing review findings, or deciding whether a warning should become a blocking issue.
- Load `references/testbench-patterns.md` when generating or repairing a Verilog-2001 self-checking testbench scaffold.
- Use `assets/examples/rtl_erie_verilog_spec.json` as the canonical Verilog-only fixture.
- Use `assets/interface_templates/catalog.json` and the referenced `.vinc` snippets when verifying or updating standard AXI-Stream, AXI4-Lite, AXI4, AHB, or APB interface guidance.
- Use `assets/examples/remote_fixtures` when verifying real-style remote xsim coverage across combinational logic, sequential pipeline logic, and ready/valid handshakes.

## Boundaries

- Do not generate non-Verilog hardware flows, C/C++ kernels, or alternate RTL dialects.
- Do not claim external tool validation happened unless the tool actually ran.
- Do not add direct SSH/SCP logic; use `erie-remote-ssh` and configured JSON for remote validation.
- Treat VCS+Verdi support as scripted backend invocation only; full Verdi GUI/session automation is out of scope unless it is explicitly added and validated.
- Keep workflow outputs in caller-selected run directories such as `reports/`; do not store generated run artifacts inside the skill.
