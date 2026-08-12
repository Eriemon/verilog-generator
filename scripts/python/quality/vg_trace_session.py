"""为跨层组合锥追踪建立调用级索引、上下文和 memo。"""

# 延迟注解求值允许 TraceContext 引用下方定义的 TraceSession。
from __future__ import annotations

# dataclass 与 Any 分别承载不可变身份对象和 formatter 异构事实。
from dataclasses import dataclass
from typing import Any

# 冻结 graph 模型提供 session 索引的事实、graph 和 producer 值类型。
from .vg_comb_model import (
    FrozenFact,
    HierarchyGraph,
    LoopPresence,
    ProducerRef,
)

# 作用域键、特化模块与解冻入口组成索引构建的另一组模型依赖。
from .vg_comb_model import (
    ScopedTarget,
    SpecializationKey,
    SpecializedModule,
    thaw_fact,
)

# 静态目标归一化用于构造 whole-target 本地事实索引。
from .vg_comb_selectors import base_target

# endpoint 查询只在 session 构建阶段执行，递归阶段读取已建索引。
from .vg_comb_targets import drivers_for_endpoint

# tracing 上下文把 operation occurrence 的全部身份维度绑定到当前端点。
@dataclass(frozen=True)
class TraceContext:
    """保存一次递归 tracing 的当前作用域与访问路径。

    参数:
        graph: 当前 definition root 的冻结 hierarchy graph。
        current: 当前正在分析的作用域静态目标。
        loop_iteration_tuple: 当前 operation 所属完整循环迭代身份。
        function_call_stack: 从外到内排列的完整函数调用栈。
        visiting: 当前数据依赖路径上已进入的作用域目标集合。
        session: 当前 definition root 私有的索引与 memo 容器。
    """

    # graph 提供模块 occurrence、端口 binding 与 producer 目录。
    graph: HierarchyGraph  # 当前冻结 hierarchy graph

    # current 决定本地事实、参数特化和 operation 作用域。
    current: ScopedTarget  # 当前 tracing 作用域目标

    # 循环身份区分同一源码 operation 的 elaborated 副本。
    loop_iteration_tuple: tuple[int, ...] = ()  # 当前完整循环迭代元组

    # 完整调用栈区分同一函数体从不同 call site 展开的 occurrence。
    function_call_stack: tuple[str, ...] = ()  # 当前完整函数调用栈

    # visiting 只检测当前递归路径，不跨独立 producer 误报环。
    visiting: frozenset[ScopedTarget] = frozenset()  # 当前数据依赖访问路径

    # session 在同一 root 的批量目标之间复用，不跨 graph 生命周期泄漏。
    session: TraceSession | None = None  # 当前调用级 tracing session

# tracing 结果同时保留已知操作下界、循环归属与局部未知原因。
@dataclass(frozen=True)
class TraceResult:
    """保存一个作用域目标的跨层 tracing 聚合结果。

    参数:
        operation_ids: 可达真实 operation occurrence 的唯一编号集合。
        loop_presence: 可达锥内循环存在性三态。
        inconclusive_reasons: 只污染当前可达路径的稳定原因集合。
    """

    # occurrence 集合按完整作用域身份去重，禁止按运算文本合并。
    operation_ids: frozenset[str]  # 可达 operation occurrence 集合

    # 三态循环归属决定 VG146/VG147 的最终所有权。
    loop_presence: LoopPresence  # 当前可达锥循环存在性

    # 原因排序后冻结，保证多 producer 输出稳定。
    inconclusive_reasons: tuple[str, ...]  # 当前目标局部不确定原因

