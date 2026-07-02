# ZCU102 公共 block design 提供处理系统和板级基础连接，确保模板只补充 MxFE 外设
source $ad_hdl_dir/projects/common/zcu102/zcu102_system_bd.tcl

# ADC FIFO 公共脚本先进入 Tcl 环境，确保接收缓存 helper 在 common_bd.tcl 前可用
source $ad_hdl_dir/projects/common/xilinx/adcfifo_bd.tcl

# DAC FIFO 公共脚本先进入 Tcl 环境，确保发送缓存 helper 在 common_bd.tcl 前可用
source $ad_hdl_dir/projects/common/xilinx/dacfifo_bd.tcl

# RX_KS_PER_CHANNEL 保留 ADI 工程参数表契约，确保 ADC FIFO 容量能由环境覆盖
set "adProjectParams(RX_KS_PER_CHANNEL)" [get_env_param RX_KS_PER_CHANNEL 4]

# ADI 旧数组名继续暴露 ADC FIFO 容量参数，避免公共缓存 helper 读取失败
set "ad_project_params(RX_KS_PER_CHANNEL)" $adProjectParams(RX_KS_PER_CHANNEL)

# TX_KS_PER_CHANNEL 保留 ADI 工程参数表契约，确保 DAC FIFO 容量能由环境覆盖
set "adProjectParams(TX_KS_PER_CHANNEL)" [get_env_param TX_KS_PER_CHANNEL 8]

# DAC FIFO 公共封装会读取传统数组键，回填可让环境覆盖值进入发送缓存深度计算
set "ad_project_params(TX_KS_PER_CHANNEL)" $adProjectParams(TX_KS_PER_CHANNEL)

# NUM_LINKS 保留 ADI 工程参数表契约，限制当前混合模板的链路扩展边界
set "adProjectParams(NUM_LINKS)" [get_env_param NUM_LINKS 2]

# ADI 旧数组名继续暴露链路数量参数，保持工程级脚本读取兼容
set "ad_project_params(NUM_LINKS)" $adProjectParams(NUM_LINKS)

# 模板目录显式保存为 smoke 与 ADI 入口契约，确保公共片段从当前模板目录加载
set "template_dir" [file dirname [info script]]

# MxFE 公共片段承接板级参数，确保外设、TDD 端口和缓存路径在同一 BD 上下文生成
source [file join $template_dir common_bd.tcl]

# SYSID ROM 地址空间限制为模板初始化文件规模，避免 Vivado 期望额外内容
ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9

# SYSID ROM 文件固定在工程根，确保生成项目沿用 ADI 默认初始化文件名
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"

# rom_sys_0 与 axi_sysid_0 地址位宽保持一致，避免系统识别数据读取错位
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
