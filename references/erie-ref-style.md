# Erie Verilog Style Extraction

## Table of Contents

- [Naming Rules](#naming-rules)
- [Comment Rules](#comment-rules)
- [State Machine Rules](#state-machine-rules)
- [Module Instantiation Rules](#module-instantiation-rules)
- [Port And Bus Grouping Rules](#port-and-bus-grouping-rules)
- [Standard-Bus-Specific Defaults](#standard-bus-specific-defaults)
- [Convolution-Domain Examples, Not Universal Defaults](#convolution-domain-examples-not-universal-defaults)

This document captures stable Verilog style rules extracted from the non-`projects` Erie source corpus:

- `AXI_Interconnect_Interface.v`
- `AXI_Lite_CFG_Interface.v`
- `AXIS_FIFO_Interface.v`
- `Conv1D_Interface.v`
- `Convolution_Interface.v`

The goal is to strengthen `rtl_style_profile=erie_strict` using recurring Erie conventions without turning domain-specific convolution controller wiring into the default for every design.

Coverage map:

- naming rules
- comment rules
- state machine rules
- module instantiation rules
- port and bus grouping rules
- standard AXI/AXIS/APB/AHB defaults
- convolution-domain examples that are not universal defaults

## Naming Rules

- Module names prefer descriptive PascalCase names with `_Interface` suffix for bus or controller wrapper modules.
- Parameters prefer `C_` uppercase names for configurable constants.
- State localparams prefer `ST_` uppercase names.
- Port names prefer explicit role prefixes:
  - `i_` for inputs
  - `o_` for outputs
  - `io_` for bidirectional ports
- Internal register and wire names prefer semantic prefixes such as:
  - `reg_`
  - `cnt_`
  - `state_`
  - `flag_`
  - `enc_`
  - `dec_`
- Internal output-driving signals prefer `_o` suffixes when outputs are routed through explicit `assign` statements.
- Standard bus clocks and resets prefer Erie bus-family naming such as:
  - `i_clk`, `i_rstn`
  - `i_axi_aclk`, `i_axi_arstn`
  - `i_axis_aclk`, `i_axis_arstn`
  - `i_ahb_hclk`, `i_ahb_hrstn`
  - `i_apb_pclk`, `i_apb_prstn`

## Header And Comment Rules

- Use a fixed bilingual file header:
  - English section first
  - Chinese section second
- Both sections should carry the same major metadata families:
  - company / ownership
  - engineer
  - create date
  - design name
  - module name
  - description
  - simulation project
  - references
  - dependencies
  - version
  - revision date
  - revision history
- Inline explanatory comments are expected near:
  - parameter definitions
  - port declarations
  - localparams
  - reg / wire declarations
  - assign statements
  - always blocks
  - module instantiations
- Default inline prose should be Chinese, while signal names, protocol names, identifiers, and proper nouns may remain in English.

## State Machine Rules

- FSMs strongly prefer explicit `state_current` and `state_next` registers.
- State encoding should use `localparam ST_*`.
- State-machine organization should prefer the Erie three-block shape:
  - combinational next-state decision
  - sequential state transition register
  - separate output / task processing blocks
- Even when the source is not fully abstracted into a textbook three-block FSM, the naming pattern remains stable and should be preserved.

## Module Instantiation Rules

- Module instance names prefer `_Inst` suffixes.
- `generate` branches prefer explicit labels starting with `gen_`.
- Instances usually use named port mapping rather than positional mapping.
- Instance port lists are grouped and commented by channel or subsystem role.

## Port And Bus Grouping Rules

- AXI / AXIS / APB / AHB interfaces are grouped by channel and role, not as one flat undifferentiated port list.
- Common grouping style includes labeled blocks such as:
  - write address
  - write data
  - write response
  - read address
  - read data
  - control channel
  - data channel
- For repeated bus endpoints, Erie sources often repeat the same channel comment structure per endpoint.
- Standard bus template generation should preserve grouped port order, grouped comments, and family-specific clock/reset naming.

## Region Order Rules

Recurring Erie region labels include combinations of:

- configuration parameter region
- state parameter region
- instantiation signal region
- counter signal region
- state-machine signal region
- register signal region
- flag signal region
- encoder signal region
- decoder signal region
- other signal region
- output signal region
- other assign region
- output assign region
- output processing region
- state-machine region
- state transition / state task region
- main task / datapath processing region
- module instantiation region

Exact wording may vary slightly by file, but the high-level structure is stable and should be preserved for `erie_strict`.

## Standard-Bus-Specific Defaults

- AXI / AXIS / APB / AHB wrappers should preserve Erie-style channel grouping and port naming.
- Bus configuration wrappers often use explicit decode-style assigns for register channels and per-channel valid/read-enable signals.
- Template guidance should treat bus naming, grouping, and comment structure as part of the interface contract, not just the raw port list.

## Convolution-Domain Examples, Not Universal Defaults

The following patterns are common in `Conv1D_Interface.v` and `Convolution_Interface.v`, but should not be promoted to universal defaults for all generated RTL:

- `cfg_*` register-map ecosystems
- `fifo_*` controller and datapath naming ecosystems
- convolution-specific `row/col/kernel/padding/stride` register families
- convolution-controller bus decode and accelerator orchestration structure

These are useful review examples and future domain-specific templates, but they are not the generic Erie default for unrelated RTL.
