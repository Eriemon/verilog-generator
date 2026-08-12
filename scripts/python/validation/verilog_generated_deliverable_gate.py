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

    # 注释语言策略传给 quality gate。
    parser.add_argument("--comment-language", choices=("zh", "en"), default="zh")

    # formatter profile 默认使用 normalize 交付配置。
    parser.add_argument("--formatter-profile", default="formatter-normalize")

    # testbench 默认不进入最终 RTL 交付门禁。
    parser.add_argument("--include-testbench", action="store_true")

    # Vitis wrapper 模式放宽 ABI 顶层端口命名。
    parser.add_argument("--vitis-wrapper", action="store_true")

    # JSON 报告供 CI 或自动化流程消费。
    parser.add_argument("--json", type=Path, help="Optional JSON report path.")

    # Markdown 报告供人工审查交付门禁结果。
    parser.add_argument("--markdown", type=Path, help="Optional Markdown report path.")

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

# _default_markdown_report_path 返回治理批准的默认 Markdown 报告路径。
def _default_markdown_report_path() -> Path:
    """返回默认 Markdown 报告路径。

    参数:
        本函数不接收业务参数。
    返回:
        返回当前布局下治理批准的默认 Markdown 报告路径。
    """

    # project_root 根据源码仓库或安装态布局选择安全的项目根目录。
    from scripts.python.workflow.config import project_root

    # path_reports_dir 汇总默认人工报告的受管目录。
    path_reports_dir = project_root() / "reports" / "readable"  # 默认 Markdown 报告目录

    # 返回固定的默认 Markdown 报告文件路径。
    return path_reports_dir / "deliverable_gate.md"

# main 连接参数解析、门禁执行、报告写出和退出码。
def main() -> int:
    """执行 Verilog 交付门禁 CLI。

    参数:
        本函数不接收业务参数，命令行参数由 argparse 读取。
    返回:
        返回进程退出码；0 表示可交付或 warn-only，1 表示门禁阻断。
    """

    # 脚本入口阶段再准备 runtime 导入路径。
    _ensure_runtime_import_path()

    # runtime 模块在导入路径准备后再导入。
    from scripts.python.quality.deliverable_gate import (
        run_verilog_deliverable_gate,
        write_verilog_deliverable_gate_report,
    )
    from scripts.python.quality.quality_gate_common import ensure_runtime_visible_target_path

    # 解析命令行参数。
    args = create_parser().parse_args()  # 命令行参数命名空间

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

        # 缺目标时直接返回失败。
        return 1

    # 规格只在调用方显式提供时读取，缺省保持纯 RTL 审查模式。
    dict_spec = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec is not None else None  # 可选 VG 规格合同

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

    # Markdown 默认写入治理批准的 reports 目录，避免污染仓库根或安装根的同级目录。
    path_markdown = args.markdown or _default_markdown_report_path()  # 默认 Markdown 报告路径

    # 按需写出 JSON 和 Markdown。
    write_verilog_deliverable_gate_report(dict_report, json_path=args.json, markdown_path=path_markdown)

    # 标量摘要避免把完整报告对象直接送到终端。
    bool_delivery_ready = bool(dict_report["delivery_ready"])  # 交付门禁通过状态

    # error 数量单独取出，便于 print 规则识别为短状态值。
    int_error_count = int(dict_report["errors"])  # 阻断 error 数量

    # strict warning 数量单独取出，避免输出结构化报告内容。
    int_strict_warning_count = int(dict_report["strict_warnings"])  # strict 模式待修复警告数量

    # 打印短摘要，避免终端刷出完整报告。
    print(
        "> INFO: [Python] Verilog deliverable gate finished; "
        f"delivery_ready={bool_delivery_ready} "
        f"errors={int_error_count} strict_warnings={int_strict_warning_count}."
    )

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
