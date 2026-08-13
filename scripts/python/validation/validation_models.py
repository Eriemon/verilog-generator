"""定义 Verilog validation 报告使用的稳定数据模型。"""

# future annotations 避免运行期解析 Path 和集合类型。
from __future__ import annotations

# dataclass 用于固定 issue/report 的 JSON 契约。
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# READINESS_LEVELS 是 CLI、脚本和集成层共享的验证阶段集合。
READINESS_LEVELS = ("static", "compile", "execute", "implement")  # 验证阶段从静态到实现递进

# ERROR_SOURCES 是 validation issue 的稳定来源枚举。
ERROR_SOURCES = (  # 验证报告可使用的问题来源全集
    "spec_issue",  # 规格或 artifact 合同问题
    "dependency_issue",  # 依赖缺失或依赖输出问题
    "testbench_issue",  # testbench 或语义验证问题
    "current_module_issue",  # 当前 RTL/产物本身的问题
    "insufficient_debug",  # 调试证据不足
    "toolchain_issue",  # 外部工具链或仿真环境问题
    "needs_human_intervention",  # 需要人工确认的问题
)

# dict_readiness_order 让 readiness 比较保持 O(1) 且避免导入期控制流。
dict_readiness_order = {  # 验证阶段到递进序号的固定映射
    "static": 0,  # 仅执行静态 gate
    "compile": 1,  # 需要真实编译证据
    "execute": 2,  # 需要真实仿真运行证据
    "implement": 3,  # 需要综合或实现级证据
}

