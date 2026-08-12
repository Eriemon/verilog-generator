"""verify-repair 的 RTL patch 类别识别与候选生成辅助函数。"""

# 延迟类型求值，降低 helper 之间的导入耦合。
from __future__ import annotations

# 正则扫描负责定位 reset、case 和 output-reg patch 插入点。
import re
from typing import Any

# patch 候选优先级保持原实现顺序。
def select_patch_candidate(
    *,
    str_source_text: str,
    dict_analysis: dict[str, Any],
    str_reset_name: str,
) -> tuple[str, str | None, list[int], str]:
    """
    按优先级选择 RTL patch 候选。

    :param str_source_text: 待扫描和修补的 RTL 源码全文。
    :param dict_analysis: 端口、状态元素和寄存输出分析结果。
    :param str_reset_name: 分析阶段确认的复位信号名。
    :return: patch 类别、候选文本、行号提示和风险级别。
    """

    # 首选 reset 初始化补全，因为它是低风险 auto_apply 类别。
    tuple_reset_candidate = patch_missing_reset_initialization(  # reset 初始化候选文本与行号
        str_source_text,  # 用于查找 else-if 链和非阻塞赋值的 RTL 全文
        dict_analysis=dict_analysis,  # 提供输出和状态信号宽度
        str_reset_name=str_reset_name,  # 定位 reset 分支的信号名
    )

    # reset 候选文本为空时继续尝试后续模式。
    str_candidate_text = tuple_reset_candidate[0]  # reset 初始化候选 RTL 全文

    # reset 候选行号用于确认插入位置。
    list_patch_lines = tuple_reset_candidate[1]  # reset 初始化新增行号

    # 命中 reset 补全时直接返回。
    if str_candidate_text and list_patch_lines:

        # reset 初始化补全是唯一允许 auto_apply 的低风险候选。
        return (
            "reset_initialization_completion",
            str_candidate_text,
            list_patch_lines,
            "reset branch assigns some staged outputs but misses at least one reset initialization assignment",
        )

    # 其次尝试 case default 补全。
    tuple_case_candidate = patch_case_default_completion(str_source_text)  # case default 候选文本与行号

    # case 候选文本为空时继续尝试 hold 分支。
    str_candidate_text = tuple_case_candidate[0]  # case default 候选 RTL 全文

    # case 候选行号用于人工审查插入位置。
    list_patch_lines = tuple_case_candidate[1]  # case default 新增行号

    # case default 属于控制覆盖面变更。
    if str_candidate_text and list_patch_lines:

        # case default 改变控制覆盖面，必须等待人工确认。
        return (
            "case_default_completion",
            str_candidate_text,
            list_patch_lines,
            "case statement lacks a default branch and can leave control state behavior underspecified",
        )

    # 再尝试 state hold 分支补全。
    tuple_hold_candidate = patch_state_hold_completion(  # else-if 链缺失保持分支时的候选文本与行号
        str_source_text,  # 用于查找 output reg reset-only 模式的 RTL 全文
        dict_analysis=dict_analysis,  # 提供需要 hold 的状态信号集合
    )

    # hold 候选文本为空时继续尝试 output reg。
    str_candidate_text = tuple_hold_candidate[0]  # hold 分支候选生成后的 RTL 全文

    # hold 候选行号标记 else 分支插入区域。
    list_patch_lines = tuple_hold_candidate[1]  # hold 分支插入点的行号提示

    # hold 候选命中后进入中风险确认路径。
    if str_candidate_text and list_patch_lines:

        # hold 分支可能影响时序保持语义，保持人工确认边界。
        return (
            "state_hold_clear_completion",
            str_candidate_text,
            list_patch_lines,
            "clocked conditional updates are missing an explicit hold branch for assigned state or output signals",
        )

    # 最后尝试 output register 补全。
    tuple_output_candidate = patch_output_register_completion(  # output reg 只有 reset 赋值时的 active 分支候选
        str_source_text,  # 待扫描的 RTL 全文
        dict_analysis=dict_analysis,  # 提供 output reg 和 input 映射信息
    )

    # output 候选文本为空时最终返回无候选。
    str_candidate_text = tuple_output_candidate[0]  # output reg active 补全后的 RTL 全文

    # output 候选行号用于定位 active 分支赋值。
    list_patch_lines = tuple_output_candidate[1]  # output reg active 分支新增行号

    # output reg 候选会新增数据通路赋值。
    if str_candidate_text and list_patch_lines:

        # output reg 新增数据通路赋值，继续要求人工确认。
        return (
            "output_register_completion",
            str_candidate_text,
            list_patch_lines,
            "an output register is initialized but never updated in the active branch, "
            "indicating a missing registered datapath assignment",
        )

    # 没有稳定 patch 模式。
    return "none", None, [], "no stable low-risk RTL patch pattern was detected"

