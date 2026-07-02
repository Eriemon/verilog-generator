"""聚合 Erie Verilog 最终交付门禁。"""

# 延迟解析类型注解，避免运行时为报告类型引入额外依赖。
from __future__ import annotations

# 标准库承担报告序列化、临时 lint 根和文件复制。
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

# 子门禁模块提供 AST、静态 lint、注释位置和规则源一致性证据。
from .comment_placement import validate_comment_placement
from .quality_gate import run_verilog_quality_gate
from .rulebook import load_verilog_rulebook
from .static_lint import StaticLintIssue, lint_generated_rtl

# 生成、修改和注释后的 RTL 都通过这个入口得到最终交付结论。
def run_verilog_deliverable_gate(
    root: Path,
    *,
    strict: bool = True,
    comment_language: str = "zh",
    formatter_profile: str = "formatter-normalize",
    include_testbench: bool = False,
    vitis_wrapper: bool = False,
) -> dict[str, Any]:
    """运行最终交付门禁并返回 JSON 友好的报告字典。

    参数:
        root: 需要检查的 Verilog 文件或目录。
        strict: 是否启用交付级严格模式。
        comment_language: 注释语言策略。
        formatter_profile: formatter AST 使用的 profile。
        include_testbench: 是否纳入 testbench 文件。
        vitis_wrapper: 是否按 Vitis wrapper ABI 放宽端口规则。
    返回:
        返回包含 delivery_ready、checks 和 issues 的交付门禁报告。
    """

    # path_root 使用绝对路径，保证报告和 CLI 摘要稳定。
    path_root = root.resolve()  # 本次交付门禁入口路径

    # 子门禁原始结果先集中收集，后续统计阶段不再重复扫描文件。
    dict_context = _collect_deliverable_context(  # 保存各子门禁原始报告，后续只基于它统计交付状态
        path_root,  # 已规范化的 RTL 交付入口
        strict=strict,  # 当前交付严格模式
        comment_language=comment_language,  # 注释语言策略
        formatter_profile=formatter_profile,  # formatter 抽象语法树配置名称
        include_testbench=include_testbench,  # 是否纳入 testbench 文件
        vitis_wrapper=vitis_wrapper,  # Vitis wrapper 端口规则开关
    )  # 交付门禁的原始子检查结果

    # 汇总 error 和 strict warning，保证最终交付条件只有一个判定来源。
    dict_totals = _count_deliverable_totals(dict_context, strict)  # 交付状态计数

    # checks 字段只保留便于人和 CI 快速判断的摘要。
    dict_checks = _build_deliverable_checks(dict_context, dict_totals)  # 子门禁摘要

    # 报告组装阶段只做字段编排，不再执行新检查。
    dict_report = _build_deliverable_report(path_root, strict, dict_context, dict_totals, dict_checks)  # 最终报告

    # 返回 JSON 友好的最终报告。
    return dict_report

