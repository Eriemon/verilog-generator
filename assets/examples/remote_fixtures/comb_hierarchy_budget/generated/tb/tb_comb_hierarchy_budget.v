`timescale 1ns / 1ps

module tb_comb_hierarchy_budget;              // 模块: 自检测试平台 - 验证三操作组合结果
	reg i_a = 1'b0;                              // 寄存器: 第一输入 - 驱动首级与运算
	reg i_b = 1'b0;                              // 寄存器: 第二输入 - 驱动首级与运算
	reg i_c = 1'b0;                              // 寄存器: 第三输入 - 驱动子锥或运算
	wire o_y;                                    // 连线: 组合结果 - 观测三操作输出

	comb_hierarchy_budget comb_hierarchy_budget_Inst_dut ( // 实例: 被测模块 - 连接四输入组合锥
		.i_a(i_a),                                 // 端口映射: 第一输入 - 连接测试激励
		.i_b(i_b),                                 // 端口映射: 第二输入 - 连接测试激励
		.i_c(i_c),                                 // 端口映射: 第三输入 - 连接测试激励
		.o_y(o_y)                                  // 端口映射: 组合结果 - 连接自检观测
	);                                          // 实例结束: 被测模块 - 完成全部端口连接

	initial begin                                // 激励流程: 三组向量 - 验证每级组合运算
		#1;                                       // 延时控制: 等待零向量组合结果稳定
		if(o_y !== 1'b1)begin
			$fatal(1, "FAIL: zero vector");        // 零向量失败: 取反结果不为一
		end
		i_a = 1'b1;                               // 激励赋值: 拉高第一输入 - 激活首级与运算
		i_b = 1'b1;                               // 激励赋值: 拉高第二输入 - 期望子锥为一
		#1;                                       // 延时控制: 等待子锥结果稳定
		if(o_y !== 1'b0)begin
			$fatal(1, "FAIL: child-equivalent cone"); // 子锥失败: 取反结果不为零
		end
		i_b = 1'b0;                               // 激励赋值: 拉低第二输入 - 关闭首级与运算
		i_c = 1'b1;                               // 激励赋值: 拉高第三输入 - 保持或运算为一
		#1;                                       // 延时控制: 等待第三组组合结果稳定
		if(o_y !== 1'b0)begin
			$fatal(1, "FAIL: third operation");   // 第三操作失败: 取反结果不为零
		end
		$display(" > INFO: [Verilog] hierarchy budget fixture passed."); // 结果输出: 报告自检通过
		$finish;                                  // 仿真结束: 所有组合断言已经通过
	end

endmodule                                     // 结束模块: 收束自检测试平台
