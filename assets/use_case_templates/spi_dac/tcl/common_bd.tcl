create_bd_intf_port -mode Master -vlnv analog.com:interface:spi_master_rtl:1.0 dac_spi

create_bd_cell -type hier spi_dac_path
current_bd_instance /spi_dac_path

create_bd_pin -dir I -type clk clk
create_bd_pin -dir I -type rst resetn
create_bd_pin -dir O irq
create_bd_pin -dir O dma_clk
create_bd_pin -dir I dma_enable
create_bd_pin -dir O dma_valid
create_bd_pin -dir I -from 15 -to 0 dma_data
create_bd_pin -dir I dma_xfer_req
create_bd_pin -dir I dma_underflow
create_bd_intf_pin -mode Master -vlnv analog.com:interface:spi_master_rtl:1.0 m_spi

ad_ip_instance spi_engine_execution execution
ad_ip_instance axi_spi_engine axi
ad_ip_instance spi_engine_interconnect interconnect

ad_ip_parameter execution CONFIG.NUM_OF_CS 1
ad_ip_parameter axi CONFIG.NUM_OFFLOAD 1

ad_connect axi/spi_engine_ctrl interconnect/s0_ctrl
ad_connect interconnect/m_ctrl execution/ctrl
ad_connect execution/spi m_spi
ad_connect clk execution/clk
ad_connect clk axi/s_axi_aclk
ad_connect clk axi/spi_clk
ad_connect clk interconnect/clk
ad_connect axi/spi_resetn execution/resetn
ad_connect axi/spi_resetn interconnect/resetn
ad_connect resetn axi/s_axi_aresetn
ad_connect irq axi/irq

current_bd_instance /

ad_connect sys_cpu_clk spi_dac_path/clk
ad_connect sys_cpu_resetn spi_dac_path/resetn
ad_connect spi_dac_path/m_spi dac_spi

ad_ip_instance axi_dmac axi_spi_dac_dma
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_TYPE_SRC 0
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_TYPE_DEST 2
ad_ip_parameter axi_spi_dac_dma CONFIG.CYCLIC 1
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_2D_TRANSFER 0
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_DATA_WIDTH_DEST 16

ad_cpu_interconnect 0x44A00000 spi_dac_path/axi
ad_cpu_interconnect 0x44A20000 axi_spi_dac_dma
ad_cpu_interrupt "ps-12" "mb-13" spi_dac_path/irq
ad_cpu_interrupt "ps-13" "mb-12" axi_spi_dac_dma/irq
