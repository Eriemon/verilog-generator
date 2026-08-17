"""解析参数合同记录并执行参数化实例门禁。"""

# future annotations 让合同节点类型在运行时保持延迟求值。
from __future__ import annotations

# typing 描述参数合同中的动态结构和 finding 元数据。
from typing import Any, Iterable

# VgFacts 提供设计需求与模块实例的共享事实。
from .vg_semantic_facts import VgFacts

# 统一评估模型承载参数合同的状态与证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 复用表达式解析器和参数合同的公开常量。
from .vg_contract_parser import (
    CONSTRAINT_ID_PATTERN,
    PARAMETER_CONSTRAINTS_KEY,
    ContractParser,
    _evaluate_node,
    _module_parameter_values,
)

# _source_modules 扁平化所有 formatter source 中的模块报告。
def _source_modules(facts: VgFacts) -> Iterable[tuple[Any, dict[str, Any]]]:
    """按源码顺序返回 source 与 module 报告。

    参数:
        facts: 当前 RTL 的共享 formatter 事实。
    返回:
        源文件事实和结构化模块报告的迭代器。
    """

    # 每个源文件的 modules 保持 formatter 顺序。
    for obj_source in facts.sources:

        # 模块报告缺失时跳过无法分析的源条目。
        for dict_module in obj_source.report.get("modules", []) or []:

            # 只把结构化字典交给新增规则。
            if isinstance(dict_module, dict):

                # 返回当前源文件与模块事实。
                yield obj_source, dict_module

# _constraint_records 校验合同列表并返回解析节点。
def _constraint_records(facts: VgFacts) -> tuple[list[dict[str, Any]], list[str]]:
    """读取无模块名参数合同并返回结构错误。

    参数:
        facts: 当前 RTL 与设计需求事实。
    返回:
        解析后的合同记录和结构问题列表。
    """

    # design_requirements 缺失时使用空字典，避免调用方类型漂移。
    dict_requirements = facts.spec.get("design_requirements", {})  # 当前设计需求对象

    # 非字典需求对象无法承载参数合同。
    if not isinstance(dict_requirements, dict):

        # 由上游已有合同规则负责报告类型错误。
        return [], ["design_requirements must be an object"]

    # 缺失字段表示未声明任何参数合同。
    obj_constraints: object = dict_requirements.get(PARAMETER_CONSTRAINTS_KEY, [])  # 参数合同列表

    # 空字段保持无合同语义。
    if obj_constraints is None:

        # null 不是公开合同中的列表形态。
        return [], ["design_requirements.parameter_constraints must be a list"]

    # 参数合同必须是数组。
    if not isinstance(obj_constraints, list):

        # 返回结构化错误，不让 evaluator 猜测输入。
        return [], ["design_requirements.parameter_constraints must be a list"]

    # 收集已经解析的合同记录。
    list_records: list[dict[str, Any]] = []  # 结构化参数合同

    # 初始化合同结构问题集合。
    list_issues: list[str] = []  # 参数合同结构问题

    # 初始化合同 id 去重集合。
    set_ids: set[str] = set()  # 已出现的合同 id

    # 逐项校验参数合同字段。
    for int_index, obj_constraint in enumerate(obj_constraints):

        # 每项必须是对象，禁止字符串快捷形式。
        if not isinstance(obj_constraint, dict):

            # 错误包含数组索引，便于修复 spec。
            list_issues.append(f"parameter_constraints[{int_index}] must be an object")

            # 跳过非对象合同项，避免猜测其字段语义。
            continue

        # 读取四个公开字段并禁止模块级隐式作用域。
        str_id = str(obj_constraint.get("id") or "")  # 当前合同 id

        # 保存合同表达式文本，供 parser 和 finding 复用。
        str_expression = str(obj_constraint.get("expression") or "")  # 当前合同表达式

        # 保存合同失败消息，作为确定违规的公开说明。
        str_message = str(obj_constraint.get("message") or "")  # 当前合同失败消息

        # 任何模块、实例或层级字段都属于歧义合同。
        set_forbidden = {"module", "instance", "hierarchy", "scope"} & set(obj_constraint)  # 识别会引入歧义作用域的字段。

        # 收集禁止字段而不静默忽略。
        if set_forbidden:

            # 输出排序后的字段名以稳定诊断。
            list_issues.append(
                f"parameter_constraints[{int_index}] has forbidden scope fields: {', '.join(sorted(set_forbidden))}"
            )

        # id、表达式和消息必须满足公开字符串合同。
        if not CONSTRAINT_ID_PATTERN.fullmatch(str_id):

            # 非法 id 不能成为稳定报告主键。
            list_issues.append(f"parameter_constraints[{int_index}].id is invalid")

        # 重复 id 会破坏跨模块证据合并。
        if str_id in set_ids:

            # 记录重复合同编号。
            list_issues.append(f"parameter_constraints[{int_index}].id is duplicated")

        # 合法 id 先登记，后续重复项仍能被发现。
        set_ids.add(str_id)

        # 合同表达式和消息都必须是非空文本。
        if not str_expression or not str_message:

            # 统一报告缺失字段。
            list_issues.append(f"parameter_constraints[{int_index}] requires expression and message")

            # 跳过缺少核心字段的合同项，继续收集结构问题。
            continue

        # 受限 parser 同时检查语法和引用参数集合。
        try:

            # 解析合同，获得自动适用所需的 required_parameters。
            tuple_node, set_required = ContractParser(str_expression).parse()  # 当前合同节点和参数集合

        # 非法语法只保留一条可读问题。
        except (TypeError, ValueError) as obj_error:

            # 说明 parser 拒绝合同的原因。
            list_issues.append(f"parameter_constraints[{int_index}] expression is invalid: {obj_error}")

            # 跳过语法非法的合同项，保持 fail-closed 结构报告。
            continue

        # 保存解析结果供每个参数环境复用。
        list_records.append(
            {
                "id": str_id,
                "expression": str_expression,
                "message": str_message,
                "required_parameters": tuple(sorted(set_required)),
                "node": tuple_node,
            }
        )

    # 返回已解析记录和结构问题。
    return list_records, list_issues

