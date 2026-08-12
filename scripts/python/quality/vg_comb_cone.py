"""基于 formatter 类型化事实计算 VG146/VG147 组合操作锥。"""

# 延迟求值类型注解，避免分析模型在导入阶段产生额外依赖。
from __future__ import annotations

# 正则只解析 formatter 已隔离的实例文本中的命名端口连接。
import re

# Any 仅描述 formatter JSON 事实中尚未收窄的叶节点。
from typing import Any

# 不可变目标结果及纯 finding 适配器统一承载计数、定位和诊断格式。
from .vg_comb_model import CombTargetCone, build_over_limit_finding, build_unknown_finding

# 标准 VG 模型保证新门禁沿用既有通过、失败和不确定协议。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 共享事实入口防止组合锥分析重新扫描 Verilog 源文本。
from .vg_semantic_facts import VgFacts

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

    # VG147 只接管含 for 展开的目标，避免同一目标重复报错。
    bool_for_gate = str_gate_id == "VG147"  # 当前是否评估循环专用门禁

    # 先构建完整目标集合，保证跨连续赋值的数据依赖可被追踪。
    list_cones = (  # 本次门禁读取的目标锥快照
        list(cones)  # 复用语义引擎已构建的不可变锥结果
        if cones is not None  # 调用方显式提供共享分析快照
        else list(build_comb_target_cones(facts))  # 独立调用时按同一事实即时构建
    )

    # 按循环归属筛出当前门禁负责的静态端点。
    list_owned_cones = [  # 当前 VG 编号需要评估的目标集合
        obj_cone  # 满足当前门禁归属的目标结果
        for obj_cone in list_cones  # 遍历全部静态目标组合锥
        if obj_cone.contains_for == bool_for_gate  # 匹配普通或循环目标
    ]

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

        # 不确定原因只归属当前目标，不降级其他目标结论。
        if obj_cone.inconclusive_reasons:

            # 把 formatter 局部缺口登记为可定位发现。
            list_unknown.append(build_unknown_finding(obj_cone))

        # 超过配置上限时必须产生确定失败。
        if obj_cone.operation_count > int_max_operations:

            # 失败证据包含真实操作数、预算和时序化建议。
            list_over_limit.append(build_over_limit_finding(obj_cone, int_max_operations))

    # 确定超限优先于同一批目标中的局部不确定状态。
    if list_over_limit:

        # 同时附带不确定发现，防止失败修复后遗漏剩余风险。
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
def build_comb_target_cones(facts: VgFacts) -> tuple[CombTargetCone, ...]:
    """为全部文件和 module 建立目标级组合操作锥。

    参数:
        facts: formatter 为全部 Verilog 来源构建的共享事实。

    返回:
        按文件和 module 聚合后的静态目标组合锥列表。
    """

    # 子模块输出方向用于把层次缺口限制在真正受实例输出影响的端点。
    dict_module_outputs: dict[str, set[str]] = {}  # module 名到输出端口集合

    # 先建立全工程 module 输出端口索引，供后续实例连接解析复用。
    for source_facts in facts.sources:

        # 每个 module 的输出和双向端口共同构成层次驱动候选。
        for dict_module in source_facts.report.get("modules", []):

            # 当前 module 的输出方向固化为后续实例解析索引。
            dict_module_outputs[str(dict_module.get("name") or "")] = {  # 当前 module 的输出端口集合
                str(dict_port.get("name") or "")  # 当前输出或双向端口名称
                for dict_port in dict_module.get("ports", [])  # 遍历当前 module 的全部端口
                if str(dict_port.get("direction") or "") in {"output", "inout"}  # 只保留向外驱动端口
            }

    # 汇总列表保持事实遍历顺序，令报告输出稳定可复现。
    list_cones: list[CombTargetCone] = []  # 全部静态目标组合锥

    # 每个来源保留自己的相对路径，供失败定位使用。
    for source_facts in facts.sources:

        # module 级分析避免不同作用域的同名信号发生串联。
        for dict_module in source_facts.report.get("modules", []):

            # 已解析的子模块输出只污染其实际连接的本地网络。
            tuple_hierarchy = _hierarchy_output_targets(  # 当前 module 的层次输出端点与缺口标记
                list(dict_module.get("instances", [])),  # 当前 module 的实例事实
                dict_module_outputs,  # 全工程 module 输出方向索引
            )

            # 当前 module 的全部结果追加到文件级集合。
            list_cones.extend(
                _module_target_cones(
                    source_facts.relative_path,
                    str(dict_module.get("name") or ""),
                    list(dict_module.get("comb_expressions", [])),
                    tuple_hierarchy[0],
                    tuple_hierarchy[1],
                    bool(dict_module.get("generates")),
                    int(dict_module.get("line_start") or 1),
                )
            )

    # 调用方获得完整目标集合后再按 VG146/VG147 归属筛选。
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

