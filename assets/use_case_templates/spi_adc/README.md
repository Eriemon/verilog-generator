# SPI ADC Use-Case Template

This bundle captures the recurring board-level pattern used by SPI ADC projects such as AD40xx, AD469x, and AD7134.

## Scope

- Triggered or periodic ADC conversion launch
- SPI Engine style serial control path
- BUSY edge capture or ODR handoff
- DMA-backed sample collection into processor memory

## Parameterization Points

- ADC resolution and packed sample width
- Number of channels or SDI lanes
- Sampling rate and SPI reference clock
- Trigger policy: CNV pulse, ODR, or BUSY edge
- Board family and GPIO banking

## Provenance Notes

- `system_top.v` is distilled from the top-level wiring style used by AD469x-class SPI ADC projects.
- `common_bd.tcl` combines the CNV/BUSY gating and DMA structure used across AD40xx, AD469x, and AD7134.
- `system_bd.tcl` and `system_project.tcl` keep only the reusable board flow skeleton and remove project-specific noise.
