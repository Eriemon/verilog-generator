"""封装 FSM 分段、状态命名与 next-state 专项规则。"""

# 延迟类型注解求值，避免模块导入阶段过早解析复杂联合类型。
from __future__ import annotations

# 复制原 quality gate 的基础标准库依赖，保持无第三方包可运行。
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# formatter_ast 与 rulebook 仍是这些子模块依赖的唯一结构化入口。
from .formatter_backend.banners import display_width
from .formatter_ast import build_ast_report_for_path, iter_verilog_sources, read_verilog_source
from scripts.python.validation.rulebook import load_verilog_rulebook

# FSM 规则只依赖当前模块实际会消费的上下文与问题类型。
from .quality_gate_types import (
    CommentVerticalSpacingContext,
    QualityIssue,
)

# FSM 主干匹配式保持共享定义，避免各子模块分叉状态机规则。
from .quality_gate_common import (
    FSM_CASE_BRANCH_BEGIN_PATTERN,
    FSM_CASE_KEYWORD_PATTERN,
    FSM_DEFAULT_BRANCH_PATTERN,
)

# 条件分支模式单独成组，便于追踪三段式结构约束依赖。
from .quality_gate_common import (
    FSM_ELSE_BEGIN_PATTERN,
    FSM_ELSE_IF_BEGIN_PATTERN,
    FSM_IF_BEGIN_PATTERN,
    FSM_PLAIN_END_PATTERN,
)

# `state_current/state_next` 相关模式继续显式导入。
from .quality_gate_common import (
    FSM_STATE_CASE_PATTERN,
    FSM_STATE_NEXT_ASSIGN_PATTERN,
    FSM_STATE_NEXT_HOLD_PATTERN,
)

# FSM 语义 helper 与行号换算单独成组，避免继续依赖 `*` 展开。
from .quality_gate_common import (
    _always_references_state_task,
    _as_line,
    _is_pure_line_comment,
)

# 缩进、区域标题与风格严重级别 helper 保持独立分组。
from .quality_gate_common import (
    _is_region_banner_line,
    _line_indent,
    _style_severity,
)

