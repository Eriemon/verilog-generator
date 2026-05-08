module ready_valid_slice_tb;

reg        i_clk;
reg        i_rstn;
reg        i_in_valid;
wire       o_in_ready;
reg  [7:0] i_in_data;
wire       o_out_valid;
reg        i_out_ready;
wire [7:0] o_out_data;
integer    errors;

ready_valid_slice dut (
    .i_clk(i_clk),
    .i_rstn(i_rstn),
    .i_in_valid(i_in_valid),
    .o_in_ready(o_in_ready),
    .i_in_data(i_in_data),
    .o_out_valid(o_out_valid),
    .i_out_ready(i_out_ready),
    .o_out_data(o_out_data)
);

initial begin
    i_clk = 1'b0;
    forever #5 i_clk = ~i_clk;
end

initial begin
    errors = 0;
    i_rstn = 1'b0;
    i_in_valid = 1'b0;
    i_in_data = 8'h00;
    i_out_ready = 1'b0;
    repeat (2) @(posedge i_clk);
    i_rstn = 1'b1;

    @(negedge i_clk);
    i_in_valid = 1'b1;
    i_in_data = 8'h42;
    i_out_ready = 1'b0;
    @(posedge i_clk);
    #1;
    if (o_out_valid !== 1'b1 || o_in_ready !== 1'b0 || o_out_data !== 8'h42) begin
        $display("FAIL ready_valid_slice case0 valid=%0d ready=%0d data=%02x", o_out_valid, o_in_ready, o_out_data);
        errors = errors + 1;
    end

    @(negedge i_clk);
    i_in_valid = 1'b1;
    i_in_data = 8'h99;
    i_out_ready = 1'b1;
    @(posedge i_clk);
    #1;
    if (o_out_valid !== 1'b1 || o_in_ready !== 1'b1 || o_out_data !== 8'h99) begin
        $display("FAIL ready_valid_slice case1 valid=%0d ready=%0d data=%02x", o_out_valid, o_in_ready, o_out_data);
        errors = errors + 1;
    end

    @(negedge i_clk);
    i_in_valid = 1'b0;
    i_out_ready = 1'b1;
    @(posedge i_clk);
    #1;
    if (o_out_valid !== 1'b0 || o_in_ready !== 1'b1 || o_out_data !== 8'h99) begin
        $display("FAIL ready_valid_slice case2 valid=%0d ready=%0d data=%02x", o_out_valid, o_in_ready, o_out_data);
        errors = errors + 1;
    end

    if (errors == 0) begin
        $display("PASS ready_valid_slice");
    end else begin
        $display("FAIL ready_valid_slice errors=%0d", errors);
    end
    $finish;
end

endmodule