# risk level 文本保持旧报告契约。
def patch_risk_level(str_patch_category: str, bool_candidate_available: bool) -> str:
    """
    返回 patch 类别对应的风险级别。

    :param str_patch_category: 当前候选命中的 RTL patch 类别。
    :param bool_candidate_available: 是否已经生成可比较的候选 RTL。
    :return: low、medium 或 blocked 风险等级。
    """

    # 没有候选即 blocked。
    if not bool_candidate_available:

        # blocked 表示无法自动处理。
        return "blocked"

    # reset 初始化补全是唯一低风险类别。
    if str_patch_category == "reset_initialization_completion":

        # 返回 low 保持 auto_apply 测试预期。
        return "low"

    # 其他候选为 medium，需要确认。
    return "medium"

# reset 初始化补全扫描 reset begin 内缺失的输出/state 赋值。
def patch_missing_reset_initialization(
    str_source_text: str,
    *,
    dict_analysis: dict[str, Any],
    str_reset_name: str,
) -> tuple[str | None, list[int]]:
    """
    补全 reset 分支缺失的初始化赋值。

    :param str_source_text: 待修改的 RTL 源码全文。
    :param dict_analysis: 状态元素和输出端口位宽分析结果。
    :param str_reset_name: reset block 匹配所需的复位信号名。
    :return: 候选 RTL 文本和插入位置行号；无法补全时返回 None 与空列表。
    """

    # 没有 reset 信号无法定位 reset 分支。
    if not str_reset_name:

        # 不产生候选。
        return None, []

    # 信号宽度来自分析结果。
    dict_signal_widths = signal_widths(dict_analysis)  # 需要考虑 reset 初始化的信号宽度

    # 待插入的 reset 赋值。
    list_patch_targets: list[str] = []  # 缺失的 reset 赋值语句

    # 逐个检查输出和状态信号。
    for str_signal, int_width in dict_signal_widths.items():

        # 只有源文件中已有该信号赋值时才尝试补 reset。
        if not re.search(rf"\b{re.escape(str_signal)}\s*<=\s*", str_source_text):

            # 信号未在时序逻辑中出现非阻塞赋值时不推断 reset 缺口。
            continue

        # reset block 文本用于判断哪些 state/output 已有初始化。
        str_reset_block = extract_reset_block(str_source_text, str_reset_name)  # 用于判断既有初始化覆盖面的 reset 分支文本

        # 缺少 reset block 时不能生成候选。
        if str_reset_block is None:

            # reset 结构不稳定时停止该低风险补全模式。
            return None, []

        # 已经有 reset 赋值则跳过。
        if re.search(rf"\b{re.escape(str_signal)}\s*<=\s*", str_reset_block):

            # 既有 reset 赋值已经覆盖该信号，避免重复初始化。
            continue

        # 记录需要插入的 reset 赋值。
        list_patch_targets.append(reset_assignment(str_signal, int_width))

    # 没有缺失赋值时不生成候选。
    if not list_patch_targets:

        # 所有可观察信号都已具备 reset 初始化。
        return None, []

    # 插入 reset 赋值并返回行号。
    return insert_reset_assignments(
        str_source_text=str_source_text,
        str_reset_name=str_reset_name,
        list_patch_targets=list_patch_targets,
    )

