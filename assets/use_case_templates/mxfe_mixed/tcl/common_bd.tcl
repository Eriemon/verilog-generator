
# 先把 ADI 参数表中的 ADC FIFO 容量读到本地变量，确保强 parser 能验证后续算术表达式
set countRxKsPerChannel [set ad_project_params(RX_KS_PER_CHANNEL)]

# ADC FIFO 容量以 converter 为粒度换算成 sample 数，确保公共封装获得明确接收缓存深度
set countAdcFifoSamplesPerConverter [expr {$countRxKsPerChannel * 1024}]

# 先把 ADI 参数表中的 DAC FIFO 容量读到本地变量，确保强 parser 能验证后续算术表达式
set countTxKsPerChannel [set ad_project_params(TX_KS_PER_CHANNEL)]

# DAC FIFO 容量以 converter 为粒度换算成 sample 数，确保公共封装获得明确发送缓存深度
set countDacFifoSamplesPerConverter [expr {$countTxKsPerChannel * 1024}]

# ADC FIFO helper 进入 Tcl 环境，确保接收缓存由 ADI 公共封装生成
source $ad_hdl_dir/projects/common/xilinx/adcfifo_bd.tcl

# DAC FIFO helper 进入 Tcl 环境，确保发送缓存由 ADI 公共封装生成
source $ad_hdl_dir/projects/common/xilinx/dacfifo_bd.tcl

# 处理系统 HP0 端口绑定 CPU 时钟域，确保高速样本搬运路径具备内存入口
ad_mem_hp0_interconnect $sys_cpu_clk sys_ps8/S_AXI_HP0

# 系统时钟端口导出 pl_clk0，便于顶层和其它模板片段复用同一时钟
create_bd_port -dir O sys_clk

# Zynq PL 时钟作为系统时钟出口，确保外部逻辑使用处理系统基准时钟
ad_connect sys_ps8/pl_clk0 sys_clk

# PMOD IIC 主接口位于模板边界，确保板级辅助器件控制通道可约束
create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 iic_pmod

# AXI IIC 控制器桥接 CPU 寄存器和 PMOD IIC 总线，确保软件可控制辅助器件
ad_ip_instance axi_iic axi_iic_pmod

# PMOD IIC 端口只连到 AXI IIC 控制器，避免多个主端同时驱动总线
ad_connect iic_pmod axi_iic_pmod/iic

# IIC 控制器寄存器地址固定在外设窗口，确保软件能配置 PMOD 辅助器件
ad_cpu_interconnect 0x45100000 axi_iic_pmod

# PMOD SPI 片选输出总线限制为 8 位，确保 Quad SPI 片选宽度和顶层端口一致
create_bd_port -dir O -from 7 -to 0 spi_pmod_csn_o

# PMOD SPI 片选输入反馈保留三态路径，确保 Xilinx Quad SPI 接口完整
create_bd_port -dir I -from 7 -to 0 spi_pmod_csn_i

# PMOD SPI 时钟输入反馈保留三态路径，确保 Quad SPI 时钟回读端存在
create_bd_port -dir I spi_pmod_clk_i

# PMOD SPI 时钟输出位于模板边界，确保外部控制器件获得软件可控时钟
create_bd_port -dir O spi_pmod_clk_o

# PMOD SPI 数据输出反馈保留三态路径，确保 Quad SPI io0 输入端有来源
create_bd_port -dir I spi_pmod_sdo_i

# PMOD SPI 数据输出位于模板边界，确保外部 SPI 从设备输入可约束
create_bd_port -dir O spi_pmod_sdo_o

# PMOD SPI 数据输入保留从设备返回路径，确保软件能读取外部器件响应
create_bd_port -dir I spi_pmod_sdi_i

# AXI Quad SPI 控制器提供 PMOD SPI 软件访问路径，确保辅助总线可配置
ad_ip_instance axi_quad_spi axi_spi_pmod

# 片选数量限制为 8 个，确保 PMOD 扩展多器件场景不超出端口宽度
ad_ip_parameter axi_spi_pmod CONFIG.C_NUM_SS_BITS 8

# SPI 时钟分频限制默认访问速率，避免 PMOD 外设超出保守时序能力
ad_ip_parameter axi_spi_pmod CONFIG.C_SCK_RATIO 8

# 片选输入反馈回到 Quad SPI 三态接口，确保三态控制链路闭合
ad_connect spi_pmod_csn_i axi_spi_pmod/ss_i

# Quad SPI 片选输出唯一驱动 PMOD 端口，防止片选总线多源冲突
ad_connect spi_pmod_csn_o axi_spi_pmod/ss_o

# SPI 时钟输入反馈回到 Quad SPI 三态接口，确保时钟三态路径闭合
ad_connect spi_pmod_clk_i axi_spi_pmod/sck_i

# Quad SPI 时钟输出唯一驱动 PMOD 端口，防止外部 SPI 时钟多源冲突
ad_connect spi_pmod_clk_o axi_spi_pmod/sck_o

# SPI 数据输出反馈回到 Quad SPI 三态接口，确保 io0 回读路径闭合
ad_connect spi_pmod_sdo_i axi_spi_pmod/io0_i

# Quad SPI 数据输出唯一驱动 PMOD 端口，防止 MOSI 信号多源冲突
ad_connect spi_pmod_sdo_o axi_spi_pmod/io0_o

# PMOD SPI 输入数据进入 Quad SPI 接收引脚，确保外部从设备响应可被软件读取
ad_connect spi_pmod_sdi_i axi_spi_pmod/io1_i

# 系统 CPU 时钟作为 Quad SPI 外部时钟源，确保 PMOD SPI 与处理系统时钟同步
ad_connect $sys_cpu_clk axi_spi_pmod/ext_spi_clk

# Quad SPI 寄存器地址固定在 PMOD 控制窗口，确保软件能访问辅助 SPI 总线
ad_cpu_interconnect 0x45200000 axi_spi_pmod

# TDD 同步输入位于模板边界，确保外部帧同步或时隙控制信号可约束
create_bd_port -dir I tdd_sync

# TDD 使能状态导出到顶层，便于调试逻辑报告时隙控制状态
create_bd_port -dir O tdd_enabled

# 接收 MxFE 使能信号导出到顶层，确保 RF ADC 时隙控制可观察
create_bd_port -dir O tdd_rx_mxfe_en

# 发送 MxFE 使能信号导出到顶层，确保 RF DAC 时隙控制可观察
create_bd_port -dir O tdd_tx_mxfe_en

# TDD 控制器使能状态到达顶层端口，确保调试视图能看到控制器状态
ad_connect axi_tdd_0/tdd_enabled tdd_enabled

# TDD 接收 RF 使能到达 MxFE 接收控制端口，确保接收时隙由控制器统一驱动
ad_connect axi_tdd_0/tdd_rx_rf_en tdd_rx_mxfe_en

# TDD 发送 RF 使能到达 MxFE 发送控制端口，确保发送时隙由控制器统一驱动
ad_connect axi_tdd_0/tdd_tx_rf_en tdd_tx_mxfe_en
