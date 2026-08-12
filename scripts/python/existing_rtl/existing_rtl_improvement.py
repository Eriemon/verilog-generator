"""existing RTL 分析与受控精修辅助流程。"""

# 启用更直接的前向引用类型标注写法。
from __future__ import annotations

# dataclass 用来承接精修入口的可选配置。
from dataclasses import dataclass

# 标准库依赖负责 JSON 读写、文件复制和外部命令执行。
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

# 既有 RTL 分析逻辑负责生成结构化分析与变换计划。
from .existing_rtl import analyze_existing_rtl, build_transform_plan, load_spec_text

# 验证模块提供后端探测与仿真配置选择能力。
from scripts.python.validation.validation import _backend_tools, _select_simulator_backend, _simulator_config

# 工作区工具负责把报告与 JSON 工件写到目标目录。
from scripts.python.workflow.workspace import write_json, write_text

# 允许的精修目标列表决定了入口参数的合法范围。
IMPROVE_GOALS = (
    "tb_scaffold",  # 生成或补写 testbench 外壳
    "style_improve",  # 输出风格精修指导报告
    "partition_assist",  # 生成分区辅助 wrapper
    "optimize_assist",  # 生成 QoR/优化辅助计划
    "merge_assist",  # 生成候选合并辅助工件
)  # existing RTL 精修入口允许的目标类型

# 把 improve_existing_rtl 的可选输入收敛成配置对象，避免入口参数继续膨胀。
@dataclass(slots=True)
class ImproveExistingRtlOptions:
    """封装 existing RTL 精修辅助流程的可选输入。

    参数:
        analysis_source: 已存在的分析结果 JSON 路径，可选。
        spec_source: 规格说明来源，可为文本路径、字典或原始字符串。
        candidate_artifacts_dir: 候选设计工件目录，可选。
        baseline_artifacts_dir: 基线设计工件目录，可选。
        readiness: 当前验证准备度级别。
        tb_language: testbench 输出语言。

    返回:
        None。该对象只承载 improve_existing_rtl 的可选配置。
    """

    # 允许调用方直接复用已有分析 JSON。
    analysis_source: Path | None = None  # 外部传入的结构分析结果路径

    # 规格可来自文本路径、字典或原始字符串。
    spec_source: str | Path | dict[str, Any] | None = None  # 传给分析器或脚手架生成器的规格来源

    # merge/optimize 场景需要读取候选设计工件目录。
    candidate_artifacts_dir: Path | None = None  # 候选设计工件目录

    # 重新分析时优先从基线工件目录提取 RTL。
    baseline_artifacts_dir: Path | None = None  # 基线设计工件目录

    # readiness 控制是否允许继续走外部验证工具链。
    readiness: str = "static"  # 当前验证流程允许使用的执行深度

    # tb_scaffold 默认生成 Verilog 版本 testbench。
    tb_language: str = "verilog"  # testbench 生成默认采用的语言

# 规范化并校验 improve_goal 参数，避免后续主流程分支走偏。
def require_improve_goal(goal: str) -> str:
    """校验并返回规范化后的精修目标名。

    参数:
        goal: 用户传入的精修目标字符串。

    返回:
        转成小写且通过合法性校验的精修目标名。

    异常:
        ValueError: 当目标不在支持列表中时抛出。
    """

    # 精修目标统一转成小写，避免大小写分支重复。
    str_normalized_goal = goal.lower()  # 规范化后的精修目标名

    # 入口目标非法时立即阻断，避免生成错误工件。
    if str_normalized_goal not in IMPROVE_GOALS:

        # 用统一错误前缀返回可读的参数错误信息。
        raise ValueError(
            f"> ERR: [Python] Improve goal must be one of {', '.join(IMPROVE_GOALS)}."
        )

    # 返回已通过校验的规范化目标名。
    return str_normalized_goal

# 把可选 improve 配置补齐成完整对象，避免主流程重复处理 None 分支。
def _resolve_improve_options(
    options: ImproveExistingRtlOptions | None,
) -> ImproveExistingRtlOptions:
    """返回带默认值补齐后的 improve 配置对象。

    参数:
        options: 调用方传入的可选 improve 配置。

    返回:
        总是可直接读取字段的 improve 配置对象。
    """

    # 调用方未显式传配置时，构造一份默认 refine 配置。
    if options is None:

        # 返回补齐默认值后的 improve 配置对象。
        return ImproveExistingRtlOptions()

    # 已传入配置对象时直接复用，避免复制现有调用方意图。
    return options

# 为已有 RTL 生成 testbench、分区建议、风格报告或优化辅助工件。
def improve_existing_rtl(
    source_path: Path,
    *,
    out_dir: Path,
    improve_goal: str,
    options: ImproveExistingRtlOptions | None = None,
) -> dict[str, Any]:
    """生成 existing RTL 精修辅助所需的计划和工件。

    参数:
        source_path: 原始 RTL 源文件路径。
        out_dir: 本轮辅助工件输出目录。
        improve_goal: 本轮希望执行的精修目标类型。
        options: 承载分析来源、规格、候选工件与 testbench 语言的可选配置对象。

    返回:
        包含计划路径、校验路径、工件路径和分析结果的字典。
    """

    # 先把 improve_goal 规范成受支持的目标名。
    str_goal = require_improve_goal(improve_goal)  # 当前执行的精修目标名

    # 统一补齐 improve 可选配置，后续各分支都按完整对象取值。
    improve_existing_rtl_options_config = _resolve_improve_options(options)  # 已补齐默认值的 improve 配置对象

    # 调用方可直接提供分析 JSON，避免重复跑结构分析。
    if improve_existing_rtl_options_config.analysis_source is not None:

        # 先读出外部分析文件中的 JSON 文本。
        str_analysis_text = improve_existing_rtl_options_config.analysis_source.read_text(encoding="utf-8")  # 外部分析文件中的 JSON 文本

        # 再把 JSON 文本解析成结构化分析结果。
        dict_analysis = json.loads(str_analysis_text)  # 从分析 JSON 载入的结构化结果

    # 未提供分析 JSON 时，基于参考工件或源文件现算分析结果。
    else:

        # 先收集基线工件目录中的 RTL 输入。
        list_analysis_sources = _artifact_sources(improve_existing_rtl_options_config.baseline_artifacts_dir)  # 基线工件目录解析出的 RTL 输入列表

        # 缺少参考工件时，退回当前 source_path 作为唯一分析输入。
        if not list_analysis_sources:

            # 回退为单文件输入，保证后续分析至少有一个 RTL 源。
            list_analysis_sources = [source_path]  # 回退后的 existing RTL 分析输入列表

        # 先把规格输入统一解析成文本，避免在调用行里堆叠逻辑。
        str_spec_text = load_spec_text(improve_existing_rtl_options_config.spec_source)  # 传给分析器的规格说明文本

        # 再基于 RTL 输入和规格文本执行结构分析。
        dict_analysis_result = analyze_existing_rtl(list_analysis_sources, spec_text=str_spec_text)  # 重新分析 existing RTL 后得到的完整结果字典

        # 仅提取 existing RTL 结构分析结果本体。
        dict_analysis = dict_analysis_result["analysis"]  # existing RTL 结构分析结果

    # optimize_assist 会走单独的优化辅助主流程。
    if str_goal == "optimize_assist":

        # 直接转交优化辅助分支处理。
        return _optimize_assist(
            source_path,
            out_dir=out_dir,
            analysis=dict_analysis,
            candidate_artifacts_dir=improve_existing_rtl_options_config.candidate_artifacts_dir,
            baseline_artifacts_dir=improve_existing_rtl_options_config.baseline_artifacts_dir,
            readiness=improve_existing_rtl_options_config.readiness,
        )

    # merge_assist 同样需要走独立的候选合并辅助分支。
    if str_goal == "merge_assist":

        # 直接转交合并辅助分支处理。
        return _merge_assist(
            source_path,
            out_dir=out_dir,
            analysis=dict_analysis,
            candidate_artifacts_dir=improve_existing_rtl_options_config.candidate_artifacts_dir,
            readiness=improve_existing_rtl_options_config.readiness,
        )

    # 这些分支共享一份期望输出列表和工件索引字典。
    list_expected_outputs: list[dict[str, Any]] = []  # 当前精修目标预期生成的输出工件列表

    # 工件索引会写进 transform_validation，便于后续流程消费。
    dict_artifacts: dict[str, str] = {}  # 当前精修目标生成的工件路径映射

    # tb_scaffold 目标输出可直接运行或补写的 testbench 外壳。
    if str_goal == "tb_scaffold":

        # testbench 路径绑定当前分析模块名，避免不同模块互相覆盖。
        str_module_name = dict_analysis["module_info"]["name"]  # 当前分析得到的模块名

        # 把输出文件名绑定到模块名，避免不同模块 testbench 互相覆盖。
        path_tb = out_dir / "tb" / f"tb_{str_module_name}.v"  # 当前模块 testbench 外壳输出路径

        # 根据结构分析结果写出 testbench 外壳。
        _write_tb_from_analysis(
            dict_analysis,
            path_tb,
            tb_language=improve_existing_rtl_options_config.tb_language,
        )

        # 记下 testbench 相对路径，让计划文件能引用生成物。
        list_expected_outputs.append(
            {
                "path": path_tb.relative_to(out_dir).as_posix(),  # 相对输出目录的 testbench 路径
                "kind": "testbench",  # 当前工件类型为 testbench
            }
        )

        # 记录 testbench 的绝对工件路径。
        dict_artifacts["testbench"] = str(path_tb)  # testbench 工件的绝对路径

    # partition_assist 目标输出顶层 wrapper 和分区辅助壳。
    elif str_goal == "partition_assist":

        # 先提取分区 wrapper 要复用的顶层模块名。
        str_module_name = dict_analysis["module_info"]["name"]  # 当前分区辅助要复用的顶层模块名

        # 让分区 wrapper 文件名与目标模块保持一一对应。
        path_wrapper = out_dir / "partition" / f"top_{str_module_name}.v"  # 分区辅助 wrapper 输出路径

        # 写出承接分区拆分的顶层 wrapper 草稿。
        _write_partition_wrapper(dict_analysis, path_wrapper)

        # 把分区 wrapper 写进预期输出清单，供后续计划引用。
        list_expected_outputs.append(
            {
                "path": path_wrapper.relative_to(out_dir).as_posix(),  # 分区 wrapper 在输出目录中的相对位置
                "kind": "wrapper",  # 该输出项对应的工件类别标签
            }
        )

        # 把分区 wrapper 路径暴露给调用方和后续校验摘要。
        dict_artifacts["wrapper"] = str(path_wrapper)  # 暴露给调用方和校验摘要的 wrapper 路径

    # style_improve 默认输出风格审查与精修引导报告。
    else:

        # 风格精修报告使用固定文件名，方便用户直接查阅。
        path_guide = out_dir / "style" / "style_improve_report.md"  # 风格精修报告输出路径

        # 输出可直接阅读的风格精修建议文档。
        _write_style_improve_guide(dict_analysis, path_guide)

        # 把风格精修文档登记到输出清单，供计划文件引用。
        list_expected_outputs.append(
            {
                "path": path_guide.relative_to(out_dir).as_posix(),  # 风格精修文档在输出目录中的相对位置
                "kind": "guide",  # 该输出项对应的指南类别标签
            }
        )

        # 把风格精修文档的绝对路径回填到工件索引。
        dict_artifacts["style_report"] = str(path_guide)  # 风格报告工件的绝对路径

    # 用结构分析和预期输出来构造本轮变换计划。
    dict_transform_plan = build_transform_plan(  # 当前精修目标的 RTL 变换计划
        dict_analysis,  # 当前既有 RTL 的结构分析结果
        transform_goal=str_goal,  # 本轮准备执行的精修目标
        expected_outputs=list_expected_outputs,  # 本轮预计生成的辅助工件清单
    )

    # 把变换计划落盘，供后续执行或人工审阅。
    path_transform_plan = write_json(out_dir / "rtl_transform_plan.json", dict_transform_plan)  # 变换计划 JSON 输出路径

    # 先提取模块级摘要字段，避免在摘要字典里堆叠长表达式。
    str_summary_module_name = dict_analysis["module_info"]["name"]  # 分析摘要中的模块名

    # 统计分析摘要里的端口数量，供 validation 快速查看。
    int_summary_port_count = dict_analysis["module_info"]["port_count"]  # 分析摘要中的端口数量

    # 计算可供拆分的候选数量，供 validation 直观看到复杂度。
    int_summary_decomposition_count = len(dict_analysis["decomposition_candidates"])  # 分析摘要中的分解候选数量

    # 组装供 validation 复用的分析摘要。
    dict_analysis_summary = {  # 本轮分析摘要，供 transform_validation 复用
        "module": str_summary_module_name,  # 摘要里的目标模块名
        "port_count": int_summary_port_count,  # 摘要里的端口数量
        "decomposition_candidates": int_summary_decomposition_count,  # 摘要里的分解候选数量
    }

    # transform_validation 汇总本轮辅助是否已准备好进入下一阶段。
    dict_transform_validation = dict(  # 本轮精修辅助的校验与工件摘要
        version=1,  # 校验摘要版本
        goal=str_goal,  # 当前精修目标名称
        ready=True,  # 当前辅助输出默认视为可进入下一阶段
        issues=[],  # 当前阶段暂未登记阻断问题
        artifacts=dict_artifacts,  # 当前阶段生成的工件路径映射
        analysis_summary=dict_analysis_summary,  # 当前阶段复用的分析摘要
    )

    # 把 transform_validation 一并落盘。
    path_validation = write_json(out_dir / "transform_validation.json", dict_transform_validation)  # 精修辅助校验摘要 JSON 输出路径

    # 返回本轮精修辅助的主结果字典。
    return {
        "status": "planned",
        "transform_plan_path": str(path_transform_plan),
        "transform_validation_path": str(path_validation),
        "artifacts": dict_artifacts,
        "analysis": dict_analysis,
    }

