"""提供 validation 静态 gate、语义检查与注释检查辅助。"""

# future annotations 避免运行期解析复杂类型提示。
from __future__ import annotations

# re 负责 RTL/testbench 的轻量语法模式扫描。
import re

# Path 负责 artifact 根目录和相对路径计算。
from pathlib import Path

# Any 兼容 JSON-like 配置和第三方 gate 返回对象。
from typing import Any

# comment_placement 提供注释位置专项检查。
from scripts.python.quality.comment_placement import validate_comment_placement

# deliverable_gate 提供最终生成物交付门禁。
from scripts.python.quality.deliverable_gate import run_verilog_deliverable_gate

# quality_gate 提供 formatter AST 结构化检查。
from scripts.python.quality.quality_gate import run_verilog_quality_gate

# semantic_contract 提供 transcript 解析和语义比较。
from scripts.python.existing_rtl.semantic_contract import (
    SEMANTIC_RESULT_TAG,
    compare_reference_to_transcript,
    parse_semantic_transcript,
)

# vectors 读取 reference/vector 合同和 RTL hash 标记。
from scripts.python.workflow.vectors import extract_vector_hashes, find_vector_contracts

# ValidationIssue 是 validation helper 的统一诊断模型。
from .validation_models import ValidationIssue

# validation_reports 提供 artifact 清点与报告整形辅助。
from .validation_reports import _is_testbench, _iter_existing_tree_paths
from .validation_reports import _path_is_existing_file, _rtl_files
from .validation_reports import _rtl_source_files, _unexpected_artifact_issues
from .validation_reports import _validate_declared_artifact_tree, _validate_expected_outputs

# PLACEHOLDER_PATTERNS 描述不能留在最终 RTL 中的占位痕迹。
PLACEHOLDER_PATTERNS = ("TODO", "FIXME", "PLACEHOLDER", "<fill", "<todo")  # RTL 产物中禁止残留的占位文本

# 把源诊断列表追加到目标诊断列表。
def _extend_issues(list_target: list[ValidationIssue], list_source: list[ValidationIssue]) -> None:
    """
    把源诊断列表追加到目标诊断列表。

    :param list_target: 汇总中的 validation issue 列表，会被就地扩展。
    :param list_source: 单个 gate 产出的 validation issue 列表。
    :return: 无返回值，直接更新目标列表。
    """

    # 没有诊断时保持目标列表不变。
    if not list_source:

        # 空输入不需要修改目标列表。
        return

    # extend 保持 gate 输出的原始顺序。
    list_target.extend(list_source)

# 合并生成目录、RTL 存在性和静态 lint 的基础诊断。
def _append_core_generation_gate_results(
    list_issues: list[ValidationIssue],
    normalized_spec: dict[str, Any],
    root: Path,
) -> None:
    """
    合并生成目录、RTL 存在性和静态 lint 的基础诊断。

    :param list_issues: validation 主报告正在累计的诊断列表。
    :param normalized_spec: 已归一化的 Verilog 规格合同。
    :param root: 生成 artifact 根目录。
    :return: 本函数原地追加基础 gate 诊断，不返回业务值。
    """

    # 开发产物 gate 防止把工作区杂项带进交付目录。
    _extend_issues(list_issues, _unexpected_artifact_issues(root))

    # outputs gate 校验 spec 声明的文件都真实落盘。
    _extend_issues(list_issues, _validate_expected_outputs(normalized_spec, root))

    # artifact tree gate 阻断未在 spec 中登记的旁路产物。
    _extend_issues(list_issues, _validate_declared_artifact_tree(normalized_spec, root))

    # vector contract gate 保护 reference/eval 的可追踪哈希。
    _extend_issues(list_issues, _validate_vector_contracts(root))

    # RTL gate 确认存在 Verilog 文件和顶层 module。
    _extend_issues(list_issues, _validate_rtl(normalized_spec, root))

# 返回注释位置 gate 的诊断和 metrics。
def _validate_comment_placement_gate(root: Path, comment_language: str) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """
    返回注释位置 gate 的诊断和 metrics。

    :param root: 待扫描的生成 artifact 根目录。
    :param comment_language: 注释语言策略，用于判断中文注释位置规则。
    :return: 注释位置诊断列表和原始 gate 度量。
    """

    # tuple_gate 保存 comment_placement 模块的原始输出。
    tuple_gate = validate_comment_placement(root, comment_language)  # 注释位置原始 gate 输出

    # comment_placement 原始字典输出在此收敛为 validation facade 的统一诊断对象。
    list_issues: list[ValidationIssue] = []  # 注释位置诊断列表

    # 原始 issue 转换为 ValidationIssue。
    for dict_item in tuple_gate[0]:

        # 当前注释位置问题进入 validation report。
        list_issues.append(
            ValidationIssue(
                str(dict_item.get("severity", "error")),
                str(dict_item.get("message", "Comment placement issue.")),
                dict_item.get("path"),
                str(dict_item.get("stage", "static")),
                str(dict_item.get("source", "current_module_issue")),
                detail=dict_item.get("detail"),
            )
        )

    # 返回转换后的诊断和原始 metrics。
    return list_issues, tuple_gate[1]

