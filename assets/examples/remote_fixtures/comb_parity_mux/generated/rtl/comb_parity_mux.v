module comb_parity_mux ( //声明组合奇偶校验多路选择模块
    input        i_sel, //选择输入数据通道
    input  [7:0] i_a, //输入数据通道A
    input  [7:0] i_b, //输入数据通道B
    output [7:0] o_y, //输出被选择的数据
    output       o_parity //输出所选数据的奇校验结果
); //结束模块端口声明

// 选择一路输入数据，并计算所选数据的奇校验。
assign o_y = i_sel ? i_b : i_a; //根据选择信号转发对应输入数据
assign o_parity = ^o_y; //对输出数据按位异或生成奇偶校验

endmodule //结束组合奇偶校验多路选择模块
