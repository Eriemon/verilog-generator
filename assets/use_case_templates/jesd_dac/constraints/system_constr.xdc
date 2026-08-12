# JESD DAC reference clock
set_property -dict {PACKAGE_PIN K28 IOSTANDARD LVDS} [get_ports i_tx_ref_clk]

# JESD DAC lane example
set_property -dict {PACKAGE_PIN N8 IOSTANDARD LVDS} [get_ports o_dac_data_valid]
set_property -dict {PACKAGE_PIN M8 IOSTANDARD LVCMOS18} [get_ports o_dac_data[0]]
