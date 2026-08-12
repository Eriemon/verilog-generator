"""工作流 mock provider 的注释辅助逻辑。"""

# future annotations 让注释 helper 保持最小导入成本
from __future__ import annotations

# mock 注释 helper 只依赖端口契约类型
from .model_provider import MockPortSpec

# 端口注释规则选择逻辑从这里开始
def _first_mock_comment_match(comment_rules: tuple[tuple[bool, str], ...]) -> str | None:
    """
    从端口注释规则表中取首个命中的说明。

    :param comment_rules: 按优先级排列的命中状态和注释文本。
    :return: 首个命中的注释文本；没有命中时返回 None。
    """

    # 规则表已经按语义优先级排列，首个命中项就是最终说明。
    return next((str_comment for bool_matched, str_comment in comment_rules if bool_matched), None)

# 生成 SPI 相关端口的业务职责说明
def _mock_spi_port_comment(port_name: str, direction_word: str) -> str | None:
    """
    根据端口名生成 SPI 专属职责短语。

    :param port_name: 原始端口名称。
    :param direction_word: 已翻译后的方向词。
    :return: 命中 SPI 语义时返回说明，否则返回 None。
    """

    # SPI 规则需要先识别串行时钟，避免被全局时钟规则覆盖。
    str_lowered_name = port_name.lower()  # SPI 端口关键词扫描文本

    # 按 SPI 专用规则优先返回首个命中说明，避免回落到通用接口注释。
    return _first_mock_comment_match(
        (
            ("sclk" in str_lowered_name or "spi_clk" in str_lowered_name, f"SPI串行时钟{direction_word}"),  # SPI 时钟脚
            ("sdo" in str_lowered_name, f"SPI串行数据{direction_word}"),  # SPI 数据脚
            ("cnv" in str_lowered_name or "conv" in str_lowered_name, f"SPI转换启动{direction_word}"),  # ADC 转换脚
            ("sync" in str_lowered_name and "tdd" not in str_lowered_name, f"SPI帧同步{direction_word}"),  # DAC 同步脚
        )
    )

# 生成状态类侧带端口的业务职责说明
def _mock_status_sideband_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成故障、触发和忙状态职责短语。

    :param port_name: 原始端口名称。
    :return: 命中状态侧带语义时返回说明，否则返回 None。
    """

    # 状态侧带规则覆盖动作触发、外设故障和转换忙状态。
    str_lowered_name = port_name.lower()  # 状态侧带关键词扫描文本

    # 状态规则的文本刻意避开“用户接口信号”这类兜底模板。
    tuple_status_comment_rules = (  # 状态侧带注释规则表
        ("fault" in str_lowered_name, "外设故障状态输入"),  # 故障状态脚
        ("trigger" in str_lowered_name or "trig" in str_lowered_name, "采集触发输入"),  # 采集触发脚
        ("busy" in str_lowered_name, "转换忙状态输入"),  # 忙状态脚
    )

    # 按状态侧带规则返回端口职责。
    return _first_mock_comment_match(tuple_status_comment_rules)

# 生成 TDD 侧带端口的业务职责说明
def _mock_tdd_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成 TDD 同步和使能职责短语。

    :param port_name: 原始端口名称。
    :return: 命中 TDD 语义时返回说明，否则返回 None。
    """

    # TDD 规则只处理含 tdd 的侧带端口，不抢占 SPI sync。
    str_lowered_name = port_name.lower()  # TDD helper 使用的规范化端口名

    # 按 TDD 专用规则区分同步输入与使能输出，避免近似重复。
    return _first_mock_comment_match(
        (
            ("tdd" in str_lowered_name and "sync" in str_lowered_name, "TDD同步节拍输入"),  # TDD 节拍脚
            (
                "tdd" in str_lowered_name and ("enable" in str_lowered_name or "enabled" in str_lowered_name),  # TDD 使能条件
                "TDD侧带使能输出",  # TDD 使能文本
            ),  # TDD 使能脚
        )
    )

