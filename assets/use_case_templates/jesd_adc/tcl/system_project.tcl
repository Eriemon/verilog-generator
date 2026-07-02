# 确保 ADI HDL 根目录先进入 Tcl 环境，ZC706 公共资源才能被工程脚本定位
source ../../../scripts/adi_env.tcl

# ADI Xilinx 封装保持 Vivado 项目目录与参考工程命令兼容，避免模板自建流程偏离
source $ad_hdl_dir/projects/scripts/adi_project_xilinx.tcl

# ZC706 板级封装限制器件、参考时钟和处理系统约束来源，防止跨板资源混用
source $ad_hdl_dir/projects/scripts/adi_board.tcl

# 工程名绑定采集模板和 ZC706 目标板，避免生成目录覆盖其它模板产物
adi_project jesd_adc_template_zc706

# 文件集合只包含模板顶层和本地约束，确保采集链路约束边界清晰
adi_project_files jesd_adc_template_zc706 [list \
	"system_top.v" \
	"system_constr.xdc" \
]

# Vivado 生成阶段在文件集合固定后触发，避免缺少顶层或约束时落盘半成品
adi_project_run jesd_adc_template_zc706
