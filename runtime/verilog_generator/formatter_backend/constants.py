"""Verilog formatter 后端复用的协议分组、排序与导出常量。"""

# 使用前向注解保持常量模块可被低成本导入。
from __future__ import annotations

# 区域标题由 banner 模块集中维护，避免格式化后端出现两份展示文案。
from .banners import REGION_LABELS, REGION_TITLES

# 声明行尾注释靠近固定列，减少 RTL 参数和端口区的视觉漂移。
TRAILING_COMMENT_COLUMN = 49  # 行尾注释推荐对齐列

# 端口方向排序遵循 Verilog 声明阅读习惯，输入先于输出和双向端口。
PORT_DIRECTION_ORDER = {"input": 0, "output": 1, "inout": 2}  # 端口方向稳定排序权重

# 已知协议集合限定启发式分组的边界，未知协议仍走通用信号聚类。
KNOWN_PROTOCOL_KINDS = {"axi", "axis", "apb", "wishbone", "uart", "spi", "i2c", "gmii", "rgmii"}  # 可识别协议名集合

# 配置参数前缀映射到领域标签，用于把自动生成的参数块切成可读小节。
KNOWN_CONFIG_PARAM_LABELS = (  # 配置参数前缀到中文标签的匹配表
    ("BASE_ADDR_", "基地址参数"),  # 地址映射参数小节
    ("LUT_THETA_", "lut_theta参数"),  # 查表角度参数小节
    ("QGEN_", "qstate_gen参数"),  # 量子态生成参数小节
    ("MVM_", "matrix_vector_multiplier参数"),  # 矩阵向量乘参数小节
    ("DDR_", "ddr_ram参数"),  # DDR 存储接口参数小节
    ("AA_", "adjust_angle参数"),  # 角度调整参数小节
    ("EV_", "expected_values参数"),  # 期望值计算参数小节
    ("H1_", "H1_gen参数"),  # H1 生成器参数小节
)

# 过于通用的参数词不单独成族，避免 WIDTH/DATA 等名字制造噪声小节。
PARAM_FAMILY_GENERIC_TOKENS = {"DATA", "WIDTH", "ADDR", "NUM", "SIZE", "LEN", "LENGTH", "COUNT"}  # 参数家族通用词过滤集合

# 端口分节标签定义 formatter 允许输出的中文小节名。
PORT_SECTION_LABELS = {  # 端口分组中文标签集合
    "时钟复位",  # 全局时序与复位小节
    "写地址通道",  # 写事务地址阶段
    "写数据通道",  # 写事务数据阶段
    "写响应通道",  # 写事务完成反馈
    "读地址通道",  # 读事务地址阶段
    "读数据通道",  # 读事务返回数据
    "请求通道",  # 请求侧控制分组
    "响应通道",  # 响应侧反馈分组
    "数据通道",  # payload 数据分组
    "控制通道",  # 握手与控制分组
    "写通道",  # 写方向简化分组
    "读通道",  # 读方向简化分组
    "监测通道",  # 链路观测信号分组
    "发送通道",  # 串行发送方向分组
    "接收通道",  # 串行接收方向分组
    "调制解调/状态",  # 调制解调器状态线分组
    "时钟片选",  # SPI 时钟和片选分组
    "总线信号",  # 双线或共享总线分组
    "控制状态",  # 控制器状态标志分组
    "扩展数据",  # 扩展 IO 数据线分组
    "其他信号",  # 未归类端口兜底分组
    "全局信号",  # 跨接口公共信号分组
    "用户接口",  # 用户自定义边界分组
}

