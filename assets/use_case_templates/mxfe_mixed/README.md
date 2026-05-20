# MxFE Mixed Use-Case Template

This bundle captures the recurring mixed RF ADC and RF DAC pattern used by MxFE projects such as AD9081 X-band designs.

## Scope

- Coordinated RX and TX JESD paths
- Shared RF transceiver tuning parameters
- PMOD and FMC control sideband buses
- TDD synchronization and enable signals

## Parameterization Points

- RX and TX lane rates
- Number of links
- RX and TX samples per channel
- PMOD or FMC control bus widths
- TDD enable and synchronization strategy

## Provenance Notes

- `common_bd.tcl` keeps the board-level orchestration pattern used by AD9081 X-band flows.
- `system_top.v` is a compact wrapper that exposes RX and TX status, PMOD control, and TDD sideband hooks.
