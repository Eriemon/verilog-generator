"""审计 Spec2RTL 计划中的子功能接口、证据和验证覆盖。"""

# 延迟注解解析，避免 dataclass 类型提示在导入期求值。
from __future__ import annotations

# 标准库依赖提供不可变审计问题对象和通用类型提示。
from dataclasses import dataclass
from typing import Any

# 计划审计先复用分解入口，确保输入规格已经过同一套规范化流程。
from scripts.python.workflow.planning import decompose_spec

@dataclass(frozen=True)
class AuditIssue:
    """描述计划审计发现的一条问题。

    属性:
        severity: 问题等级，当前使用 error 或 warning。
        message: 面向用户的审计说明文本。
        subfunction: 可选子功能名，用于定位问题来源。
    """

    # severity 决定 Markdown 报告中的 ERROR/WARNING 前缀。
    severity: str  # 报告严重程度

    # message 保留 CLI 对外展示的英文审计诊断。
    message: str  # 审计诊断文本

    # subfunction 用于把问题定位到具体拆解节点。
    subfunction: str | None = None  # 子功能定位名

    # 格式化入口把结构化问题转成 Markdown 列表行。
    def format(self) -> str:
        """把审计问题渲染成 Markdown 列表行。

        参数:
            无外部业务参数；方法使用当前审计问题实例的字段。

        返回:
            包含 severity、可选子功能名和诊断文本的 Markdown 列表行。
        """

        # 子功能名前缀只在问题能定位到具体子功能时出现。
        str_prefix = f"[{self.subfunction}] " if self.subfunction else ""  # 子功能定位前缀

        # severity 保持大写，方便报告读者快速扫描 error/warning。
        return f"- {self.severity.upper()}: {str_prefix}{self.message}"

# 公开审计入口返回结构化问题，供 CLI 和报告渲染复用。
def audit_plan(plan: dict[str, Any]) -> list[AuditIssue]:
    """检查计划中子功能接口、依赖、证据和验证用例是否完整。

    参数:
        plan: 原始规格或已经分解过的计划字典。

    返回:
        审计问题列表；空列表表示没有发现 error 或 warning。
    """

    # 审计始终先规范化计划，避免调用方传入半成品规格导致漏检。
    dict_normalized = decompose_spec(plan)  # 规范化计划

    # 问题列表按子功能顺序累积，保持报告稳定。
    list_issues: list[AuditIssue] = []  # 审计问题列表

    # 子功能列表是审计接口、依赖和证据覆盖的主轴。
    list_subfunctions = dict_normalized.get("subfunctions", [])  # 子功能候选列表

    # 没有子功能时无法继续做接口和证据覆盖审计。
    if not list_subfunctions:

        # 返回 error 而不是继续生成空报告，提醒上游规划阶段缺失。
        return [AuditIssue("error", "Plan contains no subfunctions.")]

    # 已知子功能名用于检查 dependencies 是否指向真实节点。
    set_known_names = {
        item.get("name")  # 子功能原始名称
        for item in list_subfunctions  # 规范化子功能候选
        if isinstance(item, dict)  # 只有 dict 子功能可提供 name 字段
    }  # 依赖可引用名称集合

    # 逐个子功能执行结构完整性和信息字典覆盖检查。
    for subfunction_index, subfunction_item in enumerate(list_subfunctions):

        # 非 dict 子功能无法读取接口和证据字段，按未知名称报告。
        if not isinstance(subfunction_item, dict):

            # 结构错误直接记录，避免后续字段读取异常。
            list_issues.append(AuditIssue("error", "Subfunction entry is not a dictionary.", None))

            # 当前异常项已经无法继续展开审计。
            continue

        # 子功能名缺失时保留旧版一基默认名，保证报告可定位。
        str_name = str(subfunction_item.get("name", f"subfunction_{subfunction_index + 1}"))  # 子功能报告名

        # 接口和依赖检查关注生成计划是否足够可实现。
        list_issues.extend(_audit_subfunction_shape(subfunction_item, subfunction_index, str_name, set_known_names))

        # 信息字典检查关注 behavior/constraints/test_intent 是否有证据和用例。
        list_issues.extend(_audit_subfunction_information(subfunction_item, str_name))

        # source_references 缺失会削弱规格追踪性，但不阻断生成。
        if not subfunction_item.get("source_references"):

            # 记录可追踪性 warning，供用户补充来源段落或表格。
            list_issues.append(AuditIssue("warning", "Missing source_references for traceability.", str_name))

    # 返回按子功能顺序整理的问题列表。
    return list_issues

