//----------------状态机区域----------------//
//状态机--次态逻辑
always@(*)begin
	case(state_current)
		ST_IDLE:begin
			state_next <= ST_IDLE;
		end
		default:state_next <= ST_IDLE;
	endcase
end

//状态机--状态转移
always@(posedge i_clk or negedge i_rstn)begin
	if(i_rstn == 1'b0)state_current <= ST_IDLE;
	else state_current <= state_next;
end

//-------------状态任务处理区域-------------//
//状态机--输出逻辑或状态任务
always@(posedge i_clk or negedge i_rstn)begin
	if(i_rstn == 1'b0)begin
		// reset
	end else begin
		// state task
	end
end
