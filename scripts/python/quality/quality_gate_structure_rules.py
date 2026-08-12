"""封装 module/port/parameter/signal/assign/always 等结构规则。"""

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

# 低有效复位名称统一按下划线语义段识别。
from .reset_name_roles import is_low_active_reset_name

# 结构规则只依赖当前模块实际使用的上下文与问题类型。
from .quality_gate_types import (
    CommentVerticalSpacingContext,
    OutputAssignRegionContext,
    ProtocolOrderIssueContext,
    QualityIssue,
)

# 协议分组与 FSM 关键字模式保持沿用原始 quality gate 的共享定义。
from .quality_gate_common import (
    APB_PORT_TOKENS,
    APB_REQUEST_TOKENS,
    APB_RESPONSE_TOKENS,
)

# AXIS 端口 token 独立成组，便于追踪流式接口的 section 判定。
from .quality_gate_common import (
    AXIS_CONTROL_TOKENS,
    AXIS_DATA_TOKENS,
    AXIS_PORT_TOKENS,
)

# 区域横幅与命名模式维持共享定义，避免各子模块自行拼装规则。
from .quality_gate_common import (
    PORT_GROUP_GENERIC_PATTERN,
    PORT_GROUP_PROTOCOL_PATTERN,
)

# 区域关键字与大写标识符模式用于结构区和参数命名检查。
from .quality_gate_common import (
    REGION_KEYWORDS,
    UPPER_IDENTIFIER_PATTERN,
)

# 命名重复前缀与 FSM 主干匹配式继续共用同一份规则表。
from .quality_gate_common import (
    DUPLICATE_PARAMETER_PREFIXES,
    DUPLICATE_SIGNAL_PREFIXES,
    FSM_CASE_BRANCH_BEGIN_PATTERN,
)

# case/default 关键字与 state_next 匹配式决定三段式 FSM 诊断入口。
from .quality_gate_common import (
    FSM_CASE_KEYWORD_PATTERN,
    FSM_DEFAULT_BRANCH_PATTERN,
    FSM_STATE_CASE_PATTERN,
)

# `state_next` 的赋值与保持模式单独成组，便于后续状态机规则复用。
from .quality_gate_common import (
    FSM_STATE_NEXT_ASSIGN_PATTERN,
    FSM_STATE_NEXT_HOLD_PATTERN,
)

# FSM 条件分支模式单独成组，便于追踪三段式状态机检查依赖。
from .quality_gate_common import (
    FSM_ELSE_BEGIN_PATTERN,
    FSM_ELSE_IF_BEGIN_PATTERN,
    FSM_IF_BEGIN_PATTERN,
    FSM_PLAIN_END_PATTERN,
)

# 跨区域共用的行号与 span helper 显式导入，避免结构规则继续依赖 `*` 展开。
from .quality_gate_common import (
    _as_line,
    _has_line_span,
    _line_indent,
)

# 区域标题定位 helper 单独成组，便于模块内的落区验证共享使用。
from .quality_gate_common import (
    _line_region_titles,
    _nearest_region_title,
    _span_item_label,
)

# 行尾注释清洗 helper 单独保留，避免与区域 helper 混在一起。
from .quality_gate_common import (
    _strip_line_comment,
)

# 命名与端口语义 helper 单独成组，方便追踪结构规则的判定来源。
from .quality_gate_common import (
    _expected_reg_name,
    _flag_name_needs_prefix,
)

# Vitis 端口与常见信号模式判断继续共享同一组 helper。
from .quality_gate_common import (
    _is_vitis_port,
    _looks_counter,
    _looks_decoder,
)

# encoder 与 module 输出端口抽取单独成组，便于检查输出桥接规则。
from .quality_gate_common import (
    _looks_encoder,
    _module_output_ports,
)

# 过程块与区域校验 helper 保持独立分组，阅读时更容易定位行为边界。
from .quality_gate_common import (
    _always_references_state_task,
    _bad_reset_style,
    _configured_region_keywords,
)

# 阻塞/非阻塞赋值与注释区域判断共用同一组过程块 helper。
from .quality_gate_common import (
    _has_blocking_assignment,
    _has_nonblocking_assignment,
    _is_pure_line_comment,
    _is_region_banner_line,
)

# 结构类问题的严重级别计算独立导入，方便后续诊断统一升级。
from .quality_gate_common import (
    _style_severity,
)

# 供 `_module_rules` 复用的拆分 helper，专门处理检查 module 级命名、结构、端口和控制块规则。
def _module_rules(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    vitis_wrapper: bool,
) -> list[QualityIssue]:
    """
    检查 module 级命名、结构、端口和控制块规则。

    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :return: module 级结构和命名诊断列表。
    """

    # list_issues 保存当前 AST 报告衍生出的结构诊断。
    list_issues: list[QualityIssue] = []  # module 结构规则诊断

    # str_style_severity 决定结构风格类诊断级别。
    str_style_severity = _style_severity(strict)  # 结构规则严重级别

    # list_modules 是 formatter AST 解析出的 module 集合。
    list_modules = dict_ast_report.get("modules", [])  # 当前文件 formatter AST module 条目

    # AST 没有 module 时由 formatter 诊断负责报告。
    if not list_modules:

        # 空 module 集合无需重复登记结构规则。
        return list_issues

    # 生成交付文件通常只包含一个综合 module。
    if len(list_modules) > 1:

        # 多 module 文件仍允许继续检查每个 module。
        list_issues.append(
            QualityIssue(
                "VG006",
                str_style_severity,
                "Generated delivery should normally contain one synthesizable RTL module per file.",
                str_rel_path,
                rule="file.single_module",
            )
        )

    # list_source_lines 供需要绝对行号的注释和 FSM 深语义规则复用。
    list_source_lines = str_text.splitlines()  # 当前文件源码行列表

    # 每个 module 独立执行命名、端口、参数和控制块规则。
    for dict_module in list_modules:

        # str_module_name 用于命名诊断和 header 定位。
        str_module_name = str(dict_module.get("name") or "")  # 当前 module 名称

        # AST span 是后续区域和语义规则的可信边界。
        list_issues.extend(_span_rules(dict_module, str_rel_path, strict=strict))

        # 文件头中的 module 信息必须和真实 module 声明一致。
        list_issues.extend(_header_semantic_rules(str_text, str_rel_path, dict_module, strict=strict))

        # Verilog module 名称必须满足基础标识符格式。
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str_module_name):

            # 无效名称直接定位到 module 级。
            list_issues.append(
                QualityIssue(
                    "VG010",
                    str_style_severity,
                    f"Invalid module name `{str_module_name}`.",
                    str_rel_path,
                    rule="naming.module",
                )
            )

        # 先检查 module 声明头，后续端口规则依赖该边界定位。
        list_issues.extend(_module_header_rules(str_text, str_rel_path, dict_module, strict=strict))

        # 端口方向、前缀和输出桥接规则保持旧顺序。
        list_issues.extend(
            _port_rules(dict_module, str_text, str_rel_path, strict=strict, vitis_wrapper=vitis_wrapper)
        )

        # 协议端口顺序检查基于 rulebook 中的协议段定义。
        list_issues.extend(_protocol_port_order_rules(dict_module, str_rel_path, strict=strict))

        # 参数命名规则在信号检查前执行，便于报告顺序稳定。
        list_issues.extend(_parameter_rules(dict_module, str_rel_path, strict=strict))

        # 内部声明命名检查覆盖 reg/wire/logic。
        list_issues.extend(_signal_rules(dict_module, str_rel_path, strict=strict))

        # assign 规则检查连续赋值的输出桥接和命名。
        list_issues.extend(_assign_rules(dict_module, str_text, str_rel_path, strict=strict))

        # always 规则检查时序/组合块的目标和 reset 约束。
        list_issues.extend(_always_rules(dict_module, str_rel_path, strict=strict))

        # reset 深语义检查补充旧规则只看 header 的缺口。
        list_issues.extend(_reset_semantic_rules(dict_module, str_rel_path, strict=strict))

        # FSM 规则确认三段式状态机结构。
        list_issues.extend(_fsm_rules(dict_module, list_source_lines, str_rel_path, strict=strict))

        # instance 规则检查例化命名和 wrapper 风格。
        list_issues.extend(_instance_rules(dict_module, str_rel_path, strict=strict))

        # 区域横幅规则最后执行，避免打乱旧报告顺序。
        list_issues.extend(_region_rules(str_text, str_rel_path, dict_module, strict=strict))

        # AST span 支撑的区域归属规则补充横幅存在性检查。
        list_issues.extend(_region_ownership_rules(str_text, str_rel_path, dict_module, strict=strict))

    # 规则源一致性检查只需要文件级执行一次。
    list_issues.extend(_rulebook_consistency_issues(str_rel_path, strict=strict))

    # 返回当前文件的 module 结构诊断。
    return list_issues

# 供 `_span_rules` 复用的拆分 helper，专门处理检查 formatter AST 结构条目是否带有源码行号范围。
def _span_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 formatter AST 结构条目是否带有源码行号范围。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: AST span 可信度相关诊断列表。
    """

    # list_issues 保存缺失 span 的结构诊断。
    list_issues: list[QualityIssue] = []  # AST span 诊断集合

    # module 顶层行号必须存在。
    if not _has_line_span(dict_module):

        # 没有 module span 时区域和注释定位都不可信。
        list_issues.append(
            QualityIssue(
                "VG050",
                _style_severity(strict),
                f"Module `{dict_module.get('name')}` is missing trusted formatter AST line span.",
                str_rel_path,
                rule="formatter_ast.span",
            )
        )

    # tuple_required_collections 列出必须携带 formatter line_start/line_end 的 module 子结构。
    tuple_required_collections = (
        "params",  # 参数列表条目的源码 span
        "ports",  # 端口列表条目的源码 span
        "localparams",  # 模块体常量条目的源码 span
        "decls",  # wire/reg 声明条目的源码 span
        "assigns",  # 连续赋值语句的源码 span
        "always",  # always 过程块的源码 span
        "instances",  # 子模块实例化语句的源码 span
        "generates",  # generate 结构块源码范围
        "initials",  # initial 仿真块源码范围
        "functions",  # function 定义块源码范围
        "tasks",  # task 任务块源码范围
    )

    # 遍历需要 span 的结构集合。
    for str_collection_name in tuple_required_collections:

        # 当前集合中的每个条目都应可定位。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # 缺 span 时登记 VG050。
            if not _has_line_span(dict_item):

                # name/lhs/header 用于帮助定位具体条目。
                str_label = _span_item_label(dict_item)  # 当前缺失 span 的条目说明

                # 缺少条目级 span 会影响强门禁定位。
                list_issues.append(
                    QualityIssue(
                        "VG050",
                        _style_severity(strict),
                        f"{str_collection_name} item `{str_label}` is missing trusted formatter AST line span.",
                        str_rel_path,
                        rule="formatter_ast.span",
                    )
                )

    # 返回 span 可信度诊断。
    return list_issues

# 供 `_header_semantic_rules` 复用的拆分 helper，专门处理检查 header 中声明的模块名是否匹配真实 module 名。
def _header_semantic_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 header 中声明的模块名是否匹配真实 module 名。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: header 语义一致性诊断列表。
    """

    # list_issues 保存 header 语义诊断。
    list_issues: list[QualityIssue] = []  # header 语义诊断集合

    # module_name 来自 formatter AST 的真实声明。
    str_module_name = str(dict_module.get("name") or "")  # 真实 module 声明名

    # 空 module 名由旧命名规则处理。
    if not str_module_name:

        # 没有真实 module 名时无法比较 header。
        return list_issues

    # 只读取 module 前文本作为文件头区域。
    str_pre_module = _pre_module_region(str_text)  # 待解析的文件头说明区

    # 英文和中文 header 均可提供 module 名。
    tuple_header_names = (  # header 中可比较的 module 名字段
        ("header.module_name.english", _extract_header_field(str_pre_module, "Module Name")),  # 英文头声明名
        ("header.module_name.chinese", _extract_header_field(str_pre_module, "模块名称")),  # 中文头声明名
    )

    # 逐个可用 header 字段检查一致性。
    for str_rule, str_header_name in tuple_header_names:

        # 缺失字段由 VG007 处理，这里只检查已存在的字段。
        if not str_header_name:

            # 跳过缺失字段。
            continue

        # header module 名必须和真实 module 名一致。
        if str_header_name != str_module_name:

            # 语义不一致会误导后续生成、注释和验证流程。
            list_issues.append(
                QualityIssue(
                    "VG051",
                    _style_severity(strict),
                    f"Header module name `{str_header_name}` does not match module declaration `{str_module_name}`.",
                    str_rel_path,
                    1,
                    str_rule,
                )
            )

    # 返回 header 语义诊断。
    return list_issues

# 供 `_protocol_port_order_rules` 复用的拆分 helper，专门处理检查常见协议端口是否按 rulebook 声明的 section 顺序排列。
def _protocol_port_order_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查常见协议端口是否按 rulebook 声明的 section 顺序排列。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 协议端口顺序诊断列表。
    """

    # list_ports 保持 formatter AST 声明顺序和行号。
    list_ports = list(dict_module.get("ports", []) or [])  # 端口 AST 条目顺序

    # 空端口列表无需检查。
    if not list_ports:

        # 返回空诊断。
        return []

    # 当前规则覆盖 rulebook 声明的 AXI/AXIS/APB section 顺序。
    tuple_protocol_tokens = ("axi", "axis", "apb")  # 支持检查的协议 token

    # list_issues 保存协议端口诊断。
    list_issues: list[QualityIssue] = []  # 协议端口顺序诊断集合

    # 逐个协议 token 判断是否出现协议端口。
    for str_protocol in tuple_protocol_tokens:

        # 每个协议单独计算 section 序列和第一处回退诊断。
        list_issues.extend(_protocol_order_issues_for_protocol(list_ports, str_protocol, str_rel_path, strict=strict))

    # 返回协议端口顺序诊断。
    return list_issues

# 供 `_protocol_order_issues_for_protocol` 复用的拆分 helper，专门处理检查单个协议端口是否按 section 顺序声明。
def _protocol_order_issues_for_protocol(
    list_ports: list[dict[str, Any]],
    str_protocol: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个协议端口是否按 section 顺序声明。

    :param list_ports: formatter AST 中保持源码顺序的端口列表。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把协议顺序问题升级为 error。
    :return: 当前协议的端口顺序诊断列表。
    """

    # tuple_sections 定义当前协议端口从时钟复位到数据/响应通道的期望顺序。
    tuple_sections = _protocol_sections(str_protocol)  # 协议端口分组期望顺序

    # dict_section_rank 用于判断声明顺序是否回退。
    dict_section_rank = {  # 协议 section 顺序比较表
        str_section: int_index  # section 名称到顺序号
        for int_index, str_section in enumerate(tuple_sections)  # 保留 rulebook 声明次序
    }

    # list_seen_sections 提供 VG057 判断 section 是否回退的端口序列。
    list_seen_sections = _protocol_seen_sections(list_ports, str_protocol, dict_section_rank)  # 协议端口 section 扫描结果

    # 没有当前协议端口时无需检查。
    if not list_seen_sections:

        # 当前 module 未使用该协议。
        return []

    # protocol_context 集中保存 VG057 报告需要的排序证据。
    protocol_context = ProtocolOrderIssueContext(  # 封装 VG057 的 rank 表、合法顺序、路径和 strict
        dict_section_rank=dict_section_rank,  # section 名称到 rank 的比较表
        tuple_sections=tuple_sections,  # rulebook 声明的合法 section 次序
        str_protocol=str_protocol,  # 正在检查的协议族标识
        str_rel_path=str_rel_path,  # 触发诊断的 Verilog 相对路径
        strict=strict,  # 是否按交付门禁升级为 error
    )

    # 返回第一处 section 顺序回退诊断。
    return _protocol_order_violation_issue(list_seen_sections, protocol_context)

