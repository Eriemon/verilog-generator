"""执行统一 Verilog VG 语义门禁并生成逐规则报告。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# pathlib 提供统一的 RTL 目标路径处理。
from pathlib import Path

# typing 描述公开报告与规格字典的动态字段。
from typing import Any

# catalog loader 是固定编号、等级和激活状态的唯一来源。
from scripts.python.workflow.verilog_gate_catalog import (
    COMB_OPERATION_LIMIT_KEY,
    PACKED_LOOKUP_LIMIT_KEY,
    load_verilog_quality_gates,
)

# v3 诊断适配器用于 parse error 和旧 finding 的统一公开字段。
from .vg_diagnostics import (
    VG_REPORT_VERSION,
    VgDiagnosticContractError,
    build_legacy_diagnostic,
)

# 各规则模块按语义域执行固定编号。
from .vg_branch_rules import evaluate_branch_gate
from .vg_clock_rules import evaluate_clock_gate
from .vg_control_rules import evaluate_control_gate
from .vg_driver_rules import evaluate_driver_gate
from .vg_expression_rules import evaluate_expression_gate

# vg_comb_cone 只消费 formatter 类型化表达式事实并统一执行 VG146/VG147。
from .vg_comb_cone import build_comb_target_cones, evaluate_comb_operation_gate

# vg_comment_integrity 只消费 formatter AST 注释候选并执行 VG150。
from .vg_comment_integrity import evaluate_comment_integrity_gate

# VG157 只消费 formatter 词法注释事实并豁免其识别出的固定文件头。
from .vg_comment_version import evaluate_comment_version_gate

# 参数合同、资源结构、生存性和 ready-valid 规则共享同一份 formatter 事实。
from .vg_contract_rules import (
    evaluate_handshake_gate,
    evaluate_liveness_gate,
    evaluate_parameter_gate,
    evaluate_resource_gate,
)

# 状态机、复位和结构模块承载本轮新增的语义域。
from .vg_fsm_rules import evaluate_fsm_gate
from .vg_file_rules import evaluate_file_gate
from .vg_identifier_rules import evaluate_identifier_gate
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

# 新增结构合同规则保持独立路由，避免名称或模块层级硬编码进入引擎。
PARAMETER_GATES = frozenset({"VG151"})  # 参数集合自动适用规则

# VG152 负责大型 packed 动态资源的结构事实。
RESOURCE_GATES = frozenset({"VG152"})  # packed 动态资源结构规则

# VG153/VG154 负责信号生存性和声明使用关系。
LIVENESS_GATES = frozenset({"VG153", "VG154"})  # 读取无驱动和未使用声明规则

# VG155 负责 ready-valid 消费条件的角色完整性。
HANDSHAKE_GATES = frozenset({"VG155"})  # ready-valid 完整性规则

# VG156/VG158 共享 formatter 声明事实和配置驱动的命名词表。
IDENTIFIER_GATES = frozenset({"VG156", "VG158"})  # 数字 token 与功能语义命名规则

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
    **dict_options: Any,
) -> dict[str, Any]:
    """运行 VG072 至 VG158 语义门禁并返回 fail-closed 报告。

    参数:
        root: 待检查的 Verilog 文件或目录。
        spec: 可选归一化设计规格，用于接口和时钟语义补充。
        strict: 是否让激活 WARNING 的非通过状态阻断交付。
        include_testbench: 是否把 testbench 文件纳入设计 RTL 检查。
        facts: 可选的预构建 VG 事实；提供时禁止再次解析 RTL。
        external_interface_sources: 未提供预构建事实时装载的外部接口 stub 来源。
        dict_options: 向后兼容关键字；支持 catalog 与 primitive_profile 两个可选覆盖项。
        catalog: 可选的已验证统一目录；由外层质量门提供时禁止再次加载。
        primitive_profile: 可选的 AMD-Xilinx 原语 catalog、resolved profile 或显式 profile。

    返回:
        包含 catalog 语义段逐门禁结果、摘要和交付结论的字典。
    异常:
        TypeError: 调用方传入未声明的扩展关键字。
        ValueError: 外层 catalog 或规则状态不满足 fail-closed 合同。
    """

    # 只接受治理合同中声明的两个扩展关键字，避免静默拼写错误。
    set_supported_options = {"catalog", "primitive_profile"}  # 允许的兼容关键字集合

    # 计算调用方传入但未被公共合同声明的关键字。
    set_unknown_options = set(dict_options) - set_supported_options  # 未声明的关键字集合

    # 未知关键字不能改变规则执行路径。
    if set_unknown_options:

        # 将拼写错误变成可审计的调用失败。
        raise TypeError("> ERR: [Python] run_vg_semantic_gate received an unsupported option.")

    # 从兼容关键字读取外层目录，保持旧调用方的 catalog 语义。
    dict_catalog_option: dict[str, Any] | None = dict_options.get("catalog")  # 外层传入的 VG catalog

    # 从兼容关键字读取 AMD-Xilinx 原语事实来源。
    dict_primitive_profile: dict[str, Any] | None = dict_options.get("primitive_profile")  # 原语目录或显式 profile

    # 绝对路径保证报告和 formatter 扫描使用同一目标。
    path_root = root.resolve()  # 待检查 RTL 根路径

    # 外层统一质量门可复用其目录；直接调用时仍只加载一次权威 JSON。
    dict_catalog = (  # 本轮语义执行唯一使用的已验证 VG 目录
        load_verilog_quality_gates()  # 直接语义入口自行加载目录
        if dict_catalog_option is None  # 只有调用方未提供目录时才读取资产
        else dict_catalog_option  # 复用外层质量门已经验证的目录对象
    )

    # loader 已严格校验配置类型和正整数范围，此处只读取一次共享阈值。
    int_comb_operation_limit = int(dict_catalog["config"][COMB_OPERATION_LIMIT_KEY])  # 每目标组合操作预算

    # packed 查表阈值同样由已校验目录提供，规则实现不内置业务数值。
    int_packed_lookup_limit = int(dict_catalog["config"][PACKED_LOOKUP_LIMIT_KEY])  # packed 动态查表位宽阈值

    # 单次事实构建避免每条规则重复解析 RTL。
    vg_facts_vg_facts: VgFacts = facts or build_vg_facts(  # 当前目标的可信 VG 扫描事实
        path_root,  # 待分析的规范 RTL 入口
        spec=spec,  # 可选设计规格
        include_testbench=include_testbench,  # testbench 纳入策略
        external_interface_sources=external_interface_sources,  # 显式外部接口 stub
        primitive_profile=dict_primitive_profile,  # 原语语义目录或显式 profile
    )

    # 两条组合预算规则共享一次不可变锥构建，禁止重复分析产生漂移。
    tuple_comb_cones = build_comb_target_cones(vg_facts_vg_facts)  # VG146/VG147 共享目标锥快照

    # 结果列表严格按 catalog 顺序保留全部激活和预留编号。
    list_results: list[dict[str, Any]] = []  # 语义段逐门禁结果

    # 只执行迁移后的语义段；既有 VG000-VG071 由统一质量门原生规则负责。
    for dict_rule in dict_catalog["rules"]:

        # 历史 VG 规则不在语义引擎内重复执行。
        if int(str(dict_rule["gate_id"])[2:]) < 72:

            # 统一报告层会为该规则合并原生 QualityIssue。
            continue

        # 预留规则也通过统一入口生成 reserved 结果。
        dict_evaluation = _evaluate_catalog_rule(  # 当前目录项的语义评估
            dict_rule,  # 当前 VG 规则定义
            vg_facts_vg_facts,  # 当前源码和 spec 事实
            int_comb_operation_limit,  # 组合操作预算
            int_packed_lookup_limit,  # packed 查表阈值
            tuple_comb_cones,  # 已构建的组合锥事实
        )

        # 按 catalog 顺序保存当前规则结果。
        list_results.append(dict_evaluation)

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

    # 声明区域事实直接发布 AST 已计算的共享映射，消费者无需再次推导优先级。
    dict_report["region_facts"] = _declaration_region_facts(vg_facts_vg_facts)  # 模块到声明区域的公开映射

    # 原语目录摘要进入报告，证明本轮不是 blanket whitelist。
    dict_report["primitive_semantics"] = _primitive_semantics_summary(  # 当前扫描使用的原语事实摘要
        getattr(vg_facts_vg_facts, "primitive_catalog", {}),  # 与规则共享的固定 catalog 快照
    )

    # 返回同时包含逐规则结果和文件角色事实的报告。
    return dict_report

# _declaration_region_facts 发布 formatter AST 中已经确认的声明区域。
def _declaration_region_facts(facts: VgFacts) -> dict[str, dict[str, str]]:
    """构造模块名到声明区域映射的公开报告字段。

    参数:
        facts: 当前语义门禁共享的 formatter AST 事实。
    返回:
        形如 `{module: {declaration: region}}` 的稳定映射。
    """

    # 模块映射按 source、module 和 declaration 的解析顺序稳定插入。
    dict_regions: dict[str, dict[str, str]] = {}  # 模块名到声明区域的公开映射

    # 每个来源只消费其唯一 formatter AST 报告。
    for source_facts in facts.sources:

        # 多 module 文件按 formatter 切分顺序发布声明事实。
        for dict_module in source_facts.report.get("modules", []) or []:

            # 缺少模块名的局部不完整事实不能形成公开索引键。
            str_module_name = str(dict_module.get("name") or "")  # 当前 module 名称

            # 未命名模块跳过，避免多个错误事实合并到空字符串键。
            if not str_module_name:

                # 继续处理同一来源中的下一个模块。
                continue

            # 同名模块事实按确定性扫描顺序合并声明映射。
            dict_module_regions = dict_regions.setdefault(str_module_name, {})  # 当前模块声明区域映射

            # 每个声明只发布 AST 已计算的名称和 region 字段。
            for dict_declaration in dict_module.get("decls", []) or []:

                # 空名称或空区域都不是可消费的共享归属事实。
                str_decl_name = str(dict_declaration.get("name") or "")  # 当前声明名称

                # region 必须由共享策略在 AST 序列化出口写入。
                str_region = str(dict_declaration.get("region") or "")  # 当前声明区域键

                # 两个字段都可信时才进入公开映射。
                if str_decl_name and str_region:

                    # 后续消费者直接复用此值，不再执行分类规则。
                    dict_module_regions[str_decl_name] = str_region  # 当前声明的共享区域归属

    # 返回保持插入顺序的普通字典，便于 JSON 确定性序列化。
    return dict_regions

# _primitive_semantics_summary 只公开目录身份和验证范围，不泄露全部 profile 细节。
def _primitive_semantics_summary(dict_catalog: dict[str, Any]) -> dict[str, Any]:
    """构造报告中的原语语义摘要。

    参数:
        dict_catalog: 当前 VG 扫描冻结的 AMD-Xilinx 原语目录。
    返回:
        包含目录身份、计数、冲突和验证范围的摘要字典。
    """

    # 摘要保留资产身份，方便审查报告追溯固定来源。
    dict_summary = {  # 把原语资产身份、清单计数、冲突和器件验证范围写入审查报告
        "catalog_id": dict_catalog.get("catalog_id", ""),  # 固定资产标识
        "schema_version": dict_catalog.get("schema_version"),  # 资产 schema 版本
        "counts": dict(dict_catalog.get("counts", {})),  # 三个 namespace 计数
        "conflicts": dict(dict_catalog.get("conflicts", {})),  # 显式 profile 冲突记录
        "validated_vivado_versions": list(dict_catalog.get("validated_vivado_versions", [])),  # 已验证 Vivado 版本
        "validated_parts": list(dict_catalog.get("validated_parts", [])),  # 已验证的 FPGA part 清单
    }

    # 返回可序列化的独立摘要。
    return dict_summary

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
        "version": VG_REPORT_VERSION,
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
    int_packed_lookup_limit: int,
    tuple_comb_cones: tuple[Any, ...],
) -> dict[str, Any]:
    """执行单条 catalog 规则或生成 reserved 状态。

    参数:
        dict_rule: 当前固定 VG 规则的 catalog 元数据。
        facts: 当前 RTL 目标的共享解析事实。
        int_comb_operation_limit: 已校验的每目标组合操作预算。
        int_packed_lookup_limit: VG152 使用的 packed 动态查表位宽阈值。
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
    vg_evaluation_result: VgEvaluation = _run_active_evaluator(  # 当前固定规则的执行结论
        str_gate_id,  # 当前激活 VG 编号
        facts,  # 共享解析事实
        int_comb_operation_limit,  # 目录拥有的组合操作预算
        int_packed_lookup_limit,  # 目录拥有的 packed 动态查表阈值
        tuple_comb_cones,  # 两条组合预算规则共用一次分析结果
    )

    # 未知状态属于规则实现错误，不能穿透公开报告。
    if vg_evaluation_result.status not in VG_RESULT_STATUSES:

        # 统一降级为阻断性的 error 状态。
        vg_evaluation_result: VgEvaluation = VgEvaluation(  # 替换非法状态后的错误结论
            "error",  # 公开错误状态
            True,  # 激活规则具有适用性
            message="Evaluator returned an unsupported status.",  # 未知状态诊断
        )

    # catalog 元数据和规则证据在单一出口合并。
    return _result_dict(dict_rule, vg_evaluation_result)