# _collect_deliverable_context 保留各子门禁的原始结果。
def _collect_deliverable_context(
    path_root: Path, *, strict: bool, comment_language: str,
    formatter_profile: str, include_testbench: bool, vitis_wrapper: bool,
) -> dict[str, Any]:
    """
    收集最终交付门禁需要的子检查结果。

    :param path_root: 需要检查的 Verilog 文件或目录。
    :param strict: 是否启用交付级严格模式。
    :param comment_language: 注释语言策略。
    :param formatter_profile: formatter AST 使用的 profile。
    :param include_testbench: 是否纳入 testbench 文件。
    :param vitis_wrapper: 是否按 Vitis wrapper ABI 放宽端口规则。
    :return: 包含质量门、lint 和注释 gate 原始结果的上下文字典。
    """

    # 质量门报告保留 VG 规则命中和 formatter AST 统计。
    report_quality = run_verilog_quality_gate(  # 带 AST 证据的 VG 质量门报告
        path_root,  # 待检查的 RTL 文件或目录
        strict=strict,  # 质量门严格模式
        comment_language=comment_language,  # 中文/英文注释要求
        formatter_profile=formatter_profile,  # formatter 抽象语法树配置档名称
        include_testbench=include_testbench,  # testbench 纳入开关
        vitis_wrapper=vitis_wrapper,  # wrapper ABI 兼容开关
    )  # Verilog 质量门报告对象

    # static lint 仍使用现有后端，单文件入口由 helper 隔离目录。
    list_static_lint_issues = _run_static_lint(path_root)  # static lint 原始诊断

    # 注释位置 gate 输出诊断和覆盖率统计。
    tuple_comment_gate = validate_comment_placement(path_root, comment_language)  # 注释位置 gate 原始结果

    # 将 quality gate 的对象诊断转成最终报告可序列化结构。
    list_quality_issues = [issue.to_dict() for issue in report_quality.issues]  # VG 诊断字典集合

    # 将 lint 诊断挂上交付门禁自己的编号前缀。
    list_lint_issues = [_static_lint_issue_to_dict(issue) for issue in list_static_lint_issues]  # 加前缀后的 lint 诊断

    # comment gate 诊断补齐 code/rule/severity 字段。
    list_comment_gate_issues = [_comment_placement_issue_to_dict(issue) for issue in tuple_comment_gate[0]]  # 注释诊断字典集合

    # 子门禁上下文把原始对象和序列化诊断放在一起，避免重复转换。
    dict_context = {
        "quality_report": report_quality,  # 保留 AST summary 和 VG 规则统计
        "static_lint_issues": list_static_lint_issues,  # 保留 lint severity 供计数
        "comment_metrics": tuple_comment_gate[1],  # 注释位置覆盖率和密度指标
        "quality_issues": list_quality_issues,  # 已序列化的 VG 规则诊断
        "lint_issues": list_lint_issues,  # 已统一 code 前缀的 lint 诊断
        "comment_issues": list_comment_gate_issues,  # 已补齐 severity 的注释诊断
    }  # 交付门禁上下文

    # 返回供统计和报告组装复用的上下文。
    return dict_context

# _count_deliverable_totals 只计算最终交付判定所需的数量。
def _count_deliverable_totals(dict_context: dict[str, Any], strict: bool) -> dict[str, Any]:
    """
    统计最终交付门禁的 error 和 strict warning。

    :param dict_context: _collect_deliverable_context 返回的子门禁上下文。
    :param strict: 是否启用 warning 阻断策略。
    :return: 包含各类 error、warning 和 delivery_ready 的计数字典。
    """

    # VG 质量门报告对象提供 error/warning 聚合计数。
    report_quality = dict_context["quality_report"]  # 质量门聚合计数来源

    # static lint 原始诊断保留 severity，方便分别统计 error/warning。
    list_static_lint_issues = dict_context["static_lint_issues"]  # static lint 诊断集合

    # 注释 gate 诊断已补齐 severity 字段。
    list_comment_gate_issues = dict_context["comment_issues"]  # 注释位置诊断集合

    # VG error 来自 AST、命名、区域、reset、FSM 和注释语义规则。
    int_quality_errors = report_quality.errors  # VG 阻断项总量

    # static lint error 表示可综合或 RTL 基础结构风险。
    int_lint_errors = sum(1 for issue in list_static_lint_issues if issue.severity == "error")  # 静态 lint 阻断项

    # 注释位置 error 表示实体级注释落点不满足交付要求。
    int_comment_errors = sum(1 for issue in list_comment_gate_issues if issue.get("severity") == "error")  # 注释落点阻断项

    # VG warning 在 strict 交付中等同待修复问题。
    int_quality_warnings = report_quality.warnings  # VG 可疑项总量

    # lint warning 多为边界风险，strict 模式下也阻断交付。
    int_lint_warnings = sum(1 for issue in list_static_lint_issues if issue.severity == "warning")  # 静态 lint 警告项

    # 注释 warning 代表语义覆盖不足或位置不够可靠。
    int_comment_warnings = sum(1 for issue in list_comment_gate_issues if issue.get("severity") == "warning")  # 注释落点警告项

    # 最终 error 数量由 VG、lint 和 comment gate 三类阻断项组成。
    int_errors = int_quality_errors + int_lint_errors + int_comment_errors  # 最终阻断问题总数

    # strict warning 汇总所有必须在交付前清零的 warning。
    int_strict_warnings = int_quality_warnings + int_lint_warnings + int_comment_warnings  # 严格模式待修复警告总数

    # 交付状态严格绑定到 error 和 strict warning 两类阻断因素。
    bool_delivery_ready = int_errors == 0 and (not strict or int_strict_warnings == 0)  # 最终可交付状态

    # 计数字典避免多个组装函数重复计算同一批数量。
    dict_totals = {
        "quality_errors": int_quality_errors,  # VG 深规则阻断计数
        "lint_errors": int_lint_errors,  # 结构 lint 阻断计数
        "comment_errors": int_comment_errors,  # 注释落点阻断计数
        "quality_warnings": int_quality_warnings,  # VG 可疑诊断计数
        "lint_warnings": int_lint_warnings,  # lint 非阻断风险计数
        "comment_warnings": int_comment_warnings,  # 注释覆盖风险计数
        "errors": int_errors,  # 三类子门禁阻断合计
        "strict_warnings": int_strict_warnings if strict else 0,  # strict 报告展示的 warning 合计
        "delivery_ready": bool_delivery_ready,  # error 和 strict warning 清零后的交付结论
    }  # 交付门禁计数摘要

    # 返回统一计数结果。
    return dict_totals

