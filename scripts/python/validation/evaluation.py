"""基于 trace 事件计算工作流评估指标。"""

# 延迟解析类型注解，避免运行时引入额外依赖
from __future__ import annotations

# 指标报告写盘使用标准 JSON 编码
import json

# trace 文件路径使用 pathlib 表达
from pathlib import Path

# 事件载荷字段类型随 trace 版本变化
from typing import Any

# 复用 trace 模块的读取逻辑
from scripts.python.workflow.trace import read_trace

# trace 文件评估入口
def evaluate_trace(trace_path: Path) -> dict[str, Any]:
    """
    读取 trace 文件并计算评估指标。

    :param trace_path: trace JSONL 文件路径。
    :return: 面向报告和测试的指标字典。
    """

    # 读取 trace 文件中的事件序列
    list_events = read_trace(trace_path)  # trace 事件列表

    # 返回内存事件评估结果
    return evaluate_events(list_events)

# 事件列表评估入口
def evaluate_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    统计 trace 事件中的验证、干预、语义执行和性能指标。

    :param events: trace 事件字典列表。
    :return: 汇总后的工作流评估指标。
    """

    # 初始化所有跨事件累积的计数容器
    dict_state = _initial_metric_state()  # 评估过程的可变统计状态

    # 缓存 validate 事件，后续 readiness 汇总会复用
    list_validate_events = [dict_event for dict_event in events if dict_event.get("event") == "validate"]  # 用于 readiness 汇总的验证事件

    # 将 validate 事件放入状态，便于最终报告统一读取
    dict_state["list_validate_events"] = list_validate_events  # 验证事件缓存

    # 单次遍历收集每类事件贡献的统计量
    for dict_event in events:

        # 将当前事件分发给各个统计采集器
        _collect_event_metrics(dict_event, dict_state)

    # readiness 通过 validate 事件单独汇总通过率
    _collect_readiness_metrics(list_validate_events, dict_state)

    # 根据累计状态构造对外稳定指标结构
    dict_metrics = _build_metric_report(events, dict_state)  # 最终评估指标

    # 返回评估结果给调用方或写盘函数
    return dict_metrics

# 评估指标写盘入口
def write_eval_metrics(trace_path: Path, out_path: Path) -> dict[str, Any]:
    """
    计算 trace 指标并写入 JSON 文件。

    :param trace_path: trace JSONL 文件路径。
    :param out_path: 指标 JSON 输出路径。
    :return: 已写入的指标字典。
    """

    # 先计算指标，避免创建空输出文件后才失败
    dict_metrics = evaluate_trace(trace_path)  # trace 指标载荷

    # 确保输出目录存在，支持调用方传入新的报告路径
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 将完整指标写入文件，终端输出由上层脚本负责
    out_path.write_text(json.dumps(dict_metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 返回指标，方便测试直接断言字段
    return dict_metrics

# 统计状态初始化
def _initial_metric_state() -> dict[str, Any]:
    """
    创建 evaluate_events 使用的可变统计状态。

    :param: 无外部业务参数；函数只创建一份新的统计容器。
    :return: 包含计数器、列表和集合的状态字典。
    """

    # 使用显式 key 保存状态，避免长函数维护大量散落局部变量
    dict_state = {  # 统计尝试次数验证通过率质量违规和语义执行状态的可变容器
        "set_attempt_ids": set(),  # 出现过的非空 attempt_id
        "list_validate_events": [],  # validate 事件缓存
        "dict_readiness_counts": {},  # readiness 到通过/失败计数
        "dict_error_sources": {},  # 错误来源名称到出现次数
        "dict_subfunction_failures": {},  # 子阶段失败热点
        "int_human_interventions": 0,  # 人工干预次数
        "int_qor_violation_count": 0,  # QoR 违规条目数
        "int_performance_events": 0,  # 性能或 QoR 相关事件数
        "int_performance_passes": 0,  # 性能相关事件通过数
        "int_resolved_interventions": 0,  # 已解决干预数
        "int_unresolved_interventions": 0,  # 尚未解决干预数
        "dict_prompt_tokens_by_budget": {},  # 预算类型到提示词耗用序列
        "int_verify_stage_events": 0,  # verify_stage 事件数量
        "int_semantic_events": 0,  # 语义验证事件数量
        "int_semantic_passes": 0,  # 语义验证通过数量
        "list_failed_case_counts": [],  # 每次语义验证失败 case 数
        "int_localization_hits": 0,  # 定位成功次数
        "int_localization_attempts": 0,  # 定位尝试次数
        "int_auto_debug_before_human": 0,  # 升级人工前自动调试次数
        "int_human_escalations": 0,  # ask_human 升级次数
        "list_workflow_statuses": [],  # workflow_attempt 状态序列
        "int_checkpoint_drift_events": 0,  # 检查点漂移事件累计量
        "int_toolchain_fallback_count": 0,  # 缺失首选后端数量
    }  # 评估状态字典

    # 返回新的独立状态，避免跨调用共享
    return dict_state

# 单事件统计分发
def _collect_event_metrics(dict_event: dict[str, Any], dict_state: dict[str, Any]) -> None:
    """
    将一个 trace 事件贡献到各类统计状态中。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新 dict_state。
    """

    # 记录非空 attempt_id，后续用于估算尝试次数
    str_attempt_id = str(dict_event.get("attempt_id") or "")  # 当前事件尝试标识

    # 非空 attempt_id 才进入去重集合
    if str_attempt_id:

        # 保存该尝试编号供最终 attempts 指标使用
        dict_state["set_attempt_ids"].add(str_attempt_id)

    # 分别更新各类指标，保持每个 helper 职责单一
    _collect_error_source_metrics(dict_event, dict_state)

    # 人工干预和工作流状态来自 action/status 字段
    _collect_intervention_metrics(dict_event, dict_state)

    # verify_stage 负责定位尝试和语义准备状态的前置证据
    _collect_verify_stage_metrics(dict_event, dict_state)

    # 失败热点和性能指标需要读取 ok/issues/metrics
    _collect_failure_and_performance_metrics(dict_event, dict_state)

    # token 预算指标独立收集，避免污染验证逻辑
    _collect_prompt_budget_metrics(dict_event, dict_state)

    # 语义执行和定位指标来自 diagnosis/semantic_ready/metrics
    _collect_semantic_metrics(dict_event, dict_state)

# error_sources 统计
def _collect_error_source_metrics(dict_event: dict[str, Any], dict_state: dict[str, Any]) -> None:
    """
    汇总事件中的 error_sources 字段。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新错误来源计数。
    """

    # error_sources 可能缺失，统一折算为空列表
    list_error_sources = dict_event.get("error_sources", []) or []  # 当前事件错误来源列表

    # 遍历错误来源并累计分布
    for str_source in list_error_sources:

        # 将来源转成字符串，保证报告 key 稳定
        str_source_name = str(str_source)  # 错误来源名称

        # 记录该错误来源出现次数
        _increment_counter(dict_state["dict_error_sources"], str_source_name)

        # 特定来源表示需要人工干预
        if str_source_name == "needs_human_intervention":

            # 计入人工干预总次数
            dict_state["int_human_interventions"] += 1  # 需要人工介入的错误来源计数

# 人工干预和状态统计
def _collect_intervention_metrics(dict_event: dict[str, Any], dict_state: dict[str, Any]) -> None:
    """
    汇总人工介入、干预解决和工作流尝试状态。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新干预相关计数。
    """

    # ask_human 动作表示当前流程升级给用户
    if dict_event.get("action") == "ask_human":

        # 人工干预、未解决干预和升级次数同步增加
        dict_state["int_human_interventions"] += 1  # ask_human 触发的人工干预次数

        # 当前 ask_human 尚未被 resolve 前视为未解决
        dict_state["int_unresolved_interventions"] += 1  # 等待 resolve 的干预数量

        # 记录一次面向用户的升级
        dict_state["int_human_escalations"] += 1  # 升级到用户决策的次数

        # 统计升级前是否已有自动调试尝试
        if dict_event.get("auto_debug_before_human"):

            # 自动调试先于人工升级发生
            dict_state["int_auto_debug_before_human"] += 1  # 人工升级前已自动调试的次数

    # resolve_intervention 或 resolved 状态都视为干预已闭环
    if dict_event.get("event") == "resolve_intervention" or dict_event.get("status") == "resolved":

        # 累计已解决干预数量
        dict_state["int_resolved_interventions"] += 1  # 已闭环干预数量

    # workflow_attempt 状态保留顺序，最终取最后一个
    if dict_event.get("event") == "workflow_attempt" and isinstance(dict_event.get("status"), str):

        # 保存 workflow_attempt 的状态文本
        dict_state["list_workflow_statuses"].append(str(dict_event["status"]))  # workflow 尝试状态序列

# 验证阶段定位信号采集
def _collect_verify_stage_metrics(dict_event: dict[str, Any], dict_state: dict[str, Any]) -> None:
    """
    汇总 verify_stage 事件中的定位尝试信息。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新 verify_stage 相关计数。
    """

    # 非 verify_stage 事件不参与本段指标
    if dict_event.get("event") != "verify_stage":

        # 当前事件不属于 verify_stage，直接返回
        return

    # 记录一次验证阶段事件
    dict_state["int_verify_stage_events"] += 1  # 验证阶段事件总数

    # 只有 ready 的验证阶段才检查语义定位信号
    if bool(dict_event.get("ready")):

        # semantic_ready 为 False 表示需要定位语义失败
        bool_semantic_ready = dict_event.get("semantic_ready")  # 验证阶段语义就绪标记

        # 明确语义未就绪时计入定位尝试
        if bool_semantic_ready is not None and not bool_semantic_ready:

            # 记录一次语义定位尝试
            dict_state["int_localization_attempts"] += 1  # verify_stage 发现的定位尝试

        # issues 中的 insufficient_debug 也表示定位证据不足
        for dict_issue in dict_event.get("issues", []) or []:

            # 只统计结构化 issue 中的 insufficient_debug 来源
            if isinstance(dict_issue, dict) and dict_issue.get("source") == "insufficient_debug":

                # 记录一次调试定位不足标记
                dict_state["int_localization_attempts"] += 1  # insufficient_debug 触发的定位尝试

# 失败热点和性能统计
def _collect_failure_and_performance_metrics(dict_event: dict[str, Any], dict_state: dict[str, Any]) -> None:
    """
    汇总失败热点、QoR 违规和性能相关通过率。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新失败和性能统计。
    """

    # 事件 issues 可能缺失，统一使用空列表
    list_issues = dict_event.get("issues", []) or []  # 当前事件问题列表

    # 识别问题消息中包含 QoR violation 的条目
    list_qor_violations = [  # QoR 文本命中的结构化问题
        dict_issue  # 保留原始 issue 供数量统计
        for dict_issue in list_issues  # 遍历当前事件的全部问题
        if isinstance(dict_issue, dict) and "qor violation" in str(dict_issue.get("message", "")).lower()  # 命中 QoR 违规消息
    ]  # QoR 违规问题列表

    # 累加 QoR 违规数量
    dict_state["int_qor_violation_count"] += len(list_qor_violations)  # QoR 违规累计数量

    # ok=False 或存在 error_sources 时计入子阶段失败热点
    if (dict_event.get("ok") is not None and not dict_event.get("ok")) or dict_event.get("error_sources"):

        # 失败热点按 subfunction、stage、event 的优先级归类
        if dict_event.get("subfunction"):

            # 显式子功能名称最适合作为失败热点键
            str_subfunction = str(dict_event.get("subfunction"))  # subfunction 级失败热点键

        # 没有 subfunction 时尝试用 stage 定位失败来源
        elif dict_event.get("stage"):

            # 没有 subfunction 时使用阶段名保留定位粒度
            str_subfunction = str(dict_event.get("stage"))  # 阶段名归因的失败热点键

        # subfunction 和 stage 都缺失时退回 event 字段
        elif dict_event.get("event"):

            # 阶段名也缺失时退回事件类型
            str_subfunction = str(dict_event.get("event"))  # 事件类型归因的失败热点键

        # 三类归因字段都不存在时使用固定占位
        else:

            # 完全缺少归因字段时使用稳定兜底键
            str_subfunction = "unknown"  # 未知来源失败热点键

        # 记录该热点出现次数
        _increment_counter(dict_state["dict_subfunction_failures"], str_subfunction)

    # metrics 或性能/QoR 文本表示该事件参与性能统计
    bool_has_performance_issue = any(  # issues 是否携带性能或 QoR 文本
        "performance" in str(dict_issue).lower() or "qor" in str(dict_issue).lower()  # 单条 issue 的性能文本判断
        for dict_issue in list_issues  # 遍历当前事件问题列表
    )  # issues 中出现性能或 QoR 文本

    # metrics 存在或 issues 命中性能文本时参与性能统计
    bool_has_performance_signal = bool(dict_event.get("metrics") or bool_has_performance_issue)  # 是否纳入性能统计

    # 性能信号存在时更新性能事件计数
    if bool_has_performance_signal:

        # 记录一次性能相关事件
        dict_state["int_performance_events"] += 1  # 性能相关事件总数

        # 没有 QoR 违规且 ok 未失败时视为性能通过
        if not list_qor_violations and not (dict_event.get("ok") is not None and not dict_event.get("ok")):

            # 累计性能通过事件
            dict_state["int_performance_passes"] += 1  # 未出现 QoR 违规的性能事件数量

# prompt token 预算统计
def _collect_prompt_budget_metrics(dict_event: dict[str, Any], dict_state: dict[str, Any]) -> None:
    """
    汇总 prompt_stats 中不同 budget 的 token 数。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新 token 预算映射。
    """

    # prompt_stats 必须是字典才参与统计
    dict_prompt_stats = dict_event.get("prompt_stats") if isinstance(dict_event.get("prompt_stats"), dict) else {}  # prompt 统计字段

    # 缺失 prompt_stats 时无需处理
    if not dict_prompt_stats:

        # 当前事件没有 prompt token 信息
        return

    # budget 来自 prompt_stats，缺失时回退事件级 budget
    str_budget = str(dict_prompt_stats.get("budget") or dict_event.get("budget") or "unknown")  # prompt token 聚合预算来源

    # approx_tokens 是本段唯一需要聚合的数值
    int_tokens = dict_prompt_stats.get("approx_tokens")  # prompt_stats 中的近似 token 数

    # 只接受数值 token，避免字符串污染均值计算
    if isinstance(int_tokens, (int, float)):

        # 将 token 数追加到对应 budget 分桶
        dict_state["dict_prompt_tokens_by_budget"].setdefault(str_budget, []).append(int(int_tokens))  # budget 分桶 token 列表

# 语义执行统计
def _collect_semantic_metrics(dict_event: dict[str, Any], dict_state: dict[str, Any]) -> None:
    """
    汇总 diagnosis、semantic_ready 和 semantic_execution 指标。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新语义相关计数。
    """

    # diagnosis 为结构化字典时参与定位命中统计
    dict_diagnosis = dict_event.get("diagnosis") if isinstance(dict_event.get("diagnosis"), dict) else {}  # 诊断结构体

    # 诊断存在表示一次定位尝试
    if dict_diagnosis:

        # 记录诊断定位尝试
        dict_state["int_localization_attempts"] += 1  # diagnosis 产生的定位尝试

        # localization_hit 表示定位命中
        if dict_diagnosis.get("localization_hit"):

            # 记录一次定位命中
            dict_state["int_localization_hits"] += 1  # diagnosis 标记的定位命中

    # 提取 semantic_execution 子指标
    dict_metrics = dict_event.get("metrics") or {}  # 当前事件 metrics 字段

    # semantic_execution 只有字典形式才读取
    if isinstance(dict_metrics, dict) and isinstance(dict_metrics.get("semantic_execution"), dict):

        # 仅结构化 semantic_execution 才可参与语义统计
        dict_semantic_metrics = dict_metrics.get("semantic_execution")  # 语义执行结构化指标

    # 非字典 semantic_execution 不参与语义执行统计
    else:

        # 非结构化 metrics 不提供语义执行信号
        dict_semantic_metrics = {}  # 缺省语义执行指标

    # checkpoint_drift 表示工作流 checkpoint 漂移
    if dict_semantic_metrics.get("checkpoint_drift"):

        # 累计 checkpoint 漂移事件
        dict_state["int_checkpoint_drift_events"] += 1  # checkpoint drift 命中次数

    # missing_preferred_backends 统计工具链回退数量
    if isinstance(dict_metrics, dict) and dict_metrics.get("missing_preferred_backends"):

        # 累加缺失后端列表长度
        int_missing_backend_count = len(dict_metrics.get("missing_preferred_backends") or [])  # 本事件缺失后端数量

        # 累加本事件暴露的缺失首选后端
        dict_state["int_toolchain_fallback_count"] += int_missing_backend_count  # 工具链回退累计数量

    # validate 事件承担语义通过率统计
    _collect_validate_semantic_status(dict_event, dict_state, dict_semantic_metrics)

# validate 语义状态统计
def _collect_validate_semantic_status(
    dict_event: dict[str, Any],
    dict_state: dict[str, Any],
    dict_semantic_metrics: dict[str, Any],
) -> None:
    """
    根据 validate 事件更新语义通过率和失败 case 计数。

    :param dict_event: 当前 trace 事件。
    :param dict_state: evaluate_events 的可变统计状态。
    :param dict_semantic_metrics: 当前事件的 semantic_execution 子指标。
    :return: 无业务返回值，直接更新语义验证计数。
    """

    # 非 validate 事件不参与本段统计
    if dict_event.get("event") != "validate":

        # 当前事件不是最终验证事件
        return

    # 事件级 semantic_ready 优先用于通过率统计
    bool_semantic_ready = dict_event.get("semantic_ready")  # validate 事件语义就绪标记

    # 事件级字段存在时按它计数
    if bool_semantic_ready is not None:

        # 记录一次语义验证事件
        dict_state["int_semantic_events"] += 1  # validate 语义检查事件数

        # semantic_ready 为真表示语义验证通过
        if bool_semantic_ready:

            # 累计语义通过次数
            dict_state["int_semantic_passes"] += 1  # validate 语义通过数

    # 缺少事件级字段时尝试读取 semantic_execution 子指标
    elif dict_semantic_metrics:

        # 子指标存在也表示一次语义验证事件
        dict_state["int_semantic_events"] += 1  # 子指标语义检查事件数

        # 子指标 semantic_ready 为真表示语义通过
        if dict_semantic_metrics.get("semantic_ready"):

            # 累计子指标语义通过次数
            dict_state["int_semantic_passes"] += 1  # 子指标语义通过数

    # 失败 case 只在 semantic_execution 子指标存在时统计
    if dict_semantic_metrics:

        # failed_cases 和 mismatched_cases 都算验证失败样例
        list_failed_cases = dict_semantic_metrics.get("failed_cases", []) or []  # 失败 case 列表

        # mismatched_cases 表示输出不匹配的样例
        list_mismatched_cases = dict_semantic_metrics.get("mismatched_cases", []) or []  # 不匹配 case 列表

        # 保存本次 validate 的失败样例总数
        dict_state["list_failed_case_counts"].append(len(list_failed_cases) + len(list_mismatched_cases))  # 当前 validate 失败样例数

# readiness 汇总
def _collect_readiness_metrics(list_validate_events: list[dict[str, Any]], dict_state: dict[str, Any]) -> None:
    """
    根据 validate 事件汇总 readiness 分桶通过率。

    :param list_validate_events: validate 事件列表。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 无业务返回值，直接更新 readiness 分桶。
    """

    # 逐条 validate 事件统计 readiness 分桶
    for dict_event in list_validate_events:

        # readiness 缺失时按 static 处理，保持历史行为
        str_readiness = str(dict_event.get("readiness", "static"))  # readiness 分桶名称

        # 新 readiness 首次出现时从零初始化三类计数
        dict_default_bucket = {  # 新 readiness 桶的初始计数
            "total": 0,  # 该 readiness 的验证总数
            "passed": 0,  # 该 readiness 的通过数
            "failed": 0,  # 该 readiness 的失败数
        }

        # 获取或初始化该 readiness 的计数桶
        dict_bucket = dict_state["dict_readiness_counts"].setdefault(str_readiness, dict_default_bucket)  # 当前 readiness 的三项计数桶

        # validate 事件总数递增
        dict_bucket["total"] += 1  # 当前 readiness 验证总数

        # 按 ok 字段分别累计 passed/failed
        if dict_event.get("ok"):

            # 当前 readiness 验证通过
            dict_bucket["passed"] += 1  # 当前 readiness 通过数

        # ok 为假或缺失时按失败计入历史指标
        else:

            # 当前 readiness 验证未通过
            dict_bucket["failed"] += 1  # 当前 readiness 失败数

# 最终指标报告构造
def _build_metric_report(events: list[dict[str, Any]], dict_state: dict[str, Any]) -> dict[str, Any]:
    """
    将累计状态转换为对外稳定的指标字典。

    :param events: trace 事件字典列表。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 对外返回的评估指标字典。
    """

    # 提取最终报告需要的派生指标
    dict_derived = _derive_report_values(events, dict_state)  # 最终报告派生值

    # 按历史字段顺序组织报告，降低测试快照漂移
    dict_report = {  # 写入评估报告文件的尝试次数验证通过率和质量违规汇总
        "events": len(events),  # trace 事件总数
        "attempts": dict_derived["int_attempts"],  # 工作流尝试次数
        "coding_attempts": dict_derived["int_prompt_event_count"],  # 由 prompt 事件推导的编码尝试量
        "event_counts": _event_counts(events),  # event 字段分布
        "readiness": dict_state["dict_readiness_counts"],  # readiness 分桶计数
        "readiness_pass_rates": dict_derived["dict_readiness_pass_rates"],  # readiness 分桶通过率
        "readiness_pass_rate": dict_derived["float_readiness_pass_rate"],  # 全体验证通过率
        "correctness": dict_derived["bool_correctness"],  # 是否出现 execute/implement 通过
        "correct": dict_derived["bool_correctness"],  # correctness 的兼容别名
        "final_status": dict_derived["str_final_status"],  # 最后一次 workflow_attempt 状态
        "error_source_distribution": dict_state["dict_error_sources"],  # 错误来源分布
        "human_intervention_count": dict_state["int_human_interventions"],  # trace 中人工接管事件总量
        "interventions": dict_state["int_human_interventions"],  # 人工干预兼容字段
        "intervention_resolved_count": dict_state["int_resolved_interventions"],  # 带 resolved 标记的人工干预量
        "intervention_unresolved_count": dict_derived["int_unresolved_intervention_count"],  # 未解决干预数
        "average_prompt_tokens": dict_derived["float_average_prompt_tokens"],  # 提示词整体平均耗用量
        "prompt_tokens_by_budget": dict_derived["dict_prompt_tokens_by_budget"],  # 分预算类型的提示词耗用摘要
        "repair_budget_savings": _repair_budget_savings(dict_state["dict_prompt_tokens_by_budget"]),  # 修复预算相对普通预算的耗用差
        "attempts_per_verified_stage": dict_derived["float_attempts_per_verified_stage"],  # 每个验证阶段平均尝试数
        "semantic_pass_rate": dict_derived["float_semantic_pass_rate"],  # 语义验证通过率
        "gate_false_negative_markers": _gate_false_negative_markers(events),  # gate 假阴性标记
        "localization_hit_rate": dict_derived["float_localization_hit_rate"],  # 定位命中率
        "average_failed_cases_per_attempt": dict_derived["float_average_failed_cases"],  # 每次尝试平均失败 case 数
        "auto_debug_before_human_rate": dict_derived["float_auto_debug_before_human_rate"],  # 人工前自动调试比例
        "performance_pass_rate": dict_derived["float_performance_pass_rate"],  # 性能相关事件通过率
        "qor_violation_count": dict_state["int_qor_violation_count"],  # QoR 违规数量
        "checkpoint_drift_events": dict_state["int_checkpoint_drift_events"],  # checkpoint 漂移数量
        "toolchain_fallback_count": dict_state["int_toolchain_fallback_count"],  # 工具链回退数量
        "subfunction_failure_hotspots": dict_state["dict_subfunction_failures"],  # 失败归因键的出现频次
        "average_attempts_per_subfunction": dict_derived["float_average_attempts_per_subfunction"],  # 子阶段平均尝试数
        "noise_recovery": dict_derived["dict_noise_recovery"],  # 噪声恢复情况
    }  # 工作流评估报告

    # 返回完整报告
    return dict_report

# 报告派生值计算
def _derive_report_values(events: list[dict[str, Any]], dict_state: dict[str, Any]) -> dict[str, Any]:
    """
    计算最终报告中依赖多个状态字段的派生值。

    :param events: trace 事件字典列表。
    :param dict_state: evaluate_events 的可变统计状态。
    :return: 派生指标字典。
    """

    # validate 事件用于 readiness 和 correctness 指标
    list_validate_events = dict_state["list_validate_events"]  # 用于派生指标的 validate 事件

    # 统计全体验证数量
    int_total_validations = len(list_validate_events)  # 参与 readiness 统计的 validate 样本量

    # 统计 ok=True 的验证数量
    int_passed_validations = sum(1 for dict_event in list_validate_events if dict_event.get("ok"))  # 通过验证数

    # prompt 事件数量表示编码尝试次数
    list_prompt_events = [
        dict_event  # 保留 prompt 事件用于编码尝试计数
        for dict_event in events  # 扫描完整 trace
        if dict_event.get("event") == "prompt"  # 只选编码提示事件
    ]

    # 有 attempt_id 时按去重 attempt 计数，否则回退事件数
    int_attempts = len(dict_state["set_attempt_ids"]) if dict_state["set_attempt_ids"] else len(events)  # 尝试次数

    # 展平不同 budget 下的 token 列表
    list_all_prompt_tokens = [  # 所有 budget 合并后的 token 序列
        int_token  # 单次 prompt 的 token 数
        for list_tokens in dict_state["dict_prompt_tokens_by_budget"].values()  # 遍历每个 budget 的 token 列表
        for int_token in list_tokens  # 展开当前 budget 下的 token 数
    ]  # 合并全部预算类型后的提示词耗用序列

    # 逐 readiness 计算通过率
    dict_readiness_pass_rates = {  # readiness 到通过率的映射
        str_readiness: (dict_bucket["passed"] / dict_bucket["total"] if dict_bucket["total"] else None)  # 当前 readiness 通过率
        for str_readiness, dict_bucket in dict_state["dict_readiness_counts"].items()  # 遍历 readiness 计数桶
    }  # 每个 readiness 阶段的独立通过率

    # 噪声事件通过字符串和 insufficient_debug 双线索识别
    list_noise_events = [  # 含噪声或调试不足标记的事件
        dict_event  # 保留原始事件用于恢复判断
        for dict_event in events  # 遍历完整 trace
        if "noise" in str(dict_event).lower()  # 文本中出现噪声线索
        or "insufficient_debug" in (dict_event.get("error_sources", []) or [])  # 错误源标记调试不足
    ]  # 噪声或调试不足事件

    # 噪声恢复要求噪声事件中出现通过的 validate
    list_noise_passes = [  # 噪声场景中成功恢复的验证事件
        dict_event  # 保留通过的 validate 事件
        for dict_event in list_noise_events  # 遍历噪声相关事件
        if dict_event.get("event") == "validate" and bool(dict_event.get("ok"))  # 只认通过的 validate
    ]  # 噪声场景中的通过事件

    # workflow 状态取最后一个尝试状态
    str_final_status: str | None = (
        dict_state["list_workflow_statuses"][-1]  # 最后一条工作流状态
        if dict_state["list_workflow_statuses"]  # 已记录 workflow_attempt 状态
        else None  # trace 未提供 workflow_attempt 状态
    )  # 最后一条 workflow_attempt 状态

    # correctness 保持原行为：execute 或 implement readiness 通过即可
    bool_correctness = any(  # 是否存在执行级或实现级通过验证
        dict_event.get("ok") and dict_event.get("readiness") in {"execute", "implement"}  # 单条验证满足正确性门槛
        for dict_event in list_validate_events  # 遍历 validate 样本
    )  # 正确性通过标记

    # 全体验证通过率需要避免除以零
    float_readiness_pass_rate = (
        int_passed_validations / int_total_validations  # 通过数除以验证总数
        if int_total_validations  # 至少存在一个 validate 样本
        else None  # 无 validate 时保持历史 None
    )  # 所有 validate 事件的通过率

    # prompt token 平均值在没有 prompt 样本时不可用
    float_average_prompt_tokens = (
        sum(list_all_prompt_tokens) / len(list_all_prompt_tokens)  # 全部 prompt token 的算术平均
        if list_all_prompt_tokens  # prompt_stats 至少提供一个 token 样本
        else None  # 无 prompt token 时保持不可计算
    )  # 全部 prompt 的平均 token

    # 性能通过率复用安全比率函数
    float_performance_pass_rate = _safe_rate(  # QoR/性能相关事件的通过比例
        dict_state["int_performance_passes"],  # 未触发 QoR 违规的性能事件数
        dict_state["int_performance_events"],  # 全部性能信号事件数
    )  # 性能信号事件的通过率

    # 人工干预未解决数不能低于零
    int_unresolved_intervention_count = max(  # 未被 resolved 抵消的人工干预数量
        0,  # resolved 数超过 unresolved 数时归零
        dict_state["int_unresolved_interventions"] - dict_state["int_resolved_interventions"],  # 未解决减已解决的净值
    )

    # verify_stage 事件为零时仍保留分母兜底
    float_attempts_per_verified_stage = int_attempts / max(  # verify_stage 粒度的尝试密度
        1,  # 避免没有 verify_stage 时除零
        dict_state["int_verify_stage_events"],  # 已记录的验证阶段事件数
    )

    # semantic_execution 样本为空时由安全比率返回 None
    float_semantic_pass_rate = _safe_rate(  # semantic_execution 通过比例
        dict_state["int_semantic_passes"],  # semantic_ready 为真的事件数
        dict_state["int_semantic_events"],  # 参与语义统计的事件数
    )

    # localization_hit 只在 diagnosis 样本范围内计算
    float_localization_hit_rate = _safe_rate(  # diagnosis 定位命中比例
        dict_state["int_localization_hits"],  # localization_hit 为真的诊断数
        dict_state["int_localization_attempts"],  # 产生 diagnosis 的事件数
    )

    # 收集参与子阶段密度计算的 subfunction 名称
    set_subfunctions = {  # trace 中出现过的非空 subfunction 集合
        dict_event.get("subfunction")  # 原始 subfunction 名称
        for dict_event in events  # 扫描 trace 中的全部事件
        if dict_event.get("subfunction")  # 跳过空值
    }

    # 子阶段数量为空时用一作为密度分母
    float_average_attempts_per_subfunction = int_attempts / max(  # 每个子阶段分摊的尝试量
        1,  # 无子阶段样本时使用单分母
        len(set_subfunctions),  # 非空 subfunction 数量
    )

    # 预先构造 budget 摘要，避免主报告内嵌复杂表达式
    dict_prompt_tokens_summary = _prompt_budget_summary(  # budget 级 token 聚合摘要
        dict_state["dict_prompt_tokens_by_budget"]  # 原始 budget 到 token 列表映射
    )

    # 组织派生值，主报告构造函数只负责字段排列
    dict_derived = {  # 主报告字段依赖的集中派生结果
        "int_attempts": int_attempts,  # 去重 attempt_id 后的工作流尝试量
        "int_prompt_event_count": len(list_prompt_events),  # prompt 驱动的编码尝试量
        "dict_readiness_pass_rates": dict_readiness_pass_rates,  # 分 readiness 阶段成功比例
        "float_readiness_pass_rate": float_readiness_pass_rate,  # validate 样本整体成功比例
        "bool_correctness": bool_correctness,  # execute/implement 正确性标记
        "str_final_status": str_final_status,  # 最后 workflow 状态
        "int_unresolved_intervention_count": int_unresolved_intervention_count,  # 当前仍未闭环的人工干预量
        "float_average_prompt_tokens": float_average_prompt_tokens,  # 所有预算桶合并后的平均 token
        "dict_prompt_tokens_by_budget": dict_prompt_tokens_summary,  # 每个 budget 的 count/average 摘要
        "float_attempts_per_verified_stage": float_attempts_per_verified_stage,  # 验证阶段维度的尝试密度
        "float_semantic_pass_rate": float_semantic_pass_rate,  # semantic_execution 样本成功比例
        "float_localization_hit_rate": float_localization_hit_rate,  # diagnosis 样本命中比例
        "float_average_failed_cases": _average_or_none(dict_state["list_failed_case_counts"]),  # 平均失败 case 数
        "float_auto_debug_before_human_rate": _safe_rate(  # 人工升级前自动调试覆盖率
            dict_state["int_auto_debug_before_human"],  # 人工升级前已有自动调试的次数
            dict_state["int_human_escalations"],  # 全部人工升级次数
        ),  # 人工升级前自动调试比例
        "float_performance_pass_rate": float_performance_pass_rate,  # 性能通过率
        "float_average_attempts_per_subfunction": float_average_attempts_per_subfunction,  # 子阶段维度的尝试密度
        "dict_noise_recovery": {  # 噪声场景恢复能力摘要
            "total_noise_markers": len(list_noise_events),  # 噪声标记数量
            "recovered": bool(list_noise_passes),  # 噪声后是否恢复通过
        },  # 噪声恢复摘要
    }  # 派生指标字典

    # 返回派生值给报告构造函数
    return dict_derived

# prompt budget 摘要构造
def _prompt_budget_summary(tokens_by_budget: dict[str, list[int]]) -> dict[str, dict[str, float | int]]:
    """
    计算每个 prompt budget 的数量和平均 token。

    :param tokens_by_budget: budget 到 token 数列表的映射。
    :return: budget 到 count/average 摘要的映射。
    """

    # 按 budget 名排序，保证 JSON 输出稳定
    dict_summary = {  # 每个 prompt budget 的样本量和均值
        str_budget: {  # 当前 budget 的统计摘要
            "count": len(list_tokens),  # 当前 budget 的 prompt 数量
            "average": sum(list_tokens) / len(list_tokens),  # 当前 budget 的平均 token
        }
        for str_budget, list_tokens in sorted(tokens_by_budget.items())  # 按 budget 名称稳定排序
    }  # prompt token 预算分层统计

    # 返回 budget 分桶统计
    return dict_summary

# 事件类型计数
def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """
    统计 trace 中 event 字段的出现次数。

    :param events: trace 事件字典列表。
    :return: event 名到出现次数的映射。
    """

    # 保存 event 名称分布
    dict_counts: dict[str, int] = {}  # event 名称计数器

    # 逐条事件读取 event 字段
    for dict_event in events:

        # 缺失 event 时归为 unknown
        str_event_name = str(dict_event.get("event", "unknown"))  # event 字段名称

        # 累计该 event 名出现次数
        _increment_counter(dict_counts, str_event_name)

    # 返回事件分布
    return dict_counts

# repair 与 normal 预算的 token 差额估算
def _repair_budget_savings(tokens_by_budget: dict[str, list[int]]) -> float | None:
    """
    计算 normal budget 与 repair budget 的平均 token 差值。

    :param tokens_by_budget: budget 到 token 数列表的映射。
    :return: normal 平均 token 减 repair 平均 token；缺失任一分桶时返回 None。
    """

    # normal budget 代表普通生成尝试
    list_normal_tokens = tokens_by_budget.get("normal") or []  # 普通生成预算的 token 样本

    # repair budget 代表修复尝试
    list_repair_tokens = tokens_by_budget.get("repair") or []  # 修复预算的 token 样本

    # 任一分桶缺失时无法估算节省量
    if not list_normal_tokens or not list_repair_tokens:

        # 返回 None 表示该指标不可用
        return None

    # 返回两个分桶均值的差
    return (sum(list_normal_tokens) / len(list_normal_tokens)) - (sum(list_repair_tokens) / len(list_repair_tokens))

# gate 假阴性标记统计
def _gate_false_negative_markers(events: list[dict[str, Any]]) -> int:
    """
    统计 verify_stage ready 后 validate 仍语义失败的标记数量。

    :param events: trace 事件字典列表。
    :return: gate 假阴性标记数量。
    """

    # 保存已经被 verify_stage 判定 ready 的 attempt_id
    set_verified_ready: set[str] = set()  # verify_stage ready 的尝试集合

    # 累计 ready 后仍失败的 validate 次数
    int_markers = 0  # gate 假阴性计数

    # 按事件顺序扫描，保持与原 trace 时间线一致
    for dict_event in events:

        # attempt_id 用于关联 verify_stage 和 validate
        str_attempt_id = str(dict_event.get("attempt_id") or "")  # ready 阶段与 validate 配对的尝试 ID

        # verify_stage ready 事件登记可疑前置状态
        if dict_event.get("event") == "verify_stage" and bool(dict_event.get("ready")) and str_attempt_id:

            # 保存该 attempt_id，等待后续 validate 核对
            set_verified_ready.add(str_attempt_id)

            # 当前事件处理完毕，继续扫描下一条
            continue

        # 非目标 validate 或缺少 attempt_id 时不参与假阴性统计
        if dict_event.get("event") != "validate" or not str_attempt_id or str_attempt_id not in set_verified_ready:

            # 当前事件无法与已 ready 的验证阶段配对
            continue

        # 判断 validate 是否语义失败
        bool_failed_semantically = _validate_failed_semantically(dict_event)  # validate 语义失败标记

        # ready 后语义失败视为一个假阴性标记
        if bool_failed_semantically:

            # 累计 gate 假阴性标记
            int_markers += 1  # ready 后仍语义失败的 validate 数量

    # 返回累计数量
    return int_markers

# validate 语义失败判定
def _validate_failed_semantically(dict_event: dict[str, Any]) -> bool:
    """
    判断 validate 事件是否包含语义失败信号。

    :param dict_event: validate 事件字典。
    :return: True 表示 validate 事件语义未就绪。
    """

    # 事件级 semantic_ready 优先判定
    bool_semantic_ready = dict_event.get("semantic_ready")  # 事件级 semantic_ready 原始布尔标记

    # 从 validate.metrics 中读取语义执行子结构
    dict_semantic_metrics = (dict_event.get("metrics") or {}).get("semantic_execution", {})  # validate 指标中的语义执行块

    # 事件级字段明确为 False 时失败
    bool_event_failed = bool_semantic_ready is not None and not bool_semantic_ready  # 事件级语义失败信号

    # 子指标 semantic_ready 明确为 False 时失败
    bool_metric_failed = (  # semantic_execution 子指标的失败信号
        isinstance(dict_semantic_metrics, dict)  # 子指标必须保持字典结构
        and dict_semantic_metrics.get("semantic_ready") is not None  # 子指标显式给出 semantic_ready
        and not dict_semantic_metrics.get("semantic_ready")  # 子指标标记为未就绪
    )  # 子指标失败标记

    # 任一语义失败信号命中即可
    return bool_event_failed or bool_metric_failed

# 计数器递增辅助函数
def _increment_counter(dict_counter: dict[str, int], str_key: str) -> None:
    """
    对字符串键计数器执行加一操作。

    :param dict_counter: 字符串键到整数计数的映射。
    :param str_key: 需要递增的计数键。
    :return: 无业务返回值，直接更新 dict_counter。
    """

    # 按键累加一次出现次数
    dict_counter[str_key] = dict_counter.get(str_key, 0) + 1  # 指定键的出现次数

# 安全比率计算
def _safe_rate(int_numerator: int, int_denominator: int) -> float | None:
    """
    计算可缺失分母的比率。

    :param int_numerator: 比率分子。
    :param int_denominator: 比率分母。
    :return: 分母非零时返回分子除以分母，否则返回 None。
    """

    # 分母为零时该比率不可用
    if not int_denominator:

        # 返回 None 与历史指标语义保持一致
        return None

    # 返回普通浮点比率
    return int_numerator / int_denominator

# 平均值计算
def _average_or_none(list_values: list[int]) -> float | None:
    """
    计算整数列表平均值，空列表返回 None。

    :param list_values: 待平均的整数列表。
    :return: 平均值；列表为空时返回 None。
    """

    # 空列表没有平均值
    if not list_values:

        # 返回 None 表示指标不可用
        return None

    # 返回普通算术平均值
    return sum(list_values) / len(list_values)
