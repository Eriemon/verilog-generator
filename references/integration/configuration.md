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
python -m scripts.python.toolchain.manage_skill_dependencies install --settings .\config\defaults.json --dependency-id wavedrom --yes
python -m scripts.python.toolchain.manage_skill_dependencies adapt --settings .\config\defaults.json
```

`install` requires `--yes` and must be used only after the user confirms installation. `skip` is valid only for recommended dependencies. `adapt` writes project-local dependency state to `<workspace-root>/.readable-verilog-generator-state/dependency-state.json`; for `erie-remote-ssh`, this records the installed `scripts/python/runtime/remote_ssh.py` entrypoint and the supported defaults file path under either `config/defaults.json` or `assets/defaults.json` so remote validation can use the local installation without storing machine-specific helper paths in this skill. Legacy `.erie-verilog-generator-state` files remain read-only migration sources. If the command is not launched from a workspace root containing `.git` or `AGENTS.md`, pass `--state-path` explicitly.

`fpga_developer_routing` records vendor-level developer skill preferences. AMD-Xilinx work recognizes `vivado-developer` and `vitis-developer`; PangoMicro work recognizes `pds-developer`. When any developer skill is installed, FPGA-Agent-Skills is not required and its Vivado/Vitis skills are not installed by this skill. If no developer skill is installed, FPGA-Agent-Skills remains a manual fallback only: `install --dependency-id fpga-agent-skills --yes` still skips it, and installation requires the additional `--allow-fpga-agent-fallback` flag. If both vendor families are available, ask the user which vendor to use for the current FPGA workflow and persist only that vendor choice in the project-local state file.

`tool_dependencies.wavedrom` locks the external waveform runtime to npm package `wavedrom@3.6.1`, executable `wavedrom`, and Node.js `>=20.0.0`. The bundled `scripts/python/toolchain/wavedrom_runtime.py` is the only install/check/render route: it runs `npm install --global wavedrom@3.6.1 --yes` only after explicit confirmation, checks the exact version, and captures SVG from `wavedrom --input <wavejson> --indent 2`. The separate `wavedrom-cli` package is not a supported dependency.

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
    "rtl/counter.v": "design",
    "verification/counter.sv": "testbench"
  }
}
```

A confirmation may not contradict explicit filename/directory testbench evidence. Confirming `testbench` resolves the role only; VG149 still requires renaming `verification/counter.sv` to a `tb_<function>.sv` name. Confirming `design` makes VG149 not applicable to an otherwise ambiguous ordinary filename. Unknown paths, duplicate normalized paths, unsupported role values, and paths outside the current scan root fail closed.

## Remote Validation

All remote work must go through the `erie-remote-ssh` helper and JSON configuration. Do not add direct `ssh` or `scp` commands to this skill.

The default remote settings point to:

- helper: `${CODEX_HOME}/skills/erie-remote-ssh/scripts/python/runtime/remote_ssh.py`, overridden by dependency adaptation state after `adapt`
- remote settings: `${CODEX_HOME}/skills/erie-remote-ssh/assets/defaults.json`, overridden by dependency adaptation state after `adapt`
- local server list: `<workspace-root>/.settings/server_list.local.json`; `erie-remote-ssh` owns this file and the Verilog skill only reads it
- local remote selection: `<workspace-root>/.settings/remote-selection.local.json`; store only the user-confirmed `server_id` here
- local Verilog project settings: `<workspace-root>/.settings/verilog.local.json`; store local tools, commands, and remote-first policy here, but do not store server identifiers here
- remote runtime config: `.readable-verilog-generator/.settings/verilog.remote.json` relative to the selected server-default workdir; store `remote.toolchain.simulator_backend`, `remote.toolchain.vivado_settings64`, and any remote-only environment overrides there

Run the remote gate:

```powershell
python -m scripts.python.remote.remote_validate_verilog_skill --settings .\config\defaults.json --server <selected-server>
```

The remote script uses `python -X utf8` to avoid Windows console decoding failures while invoking `erie-remote-ssh`. It performs `discover`, `list`, `check`, `scan-software`, and `workspace-check`, then stages a temporary validation copy on the remote server through `request-mkdir`, `request-upload`, `request-command`, and `run-request --execute`. The default synchronous timeout is 600 seconds so the same reviewed request can finish authoritative `pytest`, effectiveness checks, and real simulation without inheriting the helper's short-task timeout. The validation package keeps its isolated Codex `HOME` under the governed `reports/` root, so tests do not depend on the remote account's installed skills and directory governance does not treat validation dependencies as source. Before selecting a simulator, the remote command scans Xilinx `settings64.sh` candidates from `$XILINX_VIVADO`, `/tools/Xilinx/Vivado/*/settings64.sh`, `/tools/Xilinx/Vitis/*/settings64.sh`, and `/opt/Xilinx/Vivado/*/settings64.sh`. If more than one candidate exists and no user-confirmed config is present, the gate fails with `TOOLCHAIN_SELECTION_REQUIRED=1` and prints the available choices.

Write a confirmed toolchain choice after the user selects a version:

```powershell
python -m scripts.python.remote.remote_validate_verilog_skill --settings .\config\defaults.json --server <selected-server> --write-toolchain-selection --simulator-backend xsim --vivado-settings /tools/Xilinx/<toolchain>/<version>/settings64.sh
```

The dedicated local selection file records only the selected `server_id`. The remote workdir `.readable-verilog-generator/.settings/verilog.remote.json` records the active simulator backend and optional `vivado_settings64`. A selected backend can also be `iverilog`; in that case Xilinx toolchain activation is skipped and validation uses the configured simulator priority override for that run.

