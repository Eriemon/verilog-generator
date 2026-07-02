# Verilog Comment Placement Contract

Use this reference when generating, reviewing, or validating comments in generated RTL and testbench `.v` files.

The comment gate checks placement and basic usefulness at the code-entity level. Parameters, ports, declarations, assigns, FSM branches, instances, generate blocks, and non-trivial procedural blocks need comments that explain intent, ownership, reset/enable behavior, or interface meaning. Do not add mechanical comments to every syntax-only line merely to satisfy density. Pure comments may introduce a block, but they do not satisfy unrelated entities.

## Strict Placement Rules

- Block comments are forbidden. Any `/*` or `*/` marker fails the gate; use `//` only.
- Signal definitions, including `reg`, `wire`, `integer`, `real`, `input`, `output`, and `inout`, require same-line right-side comments.
- Assign statements and assignments inside `always`, `function`, `task`, `generate`, or `initial` blocks require same-line right-side comments.
- `parameter` and `localparam` definitions require same-line right-side comments.
- Instance `.formal(actual)` parameter and port mappings require same-line right-side comments.
- Same-line `//` markers align to the current region banner right-side `//` display column. If the code is too long, the marker may move right, but it must not be left of that anchor.
- Leading comments for `always`, `function`, `task`, `generate`, `initial`, definition groups, and instances sit immediately above the target, align with the target start column, and have exactly one blank line above unless they directly follow a region banner.
- Parameter and signal definition sections require a pure group comment above each group. A region banner is only an anchor and does not satisfy the group-comment requirement. Module instances require a pure function comment above the instance.
- Entity comments must be unique to the hardware object they describe. Reusing the same sentence, a numbered variant, or a lightly edited template across parameters, ports, signals, assigns, procedural assignments, or instance mappings fails `VG066` in strict mode.

## Placement Matrix

- File header: keep the fixed bilingual header at the file top when `rtl_style_profile=erie_strict`; describe module purpose, version, revision date, and history there.
- Macro: single-line `define` uses a same-line comment. Multiline backslash macros use one pure leading comment bound to the macro name; continuation lines should not carry inline explanatory comments.
- Module: the `module <name>` declaration is covered by the fixed bilingual header and nearby module-purpose comment. `endmodule` may carry a close phrase when it improves readability, but do not add noise to trivial closures.
- Parameter and localparam: each definition uses a same-line comment explaining configurability or state meaning.
- Port: group protocol/channel ports with pure comments; each port line still uses a same-line comment explaining direction, role, validity condition, or width meaning.
- Signal: each `reg`, `wire`, `integer`, and `genvar` declaration uses a same-line comment explaining driver, purpose, or clock domain.
- Assign: each `assign` line uses a same-line comment explaining the left-hand output and source semantics.
- Region banner: banners are navigation only and never replace same-line comments on code statements.
- Always and initial: a pure leading block-purpose comment is required for non-trivial blocks; it should explain combinational/sequential intent, trigger, target register family, or test phase.
- FSM and case: each FSM block has a fixed block comment; `case` comments name the selector; every state/default branch explains the transition or output behavior when the branch is not self-evident.
- If and else: branch comments explain reset, enable, exception, or default behavior when that intent is not already clear from the nearby entity comment. Do not comment every `end` line mechanically.
- Instance: add a pure leading instance-purpose comment; instance, parameter mapping, and port mapping lines use same-line comments.
- Generate: branches use `gen_` labels and same-line comments; `endgenerate` names the generated structure being closed.
- Task and function: generated RTL forbids `task` and `function`. Testbenches may use helpers only with a pure leading purpose comment plus same-line comments on declaration, body, local signals, and end lines.
- Testbench statements: stimulus, checks, PASS/FAIL reporting, timeout, waveform setup, and finish calls use same-line comments explaining verification purpose.

## Rejected Comment Forms

- Generic filler such as "line comment", "generic comment", "placeholder", "reset", "state task", or "bypass path".
- Formatter fallback comments such as `parameter`, `port signal`, `signal`, `assign`, `output bridge`, or `internal output signal`.
- Hollow Chinese comments whose meaning is only "Chinese comment", "signal comment", "logic handling", "module logic", "data handling", or "port description".
- Exact or near-duplicate entity comments after removing decorative dashes, numbering, identifier-only noise, and zero-width characters.
- A pure comment placed between two code lines and reused for both.
- Mechanical line-by-line comments that restate syntax without design meaning.
- A continuation comment on a multiline macro that can hide or break backslash continuation semantics.
