"""聚合 readable Verilog 最终交付门禁。"""

# 延迟解析类型注解，避免运行时为报告类型引入额外依赖。
from __future__ import annotations

# 标准库承担报告序列化和路径处理。
import json
from pathlib import Path
from typing import Any

# 子门禁模块提供 AST、静态 lint、注释位置和规则源一致性证据。
from .comment_placement import validate_comment_placement
from .quality_gate import run_verilog_quality_gate
from scripts.python.validation.rulebook import load_verilog_rulebook

# 文本形式便于保持公开矩阵顺序，同时避免手写多行常量被误改。
DELIVERABLE_CHECK_NAMES_TEXT = "compile ast readability comment naming profile testbench toolchain"  # checks 字段对外承诺的固定键顺序

# tuple 形式供报告组装和 workflow pass 判定复用。
DELIVERABLE_CHECK_NAMES = tuple(DELIVERABLE_CHECK_NAMES_TEXT.split())  # workflow pass 判定复用的矩阵键元组

# 文本形式描述命名类风险的宽松匹配词。
NAMING_ISSUE_KEYWORDS_TEXT = "name naming prefix port signal instance parameter module"  # naming 门禁识别命名风险的关键词文本

# tuple 形式供关键词扫描函数复用。
NAMING_ISSUE_KEYWORDS = tuple(NAMING_ISSUE_KEYWORDS_TEXT.split())  # naming 门禁分类使用的关键词元组

# 文本形式描述风格合同风险的宽松匹配词。
PROFILE_ISSUE_KEYWORDS_TEXT = "profile style rulebook vitis wrapper fallback"  # profile 门禁识别风格合同风险的关键词文本

# tuple 形式供 profile 分类扫描复用。
PROFILE_ISSUE_KEYWORDS = tuple(PROFILE_ISSUE_KEYWORDS_TEXT.split())  # profile 风险扫描复用的不可变关键词集合

# 生成、修改和注释后的 RTL 都通过这个入口得到最终交付结论。
def run_verilog_deliverable_gate(
    root: Path,
    *,
    spec: dict[str, Any] | None = None,
    strict: bool = True,
    comment_language: str = "zh",
    formatter_profile: str = "formatter-normalize",
    include_testbench: bool = False,
    vitis_wrapper: bool = False,
) -> dict[str, Any]:
    """运行最终交付门禁并返回 JSON 友好的报告字典。

    参数:
        root: 需要检查的 Verilog 文件或目录。
        spec: 可选归一化设计规格，传递给 VG 门禁。
        strict: 是否启用交付级严格模式。
        comment_language: 注释语言策略。
        formatter_profile: formatter AST 使用的 profile。
        include_testbench: 是否纳入 testbench 文件。
        vitis_wrapper: 是否按 Vitis wrapper ABI 放宽端口规则。
    返回:
        返回包含 delivery_ready、checks 和 issues 的交付门禁报告。
    """

    # path_root 使用绝对路径，保证报告和 CLI 摘要稳定。
    path_root = root.resolve()  # 本次交付门禁入口路径

    # 子门禁原始结果先集中收集，后续统计阶段不再重复扫描文件。
    dict_context = _collect_deliverable_context(  # 保存各子门禁原始报告，后续只基于它统计交付状态
        path_root,  # 已规范化的 RTL 交付入口
        spec=spec,  # 归一化设计规格

        # 以下参数定义严格度、注释规则和扫描边界。
        strict=strict,  # 当前交付严格模式
        comment_language=comment_language,  # 注释语言策略
        formatter_profile=formatter_profile,  # formatter 抽象语法树配置名称
        include_testbench=include_testbench,  # 是否纳入 testbench 文件
        vitis_wrapper=vitis_wrapper,  # Vitis wrapper 端口规则开关
    )  # 交付门禁的原始子检查结果

    # 汇总 error 和 strict warning，保证最终交付条件只有一个判定来源。
    dict_totals = _count_deliverable_totals(dict_context, strict)  # 交付状态计数

    # checks 字段只保留公开八类矩阵，避免调用方依赖内部工具名。
    dict_checks = _build_deliverable_checks(  # 公开交付矩阵摘要
        dict_context,  # 子门禁上下文
        dict_totals,  # 聚合计数
        strict=strict,  # strict warning 是否阻断交付
        include_testbench=include_testbench,  # testbench 门禁是否被请求
    )

    # 报告组装阶段只做字段编排，不再执行新检查。
    dict_report = _build_deliverable_report(path_root, strict, dict_context, dict_totals, dict_checks)  # 最终报告

    # 返回 JSON 友好的最终报告。
    return dict_report

