module top_counter (
    input wire i_clk,
    input wire i_rstn,
    input wire i_en,
    output wire [7:0] o_count
);
    leaf_counter u_leaf (
        .i_clk(i_clk),
        .i_rstn(i_rstn),
        .i_en(i_en),
        .o_count(o_count)
    );
endmodule
