"""执行统一 Verilog VG 语义门禁并生成逐规则报告。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# pathlib 提供统一的 RTL 目标路径处理。
from pathlib import Path

# typing 描述公开报告与规格字典的动态字段。
from typing import Any

# catalog loader 是固定编号、等级和激活状态的唯一来源。
from scripts.python.workflow.verilog_gate_catalog import COMB_OPERATION_LIMIT_KEY, load_verilog_quality_gates

# 各规则模块按语义域执行固定编号。
from .vg_branch_rules import evaluate_branch_gate
from .vg_clock_rules import evaluate_clock_gate
from .vg_control_rules import evaluate_control_gate
from .vg_driver_rules import evaluate_driver_gate
from .vg_expression_rules import evaluate_expression_gate

# vg_comb_cone 只消费 formatter 类型化表达式事实并统一执行 VG146/VG147。
from .vg_comb_cone import build_comb_target_cones, evaluate_comb_operation_gate

# 状态机、复位和结构模块承载本轮新增的语义域。
from .vg_fsm_rules import evaluate_fsm_gate
from .vg_file_rules import evaluate_file_gate
from .vg_reset_rules import evaluate_reset_gate
from .vg_structure_rules import evaluate_structure_gate

# 子程序规则独立处理 function 和 task 合同。
from .vg_subprogram_rules import evaluate_subprogram_gate

# facts 在单次扫描中缓存 formatter AST 与源码事实。
from .vg_semantic_facts import VgFacts, build_vg_facts

# models 统一逐门禁状态和证据结构。
from .vg_rule_models import VgEvaluation, VgFinding, passed

# 公开状态集合用于拒绝规则实现返回的未知状态。
VG_RESULT_STATUSES = frozenset(  # 统一 VG 报告允许出现的状态集合
    {"passed", "failed", "inconclusive", "error", "not_run"}  # 全部合法状态值
)

# 以下集合只负责把激活编号路由到对应语义模块。
EXPRESSION_GATES = frozenset(  # 表达式、条件与位宽规则编号
    {"VG072", "VG074", "VG078", "VG085", "VG101", "VG122", "VG125", "VG134", "VG137", "VG138"}  # 表达式模块负责的编号
)

# 分支模块承载第二批 case、条件和互斥赋值路径规则。
BRANCH_GATES = frozenset(  # 分支、case 与路径赋值规则编号
    {"VG076", "VG084", "VG088", "VG090", "VG103", "VG105", "VG109"}  # 第二批分支规则固定编号
)

# 时钟模块承载时钟域、来源、门控、边沿和非时钟用途规则。
CLOCK_GATES = frozenset(  # 时钟语义域固定编号
    {"VG073", "VG089", "VG096", "VG107", "VG120", "VG132"}  # 当前激活的时钟规则编号
)

# 子程序模块集中处理 function 和 task 规则。
SUBPROGRAM_GATES = frozenset(  # 函数与任务规则编号集合
    {"VG106", "VG115", "VG121", "VG127", "VG133", "VG139"}  # 函数与任务规则编号
)

# 驱动模块集中处理声明类型与驱动所有权规则。
DRIVER_GATES = frozenset({"VG140", "VG141", "VG142"})  # 声明与驱动来源规则编号

# 复位模块集中处理同步异步模式、控制数量、覆盖和极性一致性。
RESET_GATES = frozenset(  # 复位和触发器初始化规则编号
    {"VG075", "VG077", "VG082", "VG083", "VG093", "VG099", "VG100", "VG102", "VG110", "VG113", "VG116", "VG118"}  # 全部复位域编号
)

# FSM 模块集中处理初态、非法状态恢复、可达性和状态数量规则。
FSM_GATES = frozenset({"VG086", "VG094", "VG098", "VG112", "VG119", "VG144"})  # 状态机结构规则编号

# 结构模块集中处理锁存环、数组常量边界和普通组合反馈。
STRUCTURE_GATES = frozenset({"VG104", "VG130", "VG136", "VG145"})  # 组合结构规则编号

# 组合预算规则共享一次分析合同，但按是否包含 for 克隆节点分配所有权。
COMB_OPERATION_GATES = frozenset({"VG146", "VG147"})  # 普通与循环组合操作预算编号

# 文件命名规则独立于 formatter AST 来源与解析状态执行。
FILE_NAMING_GATES = frozenset({"VG148", "VG149"})  # .v/.sv 文件级预检编号

# run_vg_semantic_gate 是迁移语义规则的内部执行入口。
def run_vg_semantic_gate(
    root: Path,
    *,
    spec: dict[str, Any] | None = None,
    strict: bool = True,
    include_testbench: bool = False,

    # 以下参数控制预构建事实和显式外部接口来源。
    facts: VgFacts | None = None,
    external_interface_sources: tuple[Path, ...] = (),
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """运行 VG072 至 VG149 语义门禁并返回 fail-closed 报告。

    参数:
        root: 待检查的 Verilog 文件或目录。
        spec: 可选归一化设计规格，用于接口和时钟语义补充。
        strict: 是否让激活 WARNING 的非通过状态阻断交付。
        include_testbench: 是否把 testbench 文件纳入设计 RTL 检查。
        facts: 可选的预构建 VG 事实；提供时禁止再次解析 RTL。
        external_interface_sources: 未提供预构建事实时装载的外部接口 stub 来源。
        catalog: 可选的已验证统一目录；由外层质量门提供时禁止再次加载。

    返回:
        包含 78 条逐门禁结果、摘要和交付结论的字典。
    """

    # 绝对路径保证报告和 formatter 扫描使用同一目标。
    path_root = root.resolve()  # 待检查 RTL 根路径

    # 外层统一质量门可复用其目录；直接调用时仍只加载一次权威 JSON。
    dict_catalog = (  # 本轮语义执行唯一使用的已验证 VG 目录
        load_verilog_quality_gates()  # 直接语义入口自行加载目录
        if catalog is None  # 只有调用方未提供目录时才读取资产
        else catalog  # 复用外层质量门已经验证的目录对象
    )

    # loader 已严格校验配置类型和正整数范围，此处只读取一次共享阈值。
    int_comb_operation_limit = int(dict_catalog["config"][COMB_OPERATION_LIMIT_KEY])  # 每目标组合操作预算

    # 单次事实构建避免每条规则重复解析 RTL。
    vg_facts_vg_facts: VgFacts = facts or build_vg_facts(  # 当前目标的可信 VG 扫描事实
        path_root,  # 待分析的规范 RTL 入口
        spec=spec,  # 可选设计规格
        include_testbench=include_testbench,  # testbench 纳入策略
        external_interface_sources=external_interface_sources,  # 显式外部接口 stub
    )

    # 两条组合预算规则共享一次不可变锥构建，禁止重复分析产生漂移。
    tuple_comb_cones = build_comb_target_cones(vg_facts_vg_facts)  # VG146/VG147 共享目标锥快照

    # 结果列表严格按 catalog 顺序保留全部激活和预留编号。
    list_results: list[dict[str, Any]] = []  # 78 条逐门禁结果

    # 只执行迁移后的语义段；既有 VG000-VG071 由统一质量门原生规则负责。
    for dict_rule in dict_catalog["rules"]:

        # 历史 VG 规则不在语义引擎内重复执行。
        if int(str(dict_rule["gate_id"])[2:]) < 72:

            # 统一报告层会为该规则合并原生 QualityIssue。
            continue

        # 预留规则也通过统一入口生成 reserved 结果。
        list_results.append(
            _evaluate_catalog_rule(
                dict_rule,
                vg_facts_vg_facts,
                int_comb_operation_limit,
                tuple_comb_cones,
            )
        )

    # 状态摘要用于快速核对固定目录的完整覆盖。
    dict_summary = _summarize_results(list_results)  # 固定 VG 状态与目录计数

    # 交付问题只包含当前模式下真正阻断的固定编号。
    dict_delivery_issues = _delivery_issues(list_results, strict=strict)  # VG 编号到证据数量

    # 空阻断字典是唯一的可交付条件。
    bool_delivery_ready = not dict_delivery_issues  # 当前 strict 策略下的交付结论

    # 公开报告由独立构造函数保持执行流程紧凑。
    dict_report = _build_report(  # 当前语义门禁的稳定公开报告
        path_root=path_root,  # 报告绑定的规范扫描根
        strict=strict,  # WARNING 级规则阻断策略
        catalog_version=str(dict_catalog["version"]),  # 目录版本
        delivery_ready=bool_delivery_ready,  # 当前交付结论
        delivery_issues=dict_delivery_issues,  # 规则阻断计数
        summary=dict_summary,  # 全部状态汇总
        results=list_results,  # catalog 顺序的逐规则结果
    )

    # 文件事实作为加法字段公开，不扩大报告构造 helper 的参数面。
    dict_report["file_facts"] = [  # 宿主确认流程消费的公开文件角色事实
        file_fact.to_dict() for file_fact in vg_facts_vg_facts.files  # 移除内部绝对路径
    ]

    # 返回同时包含逐规则结果和文件角色事实的报告。
    return dict_report

# _build_report 统一稳定的公开字段和修复语义。
def _build_report(
    *,
    path_root: Path,
    strict: bool,
    catalog_version: str,

    # 以下参数承载执行结论和完整逐规则结果。
    delivery_ready: bool,
    delivery_issues: dict[str, int],
    summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造可序列化的固定 VG 公开报告。

    参数:
        path_root: 已解析的 RTL 目标绝对路径。
        strict: 当前 WARNING 阻断策略。
        catalog_version: 当前固定 VG catalog 版本。
        delivery_ready: 由阻断字典推导的交付结论。
        delivery_issues: 固定 VG 编号到阻断数量的映射。
        summary: 全部规则的目录和状态摘要。
        results: 按固定编号顺序生成的逐规则结果。
    返回:
        上层交付门可直接合并的 VG 报告字典。
    """

    # 修复与重跑布尔值始终与 delivery_ready 互为反值。
    return {
        "version": 2,
        "vg_catalog_version": int(catalog_version),
        "root": str(path_root),
        "strict": strict,
        "delivery_ready": delivery_ready,
        "repair_required": not delivery_ready,
        "rerun_required_after_repair": not delivery_ready,
        "delivery_issues_by_rule": delivery_issues,
        "vg_rule_summary": summary,
        "vg_rule_results": results,
    }