# _collect_deliverable_context 保留各子门禁的原始结果。
def _collect_deliverable_context(
    path_root: Path, *, spec: dict[str, Any] | None, strict: bool, comment_language: str,
    formatter_profile: str, include_testbench: bool, vitis_wrapper: bool,
) -> dict[str, Any]:
    """
    收集最终交付门禁需要的子检查结果。

    :param path_root: 需要检查的 Verilog 文件或目录。
    :param spec: 固定 VG 门禁消费的可选归一化设计规格。
    :param strict: 是否启用交付级严格模式。
    :param comment_language: 注释语言策略。
    :param formatter_profile: formatter AST 使用的 profile。
    :param include_testbench: 是否纳入 testbench 文件。
    :param vitis_wrapper: 是否按 Vitis wrapper ABI 放宽端口规则。
    :return: 包含质量门、lint 和注释 gate 原始结果的上下文字典。
    """

    # 质量门报告保留 VG 规则命中和 formatter AST 统计。
    report_quality = run_verilog_quality_gate(  # 带 AST 证据的 VG 质量门报告
        path_root,  # 待检查的 RTL 文件或目录
        strict=strict,  # 质量门严格模式
        comment_language=comment_language,  # 中文/英文注释要求
        formatter_profile=formatter_profile,  # formatter 抽象语法树配置档名称

        # 运行范围选项保持与交付门入口参数一致。
        include_testbench=include_testbench,  # testbench 纳入开关
        vitis_wrapper=vitis_wrapper,  # wrapper ABI 兼容开关
        spec=spec,  # 统一语义规则需要的可选规格合同
    )  # Verilog 质量门报告对象

    # 统一质量门报告已经包含完整逐规则结果，无需再次解析 RTL。
    dict_vg_report = report_quality.to_dict()  # 兼容后续聚合变量名的 schema v2 VG 报告

    # 注释位置 gate 输出诊断和覆盖率统计。
    tuple_comment_gate = validate_comment_placement(path_root, comment_language)  # 注释位置 gate 原始结果

    # 将 quality gate 的对象诊断转成最终报告可序列化结构。
    list_quality_issues = [issue.to_dict() for issue in report_quality.issues]  # VG 诊断字典集合

    # 统一质量报告的 issues 已包含语义规则，不再重复展开逐规则结果。
    list_vg_issues: list[dict[str, Any]] = []  # 保留上下文字段但禁止重复诊断

    # comment gate 诊断补齐 code/rule/severity 字段。
    list_comment_gate_issues = [_comment_placement_issue_to_dict(issue) for issue in tuple_comment_gate[0]]  # 注释诊断字典集合

    # 子门禁上下文把原始对象和序列化诊断放在一起，避免重复转换。
    dict_context = {
        "quality_report": report_quality,  # 保留 AST summary 和 VG 规则统计
        "vg_report": dict_vg_report,  # 保留逐门禁结果和摘要
        "comment_metrics": tuple_comment_gate[1],  # 注释位置覆盖率和密度指标
        "quality_issues": list_quality_issues,  # 已序列化的 VG 规则诊断
        "vg_issues": list_vg_issues,  # 已统一为交付 issue 的 VG 诊断
        "comment_issues": list_comment_gate_issues,  # 已补齐 severity 的注释诊断
    }  # 交付门禁上下文

    # 返回供统计和报告组装复用的上下文。
    return dict_context

# _count_deliverable_totals 只计算最终交付判定所需的数量。
def _count_deliverable_totals(dict_context: dict[str, Any], strict: bool) -> dict[str, Any]:
    """
    统计最终交付门禁的 error 和 strict warning。

    :param dict_context: _collect_deliverable_context 返回的子门禁上下文。
    :param strict: 是否启用 warning 阻断策略。
    :return: 包含各类 error、warning 和 delivery_ready 的计数字典。
    """

    # VG 质量门报告对象提供 error/warning 聚合计数。
    report_quality = dict_context["quality_report"]  # 质量门聚合计数来源

    # VG 报告提供激活规则的逐条状态和级别。
    list_vg_results = dict_context["vg_report"]["vg_rule_results"]  # 固定 VG 门禁结果

    # 注释 gate 诊断已补齐 severity 字段。
    list_comment_gate_issues = dict_context["comment_issues"]  # 注释位置诊断集合

    # VG error 来自 AST、命名、区域、reset、FSM 和注释语义规则。
    int_quality_errors = report_quality.errors  # VG 阻断项总量

    # 激活 BLOCKER 的所有非通过状态都按 fail-closed 计入错误。
    int_vg_errors = 0  # 逐规则非通过已经计入 report_quality.errors

    # 注释位置 error 表示实体级注释落点不满足交付要求。
    int_comment_errors = sum(1 for issue in list_comment_gate_issues if issue.get("severity") == "error")  # 注释落点阻断项

    # formatter mismatch 只从 AST summary 读取，不重复加入 VG/readability 诊断。
    dict_ast_summary = report_quality.ast_report.get("summary", {})  # formatter AST 聚合摘要

    # 每条 formatter violation 作为独立 AST error 计入最终交付。
    int_formatter_errors = int(dict_ast_summary.get("formatter_errors", 0) or 0)  # AST 子门禁的模板差异计数

    # VG warning 在 strict 交付中等同待修复问题。
    int_quality_warnings = report_quality.warnings  # VG 可疑项总量

    # 激活 WARNING 的非通过状态在 strict 模式计入待修复警告。
    int_vg_warnings = 0  # 逐规则 warning 已经计入 report_quality.warnings

    # 注释 warning 代表语义覆盖不足或位置不够可靠。
    int_comment_warnings = sum(1 for issue in list_comment_gate_issues if issue.get("severity") == "warning")  # 注释落点警告项

    # 最终 error 数量由 VG、lint 和 comment gate 三类阻断项组成。
    int_errors = int_quality_errors + int_vg_errors + int_comment_errors + int_formatter_errors  # 最终阻断问题总数

    # strict warning 汇总所有必须在交付前清零的 warning。
    int_strict_warnings = int_quality_warnings + int_vg_warnings + int_comment_warnings  # 严格模式待修复警告总数

    # 交付状态严格绑定到 error 和 strict warning 两类阻断因素。
    bool_delivery_ready = int_errors == 0 and (not strict or int_strict_warnings == 0)  # 最终可交付状态

    # 计数字典避免多个组装函数重复计算同一批数量。
    dict_totals = {
        "quality_errors": int_quality_errors,  # VG 深规则阻断计数
        "vg_errors": int_vg_errors,  # VG 阻断门禁非通过计数
        "comment_errors": int_comment_errors,  # 注释落点阻断计数
        "formatter_errors": int_formatter_errors,  # AST formatter 模板错误计数
        "quality_warnings": int_quality_warnings,  # VG 可疑诊断计数
        "vg_warnings": int_vg_warnings,  # VG 警告门禁非通过计数
        "comment_warnings": int_comment_warnings,  # 注释覆盖风险计数
        "errors": int_errors,  # 三类子门禁阻断合计
        "strict_warnings": int_strict_warnings if strict else 0,  # strict 报告展示的 warning 合计
        "delivery_ready": bool_delivery_ready,  # error 和 strict warning 清零后的交付结论
    }  # 交付门禁计数摘要

    # 返回统一计数结果。
    return dict_totals