# 在 diagnostic 模式下降级注释类诊断。
def _downgrade_comment_diagnostics(list_issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """
    在 diagnostic 模式下降级注释类诊断。

    :param list_issues: 原始 formatter 或注释 gate 诊断。
    :return: 注释类 error 改为 warning 后的新诊断列表。
    """

    # list_downgraded 保存降级后的诊断。
    list_downgraded: list[ValidationIssue] = []  # diagnostic 模式诊断集合

    # 逐条判断是否属于注释类问题。
    for issue in list_issues:

        # 非 error 不需要降级。
        if issue.severity != "error":

            # 保持原始诊断。
            list_downgraded.append(issue)

            # 原诊断已写入结果集，继续处理下一条。
            continue

        # str_issue_text 用于识别注释类诊断。
        str_issue_text = f"{issue.message} {issue.detail or ''}".lower()  # 注释诊断匹配文本

        # 只降级注释和 comment 相关诊断。
        if "comment" in str_issue_text or "注释" in str_issue_text:

            # 降级后的 warning 保留原始定位字段，便于调用方仍能回溯注释问题来源。
            validation_issue_obj_downgraded_issue: ValidationIssue = ValidationIssue(  # 注释类阻断降级后的诊断对象
                severity="warning",  # 注释问题在 diagnostic 模式下改为警告
                message=issue.message,  # 保留原 formatter 或注释 gate 消息
                path=issue.path,  # 保留触发注释问题的 artifact 路径
                stage=issue.stage,  # 保留原始验证阶段
                source=issue.source,  # 保留 workflow 诊断使用的问题来源
                case_id=issue.case_id,  # 保留 reference case 绑定
                tool=issue.tool,  # 保留外部工具或后端名称
                detail=issue.detail,  # 保留规则编号或补充细节
            )

            # 降级诊断追加后继续处理下一个 issue。
            list_downgraded.append(validation_issue_obj_downgraded_issue)

            # 当前注释类问题已降级，继续处理下一条。
            continue

        # 非注释类 error 保持阻断。
        list_downgraded.append(issue)

    # 返回降级后的诊断集合。
    return list_downgraded

# 返回 formatter AST quality gate 的诊断和 metrics。
def _validate_formatter_ast_quality_gate(
    root: Path,
    comment_language: str,
    spec: dict[str, Any] | None = None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """
    返回 formatter AST quality gate 的诊断和 metrics。

    :param root: 生成 artifact 根目录。
    :param comment_language: 注释语言策略，传递给 formatter AST 检查器。
    :param spec: 已归一化的 Verilog 规格合同，供语义质量门复用。
    :return: formatter AST 诊断列表和结构化质量度量。
    """

    # quality_report 是 formatter 后端生成的结构化质量报告。
    quality_report = run_verilog_quality_gate(  # formatter AST 质量报告
        root,  # formatter AST 检查的 artifact 根目录
        strict=True,  # formatter AST 质量门严格阻断问题
        comment_language=comment_language,  # formatter AST 使用的注释语言策略
        spec=spec,  # formatter AST 复用的归一化规格合同
    )

    # list_issues 保存 formatter 后端问题转换后的 validation 诊断。
    list_issues: list[ValidationIssue] = []  # 格式化抽象语法树诊断集合

    # formatter issue 转换为 validation issue。
    for formatter_issue in quality_report.issues:

        # str_severity 保持 formatter 的 error/warning 语义。
        str_severity = getattr(formatter_issue, "severity", "error")  # formatter 诊断严重级别

        # str_message 保留 formatter 可读消息。
        str_message = getattr(formatter_issue, "message", "Formatter AST quality issue.")  # formatter 诊断消息

        # str_path 保存问题路径。
        str_path = getattr(formatter_issue, "path", None)  # formatter 诊断路径

        # str_detail 保存规则或 code，便于 diagnostic 降级。
        str_detail = getattr(formatter_issue, "code", None) or getattr(formatter_issue, "rule", None)  # formatter 规则标识

        # 追加转换后的 formatter 诊断。
        list_issues.append(
            ValidationIssue(
                str(str_severity),
                str(str_message),
                str_path,
                "static",
                "current_module_issue",
                detail=str(str_detail) if str_detail else None,
            )
        )

    # 结构化 report 优先保留完整 summary，方便 workflow trace 展示规则命中。
    if hasattr(quality_report, "to_dict"):

        # to_dict 分支保留 formatter 后端产出的原始统计字段。
        dict_metrics = quality_report.to_dict()  # 格式化抽象语法树完整度量

    # 兼容旧质量报告对象没有 to_dict 的情况。
    else:

        # 旧 report 对象只暴露 ok 时，至少保留通过状态。
        dict_metrics = {"ok": quality_report.ok()}  # 格式化抽象语法树兼容度量

    # 返回诊断和 metrics。
    return list_issues, dict_metrics

# 把 formatter AST quality gate 结果合并进 validation 报告。
def _append_formatter_ast_quality_result(
    list_issues: list[ValidationIssue],
    dict_metrics: dict[str, Any],

    # root 和 comment_language 定位本轮 formatter 检查输入。
    root: Path,
    comment_language: str,

    # strict_generated_comments 和 spec 控制报告合并合同。
    *,
    strict_generated_comments: bool,
    spec: dict[str, Any] | None = None,
) -> None:
    """
    把 formatter AST quality gate 结果合并进 validation 报告。

    :param list_issues: validation 主报告正在累计的诊断列表。
    :param dict_metrics: validation 主报告正在累计的 metrics 字典。
    :param root: 生成 artifact 根目录。
    :param comment_language: 注释语言策略。
    :param spec: 已归一化的 Verilog 规格合同，供 formatter-AST 质量门复用。
    :param strict_generated_comments: 是否按新生成 RTL 严格阻断注释。
    :return: 本函数原地合并 formatter 诊断和 metrics，不返回业务值。
    """

    # formatter 后端产出 VG 诊断和 AST 解析统计。
    if spec is None:

        # 旧的私有调用仍使用无规格的静态质量门。
        tuple_quality = _validate_formatter_ast_quality_gate(root, comment_language)  # 无规格兼容质量门结果

    # spec 已提供时沿用同一份归一化合同。
    else:

        # validation 主流程把归一化规格继续传给 formatter-AST 质量门。
        tuple_quality = _validate_formatter_ast_quality_gate(root, comment_language, spec)  # 带规格质量门结果

    # list_quality_issues 在 diagnostic 模式下可降级注释类问题。
    list_quality_issues = tuple_quality[0]  # formatter AST 发现的 RTL 质量诊断

    # dict_quality_metrics 写入 metrics，供 workflow trace 使用。
    dict_quality_metrics = tuple_quality[1]  # workflow trace 中的 formatter AST 度量

    # existing RTL 或 diagnostic 模式下，注释类 formatter 问题不阻断。
    if not strict_generated_comments:

        # 降级 formatter 中的注释诊断。
        list_quality_issues = _downgrade_comment_diagnostics(list_quality_issues)  # 降级后的 formatter 诊断

    # formatter 诊断先进入统一 issue 流，供最终报告计算 ok 状态。
    _extend_issues(list_issues, list_quality_issues)

    # formatter_ast_quality_gate 保存 formatter AST 后端的结构化统计和规则命中。
    dict_metrics["formatter_ast_quality_gate"] = dict_quality_metrics  # 格式化抽象语法树检查证据

# 把最终交付门禁结果合并进 validation 报告。
def _append_deliverable_gate_result(
    list_issues: list[ValidationIssue],
    dict_metrics: dict[str, Any],

    # 以下参数定义本次 VG 门禁的设计输入和审查策略。
    spec: dict[str, Any],
    root: Path,
    comment_language: str,
    *,
    strict_generated_comments: bool,
) -> None:
    """
    把最终交付门禁结果合并进 validation 报告。
    
    :param list_issues: validation 主报告正在累计的诊断列表。
    :param dict_metrics: validation 主报告正在累计的 metrics 字典。
    :param spec: 已归一化的 Verilog 规格合同。
    :param root: 生成 artifact 根目录。
    :param comment_language: 注释语言策略。
    :param strict_generated_comments: 是否按新生成 RTL 严格阻断注释和 warning。
    :return: 本函数原地合并诊断和 metrics，不返回业务值。
    """

    # 最终交付门禁返回 strict warning 提升后的 validation 诊断。
    tuple_deliverable = _validate_verilog_generated_deliverable_gate(  # 最终交付门禁诊断和摘要
        root,  # 生成 artifact 根目录
        comment_language,  # 交付门禁采用的注释语言
        spec=spec,  # VG 门禁复用 validation 的归一化规格
        strict_generated_comments=strict_generated_comments,  # 新生成 RTL 严格模式
    )

    # strict warning 在生成物模式下会被提升为阻断问题。
    _extend_issues(list_issues, tuple_deliverable[0])

    # verilog_generated_deliverable_gate 记录最终交付门禁证据。
    dict_metrics["verilog_generated_deliverable_gate"] = tuple_deliverable[1]  # 最终交付门禁摘要

# 把注释覆盖和注释位置门禁结果合并进 validation 报告。
def _append_comment_gate_results(
    list_issues: list[ValidationIssue],
    dict_metrics: dict[str, Any],
    root: Path,
    comment_language: str,
    *,
    strict_generated_comments: bool,
) -> None:
    """
    把注释覆盖和注释位置门禁结果合并进 validation 报告。

    :param list_issues: validation 主报告正在累计的诊断列表。
    :param dict_metrics: validation 主报告正在累计的 metrics 字典。
    :param root: 生成 artifact 根目录。
    :param comment_language: 注释语言策略。
    :param strict_generated_comments: 是否按新生成 RTL 严格阻断注释问题。
    :return: 本函数原地更新诊断与 metrics，不返回业务值。
    """

    # tuple_line_comment 保存关键 Verilog 行的注释覆盖扫描结果。
    tuple_line_comment = _validate_line_comment_gate(root, comment_language)  # 行注释覆盖 gate 原始结果

    # tuple_comment_placement 保存注释相邻行和路径位置扫描结果。
    tuple_comment_placement = _validate_comment_placement_gate(root, comment_language)  # 注释位置 gate 原始结果

    # list_line_issues 是 strict/diagnostic 降级前的行注释诊断。
    list_line_issues = tuple_line_comment[0]  # 行注释覆盖诊断集合

    # list_placement_issues 是 strict/diagnostic 降级前的位置诊断。
    list_placement_issues = tuple_comment_placement[0]  # 注释位置诊断集合

    # existing RTL 模式下保留提示价值，但不阻断功能验证。
    if not strict_generated_comments:

        # 降级行注释覆盖诊断。
        list_line_issues = _downgrade_comment_diagnostics(list_line_issues)  # diagnostic 模式行注释提示

        # 降级注释位置诊断。
        list_placement_issues = _downgrade_comment_diagnostics(list_placement_issues)  # diagnostic 模式位置提示

    # 降级后的两类诊断统一进入 validation issue 流。
    _extend_issues(list_issues, list_line_issues)

    # 注释位置诊断独立合入，避免覆盖率问题掩盖相邻行问题。
    _extend_issues(list_issues, list_placement_issues)

    # line_comment_gate 保存关键生成行的覆盖证据。
    dict_metrics["line_comment_gate"] = tuple_line_comment[1]  # 关键 Verilog 行注释覆盖统计

    # comment_placement_gate 保存注释所在位置的审计证据。
    dict_metrics["comment_placement_gate"] = tuple_comment_placement[1]  # 注释位置规则扫描统计

# 返回最终交付门禁的阻断诊断和摘要 metrics。
def _validate_verilog_generated_deliverable_gate(
    root: Path,
    comment_language: str,
    *,
    spec: dict[str, Any],
    strict_generated_comments: bool,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """
    返回最终交付门禁的阻断诊断和摘要 metrics。
    
    :param root: 生成 artifact 根目录。
    :param comment_language: 注释语言策略。
    :param spec: 已归一化的 Verilog 规格合同。
    :param strict_generated_comments: 是否按新生成 RTL 严格阻断注释和 warning。
    :return: 交付门禁诊断列表和摘要 metrics。
    """

    # dict_report 是最终交付门禁的完整报告。
    dict_report = run_verilog_deliverable_gate(  # 最终交付门禁完整报告
        root,  # 待审计的生成产物根路径
        spec=spec,  # 固定 VG 门禁依赖的规格合同
        strict=True,  # validation 内部始终按交付级严格模式执行
        comment_language=comment_language,  # comment gate 与 VG 注释规则共享的语言策略
    )  # 交付门禁报告

    # dict_metrics 只保留摘要，避免 validation report 重复嵌入完整 AST。
    dict_metrics = {
        "delivery_ready": dict_report.get("delivery_ready"),  # 交付门禁最终布尔状态
        "repair_required": dict_report.get("repair_required"),  # 不可交付时是否必须修复
        "rerun_required_after_repair": dict_report.get("rerun_required_after_repair"),  # 修复后是否必须重跑
        "delivery_issues_by_rule": dict_report.get("delivery_issues_by_rule"),  # 规则级修复聚合
        "errors": dict_report.get("errors"),  # 聚合后阻断问题数量
        "strict_warnings": dict_report.get("strict_warnings"),  # strict 模式 warning 数量
        "checks": dict_report.get("checks"),  # 子门禁摘要证据
        "vg_catalog_version": dict_report.get("vg_catalog_version"),  # 统一 VG 目录版本
        "vg_rule_summary": dict_report.get("vg_rule_summary"),  # VG 逐规则状态汇总
    }  # 交付门禁摘要 metrics

    # list_issues 只补充 strict warning 阻断，error 已由既有子门禁进入 issue 流。
    list_issues: list[ValidationIssue] = []  # 交付门禁新增 validation 诊断

    # existing RTL 诊断模式不因注释 warning 阻断。
    if not strict_generated_comments:

        # 返回摘要，不新增阻断诊断。
        return list_issues, dict_metrics

    # 只有 warning 导致不可交付时，validation 需要显式 error 才能保持 ok 语义。
    if (
        not dict_report.get("delivery_ready")
        and int(dict_report.get("errors") or 0) == 0
        and int(dict_report.get("strict_warnings") or 0) > 0
    ):

        # strict warning 是最终交付门禁的阻断条件。
        list_issues.append(
            ValidationIssue(
                "error",
                "Deliverable gate blocked by strict warning(s); fix or explicitly use non-delivery analysis mode.",
                str(root),
                "static",
                "current_module_issue",
                tool="verilog_generated_deliverable_gate",
                detail=f"strict_warnings={dict_report.get('strict_warnings')}",
            )
        )

    # 返回交付门禁新增诊断和摘要。
    return list_issues, dict_metrics

# 返回 placeholder 残留诊断。
def _validate_placeholders(root: Path, files: list[Path]) -> list[ValidationIssue]:
    """
    返回 placeholder 残留诊断。

    :param root: 生成 artifact 根目录，用于计算报告相对路径。
    :param files: 需要扫描的 RTL 源文件列表。
    :return: TODO、FIXME 等占位文本残留诊断。
    """

    # list_issues 保存 RTL 占位标记残留的阻断诊断。
    list_issues: list[ValidationIssue] = []  # 占位文本残留诊断集合

    # 逐文件扫描占位模式。
    for path_file in files:

        # str_text 是当前 RTL 文本。
        str_text = _read_text(path_file)  # 当前 RTL 文件文本

        # 逐个占位模式检查。
        for str_pattern in PLACEHOLDER_PATTERNS:

            # 命中占位模式时记录 error。
            if str_pattern.lower() in str_text.lower():

                # 追加 placeholder 诊断。
                list_issues.append(
                    ValidationIssue(
                        "error",
                        f"Generated RTL still contains placeholder marker {str_pattern!r}.",
                        str(path_file.relative_to(root)),
                        "static",
                        "current_module_issue",
                    )
                )

    # 所有 testbench 文件扫描完毕后返回合同诊断。
    return list_issues

# 返回 RTL 文件存在性和顶层模块检查诊断。
def _validate_rtl(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """
    返回 RTL 文件存在性和顶层模块检查诊断。

    :param spec: 已归一化的 Verilog 规格。
    :param root: 生成 artifact 根目录。
    :return: RTL 文件缺失或顶层 module 缺失诊断。
    """

    # list_issues 保存 RTL 基础诊断。
    list_issues: list[ValidationIssue] = []  # RTL 基础 gate 诊断

    # list_files 是全部 Verilog-like 文件。
    list_files = _rtl_files(root)  # Verilog-like artifact 文件集合

    # 无 RTL 文件时立即阻断。
    if not list_files:

        # 生成目录缺少 Verilog 文件。
        return [ValidationIssue("error", "No Verilog RTL files found.", str(root), "static", "spec_issue")]

    # str_module_name 是 spec 顶层模块名。
    str_module_name = str(spec.get("name") or "")  # 期望顶层模块名

    # 未声明顶层时不做模块名检查。
    if not str_module_name:

        # 返回已有诊断。
        return list_issues

    # str_combined_text 用于检查顶层 module 是否出现。
    str_combined_text = "\n".join(_read_text(path_file) for path_file in list_files)  # 全部 RTL 文本拼接

    # 顶层 module 缺失时阻断。
    if not re.search(rf"\bmodule\s+{re.escape(str_module_name)}\b", str_combined_text):

        # 记录顶层缺失。
        list_issues.append(
            ValidationIssue(
                "error",
                f"Top module {str_module_name!r} was not found in generated RTL.",
                stage="static",
                source="current_module_issue",
            )
        )

    # 返回 RTL 基础诊断。
    return list_issues

# 返回 testbench 自检和 reference 覆盖诊断。
def _validate_rtl_testbench(
    spec: dict[str, Any],
    root: Path,
    reference_cases: list[str],
    semantic_contract: dict[str, Any] | None,
) -> list[ValidationIssue]:
    """
    返回 testbench 自检和 reference 覆盖诊断。

    :param spec: 已归一化的 Verilog 规格。
    :param root: 生成 artifact 根目录。
    :param reference_cases: testbench 必须覆盖的 reference case id 列表。
    :param semantic_contract: 可选 reference 合同，提供 checkpoint 覆盖要求。
    :return: testbench 缺失、伪 PASS、缺少失败路径或 reference 覆盖不足诊断。
    """

    # list_issues 保存 testbench 自检结构和 reference 覆盖诊断。
    list_issues: list[ValidationIssue] = []  # testbench 自检门禁诊断集合

    # testbench 候选只从文件名识别，避免把 DUT 源文件误当仿真驱动。
    list_testbenches = [path_file for path_file in _rtl_files(root) if _is_testbench(path_file)]  # 文件名命中的测试平台列表

    # bool_requested 表示 spec 是否要求 testbench artifact。
    bool_requested = _spec_requests_testbench(spec)  # 是否显式请求 testbench

    # 请求 testbench 但没有文件时阻断。
    if bool_requested and not list_testbenches:

        # 记录缺失 testbench。
        return [ValidationIssue("error", "No Verilog testbench file found.", stage="static", source="testbench_issue")]

    # str_dut_name 是 testbench 必须实例化的被测顶层模块名。
    str_dut_name = str(spec.get("name") or "")  # testbench 期望实例化的 DUT 名

    # 逐个 testbench 检查自检结构。
    for path_tb in list_testbenches:

        # str_text 保存当前 testbench 文件内容，用于实例化、自检和 reference 覆盖扫描。
        str_text = _read_text(path_tb)  # 当前 testbench 源码文本

        # str_rel_path 让 testbench 问题定位保持相对生成根目录。
        str_rel_path = str(path_tb.relative_to(root))  # 测试平台报告定位

        # DUT instance 是 testbench 真实连接被测模块的最低要求。
        if str_dut_name and not re.search(rf"\b{re.escape(str_dut_name)}\s+\w+\s*\(", str_text):

            # 缺少 DUT instance 会导致伪通过。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Testbench does not instantiate the DUT instance.",
                    str_rel_path,
                    "static",
                    "testbench_issue",
                )
            )

        # bool_has_pass 标记 testbench 是否声明成功路径，后续必须用比较保护。
        bool_has_pass = _testbench_claims_pass(str_text)  # testbench 是否明确打印 PASS

        # bool_has_comparison 区分真实自检 testbench 与只打印日志的伪测试。
        bool_has_comparison = _testbench_has_comparison(str_text)  # testbench 是否含结果比较

        # PASS 但无比较是 fake PASS 风险。
        if bool_has_pass and not bool_has_comparison:

            # 记录 fake PASS 风险。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Testbench only prints PASS without a real comparison; this is a fake PASS risk.",
                    str_rel_path,
                    "static",
                    "testbench_issue",
                )
            )

        # 有比较但没有失败路径会掩盖错误。
        if bool_has_comparison and not _testbench_has_failure_path(str_text):

            # 记录缺失失败路径。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Testbench compares results but has no failure path such as $error or $fatal.",
                    str_rel_path,
                    "static",
                    "testbench_issue",
                )
            )

        # reference case 文本覆盖保证每个向量场景都进入 testbench 可审查范围。
        _append_missing_reference_cases(list_issues, str_text, str_rel_path, reference_cases)

        # checkpoint 覆盖保证 semantic contract 的关键观测点被显式比较。
        _append_missing_reference_checkpoints(list_issues, str_text, str_rel_path, semantic_contract)

    # 返回 DUT 实例、自检结构和 reference 覆盖的累计诊断。
    return list_issues

