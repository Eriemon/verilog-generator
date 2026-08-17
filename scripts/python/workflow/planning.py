"""把规范化规格拆解为 Verilog 生成流程可消费的计划结构。"""

# 延迟注解解析，避免导入期求值复杂类型提示。
from __future__ import annotations

# 标准库依赖用于复制用户规格并保留原始输入隔离。
import copy
from typing import Any

# 运行时子模块提供证据索引和规格规范化能力。
from scripts.python.validation.evidence import evidence_refs_for_text
from .spec import normalize_checkpoint_items, normalize_info_items, normalize_spec, normalize_subfunction

# 公开入口负责生成 workflow、prompt 和 review 共用的分解计划。
def decompose_spec(
    raw: dict[str, Any],
    target: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把用户规格拆成带语义检查点和依赖图的计划字典。

    参数:
        raw: 用户传入或配置文件读取到的原始规格字典。
        target: 可选目标类型，用于约束规格规范化流程。
        evidence: 可选证据索引，用于给行为、约束和测试意图附加引用。

    返回:
        保留既有 JSON 字段形状的计划字典，包含 subfunctions、semantic_checkpoints
        和 subfunction_dependency_graph。
    """

    # 先复用规格模块的入口，保证目标、接口和元信息字段已经标准化。
    dict_spec = normalize_spec(raw, target=target)  # 已规范化规格

    # 深拷贝隔离调用方输入，后续计划补全不会反写原始规格。
    dict_plan = copy.deepcopy(dict_spec)  # 可变计划副本

    # 没有显式子功能时，用顶层 RTL 端口合成一个默认子功能。
    if not dict_plan.get("subfunctions"):

        # 顶层子功能承接旧版单模块规格的默认拆解行为。
        dict_plan["subfunctions"] = [_default_subfunction(dict_plan)]  # 默认子功能列表

    # 显式拆分时逐项规范化子功能节点。
    else:

        # 显式子功能需要逐项补齐检查点，保持用户拆解粒度。
        dict_plan["subfunctions"] = _normalized_subfunctions(dict_plan)  # 用户子功能列表

    # 顶层检查点覆盖整个模块输出，供后续 prompt 和测试计划引用。
    tuple_rtl_io = _rtl_io(dict_plan)  # 顶层输入输出端口

    # 顶层 semantic_checkpoints 继续支持用户显式覆盖。
    dict_plan["semantic_checkpoints"] = _semantic_checkpoints(  # 顶层检查点列表
        dict_plan,  # 顶层计划规格
        tuple_rtl_io[1],  # 顶层输出端口
        explicit=dict_plan.get("semantic_checkpoints"),  # 用户显式检查点
    )

    # 依赖图以子功能名称为节点，供审计报告和后续规划阶段展示。
    dict_plan["subfunction_dependency_graph"] = _dependency_graph(dict_plan)  # 子功能依赖图

    # 只有存在证据索引时才附加 evidence 字段，避免空证据污染计划。
    if evidence:

        # evidence 附加是原地修改计划中的条目，保持返回字典结构简单。
        _attach_evidence(dict_plan, evidence)

    # 返回完整计划供 workflow 继续生成 prompt、artifact 和报告。
    return dict_plan

# 默认子功能构造器兼容没有 subfunctions 的历史 RTL 规格。
def _default_subfunction(dict_plan: dict[str, Any]) -> dict[str, Any]:
    """构造缺省单子功能计划。

    参数:
        dict_plan: 已规范化的顶层计划规格。

    返回:
        兼容旧版单模块规格的子功能字典。
    """

    # 顶层输入输出端口直接成为默认子功能接口。
    tuple_rtl_io = _rtl_io(dict_plan)  # 默认子功能继承的顶层端口对

    # 明确拆出输入端口，便于默认子功能字典逐字段保持原形。
    list_inputs = tuple_rtl_io[0]  # 默认子功能输入端口

    # 明确拆出输出端口，后续语义检查点只关注可观察输出。
    list_outputs = tuple_rtl_io[1]  # 默认子功能输出端口

    # 默认子功能字段名属于对外计划形状，不能随内部命名规范改动。
    dict_subfunction = {
        "name": dict_plan["name"],  # 顶层模块名
        "inputs": list_inputs,  # 单模块规格映射出的输入集合
        "outputs": list_outputs,  # 单模块规格可观察输出集合
        "behavior": normalize_info_items(dict_plan.get("behavior"), "behavior"),  # 行为条目
        "constraints": normalize_info_items(dict_plan.get("constraints"), "constraints"),  # 约束条目
        "dependencies": [],  # 默认子功能无前置子功能
        "source_references": [],  # 默认子功能无额外来源引用
        "test_intent": normalize_info_items(_test_intent(dict_plan), "test_intent"),  # 测试意图
        "semantic_checkpoints": _semantic_checkpoints(  # 默认子功能检查点
            dict_plan,  # 默认检查点读取的完整规格
            list_outputs,  # 复位和行为检查可观察的输出端口
            explicit=dict_plan.get("semantic_checkpoints"),  # 顶层用户检查点覆盖项
        ),  # 绑定到默认子功能的检查点
    }  # 单子功能计划

    # 返回默认拆解结果，调用方会放入 subfunctions 列表。
    return dict_subfunction

# 显式子功能规范化器为每个用户拆分节点补齐检查点。
def _normalized_subfunctions(dict_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """规范化用户显式声明的子功能列表。

    参数:
        dict_plan: 已规范化且包含 subfunctions 的计划规格。

    返回:
        已补齐检查点的子功能列表。
    """

    # 保留子功能顺序，便于依赖图和提示词沿用用户输入顺序。
    list_normalized_subfunctions: list[dict[str, Any]] = []  # 规范化子功能列表

    # enumerate 的 index 传给 normalize_subfunction 以生成稳定默认名称。
    for subfunction_index, subfunction_item in enumerate(dict_plan["subfunctions"]):

        # 子功能规范化统一补齐 name、behavior、interfaces 等基础字段。
        dict_normalized = normalize_subfunction(subfunction_item, subfunction_index)  # 当前子功能

        # 检查点构造只需要当前子功能名、行为和输出端口。
        dict_checkpoint_spec = {
            "name": dict_normalized["name"],  # 子功能名
            "behavior": dict_normalized.get("behavior", []),  # 子功能行为条目
            "interfaces": {"ports": dict_normalized.get("outputs", [])},  # 子功能输出端口
        }  # 检查点输入规格

        # 用户显式检查点优先，缺失时才按行为和输出自动补齐。
        dict_normalized["semantic_checkpoints"] = _semantic_checkpoints(  # 子功能检查点列表
            dict_checkpoint_spec,  # 当前子功能的检查点来源规格
            dict_normalized.get("outputs", []),  # 当前节点暴露给 transcript 的输出端口
            explicit=dict_normalized.get("semantic_checkpoints"),  # 子功能显式检查点
        )

        # 将补齐后的子功能放回计划，保持下游消费结构不变。
        list_normalized_subfunctions.append(dict_normalized)

    # 返回全部规范化子功能。
    return list_normalized_subfunctions

# RTL 端口拆分器从 interfaces.ports 中提取输入和输出端口。
def _rtl_io(dict_spec: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    """从规格接口字段拆分输入和输出端口。

    参数:
        dict_spec: 含 interfaces.ports 字段的规格对象。

    返回:
        输入端口列表和输出端口列表。
    """

    # interfaces 可能缺省，按空端口列表处理以保持宽容输入。
    list_ports = dict_spec.get("interfaces", {}).get("ports", [])  # 顶层端口列表

    # 输入端口只保留 dict 形态且 direction 明确为 input 的项目。
    list_inputs = [
        port_item  # 输入端口对象
        for port_item in list_ports  # 顶层输入端口候选
        if isinstance(port_item, dict) and port_item.get("direction") == "input"  # input 方向端口
    ]  # 输入端口

    # 可观察端口集合只收集后续检查点能够直接比对的输出项。
    list_outputs = [
        port_item  # 输出端口对象
        for port_item in list_ports  # 顶层可观察端口候选
        if isinstance(port_item, dict) and port_item.get("direction") == "output"  # 可观察输出端口
    ]  # 输出端口

    # 返回输入和输出两个端口集合，调用方按位置消费。
    return list_inputs, list_outputs

# 测试意图构造器为默认子功能补齐最小验证目标。
def _test_intent(dict_spec: dict[str, Any]) -> list[str]:
    """生成默认子功能的测试意图列表。

    参数:
        dict_spec: 已规范化的规格对象。

    返回:
        默认验证目标和行为派生目标的合并列表。
    """

    # 默认测试意图覆盖 reset 和正常事务两个生成流程必需场景。
    list_intents = [
        "Reset behavior drives outputs to known values.",  # reset 验证目标
        "Nominal transactions match the Python semantic model.",  # 参考模型一致性目标
    ]  # 默认测试意图

    # 用户行为条目会扩展为可验证的行为检查目标。
    for behavior_item in dict_spec.get("behavior", []) or []:

        # 将行为条目收敛成 checkpoint 可展示的说明文本。
        str_behavior_text = behavior_item.get("text") if isinstance(behavior_item, dict) else str(behavior_item)  # 检查点描述文本

        # 保留英文提示文本，避免改变下游 prompt 和既有测试期望。
        list_intents.append(f"Verify behavior: {str_behavior_text}")

    # 返回默认意图和行为派生意图的合并列表。
    return list_intents

# 检查点构造器统一处理用户显式检查点和自动派生检查点。
def _semantic_checkpoints(
    dict_spec: dict[str, Any],
    list_outputs: list[Any],
    *,
    explicit: Any = None,
) -> list[dict[str, Any]]:
    """构造语义检查点列表。

    参数:
        dict_spec: 当前计划或子功能规格。
        list_outputs: 可观察输出端口列表。
        explicit: 用户显式声明的检查点。

    返回:
        规范化后的显式检查点，或自动派生的检查点列表。
    """

    # 显式检查点属于用户意图，优先通过 spec 模块做规范化后返回。
    if explicit:

        # 规范化入口保留既有字段兼容性，并补齐缺省 id/text 等信息。
        return normalize_checkpoint_items(explicit)

    # reset 检查点是所有 RTL 生成计划的基础可验证状态。
    dict_reset_checkpoint = {  # 无显式检查点时强制验证复位初始化
        "id": "reset_known_state",  # 复位已知状态标识
        "category": "reset",  # 复位阶段分类
        "signals": [str((dict_spec.get("reset") or {}).get("name") or "rst_n")],  # 复位控制信号名
        "verification_hint": "Check reset-driven initialization before nominal traffic.",  # 复位初始化验证提示
        "text": "Outputs and sequential state settle to known values after reset.",  # 复位后状态稳定说明
    }  # reset 后输出和时序状态已知值检查

    # reset 基线之后会继续追加行为验证和公开输出观察项。
    list_checkpoints: list[dict[str, Any]] = [dict_reset_checkpoint]  # 自动派生验证要求

    # 每条行为描述生成一个检查点，促使参考模型和 RTL transcript 对齐。
    for behavior_index, behavior_item in enumerate(dict_spec.get("behavior", []) or [], start=1):

        # dict 行为优先使用 text 字段；其它形态退回字符串表达。
        str_behavior_text = behavior_item.get("text") if isinstance(behavior_item, dict) else str(behavior_item)  # 行为文本

        # 行为检查点优先引用前两个输出信号，避免 transcript 过度膨胀。
        list_behavior_signals = _signal_names(list_outputs[:2])  # 行为关联输出信号

        # 长提示文本独立成变量，避免字典字段行过宽。
        str_behavior_hint = (
            "Emit this checkpoint from collect_checkpoints(case) and mirror it in the RTL transcript."  # checkpoint 镜像要求
        )  # 行为检查点 transcript 对齐提示

        # 行为检查点记录验证提示，供 prompt 要求 collect_checkpoints 暴露证据。
        list_checkpoints.append(
            {
                "id": f"behavior_checkpoint_{behavior_index}",  # 行为检查点标识
                "category": "behavior",  # 检查点类别
                "signals": list_behavior_signals,  # 行为关联信号
                "verification_hint": str_behavior_hint,  # 验证提示
                "text": str_behavior_text,  # 行为说明文本
            }
        )

    # 前四个公开输出会生成可观察检查点，覆盖常见接口验证证据。
    for output_item in list_outputs[:4]:

        # 只有带 name 的 dict 输出端口才能形成可观察信号检查点。
        if isinstance(output_item, dict) and output_item.get("name"):

            # 输出端口名同时进入 id、signals 和验证说明。
            str_output_name = str(output_item["name"])  # 输出端口名

            # 输出检查点帮助 semantic model 与 RTL transcript 对齐公开输出。
            list_checkpoints.append(_observable_output_checkpoint(str_output_name))

    # 返回自动生成的检查点列表。
    return list_checkpoints

# 信号名提取器把端口项目转换为检查点中的 signal 名称。
def _signal_names(list_outputs: list[Any]) -> list[str]:
    """提取检查点可引用的输出信号名。

    参数:
        list_outputs: 输出端口对象列表。

    返回:
        已转换为字符串的信号名列表。
    """

    # 保留输出顺序，方便生成的检查点和端口声明顺序一致。
    list_signals: list[str] = []  # 检查点信号名

    # 逐个输出端口提取 name 字段。
    for output_item in list_outputs:

        # 只有 dict 输出端口且 name 非空时才加入检查点。
        if isinstance(output_item, dict) and output_item.get("name"):

            # signal 字段必须是字符串，避免 JSON 中混入非字符串端口名。
            list_signals.append(str(output_item["name"]))

    # 返回检查点可直接使用的 signal 名称。
    return list_signals

# 输出检查点构造器集中维护 observe_* 字段形状。
def _observable_output_checkpoint(str_output_name: str) -> dict[str, Any]:
    """构造单个公开输出端口的观察检查点。

    参数:
        str_output_name: 需要观察的输出端口名。

    返回:
        observe_* 形态的检查点字典。
    """

    # 输出检查点字段保持计划 JSON 形状兼容，并把长提示文本独立成变量以避免字典行过宽。
    str_output_hint = (
        f"Observe `{str_output_name}` in both reference checkpoints and transcript output payloads."  # 输出观察镜像要求
    )  # 输出 transcript 对齐提示

    # 输出检查点字段描述单个公开端口的 transcript 观测要求。
    dict_checkpoint = {
        "id": f"observe_{str_output_name}",  # 输出观察检查点标识
        "category": "observable_output",  # 公开输出观测类别
        "signals": [str_output_name],  # 被观察输出信号
        "verification_hint": str_output_hint,  # transcript 镜像提示
        "text": f"Observe public output `{str_output_name}` for expected behavior changes.",  # 检查点文本
    }  # 输出检查点

    # 返回单个公开输出的观察检查点。
    return dict_checkpoint

# 依赖图构造器把子功能列表转换为节点和边。
def _dependency_graph(dict_spec: dict[str, Any]) -> dict[str, Any]:
    """把子功能声明转换为依赖图。

    参数:
        dict_spec: 含 subfunctions 字段的计划规格。

    返回:
        包含 nodes 和 edges 的依赖图字典。
    """

    # 节点记录每个子功能的名称、输出和检查点 id。
    list_nodes: list[dict[str, Any]] = []  # 子功能节点

    # 边记录 dependencies 声明形成的子功能依赖关系。
    list_edges: list[dict[str, Any]] = []  # 子功能依赖边

    # 遍历规范化后的子功能，过滤无名称或非 dict 项。
    for subfunction_item in dict_spec.get("subfunctions", []):

        # 非 dict 或缺 name 的项不能形成依赖图节点。
        if not isinstance(subfunction_item, dict) or not subfunction_item.get("name"):

            # 跳过异常项，保持依赖图构造对历史输入宽容。
            continue

        # 子功能名作为节点 id 和边的终点。
        str_subfunction_name = str(subfunction_item["name"])  # 子功能名称

        # 节点输出字段暴露该子功能产生的端口或中间信号名。
        list_node_outputs = _node_output_names(subfunction_item)  # 节点输出名

        # 节点检查点字段暴露该子功能关联的 semantic checkpoint id。
        list_checkpoint_ids = _checkpoint_ids(subfunction_item)  # 节点检查点标识

        # 节点字段形状保持既有审计报告兼容性。
        list_nodes.append(
            {
                "id": str_subfunction_name,  # 节点标识
                "name": str_subfunction_name,  # 节点展示名
                "outputs": list_node_outputs,  # 子功能输出名
                "semantic_checkpoints": list_checkpoint_ids,  # 子功能检查点 id
            }
        )

        # dependencies 中每个条目都生成一条指向当前子功能的边。
        for dependency_item in subfunction_item.get("dependencies", []) or []:

            # 边字段保持 from/to/kind 的既有 JSON 形状。
            dict_edge = {
                "from": str(dependency_item),  # 依赖来源子功能
                "to": str_subfunction_name,  # 依赖汇入的目标子功能
                "kind": "subfunction_dependency",  # 依赖边类型
            }  # 计划依赖图中的有向边

            # 记录当前依赖边，供审计报告渲染。
            list_edges.append(dict_edge)

    # 返回依赖图字典，节点和边字段名保持对外兼容。
    return {"nodes": list_nodes, "edges": list_edges}

# 节点输出提取器把子功能 outputs 字段整理为字符串名称。
def _node_output_names(dict_subfunction: dict[str, Any]) -> list[str | None]:
    """整理依赖图节点的输出名称。

    参数:
        dict_subfunction: 子功能字典。

    返回:
        保持旧兼容性的输出名列表。
    """

    # 输出名列表保留子功能 outputs 的原始顺序。
    list_outputs: list[str | None] = []  # 依赖图节点展示的输出端口名

    # 遍历 outputs 字段，兼容 dict 端口和字符串端口两种形态。
    for output_item in dict_subfunction.get("outputs", []) or []:

        # dict 输出端口保留 name 字段；缺失 name 时兼容旧行为返回 None。
        if isinstance(output_item, dict):

            # 旧逻辑直接使用 item.get("name")，这里保留 None 兼容性。
            list_outputs.append(output_item.get("name"))

        # 字符串等非对象输出项按展示名处理。
        else:

            # 非 dict 输出项转成字符串，匹配旧版依赖图输出规则。
            list_outputs.append(str(output_item))

    # 返回当前节点的输出名列表。
    return list_outputs

# 检查点 id 提取器收集子功能检查点中的 id 字段。
def _checkpoint_ids(dict_subfunction: dict[str, Any]) -> list[Any]:
    """提取子功能检查点 id 列表。

    参数:
        dict_subfunction: 含 semantic_checkpoints 字段的子功能字典。

    返回:
        保留原始 id 类型的检查点标识列表。
    """

    # 检查点 id 保持原始类型，兼容旧版 item.get("id") 行为。
    list_ids: list[Any] = []  # 当前子功能声明的检查点标识

    # 仅 dict 形态检查点包含可提取 id 字段。
    for checkpoint_item in dict_subfunction.get("semantic_checkpoints", []):

        # 非 dict 检查点无法提供 id，保持旧逻辑过滤行为。
        if isinstance(checkpoint_item, dict):

            # id 可能为 None，旧版列表推导会保留该值。
            list_ids.append(checkpoint_item.get("id"))

    # 返回检查点 id 列表。
    return list_ids

# 证据附加器把 evidence 索引命中的引用写回子功能条目。
def _attach_evidence(dict_spec: dict[str, Any], dict_evidence: dict[str, Any]) -> None:
    """把证据引用附加到子功能信息条目。

    参数:
        dict_spec: 需要原地补充 evidence 的计划规格。
        dict_evidence: evidence 索引对象。

    返回:
        无返回值。
    """

    # 每个子功能都可能包含行为、约束和测试意图三类可引用文本。
    for subfunction_item in dict_spec.get("subfunctions", []):

        # 三个字段共享同一证据查找逻辑。
        for field_name in ("behavior", "constraints", "test_intent"):

            # 单字段 helper 隔离条目类型判断，避免证据遍历形成深层嵌套。
            _attach_field_evidence(subfunction_item.get(field_name, []), dict_evidence)

# 字段证据 helper 隔离条目类型判断和命中引用写回。
def _attach_field_evidence(list_items: list[Any], dict_evidence: dict[str, Any]) -> None:
    """给一个计划信息字段中的字典条目附加证据引用。

    参数:
        list_items: behavior、constraints 或 test_intent 字段条目。
        dict_evidence: evidence 索引对象。

    返回:
        无返回值。
    """

    # 单层扫描保持原有条目顺序，并跳过没有可写字段的非字典值。
    for info_item in list_items:

        # 非 dict 条目没有可写 evidence 字段，保持原样跳过。
        if not isinstance(info_item, dict):

            # 调用方允许字段中混入纯文本条目。
            continue

        # 证据查找只基于 text 字段，避免把其它结构字段误当检索文本。
        list_refs = evidence_refs_for_text(dict_evidence, str(info_item.get("text", "")))  # 匹配证据引用

        # 有命中引用时才写入 evidence 字段，保持输出紧凑。
        if list_refs:

            # evidence 字段形状由 evidence 模块返回值决定。
            info_item["evidence"] = list_refs  # 命中的证据引用
