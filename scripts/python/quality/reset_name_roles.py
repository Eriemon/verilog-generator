"""集中定义 Verilog 复位名称的下划线语义段识别规则。"""

# 推迟类型标注求值，保持质量模块导入边界稳定。
from __future__ import annotations

# 正则表达式只处理已经抽取出的 Verilog 标识符名称。
import re

# 复位词干覆盖通用、异步、总线和清零命名，不包含置位角色。
RESET_STEM_PATTERN = r"(?:rst|reset|arst|areset|hrst|hreset|prst|srst|sreset|clear|clr)"  # 受控复位词干模式

# 完整复位语义段允许出现在标识符任意下划线分段位置。
RESET_ONLY_NAME_PATTERN = re.compile(  # 高有效或低有效复位名称模式
    rf"(?:^|_){RESET_STEM_PATTERN}(?:_?n)?(?=_|$)",  # 复位段前后必须是下划线边界或名称边界
    flags=re.IGNORECASE,  # Verilog 标识符角色不依赖大小写风格
)

# 低有效模式要求复位词干携带连写 n 或分离的 _n 极性后缀。
LOW_ACTIVE_RESET_NAME_PATTERN = re.compile(  # 低有效复位名称模式
    rf"(?:^|_){RESET_STEM_PATTERN}_?n(?=_|$)",  # 极性后缀结束后仍允许用途分段
    flags=re.IGNORECASE,  # 支持大写协议复位名称
)

# 单个标识符只要包含完整复位段就具有 reset-only 角色。
def is_reset_name(str_name: str) -> bool:
    """判断标识符是否包含完整的复位或清零语义段。

    参数:
        str_name: 待判断的 Verilog 标识符。
    返回:
        名称包含受控 reset-only 语义段时返回 True。
    """

    # search 允许复位段出现在任意下划线分隔位置。
    return RESET_ONLY_NAME_PATTERN.search(str_name) is not None

# 低有效判断复用相同分段边界，避免各入口维护后缀白名单。
def is_low_active_reset_name(str_name: str) -> bool:
    """判断标识符是否包含完整的低有效复位语义段。

    参数:
        str_name: 待判断的 Verilog 标识符。
    返回:
        名称包含带 n 极性后缀的完整复位段时返回 True。
    """

    # 后续用途后缀必须通过下划线形成新语义段。
    return LOW_ACTIVE_RESET_NAME_PATTERN.search(str_name) is not None
