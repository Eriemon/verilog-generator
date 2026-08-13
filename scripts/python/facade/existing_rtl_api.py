"""Verilog facade 的 existing-RTL 分析、精修与 verify-repair 入口。"""

# future annotations 避免注解在导入期求值。
from __future__ import annotations

# Path 负责把调用方输入统一转成路径对象。
from pathlib import Path

# Any 用于兼容旧 facade 的自由配置字典。
from typing import Any

# existing RTL runtime 提供分析与规范文本加载能力。
from scripts.python.existing_rtl.existing_rtl import analyze_existing_rtl, load_spec_text

# improvement runtime 负责已有 RTL 精修和语义比较。
from scripts.python.existing_rtl.existing_rtl_improvement import (
    ImproveExistingRtlOptions,
    compare_semantics,
    improve_existing_rtl as improve_existing_rtl_runtime,
)

# verify-repair runtime 负责已有 RTL 的验证修复流程。
from scripts.python.existing_rtl.verify_repair import verify_existing as verify_existing_runtime

# workspace 上下文确保运行期相对路径落在指定 run_dir。
from scripts.python.workflow.workspace import use_workspace_root

# workflow facade 私有辅助函数负责兼容参数归一化。
from .workflow_api import _merged_option_dict, _optional_path, _resolve_external_run

# _resolve_sources 把单文件或多文件 RTL 输入收敛为统一列表。
def _resolve_sources(source: str | Path | list[str | Path]) -> list[Path]:
    """把单路径或多路径 RTL 输入统一收敛成路径列表。

    参数:
        source: 单个 RTL 路径，或由多个 RTL 路径组成的列表。

    返回:
        返回按 Path 归一化后的 RTL 源文件路径列表。
    """

    # 多文件输入直接逐项转成 Path，保留原有顺序。
    if isinstance(source, list):

        # 返回多文件 RTL 的 Path 列表。
        return [Path(item) for item in source]

    # 单文件输入也包装成列表，统一下游接口形状。
    return [Path(source)]

# analyze_existing_verilog 提供已有 RTL 分析的稳定 facade 入口。
def analyze_existing_verilog(
    source: str | Path | list[str | Path],
    *,
    out_dir: str | Path,
    spec_source: str | Path | dict[str, Any] | None = None,
    module_name: str | None = None,
) -> dict[str, Any]:
    """分析已有 Verilog RTL，并把 runtime 结果裁剪成稳定字段。

    参数:
        source: 单个或多个待分析 RTL 源文件路径。
        out_dir: 运行目录，用于承载分析产物。
        spec_source: 可选规范来源，传给 runtime 用于补充分析上下文。
        module_name: 可选顶层模块名；缺省时由 runtime 自行推断。

    返回:
        返回仅包含稳定字段的 existing-RTL 分析结果字典。
    """

    # run_dir 作为分析产物根目录，并驱动 workspace 上下文切换。
    path_run_dir = Path(out_dir)  # existing-RTL 分析运行目录

    # 先确保分析产物目录存在，避免 runtime 因目录缺失失败。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 把调用方输入统一转成 runtime 需要的 Path 列表。
    list_sources = _resolve_sources(source)  # 归一化后的 RTL 源列表

    # 在 run_dir 作为工作根目录的上下文中执行 runtime 分析。
    with use_workspace_root(path_run_dir):

        # 规范文本在进入 runtime 前完成加载，保持旧入口合同。
        str_spec_text = load_spec_text(spec_source)  # 归一化后的规范文本

        # runtime 分析结果仍保留完整字段，供 facade 做稳定裁剪。
        dict_analysis_result = analyze_existing_rtl(  # runtime 返回的完整分析结果字典
            list_sources,  # 待分析 RTL 源列表
            spec_text=str_spec_text,  # 从 spec_source 载入的分析上下文文本
            module_name=module_name,  # 可选顶层模块名
            out_dir=path_run_dir,  # 分析产物输出目录
        )

    # facade 只返回上层已经依赖的稳定字段集合。
    return {
        "status": "analyzed",
        "run_dir": str(path_run_dir),
        "analysis_path": str(dict_analysis_result["analysis_path"]),
        "project_analysis_path": str(dict_analysis_result["project_analysis_path"]),
        "design_explanation_path": str(dict_analysis_result["design_explanation_path"]),
        "analysis": dict_analysis_result["analysis"],
        "project_analysis": dict_analysis_result["project_analysis"],
    }

