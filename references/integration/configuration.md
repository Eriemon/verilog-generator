# Configuration

## Table of Contents

- [Path Resolution](#path-resolution)
- [Skill Dependencies](#skill-dependencies)
- [Local Validation](#local-validation)
- [Simulator Selection](#simulator-selection)
- [Interface Defaults](#interface-defaults)
- [Remote Validation](#remote-validation)
- [Sensitive Data](#sensitive-data)

Use `config/defaults.json` for install-safe defaults, then layer project-local `.settings/verilog.local.json` on top. The remote server registry lives in `.settings/server_list.local.json`, the user-confirmed remote target lives in `.settings/remote-selection.local.json`, and the selected remote workdir must provide `.settings/verilog.remote.json` for remote external validation.

## Path Resolution

Settings support these placeholders:

- `${skill_dir}`: the `skills/readable-verilog-generator` skill directory.
- `${project_root}`: the repository root that contains `skills/`.
- `${settings_dir}`: the directory that contains the settings JSON being loaded.
- `${home}`: the current user's home directory.
- `${env:NAME}`: an environment variable value.

Keep local generated smoke runs, request files, downloads, and reports inside configured temporary directories and clean them after validation. Remote validation run directories are retained by default so the user can inspect generated Verilog projects.

The default path set also includes:

- `paths.example_spec`: the canonical Verilog-only example spec.
- `paths.use_case_examples_dir`: the directory that stores the five ADC or DAC family example specs.
- `paths.use_case_template_catalog`: the board-level ADC or DAC family catalog under `assets/use_case_templates/catalog.json`.

Skill-effectiveness eval assets live under `evals/evals.json`. The file records the canonical Verilog case, the five ADC/DAC family-template cases, and the improved local Verilog template cases so a deterministic with-vs-without skill harness can measure pass-rate delta without reconstructing the scenario list by hand.

## Skill Dependencies

`skill_dependencies` records dependency groups by GitHub URL, expected local skill names, install policy, and adaptation policy. The required group is:

- `https://github.com/Eriemon/remote-ssh.git`: provides `erie-remote-ssh` for remote SSH server selection and remote Verilog validation.

The recommended groups are:

- `https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git`: engineering debug and context optimization workflows.

The manual fallback group is:

- `https://github.com/adeleempurpled290/FPGA-Agent-skills.git`: legacy Vivado/Vitis child skills. This group is never installed during normal preflight and requires explicit fallback approval.

Run the dependency manager from the skill root through the canonical module entrypoint.

```powershell
python -m scripts.python.toolchain.manage_skill_dependencies check --settings .\config\defaults.json
python -m scripts.python.toolchain.manage_skill_dependencies prompt --settings .\config\defaults.json
python -m scripts.python.toolchain.manage_skill_dependencies install --settings .\config\defaults.json --dependency-id erie-remote-ssh --yes
python -m scripts.python.toolchain.manage_skill_dependencies install --settings .\config\defaults.json --dependency-id <renderer-dependency-id> --yes
python -m scripts.python.toolchain.manage_skill_dependencies adapt --settings .\config\defaults.json
```

`install` requires `--yes` and must be used only after the user confirms installation. `skip` is valid only for recommended dependencies. `adapt` writes project-local dependency state to `<workspace-root>/.readable-verilog-generator-state/dependency-state.json`; for `erie-remote-ssh`, this records the installed `scripts/python/runtime/remote_ssh.py` entrypoint and the supported defaults file path under either `config/defaults.json` or `assets/defaults.json` so remote validation can use the local installation without storing machine-specific helper paths in this skill. Legacy `.erie-verilog-generator-state` files remain read-only migration sources. If the command is not launched from a workspace root containing `.git` or `AGENTS.md`, pass `--state-path` explicitly.

`fpga_developer_routing` records vendor-level developer skill preferences. AMD-Xilinx work recognizes `vivado-developer` and `vitis-developer`; PangoMicro work recognizes `pds-developer`. When any developer skill is installed, FPGA-Agent-Skills is not required and its Vivado/Vitis skills are not installed by this skill. If no developer skill is installed, FPGA-Agent-Skills remains a manual fallback only: `install --dependency-id fpga-agent-skills --yes` still skips it, and installation requires the additional `--allow-fpga-agent-fallback` flag. If both vendor families are available, ask the user which vendor to use for the current FPGA workflow and persist only that vendor choice in the project-local state file.

The first `tool_dependencies.required` record in settings declares the external waveform runtime package, version, executable, and Node/npm floor. The bundled `scripts/python/toolchain/wavedrom_runtime.py` is the only install/check/render route: it builds the install and render argv from that record, requires explicit confirmation for installation, checks the configured version, and captures SVG from the configured executable. A separately named renderer package is unsupported unless it is declared by the settings authority.

Developer routing commands:

```powershell
python -m scripts.python.toolchain.manage_skill_dependencies fpga-route --settings .\config\defaults.json
python -m scripts.python.toolchain.manage_skill_dependencies select-fpga-vendor --settings .\config\defaults.json amd_xilinx
python -m scripts.python.toolchain.manage_skill_dependencies select-fpga-vendor --settings .\config\defaults.json pangomicro
python -m scripts.python.toolchain.manage_skill_dependencies cleanup-fpga-agent-skills --settings .\config\defaults.json --yes
```

If a persisted vendor choice becomes stale because its developer skill was removed, `fpga-route` reports `selection_stale`; ask again instead of silently falling back to another vendor.

`cleanup-fpga-agent-skills --yes` moves legacy FPGA-Agent child skills (`vivado-tcl`, `vivado-sim`, `vivado-synth`, `vivado-impl`, `vivado-analysis`, `vivado-constraints`, `vivado-debug`, and `vitis-hls-synthesis`) into `${CODEX_HOME}/skill-backups/fpga-agent-skills.bak.<timestamp>`. It refuses to run unless an FPGA developer skill is installed and never moves `vivado-developer`, `vitis-developer`, or `pds-developer`.

## Local Validation

Run the local confidence gate from the skill root through the canonical module entrypoint.

```powershell
python -m scripts.python.validation.validate_verilog_skill --settings .\config\defaults.json
python -m scripts.python.validation.validate_verilog_skill --settings .\config\defaults.json --no-require-remote
python -m scripts.python.validation.validate_verilog_skill --settings .\config\defaults.json --with-external-audit --with-repo-regression --no-require-remote
```

The default confidence gate is install-package self-contained: it does not require repository-root `tests/`, `tests/smoke/`, `quick_validate.py`, `audit_skill.py`, or `agents-md-generator` to exist in a clean extracted skill package. Use `--with-external-audit` for development or release audit tools outside the skill package, and use `--with-repo-regression` from the source repository to run root-level unittest and smoke suites. Use `--no-require-remote` only when you intentionally want a local-only diagnostic pass.

Run the deterministic skill-effectiveness gate from the source repository's skill root. The explicit workspace root keeps repository-level reports outside the installable skill payload while preserving path validation:

```powershell
python -B -m scripts.python.workflow.cli eval-skill --workspace-root ..\.. --evals skills\readable-verilog-generator\evals\evals.json --out reports\verilog\skill_effectiveness.json
```

In an installed skill copy, omit `--workspace-root` to retain the skill directory as the default boundary and write only to an explicitly allowed path inside that workspace. An explicit root must already exist and be a directory; eval inputs, the output report, optional remote-run evidence, and workflow state remain fail-closed inside that root.

Run the local toolchain preflight from the skill root when a caller asks for compile, execute, or implement readiness:

```powershell
python -m scripts.python.toolchain.preflight_verilog_toolchain --settings .\config\defaults.json --readiness execute
```

If the report sets `remote_selection_required=true`, do not silently fall back to local external tools. Refresh `.settings/server_list.local.json` through `erie-remote-ssh`, confirm the selected server in `.settings/remote-selection.local.json`, and ensure the remote workdir provides `.settings/verilog.remote.json` before remote validation.

For `validate`, compile, execute, or implement readiness is only credible when an external backend actually runs. Combining those readiness levels with `--no-external` produces a `toolchain_issue` error and reports `static_passed`, `compile_not_run`, `sim_not_run`, and remote-required status separately.

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

`rtl_style_profile=erie_strict` now also inherits curated Erie style guidance from `references/rules/erie-style.md` and `assets/style_templates/`. This strengthens bilingual headers, FSM naming (`state_current` / `state_next` / `ST_*`), `_Inst` instance naming, `gen_*` generate labels, and AXI/AXIS/APB/AHB port grouping as prompt-level requirements. Validation reports these newer Erie-strict checks as warnings first, not hard errors.

## File Role Confirmations

The optional top-level spec field `file_role_confirmations` resolves only ordinary `.v`/`.sv` filenames whose content produces an ambiguous VG149 testbench role. It is a JSON object keyed by the source path relative to the scanned directory or by the scanned file's basename. Keys must be canonical POSIX relative paths: no absolute path, drive prefix, backslash, empty segment, `.` segment, or `..` traversal is accepted. Values are exactly `design` or `testbench`.

```json
{
  "file_role_confirmations": {
    "<rtl-file>.v": "design",
    "<verification-file>.sv": "testbench"
  }
}
```

A confirmation may not contradict explicit filename/directory testbench evidence. Confirming `testbench` resolves the role only; VG149 still requires renaming the authority-selected verification file to a `tb_<function>.sv` name. Confirming `design` makes VG149 not applicable to an otherwise ambiguous ordinary filename. Unknown paths, duplicate normalized paths, unsupported role values, and paths outside the current scan root fail closed.

## Remote Validation

All remote work must go through the `erie-remote-ssh` helper and JSON configuration. Do not add direct `ssh` or `scp` commands to this skill.

The default remote settings point to:

- helper: `${CODEX_HOME}/skills/erie-remote-ssh/scripts/python/runtime/remote_ssh.py`, overridden by dependency adaptation state after `adapt`
- remote settings: `${CODEX_HOME}/skills/erie-remote-ssh/assets/defaults.json`, overridden by dependency adaptation state after `adapt`
- local server list: `<workspace-root>/.settings/server_list.local.json`; `erie-remote-ssh` owns this file and the Verilog skill only reads it
- local remote selection: `<workspace-root>/.settings/remote-selection.local.json`; store only the user-confirmed `server_id` here
- local Verilog project settings: `<workspace-root>/.settings/verilog.local.json`; store local tools, commands, and remote-first policy here, but do not store server identifiers here
- remote runtime config: `<remote-root>/.settings/verilog.remote.json` relative to the selected server workdir; store `remote.toolchain.simulator_backend`, `remote.toolchain.vivado_settings64`, and any remote-only environment overrides there

Run the remote gate:

```powershell
python -m scripts.python.remote.remote_validate_verilog_skill --settings .\config\defaults.json --server <selected-server>
```

The remote script uses `python -X utf8` to avoid Windows console decoding failures while invoking `erie-remote-ssh`. It performs the discovery, inventory, check, software-scan, and workspace-check operations declared by the remote helper, then stages a temporary validation copy through the governed request flow. Timeout, report roots, simulator candidates, and selection markers come from settings authority; ambiguous candidates fail with a selection-required status until the user confirms one. The validation package keeps its isolated Codex `HOME` under the configured reports root, so tests do not depend on the remote account's installed skills and directory governance does not treat validation dependencies as source.

Write a confirmed toolchain choice after the user selects a version:

```powershell
python -m scripts.python.remote.remote_validate_verilog_skill --settings .\config\defaults.json --server <selected-server> --write-toolchain-selection --simulator-backend <simulator-backend> --vivado-settings <settings64-path>
```

The dedicated local selection file records only the selected server identity field declared by settings. The remote workdir `<remote-root>/.settings/verilog.remote.json` records the active simulator backend and optional toolchain settings path. Backend activation and simulator priority are authority-driven for each run.

For confidence-sensitive gates, the active server is always the one stored in `.settings/remote-selection.local.json` or explicitly passed through `--remote-server`. The toolchain source of truth is the remote workdir `.settings/verilog.remote.json`; local legacy toolchain caches must not satisfy the active gate.

Remote validation directories are retained by default and printed as `remote_parent` and `remote_skill`. Each new run uses the collision-resistant run identity and layout declared by the settings authority. The selected project directory is uploaded only through the governed source-manifest directory contract; archive and whole-bundle transport remain forbidden. The upload request and its verification receipt bind the canonical source manifest before the remote command starts. Execution logs, reports, fixture artifacts, and Agent self-review use the configured relative artifact paths. Older retained layouts are read-only compatibility sources and are never migrated or written by the new flow. Cleanup remains an explicit opt-in action for only the current retained run; the default path never deletes successful or failed evidence.

Each remote gate validates the authority-selected pytest phases, canonical workflow, and case catalog from settings under the configured fixture root. Case-specific prechecks, source names, operation budgets, latency expectations, testbench names, and simulator commands are all catalog data rather than workflow constants. Phase summaries bind the exact command hash, exit code, timestamp, and counts; post-test, environment, cwd, pressure, manifest, evidence, completion, and Agent-review artifacts use the configured artifact names. Fixture reports use the authority-selected case identifier and validation filename, with a combined summary recording the selected backend, executed tools, and generated RTL/testbench paths.

List retained runs without staging a new run:

```powershell
python -m scripts.python.remote.remote_validate_verilog_skill --settings .\config\defaults.json --server <selected-server> --report-runs
```

Automated callers pass `--run-id <run-id>` together with `--report-runs` so the report reads only the run identity returned by the immediately preceding validation command. The report downloads `completion.json` together with the retained phase summaries, runtime evidence, and canonical execution and fixture reports. After the TESTER has committed the exact `tests/**` tree, `--write-test-evidence <path> --run-id <run-id> --test-commit-sha <sha>` writes a canonical, self-hashed receipt that binds that test commit, source manifest, `uploaded_verified` receipt reference, remote fingerprints, retained artifact manifest, pressure report, and all three successful pytest phases. A confidence-sensitive `eval-skill --require-remote` result is green only when the completion marker matches the outer run and erie source-manifest digest, pytest has positive passing counts in every phase, xsim execution passes, and all fixture reports pass. Older retained runs without `completion.json`, phase summaries, or `remote_test_evidence.json` remain inspectable but are incomplete remote evidence.

The gate must use the highest simulator backend actually available on the selected server: Vivado xsim, then VCS+Verdi, then iverilog/vvp. If higher-priority simulators are later provided, the same gate must require the highest available backend instead of preserving an older fallback expectation. If `yosys` is not detected, implement readiness must block with `toolchain_issue`; if `yosys` is later provided, implement readiness must pass instead of preserving an older blocked expectation.

## Sensitive Data

Do not store real hostnames, usernames, key names, private-key paths, ports, packaged default server ids, or packaged server display names in this skill. Keep those fields in the server-list JSON consumed by `erie-remote-ssh`, and persist only user-confirmed selections in project-local state.
