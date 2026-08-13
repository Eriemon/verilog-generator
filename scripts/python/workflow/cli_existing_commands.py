"""实现 existing-RTL 分析、受控修改、语义比较和 verify-repair CLI 子命令。"""

# future annotations 避免 argparse 类型提示在运行期求值。
from __future__ import annotations

# 标准库负责 argparse 命名空间与 JSON 输出。
import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 公开 facade API 负责 representative corpus 的治理与摘要落盘。
from scripts.python.facade.verilog_api import run_verilog_cases as run_verilog_cases_runtime

# existing RTL 分析与受控修改入口保持在核心模块。
from scripts.python.existing_rtl.existing_rtl import analyze_existing_rtl, load_spec_text
from scripts.python.existing_rtl.existing_rtl_improvement import (
    ImproveExistingRtlOptions,
    compare_semantics,
    improve_existing_rtl,
    require_improve_goal,
)

# verify-repair 主路径由 verify_repair 模块提供。
from scripts.python.existing_rtl.verify_repair import verify_existing

# CLI support 统一处理外部验证策略和状态记录。
from .cli_support import cli_run_external, record_state
from .workspace import require_workspace_path, require_write_path

# 文本形式列出不会阻断 strict CLI 的顶层状态。
STRICT_SUCCESS_STATUS_TEXT = "analyzed completed passed planned advisory_only"  # strict CLI 允许返回 0 的状态集合

# set 形式供状态查找复用。
STRICT_SUCCESS_STATUSES = set(STRICT_SUCCESS_STATUS_TEXT.split())  # strict CLI 成功状态查找表

# 基础失败状态覆盖本地阻断和异常终止。
STRICT_FAILURE_CORE_STATUS_TEXT = "ask_human blocked blocked_human blocked_toolchain error failed failure"  # 本地失败状态集合

# 外部验证失败状态覆盖远程和工具链边界。
STRICT_FAILURE_EXTERNAL_STATUS_TEXT = "needs_external_validation requires_decision timeout toolchain_issue"  # 外部验证失败状态集合

# 文本形式合并所有会阻断 strict CLI 的状态和路由。
STRICT_FAILURE_STATUS_TEXT = f"{STRICT_FAILURE_CORE_STATUS_TEXT} {STRICT_FAILURE_EXTERNAL_STATUS_TEXT}"  # strict CLI 非零状态全集

# set 形式供递归 payload 扫描复用。
STRICT_FAILURE_STATUSES = set(STRICT_FAILURE_STATUS_TEXT.split())  # strict CLI 失败状态查找表

# 文本形式固定需要回读的终态路径字段。
STRICT_LINKED_JSON_KEY_TEXT = "terminal_status_path run_summary_path verification_result_path"  # verify-existing 需要回读的终态路径字段

