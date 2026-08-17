"""为 Verilog workflow 请求提供只读入口路由。"""

# future annotations 让 Path 和泛型类型提示保持惰性求值。
from __future__ import annotations

# dataclass 用于固定路由请求、事实和模式选择结果。
from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any

# ROUTE_ENTRY_MODES 是对外文档化的入口模式集合。
ROUTE_ENTRY_MODES = (  # Verilog workflow 可返回的入口模式全集
    "spec-first generation",  # 从规格归一化开始生成
    "plan-seeded generation",  # 从已确认 codegen plan 继续生成
    "existing-RTL assist/repair",  # 面向已有 RTL 的分析或修复
    "evidence-first debug/repair",  # 面向日志、波形或远程验证证据的诊断
)

# DIAGNOSIS_ROUTES 是 verify-repair 摘要使用的诊断路由集合。
DIAGNOSIS_ROUTES = (  # verify-repair 可返回的诊断分类全集
    "local_rtl_issue",  # 本地 RTL 逻辑或时序风险
    "spec_ambiguity",  # 规格约束不清或互相冲突
    "dut_tb_contract_drift",  # DUT 与 testbench 合同漂移
    "toolchain_issue",  # 编译器、仿真器或工具链失败
    "needs_external_validation",  # 仍缺少外部验证证据
    "unknown_or_mixed",  # 证据不足或多类问题混合
)

# SPEC_ARTIFACT_NAMES 描述 artifact_dir 中可表示规格存在的文件名。
SPEC_ARTIFACT_NAMES = ("spec.json", "_adapter_inputs/spec.json")  # 规格 artifact 候选路径

# PLAN_ARTIFACT_NAMES 描述 artifact_dir 中可表示 codegen plan 的文件名。
PLAN_ARTIFACT_NAMES = ("codegen_plan.json", "_adapter_inputs/codegen_plan.json")  # codegen plan artifact 候选路径

# RouteRequest 固定 route_verilog_entry 的兼容请求字段。
@dataclass(frozen=True)
class RouteRequest:
    """保存入口路由公开参数归一化前的请求字段。"""

    # 摘要文本只用于 remote 意图和证据关键词扫描。
    request_summary: str = ""  # remote 关键词扫描文本

    # 规格输入可以是路径，也可以是调用方已解析的映射。
    spec: str | Path | dict[str, Any] | None = None  # 规格来源

    # codegen plan 输入用于判断 plan-seeded 入口是否可直接执行。
    codegen_plan: str | Path | dict[str, Any] | None = None  # 生成计划来源

    # RTL 输入代表用户希望分析或修复已有设计。
    rtl: str | Path | list[str | Path] | None = None  # 已有设计输入

    # testbench 输入用于识别已有验证环境和合同漂移风险。
    testbench: str | Path | list[str | Path] | None = None  # 验证环境输入

    # 日志输入会让路由优先进入 evidence-first 诊断。
    logs: str | Path | list[str | Path] | None = None  # 失败日志证据

    # 波形输入表示已有仿真证据，需要先做诊断分类。
    waveform: str | Path | list[str | Path] | None = None  # 仿真波形证据

    # validation 报告输入可直接作为 evidence-first 的结构化证据。
    validation: str | Path | dict[str, Any] | None = None  # 结构化验证证据

    # artifact_dir 允许从历史产物中恢复规格或 codegen plan。
    artifact_dir: str | Path | None = None  # 历史产物根目录

    # 远程验证请求必须先暴露 remote selection 和 workspace 配置缺口。
    remote_validation_requested: bool = False  # 远程闭环请求开关

# ROUTE_REQUEST_FIELDS 限制兼容关键字，避免拼写错误被静默忽略。
ROUTE_REQUEST_FIELDS = frozenset(RouteRequest.__dataclass_fields__)  # route_verilog_entry 可接受的兼容字段

# RouteFacts 固定 route_verilog_entry 的只读输入事实。
@dataclass(frozen=True)
class RouteFacts:
    """保存入口路由前已经归一化的请求事实。"""

    # summary 是证据关键词扫描使用的原始请求摘要。
    summary: str  # 原始请求摘要

    # spec_requested 表示用户或 artifact_dir 触发了规格入口。
    spec_requested: bool  # 规格入口触发标志

    # spec_present 表示规格内容已经可供生成前处理读取。
    spec_present: bool  # 规格内容可用标志

    # plan_requested 表示显式 plan 或恢复目录触发了 plan-seeded 判断。
    plan_requested: bool  # 计划入口触发标志

    # plan_present 表示 codegen plan 内容或文件真实可用。
    plan_present: bool  # 计划内容可用标志

    # plan_ready 同时要求 ready_for_generation 为真且 open_questions 为空。
    plan_ready: bool  # 计划可生成标志

    # rtl_paths 保留用户声明的 RTL 路径，包括当前不存在的项。
    rtl_paths: list[Path]  # 原始 RTL 路径

    # tb_paths 保留用户声明的 testbench 路径。
    tb_paths: list[Path]  # DUT 配套验证入口候选

    # log_paths 保留用户声明的日志证据路径。
    log_paths: list[Path]  # 原始日志路径

    # wave_paths 保留用户声明的波形证据路径。
    wave_paths: list[Path]  # 原始波形路径

    # existing_rtl_paths 只包含文件系统中真实存在的 RTL。
    existing_rtl_paths: list[Path]  # 可读 RTL 文件

    # existing_tb_paths 只包含可直接读取的 testbench。
    existing_tb_paths: list[Path]  # 已通过文件系统检查的验证入口

    # existing_log_paths 用于日志关键词扫描。
    existing_log_paths: list[Path]  # 可扫描日志文件

    # existing_wave_paths 用于判定 waveform 输入已满足。
    existing_wave_paths: list[Path]  # 可用波形文件

    # validation_payload 保存已解析的 validation 报告映射。
    validation_payload: dict[str, Any]  # validation 报告内容

    # validation_present 表示 validation 输入足以触发 evidence-first。
    validation_present: bool  # validation 可用标志

    # missing_artifacts 记录显式提供但当前不可访问的路径。
    missing_artifacts: list[Path]  # 不可访问输入路径

    # remote_validation_requested 保留调用方是否请求远程闭环。
    remote_validation_requested: bool  # 远程验证意图