# _build_deliverable_checks 生成给人快速扫读的子门禁摘要。
def _build_deliverable_checks(dict_context: dict[str, Any], dict_totals: dict[str, Any]) -> dict[str, Any]:
    """
    构造最终报告中的 checks 摘要。

    :param dict_context: 子门禁原始结果和诊断字典。
    :param dict_totals: _count_deliverable_totals 返回的计数摘要。
    :return: JSON 友好的 checks 字段。
    """

    # VG 报告中的 AST summary 是 formatter parser 的结构化证据。
    report_quality = dict_context["quality_report"]  # checks 中 VG 摘要的数据源

    # AST summary 缺失时使用空字典，避免报告组装失败。
    dict_ast_summary = report_quality.ast_report.get("summary", {})  # formatter 解析覆盖摘要

    # checks 字段固定为五个子区域，便于 workflow trace 读取。
    dict_checks: dict[str, Any] = {}  # 子门禁摘要字典

    # formatter_ast 摘要定位 AST 是否真正覆盖文件和 module。
    dict_checks["formatter_ast"] = {
        "files": dict_ast_summary.get("files", 0),  # formatter 扫描到的 RTL 文件数
        "modules": dict_ast_summary.get("modules", 0),  # formatter 成功识别的 module 数
        "parse_errors": dict_ast_summary.get("parse_errors", 0),  # formatter 后端解析失败数
    }

    # verilog_quality_gate 摘要保留 VG error/warning 状态。
    dict_checks["verilog_quality_gate"] = {
        "ok": report_quality.ok(),  # VG 子门禁自身通过状态
        "errors": dict_totals["quality_errors"],  # AST/命名/reset/FSM 阻断计数
        "warnings": dict_totals["quality_warnings"],  # VG 规则发现的待复核风险
    }

    # static_lint 摘要保留 lint 问题总数和严重级别计数。
    dict_checks["static_lint"] = {
        "errors": dict_totals["lint_errors"],  # static lint 阻断计数
        "warnings": dict_totals["lint_warnings"],  # static lint 风险提示计数
        "issues": len(dict_context["static_lint_issues"]),  # 原始 lint 诊断条数
    }

    # comment_gate 摘要把注释位置指标放入同一层，方便审查语义注释覆盖率。
    dict_checks["comment_gate"] = {
        "errors": dict_totals["comment_errors"],  # 注释位置阻断计数
        "warnings": dict_totals["comment_warnings"],  # 注释语义覆盖风险计数
        "issues": len(dict_context["comment_issues"]),  # 注释 gate 诊断条数
        "metrics": dict_context["comment_metrics"],  # 注释覆盖率指标
    }

    # rulebook 摘要让 VG059 的规则源路径可追溯。
    dict_checks["rulebook"] = {"path": str(load_verilog_rulebook().path)}  # JSON 规则源路径

    # 返回 checks 字段。
    return dict_checks