# _parse_error_finding 把单个 formatter 故障转换成操作性 error finding。
def _parse_error_finding(dict_error: dict[str, Any]) -> VgFinding:
    """构造不伪造行号的 formatter error finding。

    参数:
        dict_error: formatter 返回的单条解析错误。
    返回:
        带 error 状态和修复指导的 VG finding。
    """

    # path/line 只复用 formatter 的真实坐标，未知值保持 None。
    str_path = str(dict_error.get("path") or "") or None  # formatter 文件路径

    # 只有正整数行号才可以进入 source 定位。
    int_line_value = dict_error.get("line")  # formatter 返回的原始行号值

    # 仅把 formatter 明确确认的正整数保留为源码坐标。
    int_line = int(int_line_value) if isinstance(int_line_value, int) and int_line_value > 0 else None  # 可信源码行号

    # message 是 Agent 需要看到的解析失败事实。
    str_message = str(dict_error.get("message") or "Formatter AST parse failed.")  # 解析失败正文

    # code/evidence 保留 formatter 提供的可追溯错误信息。
    str_code = str(dict_error.get("code") or "FORMATTER_AST_PARSE")  # formatter 可追溯错误码

    # 构造不伪造来源坐标的 formatter error 诊断载荷。
    dict_diagnostic = build_legacy_diagnostic(  # formatter error v3 诊断载荷
        {
            "rule_id": "VG000",  # formatter error 的兼容规则编号
            "rule_key": "formatter_ast_parse",  # formatter error 的稳定机器键
            "severity": "BLOCKER",  # 解析失败始终阻断交付
            "path": str_path,  # formatter 提供的真实文件路径
            "line": int_line,  # formatter 提供的可选真实行号
            "message": str_message,  # Agent 需要修复的解析事实
            "evidence": str_code,  # 供宿主回溯 formatter 分支的标识码
            "status": "error",  # 解析失败的公开状态
        }
    )

    # 返回完整 finding，后续 _public_finding_dict 会绑定真实 gate_id。
    return VgFinding(
        path=str_path,
        line=int_line,
        message=str_message,
        evidence=str_code,
        severity="BLOCKER",
        diagnostic=dict_diagnostic,
    )

