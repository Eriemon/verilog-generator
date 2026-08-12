"""编排 Verilog artifact 的静态、语义和 readiness 验证。"""

# future annotations 避免运行期解析复杂类型提示。
from __future__ import annotations

# dataclass 固定内部验证请求对象，避免主入口参数继续扩张。
from dataclasses import dataclass

# Path 负责 artifact 根目录和相对路径计算。
from pathlib import Path

# Any 兼容 JSON-like 配置和第三方 gate 返回对象。
from typing import Any

# interface_contract 读取 RTL 端口事实。
from scripts.python.workflow.interface_contract import audit_interface

# prompt 模块统一校验注释语言枚举。
from scripts.python.workflow.prompt import require_comment_language

# spec 模块归一化用户规格合同。
from scripts.python.workflow.spec import normalize_spec

# verifier 检查规格接口和 RTL 接口漂移。
from scripts.python.existing_rtl.verifier import plan_contract_interface_issues

# validation helper 模块按职责拆分，validation_impl 只保留主流程编排。
from . import validation_checks as checks

# artifact helper 名继续暴露给历史测试与 readiness wrapper。
from .validation_reports import _is_testbench, _rtl_files
from .validation_reports import _rtl_source_files, _unexpected_artifact_issues
from .validation_reports import (
    build_missing_generated_path_report,
    build_validation_report,
    contract_gate_issues_from_verifier,
)

# runtime hook 名继续暴露给 validation.py facade 与 mock patch。
from .validation_runtime import ReadinessHookSet, run_rtl_readiness_with_hooks
from .validation_runtime import _backend_tools, _run_tool, _select_simulator_backend
from .validation_runtime import _short_output, _simulator_config
from .validation_runtime import _timeout_output, _yosys_quote

# validation_models 定义报告模型和 readiness 公共枚举。
from .validation_models import ERROR_SOURCES, READINESS_LEVELS

# ValidationIssue/ValidationReport 是 validation 对外报告契约。
from .validation_models import ValidationIssue, ValidationReport

# readiness helpers 校验 CLI 和 workflow 传入的阶段值。
from .validation_models import readiness_at_least, require_readiness

# ValidationRequest 聚合 validation_impl 的内部执行选项。
@dataclass(frozen=True)
class ValidationRequest:
    """
    保存一次 validation_impl 执行需要的内部参数。

    :param spec: 原始 Verilog 生成规格，执行前会先归一化。
    :param path: 生成 artifact 根目录。
    :param target: 可选目标后端，保留给 normalize_spec 兼容旧调用方。
    :param run_external: 是否允许 readiness 阶段调用外部工具链。
    :param readiness: 请求的验证深度。
    :param comment_language: 生成 RTL 注释语言策略。
    :param semantic_contract: 可选 reference/eval 合同。
    :param simulator_config: 可选 simulator 配置覆盖。
    :param strict_generated_comments: 为 False 时把现有 RTL 注释问题降级为 warning。
    """

    # spec 是 normalize_spec 的输入合同。
    spec: dict[str, Any]  # 待归一化 Verilog 规格

    # path 指向生成产物根目录。
    path: Path  # 待验证 artifact 根目录

    # target 保留旧 CLI 和测试传入的目标后端。
    target: str | None = None  # 规格归一化目标后端

    # run_external 控制 readiness 是否可以执行本地工具。
    run_external: bool = True  # 外部工具执行开关

    # readiness 描述需要达到的验证阶段。
    readiness: str = "static"  # validation 阶段深度

    # comment_language 决定中文注释 gate 是否启用。
    comment_language: str = "zh"  # RTL 注释语言策略

    # semantic_contract 提供 testbench 和 transcript 的语义合同。
    semantic_contract: dict[str, Any] | None = None  # reference 语义合同

    # simulator_config 允许调用方覆盖仿真器优先级。
    simulator_config: dict[str, Any] | None = None  # simulator 配置覆盖

    # strict_generated_comments 区分新生成 RTL 与 existing RTL 诊断模式。
    strict_generated_comments: bool = True  # 注释 gate 严格模式开关

