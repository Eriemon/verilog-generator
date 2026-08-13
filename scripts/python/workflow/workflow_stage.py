"""单个工作流阶段的提示、抽取和局部审计执行。"""

# future import 保持注解延迟求值，避免运行期循环依赖扩大。
from __future__ import annotations

# 标准库依赖只承载不可变上下文和路径类型。
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 模型响应抽取依赖负责把 Markdown 代码块落入 stage artifact 目录。
from .extractor import extract_response

# 接口审计依赖产出 Python/RTL 合同，供 workflow gates 复用。
from .interface_contract import audit_interface

# provider 调用上下文把路径、manifest 和向量合同一次性传入模型层。
from .model_provider import GenerationContext

# prompt 渲染依赖承载 Generator 模式的主要自然语言模板。
from .prompt import render_prompt

# 参考合同和向量合同用于 semantic gate 的前置证据。
from scripts.python.existing_rtl.semantic_contract import audit_semantic_model
from .requirements import build_codegen_plan, build_requirements_payload

# trace helper 负责输出 release-safe 路径和 JSONL 事件。
from .trace import append_trace_event, safe_path, spec_summary
from .vectors import audit_vectors

# workspace helper 统一写路径门禁，避免 stage 逃逸运行目录。
from .workspace import require_write_path, write_json, write_text

# workflow support 提供跨 stage 共享的确定性执行和状态记录逻辑。
from .workflow_support import (
    _generate_model_response,
    _prompt_stats,
    _record_state,
    _run_internal_json_stage,
    _stage_budget,
    _stage_manifest,
)

@dataclass(frozen=True)
class StageRunContext:
    """保存单个 workflow stage 所需的运行期依赖。

    参数:
        run_dir: workflow 根运行目录，shape 为单一路径，dtype 为 Path，单位为文件系统路径。
        attempt_dir: 当前 attempt 目录，shape 为单一路径，dtype 为 Path，单位为文件系统路径。
        attempt_id: attempt 稳定编号，shape 为标量字符串，dtype 为 str，单位为 workflow 追踪 ID。
        plan: 规范分解后的计划字典，shape 为映射，dtype 为 dict，单位为 workflow 计划字段。
        stage: 当前阶段名称，shape 为标量字符串，dtype 为 str，单位为 stage 名称。
        provider: 模型 provider 实例，shape 为对象引用，dtype 为 Any，单位为 provider 调用契约。
        config: workflow 配置，shape 为映射，dtype 为 dict，单位为配置字段。
        memory: prompt memory，可为空，shape 为映射或空值，dtype 为 dict 或 None，单位为 trace 摘要。
        decision: 人工决策输入，可为空，shape 为映射或空值，dtype 为 dict 或 None，单位为决策字段。
        previous_stage: 前一阶段输出，可为空，shape 为映射或空值，dtype 为 dict 或 None，单位为 stage 输出。
        stage_outputs: 已完成阶段输出表，shape 为 stage 到输出映射，dtype 为 dict，单位为 stage 输出。
        active_codegen_plan: 当前 codegen 计划，可为空，shape 为映射或空值，dtype 为 dict 或 None，单位为计划字段。
        trace_path: JSONL trace 路径，shape 为单一路径，dtype 为 Path，单位为文件系统路径。
        state_path: workflow state 路径，shape 为单一路径，dtype 为 Path，单位为文件系统路径。
        state_updates: 是否写 state 事件，shape 为布尔标量，dtype 为 bool，单位为开关。
    """

    # run_dir 定位 workflow 根目录，所有 attempt 输出都从这里派生。
    run_dir: Path  # workflow 根运行目录

    # attempt_dir 锁定本轮 stage 的父目录，避免跨 attempt 写文件。
    attempt_dir: Path  # 当前 attempt 目录

    # attempt_id 写入 trace 和 state，用于把事件串回同一轮尝试。
    attempt_id: str  # attempt 追踪编号

    # plan 保存已确认的设计计划，是 prompt 和内部 JSON stage 的事实来源。
    plan: dict[str, Any]  # workflow 计划字段

    # stage 决定 requirements/codegen/python/rtl/review 的执行分支。
    stage: str  # 当前阶段名称

    # provider 承担模型或命令生成，内部 JSON stage 不会调用它。
    provider: Any  # 模型 provider 实例

    # config 固化 readiness、stream、comment_language 等运行开关。
    config: dict[str, Any]  # workflow 配置字段

    # memory 来自上一轮 trace 摘要，只注入需要修复上下文的模型阶段。
    memory: dict[str, Any] | None  # 注入 render_prompt 的上一轮 trace 摘要

    # decision 承载人工恢复输入，prompt 渲染时会显式注入。
    decision: dict[str, Any] | None  # 人工决策输入

    # previous_stage 提供上一阶段 manifest、artifact 和合同路径。
    previous_stage: dict[str, Any] | None  # 上一阶段输出

    # stage_outputs 汇总已完成阶段，供 RTL 阶段回读 Python 向量合同。
    stage_outputs: dict[str, dict[str, Any]]  # 已完成阶段输出表

    # active_codegen_plan 保存当前可用的 codegen 计划，驱动后续 prompt。
    active_codegen_plan: dict[str, Any] | None  # 当前 codegen 计划

    # trace_path 是 JSONL 事件流路径，支撑诊断和 resume。
    trace_path: Path  # append_trace_event 写入的 JSONL 事件流路径

    # state_path 是轻量 workflow 状态路径，受 state_updates 控制。
    state_path: Path  # _record_state 写入的 workflow-state.json 路径

    # state_updates 控制是否写入 workflow-state.json。
    state_updates: bool  # state 写入开关

