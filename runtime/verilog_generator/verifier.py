"""校验 Verilog workflow 相邻阶段的契约一致性。"""

# 延迟注解解析，避免导入期处理嵌套 JSON 类型。
from __future__ import annotations

# 运行时契约由 JSON 字典承载，类型提示保持宽容。
from typing import Any

# 公开入口汇总阶段契约、计划接口和语义执行问题。
def verify_stage(
    plan: dict[str, Any],
    from_contract: dict[str, Any],
    to_contract: dict[str, Any],
) -> dict[str, Any]:
    """校验 workflow 相邻阶段之间的契约兼容性。

    参数:
        plan: codegen plan 或规范化计划，用于提供期望 top 和端口。
        from_contract: 上一阶段导出的契约。
        to_contract: 当前阶段导出的契约。

    返回:
        保持旧版字段形状的校验报告，包含 ready、issues 和语义执行摘要。
    """

    # issues 汇总原始契约问题、接口漂移和测试用例漂移。
    list_issues: list[dict[str, Any]] = []  # 阶段契约问题列表

    # 上一阶段问题保留原诊断内容，只附加来源侧。
    list_issues.extend(_contract_issues(from_contract, "from"))

    # 当前阶段问题同样附加来源侧，便于报告定位。
    list_issues.extend(_contract_issues(to_contract, "to"))

    # RTL 输出契约需要和 plan 中的 top/ports 对齐。
    list_issues.extend(plan_contract_interface_issues(plan, to_contract))

    # reference 和 RTL 阶段的 case_ids 不一致会降低验证闭环可信度。
    list_issues.extend(_check_cases(from_contract, to_contract))

    # 语义执行指标独立解析，供报告字段和 recommended_action 复用。
    dict_semantic = _semantic_execution_issues(to_contract)  # 当前阶段语义执行诊断

    # 语义问题加入统一 issues 列表。
    list_issues.extend(dict_semantic["issues"])

    # ready 只要存在 error 级问题就为 False。
    bool_ready = not any(item.get("severity") == "error" for item in list_issues)  # 阶段契约是否可继续

    # semantic_ready 显式布尔 False 时建议修复语义 testbench。
    semantic_ready_value = dict_semantic["semantic_ready"]  # 当前阶段语义 ready 原始值

    # 仅布尔 False 触发 repair_semantic_testbench，None 表示未检查。
    bool_requires_semantic_repair = isinstance(semantic_ready_value, bool) and not semantic_ready_value  # 是否建议修复语义 testbench

    # 默认没有语义修复建议。
    str_recommended_action = None  # 下一步建议动作

    # 语义执行明确失败时给出旧版建议动作。
    if bool_requires_semantic_repair:

        # 推荐动作字符串由旧版报告消费，不能改名。
        str_recommended_action = "repair_semantic_testbench"  # 语义 testbench 修复动作

    # error_sources 去重排序后供 workflow trace 和 repair plan 使用。
    list_error_sources = sorted({str(item.get("source")) for item in list_issues if item.get("source")})  # 问题来源集合

    # 返回字段保持旧版契约，避免上游报告解析变化。
    return {
        "version": 1,
        "target": plan.get("target", "rtl"),
        "ready": bool_ready,
        "issues": list_issues,
        "semantic_ready": dict_semantic["semantic_ready"],
        "mismatched_cases": dict_semantic["mismatched_cases"],
        "checkpoint_drift": dict_semantic["checkpoint_drift"],
        "failed_cases": dict_semantic["failed_cases"],
        "localization_confidence": dict_semantic["localization_confidence"],
        "error_sources": list_error_sources,
        "recommended_action": str_recommended_action,
        "summary": {
            "from_target": from_contract.get("target"),
            "to_target": to_contract.get("target"),
            "top": to_contract.get("top"),
        },
    }

