"""基于 formatter 类型化事实计算 VG146/VG147 组合操作锥。"""

# 延迟求值类型注解，避免分析模型在导入阶段产生额外依赖。
from __future__ import annotations

# 正则只解析 formatter 已隔离的实例文本中的命名端口连接。
import re

# Any 仅描述 formatter JSON 事实中尚未收窄的叶节点。
from typing import Any

# 不可变目标结果统一承载计数、层次身份和诊断定位。
from .vg_comb_model import (
    CombTargetCone,
    DefinitionRoot,
    HierarchyGraph,
    ScopedTarget,
)

# 纯选择、常量和路径覆盖算法由 selectors 模块统一拥有，旧私有名称保持兼容。
from .vg_comb_selectors import (
    base_target as _base_target,
    constant_truth_value as _constant_truth_value,
    facts_cover_all_paths as _facts_cover_all_paths,
    is_constant_expression as _is_constant_expression,
)

# 引用遍历、控制选择编号与目标规范化继续通过旧私有名称调用。
from .vg_comb_selectors import (
    reference_targets as _reference_targets,
    runtime_operands as _runtime_operands,
    selector_id as _selector_id,
    static_target as _static_target,
)

# hierarchy tracing 入口建立跨实例 cone 并保留完整 occurrence identity。
from .vg_comb_tracing import trace_target_cone

# source-only 实现索引和 definition roots 驱动每个独立层级图入口。
from .vg_comb_targets import (
    build_hierarchy_bindings,
    build_module_implementation_index,
    enumerate_definition_roots,
)

# 标准 VG 模型保证新门禁沿用既有通过、失败和不确定协议。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 共享事实入口防止组合锥分析重新扫描 Verilog 源文本。
from .vg_semantic_facts import VgFacts

# 两条组合预算规则使用显式三态归属表，禁止退回 contains_for 布尔推导。
def _owned_by_gate(loop_presence: str, gate_id: str) -> bool:
    """判断循环三态组合锥是否归当前预算门禁评估。

    参数:
        loop_presence: absent、present 或 unknown 循环证据。
        gate_id: VG146 或 VG147 固定编号。

    返回:
        当前 gate 是否必须评估该组合锥。
    """

    # unknown 同时进入两条 gate，确保未知循环归属不会静默放行。
    if loop_presence == "unknown":

        # 两条组合预算规则共享同一未知目标下界。
        return gate_id in {"VG146", "VG147"}

    # 确定 absent/present 分别由普通与循环预算规则独占。
    return (loop_presence == "absent" and gate_id == "VG146") or (
        loop_presence == "present" and gate_id == "VG147"
    )

# definition root 文本保留来源、模块名和完整定义范围。
def _definition_root_text(obj_cone: CombTargetCone) -> str:
    """序列化 finding evidence 使用的定义根身份。

    参数:
        obj_cone: 当前待报告的目标组合锥。

    返回:
        跨进程稳定的定义根身份文本。
    """

    # 旧 module-local cone 没有新身份字段时使用既有路径和模块定位。
    if obj_cone.definition_root is None:

        # 兼容身份仍可稳定区分来源文件和模块名。
        return f"{obj_cone.path}:{obj_cone.module}"

    # 完整定义范围区分同文件中的重复 module 声明。
    obj_span = obj_cone.definition_root.definition_span  # 当前 root 的一基定义范围

    # evidence 使用可读且稳定的 source/module/span 组合。
    return (
        f"{obj_cone.definition_root.relative_path}:{obj_cone.definition_root.module_name}@"
        f"{obj_span.line_start}:{obj_span.column_start}-"
        f"{obj_span.line_end}:{obj_span.column_end}"
    )

# 报告身份严格包含 root、实例路径、特化、目标和 gate 编号。
def _cone_report_identity(obj_cone: CombTargetCone, gate_id: str) -> tuple[object, ...]:
    """构造组合预算 finding 的唯一去重身份。

    参数:
        obj_cone: 当前待评估的目标组合锥。
        gate_id: 当前 VG146 或 VG147 编号。

    返回:
        可排序且只在完全相同报告身份间相等的元组。
    """

    # source path 已进入 definition root，实例路径保留全部 occurrence 段。
    return (
        _definition_root_text(obj_cone),
        obj_cone.instance_path,
        obj_cone.specialization_fingerprint,
        obj_cone.target,
        gate_id,
    )

# 排序键固定 source、root span、path、fingerprint、target 和 gate 顺序。
def _cone_report_sort_key(obj_cone: CombTargetCone, gate_id: str) -> tuple[object, ...]:
    """构造不受遍历顺序影响的组合预算报告排序键。

    参数:
        obj_cone: 当前待排序的目标组合锥。
        gate_id: 当前 VG146 或 VG147 编号。

    返回:
        与设计规定字段顺序一致的稳定排序元组。
    """

    # 缺少新定义身份的旧 cone 使用一基默认 span 保持兼容排序。
    obj_span = obj_cone.definition_root.definition_span if obj_cone.definition_root else None  # 可选定义范围

    # gate ID 是同一完整身份在双门 unknown 归属下的最终排序项。
    return (
        obj_cone.path,
        int(obj_span.line_start if obj_span else 1),
        int(obj_span.column_start if obj_span else 1),

        # occurrence path 与参数指纹共同隔离实例化硬件身份。
        obj_cone.instance_path,
        obj_cone.specialization_fingerprint,

        # 静态目标和 gate ID 完成最终稳定排序。
        obj_cone.target,
        gate_id,
    )

# operation occurrence ID 反向恢复当前 cone 实际遍历过的实例路径。
def _deepest_operation_path(obj_cone: CombTargetCone) -> str:
    """返回当前组合锥中最深的可达 operation 实例路径。

    参数:
        obj_cone: 当前待报告的目标组合锥。

    返回:
        最深可达 operation 的完整实例路径；无 operation 时回落到目标路径。
    """

    # occurrence ID 的首段由 definition root 和完整实例路径组成。
    str_prefix = f"{_definition_root_text(obj_cone)}/"  # operation ID 中实例路径之前的稳定前缀

    # 每个真实 operation 都可能来自不同深度的 parent 或 child occurrence。
    list_paths = [  # 当前 cone 可达 operation 的完整实例路径
        str_operation_id.split("|", 1)[0][len(str_prefix):]  # 去除 definition root 与后续身份字段
        for str_operation_id in obj_cone.operation_ids  # 遍历当前目标全部真实 operation occurrence
        if str_operation_id.split("|", 1)[0].startswith(str_prefix)  # 只接受当前 root 的规范 ID
    ]

    # 没有可解析 operation ID 时使用当前 cone 自身 occurrence 路径。
    if not list_paths:

        # 兼容旧 cone 时模块名继续提供可读路径。
        return "/".join(obj_cone.instance_path) or obj_cone.module

    # 深度优先，深度相同时使用字典序保证多 child producer 输出稳定。
    return max(list_paths, key=lambda str_path: (str_path.count("/"), str_path))

# schema-v2 evidence 继续使用字符串，并以加法 key=value 字段承载层次身份。
def _comb_finding_evidence(obj_cone: CombTargetCone, int_limit: int) -> str:
    """构造 VG146/VG147 共享的稳定层次 evidence 字符串。

    参数:
        obj_cone: 当前待报告的目标组合锥。
        int_limit: 目录配置允许的最大操作节点数。

    返回:
        保持字符串 schema 且包含全部加法身份字段的 evidence。
    """

    # 根模块路径缺失时回落到目标所属 module，保留旧 cone 可读性。
    str_instance_path = "/".join(obj_cone.instance_path) or obj_cone.module  # 完整 occurrence 路径

    # 最深 operation 路径展示跨层追踪实际到达的 child occurrence。
    str_child_output = f"{_deepest_operation_path(obj_cone)}.{obj_cone.target}"  # 末级 child 输出定位

    # 多个局部原因稳定排序后放在单一字段中，空值显式记录为 none。
    str_reason = " | ".join(sorted(obj_cone.inconclusive_reasons)) or "none"  # 当前目标局部未知原因

    # 字段顺序固定，便于 CLI JSON、Markdown 和测试作确定性比较。
    return "; ".join(
        (
            f"definition_root={_definition_root_text(obj_cone)}",
            f"instance_path={str_instance_path}",
            f"specialization={obj_cone.specialization_fingerprint or 'default'}",
            f"target={obj_cone.target}",
            f"child_output={str_child_output}",
            f"operation_count={obj_cone.operation_count}",
            f"limit={int_limit}",
            f"inconclusive_reason={str_reason}",
            f"loop_presence={obj_cone.loop_presence}",
        )
    )