# 追加 reference case 缺失诊断。
def _append_missing_reference_cases(
    list_issues: list[ValidationIssue],
    text: str,
    path: str,
    reference_cases: list[str],
) -> None:
    """
    追加 reference case 缺失诊断。

    :param list_issues: 待扩展的 testbench 诊断列表。
    :param text: 当前 testbench 源码文本。
    :param path: 当前 testbench 的报告路径。
    :param reference_cases: 必须在 testbench 文本中出现的 case id 列表。
    :return: 无返回值，直接扩展 list_issues。
    """

    # 逐个 case 检查 testbench 文本覆盖。
    for str_case_id in reference_cases:

        # case id 不在文本中时无法证明覆盖。
        if str_case_id not in text:

            # 用 case_id 字段保留 reference case 的机器可读定位。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"Reference case {str_case_id!r} is not covered by the testbench.",
                    path,
                    "static",
                    "testbench_issue",
                    case_id=str_case_id,
                )
            )

# semantic contract 声明的 checkpoint 必须在 testbench 中出现显式比较。
def _append_missing_reference_checkpoints(
    list_issues: list[ValidationIssue],
    text: str,
    path: str,
    semantic_contract: dict[str, Any] | None,
) -> None:
    """
    追加 reference checkpoint 缺失诊断。

    :param list_issues: 待扩展的 testbench 诊断列表。
    :param text: 当前 testbench 源码文本。
    :param path: 当前 testbench 的报告路径。
    :param semantic_contract: 可选 reference 合同，读取 checkpoint_keys 字段。
    :return: 无返回值，直接扩展 list_issues。
    """

    # 没有 semantic contract 时不检查 checkpoint。
    if not semantic_contract:

        # 无合同无需追加诊断。
        return

    # obj_list_candidate_checkpoint_keys 先按 object 接收，随后只允许 list 进入扫描。
    obj_list_candidate_checkpoint_keys: object = semantic_contract.get("checkpoint_keys", [])  # reference checkpoint 原始字段

    # list_checkpoints 只接受列表格式，避免字符串被逐字符扫描。
    if isinstance(obj_list_candidate_checkpoint_keys, list):

        # 原始 checkpoint_keys 已通过列表类型收窄。
        list_checkpoints = obj_list_candidate_checkpoint_keys  # testbench 必须静态比较的 checkpoint 名称

    # 非列表 checkpoint_keys 不能作为可比较点集合。
    else:

        # 非法 checkpoint_keys 字段按空列表处理。
        list_checkpoints = []  # 空 reference checkpoint 白名单

    # 逐个 checkpoint 检查比较表达式。
    for obj_checkpoint in list_checkpoints:

        # str_checkpoint 统一为文本后用于 testbench 表达式匹配。
        str_checkpoint = str(obj_checkpoint)  # testbench 需要比较的 checkpoint 名称

        # checkpoint 未被比较时记录错误。
        if not _checkpoint_is_compared(str_checkpoint, text):

            # detail 字段保留 checkpoint 名称，便于 workflow diagnosis 聚合。
            list_issues.append(
                ValidationIssue(
                    "error",
                    f"Reference checkpoint {str_checkpoint!r} is not compared by the testbench.",
                    path,
                    "static",
                    "testbench_issue",
                    detail=f"checkpoint={str_checkpoint}",
                )
            )

