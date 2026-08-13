# Testbench Patterns

Use this reference when generating or reviewing Verilog-2001 self-checking testbench scaffolds for the Erie workflow. Synthesizable RTL and verification testbenches remain Verilog-2001 `.v` artifacts.

## In Scope

- Simple directed testbenches for module-level bring-up
- Self-checking PASS/FAIL reporting backed by real comparisons
- Reset, nominal behavior, boundary conditions, and timeout coverage
- Stable vector-hash comments so downstream validation can line up reference cases

## Out Of Scope

Synthesizable source RTL remains Verilog-only. Interface-heavy verification styles and class-based verification environments are out of the current skill boundary. They may be mentioned for comparison, but they are not generation targets.

## Simple Directed Testbench

Use this structure for small modules and quick validation:

1. Declare clock, reset, DUT inputs, and DUT outputs.
2. Generate the main clock with `always #(CLK_PERIOD/2)`.
3. Apply reset through a dedicated task when the design has a reset signal.
4. Initialize every driven DUT input before the first active cycle.
5. Run a small set of named stimulus cases.
6. Print explicit `PASS` or `FAIL` messages for every checked case only after a real comparison has executed.
7. Add a watchdog timeout so a hung simulation terminates cleanly.

## Self-Checking Testbench

Use a self-checking testbench whenever the DUT contract is deterministic enough for direct comparisons.

## Self-Checking Expectations

- Compare observed outputs against known expected values.
- Emit one explicit `PASS` path and one explicit `FAIL` path.
- Instantiate the DUT, drive at least one non-clock/non-reset input, and route failures through `$error`, `$fatal`, `$finish_and_return`, or explicit `FAIL` handling.
- Keep checks local and readable; do not hide basic comparisons inside elaborate helpers.
- Preserve any semantic vector hash comment required by the workflow.
- When a semantic contract is present, mention every required case id and compare every checkpoint key against an expected value.

## Scaffold Without Expectations

When `python -m scripts.python.generation.tb_generator` is used without expectations or vector JSON, it must emit a scaffold that blocks success with a clear `$fatal` until the user fills module-specific expected values. It must not print `PASS`, must not contain a dummy never-failing comparison, and must not be treated as validation evidence.

## Minimal Checklist

- Clock and reset behavior matches the DUT contract.
- All DUT inputs are driven.
- Reset and nominal behavior are both exercised.
- Boundary or corner conditions are represented.
- Timeout handling exists.
- PASS and FAIL strings are easy to grep from simulator output, and PASS is reachable only after comparison code.
- Testbenches are compiled as Verilog `.v` files when an external backend is used.

## Comparison Note

Large class-based verification environments can be useful on complex projects, but they are intentionally outside this skill's generation boundary. If a request truly needs that style, treat it as a separate capability decision rather than stretching this Verilog-only skill.