# 供 `_protocol_seen_sections` 复用的拆分 helper，专门处理按源码顺序收集属于指定协议的端口 section。
def _protocol_seen_sections(
    list_ports: list[dict[str, Any]],
    str_protocol: str,
    dict_section_rank: dict[str, int],
) -> list[tuple[str, str, int | None]]:
    """
    按源码顺序收集属于指定协议的端口 section。

    :param list_ports: formatter AST 中保持源码顺序的端口列表。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :param dict_section_rank: 当前协议 section 到顺序号的映射。
    :return: 端口名、section 和行号组成的扫描结果。
    """

    # list_seen_sections 承载后续 rank 回退检测所需的端口序列。
    list_seen_sections: list[tuple[str, str, int | None]] = []  # 协议端口声明顺序

    # 逐端口归类到 protocol section。
    for dict_port in list_ports:

        # str_port_name 是 formatter AST 解析出的端口名。
        str_port_name = str(dict_port.get("name") or "")  # 当前端口名

        # str_section 为空说明该端口不属于当前协议。
        str_section = _protocol_port_section(str_port_name, str_protocol)  # 当前端口所属协议 section

        # rulebook 未声明的 section 按协议 fallback 规则归一。
        str_section = _normalised_protocol_section(str_section, dict_section_rank)  # 可参与排序的 section

        # 非当前协议端口不参与排序。
        if not str_section:

            # 用户普通端口不等同于协议 other section。
            continue

        # 记录端口名、section 和 AST 行号。
        list_seen_sections.append((str_port_name, str_section, _as_line(dict_port.get("line_start"))))

    # 返回按声明顺序收集的协议端口。
    return list_seen_sections

# 供 `_normalised_protocol_section` 复用的拆分 helper，专门处理协议分类结果规范到当前 rulebook 的 section 集合。
def _normalised_protocol_section(str_section: str, dict_section_rank: dict[str, int]) -> str:
    """
    把协议分类结果规范到当前 rulebook 的 section 集合。

    :param str_section: 协议端口分类结果。
    :param dict_section_rank: 当前协议 section 到顺序号的映射。
    :return: 可参与排序的 section；不应参与时返回空字符串。
    """

    # 空 section 表示端口不属于当前协议。
    if not str_section:

        # 不参与协议排序。
        return ""

    # rulebook 已声明的 section 可直接使用。
    if str_section in dict_section_rank:

        # 保留原始分类结果。
        return str_section

    # AXI/AXIS 未识别协议端口可归入 other section。
    if "other" in dict_section_rank:

        # 使用 other 维持保守排序检查。
        return "other"

    # APB 等无 other section 的协议忽略未知专名端口。
    return ""

# 供 `_protocol_order_violation_issue` 复用的拆分 helper，专门处理协议端口 section 顺序中的第一处回退诊断。
def _protocol_order_violation_issue(
    list_seen_sections: list[tuple[str, str, int | None]],
    protocol_context: ProtocolOrderIssueContext,
) -> list[QualityIssue]:
    """
    返回协议端口 section 顺序中的第一处回退诊断。

    :param list_seen_sections: 端口名、section 和行号组成的扫描结果。
    :param protocol_context: 协议端口排序诊断上下文。
    :return: 至多一条协议顺序诊断。
    """

    # int_last_rank 保存此前出现过的最大 section rank。
    int_last_rank = -1  # 当前扫描到的最高 section 排名

    # str_last_section 用于错误消息指出回退边界。
    str_last_section = ""  # 上一个最高 section 名称

    # 按端口声明顺序查找 section rank 回退。
    for str_port_name, str_section, int_line in list_seen_sections:

        # int_rank 是当前端口所属 section 的排序等级。
        int_rank = protocol_context.dict_section_rank[str_section]  # 当前端口 section 排名

        # rank 回退说明端口分组顺序违反 rulebook。
        if int_rank < int_last_rank:

            # 协议端口顺序错误影响接口扫描和 wrapper 适配。
            return [
                QualityIssue(
                    "VG057",
                    _style_severity(protocol_context.strict),
                    (
                        f"{protocol_context.str_protocol.upper()} port `{str_port_name}` is in section "
                        f"`{str_section}` after `{str_last_section}`; expected order is "
                        f"{' -> '.join(protocol_context.tuple_sections)}."
                    ),
                    protocol_context.str_rel_path,
                    int_line,
                    "protocol.port_order",
                )
            ]

        # 更新已见最大 rank 和对应 section。
        if int_rank > int_last_rank:

            # 同步最高 rank，后续低 rank 端口会据此判定回退。
            int_last_rank = int_rank  # 已见最大 section rank

            # 记录最高 rank 的 section 名称，便于 VG057 展示前序分组。
            str_last_section = str_section  # 已见最大 section 名称

    # 没有 section 回退时通过。
    return []

# 供 `_protocol_sections` 复用的拆分 helper，专门处理协议端口 section 的合法顺序。
def _protocol_sections(str_protocol: str) -> tuple[str, ...]:
    """
    返回协议端口 section 的合法顺序。

    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :return: section 名称元组。
    """

    # dict_fallback_sections 保留 rulebook 读取失败时的内置保守顺序。
    dict_fallback_sections = {  # 协议 section fallback 顺序
        "axi": ("clock_reset", "aw", "w", "b", "ar", "r", "other"),  # AXI memory mapped 通道顺序
        "axis": ("clock_reset", "slave", "master", "control", "data", "other"),  # AXIS 端点先分组再归入通用信号
        "apb": ("clock_reset", "request", "response"),  # APB 请求先于响应
    }

    # 读取 rulebook 的 protocols 配置。
    try:

        # dict_protocols 是机器规则源中的协议配置。
        dict_protocols = load_verilog_rulebook().raw.get("protocols") or {}  # rulebook 协议配置

        # tuple_rulebook_sections 取出目标协议 section 列表。
        tuple_rulebook_sections = dict_protocols.get(f"{str_protocol}_sections") or ()  # 目标协议 section 配置

        # 非空配置优先使用 rulebook。
        if tuple_rulebook_sections:

            # 返回不可变元组，避免调用方修改规则表。
            return tuple(str(item) for item in tuple_rulebook_sections)

    # rulebook 失败时让 VG059 另行报告，这里只保持协议检查可运行。
    except Exception:

        # tuple_rulebook_sections 置空后会自然落回内置协议顺序。
        tuple_rulebook_sections = ()  # rulebook 异常后的协议 section 空配置

    # 返回保守 fallback，未知协议返回空元组。
    return dict_fallback_sections.get(str_protocol, ())

# 供 `_protocol_port_section` 复用的拆分 helper，专门处理端口名在指定协议中的 section。
def _protocol_port_section(str_port_name: str, str_protocol: str) -> str:
    """
    判断端口名在指定协议中的 section。

    :param str_port_name: Verilog 端口名。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :return: section 名称；不属于该协议时返回空字符串。
    """

    # str_lower_name 用于大小写无关的协议名和信号名判断。
    str_lower_name = str_port_name.lower()  # 归一化端口名

    # AXIS 必须先判断，避免 axis 被 axi 子串误归类。
    if str_protocol == "axis":

        # 返回 AXIS 数据或控制 section。
        return _axis_port_section(str_lower_name)

    # AXI memory-mapped 端口不能误吞 AXIS。
    if str_protocol == "axi":

        # 返回 AXI channel section。
        return _axi_port_section(str_lower_name)

    # APB 端口按 request/response 分类。
    if str_protocol == "apb":

        # APB 标准端口名映射到 request/response section。
        return _apb_port_section(str_lower_name)

    # 未知协议不参与检查。
    return ""

# 供 `_axi_port_section` 复用的拆分 helper，专门处理AXI memory-mapped 端口 section。
def _axi_port_section(str_lower_name: str) -> str:
    """
    判断 AXI memory-mapped 端口 section。

    :param str_lower_name: 小写端口名。
    :return: AXI section；非 AXI 端口返回空字符串。
    """

    # AXIS 端口不属于 AXI memory-mapped。
    if "axis" in str_lower_name:

        # 避免 axis 中的 axi 子串误报。
        return ""

    # 只处理名字里明确包含 axi 的端口。
    if "axi" not in str_lower_name:

        # 普通用户端口不属于 AXI。
        return ""

    # 时钟和复位属于 clock_reset section。
    if _is_protocol_clock_or_reset(str_lower_name):

        # protocol clock/reset 必须排在最前。
        return "clock_reset"

    # tuple_parts 按下划线切分，用于识别 awaddr/wdata 等 channel 前缀。
    tuple_parts = tuple(part for part in re.split(r"[^a-z0-9]+", str_lower_name) if part)  # 端口名分段

    # AXI 通道前缀顺序和 rulebook sections 一致。
    for str_section in ("aw", "w", "b", "ar", "r"):

        # 任一段以通道名开头即归入该通道。
        if any(str_part.startswith(str_section) for str_part in tuple_parts):

            # 返回识别到的 AXI 通道 section。
            return str_section

    # 明确属于 AXI 但未归类的端口归入 other。
    return "other"

# 细分 AXI Stream 端口，供分组顺序检查区分 data/control 片段。
def _axis_port_section(str_lower_name: str) -> str:
    """
    判断 AXI Stream 端口 section。

    :param str_lower_name: 小写端口名。
    :return: AXIS section；非 AXIS 端口返回空字符串。
    """

    # AXIS 常见命名包含 axis 或 tdata/tvalid 等 stream 信号。
    bool_axis_named = "axis" in str_lower_name or _contains_any_token(str_lower_name, AXIS_PORT_TOKENS)  # AXIS 端口命名证据

    # 非 AXIS 端口不参与 AXIS 排序。
    if not bool_axis_named:

        # 当前端口不是 AXIS。
        return ""

    # AXIS 时钟复位必须排在最前。
    if _is_protocol_clock_or_reset(str_lower_name):

        # AXIS 时钟复位归入 clock_reset section。
        return "clock_reset"

    # str_endpoint_side 用于支持 s_axis/m_axis 端点成组的常见端口布局。
    str_endpoint_side = _axis_endpoint_side(str_lower_name)  # AXIS slave/master 端点侧别

    # 明确带 s_axis/m_axis 前缀的端口按端点侧别排序，不强制侧内 handshake/data 顺序。
    if str_endpoint_side:

        # 返回 slave 或 master section。
        return str_endpoint_side

    # tdata/tkeep/tstrb 是数据通道。
    if _contains_any_token(str_lower_name, AXIS_DATA_TOKENS):

        # AXIS payload 和 byte-enable 信号归入 data section。
        return "data"

    # 其余 AXIS 握手、帧尾和用户信息归入 control。
    if _contains_any_token(str_lower_name, AXIS_CONTROL_TOKENS):

        # AXIS 握手、帧尾和旁带信号归入 control section。
        return "control"

    # AXIS 专名端口未命中数据或控制 token 时归入 other。
    return "other"

# 供 `_axis_endpoint_side` 复用的拆分 helper，专门处理AXIS 端口名中的端点侧别。
def _axis_endpoint_side(str_lower_name: str) -> str:
    """
    返回 AXIS 端口名中的端点侧别。

    :param str_lower_name: 小写端口名。
    :return: slave、master 或空字符串。
    """

    # tuple_endpoint_rules 按 rulebook 顺序覆盖短前缀和长前缀两种端点写法。
    tuple_endpoint_rules = (  # AXIS 端点侧别识别规则
        ("slave", r"(^|_)s_axis(_|$)", "slave_axis"),  # slave 端点短前缀和长前缀
        ("master", r"(^|_)m_axis(_|$)", "master_axis"),  # 下游输出端点命名
    )

    # 按 slave、master 顺序识别显式端点分组。
    for str_section, str_short_pattern, str_long_token in tuple_endpoint_rules:

        # bool_short_endpoint_match 保证 s_axis 不会误命中普通字符串中间片段。
        bool_short_endpoint_match = re.search(str_short_pattern, str_lower_name) is not None  # 端点短前缀边界命中

        # bool_long_endpoint_match 兼容 slave_axis/master_axis 长前缀写法。
        bool_long_endpoint_match = str_long_token in str_lower_name  # 端点长前缀命中

        # bool_endpoint_match 汇总短前缀和长前缀两类端点命名。
        bool_endpoint_match = bool_short_endpoint_match or bool_long_endpoint_match  # 显式 AXIS 端点命中

        # 命中后返回端点级 section，侧内 tvalid/tdata 不再强排。
        if bool_endpoint_match:

            # 返回 rulebook 可排序的端点 section。
            return str_section

    # 不带端点侧别的 AXIS 端口交由 data/control fallback 分类。
    return ""

# 根据 APB 请求侧与响应侧 token，把端口映射到稳定 section 名。
def _apb_port_section(str_lower_name: str) -> str:
    """
    判断 APB 端口 section。

    :param str_lower_name: 小写端口名。
    :return: APB section；非 APB 端口返回空字符串。
    """

    # APB 端口可能显式包含 apb，也可能只使用 paddr/psel 等标准名。
    bool_apb_named = "apb" in str_lower_name or _contains_any_token(str_lower_name, APB_PORT_TOKENS)  # APB 前缀或 P* 标准信号命中标志

    # 普通用户端口不进入 APB section 顺序检查。
    if not bool_apb_named:

        # 空 section 表示该端口不参与 APB 排序。
        return ""

    # APB clock/reset 归入最前置 section。
    if _is_protocol_clock_or_reset(str_lower_name):

        # pclk/preset/prst 类信号必须早于请求和响应通道。
        return "clock_reset"

    # request 信号由 master 发起。
    if _contains_any_token(str_lower_name, APB_REQUEST_TOKENS):

        # 地址、选择、写数据和保护信号归入 request section。
        return "request"

    # response 信号由 slave 返回。
    if _contains_any_token(str_lower_name, APB_RESPONSE_TOKENS):

        # 读数据、ready 和错误信号归入 response section。
        return "response"

    # 未识别的 APB 端口不参与排序。
    return ""

# 供 `_contains_any_token` 复用的拆分 helper，专门处理小写端口名是否包含任一协议 token。
def _contains_any_token(str_lower_name: str, tuple_tokens: tuple[str, ...]) -> bool:
    """
    判断小写端口名是否包含任一协议 token。

    :param str_lower_name: 已转成小写的 Verilog 端口名。
    :param tuple_tokens: 协议端口名 token 集合。
    :return: 任一 token 出现在端口名中时返回 True。
    """

    # bool_has_token 复用协议分类中的任一 token 命中语义。
    bool_has_token = any(str_token in str_lower_name for str_token in tuple_tokens)  # 协议 token 命中标志

    # 返回 token 命中结果。
    return bool_has_token

# 供 `_is_protocol_clock_or_reset` 复用的拆分 helper，专门处理端口名是否表达协议 clock/reset。
def _is_protocol_clock_or_reset(str_lower_name: str) -> bool:
    """
    判断端口名是否表达协议 clock/reset。

    :param str_lower_name: 小写端口名。
    :return: clock 或 reset 端口返回 True。
    """

    # 常见 clock/reset token 覆盖 AXI、AXIS、APB、AHB 等命名。
    return any(str_token in str_lower_name for str_token in ("clk", "clock", "rst", "reset", "areset", "preset"))

# 供 `_extract_header_field` 复用的拆分 helper，专门处理从 module 前文件头中读取指定字段的首个值 token。
def _extract_header_field(str_pre_module: str, str_field: str) -> str:
    """
    从 module 前文件头中读取指定字段的首个值 token。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_field: 需要提取的文件头字段名。
    :return: 字段值的首个非空 token，未找到时返回空字符串。
    """

    # 字段行统一形如 // Field: value 或 // 字段: value。
    str_pattern = rf"(?m)^\s*//\s*{re.escape(str_field)}\s*:\s*(?P<value>.*?)\s*$"  # 文件头字段匹配正则

    # obj_match 定位字段行。
    obj_match = re.search(str_pattern, str_pre_module)  # 文件头字段匹配对象

    # 找不到字段时返回空字符串。
    if obj_match is None:

        # 缺字段由 VG007 负责报告。
        return ""

    # str_value 去掉外侧空白，便于从 tab 对齐字段中提取模块名。
    str_value = obj_match.group("value").strip()  # 字段原始值

    # 文件头字段可能保留制表对齐，只取第一个非空 token。
    list_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str_value)  # 字段中的 Verilog 标识符候选

    # 返回首个标识符候选。
    return list_tokens[0] if list_tokens else ""

