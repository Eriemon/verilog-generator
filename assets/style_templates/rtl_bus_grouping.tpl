//----------------AXI/AXIS/APB/AHB 端口分组模板----------------//
//按通道和角色分组,不要把标准总线端口写成无注释平铺列表

//写通道--写地址
input [C_AXI_ADDR_WIDTH - 1:0]i_axi_awaddr,		//写地址
input i_axi_awvalid,								//写地址有效
output o_axi_awready,								//写地址准备好

//写通道--写数据
input [C_AXI_DATA_WIDTH - 1:0]i_axi_wdata,		//写数据
input i_axi_wvalid,								//写数据有效
output o_axi_wready,								//写数据准备好

//写通道--写响应
output [1:0]o_axi_bresp,							//写响应
output o_axi_bvalid,								//写响应有效
input i_axi_bready,								//写响应准备好

//读通道--读地址
input [C_AXI_ADDR_WIDTH - 1:0]i_axi_araddr,		//读地址
input i_axi_arvalid,								//读地址有效
output o_axi_arready,								//读地址准备好

//读通道--读数据
output [C_AXI_DATA_WIDTH - 1:0]o_axi_rdata,		//读数据
output o_axi_rvalid,								//读数据有效
input i_axi_rready									//读数据准备好
