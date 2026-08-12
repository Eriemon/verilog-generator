# Verilog readability gate checklist

Use this checklist after deterministic tools pass and before delivering generated or modified RTL. Final delivery requires `scripts/python/validation/verilog_generated_deliverable_gate.py` to report zero errors and zero strict warnings.

## Must answer yes

1. Does the file begin with ``timescale 1ns / 1ps` and the full bilingual Erie header?
2. Is the module header ANSI-style, with grouped ports and no `wire`/`reg`/`logic` keywords in the port list?
3. Do input/output/inout ports use `i_`/`o_`/`io_` prefixes, except approved Vitis wrapper ABI ports?
4. Do parameters, localparams, state parameters, registers, counters, flags, encoders, decoders, and internal output bridges follow the naming rules without repeated semantic prefixes?
5. Are registered or complex outputs driven through internal `_o` signals and explicit `assign o_* = *_o` bridges?
6. Does each sequential `always` block have one primary target, nonblocking assignments, and active-low reset handling?
7. Does each combinational `always` block use blocking assignments and cover all branches with safe defaults?
8. Is every FSM three-segment, with `ST_*`, `state_current`, `state_next`, and a `default` branch?
9. Are region banners present in the required order for the structures that exist in the module?
10. Are AST spans present for modules, parameters, ports, declarations, assigns, always blocks, instances, generate blocks, functions, and tasks?
11. Do AXI, AXIS, and APB ports follow the rulebook section order, including channel order after clock/reset?
12. Are comments Chinese-first, semantic, and attached to the code entity they describe rather than mechanically repeated on every syntax line?
13. Does the code avoid placeholder/template comments, hollow Chinese comments, and formatter fallback comments such as `parameter`, `port signal`, `signal`, `assign`, `output bridge`, and `internal output signal`?
14. Does the code avoid exact or near-duplicate entity comments after ignoring decorative separators, numbering, and identifier-only noise?
15. Did the formatter-backed deliverable gate parse all modules and report zero strict errors and zero strict warnings?
16. If the task was comment-only, did both comment-only verifications pass from the immutable baseline?

## Must answer no

1. Did any macro, include fragment, complex generate, unsupported non-Verilog-2001 construct, or multi-module file get normalized without an explicit gate decision?
2. Did formatting or annotation change module names, port lists, lvalues, reset polarity, always targets, or instance connections unexpectedly?
3. Are output ports assigned directly inside always blocks?
4. Are there inline wire assignments, block comments, trailing whitespace, space indentation, or missing final newline?
5. Are generic fallback comments still present where real signal meaning is known?
6. Are comments reused by changing only a number, endpoint letter, signal name, or other template shell?
7. Did validation rely on model self-assessment text instead of AST/static/tool evidence?
8. Did an include fragment, vendor/IP wrapper, or complex non-Verilog-2001 construct go through strong normalize without an explicit preserve/lint decision?
9. Do runtime code, scripts, or tests load fixtures from the temporary input area instead of `tests/cases`?

## Reviewer labels

- `BLOCKER`: cannot deliver without a fix or explicit user waiver.
- `WARNING`: deliverable only if the risk remains visible and is not a generated strict-mode error.
- `NOTE`: optional maintainability improvement.