# 生成收发链路端口的业务职责说明
def _mock_rx_tx_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成 RX/TX ready 和 enable 职责短语。

    :param port_name: 原始端口名称。
    :return: 命中 RX/TX 语义时返回说明，否则返回 None。
    """

    # 收发链路规则使用中文接收/发送，保留重复检测需要的真实语义差异。
    str_lowered_name = port_name.lower()  # 收发端口关键词扫描文本

    # ready 和 enable 分别描述链路状态与控制方向。
    tuple_rx_tx_comment_rules = (  # 收发链路注释规则表
        ("rx" in str_lowered_name and "ready" in str_lowered_name, "接收链路就绪输入"),  # 接收就绪脚
        ("tx" in str_lowered_name and "ready" in str_lowered_name, "发送链路就绪输入"),  # 发送就绪脚
        ("rx" in str_lowered_name and ("enable" in str_lowered_name or "enabled" in str_lowered_name), "接收链路使能输出"),  # 接收使能脚
        ("tx" in str_lowered_name and ("enable" in str_lowered_name or "enabled" in str_lowered_name), "发送链路使能输出"),  # 发送使能脚
    )

    # 按收发链路规则返回端口职责。
    return _first_mock_comment_match(tuple_rx_tx_comment_rules)

# 生成侧带控制端口的业务职责说明
def _mock_sideband_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成 TDD、RX/TX 和状态类职责短语。

    :param port_name: 原始端口名称。
    :return: 命中侧带控制语义时返回说明，否则返回 None。
    """

    # 侧带分类按特化程度排序，避免通用状态说明覆盖链路方向。
    tuple_sideband_comments = (  # 侧带分类候选说明
        _mock_status_sideband_port_comment(port_name),  # 状态侧带候选
        _mock_tdd_port_comment(port_name),  # TDD 侧带候选
        _mock_rx_tx_port_comment(port_name),  # 收发链路候选
    )

    # 取第一个非空侧带说明。
    return next((str_comment for str_comment in tuple_sideband_comments if str_comment), None)

# 生成通用握手和数据端口的业务职责说明
def _mock_data_handshake_port_comment(port_name: str, direction_word: str, width: int) -> str | None:
    """
    根据端口名生成数据、valid、ready 等通用职责短语。

    :param port_name: 原始端口名称。
    :param direction_word: 已翻译后的方向词。
    :param width: 端口位宽。
    :return: 命中通用语义时返回说明，否则返回 None。
    """

    # 通用规则只在专用 SPI/侧带规则没有命中后使用。
    str_lowered_name = port_name.lower()  # 通用端口关键词扫描文本

    # sample data/valid 语义优先于普通 data/valid 语义。
    str_sample_comment = _mock_sample_port_comment(str_lowered_name, direction_word, width)  # 采样端口职责

    # 命中采样语义时直接返回。
    if str_sample_comment:

        # 返回采样数据或采样有效职责。
        return str_sample_comment

    # 握手控制词会覆盖普通数据兜底，避免 ready/valid 被误解释成载荷。
    str_handshake_comment = _mock_handshake_control_port_comment(str_lowered_name, direction_word)  # 握手控制职责

    # 命中握手控制语义时直接返回。
    if str_handshake_comment:

        # 返回握手或控制端口职责。
        return str_handshake_comment

    # 数据端口作为通用兜底语义。
    return _mock_plain_data_port_comment(str_lowered_name, direction_word, width)

# 生成 sample 类端口职责说明。
def _mock_sample_port_comment(lowered_name: str, direction_word: str, width: int) -> str | None:
    """
    根据小写端口名生成采样数据或采样有效说明。

    :param lowered_name: 已转成小写的端口名。
    :param direction_word: 已翻译后的方向词。
    :param width: 端口位宽。
    :return: 命中 sample 语义时返回说明，否则返回 None。
    """

    # sample valid 表示采样路径的有效标志。
    if "sample" in lowered_name and "valid" in lowered_name:

        # 返回采样有效职责。
        return f"采样{direction_word}有效标志"

    # sample data 表示采样路径的数据载荷。
    if "sample" in lowered_name and "data" in lowered_name:

        # 多比特采样数据保留总线宽度说明。
        return f"{width}位采样{direction_word}数据总线" if width > 1 else f"采样{direction_word}数据位"

    # 非 sample 端口交给后续规则。
    return None

