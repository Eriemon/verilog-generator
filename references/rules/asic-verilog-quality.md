# ASIC Verilog Quality Reference

Use this reference when generating, reviewing, or repairing Erie Verilog-2001 RTL for ASIC-oriented quality. This guidance is advisory unless validation reports an error; it does not expand the skill beyond Verilog-2001 `.v` artifacts.

## Review Priorities

1. Preserve the confirmed spec contract: top name, ports, widths, clock edge, reset polarity, latency, and interface family.
2. Keep RTL synthesizable and reviewable: no delays, no simulation system tasks, no force/release, no multiple drivers, and no hidden storage.
3. Make control and datapath easy to inspect: name pipeline registers, separate next-state logic when useful, and keep high-fanout enables visible.
4. Keep testbench code separate from RTL source. Simulation-only constructs belong only in files identified as testbenches.

## Reset And Clocking

- Follow the spec reset object exactly. If reset is synchronous, do not put reset in the sensitivity list. If reset is asynchronous, include the reset edge in the sensitivity list and use the active polarity explicitly.
- Prefer reset names that clearly show polarity, such as `rst_n`, `i_rstn`, or bus-specific Erie names.
- Do not create raw gated clocks with `assign gated_clk = clk & en`, `clk | test_en`, or similar logic. Use clock-enable RTL unless an approved clock-gating wrapper is explicitly specified by the user or platform flow.
- If the design has more than one clock or reset domain, record CDC/RDC assumptions in manifest checks and generate synchronizer or handshake logic only when the spec confirms it.

## Combinational Logic

- Use `always @(*)` for legacy Verilog combinational logic.
- Assign safe defaults before `if`, `case`, `casex`, or `casez` decisions so every output or next-state signal is driven on every path.
- Add an explicit `default` branch to every `case`, `casex`, and `casez` statement unless the spec gives a proven complete decode policy.
- Avoid `casex` unless the behavior truly requires don't-care matching; prefer plain `case` for deterministic simulation.

## Sequential Logic

- Use nonblocking assignments in clocked always blocks.
- Do not mix blocking and nonblocking assignments inside the same always block.
- Keep one clock domain per always block.
- For Erie strict style, keep the one-reg-per-always-block policy and route outputs through internal `_o` signals plus explicit assigns.

## CDC And RDC Notes

- Single-bit level crossings need a destination-domain synchronizer.
- Pulse crossings need a pulse-to-toggle synchronizer or request/acknowledge handshake.
- Multi-bit crossings need a stable-data handshake, async FIFO, or Gray-coded pointer scheme.
- Reset deassertion should be synchronized to each receiving clock domain when asynchronous reset is used.
- Avoid reconvergent use of separately synchronized signals unless the protocol accounts for skew.

## Timing-Reviewable Structure

- Name pipeline stages and valid bits clearly.
- Avoid long nested if chains when a small FSM or decode case is easier to inspect.
- Keep arithmetic widths explicit, including extension and truncation points.
- Avoid internal tri-state values; use muxes for ASIC-friendly logic.
- Treat high-fanout enables, resets, and ready/valid controls as timing-sensitive signals that should be easy to find in review.

## Static Lint Expectations

The Erie static lint layer is a fast heuristic gate, not a replacement for simulator, synthesis, or signoff tools. It should catch common quality problems early:

- RTL `function` or `task` blocks.
- Missing `case` defaults.
- Legacy combinational sensitivity lists that are not `always @(*)`.
- Mixed blocking and nonblocking assignment in one always block.
- Raw gated-clock assignments.
- Simulation-only constructs inside RTL source files.

Warnings should guide repair and review. Errors should block generated-artifact readiness until fixed.
