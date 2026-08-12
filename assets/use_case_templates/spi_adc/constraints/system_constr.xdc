# SPI ADC control pins
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports o_adc_spi_sdi]
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33} [get_ports o_adc_spi_csn]
set_property -dict {PACKAGE_PIN Y17 IOSTANDARD LVCMOS33} [get_ports o_adc_spi_sclk]
set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports i_adc_spi_sdo]

# Conversion and sideband
set_property -dict {PACKAGE_PIN W18 IOSTANDARD LVCMOS33} [get_ports o_adc_cnv]
set_property -dict {PACKAGE_PIN V18 IOSTANDARD LVCMOS33} [get_ports i_adc_busy]
set_property -dict {PACKAGE_PIN W19 IOSTANDARD LVCMOS33} [get_ports io_adc_resetn]