# 公开报告入口把审计问题和覆盖矩阵渲染为 Markdown。
def render_audit(plan: dict[str, Any]) -> str:
    """渲染计划审计报告。

    参数:
        plan: 原始规格或已经分解过的计划字典。

    返回:
        以换行结尾的 Markdown 审计报告文本。
    """

    # 报告头部需要规范化后的 name、target 和 subfunctions 数量。
    dict_normalized = decompose_spec(plan)  # 报告渲染使用的完整计划

    # 审计问题复用公开入口，保持 CLI 和库调用结果一致。
    list_issues = audit_plan(dict_normalized)  # Findings 小节的问题条目

    # 覆盖矩阵描述每个信息字典条目是否绑定证据和验证用例。
    list_matrix = _coverage_matrix(dict_normalized)  # 信息条目覆盖表格行

    # 报告基础段落保持既有标题和字段顺序，避免破坏文档测试。
    list_lines = [  # 包含 Plan audit 标题和 Findings 小节
        f"# Plan audit: {dict_normalized['name']}",  # Plan audit 报告标题行
        "",  # 标题与摘要之间分隔行
        f"- Target: {dict_normalized['target']}",  # 目标类型摘要行
        f"- Subfunctions: {len(dict_normalized.get('subfunctions', []))}",  # 子功能数量摘要行
        f"- Issues: {len(list_issues)}",  # 审计问题数量摘要行
        "",  # Findings 小节前分隔行
        "## Findings",  # Findings 小节标题行
        "",  # Findings 条目前分隔行
    ]  # 报告头部和 findings 标题行

    # 有问题时逐条格式化；无问题时保留旧版 INFO 文案。
    if list_issues:

        # AuditIssue.format 统一输出 severity 和子功能前缀。
        list_lines.extend(issue.format() for issue in list_issues)

    # 无问题时保留显式确认行，避免空 findings 小节让用户误判审计未执行。
    else:

        # 无问题报告仍显式给出 INFO 行，方便 CLI 用户确认审计执行过。
        list_lines.append("- INFO: No audit issues found.")

    # 后半段报告固定展示覆盖矩阵和证据模型说明。
    list_lines.extend(_audit_report_tail(list_matrix))

    # 返回以换行结尾的 Markdown 文本，保持 CLI 输出友好。
    return "\n".join(list_lines) + "\n"

# 子功能结构审计器检查接口和依赖关系。
def _audit_subfunction_shape(
    dict_subfunction: dict[str, Any],
    subfunction_index: int,
    str_name: str,
    set_known_names: set[Any],
) -> list[AuditIssue]:
    """检查单个子功能的接口字段和依赖引用。

    参数:
        dict_subfunction: 当前正在审计的子功能字典。
        subfunction_index: 当前子功能在计划中的零基序号。
        str_name: 用于报告定位的子功能名称。
        set_known_names: 当前计划中可被依赖引用的子功能名称集合。

    返回:
        当前子功能接口或依赖关系产生的审计问题列表。
    """

    # 结构问题在当前子功能范围内独立累积。
    list_issues: list[AuditIssue] = []  # 子功能结构问题

    # 输入接口缺失会削弱 RTL 端口约束。
    if not dict_subfunction.get("inputs"):

        # 输入接口 warning 保留旧版英文诊断。
        list_issues.append(AuditIssue("warning", "Missing input interface description.", str_name))

    # 输出接口缺失会削弱验证观测点。
    if not dict_subfunction.get("outputs"):

        # 输出缺口提示下游缺少可观测验证端口。
        list_issues.append(AuditIssue("warning", "Missing output interface description.", str_name))

    # 非初始子功能通常应声明依赖，帮助生成流程确定组合顺序。
    if subfunction_index > 0 and not dict_subfunction.get("dependencies"):

        # 非初始子功能缺依赖只作为 warning，不阻断审计。
        list_issues.append(AuditIssue("warning", "No dependencies listed for non-initial subfunction.", str_name))

    # 逐条检查 dependencies 是否引用已知子功能名。
    for dependency_item in dict_subfunction.get("dependencies", []):

        # 字符串依赖若不在已知名称集合中，报告可定位 warning。
        if isinstance(dependency_item, str) and dependency_item not in set_known_names:

            # 依赖名保留 repr，方便区分空字符串和拼写错误。
            str_message = f"Dependency {dependency_item!r} is not another subfunction name."  # 依赖错误文本

            # 将当前依赖问题附加到子功能结构问题列表。
            list_issues.append(AuditIssue("warning", str_message, str_name))

    # 返回当前子功能结构检查发现的问题。
    return list_issues

