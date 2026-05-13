# Engineering Design Goals

## Why This Skill Exists

The previous skill mixed Verilog RTL generation with HLS-oriented flows. That made triggering ambiguous, inflated the runtime with unrelated C/C++ concerns, and increased the chance that a hardware task would drift into an unintended implementation style. This project exists to provide a narrow, dependable Verilog-2001 generation skill.

The design goal is not to be a general hardware generator. It is to help Codex reliably turn a confirmed hardware specification into synthesizable Verilog RTL, a self-checking Verilog testbench, and validation evidence that can be inspected by a human or host application.

## How The Skill Should Work

The skill combines four standard skill design patterns:

- Inversion: confirm requirements before code generation.
- Pipeline: run fixed stages in order: requirements, codegen plan, Python reference model, Verilog RTL.
- Generator: use structured specs and manifest-based fenced outputs so files are stable and extractable.
- Reviewer: validate syntax, interface shape, reset/clock behavior, placeholders, reviewability, simulator readiness, and optional implementation readiness.

The Python runtime is kept because deterministic scaffolding, prompt rendering, extraction, validation, tracing, and resume behavior are too fragile to repeat only through prose instructions. The runtime must remain Verilog-only at its public interfaces and internal target checks.

Independent static lint and testbench scaffold helpers are part of the overall skill flow, but they are optional helper steps rather than mandatory gates. Strict quality control remains mandatory and is anchored by the staged workflow validation chain.

## Scope Boundaries

- Generate only Verilog-2001 `.v` artifacts.
- Keep `target` in specs for compatibility, but accept only `rtl`.
- Reject HLS, C/C++ kernel, Vitis, and SystemVerilog generation requests.
- Prefer Vivado xsim for external simulation, fall back to VCS+Verdi, then to iverilog/vvp; use `yosys` only for implementation readiness.
- Keep local and remote validation paths configurable through JSON settings; remote validation must use `erie-remote-ssh` and server-list JSON rather than direct SSH/SCP logic.
- Keep all implementation and generated support files inside this skill folder or caller-selected run directories.
- Keep helper scripts optional: `scripts/verilog_lint.py` and `scripts/tb_generator.py` must never become mandatory prerequisites for the main workflow.

## Acceptance Criteria

- `SKILL.md` and `agents/openai.yaml` describe only Verilog generation.
- `integration/verilog_adapter.py` is the only public integration facade.
- The CLI no longer requires a target argument and always resolves to Verilog RTL.
- HLS examples and runtime modules are removed.
- Smoke tests cover successful Verilog workflow execution, target rejection, prompt cleanliness, and Verilog-only artifact validation.
- Smoke tests prove helper-tool placement and policy: independent static lint and testbench scaffold are available, optional, and do not replace the mandatory quality gate.
- Smoke tests cover simulator fallback order with fake tools and local preflight behavior that requires remote selection when Vivado/xsim is unavailable.
- Remote confidence validation covers `erie-remote-ssh` discovery, selected-server checks, software scan, workspace checks, request-based staging, and simulator fallback on the selected server.
- `quick_validate.py` passes for the skill folder.
