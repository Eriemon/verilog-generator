"""verify-repair 主流程编排。"""

# 延迟注解让 core 只在运行时消费必要对象。
from __future__ import annotations

# 路径和 JSON 宽松类型用于对外 facade。
from pathlib import Path

# SimpleNamespace 仅用于私有阶段上下文，不改变公开 API。
from types import SimpleNamespace
from typing import Any

# existing RTL 分析入口保持原有 artifact schema。
from .existing_rtl import analyze_existing_rtl

# RTL patch helper 负责候选、策略和写回。
from .verify_repair_patching import (
    RtlMutationContext,
    build_patch_candidate,
    handle_rtl_mutation,
    source_mutation_policy,
    tb_mutation_policy,
)

# 报告 helper 负责结构化报告 payload。
from .verify_reporting import (
    expected_trace_markdown,
    run_summary_payload,
    simulation_slice_payload,
    synth_readiness_payload,
)

# 末端状态和覆盖类 payload 保持旧文件名契约。
from .verify_reporting import (
    terminal_status_payload,
    testcase_matrix_payload,
    timing_diagnostic_payload,
    waveform_diff_payload,
)

# support helper 负责入口规整、日志诊断和输入加载。
from .verify_repair_support import (
    diagnostic_inputs,
    diagnose_log_texts,
    excerpt,
    load_optional_spec_text,
)

# source staging helper 负责 RTL/TB 源文件拆分和复制。
from .verify_repair_support import (
    normalize_verify_options,
    split_sources,
    stage_sources,
)

# 验证计划构造单独分组，避免入口支持函数形成密集导入块。
from .verify_repair_support import (
    validation_spec,
    build_verification_plan,
)

# testbench helper 负责生成或增强 TB。
from .verify_repair_testbench import materialize_tb_contract

# 统一 validation 和 workspace 写入函数。
from scripts.python.validation.validation import validate_generated
from scripts.python.workflow.workspace import write_json, write_text

