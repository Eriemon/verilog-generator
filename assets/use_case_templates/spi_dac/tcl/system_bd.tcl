# ZedBoard 公共 block design 提供处理系统和基础时钟，确保模板只补充播放链路
source $ad_hdl_dir/projects/common/zed/zed_system_bd.tcl

# ADI 参数辅助脚本统一环境覆盖语义，避免模板默认值和工程流程脱节
source $ad_hdl_dir/projects/scripts/adi_pd.tcl

# 模板目录显式保存为 smoke 与 ADI 入口契约，确保公共片段从当前模板目录加载
set "template_dir" [file dirname [info script]]

# SPI DAC 公共片段承接板级上下文，确保 SPI engine 和 DMA 在同一 BD 上下文生成
source [file join $template_dir common_bd.tcl]

# SYSID ROM 地址空间限制为模板初始化文件规模，避免 Vivado 期望额外内容
ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9

# SYSID ROM 文件固定在工程根，确保生成项目沿用 ADI 默认初始化文件名
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"

# rom_sys_0 与 axi_sysid_0 地址位宽保持一致，避免系统识别数据读取错位
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
