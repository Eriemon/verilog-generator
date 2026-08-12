"""提供 RTL PG 规则共享的常量、符号和位宽事实。"""

# future annotations 延后解析不可变常量模型类型。
from __future__ import annotations

# re 只解析受限 Verilog 常量和表达式形状。
import re

# dataclass 固定共享常量事实的不可变字段。
from dataclasses import dataclass

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

# module_widths 从 formatter 声明事实建立作用域位宽表。
def module_widths(dict_module: dict[str, object]) -> dict[str, int | None]:
    """提取端口、声明和局部常量的简单位宽。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        标识符到确定位宽或未知值的映射。
    """

    # widths 为未知区间显式保留 None，供规则传播 inconclusive。
    dict_widths: dict[str, int | None] = {}  # 当前 module 的标识符位宽表

    # parameter 整数用于解析符号区间端点。
    dict_parameter_values = module_parameter_values(dict_module)  # 解析声明区间使用的整数参数环境

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

    # 受限模式只捕获数字、参数及单次加减的左右端点。
    obj_match = re.search(r"\[\s*([\w+-]+)\s*:\s*([\w+-]+)\s*\]", str_width)  # 声明区间匹配结果

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

    # 模式限制为一个基础值和一个可选加减增量。
    obj_match = re.fullmatch(r"(\w+)(?:([+-])(\d+))?", str_expression.strip())  # 受限整数表达式匹配结果

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

    # 显式十进制前缀允许带位宽但不接纳未知态数位。
    obj_match = re.fullmatch(r"(?:(?:\d+)'[dD])?(\d+)", str_value.strip())  # parameter 十进制匹配结果

    # 未匹配时保持未知，匹配时转换捕获的十进制数位。
    return None if obj_match is None else int(obj_match.group(1))

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
