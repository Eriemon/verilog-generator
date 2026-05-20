source $ad_hdl_dir/library/jesd204/scripts/jesd204.tcl

set TX_NUM_OF_LANES $ad_project_params(TX_JESD_L)
set TX_NUM_OF_CONVERTERS [get_env_param TX_NUM_OF_CONVERTERS 2]
set TX_SAMPLES_PER_FRAME [get_env_param TX_JESD_S 1]
set TX_SAMPLE_WIDTH [get_env_param TX_SAMPLE_WIDTH 16]
set TX_SAMPLES_PER_CHANNEL [expr {($TX_NUM_OF_LANES * 32) / ($TX_NUM_OF_CONVERTERS * $TX_SAMPLE_WIDTH)}]
set dac_fifo_address_width [get_env_param DAC_FIFO_ADDRESS_WIDTH 13]
set dac_data_width [expr {$TX_SAMPLE_WIDTH * $TX_NUM_OF_CONVERTERS * $TX_SAMPLES_PER_CHANNEL}]

ad_ip_instance axi_adxcvr axi_jesd_dac_xcvr
ad_ip_parameter axi_jesd_dac_xcvr CONFIG.NUM_OF_LANES $TX_NUM_OF_LANES
ad_ip_parameter axi_jesd_dac_xcvr CONFIG.TX_OR_RX_N 1

adi_axi_jesd204_tx_create axi_jesd_dac_link $TX_NUM_OF_LANES
adi_tpl_jesd204_tx_create axi_jesd_dac_tpl $TX_NUM_OF_LANES $TX_NUM_OF_CONVERTERS $TX_SAMPLES_PER_FRAME $TX_SAMPLE_WIDTH

ad_ip_instance util_upack2 axi_jesd_dac_upack [list \
	NUM_OF_CHANNELS $TX_NUM_OF_CONVERTERS \
	SAMPLES_PER_CHANNEL $TX_SAMPLES_PER_CHANNEL \
	SAMPLE_DATA_WIDTH $TX_SAMPLE_WIDTH \
]

ad_ip_instance axi_dmac axi_jesd_dac_dma
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_TYPE_SRC 0
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_TYPE_DEST 1
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_DATA_WIDTH_SRC 64
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_DATA_WIDTH_DEST $dac_data_width

ad_dacfifo_create axi_jesd_dac_fifo $dac_data_width $dac_data_width $dac_fifo_address_width

create_bd_port -dir I tx_ref_clk_0

ad_connect axi_jesd_dac_link/tx_data axi_jesd_dac_tpl/link
ad_connect axi_jesd_dac_tpl/dac_valid_0 axi_jesd_dac_upack/fifo_rd_en
ad_connect axi_jesd_dac_upack/s_axis_ready axi_jesd_dac_fifo/dac_valid
ad_connect axi_jesd_dac_upack/s_axis_data axi_jesd_dac_fifo/dac_data
ad_connect axi_jesd_dac_fifo/dma_xfer_req axi_jesd_dac_dma/m_axis_xfer_req

ad_cpu_interconnect 0x44A60000 axi_jesd_dac_xcvr
ad_cpu_interconnect 0x44A04000 axi_jesd_dac_tpl
ad_cpu_interconnect 0x44A90000 axi_jesd_dac_link
ad_cpu_interconnect 0x7C420000 axi_jesd_dac_dma