# verify_existing 是 existing RTL verify-repair 的稳定入口。
def verify_existing(
    source_paths: list[Path],
    *,
    out_dir: Path,
    **dict_verify_options: Any,
) -> dict[str, Any]:
    """
    运行 existing RTL verify-repair 流程并返回工件路径。

    参数:
        source_paths: 调用方传入的 RTL 源文件路径列表，可混入待检测的 testbench。
        out_dir: 本轮 verify-repair 工件输出目录。
        **dict_verify_options: 兼容旧调用点的关键字选项，包含 spec_source、module_name、
            testbench_source、decision_source、tb_mode、tb_language、automation_mode、
            readiness 和 run_external。

    返回:
        包含 run 目录、分析报告、验证报告、诊断报告和最终结果路径的索引字典。

    异常:
        ValueError: 缺少 automation_mode 或传入未知关键字选项时抛出。
    """

    # 将旧关键字入口归一到私有命名空间，主流程只读取标准字段。
    simple_namespace_options = _build_verify_options(dict_verify_options)  # verify-repair 标准化选项

    # 入口策略先统一校验，避免后续 helper 重复处理别名。
    tuple_normalized_options = normalize_verify_options(  # 标准化后的入口策略四元组
        str_automation_mode=simple_namespace_options.automation_mode,  # 用户选择的 RTL/TB 自动化策略
        str_tb_mode=simple_namespace_options.tb_mode,  # testbench 生成或增强模式
        str_tb_language=simple_namespace_options.tb_language,  # testbench 目标语言
        str_readiness=simple_namespace_options.readiness,  # validation 执行深度
    )  # 标准化后的 automation、TB 和 readiness 策略

    # run 目录必须存在。
    out_dir.mkdir(parents=True, exist_ok=True)

    # 将规范化策略收进上下文，降低后续阶段的参数横向扩散。
    simple_namespace_options: SimpleNamespace = SimpleNamespace(  # verify-repair 本轮运行策略
        spec_source=simple_namespace_options.spec_source,  # 行为规格来源
        module_name=simple_namespace_options.module_name,  # 调用方指定顶层名
        testbench_source=simple_namespace_options.testbench_source,  # 显式 testbench 路径
        decision_source=simple_namespace_options.decision_source,  # 人工确认决策文件

        # 规范化后的枚举值供后续所有阶段复用。
        str_automation_mode=tuple_normalized_options[0],  # 标准化后的自动化写回策略
        str_tb_mode=tuple_normalized_options[1],  # 标准化后的 testbench 模式
        str_tb_language=tuple_normalized_options[2],  # 标准化后的 testbench 语言
        str_readiness=tuple_normalized_options[3],  # 标准化后的验证深度
        bool_run_external=simple_namespace_options.run_external,  # 是否允许外部仿真/综合工具运行
    )

    # 规格文本只在分析和计划里作为上下文。
    str_spec_text = load_optional_spec_text(simple_namespace_options.spec_source)  # 用户提供的行为规格文本

    # 拆分 RTL 源和可选 testbench 源。
    list_rtl_sources, path_detected_tb_source = split_sources(  # RTL 源集合与检测到的 testbench
        source_paths,  # 调用方传入的 RTL/TB 候选路径
        path_explicit_testbench=simple_namespace_options.testbench_source,  # 调用方显式指定的 testbench 路径
    )  # RTL 源文件与 testbench 源文件

    # 调用 existing RTL 分析器，建立验证计划依赖的模块、端口和工程摘要。
    dict_analysis_result = analyze_existing_rtl(  # existing RTL 结构和工程分析结果
        list_rtl_sources,  # 已确认的 RTL 源文件列表
        spec_text=str_spec_text,  # 可选行为规格文本
        module_name=simple_namespace_options.module_name,  # 调用方指定的顶层模块名
        out_dir=out_dir,  # 分析工件输出目录
    )  # existing RTL 分析结果

    # 主分析 payload 后续所有阶段都会读取。
    dict_analysis = dict_analysis_result["analysis"]  # RTL 结构分析 payload

    # 构建验证计划。
    dict_verification_plan = build_verification_plan(  # 验证计划中的用例、信号和检查目标
        dict_analysis,  # 顶层模块和端口解析产物
        str_spec_text=str_spec_text,  # 规格文本上下文
        str_tb_mode=simple_namespace_options.str_tb_mode,  # testbench 生成策略
        str_tb_language=simple_namespace_options.str_tb_language,  # testbench 语言策略
        str_automation_mode=simple_namespace_options.str_automation_mode,  # 自动化写回策略
    )  # verify-repair 验证计划

    # verification_plan.json 是后续诊断和结果索引共同引用的计划工件。
    path_verification_plan = write_json(out_dir / "verification_plan.json", dict_verification_plan)  # 验证计划路径

    # validation workspace 隔离 RTL 副本和自动生成的 testbench。
    path_workspace_dir = out_dir / "verification_workspace"  # 验证阶段独占工作目录

    # 将原始 RTL 复制到隔离目录，避免 validation 阶段改动用户源文件。
    list_staged_sources = stage_sources(list_rtl_sources, path_workspace_dir / "rtl")  # 供 validation 读取的 RTL 副本

    # TB 物化结果同时提供 validation 入口和人工审计计划。
    tuple_tb_materialization = materialize_tb_contract(  # 随后拆成测试平台契约正文与增强计划正文
        dict_analysis=dict_analysis,  # 测试平台模板所需的模块接口描述
        path_out_dir=out_dir,  # 测试平台工件输出目录
        path_workspace_dir=path_workspace_dir,  # 验证器读取测试平台的隔离工作目录
        spec_source=simple_namespace_options.spec_source,  # 透传给 TB scaffold 的行为规格来源

        # 现有 testbench 和模式选项共同决定 generate/augment 的落盘方式。
        path_existing_tb_source=path_detected_tb_source,  # 自动检测或显式传入的既有测试平台
        str_tb_mode=simple_namespace_options.str_tb_mode,  # 测试平台生成或增强模式
        str_tb_language=simple_namespace_options.str_tb_language,  # 测试平台目标输出语言
        str_automation_mode=simple_namespace_options.str_automation_mode,  # 测试平台源文件写回策略
    )  # 测试平台写入契约与增强说明

    # 提取 testbench 契约，后续 validation 和 artifact 写入都依赖它。
    dict_tb_contract = tuple_tb_materialization[0]  # 记录测试平台文件、语言和备份关系的契约正文

    # 提取 testbench 增强计划，保持 generate/augment 两种模式都有同名 artifact。
    dict_tb_augment_plan = tuple_tb_materialization[1]  # 记录 augment 原文件、备份文件和差异文件的计划正文

    # tb_contract.json 记录 caller 后续可追踪的 testbench 路径。
    path_tb_contract = write_json(out_dir / "tb_contract.json", dict_tb_contract)  # TB 契约 JSON 路径

    # tb_augment_plan.json 在 generate 模式也保留空计划，维持 artifact 集合稳定。
    path_tb_augment_plan = write_json(out_dir / "tb_augment_plan.json", dict_tb_augment_plan)  # TB 增强计划路径

    # 使用统一验证器检查 staged RTL 与本轮 testbench contract。
    obj_validation_report = _run_validation(  # staged workspace 的结构化验证报告
        dict_analysis=dict_analysis,  # validation spec 需要的模块接口描述
        list_staged_sources=list_staged_sources,  # workspace 内 RTL 副本列表
        dict_tb_contract=dict_tb_contract,  # testbench 文件和语言约束
        path_workspace_dir=path_workspace_dir,  # validation 执行目录
        str_readiness=simple_namespace_options.str_readiness,  # staged workspace 验证级别
        bool_run_external=simple_namespace_options.bool_run_external,  # 是否允许外部工具执行
    )

    # validation_report.json 保留统一验证器的完整结构。
    path_validation_report = write_json(  # workspace validation 完整报告路径
        out_dir / "validation_report.json",  # validation 报告固定输出文件
        obj_validation_report.to_dict(),  # validation report 的可序列化正文
    )

    # validation 之后的诊断、patch 和末尾索引写入由独立阶段承接。
    simple_namespace_repair_context: SimpleNamespace = SimpleNamespace(  # validation 后续阶段共享上下文
        path_out_dir=out_dir,  # 本轮 run 工件根目录
        list_rtl_sources=list_rtl_sources,  # 原始 RTL 源路径集合
        dict_analysis_result=dict_analysis_result,  # analysis helper 返回的路径索引
        dict_analysis=dict_analysis,  # RTL 顶层结构分析正文
        dict_verification_plan=dict_verification_plan,  # 验证计划正文
        dict_tb_contract=dict_tb_contract,  # testbench 契约正文
        obj_validation_report=obj_validation_report,  # workspace validation 报告对象

        # 已落盘的核心 artifact 路径会进入最终对外索引。
        path_verification_plan=path_verification_plan,  # host 后续复查验证计划的 JSON 文件
        path_tb_contract=path_tb_contract,  # validation 和 UI 共同读取的 TB 契约文件
        path_tb_augment_plan=path_tb_augment_plan,  # augment 模式下记录 TB 备份关系的文件
        path_validation_report=path_validation_report,  # workspace validation 的完整证据文件
        obj_options=simple_namespace_options,  # 串联后续阶段的运行策略对象
    )

    # 返回给 facade 的索引保持旧字段和绝对路径语义。
    return _complete_verify_repair(simple_namespace_repair_context)

