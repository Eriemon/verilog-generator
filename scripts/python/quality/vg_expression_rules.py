"""实现表达式、条件和位宽相关 RTL VG 门禁。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# re 在 formatter AST 已确认的 module 文本内匹配表达式构造。
import re

# Callable 描述固定编号到规则函数的路由表。
from typing import Callable

# facts 提供可信 module 文本、行号和结构化声明。
from .vg_semantic_facts import VgFacts, iter_trusted_modules

# models 统一逐门禁状态和定位证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# value_facts 是表达式与分支规则共享的常量和位宽事实实现。
from . import vg_value_facts

# XZ_LITERAL 只匹配显式包含 X/Z 数位的 Verilog 定宽字面量。
XZ_LITERAL = r"\b\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]*[xXzZ][0-9a-fA-F_xXzZ?]*\b"  # X/Z 字面量模式

# UNSIZED_NUMBER 排除定宽字面量、标识符和位选择中的十进制数字。
UNSIZED_NUMBER = r"(?<!['\w\[:])\d+(?!['\w\]:])"  # 未显式声明位宽和进制的数字模式

# SIZED_LITERAL 是位宽规则可静态求值的定宽字面量形式。
SIZED_LITERAL = r"\d+'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+"  # 可提取前缀位宽的字面量模式

# SIMPLE_OPERAND 限定当前批次只判断标识符和显式定宽字面量。
SIMPLE_OPERAND = rf"(?:[A-Za-z_]\w*|{SIZED_LITERAL})"  # 高置信二元操作数模式

# evaluate_expression_gate 把固定编号路由到表达式规则实现。
def evaluate_expression_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行表达式规则组中的指定 VG 门禁。

    参数:
        str_gate_id: 当前执行的固定 VG 表达式门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前表达式规则的逐门禁结论。
    """

    # 路由表只包含统一 VG 语义引擎分配给本模块的激活编号。
    dict_evaluators: dict[str, Callable[[VgFacts], VgEvaluation]] = {  # 固定编号到表达式规则函数的映射
        "VG072": _xz_arithmetic,  # X/Z 算术表达式检查
        "VG074": _logic_operator_scalar_operands,  # 逻辑运算符标量操作数检查
        "VG078": _xz_condition,  # X/Z 条件表达式检查
        "VG085": _relational_width,  # 关系表达式位宽检查
        "VG101": _xz_branch_condition,  # X/Z 分支控制检查
        "VG122": _arithmetic_result_width,  # 算术结果溢出检查
        "VG125": _literal_declared_width_matches_value,  # 字面量声明位宽检查
        "VG134": _arithmetic_sign_consistency,  # 算术操作数符号一致性检查
        "VG137": _assignment_width,  # 赋值两侧位宽检查
        "VG138": _explicit_literal,  # 常量显式位宽进制检查
    }

    # engine 已保证编号属于本模块，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _literal_declared_width_matches_value 比较定宽常量声明与最小数值宽度。