# 公开接口校验器检查 plan 期望端口和 RTL 契约端口是否一致。
def plan_contract_interface_issues(plan: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    """检查 RTL 阶段契约是否保留计划中的 top 和端口定义。

    参数:
        plan: 提供期望模块名和 interfaces.ports 的计划字典。
        contract: 当前阶段导出的契约字典。

    返回:
        error 级接口漂移问题列表；非 RTL 契约返回空列表。
    """

    # 当前函数只验证 RTL 契约，其它阶段没有端口约束。
    list_issues: list[dict[str, Any]] = []  # RTL 接口契约问题

    # 非 RTL 目标不执行 top/ports 对齐检查。
    if contract.get("target") != "rtl":

        # 返回空问题，保持非 RTL 阶段宽容。
        return list_issues

    # plan.name 是期望的 RTL 顶层模块名。
    value_expected_top = plan.get("name")  # 计划中的期望 top 名称

    # contract.top 是当前阶段实际解析到的 RTL 顶层名。
    value_observed_top = contract.get("top")  # 当前契约中的 top 名称

    # 两侧都提供 top 且不一致时报告顶层漂移。
    if value_expected_top and value_observed_top and value_expected_top != value_observed_top:

        # 顶层不一致通常表示生成文件不是目标模块。
        list_issues.append(
            _issue("error", f"RTL top mismatch: expected {value_expected_top!r}, observed {value_observed_top!r}.")
        )

    # 期望端口按 name 建索引，便于后续逐个比较。
    dict_expected_ports = {
        str(item.get("name")): item  # 计划端口对象
        for item in plan.get("interfaces", {}).get("ports", [])  # 计划端口列表
        if isinstance(item, dict) and item.get("name")  # 只索引 RTL 契约中可命名匹配的端口
    }  # 计划端口索引

    # 观测端口同样按 name 建索引。
    dict_observed_ports = {
        str(item.get("name")): item  # 契约端口对象
        for item in contract.get("ports", [])  # RTL 契约端口列表
        if isinstance(item, dict) and item.get("name")  # 只保留具名端口
    }  # RTL 契约端口索引

    # 逐个计划端口检查是否缺失、方向漂移或位宽漂移。
    for str_port_name, dict_expected_port in dict_expected_ports.items():

        # observed 缺失说明 RTL 没有导出计划要求的端口。
        dict_observed_port = dict_observed_ports.get(str_port_name)  # 当前端口的 RTL 观测定义

        # 缺失端口直接记录 error，并跳过后续方向/位宽比较。
        if not dict_observed_port:

            # 端口缺失会破坏 testbench 和集成契约。
            list_issues.append(_issue("error", f"RTL port {str_port_name!r} is missing."))

            # 当前端口没有可比较的观测定义。
            continue

        # 两侧都声明 direction 时必须完全一致。
        if (
            dict_expected_port.get("direction")
            and dict_observed_port.get("direction")
            and dict_expected_port["direction"] != dict_observed_port["direction"]
        ):

            # 方向漂移会改变模块使用方式。
            list_issues.append(_issue("error", f"RTL port {str_port_name!r} direction changed."))

        # 期望宽度缺省按 1 处理，匹配旧版逻辑。
        int_expected_width = int(dict_expected_port.get("width", 1) or 1)  # 计划端口位宽

        # 观测位宽允许缺失；缺失时不报宽度漂移。
        value_observed_width = dict_observed_port.get("width")  # RTL 契约端口位宽

        # 观测位宽存在且和期望不同则报告 error。
        if value_observed_width is not None and int(value_observed_width) != int_expected_width:

            # 长错误消息拆分成变量，避免报告构造行过宽。
            str_message = (  # 位宽漂移诊断文本
                f"RTL port {str_port_name!r} width changed "  # 端口名和漂移类型
                f"from {int_expected_width} to {value_observed_width}."  # 期望位宽到观测位宽
            )

            # 记录当前端口位宽漂移。
            list_issues.append(_issue("error", str_message))

    # 返回 RTL 接口漂移问题列表。
    return list_issues

# 契约问题复制器给已有 issue 标注来源阶段。
def _contract_issues(contract: dict[str, Any], side: str) -> list[dict[str, Any]]:
    """复制契约内已有问题并标注来源侧。

    参数:
        contract: workflow 阶段导出的契约字典。
        side: 当前问题所属的阶段侧标识，通常为 from 或 to。

    返回:
        已补充 side 字段的契约问题列表。
    """

    # 复制后的问题列表不会反写原始 contract。
    list_issues: list[dict[str, Any]] = []  # 附加 side 后的契约问题

    # contract.issues 可能缺失，按空列表处理。
    for item in contract.get("issues", []) or []:

        # 只有 dict 形态问题才能安全附加 side 字段。
        if isinstance(item, dict):

            # side 字段帮助诊断问题来自 from 还是 to 阶段。
            list_issues.append({**item, "side": side})

    # 返回复制后的契约问题。
    return list_issues

# case_ids 检查器确认两阶段引用同一批参考用例。
def _check_cases(from_contract: dict[str, Any], to_contract: dict[str, Any]) -> list[dict[str, Any]]:
    """检查两阶段契约是否引用同一批 reference case。

    参数:
        from_contract: 上一阶段导出的契约字典。
        to_contract: 当前阶段导出的契约字典。

    返回:
        用例集合漂移问题列表；未漂移时返回空列表。
    """

    # from 阶段 case_ids 是参考契约期望覆盖的用例集合。
    set_expected_cases = {str(item) for item in from_contract.get("case_ids", []) or []}  # 参考阶段用例集合

    # to 阶段 case_ids 是当前契约实际覆盖的用例集合。
    set_observed_cases = {str(item) for item in to_contract.get("case_ids", []) or []}  # 当前阶段用例集合

    # 两侧都有用例且集合不一致时报告 warning。
    if set_expected_cases and set_observed_cases and set_expected_cases != set_observed_cases:

        # 用例集合漂移影响覆盖率，但不一定阻断生成。
        return [_issue("warning", "Reference case ids differ between stages.", source="testbench_issue")]

    # 没有发现用例集合漂移。
    return []

# 语义执行指标解析器把 transcript/checkpoint 失败转成契约问题。
def _semantic_execution_issues(contract: dict[str, Any]) -> dict[str, Any]:
    """把契约中的 semantic_execution 指标转换为统一诊断。

    参数:
        contract: 当前阶段导出的契约字典。

    返回:
        包含语义执行问题、ready 状态、漂移用例和定位置信度的摘要字典。
    """

    # metrics 只有 dict 形态时才可读取 semantic_execution。
    dict_metrics = contract.get("metrics") if isinstance(contract.get("metrics"), dict) else {}  # 当前契约指标对象

    # 原始 semantic_execution 可能缺失或不是对象，先以动态值承接。
    value_semantic_execution = dict_metrics.get("semantic_execution")  # 原始语义执行指标

    # semantic_execution 缺失时使用空字典表达当前阶段未运行语义用例。
    dict_semantic_execution = value_semantic_execution if isinstance(value_semantic_execution, dict) else {}  # 语义执行指标

    # 没有语义指标时返回空诊断，但保留字段形状。
    if not dict_semantic_execution:

        # None 表示未检查，而不是语义检查失败。
        return {
            "issues": [],
            "semantic_ready": None,
            "mismatched_cases": [],
            "checkpoint_drift": [],
            "failed_cases": [],
            "localization_confidence": None,
        }

    # 语义执行问题会附加到 verify_stage 的统一 issues。
    list_issues: list[dict[str, Any]] = []  # 语义执行问题列表

    # semantic_ready 是上游语义执行器的总体就绪判断。
    value_semantic_ready = dict_semantic_execution.get("semantic_ready")  # 语义执行总体状态

    # mismatched_cases 记录 reference/RTL 用例覆盖差异。
    mismatched_cases = dict_semantic_execution.get("mismatched_cases", []) or []  # 覆盖不一致的用例

    # checkpoint_drift 记录 checkpoint 名称或值的漂移。
    list_checkpoint_drift = dict_semantic_execution.get("checkpoint_drift", []) or []  # 语义检查点漂移列表

    # failed_cases 记录 transcript 中失败的用例。
    failed_cases = dict_semantic_execution.get("failed_cases", []) or []  # 语义执行失败用例

    # 显式布尔 False 表示语义用例或 checkpoint 尚未准备好。
    bool_semantic_failed = isinstance(value_semantic_ready, bool) and not value_semantic_ready  # 语义执行是否明确失败

    # 语义执行未就绪时给出阻断级问题。
    if bool_semantic_failed:

        # 长错误消息独立成变量，避免问题登记行过宽。
        str_message = (  # 语义执行未就绪诊断
            "Semantic execution is not ready; "  # 语义执行失败诊断前半句
            "reference cases or checkpoints are missing or mismatched."  # 缺失或不匹配原因
        )

        # 未就绪说明 testbench 语义闭环需要修复。
        list_issues.append(_issue("error", str_message, source="testbench_issue"))

    # 用例覆盖不一致时记录阻断级问题。
    if mismatched_cases:

        # case coverage 不一致会让 RTL 与 reference 不可比。
        list_issues.append(
            _issue("error", "Semantic case coverage differs from the reference contract.", source="testbench_issue")
        )

    # checkpoint drift 表示语义观测点不一致。
    if list_checkpoint_drift:

        # checkpoint 漂移需要在 reference 或 RTL transcript 中修复。
        list_issues.append(_issue("error", "Semantic checkpoint drift was detected.", source="testbench_issue"))

    # transcript 中存在失败 case 时记录阻断级问题。
    if failed_cases:

        # failed_cases 表示语义执行已经跑通但用例结果失败。
        str_message = "Semantic transcript contains failed reference cases."  # transcript 失败用例诊断

        # 失败用例来自 testbench 语义执行链路。
        list_issues.append(_issue("error", str_message, source="testbench_issue"))

    # 返回字段保持旧版语义执行摘要形状。
    return {
        "issues": list_issues,
        "semantic_ready": value_semantic_ready,
        "mismatched_cases": mismatched_cases,
        "checkpoint_drift": list_checkpoint_drift,
        "failed_cases": failed_cases,
        "localization_confidence": dict_semantic_execution.get("localization_confidence"),
    }

# issue 构造器统一生成 severity/source/message 三字段诊断。
def _issue(severity: str, message: str, *, source: str = "current_module_issue") -> dict[str, Any]:
    """生成 workflow 报告使用的最小 issue 对象。

    参数:
        severity: 问题严重程度。
        message: 人类可读诊断文本。
        source: 问题来源分类，默认归入当前模块。

    返回:
        包含 severity、source 和 message 的 issue 字典。
    """

    # 返回最小 issue 结构，保持 workflow 报告兼容。
    return {"severity": severity, "source": source, "message": message}
