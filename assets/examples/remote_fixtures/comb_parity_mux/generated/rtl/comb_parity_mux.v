module comb_parity_mux (
    input        i_sel,
    input  [7:0] i_a,
    input  [7:0] i_b,
    output [7:0] o_y,
    output       o_parity
);

// 选择一路输入数据，并计算所选数据的奇校验。
assign o_y = i_sel ? i_b : i_a;
assign o_parity = ^o_y;

endmodule
