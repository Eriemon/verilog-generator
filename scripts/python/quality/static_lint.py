"""提供兼容旧调用方的 RTL VG 门禁诊断 facade。"""

# future annotations 延后解析兼容诊断的类型标注。
from __future__ import annotations

# dataclass 保持旧调用方依赖的不可变诊断对象形状。
from dataclasses import dataclass

# pathlib 统一文件或目录形式的 RTL 入口。
from pathlib import Path

# typing 描述规格和序列化字典中的动态字段。
from typing import Any

# VG 引擎是兼容 facade 唯一允许调用的 RTL 规则实现。
from .quality_gate import run_verilog_quality_gate

# StaticLintIssue 仅保留历史调用方需要的字段接口。
@dataclass(frozen=True)
class StaticLintIssue:
    """保持旧调用方可消费的单条 RTL 诊断对象。

    参数:
        severity: `error` 或 `warning`。
        message: 人类可读的 VG 门禁消息。
        path: 触发问题的相对文件路径。
        line: 一基源码行号。
        source: 问题来源类别。
        code: 固定 VG 门禁编号。

    返回:
        无；实例字段由 validation 与 helper facade 消费。
    """

    # severity 保留历史 error/warning 分支合同。
    severity: str  # VG catalog 等级映射后的严重度

    # message 承载固定 VG 规则的人类可读诊断。
    message: str  # 当前 VG finding 或门禁级消息

    # path 使用相对路径定位违规 RTL 文件。
    path: str  # finding 提供的 RTL 相对路径

    # line 使用一基行号兼容既有报告消费者。
    line: int  # finding 提供或回退的一基行号

    # source 区分 VG 引擎与其他质量门来源。
    source: str = "current_module_issue"  # 兼容默认诊断来源

    # code 迁移后只允许固定 VG 编号。
    code: str = "VG_ERROR"  # 实例化时写入统一 VG 编号

    # to_dict 为旧 JSON 报告提供稳定的字段映射。
    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的诊断字典。

        参数:
            无。

        返回:
            包含严重级别、定位、来源和 VG 编号的字典。
        """

        # 字典不添加旧 lint 专属字段，code 始终承载固定 VG 编号。
        return {
            "severity": self.severity,  # 兼容 error 或 warning 严重度
            "message": self.message,  # VG 门禁的人类可读诊断
            "path": self.path,  # 违规 RTL 相对路径
            "line": self.line,  # 一基源码行号
            "source": self.source,  # 固定 VG 诊断来源
            "code": self.code,  # 统一 VG 固定编号
        }

# lint_generated_rtl 把权威 VG 报告映射为历史对象列表。
def lint_generated_rtl(spec: dict[str, Any], root: Path) -> list[StaticLintIssue]:
    """通过固定 VG 引擎返回兼容格式的 RTL 诊断。

    参数:
        spec: 可选设计规格合同。
        root: 待检查的 Verilog 文件或目录。

    返回:
        激活 VG 门禁中所有非通过结果对应的兼容诊断列表。
    """

    # strict 模式保证 WARNING 与 BLOCKER 都按旧 lint 预期返回。
    dict_report = run_verilog_quality_gate(root, spec=spec, strict=True).to_dict()  # 权威统一 VG 执行报告

    # 输出列表只做形状转换，不重新判断 RTL 语义。
    list_issues: list[StaticLintIssue] = []  # 兼容调用方消费的 VG 诊断对象

    # reserved 和 passed 条目不会形成历史 lint issue。
    for dict_result in dict_report["vg_rule_results"]:

        # 只转换已激活且未通过的固定门禁。
        if dict_result["catalog_status"] != "active" or dict_result["status"] == "passed":

            # 当前目录条目无需暴露为兼容诊断。
            continue

        # catalog 等级直接映射为旧接口的严重度。
        str_severity = "error" if dict_result["level"] == "BLOCKER" else "warning"  # 当前 VG 严重度

        # 真实 finding 优先保留精确路径和行号。
        list_findings = list(dict_result.get("findings") or [])  # 当前 VG 的定位证据

        # fail-closed 状态没有精确位置时仍需生成一条兼容诊断。
        if not list_findings:

            # 占位 finding 使用门禁级消息并明确缺少路径。
            list_findings = [
                {
                    "path": "",  # 空路径表示规则没有可靠文件定位
                    "line": 1,  # 缺少精确位置时使用稳定的一基行号
                    "message": dict_result.get("message") or f"{dict_result['gate_id']} did not pass.",  # 门禁级诊断
                }
            ]

        # 一条 VG finding 对应一个历史诊断对象。
        for dict_finding in list_findings:

            # 字段转换不改变固定编号、严重度或定位证据。
            list_issues.append(
                StaticLintIssue(
                    severity=str_severity,  # 旧接口据此选择 error 或 warning 展示
                    message=str(dict_finding.get("message") or dict_result.get("message") or "VG gate did not pass."),  # VG 诊断文本
                    path=str(dict_finding.get("path") or ""),  # finding 提供的 RTL 路径
                    line=int(dict_finding.get("line") or 1),  # finding 提供的一基行号
                    source="verilog_quality_gate",  # 权威规则来源标识
                    code=str(dict_result["gate_id"]),  # 当前固定 VG 编号
                )
            )

    # 返回值保持旧调用方的 list[StaticLintIssue] 合同。
    return list_issues