# _parse_error_evaluation 把 formatter 故障转换为统一 fail-closed 结论。
def _parse_error_evaluation(list_parse_errors: list[dict[str, Any]]) -> VgEvaluation:
    """生成包含全部 formatter AST 故障位置的 VG 错误结论。

    参数:
        list_parse_errors: formatter AST 返回的结构化解析错误。
    返回:
        可供任一激活规则复用的阻断性 error 结论。
    """

    # 仅有局部未闭合子程序时，AST 仍保留事实但不能给出确定门禁结论。
    set_error_codes = {str(dict_error.get("code") or "") for dict_error in list_parse_errors}  # parser 错误码集合

    # 局部不完整原因集合保持显式，避免把未来 parser 错误意外降级。
    set_incomplete_codes = {"FORMATTER_AST_INCOMPLETE_SUBPROGRAM"}  # 可返回 inconclusive 的错误码集合

    # 其他 parser error 表示整体 AST 不可信，继续使用 error 状态。
    bool_only_incomplete_subprogram = bool(set_error_codes) and set_error_codes == set_incomplete_codes  # 是否仅局部不完整

    # 局部覆盖不足使用 inconclusive，整体解析失败继续使用 error。
    str_status = "inconclusive" if bool_only_incomplete_subprogram else "error"  # 当前 parser 故障公开状态

    # 默认文案保持整体 formatter AST 失败的既有合同。
    str_message = "Formatter AST parse failed."  # 当前 parser 故障公开说明

    # 局部覆盖不足使用独立文案，避免把已保留事实误报为整体解析崩溃。
    if bool_only_incomplete_subprogram:

        # inconclusive 文案说明结论缺口来自覆盖不完整。
        str_message = "Formatter AST coverage is incomplete."  # 局部子程序覆盖不足说明

    # 每条解析错误都保留真实文件、可选行号和 formatter 错误码。
    return VgEvaluation(
        str_status,
        True,
        tuple(_parse_error_finding(dict_error) for dict_error in list_parse_errors),
        str_message,
    )

