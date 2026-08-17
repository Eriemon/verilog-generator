#!/usr/bin/env python3
"""运行 readable Verilog 最终交付门禁。"""

# 延迟类型注解解析，保持 CLI 启动时的导入成本稳定。
from __future__ import annotations

# argparse、json、sys 和 Path 共同完成参数解析、规格读取和退出码处理。
import argparse
import json
import sys
from pathlib import Path

# 脚本位于 scripts 目录，上一层就是可加入 sys.path 的 skill 包根。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # CLI 直运行时的 skill 包根

# create_parser 创建脚本参数解析器。
def create_parser() -> argparse.ArgumentParser:
    """创建 Verilog 交付门禁 CLI 参数解析器。

    参数:
        本函数不接收业务参数，直接构造 argparse 解析器。
    返回:
        返回已注册所有命令行参数的 ArgumentParser。
    """

    # str_description 说明脚本用途。
    str_description = "Run final readable Verilog deliverable gate for generated or modified RTL."  # argparse 描述文本

    # parser 保存全部 CLI 参数定义。
    parser = argparse.ArgumentParser(description=str_description)  # 交付门禁命令解析器

    # 位置参数指定待检查的 Verilog 文件或目录。
    parser.add_argument("path", type=Path, help="Generated Verilog file or directory to check.")

    # 非严格模式用于审查历史样例，不用于最终交付。
    parser.add_argument("--non-strict", action="store_true", help="Downgrade strict warnings for exploration.")

    # 可选规格为位宽、时钟与接口类 VG 门禁提供合同事实。
    parser.add_argument("--spec", type=Path, help="Optional normalized Verilog spec JSON path.")

    # 外部接口 stub 可重复提供，且只进入 VG097 跨模块位宽事实。
    parser.add_argument(
        "--external-interface-source",
        action="append",
        default=[],
        type=Path,
        help="Verilog stub file or directory used only by VG097; repeatable.",
    )

    # 注释语言策略传给 quality gate。
    parser.add_argument("--comment-language", choices=("zh", "en"), default="zh")

    # formatter profile 默认使用 normalize 交付配置。
    parser.add_argument("--formatter-profile", default="formatter-normalize")

    # testbench 默认不进入最终 RTL 交付门禁。
    parser.add_argument("--include-testbench", action="store_true")

    # Vitis wrapper 模式放宽 ABI 顶层端口命名。
    parser.add_argument("--vitis-wrapper", action="store_true")

    # JSON 报告供 CI 或自动化流程消费，缺省写入 reports/readable。
    parser.add_argument(
        "--json",
        type=Path,
        help="JSON report path; defaults to reports/readable/deliverable_gate.json.",
    )

    # Markdown 报告供人工审查交付门禁结果。
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Markdown report path; defaults to reports/readable/deliverable_gate.md.",
    )

    # warn-only 用于收集报告但不阻断进程。
    parser.add_argument("--warn-only", action="store_true", help="Return 0 even when the deliverable gate fails.")

    # 返回解析器。
    return parser

# _ensure_runtime_import_path 准备 runtime 包导入。
def _ensure_runtime_import_path() -> None:
    """确保脚本直运行时可以导入 skill runtime 包。

    参数:
        本函数不接收业务参数。
    返回:
        本函数只调整解释器导入路径，不返回业务值。
    """

    # 禁止脚本运行产生 __pycache__，保持 skill 源目录整洁。
    sys.dont_write_bytecode = True  # 禁止写入 pyc 文件

    # str_skill_root 用于 sys.path 成员判断。
    str_skill_root = str(PATH_SKILL_ROOT)  # skill 根目录文本

    # 只在缺少 skill 根目录时插入。
    if str_skill_root not in sys.path:

        # runtime 包位于 skill 根目录下。
        sys.path.insert(0, str_skill_root)

