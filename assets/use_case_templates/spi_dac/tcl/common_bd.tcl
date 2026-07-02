# DAC SPI 主接口位于层次块外侧，确保顶层引脚约束只绑定一个导出端口
create_bd_intf_port -mode Master -vlnv analog.com:interface:spi_master_rtl:1.0 dac_spi

# SPI DAC 层次块隔离 engine、AXI 控制和 DMA 握手，避免播放路径散落在根层级
create_bd_cell -type hier spi_dac_path

# 当前 BD 实例限制在播放层次块内，确保后续端口不会污染系统根层级
current_bd_instance /spi_dac_path

# 层次块输入时钟统一控制寄存器和串行逻辑，避免模板引入额外时钟域
create_bd_pin -dir I -type clk clk

# 层次块复位覆盖 AXI 控制和执行模块，确保软件启动前 SPI 状态一致
create_bd_pin -dir I -type rst resetn

# SPI engine 中断作为软件可见状态出口，便于报告 offload 完成或异常
create_bd_pin -dir O irq

# DMA 时钟出口保留给上游搬运路径，确保播放数据与 SPI 控制域对齐
create_bd_pin -dir O dma_clk

# DMA 使能握手限制 cyclic 播放入口，防止未启动时样本流进入 SPI engine
create_bd_pin -dir I dma_enable

# DMA 有效指示返回播放链路消费状态，便于上游判断样本是否被接收
create_bd_pin -dir O dma_valid

# DMA 样本端口固定为 16 位，确保默认 DAC 数据宽度与软件缓冲一致
create_bd_pin -dir I -from 15 -to 0 dma_data

# DMA 传输请求作为补样触发信号，避免 FIFO 消耗后缺少上游响应
create_bd_pin -dir I dma_xfer_req

# DMA underflow 状态保留给播放风险诊断，便于软件报告缓冲不足
create_bd_pin -dir I dma_underflow

# 层次块内部 SPI 主接口是唯一外部 DAC 通道，避免多处直接驱动顶层 SPI 端口
create_bd_intf_pin -mode Master -vlnv analog.com:interface:spi_master_rtl:1.0 m_spi

# SPI 执行 IP 承担 DAC 串行事务副作用，确保 offload 命令真正驱动物理总线
ad_ip_instance spi_engine_execution execution

# AXI SPI engine 控制 IP 暴露软件寄存器，确保命令表可由处理系统装载
ad_ip_instance axi_spi_engine axi

# 控制互连隔离寄存器域和执行域，确保 AXI 命令能按 SPI engine 协议传递
ad_ip_instance spi_engine_interconnect interconnect

# 片选数量限制为单个 DAC 器件，避免生成多余片选端口破坏顶层约束
ad_ip_parameter execution CONFIG.NUM_OF_CS 1

# 单 offload 通道对应模板的一条播放路径，确保 DMA cyclic 流只服务当前 DAC
ad_ip_parameter axi CONFIG.NUM_OFFLOAD 1

# AXI 控制输出进入互连从端，确保软件命令先经过 SPI engine 控制仲裁
ad_connect axi/spi_engine_ctrl interconnect/s0_ctrl

# 互连主端面向执行模块，确保仲裁后的命令成为 DAC 串行事务
ad_connect interconnect/m_ctrl execution/ctrl

# 执行模块 SPI 侧只导向层次端口，防止内部 IP 直接暴露到顶层约束
ad_connect execution/spi m_spi

# 执行模块与层次块共用时钟，避免控制口和串行口产生跨域风险
ad_connect clk execution/clk

# AXI 控制寄存器使用层次块时钟，确保软件访问与 engine 控制域一致
ad_connect clk axi/s_axi_aclk

# SPI 串行时钟复用层次块时钟，限制模板为单时钟域播放结构
ad_connect clk axi/spi_clk

# 控制互连与执行和 AXI 共享时钟，避免命令握手跨域
ad_connect clk interconnect/clk

# AXI 生成的 SPI 复位约束执行模块启动状态，确保命令前总线空闲
ad_connect axi/spi_resetn execution/resetn

# AXI 生成的 SPI 复位同步控制互连，避免复位后残留旧命令
ad_connect axi/spi_resetn interconnect/resetn

# 层次复位覆盖 AXI 控制接口，确保处理系统复位能清空寄存器状态
ad_connect resetn axi/s_axi_aresetn

# 执行中断向层次块外透出，确保软件能观察 SPI engine 状态变化
ad_connect irq axi/irq

# 根层级只承担系统资源拼接，避免后续系统连接误入播放层次块
current_bd_instance /

# 系统 CPU 时钟作为播放层次块时钟源，确保寄存器和 engine 共用处理系统时钟
ad_connect sys_cpu_clk spi_dac_path/clk

# 系统复位直接控制播放层次块，避免软件复位后 SPI 路径保持旧状态
ad_connect sys_cpu_resetn spi_dac_path/resetn

# 层次块 SPI 主接口唯一到达顶层 DAC 端口，确保 XDC 约束目标稳定
ad_connect spi_dac_path/m_spi dac_spi

# SPI DAC DMA 是内存到播放路径的唯一搬运入口，确保 cyclic 样本来源明确
ad_ip_instance axi_dmac axi_spi_dac_dma

# DMA 源端保持内存映射方向，确保软件波形缓冲是播放数据来源
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_TYPE_SRC 0

# DMA 目的端保持流接口，确保样本按握手进入 SPI DAC 播放链路
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_TYPE_DEST 2

# cyclic 模式保持连续周期播放，避免软件重复启动每个波形周期
ad_ip_parameter axi_spi_dac_dma CONFIG.CYCLIC 1

# 二维搬运被关闭，确保波形缓冲按线性地址顺序播放
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_2D_TRANSFER 0

# DMA 流侧宽度限制为 16 位，确保默认 DAC 样本不会扩展出无效位
ad_ip_parameter axi_spi_dac_dma CONFIG.DMA_DATA_WIDTH_DEST 16

# 控制寄存器地址固定在软件驱动预期窗口，确保 SPI 命令表可寻址
ad_cpu_interconnect 0x44A00000 spi_dac_path/axi

# DMA 寄存器地址固定在播放控制窗口，确保软件能启动和停止 cyclic 搬运
ad_cpu_interconnect 0x44A20000 axi_spi_dac_dma

# SPI engine 中断接入处理系统，确保命令完成和异常状态能进入软件报告
ad_cpu_interrupt "ps-12" "mb-13" spi_dac_path/irq

# DMA 中断接入处理系统，确保播放缓冲状态能被软件监控
ad_cpu_interrupt "ps-13" "mb-12" axi_spi_dac_dma/irq