# _run_active_evaluator 维护固定编号到规则模块的唯一映射。
def _run_active_evaluator(
    str_gate_id: str,
    facts: VgFacts,
    int_comb_operation_limit: int,
    int_packed_lookup_limit: int,
    tuple_comb_cones: tuple[Any, ...],
) -> VgEvaluation:
    """把激活门禁路由到对应的规则模块。

    参数:
        str_gate_id: 已确认激活的固定 VG 编号。
        facts: 当前 RTL 目标的共享解析事实。
        int_comb_operation_limit: 已校验的每目标组合操作预算。
        int_packed_lookup_limit: 已校验的 packed 动态查表位宽阈值。
        tuple_comb_cones: 单次运行预构建的组合锥快照。
    返回:
        对应语义模块生成的逐门禁结论。
    """

    # 文件门需要独立的文件事实，不进入普通 module evaluator 表。
    if str_gate_id in FILE_NAMING_GATES:

        # 文件门只消费预检事实，不读取 formatter AST。
        return evaluate_file_gate(str_gate_id, facts.files)

    # 新组合预算组需要额外的阈值和共享锥快照。
    if str_gate_id in COMB_OPERATION_GATES:

        # 两条规则共享 typed-fact 分析器和目录阈值。
        return evaluate_comb_operation_gate(
            str_gate_id,
            facts,
            int_comb_operation_limit,
            cones=tuple_comb_cones,
        )

    # 参数合同按引用的公开参数集合自动适用，不读取模块名作用域。
    if str_gate_id in PARAMETER_GATES:

        # VG151 只消费设计需求中的参数合同与 formatter 参数事实。
        return evaluate_parameter_gate(facts)

    # packed 动态查表规则只把阈值交给资源形态分析器。
    if str_gate_id in RESOURCE_GATES:

        # VG152 不强制某一个 ROM 原语，只阻断不可识别的超大 packed 存储。
        return evaluate_resource_gate(facts, int_packed_lookup_limit)

    # 生存性规则共享一次结构化 driver/read 事实构建。
    if str_gate_id in LIVENESS_GATES:

        # VG153/VG154 均不依赖特定信号名或模块名。
        return evaluate_liveness_gate(facts, str_gate_id)

    # ready-valid 规则只消费 profile 角色和控制表达式事实。
    if str_gate_id in HANDSHAKE_GATES:

        # VG155 要求同一通道的 valid 与 ready 同时控制消费行为。
        return evaluate_handshake_gate(facts)

    # 声明命名规则只消费 module/function/task 中的变量声明事实。
    if str_gate_id in IDENTIFIER_GATES:

        # VG156 和 VG158 分别检查数字 token 与功能型主体。
        return evaluate_identifier_gate(str_gate_id, facts)

    # 版本字样规则只消费 formatter AST 已公开的词法注释事实。
    if str_gate_id == "VG157":

        # 固定双语文件头的 formatter 识别范围由注释事实显式标记豁免。
        return evaluate_comment_version_gate(facts)

    # 其余模块 evaluator 具有统一的 gate_id、facts 参数形状。
    tuple_evaluator_groups = (  # 固定 gate 集合到普通 evaluator 的一一映射
        (EXPRESSION_GATES, evaluate_expression_gate),  # 表达式、条件和位宽

        (BRANCH_GATES, evaluate_branch_gate),  # case 标签和互斥路径

        (CLOCK_GATES, evaluate_clock_gate),  # 时钟来源和边沿

        (SUBPROGRAM_GATES, evaluate_subprogram_gate),  # function/task 可综合边界合同

        (DRIVER_GATES, evaluate_driver_gate),  # 声明类型和驱动所有权

        (RESET_GATES, evaluate_reset_gate),  # 复位与触发器初始化

        (FSM_GATES, evaluate_fsm_gate),  # 状态机结构和可达性

        (STRUCTURE_GATES, evaluate_structure_gate),  # 组合结构和反馈
    )  # 普通 evaluator 的固定路由表

    # 按目录声明的 gate 集合查找唯一规则模块。
    for set_gate_ids, obj_evaluator in tuple_evaluator_groups:

        # 同一 gate 不应同时命中两个模块集合。
        if str_gate_id in set_gate_ids:

            # 共享 facts 让所有普通规则读取同一份解析快照。
            return obj_evaluator(str_gate_id, facts)

    # 注释完整性规则只消费实体注释候选，不读取第二套语法树。
    if str_gate_id == "VG150":

        # VG150 以配置驱动的流程证据和结构化尾族判定阻断注释幻觉。
        return evaluate_comment_integrity_gate(facts)

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
                obj_finding.path or "",
                obj_finding.line or 0,
                obj_finding.evidence,
                str(dict_rule["gate_id"]),
            ),
        )
    )

