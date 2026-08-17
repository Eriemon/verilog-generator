"""承载 quality gate facade 需要回导的结构化数据类型。"""

# 延迟类型注解求值，避免类型对象在导入阶段过早解析。
from __future__ import annotations

# 标准库类型与 dataclass 足以表达质量门报告结构。
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# v3 诊断模块负责 VG finding 的契约校验和兼容别名。
from .vg_diagnostics import (
    build_legacy_diagnostic,
    diagnostic_from_mapping,
    diagnostic_path_line,
)

# v3 renderer 把结构化 finding 展开为 Agent 可直接执行的 Markdown。
from .vg_diagnostic_render import render_finding_markdown

# 供 `QualityIssue` 复用的结构化类型，专门承载描述单条 Verilog 质量门诊断。
@dataclass
class QualityIssue:
    """描述单条 Verilog 质量门诊断。"""

    # code 对应 VGxxx 规则编号。
    code: str  # 质量门规则编号

    # severity 使用 error/warning，保持报告消费方兼容。
    severity: str  # 诊断严重级别

    # message 是面向用户展示的诊断正文。
    message: str  # 诊断说明文本

    # path 允许为空，支持聚合级规则。
    path: str | None = None  # 相对或绝对文件路径

    # line 允许为空，支持无精确行号的结构规则。
    line: int | None = None  # 诊断所在行号

    # rule 保留稳定的规则命名空间，便于外部统计。
    rule: str | None = None  # 规则命名空间

    # vg_diagnostic 保存 VG v3 flat finding，非 VG issue 保持 None。
    vg_diagnostic: dict[str, Any] | None = field(default=None, repr=False)  # 可执行 VG 诊断

    # metadata 保留历史调用方传入的结构化扩展字段。
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)  # 兼容 finding 扩展元数据

    # __post_init__ 为历史 native emitter 补齐 v3 诊断而不伪造行号。
    def __post_init__(self) -> None:
        """校验或构造 VG 诊断载荷。

        参数:
            self: 当前质量门 issue 对象。
        返回:
            无业务返回值。
        """

        # 非 VG issue 继续使用原有六字段合同。
        if not self.code.startswith("VG"):

            # 非 VG 规则不应被强行包装成 VG finding。
            return

        # 显式 v3 载荷必须经过统一复制和校验。
        if self.vg_diagnostic is not None:

            # 诊断转换层拒绝缺字段或错误状态。
            dict_diagnostic = diagnostic_from_mapping(self.vg_diagnostic)  # 已有 v3 诊断副本

        # 旧 issue 分支保留真实 path/line 并进入兼容适配。
        else:

            # 旧 native issue 仍保留真实 path/line/message/evidence 事实。
            dict_legacy_payload: dict[str, Any] = {  # 旧质量门字段用于构造源码定位、证据和修复步骤
                "rule_id": self.code,  # 绑定当前 issue 的 VG 规则编号
                "rule_key": self.rule or "legacy_quality_issue",  # lookup 对应规则的机器键
                "severity": self.severity,  # native issue 严重等级
                "path": self.path,  # native issue 文件路径
                "line": self.line,  # source 定位可用的真实源码行号
                "message": self.message,  # native issue 问题文本
                "evidence": self.rule or "native quality gate issue",  # native 规则事实
            }  # native issue 的实际观察事实

            # 兼容适配器不把未知行号转换为 line=1。
            dict_diagnostic = build_legacy_diagnostic(dict_legacy_payload)  # 生成带定位和 guidance 的 finding

        # 保存规范化诊断，后续报告只读取该字段。
        self.vg_diagnostic = dict_diagnostic  # 保存规范化 VG 诊断

    # to_dict 输出 JSON 兼容字段。
    def to_dict(self) -> dict[str, Any]:
        """
        把诊断对象转换为 JSON 友好的字典。

        :param self: 当前质量门数据对象实例。
        :return: JSON 可序列化的报告或诊断字典。
        """

        # 先保留旧字段，再附加 v3 扁平诊断字段。
        dict_payload = {  # legacy issue 的稳定字段集合
            "code": self.code,  # 旧规则编号
            "severity": self.severity,  # 旧严重等级
            "message": self.message,  # 旧诊断正文
            "path": self.path,  # 旧文件路径
            "line": self.line,  # 旧源码行
            "rule": self.rule,  # 旧规则命名空间
            "metadata": dict(self.metadata),  # 非 VG 诊断的兼容扩展字段
        }  # legacy issue 字段

        # 非 VG issue 没有 v3 finding，但仍提供结构化事实供旧消费者读取。
        if self.vg_diagnostic is None and not self.code.startswith("VG"):

            # 外部 parser 等调用方至少能获得可序列化的错误事实对象。
            dict_payload["evidence"] = {
                "node_kind": str(self.metadata.get("origin") or "quality_issue"),  # 证据来源节点类型
                "source_excerpt": self.message,  # 原始问题正文作为外部事实片段
                "detail": self.message,  # 结构化事实细节保持与正文一致
            }

        # VG issue 使用可执行 finding 作为新增主载荷。
        if self.vg_diagnostic is not None:

            # 从 v3 location 派生兼容 path/line，未知行号保持 None。
            tuple_path_line = diagnostic_path_line(self.vg_diagnostic)  # v3 location 的兼容别名

            # v3 finding 作为新增 flat 字段覆盖 legacy 同名字段。
            dict_payload = {**dict_payload, **self.vg_diagnostic}  # 合并 flat v3 finding 字段

            # 公开 path 只反映 location 的真实文件。
            dict_payload["path"] = tuple_path_line[0]  # v3 location 文件别名

            # 公开 line 只反映 source location 的真实起始行。
            dict_payload["line"] = tuple_path_line[1]  # v3 source 起始行别名

            # v3 finding 同时公开旧 rule/code/metadata 别名，便于旧消费者继续读取。
            dict_payload["code"] = self.code  # 兼容规则编号别名

            # 兼容规则键别名保持与 v3 rule_key 同源。
            dict_payload["rule"] = self.rule  # 兼容规则键别名

            # 兼容扩展字段使用独立副本，避免调用方修改内部状态。
            dict_payload["metadata"] = dict(self.metadata)  # 兼容结构化扩展字段

        # 返回 JSON 兼容 issue/finding 字典。
        return dict_payload