# validation 后的诊断、patch 和结果写入阶段。
def _complete_verify_repair(simple_namespace_context: SimpleNamespace) -> dict[str, Any]:
    """
    完成 verify-repair 的日志诊断、候选修复和最终结果写入。

    参数:
        simple_namespace_context: validation 阶段之后汇总的运行上下文。

    返回:
        包含 verify-repair 所有对外 artifact 路径的索引字典。
    """

    # 从 validation report 派生日志诊断输入。
    str_compile_log, str_simulation_log, bool_executed = diagnostic_inputs(  # 诊断输入日志和执行标志
        simple_namespace_context.obj_validation_report,  # 统一验证器输出对象
        str_readiness=simple_namespace_context.obj_options.str_readiness,  # 日志提取遵循的执行级别
        bool_run_external=simple_namespace_context.obj_options.bool_run_external,  # 外部工具执行开关
    )  # 编译日志、仿真日志和执行状态

    # 日志诊断将 compile/simulation 文本归一为 verify-repair outcome。
    dict_diagnosis = diagnose_log_texts(  # 由日志内容归纳出的诊断 payload
        compile_log=str_compile_log,  # 编译阶段日志文本
        simulation_log=str_simulation_log,  # 仿真阶段日志文本
        executed=bool_executed,  # 外部后端是否实际执行
    )  # 日志诊断结果

    # 报告上下文集中保存本阶段共享的诊断材料。
    obj_diagnostic_context = SimpleNamespace(  # 多个诊断报告共享的输入包
        path_out_dir=simple_namespace_context.path_out_dir,  # 本轮诊断工件根目录
        dict_analysis=simple_namespace_context.dict_analysis,  # 顶层模块、端口和实例信息
        dict_verification_plan=simple_namespace_context.dict_verification_plan,  # 用例矩阵和观测信号定义

        # TB、诊断和 validation payload 由各报告生成器共同读取。
        dict_tb_contract=simple_namespace_context.dict_tb_contract,  # testbench 生成位置与语言约束
        dict_diagnosis=dict_diagnosis,  # 编译/仿真日志归因结论
        obj_validation_report=simple_namespace_context.obj_validation_report,  # 统一验证器的原始结论容器

        # 原始日志和 readiness 决定切片、波形与综合准备度报告内容。
        str_compile_log=str_compile_log,  # 编译日志切片来源
        str_simulation_log=str_simulation_log,  # 仿真日志切片来源
        bool_executed=bool_executed,  # 外部工具是否执行
        str_readiness=simple_namespace_context.obj_options.str_readiness,  # 综合准备度档位
    )

    # 写出诊断和辅助报告。
    dict_report_paths = _write_diagnostic_reports(obj_diagnostic_context)  # 诊断和矩阵类报告路径集合

    # 根据诊断 outcome 和验证计划生成候选 RTL 修复动作。
    dict_patch_candidate, dict_rtl_patch_plan = build_patch_candidate(  # 候选 RTL 改动和人工复核说明
        list_source_paths=simple_namespace_context.list_rtl_sources,  # 原始 RTL 源文件列表
        path_out_dir=simple_namespace_context.path_out_dir,  # patch 相关工件输出目录
        dict_analysis=simple_namespace_context.dict_analysis,  # 模块结构和端口宽度依据
        dict_diagnosis=dict_diagnosis,  # 失败类型与修复类别依据
        dict_verification_plan=simple_namespace_context.dict_verification_plan,  # 计划中声明的观察点和用例

        # 候选生成策略限制可自动处理的修复类别。
        str_automation_mode=simple_namespace_context.obj_options.str_automation_mode,  # 候选修复遵循的写回模式
        str_readiness=simple_namespace_context.obj_options.str_readiness,  # 候选修复匹配的验证强度
    )  # RTL patch 候选和人工复核计划

    # patch_candidate.json 暴露当前诊断对应的候选修复动作。
    path_patch_candidate = write_json(  # 候选修复 JSON 路径
        simple_namespace_context.path_out_dir / "patch_candidate.json",  # 候选修复固定输出文件
        dict_patch_candidate,  # 当前诊断对应的候选 patch 正文
    )

    # rtl_patch_plan.json 供人工确认或 auto_apply 策略复核。
    path_rtl_patch_plan = write_json(  # RTL 修复计划 JSON 路径
        simple_namespace_context.path_out_dir / "rtl_patch_plan.json",  # RTL 修复计划固定输出文件
        dict_rtl_patch_plan,  # 人工确认或 auto_apply 复核正文
    )

    # 根据策略、decision 和候选决定是否写回 RTL。
    tuple_rtl_mutation_result = handle_rtl_mutation(  # RTL 写回结论及补充验证文件
        RtlMutationContext(  # RTL 写回阶段共享输入包
            list_source_paths=simple_namespace_context.list_rtl_sources,  # 可能被写回的原始 RTL 源文件
            path_out_dir=simple_namespace_context.path_out_dir,  # mutation 工件输出目录

            # patch helper 需要同时读取结构、TB 和修复计划。
            dict_analysis=simple_namespace_context.dict_analysis,  # patch 应用时参考的结构信息
            dict_tb_contract=simple_namespace_context.dict_tb_contract,  # post-apply 验证使用的 testbench 信息
            dict_patch_candidate=dict_patch_candidate,  # 待应用的修复候选内容
            dict_rtl_patch_plan=dict_rtl_patch_plan,  # 人工确认可读的修复计划

            # 执行策略控制是否写回以及是否追加 post-apply 验证。
            str_automation_mode=simple_namespace_context.obj_options.str_automation_mode,  # RTL 写回策略
            str_readiness=simple_namespace_context.obj_options.str_readiness,  # post-apply 验证深度
            bool_run_external=simple_namespace_context.obj_options.bool_run_external,  # post-apply 是否运行外部工具
            path_decision_source=simple_namespace_context.obj_options.decision_source,  # 人工确认 decision 文件路径
        )
    )  # RTL 写回决策和 post-apply 工件路径

    # mutation helper 返回固定四元组，拆分后继续保留原有语义。
    (
        dict_rtl_mutation,  # RTL 写回状态字典
        path_intervention,  # RTL 人工确认说明路径
        path_post_apply_validation,  # patch 后验证报告路径
        path_post_apply_equivalence,  # patch 后等价检查报告路径
    ) = tuple_rtl_mutation_result

    # mutation helper 会补齐备份和生效源文件信息，需要覆盖候选报告。
    path_patch_candidate = write_json(  # 带写回证据的 patch 候选报告
        simple_namespace_context.path_out_dir / "patch_candidate.json",  # adapter 读取候选 patch 的固定文件
        dict_patch_candidate,  # 已补充 backup/active 源文件关系的候选正文
    )

    # TB mutation 只由授权模式和契约路径决定，不读取诊断结论。
    dict_tb_mutation = tb_mutation_policy(  # testbench 源文件处置摘要
        simple_namespace_context.obj_options.str_automation_mode,  # 自动化模式决定是否可写回 TB
        simple_namespace_context.dict_tb_contract,  # testbench 契约提供源文件位置
    )

    # 最终阶段写出 loop_state、summary、terminal_status 和 verification_result。
    dict_verify_result = _finalize_verify_result(  # facade 返回的全量 artifact 索引
        SimpleNamespace(  # finalizer 读取的收口上下文
            path_out_dir=simple_namespace_context.path_out_dir,  # 最终 JSON 集合所在 run 目录
            dict_analysis_result=simple_namespace_context.dict_analysis_result,  # 原始分析报告的路径索引
            dict_analysis=simple_namespace_context.dict_analysis,  # verification_result 展示的顶层结构
            dict_diagnosis=dict_diagnosis,  # terminal_status 展示的日志归因

            # TB/RTL mutation 状态进入 loop_state、terminal_status 和 result payload。
            dict_tb_contract=simple_namespace_context.dict_tb_contract,  # run_summary 展示的 testbench 来源
            dict_tb_mutation=dict_tb_mutation,  # TB 是否写回原文件的决策
            dict_rtl_mutation=dict_rtl_mutation,  # RTL patch 是否落盘的执行记录
            obj_validation_report=simple_namespace_context.obj_validation_report,  # summary 与终态共享的验证结论

            # 已写出的核心 artifact 路径需要原样进入对外返回索引。
            path_verification_plan=simple_namespace_context.path_verification_plan,  # 用例矩阵来源计划文件
            path_tb_contract=simple_namespace_context.path_tb_contract,  # staged TB 位置契约文件
            path_tb_augment_plan=simple_namespace_context.path_tb_augment_plan,  # augment 备份和 diff 计划文件
            path_validation_report=simple_namespace_context.path_validation_report,  # validation 后端完整证据文件
            path_patch_candidate=path_patch_candidate,  # 最新候选 patch 报告文件
            path_rtl_patch_plan=path_rtl_patch_plan,  # 人工复核用 RTL patch 计划文件
            dict_report_paths=dict_report_paths,  # log/timing/waveform 等诊断报告集合

            # 人工确认和 post-apply 证据路径允许为空，schema 中仍保留对应字段。
            path_intervention=path_intervention,  # conservative/semi_auto 模式的人读确认说明
            path_post_apply_validation=path_post_apply_validation,  # patch 应用后追加 validation 证据
            path_post_apply_equivalence=path_post_apply_equivalence,  # patch 应用后的等价性占位证据
            path_decision_source=simple_namespace_context.obj_options.decision_source,  # resume 本轮使用的 decision 来源
            obj_options=simple_namespace_context.obj_options,  # final payload 需要的标准化策略
        )
    )

    # 返回 facade 既有路径索引，保持调用方读取方式不变。
    return dict_verify_result