# 比较参考 RTL 与候选 RTL 在接口、检查点和 testbench 语义上的一致性。
def compare_semantics(
    reference_path: Path,
    candidate_path: Path,
    *,
    out_dir: Path,
    run_external: bool = True,
    readiness: str = "static",
) -> dict[str, Any]:
    """比较参考设计与候选设计的语义一致性。

    参数:
        reference_path: 参考 RTL 源文件路径。
        candidate_path: 候选 RTL 源文件路径。
        out_dir: 比较工件输出目录。
        run_external: 是否允许调用外部工具链。
        readiness: 当前验证准备度级别。

    返回:
        包含 equivalence、QoR 和 transform_validation 路径的结果字典。
    """

    # 先分别分析参考设计和候选设计的结构信息。
    dict_reference_result = analyze_existing_rtl([reference_path])  # 参考 RTL 的完整分析结果

    # 从完整分析结果中取出参考设计的 analysis 本体。
    dict_reference = dict_reference_result["analysis"]  # 参考 RTL 的结构分析结果

    # 候选设计使用同一分析流程，便于后续逐项比较。
    dict_candidate_result = analyze_existing_rtl([candidate_path])  # 候选 RTL 的完整分析结果

    # 从完整分析结果中取出候选设计的 analysis 本体。
    dict_candidate = dict_candidate_result["analysis"]  # 候选 RTL 的结构分析结果

    # 仿真后端尝试列表用于记录选中的后端与回退情况。
    list_backend_attempts = _simulator_backend_attempts(run_external=run_external)  # 语义比较阶段尝试过的仿真后端列表

    # 记录未被选中的后端，方便输出工具链回退轨迹。
    list_toolchain_fallbacks = [item["name"] for item in list_backend_attempts if item["status"] != "selected"]  # 语义比较阶段的工具链回退列表

    # 先比较接口层面的兼容性问题。
    list_issues = _interface_issues(dict_reference, dict_candidate)  # 参考设计与候选设计的接口问题列表

    # 再比较 checkpoint/特征映射层面的漂移问题。
    list_checkpoint_issues = _checkpoint_issues(dict_reference, dict_candidate)  # 参考设计与候选设计的检查点漂移问题列表

    # 把 checkpoint 问题并入总 issue 列表。
    list_issues.extend(list_checkpoint_issues)

    # testbench 一致性结果负责补足行为层面的观测信息。
    dict_testbench = _testbench_consistency(  # 参考设计与候选设计的 testbench 一致性结果
        dict_reference,  # 提供基准 checkpoint 序列的参考设计分析结果
        dict_candidate,  # 提供待比较 checkpoint 序列的候选设计分析结果
        run_external=run_external,  # 控制是否允许后续接外部仿真工具
        backend_attempts=list_backend_attempts,  # 沿用本轮 compare 已记录的后端探测轨迹
    )

    # QoR 报告负责补足资源与时序层面的比较信息。
    dict_qor_report = _qor_report(  # 参考设计与候选设计的 QoR 比较结果
        dict_reference,  # 参考设计的结构分析结果
        dict_candidate,  # 候选设计的结构分析结果
        run_external=run_external,  # 控制是否允许调用外部 QoR 工具
        reference_path=reference_path,  # QoR 报告绑定的参考 RTL 文件
        candidate_path=candidate_path,  # QoR 报告绑定的候选 RTL 文件
    )

    # testbench 不一致时，把 warning 补进统一问题列表。
    if not dict_testbench["consistent"]:

        # 记录 testbench 行为不一致的告警。
        list_issues.append(
            {
                "severity": "warning",
                "source": "testbench_issue",
                "message": dict_testbench["message"],
            }
        )

    # 先单独计算接口一致性，避免在结果字典里堆叠长生成式表达式。
    bool_has_interface_error = False  # compare 阶段是否存在 current_module_issue error

    # 逐项扫描问题列表，只要命中接口 error 就终止后续检查。
    for dict_issue in list_issues:

        # 只关心 current_module_issue 里的 error 级问题。
        if dict_issue["severity"] != "error" or dict_issue["source"] != "current_module_issue":

            # 非接口 error 时继续检查下一条问题。
            continue

        # 命中接口 error 后立即标记阻断状态。
        bool_has_interface_error = True  # compare 阶段已经发现接口 error

        # 首个接口 error 已足够决定结果，因此这里提前结束扫描。
        break

    # 没有接口 error 时，reference/candidate 才可视为接口一致。
    bool_interface_consistent = not bool_has_interface_error  # compare 阶段的接口一致性结论

    # equivalence 汇总接口、checkpoint、仿真与 QoR 的总比较结果。
    dict_equivalence = {  # 参考设计与候选设计的语义等价摘要
        "version": 1,  # 语义等价摘要版本
        "reference_top": dict_reference["module_info"]["name"],  # 参考设计顶层模块名
        "candidate_top": dict_candidate["module_info"]["name"],  # 候选设计顶层模块名
        "readiness": readiness,  # 当前验证准备度
        "interface_consistent": bool_interface_consistent,  # 接口是否保持一致
        "checkpoint_consistent": not list_checkpoint_issues,  # 检查点是否保持一致
        "testbench_consistent": dict_testbench["consistent"],  # testbench 行为是否一致
        "qor_comparable": dict_qor_report["qor_comparable"],  # QoR 是否具备可比性
        "simulator_backend_attempts": list_backend_attempts,  # 仿真后端尝试轨迹
        "toolchain_fallbacks": list_toolchain_fallbacks,  # 未命中的工具链回退记录
        "semantic_case_results": dict_testbench["semantic_case_results"],  # 行为级 case 结果
        "checkpoint_drift": list_checkpoint_issues,  # 检查点漂移清单
        "issues": list_issues,  # compare 阶段汇总问题列表
    }

    # 把 equivalence JSON 写入输出目录。
    path_equivalence = write_json(out_dir / "equivalence.json", dict_equivalence)  # compare 语义等价摘要 JSON 的最终输出路径

    # 把 QoR 报告 JSON 一并写入输出目录。
    path_qor = write_json(out_dir / "qor_report.json", dict_qor_report)  # QoR 比较报告 JSON 输出路径

    # 先把 compare 阶段是否可继续推进整理成布尔值。
    bool_compare_ready = not any(item["severity"] == "error" for item in list_issues)  # compare 阶段是否可继续推进

    # 再预先计算 compare 场景的推荐下一步动作。
    str_compare_next_action = _recommended_next_action(dict_equivalence, candidate_provided=True)  # compare 阶段推荐的下一步动作

    # transform_validation 为后续流程提供简洁的 compare 阶段结论。
    dict_compare_transform_validation_verification_summary_payload = dict(  # compare 返回结构里 transform_validation["verification_summary"] 字段的落盘内容
        interface_consistent=dict_equivalence["interface_consistent"],  # compare 接口一致性
        checkpoint_consistent=dict_equivalence["checkpoint_consistent"],  # compare 检查点一致性
        testbench_consistent=dict_equivalence["testbench_consistent"],  # compare 阶段 testbench 行为一致性结论
        selected_backend=dict_testbench["selected_backend"],  # compare 最终选中的仿真后端
    )

    # 把 compare 阶段的 QoR 三个核心状态压缩成小字典。
    dict_qor_summary_payload = dict(  # compare 阶段 QoR 摘要载荷
        qor_comparable=dict_qor_report["qor_comparable"],  # compare 阶段 QoR 可比性
        status=dict_qor_report["status"],  # compare 阶段 QoR 主状态
        yosys_stat=dict_qor_report.get("yosys_stat", {}).get("status"),  # compare 阶段 yosys stat 子状态
    )

    # 用 compare 证据生成 transform_validation 主摘要，供上游流程直接消费。
    dict_compare_transform_validation_payload = dict(  # compare 返回结构里 transform_validation.json 的全量门禁对象
        version=1,  # compare 阶段 validation 结果使用的版本号
        ready=bool_compare_ready,  # compare 阶段是否允许继续推进
        issues=list_issues,  # 传给调用方排查 compare 失败原因的问题清单
        verification_summary=dict_compare_transform_validation_verification_summary_payload,  # compare 验证摘要载荷
        qor_summary=dict_qor_summary_payload,  # compare QoR 摘要载荷
        recommended_next_action=str_compare_next_action,  # 驱动调用方下一步分支选择的 compare 建议动作
    )

    # 把 compare 阶段的 transform_validation 落盘。
    path_transform_validation = write_json(  # compare 阶段校验摘要 JSON 输出路径
        out_dir / "transform_validation.json",  # compare transform_validation 输出文件路径
        dict_compare_transform_validation_payload,  # compare transform_validation 正文载荷
    )

    # 返回 compare 阶段生成的主要工件路径。
    return {
        "status": "passed" if dict_compare_transform_validation_payload["ready"] else "failed",
        "equivalence_path": str(path_equivalence),
        "qor_report_path": str(path_qor),
        "transform_validation_path": str(path_transform_validation),
    }

# 汇总 optimize_assist 固定输出项，避免主流程里重复堆叠工件描述。
def _optimize_expected_outputs(
    out_dir: Path,
    *,
    path_optimization_plan: Path,
    path_candidate_wrapper: Path,
    path_partition_map: Path,
) -> list[dict[str, str]]:
    """返回 optimize_assist 阶段预期生成的输出描述。

    参数:
        out_dir: optimize_assist 阶段的工件输出目录。
        path_optimization_plan: 优化建议文档路径。
        path_candidate_wrapper: 候选 wrapper 路径。
        path_partition_map: 候选分区映射路径。

    返回:
        适合直接传给 build_transform_plan 的输出描述列表。
    """

    # 把固定工件统一整理成 expected_outputs 列表。
    return [
        {
            "path": path_optimization_plan.relative_to(out_dir).as_posix(),  # 优化建议文档相对路径
            "kind": "optimization_plan",  # 当前输出项类型为优化建议文档
        },
        {
            "path": path_candidate_wrapper.relative_to(out_dir).as_posix(),  # 候选 wrapper 相对路径
            "kind": "candidate_wrapper",  # 当前输出项类型为候选 wrapper
        },
        {
            "path": path_partition_map.relative_to(out_dir).as_posix(),  # 分区映射 JSON 相对路径
            "kind": "candidate_partition_map",  # 当前输出项类型为分区映射
        },
    ]

# 统一提供 optimize_assist 默认验证摘要，避免主流程重复展开占位字段。
def _default_optimize_verification_summary() -> dict[str, str | bool | None]:
    """返回 optimize_assist 在没有候选 RTL 时的默认验证摘要。

    参数:
        无业务参数；函数直接返回默认验证摘要。

    返回:
        optimize_assist 默认使用的验证摘要字典。
    """

    # 缺少候选 RTL 时，各项一致性结论都保持未知状态。
    return {
        "interface_consistent": None,  # 初始状态下接口一致性未知
        "checkpoint_consistent": None,  # 初始状态下检查点一致性未知
        "testbench_consistent": None,  # 初始状态下 testbench 一致性未知
        "selected_backend": None,  # 初始状态下尚未选中仿真后端
    }

