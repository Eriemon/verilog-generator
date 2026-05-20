# Configuration

## Table of Contents

- [Path Resolution](#path-resolution)
- [Skill Dependencies](#skill-dependencies)
- [Local Validation](#local-validation)
- [Simulator Selection](#simulator-selection)
- [Interface Defaults](#interface-defaults)
- [Remote Validation](#remote-validation)
- [Sensitive Data](#sensitive-data)

Use `config/defaults.json` as the single source for local and remote validation defaults.

## Path Resolution

Settings support these placeholders:

- `${skill_dir}`: the `skills/erie-verilog-generator` skill directory.
- `${project_root}`: the repository root that contains `skills/`.
- `${settings_dir}`: the directory that contains the settings JSON being loaded.
- `${home}`: the current user's home directory.
- `${env:NAME}`: an environment variable value.

Keep local generated smoke runs, request files, downloads, and reports inside configured temporary directories and clean them after validation. Remote validation run directories are retained by default so the user can inspect generated Verilog projects.

The default path set also includes:

- `paths.example_spec`: the canonical Verilog-only example spec.
- `paths.use_case_examples_dir`: the directory that stores the five ADC or DAC family example specs.
- `paths.use_case_template_catalog`: the board-level ADC or DAC family catalog under `assets/use_case_templates/catalog.json`.

Skill-effectiveness eval assets live under `evals/evals.json`. The file records the canonical Verilog case, the five ADC/DAC family-template cases, and the refined local Verilog template cases so a deterministic with-vs-without skill harness can measure pass-rate delta without reconstructing the scenario list by hand.

## Skill Dependencies

`skill_dependencies` records dependency groups by GitHub URL, expected local skill names, install policy, and adaptation policy. The required groups are:

- `https://github.com/Eriemon/remote-ssh.git`: provides `erie-remote-ssh` for remote SSH server selection and remote Verilog validation.
- `https://github.com/adeleempurpled290/FPGA-Agent-skills.git`: provides `vivado-tcl`, `vivado-sim`, `vivado-synth`, `vivado-impl`, `vivado-analysis`, `vivado-constraints`, `vivado-debug`, and `vitis-hls-synthesis`.

The recommended groups are:

- `https://github.com/obra/superpowers.git`: planning, execution, TDD, and verification workflows.
- `https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git`: engineering debug and context optimization workflows.

Run the dependency manager from the skill root:

```powershell
python .\scripts\manage_skill_dependencies.py check --settings .\config\defaults.json
python .\scripts\manage_skill_dependencies.py prompt --settings .\config\defaults.json
python .\scripts\manage_skill_dependencies.py skip --settings .\config\defaults.json superpowers
python .\scripts\manage_skill_dependencies.py install --settings .\config\defaults.json --dependency-id erie-remote-ssh --yes
python .\scripts\manage_skill_dependencies.py adapt --settings .\config\defaults.json
```

`install` requires `--yes` and must be used only after the user confirms installation. `skip` is valid only for recommended dependencies. `adapt` writes project-local dependency state to `<workspace-root>/.erie-verilog-generator-state/dependency-state.json`; for `erie-remote-ssh`, this records the installed `scripts/remote_ssh.py` and `config/defaults.json` paths so remote validation can use the local installation without storing machine-specific helper paths in this skill. If the command is not launched from a workspace root containing `.git` or `AGENTS.md`, pass `--state-path` explicitly.

`fpga_developer_routing` records vendor-level developer skill preferences. AMD-Xilinx work recognizes `vivado-developer` and `vitis-developer`; PangoMicro work recognizes `pds-developer`. When any developer skill is installed, FPGA-Agent-Skills is not required and its Vivado/Vitis skills are not installed by this skill. If no developer skill is installed, FPGA-Agent-Skills remains a manual fallback only: `install --dependency-id fpga-agent-skills --yes` still skips it, and installation requires the additional `--allow-fpga-agent-fallback` flag. If both vendor families are available, ask the user which vendor to use for the current FPGA workflow and persist only that vendor choice in the project-local state file.

Developer routing commands:

```powershell
python .\scripts\manage_skill_dependencies.py fpga-route --settings .\config\defaults.json
python .\scripts\manage_skill_dependencies.py select-fpga-vendor --settings .\config\defaults.json amd_xilinx
python .\scripts\manage_skill_dependencies.py select-fpga-vendor --settings .\config\defaults.json pangomicro
python .\scripts\manage_skill_dependencies.py cleanup-fpga-agent-skills --settings .\config\defaults.json --yes
```

If a persisted vendor choice becomes stale because its developer skill was removed, `fpga-route` reports `selection_stale`; ask again instead of silently falling back to another vendor.

`cleanup-fpga-agent-skills --yes` moves legacy FPGA-Agent child skills (`vivado-tcl`, `vivado-sim`, `vivado-synth`, `vivado-impl`, `vivado-analysis`, `vivado-constraints`, `vivado-debug`, and `vitis-hls-synthesis`) into `${home}/.codex/skill-backups/fpga-agent-skills.bak.<timestamp>`. It refuses to run unless an FPGA developer skill is installed and never moves `vivado-developer`, `vitis-developer`, or `pds-developer`.

## Local Validation

Run the local confidence gate from the skill root:

```powershell
python .\scripts\validate_verilog_skill.py --settings .\config\defaults.json
```

The script runs standard skill validation, compile checks, smoke tests, CLI checks, legacy-term scanning, hardcoded-path scanning, and residual-artifact cleanup.

Run the deterministic skill-effectiveness gate from the skill root:

```powershell
python -m runtime.verilog_generator eval-skill --evals .\evals\evals.json --out .\reports\verilog\skill_effectiveness.json
```

Run the local toolchain preflight from the skill root when a caller asks for compile, execute, or implement readiness:

```powershell
python .\scripts\preflight_verilog_toolchain.py --settings .\config\defaults.json --readiness execute
```

If the report sets `remote_selection_required=true`, run `erie-remote-ssh discover` and `choices`, then ask the user to select a server before remote validation. A configured default server is only a recommendation unless `server_confirmed=true`.

## Simulator Selection

`validation.simulators` defines the external simulation fallback order. The default order is:

1. `xsim`: requires `xvlog`, `xelab`, and `xsim`.
2. `vcs_verdi`: requires both `vcs` and `verdi`.
3. `iverilog`: requires `iverilog` and `vvp`.

The default `selection_policy` is `fallback`: validation uses the highest available backend, records missing higher-priority backends in report metrics, and only blocks when no simulator backend is available. `yosys` is separate and is required only for implement readiness.

## Interface Defaults

When callers do not provide an explicit `interface_family`, the requirements layer chooses a conservative bus default from the design text: AXI-Stream for stream/packet/frame/sample data, AXI4-Lite for control/status/register blocks, AXI4 for memory-mapped burst or DMA transfers, and AHB/APB only when those platform buses are explicitly requested. `native` and `custom` remain supported for designs that cannot or should not use a standard bus.

Supported interface families are `axi_stream`, `axi4`, `axi4_lite`, `ahb`, `apb`, `native`, and `custom`. Existing specs using `interface_family=axi4` with `interface_profile.axi4_variant=axi4_lite` remain valid. Generation prompts also ask models to avoid Verilog `function` and `task` blocks where practical; this is a style preference, not a generic validation failure.

Local standard interface templates live under `assets/interface_templates`. The catalog maps `interface_family`, `role`, and `read_write_mode` to a single `.vinc` snippet for AXI-Stream duplex, AXI4-Lite config, AXI4-Full master, AXI4-Full slave, AHB-Lite config, and APB config interfaces. Callers may set `interface_profile.template_id` to request a specific template; otherwise the requirements layer records the default selected template in `selected_interface_template_id` and the codegen plan's `interface_decision`. Template port names, parameter names, and Chinese comments are strict-preferred defaults. Generated RTL may adapt them only when the confirmed spec explicitly conflicts, and that adaptation must be recorded in the generated reviewability checks.

Board-level ADC or DAC family templates live under `assets/use_case_templates`. The catalog is keyed by `workflow.use_case_template_id` and currently supports `spi_adc`, `spi_dac`, `jesd_adc`, `jesd_dac`, and `mxfe_mixed`. Each bundle contains `manifest.json`, a representative `verilog/system_top.v`, Tcl block-design fragments, a project Tcl wrapper, and one representative XDC. The runtime does not auto-detect these families from part names; callers must set `workflow.use_case_template_id` explicitly when they want board-level family guidance.

`rtl_style_profile=erie_strict` now also inherits ref-derived Erie style guidance from `references/erie-ref-style.md` and `assets/style_templates/`. This strengthens bilingual headers, FSM naming (`state_current` / `state_next` / `ST_*`), `_Inst` instance naming, `gen_*` generate labels, and AXI/AXIS/APB/AHB port grouping as prompt-level requirements. Validation reports these newer ref-derived checks as warnings first, not hard errors.

## Remote Validation

All remote work must go through the `erie-remote-ssh` helper and JSON configuration. Do not add direct `ssh` or `scp` commands to this skill.

The default remote settings point to:

- helper: `${home}/.codex/skills/erie-remote-ssh/scripts/remote_ssh.py`, overridden by dependency adaptation state after `adapt`
- remote settings: `${home}/.codex/skills/erie-remote-ssh/config/defaults.json`, overridden by dependency adaptation state after `adapt`
- server list: `<workspace-root>/.erie-verilog-generator-state/server_list.local.json`; create this project-local file with the `erie-remote-ssh` server-list JSON before remote validation. If this project-local file is absent, `remote_validate_verilog_skill.py` falls back to the installed `erie-remote-ssh/config/server_list.local.json` so a clean local skill install can still reuse the user's existing remote registry without reintroducing committed local state
- confirmed server selection: `<workspace-root>/.erie-verilog-generator-state/remote_server_selection.json`; store only a user-confirmed server id here, not hostnames, usernames, or ports
- no packaged default server selector; the user must choose a server from `erie-remote-ssh choices` or persist one in project-local state after confirmation
- toolchain selection config: `<workspace-root>/.erie-verilog-generator-state/remote_toolchain_selection.json`

Run the remote gate:

```powershell
python .\scripts\remote_validate_verilog_skill.py --settings .\config\defaults.json --server <selected-server>
```

The remote script uses `python -X utf8` to avoid Windows console decoding failures while invoking `erie-remote-ssh`. It performs `discover`, `list`, `check`, `scan-software`, and `workspace-check`, then stages a temporary validation copy on the remote server through `request-mkdir`, `request-upload`, `request-command`, and `run-request --execute`. Before selecting a simulator, the remote command scans Xilinx `settings64.sh` candidates from `$XILINX_VIVADO`, `/tools/Xilinx/Vivado/*/settings64.sh`, `/tools/Xilinx/Vitis/*/settings64.sh`, and `/opt/Xilinx/Vivado/*/settings64.sh`. If more than one candidate exists and no user-confirmed config is present, the gate fails with `TOOLCHAIN_SELECTION_REQUIRED=1` and prints the available choices.

Write a confirmed toolchain choice after the user selects a version:

```powershell
python .\scripts\remote_validate_verilog_skill.py --settings .\config\defaults.json --server <selected-server> --write-toolchain-selection --simulator-backend xsim --vivado-settings /tools/Xilinx/<toolchain>/<version>/settings64.sh
```

The project-local config records selections by server id under `remote_toolchains`, for example `simulator_backend=xsim` and `vivado_settings64=/tools/Xilinx/<toolchain>/<version>/settings64.sh`. A selected backend can also be `iverilog`; in that case Xilinx toolchain activation is skipped and validation uses the configured simulator priority override for that run.

Remote validation directories are retained by default and printed as `remote_parent` and `remote_skill`. The server-side project path is relative to the configured remote workdir and looks like `.erie-verilog-generator-validation/run-YYYYMMDDTHHMMSS/erie-verilog-generator`. Retained runs keep `_smoke_runs` and `workflow-state.json` so generated RTL, testbenches, validation reports, and workflow traces remain inspectable. Pass `--cleanup-remote` only when the run directory should be deleted after validation. The legacy `--keep-remote` flag is accepted but no longer changes behavior because keeping is the default.

Each remote gate validates the canonical workflow and the fixed RTL fixtures in `assets/examples/remote_fixtures`: `comb_parity_mux`, `pipeline_delay`, and `ready_valid_slice`. Fixture reports are retained under `_smoke_runs/remote_fixtures/<fixture>/validation.json`, with a combined `_smoke_runs/remote_fixtures/summary.json` that records the selected simulator backend, executed tools, and generated RTL/testbench paths.

List retained runs without staging a new run:

```powershell
python .\scripts\remote_validate_verilog_skill.py --settings .\config\defaults.json --server <selected-server> --report-runs
```

The gate must use the highest simulator backend actually available on the selected server: Vivado xsim, then VCS+Verdi, then iverilog/vvp. If higher-priority simulators are later provided, the same gate must require the highest available backend instead of preserving an older fallback expectation. If `yosys` is not detected, implement readiness must block with `toolchain_issue`; if `yosys` is later provided, implement readiness must pass instead of preserving an older blocked expectation.

## Sensitive Data

Do not store real hostnames, usernames, key names, private-key paths, ports, packaged default server ids, or packaged server display names in this skill. Keep those fields in the server-list JSON consumed by `erie-remote-ssh`, and persist only user-confirmed selections in project-local state.
