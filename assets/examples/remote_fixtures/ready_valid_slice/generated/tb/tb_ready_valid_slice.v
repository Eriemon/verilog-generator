module tb_ready_valid_slice;	//模块: mock生成模块 - 承载Verilog工作流样例
	reg i_clk = 1'b0;	//寄存器: mock寄存器 - 保存样例状态或数据
	reg i_rstn = 1'b0;	//寄存器: mock寄存器 - 保存样例状态或数据
	reg i_in_valid = 1'b0;	//寄存器: mock寄存器 - 保存样例状态或数据
	reg [7:0] i_in_data = 8'd0;	//寄存器: mock寄存器 - 保存样例状态或数据
	reg i_out_ready = 1'b0;	//寄存器: mock寄存器 - 保存样例状态或数据
	wire o_in_ready;	//连线: mock连线 - 连接样例组合视图
	wire o_out_valid;	//连线: mock连线 - 连接样例组合视图
	wire [7:0] o_out_data;	//连线: mock连线 - 连接样例组合视图
	reg [7:0] EXPECTED_VALUE = 8'd1;	//寄存器: mock寄存器 - 保存样例状态或数据
	wire [7:0] value = o_out_data;	//连线: mock连线 - 连接样例组合视图
	always #5 i_clk = ~i_clk;	//时序逻辑: mock流水 - 更新样例寄存器
	ready_valid_slice dut (	//语义说明: mock代码 - 保持样例可审查
		.i_clk(i_clk),	//端口映射: mock实例 - 连接测试平台信号
		.i_rstn(i_rstn),	//端口映射: mock实例 - 连接测试平台信号
		.i_in_valid(i_in_valid),	//端口映射: mock实例 - 连接测试平台信号
		.o_in_ready(o_in_ready),	//端口映射: mock实例 - 连接测试平台信号
		.i_in_data(i_in_data),	//端口映射: mock实例 - 连接测试平台信号
		.o_out_valid(o_out_valid),	//端口映射: mock实例 - 连接测试平台信号
		.i_out_ready(i_out_ready),	//端口映射: mock实例 - 连接测试平台信号
		.o_out_data(o_out_data)	//端口映射: mock实例 - 连接测试平台信号
	);	//语义说明: mock代码 - 保持样例可审查
	initial begin	//语义说明: mock代码 - 保持样例可审查
		// case_1 compares checkpoint value against EXPECTED_VALUE and reports PASS/FAIL
		i_rstn = 1'b0;	//语义说明: mock代码 - 保持样例可审查
		#12;	//延时控制: mock测试 - 等待信号稳定
		i_rstn = 1'b1;	//语义说明: mock代码 - 保持样例可审查
		i_in_data = 8'd1;	//语义说明: mock代码 - 保持样例可审查
		i_in_valid = 1'b1;	//语义说明: mock代码 - 保持样例可审查
		#20;	//延时控制: mock测试 - 等待信号稳定
		if (o_out_valid !== 1'b1) begin	//条件分支: mock条件 - 选择复位或运行路径
			$fatal(1, "FAIL: valid output did not assert");	//语义说明: mock代码 - 保持样例可审查
		end	//结束代码块: mock流程
		if (value !== EXPECTED_VALUE) begin	//条件分支: mock条件 - 选择复位或运行路径
			$fatal(1, "FAIL: value checkpoint mismatch");	//语义说明: mock代码 - 保持样例可审查
		end	//结束代码块: mock流程
		$display(" > INFO: [Verilog] self-checking mock testbench completed.");	//结果输出: mock测试 - 打印PASS或FAIL
		$finish;	//语义说明: mock代码 - 保持样例可审查
	end	//结束代码块: mock流程
endmodule	//结束模块: mock生成模块
