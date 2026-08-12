"""提供 RTL VG 规则共享的常量、符号和位宽事实。"""

# future annotations 延后解析不可变常量模型类型。
from __future__ import annotations

# re 只解析受限 Verilog 常量和表达式形状。
import re

# dataclass 固定共享常量事实的不可变字段。
from dataclasses import dataclass

# formatter 实例渲染器提供已验证的配对括号和顶层逗号拆分逻辑。
from .formatter_backend.statement_render_mixin import StatementRenderMixin
from .formatter_backend.syntax_utils import SyntaxUtilsMixin

# ConstantBits 保存已解析常量的固定位宽模式。
@dataclass(frozen=True)
class ConstantBits:
    """保存已解析常量的固定位宽比特模式。"""

    # width 是字面量显式声明的结果位宽。
    width: int  # 常量显式位宽

    # bits 统一使用 0、1、x、z 和问号字符表达每一位。
    bits: str  # 与 width 对齐的规范比特文本

# module_constant_values 收集 parameter 与 localparam 原始常量文本。
def module_constant_values(dict_module: dict[str, object]) -> dict[str, str]:
    """返回当前 module 中可供规则解析的常量原文。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        常量名称到原始 Verilog 值文本的映射。
    """

    # values 同时容纳 module parameter 和局部常量。
    dict_values: dict[str, str] = {}  # 当前 module 的常量原文表

    # 两类 formatter 集合共享 name 与 value 字段合同。
    for str_collection in ("params", "localparams"):

        # 每条记录独立验证非空名称和值。
        for dict_item in dict_module.get(str_collection, []) or []:

            # name 是后续常量符号解析的作用域键。
            str_name = str(dict_item.get("name") or "")  # 当前常量名称

            # value 保留 formatter 提供的 Verilog 原文。
            str_value = str(dict_item.get("value") or "").strip()  # 当前常量值文本

            # 空名称或空值不具备可靠解析意义。
            if str_name and str_value:

                # 同一作用域内 formatter 的后项沿用声明顺序覆盖。
                dict_values[str_name] = str_value  # 登记可解析常量原文

    # 返回当前 module 的完整常量环境。
    return dict_values

# module_parameter_values 提取可安全用于声明区间的十进制整数。
def module_parameter_values(dict_module: dict[str, object]) -> dict[str, int]:
    """返回可安全用于区间求值的整数 parameter。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        parameter 名称到确定整数值的映射。
    """

    # values 只接纳受限解析器确认的整数 parameter。
    dict_values: dict[str, int] = {}  # 当前 module 的整数 parameter 表

    # localparam 不参与声明头部参数区间求值。
    for dict_item in dict_module.get("params", []) or []:

        # 显式十进制或裸十进制才能安全转换。
        int_value = parameter_integer(str(dict_item.get("value") or ""))  # 当前 parameter 整数值

        # 未知格式不进入符号求值表。
        if int_value is not None:

            # name 作为区间端点表达式的符号键。
            dict_values[str(dict_item.get("name") or "")] = int_value  # 登记已知 parameter 整数

    # 返回可供区间和重复连接消费的整数表。
    return dict_values

# InstanceSectionParser 复用 formatter 已验证的实例括号与顶层切分实现。
class InstanceSectionParser(StatementRenderMixin, SyntaxUtilsMixin):
    """为 VG097 暴露 formatter 内部的纯实例结构解析能力。"""

