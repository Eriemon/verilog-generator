"""工作流配置、状态写入和模型响应辅助函数。"""

# 启用延迟注解以便内部 helper 可以保持轻量类型声明。
from __future__ import annotations

# 标准库导入用于复制配置、读写 JSON 和构造运行路径。
import copy
import json
from pathlib import Path
from typing import Any

# 本地模块导入保持 workflow 支撑层的既有行为边界。
from .model_provider import GenerationContext
from .prompt import _manifest_for, _stage_manifest_for
from .requirements import validate_codegen_plan_payload
from .trace import append_trace_event, safe_path, spec_summary
from .workspace import (
    require_workspace_path,
    require_workspace_path_from,

    # state 和文件写入 helper 共同维护 workspace 路径边界。
    update_workflow_state,
    write_json,
    write_text,
)

# workflow 结果状态用于写结果前做兼容性保护。
WORKFLOW_STATUSES = (  # workflow_result.json 允许持久化的终态集合
    "passed",  # 所有必需 stage 和验证均已通过
    "failed",  # 已执行但未达到通过条件
    "blocked_human",  # 需要人工决策继续
    "blocked_toolchain",  # 外部工具链不可用或失败
    "max_attempts",  # 自动修复尝试次数耗尽
    "invalid_response",  # provider 响应无法解析为契约产物
)

# generation mode 只暴露稳定的 regular/deep_review 两类。
GENERATION_MODES = ("regular", "deep_review")  # CLI 和 resume 共同接受的生成模式

# stage 集合按目标语言和 generation mode 显式登记。
DEFAULT_STAGE_SETS = {  # 当前 RTL workflow 的默认 stage 编排
    "rtl": {  # Verilog RTL 目标的 stage 分组
        "regular": ["requirements", "codegen_plan", "python", "rtl"],  # 常规生成路径
        "deep_review": ["requirements", "codegen_plan", "python", "review", "rtl"],  # 深审生成路径
    },
}

# 最终 stage 用于执行层判断目标产物是否完成。
FINAL_STAGE = {"rtl": "rtl"}  # 每个 target 对应的最终产物 stage

# WorkflowError 暴露给 workflow façade 和执行层统一捕获。
class WorkflowError(ValueError):
    """当 workflow 配置、状态或内部产物不满足契约时抛出。"""