# 英文分组别名来自常见接口文档写法，统一折叠到内部中文标签。
PORT_SECTION_ALIASES = {  # 端口分节英文别名归一化表
    "clock reset": "时钟复位",  # 英文时钟复位别名
    "write address": "写地址通道",  # 写地址自然语言短语
    "write address channel": "写地址通道",  # 写地址完整通道短语
    "write addr": "写地址通道",  # addr 缩写写地址短语
    "write addr channel": "写地址通道",  # addr 缩写写地址通道短语
    "write data": "写数据通道",  # 写数据自然语言短语
    "write data channel": "写数据通道",  # 写数据完整通道短语
    "write response": "写响应通道",  # 写响应标准短语
    "write response channel": "写响应通道",  # 写响应完整通道短语
    "write resp": "写响应通道",  # resp 缩写写响应短语
    "write resp channel": "写响应通道",  # resp 缩写写响应通道短语
    "read address": "读地址通道",  # 读地址自然语言短语
    "read address channel": "读地址通道",  # 读地址完整通道短语
    "read addr": "读地址通道",  # addr 缩写读地址短语
    "read addr channel": "读地址通道",  # addr 缩写读地址通道短语
    "read data": "读数据通道",  # 读数据自然语言短语
    "read data channel": "读数据通道",  # 读数据完整通道短语
    "request": "请求通道",  # 请求单词别名
    "request channel": "请求通道",  # 请求通道短语
    "response": "响应通道",  # 响应单词别名
    "response channel": "响应通道",  # 响应通道短语
    "data": "数据通道",  # 数据单词别名
    "data channel": "数据通道",  # 数据通道短语
    "control": "控制通道",  # 控制单词别名
    "control channel": "控制通道",  # 控制通道短语
    "write": "写通道",  # 写方向单词别名
    "write channel": "写通道",  # 写方向通道短语
    "read": "读通道",  # 读方向单词别名
    "read channel": "读通道",  # 读方向通道短语
    "monitor": "监测通道",  # 监测单词别名
    "monitor channel": "监测通道",  # 监测通道短语
    "transmit": "发送通道",  # transmit 发送别名
    "transmit channel": "发送通道",  # transmit 通道短语
    "tx": "发送通道",  # tx 发送缩写
    "tx channel": "发送通道",  # tx 加 channel 的分组标题写法
    "receive": "接收通道",  # receive 接收别名
    "receive channel": "接收通道",  # receive 加 channel 的长标题
    "rx": "接收通道",  # rx 接收缩写
    "rx channel": "接收通道",  # rx 加 channel 的接收标题
    "modem status": "调制解调/状态",  # 调制解调状态短语
    "clock chip select": "时钟片选",  # 时钟片选组合短语
    "chip select": "时钟片选",  # 片选信号短语
    "bus signal": "总线信号",  # 单数总线信号短语
    "bus signals": "总线信号",  # 复数总线信号短语
    "control status": "控制状态",  # 控制状态短语
    "control state": "控制状态",  # 控制状态同义短语
    "extended data": "扩展数据",  # 扩展数据短语
    "other signal": "其他信号",  # 单数兜底信号短语
    "other signals": "其他信号",  # 复数兜底信号短语
    "global signal": "全局信号",  # 单数全局信号短语
    "global signals": "全局信号",  # 复数全局信号短语
    "user interface": "用户接口",  # 用户接口短语
}

# AXI 通道标签保持 AW/W/B/AR/R 的规范顺序名称。
AXI_SECTION_LABELS = {  # AXI 通道到端口小节标签的映射
    "clock_reset": "时钟复位",  # AXI 全局时序小节
    "aw": "写地址通道",  # AXI AW 写地址组
    "w": "写数据通道",  # AXI W 写数据组
    "b": "写响应通道",  # AXI B 写响应组
    "ar": "读地址通道",  # AXI AR 读地址组
    "r": "读数据通道",  # AXI R 读数据组
    "other": "其他信号",  # AXI 未识别信号组
}

# AXI 小节权重让五个独立通道按事务方向稳定排列。
AXI_SECTION_ORDER = {  # AXI 小节展示顺序权重
    "clock_reset": 0,  # AXI 时序小节最先展示
    "aw": 1,  # 写地址先于写数据
    "w": 2,  # 写数据跟随地址阶段
    "b": 3,  # 写响应结束写事务
    "ar": 4,  # 读地址排在写事务后
    "r": 5,  # 读数据完成读事务
    "other": 6,  # 兜底信号最后展示
}

