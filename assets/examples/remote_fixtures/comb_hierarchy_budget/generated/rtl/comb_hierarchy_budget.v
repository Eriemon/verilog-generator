`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         Erie
// Engineer:        Erie
//
// Create Date:     2026/08/03 00:00:00
// Design Name:     comb_hierarchy_budget
// Module Name:     comb_hierarchy_budget
// Description:     description/comb_hierarchy_budget_Design.pdf
// Simulations:     testbench/vivado/2021.1/comb_hierarchy_budget
//
// Referrences:     None
//
// Dependencies:    None
//
// Version:         V1.0
// Revision Date:   2026/08/03 00:00:00
// History:
// Time             Version     Revised by        Contents
// 2026/08/03       V1.0        Erie              Create file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:        Erie
// 开发人员:        Erie
//
// 创建日期:        2026年08月03日
// 设计名称:        comb_hierarchy_budget
// 模块名称:        comb_hierarchy_budget
// 模块说明:        Description/comb_hierarchy_budget_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/comb_hierarchy_budget
//
// 参考资料:        None
//
// 依赖文件:        None
//
// 当前版本:        V1.0
// 修订日期:        2026年08月03日
// 修订历史:
// 时间             版本        修订人            修订内容
// 2026年08月03日   V1.0        Erie              创建文件

module comb_hierarchy_budget
(
	//-----------------用户接口-----------------//
	input i_a,                                  //第一组与运算输入
	input i_b,                                  //第二组与运算输入
	input i_c,                                  //子锥或运算输入
	output o_y                                  //三操作组合结果
);

	//---------------输出信号连线---------------//
	//用户接口
	assign o_y = ~((i_a & i_b) | i_c);          //三操作组合结果桥接

endmodule