# 组合预算 finding 不再把 evidence 拼进 message，v3 字段保持扁平。
def _public_finding_dict(
    dict_rule: dict[str, Any],
    obj_finding: VgFinding,
    status: str,
) -> dict[str, Any]:
    """把 finding 绑定到 catalog 规则并输出 v3 字典。

    参数:
        dict_rule: 当前固定规则的 catalog 元数据。
        obj_finding: 当前规则生成的不可变定位证据。
        status: 当前规则执行状态。

    返回:
        包含 location、problem、evidence、guidance 的 v3 finding 字典。
    """

    # emitter 可为同一规则提供 BLOCKER/WARNING 两类证据，缺省才继承 catalog 等级。
    str_severity = str(obj_finding.severity or dict_rule.get("level") or "WARNING")  # 当前 finding 严重等级

    # 绑定真实 gate_id/rule_key，替换旧 emitter 的 VG000 占位。
    vg_finding_obj_bound: VgFinding = obj_finding.with_rule_context(  # 已绑定 catalog 规则上下文的 finding
        rule_id=str(dict_rule["gate_id"]),  # 当前 catalog 的固定规则编号
        rule_key=str(dict_rule["rule_key"]),  # 当前 catalog 的稳定机器键
        severity=str_severity,  # 当前 finding 或 catalog 治理等级
        status=status,  # 当前执行状态用于状态义务校验
    )  # 真实规则上下文的 finding

    # to_dict 输出 flat v3 字段，evidence 不再污染 message。
    return vg_finding_obj_bound.to_dict()

