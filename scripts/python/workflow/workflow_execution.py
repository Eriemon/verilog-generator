"""工作流尝试循环、修复循环和终态归档。"""

# future import 保持 dataclass 注解延迟求值，降低运行期循环导入风险。
from __future__ import annotations

# 标准库依赖只用于不可变上下文和路径对象。
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 抽取、模型和规格异常决定 attempt 的失败分类。
from .extractor import ExtractionError
from .model_provider import ManualResponseRequired, ModelProvider, ModelProviderError, build_model_provider

# prompt memory 和 reflection helper 支撑失败后的修复循环。
from scripts.python.existing_rtl.optimizer import build_prompt_memory
from scripts.python.existing_rtl.reflection import (
    build_diagnosis,
    build_intervention,
    build_repair_plan,
    generate_repair_prompt,
)
from .spec import SpecError

# trace helper 负责 JSONL 事件和 release-safe 路径。
from .trace import append_trace_event, read_trace, safe_path
from scripts.python.validation.validation import validate_generated
from .workspace import require_write_path, write_json, write_text

# workflow gate helper 只负责合同检查和 gate 组合。
from .workflow_gates import _combine_gate_results, _interface_gate, _review_gate, _semantic_gate

# stage helper 执行 prompt、provider 响应抽取和局部审计。
from .workflow_stage import _run_generation_stage

# workflow support helper 提供状态常量、attempt 记录和 result 写盘。
from .workflow_support import (
    FINAL_STAGE,
    _default_stages_for,
    _new_attempt_record,
    _previous_stage,
    _record_state,
    _write_result,
)

# 文本形式固定 validation metrics 必须包含的公开 gate 顺序。
DELIVERABLE_MATRIX_CHECK_NAMES_TEXT = "compile ast readability comment naming profile testbench toolchain"  # validation metrics 必须包含的交付矩阵键顺序

# tuple 形式供 pass 判定循环复用。
DELIVERABLE_MATRIX_CHECK_NAMES = tuple(DELIVERABLE_MATRIX_CHECK_NAMES_TEXT.split())  # workflow pass 判定复用的矩阵键元组

@dataclass(frozen=True)
class WorkflowExecutionContext:
    """保存 workflow attempt 循环的稳定输入。"""

    # run_dir 是 workflow 所有 attempt 输出的根目录。
    run_dir: Path  # attempt 目录父路径

    # plan 是已分解的设计规格，stage 和 validation 都以它为事实来源。
    plan: dict[str, Any]  # workflow 设计计划

    # config 保存 provider、stages、readiness 和 retry 策略。
    config: dict[str, Any]  # workflow 运行配置

    # result 是会被持续更新并写回 result_path 的结果对象。
    result: dict[str, Any]  # workflow 结果对象

    # result_path 是 workflow 结果 JSON 的持久化位置。
    result_path: Path  # _write_result 持久化 workflow 汇总状态的 JSON 路径

    # trace_path 保存 prompt、validate、reflect 等事件。
    trace_path: Path  # append_trace_event 追加 prompt/validate/reflect 事件的 JSONL 路径

    # state_path 保存可选的轻量 state 事件。
    state_path: Path  # _record_state 写入可选进度事件的 JSON 路径

    # decision 承载 resume blocked_human 后的人类回答。
    decision: dict[str, Any] | None  # 人工决策输入

    # state_updates 控制是否写 workflow-state.json。
    state_updates: bool  # state 写入开关

@dataclass(frozen=True)
class AttemptContext:
    """保存单次 workflow attempt 的目录、编号和记录对象。"""

    # attempt_id 是 trace、state 和 result 共享的稳定编号。
    attempt_id: str  # trace、state 和 result 共享的 attempt 编号

    # attempt_dir 是该 attempt 的所有 stage 输出目录。
    attempt_dir: Path  # attempt 输出目录

    # record 是 result["attempts"] 中对应的可变记录。
    record: dict[str, Any]  # attempt 结果记录

# stage 循环状态对象隔离可变 stage 输出表，避免 result 摘要和完整输出混在一起。
@dataclass
class StageLoopState:
    """保存单个 attempt 内 stage 循环产生的中间状态。"""

    # stage_outputs 按 stage 名保存完整输出，供后续 stage/gate 回读。
    stage_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)  # validation 和 gate 回读的完整 stage 输出表

    # active_codegen_plan 是 codegen_plan stage 产出的当前计划。
    active_codegen_plan: dict[str, Any] | None = None  # 当前 codegen 计划

@dataclass(frozen=True)
class ValidationArtifacts:
    """保存 validation 报告和落盘路径。"""

    # report 是 validate_generated 返回的完整报告对象。
    report: Any  # validation 报告对象

    # report_path 是 validation JSON 证据路径。
    report_path: Path  # validate_generated 报告写盘后的 JSON 证据路径

@dataclass(frozen=True)
class GateArtifacts:
    """保存 stage gate 的合同路径和组合结果。"""

    # contract_paths 汇总 final stage 与 stage gates 的证据路径。
    contract_paths: dict[str, Any]  # release-safe 合同路径表

    # combined_gate 是 interface/semantic/review gate 的合并结果。
    combined_gate: dict[str, Any] | None  # 合并 stage gate 结果

    # effective_gate 供 repair prompt 使用，spec_issue 时会被置空。
    effective_gate: dict[str, Any] | None  # repair 使用的 gate 结果

@dataclass(frozen=True)
class RepairArtifacts:
    """保存 repair prompt、repair plan 和 diagnosis 证据。"""

    # report_text 是 validation report 的人类可读摘要。
    report_text: str  # validation 文本报告

    # repair_plan 是下一轮修复动作决策。
    repair_plan: dict[str, Any]  # _handle_repair_decision 读取的修复动作计划

    # diagnosis 是 checkpoint/localization 诊断结果。
    diagnosis: dict[str, Any]  # reflect trace 记录的 checkpoint 定位诊断

    # repair_prompt_path 是写给下一轮模型的修复 prompt。
    repair_prompt_path: Path  # 下一轮模型修复 prompt 的 Markdown 证据路径

# workflow public-internal 入口将旧关键字参数转成上下文对象。
def _execute_workflow(**kwargs: Any) -> dict[str, Any]:
    """
    执行 workflow，并保留旧版关键字调用契约。
    
    :param kwargs: 旧版关键字调用参数，字段会被收束为 WorkflowExecutionContext。
    :return: 写盘后的 workflow result 对象。
    """

    # context 把旧版九个关键字参数收束成单一语义对象。
    context = WorkflowExecutionContext(**kwargs)  # workflow 执行上下文

    # provider 在整个 workflow run 内复用，保持旧版 provider 生命周期。
    model_provider_a = _build_provider(context)  # 所有 stage 共享的模型 provider 实例

    # stages 固化本次 run 的阶段顺序，后续 attempt 不再重新推断。
    list_stages = _execution_stages(context)  # 本次 run 固定使用的 stage 顺序

    # int_max_attempts 决定 attempt 循环的硬上限。
    int_max_attempts = int(context.config.get("max_attempts", 3))  # attempt 循环允许启动的最大次数

    # func_attempt 缩短单行调用，避免 attempt 循环赋值被迫折行。
    func_attempt = _run_attempt  # 单轮 attempt 执行 helper

    # attempt 循环持续到通过、阻塞或达到最大尝试次数。
    while _has_attempt_budget(context, int_max_attempts):

        # attempt_context_attempt 持有本轮目录和 result 中的记录引用。
        attempt_context_a = _start_attempt(context, model_provider_a)  # 当前 attempt 的目录和记录上下文

        # terminal_result 非空时代表本轮产生 workflow 终态。
        dict_terminal = func_attempt(context, attempt_context_a, model_provider_a, list_stages, int_max_attempts)  # 终态信号

        # terminal_result 为 None 表示本轮失败但仍可进入下一轮修复。
        if dict_terminal is not None:

            # 返回已写盘的终态 result，避免外层继续启动 attempt。
            return dict_terminal

    # attempt 预算耗尽时写入 max_attempts 终态。
    return _finish_with_status(context, "max_attempts")

