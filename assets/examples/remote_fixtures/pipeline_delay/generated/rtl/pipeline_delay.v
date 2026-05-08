module pipeline_delay (
    input        i_clk,
    input        i_rstn,
    input        i_valid,
    input  [7:0] i_data,
    output       o_valid,
    output [7:0] o_data
);

reg       reg_valid;
reg [7:0] reg_data;

// 同步寄存 valid 和 data，形成一级流水。
always @(posedge i_clk or negedge i_rstn) begin
    if (!i_rstn) begin
        reg_valid <= 1'b0;
        reg_data <= 8'h00;
    end else begin
        reg_valid <= i_valid;
        reg_data <= i_data;
    end
end

assign o_valid = reg_valid;
assign o_data = reg_data;

endmodule