# reset 赋值插到 reset begin 后一行。
def insert_reset_assignments(
    *,
    str_source_text: str,
    str_reset_name: str,
    list_patch_targets: list[str],
) -> tuple[str | None, list[int]]:
    """
    在 reset begin 后插入初始化赋值。

    :param str_source_text: 原始 RTL 源码全文。
    :param str_reset_name: 复位信号名，用于保留调用方语义上下文。
    :param list_patch_targets: 需要补入 reset block 的初始化赋值语句。
    :return: 插入后的候选 RTL 文本和新增赋值所在行号。
    """

    # 源文本按行处理，保留原缩进。
    list_lines = str_source_text.splitlines()  # RTL 源文本行

    # patched_lines 收集修改后的文本。
    list_patched_lines: list[str] = []  # 插入 reset 初始化后的 RTL 行序列

    # inserted_line_numbers 记录插入行号。
    list_inserted_line_numbers: list[int] = []  # reset 初始化新增赋值所在行号

    # reset begin 文本模式保留复位名转义，避免信号名中的特殊字符影响正则。
    str_regex_reset_pattern: str = rf"if\s*\(\s*!?{re.escape(str_reset_name)}\s*\)\s*begin"  # 复位条件 begin 行的正则文本

    # reset begin 正则用于定位新增初始化语句的插入点。
    pattern_regex_reset_begin: re.Pattern[str] = re.compile(str_regex_reset_pattern)  # 定位 reset 分支起始行的正则匹配器

    # 逐行复制，并在 reset begin 后插入。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先保留原行。
        list_patched_lines.append(str_line)

        # 命中 reset begin 后插入补丁行。
        if pattern_regex_reset_begin.search(str_line):

            # 插入缩进比 reset begin 多一级。
            str_indent = re.match(r"\s*", str_line).group(0) + "    "  # reset 赋值缩进

            # 写入每条 reset 赋值。
            for int_offset, str_assignment in enumerate(list_patch_targets, start=1):

                # 将缺失初始化放在 reset begin 后方。
                list_patched_lines.append(f"{str_indent}{str_assignment}")

                # 记录新增 reset 赋值对应的候选行号。
                list_inserted_line_numbers.append(int_index + int_offset)

    # 只在确实插入时返回候选文本。
    if list_inserted_line_numbers:

        # 候选文本保留源文件末尾换行约定。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # 未命中 reset begin 时说明正则模式无法安全定位插入点。
    return None, []

# case default 补全在 endcase 前插入空 default。
def patch_case_default_completion(str_source_text: str) -> tuple[str | None, list[int]]:
    """
    为缺少 default 的 case 语句生成补全候选。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :return: 已补入空 default 分支的候选文本和插入行号；没有目标时返回 None。
    """

    # 行列表用于在 endcase 前插入 default 且不破坏原缩进。
    list_lines = str_source_text.splitlines()  # case/default 补丁扫描的源行序列

    # patched_lines 保留原始 case 行并夹入 default 分支。
    list_patched_lines: list[str] = []  # 插入 default 分支后的 RTL 行序列

    # 插入行号帮助人工在 diff 中定位新增 default。
    list_inserted_line_numbers: list[int] = []  # default begin/end 两行的候选行号

    # inside_case 追踪当前扫描是否位于一个 case 块内。
    bool_inside_case = False  # 当前扫描位置是否处于 case/endcase 区间

    # has_default 避免给已经覆盖默认分支的 case 重复插入。
    bool_case_has_default = False  # 当前 case 是否已包含 default

    # case indent 用于让新增 default 与原分支对齐。
    str_case_indent = ""  # 新增 default 分支沿用的 case 子语句缩进

    # 遍历每一行查找 case/endcase。
    for int_index, str_line in enumerate(list_lines, start=1):

        # stripped 用于关键字匹配。
        str_stripped = str_line.strip()  # 去缩进后的 RTL 行

        # case 起始重置状态。
        if str_stripped.startswith("case ") or str_stripped.startswith("case("):

            # 进入新的 case 后开始收集 default 状态。
            bool_inside_case = True  # 记录后续行需要寻找 default 或 endcase

            # 新 case 初始视为尚未覆盖 default 分支。
            bool_case_has_default = False  # 当前 case 尚未发现 default 分支

            # default 缩进比 case 多一级。
            str_case_indent = re.match(r"\s*", str_line).group(0) + "    "  # default 分支插入缩进

        # default 已存在时记录。
        if bool_inside_case and str_stripped.startswith("default"):

            # 当前 case 不需要补丁。
            bool_case_has_default = True  # 记录该 case 已覆盖 default 分支

        # endcase 前补 default。
        if bool_inside_case and str_stripped.startswith("endcase") and not bool_case_has_default:

            # 插入 default begin，显式保留未列举状态的空动作。
            list_patched_lines.append(f"{str_case_indent}default: begin")

            # 插入 default end，与 begin 成对保持 Verilog 结构完整。
            list_patched_lines.append(f"{str_case_indent}end")

            # 行号指向插入的 default begin/end，供 patch plan 展示。
            list_inserted_line_numbers.extend([int_index, int_index + 1])  # default begin/end 的候选行号

            # endcase 前完成补齐后退出当前 case 追踪。
            bool_inside_case = False  # endcase 前已完成 default 补全

        # 原 RTL 行必须保留，候选 patch 只做最小插入。
        list_patched_lines.append(str_line)

        # 扫描到 endcase 时关闭当前 case 状态。
        if str_stripped.startswith("endcase"):

            # 离开 case 后避免下一段逻辑误用旧状态。
            bool_inside_case = False  # 扫描离开当前 case/endcase 区间

    # 有插入时返回候选文本。
    if list_inserted_line_numbers:

        # 候选文本保留源 RTL 的末尾换行约定。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # 所有 case 都已有 default，或没有稳定 case/endcase 区间。
    return None, []

