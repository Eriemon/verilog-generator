"""对 Erie 风格生成 RTL 执行确定性的可读性质量门检查。"""

# 延迟类型注解求值，避免质量门模块导入时解析复杂联合类型。
from __future__ import annotations

# 标准库依赖保持 quality gate facade 在无第三方包环境运行。
from pathlib import Path
from typing import Any

# 统一 catalog 为原生样式规则和迁移语义规则提供相同元数据。
from scripts.python.workflow.verilog_gate_catalog import load_verilog_quality_gates

# formatter_ast 入口仍由 facade 负责组织文件级 orchestration。
from .formatter_ast import iter_verilog_sources, read_verilog_source

# 结构化类型中的核心报告对象由 facade 稳定回导。
from .quality_gate_types import (
    QualityGateReport,
    QualityGateRunContext,
    QualityIssue,
)

# 其余上下文类型继续保留旧导入面，避免调用方改路径。
from .quality_gate_types import (
    CommentReuseCandidate,
    CommentVerticalSpacingContext,
    OutputAssignRegionContext,
    ProtocolOrderIssueContext,
    SameLineCommentCheckContext,
    StructuredCommentContext,
)

# facade 继续回导旧测试依赖的头部分隔常量与底层文本 helper。
from .quality_gate_common import (
    HEADER_CHINESE_SEPARATOR,
    HEADER_ENGLISH_SEPARATOR,
    _is_code_line,
    _is_testbench,
    _line_comment,
)

# AST 诊断转换和目录级聚合仍由 AST 子模块负责。
from .quality_gate_ast import (
    _ast_diagnostics_to_issues,
    _build_ast_tree_report,
    build_ast_report_for_path,
)

# 注释与 FSM 规则各自保持独立子模块。
from .quality_gate_comment_rules import _comment_rules
from .quality_gate_fsm_rules import _fsm_next_state_rules, _fsm_rules

# header 与协议相关规则继续按原分工拆开导入。
from .quality_gate_header_rules import _header_rules
from .quality_gate_protocol_rules import (
    _header_semantic_rules,
    _protocol_port_order_rules,
    _reset_semantic_rules,
)

# 区域、报告、结构与原始文本规则保留原兼容导出。
from .quality_gate_region_rules import _region_ownership_rules, _rulebook_consistency_issues
from .quality_gate_reports import write_quality_gate_report
from .quality_gate_structure_rules import _module_rules
from .quality_gate_text_rules import _raw_text_rules
from .vg_semantic_engine import run_vg_semantic_gate
from .vg_semantic_facts import build_vg_facts_from_reports

