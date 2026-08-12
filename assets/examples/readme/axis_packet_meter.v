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

	//-----------------标志信号-----------------//
	//AXI-Stream 事务与数据包边界
	wire flag_transfer;                         // 本拍完成 valid-ready 握手
	wire flag_packet_end;                       // 本拍握手同时到达数据包末尾

	//-----------------其他信号-----------------//
	//饱和计数使用的扩展位运算量
	wire [32:0]data_frame_sum;                  // 数据包计数器加一后的扩展结果
	wire [32:0]data_byte_sum_1;                 // 字节计数器加一后的扩展结果
	wire [32:0]data_byte_sum_2;                 // 字节计数器加二后的扩展结果
	wire [32:0]data_byte_sum_3;                 // 字节计数器加三后的扩展结果
	wire [32:0]data_byte_sum_4;                 // 字节计数器加四后的扩展结果

	//-----------------输出信号-----------------//
	//用户接口--统计控制与读出
	reg [31:0]cnt_packet_o = 32'd0;             // 已完成数据包数量的内部输出缓存
	reg [31:0]cnt_byte_o = 32'd0;               // 已传输有效字节数量的内部输出缓存

	//---------------其他信号连线---------------//
	//AXIS接口
	//AXI-Stream 事务判定
	assign flag_transfer = i_axis_tvalid & i_axis_tready; // 仅在发送方与接收方同时就绪时采样
	assign flag_packet_end = flag_transfer & i_axis_tlast; // 完成握手且 tlast 有效时结束数据包

	//其他信号连线
	assign data_frame_sum = {1'b0, cnt_packet_o} + 1'b1; // 用扩展位捕获数据包计数进位

	//单拍字节数与饱和累加量
	assign data_byte_sum_1 = {1'b0, cnt_byte_o} + 1'b1; // 捕获一个字节增量的进位
	assign data_byte_sum_2 = {1'b0, cnt_byte_o} + 2'd2; // 捕获两个字节增量的进位
	assign data_byte_sum_3 = {1'b0, cnt_byte_o} + 2'd3; // 捕获三个字节增量的进位
	assign data_byte_sum_4 = {1'b0, cnt_byte_o} + 3'd4; // 捕获四个字节增量的进位

	//---------------输出信号连线---------------//
	//用户接口--统计控制与读出
	assign o_packet_count = cnt_packet_o;       // 输出已完成数据包累计值
	assign o_byte_count = cnt_byte_o;           // 输出已传输有效字节累计值

	//-------------输出信号处理区域-------------//
	//用户接口--统计控制与读出
	//数据包计数器在数据包末拍握手后递增并在上限处饱和
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			cnt_packet_o <= 32'd0;              // 异步复位清空数据包累计值
		end else if(i_clear == 1'b1)begin
			cnt_packet_o <= 32'd0;              // 软件清零请求同步清空数据包统计
		end else if(flag_packet_end == 1'b1)begin
			if(data_frame_sum[32] == 1'b0)begin
				cnt_packet_o <= data_frame_sum[31:0]; // 未产生进位时记录新完成的数据包
			end else begin
				cnt_packet_o <= 32'hFFFF_FFFF;  // 达到上限后保持最大数据包计数
			end
		end else begin
			cnt_packet_o <= cnt_packet_o;       // 当前拍未结束数据包时保持累计值
		end
	end

	//字节计数器在每次有效握手后累加 tkeep 指示的字节数量
	always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
		if(i_axis_arstn == 1'b0)begin
			cnt_byte_o <= 32'd0;                // 异步复位清空有效字节累计值
		end else if(i_clear == 1'b1)begin
			cnt_byte_o <= 32'd0;                // 软件清零请求同步清空字节统计
		end else if(flag_transfer == 1'b1)begin
			case(i_axis_tkeep)
				4'b0000:begin
					cnt_byte_o <= cnt_byte_o;   // 当前拍没有有效字节时保持累计值
				end
				4'b0001, 4'b0010, 4'b0100, 4'b1000:begin
					if(data_byte_sum_1[32] == 1'b0)begin
						cnt_byte_o <= data_byte_sum_1[31:0]; // 累加一个有效字节
					end else begin
						cnt_byte_o <= 32'hFFFF_FFFF; // 一个字节增量溢出时饱和
					end
				end
				4'b0011, 4'b0101, 4'b0110, 4'b1001, 4'b1010, 4'b1100:begin
					if(data_byte_sum_2[32] == 1'b0)begin
						cnt_byte_o <= data_byte_sum_2[31:0]; // 累加两个有效字节
					end else begin
						cnt_byte_o <= 32'hFFFF_FFFF; // 两个字节增量溢出时饱和
					end
				end
				4'b0111, 4'b1011, 4'b1101, 4'b1110:begin
					if(data_byte_sum_3[32] == 1'b0)begin
						cnt_byte_o <= data_byte_sum_3[31:0]; // 累加三个有效字节
					end else begin
						cnt_byte_o <= 32'hFFFF_FFFF; // 三个字节增量溢出时饱和
					end
				end
				4'b1111:begin
					if(data_byte_sum_4[32] == 1'b0)begin
						cnt_byte_o <= data_byte_sum_4[31:0]; // 累加四个有效字节
					end else begin
						cnt_byte_o <= 32'hFFFF_FFFF; // 四个字节增量溢出时饱和
					end
				end
				default:begin
					cnt_byte_o <= cnt_byte_o;   // 四态仿真出现未知掩码时保持统计值
				end
			endcase
		end else begin
			cnt_byte_o <= cnt_byte_o;           // 当前拍未握手时保持累计字节数
		end
	end

endmodule
