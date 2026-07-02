#!/usr/bin/env python3
"""对单个 Verilog RTL 或 testbench 文件运行 Erie 静态 lint。"""

# future annotations 避免 CLI 类型提示在运行时提前求值。
from __future__ import annotations

# 标准库负责参数解析、外部命令调用、临时目录和脚本导入路径。
import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# skill 根目录用于脚本直接运行时定位 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[1]  # runtime 包所在目录

# lint 模式名称是公开 CLI choices，必须保持兼容。
MODE_RTL = "rtl"  # 普通 RTL lint 模式

# testbench 模式会把临时文件命名成旧脚本约定的 tb 目标。
MODE_TB = "tb"  # testbench 临时 lint 模式

# 外部工具命令保持旧脚本名称和参数顺序，避免破坏用户已有 wrapper。
DICT_EXTERNAL_LINT_COMMANDS_BY_TOOL = {  # verible/verilator/slang 的可执行入口与 lint-only 开关供 auto 探测和实际运行共用
    "verible": ("verible-verilog-lint",),  # auto 分支优先尝试的 Verible 可执行入口
    "verilator": ("verilator", "--lint-only"),  # 指定 verilator 时追加 lint-only 开关
    "slang": ("slang", "--lint-only"),  # Slang 命令同样以 lint-only 模式读取目标文件
}
@dataclass(frozen=True)
class ExternalFinding:
    """记录一次外部 lint 工具运行得到的用户可见结果。"""

    # severity 直接映射到输出中的方括号级别。
    str_severity: str  # 输出严重级别

    # tool 保留 CLI 选择名，而不是可执行文件名。
    str_tool: str  # 外部工具标识

    # message 是压缩后的单行输出摘要。
    str_message: str  # 外部工具摘要文本

# _ensure_runtime_import_path 只在需要内部 lint 时准备 runtime 导入路径。
def _ensure_runtime_import_path() -> None:
    """
    确保脚本从任意工作目录运行时可以导入 runtime 包。

    :param: 此辅助函数没有外部业务参数。
    :return: 不返回业务值；必要时只更新当前进程的 import 搜索路径。
    """

    # sys.path 使用字符串路径进行查重。
    str_skill_root = str(PATH_SKILL_ROOT)  # skill 根目录文本

    # 已经存在时不重复改变模块搜索顺序。
    if str_skill_root not in sys.path:

        # runtime 包必须位于 import 搜索路径前端。
        sys.path.insert(0, str_skill_root)

# build_parser 只维护公开 CLI 参数，不触碰文件系统。
def build_parser() -> argparse.ArgumentParser:
    """
    创建 Verilog lint CLI 参数解析器。

    :return: 已注册旧版命令行参数合同的 argparse 解析器。
    """

    # 描述文本保持英文，延续旧 help 输出风格。
    str_description = "Run Erie static lint on a Verilog file."  # argparse 描述文本

    # parser 只声明命令行合同，不读取文件或运行工具。
    parser = argparse.ArgumentParser(description=str_description)  # lint 参数解析器

    # 位置参数保持 file 名称和 Path 类型。
    parser.add_argument("file", type=Path, help="RTL or testbench file to lint.")

    # --mode choices 保持 rtl/tb 两种公开模式。
    parser.add_argument("--mode", choices=(MODE_RTL, MODE_TB), default=MODE_RTL)

    # --external 支持 none/auto/具体工具，默认仍为 none。
    tuple_external_choices = ("none", "auto", *DICT_EXTERNAL_LINT_COMMANDS_BY_TOOL.keys())  # external 参数可选值

    # external 参数注册保持旧 choices 和默认值。
    parser.add_argument("--external", choices=tuple_external_choices, default="none")

    # 返回完整解析器给 main 使用。
    return parser

# main 串联输入读取、内部 lint、外部 lint 和报告输出。
def main(argv: list[str] | None = None) -> int:
    """
    执行单文件 lint 并返回旧脚本兼容的退出码。

    :param argv: 可选命令行参数列表；为 None 时由 argparse 读取真实命令行。
    :return: 0 表示无问题，1 表示仅有 warning，2 表示输入或 lint error。
    """

    # argparse 保持原有错误处理和 SystemExit 行为。
    namespace_args: argparse.Namespace = build_parser().parse_args(argv)  # lint 命令行参数

    # 绝对路径用于错误提示和 lint 目标展示。
    path_source = namespace_args.file.resolve()  # 待检查 Verilog 文件

    # 读取编码提示放到标准 INFO 前缀下，便于 current-project 日志解析。
    print("> INFO: [Python] Encoding: UTF-8")

    # 文件不存在时保持原有错误文本和退出码 2。
    if not path_source.is_file():

        # stderr 输出便于 shell 调用方识别硬错误。
        print(f"> ERR: [Python] file not found: {path_source}", file=sys.stderr)

        # 退出码 2 表示输入文件不可用。
        return 2

    # 读取源文件文本，内部 lint 只接收 UTF-8 字符串。
    try:

        # 显式 UTF-8 与脚本首行输出保持一致。
        str_text = path_source.read_text(encoding="utf-8")  # Verilog 源文件文本

    # 解码错误会让内置 lint 无法得到可靠源码。
    except UnicodeDecodeError as exc:

        # 解码失败保持旧脚本错误文本格式。
        print(f"> ERR: [Python] failed to read {path_source} with UTF-8: {exc}", file=sys.stderr)

        # 退出码 2 表示输入文件无法读取。
        return 2

    # Erie 内置 lint 负责项目自有静态规则。
    list_issues = run_internal_lint(path_source.name, str_text, namespace_args.mode)  # 内置 lint 问题列表

    # 外部 lint 根据 --external 选择运行。
    list_external_findings = run_external_lint(path_source, namespace_args.external)  # 外部工具结果列表

    # 报告函数集中维护输出顺序和退出码。
    return report_findings(path_source, list_issues, list_external_findings)