# _missing_finding 把适用但缺少坐标的状态转成项目级可执行诊断。
def _missing_finding(
    dict_rule: dict[str, Any],
    evaluation: VgEvaluation,
) -> VgFinding:
    """为没有 emitter finding 的 fail-closed 状态保留真实原因。

    参数:
        dict_rule: 当前固定 VG 规则的 catalog 元数据。
        evaluation: 当前规则返回的非通过状态和原因。
    返回:
        不伪造文件或行号、但包含修复指导的项目级 finding。
    """

    # 空 message 也必须有可读问题正文，避免 Agent 只看到状态码。
    str_problem = str(evaluation.message or "").strip() or (  # 当前规则的状态原因
        f"{dict_rule['gate_id']} ({dict_rule['rule_key']}) 返回了 {evaluation.status} 状态，"
        "但没有提供可验证的源码级证据。"
    )

    # evidence 明确说明坐标缺失的事实边界，禁止下游误解为精确行证据。
    str_evidence = (
        f"gate_id={dict_rule['gate_id']}; rule_key={dict_rule['rule_key']}; "
        f"status={evaluation.status}; applicable={evaluation.applicable}; "
        f"reason={str_problem}"
    )  # 项目级状态证据

    # 复用统一旧 finding 转换器补齐 guidance、风险和 bad/good 示例。
    return VgFinding(
        path=None,  # 当前 emitter 未提供可信文件路径
        line=None,  # 当前 emitter 未提供可信源码行
        message=str_problem,  # 保留规则真实状态原因
        evidence=str_evidence,  # 记录缺少坐标的可审计边界
        severity=str(dict_rule.get("level") or "WARNING"),  # 继承 catalog 等级
        diagnostic=build_legacy_diagnostic(
            {
                "rule_id": str(dict_rule["gate_id"]),
                "rule_key": str(dict_rule["rule_key"]),
                "severity": str(dict_rule.get("level") or "WARNING"),
                "message": str_problem,
                "evidence": str_evidence,
                "status": evaluation.status,
            }
        ),
    )