# _parse_instance_sections 从 formatter canonical 解析结果分离参数区与端口区。
def _parse_instance_sections(
    str_instance_text: str,
    str_module_name: str,
) -> tuple[list[str], list[str]] | None:
    """按配对括号解析实例参数和端口关联区。

    参数:
        str_instance_text: formatter AST 保留的完整实例文本。
        str_module_name: formatter AST 提供的被例化模块名。
    返回:
        参数关联项和端口关联项；结构不完整时返回 None。
    """

    # formatter parser 已按配对括号分离 params 与 ports。
    dict_sections = InstanceSectionParser()._parse_instance_for_render(str_instance_text)  # canonical 实例分区结果

    # 注释、括号不完整或非 canonical 实例保持未知。
    if dict_sections is None:

        # 缺少稳定分区时不能继续比较端口位宽。
        return None

    # AST 模块名与文本解析结果必须一致。
    if str(dict_sections.get("module_name") or "") != str_module_name:

        # 名称漂移说明实例文本事实不可信。
        return None

    # formatter 已完成顶层切分，复制为当前规则的字符串列表。
    list_parameter_items = [str(str_item) for str_item in dict_sections.get("params", [])]  # 参数关联项

    # 端口关联项来自参数区之后的独立括号。
    list_port_items = [str(str_item) for str_item in dict_sections.get("ports", [])]  # 端口关联项

    # 返回两个互不重叠的关联区。
    return list_parameter_items, list_port_items

# _parse_named_association 解析单个完整命名关联项。
def _parse_named_association(str_item: str) -> tuple[str, str] | None:
    """解析 `.NAME(expression)` 形式的完整关联项。

    参数:
        str_item: 顶层切分后的单个参数或端口关联。
    返回:
        名称和表达式；不是完整命名关联时返回 None。
    """

    # 前缀只接纳 Verilog 标准标识符形式。
    obj_name = re.match(r"^\.\s*([A-Za-z_]\w*)\s*", str_item)  # 命名关联前缀匹配结果

    # 没有命名关联前缀时不能建立名称对应。
    if obj_name is None:

        # 非命名关联交由调用方保留未知状态。
        return None

    # 名称之后必须是完整配对的表达式括号。
    str_remainder = str_item[obj_name.end() :].strip()  # 命名关联的表达式括号

    # 表达式缺少左括号时关联项结构不完整。
    if not str_remainder.startswith("("):

        # 不完整关联项不得继续提取表达式。
        return None

    # 关联项末尾不能在配对右括号之后包含其他文本。
    int_close = InstanceSectionParser()._find_matching_paren_in_text(str_remainder, 0)  # 命名关联右括号下标

    # 缺失配对右括号或尾随文本都使关联不可信。
    if int_close == -1 or str_remainder[int_close + 1 :].strip():

        # 畸形命名关联交由上层返回未知。
        return None

    # 表达式保留内部文本，调用方决定空连接或求值语义。
    return obj_name.group(1), str_remainder[1:int_close].strip()