# _build_deliverable_checks 生成给人快速扫读的公开八类门禁摘要。
def _build_deliverable_checks(
    dict_context: dict[str, Any],
    dict_totals: dict[str, Any],
    *,
    strict: bool,
    include_testbench: bool,
) -> dict[str, Any]:
    """
    构造最终报告中的 checks 摘要。

    :param dict_context: 子门禁原始结果和诊断字典。
    :param dict_totals: _count_deliverable_totals 返回的计数摘要。
    :param strict: 是否启用 warning 阻断策略。
    :param include_testbench: 是否纳入 testbench 文件。
    :return: JSON 友好的 checks 字段。
    """

    # VG 报告中的 AST summary 是 formatter parser 的结构化证据。
    report_quality = dict_context["quality_report"]  # checks 中 VG 摘要的数据源

    # AST summary 缺失时使用空字典，避免报告组装失败。
    dict_ast_summary = report_quality.ast_report.get("summary", {})  # formatter 解析覆盖摘要

    # 合并诊断只用于分类统计，不改变原始 issues 顺序。
    list_all_issues = [
        *dict_context["quality_issues"],  # VG 深规则诊断
        *dict_context["vg_issues"],  # 固定 VG 门禁诊断
        *dict_context["comment_issues"],  # 注释落点诊断
    ]  # 全部交付诊断

    # AST parse error 是 compile/AST 两个公开门禁共同消费的解析证据。
    int_parse_errors = int(dict_ast_summary.get("parse_errors", 0) or 0)  # formatter AST 解析错误数

    # formatter mismatch 只属于 AST，不进入 compile、profile 或 readability。
    int_formatter_errors = int(dict_ast_summary.get("formatter_errors", 0) or 0)  # formatter 模板错误数

    # 命名门禁从现有 VG/comment/lint 诊断里抽取命名相关问题。
    tuple_naming_counts = _count_matching_issue_severity(  # 命名风险命中后会影响 checks["naming"]
        list_all_issues,  # 在完整诊断流中筛选命名问题
        NAMING_ISSUE_KEYWORDS,  # 用于识别端口、信号、实例等命名文本
    )

    # profile 门禁覆盖风格档和规则源相关诊断。
    tuple_profile_counts = _count_matching_issue_severity(  # 风格风险命中后会影响 checks["profile"]
        list_all_issues,  # 在完整诊断流中筛选 profile 问题
        PROFILE_ISSUE_KEYWORDS,  # 用于识别规则源、wrapper 与 fallback 文本
    )

    # testbench 门禁只在 include_testbench 开启时消费 testbench 路径问题。
    tuple_testbench_counts = _count_testbench_issue_severity(list_all_issues)  # testbench 相关诊断计数

    # checks 字段固定为八类公开门禁，便于 workflow trace 读取。
    dict_checks: dict[str, Any] = {}  # 子门禁摘要字典

    # compile 表示本地静态解析和基础 lint 阻断，不代表仿真器编译。
    dict_checks["compile"] = _check_summary(  # compile 门禁保留本地解析和基础 lint 证据
        errors=int_parse_errors + dict_totals["vg_errors"],  # compile 门禁合并 parser 与 VG 阻断
        warnings=dict_totals["vg_warnings"],  # compile 门禁 strict 下需要清零的 VG warning
        strict=strict,  # compile 门禁沿用最终交付 strict 策略
        files=dict_ast_summary.get("files", 0),  # compile 摘要显示本地静态扫描覆盖范围
        modules=dict_ast_summary.get("modules", 0),  # compile 摘要显示可解析设计单元规模
        parse_errors=int_parse_errors,  # compile 门禁展示 formatter parser 错误数
        source="formatter_ast+verilog_quality_gate",  # compile 门禁证据来源组合
    )

    # ast 摘要定位 formatter AST 是否真正覆盖文件和 module。
    dict_checks["ast"] = _check_summary(  # ast 门禁暴露 formatter parser 的结构覆盖状态
        errors=int_parse_errors + int_formatter_errors,  # ast 门禁合并解析与模板一致性错误
        warnings=0,  # ast 门禁当前没有独立 warning 来源
        strict=strict,  # ast 门禁保持交付 strict 字段一致
        files=dict_ast_summary.get("files", 0),  # AST 覆盖文件数用于定位 parser 是否实际运行
        modules=dict_ast_summary.get("modules", 0),  # AST 模块数用于发现空解析或漏解析
        parse_errors=int_parse_errors,  # ast 门禁展示解析失败总数
        formatter_errors=int_formatter_errors,  # ast 门禁展示模板不一致总数
        source="formatter_ast",  # ast 门禁证据来源
    )

    # readability 复用现有 VG 质量门，不新增第二套可读性规则。
    dict_checks["readability"] = _check_summary(  # readability 门禁复用 VG 规则的可读性结论
        errors=dict_totals["quality_errors"],  # readability 门禁复用 VG error 聚合
        warnings=dict_totals["quality_warnings"],  # strict 交付中必须修复的可读性风险数
        strict=strict,  # readability 门禁继承 strict warning 阻断策略
        source="verilog_quality_gate",  # 可读性结论来自 VG 质量门
        ok=report_quality.ok(),  # readability 门禁保留底层 VG ok 状态
    )

    # comment 聚合 comment placement 诊断和覆盖率指标。
    dict_checks["comment"] = _check_summary(  # comment 门禁保留语义注释位置和覆盖率证据
        errors=dict_totals["comment_errors"],  # comment 门禁的落点 error 数
        warnings=dict_totals["comment_warnings"],  # comment 门禁的语义覆盖 warning 数
        strict=strict,  # comment 门禁沿用交付 strict 策略
        issues=len(dict_context["comment_issues"]),  # comment 门禁保留原始诊断条数
        metrics=dict_context["comment_metrics"],  # comment 门禁保留覆盖率统计
        source="comment_gate",  # comment_gate 专门提供注释落点审计证据
    )

    # naming 只暴露命名类聚合状态，不复制 VG 内部所有规则。
    dict_checks["naming"] = _check_summary(  # naming 门禁只呈现命名相关诊断的聚合结果
        errors=tuple_naming_counts[0],  # naming 门禁的命名类 error 数
        warnings=tuple_naming_counts[1],  # 命名风险中 strict 必须清零的 warning 数
        strict=strict,  # 命名问题同样受 strict warning 策略控制
        source="verilog_quality_gate",  # naming 门禁当前来自 VG 诊断文本
    )

    # profile 记录规则源和 profile 类诊断，便于追踪项目风格合同。
    dict_checks["profile"] = _check_summary(  # profile 门禁保留风格档和规则源追踪证据
        errors=tuple_profile_counts[0],  # profile 门禁的风格合同 error 数
        warnings=tuple_profile_counts[1],  # 风格合同中 strict 必须清零的 warning 数
        strict=strict,  # profile warning 在 strict 交付下不可忽略
        rulebook_path=str(load_verilog_rulebook().path),  # profile 门禁展示规则源路径
        source="style_profile+rulebook",  # 风格证据保留规则版本追踪来源
    )

    # testbench 未请求时保持 not_requested，不虚构 testbench 验证通过。
    dict_checks["testbench"] = _testbench_check_summary(  # testbench 门禁区分未请求和静态失败
        include_testbench=include_testbench,  # testbench 门禁是否被调用方纳入
        errors=tuple_testbench_counts[0],  # testbench 门禁相关 error 数
        warnings=tuple_testbench_counts[1],  # testbench 静态风险中 strict 需要清零的数量
        strict=strict,  # testbench warning 受最终交付 strict 策略控制
    )

    # toolchain 只声明外部工具边界，仿真/综合结果由 validation readiness 汇入。
    dict_checks["toolchain"] = {  # toolchain 门禁在本地交付门禁中只记录边界
        "ready": True,  # 本地交付门禁没有启动外部工具
        "status": "not_requested",  # 外部仿真综合未被本入口请求
        "errors": 0,  # 本入口不制造 toolchain error
        "warnings": 0,  # 未运行外部工具时没有 toolchain 风险计数
        "source": "optional_validation_readiness",  # 外部证据由 validation readiness 汇入
        "boundary": "simulation_execute_synthesis_remote_are_optional_toolchain_checks",  # 明确外部验证边界
    }

    # 返回 checks 字段。
    return dict_checks