# 组织目录级质量门运行并保持旧入口签名稳定。
def run_verilog_quality_gate(
    root: Path,
    *,
    strict: bool = True,
    comment_language: str = "zh",
    formatter_profile: str = "formatter-normalize",
    include_testbench: bool = False,
    vitis_wrapper: bool = False,
    spec: dict[str, Any] | None = None,
) -> QualityGateReport:
    """
    运行确定性的 Verilog 可读性和风格质量门。

    :param root: 需要检查的 Verilog 源文件或目录入口。
    :param strict: 是否把样式和注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :param formatter_profile: formatter_ast 使用的解析 profile。
    :param include_testbench: 是否把 testbench 文件纳入质量门。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :param spec: 可选归一化设计规格，用于补充语义门禁事实。
    :return: 包含诊断、metrics 和 AST 汇总的质量门报告。
    """

    # path_root 统一绝对路径，保证报告 root 字段稳定。
    path_root = root.resolve()  # 本次检查的规范化入口路径

    # list_files 只包含需要进入质量门的 Verilog 文件。
    list_files = _quality_gate_source_files(path_root, include_testbench)  # 待检查 RTL 文件集合

    # list_issues 汇总文件发现、AST、文本和结构规则诊断。
    list_issues: list[QualityIssue] = []  # 本次质量门累计诊断

    # list_file_reports 保存逐文件 formatter AST 报告。
    list_file_reports: list[dict[str, Any]] = []  # 逐文件 AST 报告集合

    # dict_aggregate_metrics 统计行数、编码和 formatter 决策。
    dict_aggregate_metrics = _empty_metrics()  # 聚合质量指标

    # quality_context 统一携带单文件 helper 共享的质量门选项。
    quality_context = QualityGateRunContext(  # 单文件规则共享的质量门运行上下文
        path_root=path_root,  # 生成相对路径和报告 root 的绝对入口
        strict=strict,  # 控制样式和注释诊断 severity 的模式
        comment_language=comment_language,  # 注释语义检查使用的目标语言
        formatter_profile=formatter_profile,  # formatter_ast 后端解析配置名
        vitis_wrapper=vitis_wrapper,  # 放宽 Vitis wrapper ABI 端口命名的开关
    )

    # 没有源文件时直接登记文件发现错误。
    if not list_files:

        # 空输入会让质量门失败，避免误报成功。
        list_issues.append(
            QualityIssue(
                "VG000",
                "error",
                "No Verilog source files were found.",
                str(path_root),
                rule="file.discovery",
            )
        )

    # 逐文件执行唯一 formatter AST 解析入口和所有质量规则。
    for path_source in list_files:

        # 单文件 helper 负责读取、AST、规则和编码 metrics 合并。
        _append_file_quality_results(
            list_issues,
            list_file_reports,
            dict_aggregate_metrics,
            path_source,
            quality_context,
        )

    # dict_ast_tree_report 保持目录级 AST 聚合报告字段。
    dict_ast_tree_report = _build_ast_tree_report(  # 目录级 formatter AST 聚合报告
        path_root,  # 聚合报告使用的检查根路径
        formatter_profile,  # formatter_ast 使用的 profile 名称
        list_file_reports,  # 已成功解析的逐文件 AST 报告
    )  # 目录级 AST 报告

    # 聚合文件数来自成功构建 AST 的文件数。
    dict_aggregate_metrics["files"] = len(list_file_reports)  # 已完成 AST 报告的文件数

    # 聚合 module 数复用 AST summary，避免重复口径。
    dict_aggregate_metrics["modules"] = dict_ast_tree_report["summary"]["modules"]  # 已解析 module 总数

    # 迁移语义段复用本轮已经生成的 formatter AST，禁止第二次解析。
    vg_facts = build_vg_facts_from_reports(  # 共享 AST 语义事实
        path_root,  # 语义执行器消费的规范 RTL 入口
        list_file_reports,  # 已生成的逐文件 AST 报告
        spec=spec,  # 调用方提供的可选设计规格
    )

    # 迁移语义段通过统一入口生成 VG072-VG145 结果。
    dict_semantic_report = run_vg_semantic_gate(  # 72 条迁移语义规则报告
        path_root,  # 本轮统一质量门扫描根
        spec=spec,  # 语义规则可选设计规格
        strict=strict,  # WARNING 级结果的阻断策略
        include_testbench=include_testbench,  # testbench 纳入策略
        facts=vg_facts,  # 复用本轮唯一 AST 事实
    )

    # catalog 元数据把原生 issue 聚合为 VG000-VG071 的逐规则结论。
    dict_catalog = load_verilog_quality_gates()  # 已验证的 121 条统一规则目录

    # 原生规则结果与迁移语义结果采用相同公开模型。
    list_native_results = _native_vg_rule_results(  # 49 条原生规则结果
        dict_catalog,  # 统一规则元数据和稳定顺序
        list_issues,  # 原生质量门诊断
        bool(list_files),  # 是否发现可检查 RTL
    )

    # 两段结果严格按 catalog 顺序拼成完整统一报告。
    list_vg_results = (  # 121 条 VG 结果
        list_native_results + list(dict_semantic_report["vg_rule_results"])  # 原生段后接语义段
    )

    # 语义规则的失败或不确定状态进入统一 issue 列表，复用既有 ok/errors 语义。
    _append_semantic_issues(list_issues, dict_semantic_report["vg_rule_results"], strict=strict)

    # 全量摘要不再沿用语义子引擎的 72 条局部计数。
    dict_vg_summary = _summarize_vg_rule_results(list_vg_results)  # 121 条规则执行摘要

    # 返回不可变报告对象，供 CLI 或验证流程序列化。
    return QualityGateReport(
        root=path_root,
        issues=tuple(list_issues),
        metrics=dict_aggregate_metrics,
        ast_report=dict_ast_tree_report,
        strict=strict,
        vg_catalog_version=int(dict_catalog["version"]),
        vg_rule_summary=dict_vg_summary,
        vg_rule_results=tuple(list_vg_results),
    )

