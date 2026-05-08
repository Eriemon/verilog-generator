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

The default remote settings are placeholders that must be wired to the caller's local `erie-remote-ssh` installation:

- helper: `<erie-remote-ssh>/scripts/remote_ssh.py`
- remote settings: `<erie-remote-ssh>/config/defaults.json`
- server list: `<path-to-server-list>.json`
- recommended server selector: `<server-id>`
- user toolchain selection config: `${home}/.codex/erie-verilog-generator/remote_toolchain_selection.json`

Run the remote gate:

```powershell
python .\erie-verilog-generator\scripts\remote_validate_verilog_skill.py --settings .\erie-verilog-generator\config\defaults.json --server <server-id>
```

The remote script uses `python -X utf8` to avoid Windows console decoding failures while invoking `erie-remote-ssh`. It performs `discover`, `list`, `check`, `scan-software`, and `workspace-check`, then stages a temporary validation copy on the remote server through `request-mkdir`, `request-upload`, `request-command`, and `run-request --execute`. Before selecting a simulator, the remote command scans Vivado `settings64.sh` candidates from `$XILINX_VIVADO`, `/tools/Xilinx/Vivado/*/settings64.sh`, and `/opt/Xilinx/Vivado/*/settings64.sh`. If more than one Vivado candidate exists and no user-confirmed config is present, the gate fails with `TOOLCHAIN_SELECTION_REQUIRED=1` and prints the available choices.

Write a confirmed toolchain choice after the user selects a version:

```powershell
python .\erie-verilog-generator\scripts\remote_validate_verilog_skill.py --settings .\erie-verilog-generator\config\defaults.json --server <server-id> --write-toolchain-selection --simulator-backend xsim --vivado-settings /tools/Xilinx/Vivado/<version>/settings64.sh
```

The user config records selections by server id under `remote_toolchains`, for example `simulator_backend=xsim` and `vivado_settings64=/tools/Xilinx/Vivado/<version>/settings64.sh`. A selected backend can also be `iverilog`; in that case Vivado activation is skipped and validation uses the configured simulator priority override for that run.

Remote validation directories are retained by default and printed as `remote_parent` and `remote_skill`. The server-side project path is relative to the configured remote workdir and looks like `.erie-verilog-generator-validation/run-YYYYMMDDTHHMMSS/erie-verilog-generator`. Retained runs keep `_smoke_runs` and `workflow-state.json` so generated RTL, testbenches, validation reports, and workflow traces remain inspectable. Pass `--cleanup-remote` only when the run directory should be deleted after validation. The legacy `--keep-remote` flag is accepted but no longer changes behavior because keeping is the default.

Each remote gate validates the canonical workflow and the fixed RTL fixtures in `assets/examples/remote_fixtures`: `comb_parity_mux`, `pipeline_delay`, and `ready_valid_slice`. Fixture reports are retained under `_smoke_runs/remote_fixtures/<fixture>/validation.json`, with a combined `_smoke_runs/remote_fixtures/summary.json` that records the selected simulator backend, executed tools, and generated RTL/testbench paths.

List retained runs without staging a new run:

```powershell
python .\erie-verilog-generator\scripts\remote_validate_verilog_skill.py --settings .\erie-verilog-generator\config\defaults.json --server <server-id> --report-runs
```

The gate must use the highest simulator backend actually available on the selected server: Vivado xsim, then VCS+Verdi, then iverilog/vvp. If higher-priority simulators are later provided, the same gate must require the highest available backend instead of preserving an older fallback expectation. If `yosys` is not detected, implement readiness must block with `toolchain_issue`; if `yosys` is later provided, implement readiness must pass instead of preserving an older blocked expectation.

## Sensitive Data

Do not store real hostnames, usernames, key names, private-key paths, or ports in this skill. Keep those fields in the server-list JSON consumed by `erie-remote-ssh`.