# provider 构造集中读取 provider 子配置，保证 stage 层不触碰命令配置。
def _build_provider(context: WorkflowExecutionContext) -> ModelProvider:
    """
    构造 workflow 使用的模型 provider。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :return: 当前 workflow run 复用的模型 provider 实例。
    """

    # provider_config 是 config 中的 provider 子树。
    provider_config = context.config["provider"]  # build_model_provider 使用的 provider 子配置

    # provider 名称保持字符串化，兼容 JSON 配置输入。
    str_provider_name = str(provider_config["name"])  # build_model_provider 的 provider 注册名

    # timeout_s 与旧实现一致，默认 120 秒。
    int_timeout_s = int(context.config.get("model_timeout_s", 120))  # provider 超时时间

    # 返回 provider 实例，后续 stage 共享。
    return build_model_provider(
        str_provider_name,
        command=provider_config.get("command"),
        timeout_s=int_timeout_s,
        config=context.config,
    )

# stage 顺序 helper 只处理配置覆盖和默认 stage 推导。
def _execution_stages(context: WorkflowExecutionContext) -> list[str]:
    """
    计算本次 workflow 的 stage 顺序。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :return: 本次 workflow 使用的 stage 名称列表。
    """

    # configured_stages 优先使用配置显式指定的阶段。
    list_configured_stages: list[Any] = context.config.get("stages", [])  # 用户显式覆盖的 stage 名称序列

    # generation_mode 决定默认 stage 集合。
    str_generation_mode = str(context.config.get("generation_mode") or "regular")  # 生成模式

    # default_stages 仅在配置为空时使用。
    list_default_stages: list[str] = _default_stages_for(context.plan["target"], str_generation_mode)  # target/mode 推导出的默认 stage 序列

    # 返回字符串化后的 stage 列表，兼容 JSON 中非字符串输入。
    return [str(item) for item in list_configured_stages or list_default_stages]

# attempt 预算 helper 让正常循环和 repair max 分支共用同一计数规则。
def _has_attempt_budget(context: WorkflowExecutionContext, int_max_attempts: int) -> bool:
    """
    判断 workflow 是否仍可启动下一次 attempt。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param int_max_attempts: 本次 workflow 允许启动的最大 attempt 数。
    :return: 仍可启动下一轮 attempt 时返回 True。
    """

    # attempts_count 从 result 中读取，保持 resume 后的历史计数。
    attempts_count = len(context.result.get("attempts", []))  # result 中已经持久化的 attempt 记录数量

    # 只要未达到上限就继续尝试。
    return attempts_count < int_max_attempts

# attempt 启动 helper 负责创建目录、登记 result 并立即写盘。
def _start_attempt(context: WorkflowExecutionContext, provider: Any) -> AttemptContext:
    """
    创建 attempt 目录并登记 result 记录。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :return: 新建 attempt 的目录、编号和 result 记录上下文。
    """

    # attempt_number 基于当前 result 历史生成稳定编号。
    attempt_number = len(context.result.get("attempts", [])) + 1  # 当前 attempt 序号

    # attempt_id 使用固定三位格式，保持 trace 兼容。
    str_attempt_id = f"attempt-{attempt_number:03d}"  # attempt 稳定编号

    # attempt_dir 通过写路径门禁后，后续 stage 只能在该目录下落盘。
    path_attempt_dir: Path = require_write_path(context.run_dir / str_attempt_id, purpose="attempt directory")  # 本轮 stage 输出根目录

    # mkdir 保证后续 stage 可以写入 prompt/response/artifact。
    path_attempt_dir.mkdir(parents=True, exist_ok=True)

    # attempt_record 初始化旧版 result schema 所需字段。
    dict_attempt_record = _new_attempt_record(str_attempt_id, FINAL_STAGE[context.plan["target"]], provider.name)  # result["attempts"] 持久化 attempt 摘要

    # result["attempts"] 是 workflow 的持久化 attempt 列表。
    context.result.setdefault("attempts", []).append(dict_attempt_record)

    # 新 attempt 立即写盘，防止后续 provider 失败丢失编号。
    _write_result(context.result_path, context.result)

    # 返回 attempt 上下文供后续 helper 复用。
    return AttemptContext(attempt_id=str_attempt_id, attempt_dir=path_attempt_dir, record=dict_attempt_record)

# attempt 运行 helper 串联 stage、validation、gate 和 repair。
def _run_attempt(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    provider: Any,
    stages: list[str],
    int_max_attempts: int,
) -> dict[str, Any] | None:
    """
    执行单次 attempt，返回终态结果或 None 表示可继续下一轮。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :param stages: 本次 workflow 固化后的 stage 名称序列。
    :param int_max_attempts: 本次 workflow 允许启动的最大 attempt 数。
    :return: 终态 result；返回 None 表示外层可继续下一轮 attempt。
    """

    # memory 只在第二轮及以后从 trace 构造。
    dict_memory = _load_prompt_memory(context, attempt)  # python/rtl stage 可选注入的 prompt memory

    # stage_state 保存本轮已完成 stage 输出和 codegen plan。
    stage_state = StageLoopState()  # stage 循环状态

    # stage 执行异常会立即结束 workflow，保持旧版失败语义。
    try:

        # stage_terminal 承接 codegen 人工确认分支已经写盘的终态 result。
        dict_stage_terminal = _run_stage_sequence(context, attempt, provider, stages, dict_memory, stage_state)  # codegen 阻塞终态 result

    # ManualResponseRequired 表示 provider 需要人工输入，当前 attempt 归入无效响应。
    except ManualResponseRequired as exc:

        # 手动响应缺失属于 provider 响应无效。
        return _fail_attempt(context, attempt, "invalid_response", str(exc))

    # 抽取、模型、规格和校验异常在这里统一转换为 workflow 终态。
    except (ExtractionError, ModelProviderError, SpecError, ValueError) as exc:

        # extraction 错误保留 invalid_response，其余异常沿用 failed。
        str_status = "invalid_response" if isinstance(exc, ExtractionError) else "failed"  # 异常映射后的 attempt 状态

        # 异常终态已经写入 result。
        return _fail_attempt(context, attempt, str_status, str(exc))

    # stage_terminal 非空表示 codegen_plan 已请求人工介入。
    if dict_stage_terminal is not None:

        # codegen 人工介入已经在 stage helper 中写盘。
        return dict_stage_terminal

    # validation_artifacts_validation_artifacts 是后续 pass 判定和 repair 的共同输入。
    validation_artifacts_validation_artifacts = _validate_attempt(context, attempt, provider, stage_state)  # validation 报告和 JSON 证据

    # gate_artifacts_gate_artifacts 绑定三类 gate 输出和 contract_paths 证据。
    gate_artifacts_gate_artifacts = _run_stage_gates(  # gate 证据集合
        context,  # gate 读取的 workflow 运行上下文
        attempt,  # 当前 attempt 的目录和记录对象
        stage_state,  # 各 stage 输出与 active codegen plan
        validation_artifacts_validation_artifacts,  # gate 合并时读取的验证错误来源
    )

    # validation 和 gate 均通过时 workflow 直接完成。
    if _attempt_passed(validation_artifacts_validation_artifacts, gate_artifacts_gate_artifacts):

        # passed 分支写入最终 result 并停止 attempt 循环。
        return _mark_attempt_passed(context, attempt, validation_artifacts_validation_artifacts)

    # repair_artifacts_repair_artifacts 写出下一轮修复所需证据。
    repair_artifacts_repair_artifacts = _write_repair_artifacts(  # 下一轮修复证据
        context,  # repair prompt 需要的运行配置和路径
        attempt,  # repair artifact 写入的当前轮次
        validation_artifacts_validation_artifacts,  # repair 诊断读取的 validation 证据
        gate_artifacts_gate_artifacts,  # repair 诊断读取的 stage gate 聚合证据
    )

    # repair plan 可能要求人工介入、工具链阻塞或继续下一轮。
    return _handle_repair_decision(
        context,
        attempt,
        validation_artifacts_validation_artifacts,
        repair_artifacts_repair_artifacts,
        int_max_attempts,
    )