# 统一提供 optimize_assist 默认 QoR 摘要，避免 advisory 分支重复造字典。
def _default_optimize_qor_summary() -> dict[str, str | bool]:
    """返回 optimize_assist 在 advisory 模式下的默认 QoR 摘要。

    参数:
        无业务参数；函数直接返回默认 QoR 摘要。

    返回:
        optimize_assist 默认使用的 QoR 摘要字典。
    """

    # 没有候选 RTL 时，QoR 只能给出 advisory 级别提示。
    return {
        "qor_comparable": False,  # 默认没有候选 RTL 时不能做 QoR 对比
        "status": "advisory_only",  # 默认仅输出 advisory 级别 QoR 建议
        "yosys_stat": "not_run",  # 默认尚未运行 yosys stat
    }

# 统一读取 JSON 工件，避免主流程在 read_text/json.loads 之间重复切换。
def _read_json_artifact(path_json: str | Path) -> dict[str, Any]:
    """读取 JSON 工件并返回解析后的字典。

    参数:
        path_json: 待读取的 JSON 工件路径。

    返回:
        解析后的 JSON 字典内容。
    """

    # 先把路径统一成 Path，便于调用 read_text。
    path_artifact = Path(path_json)  # 需要读取的 JSON 工件路径

    # 再读出 JSON 文本，便于后续统一解析。
    str_json_text = path_artifact.read_text(encoding="utf-8")  # JSON 工件原始文本

    # 最后把 JSON 文本解析成字典。
    return json.loads(str_json_text)

# 从 compare 结果里提取最终选中的后端名，避免主流程重复写 next(...) 逻辑。
def _selected_backend_name(dict_equivalence: dict[str, Any]) -> str | None:
    """返回 compare 阶段最终选中的仿真后端名称。

    参数:
        dict_equivalence: compare 阶段的语义等价摘要字典。

    返回:
        被标记为 selected 的后端名称；未命中时返回 None。
    """

    # 先筛出 compare 阶段被标记为 selected 的后端名称。
    list_selected_names: list[str] = []  # compare 阶段命中的后端名称列表

    # 按顺序扫描后端尝试结果，只收集 selected 状态对应的名称。
    for dict_attempt in dict_equivalence["simulator_backend_attempts"]:

        # 非 selected 后端不属于最终命中列表。
        if dict_attempt["status"] != "selected":

            # 当前后端未被选中时直接跳过。
            continue

        # 记录本轮真正命中的仿真后端名称。
        list_selected_names.append(dict_attempt["name"])

    # 返回首个命中的 selected 后端；完全未命中时返回 None。
    return list_selected_names[0] if list_selected_names else None

# 生成 optimize_assist 模式下的辅助工件，并在候选 RTL 存在时补充比对结果。
def _optimize_assist(
    source_path: Path,
    *,
    out_dir: Path, analysis: dict[str, Any],
    candidate_artifacts_dir: Path | None, baseline_artifacts_dir: Path | None,
    readiness: str,
) -> dict[str, Any]:
    """生成优化辅助工件，并在候选 RTL 存在时附带语义比对结果。

    参数:
        source_path: 默认基线 RTL 路径，在缺少基线工件目录时作为回退输入。
        out_dir: optimize_assist 阶段的工件输出目录。
        analysis: 当前 RTL 的结构化分析结果。
        candidate_artifacts_dir: 候选 RTL 工件目录，可为空。
        baseline_artifacts_dir: 基线 RTL 工件目录，可为空。
        readiness: 当前验证准备度级别。

    返回:
        包含 transform_plan、transform_validation 与辅助工件路径的结果字典。
    """

    # 优先从基线工件目录取源文件，缺失时退回原始输入 RTL。
    list_baseline_sources = _artifact_sources(baseline_artifacts_dir)  # 基线工件目录解析出的 RTL 路径列表

    # 缺少基线工件时，退回 source_path 作为唯一基线 RTL。
    if not list_baseline_sources:

        # 当前轮次没有基线工件目录内容时，退回到原始 RTL。
        list_baseline_sources = [source_path]  # 回退后的基线 RTL 路径列表

    # 选出 optimize 流程实际使用的基线 RTL。
    path_baseline_source = list_baseline_sources[0]  # optimize 流程使用的基线 RTL 路径

    # 先声明候选 RTL 路径，后续再按工件目录情况补齐。
    path_candidate_source: Path | None = None  # optimize 流程使用的候选 RTL 路径

    # 候选工件目录存在时，提取其中首个 RTL 作为比较对象。
    if candidate_artifacts_dir is not None:

        # 先从候选工件目录里收集可比较的 RTL 文件。
        list_candidate_sources = _artifact_sources(candidate_artifacts_dir)  # 候选工件目录解析出的 RTL 路径列表

        # 仅取首个候选 RTL 参与 compare 复用链路。
        if list_candidate_sources:

            # 多个候选并存时，先固定取第一份 RTL 作为 optimize 比对基线。
            path_candidate_source = list_candidate_sources[0]  # 候选目录中首个参与 compare 复用的 RTL 路径

    # 先产出优化建议、包装模板和分解映射，方便后续人工细化 RTL。
    path_optimization_plan = out_dir / "optimization_plan.md"  # 优化建议文档输出路径

    # 候选 wrapper 用于承载后续分区重组尝试。
    path_candidate_wrapper = out_dir / "candidate_wrapper.v"  # 候选 wrapper 模板输出路径

    # 分区映射负责固化分析阶段发现的 decomposition 候选。
    path_partition_map = out_dir / "candidate_partition_map.json"  # 分区映射 JSON 输出路径

    # 写出优化建议文档，供人工决定精修优先级。
    _write_optimization_plan(analysis, path_optimization_plan)

    # 写出分区 wrapper 模板，帮助后续拆分实现。
    _write_partition_wrapper(analysis, path_candidate_wrapper)

    # 记录分析阶段推断出的分解候选，便于 wrapper 细化。
    write_json(
        path_partition_map,
        {
            "version": 1,
            "decomposition_candidates": analysis["decomposition_candidates"],
        },
    )

    # 先把 optimize_assist 阶段固定会落盘的工件整理成输出清单。
    list_expected_outputs = _optimize_expected_outputs(  # optimize 阶段固定输出项描述
        out_dir,  # optimize_assist 阶段的输出根目录
        path_optimization_plan=path_optimization_plan,  # 优化建议文档路径
        path_candidate_wrapper=path_candidate_wrapper,  # 候选 wrapper 模板路径
        path_partition_map=path_partition_map,  # 分区映射 JSON 路径
    )

    # 把优化目标和允许的变更边界写入统一 transform plan。
    dict_transform_plan = build_transform_plan(  # optimize 阶段的统一变更计划
        analysis,  # 当前参考 RTL 的结构分析结果
        transform_goal="optimize_assist",  # 固定写入 optimize_assist 目标名
        expected_outputs=list_expected_outputs,  # 会出现在变换计划里的工件清单
    )

    # 补充优化场景特有的 QoR 目标与等价性约束。
    dict_optimization_requirements = {  # optimize 模式要求保持的等价性条件
        "interface_consistent": True,  # 要求候选设计保持接口一致
        "checkpoint_consistent": True,  # 要求候选设计保持检查点一致
        "testbench_consistent": True,  # 要求候选设计保持 testbench 一致
        "qor_comparable": True,  # 要求候选设计具备 QoR 可比性
    }

    # 把 optimize 专属的目标、QoR 约束和改写边界补进变换计划。
    dict_transform_plan.update(
        {
            "optimization_targets": _optimization_targets(analysis),
            "qor_objectives": _qor_objectives(analysis),
            "equivalence_requirements": dict_optimization_requirements,
            "allowed_mutation_scope": "assist_only_no_default_rtl_rewrite",
        }
    )

    # optimize 的统一 transform plan 是调度器消费的主入口文件。
    path_transform_plan = write_json(out_dir / "rtl_transform_plan.json", dict_transform_plan)  # optimize 阶段 transform plan 输出路径

    # 先登记 optimize 阶段无条件会产出的三类主工件路径。
    dict_artifacts = dict(  # 返回给调用方的 optimize 主工件索引
        optimization_plan=str(path_optimization_plan),  # 供人工评审优化建议的文档输出路径
        candidate_wrapper=str(path_candidate_wrapper),  # 供人工补线和替换实例的 wrapper 骨架路径
        candidate_partition_map=str(path_partition_map),  # 供人工核对分区边界的映射 JSON 路径
    )

    # compare 可能被跳过，因此这里维护的是 optimize 阶段自有的问题聚合容器。
    list_issues: list[dict[str, Any]] = []  # optimize 阶段自有或继承的问题列表

    # 候选 RTL 缺失时，先使用默认验证摘要占位。
    dict_verification_summary = _default_optimize_verification_summary()  # optimize 阶段接口与 testbench 默认验证摘要

    # 候选 RTL 缺失时，QoR 摘要也先停留在 advisory 默认值。
    dict_qor_summary = _default_optimize_qor_summary()  # optimize 阶段默认 QoR 摘要

    # 候选 RTL 存在时，继续补充语义等价与 QoR 对比。
    if path_candidate_source is not None:

        # 启动 compare 主流程，补齐接口、一致性和 QoR 证据。
        dict_compare_result = compare_semantics(  # compare 阶段产出的路径集合
            path_baseline_source,  # compare 使用的基线 RTL 路径
            path_candidate_source,  # compare 使用的候选 RTL 路径
            out_dir=out_dir / "optimize_compare",  # compare 阶段输出目录
            run_external=readiness != "static",  # 非 static 模式才允许外部工具链
            readiness=readiness,  # 当前验证准备度级别
        )

        # 读取 compare 阶段生成的语义等价摘要。
        dict_equivalence = _read_json_artifact(dict_compare_result["equivalence_path"])  # compare 阶段 equivalence JSON 内容

        # 从 compare 目录读取资源与时序对比结果，供 optimize 摘要复用。
        dict_qor_report = _read_json_artifact(dict_compare_result["qor_report_path"])  # 从 compare 工件回读的 QoR 报告正文

        # 把 compare 生成的语义等价证据挂回 optimize 工件索引。
        dict_artifacts["equivalence"] = dict_compare_result["equivalence_path"]  # compare 阶段 equivalence 工件路径

        # 把 compare 生成的 QoR 报告路径挂回 optimize 工件索引。
        dict_artifacts["qor_report"] = dict_compare_result["qor_report_path"]  # compare 阶段 QoR 报告工件路径

        # 把 compare 产出的接口、checkpoint 与 testbench 结论压缩成验证摘要。
        dict_verification_summary = dict(  # optimize 继承 compare 结论后的验证摘要
            interface_consistent=dict_equivalence["interface_consistent"],  # compare 后接口一致性结论
            checkpoint_consistent=dict_equivalence["checkpoint_consistent"],  # compare 后检查点一致性结论
            testbench_consistent=dict_equivalence["testbench_consistent"],  # compare 后 testbench 一致性结论
            selected_backend=_selected_backend_name(dict_equivalence),  # compare 后最终选中的仿真后端
        )

        # 把 compare 阶段的 QoR 结论裁剪成 gating 真正关心的状态字段。
        dict_qor_summary = dict(  # optimize 继承 compare 结论后的 QoR 摘要
            qor_comparable=dict_qor_report["qor_comparable"],  # optimize 当前是否仍保留参考/候选 QoR 可比性
            status=dict_qor_report["status"],  # optimize 当前继承到的 QoR 主状态
            yosys_stat=dict_qor_report.get("yosys_stat", {}).get("status"),  # optimize 当前继承到的 yosys 统计状态
        )

        # 沿用 compare 阶段已识别的问题列表。
        list_issues = dict_equivalence.get("issues", [])  # compare 阶段输出的问题列表

    # 没有候选 RTL 时，仅保留参考设计上的 advisory 级 QoR 信息。
    else:

        # 没有候选设计时，只生成参考 RTL 一侧的 QoR 建议摘要。
        dict_qor_report = _qor_report(  # 无候选 RTL 时退回生成的单边 advisory QoR 报告正文
            analysis,  # 当前 advisory QoR 报告唯一依赖的参考 RTL 结构分析结果
            None,  # 单参考 advisory 场景没有候选 RTL 分析结果
            run_external=False,  # 当前 advisory QoR 只允许走本地静态摘要，不调用外部工具链
            reference_path=path_baseline_source,  # 单基线 advisory 场景使用的基线 RTL 路径
            candidate_path=None,  # 单参考 advisory 场景没有候选 RTL 路径
        )

        # 输出仅含参考设计的 QoR 报告。
        path_qor = write_json(out_dir / "qor_report.json", dict_qor_report)  # advisory QoR 报告输出路径

        # 把 advisory QoR 报告纳入本阶段工件清单。
        dict_artifacts["qor_report"] = str(path_qor)  # advisory QoR 报告工件路径

        # 把单参考设计场景下仍可计算的 QoR 状态裁剪成摘要。
        dict_qor_summary = dict(  # 没有候选 RTL 时，调用方只能依赖这份单边 QoR 状态切片判断是否还值得继续补候选设计
            qor_comparable=dict_qor_report["qor_comparable"],  # 单参考设计场景下自然会是不可比的 QoR 标记
            status=dict_qor_report["status"],  # 单参考 advisory 场景下的 QoR 主状态结论
            yosys_stat=dict_qor_report.get("yosys_stat", {}).get("status"),  # 单参考 advisory 场景下的 yosys stat 子状态结论
        )

    # 把 ready 结论拆出来，避免在结果字典里堆叠过长布尔表达式。
    bool_has_error_issue = False  # optimize 阶段是否存在 error 级问题

    # 逐项扫描问题列表，只要出现 error 就阻断继续推进。
    for dict_issue in list_issues:

        # 非 error 级问题不影响 ready 判定。
        if dict_issue["severity"] != "error":

            # 当前问题不是阻断项时继续检查下一条。
            continue

        # 命中 error 级问题后立即标记阻断状态。
        bool_has_error_issue = True  # optimize 阶段已经发现 error 级问题

        # 首个 error 已足够决定 ready 结论。
        break

    # 先把是否已有候选 RTL 抽出来，供 ready 与下一步动作共用。
    bool_candidate_provided = path_candidate_source is not None  # optimize 阶段是否已经拿到候选 RTL

    # 只有具备候选 RTL 且不存在阻断问题时，ready 才会向下游开放后续步骤。
    bool_ready = bool_candidate_provided and not bool_has_error_issue  # 写入 optimize transform_validation.ready 的 gating 结论

    # 为推荐动作整理一份扁平化状态摘要，避免内联字典过长。
    dict_next_action_state = dict(  # 驱动 optimize 推荐动作的聚合状态
        interface_consistent=dict_verification_summary["interface_consistent"],  # optimize 当前接口一致性
        checkpoint_consistent=dict_verification_summary["checkpoint_consistent"],  # optimize 当前检查点一致性
        testbench_consistent=dict_verification_summary["testbench_consistent"],  # optimize 当前 testbench 一致性
        qor_comparable=dict_qor_summary["qor_comparable"],  # optimize 当前 QoR 可比性
    )

    # 这里产出的动作建议会直接展示给调用方或上层调度器。
    str_optimize_next_action = _recommended_next_action(  # 返回给调用方的 optimize 下一步建议动作
        dict_next_action_state,  # 推荐动作所依据的聚合状态
        candidate_provided=bool_candidate_provided,  # 推荐动作需要知道本轮是否真的拿到了候选 RTL
    )

    # 把 optimize 场景的 gating 结论写成 transform_validation 摘要。
    dict_transform_validation = {
        "version": 1,  # 标记 optimize transform_validation 的 schema 版本
        "ready": bool_ready,  # 只有具备候选 RTL 且无 error 时才允许继续推进
        "issues": list_issues,  # 调用方后续排查时要看的 optimize 问题清单
        "verification_summary": dict_verification_summary,  # 接口和 testbench 方向的验证结论摘要
        "qor_summary": dict_qor_summary,  # 供 gating 读取的 QoR 结果切片
        "recommended_next_action": str_optimize_next_action,  # 直接指导下一步动作的推荐字符串
    }

    # 把 transform_validation 写回输出目录。
    path_validation = write_json(out_dir / "transform_validation.json", dict_transform_validation)  # 供调用方读取的 optimize validation JSON 文件路径

    # 返回 optimize_assist 阶段的核心工件与分析结果。
    return {
        "status": "planned",
        "transform_plan_path": str(path_transform_plan),
        "transform_validation_path": str(path_validation),
        "artifacts": dict_artifacts,
        "analysis": analysis,
    }

