"""Verilog-2001 RTL 生成工作流的 runtime 包入口。"""

# 暴露调用方可稳定依赖的包级符号
__all__ = ["__version__"]  # 包级公开符号列表

# 标记当前源码迁移包的语义版本
__version__ = "0.3.6"  # erie-verilog-generator 当前版本