# _instance_parameter_overrides 建立当前实例的受限整数参数覆盖。
def _instance_parameter_overrides(
    dict_child: dict[str, object],
    list_parameter_items: list[str],
) -> dict[str, int] | None:
    """验证并解析当前实例的命名或位置参数覆盖。

    参数:
        dict_child: formatter AST 中的子模块声明。
        list_parameter_items: 已从实例参数区分离的顶层关联项。
    返回:
        已验证的整数参数覆盖；无法安全对应或求值时返回 None。
    """

    # 参数声明顺序是位置关联唯一允许使用的对应依据。
    list_parameter_names = [
        str(dict_item.get("name") or "")  # 当前 parameter 声明名称
        for dict_item in dict_child.get("params", []) or []  # 遍历子模块 parameter 声明
    ]  # 子模块 parameter 声明顺序

    # 空参数名称不能建立可靠参数环境。
    if any(not str_name for str_name in list_parameter_names):

        # 缺失声明名称时无法安全对应任何覆盖。
        return None

    # 重复参数名称使命名和位置关联都不唯一。
    if len(set(list_parameter_names)) != len(list_parameter_names):

        # 声明名称不唯一时必须保持未知。
        return None

    # 没有显式覆盖时沿用子模块默认参数。
    if not list_parameter_items:

        # 空覆盖表让 module_widths 保持原默认参数行为。
        return {}

    # 命名与位置关联不能混用，否则顺序语义不可靠。
    list_is_named = [str_item.lstrip().startswith(".") for str_item in list_parameter_items]  # 每项是否为命名参数

    # 混合关联没有安全且一致的对应策略。
    if any(list_is_named) and not all(list_is_named):

        # 禁止回退到默认参数掩盖混合关联。
        return None

    # overrides 只接纳已声明且可由受限求值器解析的整数参数。
    dict_overrides: dict[str, int] = {}  # 当前实例已验证参数覆盖

    # 命名参数按显式名称建立覆盖，不依赖书写顺序。
    if all(list_is_named):

        # 每个命名关联独立验证名称、重复项和整数值。
        for str_item in list_parameter_items:

            # 当前关联必须完整符合命名参数语法。
            tuple_association = _parse_named_association(str_item)  # 当前命名参数关联

            # 畸形命名关联不能建立参数环境。
            if tuple_association is None:

                # 不完整参数关联保持未知。
                return None

            # 参数名必须真实存在且不能重复覆盖。
            str_name, str_expression = tuple_association  # 当前参数名称与覆盖表达式

            # 未知或重复参数名均禁止静默使用默认值。
            if str_name not in list_parameter_names or str_name in dict_overrides:

                # 参数名称无法唯一对应声明时保持未知。
                return None

            # 实例参数仅接受受限整数常量，复杂表达式保持未知。
            int_value = parameter_integer(str_expression)  # 当前命名参数整数值

            # 受限求值器不支持的表达式不能进入覆盖表。
            if int_value is None:

                # 禁止不可求值表达式回退到声明默认值。
                return None

            # 已验证整数值覆盖同名 parameter 默认值。
            dict_overrides[str_name] = int_value  # 当前命名参数的确定覆盖值

        # 返回全部已验证命名覆盖。
        return dict_overrides

    # 位置实参数量不能超过子模块 parameter 声明数量。
    if len(list_parameter_items) > len(list_parameter_names):

        # 超出声明数量后无法安全对应剩余实参。
        return None

    # 位置参数严格按 formatter 保留的声明顺序对应。
    for int_index, str_expression in enumerate(list_parameter_items):

        # 每个位置表达式必须由受限整数求值器确认。
        int_value = parameter_integer(str_expression)  # 当前位置参数整数值

        # 不可求值的位置表达式使整个参数环境未知。
        if int_value is None:

            # 禁止局部覆盖后对未知位置回退默认值。
            return None

        # 声明顺序唯一决定当前位置的参数名称。
        dict_overrides[list_parameter_names[int_index]] = int_value  # 当前位置参数的确定覆盖值

    # 返回全部已验证位置覆盖。
    return dict_overrides

# _named_port_connections 只从已分离端口区读取命名连接。
def _named_port_connections(list_port_items: list[str]) -> list[tuple[str, str]] | None:
    """解析实例端口区中的完整命名连接。

    参数:
        list_port_items: 已从实例文本分离的端口关联项。
    返回:
        端口名称和连接表达式列表；含位置连接或畸形项时返回 None。
    """

    # connections 保持端口书写顺序，便于稳定生成发现项。
    list_connections: list[tuple[str, str]] = []  # 已验证命名端口连接

    # 每个端口项必须完整符合命名关联形式。
    for str_item in list_port_items:

        # 当前端口关联只在完整命名形式下进入位宽比较。
        tuple_association = _parse_named_association(str_item)  # 当前命名端口关联

        # 位置或畸形端口关联不能可靠对应子模块端口名。
        if tuple_association is None:

            # 上层将 None 传播为 VG097 未知状态。
            return None

        # 已验证命名端口按实例书写顺序收集。
        list_connections.append(tuple_association)

    # 返回只含真实端口区内容的连接列表。
    return list_connections