# 供 `QualityGateReport` 复用的结构化类型，专门承载汇总一次 RTL 质量门运行的诊断、指标和 AST 报告。
@dataclass
class QualityGateReport:
    """汇总一次 RTL 质量门运行的诊断、指标和 AST 报告。"""

    # root 是本次质量门检查的输入根。
    root: Path  # 检查入口路径

    # issues 保持不可变，防止报告生成后被外部追加。
    issues: tuple[QualityIssue, ...]  # 全部质量门诊断

    # metrics 汇总文本和结构统计，供验证流程展示。
    metrics: dict[str, Any]  # 聚合统计指标

    # ast_report 直接暴露 formatter AST 聚合结果。
    ast_report: dict[str, Any]  # formatter AST 聚合报告

    # strict 决定部分样式规则是 error 还是 warning。
    strict: bool  # 严格模式开关

    # vg_catalog_version 标识统一规则目录版本。
    vg_catalog_version: int  # 统一 VG catalog 版本

    # vg_rule_summary 汇总全部规则的执行状态。
    vg_rule_summary: dict[str, Any]  # 逐规则状态摘要

    # vg_rule_results 按 catalog 顺序保存每条规则结论。
    vg_rule_results: tuple[dict[str, Any], ...]  # 121 条统一 VG 结果

    # errors 属性给 CLI 和报告摘要复用。
    @property

    # errors 统计阻断质量门的诊断数量。
    def errors(self) -> int:
        """
        返回 error 级诊断数量。

        :param self: 当前质量门数据对象实例。
        :return: error 级诊断数量。
        """

        # 只统计 severity 明确为 error 的诊断。
        return sum(1 for issue in self.issues if issue.severity == "error")

    # warnings 属性保留非阻断质量门的诊断数量。
    @property

    # warnings 统计仅提示但不阻断的诊断数量。
    def warnings(self) -> int:
        """
        返回 warning 级诊断数量。

        :param self: 当前质量门数据对象实例。
        :return: warning 级诊断数量。
        """

        # warning 统计用于 Markdown 摘要和 JSON 顶层字段。
        return sum(1 for issue in self.issues if issue.severity == "warning")

    # ok 维持旧 API 的方法形式。
    def ok(self) -> bool:
        """
        判断本次质量门是否没有 error 级问题。

        :param self: 当前质量门数据对象实例。
        :return: 没有 error 级诊断时返回 True。
        """

        # 只有 error 数量为零时质量门才通过。
        return self.errors == 0

    # to_dict 输出稳定 JSON 报告。
    def to_dict(self) -> dict[str, Any]:
        """
        转换为 v0.3.0 兼容的质量门 JSON 报告。

        :param self: 当前质量门数据对象实例。
        :return: JSON 可序列化的报告或诊断字典。
        """

        # list_findings 提供顶层 v3 findings，同时保留独立 legacy issues 投影。
        list_findings: list[dict[str, Any]] = []  # 可执行 VG finding 的顶层副本

        # v3 finding 与 legacy issue 的严重度语义必须分别投影。
        for issue in self.issues:

            # 非 VG issue 不进入顶层 actionable findings。
            if issue.vg_diagnostic is None:

                # 继续收集下一条 VG finding。
                continue

            # 复制兼容字段，确保顶层 finding 仍含旧 code/path/line 等别名。
            dict_finding = issue.to_dict()  # 当前 issue 的兼容 finding 字典

            # 顶层 v3 finding 保留 catalog/emitter 的大写等级。
            dict_finding["severity"] = issue.vg_diagnostic["severity"]  # 顶层 v3 诊断等级

            # 保存与原对象隔离的 finding 副本。
            list_findings.append(dict_finding)

        # legacy issues 使用小写 error/warning，兼容旧质量门消费者。
        list_issues: list[dict[str, Any]] = [issue.to_dict() for issue in self.issues]  # 旧 issues 的独立副本

        # v3 finding 的严重度保持在 list_findings 中，不被 legacy 投影覆盖。
        for issue, dict_issue in zip(self.issues, list_issues):

            # 仅 VG issue 需要把 catalog 等级转回旧小写枚举。
            if str(dict_issue.get("code") or "").startswith("VG"):

                # 兼容 issues[*].severity 的历史 error/warning 语义，并保留 strict 升级。
                dict_issue["severity"] = str(issue.severity).lower()  # 转回 legacy issues 小写等级，保持旧消费者合同

        # dict_report 保持历史字段和嵌套结构。
        dict_report = {  # 质量门 JSON 报告主体
            "version": 3,  # 统一 VG 质量门报告结构版本
            "root": str(self.root),  # 检查入口路径文本
            "ok": self.ok(),  # error 诊断是否为零
            "strict": self.strict,  # 本次运行 strict 开关
            "errors": self.errors,  # error 级诊断数量
            "warnings": self.warnings,  # 非阻断诊断数量
            "issues": list_issues,  # JSON legacy 诊断列表
            "findings": list_findings,  # 顶层可执行 VG finding 列表
            "metrics": self.metrics,  # 文本和结构聚合指标
            "ast_report": self.ast_report,  # 原始结构解析聚合树
            "vg_catalog_version": self.vg_catalog_version,  # 统一规则目录版本
            "vg_rule_summary": self.vg_rule_summary,  # 全部规则状态摘要
            "vg_rule_results": list(self.vg_rule_results),  # 按 catalog 顺序的逐规则结果
        }

        # 返回给 CLI、validation 和 integration 复用。
        return dict_report

    # to_markdown 输出用户可读表格。
    def to_markdown(self) -> str:
        """
        转换为 Markdown 格式的质量门报告。

        :param self: 当前质量门数据对象实例。
        :return: Markdown 格式的质量门报告文本。
        """

        # list_markdown_lines 先写入 Markdown 标题和概要字段。
        list_markdown_lines = [  # 每个元素是一行 Markdown，最终由换行 join 生成返回字符串
            "# Verilog quality gate",  # 报告首行固定标题
            "",  # 标题和 root 字段之间的 Markdown 段落间隔
            f"Root: `{self.root}`",  # 展示本次质量门检查入口路径
            f"Strict: `{self.strict}`",  # 展示本次质量门是否启用严格模式
            "",  # strict 字段和 summary 段之间的 Markdown 段落间隔
        ]

        # 摘要行对齐旧报告格式。
        list_markdown_lines.append(f"Summary: **{self.errors} error(s)**, **{self.warnings} warning(s)**")

        # 空行分隔摘要和诊断表格。
        list_markdown_lines.append("")

        # 无诊断时返回简短成功正文。
        if not self.issues:

            # 保留英文固定文案以兼容既有测试快照。
            list_markdown_lines.append("No quality-gate findings.")

            # Markdown 文件总是以换行结尾。
            return "\n".join(list_markdown_lines) + "\n"

        # v3 findings 逐条展开问题、证据、指导和 bad/good 示例。
        list_vg_findings = [  # 可执行 VG finding 的 Markdown 输入
            issue.to_dict() for issue in self.issues if issue.vg_diagnostic is not None  # 只渲染 v3 诊断
        ]

        # 有 VG finding 时优先输出 Agent 可直接执行的结构化诊断。
        if list_vg_findings:

            # 标题把 v3 诊断与历史表格分开，避免字段语义混淆。
            list_markdown_lines.extend(["## Actionable VG findings", ""])

            # 每个 finding 使用稳定编号作为 Markdown 锚点。
            for int_index, dict_finding in enumerate(list_vg_findings, start=1):

                # 锚点文本与 finding 内容分离，保证报告链接稳定。
                str_anchor = f"vg-finding-{int_index}"  # 当前 finding 的 Markdown 锚点

                # 渲染器负责 location、evidence、guidance 和 examples 的细节布局。
                str_finding_markdown = render_finding_markdown(dict_finding, str_anchor)  # 将 v3 finding 转成包含修复步骤和示例的 Markdown 诊断块

                # 去除尾部换行后再拼接一个段落间隔。
                list_markdown_lines.extend([str_finding_markdown.rstrip("\n"), ""])

        # 非 VG issue 保留历史表格，维持非 VG 调用方兼容。
        list_legacy_issues = [  # 非 VG 兼容诊断集合
            issue for issue in self.issues if issue.vg_diagnostic is None  # 保留非 VG 旧问题
        ]

        # 表格只展示未被 v3 renderer 接管的旧问题。
        if list_legacy_issues:

            # 表头保持外部文档中的列顺序。
            list_markdown_lines.append("| Severity | Code | Path | Line | Message |")

            # 表格分隔行固定为 GitHub Markdown 格式。
            list_markdown_lines.append("|---|---|---|---:|---|")

            # 每条旧诊断转成 Markdown 表格行。
            for issue in list_legacy_issues:

                # str_path 为空字符串时表示聚合级诊断。
                str_path = issue.path or ""  # Markdown 表格中的路径文本

                # str_line 为空字符串时表示无精确行号。
                str_line = "" if issue.line is None else str(issue.line)  # Markdown 表格中的行号文本

                # str_message 转义竖线，避免破坏 Markdown 表格。
                str_message = issue.message.replace("|", "\\|")  # 表格安全诊断文本

                # 追加单条兼容诊断行。
                list_markdown_lines.append(
                    f"| {issue.severity} | {issue.code} | `{str_path}` | {str_line} | {str_message} |"
                )

        # Markdown 报告保持末尾换行。
        return "\n".join(list_markdown_lines) + "\n"

