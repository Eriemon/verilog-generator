# JESD204 helper 进入 Tcl 环境，确保接收 link 和 TPL 使用 ADI 标准封装
source $ad_hdl_dir/library/jesd204/scripts/jesd204.tcl

# RX_JESD_L 来自板级 ADI 参数表，确保 GT lane 数和接收 link 宽度一致
set countRxNumOfLanes [set ad_project_params(RX_JESD_L)]

# RX_NUM_OF_CONVERTERS 控制 converter 通道展开，确保 TPL 与 cpack 结构匹配
set countRxNumOfConverters [get_env_param RX_NUM_OF_CONVERTERS 2]

# RX_JESD_S 限制接收 TPL 的帧内样本组织，确保 converter 数据按 JESD 帧边界恢复
set countRxSamplesPerFrame [get_env_param RX_JESD_S 1]

# RX_SAMPLE_WIDTH 限制 converter 样本位宽，避免 cpack 打包布局和 DMA 数据错位
set countRxSampleWidth [get_env_param RX_SAMPLE_WIDTH 16]

# 每通道样本数由 lane 吞吐推导，确保 cpack 写入速率和 JESD link 数据宽度匹配
set countRxSamplesPerChannel [expr {($countRxNumOfLanes * 32) / ($countRxNumOfConverters * $countRxSampleWidth)}]

# JESD ADC 收发器实例承载物理接收 lane，确保 GT 配置独立于链路层 IP
ad_ip_instance axi_adxcvr axi_jesd_adc_xcvr

# 收发器 lane 数锁定到模板参数，避免 GT 与 link 配置错位
ad_ip_parameter axi_jesd_adc_xcvr CONFIG.NUM_OF_LANES $countRxNumOfLanes

# 收发器方向限制为接收模式，确保 ADC 数据只沿 RX 路径进入系统
ad_ip_parameter axi_jesd_adc_xcvr CONFIG.TX_OR_RX_N 0

# JESD204 接收链路 IP 使用同一 lane 宽度，确保链路层和物理层同步
adi_axi_jesd204_rx_create axi_jesd_adc_link $countRxNumOfLanes

# ADC TPL 负责 converter 样本恢复，确保链路帧转换成后级可消费的数据流
adi_tpl_jesd204_rx_create axi_jesd_adc_tpl $countRxNumOfLanes $countRxNumOfConverters $countRxSamplesPerFrame $countRxSampleWidth

# cpack 汇聚 converter 样本到 DMA FIFO 写口，确保多通道采样按统一总线落地
ad_ip_instance util_cpack2 axi_jesd_adc_cpack [list \
	NUM_OF_CHANNELS $countRxNumOfConverters \
	SAMPLES_PER_CHANNEL $countRxSamplesPerChannel \
	SAMPLE_DATA_WIDTH $countRxSampleWidth \
]

# ADC DMA 是接收样本进入处理系统内存的唯一搬运路径，确保采集数据落点明确
ad_ip_instance axi_dmac axi_jesd_adc_dma

# DMA 源端保持流接口，确保 cpack 打包样本通过握手进入搬运路径
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_TYPE_SRC 2

# DMA 目的端保持内存映射方向，确保软件从处理系统缓冲读取采样结果
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_TYPE_DEST 0

# DMA 源端宽度限制为 64 位，确保与 cpack 输出总线兼容
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_DATA_WIDTH_SRC 64

# DMA 内存侧宽度限制为 64 位，确保与处理系统通用数据口兼容
ad_ip_parameter axi_jesd_adc_dma CONFIG.DMA_DATA_WIDTH_DEST 64

# 接收参考时钟端口位于模板边界，确保顶层 XDC 能绑定 JESD refclk
create_bd_port -dir I rx_ref_clk_0

# 接收核心时钟导出到顶层，便于后续同步逻辑和调试报告复用
create_bd_port -dir O rx_core_clk

# TPL 链路时钟作为接收核心时钟，确保下游采样域与 JESD link 同步
ad_connect axi_jesd_adc_tpl/link_clk rx_core_clk

# TPL 样本有效信号控制 cpack 写入，确保打包节拍受链路层状态约束
ad_connect axi_jesd_adc_tpl/adc_valid_0 axi_jesd_adc_cpack/fifo_wr_en

# DMA FIFO 写口消费 cpack 输出，确保恢复后的 converter 样本进入内存搬运路径
ad_connect axi_jesd_adc_dma/fifo_wr axi_jesd_adc_cpack/packed_fifo_wr

# 收发器寄存器地址固定在软件驱动预期窗口，确保 JESD 物理层可配置
ad_cpu_interconnect 0x44A60000 axi_jesd_adc_xcvr

# ADC TPL 寄存器地址固定在 converter 控制窗口，确保软件可配置数据路径
ad_cpu_interconnect 0x44A10000 axi_jesd_adc_tpl

# JESD link 寄存器地址固定在链路状态窗口，确保软件能监控接收状态
ad_cpu_interconnect 0x44AA0000 axi_jesd_adc_link

# DMA 寄存器地址固定在采集控制窗口，确保软件能启动和监控样本搬运
ad_cpu_interconnect 0x7C420000 axi_jesd_adc_dma
