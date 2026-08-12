`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         Erie
// Engineer:        Erie
//
// Create Date:     2026/05/03 12:00:00
// Design Name:     ready_valid_slice
// Module Name:     ready_valid_slice
// Description:     description/ready_valid_slice_Design.pdf
// Simulations:     testbench/vivado/2021.1/ready_valid_slice
//
// Referrences:     None
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
// 设计名称:        ready_valid_slice
// 模块名称:        ready_valid_slice
// 模块说明:        Description/ready_valid_slice_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/ready_valid_slice
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

module ready_valid_slice
#(
	parameter C_DATA_WIDTH = 8                  // 数据总线位宽
)
(
	//-----------------全局信号-----------------//
	input i_clk,                                // 工作时钟
	input i_rstn,                               // 低有效复位

	//-----------------用户接口-----------------//

	//IN接口
	input i_in_valid,                           // 输入有效标志
	output o_in_ready,                          // 输出就绪标志
	input [7:0]i_in_data,                       // 8位输入数据总线

	//OUT接口
	output o_out_valid,                         // 输出有效标志
	input i_out_ready,                          // 输入就绪标志
	output [7:0]o_out_data                      // 8位输出数据总线
);

	//---------------配置参数区域---------------//
	//复位常量
	localparam DATA_RESET_VALUE = {C_DATA_WIDTH{1'b0}}; // 数据复位默认值

	//----------------寄存器信号----------------//
	//上游数据缓存
	reg [7:0]reg_data_hold = DATA_RESET_VALUE;  // 输入数据缓存寄存器

	//-----------------标志信号-----------------//
	//上游有效状态
	reg flag_valid_hold = 1'b0;                 // 输入有效缓存标志

	//-----------------输出信号-----------------//
	//用户接口
	reg out_valid_o = 1'b0;                     // 输出有效缓存寄存器
	reg [7:0]out_data_o = DATA_RESET_VALUE;     // 输出数据缓存寄存器

	//---------------输出信号连线---------------//
	//用户接口
	assign o_in_ready = 1'b0;                   // 未使用输出固定为低电平
	assign o_out_valid = out_valid_o;           // 输出有效标志桥接
	assign o_out_data = out_data_o;             // 输出数据总线桥接

	//-------------输出信号处理区域-------------//
	//用户接口
	//输出有效标志寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			out_valid_o <= 1'b0;                // 复位清除下游有效
		end else if(flag_valid_hold == 1'b1)begin
			out_valid_o <= 1'b1;                // 缓存存在时声明下游有效
		end else begin
			out_valid_o <= 1'b0;                // 缓存为空时撤销下游有效
		end
	end

	//输出数据寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			out_data_o <= DATA_RESET_VALUE;     // 复位装载下游数据默认值
		end else if(flag_valid_hold == 1'b1)begin
			out_data_o <= reg_data_hold;        // 缓存数据送往下游接口
		end else begin
			out_data_o <= out_data_o;           // 下游未取走时保持数据
		end
	end

	//-------------主要任务处理区域-------------//
	//输入数据缓存寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			reg_data_hold <= DATA_RESET_VALUE;  // 复位装载上游缓存默认值
		end else if(i_in_valid == 1'b1)begin
			reg_data_hold <= i_in_data;         // 接收上游有效数据
		end else begin
			reg_data_hold <= reg_data_hold;     // 等待新输入时保持缓存
		end
	end

	//输入有效缓存标志更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			flag_valid_hold <= 1'b0;            // 复位清除上游有效缓存
		end else begin
			flag_valid_hold <= i_in_valid;      // 记录上游有效握手状态
		end
	end

endmodule
