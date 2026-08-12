module fsm_without_default(
    input wire i_clk,
    input wire i_rstn,
    input wire i_go,
    output reg o_done
);
    reg [1:0] state;
    localparam IDLE = 2'd0;
    localparam WORK = 2'd1;

    always @(posedge i_clk or negedge i_rstn) begin
        if (!i_rstn) begin
            state <= IDLE;
            o_done <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    o_done <= 1'b0;
                    if (i_go) begin
                        state <= WORK;
                    end
                end
                WORK: begin
                    o_done <= 1'b1;
                    state <= IDLE;
                end
            endcase
        end
    end
endmodule
