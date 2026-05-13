# Testbench Patterns

Use this reference when generating or reviewing Verilog-2001 self-checking testbench scaffolds for the Erie workflow.

## In Scope

- Simple directed testbenches for module-level bring-up
- Self-checking PASS/FAIL reporting
- Reset, nominal behavior, boundary conditions, and timeout coverage
- Stable vector-hash comments so downstream validation can line up reference cases

## Out Of Scope

The current skill remains Verilog-only. Interface-heavy verification styles and class-based verification environments are out of the current skill boundary. They may be mentioned for comparison, but they are not generation targets.

## Simple Directed Testbench

Use this structure for small modules and quick validation:

1. Declare clock, reset, DUT inputs, and DUT outputs.
2. Generate the main clock with `always #(CLK_PERIOD/2)`.
3. Apply reset through a dedicated task when the design has a reset signal.
4. Initialize every driven DUT input before the first active cycle.
5. Run a small set of named stimulus cases.
6. Print explicit `PASS` or `FAIL` messages for every checked case.
7. Add a watchdog timeout so a hung simulation terminates cleanly.

## Self-Checking Testbench

Use a self-checking testbench whenever the DUT contract is deterministic enough for direct comparisons.

## Self-Checking Expectations

- Compare observed outputs against known expected values.
- Emit one explicit `PASS` path and one explicit `FAIL` path.
- Keep checks local and readable; do not hide basic comparisons inside elaborate helpers.
- Preserve any reference vector hash comment required by the workflow.

## Minimal Checklist

- Clock and reset behavior matches the DUT contract.
- All DUT inputs are driven.
- Reset and nominal behavior are both exercised.
- Boundary or corner conditions are represented.
- Timeout handling exists.
- PASS and FAIL strings are easy to grep from simulator output.

## Comparison Note

Large class-based verification environments can be useful on complex projects, but they are intentionally outside this skill's generation boundary. If a request truly needs that style, treat it as a separate capability decision rather than stretching this Verilog-only skill.
