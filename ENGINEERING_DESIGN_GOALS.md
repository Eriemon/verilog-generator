# Engineering Design Goals

## Why This Skill Exists

The previous skill mixed Verilog RTL generation with HLS-oriented flows. That made triggering ambiguous, inflated the runtime with unrelated C/C++ concerns, and increased the chance that a hardware task would drift into an unintended implementation style. This project exists to provide a narrow, dependable Verilog-2001 generation and existing-RTL analysis skill.

The design goal is not to be a general hardware generator. It is to help Codex reliably turn a confirmed hardware specification into synthesizable Verilog RTL, offer regular and deep-review generation modes, stream or batch supported generation runs without losing evidence, analyze existing RTL into stable contracts and design explanations, emit a self-checking testbench, drive a bounded verify-repair loop for existing RTL, support wrapper-first merge assist, and preserve validation evidence that can be inspected by a human or host application.

## How The Skill Should Work

The skill combines five standard skill design patterns:

- Tool Wrapper: wrap the bundled runtime, helper scripts, references, and templates behind one stable Verilog-first skill entry point.
- Inversion: confirm requirements before code generation.
- Pipeline: run fixed stages in order for generation, with a mode-aware review stage for deep-review generation.
- Pipeline: run fixed stages in order for generation, plus controlled analysis/refinement/verify-repair subflows for existing RTL.
- Generator: use structured specs and manifest-based fenced outputs so files are stable and extractable.
- Reviewer: validate syntax, interface shape, reset/clock behavior, placeholders, reviewability, simulator readiness, and optional implementation readiness.

The Python runtime is kept because deterministic scaffolding, prompt rendering, extraction, validation, tracing, semantic comparison, and resume behavior are too fragile to repeat only through prose instructions. The runtime must remain Verilog-first at its public interfaces and internal target checks.

SiliconMind-style interaction ideas are welcome only at the workflow-shell level. This skill may present regular, deep-review, or agentic-repair modes, but it must not treat free-form model claims such as `[DESIGN IS CORRECT]` as a release or validation gate.

Independent static lint and testbench scaffold helpers are part of the overall skill flow, but they are optional helper steps rather than mandatory gates. Strict quality control remains mandatory and is anchored by the staged workflow validation chain.

## Scope Boundaries

- Generate only Verilog-2001 `.v` code artifacts for new RTL, while allowing JSON/Markdown evidence artifacts for analysis, refinement, verify-repair, and comparison flows.
- Keep `target` in specs for compatibility, but accept only `rtl`.
- Reject HLS, C/C++ kernel, Vitis, and SystemVerilog design-generation requests. Verification testbenches may use SystemVerilog when the verify-repair flow needs assertion/property support.
- Prefer Vivado xsim for external simulation, fall back to VCS+Verdi, then to iverilog/vvp; use `yosys` only for implementation readiness.
- Keep local and remote validation paths configurable through JSON settings; remote validation must use `erie-remote-ssh` and server-list JSON rather than direct SSH/SCP logic.
- Keep all implementation and generated support files inside this skill folder or caller-selected run directories.
- Keep helper scripts optional: `scripts/verilog_lint.py` and `scripts/tb_generator.py` must never become mandatory prerequisites for the main workflow.
- Keep `verify_existing_verilog(...)` as the stable repair entrypoint and present it as an agentic-repair mode rather than inventing a second mutation loop.

## Acceptance Criteria

- `SKILL.md` and `agents/openai.yaml` describe new RTL generation plus existing-RTL analysis/refinement/verify-repair boundaries without widening the design RTL domain beyond Verilog.
- `SKILL.md`, `agents/openai.yaml`, and `references/integration.md` describe `regular`, `deep_review`, and `agentic_repair` behavior clearly enough that a host can route requests without reading runtime internals.
- `integration/verilog_adapter.py` is the only public integration facade.
- `integration/verilog_adapter.py` exposes `run_verilog_batch(...)` for generation-only batch execution and keeps each case in an isolated run directory.
- The CLI no longer requires a target argument and always resolves to Verilog RTL.
- The CLI exposes `--generation-mode regular|deep_review`, `--stream/--no-stream`, and `run-batch` while preserving the existing `verify-existing` command name.
- HLS examples and runtime modules are removed.
- Smoke tests cover successful generation workflow execution, existing-RTL analysis/refinement/verify-repair flows, merge-assist planning, target rejection, prompt cleanliness, and Verilog artifact validation.
- Smoke tests and eval cases cover deep-review stage insertion, streaming transcripts, and batch aggregation without weakening the current diagnostics pack contract.
- Smoke tests prove helper-tool placement and policy: independent static lint and testbench scaffold are available, optional, and do not replace the mandatory quality gate.
- Smoke tests cover simulator fallback order with fake tools and local preflight behavior that requires remote selection when Vivado/xsim is unavailable.
- Remote confidence validation covers `erie-remote-ssh` discovery, selected-server checks, active-server toolchain coherence, software scan, workspace checks, request-based staging, and simulator fallback on the selected server.
- `quick_validate.py` passes for the skill folder.