# ready、valid、start、done 这类控制脚在这里统一转成人读说明。
def _mock_handshake_control_port_comment(lowered_name: str, direction_word: str) -> str | None:
    """
    根据小写端口名生成常见握手或控制说明。

    :param lowered_name: 已转成小写的端口名。
    :param direction_word: 已翻译后的方向词。
    :return: 命中握手或控制语义时返回说明，否则返回 None。
    """

    # tuple_handshake_rules 保留从特化到通用的命中优先级。
    tuple_handshake_rules = (  # 通用握手控制注释规则
        ("push" in lowered_name and "ready" in lowered_name, "推送通道就绪输出"),  # 推送 ready 优先使用通道语义
        ("valid" in lowered_name, f"{direction_word}有效标志"),  # 普通 valid 表示方向相关有效状态
        ("ready" in lowered_name, f"{direction_word}就绪标志"),  # 普通 ready 表示方向相关就绪状态
        ("start" in lowered_name, "启动控制信号"),  # start 归入启动控制口
        ("done" in lowered_name, "完成状态标志"),  # done 归入完成状态口
    )  # 握手控制候选职责

    # 返回第一个命中的握手控制职责。
    return _first_mock_comment_match(tuple_handshake_rules)

# 生成普通 data 类端口职责说明。
def _mock_plain_data_port_comment(lowered_name: str, direction_word: str, width: int) -> str | None:
    """
    根据小写端口名生成普通数据端口说明。

    :param lowered_name: 已转成小写的端口名。
    :param direction_word: 已翻译后的方向词。
    :param width: 端口位宽。
    :return: 命中 data 语义时返回说明，否则返回 None。
    """

    # 非数据端口不在这里兜底。
    if "data" not in lowered_name:

        # 交回上层使用默认接口语义。
        return None

    # 多比特数据总线保留位宽说明。
    return f"{width}位{direction_word}数据总线" if width > 1 else f"{direction_word}数据位"

# 生成端口或输出桥接的业务职责说明
def _mock_port_intent_comment(port_name: str, direction_word: str, width: int) -> str:
    """
    根据端口名生成可复用的业务职责短语。

    :param port_name: 原始端口名称。
    :param direction_word: 已翻译后的输入、输出或双向方向词。
    :param width: 端口位宽。
    :return: 适合写入实体注释的中文职责说明。
    """

    # SPI 端口先按外设接口语义解析。
    str_spi_comment = _mock_spi_port_comment(port_name, direction_word)  # SPI 端口职责

    # 命中 SPI 角色时直接返回。
    if str_spi_comment:

        # 返回 SPI 语义说明。
        return str_spi_comment

    # TDD、RX/TX、fault 等侧带控制端口单独解析。
    str_sideband_comment = _mock_sideband_port_comment(port_name)  # 侧带控制端口职责

    # 命中侧带控制角色时直接返回。
    if str_sideband_comment:

        # 返回侧带控制语义说明。
        return str_sideband_comment

    # 数据、valid、ready 等常见端口最后解析。
    str_data_handshake_comment = _mock_data_handshake_port_comment(port_name, direction_word, width)  # 通用数据握手职责

    # 命中常见数据握手角色时直接返回。
    if str_data_handshake_comment:

        # 返回通用数据握手说明。
        return str_data_handshake_comment

    # 当前名称未命中特殊角色时，保留端口方向和接口语义。
    return f"{direction_word}用户接口信号"

# 生成单个端口的中文说明
def _mock_port_comment(port: MockPortSpec) -> str:
    """
    根据端口名称、方向与角色生成中文说明。

    :param port: 单个端口描述字典。
    :return: 适合写入 Verilog 右侧注释的中文文本。
    """

    # 读取端口名、方向和角色。
    str_name = str(port.get("name") or "")  # 端口名原文

    # 读取端口方向，后续用于拼接“输入/输出/双向”等中文语义。
    str_direction = str(port.get("direction") or "")  # 当前端口的原始方向字段

    # 读取端口角色。
    str_role = str(port.get("role") or "").lower()  # role 字段的小写文本

    # 读取端口位宽。
    int_width = int(port.get("width", 1) or 1)  # 端口总线位宽

    # 把端口名降成小写，便于匹配 clock/reset/valid 等关键词。
    str_lowered_name = str_name.lower()  # 名称关键字扫描文本

    # 把方向字段翻译成中文词，后面可直接参与注释文本拼接。
    str_direction_word = {
        "input": "输入",  # input 方向统一翻译成“输入”
        "output": "输出",  # output 方向统一翻译成“输出”
        "inout": "双向",  # inout 方向统一翻译成“双向”
    }.get(str_direction, "接口")  # 端口方向对应的中文词

    # 时钟角色或全局时钟名优先返回时序语义，避免误把 SPI sclk 当主时钟。
    if str_role == "clock" or str_lowered_name in {"i_clk", "clk", "clock", "i_clock"}:

        # 标记驱动时序的主时钟。
        return "工作时钟"

    # 复位端口优先返回复位语义。
    if str_role == "reset" or "rst" in str_lowered_name or "reset" in str_lowered_name:

        # 标记低有效复位输入。
        return "低有效复位"

    # 业务端口由名称关键词生成实体专属说明。
    return _mock_port_intent_comment(str_name, str_direction_word, int_width)

