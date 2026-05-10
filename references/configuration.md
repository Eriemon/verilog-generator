# Configuration

Use `config/defaults.json` as the single source for local and remote validation defaults.

## Path Resolution

Settings support these placeholders:

- `${skill_dir}`: the `erie-verilog-generator` skill directory.
- `${project_root}`: the directory that contains `erie-verilog-generator`.
- `${settings_dir}`: the directory that contains the settings JSON being loaded.
- `${home}`: the current user's home directory.
- `${env:NAME}`: an environment variable value.

Keep local generated smoke runs, request files, downloads, and reports inside configured temporary directories and clean them after validation. Remote validation run directories are retained by default so the user can inspect generated Verilog projects.

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

`install` requires `--yes` and must be used only after the user confirms installation. `skip` is valid only for recommended dependencies. `adapt` writes user-level dependency state to `${home}/.codex/erie-verilog-generator/dependency-state.json`; for `erie-remote-ssh`, this records the installed `scripts/remote_ssh.py` and `config/defaults.json` paths so remote validation can use the local installation without storing machine-specific helper paths in this skill.

`fpga_developer_routing` records vendor-level developer skill preferences. AMD-Xilinx work recognizes `vivado-developer` and `vitis-developer`; PangoMicro work recognizes `pds-developer`. When any developer skill is installed, FPGA-Agent-Skills is not required and its Vivado/Vitis skills are not installed by this skill. If no developer skill is installed, FPGA-Agent-Skills remains a manual fallback only: `install --dependency-id fpga-agent-skills --yes` still skips it, and installation requires the additional `--allow-fpga-agent-fallback` flag. If both vendor families are available, ask the user which vendor to use for the current FPGA workflow and persist only that vendor choice in the user-level state file.

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

Run the local confidence gate from the project root:

```powershell
python .\erie-verilog-generator\scripts\validate_verilog_skill.py --settings .\erie-verilog-generator\config\defaults.json
```

The script runs standard skill validation, compile checks, smoke tests, CLI checks, legacy-term scanning, hardcoded-path scanning, and residual-artifact cleanup.

Run the local toolchain preflight when a caller asks for compile, execute, or implement readiness:

```powershell
python .\erie-verilog-generator\scripts\preflight_verilog_toolchain.py --settings .\erie-verilog-generator\config\defaults.json --readiness execute
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

## Remote Validation

All remote work must go through the `erie-remote-ssh` helper and JSON configuration. Do not add direct `ssh` or `scp` commands to this skill.

The default remote settings point to:

- helper: `${home}/.codex/skills/erie-remote-ssh/scripts/remote_ssh.py`, overridden by dependency adaptation state after `adapt`
- remote settings: `${home}/.codex/skills/erie-remote-ssh/config/defaults.json`, overridden by dependency adaptation state after `adapt`
- server list: `${home}/.codex/erie-verilog-generator/server_list.local.json`; create this user-local file with the `erie-remote-ssh` server-list JSON before remote validation
- recommended server selector: `<selected-server>` after the user chooses a target from `erie-remote-ssh choices`
- user toolchain selection config: `${home}/.codex/erie-verilog-generator/remote_toolchain_selection.json`

Run the remote gate:

```powershell
python .\erie-verilog-generator\scripts\remote_validate_verilog_skill.py --settings .\erie-verilog-generator\config\defaults.json --server <selected-server>
```

The remote script uses `python -X utf8` to avoid Windows console decoding failures while invoking `erie-remote-ssh`. It performs `discover`, `list`, `check`, `scan-software`, and `workspace-check`, then stages a temporary validation copy on the remote server through `request-mkdir`, `request-upload`, `request-command`, and `run-request --execute`. Before selecting a simulator, the remote command scans Vivado `settings64.sh` candidates from `$XILINX_VIVADO`, `/tools/Xilinx/Vivado/*/settings64.sh`, and `/opt/Xilinx/Vivado/*/settings64.sh`. If more than one Vivado candidate exists and no user-confirmed config is present, the gate fails with `TOOLCHAIN_SELECTION_REQUIRED=1` and prints the available choices.

Write a confirmed toolchain choice after the user selects a version:

```powershell
python .\erie-verilog-generator\scripts\remote_validate_verilog_skill.py --settings .\erie-verilog-generator\config\defaults.json --server <selected-server> --write-toolchain-selection --simulator-backend xsim --vivado-settings /tools/Xilinx/Vivado/<version>/settings64.sh
```

The user config records selections by server id under `remote_toolchains`, for example `simulator_backend=xsim` and `vivado_settings64=/tools/Xilinx/Vivado/<version>/settings64.sh`. A selected backend can also be `iverilog`; in that case Vivado activation is skipped and validation uses the configured simulator priority override for that run.

Remote validation directories are retained by default and printed as `remote_parent` and `remote_skill`. The server-side project path is relative to the configured remote workdir and looks like `.erie-verilog-generator-validation/run-YYYYMMDDTHHMMSS/erie-verilog-generator`. Retained runs keep `_smoke_runs` and `workflow-state.json` so generated RTL, testbenches, validation reports, and workflow traces remain inspectable. Pass `--cleanup-remote` only when the run directory should be deleted after validation. The legacy `--keep-remote` flag is accepted but no longer changes behavior because keeping is the default.

Each remote gate validates the canonical workflow and the fixed RTL fixtures in `assets/examples/remote_fixtures`: `comb_parity_mux`, `pipeline_delay`, and `ready_valid_slice`. Fixture reports are retained under `_smoke_runs/remote_fixtures/<fixture>/validation.json`, with a combined `_smoke_runs/remote_fixtures/summary.json` that records the selected simulator backend, executed tools, and generated RTL/testbench paths.

List retained runs without staging a new run:

```powershell
python .\erie-verilog-generator\scripts\remote_validate_verilog_skill.py --settings .\erie-verilog-generator\config\defaults.json --server <selected-server> --report-runs
```

The gate must use the highest simulator backend actually available on the selected server: Vivado xsim, then VCS+Verdi, then iverilog/vvp. If higher-priority simulators are later provided, the same gate must require the highest available backend instead of preserving an older fallback expectation. If `yosys` is not detected, implement readiness must block with `toolchain_issue`; if `yosys` is later provided, implement readiness must pass instead of preserving an older blocked expectation.

## Sensitive Data

Do not store real hostnames, usernames, key names, private-key paths, or ports in this skill. Keep those fields in the server-list JSON consumed by `erie-remote-ssh`.