# ValidationIssue 表示验证报告中的单条诊断。
@dataclass(frozen=True)
class ValidationIssue:
    """保存单条验证问题的展示字段和机器可读字段。"""

    # severity 使用 error/warning/skip 等稳定文本。
    severity: str  # 问题严重级别

    # message 是给用户和门禁日志展示的主要信息。
    message: str  # 问题描述

    # path 指向触发问题的 artifact，相对路径和绝对路径均兼容。
    path: str | None = None  # 关联 artifact 路径

    # stage 表示问题所属 validation 阶段。
    stage: str = "static"  # 验证阶段

    # source 表示问题归因，供 workflow 决策和诊断路由使用。
    source: str = "current_module_issue"  # 问题来源分类

    # case_id 绑定 semantic contract 的测试用例。
    case_id: str | None = None  # 关联 reference case 标识

    # tool 绑定外部工具或仿真后端。
    tool: str | None = None  # 关联工具名称

    # detail 保存截断后的工具输出或修复提示。
    detail: str | None = None  # 诊断细节

    # format 生成历史兼容的人类可读单行诊断。
    def format(self) -> str:
        """返回 validation report 文本中的单行问题描述。

        参数:
            self: 当前验证问题实例。

        返回:
            包含严重级别、来源、可选工具、case 和路径的单行文本。
        """

        # location 只在有路径时展示，避免空括号污染日志。
        str_location = f" [{self.path}]" if self.path else ""  # 路径展示片段

        # case_text 只在 reference case 绑定时展示。
        str_case_text = f" case={self.case_id}" if self.case_id else ""  # case 展示片段

        # 工具名称放在来源之后，便于同一问题来源下区分仿真后端。
        str_tool_text = f" tool={self.tool}" if self.tool else ""  # 外部工具展示片段

        # 返回格式保持 v0.3.0 之前的 report 文本契约。
        return f"{self.severity.upper()}[{self.source}]{str_tool_text}{str_case_text}: {self.message}{str_location}"

    # to_dict 生成发布和 workflow 使用的机器可读报告片段。
    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 可序列化的问题字典。

        参数:
            self: 当前验证问题实例。

        返回:
            保持历史字段顺序的 issue 字典。
        """

        # 字段顺序保持旧报告稳定，方便 diff 和测试断言。
        return {
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "stage": self.stage,
            "source": self.source,
            "case_id": self.case_id,
            "tool": self.tool,
            "detail": self.detail,
        }

# ValidationReport 汇总一次 Verilog artifact 验证结果。
@dataclass(frozen=True)
class ValidationReport:
    """保存验证目标、根路径、诊断集合和度量字段。"""

    # target 当前仅使用 rtl，但保留字段兼容集成层。
    target: str  # 验证目标名称

    # root 是被验证 artifact 根目录。
    root: Path  # 被验证目录

    # issues 保存所有阶段的有序诊断。
    issues: tuple[ValidationIssue, ...]  # 诊断集合

    # metrics 保存 gate、semantic 和 toolchain 的结构化证据。
    metrics: dict[str, Any] | None = None  # 验证度量

    # errors 统计阻塞级问题数量。
    @property

    # errors 属性给 CLI 和 JSON 摘要复用。
    def errors(self) -> int:
        """返回 error 级诊断数量。

        参数:
            self: 当前验证报告实例。

        返回:
            严重级别为 error 的诊断数量。
        """

        # 只统计 severity 精确等于 error 的诊断。
        return sum(1 for issue in self.issues if issue.severity == "error")

    # warnings 统计非阻塞警告数量。
    @property

    # warnings 属性保留非阻塞诊断数量。
    def warnings(self) -> int:
        """返回 warning 级诊断数量。

        参数:
            self: 当前验证报告实例。

        返回:
            严重级别为 warning 的诊断数量。
        """

        # warning 统计保留给报告摘要和调用方升级策略使用。
        return sum(1 for issue in self.issues if issue.severity == "warning")

    # skips 统计跳过项数量。
    @property

    # skips 属性记录被跳过的验证项数量。
    def skips(self) -> int:
        """返回 skip 级诊断数量。

        参数:
            self: 当前验证报告实例。

        返回:
            严重级别为 skip 的诊断数量。
        """

        # skip 不参与 ok 判定，但必须保留给旧脚本展示。
        return sum(1 for issue in self.issues if issue.severity == "skip")

    # ok 是 workflow 判断本轮验证是否通过的主入口。
    def ok(self) -> bool:
        """返回当前报告是否没有 error 级诊断。

        参数:
            self: 当前验证报告实例。

        返回:
            没有 error 级诊断时返回 True。
        """

        # 零 error 即视为通过，warning 交由调用方决定是否升级。
        return self.errors == 0

    # format 生成 CLI 使用的可读文本报告。
    def format(self) -> str:
        """返回按 readiness stage 分组的文本报告。

        参数:
            self: 当前验证报告实例。

        返回:
            CLI 和 smoke 日志使用的多行文本报告。
        """

        # lines 保存最终报告的有序文本行。
        list_lines = [f"Validation report for {self.target} at {self.root}"]  # 报告文本行

        # 逐阶段分组输出，保持旧版 CLI 展示稳定。
        for str_stage in READINESS_LEVELS:

            # stage_issues 是当前阶段的诊断集合。
            list_stage_issues: list[ValidationIssue] = []  # 当前 readiness 阶段关联的问题

            # 逐条筛选当前阶段诊断，保持报告输出顺序。
            for issue in self.issues:

                # 非当前阶段的问题留给后续 stage 输出。
                if issue.stage != str_stage:

                    # 继续检查下一条诊断。
                    continue

                # 当前 stage 的诊断追加到输出列表。
                list_stage_issues.append(issue)

            # 有问题时输出阶段标题和问题列表。
            if list_stage_issues:

                # 阶段标题保留方括号格式。
                list_lines.append(f"[{str_stage}]")

                # 单条问题使用 ValidationIssue.format 统一生成。
                list_lines.extend(issue.format() for issue in list_stage_issues)

            # 静态阶段无问题时保留历史成功提示。
            elif str_stage == "static":

                # 静态阶段标题用于 smoke 中的可读日志。
                list_lines.append("[static]")

                # 静态检查通过信息用于 smoke 中的可读日志。
                list_lines.append("INFO: Static checks passed.")

        # summary 汇总所有诊断计数。
        list_lines.append(f"Summary: {self.errors} error(s), {self.warnings} warning(s), {self.skips} skip(s)")

        # metrics 存在时直接展示结构化字典，保持旧行为。
        if self.metrics:

            # Metrics 行用于 workflow trace 快速查看。
            list_lines.append(f"Metrics: {self.metrics}")

        # 返回完整文本报告。
        return "\n".join(list_lines)

    # to_dict 生成 JSON 报告主体。
    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 可序列化的 validation report。

        参数:
            self: 当前验证报告实例。

        返回:
            包含目标、根路径、计数、诊断和度量的报告字典。
        """

        # metrics 缺省为空 dict，避免消费方重复判空。
        dict_metrics = self.metrics or {}  # JSON 报告中的度量字段

        # issues 按原顺序展开，方便回归对比。
        list_issues = [issue.to_dict() for issue in self.issues]  # JSON 报告中的诊断列表

        # 字段名保持历史契约，不额外加入内部状态。
        return {
            "target": self.target,
            "root": str(self.root),
            "ok": self.ok(),
            "errors": self.errors,
            "warnings": self.warnings,
            "skips": self.skips,
            "issues": list_issues,
            "metrics": dict_metrics,
        }

# require_readiness 规范化调用方传入的 readiness 字符串。
def require_readiness(readiness: str) -> str:
    """返回合法的小写 readiness，否则抛出 ValueError。

    参数:
        readiness: 调用方传入的 readiness 阶段名称。

    返回:
        已转换为小写且存在于 READINESS_LEVELS 的阶段名称。
    """

    # normalized 保持 CLI 大小写输入兼容。
    str_normalized = readiness.lower()  # 规范化后的 readiness

    # 非法 readiness 必须尽早失败，避免后续比较 KeyError。
    if str_normalized not in dict_readiness_order:

        # 错误信息列出合法值，方便 CLI 用户修复。
        raise ValueError(f"> ERR: [Python] Readiness must be one of {', '.join(READINESS_LEVELS)}.")

    # 返回合法 readiness。
    return str_normalized

# readiness_at_least 判断 readiness 是否达到某个阶段。
def readiness_at_least(readiness: str, stage: str) -> bool:
    """判断 readiness 是否覆盖指定 stage。

    参数:
        readiness: 当前验证 readiness 阶段。
        stage: 需要检查的最低 readiness 阶段。

    返回:
        当前 readiness 序号不低于目标 stage 时返回 True。
    """

    # 阶段序号比较表达 readiness 的单调递进关系。
    return dict_readiness_order[readiness] >= dict_readiness_order[stage]