# 供 `QualityGateRunContext` 复用的结构化类型，专门承载承载单次 Verilog 质量门运行的共享配置。
@dataclass
class QualityGateRunContext:
    """承载单次 Verilog 质量门运行的共享配置。"""

    # path_root 用于生成相对路径和聚合报告 root 字段。
    path_root: Path  # 质量门入口路径

    # strict 决定样式和注释问题是否升级为 error。
    strict: bool  # strict 交付模式开关

    # comment_language 控制中文注释规则是否启用。
    comment_language: str  # 注释语言策略

    # formatter_profile 控制 formatter_ast 的解析配置。
    formatter_profile: str  # formatter_ast 后端解析策略名称

    # vitis_wrapper 控制 Vitis wrapper 端口兼容例外。
    vitis_wrapper: bool  # Vitis wrapper 命名兼容开关

# 供 `ProtocolOrderIssueContext` 复用的结构化类型，专门承载承载单个协议端口排序诊断的共享上下文。
@dataclass
class ProtocolOrderIssueContext:
    """承载单个协议端口排序诊断的共享上下文。"""

    # dict_section_rank 用于判断端口 section 是否发生顺序回退。
    dict_section_rank: dict[str, int]  # 协议 section 到排序号的映射

    # tuple_sections 保留 rulebook 声明的合法 section 顺序。
    tuple_sections: tuple[str, ...]  # 协议 section 合法顺序

    # str_protocol 保存 axi/axis/apb 等协议 token。
    str_protocol: str  # 当前检查的协议名称

    # str_rel_path 保留调用方看到的 Verilog 相对路径。
    str_rel_path: str  # Verilog 诊断相对路径

    # strict 决定 VG057 是 error 还是 warning。
    strict: bool  # 协议端口顺序严格模式