# AXI 成员排序优先展示地址、数据、响应，再展示握手信号。
AXI_MEMBER_ORDER = {  # AXI 各通道内部信号顺序权重
    "aw": {  # AXI 写地址通道成员顺序
        "addr": 0,  # 写地址总线优先
        "prot": 1,  # 保护属性紧随地址
        "len": 2,  # 突发长度靠前展示
        "size": 3,  # 单拍大小跟随长度
        "burst": 4,  # 突发类型描述地址递进
        "lock": 5,  # 独占访问信息居中
        "cache": 6,  # 缓存属性归入地址属性
        "qos": 7,  # 服务质量属性靠后
        "region": 8,  # 区域属性接近用户扩展
        "user": 9,  # 用户扩展属性靠近握手
        "valid": 10,  # 源端有效信号在握手前
        "ready": 11,  # 从端就绪信号收尾
    },
    "w": {  # AXI 写数据通道成员顺序
        "data": 0,  # 写 payload 总线优先
        "strb": 1,  # 字节使能贴近数据
        "last": 2,  # 突发结束标志居中
        "user": 3,  # 用户扩展位于握手前
        "valid": 4,  # 写数据有效信号
        "ready": 5,  # 写数据就绪信号
    },
    "b": {  # AXI 写响应通道成员顺序
        "resp": 0,  # 写响应码最先展示
        "user": 1,  # 响应用户扩展紧随状态码
        "valid": 2,  # 响应有效信号
        "ready": 3,  # 响应接收就绪信号
    },
    "ar": {  # AXI 读地址通道成员顺序
        "addr": 0,  # 读地址总线优先
        "prot": 1,  # 读保护属性紧随地址
        "len": 2,  # 读突发长度靠前
        "size": 3,  # 读单拍大小跟随长度
        "burst": 4,  # 读突发类型描述地址递进
        "lock": 5,  # 读独占访问属性
        "cache": 6,  # 读缓存属性
        "qos": 7,  # 读服务质量属性
        "region": 8,  # 读区域属性
        "user": 9,  # 读地址用户扩展
        "valid": 10,  # 读地址有效信号
        "ready": 11,  # 读地址就绪信号
    },
    "r": {  # AXI 读数据通道成员顺序
        "data": 0,  # 读 payload 总线优先
        "resp": 1,  # 每拍读响应紧随数据
        "last": 2,  # 读突发结束标志
        "user": 3,  # 读数据用户扩展
        "valid": 4,  # 读数据有效信号
        "ready": 5,  # 读数据接收就绪信号
    },
}

# AXI-Stream 端口被拆为数据和控制两组，便于阅读握手边界。
AXIS_SECTION_LABELS = {  # AXIS 按 payload、握手控制和兜底信号分区
    "clock_reset": "时钟复位",  # AXIS aclk/aresetn 一类公共时序
    "data": "数据通道",  # AXIS tdata/tkeep 等 payload 成员进入数据区域
    "control": "控制通道",  # AXIS 握手控制组
    "other": "其他信号",  # AXIS 未归类信号组
}

# AXIS 小节先给出时钟，再给 payload，最后给控制信号。
AXIS_SECTION_ORDER = {  # AXIS 流接口小节的阅读排位
    "clock_reset": 0,  # AXIS 时序信号先于流数据
    "data": 1,  # 流数据位于时序之后
    "control": 2,  # 控制信号跟随数据
    "other": 3,  # 其他信号最后展示
}

# AXIS 数据相关成员保持 tdata/tkeep/tstrb/user/id/dest 的常用阅读顺序。
AXIS_DATA_ORDER = {  # AXIS 数据侧信号顺序权重
    "data": 0,  # AXIS payload 总线优先
    "keep": 1,  # 字节保留掩码贴近数据
    "strb": 2,  # 字节有效掩码跟随 keep
    "user": 3,  # 用户侧带信息
    "id": 4,  # 流 ID 元数据
    "dest": 5,  # 目的路由元数据
}

# AXIS 控制成员把 valid/ready 放在 last 前，突出握手关系。
AXIS_CONTROL_ORDER = {  # AXIS 控制侧信号顺序权重
    "valid": 0,  # AXIS 源端有效信号优先
    "ready": 1,  # 接收端反压信号
    "last": 2,  # 帧结束标志
}

# APB 端口区按请求与响应划分，匹配 PADDR/PREADY 等信号语义。
APB_SECTION_LABELS = {  # APB 请求响应分区的中文标题表
    "clock_reset": "时钟复位",  # APB 时钟复位小节
    "request": "请求通道",  # APB 主侧请求组
    "response": "响应通道",  # APB 从侧响应组
}