# 子功能信息字典审计器检查三类语义条目的完整性。
def _audit_subfunction_information(dict_subfunction: dict[str, Any], str_name: str) -> list[AuditIssue]:
    """检查单个子功能的信息字典覆盖情况。

    参数:
        dict_subfunction: 当前正在审计的子功能字典。
        str_name: 用于报告定位的子功能名称。

    返回:
        behavior、constraints 和 test_intent 条目的审计问题列表。
    """

    # 信息字典问题按字段顺序累积，报告顺序稳定。
    list_issues: list[AuditIssue] = []  # 信息字典问题

    # 三类字段共同构成生成、约束和验证意图的可追踪来源。
    for field_name in ("behavior", "constraints", "test_intent"):

        # 缺失字段按空列表处理，便于统一报告 missing entries。
        list_items = dict_subfunction.get(field_name, [])  # 当前字段条目

        # 空字段表示缺少生成或验证所需的语义约束。
        if not list_items:

            # 保留旧版英文诊断文本，避免 CLI 输出语义漂移。
            list_issues.append(AuditIssue("error", f"Missing {field_name} entries.", str_name))

        # 每个信息条目都需要具备 text/evidence/verification_cases 结构。
        for info_item in list_items:

            # 单条信息字典的审计结果直接并入当前子功能问题列表。
            list_issues.extend(_audit_info_item(field_name, info_item, str_name))

    # 返回当前子功能的信息字典问题。
    return list_issues

# 信息字典条目审计器检查 text、evidence 和 verification_cases。
def _audit_info_item(field_name: str, info_item: Any, str_subfunction: str) -> list[AuditIssue]:
    """检查单条信息字典是否具备审计所需字段。

    参数:
        field_name: 当前条目所属的信息字段名。
        info_item: 待检查的原始信息条目。
        str_subfunction: 用于报告定位的子功能名称。

    返回:
        当前信息条目的 error 或 warning 列表。
    """

    # 单条信息字典的问题列表保持 error 在前、warning 在后。
    list_issues: list[AuditIssue] = []  # 单条信息问题

    # 信息条目必须是 dict，才能承载 id/text/evidence 等字段。
    if not isinstance(info_item, dict):

        # 非 dict 条目无法继续展开，直接返回 error。
        return [
            AuditIssue(
                "error",
                f"{field_name} entry is not an information-dictionary object.",
                str_subfunction,
            )
        ]

    # 条目 id 用于诊断文本，缺失时沿用旧版 <unknown>。
    str_item_id = str(info_item.get("id", "<unknown>"))  # 信息条目标识

    # text 为空意味着该信息条目不能用于 prompt 或证据追踪。
    if not str(info_item.get("text", "")).strip():

        # 空 text 是 error，因为下游无法理解该条目的语义。
        list_issues.append(AuditIssue("error", f"{field_name} entry {str_item_id} has empty text.", str_subfunction))

    # evidence 缺失会削弱来源追踪，但保留为 warning。
    if not info_item.get("evidence"):

        # evidence warning 提醒补充来源段落、表格或显式用户要求。
        list_issues.append(AuditIssue("warning", f"{field_name} entry {str_item_id} has no evidence.", str_subfunction))

    # verification_cases 缺失会削弱测试闭环，但不阻断计划生成。
    if not info_item.get("verification_cases"):

        # verification_cases warning 保留旧版字段名，方便用户定位。
        str_message = f"{field_name} entry {str_item_id} has no verification_cases."  # 验证用例缺失文本

        # 追加当前条目的验证用例缺失问题。
        list_issues.append(AuditIssue("warning", str_message, str_subfunction))

    # 返回单条信息字典的审计问题。
    return list_issues

