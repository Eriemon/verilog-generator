# 确保 ADI HDL 根目录先进入 Tcl 环境，ZCU102 公共约束才能稳定定位
source ../../../scripts/adi_env.tcl

# ADI Xilinx 封装保持 Vivado 项目目录与参考工程命令兼容，避免模板自建流程偏离
source $ad_hdl_dir/projects/scripts/adi_project_xilinx.tcl

# ZCU102 板级封装限制 GT 参考时钟和处理系统约束来源，防止跨板资源混用
source $ad_hdl_dir/projects/scripts/adi_board.tcl

# 工程名绑定发送模板和 ZCU102 目标板，避免生成目录覆盖其它模板产物
adi_project jesd_dac_template_zcu102

# 文件集合同时保留模板约束与 ZCU102 公共约束，确保顶层 pin 约束完整
adi_project_files jesd_dac_template_zcu102 [list \
	"system_top.v" \
	"system_constr.xdc" \
	"$ad_hdl_dir/projects/common/zcu102/zcu102_system_constr.xdc" \
]

# Vivado 生成阶段在文件集合固定后触发，避免缺少约束时落盘半成品
adi_project_run jesd_dac_template_zcu102
