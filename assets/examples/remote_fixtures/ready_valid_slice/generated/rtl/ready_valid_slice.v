module ready_valid_slice (
    input        i_clk,
    input        i_rstn,
    input        i_in_valid,
    output       o_in_ready,
    input  [7:0] i_in_data,
    output       o_out_valid,
    input        i_out_ready,
    output [7:0] o_out_data
);

reg       reg_full;
reg [7:0] reg_data;

assign o_in_ready = !reg_full || i_out_ready;
assign o_out_valid = reg_full;
assign o_out_data = reg_data;

// 保存一个数据项，并在下游接收后清空。
always @(posedge i_clk or negedge i_rstn) begin
    if (!i_rstn) begin
        reg_full <= 1'b0;
        reg_data <= 8'h00;
    end else if (o_in_ready && i_in_valid) begin
        reg_full <= 1'b1;
        reg_data <= i_in_data;
    end else if (i_out_ready) begin
        reg_full <= 1'b0;
        reg_data <= reg_data;
    end
end

endmodule