# prompt memory helper 只在已有 trace 的重试轮次写入 memory 文件。
def _load_prompt_memory(context: WorkflowExecutionContext, attempt: AttemptContext) -> dict[str, Any] | None:
    """
    按旧规则为第二轮及以后构建 prompt memory。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :return: 可注入 stage prompt 的 memory 字典；不需要时返回 None。
    """

    # first_attempt 不需要 memory，避免把空 trace 注入 prompt。
    bool_first_attempt = len(context.result["attempts"]) <= 1  # 是否第一轮 attempt

    # 缺少 trace 文件时也不构造 memory。
    if bool_first_attempt or not context.trace_path.exists():

        # 第一轮或 trace 缺失时，prompt 不注入历史失败摘要。
        return None

    # memory 由 trace 和 plan 派生，用于下一轮 python/rtl prompt。
    dict_memory: dict[str, Any] = build_prompt_memory(context.trace_path, context.plan)  # python/rtl prompt 可引用的历史失败摘要

    # memory_path 固定写在当前 attempt 目录。
    memory_path = attempt.attempt_dir / "prompt_memory.json"  # 当前 attempt 的 prompt memory JSON 路径

    # prompt memory 写盘后挂入 attempt record。
    write_json(memory_path, dict_memory)

    # memory_path 使用 safe_path 后进入 result，避免暴露绝对路径。
    attempt.record["memory_path"] = safe_path(memory_path)  # result 中的 memory 证据路径

    # 返回 memory 供 stage 执行使用。
    return dict_memory

# stage 序列 helper 串联 requirements、codegen、python、rtl 和 review。
def _run_stage_sequence(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    provider: Any,
    stages: list[str],
    memory: dict[str, Any] | None,
    stage_state: StageLoopState,
) -> dict[str, Any] | None:
    """
    顺序执行 workflow stages，并处理 codegen 人工介入。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :param stages: 本次 workflow 固化后的 stage 名称序列。
    :param memory: 由历史 trace 构造的 prompt memory；第一轮或无 trace 时为 None。
    :param stage_state: 单个 attempt 内的 stage 输出和 codegen plan 状态。
    :return: codegen 人工介入终态 result；正常完成时返回 None。
    """

    # stages 顺序来自配置或默认 stage 集合。
    for stage in stages:

        # dict_stage_output 是当前 stage 的完整输出，后续 gate 会读取其中合同。
        dict_stage_output = _run_one_stage(context, attempt, provider, stages, memory, stage_state, stage)  # 当前 stage 输出

        # stage 输出同时进入完整表和 result 摘要。
        _record_stage_output(attempt, stage_state, stage, dict_stage_output)

        # codegen_plan 可能在生成前要求人工确认。
        if stage == "codegen_plan" and _codegen_needs_human(context, attempt, provider, dict_stage_output, stage_state):

            # codegen 阻塞时 result 已经被写成 blocked_human。
            return context.result

        # final stage 的 prompt/response/artifact 要提升到 attempt 顶层。
        if stage == FINAL_STAGE[context.plan["target"]]:

            # 顶层字段兼容旧版 result 消费方。
            _record_final_stage(context, attempt, stage, dict_stage_output)

    # stage 序列正常完成时通知外层进入 validation/gate。
    return None

# 单 stage helper 负责组织上一阶段输出、memory 和 active codegen plan。
def _run_one_stage(
    # workflow 上下文和 attempt 对象限定本 stage 的副作用边界。
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    provider: Any,

    # stage 顺序与历史 memory 决定 prompt 的上下文窗口。
    stages: list[str],
    memory: dict[str, Any] | None,
    stage_state: StageLoopState,

    # stage 名称选择 prompt 模板和输出合同。
    stage: str,
) -> dict[str, Any]:
    """
    执行单个 stage 并返回完整输出。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :param stages: 本次 workflow 固化后的 stage 名称序列。
    :param memory: 由历史 trace 构造的 prompt memory；第一轮或无 trace 时为 None。
    :param stage_state: 单个 attempt 内的 stage 输出和 codegen plan 状态。
    :param stage: 当前正在执行或记录的 stage 名称。
    :return: 当前 stage 的完整输出对象。
    """

    # previous_stage_output 连接当前 stage 与上一 stage 的 artifact。
    dict_previous_stage_output = stage_state.stage_outputs.get(_previous_stage(stage, stages))  # 上一阶段输出

    # stage_memory 只注入 Python/RTL 阶段，保持旧版 prompt 行为。
    dict_stage_memory = memory if stage in {"python", "rtl"} else None  # 当前 stage 可见 memory

    # 委托 workflow_stage 执行 prompt、provider、extract 和局部审计。
    return _run_generation_stage(
        # 运行目录和 attempt 标识限定所有 stage 副作用。
        run_dir=context.run_dir,
        attempt_dir=attempt.attempt_dir,
        attempt_id=attempt.attempt_id,

        # plan/stage/provider/config 是生成阶段的核心输入。
        plan=context.plan,
        stage=stage,
        provider=provider,
        config=context.config,

        # prompt 上下文由 memory、人工决策和上一阶段输出组成。
        memory=dict_stage_memory,
        decision=context.decision,
        previous_stage=dict_previous_stage_output,

        # 跨阶段合同和 active plan 保持原 workflow wire shape。
        stage_outputs=stage_state.stage_outputs,
        active_codegen_plan=stage_state.active_codegen_plan,

        # trace/state 路径把 stage 事件和状态写回同一个 run。
        trace_path=context.trace_path,
        state_path=context.state_path,
        state_updates=context.state_updates,
    )

# stage 记录 helper 同步完整输出表和 result 摘要字段。
def _record_stage_output(
    attempt: AttemptContext,
    stage_state: StageLoopState,
    stage: str,
    dict_stage_output: dict[str, Any],
) -> None:
    """
    把 stage 输出写入 attempt record 和 stage state。
    
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param stage_state: 单个 attempt 内的 stage 输出和 codegen plan 状态。
    :param stage: 当前正在执行或记录的 stage 名称。
    :param dict_stage_output: 当前 stage 的完整输出对象。
    :return: 无返回值，直接更新 attempt record 和 stage_state。
    """

    # stage_state 保存完整输出，供后续 validation/gate 使用。
    stage_state.stage_outputs[stage] = dict_stage_output  # validation/gate 读取的完整 stage 输出

    # attempt record 只保存 summary，避免 result JSON 过大。
    attempt.record.setdefault("stage_outputs", {})[stage] = dict_stage_output["summary"]  # result 中的 stage 摘要

# codegen plan helper 判断生成前是否必须等待人工补齐需求。
def _codegen_needs_human(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    provider: Any,
    dict_stage_output: dict[str, Any],
    stage_state: StageLoopState,
) -> bool:
    """
    处理 codegen_plan stage 的人工确认分支。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :param dict_stage_output: 当前 stage 的完整输出对象。
    :param stage_state: 单个 attempt 内的 stage 输出和 codegen plan 状态。
    :return: codegen_plan 需要人工确认时返回 True。
    """

    # codegen_plan_path 继续写回 plan，供后续 stage prompt 使用。
    context.plan["codegen_plan_path"] = dict_stage_output["summary"]["artifact_path"]  # 后续 prompt 引用的 codegen plan 路径

    # codegen_plan 为空时维持旧行为，直接继续后续 stage。
    dict_codegen_plan = dict_stage_output.get("codegen_plan")  # 可能包含 open_questions 的 codegen plan 数据

    # 没有 codegen_plan 就不触发人工介入。
    if not dict_codegen_plan:

        # 无结构化 codegen_plan 时不改变 stage 流程。
        return False

    # active_codegen_plan 提供给后续 prompt。
    stage_state.active_codegen_plan = dict_codegen_plan  # 后续 stage prompt 引用的 codegen plan

    # ready_for_generation 和 open_questions 共同决定是否阻塞生成。
    bool_has_open_questions = bool(dict_codegen_plan.get("open_questions"))  # codegen plan 是否仍有未闭合问题

    # 未 ready 或仍有问题时需要先请求人工确认。
    bool_needs_human = not dict_codegen_plan.get("ready_for_generation", False) or bool_has_open_questions  # 需求未闭合时阻塞生成

    # ready 时继续后续 stage。
    if not bool_needs_human:

        # 计划已就绪时继续 Python/RTL 生成。
        return False

    # 写出 codegen 阶段专用 intervention。
    _write_codegen_intervention(context, attempt, provider, dict_codegen_plan)

    # True 表示 workflow 已进入 blocked_human 终态。
    return True