# _build_deliverable_report 汇总最终 JSON 报告字段。
def _build_deliverable_report(
    path_root: Path,
    strict: bool,
    dict_context: dict[str, Any],
    dict_totals: dict[str, Any],
    dict_checks: dict[str, Any],
) -> dict[str, Any]:
    """
    组装最终交付门禁报告。

    :param path_root: 已规范化的交付入口路径。
    :param strict: 是否启用交付级严格模式。
    :param dict_context: 子门禁原始结果和诊断字典。
    :param dict_totals: 交付状态计数摘要。
    :param dict_checks: 子门禁摘要字段。
    :return: JSON 友好的最终报告字典。
    """

    # 诊断顺序保持 VG、lint、comment gate，方便从根因到补充证据阅读。
    list_issues = [
        *dict_context["quality_issues"],  # VG 深规则诊断排在最前
        *dict_context["lint_issues"],  # lint 结构风险作为补充证据
        *dict_context["comment_issues"],  # 注释位置诊断放在最后
    ]  # 最终报告诊断集合

    # 报告骨架先写入摘要字段，随后补充子门禁详情。
    dict_report: dict[str, Any] = {
        "version": 1,  # 报告结构版本
        "root": str(path_root),  # 被检查的 Verilog 入口
        "strict": strict,  # 是否启用 strict 交付策略
        "delivery_ready": dict_totals["delivery_ready"],  # strict 交付布尔结论
        "errors": dict_totals["errors"],  # 最终阻断问题合计
        "strict_warnings": dict_totals["strict_warnings"],  # strict 模式警告合计
        "checks": dict_checks,  # workflow trace 读取的子门禁摘要
        "issues": list_issues,  # VG/lint/comment 合并诊断
    }

    # quality_gate 详情保留完整 AST 与 VG 规则命中。
    dict_report["quality_gate"] = dict_context["quality_report"].to_dict()  # 完整 VG 报告

    # static_lint 详情采用统一后的诊断字典。
    dict_report["static_lint"] = dict_context["lint_issues"]  # 统一编号后的 lint 诊断

    # comment_gate 详情保留位置诊断和覆盖指标。
    dict_report["comment_gate"] = {
        "issues": dict_context["comment_issues"],  # 注释落点诊断列表
        "metrics": dict_context["comment_metrics"],  # 注释覆盖率统计
    }

    # 返回完整报告。
    return dict_report