# 生成内部输出寄存器说明
def _mock_internal_output_comment(port_name: str) -> str:
    """
    根据端口名生成内部输出寄存器的中文说明。

    :param port_name: 外部输出端口名。
    :return: 内部输出寄存器的中文用途说明。
    """

    # 把输出端口名降成小写，后续根据业务关键词挑选说明文本。
    str_lowered_name = port_name.lower()  # 用于判断桥接语句语义类别的小写端口名

    # valid 输出返回握手缓存语义。
    if "valid" in str_lowered_name:

        # 说明该寄存器保存 valid 状态。
        return "输出有效缓存寄存器"

    # data 输出返回数据缓存语义。
    if "data" in str_lowered_name:

        # 说明该寄存器保存输出数据。
        return "输出数据缓存寄存器"

    # done 输出返回完成状态语义。
    if "done" in str_lowered_name:

        # 说明该寄存器保存完成标志。
        return "完成状态缓存寄存器"

    # 当名称无法推断出更细语义时，统一回退到通用输出缓存说明。
    return "输出端口缓存寄存器"

# 生成未使用输出补零说明
def _mock_unused_output_comment(port_name: str, width: int) -> str:
    """
    为未使用输出生成包含端口职责的补零说明。

    :param port_name: 外部输出端口名。
    :param width: 输出端口位宽。
    :return: 行尾补零注释文本。
    """

    # 复用端口职责短语，避免多个补零 assign 使用同一句模板。
    str_port_intent = _mock_port_intent_comment(port_name, "输出", width)  # 输出端口职责

    # 补零行为要说明该端口当前未参与 mock 数据路径。
    return f"{str_port_intent}未接入时固定低电平"

# 生成输出桥接说明
def _mock_output_bridge_comment(port_name: str) -> str:
    """
    根据端口名生成 assign 桥接语句的中文说明。

    :param port_name: 外部输出端口名。
    :return: 输出桥接语句的中文用途说明。
    """

    # 把输出端口名降成小写，便于识别桥接语句承载的是哪类输出语义。
    str_lowered_name = port_name.lower()  # 输出端口名的小写文本

    # valid 输出桥接强调握手状态传播。
    if "valid" in str_lowered_name:

        # 说明内部 valid 到端口的连通关系。
        return "输出有效标志桥接"

    # data 输出桥接强调数据总线传播。
    if "data" in str_lowered_name:

        # 说明这是内部数据总线到外部输出端口的桥接语句。
        return "输出数据总线桥接"

    # done 输出桥接强调完成标志传播。
    if "done" in str_lowered_name:

        # 说明完成标志到端口的连通关系。
        return "完成状态标志桥接"

    # 当名称没有显式语义时，统一视为普通输出端口桥接。
    return "输出端口桥接"

# 统一 mock RTL 的格式化收尾
def _add_mock_line_comments(text: str) -> str:
    """
    为非注释行自动补充 Verilog 行尾说明。

    :param text: 原始 Verilog 文本。
    :return: 带 mock 行尾语义注释的文本。
    """

    # 初始化补注释后的行容器。
    list_rendered_lines: list[str] = []  # 追加语义注释后的源码行

    # 逐行扫描输入文本。
    for line in text.splitlines():

        # 读取去空白后的语义内容。
        stripped_line = line.strip()  # 当前行的语义文本

        # 普通代码行需要追加行尾说明。
        if stripped_line and not stripped_line.startswith("//") and "//" not in line:

            # 追加带语义标签的代码行。
            list_rendered_lines.append(f"{line}\t//{_mock_semantic_comment(stripped_line)}")

        # 注释行和空白行保持原样。
        else:

            # 原样保留当前行。
            list_rendered_lines.append(line)

    # 输出逐行补注释后的文本。
    return "\n".join(list_rendered_lines) + "\n"

