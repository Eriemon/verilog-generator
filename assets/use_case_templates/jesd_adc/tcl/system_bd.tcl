source $ad_hdl_dir/projects/common/zc706/zc706_system_bd.tcl
source $ad_hdl_dir/projects/scripts/adi_pd.tcl

set RX_JESD_L [get_env_param RX_JESD_L 2]
set RX_JESD_S [get_env_param RX_JESD_S 1]

set template_dir [file dirname [info script]]
source [file join $template_dir common_bd.tcl]

ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