# _evaluate_catalog_rule 保证每个 catalog 条目都有公开结果。
def _evaluate_catalog_rule(
    dict_rule: dict[str, Any],
    facts: VgFacts,
    int_comb_operation_limit: int,
    tuple_comb_cones: tuple[Any, ...],
) -> dict[str, Any]:
    """执行单条 catalog 规则或生成 reserved 状态。

    参数:
        dict_rule: 当前固定 VG 规则的 catalog 元数据。
        facts: 当前 RTL 目标的共享解析事实。
        int_comb_operation_limit: 已校验的每目标组合操作预算。
        tuple_comb_cones: VG146/VG147 共享的不可变组合锥快照。
    返回:
        合并 catalog 元数据与执行结论的公开结果字典。
    """

    # 固定编号同时用于路由和最终报告主键。
    str_gate_id = str(dict_rule["gate_id"])  # 当前 catalog 规则编号

    # 文件门禁必须先于 AST 空输入与解析错误短路独立执行。
    if str_gate_id in FILE_NAMING_GATES:

        # VG148/VG149 只读取独立收集的 .v/.sv 文件事实。
        vg_file_evaluation = evaluate_file_gate(str_gate_id, facts.files)  # 当前文件门结论

        # 文件门结论沿用统一 catalog 报告 shape。
        return _result_dict(dict_rule, vg_file_evaluation)

    # 没有 RTL 输入时激活门禁统一 fail-closed 为 not_run。
    if not facts.sources:

        # not_run 与规则通过保持可机器区分。
        return _result_dict(dict_rule, VgEvaluation("not_run", False, message="No Verilog source was discovered."))

    # formatter AST 失败会使所有激活规则失去可信输入。
    if facts.parse_errors:

        # error 状态阻止任何规则在不可信 AST 上报告 passed。
        return _result_dict(dict_rule, _parse_error_evaluation(facts.parse_errors))

    # 激活编号交给唯一对应的语义模块执行。
    vg_evaluation_obj_evaluation: VgEvaluation = _run_active_evaluator(  # 当前固定规则的执行结论
        str_gate_id,  # 当前激活 VG 编号
        facts,  # 共享解析事实
        int_comb_operation_limit,  # 目录拥有的组合操作预算
        tuple_comb_cones,  # 两条组合预算规则共用一次分析结果
    )

    # 未知状态属于规则实现错误，不能穿透公开报告。
    if vg_evaluation_obj_evaluation.status not in VG_RESULT_STATUSES:

        # 统一降级为阻断性的 error 状态。
        vg_evaluation_obj_evaluation = VgEvaluation(  # 替换非法状态后的错误结论
            "error",  # 公开错误状态
            True,  # 激活规则具有适用性
            message="Evaluator returned an unsupported status.",  # 未知状态诊断
        )

    # catalog 元数据和规则证据在单一出口合并。
    return _result_dict(dict_rule, vg_evaluation_obj_evaluation)