# _finding 为新增规则构造统一源码定位证据。
def _finding(
    obj_source: Any,  # 当前 finding 所属的源码文件事实。
    # 模块事实限定 finding 的源码定位边界。
    dict_module: dict[str, Any],  # 当前 finding 所属的模块事实。
    # 关键字参数保持公开 finding 调用的可读顺序。
    *,
    # 行号是报告中唯一的一基定位字段。
    line: int | None,  # 一基源码行号；无法验证时保持为空。
    # 消息和 evidence 共同构成用户与机器证据。
    message: str,  # 面向用户的违规说明。
    evidence: str,  # 机器可读的定位证据。
    # metadata 携带无需解析字符串的结构化上下文。
    metadata: Iterable[tuple[str, Any]] = (),  # 额外结构化证据。
) -> VgFinding:
    """按 source/module 事实创建一条新增规则 finding。

    参数:
        obj_source: 当前源码文件事实。
        dict_module: 当前 formatter 模块事实。
        line: finding 的一基源码行号；未知时传 None。
        message: 面向用户的违规说明。
        evidence: 机器可读的定位证据。
        metadata: 额外结构化证据键值。
    返回:
        统一 VgFinding 实例。
    """

    # source 的 relative_path 是报告中稳定的源码定位。
    str_path = str(getattr(obj_source, "relative_path", ""))  # 当前 finding 相对路径

    # module 名只用于定位，不参与合同匹配。
    str_module = str(dict_module.get("name") or "<unknown>")  # 当前模块定位名称

    # 统一追加 module 定位到结构化 metadata。
    tuple_metadata = (("module", str_module), *tuple(metadata))  # 当前 finding 结构化证据

    # 只有正的一基坐标才可声明为 source scope；未知坐标保持文件级事实。
    int_line = line if isinstance(line, int) and not isinstance(line, bool) and line > 0 else None  # 可信源码行

    # 返回稳定的 finding 模型。
    return VgFinding(
        path=str_path,
        line=int_line,
        message=message,
        evidence=evidence,
        metadata=tuple_metadata,
    )

