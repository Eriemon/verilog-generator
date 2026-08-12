"""读取 Verilog 源文件文本的编码辅助函数。"""

# 延迟类型注解求值，避免运行时额外类型处理
from __future__ import annotations

# 标准库路径类型用于读取本地 RTL 文件
from pathlib import Path

# 读取顺序优先覆盖 UTF-8/BOM，再兼容常见中文 Windows 编码
READ_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1")  # RTL 文本候选编码顺序

# 源文件读取需要兼容用户项目中的多种历史编码
def read_verilog_text(path: Path) -> str:
    """
    按候选编码读取 Verilog 文本。

    :param path: 待读取的 RTL 源文件路径。
    :return: 解码后的源文件文本。
    :raises OSError: 底层文件读取失败时由 pathlib 原样抛出。
    """

    # 按稳定顺序尝试常见编码，避免直接 fallback 掩盖正确 UTF-8 文件
    for encoding in READ_ENCODINGS:

        # 单个编码失败时继续尝试下一种候选编码
        try:

            # 命中可解码编码后立即返回文本，保持原有读取语义
            return path.read_text(encoding=encoding)

        # 只吞掉解码错误，文件不存在等 IO 错误继续向外传播
        except UnicodeDecodeError as exc:

            # 继续尝试后续候选编码，latin-1 兜底通常会结束循环
            continue

    # 理论上候选列表非空，此兜底维持旧行为并避免空候选时崩溃
    return path.read_text(encoding="utf-8", errors="ignore")