# RouteSelection 保存入口模式、推荐流程和恢复提示。
@dataclass(frozen=True)
class RouteSelection:
    """保存基于事实选择出的 workflow 路由。"""

    # entry_mode 是 route_decision 对外展示的主分类。
    entry_mode: str  # 入口模式名称

    # recommended_flow 指向调用方下一步应进入的主流程。
    recommended_flow: str  # 推荐主流程名称

    # safe_recovery_hint 给出该入口的保守恢复策略标签。
    safe_recovery_hint: str  # 恢复策略标签

# route_verilog_entry 是对外只读路由入口。
def route_verilog_entry(*, request: RouteRequest | None = None, **overrides: Any) -> dict[str, Any]:
    """
    根据可用事实返回不会修改源文件或产物的入口路由。

    :param request: 可选的入口路由请求对象。
    :param overrides: 兼容旧调用方式的 route 字段关键字。
    :return: route_decision 契约字典。
    :raises TypeError: 当兼容关键字不是受支持 route 字段时抛出。
    """

    # route request 合并显式对象和兼容关键字，保持旧 API 可用。
    route_request_route_request = _route_request_from_kwargs(request=request, overrides=overrides)  # 归一化前的路由请求

    # route facts 是后续决策的唯一输入，避免分支重复读取文件系统。
    route_facts_facts_route = _collect_route_facts(route_request_route_request)  # 后续路由决策使用的归一化事实

    # selection 只描述入口模式和推荐主流程。
    route_selection_selection_route = _select_route(route_facts_facts_route)  # 基于事实选择出的 workflow 路由

    # required/present/missing 输入集合分开计算，便于测试定位。
    list_required_inputs = _required_inputs(  # route_decision 对外展示的必需输入清单
        route_selection_selection_route.entry_mode,  # 缺失输入按该入口模式解释
        remote_validation_requested=route_request_route_request.remote_validation_requested,  # 远程闭环是否加入必需项
    )

    # present_inputs 只统计真实存在或明确提供的输入。
    set_present_inputs = _present_inputs(route_facts_facts_route)  # 当前请求已经满足的输入集合

    # missing_inputs 是 release gate 和 CLI 展示的核心字段。
    list_missing_inputs = _missing_inputs(  # 用户下一步需要补齐的输入名称
        route_facts_facts_route,  # 已归一化的路由事实
        route_selection_selection_route.entry_mode,  # 已选入口模式
        list_required_inputs,  # 当前模式要求的输入名称
        set_present_inputs,  # 本次请求已经满足的输入名称
    )

    # blocking findings 汇总缺失 artifact、远程请求和日志特征。
    list_blocking_findings = _blocking_findings(route_facts_facts_route)  # 当前请求的阻断发现列表

    # next_action 单独计算，避免返回字典中嵌套长调用。
    str_next_action = _next_action(  # route_decision 展示给用户的下一步动作
        route_selection_selection_route.entry_mode,  # next_action 的主分支键
        remote_validation_requested=route_request_route_request.remote_validation_requested,  # 是否需要远程配置前置
        plan_ready=route_facts_facts_route.plan_ready,  # codegen plan 是否已可直接作为 seed
    )

    # 返回结构保持 v0.3.0 route workflow 契约。
    return {
        "version": 1,
        "recommended_flow": route_selection_selection_route.recommended_flow,
        "entry_mode": route_selection_selection_route.entry_mode,
        "required_inputs": list_required_inputs,
        "missing_inputs": _dedupe(list_missing_inputs),
        "next_action": str_next_action,
        "safe_recovery_hint": route_selection_selection_route.safe_recovery_hint,
        "blocking_findings": _dedupe(list_blocking_findings),
        "provenance_policy": _provenance_policy(),
    }

# _route_request_from_kwargs 合并新式请求对象和旧式关键字。
def _route_request_from_kwargs(request: RouteRequest | None, overrides: dict[str, Any]) -> RouteRequest:
    """
    合并 RouteRequest 和旧式 route_verilog_entry 关键字。

    :param request: 可选的新式请求对象。
    :param overrides: 旧式关键字参数映射。
    :return: 合并后的冻结请求对象。
    :raises TypeError: 当 overrides 中出现未知 route 字段时抛出。
    """

    # 未提供请求对象时从默认 RouteRequest 起步。
    route_request_base: RouteRequest = request or RouteRequest()  # 合并前的基础请求

    # 无兼容关键字时直接复用请求对象。
    if not overrides:

        # 直接返回可以避免不必要的 dataclass 复制。
        return route_request_base

    # unknown_fields 捕获拼写错误或已废弃字段。
    list_unknown_fields = sorted(str_key for str_key in overrides if str_key not in ROUTE_REQUEST_FIELDS)  # 未知 route 字段

    # 未知字段不能被静默忽略，否则 CLI JSON 拼写错误会变成错误路由。
    if list_unknown_fields:

        # 报告所有未知字段，便于一次性修正请求 JSON。
        raise TypeError(f"> ERR: [Python] Unsupported route request field(s): {', '.join(list_unknown_fields)}")

    # 用兼容关键字覆盖请求对象中的同名字段。
    return replace(route_request_base, **overrides)