# 生成 merge_assist 模式下的计划、wrapper 与静态校验占位结果。
def _merge_assist(
    source_path: Path,
    *,
    out_dir: Path,
    analysis: dict[str, Any],
    candidate_artifacts_dir: Path | None,
    readiness: str,
) -> dict[str, Any]:
    """生成 merge 辅助计划，保持原始 RTL 只读并引导后续手工拼接。

    参数:
        source_path: 当前参考 RTL 路径，用于记录 merge 计划的来源。
        out_dir: merge_assist 阶段的工件输出目录。
        analysis: 当前 RTL 的结构化分析结果。
        candidate_artifacts_dir: 候选 RTL 工件目录，可为空。
        readiness: 当前验证准备度级别。

    返回:
        包含 transform_plan、transform_validation 与 merge 工件路径的结果字典。
    """

    # 把候选工件目录中的 RTL 路径转换成 merge 计划可直接序列化的字符串列表。
    list_candidate_sources: list[str] = []  # merge 模式下可直接序列化的候选 RTL 路径列表

    # 把候选工件目录里解析出的 RTL 路径顺序转成字符串形式。
    for path_item in _artifact_sources(candidate_artifacts_dir):

        # 记录 merge 计划中可直接写入 JSON 的候选 RTL 路径。
        list_candidate_sources.append(str(path_item))

    # 把 merge_assist 的策略、候选与边界写成一份可审阅计划。
    dict_merge_plan = dict(  # merge_assist 阶段计划摘要字典
        version=1,  # merge 计划版本
        mode="merge_assist",  # 当前 refine 模式
        target_module=analysis["module_info"]["name"],  # merge 目标模块名
        strategy="wrapper_first_recompose",  # merge 采用的拼接策略
        reference_source=str(source_path),  # merge 参考 RTL 路径
        merge_constraints=[  # merge 过程中必须遵守的边界约束
            "preserve the public port contract exactly",  # merge 后必须保持公共端口契约
            "preserve checkpoint visibility and semantic invariants",  # merge 后必须保留检查点可观测性
            "do not overwrite source RTL automatically",  # merge_assist 只生成辅助工件
        ],
        candidate_sources=list_candidate_sources,  # merge 计划里登记的候选 RTL 路径清单
        recommended_templates=[  # merge 计划推荐优先参考的辅助模板清单
            "wrapper_top_stitching",  # 顶层 wrapper 拼接模板
            "equivalence_wrapper_probe",  # 等价性探针 wrapper 模板
            "counter_state_bridge",  # 计数器状态桥接模板
            "phase_output_registering",  # 相位输出寄存模板
        ],
        decomposition_candidates=analysis.get("decomposition_candidates", []),  # merge 计划沿用的分解候选摘要
    )

    # 把 merge 计划写入输出目录。
    path_merge_plan = write_json(out_dir / "merge_plan.json", dict_merge_plan)  # merge 计划输出路径

    # 先给出 wrapper 模板和静态校验占位结果，避免直接改写用户 RTL。
    path_merge_wrapper = out_dir / "merge_wrapper.v"  # merge_assist 先交给人工补线的 wrapper 模板输出路径

    # 立即写出 merge wrapper 骨架，方便人工先补连接关系。
    _write_merge_wrapper(analysis, path_merge_wrapper)

    # 候选 RTL 还没真正比较前，先写一份 planned 状态的等价性占位结果。
    dict_merge_equivalence = dict(  # merge 阶段静态等价性占位摘要字典
        version=1,  # merge 等价占位结果版本
        status="planned",  # merge 当前仍处于计划态
        interface_consistent=True,  # merge 计划要求保持接口一致
        checkpoint_consistent=True,  # merge 计划要求保持检查点一致
        candidate_provided=bool(list_candidate_sources),  # 当前是否已经提供候选 RTL
        recommended_next_action="provide_candidate_rtl_or_review_plan",  # merge 阶段建议动作
    )

    # 把静态等价性占位结果写入输出目录。
    path_merge_equivalence = write_json(out_dir / "merge_equivalence.json", dict_merge_equivalence)  # merge 等价性占位结果输出路径

    # 汇总 merge 阶段当前的准备度与工件列表。
    dict_merge_artifacts = dict(  # merge validation 内嵌的工件索引字典
        merge_plan=str(path_merge_plan),  # merge 计划文档路径
        merge_wrapper=str(path_merge_wrapper),  # merge wrapper 模板路径
        merge_equivalence=str(path_merge_equivalence),  # merge 等价占位结果路径
    )

    # 把 merge 当前准备度、工件索引和下一步动作汇总成 validation 摘要。
    dict_merge_validation = {  # merge 场景写回 transform_validation.json 的占位门禁摘要
        "version": 1,  # merge validation 使用的 schema 版本
        "status": "planned",  # merge 阶段当前仍停留在计划态
        "ready": False,  # merge_assist 默认要求人工审阅后再继续
        "readiness": readiness,  # merge 阶段沿用的验证准备度级别
        "artifacts": dict_merge_artifacts,  # merge 阶段当前已经生成好的工件索引
        "recommended_next_action": "review_merge_plan_and_fill_wrapper_connections",  # 调用方下一步应先审计划再补 wrapper 连线
    }

    # 把 merge validation 结果写入输出目录。
    path_merge_validation = write_json(out_dir / "merge_validation.json", dict_merge_validation)  # merge validation 输出路径

    # 构建统一 transform plan，串联 merge 计划、wrapper 与 validation。
    dict_merge_transform_plan_payload = build_transform_plan(  # merge_assist 返回结果里 transform_plan_path 指向的计划正文
        analysis,  # 当前作为 merge 基线的参考 RTL 结构分析结果
        transform_goal="merge_assist",  # 明确把计划目标固定为 merge_assist
        expected_outputs=[  # 调度器后续需要消费的 merge 工件清单
            {"path": path_merge_plan.relative_to(out_dir).as_posix(), "kind": "merge_plan"},  # merge 计划文档输出项
            {"path": path_merge_wrapper.relative_to(out_dir).as_posix(), "kind": "merge_wrapper"},  # merge wrapper 模板输出项
            {"path": path_merge_validation.relative_to(out_dir).as_posix(), "kind": "merge_validation"},  # merge validation 摘要文件输出项
            {
                "path": path_merge_equivalence.relative_to(out_dir).as_posix(),  # merge 等价占位结果在输出目录中的相对路径
                "kind": "merge_equivalence",  # merge 等价占位工件类型
            },
        ],
    )

    # 把 merge 计划里推荐的模板提升到 transform plan 顶层，方便调度器直读。
    dict_merge_transform_plan_payload["recommended_templates"] = dict_merge_plan["recommended_templates"]  # merge 计划推荐的模板列表

    # 明确标注 merge_assist 只写辅助工件，不自动改写用户 RTL。
    dict_merge_transform_plan_payload["allowed_mutation_scope"] = "assist_only_no_default_rtl_rewrite"  # merge_assist 默认不直接改写用户 RTL

    # 把 merge 场景的统一 transform plan 落盘。
    path_transform_plan = write_json(out_dir / "rtl_transform_plan.json", dict_merge_transform_plan_payload)  # 供调度器消费的 merge transform plan JSON 文件路径

    # 返回 merge_assist 阶段的关键工件集合。
    return {
        "status": "planned",
        "transform_plan_path": str(path_transform_plan),
        "transform_validation_path": str(path_merge_validation),
        "artifacts": {
            "merge_plan": str(path_merge_plan),
            "merge_wrapper": str(path_merge_wrapper),
            "merge_validation": str(path_merge_validation),
            "merge_equivalence": str(path_merge_equivalence),
        },
        "analysis": analysis,
    }

