`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:			Erie
// Engineer:		Erie
//
// Create Date: 	2026/05/03 12:00:00
// Design Name: 	comb_parity_mux
// Module Name: 	comb_parity_mux
// Description: 	Description/comb_parity_mux_Design.pdf
// Simulations:		TestBench/Vivado/2021.1/comb_parity_mux
//
// Referrences:		None
//
// Dependencies:	None
//
// Version:			V1.0
// Revision Date:	2026/05/03 12:00:00
// History:
//    Time			   Version	   Revised by			Contents
// 2026/05/03		V1.0		Erie		Create file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:		Erie
// 开发人员:		Erie
//
// 创建日期: 		2026年05月03日
// 设计名称: 		comb_parity_mux
// 模块名称: 		comb_parity_mux
// 模块说明:		Description/comb_parity_mux_Design.pdf
// 仿真工程: 		TestBench/Vivado/2021.1/comb_parity_mux
//
// 参考资料:		None
//
// 依赖文件:		None
//
// 当前版本:		V1.0
// 修订日期:		2026年05月03日
// 修订历史:
//	时间			    版本		修订人				修订内容
// 2026年05月03日		V1.0		 Erie		创建文件
module comb_parity_mux
#(
	parameter C_DATA_WIDTH = 8	//数据总线位宽
)
(
	//-----------------用户接口-----------------//
	input i_sel,                                //输入选择控制信号
	input [7:0] i_a,                            //选择为零时的主路径输入数据
	input [7:0] i_b,                            //选择为一时的旁路输入数据
	output [7:0] o_y,                           //八位组合选择输出数据
	output o_parity                             //输出数据奇偶校验标志
);

	//---------------输出信号连线---------------//
	//用户接口
	assign o_y = i_sel ? i_b : i_a;             //组合主输出桥接
	assign o_parity = ^o_y;                     //奇偶校验输出桥接

endmodule