# state hold 补全给 clocked else-if 链加显式 hold 分支。
def patch_state_hold_completion(str_source_text: str, *, dict_analysis: dict[str, Any]) -> tuple[str | None, list[int]]:
    """
    为缺少 hold 分支的时序逻辑生成候选。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param dict_analysis: 提供状态寄存器名称的结构分析结果。
    :return: 已插入 hold 分支的候选文本和行号；无法插入时返回 None。
    """

    # 没有 else-if 或已有 else begin 时不处理。
    if "else if" not in str_source_text or re.search(r"else\s+begin", str_source_text):

        # 既无 else-if 链或已有 else 分支时不适合插入 hold。
        return None, []

    # 只处理源中确有非阻塞赋值的状态/输出信号。
    list_stateful_signals = [
        str_name  # 在时序逻辑中出现过赋值的状态或输出信号
        for str_name in signal_widths(dict_analysis)  # 分析得到的可保持信号宽度表
        if re.search(rf"\b{re.escape(str_name)}\s*<=", str_source_text)  # 源码里存在非阻塞赋值
    ]  # 需要 hold 的信号列表

    # 没有可 hold 信号时不生成候选。
    if not list_stateful_signals:

        # 没有可保持的状态信号时跳过该 patch 类别。
        return None, []

    # 插入 hold 分支。
    return insert_state_hold_branch(str_source_text, list_stateful_signals)

# state hold 分支插入到连续 end 的边界处。
def insert_state_hold_branch(str_source_text: str, list_stateful_signals: list[str]) -> tuple[str | None, list[int]]:
    """
    插入 state/output hold 分支。

    :param str_source_text: 原始 RTL 源码全文。
    :param list_stateful_signals: 需要在 hold 分支显式自保持的寄存器名称。
    :return: 增加 else begin hold 分支后的候选文本和插入行号。
    """

    # 行扫描用于寻找 else-if 链末尾的连续 end。
    list_lines = str_source_text.splitlines()  # hold 分支插入扫描的源行序列

    # patched_lines 会在插入点前后保留原始 RTL 顺序。
    list_patched_lines: list[str] = []  # hold 分支补丁输出行序列

    # 插入行号记录每条 hold 赋值，便于人工核对。
    list_inserted_line_numbers: list[int] = []  # hold 赋值新增行号

    # 逐行查找连续 end 位置。
    for int_index, str_line in enumerate(list_lines, start=1):

        # stripped line 只用于判断连续 end 和 else-if 链。
        str_stripped = str_line.strip()  # else-if 链尾判定使用的去缩进文本

        # 简单启发式定位 else-if 链结束点。
        bool_insertion_point = (
            str_stripped == "end"  # 当前行是候选链尾 end
            and int_index > 1  # 需要上一行存在
            and list_lines[int_index - 2].strip() == "end"  # 上一行也是 end
            and "else if" in "\n".join(list_lines[: int_index - 1])  # 前文出现 else-if 链
        )  # 是否命中 hold 分支插入点

        # 命中后先插入 else hold。
        if bool_insertion_point:

            # 缩进沿用当前 end。
            str_indent = re.match(r"\s*", str_line).group(0)  # hold 分支外层缩进

            # 子语句缩进多一级。
            str_child_indent = str_indent + "    "  # 自保持赋值使用的内层缩进

            # 插入 else begin。
            list_patched_lines.append(f"{str_indent}else begin")

            # 每个信号保持自赋值。
            for int_offset, str_signal in enumerate(list_stateful_signals, start=1):

                # 插入当前状态信号的保持赋值。
                list_patched_lines.append(f"{str_child_indent}{str_signal} <= {str_signal};")

                # 记录 hold 赋值在候选文件中的行号。
                list_inserted_line_numbers.append(int_index + int_offset)

            # 结束新增 hold 分支，保持 Verilog 块结构完整。
            list_patched_lines.append(f"{str_indent}end")

        # 保留原 RTL 行。
        list_patched_lines.append(str_line)

    # 有插入时返回候选。
    if list_inserted_line_numbers:

        # 有候选时保留源文件末尾换行，避免 diff 产生额外噪声。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # 未定位到链尾时不生成 hold patch。
    return None, []