# 调用级 session 把冻结 graph 的重复查询转换为一次性索引。
@dataclass
class TraceSession:
    """保存一个 definition root 内可安全复用的 tracing 状态。

    参数:
        graph: 当前 session 对应的唯一冻结 hierarchy graph。
        dict_modules: occurrence 路径与特化键到模块快照的索引。
        dict_drivers: 作用域端点到稳定 producer 元组的索引。
        dict_local_facts: 精确或基础目标到本地组合事实的索引。
        dict_storage_facts: storage Q 目标到时序驱动事实的索引。
        dict_lines: 精确目标到有效源码行号的索引。
        dict_memo: 完整 tracing 身份到已冻结结果的调用级缓存。

    返回:
        无；dataclass 实例由批量或单目标入口持有。
    """

    # graph 保留 root 身份、bindings 与全部冻结模块事实。
    graph: HierarchyGraph  # 当前 session 的唯一 hierarchy graph

    # module 索引消除每次递归对 graph.modules 的线性扫描。
    dict_modules: dict[tuple[tuple[str, ...], SpecializationKey], SpecializedModule]  # occurrence 模块索引

    # producer 索引消除每个 endpoint 重建完整字典的开销。
    dict_drivers: dict[ScopedTarget, tuple[ProducerRef, ...]]  # 递归驱动查询的 producer 元组索引

    # 本地事实索引同时保留精确 target 与 whole-target 位驱动汇聚语义。
    dict_local_facts: dict[ScopedTarget, tuple[dict[str, Any], ...]]  # endpoint 本地组合事实

    # storage 索引消除每个 D/enable projection 对全部时序事实的扫描。
    dict_storage_facts: dict[ScopedTarget, tuple[dict[str, Any], ...]]  # Q endpoint 时序驱动事实

    # 行号索引只记录精确 target，保持 finding 定位合同不变。
    dict_lines: dict[ScopedTarget, tuple[int, ...]]  # endpoint 精确驱动行号

    # memo 键包含 target、循环身份和函数栈，禁止合并不同 operation occurrence。
    dict_memo: dict[tuple[ScopedTarget, tuple[int, ...], tuple[str, ...]], TraceResult]  # 递归结果缓存

# 冻结事实解码只接受字典根，其他兼容值回落为空映射。
def _mapping(value: object) -> dict[str, Any]:
    """恢复一个 tracing 读取期事实字典。

    参数:
        value: FrozenFact 或普通字典兼容值。

    返回:
        与缓存断开引用的普通事实字典。
    """

    # FrozenFact 必须递归解冻才能读取 typed expression 子树。
    if isinstance(value, FrozenFact):

        # thaw_fact 返回普通容器并保持字段语义。
        obj_thawed = thaw_fact(value)  # 当前冻结事实的递归副本

        # 顶层不是字典时不能作为 module fact 使用。
        return dict(obj_thawed) if isinstance(obj_thawed, dict) else {}

    # 普通字典同样复制顶层，tracing 不写回输入对象。
    if isinstance(value, dict):

        # 浅副本足以隔离本层只读字段访问。
        return dict(value)

    # 其他值不具备 formatter 事实合同。
    return {}

# storage 索引构建只遍历一次全部时序事实。
def _index_storage_facts(graph: HierarchyGraph) -> dict[ScopedTarget, tuple[dict[str, Any], ...]]:
    """建立 Q endpoint 到时序驱动事实的调用级索引。

    参数:
        graph: 当前 definition root 的冻结 hierarchy graph。

    返回:
        按完整作用域 Q target 分组的不可变时序事实元组。
    """

    # 构建期列表按 Q target 保留全部过程驱动顺序。
    dict_storage_lists: dict[ScopedTarget, list[dict[str, Any]]] = {}  # Q endpoint 到时序事实列表

    # 每个 module occurrence 的 storage facts 在 session 建立时只解冻一次。
    for tuple_path, specialized_module in graph.modules:

        # D 与 enable 投影共享按 Q target 建立的一次性索引。
        for frozen_storage_fact in specialized_module.storage_drivers:

            # 当前事实先转换为与冻结 graph 断开引用的普通字典。
            dict_storage_fact = _mapping(frozen_storage_fact)  # 当前时序驱动的普通字典副本

            # Q target 去除 formatter 可能保留的空格后再建立作用域键。
            str_storage_target = str(dict_storage_fact.get("target") or "").replace(" ", "")  # 当前时序驱动的 Q 目标

            # 缺失 Q target 的事实无法绑定 synthetic endpoint。
            if not str_storage_target:

                # 无目标事实不能污染任何其他寄存器的 synthetic endpoint。
                continue

            # 完整作用域键隔离同名寄存器的实例 occurrence 和参数特化。
            scoped_storage = ScopedTarget(  # 为 D/enable 常数时间查找绑定完整 Q 作用域
                graph.root.identity,  # 时序事实所属的根定义身份
                tuple_path,  # 时序事实所属的完整实例路径
                specialized_module.key,  # 时序事实所属的参数特化键
                str_storage_target,  # 格式化器提供的规范寄存器目标
            )

            # 同一 Q target 的多过程事实按 formatter 原顺序聚合。
            dict_storage_lists.setdefault(scoped_storage, []).append(dict_storage_fact)

    # 冻结列表后，D 与 enable projection 只能只读共享当前索引。
    return {
        scoped_target: tuple(list_facts)
        for scoped_target, list_facts in dict_storage_lists.items()
    }

