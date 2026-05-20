module ready_valid_slice_tb; //声明ready-valid缓存测试平台

reg        i_clk; //测试平台时钟信号
reg        i_rstn; //测试平台复位信号
reg        i_in_valid; //测试平台上游有效标志
wire       o_in_ready; //连接待测模块上游就绪标志
reg  [7:0] i_in_data; //测试平台上游输入数据
wire       o_out_valid; //连接待测模块下游有效标志
reg        i_out_ready; //测试平台下游就绪标志
wire [7:0] o_out_data; //连接待测模块下游输出数据
integer    errors; //累计自检错误数量

ready_valid_slice dut ( //例化ready-valid缓存待测模块
    .i_clk(i_clk), //连接时钟信号
    .i_rstn(i_rstn), //连接复位信号
    .i_in_valid(i_in_valid), //连接上游有效标志
    .o_in_ready(o_in_ready), //连接上游就绪标志
    .i_in_data(i_in_data), //连接上游输入数据
    .o_out_valid(o_out_valid), //连接下游有效标志
    .i_out_ready(i_out_ready), //连接下游就绪标志
    .o_out_data(o_out_data) //连接下游输出数据
); //结束待测模块例化

initial begin //启动时钟产生流程
    i_clk = 1'b0; //初始化时钟为低电平
    forever #5 i_clk = ~i_clk; //每5ns翻转一次时钟
end //结束时钟产生流程

initial begin //启动ready-valid自检流程
    errors = 0; //初始化错误计数
    i_rstn = 1'b0; //拉低复位
    i_in_valid = 1'b0; //初始化上游有效为无效
    i_in_data = 8'h00; //初始化上游输入数据
    i_out_ready = 1'b0; //初始化下游就绪为无效
    repeat (2) @(posedge i_clk); //等待两个时钟周期保持复位
    i_rstn = 1'b1; //释放复位

    @(negedge i_clk); //在下降沿准备第一组写入激励
    i_in_valid = 1'b1; //设置上游有效
    i_in_data = 8'h42; //设置第一组输入数据
    i_out_ready = 1'b0; //保持下游未就绪以填满缓存
    @(posedge i_clk); //等待缓存采样第一组数据
    #1; //等待输出稳定
    if (o_out_valid !== 1'b1 || o_in_ready !== 1'b0 || o_out_data !== 8'h42) begin //检查缓存填满后的输出状态
        $display("FAIL ready_valid_slice case0 valid=%0d ready=%0d data=%02x", o_out_valid, o_in_ready, o_out_data); //打印第一组失败信息
        errors = errors + 1; //累加第一组错误
    end //结束第一组检查

    @(negedge i_clk); //在下降沿准备第二组流动激励
    i_in_valid = 1'b1; //保持上游有效
    i_in_data = 8'h99; //设置第二组输入数据
    i_out_ready = 1'b1; //允许下游接收并同时写入新数据
    @(posedge i_clk); //等待握手流动完成
    #1; //等待输出稳定
    if (o_out_valid !== 1'b1 || o_in_ready !== 1'b1 || o_out_data !== 8'h99) begin //检查流动传输后的输出状态
        $display("FAIL ready_valid_slice case1 valid=%0d ready=%0d data=%02x", o_out_valid, o_in_ready, o_out_data); //打印第二组失败信息
        errors = errors + 1; //累加第二组错误
    end //结束第二组检查

    @(negedge i_clk); //在下降沿准备释放缓存激励
    i_in_valid = 1'b0; //取消上游有效
    i_out_ready = 1'b1; //保持下游就绪以清空缓存
    @(posedge i_clk); //等待缓存清空
    #1; //等待输出稳定
    if (o_out_valid !== 1'b0 || o_in_ready !== 1'b1 || o_out_data !== 8'h99) begin //检查缓存清空后的输出状态
        $display("FAIL ready_valid_slice case2 valid=%0d ready=%0d data=%02x", o_out_valid, o_in_ready, o_out_data); //打印第三组失败信息
        errors = errors + 1; //累加第三组错误
    end //结束第三组检查

    if (errors == 0) begin //所有检查通过时报告PASS
        $display("PASS ready_valid_slice"); //打印通过信息
    end else begin //存在错误时报告FAIL
        $display("FAIL ready_valid_slice errors=%0d", errors); //打印失败汇总
    end //结束结果汇总
    $finish; //结束仿真
end //结束测试流程

endmodule //结束ready-valid缓存测试平台
