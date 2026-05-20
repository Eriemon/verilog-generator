# SPI DAC interface
set_property -dict {PACKAGE_PIN E15 IOSTANDARD LVCMOS33} [get_ports o_dac_spi_sclk]
set_property -dict {PACKAGE_PIN B19 IOSTANDARD LVCMOS33} [get_ports o_dac_spi_sync_n]
set_property -dict {PACKAGE_PIN B20 IOSTANDARD LVCMOS33} [get_ports o_dac_spi_sdo]

# DAC sideband
set_property -dict {PACKAGE_PIN F19 IOSTANDARD LVCMOS33} [get_ports i_dac_fault_n]
set_property -dict {PACKAGE_PIN E19 IOSTANDARD LVCMOS33} [get_ports o_dac_reset_n]
set_property -dict {PACKAGE_PIN F18 IOSTANDARD LVCMOS33} [get_ports o_dac_ldac_n]
