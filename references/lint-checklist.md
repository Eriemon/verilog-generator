# RTL Lint Checklist

Use this reference for independent static lint, code review, and repair work on Verilog-2001 RTL.

## Category A: Synthesis Errors

| Code | Check | Fix |
|------|-------|-----|
| `A0` | Generated `.v` code line lacks a same-line semantic explanatory comment in the requested language | Add a construct-bound Chinese comment when `comment_language=zh`; blank lines and pure comment lines are exempt |
| `A1` | Legacy combinational block does not use `always @(*)` | Use `always @(*)` or convert the logic shape |
| `A2` | Blocking and nonblocking assignments are mixed in one always block | Split combinational and sequential intent |
| `A3` | `case` statement has no explicit `default` | Add a safe `default` branch |
| `A4` | Multiple drivers feed the same net | Keep one driver per signal |
| `A5` | Outputs or next-state signals are not fully driven | Add defaults before the branch tree |
| `A6` | Async reset is not modeled with the reset edge in the sensitivity list | Match the reset object exactly |
| `A7` | Simulation-only `initial` block appears in RTL source | Keep `initial` blocks in testbench files only |
| `A8` | Delay controls appear in RTL source | Remove `#delay` from synthesizable logic |

## Category B: Quality Warnings

| Code | Check | Why It Matters |
|------|-------|----------------|
| `B1` | Latch inference risk | Hidden state hurts timing closure and reviewability |
| `B2` | Unreachable or dead logic | Hides real behavior and weakens review |
| `B3` | Undeclared or implicit nets | Make declarations explicit and stable |
| `B4` | Width mismatch or truncation risk | Can silently corrupt datapath behavior |
| `B5` | Signed and unsigned mixing | Produces surprising arithmetic results |
| `B6` | High-fanout enable or control path is obscured | Makes timing review harder |
| `B7` | Legacy combinational sensitivity list | Can hide simulation mismatches |
| `B8` | Testbench and RTL responsibilities are mixed | Keep verification constructs separate |

## Category C: ASIC Quality Review

| Code | Check | Expected Direction |
|------|-------|--------------------|
| `C1` | No raw gated clocks | Use clock-enable RTL or an approved wrapper |
| `C2` | Reset polarity and style match the spec | Do not improvise reset semantics |
| `C3` | CDC and RDC assumptions are documented | Record assumptions in checks or review notes |
| `C4` | Pipeline stages and valid controls are reviewable | Name signals clearly and keep control visible |
| `C5` | Arithmetic widths are explicit | Show extension and truncation points |
| `C6` | Internal tri-state behavior is avoided | Prefer muxed logic |

## CDC And RDC Checklist

- Identify every clock domain in the design.
- Use a destination-domain synchronizer for single-bit level crossings.
- Use a handshake, toggle synchronizer, or FIFO for pulse and multi-bit crossings.
- Synchronize reset deassertion per receiving clock domain when reset is asynchronous.
- Avoid reconvergent use of independently synchronized controls unless the protocol accounts for skew.

## External Lint Tools

When available, independent lint may also run one external parser-based tool:

- `verible-verilog-lint`
- `verilator --lint-only`
- `slang --lint-only`

Use the external result as an additional signal, not as a replacement for the Erie review rules.