# module_widths 从 formatter 声明事实和可选实例参数覆盖建立作用域位宽表。
def module_widths(
    dict_module: dict[str, object],
    parameter_overrides: dict[str, int] | None = None,
) -> dict[str, int | None]:
    """提取端口、声明和局部常量的简单位宽。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
        parameter_overrides: 已验证的实例 parameter 整数覆盖；省略时沿用声明默认值。
    返回:
        标识符到确定位宽或未知值的映射。
    """

    # widths 为未知区间显式保留 None，供规则传播 inconclusive。
    dict_widths: dict[str, int | None] = {}  # 当前 module 的标识符位宽表

    # parameter 整数用于解析符号区间端点。
    dict_parameter_values = module_parameter_values(dict_module)  # 解析声明区间使用的整数参数环境

    # 实例级覆盖只在调用方完成参数名和表达式验证后替换默认值。
    if parameter_overrides is not None:

        # 复制传入整数值，保持无覆盖调用方的既有行为不变。
        dict_parameter_values.update(parameter_overrides)

    # 端口和内部声明共享相同 width 字段解析规则。
    for str_collection in ("ports", "decls"):

        # 每条声明独立计算静态位宽。
        for dict_item in dict_module.get(str_collection, []) or []:

            # 未知位宽仍登记名称，便于区分已声明信号与未知符号。
            dict_widths[str(dict_item.get("name") or "")] = parse_width(  # 登记声明信号位宽
                str(dict_item.get("width") or ""),  # formatter 保留的区间文本
                dict_parameter_values,  # 区间表达式可引用的整数参数
            )

    # localparam 的常量结果宽度也可参与表达式比较。
    for dict_item in dict_module.get("localparams", []) or []:

        # 仅支持定宽字面量和受限重复连接。
        dict_widths[str(dict_item.get("name") or "")] = constant_width(  # 登记局部常量位宽
            str(dict_item.get("value") or ""),  # localparam 原始常量文本
            dict_parameter_values,  # 重复次数可引用的整数参数
        )

    # 返回当前 formatter module 的完整简单位宽环境。
    return dict_widths

# parse_width 计算常量或单参数加减常量的 Verilog 闭区间宽度。
def parse_width(str_width: str, dict_parameter_values: dict[str, int]) -> int | None:
    """解析受支持的 Verilog 声明区间。

    参数:
        str_width: formatter 提供的声明区间文本。
        dict_parameter_values: 可供区间引用的 parameter 整数表。
    返回:
        确定位宽；无法静态解析时返回 None。
    """

    # 没有声明区间的信号按 Verilog 标量处理。
    if not str_width:

        # 标量默认占一位。
        return 1

    # 区间层只切分左右端点，端点合法性仍由受限整数解析器决定。
    obj_match = re.search(r"\[\s*([^:\[\]]+?)\s*:\s*([^:\[\]]+?)\s*\]", str_width)  # 声明区间匹配结果

    # 不支持的区间表达式保持未知。
    if obj_match is None:

        # 调用规则会把 None 转成 inconclusive。
        return None

    # 左端点使用受限整数表达式解析器。
    int_left = constant_integer(obj_match.group(1), dict_parameter_values)  # 声明区间左端点

    # 右端点沿用同一解析语义。
    int_right = constant_integer(obj_match.group(2), dict_parameter_values)  # 声明区间右端点

    # 任一端点未知都会使整个位宽未知。
    if int_left is None or int_right is None:

        # 禁止猜测复杂参数算术结果。
        return None

    # Verilog 闭区间宽度等于端点差绝对值加一。
    return abs(int_left - int_right) + 1

# constant_integer 解析数字或单个已知 parameter 加减十进制增量。
def constant_integer(str_expression: str, dict_parameter_values: dict[str, int]) -> int | None:
    """解析受限整数表达式。

    参数:
        str_expression: 数字、参数或参数加减数字文本。
        dict_parameter_values: 已知 parameter 整数表。
    返回:
        确定整数；不支持或未知时返回 None。
    """

    # 模式限制为一个基础值和一个可选加减增量，只放宽运算符两侧空白。
    obj_match = re.fullmatch(r"(\w+)(?:\s*([+-])\s*(\d+))?", str_expression.strip())  # 受限整数表达式匹配结果

    # 复杂算术不进入静态求值范围。
    if obj_match is None:

        # None 会沿调用链传播为未知事实。
        return None

    # base 可以是裸十进制或已知 parameter 名称。
    str_base = obj_match.group(1)  # 整数表达式基础值文本

    # 数字直接转换，符号则从当前 parameter 表查询。
    int_value = int(str_base) if str_base.isdigit() else dict_parameter_values.get(str_base)  # 整数表达式基础值

    # 未知基础值或没有增量时直接返回当前结果。
    if int_value is None or obj_match.group(2) is None:

        # 可能返回确定基础值或未知 None。
        return int_value

    # 第三捕获组是非负十进制增量。
    int_delta = int(obj_match.group(3))  # 参数表达式加减增量

    # 第二捕获组决定增量方向。
    return int_value + int_delta if obj_match.group(2) == "+" else int_value - int_delta

