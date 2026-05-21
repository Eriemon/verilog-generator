# Verilog Comment Placement Contract

Use this reference when generating, reviewing, or validating comments in generated RTL and testbench `.v` files.

The comment gate checks placement and basic usefulness. A code line without a same-line explanatory comment is invalid unless it is a multiline macro continuation protected by a pure leading macro comment. Pure comments may introduce a block, but they do not satisfy the ordinary same-line requirement for unrelated statements.

## Placement Matrix

- File header: keep the fixed bilingual header at the file top when `rtl_style_profile=erie_strict`; describe module purpose, version, revision date, and history there.
- Macro: single-line `define` uses a same-line comment. Multiline backslash macros use one pure leading comment bound to the macro name; continuation lines should not carry inline explanatory comments.
- Module: the `module <name>` line uses a same-line comment naming the module or testbench purpose. `endmodule` starts its comment with an end/close phrase and names the closed module/testbench.
- Parameter and localparam: each definition uses a same-line comment explaining configurability or state meaning.
- Port: group protocol/channel ports with pure comments; each port line still uses a same-line comment explaining direction, role, validity condition, or width meaning.
- Signal: each `reg`, `wire`, `integer`, and `genvar` declaration uses a same-line comment explaining driver, purpose, or clock domain.
- Assign: each `assign` line uses a same-line comment explaining the left-hand output and source semantics.
- Region banner: banners are navigation only and never replace same-line comments on code statements.
- Always and initial: a pure leading block-purpose comment is recommended; the opener line still uses a same-line comment explaining combinational/sequential intent, trigger, target register family, or test phase.
- FSM and case: each FSM block has a fixed block comment; `case` comments name the selector; every state/default branch explains the transition or output behavior.
- If, else, and end: branch and close lines explain reset, enable, exception, default, or close purpose.
- Instance: add a pure leading instance-purpose comment; instance, parameter mapping, and port mapping lines use same-line comments.
- Generate: branches use `gen_` labels and same-line comments; `endgenerate` names the generated structure being closed.
- Task and function: generated RTL forbids `task` and `function`. Testbenches may use helpers only with a pure leading purpose comment plus same-line comments on declaration, body, local signals, and end lines.
- Testbench statements: stimulus, checks, PASS/FAIL reporting, timeout, waveform setup, and finish calls use same-line comments explaining verification purpose.

## Rejected Comment Forms

- Generic filler such as "line comment", "generic comment", "placeholder", "reset", "state task", or "bypass path".
- A pure comment placed between two code lines and reused for both.
- An end comment that does not start with an end/close phrase or does not identify the closed construct.
- A continuation comment on a multiline macro that can hide or break backslash continuation semantics.