# run_internal_lint 用临时目录适配 runtime 的生成物扫描接口。
def run_internal_lint(str_filename: str, str_text: str, str_mode: str) -> list[Any]:
    """
    在临时目录中运行 Erie 内置 RTL lint。

    :param str_filename: 展示给内置 lint metadata 的原始文件名。
    :param str_text: 待检查 Verilog 源码文本，按 UTF-8 读取。
    :param str_mode: lint 模式，取值为普通 RTL 或 testbench。
    :return: runtime 内置 lint 返回的 issue 对象列表。
    """

    # runtime 导入延迟到函数内，避免导入脚本时改 sys.path。
    _ensure_runtime_import_path()

    # runtime static_lint 是内置 lint 的唯一实现来源。
    from runtime.verilog_generator.static_lint import lint_generated_rtl

    # 临时目录隔离待检查文件名，避免污染源文件目录。
    with tempfile.TemporaryDirectory(prefix="erie-lint-") as str_temp_dir:

        # Path 对象便于写入临时 lint 目标。
        path_temp_root = Path(str_temp_dir)  # 临时 lint 根目录

        # testbench 模式沿用旧脚本的文件名约定。
        str_temp_name = "lint_target_tb.v" if str_mode == MODE_TB else "lint_target.v"  # 临时 Verilog 文件名

        # 内置 lint 会扫描临时根目录中的生成文件。
        path_temp = path_temp_root / str_temp_name  # 临时 Verilog 文件路径

        # 写入用户文件文本，保持 UTF-8。
        path_temp.write_text(str_text, encoding="utf-8")

        # metadata 只提供 lint 所需的最小接口形状。
        dict_design = {"name": str_filename, "interfaces": {"ports": []}}  # 内置 lint 设计元数据

        # 返回 runtime 原生 issue 对象列表。
        return lint_generated_rtl(dict_design, path_temp_root)

# select_external_tools 保持 none/auto/指定工具三种选择语义。
def select_external_tools(str_selection: str) -> list[str]:
    """
    根据 --external 参数选择需要运行的外部 lint 工具。

    :param str_selection: CLI 中的 external 选择值。
    :return: 需要按顺序尝试运行的外部工具名称列表。
    """

    # none 明确表示不运行任何外部工具。
    if str_selection == "none":

        # 保持旧脚本对 none 的空列表语义。
        return []

    # auto 选择第一个已安装的受支持工具。
    if str_selection == "auto":

        # 遍历顺序保持 verible、verilator、slang。
        for str_tool in ("verible", "verilator", "slang"):

            # 只要 PATH 中存在对应可执行文件，就选择该工具。
            if shutil.which(DICT_EXTERNAL_LINT_COMMANDS_BY_TOOL[str_tool][0]):

                # auto 只运行第一个可用工具。
                return [str_tool]

        # 没有外部工具时 auto 静默退化为空列表。
        return []

    # 具体工具名由 argparse choices 保证合法。
    return [str_selection]

# run_external_lint 负责把外部工具退出码转换成统一 finding。
def run_external_lint(path_source: Path, str_selection: str) -> list[ExternalFinding]:
    """
    运行调用方选择的外部 Verilog lint 工具。

    :param path_source: 外部工具需要读取的 Verilog 文件路径。
    :param str_selection: CLI 中的 external 选择值。
    :return: 已压缩为单行摘要的外部工具结果列表。
    """

    # finding 列表用于统一输出外部工具信息和 warning。
    list_findings: list[ExternalFinding] = []  # 外部 lint 结果列表

    # 每个工具独立检查安装状态和执行结果。
    for str_tool in select_external_tools(str_selection):

        # 工具命令的第一个元素是可执行文件名。
        str_binary = DICT_EXTERNAL_LINT_COMMANDS_BY_TOOL[str_tool][0]  # 外部工具可执行文件名

        # 未安装时保持 warning 级别，不让 subprocess 抛错。
        if shutil.which(str_binary) is None:

            # 兼容旧输出中的提示文本。
            list_findings.append(ExternalFinding("warning", str_tool, f"{str_tool} is not installed."))

            # 缺工具时跳过该工具。
            continue

        # 命令参数顺序沿用 EXTERNAL_TOOLS 配置。
        list_command = [*DICT_EXTERNAL_LINT_COMMANDS_BY_TOOL[str_tool], str(path_source)]  # 外部 lint 命令行

        # subprocess 捕获 stdout/stderr，保证脚本输出顺序可控。
        completed_process = subprocess.run(  # 外部 lint 进程结果
            list_command,  # 外部 lint 命令和目标文件路径
            capture_output=True,  # 捕获 stdout/stderr 供摘要使用
            text=True,  # 以文本模式读取工具输出
            encoding="utf-8",  # 外部工具输出按 UTF-8 解码
            errors="replace",  # 非 UTF-8 字节用替代字符保留
            check=False,  # 非零退出码转为 warning 而不是异常
        )

        # returncode 为 0 时报告 info，不计 warning。
        if completed_process.returncode == 0:

            # 成功文本保持旧脚本格式。
            list_findings.append(ExternalFinding("info", str_tool, f"{str_tool} completed with no reported issues."))

            # 成功工具无需再解析输出。
            continue

        # 失败输出压缩成单行摘要，避免污染报告。
        str_snippet = _external_output_snippet(completed_process.stdout, completed_process.stderr)  # 外部工具输出摘要

        # 非零退出码按 warning 汇总。
        list_findings.append(ExternalFinding("warning", str_tool, f"{str_tool} reported issues: {str_snippet}"))

    # 返回所有外部工具结果供报告阶段处理。
    return list_findings