# codegen 人工介入 helper 保留旧版 intervention schema。
def _write_codegen_intervention(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    provider: Any,
    codegen_plan: dict[str, Any],
) -> None:
    """
    写出 codegen_plan 未就绪时的人类介入请求。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :param codegen_plan: codegen_plan 参数，参与当前 workflow helper 的业务处理。
    :return: blocked_human 状态下已写盘的 workflow result。
    """

    # intervention_path 固定放在当前 attempt 目录。
    intervention_path = attempt.attempt_dir / "intervention.json"  # 人工介入 JSON 路径

    # questions 为空时提供兼容旧行为的默认问题。
    list_questions: list[Any] = codegen_plan.get("open_questions") or ["Confirm the remaining design requirements."]  # 待确认问题列表

    # dict_intervention 保持旧版 codegen 人工介入 schema，供前端或人工审查读取。
    dict_intervention = {  # 写入 intervention.json，包含 open_questions、question 和 expected_answer_format
        "version": 1,  # 人工介入协议版本号
        "action": "ask_human",  # 调度器据此暂停并请求用户输入
        "primary_source": "needs_human_intervention",  # codegen 阻塞来源
        "question": str(list_questions[0]),  # 首个需要人工回答的问题
        "observations": codegen_plan.get("open_questions", []),  # 全量未闭合问题
        "attempted_actions": ["requirements normalization", "code generation planning"],  # 已尝试的自动步骤
        "expected_answer_format": {  # 人工回答建议结构
            "decision": "one concise design decision",  # 需要补齐的设计决策
            "evidence": "requirement source or design rationale",  # 决策依据或规格来源
            "constraints": "any interface or pipeline constraints to preserve",  # 必须保留的接口或时序约束
        },
    }

    # 介入请求写盘后再把 release-safe 路径挂入 result。
    write_json(intervention_path, dict_intervention)

    # intervention_path 以 safe_path 形式进入 attempt record。
    attempt.record["intervention_path"] = safe_path(intervention_path)  # result 中的 intervention 相对路径

    # codegen_plan 未就绪时 workflow 停在 blocked_human。
    _finish_attempt_with_status(context, attempt, "blocked_human")

    # state 记录人工介入证据路径。
    _record_state(
        context.state_path,
        "human_intervention",
        {"output": intervention_path, "attempt_id": attempt.attempt_id, "primary_source": "needs_human_intervention"},
        enabled=context.state_updates,
    )

    # trace 记录 provider 和 primary_source。
    append_trace_event(
        context.trace_path,
        {
            "event": "human_intervention",
            "attempt_id": attempt.attempt_id,
            "output": intervention_path,
            "primary_source": "needs_human_intervention",
            "provider": provider.name,
        },
    )

# final stage 记录 helper 维护旧版 attempt 顶层字段兼容性。
def _record_final_stage(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    stage: str,
    dict_stage_output: dict[str, Any],
) -> None:
    """
    把最终 stage 摘要提升到 attempt 顶层。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param stage: 当前正在执行或记录的 stage 名称。
    :param dict_stage_output: 当前 stage 的完整输出对象。
    :return: 无返回值，直接更新 final stage 的 attempt 顶层字段。
    """

    # summary 是 prompt/response/artifact 顶层兼容字段的事实来源。
    dict_summary: dict[str, Any] = dict_stage_output["summary"]  # final stage 对外摘要

    # dict_final_stage_fields 保持旧 report 消费方读取的顶层字段集合。
    dict_final_stage_fields = {  # 写入 prompt_path、response_path、artifact_dir 和 stage 四个旧版顶层索引
        "prompt_path": dict_summary["prompt_path"],  # 旧报告入口读取的最终提示词路径
        "response_path": dict_summary["response_path"],  # 旧报告入口读取的模型响应路径
        "artifact_dir": dict_summary["artifact_dir"],  # 旧报告入口读取的最终产物目录
        "stage": stage,  # 标记这些顶层路径来自哪个最终阶段
    }

    # attempt record 一次性更新，避免字段间中途写盘。
    attempt.record.update(dict_final_stage_fields)

    # last_attempt_id 指向刚完成 final stage 的 attempt。
    context.result["last_attempt_id"] = attempt.attempt_id  # 最新 final stage attempt 编号

    # final stage 完成后立即写 result。
    _write_result(context.result_path, context.result)

# attempt 失败 helper 保持异常到 result 状态的旧映射。
def _fail_attempt(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    status: str,
    error_text: str,
) -> dict[str, Any]:
    """
    将 attempt 标记为失败并返回 workflow result。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param status: 需要写入 attempt 和 workflow result 的终态状态。
    :param error_text: error_text 参数，参与当前 workflow helper 的业务处理。
    :return: 失败状态写盘后的 workflow result。
    """

    # attempt error 字段保留异常文本。
    attempt.record["error"] = error_text  # attempt 失败原因文本

    # 失败状态立即写入 result。
    return _finish_attempt_with_status(context, attempt, status)

# validation helper 运行生成产物检查并写出证据。
def _validate_attempt(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    provider: Any,
    stage_state: StageLoopState,
) -> ValidationArtifacts:
    """
    运行 final stage 的 validation 并写 trace/state。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :param stage_state: 单个 attempt 内的 stage 输出和 codegen plan 状态。
    :return: validation 报告对象和 JSON 证据路径。
    """

    # final_stage 由目标语言决定，用于定位最终 artifact。
    str_final_stage = FINAL_STAGE[context.plan["target"]]  # 最终 stage 名称

    # final_output 是 validate_generated 的 artifact_dir 与合同输入。
    dict_final_output: dict[str, Any] = stage_state.stage_outputs[str_final_stage]  # validation 使用的 final stage 输出

    # semantic_contract 来自 Python stage，用于 RTL 语义比对。
    semantic_contract = stage_state.stage_outputs.get("python", {}).get("semantic_contract")  # Python 参考合同

    # validation report 覆盖本地检查、可选外部仿真和注释策略。
    validation_report = validate_generated(  # validate_generated 返回的本地检查报告
        context.plan,  # 规范化生成计划
        dict_final_output["artifact_dir"],  # 待验证 artifact 目录
        target=context.plan["target"],  # 验证目标语言
        run_external=bool(context.config.get("run_external", True)),  # 是否运行外部工具
        readiness=str(context.config.get("readiness", "execute")),  # readiness 检查档位
        comment_language=str(context.config.get("comment_language", "zh")),  # 注释策略语言
        semantic_contract=semantic_contract,  # RTL 语义比对用的 Python 合同
    )

    # validation_json_path 是 attempt 内固定证据路径。
    validation_json_path = attempt.attempt_dir / "validation.json"  # validation 报告 JSON 路径

    # validation 报告先写盘，后续 trace/state 只引用路径。
    write_json(validation_json_path, validation_report.to_dict())

    # attempt record 保存 release-safe 的 validation 路径。
    attempt.record["validation_json"] = safe_path(validation_json_path)  # result 中 validation JSON 相对路径

    # state 记录 validation 的机读摘要。
    _record_validation_state(context, dict_final_output, validation_json_path, validation_report)

    # trace 记录 provider、issue 和 metrics 细节。
    _trace_validation(context, attempt, provider, dict_final_output, validation_json_path, validation_report)

    # 返回 report 和路径供 gate/repair 使用。
    return ValidationArtifacts(report=validation_report, report_path=validation_json_path)