# APB 小节顺序先呈现发起侧，再呈现从设备响应侧。
APB_SECTION_ORDER = {  # APB 事务阶段的版面排位
    "clock_reset": 0,  # APB 时序信号位于请求前
    "request": 1,  # 请求组紧随时序
    "response": 2,  # 响应组结束 APB 展示
}

# APB 成员排序把地址、控制、写数据放在请求段前半部分。
APB_MEMBER_ORDER = {  # APB 请求和响应信号顺序权重
    "request": {  # APB 主设备请求侧成员
        "paddr": 0,  # APB 地址总线优先
        "pprot": 1,  # APB 保护属性
        "psel": 2,  # 片选信号开启访问
        "penable": 3,  # 使能信号描述访问阶段
        "pwrite": 4,  # 写方向标志
        "pstrb": 5,  # 写字节使能
        "pwdata": 6,  # 写数据总线收尾
    },
    "response": {  # APB 从侧响应成员
        "prdata": 0,  # 读数据返回总线
        "pready": 1,  # 从设备就绪反馈
        "pslverr": 2,  # 从设备错误反馈
    },
}

# Wishbone 端口按主设备请求和从设备响应拆分。
WISHBONE_SECTION_LABELS = {  # Wishbone 主从事务分区的中文标题表
    "clock_reset": "时钟复位",  # Wishbone clk/rst 公共声明区
    "request": "请求通道",  # Wishbone cyc/stb/we 等发起侧
    "response": "响应通道",  # Wishbone ack/stall/err 等反馈侧
}

# Wishbone 小节权重与 APB 保持同类总线的阅读节奏。
WISHBONE_SECTION_ORDER = {  # Wishbone 分区在端口块中的排位
    "clock_reset": 0,  # Wishbone 时序信号位于事务前
    "request": 1,  # 请求组位于时序之后
    "response": 2,  # 响应组位于总线末尾
}

# Wishbone 成员排序覆盖地址、写数据信号和经典握手反馈。
WISHBONE_MEMBER_ORDER = {  # Wishbone classic 信号的组内排序表
    "request": {  # Wishbone 主侧请求成员
        "adr": 0,  # Wishbone 地址总线
        "dat_w": 1,  # Wishbone 写数据总线
        "we": 2,  # 写使能控制
        "sel": 3,  # 字节选择掩码
        "cyc": 4,  # 周期有效信号
        "stb": 5,  # 传输选通信号
        "cti": 6,  # 周期类型信息
        "bte": 7,  # 突发类型扩展
        "lock": 8,  # 总线锁定请求
    },
    "response": {  # Wishbone 从设备反馈侧成员
        "dat_r": 0,  # Wishbone 读数据总线
        "ack": 1,  # 访问确认反馈
        "stall": 2,  # 从侧暂停反馈
        "err": 3,  # 错误终止反馈
        "rty": 4,  # 重试请求反馈
    },
}

# UART 小节把发送、接收和调制解调状态信号分开。
UART_SECTION_LABELS = {  # UART 串行方向和状态线标题表
    "clock_reset": "时钟复位",  # UART 波特域时序小节
    "tx": "发送通道",  # UART 发送路径组
    "rx": "接收通道",  # UART 接收路径组
    "status": "调制解调/状态",  # UART 外部握手状态组
}

# UART 展示顺序先发送后接收，最后放低频状态线。
UART_SECTION_ORDER = {  # UART 串口信号分区的版面排位
    "clock_reset": 0,  # UART 时序信号先于串行方向
    "tx": 1,  # 发送组紧随时序
    "rx": 2,  # 接收组位于发送之后
    "status": 3,  # 低频状态线放末尾
}

# UART 成员权重覆盖裸 tx/rx 管脚、数据通道和握手/状态信号。
UART_MEMBER_ORDER = {  # UART 各小节内部信号顺序权重
    "tx": {  # UART 发送路径成员
        "txd": 0,  # 标准发送管脚名
        "tx": 1,  # 简写发送管脚名
        "tx_data": 2,  # 并行发送数据
        "tx_valid": 3,  # 发送数据有效
        "tx_ready": 4,  # 发送端可接收数据
        "tx_busy": 5,  # 发送器忙状态
    },
    "rx": {  # UART 接收路径成员
        "rxd": 0,  # 标准接收管脚名
        "rx": 1,  # 简写接收管脚名
        "rx_data": 2,  # 并行接收数据
        "rx_valid": 3,  # 接收数据有效
        "rx_ready": 4,  # 接收数据消费就绪
        "rx_err": 5,  # 接收错误状态
        "rx_busy": 6,  # 接收器忙状态
    },
    "status": {  # UART 调制解调状态成员
        "cts": 0,  # 清除发送状态线
        "rts": 1,  # 请求发送状态线
        "dsr": 2,  # 数据设备就绪状态线
        "dtr": 3,  # 数据终端就绪状态线
        "dcd": 4,  # 载波检测状态线
        "ri": 5,  # 振铃指示状态线
    },
}