# 返回 semantic contract 的语义执行诊断和 metrics。
def _validate_semantic_execution(
    root: Path,
    semantic_contract: dict[str, Any] | None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """
    返回 semantic contract 的语义执行诊断和 metrics。

    :param root: 生成 artifact 根目录，用于查找 semantic transcript 日志。
    :param semantic_contract: 可选 reference 合同，声明 case 和 checkpoint 要求。
    :return: semantic transcript 诊断列表和 metrics 字典。
    """

    # 没有 semantic contract 时不生成 semantic gate。
    if not semantic_contract:

        # 返回空 semantic 输出。
        return [], {}

    # list_issues 保存 reference transcript 缺失或失败的语义诊断。
    list_issues: list[ValidationIssue] = []  # reference 语义验证诊断集合

    # list_case_ids 是 semantic contract 要求 testbench 覆盖的 case id。
    list_case_ids = _reference_case_ids(semantic_contract)  # reference 语义 case 列表

    # obj_list_candidate_checkpoint_keys 先按 object 接收，避免非列表配置直接污染 metrics。
    obj_list_candidate_checkpoint_keys: object = semantic_contract.get("checkpoint_keys", [])  # reference checkpoint 原始列表

    # semantic metrics 只记录列表形态的 checkpoint，非列表合同不参与语义闭环统计。
    if isinstance(obj_list_candidate_checkpoint_keys, list):

        # 合法 checkpoint 列表可进入 semantic metrics 和缺 transcript 诊断。
        list_checkpoint_keys = obj_list_candidate_checkpoint_keys  # semantic transcript 必须证明的 checkpoint 名称

    # 非列表 checkpoint_keys 不能进入 semantic metrics。
    else:

        # 非法 checkpoint_keys 字段按空语义 checkpoint 集合处理。
        list_checkpoint_keys = []  # 非法合同下不要求 semantic checkpoint 证明

    # dict_metrics 是 workflow diagnosis 使用的 semantic_execution 字段。
    dict_metrics: dict[str, Any] = {  # semantic_execution 度量
        "required_case_ids": list_case_ids,  # 语义执行必须覆盖的 case id
        "required_checkpoint_keys": [str(item) for item in list_checkpoint_keys],  # 必须比较的 checkpoint 名称
        "semantic_ready": True,  # 是否具备 transcript 级语义证据
        "mismatched_cases": [],  # reference 与 transcript 不一致的 case
        "checkpoint_drift": [],  # checkpoint 观测值漂移记录
        "failed_cases": [],  # transcript 中失败的 reference case
        "localization_confidence": 0.85,  # verify-repair 使用的默认定位置信度
    }

    # transcript 存在时使用 semantic_contract 模块做语义比较。
    dict_transcript = _parse_transcript_from_generated(root)  # 解析到的 semantic transcript

    # 没有 transcript 时只依赖 testbench 静态覆盖，semantic_ready 标记为 False。
    if dict_transcript is None:

        # 没有运行证据不能声明语义 ready。
        dict_metrics["semantic_ready"] = False  # transcript 缺失时语义闭环未完成

        # 只有缺少 concrete cases 的外部合同才无法通过静态 testbench 覆盖兜底。
        if _semantic_contract_requires_transcript(semantic_contract):

            # 缺少 transcript 时仍要为 reference 语义闭环登记错误。
            _append_missing_semantic_transcript_issues(list_issues, list_case_ids, list_checkpoint_keys)

        # 返回 metrics 和缺失 semantic transcript 的错误诊断。
        return list_issues, dict_metrics

    # dict_comparison 是 reference 与 transcript 的比较结果。
    dict_comparison = compare_reference_to_transcript(semantic_contract, dict_transcript)  # reference transcript 比较结果

    # 比较结果合入 semantic metrics。
    dict_metrics.update(dict_comparison)

    # 有失败 case 时补充 testbench_issue。
    for str_case_id in dict_comparison.get("failed_cases", []) or []:

        # 记录失败 case。
        list_issues.append(
            ValidationIssue(
                "error",
                f"Reference case {str_case_id!r} failed in semantic transcript.",
                stage="static",
                source="testbench_issue",
                case_id=str(str_case_id),
            )
        )

    # 返回 semantic 输出。
    return list_issues, dict_metrics

# 判断缺少 transcript 时 semantic contract 是否必须报错。
def _semantic_contract_requires_transcript(semantic_contract: dict[str, Any]) -> bool:
    """
    判断缺少 transcript 时 semantic contract 是否必须报错。

    :param semantic_contract: 已提供的 reference 合同。
    :return: 合同无法由静态 concrete cases 兜底时返回 True。
    """

    # concrete cases 能由 testbench 静态覆盖规则兜底审查。
    obj_cases = semantic_contract.get("cases")  # 用于识别静态覆盖可审查的 concrete cases

    # 没有 concrete cases 时只剩 case_ids/checkpoint_keys，必须依赖 transcript 证明比较闭环。
    return not isinstance(obj_cases, list) or not obj_cases

# 追加 semantic transcript 缺失导致的 reference 比较错误。
def _append_missing_semantic_transcript_issues(
    list_issues: list[ValidationIssue],
    list_case_ids: list[str],
    list_checkpoint_keys: list[Any],
) -> None:
    """
    追加 semantic transcript 缺失导致的 reference 比较错误。

    :param list_issues: 待扩展的 semantic 诊断列表。
    :param list_case_ids: 必须由 transcript 覆盖的 case id 列表。
    :param list_checkpoint_keys: 必须由 transcript 证明比较过的 checkpoint 列表。
    :return: 无返回值，直接扩展 list_issues。
    """

    # 每个 reference case 都需要 transcript 证明已执行。
    for str_case_id in list_case_ids:

        # 缺少 transcript 时无法证明该 case 的执行结果。
        list_issues.append(
            ValidationIssue(
                "error",
                f"Reference case {str_case_id!r} has no semantic transcript comparison.",
                stage="static",
                source="testbench_issue",
                case_id=str(str_case_id),
            )
        )

    # 每个 checkpoint 都需要 transcript 证明被观测和比较。
    for obj_checkpoint in list_checkpoint_keys:

        # checkpoint 统一转成文本，保持诊断 detail 可读。
        str_checkpoint = str(obj_checkpoint)  # 缺少 transcript 的 checkpoint 名称

        # 缺少 transcript 时无法证明 checkpoint 比较闭环。
        list_issues.append(
            ValidationIssue(
                "error",
                f"Reference checkpoint {str_checkpoint!r} has no semantic transcript comparison.",
                stage="static",
                source="testbench_issue",
                detail=f"checkpoint={str_checkpoint}",
            )
        )

# 从生成目录中的日志解析 semantic transcript。
def _parse_transcript_from_generated(root: Path) -> dict[str, Any] | None:
    """
    从生成目录中的日志解析 semantic transcript。

    :param root: 生成 artifact 根目录。
    :return: 首个可解析的 semantic transcript；没有匹配日志时返回 None。
    """

    # list_candidates 覆盖常见 log/txt 输出。
    list_candidates = sorted(  # 当前生成目录下仍可访问的 transcript 候选文件列表
        path_item  # 当前仍可访问的 transcript 候选文件
        for path_item in _iter_existing_tree_paths(root)  # 递归扫描生成目录下当前仍可访问的路径
        if _path_is_existing_file(path_item) and path_item.suffix.lower() in {".log", ".txt"}  # 只保留 log/txt 候选
    )

    # 逐个候选查找 reference tag。
    for path_candidate in list_candidates:

        # str_text 是候选日志文本。
        str_text = _read_text(path_candidate)  # transcript 候选文本

        # 没有 reference tag 时跳过。
        if SEMANTIC_RESULT_TAG not in str_text:

            # 继续检查其他日志。
            continue

        # parse_semantic_transcript 负责解析 tagged transcript。
        try:

            # 成功解析后返回 transcript。
            return parse_semantic_transcript(str_text)

        # 当前日志含 tag 但格式不可解析时，继续尝试其他候选。
        except ValueError:

            # 单个日志格式错误不阻断其他候选。
            continue

    # 没有可解析 transcript。
    return None

# 返回 vector contract 覆盖诊断。
def _validate_vector_contracts(root: Path) -> list[ValidationIssue]:
    """
    返回 vector contract 覆盖诊断。

    :param root: 生成 artifact 根目录。
    :return: vector contract hash 未嵌入 RTL 注释的可追溯性诊断。
    """

    # list_issues 保存 vector contract hash 未嵌入 RTL 的可审查性诊断。
    list_issues: list[ValidationIssue] = []  # vector contract 覆盖诊断集合

    # list_contracts 是生成目录中发现的向量合同文件内容。
    list_contracts = find_vector_contracts(root)  # vector contract 数据列表

    # 无 contract 时无需检查 hash。
    if not list_contracts:

        # 返回空诊断。
        return list_issues

    # set_hashes 收集 RTL 中声明的 vector hash。
    set_hashes: set[str] = set()  # RTL 中提取的 vector hash 集合

    # 逐个 RTL 文件提取 hash tag。
    for path_file in _rtl_files(root):

        # 当前文件的 hash 合入集合。
        set_hashes.update(extract_vector_hashes(_read_text(path_file)))

    # contract hash 未出现在 RTL 中时告警。
    for contract in list_contracts:

        # str_digest 是必须嵌入 RTL 注释以保持向量可追溯性的 sha256。
        str_digest = str(contract.get("sha256", ""))  # vector contract 哈希摘要

        # 空 digest 或已嵌入 RTL 时跳过。
        if not str_digest or str_digest in set_hashes:

            # 当前 contract 已满足。
            continue

        # 缺失 hash 是可审查性 warning。
        list_issues.append(
            ValidationIssue(
                "warning",
                "Vector contract hash is not embedded in generated RTL comments.",
                stage="static",
                source="current_module_issue",
                detail=str_digest,
            )
        )

    # vector contract 扫描结束后返回哈希追踪诊断。
    return list_issues

# 返回 vector contract 中声明的 reference case id。
def _collect_reference_cases(root: Path) -> list[str]:
    """
    返回 vector contract 中声明的 reference case id。

    :param root: 生成 artifact 根目录。
    :return: 从 vector contract 收集并排序后的 reference case id。
    """

    # set_cases 去重保存从 vector contract 推导出的 reference case id。
    set_cases: set[str] = set()  # vector contract case 去重集合

    # 遍历所有 vector contract。
    for contract in find_vector_contracts(root):

        # obj_cases 是单个 vector contract 中的 case_ids 原始字段。
        obj_cases = contract.get("case_ids", [])  # 用于 testbench 覆盖匹配的 vector case 列表

        # 只处理列表格式。
        if not isinstance(obj_cases, list):

            # 非列表 case_ids 忽略。
            continue

        # 逐个 case id 加入集合。
        for obj_case_id in obj_cases:

            # 字符串化 case id，保持与 testbench 文本匹配。
            set_cases.add(str(obj_case_id))

    # 返回稳定排序的 case id。
    return sorted(set_cases)

# 返回 semantic contract 中的 case id 列表。
def _reference_case_ids(semantic_contract: dict[str, Any] | None) -> list[str]:
    """
    返回 semantic contract 中的 case id 列表。

    :param semantic_contract: 可选 reference 合同。
    :return: 字符串化后的 case id 列表；没有合同或字段非法时返回空列表。
    """

    # 无 contract 时返回空列表。
    if not semantic_contract:

        # 没有显式 reference case。
        return []

    # obj_cases 是显式 semantic contract 中的 case_ids 原始字段。
    obj_cases = semantic_contract.get("case_ids", [])  # 显式 reference case_ids 字段

    # 非列表格式视为空。
    if not isinstance(obj_cases, list):

        # 不接受非列表 case_ids。
        return []

    # 返回字符串化 case id。
    return [str(obj_case_id) for obj_case_id in obj_cases]

# 返回 RTL 可审查性 warning。
def _validate_rtl_reviewability(root: Path, comment_language: str) -> list[ValidationIssue]:
    """
    返回 RTL 可审查性 warning。

    :param root: 生成 artifact 根目录。
    :param comment_language: 注释语言策略，只有中文策略触发 CJK 审查。
    :return: 缺少中文解释注释的 RTL 文件 warning。
    """

    # list_issues 保存缺少中文审查注释的 RTL 可审查性诊断。
    list_issues: list[ValidationIssue] = []  # 中文审查注释诊断集合

    # 中文注释策略要求至少存在中文说明。
    if comment_language != "zh":

        # 非中文策略不检查 CJK 注释。
        return list_issues

    # 逐个 RTL 源文件检查中文注释。
    for path_file in _rtl_source_files(root):

        # str_text 保存当前 RTL 源文件内容，用于抽取注释文本。
        str_text = _read_text(path_file)  # 当前 RTL 可审查文本

        # 文件含中文注释时通过。
        if _contains_cjk(_comment_texts(str_text)):

            # 当前文件有中文解释。
            continue

        # 缺中文解释时给 warning。
        list_issues.append(
            ValidationIssue(
                "warning",
                "Generated RTL has no Chinese explanatory comments for review.",
                str(path_file.relative_to(root)),
                "static",
                "current_module_issue",
            )
        )

    # RTL 源文件审查完毕后返回中文注释覆盖诊断。
    return list_issues

# 返回行内注释 gate 的诊断和 metrics。
def _validate_line_comment_gate(root: Path, comment_language: str) -> tuple[list[ValidationIssue], dict[str, int]]:
    """
    返回行内注释 gate 的诊断和 metrics。

    :param root: 生成 artifact 根目录。
    :param comment_language: 注释语言策略。
    :return: 行内注释诊断列表和覆盖率度量。
    """

    # list_issues 保存行内注释问题。
    list_issues: list[ValidationIssue] = []  # 行内注释诊断集合

    # dict_metrics 记录行内注释 gate 的扫描规模和违规数。
    dict_metrics = {
        "scanned_files": 0,  # 已扫描的 Verilog-like 文件数
        "code_lines": 0,  # 需要行内注释的关键代码行数
        "commented_code_lines": 0,  # 已满足注释语言策略的关键行数
        "violations": 0,  # 缺少合规行内注释的关键行数
    }  # 行内注释覆盖扫描度量

    # 逐个 RTL 文件扫描。
    for path_file in _rtl_files(root):

        # 当前文件进入扫描计数。
        dict_metrics["scanned_files"] += 1  # 本轮已纳入检查的源文件数量

        # list_lines 是当前文件文本行。
        list_lines = _read_text(path_file).splitlines()  # 当前 RTL 文件行列表

        # 逐行检查关键语句。
        for int_line_number, str_line in enumerate(list_lines, start=1):

            # tuple_split 分离代码和行内注释。
            tuple_split = _split_verilog_code_and_comment(str_line)  # 当前行代码和注释

            # str_code 是去除注释后的 Verilog 代码。
            str_code = tuple_split[0].strip()  # 当前行 Verilog 代码

            # str_comment 是当前行注释文本。
            str_comment = tuple_split[1].strip()  # 当前行 Verilog 注释

            # 非关键代码行不要求注释。
            if not _requires_generated_line_comment(str_code):

                # 跳过不需要行内注释的行。
                continue

            # 关键代码行进入 code_lines 计数。
            dict_metrics["code_lines"] += 1  # 需要行内注释的关键语句数

            # 注释语言满足要求时计入 commented。
            if _comment_satisfies_language(str_comment, comment_language):

                # 当前关键行注释有效。
                dict_metrics["commented_code_lines"] += 1  # 当前累计合规的关键语句数量

                # 合规关键行无需生成诊断，继续扫描下一行。
                continue

            # 缺失注释时记录 violation。
            dict_metrics["violations"] += 1  # 当前累计需要修复的关键语句数量

            # 追加行内注释诊断。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Every generated Verilog code line must have a Chinese explanatory comment; "
                    "use a same-line comment for this generated statement.",
                    str(path_file.relative_to(root)),
                    "static",
                    "current_module_issue",
                    detail=f"line={int_line_number}",
                )
            )

    # 返回行内注释缺口诊断及覆盖率统计。
    return list_issues, dict_metrics

