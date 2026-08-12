"""从 formatter 控制树中提取 generate 实例模型。"""

# future annotations 避免运行期解析 formatter 内部类型。
from __future__ import annotations

# Any 保持本桥接模块不依赖 formatter 私有模型类型。
from typing import Any

# re 仅剥离实例声明前合法的 Verilog attribute。
import re

# 单条控制树叶子只在后端确认实例起点后进入实例 parser。
def instance_from_control_statement(formatter_engine: Any, statement_text: str) -> Any | None:
    """识别 generate 控制树叶子中的实例。

    参数:
        formatter_engine: formatter 后端。
        statement_text: 单条叶子语句。
    返回:
        实例模型或 ``None``。
    """

    # attribute 不是模块名的一部分，必须在实例起点识别前完整剥离。
    instance_candidate = re.sub(  # 去除连续前导 attribute 后的实例候选
        r"^(?:\s*\(\*.*?\*\)\s*)+",  # 仅匹配 statement 起始处的 attribute 块
        "",  # attribute 不进入后端实例名解析
        statement_text,  # 控制树已经隔离出的叶子 statement
        flags=re.DOTALL,  # 允许 attribute 自身跨越物理行
    )  # attribute 清理结果

    # 规范候选行供实例起点识别。
    normalized_lines = [  # 非空实例候选行
        formatter_engine._normalize_statement_line(source_line.strip())  # 当前候选的规范语句
        for source_line in instance_candidate.splitlines()  # attribute 后的物理行集合
        if source_line.strip()  # 空行不参与实例起点判断
    ]

    # attribute 后没有可见语句时不存在实例模型。
    if not normalized_lines:

        # 空候选交回普通 statement 路径。
        return None

    # 首行提供模块名或参数起点。
    first_line = normalized_lines[0]  # 实例候选首行

    # 次行补足跨行参数化实例。
    next_line = normalized_lines[1] if len(normalized_lines) > 1 else ""  # 实例候选次行

    # 非实例 statement 不得交给实例元数据 parser 猜测。
    if not formatter_engine._is_instance_start_line(first_line, next_line):

        # 普通 assign 或声明继续由既有控制树语义处理。
        return None

    # 单实例 parser 只提取当前声明，不递归扫描 module body。
    return formatter_engine._parse_instance_block("\n".join(normalized_lines))

# 深度优先遍历保持源码节点顺序和同级注释边界。
def instances_from_control_nodes(formatter_engine: Any, nodes: list[Any]) -> list[Any]:
    """按源码顺序收集 generate 控制树中的实例。

    参数:
        formatter_engine: formatter 后端。
        nodes: generate 控制节点。
    返回:
        实例模型列表。
    """

    # 实例集合按控制树的深度优先源码顺序追加。
    list_instances: list[Any] = []  # 已确认的 generate 实例

    # 同级连续纯注释暂存到下一条可判定节点。
    list_pending_comments: list[str] = []  # 当前兄弟层级的候选前导说明

    # 每个控制节点都先处理自身，再递归处理其结构化子节点。
    for node in nodes:

        # 节点种类决定注释暂存、实例识别或递归边界。
        str_node_kind = str(node.kind)  # formatter 控制节点类型

        # 纯注释只有与实例同级紧邻时才可能成为实例说明。
        if str_node_kind == "comment":

            # 空注释不进入实例前导说明集合。
            comment_text = str(node.text).strip()  # 当前纯注释正文

            # 非空说明继续等待下一条同级节点确认归属。
            if comment_text:

                # 保留连续纯注释的原始顺序。
                list_pending_comments.append(comment_text)

            # 注释节点不包含可单独识别的实例 statement。
            continue

        # 非空 statement 才可能承载 generate 内实例声明。
        if str_node_kind == "statement" and str(node.text).strip():

            # 有界 parser 只检查当前 statement，不扫描相邻节点。
            instance = instance_from_control_statement(  # 当前叶子的可选实例模型
                formatter_engine,  # 复用同一 formatter 语法后端
                str(node.text),  # 当前控制树叶子文本
            )

            # 识别成功后才把同级连续注释绑定到实例。
            if instance is not None:

                # 复制候选说明，避免后续清空暂存列表影响实例对象。
                instance.leading_comments = list(list_pending_comments)  # 当前实例的同级连续前导说明

                # 当前实例按源码遍历顺序进入统一集合。
                list_instances.append(instance)

        # 任意非注释节点都会切断前导说明候选关系。
        list_pending_comments.clear()

        # 普通 children 与 alternate 都保持各自的局部注释边界。
        for child_nodes in (node.children, node.alternate):

            # 递归结果延续当前节点之后的深度优先顺序。
            list_instances.extend(instances_from_control_nodes(formatter_engine, child_nodes))

        # case item 的 children 需要逐分支独立递归。
        for case_item in node.items:

            # 分支内实例按 case item 的源码顺序追加。
            list_instances.extend(instances_from_control_nodes(formatter_engine, case_item.children))

    # 返回当前控制树范围内的全部实例模型。
    return list_instances
