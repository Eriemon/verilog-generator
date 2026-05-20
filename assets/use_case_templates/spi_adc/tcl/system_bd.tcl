source $ad_hdl_dir/projects/common/zed/zed_system_bd.tcl
source $ad_hdl_dir/projects/scripts/adi_pd.tcl

set spi_clk_ref_frequency [get_env_param SPI_CLK_REF_FREQUENCY 166]
set ADC_RESOLUTION [get_env_param ADC_RESOLUTION 16]
set ADC_NUM_CHANNELS [get_env_param ADC_NUM_CHANNELS 1]
set ADC_SAMPLING_RATE [get_env_param ADC_SAMPLING_RATE 1000000]

set template_dir [file dirname [info script]]
source [file join $template_dir common_bd.tcl]

ad_ip_parameter axi_sysid_0 CONFIG.ROM_ADDR_BITS 9
ad_ip_parameter rom_sys_0 CONFIG.PATH_TO_FILE "[pwd]/mem_init_sys.txt"
ad_ip_parameter rom_sys_0 CONFIG.ROM_ADDR_BITS 9
