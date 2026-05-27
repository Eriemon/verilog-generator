module ready_valid_slice (
    input wire i_clk,
    input wire i_rstn,
    input wire i_in_valid,
    input wire [7:0] i_in_data,
    output wire o_in_ready,
    output reg o_out_valid,
    output reg [7:0] o_out_data
);
    reg hold_valid;
    reg [7:0] hold_data;

    assign o_in_ready = !hold_valid;

    always @(posedge i_clk or negedge i_rstn) begin
        if (!i_rstn) begin
            o_in_ready <= 1'b0;
            hold_valid <= 1'b0;
            hold_data <= 8'd0;
            o_out_valid <= 1'b0;
            o_out_data <= 8'd0;
        end else begin
            o_in_ready <= 1'b0;
            if (i_in_valid && o_in_ready) begin
                hold_valid <= 1'b1;
                hold_data <= i_in_data;
            end
            o_out_valid <= hold_valid;
            o_out_data <= hold_data;
            if (hold_valid) begin
                hold_valid <= 1'b0;
            end
        end
    end
endmodule