# _parse_error_evaluation 把 formatter 故障转换为统一 fail-closed 结论。
def _parse_error_evaluation(list_parse_errors: list[dict[str, Any]]) -> VgEvaluation:
    """生成包含全部 formatter AST 故障位置的 VG 错误结论。

    参数:
        list_parse_errors: formatter AST 返回的结构化解析错误。
    返回:
        可供任一激活规则复用的阻断性 error 结论。
    """

    # 每条解析错误都保留文件、行号、诊断文本和 formatter 错误码。
    return VgEvaluation(
        "error",
        True,
        tuple(
            VgFinding(
                str(dict_error.get("path") or ""),
                int(dict_error.get("line") or 1),
                str(dict_error.get("message") or "Formatter AST parse failed."),
                str(dict_error.get("code") or "FORMATTER_AST_PARSE"),
            )
            for dict_error in list_parse_errors
        ),
        "Formatter AST parse failed.",
    )

# _run_active_evaluator 维护固定编号到规则模块的唯一映射。
def _run_active_evaluator(
    str_gate_id: str,
    facts: VgFacts,
    int_comb_operation_limit: int,
    tuple_comb_cones: tuple[Any, ...],
) -> VgEvaluation:
    """把激活门禁路由到对应的规则模块。

    参数:
        str_gate_id: 已确认激活的固定 VG 编号。
        facts: 当前 RTL 目标的共享解析事实。
        int_comb_operation_limit: 已校验的每目标组合操作预算。
        tuple_comb_cones: 单次运行预构建的组合锥快照。
    返回:
        对应语义模块生成的逐门禁结论。
    """

    # 文件门路由保留显式分支，供直接 evaluator 调用保持一致。
    if str_gate_id in FILE_NAMING_GATES:

        # 文件门只消费预检事实，不读取 formatter AST。
        return evaluate_file_gate(str_gate_id, facts.files)

    # 新组合预算组必须先于旧结构组接管 VG146/VG147。
    if str_gate_id in COMB_OPERATION_GATES:

        # 两条规则共享 typed-fact 分析器和目录阈值。
        return evaluate_comb_operation_gate(
            str_gate_id,
            facts,
            int_comb_operation_limit,
            cones=tuple_comb_cones,
        )

    # 表达式组覆盖字面量、条件和位宽语义。
    if str_gate_id in EXPRESSION_GATES:

        # 表达式规则共享同一 facts 对象。
        return evaluate_expression_gate(str_gate_id, facts)

    # 分支组覆盖 case 标签、条件宽度和互斥控制路径。
    if str_gate_id in BRANCH_GATES:

        # 分支规则共享 formatter 控制树和 module 事实。
        return evaluate_branch_gate(str_gate_id, facts)

    # 时钟组覆盖边沿和门控时钟语义。
    if str_gate_id in CLOCK_GATES:

        # 时钟规则共享同一 facts 对象。
        return evaluate_clock_gate(str_gate_id, facts)

    # 子程序组覆盖 function/task 的可综合边界。
    if str_gate_id in SUBPROGRAM_GATES:

        # 子程序规则共享同一 facts 对象。
        return evaluate_subprogram_gate(str_gate_id, facts)

    # 驱动组覆盖 wire 和多来源所有权。
    if str_gate_id in DRIVER_GATES:

        # 驱动规则共享同一 facts 对象。
        return evaluate_driver_gate(str_gate_id, facts)

    # 复位组覆盖模式混用、循环边界、目标覆盖和触发极性。
    if str_gate_id in RESET_GATES:

        # 复位规则共享 formatter always 与控制树事实。
        return evaluate_reset_gate(str_gate_id, facts)

    # FSM 组覆盖复位入口、默认恢复、状态图和规模边界。
    if str_gate_id in FSM_GATES:

        # FSM 规则共享可信 module 文本并只在完整状态图上作结论。
        return evaluate_fsm_gate(str_gate_id, facts)

    # 结构组覆盖锁存反馈、数组越界和普通组合环。
    if str_gate_id in STRUCTURE_GATES:

        # 结构规则共享可信 module 文本并按层级隔离依赖图。
        return evaluate_structure_gate(str_gate_id, facts)

    # 其余激活编号属于既有控制结构组。
    return evaluate_control_gate(str_gate_id, facts)