# 规范化 testbench 语言名称，并在非法值时给出统一错误前缀。
def _require_tb_language(tb_language: str) -> str:
    """校验 testbench 语言选项，仅允许 verilog。

    参数:
        tb_language: 用户指定的 testbench 语言名称。

    返回:
        归一化后的 testbench 语言名称。

    异常:
        ValueError: 当语言名称不在允许集合内时抛出。
    """

    # 统一转成小写，便于后续做固定集合判断。
    str_normalized = tb_language.lower()  # 归一化后的 testbench 语言名称

    # 仅允许 Verilog-2001 testbench 方言。
    if str_normalized != "verilog":

        # 语言不在允许集合时，立即返回统一前缀的参数错误。
        raise ValueError(
            "> ERR: [Python] tb_language 仅支持 'verilog'。"
        )

    # 返回归一化后的语言名称。
    return str_normalized

# 基于分析结果生成最小可运行的自检 testbench 脚手架。
def _write_tb_from_analysis(
    analysis: dict[str, Any],
    output_path: Path,
    *,
    tb_language: str,
) -> None:
    """基于分析结果生成最小可运行的自检 testbench 脚手架。

    参数:
        analysis: RTL 结构化分析结果。
        output_path: 生成 testbench 文件的输出路径。
        tb_language: 目标 testbench 语言，当前仅支持 verilog。

    返回:
        None。函数会直接把 testbench 内容写入输出文件。
    """

    # 规范化 testbench 语言，避免大小写差异影响分支判断。
    str_tb_language = _require_tb_language(tb_language)  # 归一化后的 testbench 语言

    # 提取顶层模块名，供模块实例与日志输出复用。
    str_module_name = str(analysis["module_info"]["name"])  # 顶层模块名称

    # 读取端口定义列表，后续统一生成声明与连接。
    list_ports = analysis["ports"]  # 顶层端口定义列表

    # 仅截取前几个 checkpoint，避免脚手架日志过长。
    list_checkpoints = analysis.get("verification_targets", [])[:4]  # 截断后的 checkpoint 列表

    # 先搭出 testbench 的说明头、模块头和最小常量定义。
    list_lines = [  # 依次写出 testbench 说明头、模块头和默认时钟周期常量
        f"// Analysis-derived self-checking scaffold for {str_module_name}",  # testbench 说明头文本
        f"module tb_{str_module_name};",  # testbench 模块声明头
        "    localparam CLK_PERIOD = 10;",  # 默认时钟周期常量
        "",  # 头部常量区与后续声明区之间的空行
    ]

    # 追加端口驱动/观测声明。
    list_lines.extend(_tb_declaration_lines(list_ports))

    # 追加 DUT 实例化模板。
    list_lines.extend(_tb_instance_lines(str_module_name, list_ports))

    # 追加自动识别到的时钟翻转驱动。
    list_lines.extend(_tb_clock_driver_lines(list_ports))

    # 追加脚手架启动横幅，方便判断 testbench 是否被执行。
    list_lines.extend(_tb_startup_banner_lines())

    # 追加 initial 主激励序列与收尾日志。
    list_lines.extend(
        _tb_initial_sequence_lines(
            list_ports,
            list_checkpoints,
            tb_language=str_tb_language,
        )
    )

    # 把拼好的 testbench 文本一次性写到目标文件。
    write_text(output_path, "\n".join(list_lines))

# 根据端口方向生成 testbench 驱动/观测声明。
def _tb_declaration_lines(list_ports: list[dict[str, Any]]) -> list[str]:
    """为 testbench 生成 reg/wire 端口声明文本。

    参数:
        list_ports: 顶层端口定义列表。

    返回:
        适合直接拼接进 testbench 的 reg/wire 声明文本列表。
    """

    # 准备 testbench 端口声明文本缓冲区。
    list_lines: list[str] = []  # testbench 端口声明文本列表

    # 逐个端口生成 reg/wire 声明。
    for dict_port in list_ports:

        # 先把宽度归一化成整数。
        int_width = int(dict_port.get("width") or 1)  # 当前端口位宽

        # 再拼接 Verilog 位宽切片文本。
        str_width_text = "" if int_width <= 1 else f" [{int_width - 1}:0]"  # 当前端口位宽文本

        # 输出端口作为观测对象，其余端口默认用 reg 驱动。
        str_storage_kind = "wire" if dict_port["direction"] == "output" else "reg"  # 当前端口在 testbench 中的存储类型

        # 把当前端口对应的 reg/wire 声明追加到结果列表。
        list_lines.append(f"    {str_storage_kind}{str_width_text} {dict_port['name']};")

    # 返回端口声明文本列表。
    return list_lines

# 生成 DUT 例化片段，保持端口一一映射。
def _tb_instance_lines(
    str_module_name: str,
    list_ports: list[dict[str, Any]],
) -> list[str]:
    """生成 DUT 例化所需的文本行。

    参数:
        str_module_name: 顶层模块名称。
        list_ports: 顶层端口定义列表。

    返回:
        适合直接拼接进 testbench 的 DUT 例化文本列表。
    """

    # 先收集 `.port(signal)` 形式的端口映射片段，再统一拼接成多行文本。
    list_port_map_lines: list[str] = []  # DUT 例化里逐个端口的映射文本片段

    # 按端口顺序生成 `.port(signal)` 形式的映射文本。
    for dict_port in list_ports:

        # 记录当前端口对应的 DUT 命名映射片段。
        list_port_map_lines.append(f"        .{dict_port['name']}({dict_port['name']})")

    # 再把端口映射片段拼成逗号换行分隔的例化正文。
    str_port_map = ",\n".join(list_port_map_lines)  # DUT 例化使用的端口映射文本

    # 返回 DUT 实例化文本。
    return [
        "",
        f"    {str_module_name} DUT_Inst (",
        str_port_map,
        "    );",
        "",
    ]

# 为识别到的时钟端口生成自动翻转驱动。
def _tb_clock_driver_lines(list_ports: list[dict[str, Any]]) -> list[str]:
    """生成 testbench 的 always 时钟驱动文本。

    参数:
        list_ports: 顶层端口定义列表。

    返回:
        针对 role=clock 端口生成的 always 驱动文本列表。
    """

    # 准备时钟驱动文本缓冲区。
    list_lines: list[str] = []  # always 时钟驱动文本列表

    # 只为 role=clock 的端口生成翻转逻辑。
    for dict_port in list_ports:

        # 仅当端口被标记为 clock 时才追加翻转驱动。
        if dict_port.get("role") == "clock":

            # 把当前时钟端口的翻转驱动写入结果列表。
            list_lines.append(
                f"    always #(CLK_PERIOD/2) {dict_port['name']} = ~{dict_port['name']};"
            )

    # 返回时钟驱动文本。
    return list_lines

# 生成脚手架启动横幅，帮助人工确认 testbench 已执行。
def _tb_startup_banner_lines() -> list[str]:
    """返回固定的启动横幅文本。

    参数:
        无业务参数；函数直接返回固定启动横幅片段。

    返回:
        testbench 启动时打印的一组固定文本行。
    """

    # 返回脚手架启动 initial 块。
    return [
        "",
        "    initial begin",
        (
            '        $display(" > INFO: [Verilog] [TB_MONITOR] Time: %0t | '
            'Starting analysis-derived verification.", $time);'
        ),
        "    end",
        "",
    ]

# 按角色或方向挑选默认端口名，供 testbench 断言和日志片段共用。
def _tb_preferred_port_name(
    list_ports: list[dict[str, Any]],
    *,
    role: str | None = None,
    direction: str | None = None,
    default: str | None = None,
) -> str | None:
    """返回符合条件的首个端口名。

    参数:
        list_ports: 顶层端口定义列表。
        role: 期望匹配的角色标签，可为空。
        direction: 期望匹配的方向标签，可为空。
        default: 未匹配到端口时返回的默认名称。

    返回:
        命中的端口名称；未命中时返回 default。
    """

    # 逐个端口按 role/direction 过滤，命中后立即返回名称。
    for dict_port in list_ports:

        # role 已指定时，只保留角色与目标一致的端口。
        if role is not None and dict_port.get("role") != role:

            # 当前端口角色不匹配时，继续检查下一个端口。
            continue

        # direction 已指定时，只保留方向与目标一致的端口。
        if direction is not None and dict_port.get("direction") != direction:

            # 当前端口方向不匹配时，继续检查下一个端口。
            continue

        # 第一个同时满足条件的端口直接返回其名称。
        return str(dict_port["name"])

    # 没有命中时，回退到调用方提供的默认名称。
    return default

# 生成 Verilog 模式的占位检查片段，提醒用户补全模块专属期望。
def _tb_verilog_placeholder_lines() -> list[str]:
    """返回纯 Verilog 模式下的占位检查文本。

    参数:
        无业务参数；函数直接返回固定占位片段。

    返回:
        Verilog 模式追加到 initial 序列末尾的文本列表。
    """

    # 纯 Verilog 模式无法直接使用 SV assertion，因此只给出占位提醒。
    return [
        "        if (^1'b0 === 1'b1) begin",
        (
            '            $error("[TB_ERROR] Time: %0t | Replace scaffold checks with '
            'module-specific expectations.", $time);'
        ),
        '            $display(" > ERR: [Verilog] replace scaffold checks with module-specific expectations.");',
        "        end",
    ]

# 生成 testbench 的 initial 主激励、日志与收尾片段。
def _tb_initial_sequence_lines(
    list_ports: list[dict[str, Any]],
    list_checkpoints: list[dict[str, Any]],
    *,
    tb_language: str,
) -> list[str]:
    """生成 initial 主序列的所有文本行。

    参数:
        list_ports: 顶层端口定义列表。
        list_checkpoints: 需要打印的 checkpoint 摘要列表。
        tb_language: 目标 testbench 语言名称。

    返回:
        initial 主激励、日志与收尾阶段对应的文本行列表。
    """

    # 准备 initial 主序列文本缓冲区。
    list_lines = ["", "    initial begin"]  # initial 主序列文本列表

    # 先为所有输入端口写入零值初始态。
    for dict_port in list_ports:

        # 输出端口由 DUT 驱动，不在 initial 中赋初值。
        if dict_port["direction"] != "output":

            # 输入端口位宽决定零值字面量格式。
            int_width = int(dict_port.get("width") or 1)  # 当前输入端口位宽

            # 把位宽换算成匹配当前输入端口的零值字面量。
            str_zero_value = "1'b0" if int_width == 1 else f"{int_width}'b0"  # 当前输入端口的零值文本

            # 把当前输入端口的零值初始化写入 initial 块。
            list_lines.append(f"        {dict_port['name']} = {str_zero_value};")

    # 如果识别到复位端口，则自动给出最小复位脉冲。
    if any(dict_port.get("role") == "reset" for dict_port in list_ports):

        # 取首个 reset 角色端口作为脚手架复位对象。
        dict_reset_port = next(  # 识别到的复位端口定义
            dict_port for dict_port in list_ports if dict_port.get("role") == "reset"  # 脚手架使用的复位端口
        )

        # 先把复位端口名转成小写，便于按后缀推断有效电平。
        str_reset_name = str(dict_reset_port["name"]).lower()  # 小写化后的复位端口名

        # 再根据命名后缀推断复位有效电平。
        str_active_value = "1'b0" if str_reset_name.endswith("n") else "1'b1"  # 复位有效电平文本

        # 反向得到复位释放后的无效电平。
        str_inactive_value = "1'b1" if str_active_value == "1'b0" else "1'b0"  # 复位无效电平文本

        # 写入复位拉低/释放序列，形成最小复位脉冲。
        list_lines.extend(
            [
                f"        {dict_reset_port['name']} = {str_active_value};",
                "        #(CLK_PERIOD * 2);",
                f"        {dict_reset_port['name']} = {str_inactive_value};",
            ]
        )

    # 为 checkpoint 追加最小监视日志，帮助人工对齐观察点。
    for dict_target in list_checkpoints:

        # 先拼出当前 checkpoint 关心的信号列表。
        str_signal_list = ",".join(dict_target.get("signals", []))  # 当前 checkpoint 关心的信号列表文本

        # 再取出日志里要打印的 checkpoint 标识。
        str_check_id = str(dict_target["check_id"])  # 当前 checkpoint 标识

        # 把当前 checkpoint 的监视日志追加到 initial 序列。
        list_lines.append(
            f'        $display(" > INFO: [Verilog] [TB_MONITOR] Time: %0t | '
            f'{str_check_id} | signals={str_signal_list}", $time);'
        )

    # 抽取一个输出端口，供观测日志与未知态检查共用。
    str_output_signal = _tb_preferred_port_name(  # 脚手架默认观测的输出信号名称
        list_ports, direction="output", default=None  # 观测日志默认输出端口选择条件
    )

    # 找到输出端口时，补一条观测日志。
    if str_output_signal:

        # 输出当前默认观测信号的运行时数值。
        list_lines.append(
            f'        $display(" > INFO: [Verilog] [TB_DATA] Time: %0t | '
            f'Observed {str_output_signal}=%0h", $time, {str_output_signal});'
        )

    # 固定输出脚手架执行结果。
    str_result_banner = (  # 统一的 VERILOG-GEN-RESULT 输出文本
        '        $display("VERILOG-GEN-RESULT {\\"case_id\\":\\"analysis_scaffold\\",'
        '\\"status\\":\\"PASS\\",\\"outputs\\":{},\\"checkpoints\\":{\\"phase\\":\\"analysis\\"}}");'
    )

    # 先输出一条简洁 PASS 日志，提示脚手架主序列已跑通。
    list_lines.append('        $display(" > INFO: [Verilog] analysis-derived scaffold executed.");')

    # 再输出机器可解析的 VERILOG-GEN-RESULT 横幅。
    list_lines.append(str_result_banner)

    # Verilog 脚手架仅提示用户补全模块专属检查。
    list_lines.extend(_tb_verilog_placeholder_lines())

    # 最后追加统一的仿真收尾语句。
    list_lines.extend(
        [
            "        #(CLK_PERIOD * 4);",
            '        $display(" > INFO: [Verilog] [TB_INFO] Simulation Finished!");',
            "        $finish;",
            "    end",
            "endmodule",
            "",
        ]
    )

    # 返回 initial 主序列文本。
    return list_lines