# _native_vg_rule_results 把原生 QualityIssue 聚合为逐规则结果。
def _native_vg_rule_results(
    dict_catalog: dict[str, Any],
    list_issues: list[QualityIssue],
    bool_has_sources: bool,
) -> list[dict[str, Any]]:
    """构造 VG000 至 VG071 已发射规则的逐规则结论。

    参数:
        dict_catalog: 已验证的统一 VG 规则目录。
        list_issues: 原生质量门已经产生的诊断。
        bool_has_sources: 当前运行是否发现 Verilog 输入。
    返回:
        按 catalog 顺序组织的原生逐规则结果。
    """

    # 先按规则码归组，避免每条 catalog 记录重复扫描全部诊断。
    dict_issues_by_code: dict[str, list[QualityIssue]] = {}  # 原生规则码到诊断集合

    # 每条原生诊断只归入自身固定 VG 编号。
    for quality_issue in list_issues:

        # setdefault 保持同一规则的诊断出现顺序。
        dict_issues_by_code.setdefault(quality_issue.code, []).append(quality_issue)

    # 只处理迁移段之前实际存在的 49 条规则。
    list_results: list[dict[str, Any]] = []  # 原生 VG 逐规则结果

    # catalog 顺序是公开逐规则结果的唯一顺序来源。
    for dict_rule in dict_catalog["rules"]:

        # 当前 catalog 条目提供固定 VG 主键。
        str_gate_id = str(dict_rule["gate_id"])  # 当前 catalog 规则码

        # VG072 之后由语义引擎负责，停止原生段聚合。
        if int(str_gate_id[2:]) >= 72:

            # 后续规则不得在两个执行器中重复报告。
            break

        # 当前规则只消费与其固定编号相同的原生诊断。
        list_rule_issues = dict_issues_by_code.get(str_gate_id, [])  # 当前规则全部诊断

        # 公开模型始终保留规则元数据、状态和逐项证据。
        list_results.append(
            {
                "gate_id": str_gate_id,
                "rule_key": dict_rule["rule_key"],
                "level": dict_rule["level"],
                "catalog_status": "active",
                "status": "failed" if list_rule_issues else "passed",
                "applicable": bool_has_sources,
                "message": "" if bool_has_sources else "No Verilog source was discovered.",
                "findings": [
                    {
                        "path": quality_issue.path or "",
                        "line": quality_issue.line or 1,
                        "message": quality_issue.message,
                        "evidence": quality_issue.rule or "",
                    }
                    for quality_issue in list_rule_issues
                ],
            }
        )

    # 返回值保持 catalog 的稳定顺序。
    return list_results

