module hierarchy_q_child
(
    input wire clk,
    input wire a,
    input wire b,
    input wire c,
    output reg y
);
always @(posedge clk) begin
    y <= (a & b) | c;
end
endmodule

module hierarchy_q_cut
(
    input wire clk,
    input wire a,
    input wire b,
    input wire c,
    input wire d,
    output wire y
);
wire child_q;
hierarchy_q_child u_child (
    .clk(clk),
    .a(a),
    .b(b),
    .c(c),
    .y(child_q)
);
assign y = child_q ^ d;
endmodule
