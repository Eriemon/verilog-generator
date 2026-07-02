"""提供随包内置 Verilog formatter 后端类型。"""

# 延迟类型注解求值，保持后端导入轻量
from __future__ import annotations

# 后端接口与本地模板驱动引擎共同组成内置实现
from .base import FormatterBackend
from .engine import VerilogFormatterEngine

# 内置后端复用本地引擎，并显式声明符合 FormatterBackend 协议
class BuiltinFormatterBackend(VerilogFormatterEngine, FormatterBackend):
    """基于本地模板驱动引擎的 formatter 后端。"""
