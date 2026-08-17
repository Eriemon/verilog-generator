"""封装结构化注释、同线注释与重复注释规则。"""

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

# 注释规则只保留当前模块实际会消费的上下文与问题类型。
from .quality_gate_types import (
    CommentReuseCandidate,
    CommentVerticalSpacingContext,
    QualityIssue,
    SameLineCommentCheckContext,
    StructuredCommentContext,
)

# 重复注释判定与分组注释集合继续复用共享常量。
from .quality_gate_common import (
    BLOCK_LEADING_COMMENT_COLLECTIONS,
    CJK_PATTERN,
    COMMENT_REUSE_MIN_CJK_CHARS,
)

# 相似度阈值与过程赋值集合单独成组，便于追踪注释覆盖规则来源。
from .quality_gate_common import (
    COMMENT_REUSE_SIMILARITY_THRESHOLD,
    PARAM_SIGNAL_GROUP_REGIONS,
    PROCEDURAL_ASSIGNMENT_COLLECTIONS,
)

# 过程赋值忽略前缀和行号换算 helper 保持显式导入。
from .quality_gate_common import (
    PROCEDURAL_ASSIGNMENT_IGNORED_PREFIXES,
    _as_line,
    _assignment_lhs_label,
)

# 注释语义 helper 单独成组，方便定位空泛中文与 fallback 判定。
from .quality_gate_common import (
    _comment_has_meaningful_chinese,
    _comment_severity,
    _is_fallback_comment,
    _is_generic_comment,
)

# 注释有效性与区域标题 helper 单独成组，避免继续依赖 `*` 展开。
from .quality_gate_common import (
    _is_hollow_chinese_comment,
    _is_pure_line_comment,
    _is_region_banner_line,
    _line_comment,
)

# 缩进、区域和声明前导判断 helper 继续显式列出。
from .quality_gate_common import (
    _line_indent,
    _line_region_titles,
    _nearest_region_title,
    _previous_line_is_definition,
)

# 代码行筛选、赋值类型判断与 span 标签 helper 复用共享实现。
from .quality_gate_common import (
    _has_blocking_assignment,
    _has_nonblocking_assignment,
    _is_code_line,
    _span_item_label,
    _strip_line_comment,
)

