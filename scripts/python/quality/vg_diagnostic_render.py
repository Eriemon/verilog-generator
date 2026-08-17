"""把 VG v3 诊断渲染为 Markdown 和终端可执行提示。"""

# future annotations 延后解析 Mapping 和 Path 的联合类型。
from __future__ import annotations

# pathlib 只用于在终端提示中展示可选报告路径。
from pathlib import Path

# Mapping 表达经过契约校验的 finding 字典。
from typing import Any, Mapping

# _text 统一把缺省字段转成可展示文本。
def _text(value: Any, fallback: str = "unknown") -> str:
    """返回安全的展示文本。

    参数:
        value: 待展示的字段值。
        fallback: 字段为空时使用的文本。
    返回:
        去除外围空白的文本。
    """

    # 空字符串不能帮助 Agent 定位或修改问题。
    str_value = str(value).strip() if value is not None else ""  # 规范化展示文本

    # 未知事实必须明确显示，不伪造坐标或示例。
    return str_value or fallback

# format_location 把四种 v3 location scope 转成单行定位摘要。
def format_location(location: Mapping[str, Any]) -> str:
    """渲染源码、文件、跨文件和项目级定位。

    参数:
        location: v3 finding 的结构化定位对象。
    返回:
        面向终端或 Markdown 的定位文本。
    """

    # scope 决定可展示的坐标边界。
    str_scope = _text(location.get("scope"), "project")  # 定位范围

    # file 可能为空，聚合级 finding 不伪造文件名。
    str_file = _text(location.get("file"), "unknown")  # 定位文件

    # file/source scope 在确认有行号时展示真实起止行。
    int_line_start = location.get("line_start")  # 起始行号

    # 结束行用于显示多行编辑范围。
    int_line_end = location.get("line_end")  # 结束行号

    # 精确源码行范围保留单行和多行两种表达。
    if str_scope in {"file", "source"} and int_line_start is not None:

        # 单行使用最短的 file:line 形式。
        if int_line_end in (None, int_line_start):

            # 精确定位可直接交给编辑器或 Agent。
            return f"{str_file}:{int_line_start}"

        # 多行范围保留完整边界。
        return f"{str_file}:{int_line_start}-{int_line_end}"

    # 文件级 finding 明确告诉 Agent 行号未知。
    if str_scope == "file":

        # file:unknown 避免把文件级事实误读为精确行。
        return f"file:{str_file}:unknown"

    # cross_file 需要提示关系范围而不是伪造一条主路径。
    if str_scope == "cross_file":

        # 相关位置数量帮助 Agent 判断关系复杂度。
        int_related_count = len(list(location.get("related_locations", [])))  # 相关位置数量

        # 没有可展示主文件时仍保留跨文件语义。
        return f"cross_file:{str_file}:unknown ({int_related_count} related location(s))"

    # project/run scope 只说明聚合事实，禁止补默认行号。
    return f"{str_scope}:unknown"

# _example_list 统一读取 v3 示例列表并兼容旧的单对象载荷。
def _example_list(value: Any) -> list[Mapping[str, Any]]:
    """返回可渲染的示例对象列表。

    参数:
        value: guidance.examples 原始值。
    返回:
        仅包含映射对象的示例列表。
    """

    # v3 公开合同使用列表；旧报告单对象仍可被人工渲染。
    if isinstance(value, Mapping):

        # 单对象兼容路径不改变写入层的严格校验。
        return [value]

    # 列表中的非对象不应阻断其余 finding 的展示。
    if isinstance(value, list):

        # 过滤不可渲染对象，避免把 repr 当作示例文本。
        return [item for item in value if isinstance(item, Mapping)]

    # 缺少示例时由调用方显示安全占位。
    return []

# _format_evidence 渲染节点类型、源码片段和结构事实。
def _format_evidence(evidence: Mapping[str, Any]) -> list[str]:
    """返回 Markdown evidence 行。

    参数:
        evidence: v3 evidence 对象。
    返回:
        evidence 的 Markdown 行列表。
    """

    # 证据字段各自保留事实来源和可复核细节。
    str_node_kind = _text(evidence.get("node_kind"))  # 证据节点类型

    # detail 说明规则实际观察到的结构事实。
    str_detail = _text(evidence.get("detail"))  # 规则观察到的事实

    # source_excerpt 只展示 emitter 提供的真实片段。
    str_excerpt = _text(evidence.get("source_excerpt"), "not provided")  # 可选源码片段

    # 结构化列表让终端和 Markdown 消费方共享相同信息。
    list_lines = [  # evidence Markdown 行集合
        f"- node_kind: `{str_node_kind}`",  # 证据节点类型行
        f"- detail: {str_detail}",  # 规则观察事实行
        f"- source_excerpt: `{str_excerpt}`",  # 可选源码片段行
    ]

    # 返回稳定的三字段 evidence 摘要。
    return list_lines