# erie_strict 额外检查只在项目指定风格下启用。
def _validate_rtl_style_profile(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """
    返回 RTL style profile 诊断。

    :param spec: 已归一化的 Verilog 规格。
    :param root: 生成 artifact 根目录。
    :return: erie_strict 风格配置下的 RTL 风格诊断。
    """

    # list_issues 保存 erie_strict profile 下的 RTL 风格阻断诊断。
    list_issues: list[ValidationIssue] = []  # erie_strict 风格诊断集合

    # 仅 erie_strict profile 启用额外检查。
    if str(spec.get("rtl_style_profile", "")).lower() != "erie_strict":

        # 非 erie_strict 不追加风格要求。
        return list_issues

    # 逐个 RTL 源文件检查 wire 初始化风险。
    for path_file in _rtl_source_files(root):

        # str_text 保存当前 RTL 源文件内容，用于扫描 wire 初始化风险。
        str_text = _read_text(path_file)  # erie_strict 风格扫描文本

        # wire 声明直接赋值在 erie_strict 下阻断。
        if re.search(r"\bwire\b[^;=]*=", str_text):

            # 记录 wire 初始化问题。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "erie_strict RTL should avoid initialized wire declarations.",
                    str(path_file.relative_to(root)),
                    "static",
                    "current_module_issue",
                )
            )

    # erie_strict 文件扫描完毕后返回风格诊断。
    return list_issues

