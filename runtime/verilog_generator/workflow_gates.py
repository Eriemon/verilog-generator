"""工作流接口、语义和深度审查门禁。"""

# 启用延迟注解，避免 gate helper 之间出现导入顺序压力。
from __future__ import annotations

# 标准库导入用于处理 review artifact 路径和结构化结果。
from pathlib import Path
from typing import Any

# 本地模块导入保持 gate 层只负责验证、trace 和 JSON 证据。
from .trace import append_trace_event
from .verifier import verify_stage
from .workspace import write_json

# `_interface_gate` 比较 Python 合同和最终 RTL 接口合同。
def _interface_gate(
    plan: dict[str, Any],
    stage_outputs: dict[str, dict[str, Any]],
    final_output: dict[str, Any],
    attempt_dir: Path,
    trace_path: Path,
) -> dict[str, Any] | None:
    """运行 Python 到 RTL 的接口一致性门禁。

    参数:
        plan: 当前 attempt 使用的工作流计划。
        stage_outputs: 各阶段输出摘要，需包含 Python 合同。
        final_output: RTL 阶段最终输出摘要。
        attempt_dir: 当前 attempt 的工件目录。
        trace_path: 需要追加 gate trace 的文件路径。
    返回:
        dict[str, Any] | None: gate 证据路径和结果；合同缺失时返回 None。
    """

    # Python stage 输出提供参考接口合同。
    dict_python_contract = stage_outputs.get("python", {}).get("python_contract")  # Python 阶段接口合同

    # 最终 RTL stage 输出提供与 Python 合同对齐的 RTL 端口描述。
    dict_interface_contract = final_output.get("interface_contract")  # RTL 阶段待验证端口合同

    # 任一合同缺失时不执行 interface gate。
    if not dict_python_contract or not dict_interface_contract:

        # None 表示该门禁不适用当前 attempt。
        return None

    # verifier 返回标准 verify_stage 结果。
    dict_gate_result = verify_stage(plan, dict_python_contract, dict_interface_contract)  # 接口一致性验证结果

    # interface gate 结果写入当前 attempt 目录。
    path_gate_result = attempt_dir / "interface_gate.json"  # interface gate JSON 证据路径

    # 持久化结果后 trace 才能引用该路径。
    write_json(path_gate_result, dict_gate_result)

    # trace 事件记录接口合同来源和验证摘要。
    _append_verify_trace(
        trace_path,
        attempt_dir,
        output_path=path_gate_result,
        from_contract=stage_outputs.get("python", {}).get("contract_paths", {}).get("python_interface"),
        to_contract=final_output.get("contract_paths", {}),
        result=dict_gate_result,
    )

    # semantic gate wrapper 返回给执行层做后续合并。
    return {"path": path_gate_result, "result": dict_gate_result}

# `_semantic_gate` 比较 Python 参考合同和仿真语义指标。
def _semantic_gate(
    plan: dict[str, Any],
    validation_report: Any,
    stage_outputs: dict[str, dict[str, Any]],
    attempt_dir: Path,
    trace_path: Path,
) -> dict[str, Any] | None:
    """运行语义一致性门禁。

    参数:
        plan: 当前 attempt 使用的工作流计划。
        validation_report: 验证阶段返回的报告对象。
        stage_outputs: 各阶段输出摘要，需包含 Python 参考合同。
        attempt_dir: 当前 attempt 的工件目录。
        trace_path: 需要追加 gate trace 的文件路径。
    返回:
        dict[str, Any] | None: gate 证据路径和结果；缺少可比较指标时返回 None。
    """

    # reference_contract 来自 Python stage 的可执行参考合同。
    reference_contract = stage_outputs.get("python", {}).get("reference_contract")  # Python 参考语义合同

    # validation metrics 缺失时语义门禁不适用。
    if not reference_contract or not validation_report.metrics:

        # None 表示当前 attempt 没有可比较语义证据。
        return None

    # metrics 中的 semantic_execution 可能已经携带定位结果。
    dict_semantic_metrics = _semantic_metrics(validation_report)  # validation 报告中供旧报告兼容判断的语义指标

    # 缺少 mismatch 细节的 semantic_ready=False 走保护性通过兼容旧报告。
    dict_gate_result = _semantic_gate_result(plan, reference_contract, validation_report, dict_semantic_metrics)  # 语义门禁结果

    # 语义比较证据放在 attempt 根目录，便于 combine gate 直接引用。
    path_gate_result = attempt_dir / "semantic_gate.json"  # semantic 比较结果证据文件路径

    # 持久化结果后 trace 才能引用。
    write_json(path_gate_result, dict_gate_result)

    # trace 事件记录 reference contract 和语义验证摘要。
    _append_verify_trace(
        trace_path,
        attempt_dir,
        output_path=path_gate_result,
        from_contract=stage_outputs.get("python", {}).get("contract_paths", {}).get("reference_contract"),
        to_contract=path_gate_result,
        result=dict_gate_result,
    )

    # 返回路径和结果供执行层合并门禁。
    return {"path": path_gate_result, "result": dict_gate_result}

