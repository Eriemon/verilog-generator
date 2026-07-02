"""分阶段提示生成的端到端工作流门面。"""
# 标准库导入提供路径和动态关键字参数类型。
from __future__ import annotations

# 标准库类型用于声明路径和动态关键字入口。
from pathlib import Path
from typing import Any

# 本包导入负责规格解析、路由、执行和工作区安全边界。
from .planning import decompose_spec

# 需求确认 gate 在生成前阻断不完整规格。
from .requirements import validate_requirement_confirmation

# spec helper 负责读写规范化计划。
from .spec import read_spec, write_spec

# execution helper 承接 attempt 循环和验证修复。
from .workflow_execution import _execute_workflow

# gate helper 保持旧模块可导入的兼容符号。
from .workflow_gates import _combine_gate_results, _interface_gate, _review_artifact_gate, _review_gate, _semantic_gate

# support helper 提供状态、配置和路径治理能力。
from .workflow_support import (
    # 常量导出保持旧调用方兼容。
    DEFAULT_STAGE_SETS,
    FINAL_STAGE,
    GENERATION_MODES,
    WORKFLOW_STATUSES,

    # 异常和 stage helper 支撑门面逻辑。
    WorkflowError,
    _default_stages_for,

    # JSON、state 和 codegen plan helper 管理运行证据。
    _read_json,
    _record_state,
    _resolve_external_codegen_plan,

    # 配置、result 和 mode helper 交给 execution 层消费。
    _workflow_config,
    _write_result,
    require_generation_mode,
)

# router helper 负责 spec-only 和 plan-seeded 入口分流。
from .workflow_router import route_verilog_entry

# workspace helper 约束所有读写路径。
from .workspace import require_workspace_path, require_write_path, write_json

# workflow 门面接受历史关键字参数，并把执行细节转交给拆分后的 helper。
def run_workflow(**kwargs: Any) -> dict[str, Any]:
    """执行或恢复分阶段 Spec2RTL 工作流。

    参数:
        kwargs: 旧 API 和 CLI 透传的 workflow 运行选项。

    返回:
        workflow_result.json 对应的运行结果字典。

    异常:
        WorkflowError: 新运行缺少必需路径，或恢复流程缺少人工决策。
    """

    # resume_dir 存在时沿用旧入口语义，直接进入恢复路径。
    obj_resume_dir: object = kwargs.get("resume_dir")  # 恢复运行目录参数

    # 恢复模式不要求新 spec/out_dir，避免误触发新运行校验。
    if obj_resume_dir is not None:

        # 恢复分支只透传原始关键字表，由恢复 helper 统一读取默认值。
        return _resume_workflow(**kwargs)

    # spec_path 和 out_dir 是新 workflow 的最小必需输入。
    obj_spec_path: object = kwargs.get("spec_path")  # 新运行规格文件参数

    # out_dir 决定新 workflow 的所有证据输出位置。
    obj_out_dir: object = kwargs.get("out_dir")  # 新运行输出目录参数

    # 新运行缺少任一必需路径时立即失败，保持旧错误语义。
    if obj_spec_path is None or obj_out_dir is None:

        # 缺少新运行路径时不能创建半成品 run 目录。
        raise WorkflowError("> ERR: [Python] New workflow runs require both spec_path and out_dir.")

    # new_run_context 汇总新运行需要的路径、计划、配置和初始结果。
    dict_new_run_context = _prepare_new_workflow_run(kwargs, obj_spec_path, obj_out_dir)  # 新运行执行上下文

    # 执行 helper 负责 attempt 循环、stage、validation、gate 和 repair。
    return _execute_workflow(**dict_new_run_context)