# improve_existing_verilog 提供已有 RTL 受控精修的稳定 facade 入口。
def improve_existing_verilog(
    source: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """执行已有 RTL 受控精修流程。

    参数:
        source: 待精修 RTL 文件路径。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 runtime 精修流程输出的结构化结果字典。
    """

    # 精修白名单文本先单独命名，避免集合定义行过长。
    str_allowed_keys_text = (
        "out_dir improve_goal analysis_source spec_source "
        "candidate_artifacts_dir baseline_artifacts_dir readiness tb_language"
    )  # 精修入口允许的兼容键文本

    # 再把白名单文本切分成 set，供兼容配置合并使用。
    set_allowed_keys = set(str_allowed_keys_text.split())  # 精修入口允许的兼容配置键集合

    # facade 操作名需要单独传入合并器，便于错误信息定位调用入口。
    str_operation_name = "improve_existing_verilog"  # 当前 facade 配置合并的入口名称

    # 合并 config 与 legacy_options，得到精修入口的统一配置视图。
    dict_options = _merged_option_dict(str_operation_name, config, legacy_options, allowed_keys=set_allowed_keys)  # 归一化后的精修配置字典

    # run_dir 作为精修产物根目录，并驱动 workspace 上下文切换。
    path_run_dir = Path(dict_options["out_dir"])  # 精修流程运行目录

    # 先确保精修产物目录存在，避免 runtime 因目录缺失失败。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # analysis_source 表示这次精修要复用的旧分析结论文件。
    path_analysis_source = _optional_path(dict_options.get("analysis_source"))  # 精修流程回看既有分析的输入文件

    # candidate_artifacts_dir 表示这次精修待比较的新候选工件目录。
    path_candidate_artifacts_dir = _optional_path(dict_options.get("candidate_artifacts_dir"))  # 精修流程比较候选结果的工件目录

    # baseline_artifacts_dir 表示回归比对依赖的基线工件集合目录。
    path_baseline_artifacts_dir = _optional_path(dict_options.get("baseline_artifacts_dir"))  # 精修流程比对基线结果的目录

    # 在 run_dir 作为工作根目录的上下文中执行 runtime 精修。
    with use_workspace_root(path_run_dir):

        # 直接返回 runtime 精修结果，保持上层依赖字段稳定。
        return improve_existing_rtl_runtime(
            Path(source),
            out_dir=path_run_dir,
            improve_goal=str(dict_options.get("improve_goal", "tb_scaffold")),
            options=ImproveExistingRtlOptions(
                analysis_source=path_analysis_source,
                spec_source=dict_options.get("spec_source"),
                candidate_artifacts_dir=path_candidate_artifacts_dir,
                baseline_artifacts_dir=path_baseline_artifacts_dir,
                readiness=str(dict_options.get("readiness", "static")),
                tb_language=str(dict_options.get("tb_language", "verilog")),
            ),
        )

# compare_verilog_semantics 提供参考 RTL 与候选 RTL 的语义对比入口。
def compare_verilog_semantics(
    reference: str | Path,
    candidate: str | Path,
    *,
    out_dir: str | Path,
    run_external: bool = True,
    readiness: str = "static",
    external_target: str = "remote",
) -> dict[str, Any]:
    """比较参考 RTL 与候选 RTL 的语义漂移。

    参数:
        reference: 参考 RTL 文件路径。
        candidate: 候选 RTL 文件路径。
        out_dir: 运行目录，用于承载语义比较产物。
        run_external: 是否允许触发外部验证链。
        readiness: 当前验证准备度等级。
        external_target: 外部验证目标环境标识。

    返回:
        返回 runtime 语义比较流程输出的结构化结果字典。
    """

    # run_dir 作为语义比较产物根目录，并驱动 workspace 上下文切换。
    path_run_dir = Path(out_dir)  # 语义比较运行目录

    # 先确保语义比较产物目录存在。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 语义比较的外部执行开关由 readiness 与目标环境共同裁决。
    bool_run_external = _resolve_external_run(  # 当前语义比较是否执行外部链路
        run_external,  # 调用方请求的外部执行开关
        readiness=readiness,  # 当前语义比较的准备度
        external_target=external_target,  # 语义比较要命中的外部环境
        allow_static_external=True,  # static 准备度下允许远程比较
    )

    # 在 run_dir 作为工作根目录的上下文中执行语义比较。
    with use_workspace_root(path_run_dir):

        # 直接返回 runtime 语义比较结果，保持上层字段合同稳定。
        return compare_semantics(
            Path(reference),
            Path(candidate),
            out_dir=path_run_dir,
            run_external=bool_run_external,
            readiness=readiness,
        )

# verify_existing_verilog 提供已有 RTL 验证修复流程的稳定 facade 入口。
def verify_existing_verilog(
    source: str | Path | list[str | Path],
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """对已有 RTL 执行验证修复流程。

    参数:
        source: 单个或多个待验证 RTL 源文件路径。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 runtime verify-repair 流程输出的结构化结果字典。
    异常:
        ValueError: 缺少显式 automation_mode 时抛出，避免已有 RTL 修复边界被默认值放宽。
    """

    # verify-repair 白名单文本先单独命名，避免集合定义行过长。
    str_allowed_keys_text = (
        "out_dir spec_source module_name testbench_source decision_source "
        "tb_mode tb_language automation_mode readiness run_external external_target"
    )  # verify-repair 入口允许的兼容键文本

    # 这里把键文本切成集合，后面只允许这些字段透传到 verify-repair runtime。
    set_allowed_keys = set(str_allowed_keys_text.split())  # verify-repair 入口允许透传给 runtime 的字段集合

    # 合并 config 与 legacy_options，得到 verify-repair 入口的统一配置视图。
    dict_options = _merged_option_dict("verify_existing_verilog", config, legacy_options, allowed_keys=set_allowed_keys)  # 归一化后的 verify-repair 配置字典

    # automation_mode 是已有资产修复边界，必须由调用方显式声明。
    if not dict_options.get("automation_mode"):

        # 不再回退 guided，避免 facade 静默放宽已有 RTL 修复策略。
        raise ValueError("> ERR: [Python] verify_existing_verilog 需要显式 automation_mode")

    # run_dir 作为验证修复产物根目录，并驱动 workspace 上下文切换。
    path_run_dir = Path(dict_options["out_dir"])  # verify-repair 运行目录

    # 先确保验证修复产物目录存在。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # source 参数可能是单文件也可能是列表，这里先把形状统一下来。
    list_sources = _resolve_sources(source)  # verify-repair 要处理的 RTL 源列表

    # 先把 verify-repair 的外部验证请求归一化成布尔值。
    bool_requested_external = bool(dict_options.get("run_external", True))  # verify-repair 是否收到外部验证请求

    # verify-repair 的外部执行开关由 readiness 与目标环境共同裁决。
    bool_run_external = _resolve_external_run(  # 当前 verify-repair 是否执行外部链路
        bool_requested_external,  # 调用方给出的 verify-repair 外部验证意图
        readiness=str(dict_options.get("readiness", "static")),  # verify-repair 当前准备度
        external_target=str(dict_options.get("external_target", "remote")),  # verify-repair 目标外部环境
        allow_static_external=True,  # static 准备度下允许远程验证
    )

    # testbench_source 表示验证修复流程要复用的激励脚手架来源。
    path_testbench_source = _optional_path(dict_options.get("testbench_source"))  # verify-repair 引用的 testbench 文件路径

    # decision_source 表示验证修复流程要复用的历史决策记录来源。
    path_decision_source = _optional_path(dict_options.get("decision_source"))  # verify-repair 引用的 decision 记录路径

    # 在 run_dir 作为工作根目录的上下文中执行 verify-repair。
    with use_workspace_root(path_run_dir):

        # 直接返回 runtime verify-repair 结果，保持上层字段合同稳定。
        return verify_existing_runtime(
            list_sources,
            out_dir=path_run_dir,
            spec_source=dict_options.get("spec_source"),
            module_name=dict_options.get("module_name"),
            testbench_source=path_testbench_source,
            decision_source=path_decision_source,
            tb_mode=str(dict_options.get("tb_mode", "generate")),
            tb_language=str(dict_options.get("tb_language", "verilog")),
            automation_mode=str(dict_options["automation_mode"]),
            readiness=str(dict_options.get("readiness", "static")),
            run_external=bool_run_external,
        )
