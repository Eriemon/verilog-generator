#!/usr/bin/env python3
"""运行 Erie Verilog 可读性质量门。"""

# future annotations 避免 argparse 类型提示在运行期求值。
from __future__ import annotations

# 标准库负责 CLI 参数、导入路径和脚本退出码。
import argparse
import sys
from pathlib import Path

# skill 根目录用于脚本模式导入 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[1]  # skill 主体根目录

# create_parser 保持既有 CLI 参数合同。
def create_parser() -> argparse.ArgumentParser:
    """创建 Verilog 质量门脚本的参数解析器。

    参数:
        本函数不接收业务参数，直接构造脚本级 argparse 解析器。

    返回:
        返回已注册全部兼容参数的 argparse.ArgumentParser 实例。
    """

    # 描述文本保持英文，避免改变命令行帮助的用户可见语义。
    str_description = "Check Verilog RTL with Erie style, comment, naming, and formatter-AST gates."  # argparse 描述文本

    # parser 只负责注册既有参数，不触发运行时检查。
    parser = argparse.ArgumentParser(description=str_description)  # 质量门命令解析器

    # 位置参数指定待检查的 Verilog 文件或目录。
    parser.add_argument("path", type=Path, help="Verilog file or directory to check.")

    # 非严格模式用于分析历史参考代码。
    parser.add_argument(
        "--non-strict",
        action="store_true",
        help="Downgrade style/comment rules to warnings for legacy reference analysis.",
    )

    # 注释语言约束传给 runtime 质量门。
    parser.add_argument("--comment-language", choices=("zh", "en"), default="zh")

    # formatter profile 保持默认 normalize 策略。
    parser.add_argument("--formatter-profile", default="formatter-normalize")

    # testbench 检查需要用户显式打开。
    parser.add_argument("--include-testbench", action="store_true")

    # Vitis wrapper 模式保留 ABI 顶层端口命名。
    parser.add_argument("--vitis-wrapper", action="store_true", help="Preserve Vitis ABI top-level port names.")

    # JSON 报告路径可选，缺省只打印 Markdown。
    parser.add_argument("--json", type=Path, help="Optional JSON report path.")

    # Markdown 报告路径可选，用于归档人工可读结果。
    parser.add_argument("--markdown", type=Path, help="Optional Markdown report path.")

    # warn-only 保留 CI 探测场景的零退出码兼容。
    parser.add_argument("--warn-only", action="store_true", help="Always return 0 while still reporting findings.")

    # 返回已注册全部兼容参数的解析器。
    return parser

# _ensure_runtime_import_path 仅在脚本入口执行路径注入。
def _ensure_runtime_import_path() -> None:
    """确保从 skill scripts 目录运行时能够导入 runtime 包。

    参数:
        本函数不接收业务参数，直接读取当前脚本所在目录。

    返回:
        本函数只调整导入路径和 pyc 策略，不返回业务值。
    """

    # 禁止生成 pyc，避免脚本检查污染 skill 目录。
    sys.dont_write_bytecode = True  # 避免质量门脚本生成 __pycache__

    # skill 根目录字符串用于 sys.path 成员比较。
    str_skill_root = str(PATH_SKILL_ROOT)  # sys.path 中的 skill 根目录文本

    # 只在缺少 skill 根目录时插入，避免重复改变导入顺序。
    if str_skill_root not in sys.path:

        # 将 runtime 包所在目录放到导入路径最前。
        sys.path.insert(0, str_skill_root)

# main 连接参数解析、质量门执行和退出码判定。
def main() -> int:
    """执行 Verilog 质量门 CLI。

    参数:
        本函数不接收业务参数，命令行参数由 argparse 从进程参数读取。

    返回:
        返回进程退出码；0 表示通过或 warn-only，1 表示严格质量门失败。
    """

    # 入口阶段才准备 runtime 导入路径，避免 import-time 副作用。
    _ensure_runtime_import_path()

    # runtime 质量门在路径准备后再导入。
    from runtime.verilog_generator.quality_gate import run_verilog_quality_gate, write_quality_gate_report

    # 解析命令行参数，保持 argparse 默认错误和退出语义。
    args = create_parser().parse_args()  # 命令行参数命名空间

    # 核心质量门返回 Markdown、JSON 和退出状态所需报告对象。
    report = run_verilog_quality_gate(  # Verilog 质量门报告
        args.path,  # 待检查 RTL 路径
        strict=not args.non_strict,  # 是否启用严格检查
        comment_language=args.comment_language,  # 期望注释语言
        formatter_profile=args.formatter_profile,  # formatter-AST 配置档位
        include_testbench=args.include_testbench,  # 是否纳入 testbench 检查
        vitis_wrapper=args.vitis_wrapper,  # 是否按 Vitis wrapper ABI 放宽端口
    )

    # Markdown 报告默认落盘，避免终端直接输出整份结构化报告。
    path_markdown = args.markdown or Path("verilog_quality_gate.md")  # Markdown 报告写出路径

    # 可选写出 JSON 和 Markdown 报告文件。
    write_quality_gate_report(report, json_path=args.json, markdown_path=path_markdown)

    # 终端只报告摘要和报告位置，符合 current-project 输出边界。
    print("> INFO: [Python] Verilog quality gate finished; reports were written to disk.")  # 质量门运行摘要

    # warn-only 模式用于只收集报告、不阻断流水线。
    if args.warn_only:

        # 用户显式要求不因发现项失败。
        return 0

    # 严格模式下按报告 ok 状态映射退出码。
    return 1 if not report.ok() else 0

# 脚本直运行时将 main 返回值转为进程退出码。
if __name__ == "__main__":

    # SystemExit 保留 argparse 和 shell 的传统退出行为。
    raise SystemExit(main())
