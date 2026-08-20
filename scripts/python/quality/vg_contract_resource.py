"""解析 packed 资源声明并执行动态查表门禁。"""

# future annotations 让动态 formatter 结构类型可以延迟解析。
from __future__ import annotations

# re 和 typing 支持源码选择表达式与 formatter 动态结构解析。
import re
from typing import Any

# 资源门禁读取共享 RTL 事实和统一评估模型。
from .vg_semantic_facts import VgFacts
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 复用模块枚举、finding 构造和参数表达式计算。
from .vg_contract_parameter import _finding, _source_modules
from .vg_contract_parser import ContractParser, _evaluate_node, _module_parameter_values

# _packed_width 解析 parameter/localparam 的 packed 声明宽度。
def _packed_width(dict_declaration: dict[str, Any], values: dict[str, int], source_line: str) -> int | None:
    """返回 packed 声明的位数，无法确定时返回 None。

    参数:
        dict_declaration: formatter 参数或 localparam 声明事实。
        values: 当前模块可确定的参数环境。
        source_line: 声明所在源码行。
    返回:
        packed 位宽；边界未知或非 packed 时返回 None。
    """

    # 优先使用 formatter 保留的声明规格和源码行。
    str_declaration = " ".join(  # 合并声明规格和源码行用于宽度解析。
        str(dict_declaration.get(str_key) or "")  # 当前声明可用的文本片段
        for str_key in ("decl_spec", "width", "unpacked")  # formatter 可能使用的宽度字段
    ) + " " + source_line  # 合并声明事实和源码证据

    # 只识别普通 [left:right] packed 范围。
    obj_match = re.search(r"\[\s*([^:\]]+)\s*:\s*([^\]]+)\s*\]", str_declaration)  # 提取 packed 范围的左右边界。

    # 没有 packed 范围时不触发大表规则。
    if obj_match is None:

        # 无范围可能是标量或无法从 formatter 中恢复。
        return None

    # 受限 parser 求出左右边界。
    try:

        # 左边界按当前 parameter 环境求值。
        tuple_left, _ = ContractParser(obj_match.group(1)).parse()  # packed 左边界节点

        # 计算 packed 左边界，确定存储范围起点。
        int_left = _evaluate_node(tuple_left, values)  # packed 左边界数值

        # 右边界按当前 parameter 环境求值。
        tuple_right, _ = ContractParser(obj_match.group(2)).parse()  # packed 右边界节点

        # 计算 packed 右边界，确定存储范围终点。
        int_right = _evaluate_node(tuple_right, values)  # packed 右边界数值

    # 非法或未知边界保持不确定。
    except (TypeError, ValueError):

        # 调用方会把未知宽度作为 inconclusive 证据。
        return None

    # 任一边界未知都不能产生可靠宽度。
    if int_left is None or int_right is None:

        # 交由资源规则 fail closed。
        return None

    # Verilog 范围宽度是两端差值的绝对值加一。
    return abs(int_left - int_right) + 1

# _dynamic_selectors 查找对指定声明的运行时选择表达式。
def _dynamic_selectors(name: str, lines: tuple[str, ...]) -> tuple[tuple[int, str], ...]:
    """返回指定常量的动态 bit/part-select 位置。

    参数:
        name: packed 参数或 localparam 名称。
        lines: 当前源码文件的物理行元组。
    返回:
        动态选择的行号和选择表达式元组。
    """

    # 选择表达式只保留当前声明名称之后的方括号内容。
    pattern_selector: re.Pattern[str] = re.compile(rf"\b{re.escape(name)}\s*\[([^\]]+)\]")  # 当前声明的选择模式

    # 收集非纯常量选择，避免把常量切片当作运行时存储。
    list_selectors: list[tuple[int, str]] = []  # 动态选择位置和文本

    # 每行单独扫描以保留 formatter 源码行定位。
    for int_line, str_line in enumerate(lines, start=1):

        # 去掉行尾注释，避免注释中的表名触发资源规则。
        str_code = str_line.split("//", 1)[0]  # 当前行的可执行文本

        # 一个行内可以包含多个选择表达式。
        for obj_match in pattern_selector.finditer(str_code):

            # 只要选择器含普通标识符或运行时算术就视为动态。
            str_selector = obj_match.group(1).strip()  # 当前选择表达式

            # 纯数值和 Verilog 数字字面量属于静态选择。
            if re.fullmatch(r"[0-9'\s_:+\-]+", str_selector):

                # 静态切片不属于 packed 动态查表。
                continue

            # 保存动态选择证据。
            list_selectors.append((int_line, str_selector))

    # 返回稳定的源码顺序选择列表。
    return tuple(list_selectors)