# SPI 小节区分时钟片选、发送、接收和四线扩展数据。
SPI_SECTION_LABELS = {  # SPI 控制线、数据线和扩展线标题表
    "clock_reset": "时钟复位",  # SPI 控制器时序小节
    "clock_cs": "时钟片选",  # SPI 时钟与片选组
    "tx": "发送通道",  # SPI 主出从入方向组
    "rx": "接收通道",  # SPI 主入从出方向组
    "extended": "扩展数据",  # Quad/多 IO 扩展组
}

# SPI 展示顺序先列出时序控制，再列数据方向和扩展线。
SPI_SECTION_ORDER = {  # SPI 端口分区的阅读排位
    "clock_reset": 0,  # SPI 时序信号优先于片选
    "clock_cs": 1,  # 时钟片选先于数据线
    "tx": 2,  # 发送方向接在控制线后
    "rx": 3,  # 接收方向跟随发送方向
    "extended": 4,  # 扩展 IO 线最后展示
}

# SPI 成员排序兼容 MOSI/MISO 与 COPI/CIPO 两套命名。
SPI_MEMBER_ORDER = {  # SPI 别名信号在各分区内的排序表
    "clock_cs": {  # SPI 时钟与片选成员
        "sclk": 0,  # 标准 SPI 串行时钟
        "clk": 1,  # 简写时钟别名
        "cs": 2,  # 高有效片选别名
        "csn": 3,  # 低有效片选别名
        "ss": 4,  # 从设备选择别名
        "ss_n": 5,  # 低有效从设备选择
    },
    "tx": {  # SPI 发送方向成员
        "mosi": 0,  # 主出从入传统命名
        "copi": 1,  # 控制器出外设入命名
    },
    "rx": {  # SPI 接收方向成员
        "miso": 0,  # 主入从出传统命名
        "cipo": 1,  # 控制器入外设出命名
    },
    "extended": {  # SPI 多线扩展成员
        "sio0": 0,  # 串行 IO0 标准扩展名
        "sio1": 1,  # 串行 IO1 保持在 IO0 之后
        "sio2": 2,  # 串行 IO2 用于四线模式中段
        "sio3": 3,  # 串行 IO3 用于四线模式高位
        "io0": 4,  # 简写 IO0 扩展线
        "io1": 5,  # 简写 IO1 跟随标准 sio 别名
        "io2": 6,  # 简写 IO2 对应四线中段
        "io3": 7,  # 简写 IO3 对应四线高位
        "wp": 8,  # 写保护扩展线
        "hold": 9,  # 总线保持扩展线
    },
}

# I2C 小节把双向总线线缆和控制状态信号分离。
I2C_SECTION_LABELS = {  # I2C 把 SCL/SDA 物理线和控制器状态分成两个阅读区域
    "clock_reset": "时钟复位",  # I2C 控制器公共时序区
    "bus": "总线信号",  # I2C SCL/SDA 及三态拆分线进入总线区域
    "control": "控制状态",  # I2C 控制器状态组
}

# I2C 展示顺序先总线后控制，突出 SCL/SDA 的接口主体。
I2C_SECTION_ORDER = {  # I2C 物理线和状态区的排位表
    "clock_reset": 0,  # I2C 时序信号先于双线总线
    "bus": 1,  # 双线总线优先展示
    "control": 2,  # 控制状态跟随物理线
}