# output register 补全寻找只 reset 不更新的 output reg。
def patch_output_register_completion(
    str_source_text: str,
    *,
    dict_analysis: dict[str, Any],
) -> tuple[str | None, list[int]]:
    """
    为缺少 active 分支更新的 output reg 生成候选。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param dict_analysis: 输出端口和信号角色分析结果。
    :return: 已补入输出寄存器赋值的候选文本和行号；无缺失项时返回 None。
    """

    # 先识别声明为 output reg 的端口。
    set_reg_outputs = declared_reg_outputs(str_source_text)  # RTL 中声明为 reg 的输出端口

    # 只考虑分析端口中属于 output reg 的项。
    list_outputs = [
        dict_item  # 分析器确认且源码声明为 reg 的 output 端口
        for dict_item in dict_analysis.get("ports", [])  # output reg 候选来自顶层端口分析结果
        if dict_item.get("direction") == "output"  # 只分析 output 方向端口
        and str(dict_item.get("name") or "") in set_reg_outputs  # 只保留源码声明为 reg 的输出
    ]  # 候选 output reg 端口

    # 没有 output reg 时不处理。
    if not list_outputs:

        # 没有 output reg 时该补全类别不适用。
        return None, []

    # reset 名称用于划分 reset 和 active 分支赋值。
    str_reset_name = next(  # 用于区分 reset body 与 active body 的复位端口名
        (
            dict_item["name"]  # reset 角色端口名
            for dict_item in dict_analysis.get("ports", [])  # reset 名称来自顶层端口角色分析
            if dict_item.get("role") == "reset"  # 只选择 reset 角色
        ),
        "",  # 缺少 reset 时关闭 output-reg active 补全
    )  # RTL 复位信号名

    # reset block 和 span 同时用于判断赋值位置。
    str_reset_block = extract_reset_block(str_source_text, str_reset_name) if str_reset_name else None  # reset 分支文本

    # span 用字符位置判断是否在 reset 中。
    tuple_reset_span = extract_reset_block_span(str_source_text, str_reset_name) if str_reset_name else None  # reset 分支字符范围

    # 缺少 reset block 时不能稳定判断。
    if str_reset_block is None or tuple_reset_span is None:

        # reset 结构缺失时无法区分 reset 与 active 赋值。
        return None, []

    # 找出只在 reset 中赋值的 output reg。
    list_missing_outputs = missing_output_register_assignments(  # 需要补 active 更新的 output reg 赋值语句
        str_source_text=str_source_text,  # 用于查找 output reg 赋值位置的 RTL 全文
        dict_analysis=dict_analysis,  # 推断 active 分支赋值来源的结构分析
        list_outputs=list_outputs,  # 只在 reset 中赋值的候选 output reg 集合
        tuple_reset_span=tuple_reset_span,  # reset body 字符范围
    )  # active 分支缺失的 output reg 赋值

    # 没有缺失输出时不生成候选。
    if not list_missing_outputs:

        # 返回无候选。
        return None, []

    # 插入 active 分支赋值。
    return insert_output_register_assignments(str_source_text, list_missing_outputs)

