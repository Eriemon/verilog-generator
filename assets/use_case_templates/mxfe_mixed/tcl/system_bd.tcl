# ZCU102 公共 block design 提供处理系统和板级基础连接，确保模板只补充 MxFE 外设
source $ad_hdl_dir/projects/common/zcu102/zcu102_system_bd.tcl

# ADC FIFO 公共脚本先进入 Tcl 环境，确保接收缓存 helper 在 common_bd.tcl 前可用
source $ad_hdl_dir/projects/common/xilinx/adcfifo_bd.tcl

# DAC FIFO 公共脚本先进入 Tcl 环境，确保发送缓存 helper 在 common_bd.tcl 前可用
source $ad_hdl_dir/projects/common/xilinx/dacfifo_bd.tcl

# 模板目录使用当前脚本目录变量，确保公共片段从当前模板目录加载
set templateDir [file dirname [info script]]

# MxFE 公共片段承接板级参数，确保外设、TDD 端口和缓存路径在同一 BD 上下文生成
source [file join $templateDir common_bd.tcl]

# SYSID ROM 地址空间限制为模板初始化文件规模，避免 Vivado 期望额外内容
ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9

# SYSID ROM 文件固定在工程根，确保生成项目沿用 ADI 默认初始化文件名
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"

# rom_sys_0 与 axi_sysid_0 地址位宽保持一致，避免系统识别数据读取错位
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
