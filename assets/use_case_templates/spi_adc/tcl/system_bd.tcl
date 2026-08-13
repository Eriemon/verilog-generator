# ZedBoard 公共 block design 提供处理系统和板级时钟，确保模板只补充采样链路
source $ad_hdl_dir/projects/common/zed/zed_system_bd.tcl

# ADI 参数辅助脚本统一环境覆盖语义，避免 SPI 默认值和工程流程脱节
source $ad_hdl_dir/projects/scripts/adi_pd.tcl

# SPI_CLK_REF_FREQUENCY 是采样周期换算基准，确保 CNV 脉冲节拍与 SPI 时钟一致
set countSpiClkRefFrequency [get_env_param SPI_CLK_REF_FREQUENCY 166]

# ADC_RESOLUTION 限制样本打包宽度，避免 SPI engine 输出被错误截断
set countAdcResolution [get_env_param ADC_RESOLUTION 16]

# ADC_NUM_CHANNELS 控制采样通道展开，确保 SPI engine 与外部 ADC 通道数一致
set countAdcNumChannels [get_env_param ADC_NUM_CHANNELS 1]

# ADC_SAMPLING_RATE 决定 CNV 周期，确保脉冲发生器匹配目标采样节拍
set countAdcSamplingRate [get_env_param ADC_SAMPLING_RATE 1000000]

# 模板目录使用当前脚本目录变量，确保公共片段从当前模板目录加载
set templateDir [file dirname [info script]]

# SPI ADC 公共片段承接采样参数，确保采集链路和 DMA 在同一 BD 上下文生成
source [file join $templateDir common_bd.tcl]

# SYSID ROM 地址空间限制为模板初始化文件规模，避免 Vivado 期望额外内容
ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9

# SYSID ROM 文件固定在工程根，确保生成项目沿用 ADI 默认初始化文件名
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"

# rom_sys_0 与 axi_sysid_0 地址位宽保持一致，避免系统识别数据读取错位
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
