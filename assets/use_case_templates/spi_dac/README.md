# SPI DAC Use-Case Template

This bundle captures the recurring board-level pattern used by SPI DAC projects such as AD5758 and AD5766.

## Scope

- SPI DAC register and streaming control
- Optional cyclic DMA feed for playback
- GPIO sideband control such as fault, reset, and LDAC
- Board-level top and block-design scaffolding

## Parameterization Points

- DAC sample width
- Number of DAC channels
- DMA cyclic policy
- GPIO sideband presence
- Board family and voltage standard

## Provenance Notes

- `system_top.v` follows the DAC sideband wiring style used by AD5758.
- `common_bd.tcl` keeps the SPI Engine and DMA structure used by AD5766.
- The project and constraint files are distilled to the minimum reusable skeleton.