# 报告写出函数只处理文件 IO，不改变门禁结果。
def write_verilog_deliverable_gate_report(
    report: dict[str, Any],
    *,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> None:
    """写出交付门禁报告。

    参数:
        report: run_verilog_deliverable_gate 返回的报告字典。
        json_path: 可选 JSON 输出路径。
        markdown_path: 可选 Markdown 输出路径。
    返回:
        本函数只写文件，不返回业务值。
    """

    # JSON 报告用于 CI 或自动化流程消费。
    if json_path is not None:

        # JSON 输出目录必须先创建，避免报告写出失败。
        json_path.parent.mkdir(parents=True, exist_ok=True)

        # 写出保留中文的 JSON 文本。
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Markdown 报告用于人工审查。
    if markdown_path is not None:

        # Markdown 输出目录独立创建，兼容只写人工报告的调用。
        markdown_path.parent.mkdir(parents=True, exist_ok=True)

        # 写出 Markdown 摘要和诊断表。
        markdown_path.write_text(_deliverable_report_to_markdown(report), encoding="utf-8")

# _run_static_lint 对文件入口使用临时根，避免误扫同目录其他 RTL。
def _run_static_lint(path_root: Path) -> list[StaticLintIssue]:
    """
    对交付入口运行内置 static lint。
    
    :param path_root: 交付门禁入口文件或目录。
    :return: static lint 诊断列表。
    """

    # dict_spec 提供 static lint 需要的最小设计元数据。
    dict_spec = {"name": path_root.stem or path_root.name, "interfaces": {"ports": []}}  # lint 最小规格

    # 目录入口可直接扫描。
    if path_root.is_dir():

        # 返回目录 lint 结果。
        return lint_generated_rtl(dict_spec, path_root)

    # 非文件入口由质量门报告文件发现问题，lint 不重复报错。
    if not path_root.is_file():

        # 没有可扫描的实体。
        return []

    # 单文件入口复制到临时目录，防止 static lint 扫描兄弟文件。
    with tempfile.TemporaryDirectory(prefix="erie-deliverable-lint-") as str_temp_dir:

        # 临时根只包含当前文件，隔离同目录历史 RTL 对 lint 的干扰。
        path_temp_root = Path(str_temp_dir)  # 临时 lint 根目录

        # path_temp_file 保留原始文件名，便于 lint 报告识别。
        path_temp_file = path_temp_root / path_root.name  # 临时 RTL 文件路径

        # 复制目标文件到临时根。
        shutil.copy2(path_root, path_temp_file)

        # 返回临时根 lint 结果。
        return lint_generated_rtl(dict_spec, path_temp_root)

# _static_lint_issue_to_dict 统一 static lint 报告字段。
def _static_lint_issue_to_dict(issue: StaticLintIssue) -> dict[str, Any]:
    """
    把 StaticLintIssue 转换为交付门禁 issues 条目。
    
    :param issue: static lint 诊断对象。
    :return: JSON 友好的诊断字典。
    """

    # dict_issue 先复用 lint 自身导出字段。
    dict_issue = issue.to_dict()  # static lint 原始诊断字典

    # code 加前缀，避免和 VG 编号混淆。
    dict_issue["code"] = f"STATIC_{dict_issue.get('code', 'LINT')}"  # 交付报告中的 lint 编号

    # rule 字段便于和 quality gate issues 对齐。
    dict_issue["rule"] = "static_lint"  # 交付报告规则来源

    # 返回带交付门禁前缀的 lint 诊断。
    return dict_issue

# _comment_placement_issue_to_dict 补齐注释位置诊断的交付报告字段。
def _comment_placement_issue_to_dict(issue: dict[str, Any]) -> dict[str, Any]:
    """
    把 comment placement issue 转换为交付门禁 issues 条目。
    
    :param issue: comment placement 诊断字典。
    :return: JSON 友好的诊断字典。
    """

    # dict_issue 复制输入，避免修改 comment_placement 原始对象。
    dict_issue = dict(issue)  # 注释位置诊断副本

    # code 缺失时使用稳定默认值。
    str_code = str(dict_issue.get("code") or "COMMENT_PLACEMENT")  # 注释诊断编号

    # comment gate 编号加前缀后不会和 VG 规则号混淆。
    dict_issue["code"] = f"COMMENT_{str_code}"  # 交付报告中的注释编号

    # rule 字段便于统一过滤。
    dict_issue["rule"] = str(dict_issue.get("rule") or "comment_gate")  # 注释规则来源

    # 缺少 severity 时保守作为 error。
    dict_issue.setdefault("severity", "error")

    # 返回带 comment gate 来源标记的诊断。
    return dict_issue

# _deliverable_report_to_markdown 生成简洁人工报告。
def _deliverable_report_to_markdown(report: dict[str, Any]) -> str:
    """
    把交付门禁报告转换为 Markdown 文本。
    
    :param report: 交付门禁报告字典。
    :return: Markdown 格式报告文本。
    """

    # list_lines 先写标题和摘要。
    list_lines = [  # Markdown 文本缓冲区，按顺序承载标题、摘要和诊断表
        "# Verilog deliverable gate",  # 交付门禁人工报告标题
        "",  # 标题结束后的段落断点
        f"Root: `{report.get('root')}`",  # 被检查的 RTL 入口路径
        f"Delivery ready: `{report.get('delivery_ready')}`",  # 严格交付通过状态
        f"Summary: **{report.get('errors')} error(s)**, **{report.get('strict_warnings')} strict warning(s)**",  # 错误和警告摘要
        "",  # 摘要结束后的表格断点
    ]  # Markdown 输出行集合

    # 无诊断时返回成功摘要。
    if not report.get("issues"):

        # 保留简短通过文案。
        list_lines.append("No deliverable-gate findings.")

        # 返回带末尾换行的 Markdown。
        return "\n".join(list_lines) + "\n"

    # 写入诊断表头的列名。
    list_lines.append("| Severity | Code | Path | Line | Message |")

    # 写入 Markdown 表格分隔行，保证人工报告能正常渲染。
    list_lines.append("|---|---|---|---:|---|")

    # 逐条写入诊断。
    for dict_issue in report.get("issues", []):

        # 诊断消息中的竖线必须转义，避免破坏 Markdown 表格列。
        str_message = str(dict_issue.get("message") or "").replace("|", "\\|")  # 表格安全诊断文本

        # str_line 为空时保持表格单元为空。
        str_line = "" if dict_issue.get("line") is None else str(dict_issue.get("line"))  # 行号文本

        # 追加 Markdown 表格行。
        list_lines.append(
            f"| {dict_issue.get('severity')} | {dict_issue.get('code')} | "
            f"`{dict_issue.get('path') or ''}` | {str_line} | {str_message} |"
        )

    # 返回完整 Markdown 文本。
    return "\n".join(list_lines) + "\n"