# _append_semantic_issues 让语义结果参与统一 errors/warnings 判定。
def _append_semantic_issues(
    list_issues: list[QualityIssue],
    list_results: list[dict[str, Any]],
    *,
    strict: bool,
) -> None:
    """把未通过的语义规则转换为统一质量诊断。

    参数:
        list_issues: 接收转换后诊断的统一列表。
        list_results: 语义引擎产生的逐规则结果。
        strict: 是否把 WARNING 级非通过结果升级为 error。
    返回:
        无；诊断直接追加到 ``list_issues``。
    """

    # 每条未通过结果按 finding 粒度映射为统一诊断。
    for dict_result in list_results:

        # 已通过结果不需要重复生成 issue。
        if dict_result["status"] == "passed":

            # 继续处理其他语义规则。
            continue

        # 没有定位证据时仍生成规则级 fail-closed 诊断。
        list_findings = list(dict_result["findings"]) or [  # 当前规则的定位证据或规则级回退
            {
                "path": None,  # 规则级回退没有文件位置
                "line": None,  # 规则级回退没有源码行号
                "message": dict_result["message"] or "VG semantic rule did not pass.",  # 非通过原因
            }
        ]

        # 每个 finding 独立保留路径、行号和规则键。
        for dict_finding in list_findings:

            # finding 可覆盖 catalog 的默认治理等级。
            str_finding_level = str(dict_finding.get("severity") or dict_result["level"])  # finding 级或目录级治理等级

            # strict 模式把 WARNING 非通过状态统一升级为 error。
            str_severity = "error" if str_finding_level == "BLOCKER" or strict else "warning"  # 当前阻断策略

            # 统一 issue 让既有 errors/warnings 汇总无需理解语义子模型。
            list_issues.append(
                QualityIssue(
                    str(dict_result["gate_id"]),
                    str_severity,
                    str(dict_finding["message"]),
                    dict_finding.get("path"),
                    dict_finding.get("line"),
                    str(dict_result["rule_key"]),
                )
            )