# 单条 finding 统一携带完整身份，无论最终状态是 failed 还是 inconclusive。
def _comb_finding(
    obj_cone: CombTargetCone,
    int_limit: int,
    *,
    over_limit: bool,
) -> VgFinding:
    """把目标组合锥转换为 schema-v2 兼容的层次 finding。

    参数:
        obj_cone: 当前待报告的目标组合锥。
        int_limit: 当前组合操作预算上限。
        over_limit: 是否已经确定超过操作预算。

    返回:
        path/line 合同不变且 evidence 包含加法字段的发现。
    """

    # 确定超限使用既有时序化建议。
    if over_limit:

        # 超限诊断明确提示延迟合同和人工架构审查边界。
        str_message = (  # 当前确定超限 finding 的修复建议
            "组合逻辑操作锥超过强预算；优先加入流水寄存器、注册标志或预译码，并将复杂 FSM 条件拆为多周期时序步骤。"
            "这些修改可能改变可见延迟；若协议延迟不可变化，必须阻断并进行人工架构审查。"
        )

    # 未超限的未知目标继续明确禁止按低计数放行。
    else:

        # formatter 缺口只形成当前目标的局部不确定诊断。
        str_message = "当前目标的组合操作锥包含 formatter 无法确定的结构，禁止按低计数放行。"  # 当前未知 finding 诊断

    # finding 公开结构不变，仅替换为完整层次 evidence 字符串。
    return VgFinding(
        obj_cone.path,
        obj_cone.line,
        str_message,
        _comb_finding_evidence(obj_cone, int_limit),
    )

# 缺失函数定义扫描只沿 formatter typed tree 的 operands 递归。
def _missing_function_names(
    value: object,
    known_names: set[str],
) -> set[str]:
    """收集表达式中没有本地定义的函数调用名称。

    参数:
        value: formatter typed expression 节点或兼容空值。
        known_names: 当前 module 内已解析函数名称集合。

    返回:
        当前表达式子树引用但没有本地定义的函数名称集合。
    """

    # 非字典值不具备 typed expression 合同。
    if not isinstance(value, dict):

        # 空集合让同层其他操作数继续独立检查。
        return set()

    # 当前节点的缺失 callee 与全部操作数子树分别收集。
    set_missing: set[str] = set()  # 当前表达式子树缺少定义的函数名

    # function_call marker 自身零成本，但必须具有可展开的本地定义。
    if str(value.get("kind") or "") == "function_call":

        # callee 字段是 formatter 函数调用事实的权威名称。
        str_callee = str(value.get("callee") or "")  # 当前调用引用的本地函数名

        # 空名称或索引中不存在的 callee 都不能确定其组合操作锥。
        if not str_callee or str_callee not in known_names:

            # 空名称使用固定占位，避免生成不可定位的空原因。
            set_missing.add(str_callee or "<unknown>")

    # 嵌套调用可能出现在普通运算、选择器或其他函数 actual 内。
    for obj_operand in value.get("operands", []) or []:

        # 子树缺失名称并入当前表达式的局部原因集合。
        set_missing.update(_missing_function_names(obj_operand, known_names))

    # 集合去除同一表达式内对同一缺失函数的重复调用。
    return set_missing