# 单个模块的组合事实索引保持精确目标、基础目标和行号语义一致。
def _append_module_comb_indexes(
    graph: HierarchyGraph,
    tuple_path: tuple[str, ...],
    specialized_module: SpecializedModule,
    dict_fact_lists: dict[ScopedTarget, list[dict[str, Any]]],
    dict_line_lists: dict[ScopedTarget, list[int]],
) -> None:
    """把一个模块 occurrence 的组合事实加入构建期索引。

    参数:
        graph: 当前 definition root 的冻结 hierarchy graph。
        tuple_path: 当前模块 occurrence 的完整实例路径。
        specialized_module: 当前路径绑定的特化模块快照。
        dict_fact_lists: 正在构建的 endpoint 事实列表索引。
        dict_line_lists: 正在构建的精确 endpoint 行号索引。

    返回:
        无；两个构建期字典由当前函数原地追加。
    """

    # 当前 occurrence 的每条 fact 只解冻一次。
    for frozen_fact in specialized_module.comb_expressions:

        # 当前事实先转换为 session 生命周期内的普通只读字典。
        dict_fact = _mapping(frozen_fact)  # 当前组合事实的普通字典副本

        # 精确目标文本去除空格后用于完整作用域索引。
        str_exact_target = str(dict_fact.get("target") or "").replace(" ", "")  # 当前事实的规范精确目标

        # 缺失 target 的兼容事实继续由其他 fail-closed 路径处理。
        if not str_exact_target:

            # 无目标事实不能建立稳定 ScopedTarget 键。
            continue

        # 精确键保留静态 bit、slice 或 whole target 文本。
        scoped_exact = ScopedTarget(  # 当前事实的完整精确作用域身份
            graph.root.identity,  # 组合事实所属 definition root
            tuple_path,  # 组合事实所属实例路径
            specialized_module.key,  # 组合事实所属参数特化
            str_exact_target,  # formatter 提供的静态目标
        )

        # 精确查询按模块事实顺序返回全部 producer。
        dict_fact_lists.setdefault(scoped_exact, []).append(dict_fact)

        # finding 行号只登记到精确静态目标。
        dict_line_lists.setdefault(scoped_exact, []).append(int(dict_fact.get("line") or 1))

        # whole-target 查询继续汇聚静态 bit/slice 驱动。
        str_base_target = base_target(str_exact_target)  # 当前事实的基础向量目标

        # 已经是 whole target 时不建立重复索引键。
        if str_base_target == str_exact_target:

            # 当前事实无需再写入一个等价基础目标键。
            continue

        # 基础键只替换 target，其他 occurrence 身份保持不变。
        scoped_base = ScopedTarget(  # 当前 bit 或 slice 事实对应的 whole-target 身份
            graph.root.identity,  # 基础目标所属 definition root
            tuple_path,  # 基础目标所属实例路径
            specialized_module.key,  # 基础目标所属参数特化
            str_base_target,  # 位选或切片所属基础向量
        )

        # 基础目标按原始模块顺序汇聚全部静态选择器事实。
        dict_fact_lists.setdefault(scoped_base, []).append(dict_fact)

