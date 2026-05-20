module pipeline_delay ( //声明一级流水延迟模块
    input        i_clk, //输入时钟信号
    input        i_rstn, //低有效异步复位信号
    input        i_valid, //输入数据有效标志
    input  [7:0] i_data, //输入数据总线
    output       o_valid, //输出数据有效标志
    output [7:0] o_data //输出数据总线
); //结束模块端口声明

reg       reg_valid; //保存一级流水有效标志
reg [7:0] reg_data; //保存一级流水数据

// 同步寄存 valid 和 data，形成一级流水。
always @(posedge i_clk or negedge i_rstn) begin //在时钟上升沿或复位下降沿更新流水寄存器
    if (!i_rstn) begin //复位有效时清空寄存器
        reg_valid <= 1'b0; //复位有效标志寄存器
        reg_data <= 8'h00; //复位数据寄存器
    end else begin //复位释放后采样输入
        reg_valid <= i_valid; //锁存输入有效标志
        reg_data <= i_data; //锁存输入数据
    end //结束复位分支
end //结束流水寄存器时序逻辑

assign o_valid = reg_valid; //连接输出有效标志
assign o_data = reg_data; //连接输出数据

endmodule //结束一级流水延迟模块