# _resolve_report_paths 返回调用方工作目录中的双格式报告路径。
def _resolve_report_paths(
    path_json: Path | None,
    path_markdown: Path | None,
) -> tuple[Path, Path]:
    """解析交付门禁 CLI 的 JSON/Markdown 报告路径。

    参数:
        path_json: 用户显式指定的 JSON 路径，可为空。
        path_markdown: 用户显式指定的 Markdown 路径，可为空。
    返回:
        返回解析后的 JSON 与 Markdown 路径元组。
    异常:
        ValueError: 两个路径解析后指向同一文件。
    """

    # 默认报告属于调用方项目，不能回写只读的 source 或 installed skill 根。
    path_reports_dir = Path.cwd() / "reports" / "readable"  # 调用方报告目录

    # 机器报告和人工报告分别使用稳定文件名。
    path_json_target = path_json or path_reports_dir / "deliverable_gate.json"  # 机器报告目标

    # 人工报告目标与机器报告同目录但使用独立扩展名。
    path_markdown_target = path_markdown or path_reports_dir / "deliverable_gate.md"  # 人工报告目标

    # 同一路径会让第二种格式覆盖第一种格式。
    if path_json_target.resolve() == path_markdown_target.resolve():

        # ValueError 由 CLI 转换为 invocation/report exit 2。
        raise ValueError(
            "> ERR: [Python] JSON and Markdown report paths must differ."
        )

    # 返回双格式目标，writer 会负责创建父目录和原子替换。
    return path_json_target, path_markdown_target