# output reg 缺失分析输出具体赋值语句。
def missing_output_register_assignments(
    *,
    str_source_text: str,
    dict_analysis: dict[str, Any],
    list_outputs: list[dict[str, Any]],
    tuple_reset_span: tuple[int, int],
) -> list[str]:
    """
    识别只在 reset 中赋值的 output reg。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param dict_analysis: 输出端口角色和名称分析结果。
    :param list_outputs: 结构分析得到的 output port 条目。
    :param tuple_reset_span: reset body 在源文本中的字符范围。
    :return: 需要在时序 active 分支补赋值的语句列表。
    """

    # missing outputs 保存需要补入 active 分支的非阻塞赋值。
    list_missing_outputs: list[str] = []  # 需要插入 active 分支的 output reg 赋值

    # 逐个 output reg 检查赋值位置。
    for dict_output in list_outputs:

        # 输出名用于搜索该 output reg 的所有非阻塞赋值。
        str_name = str(dict_output["name"])  # 正在检查 active 分支覆盖面的 output reg 名称

        # 查找所有非阻塞赋值。
        list_assignments = list(re.finditer(rf"\b{re.escape(str_name)}\s*<=", str_source_text))  # 该 output 的赋值位置

        # 是否存在 reset 内赋值。
        bool_has_reset_assignment = any(  # 当前 output reg 是否具备 reset 初始化
            tuple_reset_span[0] <= match_assignment.start() < tuple_reset_span[1]  # 赋值位置落在 reset body 内
            for match_assignment in list_assignments  # reset 范围判断使用的赋值位置集合
        )  # reset 分支是否赋值

        # 是否存在 active 分支赋值。
        bool_has_non_reset_assignment = any(  # 当前 output reg 是否具备 active 更新
            not (tuple_reset_span[0] <= match_assignment.start() < tuple_reset_span[1])  # 赋值起点位于 reset body 之外
            for match_assignment in list_assignments  # 遍历当前 output reg 的非阻塞赋值位置
        )  # 非 reset 区间内是否已有输出更新

        # 没有 reset 赋值时不属于该补全类别。
        if not bool_has_reset_assignment:

            # 跳过不稳定候选。
            continue

        # 已有 active 赋值时不需要补。
        if bool_has_non_reset_assignment:

            # 跳过已覆盖输出。
            continue

        # 推断 active 分支赋值。
        list_missing_outputs.append(inferred_output_assignment(str_name, dict_analysis))

    # 返回缺失赋值列表。
    return list_missing_outputs

# output reg 赋值插到 else begin 后。
def insert_output_register_assignments(
    str_source_text: str,
    list_missing_outputs: list[str],
) -> tuple[str | None, list[int]]:
    """
    插入 output reg active 分支赋值。

    :param str_source_text: 原始 RTL 源码全文。
    :param list_missing_outputs: 需要插入到 active 分支的赋值语句。
    :return: 插入输出寄存器赋值后的候选文本和行号。
    """

    # 行扫描用于在 active else begin 后插入 output reg 更新。
    list_lines = str_source_text.splitlines()  # 寻找时序 active else begin 的原始 RTL 行序列

    # patched_lines 保留原 RTL 并插入 active 分支赋值。
    list_patched_lines: list[str] = []  # output reg 补丁输出行序列

    # inserted line numbers 对应新增的 output reg 赋值。
    list_inserted_line_numbers: list[int] = []  # active 分支新增赋值行号

    # 记录是否插入。
    bool_inserted = False  # 是否已经插入 output reg 赋值

    # 查找 active else begin。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 原行先写入，后续命中 active 分支再追加赋值。
        list_patched_lines.append(str_line)

        # 去缩进后的控制行用于识别 active else begin 结构。
        str_stripped = str_line.strip()  # active else begin 匹配使用的去缩进文本

        # 命中 active branch 后插入赋值。
        if str_stripped == "end else begin" or str_stripped.endswith("else begin"):

            # 赋值缩进比 else begin 多一级。
            str_indent = re.match(r"\s*", str_line).group(0) + "    "  # active 赋值使用的内层缩进

            # 逐条插入缺失赋值。
            for int_offset, str_assignment in enumerate(list_missing_outputs, start=1):

                # 添加 active 赋值。
                list_patched_lines.append(f"{str_indent}{str_assignment}")

                # 记录新增 output reg 赋值的候选行号。
                list_inserted_line_numbers.append(int_index + int_offset)

            # 已插入后避免重复处理后续 else begin。
            bool_inserted = True  # output reg active 赋值已经插入

    # 有插入则返回候选。
    if bool_inserted:

        # 有候选时保留末尾换行，保持写回文件格式稳定。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # active 分支定位失败时不生成候选。
    return None, []

