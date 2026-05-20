source ../../../scripts/adi_env.tcl
source $ad_hdl_dir/projects/scripts/adi_project_xilinx.tcl
source $ad_hdl_dir/projects/scripts/adi_board.tcl

adi_project jesd_adc_template_zc706

adi_project_files jesd_adc_template_zc706 [list \
	"system_top.v" \
	"system_constr.xdc" \
]

adi_project_run jesd_adc_template_zc706