# classify_diagnosis_route 把 verify-repair 证据压缩为稳定摘要标签。
def classify_diagnosis_route(
    *,
    diagnosis: dict[str, Any] | None = None,
    validation_report: Any | None = None,
    tb_contract: dict[str, Any] | None = None,
) -> str:
    """
    把 verify-repair 证据分类为稳定诊断路由。

    :param diagnosis: verify-repair 诊断摘要。
    :param validation_report: validation 报告对象或字典。
    :param tb_contract: testbench 合同摘要。
    :return: `DIAGNOSIS_ROUTES` 中的诊断分类。
    """

    # diagnosis 可为空，空值按 unknown 证据处理。
    dict_diagnosis = diagnosis or {}  # verify-repair 诊断对象

    # outcome 是诊断优先级最高的状态字段。
    str_outcome = str(dict_diagnosis.get("outcome") or "")  # verify-repair 结果状态

    # validation report 支持对象或 dict 两种输入。
    dict_report = _validation_report_dict(validation_report)  # validation 报告字典

    # issues 提供 source/stage 维度的补充证据。
    list_issues = dict_report.get("issues", []) if isinstance(dict_report.get("issues", []), list) else []  # 用于诊断分类的 validation issue 列表

    # source 集合用于识别 spec 或 toolchain 问题。
    set_issue_sources: set[str] = set()  # issue source 字段集合

    # stage 集合用于识别 compile 阶段失败。
    set_issue_stages: set[str] = set()  # compile 分类候选阶段池

    # 只从 dict issue 中提取 source/stage 字段。
    for dict_item in list_issues:

        # 非 dict issue 不能提供结构化诊断字段。
        if isinstance(dict_item, dict):

            # source 字段用于识别 spec/toolchain 风险。
            set_issue_sources.add(str(dict_item.get("source") or ""))

            # stage 字段用于识别 compile 阶段失败。
            set_issue_stages.add(str(dict_item.get("stage") or ""))

    # not_run 表示还需要外部验证证据。
    if str_outcome == "not_run":

        # 外部验证缺失时必须显式返回 needs_external_validation。
        return "needs_external_validation"

    # spec_issue 优先归类为规格歧义。
    if "spec_issue" in set_issue_sources:

        # 规格问题要求回到需求澄清，而不是直接修 RTL。
        return "spec_ambiguity"

    # compile/toolchain 证据优先归类为工具链或编译路径问题。
    if str_outcome == "compile_error" or "toolchain_issue" in set_issue_sources or "compile" in set_issue_stages:

        # 编译失败先归为 toolchain_issue，避免误报 RTL 语义缺陷。
        return "toolchain_issue"

    # assertion/protocol/timeout 属于本地 RTL 或 DUT/TB 合同问题。
    if str_outcome in {"assertion_fail", "protocol_violation", "timeout"}:

        # augment 模式下优先提示 DUT/TB 合同漂移。
        if tb_contract and tb_contract.get("tb_mode") == "augment":

            # 增强 testbench 失败通常需要先复核合同。
            return "dut_tb_contract_drift"

        # 默认归类为 RTL 本地问题。
        return "local_rtl_issue"

    # pass 只说明当前证据未定位具体问题。
    if str_outcome == "pass":

        # 保守返回 unknown，避免把 pass 包装成根因。
        return "unknown_or_mixed"

    # 其他未知状态统一走混合/未知。
    return "unknown_or_mixed"

