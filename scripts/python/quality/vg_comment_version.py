"""执行普通 Verilog 注释中的版本标记禁用门禁。"""

# future annotations 延迟解析注释事实中的联合类型。
from __future__ import annotations

# re 提供边界安全且不区分大小写的版本标记匹配。
import re

# Any 描述 formatter 注释事实的异构字典字段。
from typing import Any

# 规则模型统一 VG157 的结果状态和 finding 输出格式。
from .vg_rule_models import VgEvaluation, VgFinding, failed, passed

# VgFacts 提供共享 formatter AST 中的词法注释事实。
from .vg_semantic_facts import VgFacts

# 负向字母数字边界防止把版本片段从普通单词或标识符中截出。
VERSION_MARKER_PATTERN = re.compile(  # VG157 支持的中英文版本标记全集
    r"(?<![A-Za-z0-9_])(?:"
    r"v\d+(?:\.\d+)*"
    r"|ver(?:sion)?\s*\d+(?:\.\d+)*"
    r"|版本\s*\d+(?:\.\d+)*"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,  # 英文版本前缀按合同执行不区分大小写匹配
)

# evaluate_comment_version_gate 对固定文件头之外的真实注释执行 VG157。
def evaluate_comment_version_gate(facts: VgFacts) -> VgEvaluation:
    """检查普通注释，按每条注释生成至多一条 finding。

    参数:
        facts: formatter AST 聚合后的 Verilog 事实。
    返回:
        包含普通注释适用性和版本标记 finding 的 VG157 结果。
    """

    # finding 顺序与文件和词法注释顺序一致，保证报告确定性。
    list_findings: list[VgFinding] = []  # 已确认携带版本字样的普通注释

    # 没有普通注释的设计应标记为不适用，而不是伪造检查对象。
    bool_applicable = False  # 是否至少看到一条非固定文件头注释

    # 每个源文件报告都复用 formatter 词法扫描结果。
    for source_facts in facts.sources:

        # comment_facts 缺失时按空集合处理，兼容不含注释的旧报告。
        for dict_comment in source_facts.report.get("comment_facts", []) or []:

            # 只有 formatter 明确认出的固定双语文件头允许版本历史前缀。
            if bool(dict_comment.get("header_exempt")):

                # 固定文件头注释不进入普通注释适用性统计。
                continue

            # 到达此处即确认当前注释属于 VG157 检查范围。
            bool_applicable = True  # 当前源码至少含一条普通注释

            # 保留注释标记和正文，正则边界以完整词法文本为准。
            str_text = str(dict_comment.get("text") or "")  # 当前普通注释的原始文本

            # 同一注释内的多个版本标记合并为一条可读证据。
            list_markers: list[str] = []  # 当前普通注释中按出现顺序匹配的版本标记

            # finditer 保留词法文本中的自然出现顺序。
            for match in VERSION_MARKER_PATTERN.finditer(str_text):

                # 只保存匹配正文，不泄露注释之外的源代码内容。
                list_markers.append(match.group(0))

            # 没有匹配时当前注释满足 VG157。
            if not list_markers:

                # 继续检查同一文件中的下一条普通注释。
                continue

            # finding 绑定词法扫描确认的真实行号和注释类别。
            list_findings.append(
                VgFinding(
                    path=source_facts.relative_path,
                    line=_comment_line(dict_comment),
                    message="普通 RTL 注释不得携带版本字样；版本历史只能位于 formatter 固定文件头范围。",
                    evidence=(
                        f"markers={','.join(list_markers)}; "
                        f"kind={dict_comment.get('kind', '')}"
                    ),
                    severity="BLOCKER",
                )
            )

    # 任意普通注释违规都使 VG157 阻断失败。
    if list_findings:

        # 每条违规注释保留独立 finding，便于精确修改。
        return failed(*list_findings)

    # 无违规时根据是否存在普通注释返回适用性。
    return passed(
        applicable=bool_applicable,
        message="Ordinary comments contain no forbidden version markers.",
    )

# _comment_line 仅接受词法扫描器确认的一基正整数行号。
def _comment_line(dict_comment: dict[str, Any]) -> int | None:
    """只公开词法扫描器确认的真实注释行号。

    参数:
        dict_comment: formatter AST 中的一条注释事实。
    返回:
        已确认的一基正整数行号，未知时返回 None。
    """

    # line_start 是注释扫描器生成的唯一位置来源。
    int_line = dict_comment.get("line_start")  # 当前注释的一基起始行候选

    # bool 属于 int 子类但不能作为源码行号使用。
    if isinstance(int_line, int) and not isinstance(int_line, bool) and int_line > 0:

        # 返回扫描器确认的真实位置。
        return int_line

    # 缺失或非法位置保持未知，禁止回退到第一行。
    return None
