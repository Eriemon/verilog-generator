module hierarchy_pass_child
(
    input wire a,
    input wire b,
    input wire c,
    output wire y
);
wire pair;
assign pair = a & b;
assign y = pair | c;
endmodule

module hierarchy_1_plus_2
(
    input wire a,
    input wire b,
    input wire c,
    input wire d,
    output wire y
);
wire child_y;
hierarchy_pass_child u_child (
    .a(a),
    .b(b),
    .c(c),
    .y(child_y)
);
assign y = child_y ^ d;
endmodule
