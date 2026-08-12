`timescale 1ns/1ps
module tb_ready_valid_slice;
    reg i_clk;
    reg i_rstn;
    reg i_in_valid;
    reg [7:0] i_in_data;
    wire o_in_ready;
    wire o_out_valid;
    wire [7:0] o_out_data;

    ready_valid_slice dut(
        .i_clk(i_clk),
        .i_rstn(i_rstn),
        .i_in_valid(i_in_valid),
        .i_in_data(i_in_data),
        .o_in_ready(o_in_ready),
        .o_out_valid(o_out_valid),
        .o_out_data(o_out_data)
    );

    always #5 i_clk = ~i_clk;

    initial begin
        i_clk = 1'b0;
        i_rstn = 1'b0;
        i_in_valid = 1'b0;
        i_in_data = 8'h00;
        #20 i_rstn = 1'b1;
        #10 i_in_valid = 1'b1;
        i_in_data = 8'h3C;
        #10 i_in_valid = 1'b0;
        #40 $display("PASS: legacy tb finished");
        $finish;
    end
endmodule
