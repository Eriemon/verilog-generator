set adc_fifo_samples_per_converter [expr {$ad_project_params(RX_KS_PER_CHANNEL) * 1024}]
set dac_fifo_samples_per_converter [expr {$ad_project_params(TX_KS_PER_CHANNEL) * 1024}]

source $ad_hdl_dir/projects/common/xilinx/adcfifo_bd.tcl
source $ad_hdl_dir/projects/common/xilinx/dacfifo_bd.tcl

ad_mem_hp0_interconnect $sys_cpu_clk sys_ps8/S_AXI_HP0

create_bd_port -dir O sys_clk
ad_connect sys_ps8/pl_clk0 sys_clk

create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 iic_pmod
ad_ip_instance axi_iic axi_iic_pmod
ad_connect iic_pmod axi_iic_pmod/iic
ad_cpu_interconnect 0x45100000 axi_iic_pmod

create_bd_port -dir O -from 7 -to 0 spi_pmod_csn_o
create_bd_port -dir I -from 7 -to 0 spi_pmod_csn_i
create_bd_port -dir I spi_pmod_clk_i
create_bd_port -dir O spi_pmod_clk_o
create_bd_port -dir I spi_pmod_sdo_i
create_bd_port -dir O spi_pmod_sdo_o
create_bd_port -dir I spi_pmod_sdi_i

ad_ip_instance axi_quad_spi axi_spi_pmod
ad_ip_parameter axi_spi_pmod CONFIG.C_NUM_SS_BITS 8
ad_ip_parameter axi_spi_pmod CONFIG.C_SCK_RATIO 8
ad_connect spi_pmod_csn_i axi_spi_pmod/ss_i
ad_connect spi_pmod_csn_o axi_spi_pmod/ss_o
ad_connect spi_pmod_clk_i axi_spi_pmod/sck_i
ad_connect spi_pmod_clk_o axi_spi_pmod/sck_o
ad_connect spi_pmod_sdo_i axi_spi_pmod/io0_i
ad_connect spi_pmod_sdo_o axi_spi_pmod/io0_o
ad_connect spi_pmod_sdi_i axi_spi_pmod/io1_i
ad_connect $sys_cpu_clk axi_spi_pmod/ext_spi_clk
ad_cpu_interconnect 0x45200000 axi_spi_pmod

create_bd_port -dir I tdd_sync
create_bd_port -dir O tdd_enabled
create_bd_port -dir O tdd_rx_mxfe_en
create_bd_port -dir O tdd_tx_mxfe_en

ad_connect axi_tdd_0/tdd_enabled tdd_enabled
ad_connect axi_tdd_0/tdd_rx_rf_en tdd_rx_mxfe_en
ad_connect axi_tdd_0/tdd_tx_rf_en tdd_tx_mxfe_en