# _summarize_vg_rule_results 生成稳定的全目录状态计数。
def _summarize_vg_rule_results(list_results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总全部统一 VG 逐规则结果。

    参数:
        list_results: 按 catalog 顺序组织的完整逐规则结果。
    返回:
        规则总数、激活数和各公开状态计数。
    """

    # 公开状态顺序保持 JSON 和 Markdown 报告字段稳定。
    tuple_statuses = ("passed", "failed", "inconclusive", "error", "not_run")  # 公开状态顺序

    # 每个状态都显式计数，零值字段也不会从报告消失。
    return {
        "total": len(list_results),
        "active": len(list_results),
        "status_counts": {
            str_status: sum(dict_result["status"] == str_status for dict_result in list_results)
            for str_status in tuple_statuses
        },
    }

# 从检查入口收集需要进入质量门的 Verilog 源文件。
def _quality_gate_source_files(path_root: Path, include_testbench: bool) -> list[Path]:
    """
    返回质量门需要扫描的 Verilog 文件列表。

    :param path_root: 质量门入口文件或目录。
    :param include_testbench: 是否把 testbench 文件纳入扫描。
    :return: 已按 testbench 策略过滤后的源文件路径列表。
    """

    # list_files 保留 iter_verilog_sources 的稳定遍历顺序。
    list_files = [  # 进入质量门的 RTL 源文件集合
        path_source  # 通过 include_testbench 过滤后的源文件
        for path_source in iter_verilog_sources(path_root)  # 遍历检查根下的 Verilog 源
        if include_testbench or not _is_testbench(path_source)  # 默认排除 testbench 文件
    ]

    # 返回过滤后的源文件集合。
    return list_files

# 合并单个源文件的读取、AST 与规则诊断结果。
def _append_file_quality_results(
    list_issues: list[QualityIssue],
    list_file_reports: list[dict[str, Any]],
    dict_aggregate_metrics: dict[str, Any],
    path_source: Path,
    quality_context: QualityGateRunContext,
) -> None:
    """
    读取单个 Verilog 文件并合并 AST、文本、结构和注释诊断。

    :param list_issues: 质量门主报告正在累计的诊断列表。
    :param list_file_reports: 质量门主报告正在累计的逐文件 AST 报告。
    :param dict_aggregate_metrics: 质量门主报告正在累计的 metrics 字典。
    :param path_source: 当前待检查的 Verilog 源文件。
    :param quality_context: 单次质量门运行的共享配置。
    :return: 本函数原地合并诊断、AST 报告和 metrics。
    """

    # str_rel_path 是报告中展示的文件定位字段。
    str_rel_path = _rel_path(path_source, quality_context.path_root)  # 相对检查根的源文件路径

    # tuple_source 保存文本和编码；读取失败时已登记 VG000。
    tuple_source = _read_source_for_quality_gate(path_source, str_rel_path, list_issues)  # Verilog 源读取结果

    # 读取失败的文件没有可信文本，跳过后续规则。
    if tuple_source is None:

        # 当前文件已经产生文件读取诊断。
        return

    # str_text 是后续文本规则和 AST 规则共享的源码。
    str_text = tuple_source[0]  # 当前文件 Verilog 源文本

    # str_encoding 记录 read_verilog_source 实际采用的编码。
    str_encoding = tuple_source[1]  # 当前文件读取编码

    # include 片段默认不应进入完整 RTL strict normalize 交付门禁。
    _append_include_fragment_issue(
        list_issues,
        path_source,
        str_rel_path,
        quality_context.strict,
        quality_context.formatter_profile,
    )

    # dict_ast_report 使用 formatter_ast 的唯一 parser 后端构建。
    dict_ast_report = build_ast_report_for_path(path_source, profile=quality_context.formatter_profile)  # 当前文件 AST 报告

    # relative_path 帮助下游把 AST 报告和质量门诊断对齐。
    dict_ast_report["relative_path"] = str_rel_path  # AST 报告中的相对路径

    # 保存逐文件 AST 报告供最终聚合。
    list_file_reports.append(dict_ast_report)

    # 累计基础文本和 formatter 决策指标。
    _accumulate_metrics(dict_aggregate_metrics, str_text, dict_ast_report)

    # 单文件 AST、文本、结构和注释规则诊断按原顺序合并。
    _append_file_rule_issues(
        list_issues,
        dict_ast_report,
        str_text,
        str_rel_path,
        quality_context,
    )

    # 编码 metrics 记录混合编码工程风险。
    _record_encoding_metric(dict_aggregate_metrics, str_encoding)

# 安全读取源文件并把读取异常转成质量门诊断。
def _read_source_for_quality_gate(
    path_source: Path,
    str_rel_path: str,
    list_issues: list[QualityIssue],
) -> tuple[str, str] | None:
    """
    返回 Verilog 源文本和编码，读取失败时登记质量门诊断。

    :param path_source: 当前待读取的 Verilog 源文件。
    :param str_rel_path: 报告中展示的相对路径。
    :param list_issues: 质量门主报告正在累计的诊断列表。
    :return: 读取成功时返回源码文本和编码，失败时返回 None。
    """

    # 读取源码时保留实际编码，用于最终 metrics。
    try:

        # tuple_source 保存文本和编码，避免重复读文件。
        tuple_source = read_verilog_source(path_source)  # Verilog 源文本及命中编码

    # 单个源文件读取失败不能阻断其他文件继续检查。
    except Exception as exc:

        # 文件读取失败时登记错误并让调用方跳过该文件。
        list_issues.append(
            QualityIssue(
                "VG000",
                "error",
                f"Unable to read Verilog source: {exc}",
                str_rel_path,
                rule="file.encoding",
            )
        )

        # None 明确表示当前文件无可信文本。
        return None

    # 返回读取成功的源码文本和编码。
    return tuple_source

# 在 strict delivery 路径上阻断 include fragment 误用。
def _append_include_fragment_issue(
    list_issues: list[QualityIssue],
    path_source: Path,
    str_rel_path: str,
    strict: bool,
    formatter_profile: str,
) -> None:
    """
    在 strict formatter-normalize 模式下阻断 include fragment。

    :param list_issues: 质量门主报告正在累计的诊断列表。
    :param path_source: 当前待检查的 Verilog 源文件。
    :param str_rel_path: 报告中展示的相对路径。
    :param strict: 是否按最终交付模式执行。
    :param formatter_profile: formatter_ast 使用的解析 profile。
    :return: 本函数原地追加 VG058 诊断。
    """

    # include_fragment 表示 .vh 片段误走完整 RTL normalize 路径。
    bool_include_fragment = path_source.suffix.lower() == ".vh"  # include 片段文件标志

    # 非 strict、非 include 或非 formatter-normalize 时无需阻断。
    if not strict or not bool_include_fragment or formatter_profile != "formatter-normalize":

        # 当前文件不触发 VG058。
        return

    # include fragment 需要 lint/preserve 路径或人工声明完整 module 边界。
    list_issues.append(
        QualityIssue(
            "VG058",
            "error",
            "Include fragments must not be treated as formatter-normalize delivery RTL by default.",
            str_rel_path,
            rule="profile.include_fragment",
        )
    )

# 按既定顺序合并单文件的所有规则子集。
def _append_file_rule_issues(
    list_issues: list[QualityIssue],
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    quality_context: QualityGateRunContext,
) -> None:
    """
    合并单个文件的 AST 诊断、文本规则、结构规则和注释规则。

    :param list_issues: 质量门主报告正在累计的诊断列表。
    :param dict_ast_report: formatter_ast 生成的当前文件报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中展示的相对路径。
    :param quality_context: 单次质量门运行的共享配置。
    :return: 本函数原地追加单文件规则诊断。
    """

    # formatter AST 自身诊断先映射为质量门问题。
    list_issues.extend(_ast_diagnostics_to_issues(dict_ast_report, str_rel_path, strict=quality_context.strict))

    # 原始文本规则检查缩进、文件头和行级注释。
    list_issues.extend(
        _raw_text_rules(
            str_text,
            str_rel_path,
            strict=quality_context.strict,
            comment_language=quality_context.comment_language,
        )
    )

    # module 结构规则依赖 formatter AST 的 module/port/always 等模型。
    list_module_issues = _file_module_rule_issues(dict_ast_report, str_text, str_rel_path, quality_context)  # module 结构诊断

    # module 结构诊断保持在注释规则之前合入。
    list_issues.extend(list_module_issues)

    # 注释覆盖和语义规则复用 AST 中的声明、赋值和实例信息。
    list_comment_issues = _file_comment_rule_issues(dict_ast_report, str_text, str_rel_path, quality_context)  # 注释覆盖与语义诊断

    # 注释诊断最后合入，保持旧报告顺序。
    list_issues.extend(list_comment_issues)

# 包装 module 结构规则调用，保持 facade 兼容入口。
def _file_module_rule_issues(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    quality_context: QualityGateRunContext,
) -> list[QualityIssue]:
    """
    返回单个文件的 module 结构规则诊断。

    :param dict_ast_report: formatter_ast 生成的当前文件报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中展示的相对路径。
    :param quality_context: 单次质量门运行的共享配置。
    :return: module 级结构、命名和区域规则诊断列表。
    """

    # 返回 module 规则输出列表，调用方负责合并到文件级报告。
    return _module_rules(
        dict_ast_report,  # module/port/always 等结构化节点来源
        str_text,  # module 区域和 header 规则使用的源码文本
        str_rel_path,  # 诊断报告中的相对路径
        strict=quality_context.strict,  # style severity 的 strict 开关
        vitis_wrapper=quality_context.vitis_wrapper,  # Vitis wrapper 命名例外开关
    )

# 包装注释规则调用，保持 facade 兼容入口。
def _file_comment_rule_issues(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    quality_context: QualityGateRunContext,
) -> list[QualityIssue]:
    """
    返回单个文件的注释覆盖和语义规则诊断。

    :param dict_ast_report: formatter_ast 生成的当前文件报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中展示的相对路径。
    :param quality_context: 单次质量门运行的共享配置。
    :return: 注释覆盖、注释深度和块级说明诊断列表。
    """

    # 返回注释规则输出列表，调用方负责合并到文件级报告。
    return _comment_rules(
        dict_ast_report,  # 提供声明、赋值和实例化的注释上下文
        str_text,  # 行注释密度和 header 注释扫描文本
        str_rel_path,  # 注释诊断报告中的相对路径
        strict=quality_context.strict,  # 注释 severity 的 strict 开关
        comment_language=quality_context.comment_language,  # 注释语言约束
    )

# 累计单文件编码分布，供最终报告展示工程读写情况。
def _record_encoding_metric(dict_aggregate_metrics: dict[str, Any], str_encoding: str) -> None:
    """
    累计单个源码文件的读取编码。

    :param dict_aggregate_metrics: 质量门主报告正在累计的 metrics 字典。
    :param str_encoding: 当前文件读取时命中的编码名称。
    :return: 本函数原地更新 encodings 计数。
    """

    # encodings 字段用于定位混合编码工程。
    dict_aggregate_metrics["encodings"].setdefault(str_encoding, 0)

    # 当前文件命中编码计数加一。
    dict_aggregate_metrics["encodings"][str_encoding] += 1  # 当前文件编码出现次数

# 构造每次质量门运行都重新分配的 metrics 初值。
def _empty_metrics() -> dict[str, Any]:
    """
    返回质量门聚合指标的初始结构。

    参数:
        无外部业务参数。

    :return: 质量门 metrics 初始字典。
    """

    # dict_metrics 字段名保持质量门 JSON 消费方的历史契约。
    dict_metrics = {  # 单次质量门运行的聚合指标初值
        "files": 0,  # 成功生成 AST 报告的文件数
        "modules": 0,  # AST summary 中的 module 总数
        "lines": 0,  # 源码总行数
        "code_lines": 0,  # 非空非纯注释代码行数
        "line_comments": 0,  # 含双斜线的文本行数
        "commented_code_lines": 0,  # 带真实行注释的代码行数
        "block_comment_markers": 0,  # 块注释边界标记数
        "formatter_decisions": {},  # formatter 路由决策分布
        "encodings": {},  # 源文件编码分布
    }

    # 返回新字典，避免跨次运行共享状态。
    return dict_metrics

# 把单文件文本统计累计到目录级 metrics 结构。
def _accumulate_metrics(dict_metrics: dict[str, Any], str_text: str, dict_ast_report: dict[str, Any]) -> None:
    """
    把单个 Verilog 文件的行数、注释和 formatter 决策累计到 metrics。

    :param dict_metrics: 待累计更新的质量门 metrics 字典。
    :param str_text: 当前 Verilog 源码文本。
    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :return: 无返回值，直接累计更新 metrics 字典。
    """

    # list_lines 保存单文件的原始行序列，供多项 metrics 复用。
    list_lines = str_text.splitlines()  # metrics 累加使用的源码行序列

    # list_code_lines 只统计 metrics 口径下的真实 RTL 代码行。
    list_code_lines = [  # 单文件 code_lines 指标的源码行集合
        str_line  # 将计入 code_lines 的 RTL 源码行
        for str_line in list_lines  # 遍历单文件源码行
        if _is_code_line(str_line)  # 只保留会影响 RTL 语义的代码行
    ]  # metrics.code_lines 使用的非空非纯注释行

    # int_line_comment_count 统计包含 // 的文本行，保持旧 metrics 口径。
    int_line_comment_count = sum(  # metrics.line_comments 本文件增量
        1  # 每个包含 // 的文本行计一次
        for str_line in list_lines  # 扫描原始文本行
        if "//" in str_line  # 按旧 metrics 口径统计所有双斜线行
    )

    # int_commented_code_line_count 统计既是代码又带行注释的行。
    int_commented_code_line_count = sum(  # 本文件带注释代码行增量
        1  # 每个带行注释的代码行计一次
        for str_line in list_code_lines  # 扫描已过滤的 RTL 代码行
        if _line_comment(str_line)  # 只统计代码行上的真实行注释
    )

    # int_block_marker_count 统计块注释开始和结束标记。
    int_block_marker_count = (
        str_text.count("/*") + str_text.count("*/")  # 开始和结束标记都计入 metrics
    )  # 本文件块注释边界增量

    # 累计基础行数和注释指标。
    dict_metrics["lines"] += len(list_lines)  # 累计源码总行数

    # 累计真实 RTL 代码行数。
    dict_metrics["code_lines"] += len(list_code_lines)  # 累计可执行 RTL 行数

    # 累计带双斜线的文本行数。
    dict_metrics["line_comments"] += int_line_comment_count  # 累计含行注释标记的行数

    # 累计带行注释的代码行数。
    dict_metrics["commented_code_lines"] += int_commented_code_line_count  # 累计带注释的 RTL 行数

    # 累计块注释边界标记数。
    dict_metrics["block_comment_markers"] += int_block_marker_count  # 累计块注释边界数

    # 仅在 AST 报告仍显式提供 formatter 路由决策时累计该统计。
    obj_score_payload = dict_ast_report.get("score")  # 兼容旧报告结构的 formatter 路由载荷

    # 旧报告会提供包含 decision 字段的字典；新报告缺失时不再伪造 unknown。
    if isinstance(obj_score_payload, dict):

        # 提取旧报告中的 formatter 路由决策名。
        str_decision = str(obj_score_payload.get("decision") or "").strip()  # 旧报告中的决策标签

        # 只有真实决策标签存在时才累计统计，避免把缺失字段误报成 unknown。
        if str_decision:

            # 初始化当前 formatter 决策计数。
            dict_metrics["formatter_decisions"].setdefault(str_decision, 0)

            # 当前文件对应决策计数加一。
            dict_metrics["formatter_decisions"][str_decision] += 1  # 当前 formatter 决策出现次数

# 根据检查根生成稳定的相对或绝对报告路径。
def _rel_path(path_source: Path, path_root: Path) -> str:
    """
    根据检查入口生成稳定的报告路径。

    :param path_source: 当前正在检查的 Verilog 源文件路径。
    :param path_root: 质量门检查入口根路径。
    :return: 用于报告展示的稳定路径文本。
    """

    # 单文件入口时直接展示文件名。
    if path_root.is_file():

        # 文件名保持旧行为。
        return path_source.name

    # 尝试生成相对目录路径。
    try:

        # 成功时使用 POSIX 风格，便于跨平台报告比较。
        return path_source.relative_to(path_root).as_posix()

    # 不在检查根目录下时使用完整路径兜底。
    except ValueError:

        # 不在 root 下时退回完整 POSIX 路径。
        return path_source.as_posix()

# 返回 facade 需要保留的兼容导出名称清单。
def _export_names() -> list[str]:
    """
    返回 facade 对外继续公开的兼容符号名。

    参数:
        无外部业务参数。

    :return: 稳定的兼容导出名称列表。
    """

    # str_exports_source 按旧测试与调用方依赖顺序保留兼容导出名原文。
    str_exports_source = """
    CommentReuseCandidate
    CommentVerticalSpacingContext
    OutputAssignRegionContext
    ProtocolOrderIssueContext
    QualityGateReport
    QualityGateRunContext
    QualityIssue
    SameLineCommentCheckContext
    StructuredCommentContext
    HEADER_CHINESE_SEPARATOR
    HEADER_ENGLISH_SEPARATOR
    run_verilog_quality_gate
    write_quality_gate_report
    _raw_text_rules
    _header_semantic_rules
    _protocol_port_order_rules
    _reset_semantic_rules
    _region_ownership_rules
    _rulebook_consistency_issues
    _module_rules
    _fsm_rules
    _fsm_next_state_rules
    _comment_rules
    _header_rules
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持旧调用方依赖的 facade 兼容导出面。
__all__ = _export_names()  # facade 对外兼容导出列表