# 新 workflow 准备 helper 只做可重复的路径、计划和配置写盘。
def _prepare_new_workflow_run(kwargs: dict[str, Any], spec_path: Any, out_dir: Any) -> dict[str, Any]:
    """创建新 workflow 的初始上下文。

    参数:
        kwargs: 旧 API 和 CLI 透传的 workflow 运行选项。
        spec_path: 新运行规格文件路径。
        out_dir: 新运行输出目录路径。

    返回:
        可直接传给 _execute_workflow 的执行上下文字典。
    """

    # spec_file 限定在工作区内，防止读取不受控规格。
    spec_file = require_workspace_path(spec_path, purpose="spec path", must_exist=True)  # 工作区内规格路径

    # run_dir 是 workflow 所有证据文件的根目录。
    path_run_dir: Path = require_write_path(out_dir, purpose="workflow output directory")  # workflow 输出根目录

    # 输出目录先创建，后续写 plan/config/result 不再重复建目录。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # dict_paths 固定 run 内部所有治理证据文件名。
    dict_paths = _workflow_paths(path_run_dir)  # workflow 证据路径集合

    # raw_spec 是用户规格经 target 约束后的结构化输入。
    raw_spec = read_spec(spec_file, target=kwargs.get("target"))  # 规格解析结果

    # 需求确认 gate 在生成计划前阻断不完整输入。
    validate_requirement_confirmation(raw_spec)

    # external_codegen_plan 支持由规格旁路携带的已确认 codegen plan。
    obj_external_codegen_plan: object = _resolve_external_codegen_plan(raw_spec, spec_file)  # 外部 codegen plan 路径

    # route_decision 记录 spec-only 或 plan-seeded 路由结论。
    dict_route_decision: dict[str, Any] = route_verilog_entry(  # 入口路由摘要和证据
        request_summary="Run Verilog workflow.",  # route trace 中的人类可读请求摘要
        spec=spec_file,  # 路由分析读取的规格文件
        codegen_plan=obj_external_codegen_plan,  # 可选的外部 codegen plan
    )  # workflow 路由决策

    # evidence 用于把外部证据注入规格分解。
    evidence = _read_json(kwargs.get("evidence_path")) if kwargs.get("evidence_path") else None  # 规格分解证据

    # plan 是后续 stage、validation 和 gate 的共同事实来源。
    dict_plan: dict[str, Any] = decompose_spec(raw_spec, target=kwargs.get("target"), evidence=evidence)  # 规范化生成计划

    # plan 写盘后 result/config 只保存相对索引。
    write_spec(dict_paths["plan_path"], dict_plan)

    # dict_config 将 CLI/API 选项规范化为 execution helper 可消费的配置。
    dict_config = _build_workflow_config(kwargs, dict_plan, obj_external_codegen_plan, dict_route_decision)  # execution 使用的规范化配置

    # config 写盘是 resume 路径的配置事实来源。
    write_json(dict_paths["config_path"], dict_config)

    # dict_result 初始化 workflow_result.json 的 release-safe 顶层结构。
    dict_result = _initial_result(dict_plan, dict_route_decision)  # workflow 初始结果

    # 初始 result 写盘后 attempt 循环可以安全恢复。
    _write_result(dict_paths["result_path"], dict_result)

    # state 记录新运行启动证据，供治理 resume-check 使用。
    _record_state(
        dict_paths["state_path"],
        "run_workflow",
        {"out_dir": path_run_dir, "target": dict_plan["target"], "name": dict_plan["name"]},
        enabled=bool(kwargs.get("state_updates", True)),
    )

    # 执行上下文只包含 _execute_workflow 明确需要的关键字。
    return _execution_context(kwargs, path_run_dir, dict_plan, dict_config, dict_result, dict_paths)

# workflow 路径 helper 统一 run 目录内的固定文件名。
def _workflow_paths(run_dir: Path) -> dict[str, Path]:
    """生成 workflow run 内部固定路径。

    参数:
        run_dir: workflow 输出根目录。

    返回:
        trace、state、result、config 和 plan 的路径字典。
    """

    # 路径集合用于避免多个 helper 重复拼接文件名。
    return {
        "trace_path": run_dir / "trace.jsonl",
        "state_path": run_dir / "workflow-state.json",
        "result_path": run_dir / "workflow_result.json",
        "config_path": run_dir / "workflow_config.json",
        "plan_path": run_dir / "plan.json",
    }