@dataclass(frozen=True)
class StagePaths:
    """保存 stage 目录和关键文件路径。"""

    # stage_dir 是当前 stage 的根目录。
    stage_dir: Path  # 约束 prompt、response 和 generated 写入的 stage 根目录

    # prompt_path 保存发送给 provider 的 prompt 文本。
    prompt_path: Path  # prompt 文件路径

    # response_path 保存 provider 响应或内部阶段说明。
    response_path: Path  # provider 响应或内部说明文件

    # artifact_dir 承载 extract_response 产物和内部 JSON artifact。
    artifact_dir: Path  # 审计和 extract_response 共享的 generated 子目录

# workflow stage 入口保留关键字兼容，供 execution 层调用。
def _run_generation_stage(**kwargs: Any) -> dict[str, Any]:
    """执行单个 workflow stage，并保留旧关键字调用契约。

    参数:
        kwargs: 旧版 `_run_generation_stage` 的关键字参数表；shape 为映射，dtype 为 dict，单位为函数参数。

    返回:
        包含 prompt、response、artifact 和 contract 摘要的 stage 输出。
    """

    # 旧调用点仍传入关键字参数；上下文对象把参数数量压回一个语义单元。
    context = StageRunContext(**kwargs)  # stage 运行上下文

    # stage 路径先集中创建，避免后续审计分支重复拼接目录。
    stage_paths_current = _prepare_stage_paths(context)  # 当前 stage 的 prompt/response/artifact 路径集合

    # requirements/codegen_plan 是本地确定性 JSON 阶段，不调用模型 provider。
    dict_internal_output: dict[str, Any] | None = _try_run_internal_stage(  # 内部 JSON stage 的返回负载
        context,  # 本轮 stage 的运行上下文
        stage_paths_current,  # 内部 JSON 写入的目标路径集合
    )

    # 内部阶段已完成时直接返回其 artifact 摘要。
    if dict_internal_output is not None:

        # 返回确定性阶段结果，保持旧版 stage 输出结构。
        return dict_internal_output

    # 模型阶段先渲染 prompt，再写 trace 供后续修复循环定位。
    dict_prompt_details = _render_and_trace_prompt(context, stage_paths_current)  # prompt 渲染结果和 trace 上下文

    # provider 响应和抽取文件是后续接口审计的输入。
    dict_response_details = _generate_and_extract(context, stage_paths_current, dict_prompt_details)  # provider 响应和抽取文件

    # 基础输出记录所有阶段共用的文件摘要。
    dict_stage_output = _base_stage_output(context, stage_paths_current, dict_prompt_details, dict_response_details)  # attempt 记录中的 stage 输出对象

    # Python 阶段产生参考模型、接口和向量合同。
    if context.stage == "python":

        # Python 审计结果会被 RTL 语义门和接口门复用。
        _augment_python_stage_output(context, stage_paths_current, dict_stage_output, dict_response_details)

    # RTL 阶段产生最终接口合同供 validate 和 stage gate 使用。
    elif context.stage == "rtl":

        # RTL 接口审计结果是最终 workflow 的核心合同。
        _augment_rtl_stage_output(context, stage_paths_current, dict_stage_output)

    # 返回完整 stage 输出供 execution 层挂入 attempt record。
    return dict_stage_output

# stage 路径 helper 集中执行写路径门禁，避免调用方分散创建目录。
def _prepare_stage_paths(context: StageRunContext) -> StagePaths:
    """
    创建 stage 目录和 artifact 目录。

    参数:
        context: 当前 stage 的运行上下文，提供 attempt 目录和 stage 名称。

    返回:
        包含 stage 根目录、prompt、response 和 artifact 目录的路径集合。
    """

    # stage 目录按 attempt/stage 固定布局，便于 release 证据复现。
    stage_dir_path = context.attempt_dir / context.stage  # 当前 attempt 下的 stage 根路径

    # stage_dir 经过写门禁后才允许 mkdir 和文件写入。
    path_stage_dir: Path = require_write_path(stage_dir_path, purpose="stage directory")  # 写门禁确认后的 stage 根目录

    # 目录必须存在后才能写 prompt、response 和 generated artifact。
    path_stage_dir.mkdir(parents=True, exist_ok=True)

    # prompt_path 和 response_path 使用稳定文件名，便于 resume 精确回读。
    prompt_path = path_stage_dir / f"{context.stage}_prompt.md"  # provider 或内部 stage 的请求文本路径

    # response_path 记录模型响应或内部 JSON 阶段说明。
    response_path = path_stage_dir / f"{context.stage}_response.md"  # provider 响应或内部说明路径

    # artifact_dir 承载 extract_response 或内部 JSON 产物。
    artifact_dir_path = path_stage_dir / "generated"  # stage 生成物固定子目录

    # artifact_dir 经过写门禁后供模型抽取和审计读取。
    path_artifact_dir: Path = require_write_path(artifact_dir_path, purpose="artifact directory")  # 审计产物写门禁后的目录

    # artifact 目录需要预先存在，内部阶段和模型抽取都会写入。
    path_artifact_dir.mkdir(parents=True, exist_ok=True)

    # 返回不可变路径集合，降低后续 helper 的参数数量。
    return StagePaths(path_stage_dir, prompt_path, response_path, path_artifact_dir)

