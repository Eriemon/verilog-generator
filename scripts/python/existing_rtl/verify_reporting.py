"""组装 existing RTL verify-repair 流程的结构化报告。"""

# 延迟类型注解求值，保持运行时导入成本稳定
from __future__ import annotations

# 标准类型依赖用于表达 JSON payload 的宽松结构
from typing import Any

# workflow router 提供诊断分流标签，报告层只消费其结果
from scripts.python.workflow.workflow_router import classify_diagnosis_route

# 诊断 outcome 到人工可读 bug class 的映射保持独立，避免报告函数堆叠分支
def _bug_class_for_outcome(diagnosis: dict[str, Any]) -> str:
    """把验证诊断 outcome 映射为报告层 bug class。

    参数:
        diagnosis: 验证诊断结果，通常包含 outcome 字段。
    返回:
        兼容既有报告 wire shape 的 bug class 字符串。
    """

    # outcome 先转成字符串，兼容诊断 payload 中的 None 或枚举式对象
    str_outcome = str(diagnosis.get("outcome"))  # 诊断结果标签

    # compile 错误优先归入编译类，便于上层提示先看工具日志
    if str_outcome == "compile_error":

        # 返回 wire shape 既有的 bug class 字符串
        return "compile"

    # assertion 失败通常说明协议或 RTL 逻辑不满足测试约束
    if str_outcome == "assertion_fail":

        # 保持既有 payload 中的分类文本
        return "protocol_or_logic"

    # protocol violation 需要保留 timing 可能性，供修复计划选择检查路径
    if str_outcome == "protocol_violation":

        # 返回兼容原 JSON 的协议/时序分类
        return "protocol_or_timing"

    # timeout 更接近活性问题，避免误导为单点组合逻辑错误
    if str_outcome == "timeout":

        # 返回既有 liveness 分类
        return "liveness"

    # pass 表示没有待归因缺陷
    if str_outcome == "pass":

        # 返回 none 保持 downstream 字段值兼容
        return "none"

    # not_run 表示验证没有执行，不能推断具体 bug class
    if str_outcome == "not_run":

        # 返回 not_run 保留未执行状态
        return "not_run"

    # 未知 outcome 交给调用方以保守方式展示
    return "unknown"

# 仿真片段报告保留 compile/simulation 摘要与 testbench 标签观测
def simulation_slice_payload(
    *,
    compile_log: str,
    simulation_log: str,
    executed: bool,
    tb_contract: dict[str, Any],
    excerpt_fn: Any,
) -> dict[str, Any]:
    """
    生成仿真日志切片报告。

    :param compile_log: 编译阶段完整日志文本。
    :param simulation_log: 仿真阶段完整日志文本。
    :param executed: 仿真命令是否实际执行。
    :param tb_contract: testbench 契约，包含日志标签和 transcript 前缀。
    :param excerpt_fn: 日志截断函数，用于生成可读摘要。
    :return: 保持 wire shape 兼容的仿真日志切片 payload。
    """

    # 只记录真实出现在仿真日志中的 testbench 标签
    list_tb_tags = [tag for tag in tb_contract.get("log_tags", []) if tag in simulation_log]  # 日志中命中的 TB 标签

    # 返回给上层报告写入器的稳定 JSON payload
    return {
        "version": 1,
        "executed": executed,
        "compile_excerpt": excerpt_fn(compile_log),
        "simulation_excerpt": excerpt_fn(simulation_log),
        "observed_tags": list_tb_tags,
        "transcript_prefix": tb_contract.get("transcript_prefix"),
    }

