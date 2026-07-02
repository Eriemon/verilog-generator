# ZCU102 公共 block design 提供处理系统和基础时钟结构，确保模板只补充发送链路
source $ad_hdl_dir/projects/common/zcu102/zcu102_system_bd.tcl

# ADI 参数字典脚本提供跨片段参数表，确保 common_bd.tcl 能读取板级默认值
source $ad_hdl_dir/projects/scripts/adi_pd.tcl

# TX_JESD_L 保留 ADI 工程参数表契约，避免 common_bd.tcl 与 GT/link 宽度脱节
set "adProjectParams(TX_JESD_L)" [get_env_param TX_JESD_L 4]

# ADI 旧数组名继续暴露 TX lane 参数，避免外部 helper 只识别传统变量名
set "ad_project_params(TX_JESD_L)" $adProjectParams(TX_JESD_L)

# TX_JESD_S 保留 ADI 工程参数表契约，确保发送 TPL 的帧内样本组织可覆盖
set "adProjectParams(TX_JESD_S)" [get_env_param TX_JESD_S 1]

# ADI 旧数组名继续暴露 TX 帧内样本参数，保持公共片段读取兼容
set "ad_project_params(TX_JESD_S)" $adProjectParams(TX_JESD_S)

# 模板目录显式保存为 smoke 与 ADI 入口契约，确保公共片段从当前模板目录加载
set "template_dir" [file dirname [info script]]

# JESD DAC 公共片段承接板级参数，确保发送链路和 DMA 在同一 BD 上下文生成
source [file join $template_dir common_bd.tcl]

# SYSID ROM 地址空间限制为模板初始化文件规模，避免 Vivado 期望额外内容
ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9

# SYSID ROM 文件固定在工程根，确保生成项目沿用 ADI 默认初始化文件名
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"

# rom_sys_0 与 axi_sysid_0 地址位宽保持一致，避免系统识别数据读取错位
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
