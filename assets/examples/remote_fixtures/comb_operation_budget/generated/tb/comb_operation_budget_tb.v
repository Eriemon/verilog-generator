`timescale 1ns / 1ps

module comb_operation_budget_tb;              // 模块: 自检测试平台 - 验证流水握手与停顿保持
	reg i_clk = 1'b0;                          // 寄存器: 测试时钟 - 驱动同步逻辑
	reg i_rstn = 1'b0;                         // 寄存器: 低有效复位 - 初始化被测模块
	reg i_valid = 1'b0;                        // 寄存器: 输入有效 - 标记测试事务
	reg [3:0]i_data = 4'b0000;                 // 寄存器: 输入数据 - 提供条件组合
	wire o_input_ready;                        // 连线: 输入许可 - 观测接收能力
	reg i_output_ready = 1'b1;                 // 寄存器: 下游许可 - 注入反压停顿
	wire o_valid;                              // 连线: 输出有效 - 观测结果事务
	wire o_match;                              // 连线: 匹配结果 - 观测流水计算
	integer cycle_count = 0;                   // 计数器: 复位后周期 - 核对固定延迟
	integer timeout_count = 0;                 // 计数器: 超时保护 - 防止仿真挂起
	integer accepted_count = 0;                // 计数器: 已接收事务 - 核对事务守恒
	integer delivered_count = 0;               // 计数器: 已交付事务 - 核对事务守恒
	reg stalled_match = 1'b0;                  // 寄存器: 停顿快照 - 核对输出稳定

	always #5 i_clk = ~i_clk;                  // 时序逻辑: 测试时钟 - 生成十纳秒周期

	comb_operation_budget dut (                // 实例: 被测模块 - 连接握手与数据接口
		.i_clk(i_clk),                           // 端口映射: 工作时钟 - 连接测试时钟
		.i_rstn(i_rstn),                         // 端口映射: 低有效复位 - 连接测试复位
		.i_valid(i_valid),                       // 端口映射: 输入有效 - 连接事务标志
		.i_data(i_data),                         // 端口映射: 输入数据 - 连接条件向量
		.o_input_ready(o_input_ready),           // 端口映射: 输入许可 - 连接握手观测
		.i_output_ready(i_output_ready),         // 端口映射: 下游许可 - 连接反压控制
		.o_valid(o_valid),                       // 端口映射: 输出有效 - 连接结果观测
		.o_match(o_match)                        // 端口映射: 匹配结果 - 连接数据观测
	);                                         // 实例结束: 被测模块 - 完成全部端口连接

	//复位后统计握手事务和超时周期
	always@(posedge i_clk)begin
		if(i_rstn == 1'b1)begin
			cycle_count <= cycle_count + 1;         // 周期计数: 核对首个结果延迟
			timeout_count <= timeout_count + 1;     // 超时计数: 限制最大仿真等待
			if(timeout_count > 12)begin
				$fatal(1, "FAIL: timed out waiting for registered result"); // 超时失败: 结果未按期到达
			end
			if((i_valid == 1'b1) && (o_input_ready == 1'b1))begin
				accepted_count <= accepted_count + 1; // 接收计数: 记录输入握手事务
			end
			if((o_valid == 1'b1) && (i_output_ready == 1'b1))begin
				delivered_count <= delivered_count + 1; // 交付计数: 记录输出握手事务
			end
		end
	end

	initial begin                               // 激励流程: 单事务反压 - 验证延迟和保持
		#12;                                     // 延时控制: 保持复位 - 跨越首个上升沿
		@(negedge i_clk);                        // 边沿等待: 下降沿 - 安全切换输入激励
		i_rstn = 1'b1;                           // 激励赋值: 释放复位 - 启动流水逻辑
		i_data = 4'b0011;                        // 激励赋值: 条件数据 - 期望匹配为一
		i_valid = 1'b1;                          // 激励赋值: 输入有效 - 提交一个事务
		@(negedge i_clk);                        // 边沿等待: 下降沿 - 完成单拍输入事务
		i_valid = 1'b0;                          // 激励赋值: 输入无效 - 防止重复接收
		i_output_ready = 1'b0;                   // 激励赋值: 拉低下游许可 - 注入反压
		@(posedge i_clk);                        // 边沿等待: 上升沿 - 等待第二级结果形成
		#1;                                      // 延时控制: 等待非阻塞赋值稳定
		if((o_valid !== 1'b1) || (o_match !== 1'b1))begin
			$fatal(1, "FAIL: expected registered match after two rising edges"); // 结果失败: 固定延迟或计算错误
		end
		if(cycle_count !== 2)begin
			$fatal(1, "FAIL: first result latency changed"); // 延迟失败: 首结果不是两拍到达
		end
		stalled_match = o_match;                 // 快照赋值: 保存停顿前匹配结果
		@(posedge i_clk);                        // 边沿等待: 上升沿 - 保持一拍反压
		#1;                                      // 延时控制: 等待停顿状态稳定
		if((o_valid !== 1'b1) || (o_match !== stalled_match))begin
			$fatal(1, "FAIL: output changed while backpressured"); // 保持失败: 反压期间输出发生变化
		end
		i_output_ready = 1'b1;                   // 激励赋值: 恢复下游许可 - 交付缓存结果
		@(posedge i_clk);                        // 边沿等待: 上升沿 - 完成输出握手
		#1;                                      // 延时控制: 等待交付后状态稳定
		if(o_valid !== 1'b0)begin
			$fatal(1, "FAIL: o_valid did not deassert after one result cycle"); // 有效失败: 交付后未清除输出标志
		end
		if((accepted_count !== 1) || (delivered_count !== 1))begin
			$fatal(1, "FAIL: ready/valid conservation mismatch"); // 守恒失败: 接收与交付数量不一致
		end
		$display(" > INFO: [Verilog] comb operation budget fixture passed."); // 结果输出: 报告自检通过
		$finish;                                 // 仿真结束: 所有断言已经通过
	end

endmodule                                    // 结束模块: 收束自检测试平台
