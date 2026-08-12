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

	//-----------------计数信号-----------------//
	//当前握手拍的有效字节数
	wire [2:0]cnt_byte;                         // tkeep 掩码对应的有效字节数量

	//-----------------标志信号-----------------//
	//AXI-Stream 事务与数据包边界
	wire flag_transfer;                         // 本拍完成 valid-ready 握手
	wire flag_packet_end;                       // 本拍握手同时到达数据包末尾

	//-----------------其他信号-----------------//
	//饱和计数使用的扩展位运算量
	wire [31:0]data_byte_increment;             // 零扩展后的单拍有效字节增量
	wire [32:0]data_frame_sum;                  // 数据包计数器加一后的扩展结果
	wire [32:0]data_byte_sum;                   // 字节计数器累加后的扩展结果

	//-----------------输出信号-----------------//
	//用户接口--统计控制与读出
	reg [31:0]cnt_packet_o = 32'd0;             // 已完成数据包数量的内部输出缓存
	reg [31:0]cnt_byte_o = 32'd0;               // 已传输有效字节数量的内部输出缓存

	//---------------其他信号连线---------------//
	//AXI-Stream 事务判定
	assign flag_transfer = i_axis_tvalid & i_axis_tready; // 仅在发送方与接收方同时就绪时采样
	assign flag_packet_end = flag_transfer & i_axis_tlast; // 完成握手且 tlast 有效时结束数据包

	//单拍字节数与饱和累加量
	assign cnt_byte = {2'b00, i_axis_tkeep[0]} + {2'b00, i_axis_tkeep[1]} + {2'b00, i_axis_tkeep[2]} + {2'b00, i_axis_tkeep[3]}; // 累加四个字节使能位
	assign data_byte_increment = {29'd0, cnt_byte}; // 将单拍字节数零扩展为 32 位
	assign data_frame_sum = {1'b0, cnt_packet_o} + 1'b1; // 用扩展位捕获数据包计数进位
	assign data_byte_sum = {1'b0, cnt_byte_o} + {1'b0, data_byte_increment}; // 用扩展位捕获字节计数进位

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
			if(data_byte_sum[32] == 1'b0)begin
				cnt_byte_o <= data_byte_sum[31:0]; // 未产生进位时累加当前拍的有效字节数
			end else begin
				cnt_byte_o <= 32'hFFFF_FFFF;    // 本次累加将溢出时直接饱和
			end
		end else begin
			cnt_byte_o <= cnt_byte_o;           // 当前拍未握手时保持累计字节数
		end
	end

endmodule