# _format_steps 渲染 Agent 可按顺序执行的修改步骤。
def _format_steps(guidance: Mapping[str, Any]) -> list[str]:
    """返回编号化修复步骤。

    参数:
        guidance: v3 guidance 对象。
    返回:
        Markdown 步骤行列表。
    """

    # steps 缺失时显示未知而非编造操作。
    list_steps = list(guidance.get("steps", []))  # Agent 修复步骤

    # 空列表仍生成一个可审计的占位步骤。
    if not list_steps:

        # 契约层通常已经拒绝此情况，渲染层保持 fail-safe。
        return ["1. 依据门禁规则和 evidence 重新确认修改范围。"]

    # 每一步独立编号，保留 emitter 的执行顺序。
    return [f"{index}. {_text(step)}" for index, step in enumerate(list_steps, start=1)]

# render_finding_markdown 生成一条完整的 Agent 可读诊断块。
def render_finding_markdown(finding: Mapping[str, Any], anchor: str | None = None) -> str:
    """渲染包含问题、证据、指导和示例的 Markdown finding。

    参数:
        finding: v3 finding 字典。
        anchor: 可选的稳定锚点文本。
    返回:
        一条以换行结尾的 Markdown 诊断块。
    """

    # 读取扁平 v3 字段，保留规则编号和状态上下文。
    str_rule_id = _text(finding.get("rule_id"), "VG000")  # 规则编号

    # problem 是 Agent 首先需要理解的具体违规事实。
    str_problem = _text(  # 规范化 Markdown 问题正文
        finding.get("problem"),  # finding 原始问题字段
        "VG finding did not provide a problem description.",  # 缺失问题的安全提示
    )  # 问题说明

    # status 说明当前 finding 是否仍需修复或复验。
    str_status = _text(finding.get("status"), "failed")  # 执行状态

    # severity 继承 catalog 的治理等级。
    str_severity = _text(finding.get("severity"), "WARNING")  # 治理等级

    # location/evidence/guidance 是 v3 的三个可执行嵌套对象。
    dict_location: Mapping[str, Any] | Any = finding.get("location", {})  # 结构化定位

    # evidence 保存规则实际观察到的结构事实。
    dict_evidence: Mapping[str, Any] | Any = finding.get("evidence", {})  # 结构化证据

    # guidance 保存 Agent 可执行的修改步骤和示例。
    dict_guidance: Mapping[str, Any] | Any = finding.get("guidance", {})  # 结构化修改指导

    # 非 Mapping 输入只在渲染层降级，写入前的诊断契约仍由 validator 负责。
    if not isinstance(dict_location, Mapping):

        # 保留未知定位，不推断一个源码行。
        dict_location = {}  # 安全的未知定位

    # 证据缺失时让 Agent 看到字段缺失事实。
    if not isinstance(dict_evidence, Mapping):

        # 不把 problem 复制成伪证据。
        dict_evidence = {}  # 安全的未知证据

    # guidance 缺失时仍保留修复边界说明。
    if not isinstance(dict_guidance, Mapping):

        # 空对象触发渲染层的安全占位。
        dict_guidance = {}  # 安全的未知指导

    # 示例是 guidance 的子列表，缺失时显示未知。
    list_examples: list[Mapping[str, Any]] = _example_list(dict_guidance.get("examples", []))  # bad/good 示例列表

    # 缺失示例由渲染层保留一个安全占位。
    if not list_examples:

        # 不猜测 bad/good 内容。
        list_examples = [{}]  # 安全的未知示例

    # 可选锚点仅用于稳定链接，不改变 finding 内容。
    str_anchor = f"<a id=\"{anchor}\"></a>\n" if anchor else ""  # Markdown 稳定锚点

    # list_lines 收集报告标题、状态、证据、修复和示例段落。
    list_lines = [  # list_lines 累积 v3 finding 的标题、状态、证据和修复段落
        f"{str_anchor}### {str_rule_id}: {str_problem}",  # 输出规则编号和具体问题标题
        f"- Status: `{str_status}`",  # 输出状态供 Agent 判断是否仍需修复
        f"- Severity: `{str_severity}`",  # 输出 catalog 继承的治理等级
        f"- Location: `{format_location(dict_location)}`",  # 输出真实源码或聚合定位范围
        "- Evidence:",  # 输出证据段落的起始标志
    ]

    # 追加 evidence 事实行。
    list_evidence_lines: list[str] = _format_evidence(dict_evidence)  # 结构化 evidence 行

    # 把 evidence 行追加到 finding 主体。
    list_lines.extend(list_evidence_lines)  # 追加结构化 evidence 行

    # metadata 保留文件角色等扩展事实，避免 Markdown 丢失 ambiguous 等确认语义。
    obj_metadata = finding.get("metadata")  # finding 的兼容扩展元数据

    # 只有存在扩展事实时才增加额外展示行，普通 finding 保持简洁。
    if isinstance(obj_metadata, Mapping) and obj_metadata:

        # repr 保留布尔、列表和 ambiguous 等机器事实的原文。
        list_lines.append(f"- Metadata: `{dict(obj_metadata)}`")  # 扩展事实摘要

    # 追加直接回答如何修改的 instruction。
    str_instruction = _text(  # 调用 helper 规范化修复指令
        dict_guidance.get("instruction"),  # guidance 原始修改动作
        "follow the VG rule contract and rerun the gate.",  # 缺失动作的安全提示
    )  # Agent 直接执行的修改指令

    # instruction 与 steps 分开显示，避免修复语义被压成一行。
    list_lines.append(f"- How to fix: {str_instruction}")  # 追加修改指令

    # 追加编号步骤标题。
    list_lines.append("- Steps:")  # 追加步骤标题

    # 追加按顺序执行的修复步骤。
    list_lines.extend(f"  {line}" for line in _format_steps(dict_guidance))  # 追加修复步骤

    # 风险和人工复核义务必须显式可见。
    str_risk = _text(dict_guidance.get("risk"))  # 修改风险

    # 人工复核标志必须在终端和 Markdown 中同步展示。
    bool_review = bool(dict_guidance.get("human_review_required", True))  # 人工复核义务

    # 风险行把人工复核义务与风险等级绑定展示。
    list_lines.append(f"- Risk: `{str_risk}`; human review required: `{bool_review}`")  # 追加风险与复核义务

    # 每个 bad/good 示例保留原文，不生成不存在的 RTL 片段。
    for int_index, dict_example in enumerate(list_examples, start=1):

        # 示例边界说明不能替代接口和时序审查。
        str_example_note = _text(  # 规范化示例适用边界
            dict_example.get("note"),  # emitter 提供的示例边界
            "example is illustrative; verify interface and timing constraints.",  # 缺失边界的安全提示
        )  # 示例适用边界

        # 多个示例通过稳定序号区分不同修改方向。
        list_lines.extend(
            [
                f"- Example {int_index} kind: `{_text(dict_example.get('kind'))}`",
                "- Bad example:",
                "```text",
                _text(dict_example.get("bad"), "not provided"),
                "```",
                "- Good example:",
                "```text",
                _text(dict_example.get("good"), "not provided"),
                "```",
                f"- Example note: {str_example_note}",
                "",
            ]
        )

    # 返回稳定换行结尾，便于多个 finding 拼接。
    return "\n".join(list_lines)

