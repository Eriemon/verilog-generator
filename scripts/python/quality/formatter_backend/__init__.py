"""汇总 Verilog formatter 后端的公开入口。"""

# 后端协议和默认实现保持旧导入路径兼容。
from .base import FormatterBackend
from .builtin_backend import BuiltinFormatterBackend
from .engine import VerilogFormatterEngine, VerilogFormatterError
from .factory import create_backend
from .format_routing import FormatRouteResult

# __all__ 明确暴露 formatter backend 的稳定 API 名称。
__all__ = [
    "FormatterBackend",  # 抽象后端协议
    "BuiltinFormatterBackend",  # 随包 formatter 默认实现
    "VerilogFormatterEngine",  # 具体解析和渲染引擎
    "VerilogFormatterError",  # formatter 层统一异常
    "FormatRouteResult",  # 路由阶段结果模型
    "create_backend",  # 后端工厂入口
]
