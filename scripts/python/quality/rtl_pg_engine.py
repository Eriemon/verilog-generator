"""执行固定 PG1001-PG1072 RTL 门禁并生成逐规则报告。"""

# future annotations 延后解析 PG 数据模型类型。
from __future__ import annotations

# pathlib 提供统一的 RTL 目标路径处理。
from pathlib import Path

# typing 描述公开报告与规格字典的动态字段。
from typing import Any

# catalog loader 是固定编号、等级和激活状态的唯一来源。
from scripts.python.workflow.rtl_md_constraints import load_rtl_md_constraints

# 各规则模块按语义域执行固定编号。
from .rtl_pg_branch_rules import evaluate_branch_gate
from .rtl_pg_clock_rules import evaluate_clock_gate
from .rtl_pg_control_rules import evaluate_control_gate
from .rtl_pg_driver_rules import evaluate_driver_gate
from .rtl_pg_expression_rules import evaluate_expression_gate

# 状态机、复位和结构模块承载本轮新增的语义域。
from .rtl_pg_fsm_rules import evaluate_fsm_gate
from .rtl_pg_reset_rules import evaluate_reset_gate
from .rtl_pg_structure_rules import evaluate_structure_gate

# 子程序规则独立处理 function 和 task 合同。
from .rtl_pg_subprogram_rules import evaluate_subprogram_gate

# facts 在单次扫描中缓存 formatter AST 与源码事实。
from .rtl_pg_facts import PgFacts, build_pg_facts

# models 统一逐门禁状态和证据结构。
from .rtl_pg_models import PgEvaluation, PgFinding

# 公开状态集合用于拒绝规则实现返回的未知状态。
PG_RESULT_STATUSES = frozenset(  # 固定 PG 报告允许出现的状态集合
    {"passed", "failed", "inconclusive", "error", "not_run", "reserved"}  # 全部合法状态值
)

# 以下集合只负责把激活编号路由到对应语义模块。
EXPRESSION_GATES = frozenset(  # 表达式、条件与位宽规则编号
    {"PG1001", "PG1003", "PG1007", "PG1014", "PG1030", "PG1051", "PG1063", "PG1066", "PG1067"}  # 表达式模块负责的编号
)

# 分支模块承载第二批 case、条件和互斥赋值路径规则。
BRANCH_GATES = frozenset(  # 分支、case 与路径赋值规则编号
    {"PG1005", "PG1013", "PG1017", "PG1019", "PG1032", "PG1034", "PG1038"}  # 第二批分支规则固定编号
)

# 时钟模块承载时钟域、来源、门控、边沿和非时钟用途规则。
CLOCK_GATES = frozenset(  # 时钟语义域固定编号
    {"PG1002", "PG1018", "PG1025", "PG1036", "PG1049", "PG1061"}  # 当前激活的时钟规则编号
)

# 子程序模块集中处理 function 和 task 规则。
SUBPROGRAM_GATES = frozenset(  # 函数与任务规则编号集合
    {"PG1035", "PG1044", "PG1050", "PG1056", "PG1062", "PG1068"}  # 函数与任务规则编号
)

# 驱动模块集中处理声明类型与驱动所有权规则。
DRIVER_GATES = frozenset({"PG1069", "PG1070", "PG1071"})  # 声明与驱动来源规则编号

# 复位模块集中处理同步异步模式、控制数量、覆盖和极性一致性。
RESET_GATES = frozenset(  # 复位和触发器初始化规则编号
    {"PG1004", "PG1006", "PG1012", "PG1022", "PG1029", "PG1031", "PG1039", "PG1042", "PG1045", "PG1047"}  # 全部复位域编号
)

# FSM 模块集中处理初态、非法状态恢复、可达性和状态数量规则。
FSM_GATES = frozenset({"PG1015", "PG1023", "PG1027", "PG1041"})  # 状态机结构规则编号

# 结构模块集中处理锁存环、数组常量边界和普通组合反馈。
STRUCTURE_GATES = frozenset({"PG1033", "PG1059", "PG1065"})  # 组合结构规则编号