# 统一生成 wrapper module 头中的端口声明文本，避免多个辅助模式重复拼装。
def _wrapper_port_lines(list_ports: list[dict[str, Any]]) -> list[str]:
    """返回 wrapper/module 头使用的端口声明文本列表。

    参数:
        list_ports: 顶层端口定义列表。

    返回:
        适合直接拼接进 module 头的端口声明文本列表。
    """

    # 这里缓存的是 wrapper 模块头最终会逐行展开的端口声明文本。
    list_port_lines: list[str] = []  # 按 module 头顺序累积的 wrapper 端口声明文本

    # 逐个端口生成 direction/width/name 形式的声明文本。
    for int_index, dict_port in enumerate(list_ports):

        # 端口未显式声明位宽时，默认按单比特端口生成 wrapper 声明。
        int_width = int(dict_port.get("width") or 1)  # 为当前端口规整出的数值位宽

        # 这里把整数位宽转换成 Verilog 端口声明可直接复用的切片前缀。
        str_width_text = "" if int_width <= 1 else f"[{int_width - 1}:0] "  # 当前端口在 module 头中的位宽切片文本

        # 这里负责控制 module 头每一行后面是否还需要继续拼接下一个端口。
        str_trailing = "," if int_index < len(list_ports) - 1 else ""  # 当前端口声明尾部分隔符

        # 把当前端口的 direction/width/name 声明压入 module 头文本列表。
        list_port_lines.append(
            f"    {dict_port['direction']} {str_width_text}{dict_port['name']}{str_trailing}"
        )

    # 返回 wrapper/module 头所需的端口声明文本。
    return list_port_lines

# 生成 partition-assist wrapper 骨架，帮助后续人工拆分边界信号。
def _write_partition_wrapper(analysis: dict[str, Any], output_path: Path) -> None:
    """根据分解候选生成 partition-assist wrapper 骨架。

    参数:
        analysis: RTL 结构化分析结果。
        output_path: wrapper 文件输出路径。

    返回:
        None。函数会把 partition-assist wrapper 骨架直接写入 output_path。
    """

    # 读取原始模块名，稍后会拼成 `top_<module>` 形式的 partition wrapper 标识。
    str_module_name = str(analysis["module_info"]["name"])  # 将被拼成 top_<module> 的原始模块名

    # 这些顶层端口稍后会原样透传到 partition wrapper 外围接口。
    list_ports = analysis["ports"]  # 需要透传到 partition wrapper 的顶层端口列表

    # 提前把顶层端口整理成 partition wrapper 模块头可直接复用的声明行。
    list_port_lines = _wrapper_port_lines(list_ports)  # 可直接写入 partition wrapper 头部的端口声明文本

    # 先搭出 wrapper 的模块头和内部信号注释区。
    list_lines = [  # partition wrapper 头部与内部边界提示文本
        f"// Partition-assist wrapper skeleton for {str_module_name}",  # partition wrapper 的英文说明头文本
        f"module top_{str_module_name}(",  # partition wrapper 模块头起始行
        "\n".join(list_port_lines),  # partition wrapper 顶层端口声明区
        ");",  # partition wrapper 模块头结束行
        "",  # 模块头与内部边界信号说明区之间的空行
        "    // Internal boundary signals inferred from structural analysis.",  # partition wrapper 内部边界信号说明头
    ]  # partition wrapper 文本行列表

    # 记录已经声明过的内部边界信号，避免重复定义。
    set_seen_internal: set[str] = set()  # 已声明内部边界信号集合

    # 顶层端口名称集合用于过滤无需重复声明的边界信号。
    set_port_names = {dict_port["name"] for dict_port in list_ports}  # 顶层端口名称集合

    # 为每个分解候选补充内部边界 wire 声明。
    for dict_candidate in analysis["decomposition_candidates"]:

        # 逐个检查该候选暴露出来的边界信号。
        for str_signal in dict_candidate["boundary_signals"]:

            # 已经是顶层端口或已声明过的信号不再重复生成 wire。
            if str_signal in set_port_names or str_signal in set_seen_internal:

                # 当前边界信号无需重复声明时，继续检查下一个信号。
                continue

            # 先记录这个内部边界信号，避免后续候选重复声明。
            set_seen_internal.add(str_signal)

            # 把当前边界信号生成为 wrapper 内部 wire。
            list_lines.append(f"    wire {str_signal};")

    # 内部信号声明之后留一行空白，再写各子块说明。
    list_lines.append("")

    # 为每个分解候选追加角色、行号和边界信号说明。
    for dict_candidate in analysis["decomposition_candidates"]:

        # 追加当前候选的职责、边界信号和人工跟进提示。
        list_lines.extend(
            [
                (
                    f"    // {dict_candidate['module_name']} handles {dict_candidate['role']} "
                    f"lines {dict_candidate['line_range'][0]}-{dict_candidate['line_range'][1]}."
                ),
                (
                    f"    // Boundary signals: "
                    f"{', '.join(dict_candidate['boundary_signals']) or 'none detected'}."
                ),
                "    // Human follow-up should preserve the semantic invariants recorded in rtl_transform_plan.json.",
                "",
            ]
        )

    # 结束 wrapper 模块并写回文件。
    list_lines.append("endmodule")

    # 把 partition wrapper 文本整体写入目标文件。
    write_text(output_path, "\n".join(list_lines) + "\n")

# 生成 merge-assist wrapper 骨架，提醒人工按 merge 计划做拼接。
def _write_merge_wrapper(analysis: dict[str, Any], output_path: Path) -> None:
    """根据分析结果生成 merge-assist wrapper 骨架。

    参数:
        analysis: RTL 结构化分析结果。
        output_path: wrapper 文件输出路径。

    返回:
        None。函数会把 merge-assist wrapper 骨架直接写入 output_path。
    """

    # merge wrapper 名称会直接派生自原始模块名。
    str_module_name = str(analysis["module_info"]["name"])  # 供 merge wrapper 命名与提示文本复用的原始模块名

    # merge wrapper 外部接口必须沿用原设计的同一组顶层端口。
    list_ports = analysis["ports"]  # merge wrapper 需要原样保留的顶层端口列表

    # 这里先准备 merge wrapper 模块头前半段，端口声明稍后再插入。
    list_lines = [  # merge wrapper 模块头起始文本与后续占位说明入口
        f"// Merge-assist wrapper skeleton for {str_module_name}",  # 写入 merge wrapper 文件首行的英文提示
        f"module merge_{str_module_name}(",  # merge wrapper 顶层 module 声明头
    ]

    # 提前把顶层端口整理成 merge wrapper 模块头可直接拼接的声明区。
    list_port_lines = _wrapper_port_lines(list_ports)  # 可直接插入 merge module 头的端口声明文本

    # 先把端口声明拼进 merge wrapper 的 module 头。
    list_lines.append("\n".join(list_port_lines))

    # 再追加人工拼接时需要关注的提示文本。
    list_lines.extend(
        [
            ");",
            "",
            "    // Stitch candidate sub-blocks here after reviewing merge_plan.json.",
            "    // Preserve public ports and semantic checkpoints while reconnecting partitions.",
            "",
            f"    // Original top-level reference: {str_module_name}",
            "endmodule",
            "",
        ]
    )

    # 把 merge wrapper 正文写入目标文件，供后续人工补全连接关系。
    write_text(output_path, "\n".join(list_lines))

# 生成 style improve 指南，提醒调用方只做风格层精修而不改语义。
def _write_style_improve_guide(analysis: dict[str, Any], output_path: Path) -> None:
    """根据分析结果生成 style improve 指南文档。

    参数:
        analysis: RTL 结构化分析结果。
        output_path: style improve 指南输出路径。

    返回:
        None。函数会直接把指南文档写入目标文件。
    """

    # 先构造 guide 的固定段落，明确需要保留的语义边界。
    list_lines = [
        "# Style Refine Guide",  # 指南标题
        "",  # 标题与正文之间的空行
        f"Target module: `{analysis['module_info']['name']}`",  # style improve 面向的目标模块名
        "",  # 模块名与保留项标题之间的空行
        "## Preserve",  # 保留约束标题
        "- Public port names, widths, and directions.",  # 端口契约保持不变
        "- Reset behavior and sequential state initialization.",  # 复位语义保持不变
        "- Verification targets captured in `rtl_analysis.json`.",  # 校验目标保持不变
        "",  # 保留项与建议项之间的空行
        "## Suggested style improvements",  # 风格建议标题
    ]  # style improve 指南正文行列表

    # 逐个 always block 追加风格隔离建议，避免后续精修时再次混叠职责。
    for dict_block in analysis["always_blocks"]:

        # 追加当前 always block 的风格精修建议行。
        list_lines.append(
            f"- `{dict_block['block_id']}`: keep `{dict_block['role']}` logic isolated and well-commented."
        )

    # 把拼装好的 style improve 指南写回目标文件。
    write_text(output_path, "\n".join(list_lines) + "\n")

# 生成 optimize_assist 的文本计划，帮助人工理解优化关注点。
def _write_optimization_plan(analysis: dict[str, Any], output_path: Path) -> None:
    """根据分析结果生成 optimization assist 计划文档。

    参数:
        analysis: RTL 结构化分析结果。
        output_path: optimization plan 输出路径。

    返回:
        None。函数会直接把计划文档写入目标文件。
    """

    # 先构造优化计划的标题与固定章节。
    list_lines = [
        "# Optimization Assist Plan",  # 计划标题
        "",  # 标题与模块名之间的空行
        f"Target module: `{analysis['module_info']['name']}`",  # optimize 计划关注的目标模块名
        "",  # 模块名与优化目标标题之间的空行
        "## Candidate optimization targets",  # 优化目标标题
    ]  # 优化计划正文行列表

    # 逐条写出结构分析阶段提取出的优化目标。
    for dict_target in _optimization_targets(analysis):

        # 追加当前优化目标的摘要行。
        list_lines.append(f"- `{dict_target['id']}`: {dict_target['text']}")

    # 在优化目标后追加 QoR 小节标题。
    list_lines.extend(["", "## QoR objectives"])

    # 逐条写出当前分析结果对应的 QoR 关注点。
    for dict_objective in _qor_objectives(analysis):

        # 追加当前 QoR 目标的摘要行。
        list_lines.append(f"- `{dict_objective['id']}`: {dict_objective['text']}")

    # 把优化计划文档写回目标文件。
    write_text(output_path, "\n".join(list_lines) + "\n")