# _parameter_contract_findings 执行单个参数环境中的合同求值。
def _parameter_contract_findings(
    obj_source: Any,
    dict_module: dict[str, Any],
    tuple_records: tuple[dict[str, Any], ...],
    tuple_parameter_values: dict[str, int],
    set_covered: set[str],
) -> list[VgFinding]:
    """返回一个参数环境的合同违规证据。

    参数:
        obj_source: 当前源码文件事实。
        dict_module: 当前 formatter 模块事实。
        tuple_records: 已解析的参数合同记录。
        tuple_parameter_values: 当前模块的确定参数环境。
        set_covered: 当前环境已覆盖的参数名称集合。
    返回:
        当前参数环境产生的 finding 列表。
    """

    # 初始化当前参数环境的求值证据。
    list_findings: list[VgFinding] = []  # 当前环境的参数合同 finding

    # 每条合同只在其引用参数全部确定时自动适用。
    for dict_record in tuple_records:

        # 固化该合同特化边界所需的公开参数键。
        tuple_required = tuple(dict_record["required_parameters"])  # 当前特化所需参数键

        # 不完整参数集合留给其他特化环境，禁止默认值猜测。
        if not set(tuple_required).issubset(tuple_parameter_values):

            # 当前合同在本环境不适用，继续尝试其他合同。
            continue

        # 记录已被本合同覆盖的公开参数。
        set_covered.update(tuple_required)

        # 只向 finding 暴露合同实际引用的参数值。
        dict_specialization_values = {
            str_name: tuple_parameter_values[str_name]  # 当前合同实际使用的参数值
            for str_name in tuple_required  # 只保留表达式引用的参数
        }  # 当前合同 specialization 参数快照

        # fingerprint 由稳定排序的参数键值组成，不携带模块作用域。
        str_specialization_fingerprint = ";".join(  # 生成不含模块作用域的特化身份。
            f"{str_name}={dict_specialization_values[str_name]}"  # 当前参数特化片段
            for str_name in tuple_required  # required_parameters 已按名称排序
        )  # 当前合同 specialization 身份

        # 执行当前合同表达式。
        int_result = _evaluate_node(dict_record["node"], tuple_parameter_values)  # 当前合同求值结果

        # 未知结果阻断严格交付并保留实际特化证据。
        if int_result is None:

            # 输出不可确定原因而不生成假通过。
            list_findings.append(
                _finding(
                    obj_source,
                    dict_module,
                    line=None,
                    message="参数合同无法确定求值结果。",
                    evidence=str(dict_record["expression"]),
                    metadata=(
                        ("constraint_id", dict_record["id"]),
                        ("required_parameters", list(tuple_required)),
                        ("parameter_values", dict_specialization_values),
                        ("specialization_fingerprint", str_specialization_fingerprint),
                        ("expression", str(dict_record["expression"])),
                        ("reason", "unknown_or_unsupported_value"),
                    ),
                )
            )

        # 零值表示合同不满足当前参数特化。
        elif int_result == 0:

            # 失败 finding 保留实际环境和合同消息。
            list_findings.append(
                _finding(
                    obj_source,
                    dict_module,
                    line=None,
                    message=str(dict_record["message"]),
                    evidence=str(dict_record["expression"]),
                    metadata=(
                        ("constraint_id", dict_record["id"]),
                        ("required_parameters", list(tuple_required)),
                        ("parameter_values", dict_specialization_values),
                        ("specialization_fingerprint", str_specialization_fingerprint),
                        ("expression", str(dict_record["expression"])),
                        ("reason", "constraint_false"),
                    ),
                )
            )

    # 返回当前环境全部合同证据。
    return list_findings

