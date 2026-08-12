`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:			Erie
// Engineer:		Erie
// 
// Create Date: 	2026/05/17 15:11:57
// Design Name: 	jesd_adc_system_top
// Module Name: 	jesd_adc_system_top
// Description: 	Description/jesd_adc_system_top_Design.pdf
// Simulations:		TestBench/Vivado/2021.1/jesd_adc_system_top
// 
// Referrences:		None
//
// Dependencies:	None
//
// Version:			V1.0
// Revision Date:	2026/05/17 15:11:57
// History:
//    Time			   Version	   Revised by			Contents
// 2026/05/17			V1.0		 Erie		Create file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:		Erie
// 开发人员:		Erie
// 
// 创建日期: 		2026年05月17日
// 设计名称: 		jesd_adc_system_top
// 模块名称: 		jesd_adc_system_top
// 模块说明:		Description/jesd_adc_system_top_Design.pdf
// 仿真工程: 		TestBench/Vivado/2021.1/jesd_adc_system_top
//	
// 参考资料:		None
//
// 依赖文件:		None
//
// 当前版本:		V1.0
// 修订日期:		2026年05月17日
// 修订历史:
//	时间			    版本		修订人				修订内容	
// 2026年05月17日		V1.0		 Erie		创建文件
module jesd_adc_system_top
(
	//-----------------全局信号-----------------//
	input i_sys_clk,                            // port signal
	input i_rx_ref_clk,                         // port signal
	input i_sys_rstn,                           // port signal
	output o_rx_core_clk,                       // port signal

	//-----------------用户接口-----------------//
	input [1:0]i_rx_data_p,                     // port signal
	input [1:0]i_rx_data_n,                     // port signal
	output o_sample_valid,                      // port signal
	output [31:0]o_sample_data                  // port signal
);

	//-----------------输出信号-----------------//
	//用户接口
	reg sample_valid_o = 0;                     // signal
	reg [31:0]sample_data_o = 0;                // signal

	//---------------输出信号连线---------------//
	//全局信号
	assign o_rx_core_clk = i_rx_ref_clk;        // assign

	//用户接口
	assign o_sample_valid = sample_valid_o;     // assign
	assign o_sample_data = sample_data_o;       // assign

	//-------------输出信号处理区域-------------//
	//用户接口
	always@(posedge i_rx_ref_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			sample_data_o <= 32'h00000000;
		end else begin
			sample_data_o <= {30'h00000000, i_rx_data_p[0], i_rx_data_n[0]};
		end
	end

	always@(posedge i_rx_ref_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			sample_valid_o <= 1'b0;
		end else begin
			sample_valid_o <= 1'b1;
		end
	end

endmodule