# root cause evidence 连接诊断 finding、patch reason 和 checkpoint。
def build_root_cause_evidence(
    dict_diagnosis: dict[str, Any],
    dict_verification_plan: dict[str, Any],
    *,
    str_patch_reason: str,
) -> list[str]:
    """
    生成 patch candidate 的根因证据摘要。

    :param dict_diagnosis: 诊断阶段的 finding 和 outcome。
    :param dict_verification_plan: checkpoint 与 focus signal 计划。
    :param str_patch_reason: 当前 patch 候选命中的根因说明。
    :return: 写入 patch plan 的根因证据文本列表。
    """

    # 诊断 finding 和 patch reason 是最核心证据。
    list_evidence = [str(dict_diagnosis["findings"][0]), str_patch_reason]  # 根因证据文本

    # focus signals 帮助人工定位波形。
    list_focus_signals = [
        str(item_signal)  # 波形排查时展示的关注信号名
        for item_signal in dict_verification_plan.get("focus_signals", [])  # 验证计划中的 focus signal 字段
        if str(item_signal)  # 过滤空信号名
    ]  # 关注信号列表

    # 最多展示四个信号，避免 payload 太大。
    if list_focus_signals:

        # 添加 focus signals 摘要。
        list_evidence.append("focus_signals: " + ", ".join(list_focus_signals[:4]))

    # 最多展示两个 checkpoint。
    for dict_target in dict_verification_plan.get("verification_targets", [])[:2]:

        # checkpoint 证据优先展示行为描述，缺失时退回目标名。
        str_description = str(  # root_cause_evidence 中展示的 checkpoint 文本
            dict_target.get("description") or dict_target.get("name") or ""  # 行为描述优先，目标名兜底
        ).strip()

        # 非空描述才进入证据。
        if str_description:

            # 添加 checkpoint 证据。
            list_evidence.append("checkpoint: " + str_description)

    # 返回证据列表。
    return list_evidence

# reset block 提取是多个 patch 类别的基础。
def extract_reset_block(str_source_text: str, str_reset_name: str) -> str | None:
    """
    提取 reset 分支 body 文本。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param str_reset_name: 用于定位 reset 条件的信号名。
    :return: reset begin/end 块文本；未找到时返回 None。
    """

    # reset block match 决定是否能安全截取 body。
    match_reset = extract_reset_block_match(str_source_text, str_reset_name)  # reset block 正则匹配

    # 缺失 match 时返回 None。
    if not match_reset:

        # 没有稳定 reset block。
        return None

    # 返回命名分组 body。
    return match_reset.group("body")

# reset block match 使用非贪婪正则定位 else 前文本。
def extract_reset_block_match(str_source_text: str, str_reset_name: str) -> re.Match[str] | None:
    """
    返回 reset 分支的正则匹配对象。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param str_reset_name: reset 条件中应出现的信号名。
    :return: 匹配 reset begin/end 文本的正则结果；未找到时返回 None。
    """

    # reset block 模式截取 reset 分支 body，供赋值覆盖分析复用。
    str_reset_block_pattern = (
        rf"if\s*\(\s*!?{re.escape(str_reset_name)}\s*\)\s*begin(?P<body>.*?)end\s+else"  # reset body 到 active else 的捕获模式
    )  # 复位分支 body 提取正则文本

    # reset block 正则用于同时支持 body 文本和字符 span。
    pattern_regex_pattern: re.Pattern[str] = re.compile(  # reset body 提取和 span 定位共用的正则对象
        str_reset_block_pattern,  # 捕获 reset body 到 active else 前
        re.DOTALL,  # 允许 reset body 跨多行
    )  # reset block 正则

    # 正则匹配结果供 reset body 和 span helper 复用。
    return pattern_regex_pattern.search(str_source_text)

# reset span 用于区分 reset/non-reset 赋值。
def extract_reset_block_span(str_source_text: str, str_reset_name: str) -> tuple[int, int] | None:
    """
    返回 reset 分支 body 的字符范围。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param str_reset_name: reset 条件中应出现的信号名。
    :return: reset block 字符范围；未找到时返回 None。
    """

    # reset span 复用同一匹配，避免 body 和 span 使用不同正则。
    match_reset = extract_reset_block_match(str_source_text, str_reset_name)  # reset span 使用的正则匹配

    # 没有 match 时无法判断 span。
    if not match_reset:

        # 无法定位 reset body 时不做 reset/non-reset 赋值区分。
        return None

    # 返回 body 分组 span。
    return match_reset.span("body")

