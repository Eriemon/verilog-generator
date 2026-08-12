"""负责 quality gate JSON 与 Markdown 报告写出。"""

# 延迟类型注解求值，避免导入阶段提前解释联合类型。
from __future__ import annotations

# JSON 序列化用于写出机器可读质量门报告。
import json

# 标准库路径对象足以处理报告落盘。
from pathlib import Path

# 质量门报告类型由 facade 回导模块统一提供。
from .quality_gate_types import QualityGateReport

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

    # JSON 路径存在时先创建父目录再写入 UTF-8 文本。
    if json_path is not None:

        # 报告目录可能来自 CLI 参数，需要按需创建。
        json_path.parent.mkdir(parents=True, exist_ok=True)

        # dict_report_text 保持中文不转义，便于人工审查。
        str_json_text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"  # JSON 报告文本

        # 写入 JSON 报告供机器读取。
        json_path.write_text(str_json_text, encoding="utf-8")

    # Markdown 路径存在时写出用户可读报告。
    if markdown_path is not None:

        # Markdown 报告目录同样按需创建。
        markdown_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入 Markdown 表格报告。
        markdown_path.write_text(report.to_markdown(), encoding="utf-8")

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