# 返回 mock Verilog 常见前缀到中文语义说明的固定映射表。
def _mock_prefix_comments() -> tuple[tuple[str, str], ...]:
    """
    汇总 mock Verilog 常见语句前缀到中文说明文本的映射。

    :param: 无输入参数；映射内容由函数内部固定维护。
    :return: 供逐行补注释流程复用的前缀与中文语义说明元组。
    """

    # 返回最常见 Verilog 前缀及其中文语义说明。
    return (
        ("module ", "模块声明，定义当前 mock 设计单元。"),
        ("endmodule", "结束当前模块，收束当前 mock 设计单元。"),
        ("input", "输入端口声明，接收测试平台或上游驱动。"),
        ("output", "输出端口声明，对外暴露当前设计结果。"),
        ("parameter", "参数声明，约束当前 mock 的位宽或配置。"),
        ("localparam", "局部常量声明，固定内部复位或状态值。"),
        ("reg ", "寄存器声明，保存时序路径中的中间状态。"),
        ("wire ", "导线声明，连接组合结果与观测节点。"),
        ("assign ", "连续赋值语句，把内部信号桥接到目标端口。"),
        ("always@(*)", "组合逻辑块，生成无时钟依赖的结果。"),
        ("always @(*)", "组合逻辑块，生成无时钟依赖的结果。"),
        ("always", "时序逻辑块，驱动寄存器在时钟边沿更新。"),
        ("case", "多路分支选择，根据状态或条件切换路径。"),
        ("endcase", "结束当前 case 分支选择。"),
        ("if", "条件判断语句，区分复位或运行路径。"),
        ("else", "条件兜底分支，承接未命中的运行路径。"),
        ("end", "结束当前 begin-end 代码块。"),
        ("$display", "成功摘要输出，向仿真日志报告结果。"),
        (".", "端口映射语句，把 TB 信号接到 DUT 端口。"),
        ("#", "延时控制语句，给信号传播预留稳定时间。"),
    )

# 生成 mock 行的中文语义标签
def _mock_semantic_comment(stripped: str) -> str:
    """
    为 mock Verilog 行生成简短语义说明。

    :param stripped: 去掉首尾空白后的单行 Verilog 文本。
    :return: 行尾中文语义注释。
    """

    # 读取 mock Verilog 常见前缀与中文语义说明的映射表。
    tuple_prefix_comments = _mock_prefix_comments()  # 逐项匹配的语义前缀表

    # 先按前缀表匹配最常见的源码结构。
    for prefix, comment_text in tuple_prefix_comments:

        # 当前前缀命中时直接返回对应语义。
        if stripped.startswith(prefix):

            # 输出前缀规则定义的语义说明。
            return comment_text

    # 例化头行带括号结尾时补充模块例化语义。
    if stripped.endswith("(") and "_Inst" in stripped:

        # 说明下方即将展开待测模块实例。
        return "模块例化入口，开始连接待测设计。"

    # 未命中已知规则时回退到通用说明。
    return "普通语句，保持 mock 示例的结构可审查。"

# 生成内部输出名
def _internal_output_name(port_name: str) -> str:
    """
    为外部输出端口派生内部寄存器名。

    :param port_name: 外部输出端口名。
    :return: 内部输出寄存器名。
    """

    # 遇到 o_ 前缀时，先剥离对外端口前缀再生成内部寄存器名。
    if port_name.startswith("o_"):

        # 生成更紧凑的内部寄存器名。
        return port_name[2:] + "_o"

    # 其他输出端口直接在原名后追加内部寄存器后缀。
    return port_name + "_o"

# 生成给定宽度的零字面量
def _zero_literal(width: int) -> str:
    """
    根据位宽返回 Verilog 零值字面量。

    :param width: 信号位宽。
    :return: 宽度匹配的零字面量文本。
    """

    # 单比特宽度直接使用 1'b0。
    if width <= 1:

        # 返回单比特零字面量。
        return "1'b0"

    # 多比特宽度返回显式位宽零值。
    return f"{width}'d0"

# 生成可选位宽前缀
def _width_text(signal: MockPortSpec | None) -> str:
    """
    根据端口或信号描述生成位宽文本。

    :param signal: 含 width 字段的信号描述字典。
    :return: 空串或形如 [n:0] 的位宽前缀。
    """

    # 读取信号位宽并做 1 位兜底。
    int_width = int((signal or {}).get("width", 1) or 1)  # 信号位宽

    # 单比特信号无需显式位宽前缀。
    if int_width <= 1:

        # 返回空位宽前缀。
        return ""

    # 多比特信号返回标准位宽文本。
    return f"[{int_width - 1}:0] "

# 构造 mock 响应 manifest
