module ready_valid_slice ( //声明ready-valid一级缓存模块
    input        i_clk, //输入时钟信号
    input        i_rstn, //低有效异步复位信号
    input        i_in_valid, //上游输入有效标志
    output       o_in_ready, //反馈给上游的就绪标志
    input  [7:0] i_in_data, //上游输入数据
    output       o_out_valid, //输出给下游的有效标志
    input        i_out_ready, //下游反馈的就绪标志
    output [7:0] o_out_data //输出给下游的数据
); //结束模块端口声明

reg       reg_full; //标记内部缓存是否持有有效数据
reg [7:0] reg_data; //保存内部缓存的数据

assign o_in_ready = !reg_full || i_out_ready; //缓存空或下游就绪时允许上游写入
assign o_out_valid = reg_full; //缓存满时向下游声明输出有效
assign o_out_data = reg_data; //将缓存数据连接到输出端口

// 保存一个数据项，并在下游接收后清空。
always @(posedge i_clk or negedge i_rstn) begin //在时钟上升沿或复位下降沿更新缓存状态
    if (!i_rstn) begin //复位有效时清空缓存
        reg_full <= 1'b0; //复位缓存有效标志
        reg_data <= 8'h00; //复位缓存数据
    end else if (o_in_ready && i_in_valid) begin //上游握手成功时写入新数据
        reg_full <= 1'b1; //标记缓存持有有效数据
        reg_data <= i_in_data; //锁存上游输入数据
    end else if (i_out_ready) begin //下游单独接收时释放缓存
        reg_full <= 1'b0; //清除缓存有效标志
        reg_data <= reg_data; //保持缓存数据便于调试观察
    end //结束缓存状态分支
end //结束ready-valid缓存时序逻辑

endmodule //结束ready-valid一级缓存模块
