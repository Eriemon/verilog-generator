//--------------模块实例化区域--------------//
generate if(C_ENABLE == "ON")begin:gen_feature
	Child_Module #(
		.C_DATA_WIDTH(C_DATA_WIDTH)			//参数说明
	)Child_Module_Inst(
		//------------时钟与复位------------//
		.i_clk(i_clk),						//时钟
		.i_rstn(i_rstn),					//复位
		//-------------控制通道-------------//
		.i_cfg_valid(i_cfg_valid),			//控制输入
		.o_cfg_ready(o_cfg_ready)			//控制输出
	);
end else begin:gen_bypass
	// bypass path
end