# 引用端点提取保留可静态确定的位选和切片。
def _reference_targets(dict_expression: dict[str, Any]) -> set[str]:
    """提取表达式引用的精确静态端点，动态选择回退基础信号。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        当前表达式引用的静态端点或保守基础信号集合。
    """

    # 静态选择可重建为与左值一致的规范端点文本。
    if dict_expression.get("kind") == "select":

        # 选择节点由专用辅助函数处理静态索引和动态回退。
        return _selected_reference_targets(dict_expression)

    # 普通节点递归合并全部数据引用。
    set_references: set[str] = set()  # 当前普通表达式累计的引用端点

    # 标识符叶节点直接贡献自身名称。
    if dict_expression.get("kind") == "identifier":

        # 名称保持 formatter 输出，选择器仅由选择节点补充。
        set_references.add(str(dict_expression.get("name") or ""))

    # 复合表达式只遍历综合后可达的操作数。
    for dict_operand in _runtime_operands(dict_expression):

        # 只有类型化字典节点才能递归提取引用。
        if isinstance(dict_operand, dict):

            # 子树端点合并后由集合去除重复引用。
            set_references.update(_reference_targets(dict_operand))

    # 返回普通表达式完整的去重引用集合。
    return set_references

# 综合可达性辅助函数统一剪除常量三目的死分支。
def _runtime_operands(dict_expression: dict[str, Any]) -> list[dict[str, Any]]:
    """返回综合后仍可达的类型化操作数。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        已剪除常量三目死分支的操作数列表。
    """

    # 只保留满足类型化表达式合同的字典操作数。
    list_operands = [  # 当前节点的全部类型化操作数
        dict_operand  # 可继续递归遍历的操作数节点
        for dict_operand in dict_expression.get("operands", [])  # formatter 原始操作数
        if isinstance(dict_operand, dict)  # 排除非表达式叶值
    ]

    # 非三目节点或非标准三操作数形状保持原始可达集合。
    if str(dict_expression.get("kind") or "") != "ternary" or len(list_operands) != 3:

        # 普通节点的全部类型化操作数都可达。
        return list_operands

    # 常量条件允许在综合前确定唯一可达分支。
    bool_condition = _constant_truth_value(list_operands[0])  # 三目条件的确定真值

    # 运行时条件必须保留条件、真分支和假分支。
    if bool_condition is None:

        # 未知条件禁止静态剪除任一分支。
        return list_operands

    # 常真选择第一分支，常假选择第二分支。
    return [list_operands[1] if bool_condition else list_operands[2]]