# 返回规格是否请求 testbench artifact。
def _spec_requests_testbench(spec: dict[str, Any]) -> bool:
    """
    返回规格是否请求 testbench artifact。

    :param spec: 已归一化的 Verilog 规格。
    :return: outputs 中声明 testbench kind 或 _tb 路径时返回 True。
    """

    # obj_list_candidate_outputs 先按 object 接收，随后只允许列表触发 testbench 需求。
    obj_list_candidate_outputs: object = spec.get("outputs", [])  # testbench 请求判断来源

    # list_outputs 只保留列表形态，非列表配置不应触发 TB 必需检查。
    list_outputs = obj_list_candidate_outputs if isinstance(obj_list_candidate_outputs, list) else []  # 待检查的输出声明列表

    # 逐个输出项检查 testbench 信号。
    for dict_output in list_outputs:

        # 非 dict 输出项不参与判断。
        if not isinstance(dict_output, dict):

            # 继续检查后续输出。
            continue

        # str_path 是输出路径。
        str_path = str(dict_output.get("path", ""))  # 输出 artifact 路径

        # kind 或路径命中 testbench 即返回 True。
        if dict_output.get("kind") == "testbench" or "_tb." in str_path.lower():

            # spec 已要求 testbench。
            return True

    # 未发现 testbench 输出。
    return False