# 比较参考设计与候选设计的接口契约差异，并输出问题列表。
def _interface_issues(
    reference: dict[str, Any],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    """收集参考 RTL 与候选 RTL 的接口差异问题。

    参数:
        reference: 参考 RTL 的结构化分析结果。
        candidate: 候选 RTL 的结构化分析结果。

    返回:
        接口差异问题列表；没有差异时返回空列表。
    """

    # 把参考设计端口表转成按名称索引的字典，便于逐项比较。
    dict_reference_ports = {item["name"]: item for item in reference["ports"]}  # 参考设计端口索引

    # 把候选设计端口表转成按名称索引的字典，便于快速定位对应端口。
    dict_candidate_ports = {item["name"]: item for item in candidate["ports"]}  # 候选设计端口索引

    # 初始化接口差异问题列表。
    list_issues: list[dict[str, Any]] = []  # 接口差异问题列表

    # 逐个参考端口检查候选实现是否保持同名、同方向、同位宽。
    for str_port_name, dict_reference_port in dict_reference_ports.items():

        # 读取当前参考端口在候选实现中的同名端口定义。
        dict_candidate_port = dict_candidate_ports.get(str_port_name)  # 当前端口在候选设计中的定义

        # 缺失同名端口时，直接登记阻断性接口问题。
        if dict_candidate_port is None:

            # 记录候选实现缺失端口的错误。
            list_issues.append(
                {
                    "severity": "error",  # 缺失端口属于阻断问题
                    "source": "current_module_issue",  # 当前问题来自候选设计缺失端口
                    "message": f"Missing candidate port `{str_port_name}`.",  # 缺失端口说明
                }
            )

            # 当前端口已无法继续做方向和位宽比较，直接进入下一项。
            continue

        # 当端口方向被改动时，登记接口方向漂移问题。
        if dict_reference_port.get("direction") != dict_candidate_port.get("direction"):

            # 记录端口方向变化导致的错误。
            list_issues.append(
                {
                    "severity": "error",  # 方向变化属于阻断问题
                    "source": "current_module_issue",  # 当前问题来自端口方向漂移
                    "message": f"Port `{str_port_name}` direction changed.",  # 方向变化说明
                }
            )

        # 当端口位宽被改动时，登记接口位宽漂移问题。
        if int(dict_reference_port.get("width") or 1) != int(
            dict_candidate_port.get("width") or 1
        ):

            # 记录端口位宽变化导致的错误。
            list_issues.append(
                {
                    "severity": "error",  # 位宽变化属于阻断问题
                    "source": "current_module_issue",  # 当前问题来自端口位宽漂移
                    "message": f"Port `{str_port_name}` width changed.",  # 位宽变化说明
                }
            )

    # 找出仅存在于候选实现中的新增端口。
    list_extra_ports = sorted(set(dict_candidate_ports) - set(dict_reference_ports))  # 候选实现新增端口列表

    # 对新增端口逐一登记 warning，提醒人工确认意图。
    for str_port_name in list_extra_ports:

        # 记录候选实现引入新增端口的告警。
        list_issues.append(
            {
                "severity": "warning",  # 新增端口先按告警处理
                "source": "current_module_issue",  # 当前问题来自候选设计新增端口
                "message": f"Candidate introduced extra port `{str_port_name}`.",  # 新增端口说明
            }
        )

    # 返回汇总后的接口差异问题列表。
    return list_issues

# 比较参考设计与候选设计的 checkpoint 覆盖是否发生漂移。
def _checkpoint_issues(
    reference: dict[str, Any],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    """收集参考 RTL 与候选 RTL 的 checkpoint 漂移问题。

    参数:
        reference: 参考 RTL 的结构化分析结果。
        candidate: 候选 RTL 的结构化分析结果。

    返回:
        checkpoint 漂移问题列表；没有漂移时返回空列表。
    """

    # 把参考设计 checkpoint 集合规范化为可比较的元组集合。
    set_reference_targets = {
        (item["category"], tuple(item["signals"]))  # 单个参考 checkpoint 的分类与信号元组
        for item in reference["verification_targets"]  # 遍历参考设计的全部验证目标
    }  # 参考设计 checkpoint 集合

    # 把候选设计 checkpoint 集合规范化为可比较的元组集合。
    set_candidate_targets = {
        (item["category"], tuple(item["signals"]))  # 单个候选 checkpoint 的分类与信号元组
        for item in candidate["verification_targets"]  # 遍历候选设计的全部验证目标
    }  # 候选设计 checkpoint 集合

    # 两侧集合一致时，不需要登记任何问题。
    if set_reference_targets == set_candidate_targets:

        # checkpoint 完全对齐时直接返回空问题列表。
        return []

    # 返回一条覆盖漂移告警，提示人工补查 checkpoint 对齐情况。
    return [
        {
            "severity": "warning",  # checkpoint 漂移先按告警处理
            "source": "testbench_issue",  # 问题来源于验证目标
            "message": "Verification target or checkpoint coverage drifted between reference and candidate RTL.",  # 覆盖漂移说明
        }
    ]

# 记录本次语义比较尝试过的仿真后端及其选中状态。
def _simulator_backend_attempts(*, run_external: bool) -> list[dict[str, Any]]:
    """构造语义比较阶段的仿真后端尝试列表。

    参数:
        run_external: 是否允许访问外部工具链。

    返回:
        按优先顺序排列的后端尝试列表。
    """

    # 允许外部工具时读取仿真器选择结果，否则构造静态占位结果。
    dict_selection = (
        _select_simulator_backend(_simulator_config())  # 允许外部工具时执行真实后端选择
        if run_external  # 仅在允许访问外部工具链时做探测
        else {"backend": None, "missing_preferred": []}  # 静态模式下返回空选择结果
    )  # 仿真器选择结果

    # 提取当前真正被选中的后端名称。
    str_selected_name = (
        dict_selection["backend"]["name"] if dict_selection.get("backend") else None  # 从选择结果里提取后端名称
    )  # 被选中的仿真后端名称

    # 初始化后端尝试结果列表。
    list_attempts: list[dict[str, Any]] = []  # 后端尝试结果列表

    # 按固定顺序登记各后端的工具信息与选中状态。
    for str_backend_name in ["xsim", "vcs_verdi", "iverilog"]:

        # 收集当前后端依赖的工具名称列表。
        list_tools = list(_backend_tools(str_backend_name))  # 当前后端声明依赖的工具列表

        # 查找当前后端缺失的首选工具列表。
        list_missing_tools: list[str] = []  # 当前后端在本轮探测里累计出的缺失首选工具列表

        # 在缺失首选工具清单里查找与当前后端同名的记录。
        for dict_missing_backend in dict_selection.get("missing_preferred", []):

            # 名称不匹配时继续检查下一个后端记录。
            if dict_missing_backend.get("name") != str_backend_name:

                # 当前缺失记录不属于正在处理的后端。
                continue

            # 命中后端名称时提取缺失工具集合。
            list_missing_tools = dict_missing_backend.get("missing_tools", [])  # 当前后端缺失的首选工具清单

            # 找到匹配记录后无需继续扫描其他后端条目。
            break

        # 按运行许可和缺失情况决定当前后端状态。
        str_status = (
            "selected"  # 当前后端就是最终选中的执行后端
            if str_backend_name == str_selected_name  # 与最终选中的后端名称一致
            else "unavailable" if list_missing_tools or not run_external else "not_selected"  # 否则按可用性给出状态
        )  # 当前后端状态

        # 把当前后端状态登记到尝试结果列表。
        list_attempts.append(
            {
                "name": str_backend_name,  # 当前后端名称
                "tools": list_tools,  # 当前后端对应的依赖工具清单
                "missing_tools": list_missing_tools,  # 当前后端实际缺失的工具清单
                "status": str_status,  # 当前后端在本轮探测中的选中状态
            }
        )

    # 返回完整的后端尝试结果列表。
    return list_attempts

# 对比参考设计和候选设计的 testbench checkpoint 一致性。
def _testbench_consistency(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    *,
    run_external: bool,
    backend_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """收集 testbench checkpoint 一致性摘要。

    参数:
        reference: 参考 RTL 的结构化分析结果。
        candidate: 候选 RTL 的结构化分析结果。
        run_external: 是否允许访问外部工具链。
        backend_attempts: 已登记的仿真后端尝试列表。

    返回:
        包含一致性结论、已选后端和语义用例结果的摘要字典。
    """

    # 提取参考设计中登记的 checkpoint 标识序列。
    list_reference_ids = [item["check_id"] for item in reference["verification_targets"]]  # 参考设计 checkpoint 标识列表

    # 提取候选设计中登记的 checkpoint 标识序列。
    list_candidate_ids = [item["check_id"] for item in candidate["verification_targets"]]  # 候选设计 checkpoint 标识列表

    # 用完全相同的标识序列判断 checkpoint 是否一致。
    bool_consistent = list_reference_ids == list_candidate_ids  # 参考与候选 checkpoint 序列是否完全一致

    # 提取真正被选中的仿真后端名称。
    str_selected_backend = next(  # 选出 compare 阶段真正采用的仿真后端
        (item["name"] for item in backend_attempts if item["status"] == "selected"),  # 找出状态为 selected 的后端名称
        None,  # 未选中任何后端时返回空值
    )  # 已选仿真后端名称

    # 提取已选后端对应的工具列表，缺失时保留空列表。
    list_available_tools = next(  # 提取已选后端实际可用的工具清单
        (item["tools"] for item in backend_attempts if item["status"] == "selected"),  # 读取已选后端关联的工具列表
        [],
    )  # 已选仿真后端工具列表

    # 为每个参考 checkpoint 生成一条 PASS/WARN 结果。
    list_semantic_case_results = [
        {
            "case_id": str_case_id,  # 当前语义检查结果对应的 checkpoint 标识
            "status": "PASS" if bool_consistent else "WARN",  # 当前 checkpoint 在 compare 阶段的语义状态
        }
        for str_case_id in list_reference_ids  # 遍历参考设计里的全部 checkpoint 标识
    ]  # checkpoint 语义结果列表

    # 根据一致性结果选择更贴切的摘要消息。
    str_message = (
        "Matched analysis-derived testbench checkpoints."  # checkpoint 全部对齐时返回成功摘要
        if bool_consistent  # 根据一致性结果选择摘要消息
        else "Analysis-derived testbench checkpoints differ."  # checkpoint 漂移时返回告警摘要
    )  # testbench 一致性摘要消息

    # 返回 testbench 一致性摘要。
    return {
        "consistent": bool_consistent,  # 本轮 checkpoint 是否保持完全一致
        "selected_backend": str_selected_backend,  # 当前 compare 阶段最终选中的仿真后端
        "available_tools": list_available_tools,  # 最终选中后端对应的工具清单
        "semantic_case_results": list_semantic_case_results,  # 基于 checkpoint 序列构造的语义结果列表
        "message": str_message,  # 对齐结果对应的简洁摘要文本
        "run_external": run_external,  # 本轮 compare 是否允许调用外部工具
    }

# 生成 QoR 对比摘要，必要时附带 yosys stat 结果。
def _qor_report(
    reference: dict[str, Any],
    candidate: dict[str, Any] | None,
    *,
    run_external: bool,
    reference_path: Path,
    candidate_path: Path | None,
) -> dict[str, Any]:
    """收集参考设计与候选设计的 QoR 摘要。

    参数:
        reference: 参考 RTL 的结构化分析结果。
        candidate: 候选 RTL 的结构化分析结果；缺失时仅输出参考摘要。
        run_external: 是否允许访问外部工具链。
        reference_path: 参考 RTL 源文件路径。
        candidate_path: 候选 RTL 源文件路径，可为空。

    返回:
        包含结构规模、接口代价与 yosys 状态的 QoR 摘要字典。
    """

    # 先构造不依赖外部工具的基础 QoR 摘要。
    dict_report = {  # QoR 结构化摘要
        "version": 1,  # QoR 报告版本
        "status": "skipped",  # 默认状态
        "qor_comparable": candidate is not None,  # 是否具备参考/候选对比条件
        "area_like_signals": {  # 参考设计与候选设计的面积近似信号指标
            "reference": _area_like_signals(reference),  # 参考设计面积近似指标
            "candidate": _area_like_signals(candidate) if candidate else None,  # 候选设计面积近似指标
        },
        "sequential_elements": {  # 参考设计与候选设计的时序元素数量
            "reference": len(reference["state_elements"]),  # 参考设计时序元素数量
            "candidate": len(candidate["state_elements"]) if candidate else None,  # 候选设计时序元素数量
        },
        "always_block_count": {  # 参考设计与候选设计的 always 块数量
            "reference": len(reference["always_blocks"]),  # 参考设计 always 块数量
            "candidate": len(candidate["always_blocks"]) if candidate else None,  # 候选设计 always 块数量
        },
        "interface_cost_markers": {  # 参考设计与候选设计的接口代价标记
            "reference": _interface_cost_markers(reference),  # 参考设计接口代价标记
            "candidate": _interface_cost_markers(candidate) if candidate else None,  # 候选设计接口代价标记
        },
        "yosys_stat": {"status": "not_run"},  # 默认不运行 yosys
    }  # QoR 摘要字典

    # 具备工具链和候选路径时，再补一轮 yosys stat 结果。
    if run_external and shutil.which("yosys") and candidate_path is not None:

        # 运行 yosys stat 并覆盖默认占位结果。
        dict_report["yosys_stat"] = _yosys_stat(reference_path, candidate_path)  # 实际执行后的 yosys 统计结果

    # 根据 yosys 结果决定 QoR 报告状态。
    dict_report["status"] = (
        "available"  # yosys 成功返回统计结果时标记为 available
        if dict_report["yosys_stat"]["status"] == "available"  # 仅当 yosys 状态可用时升级报告状态
        else "skipped"  # 其他情况仍保留 skipped 状态
    )

    # 返回完整的 QoR 摘要字典。
    return dict_report

# 调用 yosys stat 并把文本输出收敛为字典摘要。
def _yosys_stat(reference_path: Path, candidate_path: Path) -> dict[str, Any]:
    """运行 yosys stat 并收集结构化摘要。

    参数:
        reference_path: 参考 RTL 源文件路径。
        candidate_path: 候选 RTL 源文件路径。

    返回:
        包含 yosys 状态与原始摘要的结果字典。

    异常:
        无业务异常向外抛出；工具失败会转成状态字典返回。
    """

    # 把参考 RTL 和候选 RTL 拼成一条固定的 yosys `read_verilog ...; stat` 命令。
    list_yosys_stat_command_argv = [  # _yosys_stat 真正执行的 argv，会把参考 RTL 和候选 RTL 一起送进同一次 yosys stat
        "yosys",  # yosys 可执行文件
        "-q",  # 静默模式
        "-p",  # 传入脚本命令
        f"read_verilog {json.dumps(str(reference_path))} {json.dumps(str(candidate_path))}; stat",  # 用于读取双输入并执行 stat 的 yosys 脚本文本
    ]

    # 运行 yosys 子进程并保留 stdout/stderr 供后续解析。
    try:

        # 执行 yosys 子进程，并把 stdout/stderr 留给后续状态整理。
        completed_process = subprocess.run(  # 执行 yosys 子进程并捕获 stdout/stderr
            list_yosys_stat_command_argv,  # 当前 reference/candidate 组合对应的 yosys argv
            capture_output=True,  # 同时捕获 stdout/stderr 供后续状态整理
            text=True,  # 直接按文本模式读取 yosys 输出
            timeout=30,  # 给 yosys stat 设置固定超时上限
            check=False,  # 非零退出码由下方逻辑统一转成状态字典
        )

    # 系统错误或超时都归一化成 error 状态。
    except (OSError, subprocess.TimeoutExpired) as exc:

        # 工具调用异常时直接返回 error 状态摘要。
        return {
            "status": "error",  # yosys 调用失败
            "detail": str(exc),  # 工具失败细节
        }

    # yosys 返回非零退出码时，回传 stderr/stdout 摘要。
    if completed_process.returncode != 0:

        # yosys 非零退出时直接返回错误摘要，避免继续解析无效输出。
        return {
            "status": "error",  # yosys stat 执行失败
            "detail": (completed_process.stderr or completed_process.stdout).strip(),  # 工具错误摘要
        }

    # 先建立一个包含原始 stdout 的可用状态摘要，后面再逐项补字段。
    dict_yosys_stat_summary = {  # _yosys_stat 的返回对象，先收原始 stdout，后续解析出的统计字段都会累加到这里
        "status": "available",  # yosys stat 已成功可用的状态标记
        "raw": (completed_process.stdout or "").strip(),  # yosys 原始输出文本
    }

    # 逐行解析形如 key: value 的统计项。
    for str_line in (completed_process.stdout or "").splitlines():

        # 不含冒号的行不属于键值统计项，直接跳过。
        if ":" not in str_line:

            # 只保留符合 key: value 结构的统计行。
            continue

        # 拆分当前统计行的 key 与 value。
        str_key, str_value = str_line.split(":", 1)  # 当前统计项原始键值对

        # 归一化当前统计项的 key 名称。
        str_normalized_key = str_key.strip().lower().replace(" ", "_")  # 归一化后的统计项名称

        # 把当前统计项写入结构化摘要。
        dict_yosys_stat_summary[str_normalized_key] = str_value.strip()  # 当前统计项规整后的值

    # 返回解析后的 yosys stat 摘要。
    return dict_yosys_stat_summary

# 根据 compare/optimize 阶段摘要决定推荐的下一步动作。
def _recommended_next_action(summary: dict[str, Any], *, candidate_provided: bool) -> str:
    """根据校验摘要返回更具体的后续动作建议。

    参数:
        summary: compare 或 optimize 阶段的结构化摘要。
        candidate_provided: 是否已经提供候选 RTL。

    返回:
        供上层流程使用的推荐动作字符串。
    """

    # 缺少候选 RTL 时，优先提示补充候选实现或先审阅计划。
    if not candidate_provided:

        # 未提供候选 RTL 时，先返回补料/审计划指令。
        return "provide_candidate_rtl_or_review_plan"

    # 接口不一致时，先修接口漂移再谈后续验证。
    if not summary.get("interface_consistent"):

        # 接口漂移会直接破坏外部契约，必须优先修复。
        return "fix_interface_drift"

    # checkpoint 不一致时，优先修复观察点漂移。
    if not summary.get("checkpoint_consistent"):

        # 观察点漂移会影响语义核对与调试，因此单独拦截。
        return "fix_checkpoint_drift"

    # testbench 不一致时，优先补齐测试用例或参考行为。
    if not summary.get("testbench_consistent"):

        # 行为测试不一致时，优先修测试或参考行为基线。
        return "repair_testbench_or_reference_cases"

    # 前述关键条件都满足时，转入人工复查候选 RTL。
    return "review_candidate_manually"

# 提取 optimize_assist 阶段应关注的优化目标列表。
def _optimization_targets(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """根据分析结果生成 optimization target 列表。

    参数:
        analysis: RTL 结构化分析结果。

    返回:
        optimization target 列表，供优化计划与 transform plan 复用。
    """

    # 先写入始终成立的 IO 契约保持目标。
    list_targets = [
        {
            "id": "preserve_io_contract",  # 优化目标标识
            "text": "Preserve the existing public IO contract exactly.",  # IO 契约保持目标说明
        }
    ]  # optimization target 列表

    # 识别到可分解热点时，补充分区级优化目标。
    if analysis.get("decomposition_candidates"):

        # 追加分解热点相关的优化目标。
        list_targets.append(
            {
                "id": "partition_hotspots",  # 分区热点目标标识
                "text": "Use decomposition candidates as safe partition hotspots for assist planning.",  # 分区热点目标说明
            }
        )

    # 识别到计数器类状态时，补充可见性保持目标。
    if any(item.get("role") == "counter" for item in analysis.get("state_elements", [])):

        # 追加计数器/状态交互可见性目标。
        list_targets.append(
            {
                "id": "counter_fsm_visibility",  # 计数器可见性目标标识
                "text": "Keep counter/state interaction visible for timing and debug review.",  # 计数器可见性目标说明
            }
        )

    # 返回汇总后的优化目标列表。
    return list_targets

# 提取优化辅助阶段应保持的 QoR 目标列表。
def _qor_objectives(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """根据分析结果生成 QoR objective 列表。

    参数:
        analysis: RTL 结构化分析结果。

    返回:
        QoR objective 列表，供优化计划与 transform plan 复用。
    """

    # 预先记录当前设计的时序元素数量基线。
    int_state_element_count = len(analysis.get("state_elements", []))  # 当前设计时序元素数量

    # 返回优化辅助阶段的 QoR 目标列表。
    return [
        {
            "id": "reduce_area_like_signals",  # 面积近似信号目标标识
            "text": "Avoid increasing structural signal count unless the candidate clearly improves observability.",  # 面积近似信号目标说明
        },
        {
            "id": "preserve_sequential_footprint",  # 时序足迹目标标识
            "text": f"Keep sequential element count near the current baseline of {int_state_element_count}.",  # 时序足迹目标说明
        },
        {
            "id": "preserve_interface_cost_markers",  # 接口代价目标标识
            "text": "Do not add extra public ports or widen the existing interface without explicit intent.",  # 接口代价目标说明
        },
    ]

# 计算可粗略反映结构面积的信号数量指标。
def _area_like_signals(analysis: dict[str, Any] | None) -> int | None:
    """估算可类比面积规模的结构信号数量。

    参数:
        analysis: RTL 结构化分析结果；为空时表示当前没有可统计对象。

    返回:
        端口数与状态元素数之和；缺少分析结果时返回 None。
    """

    # 缺少分析结果时，不输出面积近似指标。
    if not analysis:

        # 缺少分析结果时，返回 None 让上游跳过该指标。
        return None

    # 分别统计端口与状态元素数量，避免返回式过长。
    int_port_count = len(analysis.get("ports", []))  # 当前设计端口数量

    # 单独记录状态元素数量，便于与端口规模合并统计。
    int_state_count = len(analysis.get("state_elements", []))  # 当前设计状态元素数量

    # 返回端口数量与状态元素数量之和。
    return int_port_count + int_state_count

# 统计接口规模相关的代价标记，供 QoR 摘要复用。
def _interface_cost_markers(analysis: dict[str, Any] | None) -> dict[str, Any] | None:
    """计算接口规模相关的代价标记。

    参数:
        analysis: RTL 结构化分析结果；为空时表示当前没有可统计对象。

    返回:
        包含端口数量、总位宽和输出数量的摘要字典；缺少分析结果时返回 None。
    """

    # 缺少分析结果时，不输出接口代价标记。
    if not analysis:

        # 缺少分析结果时，返回 None 让上游跳过该摘要。
        return None

    # 累加所有公共端口的总位宽。
    int_total_width = sum(  # 汇总全部公共端口的位宽总和
        int(item.get("width") or 1) for item in analysis.get("ports", [])  # 逐个累加公共端口位宽
    )  # 公共端口总位宽

    # 返回接口规模相关的统计摘要。
    return {
        "port_count": len(analysis.get("ports", [])),  # 公共端口数量
        "total_port_width": int_total_width,  # 所有公共端口累计位宽
        "output_count": sum(
            1 for item in analysis.get("ports", []) if item.get("direction") == "output"
        ),  # 输出端口数量
    }

# 从单文件或目录中提取可比较的 RTL 源文件列表。
def _artifact_sources(path: Path | None) -> list[Path]:
    """解析工件路径并返回可用 RTL 源文件列表。

    参数:
        path: 单个 RTL 文件路径或包含 RTL 文件的目录路径，可为空。

    返回:
        过滤 testbench 后的 RTL 源文件路径列表。
    """

    # 缺少工件路径时，直接返回空列表。
    if path is None:

        # 没有提供工件路径时，不再尝试解析任何 RTL 源文件。
        return []

    # 统一把输入转换成 Path 对象，便于后续判断。
    path_candidate = Path(path)  # 待解析的工件路径

    # 指向单个 Verilog 源文件时，直接返回该文件。
    if path_candidate.is_file() and path_candidate.suffix.lower() == ".v":

        # 单文件输入可以直接作为唯一 RTL 源。
        return [path_candidate]

    # 路径不存在时，不返回任何源文件。
    if not path_candidate.exists():

        # 路径不存在时，不再继续递归扫描。
        return []

    # 递归收集目录下所有非 testbench 的 Verilog 文件。
    return sorted(
        item for item in path_candidate.rglob("*.v") if not _is_testbench(item)
    )

# 判断给定 Verilog 文件是否属于 testbench。
def _is_testbench(path: Path) -> bool:
    """根据文件名规则判断当前 Verilog 文件是否为 testbench。

    参数:
        path: 待判断的 Verilog 文件路径。

    返回:
        命中 testbench 命名模式时返回 True，否则返回 False。
    """

    # 提取小写化后的文件 stem，便于统一匹配命名规则。
    str_stem = path.stem.lower()  # 小写化后的文件 stem

    # 返回当前文件是否命中常见 testbench 命名模式。
    return (
        str_stem.endswith("_tb")
        or str_stem.startswith("tb_")
        or "testbench" in str_stem
    )