# 常量真值辅助函数只解释 formatter 已确认的整数字面量。
def _constant_truth_value(dict_expression: dict[str, Any]) -> bool | None:
    """把简单 Verilog 整数字面量转换为可确定真值。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        可确定常量的布尔值；非整数字面量或含未知位时返回 None。
    """

    # 只有 formatter 明确标记的常量节点可进入字面量转换。
    if str(dict_expression.get("kind") or "") != "constant":

        # 其他节点依赖运行时值，不能静态折叠。
        return None

    # 去除数字分隔符并统一进制标志大小写。
    str_value = str(dict_expression.get("value") or "").replace("_", "").lower()  # 规范化字面量文本

    # x、z 与问号位都不具有确定布尔值。
    if any(str_unknown in str_value for str_unknown in ("x", "z", "?")):

        # 未知位禁止按零或非零常量剪枝。
        return None

    # 数值转换失败时保持局部未知，不抛出到组合锥主流程。
    try:

        # 定宽 Verilog 字面量从撇号后读取进制和数值载荷。
        if "'" in str_value:

            # 位宽位于撇号之前，不参与数值转换。
            str_payload = str_value.split("'", 1)[1]  # 进制标志与数字载荷

            # 首字符是 Verilog 进制标志。
            str_base = str_payload[:1]  # 当前字面量进制标志

            # 显式映射限制为受支持的二、八、十和十六进制。
            int_base = {"b": 2, "o": 8, "d": 10, "h": 16}.get(str_base)  # Python 转换基数

            # 未识别进制不能形成确定常量值。
            if int_base is None:

                # 保守返回未知，避免错误剪枝。
                return None

            # 数字载荷非零即为逻辑真。
            return int(str_payload[1:], int_base) != 0

        # 无撇号普通整数按十进制解释。
        return int(str_value, 10) != 0

    # 非法数字载荷保留为不可确定条件。
    except ValueError:

        # 解析缺口不应中断其他目标分析。
        return None

# 选择节点辅助函数负责恢复常量位选并隔离动态选择。
def _selected_reference_targets(dict_expression: dict[str, Any]) -> set[str]:
    """提取一个选择表达式引用的静态或保守基础端点。

    参数:
        dict_expression: kind 为 select 的 formatter 表达式节点。

    返回:
        可完整恢复时返回精确选择端点，否则返回基础信号集合。
    """

    # 第一个操作数是被选择对象，其余操作数描述索引或切片边界。
    list_operands = list(dict_expression.get("operands", []))  # 选择节点的基础值与索引节点

    # 缺少类型化基础值时没有可信引用可供上游追踪。
    if not list_operands or not isinstance(list_operands[0], dict):

        # 空集合让调用方保持当前表达式的局部依赖边界。
        return set()

    # 基础表达式可能自身包含可解析的静态选择端点。
    set_base_targets = _reference_targets(list_operands[0])  # 被选择对象对应的基础端点集合

    # 动态索引或多基础引用只能保守回退到整信号。
    bool_static_single_base = (  # 当前选择是否具备唯一静态基础端点
        not bool(dict_expression.get("dynamic"))  # formatter 已确认索引不是运行时表达式
        and len(set_base_targets) == 1  # 选择器只能附着到一个明确基础端点
    )

    # 无法精确恢复时仍保留所有基础生产者依赖。
    if not bool_static_single_base:

        # 去除嵌套选择器，避免构造不存在的精确生产者名称。
        return {_base_target(str_item) for str_item in set_base_targets}

    # 常量索引文本按 formatter 操作数顺序组成位选或切片。
    list_indices = [  # 当前选择器包含的常量索引文本
        str(dict_item.get("value") or "")  # 单个索引或切片边界文本
        for dict_item in list_operands[1:]  # 跳过第一个基础表达式操作数
        if isinstance(dict_item, dict)  # 仅接受 formatter 类型化索引节点
    ]

    # 任一索引节点缺失都会使精确选择器无法重建。
    if len(list_indices) != len(list_operands) - 1:

        # 不完整索引退回基础生产者，防止伪造静态端点。
        return {_base_target(str_item) for str_item in set_base_targets}

    # formatter operator 区分单比特选择和带方向的切片。
    str_separator = str(dict_expression.get("operator") or "bit")  # 选择器种类或切片分隔符

    # 选择器文本保持原有位选与切片的格式语义。
    str_selector = _static_selector_text(list_indices, str_separator)  # 已恢复的常量选择器正文

    # 唯一基础端点从单元素集合中确定取出。
    str_base_target = next(iter(set_base_targets))  # 选择器附着的基础端点文本

    # 返回与静态左值端点格式一致的引用名称。
    return {f"{str_base_target}[{str_selector}]"}