# 返回 testbench 是否包含 if 包裹的比较表达式。
def _testbench_has_comparison(text: str) -> bool:
    """
    返回 testbench 是否包含 if 包裹的比较表达式。

    :param text: testbench 源码文本。
    :return: 同时出现比较运算符和 if 条件时返回 True。
    """

    # 比较运算符和 if 同时存在才视为真实检查。
    return bool(re.search(r"(!==|===|!=|==)", text) and re.search(r"\bif\s*\(", text))

# 返回 testbench 是否通过仿真输出通道宣称 PASS。
def _testbench_claims_pass(text: str) -> bool:
    """
    返回 testbench 是否通过仿真输出通道宣称 PASS。

    :param text: testbench 源码文本。
    :return: display/write/strobe 输出 PASS 时返回 True。
    """

    # 只把 display/write/strobe 中的 PASS 当成通过声明，避免 $fatal 文案中的 PASS 提示误触发。
    return bool(re.search(r"\$(?:display|write|strobe)\s*\([^;]*\bPASS\b", text, flags=re.IGNORECASE | re.DOTALL))

# 返回 testbench 是否会在比较失败时显式报错。
def _testbench_has_failure_path(text: str) -> bool:
    """
    返回 testbench 是否会在比较失败时显式报错。

    :param text: testbench 源码文本。
    :return: 含 $error、$fatal、$finish_and_return 或 FAIL 标记时返回 True。
    """

    # 常见失败路径包含 $error、$fatal、$finish_and_return 或 FAIL 标记。
    return bool(re.search(r"\$(error|fatal|finish_and_return)\b|FAIL", text))