def _literal_declared_width_matches_value(facts: VgFacts) -> VgEvaluation:
    """检查定宽字面量的声明位宽是否等于其最小实际位宽。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        位宽不一致的失败证据或适用性结论。
    """

    # findings 专门保存需要调整声明宽度的字面量位置。
    list_findings: list[VgFinding] = []  # 声明位宽与实际位宽不一致证据

    # applicable 区分无字面量输入与检查后合规。
    bool_applicable = False  # 是否发现可静态求值的定宽字面量

    # 模式分别捕获声明位宽、进制和数位。
    str_pattern = (  # 声明位宽、进制和数位三个捕获组
        r"\b(\d+)'[sS]?([bBoOdDhH])"
        r"([0-9a-fA-F_xXzZ?]+)(?![0-9a-fA-F_xXzZ?])"
    )

    # 每个可信 module 独立扫描可静态求值的定宽字面量。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 保留匹配对象以生成精确证据位置和原文。
        for obj_match in re.finditer(str_pattern, str_module_text):

            # 当前匹配使规则进入适用状态。
            bool_applicable = True  # 已发现规则适用的定宽字面量

            # 第一捕获组提供声明位宽。
            int_declared_width = int(obj_match.group(1))  # 字面量声明位宽

            # 第二捕获组统一为小写进制标识。
            str_base = obj_match.group(2).lower()  # 归一化进制标识

            # 第三捕获组移除仅用于可读性的下划线。
            str_digits = obj_match.group(3).replace("_", "")  # 移除可读分隔符后的数位

            # X/Z/? 数位没有可比较的确定数值宽度，交由专门未知态规则处理。
            if re.search(r"[xXzZ?]", str_digits):

                # 继续检查当前 module 的其他确定数值字面量。
                continue

            # 参数声明表达设计接口宽度，全零常量表达复位/清零宽度，二者不按最小数值位宽收缩。
            # 最近换行位置限定字面量所在声明行。
            int_line_start = str_module_text.rfind("\n", 0, obj_match.start()) + 1  # 当前源码行起始偏移

            # 行前缀用于识别 parameter/localparam 合同。
            str_line_prefix = str_module_text[int_line_start : obj_match.start()]  # 字面量之前的声明文本

            # 参数宽度与显式全零宽度都是设计合同，不按数值最小宽度收缩。
            if re.search(r"\b(?:parameter|localparam)\b", str_line_prefix) or (
                str_base == "d" and int(str_digits, 10) == 0
            ):

                # 当前字面量属于合同宽度，继续扫描其他字面量。
                continue

            # 受限求值器只处理已排除未知态的确定数值。
            int_actual_width = _literal_actual_width(str_base, str_digits)  # 当前数值的最小位宽

            # 声明宽度已经精确时无需生成诊断。
            if int_declared_width == int_actual_width:

                # 继续检查当前 module 的其他字面量。
                continue

            # 把 module 内偏移换算为一基文件行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 当前字面量一基行号

            # 报告声明宽度与最小数值宽度的确定差异。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    f"字面量声明为 {int_declared_width} 位，但实际值只需要 {int_actual_width} 位。",
                    obj_match.group(0),
                )
            )

    # 任一宽度差异都使本门禁失败。
    if list_findings:

        # 返回全部字面量证据，支持批量修复。
        return failed(*list_findings)

    # 没有违规时保留规则是否实际适用的信息。
    return passed(applicable=bool_applicable)

# _literal_actual_width 对确定数位执行受限的最小宽度求值。
def _literal_actual_width(str_base: str, str_digits: str) -> int:
    """计算受支持定宽字面量的最小实际位宽。

    参数:
        str_base: 已归一化的小写进制标识。
        str_digits: 已移除分隔符的字面量数位。
    返回:
        至少为一位的最小表示宽度。
    """

    # 非十进制字面量按每个数位的固定承载位数计算。
    if str_base != "d":

        # 二、八、十六进制分别按一、三、四位展开。
        return max(1, len(str_digits) * {"b": 1, "o": 3, "h": 4}[str_base])

    # 防御性保留未知态分支，避免未来调用方绕过上游过滤。
    if re.search(r"[xXzZ?]", str_digits):

        # 未知十进制数位按最保守的每位四比特估计。
        return max(1, len(str_digits) * 4)

    # 确定数值通过 Python 整数位长得到精确最小宽度。
    # 当前进制标识映射为 Python 整数解析基数。
    int_radix = {"b": 2, "o": 8, "d": 10, "h": 16}[str_base]  # 当前字面量数值进制

    # 确定数位转换为非负整数值。
    int_value = int(str_digits, int_radix)  # 解析后的非负整数值

    # 零值也至少需要一个表示位。
    return max(1, int_value.bit_length())

# _logic_operator_scalar_operands 禁止逻辑运算符直接消费多位向量。
def _logic_operator_scalar_operands(facts: VgFacts) -> VgEvaluation:
    """检查逻辑与、逻辑或两侧是否为可证明的标量表达式。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG074 的通过、失败或不确定结论。
    """

    # 逻辑表达式使用统一的简单二元表达式扫描器。
    return _binary_expression_gate(facts, str_rule="logic")

# _arithmetic_result_width 检查目标是否容纳简单算术表达式的完整结果。
def _arithmetic_result_width(facts: VgFacts) -> VgEvaluation:
    """比较简单算术结果所需位宽与赋值目标位宽。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG122 的通过、失败或不确定结论。
    """

    # 算术宽度规则复用同一受限表达式扫描边界。
    return _binary_expression_gate(facts, str_rule="overflow")

# _arithmetic_sign_consistency 禁止简单算术表达式混用有符号和无符号操作数。
def _arithmetic_sign_consistency(facts: VgFacts) -> VgEvaluation:
    """比较简单算术表达式两侧的声明符号属性。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG134 的通过、失败或不确定结论。
    """

    # 符号规则沿用相同匹配结果，避免建立第二套表达式抽取器。
    return _binary_expression_gate(facts, str_rule="sign")