# timing diagnostic 报告提取诊断归因和验证计划焦点
def timing_diagnostic_payload(
    diagnosis: dict[str, Any],
    *,
    validation_report: Any,
    verification_plan: dict[str, Any],
) -> dict[str, Any]:
    """
    生成时序/协议诊断 payload。

    :param diagnosis: 验证诊断结果，至少包含 outcome 与 findings。
    :param validation_report: 验证报告对象，需提供 ok() 方法。
    :param verification_plan: 验证计划，包含 focus_signals 等定位信息。
    :return: 面向修复报告的诊断摘要 payload。
    """

    # 将 outcome 归一到报告层展示的 bug class
    str_bug_class = _bug_class_for_outcome(diagnosis)  # 报告层缺陷分类

    # 返回诊断摘要，字段名保持既有 wire shape 不变
    return {
        "version": 1,
        "outcome": diagnosis.get("outcome"),
        "bug_class": str_bug_class,
        "validation_ok": bool(validation_report.ok()),
        "focus_signals": verification_plan.get("focus_signals", []),
        "findings": diagnosis.get("findings", []),
    }

# expected trace markdown 是分析结果和验证 checkpoint 的人读摘要
def expected_trace_markdown(analysis: dict[str, Any], verification_plan: dict[str, Any]) -> str:
    """
    生成 expected trace Markdown 摘要。

    :param analysis: RTL 分析结构，包含 module_info.name。
    :param verification_plan: 验证计划，包含 verification_targets 列表。
    :return: 描述 checkpoint、signals 与 expectation 的 Markdown 文本。
    """

    # 模块名用于报告标题，与生成工件的 RTL 模块保持一致
    str_module_name = str(analysis["module_info"]["name"])  # Expected Trace 标题模块名

    # checkpoint 列表来自 verification plan，缺失时生成兜底行
    list_checkpoints = verification_plan.get("verification_targets", [])  # 待展示的验证 checkpoint

    # 表格头保留英文文本，保证已有快照和文档比较稳定
    list_lines = [
        f"# Expected Trace: {str_module_name}",  # 报告标题中的 RTL 模块名
        "",  # 标题与来源说明之间的 Markdown 间隔
        "This trace is analysis-derived and used as a stable semantic checkpoint summary.",  # trace 来源说明
        "",  # 来源说明与 checkpoint 表格之间的 Markdown 间隔
        "| Step | Checkpoint | Signals | Expectation |",  # checkpoint 表格列名
        "| --- | --- | --- | --- |",  # Markdown 表格列对齐标记
    ]  # checkpoint 明细追加前必须先输出的标题、来源说明和表头

    # 逐个 checkpoint 生成表格行，按 1 起始序号匹配人读报告习惯
    for int_index, dict_checkpoint in enumerate(list_checkpoints, start=1):

        # signals 字段为空时写成 n/a，避免报告单元格留空
        str_signals = ", ".join(dict_checkpoint.get("signals", [])) or "n/a"  # checkpoint 关注信号文本

        # description 是最适合作为 expectation 的人读说明
        str_description = dict_checkpoint.get("description")  # checkpoint 描述文本

        # name 作为 description 缺失时的次级兜底
        str_checkpoint_name = dict_checkpoint.get("name")  # checkpoint 名称文本

        # expectation 优先使用描述，其次回退到 checkpoint 名称
        str_expectation = str(str_description or str_checkpoint_name or "analysis-derived behavior")  # checkpoint 期望行为文本

        # 追加当前 checkpoint 的 Markdown 表格行
        list_lines.append(
            f"| {int_index} | {dict_checkpoint.get('check_id', f'checkpoint_{int_index}')} | "
            f"{str_signals} | {str_expectation} |"
        )

    # 只有表头时写入兜底 checkpoint，明确说明分析未推断出检查点
    if len(list_lines) == 6:

        # 追加无 checkpoint 的显式占位行，保持表格结构完整
        list_lines.append("| 1 | no_checkpoints | n/a | No verification checkpoints were inferred. |")

    # Markdown 末尾保留一个空行，匹配原报告格式
    list_lines.append("")

    # 返回完整 Markdown 文本供文件写入器保存
    return "\n".join(list_lines)