# main 连接参数解析、门禁执行、报告写出和退出码。
def main() -> int:
    """执行 Verilog 交付门禁 CLI。

    参数:
        本函数不接收业务参数，命令行参数由 argparse 读取。
    返回:
        返回进程退出码；0 表示可交付或 warn-only，1 表示门禁阻断，2 表示调用/报告合同错误。
    """

    # 脚本入口阶段再准备 runtime 导入路径。
    _ensure_runtime_import_path()

    # runtime 模块在导入路径准备后再导入。
    from scripts.python.quality.deliverable_gate import (
        run_verilog_deliverable_gate,
        write_verilog_deliverable_gate_report,
    )

    # 外部接口来源键由质量门 facade 统一声明。
    from scripts.python.quality.quality_gate import EXTERNAL_INTERFACE_SOURCES_SPEC_KEY

    # 入口路径预检复用质量门的运行时可见性规则。
    from scripts.python.quality.quality_gate_common import ensure_runtime_visible_target_path

    # 终端 finding 使用与质量门相同的 v3 renderer。
    from scripts.python.quality.vg_diagnostic_render import format_terminal_finding

    # 诊断和报告合同错误需要区分于 RTL 违规退出码。
    from scripts.python.quality.vg_diagnostics import VgDiagnosticContractError

    # 报告发布异常用于返回 invocation/report exit 2。
    from scripts.python.quality.vg_report_publisher import VgReportPublishError

    # 解析命令行参数。
    args = create_parser().parse_args()  # 命令行参数命名空间

    # 默认和显式参数都解析为双格式报告路径。
    try:

        # tuple_path_json/tuple_path_markdown 供 writer 和终端摘要复用。
        tuple_path_json, tuple_path_markdown = _resolve_report_paths(args.json, args.markdown)  # CLI 报告目标

    # 同路径属于命令调用错误，不进入 RTL 扫描。
    except ValueError as exc:

        # 固定错误前缀便于 Agent 识别调用合同失败。
        sys.stderr.write("> ERR: [Python] report path contract failed: {}\n".format(exc))

        # invocation/report 合同错误使用退出码 2。
        return 2

    # 写报告前先确认目标路径对当前运行宿主可见。
    try:

        # path_target 复用给交付门禁，避免重复路径归一化。
        path_target = ensure_runtime_visible_target_path(args.path)  # 通过入口预检的目标路径

    # 目标路径不满足前置条件时直接失败，不写任何报告。
    except FileNotFoundError:

        # 终端使用固定错误前缀，保留“当前运行时可见路径”这条用户提示。
        sys.stderr.write(
            "> ERR: [Python] Target path precheck failed; "
            "use a path visible to the current Python runtime.\n"
        )

        # 缺目标时直接返回 invocation 失败。
        return 2

    # 规格、门禁执行和报告发布统一映射合同错误。
    try:

        # 规格只在调用方显式提供时读取，缺省保持纯 RTL 审查模式。
        dict_spec = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec is not None else None  # 可选 VG 规格合同

        # 外部接口来源通过运行时专用键进入质量门，并在事实构建前从设计规格中剥离。
        if args.external_interface_source:

            # 复制可选规格后再注入路径，避免改变已读取 JSON 对象的知识语义。
            dict_spec = dict(dict_spec or {})  # 包含本轮运行时来源的规格载体

            # 专用键只在质量门入口存活，随后会与设计规格分离。
            dict_spec[EXTERNAL_INTERFACE_SOURCES_SPEC_KEY] = tuple(args.external_interface_source)  # 外部接口路径

        # 执行最终交付门禁。
        dict_report = run_verilog_deliverable_gate(  # 保存完整诊断、摘要计数和退出码依据
            path_target,  # 用户指定的 RTL 文件或目录
            spec=dict_spec,  # VG 门禁使用的归一化规格

            # 以下选项定义交付严格度、注释策略和扫描边界。
            strict=not args.non_strict,  # strict 模式默认开启
            comment_language=args.comment_language,  # 注释语言策略
            formatter_profile=args.formatter_profile,  # formatter 抽象语法树配置名称
            include_testbench=args.include_testbench,  # 是否扫描 testbench
            vitis_wrapper=args.vitis_wrapper,  # Vitis wrapper 端口放宽开关
        )  # 交付门禁报告

        # CLI 总是发布双格式报告，库入口仍保持 no-path 无副作用。
        write_verilog_deliverable_gate_report(
            dict_report,
            json_path=tuple_path_json,
            markdown_path=tuple_path_markdown,
        )

    # 统一捕获诊断合同、报告写出和文件系统错误。
    except (VgDiagnosticContractError, VgReportPublishError, OSError, ValueError) as exc:

        # 诊断或报告合同错误不应伪装成 RTL 违规。
        sys.stderr.write("> ERR: [Python] report execution failed: {}\n".format(exc))

        # invocation/report/contract 错误使用退出码 2。
        return 2

    # 终端逐条打印紧凑 finding，完整指导和示例留在 Markdown/JSON。
    for dict_finding in dict_report.get("findings", []):

        # 终端行包含规则、定位、问题、修改指令和报告追溯路径。
        str_terminal_finding = format_terminal_finding(dict_finding, tuple_path_json)  # 单条终端诊断

        # 输出保留完整问题、定位和修复指令，但不刷完整 JSON。
        print("> INFO: [Python] VG finding: {}".format(str_terminal_finding))

    # 标量摘要避免把完整报告对象直接送到终端。
    bool_delivery_ready = bool(dict_report["delivery_ready"])  # 交付门禁通过状态

    # error 数量单独取出，便于 print 规则识别为短状态值。
    int_error_count = int(dict_report["errors"])  # 阻断 error 数量

    # strict warning 数量单独取出，避免输出结构化报告内容。
    int_strict_warning_count = int(dict_report["strict_warnings"])  # strict 模式待修复警告数量

    # 将 Path 转成纯文本，避免终端摘要被 tuple 命名规则误判为结构化载荷。
    str_json_output = str(tuple_path_json)  # 终端摘要中的机器输出路径

    # 人工报告路径单独命名，便于 Agent 打开修复指导。
    str_markdown_output = str(tuple_path_markdown)  # 终端摘要中的人工输出路径

    # 打印短摘要，避免终端刷出完整报告。
    print(
        "> INFO: [Python] Verilog deliverable gate finished; "
        f"delivery_ready={bool_delivery_ready} "
        f"errors={int_error_count} strict_warnings={int_strict_warning_count}."
    )

    # 机器报告路径摘要用于自动化追溯。
    print("> INFO: [Python] deliverable JSON report: {}".format(str_json_output))

    # Markdown 报告路径摘要用于人工查看示例和修复步骤。
    print("> INFO: [Python] deliverable Markdown report: {}".format(str_markdown_output))

    # warn-only 显式请求不阻断流程。
    if args.warn_only:

        # 调用方只收集报告。
        return 0

    # 不可交付时返回 1。
    return 0 if dict_report["delivery_ready"] else 1

# 脚本直运行时将 main 返回值转为进程退出码。
if __name__ == "__main__":

    # SystemExit 保留标准 CLI 退出行为。
    raise SystemExit(main())