# I2C 成员排序覆盖三态拆分线和常见事务状态标志。
I2C_MEMBER_ORDER = {  # I2C 三态线和状态标志排序表
    "bus": {  # I2C SCL/SDA 物理线成员
        "scl": 0,  # SCL 未拆分管脚
        "scl_i": 1,  # SCL 输入采样线
        "scl_o": 2,  # SCL 输出驱动线
        "scl_t": 3,  # SCL 三态控制线
        "sda": 4,  # SDA 未拆分数据管脚
        "sda_i": 5,  # SDA 数据输入采样线
        "sda_o": 6,  # SDA 数据输出驱动线
        "sda_t": 7,  # SDA 数据三态控制线
    },
    "control": {  # I2C 控制状态成员
        "busy": 0,  # 控制器忙状态
        "ack": 1,  # ACK 采样状态
        "nack": 2,  # NACK 未应答采样状态
        "start": 3,  # START 条件标志
        "stop": 4,  # STOP 结束条件标志
    },
}

# GMII 小节区分碰撞监测、发送侧和接收侧。
GMII_SECTION_LABELS = {  # GMII 链路监测和双向数据标题表
    "clock_reset": "时钟复位",  # GMII MAC 时序小节
    "monitor": "监测通道",  # GMII 载波监测组
    "write": "写通道",  # GMII 发送方向组
    "read": "读通道",  # GMII 接收方向组
}

# GMII 展示顺序把链路监测信号放在数据方向之前。
GMII_SECTION_ORDER = {  # GMII 小节在 MAC 端口块中的排位
    "clock_reset": 0,  # GMII 时序信号先于链路监测
    "monitor": 1,  # 监测信号位于数据方向前
    "write": 2,  # 发送方向先于接收方向
    "read": 3,  # 接收方向最后展示
}

# GMII 成员排序覆盖载波/冲突、写侧时钟控制和读侧状态。
GMII_MEMBER_ORDER = {  # GMII 载波、发送和接收成员排序表
    "monitor": {  # GMII 载波与冲突监测成员
        "crs": 0,  # 载波侦听信号
        "col": 1,  # 碰撞检测信号
    },
    "write": {  # GMII TX 侧时钟控制成员
        "wclk": 0,  # 发送侧时钟
        "wctrl": 1,  # 发送侧控制线
        "wen": 2,  # 发送使能线
        "werr": 3,  # 发送错误线
        "wdata": 4,  # 发送数据总线
    },
    "read": {  # GMII RX 侧状态和数据成员
        "rclk": 0,  # 接收侧时钟
        "rctrl": 1,  # 接收侧控制线
        "rvalid": 2,  # 接收数据有效线
        "rerr": 3,  # 接收错误线
        "rdata": 4,  # 接收数据总线
    },
}

# RGMII 小节保留精简的写读方向划分。
RGMII_SECTION_LABELS = {  # rgmii_txd 和 rgmii_rxd 归类后依赖此映射显示写通道与读通道
    "clock_reset": "时钟复位",  # RGMII PHY 参考时钟和复位端口显示在此小节
    "write": "写通道",  # RGMII rgmii_txd 与 tx_ctl 输出端口显示在此小节
    "read": "读通道",  # RGMII rgmii_rxd 与 rx_ctl 输入端口显示在此小节
}

# RGMII 展示顺序直接跟随写侧和读侧两组 DDR 信号。
RGMII_SECTION_ORDER = {  # RGMII 精简双向分区排位
    "clock_reset": 0,  # RGMII 时序信号最先展示
    "write": 1,  # 发送方向排在接收前
    "read": 2,  # 接收方向结束展示
}

# RGMII 成员权重覆盖控制线、时钟线和紧凑数据总线。
RGMII_MEMBER_ORDER = {  # RGMII DDR 控制和数据排序表
    "write": {  # RGMII TX 输出侧成员
        "wclk": 0,  # RGMII 发送时钟
        "wctrl": 1,  # RGMII 发送控制
        "wdata": 2,  # RGMII 发送数据
    },
    "read": {  # RGMII RX 输入侧成员
        "rclk": 0,  # RGMII 接收时钟
        "rctrl": 1,  # RGMII 接收控制
        "rdata": 2,  # RGMII 接收数据
    },
}

# 未识别协议时用常见后缀聚合同名通道，降低 other 小节的杂乱程度。
UNKNOWN_CLUSTER_SUFFIXES = {  # 未知接口的端口聚类后缀集合
    "data",  # 数据总线后缀
    "valid",  # 有效信号后缀
    "ready",  # 就绪信号后缀
    "last",  # 末拍信号后缀
    "addr",  # 地址信号后缀
    "cmd",  # 命令信号后缀
    "rsp",  # 响应缩写后缀
    "resp",  # 响应完整后缀
    "req",  # 请求缩写后缀
    "ack",  # 确认信号后缀
    "enable",  # 使能信号后缀
    "sel",  # 选择信号后缀
}

