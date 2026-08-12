# 确保 ADI HDL 根目录先进入 Tcl 环境，公共约束和 IO buffer 路径才能展开
source ../../../scripts/adi_env.tcl

# ADI Xilinx 封装保持 SPI DAC 项目结构与参考工程兼容，降低 EDA 工程偏差
source $ad_hdl_dir/projects/scripts/adi_project_xilinx.tcl

# ZedBoard 板级封装限制默认 part、board file 和公共约束来源
source $ad_hdl_dir/projects/scripts/adi_board.tcl

# 工程名绑定播放模板和目标板卡，避免生成目录覆盖其它模板产物
adi_project spi_dac_template_zed

# 文件集合包含 IO buffer 与板级约束，确保 SPI 三态引脚综合边界完整
adi_project_files spi_dac_template_zed [list \
	"system_top.v" \
	"system_constr.xdc" \
	"$ad_hdl_dir/library/common/ad_iobuf.v" \
	"$ad_hdl_dir/projects/common/zed/zed_system_constr.xdc" \
]

# Vivado 生成阶段在文件集合固定后触发，避免缺少 SPI 侧带约束时落盘半成品
adi_project_run spi_dac_template_zed