# 本地事实与行号索引由同一次模块事实遍历共同构建。
def _index_local_facts(
    graph: HierarchyGraph,
) -> tuple[dict[ScopedTarget, tuple[dict[str, Any], ...]], dict[ScopedTarget, tuple[int, ...]]]:
    """建立组合事实与精确驱动行号的调用级索引。

    参数:
        graph: 当前 definition root 的冻结 hierarchy graph。

    返回:
        endpoint 事实元组索引与精确 endpoint 行号元组索引。
    """

    # 两个构建期列表保持 formatter 原始事实顺序。
    dict_fact_lists: dict[ScopedTarget, list[dict[str, Any]]] = {}  # endpoint 到有序本地事实列表

    # 行号列表单独保留精确目标定位，不受基础目标汇聚影响。
    dict_line_lists: dict[ScopedTarget, list[int]] = {}  # endpoint 到精确驱动行号列表

    # 每个 occurrence 委派给单模块 helper，限制单个函数职责和长度。
    for tuple_path, specialized_module in graph.modules:

        # 单模块 helper 共同更新事实和行号构建期索引。
        _append_module_comb_indexes(
            graph,
            tuple_path,
            specialized_module,
            dict_fact_lists,
            dict_line_lists,
        )

    # 冻结构建期列表，递归 tracing 只读取不可变元组。
    dict_local_facts: dict[ScopedTarget, tuple[dict[str, Any], ...]] = {  # endpoint 到不可变本地事实元组
        scoped_target: tuple(list_facts)  # 当前 endpoint 保留的 formatter 事实顺序
        for scoped_target, list_facts in dict_fact_lists.items()  # 遍历全部构建期组合事实分组
    }

    # 行号元组允许 cone 组装直接选择最早驱动位置。
    dict_lines: dict[ScopedTarget, tuple[int, ...]] = {  # endpoint 到不可变精确行号元组
        scoped_target: tuple(list_lines)  # 当前精确 endpoint 的全部驱动行号
        for scoped_target, list_lines in dict_line_lists.items()  # 遍历全部精确端点行号分组
    }

    # 两项索引来自同一次模块事实遍历，必须作为同一快照返回。
    return dict_local_facts, dict_lines

# session 构建把一次性索引和调用级 memo 收束为稳定对象。
def _build_trace_session(graph: HierarchyGraph) -> TraceSession:
    """为一个 definition root 构建调用级 tracing 索引。

    参数:
        graph: 当前 definition root 的冻结 hierarchy graph。

    返回:
        仅在当前批量或单目标调用内复用的 session。
    """

    # 路径与完整特化键共同定位唯一模块快照。
    dict_modules: dict[tuple[tuple[str, ...], SpecializationKey], SpecializedModule] = {  # 当前 graph 的 occurrence 模块索引
        (tuple_path, specialized_module.key): specialized_module  # 当前 occurrence 对应的冻结模块快照
        for tuple_path, specialized_module in graph.modules  # 遍历当前 root 的全部模块 occurrence
    }

    # producer 冻结元组在 session 创建时只复制一次顶层映射。
    dict_drivers = dict(graph.endpoint_drivers)  # 当前 graph 的 producer 查找表

    # storage facts 独立建立 Q 目标索引，供 D 与 enable 投影共享。
    dict_storage_facts = _index_storage_facts(graph)  # Q endpoint 到时序事实的索引

    # 本地组合事实与精确行号必须来自同一冻结 graph 快照。
    tuple_local_indexes = _index_local_facts(graph)  # 组合事实索引与精确行号索引快照

    # 第一个返回项始终是 endpoint 到组合事实元组的映射。
    dict_local_facts = tuple_local_indexes[0]  # 当前 graph 的组合事实索引

    # 第二个返回项始终是精确 endpoint 到行号元组的映射。
    dict_lines = tuple_local_indexes[1]  # 当前 graph 的精确行号索引

    # memo 从空映射开始，生命周期严格绑定当前 session。
    dict_memo: dict[tuple[ScopedTarget, tuple[int, ...], tuple[str, ...]], TraceResult] = {}  # 当前 root 私有递归缓存

    # 返回对象不写入 graph、specialization cache 或模块级全局状态。
    return TraceSession(
        graph=graph,  # 当前 session 唯一冻结 graph

        # 模块索引同时核对实例路径与参数特化身份。
        dict_modules=dict_modules,  # 路径和特化到模块快照的映射

        # producer 索引保持 endpoint 精确查询合同。
        dict_drivers=dict_drivers,  # endpoint producer 索引

        # 组合事实索引同时保留精确目标和 whole-target 汇聚键。
        dict_local_facts=dict_local_facts,  # 本地组合事实索引

        # 时序事实索引供 D 与 enable synthetic endpoint 共享。
        dict_storage_facts=dict_storage_facts,  # Q endpoint 时序事实索引

        # 行号索引只登记精确端点，避免基础目标改变定位。
        dict_lines=dict_lines,  # 精确 endpoint 行号索引

        # memo 生命周期严格限制在当前 TraceSession 对象内。
        dict_memo=dict_memo,  # 当前调用私有递归缓存
    )