# _binary_expression_gate 对高置信简单赋值表达式执行三类规则判断。
def _binary_expression_gate(facts: VgFacts, *, str_rule: str) -> VgEvaluation:
    """执行逻辑标量、算术位宽或符号一致性判断。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        str_rule: logic、overflow 或 sign 规则选择器。
    返回:
        当前规则的确定结论或事实不足结论。
    """

    # findings 汇总当前规则的全部确定违规。
    list_findings: list[VgFinding] = []  # 当前规则的定位证据

    # applicable 只在发现目标二元表达式后置位。
    bool_applicable = False  # 是否存在当前规则关心的表达式

    # unknown 防止未声明操作数被静默视为通过。
    bool_unknown = False  # 是否存在无法静态判断的表达式

    # 每个 formatter 可信 module 独立建立声明事实。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 位宽表统一来自共享 value facts。
        dict_widths = vg_value_facts.module_widths(dict_module)  # 当前 module 的信号位宽

        # signed 字段由 formatter 对端口和内部声明统一提供。
        dict_signed = _module_signedness(dict_module)  # 当前 module 的信号符号属性

        # 逻辑规则和算术规则只选择各自运算符集合。
        str_operator = (  # 当前规则允许匹配的运算符
            r"&&|\|\|" if str_rule == "logic" else r"\+|-|\*|/|%"  # 逻辑或算术运算符集合
        )

        # 受限模式只接受简单目标和简单二元操作数。
        str_pattern = (  # 简单二元赋值模式
            rf"(?:assign\s+)?\b([A-Za-z_]\w*)\s*(?:<=|=)\s*"
            rf"({SIMPLE_OPERAND})\s*({str_operator})\s*({SIMPLE_OPERAND})\s*;"
        )

        # 每条匹配独立产生确定证据或未知标记。
        for obj_match in re.finditer(str_pattern, str_module_text):

            # 匹配成功证明规则适用。
            bool_applicable = True  # 当前 module 存在目标表达式

            # 捕获目标、左右操作数和运算符供规则分支复用。
            str_target, str_left, str_operator_text, str_right = obj_match.groups()  # 当前二元赋值的四个语义字段

            # 三类规则通过独立助手返回违规、通过或未知。
            str_outcome = _binary_expression_outcome(  # 当前表达式的三态判断结果
                str_rule=str_rule,  # 当前规则选择器
                str_target=str_target,  # 赋值目标名称

                # 二元表达式主体保持 formatter 原始操作数顺序。
                str_left=str_left,  # 左操作数文本
                str_operator=str_operator_text,  # 二元运算符文本
                str_right=str_right,  # 右操作数文本

                # 共享声明事实供单条规则计算宽度和符号。
                dict_widths=dict_widths,  # 当前 module 位宽事实
                dict_signed=dict_signed,  # 当前 module 符号事实
            )

            # 未知事实只登记状态，不制造定位诊断。
            if str_outcome == "unknown":

                # 继续扫描其他表达式以优先收集确定违规。
                bool_unknown = True  # 当前表达式无法静态判断

                # 当前匹配没有确定 finding。
                continue

            # 只有 violation 才形成逐行修复证据。
            if str_outcome == "violation":

                # module 基线结合匹配前换行数得到一基行号。
                int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 当前违规的一基源码行号

                # 规则选择器映射到稳定的中文诊断。
                dict_messages = {  # 规则选择器到用户诊断的映射
                    "logic": "逻辑运算符直接使用多位向量操作数。",  # 标量逻辑违规说明
                    "overflow": "算术结果位宽超过赋值目标位宽。",  # 完整结果截断风险说明
                    "sign": "算术表达式混用有符号和无符号操作数。",  # 符号扩展歧义说明
                }  # 三类表达式审查的稳定用户诊断

                # finding 保留原始赋值表达式便于修复。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,
                        int_line,
                        dict_messages[str_rule],
                        obj_match.group(0).strip(),
                    )
                )

    # 确定违规优先于同文件其他未知表达式。
    if list_findings:

        # 返回全部确定命中。
        return failed(*list_findings)

    # 没有违规但存在未知目标时保持 fail-closed。
    if bool_unknown:

        # 不同规则共享同一事实不足说明。
        return inconclusive("存在无法静态确定的二元表达式事实。")

    # 全部适用表达式通过或没有目标形状时返回通过。
    return passed(applicable=bool_applicable)

