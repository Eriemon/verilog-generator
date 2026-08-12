# JESD ADC Use-Case Template

This bundle captures the recurring JESD204 receive pattern used by JESD ADC projects such as AD6676.

## Scope

- JESD204 RX transceiver and link bring-up
- Transport receive and channel packing
- DMA-backed ADC capture
- Board-level top and block-design scaffolding

## Parameterization Points

- RX lane count
- Number of converters
- Sample width and samples per frame
- Reference clock routing
- Board family and DMA interconnect

## Provenance Notes

- `common_bd.tcl` is distilled from the receive-side structure used by AD6676.
- `system_top.v` is a compact board-facing wrapper that exposes JESD RX sideband clocks and valid/data output.
