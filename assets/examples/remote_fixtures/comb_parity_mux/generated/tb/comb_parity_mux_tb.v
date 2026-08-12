module comb_parity_mux_tb;	//模块声明，定义当前 mock 设计单元。
	reg i_sel = 1'b0;	//寄存器声明，保存时序路径中的中间状态。
	reg [7:0] i_a = 8'd0;	//寄存器声明，保存时序路径中的中间状态。
	reg [7:0] i_b = 8'd0;	//寄存器声明，保存时序路径中的中间状态。
	wire [7:0] o_y;	//导线声明，连接组合结果与观测节点。
	wire o_parity;	//导线声明，连接组合结果与观测节点。
	comb_parity_mux dut (	//普通语句，保持 mock 示例的结构可审查。
		.i_sel(i_sel),	//端口映射语句，把 TB 信号接到 DUT 端口。
		.i_a(i_a),	//端口映射语句，把 TB 信号接到 DUT 端口。
		.i_b(i_b),	//端口映射语句，把 TB 信号接到 DUT 端口。
		.o_y(o_y),	//端口映射语句，把 TB 信号接到 DUT 端口。
		.o_parity(o_parity)	//端口映射语句，把 TB 信号接到 DUT 端口。
	);	//普通语句，保持 mock 示例的结构可审查。
	initial begin	//普通语句，保持 mock 示例的结构可审查。
		i_a = 8'h3C;	//普通语句，保持 mock 示例的结构可审查。
		i_b = 8'hA5;	//普通语句，保持 mock 示例的结构可审查。
		i_sel = 1'b0;	//普通语句，保持 mock 示例的结构可审查。
		#1;	//延时控制语句，给信号传播预留稳定时间。
		if (o_y !== i_a) begin	//条件判断语句，区分复位或运行路径。
			$fatal(1, "FAIL: combinational primary output mismatch when selector is 0");	//普通语句，保持 mock 示例的结构可审查。
		end	//结束当前 begin-end 代码块。
		if (o_parity !== ^o_y) begin	//条件判断语句，区分复位或运行路径。
			$fatal(1, "FAIL: parity output mismatch when selector is 0");	//普通语句，保持 mock 示例的结构可审查。
		end	//结束当前 begin-end 代码块。
		i_sel = 1'b1;	//普通语句，保持 mock 示例的结构可审查。
		#1;	//延时控制语句，给信号传播预留稳定时间。
		if (o_y !== i_b) begin	//条件判断语句，区分复位或运行路径。
			$fatal(1, "FAIL: combinational primary output mismatch when selector is 1");	//普通语句，保持 mock 示例的结构可审查。
		end	//结束当前 begin-end 代码块。
		if (o_parity !== ^o_y) begin	//条件判断语句，区分复位或运行路径。
			$fatal(1, "FAIL: parity output mismatch when selector is 1");	//普通语句，保持 mock 示例的结构可审查。
		end	//结束当前 begin-end 代码块。
		$display("PASS: self-checking mock testbench completed");	//成功摘要输出，向仿真日志报告结果。
		$finish;	//普通语句，保持 mock 示例的结构可审查。
	end	//结束当前 begin-end 代码块。
endmodule	//结束当前模块，收束当前 mock 设计单元。
