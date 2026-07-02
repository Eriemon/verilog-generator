# Verilog Dispatcher Workflow

## Entry decision

Treat a request as an Erie Verilog Generator task when it asks for Verilog, RTL, `.v`, `.sv`, FPGA, ASIC, modules, finite-state machines, AXI, AXIS, APB, SPI, I2C, UART, or a hardware interface/register behavior description.

## Task classes

Mark at least one of these classes in the execution plan:

- `generate`: create new synthesizable RTL or a testbench scaffold from a confirmed specification.
- `modify`: change, repair, normalize, or refactor existing RTL.
- `comment`: add, translate, rewrite, or optimize comments only.
- `analyze`: inspect existing RTL, interfaces, risk, or style differences without writing source files.
- `validate`: run static gates, formatter-AST gates, comment-only gates, or optional external simulation/synthesis readiness checks.

Every triggered task must name the applicable check matrix: `verilog_generated_deliverable_gate`, `formatter_ast_quality_gate`, `style_naming_gate`, `comment_quality_gate`, `comment_only_gate`, `static_lint`, `interface_contract_gate`, and `optional_external_validation`.

## Execution matrix

### `generate`

Confirm the intent contract first: module name, ports, clock/reset, interface family, behavior, latency/pipeline expectations, reset values, test cases, and deliverable files. Generated RTL must pass the final deliverable gate; model self-review is never enough.

### `modify`

Build a formatter-AST report and risk score for the original RTL before deciding whether to preserve, micro-format, normalize, or fail without writing. Any change touching interfaces, port names, reset polarity, always-block splitting, FSMs, or output bridges must report risk and verification evidence.

### `comment`

Run exactly: `format baseline -> comment draft -> verify comment-only -> deliverable gate -> format final -> verify comment-only`. The script only proves RTL tokens did not change; the agent must still write entity-level comments from real code intent and must not emit template, hollow, or fallback placeholders.

### `analyze`

Read-only analysis must not write source files. It may output AST summaries, style deltas, risk grades, and suggested commands, but suggestions are not executed work.

### `validate`

At minimum, run `scripts/verilog_generated_deliverable_gate.py`. Use strict mode for generated deliverables. Use `scripts/verilog_quality_gate.py` for focused VG debugging only. Use `--non-strict --warn-only` only for historical reference corpora that now live under `tests/cases/ideal/rtl` and `tests/cases/bad/rtl`.

Repository regression fixtures live under `tests/cases`. Tests and runtime validation must read that stable corpus and must not rely on the temporary input area used during one-time corpus migration.

## Safe repair boundary

- Do not guess rewrites when the AST cannot identify modules, ports, always blocks, instances, or preprocessor-sensitive structure.
- Do not change RTL tokens, lvalues, ports, instance connections, reset polarity, or always targets during a comment-only workflow.
- Do not drive top-level outputs directly from sequential logic; use an internal output signal and an assign bridge.
- Do not split a multi-target always block unless the AST and normalization path can prove the split is safe enough for the selected profile.
- Do not treat non-strict legacy analysis as approval for generated deliverables.
- Do not classify a `*_good.v` compatibility fixture from the bad corpus as a strict negative case unless its manifest entry explicitly says so.
