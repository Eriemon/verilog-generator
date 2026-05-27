module missing_output_register(
    input wire i_clk,
    input wire i_rstn,
    input wire i_valid,
    input wire [7:0] i_data,
    output reg o_valid,
    output reg [7:0] o_data
);
    always @(posedge i_clk or negedge i_rstn) begin
        if (!i_rstn) begin
            o_valid <= 1'b0;
            o_data <= 8'd0;
        end else begin
            o_valid <= i_valid;
        end
    end
endmodule