# parameter_integer 接纳裸十进制和显式十进制 Verilog 常量。
def parameter_integer(str_value: str) -> int | None:
    """读取可安全用于区间求值的十进制 parameter。

    参数:
        str_value: formatter 提供的 parameter 常量文本。
    返回:
        十进制整数；不支持的常量形式返回 None。
    """

    # 显式十进制前缀单独捕获位宽，以便应用 Verilog 截断语义。
    obj_match = re.fullmatch(r"(?:(\d+)'[dD])?(\d+)", str_value.strip())  # parameter 十进制匹配结果

    # 未匹配的常量形式不进入静态整数环境。
    if obj_match is None:

        # 受限求值器无法确认该常量的整数语义。
        return None

    # 十进制数位先转换为未截断的非负整数。
    int_value = int(obj_match.group(2))  # parameter 原始十进制数值

    # 裸十进制常量没有显式位宽，保持原整数值。
    if obj_match.group(1) is None:

        # 无定宽前缀时不存在高位截断。
        return int_value

    # Verilog 定宽常量要求正位宽，零位宽保持未知。
    int_width = int(obj_match.group(1))  # parameter 显式常量位宽

    # 非法零位宽不能生成可靠整数结果。
    if int_width <= 0:

        # 交由上层传播 inconclusive，避免猜测非法常量。
        return None

    # 数值已能放入声明位宽时无需构造截断掩码。
    if int_value.bit_length() <= int_width:

        # 未溢出的定宽十进制值保持不变。
        return int_value

    # 超出位宽的高位按 Verilog 无符号定宽常量语义截断。
    int_mask = (1 << int_width) - 1  # 保留低 int_width 位的截断掩码

    # 返回定宽常量在 elaboration 中实际携带的非负整数值。
    return int_value & int_mask

# constant_width 推断定宽字面量或受限重复连接的结果宽度。
def constant_width(str_value: str, dict_parameter_values: dict[str, int]) -> int | None:
    """推断受支持常量形式的结果位宽。

    参数:
        str_value: localparam 的原始常量文本。
        dict_parameter_values: 可供重复次数引用的 parameter 表。
    返回:
        确定结果位宽；不支持的形式返回 None。
    """

    # 定宽字面量的前缀直接给出结果宽度。
    obj_literal = re.fullmatch(  # 定宽字面量匹配结果
        r"(\d+)'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+",  # 捕获显式位宽前缀
        str_value.strip(),  # 去除常量原文两侧空白
    )

    # 普通定宽字面量无需继续推断。
    if obj_literal is not None:

        # 第一捕获组就是显式结果宽度。
        return int(obj_literal.group(1))

    # 重复连接只支持简单次数和单个定宽字面量。
    obj_repeat = re.fullmatch(  # 常量重复连接匹配结果
        r"\{(\w+)\{(\d+)'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+\}\}",  # 捕获次数与内部位宽
        str_value.strip(),  # 去除重复连接两侧空白
    )

    # 其他连接或算术形式保持未知。
    if obj_repeat is None:

        # 调用规则不得把复杂常量误判为已知宽度。
        return None

    # 重复次数可为数字或已知 parameter。
    int_count = constant_integer(obj_repeat.group(1), dict_parameter_values)  # 重复连接次数

    # 总宽度等于重复次数乘内部字面量宽度。
    return None if int_count is None else int_count * int(obj_repeat.group(2))