# 兼容旧关键字入口，同时让主函数签名保持短小。
def _build_verify_options(dict_verify_options: dict[str, Any]) -> SimpleNamespace:
    """
    将 verify_existing 的旧关键字参数归一成私有选项对象。

    参数:
        dict_verify_options: 旧调用点通过关键字传入的 verify-repair 选项。

    返回:
        包含规格来源、模块名、testbench、decision 和执行策略的命名空间。

    异常:
        ValueError: 缺少 automation_mode 或出现未知关键字时抛出。
    """

    # 缺少 automation_mode 会让自动写回边界不明确，必须立即阻断。
    if "automation_mode" not in dict_verify_options:

        # 错误文本面向调用方展示，需要遵守 current-project 前缀。
        raise ValueError("> ERR: [Python] verify_existing 需要显式 automation_mode")

    # 复制一份可消费的选项字典，避免修改调用方持有的对象。
    dict_remaining_options = dict(dict_verify_options)  # 待解析的 verify-repair 选项

    # 按旧签名逐项取值并保留默认值。
    simple_namespace_options: SimpleNamespace = SimpleNamespace(  # verify-repair 入口选项
        spec_source=dict_remaining_options.pop("spec_source", None),  # 可选行为规格来源
        module_name=dict_remaining_options.pop("module_name", None),  # 可选顶层模块名
        testbench_source=dict_remaining_options.pop("testbench_source", None),  # 可选 testbench 文件
        decision_source=dict_remaining_options.pop("decision_source", None),  # 可选人工确认文件

        # 执行模式字段保留旧默认值，避免 CLI 和 adapter 调用点变化。
        tb_mode=dict_remaining_options.pop("tb_mode", "generate"),  # testbench 生成/增强模式
        tb_language=dict_remaining_options.pop("tb_language", "verilog"),  # testbench 输出语言
        automation_mode=dict_remaining_options.pop("automation_mode"),  # RTL/TB 写回授权策略
        readiness=dict_remaining_options.pop("readiness", "static"),  # validation 后端覆盖深度
        run_external=dict_remaining_options.pop("run_external", True),  # 是否触发仿真/综合后端
    )

    # 未识别的关键字通常代表调用方拼写错误，不能静默忽略。
    if dict_remaining_options:

        # 只展示排序后的 key，避免终端输出完整结构化 payload。
        str_unknown_options = ", ".join(sorted(dict_remaining_options))  # 未识别关键字摘要

        # 阻断未知选项进入后续验证流程。
        raise ValueError(f"> ERR: [Python] verify_existing 收到未知选项: {str_unknown_options}")

    # 返回供主流程读取的标准选项对象。
    return simple_namespace_options

