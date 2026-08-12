"""集中定义 Verilog 时钟名称的下划线语义段识别规则。"""

# 推迟类型标注求值，保持质量模块导入边界稳定。
from __future__ import annotations

# 正则表达式只处理已经抽取出的 Verilog 标识符名称。
import re

# 时钟词干覆盖通用、AXI、AHB 和 APB 常见命名。
CLOCK_NAME_PATTERN = re.compile(  # 完整时钟语义段名称模式
    r"(?:^|_)(?:clk|clock|aclk|hclk|pclk)(?=_|$)",  # 时钟段前后必须是下划线边界或名称边界
    flags=re.IGNORECASE,  # Verilog 标识符角色不依赖大小写风格
)

# 单个标识符只要包含完整时钟段就具有 clock 角色。
def is_clock_name(str_name: str) -> bool:
    """判断标识符是否包含完整的时钟语义段。

    参数:
        str_name: 待判断的 Verilog 标识符。
    返回:
        名称包含受控 clock 语义段时返回 True。
    """

    # search 允许时钟段出现在任意下划线分隔位置。
    return CLOCK_NAME_PATTERN.search(str_name) is not None
