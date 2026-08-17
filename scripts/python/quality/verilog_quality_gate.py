#!/usr/bin/env python3
"""运行 readable Verilog 可读性质量门。"""

# future annotations 避免 argparse 类型提示在运行期求值。
from __future__ import annotations

# 标准库负责 CLI 参数、导入路径和脚本退出码。
import argparse
import sys
from pathlib import Path

# skill 根目录用于脚本模式导入 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # skill 主体根目录

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

    # JSON 报告路径可选，缺省与 Markdown 一起写入 reports/readable。
    parser.add_argument("--json", type=Path, help="JSON report path; defaults to reports/readable/quality_gate.json.")

    # Markdown 报告路径可选，用于归档人工可读结果。
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Markdown report path; defaults to reports/readable/quality_gate.md.",
    )

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

# _resolve_report_paths 为 CLI 提供稳定的双格式默认落盘位置。
def _resolve_report_paths(
    path_json: Path | None,
    path_markdown: Path | None,
) -> tuple[Path, Path]:
    """解析质量门 CLI 的 JSON/Markdown 报告路径。

    参数:
        path_json: 用户显式指定的 JSON 路径，可为空。
        path_markdown: 用户显式指定的 Markdown 路径，可为空。
    返回:
        解析后的 JSON 与 Markdown 路径元组。
    异常:
        ValueError: 两个路径解析后指向同一文件。
    """

    # cwd 是 CLI 默认输出边界，库调用不会经过此 helper。
    path_report_dir = Path.cwd() / "reports" / "readable"  # 质量门默认报告目录

    # JSON 缺省值固定为机器报告文件，避免每次运行推断不同路径。
    path_json_target = path_json or path_report_dir / "quality_gate.json"  # 最终 JSON 报告路径

    # 人工报告入口沿用机器报告根目录，便于一起归档和清理。
    path_markdown_target = path_markdown or path_report_dir / "quality_gate.md"  # 归档人工报告的最终目标

    # 同一路径会导致两种格式互相覆盖，必须在运行门禁前拒绝。
    if path_json_target.resolve() == path_markdown_target.resolve():

        # ValueError 由 CLI 转换为 invocation/report exit 2。
        raise ValueError(
            "> ERR: [Python] JSON and Markdown report paths must differ."
        )

    # 返回保留用户相对路径的目标，writer 会按需创建父目录。
    return path_json_target, path_markdown_target

# main 连接参数解析、质量门执行和退出码判定。
def main() -> int:
    """执行 Verilog 质量门 CLI。

    参数:
        本函数不接收业务参数，命令行参数由 argparse 从进程参数读取。

    返回:
        返回进程退出码；0 表示通过或 warn-only，1 表示质量门失败，2 表示调用/报告合同错误。
    """

    # 入口阶段才准备 runtime 导入路径，避免 import-time 副作用。
    _ensure_runtime_import_path()

    # runtime 质量门在路径准备后再导入。
    from scripts.python.quality.quality_gate import run_verilog_quality_gate, write_quality_gate_report
    from scripts.python.quality.quality_gate_common import ensure_runtime_visible_target_path
    from scripts.python.quality.vg_diagnostic_render import format_terminal_finding
    from scripts.python.quality.vg_diagnostics import VgDiagnosticContractError
    from scripts.python.quality.vg_report_publisher import VgReportPublishError

    # 解析命令行参数，保持 argparse 默认错误和退出语义。
    args = create_parser().parse_args()  # 命令行参数命名空间

    # 默认和显式参数都解析为双格式报告路径。
    try:

        # tuple_path_json/tuple_path_markdown 供 writer 和终端摘要复用。
        tuple_path_json, tuple_path_markdown = _resolve_report_paths(args.json, args.markdown)  # CLI 报告目标

    # 同路径属于命令调用错误，不进入 RTL 扫描。
    except ValueError as exc:

        # 固定错误前缀便于 Agent 识别调用合同失败。
        sys.stderr.write("> ERR: [Python] report path contract failed: {}\n".format(exc))

        # invocation/report 合同错误使用专用退出码。
        return 2

    # 写报告前先确认目标路径对当前运行宿主可见。
    try:

        # path_target 复用给后续质量门，避免重复路径归一化。
        path_target = ensure_runtime_visible_target_path(args.path)  # 通过入口预检的目标路径

    # 目标路径缺失时直接失败，不生成任何报告文件。
    except FileNotFoundError:

        # 终端使用固定错误前缀，保留“当前运行时可见路径”这条用户提示。
        sys.stderr.write(
            "> ERR: [Python] Target path precheck failed; "
            "use a path visible to the current Python runtime.\n"
        )

        # 路径前置条件不满足时返回 invocation 失败。
        return 2

    # 核心执行、结构化诊断校验和双格式报告发布统一映射合同错误。
    try:

        # 核心质量门返回包含 v3 findings 的报告对象。
        report = run_verilog_quality_gate(  # Verilog 质量门报告
            path_target,  # 待检查 RTL 路径
            strict=not args.non_strict,  # 是否启用严格检查
            comment_language=args.comment_language,  # 期望注释语言
            formatter_profile=args.formatter_profile,  # formatter-AST 配置档位
            include_testbench=args.include_testbench,  # 是否纳入 testbench 检查
            vitis_wrapper=args.vitis_wrapper,  # 是否按 Vitis wrapper ABI 放宽端口
        )

        # CLI 总是发布双格式报告，库入口仍保持 no-path 无副作用。
        write_quality_gate_report(
            report,
            json_path=tuple_path_json,
            markdown_path=tuple_path_markdown,
        )

    # 统一捕获诊断合同、报告写出和文件系统错误。
    except (VgDiagnosticContractError, VgReportPublishError, OSError, ValueError) as exc:

        # 诊断或报告合同错误不应伪装成 RTL 违规。
        sys.stderr.write("> ERR: [Python] report publication failed: {}\n".format(exc))

        # invocation/report/contract 错误使用退出码 2。
        return 2

    # 终端逐条打印紧凑 finding，完整指导和示例留在 Markdown/JSON。
    dict_report = report.to_dict()  # 机器报告字典

    # 每个 finding 都使用带 Python 前缀的人工可读摘要。
    for dict_finding in dict_report.get("findings", []):

        # 终端行包含规则、定位、问题、修改指令和报告追溯路径。
        str_terminal_finding = format_terminal_finding(dict_finding, tuple_path_json)  # 单条终端诊断

        # 输出保留完整问题、定位和修复指令，但不刷完整 JSON。
        print("> INFO: [Python] VG finding: {}".format(str_terminal_finding))

    # 无论是否有 finding，都回显两个最终报告路径。
    str_json_output = str(tuple_path_json)  # 终端摘要中的机器输出路径

    # 人工报告路径单独命名，避免和机器输出混淆。
    str_markdown_output = str(tuple_path_markdown)  # 终端摘要中的人工输出路径

    # JSON 路径摘要用于 Agent 追溯机器报告。
    print("> INFO: [Python] quality JSON report: {}".format(str_json_output))

    # Markdown 路径摘要用于 Agent 查看完整修复指导。
    print("> INFO: [Python] quality Markdown report: {}".format(str_markdown_output))

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
