# 确保 ADI HDL 根目录先进入 Tcl 环境，MxFE 所需 ZCU102 约束和公共脚本才能定位
source ../../../scripts/adi_env.tcl

# ADI Xilinx 封装保持混合收发模板与参考项目结构一致，降低 EDA 工程偏差
source $ad_hdl_dir/projects/scripts/adi_project_xilinx.tcl

# ZCU102 板级封装限制 MxFE 参考时钟、TDD 侧带和处理系统基线
source $ad_hdl_dir/projects/scripts/adi_board.tcl

# 工程参数在创建 block design 前注入 ADI 参数表，确保 system_bd.tcl 读取到可覆盖的 FIFO 与链路默认值
adi_project mxfe_mixed_template_zcu102 0 [list \
	RX_KS_PER_CHANNEL [get_env_param RX_KS_PER_CHANNEL 4] \
	TX_KS_PER_CHANNEL [get_env_param TX_KS_PER_CHANNEL 8] \
	NUM_LINKS [get_env_param NUM_LINKS 2] \
]

# 文件集合同时保留模板约束与 ZCU102 公共约束，确保 MxFE 顶层引脚覆盖完整
adi_project_files mxfe_mixed_template_zcu102 [list \
	"system_top.v" \
	"system_constr.xdc" \
	"$ad_hdl_dir/projects/common/zcu102/zcu102_system_constr.xdc" \
]

# Vivado 生成阶段在文件集合固定后触发，避免缺少约束时落盘半成品
adi_project_run mxfe_mixed_template_zcu102
