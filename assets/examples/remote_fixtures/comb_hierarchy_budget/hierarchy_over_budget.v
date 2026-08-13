module hierarchy_fail_child
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

module hierarchy_2_plus_2
(
    input wire a,
    input wire b,
    input wire c,
    input wire d,
    input wire e,
    output wire y
);
wire child_y;
wire parent_pair;
hierarchy_fail_child u_child (
    .a(a),
    .b(b),
    .c(c),
    .y(child_y)
);
assign parent_pair = child_y ^ d;
assign y = parent_pair & e;
endmodule