# 小分组至少需要多个成员，避免孤立信号被过度包装成子标题。
PORT_SUBGROUP_MIN_MEMBERS = 3  # 端口子分组最少成员数

# 显式导出清单保持后端 mixin 对常量模块的依赖边界稳定。
__all__ = [  # formatter backend 常量公开导出名
    "REGION_LABELS",  # 区域标签表导出
    "REGION_TITLES",  # 区域标题表导出
    "TRAILING_COMMENT_COLUMN",  # 注释对齐列导出
    "PORT_DIRECTION_ORDER",  # 端口方向排序表导出
    "KNOWN_PROTOCOL_KINDS",  # 已知协议集合导出
    "KNOWN_CONFIG_PARAM_LABELS",  # 配置参数标签表导出
    "PARAM_FAMILY_GENERIC_TOKENS",  # 参数通用词过滤集合导出
    "PORT_SECTION_LABELS",  # 端口小节标签集合导出
    "PORT_SECTION_ALIASES",  # 端口小节别名表导出
    "AXI_SECTION_LABELS",  # AXI 小节标签表导出
    "AXI_SECTION_ORDER",  # AXI 小节顺序表导出
    "AXI_MEMBER_ORDER",  # AXI 成员顺序表导出
    "AXIS_SECTION_LABELS",  # AXIS 数据控制标题供端口 renderer 使用
    "AXIS_SECTION_ORDER",  # AXIS 流分区排位供排序器使用
    "AXIS_DATA_ORDER",  # AXIS 数据成员顺序导出
    "AXIS_CONTROL_ORDER",  # AXIS 控制成员顺序导出
    "APB_SECTION_LABELS",  # APB 请求响应标题供端口 renderer 使用
    "APB_SECTION_ORDER",  # APB 事务阶段排位供排序器使用
    "APB_MEMBER_ORDER",  # APB paddr/pready 成员排位供端口整理使用
    "WISHBONE_SECTION_LABELS",  # Wishbone 主从标题供端口 renderer 使用
    "WISHBONE_SECTION_ORDER",  # Wishbone 请求反馈排位供排序器使用
    "WISHBONE_MEMBER_ORDER",  # Wishbone adr/dat/ack 族字段的组内优先级
    "UART_SECTION_LABELS",  # UART 串行方向标题供端口 renderer 使用
    "UART_SECTION_ORDER",  # UART 发送接收状态排位供排序器使用
    "UART_MEMBER_ORDER",  # UART 管脚与状态成员排位供端口整理使用
    "SPI_SECTION_LABELS",  # SPI 控制和数据标题供端口 renderer 使用
    "SPI_SECTION_ORDER",  # SPI 片选数据扩展排位供排序器使用
    "SPI_MEMBER_ORDER",  # SPI MOSI/MISO 别名排位供端口整理使用
    "I2C_SECTION_LABELS",  # I2C 物理线状态标题供端口 renderer 使用
    "I2C_SECTION_ORDER",  # I2C 总线控制排位供排序器使用
    "I2C_MEMBER_ORDER",  # I2C SCL/SDA 拆分线与状态标志的组内优先级
    "GMII_SECTION_LABELS",  # GMII 监测和数据标题供端口 renderer 使用
    "GMII_SECTION_ORDER",  # GMII 链路方向排位供排序器使用
    "GMII_MEMBER_ORDER",  # GMII 载波监测、TX 与 RX 字段的组内优先级
    "RGMII_SECTION_LABELS",  # RGMII DDR 时序、TX 和 RX 三段标题
    "RGMII_SECTION_ORDER",  # RGMII 参考时序先于 DDR 数据方向的排位
    "RGMII_MEMBER_ORDER",  # RGMII 控制数据成员排位供端口整理使用
    "UNKNOWN_CLUSTER_SUFFIXES",  # 未知接口聚类后缀导出
    "PORT_SUBGROUP_MIN_MEMBERS",  # 子分组成员阈值导出
]