# `_semantic_metrics` 安全提取 validation report 中的语义指标。
def _semantic_metrics(validation_report: Any) -> dict[str, Any] | None:
    """从 validation report 中读取 semantic_execution 指标。

    参数:
        validation_report: 验证阶段返回且可能带 metrics 字段的对象。
    返回:
        dict[str, Any] | None: 结构化 semantic_execution 指标；缺失或类型不符时返回 None。
    """

    # metrics 必须是 dict 才能读取 semantic_execution。
    if not isinstance(validation_report.metrics, dict):

        # 非结构化 metrics 不能提供语义执行证据。
        return None

    # 只有 dict 指标才参与语义门禁特殊分支。
    if isinstance(validation_report.metrics.get("semantic_execution"), dict):

        # 返回指标对象供后续判断 ready/mismatch。
        return validation_report.metrics["semantic_execution"]

    # 缺少结构化语义指标时交给普通 verify_stage。
    return None

# `_semantic_gate_result` 生成语义门禁的标准结果。
def _semantic_gate_result(
    plan: dict[str, Any],
    reference_contract: dict[str, Any],
    validation_report: Any,
    semantic_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    """根据语义指标决定直接兼容结果或调用 verifier。

    参数:
        plan: 当前 attempt 使用的工作流计划。
        reference_contract: Python 阶段生成的语义参考合同。
        validation_report: 验证阶段报告，提供 metrics。
        semantic_metrics: 已提取的 semantic_execution 指标。
    返回:
        dict[str, Any]: verify_stage schema 兼容的语义门禁结果。
    """

    # 旧报告可能只给 semantic_ready=False 而没有具体 mismatch。
    if _semantic_false_without_details(semantic_metrics):

        # 构造兼容结果，避免把缺少定位证据误判为修复指令。
        return _semantic_compatibility_result(plan, semantic_metrics or {})

    # 普通路径由 verifier 比较参考合同和 validation metrics。
    return verify_stage(
        plan,
        reference_contract,
        {
            "metrics": validation_report.metrics,
            "case_ids": reference_contract.get("case_ids", []),
        },
    )

# `_semantic_false_without_details` 识别缺少 mismatch 细节的旧报告。
def _semantic_false_without_details(semantic_metrics: dict[str, Any] | None) -> bool:
    """判断 semantic_ready=False 是否缺少可定位细节。

    参数:
        semantic_metrics: validation metrics 中的 semantic_execution 字段。
    返回:
        bool: True 表示旧报告只有失败标记但没有 mismatch 定位证据。
    """

    # 非 dict 指标不能进入兼容分支。
    if not isinstance(semantic_metrics, dict):

        # False 表示继续走 verify_stage。
        return False

    # semantic_ready 只有明确为布尔 False 时才考虑兼容分支。
    semantic_ready_value = semantic_metrics.get("semantic_ready")  # 原始 semantic_ready 字段值

    # isinstance 排除 0、None 或字符串等非布尔占位值。
    bool_semantic_ready_false = isinstance(semantic_ready_value, bool) and not semantic_ready_value  # semantic_ready 明确失败标记

    # True、缺失或非布尔值都不触发旧报告兼容。
    if not bool_semantic_ready_false:

        # True 或缺失都不触发兼容结果。
        return False

    # 如果有任何定位细节，就交给 verifier 生成修复建议。
    return not any(
        semantic_metrics.get(key)
        for key in ("mismatched_cases", "checkpoint_drift", "failed_cases")
    )

# `_semantic_compatibility_result` 构造旧报告兼容结果。
def _semantic_compatibility_result(plan: dict[str, Any], semantic_metrics: dict[str, Any]) -> dict[str, Any]:
    """返回缺少 mismatch 细节时的语义门禁兼容结果。

    参数:
        plan: 当前 attempt 使用的工作流计划。
        semantic_metrics: 旧验证报告中的 semantic_execution 指标。
    返回:
        dict[str, Any]: 保持 verify_stage schema 的兼容通过结果。
    """

    # summary 保留从 Python reference 到当前验证指标的比较方向。
    dict_summary = {
        "from_target": "python_reference",  # 语义比较的参考来源
        "to_target": None,  # 旧报告缺少明确的目标合同
        "top": plan.get("name"),  # 当前设计顶层名称
    }  # 语义兼容结果摘要

    # 兼容结果保持 verify_stage schema，供 combine gate 复用。
    return {
        "version": 1,
        "target": plan.get("target"),
        "ready": True,
        "issues": [],
        "semantic_ready": False,
        "mismatched_cases": [],
        "checkpoint_drift": [],
        "failed_cases": [],
        "localization_confidence": semantic_metrics.get("localization_confidence"),
        "error_sources": [],
        "recommended_action": None,
        "summary": dict_summary,
    }

# `_append_verify_trace` 写入 verify_stage 类门禁 trace 事件。
def _append_verify_trace(
    trace_path: Path,
    attempt_dir: Path,
    **kwargs: Any,
) -> None:
    """记录接口或语义门禁的 trace 事件。

    参数:
        trace_path: 需要追加 trace 事件的文件路径。
        attempt_dir: 当前 attempt 的工件目录。
        **kwargs: gate 输出路径、来源合同、目标合同和结果对象。
    返回:
        None: 直接追加 trace 事件。
    """

    # 输出路径来自调用方的 gate JSON 证据。
    path_output = kwargs["output_path"]  # trace 中记录的 verify gate 证据文件路径

    # result 是已经持久化的 gate 结果。
    dict_verify_result = kwargs["result"]  # verify gate 结果对象

    # trace payload 字段保持旧事件 schema。
    dict_trace_payload = {
        "event": "verify_stage",  # trace 事件类型
        "attempt_id": attempt_dir.name,  # 当前 attempt 标识
        "from_contract": kwargs.get("from_contract"),  # 被比较的来源合同
        "to_contract": kwargs.get("to_contract"),  # 被比较的目标合同或报告路径
        "output": path_output,  # 本次 verify_stage gate 的 JSON 证据路径

        # ready 和修复摘要用于后续 attempt 决策。
        "ready": dict_verify_result.get("ready"),  # gate 是否通过
        "error_sources": dict_verify_result.get("error_sources", []),  # gate 错误来源
        "recommended_action": dict_verify_result.get("recommended_action"),  # gate 建议动作
        "issues": dict_verify_result.get("issues", []),  # gate 问题列表

        # 语义定位字段用于 verify-repair 诊断。
        "semantic_ready": dict_verify_result.get("semantic_ready"),  # 语义 ready 状态
        "mismatched_cases": dict_verify_result.get("mismatched_cases", []),  # 不匹配用例
        "checkpoint_drift": dict_verify_result.get("checkpoint_drift", []),  # 检查点漂移
        "localization_confidence": dict_verify_result.get("localization_confidence"),  # 定位置信度
    }  # verify_stage trace 事件负载

    # trace 写入统一使用 append_trace_event。
    append_trace_event(trace_path, dict_trace_payload)

# `_review_gate` 检查 deep_review stage 是否产出合格审查工件。
def _review_gate(
    stage_outputs: dict[str, dict[str, Any]],
    attempt_dir: Path,
    trace_path: Path,
) -> dict[str, Any] | None:
    """运行 deep_review 工件门禁。

    参数:
        stage_outputs: 各阶段输出摘要，需包含 review artifact_dir。
        attempt_dir: 当前 attempt 的工件目录。
        trace_path: 需要追加 review trace 的文件路径。
    返回:
        dict[str, Any] | None: review gate 证据路径和结果；无 review 输出时返回 None。
    """

    # review stage 缺失时该门禁不适用。
    dict_review_output = stage_outputs.get("review")  # 深度审查阶段输出

    # 没有 review stage 输出时跳过。
    if not dict_review_output:

        # None 表示当前 generation mode 不需要 review gate。
        return None

    # 非 Path 表示 review 输出不可扫描。
    if not isinstance(dict_review_output.get("artifact_dir"), Path):

        # None 表示缺少可用 review 工件目录。
        return None

    # artifact_dir 已通过 Path 类型检查，后续可用于 glob 扫描。
    path_artifact_dir = Path(dict_review_output["artifact_dir"])  # review 工件目录

    # review artifact gate 只读取 review 目录下的 plan_review 文件。
    dict_gate_result = _review_artifact_gate(path_artifact_dir)  # deep_review 工件检查结果

    # 深审覆盖证据放在 attempt 根目录，便于工作流摘要统一收集。
    path_gate_result = attempt_dir / "review_gate.json"  # deep_review 覆盖检查证据文件路径

    # review gate JSON 先落盘，trace 中才能引用稳定路径。
    write_json(path_gate_result, dict_gate_result)

    # review trace 记录深度审查覆盖结论。
    _append_review_trace(trace_path, attempt_dir, path_gate_result, dict_gate_result)

    # review gate wrapper 返回给执行层挂入 attempt。
    return {"path": path_gate_result, "result": dict_gate_result}

# `_append_review_trace` 写入 deep_review 门禁 trace 事件。
def _append_review_trace(
    trace_path: Path,
    attempt_dir: Path,
    output_path: Path,
    result: dict[str, Any],
) -> None:
    """记录 review gate 的 trace 事件。

    参数:
        trace_path: 需要追加 trace 事件的文件路径。
        attempt_dir: 当前 attempt 的工件目录。
        output_path: review gate JSON 证据路径。
        result: review gate 的结构化结果。
    返回:
        None: 直接追加 trace 事件。
    """

    # review trace payload 只包含 review gate 关心的摘要字段。
    dict_trace_payload = {
        "event": "review_gate",  # 深度审查 gate 事件类型
        "attempt_id": attempt_dir.name,  # 审查 gate 所属 attempt 标识
        "output": output_path,  # 深审覆盖结果 JSON 路径
        "ready": result.get("ready"),  # 深审工件覆盖是否完整
        "issues": result.get("issues", []),  # 深审缺口问题列表
        "error_sources": result.get("error_sources", []),  # 深审缺口来源集合
    }

    # review gate trace 事件写入追踪日志。
    append_trace_event(trace_path, dict_trace_payload)

# `_review_artifact_gate` 验证 deep_review 产物覆盖关键审查维度。
def _review_artifact_gate(review_root: Path) -> dict[str, Any]:
    """检查 deep_review plan_review.md 工件。

    参数:
        review_root: deep_review 阶段的工件根目录。
    返回:
        dict[str, Any]: review gate schema 结果，包含 ready、issues 和建议动作。
    """

    # deep_review 必须生成至少一个 plan_review.md 文件。
    list_review_files = sorted(review_root.glob("**/*plan_review.md"))  # review 工件文件列表

    # issue 列表累积所有 review 覆盖缺口。
    list_issues: list[dict[str, Any]] = []  # review gate 发现的问题

    # 没有 review 文件时直接登记错误。
    if not list_review_files:

        # 缺失 review 工件说明 deep_review stage 未完成职责。
        list_issues.append(_review_issue("deep_review required a plan review artifact, but none was generated."))

    # required_groups 定义 plan review 必须覆盖的审查主题。
    dict_required_groups = _review_required_groups()  # review 主题到关键词集合的映射

    # 逐个 review 文件检查占位内容和主题覆盖。
    _check_review_files(review_root, list_review_files, dict_required_groups, list_issues)

    # 没有错误级 issue 时 review gate 才能通过。
    bool_ready = not any(issue.get("severity") == "error" for issue in list_issues)  # 深审覆盖门禁是否通过

    # 返回结果保持 gate result schema。
    return {
        "version": 1,
        "ready": bool_ready,
        "issues": list_issues,
        "error_sources": sorted({str(issue.get("source")) for issue in list_issues if issue.get("source")}),
        "recommended_action": "regenerate_review" if not bool_ready else None,
    }

# `_review_required_groups` 定义 deep_review 必须覆盖的主题。
def _review_required_groups() -> dict[str, tuple[str, ...]]:
    """返回 review 主题和关键词映射。

    参数:
        无参数；主题集合由 gate 规则固定定义。
    返回:
        dict[str, tuple[str, ...]]: 审查主题到中英文关键词的映射。
    """

    # 每组关键词允许英文或中文命中。
    return {
        "interface": ("interface", "port", "接口", "端口"),
        "reset": ("reset", "复位"),
        "timing": ("timing", "pipeline", "时序", "流水"),
        "handshake_or_fsm": ("handshake", "ready", "valid", "fsm", "state", "握手", "状态机"),
        "width": ("width", "bit", "位宽"),
        "synthesis": ("synthesis", "synthesizable", "综合"),
        "testbench": ("testbench", "verification", "测试", "验证"),
        "risk": ("risk", "issue", "风险", "问题"),
    }

# `_check_review_files` 扫描 review 文件内容和主题覆盖。
def _check_review_files(
    review_root: Path,
    review_files: list[Path],
    required_groups: dict[str, tuple[str, ...]],
    issues: list[dict[str, Any]],
) -> None:
    """检查所有 review 文件并追加 issue。

    参数:
        review_root: deep_review 工件根目录。
        review_files: 待检查的 plan_review.md 文件列表。
        required_groups: 审查主题到关键词的映射。
        issues: 用于累计缺口问题的可变列表。
    返回:
        None: 直接向 issues 追加发现项。
    """

    # 每个 review 文件都必须有真实文本和主题覆盖。
    for path_review_file in review_files:

        # review 文本允许忽略编码错误，避免单个坏字符阻断扫描。
        str_review_text = path_review_file.read_text(encoding="utf-8", errors="ignore").strip()  # review 文件文本

        # 相对路径进入错误消息，避免泄露绝对路径。
        str_relative_path = path_review_file.relative_to(review_root).as_posix()  # review 文件相对路径

        # 空文本或占位 JSON 都不能代表真实 review。
        if not str_review_text or str_review_text in {"{}", "[]"}:

            # 占位 review 需要重新生成。
            issues.append(_review_issue(f"deep_review artifact {str_relative_path} is empty or placeholder JSON."))

            # 占位文件没有主题内容，后续覆盖扫描无法提供额外信息。
            continue

        # 缺失主题列表用于生成可执行的 regenerate 原因。
        list_missing_groups = _missing_review_groups(str_review_text, required_groups)  # review 缺失的主题组

        # 有缺失主题时登记一条错误。
        if list_missing_groups:

            # 错误消息列出缺失主题，供 prompt 修复。
            issues.append(
                _review_issue(
                    f"deep_review artifact {str_relative_path} is missing required section coverage: "
                    f"{', '.join(list_missing_groups)}."
                )
            )

# `_missing_review_groups` 判断单个 review 文本缺失哪些主题。
def _missing_review_groups(text: str, required_groups: dict[str, tuple[str, ...]]) -> list[str]:
    """返回 review 文本没有覆盖的主题组。

    参数:
        text: 单个 review 文件的文本内容。
        required_groups: 审查主题到关键词的映射。
    返回:
        list[str]: 未被文本覆盖的主题组名称。
    """

    # 比较统一使用小写文本。
    str_lowered_text = text.lower()  # review 文本的小写形式

    # missing 列表保存未命中任意关键词的主题组。
    list_missing_groups = [  # review 文本未覆盖的必需主题
        name  # 未命中的审查主题名称
        for name, tokens in required_groups.items()  # 遍历主题和关键词集合
        if not any(token in str_lowered_text for token in tokens)  # 任一关键词命中即视为覆盖
    ]

    # 返回缺失主题组，空列表表示覆盖完整。
    return list_missing_groups

# `_review_issue` 生成 review gate 的标准错误对象。
def _review_issue(message: str) -> dict[str, Any]:
    """构造 deep_review gate 错误。

    参数:
        message: 面向用户的 review 覆盖缺口说明。
    返回:
        dict[str, Any]: review gate 使用的标准 error issue 对象。
    """

    # testbench_issue 是旧 gate 结果使用的 source。
    return {
        "severity": "error",
        "source": "testbench_issue",
        "message": message,
    }

# `_combine_gate_results` 合并 interface 和 semantic gate 结果。
def _combine_gate_results(
    interface_gate: dict[str, Any] | None,
    semantic_gate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """合并接口和语义门禁结果。

    参数:
        interface_gate: interface gate wrapper 或 None。
        semantic_gate: semantic gate wrapper 或 None。
    返回:
        dict[str, Any] | None: 合并后的 gate result；两个 gate 都缺失时返回 None。
    """

    # 两个 gate 都不适用时返回 None。
    if interface_gate is None and semantic_gate is None:

        # None 告诉执行层没有可合并门禁。
        return None

    # result wrapper 可能包含 path/result，需要统一解包。
    dict_interface_result = _gate_result(interface_gate)  # interface gate 的解包结果

    # semantic gate wrapper 解包后提供语义定位字段。
    dict_semantic_result = _gate_result(semantic_gate)  # 后续语义摘要字段的输入结果

    # 合并 issues 和 error_sources，同时计算 ready。
    dict_combined = _combined_gate_core(dict_interface_result, dict_semantic_result)  # 合并后的门禁核心字段

    # 补充 semantic 相关定位字段。
    dict_combined.update(_semantic_summary_fields(dict_interface_result, dict_semantic_result))

    # 合并结果交给 attempt 汇总层决定是否继续下一轮生成。
    return dict_combined

# `_gate_result` 兼容 wrapper 或裸 result 两种输入。
def _gate_result(gate: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 gate wrapper 中取出 result。

    参数:
        gate: gate wrapper、裸 gate result 或 None。
    返回:
        dict[str, Any] | None: 解包后的 gate result；输入缺失时返回 None。
    """

    # gate 缺失时保持 None。
    if gate is None:

        # None 表示该 gate 不参与合并。
        return None

    # workflow gate wrapper 使用 result 字段承载真实结果。
    raw_gate_result = gate.get("result")  # gate wrapper 内的原始结果字段

    # result 是 dict 时优先使用它。
    if isinstance(raw_gate_result, dict):

        # 返回实际 gate result。
        return raw_gate_result

    # 兼容调用方直接传入裸 gate result 的情况。
    return gate

# `_combined_gate_core` 汇总 interface/semantic gate 的通过状态、问题和修复建议。
def _combined_gate_core(
    interface_result: dict[str, Any] | None,
    semantic_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并 gate result 的通用字段。

    参数:
        interface_result: 已解包的 interface gate result。
        semantic_result: 已解包的 semantic gate result。
    返回:
        dict[str, Any]: 合并后的 ready、issues、error_sources 和 recommended_action。
    """

    # issue 累积列表按 gate 顺序保留首次出现项。
    list_issues: list[dict[str, Any]] = []  # 合并后的 issue 列表

    # error source 累积列表保留导致重生成建议的唯一来源。
    list_error_sources: list[str] = []  # 合并后的错误来源列表

    # recommended_action 取第一个失败 gate 提供的建议。
    recommended_action = None  # 合并门禁的修复建议

    # bool_ready 初值为 True，任一 gate 失败则置 False。
    bool_ready = True  # 合并门禁是否通过

    # 顺序合并 interface 和 semantic 结果。
    for dict_gate_result in (interface_result, semantic_result):

        # 缺失的 interface/semantic 结果不影响另一个 gate 的合并。
        if not dict_gate_result:

            # 空结果没有 issue/source 可贡献，直接进入下一个 gate。
            continue

        # issue 和 source 需要稳定去重。
        _merge_gate_lists(dict_gate_result, list_issues, list_error_sources)

        # ready=False 时记录失败状态和建议动作。
        if not dict_gate_result.get("ready"):

            # 任一 gate 失败都会让合并结果失败。
            bool_ready = False  # 合并门禁失败状态

            # 第一个失败 gate 的建议动作优先。
            if recommended_action is None:

                # 保存首个失败门禁给出的修复动作。
                recommended_action = dict_gate_result.get("recommended_action")  # 首个失败 gate 的建议动作

    # 返回合并后的基础 schema。
    return {
        "version": 1,
        "ready": bool_ready,
        "issues": list_issues,
        "error_sources": list_error_sources,
        "recommended_action": recommended_action or "regenerate_current",
    }

# `_merge_gate_lists` 将单个 gate 的列表字段合并到累积对象。
def _merge_gate_lists(
    gate_result: dict[str, Any],
    issues: list[dict[str, Any]],
    error_sources: list[str],
) -> None:
    """合并 issues 和 error_sources。

    参数:
        gate_result: 单个 gate 的结果对象。
        issues: 累计 issue 的可变列表。
        error_sources: 累计 error source 的可变列表。
    返回:
        None: 直接修改 issues 和 error_sources。
    """

    # issue 对象按完整字典去重。
    for issue in gate_result.get("issues", []) or []:

        # 只有新 issue 才追加，避免两个 gate 写出重复对象。
        if issue not in issues:

            # issue 首次出现时加入合并列表。
            issues.append(issue)

    # source 字符串按值去重。
    for source in gate_result.get("error_sources", []) or []:

        # 只有新 source 才追加，维持诊断来源的首次发现顺序。
        if source not in error_sources:

            # source 首次出现时加入跨 gate 诊断来源集合。
            error_sources.append(source)

# `_semantic_summary_fields` 选择 semantic 优先的定位字段。
def _semantic_summary_fields(
    interface_result: dict[str, Any] | None,
    semantic_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """构造合并结果中的语义定位字段。

    参数:
        interface_result: 已解包的 interface gate result。
        semantic_result: 已解包的 semantic gate result。
    返回:
        dict[str, Any]: semantic_ready、mismatch、drift 和定位置信度字段。
    """

    # semantic_ready 优先来自 semantic gate，缺失时回退 interface gate。
    # semantic gate 提供 mismatch、drift 和 failed case 细节。
    return {
        "semantic_ready": _first_available_field(semantic_result, interface_result, "semantic_ready"),
        "mismatched_cases": _list_field(semantic_result, "mismatched_cases"),
        "checkpoint_drift": _list_field(semantic_result, "checkpoint_drift"),
        "failed_cases": _list_field(semantic_result, "failed_cases"),
        "localization_confidence": _value_field(semantic_result, "localization_confidence"),
    }

# `_first_available_field` 从两个结果中按顺序读取字段。
def _first_available_field(
    primary: dict[str, Any] | None,
    fallback: dict[str, Any] | None,
    key: str,
) -> Any:
    """读取第一个存在的字段值。

    参数:
        primary: 优先读取的 gate result。
        fallback: primary 缺少字段时使用的备选 gate result。
        key: 需要读取的字段名。
    返回:
        Any: 第一个存在的字段值；两侧都缺失时返回 None。
    """

    # primary 存在且包含 key 时优先返回。
    if primary is not None and key in primary:

        # 返回 semantic gate 字段值。
        return primary.get(key)

    # fallback 存在且包含 key 时作为备选。
    if fallback is not None and key in fallback:

        # 备选路径通常来自 interface gate 的语义摘要字段。
        return fallback.get(key)

    # 两边都没有该字段时返回 None。
    return None

# `_list_field` 从 gate result 中读取列表字段。
def _list_field(result: dict[str, Any] | None, key: str) -> list[Any]:
    """读取列表字段，缺失时返回空列表。

    参数:
        result: 可能包含列表字段的 gate result。
        key: 需要读取的列表字段名。
    返回:
        list[Any]: 原始列表字段；字段缺失或类型不符时返回空列表。
    """

    # result 缺失时没有列表内容。
    if result is None:

        # 空列表保持 result schema 稳定。
        return []

    # 只有 list 字段才原样返回。
    list_candidate_value = result.get(key)  # gate result 中的候选列表字段

    # 非列表字段按空列表处理。
    if not isinstance(list_candidate_value, list):

        # 空列表避免 downstream 处理非列表值。
        return []

    # 返回列表字段原值。
    return list_candidate_value

# `_value_field` 从 gate result 中读取普通字段。
def _value_field(result: dict[str, Any] | None, key: str) -> Any:
    """读取可选普通字段。

    参数:
        result: 可能包含目标字段的 gate result。
        key: 需要读取的字段名。
    返回:
        Any: 字段原值；result 缺失时返回 None。
    """

    # result 缺失时字段值也缺失。
    if result is None:

        # None 表示该字段不可用。
        return None

    # 返回字段原值。
    return result.get(key)