# validation 执行保持 core 主流程简洁。
def _run_validation(
    *,
    dict_analysis: dict[str, Any],
    list_staged_sources: list[Path],

    # testbench 与 workspace 参数决定 validate_generated 的输入边界。
    dict_tb_contract: dict[str, Any],
    path_workspace_dir: Path,
    str_readiness: str,
    bool_run_external: bool,
) -> Any:
    """
    运行 verify-repair workspace validation。

    参数:
        dict_analysis: existing RTL 分析得到的模块和端口结构。
        list_staged_sources: 已复制到 validation workspace 的 RTL 源文件。
        dict_tb_contract: testbench 物化阶段输出的文件和语言契约。
        path_workspace_dir: validation 执行和报告写入的隔离目录。
        str_readiness: validation 执行深度。
        bool_run_external: 是否允许调用外部仿真或综合工具。

    返回:
        validate_generated 返回的验证报告对象。
    """

    # existing RTL 诊断路径将注释门禁作为诊断，不作为阻断。
    return validate_generated(
        validation_spec(
            dict_analysis,
            list_staged_sources,
            dict_tb_contract["workspace_testbench_path"],
        ),
        path_workspace_dir,
        target="rtl",
        run_external=bool_run_external,
        readiness=str_readiness,
        comment_language="zh",
        strict_generated_comments=False,
    )

# 诊断类报告路径集中生成，减少 verify_existing 主函数长度。
def _write_diagnostic_reports(obj_context: SimpleNamespace) -> dict[str, Path]:
    """
    写出 verify-repair 诊断和覆盖报告。

    参数:
        obj_context: 包含日志、诊断、验证计划和 validation 报告的诊断上下文。

    返回:
        诊断类 artifact 名称到实际路径的映射。
    """

    # log_diagnosis.json 是 compile/simulation 分类后的主诊断报告。
    path_log_diagnosis = write_json(  # 主日志诊断 JSON 路径
        obj_context.path_out_dir / "log_diagnosis.json",  # 主诊断报告固定路径
        obj_context.dict_diagnosis,  # outcome、类别和证据摘录
    )  # 日志诊断 artifact 路径

    # simulation_slice 摘要编译/仿真日志和观察标签。
    path_simulation_slice = write_json(  # 编译和仿真日志切片路径
        obj_context.path_out_dir / "simulation_slice.json",  # 日志切片报告固定路径
        simulation_slice_payload(  # 截取日志并附带 testbench 来源的报告正文
            compile_log=obj_context.str_compile_log,  # 编译日志文本
            simulation_log=obj_context.str_simulation_log,  # 仿真日志文本
            executed=obj_context.bool_executed,  # 波形复核是否有真实仿真依据
            tb_contract=obj_context.dict_tb_contract,  # testbench 文件位置和语言信息
            excerpt_fn=excerpt,  # 长日志截断函数
        ),
    )

    # timing diagnostic 汇总归因和 focus signals。
    path_timing_diagnostic = write_json(  # 时序归因和重点信号报告路径
        obj_context.path_out_dir / "timing_diagnostic.json",  # 时序诊断报告固定路径
        timing_diagnostic_payload(  # 汇总 timing 归因和重点观测信号
            obj_context.dict_diagnosis,  # timing 归因所需的失败类别
            validation_report=obj_context.obj_validation_report,  # backend 报告和 readiness 状态
            verification_plan=obj_context.dict_verification_plan,  # focus signal 与用例定义
        ),
    )

    # expected_trace.md 是人工排查时阅读的预期行为说明。
    path_expected_trace = obj_context.path_out_dir / "expected_trace.md"  # 预期行为 Markdown 路径

    # 写出 expected trace。
    write_text(
        path_expected_trace,
        expected_trace_markdown(obj_context.dict_analysis, obj_context.dict_verification_plan),
    )

    # waveform diff payload 记录待复核状态。
    path_waveform_diff = write_json(  # 波形差异待复核报告路径
        obj_context.path_out_dir / "waveform_diff.json",  # 波形差异报告固定路径
        waveform_diff_payload(  # 标记波形复核状态和期望观察点
            obj_context.dict_diagnosis,  # 波形比较的失败归因来源
            verification_plan=obj_context.dict_verification_plan,  # 期望观测信号集合
            executed=obj_context.bool_executed,  # 外部后端执行标志
        ),
    )

    # testcase matrix 描述验证计划覆盖。
    path_testcase_matrix = write_json(  # 验证计划覆盖矩阵路径
        obj_context.path_out_dir / "testcase_matrix.json",  # 用例矩阵报告固定路径
        testcase_matrix_payload(  # 展开测试用例、TB 来源和诊断状态
            obj_context.dict_verification_plan,  # 用例矩阵的计划来源
            tb_contract=obj_context.dict_tb_contract,  # testbench 覆盖入口信息
            diagnosis=obj_context.dict_diagnosis,  # 用例状态标记依据
        ),
    )

    # synth readiness 暴露后端选择和 readiness 状态。
    path_synth_readiness = write_json(  # 综合准备度报告路径
        obj_context.path_out_dir / "synth_readiness.json",  # 综合准备度报告固定路径
        synth_readiness_payload(  # 汇总后端可用性和 readiness 结论
            obj_context.obj_validation_report,  # readiness 判断所需的验证结论
            readiness=obj_context.str_readiness,  # 综合准备度对应的执行级别
        ),
    )

    # 返回路径集合供 verification_result 和 facade 索引引用。
    return {
        "log_diagnosis": path_log_diagnosis,  # compile/simulation 归因主报告
        "simulation_slice": path_simulation_slice,  # 日志摘录和执行标志报告
        "timing_diagnostic": path_timing_diagnostic,  # 时序重点信号诊断报告
        "expected_trace": path_expected_trace,  # 人工排查用的预期行为说明
        "waveform_diff": path_waveform_diff,  # 波形复核占位和观察点报告
        "testcase_matrix": path_testcase_matrix,  # 验证计划覆盖矩阵报告
        "synth_readiness": path_synth_readiness,  # 综合后端准备度报告
    }