# 配置 helper 保持旧 run_workflow 关键字参数的默认值。
def _build_workflow_config(
    kwargs: dict[str, Any],
    plan: dict[str, Any],
    external_codegen_plan: Any,
    route_decision: dict[str, Any],
) -> dict[str, Any]:
    """构造 workflow execution 配置。

    参数:
        kwargs: 旧 API 和 CLI 透传的 workflow 运行选项。
        plan: 已规范化的生成计划。
        external_codegen_plan: 可选外部 codegen plan 证据。
        route_decision: workflow 入口路由决策。

    返回:
        execution helper 可消费的规范化配置字典。
    """

    # dict_config 由 workflow_support 统一校验 generation mode 和 stage 集合。
    dict_config = _workflow_config(  # 执行层选择 stage、provider 和验证策略的规范化配置
        plan,  # 配置构造读取的目标语言和 stage 默认值
        provider_name=str(kwargs.get("provider_name", "manual")),  # 配置中的模型提供方名称
        provider_command=kwargs.get("provider_command"),  # 命令型提供方的可执行命令
        generation_mode=kwargs.get("generation_mode"),  # 配置中的生成模式覆盖值
        stream=kwargs.get("stream"),  # 配置中的 provider 流式输出开关
        readiness=str(kwargs.get("readiness", "static")),  # validation gates 选择静态或仿真 readiness 档位
        max_attempts=int(kwargs.get("max_attempts", 3)),  # 自动修复最大轮数
        stop_on_human=bool(kwargs.get("stop_on_human", True)),  # 人工介入是否立即停机
        run_external=bool(kwargs.get("run_external", True)),  # 是否运行外部验证工具
        comment_language=str(kwargs.get("comment_language", "zh")),  # 生成注释语言策略
        external_codegen_plan=external_codegen_plan,  # 外部 codegen plan 证据
        route_decision=route_decision,  # workflow 入口路由决策
        model_timeout_s=int(kwargs.get("model_timeout_s", 120)),  # 模型调用超时时间
    )

    # 返回配置供写盘和执行共用。
    return dict_config

# 初始 result helper 保持 workflow_result.json 顶层 schema。
def _initial_result(plan: dict[str, Any], route_decision: dict[str, Any]) -> dict[str, Any]:
    """构造 workflow 初始 result。

    参数:
        plan: 已规范化的生成计划。
        route_decision: workflow 入口路由决策。

    返回:
        workflow_result.json 的初始字典。
    """

    # 初始 result 在第一次 attempt 前写盘，避免失败时缺少 receipt。
    return {
        "version": 1,
        "name": plan["name"],
        "target": plan["target"],
        "status": "failed",
        "plan_path": "plan.json",
        "workflow_config": "workflow_config.json",
        "trace_path": "trace.jsonl",
        "route_decision": route_decision,
        "attempts": [],
    }

# execution context helper 把门面上下文压缩为执行 helper 的关键字。
def _execution_context(
    kwargs: dict[str, Any],
    run_dir: Path,
    plan: dict[str, Any],
    dict_config: dict[str, Any],
    dict_result: dict[str, Any],
    dict_paths: dict[str, Path],
) -> dict[str, Any]:
    """构造 _execute_workflow 的参数字典。

    参数:
        kwargs: 旧 API 和 CLI 透传的 workflow 运行选项。
        run_dir: workflow 输出根目录。
        plan: 已规范化的生成计划。
        dict_config: execution helper 可消费的配置字典。
        dict_result: 当前 workflow 结果字典。
        dict_paths: workflow 运行证据路径集合。

    返回:
        可直接展开给 _execute_workflow 的参数字典。
    """

    # 默认新运行不携带人工决策内容。
    dict_decision: dict[str, Any] | None = None  # 人工决策输入

    # 显式 decision_path 会把人工选择注入下一轮 prompt。
    if kwargs.get("decision_path"):

        # 人工决策 JSON 由 workspace helper 统一读取和校验。
        dict_decision = _read_json(kwargs.get("decision_path"))  # 已读取的人工决策输入

    # execution_context 严格匹配 _execute_workflow 的 keyword-only 契约。
    dict_execution_context = {  # 恢复执行层所需的 run 目录、计划、决策和状态路径集合
        "run_dir": run_dir,  # 执行层写入所有运行证据的目录
        "plan": plan,  # 执行层驱动 stage 的生成计划
        "config": dict_config,  # 执行层读取的规范化运行配置
        "result": dict_result,  # 执行层持续更新的结果对象
        "result_path": dict_paths["result_path"],  # 持久化最终 workflow_result.json 的证据路径
        "trace_path": dict_paths["trace_path"],  # 追加记录 stage 事件 trace.jsonl 的审计路径
        "state_path": dict_paths["state_path"],  # resume 读取 workflow-state.json 的检查点路径
        "decision": dict_decision,  # resume 人工决策内容
        "state_updates": bool(kwargs.get("state_updates", True)),  # 是否写 state 文件
    }  # _execute_workflow 参数集合

    # 返回执行上下文给门面入口调用。
    return dict_execution_context