# _binary_expression_outcome 计算单条简单二元赋值的规则结果。
def _binary_expression_outcome(
    *,
    str_rule: str,
    str_target: str,

    # 以下三个参数共同描述二元表达式主体。
    str_left: str,
    str_operator: str,
    str_right: str,

    # 共享事实仅服务当前表达式，不在助手内重新解析 module。
    dict_widths: dict[str, int | None],
    dict_signed: dict[str, bool],
) -> str:
    """返回单条表达式的 pass、violation 或 unknown。

    参数:
        str_rule: 当前规则选择器。
        str_target: 赋值目标名称。
        str_left: 左操作数文本。
        str_operator: 二元运算符文本。
        str_right: 右操作数文本。
        dict_widths: 当前 module 位宽事实。
        dict_signed: 当前 module 符号事实。
    返回:
        pass、violation 或 unknown 三态字符串。
    """

    # 三个操作数宽度统一通过共享表达式事实求取。
    int_target_width = vg_value_facts.expression_width(str_target, dict_widths)  # 赋值目标位宽

    # 左操作数宽度用于标量和算术结果判断。
    int_left_width = vg_value_facts.expression_width(str_left, dict_widths)  # 左操作数位宽

    # 右操作数独立求宽，未知值不会继承左侧结果。
    int_right_width = vg_value_facts.expression_width(str_right, dict_widths)  # 右操作数位宽

    # 逻辑规则要求两侧都可证明为单位宽。
    if str_rule == "logic":

        # 任一未知宽度都无法给出可信结论。
        if int_left_width is None or int_right_width is None:

            # 未声明或复杂操作数保留未知状态。
            return "unknown"

        # 任一多位操作数即违反标量逻辑约束。
        return "violation" if max(int_left_width, int_right_width) > 1 else "pass"

    # 溢出规则需要目标和两侧位宽全部已知。
    if str_rule == "overflow":

        # 缺少任一位宽都保持未知。
        if int_target_width is None or int_left_width is None or int_right_width is None:

            # 目标或操作数宽度不足时不猜测溢出结论。
            return "unknown"

        # 加减结果额外保留进位或借位。
        if str_operator in {"+", "-"}:

            # 较宽操作数再增加一位形成完整结果。
            int_required_width = max(int_left_width, int_right_width) + 1  # 加减完整结果位宽

        # 乘法结果需要容纳两侧位宽总和。
        elif str_operator == "*":

            # 两个操作数的位数共同决定完整乘积范围。
            int_required_width = int_left_width + int_right_width  # 乘法完整结果位宽

        # 除法和取模不扩大左操作数结果范围。
        else:

            # 左操作数宽度是当前受限规则的安全上界。
            int_required_width = int_left_width  # 除法或取模结果位宽

        # 目标不足以容纳完整结果时报告溢出风险。
        return "violation" if int_target_width < int_required_width else "pass"

    # 符号规则需要两个操作数都具有可确定符号属性。
    bool_left_signed = _expression_signedness(str_left, dict_signed)  # 左操作数符号属性

    # 右操作数符号独立解析，避免继承左侧声明。
    bool_right_signed = _expression_signedness(str_right, dict_signed)  # 右操作数符号属性

    # 未声明符号来源不能被默认为 unsigned。
    if bool_left_signed is None or bool_right_signed is None:

        # 未声明操作数保持未知状态。
        return "unknown"

    # 两侧符号属性不同即形成混用。
    return "violation" if bool_left_signed != bool_right_signed else "pass"

# _module_signedness 提取 formatter 声明中的显式 signed 属性。
def _module_signedness(dict_module: dict[str, object]) -> dict[str, bool]:
    """返回当前 module 的信号名称到 signed 标志映射。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        信号名称到显式 signed 标志的映射。
    """

    # 端口和内部声明共享 signed 字段。
    return {
        str(dict_item.get("name") or ""): bool(dict_item.get("signed", False))
        for str_collection in ("ports", "decls")
        for dict_item in dict_module.get(str_collection, []) or []
        if str(dict_item.get("name") or "")
    }

