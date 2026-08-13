# ADC SPI 主接口位于模板边界，确保顶层引脚约束只绑定一个采样通道
create_bd_intf_port -mode Master -vlnv analog.com:interface:spi_master_rtl:1.0 adc_spi

# CNV 输出端口承担外部 SAR ADC 启动副作用，确保采样节拍可由 BD 控制
create_bd_port -dir O adc_spi_cnv

# BUSY 输入保留外部转换完成状态，确保 offload 触发依据来自真实 ADC 引脚
create_bd_port -dir I adc_spi_busy

# ADC_RESOLUTION 限制 SPI 样本打包位宽，避免高分辨率数据被截断
set countAdcResolution [get_env_param ADC_RESOLUTION 16]

# ADC_NUM_CHANNELS 控制 SPI engine 通道展开，确保采样流和外部 ADC 通道数一致
set countAdcNumChannels [get_env_param ADC_NUM_CHANNELS 1]

# ADC_SAMPLING_RATE 作为 CNV 周期目标，确保脉冲发生器匹配采样需求
set countAdcSamplingRate [get_env_param ADC_SAMPLING_RATE 1000000]

# SPI_CLK_REF_FREQUENCY 是周期换算基准，避免采样率与 SPI 时钟单位混淆
set countSpiClkRefFrequency [get_env_param SPI_CLK_REF_FREQUENCY 166]

# SPI 数据宽度按分辨率折叠到 16/32 位，确保 DMA 侧字宽与样本容器匹配
set countSpiDataWidth [expr {$countAdcResolution <= 16 ? 16 : 32}]

# 采样周期以 SPI 参考时钟计数表示，确保脉冲发生器无需再处理频率单位
set countSamplingCycle [expr {int(ceil(double($countSpiClkRefFrequency * 1000000) / $countAdcSamplingRate))}]

# SPI engine helper 进入 Tcl 环境，确保后续采样层次块按 ADI 封装生成
source $ad_hdl_dir/library/spi_engine/scripts/spi_engine.tcl

# SPI engine 层次名保持稳定，确保顶层连接和软件地址映射引用同一实例
set hierSpiEngine spi_adc_capture

# SPI 采样 engine 承接 ADC 参数，确保通道数量和数据宽度同步进入 offload 路径
spi_engine_create $hierSpiEngine $countSpiDataWidth 1 1 $countAdcNumChannels 0

# 转换触发脉冲发生器是 CNV 的唯一来源，避免外部 ADC 被多个逻辑同时启动
ad_ip_instance axi_pulse_gen adc_trigger_gen

# 触发周期使用换算后的时钟计数，确保目标采样节拍由硬件维持
ad_ip_parameter adc_trigger_gen CONFIG.PULSE_PERIOD $countSamplingCycle

# 单周期 CNV 宽度限制触发窗口，避免外部 ADC 在一次采样中重复启动
ad_ip_parameter adc_trigger_gen CONFIG.PULSE_WIDTH 1

# BUSY 同步模块隔离外部引脚和 SPI 时钟域，降低亚稳态风险
create_bd_cell -type module -reference sync_bits adc_busy_sync

# BUSY 边沿检测模块将转换完成事件变成 offload 触发，确保采样动作由 ADC 状态驱动
create_bd_cell -type module -reference ad_edge_detect adc_busy_edge

# 上升沿约定匹配 BUSY 完成时序，避免在转换开始阶段提前触发采样
set_property -dict [list CONFIG.EDGE 1] [get_bd_cells adc_busy_edge]

# ADC DMA 是采样流进入处理系统内存的唯一搬运路径，确保数据落点清晰
ad_ip_instance axi_dmac axi_spi_adc_dma

# DMA 源端保持流接口，确保 SPI engine 样本通过握手进入搬运路径
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_TYPE_SRC 1

# DMA 目的端保持内存映射方向，确保软件从处理系统缓冲区读取采样结果
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_TYPE_DEST 0

# cyclic 模式关闭后采集窗口由软件启动，避免持续覆盖采样缓冲
ad_ip_parameter axi_spi_adc_dma CONFIG.CYCLIC 0

# 二维搬运被关闭，确保样本流按线性内存顺序写入
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_2D_TRANSFER 0

# DMA 流侧宽度跟随 SPI 样本宽度，避免采样数据在入口被截断
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_DATA_WIDTH_SRC $countSpiDataWidth

# DMA 内存侧宽度限制为 64 位，确保与处理系统通用数据口兼容
ad_ip_parameter axi_spi_adc_dma CONFIG.DMA_DATA_WIDTH_DEST 64

# SPI engine 控制域使用系统 CPU 时钟，确保寄存器访问与处理系统同步
ad_connect sys_cpu_clk $hierSpiEngine/clk

# 触发发生器外部时钟来自 SPI 时钟，确保 CNV 与采样域同步
ad_connect spi_clk adc_trigger_gen/ext_clk

# 触发发生器寄存器接口使用系统 CPU 时钟，确保软件配置路径稳定
ad_connect sys_cpu_clk adc_trigger_gen/s_axi_aclk

# 触发发生器复位跟随系统复位，避免复位后残留旧采样周期
ad_connect sys_cpu_resetn adc_trigger_gen/s_axi_aresetn

# BUSY 边沿检测使用 SPI 时钟，确保 offload 触发与采样 engine 同域
ad_connect spi_clk adc_busy_edge/clk

# BUSY 边沿检测复位固定为低，避免额外复位屏蔽采样完成事件
ad_connect adc_busy_edge/rst GND

# SPI engine 复位跟随系统复位，确保采样链路启动状态由软件统一控制
ad_connect sys_cpu_resetn $hierSpiEngine/resetn

# SPI engine 串行时钟使用采样域时钟，避免串行事务跨域
ad_connect spi_clk $hierSpiEngine/spi_clk

# SPI engine 主接口唯一到达顶层 ADC SPI 端口，确保 XDC 约束目标稳定
ad_connect $hierSpiEngine/m_spi adc_spi

# ADC BUSY 先进入同步模块，确保外部状态跨域后再参与边沿检测
ad_connect adc_spi_busy adc_busy_sync/in_bits

# 同步后的 BUSY 才能驱动边沿检测，避免亚稳态直接触发 offload
ad_connect adc_busy_sync/out_bits adc_busy_edge/signal_in

# BUSY 边沿作为 offload 触发源，确保转换完成后才发起 SPI 读取
ad_connect adc_busy_edge/signal_out $hierSpiEngine/offload/trigger

# 周期性脉冲唯一到达 CNV 端口，确保外部 ADC 启动节拍由脉冲发生器控制
ad_connect adc_trigger_gen/pulse adc_spi_cnv

# DMA 输入流消费 SPI engine 样本输出，确保采样结果直接进入内存搬运路径
ad_connect axi_spi_adc_dma/s_axis $hierSpiEngine/M_AXIS_SAMPLE

# SPI engine 寄存器地址固定在软件驱动预期窗口，确保采样命令表可寻址
ad_cpu_interconnect 0x44A00000 $hierSpiEngine/axi_regmap

# ADC DMA 寄存器地址固定在采集控制窗口，确保软件能启动和监控搬运
ad_cpu_interconnect 0x44A30000 axi_spi_adc_dma

# 触发发生器寄存器地址固定在节拍控制窗口，确保软件可调整采样周期
ad_cpu_interconnect 0x44B00000 adc_trigger_gen

# ADC DMA 中断接入处理系统，确保采集完成状态能进入软件报告
ad_cpu_interrupt "ps-13" "mb-13" axi_spi_adc_dma/irq
