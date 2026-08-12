`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:			Erie
// Engineer:		Erie
// 
// Create Date: 	2026/05/17 15:11:57
// Design Name: 	spi_adc_system_top
// Module Name: 	spi_adc_system_top
// Description: 	Description/spi_adc_system_top_Design.pdf
// Simulations:		TestBench/Vivado/2021.1/spi_adc_system_top
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
// 设计名称: 		spi_adc_system_top
// 模块名称: 		spi_adc_system_top
// 模块说明:		Description/spi_adc_system_top_Design.pdf
// 仿真工程: 		TestBench/Vivado/2021.1/spi_adc_system_top
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
module spi_adc_system_top
(
	//-----------------全局信号-----------------//
	input i_sys_clk,                            // port signal
	input i_sys_rstn,                           // port signal
	inout io_adc_resetn,                        // port signal

	//-----------------用户接口-----------------//
	input i_adc_busy,                           // port signal

	//ADC_SPI接口
	input i_adc_spi_sdo,                        // port signal
	output o_adc_spi_sclk,                      // port signal
	output o_adc_spi_csn,                       // port signal
	output o_adc_spi_sdi,                       // port signal
	output o_adc_cnv,                           // port signal
	output o_sample_valid,                      // port signal
	output [15:0]o_sample_data                  // port signal
);

	//----------------寄存器信号----------------//
	reg reg_busy_d1 = 0;                        // signal

	//-----------------其他信号-----------------//
	wire wire_busy_fall;                        // signal
	wire wire_reset_drive;                      // signal

	//-----------------输出信号-----------------//
	//用户接口
	reg adc_cnv_o = 0;                          // signal
	reg sample_valid_o = 0;                     // signal
	reg [15:0]sample_data_o = 0;                // signal

	//---------------其他信号连线---------------//
	assign wire_busy_fall = reg_busy_d1 & (~i_adc_busy); // assign
	assign wire_reset_drive = i_sys_rstn;       // assign
	assign io_adc_resetn = wire_reset_drive;    // assign

	//---------------输出信号连线---------------//
	//用户接口
	assign o_adc_spi_sclk = i_sys_clk;          // assign
	assign o_adc_spi_csn = ~adc_cnv_o;          // assign
	assign o_adc_spi_sdi = 1'b0;                // assign
	assign o_adc_cnv = adc_cnv_o;               // assign
	assign o_sample_valid = sample_valid_o;     // assign
	assign o_sample_data = sample_data_o;       // assign

	//-------------输出信号处理区域-------------//
	//用户接口
	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			adc_cnv_o <= 1'b0;
		end else begin
			adc_cnv_o <= ~adc_cnv_o;
		end
	end

	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			sample_data_o <= 16'h0000;
		end else begin
			if(wire_busy_fall == 1'b1)begin
				sample_data_o <= {15'h0000, i_adc_spi_sdo};
			end
		end
	end

	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			sample_valid_o <= 1'b0;
		end else begin
			sample_valid_o <= wire_busy_fall;
		end
	end

	//-------------主要任务处理区域-------------//
	always@(posedge i_sys_clk or negedge i_sys_rstn)begin
		if(i_sys_rstn == 1'b0)begin
			reg_busy_d1 <= 1'b0;
		end else begin
			reg_busy_d1 <= i_adc_busy;
		end
	end

endmodule