# run_rtl_pg_gate 是固定 PG 系列的公开执行入口。
def run_rtl_pg_gate(
    root: Path,
    *,
    spec: dict[str, Any] | None = None,
    strict: bool = True,
    include_testbench: bool = False,
) -> dict[str, Any]:
    """运行全部固定 PG 门禁并返回 fail-closed 报告。

    参数:
        root: 待检查的 Verilog 文件或目录。
        spec: 可选归一化设计规格，用于接口和时钟语义补充。
        strict: 是否让激活 WARNING 的非通过状态阻断交付。
        include_testbench: 是否把 testbench 文件纳入设计 RTL 检查。

    返回:
        包含 72 条逐门禁结果、摘要和交付结论的字典。
    """

    # 绝对路径保证报告和 formatter 扫描使用同一目标。
    path_root = root.resolve()  # 待检查 RTL 根路径

    # catalog 决定 72 条规则的固定顺序和治理元数据。
    dict_catalog = load_rtl_md_constraints()  # 已校验的 RTL PG 目录

    # 单次事实构建避免每条规则重复解析 RTL。
    pg_facts_facts: PgFacts = build_pg_facts(  # 当前目标的可信 PG 扫描事实
        path_root,  # 统一后的绝对目标路径
        spec=spec,  # 可选设计规格
        include_testbench=include_testbench,  # testbench 扫描边界
    )

    # 结果列表严格按 catalog 顺序保留全部激活和预留编号。
    list_results: list[dict[str, Any]] = []  # 72 条逐门禁结果

    # 每条 catalog 记录必须产生且仅产生一条公开结果。
    for dict_rule in dict_catalog["rules"]:

        # 预留规则也通过统一入口生成 reserved 结果。
        list_results.append(_evaluate_catalog_rule(dict_rule, pg_facts_facts))

    # 状态摘要用于快速核对固定目录的完整覆盖。
    dict_summary = _summarize_results(list_results)  # 固定 PG 状态与目录计数

    # 交付问题只包含当前模式下真正阻断的固定编号。
    dict_delivery_issues = _delivery_issues(list_results, strict=strict)  # PG 编号到证据数量

    # 空阻断字典是唯一的可交付条件。
    bool_delivery_ready = not dict_delivery_issues  # 当前 strict 策略下的交付结论

    # 公开报告由独立构造函数保持执行流程紧凑。
    return _build_report(
        path_root=path_root,
        strict=strict,
        catalog_version=str(dict_catalog["version"]),
        delivery_ready=bool_delivery_ready,
        delivery_issues=dict_delivery_issues,
        summary=dict_summary,
        results=list_results,
    )

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
    """构造可序列化的固定 PG 公开报告。

    参数:
        path_root: 已解析的 RTL 目标绝对路径。
        strict: 当前 WARNING 阻断策略。
        catalog_version: 当前固定 PG catalog 版本。
        delivery_ready: 由阻断字典推导的交付结论。
        delivery_issues: 固定 PG 编号到阻断数量的映射。
        summary: 全部规则的目录和状态摘要。
        results: 按固定编号顺序生成的逐规则结果。
    返回:
        上层交付门可直接合并的 PG 报告字典。
    """

    # 修复与重跑布尔值始终与 delivery_ready 互为反值。
    return {
        "version": 1,
        "pg_catalog_version": catalog_version,
        "root": str(path_root),
        "strict": strict,
        "delivery_ready": delivery_ready,
        "repair_required": not delivery_ready,
        "rerun_required_after_repair": not delivery_ready,
        "delivery_issues_by_rule": delivery_issues,
        "pg_gate_summary": summary,
        "pg_gate_results": results,
    }

# _evaluate_catalog_rule 保证每个 catalog 条目都有公开结果。
def _evaluate_catalog_rule(dict_rule: dict[str, Any], facts: PgFacts) -> dict[str, Any]:
    """执行单条 catalog 规则或生成 reserved 状态。

    参数:
        dict_rule: 当前固定 PG 规则的 catalog 元数据。
        facts: 当前 RTL 目标的共享解析事实。
    返回:
        合并 catalog 元数据与执行结论的公开结果字典。
    """

    # 固定编号同时用于路由和最终报告主键。
    str_gate_id = str(dict_rule["gate_id"])  # 当前 catalog 规则编号

    # 预留条目不得执行，也不得伪装成 passed。
    if dict_rule["status"] == "reserved":

        # reserved 明确表明该编号尚未进入当前自动门禁批次。
        return _result_dict(dict_rule, PgEvaluation("reserved", False, message="Reserved guidance is not executed."))

    # 没有 RTL 输入时激活门禁统一 fail-closed 为 not_run。
    if not facts.sources:

        # not_run 与规则通过保持可机器区分。
        return _result_dict(dict_rule, PgEvaluation("not_run", False, message="No Verilog source was discovered."))

    # formatter AST 失败会使所有激活规则失去可信输入。
    if facts.parse_errors:

        # error 状态阻止任何规则在不可信 AST 上报告 passed。
        return _result_dict(dict_rule, _parse_error_evaluation(facts.parse_errors))

    # 激活编号交给唯一对应的语义模块执行。
    pg_evaluation_pg_evaluation: PgEvaluation = _run_active_evaluator(  # 当前固定规则的执行结论
        str_gate_id,  # 当前激活 PG 编号
        facts,  # 共享解析事实
    )

    # 未知状态属于规则实现错误，不能穿透公开报告。
    if pg_evaluation_pg_evaluation.status not in PG_RESULT_STATUSES:

        # 统一降级为阻断性的 error 状态。
        pg_evaluation_pg_evaluation: PgEvaluation = PgEvaluation(  # 替换非法状态后的错误结论
            "error",  # 公开错误状态
            True,  # 激活规则具有适用性
            message="Evaluator returned an unsupported status.",  # 未知状态诊断
        )

    # catalog 元数据和规则证据在单一出口合并。
    return _result_dict(dict_rule, pg_evaluation_pg_evaluation)

