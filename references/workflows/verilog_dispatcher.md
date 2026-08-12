# Verilog Dispatcher Workflow

## Entry decision

Treat a request as a Readable Verilog Generator task when it asks for Verilog, RTL, `.v`, FPGA, ASIC, modules, finite-state machines, AXI, AXIS, APB, SPI, I2C, UART, or a hardware interface/register behavior description.

Requests for other HDL dialect files are out of scope for generation and repair. Route them to a clear unsupported-dialect finding instead of widening this skill boundary.

## Task classes

Mark at least one of these classes in the execution plan:

- `generate`: create new synthesizable RTL or a testbench scaffold from a confirmed specification.
- `modify`: change, repair, normalize, or refactor existing RTL.
- `comment`: add, translate, rewrite, or optimize comments only.
- `analyze`: inspect existing RTL, interfaces, risk, or style differences without writing source files.
- `validate`: run static gates, formatter-AST gates, comment-only gates, or optional external simulation/synthesis readiness checks.

Every triggered task must name the applicable public delivery matrix gates: `compile`, `ast`, `readability`, `comment`, `naming`, `profile`, `testbench`, and `toolchain`. Internal tools such as formatter AST, quality gate, comment gate, static lint, interface gate, and optional external validation are evidence sources for that matrix; they are not the public delivery contract.

Run public CLI examples from the skill root. Every path argument must be visible to the current Python runtime that is executing the command. Do not expect automatic path translation between Windows drive-letter paths and POSIX-mounted paths.

When implementation work touches Python, first use `readable-python-generator`: classify the task, state the functional intent contract, follow current-project Chinese style, and pass strict readable-python gates before claiming VerilogGenerator readiness. When implementation work touches bat/cmd, shell/bash, PowerShell, or Tcl, first use `readable-script-generator`: identify the language, classify the task, state the script intent contract, and pass strict readable-script gates. Mixed changes must satisfy both paths.

## Execution matrix

### `generate`

Confirm the intent contract first: module name, ports, clock/reset, interface family, behavior, latency/pipeline expectations, reset values, test cases, and deliverable files. Generated RTL must pass the final deliverable gate; model self-review is never enough.

### `modify`

Build a formatter-AST report and change-risk review for the original RTL before deciding whether to preserve, micro-format, normalize, or fail without writing. Any change touching interfaces, port names, reset polarity, always-block splitting, FSMs, or output bridges must report risk and verification evidence.

Existing RTL modify requests require a real source target. If the target RTL or required context is missing, fail with `TARGET_OR_CODE_REQUIRED` or the nearest equivalent rule. Do not generate placeholder RTL or directly replaceable template patches for an unspecified asset.

### `comment`

Run exactly: `format baseline -> comment draft -> verify comment-only -> deliverable gate -> format final -> verify comment-only`. The script only proves RTL tokens did not change; the agent must still write entity-level comments from real code intent and must not emit template, hollow, or fallback placeholders.

### `analyze`

Read-only analysis must not write source files. It may output AST summaries, style deltas, review-priority notes, and suggested commands, but suggestions are not executed work.

The public read-only review entrypoint is `python -m scripts.python.workflow.cli review --target <input.v> --report-json review.json`.

Existing-asset analysis and reports may locate issues and explain risk, but they must not output drop-in replacement code unless the user has explicitly requested modify/repair and supplied the real target asset.

### `validate`

At minimum, run `python -m scripts.python.validation.verilog_generated_deliverable_gate`. Use strict mode for generated deliverables. Use `python -m scripts.python.quality.verilog_quality_gate` for focused VG debugging only. Use `--non-strict --warn-only` only for historical reference corpora that now live under `tests/cases/ideal/rtl` and `tests/cases/bad/rtl`.

The deliverable gate must expose the eight public checks: `compile`, `ast`, `readability`, `comment`, `naming`, `profile`, `testbench`, and `toolchain`. `compile` means local static parser/lint evidence. Simulator compile, execution, synthesis, and remote validation belong to `toolchain`; they are optional unless the user requests them or configuration enables them, and any requested failure blocks delivery.

Repository regression fixtures live under `tests/cases`. Tests and runtime validation must read that stable corpus and must not rely on the temporary input area used during one-time corpus migration.

## Safe repair boundary

- Do not guess rewrites when the AST cannot identify modules, ports, always blocks, instances, or preprocessor-sensitive structure.
- Do not change RTL tokens, lvalues, ports, instance connections, reset polarity, or always targets during a comment-only workflow.
- Do not drive top-level outputs directly from sequential logic; use an internal output signal and an assign bridge.
- Do not split a multi-target always block unless the AST and normalization path can prove the split is safe enough for the selected profile.
- Do not treat non-strict legacy analysis as approval for generated deliverables.
- Do not classify a `*_good.v` compatibility fixture from the bad corpus as a strict negative case unless its manifest entry explicitly says so.
- Do not claim repair completion until readability and syntax/AST gates pass and strict delivery warnings are clear.
