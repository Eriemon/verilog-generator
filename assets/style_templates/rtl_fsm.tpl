//----------------状态机区域----------------//
//状态机--次态逻辑
always@(*)begin
	state_next <= state_current;           // 缺省保持当前状态
	case(state_current)
		//空闲状态转移分支
		ST_IDLE:begin
			state_next <= ST_IDLE;         // 默认停留在空闲状态
		end
		//默认状态转移分支
		default:begin
			state_next <= ST_IDLE;         // 非法状态统一回收到空闲态
		end
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