# 供 `_reset_semantic_rules` 复用的拆分 helper，专门处理检查时序 always 的 reset 条件和低有效命名是否一致。
def _reset_semantic_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查时序 always 的 reset 条件和低有效命名是否一致。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: reset 深语义诊断列表。
    """

    # list_issues 保存 reset 条件诊断。
    list_issues: list[QualityIssue] = []  # reset 深语义诊断集合

    # 逐个时序 always 检查 reset 分支。
    for dict_always in dict_module.get("always", []) or []:

        # 单个 always 的 reset 极性和覆盖检查独立完成。
        list_issues.extend(_reset_semantic_issues_for_always(dict_always, str_rel_path, strict=strict))

    # 返回 reset 深语义诊断。
    return list_issues

# 供 `_reset_semantic_issues_for_always` 复用的拆分 helper，专门处理检查单个时序 always 的低有效 reset 分支。
def _reset_semantic_issues_for_always(
    dict_always: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个时序 always 的低有效 reset 分支。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 reset 语义问题升级为 error。
    :return: 当前 always 的 reset 语义诊断列表。
    """

    # 只检查 formatter 确认为时序块的 always。
    if dict_always.get("trigger_kind") != "seq":

        # 组合块不涉及 reset 极性。
        return []

    # str_reset 是 formatter 从敏感列表和 if 分支推断出的复位名。
    str_reset = str(dict_always.get("reset") or "")  # 时序块复位信号名

    # 没有 reset 或不是低有效命名时交给旧 VG021 处理。
    if not str_reset or not _is_low_active_reset_name(str_reset):

        # 本规则只判断低有效 reset 的分支条件是否反向。
        return []

    # int_base_line 是 always 起始行，行内偏移用于精确报告 reset 条件位置。
    int_base_line = _as_line(dict_always.get("line_start")) or 1  # always 块起始行号

    # list_always_lines 保持 formatter AST 提供的 always 内部源码行。
    list_always_lines = list(dict_always.get("lines", []) or [])  # always 内部源码行

    # int_reset_offset 定位 reset 分支 if 所在的 always 内部行。
    int_reset_offset = _find_reset_condition_offset(list_always_lines, str_reset)  # reset 条件行偏移

    # 找不到 reset if 条件时，敏感列表和正文不一致。
    if int_reset_offset is None:

        # 复位敏感列表没有对应复位分支会导致寄存器覆盖缺失。
        return [
            QualityIssue(
                "VG053",
                _style_severity(strict),
                f"Sequential always declares reset `{str_reset}` but no matching reset branch was found.",
                str_rel_path,
                int_base_line,
                rule="reset.coverage",
            )
        ]

    # str_code_line 用于忽略注释中的 reset 名称。
    str_code_line = _strip_line_comment(str(list_always_lines[int_reset_offset]))  # 去注释后的 reset 条件行

    # 条件符合低有效语义时通过。
    if _active_low_reset_condition_is_correct(str_code_line, str_reset):

        # reset 条件极性与命名一致。
        return []

    # 错误极性会导致复位覆盖语义反转。
    return [
        QualityIssue(
            "VG053",
            _style_severity(strict),
            f"Reset `{str_reset}` is low-active, but the reset branch condition is not low-active.",
            str_rel_path,
            int_base_line + int_reset_offset,
            rule="reset.condition_polarity",
        )
    ]

# 供 `_find_reset_condition_offset` 复用的拆分 helper，专门处理查找 always 内部首个 reset 条件行偏移。
def _find_reset_condition_offset(list_always_lines: list[Any], str_reset: str) -> int | None:
    """
    查找 always 内部首个 reset 条件行偏移。

    :param list_always_lines: always 内部源码行列表。
    :param str_reset: 低有效复位信号名。
    :return: reset 条件行偏移；未找到时返回 None。
    """

    # 遍历 always 内部行，查找 reset 分支 if。
    for int_offset, str_line in enumerate(list_always_lines):

        # 去注释文本避免注释中的 reset 名称触发覆盖判断。
        str_code_line = _strip_line_comment(str(str_line))  # 去注释后的 always 内部行

        # reset 条件通常在包含 if 和 reset 名的第一行。
        if str_reset in str_code_line and "if" in str_code_line:

            # 返回 reset 条件相对 always 起始行的偏移。
            return int_offset

    # 没有找到 reset 条件。
    return None

# 供 `_active_low_reset_condition_is_correct` 复用的拆分 helper，专门处理reset 条件行是否符合低有效复位约定。
def _active_low_reset_condition_is_correct(str_line: str, str_reset: str) -> bool:
    """
    判断 reset 条件行是否符合低有效复位约定。

    :param str_line: 去注释后的 Verilog 条件行。
    :param str_reset: 低有效复位信号名。
    :return: 条件表达低有效复位时返回 True。
    """

    # str_compact 去掉空白，统一比较不同代码风格。
    str_compact = re.sub(r"\s+", "", str_line)  # 去空白后的条件行

    # str_reset_pattern 是复位信号名的正则转义版本。
    str_reset_pattern = re.escape(str_reset)  # reset 名称正则

    # !rstn 或 ~rstn 是最直接的低有效判断。
    if re.search(rf"if\((?:!|~){str_reset_pattern}\)", str_compact):

        # 直接取反形式通过检查。
        return True

    # rstn == 0、rstn == 1'b0、rstn === 1'b0 等形式均表示低有效。
    tuple_low_patterns = (  # 可接受的低有效比较形式
        rf"{str_reset_pattern}={{2,3}}(?:1'b0|1'h0|1'd0|0)",  # reset 信号在比较左侧
        rf"(?:1'b0|1'h0|1'd0|0)={{2,3}}{str_reset_pattern}",  # 低电平常量在比较左侧
    )

    # 任一低有效比较形式命中即可通过。
    return any(re.search(str_pattern, str_compact, re.IGNORECASE) for str_pattern in tuple_low_patterns)

# 供 `_region_ownership_rules` 复用的拆分 helper，专门处理检查关键 AST 节点的源码行是否归属正确区域。
def _region_ownership_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查关键 AST 节点的源码行是否归属正确区域。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 区域归属诊断列表。
    """

    # list_issues 保存 AST 区域归属诊断。
    list_issues: list[QualityIssue] = []  # 区域归属诊断集合

    # dict_region_by_line 记录每个区域横幅出现的行号。
    dict_region_by_line = _line_region_titles(str_text)  # 源码中的区域横幅行

    # 没有任何横幅时由 VG031 负责报告。
    if not dict_region_by_line:

        # 无法判断具体 AST 归属。
        return list_issues

    # 输出端口集合用于识别 output bridge assign。
    set_output_ports = _module_output_ports(dict_module)  # output bridge 目标端口集合

    # VG052 仍只检查 output bridge，不混入新的 VG061 通用归属。
    tuple_output_bridge_args = (dict_module, set_output_ports, dict_region_by_line, str_rel_path)  # VG052 位置参数

    # 调用旧专项检查器，保留原有输出连线错误文案。
    list_output_bridge_issues = _output_assign_region_issues(*tuple_output_bridge_args, strict=strict)  # 输出桥接诊断

    # VG052 保持兼容，避免输出连线规则编号漂移。
    list_issues.extend(list_output_bridge_issues)

    # VG061 使用 module、区域索引和输出端口集合推导通用结构归属。
    tuple_general_region_args = (dict_module, dict_region_by_line, set_output_ports, str_rel_path)  # 通用归属位置参数

    # 调用新增通用检查器，补齐参数、声明和过程块区域归属。
    list_general_region_issues = _general_region_ownership_issues(*tuple_general_region_args, strict=strict)  # 通用归属诊断

    # VG061 覆盖参数、声明、过程块、实例化等通用归属。
    list_issues.extend(list_general_region_issues)

    # VG070/VG071 直接把模块接口输出当作真源，校验输出声明区、桥接区和处理区的分组标签与顺序是否逐字镜像。
    list_issues.extend(
        _output_mirror_rules(
            dict_module,  # 当前 module AST
            dict_region_by_line,  # 区域横幅索引
            str_rel_path,  # 当前 Verilog 相对路径
            strict=strict,  # 复用 strict 决定是否阻断
        )
    )

    # 返回区域归属诊断。
    return list_issues

# 供 `_general_region_ownership_issues` 复用的拆分 helper，专门处理检查参数、声明、assign、过程块和实例化的区域归属。
def _general_region_ownership_issues(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_output_ports: set[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查参数、声明、assign、过程块和实例化的区域归属。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_output_ports: 顶层 output 端口名集合。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :return: 通用区域归属诊断列表。
    """

    # list_issues 保存 VG061 通用区域归属诊断。
    list_issues: list[QualityIssue] = []  # 通用区域归属诊断集合

    # localparam、声明、assign 和 always 的期望区域由专门迭代器统一给出。
    for region_item in _iter_region_expectations(dict_module, set_output_ports):

        # 每个期望项只产生零条或一条 VG061。
        list_issues.extend(
            _region_owner_issue_for_item(
                region_item["item"],
                region_item["label"],
                region_item["regions"],
                dict_region_by_line,
                str_rel_path,
                strict=strict,
                str_rule=region_item["rule"],
            )
        )

    # generate、initial、function、task 和实例化使用固定区域。
    for region_item in _iter_fixed_region_expectations(dict_module):

        # 固定结构直接携带期望区域，避免在主循环里重复分支。
        list_issues.extend(
            _region_owner_issue_for_item(
                region_item["item"],
                region_item["label"],
                region_item["regions"],
                dict_region_by_line,
                str_rel_path,
                strict=strict,
                str_rule=region_item["rule"],
            )
        )

    # 返回通用区域归属诊断。
    return list_issues