# format_terminal_finding 生成一行适合 Agent 日志扫描的终端提示。
def format_terminal_finding(
    finding: Mapping[str, Any],
    report_path: Path | None = None,
) -> str:
    """渲染一条带定位和修复动作的终端 finding。

    参数:
        finding: v3 finding 字典。
        report_path: 可选的完整报告路径。
    返回:
        以 ``[Python]`` 前缀开头的终端文本。
    """

    # 规则、状态和定位构成最短可扫描前缀。
    str_rule_id = _text(finding.get("rule_id"), "VG000")  # 终端规则编号

    # status 保留门禁执行结果，便于日志过滤。
    str_status = _text(finding.get("status"), "failed")  # 终端状态

    # location 决定终端行是否可以直接交给 Agent 编辑。
    dict_location: Mapping[str, Any] | Any = finding.get("location", {})  # 终端定位对象

    # 缺失定位不应阻断其他字段展示。
    if not isinstance(dict_location, Mapping):

        # 终端显示项目未知定位。
        dict_location = {}  # 终端未知定位

    # 终端第一行直接展示违规事实，让 Agent 无需先打开 Markdown。
    str_problem = _text(  # 把门禁问题映射到终端首行
        finding.get("problem"),  # 终端首段来源字段
        "VG finding did not provide a problem description.",  # 缺失问题的终端提示
    )  # 终端问题正文

    # guidance 提供终端可立即执行的修改动作。
    dict_guidance: Mapping[str, Any] | Any = finding.get("guidance", {})  # 终端指导对象

    # 非 Mapping guidance 退化为安全的重新检查提示。
    if not isinstance(dict_guidance, Mapping):

        # 不伪造具体修改动作。
        dict_guidance = {}  # 终端未知指导

    # 终端 instruction 缺失时只提示重新核对规则合同。
    str_instruction = _text(  # 调用 helper 规范化终端修复动作
        dict_guidance.get("instruction"),  # guidance 原始终端动作
        "follow the VG rule contract and rerun the gate.",  # 默认回归命令提示
    )  # 终端修复动作

    # 报告路径只作为可选追溯入口追加。
    str_report = f" report={report_path}" if report_path is not None else ""  # 终端报告追溯路径

    # 拼接单行终端文本，保持每个 finding 可独立扫描。
    return (
        f"[Python] {str_rule_id} {str_status} {format_location(dict_location)} - "
        f"{str_problem}; fix: {str_instruction}.{str_report}"
    )

# __all__ 固定导出定位、终端和 Markdown 三个入口。
__all__ = [  # __all__ 限制公开接口，避免内部 helper 被外部调用
    "format_location",  # 提供 scope 到文本的定位格式化
    "format_terminal_finding",  # 提供终端单行 finding 提示
    "render_finding_markdown",  # 提供 Markdown 完整诊断块
]