# validate_generated 是 validation_impl 的内部主入口。
def validate_generated(request: ValidationRequest) -> ValidationReport:
    """
    验证生成的 Verilog artifact 并返回结构化报告。

    :param request: validation facade 装配好的内部请求对象。
    :return: 包含诊断、metrics 和 artifact 根目录的 validation report。
    """

    # normalized_spec 是后续所有 gate 使用的规格合同。
    dict_normalized_spec = normalize_spec(request.spec, target=request.target)  # 归一化后的 Verilog 规格

    # str_readiness 是合法化后的 readiness 请求。
    str_readiness = require_readiness(request.readiness)  # 外部验证准备级别

    # str_comment_language 决定注释语言 gate 的期望。
    str_comment_language = require_comment_language(request.comment_language)  # prompt 枚举校验后的语言值

    # path_root 是本轮 validation 的 artifact 根目录。
    path_root = request.path.resolve()  # 生成产物根目录

    # 缺失目录无法继续扫描，直接返回 spec_issue。
    if not path_root.exists():

        # 返回只包含缺失路径诊断的报告。
        return build_missing_generated_path_report(path_root)

    # list_issues 汇总所有 gate 诊断。
    list_issues: list[ValidationIssue] = []  # validation 诊断集合

    # dict_metrics 汇总质量、注释、语义和 readiness 证据。
    dict_metrics: dict[str, Any] = {}  # validation 结构化度量

    # list_reference_cases 兼容显式 semantic contract 和随包 vector contracts。
    list_reference_cases = (
        checks._reference_case_ids(request.semantic_contract)  # 优先使用 semantic contract 显式声明的 case
        or checks._collect_reference_cases(path_root)  # 否则退回到 artifact 目录里的随包 contracts
    )  # 需要在 testbench 中覆盖的 reference case

    # artifact 基础 gate 先阻断目录合同、RTL 存在性和静态硬错误。
    checks._append_core_generation_gate_results(list_issues, dict_normalized_spec, path_root)

    # formatter AST quality gate 提供结构化 RTL 风格证据。
    checks._append_formatter_ast_quality_result(
        list_issues,
        dict_metrics,
        path_root,
        str_comment_language,
        strict_generated_comments=request.strict_generated_comments,
        spec=dict_normalized_spec,
    )

    # 最终交付门禁聚合 formatter AST、VG、static lint、comment gate 和 rulebook。
    checks._append_deliverable_gate_result(
        list_issues,
        dict_metrics,
        dict_normalized_spec,
        path_root,
        str_comment_language,
        strict_generated_comments=request.strict_generated_comments,
    )

    # interface contract gate 检查规格端口和 RTL 端口漂移。
    checks._extend_issues(
        list_issues,
        contract_gate_issues_from_verifier(
            plan_contract_interface_issues(dict_normalized_spec, audit_interface("rtl", path_root))
        ),
    )

    # 注释覆盖与位置 gate 共用 strict/diagnostic 降级策略。
    checks._append_comment_gate_results(
        list_issues,
        dict_metrics,
        path_root,
        str_comment_language,
        strict_generated_comments=request.strict_generated_comments,
    )

    # comment_gate_mode 记录本次注释 gate 是阻断模式还是诊断模式。
    dict_metrics["comment_gate_mode"] = "strict" if request.strict_generated_comments else "diagnostic"  # 注释门禁执行模式

    # reviewability gate 检查中文审查注释是否存在。
    checks._extend_issues(list_issues, checks._validate_rtl_reviewability(path_root, str_comment_language))

    # style profile gate 检查 erie_strict 附加风格约束。
    checks._extend_issues(list_issues, checks._validate_rtl_style_profile(dict_normalized_spec, path_root))

    # testbench gate 检查 DUT 实例、自检比较和 reference 覆盖。
    list_testbench_issues = checks._validate_rtl_testbench(  # testbench 合同诊断集合
        dict_normalized_spec,  # 当前生成批次归一化后的 spec
        path_root,  # 待校验 artifact 根目录
        list_reference_cases,  # 供 DUT 与 reference 覆盖对照使用的 case id 列表
        request.semantic_contract,  # 可选 semantic contract 原始输入
    )

    # testbench 诊断合入统一 issue 流，防止 fake PASS 进入后续 readiness。
    checks._extend_issues(list_issues, list_testbench_issues)

    # semantic gate 汇总 reference transcript 与 checkpoint 对比证据。
    tuple_semantic = checks._validate_semantic_execution(path_root, request.semantic_contract)  # reference 语义诊断和度量

    # 合并 semantic issues。
    checks._extend_issues(list_issues, tuple_semantic[0])

    # 有 semantic metrics 时写入报告。
    if tuple_semantic[1]:

        # semantic_execution 字段供 workflow diagnosis 使用。
        dict_metrics["semantic_execution"] = tuple_semantic[1]  # reference 语义执行证据

    # placeholder gate 防止临时文本进入最终 RTL。
    checks._extend_issues(list_issues, checks._validate_placeholders(path_root, _rtl_source_files(path_root)))

    # readiness gate 负责外部工具链执行或缺证据阻断。
    tuple_readiness = _run_rtl_readiness(  # readiness 诊断和运行度量
        dict_normalized_spec,  # 把归一化 spec 里的语言/接口约束继续传给 readiness
        path_root,  # readiness 在该目录下扫描 RTL、日志和生成物
        str_readiness,  # 本次 validation 需要达到的 readiness 阶段
        request.run_external,  # 是否允许本地调用外部工具
        request.simulator_config,  # 调用方传入的 simulator 配置覆盖
    )

    # readiness issues 进入最终报告，并保留外部工具链运行证据。
    checks._extend_issues(list_issues, tuple_readiness[0])

    # readiness metrics 包含 tool/run_external/backend 等后续发布审计字段。
    dict_metrics.update(tuple_readiness[1])

    # 返回完整 validation report。
    return build_validation_report(path_root, list_issues, dict_metrics)