# 末尾工件写入保持旧 verification_result schema。
def _finalize_verify_result(obj_context: SimpleNamespace) -> dict[str, Any]:
    """
    写出最终 verify-repair 结果并返回路径索引。

    参数:
        obj_context: 汇总分析、诊断、mutation 状态和已写 artifact 路径的上下文。

    返回:
        facade 使用的绝对路径索引。
    """

    # loop_state 记录 resume 相关状态。
    path_loop_state = _write_loop_state(  # resume 状态机的持久化文件
        path_out_dir=obj_context.path_out_dir,  # loop_state 固定落盘目录
        dict_diagnosis=obj_context.dict_diagnosis,  # loop_state 记录的最新 outcome

        # mutation 状态决定 resume 是否还需要人工动作。
        dict_tb_mutation=obj_context.dict_tb_mutation,  # TB 是否写回的状态记录
        dict_rtl_mutation=obj_context.dict_rtl_mutation,  # RTL patch 是否应用的状态记录
        path_intervention=obj_context.path_intervention,  # 未自动写回时的介入说明
        path_decision_source=obj_context.path_decision_source,  # resume 时读取的决策文件
        str_automation_mode=obj_context.obj_options.str_automation_mode,  # loop_state 记录的用户授权等级
    )  # loop_state 路径

    # run_summary 汇总本轮诊断和 mutation 状态。
    path_run_summary = write_json(  # 人读运行摘要文件
        obj_context.path_out_dir / "run_summary.json",  # 运行摘要固定路径
        run_summary_payload(  # 面向人读摘要的诊断和变更汇总
            diagnosis=obj_context.dict_diagnosis,  # 摘要中的日志 outcome 来源
            validation_report=obj_context.obj_validation_report,  # 摘要中的验证状态来源
            tb_contract=obj_context.dict_tb_contract,  # 摘要中的 testbench 文件来源
            rtl_mutation=obj_context.dict_rtl_mutation,  # 摘要中的 RTL 变更状态
        ),
    )

    # terminal_status 是上层 UI/host 的最终状态。
    path_terminal_status = write_json(  # UI/host 读取的闭环状态文件
        obj_context.path_out_dir / "terminal_status.json",  # 终端状态固定路径
        terminal_status_payload(  # 面向 UI/host 的闭环状态正文
            diagnosis=obj_context.dict_diagnosis,  # 终态中的诊断 outcome 来源
            validation_report=obj_context.obj_validation_report,  # 终态中的验证通过状态
            tb_contract=obj_context.dict_tb_contract,  # 终态中的 testbench 可追踪信息
            tb_mutation=obj_context.dict_tb_mutation,  # 终态中的 TB 写回决策
            rtl_mutation=obj_context.dict_rtl_mutation,  # 终态中的 RTL patch 应用和确认状态
        ),
    )

    # verification_result 汇总所有工件相对路径。
    dict_verification_result = _verification_result_payload(obj_context)  # 最终结果 JSON 内容

    # 将 host 读取的最终状态和 artifact 索引写入固定 JSON 文件。
    path_verification_result = write_json(  # host 结果索引文件路径
        obj_context.path_out_dir / "verification_result.json",  # 最终结果固定路径
        dict_verification_result,  # 最终结果正文
    )

    # 返回 facade 既有路径索引。
    return _result_index(
        obj_context=obj_context,
        dict_verification_result=dict_verification_result,
        path_loop_state=path_loop_state,
        path_verification_result=path_verification_result,
        path_run_summary=path_run_summary,
        path_terminal_status=path_terminal_status,
    )