# 供 `StructuredCommentContext` 复用的结构化类型，专门承载承载单个结构化条目的注释诊断上下文。
@dataclass
class StructuredCommentContext:
    """承载单个结构化条目的注释诊断上下文。"""

    # str_name 是端口、声明、parameter 或 assign 左值名称。
    str_name: str  # 被检查结构条目的名称

    # str_label 用于诊断文本展示结构类别。
    str_label: str  # AST 条目类别标签

    # str_rel_path 保留重复注释问题所在的源文件。
    str_rel_path: str  # 重复注释诊断源文件

    # int_line_no 把注释诊断定位到 AST 条目的实体行。
    int_line_no: int | None  # 注释诊断对应的源码实体行

    # str_severity 由 strict 模式决定。
    str_severity: str  # 覆盖类注释诊断级别

    # comment_language 控制中文优先规则是否参与注释深度检查。
    comment_language: str  # 中文优先检查策略

# 供 `SameLineCommentCheckContext` 复用的结构化类型，专门承载承载单条同线注释检查需要的上下文。
@dataclass
class SameLineCommentCheckContext:
    """承载单条同线注释检查需要的上下文。"""

    # str_rel_path 指向触发同线注释规则的 Verilog 文件。
    str_rel_path: str  # 同线注释诊断文件路径

    # str_severity 保存 VG062/VG064 的最终级别。
    str_severity: str  # 同线缺注释诊断级别

    # comment_language 传递给语义深度检查器。
    comment_language: str  # 同线注释语言策略

    # str_code 是缺注释时输出的 VG 规则编号。
    str_code: str  # 缺失同线注释规则编号

    # str_label 用于诊断文本展示行类别。
    str_label: str  # 同线注释条目类别

    # str_rule 保留稳定规则命名空间。
    str_rule: str  # 诊断规则命名空间