# resume 门面读取已存在 run 目录，并允许覆盖少量运行选项。
def _resume_workflow(**kwargs: Any) -> dict[str, Any]:
    """恢复已存在的 workflow run。

    参数:
        kwargs: 旧 API 和 CLI 透传的恢复选项。

    返回:
        workflow_result.json 对应的运行结果字典。
    """

    # run_dir 必须指向已存在的 workflow 输出目录。
    path_run_dir: Path = require_workspace_path(  # 工作区内已有 workflow 恢复目录
        kwargs["resume_dir"],  # 调用方传入的 resume 目录
        purpose="workflow resume directory",  # workspace 错误消息用途
        must_exist=True,  # 恢复目录必须已经存在
    )

    # dict_paths 复用固定文件名，让 resume 严格读取原 run 的证据集合。
    dict_paths = _workflow_paths(path_run_dir)  # 恢复已有运行时使用的证据路径集合

    # 恢复前先确认 config/result/plan 已存在。
    _require_resume_files(dict_paths)

    # dict_config 是 resume 修改和执行的配置事实来源。
    dict_config = _read_json(dict_paths["config_path"])  # 已保存 workflow 配置

    # dict_result 保存已完成 attempt 和当前状态。
    dict_result = _read_json(dict_paths["result_path"])  # 已保存 workflow 结果

    # 已保存 target 需要恢复为 read_spec 接受的可选字符串。
    str_saved_target = str(dict_config.get("target") or None) or None  # 已保存或缺省的目标语言

    # plan 重新读取，保持 execution helper 接口不变。
    dict_plan: dict[str, Any] = read_spec(  # 恢复运行使用的生成计划
        dict_paths["plan_path"],  # 已保存 plan.json 路径
        target=str_saved_target,  # 已保存生成目标
    )  # 已保存生成计划

    # decision 在 blocked_human 恢复时必须存在。
    dict_decision: dict[str, Any] | None = _resume_decision(kwargs, dict_result)  # resume 人工决策

    # resume 覆盖项写回 config，供后续 attempt 使用。
    _update_resume_config(dict_config, kwargs)

    # 更新后的 config 写盘，保证再次 resume 可复现。
    write_json(dict_paths["config_path"], dict_config)

    # state 记录 resume 操作及 decision 路径。
    _record_state(
        dict_paths["state_path"],
        "resume_workflow",
        {"resume_dir": path_run_dir, "decision": kwargs.get("decision_path")},
        enabled=bool(kwargs.get("state_updates", True)),
    )

    # 执行 helper 继续 attempt 循环或处理人工决策。
    return _execute_workflow(
        run_dir=path_run_dir,
        plan=dict_plan,
        config=dict_config,
        result=dict_result,
        result_path=dict_paths["result_path"],
        trace_path=dict_paths["trace_path"],
        state_path=dict_paths["state_path"],
        decision=dict_decision,
        state_updates=bool(kwargs.get("state_updates", True)),
    )

