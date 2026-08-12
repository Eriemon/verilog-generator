module hierarchy_loop_child
(
    input wire [3:0] data,
    output reg parity
);
integer i;
always @(*) begin
    parity = 1'b0;
    for (i = 0; i < 4; i = i + 1) begin
        parity = parity ^ data[i];
    end
end
endmodule

module hierarchy_child_loop
(
    input wire [3:0] data,
    output wire parity
);
wire child_parity;
hierarchy_loop_child u_child (
    .data(data),
    .parity(child_parity)
);
assign parity = child_parity;
endmodule