# 组合预算 finding 按公开定位与层次 evidence 稳定排序。
def _ordered_findings(dict_rule: dict[str, Any], evaluation: VgEvaluation) -> tuple[VgFinding, ...]:
    """返回保持兼容或按组合预算身份排序的 finding 集合。

    参数:
        dict_rule: 当前固定规则的 catalog 元数据。
        evaluation: 对应规则实现生成的执行结论。

    返回:
        普通规则保持原顺序，VG146/VG147 使用确定性顺序的 finding 元组。
    """

    # 非组合预算规则继续保持各自既有 finding 顺序合同。
    if str(dict_rule["gate_id"]) not in COMB_OPERATION_GATES:

        # 原始不可变元组无需复制或重新排序。
        return evaluation.findings

    # evidence 已按固定 key=value 字段包含完整报告身份和 gate ID。
    return tuple(
        sorted(
            evaluation.findings,
            key=lambda obj_finding: (
                obj_finding.path,
                obj_finding.line,
                obj_finding.evidence,
                str(dict_rule["gate_id"]),
            ),
        )
    )

# 组合预算 finding 在 message 中镜像 evidence，供既有 issue-only Markdown writer 展示。
def _public_finding_dict(dict_rule: dict[str, Any], obj_finding: VgFinding) -> dict[str, Any]:
    """把 finding 映射为保持 schema-v2 的公开字典。

    参数:
        dict_rule: 当前固定规则的 catalog 元数据。
        obj_finding: 当前规则生成的不可变定位证据。

    返回:
        字段结构不变且组合预算 message 可被 Markdown 完整展示的字典。
    """

    # 标准模型先生成 path、line、message、evidence 和 severity 固定字段。
    dict_finding = obj_finding.to_dict()  # 当前 finding 的 schema-v2 字典

    # 普通 VG 规则继续保持既有 message 文本和 evidence 分离合同。
    if str(dict_rule["gate_id"]) not in COMB_OPERATION_GATES or not obj_finding.evidence:

        # 未变化的 finding 字典直接进入公开结果。
        return dict_finding

    # 既有 Markdown writer 只读取 message，因此镜像同一层次 evidence 文本。
    dict_finding["message"] = f"{obj_finding.message} Evidence: {obj_finding.evidence}"  # Markdown 可见组合证据

    # evidence 字段继续作为机器读取的权威结构化字符串。
    return dict_finding