# _expression_signedness 读取简单标识符或定宽字面量的符号属性。
def _expression_signedness(str_expression: str, dict_signed: dict[str, bool]) -> bool | None:
    """返回简单算术操作数的 signed 属性；未知时返回 None。

    参数:
        str_expression: 简单标识符或定宽字面量。
        dict_signed: 当前 module 的声明符号映射。
    返回:
        确定符号标志；表达式不受支持时返回 None。
    """

    # 标识符必须来自 formatter 已确认声明。
    if re.fullmatch(r"[A-Za-z_]\w*", str_expression):

        # 未声明标识符从映射查询得到 None。
        return dict_signed.get(str_expression)

    # 定宽字面量只有显式 s 修饰时视为 signed。
    if re.fullmatch(r"\d+'[sS][bBoOdDhH][0-9a-fA-F_xXzZ?]+", str_expression):

        # 显式 s 修饰符确定有符号语义。
        return True

    # 其他定宽字面量是确定的 unsigned。
    if re.fullmatch(r"\d+'[bBoOdDhH][0-9a-fA-F_xXzZ?]+", str_expression):

        # 无 s 修饰的定宽字面量按无符号处理。
        return False

    # 区间端点超出受限整数语法时停止静态求值。
    return None

# _xz_arithmetic 禁止未知态字面量进入算术运算。
def _xz_arithmetic(facts: VgFacts) -> VgEvaluation:
    """检查算术表达式是否包含 X/Z 字面量。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG072 的确定性执行结论。
    """

    # 双向模式覆盖未知态字面量位于运算符任一侧的情况。
    return _pattern_gate(
        facts,
        rf"[^;\n]*(?:\+|-|\*|/|%)[^;\n]*{XZ_LITERAL}|{XZ_LITERAL}[^;\n]*(?:\+|-|\*|/|%)",
        "算术表达式包含 X/Z 字面量。",
    )

# _xz_condition 禁止未知态字面量参与布尔或三目判断。
def _xz_condition(facts: VgFacts) -> VgEvaluation:
    """检查条件和三目表达式是否包含 X/Z 字面量。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG078 的确定性执行结论。
    """

    # 模式同时覆盖 if 条件和三目运算符两侧的未知态字面量。
    return _pattern_gate(
        facts,
        rf"(?:if\s*\([^\n)]*{XZ_LITERAL}|\?[^;\n]*{XZ_LITERAL}|{XZ_LITERAL}[^;\n]*\?)",
        "条件表达式包含 X/Z 字面量。",
    )

# _xz_branch_condition 专门覆盖 if 与 case 控制表达式。
def _xz_branch_condition(facts: VgFacts) -> VgEvaluation:
    """检查分支控制表达式是否包含 X/Z 字面量。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG101 的确定性执行结论。
    """

    # case、casex 和 casez 控制项都进入相同未知态检查。
    return _pattern_gate(
        facts,
        rf"(?:if\s*\([^\n)]*{XZ_LITERAL}|case[xz]?\s*\([^\n)]*{XZ_LITERAL})",
        "分支控制表达式包含 X/Z 字面量。",
    )

# _relational_width 复用通用位宽比较并限定关系表达式。
def _relational_width(facts: VgFacts) -> VgEvaluation:
    """比较简单关系表达式两侧的可判定位宽。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG085 的通过、失败或不确定结论。
    """

    # relational_only 选择比较运算符匹配路径。
    return _width_gate(facts, relational_only=True)

# _assignment_width 将同一求宽算法切换到连续和过程赋值语法。
def _assignment_width(facts: VgFacts) -> VgEvaluation:
    """比较简单赋值两侧的可判定位宽。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG137 的通过、失败或不确定结论。
    """

    # relational_only 关闭后选择连续或过程赋值匹配路径。
    return _width_gate(facts, relational_only=False)

# _explicit_literal 拒绝赋值和参数中的无尺寸十进制数。
def _explicit_literal(facts: VgFacts) -> VgEvaluation:
    """检查常量是否显式声明位宽与进制。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG138 的确定性执行结论。
    """

    # 前置负向断言避免把位选择下标误判为无尺寸字面量。
    return _pattern_gate(
        facts,
        rf"(?:=|parameter\s+\w+\s*=)[^;\n]*{UNSIZED_NUMBER}",
        "常量未显式声明位宽和进制。",
    )