# _check_summary 统一构造单个公开门禁的 ready/status 字段。
def _check_summary(errors: int, warnings: int, *, strict: bool, **extra_fields: Any) -> dict[str, Any]:
    """
    构造公开门禁摘要字段。

    :param errors: 当前门禁 error 计数。
    :param warnings: 当前门禁 warning 计数。
    :param strict: 是否启用 warning 阻断策略。
    :param extra_fields: 需要原样透出的补充证据字段。
    :return: 包含 ready、status、errors、warnings 的门禁摘要。
    """

    # strict 模式下 warning 也会阻断当前门禁 ready。
    bool_ready = int(errors) == 0 and (not strict or int(warnings) == 0)  # ready 字段沿用 strict warning 阻断口径

    # status 只表达通过或失败，not_requested 由专门 helper 处理。
    str_status = "passed" if bool_ready else "failed"  # 当前门禁状态文本

    # 先写入稳定字段，再合并补充证据。
    dict_summary: dict[str, Any] = {
        "ready": bool_ready,  # 当前门禁是否满足交付要求
        "status": str_status,  # 当前门禁的人读状态
        "errors": int(errors),  # 保留给 CI 聚合的当前门禁 error 数
        "warnings": int(warnings),  # 保留给 CI 聚合的 strict 风险计数
    }  # 单个门禁摘要

    # 补充字段只来自调用方传入的现有证据。
    dict_summary.update(extra_fields)

    # 返回 JSON 友好的门禁摘要。
    return dict_summary