# _result_dict 集中定义单条 VG 结果的公开字段。
def _result_dict(dict_rule: dict[str, Any], evaluation: VgEvaluation) -> dict[str, Any]:
    """合并 catalog 元数据与执行结论。

    参数:
        dict_rule: 当前固定规则的 catalog 元数据。
        evaluation: 对应规则实现生成的执行结论。
    返回:
        可序列化的单条 VG 公开结果。
    """

    # 组合预算结果在公开序列化前执行最后一道确定性 finding 排序。
    tuple_findings = _ordered_findings(dict_rule, evaluation)  # 当前规则公开输出顺序的 finding 集合

    # 所有字段在此集中映射，避免各规则模块形成报告漂移。
    return {
        "gate_id": dict_rule["gate_id"],
        "rule_key": dict_rule["rule_key"],
        "level": dict_rule["level"],
        "catalog_status": dict_rule["status"],
        "status": evaluation.status,
        "applicable": evaluation.applicable,
        "message": evaluation.message,
        "findings": [_public_finding_dict(dict_rule, obj_finding) for obj_finding in tuple_findings],
    }

# _summarize_results 同时统计目录状态和执行状态。
def _summarize_results(list_results: list[dict[str, Any]]) -> dict[str, Any]:
    """按状态和目录类别汇总 76 条结果。

    参数:
        list_results: 按 catalog 顺序生成的全部规则结果。
    返回:
        总数、激活数、预留数和各状态计数。
    """

    # 即使某状态计数为零也保留稳定字段。
    dict_status_counts = {  # 公开状态到当前命中数量的映射
        str_status: 0  # 当前允许状态的初始计数
        for str_status in sorted(VG_RESULT_STATUSES)  # 固定排序保证报告稳定
    }

    # 每条结果必须落入一个已声明状态桶。
    for dict_result in list_results:

        # 前置状态校验保证此处不会创建未知键。
        dict_status_counts[str(dict_result["status"])] += 1  # 累加当前规则状态

    # 目录计数与运行状态计数并列输出，避免语义混淆。
    return {
        "total": len(list_results),
        "active": sum(dict_result["catalog_status"] == "active" for dict_result in list_results),
        "reserved": sum(dict_result["catalog_status"] == "reserved" for dict_result in list_results),
        "status_counts": dict_status_counts,
    }