# 覆盖矩阵构造器把信息字典条目渲染为 Markdown 表格行。
def _coverage_matrix(dict_plan: dict[str, Any]) -> list[str]:
    """构造信息字典证据与验证用例覆盖矩阵。

    参数:
        dict_plan: 已经规范化的计划字典。

    返回:
        Markdown 表格数据行；空计划返回占位行。
    """

    # 表格行按子功能和字段顺序累积。
    list_rows: list[str] = []  # 覆盖矩阵行

    # 子功能顺序来自计划本身，保持审计报告稳定。
    for subfunction_item in dict_plan.get("subfunctions", []):

        # 覆盖矩阵沿用旧逻辑假设规范化后子功能是 dict。
        str_name = _escape_cell(str(subfunction_item.get("name", "<unknown>")))  # 表格子功能名单元格

        # 三类字段都进入覆盖矩阵，便于查看证据和验证用例缺口。
        for field_name in ("behavior", "constraints", "test_intent"):

            # 字段中的每个条目生成一行覆盖状态。
            for info_item in subfunction_item.get(field_name, []):

                # 非 dict 条目无法读取 id/evidence/cases，用 invalid 行保留位置。
                if not isinstance(info_item, dict):

                    # invalid 行明确标记证据和验证用例均不可用。
                    list_rows.append(f"| {str_name} | {field_name} | <invalid> | no | no |")

                    # 当前 invalid 条目已完成矩阵行渲染。
                    continue

                # 条目 id 进入 Markdown 单元格前需要转义竖线。
                str_item_id = _escape_cell(str(info_item.get("id", "<unknown>")))  # 信息条目标识单元格

                # evidence 列只暴露 yes/no，避免报告中展开本地路径或长引用。
                str_evidence = "yes" if info_item.get("evidence") else "no"  # 证据覆盖状态

                # verification_cases 列同样只暴露 yes/no。
                str_cases = "yes" if info_item.get("verification_cases") else "no"  # 验证用例覆盖状态

                # 矩阵行保持既有列顺序，供文档测试和人工扫描复用。
                list_rows.append(f"| {str_name} | {field_name} | {str_item_id} | {str_evidence} | {str_cases} |")

    # 空计划仍输出占位行，避免 Markdown 表格只有表头。
    return list_rows or ["| <none> | <none> | <none> | no | no |"]

# 报告尾段集中维护覆盖矩阵和证据模型说明。
def _audit_report_tail(list_matrix: list[str]) -> list[str]:
    """生成审计报告的覆盖矩阵和证据模型尾段。

    参数:
        list_matrix: 覆盖矩阵的 Markdown 数据行。

    返回:
        可直接追加到审计报告正文的 Markdown 行列表。
    """

    # 必需字段说明较长，独立成变量以避免报告尾段行过宽。
    str_required_fields_line = (
        "- Each behavior, constraint, and test intent should include "  # 字段要求句前半段
        "`id`, `text`, `evidence`, and `verification_cases`."  # 字段要求句后半段
    )  # 信息字典必需字段说明

    # 尾段内容保持旧版顺序，便于已有文档和测试继续匹配。
    list_tail = [  # Evidence Coverage Matrix 之后的固定报告段落
        "",  # Findings 与矩阵之间分隔行
        "## Evidence Coverage Matrix",  # 覆盖矩阵小节标题行
        "",  # 覆盖矩阵表格前分隔行
        "| Subfunction | Field | Item | Evidence | Verification cases |",  # 覆盖矩阵表头行
        "| --- | --- | --- | --- | --- |",  # 覆盖矩阵 Markdown 分隔行
        *list_matrix,  # 信息条目覆盖状态行
        "",  # Required Evidence Model 前分隔行
        "## Required Evidence Model",  # 证据模型小节标题行
        "",  # 证据模型说明列表前分隔行
        str_required_fields_line,  # 必需字段说明
        "- Evidence should point to the originating spec paragraph, table, equation, or explicit user requirement.",  # 证据来源说明
    ]  # 覆盖矩阵标题与证据模型说明行

    # 返回报告尾段，调用方会追加到主报告行缓冲。
    return list_tail

# Markdown 表格单元格转义器避免竖线破坏列结构。
def _escape_cell(text: str) -> str:
    """转义 Markdown 表格单元格中的竖线。

    参数:
        text: 原始单元格文本。

    返回:
        可安全写入 Markdown 表格的单元格文本。
    """

    # 只转义表格分隔符，保留其它字符的可读性。
    return text.replace("|", "\\|")
