"""定义 RTL VG 门禁内部结果模型。"""

# future annotations 避免运行期求值递归类型。
from __future__ import annotations

# dataclass 工具负责不可变结果模型和默认空证据元组。
from dataclasses import dataclass, field

# Any 表达 JSON 字典中的异构标量。
from typing import Any

# v3 诊断模块负责公开字段校验和旧 finding 适配。
from .vg_diagnostics import (
    build_legacy_diagnostic,
    diagnostic_from_mapping,
    diagnostic_path_line,
)

# VgFinding 固定单条门禁证据的公共字段。
@dataclass(frozen=True)
class VgFinding:
    """保存单条 VG 违规或不确定证据。"""

    # path 记录相对扫描根的 RTL 文件位置。
    path: str | None  # 证据所在的 RTL 相对路径；聚合规则可为空

    # line 记录证据在 RTL 文件中的一基行号。
    line: int | None  # 证据对应的一基源码行号；未知时保持为空

    # message 提供面向审查者的中文规则解释。
    message: str  # 当前发现的可读诊断文本

    # evidence 保留触发规则的最小源码或结构事实。
    evidence: str = ""  # 当前发现的最小可追溯证据

    # severity 允许单条规则同时产生 BLOCKER 与 WARNING 发现。
    severity: str | None = None  # finding 级治理等级；缺省时继承 catalog

    # metadata 承载文件级门禁声明的固定扩展字段。
    metadata: tuple[tuple[str, Any], ...] = ()  # 不改变旧 finding 的默认 JSON shape

    # diagnostic 保存 v3 flat finding，旧调用方可继续只传 path/line/message。
    diagnostic: dict[str, Any] | None = field(default=None, compare=True)  # 可执行诊断载荷

    # __post_init__ 阻止扩展字段覆盖既有公共合同。
    def __post_init__(self) -> None:
        """校验 finding 扩展字段不会覆盖稳定公共字段。

        参数:
            self: 当前不可变 VG 证据对象。
        返回:
            无业务返回值。
        异常:
            ValueError: metadata 使用保留公共字段名时抛出。
        """

        # 公共字段只能由 dataclass 的显式参数提供。
        set_reserved_keys = {"path", "line", "message", "evidence", "severity"}  # 禁止覆盖的报告键

        # 逐项检查扩展键，避免 payload.update 改写定位或等级。
        for str_key, _ in self.metadata:

            # 保留键会破坏既有 finding JSON 合同。
            if str_key in set_reserved_keys:

                # 非法扩展必须在构造阶段明确失败。
                raise ValueError("> ERR: [Python] finding metadata 不得覆盖公共字段")

        # 没有显式诊断时，从真实旧 finding 字段构造兼容 v3 载荷。
        if self.diagnostic is None:

            # 旧 emitter 尚未携带规则上下文，先使用稳定占位并由 facade 绑定。
            dict_legacy_payload = {  # 兼容旧字段组装成 v3 输入
                "rule_id": "VG000",  # 占位规则编号
                "rule_key": "legacy_finding",  # 兼容规则键
                "severity": self.severity or "WARNING",  # 继承 finding 等级
                "path": self.path,  # 真实文件路径
                "line": self.line,  # 真实源码行
                "message": self.message,  # 旧问题文本
                "evidence": self.evidence,  # 旧结构证据
            }  # 兼容 finding 的实际事实

            # build_legacy_diagnostic 负责不伪造 line=1 和补齐 guidance。
            dict_diagnostic = build_legacy_diagnostic(dict_legacy_payload)  # 旧 finding v3 适配结果

        # 显式诊断分支从此处继续，确保 else 与 if 有清晰语义边界。
        else:

            # 显式诊断必须经过同一契约校验，禁止绕过报告门禁。
            dict_diagnostic = diagnostic_from_mapping(self.diagnostic)  # 已有 v3 诊断副本

        # frozen dataclass 只能在构造后通过 object.__setattr__ 保存规范化诊断。
        object.__setattr__(self, "diagnostic", dict_diagnostic)

    # to_dict 把不可变证据模型转换为报告字典。
    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的证据字典。

        参数:
            self: 当前不可变 VG 证据对象。
        返回:
            字段稳定、可直接写入 JSON 报告的字典。
        """

        # v3 诊断字段作为主载荷，旧别名继续保留给历史消费者。
        dict_payload = dict(self.diagnostic or {})  # 可执行 finding 主体

        # path/line/message 保留旧 API 形状，但不覆盖 v3 location/evidence。
        dict_payload.update({
            "code": dict_payload.get("rule_id"),
            "rule": dict_payload.get("rule_key"),
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "severity": self.severity,
            "metadata": dict(self.metadata),
        })  # 兼容旧字段别名

        # 只有文件级门禁提供扩展事实时才增加新键。
        if self.metadata:

            # 扩展键已在构造阶段完成保留字段检查。
            dict_payload.update(dict(self.metadata))

        # 返回可直接写入 JSON 报告的字段集合。
        return dict_payload

    # with_rule_context 把占位诊断绑定到真实 catalog 规则。
    def with_rule_context(
        self,
        *,
        rule_id: str,
        rule_key: str,
        severity: str,
        status: str | None = None,
    ) -> "VgFinding":
        """返回绑定规则上下文的 finding 副本。

        参数:
            self: 当前不可变 finding。
            rule_id: catalog 中的 VGddd 编号。
            rule_key: catalog 中的规则键。
            severity: 规则或 finding 等级。
            status: 可选的门禁结果状态。
        返回:
            带真实规则上下文和 v3 诊断的 finding。
        """

        # 复制诊断并更新规则元数据，保留原始位置、证据和指导。
        dict_context = dict(self.diagnostic or {})  # 规则上下文副本

        # 绑定真实 catalog 规则编号和键。
        dict_context["rule_id"] = rule_id  # 重新绑定 active VG 编号

        # 规则键决定下游 catalog 查询和修复提示归属。
        dict_context["rule_key"] = rule_key  # 重新绑定 catalog 规则键

        # 规则等级决定终端显示和阻断策略。
        dict_context["severity"] = severity  # 重新绑定规则严重等级

        # status 缺省时沿用旧 finding 的状态。
        if status is not None:

            # 结果状态由语义引擎或 native facade 提供。
            dict_context["status"] = status  # 绑定结果状态

        # 从诊断 location 重新取得旧 path/line 别名，避免伪造行号。
        tuple_path_line = diagnostic_path_line(dict_context)  # 兼容定位别名

        # 先组装构造参数，避免长调用遮蔽定位和诊断绑定关系。
        dict_finding_kwargs = {
            "path": tuple_path_line[0],  # 由 location 派生的文件兼容字段
            "line": tuple_path_line[1],  # 由 source location 派生的行兼容字段
            "message": self.message,  # 旧消费者读取的问题文本
            "evidence": self.evidence,  # 旧消费者读取的证据文本
            "severity": severity,  # 绑定后的规则等级
            "metadata": self.metadata,  # 保留 emitter 扩展字段
            "diagnostic": dict_context,  # v3 可执行诊断
        }  # 新 finding 的完整构造参数

        # 返回新的不可变 finding，不修改原始规则 emitter 结果。
        return VgFinding(**dict_finding_kwargs)

# VgEvaluation 固定单条 active 门禁的内部执行结论。
@dataclass(frozen=True)
class VgEvaluation:
    """保存一条激活 VG 门禁的执行结论。"""

    # status 只能取 VG 引擎声明的固定状态集合。
    status: str  # 当前门禁执行后的固定状态

    # applicable 表明当前 RTL 是否出现该规则的适用结构。
    applicable: bool  # 当前门禁是否存在可分析对象

    # findings 保留所有确定违规或不确定证据。
    findings: tuple[VgFinding, ...] = field(default_factory=tuple)  # 当前门禁的有序证据集合

    # message 解释无具体 finding 时的状态原因。
    message: str = ""  # 当前状态的补充说明

# passed 统一创建规则通过结论。
def passed(*, applicable: bool = False, message: str = "") -> VgEvaluation:
    """构造通过结论。

    参数:
        applicable: 当前 RTL 是否出现规则适用结构。
        message: 可选的通过状态补充说明。
    返回:
        状态固定为 passed 的门禁结论。
    """

    # 通过结论不携带违规证据。
    return VgEvaluation("passed", applicable, message=message)

# failed 统一创建带确定证据的失败结论。
def failed(*findings: VgFinding) -> VgEvaluation:
    """构造包含确定违规证据的失败结论。

    参数:
        findings: 当前规则确认命中的一个或多个证据。
    返回:
        状态固定为 failed 的门禁结论。
    """

    # 失败结论始终标记规则适用并保留证据顺序。
    return VgEvaluation("failed", True, tuple(findings))

# inconclusive 统一创建证据不足的 fail-closed 结论。
def inconclusive(message: str, *findings: VgFinding) -> VgEvaluation:
    """构造分析信息不足时的 fail-closed 结论。

    参数:
        message: 说明无法得出确定结论的原因。
        findings: 可选的不确定证据位置。
    返回:
        状态固定为 inconclusive 的门禁结论。
    """

    # 不确定结论保持适用状态，交由 strict 交付策略阻断。
    return VgEvaluation("inconclusive", True, tuple(findings), message)