# 供 `_fsm_rules` 复用的拆分 helper，专门处理检查状态参数、状态寄存器和三段式 FSM 结构。
def _fsm_rules(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查状态参数、状态寄存器和三段式 FSM 结构。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: FSM 结构相关诊断列表。
    """

    # list_issues 保存三段式 FSM 结构、状态命名和状态任务诊断。
    list_issues: list[QualityIssue] = []  # FSM 结构诊断集合

    # list_state_params 收集 ST_ 状态参数。
    list_state_params = _fsm_state_params(dict_module)  # ST_ 状态枚举参数列表

    # list_state_signals 收集 FSM current/next 状态信号候选。
    list_state_signals = _fsm_state_signals(dict_module)  # state_ 状态信号列表

    # 没有状态参数和状态信号时不执行 FSM 规则。
    if not list_state_params and not list_state_signals:

        # 非 FSM 模块无需三段式约束。
        return list_issues

    # str_severity 让 FSM 结构问题跟随 strict 模式升级或降级。
    str_severity = "error" if strict else "warning"  # FSM gate 输出级别

    # 状态信号检查确认 state_current/state_next 声明齐备。
    list_issues.extend(_fsm_state_signal_issues(list_state_signals, str_rel_path, str_severity))

    # FSM 分段检查确认状态寄存器段和 next-state 段存在。
    list_issues.extend(_fsm_segment_issues(dict_module, list_state_params, str_rel_path, str_severity))

    # 状态枚举命名检查 ST_ 后缀是否保持大写。
    list_issues.extend(_fsm_state_name_issues(list_state_params, str_rel_path, strict=strict))

    # next-state 组合段必须包含 default 和默认保持。
    list_issues.extend(_fsm_next_state_rules(dict_module, str_rel_path, strict=strict))

    # next-state case 分支的前导注释沿用 VG063 的相邻行、缩进与空行布局规则。
    list_issues.extend(_fsm_case_branch_leading_comment_issues(dict_module, list_lines, str_rel_path, str_severity))

    # 返回状态枚举、三段式分段和状态任务诊断。
    return list_issues

# 供 `_fsm_state_params` 复用的拆分 helper，专门处理收集 module 中的 ST_ 状态枚举参数。
def _fsm_state_params(dict_module: dict[str, Any]) -> list[str]:
    """
    收集 module 中的 ST_ 状态枚举参数。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: ST_ 状态参数名列表。
    """

    # 返回所有状态枚举参数名。
    return [
        str(dict_item.get("name"))  # ST_ 状态枚举名
        for dict_item in dict_module.get("localparams", [])  # 遍历 localparam 条目
        if str(dict_item.get("name", "")).startswith("ST_")  # 只保留状态枚举参数
    ]

# 供 `_fsm_state_signals` 复用的拆分 helper，专门处理收集 module 中的 state_ 状态信号。
def _fsm_state_signals(dict_module: dict[str, Any]) -> list[str]:
    """
    收集 module 中的 state_ 状态信号。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: state_ 状态信号名列表。
    """

    # 返回 current/next 等状态信号名。
    return [
        str(dict_item.get("name"))  # state_ 前缀状态信号名
        for dict_item in dict_module.get("decls", [])  # 遍历内部声明条目
        if str(dict_item.get("name", "")).startswith("state_")  # 只保留状态信号声明
    ]

# 供 `_fsm_state_signal_issues` 复用的拆分 helper，专门处理检查 FSM 是否同时声明 state_current 和 state_next。
def _fsm_state_signal_issues(
    list_state_signals: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 FSM 是否同时声明 state_current 和 state_next。

    :param list_state_signals: state_ 状态信号名列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: FSM 结构诊断级别。
    :return: 状态信号诊断列表。
    """

    # 必须存在 current 和 next 两个状态信号。
    if "state_current" in list_state_signals and "state_next" in list_state_signals:

        # 状态信号满足三段式约定。
        return []

    # 状态信号缺失时登记三段式问题。
    return [
        QualityIssue(
            "VG023",
            str_severity,
            "FSM must use `state_current` and `state_next` signals.",
            str_rel_path,
            rule="fsm.three_segment",
        )
    ]

# 供 `_fsm_segment_issues` 复用的拆分 helper，专门处理检查 FSM 是否具备状态寄存器段、next-state 段和状态任务段。
def _fsm_segment_issues(
    dict_module: dict[str, Any],
    list_state_params: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 FSM 是否具备状态寄存器段、next-state 段和状态任务段。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_state_params: ST_ 状态参数名列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: FSM 结构诊断级别。
    :return: FSM 分段结构诊断列表。
    """

    # tuple_flags 表示状态寄存器、next-state 和状态任务段是否存在。
    tuple_flags = _fsm_segment_flags(dict_module)  # (状态寄存器段, next-state 段, 状态任务段)

    # list_issues 保存 FSM 分段问题。
    list_issues: list[QualityIssue] = []  # FSM 分段诊断

    # 状态寄存器段和下一状态段必须同时存在。
    if not (tuple_flags[0] and tuple_flags[1]):

        # 三段式至少需要 state register 和 next-state combinational 两段。
        list_issues.extend(_fsm_missing_core_segment_issues(str_rel_path, str_severity))

    # 有状态参数但没有独立状态任务块时给 warning。
    if list_state_params and not tuple_flags[2]:

        # 第三段可能是直接输出，保留人工确认空间。
        list_issues.append(
            QualityIssue(
                "VG023",
                "warning",
                "FSM has state parameters but no separate state task/output block was detected; "
                "confirm three-segment FSM intent.",
                str_rel_path,
                rule="fsm.three_segment",
            )
        )

    # 返回 FSM 分段诊断。
    return list_issues

# 供 `_fsm_segment_flags` 复用的拆分 helper，专门处理FSM 状态寄存器段、next-state 段和状态任务段是否存在。
def _fsm_segment_flags(dict_module: dict[str, Any]) -> tuple[bool, bool, bool]:
    """
    判断 FSM 状态寄存器段、next-state 段和状态任务段是否存在。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 三个布尔值分别表示状态寄存器段、next-state 段和状态任务段。
    """

    # bool_seq_state 表示状态寄存器段存在。
    bool_seq_state = any(  # 三段式 FSM 的状态寄存器段是否存在
        dict_always.get("trigger_kind") == "seq"  # 时序段候选 always
        and "state_current" in dict_always.get("targets", [])  # 目标包含当前态寄存器
        for dict_always in dict_module.get("always", [])  # 扫描候选时序 always
    )

    # bool_comb_next 表示 next-state 组合逻辑段存在。
    bool_comb_next = any(  # 三段式 FSM 的 next-state 组合段是否存在
        dict_always.get("is_combinational")  # 组合段候选 always
        and "state_next" in dict_always.get("targets", [])  # 目标包含下一态信号
        for dict_always in dict_module.get("always", [])  # 扫描 next-state 候选块
    )

    # bool_state_task 表示独立状态输出/任务段存在。
    bool_state_task = any(  # 是否存在第三段状态输出/任务逻辑
        _always_references_state_task(dict_always)  # 状态输出/任务段候选 always
        for dict_always in dict_module.get("always", [])  # 扫描第三段候选块
    )

    # 返回三个分段是否存在。
    return bool_seq_state, bool_comb_next, bool_state_task

# 供 `_fsm_missing_core_segment_issues` 复用的拆分 helper，专门处理构造状态寄存器段或 next-state 段缺失诊断。
def _fsm_missing_core_segment_issues(str_rel_path: str, str_severity: str) -> list[QualityIssue]:
    """
    构造状态寄存器段或 next-state 段缺失诊断。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: FSM 结构诊断级别。
    :return: 缺失核心分段诊断列表。
    """

    # 新旧规则号同时保留，兼容已有测试和新交付门禁。
    return [
        QualityIssue(
            "VG023",
            str_severity,
            "FSM must be generated as at least state-register and next-state combinational blocks.",
            str_rel_path,
            rule="fsm.three_segment",
        ),
        QualityIssue(
            "VG054",
            str_severity,
            "FSM delivery must keep separate state-register and next-state combinational blocks.",
            str_rel_path,
            rule="fsm.strict_three_segment",
        ),
    ]

# 供 `_fsm_state_name_issues` 复用的拆分 helper，专门处理检查 FSM 状态参数是否使用 ST_ 大写命名。
def _fsm_state_name_issues(list_state_params: list[str], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 FSM 状态参数是否使用 ST_ 大写命名。

    :param list_state_params: ST_ 状态参数名列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把状态命名问题升级为 error。
    :return: 状态参数命名诊断列表。
    """

    # list_issues 保存状态参数命名问题。
    list_issues: list[QualityIssue] = []  # FSM 状态命名诊断

    # 状态参数命名再次按 FSM 语义校验。
    for str_param_name in list_state_params:

        # 合规状态名跳过。
        if re.fullmatch(r"ST_[A-Z0-9_]+", str_param_name):

            # ST_ 后为大写状态名。
            continue

        # 状态名问题归入 FSM 规则。
        list_issues.append(
            QualityIssue(
                "VG023",
                _style_severity(strict),
                f"State parameter `{str_param_name}` must use ST_ uppercase naming.",
                str_rel_path,
                rule="fsm.state_name",
            )
        )

    # 返回状态参数命名诊断。
    return list_issues

# 供 `_leading_comment_layout_issues_for_line_no` 复用的拆分 helper，专门处理检查指定源码行是否具备紧贴、对齐且空行布局正确的前导注释。
def _leading_comment_layout_issues_for_line_no(
    int_line_no: int,
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    str_label: str,
    str_rule: str,
) -> list[QualityIssue]:
    """
    检查指定源码行是否具备紧贴、对齐且空行布局正确的前导注释。

    :param int_line_no: 目标结构或分支标签的一基行号。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 注释布局问题的严重级别。
    :param str_label: 诊断中展示的结构类别。
    :param str_rule: 诊断规则命名空间。
    :return: 前导注释布局诊断列表。
    """

    # list_issues 保存当前目标行的前导注释布局诊断。
    list_issues: list[QualityIssue] = []  # 指定目标行的前导注释布局诊断

    # 缺少行号或越界时不伪造诊断位置。
    if int_line_no <= 1 or int_line_no > len(list_lines):

        # 无法定位上一行时直接跳过。
        return list_issues

    # str_target_line 是当前块或状态分支标签行。
    str_target_line = list_lines[int_line_no - 1]  # 目标结构源码行

    # int_comment_line_no 是目标结构正上方一行。
    int_comment_line_no = int_line_no - 1  # 前导注释行号

    # str_comment_line 是目标结构正上方的源码行。
    str_comment_line = list_lines[int_comment_line_no - 1]  # 前导注释候选行

    # 前导说明必须恰好在结构上一行，不能隔空引用。
    if not _is_pure_line_comment(str_comment_line):

        # 已有 leading_comments 但源码上一行不是纯注释，说明位置不合规。
        list_issues.append(
            QualityIssue(
                "VG063",
                str_severity,
                f"{str_label} leading comment must be the pure comment line immediately above the block.",
                str_rel_path,
                int_line_no,
                rule=str_rule,
            )
        )

        # 无纯注释行时无法继续检查缩进和空行。
        return list_issues

    # 前导注释必须和目标结构的最左列对齐。
    if _line_indent(str_comment_line) != _line_indent(str_target_line):

        # 缩进不一致会破坏块归属的视觉锚点。
        list_issues.append(
            QualityIssue(
                "VG063",
                str_severity,
                f"{str_label} leading comment must align with the block start column.",
                str_rel_path,
                int_comment_line_no,
                rule=str_rule,
            )
        )

    # vertical_spacing_context 绑定 VG063 的空行布局诊断字段。
    vertical_spacing_context = CommentVerticalSpacingContext(  # VG063 空行布局上下文
        str_rel_path,  # 前导注释布局诊断路径
        str_severity,  # 前导注释布局严重级别
        "VG063",  # 块前导注释空行规则码
        str_label,  # 块前导注释诊断标签
        str_rule,  # 块前导注释规则路径
    )

    # 前导注释上方必须满足唯一空行或紧邻区域横幅规则。
    list_issues.extend(_comment_vertical_spacing_issues(list_lines, int_comment_line_no, vertical_spacing_context))

    # 返回当前结构的前导注释布局诊断。
    return list_issues

# 供 `_comment_vertical_spacing_issues` 复用的拆分 helper，专门处理检查前导或分组注释上方是否满足唯一空行规则。
def _comment_vertical_spacing_issues(
    list_lines: list[str],  # 当前文件源码行
    int_comment_line_no: int,  # 被检查的纯注释行号
    vertical_spacing_context: CommentVerticalSpacingContext,  # 空行布局诊断上下文
) -> list[QualityIssue]:
    """
    检查前导或分组注释上方是否满足唯一空行规则。

    :param list_lines: 当前 Verilog 源码行列表。
    :param int_comment_line_no: 纯注释所在的一基行号。
    :param vertical_spacing_context: 空行布局诊断上下文。
    :return: 空行布局诊断列表。
    """

    # 第一行注释没有上方上下文，保持豁免。
    if int_comment_line_no <= 1:

        # 文件顶部注释由文件头规则处理。
        return []

    # int_anchor_line_no 回溯连续注释栈的首行，让区域横幅或空行规则绑定到整组注释。
    int_anchor_line_no = int_comment_line_no  # 连续纯注释栈的首行候选

    # 当前注释若只是多行说明中的后续行，应复用首条注释的空行上下文。
    while int_anchor_line_no > 1:

        # str_previous_comment_line 是当前注释栈上一行。
        str_previous_comment_line = list_lines[int_anchor_line_no - 2]  # 注释栈上一行源码

        # 只有连续纯注释才属于同一说明栈；区域横幅仍视作外层上下文。
        if not _is_pure_line_comment(str_previous_comment_line) or _is_region_banner_line(str_previous_comment_line):

            # 命中非纯注释或区域横幅时，当前栈首已确定。
            break

        # 继续向上回溯，直到到达连续注释栈首行。
        int_anchor_line_no -= 1  # 注释栈首行继续上移一行

    # str_previous_line 是注释栈首行上方一行。
    str_previous_line = list_lines[int_anchor_line_no - 2]  # 注释栈首行上一行

    # 紧邻区域横幅时不允许额外空行。
    if _is_region_banner_line(str_previous_line):

        # 当前注释直接绑定区域横幅后的首个结构。
        return []

    # 非区域上下文下，注释上方必须恰好一个空行。
    if str_previous_line.strip():

        # str_message 说明注释上方缺少唯一空行。
        str_message = (
            f"{vertical_spacing_context.str_label} comment must have exactly one blank line "
            "above unless it follows a region banner."
        )  # 缺少空行诊断文本

        # 上方不是空行也不是区域横幅，说明缺少唯一空行。
        return [_vertical_spacing_issue(vertical_spacing_context, int_anchor_line_no, str_message)]

    # 注释上方已有一个空行时，不能再多一个空行。
    if int_anchor_line_no > 2 and not list_lines[int_anchor_line_no - 3].strip():

        # str_message 说明注释上方存在多个连续空行。
        str_message = (
            f"{vertical_spacing_context.str_label} comment must have exactly one blank line "
            "above, not multiple blank lines."
        )  # 多余空行诊断文本

        # 连续空行违反“必须且只有 1 个空行”。
        return [_vertical_spacing_issue(vertical_spacing_context, int_anchor_line_no, str_message)]

    # 区域横幅之后如果插入空行再写注释，同样违反例外规则。
    if int_anchor_line_no > 2 and _is_region_banner_line(list_lines[int_anchor_line_no - 3]):

        # str_message 说明区域横幅和首个注释之间不能隔空。
        str_message = (
            f"{vertical_spacing_context.str_label} comment must directly follow the "
            "region banner without a blank line."
        )  # 横幅后空行诊断文本

        # 区域横幅和第一条前导/分组注释之间不能有空行。
        return [_vertical_spacing_issue(vertical_spacing_context, int_anchor_line_no, str_message)]

    # 空行布局满足规则。
    return []

# 供 `_vertical_spacing_issue` 复用的拆分 helper，专门处理构造 VG063/VG065 空行布局诊断。
def _vertical_spacing_issue(
    vertical_spacing_context: CommentVerticalSpacingContext,
    int_comment_line_no: int,
    str_message: str,
) -> QualityIssue:
    """
    构造 VG063/VG065 空行布局诊断。

    :param vertical_spacing_context: 空行布局诊断上下文。
    :param int_comment_line_no: 当前纯注释的一基行号。
    :param str_message: 已生成的英文诊断文本。
    :return: 空行布局质量门诊断。
    """

    # 返回绑定上下文规则码和源码行的布局问题。
    return QualityIssue(
        vertical_spacing_context.str_code,
        vertical_spacing_context.str_severity,
        str_message,
        vertical_spacing_context.str_rel_path,
        int_comment_line_no,
        rule=vertical_spacing_context.str_rule,
    )

# 供 `_fsm_case_branch_action` 复用的拆分 helper，专门处理当前 next-state 行对状态 case 深度的影响动作。
def _fsm_case_branch_action(str_line: str, int_case_depth: int) -> str:
    """
    返回当前 next-state 行对状态 case 深度的影响动作。

    :param str_line: 去除外侧空白后的当前源码行。
    :param int_case_depth: 进入 `case(state_current)` 后的当前嵌套深度。
    :return: case 深度动作名称。
    """

    # 顶层 `case(state_current)` 会开启后续状态标签扫描。
    if FSM_STATE_CASE_PATTERN.match(str_line):

        # 命中主状态分派 case。
        return "enter_state_case"

    # 已进入状态 case 后，普通 case 关键字会继续增加嵌套深度。
    if int_case_depth > 0 and FSM_CASE_KEYWORD_PATTERN.match(str_line):

        # 命中内层 case。
        return "enter_nested_case"

    # 只有进入过状态 case 时，endcase 才需要回退深度。
    if int_case_depth > 0 and str_line == "endcase":

        # 命中 case 结束。
        return "leave_case"

    # 顶层状态分支标签需要进一步追加 VG063 注释检查。
    if int_case_depth == 1 and FSM_CASE_BRANCH_BEGIN_PATTERN.match(str_line):

        # 命中 ST_* 或 default 顶层分支。
        return "check_branch"

    # 其余行不改变状态 case 扫描。
    return "other"

# 供 `_fsm_case_branch_leading_comment_issues_for_block` 复用的拆分 helper，专门处理扫描单个 next-state always 中的状态分支前导注释布局。
def _fsm_case_branch_leading_comment_issues_for_block(
    dict_always: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    扫描单个 next-state always 中的状态分支前导注释布局。

    :param dict_always: formatter AST 中驱动 state_next 的组合 always 条目。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 注释布局问题的严重级别。
    :return: 当前 next-state always 的状态分支注释诊断列表。
    """

    # int_block_line_start 是 always 正文第一行在源码中的绝对行号。
    int_block_line_start = _as_line(dict_always.get("line_start"))  # next-state always 起始行号

    # 缺少绝对行号时无法把分支注释准确落回源文件。
    if int_block_line_start is None:

        # 行号缺失交给 span 规则，不在 VG063 伪造位置。
        return []

    # list_issues 保存当前 next-state always 的状态分支注释诊断。
    list_issues: list[QualityIssue] = []  # 当前 next-state always 的状态分支诊断

    # int_case_depth 只在进入 `case(state_current)` 后跟踪嵌套层数。
    int_case_depth = 0  # 当前 next-state always 内的状态 case 嵌套深度

    # 逐行扫描 next-state always，找出直属状态分支标签。
    for int_offset, str_raw_line in enumerate(dict_always.get("lines", []) or []):

        # str_line 用于匹配 Erie 紧凑 next-state 风格里的 case 分支文本。
        str_line = str(str_raw_line).strip()  # 当前 next-state 行文本

        # str_action 统一表达当前行对状态 case 深度的影响。
        str_action = _fsm_case_branch_action(str_line, int_case_depth)  # 当前 next-state 行的状态分支动作

        # 主状态 case 和内层 case 都会增加嵌套深度。
        if str_action in {"enter_state_case", "enter_nested_case"}:

            # 新进入的 case 作用域需要增加深度。
            int_case_depth += 1  # 当前状态 case 嵌套深度加一

            # case 深度更新完成后继续扫描下一行。
            continue

        # endcase 命中后回退一层 case 深度。
        if str_action == "leave_case":

            # 退出当前 case 作用域。
            int_case_depth = max(int_case_depth - 1, 0)  # 当前状态 case 嵌套深度回退一层

            # case 深度回退完成后继续扫描下一行。
            continue

        # 非顶层状态分支标签不需要继续做 VG063 扩展检查。
        if str_action != "check_branch":

            # 当前行不是直属状态分支标签。
            continue

        # always.lines 从头部下一行开始，换算源码行号时需要补一行偏移。
        int_branch_line_no = int_block_line_start + int_offset + 1  # 状态分支标签绝对行号

        # 直接借用通用布局检查，评估当前状态标签前导注释。
        list_branch_issues = _leading_comment_layout_issues_for_line_no(  # 当前状态分支标签的 VG063 扩展诊断
            int_branch_line_no,  # 当前状态分支标签的绝对源码行号
            list_lines,  # 整个 RTL 文件的源码行列表
            str_rel_path,  # 状态注释诊断落点路径
            str_severity,  # 状态注释规则严重级别
            "Case branch",  # 诊断文案里的对象名称
            "comments.case_branch_leading_comment",  # VG063 扩展到 case 分支的子规则
        )

        # 状态标签与 default 标签都必须带纯前导注释并满足现有布局规则。
        list_issues.extend(list_branch_issues)

    # 返回当前 next-state always 的 VG063 扩展诊断。
    return list_issues

# 供 `_fsm_case_branch_leading_comment_issues` 复用的拆分 helper，专门处理检查 `case(state_current)` 下的 `ST_*:begin` 与 `default:begin` 前导注释布局。
def _fsm_case_branch_leading_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 `case(state_current)` 下的 `ST_*:begin` 与 `default:begin` 前导注释布局。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 注释布局问题的严重级别。
    :return: FSM 状态分支前导注释诊断列表。
    """

    # list_issues 聚合全部 next-state always 的状态分支注释诊断。
    list_issues: list[QualityIssue] = []  # module 级 FSM case 分支前导注释诊断

    # 逐个 next-state always 扫描 `case(state_current)` 下的分支标签。
    for dict_always in _next_state_always_blocks(dict_module):

        # 让单 always helper 只负责一个块的状态标签检查。
        list_block_issues = _fsm_case_branch_leading_comment_issues_for_block(  # 单个 next-state always 的 VG063 扩展诊断
            dict_always,  # 当前正在扫描的 next-state always 条目
            list_lines,  # 当前 module 对应的完整源码行列表
            str_rel_path,  # module 级状态注释诊断路径
            str_severity,  # module 级状态注释规则严重级别
        )

        # 合并当前 always 贡献的全部状态分支前导注释问题。
        list_issues.extend(list_block_issues)

    # 返回 FSM 状态分支的 VG063 诊断。
    return list_issues

# 供 `_fsm_next_state_rules` 复用的拆分 helper，专门处理检查 FSM next-state 组合段是否包含 default、默认保持和完整 if/else 闭合。
def _fsm_next_state_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 FSM next-state 组合段是否包含 default、默认保持和完整 if/else 闭合。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: FSM next-state 深语义诊断列表。
    """

    # list_issues 保存 next-state 默认覆盖相关的 VG054 诊断。
    list_issues: list[QualityIssue] = []  # next-state 组合段诊断集合

    # FSM next-state 组合块以 state_next 作为赋值目标。
    list_next_blocks = _next_state_always_blocks(dict_module)  # state_next 组合 always 块集合

    # 没有 next-state 块时由三段式规则报告。
    if not list_next_blocks:

        # 无需重复登记。
        return list_issues

    # 逐个 next-state 组合块检查 case/default 和默认保持。
    for dict_always in list_next_blocks:

        # str_block_text 用于跨行查找 case/default 和 state_next 赋值。
        str_block_text = _always_block_text(dict_always)  # next-state always 正文文本

        # int_line_no 定位 VG054 到 next-state always 起始行。
        int_line_no = _as_line(dict_always.get("line_start"))  # VG054 报告使用的 next-state always 起始行

        # case 型 next-state 必须有 default 分支。
        if _has_case_without_default(str_block_text):

            # 缺 default 会导致未覆盖状态锁存或不可预测跳转。
            list_issues.append(
                QualityIssue(
                    "VG054",
                    _style_severity(strict),
                    "FSM next-state case block must include a default branch.",
                    str_rel_path,
                    int_line_no,
                    rule="fsm.next_state_default",
                )
            )

        # next-state 组合段应先默认保持当前态，再按条件覆盖。
        if FSM_STATE_NEXT_ASSIGN_PATTERN.search(str_block_text) and not FSM_STATE_NEXT_HOLD_PATTERN.search(
            str_block_text
        ):

            # 默认保持是 Erie 三段式 FSM 的可读性和锁存防护要求。
            list_issues.append(
                QualityIssue(
                    "VG054",
                    _style_severity(strict),
                    "FSM next-state block must default `state_next = state_current;` "
                    "before overrides.",
                    str_rel_path,
                    int_line_no,
                    rule="fsm.next_state_hold",
                )
            )

        # next-state 组合逻辑中的 if / else if 链必须显式闭合到最终 else。
        list_issues.extend(_fsm_next_state_branch_closure_issues(dict_always, str_rel_path, strict=strict))

    # 返回 next-state 默认覆盖诊断。
    return list_issues

# 供 `_fsm_next_state_line_action` 复用的拆分 helper，专门处理当前 next-state 行在 if 链闭合扫描中的动作名称。
def _fsm_next_state_line_action(str_line: str) -> str:
    """
    返回当前 next-state 行在 if 链闭合扫描中的动作名称。

    :param str_line: 去除外侧空白后的当前源码行。
    :return: 分支闭合扫描动作名称。
    """

    # 状态分支标签本身会打开一层 begin/end 作用域。
    if FSM_CASE_BRANCH_BEGIN_PATTERN.match(str_line):

        # 命中状态分支标签。
        return "case_branch"

    # 普通 if 分支会开启一条新的 if / else if / else 链。
    if FSM_IF_BEGIN_PATTERN.match(str_line):

        # 命中 if 链起点。
        return "if_begin"

    # else-if 只是延续已有 if 链，不单独开新链。
    if FSM_ELSE_IF_BEGIN_PATTERN.match(str_line):

        # 命中 else-if 延续分支。
        return "else_if"

    # 终止 else 负责闭合当前 if 链的语义覆盖。
    if FSM_ELSE_BEGIN_PATTERN.match(str_line):

        # 命中终止 else 分支。
        return "else_begin"

    # 普通 end 用于关闭最近的 begin/end 作用域。
    if FSM_PLAIN_END_PATTERN.match(str_line):

        # 命中普通 end。
        return "plain_end"

    # 其余行不会影响 if 链闭合判定。
    return "other"

# 供 `_mark_terminal_else_for_top_if_chain` 复用的拆分 helper，专门处理标记最内层 if 链已经出现终止 else。
def _mark_terminal_else_for_top_if_chain(list_if_stack: list[dict[str, Any]]) -> None:
    """
    标记最内层 if 链已经出现终止 else。

    :param list_if_stack: 当前打开的 if 链状态栈。
    :return: 本函数只更新传入栈状态，不返回业务值。
    """

    # 没有待闭合 if 链时无需记录终止 else。
    if not list_if_stack:

        # 空栈时忽略孤立 else begin。
        return

    # 顶层 if 链已经具备显式终止 else。
    list_if_stack[-1]["has_terminal_else"] = True  # 当前最内层 if 链已命中终止 else

# 供 `_fsm_missing_terminal_else_issue` 复用的拆分 helper，专门处理在 plain end 关闭作用域时返回缺失终止 else 的诊断。
def _fsm_missing_terminal_else_issue(
    list_if_stack: list[dict[str, Any]],
    list_block_stack: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> QualityIssue | None:
    """
    在 plain end 关闭作用域时返回缺失终止 else 的诊断。

    :param list_if_stack: 当前打开的 if 链状态栈。
    :param list_block_stack: 当前 begin/end 作用域栈。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把结构问题升级为 error。
    :return: 需要追加的 VG054 诊断，不需要时返回 None。
    """

    # 没有打开作用域时，plain end 不参与 if 链闭合判定。
    if not list_block_stack:

        # 空作用域栈不追加诊断。
        return None

    # str_block_kind 表示当前 plain end 正在关闭的最近作用域类型。
    str_block_kind = list_block_stack.pop()  # 最近 begin/end 作用域类型

    # 只有关闭 if 链本身时才判断是否缺少最终 else。
    if str_block_kind != "if_chain" or not list_if_stack:

        # 关闭 case item 或异常栈深时不产出 if 链诊断。
        return None

    # dict_if_chain 是当前被 plain end 关闭的 if 链状态。
    dict_if_chain = list_if_stack.pop()  # 当前 if 链闭合状态

    # 已命中终止 else 的 if 链满足完整闭合要求。
    if dict_if_chain.get("has_terminal_else"):

        # 当前 if 链已完整闭合。
        return None

    # 当前 if / else if 链没有以 else 收尾，会留下组合分支缺口。
    return QualityIssue(
        "VG054",
        _style_severity(strict),
        "FSM next-state if / else if chain must end with an explicit else branch.",
        str_rel_path,
        int(dict_if_chain["int_line_no"]),
        rule="fsm.next_state_branch_closure",
    )

# 供 `_fsm_open_if_chain` 复用的拆分 helper，专门处理记录新开启的 if 链上下文，并把对应作用域压入 begin/end 栈。
def _fsm_open_if_chain(
    int_line_no: int,
    list_if_stack: list[dict[str, Any]],
    list_block_stack: list[str],
) -> None:
    """
    记录新开启的 if 链上下文，并把对应作用域压入 begin/end 栈。

    :param int_line_no: 当前 if 链起始行的绝对源码行号。
    :param list_if_stack: 当前打开的 if 链状态栈。
    :param list_block_stack: 当前 begin/end 作用域栈。
    :return: 本函数只更新传入栈状态，不返回业务值。
    """

    # dict_if_chain 只保存一个 if 链的起点和 else 覆盖位。
    dict_if_chain = {"int_line_no": int_line_no, "has_terminal_else": False}  # 供 plain end 回收的 if 链上下文

    # 先记录 if 链的诊断上下文。
    list_if_stack.append(dict_if_chain)

    # 再压入 begin/end 作用域，等待 plain end 回收。
    list_block_stack.append("if_chain")

    # 条件链上下文入栈后，本 helper 就可以把控制权交回调用方。
    return None

# 供 `_fsm_apply_nonclosing_next_state_action` 复用的拆分 helper，专门处理处理 case_branch、if_begin、else_begin 这类只更新栈状态的 next-state 行动作。
def _fsm_apply_nonclosing_next_state_action(
    str_action: str,
    int_line_no: int,
    list_if_stack: list[dict[str, Any]],
    list_block_stack: list[str],
) -> None:
    """
    处理 case_branch、if_begin、else_begin 这类只更新栈状态的 next-state 行动作。

    :param str_action: 当前行对应的闭合扫描动作。
    :param int_line_no: 当前行的绝对源码行号。
    :param list_if_stack: 当前打开的 if 链状态栈。
    :param list_block_stack: 当前 begin/end 作用域栈。
    :return: 本函数只更新传入栈状态，不返回业务值。
    """

    # 状态分支标签只需要把 case item 作用域压栈。
    if str_action == "case_branch":

        # 进入新的状态分支作用域，等待后续 plain end 回收。
        list_block_stack.append("case_branch")

        # case_branch 只更新作用域栈，不需要继续向上传递诊断。
        return

    # 普通 if 分支会开启一条新的闭合链。
    if str_action == "if_begin":

        # 把当前 if 链的起始位置和作用域状态同步压入两类栈。
        _fsm_open_if_chain(int_line_no, list_if_stack, list_block_stack)

        # if_begin 只负责登记新链，不直接产出诊断对象。
        return

    # 终止 else 命中后，当前最内层 if 链具备完整闭合语义。
    if str_action == "else_begin":

        # 显式标记最内层 if 链已经补齐最终 else。
        _mark_terminal_else_for_top_if_chain(list_if_stack)

        # else_begin 只补闭合状态，不直接返回质量问题。
        return

# 供 `_fsm_next_state_branch_closure_issues` 复用的拆分 helper，专门处理检查 next-state 组合段中的 if / else if 链是否显式终止于 else 分支。
def _fsm_next_state_branch_closure_issues(
    dict_always: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 next-state 组合段中的 if / else if 链是否显式终止于 else 分支。

    :param dict_always: formatter AST 中驱动 state_next 的组合 always 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把结构问题升级为 error。
    :return: next-state if 链闭合诊断列表。
    """

    # int_block_line_start 是 next-state always 正文第一行的绝对行号。
    int_block_line_start = _as_line(dict_always.get("line_start"))  # next-state always 正文起始行号

    # 缺少绝对行号时无法把 VG054 精确落点到具体 if 链。
    if int_block_line_start is None:

        # 行号缺失时不伪造分支闭合诊断。
        return []

    # list_issues 保存当前 next-state always 的分支闭合诊断。
    list_issues: list[QualityIssue] = []  # 当前 next-state always 的 if 链闭合诊断

    # list_if_stack 记录每条 next-state if 链的起始行和终止 else 覆盖状态。
    list_if_stack: list[dict[str, Any]] = []  # 当前打开的 if 链状态栈

    # list_block_stack 只追踪 case_branch 与 if_chain 两类 begin/end 作用域归属。
    list_block_stack: list[str] = []  # 当前 next-state begin/end 作用域栈

    # 逐行扫描 next-state always 文本，按 begin/end 栈识别 if 链闭合。
    for int_offset, str_raw_line in enumerate(dict_always.get("lines", []) or []):

        # str_line 用于匹配 next-state 组合逻辑的 Erie 紧凑 begin/end 结构。
        str_line = str(str_raw_line).strip()  # 当前 next-state 组合行文本

        # int_line_no 把 block 内行偏移映射回源文件行号。
        int_line_no = int_block_line_start + int_offset  # 当前行的绝对源码行号

        # str_action 统一表达当前行对 if 链闭合扫描的影响。
        str_action = _fsm_next_state_line_action(str_line)  # 当前 next-state 行的闭合扫描动作

        # 非 plain end 动作只更新栈状态，不直接产出 VG054 诊断。
        if str_action != "plain_end":

            # case_branch、if_begin、else_begin 都在这里统一更新作用域状态。
            _fsm_apply_nonclosing_next_state_action(
                str_action,
                int_line_no,
                list_if_stack,
                list_block_stack,
            )

            # 当前行没有关闭 if 链，不需要继续走 VG054 诊断分支。
            continue

        # obj_issue 只在 plain end 回收 if 链且缺少最终 else 时非空。
        obj_issue = _fsm_missing_terminal_else_issue(  # 当前 plain end 关闭动作触发的 VG054 诊断
            list_if_stack,  # 即将被 plain end 回收的 if 链状态栈
            list_block_stack,  # plain end 当前面对的 begin/end 作用域栈
            str_rel_path,  # 需要挂载诊断的相对路径
            strict=strict,  # 是否按严格模式上报 VG054
        )

        # 非 plain end 或已完整闭合的场景不会返回诊断。
        if obj_issue is None:

            # 当前行没有产生新的 VG054 分支闭合问题。
            continue

        # 缺少终止 else 的 if 链在 plain end 关闭时追加诊断。
        list_issues.append(obj_issue)

    # 返回当前 next-state always 的 if 链闭合诊断。
    return list_issues

# 供 `_next_state_always_blocks` 复用的拆分 helper，专门处理驱动 state_next 的组合 always 块。
def _next_state_always_blocks(dict_module: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回驱动 state_next 的组合 always 块。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 驱动 state_next 的组合 always 条目列表。
    """

    # list_blocks 保留后续 VG054 需要检查的 next-state 组合块。
    list_blocks: list[dict[str, Any]] = []  # next-state 组合 always 候选集合

    # 逐个 always 条目筛选组合逻辑中的 state_next 目标。
    for dict_always in dict_module.get("always", []) or []:

        # 非组合 always 不属于三段式 next-state 逻辑。
        if not dict_always.get("is_combinational"):

            # 时序块和无类型块由旧 FSM 规则处理。
            continue

        # targets 表示 formatter 识别到的赋值左值集合。
        if "state_next" not in (dict_always.get("targets", []) or []):

            # 组合块未驱动 state_next 时跳过。
            continue

        # 收集 next-state 组合段。
        list_blocks.append(dict_always)

    # 返回筛选出的 next-state 组合块。
    return list_blocks

# 供 `_always_block_text` 复用的拆分 helper，专门处理always 块的源码文本。
def _always_block_text(dict_always: dict[str, Any]) -> str:
    """
    返回 always 块的源码文本。

    :param dict_always: formatter AST 中的 always 条目。
    :return: 用换行合并后的 always 块文本。
    """

    # str_block_text 保留跨行 case/default 搜索所需的换行边界。
    str_block_text = "\n".join(str(item) for item in dict_always.get("lines", []) or [])  # always 原始行拼接文本

    # 返回 always 正文文本。
    return str_block_text

# 供 `_has_case_without_default` 复用的拆分 helper，专门处理next-state case 块是否缺少 default 分支。
def _has_case_without_default(str_block_text: str) -> bool:
    """
    判断 next-state case 块是否缺少 default 分支。

    :param str_block_text: next-state always 块源码文本。
    :return: 存在 case 但没有 default 分支时返回 True。
    """

    # bool_has_case 表示该组合段采用 case 分派状态。
    bool_has_case = FSM_CASE_KEYWORD_PATTERN.search(str_block_text) is not None  # next-state case 结构存在性

    # bool_has_default 表示 case 至少覆盖默认兜底路径。
    bool_has_default = FSM_DEFAULT_BRANCH_PATTERN.search(str_block_text) is not None  # next-state default 分支存在性

    # case 存在但 default 缺失时触发 VG054。
    return bool_has_case and not bool_has_default

# 返回当前模块需要公开的兼容导出名称清单。
def _export_names() -> list[str]:
    """
    返回当前模块对外继续公开的兼容符号名。

    参数:
        无外部业务参数。

    :return: 稳定的兼容导出名称列表。
    """

    # str_exports_source 按旧测试与调用方依赖顺序保留兼容导出名原文。
    str_exports_source = """
    _fsm_rules
    _fsm_next_state_rules
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