# _delivery_issues 应用 BLOCKER/WARNING 与 strict 的组合语义。
def _delivery_issues(list_results: list[dict[str, Any]], *, strict: bool) -> dict[str, int]:
    """根据 BLOCKER/WARNING 和 strict 语义生成交付问题计数。

    参数:
        list_results: 按 catalog 顺序生成的全部规则结果。
        strict: 是否让激活 WARNING 的非通过状态阻断交付。
    返回:
        阻断性固定 VG 编号到证据数量的映射。
    """

    # 字典只收集当前模式下的阻断项。
    dict_issues: dict[str, int] = {}  # 固定 VG 编号到阻断证据数量

    # reserved 与 passed 结果始终不会进入交付问题集合。
    for dict_result in list_results:

        # 只处理激活且未通过的规则。
        if dict_result["catalog_status"] != "active" or dict_result["status"] == "passed":

            # 当前结果不具备阻断资格。
            continue

        # finding 级 severity 优先；没有 finding 时继承 catalog 等级。
        set_finding_levels = {  # 当前规则实际发现携带的治理等级
            str(dict_finding.get("severity") or dict_result["level"])  # finding 或 catalog 治理等级
            for dict_finding in dict_result["findings"]  # 遍历当前规则全部定位证据
        }

        # non-strict 模式仅在全部发现都是 WARNING 时允许继续交付。
        if not strict and set_finding_levels and set_finding_levels <= {"WARNING"}:

            # 警告仍留在逐规则报告中，无需加入阻断字典。
            continue

        # 没有定位 finding 的 fail-closed 状态仍至少计为一个问题。
        int_count = max(1, len(dict_result["findings"]))  # 当前规则的阻断证据数量

        # 固定编号是公开交付问题的唯一键。
        dict_issues[str(dict_result["gate_id"])] = int_count  # 登记当前规则阻断计数

    # 空字典表示当前 strict 策略下没有阻断项。
    return dict_issues