# tuple 形式保持终态文件回读顺序稳定。
STRICT_LINKED_JSON_KEYS = tuple(STRICT_LINKED_JSON_KEY_TEXT.split())  # strict CLI 回读 JSON 的字段顺序

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
    dict_analysis_result = analyze_existing_rtl(  # 供后续 improve/verify 复用的分析索引
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

    # analyze-existing 按统一 strict payload 判定返回码。
    return _strict_existing_payload_exit_code(dict_payload)

# cmd_improve_existing 对单个 RTL 源文件生成受控修改辅助产物。
def cmd_improve_existing(args: argparse.Namespace) -> int:
    """处理 improve-existing 子命令。

    参数:
        args: argparse 解析出的 improve-existing 命名空间。

    返回:
        受控修改辅助产物生成成功时返回 0。
    """

    # source 输入是本次受控修改的主 RTL 文件。
    path_source = require_workspace_path(args.source, purpose="Verilog source path", must_exist=True)  # 受控修改 RTL 源文件

    # improvement 输出目录承载计划、wrapper 或辅助 testbench。
    path_output_dir = require_write_path(args.out_dir, purpose="improvement output directory")  # 受控修改输出目录

    # analysis 可选复用 analyze-existing 的结构化结果。
    path_analysis = _optional_workspace_path(args.analysis, "analysis path")  # 可选分析报告路径

    # spec_source 可选提供用户约束。
    path_spec_source = _optional_workspace_path(args.spec_source, "spec source")  # 可选规格说明路径

    # 把 improve-existing 的所有可选输入收敛成配置对象，避免入口参数继续拉长。
    improve_existing_rtl_options_config = ImproveExistingRtlOptions(  # improve-existing 子命令的可选输入配置
        analysis_source=path_analysis,  # analyze-existing 产出的项目分析文件
        spec_source=path_spec_source,  # 用户提供的修改约束说明
        candidate_artifacts_dir=args.candidate_artifacts_dir,  # 候选产物目录
        baseline_artifacts_dir=args.baseline_artifacts_dir,  # 基线产物目录
        readiness=args.readiness,  # 验证 readiness 深度
        tb_language=getattr(args, "tb_language", "verilog"),  # 测试平台生成时采用的硬件描述语言边界
    )

    # improve_existing_rtl 按目标生成可审计辅助产物，不静默改写源文件。
    dict_improve_result = improve_existing_rtl(  # 受控修改模式产出的辅助工件索引
        path_source,  # 本次 improve 的主 RTL 文件
        out_dir=path_output_dir,  # 保存 improve 辅助产物的目录
        improve_goal=require_improve_goal(args.goal),  # 合并、优化或 testbench 辅助目标
        options=improve_existing_rtl_options_config,  # 所有可选 improve 输入都通过配置对象传递
    )

    # improve-existing 输出完整产物索引，供自动化流程直接解析。
    sys.stdout.write(json.dumps(dict_improve_result, indent=2, ensure_ascii=False) + "\n")

    # workflow-state 记录目标和输出目录。
    record_state(args, "improve_existing", {"source": path_source, "goal": args.goal, "output": path_output_dir})

    # improve-existing 的计划产物也必须传导 strict blocker。
    return _strict_existing_payload_exit_code(dict_improve_result)

# cmd_compare_semantics 比较基线 RTL 与候选 RTL 的语义证据。
def cmd_compare_semantics(args: argparse.Namespace) -> int:
    """处理 compare-semantics 子命令。

    参数:
        args: argparse 解析出的 compare-semantics 命名空间。

    返回:
        语义比较通过时返回 0，否则返回 1。
    """

    # baseline RTL 是语义比较的黄金基线设计。
    path_baseline = require_workspace_path(args.baseline, purpose="baseline RTL path", must_exist=True)  # 语义比较基线 RTL 路径

    # candidate RTL 是需要证明语义保持的修改后设计。
    path_candidate = require_workspace_path(args.candidate, purpose="candidate RTL path", must_exist=True)  # 待验证候选 RTL 路径

    # compare 输出目录承载 equivalence 和 QoR 报告。
    path_output_dir = require_write_path(args.out_dir, purpose="compare output directory")  # 语义比较输出目录

    # 外部验证策略必须在 CLI 层先解析，保持 remote-first 边界。
    bool_run_external = cli_run_external(args.no_external, args.external_target, args.readiness)  # 语义比较外部仿真开关

    # compare_semantics 同步产出等价状态、QoR 摘要和转换验证路径。
    dict_compare_result = compare_semantics(  # 候选实现相对基线实现的语义比较报告
        path_baseline,  # 作为等价基准的 RTL 文件
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
        {"baseline": path_baseline, "candidate": path_candidate, "output": path_output_dir},
    )

    # compare-semantics 的等价失败和 warning 必须传导到退出码。
    return _strict_existing_payload_exit_code(dict_compare_result)

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

    # verify-existing 在 strict CLI 下必须把 warning/error/status 失败映射成非零退出。
    return _strict_existing_payload_exit_code(dict_verify_result)

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

# _strict_existing_payload_exit_code 把 existing RTL payload 映射成 strict CLI 返回码。
def _strict_existing_payload_exit_code(dict_payload: dict[str, Any]) -> int:
    """
    根据 existing RTL 结构化 payload 计算 strict CLI 退出码。

    参数:
        dict_payload: CLI 即将写出的 JSON payload。

    返回:
        可交付或纯分析成功时返回 0；存在 warning、error 或失败状态时返回 1。
    """

    # payload 或其引用终态文件里出现 strict 阻断即返回非零。
    if _payload_has_strict_blocker(dict_payload):

        # warning/error/status 失败都阻断 strict CLI 成功。
        return 1

    # verify-existing payload 可能只在终态文件里记录失败路由。
    if _linked_payloads_have_strict_blocker(dict_payload):

        # 终态文件失败也必须传导到 CLI。
        return 1

    # 顶层 status 决定没有具体诊断时的默认返回码。
    str_status = str(dict_payload.get("status") or "").lower()  # payload 顶层状态

    # 没有 status 的旧 payload 默认按成功处理。
    if not str_status:

        # 兼容旧命令输出。
        return 0

    # 只有明确成功状态才返回 0。
    return 0 if str_status in STRICT_SUCCESS_STATUSES else 1

# _linked_payloads_have_strict_blocker 读取 verify-existing 引用的终态 JSON。
def _linked_payloads_have_strict_blocker(dict_payload: dict[str, Any]) -> bool:
    """
    判断 payload 引用的终态 JSON 文件是否包含 strict 阻断。

    参数:
        dict_payload: CLI 即将写出的 JSON payload。

    返回:
        任一引用 JSON 文件包含 warning、error 或失败状态时返回 True。
    """

    # 逐个读取已知终态路径字段。
    for str_key in STRICT_LINKED_JSON_KEYS:

        # 缺失路径字段表示该 payload 没有对应终态文件。
        str_path = str(dict_payload.get(str_key) or "")  # 终态 JSON 路径文本

        # 空路径无需读取。
        if not str_path:

            # 当前 payload 未声明该终态文件，继续检查下一个路径键。
            continue

        # path_json 使用 Path 统一处理字符串路径。
        path_json = Path(str_path)  # 终态 JSON 路径对象

        # 文件不存在时不额外阻断，保留旧 payload 兼容性。
        if not path_json.is_file():

            # 历史 payload 没有落盘该终态文件时继续扫描其他路径。
            continue

        # 可读 JSON 进入同一套 strict blocker 判断。
        if _payload_has_strict_blocker(_read_json_payload(path_json)):

            # 终态文件已表达 blocker，立即传导给 CLI。
            return True

    # 所有终态文件都没有 strict 阻断。
    return False

# _read_json_payload 读取终态 JSON，失败时返回阻断 payload。
def _read_json_payload(path_json: Path) -> Any:
    """
    读取 JSON 文件并返回解析结果。

    参数:
        path_json: 需要读取的 JSON 文件路径。

    返回:
        JSON 解析结果；读取失败时返回带 error severity 的 payload。
    """

    # 终态 JSON 读失败在 strict CLI 中视作可追踪错误。
    try:

        # 读取到的 JSON 对象返回给 strict blocker 扫描。
        object_json_payload = json.loads(path_json.read_text(encoding="utf-8"))  # 终态 JSON 解析结果

        # 解析成功时直接交给调用方继续判断。
        return object_json_payload

    # 解析失败时构造 error payload，确保 CLI 返回非零。
    except (OSError, json.JSONDecodeError) as exc:

        # 读失败本身就是 strict CLI blocker。
        dict_error_payload = {  # strict blocker 扫描可识别的读失败载荷
            "status": "failed",  # 让 strict 扫描识别终态文件读取失败
            "issues": [  # 保留原始异常文本供 CLI 调用方定位问题
                {
                    "severity": "error",  # 读失败在 strict 模式下等同 blocker
                    "message": f"Unable to read linked status payload: {exc}",  # 终态文件读取失败详情
                }
            ],
        }

        # 返回可被 _payload_has_strict_blocker 识别的错误 payload。
        return dict_error_payload

# _payload_has_strict_blocker 递归查找 warning/error 和失败状态。
def _payload_has_strict_blocker(object_payload: Any) -> bool:
    """
    判断任意 JSON payload 中是否存在 strict 阻断。

    参数:
        object_payload: 需要扫描的 JSON 兼容对象。

    返回:
        出现 warning、error、失败 status 或失败 route 时返回 True。
    """

    # dict payload 同时检查当前层和子字段。
    if isinstance(object_payload, dict):

        # 当前层的 severity 或 status 已足够决定阻断。
        if _dict_has_strict_blocker(object_payload):

            # 当前 dict 层已经表达 blocker。
            return True

        # 递归扫描子字段。
        for object_value in object_payload.values():

            # 任一子字段阻断即可终止扫描。
            if _payload_has_strict_blocker(object_value):

                # 子 payload 已表达 blocker。
                return True

        # 当前 dict 及其子字段都没有 strict blocker。
        return False

    # list payload 逐项扫描。
    if isinstance(object_payload, list):

        # 任一子项阻断即可返回 True。
        for object_item in object_payload:

            # 递归检查 list 子项。
            if _payload_has_strict_blocker(object_item):

                # list 子项已表达 blocker。
                return True

        # list 中没有 strict blocker。
        return False

    # 标量值本身不表达 strict 阻断。
    return False

# _dict_has_strict_blocker 判断单层 dict 是否表达 strict 阻断。
def _dict_has_strict_blocker(dict_payload: dict[str, Any]) -> bool:
    """
    判断单层字典是否包含 strict 阻断字段。

    参数:
        dict_payload: 单层 JSON 字典。

    返回:
        当前层出现 warning、error 或失败状态时返回 True。
    """

    # severity 明确为 warning/error/fatal 时直接阻断。
    str_severity = str(dict_payload.get("severity") or dict_payload.get("level") or "").lower()  # 当前层严重级别文本

    # 当前层明确包含 warning/error 时立即阻断。
    if str_severity in {"error", "fatal", "warning"}:

        # severity 字段已经足以要求非零退出。
        return True

    # status/outcome/route 等字段出现失败值时阻断。
    for str_key in ("status", "outcome", "diagnosis_route", "primary_source", "action"):

        # 空值不参与判断。
        str_value = str(dict_payload.get(str_key) or "").lower()  # 当前状态字段的小写文本

        # 明确失败状态或路由必须非零退出。
        if str_value in STRICT_FAILURE_STATUSES:

            # 当前状态字段命中失败路由。
            return True

    # 当前层没有 strict 阻断。
    return False
