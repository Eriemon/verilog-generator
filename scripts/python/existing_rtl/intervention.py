"""人工介入决策的归一化、适用范围判断和记忆条目生成。"""

# 未来注解避免运行时解析复杂类型，保持轻量导入。
from __future__ import annotations

# 标准库类型：仅用于描述 JSON-like 字典中的任意值。
from typing import Any

# 公开入口把用户回答转换成 workflow 可持久化的决策和记忆。
def resolve_intervention(intervention: dict[str, Any], answer: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    将一次人工介入回答整理为决策对象和决策记忆。

    :param intervention: workflow 生成的待确认问题，包含来源和问题文本。
    :param answer: 用户提交的回答对象，必须包含 decision、evidence、constraints 和 affected_subfunctions。
    :return: 二元组，第一项是 resolved decision，第二项是可写入 workflow state 的 memory。
    :raises ValueError: 当回答不是对象、缺少必要字段或 decision 为空时抛出。
    """

    # 先校验回答字段，避免写出不完整的人类决策记录。
    _validate_answer(answer)

    # affected_subfunctions 为空时默认影响全部子功能，保持旧流程的全局决策语义。
    list_affected = [str(item) for item in _as_list(answer.get("affected_subfunctions"))] or ["*"]  # 影响范围

    # 原始介入摘要单独命名，避免主决策对象变成难读的大字典。
    dict_source_intervention = {
        "primary_source": intervention.get("primary_source"),  # 原始介入来源
        "question": intervention.get("question"),  # 原始人工确认问题
    }  # 原始介入摘要对象

    # 决策对象保留原有 wire shape，供 workflow state 和报告继续读取。
    dict_decision: dict[str, Any] = {}  # 人工决策记录

    # 决策记录版本用于未来迁移兼容。
    dict_decision["version"] = 1  # 决策记录版本

    # 人工介入完成后固定进入 resolved 状态。
    dict_decision["status"] = "resolved"  # 人工介入已解决状态

    # 决策文本保持用户原文的字符串形式。
    dict_decision["decision"] = str(answer["decision"])  # 用户确认的决策文本

    # 证据字段归一化为列表，保留旧 wire shape。
    dict_decision["evidence"] = _as_list(answer.get("evidence", []))  # 用户提供的证据列表

    # 约束字段归一化为列表，供记忆摘要拼接。
    dict_decision["constraints"] = _as_list(answer.get("constraints", []))  # 用户确认的约束列表

    # 影响范围使用前面计算好的默认星号语义。
    dict_decision["affected_subfunctions"] = list_affected  # 决策影响的子功能范围

    # 原始介入摘要保留问题来源，便于审计人类决策链。
    dict_decision["source_intervention"] = dict_source_intervention  # 原始介入摘要

    # 记忆条目按影响范围展开，让后续阶段能按子功能匹配人类决策。
    dict_memory = {
        "version": 1,  # 记忆结构版本
        "entries": [  # workflow 可持久化的记忆条目列表
            {
                "subfunction": subfunction_name,  # 记忆适用的子功能
                "stage": "*",  # 决策适用于所有阶段
                "error_signature": "human_decision",  # 人工决策的固定错误签名
                "constraint": _constraint_text(dict_decision),  # 可读约束摘要
                "decision": dict_decision["decision"],  # 原始决策文本
            }
            for subfunction_name in list_affected  # 决策影响范围逐项展开
        ],  # 按影响范围展开后的记忆条目集合
    }

    # 返回决策和记忆，调用方负责写入状态文件或报告。
    return dict_decision, dict_memory

# workflow 在进入子功能阶段前用该函数判断已有人工决策是否命中。
def decision_applies(decision: dict[str, Any] | None, subfunction: str | None) -> bool:
    """
    判断人工决策是否适用于指定子功能。

    :param decision: 已解析的人工决策对象；为空表示没有可用决策。
    :param subfunction: 当前子功能名称；为空时表示调用方处于全局上下文。
    :return: 当决策为空返回 False；当影响范围为空、包含 ``*``、处于全局上下文或命中子功能名时返回 True。
    """

    # 没有决策时不能套用任何人工约束。
    if not decision:

        # 返回不适用，保持调用方继续等待或触发人工介入。
        return False

    # 决策影响范围统一转字符串列表，兼容 JSON 中的数字或其他标量。
    list_affected = [str(item) for item in decision.get("affected_subfunctions", []) or []]  # 当前决策影响范围

    # 空范围、全局星号、全局上下文或显式子功能命中都视为适用。
    return not list_affected or "*" in list_affected or subfunction is None or str(subfunction) in list_affected

# 回答校验集中在这里，保持 resolve_intervention 主流程直观。
def _validate_answer(answer: dict[str, Any]) -> None:
    """校验人工介入回答是否包含生成决策所需的字段。

    参数:
        answer: 用户提交的人工介入回答对象。

    返回:
        无。

    异常:
        ValueError: 当回答不是对象、缺少字段或 decision 为空时抛出。
    """

    # 人工回答必须是 JSON object，列表或字符串都无法承载必要字段。
    if not isinstance(answer, dict):

        # 错误前缀遵循脚本输出规范，主体保留原英文诊断。
        raise ValueError("> ERR: [Python] Human intervention answer must be a JSON object.")

    # 必需字段缺失时一次性报告全部字段，方便用户补齐回答。
    list_missing = [
        field_name  # 缺失的必需字段名
        for field_name in ("decision", "evidence", "constraints", "affected_subfunctions")  # 必需字段枚举
        if field_name not in answer  # 当前回答缺失该字段
    ]  # 缺失字段列表

    # 缺字段的回答不能进入决策记忆，否则后续阶段会误用不完整约束。
    if list_missing:

        # 缺失字段列表保留原拼接格式，便于用户直接补齐。
        raise ValueError(
            "> ERR: [Python] Human intervention answer is missing required fields: " + ", ".join(list_missing)
        )

    # decision 文本为空时无法形成可审计的人类决策。
    if not str(answer.get("decision", "")).strip():

        # 空决策的诊断也保留英文主体，便于既有调用方识别原因。
        raise ValueError("> ERR: [Python] Human intervention answer decision must not be empty.")

# 记忆约束摘要只拼接非空约束，避免空字符串污染报告。
def _constraint_text(decision: dict[str, Any]) -> str:
    """把人工决策和非空约束合成为可写入记忆的摘要。

    参数:
        decision: 已解析的人工决策对象。

    返回:
        面向 prompt 记忆的人工决策摘要文本。
    """

    # 约束文本使用分号拼接，保留用户提供约束的顺序。
    str_constraints = "; ".join(str(item) for item in decision.get("constraints", []) if str(item).strip())  # 约束摘要

    # 有约束时把决策和约束同时写入记忆，便于后续 prompt 引用。
    if str_constraints:

        # 返回带约束的人工决策摘要。
        return f"Human decision: {decision['decision']}. Constraints: {str_constraints}."

    # 没有约束时只记录决策本身。
    return f"Human decision: {decision['decision']}."

# JSON 字段兼容空值、列表和单项标量三种输入。
def _as_list(value: Any) -> list[Any]:
    """把 JSON 字段值归一化为列表形态。

    参数:
        value: 可能为空、列表或单项标量的 JSON 字段值。

    返回:
        归一化后的列表对象；原列表输入保持原对象返回。
    """

    # 空值代表没有条目。
    if value is None:

        # 返回空列表，调用方不需要再判断 None。
        return []

    # 列表输入保留原有顺序和元素对象。
    if isinstance(value, list):

        # 返回原列表，保持旧行为不做深拷贝。
        return value

    # 标量输入提升为单元素列表，兼容简单回答格式。
    return [value]
