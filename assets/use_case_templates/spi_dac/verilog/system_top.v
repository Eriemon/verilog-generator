`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:			Erie
// Engineer:		Erie
// 
// Create Date: 	2026/05/17 15:11:57
// Design Name: 	spi_dac_system_top
// Module Name: 	spi_dac_system_top
// Description: 	Description/spi_dac_system_top_Design.pdf
// Simulations:		TestBench/Vivado/2021.1/spi_dac_system_top
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
// 设计名称: 		spi_dac_system_top
// 模块名称: 		spi_dac_system_top
// 模块说明:		Description/spi_dac_system_top_Design.pdf
// 仿真工程: 		TestBench/Vivado/2021.1/spi_dac_system_top
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
module spi_dac_system_top
(
	//-----------------全局信号-----------------//
	input i_sys_clk,                            // port signal
	input i_sys_rstn,                           // port signal
	output o_dac_reset_n,                       // port signal

	//-----------------用户接口-----------------//
	input i_dac_fault_n,                        // port signal
	input [15:0]i_sample_data,                  // port signal
	input i_sample_valid,                       // port signal
	output o_dac_spi_sclk,                      // port signal
	output o_dac_spi_sync_n,                    // port signal
	output o_dac_spi_sdo,                       // port signal
	output o_dac_ldac_n,                        // port signal
	output o_push_ready                         // port signal
);

	//-----------------输出信号-----------------//
	//用户接口
	reg [15:0]reg_last_sample = 0;              // signal
	reg dac_ldac_n_o = 0;                       // signal
	reg push_ready_o = 0;                       // signal

	//---------------输出信号连线---------------//
	//全局信号
	assign o_dac_reset_n = i_sys_rstn;          // assign

	//用户接口
	assign o_dac_spi_sclk = i_sys_clk;          // assign
	assign o_dac_spi_sync_n = ~(i_sample_valid & i_dac_fault_n); // assign
	assign o_dac_spi_sdo = reg_last_sample[15]; // assign
	assign o_dac_ldac_n = dac_ldac_n_o;         // assign
	assign o_push_ready = push_ready_o;         // assign

	//-------------输出信号处理区域-------------//
	//用户接口
	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			dac_ldac_n_o <= 1'b1;
		end else begin
			dac_ldac_n_o <= ~i_sample_valid;
		end
	end

	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			push_ready_o <= 1'b0;
		end else begin
			push_ready_o <= i_dac_fault_n;
		end
	end

	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			reg_last_sample <= 16'h0000;
		end else begin
			if((i_sample_valid == 1'b1) && (i_dac_fault_n == 1'b1))begin
				reg_last_sample <= i_sample_data;
			end
		end
	end

endmodule
