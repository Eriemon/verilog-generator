`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:			Erie
// Engineer:		Erie
// 
// Create Date: 	2026/05/17 15:11:57
// Design Name: 	jesd_dac_system_top
// Module Name: 	jesd_dac_system_top
// Description: 	Description/jesd_dac_system_top_Design.pdf
// Simulations:		TestBench/Vivado/2021.1/jesd_dac_system_top
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
// 设计名称: 		jesd_dac_system_top
// 模块名称: 		jesd_dac_system_top
// 模块说明:		Description/jesd_dac_system_top_Design.pdf
// 仿真工程: 		TestBench/Vivado/2021.1/jesd_dac_system_top
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
module jesd_dac_system_top
(
	//-----------------全局信号-----------------//
	input i_sys_clk,                            // port signal
	input i_tx_ref_clk,                         // port signal
	input i_sys_rstn,                           // port signal
	output o_tx_core_clk,                       // port signal

	//-----------------用户接口-----------------//
	input [15:0]i_waveform_data,                // port signal
	input i_waveform_valid,                     // port signal
	output o_dac_data_valid,                    // port signal
	output [15:0]o_dac_data                     // port signal
);

	//-----------------输出信号-----------------//
	//用户接口
	reg dac_data_valid_o = 0;                   // signal
	reg [15:0]dac_data_o = 0;                   // signal

	//---------------输出信号连线---------------//
	//全局信号
	assign o_tx_core_clk = i_tx_ref_clk;        // assign

	//用户接口
	assign o_dac_data_valid = dac_data_valid_o; // assign
	assign o_dac_data = dac_data_o;             // assign

	//-------------输出信号处理区域-------------//
	//用户接口
	always@(posedge i_tx_ref_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			dac_data_o <= 16'h0000;
		end else begin
			if(i_waveform_valid == 1'b1)begin
				dac_data_o <= i_waveform_data;
			end
		end
	end

	always@(posedge i_tx_ref_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			dac_data_valid_o <= 1'b0;
		end else begin
			dac_data_valid_o <= i_waveform_valid;
		end
	end

endmodule