# module 预处理把缺失函数定义局部化为对应赋值事实的 parse_error。
def _mark_missing_function_definitions(
    list_facts: list[dict[str, Any]],
    list_functions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为引用未知本地函数的组合事实附加稳定解析原因。

    参数:
        list_facts: 当前 module 的组合表达式事实。
        list_functions: 当前 module 的本地函数定义事实。

    返回:
        与输入容器断开顶层引用并保留原顺序的组合事实列表。
    """

    # 只有名称非空且解析完整的函数才能支持后续函数体展开。
    set_known_names = {  # 当前 module 可解析的本地函数名
        str(dict_function.get("name") or "")  # 保存函数定义公开名称
        for dict_function in list_functions  # 遍历 formatter 本地函数目录
        if str(dict_function.get("name") or "")  # 排除兼容空定义占位
        and bool(dict_function.get("parse_complete", True))  # 不完整定义不能作为可信 callee
    }

    # 输出列表保持 formatter 事实顺序，避免改变目标诊断次序。
    list_marked: list[dict[str, Any]] = []  # 附加缺失函数原因后的事实副本

    # 每条赋值仅在自身表达式引用缺失函数时变为 inconclusive。
    for dict_fact in list_facts:

        # 浅副本足以隔离 parse_error 写入，typed tree 保持只读。
        dict_marked = dict(dict_fact)  # 当前组合事实的独立顶层副本

        # 既有 parser 错误优先保留，不被函数定义检查覆盖。
        if not str(dict_marked.get("parse_error") or ""):

            # typed tree 递归定位嵌套或根级 function_call marker。
            set_missing = _missing_function_names(dict_marked.get("expression"), set_known_names)  # 当前事实缺失 callee

            # 任一缺失定义都必须阻止零成本 marker 静默放行。
            if set_missing:

                # 稳定排序保证多个缺失 callee 的诊断可重复。
                str_names = ", ".join(sorted(set_missing))  # 当前事实全部缺失函数名

                # parse_error 由既有事实分析路径转换成局部 inconclusive finding。
                dict_marked["parse_error"] = f"missing function definition: {str_names}"  # 当前事实的函数展开缺口

        # 无论是否标记缺口，都保持当前事实原有出现位置。
        list_marked.append(dict_marked)

    # 调用方使用独立列表聚合目标，原 formatter 报告保持不变。
    return list_marked

# 公开评估入口按门禁编号筛选普通目标或 for 展开目标。
def evaluate_comb_operation_gate(
    str_gate_id: str,
    facts: VgFacts,
    int_max_operations: int,
    *,
    cones: tuple[CombTargetCone, ...] | None = None,
) -> VgEvaluation:
    """执行普通或 for 展开组合操作预算门禁。

    参数:
        str_gate_id: VG146 或 VG147。
        facts: formatter 构建的共享 RTL 事实。
        int_max_operations: 每个静态目标允许的最大操作节点数。
        cones: 可选的共享组合锥快照；缺省时从 facts 构建。

    返回:
        当前门禁的通过、失败或不确定结论。
    """

    # 先构建完整目标集合，保证跨连续赋值的数据依赖可被追踪。
    list_cones = (  # 本次门禁读取的目标锥快照
        list(cones)  # 复用语义引擎已构建的不可变锥结果
        if cones is not None  # 调用方显式提供共享分析快照
        else list(build_comb_target_cones(facts))  # 独立调用时按同一事实即时构建
    )

    # 三态 owner 表先筛选当前 gate，再按完整报告身份消除重复 cone。
    dict_owned_cones = {  # 当前 gate 报告身份到唯一目标锥的映射
        _cone_report_identity(obj_cone, str_gate_id): obj_cone  # 完整身份相同的重复 cone 只保留一次
        for obj_cone in list_cones  # 遍历全部静态目标组合锥
        if _owned_by_gate(obj_cone.loop_presence, str_gate_id)  # 应用 absent/present/unknown 归属表
    }

    # 排序固定加入 gate ID，禁止输入遍历顺序改变 finding 次序。
    list_owned_cones = sorted(  # 当前 gate 去重并稳定排序后的目标集合
        dict_owned_cones.values(),  # 每个完整报告身份唯一保留的 cone
        key=lambda obj_cone: _cone_report_sort_key(obj_cone, str_gate_id),  # 设计规定的身份顺序
    )

    # 没有归属目标时保持目录规则存在但不适用。
    if not list_owned_cones:

        # 不适用不是失败，也不应制造空发现。
        return passed(applicable=False)

    # 超限发现独立保存，确保不确定事实不能覆盖确定失败。
    list_over_limit: list[VgFinding] = []  # 已确认超过预算的目标发现

    # 局部解析不完整的目标保留为附加风险证据。
    list_unknown: list[VgFinding] = []  # 无法完整判定的目标发现

    # 每个静态目标单独核算，避免无关信号互相污染计数。
    for obj_cone in list_owned_cones:

        # 超过配置上限时必须产生确定失败。
        if obj_cone.operation_count > int_max_operations:

            # 超限优先级高于 unknown，同一身份只产生一条确定失败 finding。
            list_over_limit.append(
                _comb_finding(
                    obj_cone,
                    int_max_operations,
                    over_limit=True,
                )
            )

            # 当前身份已由 failed finding 保留全部未知原因，无需重复登记。
            continue

        # 未超限但存在局部解析缺口时保留 inconclusive finding。
        if obj_cone.inconclusive_reasons:

            # unknown 下界未超限时禁止按已知计数通过。
            list_unknown.append(
                _comb_finding(
                    obj_cone,
                    int_max_operations,
                    over_limit=False,
                )
            )

    # 确定超限优先于同一批目标中的局部不确定状态。
    if list_over_limit:

        # failed 优先，同时保留其他未超限身份的局部未知证据。
        return failed(*(list_over_limit + list_unknown))

    # 没有超限但存在解析缺口时禁止按低计数放行。
    if list_unknown:

        # 门禁结论明确指出事实来源是 formatter 类型化报告。
        return inconclusive(
            "部分目标的组合操作锥无法从 formatter 类型化事实中确定。",
            *list_unknown,
        )

    # 所有归属目标均可判定且未超过预算时才通过。
    return passed(applicable=True)

# 文件级遍历负责把共享事实转换成目标结果，不参与表达式解析。
def _root_report_module(facts: VgFacts, definition_root: DefinitionRoot) -> dict[str, Any] | None:
    """按完整定义身份查找原始 module 报告。

    参数:
        facts: formatter 为扫描闭包生成的共享事实。
        definition_root: 待映射回原始报告的模块定义入口。

    返回:
        身份完全匹配的 module 报告；找不到时返回 None。
    """

    # 路径先把候选限制到定义所属源文件。
    for source_facts in facts.sources:

        # 其他文件中的同名 module 不是当前 definition root。
        if source_facts.relative_path != definition_root.identity.relative_path:

            # 跳过路径不匹配的来源。
            continue

        # 名称和起始行共同区分同文件中的重复定义。
        for dict_module in source_facts.report.get("modules", []):

            # 模块名用于第一层定义身份核对。
            bool_same_name = str(dict_module.get("name") or "") == definition_root.identity.module_name  # 当前候选名称身份

            # 起始行是实现身份中 definition span 的稳定锚点。
            int_expected_line = definition_root.identity.definition_span.line_start  # 入口定义的一基起始行

            # 候选报告必须与入口定义行完全一致。
            bool_same_line = int(dict_module.get("line_start") or 1) == int_expected_line  # 当前候选位置身份

            # 两项身份均一致时返回既有模块内算法需要的完整报告。
            if bool_same_name and bool_same_line:

                # 原始字典只读传给后续事实复制逻辑。
                return dict_module

    # 索引身份无法映射回报告时由调用方使用新追踪器失败关闭。
    return None

# 兼容路径只接管无需层次、函数或循环物化语义的入口。
def _legacy_root_cones(
    facts: VgFacts,
    definition_root: DefinitionRoot,
    hierarchy_graph: HierarchyGraph,
) -> list[CombTargetCone] | None:
    """为纯模块内入口保留既有过程顺序与分支覆盖语义。

    参数:
        facts: formatter 为扫描闭包生成的共享事实。
        definition_root: 当前默认参数环境下的定义入口。
        hierarchy_graph: 当前入口构建出的冻结层次图。

    返回:
        可安全复用旧算法时返回目标锥列表，否则返回 None。
    """

    # 多节点图需要跨实例追踪，不能退回模块内算法。
    if len(hierarchy_graph.modules) != 1:

        # None 明确表示当前 root 应继续走新追踪器。
        return None

    # 单节点中的实例或函数仍需要新的绑定与函数展开语义。
    _, root_module = hierarchy_graph.modules[0]  # 当前 root 的默认参数特化模块

    # 实例和函数都要求使用完整 occurrence tracing。
    if root_module.instances or root_module.functions:

        # 这些结构超出既有模块内算法的能力边界。
        return None

    # 原始 formatter 报告保留过程版本、分支覆盖和循环展开事实。
    dict_module = _root_report_module(facts, definition_root)  # 当前 root 的原始模块事实

    # 无法恢复原始定义时禁止猜测，应由新追踪器给出保守结果。
    if dict_module is None:

        # None 触发调用方的层级追踪路径。
        return None

    # 已知循环已把动态 lvalue 物化为静态位时，应使用新追踪器而非保留旧占位 cone。
    list_report_expressions = list(dict_module.get("comb_expressions", []) or [])  # 原始模块组合表达式目录

    # 只检查结构化事实中的动态 lvalue 缺口。
    bool_had_dynamic_lvalue = any(  # 原始模块是否含动态目标
        str(dict_fact.get("parse_error") or "") == "dynamic lvalue selection is not a static endpoint"  # 目标缺口文本
        for dict_fact in list_report_expressions  # 遍历原始模块组合表达式
        if isinstance(dict_fact, dict)  # 排除非结构化兼容值
    )

    # 特化结果需要证明动态目标已经形成静态 occurrence。
    bool_has_static_clones = any(  # 特化模块是否形成静态循环 occurrence
        not str(dict(frozen_fact.fields).get("parse_error") or "")  # 物化事实必须解析完整
        and bool(dict(frozen_fact.fields).get("loop_iteration_tuple"))  # 物化事实携带静态迭代身份
        for frozen_fact in root_module.comb_expressions  # 遍历特化模块组合事实
    )

    # 物化成功的循环由完整 occurrence tracing 负责，避免旧动态目标重复报告。
    if bool_had_dynamic_lvalue and bool_has_static_clones:

        # None 让当前 root 进入新追踪器。
        return None

    # 缺失函数调用继续沿既有局部 parse_error 路径失败关闭。
    list_module_functions = list(dict_module.get("functions", []))  # 原始模块函数定义目录

    # 兼容路径复制事实并标记缺失函数定义。
    list_local_facts = _mark_missing_function_definitions(list_report_expressions, list_module_functions)  # 当前模块兼容事实

    # 纯模块内入口不存在需要旧算法处理的跨实例输出边界。
    return _module_target_cones(
        definition_root.identity.relative_path,
        definition_root.identity.module_name,
        list_local_facts,
        set(),
        False,
        bool(dict_module.get("generates")),
        definition_root.identity.definition_span.line_start,
    )

# 公共 facade 按 definition root 构建全部 occurrence 的目标组合锥。
def build_comb_target_cones(facts: VgFacts) -> tuple[CombTargetCone, ...]:
    """为全部文件和 module 建立目标级组合操作锥。

    参数:
        facts: formatter 为全部 Verilog 来源构建的共享事实。

    返回:
        按文件和 module 聚合后的静态目标组合锥列表。
    """

    # source definitions 与 external interfaces 首先进入不可变实现索引。
    module_index = build_module_implementation_index(facts)  # 当前扫描闭包的 source-only 实现索引

    # specialization cache 在全部 definition roots 之间安全复用不可变模块图。
    dict_cache = {}  # 当前 facade 调用私有的特化缓存

    # 汇总列表包含 standalone root 和每个已实例化 occurrence 的独立目标。
    list_cones: list[CombTargetCone] = []  # 全部完整身份组合锥

    # 每个 source definition 都作为默认参数环境下的独立分析入口。
    for definition_root in enumerate_definition_roots(module_index):

        # root graph 递归展开唯一 source 实现并局部化未知边界。
        hierarchy_graph_hierarchy_graph: HierarchyGraph = build_hierarchy_bindings(  # 当前入口的冻结层次绑定图
            definition_root,  # 兼容算法所分析的定义入口
            module_index,  # 扫描闭包实现索引
            dict_cache,  # 本次 facade 调用的特化缓存
        )

        # 纯模块内入口复用已经验证的过程版本与分支覆盖语义。
        list_legacy_cones = _legacy_root_cones(  # 当前 root 的可选兼容结果
            facts,  # 扫描闭包共享 formatter 事实
            definition_root,  # 当前默认参数定义入口
            hierarchy_graph_hierarchy_graph,  # 当前入口冻结层次图
        )

        # 有兼容结果时禁止同一 root 再由新追踪器重复分析。
        if list_legacy_cones is not None:

            # 保持既有目标顺序并继续处理下一个 definition root。
            list_cones.extend(list_legacy_cones)

            # 当前 root 已完成模块内分析。
            continue

        # 每个 occurrence 内的本地和跨层 endpoint 分别接受预算检查。
        for tuple_path, specialized_module in hierarchy_graph_hierarchy_graph.modules:

            # endpoint driver 目录包含本地、实例输出、unknown 和 storage projections。
            set_targets = {  # 当前 module occurrence 的全部可查询静态目标
                scoped_endpoint.target  # 保存作用域端点的 module-local 目标名
                for scoped_endpoint, _ in hierarchy_graph_hierarchy_graph.endpoint_drivers  # 遍历冻结 producer 目录
                if scoped_endpoint.instance_path == tuple_path  # 只保留当前 occurrence 路径
                and scoped_endpoint.specialization == specialized_module.key  # 核对参数特化身份
            }

            # comb facts 即使没有 producer 摘要也必须进入目标枚举。
            for frozen_fact in specialized_module.comb_expressions:

                # FrozenFact 的字段元组可无损恢复顶层 target 文本。
                dict_fact = dict(frozen_fact.fields)  # 当前本地组合事实顶层字段

                # 空目标不能形成作用域 endpoint。
                if str(dict_fact.get("target") or ""):

                    # 静态目标规范化后并入当前 occurrence 目录。
                    set_targets.add(_static_target(str(dict_fact.get("target") or "")))

            # 稳定字典序避免 producer 插入实现细节改变报告顺序。
            for str_target in sorted(set_targets):

                # 完整 ScopedTarget 绑定 root、path、specialization 和静态端点。
                scoped_target = ScopedTarget(definition_root.identity, tuple_path, specialized_module.key, str_target)  # 当前待追踪的完整作用域目标

                # tracing 结果直接适配为 facade-compatible CombTargetCone。
                list_cones.append(trace_target_cone(hierarchy_graph_hierarchy_graph, scoped_target))

    # 最终排序显式覆盖 source、root span、path、fingerprint 和静态 target。
    list_cones.sort(
        key=lambda obj_cone: (
            obj_cone.path,
            obj_cone.definition_root.definition_span.line_start if obj_cone.definition_root else 1,

            # 列号在同一 source line 中稳定区分重复定义入口。
            obj_cone.definition_root.definition_span.column_start if obj_cone.definition_root else 1,
            obj_cone.instance_path,
            obj_cone.specialization_fingerprint,

            # 静态目标名作为同一 occurrence 内的最终排序键。
            obj_cone.target,
        )
    )

    # 调用方获得跨层完整目标集合后再按 VG146/VG147 三态归属筛选。
    return tuple(list_cones)

# 层次连接解析只处理 formatter 已隔离的命名实例文本。
def _hierarchy_output_targets(
    list_instances: list[dict[str, Any]],
    dict_module_outputs: dict[str, set[str]],
) -> tuple[set[str], bool]:
    """解析命名实例连接中由子模块输出驱动的本地静态端点。

    参数:
        list_instances: 当前 module 的 formatter 实例事实。
        dict_module_outputs: 全工程 module 名到输出端口集合的索引。

    返回:
        已解析的本地输出端点集合及是否存在未解析实例。
    """

    # 已解析端点按静态目标去重，供下游精确传播层次缺口。
    set_outputs: set[str] = set()  # 子模块输出实际驱动的本地端点

    # 任一实例方向或连接不完整时保留 module 级不确定标记。
    bool_unresolved = False  # 当前 module 是否含未解析实例

    # 每个实例独立核对子模块方向和命名端口连接。
    for dict_instance in list_instances:

        # 子模块名称用于查找其已知输出方向。
        str_child = str(dict_instance.get("module_name") or "")  # 当前实例的 module 类型

        # 缺失 module 定义时无法区分输入与输出连接。
        set_child_outputs = dict_module_outputs.get(str_child)  # 当前子模块的输出端口

        # formatter 隔离文本只用于解析命名端口的静态实际信号。
        str_text = str(dict_instance.get("text") or "")  # 当前实例的源文本

        # 未知子模块或空实例文本都必须 fail-closed。
        if set_child_outputs is None or not str_text:

            # 标记缺口后继续收集其他可确定实例的精确端点。
            bool_unresolved = True  # 当前实例无法形成完整层次映射

            # 其余实例仍可能提供可精确传播的输出连接。
            continue

        # 命名端口连接映射到 formatter 可识别的本地静态目标。
        dict_connections = {  # 当前实例的端口名到本地端点映射
            str_port: _static_target(str_actual)  # 当前端口连接的规范静态端点
            for str_port, str_actual in re.findall(  # 遍历已解析的命名端口连接
                r"\.([A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*(?:\s*\[[^\]]+\])?)\s*\)",  # 命名端口与静态实际信号
                str_text,  # formatter 隔离的当前实例文本
            )
        }

        # 任一输出端口缺失连接时不能宣称层次锥完整。
        if not set_child_outputs.issubset(dict_connections):

            # 保留缺口并避免使用不完整连接集合。
            bool_unresolved = True  # 当前实例缺少至少一个输出连接

            # 跳过当前实例，防止部分映射制造错误确定性。
            continue

        # 只有已确认输出方向的实际信号才进入层次边界集合。
        set_outputs.update(
            dict_connections[str_port]
            for str_port in set_child_outputs
            if dict_connections[str_port]
        )

    # 同时返回精确端点和剩余的 module 级解析缺口。
    return set_outputs, bool_unresolved

# module 级聚合按静态端点分组，再递归合并组合上游。
def _module_target_cones(
    str_path: str, str_module: str,
    list_facts: list[dict[str, Any]],
    set_hierarchy_outputs: set[str], bool_unresolved_instance: bool,
    bool_has_generate: bool, int_module_line: int,
) -> list[CombTargetCone]:
    """计算单个 module 内各目标的可达操作集合。

    参数:
        str_path: 当前 module 所属 Verilog 文件相对路径。
        str_module: formatter 报告中的 module 名称。
        list_facts: 当前 module 的类型化组合表达式事实。
        set_hierarchy_outputs: 已确认由子模块输出端口驱动的本地端点。
        bool_unresolved_instance: 是否存在无法按命名端口和子模块方向解析的实例。
        bool_has_generate: 是否存在 formatter 尚未展开的 generate 层次形状。
        int_module_line: module 声明所在行，供层次缺口定位。

    返回:
        当前 module 内每个基础目标的组合操作锥。
    """

    # 常量位选和切片作为独立静态端点聚合驱动事实。
    dict_by_target = _group_facts_by_target(list_facts)  # 静态端点到驱动事实的映射

    # 无法解析的实例仍需 module 级占位；已解析实例只沿输出依赖传播。
    str_hierarchy_reason = (  # 当前 module 的层次分析缺口
        f"{str_module}: instance port cone requires hierarchy expansion"  # 存在实例时登记层次展开缺口
        if bool_unresolved_instance  # 仅方向或连接无法解析时使用全局占位
        else ""  # 无实例 module 不附加层次不确定原因
    )

    # 只有实例连接而没有本地赋值时仍需产生阻断性占位端点。
    if bool_unresolved_instance and not dict_by_target:

        # 返回层次缺口端点，防止空本地事实被误认为规则不适用。
        return [
            CombTargetCone(
                path=str_path,
                module=str_module,
                target="<hierarchy>",
                line=int_module_line,
                operation_ids=frozenset(),
                contains_for=False,
                inconclusive_reasons=(str_hierarchy_reason,),
            )
        ]

    # module 结果按目标首次出现顺序生成，保持诊断稳定。
    list_cones = [  # 当前 module 的目标结果
        _build_target_cone(  # 当前静态端点的不可变组合锥
            str_path, str_module,  # 当前目标所属文件和 module

            # 当前目标事实与 module 级生产者索引共同建立递归锥。
            str_target, list_target_facts,  # 当前静态端点及其驱动事实
            dict_by_target, set_hierarchy_outputs,  # 生产者表与层次边界
            str_hierarchy_reason,  # 当前 module 的共享层次缺口
        )
        for str_target, list_target_facts in dict_by_target.items()  # 保持目标首次出现顺序
    ]

    # generate 层次尚未形成类型化锥时，两条门禁都必须失败关闭。
    list_cones.extend(
        _generate_placeholder_cones(
            str_path,
            str_module,
            bool_has_generate,
            int_module_line,
        )
    )

    # 返回当前作用域内所有目标，禁止跨 module 合并同名信号。
    return list_cones

# 驱动事实聚合保持 formatter 的出现顺序并保留静态选择器身份。
def _group_facts_by_target(
    list_facts: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按静态目标聚合 formatter 驱动事实。

    参数:
        list_facts: 当前 module 的类型化组合表达式事实。

    返回:
        静态端点到有序驱动事实列表的映射。
    """

    # 聚合索引从空映射开始，并按 formatter 顺序追加事实。
    dict_by_target: dict[str, list[dict[str, Any]]] = {}  # 当前 module 的目标驱动索引

    # 每条事实按出现顺序归入规范化静态目标。
    for dict_fact in list_facts:

        # 规范空白但保留 formatter 已确认的常量选择器。
        str_target = _static_target(str(dict_fact.get("target") or ""))  # 当前静态预算端点

        # 空目标不是可分析端点，不应写入组合锥。
        if str_target:

            # 同一目标的分支和多个过程事实共同参与驱动判定。
            dict_by_target.setdefault(str_target, []).append(dict_fact)

    # 返回稳定有序的目标驱动索引。
    return dict_by_target

# 单目标构造隔离递归分析、层次缺口传播和不可变结果封装。
def _build_target_cone(
    str_path: str, str_module: str, str_target: str,
    list_target_facts: list[dict[str, Any]],
    dict_by_target: dict[str, list[dict[str, Any]]],
    set_hierarchy_outputs: set[str], str_hierarchy_reason: str,
) -> CombTargetCone:
    """构造一个静态目标的完整组合操作锥结果。

    参数:
        str_path: 当前 module 所属 Verilog 文件相对路径。
        str_module: formatter 报告中的 module 名称。
        str_target: 当前预算核算端点。
        list_target_facts: 当前目标的全部驱动事实。
        dict_by_target: 当前 module 的目标驱动索引。
        set_hierarchy_outputs: 已确认由子模块输出驱动的本地端点。
        str_hierarchy_reason: 当前 module 的可选层次分析缺口。

    返回:
        当前目标的不可变组合操作锥结果。
    """

    # 递归结果同时包含可计数节点与局部不确定原因。
    tuple_operation_result = _target_operations(  # 当前目标的操作集合与原因集合
        str_target,  # 当前预算核算端点
        dict_by_target,  # 供事实内信号引用查找生产者
        set(),  # 根节点尚无递归访问历史
        set_hierarchy_outputs,  # 仅沿实际子模块输出连接传播层次缺口
    )

    # 复制操作集合，避免修改递归结果对象。
    set_operations = set(tuple_operation_result[0])  # 当前目标可达操作编号

    # 复制原因集合以便附加 module 级层次缺口。
    set_reasons = set(tuple_operation_result[1])  # 当前目标局部不确定原因

    # 未解析实例使当前 module 的每个本地端点都保持不确定。
    if str_hierarchy_reason:

        # 追加同一 module 共享的层次分析缺口。
        set_reasons.add(str_hierarchy_reason)

    # 循环归属同时识别已克隆节点和零次之外的循环事实。
    bool_contains_for = (  # 当前目标是否属于 VG147
        any(":iter" in str_operation for str_operation in set_operations)  # 已生成迭代克隆节点
        or any(  # 非零循环事实仍归属循环专用门禁
            bool(dict_fact.get("from_for"))  # 当前事实确实来自循环体
            and int(dict_fact.get("loop_iterations") or 0) != 0  # 零次循环不生成硬件
            for dict_fact in list_target_facts  # 检查当前目标的全部循环驱动事实
        )
    )

    # 完整目标结果是 VG 发现生成的唯一输入。
    return CombTargetCone(
        path=str_path,
        module=str_module,
        target=str_target,
        line=min(int(dict_fact.get("line") or 1) for dict_fact in list_target_facts),
        operation_ids=frozenset(set_operations),
        contains_for=bool_contains_for,
        inconclusive_reasons=tuple(sorted(set_reasons)),
    )

# generate 占位结果与普通目标聚合分离，保持主流程规模受控。
def _generate_placeholder_cones(
    str_path: str,
    str_module: str,
    bool_has_generate: bool,
    int_module_line: int,
) -> list[CombTargetCone]:
    """为尚未展开的 generate 层次构造两条门禁占位结果。

    参数:
        str_path: 当前 module 所属 Verilog 文件相对路径。
        str_module: formatter 报告中的 module 名称。
        bool_has_generate: 当前 module 是否包含 generate 结构。
        int_module_line: module 声明所在行。

    返回:
        VG146 与 VG147 使用的阻断性占位结果；无 generate 时为空。
    """

    # 没有 generate 时不新增任何人工端点。
    if not bool_has_generate:

        # 空列表保持调用方 extend 操作无副作用。
        return []

    # 两个占位共享同一层次展开原因。
    str_generate_reason = f"{str_module}: generate hierarchy requires elaboration"  # 当前 module 的层次缺口

    # 普通和循环门禁分别获得一个不可静默跳过的端点。
    return [
        CombTargetCone(
            path=str_path,
            module=str_module,
            target="<generate-for>" if bool_for_gate else "<generate>",
            line=int_module_line,
            operation_ids=frozenset(),
            contains_for=bool_for_gate,
            inconclusive_reasons=(str_generate_reason,),
        )
        for bool_for_gate in (False, True)
    ]

# 递归入口先处理环路和多驱动，再逐条合并目标事实。
def _target_operations(
    str_target: str,
    dict_by_target: dict[str, list[dict[str, Any]]],
    set_visiting: set[str],
    set_hierarchy_outputs: set[str],
) -> tuple[set[str], set[str]]:
    """递归合并目标自身和上游组合生产者的操作节点。

    参数:
        str_target: 当前需要建立组合锥的基础目标。
        dict_by_target: 当前 module 内目标到驱动事实的映射。
        set_visiting: 当前递归路径上已经访问的目标集合。
        set_hierarchy_outputs: 已确认由子模块输出驱动的本地端点。

    返回:
        可达操作编号集合和局部不确定原因集合。
    """

    # 再次访问当前递归路径中的目标说明存在组合环路。
    if str_target in set_visiting:

        # 环路无法形成有限可靠操作锥，必须局部标记不确定。
        return set(), {f"combinational cycle at {str_target}"}

    # 为下游递归复制访问集合，避免同层分支互相污染。
    set_next_visiting = set(set_visiting)  # 包含当前路径的访问状态

    # 更新副本而不污染调用方持有的递归访问集合。
    set_next_visiting.add(str_target)

    # 目标事实列表在当前作用域内集中复用。
    list_target_facts = dict_by_target.get(str_target, [])  # 当前目标的全部驱动事实

    # 多驱动原因在遍历具体表达式前即可确定。
    set_reasons = _driver_reasons(str_target, list_target_facts)  # 当前目标初始不确定原因

    # 操作集合使用真实出现编号去重分支汇合。
    set_operations: set[str] = set()  # 当前目标累计操作编号

    # 同一过程内阻塞赋值读取前一版本时使用该 SSA 状态。
    set_previous_operations: set[str] = set()  # 当前目标上一版本操作集合

    # 混用阻塞和非阻塞赋值无法建立确定的过程版本语义。
    set_assignment_operators = {  # 当前目标实际使用的赋值操作符
        str(dict_fact.get("assignment_operator") or "=")  # 单条事实采用的赋值符号
        for dict_fact in list_target_facts  # 当前目标的全部过程赋值事实
    }

    # 阻塞与非阻塞混用时无法建立统一的过程版本顺序。
    if len(set_assignment_operators) > 1:

        # 把赋值语义冲突局部登记到当前目标。
        set_reasons.add(f"{str_target}: mixed blocking and nonblocking assignments")

    # 每条驱动事实分别计算自身、控制和组合上游操作。
    for dict_fact in list_target_facts:

        # 单条事实的失败不会阻止其他确定操作进入计数。
        tuple_fact_result = _fact_operations(  # 单条事实的操作集合与原因集合
            str_target,  # 当前事实驱动目标
            dict_fact,  # 当前 formatter 赋值事实
            dict_by_target,  # 当前 module 驱动索引
            set_next_visiting,  # 已包含当前目标的访问路径
            set_previous_operations,  # 同一过程内可见的前一目标版本
            set_hierarchy_outputs,  # 精确子模块输出边界
        )

        # 合并当前事实可确定的真实操作节点。
        bool_unconditional_assignment = (  # 是否为覆盖前序 D 版本的无条件赋值
            (
                str(dict_fact.get("assignment_operator") or "=") == "="  # 阻塞赋值立即更新过程版本
                or str(dict_fact.get("process_kind") or "") == "seq"  # 时序过程更新 D 版本
            )
            and not dict_fact.get("controls")  # 条件赋值需要与其他分支共同保留
            and not bool(dict_fact.get("from_for"))  # 循环展开产生多个并存硬件版本
            and len({str(item.get("driver_id") or "") for item in list_target_facts}) == 1  # 同一过程内才允许顺序覆盖
        )

        # 无条件阻塞赋值覆盖旧版本；分支和循环硬件则形成并集。
        if bool_unconditional_assignment:

            # 新的无条件版本完全替代当前过程中的旧版本。
            set_operations = set(tuple_fact_result[0])  # 当前阻塞赋值形成的最新操作集合

        # 条件、循环或跨驱动事实需要保留所有可达硬件节点。
        else:

            # 把当前事实的节点并入既有分支与循环硬件。
            set_operations.update(tuple_fact_result[0])

        # 下一条同过程阻塞赋值只能读取当前已形成的版本。
        set_previous_operations = set(set_operations)  # 下一条阻塞赋值可见的目标版本

        # 合并当前事实的局部解析或展开缺口。
        set_reasons.update(tuple_fact_result[1])

    # 返回两个独立集合，调用方负责固化和排序原因。
    return set_operations, set_reasons

# 多驱动只污染当前目标，并保留后续可确定的超限计数。
def _driver_reasons(
    str_target: str,
    list_target_facts: list[dict[str, Any]],
) -> set[str]:
    """检查一个目标是否存在多个独立驱动来源。

    参数:
        str_target: 当前目标基础名称。
        list_target_facts: 当前目标的全部 formatter 驱动事实。

    返回:
        空集合或包含多驱动原因的单项集合。
    """

    # driver_id 由 formatter 按 assign 或 always 来源生成。
    set_driver_ids = {  # 当前目标的独立驱动来源编号
        str(dict_fact.get("driver_id") or "")  # 非空独立驱动编号
        for dict_fact in list_target_facts  # 遍历当前目标全部事实
        if str(dict_fact.get("driver_id") or "")  # 排除缺失来源编号
    }

    # 单一来源内的不同分支不是多驱动问题。
    if len(set_driver_ids) <= 1:

        # 没有跨来源冲突时保持原因集合为空。
        return set()

    # 多个 assign 或 always 驱动使目标锥无法唯一确定。
    return {f"{str_target}: multiple independent drivers"}

# 单事实分析把解析错误、循环展开、控制和数据依赖分层处理。
def _fact_operations(
    str_target: str,
    dict_fact: dict[str, Any],
    dict_by_target: dict[str, list[dict[str, Any]]],
    set_visiting: set[str],
    set_previous_operations: set[str],
    set_hierarchy_outputs: set[str],
) -> tuple[set[str], set[str]]:
    """计算一条目标驱动事实贡献的操作和不确定原因。

    参数:
        str_target: 当前事实驱动的基础目标。
        dict_fact: formatter 输出的单条赋值事实。
        dict_by_target: 当前 module 的目标驱动索引。
        set_visiting: 已包含当前目标的递归访问路径。
        set_previous_operations: 同一过程内当前目标的前一 SSA 版本。
        set_hierarchy_outputs: 已确认由子模块输出驱动的本地端点。

    返回:
        当前事实可确定的操作集合和局部不确定原因集合。
    """

    # formatter 已把表达式解析失败局部化到当前事实。
    str_error = str(dict_fact.get("parse_error") or "")  # 当前事实解析错误

    # 解析失败时不能继续读取缺失的表达式树。
    if str_error:

        # 原因携带目标名，便于合并后仍能定位污染端点。
        return set(), {f"{str_target}: {str_error}"}

    # 表达式必须是 formatter 输出的类型化字典节点。
    dict_expression = dict_fact.get("expression")  # 当前赋值右值表达式树

    # 缺失类型化表达式时禁止回退到源码文本猜测。
    if not isinstance(dict_expression, dict):

        # 目标保留不确定状态，已知其他事实仍可继续计数。
        return set(), {f"{str_target}: missing typed expression"}

    # 循环迭代数由 formatter 对简单常量 for 头求值。
    int_iterations = int(dict_fact.get("loop_iterations") or 0)  # 展开迭代次数

    # 循环来源标记决定真实操作节点是否按迭代克隆。
    bool_from_for = bool(dict_fact.get("from_for"))  # 当前事实是否来自 for 循环

    # 非常量边界无法静态确定展开后的门数量。
    if bool_from_for and int_iterations < 0:

        # 动态或复杂边界必须交由人工架构审查。
        return set(), {f"{str_target}: for bounds are not elaboration-time constants"}

    # 右值表达式贡献当前事实的基础操作集合。
    set_operations = _operation_ids(  # 当前事实累计操作编号
        dict_expression,  # 需要递归统计的右值根节点
        int_iterations,  # formatter 求得的展开次数
        bool_from_for,  # 是否需要按迭代克隆节点
    )

    # 控制上游追踪复用当前事实的数据依赖边界与过程版本。
    tuple_control_context = (  # 当前事实的控制锥递归上下文
        dict_by_target, set_visiting,  # module 生产者索引与递归访问路径
        set_previous_operations, set_hierarchy_outputs,  # 过程版本与层次切点
    )

    # 控制表达式同时贡献比较、逻辑、分支选择和上游生产者操作。
    tuple_control_result = _control_operations(  # 控制节点操作与局部原因
        str_target,  # 控制条件约束的目标
        dict_fact,  # 含控制栈的当前事实
        int_iterations,  # 控制操作的展开次数
        bool_from_for,  # 控制节点是否位于循环体
        tuple_control_context,  # 控制上游递归的共享边界
    )

    # 合并控制条件中的真实操作编号。
    set_operations.update(tuple_control_result[0])

    # 数据依赖只穿透组合生产者，遇到寄存器输出立即截断。
    tuple_upstream_result = _upstream_operations(  # 上游组合操作与局部原因
        str_target, dict_expression,  # 当前目标及其右值表达式树
        dict_fact, dict_by_target,  # 当前事实与 module 生产者映射
        set_visiting, set_previous_operations,  # 递归路径与前一过程版本
        set_hierarchy_outputs,  # 当前 module 的精确子模块输出边界
    )

    # 合并跨 assign 或组合过程的上游节点。
    set_operations.update(tuple_upstream_result[0])

    # 控制和上游的不确定原因共同归属当前目标。
    set_reasons = tuple_control_result[1] | tuple_upstream_result[1]  # 当前事实全部原因

    # 单事实结果交由目标级递归入口继续合并。
    return set_operations, set_reasons

# 控制条件计入其表达式操作，并为每个真实分支选择增加一次操作。
def _control_operations(
    str_target: str,
    dict_fact: dict[str, Any],
    int_iterations: int,
    bool_from_for: bool,
    tuple_context: tuple[dict[str, list[dict[str, Any]]], set[str], set[str], set[str]],
) -> tuple[set[str], set[str]]:
    """计算一条事实附带控制条件的操作贡献。

    参数:
        str_target: 当前控制条件约束的基础目标。
        dict_fact: 包含 controls 数组的 formatter 赋值事实。
        int_iterations: 当前事实的循环展开次数。
        bool_from_for: 当前事实是否来自 for 循环。
        tuple_context: 控制上游递归所需的生产者、访问路径和层次边界。

    返回:
        控制操作编号集合和控制解析不确定原因集合。
    """

    # 控制操作与数据操作使用相同的真实出现编号计数语义。
    set_operations: set[str] = set()  # 全部控制操作编号

    # 单个控制解析失败不抹除其他已确定条件。
    set_reasons: set[str] = set()  # 控制条件局部原因

    # 条件分支和 case 控制项按 formatter 控制栈顺序处理。
    for int_control_index, dict_control in enumerate(dict_fact.get("controls", [])):

        # 控制解析错误由 formatter 保存在对应节点。
        str_control_error = str(dict_control.get("parse_error") or "")  # 当前控制解析错误

        # 无法解析的条件不能按零操作处理。
        if str_control_error:

            # 原因只污染当前目标的控制路径。
            set_reasons.add(f"{str_target}: control expression: {str_control_error}")

            # 跳过缺失表达式树，继续收集其他控制节点。
            continue

        # 控制表达式内的比较和逻辑节点全部计入预算。
        set_operations.update(_operation_ids(dict_control, int_iterations, bool_from_for))

        # 控制引用与数据右值遵循同一递归规则，寄存器 Q 仍作为组合切点。
        tuple_control_upstream = _upstream_operations(  # 当前控制表达式的组合生产者锥
            str_target, dict_control,  # 当前目标及控制表达式树
            dict_fact, tuple_context[0],  # 事实与 module 生产者索引
            tuple_context[1], tuple_context[2],  # 递归路径与过程版本
            tuple_context[3],  # 子模块输出组合切点
        )

        # 控制信号的 assign 或组合过程生产者必须并入完整目标锥。
        set_operations.update(tuple_control_upstream[0])

        # 层次缺口、组合环或局部解析缺口沿控制依赖传播。
        set_reasons.update(tuple_control_upstream[1])

        # case default 不产生额外 selector，其余分支和 if 均产生选择操作。
        str_selector_id = _selector_id(dict_control, str_target, int_control_index)  # 分支选择编号

        # 空选择编号表示 formatter 明确标记 default 分支。
        if str_selector_id:

            # 循环内选择器与数据操作使用一致的迭代克隆规则。
            set_operations.update(
                _clone_operation(str_selector_id, int_iterations, bool_from_for)
            )

    # 控制层结果与数据依赖层结果在上级函数合并。
    return set_operations, set_reasons

# 精确静态端点优先于整信号回退，避免位选依赖被错误放大。
def _resolve_reference_targets(
    str_reference: str,
    dict_by_target: dict[str, list[dict[str, Any]]],
) -> set[str]:
    """解析引用在当前 module 中对应的生产者端点。

    参数:
        str_reference: formatter 提取的精确引用端点。
        dict_by_target: 当前 module 的静态端点驱动索引。

    返回:
        精确端点存在时返回单项集合；整向量读取返回全部静态位生产者。
    """

    # 已有精确位选或切片驱动时必须保持该端点身份。
    if str_reference in dict_by_target:

        # 精确生产者可避免串入同一总线的其他位逻辑。
        return {str_reference}

    # 整向量读取必须汇聚该基础信号的全部已知位选或切片生产者。
    if str_reference == _base_target(str_reference):

        # 同一基础信号的所有静态生产者共同定义整向量读取。
        set_static_producers = {  # 当前整向量引用可达的位选和切片端点
            str_target  # 与整向量同源的静态生产者
            for str_target in dict_by_target  # 遍历当前 module 的全部目标
            if _base_target(str_target) == str_reference  # 只保留相同基础信号
        }

        # 找到静态端点时禁止退回模糊的整信号生产者。
        if set_static_producers:

            # 返回全部已知位生产者以保持向量依赖完整。
            return set_static_producers

    # 找不到精确端点时保守回退到整信号生产者。
    return {_base_target(str_reference)}

# 自引用辅助函数区分循环展开、寄存器切点和过程内前一版本。
def _self_reference_operations(
    str_reference_target: str,
    str_target: str,
    dict_fact: dict[str, Any],
    set_previous_operations: set[str],
) -> tuple[bool, set[str]]:
    """判断当前引用是否已由目标自身的版本语义处理。

    参数:
        str_reference_target: 当前引用解析后的生产者端点。
        str_target: 当前赋值事实驱动的静态目标。
        dict_fact: formatter 输出的当前赋值事实。
        set_previous_operations: 同一过程内前一目标版本的操作集合。

    返回:
        返回是否已处理以及应并入的前一版本操作集合。
    """

    # 非自引用必须继续进入普通上游生产者追踪。
    if str_reference_target != str_target:

        # 未处理标记让调用方沿组合依赖继续递归。
        return False, set()

    # 循环累加器的硬件副本已经由迭代编号表达。
    if bool(dict_fact.get("from_for")):

        # 已展开自引用不能再次递归成源码环路。
        return True, set()

    # 时序过程中的自引用读取寄存器 Q，组合追踪在此截断。
    if str(dict_fact.get("process_kind") or "") == "seq":

        # 时钟边界之前的旧 Q 不属于当前 D 端组合锥。
        return True, set()

    # 只有阻塞赋值且已有版本时才能绑定过程内 SSA 前驱。
    bool_reads_previous_version = (  # 当前自引用是否读取已形成的过程版本
        str(dict_fact.get("assignment_operator") or "=") == "="  # 阻塞赋值即时读取过程状态
        and bool(set_previous_operations)  # 前序事实已经产生可引用版本
    )

    # 已确认前一版本时直接返回其操作节点，不再递归当前目标。
    if bool_reads_previous_version:

        # 复制集合避免调用方修改 SSA 状态快照。
        return True, set(set_previous_operations)

    # 尚无可绑定版本的自引用继续走普通递归，以便报告真实组合环。
    return False, set()

# 单个非自引用生产者只在组合驱动下继续向上游展开。
def _upstream_reference_operations(
    str_reference_target: str,
    dict_by_target: dict[str, list[dict[str, Any]]],
    set_visiting: set[str],
    set_hierarchy_outputs: set[str],
) -> tuple[set[str], set[str]]:
    """计算一个引用端点可穿透的上游组合操作。

    参数:
        str_reference_target: 当前引用对应的静态生产者端点。
        dict_by_target: 当前 module 的静态端点驱动索引。
        set_visiting: 当前递归路径中已经访问的目标集合。
        set_hierarchy_outputs: 已确认由子模块输出驱动的本地端点。

    返回:
        返回该生产者贡献的操作集合和局部不确定原因。
    """

    # 子模块输出是精确层次边界，仅污染真正读取该网络的下游端点。
    if str_reference_target in set_hierarchy_outputs:

        # 未展开的子模块内部锥不能按零操作静默放行。
        return set(), {f"{str_reference_target}: instance output cone requires hierarchy expansion"}

    # 当前作用域中的驱动事实决定是否存在可追踪生产者。
    list_upstream = dict_by_target.get(str_reference_target, [])  # 引用端点的全部驱动事实

    # 输入端口、参数和常量没有 module 内部上游操作。
    if not list_upstream:

        # 外部叶节点不产生额外操作或不确定原因。
        return set(), set()

    # 寄存器或锁存器输出作为明确的组合路径切点。
    bool_storage_driver = _is_storage_driver(list_upstream)  # 当前生产者是否定义存储输出

    # 存储 Q 端隔离其内部 D 端逻辑。
    if bool_storage_driver:

        # 下游端点不继承存储单元前方的组合操作。
        return set(), set()

    # 组合生产者沿当前访问路径递归建立完整操作集合。
    tuple_upstream_result = _target_operations(  # 当前引用生产者的递归分析结果
        str_reference_target,  # 继续追踪的组合端点
        dict_by_target,  # 当前 module 的驱动索引
        set_visiting,  # 下游已经访问的目标路径
        set_hierarchy_outputs,  # 继续传播精确层次输出集合
    )

    # 保持递归结果的操作与原因集合原样返回。
    return tuple_upstream_result

# 上游追踪只穿过组合驱动，寄存器 Q 是明确的组合路径切点。
def _upstream_operations(
    str_target: str, dict_expression: dict[str, Any], dict_fact: dict[str, Any],
    dict_by_target: dict[str, list[dict[str, Any]]],
    set_visiting: set[str], set_previous_operations: set[str],
    set_hierarchy_outputs: set[str],
) -> tuple[set[str], set[str]]:
    """递归合并表达式引用的组合上游操作。

    参数:
        str_target: 当前事实驱动的基础目标。
        dict_expression: 当前事实的类型化右值表达式树。
        dict_fact: 当前赋值事实，用于识别循环自引用。
        dict_by_target: 当前 module 的目标驱动索引。
        set_visiting: 当前递归路径中的目标集合。
        set_previous_operations: 当前目标在同一过程内的前一版本操作。
        set_hierarchy_outputs: 已确认由子模块输出驱动的本地端点。

    返回:
        可穿透组合上游的操作集合和不确定原因集合。
    """

    # 上游操作按真实节点编号去重共享生产者。
    set_operations: set[str] = set()  # 上游可达操作编号

    # 上游解析缺口沿数据依赖传播到当前目标。
    set_reasons: set[str] = set()  # 上游不确定原因

    # 每个表达式引用独立解析端点、版本语义和生产者边界。
    for str_reference in _reference_targets(dict_expression):

        # 精确位选优先，整向量读取则汇聚全部已知静态位生产者。
        set_reference_targets = _resolve_reference_targets(  # 当前引用可见的生产者端点
            str_reference,  # formatter 提取的引用文本
            dict_by_target,  # 解析精确端点所需的生产者表
        )

        # 每个解析后的静态端点分别处理版本语义与组合生产者。
        for str_reference_target in set_reference_targets:

            # 自引用可能绑定循环副本、寄存器 Q 或过程内前一版本。
            tuple_self_result = _self_reference_operations(  # 当前端点的自引用处理结果
                str_reference_target,  # 当前待分类生产者端点
                str_target,  # 当前事实的目标端点
                dict_fact,  # 提供过程与循环语义的事实
                set_previous_operations,  # 同一过程内前一版本操作
            )

            # 已绑定版本语义的自引用不再进入普通递归。
            if tuple_self_result[0]:

                # 前一版本操作并入当前上游集合。
                set_operations.update(tuple_self_result[1])

                # 当前引用已经完整处理，继续下一个生产者端点。
                continue

            # 普通引用只穿透组合生产者，存储输出在辅助函数内截断。
            tuple_upstream_result = _upstream_reference_operations(  # 当前端点的组合上游结果
                str_reference_target,  # 待追踪的静态端点
                dict_by_target,  # 解析生产者使用的 module 驱动索引
                set_visiting,  # 继续向上游传播的访问路径
                set_hierarchy_outputs,  # 需要截断的子模块输出端点
            )

            # 真实操作编号按集合合并，避免共享生产者重复计数。
            set_operations.update(tuple_upstream_result[0])

            # 层次缺口和解析失败原因沿当前数据依赖传播。
            set_reasons.update(tuple_upstream_result[1])

    # 返回当前表达式可穿透的全部组合依赖。
    return set_operations, set_reasons

# 表达式树遍历只识别 formatter 标注的真实操作节点。
def _operation_ids(
    dict_expression: dict[str, Any],
    int_iterations: int,
    bool_from_for: bool,
) -> set[str]:
    """返回表达式中的真实操作出现编号，并按循环迭代克隆。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。
        int_iterations: 当前事实的静态循环展开次数。
        bool_from_for: 当前表达式是否位于 for 展开体内。

    返回:
        当前表达式子树贡献的唯一操作编号集合。
    """

    # 纯常量子树会在综合期折叠，不形成运行时组合操作。
    if _is_constant_expression(dict_expression):

        # 完全可折叠的表达式不消耗运行时组合预算。
        return set()

    # 子树集合逐层合并，天然去除共享事实中的重复节点编号。
    set_operations: set[str] = set()  # 当前表达式子树操作编号

    # occurrence_id 仅由真实运算符和动态选择节点提供。
    str_occurrence_id = str(dict_expression.get("occurrence_id") or "")  # 当前节点出现编号

    # kind 区分普通操作、标识符、常量和位选择。
    str_kind = str(dict_expression.get("kind") or "")  # 当前表达式节点类型

    # 常量选择的三目在综合后不形成运行时 mux。
    bool_constant_ternary = (
        str_kind == "ternary"  # 仅三目节点具有可裁剪的运行时选择器
        and bool(dict_expression.get("operands"))  # 三目节点必须含条件操作数
        and isinstance(dict_expression.get("operands", [None])[0], dict)  # 条件满足类型化节点合同
        and _constant_truth_value(dict_expression["operands"][0]) is not None  # 条件真值可静态确定
    )

    # 循环展开后的索引选择被视作静态连线，不额外计门操作。
    bool_counts_select = (  # 当前选择节点是否消耗组合预算
        str_kind != "select"  # 普通操作节点直接计数
        or (bool(dict_expression.get("dynamic")) and not bool_from_for)  # 非循环动态选择计数
    )

    # 有真实编号且满足选择规则时计入当前节点。
    if str_occurrence_id and bool_counts_select and not bool_constant_ternary:

        # 循环内节点按每次 elaboration 迭代生成独立编号。
        set_operations.update(
            _clone_operation(str_occurrence_id, int_iterations, bool_from_for)
        )

    # 所有综合后可达操作数递归贡献各自子树的真实操作节点。
    for dict_operand in _runtime_operands(dict_expression):

        # 非字典叶值不是 formatter 类型化表达式节点。
        if isinstance(dict_operand, dict):

            # 合并合法子节点，保持同一出现编号只计一次。
            set_operations.update(
                _operation_ids(dict_operand, int_iterations, bool_from_for)
            )

    # 返回当前表达式完整子树的预算占用。
    return set_operations

# 循环克隆将一处源码运算符映射为多个 elaborated 硬件操作。
def _clone_operation(
    str_occurrence_id: str,
    int_iterations: int,
    bool_from_for: bool,
) -> set[str]:
    """按 elaborated for 次数克隆真实语法操作节点。

    参数:
        str_occurrence_id: formatter 分配的真实语法节点编号。
        int_iterations: 当前 for 的静态展开次数。
        bool_from_for: 当前节点是否来自 for 循环体。

    返回:
        单一原始编号或带迭代后缀的展开编号集合。
    """

    # 普通表达式保持 formatter 原始出现编号。
    if not bool_from_for:

        # 非循环节点只对应一个硬件操作。
        return {str_occurrence_id}

    # 零次展开不生成硬件操作，负值已在事实入口标记不确定。
    if int_iterations <= 0:

        # 空循环体对操作预算没有贡献。
        return set()

    # 每次展开生成独立后缀，确保真实硬件复制量进入计数。
    return {
        f"{str_occurrence_id}:iter{int_iteration}"
        for int_iteration in range(int_iterations)
    }

# 标识符提取只用于建立数据依赖，不把常量当成生产者。
def _identifier_references(dict_expression: dict[str, Any]) -> set[str]:
    """提取表达式数据依赖中的标识符引用。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        当前表达式子树引用的唯一标识符名称集合。
    """

    # 引用集合去除同一表达式中重复读取的同一信号。
    set_references: set[str] = set()  # 当前子树标识符引用

    # 只有 identifier 节点直接贡献信号名称。
    if dict_expression.get("kind") == "identifier":

        # formatter name 字段保存未带选择器的标识符文本。
        set_references.add(str(dict_expression.get("name") or ""))

    # 递归遍历运算符、三目和选择节点的操作数。
    for dict_operand in dict_expression.get("operands", []):

        # 仅类型化字典节点具有可递归的 operands 合同。
        if isinstance(dict_operand, dict):

            # 合并子树引用，供上游组合锥追踪使用。
            set_references.update(_identifier_references(dict_operand))

    # 返回去重后的数据依赖叶节点。
    return set_references

# 寄存器和由不完整组合赋值形成的锁存器均提供 Q 端切点。
def _is_storage_driver(list_facts: list[dict[str, Any]]) -> bool:
    """判断一组驱动事实是否定义时序存储输出。

    参数:
        list_facts: 同一静态端点的全部 formatter 驱动事实。

    返回:
        任一时序驱动或全部受控组合驱动形成存储时返回 True。
    """

    # 任一时序过程驱动都明确建立寄存器 Q 端边界。
    if any(str(dict_fact.get("process_kind") or "") == "seq" for dict_fact in list_facts):

        # 时序驱动无需再检查组合覆盖完整性。
        return True

    # 组合过程只有无法证明覆盖全部运行时路径时才形成锁存器。
    bool_latch_driver = (
        bool(list_facts)  # 空事实集合不代表锁存器驱动
        and all(  # 全部驱动都必须属于组合过程
            str(dict_fact.get("process_kind") or "") == "comb"  # 当前事实属于组合过程
            for dict_fact in list_facts  # 遍历同一端点的全部驱动事实
        )
        and not _facts_cover_all_paths(list_facts)  # 未覆盖路径形成电平保持
    )

    # 返回锁存器覆盖判定，供上游锥决定是否截断。
    return bool_latch_driver
