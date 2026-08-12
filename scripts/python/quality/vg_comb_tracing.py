"""在冻结 hierarchy graph 上追踪跨实例组合操作 occurrence。"""

# 延迟注解求值保持 facade 与两个职责模块之间的类型引用稳定。
from __future__ import annotations

# cone、冻结 graph 和作用域目标组成公开 tracing 输入输出合同。
from .vg_comb_model import CombTargetCone, HierarchyGraph, ScopedTarget

# session 模块提供调用级索引、上下文以及兼容的事实解冻入口。
from .vg_trace_session import (
    TraceContext,
    TraceResult,
    TraceSession,
    _build_trace_session,
    _mapping,
    operation_occurrence_id,
)

# walk 模块提供递归遍历，同时由 facade 保留旧私有符号兼容性。
from .vg_trace_walk import (
    TraceAccumulator,
    _trace_scoped_target,
    merge_loop_presence,
    trace_reference,
)

# session 私有入口把 TraceResult 适配为带完整身份的 CombTargetCone。
def _trace_target_cone_with_session(
    trace_session: TraceSession,
    target: ScopedTarget,
) -> CombTargetCone:
    """在现有调用级 session 中追踪一个完整组合操作锥。

    参数:
        trace_session: 当前 definition root 的索引与 memo 容器。
        target: 需要核算组合预算的作用域静态目标。

    返回:
        带 root、path、specialization 和 loop 三态身份的 cone。
    """

    # 单个目标使用空调用栈、空循环身份和空 visiting 集合开始。
    context = TraceContext(trace_session.graph, target, session=trace_session)  # 当前目标的初始 tracing 上下文

    # 内部递归结果保留已知操作下界与局部 unknown。
    trace_result_result = _trace_scoped_target(context)  # 当前目标跨层 tracing 结果

    # 缺失 occurrence 时仍使用 specialization implementation 提供定位。
    obj_identity = target.specialization.implementation  # 当前目标模块实现身份

    # 精确行号索引保持旧入口只定位同一静态 target 的合同。
    tuple_lines = trace_session.dict_lines.get(target, ())  # 当前目标本地驱动的有效源码行

    # 无本地驱动的实例 output 使用模块定义起始行作为稳定回落。
    int_line = min(tuple_lines) if tuple_lines else obj_identity.definition_span.line_start  # 当前 cone 用户可见行号

    # 兼容 contains_for 由 model 根据 loop_presence 同步。
    return CombTargetCone(
        path=obj_identity.relative_path,
        module=obj_identity.module_name,
        target=target.target,
        line=int_line,
        operation_ids=trace_result_result.operation_ids,
        contains_for=trace_result_result.loop_presence == "present",
        inconclusive_reasons=trace_result_result.inconclusive_reasons,
        definition_root=trace_session.graph.root.identity,
        instance_path=target.instance_path,
        specialization_fingerprint=target.specialization.fingerprint,
        loop_presence=trace_result_result.loop_presence,
    )

# 公开单目标入口保持原签名并创建一个私有 session。
def trace_target_cone(
    graph: HierarchyGraph,
    target: ScopedTarget,
) -> CombTargetCone:
    """追踪一个 hierarchy target 的完整组合操作锥。

    参数:
        graph: 当前 definition root 的冻结 hierarchy graph。
        target: 需要核算组合预算的作用域静态目标。

    返回:
        带 root、path、specialization 和 loop 三态身份的 cone。
    """

    # 兼容入口的 session 仅服务当前目标，不跨调用持久化。
    trace_session_obj_trace_session = _build_trace_session(graph)  # 当前单目标调用的私有索引与 memo

    # 私有适配入口复用批量路径的全部 tracing 语义。
    return _trace_target_cone_with_session(trace_session_obj_trace_session, target)

# 批量入口让 facade 在同一 definition root 内复用一次索引和 memo。
def _trace_target_cones(
    graph: HierarchyGraph,
    targets: tuple[ScopedTarget, ...],
) -> tuple[CombTargetCone, ...]:
    """批量追踪同一 hierarchy root 的静态目标。

    参数:
        graph: 当前 definition root 的冻结 hierarchy graph。
        targets: 按调用方稳定顺序排列的作用域静态目标。

    返回:
        与输入顺序一致且字段等价于逐目标入口的 cone 元组。
    """

    # 一个 session 只覆盖当前 graph 和本次批量调用。
    trace_session_obj_trace_session = _build_trace_session(graph)  # 当前 root 批量追踪的调用级状态

    # 输入顺序直接决定输出顺序，不让 memo 命中改变报告排列。
    tuple_cones = tuple(  # 当前 root 的完整目标组合锥
        _trace_target_cone_with_session(trace_session_obj_trace_session, scoped_target)  # 复用同一索引追踪一个目标
        for scoped_target in targets  # 按 facade 已稳定排序的目标顺序遍历
    )

    # 返回值冻结后可由 facade 安全合并和最终排序。
    return tuple_cones
