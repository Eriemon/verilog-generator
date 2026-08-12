"""实现分支、case 和互斥赋值路径相关 RTL VG 门禁。"""

# future annotations 延后解析规则模型类型。
from __future__ import annotations

# re 仅在 formatter 已确认的控制节点文本内提取标识符和简单左值。
import re

# Callable 与 Iterator 描述固定路由表和递归节点迭代器。
from typing import Any, Callable, Iterator

# facts 提供 formatter AST 确认的 module 边界和源码定位上下文。
from .vg_semantic_facts import VgFacts, iter_trusted_modules

# models 统一逐门禁结论和定位证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# value_facts 提供分支规则共享的常量解析和位宽事实。
from .vg_value_facts import (
    ConstantBits,
    expression_width,
    module_constant_values,
    module_widths,
    resolve_constant_bits,
)

# evaluate_branch_gate 把七个固定编号路由到唯一分支规则实现。
def evaluate_branch_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行分支规则组中的指定固定 VG 门禁。

    参数:
        str_gate_id: 当前执行的固定 VG 分支门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前分支规则的逐门禁结论。
    """

    # 路由表集中声明第二批七条规则的唯一执行函数。
    dict_evaluators: dict[str, Callable[[VgFacts], VgEvaluation]] = {  # 固定编号到规则函数的映射
        "VG076": _case_no_overlap,  # case 标签重叠检查
        "VG084": _case_item_in_range_width,  # case 标签位宽检查
        "VG088": _branch_condition_scalar,  # if 条件单位宽检查
        "VG090": _case_control_not_constant,  # case 控制项常量检查
        "VG103": _assign_no_duplicate_condition,  # 同路径重复赋值检查
        "VG105": _case_item_constant_only,  # case 标签常量检查
        "VG109": _combinational_if_has_else,  # 组合 if 终止 else 检查
    }

    # engine 已保证编号属于分支模块，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _case_no_overlap 比较同一 case 内可静态解析的标签模式。
def _case_no_overlap(facts: VgFacts) -> VgEvaluation:
    """检查同一 case 中可解析标签是否形成重叠。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG076 的通过、失败或不确定结论。
    """

    # findings 汇总全部可确定的标签重叠位置。
    list_findings: list[VgFinding] = []  # case 标签重叠证据

    # applicable 区分没有 case 和完成过 case 分析。
    bool_applicable = False  # 是否发现 case 控制节点

    # unknown 记录至少一个无法解析的标签。
    bool_unknown = False  # 是否存在未知 case 标签

    # 每个 module 独立解析常量符号，避免跨作用域串扰。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 当前 module 的 parameter 与 localparam 构成标签解析环境。
        dict_constants = module_constant_values(dict_module)  # 当前 module 常量原文表

        # formatter 控制树是发现 case 结构的唯一来源。
        for dict_case in _iter_module_nodes(dict_module, "case"):

            # 出现 case 即表明规则对当前设计适用。
            bool_applicable = True  # 当前 module 包含待检查 case

            # 单个 case 的标签展开和逐对比较交给浅层 helper 完成。
            tuple_case_findings, tuple_bool_case_unknown = _case_overlap_findings(  # 当前 case 的重叠分析结果
                source_facts.relative_path,  # 当前 RTL 文件相对路径
                str_module_text,  # 当前可信 module 文本
                int_base_line,  # 当前 module 一基起始行
                dict_case,  # 当前 formatter case 节点
                dict_constants,  # 当前 module 常量解析环境
            )  # 当前 case 的重叠证据与未知标记

            # 当前 case 的确定冲突并入跨 module 汇总。
            list_findings.extend(tuple_case_findings)

            # 任一 case 存在未知标签都会传播不确定状态。
            bool_unknown = bool_unknown or tuple_bool_case_unknown  # 汇总 case 标签解析状态

    # 统一应用确定失败优先于未知事实的状态顺序。
    return _finish(list_findings, bool_applicable, bool_unknown, "存在无法静态解析的 case 标签。")

# _case_overlap_findings 在单个 case 内展开标签并收集重叠证据。
def _case_overlap_findings(
    str_path: str,
    str_module_text: str,
    int_base_line: int,
    dict_case: dict[str, Any],
    dict_constants: dict[str, str],
) -> tuple[list[VgFinding], bool]:
    """返回单个 case 的重叠证据和未知标签标记。

    参数:
        str_path: 当前 RTL 文件的稳定相对路径。
        str_module_text: formatter 已确认边界的 module 文本。
        int_base_line: module 文本的一基起始行。
        dict_case: 当前 formatter case 控制节点。
        dict_constants: 当前 module 的常量原文表。
    返回:
        当前 case 的重叠 finding 列表和未知标签布尔值。
    """

    # case 类别决定未知态字符的通配规则。
    str_case_kind = _case_kind(str(dict_case.get("header") or ""))  # 当前标签比较采用的 case 类别

    # patterns 保留已解析标签供后续标签逐一比较。
    list_patterns: list[tuple[str, ConstantBits]] = []  # 当前 case 的已知标签模式

    # findings 保存当前 case 内的全部确定重叠。
    list_findings: list[VgFinding] = []  # 当前 case 的标签重叠证据

    # unknown 标记至少一个标签无法解析。
    bool_unknown = False  # 当前 case 是否含未知标签

    # item 标签按 formatter 源码顺序展开。
    for dict_item in dict_case.get("items", []) or []:

        # 同一 item 的并列标签按顶层逗号拆分。
        for str_label in _split_case_labels(str(dict_item.get("label") or "")):

            # default 不参与普通标签重叠比较。
            if str_label.lower() == "default":

                # 继续处理显式标签。
                continue

            # 当前标签只接受字面量或已知常量符号。
            constant_bits: ConstantBits | None = resolve_constant_bits(str_label, dict_constants)  # 当前标签的规范比特模式

            # 无法解析的标签只产生未知事实。
            if constant_bits is None:

                # 确定冲突仍可覆盖该未知事实。
                bool_unknown = True  # 当前 case 出现未知标签

                # 未知模式不能参与逐位比较。
                continue

            # 当前已知模式与此前标签逐对比较。
            for str_previous, previous_bits in list_patterns:

                # 存在共同匹配值时登记后出现的标签。
                if _patterns_overlap(previous_bits, constant_bits, str_case_kind):

                    # finding 保留冲突标签对和准确源码位置。
                    list_findings.append(
                        _finding(
                            str_path,
                            str_module_text,
                            int_base_line,
                            str_label,
                            "case 分支标签存在重叠。",
                            f"{str_previous} <-> {str_label}",
                        )
                    )

            # 当前标签加入后续比较集合。
            list_patterns.append((str_label, constant_bits))

    # 调用方统一决定失败和不确定状态优先级。
    return list_findings, bool_unknown

# _case_item_in_range_width 比较控制项和每个可解析标签的位宽。
def _case_item_in_range_width(facts: VgFacts) -> VgEvaluation:
    """检查 case 标签与控制表达式的可判定位宽。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG084 的通过、失败或不确定结论。
    """

    # findings 保存全部可定位的位宽不一致标签。
    list_findings: list[VgFinding] = []  # case 标签位宽冲突证据

    # applicable 标记至少发现一个 case 控制节点。
    bool_applicable = False  # 是否存在 case 位宽检查对象

    # unknown 表示控制项或标签至少一侧无法静态求宽。
    bool_unknown = False  # 是否存在未知 case 位宽

    # 位宽和常量事实都限定在 formatter 确认的 module 内。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 声明、端口和 localparam 构成表达式位宽表。
        dict_widths = module_widths(dict_module)  # 当前 module 的信号位宽表

        # 常量原文表用于解析 case item 符号。
        dict_constants = module_constant_values(dict_module)  # 当前 module 的常量符号表

        # 每个 formatter case 节点独立比较自己的控制项与标签。
        for dict_case in _iter_module_nodes(dict_module, "case"):

            # 出现 case 即表明规则具有适用结构。
            bool_applicable = True  # 当前 module 包含待求宽 case

            # 头部括号内容是 case 控制表达式。
            str_selector = _header_expression(str(dict_case.get("header") or ""))  # 当前 case 控制表达式

            # 控制项宽度来自共享表达式位宽事实。
            int_selector_width = expression_width(str_selector, dict_widths)  # 当前 case 控制项位宽

            # 控制项无法求宽时保留未知状态并继续检查标签事实。
            if int_selector_width is None:

                # 其他 case 仍可能形成确定违规。
                bool_unknown = True  # 当前控制项位宽未知

            # default 之外的每个标签都必须与控制项同宽。
            for dict_item in dict_case.get("items", []) or []:

                # 支持单个 case item 携带多个并列标签。
                for str_label in _split_case_labels(str(dict_item.get("label") or "")):

                    # default 没有常量位宽比较语义。
                    if str_label.lower() == "default":

                        # 继续检查同一 case 的显式标签。
                        continue

                    # 标签解析结果同时给出显式宽度和规范比特模式。
                    constant_bits: ConstantBits | None = resolve_constant_bits(str_label, dict_constants)  # 当前标签的位宽与比特事实

                    # 任一侧未知时不能伪造位宽一致结论。
                    if int_selector_width is None or constant_bits is None:

                        # 没有确定违规时规则将返回 inconclusive。
                        bool_unknown = True  # 当前标签比较事实不完整

                        # 跳过缺少可靠宽度的当前标签。
                        continue

                    # 已知宽度不同即形成确定违规。
                    if constant_bits.width != int_selector_width:

                        # 证据同时给出控制项和标签的静态宽度。
                        list_findings.append(
                            _finding(
                                source_facts.relative_path,
                                str_module_text,
                                int_base_line,
                                str_label,
                                "case 分支标签与控制表达式位宽不一致。",
                                f"selector={int_selector_width}, item={constant_bits.width}: {str_label}",
                            )
                        )

    # 确定冲突优先，不完整事实只在无冲突时形成不确定结论。
    return _finish(list_findings, bool_applicable, bool_unknown, "存在无法静态确定的 case 控制项或标签位宽。")

# _branch_condition_scalar 对 formatter 识别的 if 条件执行单位宽判断。
def _branch_condition_scalar(facts: VgFacts) -> VgEvaluation:
    """检查 if 条件是否可确定为单位宽表达式。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG088 的通过、失败或不确定结论。
    """

    # findings 保存全部可确定的向量条件位置。
    list_findings: list[VgFinding] = []  # 非单位宽条件证据

    # applicable 标记至少出现一个 if 节点。
    bool_applicable = False  # 是否存在分支条件检查对象

    # unknown 表示某个条件超出共享求宽能力。
    bool_unknown = False  # 是否存在未知条件位宽

    # 每个 module 使用自己的声明位宽环境。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 信号位宽表支持标识符、位选和简单布尔表达式。
        dict_widths = module_widths(dict_module)  # 当前 module 的条件位宽表

        # formatter 控制树提供全部嵌套 if 节点。
        for dict_if in _iter_module_nodes(dict_module, "if"):

            # 出现 if 即表明规则适用。
            bool_applicable = True  # 当前 module 包含待检查条件

            # 条件文本取自 if 头部的完整外层括号。
            str_condition = _header_expression(str(dict_if.get("header") or ""))  # 当前 if 条件表达式

            # 共享位宽推断只对高置信表达式返回确定值。
            int_width = expression_width(str_condition, dict_widths)  # 当前条件的静态位宽

            # 不支持的函数调用或复杂运算保持未知。
            if int_width is None:

                # 继续检查其他条件，避免漏掉确定向量违规。
                bool_unknown = True  # 当前条件位宽无法确定

            # 确定的多位条件违反单位宽指导。
            elif int_width != 1:

                # finding 定位 if 头并保留原始条件文本。
                list_findings.append(
                    _finding(
                        source_facts.relative_path,
                        str_module_text,
                        int_base_line,
                        str(dict_if.get("header") or str_condition),
                        "分支条件不是单位宽表达式。",
                        str_condition,
                    )
                )

    # 已知向量条件失败，纯未知条件则保持 fail-closed。
    return _finish(list_findings, bool_applicable, bool_unknown, "存在无法静态确定宽度的分支条件。")

# _case_control_not_constant 区分纯常量控制项、运行时信号和未知符号。
def _case_control_not_constant(facts: VgFacts) -> VgEvaluation:
    """禁止 case 控制表达式完全由常量组成。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG090 的通过、失败或不确定结论。
    """

    # findings 保存纯常量 case 控制项。
    list_findings: list[VgFinding] = []  # 固定 case 控制项证据

    # applicable 标记至少发现一个 case 节点。
    bool_applicable = False  # 是否存在 case 控制项检查对象

    # unknown 表示控制项含未声明符号或无法区分来源。
    bool_unknown = False  # 是否存在未知控制项来源

    # 每个 module 独立维护运行时信号和常量符号集合。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 声明位宽表的键代表当前作用域已知运行时信号。
        dict_widths = module_widths(dict_module)  # 当前 module 的运行时信号表

        # 常量表用于判定控制项能否完全静态求值。
        dict_constants = module_constant_values(dict_module)  # 当前 module 的控制项常量环境

        # 所有嵌套 case 都使用相同来源判定顺序。
        for dict_case in _iter_module_nodes(dict_module, "case"):

            # 当前节点进入控制项来源分类范围。
            bool_applicable = True  # 已发现需要排除纯常量的 case

            # 头部表达式用于区分静态常量和运行时来源。
            str_selector = _header_expression(str(dict_case.get("header") or ""))  # 当前待分类的 case 选择表达式

            # 完全可解析为常量时形成确定失败。
            if resolve_constant_bits(str_selector, dict_constants) is not None:

                # finding 保留控制项原文供直接修复。
                list_findings.append(
                    _finding(
                        source_facts.relative_path,
                        str_module_text,
                        int_base_line,
                        str(dict_case.get("header") or str_selector),
                        "case 控制表达式使用了固定值。",
                        str_selector,
                    )
                )

                # 当前控制项已有确定结论，无需再做符号来源分类。
                continue

            # 标识符集合用于识别运行时信号、常量和未知名称。
            set_identifiers = set(re.findall(r"\b[A-Za-z_]\w*\b", str_selector))  # 控制项引用的全部标识符

            # 既非信号也非常量的名称会破坏确定性判断。
            set_unknown = set_identifiers - set(dict_widths) - set(dict_constants)  # 当前控制项的未知标识符

            # 未知名称或完全没有运行时信号时不能确认合规。
            if set_unknown or not (set_identifiers & set(dict_widths)):

                # 其他 case 的确定失败仍保持更高优先级。
                bool_unknown = True  # 当前控制项来源无法完整分类

    # 纯常量控制项失败，其余未知来源保持 inconclusive。
    return _finish(list_findings, bool_applicable, bool_unknown, "存在无法区分运行时信号与常量的 case 控制表达式。")

# _assign_no_duplicate_condition 在 formatter 控制路径内检查重复简单左值。
def _assign_no_duplicate_condition(facts: VgFacts) -> VgEvaluation:
    """检查同一控制路径是否重复写入同一简单左值。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG103 的通过、失败或不确定结论。
    """

    # findings 保存同一控制路径上的第二次赋值位置。
    list_findings: list[VgFinding] = []  # 重复过程赋值证据

    # applicable 标记至少有一个可分析控制树。
    bool_applicable = False  # 是否存在过程控制节点

    # unknown 表示赋值左值形状超出简单标识符支持范围。
    bool_unknown = False  # 是否存在复杂过程左值

    # 重复路径只在各自 module 和 always 内独立计算。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 每个 always 的根节点代表独立过程驱动上下文。
        for dict_always in dict_module.get("always", []) or []:

            # nodes 是 formatter 公开的递归控制树。
            list_nodes = list(dict_always.get("nodes", []) or [])  # 当前 always 的顶层控制节点

            # 空控制树没有可靠路径事实。
            if not list_nodes:

                # 继续检查同一 module 的其他 always。
                continue

            # 至少一个非空控制树使规则适用。
            bool_applicable = True  # 当前 always 可执行路径分析

            # helper 返回重复项和复杂左值不确定标记。
            tuple_duplicates, tuple_bool_path_unknown = _duplicate_assignments(list_nodes)  # 当前 always 的路径分析结果

            # 任一复杂左值都会保留 fail-closed 状态。
            bool_unknown = bool_unknown or tuple_bool_path_unknown  # 汇总所有 always 的未知左值

            # 每个重复项包含规范左值和后出现的赋值语句。
            for str_lvalue, str_statement in tuple_duplicates:

                # finding 定位第二次赋值并把信号名作为最小证据。
                list_findings.append(
                    _finding(
                        source_facts.relative_path,
                        str_module_text,
                        int_base_line,
                        str_statement,
                        "同一控制路径重复写入同一信号。",
                        str_lvalue,
                    )
                )

    # 确定重复优先，复杂左值只在无重复时形成不确定结论。
    return _finish(list_findings, bool_applicable, bool_unknown, "存在无法静态识别的过程赋值左值。")

# _case_item_constant_only 判断标签是常量、运行时表达式还是未知符号。
def _case_item_constant_only(facts: VgFacts) -> VgEvaluation:
    """检查 case 标签是否仅使用字面量或已知常量符号。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG105 的通过、失败或不确定结论。
    """

    # findings 保存变量或逻辑表达式标签。
    list_findings: list[VgFinding] = []  # 非常量 case 标签证据

    # applicable 专门记录标签常量规则是否见到目标结构。
    bool_applicable = False  # 是否存在 case 标签检查对象

    # unknown 表示标签名称既未声明为信号也未声明为常量。
    bool_unknown = False  # 是否存在来源未知的标签

    # 标签来源只在当前 formatter module 作用域内解析。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # width 表中的键代表已声明运行时信号。
        dict_widths = module_widths(dict_module)  # 当前 module 的信号声明表

        # constants 提供 parameter 与 localparam 的可解析原文。
        dict_constants = module_constant_values(dict_module)  # 当前 module 的常量声明表

        # 递归遍历所有 always 内的 case 控制节点。
        for dict_case in _iter_module_nodes(dict_module, "case"):

            # 任一 case 都使标签常量规则适用。
            bool_applicable = True  # 当前 module 包含待检查标签

            # 每个 item 的标签列表独立分类。
            for dict_item in dict_case.get("items", []) or []:

                # 并列标签按顶层逗号展开。
                for str_label in _split_case_labels(str(dict_item.get("label") or "")):

                    # default 和可解析常量都满足当前规则。
                    if str_label.lower() == "default" or resolve_constant_bits(str_label, dict_constants) is not None:

                        # 继续处理同一 case 的其余标签。
                        continue

                    # 未解析标签中的标识符用于区分变量和未知符号。
                    set_identifiers = set(re.findall(r"\b[A-Za-z_]\w*\b", str_label))  # 当前标签引用的标识符

                    # 声明信号或明确运算符都证明标签不是纯常量。
                    if set_identifiers & set(dict_widths) or re.search(r"[(){}+*/%&|^~<>]", str_label):

                        # finding 保留完整标签表达式。
                        list_findings.append(
                            _finding(
                                source_facts.relative_path,
                                str_module_text,
                                int_base_line,
                                str_label,
                                "case 分支标签使用了变量或逻辑表达式。",
                                str_label,
                            )
                        )

                    # 未声明名称不应被误报为确定变量违规。
                    else:

                        # 无确定违规时返回 inconclusive。
                        bool_unknown = True  # 当前标签符号来源未知

    # 非常量表达式失败，未声明符号保持不确定。
    return _finish(list_findings, bool_applicable, bool_unknown, "存在无法静态解析的 case 分支标签。")

# _combinational_if_has_else 检查组合过程内每条 if 链的终止分支。
def _combinational_if_has_else(facts: VgFacts) -> VgEvaluation:
    """检查组合 always 中每条 if 链是否具有终止 else。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG109 的通过或失败结论。
    """

    # findings 保存缺少终止 else 的组合 if 头部。
    list_findings: list[VgFinding] = []  # 组合 if 链不完整证据

    # applicable 标记至少发现一个组合 if 节点。
    bool_applicable = False  # 是否存在组合 if 检查对象

    # 只消费 formatter 明确认定的 module 和组合 always。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 每个 always 先由 formatter 的组合属性筛选。
        for dict_always in dict_module.get("always", []) or []:

            # 时序过程不适用组合 else 指导。
            if not bool(dict_always.get("is_combinational")):

                # 继续检查同一 module 的其他过程块。
                continue

            # 递归遍历组合过程中的全部 if 节点。
            for dict_if in _iter_nodes(list(dict_always.get("nodes", []) or []), "if"):

                # 出现组合 if 即表明规则适用。
                bool_applicable = True  # 当前 always 包含组合 if

                # 完整终止 else 的链无需生成 finding。
                if _if_chain_has_terminal_else(dict_if):

                    # 继续检查嵌套的其他 if 节点。
                    continue

                # 头部文本同时用于定位和报告证据。
                str_header = str(dict_if.get("header") or "if")  # 当前缺失终止 else 的 if 头

                # finding 定位不完整 if 链的起点。
                list_findings.append(
                    _finding(
                        source_facts.relative_path,
                        str_module_text,
                        int_base_line,
                        str_header,
                        "组合逻辑 if 链缺少终止 else。",
                        str_header,
                    )
                )

    # 缺少 else 时失败，否则保留实际适用性。
    if list_findings:

        # 返回全部不完整组合 if 链。
        return failed(*list_findings)

    # 没有违规时区分无组合 if 和已确认完整结构。
    return passed(applicable=bool_applicable)

# _iter_module_nodes 从每个 always 根节点递归筛选目标种类。
def _iter_module_nodes(dict_module: dict[str, object], str_kind: str) -> Iterator[dict[str, Any]]:
    """遍历 module 全部 always 控制树中的指定节点。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
        str_kind: 需要筛选的控制节点种类。
    返回:
        依次产生匹配种类的控制节点字典。
    """

    # always 顺序沿用 formatter 报告的稳定源码顺序。
    for dict_always in dict_module.get("always", []) or []:

        # 递归 helper 同时覆盖主路径、备选路径和 case item。
        yield from _iter_nodes(list(dict_always.get("nodes", []) or []), str_kind)

# _iter_nodes 深度优先遍历 formatter 控制树的所有分支容器。
def _iter_nodes(list_nodes: list[dict[str, Any]], str_kind: str) -> Iterator[dict[str, Any]]:
    """深度优先遍历控制树、备选路径和 case item。

    参数:
        list_nodes: 当前层的 formatter 控制节点列表。
        str_kind: 需要筛选的控制节点种类。
    返回:
        依次产生当前子树中匹配种类的节点。
    """

    # 当前层节点按 formatter 原始顺序遍历。
    for dict_node in list_nodes:

        # 节点种类命中时先产出当前节点。
        if dict_node.get("kind") == str_kind:

            # 调用方获得原始字典且不得修改共享事实。
            yield dict_node

        # 主路径递归覆盖普通容器和 if 真分支。
        yield from _iter_nodes(list(dict_node.get("children", []) or []), str_kind)

        # alternate 递归覆盖 else 与 else-if 路径。
        yield from _iter_nodes(list(dict_node.get("alternate", []) or []), str_kind)

        # case item 分支各自递归，保持互斥路径身份。
        for dict_item in dict_node.get("items", []) or []:

            # 当前 item 的 children 属于独立 case 路径。
            yield from _iter_nodes(list(dict_item.get("children", []) or []), str_kind)

# _duplicate_assignments 在单条顺序路径内登记简单左值。
def _duplicate_assignments(list_nodes: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], bool]:
    """返回当前控制子树的同路径重复赋值和未知左值标记。

    参数:
        list_nodes: 当前互斥控制路径上的节点列表。
    返回:
        重复左值及语句列表，以及是否遇到复杂左值。
    """

    # seen 保存当前顺序路径第一次出现的简单左值。
    dict_seen: dict[str, str] = {}  # 简单左值到首次赋值语句的映射

    # duplicates 保存第二次及后续同路径写入。
    list_duplicates: list[tuple[str, str]] = []  # 当前子树的重复赋值

    # unknown 标记连接等复杂左值无法可靠归一化。
    bool_unknown = False  # 当前子树是否包含复杂赋值左值

    # 同层 statement 共享路径，控制节点的子分支分别递归。
    for dict_node in list_nodes:

        # kind 决定当前节点是否包含直接赋值文本。
        str_kind = str(dict_node.get("kind") or "")  # 当前控制节点种类

        # 只有 statement 节点可能在当前路径直接写入左值。
        if str_kind == "statement":

            # formatter statement 文本保持原始过程语句。
            str_statement = str(dict_node.get("text") or "")  # 当前过程语句文本

            # 没有赋值运算符的语句不参与重复驱动检查。
            if not re.search(r"(?:<=|=)", str_statement):

                # 继续处理同层后续节点。
                continue

            # 简单左值允许标识符及其位选或范围选择。
            obj_match = re.match(r"\s*([A-Za-z_]\w*)(?:\s*\[[^\]]+\])?\s*(?:<=|=)", str_statement)  # 简单左值匹配结果

            # 连接、层级名等复杂形状保持未知。
            if obj_match is None:

                # 无确定重复时上层返回 inconclusive。
                bool_unknown = True  # 当前赋值左值无法归一化

                # 当前语句没有可靠左值键。
                continue

            # 基础标识符用于合并同一信号的位选赋值。
            str_lvalue = obj_match.group(1)  # 当前赋值的规范基础信号名

            # 已登记信号再次出现在同层路径即为重复赋值。
            if str_lvalue in dict_seen:

                # 保留第二次赋值文本作为定位依据。
                list_duplicates.append((str_lvalue, str_statement))

            # 首次出现只登记路径所有权。
            else:

                # 后续同层 statement 将与该记录比较。
                dict_seen[str_lvalue] = str_statement  # 当前路径首次赋值语句

        # 控制节点的互斥子路径分别递归，不与同层登记表合并。
        for list_child_nodes in _exclusive_child_paths(dict_node):

            # 子路径分析返回自己的重复项与未知标记。
            tuple_child_duplicates, tuple_bool_child_unknown = _duplicate_assignments(list_child_nodes)  # 当前互斥子路径结果

            # 子路径重复证据并入当前子树结果。
            list_duplicates.extend(tuple_child_duplicates)

            # 任一子路径复杂左值都会传播不确定状态。
            bool_unknown = bool_unknown or tuple_bool_child_unknown  # 汇总子路径未知左值

    # 返回当前子树全部重复项和未知事实。
    return list_duplicates, bool_unknown

# _exclusive_child_paths 把控制节点展开为互不合并的路径列表。
def _exclusive_child_paths(dict_node: dict[str, Any]) -> Iterator[list[dict[str, Any]]]:
    """把 if、case 和普通容器展开为互相独立的控制路径。

    参数:
        dict_node: 当前 formatter 控制节点。
    返回:
        依次产生需要独立分析的子节点路径。
    """

    # case 的每个 item 都是独立互斥路径。
    if str(dict_node.get("kind") or "") == "case":

        # item 顺序保持源码顺序但不共享赋值登记表。
        for dict_item in dict_node.get("items", []) or []:

            # 当前 case item 的节点形成单独路径。
            yield list(dict_item.get("children", []) or [])

        # case 路径已经完整展开，不再消费通用 children。
        return

    # 普通 children 代表当前节点的主路径。
    list_children = list(dict_node.get("children", []) or [])  # 当前节点主路径子节点

    # 非空主路径单独递归。
    if list_children:

        # 主路径与 alternate 不共享同层赋值记录。
        yield list_children

    # alternate 代表 if 的互斥备选路径。
    list_alternate = list(dict_node.get("alternate", []) or [])  # 当前节点备选路径子节点

    # 非空备选路径独立递归。
    if list_alternate:

        # 备选路径保持 formatter 的 else 包装节点。
        yield list_alternate

# _if_chain_has_terminal_else 递归确认 else-if 链最终落到普通 else。
def _if_chain_has_terminal_else(dict_if: dict[str, Any]) -> bool:
    """判断 if 的 alternate 是否存在最终非空 else 路径。

    参数:
        dict_if: formatter AST 中的 if 控制节点。
    返回:
        存在终止 else 时返回 True，否则返回 False。
    """

    # alternate 由 formatter 单独保存互斥备选路径。
    list_alternate = list(dict_if.get("alternate", []) or [])  # 当前 if 的备选路径

    # 没有 alternate 即缺少 else。
    if not list_alternate:

        # 当前 if 链未闭合。
        return False

    # 多个直接 alternate 已提供非空终止路径。
    if len(list_alternate) != 1:

        # 非标准但明确存在备选分支时视为闭合。
        return True

    # 单个 alternate 通常是 formatter 的 else 包装节点。
    dict_alternate = list_alternate[0]  # 当前 if 的唯一备选节点

    # 非 else 包装仍代表明确备选路径。
    if dict_alternate.get("kind") != "else":

        # 当前分支已由 formatter 确认为 alternate。
        return True

    # else 的 children 承载普通 else 主体或嵌套 else-if。
    list_children = list(dict_alternate.get("children", []) or [])  # 当前 else 的主体节点

    # 空 else 没有形成有效终止路径。
    if not list_children:

        # 当前 if 链仍未闭合。
        return False

    # 单一嵌套 if 表示 else-if，需要继续检查链尾。
    if len(list_children) == 1 and list_children[0].get("kind") == "if":

        # 递归结果决定 else-if 链是否具有最终 else。
        return _if_chain_has_terminal_else(list_children[0])

    # 普通非空 else 主体形成终止分支。
    return True

# _header_expression 提取 formatter 控制头第一对完整括号内的文本。
def _header_expression(str_header: str) -> str:
    """提取 if 或 case 头部第一对完整括号内的表达式。

    参数:
        str_header: formatter 保留的控制语句头部。
    返回:
        第一对完整括号内的表达式；括号不完整时返回空字符串。
    """

    # 起始括号确定控制表达式的左边界。
    int_start = str_header.find("(")  # 控制头第一个左括号位置

    # 缺少左括号时不能可靠提取表达式。
    if int_start < 0:

        # 空文本会由上层传播为未知事实。
        return ""

    # depth 跟踪嵌套函数调用等内部括号。
    int_depth = 0  # 当前括号嵌套深度

    # 从首个左括号开始寻找与之匹配的右括号。
    for int_index in range(int_start, len(str_header)):

        # 当前字符用于更新括号深度。
        str_character = str_header[int_index]  # 当前扫描字符

        # 左括号增加嵌套层级。
        int_depth += str_character == "("  # 更新左括号深度

        # 右括号关闭当前嵌套层级。
        int_depth -= str_character == ")"  # 更新右括号深度

        # 深度回到零时找到完整外层括号。
        if int_depth == 0:

            # 返回去除两侧空白的控制表达式。
            return str_header[int_start + 1 : int_index].strip()

    # 括号不完整时保持未知而不猜测文本边界。
    return ""

# _split_case_labels 按括号深度识别 case item 的顶层逗号。
def _split_case_labels(str_label: str) -> list[str]:
    """按顶层逗号拆分 case item 标签。

    参数:
        str_label: formatter 保留的完整 case item 标签文本。
    返回:
        去除空白和空项后的标签列表。
    """

    # labels 保存每个顶层逗号切分出的标签。
    list_labels: list[str] = []  # 已完成切分的标签文本

    # depth 防止连接、索引和函数调用内部逗号被误切分。
    int_depth = 0  # 当前括号、方括号和花括号深度

    # start 指向当前标签片段的起始偏移。
    int_start = 0  # 当前待切分标签起点

    # 逐字符扫描以识别顶层逗号。
    for int_index, str_character in enumerate(str_label):

        # 开括号统一增加嵌套深度。
        int_depth += str_character in "({["  # 更新开括号深度

        # 闭括号统一减少嵌套深度。
        int_depth -= str_character in ")} ]".replace(" ", "")  # 更新闭括号深度

        # 只有零深度逗号才分隔并列 case 标签。
        if str_character == "," and int_depth == 0:

            # 当前片段去除两侧空白后进入结果。
            list_labels.append(str_label[int_start:int_index].strip())

            # 下一个标签从逗号后一位开始。
            int_start = int_index + 1  # 更新下一标签起点

    # 末尾片段没有终止逗号，需要显式加入。
    list_labels.append(str_label[int_start:].strip())

    # 空标签不进入规则判断。
    return [str_item for str_item in list_labels if str_item]

# _case_kind 归一化 formatter case 头部关键字。
def _case_kind(str_header: str) -> str:
    """返回 case、casez 或 casex 的规范类别。

    参数:
        str_header: formatter 保留的 case 控制头部。
    返回:
        规范化 case 类别；无法识别时返回普通 case。
    """

    # 只读取头部首个 case 关键字，不扫描 case 主体。
    obj_match = re.match(r"\s*(case[xz]?)\b", str_header, flags=re.IGNORECASE)  # case 类别匹配结果

    # 未匹配时保守使用普通 case 的精确比较语义。
    return "case" if obj_match is None else obj_match.group(1).lower()

# _patterns_overlap 按 case 类别比较两个规范比特模式。
def _patterns_overlap(left: ConstantBits, right: ConstantBits, str_case_kind: str) -> bool:
    """判断两个同宽 case 模式是否存在共同匹配值。

    参数:
        left: 左侧已解析常量比特模式。
        right: 右侧已解析常量比特模式。
        str_case_kind: case、casez 或 casex 类别。
    返回:
        两个模式存在共同匹配值时返回 True。
    """

    # 不同宽度由 VG084 处理，当前规则不猜测扩展语义。
    if left.width != right.width:

        # 位宽不同不形成当前高置信重叠证据。
        return False

    # casex 把 x、z 和问号都作为通配位。
    set_wildcards = {"x", "z", "?"} if str_case_kind == "casex" else {"z", "?"}  # 当前 case 类别的通配字符

    # 普通 case 的未知态字符按精确四态值比较。
    if str_case_kind == "case":

        # 清空通配集合即可复用逐位兼容算法。
        set_wildcards = set()  # 普通 case 不含通配字符

    # 每一位相等或至少一侧为通配符时模式兼容。
    return all(
        str_left == str_right or str_left in set_wildcards or str_right in set_wildcards
        for str_left, str_right in zip(left.bits, right.bits)
    )

# _finding 把可信 module 内的文本位置转换为一基源码证据。
def _finding(
    str_path: str,
    str_module_text: str,
    int_base_line: int,
    str_locator: str,
    str_message: str,
    str_evidence: str,
) -> VgFinding:
    """按可信 module 文本定位并构造规则证据。

    参数:
        str_path: 当前 RTL 文件的稳定相对路径。
        str_module_text: formatter 已确认边界的 module 文本。
        int_base_line: module 文本的一基起始行。
        str_locator: 需要定位的原始控制或语句文本。
        str_message: 当前固定规则的中文诊断。
        str_evidence: 报告中保留的最小规则证据。
    返回:
        已计算一基行号的 VG finding。
    """

    # locator 首次出现位置足以定位当前 formatter 节点。
    int_offset = str_module_text.find(str_locator)  # 定位文本在 module 内的字符偏移

    # 定位失败时保守使用 module 起始行，禁止伪造精确位置。
    int_line = int_base_line if int_offset < 0 else int_base_line + str_module_text.count("\n", 0, int_offset)  # 当前证据的一基行号

    # 统一 finding 字段供 engine 序列化。
    return VgFinding(str_path, int_line, str_message, str_evidence)

# _finish 集中实施确定失败优先于不确定事实的状态合同。
def _finish(
    list_findings: list[VgFinding],
    bool_applicable: bool,
    bool_unknown: bool,
    str_unknown_message: str,
) -> VgEvaluation:
    """按失败优先、不确定次之的统一顺序生成规则结论。

    参数:
        list_findings: 当前规则确认的全部违规证据。
        bool_applicable: 当前 RTL 是否包含规则适用结构。
        bool_unknown: 当前规则是否遇到无法静态判断的事实。
        str_unknown_message: 不确定结论的用户诊断文本。
    返回:
        失败、不确定或通过的统一 VG 结论。
    """

    # 任一确定违规都优先形成 failed。
    if list_findings:

        # 失败结论保留全部可定位证据。
        return failed(*list_findings)

    # 无确定违规但事实不完整时必须 fail-closed。
    if bool_unknown:

        # 不确定结论不得伪装为 passed。
        return inconclusive(str_unknown_message)

    # 已知事实全部合规或规则不适用时返回 passed。
    return passed(applicable=bool_applicable)