For confidence-sensitive gates, the active server is always the one stored in `.settings/remote-selection.local.json` or explicitly passed through `--remote-server`. The toolchain source of truth is the remote workdir `.settings/verilog.remote.json`; local legacy toolchain caches must not satisfy the active gate.

Remote validation directories are retained by default and printed as `remote_parent` and `remote_skill`. Each new run uses a collision-resistant `validation_<UTC>_<time-ns>_<nonce8>` identity below the fixed server-default workdir root `.readable-verilog-generator/runs/`. The selected project directory is uploaded to `runs/<run-id>/workspace/readable-verilog-generator/` only through erie-remote-ssh `upload_contract_version=2` source-manifest directory upload; tar/tar.gz and whole-bundle archive transport are forbidden. The upload request and its `uploaded_verified` receipt bind the canonical `source_manifest_sha256` before the remote command starts. All execution logs and reports are written directly to `runs/<run-id>/reports/`, and Agent self-review is atomically written to `runs/<run-id>/agent_review.json`. The successful and failed reports remain available without manual review confirmation. The old `.readable-verilog-generator-validation/run-*` tree is retained for exact `--report-runs --run-id` read-only compatibility and is never migrated or written by the new flow. The legacy `--keep-remote` flag is accepted but no longer changes behavior because keeping is the default. `--cleanup-remote` remains an explicit opt-in compatibility action for deleting only the current retained run; the default path never deletes successful or failed evidence.

Each remote gate validates authoritative pytest, the canonical workflow, and the fixed RTL fixtures in `assets/examples/remote_fixtures`: `comb_operation_budget`, `comb_hierarchy_budget`, `comb_parity_mux`, `pipeline_delay`, and `ready_valid_slice`. `comb_operation_budget` first proves that its standalone bad source is rejected by VG146, then simulates the registered replacement and checks its visible two-edge latency, ready/backpressure behavior, stalled-output stability, and accepted-versus-delivered transaction conservation. `comb_hierarchy_budget` runs four independent source-closure probes before simulation: child 1 plus parent 2 operations passes VG146, child 2 plus parent 2 fails VG146 with count 4 and a child evidence path, a registered child Q cuts upstream expansion, and a child constant loop is owned and rejected by VG147. Its generated two-module design and self-checking testbench then compile and run with the selected simulator. Under the direct `runs/<run-id>/reports/` directory, `remote_pytest.log` retains the compatibility aggregate and `remote_pytest_summary.json` records the aggregate counts. The authoritative pytest evidence is phase-scoped and retained as `remote_pytest_targeted.log`/`remote_pytest_targeted_summary.json`, `remote_pytest_regression.log`/`remote_pytest_regression_summary.json`, and `remote_pytest_full.log`/`remote_pytest_full_summary.json`; the phases run in that order and each summary binds the exact command hash, exit code, timestamp, and counts. Immediately after the full summary and before smoke begins, `remote_post_pytest_phase.json` records the post-pytest phase start; `remote_post_pytest.log` captures smoke output through `tee`, and the marker is updated for smoke, workflow, fixture, implement, and evidence-runtime failures. `remote_environment.json`, `remote_cwd.json`, `skill_pressure_report.json`, `validation_archive_manifest.json`, and `remote_test_evidence.json` bind the server identity, actual working directory, pressure report, retained artifact inventory, and phase evidence; `validation_archive_manifest.json` is not an upload archive. `agent_review.json` records the Agent's automatic pass/fail review. Fixture reports are retained under `remote_fixtures/<fixture>/validation.json`, with a combined `remote_fixtures/summary.json` that records the selected simulator backend, executed tools, and generated RTL/testbench paths.

List retained runs without staging a new run:

```powershell
python -m scripts.python.remote.remote_validate_verilog_skill --settings .\config\defaults.json --server <selected-server> --report-runs
```

Automated callers pass `--run-id <run-id>` together with `--report-runs` so the report reads only the run identity returned by the immediately preceding validation command. The report downloads `completion.json` together with the retained phase summaries, runtime evidence, and canonical execution and fixture reports. After the TESTER has committed the exact `tests/**` tree, `--write-test-evidence <path> --run-id <run-id> --test-commit-sha <sha>` writes a canonical, self-hashed receipt that binds that test commit, source manifest, `uploaded_verified` receipt reference, remote fingerprints, retained artifact manifest, pressure report, and all three successful pytest phases. A confidence-sensitive `eval-skill --require-remote` result is green only when the completion marker matches the outer run and erie source-manifest digest, pytest has positive passing counts in every phase, xsim execution passes, and all fixture reports pass. Older retained runs without `completion.json`, phase summaries, or `remote_test_evidence.json` remain inspectable but are incomplete remote evidence.

The gate must use the highest simulator backend actually available on the selected server: Vivado xsim, then VCS+Verdi, then iverilog/vvp. If higher-priority simulators are later provided, the same gate must require the highest available backend instead of preserving an older fallback expectation. If `yosys` is not detected, implement readiness must block with `toolchain_issue`; if `yosys` is later provided, implement readiness must pass instead of preserving an older blocked expectation.

## Sensitive Data

Do not store real hostnames, usernames, key names, private-key paths, ports, packaged default server ids, or packaged server display names in this skill. Keep those fields in the server-list JSON consumed by `erie-remote-ssh`, and persist only user-confirmed selections in project-local state.
