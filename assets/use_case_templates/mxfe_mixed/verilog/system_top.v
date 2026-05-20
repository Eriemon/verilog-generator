`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:			Erie
// Engineer:		Erie
// 
// Create Date: 	2026/05/17 15:11:57
// Design Name: 	mxfe_mixed_system_top
// Module Name: 	mxfe_mixed_system_top
// Description: 	Description/mxfe_mixed_system_top_Design.pdf
// Simulations:		TestBench/Vivado/2021.1/mxfe_mixed_system_top
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
// 设计名称: 		mxfe_mixed_system_top
// 模块名称: 		mxfe_mixed_system_top
// 模块说明:		Description/mxfe_mixed_system_top_Design.pdf
// 仿真工程: 		TestBench/Vivado/2021.1/mxfe_mixed_system_top
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
module mxfe_mixed_system_top
(
	//-----------------全局信号-----------------//
	input i_sys_clk,                            // port signal
	input i_rx_ref_clk,                         // port signal
	input i_tx_ref_clk,                         // port signal
	input i_sys_rstn,                           // port signal
	output o_sys_clk,                           // port signal

	//-----------------用户接口-----------------//
	input i_tdd_sync,                           // port signal
	input [7:0]i_spi_pmod_sdo,                  // port signal
	output o_tdd_enabled,                       // port signal
	output o_tdd_rx_enable,                     // port signal
	output o_tdd_tx_enable,                     // port signal
	output [7:0]o_spi_pmod_csn                  // port signal
);

	//-----------------输出信号-----------------//
	//用户接口
	reg tdd_enabled_o = 0;                      // signal
	reg tdd_rx_enable_o = 0;                    // signal
	reg tdd_tx_enable_o = 0;                    // signal

	//---------------输出信号连线---------------//
	//全局信号
	assign o_sys_clk = i_sys_clk;               // assign

	//用户接口
	assign o_tdd_enabled = tdd_enabled_o;       // assign
	assign o_tdd_rx_enable = tdd_rx_enable_o;   // assign
	assign o_tdd_tx_enable = tdd_tx_enable_o;   // assign
	assign o_spi_pmod_csn = ~i_spi_pmod_sdo;    // assign

	//-------------输出信号处理区域-------------//
	//用户接口
	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			tdd_enabled_o <= 1'b0;
		end else begin
			tdd_enabled_o <= i_tdd_sync;
		end
	end

	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			tdd_rx_enable_o <= 1'b0;
		end else begin
			tdd_rx_enable_o <= i_rx_ref_clk;
		end
	end

	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			tdd_tx_enable_o <= 1'b0;
		end else begin
			tdd_tx_enable_o <= i_tx_ref_clk;
		end
	end

endmodule