# 内部 JSON stage helper 只处理确定性本地阶段，不进入 provider。
def _try_run_internal_stage(context: StageRunContext, paths: StagePaths) -> dict[str, Any] | None:
    """
    处理 requirements 和 codegen_plan 的确定性 JSON stage。

    参数:
        context: 当前 stage 的运行上下文。
        paths: 当前 stage 的写入路径集合。

    返回:
        内部 JSON stage 已处理时返回 stage 输出；非内部 stage 返回 None。
    """

    # manifest 描述当前 stage 需要产生的文件集合。
    manifest = _stage_manifest(context.plan, context.stage)  # 当前内部 stage 写出的文件清单

    # requirements 阶段直接从 plan 派生确认 payload。
    if context.stage == "requirements":

        # requirements payload 不依赖模型，避免引入非确定性。
        payload = build_requirements_payload(context.plan)  # requirements 确认数据

        # 内部 JSON 阶段写 prompt/response 和 artifact 后直接返回。
        return _run_internal_json_stage(
            attempt_id=context.attempt_id,
            plan=context.plan,
            stage=context.stage,
            manifest=manifest,

            # requirements 的写入位置来自当前 stage 路径集合。
            stage_dir=paths.stage_dir,
            artifact_dir=paths.artifact_dir,
            trace_path=context.trace_path,
            state_path=context.state_path,
            state_updates=context.state_updates,

            # payload 是 requirements 的确定性确认数据。
            payload=payload,
        )

    # codegen_plan 阶段优先复用外部计划，否则从 plan 派生。
    if context.stage == "codegen_plan":

        # codegen_plan 允许外部配置覆盖，本地 builder 只作为兜底来源。
        payload = context.config.get("external_codegen_plan") or build_codegen_plan(context.plan)  # codegen 计划数据

        # codegen_plan 输出仍使用 codegen_plan.json 文件名。
        return _run_internal_json_stage(
            attempt_id=context.attempt_id,
            plan=context.plan,
            stage=context.stage,
            manifest=manifest,

            # codegen_plan 的 prompt/response/artifact 仍落在当前 stage 目录。
            stage_dir=paths.stage_dir,
            artifact_dir=paths.artifact_dir,
            trace_path=context.trace_path,
            state_path=context.state_path,
            state_updates=context.state_updates,

            # payload_key 保留旧版 artifact JSON schema。
            payload=payload,
            payload_key="codegen_plan",
        )

    # 非内部 stage 继续进入模型生成路径。
    return None

# prompt helper 负责把上游上下文、向量合同和运行预算写入 trace。
def _render_and_trace_prompt(context: StageRunContext, paths: StagePaths) -> dict[str, Any]:
    """
    渲染模型 prompt 并写入 trace 事件。

    参数:
        context: 当前模型 stage 的运行上下文。
        paths: prompt 和 trace 相关 artifact 的路径集合。

    返回:
        prompt 文本、manifest、上下文目录、向量合同和统计摘要。
    """

    # 上一阶段 manifest 为当前 prompt 提供结构化上下文。
    context_manifest = context.previous_stage.get("manifest") if context.previous_stage else None  # prompt 可见的上游清单

    # 上一阶段 artifact 目录用于 prompt 引用已生成产物。
    context_dir = context.previous_stage.get("artifact_dir") if context.previous_stage else None  # 上下文目录

    # Python 阶段的向量合同会传递给 RTL 阶段。
    dict_vector_contract: dict[str, Any] | None = _resolve_vector_contract(context)  # 传给 RTL prompt 的 Python 向量合同

    # prompt_text 是模型 provider 的唯一自然语言输入。
    str_prompt_text = _render_prompt_text(context, context_manifest, context_dir, dict_vector_contract)  # provider 输入 prompt 文本

    # prompt 落盘是后续调试、resume 和 release 证据的固定输入。
    write_text(paths.prompt_path, str_prompt_text)

    # prompt_stats 记录上下文规模，帮助定位超预算 prompt。
    dict_prompt_stats = _build_prompt_stats(  # prompt 统计摘要
        context,  # 当前 stage 上下文
        str_prompt_text,  # 已渲染 prompt 文本
        context_manifest,  # prompt 可见的上游 manifest
        context_dir,  # prompt 可见的上游 artifact 目录
        dict_vector_contract,  # prompt 可见的向量合同
    )

    # workflow state 只在启用时写入，不改变无状态测试行为。
    _record_state(
        context.state_path,
        "prompt",
        {"output": paths.prompt_path, "stage": context.stage, "budget": _stage_budget(context.config, context.stage)},
        enabled=context.state_updates,
    )

    # trace 事件把 prompt、上下文和 provider 绑定到同一个 attempt。
    dict_prompt_trace_payload = _prompt_trace_payload(  # prompt 事件负载
        context,  # trace 事件所属的运行上下文
        paths,  # prompt 文件路径集合
        context_dir,  # 上游 artifact 目录
        dict_vector_contract,  # 注入 prompt 的向量合同
        dict_prompt_stats,  # prompt 规模统计
    )

    # trace 事件单独写入，避免长调用行隐藏 payload 结构。
    append_trace_event(context.trace_path, dict_prompt_trace_payload)

    # 返回 prompt 相关细节，供 GenerationContext 和输出摘要复用。
    return {
        "prompt_text": str_prompt_text,
        "manifest": _stage_manifest(context.plan, context.stage),
        "context_manifest": context_manifest,
        "context_dir": context_dir,
        "vector_contract": dict_vector_contract,
        "prompt_stats": dict_prompt_stats,
    }

