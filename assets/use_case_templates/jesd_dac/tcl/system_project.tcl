source ../../../scripts/adi_env.tcl
source $ad_hdl_dir/projects/scripts/adi_project_xilinx.tcl
source $ad_hdl_dir/projects/scripts/adi_board.tcl

adi_project jesd_dac_template_zcu102

adi_project_files jesd_dac_template_zcu102 [list \
	"system_top.v" \
	"system_constr.xdc" \
	"$ad_hdl_dir/projects/common/zcu102/zcu102_system_constr.xdc" \
]

adi_project_run jesd_dac_template_zcu102
