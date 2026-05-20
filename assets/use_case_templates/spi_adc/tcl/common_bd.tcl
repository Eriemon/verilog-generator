create_bd_intf_port -mode Master -vlnv analog.com:interface:spi_master_rtl:1.0 adc_spi
create_bd_port -dir O adc_spi_cnv
create_bd_port -dir I adc_spi_busy

set adc_resolution [get_env_param ADC_RESOLUTION 16]
set adc_num_channels [get_env_param ADC_NUM_CHANNELS 1]
set adc_sampling_rate [get_env_param ADC_SAMPLING_RATE 1000000]
set spi_clk_ref_frequency [get_env_param SPI_CLK_REF_FREQUENCY 166]
set spi_data_width [expr {$adc_resolution <= 16 ? 16 : 32}]
set sampling_cycle [expr {int(ceil(double($spi_clk_ref_frequency * 1000000) / $adc_sampling_rate))}]

source $ad_hdl_dir/library/spi_engine/scripts/spi_engine.tcl

set hier_spi_engine spi_adc_capture
spi_engine_create $hier_spi_engine $spi_data_width 1 1 $adc_num_channels 0

ad_ip_instance axi_pulse_gen adc_trigger_gen
ad_ip_parameter adc_trigger_gen CONFIG.PULSE_PERIOD $sampling_cycle
ad_ip_parameter adc_trigger_gen CONFIG.PULSE_WIDTH 1

create_bd_cell -type module -reference sync_bits adc_busy_sync
create_bd_cell -type module -reference ad_edge_detect adc_busy_edge
set_property -dict [list CONFIG.EDGE 1] [get_bd_cells adc_busy_edge]

ad_ip_instance axi_dmac axi_spi_adc_dma
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_TYPE_SRC 1
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_TYPE_DEST 0
ad_ip_parameter axi_spi_adc_dma CONFIG.CYCLIC 0
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_2D_TRANSFER 0
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_DATA_WIDTH_SRC $spi_data_width
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_DATA_WIDTH_DEST 64

ad_connect sys_cpu_clk $hier_spi_engine/clk
ad_connect spi_clk adc_trigger_gen/ext_clk
ad_connect sys_cpu_clk adc_trigger_gen/s_axi_aclk
ad_connect sys_cpu_resetn adc_trigger_gen/s_axi_aresetn
ad_connect spi_clk adc_busy_edge/clk
ad_connect adc_busy_edge/rst GND
ad_connect sys_cpu_resetn $hier_spi_engine/resetn
ad_connect spi_clk $hier_spi_engine/spi_clk
ad_connect $hier_spi_engine/m_spi adc_spi
ad_connect adc_spi_busy adc_busy_sync/in_bits
ad_connect adc_busy_sync/out_bits adc_busy_edge/signal_in
ad_connect adc_busy_edge/signal_out $hier_spi_engine/offload/trigger
ad_connect adc_trigger_gen/pulse adc_spi_cnv
ad_connect axi_spi_adc_dma/s_axis $hier_spi_engine/M_AXIS_SAMPLE

ad_cpu_interconnect 0x44A00000 $hier_spi_engine/axi_regmap
ad_cpu_interconnect 0x44A30000 axi_spi_adc_dma
ad_cpu_interconnect 0x44B00000 adc_trigger_gen

ad_cpu_interrupt "ps-13" "mb-13" axi_spi_adc_dma/irq