# 供 `CommentVerticalSpacingContext` 复用的结构化类型，专门承载承载纯注释上方空行布局检查的诊断上下文。
@dataclass
class CommentVerticalSpacingContext:
    """承载纯注释上方空行布局检查的诊断上下文。"""

    # str_rel_path 指向触发空行布局规则的 Verilog 文件。
    str_rel_path: str  # 空行布局诊断文件路径

    # str_severity 保存空行布局问题的门禁级别。
    str_severity: str  # 前导空行问题级别

    # str_code 是空行布局问题输出的 VG 规则编号。
    str_code: str  # 空行布局规则编号

    # str_label 用于诊断文本展示注释类别。
    str_label: str  # 空行布局注释类别

    # str_rule 标识前导或分组注释的布局规则来源。
    str_rule: str  # 空行布局规则来源

# 供 `CommentReuseCandidate` 复用的结构化类型，专门承载承载一条可参与重复检测的实体注释。
@dataclass
class CommentReuseCandidate:
    """承载一条可参与重复检测的实体注释。"""

    # str_comment 保留原始注释正文，用于诊断展示。
    str_comment: str  # 原始注释正文

    # str_normalized 是精确重复检测使用的规范化文本。
    str_normalized: str  # 精确重复检测键

    # str_similarity_key 是近似重复检测使用的低噪声文本。
    str_similarity_key: str  # 近似重复检测键

    # str_label 表示该注释绑定的 RTL 实体类别。
    str_label: str  # 注释实体类别

    # str_name 表示端口、信号、assign 或过程赋值名称。
    str_name: str  # 注释绑定的实体名称

    # str_rel_path 是诊断报告使用的文件路径。
    str_rel_path: str  # 报告中的相对文件路径

    # int_line_no 指向后出现的复用注释行。
    int_line_no: int | None  # 复用注释报告行号

# 供 `OutputAssignRegionContext` 复用的结构化类型，专门承载承载 output bridge assign 区域归属检查上下文。
@dataclass
class OutputAssignRegionContext:
    """承载 output bridge assign 区域归属检查上下文。"""

    # set_output_ports 用于识别 assign 左值是否直接驱动顶层 output。
    set_output_ports: set[str]  # 顶层 output 端口集合

    # dict_region_by_line 支持从 assign 行号回溯最近区域横幅。
    dict_region_by_line: dict[int, str]  # 区域横幅行号映射

    # str_rel_path 是 VG052 诊断使用的报告路径。
    str_rel_path: str  # 区域归属诊断路径

    # strict 决定区域归属错误是否阻断交付。
    strict: bool  # 区域归属严格模式

# 返回当前模块需要公开的兼容导出名称清单。
def _export_names() -> list[str]:
    """
    返回当前模块对外继续公开的兼容符号名。

    参数:
        无外部业务参数。

    :return: 稳定的兼容导出名称列表。
    """

    # str_exports_source 按旧测试与调用方依赖顺序保留兼容导出名原文。
    str_exports_source = """
    QualityIssue
    QualityGateReport
    QualityGateRunContext
    ProtocolOrderIssueContext
    StructuredCommentContext
    SameLineCommentCheckContext
    CommentVerticalSpacingContext
    CommentReuseCandidate
    OutputAssignRegionContext
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
