module comb_parity_mux_tb; //声明组合奇偶校验测试平台

reg        i_sel; //测试平台驱动选择信号
reg  [7:0] i_a; //测试平台驱动输入A
reg  [7:0] i_b; //测试平台驱动输入B
wire [7:0] o_y; //连接待测模块数据输出
wire       o_parity; //连接待测模块奇偶校验输出
integer    errors; //累计自检错误数量

comb_parity_mux dut ( //例化待测组合奇偶校验模块
    .i_sel(i_sel), //连接选择信号
    .i_a(i_a), //连接输入A
    .i_b(i_b), //连接输入B
    .o_y(o_y), //连接数据输出
    .o_parity(o_parity) //连接奇偶校验输出
); //结束待测模块例化

initial begin //启动组合逻辑自检流程
    errors = 0; //初始化错误计数
    i_sel = 1'b0; //选择输入A作为第一组测试
    i_a = 8'h55; //设置第一组输入A数据
    i_b = 8'haa; //设置第一组输入B数据
    #1; //等待组合逻辑稳定
    if (o_y !== 8'h55 || o_parity !== ^8'h55) begin //检查第一组输出是否匹配期望
        $display("FAIL comb_parity_mux case0 y=%02x parity=%0d", o_y, o_parity); //打印第一组失败信息
        errors = errors + 1; //累加第一组错误
    end //结束第一组检查
    i_sel = 1'b1; //选择输入B作为第二组测试
    i_a = 8'h55; //保持第二组输入A数据
    i_b = 8'haa; //设置第二组输入B数据
    #1; //等待组合逻辑稳定
    if (o_y !== 8'haa || o_parity !== ^8'haa) begin //检查第二组输出是否匹配期望
        $display("FAIL comb_parity_mux case1 y=%02x parity=%0d", o_y, o_parity); //打印第二组失败信息
        errors = errors + 1; //累加第二组错误
    end //结束第二组检查
    i_sel = 1'b0; //重新选择输入A作为第三组测试
    i_a = 8'hf0; //设置第三组输入A数据
    i_b = 8'h0f; //设置第三组输入B数据
    #1; //等待组合逻辑稳定
    if (o_y !== 8'hf0 || o_parity !== ^8'hf0) begin //检查第三组输出是否匹配期望
        $display("FAIL comb_parity_mux case2 y=%02x parity=%0d", o_y, o_parity); //打印第三组失败信息
        errors = errors + 1; //累加第三组错误
    end //结束第三组检查
    if (errors == 0) begin //所有检查通过时报告PASS
        $display("PASS comb_parity_mux"); //打印通过信息
    end else begin //存在错误时报告FAIL
        $display("FAIL comb_parity_mux errors=%0d", errors); //打印失败汇总
    end //结束结果汇总
    $finish; //结束仿真
end //结束测试流程

endmodule //结束组合奇偶校验测试平台