# expression_width 返回分支和表达式规则支持的高置信静态宽度。
def expression_width(str_expression: str, dict_widths: dict[str, int | None]) -> int | None:
    """返回受支持表达式的静态位宽。

    参数:
        str_expression: 待判断位宽的 Verilog 表达式文本。
        dict_widths: 当前 module 的标识符位宽表。
    返回:
        确定位宽；未知标识符或复杂形式返回 None。
    """

    # 外层完整括号不改变表达式结果位宽。
    str_value = strip_outer_parentheses(str_expression.strip())  # 去除完整外层括号的表达式

    # 定宽字面量直接读取显式位宽前缀。
    obj_literal = re.fullmatch(r"(\d+)'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+", str_value)  # 表达式定宽字面量识别结果

    # 字面量匹配成功时不依赖声明表。
    if obj_literal is not None:

        # 第一捕获组是显式结果位宽。
        return int(obj_literal.group(1))

    # 简单位选始终产生单位宽结果。
    if re.fullmatch(r"[A-Za-z_]\w*\s*\[\s*[^:\]]+\s*\]", str_value):

        # 位选条件可直接满足标量要求。
        return 1

    # 常数范围选择可以精确计算闭区间宽度。
    obj_slice = re.fullmatch(r"[A-Za-z_]\w*\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]", str_value)  # 常数范围选择匹配结果

    # 匹配成功时按端点差计算结果宽度。
    if obj_slice is not None:

        # 范围选择结果宽度等于端点差绝对值加一。
        return abs(int(obj_slice.group(1)) - int(obj_slice.group(2))) + 1

    # 关系和逻辑运算符产生单位宽真值结果。
    if re.search(r"(?:===|!==|==|!=|<=|>=|<|>|&&|\|\|)", str_value):

        # 布尔结果可作为单位宽分支条件。
        return 1

    # 逻辑非和归约运算符也产生单位宽结果。
    if re.match(r"^\s*(?:!|&|\||\^|~&|~\||~\^|\^~)\s*", str_value):

        # 归约或逻辑非结果固定为一位。
        return 1

    # 其余受支持形式必须是当前作用域简单标识符。
    return dict_widths.get(str_value)

# resolve_constant_bits 递归解析字面量或当前作用域常量别名。
def resolve_constant_bits(
    str_expression: str,
    dict_constant_values: dict[str, str],
    *,
    _seen: frozenset[str] = frozenset(),
) -> ConstantBits | None:
    """把定宽字面量或常量符号解析为规范二进制模式。

    参数:
        str_expression: 待解析的 Verilog 常量表达式。
        dict_constant_values: 当前 module 的常量名称到原文映射。
        _seen: 递归解析时已访问的常量名称集合。
    返回:
        已知固定位宽比特模式；不支持或循环引用时返回 None。
    """

    # 外层完整括号和两侧空白不影响常量身份。
    str_value = strip_outer_parentheses(str_expression.strip())  # 当前待解析常量文本

    # 简单标识符尝试作为 parameter 或 localparam 别名递归解析。
    if re.fullmatch(r"[A-Za-z_]\w*", str_value):

        # 已访问名称或未声明名称都不能继续解析。
        if str_value in _seen or str_value not in dict_constant_values:

            # None 让调用规则保留未知状态。
            return None

        # 递归解析常量原文并记录已访问名称防止循环。
        return resolve_constant_bits(
            dict_constant_values[str_value],
            dict_constant_values,
            _seen=_seen | {str_value},
        )

    # 数位下划线只影响可读性，不影响常量值。
    str_value = str_value.replace("_", "")  # 去除字面量数位分隔符

    # 只接受显式位宽和显式进制的 Verilog 字面量。
    obj_match = re.fullmatch(r"(\d+)'[sS]?([bBoOdDhH])([0-9a-fA-FxXzZ?]+)", str_value)  # 定宽常量匹配结果

    # 无尺寸或复杂常量表达式保持未知。
    if obj_match is None:

        # 上层不会把未知常量当作确定通过。
        return None

    # 第一捕获组固定结果宽度。
    int_width = int(obj_match.group(1))  # 字面量前缀声明的结果位数

    # 第二捕获组确定数位展开基数。
    str_base = obj_match.group(2).lower()  # 常量规范进制字符

    # 第三捕获组保留数字和未知态字符。
    str_digits = obj_match.group(3).lower()  # 常量规范数位文本

    # 各进制数位统一展开为二进制字符。
    str_bits = _digits_to_bits(str_base, str_digits)  # 常量展开后的比特文本

    # 不支持的数位或超出显式位宽时保持未知。
    if str_bits is None or len(str_bits) > int_width:

        # 禁止截断超宽常量后伪造确定模式。
        return None

    # 左侧补零使比特文本长度与显式位宽一致。
    return ConstantBits(int_width, str_bits.rjust(int_width, "0"))

