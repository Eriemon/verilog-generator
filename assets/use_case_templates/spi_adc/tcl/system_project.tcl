# 确保 ADI HDL 根目录先进入 Tcl 环境，ZedBoard 约束和公共脚本才能定位
source ../../../scripts/adi_env.tcl

# ADI Xilinx 封装保持 SPI ADC 项目结构与参考工程兼容，降低 EDA 工程偏差
source $ad_hdl_dir/projects/scripts/adi_project_xilinx.tcl

# ZedBoard 板级封装限制默认器件、board file 和处理系统基线
source $ad_hdl_dir/projects/scripts/adi_board.tcl

# 工程名绑定采集模板和目标板卡，避免生成目录覆盖其它模板产物
adi_project spi_adc_template_zed

# 文件集合包含 IO buffer 与板级约束，确保 ADC SPI、BUSY 和 CNV 引脚完整
adi_project_files spi_adc_template_zed [list \
	"system_top.v" \
	"system_constr.xdc" \
	"$ad_hdl_dir/library/common/ad_iobuf.v" \
	"$ad_hdl_dir/projects/common/zed/zed_system_constr.xdc" \
]

# Vivado 生成阶段在文件集合固定后触发，避免缺少采集链路约束时落盘半成品
adi_project_run spi_adc_template_zed
