module pipeline_delay_tb; //声明一级流水延迟测试平台

reg        i_clk; //测试平台时钟信号
reg        i_rstn; //测试平台复位信号
reg        i_valid; //测试平台输入有效标志
reg  [7:0] i_data; //测试平台输入数据
wire       o_valid; //连接待测模块输出有效标志
wire [7:0] o_data; //连接待测模块输出数据
integer    errors; //累计自检错误数量

pipeline_delay dut ( //例化一级流水延迟待测模块
    .i_clk(i_clk), //连接时钟信号
    .i_rstn(i_rstn), //连接复位信号
    .i_valid(i_valid), //连接输入有效标志
    .i_data(i_data), //连接输入数据
    .o_valid(o_valid), //连接输出有效标志
    .o_data(o_data) //连接输出数据
); //结束待测模块例化

initial begin //启动时钟产生流程
    i_clk = 1'b0; //初始化时钟为低电平
    forever #5 i_clk = ~i_clk; //每5ns翻转一次时钟
end //结束时钟产生流程

initial begin //启动流水延迟自检流程
    errors = 0; //初始化错误计数
    i_rstn = 1'b0; //拉低复位
    i_valid = 1'b0; //初始化输入有效为无效
    i_data = 8'h00; //初始化输入数据
    repeat (2) @(posedge i_clk); //等待两个时钟周期保持复位
    i_rstn = 1'b1; //释放复位
    @(negedge i_clk); //在时钟下降沿准备第一组激励
    i_valid = 1'b1; //设置第一组输入有效
    i_data = 8'h3c; //设置第一组输入数据
    @(posedge i_clk); //等待流水寄存器采样
    #1; //等待寄存器输出稳定
    if (o_valid !== 1'b1 || o_data !== 8'h3c) begin //检查第一组流水输出
        $display("FAIL pipeline_delay case0 valid=%0d data=%02x", o_valid, o_data); //打印第一组失败信息
        errors = errors + 1; //累加第一组错误
    end //结束第一组检查
    @(negedge i_clk); //在时钟下降沿准备第二组激励
    i_valid = 1'b0; //设置第二组输入有效为无效
    i_data = 8'ha5; //设置第二组输入数据
    @(posedge i_clk); //等待流水寄存器采样
    #1; //等待寄存器输出稳定
    if (o_valid !== 1'b0 || o_data !== 8'ha5) begin //检查第二组流水输出
        $display("FAIL pipeline_delay case1 valid=%0d data=%02x", o_valid, o_data); //打印第二组失败信息
        errors = errors + 1; //累加第二组错误
    end //结束第二组检查
    if (errors == 0) begin //所有检查通过时报告PASS
        $display("PASS pipeline_delay"); //打印通过信息
    end else begin //存在错误时报告FAIL
        $display("FAIL pipeline_delay errors=%0d", errors); //打印失败汇总
    end //结束结果汇总
    $finish; //结束仿真
end //结束测试流程

endmodule //结束一级流水延迟测试平台