# _collect_route_facts 归一化 route_verilog_entry 的所有输入。
def _collect_route_facts(route_request: RouteRequest) -> RouteFacts:
    """
    把原始请求参数归一化为只读路由事实。

    :param route_request: 合并后的入口路由请求对象。
    :return: 供入口选择和报告生成使用的只读事实。
    """

    # request_summary 只参与关键词风险判断。
    str_summary = str(route_request.request_summary or "")  # 用于 remote 关键词扫描的请求摘要

    # spec artifact 既可来自显式输入，也可来自 artifact_dir。
    bool_spec_artifact_present = _artifact_exists(route_request.artifact_dir, SPEC_ARTIFACT_NAMES)  # 恢复目录规格证据

    # spec_requested 表示调用方意图中包含规格上下文。
    bool_spec_requested = route_request.spec is not None or bool_spec_artifact_present  # 规格入口触发状态

    # spec_present 表示规格事实真实可用。
    bool_spec_present = _value_present(route_request.spec) or bool_spec_artifact_present  # 规格内容可读状态

    # plan artifact 用于从中断运行目录接续生成计划。
    bool_plan_artifact_present = _artifact_exists(route_request.artifact_dir, PLAN_ARTIFACT_NAMES)  # 中断运行目录中的计划证据

    # plan payload 用于判断是否 ready_for_generation。
    dict_plan_payload = (  # 用于读取 ready/open_questions 的 codegen plan 内容
        _load_mapping(route_request.codegen_plan)  # 显式传入的计划映射优先
        or _load_artifact_mapping(route_request.artifact_dir, PLAN_ARTIFACT_NAMES)  # 恢复目录中的计划作为后备
    )

    # plan_requested 表示用户提供或 artifact_dir 暗示了 plan。
    bool_plan_requested = route_request.codegen_plan is not None or bool_plan_artifact_present  # plan 入口触发状态

    # plan_present 表示 plan 文件或 dict 已经可用。
    bool_plan_present = _value_present(route_request.codegen_plan) or bool_plan_artifact_present  # codegen plan 可读状态

    # ready 标记必须显式为真。
    bool_plan_marked_ready = bool(dict_plan_payload.get("ready_for_generation"))  # plan 自身声明可进入生成

    # open questions 非空时不能直接生成。
    bool_plan_has_questions = bool(dict_plan_payload.get("open_questions"))  # 计划仍等待用户回答的问题

    # plan_ready 必须同时满足 ready 标记且没有 open questions。
    bool_plan_ready = bool_plan_marked_ready and not bool_plan_has_questions  # plan 可以安全作为生成 seed

    # 路径类输入全部转成 list[Path]。
    list_rtl_paths = _path_list(route_request.rtl)  # 调用方声明的 RTL 候选路径

    # testbench 路径独立统计，避免和 RTL 混淆。
    list_tb_paths = _path_list(route_request.testbench)  # DUT 合同核验入口候选

    # log 路径用于 evidence-first 入口。
    list_log_paths = _path_list(route_request.logs)  # evidence-first 可读取的日志候选路径

    # waveform 可作为仿真后态证据，不参与源码路径判断。
    list_wave_paths = _path_list(route_request.waveform)  # 仿真时序证据候选文件

    # existing_* 只保留文件系统中真实存在的路径。
    list_existing_rtl_paths = _existing_paths(list_rtl_paths)  # 可直接读取的 RTL 文件

    # existing testbench 输入决定 present_inputs 中的 testbench 项。
    list_existing_tb_paths = _existing_paths(list_tb_paths)  # 已确认存在的验证入口文件

    # existing logs 用于实际读取风险关键词。
    list_existing_log_paths = _existing_paths(list_log_paths)  # 可扫描关键词的日志文件

    # existing waveforms 用于 present_inputs 中的 waveform 项。
    list_existing_wave_paths = _existing_paths(list_wave_paths)  # 可交给诊断流程的波形文件

    # validation payload 可来自 dict 或 JSON 文件。
    dict_validation_payload = _load_mapping(route_request.validation)  # 解析后的 validation 报告映射

    # validation_present 表示报告输入可供 evidence-first 使用。
    bool_validation_present = _value_present(route_request.validation) or bool(dict_validation_payload)  # validation 可用状态

    # 将显式 artifact 路径和已归一化路径列表传入 missing kwargs，供 risk flag 和 missing input 检查。
    dict_missing_kwargs = {  # _missing_artifact_paths 需要检查的 artifact 输入
        "spec": route_request.spec,  # 规格路径或字典输入
        "codegen_plan": route_request.codegen_plan,  # codegen plan 路径或字典输入
        "rtl_paths": list_rtl_paths,  # 已归一化 RTL 路径
        "tb_paths": list_tb_paths,  # 缺失检测要核验的验证入口路径
        "log_paths": list_log_paths,  # 已归一化日志路径
        "wave_paths": list_wave_paths,  # 已归一化波形路径
        "validation": route_request.validation,  # 需要核验存在性的报告输入
    }

    # missing_artifacts 只记录显式提供却不可访问的路径。
    list_missing_artifacts = _missing_artifact_paths(**dict_missing_kwargs)  # 用于风险提示的缺失文件集合

    # facts kwargs 固定 RouteFacts 字段，后续步骤不得再重新读取输入参数。
    dict_facts_kwargs = {  # RouteFacts 构造字段
        "summary": str_summary,  # remote 关键词和用户意图扫描文本
        "spec_requested": bool_spec_requested,  # 规格入口由输入或恢复目录触发
        "spec_present": bool_spec_present,  # 规格事实已经可读
        "plan_requested": bool_plan_requested,  # plan-seeded 入口由输入或缓存触发
        "plan_present": bool_plan_present,  # 计划事实已经可读
        "plan_ready": bool_plan_ready,  # 计划可跳过人工补问并直接生成
        "rtl_paths": list_rtl_paths,  # 保留不存在项以便报告缺失 RTL
        "tb_paths": list_tb_paths,  # 保留验证入口原始候选以便报告缺口
        "log_paths": list_log_paths,  # 保留日志候选以便扫描错误关键词
        "wave_paths": list_wave_paths,  # 保留波形候选以便诊断工具接续
        "existing_rtl_paths": list_existing_rtl_paths,  # 通过 exists 过滤后的 RTL 文件
        "existing_tb_paths": list_existing_tb_paths,  # 已确认可读的验证入口集合
        "existing_log_paths": list_existing_log_paths,  # 通过 exists 过滤后的日志文件
        "existing_wave_paths": list_existing_wave_paths,  # 通过 exists 过滤后的波形文件
        "validation_payload": dict_validation_payload,  # 归一化后的 validation JSON 内容
        "validation_present": bool_validation_present,  # validation 是否足以进入 evidence-first
        "missing_artifacts": list_missing_artifacts,  # risk flag 使用的不可访问文件
        "remote_validation_requested": route_request.remote_validation_requested,  # 远程验证请求是否参与路由
    }

    # 返回冻结事实对象给 route selection。
    return RouteFacts(**dict_facts_kwargs)

# _select_route 根据只读事实选择 workflow 入口。
def _select_route(route_facts: RouteFacts) -> RouteSelection:
    """
    根据路由事实选择入口模式、推荐流程和恢复提示。

    :param route_facts: 已归一化的路由事实。
    :return: 路由入口模式和推荐流程。
    """

    # 远程请求、日志、波形或 validation 报告都先进入 evidence-first。
    if _has_evidence_inputs(route_facts):

        # evidence-first 路径必须先分类证据再修复。
        return RouteSelection(
            entry_mode="evidence-first debug/repair",
            recommended_flow="verify_existing_verilog",
            safe_recovery_hint="inspect_diagnostics_before_mutation",
        )

    # 已有 RTL 或 testbench 输入进入 existing RTL assist/repair。
    if route_facts.rtl_paths or route_facts.tb_paths:

        # 有规格时建议 verify，没有规格时先 analyze。
        str_existing_flow = "verify_existing_verilog" if route_facts.spec_requested else "analyze_existing_verilog"  # existing RTL 推荐流程

        # existing RTL 路径必须保留源文件并显式选择自动化边界。
        return RouteSelection(
            entry_mode="existing-RTL assist/repair",
            recommended_flow=str_existing_flow,
            safe_recovery_hint="preserve_sources_and_choose_explicit_automation_mode",
        )

    # ready plan 和 spec 同时存在时走 plan-seeded。
    if route_facts.spec_requested and route_facts.plan_requested and route_facts.plan_ready:

        # plan 只能作为 seed，仍需保持验证门禁。
        return RouteSelection(
            entry_mode="plan-seeded generation",
            recommended_flow="run_verilog_workflow",
            safe_recovery_hint="resume_requirements_if_plan_drift_is_detected",
        )

    # 默认从规格驱动生成入口开始。
    return RouteSelection(
        entry_mode="spec-first generation",
        recommended_flow="run_verilog_workflow",
        safe_recovery_hint="complete_requirements_before_generation",
    )

