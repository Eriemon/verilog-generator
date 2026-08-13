# JESD204 helper 进入 Tcl 环境，确保发送 link 和 TPL 使用 ADI 标准封装
source $ad_hdl_dir/library/jesd204/scripts/jesd204.tcl

# TX_JESD_L 来自板级 ADI 参数表，确保 GT lane 数和发送 link 宽度一致
set countTxNumOfLanes [set ad_project_params(TX_JESD_L)]

# TX_NUM_OF_CONVERTERS 控制 converter 通道展开，确保 TPL 与 upack 结构匹配
set countTxNumOfConverters [get_env_param TX_NUM_OF_CONVERTERS 2]

# TX_JESD_S 限制发送 TPL 的帧内样本组织，确保 converter 数据按 JESD 帧边界输出
set countTxSamplesPerFrame [get_env_param TX_JESD_S 1]

# TX_SAMPLE_WIDTH 限制 converter 样本位宽，避免 FIFO 和 DMA 数据宽度错位
set countTxSampleWidth [get_env_param TX_SAMPLE_WIDTH 16]

# 每通道样本数由 JESD lane 吞吐推导，确保 upack 和链路发送带宽匹配
set countTxSamplesPerChannel [expr {($countTxNumOfLanes * 32) / ($countTxNumOfConverters * $countTxSampleWidth)}]

# DAC_FIFO_ADDRESS_WIDTH 控制发送缓存深度，避免 DMA 写入节奏直接压迫链路侧
set countDacFifoAddressWidth [get_env_param DAC_FIFO_ADDRESS_WIDTH 13]

# DAC 数据总线宽度由样本组织推导，确保 DMA、FIFO 和 upack 数据位宽对齐
set countDacDataWidth [expr {$countTxSampleWidth * $countTxNumOfConverters * $countTxSamplesPerChannel}]

# JESD DAC 收发器实例承载物理发送 lane，确保 GT 配置独立于链路层 IP
ad_ip_instance axi_adxcvr axi_jesd_dac_xcvr

# 收发器 lane 数锁定到模板参数，避免 GT 与 link 配置错位
ad_ip_parameter axi_jesd_dac_xcvr CONFIG.NUM_OF_LANES $countTxNumOfLanes

# 收发器方向限制为发送模式，确保 DAC 数据只沿 TX 路径离开系统
ad_ip_parameter axi_jesd_dac_xcvr CONFIG.TX_OR_RX_N 1

# JESD204 发送链路 IP 使用同一 lane 宽度，确保链路层和物理层同步
adi_axi_jesd204_tx_create axi_jesd_dac_link $countTxNumOfLanes

# DAC TPL 负责 converter 样本到链路帧的映射，确保发送帧结构由参数控制
adi_tpl_jesd204_tx_create axi_jesd_dac_tpl $countTxNumOfLanes $countTxNumOfConverters $countTxSamplesPerFrame $countTxSampleWidth

# upack 将 FIFO 总线展开成 converter 通道，确保 TPL 接收的数据布局正确
ad_ip_instance util_upack2 axi_jesd_dac_upack [list \
	NUM_OF_CHANNELS $countTxNumOfConverters \
	SAMPLES_PER_CHANNEL $countTxSamplesPerChannel \
	SAMPLE_DATA_WIDTH $countTxSampleWidth \
]

# DAC DMA 是处理系统写入发送 FIFO 的唯一入口，确保播放样本来源明确
ad_ip_instance axi_dmac axi_jesd_dac_dma

# DMA 源端保持内存映射方向，确保处理系统缓冲是发送样本来源
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_TYPE_SRC 0

# DMA 目的端保持流接口，确保样本按握手进入 DAC FIFO 写入侧
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_TYPE_DEST 1

# DMA 内存侧宽度限制为 64 位，确保与处理系统通用数据口兼容
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_DATA_WIDTH_SRC 64

# DMA 流侧宽度跟随 DAC 数据总线，避免 FIFO 写入端截断样本
ad_ip_parameter axi_jesd_dac_dma CONFIG.DMA_DATA_WIDTH_DEST $countDacDataWidth

# DAC FIFO 隔离 DMA 写入和 JESD 发送消耗节奏，降低播放侧欠读风险
ad_dacfifo_create axi_jesd_dac_fifo $countDacDataWidth $countDacDataWidth $countDacFifoAddressWidth

# 发送参考时钟端口位于模板边界，确保顶层 XDC 能绑定 JESD refclk
create_bd_port -dir I tx_ref_clk_0

# JESD link 编码数据进入 TPL，确保链路层输出最终驱动 DAC 样本路径
ad_connect axi_jesd_dac_link/tx_data axi_jesd_dac_tpl/link

# TPL 有效信号控制 upack 读 FIFO，确保发送样本节拍受链路状态约束
ad_connect axi_jesd_dac_tpl/dac_valid_0 axi_jesd_dac_upack/fifo_rd_en

# upack 就绪信号反馈到 FIFO，避免发送侧在未就绪时消耗样本
ad_connect axi_jesd_dac_upack/s_axis_ready axi_jesd_dac_fifo/dac_valid

# FIFO 数据进入 upack，确保 DMA 写入的总线样本展开为 converter 通道
ad_connect axi_jesd_dac_upack/s_axis_data axi_jesd_dac_fifo/dac_data

# FIFO 传输请求反馈到 DMA，确保发送链路需要样本时处理系统补充数据
ad_connect axi_jesd_dac_fifo/dma_xfer_req axi_jesd_dac_dma/m_axis_xfer_req

# 收发器寄存器地址固定在软件驱动预期窗口，确保 JESD 物理层可配置
ad_cpu_interconnect 0x44A60000 axi_jesd_dac_xcvr

# DAC TPL 寄存器地址固定在 converter 控制窗口，确保软件可配置数据路径
ad_cpu_interconnect 0x44A04000 axi_jesd_dac_tpl

# JESD link 寄存器地址固定在链路控制窗口，确保软件能配置发送状态
ad_cpu_interconnect 0x44A90000 axi_jesd_dac_link

# DMA 寄存器地址固定在播放控制窗口，确保软件能启动和监控样本搬运
ad_cpu_interconnect 0x7C420000 axi_jesd_dac_dma