# 供 `_comment_rules` 复用的拆分 helper，专门处理检查声明、赋值、always 和实例的注释覆盖及语义。
def _comment_rules(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查声明、赋值、always 和实例的注释覆盖及语义。

    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :return: 注释覆盖和语义相关诊断列表。
    """

    # list_issues 汇总所有注释相关诊断。
    list_issues: list[QualityIssue] = []  # 注释规则诊断

    # str_severity 根据 strict 决定缺注释问题级别。
    str_severity = _comment_severity(strict)  # 注释覆盖严重级别

    # list_lines 供需要精确空行和同线注释位置的规则复用。
    list_lines = str_text.splitlines()  # 当前 Verilog 源码行列表

    # dict_region_by_line 记录区域横幅位置，供分组注释和前导空行规则定位。
    dict_region_by_line = _line_region_titles(str_text)  # 源码区域横幅索引

    # 逐个 module 检查声明同 行注释与块级 leading comment。
    for dict_module in dict_ast_report.get("modules", []) or []:

        # tuple_structured_args 固定结构化注释 helper 的参数顺序。
        tuple_structured_args = (dict_module, str_rel_path, str_severity, comment_language)  # 声明注释检查参数

        # 结构化声明和赋值注释检查拆给 helper。
        list_structured_issues = _structured_comment_issues(*tuple_structured_args)  # 声明和连续赋值注释诊断

        # 合并结构化条目的注释诊断。
        list_issues.extend(list_structured_issues)

        # tuple_block_args 固定前导注释 helper 的参数顺序。
        tuple_block_args = (dict_module, list_lines, str_rel_path, str_severity)  # 块前导注释检查参数

        # always/function/task/generate/initial 和 instance 的前导注释单独检查。
        list_block_comment_issues = _block_comment_issues(*tuple_block_args)  # 过程块和实例前导注释诊断

        # 合并块级前导注释诊断。
        list_issues.extend(list_block_comment_issues)

        # tuple_assign_group_args 固定 assign 子分组空行 helper 的参数顺序。
        tuple_assign_group_args = (dict_module, list_lines, str_rel_path, str_severity)  # assign 子分组空行检查参数

        # assign 纯分组注释存在时，同样必须满足唯一空行或区域横幅直连规则。
        list_assign_group_spacing_issues = _assign_group_comment_spacing_issues(*tuple_assign_group_args)  # assign 子分组布局诊断

        # 合并 assign 子分组空行布局诊断。
        list_issues.extend(list_assign_group_spacing_issues)

        # tuple_procedural_args 固定过程赋值注释 helper 的参数顺序。
        tuple_procedural_args = (dict_module, list_lines, str_rel_path, str_severity, comment_language)  # 过程赋值参数

        # 过程块内部赋值必须具备同线语义注释。
        list_procedural_issues = _procedural_assignment_comment_issues(*tuple_procedural_args)  # 过程赋值注释诊断

        # 合并过程赋值注释诊断。
        list_issues.extend(list_procedural_issues)

        # tuple_instance_args 固定实例关联注释 helper 的参数顺序。
        tuple_instance_args = tuple_procedural_args  # 实例关联注释检查参数

        # 实例化参数和端口连线必须具备同线语义注释。
        list_instance_mapping_issues = _instance_mapping_comment_issues(*tuple_instance_args)  # 实例关联注释诊断

        # 合并实例关联注释诊断。
        list_issues.extend(list_instance_mapping_issues)

        # tuple_group_args 固定定义分组注释 helper 的参数顺序。
        tuple_group_args = (dict_module, list_lines, dict_region_by_line, str_rel_path, str_severity)  # 分组注释参数

        # 参数和信号定义区域必须有分组注释。
        list_group_comment_issues = _definition_group_comment_issues(*tuple_group_args)  # 参数和信号分组诊断

        # 合并定义分组注释诊断。
        list_issues.extend(list_group_comment_issues)

        # list_reuse_candidates 只收集绑定 RTL 实体的语义注释。
        list_reuse_candidates = _comment_reuse_candidates_for_module(  # 当前 module 的实体注释候选
            dict_module,  # 重复检测所在 module
            list_lines,  # 重复检测源码行
            str_rel_path,  # 重复诊断相对路径
        )  # VG066 重复注释候选集合

        # 重复或近似复用注释应在后出现的实体上报告。
        list_issues.extend(_comment_reuse_issues(list_reuse_candidates, str_severity))

    # 注释覆盖率按文本行统计。
    list_issues.extend(_comment_density_issues(str_text, str_rel_path, strict=strict))

    # 返回 AST 结构注释和文本密度合并后的诊断。
    return list_issues

# 供 `_structured_comment_issues` 复用的拆分 helper，专门处理检查 AST 结构条目的同 行语义注释。
def _structured_comment_issues(
    dict_module: dict[str, Any],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查 AST 结构条目的同 行语义注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: str_severity 文本值，供质量门规则匹配。
    :param comment_language: 注释语言策略。
    :return: 结构化声明和语句的注释诊断列表。
    """

    # list_issues 保存声明、端口、assign 的 same-line 注释诊断。
    list_issues: list[QualityIssue] = []  # 结构化注释覆盖诊断

    # tuple_collections 定义需要 same-line 注释的 AST 集合。
    tuple_collections = (  # 结构化注释覆盖规则需要扫描的 AST 集合
        ("params", "parameter"),  # parameter 条目及诊断标签
        ("localparams", "localparam"),  # 状态和普通常量条目
        ("ports", "port"),  # 端口条目及诊断标签
        ("decls", "signal"),  # 内部声明条目及诊断标签
        ("assigns", "assign"),  # 连线语句条目
    )

    # 遍历所有声明/赋值集合。
    for str_collection_name, str_label in tuple_collections:

        # 当前集合中每个条目都应有语义注释。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # 当前 AST 条目的注释诊断交给单项 helper，降低主循环嵌套。
            list_item_issues = _structured_comment_item_issues(  # 单个 AST 条目的注释质量诊断
                dict_item,  # 当前待检查的 AST 条目
                str_rel_path,  # 诊断报告中的相对文件路径
                str_severity,  # strict 模式决定的覆盖类严重级别
                comment_language,  # 当前注释语言策略
                str_label,  # 当前 AST 集合对应的诊断标签
            )

            # 单项诊断保持原始扫描顺序并入模块级列表。
            list_issues.extend(list_item_issues)

    # 返回结构化注释诊断。
    return list_issues

# 供 `_structured_comment_item_issues` 复用的拆分 helper，专门处理单个结构化条目的注释质量诊断。
def _structured_comment_item_issues(
    dict_item: dict[str, Any],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
    str_label: str,
) -> list[QualityIssue]:
    """
    返回单个结构化条目的注释质量诊断。

    :param dict_item: formatter AST 中的参数、端口、声明或 assign 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: strict 模式决定的覆盖类严重级别。
    :param comment_language: 注释语言策略。
    :param str_label: 诊断中展示的 AST 条目类别。
    :return: 当前条目的注释诊断列表。
    """

    # list_issues 保存当前条目产生的注释诊断。
    list_issues: list[QualityIssue] = []  # 单条 AST 注释诊断集合

    # str_name 优先使用 name，assign 使用 lhs。
    str_name = str(dict_item.get("name") or dict_item.get("lhs") or "")  # 被检查条目名称

    # str_comment 是 formatter AST 提取出的同 行注释。
    str_comment = str(dict_item.get("comment") or "").strip()  # 条目关联注释正文

    # int_line_no 把注释问题绑定回 formatter AST 的实体起始行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 注释诊断实体行号

    # structured_context 统一携带该条 AST 注释诊断所需字段。
    structured_context = StructuredCommentContext(  # 单条结构化注释诊断上下文
        str_name=str_name,  # 被检查结构条目名称
        str_label=str_label,  # 诊断文本使用的条目类别
        str_rel_path=str_rel_path,  # 报告中的文件路径
        int_line_no=int_line_no,  # 条目源码起始行
        str_severity=str_severity,  # strict 派生的严重级别
        comment_language=comment_language,  # 当前条目适用的中文优先策略
    )

    # 缺少注释时登记覆盖问题并结束后续语义检查。
    if not str_comment:

        # 缺注释时没有更多语义可检查。
        return [
            _missing_structured_comment_issue(structured_context)
        ]

    # 深度诊断覆盖 fallback、空洞中文和语言策略三类语义问题。
    list_issues.extend(_structured_comment_depth_issues(str_comment, structured_context))

    # 泛化诊断单独保留 warning 语义，不和缺失注释合并。
    list_issues.extend(_generic_structured_comment_issues(str_comment, structured_context))

    # 返回当前条目的全部注释诊断。
    return list_issues

# 供 `_missing_structured_comment_issue` 复用的拆分 helper，专门处理缺失 same-line 注释转换为 VG040 覆盖诊断。
def _missing_structured_comment_issue(structured_context: StructuredCommentContext) -> QualityIssue:
    """
    把缺失 same-line 注释转换为 VG040 覆盖诊断。

    :param structured_context: 单个结构化条目的注释诊断上下文。
    :return: 缺注释诊断。
    """

    # VG040 覆盖诊断保留条目类别和源码实体行。
    return QualityIssue(
        "VG040",
        structured_context.str_severity,
        f"{structured_context.str_label} `{structured_context.str_name}` should have a same-line semantic comment.",
        structured_context.str_rel_path,
        structured_context.int_line_no,
        rule="comments.coverage",
    )

# 供 `_structured_comment_depth_issues` 复用的拆分 helper，专门处理检查结构化条目注释是否避免 fallback、空洞中文和纯英文兜底。
def _structured_comment_depth_issues(
    str_comment: str,
    structured_context: StructuredCommentContext,
) -> list[QualityIssue]:
    """
    检查结构化条目注释是否避免 fallback、空洞中文和纯英文兜底。

    :param str_comment: 条目关联注释正文。
    :param structured_context: 单个结构化条目的注释诊断上下文。
    :return: 注释语义深度诊断列表。
    """

    # formatter fallback 注释不能出现在最终交付代码中。
    if _is_fallback_comment(str_comment):

        # VG056 比旧 VG041 更明确地表达交付阻断原因。
        return [
            QualityIssue(
                "VG056",
                structured_context.str_severity,
                f"Comment for {structured_context.str_label} `{structured_context.str_name}` "
                "still uses formatter fallback text.",
                structured_context.str_rel_path,
                structured_context.int_line_no,
                rule="comments.no_fallback",
            )
        ]

    # 中文但空洞的注释不能满足实体级语义说明要求。
    if _is_hollow_chinese_comment(str_comment):

        # VG055 阻止“有中文字符但没有 RTL 意图”的注释放行。
        return [
            QualityIssue(
                "VG055",
                structured_context.str_severity,
                f"Comment for {structured_context.str_label} `{structured_context.str_name}` "
                "is hollow and must describe RTL intent.",
                structured_context.str_rel_path,
                structured_context.int_line_no,
                rule="comments.semantic_depth",
            )
        ]

    # 中文模式下要求注释包含具体中文语义。
    if structured_context.comment_language == "zh" and not _comment_has_meaningful_chinese(str_comment):

        # str_message 给纯英文注释问题生成稳定诊断文本。
        str_message = (  # 中文优先注释诊断文本
            f"Comment for {structured_context.str_label} `{structured_context.str_name}` should be "
            "Chinese-first and semantic, not only fallback text."
        )

        # 纯英文或兜底词注释只能作为 warning。
        return [
            QualityIssue(
                "VG041",
                "warning",
                str_message,
                structured_context.str_rel_path,
                structured_context.int_line_no,
                rule="comments.semantic",
            )
        ]

    # 当前注释满足实体级语义要求。
    return []

# 供 `_generic_structured_comment_issues` 复用的拆分 helper，专门处理识别结构化条目注释是否仍是泛化说明。
def _generic_structured_comment_issues(
    str_comment: str,
    structured_context: StructuredCommentContext,
) -> list[QualityIssue]:
    """
    识别结构化条目注释是否仍是泛化说明。

    :param str_comment: 条目关联注释正文。
    :param structured_context: 单个结构化条目的注释诊断上下文。
    :return: 泛化注释诊断列表。
    """

    # 非泛化文本不需要额外登记 VG041。
    if not _is_generic_comment(str_comment):

        # 当前注释不属于泛化说明。
        return []

    # 泛化注释不能证明 RTL 意图。
    return [
        QualityIssue(
            "VG041",
            "warning",
            f"Comment for {structured_context.str_label} `{structured_context.str_name}` "
            f"looks generic: `{str_comment}`.",
            structured_context.str_rel_path,
            structured_context.int_line_no,
            rule="comments.semantic",
        )
    ]

# 供 `_block_comment_issues` 复用的拆分 helper，专门处理检查过程块和实例化是否有邻近说明注释。
def _block_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查过程块和实例化是否有邻近说明注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: str_severity 文本值，供质量门规则匹配。
    :return: 块注释相关诊断列表。
    """

    # list_issues 保存块级注释诊断。
    list_issues: list[QualityIssue] = []  # 块级注释诊断

    # 逐类检查过程块上方的说明注释。
    for str_collection_name, str_label, str_rule in BLOCK_LEADING_COMMENT_COLLECTIONS:

        # 当前集合中每个块都必须有前导说明。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # str_block_name 优先展示 header，其次展示 span 标签。
            str_block_name = str(dict_block.get("header") or _span_item_label(dict_block))  # 过程块诊断展示名称

            # 缺少前导注释时登记覆盖问题。
            if not dict_block.get("leading_comments"):

                # 行为说明应紧贴过程块上方。
                list_issues.append(
                    QualityIssue(
                        "VG040",
                        str_severity,
                        f"{str_label} `{str_block_name}` should have a nearby leading comment explaining behavior.",
                        str_rel_path,
                        _as_line(dict_block.get("line_start")),
                        rule=str_rule,
                    )
                )

                # 没有说明注释时不再检查位置布局。
                continue

            # 已有前导注释时继续检查相邻行、空行和缩进。
            tuple_layout_args = (dict_block, list_lines, str_rel_path, str_severity, str_label, str_rule)  # 块布局参数

            # 当前过程块的前导注释布局诊断。
            list_layout_issues = _leading_comment_layout_issues(*tuple_layout_args)  # 过程块前导布局诊断

            # 合并当前过程块的布局诊断。
            list_issues.extend(list_layout_issues)

    # 子模块实例化前应有连接或功能说明。
    for dict_inst in dict_module.get("instances", []) or []:

        # 缺 leading comment 时登记实例注释问题。
        if not dict_inst.get("leading_comments"):

            # 实例说明有助于审查跨模块连接意图。
            list_issues.append(
                QualityIssue(
                    "VG040",
                    str_severity,
                    f"Instance `{dict_inst.get('instance_name')}` should have a leading function/connection comment.",
                    str_rel_path,
                    _as_line(dict_inst.get("line_start")),
                    rule="comments.instance",
                )
            )

            # 缺说明时不再检查位置布局。
            continue

        # 已有实例说明时检查其相邻行、空行和缩进。
        tuple_instance_layout_args = (  # 实例前导布局 helper 参数
            dict_inst,  # 当前实例 AST 条目
            list_lines,  # 前导注释所在源码行
            str_rel_path,  # 实例布局诊断路径
            str_severity,  # 实例布局问题严重级别
            "Instance",  # 实例布局诊断标签
            "comments.instance",  # 实例功能说明规则名
        )

        # 实例功能说明的前导布局诊断。
        list_instance_layout_issues = _leading_comment_layout_issues(*tuple_instance_layout_args)  # 实例前导布局诊断

        # 合并实例前导注释布局诊断。
        list_issues.extend(list_instance_layout_issues)

    # 返回块级注释诊断。
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

# 供 `_leading_comment_layout_issues` 复用的拆分 helper，专门处理检查块或实例前导注释是否紧贴目标结构并满足空行规则。
def _leading_comment_layout_issues(
    dict_item: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    str_label: str,
    str_rule: str,
) -> list[QualityIssue]:
    """
    检查块或实例前导注释是否紧贴目标结构并满足空行规则。

    :param dict_item: formatter AST 中的块或实例条目。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 注释布局问题的严重级别。
    :param str_label: 诊断中展示的结构类别。
    :param str_rule: 诊断规则命名空间。
    :return: 前导注释布局诊断列表。
    """

    # int_line_no 是目标结构的起始行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 结构起始行号

    # 缺少行号时由 AST span 规则负责，本规则不伪造位置。
    if int_line_no is None:

        # 无法定位前一行时不追加布局诊断。
        return []

    # 复用统一的指定行号布局检查逻辑。
    return _leading_comment_layout_issues_for_line_no(
        int_line_no,
        list_lines,
        str_rel_path,
        str_severity,
        str_label,
        str_rule,
    )

# 供 `_assign_group_comment_spacing_issues` 复用的拆分 helper，专门处理检查 assign 纯分组注释上方是否满足唯一空行或区域横幅直连规则。
def _assign_group_comment_spacing_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 assign 纯分组注释上方是否满足唯一空行或区域横幅直连规则。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 空行布局问题的严重级别。
    :return: assign 子分组空行布局诊断列表。
    """

    # list_issues 保存 assign 子分组空行布局诊断。
    list_issues: list[QualityIssue] = []  # assign 子分组空行诊断

    # 逐条扫描连续赋值，只有写了纯分组注释时才附加 VG067 约束。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 无前导纯注释时不强制要求 assign 必须额外补分组说明。
        if not dict_assign.get("leading_comments"):

            # 当前 assign 没有子分组注释，跳过 VG067。
            continue

        # int_line_no 是 assign 代码所在行，用于反查注释位置。
        int_line_no = _as_line(dict_assign.get("line_start"))  # assign 源码行号

        # 缺少源码行号或越界时不伪造 VG067 落点。
        if int_line_no is None or int_line_no <= 1 or int_line_no > len(list_lines):

            # 行号无效时交给 span 规则，不重复报布局问题。
            continue

        # int_comment_line_no 是 assign 正上方一行的纯注释候选。
        int_comment_line_no = int_line_no - 1  # assign 子分组注释候选行号

        # AST 和源码若不同步，避免对非纯注释行错误附加 VG067。
        if not _is_pure_line_comment(list_lines[int_comment_line_no - 1]):

            # 只有真实纯注释才参与分组空行布局检查。
            continue

        # assign_spacing_context 只保存 assign 子分组布局诊断需要的规则元数据。
        assign_spacing_context = CommentVerticalSpacingContext(  # assign 子分组空行上下文
            str_rel_path,  # assign 分组布局诊断路径
            str_severity,  # assign 分组布局问题级别
            "VG067",  # assign 子分组空行规则码
            "Assign group",  # assign 子分组诊断标签
            "comments.assign_group_spacing",  # assign 子分组规则路径
        )

        # 纯分组注释上方必须满足唯一空行或区域横幅直连规则。
        list_issues.extend(_comment_vertical_spacing_issues(list_lines, int_comment_line_no, assign_spacing_context))

    # 返回 assign 子分组空行布局诊断。
    return list_issues

# 供 `_procedural_assignment_comment_issues` 复用的拆分 helper，专门处理检查 always/function/task/generate/initial 内赋值语句的同线注释。
def _procedural_assignment_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查 always/function/task/generate/initial 内赋值语句的同线注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: 过程赋值注释诊断列表。
    """

    # list_issues 跨所有过程块累计右侧注释缺失或失效的 VG062 结果。
    list_issues: list[QualityIssue] = []  # 跨过程块赋值注释问题集合

    # 逐类过程块扫描内部赋值语句。
    for str_collection_name in PROCEDURAL_ASSIGNMENT_COLLECTIONS:

        # 当前集合中每个块按源码 span 扫描。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # tuple_block_args 固定单块过程赋值 helper 的参数顺序。
            tuple_block_args = (dict_block, list_lines, str_rel_path, str_severity, comment_language)  # 单块赋值参数

            # 当前块的过程赋值检查拆给单块 helper。
            list_issues.extend(_procedural_assignment_issues_for_block(*tuple_block_args))

    # 返回过程赋值注释诊断。
    return list_issues

# 供 `_procedural_assignment_issues_for_block` 复用的拆分 helper，专门处理检查单个过程块 span 内的赋值语句同线注释。
def _procedural_assignment_issues_for_block(
    dict_block: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查单个过程块 span 内的赋值语句同线注释。

    :param dict_block: formatter AST 中的单个过程块。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: 当前过程块的赋值注释诊断。
    """

    # list_issues 保存当前块内的赋值注释诊断。
    list_issues: list[QualityIssue] = []  # 当前过程块赋值注释诊断

    # int_line_start 和 int_line_end 是当前块的源码范围。
    int_line_start = _as_line(dict_block.get("line_start"))  # 过程块起始行

    # int_line_end 是当前块扫描的结束边界。
    int_line_end = _as_line(dict_block.get("line_end"))  # 过程块结束行

    # 缺少 span 时无法精确扫描。
    if int_line_start is None or int_line_end is None:

        # AST span 可信度由 VG050 负责。
        return list_issues

    # 约束结束行不越过文件实际行数。
    int_last_line = min(int_line_end, len(list_lines))  # 实际可扫描结束行

    # same_line_comment_check_context_process 绑定过程赋值专用的规则码和实体标签。
    same_line_comment_check_context_process: SameLineCommentCheckContext = _procedural_assignment_comment_context(  # VG062 过程赋值检查上下文
        str_rel_path,  # 过程赋值诊断路径
        str_severity,  # 过程赋值缺注释严重级别
        comment_language,  # 过程赋值注释语言策略
    )

    # 逐行扫描当前过程块。
    for int_line_no in range(int_line_start, int_last_line + 1):

        # str_line 是当前源码行。
        str_line = list_lines[int_line_no - 1]  # 当前过程块源码行

        # 非过程赋值行不需要同线注释。
        if not _is_procedural_assignment_line(str_line):

            # 继续扫描后续行。
            continue

        # 当前赋值语句的注释诊断交给统一 helper。
        list_issues.extend(
            _same_line_assignment_comment_issues(
                str_line,
                int_line_no,
                same_line_comment_check_context_process,
            )
        )

    # 返回当前过程块的赋值注释诊断。
    return list_issues

# 供 `_instance_mapping_comment_issues` 复用的拆分 helper，专门处理检查实例化参数和端口连线是否带同线语义注释。
def _instance_mapping_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查实例化参数和端口连线是否带同线语义注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: 实例关联注释诊断列表。
    """

    # list_issues 收集实例参数和端口映射的同线注释问题。
    list_issues: list[QualityIssue] = []  # 实例映射注释覆盖问题集合

    # same_line_comment_check_context_instance 绑定实例映射专用的规则码和实体标签。
    same_line_comment_check_context_instance: SameLineCommentCheckContext = _instance_mapping_comment_context(  # VG064 实例映射检查上下文
        str_rel_path,  # 实例映射诊断路径
        str_severity,  # 实例映射缺注释严重级别
        comment_language,  # 实例映射注释语言策略
    )

    # 遍历 module 内的每个实例化。
    for dict_inst in dict_module.get("instances", []) or []:

        # 当前实例的源码范围。
        int_line_start = _as_line(dict_inst.get("line_start"))  # 实例起始行

        # int_line_end 是当前实例连接列表结束边界。
        int_line_end = _as_line(dict_inst.get("line_end"))  # 实例结束行

        # 缺少 span 时不做文本扫描。
        if int_line_start is None or int_line_end is None:

            # VG050 会单独报告实例 span 缺失。
            continue

        # int_last_line 防止异常 span 越界。
        int_last_line = min(int_line_end, len(list_lines))  # 实例可扫描结束行

        # 逐行扫描实例关联。
        for int_line_no in range(int_line_start, int_last_line + 1):

            # str_line 是当前实例源码行。
            str_line = list_lines[int_line_no - 1]  # 当前实例行

            # 非 .formal(actual) 关联行不需要 VG064。
            if not _is_instance_association_line(str_line):

                # 继续扫描下一个实例行。
                continue

            # 实例关联同线注释必须存在且具备语义。
            list_issues.extend(
                _same_line_assignment_comment_issues(
                    str_line,
                    int_line_no,
                    same_line_comment_check_context_instance,
                )
            )

    # 返回实例关联注释诊断。
    return list_issues

# 供 `_definition_group_comment_issues` 复用的拆分 helper，专门处理检查参数和信号定义区域是否有分组说明注释。
def _definition_group_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查参数和信号定义区域是否有分组说明注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :return: 参数和信号分组注释诊断列表。
    """

    # list_issues 保存分组注释诊断。
    list_issues: list[QualityIssue] = []  # 参数和信号分组注释诊断

    # list_definition_items 汇总需要检查分组说明的 module body 定义。
    list_definition_items = _definition_group_items(dict_module)  # 参数和信号定义条目

    # 逐个定义检查其所在区域和上方分组注释。
    for dict_item, str_label in list_definition_items:

        # tuple_item_args 保持 VG065 单条定义检查参数顺序。
        tuple_item_args = (
            dict_item,  # 待检查的参数或信号定义
            str_label,  # 定义类别诊断标签
            list_lines,  # 分组注释所在源码行
            dict_region_by_line,  # 区域横幅定位索引
            str_rel_path,  # VG065 诊断路径
            str_severity,  # VG065 严重级别
        )

        # 单个定义的分组注释检查拆给 helper。
        list_issues.extend(_definition_group_comment_issues_for_item(*tuple_item_args))

    # 返回参数和信号分组注释诊断。
    return list_issues

# 供 `_definition_group_items` 复用的拆分 helper，专门处理需要检查分组注释的参数和信号定义条目。
def _definition_group_items(dict_module: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """
    返回需要检查分组注释的参数和信号定义条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 按源码行排序的 `(item, label)` 条目列表。
    """

    # list_definition_items 承载参数和信号定义的统一扫描队列。
    list_definition_items: list[tuple[dict[str, Any], str]] = []  # 分组规则候选定义条目

    # localparam 属于参数定义区域。
    for dict_param in dict_module.get("localparams", []) or []:

        # 收集局部参数定义。
        list_definition_items.append((dict_param, "parameter"))

    # decls 属于信号定义区域。
    for dict_decl in dict_module.get("decls", []) or []:

        # 收集内部信号定义。
        list_definition_items.append((dict_decl, "signal"))

    # 按源码行号排序，确保连续定义分组判断稳定。
    list_definition_items.sort(key=lambda item: _as_line(item[0].get("line_start")) or 0)

    # 返回可供 VG065 检查的定义条目。
    return list_definition_items

# 供 `_definition_group_comment_issues_for_item` 复用的拆分 helper，专门处理检查单条参数或信号定义是否由分组注释引入。
def _definition_group_comment_issues_for_item(
    dict_item: dict[str, Any],
    str_label: str,
    list_lines: list[str],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查单条参数或信号定义是否由分组注释引入。

    :param dict_item: formatter AST 中的参数或声明条目。
    :param str_label: 诊断中展示的定义类别。
    :param list_lines: 当前 Verilog 源码行列表。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :return: 单条定义的分组注释诊断。
    """

    # list_issues 保存当前定义的分组注释诊断。
    list_issues: list[QualityIssue] = []  # 当前定义分组注释诊断

    # tuple_context 保存定义行号和所在区域。
    tuple_context = _definition_group_item_context(dict_item, list_lines, dict_region_by_line)  # 定义分组上下文

    # 无需检查当前条目时直接返回。
    if tuple_context is None:

        # module header 或同组后续定义不需要本规则重复报告。
        return list_issues

    # 解包当前定义所在行和区域标题。
    int_line_no, str_region_title = tuple_context  # 定义分组检查定位信息

    # str_previous_line 是当前定义正上方一行。
    str_previous_line = list_lines[int_line_no - 2]  # 定义上方一行

    # 区域横幅只是导航锚点，不能替代分组注释。
    if _is_region_banner_line(str_previous_line):

        # 横幅后直接跟定义时说明缺少真正分组说明。
        quality_issue_missing_group_comment = _missing_definition_group_comment_issue(  # 横幅直连定义的 VG065 诊断
            dict_item,  # 横幅下方首个参数或信号 AST 条目
            str_label,  # 横幅直连场景的定义类别
            str_region_title,  # 当前定义所在的中文区域标题
            str_rel_path,  # 横幅直连缺分组说明的报告路径
            str_severity,  # strict 模式派生的横幅直连级别
            int_line_no,  # 横幅后首条定义的源码行号
        )

        # 返回当前定义的缺失分组注释诊断。
        return [
            quality_issue_missing_group_comment,
        ]

    # 分组注释必须紧贴定义上方。
    if not _is_pure_line_comment(str_previous_line):

        # 缺少分组说明会让参数/信号区域不可扫描。
        quality_issue_missing_group_comment = _missing_definition_group_comment_issue(  # 非注释前导行的 VG065 诊断
            dict_item,  # 前导行不是纯注释的定义 AST 条目
            str_label,  # 非注释前导场景的定义类别
            str_region_title,  # 需要分组说明的区域标题
            str_rel_path,  # 非注释前导缺分组说明的报告路径
            str_severity,  # strict 模式派生的非注释前导级别
            int_line_no,  # 缺少贴邻分组注释的定义行号
        )

        # 记录当前定义缺少分组说明。
        list_issues.append(quality_issue_missing_group_comment)

        # 没有分组注释时不继续检查布局。
        return list_issues

    # 分组注释必须和定义最左列对齐。
    if _line_indent(str_previous_line) != _line_indent(list_lines[int_line_no - 1]):

        # 缩进错位说明分组注释没有绑定当前定义组。
        list_issues.append(
            QualityIssue(
                "VG065",
                str_severity,
                f"{str_label} group comment must align with the definition start column.",
                str_rel_path,
                int_line_no - 1,
                rule="comments.definition_group",
            )
        )

    # definition_spacing_context 保留定义分组布局诊断所需的路径和规则编号。
    definition_spacing_context = CommentVerticalSpacingContext(  # 定义分组空行布局上下文
        str_rel_path,  # 当前定义组在报告中的相对路径
        str_severity,  # 分组注释布局严重级别
        "VG065",  # 定义分组注释空行规则码
        f"{str_label} group",  # 定义分组注释诊断标签
        "comments.definition_group",  # 定义分组注释规则路径
    )

    # 分组注释上方同样遵循唯一空行或紧邻区域横幅规则。
    list_issues.extend(_comment_vertical_spacing_issues(list_lines, int_line_no - 1, definition_spacing_context))

    # 返回当前定义的 VG065 布局诊断。
    return list_issues

# 供 `_definition_group_item_context` 复用的拆分 helper，专门处理单条定义需要检查分组注释时的行号和区域。
def _definition_group_item_context(
    dict_item: dict[str, Any],
    list_lines: list[str],
    dict_region_by_line: dict[int, str],
) -> tuple[int, str] | None:
    """
    返回单条定义需要检查分组注释时的行号和区域。

    :param dict_item: formatter AST 中的参数或声明条目。
    :param list_lines: 当前 Verilog 源码行列表。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :return: 需要检查时返回 `(line_no, region_title)`，否则返回 None。
    """

    # int_line_no 是定义所在行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 定义行号

    # 无法定位或越界时跳过。
    if int_line_no is None or int_line_no <= 1 or int_line_no > len(list_lines):

        # 无 span 的定义由 VG050 统一覆盖。
        return None

    # str_region_title 是定义所在的区域标题。
    str_region_title = _nearest_region_title(dict_region_by_line, int_line_no)  # 定义所在区域

    # module header 参数和端口由结构化注释规则负责。
    if str_region_title not in PARAM_SIGNAL_GROUP_REGIONS:

        # 当前条目不属于本规则负责的定义区域。
        return None

    # 连续定义行共享同一个上方分组注释，非首行不重复要求。
    if _previous_line_is_definition(list_lines, int_line_no):

        # 当前定义属于同一组的后续定义。
        return None

    # 返回分组注释检查所需上下文。
    return int_line_no, str_region_title

# 供 `_missing_definition_group_comment_issue` 复用的拆分 helper，专门处理构造参数或信号定义缺少分组注释的诊断。
def _missing_definition_group_comment_issue(
    dict_item: dict[str, Any],
    str_label: str,
    str_region_title: str,
    str_rel_path: str,
    str_severity: str,
    int_line_no: int,
) -> QualityIssue:
    """
    构造参数或信号定义缺少分组注释的诊断。

    :param dict_item: formatter AST 中的参数或声明条目。
    :param str_label: 诊断中展示的定义类别。
    :param str_region_title: 当前定义所在区域标题。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param int_line_no: 定义源码行号。
    :return: 缺少分组注释的 VG065 诊断。
    """

    # str_name 用于把诊断绑定到具体参数或信号。
    str_name = str(dict_item.get("name") or "")  # 缺少分组注释的定义名

    # 返回统一的分组注释缺失诊断。
    return QualityIssue(
        "VG065",
        str_severity,
        f"{str_label} `{str_name}` must be introduced by a group comment in `{str_region_title}`.",
        str_rel_path,
        int_line_no,
        rule="comments.definition_group",
    )

# 供 `_procedural_assignment_comment_context` 复用的拆分 helper，专门处理构造过程赋值同线注释检查上下文。
def _procedural_assignment_comment_context(
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> SameLineCommentCheckContext:
    """
    构造过程赋值同线注释检查上下文。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: VG062 同线注释检查上下文。
    """

    # tuple_context_args 按上下文字段顺序组装过程赋值检查配置。
    tuple_context_args = (
        str_rel_path,  # 过程赋值问题所在文件
        str_severity,  # 过程赋值缺尾注门禁等级
        comment_language,  # 过程赋值语义注释语言
        "VG062",  # 过程赋值尾注规则码
        "process assignment",  # 过程赋值报告实体名
        "comments.procedural_assignment",  # 过程赋值规则路径
    )  # 过程赋值检查上下文构造参数

    # 返回过程赋值专用规则码和标签。
    return SameLineCommentCheckContext(*tuple_context_args)

# 供 `_instance_mapping_comment_context` 复用的拆分 helper，专门处理构造实例参数和端口关联同线注释检查上下文。
def _instance_mapping_comment_context(
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> SameLineCommentCheckContext:
    """
    构造实例参数和端口关联同线注释检查上下文。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: VG064 同线注释检查上下文。
    """

    # tuple_context_args 按上下文字段顺序组装实例映射检查配置。
    tuple_context_args = (
        str_rel_path,  # 实例连线问题所在文件
        str_severity,  # 实例连线缺尾注门禁等级
        comment_language,  # 实例连线语义注释语言
        "VG064",  # 实例连线尾注规则码
        "instance mapping",  # 实例连线报告实体名
        "comments.instance_mapping",  # 实例连线规则路径
    )  # 实例映射检查上下文构造参数

    # 返回实例关联专用规则码和标签。
    return SameLineCommentCheckContext(*tuple_context_args)

# 供 `_same_line_assignment_comment_issues` 复用的拆分 helper，专门处理检查单行赋值或实例关联是否带有同线语义注释。
def _same_line_assignment_comment_issues(
    str_line: str,
    int_line_no: int,
    same_line_comment_check_context: SameLineCommentCheckContext,
) -> list[QualityIssue]:
    """
    检查单行赋值或实例关联是否带有同线语义注释。

    :param str_line: 当前源码行。
    :param int_line_no: 当前源码行号。
    :param same_line_comment_check_context: 同线注释检查上下文。
    :return: 当前行同线注释诊断。
    """

    # str_name 是当前赋值或关联的展示名称。
    str_name = _assignment_lhs_label(str_line)  # 赋值左值或实例 formal 名称

    # str_comment 是当前行真实 // 注释正文。
    str_comment = _line_comment(str_line)  # 当前行同线注释正文

    # tuple_structured_context_args 把同线注释映射到结构化深度检查字段。
    tuple_structured_context_args = (
        str_name,  # 语义深度检查实体名
        same_line_comment_check_context.str_label,  # 语义深度检查实体类别
        same_line_comment_check_context.str_rel_path,  # 语义深度检查文件路径
        int_line_no,  # 语义深度检查源码行
        same_line_comment_check_context.str_severity,  # 空洞注释问题等级
        same_line_comment_check_context.comment_language,  # 空洞注释语言策略
    )  # 同线注释深度检查构造参数

    # structured_context 承接已有语义深度和泛化注释检查。
    structured_context = StructuredCommentContext(*tuple_structured_context_args)  # 同线注释语义上下文

    # 缺少同线注释时使用新增规则码。
    if not str_comment:

        # str_message 描述当前赋值或实例关联缺少同线注释。
        str_message = (
            f"{same_line_comment_check_context.str_label} `{str_name}` "
            "should have a same-line semantic comment."
        )  # 同线语义注释缺失诊断文本

        # 当前行不满足用户要求的同线右侧注释。
        quality_issue_missing_same_line_comment = QualityIssue(  # 赋值或实例映射缺少右侧说明
            same_line_comment_check_context.str_code,  # VG062 或 VG064 规则码
            same_line_comment_check_context.str_severity,  # 同线注释缺失严重级别
            str_message,  # 带实体名的缺注释诊断文本
            same_line_comment_check_context.str_rel_path,  # 当前 RTL 文件报告路径
            int_line_no,  # 缺少右侧说明的源码行号
            rule=same_line_comment_check_context.str_rule,  # 同线注释规则命名空间
        )

        # 返回当前行的缺失注释诊断。
        return [
            quality_issue_missing_same_line_comment,
        ]

    # 已有注释时继续复用语义深度检查。
    list_issues = _structured_comment_depth_issues(str_comment, structured_context)  # 当前同线注释深度诊断

    # 泛化占位注释同样不能满足新增同线规则。
    list_issues.extend(_generic_structured_comment_issues(str_comment, structured_context))

    # 返回当前行同线注释诊断。
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

# 供 `_is_procedural_assignment_line` 复用的拆分 helper，专门处理源码行是否是 always/function/task/generate/initial 内的赋值语句。
def _is_procedural_assignment_line(str_line: str) -> bool:
    """
    判断源码行是否是 always/function/task/generate/initial 内的赋值语句。

    :param str_line: 当前源码行。
    :return: 是过程赋值语句时返回 True。
    """

    # str_code 去掉行尾注释后用于判断过程赋值语法形态。
    str_code = _strip_line_comment(str_line).strip()  # 过程赋值候选源码

    # 空行、控制结构和声明行不属于赋值语句。
    if not str_code or not str_code.endswith(";"):

        # 非完整语句不检查同线赋值注释。
        return False

    # 当前行属于忽略类别时跳过。
    if str_code.startswith(PROCEDURAL_ASSIGNMENT_IGNORED_PREFIXES):

        # 声明和连续赋值由其他规则检查。
        return False

    # bool_has_procedural_assignment 汇总过程块中两类赋值命中结果。
    bool_has_procedural_assignment = (
        _has_nonblocking_assignment(str_code)  # <= 非阻塞形式命中
        or _has_blocking_assignment(str_code)  # = 阻塞形式命中
    )  # 过程赋值语句判定结果

    # 非阻塞赋值或阻塞赋值均需要同线注释。
    return bool_has_procedural_assignment

# 供 `_is_instance_association_line` 复用的拆分 helper，专门处理源码行是否是 `.formal(actual)` 形式的实例关联。
def _is_instance_association_line(str_line: str) -> bool:
    """
    判断源码行是否是 `.formal(actual)` 形式的实例关联。

    :param str_line: 当前源码行。
    :return: 是实例关联行时返回 True。
    """

    # str_code 去掉行尾注释后用于识别 formal 连接。
    str_code = _strip_line_comment(str_line).strip()  # 去注释后的实例行

    # 实例参数和端口关联均以点号 formal 起始。
    return re.match(r"^\.[A-Za-z_][A-Za-z0-9_]*\s*\(", str_code) is not None

# 供 `_comment_reuse_candidates_for_module` 复用的拆分 helper，专门处理收集当前 module 中需要参与重复检测的实体注释。
def _comment_reuse_candidates_for_module(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集当前 module 中需要参与重复检测的实体注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 可参与 VG066 重复检测的注释候选列表。
    """

    # list_candidates 保持源码实体顺序，后续只在后出现的注释上报告。
    list_candidates: list[CommentReuseCandidate] = []  # module 内可比较的实体注释队列

    # 结构化参数、端口、信号和 assign 注释来自 formatter AST。
    list_candidates.extend(_structured_comment_reuse_candidates(dict_module, str_rel_path))

    # always、function、task、generate、initial 和 instance 前导注释绑定具体块。
    list_candidates.extend(_leading_comment_reuse_candidates(dict_module, str_rel_path))

    # 过程赋值右侧注释需要按源码 span 扫描。
    list_candidates.extend(_procedural_assignment_reuse_candidates(dict_module, list_lines, str_rel_path))

    # 实例化端口和参数映射注释同样属于实体说明。
    list_candidates.extend(_instance_mapping_reuse_candidates(dict_module, list_lines, str_rel_path))

    # 按行号排序，让重复诊断稳定落到后出现的注释。
    list_candidates.sort(key=lambda item: item.int_line_no or 0)

    # 返回全部实体级候选。
    return list_candidates

# 供 `_structured_comment_reuse_candidates` 复用的拆分 helper，专门处理收集参数、端口、声明和 assign 的同线注释候选。
def _structured_comment_reuse_candidates(
    dict_module: dict[str, Any],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集参数、端口、声明和 assign 的同线注释候选。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 结构化实体注释候选列表。
    """

    # list_candidates 保存 formatter AST 已定位的实体注释。
    list_candidates: list[CommentReuseCandidate] = []  # 参数端口声明和 assign 候选队列

    # tuple_collections 与结构化注释覆盖规则保持一致。
    tuple_collections = (  # AST 实体集合与诊断标签映射
        ("params", "parameter"),  # module 头部参数
        ("localparams", "localparam"),  # module body 局部参数
        ("ports", "port"),  # module 头部端口
        ("decls", "signal"),  # module body 信号声明
        ("assigns", "assign"),  # 连续赋值语句
    )

    # 遍历每类 AST 实体。
    for str_collection_name, str_label in tuple_collections:

        # 当前集合中的条目按 formatter 顺序扫描。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # str_comment 是同线注释正文。
            str_comment = str(dict_item.get("comment") or "").strip()  # 当前实体注释正文

            # str_name 优先绑定实体名，assign 使用 lhs。
            str_name = str(dict_item.get("name") or dict_item.get("lhs") or "")  # 当前实体名称

            # int_line_no 定位到实体声明或 assign 行。
            int_line_no = _as_line(dict_item.get("line_start"))  # 当前实体源码行号

            # comment_reuse_candidate 由统一构造函数过滤短标签和空注释。
            comment_reuse_candidate: CommentReuseCandidate | None = _comment_reuse_candidate(  # 结构化实体 VG066 候选
                str_comment,  # AST 条目右侧注释正文
                str_label,  # 参数端口声明或 assign 类别
                str_name,  # 报告中展示的结构化实体名
                str_rel_path,  # 结构化实体所在 RTL 路径
                int_line_no,  # formatter AST 提供的实体起始行
            )

            # 空注释或过短信号标签不会进入 VG066。
            if comment_reuse_candidate is None:

                # 当前注释不具备重复检测价值。
                continue

            # 收集可检测的实体注释。
            list_candidates.append(comment_reuse_candidate)

    # 返回结构化注释候选。
    return list_candidates

# 供 `_leading_comment_reuse_candidates` 复用的拆分 helper，专门处理收集过程块和实例上方的实体说明注释。
def _leading_comment_reuse_candidates(
    dict_module: dict[str, Any],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集过程块和实例上方的实体说明注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 前导实体注释候选列表。
    """

    # list_candidates 保存 always/instance 等块级说明注释。
    list_candidates: list[CommentReuseCandidate] = []  # always 实例等前导说明队列

    # 过程块集合沿用注释覆盖规则表。
    for str_collection_name, str_label, _str_rule in BLOCK_LEADING_COMMENT_COLLECTIONS:

        # 每个过程块可能带一条或多条紧邻前导注释。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # str_name 展示过程块 header 或 span 标签。
            str_name = str(dict_block.get("header") or _span_item_label(dict_block))  # 过程块名称

            # 当前块的前导注释逐条收集。
            list_candidates.extend(
                _leading_comment_candidates_for_item(dict_block, str_label, str_name, str_rel_path)
            )

    # 实例级前导说明同样绑定具体 instance。
    for dict_inst in dict_module.get("instances", []) or []:

        # str_name 使用实例名定位注释对象。
        str_name = str(dict_inst.get("instance_name") or "")  # 实例名称

        # 收集实例功能或连接说明。
        list_candidates.extend(
            _leading_comment_candidates_for_item(dict_inst, "instance", str_name, str_rel_path)
        )

    # 返回块级实体注释候选。
    return list_candidates

# 供 `_leading_comment_candidates_for_item` 复用的拆分 helper，专门处理转换单个 AST 条目的前导注释候选。
def _leading_comment_candidates_for_item(
    dict_item: dict[str, Any],
    str_label: str,
    str_name: str,
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    转换单个 AST 条目的前导注释候选。

    :param dict_item: 带 leading_comments 的 formatter AST 条目。
    :param str_label: 注释绑定的实体类别。
    :param str_name: 注释绑定的实体名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 当前条目的前导注释候选列表。
    """

    # list_candidates 保存当前条目的前导注释候选。
    list_candidates: list[CommentReuseCandidate] = []  # 单个块或实例的前导说明队列

    # list_comments 是 formatter AST 记录的紧邻前导注释。
    list_comments = list(dict_item.get("leading_comments") or [])  # 前导注释原始文本列表

    # int_line_start 用于推断前导注释行号。
    int_line_start = _as_line(dict_item.get("line_start"))  # 目标实体起始行

    # 前导注释缺少目标行号时仍可参与文件级重复检测。
    int_first_comment_line = (int_line_start - len(list_comments)) if int_line_start is not None else None  # 第一条前导注释行号

    # 逐条转换前导注释。
    for int_index, str_raw_comment in enumerate(list_comments):

        # str_comment 去掉 // 后只保留语义正文。
        str_comment = _comment_body_from_raw(str(str_raw_comment))  # 前导注释正文

        # int_line_no 尽量定位到真实注释行。
        int_line_no = None if int_first_comment_line is None else int_first_comment_line + int_index  # 前导注释真实行号

        # 当前前导注释可能只是短导航，统一构造函数会过滤。
        comment_reuse_candidate: CommentReuseCandidate | None = _comment_reuse_candidate(  # 前导块说明 VG066 候选
            str_comment,  # 去掉注释符后的前导说明
            str_label,  # always/initial/instance 等块类别
            str_name,  # 前导注释绑定的块或实例名
            str_rel_path,  # 前导说明所在 RTL 路径
            int_line_no,  # 推算得到的前导注释行号
        )

        # 不具备检测价值的前导注释跳过。
        if comment_reuse_candidate is None:

            # 短标签或空文本不参与重复检测。
            continue

        # 收集当前前导注释。
        list_candidates.append(comment_reuse_candidate)

    # 返回当前条目的候选列表。
    return list_candidates

# 供 `_procedural_assignment_reuse_candidates` 复用的拆分 helper，专门处理收集 always/function/task 等过程赋值右侧注释。
def _procedural_assignment_reuse_candidates(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集 always/function/task 等过程赋值右侧注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 过程赋值注释候选列表。
    """

    # list_candidates 保存过程赋值注释候选。
    list_candidates: list[CommentReuseCandidate] = []  # 过程赋值右侧说明集合

    # 遍历所有过程块集合。
    for str_collection_name in PROCEDURAL_ASSIGNMENT_COLLECTIONS:

        # 当前集合中的块按 AST 顺序扫描。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # 当前块内候选由 span 扫描 helper 处理。
            list_candidates.extend(_comment_reuse_candidates_for_assignment_block(dict_block, list_lines, str_rel_path))

    # 返回过程赋值注释候选。
    return list_candidates

# 供 `_comment_reuse_candidates_for_assignment_block` 复用的拆分 helper，专门处理收集单个过程块 span 内的赋值注释候选。
def _comment_reuse_candidates_for_assignment_block(
    dict_block: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集单个过程块 span 内的赋值注释候选。

    :param dict_block: formatter AST 中的单个过程块。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 单个过程块内的注释候选列表。
    """

    # list_candidates 保存当前过程块中的赋值注释。
    list_candidates: list[CommentReuseCandidate] = []  # 当前过程块赋值说明队列

    # int_line_start 是过程块起始行。
    int_line_start = _as_line(dict_block.get("line_start"))  # 过程块起始行号

    # int_line_end 是过程块结束行。
    int_line_end = _as_line(dict_block.get("line_end"))  # 过程块结束行号

    # 缺少 span 时不进行文本扫描。
    if int_line_start is None or int_line_end is None:

        # VG050 会报告缺失 span，本规则跳过。
        return list_candidates

    # int_last_line 防止 AST span 越过文件尾。
    int_last_line = min(int_line_end, len(list_lines))  # 实际扫描结束行

    # 逐行扫描过程块内部赋值。
    for int_line_no in range(int_line_start, int_last_line + 1):

        # str_line 是当前过程块行。
        str_line = list_lines[int_line_no - 1]  # 当前源码行

        # 只收集真正的过程赋值语句。
        if not _is_procedural_assignment_line(str_line):

            # 非赋值语句继续扫描。
            continue

        # 当前赋值行同线注释转换为候选。
        comment_reuse_candidate: CommentReuseCandidate | None = _line_comment_reuse_candidate(  # 过程赋值右侧说明候选
            str_line,  # 当前过程块内的赋值源码行
            int_line_no,  # 过程赋值语句的一基行号
            "process assignment",  # VG066 报告中的过程赋值类别
            str_rel_path,  # 过程赋值所在 RTL 路径
        )

        # 没有有效注释时跳过。
        if comment_reuse_candidate is None:

            # 缺注释由 VG062 负责。
            continue

        # 收集过程赋值注释候选。
        list_candidates.append(comment_reuse_candidate)

    # 返回当前块候选。
    return list_candidates

# 供 `_instance_mapping_reuse_candidates` 复用的拆分 helper，专门处理收集实例参数和端口关联右侧注释。
def _instance_mapping_reuse_candidates(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集实例参数和端口关联右侧注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 实例映射注释候选列表。
    """

    # list_candidates 保存实例映射注释候选。
    list_candidates: list[CommentReuseCandidate] = []  # 实例 formal 连接说明队列

    # 遍历所有子模块实例。
    for dict_inst in dict_module.get("instances", []) or []:

        # int_line_start 是实例起始行。
        int_line_start = _as_line(dict_inst.get("line_start"))  # 实例起始行号

        # int_line_end 是实例结束行。
        int_line_end = _as_line(dict_inst.get("line_end"))  # 实例结束行号

        # 无实例 span 时无法定位端口映射行。
        if int_line_start is None or int_line_end is None:

            # VG050 会覆盖缺失 span 情况。
            continue

        # int_last_line 防止越过源码行数。
        int_last_line = min(int_line_end, len(list_lines))  # 实例扫描结束行

        # 扫描实例连接列表。
        for int_line_no in range(int_line_start, int_last_line + 1):

            # str_line 是当前实例行。
            str_line = list_lines[int_line_no - 1]  # 当前实例源码行

            # 非 .formal(actual) 关联不属于本候选集合。
            if not _is_instance_association_line(str_line):

                # 继续扫描实例下一行。
                continue

            # 实例映射行同线注释转换为候选。
            comment_reuse_candidate: CommentReuseCandidate | None = _line_comment_reuse_candidate(  # 实例 formal 说明候选
                str_line,  # 当前实例参数或端口映射行
                int_line_no,  # 实例映射语句的一基行号
                "instance mapping",  # VG066 报告中的实例映射类别
                str_rel_path,  # 实例映射所在 RTL 路径
            )

            # 缺少同线注释时由 VG064 负责。
            if comment_reuse_candidate is None:

                # 当前行没有可复用注释文本。
                continue

            # 收集实例映射候选。
            list_candidates.append(comment_reuse_candidate)

    # 返回实例映射候选。
    return list_candidates

# 供 `_line_comment_reuse_candidate` 复用的拆分 helper，专门处理从一行 RTL 中提取实体注释候选。
def _line_comment_reuse_candidate(
    str_line: str,
    int_line_no: int,
    str_label: str,
    str_rel_path: str,
) -> CommentReuseCandidate | None:
    """
    从一行 RTL 中提取实体注释候选。

    :param str_line: 当前源码行。
    :param int_line_no: 当前源码行号。
    :param str_label: 注释绑定的实体类别。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 可检测候选；没有有效注释时返回 None。
    """

    # str_comment 是当前行真实 // 后的正文。
    str_comment = _line_comment(str_line)  # 当前实体右侧说明正文

    # str_name 展示赋值左值或实例 formal 名。
    str_name = _assignment_lhs_label(str_line)  # 当前行绑定的实体名称

    # 构造候选并执行统一过滤。
    return _comment_reuse_candidate(str_comment, str_label, str_name, str_rel_path, int_line_no)

# 供 `_comment_reuse_candidate` 复用的拆分 helper，专门处理构造可参与 VG066 的注释候选。
def _comment_reuse_candidate(
    str_comment: str,
    str_label: str,
    str_name: str,
    str_rel_path: str,
    int_line_no: int | None,
) -> CommentReuseCandidate | None:
    """
    构造可参与 VG066 的注释候选。

    :param str_comment: 注释正文。
    :param str_label: 注释绑定的实体类别。
    :param str_name: 注释绑定的实体名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param int_line_no: 注释行号。
    :return: 可检测候选；空注释或过短中文返回 None。
    """

    # str_body 去掉可能残留的 // 标记。
    str_body = _comment_body_from_raw(str_comment)  # 规范化前的注释正文

    # 空注释不能参与重复检测。
    if not str_body:

        # 覆盖类缺注释规则会单独报告。
        return None

    # int_cjk_chars 控制短标签不进入相似度比较。
    int_cjk_chars = len(CJK_PATTERN.findall(str_body))  # 注释中的中文字符数量

    # 中文字符太少时可能只是协议短标签或纯英文工具词。
    if int_cjk_chars < COMMENT_REUSE_MIN_CJK_CHARS:

        # 短注释交给语义深度规则判断。
        return None

    # str_normalized 是精确重复检测键。
    str_normalized = _normalized_comment_reuse_text(str_body)  # 去噪后的精确复用键

    # 规范化后太短的文本不稳定。
    if len(str_normalized) < COMMENT_REUSE_MIN_CJK_CHARS:

        # 过短键不参与重复检测。
        return None

    # str_similarity_key 进一步去掉标识符噪声。
    str_similarity_key = _comment_similarity_key(str_body)  # 近似复用比较键

    # 返回不可变候选对象。
    comment_reuse_candidate = CommentReuseCandidate(  # 完成归一化后的 VG066 候选对象
        str_comment=str_body,  # 展示给诊断的人读注释正文
        str_normalized=str_normalized,  # 精确重复检测使用的去噪键
        str_similarity_key=str_similarity_key,  # 近似复用检测使用的低噪声键
        str_label=str_label, str_name=str_name,  # 候选绑定的 RTL 实体类别和名称
        str_rel_path=str_rel_path, int_line_no=int_line_no,  # 候选所在路径和 VG066 落点行号
    )

    # 返回经过长度和语义噪声过滤的候选。
    return comment_reuse_candidate

# 供 `_comment_reuse_issues` 复用的拆分 helper，专门处理实体注释重复或近似复用转换为 VG066 诊断。
def _comment_reuse_issues(
    list_candidates: list[CommentReuseCandidate],
    str_severity: str,
) -> list[QualityIssue]:
    """
    把实体注释重复或近似复用转换为 VG066 诊断。

    :param list_candidates: 已按源码顺序排序的实体注释候选。
    :param str_severity: strict 派生的注释问题级别。
    :return: VG066 诊断列表。
    """

    # list_issues 保存后出现候选上的重复诊断。
    list_issues: list[QualityIssue] = []  # 后出现实体上的 VG066 诊断集合

    # dict_seen_by_key 记录每个规范化文本第一次出现的实体。
    dict_seen_by_key: dict[str, CommentReuseCandidate] = {}  # 规范化注释到首次实体的索引

    # set_reported_lines 避免同一注释同时报 exact 和 near duplicate。
    set_reported_lines: set[int | None] = set()  # 已经落点到 VG066 的源码行

    # 先做精确重复和数字噪声重复检测。
    for comment_reuse_candidate in list_candidates:

        # 第一次出现的规范化文本只登记不报错。
        if comment_reuse_candidate.str_normalized not in dict_seen_by_key:

            # 保存首个候选，后续复用指向它。
            dict_seen_by_key[comment_reuse_candidate.str_normalized] = comment_reuse_candidate  # 首次出现实体缓存

            # 继续检查下一个候选。
            continue

        # str_reuse_key 指向当前候选的精确重复键。
        str_reuse_key = comment_reuse_candidate.str_normalized  # 当前候选规范化文本

        # comment_reuse_candidate_previous 是相同注释键的首次出现位置。
        comment_reuse_candidate_previous = dict_seen_by_key[str_reuse_key]  # 精确重复首次实体候选

        # exact 重复登记到当前候选行。
        list_issues.append(
            _comment_reuse_issue(
                comment_reuse_candidate,
                comment_reuse_candidate_previous,
                str_severity,
            )
        )

        # 记录当前行，避免后续近似比较重复报告。
        set_reported_lines.add(comment_reuse_candidate.int_line_no)

    # 近似检测只在未被 exact 覆盖的后续候选上执行。
    for int_index, comment_reuse_candidate in enumerate(list_candidates):

        # 已报告 exact 的注释不重复报告 near。
        if comment_reuse_candidate.int_line_no in set_reported_lines:

            # 当前行已命中 VG066。
            continue

        # 与所有更早候选比较。
        for previous_comment_reuse_candidate in list_candidates[:int_index]:

            # 当前候选若近似复用更早注释则报告一次即可。
            if _comments_are_near_duplicate(comment_reuse_candidate, previous_comment_reuse_candidate):

                # 近似复用登记为 VG066。
                list_issues.append(
                    _comment_reuse_issue(
                        comment_reuse_candidate,
                        previous_comment_reuse_candidate,
                        str_severity,
                    )
                )

                # 当前行已报告，跳出更早候选循环。
                set_reported_lines.add(comment_reuse_candidate.int_line_no)

                # 当前候选已登记近似复用诊断，停止比较更早候选。
                break

    # 返回全部重复注释诊断。
    return list_issues

# 供 `_comments_are_near_duplicate` 复用的拆分 helper，专门处理两条注释是否属于近似复用。
def _comments_are_near_duplicate(
    candidate: CommentReuseCandidate,
    previous_candidate: CommentReuseCandidate,
) -> bool:
    """
    判断两条注释是否属于近似复用。

    :param candidate: 后出现的候选注释。
    :param previous_candidate: 更早出现的候选注释。
    :return: 两条注释高度相似时返回 True。
    """

    # 两条注释的低噪声比较键都必须足够长。
    if len(candidate.str_similarity_key) < COMMENT_REUSE_MIN_CJK_CHARS:

        # 当前候选过短，不做近似判定。
        return False

    # 更早候选过短也跳过。
    if len(previous_candidate.str_similarity_key) < COMMENT_REUSE_MIN_CJK_CHARS:

        # 避免短标签误报。
        return False

    # 完全相同已由 exact 阶段报告。
    if candidate.str_normalized == previous_candidate.str_normalized:

        # exact 阶段负责该情况。
        return False

    # 同一个 matcher 依次执行两个安全上界和最终精确比率，避免重复建索引。
    sequence_matcher_obj_matcher = difflib.SequenceMatcher(  # 当前候选对的标准库序列比较器
        None,  # 使用默认元素比较函数
        candidate.str_similarity_key,  # 后出现候选的低噪声文本
        previous_candidate.str_similarity_key,  # 更早候选的低噪声文本
    )

    # 长度比率是精确相似度的宽松上界，低于阈值时可安全拒绝。
    float_length_upper_bound = sequence_matcher_obj_matcher.real_quick_ratio()  # 当前候选对的长度比率上界

    # 上界不足时精确 ratio 必然不足，保持既有阈值判断语义。
    if float_length_upper_bound < COMMENT_REUSE_SIMILARITY_THRESHOLD:

        # 当前长度差异已证明候选不可能达到近似重复阈值。
        return False

    # 字符频率比率仍是精确相似度上界，但比长度上界更紧。
    float_frequency_upper_bound = sequence_matcher_obj_matcher.quick_ratio()  # 当前候选对的字符频率比率上界

    # 字符组成差异足够大时跳过昂贵匹配块计算。
    if float_frequency_upper_bound < COMMENT_REUSE_SIMILARITY_THRESHOLD:

        # 上界严格低于阈值，提前返回不会漏报近似重复。
        return False

    # 仅对两个安全上界均达到阈值的候选计算精确序列相似度。
    float_ratio = sequence_matcher_obj_matcher.ratio()  # 当前候选对的精确序列相似度

    # 达到阈值时视作近似复用。
    return float_ratio >= COMMENT_REUSE_SIMILARITY_THRESHOLD

# 供 `_comment_reuse_issue` 复用的拆分 helper，专门处理构造注释重复或近似复用诊断。
def _comment_reuse_issue(
    candidate: CommentReuseCandidate,
    previous_candidate: CommentReuseCandidate,
    str_severity: str,
) -> QualityIssue:
    """
    构造注释重复或近似复用诊断。

    :param candidate: 后出现并触发诊断的注释候选。
    :param previous_candidate: 被复用的更早候选。
    :param str_severity: 注释问题级别。
    :return: VG066 诊断。
    """

    # str_message 展示当前实体和首次出现实体，便于人工重写注释。
    str_message = (  # VG066 诊断文本
        f"Comment for {candidate.str_label} `{candidate.str_name}` repeats or closely reuses "
        f"comment from {previous_candidate.str_label} `{previous_candidate.str_name}`; "
        "write entity-specific RTL intent."
    )

    # 返回新增 VG066 诊断。
    return QualityIssue(
        "VG066",
        str_severity,
        str_message,
        candidate.str_rel_path,
        candidate.int_line_no,
        rule="comments.repeated_semantic",
    )

# 供 `_comment_body_from_raw` 复用的拆分 helper，专门处理去掉 // 标记后的注释正文。
def _comment_body_from_raw(str_comment: str) -> str:
    """
    返回去掉 // 标记后的注释正文。

    :param str_comment: 原始注释文本。
    :return: 只保留语义正文的注释文本。
    """

    # str_body 先去除外围空白。
    str_body = str_comment.strip()  # 待剥离注释标记的原始正文

    # 前导注释可能保留了 // 标记。
    if str_body.startswith("//"):

        # 去掉 Verilog 单行注释符。
        str_body = str_body[2:].strip()  # 去注释符后的正文

    # 返回最终正文。
    return str_body

# 供 `_normalized_comment_reuse_text` 复用的拆分 helper，专门处理生成忽略编号、空白和标点的注释重复检测键。
def _normalized_comment_reuse_text(str_comment: str) -> str:
    """
    生成忽略编号、空白和标点的注释重复检测键。

    :param str_comment: 原始注释正文。
    :return: 去噪后的重复检测文本。
    """

    # str_normalized 先执行 Unicode 兼容归一化。
    str_normalized = unicodedata.normalize("NFKC", str_comment)  # Unicode 归一化文本

    # 去掉零宽字符，防止不可见字符绕过重复检查。
    str_normalized = re.sub(r"[\u200b-\u200f\ufeff]", "", str_normalized)  # 去零宽字符文本

    # 数字编号不应让模板复用通过。
    str_normalized = re.sub(r"\d+", "", str_normalized)  # 去数字编号文本

    # 常见标点和空白都不参与重复比较。
    str_normalized = re.sub(r"[\s,，.。;；:：、/\\|()[\]{}<>《》\"'`~!！?？+=*_#-]+", "", str_normalized)  # 去标点空白文本

    # 小写化让 ASCII 标识符大小写差异不能绕过。
    return str_normalized.lower()

# 供 `_comment_similarity_key` 复用的拆分 helper，专门处理生成近似重复检测使用的低噪声文本。
def _comment_similarity_key(str_comment: str) -> str:
    """
    生成近似重复检测使用的低噪声文本。

    :param str_comment: 原始注释正文。
    :return: 去掉 ASCII 标识符后的相似度比较文本。
    """

    # str_key 先复用精确重复归一化。
    str_key = _normalized_comment_reuse_text(str_comment)  # 归一化注释文本

    # ASCII 标识符通常是信号名噪声，不应让同模板句逃逸。
    str_key = re.sub(r"[a-z_][a-z0-9_]*", "", str_key)  # 去 ASCII 标识符文本

    # 返回近似比较键。
    return str_key

# 供 `_comment_density_issues` 复用的拆分 helper，专门处理按代码行比例检查注释覆盖密度。
def _comment_density_issues(str_text: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    按代码行比例检查注释覆盖密度。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 注释密度相关诊断列表。
    """

    # list_issues 保存注释密度诊断。
    list_issues: list[QualityIssue] = []  # 注释密度诊断

    # list_code_lines 只统计非空且非纯注释的 RTL 行。
    list_code_lines = [
        str_line  # 注释覆盖率分母中的 RTL 代码行
        for str_line in str_text.splitlines()  # 遍历文件全部文本行
        if _is_code_line(str_line)  # 排除空行和纯注释行
    ]  # 参与注释密度计算的 RTL 代码行

    # list_commented_code_lines 统计参与覆盖率分子的注释代码行。
    list_commented_code_lines = [
        str_line  # 覆盖率分子中带行注释的 RTL 代码行
        for str_line in list_code_lines  # 遍历已确认的 RTL 代码行
        if _line_comment(str_line)  # 只保留带真实行注释的代码行
    ]  # 注释密度分子使用的带行注释代码行

    # 没有代码行时不计算密度。
    if not list_code_lines:

        # 空文件或纯注释文件由其他规则处理。
        return list_issues

    # float_density 是带注释代码行占比。
    float_density = len(list_commented_code_lines) / len(list_code_lines)  # 代码行注释覆盖率

    # strict 模式下覆盖率低于 20% 为 error。
    if strict and float_density < 0.20:

        # 生成 RTL 需要足够语义注释支撑审查。
        list_issues.append(
            QualityIssue(
                "VG042",
                "error",
                f"Comment coverage is too low for generated RTL ({float_density:.2%}); "
                "add semantic comments near declarations, assigns, always blocks, FSM, and instances.",
                str_rel_path,
                rule="comments.coverage",
            )
        )

    # 非 strict 或轻微不足时保留 warning。
    elif float_density < 0.15:

        # 低注释覆盖率仍需提示维护风险。
        list_issues.append(
            QualityIssue(
                "VG042",
                "warning",
                f"Comment coverage is low ({float_density:.2%}).",
                str_rel_path,
                rule="comments.coverage",
            )
        )

    # 返回注释密度诊断。
    return list_issues

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
    _comment_rules
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