# evaluate_resource_gate 执行 VG152 的访问形态与资源结构检查。
def evaluate_resource_gate(facts: VgFacts, int_limit: int) -> VgEvaluation:
    """阻止超大 packed 参数被用作运行时动态存储。

    参数:
        facts: 当前 RTL 的共享 formatter 事实。
        int_limit: 目录提供的 packed 位宽阻断阈值。
    返回:
        VG152 的通过、失败或不确定结论。
    """

    # 收集确定违规和不确定宽度证据。
    list_findings: list[VgFinding] = []  # VG152 资源结构证据

    # 动态 packed 访问存在时进入资源结构适用范围。
    bool_applicable = False  # 当前 RTL 是否出现动态 packed 访问

    # 逐个 source/module 扫描 packed parameter/localparam。
    for obj_source, dict_module in _source_modules(facts):

        # 当前模块的可求值参数环境供范围端点使用。
        tuple_parameter_values, _ = _module_parameter_values(dict_module)  # 当前模块的整数环境

        # parameter、localparam 和实际 signal declaration 都可能承载大 packed 查表。
        for dict_declaration in (
        *(dict_module.get("params", []) or []),
        *(dict_module.get("localparams", []) or []),
        *(dict_module.get("decls", []) or []),
        ):

            # 声明名称缺失时跳过，解析错误由 formatter 规则处理。
            str_name = str(dict_declaration.get("name") or "")  # 当前常量名称

            # 未知名称无法安全扫描动态选择。
            if not str_name:

                # 继续检查其他声明。
                continue

            # 取声明行作为宽度和定位的补充事实。
            int_decl_line = int(dict_declaration.get("line_start") or 1)  # 声明起始行

            # 保存声明源码行，补足 formatter 缺失的宽度文本。
            str_decl_line = obj_source.lines[int_decl_line - 1] if int_decl_line <= len(obj_source.lines) else ""  # 声明源码行

            # unpacked memory 不是本规则的 packed 参数对象。
            bool_metadata_unpacked = bool(dict_declaration.get("unpacked"))  # formatter 明确的 unpacked 维度

            # 补足声明名称后的源码范围证据。
            obj_source_unpacked = re.search(rf"\b{re.escape(str_name)}\s*\[", str_decl_line)  # 源码 memory 范围

            # 合并 formatter 和源码两类 memory 事实。
            bool_source_unpacked = obj_source_unpacked is not None  # 源码存在 unpacked 范围

            # 任一来源确认 memory 即跳过 packed 资源判定。
            bool_unpacked = bool_metadata_unpacked or bool_source_unpacked  # 当前 declaration 的 memory 状态

            # 已确认 unpacked 时退出当前声明的 packed 检查。
            if bool_unpacked:

                # 保留推断 memory 的合法结构。
                continue

            # 先解析 packed 宽度，再查找运行时选择。
            int_width = _packed_width(dict_declaration, tuple_parameter_values, str_decl_line)  # 当前 packed 位宽

            # 保存动态选择位置，供 finding 保留源码行号。
            tuple_selectors = _dynamic_selectors(str_name, obj_source.lines)  # 当前常量的动态选择

            # 没有动态选择时不形成资源结构适用对象。
            if not tuple_selectors:

                # 只报告真正被运行时寻址的 packed 常量。
                continue

            # 动态 packed 选择本身已经进入规则适用范围。
            bool_applicable = True  # 当前声明确实存在运行时动态选择。

            # 宽度未知时必须保留不确定证据。
            if int_width is None:

                # 无法确定阈值关系时严格模式不能放行。
                list_findings.append(
                    _finding(
                        obj_source,
                        dict_module,
                        line=tuple_selectors[0][0],
                        message="packed 动态查表的存储宽度无法确定。",
                        evidence=str_name,
                        metadata=(
                            ("width", None),
                            ("selector", "dynamic"),
                            ("selector_expression", tuple_selectors[0][1]),
                            ("reason", "unknown_width"),
                        ),
                    )
                )

                # 跳过未知宽度的选择，保留前一条不确定证据。
                continue

            # 小于门槛的 packed 访问交给既有结构规则。
            if int_width < int_limit:

                # 阈值以下不强制改变实现结构。
                continue

            # 大型 packed 动态查表必须改成资源结构可识别的实现。
            for int_line, str_selector in tuple_selectors:

                # 每个运行时选择保留独立证据，方便逐项修复。
                list_findings.append(
                    _finding(
                        obj_source,
                        dict_module,
                        line=int_line,
                        message="超大 packed 参数被用于运行时动态查表，应改用结构化译码或 memory/ROM。",
                        evidence=f"{str_name}[{str_selector}]",
                        metadata=(
                            ("width", int_width),
                            ("threshold", int_limit),
                            ("selector", "dynamic"),
                            ("selector_expression", str_selector),
                            ("resource_class", "packed_dynamic_storage"),
                            ("alternatives", ["case_or_fsm", "inferred_memory", "vendor_memory_primitive"]),
                        ),
                    )
                )

    # 不确定宽度使用 inconclusive，确定超阈值结构使用 failed。
    if list_findings:

        # unknown_width 是唯一的结构不确定原因。
        bool_unknown = any(  # 判断 finding 中是否存在未知宽度原因。
            any(  # 在 metadata 中确认未知宽度原因。
                key == "reason"  # 读取 finding 的原因键。
                and value == "unknown_width"  # 只捕获宽度未知原因
                for key, value in obj_finding.metadata  # 遍历宽度 finding metadata
            )
            for obj_finding in list_findings  # 遍历全部资源宽度 finding
        )  # 是否存在宽度不确定证据

        # 不确定证据保持严格门禁的阻断语义。
        if bool_unknown:

            # 返回 fail-closed 的 inconclusive 结果。
            return inconclusive("packed 动态查表资源形态无法完全确定。", *list_findings)

        # 超阈值 packed 动态存储是确定违规。
        return failed(*list_findings)

    # 无动态选择时不适用；其余动态小表通过。
    return passed(applicable=bool_applicable)