# `_workflow_config` 保持旧调用点的配置构造入口。
def _workflow_config(plan: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """根据计划和入口参数构造执行层配置。

    :param plan: 工作流计划映射。
    :param kwargs: 调用方传入的关键字参数。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # mode 需要先规范化，后续 stage 集合和预算都依赖它。
    str_generation_mode = require_generation_mode(str(kwargs.get("generation_mode") or "regular"))  # 规范化生成模式

    # provider 子配置保持旧 workflow_result/config.json schema。
    dict_provider_config = _provider_config_from_kwargs(kwargs)  # provider 名称和命令配置

    # target stage 集合由规范化后的 target 和 generation mode 决定。
    list_stages = _default_stages_for(str(plan["target"]), str_generation_mode)  # 当前目标的 stage 顺序

    # 返回对象字段保持旧配置文件 schema 兼容。
    return _assemble_workflow_config(
        plan,
        kwargs,
        str_generation_mode=str_generation_mode,
        dict_provider_config=dict_provider_config,
        list_stages=list_stages,
    )

# `_provider_config_from_kwargs` 为 config.json 构造 provider 名称和命令子对象。
def _provider_config_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """提取 provider 配置并保持 JSON 友好结构。

    :param kwargs: 调用方传入的关键字参数。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # provider_name 缺省沿用 manual，保证无模型命令时进入人工响应路径。
    str_provider_name = str(kwargs.get("provider_name", "manual"))  # workflow provider 的后端选择名称

    # provider 子结构会直接写入 config.json。
    return {
        "name": str_provider_name,
        "command": kwargs.get("provider_command"),
    }

# `_assemble_workflow_config` 写出 resume 可复用的配置对象。
def _assemble_workflow_config(
    plan: dict[str, Any],
    kwargs: dict[str, Any],
    *,
    str_generation_mode: str,
    dict_provider_config: dict[str, Any],
    list_stages: list[str],
) -> dict[str, Any]:
    """组装 workflow config 的持久化 schema。

    :param plan: 工作流计划映射。
    :param kwargs: 调用方传入的关键字参数。
    :param str_generation_mode: 已校验的生成模式名称。
    :param dict_provider_config: provider 子配置映射。
    :param list_stages: 本轮 workflow 使用的阶段列表。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # workflow 节点可能缺失，使用空字典保护旧 spec。
    dict_workflow = plan.get("workflow") if isinstance(plan.get("workflow"), dict) else {}  # spec 中的 workflow 配置

    # 需求和接口 profile 需要深拷贝，避免执行层修改输入计划。
    dict_design_requirements = _copy_dict(plan.get("design_requirements"))  # 设计需求快照

    # interface_profile 同样作为下游 prompt 上下文读取。
    dict_interface_profile = _copy_dict(plan.get("interface_profile"))  # 接口约束快照

    # external route 证据只在对象类型正确时进入配置。
    dict_external_codegen_plan = _copy_optional_dict(kwargs.get("external_codegen_plan"))  # 外部 codegen plan 证据

    # route_decision 记录入口路由来源，便于后续 trace 审计。
    dict_route_decision = _copy_optional_dict(kwargs.get("route_decision"))  # route-workflow 判定证据

    # budgets 与 stages 使用同一顺序，执行层按 stage 读取预算。
    dict_budgets = {stage: "normal" for stage in list_stages}  # 每个 stage 的默认 prompt 预算

    # 完整配置保持 v1 schema，供 resume 和 release 回归复用。
    return {
        "version": 1,
        "mode": str(dict_workflow.get("mode") or "generate"),
        "generation_mode": str_generation_mode,
        "name": plan["name"],
        "target": plan["target"],
        "rtl_dialect": plan.get("rtl_dialect"),
        "rtl_style_profile": plan.get("rtl_style_profile"),
        "design_requirements": dict_design_requirements,
        "streamability": plan.get("streamability"),
        "interface_family": plan.get("interface_family"),
        "interface_profile": dict_interface_profile,
        "pipeline_required": bool(plan.get("pipeline_required", True)),
        "codegen_plan_required": bool(plan.get("codegen_plan_required", True)),
        "codegen_plan_path": plan.get("codegen_plan_path"),
        "stages": list_stages,
        "readiness": str(kwargs.get("readiness", "static")),
        "max_attempts": int(kwargs.get("max_attempts", 3)),
        "stop_on_human": bool(kwargs.get("stop_on_human", True)),
        "run_external": bool(kwargs.get("run_external", True)),
        "comment_language": str(kwargs.get("comment_language", "zh")),
        "stream": bool(kwargs.get("stream")),
        "external_codegen_plan": dict_external_codegen_plan,
        "route_decision": dict_route_decision,
        "model_timeout_s": int(kwargs.get("model_timeout_s", 120)),
        "provider": dict_provider_config,
        "budgets": dict_budgets,
        "mock_behavior": dict_workflow.get("mock_behavior"),
    }

# `_copy_dict` 保护结构化输入不被执行层原地修改。
def _copy_dict(value: Any) -> dict[str, Any]:
    """复制 dict 输入，非 dict 时返回空对象。

    :param value: 需要复制或校验的输入值。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # 只有 dict 才能作为结构化 workflow 上下文。
    if isinstance(value, dict):

        # 深拷贝保护调用方传入的计划对象。
        return copy.deepcopy(value)

    # 非对象输入不能进入结构化配置。
    return {}

# `_copy_optional_dict` 处理可选路由证据对象。
def _copy_optional_dict(value: Any) -> dict[str, Any] | None:
    """复制可选 dict 输入，类型不匹配时返回 None。

    :param value: 需要复制或校验的输入值。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # 可选证据只有 dict 才能被持久化。
    if isinstance(value, dict):

        # 深拷贝避免执行层污染路由证据。
        return copy.deepcopy(value)

    # 缺失或非对象证据都按未提供处理。
    return None

# `_stage_manifest` 兼容空 stage 和指定 stage 两种 manifest。
def _stage_manifest(plan: dict[str, Any], stage: str) -> dict[str, Any]:
    """返回指定 stage 的产物 manifest。

    :param plan: 工作流计划映射。
    :param stage: 当前工作流阶段名称。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # 具体 stage 使用 stage 专属 manifest。
    if stage:

        # prompt 模块负责生成 manifest schema。
        return _stage_manifest_for(plan, stage)

    # 空 stage 使用完整计划 manifest。
    return _manifest_for(plan)

# `_stage_budget` 为 prompt 构造读取 stage 预算。
def _stage_budget(config: dict[str, Any], stage: str) -> str:
    """读取指定 stage 的 prompt 预算。

    :param config: 工作流配置映射。
    :param stage: 当前工作流阶段名称。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # budgets 字段可能来自旧配置，先做类型保护。
    dict_budgets = config.get("budgets", {})  # config.json 中的 stage 预算映射

    # 只有 dict budgets 且包含 stage 时才使用配置值。
    if isinstance(dict_budgets, dict) and stage in dict_budgets:

        # 预算值转为字符串，兼容 JSON 中的简单标量。
        return str(dict_budgets[stage])

    # 未配置时保持 normal 预算。
    return "normal"

# `require_generation_mode` 统一约束 generation mode 输入。
def require_generation_mode(value: str) -> str:
    """校验并规范化 generation mode。

    :param value: 需要复制或校验的输入值。
    :return: 通过校验后的规范化字符串。
    :raises WorkflowError: 当输入状态或文件内容不符合 workflow 契约时抛出。
    """

    # mode 比较统一使用小写。
    str_normalized_mode = value.lower()  # 小写后的 generation mode

    # 未知 mode 会改变 stage 编排，必须硬失败。
    if str_normalized_mode not in GENERATION_MODES:

        # 错误消息列出稳定可选值，供 CLI 直接展示。
        raise WorkflowError(f"> ERR: [Python] generation_mode must be one of {', '.join(GENERATION_MODES)}.")

    # 返回规范化 mode 给调用方写入配置。
    return str_normalized_mode

# `_default_stages_for` 是 stage 编排表的唯一读取入口。
def _default_stages_for(target: str, generation_mode: str) -> list[str]:
    """返回 target 与 generation mode 对应的 stage 列表。

    :param target: 工作流目标类型。
    :param generation_mode: 生成模式名称。
    :return: 符合工作流契约的列表。
    :raises WorkflowError: 当输入状态或文件内容不符合 workflow 契约时抛出。
    """

    # target 查表使用小写字符串。
    str_normalized_target = str(target).lower()  # stage 表使用的 target 键

    # generation mode 复用公开校验逻辑。
    str_normalized_mode = require_generation_mode(generation_mode)  # 默认 stage 查表使用的生成模式键

    # 当前 target 的所有 mode stage 集合。
    dict_stage_sets = DEFAULT_STAGE_SETS.get(str_normalized_target)  # target 对应的 stage 集合

    # 缺少 stage 集合表示当前 workflow 不支持该组合。
    if not dict_stage_sets or str_normalized_mode not in dict_stage_sets:

        # 错误中保留 target/mode，方便定位配置漂移。
        raise WorkflowError(
            f"> ERR: [Python] No stage set defined for target={str_normalized_target!r} "
            f"generation_mode={str_normalized_mode!r}."
        )

    # 返回副本，避免调用方修改全局默认值。
    return list(dict_stage_sets[str_normalized_mode])

# `_new_attempt_record` 生成 workflow_result 中的 attempt 初值。
def _new_attempt_record(attempt_id: str, stage: str, provider: str) -> dict[str, Any]:
    """构造一次 stage attempt 的初始记录。

    :param attempt_id: 工作流尝试标识。
    :param stage: 当前工作流阶段名称。
    :param provider: 模型提供器名称。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # attempt 记录 schema 由 workflow_result.json 消费。
    return {
        "attempt_id": attempt_id,
        "stage": stage,
        "prompt_path": None,
        "response_path": None,
        "artifact_dir": None,
        "validation_json": None,
        "contract_paths": {},
        "repair_plan": None,
        "status": "failed",
        "provider": provider,
    }

# `_generate_model_response` 隐藏 streaming 和 buffered provider 差异。
def _generate_model_response(
    *,
    provider: Any,
    prompt_text: str,
    context: GenerationContext,
    stage_dir: Path,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """调用 provider 并返回响应文本与流式统计。

    :param provider: 模型 provider 实例。
    :param prompt_text: 发送给 provider 的 prompt 文本。
    :param context: 模型生成上下文。
    :param stage_dir: 当前 stage 的证据目录。
    :param config: 工作流配置映射。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # 配置和 provider 能力共同决定是否真正走 streaming。
    bool_stream_requested = bool(config.get("stream", False))  # 用户配置是否请求流式输出

    # provider 缺少 supports_streaming 时按不支持处理。
    bool_stream_supported = bool(getattr(provider, "supports_streaming", False))  # provider 是否支持流式输出

    # 请求流式时保留 transcript，非流式不产生额外路径。
    transcript_path = _stream_transcript_path(stage_dir, context.stage, bool_stream_requested)  # 流式 transcript 路径

    # 旧 transcript 不能混入本轮 provider 输出。
    _remove_existing_transcript(transcript_path)

    # 支持流式时走 chunk 收集路径。
    if bool_stream_requested and bool_stream_supported:

        # 流式响应需要同时写 transcript 和合并文本。
        return _generate_streaming_response(provider, prompt_text, context, transcript_path)

    # 不支持流式或未请求时走普通 generate。
    return _generate_buffered_response(
        provider,
        prompt_text,
        context,
        transcript_path,
        bool_stream_requested,
        bool_stream_supported,
    )

# `_stream_transcript_path` 将 stream 开关映射为可选证据路径。
def _stream_transcript_path(stage_dir: Path, stage: str, stream_requested: bool) -> Path | None:
    """按流式开关计算 transcript 路径。

    :param stage_dir: 阶段工件目录。
    :param stage: 当前工作流阶段名称。
    :param stream_requested: 是否请求流式输出。
    :return: 解析后的路径；不可用时返回 None。
    """

    # 未请求流式时不创建 transcript 文件。
    if not stream_requested:

        # None 告诉调用方跳过 transcript 写入。
        return None

    # transcript 与 stage 目录绑定，便于审计单个 stage 输出。
    return stage_dir / f"{stage}_stream.txt"

# `_remove_existing_transcript` 防止跨轮次流式证据串联。
def _remove_existing_transcript(transcript_path: Path | None) -> None:
    """删除上一轮同名 transcript。

    :param transcript_path: 流式 transcript 路径。
    :return: 无业务返回值，直接写入或更新相关工件。
    """

    # 只有已有 transcript 文件才需要清理。
    if transcript_path is not None and transcript_path.exists():

        # 删除旧文件防止 chunk 内容串联。
        transcript_path.unlink()

# `_generate_streaming_response` 收集 chunk 并写 transcript。
def _generate_streaming_response(
    provider: Any,
    prompt_text: str,
    context: GenerationContext,
    transcript_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    """收集 provider 流式响应。

    :param provider: 模型提供器名称。
    :param prompt_text: prompt_text 参数。
    :param context: 内部 stage 上下文映射。
    :param transcript_path: 流式 transcript 路径。
    :return: 符合 workflow runtime 契约的映射或文本。
    :raises WorkflowError: 当输入状态或文件内容不符合 workflow 契约时抛出。
    """

    # 流式路径调用前必须已经准备 transcript 路径。
    if transcript_path is None:

        # 这是内部调用契约错误，直接抛出 workflow 异常。
        raise WorkflowError("> ERR: [Python] Streaming response requires a transcript path.")

    # chunk 列表用于还原完整响应文本。
    list_chunks: list[str] = []  # provider 流式返回的非空 chunk

    # chunk 计数进入 workflow trace 统计。
    int_chunk_count = 0  # 写入 transcript 的 chunk 数量

    # transcript 以追加模式写入本轮 chunk。
    with transcript_path.open("a", encoding="utf-8") as handle:

        # provider.generate_stream 是流式输出的唯一来源。
        for chunk in provider.generate_stream(prompt_text, context):

            # 空 chunk 不进入响应和统计。
            if not chunk:

                # 跳过空片段，避免 transcript 统计虚增。
                continue

            # 非空 chunk 先进入统计计数。
            int_chunk_count += 1  # transcript 已写入的非空 chunk 数

            # chunk 缓存在列表中用于恢复完整响应。
            list_chunks.append(chunk)

            # transcript 文件逐 chunk 记录 provider 输出。
            handle.write(chunk)

    # 合并 chunk 得到与 buffered 路径一致的响应文本。
    str_response_text = "".join(list_chunks)  # provider 完整响应文本

    # 返回文本和流式审计统计。
    return str_response_text, {
        "stream_requested": True,
        "stream_supported": True,
        "stream_used": True,
        "stream_chunk_count": int_chunk_count,
        "stream_transcript_path": safe_path(transcript_path),
    }

# `_generate_buffered_response` 处理非流式或流式降级路径。
def _generate_buffered_response(
    provider: Any,
    prompt_text: str,
    context: GenerationContext,
    transcript_path: Path | None,
    bool_stream_requested: bool,
    bool_stream_supported: bool,
) -> tuple[str, dict[str, Any]]:
    """调用普通 generate 并记录非流式统计。

    :param provider: 模型提供器名称。
    :param prompt_text: prompt_text 参数。
    :param context: 内部 stage 上下文映射。
    :param transcript_path: 流式 transcript 路径。
    :param bool_stream_requested: bool_stream_requested 参数。
    :param bool_stream_supported: bool_stream_supported 参数。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # 普通 provider 调用一次返回完整文本。
    str_response_text = provider.generate(prompt_text, context)  # provider 非流式响应文本

    # 请求过流式但 provider 不支持时仍写 transcript 供用户审计。
    if transcript_path is not None:

        # transcript 记录实际 buffered 响应。
        write_text(transcript_path, str_response_text)

    # 返回文本和降级后的流式统计。
    return str_response_text, {
        "stream_requested": bool_stream_requested,
        "stream_supported": bool_stream_supported,
        "stream_used": False,
        "stream_chunk_count": 1 if str_response_text else 0,
        "stream_transcript_path": safe_path(transcript_path) if transcript_path is not None else None,
    }

# `_write_result` 保护 workflow_result 的状态枚举。
def _write_result(path: Path, result: dict[str, Any]) -> None:
    """校验并写入 workflow_result.json。

    :param path: JSON 或报告文件路径。
    :param result: 待写出的结果映射。
    :return: 无业务返回值，直接写入或更新相关工件。
    :raises WorkflowError: 当输入状态或文件内容不符合 workflow 契约时抛出。
    """

    # 已有 attempts 的结果必须使用允许的状态值。
    if result.get("status") not in WORKFLOW_STATUSES and result.get("attempts"):

        # 状态漂移会破坏 release 证据判读，立即失败。
        raise WorkflowError(f"> ERR: [Python] Workflow status must be one of {', '.join(WORKFLOW_STATUSES)}.")

    # 通过 workspace helper 写入 JSON，保持路径约束一致。
    write_json(path, result)

# `_previous_stage` 支持执行层查找前序产物上下文。
def _previous_stage(stage: str, stages: list[str]) -> str | None:
    """返回当前 stage 的前一个 stage。

    :param stage: 当前工作流阶段名称。
    :param stages: 工作流阶段列表。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # stage 可能来自 resume 状态，先捕获不存在的情况。
    try:

        # index 用于从 stage 顺序表回溯前驱。
        int_stage_index = stages.index(stage)  # 当前 stage 在顺序表中的索引

    # stage 不在顺序表中时没有前驱。
    except ValueError:

        # 不存在的 stage 没有前驱。
        return None

    # 第一个 stage 没有前驱。
    if int_stage_index <= 0:

        # None 表示当前 stage 不依赖前序产物。
        return None

    # 返回顺序表中的前一个 stage。
    return stages[int_stage_index - 1]

# `_prompt_stats` 生成 trace 中的 prompt 规模统计。
def _prompt_stats(output: str, **kwargs: Any) -> dict[str, Any]:
    """整理 prompt 输出和上下文规模统计。

    :param output: 模型响应文本。
    :param kwargs: 调用方传入的关键字参数。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # 调用方保持关键字传参，当前函数集中转换 trace 字段。
    str_stage = str(kwargs.get("stage"))  # 提示词统计归属的工作流阶段名称

    # budget 进入 trace，便于定位 prompt 规模策略。
    str_budget = str(kwargs.get("budget"))  # prompt 使用的预算档位

    # context_manifest 描述注入 prompt 的结构化上下文。
    context_manifest = kwargs.get("context_manifest")  # 提示词注入上下文的结构化清单

    # context_dir 代表额外文件上下文目录。
    context_dir = kwargs.get("context_dir")  # prompt 上下文目录

    # manifest 中的 files 数量代表显式上下文产物数量。
    int_manifest_artifacts = _manifest_artifact_count(context_manifest)  # manifest 文件条目数量

    # context_dir 本身也算一个额外上下文来源。
    int_context_artifacts = int_manifest_artifacts + (1 if context_dir else 0)  # prompt 可见上下文产物总数

    # 统计结构进入 trace，供 prompt 预算和回归分析使用。
    return {
        "version": 1,
        "chars": len(output),
        "approx_tokens": max(1, len(output) // 4),

        # 上下文规模字段用于判断 prompt 是否携带足够证据。
        "context_artifacts": int_context_artifacts,
        "has_vector_contract": bool(kwargs.get("vector_contract")),
        "has_decision": bool(kwargs.get("decision")),

        # 调度字段用于把统计归属到具体 stage 和预算。
        "budget": str_budget,
        "subfunction": kwargs.get("subfunction"),
        "stage": str_stage,
    }

# `_manifest_artifact_count` 计算 manifest 贡献的上下文数量。
def _manifest_artifact_count(context_manifest: dict[str, Any] | None) -> int:
    """统计 manifest 中的文件条目数量。

    :param context_manifest: 上下文 manifest 映射。
    :return: 统计得到的整数数量。
    """

    # 非 dict manifest 不能提供文件列表。
    if not isinstance(context_manifest, dict):

        # 无 manifest 时按零上下文产物处理。
        return 0

    # files 必须是列表才参与统计。
    files = context_manifest.get("files", [])  # manifest 内的文件条目

    # 只有列表结构才具备可计数语义。
    if not isinstance(files, list):

        # 非列表 files 视作无结构化产物。
        return 0

    # 返回文件条目的数量，不校验每个条目的 schema。
    return len(files)

# `_read_json` 是 workflow 可选 JSON 输入的统一读取点。
def _read_json(path: Path | None) -> dict[str, Any]:
    """读取并校验 JSON object。

    :param path: JSON 或报告文件路径。
    :return: 符合 workflow runtime 契约的映射或文本。
    :raises WorkflowError: 当输入状态或文件内容不符合 workflow 契约时抛出。
    """

    # None 表示调用方没有提供可选 JSON。
    if path is None:

        # 可选 JSON 缺失时返回空对象。
        return {}

    # workspace helper 负责路径存在性和根目录约束。
    json_path = require_workspace_path(path, purpose="JSON path", must_exist=True)  # 已确认存在的 JSON 路径

    # JSON 解析错误需要转成 WorkflowError 以便 CLI 友好展示。
    try:

        # 解析 workflow 配置、证据或人工决策的 JSON 对象。
        dict_payload = json.loads(json_path.read_text(encoding="utf-8"))  # workflow 输入 JSON 对象

    # JSON 解析失败时转换成 workflow 统一异常。
    except json.JSONDecodeError as exc:

        # 异常消息保留原始解析位置。
        raise WorkflowError(f"> ERR: [Python] Invalid JSON in {json_path}: {exc}") from exc

    # workflow 配置和证据入口只接受对象。
    if not isinstance(dict_payload, dict):

        # 非对象 JSON 不能作为 workflow 配置。
        raise WorkflowError(f"> ERR: [Python] Expected JSON object in {json_path}.")

    # 返回结构化 JSON 对象。
    return dict_payload

# `_record_state` 为调用方集中穿透 state 更新开关。
def _record_state(state_path: Path, event: str, payload: dict[str, Any], *, enabled: bool) -> None:
    """按开关写 workflow state 事件。

    :param state_path: 状态文件路径。
    :param event: 状态事件名称。
    :param payload: 内部 stage payload 映射。
    :param enabled: 是否写入 workflow state 事件。
    :return: 无业务返回值，直接写入或更新相关工件。
    """

    # state 写入统一交给 workspace helper 处理。
    update_workflow_state(state_path, event, payload, enabled=enabled)

# `_resolve_external_codegen_plan` 加载 spec 引用的可选计划。
def _resolve_external_codegen_plan(spec: dict[str, Any], spec_file: Path) -> dict[str, Any] | None:
    """解析 spec 中引用的外部 codegen plan。

    :param spec: Verilog 规格映射。
    :param spec_file: 规格文件路径。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # codegen_plan_path 缺失时无需加载外部计划。
    raw_path = spec.get("codegen_plan_path")  # spec 中的外部 codegen plan 路径

    # 空路径表示没有可复用 codegen plan。
    if not raw_path:

        # None 告诉 workflow 继续内部 planning stage。
        return None

    # 相对路径以 spec 文件所在目录为基准解析。
    plan_path = require_workspace_path_from(  # spec 相对路径解析后的 codegen plan 文件
        spec_file,  # 外部计划路径的相对基准 spec 文件
        Path(str(raw_path)),  # spec 声明的原始 codegen plan 路径
        purpose="codegen plan path",  # workspace 路径校验用途标签
        must_exist=True,  # 外部 codegen plan 必须已存在
    )

    # 复用通用 JSON 读取和对象校验。
    dict_payload = _read_json(plan_path)  # 外部 codegen plan 内容

    # 外部计划可以未 ready，但 schema 必须可识别。
    validate_codegen_plan_payload(spec, dict_payload, require_ready=False)

    # 返回已校验的外部计划。
    return dict_payload

# `_run_internal_json_stage` 运行 requirements/codegen_plan 本地 stage。
def _run_internal_json_stage(**kwargs: Any) -> dict[str, Any]:
    """运行无需模型的内部 JSON stage。

    :param kwargs: 调用方传入的关键字参数。
    :return: 符合 workflow runtime 契约的映射或文本。
    :raises WorkflowError: 当输入状态或文件内容不符合 workflow 契约时抛出。
    """

    # 先解析 keyword-only 调用参数，保持旧调用接口兼容。
    dict_context = _internal_stage_context(kwargs)  # 内部 stage 运行上下文

    # prompt/response 路径固定落在 stage_dir 下。
    dict_paths = _internal_stage_paths(dict_context)  # 内部 stage 的 prompt 与 response 路径

    # manifest 必须只声明一个写入文件。
    list_files = _manifest_files(dict_context["manifest"])  # manifest 中声明的产物文件

    # 单文件契约确保内部 stage 不产生隐藏产物。
    if len(list_files) != 1:

        # manifest 漂移会破坏 extract 记录，直接失败。
        raise WorkflowError(
            f"> ERR: [Python] Internal stage {dict_context['stage']!r} expects exactly one manifest file."
        )

    # 解析产物路径并写入 payload。
    path_artifact = _write_internal_artifact(dict_context, list_files[0])  # 内部 JSON stage 产物路径

    # response 文本用 fenced JSON 同时包含 manifest 和 payload。
    str_response_text = _internal_stage_response_text(  # extractor 可重新读取的内部 stage 响应文本
        dict_context["manifest"],  # response 顶层 manifest 片段
        str(list_files[0]["path"]),  # fenced JSON 使用的产物相对路径
        dict_context["payload"],  # 写入 response 的内部 JSON payload
    )

    # prompt/response 和 state/trace 证据按旧 schema 写入。
    _write_internal_stage_evidence(dict_context, dict_paths, path_artifact, str_response_text)

    # 返回值保持 workflow_stage 期待的结构。
    return _internal_stage_output(dict_context, dict_paths, path_artifact)

# `_internal_stage_context` 将松散 kwargs 变成内部上下文字典。
def _internal_stage_context(kwargs: dict[str, Any]) -> dict[str, Any]:
    """从 kwargs 提取内部 stage 运行上下文。

    :param kwargs: 调用方传入的关键字参数。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # context 保留原始对象，避免 Path 和 payload 类型被过早字符串化。
    return {
        "attempt_id": str(kwargs["attempt_id"]),
        "plan": kwargs["plan"],
        "stage": str(kwargs["stage"]),
        "manifest": kwargs["manifest"],
        "stage_dir": kwargs["stage_dir"],
        "artifact_dir": kwargs["artifact_dir"],
        "trace_path": kwargs["trace_path"],
        "state_path": kwargs["state_path"],
        "state_updates": bool(kwargs["state_updates"]),
        "payload": kwargs["payload"],
        "payload_key": kwargs.get("payload_key"),
    }

# `_internal_stage_paths` 计算内部 stage 证据文件。
def _internal_stage_paths(context: dict[str, Any]) -> dict[str, Path]:
    """计算内部 stage 的 prompt 和 response 路径。

    :param context: 内部 stage 上下文映射。
    :return: 解析后的路径；不可用时返回 None。
    """

    # stage_dir 由 workflow_stage 创建，当前函数只拼接文件名。
    path_stage_dir: Path = context["stage_dir"]  # 当前 stage 的证据目录

    # stage 名进入文件名，方便按阶段查看证据。
    str_stage = str(context["stage"])  # 内部 stage 名称

    # 返回路径字典供后续写入和 summary 复用。
    return {
        "prompt_path": path_stage_dir / f"{str_stage}_prompt.md",
        "response_path": path_stage_dir / f"{str_stage}_response.md",
    }

# `_manifest_files` 过滤 manifest 中真实可写的文件项。
def _manifest_files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """返回 manifest 中具备 path 的文件条目。

    :param manifest: stage manifest 映射。
    :return: 符合工作流契约的列表。
    """

    # manifest files 可能来自模板，先按 list 防御。
    files = manifest.get("files", [])  # manifest 原始 files 字段

    # 非列表 files 不具备可迭代文件条目语义。
    if not isinstance(files, list):

        # 空列表触发单文件契约错误。
        return []

    # 只保留 dict 且带 path 的条目。
    return [entry for entry in files if isinstance(entry, dict) and entry.get("path")]

# `_write_internal_artifact` 按 manifest 相对路径写 JSON。
def _write_internal_artifact(context: dict[str, Any], manifest_file: dict[str, Any]) -> Path:
    """按 manifest 路径写入内部 JSON 产物。

    :param context: 内部 stage 上下文映射。
    :param manifest_file: manifest 中的单个文件条目。
    :return: 无业务返回值，直接写入或更新相关工件。
    """

    # manifest path 是相对 artifact_dir 的逻辑路径。
    str_artifact_rel_path = str(manifest_file["path"])  # manifest 声明的相对产物路径

    # Path.parts 可跨平台展开相对路径片段。
    path_artifact_dir: Path = context["artifact_dir"]  # stage 的产物根目录

    # artifact_path 是实际写入 JSON payload 的路径。
    path_artifact = path_artifact_dir / Path(*Path(str_artifact_rel_path).parts)  # 内部 stage 实际产物路径

    # 父目录可能由 manifest 子路径隐含创建。
    path_artifact.parent.mkdir(parents=True, exist_ok=True)

    # payload 是内部 stage 的确定性 JSON 内容。
    write_json(path_artifact, context["payload"])

    # 返回产物路径供 trace 和 summary 使用。
    return path_artifact

# `_write_internal_stage_evidence` 汇总写入内部 stage 证据。
def _write_internal_stage_evidence(
    context: dict[str, Any],
    paths: dict[str, Path],
    artifact_path: Path,
    str_response_text: str,
) -> None:
    """写入内部 stage 的 prompt、response、state 和 trace 证据。

    :param context: 内部 stage 上下文映射。
    :param paths: 内部 stage 路径映射。
    :param artifact_path: 内部 artifact 路径。
    :param str_response_text: str_response_text 参数。
    :return: 无业务返回值，直接写入或更新相关工件。
    """

    # prompt 内容说明该 stage 由本地规则合成，不调用模型。
    str_prompt_text = _internal_prompt_text(context["stage"])  # 内部 stage prompt 文本

    # prompt 和 response 必须先落盘，state/trace 才能引用路径。
    write_text(paths["prompt_path"], str_prompt_text)

    # response 保存 fenced JSON，供 extractor 语义保持一致。
    write_text(paths["response_path"], str_response_text)

    # state 记录 prompt 和 extract 两类事件。
    _record_internal_stage_state(context, paths, artifact_path)

    # trace 记录同样的 prompt/extract 审计事件。
    _record_internal_stage_trace(context, paths, artifact_path)

# `_internal_prompt_text` 说明内部 stage 没有调用模型。
def _internal_prompt_text(stage: str) -> str:
    """生成内部 stage 的固定 prompt 说明。

    :param stage: 当前工作流阶段名称。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # 文本保持英文是因为它写入可解析 workflow artifact。
    return (
        f"# Internal {stage} stage\n\n"
        "This stage is synthesized from confirmed inputs and local planning rules.\n"
    )

# `_record_internal_stage_state` 记录 resume 可用的 state 事件。
def _record_internal_stage_state(context: dict[str, Any], paths: dict[str, Path], artifact_path: Path) -> None:
    """记录内部 stage 的 workflow state 事件。

    :param context: 内部 stage 上下文映射。
    :param paths: 内部 stage 路径映射。
    :param artifact_path: 内部 artifact 路径。
    :return: 无业务返回值，直接写入或更新相关工件。
    """

    # state 更新开关来自 run_workflow/resume 参数。
    bool_state_updates = bool(context["state_updates"])  # 是否记录可 resume 的 workflow state

    # prompt state 指向内部 prompt 文件。
    _record_state(
        context["state_path"],
        "prompt",
        {"output": paths["prompt_path"], "stage": context["stage"], "budget": "internal"},
        enabled=bool_state_updates,
    )

    # extract state 指向内部 response 和产物目录。
    _record_state(
        context["state_path"],
        "extract",
        {
            "response": paths["response_path"],
            "out_dir": context["artifact_dir"],
            "written_files": [artifact_path],
        },
        enabled=bool_state_updates,
    )

# `_record_internal_stage_trace` 记录审计 trace 事件。
def _record_internal_stage_trace(context: dict[str, Any], paths: dict[str, Path], artifact_path: Path) -> None:
    """记录内部 stage 的 trace 事件。

    :param context: 内部 stage 上下文映射。
    :param paths: 内部 stage 路径映射。
    :param artifact_path: 内部 artifact 路径。
    :return: 无业务返回值，直接写入或更新相关工件。
    """

    # prompt trace 记录内部 stage 输入摘要。
    append_trace_event(
        context["trace_path"],
        {
            "event": "prompt",
            "attempt_id": context["attempt_id"],
            "target": context["plan"]["target"],
            "stage": context["stage"],
            "spec": spec_summary(context["plan"]),
            "output": paths["prompt_path"],
            "budget": "internal",
            "provider": "internal",
        },
    )

    # extract trace 记录内部 stage 写出的产物路径。
    append_trace_event(
        context["trace_path"],
        {
            "event": "extract",
            "attempt_id": context["attempt_id"],
            "response": paths["response_path"],
            "out_dir": context["artifact_dir"],
            "written_files": [safe_path(artifact_path)],
        },
    )

# `_internal_stage_output` 返回执行层消费的 stage 输出对象。
def _internal_stage_output(context: dict[str, Any], paths: dict[str, Path], artifact_path: Path) -> dict[str, Any]:
    """组装内部 stage 返回给执行层的结果。

    :param context: 内部 stage 上下文映射。
    :param paths: 内部 stage 路径映射。
    :param artifact_path: 内部 artifact 路径。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # summary 保存给 workflow_result.json 的可读路径。
    dict_summary = {
        "prompt_path": safe_path(paths["prompt_path"]),  # 内部 prompt 证据路径
        "response_path": safe_path(paths["response_path"]),  # 内部响应 markdown 的可读路径
        "artifact_dir": safe_path(context["artifact_dir"]),  # 内部 stage 产物目录
        "artifact_path": safe_path(artifact_path),  # 内部 JSON artifact 的可读路径
    }  # 内部 stage 的路径摘要

    # 返回主体保持旧 schema。
    dict_output = {
        "stage": context["stage"],  # 当前内部 stage 名称
        "prompt_path": paths["prompt_path"],  # prompt markdown 证据路径
        "response_path": paths["response_path"],  # 原始响应 markdown 路径对象
        "artifact_dir": context["artifact_dir"],  # stage 产物根目录
        "manifest": context["manifest"],  # extractor 使用的 manifest 内容
        "manifest_path": paths["response_path"],  # manifest 所在 response 文件
        "contract_paths": {},  # 内部 stage 暂不生成额外 contract 文件
        "summary": dict_summary,  # workflow_result 可读摘要
    }  # 内部 stage 结果对象

    # payload_key 允许 requirements/codegen_plan 写入专属字段。
    payload_key = context.get("payload_key")  # 额外 payload 字段名

    # 有 payload_key 时按调用方指定字段挂载。
    if payload_key:

        # 指定字段名保持 workflow_stage 的旧期望。
        dict_output[str(payload_key)] = context["payload"]  # 调用方命名的 payload 字段

    # 没有专属 payload_key 时回退到 stage 名。
    else:

        # 默认用 stage 名作为 payload 字段。
        dict_output[context["stage"]] = context["payload"]  # stage 名兜底 payload 字段

    # 返回完整内部 stage 输出。
    return dict_output

# `_internal_stage_response_text` 保持内部 stage 与 extractor 契约一致。
def _internal_stage_response_text(manifest: dict[str, Any], artifact_rel_path: str, payload: dict[str, Any]) -> str:
    """生成 extractor 可读取的内部 stage response 文本。

    :param manifest: stage manifest 映射。
    :param artifact_rel_path: artifact 相对路径。
    :param payload: 内部 stage payload 映射。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # response manifest 包含本地合成检查说明。
    dict_response_manifest = _internal_response_manifest(manifest)  # 写入 response 的 manifest 内容

    # fenced JSON 保持与模型响应解析路径一致。
    return (
        "```json\n"
        + json.dumps(dict_response_manifest, indent=2, ensure_ascii=False)
        + "\n```\n"
        + f"```json path={artifact_rel_path}\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n```\n"
    )

# `_internal_response_manifest` 给内部 response 附加固定 checks。
def _internal_response_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """构造内部 stage response 中的 manifest。

    :param manifest: stage manifest 映射。
    :return: 符合 workflow runtime 契约的映射或文本。
    """

    # stage 名用于英文 evidence 文本，不参与业务判断。
    str_stage = str(manifest.get("stage"))  # manifest 中用于 evidence 文本的 stage 名称

    # checks 字段模拟模型 response 中的审计段落。
    dict_checks = {
        "spec_coverage": [f"Internal {str_stage} stage synthesized from confirmed inputs."],  # spec 覆盖说明
        "verification_plan": ["No model generation was used for this planning stage."],  # 验证计划说明
        "execution_plan": ["This planning artifact is consumed by later generation stages."],  # 执行计划说明
        "implementation_assessment": ["The internal planning payload was generated locally."],  # 实现来源说明
        "reviewability_assessment": ["The planning payload is fully structured JSON."],  # 可审查性说明
        "assumptions": [],  # 内部 stage 不新增隐藏假设
        "known_limitations": [],  # 内部 stage 不新增已知限制
    }  # 内部 stage 的固定审计说明

    # 原 manifest 字段优先保留，再覆盖 checks。
    return {
        **manifest,
        "checks": dict_checks,
    }