# _width_gate 对简单标识符和定宽字面量执行静态位宽比较。
def _width_gate(facts: VgFacts, *, relational_only: bool) -> VgEvaluation:
    """执行关系或赋值表达式的简单位宽比较。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        relational_only: True 检查关系表达式，False 检查赋值。
    返回:
        失败证据、不确定原因或确定通过结论。
    """

    # applicable 区分没有目标表达式和已完成静态比较。
    bool_applicable = False  # 是否发现符合本规则形状的表达式

    # findings 保存所有可确定的两侧位宽冲突。
    list_findings: list[VgFinding] = []  # 位宽不一致的定位证据

    # unknown 表示至少一条目标表达式无法静态求宽。
    bool_unknown = False  # 是否存在无法静态确定的表达式

    # 位宽只在 formatter 已确认的 module 边界内推断。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 当前 module 的端口、声明和常量形成标识符位宽表。
        dict_widths = _module_widths(dict_module)  # 当前 module 的信号位宽映射

        # 右值范围故意限制为定宽字面量或简单标识符。
        str_value = rf"(?:{SIZED_LITERAL}|\w+)"  # 可确定宽度的简单右值模式

        # 关系和赋值语句使用不同捕获组，但共享后续求宽逻辑。
        str_pattern = (  # 当前规则模式及其左右值捕获组
            rf"(?<!['\w])([A-Za-z_]\w*)\s*(==|!=|<|>)\s*({str_value})"  # 关系表达式排除定宽字面量内部的伪标识符
            if relational_only  # True 时使用比较运算符模式
            else rf"(?:assign\s+)?\b(\w+)\s*(?:<=|=)\s*({str_value})\s*;"  # False 时使用赋值模式
        )

        # 每个匹配表达式独立求取左右位宽。
        for obj_match in re.finditer(str_pattern, str_module_text):

            # 至少发现一条目标表达式即标记规则适用。
            bool_applicable = True  # 当前 module 已出现目标表达式

            # 第一捕获组在两种模式中都是左值标识符。
            str_left = obj_match.group(1)  # 当前表达式左侧标识符

            # 关系模式的右值位于第三组，赋值模式位于第二组。
            str_right = obj_match.group(3) if relational_only else obj_match.group(2)  # 当前表达式右侧值

            # 左值宽度来自当前 module 的声明表。
            int_left_width = _expression_width(str_left, dict_widths)  # 左侧表达式位宽

            # 右值可来自声明表或定宽字面量前缀。
            int_right_width = _expression_width(str_right, dict_widths)  # 右侧表达式位宽

            # 任一侧未知时保留不确定状态，避免误报通过。
            if int_left_width is None or int_right_width is None:

                # 后续仍继续收集其他可确定的冲突。
                bool_unknown = True  # 当前目标表达式无法完成静态求宽

                # 当前匹配没有足够事实生成失败证据。
                continue

            # 两侧已知且不同即形成确定违规。
            if int_left_width != int_right_width:

                # 匹配起点结合 module 基线得到一基源码行号。
                int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 位宽冲突所在行

                # finding 保留原始表达式便于直接修复。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,  # 位宽冲突所在 RTL 文件
                        int_line,  # 位宽冲突的一基源码行号
                        "表达式两侧位宽不一致。",  # 固定 VG 诊断文本
                        obj_match.group(0).strip(),  # 原始关系或赋值表达式
                    )
                )

    # 确定冲突优先于其他表达式的不确定状态。
    if list_findings:

        # 返回全部可定位的位宽冲突。
        return failed(*list_findings)

    # 没有确定冲突但存在未知宽度时必须 fail-closed。
    if bool_unknown:

        # 不确定状态明确说明静态证据不足。
        return inconclusive("存在无法静态确定的表达式位宽。")

    # 所有可判断表达式均同宽，或规则不适用。
    return passed(applicable=bool_applicable)

# _module_widths 从 formatter AST 构建当前 module 的简单位宽表。
def _module_widths(dict_module: dict[str, object]) -> dict[str, int | None]:
    """通过共享事实模块提取端口、声明和局部常量的简单位宽。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        标识符到确定位宽或未知值的映射。
    """

    # 共享事实模块是位宽解析的唯一实现来源，避免兼容入口复制分支逻辑。
    return vg_value_facts.module_widths(dict_module)

