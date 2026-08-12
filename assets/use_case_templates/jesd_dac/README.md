# JESD DAC Use-Case Template

This bundle captures the recurring JESD204 transmit pattern used by JESD DAC projects such as DAC_FMC_EBZ and the transmit side of DAQ3.

## Scope

- JESD204 TX transceiver and link bring-up
- Transport feed through upack and DAC FIFO
- DMA-backed waveform playback
- Board-level top and block-design scaffolding

## Parameterization Points

- TX lane count
- Number of converters
- Sample width and samples per frame
- FIFO depth and DMA width
- Board family and transceiver settings

## Provenance Notes

- `common_bd.tcl` keeps the TX-side structure used by DAC_FMC_EBZ and DAQ3.
- `system_top.v` is a compact board-facing wrapper that exposes a waveform push input and lane clock output.