# 供 `_iter_fixed_region_expectations` 复用的拆分 helper，专门处理generate、initial、function、task 和实例化的区域期望项。
def _iter_fixed_region_expectations(dict_module: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回 generate、initial、function、task 和实例化的区域期望项。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 可直接传给区域归属检查的固定期望项列表。
    """

    # list_items 汇总不依赖命名推导的固定 AST 归属规则。
    list_items: list[dict[str, Any]] = []  # 固定 AST 区域期望项

    # generate 块只允许出现在生成块区域。
    tuple_generate_check = ("generates", ("生成块区域",), "regions.generate")  # generate 块区域规则

    # initial 块允许用于初始化或参数检查前置断言。
    tuple_initial_check = ("initials", ("初始化区域", "参数检查区域"), "regions.initial")  # initial 区域规则

    # function 定义兼容历史名称和当前规范区域。
    tuple_function_check = ("functions", ("函数区域", "函数定义区域"), "regions.function")  # 函数定义标题兼容映射

    # task 定义兼容普通任务和状态任务区域。
    tuple_task_check = ("tasks", ("任务区域", "任务定义区域", "状态任务处理区域"), "regions.task")  # task AST 归属映射

    # 子模块实例化必须留在实例化区域。
    tuple_instance_check = ("instances", ("模块实例化区域",), "regions.instance")  # 实例化区域规则

    # 固定结构检查先从 generate 规则开始。
    list_fixed_region_checks = [tuple_generate_check]  # 固定结构区域规则表

    # initial 规则保持在 generate 后，贴近规范区域顺序。
    list_fixed_region_checks += [tuple_initial_check]  # initial 结构区域规则

    # function 规则覆盖工具函数定义。
    list_fixed_region_checks += [tuple_function_check]  # function 固定检查入口

    # task 追加在 function 之后，保持工具过程定义的检查顺序。
    list_fixed_region_checks += [tuple_task_check]  # 任务定义检查入口

    # 实例化规则最后追加，便于和主要逻辑区域分离。
    list_fixed_region_checks += [tuple_instance_check]  # 补充实例化规则

    # 逐个 AST 集合生成统一结构，供 VG061 复用。
    for str_collection_name, tuple_expected_regions, str_rule in list_fixed_region_checks:

        # 当前集合的每个条目共享同一个规范区域集合。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # 固定结构的诊断标签优先使用 AST span 推导结果。
            list_items.append(
                {
                    "item": dict_item,
                    "label": _span_item_label(dict_item),
                    "regions": tuple_expected_regions,
                    "rule": str_rule,
                }
            )

    # 返回固定结构区域期望。
    return list_items

# 汇总一个 module 中 localparam、声明、assign 与 always 的期望区域顺序。
def _iter_region_expectations(
    dict_module: dict[str, Any],
    set_output_ports: set[str],
) -> list[dict[str, Any]]:
    """
    返回 localparam、声明、assign 和 always 的区域期望项。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param set_output_ports: 顶层 output 端口名集合。
    :return: 可直接传给区域归属检查的期望项列表。
    """

    # list_items 保存动态结构的区域期望。
    list_items: list[dict[str, Any]] = []  # 动态 AST 区域期望项

    # localparam 是实际出现在 module body 区域中的参数实体。
    for dict_param in dict_module.get("localparams", []) or []:

        # 当前 localparam 可能是状态编码或普通配置常量。
        list_items.append(_localparam_region_expectation(dict_param))

    # 内部声明按命名语义放入对应信号区域。
    for dict_decl in dict_module.get("decls", []) or []:

        # 当前声明的期望区域由名称和声明类型共同决定。
        list_items.append(
            {
                "item": dict_decl,
                "label": _span_item_label(dict_decl),
                "regions": _expected_decl_regions(dict_decl),
                "rule": "regions.declaration",
            }
        )

    # assign 按 output bridge 和普通连线分流。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 当前 assign 的期望区域由左值决定。
        list_items.append(_assign_region_expectation(dict_assign, set_output_ports))

    # always 块根据目标信号和状态引用分配到输出、状态机或主任务区域。
    for dict_always in dict_module.get("always", []) or []:

        # 当前 always 的 header 用于诊断定位。
        list_items.append(
            {
                "item": dict_always,
                "label": str(dict_always.get("header") or "always"),
                "regions": _expected_always_regions(dict_always),
                "rule": "regions.always",
            }
        )

    # 返回所有动态区域期望。
    return list_items

# 供 `_localparam_region_expectation` 复用的拆分 helper，专门处理构造单个 localparam 的区域期望项。
def _localparam_region_expectation(dict_param: dict[str, Any]) -> dict[str, Any]:
    """
    构造单个 localparam 的区域期望项。

    :param dict_param: formatter AST localparam 条目。
    :return: 区域期望项字典。
    """

    # str_name 用于区分状态参数和普通局部常量。
    str_name = str(dict_param.get("name") or "")  # localparam 区域判定名称

    # tuple_expected_regions 表示该 localparam 允许出现的区域。
    tuple_expected_regions = ("状态参数区域",) if str_name.startswith("ST_") else ("配置参数区域",)  # localparam 期望区域

    # localparam 期望项交给 VG061 的统一定位逻辑处理。
    return {
        "item": dict_param,
        "label": _span_item_label(dict_param),
        "regions": tuple_expected_regions,
        "rule": "regions.localparam",
    }

# 为单条 assign 构造区域期望对象，优先区分输出桥接赋值。
def _assign_region_expectation(dict_assign: dict[str, Any], set_output_ports: set[str]) -> dict[str, Any]:
    """
    构造单条 assign 的区域期望项。

    :param dict_assign: formatter AST assign 条目。
    :param set_output_ports: 顶层 output 端口名集合。
    :return: 区域期望项字典。
    """

    # str_lhs 用于判断 assign 是否直接驱动顶层输出。
    str_lhs = str(dict_assign.get("lhs") or "")  # assign 输出桥接判定左值

    # 输出端口桥接必须在专门连线区域。
    if str_lhs in set_output_ports or str_lhs.startswith("o_"):

        # tuple_expected_regions 指向 output bridge 规范区域。
        tuple_expected_regions = ("输出信号连线",)  # 输出桥接 assign 期望区域

    # 普通组合连线落入其他信号连线区域。
    else:

        # tuple_expected_regions 指向非 output bridge 连线区域。
        tuple_expected_regions = ("其他信号连线",)  # 普通 assign 期望区域

    # 当前 assign 的区域归属由统一定位逻辑生成最终 VG061 诊断。
    return {
        "item": dict_assign,
        "label": str_lhs or "assign",
        "regions": tuple_expected_regions,
        "rule": "regions.assign",
    }

# 供 `_region_owner_issue_for_item` 复用的拆分 helper，专门处理检查单个 AST 条目所在区域是否属于允许集合。
def _region_owner_issue_for_item(
    dict_item: dict[str, Any],
    str_label: str,
    tuple_expected_regions: tuple[str, ...],
    dict_region_by_line: dict[int, str],
    str_rel_path: str, *,
    strict: bool, str_rule: str,
) -> list[QualityIssue]:
    """
    检查单个 AST 条目所在区域是否属于允许集合。

    :param dict_item: formatter AST 条目。
    :param str_label: 诊断中展示的条目标签。
    :param tuple_expected_regions: 允许的区域标题集合。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :param str_rule: 规则子命名空间。
    :return: 当前条目的区域归属诊断列表。
    """

    # int_line_no 使用 AST 起始行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 区域归属定位行号

    # 缺少行号由 VG050 负责。
    if int_line_no is None:

        # 本规则无法定位无 span 条目。
        return []

    # str_region_title 是当前条目最近的区域横幅。
    str_region_title = _nearest_region_title(dict_region_by_line, int_line_no)  # 当前条目所属区域

    # 若当前行位于第一个区域前，保守跳过 module header 参数/端口等结构。
    if not str_region_title:

        # 没有区域上下文时不做归属判断。
        return []

    # 命中允许区域时通过。
    if str_region_title in tuple_expected_regions:

        # 区域归属符合预期。
        return []

    # 生成通用区域归属诊断。
    return [
        QualityIssue(
            "VG061",
            _style_severity(strict),
            f"Item `{str_label}` must be placed in {', '.join(tuple_expected_regions)}, "
            f"not `{str_region_title}`.",
            str_rel_path,
            int_line_no,
            rule=str_rule,
        )
    ]

# 供 `_expected_decl_regions` 复用的拆分 helper，专门处理内部声明允许出现的区域集合。
def _expected_decl_regions(dict_decl: dict[str, Any]) -> tuple[str, ...]:
    """
    返回内部声明允许出现的区域集合。

    :param dict_decl: formatter AST 内部声明条目。
    :return: 允许区域标题元组。
    """

    # str_name 用于按 Erie 命名前缀识别区域。
    str_name = str(dict_decl.get("name") or "")  # 内部声明名称

    # str_kind 表示 wire/reg/logic 等声明类型。
    str_kind = str(dict_decl.get("kind") or "")  # 内部声明类型

    # list_region_rules 按优先级保存声明名称与目标区域的映射。
    list_region_rules: list[tuple[bool, tuple[str, ...]]] = []  # 内部声明区域推断规则

    # 输出桥接内部信号必须进入输出信号区域。
    list_region_rules.append((str_name.endswith("_o"), ("输出信号",)))

    # 计数器前缀信号必须进入计数信号区域。
    list_region_rules.append((str_name.startswith("cnt_"), ("计数信号",)))

    # 状态寄存器前缀信号必须进入状态机信号区域。
    list_region_rules.append((str_name.startswith("state_"), ("状态机信号",)))

    # 握手、完成和请求类标志必须进入标志信号区域。
    list_region_rules.append((str_name.startswith("flag_"), ("标志信号",)))

    # 编码类命名或语义词命中时进入编码信号区域。
    list_region_rules.append((str_name.startswith("enc_") or _looks_encoder(str_name), ("编码信号",)))

    # 译码类命名或语义词命中时进入译码信号区域。
    list_region_rules.append((str_name.startswith("dec_") or _looks_decoder(str_name), ("译码信号",)))

    # 其他寄存器声明按寄存器信号区域处理。
    list_region_rules.append((str_name.startswith("reg_") or str_kind == "reg", ("寄存器信号",)))

    # 按优先级返回第一个命中的声明区域。
    for bool_matched, tuple_regions in list_region_rules:

        # 当前规则未命中时继续检查下一项。
        if not bool_matched:

            # 保持区域规则优先级顺序。
            continue

        # 返回当前命中的区域集合。
        return tuple_regions

    # 其他内部连线允许放入其他信号或实例化信号区。
    return ("其他信号", "模块实例化信号")

# 按 always 类型返回可接受的区域标题，用于 block 落区验证。
def _expected_always_regions(dict_always: dict[str, Any]) -> tuple[str, ...]:
    """
    返回 always 块允许出现的区域集合。

    :param dict_always: formatter AST always 条目。
    :return: 允许区域标题元组。
    """

    # set_targets 保存 always 的赋值目标。
    set_targets = {str(item) for item in dict_always.get("targets", []) or []}  # always 赋值目标集合

    # 输出桥接内部寄存器属于输出信号处理区域。
    if any(str_target.endswith("_o") for str_target in set_targets):

        # 输出处理 always 应靠近输出信号处理区域。
        return ("输出信号处理区域",)

    # 状态寄存器和 next-state 组合块属于状态机区域。
    if "state_current" in set_targets or "state_next" in set_targets:

        # FSM 前两段归入状态机区域。
        return ("状态机区域",)

    # 引用状态但不更新状态寄存器的第三段逻辑属于状态任务处理区域。
    if _always_references_state_task(dict_always):

        # FSM 第三段归入状态任务处理区域。
        return ("状态任务处理区域",)

    # 其他 always 默认属于主要任务处理区域。
    return ("主要任务处理区域",)

# 供 `_output_assign_region_issues` 复用的拆分 helper，专门处理检查 output bridge assign 是否位于输出信号连线区域。
def _output_assign_region_issues(
    dict_module: dict[str, Any],
    set_output_ports: set[str],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 output bridge assign 是否位于输出信号连线区域。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param set_output_ports: 顶层 output 端口名集合。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :return: output bridge 区域归属诊断列表。
    """

    # list_issues 保存 output bridge assign 的区域诊断。
    list_issues: list[QualityIssue] = []  # output bridge 区域诊断

    # region_context 保存单条 assign 区域判断所需的共享信息。
    region_context = OutputAssignRegionContext(  # VG052 output bridge 区域判定证据
        set_output_ports=set_output_ports,  # 用于识别 assign 是否驱动顶层输出
        dict_region_by_line=dict_region_by_line,  # 用于从 assign 行回溯最近横幅
        str_rel_path=str_rel_path,  # 写入 VG052 诊断的文件路径
        strict=strict,  # 控制 VG052 是否阻断交付
    )

    # 逐条 assign 判断 output bridge 归属，避免普通内部连线误报。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 单条 assign helper 返回空列表或一条 VG052。
        list_issues.extend(_output_assign_region_issues_for_assign(dict_assign, region_context))

    # 返回 output bridge 区域诊断。
    return list_issues

# 供 `_output_assign_region_issues_for_assign` 复用的拆分 helper，专门处理检查单条 assign 是否违反 output bridge 区域归属。
def _output_assign_region_issues_for_assign(
    dict_assign: dict[str, Any],
    region_context: OutputAssignRegionContext,
) -> list[QualityIssue]:
    """
    检查单条 assign 是否违反 output bridge 区域归属。

    :param dict_assign: formatter AST 中的 assign 条目。
    :param region_context: output bridge assign 区域判断上下文。
    :return: 当前 assign 的区域归属诊断列表。
    """

    # str_lhs 标识当前 assign 是否正在驱动 output bridge。
    str_lhs = str(dict_assign.get("lhs") or "")  # output bridge 连续赋值左侧信号

    # 只检查 output bridge 语义的 assign。
    if str_lhs not in region_context.set_output_ports and not str_lhs.startswith("o_"):

        # 普通连线不属于输出桥接强规则。
        return []

    # int_line_no 用于把区域归属问题定位到 assign 起始行。
    int_line_no = _as_line(dict_assign.get("line_start"))  # output bridge assign 的源码起始行

    # 无行号时由 VG050 报告。
    if int_line_no is None:

        # 本规则依赖行号，缺失时跳过避免重复噪音。
        return []

    # str_region_title 是该 assign 前最近的区域横幅。
    str_region_title = _nearest_region_title(region_context.dict_region_by_line, int_line_no)  # assign 当前区域

    # 输出桥接位于正确区域时通过。
    if str_region_title == "输出信号连线":

        # assign 区域归属符合规范。
        return []

    # 区域归属错误会影响 formatter/审查对输出桥接的识别。
    return [
        QualityIssue(
            "VG052",
            _style_severity(region_context.strict),
            f"Output bridge assign `{str_lhs}` must be placed in 输出信号连线, "
            f"not `{str_region_title or 'unknown'}`.",
            region_context.str_rel_path,
            int_line_no,
            rule="regions.output_assign",
        )
    ]

# 供 `_output_mirror_rules` 复用的拆分 helper，专门处理检查输出信号、输出桥接和输出处理区域是否镜像模块接口输出定义。
def _output_mirror_rules(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查输出信号、输出桥接和输出处理区域是否镜像模块接口输出定义。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 输出镜像相关诊断列表。
    """

    # 先从模块接口抽取输出镜像真源。
    list_expected_outputs = _expected_output_mirror_items(dict_module)  # 模块接口输出镜像真源列表。

    # 没有顶层输出端口时不需要继续执行镜像检查。
    if not list_expected_outputs:

        # 空输出模块不会产生输出镜像诊断。
        return []

    # dict_expected_by_signal 保存内部桥接信号到真源条目的映射。
    dict_expected_by_signal: dict[str, dict[str, Any]] = {}  # 供输出声明区和输出处理区按 signal 回查接口真源。

    # 先按内部桥接 signal 建立输出声明区和处理区共用的索引。
    for dict_expected_output in list_expected_outputs:

        # 读取桥接信号键，缺键时无法把内部 `_o` 信号映射回接口输出。
        str_signal_name = str(dict_expected_output.get("signal") or "")  # 输出声明区和输出处理区共用的 signal 键。

        # 缺少内部桥接信号名时跳过当前真源条目。
        if not str_signal_name:

            # 空信号名不能作为信号级真源索引。
            continue

        # 用桥接键名回写声明区和处理区复用的真源条目。
        dict_expected_by_signal[str_signal_name] = dict_expected_output  # 让 signal 键能直接回到对应接口输出。

    # dict_expected_by_port 保存顶层输出端口到真源条目的映射。
    dict_expected_by_port: dict[str, dict[str, Any]] = {}  # 供输出桥接区按 port 回查接口真源。

    # 再按顶层输出 port 建立输出桥接区独有的索引。
    for dict_expected_output in list_expected_outputs:

        # 读取顶层输出 port 键，缺键时无法校验输出桥接 assign 的镜像关系。
        str_port_name = str(dict_expected_output.get("port") or "")  # 输出桥接区用来回查接口真源的 port 键。

        # 缺少顶层输出端口名时跳过当前真源条目。
        if not str_port_name:

            # 空端口名不能作为端口级真源索引。
            continue

        # 用端口键名回写输出桥接区域复用的真源条目。
        dict_expected_by_port[str_port_name] = dict_expected_output  # 把顶层输出端口名绑定到镜像真源，供桥接 assign 直接回查。

    # set_group_labels 保存接口真源允许切换到的组标签。
    set_group_labels: set[str] = set()  # 供输出三区识别合法切组时机的接口标签白名单。

    # 只收集接口真源中显式出现过的组标签。
    for dict_expected_output in list_expected_outputs:

        # 读取接口分组标签，供输出三区在注释切组时对齐接口文本。
        str_group_label = str(dict_expected_output.get("group_label") or "")  # 接口输出定义里显式声明的组标签文本。

        # 空组标签不参与切组白名单。
        if not str_group_label:

            # 无标签真源不应该扩大切组集合。
            continue

        # 把当前显式分组标签登记到输出镜像切组白名单。
        set_group_labels.add(str_group_label)  # 允许输出三区只切换到接口里真实存在的标签。

    # 把信号级真源索引键折叠成集合，供声明区和处理区筛选复用。
    set_signal_names = set(dict_expected_by_signal)  # 可映射回顶层输出的内部桥接信号集合。

    # 把端口级真源索引键折叠成集合，供输出桥接区域筛选复用。
    set_port_names = set(dict_expected_by_port)  # 需要镜像比较的顶层输出端口集合。

    # 先收集输出信号区域的实际条目。
    list_decl_items = _output_decl_mirror_items(  # 从输出信号区域抽取 signal 级镜像条目。
        dict_module, dict_region_by_line, set_signal_names, set_group_labels)  # 只保留可映射回接口输出的 `_o` 声明。

    # 再收集输出信号连线区域的实际条目。
    list_assign_items = _output_assign_mirror_items(  # 从输出桥接区域抽取 port 级镜像条目。
        dict_module, dict_region_by_line, set_port_names, set_group_labels)  # 只保留真正驱动顶层输出端口的桥接 assign。

    # 最后收集输出信号处理区域的实际条目。
    list_always_items = _output_always_mirror_items(  # 从输出处理区域抽取 always 级镜像条目。
        dict_module, dict_region_by_line, set_signal_names, set_group_labels)  # 只保留会驱动输出桥接信号的处理块。

    # list_issues 汇总所有输出镜像诊断。
    list_issues: list[QualityIssue] = []  # 输出镜像诊断集合。

    # 先比较输出信号区域的组标签逐字一致性。
    list_issues.extend(
        _output_group_label_mirror_issues(
            list_decl_items,
            dict_expected_by_signal,
            "输出信号",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 再比较输出信号区域的条目顺序是否与接口一致。
    list_issues.extend(
        _output_order_mirror_issues(
            list_expected_outputs,
            list_decl_items,
            "输出信号",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 接着比较输出信号连线区域的组标签逐字一致性。
    list_issues.extend(
        _output_group_label_mirror_issues(
            list_assign_items,
            dict_expected_by_port,
            "输出信号连线",
            "port",
            str_rel_path,
            strict=strict,
        )
    )

    # 然后比较输出信号连线区域的条目顺序是否与接口一致。
    list_issues.extend(
        _output_order_mirror_issues(
            list_expected_outputs,
            list_assign_items,
            "输出信号连线",
            "port",
            str_rel_path,
            strict=strict,
        )
    )

    # 再比较输出信号处理区域的组标签逐字一致性。
    list_issues.extend(
        _output_group_label_mirror_issues(
            list_always_items,
            dict_expected_by_signal,
            "输出信号处理区域",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 最后比较输出信号处理区域的条目顺序是否与接口一致。
    list_issues.extend(
        _output_order_mirror_issues(
            list_expected_outputs,
            list_always_items,
            "输出信号处理区域",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 返回三个输出相关区域汇总后的镜像诊断。
    return list_issues

# 供 `_expected_output_mirror_items` 复用的拆分 helper，专门处理按模块接口顺序排列的输出镜像基线条目。
def _expected_output_mirror_items(dict_module: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回按模块接口顺序排列的输出镜像基线条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 按接口输出顺序排列的镜像基线条目列表。
    """

    # dict_output_bridges 保存顶层 output 到内部桥接信号的显式映射。
    dict_output_bridges: dict[str, str] = {}  # 顶层输出端口到内部桥接信号的显式绑定表。

    # 逐条扫描 assign，提取显式 output bridge 绑定关系。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 读取当前 assign 的左值，判断是否是顶层输出桥接。
        str_lhs = str(dict_assign.get("lhs") or "")  # 当前 assign 左值。

        # 只保留 `o_` 前缀的顶层输出桥接 assign。
        if not str_lhs.startswith("o_"):

            # 普通内部连线不会写入显式 bridge 绑定表。
            continue

        # 读取当前 assign 的右值，作为内部桥接信号名。
        str_rhs = str(dict_assign.get("rhs") or "")  # 当前 assign 右值。

        # 写入当前顶层输出端口对应的显式桥接信号。
        dict_output_bridges[str_lhs] = str_rhs  # 当前顶层输出端口对应的显式桥接信号名。

    # list_items 保存按接口顺序整理后的镜像真源条目。
    list_items: list[dict[str, Any]] = []  # 输出镜像真源条目列表。

    # 按模块接口原始顺序遍历全部端口。
    for dict_port in dict_module.get("ports", []) or []:

        # 只把 output 端口纳入输出镜像真源。
        if str(dict_port.get("direction") or "") != "output":

            # 非 output 端口不属于输出镜像合同。
            continue

        # 先读取当前顶层输出端口名称。
        str_port_name = str(dict_port.get("name") or "")  # 当前顶层输出端口名。

        # 端口名为空时无法参与镜像比较。
        if not str_port_name:

            # 跳过异常端口，避免构造空主键真源项。
            continue

        # 生成当前输出端口的真源组标签。
        str_group_label = _output_group_label_from_port(dict_port)  # 当前顶层输出端口对应的真源组标签。

        # 先推导当前输出端口的默认内部桥接信号名称。
        str_default_internal_signal = _default_output_bridge_signal_name(str_port_name)  # 当前输出端口的默认内部桥接信号名。

        # 再优先使用显式桥接绑定，否则回退到默认命名合同。
        str_internal_signal = dict_output_bridges.get(str_port_name) or str_default_internal_signal  # 当前输出端口最终使用的内部桥接信号名。

        # 追加当前顶层输出端口的镜像真源项。
        list_items.append(
            {
                "port": str_port_name,
                "signal": str_internal_signal,
                "group_label": str_group_label,
                "line": _as_line(dict_port.get("line_start")),
            }
        )  # 当前顶层输出端口对应的镜像真源条目。

    # 返回按接口顺序整理好的输出镜像真源。
    return list_items

# 供 `_output_group_label_from_port` 复用的拆分 helper，专门处理模块接口端口的输出镜像组标签。
def _output_group_label_from_port(dict_port: dict[str, Any]) -> str:
    """
    返回模块接口端口的输出镜像组标签。

    :param dict_port: formatter AST 端口条目。
    :return: 由 group 和 section 拼成的镜像组标签。
    """

    # 先读取接口级组注释正文。
    str_group = str(dict_port.get("group") or "").strip()  # 接口级组注释正文。

    # 再读取接口内子组注释正文。
    str_section = str(dict_port.get("section") or "").strip()  # 接口内子组注释正文。

    # group 和 section 同时存在时，需要把二者拼成完整镜像组标签。
    if str_group and str_section:

        # 复用既有 `group--section` 文本格式。
        return f"{str_group}--{str_section}"

    # 只有一级标签时直接返回非空标签。
    return str_group or str_section

# 供 `_default_output_bridge_signal_name` 复用的拆分 helper，专门处理顶层输出端口对应的默认内部 `_o` 信号名。
def _default_output_bridge_signal_name(str_port_name: str) -> str:
    """
    返回顶层输出端口对应的默认内部 `_o` 信号名。

    :param str_port_name: 顶层 output 端口名。
    :return: 默认内部输出桥接信号名。
    """

    # 先处理符合 `o_*` 约定的常规输出端口。
    if str_port_name.startswith("o_"):

        # 去掉顶层输出前缀后再追加内部桥接后缀。
        return f"{str_port_name[2:]}_o"

    # 其他命名保持原名再追加内部桥接后缀。
    return f"{str_port_name}_o"

# 供 `_output_decl_mirror_items` 复用的拆分 helper，专门处理收集输出信号区域中的实际 `_o` 信号镜像条目。
def _output_decl_mirror_items(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_expected_signals: set[str],
    set_group_labels: set[str],
) -> list[dict[str, Any]]:
    """
    收集输出信号区域中的实际 `_o` 信号镜像条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_expected_signals: 可映射回顶层输出的内部信号集合。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 输出信号区域的镜像条目列表。
    """

    # list_items 保存输出信号区域里实际出现的镜像条目。
    list_items: list[dict[str, Any]] = []  # 输出信号区域的实际镜像条目列表。

    # str_current_group_label 记录当前声明条目继承到的组标签。
    str_current_group_label = ""  # 输出信号区域当前生效的组标签。

    # 先按源码顺序扫描全部内部声明。
    for dict_decl in sorted(
        dict_module.get("decls", []) or [],
        key=lambda item: _as_line(item.get("line_start")) or 0,
    ):

        # 读取当前内部声明名称，判断它是否属于输出桥接信号。
        str_name = str(dict_decl.get("name") or "")  # 当前内部声明名称。

        # 只保留能映射回顶层输出的内部声明。
        if str_name not in set_expected_signals:

            # 其他声明不参与输出镜像比较。
            continue

        # 定位当前内部声明的源码行号。
        int_line_no = _as_line(dict_decl.get("line_start"))  # 当前内部声明起始行号。

        # 没有可信行号时无法回溯区域横幅。
        if int_line_no is None:

            # 跳过无 span 的内部声明。
            continue

        # 只接收落在输出信号区域的内部声明。
        if _nearest_region_title(dict_region_by_line, int_line_no) != "输出信号":

            # 其他区域里的 `_o` 声明不计入当前镜像区域。
            continue

        # 先提取当前声明条目的前导注释。
        list_leading_comments = dict_decl.get("leading_comments") or []  # 当前声明条目的前导注释列表。

        # 根据声明条目前导注释刷新当前生效组标签。
        str_current_group_label = _next_output_group_label(  # 当前声明条目继承到的接口组标签。
            list_leading_comments, str_current_group_label, set_group_labels  # 桥接区不能自造分组标题，只能沿用接口里真实存在的标签。
        )

        # 把当前输出信号声明记入镜像条目列表。
        list_items.append(
            {
                "signal": str_name,
                "group_label": str_current_group_label,
                "line": int_line_no,
            }
        )  # 当前输出信号声明对应的镜像条目。

    # 返回输出信号区域的实际镜像条目。
    return list_items

# 供 `_output_assign_mirror_items` 复用的拆分 helper，专门处理收集输出信号连线区域中的实际输出桥接条目。
def _output_assign_mirror_items(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_expected_ports: set[str],
    set_group_labels: set[str],
) -> list[dict[str, Any]]:
    """
    收集输出信号连线区域中的实际输出桥接条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_expected_ports: 需要镜像比较的顶层输出端口集合。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 输出信号连线区域的镜像条目列表。
    """

    # list_items 保存输出桥接区域里实际出现的镜像条目。
    list_items: list[dict[str, Any]] = []  # 输出桥接区域的实际镜像条目列表。

    # str_current_group_label 记录当前输出桥接条目生效的组标签。
    str_current_group_label = ""  # 输出桥接区域当前生效的组标签。

    # 按源码顺序扫描全部 assign 条目。
    for dict_assign in sorted(
        dict_module.get("assigns", []) or [],
        key=lambda item: _as_line(item.get("line_start")) or 0,
    ):

        # 提取当前候选 output bridge 的左值端口名。
        str_lhs = str(dict_assign.get("lhs") or "")  # 当前候选 output bridge 的左值端口名。

        # 只保留顶层输出桥接 assign。
        if str_lhs not in set_expected_ports:

            # 其他内部连线不属于输出镜像比较对象。
            continue

        # 读取当前候选 output bridge 的源码起始行号。
        int_line_no = _as_line(dict_assign.get("line_start"))  # 当前候选 output bridge 的源码起始行号。

        # 没有可信行号时无法确定区域归属。
        if int_line_no is None:

            # 跳过无 span 的输出桥接 assign。
            continue

        # 只接收落在输出信号连线区域的桥接 assign。
        if _nearest_region_title(dict_region_by_line, int_line_no) != "输出信号连线":

            # 其他区域里的输出桥接不计入当前镜像区域。
            continue

        # 先提取当前桥接条目的前导注释。
        list_leading_comments = dict_assign.get("leading_comments") or []  # 当前桥接条目的前导注释列表。

        # 根据桥接条目前导注释刷新当前生效组标签。
        str_current_group_label = _next_output_group_label(  # 当前桥接条目继承到的接口组标签。
            list_leading_comments, str_current_group_label, set_group_labels  # 处理区不能自造分组标题，只能沿用接口里真实存在的标签。
        )

        # 把当前输出桥接 assign 追加到镜像条目列表。
        list_items.append(
            {
                "port": str_lhs,
                "group_label": str_current_group_label,
                "line": int_line_no,
            }
        )  # 当前输出桥接 assign 对应的镜像条目。

    # 返回输出桥接区域的实际镜像条目。
    return list_items

# 供 `_output_always_mirror_items` 复用的拆分 helper，专门处理收集输出信号处理区域中的实际输出处理块镜像条目。
def _output_always_mirror_items(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_expected_signals: set[str],
    set_group_labels: set[str],
) -> list[dict[str, Any]]:
    """
    收集输出信号处理区域中的实际输出处理块镜像条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_expected_signals: 可映射回顶层输出的内部信号集合。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 输出信号处理区域的镜像条目列表。
    """

    # list_items 保存输出处理区域里实际出现的镜像条目。
    list_items: list[dict[str, Any]] = []  # 输出处理区域的实际镜像条目列表。

    # str_current_group_label 记录当前输出处理条目生效的组标签。
    str_current_group_label = ""  # 输出处理区域当前生效的组标签。

    # 按源码顺序扫描全部 always 块。
    for dict_always in sorted(
        dict_module.get("always", []) or [],
        key=lambda item: _as_line(item.get("line_start")) or 0,
    ):

        # 读取当前输出处理块的源码起始行号。
        int_line_no = _as_line(dict_always.get("line_start"))  # 锁定 always 起始行，后续用它回查最近的输出处理区域横幅。

        # 没有可信行号时无法回溯输出处理区域横幅。
        if int_line_no is None:

            # 跳过无 span 的 always 块。
            continue

        # 只接收落在输出信号处理区域的 always 块。
        if _nearest_region_title(dict_region_by_line, int_line_no) != "输出信号处理区域":

            # 其他区域里的 always 块不属于当前镜像区域。
            continue

        # list_targets 保存当前 always 真正命中的输出桥接目标。
        list_targets: list[str] = []  # 当前 always 命中的输出桥接目标列表。

        # 扫描当前 always 的赋值目标，只保留输出桥接信号。
        for item in dict_always.get("targets", []) or []:

            # 规范化当前目标信号名，便于与真源集合比较。
            str_target_name = str(item)  # 当前 always 目标信号名。

            # 只保留能映射回顶层输出的内部桥接目标。
            if str_target_name not in set_expected_signals:

                # 非输出桥接目标不参与当前镜像条目生成。
                continue

            # 记录当前 always 命中的输出桥接目标。
            list_targets.append(str_target_name)  # 当前 always 命中的输出桥接目标。

        # 没命中任何输出桥接目标时不参与镜像比较。
        if not list_targets:

            # 当前 always 与输出镜像无关。
            continue

        # 先提取当前处理块的前导注释。
        list_leading_comments = dict_always.get("leading_comments") or []  # 当前处理块的前导注释列表。

        # 根据处理块前导注释刷新当前生效组标签。
        str_current_group_label = _next_output_group_label(  # 当前处理块继承到的接口组标签。
            list_leading_comments, str_current_group_label, set_group_labels  # 只允许切换到接口真源里存在的组标签。
        )

        # 只记录当前 always 命中的首个输出桥接目标。
        list_items.append(
            {
                "signal": list_targets[0],
                "group_label": str_current_group_label,
                "line": int_line_no,
            }
        )  # 当前输出处理 always 对应的镜像条目。

    # 返回输出处理区域的实际镜像条目。
    return list_items

# 供 `_next_output_group_label` 复用的拆分 helper，专门处理当前输出条目生效的组标签。
def _next_output_group_label(
    list_leading_comments: list[str],
    str_current_group_label: str,
    set_group_labels: set[str],
) -> str:
    """
    返回当前输出条目生效的组标签。

    :param list_leading_comments: 当前 AST 条目的前导注释列表。
    :param str_current_group_label: 前一个已生效的组标签。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 当前条目生效的组标签。
    """

    # 先尝试读取当前条目的显式组注释标签。
    str_group_label = _leading_group_comment_label(list_leading_comments)  # 当前条目的显式组注释标签。

    # 只有命中接口真源允许的组标签时才切换当前组。
    if str_group_label and str_group_label in set_group_labels:

        # 使用当前条目的显式组标签覆盖上一组状态。
        return str_group_label

    # 否则沿用上一条已经生效的组标签。
    return str_current_group_label

# 供 `_leading_group_comment_label` 复用的拆分 helper，专门处理前导注释中的首个组标签正文。
def _leading_group_comment_label(list_leading_comments: list[str]) -> str:
    """
    返回前导注释中的首个组标签正文。

    :param list_leading_comments: formatter AST 暴露的前导注释列表。
    :return: 首个组标签正文；没有可用组标签时返回空字符串。
    """

    # 没有任何前导注释时不可能提取到组标签。
    if not list_leading_comments:

        # 让调用方继续沿用上一组标签。
        return ""

    # 只取当前条目前导注释里的首行作为组标签候选。
    str_first_comment = str(list_leading_comments[0] or "")  # 当前条目前导注释首行文本。

    # 把首行注释正文规范化成可比较的组标签文本。
    return _normalize_comment_label(str_first_comment)

# 供 `_normalize_comment_label` 复用的拆分 helper，专门处理去掉注释前缀后的纯注释正文。
def _normalize_comment_label(str_comment: str) -> str:
    """
    返回去掉注释前缀后的纯注释正文。

    :param str_comment: 原始注释文本。
    :return: 规范化后的注释正文。
    """

    # 先去掉注释两侧空白，保留纯正文比较视图。
    str_label = str(str_comment or "").strip()  # 去掉首尾空白后的注释正文。

    # 行注释标签需要先剥离 `//` 前缀再参与比较。
    if str_label.startswith("//"):

        # 去掉纯行注释前缀，保留真正的标签文本。
        str_label = str_label[2:].strip()  # 剥离 `//` 后的组标签正文。

    # 返回规范化后的组标签文本。
    return str_label

# 供 `_single_output_mirror_issue` 复用的拆分 helper，专门处理只包含一条输出镜像诊断的列表。
def _single_output_mirror_issue(
    str_code: str, str_message: str, str_rel_path: str, int_line_no: int | None,
    str_rule: str, *, strict: bool,
) -> list[QualityIssue]:
    """
    返回只包含一条输出镜像诊断的列表。

    :param str_code: 诊断规则编号。
    :param str_message: 诊断文案。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param int_line_no: 诊断落点行号。
    :param str_rule: 诊断子规则名。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 只包含一条输出镜像诊断的列表。
    """

    # 先组装调用点需要回报的单条输出镜像诊断对象。
    quality_issue_issue: QualityIssue = QualityIssue(  # 当前输出镜像 helper 要返回的唯一诊断对象。
        str_code, _style_severity(strict), str_message, str_rel_path, int_line_no, rule=str_rule  # 保持既有 QualityIssue 构造顺序，避免聚合口径漂移。
    )

    # 再包装成列表，复用现有质量门聚合接口。
    return [quality_issue_issue]

# 供 `_output_group_label_mirror_issues` 复用的拆分 helper，专门处理输出相关区域组标签文本漂移诊断。
def _output_group_label_mirror_issues(
    list_actual_items: list[dict[str, Any]], dict_expected_items: dict[str, dict[str, Any]],
    str_region_title: str, str_key_name: str, str_rel_path: str, *, strict: bool,
) -> list[QualityIssue]:
    """
    返回输出相关区域组标签文本漂移诊断。

    :param list_actual_items: 当前输出相关区域的实际条目列表。
    :param dict_expected_items: 当前条目类型对应的接口真源索引。
    :param str_region_title: 当前正在比较的输出相关区域标题。
    :param str_key_name: 当前条目类型的比较键名。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 组标签文本漂移诊断列表。
    """

    # 逐条比较当前区域条目的组标签文本。
    for dict_actual_item in list_actual_items:

        # 先读取当前条目的比较主键。
        str_item_key = str(dict_actual_item.get(str_key_name) or "")  # 当前条目的比较主键。

        # 再按主键回查接口真源条目。
        dict_expected_item = dict_expected_items.get(str_item_key) or {}  # 当前条目对应的接口真源条目。

        # 读取接口真源声明的组标签文本。
        str_expected_label = str(dict_expected_item.get("group_label") or "")  # 接口真源要求使用的组标签文本。

        # 真源没有组标签时不做逐字标签比较。
        if not str_expected_label:

            # 无标签真源只参与顺序比较，不参与 VG070。
            continue

        # 读取当前区域实际观察到的组标签文本。
        str_actual_label = str(dict_actual_item.get("group_label") or "")  # 当前区域实际使用的组标签文本。

        # 当前条目组标签逐字一致时直接通过。
        if str_actual_label == str_expected_label:

            # 当前条目没有发生组标签文本漂移。
            continue

        # 把接口真源标签漂移展开成 VG070 文案，直接回显期望标签和实际标签。
        str_issue_message = (
            f"`{str_region_title}` item `{str_item_key}` must use group label "
            f"`{str_expected_label}`, not `{str_actual_label or 'unknown'}`."
        )  # 诊断里同时回显接口标签文本和当前区域标签文本。

        # 让标签漂移定位到当前区域的实际条目，避免把问题报到接口真源定义处。
        return _single_output_mirror_issue(
            "VG070",
            str_issue_message,
            str_rel_path,
            _as_line(dict_actual_item.get("line")),
            "output.mirror.group_label",
            strict=strict,
        )

    # 当前区域没有发现组标签文本漂移。
    return []

# 供 `_output_order_mirror_issues` 复用的拆分 helper，专门处理输出相关区域顺序漂移诊断。
def _output_order_mirror_issues(
    list_expected_outputs: list[dict[str, Any]], list_actual_items: list[dict[str, Any]],
    str_region_title: str, str_key_name: str, str_rel_path: str, *, strict: bool,
) -> list[QualityIssue]:
    """
    返回输出相关区域顺序漂移诊断。

    :param list_expected_outputs: 按接口顺序排列的输出镜像真源。
    :param list_actual_items: 当前输出相关区域的实际条目列表。
    :param str_region_title: 当前正在比较的输出相关区域标题。
    :param str_key_name: 当前条目类型的比较键名。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 顺序漂移诊断列表。
    """

    # 当前区域没有任何条目时无需生成顺序诊断。
    if not list_actual_items:

        # 空区域不会触发输出顺序比较。
        return []

    # list_actual_order 保存当前区域实际观察到的主键顺序。
    list_actual_order: list[str] = []  # 当前区域实际出现的主键顺序。

    # 逐条提取当前区域实际观察到的主键顺序。
    for dict_item in list_actual_items:

        # 读取当前实际条目的比较主键。
        str_actual_key = str(dict_item.get(str_key_name) or "")  # 当前实际条目的比较主键。

        # 记录当前实际条目的主键顺序。
        list_actual_order.append(str_actual_key)  # 当前区域实际出现的主键。

    # 把当前区域实际出现过的主键折叠成集合，便于裁剪真源顺序。
    set_actual_order = set(list_actual_order)  # 当前区域实际出现过的条目主键集合。

    # list_expected_order 保存当前区域按接口真源裁剪后的约束顺序。
    list_expected_order: list[str] = []  # 只保留当前区域实际出现过、且必须遵循接口顺序的主键列表。

    # 按模块接口真源顺序裁剪出当前区域需要遵循的主键顺序。
    for dict_expected_item in list_expected_outputs:

        # 读取当前真源条目的比较主键。
        str_expected_key = str(dict_expected_item.get(str_key_name) or "")  # 当前真源条目的比较主键。

        # 当前真源主键未出现在实际区域时不纳入比较。
        if str_expected_key not in set_actual_order:

            # 只比较当前区域实际出现过的输出条目。
            continue

        # 记录当前区域应遵循的真源主键顺序。
        list_expected_order.append(str_expected_key)  # 当前区域应遵循的真源主键。

    # 实际顺序与真源顺序一致时直接通过。
    if list_actual_order == list_expected_order:

        # 当前区域没有发生顺序漂移。
        return []

    # 把顺序漂移落点到当前区域首个实际条目上。
    int_line_no = _as_line(list_actual_items[0].get("line"))  # 当前区域首个实际条目对应的源码行号。

    # 先把期望顺序和实际顺序格式化成独立文本，避免最终 VG071 文案过长。
    str_expected_order_text = str(list_expected_order)  # VG071 文案里回显的接口真源顺序文本。

    # 再把当前区域实际顺序格式化成独立文本，供最终 VG071 文案直接拼接。
    str_actual_order_text = str(list_actual_order)  # VG071 文案里回显的当前区域顺序文本。

    # 再拼出当前 VG071 的最终诊断文案。
    str_issue_message = (
        f"`{str_region_title}` order must follow module interface outputs {str_expected_order_text}, "
        f"not {str_actual_order_text}."  # 用两组预格式化顺序文本拼出最终 VG071 文案。
    )

    # 让顺序漂移落在当前区域第一条实际条目上，避免把问题报到接口真源定义处。
    return _single_output_mirror_issue(
        "VG071",
        str_issue_message,
        str_rel_path,
        int_line_no,
        "output.mirror.order",
        strict=strict,
    )

# 供 `_rulebook_consistency_issues` 复用的拆分 helper，专门处理检查 rulebook JSON 是否仍是运行时规则的可信来源。
def _rulebook_consistency_issues(str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 rulebook JSON 是否仍是运行时规则的可信来源。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 规则源一致性诊断列表。
    """

    # 读取规则源失败时必须转成 VG059，而不是让质量门崩溃。
    try:

        # rulebook_source 汇总区域、fallback 注释和 profile 规则。
        rulebook_source = load_verilog_rulebook()  # Verilog 风格规则源

    # 规则源不可用说明门禁无法可信执行。
    except Exception as exc:

        # 返回阻断诊断，提示维护者修复规则源。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                f"Verilog rulebook cannot be loaded: {exc}",
                str_rel_path,
                rule="rulebook.load",
            )
        ]

    # 区域横幅顺序必须和 JSON 中 regions 保持一致。
    if tuple(rulebook_source.region_labels) != tuple(REGION_KEYWORDS):

        # 硬编码表和 JSON 漂移时区域归属结论不可信。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Runtime region labels drifted from assets/verilog_style_rules.json.",
                str_rel_path,
                rule="rulebook.region_drift",
            )
        ]

    # fallback 注释列表缺失时 VG056 无法可信执行。
    if not rulebook_source.fallback_comments:

        # 空 fallback 配置代表规则源结构漂移。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Rulebook must define comments.fallback_comments for deliverable gate.",
                str_rel_path,
                rule="rulebook.fallback_comments",
            )
        ]

    # runtime_messages 为空时，VG069 和参数检查尾部合同都失去机器真源支撑。
    dict_runtime_messages = rulebook_source.raw.get("runtime_messages") or {}  # 读取运行时消息规则分区，后续校验 VG069 真源是否齐全。

    # 人类可读 display 前缀必须在 rulebook 中显式声明。
    if not dict_runtime_messages.get("human_readable_display_prefixes"):

        # 缺少 display 前缀配置会让 VG069 失去机器真源。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Rulebook must define runtime_messages.human_readable_display_prefixes.",
                str_rel_path,
                rule="rulebook.runtime_messages",
            )
        ]

    # 机器 transcript 豁免前缀同样需要在 rulebook 中显式声明。
    if not dict_runtime_messages.get("machine_transcript_prefixes"):

        # 缺少 transcript 前缀配置会让机器输出豁免失去依据。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Rulebook must define runtime_messages.machine_transcript_prefixes.",
                str_rel_path,
                rule="rulebook.runtime_messages",
            )
        ]

    # 规则源一致时无诊断。
    return []

# 供 `_port_rules` 复用的拆分 helper，专门处理检查端口方向前缀和 top-level port 声明风格。
def _port_rules(
    dict_module: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    vitis_wrapper: bool,
) -> list[QualityIssue]:
    """
    检查端口方向前缀和 top-level port 声明风格。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :return: 端口命名和注释诊断列表。
    """

    # list_issues 保存端口规则诊断。
    list_issues: list[QualityIssue] = []  # 端口规则诊断

    # 逐个端口应用方向前缀规则，Vitis 例外在循环内短路。
    for dict_port in dict_module.get("ports", []) or []:

        # 单个端口的方向前缀和重复前缀独立检查。
        list_issues.extend(_port_name_issues(dict_port, str_rel_path, strict=strict, vitis_wrapper=vitis_wrapper))

    # 文本 port 声明检查保留行号定位。
    list_issues.extend(_port_header_text_issues(str_text, str_rel_path, strict=strict))

    # 返回端口规则诊断。
    return list_issues

# 供 `_port_name_issues` 复用的拆分 helper，专门处理检查单个端口是否符合方向前缀命名。
def _port_name_issues(
    dict_port: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
    vitis_wrapper: bool,
) -> list[QualityIssue]:
    """
    检查单个端口是否符合方向前缀命名。

    :param dict_port: formatter AST 中的端口条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :return: 当前端口命名诊断列表。
    """

    # str_direction 决定端口必须使用的 i_/o_/io_ 前缀。
    str_direction = str(dict_port.get("direction") or "")  # 前缀映射使用的端口方向

    # str_name 是当前端口命名规则的检查对象。
    str_name = str(dict_port.get("name") or "")  # 当前端口标识符

    # 空端口名由 AST/parser 诊断处理。
    if not str_name:

        # 当前端口无法执行命名规则。
        return []

    # Vitis wrapper 固定端口名不要求 Erie 前缀。
    if vitis_wrapper and _is_vitis_port(str_name):

        # 工具链固定端口直接跳过命名检查。
        return []

    # list_issues 保存单端口命名问题。
    list_issues: list[QualityIssue] = []  # 单端口命名诊断

    # str_expected_prefix 按方向映射 Erie 前缀。
    str_expected_prefix = {"input": "i_", "output": "o_", "inout": "io_"}.get(str_direction)  # 方向对应端口前缀

    # 已知方向端口必须带对应前缀。
    if str_expected_prefix and not str_name.startswith(str_expected_prefix):

        # 前缀错误会影响接口阅读和后续 formatter 分组。
        list_issues.append(
            QualityIssue(
                "VG010",
                _style_severity(strict),
                f"{str_direction} port `{str_name}` must use `{str_expected_prefix}` prefix.",
                str_rel_path,
                rule="naming.port_prefix",
            )
        )

    # 双重方向前缀通常来自生成器拼接错误。
    if str_name.startswith(("i_i_", "o_o_", "io_io_")):

        # 重复前缀登记为命名问题。
        list_issues.append(
            QualityIssue(
                "VG010",
                _style_severity(strict),
                f"Port `{str_name}` has duplicated direction prefix.",
                str_rel_path,
                rule="naming.no_duplicate_prefix",
            )
        )

    # 返回当前端口命名诊断。
    return list_issues

# 供 `_port_header_text_issues` 复用的拆分 helper，专门处理检查端口声明行是否违反 ANSI header 风格。
def _port_header_text_issues(str_text: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查端口声明行是否违反 ANSI header 风格。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把端口声明问题升级为 error。
    :return: 端口声明文本诊断列表。
    """

    # list_issues 保存端口声明行问题。
    list_issues: list[QualityIssue] = []  # 端口声明文本诊断

    # 逐行扫描文本 port 声明，定位遗留非 ANSI 写法。
    for int_line_no, str_line in enumerate(str_text.splitlines(), start=1):

        # 单行端口声明文本检查保持行号定位。
        list_issues.extend(_port_header_line_issues(str_line, int_line_no, str_rel_path, strict=strict))

    # 返回端口声明文本诊断。
    return list_issues

# 供 `_port_header_line_issues` 复用的拆分 helper，专门处理检查单行端口声明是否仍含 wire/reg/logic 或 output reg。
def _port_header_line_issues(
    str_line: str,
    int_line_no: int,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单行端口声明是否仍含 wire/reg/logic 或 output reg。

    :param str_line: 当前源码行。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把端口声明问题升级为 error。
    :return: 当前端口声明行诊断列表。
    """

    # list_issues 保存当前行端口声明问题。
    list_issues: list[QualityIssue] = []  # 单行端口声明诊断

    # ANSI header 中端口声明不应显式带 wire/reg/logic。
    if re.search(r"^\s*(input|output|inout)\s+(wire|reg|logic)\b", str_line):

        # 端口声明类型关键字会破坏最终风格要求。
        list_issues.append(
            QualityIssue(
                "VG011",
                _style_severity(strict),
                "Port declarations must not include wire/reg/logic in final ANSI header style.",
                str_rel_path,
                int_line_no,
                "ports.no_kind_keyword",
            )
        )

    # output reg 端口应使用内部 _o bridge。
    if re.search(r"^\s*output\s+reg\b", str_line):

        # top-level output reg 会破坏输出桥接约束。
        list_issues.append(
            QualityIssue(
                "VG011",
                _style_severity(strict),
                "Top-level outputs must be driven through internal `_o` signals and "
                "assign bridges, not output reg ports.",
                str_rel_path,
                int_line_no,
                "ports.output_bridge",
            )
        )

    # 返回当前行端口声明诊断。
    return list_issues

# 供 `_parameter_rules` 复用的拆分 helper，专门处理检查 module 参数和 localparam 命名约束。
def _parameter_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 module 参数和 localparam 命名约束。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 参数命名和注释诊断列表。
    """

    # list_issues 保存参数命名诊断。
    list_issues: list[QualityIssue] = []  # 参数规则诊断

    # module parameter 必须使用 C_ 大写命名。
    for dict_param in dict_module.get("params", []) or []:

        # 单个 parameter 的 C_ 前缀检查。
        list_issues.extend(_parameter_name_issues(dict_param, str_rel_path, strict=strict))

    # localparam 需要按状态参数和普通常量分流检查。
    for dict_param in dict_module.get("localparams", []) or []:

        # 单个 localparam 按 ST_ 和普通常量规则检查。
        list_issues.extend(_localparam_name_issues(dict_param, str_rel_path, strict=strict))

    # 返回参数命名诊断。
    return list_issues

# 供 `_parameter_name_issues` 复用的拆分 helper，专门处理检查单个 parameter 是否使用 C_ 大写命名。
def _parameter_name_issues(dict_param: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查单个 parameter 是否使用 C_ 大写命名。

    :param dict_param: formatter AST 中的 parameter 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: parameter 命名诊断列表。
    """

    # str_name 是 C_ 参数命名规则的检查对象。
    str_name = str(dict_param.get("name") or "")  # 当前 parameter 标识符

    # 空名称不进入状态参数命名分支。
    if not str_name:

        # 无法生成稳定状态前缀诊断时跳过。
        return []

    # C_ 前缀只能出现一次，防止生成器重复拼接参数类别。
    if str_name.startswith(DUPLICATE_PARAMETER_PREFIXES[0]):

        # 重复 C_ 前缀仍归入 parameter 命名规则。
        return [
            QualityIssue(
                "VG012",
                _style_severity(strict),
                f"Module parameter `{str_name}` has duplicated `C_` prefix.",
                str_rel_path,
                rule="naming.no_duplicate_prefix",
            )
        ]

    # 合规 C_ 大写命名不产生诊断。
    if re.fullmatch(r"C_[A-Z0-9_]+", str_name):

        # parameter 命名已满足规则。
        return []

    # 参数命名问题登记为 VG012。
    return [
        QualityIssue(
            "VG012",
            _style_severity(strict),
            f"Module parameter `{str_name}` must use `C_` + uppercase naming.",
            str_rel_path,
            rule="naming.parameter",
        )
    ]

# 供 `_localparam_name_issues` 复用的拆分 helper，专门处理检查单个 localparam 是否符合状态或常量命名。
def _localparam_name_issues(dict_param: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查单个 localparam 是否符合状态或常量命名。

    :param dict_param: formatter AST 中的 localparam 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: localparam 命名诊断列表。
    """

    # str_name 用于 localparam 命名规则和状态枚举识别。
    str_name = str(dict_param.get("name") or "")  # 状态枚举候选 localparam 名

    # 空名称由 AST 解析层负责。
    if not str_name:

        # 无名称时跳过命名规则。
        return []

    # 状态参数前缀只能出现一次。
    if str_name.startswith(DUPLICATE_PARAMETER_PREFIXES[1]):

        # 重复状态前缀通常来自生成器拼接错误。
        return [
            QualityIssue(
                "VG012",
                _style_severity(strict),
                f"State localparam `{str_name}` has duplicated `ST_` prefix.",
                str_rel_path,
                rule="naming.no_duplicate_prefix",
            )
        ]

    # ST_ 状态参数有更严格的状态命名规则。
    if str_name.startswith("ST_"):

        # 状态参数命名合规时直接通过。
        if re.fullmatch(r"ST_[A-Z0-9_]+", str_name):

            # ST_ 后接大写和数字。
            return []

        # 状态参数命名问题保持 VG012。
        return [
            QualityIssue(
                "VG012",
                _style_severity(strict),
                f"State localparam `{str_name}` must use `ST_` + uppercase naming.",
                str_rel_path,
                rule="naming.state_parameter",
            )
        ]

    # 普通 localparam 应为全大写。
    if UPPER_IDENTIFIER_PATTERN.fullmatch(str_name):

        # 普通常量命名合规。
        return []

    # 普通 localparam 大写约束。
    return [
        QualityIssue(
            "VG012",
            _style_severity(strict),
            f"localparam `{str_name}` should be uppercase.",
            str_rel_path,
            rule="naming.localparam",
        )
    ]

# 供 `_signal_rules` 复用的拆分 helper，专门处理检查内部信号、寄存器、计数器和 flag 类命名。
def _signal_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查内部信号、寄存器、计数器和 flag 类命名。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 信号命名、区域和注释诊断列表。
    """

    # list_issues 保存信号命名诊断。
    list_issues: list[QualityIssue] = []  # 信号规则诊断

    # set_output_ports 建立内部声明重名 output 的判定基准。
    set_output_ports = _module_output_ports(dict_module)  # output 重声明检测使用的端口名集合

    # 遍历内部声明模型。
    for dict_decl in dict_module.get("decls", []) or []:

        # 单个内部声明的前缀、重声明和语义命名独立检查。
        list_issues.extend(_signal_decl_issues(dict_decl, set_output_ports, str_rel_path, strict=strict))

    # 返回信号命名诊断。
    return list_issues

# 供 `_signal_decl_issues` 复用的拆分 helper，专门处理检查单个内部声明的前缀、重声明和语义命名。
def _signal_decl_issues(
    dict_decl: dict[str, Any],
    set_output_ports: set[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个内部声明的前缀、重声明和语义命名。

    :param dict_decl: formatter AST 中的内部声明条目。
    :param set_output_ports: 顶层 output 端口名集合。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: 当前内部声明的命名诊断列表。
    """

    # str_name 是内部声明名。
    str_name = str(dict_decl.get("name") or "")  # 内部信号名称

    # 空名称由 parser 诊断负责。
    if not str_name:

        # 无名称声明跳过命名判断。
        return []

    # str_kind 是 wire/reg/logic 等声明类型。
    str_kind = str(dict_decl.get("kind") or "")  # 内部信号声明类型

    # list_issues 保存单个内部声明的命名问题。
    list_issues: list[QualityIssue] = []  # 单声明命名诊断

    # 内部分类前缀只能出现一次。
    if str_name.startswith(DUPLICATE_SIGNAL_PREFIXES):

        # 重复分类前缀说明命名拼接过程已经失控。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Internal signal `{name}` has duplicated semantic prefix.",
                "naming.no_duplicate_prefix",
                str_rel_path,
                strict=strict,
            )
        )

    # 内部信号不能抢占 top-level output 前缀。
    if str_name.startswith("o_"):

        # 输出桥接应使用 _o 后缀。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Internal signal `{name}` must not use output-port `o_` prefix; use `_o` suffix for output bridges.",
                "naming.internal_output",
                str_rel_path,
                strict=strict,
            )
        )

    # 内部声明不应重声明输出端口。
    if str_name in set_output_ports:

        # 输出端口重声明会造成驱动语义混乱。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Top-level output `{name}` is redeclared internally.",
                "naming.output_redecl",
                str_rel_path,
                strict=strict,
            )
        )

    # reg/logic 信号应使用项目约定前缀或输出桥接后缀。
    if str_kind in {"reg", "logic"} and not _expected_reg_name(str_name):

        # 寄存器类命名不符合 Erie 规则。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Register `{name}` should use reg_/cnt_/state_/flag_/enc_/dec_ prefix or `_o` output suffix.",
                "naming.register_signal",
                str_rel_path,
                strict=strict,
            )
        )

    # 模块内部非 array 的 reg 标量/向量声明必须显式初始化。
    quality_issue_default_init = _internal_reg_default_init_issue(  # 当前声明对应的 VG015 诊断对象
        dict_decl,  # 用于读取 init 与 unpacked 状态的声明节点
        str_kind,  # 决定是否属于 reg 的类型文本
        str_name,  # 写入诊断报文的寄存器成员名
        str_rel_path,  # 绑定 issue 的源码相对路径
        strict=strict,  # 继承当前 strict 与 non-strict 严格度
    )

    # 命中缺省初始化门禁时追加成员级 VG015。
    if quality_issue_default_init is not None:

        # 该诊断与命名诊断并列存在，不能互相覆盖。
        list_issues.append(quality_issue_default_init)

    # 追加计数、flag、编码和译码语义命名诊断。
    list_issues.extend(_signal_semantic_name_issues(str_name, str_rel_path, strict=strict))

    # 返回单个内部声明的诊断。
    return list_issues