# _parse_width 解析常量或单参数加减常量的 Verilog 区间。
def _parse_width(str_width: str, dict_parameter_values: dict[str, int]) -> int | None:
    """解析声明区间并计算信号位宽。

    参数:
        str_width: formatter 提供的声明区间文本。
        dict_parameter_values: 可供区间引用的 parameter 整数表。
    返回:
        确定位宽；无法静态解析时返回 None。
    """

    # 标量声明没有区间时按一位处理。
    if not str_width:

        # Verilog 标量默认宽度为一位。
        return 1

    # 捕获区间左右端点，不尝试解析复杂算术表达式。
    obj_match: re.Match[str] | None = re.search(  # 声明区间端点匹配结果
        r"\[\s*([\w+-]+)\s*:\s*([\w+-]+)\s*\]",  # 捕获声明区间的左右端点
        str_width,  # formatter 提供的原始区间文本
    )

    # formatter 未提供受支持区间时保留未知状态。
    if obj_match is None:

        # 上层会把未知宽度转成 inconclusive。
        return None

    # 左端点可为数字、参数或参数加减数字。
    int_left = _constant_integer(obj_match.group(1), dict_parameter_values)  # 区间左端点整数

    # 右端点使用相同受限表达式语法。
    int_right = _constant_integer(obj_match.group(2), dict_parameter_values)  # 区间右端点整数

    # 任一端点未知都会使整个位宽不可确定。
    if int_left is None or int_right is None:

        # 不猜测复杂参数表达式的结果。
        return None

    # Verilog 闭区间位宽等于端点差绝对值加一。
    return abs(int_left - int_right) + 1

# _constant_integer 解析数字或单个参数加减数字。
def _constant_integer(str_expression: str, dict_parameter_values: dict[str, int]) -> int | None:
    """解析受限整数表达式。

    参数:
        str_expression: 数字、参数或参数加减数字文本。
        dict_parameter_values: 已知 parameter 整数表。
    返回:
        确定整数；不支持或未知时返回 None。
    """

    # 受限语法避免对任意 Verilog 表达式做不可靠求值。
    obj_match: re.Match[str] | None = re.fullmatch(  # 受限整数表达式匹配结果
        r"(\w+)(?:([+-])(\d+))?",  # 基值以及可选加减增量捕获组
        str_expression.strip(),  # 去除声明文本两侧空白
    )

    # 不支持的表达式保持未知。
    if obj_match is None:

        # 上层不会把无法解析的表达式当成通过。
        return None

    # 基值可以是十进制数字或已知 parameter。
    str_base = obj_match.group(1)  # 表达式基础数字或参数名

    # 数字直接转换，符号则查询 parameter 表。
    int_value = int(str_base) if str_base.isdigit() else dict_parameter_values.get(str_base)  # 表达式基础整数

    # 基值未知或没有增减项时直接返回当前结果。
    if int_value is None or obj_match.group(2) is None:

        # None 会由调用方传播为未知宽度。
        return int_value

    # 第三捕获组是确定的非负十进制增量。
    int_delta = int(obj_match.group(3))  # 参数表达式的加减增量

    # 第二捕获组决定增量方向。
    return int_value + int_delta if obj_match.group(2) == "+" else int_value - int_delta

# _parameter_integer 读取可安全用于区间求值的十进制 parameter。
def _parameter_integer(str_value: str) -> int | None:
    """读取无符号十进制 parameter 的整数值。

    参数:
        str_value: formatter 提供的 parameter 常量文本。
    返回:
        十进制整数；不支持的常量形式返回 None。
    """

    # 同时接纳裸十进制和显式十进制进制前缀。
    obj_match: re.Match[str] | None = re.fullmatch(  # parameter 十进制常量匹配结果
        r"(?:(?:\d+)'[dD])?(\d+)",  # 裸十进制或显式十进制常量模式
        str_value.strip(),  # 去除常量文本两侧空白
    )

    # 未匹配时不尝试隐式进制或表达式求值。
    return None if obj_match is None else int(obj_match.group(1))

