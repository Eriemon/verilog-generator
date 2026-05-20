source $ad_hdl_dir/library/jesd204/scripts/jesd204.tcl

set RX_NUM_OF_LANES $ad_project_params(RX_JESD_L)
set RX_NUM_OF_CONVERTERS [get_env_param RX_NUM_OF_CONVERTERS 2]
set RX_SAMPLES_PER_FRAME [get_env_param RX_JESD_S 1]
set RX_SAMPLE_WIDTH [get_env_param RX_SAMPLE_WIDTH 16]
set RX_SAMPLES_PER_CHANNEL [expr {($RX_NUM_OF_LANES * 32) / ($RX_NUM_OF_CONVERTERS * $RX_SAMPLE_WIDTH)}]

ad_ip_instance axi_adxcvr axi_jesd_adc_xcvr
ad_ip_parameter axi_jesd_adc_xcvr CONFIG.NUM_OF_LANES $RX_NUM_OF_LANES
ad_ip_parameter axi_jesd_adc_xcvr CONFIG.TX_OR_RX_N 0

adi_axi_jesd204_rx_create axi_jesd_adc_link $RX_NUM_OF_LANES
adi_tpl_jesd204_rx_create axi_jesd_adc_tpl $RX_NUM_OF_LANES $RX_NUM_OF_CONVERTERS $RX_SAMPLES_PER_FRAME $RX_SAMPLE_WIDTH

ad_ip_instance util_cpack2 axi_jesd_adc_cpack [list \
	NUM_OF_CHANNELS $RX_NUM_OF_CONVERTERS \
	SAMPLES_PER_CHANNEL $RX_SAMPLES_PER_CHANNEL \
	SAMPLE_DATA_WIDTH $RX_SAMPLE_WIDTH \
]

ad_ip_instance axi_dmac axi_jesd_adc_dma
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_TYPE_SRC 2
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_TYPE_DEST 0
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_DATA_WIDTH_SRC 64
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_DATA_WIDTH_DEST 64

create_bd_port -dir I rx_ref_clk_0
create_bd_port -dir O rx_core_clk

ad_connect axi_jesd_adc_tpl/link_clk rx_core_clk
ad_connect axi_jesd_adc_tpl/adc_valid_0 axi_jesd_adc_cpack/fifo_wr_en
ad_connect axi_jesd_adc_dma/fifo_wr axi_jesd_adc_cpack/packed_fifo_wr

ad_cpu_interconnect 0x44A60000 axi_jesd_adc_xcvr
ad_cpu_interconnect 0x44A10000 axi_jesd_adc_tpl
ad_cpu_interconnect 0x44AA0000 axi_jesd_adc_link
ad_cpu_interconnect 0x7C420000 axi_jesd_adc_dma