# loop_state 保留人工确认和 round 计数。
def _write_loop_state(
    *,
    path_out_dir: Path,
    dict_diagnosis: dict[str, Any],

    # mutation 输入决定确认状态和轮次统计。
    dict_tb_mutation: dict[str, Any],
    dict_rtl_mutation: dict[str, Any],
    path_intervention: Path | None,
    path_decision_source: Path | None,
    str_automation_mode: str,
) -> Path:
    """
    写出 verify-repair loop_state。

    参数:
        path_out_dir: loop_state.json 所在的 run 目录。
        dict_diagnosis: 最新日志诊断结论。
        dict_tb_mutation: testbench 源文件处置状态。
        dict_rtl_mutation: RTL patch 写回状态。
        path_intervention: 需要人工确认时写出的说明文件路径。
        path_decision_source: resume 时读取的人工决策文件路径。
        str_automation_mode: 调用方授权的自动化写回等级。

    返回:
        写出的 loop_state.json 路径。
    """

    # 是否等待 RTL 人工确认。
    bool_awaiting_rtl_confirmation = bool(path_intervention) and not dict_rtl_mutation.get("applied", False)  # RTL 是否等待确认

    # 应用 patch 后 round 计数增加。
    int_applied_patch_round = 1 if dict_rtl_mutation.get("applied") else 0  # 已应用 patch 轮次

    # 验证轮次记录是否进行了 post-apply 验证。
    int_verification_rounds = 2 if dict_rtl_mutation.get("applied") else 1  # 验证轮次数量

    # 写出固定文件名。
    return write_json(
        path_out_dir / "loop_state.json",
        {
            "version": 1,  # loop_state schema 版本
            "attempt_count": 1,  # 本轮 verify-existing 尝试次数
            "status": dict_diagnosis["outcome"],  # 最新日志诊断 outcome
            "automation_mode": str_automation_mode,  # 本轮自动化写回策略
            "last_result_path": "verification_result.json",  # 最新结果文件相对路径
            "confirmation_required": bool(dict_rtl_mutation.get("confirmation_required")),  # 是否需要人工确认
            "tb_mutation": dict_tb_mutation,  # TB generate/augment 写回记录
            "rtl_mutation": dict_rtl_mutation,  # RTL patch 授权和落盘记录
            "awaiting_rtl_confirmation": bool_awaiting_rtl_confirmation,  # conservative 模式是否仍需确认
            "last_decision_path": str(path_decision_source) if path_decision_source is not None else None,  # resume 决策文件记录
            "applied_patch_round": int_applied_patch_round,  # patch 已落盘时增加的轮次
            "verification_rounds": int_verification_rounds,  # 已执行验证轮次
        },
    )

# verification_result payload 独立组装，避免主函数塞入大字面量。
def _verification_result_payload(obj_context: SimpleNamespace) -> dict[str, Any]:
    """
    组装 verification_result.json payload。

    参数:
        obj_context: 包含最终状态、mutation 结果和 artifact 路径的上下文。

    返回:
        可直接写入 verification_result.json 的字典正文。
    """

    # artifact map 使用旧相对文件名契约。
    dict_artifacts = _artifact_map(  # verification_result 内部 artifact 映射
        dict_report_paths=obj_context.dict_report_paths,  # 已写诊断报告的实际文件位置
        path_intervention=obj_context.path_intervention,  # 需要人工介入时的说明文件
        path_post_apply_validation=obj_context.path_post_apply_validation,  # 修复落盘后的验证证据
        path_post_apply_equivalence=obj_context.path_post_apply_equivalence,  # 修复前后等价性证据
    )  # verification_result 中的 artifact map

    # 结果 payload 字段名保持旧 schema，便于 host 继续按固定 key 读取。
    return {
        "version": 1,  # verification_result 契约版本号
        "status": "completed",  # host 识别的 verify-repair 终态
        "automation_mode": obj_context.obj_options.str_automation_mode,  # 用户授权的写回等级
        "tb_mode": obj_context.obj_options.str_tb_mode,  # TB 物化路径选择
        "tb_language": obj_context.obj_options.str_tb_language,  # TB 文件语法族
        "readiness": obj_context.obj_options.str_readiness,  # validation readiness 档位
        "analysis_top_module": obj_context.dict_analysis["module_info"]["name"],  # 分析确认的顶层模块名
        "log_outcome": obj_context.dict_diagnosis["outcome"],  # 日志诊断分类结果
        "validation_ok": obj_context.obj_validation_report.ok(),  # 统一验证器通过标志
        "tb_mutation": obj_context.dict_tb_mutation,  # TB 源文件处置结论
        "rtl_mutation": obj_context.dict_rtl_mutation,  # RTL 写回策略和执行摘要
        "source_mutation": source_mutation_policy(
            obj_context.dict_tb_mutation,
            obj_context.dict_rtl_mutation,
        ),  # RTL 与 testbench 源文件的综合处置策略
        "artifacts": dict_artifacts,  # verification_result 内部相对 artifact 索引
    }