# evaluate_parameter_gate 执行 VG151 的参数集合自动适用语义。
def evaluate_parameter_gate(facts: VgFacts) -> VgEvaluation:
    """执行不绑定模块名的参数合同完整性检查。

    参数:
        facts: 当前 RTL 与设计需求事实。
    返回:
        VG151 的通过、失败或不确定结论。
    """

    # 先解析合同结构，避免对非法合同产生局部通过结论。
    tuple_list_records, tuple_contract_issues = _constraint_records(facts)  # 参数合同记录和结构问题

    # 把列表固定为不可变容器供每个模块复用。
    tuple_records = tuple(tuple_list_records)  # 当前合同记录元组

    # 扁平化模块供规则判断和无模块设计识别。
    list_modules = list(_source_modules(facts))  # 当前 RTL 的 source/module 事实

    # 没有模块时无法确认 parameter 合同覆盖。
    if not list_modules:

        # 解析错误由外层统一处理，空目标保持不适用。
        return passed(applicable=False)

    # 无参数设计不需要合同覆盖，但合同结构仍必须合法。
    bool_has_parameters = any(dict_module.get("params") for _, dict_module in list_modules)  # 是否存在公开参数

    # 收集结构问题对应的 fail-closed 证据。
    list_findings: list[VgFinding] = []  # VG151 结构与求值证据

    # 把合同结构问题绑定到第一个可信模块，保持 finding schema 稳定。
    if tuple_contract_issues:

        # 每个结构问题都保留独立报告证据。
        obj_source, dict_module = list_modules[0]  # 结构错误的默认定位模块

        # 结构问题不能被非严格模式伪装成通过。
        for str_issue in tuple_contract_issues:

            # 记录合同解析或字段结构的阻断性证据。
            list_findings.append(
                _finding(
                    obj_source,
                    dict_module,
                    line=None,
                    message="参数合同结构不合法。",
                    evidence=str_issue,
                    metadata=(("reason", str_issue),),
                )
            )

    # 逐个参数环境执行默认值合同。
    for obj_source, dict_module in list_modules:

        # 当前模块默认参数环境按声明顺序构造。
        tuple_parameter_values, tuple_unknown_names = _module_parameter_values(dict_module)  # 当前模块参数环境

        # 当前模块的公开参数名称用于自动作用域匹配。
        tuple_parameter_names = tuple(  # 收集当前模块的公开参数名称。
            str(dict_item.get("name") or "")  # 读取 formatter 参数声明名称。
            for dict_item in dict_module.get("params", []) or []  # 遍历 formatter 参数声明
        )  # 当前模块公开参数名称

        # 无参数模块不参与参数合同适用判定。
        if not tuple_parameter_names:

            # 保留其他模块的合同检查机会。
            continue

        # 任何默认值未知都使参数合同无法确定。
        if tuple_unknown_names:

            # 未知值逐项输出，阻止默认零值猜测。
            list_findings.append(
                _finding(
                    obj_source,
                    dict_module,
                    line=None,
                    message="参数默认值无法在受限环境中确定。",
                    evidence=", ".join(tuple_unknown_names),
                    metadata=(("reason", "unknown_parameter_value"), ("unknown_parameters", list(tuple_unknown_names))),
                )
            )

        # 记录当前环境已真正适用的约束参数集合。
        set_covered: set[str] = set()  # 当前模块已覆盖参数

        # 逐条合同按 required_parameters 自动匹配当前环境。
        list_parameter_findings = _parameter_contract_findings(  # 读取当前模块的合同 finding
            obj_source,  # 当前源文件事实
            dict_module,  # 当前模块事实
            tuple_records,  # 已解析合同记录
            tuple_parameter_values,  # 已解析的当前实例参数环境
            set_covered,  # 当前环境覆盖集合
        )  # 当前模块的合同求值证据

        # 合并当前模块的参数合同 finding。
        list_findings.extend(list_parameter_findings)

        # 每个公开参数都必须被至少一条适用合同覆盖。
        for dict_parameter in dict_module.get("params", []) or []:

            # 读取当前参数名称和声明行。
            str_name = str(dict_parameter.get("name") or "")  # 当前未覆盖候选参数

            # 已被合同引用的参数无需重复报告。
            if str_name in set_covered:

                # 继续检查下一个参数。
                continue

            # 无合同或合同集合不完整均属于覆盖缺口。
            list_findings.append(
                _finding(
                    obj_source,
                    dict_module,
                    line=dict_parameter.get("line_start"),
                    message="公开 parameter 没有适用的参数合同。",
                    evidence=str_name,
                    metadata=(("parameter", str_name), ("reason", "parameter_not_covered")),
                )
            )

    # 存在合同或参数时，规则才具有适用性。
    bool_applicable = bool_has_parameters or bool(tuple_records) or bool(tuple_contract_issues)  # 参数或合同存在时规则适用。

    # 任何 finding 都表示失败或不确定，按 metadata reason 区分状态。
    if list_findings:

        # 未知解析原因使用 inconclusive，确定假值和覆盖缺口使用 failed。
        bool_inconclusive = any(  # 识别无法确定的参数合同证据。
            any(  # 在 metadata 中寻找不确定原因。
                key == "reason"  # 读取 finding 的原因字段。
                and value not in {"constraint_false", "parameter_not_covered"}  # 保留未知合同原因
                for key, value in obj_finding.metadata  # 遍历当前 finding metadata
            )
            for obj_finding in list_findings  # 遍历全部参数合同 finding
        )  # 是否存在无法确定的合同证据

        # 不确定结果必须保留所有 finding 供严格交付阻断。
        if bool_inconclusive:

            # 返回 fail-closed 的不确定状态。
            return inconclusive("参数合同存在无法确定的语义证据。", *list_findings)

        # 其余 finding 是确定的合同违规。
        return failed(*list_findings)

    # 没有 finding 时返回当前合同实际适用状态。
    return passed(applicable=bool_applicable)

