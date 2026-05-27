module reset_gap_counter (
    input wire i_clk,
    input wire i_rstn,
    input wire i_en,
    output reg [7:0] o_count
);
    always @(posedge i_clk or negedge i_rstn) begin
        if (!i_rstn) begin
        end else if (i_en) begin
            o_count <= o_count + 8'd1;
        end
    end
endmodule