# _testbench_check_summary 构造 testbench 门禁摘要。
def _testbench_check_summary(
    *,
    include_testbench: bool,
    errors: int,
    warnings: int,
    strict: bool,
) -> dict[str, Any]:
    """
    构造 testbench 公开门禁摘要。

    :param include_testbench: 是否请求纳入 testbench 文件。
    :param errors: testbench 相关 error 计数。
    :param warnings: testbench 相关 warning 计数。
    :param strict: 是否启用 warning 阻断策略。
    :return: testbench 门禁摘要。
    """

    # 未请求 testbench 时不能宣称验证通过，只能声明未请求。
    if not include_testbench:

        # 返回明确的 not_requested 状态。
        return {
            "ready": True,
            "status": "not_requested",
            "errors": 0,
            "warnings": 0,
            "source": "include_testbench=false",
        }

    # 已请求 testbench 时按诊断计数决定 ready。
    return _check_summary(
        errors=errors,
        warnings=warnings,
        strict=strict,
        source="testbench_static_gate",
    )

# _count_matching_issue_severity 统计包含指定关键词的诊断严重级别。
def _count_matching_issue_severity(
    list_issues: list[dict[str, Any]],
    tuple_keywords: tuple[str, ...],
) -> tuple[int, int]:
    """
    按关键词统计诊断中的 error 与 warning 数量。

    :param list_issues: 交付门禁合并后的诊断列表。
    :param tuple_keywords: 用于匹配 code、rule、message 的小写关键词。
    :return: 返回 (errors, warnings) 二元组。
    """

    # 当前公开分类需要纳入 ready 判定的诊断集合。
    list_matched_issues: list[dict[str, Any]] = []  # 关键词命中的诊断列表

    # 逐条匹配 code、rule 和 message，避免依赖单一字段。
    for dict_issue in list_issues:

        # 未命中关键词的诊断不影响当前公开分类。
        if not _issue_matches_keywords(dict_issue, tuple_keywords):

            # 当前诊断不属于该分类，跳过后续计数。
            continue

        # 命中关键词后纳入当前分类计数。
        list_matched_issues.append(dict_issue)

    # 返回命中诊断的严重级别计数。
    return _count_issue_severity(list_matched_issues)

# _count_testbench_issue_severity 统计 testbench 路径相关诊断。
def _count_testbench_issue_severity(list_issues: list[dict[str, Any]]) -> tuple[int, int]:
    """
    统计 testbench 路径相关诊断的 error 与 warning 数量。

    :param list_issues: 交付门禁合并后的诊断列表。
    :return: 返回 (errors, warnings) 二元组。
    """

    # 只影响 testbench 门禁 ready 状态的诊断集合。
    list_testbench_issues: list[dict[str, Any]] = []  # testbench 路径相关诊断列表

    # testbench 相关诊断以 path 字段包含 tb 或 testbench 为主要识别方式。
    for dict_issue in list_issues:

        # 非 testbench 诊断不影响 testbench 门禁。
        if not _issue_is_testbench_related(dict_issue):

            # 当前诊断不来自 testbench 路径，跳过后续计数。
            continue

        # testbench 诊断纳入 testbench 门禁计数。
        list_testbench_issues.append(dict_issue)

    # 返回 testbench 诊断严重级别计数。
    return _count_issue_severity(list_testbench_issues)

# _count_issue_severity 汇总诊断严重级别。
def _count_issue_severity(list_issues: list[dict[str, Any]]) -> tuple[int, int]:
    """
    统计诊断列表里的 error 与 warning 数量。

    :param list_issues: 需要统计的诊断列表。
    :return: 返回 (errors, warnings) 二元组。
    """

    # 当前分类中会直接阻断交付的 error 数。
    int_errors = 0  # 当前公开分类的 error 计数

    # strict 风险桶记录需要交付前清零的 warning 数。
    int_warnings = 0  # 当前公开分类的 strict 风险计数

    # 逐条诊断归入 error 或 warning 计数。
    for dict_issue in list_issues:

        # severity 是公开矩阵 ready 计算的唯一严重级别来源。
        str_severity = str(dict_issue.get("severity")).lower()  # 当前诊断严重级别

        # error 严重级别会直接阻断交付。
        if str_severity == "error":

            # 当前诊断记入 error bucket。
            int_errors += 1  # 当前分类新增一个 error 阻断项

        # warning 在 strict 模式下会阻断交付。
        if str_severity == "warning":

            # 当前诊断记入 strict 风险 bucket。
            int_warnings += 1  # 当前分类新增一个 strict warning 风险项

    # 返回二元组供公开矩阵消费。
    return int_errors, int_warnings