# _external_output_snippet 将工具输出限制为报告中的一行摘要。
def _external_output_snippet(str_stdout: str, str_stderr: str) -> str:
    """
    压缩外部 lint 输出，生成单行摘要。

    :param str_stdout: 外部工具标准输出文本。
    :param str_stderr: 外部工具标准错误文本。
    :return: 最多 400 字符的单行诊断摘要。
    """

    # stdout 和 stderr 合并后才能保留工具完整诊断。
    str_combined = (str_stdout + "\n" + str_stderr).strip().replace("\r", " ")  # 合并后的外部输出

    # split/join 将换行和多空白压成单空格。
    str_single_line = " ".join(str_combined.split())  # 单行外部输出

    # 400 字符截断沿用旧脚本上限。
    return str_single_line[:400]

# report_findings 是唯一负责 stdout 报告和退出码映射的函数。
def report_findings(path_source: Path, list_issues: list[Any], list_external_findings: list[ExternalFinding]) -> int:
    """
    打印 lint 报告并返回兼容旧脚本的退出码。

    :param path_source: 当前 lint 目标文件路径。
    :param list_issues: Erie 内置 lint 产生的问题对象。
    :param list_external_findings: 外部 lint 工具产生的摘要结果。
    :return: 0 表示通过，1 表示 warning，2 表示 error。
    """

    # 错误计数决定最终是否返回 2。
    int_errors = 0  # 内置 lint error 数量

    # warning 计数决定无 error 时是否返回 1。
    int_warnings = 0  # 内置和外部 warning 总数

    # 目标路径输出使用标准 INFO 前缀，同时保留 Lint target 字段文本。
    print(f"> INFO: [Python] Lint target: {path_source}")

    # 内置 lint issue 按 runtime 返回顺序输出。
    for lint_issue in list_issues:

        # 输出格式保持 severity/code/line/path/message 顺序。
        print(
            f"> INFO: [Python] [{lint_issue.severity.upper()}] [{lint_issue.code}] "
            f"line={lint_issue.line} path={lint_issue.path} {lint_issue.message}"
        )

        # error 级别映射到退出码 2。
        if lint_issue.severity == "error":

            # 将内置 lint 的 error 计入最高失败等级。
            int_errors += 1  # 内置 lint error 计数

        # warning 级别不会阻断 error 统计，但会让脚本返回 1。
        elif lint_issue.severity == "warning":

            # 记录内置规则发现的可维护性或风格风险。
            int_warnings += 1  # 内置 lint 非阻断发现数量

    # 外部工具结果只会产生 info 或 warning。
    for external_finding in list_external_findings:

        # 外部结果也纳入标准 INFO 前缀，便于日志聚合工具识别。
        print(
            f"> INFO: [Python] [{external_finding.str_severity.upper()}] "  # 外部结果严重级别前缀
            f"[external:{external_finding.str_tool}] {external_finding.str_message}"  # 工具名和摘要文本
        )

        # 外部 warning 计入总 warning 数。
        if external_finding.str_severity == "warning":

            # 累加外部工具 warning。
            int_warnings += 1  # 外部 lint warning 计数

    # Summary 文本保留旧字段和复数写法，只增加标准 INFO 前缀。
    print(f"> INFO: [Python] Summary: {int_errors} error(s), {int_warnings} warning(s)")

    # 存在 error 时优先返回 2。
    if int_errors:

        # error 是 lint 的最高失败等级。
        return 2

    # 没有 error 但有 warning 时返回 1。
    if int_warnings:

        # warning 表示 lint 需要人工关注。
        return 1

    # 无 error/warning 时 lint 通过。
    return 0

# 脚本直运行时将 main 返回码交给 shell。
if __name__ == "__main__":

    # SystemExit 保留 CLI 退出语义。
    raise SystemExit(main())