# operation occurrence ID 绑定定义根、路径、特化、位置、循环和调用栈。
def operation_occurrence_id(
    context: TraceContext,
    operation: dict[str, Any],
) -> str:
    """为一个冻结 operation 节点生成完整作用域身份。

    参数:
        context: 当前 operation 所属 tracing 上下文。
        operation: formatter typed operation 节点。

    返回:
        跨进程稳定且不会跨实例或 call site 合并的 occurrence ID。
    """

    # definition root 文本包含相对路径、模块名和完整定义范围。
    obj_identity = context.current.root  # 当前 operation 所属定义根身份

    # 定义范围消除同文件内重名 module 的身份碰撞。
    obj_definition_span = obj_identity.definition_span  # 当前根模块完整定义范围

    # root 片段保持 source path 与 module declaration 身份可读。
    str_root = (
        f"{obj_identity.relative_path}:{obj_identity.module_name}@"
        f"{obj_definition_span.line_start}:{obj_definition_span.column_start}-"
        f"{obj_definition_span.line_end}:{obj_definition_span.column_end}"
    )  # 当前 definition root 的规范文本

    # 实例路径从 root module 名开始逐层保留数组或 generate 索引。
    str_path = "/".join(context.current.instance_path)  # 当前 operation 完整实例路径

    # operation span 优先使用 typed tree 的权威一基范围。
    dict_span = _mapping(operation.get("span"))  # 当前 operation 源码范围

    # 缺失 span 时使用 occurrence_id 作为局部兼容位置。
    str_span = (  # 当前 operation 的稳定 source-local 位置
        f"{int(dict_span.get('line_start') or 1)}:{int(dict_span.get('column_start') or 1)}-"
        f"{int(dict_span.get('line_end') or dict_span.get('line_start') or 1)}:"
        f"{int(dict_span.get('column_end') or 1)}"
        if dict_span  # 权威范围存在时使用一基 span
        else str(operation.get("occurrence_id") or "unknown-span")  # 缺失 span 时的兼容位置
    )

    # 循环和函数栈使用 repr 风格保留有序 tuple 边界。
    str_loop = repr(tuple(context.loop_iteration_tuple))  # 当前完整循环迭代身份

    # 调用栈从外到内排列，区分相同函数体的不同调用 occurrence。
    str_stack = repr(tuple(context.function_call_stack))  # 当前完整函数调用栈身份

    # 六段身份按批准顺序连接，竖线便于证据解析和测试观察。
    return (
        f"{str_root}/{str_path}|{context.current.specialization.fingerprint}|"
        f"{str_span}|{str_loop}|{str_stack}"
    )

# module occurrence 查找同时核对路径与完整 specialization key。
def _module_for_target(graph: HierarchyGraph, target: ScopedTarget) -> SpecializedModule | None:
    """查找作用域目标所属的特化模块 occurrence。

    参数:
        graph: 当前 hierarchy graph。
        target: 需要定位所属模块的作用域端点。

    返回:
        路径与特化键均匹配的模块；不存在时为 None。
    """

    # 同一特化可出现在多个实例路径，必须同时比较两个维度。
    for tuple_path, specialized_module in graph.modules:

        # 完整路径和 key 一致才能读取该 occurrence 的本地事实。
        if tuple_path == target.instance_path and specialized_module.key == target.specialization:

            # 返回当前作用域唯一匹配模块。
            return specialized_module

    # 缺失 occurrence 表示 graph 与 endpoint 不属于同一冻结快照。
    return None

# context 查询优先使用调用级 occurrence 索引，兼容无 session 的内部调用。
def _module_for_context(context: TraceContext, target: ScopedTarget | None = None) -> SpecializedModule | None:
    """查找 context 内目标所属的特化模块 occurrence。

    参数:
        context: 携带 graph 与可选 session 的 tracing 上下文。
        target: 可选的替代作用域目标；缺省使用 context.current。

    返回:
        路径与特化键匹配的模块；不存在时为 None。
    """

    # 显式目标用于 parent actual 等跨 occurrence 查询。
    scoped_target = target or context.current  # 当前需要定位所属模块的端点

    # 批量入口提供 session 时使用常数时间 occurrence lookup。
    if context.session is not None:

        # 完整路径与特化键共同隔离同名模块 occurrence。
        return context.session.dict_modules.get((scoped_target.instance_path, scoped_target.specialization))

    # 兼容内部直接构造 TraceContext 的旧调用。
    return _module_for_target(context.graph, scoped_target)