# _has_evidence_inputs 判断请求是否已经处于证据驱动场景。
def _has_evidence_inputs(route_facts: RouteFacts) -> bool:
    """
    判断请求是否应进入 evidence-first debug/repair。

    :param route_facts: 已归一化的路由事实。
    :return: 请求包含证据类输入时为 `True`。
    """

    # 远程验证请求必须先走证据/配置确认路径。
    if route_facts.remote_validation_requested:

        # remote validation 不能被当成普通生成入口。
        return True

    # 显式日志输入表示用户已经有失败证据。
    if route_facts.log_paths:

        # 日志驱动路径需要先分类诊断。
        return True

    # 显式波形输入同样属于证据驱动。
    if route_facts.wave_paths:

        # 波形证据需要先分类诊断。
        return True

    # validation report 是 evidence-first 的最高结构化证据。
    if route_facts.validation_present:

        # validation report 已经是证据输入。
        return True

    # 没有证据输入时继续生成或 existing RTL 路由。
    return False

# _required_inputs 返回入口模式的最小必需输入。
def _required_inputs(entry_mode: str, *, remote_validation_requested: bool) -> list[str]:
    """
    返回指定入口模式需要的输入名称列表。

    :param entry_mode: 已选 workflow 入口模式。
    :param remote_validation_requested: 是否请求远程验证闭环。
    :return: 当前入口模式要求的输入名称列表。
    """

    # spec-first 只要求规格，但后续会补 codegen_plan 缺失提示。
    if entry_mode == "spec-first generation":

        # spec 是规格驱动生成入口的根输入。
        return ["spec"]

    # plan-seeded 必须同时有 spec 和 codegen_plan。
    if entry_mode == "plan-seeded generation":

        # plan 不能替代规格确认。
        return ["spec", "codegen_plan"]

    # existing RTL 路径至少需要 RTL。
    if entry_mode == "existing-RTL assist/repair":

        # rtl 是分析和修复已有设计的根输入。
        return ["rtl"]

    # remote evidence-first 需要远程选择和工作区设置。
    if remote_validation_requested:

        # validation_artifacts 是远程验证的本地证据入口。
        return ["validation_artifacts", "remote_selection", "remote_workspace_settings"]

    # 普通 evidence-first 默认从日志开始。
    return ["logs"]

# _present_inputs 统计当前请求已满足的输入集合。
def _present_inputs(route_facts: RouteFacts) -> set[str]:
    """
    根据归一化事实返回已经存在的输入名称集合。

    :param route_facts: 已归一化的路由事实。
    :return: 当前请求已经满足的输入名称集合。
    """

    # present 集合只收录真实可用的输入。
    set_present: set[str] = set()  # 当前请求已满足的输入集合

    # 规格存在时可满足 spec。
    if route_facts.spec_present:

        # 添加 spec 满足项。
        set_present.add("spec")

    # codegen plan 存在时可满足 codegen_plan。
    if route_facts.plan_present:

        # 计划内容已经就绪，codegen_plan 不再列为缺口。
        set_present.add("codegen_plan")

    # existing RTL 文件存在时可满足 rtl。
    if route_facts.existing_rtl_paths:

        # 至少一个设计文件可读，rtl 输入已经满足。
        set_present.add("rtl")

    # 验证入口可读时，missing_inputs 不再要求用户补 testbench。
    if route_facts.existing_tb_paths:

        # 可读验证入口存在，testbench 合同检查可继续。
        set_present.add("testbench")

    # 日志文件已存在时，证据优先路由可以展开文本扫描。
    if route_facts.existing_log_paths:

        # 日志证据已经落盘，logs 诊断输入可用。
        set_present.add("logs")

    # 波形文件已存在时，诊断流程可进入时序证据分支。
    if route_facts.existing_wave_paths:

        # 波形文件可交给诊断流程，waveform 输入可用。
        set_present.add("waveform")

    # validation 已加载时，结构化诊断 artifact 视为满足。
    if route_facts.validation_present:

        # 结构化报告已经解析，validation_artifacts 可用于路由。
        set_present.add("validation_artifacts")

    # remote request 只说明请求存在，不等于配置满足。
    if route_facts.remote_validation_requested:

        # 远程闭环意图已声明，后续风险提示需要保留该事实。
        set_present.add("remote_validation_request")

    # 返回完整 present 集合。
    return set_present