# artifact map 固定为旧文件名，路径存在性由上游写入保证。
def _artifact_map(
    *,
    dict_report_paths: dict[str, Path],
    path_intervention: Path | None,
    path_post_apply_validation: Path | None,
    path_post_apply_equivalence: Path | None,
) -> dict[str, str | None]:
    """
    生成 verification_result 的 artifact 映射。

    参数:
        dict_report_paths: 已写出的诊断类报告路径集合。
        path_intervention: RTL 需要人工确认时的说明文件路径。
        path_post_apply_validation: patch 应用后追加验证报告路径。
        path_post_apply_equivalence: patch 应用后的等价性报告路径。

    返回:
        verification_result.json 中使用的相对 artifact 路径映射。
    """

    # expected_trace 使用真实文件名，兼容未来 Markdown 命名调整。
    path_expected_trace = dict_report_paths["expected_trace"]  # 人读预期 trace 文件名来源

    # artifact map 保持旧相对文件名，避免影响 adapter 和测试断言。
    return {
        "analysis_path": "rtl_analysis.json",  # 模块接口分析报告文件名
        "project_analysis_path": "project_analysis.json",  # 多文件工程拓扑报告文件名
        "verification_plan_path": "verification_plan.json",  # 用例和观测点计划文件名
        "tb_contract_path": "tb_contract.json",  # staged testbench 契约文件名
        "tb_augment_plan_path": "tb_augment_plan.json",  # 既有 TB 增强计划文件名
        "tb_augment_diff_path": "tb_augment_diff.txt",  # TB 增强差异文本文件名
        "log_diagnosis_path": "log_diagnosis.json",  # 编译/仿真归因报告文件名
        "simulation_slice_path": "simulation_slice.json",  # 日志摘录报告文件名
        "timing_diagnostic_path": "timing_diagnostic.json",  # 时序重点信号报告文件名
        "expected_trace_path": str(path_expected_trace.name),  # 预期行为说明文件名
        "waveform_diff_path": "waveform_diff.json",  # 波形复核占位报告文件名
        "patch_candidate_path": "patch_candidate.json",  # 低风险修复候选文件名
        "rtl_patch_plan_path": "rtl_patch_plan.json",  # RTL 人工复核计划文件名
        "rtl_patch_diff_path": "rtl_patch_diff.txt",  # RTL 修改差异文本文件名
        "loop_state_path": "loop_state.json",  # resume 状态机文件名
        "validation_report_path": "validation_report.json",  # staged workspace 验证报告文件名
        "testcase_matrix_path": "testcase_matrix.json",  # 用例覆盖矩阵文件名
        "run_summary_path": "run_summary.json",  # 人读运行摘要文件名
        "synth_readiness_path": "synth_readiness.json",  # 综合后端准备度文件名
        "terminal_status_path": "terminal_status.json",  # host 轮询终态文件名
        "rtl_intervention_path": str(path_intervention.name) if path_intervention else None,  # 需要确认时的介入说明文件名
        "post_apply_validation_path": (
            str(path_post_apply_validation.name) if path_post_apply_validation else None
        ),  # patch 落盘后的验证证据文件名
        "post_apply_equivalence_path": (
            str(path_post_apply_equivalence.name) if path_post_apply_equivalence else None
        ),  # patch 后等价性占位报告文件名
    }

# facade 返回绝对/字符串路径索引，保持 integration adapter 兼容。
def _result_index(
    *,
    obj_context: SimpleNamespace,
    dict_verification_result: dict[str, Any],

    # 末端写入生成的路径进入 facade 返回字典。
    path_loop_state: Path,
    path_verification_result: Path,
    path_run_summary: Path,
    path_terminal_status: Path,
) -> dict[str, Any]:
    """
    生成 verify_existing 对外返回的路径索引。

    参数:
        obj_context: 最终状态和所有 artifact 路径的上下文。
        dict_verification_result: verification_result.json 的已组装正文。
        path_loop_state: loop_state.json 的实际路径。
        path_verification_result: verification_result.json 的实际路径。
        path_run_summary: run_summary.json 的实际路径。
        path_terminal_status: terminal_status.json 的实际路径。

    返回:
        对外 facade 继续使用的绝对路径字符串索引。
    """

    # 对外索引使用绝对路径字符串，继续兼容 integration adapter。
    return {
        "status": dict_verification_result["status"],  # verify-repair 对外状态
        "run_dir": str(obj_context.path_out_dir),  # run 目录绝对路径
        "analysis_path": str(obj_context.dict_analysis_result["analysis_path"]),  # RTL 分析绝对路径
        "project_analysis_path": str(
            obj_context.dict_analysis_result["project_analysis_path"]
        ),  # 工程分析绝对路径
        "verification_plan_path": str(obj_context.path_verification_plan),  # 验证计划绝对路径
        "tb_contract_path": str(obj_context.path_tb_contract),  # testbench 契约绝对路径
        "tb_augment_plan_path": str(obj_context.path_tb_augment_plan),  # testbench 增强计划绝对路径
        "tb_augment_diff_path": str(obj_context.path_out_dir / "tb_augment_diff.txt"),  # testbench diff 绝对路径
        "log_diagnosis_path": str(obj_context.dict_report_paths["log_diagnosis"]),  # 日志诊断绝对路径
        "simulation_slice_path": str(obj_context.dict_report_paths["simulation_slice"]),  # 仿真切片绝对路径
        "timing_diagnostic_path": str(obj_context.dict_report_paths["timing_diagnostic"]),  # 时序诊断绝对路径
        "expected_trace_path": str(obj_context.dict_report_paths["expected_trace"]),  # 预期行为绝对路径
        "waveform_diff_path": str(obj_context.dict_report_paths["waveform_diff"]),  # 波形差异绝对路径
        "patch_candidate_path": str(obj_context.path_patch_candidate),  # patch 候选绝对路径
        "rtl_patch_plan_path": str(obj_context.path_rtl_patch_plan),  # RTL patch 计划绝对路径
        "rtl_patch_diff_path": str(obj_context.path_out_dir / "rtl_patch_diff.txt"),  # RTL 修改差异文本绝对位置
        "loop_state_path": str(path_loop_state),  # resume 状态机绝对位置
        "verification_result_path": str(path_verification_result),  # host 主结果索引绝对位置
        "validation_report_path": str(obj_context.path_validation_report),  # staged workspace 验证证据绝对位置
        "testcase_matrix_path": str(obj_context.dict_report_paths["testcase_matrix"]),  # 用例覆盖矩阵绝对位置
        "run_summary_path": str(path_run_summary),  # 人读运行摘要绝对位置
        "synth_readiness_path": str(obj_context.dict_report_paths["synth_readiness"]),  # 综合准备度报告绝对位置
        "terminal_status_path": str(path_terminal_status),  # UI/host 轮询终态绝对位置
    }
