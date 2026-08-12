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
	parameter C_DATA_WIDTH = 32'd8              // 数据总线位宽
)
(
	//-----------------全局信号-----------------//
	input i_clk,                                // 工作时钟
	input i_rstn,                               // 低有效复位

	//-----------------入IN接口-----------------//

	//IN接口
	input i_in_valid,                           // 输入有效标志
	output o_in_ready,                          // 输出就绪标志
	input [C_DATA_WIDTH - 1:0]i_in_data,        // 输入数据总线

	//----------------出OUT接口-----------------//

	//OUT接口
	output o_out_valid,                         // 输出有效标志
	input i_out_ready,                          // 输入就绪标志
	output [C_DATA_WIDTH - 1:0]o_out_data       // 输出数据总线
);

	//---------------配置参数区域---------------//
	//复位常量
	localparam DATA_RESET_VALUE = {C_DATA_WIDTH{1'b0}}; // 数据复位默认值

	//-----------------其他信号-----------------//
	//上下游握手状态
	wire [1:0]input_transfer_code;              // 上游接收状态编码
	wire [1:0]output_transfer_code;             // 下游消费状态编码

	//-----------------输出信号-----------------//
	//入IN接口
	//输出缓存空闲状态
	reg in_ready_o = 1'b1;                      // 输出缓存空闲标志

	//出OUT接口
	//输出缓存有效状态
	reg out_valid_o = 1'b0;                     // 输出缓存有效标志
	reg [C_DATA_WIDTH - 1:0]out_data_o = DATA_RESET_VALUE; // 输入数据缓存寄存器

	//---------------其他信号连线---------------//
	//IN接口
	//IN
	//内部控制信号
	assign input_transfer_code = {o_in_ready, i_in_valid}; // 上游接收状态

	//OUT接口
	//OUT
	assign output_transfer_code = {o_out_valid, i_out_ready}; // 下游消费状态

	//---------------输出信号连线---------------//
	//入IN接口
	//用户接口
	assign o_in_ready = in_ready_o;             // 缓冲区为空时接收输入

	//出OUT接口
	assign o_out_valid = out_valid_o;           // 输出有效标志桥接
	assign o_out_data = out_data_o;             // 输出数据总线桥接

	//-------------输出信号处理区域-------------//
	//入IN接口
	//输入可接收状态跟踪缓存占用
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			in_ready_o <= 1'b1;                 // 复位声明输出缓存空闲
		end else begin
			case(input_transfer_code)
				2'b11:begin
					in_ready_o <= 1'b0;         // 输入握手后声明缓存占用
				end
				default:begin
					case(output_transfer_code)
						2'b11:begin
							in_ready_o <= 1'b1; // 下游消费后声明缓存空闲
						end
						default:begin
							in_ready_o <= in_ready_o; // 保持输入接收状态
						end
					endcase
				end
			endcase
		end
	end

	//出OUT接口
	//下游输出状态跟踪缓存有效
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			out_valid_o <= 1'b0;                // 复位清除输出有效
		end else begin
			case(input_transfer_code)
				2'b11:begin
					out_valid_o <= 1'b1;        // 输入握手后声明输出有效
				end
				default:begin
					case(output_transfer_code)
						2'b11:begin
							out_valid_o <= 1'b0; // 下游消费后清除输出有效
						end
						default:begin
							out_valid_o <= out_valid_o; // 保持输出有效状态
						end
					endcase
				end
			endcase
		end
	end

	//出OUT接口
	//下游数据寄存器保存通过的输入载荷
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			out_data_o <= DATA_RESET_VALUE;     // 复位装载上游缓存默认值
		end else begin
			case(input_transfer_code)
				2'b11:begin
					out_data_o <= i_in_data;    // 输入握手时锁存数据
				end
				default:begin
					out_data_o <= out_data_o;   // 无输入握手时保持输出数据
				end
			endcase
		end
	end

endmodule