# _missing_inputs 计算面向 CLI 和报告的缺失输入。
def _missing_inputs(
    route_facts: RouteFacts,
    entry_mode: str,
    list_required_inputs: list[str],
    set_present_inputs: set[str],
) -> list[str]:
    """
    根据 required/present 集合生成缺失输入列表。

    :param route_facts: 已归一化的路由事实。
    :param entry_mode: 已选 workflow 入口模式。
    :param list_required_inputs: 当前入口模式要求的输入名称。
    :param set_present_inputs: 当前请求已经满足的输入集合。
    :return: 尚需补齐的输入名称列表。
    """

    # required 中未满足的字段先进入 missing。
    list_missing: list[str] = []  # required 输入中尚未满足的名称列表

    # 按 required 顺序检查，保证报告顺序稳定。
    for str_item in list_required_inputs:

        # present 集合不含该项时才记录缺失。
        if str_item not in set_present_inputs:

            # 缺失项追加到 missing 输入列表。
            list_missing.append(str_item)

    # spec-first 还需要提示 codegen_plan 会在生成前构建。
    if entry_mode == "spec-first generation" and not route_facts.plan_present:

        # codegen_plan 缺失是规格入口的下一步动作。
        list_missing.append("codegen_plan")

    # remote evidence-first 必须显式暴露远程配置缺口。
    if entry_mode == "evidence-first debug/repair" and route_facts.remote_validation_requested:

        # remote_selection 和 remote_workspace_settings 不由 request 本身满足。
        for str_item in ("remote_selection", "remote_workspace_settings"):

            # 避免重复添加已经满足或已缺失的项。
            if str_item not in list_missing and str_item not in set_present_inputs:

                # 记录远程配置缺失项。
                list_missing.append(str_item)

    # 返回缺失输入列表，调用方再去重。
    return list_missing

# _next_action 返回面向用户的下一步只读建议。
def _next_action(entry_mode: str, *, remote_validation_requested: bool, plan_ready: bool) -> str:
    """
    返回入口模式对应的下一步动作提示。

    :param entry_mode: 已选 workflow 入口模式。
    :param remote_validation_requested: 是否请求远程验证闭环。
    :param plan_ready: codegen plan 是否可直接作为生成 seed。
    :return: 面向用户的下一步动作提示。
    """

    # 远程验证必须先确认 erie-remote-ssh 配置。
    if remote_validation_requested:

        # 提示用户先完成远程选择和 workspace 设置。
        return (
            "Resolve erie-remote-ssh server selection and remote workspace settings "
            "before any external validation claim."
        )

    # spec-first 需要规格归一化和计划生成。
    if entry_mode == "spec-first generation":

        # 提示 Verilog-2001 计划和验证门禁。
        return (
            "Normalize requirements, build a Verilog-2001 codegen_plan, "
            "and run the mandatory validation gate."
        )

    # plan-seeded 需要根据 plan ready 状态提示。
    if entry_mode == "plan-seeded generation":

        # ready plan 可以作为生成 seed，但不能跳过验证。
        if plan_ready:

            # 保留需求确认和 validation gate。
            return (
                "Use the plan as seed only; preserve requirements confirmation "
                "and the validation gate before RTL use."
            )

        # plan 处于待确认状态时先处理 open questions。
        return "Review open codegen_plan questions before any RTL emission."

    # existing RTL 入口先分析或验证已有代码。
    if entry_mode == "existing-RTL assist/repair":

        # 提示显式 automation mode。
        return "Analyze or verify existing RTL with an explicit automation mode before mutation."

    # evidence-first 默认先分类证据。
    return "Classify logs or validation evidence before selecting repair or rerun."

# _blocking_findings 根据路由事实提取阻断发现。
def _blocking_findings(route_facts: RouteFacts) -> list[str]:
    """
    根据请求摘要、日志和 validation 报告生成阻断发现。

    :param route_facts: 已归一化的路由事实。
    :return: 按发现顺序排列的阻断发现列表。
    """

    # findings 按发现顺序记录，最后由调用方去重。
    list_findings: list[str] = []  # 路由报告中的阻断发现序列

    # summary 用于捕获用户文本里的 remote 意图。
    str_lower_summary = route_facts.summary.lower()  # 小写请求摘要

    # remote 显式请求或摘要提到 remote 都标记需要远程配置。
    if route_facts.remote_validation_requested or "remote" in str_lower_summary:

        # 远程验证声明必须依赖后续 remote gate。
        list_findings.append("remote_validation_requested")

    # 显式输入不存在时标记 artifact 缺失。
    if route_facts.missing_artifacts:

        # 缺失 artifact 会影响 present_inputs 判断。
        list_findings.append("missing_artifact_inputs")

    # 日志内容只从实际存在的日志路径读取。
    str_combined_logs = "\n".join(_safe_read_text(path_log) for path_log in route_facts.existing_log_paths).lower()  # 合并后的日志文本

    # 编译关键字标记 compile failure 发现。
    if any(str_token in str_combined_logs for str_token in ("syntax error", "compile error", "** error", "fatal")):

        # compile_failure 提示先处理编译证据。
        list_findings.append("compile_failure")

    # timeout 关键字标记仿真超时发现。
    if "timeout" in str_combined_logs:

        # sim_timeout 提示仿真可能未收敛。
        list_findings.append("sim_timeout")

    # testbench 协议关键字标记 DUT/TB 合同问题。
    if any(str_token in str_combined_logs for str_token in ("[tb_error]", "protocol violation", "mismatch")):

        # DUT/TB 合同问题需要先看接口和时序约定。
        list_findings.append("dut_tb_contract_drift")

    # validation issue 中的 toolchain_issue 也进入阻断发现。
    list_validation_issues = _validation_issues(route_facts.validation_payload)  # validation 报告 issue 列表

    # toolchain source 可能说明验证工具链失败；当前默认没有发现 toolchain issue。
    bool_has_toolchain_issue = False  # validation issues 是否包含 source=toolchain_issue

    # 逐条扫描 issue，避免多行 any 表达式触发可读性误判。
    for dict_item in list_validation_issues:

        # 只有 dict issue 才可能携带 source 字段。
        if isinstance(dict_item, dict) and dict_item.get("source") == "toolchain_issue":

            # 一旦发现工具链 issue 就设置标志。
            bool_has_toolchain_issue = True  # validation 报告已确认存在工具链来源 issue

            # 已有足够证据，不再继续扫描。
            break

    # 工具链 issue 不能归咎于 RTL 生成逻辑。
    if bool_has_toolchain_issue:

        # 工具链问题不能包装成 RTL 功能错误。
        list_findings.append("toolchain_issue")

    # 返回发现的阻断项。
    return list_findings

