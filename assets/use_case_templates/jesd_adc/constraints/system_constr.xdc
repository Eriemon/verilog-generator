# JESD ADC reference clock
set_property -dict {PACKAGE_PIN K28 IOSTANDARD LVDS} [get_ports i_rx_ref_clk]

# JESD ADC lane example
set_property -dict {PACKAGE_PIN G6 IOSTANDARD LVDS} [get_ports i_rx_data_p[0]]
set_property -dict {PACKAGE_PIN G5 IOSTANDARD LVDS} [get_ports i_rx_data_n[0]]
set_property -dict {PACKAGE_PIN F4 IOSTANDARD LVDS} [get_ports i_rx_data_p[1]]
set_property -dict {PACKAGE_PIN F3 IOSTANDARD LVDS} [get_ports i_rx_data_n[1]]
