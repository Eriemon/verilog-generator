# Mixed MxFE sideband clocks
set_property -dict {PACKAGE_PIN K28 IOSTANDARD LVDS} [get_ports i_rx_ref_clk]
set_property -dict {PACKAGE_PIN J28 IOSTANDARD LVDS} [get_ports i_tx_ref_clk]

# TDD and PMOD sideband
set_property -dict {PACKAGE_PIN H18 IOSTANDARD LVCMOS18} [get_ports i_tdd_sync]
set_property -dict {PACKAGE_PIN G18 IOSTANDARD LVCMOS18} [get_ports o_tdd_enabled]