# 位选和切片使用不同文本形状，单独封装可降低引用提取分支数。
def _static_selector_text(list_indices: list[str], str_separator: str) -> str:
    """把 formatter 常量索引恢复为位选或切片正文。

    参数:
        list_indices: 按源码顺序保存的常量索引文本。
        str_separator: bit 标记或 formatter 保留的切片分隔符。

    返回:
        可直接放入方括号的静态选择器正文。
    """

    # 单比特选择只需要第一个常量索引。
    if str_separator == "bit":

        # 保持原有单索引端点文本。
        return list_indices[0]

    # 切片按 formatter 给出的分隔符连接两个边界。
    return f"{list_indices[0]}{str_separator}{list_indices[1]}"

# 常量折叠只接受完全不含标识符或动态选择的表达式树。
def _is_constant_expression(dict_expression: dict[str, Any]) -> bool:
    """判断表达式是否完全由常量叶节点构成。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        全部叶节点可在 elaboration 阶段确定时返回 True。
    """

    # 节点种类决定常量叶、运行时叶与复合表达式的分流。
    str_kind = str(dict_expression.get("kind") or "")  # 当前待判定节点的 formatter 种类

    # 常量叶节点自身满足折叠条件。
    if str_kind == "constant":

        # 字面量无需继续检查子树。
        return True

    # 标识符、未支持结构和动态选择依赖运行时信号。
    if str_kind in {"identifier", "unsupported"} or bool(dict_expression.get("dynamic")):

        # 任一运行时依赖都会阻止整棵子树常量折叠。
        return False

    # 复合节点只检查 formatter 已类型化的操作数子树。
    list_operands = [  # 当前复合表达式的类型化操作数
        dict_item  # 可递归判断常量性的操作数节点
        for dict_item in dict_expression.get("operands", [])  # formatter 提供的全部操作数
        if isinstance(dict_item, dict)  # 排除不具备表达式合同的叶值
    ]

    # 非空操作数全部为常量时，当前运算也可在 elaboration 阶段折叠。
    return bool(list_operands) and all(_is_constant_expression(dict_item) for dict_item in list_operands)

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

# 顶层覆盖判定把 formatter 事实转换成统一的递归路径合同。
def _facts_cover_all_paths(list_facts: list[dict[str, Any]]) -> bool:
    """判断同一目标的事实集合是否覆盖完整运行时控制树。

    参数:
        list_facts: 同一静态端点的全部 formatter 驱动事实。

    返回:
        存在无条件赋值或每层决策的两个分支均完整时返回 True。
    """

    # 每条赋值只保留 formatter 记录的互斥分支路径。
    list_paths = [  # 当前目标全部赋值的运行时分支路径
        list(dict_fact.get("branch_path", []))  # 单条事实的可变递归副本
        for dict_fact in list_facts  # 收集同一端点各赋值的控制路径
    ]

    # 顶层与嵌套层使用同一个分支完备性证明算法。
    return _paths_cover_all(list_paths)

# 递归覆盖证明要求同一决策的 then 与 else 子树分别完整。
def _paths_cover_all(list_paths: list[list[dict[str, Any]]]) -> bool:
    """递归证明当前父分支下的全部运行时路径均有赋值。

    参数:
        list_paths: 当前父分支下各赋值尚未消费的决策路径。

    返回:
        存在无条件赋值或任一完整决策的两侧子树均覆盖时返回 True。
    """

    # 空尾路径表示当前父分支内存在无条件赋值。
    if any(not list_path for list_path in list_paths):

        # 无条件赋值覆盖当前父分支的所有后续运行时选择。
        return True

    # 同层决策编号用于分别尝试可证明完整的控制树。
    set_ids = {  # 当前父分支下出现的决策编号
        str(list_path[0].get("id") or "")  # 每条路径的首个未消费决策
        for list_path in list_paths  # 遍历当前父分支的全部赋值路径
    }

    # 任一决策的两个分支都完整即可覆盖当前父分支。
    return any(
        _decision_paths_cover_all(list_paths, str_id)  # 分别证明该决策两侧子树
        for str_id in set_ids  # 尝试当前层出现的全部决策编号
    )