# _constant_width 推断 localparam 的确定结果位宽。
def _constant_width(str_value: str, dict_parameter_values: dict[str, int]) -> int | None:
    """推断定宽字面量或常量重复连接的结果位宽。

    参数:
        str_value: localparam 的原始常量文本。
        dict_parameter_values: 可供重复次数引用的 parameter 表。
    返回:
        确定结果位宽；不支持的形式返回 None。
    """

    # 定宽字面量的前缀直接声明结果位宽。
    obj_literal: re.Match[str] | None = re.fullmatch(  # 定宽字面量匹配结果
        rf"(\d+)'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+",  # 捕获 localparam 字面量的显式位宽
        str_value.strip(),  # 去除 localparam 文本两侧空白
    )

    # 普通定宽字面量无需进一步推断。
    if obj_literal is not None:

        # 第一捕获组就是显式声明的位宽。
        return int(obj_literal.group(1))

    # 重复连接只接纳简单次数和单个定宽字面量。
    obj_repeat: re.Match[str] | None = re.fullmatch(  # 常量重复连接匹配结果
        r"\{(\w+)\{(\d+)'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+\}\}",  # 捕获重复次数和内部位宽
        str_value.strip(),  # 去除重复连接文本两侧空白
    )

    # 其他连接或算术形式保持未知。
    if obj_repeat is None:

        # 上层会把未知 localparam 宽度传播为 inconclusive。
        return None

    # 重复次数可为数字或已知 parameter。
    int_count = _constant_integer(obj_repeat.group(1), dict_parameter_values)  # 重复连接次数

    # 总宽度等于重复次数乘内部字面量位宽。
    return None if int_count is None else int_count * int(obj_repeat.group(2))

# _expression_width 统一读取简单标识符或字面量位宽。
def _expression_width(str_expression: str, dict_widths: dict[str, int | None]) -> int | None:
    """返回简单标识符或定宽字面量的位宽。

    参数:
        str_expression: 位宽规则捕获的简单表达式文本。
        dict_widths: 当前 module 的标识符位宽表。
    返回:
        确定位宽；未知标识符或不支持形式返回 None。
    """

    # 字面量分支直接读取显式位宽前缀。
    obj_literal: re.Match[str] | None = re.fullmatch(  # 简单定宽字面量匹配结果
        rf"(\d+)'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+",  # 捕获简单字面量的显式位宽
        str_expression,  # 位宽规则捕获的原始右值文本
    )

    # 匹配成功时不依赖声明表。
    if obj_literal is not None:

        # 第一捕获组是字面量显式位宽。
        return int(obj_literal.group(1))

    # 其余受支持形式都是当前 module 的简单标识符。
    return dict_widths.get(str_expression)

# 表达式规则继续保留内部函数名，但实际统一使用共享事实实现。
# module_widths 别名让既有表达式调用切换到共享声明事实。
_module_widths = vg_value_facts.module_widths  # 共享 module 位宽入口

# parse_width 别名保留本模块内部调用合同。
_parse_width = vg_value_facts.parse_width  # 共享声明区间解析入口

# constant_integer 别名统一参数区间整数求值语义。
_constant_integer = vg_value_facts.constant_integer  # 共享受限整数解析入口

# parameter_integer 别名统一十进制参数识别语义。
_parameter_integer = vg_value_facts.parameter_integer  # 共享 parameter 整数入口

# constant_width 别名统一 localparam 结果位宽推断。
_constant_width = vg_value_facts.constant_width  # 共享常量宽度入口

# expression_width 别名让既有规则复用扩展后的表达式求宽。
_expression_width = vg_value_facts.expression_width  # 共享表达式位宽入口

# _pattern_gate 在可信 module 边界内执行文本型表达式规则。
def _pattern_gate(facts: VgFacts, str_pattern: str, str_message: str) -> VgEvaluation:
    """扫描指定模式并生成精确行号证据。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        str_pattern: 当前固定规则的正则模式。
        str_message: 命中时写入 finding 的诊断文本。
    返回:
        包含全部命中证据的失败结论或不适用通过结论。
    """

    # findings 保存可信 module 中的全部正则命中。
    list_findings: list[VgFinding] = []  # 当前文本规则的违规证据

    # module 文本排除 formatter 无法确认的顶层噪声。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 多行和大小写选项覆盖常见 Verilog 书写差异。
        for obj_match in re.finditer(str_pattern, str_module_text, flags=re.IGNORECASE | re.MULTILINE):

            # 匹配偏移结合 module 基线得到一基源码行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 当前模式命中行号

            # finding 保留原始匹配片段便于修复。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,  # 违规表达式所在 RTL 文件
                    int_line,  # 当前命中的一基源码行号
                    str_message,  # 当前固定规则的诊断文本
                    obj_match.group(0).strip(),  # 正则命中的原始代码片段
                )
            )

    # 任一确定命中都使固定门禁失败。
    if list_findings:

        # 返回全部命中，避免只修复第一处后重复往返。
        return failed(*list_findings)

    # 没有目标构造时文本规则按不适用通过。
    return passed(applicable=False)
