"""定义 RTL PG 门禁内部结果模型。"""

# future annotations 避免运行期求值递归类型。
from __future__ import annotations

# dataclass 工具负责不可变结果模型和默认空证据元组。
from dataclasses import dataclass, field

# Any 表达 JSON 字典中的异构标量。
from typing import Any

# PgFinding 固定单条门禁证据的公共字段。
@dataclass(frozen=True)
class PgFinding:
    """保存单条 PG 违规或不确定证据。"""

    # path 记录相对扫描根的 RTL 文件位置。
    path: str  # 证据所在的 RTL 相对路径

    # line 记录证据在 RTL 文件中的一基行号。
    line: int  # 证据对应的一基源码行号

    # message 提供面向审查者的中文规则解释。
    message: str  # 当前发现的可读诊断文本

    # evidence 保留触发规则的最小源码或结构事实。
    evidence: str = ""  # 当前发现的最小可追溯证据

    # to_dict 把不可变证据模型转换为报告字典。
    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的证据字典。

        参数:
            self: 当前不可变 PG 证据对象。
        返回:
            字段稳定、可直接写入 JSON 报告的字典。
        """

        # 报告字段保持与门禁结果合同一致。
        return {
            "path": self.path,  # 定位触发门禁的 RTL 文件
            "line": self.line,  # 定位文件内的具体触发行
            "message": self.message,  # 面向审查者的规则诊断
            "evidence": self.evidence,  # 触发规则的最小结构证据
        }

# PgEvaluation 固定单条 active 门禁的内部执行结论。
@dataclass(frozen=True)
class PgEvaluation:
    """保存一条激活 PG 门禁的执行结论。"""

    # status 只能取 PG 引擎声明的固定状态集合。
    status: str  # 当前门禁执行后的固定状态

    # applicable 表明当前 RTL 是否出现该规则的适用结构。
    applicable: bool  # 当前门禁是否存在可分析对象

    # findings 保留所有确定违规或不确定证据。
    findings: tuple[PgFinding, ...] = field(default_factory=tuple)  # 当前门禁的有序证据集合

    # message 解释无具体 finding 时的状态原因。
    message: str = ""  # 当前状态的补充说明

# passed 统一创建规则通过结论。
def passed(*, applicable: bool = False, message: str = "") -> PgEvaluation:
    """构造通过结论。

    参数:
        applicable: 当前 RTL 是否出现规则适用结构。
        message: 可选的通过状态补充说明。
    返回:
        状态固定为 passed 的门禁结论。
    """

    # 通过结论不携带违规证据。
    return PgEvaluation("passed", applicable, message=message)

# failed 统一创建带确定证据的失败结论。
def failed(*findings: PgFinding) -> PgEvaluation:
    """构造包含确定违规证据的失败结论。

    参数:
        findings: 当前规则确认命中的一个或多个证据。
    返回:
        状态固定为 failed 的门禁结论。
    """

    # 失败结论始终标记规则适用并保留证据顺序。
    return PgEvaluation("failed", True, tuple(findings))

# inconclusive 统一创建证据不足的 fail-closed 结论。
def inconclusive(message: str, *findings: PgFinding) -> PgEvaluation:
    """构造分析信息不足时的 fail-closed 结论。

    参数:
        message: 说明无法得出确定结论的原因。
        findings: 可选的不确定证据位置。
    返回:
        状态固定为 inconclusive 的门禁结论。
    """

    # 不确定结论保持适用状态，交由 strict 交付策略阻断。
    return PgEvaluation("inconclusive", True, tuple(findings), message)