# _validation_report_dict 兼容对象式 validation report。
def _validation_report_dict(validation_report: Any | None) -> dict[str, Any]:
    """
    把 validation_report 归一化为 dict。

    :param validation_report: validation 报告对象、字典或空值。
    :return: validation 报告字典；不可用时返回空字典。
    """

    # 对象式报告优先使用 to_dict。
    if hasattr(validation_report, "to_dict"):

        # to_dict 结果可能仍需类型检查。
        obj_report = validation_report.to_dict()  # validation report 对象展开结果

        # dict 结果直接返回。
        if isinstance(obj_report, dict):

            # 返回对象展开出的报告字典。
            return obj_report

        # 非 dict 结果视为无报告。
        return {}

    # dict 输入复制后返回。
    if isinstance(validation_report, dict):

        # 浅拷贝避免调用方后续修改影响本函数。
        return dict(validation_report)

    # 其他输入不构成 validation report。
    return {}

# _validation_issues 读取 validation payload 中的 issue 列表。
def _validation_issues(dict_validation: dict[str, Any]) -> list[Any]:
    """
    返回 validation payload 中的 issues 列表。

    :param dict_validation: validation 报告映射。
    :return: issues 列表；字段不存在或类型不符时返回空列表。
    """

    # issues 只有 list 类型才可遍历。
    obj_issues = dict_validation.get("issues", [])  # validation issues 原始字段

    # list 输入直接返回。
    if isinstance(obj_issues, list):

        # 返回 issue 列表给风险分类。
        return obj_issues

    # 非 list 输入按空 issues 处理。
    return []

# _path_list 把单路径或路径列表归一化为 list[Path]。
def _path_list(value: str | Path | list[str | Path] | None) -> list[Path]:
    """
    把可选路径输入归一化为 Path 列表。

    :param value: 单路径、路径列表或空值。
    :return: 归一化后的 Path 列表。
    """

    # None 表示调用方没有提供该类输入。
    if value is None:

        # 空列表保持调用方判断简单。
        return []

    # list 输入逐项转成 Path。
    if isinstance(value, list):

        # 保留用户提供顺序，便于报告定位。
        return [Path(item) for item in value]

    # 单路径输入包装成列表。
    return [Path(value)]

# _load_mapping 读取 dict 或 JSON 文件。
def _load_mapping(value: str | Path | dict[str, Any] | None) -> dict[str, Any]:
    """
    从 dict 或 JSON 路径读取映射对象。

    :param value: 映射对象、JSON 路径或空值。
    :return: 可用映射；不可读取或非 object JSON 时返回空字典。
    """

    # None 表示没有可读取映射。
    if value is None:

        # 空 dict 保持调用方合并逻辑简单。
        return {}

    # dict 输入复制一份，避免修改调用方对象。
    if isinstance(value, dict):

        # 返回浅拷贝映射。
        return dict(value)

    # 文件路径必须存在且是普通文件。
    path_value = Path(value)  # 候选 JSON 文件路径

    # 不存在或非文件时按不可用处理。
    if not path_value.exists() or not path_value.is_file():

        # 空 dict 表示映射不可用。
        return {}

    # JSON 解析失败时保守返回空映射。
    try:

        # 只读取 UTF-8 JSON，保持 artifact 契约稳定。
        obj_object_loaded: object = json.loads(path_value.read_text(encoding="utf-8"))  # JSON 解码后的对象

    # JSON 格式错误时不能参与路由映射。
    except json.JSONDecodeError:

        # 非法 JSON 不能参与路由 ready 判断。
        return {}

    # 只有 dict payload 才能作为映射返回。
    if isinstance(obj_object_loaded, dict):

        # 返回 JSON 映射对象。
        return obj_object_loaded

    # 非 dict JSON 不满足 plan/validation 契约。
    return {}

# _load_artifact_mapping 从 artifact_dir 的候选路径读取第一个映射。
def _load_artifact_mapping(artifact_dir: str | Path | None, names: tuple[str, ...]) -> dict[str, Any]:
    """
    从 artifact_dir 候选文件中读取第一个可用映射。

    :param artifact_dir: 历史产物目录。
    :param names: 相对候选 JSON 文件名。
    :return: 第一个可用 JSON 映射；未命中时返回空字典。
    """

    # artifact_dir 为空时没有候选文件。
    if artifact_dir is None:

        # 空 dict 表示没有 artifact 映射。
        return {}

    # artifact_dir 先转为 Path，避免在生成器表达式里重复归一化。
    path_root = Path(artifact_dir)  # artifact 候选文件所在根目录

    # 按候选顺序读取 mapping。
    for str_name in names:

        # 当前候选文件的 JSON 映射。
        dict_loaded = _load_mapping(path_root / str_name)  # 当前 artifact JSON 映射

        # 找到第一个非空映射即返回。
        if dict_loaded:

            # 返回第一个可用 artifact 映射。
            return dict_loaded

    # 没有候选映射时返回空 dict。
    return {}

# _value_present 判断 dict 或路径输入是否可用。
def _value_present(value: str | Path | dict[str, Any] | None) -> bool:
    """
    判断输入值是否表示真实存在的内容。

    :param value: 路径、映射或空值。
    :return: 输入代表可用内容时为 `True`。
    """

    # None 明确表示不存在。
    if value is None:

        # 返回 False 供 present_inputs 使用。
        return False

    # dict 输入表示调用方已经提供了 payload。
    if isinstance(value, dict):

        # dict 即使为空也表示显式提供。
        return True

    # 路径输入必须真实存在。
    path_value = Path(value)  # 待检查路径

    # 返回文件系统存在性。
    return path_value.exists()