# strip_outer_parentheses 仅移除完整包围表达式的括号层。
def strip_outer_parentheses(str_expression: str) -> str:
    """移除完整包围表达式的成对外层括号。

    参数:
        str_expression: 待规范化的 Verilog 表达式文本。
    返回:
        去除完整外层括号后的表达式文本。
    """

    # value 在循环中逐层剥离完整外括号。
    str_value = str_expression.strip()  # 当前规范化表达式文本

    # 只有首尾都是括号时才需要检查是否完整包围。
    while str_value.startswith("(") and str_value.endswith(")"):

        # depth 跟踪当前扫描位置的括号层级。
        int_depth = 0  # 当前括号嵌套深度

        # wrapped 默认假设外括号完整包围全部文本。
        bool_wrapped = True  # 当前首尾括号是否为同一完整外层

        # 扫描中途提前归零说明外括号没有包围全部表达式。
        for int_index, str_character in enumerate(str_value):

            # 左括号增加当前嵌套层级。
            int_depth += str_character == "("  # 更新左括号深度

            # 右括号关闭当前嵌套层级。
            int_depth -= str_character == ")"  # 更新右括号深度

            # 最末字符之前归零证明存在外层并列表达式。
            if int_depth == 0 and int_index != len(str_value) - 1:

                # 当前首尾括号不能作为完整外层删除。
                bool_wrapped = False  # 标记外括号未完整包围

                # 无需继续扫描当前字符串。
                break

        # 外括号不完整时结束剥离。
        if not bool_wrapped:

            # 保留当前文本中的语义括号。
            break

        # 删除当前完整外括号并继续检查下一层。
        str_value = str_value[1:-1].strip()  # 剥离一层完整外括号

    # 返回保留内部语义括号的规范表达式。
    return str_value

# _digits_to_bits 把不同进制数位展开为二进制字符。
def _digits_to_bits(str_base: str, str_digits: str) -> str | None:
    """把定宽字面量数位转换为保留未知态的二进制文本。

    参数:
        str_base: 已规范化的小写进制字符。
        str_digits: 已去除下划线的小写数位文本。
    返回:
        展开后的二进制字符；不支持的十进制未知态返回 None。
    """

    # 二进制数位已经是目标表示。
    if str_base == "b":

        # 保留 0、1、x、z 和问号字符。
        return str_digits

    # 十进制没有稳定的逐位未知态展开语义。
    if str_base == "d":

        # 纯数字转二进制，未知态数位保持 None。
        return None if not str_digits.isdigit() else format(int(str_digits), "b")

    # 八进制每位展开三比特，十六进制每位展开四比特。
    int_group_width = 3 if str_base == "o" else 4  # 单个数位对应的二进制宽度

    # groups 按原始数位顺序保存展开结果。
    list_groups: list[str] = []  # 每个八进制或十六进制数位的比特组

    # 每个数位独立展开，未知态字符复制到整组。
    for str_digit in str_digits:

        # x、z 和问号在该进制数位覆盖整组比特。
        if str_digit in "xz?":

            # 未知态字符按组宽重复。
            list_groups.append(str_digit * int_group_width)

        # 普通数位按对应进制转为定宽二进制。
        else:

            # radix 只可能是八或十六。
            int_radix = 8 if str_base == "o" else 16  # 当前数位转换基数

            # 左侧补零保持每个数位的固定组宽。
            list_groups.append(format(int(str_digit, int_radix), f"0{int_group_width}b"))

    # 连接所有数位组得到完整二进制文本。
    return "".join(list_groups)