# _result_dict 集中定义单条 VG 结果的公开字段。
def _result_dict(dict_rule: dict[str, Any], evaluation: VgEvaluation) -> dict[str, Any]:
    """合并 catalog 元数据与执行结论。

    参数:
        dict_rule: 当前固定规则的 catalog 元数据。
        evaluation: 对应规则实现生成的执行结论。
    返回:
        可序列化的单条 VG 公开结果。
    异常:
        VgDiagnosticContractError: 非通过且适用的规则缺失可执行 finding。
    """

    # 组合预算结果在公开序列化前执行最后一道确定性 finding 排序。
    tuple_findings = _ordered_findings(dict_rule, evaluation)  # 当前规则公开输出顺序的 finding 集合

    # 失败、无法判断和解析错误必须携带至少一条可执行诊断。
    if evaluation.applicable and evaluation.status in {"failed", "inconclusive", "error"} and not tuple_findings:

        # 坐标未知时保留项目级状态证据，不伪造文件或 line=1。
        tuple_findings = (_missing_finding(dict_rule, evaluation),)  # 为无源码坐标状态补充项目级可执行证据

    # 所有字段在此集中映射，避免各规则模块形成报告漂移。
    return {
        "gate_id": dict_rule["gate_id"],
        "rule_id": dict_rule["gate_id"],
        "rule_key": dict_rule["rule_key"],
        "level": dict_rule["level"],
        "catalog_status": dict_rule["status"],
        "status": evaluation.status,
        "applicable": evaluation.applicable,
        "message": evaluation.message,
        "findings": [
            _public_finding_dict(dict_rule, obj_finding, evaluation.status)
            for obj_finding in tuple_findings
        ],
    }

# _summarize_results 同时统计目录状态和执行状态。
def _summarize_results(list_results: list[dict[str, Any]]) -> dict[str, Any]:
    """按状态和目录类别汇总 128 条结果。

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
