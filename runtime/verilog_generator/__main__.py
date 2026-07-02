"""`python -m verilog_generator` 的命令行入口。"""

# 复用主 CLI 入口，避免模块执行路径复制参数解析逻辑
from .cli import main

# 仅在模块被直接执行时启动命令行流程
if __name__ == "__main__":

    # 把 CLI 返回码转换为 Python 进程退出码
    raise SystemExit(main())