# _issue_matches_keywords 判断诊断是否属于指定聚合分类。
def _issue_matches_keywords(dict_issue: dict[str, Any], tuple_keywords: tuple[str, ...]) -> bool:
    """
    判断诊断文本是否命中关键词集合。

    :param dict_issue: 单条诊断字典。
    :param tuple_keywords: 小写关键词集合。
    :return: 命中任一关键词时返回 True。
    """

    # code 字段优先提供稳定规则编号。
    str_code_text = str(dict_issue.get("code") or "")  # 规则编号匹配文本

    # rule 字段补充来源分类。
    str_rule_text = str(dict_issue.get("rule") or "")  # 规则来源匹配文本

    # message 字段补充自然语言线索。
    str_message_text = str(dict_issue.get("message") or "")  # 诊断消息匹配文本

    # 三类文本合并后进行统一小写匹配。
    str_issue_text = f"{str_code_text} {str_rule_text} {str_message_text}".lower()  # 宽松分类匹配文本

    # 任一关键词命中即归入该分类。
    return any(str_keyword in str_issue_text for str_keyword in tuple_keywords)

# _issue_is_testbench_related 判断诊断是否来自 testbench 文件。
def _issue_is_testbench_related(dict_issue: dict[str, Any]) -> bool:
    """
    判断诊断是否来自 testbench 相关路径。

    :param dict_issue: 单条诊断字典。
    :return: 诊断路径包含 tb 或 testbench 时返回 True。
    """

    # path 字段是 testbench 相关性最稳定的来源。
    str_path = str(dict_issue.get("path") or "").lower()  # 诊断路径文本

    # 同时兼容 tb_ 前缀和 testbench 目录/文件名。
    return "testbench" in str_path or "/tb" in str_path or "\\tb" in str_path or "_tb" in str_path

# _count_delivery_issues_by_rule 生成对上层修复队列友好的规则聚合。
def _count_delivery_issues_by_rule(
    list_issues: list[dict[str, Any]],
    dict_totals: dict[str, Any],
    *,
    strict: bool,
) -> dict[str, int]:
    """
    按规则编号聚合会阻断交付的问题。

    :param list_issues: 交付门禁合并后的诊断列表。
    :param dict_totals: _count_deliverable_totals 返回的计数摘要。
    :param strict: 是否启用 warning 阻断策略。
    :return: 规则编号到阻断计数的映射。
    """

    # dict_counts 保存需要修复的规则编号计数。
    dict_counts: dict[str, int] = {}  # 交付问题按规则聚合的计数字典

    # 逐条诊断判断是否会阻断交付。
    for dict_issue in list_issues:

        # severity 决定当前诊断在 strict 下是否需要修复。
        str_severity = str(dict_issue.get("severity") or "").lower()  # 诊断严重级别

        # 非 strict warning 不进入交付问题聚合。
        if str_severity != "error" and not (strict and str_severity == "warning"):

            # 当前诊断不会阻断本次交付，跳过规则聚合。
            continue

        # code 优先，其次 rule，最后给稳定兜底编号。
        # 规则来源先保留原始对象，便于 None 和非字符串统一收敛。
        object_rule_source = dict_issue.get("code") or dict_issue.get("rule") or "DELIVERABLE_ISSUE"  # 修复队列规则来源

        # 规则编号最终必须是 JSON key 兼容字符串。
        str_rule = str(object_rule_source)  # 修复队列优先展示的稳定规则编号

        # 累加当前规则的阻断次数。
        dict_counts[str_rule] = dict_counts.get(str_rule, 0) + 1  # 当前规则的待修复命中数

    # formatter mismatch 没有伪装成 VG 诊断，使用稳定非 VG 键单独聚合。
    if dict_totals.get("formatter_errors", 0) > 0:

        # 计数与 AST summary 中的 formatter violation 数量保持一致。
        dict_counts["FORMATTER_TEMPLATE_MISMATCH"] = int(dict_totals["formatter_errors"])  # formatter 模板阻断数

    # 如果某些子门禁只给聚合计数而没有细粒度诊断，保留兜底可追踪项。
    if not dict_counts and (dict_totals["errors"] > 0 or (strict and dict_totals["strict_warnings"] > 0)):

        # 兜底计数等于最终阻断合计。
        # 聚合阻断总量用于缺失细粒度 issue 的兜底报告。
        int_blocked_issue_count = dict_totals["errors"] + dict_totals["strict_warnings"]  # 聚合阻断总量

        # 兜底 key 保证修复队列仍能定位阻断规模。
        dict_counts["DELIVERABLE_ISSUE"] = int_blocked_issue_count  # 缺少细粒度诊断时保留阻断数量

    # 返回规则聚合。
    return dict_counts

