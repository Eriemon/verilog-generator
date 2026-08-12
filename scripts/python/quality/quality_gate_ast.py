"""封装 formatter AST 报告构建与 AST 诊断转换。"""

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

# AST 诊断转换只依赖统一的质量门诊断类型。
from .quality_gate_types import QualityIssue

# AST 诊断行号需要复用 common 中的安全转换 helper。
from .quality_gate_common import _as_line

# 供 `_build_ast_tree_report` 复用的拆分 helper，专门处理构造与旧版本兼容的目录级 formatter AST 聚合报告。
def _build_ast_tree_report(
    path_root: Path,
    str_formatter_profile: str,
    list_file_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构造与旧版本兼容的目录级 formatter AST 聚合报告。

    :param path_root: 质量门检查入口根路径。
    :param str_formatter_profile: formatter_ast 使用的解析 profile 名称。
    :param list_file_reports: 逐文件 formatter AST 报告列表。
    :return: 目录级 formatter AST 聚合报告。
    """

    # int_module_count 汇总所有文件中的 module 数量。
    int_module_count = sum(  # 目录 summary.modules 字段使用的 module 总数
        len(dict_report.get("modules", []))  # 单文件 AST module 条目数
        for dict_report in list_file_reports  # 遍历逐文件 AST 报告
    )  # AST summary.files 下所有 module 条目数量

    # int_parse_errors 汇总 formatter AST 报出的 error 诊断。
    int_parse_errors = sum(  # 目录 summary.parse_errors 字段使用的错误总数
        1  # 每条 error 诊断计为一次 parse error
        for dict_report in list_file_reports  # 逐文件扫描 parse 诊断
        for dict_item in dict_report.get("diagnostics", [])  # 单文件诊断对象
        if dict_item.get("severity") == "error"  # 只统计 error 级 parse 诊断
    )

    # formatter mismatch 独立于 parser 诊断汇总，避免解析成功时继续假绿。
    int_formatter_errors = sum(  # 目录报告暴露的模板差异总量
        len(dict_report.get("formatter_violations", []))  # 单个 RTL 文件的未收敛模板条目数
        for dict_report in list_file_reports  # 汇合全部待交付 RTL 的模板检查结果
    )

    # 返回目录级 AST 报告，字段保持旧调用方兼容。
    return {
        "version": 1,
        "root": str(path_root),
        "profile": str_formatter_profile,
        "files": list_file_reports,
        "summary": {
            "files": len(list_file_reports),
            "modules": int_module_count,
            "parse_errors": int_parse_errors,
            "formatter_errors": int_formatter_errors,
            "errors": int_parse_errors + int_formatter_errors,
        },
    }

# 供 `_ast_diagnostics_to_issues` 复用的拆分 helper，专门处理formatter AST 诊断映射到质量门诊断列表。
def _ast_diagnostics_to_issues(
    dict_ast_report: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    把 formatter AST 诊断映射到质量门诊断列表。

    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 转换后的 QualityIssue 诊断列表。
    """

    # list_issues 存放当前文件的 AST 诊断转换结果。
    list_issues: list[QualityIssue] = []  # AST 转换后的质量门诊断

    # 遍历 formatter_ast 暴露的诊断条目。
    for dict_item in dict_ast_report.get("diagnostics", []):

        # str_severity 先读取 AST 原始级别，后续再按 strict 开关调整。
        str_severity = str(dict_item.get("severity") or "error")  # formatter_ast 原始级别文本

        # 非严格模式下 AST error 降级为 warning。
        if not strict and str_severity == "error":

            # 降级让调用方可在探索阶段查看完整报告。
            str_severity = "warning"  # 非严格模式诊断级别

        # str_message 保留 formatter AST 的具体原因。
        str_message = str(dict_item.get("message") or "Formatter AST diagnostic.")  # AST 诊断正文

        # str_rule 记录原始 formatter AST 诊断代码。
        str_rule = str(dict_item.get("code") or "formatter.ast")  # AST 诊断规则名

        # 追加统一 VG000 诊断。
        list_issues.append(
            QualityIssue(
                "VG000",
                str_severity,
                str_message,
                str_rel_path,
                _as_line(dict_item.get("line")),
                rule=str_rule,
            )
        )

    # 返回当前文件的 AST 诊断。
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
    _build_ast_tree_report
    _ast_diagnostics_to_issues
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