# resume 文件 gate 防止在不完整 run 目录上继续执行。
def _require_resume_files(dict_paths: dict[str, Path]) -> None:
    """确认 resume 需要的文件存在。

    参数:
        dict_paths: workflow 运行证据路径集合。

    返回:
        无返回值。
    """

    # workflow_config.json 缺失时无法恢复 provider 和 stage 设置。
    require_workspace_path(dict_paths["config_path"], purpose="workflow config", must_exist=True)

    # workflow_result.json 缺失时无法恢复 attempt 状态。
    require_workspace_path(dict_paths["result_path"], purpose="workflow result", must_exist=True)

    # plan.json 缺失时无法继续生成或验证。
    require_workspace_path(dict_paths["plan_path"], purpose="workflow plan", must_exist=True)

    # trace/state 是可追加输出，使用 write path 约束即可。
    require_write_path(dict_paths["trace_path"], purpose="workflow trace")

    # workflow-state.json 可追加写入，用 write path 保护目录边界。
    require_write_path(dict_paths["state_path"], purpose="workflow state")

# resume decision helper 校验 blocked_human 恢复所需的人类决策。
def _resume_decision(kwargs: dict[str, Any], dict_result: dict[str, Any]) -> dict[str, Any] | None:
    """读取并校验 resume decision。

    参数:
        kwargs: 旧 API 和 CLI 透传的恢复选项。
        dict_result: 已保存 workflow 结果字典。

    返回:
        人工决策字典；未提供时返回 None。

    异常:
        WorkflowError: blocked_human 状态缺少 decision JSON 时抛出。
    """

    # blocked_human 恢复入口先以未提交人工回答作为初始状态。
    dict_decision: dict[str, Any] | None = None  # 待注入的恢复决策

    # decision_path 存在时才读取用户已经确认的恢复决策。
    if kwargs.get("decision_path"):

        # 恢复决策读取后会交给 execution helper 写入 prompt。
        dict_decision = _read_json(kwargs.get("decision_path"))  # 已读取的恢复决策

    # blocked_human 没有 decision 时不能继续自动执行。
    if dict_result.get("status") == "blocked_human" and dict_decision is None:

        # 缺少人工决策会导致模型继续猜测用户意图。
        raise WorkflowError("> ERR: [Python] Resuming a blocked_human workflow requires a decision JSON file.")

    # 返回 decision 供 execution helper 写入下一轮 prompt。
    return dict_decision

# resume config helper 应用命令行覆盖项。
def _update_resume_config(dict_config: dict[str, Any], kwargs: dict[str, Any]) -> None:
    """更新 resume 时允许覆盖的配置项。

    参数:
        dict_config: 已保存并准备更新的 workflow 配置。
        kwargs: 旧 API 和 CLI 透传的恢复选项。

    返回:
        无返回值。
    """

    # generation_mode 覆盖时必须同步 stage 集合。
    if kwargs.get("generation_mode") is not None:

        # 先校验 mode，再按 target 重建 stages。
        dict_config["generation_mode"] = require_generation_mode(kwargs["generation_mode"])  # resume 后的生成模式

        # stages 必须跟随 generation_mode 同步更新。
        dict_config["stages"] = _default_stages_for(  # 新 generation mode 对应的 stage 顺序
            str(dict_config.get("target") or "rtl"),  # 已保存或默认的目标语言
            str(dict_config["generation_mode"]),  # 已校验的生成模式
        )  # resume 后的 stage 顺序

    # stream 只有显式传入时覆盖历史配置。
    if kwargs.get("stream") is not None:

        # stream 会改变 provider 调用方式，但不改变 stage 顺序。
        dict_config["stream"] = bool(kwargs["stream"])  # resume 后的流式输出开关

    # 这些覆盖项每次 resume 都按调用方输入或旧默认写回。
    dict_config["stop_on_human"] = bool(kwargs.get("stop_on_human", True))  # 人工介入停机策略

    # run_external 决定 resume 后是否继续外部验证。
    dict_config["run_external"] = bool(kwargs.get("run_external", True))  # 外部验证开关

    # comment_language 维持旧配置，除非调用方显式覆盖。
    dict_config["comment_language"] = kwargs.get("comment_language") or dict_config.get("comment_language", "zh")  # 注释语言策略

    # model_timeout_s 使用新传入值或保存的旧值。
    dict_config["model_timeout_s"] = int(kwargs.get("model_timeout_s") or dict_config.get("model_timeout_s", 120))  # 模型超时秒数