# producer 查询复用 session 字典并保持 whole-target 回退合同。
def _drivers_for_context(context: TraceContext) -> tuple[ProducerRef, ...]:
    """查询当前 context endpoint 的全部 producer。

    参数:
        context: 携带当前作用域端点与可选 session 的 tracing 上下文。

    返回:
        精确 producer 或 bit/slice 对应 whole-target producer 元组。
    """

    # 无 session 的兼容路径继续调用既有公开查询函数。
    if context.session is None:

        # 旧入口行为保持不变。
        return drivers_for_endpoint(context.graph, context.current)

    # 精确 producer 优先于 whole-target 保守边界。
    tuple_exact = context.session.dict_drivers.get(context.current, ())  # 当前静态端点的精确 producer

    # 已知精确映射禁止被 whole-target unknown 覆盖。
    if tuple_exact:

        # 当前 endpoint 已完成 producer 定位。
        return tuple_exact

    # bit 或 slice 查询在精确缺失时继承 whole-target producer。
    str_base = base_target(context.current.target)  # 当前静态选择器的基础向量目标

    # whole target 自身没有第二个回退键。
    if str_base == context.current.target:

        # 当前 whole endpoint 保持未驱动语义。
        return ()

    # 回退键只替换 target，完整作用域身份保持一致。
    scoped_base = ScopedTarget(  # 当前静态选择器的 whole-target 查询键
        context.current.root,  # 当前 definition root 身份
        context.current.instance_path,  # 当前模块 occurrence 路径
        context.current.specialization,  # 当前模块参数特化键
        str_base,  # 当前位选或切片所属基础目标
    )

    # whole producer 只在精确映射缺失时生效。
    return context.session.dict_drivers.get(scoped_base, ())

# 本地事实查询优先使用 session 的精确与基础目标索引。
def _local_facts_for_context(
    context: TraceContext,
    module: SpecializedModule,
) -> tuple[dict[str, Any], ...]:
    """查询当前 endpoint 对应的本地组合事实。

    参数:
        context: 携带当前作用域端点与可选 session 的 tracing 上下文。
        module: 当前 occurrence 的特化模块快照。

    返回:
        按模块原始顺序排列且排除时序 D 事实的本地组合事实元组。
    """

    # 批量入口直接读取构建期已经完成的 target 索引。
    if context.session is not None:

        # 索引结果保留原始顺序，时序事实只能由 D/enable projection 消费。
        tuple_indexed_facts = context.session.dict_local_facts.get(context.current, ())  # 当前 endpoint 的全部索引事实

        # 普通 Q 端排除 process_kind=seq，避免穿透寄存器切点进入 D 锥。
        return tuple(  # 当前 endpoint 可作为组合 producer 的事实
            dict_fact  # 保留连续赋值和组合过程事实
            for dict_fact in tuple_indexed_facts  # 按 formatter 原始驱动顺序筛选
            if str(dict_fact.get("process_kind") or "") != "seq"  # 时序赋值仅归属 synthetic projection
        )

    # 兼容无 session 的内部调用，保留旧精确和基础目标匹配逻辑。
    tuple_facts = tuple(  # 当前 endpoint 对应的本地组合驱动事实
        dict_fact  # 保留与当前 endpoint 匹配的非时序事实
        for dict_fact in (_mapping(frozen_fact) for frozen_fact in module.comb_expressions)  # 每条事实仅解冻一次
        if str(dict_fact.get("process_kind") or "") != "seq"  # 无 session 路径同样保持寄存器 Q 切点
        and (  # 精确或基础目标匹配保持旧 whole-target 聚合合同
            str(dict_fact.get("target") or "").replace(" ", "") == context.current.target  # 精确静态端点
            or base_target(str(dict_fact.get("target") or "")) == context.current.target  # 基础向量端点
        )
    )

    # tuple 防止调用方修改兼容路径返回的事实目录。
    return tuple_facts
