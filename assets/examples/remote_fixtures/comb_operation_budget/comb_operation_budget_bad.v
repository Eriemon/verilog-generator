module comb_operation_budget_bad
(
	input wire [3:0] i_data,
	output wire o_match
);

	assign o_match = (i_data[0] & i_data[1]) | (i_data[2] & i_data[3]) | i_data[0];

endmodule
