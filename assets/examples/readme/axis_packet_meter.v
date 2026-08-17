`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         Erie
// Engineer:        Erie
//
// Create Date:     2026/07/18 18:00:00
// Design Name:     axis_packet_meter
// Module Name:     axis_packet_meter
// Description:     description/axis_packet_meter_Design.pdf
// Simulations:     testbench/vivado/2021.1/axis_packet_meter
//
// Referrences:     None
//
// Dependencies:    None
//
// Version:         V1.0
// Revision Date:   2026/07/18 18:00:00
// History:
// Time             Version     Revised by        Contents
// 2026/07/18       V1.0        Erie              Create file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:        Erie
// 开发人员:        Erie
//
// 创建日期:        2026年07月18日
// 设计名称:        axis_packet_meter
// 模块名称:        axis_packet_meter
// 模块说明:        Description/axis_packet_meter_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/axis_packet_meter
//
// 参考资料:        None
//
// 依赖文件:        None
//
// 当前版本:        V1.0
// 修订日期:        2026年07月18日
// 修订历史:
// 时间             版本        修订人            修订内容
// 2026年07月18日   V1.0        Erie              创建文件

module axis_packet_meter
(
	//-----------------全局信号-----------------//
	input i_axis_aclk,                          // AXI-Stream 接口采样时钟
	input i_axis_arstn,                         // 统计寄存器低有效异步复位

	//-----------------AXIS接口-----------------//
	//握手、包边界与字节有效性监测
	input i_axis_tvalid,                        // 上游声明当前传输拍有效
	input i_axis_tready,                        // 下游确认接受当前传输拍
	input i_axis_tlast,                         // 当前传输拍结束一个数据包
	input [3:0]i_axis_tkeep,                    // 当前传输拍的逐字节有效掩码

	//-----------------用户接口-----------------//
	//统计控制与读出
	input i_clear,                              // 同步清零全部累计统计值
	output [31:0]o_packet_count,                // 已完成数据包的饱和计数值
	output [31:0]o_byte_count                   // 已传输有效字节的饱和计数值
);

	//----------------寄存器信号----------------//
	//用户接口--统计控制与读出
	reg [31:0]reg_frame_total = 32'd0;          // 数据包计数的未钳位内部状态
	reg [31:0]reg_byte_total = 32'd0;           // 有效字节计数的未钳位内部状态

	//流水化统计中间值
	reg [2:0]reg_byte_quantity_buffered = 3'd0; // 流水缓存的单拍有效字节数量

	//-----------------标志信号-----------------//
	//AXI-Stream 事务与数据包边界
	wire flag_transfer;                         // 本拍完成 valid-ready 握手
	wire flag_packet_end;                       // 本拍完成数据包末拍握手
	reg flag_packet_end_pending = 1'b0;         // 待处理的数据包结束事件
	reg flag_transfer_pending = 1'b0;           // 待处理的有效传输事件
	reg flag_packet_overflow_pending = 1'b0;    // 待锁存的数据包计数进位事件
	reg flag_byte_overflow_pending = 1'b0;      // 待锁存的字节计数进位事件
	reg flag_packet_saturated = 1'b0;           // 数据包计数饱和状态
	reg flag_byte_saturated = 1'b0;             // 字节计数饱和状态

	//-----------------其他信号-----------------//
	//饱和计数使用的扩展位运算量
	wire [32:0]data_frame_sum;                  // 数据包计数器加一后的扩展结果
	wire [32:0]data_byte_sum;                   // 字节计数器流水累加扩展结果

	//-----------------输出信号-----------------//
	//用户接口--统计控制与读出
	wire [31:0]cnt_packet_o;                    // 数据包计数输出桥接值
	wire [31:0]cnt_byte_o;                      // 有效字节计数输出桥接值

	//---------------其他信号连线---------------//
	//AXIS接口
	//AXI-Stream 事务判定
	assign flag_transfer = i_axis_tvalid & i_axis_tready; // 仅在发送方与接收方同时就绪时采样
	assign flag_packet_end = flag_transfer & i_axis_tlast; // 仅在末拍握手时累计数据包

	//其他信号连线
	assign data_frame_sum = {1'b0, reg_frame_total} + 1'b1; // 用扩展位捕获数据包计数进位

	//单拍字节数与饱和累加量
	assign data_byte_sum = {1'b0, reg_byte_total} + reg_byte_quantity_buffered; // 使用缓存字节数捕获计数进位

	//饱和统计输出选择
	assign cnt_packet_o = (flag_packet_saturated | flag_packet_overflow_pending) ? 32'hFFFF_FFFF : reg_frame_total; // 饱和后钳位数据包计数
	assign cnt_byte_o = (flag_byte_saturated | flag_byte_overflow_pending) ? 32'hFFFF_FFFF : reg_byte_total; // 饱和后钳位有效字节计数

	//---------------输出信号连线---------------//
	//用户接口--统计控制与读出
	assign o_packet_count = cnt_packet_o;       // 桥接已钳位的数据包计数
	assign o_byte_count = cnt_byte_o;           // 桥接已钳位的有效字节计数

	//-------------主要任务处理区域-------------//
	//用户接口--统计控制与读出
	//数据包原始计数每次末拍握手后递增，饱和状态由独立寄存器保持
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			reg_frame_total <= 32'd0;           // 异步复位清空数据包累计值
		end else begin
			case(i_clear)
				1'b1:begin
					reg_frame_total <= 32'd0;   // 软件清零优先于当前数据包事件
				end
				default:begin
					case(flag_packet_end_pending)
						1'b1:begin
							reg_frame_total <= data_frame_sum[31:0]; // 累计流水数据包事件
						end
						default:begin
							reg_frame_total <= reg_frame_total; // 无数据包事件时保持原始计数
						end
					endcase
				end
			endcase
		end
	end

	//字节原始计数使用前级字节数量，每拍仍可接受新的传输事件
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			reg_byte_total <= 32'd0;            // 异步复位清空有效字节累计值
		end else begin
			case(i_clear)
				1'b1:begin
					reg_byte_total <= 32'd0;    // 软件清零优先于流水传输事件
				end
				default:begin
					case(flag_transfer_pending)
						1'b1:begin
							reg_byte_total <= data_byte_sum[31:0]; // 累计前级缓存的有效字节数量
						end
						default:begin
							reg_byte_total <= reg_byte_total; // 无流水传输事件时保持原始计数
						end
					endcase
				end
			endcase
		end
	end

	//数据包进位事件寄存器更新逻辑
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			flag_packet_overflow_pending <= 1'b0; // 异步复位清除数据包进位事件
		end else begin
			case(i_clear)
				1'b1:begin
					flag_packet_overflow_pending <= 1'b0; // 软件清零丢弃当前进位事件
				end
				default:begin
					flag_packet_overflow_pending <= flag_packet_end_pending & data_frame_sum[32]; // 缓存流水数据包进位
				end
			endcase
		end
	end

	//数据包饱和状态寄存器更新逻辑
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			flag_packet_saturated <= 1'b0;      // 异步复位清除数据包饱和状态
		end else begin
			case(i_clear)
				1'b1:begin
					flag_packet_saturated <= 1'b0; // 软件清零释放数据包饱和状态
				end
				default:begin
					flag_packet_saturated <= flag_packet_saturated | flag_packet_overflow_pending; // 锁存已缓存的数据包进位
				end
			endcase
		end
	end

	//字节数量预译码寄存器更新逻辑
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			reg_byte_quantity_buffered <= 3'd0; // 异步复位清除缓存字节数
		end else begin
			reg_byte_quantity_buffered <= i_axis_tkeep[0] + i_axis_tkeep[1] + i_axis_tkeep[2] + i_axis_tkeep[3]; // 流水预译码有效字节数量
		end
	end

	//数据包结束事件缓存标志更新逻辑
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			flag_packet_end_pending <= 1'b0;    // 异步复位清除数据包结束事件
		end else begin
			case(i_clear)
				1'b1:begin
					flag_packet_end_pending <= 1'b0; // 软件清零冲刷当前数据包事件
				end
				default:begin
					flag_packet_end_pending <= flag_packet_end; // 缓存本拍数据包末拍握手
				end
			endcase
		end
	end

	//首级传输事件缓存标志更新逻辑
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			flag_transfer_pending <= 1'b0;      // 异步复位清除待处理传输事件
		end else begin
			case(i_clear)
				1'b1:begin
					flag_transfer_pending <= 1'b0; // 清零时冲刷待处理传输事件
				end
				default:begin
					flag_transfer_pending <= flag_transfer; // 缓存本拍有效传输事件
				end
			endcase
		end
	end

	//字节进位事件寄存器更新逻辑
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			flag_byte_overflow_pending <= 1'b0; // 异步复位清除字节进位事件
		end else begin
			case(i_clear)
				1'b1:begin
					flag_byte_overflow_pending <= 1'b0; // 软件清零丢弃当前字节进位事件
				end
				default:begin
					flag_byte_overflow_pending <= flag_transfer_pending & data_byte_sum[32]; // 缓存流水字节进位
				end
			endcase
		end
	end

	//字节饱和状态寄存器更新逻辑
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			flag_byte_saturated <= 1'b0;        // 异步复位清除字节饱和状态
		end else begin
			case(i_clear)
				1'b1:begin
					flag_byte_saturated <= 1'b0; // 软件清零释放字节饱和状态
				end
				default:begin
					flag_byte_saturated <= flag_byte_saturated | flag_byte_overflow_pending; // 锁存已缓存的字节进位
				end
			endcase
		end
	end

endmodule