# prompt 文本 helper 包住 render_prompt 的长参数表，主流程只保留 stage 编排。
def _render_prompt_text(
    context: StageRunContext,
    context_manifest: Any,
    context_dir: Any,
    vector_contract: dict[str, Any] | None,
) -> str:
    """
    渲染 provider 将消费的完整 prompt 文本。

    参数:
        context: 当前 stage 的运行上下文。
        context_manifest: 上一阶段 manifest，可为空。
        context_dir: 上一阶段 artifact 目录，可为空。
        vector_contract: Python stage 产生并传给 RTL 的向量合同。

    返回:
        完整 prompt 文本。
    """

    # render_prompt 集中接收 stage、预算、决策和向量合同，保持 Generator 模式入口单一。
    return render_prompt(
        context.plan,
        target=context.plan["target"],
        stage=context.stage,
        context_manifest=context_manifest,
        context_dir=context_dir,
        memory=context.memory,
        comment_language=str(context.config.get("comment_language", "zh")),
        vector_contract=vector_contract,
        codegen_plan=context.active_codegen_plan,
        budget=_stage_budget(context.config, context.stage),
        decision=context.decision,
    )

# prompt 统计 helper 只计算 trace 摘要，不写 prompt 文件。
def _build_prompt_stats(
    context: StageRunContext,
    prompt_text: str,
    context_manifest: Any,
    context_dir: Any,
    vector_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    生成 prompt 规模和上下文注入统计。

    参数:
        context: 当前 stage 的运行上下文。
        prompt_text: 已渲染的 prompt 文本。
        context_manifest: 上一阶段 manifest，可为空。
        context_dir: 上一阶段 artifact 目录，可为空。
        vector_contract: 当前 prompt 可见的向量合同。

    返回:
        prompt 长度、预算和上下文注入状态摘要。
    """

    # subfunction 维持 None，确保 workflow trace schema 不因拆分 helper 改变。
    return _prompt_stats(
        prompt_text,

        # stage 和预算字段用于定位 prompt 是否超过当前阶段限制。
        stage=context.stage,
        budget=_stage_budget(context.config, context.stage),

        # workflow stage 当前不再拆 subfunction，保留旧 trace 语义。
        subfunction=None,

        # 上下文输入摘要帮助诊断 prompt memory 与向量合同注入。
        context_manifest=context_manifest,
        context_dir=context_dir,
        vector_contract=vector_contract,
        decision=context.decision,
    )

# 向量合同 helper 统一上一阶段继承和 Python stage 兜底路径。
def _resolve_vector_contract(context: StageRunContext) -> dict[str, Any] | None:
    """
    取得当前 stage 可见的向量合同。

    参数:
        context: 当前 stage 的运行上下文。

    返回:
        当前 stage 可注入 prompt 的向量合同；无可用合同时返回 None。
    """

    # 默认继承上一阶段显式输出的向量合同。
    dict_vector_contract: dict[str, Any] | None = (  # 上一阶段显式传出的向量合同
        context.previous_stage.get("vector_contract") if context.previous_stage else None  # 上游 stage 合同或空值
    )

    # RTL 阶段缺少直接前驱合同时，从 python stage 输出兜底读取。
    if context.stage == "rtl" and dict_vector_contract is None:

        # python stage 的向量合同用于 RTL testbench 语义比对。
        dict_vector_contract = context.stage_outputs.get("python", {}).get("vector_contract")  # Python stage 兜底向量合同

    # 返回可为空的向量合同。
    return dict_vector_contract

# prompt trace helper 只组装 JSON payload，不直接写文件。
def _prompt_trace_payload(
    context: StageRunContext,
    paths: StagePaths,
    context_dir: Any,
    vector_contract: dict[str, Any] | None,
    prompt_stats: dict[str, Any],
) -> dict[str, Any]:
    """
    组装 prompt trace 事件 payload。

    参数:
        context: 当前 stage 的运行上下文。
        paths: prompt 和响应路径集合。
        context_dir: prompt 引用的上游 artifact 目录。
        vector_contract: 当前 prompt 使用的向量合同。
        prompt_stats: prompt 规模和预算统计。

    返回:
        可写入 JSONL trace 的 prompt 事件字典。
    """

    # previous_stage 为空时所有上下文引用都保持 None。
    dict_previous_stage: dict[str, Any] = context.previous_stage or {}  # 上一阶段输出或空映射

    # vector_contract_path 只在上一阶段写出合同时存在。
    vector_contract_path = dict_previous_stage.get("vector_contract_path")  # 向量合同路径

    # 返回 trace 事件字段，保持旧版 JSONL schema。
    return {
        "event": "prompt",
        "attempt_id": context.attempt_id,
        "target": context.plan["target"],
        "stage": context.stage,
        "spec": spec_summary(context.plan),
        "output": paths.prompt_path,
        "context_manifest": dict_previous_stage.get("manifest_path"),
        "context_dir": context_dir,
        "memory": dict_previous_stage.get("memory_path"),
        "comment_language": context.config.get("comment_language"),
        "vector_contract": safe_path(vector_contract_path) if vector_contract_path else None,
        "decision": context.decision is not None,
        "subfunction": None,
        "budget": _stage_budget(context.config, context.stage),
        "prompt_stats": prompt_stats,
        "provider": context.provider.name,
    }

# 生成 helper 负责 provider 调用、响应落盘和 artifact 抽取。
def _generate_and_extract(
    context: StageRunContext,
    paths: StagePaths,
    prompt_details: dict[str, Any],
) -> dict[str, Any]:
    """
    调用 provider、记录响应并抽取 artifact 文件。

    参数:
        context: 当前模型 stage 的运行上下文。
        paths: response 和 artifact 写入路径集合。
        prompt_details: prompt 文本、manifest 和上下文摘要。

    返回:
        provider 响应文本、streaming 摘要和抽取文件列表。
    """

    # GenerationContext 是 provider 需要的结构化调用上下文。
    generation_context_provider_call_context = _build_generation_context(context, paths, prompt_details)  # provider 调用上下文

    # provider 返回文本响应和 streaming 统计。
    tuple_provider_result = _call_provider_for_stage(  # provider 返回的响应文本和流统计元组
        context,  # 当前 stage 的运行上下文
        paths,  # provider 响应和 artifact 的落盘路径
        prompt_details,  # prompt 文本和输入摘要
        generation_context_provider_call_context,  # provider 可见的 run/attempt/manifest 上下文
    )

    # str_response_text 是落盘和 extract_response 的唯一响应原文。
    str_response_text = tuple_provider_result[0]  # 模型响应原文

    # dict_stream_summary 进入 trace 与 stage summary，保留 streaming 证据。
    dict_stream_summary = tuple_provider_result[1]  # streaming 模式和 chunk 统计

    # 响应原文落盘，extract_response 只读该文本派生 artifact。
    write_text(paths.response_path, str_response_text)

    # 记录 provider 响应路径，方便 resume 或人工查看。
    _record_state(
        context.state_path,
        "model_generate",
        {"output": paths.response_path, "provider": context.provider.name, "stage": context.stage},
        enabled=context.state_updates,
    )

    # streaming 事件保留 chunk 计数和模式。
    append_trace_event(
        context.trace_path,
        {
            "event": "model_stream",
            "attempt_id": context.attempt_id,
            "stage": context.stage,
            "provider": context.provider.name,
            **dict_stream_summary,
        },
    )

    # model_generate 事件绑定 prompt 和 response 路径。
    append_trace_event(
        context.trace_path,
        {
            "event": "model_generate",
            "attempt_id": context.attempt_id,
            "stage": context.stage,
            "provider": context.provider.name,
            "prompt_path": paths.prompt_path,
            "response_path": paths.response_path,
        },
    )

    # 抽取模型响应中的代码块到 generated 目录。
    written_files = extract_response(str_response_text, paths.artifact_dir)  # 抽取出的 artifact 路径列表

    # state 记录抽取产物集合。
    _record_state(
        context.state_path,
        "extract",
        {"response": paths.response_path, "out_dir": paths.artifact_dir, "written_files": written_files},
        enabled=context.state_updates,
    )

    # trace 记录抽取产物的相对路径摘要。
    append_trace_event(
        context.trace_path,
        {
            "event": "extract",
            "attempt_id": context.attempt_id,
            "response": paths.response_path,
            "out_dir": paths.artifact_dir,
            "written_files": [safe_path(path) for path in written_files],
        },
    )

    # 返回响应和抽取结果供审计阶段使用。
    return {
        "response_text": str_response_text,
        "stream_summary": dict_stream_summary,
        "written_files": written_files,
    }

# provider 上下文 helper 隔离 GenerationContext 的构造细节。
def _build_generation_context(
    context: StageRunContext,
    paths: StagePaths,
    prompt_details: dict[str, Any],
) -> GenerationContext:
    """
    构造 provider 调用所需的结构化上下文。

    参数:
        context: 当前 stage 的运行上下文。
        paths: provider 可见的 prompt、response 和 run 路径集合。
        prompt_details: prompt 渲染阶段产出的 manifest 和向量合同。

    返回:
        provider 调用所需的 GenerationContext 对象。
    """

    # GenerationContext 固定 provider 可见路径和 workflow 配置，避免 provider 自行推断目录。
    return GenerationContext(
        attempt_id=context.attempt_id,
        stage=context.stage,
        prompt_path=paths.prompt_path,
        response_path=paths.response_path,
        run_dir=context.run_dir,
        attempt_dir=context.attempt_dir,
        spec=context.plan,
        manifest=prompt_details["manifest"],
        workflow_config=context.config,
        vector_contract=prompt_details["vector_contract"],
        comment_language=str(context.config.get("comment_language", "zh")),
    )

# provider 调用 helper 只返回响应文本和流式摘要，不写文件系统。
def _call_provider_for_stage(
    context: StageRunContext,
    paths: StagePaths,
    prompt_details: dict[str, Any],
    generation_context: GenerationContext,
) -> tuple[str, dict[str, Any]]:
    """
    调用 provider 并返回响应文本与 streaming 摘要。

    参数:
        context: 当前 stage 的运行上下文。
        paths: 当前 stage 的路径集合。
        prompt_details: prompt 文本和输入摘要。
        generation_context: provider 需要的结构化调用上下文。

    返回:
        响应文本和 streaming 摘要组成的二元组。
    """

    # provider 只能写入当前 stage_dir，stream 摘要继续由 workflow 输出保留。
    return _generate_model_response(
        provider=context.provider,
        prompt_text=prompt_details["prompt_text"],
        context=generation_context,
        stage_dir=paths.stage_dir,
        config=context.config,
    )

# 输出 helper 构造所有 stage 都返回的最小 schema。
def _base_stage_output(
    context: StageRunContext,
    paths: StagePaths,
    prompt_details: dict[str, Any],
    response_details: dict[str, Any],
) -> dict[str, Any]:
    """
    构造所有 stage 共用的输出记录。

    参数:
        context: 当前 stage 的运行上下文。
        paths: 当前 stage 的路径集合。
        prompt_details: prompt 渲染阶段产出的摘要。
        response_details: provider 响应和抽取结果摘要。

    返回:
        包含 prompt、response、artifact、manifest 和 summary 的基础 stage 输出。
    """

    # stream_summary 需要并入 summary，保持 batch/report 旧字段。
    dict_stream_summary: dict[str, Any] = response_details["stream_summary"]  # provider 流式响应摘要

    # 返回基础输出，审计 helper 会按 stage 增补 contract_paths。
    return {
        "stage": context.stage,
        "prompt_path": paths.prompt_path,
        "response_path": paths.response_path,
        "artifact_dir": paths.artifact_dir,
        "manifest": prompt_details["manifest"],
        "manifest_path": paths.response_path,
        "contract_paths": {},
        "summary": {
            "prompt_path": safe_path(paths.prompt_path),
            "response_path": safe_path(paths.response_path),
            "artifact_dir": safe_path(paths.artifact_dir),
            **dict_stream_summary,
        },
    }

# Python stage helper 写出参考、接口和向量合同证据。
def _augment_python_stage_output(
    context: StageRunContext,
    paths: StagePaths,
    stage_output: dict[str, Any],
    response_details: dict[str, Any],
) -> None:
    """
    为 Python stage 输出补齐参考、接口和向量合同。

    参数:
        context: 当前 Python stage 的运行上下文。
        paths: Python stage 的路径集合。
        stage_output: 将被原地补充合同字段的 stage 输出。
        response_details: provider 响应抽取出的文件摘要。

    返回:
        只更新 stage_output 和写出审计文件，不返回业务值。
    """

    # 参考合同校验 Python 模型是否产出可执行用例。
    dict_semantic_contract: dict[str, Any] = audit_semantic_model(paths.artifact_dir)  # Python 参考模型合同

    # semantic_contract_path 是语义门读取的固定证据文件。
    semantic_contract_path = paths.stage_dir / "semantic_contract.json"  # 参考合同路径

    # 将参考合同写入 stage 目录，便于 release 证据收集。
    write_json(semantic_contract_path, dict_semantic_contract)

    # Python 接口合同用于和 RTL 接口合同做结构比对。
    dict_python_contract: dict[str, Any] = audit_interface("python", paths.artifact_dir)  # Python 接口审计合同

    # python_contract_path 是 interface gate 的输入证据。
    python_contract_path = paths.stage_dir / "python_interface.json"  # Python 接口合同路径

    # 写出 Python 接口合同。
    write_json(python_contract_path, dict_python_contract)

    # 向量文件按旧规则选择第一个 *_vectors.json。
    vector_path = _find_vector_path(response_details)  # Python stage 抽取出的向量 JSON 路径

    # 缺少向量文件时 vector_contract 保持 None。
    dict_vector_contract: dict[str, Any] | None = _audit_optional_vector_contract(vector_path)  # semantic gate 消费的向量合同对象

    # vector_contract_path 仅在存在合同内容时写出。
    vector_contract_path = _vector_contract_path(paths, dict_vector_contract)  # 写给 RTL prompt 和 trace 的向量合同路径

    # 存在向量合同时写入 JSON 文件。
    if vector_contract_path is not None:

        # 向量合同供 RTL prompt 和 semantic gate 复用。
        write_json(vector_contract_path, dict_vector_contract)

    # 将 Python 合同补入 stage 输出对象。
    _attach_python_contracts(
        stage_output,
        dict_semantic_contract,

        # 以下路径字段保持 contract_paths 与实际 JSON 证据同步。
        semantic_contract_path,
        dict_python_contract,
        python_contract_path,

        # 向量合同允许为空，helper 会保留稳定 schema。
        dict_vector_contract,
        vector_contract_path,
    )

    # 记录审计 state 和 trace。
    _trace_python_audits(
        context,
        paths,
        dict_semantic_contract,
        semantic_contract_path,
        dict_python_contract,
        python_contract_path,
    )

# 向量文件选择 helper 保持旧版“第一个文件胜出”的顺序语义。
def _find_vector_path(response_details: dict[str, Any]) -> Path | None:
    """
    从抽取文件列表中选择向量 JSON 文件。

    参数:
        response_details: provider 响应抽取出的文件列表和响应摘要。

    返回:
        第一个 `*_vectors.json` 文件路径；不存在时返回 None。
    """

    # 旧 workflow 只消费第一个 *_vectors.json 文件，保持顺序兼容。
    return next((path for path in response_details["written_files"] if path.name.endswith("_vectors.json")), None)

# 向量审计 helper 隔离可空输入，避免主流程重复 None 分支。
def _audit_optional_vector_contract(vector_path: Path | None) -> dict[str, Any] | None:
    """
    在存在向量文件时读取向量合同。

    参数:
        vector_path: Python stage 抽取出的向量 JSON 文件路径。

    返回:
        向量审计合同；没有向量文件时返回 None。
    """

    # 没有向量文件时返回 None，让后续 stage 保留可空合同语义。
    return audit_vectors(vector_path) if vector_path is not None else None

# 向量合同路径 helper 保证只有有效合同才产生证据路径。
def _vector_contract_path(paths: StagePaths, vector_contract: dict[str, Any] | None) -> Path | None:
    """
    计算可选向量合同证据路径。

    参数:
        paths: 当前 Python stage 的路径集合。
        vector_contract: 已审计的向量合同，可为空。

    返回:
        应写入的 vector_contract.json 路径；无合同对象时返回 None。
    """

    # 只有存在合同对象时才需要写 JSON 文件。
    return paths.stage_dir / "vector_contract.json" if vector_contract is not None else None

# Python 合同挂载 helper 只修改传入的 stage_output，不写文件系统。
def _attach_python_contracts(
    # stage_output 是唯一被原地更新的对象。
    stage_output: dict[str, Any],

    # 参考合同字段供 semantic gate 使用。
    semantic_contract: dict[str, Any],
    semantic_contract_path: Path,

    # Python 接口合同字段供 interface gate 使用。
    python_contract: dict[str, Any],
    python_contract_path: Path,

    # 向量合同字段允许为空，保持无向量场景兼容。
    vector_contract: dict[str, Any] | None,
    vector_contract_path: Path | None,
) -> None:
    """
    把 Python 审计合同挂入 stage 输出。

    参数:
        stage_output: 将被原地更新的 Python stage 输出。
        semantic_contract: Python 参考模型审计合同。
        semantic_contract_path: 参考模型合同 JSON 路径。
        python_contract: Python 接口审计合同。
        python_contract_path: Python 接口合同 JSON 路径。
        vector_contract: Python 向量审计合同，可为空。
        vector_contract_path: 向量合同 JSON 路径，可为空。

    返回:
        只原地更新 stage_output，不返回业务值。
    """

    # Python 合同对象保留给 execution 层直接读取。
    stage_output["semantic_contract"] = semantic_contract  # semantic gate 读取的 Python 参考合同对象

    # Python 接口合同保留给 interface gate 读取。
    stage_output["python_contract"] = python_contract  # interface gate 读取的 Python 接口合同对象

    # 向量合同可能为空，但字段保留以维持 schema 稳定。
    stage_output["vector_contract"] = vector_contract  # RTL prompt 和 semantic gate 共享的向量合同对象

    # 向量合同路径可能为空，调用方据此判断是否写出。
    stage_output["vector_contract_path"] = vector_contract_path  # trace 和 prompt 引用的向量合同文件路径

    # contract_paths 使用相对安全路径，避免报告泄漏绝对路径。
    stage_output["contract_paths"].update(
        {
            "semantic_contract": safe_path(semantic_contract_path),
            "python_interface": safe_path(python_contract_path),
        }
    )

    # 存在向量合同时才追加 vector_contract 路径。
    if vector_contract_path is not None:

        # vector_contract 路径供后续 prompt trace 使用。
        stage_output["contract_paths"]["vector_contract"] = safe_path(vector_contract_path)  # 脱敏后的向量合同引用

# Python 审计 trace helper 负责把合同路径写入 state 和 JSONL。
def _trace_python_audits(
    context: StageRunContext,
    paths: StagePaths,
    semantic_contract: dict[str, Any],
    semantic_contract_path: Path,
    python_contract: dict[str, Any],
    python_contract_path: Path,
) -> None:
    """
    记录 Python stage 的审计 state 和 trace。

    参数:
        context: 当前 Python stage 的运行上下文。
        paths: Python stage 的路径集合。
        semantic_contract: Python 参考模型审计合同。
        semantic_contract_path: 参考模型合同 JSON 路径。
        python_contract: Python 接口审计合同。
        python_contract_path: Python 接口合同 JSON 路径。

    返回:
        只写 workflow state 和 JSONL trace，不返回业务值。
    """

    # reference audit state 记录 case 数量和输出路径。
    _record_state(
        context.state_path,
        "audit_semantic_model",
        {
            "path": paths.artifact_dir,
            "output": semantic_contract_path,
            "case_count": semantic_contract.get("case_count"),
        },
        enabled=context.state_updates,
    )

    # interface audit state 记录 Python 接口合同路径。
    _record_state(
        context.state_path,
        "audit_interface",
        {"target": "python", "path": paths.artifact_dir, "output": python_contract_path},
        enabled=context.state_updates,
    )

    # reference trace 记录 case id 和 checksum。
    append_trace_event(
        context.trace_path,
        {
            "event": "audit_semantic_model",
            "attempt_id": context.attempt_id,
            "path": paths.artifact_dir,
            "output": semantic_contract_path,
            "case_count": semantic_contract.get("case_count"),
            "case_ids": semantic_contract.get("case_ids", []),
            "sha256": semantic_contract.get("sha256"),
        },
    )

    # interface trace 记录 Python 侧接口摘要。
    append_trace_event(
        context.trace_path,
        {
            "event": "audit_interface",
            "attempt_id": context.attempt_id,
            "target": "python",
            "path": paths.artifact_dir,
            "output": python_contract_path,
            "interface_sha256": python_contract.get("interface_sha256"),
            "top": python_contract.get("top"),
            "case_ids": python_contract.get("case_ids", []),
            "vector_hashes": python_contract.get("vector_hashes", []),
        },
    )

# RTL stage helper 写出最终接口合同，并把 release-safe 路径挂入输出。
def _augment_rtl_stage_output(context: StageRunContext, paths: StagePaths, stage_output: dict[str, Any]) -> None:
    """
    为 RTL stage 输出补齐最终接口合同。

    参数:
        context: 当前 RTL stage 的运行上下文。
        paths: RTL stage 的路径集合。
        stage_output: 将被原地补充 RTL 合同字段的 stage 输出。

    返回:
        只更新 stage_output 并写出接口审计证据，不返回业务值。
    """

    # RTL 接口合同是 final_output 的核心验证输入。
    dict_interface_contract: dict[str, Any] = audit_interface(context.stage, paths.artifact_dir)  # RTL 顶层接口审计合同

    # 接口合同路径按 stage 名称固定。
    interface_contract_path = paths.stage_dir / f"{context.stage}_interface.json"  # RTL stage 接口审计 JSON

    # 将 RTL 审计正文落盘，供 validate 和 review 阶段复用。
    write_json(interface_contract_path, dict_interface_contract)

    # stage 输出直接携带接口合同对象。
    stage_output["interface_contract"] = dict_interface_contract  # final_output 汇总时读取的 RTL 接口对象

    # contract_paths 的键名必须与当前 RTL stage 名称绑定。
    interface_key = f"{context.stage}_interface"  # contract_paths 内的 RTL 接口索引键

    # 使用接口合同索引键更新 contract_paths，避免调用方重新拼接 stage 名称。
    stage_output["contract_paths"][interface_key] = safe_path(interface_contract_path)  # 脱敏后的 RTL 接口合同引用

    # state 记录 interface audit 输出。
    _record_state(
        context.state_path,
        "audit_interface",
        {"target": context.stage, "path": paths.artifact_dir, "output": interface_contract_path},
        enabled=context.state_updates,
    )

    # trace 记录 RTL 接口摘要。
    append_trace_event(
        context.trace_path,
        {
            "event": "audit_interface",
            "attempt_id": context.attempt_id,
            "target": context.stage,
            "path": paths.artifact_dir,
            "output": interface_contract_path,
            "interface_sha256": dict_interface_contract.get("interface_sha256"),
            "top": dict_interface_contract.get("top"),
            "case_ids": dict_interface_contract.get("case_ids", []),
            "vector_hashes": dict_interface_contract.get("vector_hashes", []),
        },
    )