# 供 `_signal_semantic_name_issues` 复用的拆分 helper，专门处理检查内部信号是否按语义使用 cnt_/flag_/enc_/dec_ 前缀。
def _signal_semantic_name_issues(str_name: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查内部信号是否按语义使用 cnt_/flag_/enc_/dec_ 前缀。

    :param str_name: 内部信号名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: 语义前缀诊断列表。
    """

    # list_issues 保存语义命名问题。
    list_issues: list[QualityIssue] = []  # 语义命名诊断

    # 计数语义信号应使用 cnt_ 前缀。
    if _looks_counter(str_name) and not str_name.startswith("cnt_"):

        # 计数器命名问题登记为 VG013。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Counter-like signal `{name}` should use `cnt_` prefix.",
                "naming.counter",
                str_rel_path,
                strict=strict,
            )
        )

    # flag 类信号除端口和输出桥接外应使用 flag_ 前缀。
    if _flag_name_needs_prefix(str_name):

        # flag 命名问题登记为 VG013。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Flag-like signal `{name}` should use `flag_` prefix unless it is an output bridge.",
                "naming.flag",
                str_rel_path,
                strict=strict,
            )
        )

    # encoder 语义信号应使用 enc_ 前缀。
    if _looks_encoder(str_name) and not str_name.startswith("enc_"):

        # 编码信号命名问题。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Encoder-like signal `{name}` should use `enc_` prefix.",
                "naming.encoder",
                str_rel_path,
                strict=strict,
            )
        )

    # 译码语义信号缺少 dec_ 前缀时会污染区域归类。
    if _looks_decoder(str_name) and not str_name.startswith("dec_"):

        # 译码信号命名问题。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Decoder-like signal `{name}` should use `dec_` prefix.",
                "naming.decoder",
                str_rel_path,
                strict=strict,
            )
        )

    # 返回语义前缀诊断。
    return list_issues

# 供 `_signal_naming_issue` 复用的拆分 helper，专门处理构造内部信号命名诊断。
def _signal_naming_issue(
    str_name: str,
    str_message_template: str,
    str_rule: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> QualityIssue:
    """
    构造内部信号命名诊断。

    :param str_name: 内部信号名称。
    :param str_message_template: 包含 {name} 占位符的诊断文本模板。
    :param str_rule: 命名子规则名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: 内部信号命名诊断。
    """

    # str_message 使用真实信号名填充模板。
    str_message = str_message_template.format(name=str_name)  # 当前信号诊断文本

    # 返回统一 VG013 诊断对象。
    return QualityIssue("VG013", _style_severity(strict), str_message, str_rel_path, rule=str_rule)

# 供 `_signal_decl_issues` 复用的拆分 helper，专门处理模块内部非 array reg 缺省初始化门禁。
def _internal_reg_default_init_issue(
    dict_decl: dict[str, Any],
    str_kind: str,
    str_name: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> QualityIssue | None:
    """
    检查模块内部非 array reg 标量/向量声明是否显式初始化。

    :param dict_decl: formatter AST 中的内部声明条目。
    :param str_kind: 当前内部声明类型。
    :param str_name: 当前内部声明名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式问题升级为 error。
    :return: 命中缺省初始化门禁时返回 VG015，否则返回 None。
    """

    # 只有模块内部 reg 声明进入本门禁。
    if str_kind != "reg":

        # wire/logic/integer 等其他类型不在本轮治理范围内。
        return None

    # unpacked array 继续豁免当前缺省初始化门禁。
    if str(dict_decl.get("unpacked") or "").strip():

        # array 声明允许维持原始未初始化形态。
        return None

    # 已显式初始化的声明保持现状，不重复报缺省初始化问题。
    if str(dict_decl.get("init") or "").strip():

        # 只有原始缺少 init 的声明才需要命中 VG015。
        return None

    # int_line_no 尽量把诊断锚到内部声明起始行。
    int_line_no = _as_line(dict_decl.get("line_start"))  # VG015 报告使用的声明起始行号

    # 缺省修复合同必须明确要求精确 ` = 0;` 文本。
    str_message = (
        f"Internal non-array reg declaration `{str_name}` must be explicitly initialized inside the module; "
        "when backfilling a missing initializer, use exact ` = 0;`."
    )

    # 返回统一的 VG015 诊断对象。
    return QualityIssue(
        "VG015",
        _style_severity(strict),
        str_message,
        str_rel_path,
        int_line_no,
        rule="declaration.internal_reg_default_init",
    )

# 供 `_assign_rules` 复用的拆分 helper，专门处理检查 assign 写法和 top-level output 桥接约束。
def _assign_rules(
    dict_module: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 assign 写法和 top-level output 桥接约束。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: assign 语句相关诊断列表。
    """

    # list_issues 保存 assign 相关诊断。
    list_issues: list[QualityIssue] = []  # assign 规则诊断

    # set_output_ports 是输出桥接规则需要覆盖的 top-level output 集合。
    set_output_ports = {  # 输出桥接规则覆盖的 top-level output 集合
        str(dict_port.get("name"))  # 待检查桥接关系的输出端口名
        for dict_port in dict_module.get("ports", [])  # 扫描端口 AST 条目
        if str(dict_port.get("direction")) == "output"  # 只保留 output 方向端口
    }

    # set_output_bridge_targets 记录 assign 直接驱动的 output。
    set_output_bridge_targets = {  # 已通过 assign 语句桥接的输出端口集合
        str(dict_assign.get("lhs"))  # assign 左值中的输出端口名
        for dict_assign in dict_module.get("assigns", [])  # 遍历连续赋值条目
        if str(dict_assign.get("lhs")) in set_output_ports  # assign 左值直接命中输出端口
    }

    # 文本扫描用于捕获 inline wire initialization。
    for int_line_no, str_line in enumerate(str_text.splitlines(), start=1):

        # wire 声明行中不应直接初始化。
        if re.search(r"\bwire\b[^;]*=", _strip_line_comment(str_line)):

            # inline wire 初始化应拆成声明和 assign。
            list_issues.append(
                QualityIssue(
                    "VG030",
                    _style_severity(strict),
                    "Inline wire initialization is forbidden; declare wire and use a separate assign.",
                    str_rel_path,
                    int_line_no,
                    "assign.inline_wire",
                )
            )

    # 每个输出端口检查 always 驱动和 assign bridge。
    for str_port in sorted(set_output_ports):

        # bool_driven_in_always 标记输出端口是否在 always 中被直接赋值。
        bool_driven_in_always = any(  # 当前输出端口是否被 always 块直接赋值
            str_port in dict_always.get("targets", [])  # always 目标是否包含该输出端口
            for dict_always in dict_module.get("always", [])  # 遍历 always 结构条目
        )

        # always 中直接驱动输出端口违反桥接规则。
        if bool_driven_in_always:

            # 输出端口应经由内部 _o 信号和 assign bridge。
            list_issues.append(
                QualityIssue(
                    "VG014",
                    _style_severity(strict),
                    f"Output port `{str_port}` is assigned in an always block; "
                    "drive an internal `_o` signal and bridge with assign.",
                    str_rel_path,
                    rule="output.bridge",
                )
            )

        # 未检测到 assign bridge 时保留 warning，允许直接组合输出人工确认。
        if str_port not in set_output_bridge_targets:

            # 该诊断历史上为 advisory warning，保持兼容。
            list_issues.append(
                QualityIssue(
                    "VG014",
                    "warning",
                    f"Output port `{str_port}` has no explicit assign bridge detected; "
                    "confirm direct output assignment is intentional.",
                    str_rel_path,
                    rule="output.bridge",
                )
            )

    # 返回 assign 规则诊断。
    return list_issues

# 供 `_always_rules` 复用的拆分 helper，专门处理检查 always 块是否符合 Erie 的单目标和赋值类型约束。
def _always_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 always 块是否符合 Erie 的单目标和赋值类型约束。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: always 块相关诊断列表。
    """

    # list_issues 保存 always 规则诊断。
    list_issues: list[QualityIssue] = []  # always 块规则诊断

    # 逐个 always 检查单目标、reset 和赋值类型约束。
    for dict_always in dict_module.get("always", []) or []:

        # 单个 always 的目标数量、reset、赋值类型和复杂左值独立检查。
        list_issues.extend(_always_block_issues(dict_always, str_rel_path, strict=strict))

    # 返回复杂 lvalue、blocking 和复位风格等 always 诊断。
    return list_issues

# 供 `_always_block_issues` 复用的拆分 helper，专门处理检查单个 always 块的目标数量、reset 和赋值类型。
def _always_block_issues(dict_always: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查单个 always 块的目标数量、reset 和赋值类型。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 当前 always 块诊断列表。
    """

    # str_header 用于诊断定位具体 always 块。
    str_header = str(dict_always.get("header") or "")  # always 块头文本

    # list_targets 是 formatter 提取的赋值目标集合。
    list_targets = [
        str(item)  # 当前 always 块的赋值目标名
        for item in dict_always.get("targets", []) or []  # 遍历 formatter 提取目标
    ]  # 单目标 always 规则使用的赋值目标列表

    # list_issues 保存当前 always 块诊断。
    list_issues: list[QualityIssue] = []  # 单个 always 块诊断

    # 单目标约束先检查 always 是否需要拆块。
    list_issues.extend(_always_target_issues(str_header, list_targets, str_rel_path, strict=strict))

    # 时序 always 检查 reset 风格和阻塞赋值。
    list_issues.extend(_sequential_always_issues(dict_always, str_header, str_rel_path, strict=strict))

    # 组合 always 检查是否误用非阻塞赋值。
    list_issues.extend(_combinational_always_issues(dict_always, str_header, str_rel_path, strict=strict))

    # 复杂左值规则阻止 formatter 盲猜多目标拆分。
    list_issues.extend(
        _always_complex_lvalue_issues(dict_always, str_header, list_targets, str_rel_path, strict=strict)
    )

    # 返回当前 always 块全部诊断。
    return list_issues

# 供 `_always_target_issues` 复用的拆分 helper，专门处理检查 always 块是否只驱动一个唯一目标。
def _always_target_issues(
    str_header: str,
    list_targets: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 always 块是否只驱动一个唯一目标。

    :param str_header: always 块头文本。
    :param list_targets: formatter 提取的赋值目标列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 单目标规则诊断列表。
    """

    # 多目标 always 块应拆分。
    if len(set(list_targets)) <= 1:

        # 当前 always 满足单目标约束。
        return []

    # 单目标约束方便人工审查和后续注释生成。
    return [
        QualityIssue(
            "VG020",
            _style_severity(strict),
            f"Always block `{str_header}` assigns multiple targets {sorted(set(list_targets))}; "
            "split to one target per always.",
            str_rel_path,
            rule="always.single_target",
        )
    ]

# 供 `_sequential_always_issues` 复用的拆分 helper，专门处理检查时序 always 是否带低有效 reset 并使用非阻塞赋值。
def _sequential_always_issues(
    dict_always: dict[str, Any],
    str_header: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查时序 always 是否带低有效 reset 并使用非阻塞赋值。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_header: always 块头文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 时序 always 诊断列表。
    """

    # 非时序 always 不进入 reset 和非阻塞赋值规则。
    if dict_always.get("trigger_kind") != "seq":

        # 组合块由组合规则处理。
        return []

    # list_issues 保存时序 always 问题。
    list_issues: list[QualityIssue] = []  # 时序 always 诊断

    # 当前 always 的 reset 名称由 formatter AST 推断。
    str_reset = str(dict_always.get("reset") or "")  # 时序 always 复位信号名

    # reset 风格检查和赋值类型检查分别追加。
    list_issues.extend(_sequential_reset_style_issues(str_header, str_reset, str_rel_path, strict=strict))

    # 时序逻辑中出现阻塞赋值时登记问题。
    if any(_has_blocking_assignment(str_line) for str_line in dict_always.get("lines", []) or []):

        # 时序 always 只允许非阻塞赋值。
        list_issues.append(
            QualityIssue(
                "VG022",
                _style_severity(strict),
                f"Sequential always `{str_header}` must use nonblocking assignments only.",
                str_rel_path,
                rule="always.seq_nonblocking",
            )
        )

    # 返回时序 always 诊断。
    return list_issues

# 供 `_sequential_reset_style_issues` 复用的拆分 helper，专门处理检查时序 always 是否声明低有效 reset。
def _sequential_reset_style_issues(
    str_header: str,
    str_reset: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查时序 always 是否声明低有效 reset。

    :param str_header: always 块头文本。
    :param str_reset: formatter 推断出的 reset 名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 reset 风格问题升级为 error。
    :return: reset 风格诊断列表。
    """

    # 时序 always 必须带复位。
    if not str_reset:

        # 缺复位直接登记 VG021。
        return [
            QualityIssue(
                "VG021",
                _style_severity(strict),
                f"Sequential always `{str_header}` must include an active-low reset in the sensitivity list.",
                str_rel_path,
                rule="always.reset",
            )
        ]

    # 复位名称和触发边沿需符合低有效约定。
    if not _bad_reset_style(str_header, str_reset):

        # reset 命名和 negedge 风格都符合规则。
        return []

    # 复位命名或边沿不符合 Erie 规则。
    return [
        QualityIssue(
            "VG021",
            _style_severity(strict),
            f"Sequential always `{str_header}` should use negedge active-low reset naming such as i_rstn/i_axis_arstn.",
            str_rel_path,
            rule="always.reset",
        )
    ]

# 供 `_combinational_always_issues` 复用的拆分 helper，专门处理检查组合 always 是否只使用阻塞赋值。
def _combinational_always_issues(
    dict_always: dict[str, Any],
    str_header: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查组合 always 是否只使用阻塞赋值。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_header: always 块头文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 组合 always 诊断列表。
    """

    # 非组合 always 不进入该规则。
    if not dict_always.get("is_combinational"):

        # 时序块由时序规则处理。
        return []

    # bool_has_nonblocking 标记组合逻辑是否误用非阻塞赋值。
    bool_has_nonblocking = any(  # 组合 always 中是否存在非阻塞赋值
        _has_nonblocking_assignment(str(str_line))  # 当前源码行是否包含 <=
        for str_line in dict_always.get("lines", []) or []  # 遍历 always 内部源码行
    )

    # 组合 always 没有非阻塞赋值时通过。
    if not bool_has_nonblocking:

        # 赋值类型符合组合逻辑约束。
        return []

    # 组合 always 只允许阻塞赋值。
    return [
        QualityIssue(
            "VG022",
            _style_severity(strict),
            f"Combinational always `{str_header}` must use blocking assignments only.",
            str_rel_path,
            rule="always.comb_blocking",
        )
    ]

# 供 `_always_complex_lvalue_issues` 复用的拆分 helper，专门处理检查复杂左值 always 是否仍包含多个目标。
def _always_complex_lvalue_issues(
    dict_always: dict[str, Any],
    str_header: str,
    list_targets: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查复杂左值 always 是否仍包含多个目标。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_header: always 块头文本。
    :param list_targets: formatter 提取的赋值目标列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 复杂左值诊断列表。
    """

    # 复杂左值和多目标组合时 formatter 不能安全猜测拆分。
    if not dict_always.get("has_complex_lvalues") or len(set(list_targets)) <= 1:

        # 当前 always 无需人工拆分复杂左值。
        return []

    # 复杂左值多目标 always 需要人工或生成器显式拆分。
    return [
        QualityIssue(
            "VG020",
            _style_severity(strict),
            f"Always block `{str_header}` has complex lvalues and multiple targets; formatter must not guess a split.",
            str_rel_path,
            rule="always.complex_lvalue",
        )
    ]

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

# 对提取出的状态参数名执行 ST_ 大写约束，保持 FSM 命名一致。
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

# 供 `_instance_rules` 复用的拆分 helper，专门处理检查子模块实例名是否避免 u0/u1/inst 等泛化命名。
def _instance_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查子模块实例名是否避免 u0/u1/inst 等泛化命名。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 模块实例化相关诊断列表。
    """

    # list_issues 保存实例命名诊断。
    list_issues: list[QualityIssue] = []  # 实例命名规则诊断

    # 逐个实例检查是否仍是生成器默认名。
    for dict_inst in dict_module.get("instances", []) or []:

        # str_instance_name 是当前实例名。
        str_instance_name = str(dict_inst.get("instance_name") or "")  # 子模块实例名

        # 空实例名由 parser 诊断处理。
        if not str_instance_name:

            # 无实例名时跳过命名规则。
            continue

        # 泛化实例名不可用于生成交付 RTL。
        if str_instance_name in {"u0", "u1", "inst", "inst0"} or re.fullmatch(r"u\d+", str_instance_name):

            # 泛化实例名缺少连接语义。
            list_issues.append(
                QualityIssue(
                    "VG024",
                    _style_severity(strict),
                    f"Instance `{str_instance_name}` should be semantic, not generic u0/u1/inst.",
                    str_rel_path,
                    rule="instance.naming",
                )
            )

        # 推荐实例名包含 _Inst 结构。
        if "_Inst" not in str_instance_name and not str_instance_name.endswith("_Inst"):

            # 命名建议保持 warning，避免破坏兼容模块。
            list_issues.append(
                QualityIssue(
                    "VG024",
                    "warning",
                    f"Instance `{str_instance_name}` should follow `<module>_Inst_<role>` naming when practical.",
                    str_rel_path,
                    rule="instance.naming",
                )
            )

    # 返回实例命名诊断。
    return list_issues

# 供 `_region_rules` 复用的拆分 helper，专门处理检查非平凡 RTL 是否带有固定区域横幅并保持顺序。
def _region_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查非平凡 RTL 是否带有固定区域横幅并保持顺序。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 区域横幅和顺序相关诊断列表。
    """

    # list_issues 保存区域横幅缺失、乱序和实例区域诊断。
    list_issues: list[QualityIssue] = []  # 区域横幅规则诊断

    # int_body_activity 粗略衡量模块主体复杂度。
    int_body_activity = sum(  # 判断是否需要强制区域横幅的结构条目数量
        dict_module.get("counts", {}).get(str_key, 0)  # 单类 module 主体结构数量
        for str_key in ("decls", "assigns", "always", "instances", "generates")  # 结构计数键
    )

    # 简单 wrapper 或空叶子模块不强制区域横幅。
    if int_body_activity < 3:

        # 轻量模块跳过区域规则。
        return list_issues

    # tuple_region_keywords 保存当前规则源声明的区域顺序。
    tuple_region_keywords = _configured_region_keywords()  # 当前文件应遵守的区域横幅顺序

    # list_found 保存源码中出现的已知区域标题。
    list_found = [
        str_keyword  # 已命中的 Erie 区域标题
        for str_keyword in tuple_region_keywords  # 遍历当前生效的区域顺序表
        if str_keyword in str_text  # 仅记录源码实际包含的区域标题
    ]  # 源码中命中的 Erie 区域横幅标题

    # 非平凡模块必须至少有区域横幅。
    if not list_found:

        # 缺区域横幅直接登记 VG031。
        list_issues.append(
            QualityIssue(
                "VG031",
                _style_severity(strict),
                "Non-trivial RTL must use fixed Erie region banners.",
                str_rel_path,
                rule="regions.banner",
            )
        )

        # 没有任何区域时无需继续检查顺序。
        return list_issues

    # list_positions 按既定顺序记录区域标题在源码中的位置。
    list_positions = [
        str_text.find(str_keyword)  # 区域横幅在源码中的字符偏移
        for str_keyword in tuple_region_keywords  # 按当前规范区域顺序扫描
        if str_keyword in str_text  # 仅记录已出现标题的位置
    ]  # 已命中区域横幅在源码中的出现位置

    # 区域出现顺序必须和 REGION_KEYWORDS 一致。
    if list_positions != sorted(list_positions):

        # 顺序错乱会影响生成 RTL 的可扫描性。
        list_issues.append(
            QualityIssue(
                "VG031",
                _style_severity(strict),
                "Region banners appear out of the required order.",
                str_rel_path,
                rule="regions.order",
            )
        )

    # 有实例化时必须放在实例化区域，由顺序规则决定参数检查区是否覆盖最终位置。
    if dict_module.get("instances") and "模块实例化区域" not in str_text:

        # 实例区域缺失时登记 VG031。
        list_issues.append(
            QualityIssue(
                "VG031",
                _style_severity(strict),
                "Module instances must stay inside the 模块实例化区域 banner.",
                str_rel_path,
                rule="regions.instances_last",
            )
        )

    # 返回区域横幅诊断。
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

# 供 `_module_header_rules` 复用的拆分 helper，专门处理检查 module header 是否使用 ANSI 端口和分组注释。
def _module_header_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 module header 是否使用 ANSI 端口和分组注释。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 模块头部说明诊断列表。
    """

    # list_issues 保存 ANSI header、端口分组和 compact header 违规诊断。
    list_issues: list[QualityIssue] = []  # module header 合同诊断

    # str_severity 按 strict 控制 module header 风格违规级别。
    str_severity = _style_severity(strict)  # module header 规则严重级别

    # str_module_name 供 header 正则定位模块声明边界。
    str_module_name = str(dict_module.get("name") or "")  # header 正则匹配目标名

    # list_ports 是 ANSI header 规则需要检查的端口集合。
    list_ports = dict_module.get("ports", []) or []  # 当前 module 端口 AST 条目

    # 有端口但缺方向说明时不是 ANSI header。
    if list_ports and any(not str(dict_port.get("direction") or "") for dict_port in list_ports):

        # ANSI 端口声明要求方向写在 header。
        list_issues.append(
            QualityIssue(
                "VG008",
                str_severity,
                f"Module `{str_module_name}` must use ANSI-style port declarations "
                "with direction in the module header.",
                str_rel_path,
                rule="module.ansi_header",
            )
        )

    # str_header_text 截取当前 module header 区域。
    str_header_text = _module_header_region(str_text, str_module_name)  # 分组注释检查使用的 header 文本

    # 三个以上端口应有中文或协议分组注释。
    if len(list_ports) >= 3 and str_header_text and not _has_port_group_comment(str_header_text):

        # 分组注释帮助审查大型接口。
        list_issues.append(
            QualityIssue(
                "VG009",
                str_severity,
                f"Module `{str_module_name}` port list should use Chinese group comments "
                "such as 全局信号, 用户接口, or protocol 接口 groups.",
                str_rel_path,
                rule="ports.group_comments",
            )
        )

    # 旧式单行 module header 不适合生成交付 RTL。
    if re.search(r"^\s*module\s+\w+\s*\([^\n]*\);", str_text, re.MULTILINE) and list_ports:

        # compact header 会降低端口注释可读性。
        list_issues.append(
            QualityIssue(
                "VG008",
                str_severity,
                f"Module `{str_module_name}` should not use a compact legacy one-line header "
                "for generated delivery RTL.",
                str_rel_path,
                rule="module.ansi_header",
            )
        )

    # 返回 module header 诊断。
    return list_issues

# 供 `_pre_module_region` 复用的拆分 helper，专门处理首个 module 声明之前的文件头区域。
def _pre_module_region(str_text: str) -> str:
    """
    返回首个 module 声明之前的文件头区域。

    :param str_text: 当前 Verilog 源码文本。
    :return: 模块声明前的源码区域文本。
    """

    # str_module_pattern 捕获文件中最早出现的 module 声明。
    str_module_pattern = r"(?m)^\s*module\s+[A-Za-z_][A-Za-z0-9_]*\b"  # 文件头截断锚点正则

    # obj_match 定位文件头和首个 module 主体的分界。
    obj_match = re.search(str_module_pattern, str_text)  # 首个 module 声明匹配对象

    # 找到 module 时返回前缀，否则返回全文。
    return str_text[: obj_match.start()] if obj_match else str_text

# 供 `_module_header_region` 复用的拆分 helper，专门处理指定 module 的 header 文本区域。
def _module_header_region(str_text: str, str_module_name: str) -> str:
    """
    返回指定 module 的 header 文本区域。

    :param str_text: 当前 Verilog 源码文本。
    :param str_module_name: str_module_name 文本值，供质量门规则匹配。
    :return: 模块头部区域文本。
    """

    # 空 module 名称无法构造安全正则。
    if not str_module_name:

        # 无 module 名称时返回空 header。
        return ""

    # str_header_pattern 跨行捕获目标 module 的完整端口头。
    str_header_pattern = rf"(?ms)^\s*module\s+{re.escape(str_module_name)}\b.*?^\s*\);"  # header 截取正则

    # obj_match 定位指定 module ANSI header 的文本范围。
    obj_match = re.search(str_header_pattern, str_text)  # 指定 module header 匹配对象

    # 返回匹配到的 header 文本。
    return obj_match.group(0) if obj_match else ""

# 供 `_has_port_group_comment` 复用的拆分 helper，专门处理端口 header 是否包含中文或协议接口分组注释。
def _has_port_group_comment(str_header_text: str) -> bool:
    """
    判断端口 header 是否包含中文或协议接口分组注释。

    :param str_header_text: str_header_text 文本值，供质量门规则匹配。
    :return: 找到端口分组注释时返回 True。
    """

    # tuple_group_patterns 覆盖中文分组和常见协议接口分组。
    tuple_group_patterns = (  # module header 分组注释允许的模式
        re.compile(r"//[-\s]*全局信号[-\s]*//"),  # 全局信号分组横幅
        re.compile(r"//[-\s]*用户接口[-\s]*//"),  # 用户接口分组横幅
        PORT_GROUP_PROTOCOL_PATTERN,  # 协议接口分组
        PORT_GROUP_GENERIC_PATTERN,  # 通用接口分组说明
    )

    # 任一分组模式命中即可认为 header 有分组注释。
    return any(obj_pattern.search(str_header_text) for obj_pattern in tuple_group_patterns)

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

        # int_branch_line_no 把 block 内偏移换算成源码绝对行号。
        int_branch_line_no = int_block_line_start + int_offset  # 状态分支标签绝对行号

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
                    "FSM next-state block must default `state_next <= state_current;` or "
                    "`state_next = state_current;` before overrides.",
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

# 供 `_is_low_active_reset_name` 复用的拆分 helper，专门处理reset 信号名是否符合低有效约定。
def _is_low_active_reset_name(str_reset: str) -> bool:
    """
    判断 reset 信号名是否符合低有效约定。

    :param str_reset: reset 信号名称。
    :return: reset 名称表达低有效时返回 True。
    """

    # 共享角色函数同时支持 rstn、_rstn、_rst_n 和后续用途分段。
    return is_low_active_reset_name(str_reset)

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
    _module_rules
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
