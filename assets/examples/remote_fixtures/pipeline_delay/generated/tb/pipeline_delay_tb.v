module pipeline_delay_tb;

reg        i_clk;
reg        i_rstn;
reg        i_valid;
reg  [7:0] i_data;
wire       o_valid;
wire [7:0] o_data;
integer    errors;

pipeline_delay dut (
    .i_clk(i_clk),
    .i_rstn(i_rstn),
    .i_valid(i_valid),
    .i_data(i_data),
    .o_valid(o_valid),
    .o_data(o_data)
);

initial begin
    i_clk = 1'b0;
    forever #5 i_clk = ~i_clk;
end

initial begin
    errors = 0;
    i_rstn = 1'b0;
    i_valid = 1'b0;
    i_data = 8'h00;
    repeat (2) @(posedge i_clk);
    i_rstn = 1'b1;
    @(negedge i_clk);
    i_valid = 1'b1;
    i_data = 8'h3c;
    @(posedge i_clk);
    #1;
    if (o_valid !== 1'b1 || o_data !== 8'h3c) begin
        $display("FAIL pipeline_delay case0 valid=%0d data=%02x", o_valid, o_data);
        errors = errors + 1;
    end
    @(negedge i_clk);
    i_valid = 1'b0;
    i_data = 8'ha5;
    @(posedge i_clk);
    #1;
    if (o_valid !== 1'b0 || o_data !== 8'ha5) begin
        $display("FAIL pipeline_delay case1 valid=%0d data=%02x", o_valid, o_data);
        errors = errors + 1;
    end
    if (errors == 0) begin
        $display("PASS pipeline_delay");
    end else begin
        $display("FAIL pipeline_delay errors=%0d", errors);
    end
    $finish;
end

endmodule