# _parse_error_evaluation 把 formatter 故障转换为统一 fail-closed 结论。
def _parse_error_evaluation(list_parse_errors: list[dict[str, Any]]) -> PgEvaluation:
    """生成包含全部 formatter AST 故障位置的 PG 错误结论。

    参数:
        list_parse_errors: formatter AST 返回的结构化解析错误。
    返回:
        可供任一激活规则复用的阻断性 error 结论。
    """

    # 每条解析错误都保留文件、行号、诊断文本和 formatter 错误码。
    return PgEvaluation(
        "error",
        True,
        tuple(
            PgFinding(
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
def _run_active_evaluator(str_gate_id: str, facts: PgFacts) -> PgEvaluation:
    """把激活门禁路由到对应的规则模块。

    参数:
        str_gate_id: 已确认激活的固定 PG 编号。
        facts: 当前 RTL 目标的共享解析事实。
    返回:
        对应语义模块生成的逐门禁结论。
    """

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

# _result_dict 集中定义单条 PG 结果的公开字段。
def _result_dict(dict_rule: dict[str, Any], evaluation: PgEvaluation) -> dict[str, Any]:
    """合并 catalog 元数据与执行结论。

    参数:
        dict_rule: 当前固定规则的 catalog 元数据。
        evaluation: 对应规则实现生成的执行结论。
    返回:
        可序列化的单条 PG 公开结果。
    """

    # 所有字段在此集中映射，避免各规则模块形成报告漂移。
    return {
        "gate_id": dict_rule["gate_id"],
        "rule_key": dict_rule["rule_key"],
        "level": dict_rule["level"],
        "catalog_status": dict_rule["status"],
        "status": evaluation.status,
        "applicable": evaluation.applicable,
        "message": evaluation.message,
        "findings": [obj_finding.to_dict() for obj_finding in evaluation.findings],
    }

# _summarize_results 同时统计目录状态和执行状态。
def _summarize_results(list_results: list[dict[str, Any]]) -> dict[str, Any]:
    """按状态和目录类别汇总 72 条结果。

    参数:
        list_results: 按 catalog 顺序生成的全部规则结果。
    返回:
        总数、激活数、预留数和各状态计数。
    """

    # 即使某状态计数为零也保留稳定字段。
    dict_status_counts = {  # 公开状态到当前命中数量的映射
        str_status: 0  # 当前允许状态的初始计数
        for str_status in sorted(PG_RESULT_STATUSES)  # 固定排序保证报告稳定
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
        阻断性固定 PG 编号到证据数量的映射。
    """

    # 字典只收集当前模式下的阻断项。
    dict_issues: dict[str, int] = {}  # 固定 PG 编号到阻断证据数量

    # reserved 与 passed 结果始终不会进入交付问题集合。
    for dict_result in list_results:

        # 只处理激活且未通过的规则。
        if dict_result["catalog_status"] != "active" or dict_result["status"] == "passed":

            # 当前结果不具备阻断资格。
            continue

        # non-strict 模式允许 WARNING 结果保留但不阻断。
        if dict_result["level"] == "WARNING" and not strict:

            # 警告仍留在逐规则报告中，无需加入阻断字典。
            continue

        # 没有定位 finding 的 fail-closed 状态仍至少计为一个问题。
        int_count = max(1, len(dict_result["findings"]))  # 当前规则的阻断证据数量

        # 固定编号是公开交付问题的唯一键。
        dict_issues[str(dict_result["gate_id"])] = int_count  # 登记当前规则阻断计数

    # 空字典表示当前 strict 策略下没有阻断项。
    return dict_issues
