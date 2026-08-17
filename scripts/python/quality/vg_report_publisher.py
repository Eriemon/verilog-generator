"""提供 VG JSON/Markdown 报告的双阶段、可追溯写出。"""

# future annotations 让共享 writer 在 CLI 与库调用中保持轻量导入。
from __future__ import annotations

# json 序列化机器报告，os.replace 提供同目录原子替换。
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

# VgReportPublishError 区分报告写出失败与门禁本身的 RTL 违规。
class VgReportPublishError(OSError):
    """表示 JSON/Markdown 报告未能完整发布。"""

# _atomic_write_text 先落临时文件再替换目标，避免半截报告。
def _atomic_write_text(path_output: Path, text_content: str) -> None:
    """原子写出单个 UTF-8 报告文件。

    参数:
        path_output: 目标报告路径。
        text_content: 待写入的完整文本。
    返回:
        无业务返回值。
    异常:
        OSError: 临时文件或目标文件无法写入。
    """

    # 输出目录属于调用方项目，缺失时由 writer 创建。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 临时文件与目标位于同一目录，保证 os.replace 保持原子性。
    int_fd, str_temp_name = tempfile.mkstemp(  # 同目录临时文件支持原子替换
        prefix=f".{path_output.name}.",  # 保留目标名便于故障定位
        suffix=".tmp",  # 临时后缀避免误读为正式报告
        dir=path_output.parent,  # 同目录保证 os.replace 不跨卷
        text=True,  # 直接写入 UTF-8 文本
    )

    # 先关闭原始文件描述符，再由 Path 写入，避免 Windows 文件锁。
    os.close(int_fd)

    # 临时路径由 mkstemp 返回，最终失败时必须清理。
    path_temp = Path(str_temp_name)  # 将临时文件句柄转换为可读路径

    # 写入和替换必须成组执行，失败时统一清理临时文件。
    try:

        # 写入完整内容后才替换现有报告。
        path_temp.write_text(text_content, encoding="utf-8")

        # 同目录替换让读者只看到旧文件或完整新文件。
        os.replace(path_temp, path_output)

    # finally 无条件回收临时路径，避免失败残留污染后续运行。
    finally:

        # 替换失败时清理临时文件，保留已有目标文件供恢复。
        if path_temp.exists():

            # unlink 失败不能覆盖原始发布错误。
            path_temp.unlink()

# publish_vg_reports 按 Markdown 后 JSON 顺序发布两个可选报告。
def publish_vg_reports(
    report: Mapping[str, Any],
    markdown_text: str,
    *,
    json_path: Path | None,
    markdown_path: Path | None,
) -> None:
    """发布 VG JSON 和 Markdown 报告。

    参数:
        report: JSON 机器报告映射。
        markdown_text: 已渲染的 Markdown 文本。
        json_path: 可选 JSON 目标；None 时不写 JSON。
        markdown_path: 可选 Markdown 目标；None 时不写 Markdown。
    返回:
        无业务返回值；两个目标均成功替换才正常返回。
    异常:
        VgReportPublishError: 目标冲突、序列化失败或任一文件写出失败。
    """

    # 库调用两个目标都缺省时必须保持无副作用。
    if json_path is None and markdown_path is None:

        # 不因默认路径推断而回写当前项目。
        return

    # 同一解析路径会让第二次替换覆盖第一种报告格式。
    if json_path is not None and markdown_path is not None:

        # resolve 处理相对路径和 Windows 大小写别名。
        if json_path.resolve() == markdown_path.resolve():

            # 目标冲突属于调用错误，CLI 应返回 invocation/report exit 2。
            raise VgReportPublishError(
                "> ERR: [Python] JSON and Markdown report paths must differ."
            )

    # 先在不接触文件的阶段完成 JSON 序列化，避免输入错误产生部分报告。
    try:

        # ensure_ascii=False 保留中文诊断和示例原文。
        str_json_text = json.dumps(dict(report), indent=2, ensure_ascii=False) + "\n"  # 生成完整机器报告

    # 将 JSON 类型错误转换为统一的发布合同异常。
    except (TypeError, ValueError) as exc:

        # 序列化失败同样属于报告发布合同错误。
        raise VgReportPublishError(
            "> ERR: [Python] report JSON serialization failed."
        ) from exc

    # 按 Markdown 后 JSON 的顺序发布，保留部分发布证据。
    try:

        # Markdown 先发布，JSON 最后发布作为完整报告可用信号。
        if markdown_path is not None:

            # Markdown 失败时 JSON 尚未替换，保留旧报告一致性。
            _atomic_write_text(markdown_path, markdown_text)

        # JSON 替换成功代表机器报告也已完整落盘。
        if json_path is not None:

            # JSON 失败时已发布的 Markdown 保留为部分发布证据。
            _atomic_write_text(json_path, str_json_text)

    # 文件系统错误必须保留已成功替换的文件，便于诊断部分发布。
    except OSError as exc:

        # 保留底层异常链，供 CLI 记录具体 filesystem 原因。
        raise VgReportPublishError(
            "> ERR: [Python] report publication failed; partial report files may remain."
        ) from exc

# __all__ 公开 CLI 和库调用共用的发布入口。
__all__ = [  # 报告写出合同
    "VgReportPublishError",  # 报告发布异常
    "publish_vg_reports",  # 双格式报告发布入口
]