# _build_deliverable_report 汇总最终 JSON 报告字段。
def _build_deliverable_report(
    path_root: Path,
    strict: bool,
    dict_context: dict[str, Any],
    dict_totals: dict[str, Any],
    dict_checks: dict[str, Any],
) -> dict[str, Any]:
    """
    组装最终交付门禁报告。

    :param path_root: 已规范化的交付入口路径。
    :param strict: 是否启用交付级严格模式。
    :param dict_context: 子门禁原始结果和诊断字典。
    :param dict_totals: 交付状态计数摘要。
    :param dict_checks: 子门禁摘要字段。
    :return: JSON 友好的最终报告字典。
    """

    # 诊断顺序保持 VG、lint、comment gate，方便从根因到补充证据阅读。
    list_issues = [
        *dict_context["quality_issues"],  # VG 深规则诊断排在最前
        *dict_context["vg_issues"],  # VG 结构风险作为补充证据
        *dict_context["comment_issues"],  # 注释位置诊断放在最后
    ]  # 最终报告诊断集合

    # repair_required 与 rerun_required_after_repair 统一绑定最终交付状态。
    bool_repair_required = not dict_totals["delivery_ready"]  # 是否需要修复后才能交付

    # delivery_issues_by_rule 只聚合 strict 下会阻断交付的问题。
    dict_delivery_issues_by_rule = _count_delivery_issues_by_rule(  # 规则级修复队列摘要
        list_issues,  # 合并后的诊断列表
        dict_totals,  # 最终交付计数
        strict=strict,  # 是否把 warning 计入规则级修复队列
    )

    # 报告骨架先写入摘要字段，随后补充子门禁详情。
    dict_report: dict[str, Any] = {
        "version": 2,  # 报告结构版本
        "root": str(path_root),  # 被检查的 Verilog 入口
        "strict": strict,  # 是否启用 strict 交付策略
        "delivery_ready": dict_totals["delivery_ready"],  # strict 交付布尔结论
        "repair_required": bool_repair_required,  # 不可交付时必须进入修复
        "rerun_required_after_repair": bool_repair_required,  # 修复后必须重跑门禁
        "delivery_issues_by_rule": dict_delivery_issues_by_rule,  # 规则级修复聚合
        "errors": dict_totals["errors"],  # 最终阻断问题合计
        "strict_warnings": dict_totals["strict_warnings"],  # strict 模式警告合计
        "checks": dict_checks,  # workflow trace 读取的子门禁摘要
        "issues": list_issues,  # VG/lint/comment 合并诊断
    }

    # quality_gate 详情保留完整 AST 与 VG 规则命中。
    dict_report["quality_gate"] = dict_context["quality_report"].to_dict()  # 完整 VG 报告

    # VG 门禁摘要和逐规则结果作为 RTL 规则判断的权威来源。
    dict_report["vg_catalog_version"] = dict_context["vg_report"]["vg_catalog_version"]  # VG 目录版本

    # 状态摘要用于快速核对激活、预留与执行状态数量。
    dict_report["vg_rule_summary"] = dict_context["vg_report"]["vg_rule_summary"]  # VG 状态摘要

    # 逐规则结果保留全部 72 个固定编号及其证据。
    dict_report["vg_rule_results"] = dict_context["vg_report"]["vg_rule_results"]  # 72 条逐门禁结果

    # comment_gate 详情保留位置诊断和覆盖指标。
    dict_report["comment_gate"] = {
        "issues": dict_context["comment_issues"],  # 注释落点诊断列表
        "metrics": dict_context["comment_metrics"],  # 注释覆盖率统计
    }

    # 返回完整报告。
    return dict_report