# 单决策辅助函数隔离 then 与 else 路径，防止空尾跨分支误覆盖。
def _decision_paths_cover_all(
    list_paths: list[list[dict[str, Any]]],
    str_id: str,
) -> bool:
    """证明一个决策编号的 then 与 else 子树分别完整。

    参数:
        list_paths: 当前父分支下各赋值尚未消费的决策路径。
        str_id: 当前需要证明的 formatter 决策编号。

    返回:
        两个分支均存在且各自递归覆盖全部路径时返回 True。
    """

    # 两侧路径必须独立收集，禁止一侧空尾替另一侧证明完整。
    dict_branch_paths: dict[str, list[list[dict[str, Any]]]] = {  # then 与 else 的剩余子路径
        "then": [],  # 当前决策真分支的剩余路径
        "else": [],  # 当前决策假分支的剩余路径
    }

    # 只消费编号匹配且 formatter 明确含 alternate 的完整决策。
    for list_path in list_paths:

        # 首节点描述当前赋值在该层选择的决策与分支。
        dict_decision = list_path[0]  # 当前路径首个未消费决策

        # 其他编号或缺少 alternate 的决策不能证明当前控制树完整。
        if str(dict_decision.get("id") or "") != str_id or not bool(dict_decision.get("complete")):

            # 保留路径给其他决策编号尝试，不纳入当前分支证明。
            continue

        # formatter 只接受 then 与 else 两种运行时分支极性。
        str_branch = str(dict_decision.get("branch") or "")  # 当前路径选择的分支极性

        # 未知极性不能参与完整性证明。
        if str_branch in dict_branch_paths:

            # 消费当前决策后把剩余子路径归入对应分支。
            dict_branch_paths[str_branch].append(list_path[1:])

    # 两侧必须各自存在赋值路径，并分别递归证明完整。
    return all(
        list_branch_paths and _paths_cover_all(list_branch_paths)  # 当前分支非空且完整
        for list_branch_paths in dict_branch_paths.values()  # then 与 else 分开验证
    )

# 选择器编号区分 case default、显式 case 项和普通 if 条件。
def _selector_id(
    dict_control: dict[str, Any],
    str_target: str,
    int_index: int,
) -> str:
    """从条件根节点派生一次真实控制选择操作编号。

    参数:
        dict_control: formatter 输出的控制表达式节点。
        str_target: 当前控制条件约束的基础目标。
        int_index: 控制条件在当前事实控制栈中的序号。

    返回:
        稳定选择操作编号；case default 返回空字符串。
    """

    # case 项由 formatter 显式提供 selector_id，default 值为空。
    if "selector_id" in dict_control:

        # 保留空值语义，防止 default 分支虚增选择操作。
        return str(dict_control.get("selector_id") or "")

    # 普通控制条件优先派生自真实根操作节点编号。
    str_root_id = str(dict_control.get("occurrence_id") or "")  # 控制根节点编号

    # 有根操作编号时生成与语法位置稳定关联的选择编号。
    if str_root_id:

        # 后缀区分条件表达式自身操作与分支选择操作。
        return f"{str_root_id}:selector"

    # 纯标识符条件没有操作编号，使用目标和控制序号生成稳定编号。
    return f"{str_target}:control{int_index}:selector"

# 静态端点规范化保留位选和切片，只删除无语义空白。
def _static_target(str_target: str) -> str:
    """返回保留常量选择器的规范静态目标。

    参数:
        str_target: formatter 赋值事实中的原始目标文本。

    返回:
        删除空白但保留位选或切片的静态端点名称。
    """

    # 空白不参与端点身份，选择器文本则必须完整保留。
    return "".join(str_target.split())

# 基础信号只用于精确端点不存在时的保守依赖回退。
def _base_target(str_target: str) -> str:
    """把静态选择目标规范为当前基础信号。

    参数:
        str_target: formatter 赋值事实中的目标文本。

    返回:
        去除首个选择器并清理空白后的基础信号名称。
    """

    # 第一个左方括号之前的文本就是当前基础目标。
    return str_target.split("[", 1)[0].strip()