# validation state helper 只写轻量摘要，完整问题留给 trace。
def _record_validation_state(
    context: WorkflowExecutionContext,
    final_output: dict[str, Any],
    validation_json_path: Path,
    validation_report: Any,
) -> None:
    """
    写入 validation state 摘要。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param final_output: final_output 参数，参与当前 workflow helper 的业务处理。
    :param validation_json_path: validation_json_path 参数，参与当前 workflow helper 的业务处理。
    :param validation_report: validate_generated 返回的 validation report。
    :return: 无返回值，按需写入 validation state。
    """

    # dict_state_payload 保留 validation state 需要的路径、readiness 和总体结论。
    dict_state_payload = {  # validation state 机读摘要
        "path": final_output["artifact_dir"],  # 被检查的 artifact 目录
        "output": validation_json_path,  # validation JSON 证据路径
        "readiness": context.config.get("readiness"),  # 本次 readiness 档位
        "ok": validation_report.ok(),  # validation 总体结论
    }

    # 可选 state 写入由 context.state_updates 控制。
    _record_state(context.state_path, "validate", dict_state_payload, enabled=context.state_updates)

# validation trace helper 保留 provider、issue 与 metrics 的完整审计信息。
def _trace_validation(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    provider: Any,
    final_output: dict[str, Any],
    validation_json_path: Path,
    validation_report: Any,
) -> None:
    """
    写入 validation trace 事件。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param provider: 当前 workflow 复用的模型 provider 实例。
    :param final_output: final_output 参数，参与当前 workflow helper 的业务处理。
    :param validation_json_path: validation_json_path 参数，参与当前 workflow helper 的业务处理。
    :param validation_report: validate_generated 返回的 validation report。
    :return: 无返回值，追加 validation trace 事件。
    """

    # issue_sources 收集可驱动 repair 分类的 validation issue source 字段。
    set_issue_sources: set[str] = {
        issue.source  # repair 分类用 issue.source 字段
        for issue in validation_report.issues  # validation 报告中的所有 issue
        if issue.severity in {"error", "warning", "skip"}  # 只保留会影响 repair 的级别
    }  # repair plan 输入的问题来源集合

    # error_sources 使用稳定排序，保证 trace JSON 可重复。
    list_error_sources: list[str] = sorted(set_issue_sources)  # repair plan 分类用的问题来源列表

    # semantic_ready 提取语义执行 readiness，缺失时保持 None。
    bool_semantic_ready = _semantic_ready_metric(validation_report)  # 语义执行 ready 标志

    # dict_trace_payload 保持旧版 validate trace 字段集合。
    dict_trace_payload = {  # 写入 validate 事件，包含 errors、warnings、issues、metrics 和 report_json
        "event": "validate",  # trace 读取器识别 validation 事件
        "attempt_id": attempt.attempt_id,  # 验证事件所属的尝试轮次
        "target": context.plan["target"],  # 被生成目标语言
        "readiness": context.config.get("readiness"),  # validation 执行档位
        "path": final_output["artifact_dir"],  # validation 输入 artifact 目录
        "ok": validation_report.ok(),  # validation 是否通过
        "errors": validation_report.errors,  # 阻塞发布的错误数量
        "warnings": validation_report.warnings,  # 进入修复提示的警告数量
        "skips": validation_report.skips,  # 记录未执行检查的数量
        "error_sources": list_error_sources,  # repair 分类问题来源
        "report_json": validation_json_path,  # 本轮 validation.json 证据路径
        "metrics": validation_report.metrics or {},  # validation 指标集合
        "issues": [issue.to_dict() for issue in validation_report.issues],  # 完整 issue 列表
        "comment_language": context.config.get("comment_language"),  # 注释语言策略
        "provider": provider.name,  # 本轮模型 provider 名称
        "semantic_ready": bool_semantic_ready,  # 语义执行 readiness 结果
    }

    # validate trace 追加到 workflow JSONL 证据流。
    append_trace_event(context.trace_path, dict_trace_payload)

# semantic readiness helper 从 validation metrics 中取出语义执行状态。
def _semantic_ready_metric(validation_report: Any) -> bool | None:
    """
    从 validation metrics 中提取 semantic_ready 字段。
    
    :param validation_report: validate_generated 返回的 validation report。
    :return: semantic_execution.ready 指标；缺失时返回 None。
    """

    # metrics 为空时直接视为语义执行状态未知。
    dict_metrics: dict[str, Any] = validation_report.metrics or {}  # semantic_execution 所在的 metrics 源字典

    # semantic_execution 可能不存在或不是 dict。
    dict_semantic_execution = dict_metrics.get("semantic_execution")  # semantic_execution 指标对象

    # 只有 dict 指标才读取 semantic_ready。
    if isinstance(dict_semantic_execution, dict):

        # semantic_ready 只有布尔值才进入 trace，避免字符串污染 readiness 字段。
        bool_semantic_ready = dict_semantic_execution.get("semantic_ready")  # 语义执行布尔状态

        # 非布尔指标视为未知，保持 trace schema 稳定。
        return bool_semantic_ready if isinstance(bool_semantic_ready, bool) else None

    # 非 dict 指标视为未知。
    return None

# stage gate helper 汇总 interface、semantic 和 review 三类证据。
def _run_stage_gates(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    stage_state: StageLoopState,
    validation_artifacts: ValidationArtifacts,
) -> GateArtifacts:
    """
    运行 interface、semantic 和 review gate。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param stage_state: 单个 attempt 内的 stage 输出和 codegen plan 状态。
    :param validation_artifacts: validation 报告和 JSON 证据路径。
    :return: stage gate 证据对象。
    """

    # final_stage 选择最终 RTL/Python stage 输出，用于 gate 输入。
    str_final_stage = FINAL_STAGE[context.plan["target"]]  # gate 输入 stage 名称

    # final_output 提供 gate 所需 artifact_dir 与 contract_paths。
    dict_final_output: dict[str, Any] = stage_state.stage_outputs[str_final_stage]  # gate 输入 artifact 与合同

    # dict_contract_paths 从 final stage contract_paths 复制，避免原地污染。
    dict_contract_paths = dict(dict_final_output["contract_paths"])  # 合同路径汇总

    # interface_gate 比对 Python/RTL 接口合同。
    dict_interface_gate = _interface_gate(  # 返回接口合同比对结果，失败时阻断 workflow passed 状态
        context.plan,  # 接口 gate 用于读取目标语言和接口约束
        stage_state.stage_outputs,  # 接口 gate 用于比对 Python/RTL 合同
        dict_final_output,  # 接口 gate 使用的最终产物摘要
        attempt.attempt_dir,  # interface_gate.json 写入目录
        context.trace_path,  # interface gate 追加 trace 的路径
    )

    # interface gate 适用时把合同比对证据挂入 contract_paths。
    _append_gate_path(dict_contract_paths, "interface_gate", dict_interface_gate)

    # semantic_gate 比对 validation 语义执行与向量合同。
    dict_semantic_gate = _semantic_gate(  # validation 语义执行结果与向量合同的交叉检查对象
        context.plan,  # 语义 gate 用于定位 target 和语义合同
        validation_artifacts.report,  # 语义 gate 读取 validation metrics
        stage_state.stage_outputs,  # 语义 gate 读取 vector/reference 合同
        attempt.attempt_dir,  # 语义 gate 证据文件所在 attempt 目录
        context.trace_path,  # semantic gate 记录语义比对事件
    )

    # semantic gate 适用时把语义一致性证据挂入 contract_paths。
    _append_gate_path(dict_contract_paths, "semantic_gate", dict_semantic_gate)

    # review_gate 把 deep review 阻塞 findings 纳入 workflow pass 判定。
    dict_review_gate = _review_gate(stage_state.stage_outputs, attempt.attempt_dir, context.trace_path)  # deep review 阻塞 findings 的判定对象

    # review gate 适用时把人工审查 findings 证据挂入 contract_paths。
    _append_gate_path(dict_contract_paths, "review_gate", dict_review_gate)

    # combined_gate 先合并 interface 和 semantic，保持旧版组合顺序。
    dict_combined_gate = _combine_gate_results(  # 合并 interface 与 semantic 的 ready 状态供 passed 判定读取
        dict_interface_gate["result"] if dict_interface_gate else None,  # 接口合同检查的 ready 结果
        dict_semantic_gate["result"] if dict_semantic_gate else None,  # 语义合同检查的 ready 结果
    )

    # review gate 存在时继续合并。
    if dict_review_gate is not None:

        # combined_gate 加入 review 结论后才用于最终 pass 判定。
        dict_combined_gate = _combine_gate_results(dict_combined_gate, dict_review_gate["result"])  # 三类 gate 合并结果

    # spec_issue 交给 repair 直接处理，不使用 gate 作为修复证据。
    dict_effective_gate = _effective_gate(validation_artifacts.report, dict_combined_gate)  # repair prompt 可用 gate 结果

    # combined_gate 存在时写 stage_verification.json。
    _write_stage_verification(context, attempt, dict_contract_paths, dict_combined_gate)

    # attempt record 挂入所有合同路径。
    attempt.record["contract_paths"] = dict_contract_paths  # result 中的 gate 合同路径集合

    # 返回 gate 产物供 pass 判定和 repair 使用。
    return GateArtifacts(dict_contract_paths, dict_combined_gate, dict_effective_gate)