# 报告写出函数只处理文件 IO，不改变门禁结果。
def write_verilog_deliverable_gate_report(
    report: dict[str, Any],
    *,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> None:
    """写出交付门禁报告。

    参数:
        report: run_verilog_deliverable_gate 返回的报告字典。
        json_path: 可选 JSON 输出路径。
        markdown_path: 可选 Markdown 输出路径。
    返回:
        本函数只写文件，不返回业务值。
    """

    # JSON 报告用于 CI 或自动化流程消费。
    if json_path is not None:

        # JSON 输出目录必须先创建，避免报告写出失败。
        json_path.parent.mkdir(parents=True, exist_ok=True)

        # 写出保留中文的 JSON 文本。
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Markdown 报告用于人工审查。
    if markdown_path is not None:

        # Markdown 输出目录独立创建，兼容只写人工报告的调用。
        markdown_path.parent.mkdir(parents=True, exist_ok=True)

        # 写出 Markdown 摘要和诊断表。
        markdown_path.write_text(_deliverable_report_to_markdown(report), encoding="utf-8")

# VG 结果转换器只展开激活门禁的非通过状态。
def _vg_results_to_issues(list_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把逐门禁结果转换为交付报告统一 issue。

    参数:
        list_results: run_verilog_quality_gate 返回的 72 条逐门禁结果。

    返回:
        激活门禁中所有非通过结果对应的 issue 字典列表。
    """

    # 结果列表保留 catalog 顺序，便于报告稳定比较。
    list_issues: list[dict[str, Any]] = []  # VG 非通过状态转换后的交付问题

    # reserved 与 passed 不进入交付问题集合。
    for dict_result in list_results:

        # 只展开会参与交付判断的激活非通过门禁。
        if dict_result["catalog_status"] != "active" or dict_result["status"] == "passed":

            # 当前结果无需报告为问题，继续处理下一条。
            continue

        # finding 为空时仍要保留 fail-closed 状态的通用问题。
        list_findings = list(dict_result.get("findings") or [])  # 当前 VG 门禁的定位证据

        # 没有具体定位时使用门禁级消息承载不确定或执行错误。
        if not list_findings:

            # 通用 finding 保留状态信息，避免无定位结果静默消失。
            list_findings = [
                {
                    "path": None,  # 无定位结果时明确表示没有可靠文件路径
                    "line": 1,  # 无精确定位时使用稳定的一基起始行
                    "message": dict_result.get("message") or f"{dict_result['gate_id']} did not pass.",  # 门禁级失败说明
                    "evidence": dict_result["status"],  # 保留触发 fail-closed 的原始状态
                }
            ]

        # 每条定位证据都转换为独立 issue，便于修复计数和报告展示。
        for dict_finding in list_findings:

            # catalog 等级决定该 finding 在交付报告中的严重度。
            str_severity = "error" if dict_result["level"] == "BLOCKER" else "warning"  # 交付问题严重级别

            # VG 编号直接成为公开 code，不再添加其他前缀。
            dict_issue = {
                "code": dict_result["gate_id"],  # 固定 VG 编号是公开问题码
                "rule": dict_result["gate_id"],  # 规则字段与公开问题码保持一致
                "severity": str_severity,  # catalog 等级映射后的报告严重度
                "message": dict_finding.get("message") or dict_result.get("message") or "VG gate did not pass.",  # 优先使用定位诊断
                "path": dict_finding.get("path"),  # 违规 RTL 的相对路径
                "line": int(dict_finding.get("line") or 1),  # 违规位置的一基行号
                "source": "verilog_quality_gate",  # 诊断来源标识
                "detail": dict_finding.get("evidence") or dict_result["status"],  # 原始证据或 fail-closed 状态
            }  # 当前 finding 对应的统一交付问题

            # 把当前 VG issue 追加到最终问题列表。
            list_issues.append(dict_issue)

    # 返回按 catalog 和 finding 顺序排列的 VG issues。
    return list_issues

# _comment_placement_issue_to_dict 补齐注释位置诊断的交付报告字段。
def _comment_placement_issue_to_dict(issue: dict[str, Any]) -> dict[str, Any]:
    """
    把 comment placement issue 转换为交付门禁 issues 条目。
    
    :param issue: comment placement 诊断字典。
    :return: JSON 友好的诊断字典。
    """

    # dict_issue 复制输入，避免修改 comment_placement 原始对象。
    dict_issue = dict(issue)  # 注释位置诊断副本

    # code 缺失时使用稳定默认值。
    str_code = str(dict_issue.get("code") or "COMMENT_PLACEMENT")  # 注释诊断编号

    # comment gate 编号加前缀后不会和 VG 规则号混淆。
    dict_issue["code"] = f"COMMENT_{str_code}"  # 交付报告中的注释编号

    # rule 字段便于统一过滤。
    dict_issue["rule"] = str(dict_issue.get("rule") or "comment_gate")  # 注释规则来源

    # 缺少 severity 时保守作为 error。
    dict_issue.setdefault("severity", "error")

    # 返回带 comment gate 来源标记的诊断。
    return dict_issue

# _deliverable_report_to_markdown 生成简洁人工报告。
def _deliverable_report_to_markdown(report: dict[str, Any]) -> str:
    """
    把交付门禁报告转换为 Markdown 文本。
    
    :param report: 交付门禁报告字典。
    :return: Markdown 格式报告文本。
    """

    # list_lines 先写标题和摘要。
    list_lines = [  # Markdown 文本缓冲区，按顺序承载标题、摘要和诊断表
        "# Verilog deliverable gate",  # 交付门禁人工报告标题
        "",  # 标题结束后的段落断点
        f"Root: `{report.get('root')}`",  # 被检查的 RTL 入口路径
        f"Delivery ready: `{report.get('delivery_ready')}`",  # 严格交付通过状态
        f"Summary: **{report.get('errors')} error(s)**, **{report.get('strict_warnings')} strict warning(s)**",  # 错误和警告摘要
        "",  # 摘要结束后的表格断点
    ]  # Markdown 输出行集合

    # 无诊断时返回成功摘要。
    if not report.get("issues"):

        # 保留简短通过文案。
        list_lines.append("No deliverable-gate findings.")

        # 返回带末尾换行的 Markdown。
        return "\n".join(list_lines) + "\n"

    # 写入诊断表头的列名。
    list_lines.append("| Severity | Code | Path | Line | Message |")

    # 写入 Markdown 表格分隔行，保证人工报告能正常渲染。
    list_lines.append("|---|---|---|---:|---|")

    # 逐条写入诊断。
    for dict_issue in report.get("issues", []):

        # 诊断消息中的竖线必须转义，避免破坏 Markdown 表格列。
        str_message = str(dict_issue.get("message") or "").replace("|", "\\|")  # 表格安全诊断文本

        # str_line 为空时保持表格单元为空。
        str_line = "" if dict_issue.get("line") is None else str(dict_issue.get("line"))  # 行号文本

        # 追加 Markdown 表格行。
        list_lines.append(
            f"| {dict_issue.get('severity')} | {dict_issue.get('code')} | "
            f"`{dict_issue.get('path') or ''}` | {str_line} | {str_message} |"
        )

    # 返回完整 Markdown 文本。
    return "\n".join(list_lines) + "\n"