# waveform diff payload 记录波形检查状态和人工复核摘要
def waveform_diff_payload(
    diagnosis: dict[str, Any],
    *,
    verification_plan: dict[str, Any],
    executed: bool,
) -> dict[str, Any]:
    """
    生成波形差异检查 payload。

    :param diagnosis: 验证诊断结果，包含 outcome 与 findings。
    :param verification_plan: 验证计划，包含 focus_signals。
    :param executed: 波形检查是否实际执行。
    :return: 波形差异状态 payload。
    """

    # 返回波形检查摘要，保持 pass/pending_review 状态文本不变
    return {
        "version": 1,
        "executed": executed,
        "status": "pass" if diagnosis.get("outcome") == "pass" else "pending_review",
        "focus_signals": verification_plan.get("focus_signals", []),
        "summary": diagnosis.get("findings", []),
    }

# testcase matrix payload 将验证计划转换为 TB 覆盖矩阵
def testcase_matrix_payload(
    verification_plan: dict[str, Any],
    *,
    tb_contract: dict[str, Any],
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    """
    生成 testcase 覆盖矩阵 payload。

    :param verification_plan: 验证计划，包含 verification_targets。
    :param tb_contract: testbench 契约，包含 tb_mode 与 log_tags。
    :param diagnosis: 验证诊断结果，用于判断 case 是否被编译错误阻断。
    :return: 描述 TB mode 与 case 覆盖状态的 payload。
    """

    # 收集每个 verification target 对应的 testcase 行
    list_cases = []  # 验证计划转换后的 testcase payload 行

    # 按验证计划顺序生成 case，保证报告稳定可比较
    for int_index, dict_target in enumerate(verification_plan.get("verification_targets", []), start=1):

        # 追加单个 testcase 行，字段名保持既有 JSON 契约
        list_cases.append(
            {
                "case_id": dict_target.get("check_id", f"checkpoint_{int_index}"),
                "category": dict_target.get("category", "behavior"),
                "signals": dict_target.get("signals", []),
                "expectation": dict_target.get("description")
                or dict_target.get("name")
                or "analysis-derived verification target",
                "log_tags": tb_contract.get("log_tags", []),
                "status": "covered" if diagnosis.get("outcome") != "compile_error" else "blocked_by_compile",
            }
        )

    # 返回 testbench 模式和 case 矩阵
    return {"version": 1, "tb_mode": tb_contract.get("tb_mode"), "cases": list_cases}

# run summary payload 汇总验证循环终态和 RTL/TB 变更状态
def run_summary_payload(
    *,
    diagnosis: dict[str, Any],
    validation_report: Any,
    tb_contract: dict[str, Any],
    rtl_mutation: dict[str, Any],
) -> dict[str, Any]:
    """
    生成 verify-repair 单轮运行摘要。

    :param diagnosis: 验证诊断结果，包含 outcome。
    :param validation_report: 验证报告对象，需提供 ok() 方法。
    :param tb_contract: testbench 契约，包含模式与语言。
    :param rtl_mutation: RTL 修复记录，包含 applied 与 confirmation_required。
    :return: 面向上层 workflow 的运行摘要 payload。
    """

    # diagnosis route 由 workflow router 统一分类，报告层不复制分流规则
    str_diagnosis_route = classify_diagnosis_route(  # 运行摘要使用的诊断分流标签
        diagnosis=diagnosis,  # 诊断分流依据的 outcome/findings payload
        validation_report=validation_report,  # 诊断分流依据的验证报告对象
        tb_contract=tb_contract,  # 诊断分流依据的 testbench 契约
    )

    # 先创建摘要字典，再逐项填充以避免大字面量遮住字段来源
    dict_summary = {"version": 1}  # run summary payload 基础结构

    # status 固定表示本轮 verify-repair 已走到摘要阶段
    dict_summary["status"] = "completed"  # 运行摘要状态

    # outcome 透传诊断阶段产出的终态标签
    dict_summary["outcome"] = diagnosis.get("outcome")  # 验证诊断 outcome

    # diagnosis route 标明后续报告应优先查看的诊断方向
    dict_summary["diagnosis_route"] = str_diagnosis_route  # 诊断分流标签

    # validation_ok 统一转成 bool，屏蔽报告对象实现细节
    dict_summary["validation_ok"] = bool(validation_report.ok())  # 验证报告通过标志

    # testbench 模式保留给上层报告展示 TB 生成策略
    dict_summary["tb_mode"] = tb_contract.get("tb_mode")  # 报告展示用 testbench 模式

    # testbench 语言用于区分 Verilog/SystemVerilog 输出
    dict_summary["tb_language"] = tb_contract.get("tb_language")  # 报告展示用 testbench 语言

    # RTL patch 状态说明本轮是否修改过设计文件
    dict_summary["rtl_patch_applied"] = bool(rtl_mutation.get("applied"))  # RTL 修改应用状态

    # confirmation_required 保留人工确认边界，避免自动修复越权
    dict_summary["confirmation_required"] = bool(rtl_mutation.get("confirmation_required"))  # 人工确认需求

    # 返回运行摘要，字段名保持下游消费兼容
    return dict_summary

# synth readiness payload 暴露仿真后端选择和综合准备状态
def synth_readiness_payload(validation_report: Any, *, readiness: str) -> dict[str, Any]:
    """
    生成 synthesis readiness payload。

    :param validation_report: 验证报告对象，需提供 to_dict() 与 ok() 方法。
    :param readiness: 调用方请求的 readiness 模式，例如 implement。
    :return: 描述工具执行、后端选择和 readiness 请求的 payload。
    """

    # metrics 承载验证阶段记录的工具链选择和缺失后端信息
    dict_metrics = validation_report.to_dict().get("metrics", {})  # 验证报告指标字段

    # 返回综合准备状态，保留既有字段名供报告和测试消费
    return {
        "version": 1,
        "requested_readiness": readiness,
        "selected_simulator_backend": dict_metrics.get("selected_simulator_backend"),
        "executed_tools": dict_metrics.get("executed_tools", []),
        "missing_preferred_backends": dict_metrics.get("missing_preferred_backends", []),
        "selection_policy": dict_metrics.get("selection_policy"),
        "implement_requested": readiness == "implement",
        "validation_ok": bool(validation_report.ok()),
    }

# terminal status payload 是 verify-repair 对外暴露的最终状态摘要
def terminal_status_payload(
    *,
    diagnosis: dict[str, Any],
    validation_report: Any,
    tb_contract: dict[str, Any] | None = None,
    tb_mutation: dict[str, Any],
    rtl_mutation: dict[str, Any],
) -> dict[str, Any]:
    """
    生成 verify-repair 终态 payload。

    :param diagnosis: 验证诊断结果，包含 outcome。
    :param validation_report: 验证报告对象，需提供 ok() 方法。
    :param tb_contract: 可选 testbench 契约，缺失时按空契约分类。
    :param tb_mutation: testbench 变更记录，包含 applied。
    :param rtl_mutation: RTL 变更记录，包含 applied。
    :return: 描述验证终态、变更状态和用户可读消息的 payload。
    """

    # 只有诊断 pass 且验证报告 ok 时，才认为 verify-repair 达到成功终态
    bool_success = diagnosis.get("outcome") == "pass" and validation_report.ok()  # 终态成功标志

    # 空 TB 契约按空字典处理，避免分类器收到 None
    dict_tb_contract = tb_contract or {}  # 诊断分类使用的 TB 契约

    # diagnosis route 复用 workflow router 的统一分类规则
    str_diagnosis_route = classify_diagnosis_route(  # 终态 payload 使用的诊断分流标签
        diagnosis=diagnosis,  # 终态分流依据的 outcome/findings payload
        validation_report=validation_report,  # 终态分流依据的验证报告对象
        tb_contract=dict_tb_contract,  # 终态分流依据的 testbench 契约
    )

    # 返回 verify-repair 最终状态，message 文本保持原有英文契约
    return {
        "version": 1,
        "success": bool_success,
        "outcome": diagnosis.get("outcome"),
        "diagnosis_route": str_diagnosis_route,
        "validation_ok": bool(validation_report.ok()),
        "tb_mutation_applied": bool(tb_mutation.get("applied")),
        "rtl_mutation_applied": bool(rtl_mutation.get("applied")),
        "message": "Verification loop reached a clean PASS state."
        if bool_success
        else "Verification loop did not reach a terminal PASS state.",
    }