# signal width 同时覆盖输出端口和 state elements。
def signal_widths(dict_analysis: dict[str, Any]) -> dict[str, int]:
    """
    提取 output/state 信号宽度。

    :param dict_analysis: 包含 ports 和 state_elements 的结构分析结果。
    :return: 信号名到位宽的映射。
    """

    # 收集信号宽度。
    dict_widths: dict[str, int] = {}  # 输出和状态信号宽度表

    # 输出端口优先进入宽度表。
    for dict_item in dict_analysis.get("ports", []):

        # 只处理 output。
        if dict_item.get("direction") == "output":

            # width 缺失时按 1 bit 处理单比特输出。
            dict_widths[str(dict_item["name"])] = int(dict_item.get("width") or 1)  # output 端口位宽

    # state elements 补充进入宽度表。
    for dict_item in dict_analysis.get("state_elements", []):

        # state element 名称用于补充 output 表未覆盖的寄存器宽度。
        str_name = str(dict_item["name"])  # 状态寄存器宽度表键名

        # output 已有记录时不覆盖。
        if str_name not in dict_widths:

            # width 缺失时按 1 bit 处理未显式标注的状态寄存器。
            dict_widths[str_name] = int(dict_item.get("width") or 1)  # 状态信号位宽

    # 返回宽度表。
    return dict_widths

# reset assignment 文本按位宽选择 Verilog literal。
def reset_assignment(str_signal: str, int_width: int) -> str:
    """
    生成 reset 初始化赋值语句。

    :param str_signal: RTL 信号名，shape=scalar，dtype=str，unit=Verilog identifier。
    :param int_width: 信号位宽，shape=scalar，dtype=int，unit=bit。
    :return: 非阻塞 reset 赋值文本，shape=scalar，dtype=str，unit=Verilog statement。
    """

    # 单 bit 使用 1'b0。
    if int_width <= 1:

        # 返回单 bit reset 赋值。
        return f"{str_signal} <= 1'b0;"

    # 多 bit 使用 width'd0。
    return f"{str_signal} <= {int_width}'d0;"

# output reg 声明提取使用 Verilog-2001 常见写法。
def declared_reg_outputs(str_source_text: str) -> set[str]:
    """
    提取声明为 output reg 的端口名。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :return: 在 output reg 声明中出现的信号名集合。
    """

    # 使用 set 推导保持唯一端口名。
    return {
        match_output.group("name")
        for match_output in re.finditer(
            r"output\s+reg(?:\s*\[[^\]]+\])?\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
            str_source_text,
        )
    }

# output active 赋值优先从同名 input 推断。
def inferred_output_assignment(str_signal: str, dict_analysis: dict[str, Any]) -> str:
    """
    推断 output register 的 active 分支赋值。

    :param str_signal: output reg 信号名，shape=scalar，dtype=str，unit=Verilog identifier。
    :param dict_analysis: 结构分析 payload，shape=dict，dtype=dict[str, Any]，unit=analysis fields。
    :return: active 分支非阻塞赋值文本，shape=scalar，dtype=str，unit=Verilog statement。
    """

    # 去掉 o_ 前缀后匹配 i_ 前缀输入。
    str_suffix = str_signal[2:] if str_signal.startswith("o_") else str_signal  # output 对应的数据后缀

    # 查找同名或 i_ 前缀输入。
    for dict_port in dict_analysis.get("ports", []):

        # 输入端口才可作为赋值来源。
        if dict_port.get("direction") != "input":

            # 跳过非输入端口。
            continue

        # 输入端口名称。
        str_port_name = str(dict_port.get("name") or "")  # 候选输入端口名

        # 匹配 i_<suffix> 或 suffix。
        if str_port_name == f"i_{str_suffix}" or str_port_name == str_suffix:

            # 返回 input 到 output 的寄存赋值。
            return f"{str_signal} <= {str_port_name};"

    # 找不到输入时保持自赋值 hold。
    return f"{str_signal} <= {str_signal};"