# _existing_paths 过滤真实存在的路径。
def _existing_paths(paths: list[Path]) -> list[Path]:
    """
    返回输入列表中真实存在的路径。

    :param paths: 待检查的路径列表。
    :return: 真实存在的路径列表。
    """

    # 列表推导只保留存在路径，顺序不变。
    return [
        path_item
        for path_item in paths
        if path_item.exists()
    ]

# _missing_artifact_paths 收集显式输入中当前不存在的 artifact 路径。
def _missing_artifact_paths(
    *,
    # spec 路径形态需要检查存在性，映射形态视为已加载内容。
    spec: str | Path | dict[str, Any] | None,
    # codegen_plan 路径形态需要检查存在性，映射形态视为已加载计划。
    codegen_plan: str | Path | dict[str, Any] | None,
    # rtl_paths 是已归一化 RTL 路径列表。
    rtl_paths: list[Path],
    # tb_paths 专门承载 DUT 配套验证入口，不与 rtl_paths 混合。
    tb_paths: list[Path],
    # log_paths 是已归一化日志路径列表。
    log_paths: list[Path],
    # wave_paths 是已归一化波形路径列表。
    wave_paths: list[Path],
    # validation 路径形态需要检查存在性，映射形态视为已加载报告。
    validation: str | Path | dict[str, Any] | None,
) -> list[Path]:
    """
    返回显式传入但当前文件系统不存在的 artifact 路径。

    :param spec: 规格路径、规格映射或空值。
    :param codegen_plan: codegen plan 路径、计划映射或空值。
    :param rtl_paths: 已归一化 RTL 路径列表。
    :param tb_paths: 已归一化 testbench 路径列表。
    :param log_paths: 已归一化日志路径列表。
    :param wave_paths: 已归一化波形路径列表。
    :param validation: validation 路径、报告映射或空值。
    :return: 显式提供但当前不可访问的路径列表。
    """

    # missing 列表用于 risk flag 和用户提示。
    list_missing: list[Path] = []  # 显式输入中无法访问的文件

    # 映射类输入只有路径形态才检查存在性。
    for obj_item in (spec, codegen_plan, validation):

        # str/Path 输入表示调用方指向文件。
        if isinstance(obj_item, (str, Path)) and not Path(obj_item).exists():

            # 路径输入不可读时保留原路径供用户修正。
            list_missing.append(Path(obj_item))

    # RTL/testbench/log/waveform 输入逐项检查。
    for path_item in [*rtl_paths, *tb_paths, *log_paths, *wave_paths]:

        # 不存在的路径加入缺失列表。
        if not path_item.exists():

            # 多文件输入中的不可读项同样进入缺失证据。
            list_missing.append(path_item)

    # 返回缺失路径列表。
    return list_missing

# _artifact_exists 检查 artifact_dir 中任一候选文件是否存在。
def _artifact_exists(artifact_dir: str | Path | None, names: tuple[str, ...]) -> bool:
    """
    判断 artifact_dir 下任一候选 artifact 是否存在。

    :param artifact_dir: 历史产物目录。
    :param names: 相对候选文件名。
    :return: 任一候选文件存在时为 `True`。
    """

    # 没有 artifact_dir 时不可能存在 artifact。
    if artifact_dir is None:

        # 缺少根目录时 artifact 检测必然失败。
        return False

    # 根路径只构造一次，供候选名称逐项拼接。
    path_root = Path(artifact_dir)  # 恢复产物候选文件所在目录

    # 候选文件任一存在即满足。
    return any((path_root / str_name).exists() for str_name in names)

# _safe_read_text 读取日志文本并吞掉 I/O 错误。
def _safe_read_text(path: Path) -> str:
    """
    安全读取日志文本，失败时返回空字符串。

    :param path: 待读取的日志路径。
    :return: 日志文本；读取失败时返回空字符串。
    """

    # 日志读取失败不能中断路由。
    try:

        # errors=ignore 避免日志编码问题阻断风险分类。
        return path.read_text(encoding="utf-8", errors="ignore")

    # 日志读取异常只影响风险提示，不应中断路由。
    except OSError:

        # 不可读日志按空文本处理。
        return ""

# _dedupe 保持顺序去重。
def _dedupe(items: list[str]) -> list[str]:
    """
    按首次出现顺序对字符串列表去重。

    :param items: 待去重的字符串列表。
    :return: 去除空字符串和重复项后的列表。
    """

    # seen 用于 O(1) 判断是否已经输出。
    set_seen: set[str] = set()  # 已输出字符串集合

    # deduped 保存按原顺序去重后的结果。
    list_deduped: list[str] = []  # 去重后的字符串列表

    # 按输入顺序扫描，保留首次出现。
    for str_item in items:

        # 空字符串和重复值都不进入结果。
        if str_item and str_item not in set_seen:

            # 记录该字符串已经输出。
            set_seen.add(str_item)

            # 保留首次出现的字符串。
            list_deduped.append(str_item)

    # 返回顺序稳定的去重结果。
    return list_deduped

# _provenance_policy 返回路由报告固定的引用材料策略。
def _provenance_policy() -> dict[str, str]:
    """
    返回 workflow 路由报告使用的固定 provenance 策略。

    :param: 无外部参数。
    :return: 固定引用材料策略映射。
    """

    # 策略字段防止参考仓库文本或模板泄漏进 durable 输出。
    return {
        "reference_material": "abstract_principles_only",
        "copy_policy": "no_reference_text_code_templates_or_schemas",
        "runtime_dependency": "none",
    }
