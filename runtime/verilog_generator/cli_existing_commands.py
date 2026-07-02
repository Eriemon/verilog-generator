"""实现 existing-RTL 分析、受控修改、语义比较和 verify-repair CLI 子命令。"""

# future annotations 避免 argparse 类型提示在运行期求值。
from __future__ import annotations

# 标准库负责 argparse 命名空间与 JSON 输出。
import argparse
import json
import sys
from pathlib import Path

# integration facade 负责 representative corpus 的治理与摘要落盘。
from integration.verilog_adapter import run_verilog_cases as run_verilog_cases_runtime

# existing RTL 分析与受控修改入口保持在核心模块。
from .existing_rtl import analyze_existing_rtl, load_spec_text
from .existing_rtl_refinement import (
    RefineExistingRtlOptions,
    compare_semantics,
    refine_existing_rtl,
    require_refine_goal,
)

# verify-repair 主路径由 verify_repair 模块提供。
from .verify_repair import verify_existing

# CLI support 统一处理外部验证策略和状态记录。
from .cli_support import cli_run_external, record_state
from .workspace import require_workspace_path, require_write_path

# cmd_analyze_existing 对已有 RTL 项目生成结构化分析。
def cmd_analyze_existing(args: argparse.Namespace) -> int:
    """处理 analyze-existing 子命令。

    参数:
        args: argparse 解析出的 analyze-existing 命名空间。

    返回:
        分析成功时返回 0。
    """

    # source 列表中的每个 RTL 文件都必须存在。
    list_source_paths = _existing_source_paths(args.source)  # 已有 RTL 源文件路径列表

    # analysis 输出目录必须允许写入。
    path_output_dir = require_write_path(args.out_dir, purpose="analysis output directory")  # RTL 分析输出目录

    # spec text 可选提供用户意图和功能约束。
    str_spec_text = load_spec_text(args.spec_source) if args.spec_source else None  # 规格说明文本

    # analyze_existing_rtl 生成项目分析、模块分析和说明文档。
    dict_analysis_result = analyze_existing_rtl(  # 供后续 refine/verify 复用的分析索引
        list_source_paths,  # 待扫描的既有 RTL 文件集合
        spec_text=str_spec_text,  # 可选规格说明文本
        module_name=args.module_name,  # 用户指定或由分析器推断的顶层模块
        out_dir=path_output_dir,  # 写入项目报告和模块报告的目录
    )

    # CLI 摘要保持旧字段名，避免调用方破坏。
    dict_payload = {  # analyze-existing 命令行摘要
        "status": "analyzed",  # 分析完成状态
        "analysis_path": str(dict_analysis_result["analysis_path"]),  # 模块分析 JSON 路径
        "project_analysis_path": str(dict_analysis_result["project_analysis_path"]),  # 项目分析 JSON 路径
        "design_explanation_path": str(dict_analysis_result["design_explanation_path"]),  # 设计说明 Markdown 路径
    }

    # CLI JSON stdout 是外部调用方契约，不能改为普通日志前缀。
    sys.stdout.write(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n")

    # workflow-state 记录分析输入与主报告路径。
    record_state(
        args,
        "analyze_existing",
        {"source": list_source_paths, "output": dict_analysis_result["analysis_path"]},
    )

    # analyze-existing 成功即返回 0。
    return 0

# cmd_refine_existing 对单个 RTL 源文件生成受控修改辅助产物。
def cmd_refine_existing(args: argparse.Namespace) -> int:
    """处理 refine-existing 子命令。

    参数:
        args: argparse 解析出的 refine-existing 命名空间。

    返回:
        受控修改辅助产物生成成功时返回 0。
    """

    # source 输入是本次受控修改的主 RTL 文件。
    path_source = require_workspace_path(args.source, purpose="Verilog source path", must_exist=True)  # 受控修改 RTL 源文件

    # refinement 输出目录承载计划、wrapper 或辅助 testbench。
    path_output_dir = require_write_path(args.out_dir, purpose="refinement output directory")  # 受控修改输出目录

    # analysis 可选复用 analyze-existing 的结构化结果。
    path_analysis = _optional_workspace_path(args.analysis, "analysis path")  # 可选分析报告路径

    # spec_source 可选提供用户约束。
    path_spec_source = _optional_workspace_path(args.spec_source, "spec source")  # 可选规格说明路径

    # 把 refine-existing 的所有可选输入收敛成配置对象，避免入口参数继续拉长。
    refine_existing_rtl_options_config = RefineExistingRtlOptions(  # refine-existing 子命令的可选输入配置
        analysis_source=path_analysis,  # analyze-existing 产出的项目分析文件
        spec_source=path_spec_source,  # 用户提供的修改约束说明
        candidate_artifacts_dir=args.candidate_artifacts_dir,  # 候选产物目录
        reference_artifacts_dir=args.reference_artifacts_dir,  # 参考产物目录
        readiness=args.readiness,  # 验证 readiness 深度
        tb_language=getattr(args, "tb_language", "verilog"),  # 测试平台生成时采用的硬件描述语言边界
    )

    # refine_existing_rtl 按目标生成可审计辅助产物，不静默改写源文件。
    dict_refine_result = refine_existing_rtl(  # 受控修改模式产出的辅助工件索引
        path_source,  # 本次 refine 的主 RTL 文件
        out_dir=path_output_dir,  # 保存 refine 辅助产物的目录
        refine_goal=require_refine_goal(args.goal),  # 合并、优化或 testbench 辅助目标
        options=refine_existing_rtl_options_config,  # 所有可选 refine 输入都通过配置对象传递
    )

    # refine-existing 输出完整产物索引，供自动化流程直接解析。
    sys.stdout.write(json.dumps(dict_refine_result, indent=2, ensure_ascii=False) + "\n")

    # workflow-state 记录目标和输出目录。
    record_state(args, "refine_existing", {"source": path_source, "goal": args.goal, "output": path_output_dir})

    # 受控修改命令只负责产物生成，异常路径由下层抛出。
    return 0

# cmd_compare_semantics 比较参考 RTL 与候选 RTL 的语义证据。
def cmd_compare_semantics(args: argparse.Namespace) -> int:
    """处理 compare-semantics 子命令。

    参数:
        args: argparse 解析出的 compare-semantics 命名空间。

    返回:
        语义比较通过时返回 0，否则返回 1。
    """

    # reference RTL 是语义比较的黄金参考设计。
    path_reference = require_workspace_path(args.reference, purpose="reference RTL path", must_exist=True)  # 语义比较参考 RTL 路径

    # candidate RTL 是需要证明语义保持的修改后设计。
    path_candidate = require_workspace_path(args.candidate, purpose="candidate RTL path", must_exist=True)  # 待验证候选 RTL 路径

    # compare 输出目录承载 equivalence 和 QoR 报告。
    path_output_dir = require_write_path(args.out_dir, purpose="compare output directory")  # 语义比较输出目录

    # 外部验证策略必须在 CLI 层先解析，保持 remote-first 边界。
    bool_run_external = cli_run_external(args.no_external, args.external_target, args.readiness)  # 语义比较外部仿真开关

    # compare_semantics 同步产出等价状态、QoR 摘要和转换验证路径。
    dict_compare_result = compare_semantics(  # 候选实现相对参考实现的语义比较报告
        path_reference,  # 作为等价基准的 RTL 文件
        path_candidate,  # 需要验证语义保持的 RTL 文件
        out_dir=path_output_dir,  # 保存 equivalence 和 QoR 摘要的目录
        run_external=bool_run_external,  # 是否允许调用仿真或综合工具
        readiness=args.readiness,  # compare-semantics 采用的验证强度
    )

    # compare-semantics 输出完整比较报告，调用方用 status 决定后续动作。
    sys.stdout.write(json.dumps(dict_compare_result, indent=2, ensure_ascii=False) + "\n")

    # workflow-state 记录对比两端和输出目录。
    record_state(
        args,
        "compare_semantics",
        {"reference": path_reference, "candidate": path_candidate, "output": path_output_dir},
    )

    # compare_semantics 明确 passed 才返回成功。
    return 0 if dict_compare_result["status"] == "passed" else 1

# cmd_verify_existing 执行 existing RTL 的证据驱动 verify-repair 主路径。
def cmd_verify_existing(args: argparse.Namespace) -> int:
    """处理 verify-existing 子命令。

    参数:
        args: argparse 解析出的 verify-existing 命名空间。

    返回:
        命令成功写出结构化诊断时返回 0。
    """

    # source 列表允许多文件 RTL 项目，但每个输入都必须存在。
    list_source_paths = _existing_source_paths(args.source)  # verify-repair RTL 源文件路径列表

    # verify-existing 输出目录承载诊断、补丁计划和验证证据。
    path_output_dir = require_write_path(args.out_dir, purpose="verify-existing output directory")  # verify-repair 输出目录

    # 可选输入统一通过 workspace 边界检查。
    path_spec_source = _optional_workspace_path(args.spec_source, "spec source")  # verify-repair 约束说明文件

    # legacy testbench 可选参与 augment 或验证合同生成。
    path_testbench_source = _optional_workspace_path(args.testbench_source, "testbench source")  # 可选 testbench 路径

    # decision_source 用于确认驱动的 resume。
    path_decision_source = _optional_workspace_path(args.decision_source, "decision source")  # 可选人工决策路径

    # 外部验证策略在 CLI 层解析，避免 verify-repair 暗中启动本地工具。
    bool_run_external = cli_run_external(args.no_external, args.external_target, args.readiness)  # verify-repair 外部工具开关

    # verify_existing 生成诊断包、补丁候选、仿真切片和终端状态。
    dict_verify_result = verify_existing(  # verify-repair 主流程返回的诊断和修复包
        list_source_paths,  # 进入诊断闭环的 RTL 文件集合
        out_dir=path_output_dir,  # 保存诊断、补丁和验证证据的目录
        spec_source=path_spec_source,  # 约束诊断目标的规格说明文件
        module_name=args.module_name,  # 用户指定或自动发现的待验证模块

        # testbench 和 decision 输入决定 verify-repair 是否进入增强或恢复路径。
        testbench_source=path_testbench_source,  # 供 augment 或复用的 legacy testbench
        decision_source=path_decision_source,  # 恢复人工确认流程的决策文件

        # 自动化边界必须由调用者显式传入。
        tb_mode=args.tb_mode,  # 生成、增强或复用 testbench 的策略
        tb_language=args.tb_language,  # 生成测试平台时采用的 HDL 方言
        automation_mode=args.automation_mode,  # 自动应用补丁前的人机协作边界

        # readiness 与外部工具开关决定验证闭环深度。
        readiness=args.readiness,  # 静态、语义或仿真验证深度
        run_external=bool_run_external,  # 按 remote-first 规则解析后的外部执行许可
    )

    # verify-existing 输出诊断包和终态，失败状态由 JSON 字段表达。
    sys.stdout.write(json.dumps(dict_verify_result, indent=2, ensure_ascii=False) + "\n")

    # workflow-state 记录 verify-repair 终态。
    record_state(
        args,
        "verify_existing",
        {
            "sources": list_source_paths,
            "out_dir": path_output_dir,
            "automation_mode": args.automation_mode,
            "status": dict_verify_result["status"],
        },
    )

    # verify-existing 报告型流程返回 0，失败状态由结构化结果表达。
    return 0

# cmd_run_cases 执行 representative corpus 并写出 governed RTL 与逐例报告。
def cmd_run_cases(args: argparse.Namespace) -> int:
    """处理 run-cases 子命令。

    参数:
        args: argparse 解析出的 run-cases 命名空间。

    返回:
        全部 case 完成治理合同时返回 0，否则返回 1。
    """

    # 先把 CLI 输出目录收敛到 workspace 可写边界内。
    path_output_dir = require_write_path(args.out_dir, purpose="run-cases output directory")  # representative corpus 的可写输出根目录

    # adapter 只需要输出根目录和可选 case 选择这两个稳定字段。
    dict_run_cases_config = {"out_dir": path_output_dir, "case": args.case}  # 透传给 adapter 的 run-cases 目录与 case 选择配置

    # adapter 返回的结果会直接写到 stdout，供自动化解析 summary 状态。
    dict_run_cases_result = run_verilog_cases_runtime(config=dict_run_cases_config)  # adapter 返回的 representative corpus 稳定摘要

    # CLI stdout 保持 JSON 契约，方便自动化直接读取。
    sys.stdout.write(json.dumps(dict_run_cases_result, indent=2, ensure_ascii=False) + "\n")

    # representative corpus 只有全部完成时才返回成功退出码。
    return 0 if dict_run_cases_result["status"] == "completed" else 1

# _existing_source_paths 校验已有 RTL 源文件列表。
def _existing_source_paths(sources: list[Path]) -> list[Path]:
    """解析已有 RTL CLI 输入的源文件列表。

    参数:
        sources: CLI 传入的 RTL 源文件路径列表。

    返回:
        通过 workspace 边界检查的源文件路径列表。
    """

    # 每个 source 参数都必须在 workspace 边界内存在。
    return [
        require_workspace_path(path_source, purpose="Verilog source path", must_exist=True)
        for path_source in sources
    ]

# _optional_workspace_path 统一处理 optional Path 参数。
def _optional_workspace_path(path_value: Path | None, purpose: str) -> Path | None:
    """解析可选 workspace 路径参数。

    参数:
        path_value: CLI 传入的可选路径。
        purpose: 错误信息中使用的路径用途说明。

    返回:
        未提供路径时返回 None，否则返回通过 workspace 检查的路径。
    """

    # None 表示调用方没有提供该可选输入。
    if path_value is None:

        # 缺省输入保持 None 传给核心 workflow。
        return None

    # 非空路径必须存在且通过 workspace 边界检查。
    return require_workspace_path(path_value, purpose=purpose, must_exist=True)
