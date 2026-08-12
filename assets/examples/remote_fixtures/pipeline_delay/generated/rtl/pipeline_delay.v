`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         Erie
// Engineer:        Erie
//
// Create Date:     2026/05/03 12:00:00
// Design Name:     pipeline_delay
// Module Name:     pipeline_delay
// Description:     Description/pipeline_delay_Design.pdf
// Simulations:     TestBench/Vivado/2021.1/pipeline_delay
//
// References:     None
//
// Dependencies:    None
//
// Version:         V1.0
// Revision Date:   2026/05/03 12:00:00
// History:
// Time             Version     Revised by        Contents
// 2026/05/03       V1.0        Erie              Create file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:        Erie
// 开发人员:        Erie
//
// 创建日期:        2026年05月03日
// 设计名称:        pipeline_delay
// 模块名称:        pipeline_delay
// 模块说明:        Description/pipeline_delay_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/pipeline_delay
//
// 参考资料:        None
//
// 依赖文件:        None
//
// 当前版本:        V1.0
// 修订日期:        2026年05月03日
// 修订历史:
// 时间             版本        修订人            修订内容
// 2026年05月03日   V1.0        Erie              创建文件

// 输入数据单拍缓存与延迟输出桥接模块
module pipeline_delay
#(
	parameter C_DATA_WIDTH = 8                  // 数据总线位宽
)
(
	//-----------------全局信号-----------------//
	input i_clk,                                // 工作时钟
	input i_rstn,                               // 低有效复位

	//-----------------用户接口-----------------//
	input i_valid,                              // 输入有效标志
	input [7:0]i_data,                          // 8位输入数据总线
	output o_valid,                             // 输出有效标志
	output [7:0]o_data                          // 8位输出数据总线
);

	//---------------配置参数区域---------------//
	//复位常量
	localparam DATA_RESET_VALUE = {C_DATA_WIDTH{1'b0}}; // 数据复位默认值

	//----------------寄存器信号----------------//
	//输入数据缓存
	reg [7:0]reg_data_hold = DATA_RESET_VALUE;  // 输入数据缓存寄存器

	//-----------------标志信号-----------------//
	//有效缓存状态
	reg flag_valid_hold = 1'b0;                 // 输入有效缓存标志

	//-----------------输出信号-----------------//
	//用户接口
	reg valid_o = 1'b0;                         // 输出有效缓存寄存器
	reg [7:0]data_o = DATA_RESET_VALUE;         // 输出数据缓存寄存器

	//---------------输出信号连线---------------//
	//用户接口
	assign o_valid = valid_o;                   // 输出有效标志桥接
	assign o_data = data_o;                     // 输出数据总线桥接

	//-------------输出信号处理区域-------------//
	//输出有效标志寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			valid_o <= 1'b0;                    // 复位清除输出有效
		end else if(flag_valid_hold == 1'b1)begin
			valid_o <= 1'b1;                    // 缓存有效时拉高输出
		end else begin
			valid_o <= 1'b0;                    // 无缓存有效时清零输出
		end
	end

	//输出数据寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			data_o <= DATA_RESET_VALUE;         // 复位装载数据默认值
		end else if(flag_valid_hold == 1'b1)begin
			data_o <= reg_data_hold;            // 有效缓存驱动输出数据
		end else begin
			data_o <= data_o;                   // 无新缓存时保持输出数据
		end
	end

	//-------------主要任务处理区域-------------//
	//输入数据缓存寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			reg_data_hold <= DATA_RESET_VALUE;  // 复位装载输入缓存默认值
		end else if(i_valid == 1'b1)begin
			reg_data_hold <= i_data;            // 采样有效输入数据
		end else begin
			reg_data_hold <= reg_data_hold;     // 保持上一拍输入缓存
		end
	end

	//输入有效缓存标志更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			flag_valid_hold <= 1'b0;            // 复位清除有效缓存
		end else begin
			flag_valid_hold <= i_valid;         // 锁存当前输入有效状态
		end
	end

endmodule
