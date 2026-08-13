"""从 workflow trace 事件构造 artifact 依赖图。"""

# 延迟注解解析，降低运行期导入成本。
from __future__ import annotations

# 标准库依赖提供路径类型和松散 JSON 字段类型。
from pathlib import Path
from typing import Any

# trace 模块负责读取 JSONL 事件，本模块只负责图结构投影。
from .trace import read_trace

# 事件中这些字段会被解释为 artifact 路径并连到对应事件。
ARTIFACT_EVENT_KEYS = (  # 可直接映射为 artifact 节点的 trace 字段名
    "output",  # 生成输出路径字段
    "path",  # 通用 artifact 路径字段
    "report",  # 文本报告路径字段
    "report_json",  # JSON 报告路径字段
    "repair_plan",  # 修复计划路径字段
    "context_manifest",  # 上下文清单路径字段
    "context_dir",  # 上下文目录路径字段
    "memory",  # 记忆快照路径字段
)

# 公开入口把 trace 和计划依赖合成为可视化/诊断用图。
def build_artifact_graph(trace_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """构建 workflow artifact graph。

    参数:
        trace_path: trace JSONL 文件路径。
        plan: 规范化计划字典，用于补充子功能依赖节点。

    返回:
        包含 nodes、edges 和 suspect_artifacts 的图字典。
    """

    # trace 事件顺序决定 event 节点顺序和 sequence 边。
    list_events = read_trace(trace_path)  # 按文件顺序读取到的 workflow trace 事件

    # nodes 使用 id 去重，保持 artifact 和子功能节点唯一。
    dict_nodes: dict[str, dict[str, Any]] = {}  # 图节点索引

    # edges 保留事件遍历顺序，便于回看生成链路。
    list_edges: list[dict[str, Any]] = []  # 图边列表

    # attempt 维度记录上一个事件，用于串起同一次尝试。
    dict_previous_by_attempt: dict[str, str] = {}  # attempt 前序事件

    # subfunction 维度记录上一个事件，用于串起同一子功能演进。
    dict_previous_by_subfunction: dict[str, str] = {}  # 子功能前序事件

    # 先从 trace 事件生成事件节点、artifact 节点和诊断节点。
    for event_index, event in enumerate(list_events):

        # event_id 保留旧格式，包含序号和事件名。
        str_event_id = f"event:{event_index}:{event.get('event', 'unknown')}"  # 事件节点 id

        # subfunction 缺失时回退 stage，再回退 global。
        str_subfunction = str(event.get("subfunction") or event.get("stage") or "global")  # 事件归属子功能分组

        # attempt_id 缺失时使用旧版 attempt-unknown 占位。
        str_attempt_id = str(event.get("attempt_id") or "attempt-unknown")  # 尝试维度

        # 事件节点字段保持原始 trace 信息，供报告定位。
        dict_event_node: dict[str, Any] = {}  # 连接 artifact、错误来源和漂移检查点的事件节点

        # id 是后续边引用当前事件节点的稳定键。
        dict_event_node["id"] = str_event_id  # 图边引用的事件节点 id

        # type 区分事件节点和 artifact/checkpoint/subfunction 节点。
        dict_event_node["type"] = "event"  # 图节点类型

        # event 字段保留 trace 原始事件名。
        dict_event_node["event"] = event.get("event")  # 报告中展示的 workflow trace 事件名

        # attempt_id 保留原值，便于报告聚合同一次生成尝试。
        dict_event_node["attempt_id"] = event.get("attempt_id")  # attempt_sequence 分组使用的原始尝试 id

        # stage 优先使用 stage，缺失时沿用旧逻辑回退 readiness。
        dict_event_node["stage"] = event.get("stage") or event.get("readiness")  # 事件阶段

        # subfunction 使用前面归一化后的分组值。
        dict_event_node["subfunction"] = str_subfunction  # 事件归属子功能

        # ok 保留 trace 中的成功状态。
        dict_event_node["ok"] = event.get("ok")  # 事件成功状态

        # error_sources 是 suspect_artifacts 推断错误事件的关键字段。
        dict_event_node["error_sources"] = event.get("error_sources", [])  # 错误来源列表

        # 将事件节点写入节点索引，后续边会引用该 id。
        dict_nodes[str_event_id] = dict_event_node  # 当前事件节点

        # 同一 attempt 的相邻事件之间建立顺序边。
        _append_sequence_edge(list_edges, dict_previous_by_attempt, str_attempt_id, str_event_id, "attempt_sequence")

        # 同一子功能的相邻事件之间建立顺序边。
        _append_sequence_edge(
            list_edges,
            dict_previous_by_subfunction,
            str_subfunction,
            str_event_id,
            "subfunction_sequence",
        )

        # 固定 artifact 字段先投影，保留 trace 字段名作为边类型。
        _attach_artifact_edges(dict_nodes, list_edges, str_event_id, event)

        # written_files 记录显式写出文件，独立使用 written_file 边类型。
        _attach_written_file_edges(dict_nodes, list_edges, str_event_id, event)

        # error_sources 连接诊断来源，供 suspect_artifacts 查找错误事件。
        _attach_error_source_edges(dict_nodes, list_edges, str_event_id, event)

        # checkpoint drift 连接语义漂移键，帮助报告定位偏移来源。
        _attach_checkpoint_drift_edges(dict_nodes, list_edges, str_event_id, event)

    # 计划中的子功能依赖补成 subfunction 节点和 dependency 边。
    _attach_plan_dependency_edges(dict_nodes, list_edges, plan)

    # 节点列表从字典值导出，保持插入顺序。
    list_graph_nodes = list(dict_nodes.values())  # 输出图节点

    # suspect_artifacts 只依赖图自身，便于调用方单独复用。
    list_suspect_artifacts = suspect_artifacts_from_graph({"nodes": list_graph_nodes, "edges": list_edges})  # 错误事件直接产出的 artifact 路径

    # 返回字段形状保持既有 artifact graph 版本 1。
    return {
        "version": 1,
        "name": plan.get("name"),
        "target": plan.get("target"),
        "nodes": list_graph_nodes,
        "edges": list_edges,
        "suspect_artifacts": list_suspect_artifacts,
    }

# 可疑 artifact 推断入口从带 error_sources 的事件出边中收集 artifact。
def suspect_artifacts_from_graph(graph: dict[str, Any] | None) -> list[str]:
    """提取由错误事件直接产出的 artifact 路径。

    参数:
        graph: build_artifact_graph 返回的图，或兼容字段形状的字典。

    返回:
        artifact 路径列表，顺序与边遍历顺序一致且去重。
    """

    # 空图没有可疑 artifact。
    if not graph:

        # 返回空列表保持调用方宽容。
        return []

    # 错误事件节点由 type=event 且 error_sources 非空识别。
    set_error_event_ids = {
        node.get("id")  # 错误事件 id
        for node in graph.get("nodes", [])  # 图节点候选
        if isinstance(node, dict) and node.get("type") == "event" and node.get("error_sources")  # 错误事件节点
    }  # 带错误来源的事件节点

    # artifacts 保持第一次出现顺序，避免报告重复噪声。
    list_artifacts: list[str] = []  # 可疑 artifact 路径

    # 只看错误事件直接指向的 artifact 边。
    for edge in graph.get("edges", []) or []:

        # 非 dict 边或非错误事件来源边都跳过。
        if not isinstance(edge, dict) or edge.get("from") not in set_error_event_ids:

            # 当前边与可疑 artifact 无关。
            continue

        # artifact 节点 id 使用 artifact:<path> 形式。
        str_target = str(edge.get("to", ""))  # 边目标节点 id

        # 只提取 artifact 节点，忽略 checkpoint/error_source 等诊断节点。
        if not str_target.startswith("artifact:"):

            # 非 artifact 节点不进入可疑列表。
            continue

        # 去掉 artifact 前缀后得到原始路径文本。
        str_path = str_target.removeprefix("artifact:")  # 去掉节点前缀后的 artifact 路径

        # 路径非空且未出现过时加入列表。
        if str_path and str_path not in list_artifacts:

            # 记录第一次出现的可疑 artifact。
            list_artifacts.append(str_path)

    # 返回去重后的可疑 artifact 路径。
    return list_artifacts

# 顺序边追加器维护 attempt/subfunction 两类前序索引。
def _append_sequence_edge(
    list_edges: list[dict[str, Any]],
    dict_previous: dict[str, str],
    str_group_key: str,
    str_event_id: str,
    str_edge_kind: str,
) -> None:
    """为同一分组内相邻事件追加顺序边。

    参数:
        list_edges: 图边列表，函数会在存在前序事件时追加新边。
        dict_previous: 分组键到上一事件节点 id 的索引。
        str_group_key: attempt 或 subfunction 分组键。
        str_event_id: 当前事件节点 id。
        str_edge_kind: 写入边 kind 字段的顺序关系名称。

    返回:
        无业务返回值；函数直接更新边列表和前序事件索引。
    """

    # 当前分组已有前序事件时才生成 sequence 边。
    if str_group_key in dict_previous:

        # 边字段保持 from/to/kind 三元结构。
        list_edges.append({"from": dict_previous[str_group_key], "to": str_event_id, "kind": str_edge_kind})

    # 更新当前分组的最新事件。
    dict_previous[str_group_key] = str_event_id  # 当前分组下一条 sequence 边的来源事件

# artifact 字段投影器处理 event 中固定路径字段。
def _attach_artifact_edges(
    dict_nodes: dict[str, dict[str, Any]],
    list_edges: list[dict[str, Any]],
    str_event_id: str,
    event: dict[str, Any],
) -> None:
    """把 trace 事件中的固定 artifact 字段连接到图中。

    参数:
        dict_nodes: 图节点索引，函数会补充 artifact 节点。
        list_edges: 图边列表，函数会追加事件到 artifact 的边。
        str_event_id: 当前事件节点 id。
        event: 当前 trace 事件字典。

    返回:
        无业务返回值；函数直接更新节点索引和边列表。
    """

    # 固定字段里的真值会被视作 artifact 路径。
    for field_name in ARTIFACT_EVENT_KEYS:

        # 缺失或空值字段不生成 artifact 节点。
        artifact_value = event.get(field_name)  # 当前 trace 字段声明的 artifact 路径值

        # 只有真值才进入图，保持旧实现过滤逻辑。
        if artifact_value:

            # 创建 artifact 节点并连接当前事件。
            _connect_artifact(dict_nodes, list_edges, str_event_id, artifact_value, field_name)

# written_files 投影器处理事件显式写出的文件列表。
def _attach_written_file_edges(
    dict_nodes: dict[str, dict[str, Any]],
    list_edges: list[dict[str, Any]],
    str_event_id: str,
    event: dict[str, Any],
) -> None:
    """把 trace 事件显式写出的文件列表连接到图中。

    参数:
        dict_nodes: 图节点索引，函数会补充写出文件对应的 artifact 节点。
        list_edges: 图边列表，函数会追加 written_file 边。
        str_event_id: 当前事件节点 id。
        event: 当前 trace 事件字典。

    返回:
        无业务返回值；函数直接更新节点索引和边列表。
    """

    # written_files 缺失时按空列表处理。
    for artifact_value in event.get("written_files", []) or []:

        # 每个写出文件都作为 artifact 边记录。
        _connect_artifact(dict_nodes, list_edges, str_event_id, artifact_value, "written_file")

# error_sources 投影器把错误来源连到事件节点。
def _attach_error_source_edges(
    dict_nodes: dict[str, dict[str, Any]],
    list_edges: list[dict[str, Any]],
    str_event_id: str,
    event: dict[str, Any],
) -> None:
    """把 trace 事件的错误来源诊断节点连接到图中。

    参数:
        dict_nodes: 图节点索引，函数会补充 error_source 节点。
        list_edges: 图边列表，函数会追加 has_error_source 边。
        str_event_id: 当前事件节点 id。
        event: 当前 trace 事件字典。

    返回:
        无业务返回值；函数直接更新节点索引和边列表。
    """

    # 错误来源为空时自然不生成诊断节点，保持 trace 投影宽容。
    for source in event.get("error_sources", []) or []:

        # source_id 保持旧格式，方便和历史报告对齐。
        str_source_id = f"error_source:{source}"  # 错误来源节点 id

        # 错误来源节点保留原始来源名称，便于报告回连诊断文本。
        dict_nodes.setdefault(str_source_id, {"id": str_source_id, "type": "error_source", "name": source})

        # 当前事件与错误来源之间建立诊断边。
        list_edges.append({"from": str_event_id, "to": str_source_id, "kind": "has_error_source"})

# checkpoint drift 投影器提取 semantic_execution 指标中的漂移键。
def _attach_checkpoint_drift_edges(
    dict_nodes: dict[str, dict[str, Any]],
    list_edges: list[dict[str, Any]],
    str_event_id: str,
    event: dict[str, Any],
) -> None:
    """把 checkpoint drift 指标投影成诊断节点和边。

    参数:
        dict_nodes: 图节点索引，函数会补充 checkpoint 节点。
        list_edges: 图边列表，函数会追加 checkpoint_drift 边。
        str_event_id: 当前事件节点 id。
        event: 当前 trace 事件字典。

    返回:
        无业务返回值；函数直接更新节点索引和边列表。
    """

    # metrics 可能不是 dict，先做类型检查。
    raw_metrics = event.get("metrics")  # 可能包含 semantic_execution 的原始指标对象

    # 只有 dict metrics 才能读取 semantic_execution。
    dict_semantic = (raw_metrics or {}).get("semantic_execution", {}) if isinstance(raw_metrics, dict) else {}  # 语义执行指标

    # checkpoint_drift 中每个 drift_key 都生成 checkpoint 节点。
    for drift_item in dict_semantic.get("checkpoint_drift", []) or []:

        # 非 dict 漂移项无法读取 drift_keys，直接跳过。
        if not isinstance(drift_item, dict):

            # 保持对异常 trace 的宽容。
            continue

        # 一个 drift_item 可包含多个漂移键。
        for drift_key in drift_item.get("drift_keys", []) or []:

            # checkpoint id 保持旧格式。
            str_checkpoint_id = f"checkpoint:{drift_key}"  # 漂移检查点 id

            # checkpoint 节点记录名称，供图展示。
            dict_nodes.setdefault(
                str_checkpoint_id,
                {"id": str_checkpoint_id, "type": "checkpoint", "name": str(drift_key)},
            )

            # 当前事件与漂移检查点建立诊断边。
            list_edges.append({"from": str_event_id, "to": str_checkpoint_id, "kind": "checkpoint_drift"})

# 计划依赖投影器把 subfunctions.dependencies 合并进图。
def _attach_plan_dependency_edges(
    dict_nodes: dict[str, dict[str, Any]],
    list_edges: list[dict[str, Any]],
    plan: dict[str, Any],
) -> None:
    """把计划中的子功能依赖关系合并到 artifact graph。

    参数:
        dict_nodes: 图节点索引，函数会补充 subfunction 节点。
        list_edges: 图边列表，函数会追加 dependency 边。
        plan: 已规范化或兼容字段形状的计划字典。

    返回:
        无业务返回值；函数直接更新节点索引和边列表。
    """

    # 计划子功能可能来自用户输入，逐项检查类型。
    for subfunction in plan.get("subfunctions", []) or []:

        # 非 dict 子功能无法提供 name/dependencies。
        if not isinstance(subfunction, dict):

            # 跳过异常项，保持图构建宽容。
            continue

        # 子功能节点 id 使用旧格式 subfunction:<name>。
        str_node_id = f"subfunction:{subfunction.get('name')}"  # 子功能节点 id

        # 子功能节点只保存名称和类型。
        dict_nodes.setdefault(str_node_id, {"id": str_node_id, "type": "subfunction", "name": subfunction.get("name")})

        # dependencies 中每个条目生成 dependency 边。
        for dependency in subfunction.get("dependencies", []) or []:

            # 依赖节点即使计划中没有定义，也按旧行为补出来。
            str_dependency_id = f"subfunction:{dependency}"  # 依赖子功能节点 id

            # 补齐依赖来源节点。
            dict_nodes.setdefault(
                str_dependency_id,
                {"id": str_dependency_id, "type": "subfunction", "name": dependency},
            )

            # dependency 边从依赖节点指向当前子功能节点。
            list_edges.append({"from": str_dependency_id, "to": str_node_id, "kind": "dependency"})

# artifact 连接器集中维护 artifact 节点字段和边类型。
def _connect_artifact(
    dict_nodes: dict[str, dict[str, Any]],
    list_edges: list[dict[str, Any]],
    str_event_id: str,
    value: Any,
    str_edge_kind: str,
) -> None:
    """创建 artifact 节点并连接当前事件。

    参数:
        dict_nodes: 图节点索引，函数会按 artifact id 去重补点。
        list_edges: 图边列表，函数会追加事件到 artifact 的边。
        str_event_id: 当前事件节点 id。
        value: trace 中记录的 artifact 路径或路径样文本。
        str_edge_kind: 写入边 kind 字段的 artifact 来源类型。

    返回:
        无业务返回值；函数直接更新节点索引和边列表。
    """

    # artifact_id 标准化 Windows 路径分隔符，保持跨平台展示稳定。
    str_artifact_id = _artifact_id(value)  # 当前路径值对应的 artifact 节点 id

    # artifact 节点保留原始路径字符串。
    dict_nodes.setdefault(str_artifact_id, {"id": str_artifact_id, "type": "artifact", "path": str(value)})

    # 事件到 artifact 的边 kind 使用来源字段名。
    list_edges.append({"from": str_event_id, "to": str_artifact_id, "kind": str_edge_kind})

# artifact id 生成器把路径文本转换成图节点 id。
def _artifact_id(value: Any) -> str:
    """生成跨平台稳定的 artifact 图节点 id。

    参数:
        value: trace 中记录的 artifact 路径或路径样文本。

    返回:
        以 artifact: 开头且路径分隔符归一化后的节点 id。
    """

    # Windows 反斜杠统一成正斜杠，避免同一 artifact 出现两个 id。
    str_text = str(value).replace("\\", "/")  # 归一化路径文本

    # artifact: 前缀用于和 event/subfunction/checkpoint 节点区分。
    return f"artifact:{str_text}"
