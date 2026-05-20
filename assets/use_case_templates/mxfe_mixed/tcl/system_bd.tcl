source $ad_hdl_dir/projects/common/zcu102/zcu102_system_bd.tcl
source $ad_hdl_dir/projects/common/xilinx/adcfifo_bd.tcl
source $ad_hdl_dir/projects/common/xilinx/dacfifo_bd.tcl

set RX_KS_PER_CHANNEL [get_env_param RX_KS_PER_CHANNEL 4]
set TX_KS_PER_CHANNEL [get_env_param TX_KS_PER_CHANNEL 8]
set NUM_LINKS [get_env_param NUM_LINKS 2]

set template_dir [file dirname [info script]]
source [file join $template_dir common_bd.tcl]

ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
