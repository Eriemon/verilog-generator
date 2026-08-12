`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         Erie
// Engineer:        Erie
//
// Create Date:     2026/08/01 12:00:00
// Design Name:     comb_operation_budget
// Module Name:     comb_operation_budget
// Description:     description/comb_operation_budget_Design.pdf
// Simulations:     testbench/vivado/2021.1/comb_operation_budget
//
// Referrences:     None
//
// Dependencies:    None
//
// Version:         V1.0
// Revision Date:   2026/08/01 12:00:00
// History:
// Time             Version     Revised by        Contents
// 2026/08/01       V1.0        Erie              Create file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:        Erie
// 开发人员:        Erie
//
// 创建日期:        2026年08月01日
// 设计名称:        comb_operation_budget
// 模块名称:        comb_operation_budget
// 模块说明:        Description/comb_operation_budget_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/comb_operation_budget
//
// 参考资料:        None
//
// 依赖文件:        None
//
// 当前版本:        V1.0
// 修订日期:        2026年08月01日
// 修订历史:
// 时间             版本        修订人            修订内容
// 2026年08月01日   V1.0        Erie              创建文件

module comb_operation_budget
(
	//-----------------全局信号-----------------//
	input i_clk,                                // 工作时钟
	input i_rstn,                               // 低有效复位

	//-----------------用户接口-----------------//
	input i_valid,                              // 输入事务有效标志
	input [3:0]i_data,                          // 四位输入条件数据
	output o_input_ready,                       // 输入事务接收许可
	input i_output_ready,                       // 下游结果接收许可
	output o_valid,                             // 输出结果有效标志
	output o_match                              // 两级条件匹配结果
);

	//-----------------标志信号-----------------//
	//首级条件结果
	reg flag_low = 1'b0;                        // 低两位条件缓存标志
	reg flag_high = 1'b0;                       // 高两位条件缓存标志
	reg flag_pair_valid = 1'b0;                 // 首级条件有效缓存标志
	reg flag_output_empty = 1'b1;               // 输出缓存空闲标志

	//-----------------其他信号-----------------//
	//首级组合条件结果
	wire data_pair_match;                       // 首级任一条件命中结果

	//-----------------输出信号-----------------//
	//用户接口
	wire input_ready_o;                         // 流水级更新许可标志
	wire valid_o;                               // 输出有效桥接标志
	reg match_o = 1'b0;                         // 输出匹配缓存标志

	//---------------其他信号连线---------------//
	//其他信号连线
	//流水控制
	assign input_ready_o = i_output_ready | flag_output_empty; // 空闲或下游就绪时允许流水更新
	assign valid_o = ~flag_output_empty;        // 空闲状态取反后形成输出有效标志
	assign data_pair_match = flag_low | flag_high; // 合并首级两路条件结果

	//---------------输出信号连线---------------//
	//用户接口
	assign o_input_ready = input_ready_o;       // 流水更新许可桥接到输入 ready
	assign o_valid = valid_o;                   // 输出有效标志桥接
	assign o_match = match_o;                   // 输出匹配结果桥接

	//-------------输出信号处理区域-------------//
	//用户接口
	//输出匹配缓存寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			match_o <= 1'b0;                    // 复位清除输出匹配缓存
		end else begin
			case(input_ready_o)
				1'b1:begin
					match_o <= data_pair_match; // 流水推进时缓存首级条件合并结果
				end
				default:begin
					match_o <= match_o;         // 下游停顿时保持匹配缓存
				end
			endcase
		end
	end

	//-------------主要任务处理区域-------------//
	//输出空闲缓存标志更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			flag_output_empty <= 1'b1;          // 复位后声明输出缓存空闲
		end else begin
			case(input_ready_o)
				1'b1:begin
					flag_output_empty <= ~flag_pair_valid; // 流水推进时更新输出空闲状态
				end
				default:begin
					flag_output_empty <= flag_output_empty; // 下游停顿时保持输出空闲状态
				end
			endcase
		end
	end

	//低两位条件缓存寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			flag_low <= 1'b0;                   // 复位清除低两位条件缓存
		end else begin
			case(input_ready_o)
				1'b1:begin
					flag_low <= i_data[0] & i_data[1]; // 流水推进时计算低两位条件
				end
				default:begin
					flag_low <= flag_low;       // 停顿时保持低半区条件缓存
				end
			endcase
		end
	end

	//高两位条件缓存寄存器更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			flag_high <= 1'b0;                  // 复位清除高两位条件缓存
		end else begin
			case(input_ready_o)
				1'b1:begin
					flag_high <= i_data[2] & i_data[3]; // 流水推进时计算高两位条件
				end
				default:begin
					flag_high <= flag_high;     // 停顿时保持高半区条件缓存
				end
			endcase
		end
	end

	//首级有效缓存标志更新逻辑
	always@(posedge i_clk or negedge i_rstn)begin
		if(i_rstn == 1'b0)begin
			flag_pair_valid <= 1'b0;            // 复位清除首级有效缓存
		end else begin
			case(input_ready_o)
				1'b1:begin
					flag_pair_valid <= i_valid; // 流水可推进时采样输入有效状态
				end
				default:begin
					flag_pair_valid <= flag_pair_valid; // 下游停顿时保持首级有效状态
				end
			endcase
		end
	end

endmodule