# 返回 checkpoint 是否被 testbench 比较。
def _checkpoint_is_compared(checkpoint: str, text: str) -> bool:
    """
    返回 checkpoint 是否被 testbench 比较。

    :param checkpoint: reference 合同要求的 checkpoint 名称。
    :param text: testbench 源码文本。
    :return: checkpoint 出现且 testbench 存在比较表达式时返回 True。
    """

    # checkpoint 出现且文本存在比较表达式时视为静态覆盖。
    return checkpoint in text and _testbench_has_comparison(text)

# 返回代码行是否必须携带行内解释注释。
def _requires_generated_line_comment(code: str) -> bool:
    """
    返回代码行是否必须携带行内解释注释。

    :param code: 去除行内注释后的单行 Verilog 代码。
    :return: 声明、assign 或模块实例化等关键生成语句返回 True。
    """

    # 空行和纯结构行不要求注释。
    if not code or code.startswith("`timescale"):

        # 非生成逻辑行无需注释。
        return False

    # str_lower 是小写代码，用于关键字判断。
    str_lower = code.lower()  # 小写 Verilog 代码

    # 常见结构关键字不强制行内注释。
    if str_lower in {"begin", "end", "endmodule", "endcase", "endgenerate"}:

        # 结构闭合行不要求注释。
        return False

    # module/always/if/case 等块头由上方注释负责。
    if re.match(r"^\s*(module|always|if|else|case|for|while|generate)\b", str_lower):

        # 块级语句不要求同行注释。
        return False

    # 声明、assign 和实例化行需要同行解释。
    if re.match(r"^\s*(input|output|inout|parameter|localparam|wire|reg|logic|integer|assign)\b", str_lower):

        # 声明或 assign 行需要注释。
        return True

    # 模块实例化行需要说明实例职责。
    return _looks_like_instance_statement(code)

# 返回代码行是否类似模块实例化语句。
def _looks_like_instance_statement(code: str) -> bool:
    """
    返回代码行是否类似模块实例化语句。

    :param code: 去除注释后的单行 Verilog 代码。
    :return: 形如模块名加实例名再接端口括号时返回 True。
    """

    # 跳过明显非实例化开头。
    if re.match(r"^\s*(assign|if|for|while|case|always|module)\b", code):

        # 这些语句不是实例化。
        return False

    # 两个标识符后接括号通常是模块实例化。
    return bool(re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s+(#\s*\([^;]*\)\s*)?[A-Za-z_][A-Za-z0-9_]*\s*\(", code))

# 返回注释是否满足语言要求。
def _comment_satisfies_language(comment: str, comment_language: str) -> bool:
    """
    返回注释是否满足语言要求。

    :param comment: 行内注释文本。
    :param comment_language: 注释语言策略。
    :return: 中文策略下包含 CJK 字符、其他策略下注释非空时返回 True。
    """

    # 缺注释直接失败。
    if not comment.strip():

        # 空注释不满足任何策略。
        return False

    # 中文策略要求 CJK 字符。
    if comment_language == "zh":

        # CJK 字符表示中文说明存在。
        return _contains_cjk(comment)

    # 非中文策略只要求注释非空。
    return True

# 返回一行中的 Verilog 代码和行内注释文本。
def _split_verilog_code_and_comment(line: str) -> tuple[str, str]:
    """
    返回一行中的 Verilog 代码和行内注释文本。

    :param line: 原始 Verilog 单行文本。
    :return: 代码部分和第一处 // 之后的注释部分。
    """

    # 不含 // 时整行都是代码。
    if "//" not in line:

        # 返回空注释。
        return line, ""

    # tuple_parts 分离第一处 //。
    tuple_parts = line.split("//", 1)  # 代码和注释文本

    # 返回代码与注释。
    return tuple_parts[0], tuple_parts[1]

# 返回所有 // 注释文本拼接。
def _comment_texts(text: str) -> str:
    """
    返回所有 // 注释文本拼接。

    :param text: Verilog 源码文本。
    :return: 所有行内 // 注释内容按换行拼接后的文本。
    """

    # list_comments 保存每行注释。
    list_comments: list[str] = []  # 注释文本集合

    # 逐行提取 // 之后的文本。
    for str_line in text.splitlines():

        # 没有 // 的行跳过。
        if "//" not in str_line:

            # 继续处理下一行。
            continue

        # 追加当前行注释文本。
        list_comments.append(str_line.split("//", 1)[1])

    # 返回拼接后的注释文本。
    return "\n".join(list_comments)

# 返回文本中是否包含 CJK 字符。
def _contains_cjk(text: str) -> bool:
    """
    返回文本中是否包含 CJK 字符。

    :param text: 待扫描文本。
    :return: 任一字符落在 CJK 基本区时返回 True。
    """

    # 任一字符落在 CJK 基本区即返回 True。
    return any("\u4e00" <= char <= "\u9fff" for char in text)

# 按 UTF-8 优先读取文本，失败时容错替换。
def _read_text(path: Path) -> str:
    """
    按 UTF-8 优先读取文本，失败时容错替换。

    :param path: 待读取的文本文件路径。
    :return: UTF-8 文本；遇到解码异常时用 replacement 字符保留可扫描内容。
    """

    # 首选 UTF-8，符合当前 skill artifact 输出。
    try:

        # 返回 UTF-8 文本。
        return path.read_text(encoding="utf-8")

    # 部分外部工具日志可能不是严格 UTF-8，失败时保留可扫描文本。
    except UnicodeDecodeError:

        # 编码异常时用 replacement 保留可扫描内容。
        return path.read_text(encoding="utf-8", errors="replace")