# gate path helper 只在 gate 适用时写入 release-safe 路径。
def _append_gate_path(dict_contract_paths: dict[str, Any], key: str, gate: dict[str, Any] | None) -> None:
    """
    把 gate 路径安全写入 contract_paths。
    
    :param dict_contract_paths: attempt record 中保存的 release-safe 合同路径表。
    :param key: contract_paths 中写入的 gate 名称。
    :param gate: 单个 gate 的输出对象；不适用时为 None。
    :return: 无返回值，适用 gate 会写入 contract_paths。
    """

    # gate 为空表示该 gate 在当前 target/mode 下不适用。
    if gate is None:

        # 不适用的 gate 不写 contract_paths，避免制造虚假证据。
        return

    # gate path 使用 safe_path，避免绝对路径进入报告。
    dict_contract_paths[key] = safe_path(gate["path"])  # 当前 gate 报告的 release-safe 路径

# repair gate helper 避免规格错误被误判为实现错误。
def _effective_gate(validation_report: Any, combined_gate: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    计算 repair prompt 实际可用的 gate 结果。
    
    :param validation_report: validate_generated 返回的 validation report。
    :param combined_gate: interface、semantic 和 review gate 的组合结果。
    :return: repair prompt 可使用的 gate 结果；spec_issue 时返回 None。
    """

    # spec_issue 说明规格自身不完整，gate 结果不应误导修复。
    has_spec_issue = any(issue.source == "spec_issue" for issue in validation_report.issues)  # 是否规格问题

    # 规格问题直接清空 effective gate。
    if has_spec_issue:

        # spec_issue 优先交给规格修复，不让 gate 结果误导 repair。
        return None

    # 其他情况沿用 combined_gate。
    return combined_gate

# stage verification helper 在 gate 存在时写出组合证据。
def _write_stage_verification(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    dict_contract_paths: dict[str, Any],
    combined_gate: dict[str, Any] | None,
) -> None:
    """
    在存在 gate 结果时写出 stage_verification.json。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param dict_contract_paths: attempt record 中保存的 release-safe 合同路径表。
    :param combined_gate: interface、semantic 和 review gate 的组合结果。
    :return: 无返回值，存在 gate 结果时写出 stage_verification.json。
    """

    # combined_gate 为空表示无可写 stage verification。
    if not combined_gate:

        # 没有 gate 结果时不生成 stage_verification.json。
        return

    # stage_verification_path 是 gate 合并结果证据。
    stage_verification_path = attempt.attempt_dir / "stage_verification.json"  # 合并 gate 证据 JSON 路径

    # 写出合并 gate 结果，供 release 证据和 repair trace 复查。
    write_json(stage_verification_path, combined_gate)

    # contract_paths 使用 safe_path 避免本机绝对路径进入 result。
    dict_contract_paths["stage_verification"] = safe_path(stage_verification_path)  # stage verification 相对路径

    # state 记录 stage gate readiness。
    _record_state(
        context.state_path,
        "verify_stage",
        {"output": stage_verification_path, "ready": combined_gate.get("ready")},
        enabled=context.state_updates,
    )

# pass 判定 helper 统一 validation 和 gate 的完成条件。
def _attempt_passed(validation_artifacts: ValidationArtifacts, gate_artifacts: GateArtifacts) -> bool:
    """
    判断 validation 和 stage gate 是否均通过。
    
    :param validation_artifacts: validation 报告和 JSON 证据路径。
    :param gate_artifacts: stage gate 合同路径、组合结果和 repair 可用 gate。
    :return: validation 与 gate 都通过时返回 True。
    """

    # validation_ok 是 validate_generated 的总体结论。
    bool_validation_ok = validation_artifacts.report.ok()  # validation 总体 ok 判定

    # gate_ready 为空 gate 时视为通过，保持旧版条件。
    bool_gate_ready = gate_artifacts.combined_gate is None or gate_artifacts.combined_gate.get("ready", True)  # stage gate 是否全部通过

    # deliverable_matrix_ready 确认八类最终交付门禁全部 ready。
    bool_deliverable_matrix_ready = _deliverable_matrix_ready(validation_artifacts.report)  # 公开交付矩阵通过状态

    # 三者都通过才视为 workflow passed。
    return bool_validation_ok and bool_gate_ready and bool_deliverable_matrix_ready

# _deliverable_matrix_ready 从 validation metrics 中提取八类交付门禁状态。
def _deliverable_matrix_ready(validation_report: Any) -> bool:
    """
    判断 validation report 中的八类交付矩阵是否全部 ready。
    
    :param validation_report: validate_generated 返回的 ValidationReport 对象。
    :return: 没有交付矩阵或全部 ready 时返回 True。
    """

    # metrics 缺失时保持旧版兼容，交由 validation_report.ok() 判定。
    dict_metrics = getattr(validation_report, "metrics", None)  # 读取交付矩阵所在的 validation metrics 容器

    # 非 dict metrics 不参与交付矩阵判定。
    if not isinstance(dict_metrics, dict):

        # 没有结构化 metrics 时不额外阻断旧路径。
        return True

    # deliverable metrics 是 validation_impl 写入的新交付门禁摘要。
    dict_deliverable_metrics = dict_metrics.get("verilog_generated_deliverable_gate")  # 最终交付门禁 metrics

    # 未运行 deliverable gate 的旧路径保持兼容。
    if not isinstance(dict_deliverable_metrics, dict):

        # 没有交付 metrics 时不额外阻断。
        return True

    # delivery_ready=false 已经足以阻断 workflow passed。
    bool_delivery_ready = bool(dict_deliverable_metrics.get("delivery_ready", True))  # 兼容旧报告缺省时的交付状态

    # 不可交付状态必须阻断 attempt passed。
    if not bool_delivery_ready:

        # 不可交付时不能宣称 attempt passed。
        return False

    # checks 必须是八类矩阵字典；缺失时只依据 delivery_ready。
    dict_checks = dict_deliverable_metrics.get("checks")  # 八类门禁矩阵

    # 非 dict checks 无法进一步细分，兼容旧报告。
    if not isinstance(dict_checks, dict):

        # 只要 delivery_ready 没有显式 false，就不额外阻断。
        return True

    # 八类公开门禁中任何一项 ready=false 都阻断 attempt passed。
    for str_check_name in DELIVERABLE_MATRIX_CHECK_NAMES:

        # 当前门禁摘要必须是 dict 才读取 ready 字段。
        dict_check = dict_checks.get(str_check_name)  # 单个公开门禁摘要

        # 缺失门禁不能视为修复完成。
        if not isinstance(dict_check, dict):

            # 公开矩阵不完整时阻断 attempt passed。
            return False

        # 当前门禁 ready 字段归一化为布尔值。
        bool_check_ready = bool(dict_check.get("ready", False))  # 单个公开门禁是否通过

        # ready 显式失败不能视为修复完成。
        if not bool_check_ready:

            # 公开矩阵存在失败项时阻断 attempt passed。
            return False

    # 全部公开门禁均为 ready。
    return True

# passed helper 写入终态和成功 attempt 证据。
def _mark_attempt_passed(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    validation_artifacts: ValidationArtifacts,
) -> dict[str, Any]:
    """
    将 workflow 标记为 passed 并返回 result。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param validation_artifacts: validation 报告和 JSON 证据路径。
    :return: passed 状态写盘后的 workflow result。
    """

    # passed 状态写入 attempt/result。
    _finish_attempt_with_status(context, attempt, "passed")

    # last_attempt_id 指向通过的 attempt。
    context.result["last_attempt_id"] = attempt.attempt_id  # 本次通过的 attempt 编号

    # 写盘后再记录 state。
    _write_result(context.result_path, context.result)

    # workflow_attempt state 是成功 attempt 的最终证据。
    _record_state(
        context.state_path,
        "workflow_attempt",
        {"attempt_id": attempt.attempt_id, "status": "passed", "validation_json": validation_artifacts.report_path},
        enabled=context.state_updates,
    )

    # 返回终态 result。
    return context.result

# repair artifact helper 写出下一轮修复所需的 prompt、plan 和 diagnosis。
def _write_repair_artifacts(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    validation_artifacts: ValidationArtifacts,
    gate_artifacts: GateArtifacts,
) -> RepairArtifacts:
    """
    写出 repair prompt、repair plan 和 diagnosis。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param validation_artifacts: validation 报告和 JSON 证据路径。
    :param gate_artifacts: stage gate 合同路径、组合结果和 repair 可用 gate。
    :return: repair prompt、repair plan 和 diagnosis 证据对象。
    """

    # report_text 是 repair prompt 的主输入，保留人类可读 validation 摘要。
    str_report_text = validation_artifacts.report.format()  # repair prompt 读取的 validation 文本

    # repair_prompt_path 保存下一轮模型可读修复提示。
    repair_prompt_path = attempt.attempt_dir / "repair_prompt.md"  # 下一轮模型 repair prompt 路径

    # repair_plan_path 保存结构化继续/阻塞决策。
    repair_plan_path = attempt.attempt_dir / "repair_plan.json"  # workflow repair 决策 JSON 路径

    # diagnosis_path 保存 build_diagnosis 输出，用于 checkpoint drift 和局部化审计。
    diagnosis_path = attempt.attempt_dir / "diagnosis.json"  # repair 诊断证据 JSON 路径

    # trace_events 只读取一次，保证 repair 三份证据基于同一 trace 快照。
    list_trace_events: list[dict[str, Any]] = read_trace(context.trace_path)  # 三个 repair helper 共用的 trace 快照

    # validation_dict 供 reflection helper 复用同一份机读 validation 结果。
    dict_validation_report: dict[str, Any] = validation_artifacts.report.to_dict()  # repair 使用的 validation 机读报告

    # repair_prompt 提供下一轮模型修复指导。
    str_repair_prompt = generate_repair_prompt(  # 面向下一轮 provider 的修复提示文本
        str_report_text,  # prompt 中的人类可读失败报告
        context.plan,  # prompt 中的目标、接口和 stage 计划
        list_trace_events,  # prompt 中的历史 stage/validation 事件
        dict_validation_report,  # prompt 中的机读 issue 与 metrics
        None,  # prompt 生成保留旧扩展位
        gate_artifacts.effective_gate,  # prompt 中的 gate 阻塞证据
    )

    # repair_plan 决定是否继续、人工介入或工具链阻塞。
    dict_repair_plan: dict[str, Any] = build_repair_plan(  # 自动修复循环的下一步决策对象
        str_report_text,  # 决策分类读取的失败报告文本
        context.plan,  # 决策分类读取的目标和约束
        list_trace_events,  # 决策分类读取的历史事件序列
        dict_validation_report,  # 决策分类读取的 issue 明细
        None,  # 决策分类保留旧扩展位
        gate_artifacts.effective_gate,  # 决策分类读取的 gate 阻塞证据
    )

    # diagnosis 说明失败是否可自动调试，以及应优先定位哪个 checkpoint。
    dict_diagnosis: dict[str, Any] = build_diagnosis(  # intervention 前判断是否还能自动 debug 的诊断对象
        context.plan,  # 诊断读取的 checkpoint 计划
        list_trace_events,  # 诊断读取的执行轨迹
        dict_validation_report,  # 诊断读取的 validation issue
        gate_artifacts.effective_gate,  # 诊断读取的 gate 阻塞上下文
    )

    # repair prompt 写盘供下一轮模型输入。
    write_text(repair_prompt_path, str_repair_prompt)

    # repair plan 写盘供 resume 和人工审计复查。
    write_json(repair_plan_path, dict_repair_plan)

    # diagnosis 写盘供 checkpoint drift 定位。
    write_json(diagnosis_path, dict_diagnosis)

    # dict_repair_paths 只保存 release-safe 路径。
    dict_repair_paths = {
        "repair_plan": safe_path(repair_plan_path),  # 下一轮修复策略的 release-safe 路径
        "diagnosis_path": safe_path(diagnosis_path),  # checkpoint 诊断报告的 release-safe 路径
    }  # attempt record 使用的 repair 二级证据索引

    # attempt record 一次性挂入 repair 证据路径。
    attempt.record.update(dict_repair_paths)

    # state 记录 reflection 产物路径。
    _record_repair_state(context, repair_prompt_path, repair_plan_path, diagnosis_path)

    # trace 记录 repair 决策和诊断摘要。
    _trace_repair(context, attempt, repair_prompt_path, repair_plan_path, dict_repair_plan, dict_diagnosis)

    # 返回 repair 证据对象。
    return RepairArtifacts(str_report_text, dict_repair_plan, dict_diagnosis, repair_prompt_path)

# repair state helper 记录三份 reflection 产物的路径。
def _record_repair_state(
    context: WorkflowExecutionContext,
    repair_prompt_path: Path,
    repair_plan_path: Path,
    diagnosis_path: Path,
) -> None:
    """
    写入 repair state 事件。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param repair_prompt_path: repair prompt Markdown 证据路径。
    :param repair_plan_path: repair plan JSON 证据路径。
    :param diagnosis_path: diagnosis JSON 证据路径。
    :return: 无返回值，按需写入 repair state。
    """

    # dict_state_payload 关联 prompt、plan 和 diagnosis 三份证据。
    dict_state_payload = {
        "output": repair_prompt_path,  # 下一轮模型读取的 repair prompt
        "repair_plan": repair_plan_path,  # 自动修复循环的结构化决策
        "diagnosis": diagnosis_path,  # checkpoint drift 定位诊断
    }  # repair state 事件字段集合

    # state 写入受 context.state_updates 控制。
    _record_state(context.state_path, "reflect", dict_state_payload, enabled=context.state_updates)

# repair trace helper 记录模型修复决策和诊断摘要。
def _trace_repair(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    repair_prompt_path: Path,
    repair_plan_path: Path,
    repair_plan: dict[str, Any],
    diagnosis: dict[str, Any],
) -> None:
    """
    写入 reflect trace 事件。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param repair_prompt_path: repair prompt Markdown 证据路径。
    :param repair_plan_path: repair plan JSON 证据路径。
    :param repair_plan: reflection helper 生成的下一步修复决策。
    :param diagnosis: checkpoint/localization 诊断结果。
    :return: 无返回值，追加 reflect trace 事件。
    """

    # dict_trace_payload 保持旧版 reflect 字段。
    dict_trace_payload = {  # 写入 reflect 事件，记录 repair_plan、action、error_sources 和 diagnosis
        "event": "reflect",  # trace 读取器识别反思修复事件
        "attempt_id": attempt.attempt_id,  # 修复事件所属的尝试轮次
        "output": repair_prompt_path,  # reflect 事件关联的 prompt 文件
        "repair_plan": repair_plan_path,  # reflect 事件关联的决策文件
        "error_sources": repair_plan.get("error_sources", []),  # repair 分类来源
        "action": repair_plan.get("action"),  # repair 下一步动作
        "diagnosis": diagnosis,  # 检查点漂移和定位诊断
        "auto_debug_before_human": diagnosis.get("auto_debug_before_human"),  # 人工介入前自动调试建议
    }

    # reflect 事件落入同一 JSONL 时间线，供下一轮 memory 读取。
    append_trace_event(context.trace_path, dict_trace_payload)

# repair decision helper 把 plan action 映射成 workflow 终态或下一轮。
def _handle_repair_decision(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    validation_artifacts: ValidationArtifacts,
    repair_artifacts: RepairArtifacts,
    int_max_attempts: int,
) -> dict[str, Any] | None:
    """
    根据 repair plan 决定终止或继续下一轮。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param validation_artifacts: validation 报告和 JSON 证据路径。
    :param repair_artifacts: repair prompt、repair plan 和 diagnosis 证据对象。
    :param int_max_attempts: 本次 workflow 允许启动的最大 attempt 数。
    :return: 终态 workflow result；返回 None 表示允许外层继续下一轮。
    """

    # repair_plan 是 ask_human/toolchain/max_attempts 分支的决策事实来源。
    dict_repair_plan: dict[str, Any] = repair_artifacts.repair_plan  # workflow repair 决策对象

    # ask_human 且配置 stop_on_human 时进入 blocked_human。
    if dict_repair_plan.get("action") == "ask_human" and bool(context.config.get("stop_on_human", True)):

        # stop_on_human 触发时直接写出人工介入请求。
        return _block_for_repair_human(context, attempt, validation_artifacts, repair_artifacts)

    # toolchain_issue 不应继续消耗 attempt。
    if dict_repair_plan.get("primary_source") == "toolchain_issue":

        # 工具链问题保持 blocked_toolchain，等待外部环境修复。
        return _finish_attempt_with_status(context, attempt, "blocked_toolchain")

    # 达到最大尝试次数时标记 max_attempts。
    if not _has_attempt_budget(context, int_max_attempts):

        # attempt 预算耗尽后停止自动修复循环。
        return _finish_attempt_with_status(context, attempt, "max_attempts")

    # 否则本轮失败，允许外层 while 启动下一轮。
    _finish_attempt_with_status(context, attempt, "failed")

    # None 通知外层 while 继续下一轮 attempt。
    return None

# repair 人工介入 helper 写出 intervention 并结束 workflow。
def _block_for_repair_human(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    validation_artifacts: ValidationArtifacts,
    repair_artifacts: RepairArtifacts,
) -> dict[str, Any]:
    """
    写出 repair 阶段的人类介入请求并返回 result。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param validation_artifacts: validation 报告和 JSON 证据路径。
    :param repair_artifacts: repair prompt、repair plan 和 diagnosis 证据对象。
    :return: blocked_human 状态下已写盘的 workflow result。
    """

    # repair_plan 决定 intervention 的 primary_source 和问题描述。
    dict_repair_plan: dict[str, Any] = repair_artifacts.repair_plan  # 人工介入 repair 决策

    # intervention_path 固定在当前 attempt 目录。
    path_intervention = attempt.attempt_dir / "intervention.json"  # repair 阶段待回答请求文件

    # intervention 把无法自动决策的修复问题转换成用户可回答的结构化请求。
    dict_intervention: dict[str, Any] = build_intervention(  # repair 无法自动推进时交给用户补证据的 payload
        dict_repair_plan,  # 决定用户需补充哪些证据的计划
        repair_artifacts.report_text,  # 人类可读 validation 报告
        validation_artifacts.report.to_dict(),  # intervention 中的机读 validation 证据
    )

    # intervention 写盘供人工审查。
    write_json(path_intervention, dict_intervention)

    # result 保存人工介入 JSON 的 release-safe 路径。
    attempt.record["intervention_path"] = safe_path(path_intervention)  # result 暴露给人工处理的相对路径

    # result 进入 blocked_human。
    _finish_attempt_with_status(context, attempt, "blocked_human")

    # state 侧保存人工等待点，方便前端恢复 blocked_human。
    _record_human_intervention_state(context, attempt, path_intervention, dict_repair_plan.get("primary_source"))

    # human_intervention trace 记录人工介入来源和 provider。
    _trace_human_intervention(context, attempt, path_intervention, dict_repair_plan.get("primary_source"))

    # repair 人工介入分支返回 blocked_human 终态 result。
    return context.result

# human intervention state helper 记录人工等待点。
def _record_human_intervention_state(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    intervention_path: Path,
    primary_source: Any,
) -> None:
    """
    写入 human_intervention state。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param intervention_path: 人工介入 JSON 证据路径。
    :param primary_source: repair plan 判定的主要阻塞来源。
    :return: 无返回值，按需写入 human_intervention state。
    """

    # dict_state_payload 关联 intervention 路径和来源。
    dict_state_payload = {
        "output": intervention_path,  # state 指向待用户处理的 intervention 文件
        "attempt_id": attempt.attempt_id,  # state 中的人工介入 attempt
        "primary_source": primary_source,  # repair plan 判定的阻塞来源
    }  # 人工介入 state 事件字段集合

    # human_intervention state 只在启用状态文件时落盘。
    _record_state(context.state_path, "human_intervention", dict_state_payload, enabled=context.state_updates)

# human intervention trace helper 写入人工等待事件。
def _trace_human_intervention(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    intervention_path: Path,
    primary_source: Any,
) -> None:
    """
    写入 human_intervention trace。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param intervention_path: 人工介入 JSON 证据路径。
    :param primary_source: repair plan 判定的主要阻塞来源。
    :return: 无返回值，追加 human_intervention trace 事件。
    """

    # dict_trace_payload 记录人工介入来源。
    dict_trace_payload = {  # human_intervention 事件的序列化 trace payload
        "event": "human_intervention",  # 人工介入 trace 事件类型
        "attempt_id": attempt.attempt_id,  # 等待人工处理的 attempt 编号
        "output": intervention_path,  # 待回答的 intervention JSON
        "primary_source": primary_source,  # 人工介入来源
        "provider": context.config["provider"]["name"],  # 当前 provider 名称
    }

    # trace 侧记录人工等待点，供 resume 和 memory 构造读取。
    append_trace_event(context.trace_path, dict_trace_payload)

# attempt finish helper 同步 attempt record 和 workflow result 状态。
def _finish_attempt_with_status(
    context: WorkflowExecutionContext,
    attempt: AttemptContext,
    status: str,
) -> dict[str, Any]:
    """
    写入 attempt/result 状态并返回 result。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param attempt: 当前 attempt 上下文，提供 attempt 目录、编号和 result 记录。
    :param status: 需要写入 attempt 和 workflow result 的终态状态。
    :return: 状态同步并写盘后的 workflow result。
    """

    # attempt 和 result 状态必须同步。
    attempt.record["status"] = status  # 当前 attempt 状态

    # workflow result 记录最新终态。
    context.result["status"] = status  # 无 attempt 终态状态

    # attempt 状态变化后立即持久化 result。
    _write_result(context.result_path, context.result)

    # 返回 attempt 更新后的内存 result 对象。
    return context.result

# workflow finish helper 处理没有追加 attempt 的终态。
def _finish_with_status(context: WorkflowExecutionContext, status: str) -> dict[str, Any]:
    """
    写入 workflow 终态并返回 result。
    
    :param context: workflow 执行上下文，提供路径、计划、配置和持久化对象。
    :param status: 需要写入 attempt 和 workflow result 的终态状态。
    :return: 顶层 workflow 状态写盘后的 result。
    """

    # 终态只更新 result，不追加 attempt。
    context.result["status"] = status  # workflow 顶层状态

    # workflow 终态变化后立即持久化 result。
    _write_result(context.result_path, context.result)

    # 调用方继续持有同一个 result 引用，避免额外重读 JSON。
    return context.result
