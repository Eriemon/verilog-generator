module comb_parity_mux_tb;

reg        i_sel;
reg  [7:0] i_a;
reg  [7:0] i_b;
wire [7:0] o_y;
wire       o_parity;
integer    errors;

comb_parity_mux dut (
    .i_sel(i_sel),
    .i_a(i_a),
    .i_b(i_b),
    .o_y(o_y),
    .o_parity(o_parity)
);

initial begin
    errors = 0;
    i_sel = 1'b0;
    i_a = 8'h55;
    i_b = 8'haa;
    #1;
    if (o_y !== 8'h55 || o_parity !== ^8'h55) begin
        $display("FAIL comb_parity_mux case0 y=%02x parity=%0d", o_y, o_parity);
        errors = errors + 1;
    end
    i_sel = 1'b1;
    i_a = 8'h55;
    i_b = 8'haa;
    #1;
    if (o_y !== 8'haa || o_parity !== ^8'haa) begin
        $display("FAIL comb_parity_mux case1 y=%02x parity=%0d", o_y, o_parity);
        errors = errors + 1;
    end
    i_sel = 1'b0;
    i_a = 8'hf0;
    i_b = 8'h0f;
    #1;
    if (o_y !== 8'hf0 || o_parity !== ^8'hf0) begin
        $display("FAIL comb_parity_mux case2 y=%02x parity=%0d", o_y, o_parity);
        errors = errors + 1;
    end
    if (errors == 0) begin
        $display("PASS comb_parity_mux");
    end else begin
        $display("FAIL comb_parity_mux errors=%0d", errors);
    end
    $finish;
end

endmodule
