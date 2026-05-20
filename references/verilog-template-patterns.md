# Verilog Template Patterns

## Purpose

Use these refined local templates when the task needs compact, reusable Verilog
design patterns derived from the local repository reference RTL instead of copying
large source files verbatim.

## Available Patterns

- `axi4_lite_csr_shell`: use for small control or status register banks.
- `axis_ready_valid_slice`: use for one-stage ready/valid or AXIS pipeline cuts.
- `axi_interconnect_port_groups`: use for AXI4 channel grouping and parameterized
  interconnect or DMA front ends.
- `conv_load_store_pipeline`: use for convolution-oriented IFM or OFM buffering,
  window advance, and load/store stage partitioning.

## Usage Rules

- Treat every refined template as a hint, not a drop-in module.
- Preserve the task's confirmed interface and reset contract.
- Keep comments and paths ASCII-safe inside installable skill assets.
- Do not copy historical project paths, encoded garbage text, or full reference
  modules into generated outputs.
- Record any template-driven adaptation in generation checks or codegen plan
  evidence.