# _run_rtl_readiness 保留 validation facade 的外部阶段兼容入口。
def _run_rtl_readiness(
    spec: dict[str, Any],
    root: Path,
    readiness: str,
    run_external: bool,
    simulator_config: dict[str, Any] | None = None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """返回 readiness 阶段诊断和 metrics。

    :param spec: 已归一化的 Verilog 规格。
    :param root: 生成 artifact 根目录。
    :param readiness: 已校验的 readiness 深度。
    :param run_external: 是否允许调用外部工具。
    :param simulator_config: 可选 simulator 配置覆盖。
    :return: readiness 诊断列表和工具链运行 metrics。
    """

    # runtime_hook_set 聚合 readiness helper 需要的兼容 hook。
    runtime_hook_set = ReadinessHookSet(  # 本次 readiness 调用使用的兼容 hook 集合
        rtl_files=_rtl_files,  # 保持 facade 暴露的 RTL 文件扫描入口
        rtl_source_files=_rtl_source_files,  # 保持 facade 暴露的 RTL 源文件过滤入口
        is_testbench=_is_testbench,  # 保持 facade 暴露的 testbench 判定入口

        # 后端选择与命令执行继续沿用 validation facade 暴露的 patch 点。
        select_simulator_backend=_select_simulator_backend,  # 保持 simulator 后端选择 patch 点
        simulator_config_reader=_simulator_config,  # 保持 simulator 配置读取 patch 点
        backend_tools=_backend_tools,  # 保持后端依赖工具列表 patch 点
        run_tool=_run_tool,  # 保持外部工具执行 patch 点
    )

    # 把 hook_set 交给 runtime helper，保持 validation_impl 对外兼容名不变。
    return run_rtl_readiness_with_hooks(
        spec,
        root,
        readiness,
        run_external,
        simulator_config,
        runtime_hook_set,
    )

