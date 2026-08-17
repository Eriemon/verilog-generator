"""负责 quality gate JSON 与 Markdown 报告写出。"""

# 延迟类型注解求值，避免导入阶段提前解释联合类型。
from __future__ import annotations

# 标准库路径对象足以处理报告落盘。
from pathlib import Path

# 质量门报告类型由 facade 回导模块统一提供。
from .quality_gate_types import QualityGateReport
from .vg_report_publisher import publish_vg_reports

# 供 `write_quality_gate_report` 复用的拆分 helper，专门处理按调用方请求写出 JSON 和 Markdown 质量门报告。
def write_quality_gate_report(
    report: QualityGateReport,
    *,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> None:
    """
    按调用方请求写出 JSON 和 Markdown 质量门报告。

    :param report: 需要写出的质量门报告对象。
    :param json_path: 可选 JSON 报告输出路径。
    :param markdown_path: 可选 Markdown 报告输出路径。
    :return: 无返回值，按需写出报告文件。
    """

    # 统一 writer 负责双格式原子写出、路径冲突和部分发布语义。
    publish_vg_reports(
        report.to_dict(),
        report.to_markdown(),
        json_path=json_path,
        markdown_path=markdown_path,
    )

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
    write_quality_gate_report
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
