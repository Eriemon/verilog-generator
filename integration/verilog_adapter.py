"""Verilog-only 生成、分析和验证门面。"""

# 延迟解析类型标注，降低导入期耦合。
from __future__ import annotations

# 标准库负责 JSON、路径和输入副本。
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

# 随包配置提供默认流程参数。
from runtime.verilog_generator.config import load_settings, workflow_defaults

# 已有 RTL 分析入口。
from runtime.verilog_generator.existing_rtl import analyze_existing_rtl, load_spec_text

# 最终交付门禁入口。
from runtime.verilog_generator.deliverable_gate import (
    run_verilog_deliverable_gate,
    write_verilog_deliverable_gate_report,
)

# 已有 RTL 精修和语义比较入口。
from runtime.verilog_generator.existing_rtl_refinement import (
    compare_semantics,
    RefineExistingRtlOptions,
    refine_existing_rtl as refine_existing_rtl_runtime,
)

# formatter preserve runner 复用统一编码读取和 profile 工厂。
from runtime.verilog_generator.formatter_ast import read_verilog_source
from runtime.verilog_generator.formatter_config import create_formatter_backend

# 提示词渲染入口。
from runtime.verilog_generator.prompt import render_prompt

# Verilog 质量门入口。
from runtime.verilog_generator.quality_gate import (
    run_verilog_quality_gate,
    write_quality_gate_report,
)

# 需求契约和生成计划工具。
from runtime.verilog_generator.requirements import (
    RequirementConfirmation,
    apply_requirement_defaults,
    build_codegen_plan,
    build_requirements_payload,
    validate_requirement_confirmation,
)

# 规格读写和目标归一化工具。
from runtime.verilog_generator.spec import normalize_spec, read_spec, write_spec

# 生成 RTL 验证入口。
from runtime.verilog_generator.validation import readiness_at_least, validate_generated

# 已有 RTL 验证修复入口。
from runtime.verilog_generator.verify_repair import verify_existing as verify_existing_runtime

# 主流程入口。
from runtime.verilog_generator.workflow import run_workflow

# 只读路由入口。
from runtime.verilog_generator.workflow_router import route_verilog_entry

# 工作区上下文限定相对产物目录。
from runtime.verilog_generator.workspace import use_workspace_root

# 仓库根路径用于定位 tests/cases 与默认 runs 合同。
REPO_ROOT = Path(__file__).resolve().parents[3]  # 当前 skill 仓库根目录

# 返回 representative-10 默认坏例集合。
def _representative_bad_case_ids() -> tuple[str, ...]:
    """返回 representative corpus 固定坏例的 case_id 列表。

    参数:
        无。

    返回:
        5 个 bad 样例的稳定 case_id 元组。
    """

    # 返回计划中固定的 5 个 bad 样例标识。
    return (
        "missing_reset_fail",
        "bad_instance_shape_fail",
        "always_split_case_fail",
        "complex_case_slice_fail",
        "part_select_split_fail",
    )

# 返回 representative-10 默认理想样例集合。
def _representative_ideal_case_ids() -> tuple[str, ...]:
    """返回 representative corpus 固定理想样例的 case_id 列表。

    参数:
        无。

    返回:
        5 个 ideal 样例的稳定 case_id 元组。
    """

    # 这些理想样例覆盖同步复位、控制接口和 AXIS 包装等主路径。
    return (
        "Sync_Reset_Interface",
        "Core_Tim_Ctrl_Interface",
        "Core_Ptp_Ctrl_Interface",
        "AXIS_ClockConverter_Interface",
        "AXIS_Buffer_Interface",
    )

# 生成单个 representative case 的稳定元信息。
def _representative_case_spec(case_id: str, cohort: str, path_source: Path) -> dict[str, Any]:
    """返回单个 representative case 的元信息字典。

    参数:
        case_id: 当前样例的稳定标识。
        cohort: 样例所属的 bad 或 ideal 分组。
        path_source: 仓库内原始样例文件路径。

    返回:
        供 runner 和 summary 复用的 case 元信息字典。
    """

    # 返回单个 case 的固定目录与分组信息。
    return {
        "case_id": case_id,
        "cohort": cohort,
        "source": path_source,
    }

# 汇总代表性 corpus 的所有 case 元信息。
def _representative_case_catalog() -> dict[str, dict[str, Any]]:
    """构建 representative corpus 的 case_id 到元信息映射。

    参数:
        无。

    返回:
        所有默认 bad/ideal 样例的稳定目录清单。
    """

    # dict_case_catalog 保存 case_id 到元信息的唯一映射。
    dict_case_catalog: dict[str, dict[str, Any]] = {}  # representative case 元信息目录

    # 坏例固定来自 bad/rtl/fixtures 目录。
    for case_id in _representative_bad_case_ids():

        # path_bad_case_source 保留当前坏例在 tests/cases/bad 下的真实 fixture 路径。
        path_bad_case_source = REPO_ROOT / "tests" / "cases" / "bad" / "rtl" / "fixtures" / f"{case_id}.v"  # 当前 bad 样例的源文件路径

        # 当前坏例目录项固定记录 case_id、cohort 和 fixture 路径。
        dict_case_catalog[case_id] = _representative_case_spec(case_id, "bad", path_bad_case_source)  # 当前 bad 样例的目录项

    # 理想样例固定来自 ideal/rtl 目录。
    for case_id in _representative_ideal_case_ids():

        # 当前 ideal 样例直接引用 ideal corpus 中未经治理的参考 RTL。
        path_ideal_case_source = REPO_ROOT / "tests" / "cases" / "ideal" / "rtl" / f"{case_id}.v"  # ideal corpus 的参考 RTL 文件

        # representative corpus 目录项在这里登记只读参考条目，不附带坏例诊断语义。
        dict_case_catalog[case_id] = _representative_case_spec(case_id, "ideal", path_ideal_case_source)  # ideal 样例的参考目录项

    # 返回默认 representative corpus 的完整目录。
    return dict_case_catalog

# 返回 representative-10 的固定执行顺序。
def _representative_case_order() -> tuple[str, ...]:
    """返回 representative corpus 的默认执行顺序。

    参数:
        无。

    返回:
        先 bad 后 ideal 的 stable case_id 元组。
    """

    # 默认顺序固定为 5 个 bad 后接 5 个 ideal。
    return (*_representative_bad_case_ids(), *_representative_ideal_case_ids())

# representative-10 坏例固定来自 bad/rtl/fixtures。
REPRESENTATIVE_BAD_CASE_IDS = _representative_bad_case_ids()  # 默认 representative corpus 的坏例集合

# representative-10 理想样例固定来自 ideal/rtl。
REPRESENTATIVE_IDEAL_CASE_IDS = _representative_ideal_case_ids()  # 默认 representative corpus 的理想样例集合

# 默认 representative-10 输出目录合同固定到仓库 runs 下。
DEFAULT_CASE_RUN_DIR = Path("runs") / "representative-10"  # run-cases 缺省输出目录

# JSON 风格输入既可以是路径，也可以是内存态字典。
JsonSource = str | Path | dict[str, Any]  # 支持路径或内存态字典的 JSON 风格输入

# RTL、日志和波形等输入既支持单路径，也支持路径列表。
PathCollectionSource = str | Path | list[str | Path]  # 支持单路径或路径列表的工件输入

# 公开符号保持稳定，供 smoke、tests 和 CLI 复用。
__all__ = [  # 限定星号导入只暴露公共 facade，避免宿主误用运行时细节
    "run_verilog_workflow",  # 单次 RTL 生成工作流入口
    "run_verilog_batch",  # 批量 RTL 生成工作流入口
    "render_verilog_prompt",  # 单独生成模型提示词的门面
    "validate_verilog_artifacts",  # 对生成工件运行验证闭环的门面
    "check_verilog_deliverable",  # 暴露最终交付门禁的门面
    "check_verilog_quality",  # 暴露 formatter-AST 质量门的门面
    "analyze_existing_verilog",  # 读取既有 RTL 并生成分析报告的门面
    "refine_existing_verilog",  # 生成受控修改辅助产物的门面
    "compare_verilog_semantics",  # 比较参考与候选 RTL 语义的门面
    "verify_existing_verilog",  # 驱动既有 RTL verify-repair 的门面
    "run_verilog_cases",  # 运行 representative corpus 并写出治理工件的门面
    "route_verilog_request",  # 请求路由分类入口
    "load_default_workflow_config",  # 默认流程配置读取入口
    "load_workflow_result",  # 运行结果读取入口
]

# representative case 目录清单在导入期固定，避免运行时每次重新拼目录合同。
REPRESENTATIVE_CASE_CATALOG = _representative_case_catalog()  # case_id 到样例元信息的稳定映射

# 默认 representative-10 顺序保持 5 个 bad 后接 5 个 ideal。
REPRESENTATIVE_CASE_ORDER = _representative_case_order()  # run-cases 缺省执行顺序

# 读取默认流程配置。
def load_default_workflow_config() -> dict[str, Any]:
    """
    读取随包默认流程配置。

    :return: 默认流程参数字典。
    """

    # 返回可被调用方覆盖合并的默认配置。
    return workflow_defaults(load_settings())

# 读取已有流程结果文件。
def load_workflow_result(run_dir: str | Path) -> dict[str, Any]:
    """
    读取运行目录中的 `workflow_result.json`。

    :param run_dir: 流程运行目录。
    :return: 反序列化后的流程结果。
    """

    # 锁定约定输出文件，避免调用方拼接内部名。
    path_result = Path(run_dir) / "workflow_result.json"  # workflow_result.json 绝对或相对路径

    # 返回结果文件中的稳定 JSON 内容。
    return json.loads(path_result.read_text(encoding="utf-8"))

# 从调用参数和默认配置中抽取单个控制项。
def _option_value(
    dict_options: dict[str, Any],
    option_key: str,
    dict_defaults: dict[str, Any],
    default_value: Any,
) -> Any:
    """按“显式参数优先、配置默认值次之、硬编码缺省兜底”的顺序解析控制项。

    参数:
        dict_options: 调用方显式传入的控制参数字典。
        option_key: 当前要解析的参数名。
        dict_defaults: 已合并好的 workflow 默认配置字典。
        default_value: 默认配置缺失时采用的兜底值。

    返回:
        当前控制项的最终取值。
    """

    # 显式传入的参数优先级最高，哪怕值本身是假值也保留。
    if dict_options.get(option_key) is not None:

        # 返回调用方显式提供的配置值。
        return dict_options[option_key]

    # 否则回落到 workflow 默认配置或硬编码兜底值。
    return dict_defaults.get(option_key, default_value)

# 把可选路径参数转成 Path，同时保留 None 语义。
def _optional_path(value: str | Path | None) -> Path | None:
    """把可选路径参数转成 `Path`，并保留未提供时的空值语义。

    参数:
        value: 可选路径参数。

    返回:
        提供路径时返回 `Path`；未提供时返回 `None`。
    """

    # None 表示调用方没有提供该路径，后续流程应继续走默认分支。
    if value is None:

        # 保留空值，避免制造不存在的磁盘路径对象。
        return None

    # 其余情况统一包成 Path，供 runtime 和文件写入逻辑复用。
    return Path(value)

# 合并配置字典和旧关键字参数。
def _merged_option_dict(
    option_name: str,
    config: Any | None,
    dict_legacy_options: dict[str, Any],
    *,
    allowed_keys: set[str],
) -> dict[str, Any]:
    """合并配置字典和旧关键字参数，并拒绝未知字段。

    参数:
        option_name: 当前门面或 helper 的名称。
        config: 新式配置字典。
        dict_legacy_options: 旧式关键字参数字典。
        allowed_keys: 允许透传的旧关键字字段集合。

    返回:
        已合并且通过字段校验的选项字典。

    异常:
        TypeError: 当调用方传入未知关键字参数时抛出。
    """

    # 新式入口默认接收字典；兼容对象时只读取其实例字段副本。
    dict_options = {} if config is None else config.copy() if isinstance(config, dict) else vars(config).copy()  # 配置对象展开后的基础字段

    # 未声明的旧关键字参数必须显式拒绝，避免悄悄吞掉拼写错误。
    set_unknown_keys = set(dict_legacy_options) - allowed_keys  # 当前调用里不受支持的关键字集合

    # 一旦出现未知关键字，就立即给出明确错误。
    if set_unknown_keys:

        # 把未知关键字排序后拼进错误消息，方便调用方一次修正。
        str_unknown_keys = ", ".join(sorted(set_unknown_keys))  # 未知关键字名称列表

        # 拒绝拼写错误或越界字段，避免 facade 静默吞参。
        raise TypeError("> ERR: [Python] Unexpected keyword arguments: " f"{option_name} -> {str_unknown_keys}.")

    # 旧关键字参数优先级高于配置对象，兼容原有调用方式。
    dict_options.update(dict_legacy_options)

    # 返回已完成覆盖合并的选项字典。
    return dict_options

# 分类用户请求应进入哪个 Verilog 工作流入口。
def route_verilog_request(
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    分类 Verilog 请求的安全入口，不执行生成或验证。

    :param config: 新式路由配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: 推荐入口、缺失输入、风险和下一步。
    :raises TypeError: 传入未知关键字参数时抛出。
    """

    # 先列出该入口允许兼容的旧关键字，避免把字段集合内联到合并调用里。
    set_allowed_keys = {
        "request_summary",  # 路由阶段读取用户需求摘要
        "spec",  # 路由阶段读取规格输入
        "codegen_plan",  # 路由阶段读取已有代码计划
        "rtl",  # 路由阶段读取已有 RTL 输入
        "testbench",  # 路由阶段读取 testbench 输入
        "logs",  # 路由阶段读取日志证据
        "waveform",  # 路由阶段读取波形证据
        "validation",  # 路由阶段读取验证报告
        "artifact_dir",  # 路由阶段读取已有工件目录
        "remote_validation_requested",  # 路由阶段读取远程验证意图
    }  # route 分类阶段允许的旧式关键字集合

    # 兼容旧关键字调用，同时把入口压缩成单一配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # 路由阶段统一读取的输入字典
        "route_verilog_request",  # 标识当前路由入口
        config,  # 新式配置对象
        legacy_options,  # 兼容旧关键字输入
        allowed_keys=set_allowed_keys,  # 路由入口允许的关键字集合
    )

    # 返回只读路由判断，分类阶段不产生文件副作用。
    return route_verilog_entry(
        # 用户请求和规格类输入决定生成入口优先级。
        request_summary=str(dict_options.get("request_summary", "")),  # 用户请求摘要
        spec=dict_options.get("spec"),  # 可选规格来源
        codegen_plan=dict_options.get("codegen_plan"),  # 可选代码生成计划来源

        # 已有工件和诊断证据决定分析、比较或修复入口。
        rtl=dict_options.get("rtl"),  # 可选 RTL 源文件集合
        testbench=dict_options.get("testbench"),  # 可选 testbench 文件集合
        logs=dict_options.get("logs"),  # 可选日志证据集合
        waveform=dict_options.get("waveform"),  # 可选波形证据集合

        # 验证报告、工件目录和远程意图补齐路由风险判断。
        validation=dict_options.get("validation"),  # 可选验证报告来源
        artifact_dir=dict_options.get("artifact_dir"),  # 可选生成工件目录
        remote_validation_requested=bool(dict_options.get("remote_validation_requested", False)),  # 是否显式请求远程验证
    )

# 分析已有 RTL，并把 runtime 结果裁剪成门面稳定字段。
def analyze_existing_verilog(
    source: str | Path | list[str | Path],
    *,
    out_dir: str | Path,
    spec_source: str | Path | dict[str, Any] | None = None,
    module_name: str | None = None,
) -> dict[str, Any]:
    """
    分析已有 Verilog RTL。

    :param source: RTL 路径或列表。
    :param out_dir: 分析输出目录。
    :param spec_source: 可选规格来源。
    :param module_name: 可选顶层模块名。
    :return: 分析产物路径和分析结果。
    """

    # 归一化分析目录，所有产物写入这里。
    path_run_dir = Path(out_dir)  # existing RTL 分析运行目录

    # 确保分析目录存在，避免报告写入失败。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 统一单文件和多文件输入。
    list_sources = _resolve_sources(source)  # 待分析 RTL 路径列表

    # 在分析目录上下文中执行，稳定相对产物路径。
    with use_workspace_root(path_run_dir):

        # 保留完整分析契约，再挑选稳定字段返回。
        str_spec_text = load_spec_text(spec_source)  # 分析阶段可选规格文本

        # 调用 runtime 分析入口，并保留完整分析结果对象。
        dict_analysis_result = analyze_existing_rtl(  # existing RTL 分析阶段返回的完整结果
            list_sources,  # 已归一化的待分析 RTL 文件集合
            spec_text=str_spec_text,  # 分析阶段使用的规格文本
            module_name=module_name,  # 可选顶层模块名
            out_dir=path_run_dir,  # runtime 分析输出目录
        )

    # 返回宿主依赖的稳定字段。
    return {
        "status": "analyzed",
        "run_dir": str(path_run_dir),
        "analysis_path": str(dict_analysis_result["analysis_path"]),
        "project_analysis_path": str(dict_analysis_result["project_analysis_path"]),
        "design_explanation_path": str(dict_analysis_result["design_explanation_path"]),
        "analysis": dict_analysis_result["analysis"],
        "project_analysis": dict_analysis_result["project_analysis"],
    }

# 对已有 RTL 执行受控精修。
def refine_existing_verilog(
    source: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    执行已有 RTL 受控精修流程。

    :param source: 待精修 RTL 路径。
    :param config: 新式精修配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: 精修结果字典。
    :raises TypeError: 传入未知关键字参数时抛出。
    """

    # 精修入口允许兼容的旧关键字单独列出，便于后续扩展与校验。
    set_allowed_keys = {
        "out_dir",  # 精修阶段读取输出目录
        "refine_goal",  # 精修阶段读取目标类型
        "analysis_source",  # 精修阶段读取已有分析报告
        "spec_source",  # 精修阶段读取规格来源
        "candidate_artifacts_dir",  # 精修阶段读取候选工件目录
        "reference_artifacts_dir",  # 精修阶段读取参考工件目录
        "readiness",  # 精修阶段读取验证准备级别
        "tb_language",  # 精修阶段读取 testbench 语言
    }  # refine 门面兼容的旧式关键字集合

    # 兼容旧关键字调用，同时把精修参数收敛到单一配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # 精修流程统一读取的控制参数
        "refine_existing_verilog",  # 标识当前精修入口
        config,  # 新式精修配置对象
        legacy_options,  # 兼容旧版 refine 调用的关键字输入
        allowed_keys=set_allowed_keys,  # 精修入口允许的关键字集合
    )

    # 归一化精修目录，集中存放产物和报告。
    path_run_dir = Path(dict_options["out_dir"])  # refinement 运行目录

    # 创建精修输出目录，避免报告写入失败。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 先读取原始分析报告来源，便于后续统一转成 Path。
    raw_analysis_source = dict_options.get("analysis_source")  # 调用方给出的已有分析报告来源

    # 将可选分析报告转成 Path，缺省时保持为空。
    path_analysis = Path(raw_analysis_source) if raw_analysis_source is not None else None  # 已归一化的分析报告路径

    # 候选工件目录只在调用方提供时转为 Path。
    path_candidate_artifacts_dir = (  # merge/optimize 辅助模式读取的候选工件目录
        Path(dict_options["candidate_artifacts_dir"])  # 候选实现产物所在目录
        if dict_options.get("candidate_artifacts_dir") is not None  # 调用方是否提供候选工件目录
        else None  # 未提供时不启用候选工件目录约束
    )

    # 参考工件目录用于和候选工件做结构或语义对齐。
    path_reference_artifacts_dir = (  # merge/optimize 辅助模式读取的 baseline 工件目录
        Path(dict_options["reference_artifacts_dir"])  # 参考实现产物所在目录
        if dict_options.get("reference_artifacts_dir") is not None  # 调用方是否提供参考实现产物
        else None  # 缺省时跳过参考工件对齐
    )

    # 在精修目录上下文中调用，避免相对路径写回仓库根。
    with use_workspace_root(path_run_dir):

        # 返回精修结果，不改写内部字段。
        return refine_existing_rtl_runtime(
            Path(source),  # refinement 源 RTL 路径
            out_dir=path_run_dir,  # refinement 产物输出目录
            refine_goal=str(dict_options["refine_goal"]),  # 调用方要求的 refinement 目标
            options=RefineExistingRtlOptions(  # runtime refine 入口使用的可选配置对象
                analysis_source=path_analysis,
                spec_source=dict_options.get("spec_source"),
                candidate_artifacts_dir=path_candidate_artifacts_dir,
                reference_artifacts_dir=path_reference_artifacts_dir,
                readiness=str(dict_options.get("readiness", "static")),
                tb_language=str(dict_options.get("tb_language", "verilog")),
            ),
        )

# 比较两个 RTL 的接口和语义。
def compare_verilog_semantics(
    reference: str | Path,
    candidate: str | Path,
    *,
    out_dir: str | Path,
    run_external: bool = True,
    readiness: str = "static",
    external_target: str = "remote",
) -> dict[str, Any]:
    """
    比较参考 RTL 与候选 RTL 的语义漂移。

    :param reference: 参考 RTL 文件路径。
    :param candidate: 候选 RTL 文件路径。
    :param out_dir: 比较报告输出目录。
    :param run_external: 是否允许外部验证参与比较。
    :param readiness: 外部验证准备级别。
    :param external_target: 外部验证目标。
    :return: 语义比较报告。
    """

    # 归一化比较目录，报告写入这里。
    path_run_dir = Path(out_dir)  # 语义比较运行目录

    # 创建比较报告目录，避免写入失败。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 解析比较是否允许外部执行。
    bool_run_external = _resolve_external_run(  # 语义比较外部验证开关
        run_external,  # 调用方请求的外部验证开关
        readiness=readiness,  # 语义比较验证准备级别
        external_target=external_target,  # 语义比较外部验证目标
        allow_static_external=True,  # 允许 static 比较入口显式外部验证
    )

    # 在比较目录上下文中执行，保持报告路径稳定。
    with use_workspace_root(path_run_dir):

        # 返回语义比较结果，不重命名字段。
        return compare_semantics(
            Path(reference),  # 参考 RTL 文件路径
            Path(candidate),  # 候选 RTL 文件路径
            out_dir=path_run_dir,  # 语义比较报告目录
            run_external=bool_run_external,  # 解析后的外部验证开关
            readiness=readiness,  # compare facade 采用的验证深度
        )

# 对已有 RTL 执行验证修复。
def verify_existing_verilog(
    source: str | Path | list[str | Path],
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    对已有 RTL 执行验证修复流程。

    :param source: RTL 路径或列表。
    :param config: 新式 verify-repair 配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: 验证修复结果。
    :raises TypeError: 传入未知关键字参数时抛出。
    """

    # verify-repair 的兼容关键字比单次 workflow 少，单独列出更容易审计。
    set_allowed_keys = {
        "out_dir",  # 修复阶段读取输出目录
        "spec_source",  # 修复阶段读取规格来源
        "module_name",  # 修复阶段读取顶层模块名
        "testbench_source",  # 修复阶段读取 testbench 来源
        "decision_source",  # 修复阶段读取人工决策文件
        "tb_mode",  # 修复阶段读取 testbench 处理模式
        "tb_language",  # 修复阶段读取 testbench 语言
        "automation_mode",  # 修复阶段读取自动化策略
        "readiness",  # 修复阶段读取验证准备级别
        "run_external",  # 修复阶段读取外部验证开关
        "external_target",  # 修复阶段读取外部验证目标
    }  # verify-repair 入口允许透传的旧式兼容字段

    # 兼容旧关键字调用，同时把验证修复控制参数收敛到配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # verify-repair 统一读取的控制参数
        "verify_existing_verilog",  # 标识当前验证修复入口
        config,  # 新式验证修复配置对象
        legacy_options,  # 保留旧版 verify-repair 关键字入口的兼容输入
        allowed_keys=set_allowed_keys,  # 验证修复入口允许的关键字集合
    )

    # 验证修复会把诊断、补丁和报告都收敛到这一处运行目录。
    path_run_dir = Path(dict_options["out_dir"])  # verify-repair 的统一工作目录

    # 先保证输出目录存在，再交给 runtime 写入中间产物。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 把单文件与多文件输入统一成 runtime 可迭代的 RTL 路径序列。
    list_sources = _resolve_sources(source)  # 进入 verify-repair 诊断闭环的 RTL 文件集合

    # readiness 和外部目标会继续收紧 verify-repair 是否真的跑外部验证。
    bool_run_external = _resolve_external_run(  # verify-repair 实际外部验证开关
        bool(dict_options.get("run_external", True)),  # 调用方请求的外部验证意图
        readiness=str(dict_options.get("readiness", "static")),  # 是否达到允许外部执行的验证深度
        external_target=str(dict_options.get("external_target", "remote")),  # verify-repair 外部验证目标
        allow_static_external=True,  # static 模式也允许显式外部验证
    )

    # 先读取原始 testbench 来源，便于后续统一转成 Path。
    raw_testbench_source = dict_options.get("testbench_source")  # 调用方给出的 testbench 来源

    # 仅在调用方显式提供时解析 testbench 路径。
    path_testbench_source = Path(raw_testbench_source) if raw_testbench_source is not None else None  # verify-repair 使用的现有 testbench 路径

    # 先读取原始人工决策来源，便于后续统一转成 Path。
    raw_decision_source = dict_options.get("decision_source")  # 调用方给出的人工决策来源

    # 人工修复决策文件只在显式传入时启用。
    path_decision_source = Path(raw_decision_source) if raw_decision_source is not None else None  # verify-repair 使用的人工决策文件

    # 在修复目录上下文中执行，保持产物路径稳定。
    with use_workspace_root(path_run_dir):

        # 返回修复结果，门面只负责边界控制。
        return verify_existing_runtime(
            list_sources,
            out_dir=path_run_dir,
            spec_source=dict_options.get("spec_source"),
            module_name=dict_options.get("module_name"),

            # testbench 和 decision 输入控制修复时的证据边界。
            testbench_source=path_testbench_source,
            decision_source=path_decision_source,

            # 自动化和验证参数保持原有语义。
            tb_mode=str(dict_options.get("tb_mode", "generate")),
            tb_language=str(dict_options.get("tb_language", "verilog")),
            automation_mode=str(dict_options.get("automation_mode", "guided")),
            readiness=str(dict_options.get("readiness", "static")),
            run_external=bool_run_external,
        )

# 解析 workflow 入口真正要用的运行时参数。
def _resolved_workflow_runtime_options(
    spec: JsonSource | None,
    dict_options: dict[str, Any],
) -> dict[str, Any]:
    """根据默认配置和调用覆盖项生成最终 workflow 运行参数。

    参数:
        spec: 当前 workflow 运行绑定的规格输入，可为空。
        dict_options: 已完成关键字合并后的 workflow 控制参数字典。

    返回:
        供新运行与恢复运行共用的最终 runtime 参数字典。
    """

    # 读取随包默认配置作为基础值。
    dict_defaults = load_default_workflow_config()  # workflow 默认配置

    # workflow_config 允许是路径、字典或空值。
    dict_workflow_payload = _load_optional_json(dict_options.get("workflow_config")) or {}  # workflow 原始覆盖载荷

    # 兼容旧版把 workflow 配置嵌套在 workflow 字段下的结构。
    dict_overrides = _workflow_overrides(dict_workflow_payload)  # 用户提供的 workflow 覆盖项

    # 合并默认配置和用户覆盖项，用户值优先。
    dict_merged = {**dict_defaults, **dict_overrides}  # 合并后的 workflow 配置

    # target 在整个 facade 中固定只能收敛到 rtl。
    str_resolved_target = _resolve_target(dict_options.get("target"), spec, dict_merged)  # 归一化后的 workflow target

    # readiness 始终转成字符串，避免从 JSON 载荷读到非字符串类型。
    str_resolved_readiness = str(_option_value(dict_options, "readiness", dict_merged, "static"))  # workflow 最终采用的验证准备级别

    # 最大尝试次数始终转成整数。
    int_resolved_attempts = int(_option_value(dict_options, "max_attempts", dict_merged, 3))  # workflow 最终允许的最大尝试次数

    # stop_on_human 允许调用参数覆盖配置默认值。
    bool_resolved_stop_on_human = bool(_option_value(dict_options, "stop_on_human", dict_merged, True))  # workflow 人工干预停止策略

    # 这里保留“调用方是否希望启用外部验证”的原始意图，后面再做远程策略裁剪。
    bool_requested_run_external = bool(_option_value(dict_options, "run_external", dict_merged, True))  # workflow 原始外部验证请求

    # 实际是否允许外部验证还要经过 readiness 与 remote-first 策略裁剪。
    str_resolved_external_target = str(dict_options.get("external_target", "remote"))  # workflow 外部验证目标位置

    # 这里把默认配置、readiness 和 external_target 一起裁剪成真正可执行的外部策略。
    bool_resolved_run_external = _resolve_external_run(  # remote-first 裁剪后的 workflow 外部执行许可
        bool_requested_run_external,  # 调用方原始外部验证意图
        readiness=str_resolved_readiness,  # workflow 最终验证准备级别
        external_target=str_resolved_external_target,  # 本次外部验证最终指向的位置
    )

    # 注释语言默认继承工作流配置中的中文约定。
    str_resolved_comment_language = str(_option_value(dict_options, "comment_language", dict_merged, "zh"))  # workflow RTL 注释语言

    # provider 名称默认回落到配置里的 command。
    str_resolved_provider_name = str(_option_value(dict_options, "provider_name", dict_merged, "command"))  # workflow 最终 provider 名称

    # 模型超时统一转成整数秒。
    int_resolved_timeout = int(_option_value(dict_options, "model_timeout_s", dict_merged, 120))  # workflow 模型超时秒数

    # generation_mode 既影响 prompt 细节，也决定 workflow 是否进入 patch/refine 等变体路径。
    str_resolved_generation_mode = str(_option_value(dict_options, "generation_mode", dict_merged, "regular"))  # workflow 最终生成模式

    # stream 显式传入时优先级最高，否则沿用配置默认值。
    bool_resolved_stream = bool(_option_value(dict_options, "stream", dict_merged, False))  # workflow 流式输出开关

    # 先收集决定主流程走向的核心 runtime 字段。
    dict_runtime_values = {  # workflow 运行的核心控制字段
        "target": str_resolved_target,  # 固定成 facade 唯一支持的 rtl 目标
        "readiness": str_resolved_readiness,  # 供 runtime 判定验证阶段深度
        "max_attempts": int_resolved_attempts,  # 限制 workflow 自修复循环次数
        "stop_on_human": bool_resolved_stop_on_human,  # 遇到人工决策节点时是否立即停下
        "run_external": bool_resolved_run_external,  # 是否允许调起本地或远端外部验证
    }

    # 再补 provider、超时和生成模式这类环境与交互字段。
    dict_runtime_values.update({  # workflow 运行的环境与交互字段
        "comment_language": str_resolved_comment_language,  # 生成 RTL 时要求采用的注释语言
        "provider_name": str_resolved_provider_name,  # 选择底层模型或命令 provider 的名称
        "model_timeout_s": int_resolved_timeout,  # 单次模型调用的超时上限秒数
        "generation_mode": str_resolved_generation_mode,  # regular、patch、refine 等生成路径
        "stream": bool_resolved_stream,  # 是否按流式方式接收模型输出
    })

    # helper 负责按固定字段顺序回传稳定 payload，减少主流程里的大块参数表。
    return _workflow_runtime_payload(dict_runtime_values)

# 按固定字段顺序回传 workflow runtime 参数字典。
def _workflow_runtime_payload(dict_runtime_values: dict[str, Any]) -> dict[str, Any]:
    """按固定字段顺序返回 workflow runtime 参数字典。

    参数:
        dict_runtime_values: 已解析完成的 workflow runtime 字段字典。

    返回:
        供新运行与恢复运行共用的稳定 runtime 参数字典。
    """

    # 先放入控制主流程分支的核心字段。
    dict_payload = {  # workflow runtime 的核心分支字段
        "target": dict_runtime_values["target"],  # 固定 payload 的目标字段顺序起点
        "readiness": dict_runtime_values["readiness"],  # 决定静态/语义/外部验证的下探深度
        "max_attempts": dict_runtime_values["max_attempts"],  # 控制 workflow 自修复循环最多重试多少轮
        "stop_on_human": dict_runtime_values["stop_on_human"],  # 人工决策节点处是否立刻暂停自动推进
        "run_external": dict_runtime_values["run_external"],  # 外部校验链路在本轮是否允许触发
    }

    # 再补入 provider、超时和输出形态等环境字段。
    dict_payload.update({  # workflow runtime 的环境与交互字段
        "comment_language": dict_runtime_values["comment_language"],
        "provider_name": dict_runtime_values["provider_name"],
        "model_timeout_s": dict_runtime_values["model_timeout_s"],
        "generation_mode": dict_runtime_values["generation_mode"],
        "stream": dict_runtime_values["stream"],
    })

    # 返回固定字段顺序的 runtime payload，避免宿主看到键位漂移。
    return dict_payload

# 恢复已有 workflow 运行目录。
def _resume_workflow_run(
    dict_options: dict[str, Any],
    dict_runtime_options: dict[str, Any],
) -> dict[str, Any]:
    """恢复已有 workflow 目录，并返回稳定 facade 结果。

    参数:
        dict_options: 已完成关键字合并后的 workflow 控制参数字典。
        dict_runtime_options: 已解析完成的 workflow runtime 参数字典。

    返回:
        宿主稳定依赖的恢复运行结果字典。
    """

    # 恢复模式必须先锁定已有 run 目录。
    path_run_dir = Path(dict_options["resume_dir"])  # workflow 恢复目录

    # 决策文件只在调用方提供时物化到恢复目录。
    path_decision = _materialize_optional_json(  # 恢复阶段可能注入的人工决策文件路径
        dict_options.get("decision"),  # 调用方提供的人工决策载荷
        path_run_dir / "_adapter_inputs" / "decision.json",  # 恢复阶段决策文件落盘位置
    )

    # 在恢复目录上下文中调用，避免相对路径写回仓库根。
    with use_workspace_root(path_run_dir):

        # 恢复流程仍允许调用方覆盖生成模式与流式开关。
        dict_workflow_result = run_workflow(  # workflow 恢复阶段的完整结果对象
            resume_dir=path_run_dir,  # 待恢复的 workflow 目录
            decision_path=path_decision,  # 恢复阶段可选人工决策文件
            generation_mode=dict_options.get("generation_mode"),  # 恢复阶段覆盖的生成模式
            stream=dict_options.get("stream"),  # 恢复阶段覆盖的流式输出开关
            stop_on_human=dict_runtime_options["stop_on_human"],  # 恢复阶段停止策略
            run_external=dict_runtime_options["run_external"],  # 恢复阶段外部验证许可
            comment_language=dict_runtime_options["comment_language"],  # 恢复阶段 RTL 注释语言
            model_timeout_s=dict_runtime_options["model_timeout_s"],  # 恢复阶段模型超时秒数
        )

    # 返回恢复模式下宿主依赖的稳定字段。
    return {
        "status": dict_workflow_result["status"],  # 宿主先用它判断本轮 workflow 最终是否成功
        "run_dir": str(path_run_dir),  # 宿主据此回看整轮运行留下的输入、日志与工件目录
        "result_path": str(path_run_dir / "workflow_result.json"),  # 脚本可直接读取这份 JSON 获取完整细节
        "workflow_result": dict_workflow_result,
    }

# 启动新的 spec-first workflow 运行。
def _start_new_workflow_run(
    spec: JsonSource,
    dict_options: dict[str, Any],
    dict_runtime_options: dict[str, Any],
) -> dict[str, Any]:
    """启动新的 workflow 运行，并返回稳定 facade 结果。

    参数:
        spec: 当前运行的规格输入。
        dict_options: 已完成关键字合并后的 workflow 控制参数字典。
        dict_runtime_options: 已解析完成的 workflow runtime 参数字典。

    返回:
        宿主稳定依赖的新运行结果字典。
    """

    # 新运行模式需要独立输出目录承载所有物化输入和工件。
    path_run_dir = Path(dict_options["out_dir"])  # workflow 新运行目录

    # adapter 输入目录专门保存物化的 JSON 和 spec 文件。
    path_inputs_dir = path_run_dir / "_adapter_inputs"  # facade 输入物化目录

    # 创建输入目录，保证后续 JSON 文件都能安全写入。
    path_inputs_dir.mkdir(parents=True, exist_ok=True)

    # 先把设计需求覆盖项读成字典，避免把 JSON 读取逻辑塞进规格准备调用里。
    dict_design_requirements = _load_optional_json(dict_options.get("design_requirements"))  # 结构化设计需求覆盖字典

    # 接口 profile 同样先解成字典，便于后续复用和检查。
    dict_interface_profile = _load_optional_json(dict_options.get("interface_profile"))  # 接口 profile 覆盖字典

    # 先拆出这次 workflow 要使用的规格覆盖项，避免把所有读取逻辑塞进一次调用里。
    str_workflow_target = dict_runtime_options["target"]  # workflow 归一化后的目标类型

    # 单独缓存流水线要求，避免后续多次从配置字典里重复取值。
    bool_pipeline_required = dict_options.get("pipeline_required")  # workflow 的流水线要求覆盖

    # 流式能力约束会直接影响接口协议描述，先抽成单独变量。
    str_streamability = dict_options.get("streamability")  # workflow 的流式接口要求覆盖

    # 接口家族字段同样先缓存，便于后续复用和注释说明。
    str_interface_family = dict_options.get("interface_family")  # workflow 的接口家族覆盖

    # 准备带需求默认值与确认信息的 RTL 规格。
    dict_prepared_spec = _prepare_facade_spec(spec,  # 已补齐需求默认值并通过确认校验的规格字典
        target=str_workflow_target,  # 用统一目标类型锁定 facade spec 的语义分支
        design_requirements=dict_design_requirements,  # 把显式设计约束并入默认值补齐流程
        pipeline_required=bool_pipeline_required,  # 让默认值逻辑知道是否必须插入流水线
        streamability=str_streamability,  # 把流式接口诉求写入最终 spec
        interface_family=str_interface_family,  # 固定接口家族，避免后续阶段再次猜测
        interface_profile=dict_interface_profile,  # 注入 profile 细节供端口模板展开
    )

    # requirements payload 供 workflow 和人工审阅共用。
    dict_requirements_payload = build_requirements_payload(dict_prepared_spec)  # requirements 契约内容

    # requirements 文件会被 workflow 和测试 fixture 直接引用。
    path_requirements = _write_json_object(path_inputs_dir / "requirements.json", dict_requirements_payload)  # workflow 需求确认文件路径

    # codegen plan 用于约束接口、依赖和检查点展开。
    dict_codegen_plan = build_codegen_plan(dict_prepared_spec)  # workflow 读取的阶段与接口展开计划

    # 把 codegen plan 固化成独立文件，便于复现和审阅。
    path_codegen_plan = _write_json_object(path_inputs_dir / "codegen_plan.json", dict_codegen_plan)  # workflow codegen plan 文件路径

    # 把相对 plan 路径写回 spec，保持 workflow 入口契约一致。
    str_codegen_plan_path = path_codegen_plan.relative_to(path_run_dir).as_posix()  # spec 内引用的相对 codegen plan 路径

    # 把相对 plan 路径写回 spec，避免 workflow 输入中出现绝对路径。
    dict_prepared_spec["codegen_plan_path"] = str_codegen_plan_path  # 把 plan 相对路径回写进 spec 供 workflow 使用

    # 物化 workflow 最终要读取的 spec 文件。
    path_spec = _materialize_spec(dict_prepared_spec,  # workflow spec 输入文件路径
        path_inputs_dir / "spec.json",  # 写入新运行目录的 spec 文件位置
        target=str_workflow_target,  # 物化 spec 时按统一目标类型选择规范化规则
    )

    # evidence 和 decision 都允许路径透传或字典物化。
    path_evidence = _materialize_optional_json(dict_options.get("evidence"),  # 新运行阶段可选证据文件路径
        path_inputs_dir / "evidence.json",  # 新运行目录里的证据物化文件
    )

    # decision 文件只在调用方提供时物化，供人工协同阶段读取。
    path_decision = _materialize_optional_json(dict_options.get("decision"),  # 新运行阶段可选人工决策文件路径
        path_inputs_dir / "decision.json",  # 新运行目录里的决策物化文件
    )

    # 在新目录上下文中启动 workflow，稳定内部相对路径。
    with use_workspace_root(path_run_dir):

        # spec-first 流程在这里真正执行。
        dict_workflow_result = run_workflow(spec_path=path_spec,  # spec-first 新运行阶段的完整结果对象
            target=str_workflow_target,  # 目标类型决定生成/校验分支
            out_dir=path_run_dir,  # 全部运行产物写入当前 run 目录
            decision_path=path_decision,  # 协同确认阶段可回读的决策文件
            evidence_path=path_evidence,  # 校验与修复阶段可引用的证据文件
            provider_name=dict_runtime_options["provider_name"],  # provider 选择影响底层模型接入方式
            provider_command=dict_options.get("provider_command"),  # 调用方可显式覆盖 provider 命令
            generation_mode=dict_runtime_options["generation_mode"],  # 生成模式控制本轮 workflow 的执行策略
            stream=dict_runtime_options["stream"],  # 流式输出开关影响交互反馈方式
            readiness=dict_runtime_options["readiness"],  # readiness 门禁决定是否允许继续推进
            max_attempts=dict_runtime_options["max_attempts"],  # 单轮 workflow 的最大重试次数
            stop_on_human=dict_runtime_options["stop_on_human"],  # 人工介入后是否立即暂停自动流程
            run_external=dict_runtime_options["run_external"],  # 是否在本轮中触发外部校验/执行
            comment_language=dict_runtime_options["comment_language"],  # 生成注释时使用的语言约定
            model_timeout_s=dict_runtime_options["model_timeout_s"],  # 模型调用超时上限，防止长时间挂起
        )

    # helper 负责按固定字段顺序回传新运行结果，减少主流程里的大块结果表。
    return _new_workflow_result_payload(dict_workflow_result, path_run_dir, path_requirements, path_codegen_plan)

# 按固定字段顺序回传新运行 facade 的稳定结果字典。
def _new_workflow_result_payload(
    dict_workflow_result: dict[str, Any],
    path_run_dir: Path,
    path_requirements: Path,
    path_codegen_plan: Path,
) -> dict[str, Any]:
    """按固定字段顺序返回新运行 facade 的稳定结果字典。

    参数:
        dict_workflow_result: run_workflow 返回的完整结果对象。
        path_run_dir: 当前 workflow 新运行目录。
        path_requirements: 物化后的 requirements 文件路径。
        path_codegen_plan: 物化后的 codegen plan 文件路径。

    返回:
        宿主稳定依赖的新运行结果字典。
    """

    # 先写入状态和目录级产物路径，方便宿主快速定位本轮运行。
    dict_result = {  # 新运行 facade 的核心结果字段
        "status": dict_workflow_result["status"],  # workflow 总体状态字符串
        "run_dir": str(path_run_dir),  # 当前新运行目录，供宿主定位全部产物
        "result_path": str(path_run_dir / "workflow_result.json"),  # workflow_result.json 的稳定落盘路径
    }

    # 再补 requirements、plan 和完整 workflow_result 供深入检查使用。
    dict_result.update({  # 新运行 facade 的补充诊断字段
        "requirements_path": str(path_requirements),
        "codegen_plan_path": str(path_codegen_plan),
        "workflow_result": dict_workflow_result,
    })

    # 返回宿主稳定依赖的新运行结果字段集合。
    return dict_result

# 启动或恢复单个 Verilog 生成工作流。
def run_verilog_workflow(
    spec: JsonSource | None = None,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """启动或恢复 Verilog workflow，兼容旧关键字参数调用。

    参数:
        spec: 新运行模式下的规格输入；恢复模式可为空。
        config: 新式 workflow 配置字典。
        legacy_options: 兼容旧接口的关键字输入集合。

    返回:
        新运行或恢复运行的稳定结果字典。

    异常:
        TypeError: 传入未知关键字参数时抛出。
        ValueError: 新运行缺少必要输入时抛出。
    """

    # 运行目录和输入文件字段控制 workflow 的物化边界。
    set_workflow_path_keys = {"out_dir", "resume_dir", "workflow_config", "evidence", "decision"}  # workflow 的运行目录与输入文件关键字

    # provider 相关字段只影响模型调用方式，不改变 spec 语义。
    set_workflow_provider_keys = {"provider_name", "provider_command"}  # workflow 的 provider 选择关键字

    # generation_mode、stream 和 target 决定此次生成请求的基本执行轮廓。
    set_workflow_mode_keys = {"generation_mode", "stream", "target"}  # workflow 的生成模式与目标关键字

    # 规格覆盖项会写回 normalized spec，直接影响接口与实现约束。
    set_workflow_spec_keys = {"design_requirements", "pipeline_required"}  # 先登记需求与流水线这两类核心覆盖

    # 再补会改变端口形态的流式与接口族相关字段。
    set_workflow_spec_keys |= {"streamability", "interface_family", "interface_profile"}  # 把接口流式约束并入同一白名单

    # readiness 与外部执行相关字段控制验证深度和远端执行策略。
    set_workflow_validation_keys = {"readiness", "max_attempts"}  # 先登记门禁深度与单轮重试上限

    # 再补是否触发外部执行以及外部目标选择。
    set_workflow_validation_keys |= {"run_external", "external_target"}  # 把外部执行策略并入验证控制字段

    # 人工停机、注释语言和模型超时属于交互层策略。
    set_workflow_interaction_keys = {"stop_on_human", "comment_language", "model_timeout_s"}  # workflow 的交互策略关键字

    # 先允许目录和 provider 这类运行上下文字段。
    set_allowed_keys = set_workflow_path_keys | set_workflow_provider_keys  # workflow 入口的基础兼容关键字

    # 再并入会下发到 spec 的模式与规格覆盖字段。
    set_allowed_keys = set_allowed_keys | set_workflow_mode_keys | set_workflow_spec_keys  # 扩展到生成模式与规格控制关键字

    # 最后补齐验证链路和交互层策略字段。
    set_allowed_keys = set_allowed_keys | set_workflow_validation_keys | set_workflow_interaction_keys  # 收敛完整 workflow 兼容关键字集合

    # 兼容旧关键字调用，同时把 workflow 控制参数收敛到配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # workflow 入口统一读取的控制参数
        "run_verilog_workflow",  # 当前 workflow facade 的入口名称
        config,  # 调用方提供的新式 workflow 配置对象
        legacy_options,  # 保留旧版 workflow 关键字调用的兼容输入
        allowed_keys=set_allowed_keys,  # 合法的 workflow 兼容关键字集合
    )

    # 新运行和恢复运行共享一套最终解析后的 runtime 参数。
    dict_runtime_options = _resolved_workflow_runtime_options(  # workflow 最终运行参数
        spec,  # 新运行时使用的原始规格输入
        dict_options,  # 已完成合并的 workflow 控制参数
    )

    # 恢复模式优先，只要给出 resume_dir 就不再要求 spec/out_dir。
    if dict_options.get("resume_dir") is not None:

        # 恢复已有运行目录，并复用统一参数解析结果。
        return _resume_workflow_run(dict_options, dict_runtime_options)

    # 新运行缺少 spec 或 out_dir 时必须立即报错。
    if spec is None or dict_options.get("out_dir") is None:

        # 阻止缺少关键输入时启动新的 workflow 运行。
        raise ValueError("> ERR: [Python] New workflow runs require both `spec` and `out_dir`.")

    # 启动新的 spec-first workflow。
    return _start_new_workflow_run(spec, dict_options, dict_runtime_options)

# 批量执行多个 Verilog 工作流。
def run_verilog_batch(
    specs: list[JsonSource],
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """批量运行多个 spec-to-RTL workflow，兼容旧关键字参数调用。

    参数:
        specs: 待执行的规格输入列表。
        config: 新式批量 workflow 配置字典。
        legacy_options: 兼容旧接口的关键字输入集合。

    返回:
        总体状态、批量摘要和 case 摘要列表。

    异常:
        TypeError: 传入未知关键字参数时抛出。
        ValueError: 规格列表为空或缺少 out_dir 时抛出。
    """

    # batch 根目录、共享证据和 provider 相关字段决定整个批量任务的公共上下文。
    set_batch_path_keys = {"out_dir", "workflow_config", "evidence"}  # batch 的公共目录与证据关键字

    # 这组字段专门决定批量任务底层模型或命令的接入方式。
    set_batch_provider_keys = {"provider_name", "provider_command"}  # 指向 batch 底层 provider 选择的关键字集合

    # 生成模式和目标类型会广播到每个 case 的 workflow 初始化阶段。
    set_batch_mode_keys = {"generation_mode", "stream", "target"}  # batch 广播到各 case 的生成边界字段

    # 这些规格覆盖项会传给每个 case 的 spec 归一化流程。
    set_batch_spec_keys = {"design_requirements", "pipeline_required"}  # 先定义 case 共享的需求与流水线覆盖入口

    # 再补会影响端口与握手语义的接口相关字段。
    set_batch_spec_keys |= {"streamability", "interface_family", "interface_profile"}  # 把流式能力和接口 profile 一并广播给各 case

    # 验证深度和外部执行策略决定每个 case 是否继续下探验证链路。
    set_batch_validation_keys = {"readiness", "max_attempts"}  # 先定义批量共享的门禁深度与重试上限

    # 再补是否外跑以及外部目标选择。
    set_batch_validation_keys |= {"run_external", "external_target"}  # 把外部执行策略扩充进 batch 验证字段

    # stop_on_human、注释语言和超时上限统一约束整个批量任务的人机交互节奏。
    set_batch_interaction_keys = {"stop_on_human", "comment_language", "model_timeout_s"}  # batch 共用的交互层策略字段

    # 先允许批量任务自身的目录与 provider 字段。
    set_allowed_keys = set_batch_path_keys | set_batch_provider_keys  # 先收敛 batch 自身目录与 provider 相关字段

    # 再并入会分发给每个 case 的生成与规格字段。
    set_allowed_keys = set_allowed_keys | set_batch_mode_keys | set_batch_spec_keys  # 扩展到 case 级生成模式与规格控制字段

    # 剩余白名单只补 case 级验证控制和人机交互策略。
    set_allowed_keys = set_allowed_keys | set_batch_validation_keys | set_batch_interaction_keys  # 追加 batch 入口收尾所需的验证与交互字段

    # 兼容旧关键字调用，同时把批量共享参数收敛到配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # 批量 workflow 共享控制参数
        "run_verilog_batch",  # 批量入口名，供报错与审计记录使用
        config,  # 优先读取的新式批量配置对象
        legacy_options,  # 从旧版 kwargs 收敛来的兼容输入
        allowed_keys=set_allowed_keys,  # 限定批量 facade 可接受的透传关键字
    )

    # 空批量无法形成 case 摘要。
    if not specs:

        # 阻止创建没有任何 case 的批量输出目录。
        raise ValueError("> ERR: [Python] run_verilog_batch requires at least one spec.")

    # 批量入口必须显式给出根目录，便于 case 子目录稳定落位。
    if dict_options.get("out_dir") is None:

        # 缺少 out_dir 时不能继续创建批量运行结构。
        raise ValueError("> ERR: [Python] run_verilog_batch requires `out_dir`.")

    # 归一化批量根目录。
    path_batch_root = Path(dict_options["out_dir"])  # 批量运行根目录

    # 创建根目录，保证 case 子目录可写。
    path_batch_root.mkdir(parents=True, exist_ok=True)

    # 收集每个 case 的稳定摘要。
    list_case_results: list[dict[str, Any]] = []  # 批量 case 摘要列表

    # 统计通过的 case 数量，用于生成批量总体状态。
    int_passed_cases = 0  # 已通过 case 数量

    # 按输入顺序执行 case。
    for index, spec_item in enumerate(specs, start=1):

        # case id 先由 spec 名称或序号稳定导出。
        str_case_id = _batch_case_id(spec_item, index)  # 批量 case 标识

        # 每个 case 都使用独立目录，避免工件覆盖。
        path_case_run_dir = path_batch_root / f"{index:03d}-{str_case_id}"  # 单个 case 运行目录

        # 复用共享控制参数，只把当前 case 的输出边界收敛到独立目录。
        dict_case_config = {**dict_options, "out_dir": path_case_run_dir, "resume_dir": None}  # 单个 case 的 workflow 配置

        # 执行当前 case workflow，并保留完整结果供后续摘要裁剪。
        dict_case_result = run_verilog_workflow(spec_item, config=dict_case_config)  # 单个 case workflow 结果

        # 批量接口只保留稳定摘要，避免嵌入整个 workflow_result 树。
        dict_case_summary = _batch_case_summary(str_case_id, path_case_run_dir, dict_case_result)  # 单个 case 稳定摘要

        # 按输入顺序保留 case 摘要。
        list_case_results.append(dict_case_summary)

        # 只有显式 passed 才计入通过数。
        if dict_case_summary["status"] == "passed":

            # 累加通过数，最终用于批量总体状态判定。
            int_passed_cases += 1  # 累加通过 case 数量

    # 所有 case 都 passed 时，批量总体才视为 passed。
    str_status = "passed" if int_passed_cases == len(list_case_results) else "failed"  # 批量总体状态

    # 返回批量运行的稳定摘要，保持既有 JSON 字段不变。
    return {
        "status": str_status,
        "run_dir": str(path_batch_root),
        "summary": {
            "case_count": len(list_case_results),
            "passed_cases": int_passed_cases,
            "failed_cases": len(list_case_results) - int_passed_cases,
            "generation_mode": dict_options.get("generation_mode") or "regular",
        },
        "cases": list_case_results,
    }

# 生成当前 case 的独立输出目录路径。
def _representative_case_dir(path_cases_root: Path, dict_case_spec: dict[str, Any]) -> Path:
    """返回当前 representative case 的独立输出目录。

    参数:
        path_cases_root: representative corpus 的 cases 根目录。
        dict_case_spec: 当前 case 的元信息字典。

    返回:
        当前 case 对应的固定输出目录路径。
    """

    # 每个 case 的目录名固定使用稳定 case_id。
    return path_cases_root / str(dict_case_spec["case_id"])

# 构建 representative corpus 的 summary 载荷。
def _representative_summary_payload(
    path_run_dir: Path,
    list_case_results: list[dict[str, Any]],
    int_completed_cases: int,
) -> dict[str, Any]:
    """返回 representative corpus 的稳定摘要字典。

    参数:
        path_run_dir: 当前 representative corpus 的输出根目录。
        list_case_results: 逐例执行后的稳定摘要列表。
        int_completed_cases: 已完成治理合同的 case 数量。

    返回:
        与 `summary.json` 对齐的摘要字典。
    """

    # 全部 case 都完成治理合同后，总体状态才记为 completed。
    str_status = "completed" if int_completed_cases == len(list_case_results) else "failed"  # representative corpus 总体状态

    # 返回固定的 summary JSON 字段集合。
    return {
        "status": str_status,
        "run_dir": str(path_run_dir),
        "case_count": len(list_case_results),
        "completed_cases": int_completed_cases,
        "failed_cases": len(list_case_results) - int_completed_cases,
        "cases": list_case_results,
    }

# 统一合并 run-cases 入口的 config 和兼容 kwargs。
def _case_option_values(
    config: dict[str, Any] | None,
    legacy_options: dict[str, Any],
    set_allowed_keys: set[str],
) -> dict[str, Any]:
    """返回 run-cases 入口归并后的配置字典。

    参数:
        config: 新式 run-cases 配置字典。
        legacy_options: 兼容旧接口的关键字输入集合。
        set_allowed_keys: 允许透传到 run-cases 的关键字白名单。

    返回:
        `_merged_option_dict` 归并后的稳定入口配置字典。
    """

    # str_entry_name 固定标识当前使用的 representative corpus facade。
    str_entry_name = "run_verilog_cases"  # `_merged_option_dict` 使用的 representative corpus facade 名称

    # 返回 run-cases 入口合并后的稳定配置字典。
    return _merged_option_dict(str_entry_name, config, legacy_options, allowed_keys=set_allowed_keys)

# 运行 representative existing-RTL corpus，并按固定合同写出治理结果。
def run_verilog_cases(
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    运行 representative existing-RTL corpus，并写出逐例 governed RTL 与严格报告。

    :param config: 新式 case-runner 配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: corpus 运行摘要字典。
    :raises TypeError: 传入未知关键字参数时抛出。
    :raises ValueError: case 选择非法时抛出。
    """

    # run-cases 入口只接受输出目录和可选 case 选择。
    set_allowed_keys = {"out_dir", "case"}  # representative corpus 入口白名单

    # config 和 legacy kwargs 在这里统一归并。
    dict_options = _case_option_values(config, legacy_options, set_allowed_keys)  # run-cases 合并后的入口配置

    # 缺省输出根目录固定落到仓库 runs/representative-10。
    path_run_dir = Path(dict_options.get("out_dir") or DEFAULT_CASE_RUN_DIR)  # representative corpus 输出根目录

    # 先创建根目录，再写 summary 与逐例工件。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 用户未指定 `--case` 时回退到默认 representative-10。
    list_selected_cases = _selected_representative_cases(dict_options.get("case"))  # 本次需要执行的 representative case 清单

    # list_case_results 保存逐例摘要，供 summary JSON/Markdown 共用。
    list_case_results: list[dict[str, Any]] = []  # representative corpus 逐例摘要列表

    # int_completed_cases 用于计算 summary 总体状态。
    int_completed_cases = 0  # 已完成逐例治理合同的 case 数量

    # 所有逐例结果都固定写到 cases/<case_id>/ 子目录。
    path_cases_root = path_run_dir / "cases"  # representative corpus 的 cases 根目录

    # 按稳定顺序逐例执行治理与诊断流水。
    for dict_case_spec in list_selected_cases:

        # 当前 case 的目录名固定使用 stable case_id。
        path_case_dir = _representative_case_dir(path_cases_root, dict_case_spec)  # 当前 case 的独立输出目录

        # 运行当前 case 的完整治理合同并保留稳定摘要。
        dict_case_result = _run_single_representative_case(dict_case_spec, path_case_dir)  # 当前 case 的稳定摘要结果

        # 按执行顺序追加到 representative corpus 摘要。
        list_case_results.append(dict_case_result)

        # 只有完整跑完治理合同的 case 才计入完成数。
        if dict_case_result["status"] == "completed":

            # 累加完成数，供 summary 总体状态汇总。
            int_completed_cases += 1  # 追加一例已完成治理合同的 case

    # summary JSON 与 Markdown 都复用同一份稳定摘要字典。
    dict_summary = _representative_summary_payload(path_run_dir, list_case_results, int_completed_cases)  # representative corpus 稳定摘要

    # 固定写出机器可读摘要，供 tests 和后续自动化复用。
    _write_json(path_run_dir / "summary.json", dict_summary)

    # Markdown 摘要便于人工快速审阅各 case 状态和 strict 结果。
    (path_run_dir / "summary.md").write_text(_representative_summary_markdown(dict_summary), encoding="utf-8")

    # 返回与 summary JSON 一致的稳定摘要字典。
    return dict_summary

# 渲染单个 Verilog prompt。
def render_verilog_prompt(
    spec: JsonSource,
    out_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    渲染 Verilog 生成 prompt，并写入调用方指定路径。

    :param spec: 规格路径或规格字典。
    :param out_path: prompt 输出文件路径。
    :param config: 新式 prompt 渲染配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: prompt 文件路径和 prompt 文本。

    本 facade 不接收 NumPy/PyTorch 数组；shape、dtype、unit 对这些路径、dict 和标量控制参数不适用。
    """

    # target 和设计需求决定 prompt 要描述的 RTL 任务边界。
    set_prompt_target_keys = {"target", "design_requirements", "pipeline_required"}  # prompt 的目标与需求关键字

    # streamability 和接口族字段会直接影响端口约束描述。
    set_prompt_interface_keys = {"streamability", "interface_family", "interface_profile"}  # prompt 的接口约束关键字

    # 阶段、上下文和证据共同决定模型当前能看到的材料范围。
    set_prompt_context_keys = {"stage", "context_manifest", "context_dir"}  # 先定义阶段与上下文清单定位字段

    # 再补会直接进入提示词材料池的辅助输入。
    set_prompt_context_material_keys = {"evidence", "memory", "decision"}  # 会进入 prompt 材料池的辅助输入字段

    # 把辅助材料字段并入上下文关键字集合。
    set_prompt_context_keys |= set_prompt_context_material_keys  # 把辅助材料加入 prompt 上下文白名单

    # 渲染细节字段只影响提示词文字，不改变规格结构。
    set_prompt_render_keys = {"comment_language", "vector_contract", "subfunction", "budget"}  # prompt 的渲染控制关键字

    # 先允许决定 RTL 任务边界的规格字段。
    set_allowed_keys = set_prompt_target_keys | set_prompt_interface_keys  # prompt 入口的基础规格关键字

    # 第二段白名单专门开放上下文材料与提示词渲染控制。
    set_allowed_keys = set_allowed_keys | set_prompt_context_keys | set_prompt_render_keys  # 追加 prompt 入口剩余的上下文与渲染字段

    # 兼容旧关键字调用，同时把 prompt 渲染参数收敛到配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # prompt 渲染阶段统一读取的控制参数
        "render_verilog_prompt",  # 用于报错与调试定位的 prompt facade 名称
        config,  # 来自调用方的新式 prompt 配置对象
        legacy_options,  # 由旧版 kwargs 汇总出来的兼容输入
        allowed_keys=set_allowed_keys,  # 当前 prompt 入口认可的关键字全集
    )

    # 准备带需求默认值的规格字典，保证 prompt 和 workflow 使用同一套约束。
    dict_resolved_spec = _prepare_facade_spec(  # 作为 render_prompt 输入的已确认 RTL 规格
        # spec 和 target 决定提示词任务所描述的 RTL 对象。
        spec,  # prompt 原始规格输入
        target=_resolve_target(dict_options.get("target"), spec, {}),  # 提示词阶段归一化目标

        # 需求和接口覆盖项会直接影响提示词中的约束段落。
        design_requirements=_load_optional_json(dict_options.get("design_requirements")),  # 提示词设计需求覆盖字典
        pipeline_required=dict_options.get("pipeline_required"),  # 提示词流水线要求
        streamability=dict_options.get("streamability"),  # 提示词流式接口要求
        interface_family=dict_options.get("interface_family"),  # 提示词接口家族
        interface_profile=_load_optional_json(dict_options.get("interface_profile")),  # 提示词接口配置字典
    )

    # prompt 渲染需要 codegen plan 才能体现接口、检查点和依赖图。
    dict_resolved_codegen_plan = build_codegen_plan(dict_resolved_spec)  # 提示词中约束接口和检查点的生成计划

    # 生成最终 prompt 文本，所有可选上下文在这里按需加载。
    str_prompt_text = render_prompt(  # 写入 out_path 并返回给调用方审阅的完整提示词
        # spec、target 和 stage 决定提示词的主任务语境。
        dict_resolved_spec,  # prompt 渲染规格字典
        target="rtl",  # 提示词目标固定为 RTL
        stage=dict_options.get("stage") or "rtl",  # 提示词阶段名称

        # 上下文、证据和记忆输入用于补充模型可见材料。
        context_manifest=_load_optional_json(dict_options.get("context_manifest")),  # 提示词上下文清单
        context_dir=Path(dict_options["context_dir"]) if dict_options.get("context_dir") is not None else None,  # 提示词上下文目录
        evidence=_load_optional_json(dict_options.get("evidence")),  # 提示词证据字典
        memory=_load_optional_json(dict_options.get("memory")),  # 提示词记忆字典

        # 生成约束、子功能和人工决策控制提示词细节。
        comment_language=str(dict_options.get("comment_language", "zh")),  # 提示词要求的 RTL 注释语言
        vector_contract=_load_optional_json(dict_options.get("vector_contract")),  # 提示词向量契约字典
        codegen_plan=dict_resolved_codegen_plan,  # 提示词中的接口和检查点生成计划
        subfunction=dict_options.get("subfunction"),  # 提示词子功能名称
        budget=str(dict_options.get("budget", "normal")),  # 提示词预算档位
        decision=_load_optional_json(dict_options.get("decision")),  # 提示词人工决策字典
    )

    # 归一化 prompt 输出路径，调用方可以传字符串或 Path。
    path_output = Path(out_path)  # prompt 输出文件路径

    # 创建 prompt 父目录，避免写文件时因目录缺失失败。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 写出 prompt 文本，供用户审阅或后续模型调用。
    path_output.write_text(str_prompt_text, encoding="utf-8")

    # 返回 prompt 文件位置和文本内容，保持既有 facade 字段不变。
    return {"path": str(path_output), "prompt": str_prompt_text}

# 检查 Verilog 工件是否满足 Erie formatter-AST 质量门。
def check_verilog_quality(
    artifacts_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    对 Verilog 工件运行 formatter-AST Erie 风格、注释和命名质量门。

    :param artifacts_path: 待检查的 Verilog 工件目录或文件路径。
    :param config: 新式质量门配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: 质量门报告的字典形式。
    :raises TypeError: 传入未知关键字参数时抛出。
    """

    # 质量门入口只允许和检查行为相关的少量兼容关键字。
    set_allowed_keys = {
        "strict",  # 质量门严格模式开关
        "comment_language",  # 期望 RTL 注释采用的语言代码
        "formatter_profile",  # 质量门格式化配置名称
        "include_testbench",  # 质量门是否纳入 testbench
        "vitis_wrapper",  # 质量门是否启用 Vitis 包装规则
        "report_json",  # 供工具消费的 JSON 结果输出路径
        "report_md",  # 面向人工阅读的 Markdown 摘要输出路径
    }  # 质量门入口允许透传的旧式兼容字段

    # 兼容旧关键字调用，同时把质量门控制参数收敛到配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # 质量门阶段统一读取的控制参数
        "check_verilog_quality",  # 标识当前质量门入口
        config,  # 新式质量门配置对象
        legacy_options,  # 保留旧版质量门关键字入口的兼容输入
        allowed_keys=set_allowed_keys,  # 质量门入口允许的关键字集合
    )

    # formatter_profile 先收敛成单独变量，避免在函数调用里保留多行参数表达式。
    str_formatter_profile = str(dict_options.get("formatter_profile", "formatter-normalize"))  # 统一成字符串后再交给质量门选择 formatter 配置

    # 运行 Verilog 质量门，report 对象保留详细诊断和统计指标。
    report = run_verilog_quality_gate(  # 后续转字典并按需落盘的质量门诊断
        Path(artifacts_path),  # 待检查 Verilog 工件路径
        strict=bool(dict_options.get("strict", True)),  # 是否启用严格质量门
        comment_language=str(dict_options.get("comment_language", "zh")),  # 期望 RTL 注释语言
        formatter_profile=str_formatter_profile,  # 格式化检查配置名称
        include_testbench=bool(dict_options.get("include_testbench", False)),  # 是否纳入 testbench 检查
        vitis_wrapper=bool(dict_options.get("vitis_wrapper", False)),  # 是否启用 Vitis 包装规则
    )

    # 根据调用方要求写出 JSON 或 Markdown 报告。
    write_quality_gate_report(
        report,
        json_path=Path(dict_options["report_json"]) if dict_options.get("report_json") is not None else None,
        markdown_path=Path(dict_options["report_md"]) if dict_options.get("report_md") is not None else None,
    )

    # 返回报告字典，方便宿主程序直接断言 ok/errors/warnings。
    return report.to_dict()

# 检查 Verilog 工件是否满足最终交付门禁。
def check_verilog_deliverable(
    artifacts_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    对 Verilog 工件运行最终交付门禁。

    :param artifacts_path: 待检查的 Verilog 工件目录或文件路径。
    :param config: 新式交付门禁配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: 交付门禁报告的字典形式。
    :raises TypeError: 传入未知关键字参数时抛出。
    """

    # facade 只透传影响交付判定和报告写出的显式字段。
    set_allowed_keys = {
        "strict",  # strict warning 是否阻断最终交付
        "comment_language",  # 中文或英文注释策略
        "formatter_profile",  # formatter 抽象语法树配置名称
        "include_testbench",  # testbench 文件是否纳入扫描
        "vitis_wrapper",  # Vitis wrapper ABI 端口兼容开关
        "report_json",  # JSON 机器报告写出路径
        "report_md",  # Markdown 人工报告写出路径
    }  # legacy 关键字调用和 config 字典的交集白名单

    # 旧式关键字和 config 字典在这里被合并成唯一调用载荷。
    dict_options: dict[str, Any] = _merged_option_dict(  # 交付 facade 的规范化选项集合
        "check_verilog_deliverable",  # 报错信息中展示的 facade 名称
        config,  # 新式配置字典
        legacy_options,  # 旧式关键字参数
        allowed_keys=set_allowed_keys,  # 可透传字段白名单
    )  # deliverable gate 调用参数

    # formatter_profile 先收敛成单独变量。
    str_formatter_profile = str(dict_options.get("formatter_profile", "formatter-normalize"))  # formatter 配置档名称

    # 运行最终交付门禁。
    dict_report = run_verilog_deliverable_gate(  # adapter 返回给宿主程序的交付报告
        Path(artifacts_path),  # 调用方传入的 Verilog 工件入口
        strict=bool(dict_options.get("strict", True)),  # 默认按 strict 交付策略执行
        comment_language=str(dict_options.get("comment_language", "zh")),  # 默认要求中文语义注释
        formatter_profile=str_formatter_profile,  # 已归一化的 formatter 配置档
        include_testbench=bool(dict_options.get("include_testbench", False)),  # testbench 纳入开关
        vitis_wrapper=bool(dict_options.get("vitis_wrapper", False)),  # wrapper ABI 兼容开关
    )  # 交付门禁报告字典

    # 根据调用方要求写出报告。
    write_verilog_deliverable_gate_report(
        dict_report,
        json_path=Path(dict_options["report_json"]) if dict_options.get("report_json") is not None else None,
        markdown_path=Path(dict_options["report_md"]) if dict_options.get("report_md") is not None else None,
    )

    # 返回报告字典，供宿主直接判断 delivery_ready。
    return dict_report

# 验证已生成的 Verilog 工件。
def validate_verilog_artifacts(
    spec: JsonSource,
    artifacts_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """
    验证已生成的 Verilog 工件，并可选写出验证报告 JSON。

    :param spec: 用于验证的规格路径或规格字典。
    :param artifacts_path: 待验证的生成工件路径。
    :param config: 新式生成工件验证配置字典。
    :param legacy_options: 兼容旧接口的关键字输入集合。
    :return: 验证报告的字典形式。
    :raises TypeError: 传入未知关键字参数时抛出。
    """

    # 验证入口允许的兼容关键字只覆盖验证行为，不接受生成期 provider 参数。
    set_allowed_keys = {
        "target",  # 工件验证目标类型
        "design_requirements",  # 工件验证设计需求覆盖
        "pipeline_required",  # 工件验证 pipeline 要求
        "streamability",  # 工件验证流式接口要求
        "interface_family",  # 工件验证接口家族
        "interface_profile",  # 工件验证接口 profile
        "run_external",  # 工件验证外部执行开关
        "external_target",  # 工件验证外部执行目标
        "readiness",  # 工件验证准备级别
        "comment_language",  # 工件验证注释语言
        "reference_contract",  # 工件验证参考契约
        "report_json",  # 工件验证 JSON 报告路径
    }  # 工件验证入口允许透传的旧式兼容字段

    # 兼容旧关键字调用，同时把验证控制参数收敛到配置对象。
    dict_options: dict[str, Any] = _merged_option_dict(  # 生成工件验证阶段统一读取的控制参数
        "validate_verilog_artifacts",  # 标识当前工件验证入口
        config,  # 新式工件验证配置对象
        legacy_options,  # 兼容旧版工件验证调用的关键字输入
        allowed_keys=set_allowed_keys,  # 工件验证入口允许的关键字集合
    )

    # 准备验证用规格，保证验证逻辑使用和生成逻辑一致的需求默认值。
    dict_resolved_spec = _prepare_facade_spec(spec,  # 验证用规格字典
        # spec 和 target 定义 generated RTL 应满足的目标契约。
        target=_resolve_target(dict_options.get("target"), spec, {}),  # 验证阶段归一化 target

        # 需求和接口覆盖项保证验证与生成阶段使用同一批约束。
        design_requirements=_load_optional_json(dict_options.get("design_requirements")),  # 验证设计需求覆盖字典
        pipeline_required=dict_options.get("pipeline_required"),  # 验证 pipeline 要求
        streamability=dict_options.get("streamability"),  # 验证流式接口要求
        interface_family=dict_options.get("interface_family"),  # 验证接口家族
        interface_profile=_load_optional_json(dict_options.get("interface_profile")),  # 验证接口 profile 字典
    )

    # 计算本次验证是否允许外部执行，未达 readiness 时会自动降级。
    bool_run_external = _resolve_external_run(bool(dict_options.get("run_external", True)),  # 验证阶段最终采用的外部执行开关
        readiness=str(dict_options.get("readiness", "static")),  # 验证阶段准备级别
        external_target=str(dict_options.get("external_target", "remote")),  # 验证阶段外部执行目标
    )

    # 运行 generated RTL 验证，返回对象保留静态、编译和执行诊断。
    report = validate_generated(  # generated artifact 的综合验证报告
        dict_resolved_spec,  # 已补齐接口需求的验证规格
        Path(artifacts_path),  # 待验证生成工件路径
        target="rtl",  # artifact 验证固定检查 RTL
        run_external=bool_run_external,  # readiness 解析后的外部执行许可
        readiness=str(dict_options.get("readiness", "static")),  # validate facade 请求的验证深度
        comment_language=str(dict_options.get("comment_language", "zh")),  # 验证期望 RTL 注释语言
        reference_contract=_load_optional_json(dict_options.get("reference_contract")),  # 验证参考契约字典
    )

    # 将报告对象转为字典，作为 facade 的稳定返回形式。
    payload = report.to_dict()  # 验证报告字典

    # 调用方传入 report_json 时，额外写出机器可读报告。
    if dict_options.get("report_json") is not None:

        # 归一化报告输出路径，允许调用方传字符串或 Path。
        path_out = Path(dict_options["report_json"])  # 验证报告 JSON 输出路径

        # 创建报告父目录，避免写入时目录不存在。
        path_out.parent.mkdir(parents=True, exist_ok=True)

        # 写出 pretty JSON，便于人工审阅和测试 fixture 对比。
        path_out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 返回验证报告字典，保持既有 facade wire shape。
    return payload

# 解析并校验 facade 支持的唯一 target。
def _resolve_target(
    target: str | None,
    spec: str | Path | dict[str, Any] | None,
    config: dict[str, Any],
) -> str:
    """把目标类型收敛为 facade 支持的唯一 `rtl` 取值。

    参数:
        target: 调用方显式传入的目标类型。
        spec: 可能携带 target 字段的规格输入。
        config: workflow 或 facade 级默认配置字典。

    返回:
        经过校验后的固定字符串 `rtl`。

    异常:
        ValueError: 当调用方尝试使用非 RTL 目标时抛出。
    """

    # 默认没有 spec target，后续按显式 target 或配置兜底。
    raw_target: Any | None = None  # spec 中声明的 target

    # spec 存在时读取其 target 字段，用于和调用参数共同决定目标。
    if spec is not None:

        # 读取原始 spec 副本，避免 target 判断阶段修改调用方字典。
        raw_target = _load_raw_spec(spec).get("target")  # 原始 spec target 字段

    # 解析最终 target，facade 只允许 rtl。
    str_resolved = str(target or raw_target or config.get("target") or "rtl").lower()  # 归一化 target 字符串

    # 非 rtl target 会破坏本 skill 的 Verilog-only 合约。
    if str_resolved != "rtl":

        # 明确拒绝非 RTL 目标，保持 Verilog-only 门面边界。
        raise ValueError("> ERR: [Python] Only target 'rtl' is supported.")

    # 返回固定 rtl，避免大小写或空值差异传播到 runtime。
    return "rtl"

# 把路径或字典形式的 spec 物化为 workflow 输入文件。
def _materialize_spec(spec: str | Path | dict[str, Any], out_path: Path, *, target: str | None) -> Path:
    """把路径或字典形式的规格输入物化成 workflow 可读取的文件。

    参数:
        spec: 原始规格路径或规格字典。
        out_path: 物化后 spec 文件的输出路径。
        target: 需要写入 spec 的归一化目标名称。

    返回:
        已写出的 spec 文件路径。
    """

    # 路径输入走 read_spec，保持文件格式和 target 归一化语义。
    if isinstance(spec, (str, Path)):

        # 从磁盘读取并归一化 spec，供 workflow 使用。
        normalized = read_spec(Path(spec), target=target)  # 从文件读取的归一化 spec

    # 非路径输入时，按内存态字典直接归一化为 workflow 可读规格。
    else:

        # 字典输入直接归一化，避免调用方必须先写临时 spec 文件。
        normalized = normalize_spec(spec, target=target)  # 从字典归一化的 spec

    # 创建 spec 输出目录，保证 write_spec 可以写入目标文件。
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 写出 workflow 使用的 spec 文件。
    write_spec(out_path, normalized)

    # 返回 materialized spec 路径，供 workflow 入口引用。
    return out_path

# 准备 workflow 和 prompt 共用的 RTL spec。
def _prepare_facade_spec(
    spec: str | Path | dict[str, Any],
    *,
    # target 与设计需求字段决定 spec 的生成边界。
    target: str | None,
    design_requirements: dict[str, Any] | None,
    pipeline_required: bool | None,
    streamability: str | None,

    # 接口族和接口 profile 决定生成代码的端口契约。
    interface_family: str | None,
    interface_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """准备同时适用于 workflow 与 prompt 的标准化 RTL 规格。

    参数:
        spec: 原始规格路径或规格字典。
        target: 调用方显式传入的目标类型。
        design_requirements: 可选设计需求覆盖字典。
        pipeline_required: 可选流水线要求。
        streamability: 可选流式接口要求。
        interface_family: 可选接口家族名称。
        interface_profile: 可选接口 profile 字典。

    返回:
        补齐需求默认值并通过确认校验的标准化规格字典。

    异常:
        ValueError: 当调用方请求非 Verilog-2001 方言时抛出。
    """

    # 读取 spec 副本，避免 facade 在需求补齐时修改调用方原始 dict。
    dict_raw = _load_raw_spec(spec)  # facade 输入 spec 副本

    # 校验 target 仍是 Verilog-only 支持的 rtl。
    _resolve_target(target, dict_raw, {})

    # 非 Verilog-2001 方言不进入本 facade，避免后续 runtime 产生混合语义。
    if dict_raw.get("rtl_dialect") not in (None, "", "verilog"):

        # 明确拒绝非 Verilog-2001 方言，避免生成混合 RTL 契约。
        raise ValueError("> ERR: [Python] Only Verilog-2001 is supported.")

    # 固定 target，确保后续 spec/write workflow 都看到 rtl。
    dict_raw["target"] = "rtl"  # 传给 workflow 的目标类型固定为 RTL

    # 固定 RTL 方言，确保 runtime 不生成 SystemVerilog-only 契约。
    dict_raw["rtl_dialect"] = "verilog"  # 传给 workflow 的 RTL 方言固定为 Verilog-2001

    # 只有用户提供需求覆盖项时，才标记需求已由用户确认。
    bool_user_confirmed = any(  # 用户是否提供过需求覆盖项
        item is not None  # 单个需求覆盖项是否存在

        # 这些入口参数一旦出现，就表示调用方显式确认过需求约束。
        for item in (  # facade 允许用户显式确认的需求覆盖项
            design_requirements,  # 用户确认的结构化设计需求
            pipeline_required,  # 用户确认的流水线需求
            streamability,  # 用户确认的流式接口能力
            interface_family,  # 用户确认的接口家族
            interface_profile,  # 接口 profile 覆盖项
        )
    )

    # 单独实例化需求确认状态对象，避免在调用点里嵌套构造器。
    requirement_confirmation_state: RequirementConfirmation = RequirementConfirmation(  # 供默认值补齐逻辑消费的确认状态
        confirmed_by_user=True if bool_user_confirmed else None,  # 仅在用户显式覆盖需求时标记确认
    )

    # 把规格副本、覆盖项和确认状态合成为最终 facade spec。
    dict_enriched: dict[str, Any] = apply_requirement_defaults(  # 带 RTL 需求确认信息的 spec
        dict_raw,  # 作为默认值补齐基底的规格副本
        design_requirements=design_requirements,  # 注入调用方显式给出的结构化需求

        # 这些流水线与接口字段会直接影响 normalize 后的端口契约。
        pipeline_required=pipeline_required,  # 传入是否要求插入流水线
        streamability=streamability,  # 传入对流式握手能力的期待
        interface_family=interface_family,  # 指定接口族，约束端口语义
        interface_profile=interface_profile,  # 附加 profile 细节，细化端口模板
        confirmation=requirement_confirmation_state,  # 把确认状态带入需求默认值补全过程
    )

    # 把补齐过需求字段的 spec 再做一次规范化，得到 workflow 可消费的固定结构。
    dict_normalized_spec = normalize_spec(dict_enriched, target="rtl")  # workflow 可消费的规范 spec

    # 校验需求确认状态，避免缺少关键约束时继续生成 RTL。
    validate_requirement_confirmation(dict_normalized_spec)

    # 返回经过需求补齐和确认校验的 spec。
    return dict_normalized_spec

# 写出 facade 生成的结构化 JSON 文件。
def _write_json_object(path: Path, payload: dict[str, Any]) -> Path:
    """把结构化字典写成 UTF-8 JSON 文件并返回其路径。

    参数:
        path: 目标 JSON 文件路径。
        payload: 待写出的结构化字典对象。

    返回:
        已完成写入的 JSON 文件路径。
    """

    # 创建目标父目录，保证 JSON 文件可以写入。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 写出缩进 JSON，保留中文字符以便人工审阅。
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 返回写入路径，方便调用方记录到 workflow payload。
    return path

# 将可选 JSON 字典物化为文件路径。
def _materialize_optional_json(value: str | Path | dict[str, Any] | None, out_path: Path) -> Path | None:
    """把可选 JSON 输入统一成可供 runtime 读取的路径。

    参数:
        value: 可选 JSON 路径、字典或空值。
        out_path: 字典输入需要物化到的目标路径。

    返回:
        路径输入或物化后的路径；空值时返回 None。
    """

    # None 表示调用方没有提供该可选 JSON 输入。
    if value is None:

        # 保持 None 传递给 runtime，让其使用默认行为。
        return None

    # 路径输入表示调用方已经准备好 JSON 文件，facade 不复制内容。
    if isinstance(value, (str, Path)):

        # 返回调用方提供的路径，避免改变外部文件布局。
        return Path(value)

    # 字典输入需要物化到 adapter 输入目录，供 runtime 通过路径读取。
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 写出字典 JSON，确保中文内容不被转义。
    out_path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 返回物化后的 JSON 路径。
    return out_path

# 读取可选 JSON 输入，保留 None 和字典输入语义。
def _load_optional_json(value: str | Path | dict[str, Any] | None) -> dict[str, Any] | None:
    """把可选 JSON 输入读成字典，同时保留空值语义。

    参数:
        value: 可选 JSON 路径、字典或空值。

    返回:
        读取后的字典对象；空值时返回 None。
    """

    # None 表示该配置或上下文不存在，调用方应使用默认值。
    if value is None:

        # 保持 None，避免把缺省输入误判为空字典覆盖。
        return None

    # 字典输入已经是内存态 JSON payload，直接返回。
    if isinstance(value, dict):

        # 返回原字典对象，保持调用方显式配置内容。
        return value

    # 路径输入需要读取磁盘 JSON。
    path_value = Path(value)  # 待读取 JSON 路径

    # 返回反序列化字典，供 facade 合并或传给 runtime。
    return json.loads(path_value.read_text(encoding="utf-8"))

# 解析 workflow 配置覆盖项的兼容结构。
def _workflow_overrides(value: dict[str, Any]) -> dict[str, Any]:
    """抽取 workflow 配置覆盖项，兼容嵌套在 `workflow` 字段下的旧结构。

    参数:
        value: 原始 workflow 配置字典。

    返回:
        真正用于覆盖默认值的 workflow 配置字典。
    """

    # workflow 字段允许用户把配置嵌套在更大的 JSON 对象内。
    dict_nested_workflow = value.get("workflow")  # 可选嵌套 workflow 配置

    # 嵌套 workflow 为字典时，只取该子配置作为覆盖项。
    if isinstance(dict_nested_workflow, dict):

        # 返回嵌套 workflow 覆盖项，保持旧配置文件兼容。
        return dict_nested_workflow

    # 没有嵌套 workflow 时，整个字典就是覆盖项。
    return value

# 读取 spec 原始字典并保护调用方传入对象。
def _load_raw_spec(spec: str | Path | dict[str, Any]) -> dict[str, Any]:
    """读取 spec 原始字典，并避免修改调用方传入对象。

    参数:
        spec: 原始规格路径或规格字典。

    返回:
        可安全修改的规格字典副本。
    """

    # 字典输入必须深拷贝，避免后续默认值补齐污染调用方对象。
    if isinstance(spec, dict):

        # 返回输入 spec 的深拷贝。
        return deepcopy(spec)

    # 路径输入需要读取 JSON spec。
    path_spec = Path(spec)  # 调用方提供的 spec JSON 文件路径

    # 返回磁盘 spec 的反序列化内容。
    return json.loads(path_spec.read_text(encoding="utf-8"))

# 统一已有 RTL 输入为路径列表。
def _resolve_sources(source: str | Path | list[str | Path]) -> list[Path]:
    """把单路径或多路径 RTL 输入统一收敛成路径列表。

    参数:
        source: 单个 RTL 路径或 RTL 路径列表。

    返回:
        统一转换后的 Path 列表。
    """

    # 多文件输入保持原有顺序，只把元素归一化为 Path。
    if isinstance(source, list):

        # 返回多文件 RTL 输入路径列表。
        return [Path(item) for item in source]

    # 单文件输入包装为列表，便于 runtime 统一处理项目拓扑。
    return [Path(source)]

# 解析 representative corpus 的默认集合或用户显式挑选的 case 列表。
def _selected_representative_cases(raw_case_selection: Any) -> list[dict[str, Any]]:
    """返回本次 run-cases 需要执行的 representative case 清单。

    参数:
        raw_case_selection: 调用方传入的可选 case 选择值。

    返回:
        按稳定顺序排列的 representative case 元信息列表。

    异常:
        ValueError: 当 case_id 未知或显式选择为空时抛出。
    """

    # 未显式指定 case 时，固定执行 representative-10 默认集合。
    if raw_case_selection is None:

        # 缺省路径保持 5 个 bad 后接 5 个 ideal 的稳定顺序。
        list_requested_case_ids = list(REPRESENTATIVE_CASE_ORDER)  # 默认 representative-10 顺序

    # 单个 case_id 字符串按单例列表处理。
    elif isinstance(raw_case_selection, str):

        # 保持显式单 case 选择接口简单可用。
        list_requested_case_ids = [raw_case_selection]  # 单例 case 选择列表

    # 列表或元组输入保持原始顺序，并统一转换成字符串。
    elif isinstance(raw_case_selection, (list, tuple)):

        # CLI `--case` 会以列表形式进入这里。
        list_requested_case_ids = [str(case_id) for case_id in raw_case_selection]  # 调用方显式请求的 case_id 顺序列表

    # 其余类型都不符合 case 选择合同。
    else:

        # 阻止不透明对象静默进入 case 选择路径。
        raise ValueError("> ERR: [Python] run_verilog_cases expects `case` to be None, a string, or a list of strings.")

    # 显式传入空列表会导致输出目录语义不明确，应立即拒绝。
    if not list_requested_case_ids:

        # 明确提示调用方不要把空选择当作默认 representative-10。
        raise ValueError("> ERR: [Python] run_verilog_cases received an empty case selection.")

    # set_known_case_ids 用于快速校验请求项是否合法。
    set_known_case_ids = set(REPRESENTATIVE_CASE_CATALOG)  # 当前支持的 representative case_id 集合

    # 先按用户输入顺序去重，避免重复 case 覆盖同名输出目录。
    list_unique_case_ids: list[str] = []  # 去重后仍保持原始顺序的 case_id 列表

    # set_seen_case_ids 防止相同 case 被重复加入输出队列。
    set_seen_case_ids: set[str] = set()  # 已经收录过的 case_id 集合

    # 逐项去重，同时保留调用方给出的顺序。
    for str_case_id in list_requested_case_ids:

        # 重复选择同一 case 时直接跳过后续重复项。
        if str_case_id in set_seen_case_ids:

            # 同名 case 目录只允许执行一次。
            continue

        # 首次出现的 case_id 保留到最终执行队列。
        list_unique_case_ids.append(str_case_id)

        # 标记当前 case 已加入执行队列。
        set_seen_case_ids.add(str_case_id)

    # 统一收集未知 case_id，便于一次性报错。
    list_unknown_case_ids = [case_id for case_id in list_unique_case_ids if case_id not in set_known_case_ids]  # 不在 representative corpus 中的 case_id 列表

    # 只要存在未知 case，就立即阻断，避免部分输出导致合同混乱。
    if list_unknown_case_ids:

        # str_available_cases 列出全部可用 case，方便调用方直接修正。
        str_available_cases = ", ".join(REPRESENTATIVE_CASE_ORDER)  # representative corpus 的固定可选 case_id 列表

        # str_unknown_cases 保留用户实际输入的未知项，便于定位错误。
        str_unknown_cases = ", ".join(list_unknown_case_ids)  # 当前命中的未知 case_id 列表

        # 拒绝未知 case 选择，避免 silently fallback 到默认集合。
        raise ValueError(
            f"> ERR: [Python] Unknown representative case(s): {str_unknown_cases}. "
            f"Available cases: {str_available_cases}."
        )

    # 返回元信息副本，避免调用方误改全局目录清单。
    return [dict(REPRESENTATIVE_CASE_CATALOG[case_id]) for case_id in list_unique_case_ids]

# 生成单个 representative case 的工件路径集合。
def _case_artifact_paths(path_case_dir: Path, path_source_reference: Path) -> dict[str, Path]:
    """返回单个 representative case 固定合同要求的路径集合。

    参数:
        path_case_dir: 当前 case 的独立输出目录。
        path_source_reference: 当前 case 的原始样例路径。

    返回:
        source、governed、stage_status、strict_report 和 verify_existing 的路径字典。
    """

    # 原样保留输入样例后缀，确保 governed/source 扩展名一致。
    str_case_suffix = path_source_reference.suffix or ".v"  # 当前样例的文件后缀

    # 返回单个 case 目录合同要求的全部固定路径。
    return {
        "source": path_case_dir / f"source{str_case_suffix}",
        "governed": path_case_dir / f"governed{str_case_suffix}",
        "stage_status": path_case_dir / "stage_status.json",
        "strict_report_json": path_case_dir / "strict_report.json",
        "strict_report_md": path_case_dir / "strict_report.md",
        "verify_existing_dir": path_case_dir / "verify_existing",
    }

# 初始化单个 representative case 的 stage_status 载荷。
def _case_stage_status_payload(dict_case_spec: dict[str, Any], path_source_reference: Path) -> dict[str, Any]:
    """返回单 case 初始 stage_status 载荷。

    参数:
        dict_case_spec: 当前 case 的元信息字典。
        path_source_reference: 当前 case 的原始样例路径。

    返回:
        初始状态为 running 的 stage_status 字典。
    """

    # 返回逐例目录固定的阶段状态骨架。
    return {
        "case_id": dict_case_spec["case_id"],
        "cohort": dict_case_spec["cohort"],
        "source_reference": str(path_source_reference),
        "status": "running",
        "stages": {},
    }

# 复制原始样例到当前 case 私有目录并返回阶段摘要。
def _copy_source_stage_payload(path_source_reference: Path, path_case_source: Path) -> dict[str, str]:
    """复制原始样例副本，并返回 copy_source 阶段摘要。

    参数:
        path_source_reference: 仓库内原始样例路径。
        path_case_source: 当前 case 目录中的 source 副本路径。

    返回:
        `copy_source` 阶段的稳定摘要字典。
    """

    # 复制原始样例，后续所有治理与诊断都以副本为边界。
    path_case_source.write_bytes(path_source_reference.read_bytes())

    # 返回原始样例复制完成后的阶段摘要。
    return {
        "status": "completed",
        "path": str(path_case_source),
    }

# 用 formatter-preserve 生成 governed RTL 文件并返回命中编码。
def _preserve_governed_case_file(path_case_source: Path, path_case_governed: Path) -> str:
    """把 source 副本转换为 governed RTL，并返回命中的源编码。

    参数:
        path_case_source: 当前 case 的 source 副本路径。
        path_case_governed: 当前 case 的 governed RTL 输出路径。

    返回:
        `read_verilog_source` 命中的源文件编码名称。
    """

    # 先按统一编码策略读取 source 副本。
    str_source_text, str_source_encoding = read_verilog_source(path_case_source)  # 当前样例的规范化文本与命中编码

    # process_formatter_backend_preserve 只用于生成当前 case 的 governed RTL 文本。
    process_formatter_backend_preserve = create_formatter_backend(profile="formatter-preserve")  # 当前 case 的 formatter-preserve 后端

    # str_governed_text 是 governed.<ext> 需要写回的治理后 RTL 文本。
    str_governed_text = process_formatter_backend_preserve.format_text(str_source_text, path_case_source)  # formatter-preserve 生成的治理后 RTL 文本

    # governed 文件统一写成 UTF-8，供 strict formatter 和 verify-existing 继续消费。
    path_case_governed.write_text(str_governed_text, encoding="utf-8")

    # 返回 source 副本命中的编码名，供 stage_status 记录。
    return str_source_encoding

# 构建 formatter-preserve 阶段摘要。
def _preserve_stage_payload(path_case_governed: Path, str_source_encoding: str) -> dict[str, str]:
    """返回 formatter-preserve 阶段的稳定摘要。

    参数:
        path_case_governed: 当前 case 的 governed RTL 路径。
        str_source_encoding: source 副本命中的编码名称。

    返回:
        `formatter_preserve` 阶段摘要字典。
    """

    # 返回 governed RTL 生成完成后的阶段摘要。
    return {
        "status": "completed",
        "profile": "formatter-preserve",
        "source_encoding": str_source_encoding,
        "path": str(path_case_governed),
    }

# 返回 strict quality-gate 所需的固定配置字典。
def _strict_quality_config(path_report_json: Path, path_report_md: Path) -> dict[str, Any]:
    """返回 governed RTL strict 检查使用的固定配置。

    参数:
        path_report_json: strict JSON 报告落盘路径。
        path_report_md: strict Markdown 报告落盘路径。

    返回:
        `check_verilog_quality` 使用的 strict 配置字典。
    """

    # 返回 strict formatter-normalize 报告的固定配置。
    return {
        "strict": True,
        "formatter_profile": "formatter-normalize",
        "report_json": path_report_json,
        "report_md": path_report_md,
    }

# 生成 strict quality-gate 阶段摘要。
def _strict_quality_stage_payload(
    dict_strict_report: dict[str, Any],
    bool_strict_ok: bool,
    path_report_json: Path,
    path_report_md: Path,
) -> dict[str, Any]:
    """返回 strict quality-gate 阶段的稳定摘要。

    参数:
        dict_strict_report: `check_verilog_quality` 返回的报告字典。
        bool_strict_ok: strict quality-gate 的通过状态。
        path_report_json: strict JSON 报告路径。
        path_report_md: strict Markdown 报告路径。

    返回:
        `strict_quality_gate` 阶段的稳定摘要字典。
    """

    # 返回 strict formatter-normalize 阶段摘要。
    return {
        "status": "passed" if bool_strict_ok else "reported",
        "profile": "formatter-normalize",
        "ok": bool_strict_ok,
        "errors": _quality_issue_count(dict_strict_report, "error"),
        "warnings": _quality_issue_count(dict_strict_report, "warning"),
        "report_json": str(path_report_json),
        "report_md": str(path_report_md),
    }

# 返回 verify-existing 固定配置。
def _verify_existing_config(path_verify_existing_dir: Path) -> dict[str, Any]:
    """返回 representative bad case 的 verify-existing 固定配置。

    参数:
        path_verify_existing_dir: 当前 case 的 verify_existing 目录。

    返回:
        `verify_existing_verilog` 使用的稳定配置字典。
    """

    # 返回计划要求的 conservative/static/no-external 诊断边界。
    return {
        "out_dir": path_verify_existing_dir,
        "automation_mode": "conservative",
        "readiness": "static",
        "run_external": False,
    }

# 生成坏例 verify-existing 阶段摘要。
def _verify_existing_stage_payload(path_verify_existing_dir: Path, str_verify_status: str) -> dict[str, str]:
    """返回坏例 verify-existing 阶段的稳定摘要。

    参数:
        path_verify_existing_dir: verify-existing 诊断目录。
        str_verify_status: verify-existing runtime 返回的结果状态。

    返回:
        `verify_existing` 阶段摘要字典。
    """

    # 返回坏例 verify-existing 的目录和状态摘要。
    return {
        "status": "completed",
        "result_status": str_verify_status,
        "run_dir": str(path_verify_existing_dir),
    }

# 生成 ideal 样例的 verify-existing 跳过摘要。
def _verify_existing_skip_payload() -> dict[str, str]:
    """返回 ideal 样例 verify-existing 跳过说明。

    参数:
        无。

    返回:
        `verify_existing` 阶段的 skipped 摘要字典。
    """

    # ideal 样例只要求 governed RTL 与 strict 报告，不执行 verify-existing。
    return {
        "status": "skipped",
        "reason": "verify-existing only runs for representative bad cases.",
    }

# 把单 case 常用工件路径收敛成短键名，便于阶段逻辑保持短行宽。
def _case_path_refs(dict_case_paths: dict[str, Path]) -> dict[str, Path]:
    """返回单 case 常用工件路径的短键引用字典。

    参数:
        dict_case_paths: `_case_artifact_paths` 返回的完整路径集合。

    返回:
        source、governed、report_json、report_md 和 verify_dir 的短键路径字典。
    """

    # 返回后续阶段最常访问的几条工件路径短引用。
    return {
        "source": dict_case_paths["source"],
        "governed": dict_case_paths["governed"],
        "report_json": dict_case_paths["strict_report_json"],
        "report_md": dict_case_paths["strict_report_md"],
        "verify_dir": dict_case_paths["verify_existing_dir"],
    }

# 返回当前 case 的 strict quality-gate 固定配置。
def _case_strict_quality_config(dict_case_path_refs: dict[str, Path]) -> dict[str, Any]:
    """返回单 case strict 检查使用的固定配置。

    参数:
        dict_case_path_refs: 当前 case 常用工件路径的短键引用字典。

    返回:
        `check_verilog_quality` 使用的 strict 配置字典。
    """

    # 使用单 case 的 JSON/Markdown 报告路径生成 strict 配置。
    return _strict_quality_config(dict_case_path_refs["report_json"], dict_case_path_refs["report_md"])

# 返回当前 case 的 strict stage 摘要。
def _case_strict_stage_payload(
    dict_strict_report: dict[str, Any],
    bool_strict_ok: bool,
    dict_case_path_refs: dict[str, Path],
) -> dict[str, Any]:
    """返回单 case strict quality-gate 阶段摘要。

    参数:
        dict_strict_report: 当前 governed RTL 的 strict 报告字典。
        bool_strict_ok: strict quality-gate 的通过状态。
        dict_case_path_refs: 当前 case 常用工件路径的短键引用字典。

    返回:
        供 stage_status 复用的 strict 阶段摘要字典。
    """

    # 用短键路径回填 strict 报告路径，减少调用点重复样板。
    return _strict_quality_stage_payload(
        dict_strict_report,
        bool_strict_ok,
        dict_case_path_refs["report_json"],
        dict_case_path_refs["report_md"],
    )

# 单独提炼 bad case verify-existing 配置 helper，避免主流程继续堆长行。
def _case_verify_config(dict_case_path_refs: dict[str, Path]) -> dict[str, Any]:
    """返回单个 bad case 的 verify-existing 固定配置。

    参数:
        dict_case_path_refs: 当前 case 常用工件路径的短键引用字典。

    返回:
        `verify_existing_verilog` 使用的固定配置字典。
    """

    # verify-existing 目录从当前 case 的短键路径字典中读取。
    return _verify_existing_config(dict_case_path_refs["verify_dir"])

# 返回当前 bad case 的 verify-existing 阶段摘要。
def _case_verify_stage_payload(dict_case_path_refs: dict[str, Path], str_verify_status: str) -> dict[str, str]:
    """返回单个 bad case 的 verify-existing 阶段摘要。

    参数:
        dict_case_path_refs: 当前 case 常用工件路径的短键引用字典。
        str_verify_status: verify-existing runtime 返回的结果状态。

    返回:
        供 stage_status 复用的 verify-existing 阶段摘要字典。
    """

    # verify-existing 阶段摘要同时记录结果状态和目录位置。
    return _verify_existing_stage_payload(dict_case_path_refs["verify_dir"], str_verify_status)

# 用当前 case 的短键路径引用生成 copy_source 阶段摘要。
def _case_copy_source_stage_payload(
    path_source_reference: Path,
    dict_case_path_refs: dict[str, Path],
) -> dict[str, str]:
    """返回单 case 的 copy_source 阶段摘要。

    参数:
        path_source_reference: 仓库内原始样例路径。
        dict_case_path_refs: 当前 case 常用工件路径的短键引用字典。

    返回:
        `copy_source` 阶段的稳定摘要字典。
    """

    # 复用短键 source 路径生成原始样例复制阶段摘要。
    return _copy_source_stage_payload(path_source_reference, dict_case_path_refs["source"])

# 用当前 case 的短键路径引用执行 formatter-preserve 并回传源编码。
def _case_preserve_encoding(dict_case_path_refs: dict[str, Path]) -> str:
    """返回单 case 执行 formatter-preserve 后命中的源编码名称。

    参数:
        dict_case_path_refs: 当前 case 常用工件路径的短键引用字典。

    返回:
        `read_verilog_source` 命中的源编码名称。
    """

    # governed RTL 固定由短键 source/governed 路径组合生成。
    return _preserve_governed_case_file(dict_case_path_refs["source"], dict_case_path_refs["governed"])

# 把 governed 副本路径与命中编码封装成 formatter-preserve 阶段记录。
def _case_preserve_stage_payload(dict_case_path_refs: dict[str, Path], str_source_encoding: str) -> dict[str, str]:
    """返回单 case 的 formatter-preserve 阶段摘要。

    参数:
        dict_case_path_refs: 当前 case 常用工件路径的短键引用字典。
        str_source_encoding: source 副本命中的编码名称。

    返回:
        `formatter_preserve` 阶段的稳定摘要字典。
    """

    # 复用短键 governed 路径和源编码生成 preserve 阶段摘要。
    return _preserve_stage_payload(dict_case_path_refs["governed"], str_source_encoding)

# 把 verify_existing 目录路径转换为 summary 可接受的可选字符串。
def _existing_verify_dir_value(path_verify_existing_dir: Path) -> str | None:
    """返回 summary 需要的 verify_existing 目录字符串。

    参数:
        path_verify_existing_dir: 当前 case 的 verify_existing 目录路径。

    返回:
        目录已存在时返回字符串路径；否则返回 None。
    """

    # 只有 bad 样例真正产出目录时才把路径带回 summary。
    if path_verify_existing_dir.is_dir():

        # 把 verify_existing 目录标准化为可序列化字符串。
        return str(path_verify_existing_dir)

    # ideal 或失败路径没有目录时显式返回空值。
    return None

# 汇总单个 representative case 的稳定摘要。
def _case_summary_payload(
    dict_case_spec: dict[str, Any],
    dict_case_paths: dict[str, Path],
    dict_stage_status: dict[str, Any],
    bool_strict_ok: bool,
    str_verify_status: str | None,
) -> dict[str, Any]:
    """返回单个 representative case 的 summary 条目。

    参数:
        dict_case_spec: 当前 case 的元信息字典。
        dict_case_paths: 当前 case 的固定路径集合。
        dict_stage_status: 最终 stage_status 字典。
        bool_strict_ok: strict quality-gate 的通过状态。
        str_verify_status: verify-existing 的 runtime 状态；ideal 样例可为空。

    返回:
        供 `summary.json` 和 `summary.md` 复用的单 case 摘要字典。
    """

    # 只有坏例真正产出 verify_existing 目录时，summary 才回填该目录路径。
    str_verify_dir = _existing_verify_dir_value(dict_case_paths["verify_existing_dir"])  # 当前 case verify-existing 目录路径

    # 返回 summary JSON/Markdown 需要的逐例稳定字段。
    return {
        "case_id": str(dict_case_spec["case_id"]),
        "cohort": str(dict_case_spec["cohort"]),
        "status": str(dict_stage_status["status"]),
        "source_path": str(dict_case_paths["source"]) if dict_case_paths["source"].is_file() else None,
        "governed_path": str(dict_case_paths["governed"]) if dict_case_paths["governed"].is_file() else None,
        "stage_status_path": str(dict_case_paths["stage_status"]),
        "strict_ok": bool_strict_ok,
        "strict_report_json": (
            str(dict_case_paths["strict_report_json"])
            if dict_case_paths["strict_report_json"].is_file()
            else None
        ),
        "strict_report_md": (
            str(dict_case_paths["strict_report_md"])
            if dict_case_paths["strict_report_md"].is_file()
            else None
        ),
        "verify_existing_status": str_verify_status,
        "verify_existing_dir": str_verify_dir,
    }

# 执行单个 representative case 的治理、strict 报告和可选 verify-existing 诊断。
def _run_single_representative_case(dict_case_spec: dict[str, Any], path_case_dir: Path) -> dict[str, Any]:
    """运行单个 representative case，并写出固定目录合同要求的产物。

    参数:
        dict_case_spec: 当前 case 的元信息字典。
        path_case_dir: 当前 case 的独立输出目录。

    返回:
        当前 case 的稳定摘要字典。

    异常:
        本函数会把执行异常折叠进 `stage_status.json`，并在返回摘要中标记 `failed`。
    """

    # path_source_reference 指向仓库内的原始 corpus 样例。
    path_source_reference = Path(dict_case_spec["source"])  # representative case 的原始样例路径

    # dict_case_paths 汇总当前 case 合同要求的所有固定工件路径。
    dict_case_paths = _case_artifact_paths(path_case_dir, path_source_reference)  # 当前 case 的固定工件路径集合

    # bool_strict_ok 和 str_verify_status 会同步写入 stage_status 与 summary。
    bool_strict_ok = False  # 当前 case strict quality-gate 是否通过

    # str_verify_status 只在 bad 样例成功追加 verify-existing 后才会写回。
    str_verify_status: str | None = None  # 当前 case verify-existing 的结果状态

    # dict_stage_status 承载当前 case 的阶段明细，失败时也必须落盘。
    dict_stage_status = _case_stage_status_payload(dict_case_spec, path_source_reference)  # 单 case 初始阶段状态

    # try/finally 确保失败路径也会落盘 stage_status.json，保持逐例目录可审计。
    try:

        # 每个 case 都使用独立目录，避免原始样例、副本和诊断文件互相覆盖。
        path_case_dir.mkdir(parents=True, exist_ok=True)

        # 缺失原始样例时必须立即阻断，避免输出空壳目录冒充完成。
        if not path_source_reference.is_file():

            # 原始样例是当前 case 继续执行任何治理步骤的前提。
            raise ValueError(f"> ERR: [Python] Representative case source is missing: {path_source_reference}")

        # dict_case_path_refs 用短键名承载最常访问的几条工件路径。
        dict_case_path_refs = _case_path_refs(dict_case_paths)  # 当前 case 常用工件路径的短键引用字典

        # dict_stages 指向 stage_status 的阶段映射，便于后续短行宽写入。
        dict_stages = dict_stage_status["stages"]  # 当前 case 的阶段摘要映射

        # 先复制原始样例，后续所有治理和诊断都以该副本为输入边界。
        dict_stages["copy_source"] = _case_copy_source_stage_payload(path_source_reference, dict_case_path_refs)  # 原始样例复制阶段摘要

        # preserve 是 governed RTL 的唯一来源，同时回传命中源编码。
        str_source_encoding = _case_preserve_encoding(dict_case_path_refs)  # source 副本命中的编码名称

        # preserve 阶段摘要必须把 governed 路径和命中编码同步写回阶段映射。
        dict_stages["formatter_preserve"] = _case_preserve_stage_payload(  # formatter-preserve 的 governed 路径与编码记录
            dict_case_path_refs,  # 当前 case 的短键路径引用
            str_source_encoding,  # formatter-preserve 命中的源编码
        )

        # dict_strict_config 固定收敛 strict formatter-normalize 所需的报告输出路径。
        dict_strict_config = _case_strict_quality_config(dict_case_path_refs)  # 当前 governed RTL 的 strict 检查配置

        # strict report 固定使用 formatter-normalize + quality-gate 产出 JSON/Markdown 诊断。
        dict_strict_report = check_verilog_quality(dict_case_path_refs["governed"], config=dict_strict_config)  # 当前 governed RTL 的 strict 质量门报告

        # strict ok 状态在 summary 与 Markdown 报告中都需要透出。
        bool_strict_ok = bool(dict_strict_report.get("ok"))  # 当前 governed RTL 的 strict 通过状态

        # dict_strict_stage 汇总 strict 报告路径和 error/warning 计数。
        dict_strict_stage = _case_strict_stage_payload(dict_strict_report, bool_strict_ok, dict_case_path_refs)  # strict formatter-normalize 阶段摘要

        # 把 strict 阶段的统计结果挂回阶段映射，确保 finally 落盘时能原样带出。
        dict_stages["strict_quality_gate"] = dict_strict_stage  # strict 阶段在阶段表中的汇总记录

        # 坏例需要补跑 verify-existing，只产出诊断证据，不改写 governed 文件。
        if dict_case_spec["cohort"] == "bad":

            # dict_verify_config 固定约束 verify-existing 只产出静态诊断证据。
            dict_verify_config = _case_verify_config(dict_case_path_refs)  # bad 样例的 verify-existing 固定配置

            # bad 样例固定追加 conservative/static/no-external 诊断证据。
            dict_verify_result = verify_existing_verilog(dict_case_path_refs["governed"], config=dict_verify_config)  # bad 样例 governed RTL 的 verify-existing 诊断结果

            # verify-existing 的 runtime 状态供 summary 和 stage_status 汇总。
            str_verify_status = str(dict_verify_result.get("status") or "completed")  # verify-existing runtime 返回的结果状态

            # 把 bad 样例 verify-existing 的目录与运行状态写回阶段映射。
            dict_stages["verify_existing"] = _case_verify_stage_payload(  # bad 样例 verify-existing 的阶段记录
                dict_case_path_refs,  # bad 样例 verify-existing 阶段复用的短键路径集合
                str_verify_status,  # verify-existing 的运行状态
            )

        # ideal 样例不执行 verify-existing，摘要里只保留跳过说明。
        else:

            # ideal 样例只需 governed RTL 和 strict 报告即可完成合同。
            dict_stages["verify_existing"] = _verify_existing_skip_payload()  # ideal 样例 verify-existing 跳过说明

        # 走到这里说明当前 case 已经完成 source/governed/strict/verify 合同。
        dict_stage_status["status"] = "completed"  # 当前 case 已完成全部治理合同

    # 任何阶段抛错都要落盘 stage_status，并把失败细节带回 summary。
    except Exception as exc:

        # 失败状态必须先写回 stage_status，后续 handoff 和 summary 才能区分未完成 case。
        dict_stage_status["status"] = "failed"  # 当前 case 未能完整写出治理合同

        # 保留失败文本，便于 tests 之外的人工排查。
        dict_stage_status["error"] = str(exc)  # 失败路径保留给人工排查的异常文本

    # 无论成功失败都必须落盘 stage_status.json，保持逐例目录可审计。
    finally:

        # stage_status.json 是逐例目录合同的固定组成部分。
        _write_json(dict_case_paths["stage_status"], dict_stage_status)

    # 返回当前 case 最终落盘结果对应的稳定摘要字段。
    return _case_summary_payload(
        dict_case_spec,
        dict_case_paths,
        dict_stage_status,
        bool_strict_ok,
        str_verify_status,
    )

# 统计质量门报告中指定 severity 的 issue 数量。
def _quality_issue_count(dict_report: dict[str, Any], severity: str) -> int:
    """返回质量门报告中指定 severity 的 issue 数量。

    参数:
        dict_report: `check_verilog_quality` 返回的报告字典。
        severity: 需要统计的 issue severity。

    返回:
        命中指定 severity 的 issue 数量。
    """

    # list_issues 保持非列表输入时的安全兜底，避免 summary 生成阶段抛异常。
    list_issues = dict_report.get("issues", []) if isinstance(dict_report, dict) else []  # 质量门报告中的 issue 列表

    # 仅统计字典形态且 severity 精确匹配的 issue。
    return sum(1 for issue in list_issues if isinstance(issue, dict) and issue.get("severity") == severity)

# 把 JSON 字典稳定写成 UTF-8 文件，统一 summary/stage_status 的落盘方式。
def _write_json(path_output: Path, dict_payload: dict[str, Any]) -> None:
    """把字典对象按 UTF-8 JSON 文本写到目标路径。

    参数:
        path_output: 待写入的 JSON 文件路径。
        dict_payload: 需要序列化的字典对象。

    返回:
        无返回值。
    """

    # 父目录按需创建，避免逐例目录尚未生成时写文件失败。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # JSON 报告统一保持中文不转义，并在末尾补换行便于查看 diff。
    path_output.write_text(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# 生成 representative corpus Markdown 摘要的头部行。
def _representative_summary_lead_lines(dict_summary: dict[str, Any]) -> list[str]:
    """返回 representative corpus Markdown 摘要的头部行列表。

    参数:
        dict_summary: `run_verilog_cases` 的结构化摘要字典。

    返回:
        固定包含标题、摘要字段和表头的 Markdown 行列表。
    """

    # 返回 Markdown 摘要固定头部和表格表头。
    return [
        "# representative-10",
        "",
        f"- status: {dict_summary['status']}",
        f"- case_count: {dict_summary['case_count']}",
        f"- completed_cases: {dict_summary['completed_cases']}",
        f"- failed_cases: {dict_summary['failed_cases']}",
        "",
        "| case_id | cohort | status | strict_ok | verify_existing |",
        "| --- | --- | --- | --- | --- |",
    ]

# 生成 representative corpus 的人工可读 Markdown 摘要。
def _representative_summary_markdown(dict_summary: dict[str, Any]) -> str:
    """把 representative corpus 结构化摘要转换为 Markdown 文本。

    参数:
        dict_summary: `run_verilog_cases` 的结构化摘要字典。

    返回:
        供人工审阅的 Markdown 摘要文本。
    """

    # list_lines 先写入固定头部，再逐例追加表格行。
    list_lines = _representative_summary_lead_lines(dict_summary)  # representative corpus Markdown 摘要头部行列表

    # 逐例附加表格行，便于快速审阅 governed/strict/verify 总体状态。
    for dict_case in dict_summary.get("cases", []):

        # verify_existing 列为空时显示 n/a，避免 ideal 样例留下空白单元格。
        str_verify_display = str(dict_case.get("verify_existing_status") or "n/a")  # 表格中的 verify-existing 显示文本

        # strict_ok 统一显示 yes/no，便于肉眼浏览。
        str_strict_display = "yes" if dict_case.get("strict_ok") else "no"  # 表格中的 strict 通过显示文本

        # 当前 case 的表格行追加到摘要末尾。
        list_lines.append(
            f"| {dict_case['case_id']} | {dict_case['cohort']} | {dict_case['status']} | "
            f"{str_strict_display} | {str_verify_display} |"
        )

    # Markdown 文件末尾补换行，便于 shell/cat 和 git diff 审阅。
    return "\n".join(list_lines) + "\n"

# 生成批量 case 的稳定目录标识。
def _batch_case_id(spec: str | Path | dict[str, Any], index: int) -> str:
    """根据规格输入和序号生成稳定的批量 case 标识。

    参数:
        spec: 当前 case 的规格路径或规格字典。
        index: 当前 case 的顺序编号。

    返回:
        可安全用于目录名的 case 标识字符串。
    """

    # 字典 spec 优先使用 name 字段作为 case id。
    if isinstance(spec, dict):

        # 返回字典 spec 的名称，缺省时回落到序号。
        return str(spec.get("name") or f"case_{index}")

    # 路径 spec 使用文件 stem，空 stem 时回落到序号。
    return Path(spec).stem or f"case_{index}"

# 从单 case workflow_result 中提取批量摘要字段。
def _batch_case_summary(case_id: str, case_run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    """从单 case workflow 结果中提取批量摘要字段。

    参数:
        case_id: 当前 case 的稳定标识。
        case_run_dir: 当前 case 的运行目录。
        result: 单 case workflow 返回结果。

    返回:
        适合批量总结果聚合的 case 摘要字典。
    """

    # 提取 workflow_result，缺失或非字典时按空结果处理。
    dict_workflow_result: dict[str, Any] = result.get("workflow_result", {}) if isinstance(result, dict) else {}  # 当前 case 中嵌套的 workflow_result 字段

    # 提取尝试列表，缺失或非字典时按空列表处理。
    bool_has_workflow_attempts = isinstance(dict_workflow_result, dict)  # workflow_result 是否仍保持字典形态

    # 后续摘要依赖 attempts 里的最新一次执行信息，因此先统一兜底成列表。
    list_attempts: list[dict[str, Any]] = dict_workflow_result.get("attempts", []) if bool_has_workflow_attempts else []  # workflow_result 中记录的尝试列表

    # 最新尝试用于定位 artifact_dir、validation_json 和 stage_verification。
    dict_latest_attempt: dict[str, Any] = list_attempts[-1] if list_attempts else {}  # 最新 workflow 尝试记录

    # artifact_dir 可能是相对路径、绝对路径或缺失值。
    raw_artifact_dir = dict_latest_attempt.get("artifact_dir")  # 最新尝试生成工件目录

    # 默认验证状态为 false，只有读取到 validation_json 且 ok 为真才置位。
    bool_validation_ok = False  # case 验证是否通过

    # semantic gate 可能没有生成，缺省保持 None。
    str_or_bool_semantic_gate_ready: bool | str | None = None  # stage verification 缺失时的 ready 占位状态

    # 将 validation_json 字段解析为本地可读路径。
    path_validation = _resolve_result_path(  # validation_json 解析路径
        case_run_dir,  # 查找 stage verification 合同的 case 目录
        dict_latest_attempt.get("validation_json"),  # runtime 记录的 validation_json 字段
    )

    # validation_json 存在时，读取 ok 字段作为 case validation 状态。
    if path_validation is not None and path_validation.exists():

        # 读取验证报告 JSON，用于提取 ok 状态。
        validation_payload = json.loads(path_validation.read_text(encoding="utf-8"))  # validation 报告字典

        # validation ok 字段决定 case 摘要中的 validation_ok。
        bool_validation_ok = bool(validation_payload.get("ok"))  # case 验证布尔结果

    # 将 stage_verification 字段解析为本地路径。
    path_stage_verification = _resolve_result_path(  # 从 contract_paths 中回放语义门报告路径
        case_run_dir,  # 当前 batch case 的运行目录
        (
            (dict_latest_attempt.get("contract_paths") or {}).get("stage_verification")  # runtime 记录的语义门合同路径
            if isinstance(dict_latest_attempt, dict)  # 最新尝试必须是字典才能读取 contract_paths
            else None  # 非字典尝试记录没有可解析的语义门路径
        ),  # stage verification 原始路径字段
    )

    # stage verification 存在时，读取 ready 字段作为语义门状态。
    if path_stage_verification is not None and path_stage_verification.exists():

        # 读取 stage verification JSON，用于提取 ready 状态。
        stage_verification_payload = json.loads(  # 为批量摘要提取 ready 的语义门报告
            path_stage_verification.read_text(encoding="utf-8"),  # 语义门报告文件文本
        )

        # ready 字段可为布尔值或 runtime 保留值，facade 原样透出。
        str_or_bool_semantic_gate_ready = stage_verification_payload.get("ready")  # 语义门 ready 字段的原始取值

    # 解析 artifact_dir 一次，避免返回字典中重复调用路径解析函数。
    path_artifact = _resolve_result_path(case_run_dir, raw_artifact_dir)  # 生成工件解析路径

    # 返回批量 case 的稳定摘要字段。
    return {
        "case_id": case_id,
        "status": str(result.get("status") or "failed"),
        "run_dir": str(case_run_dir),
        "artifact_dir": path_artifact.as_posix() if path_artifact is not None else None,
        "validation_ok": bool_validation_ok,
        "semantic_gate_ready": str_or_bool_semantic_gate_ready,
        "result_path": str(case_run_dir / "workflow_result.json"),
    }

# 解析 workflow_result 中可能出现的本地路径字段。
def _resolve_result_path(run_dir: Path, value: Any) -> Path | None:
    """解析 workflow_result 中可能出现的本地路径字段。

    参数:
        run_dir: 当前运行目录。
        value: workflow_result 中记录的路径字段原值。

    返回:
        可在本地直接读取的路径；无法解析时返回 None。
    """

    # 空值或非字符串字段不能解析为本地路径。
    if not value or not isinstance(value, str):

        # 返回 None 表示该结果字段没有可读本地路径。
        return None

    # 将字符串路径转换为 Path，便于统一处理绝对路径和相对路径。
    path_value = Path(value)  # 结果字段路径候选

    # 绝对路径可以直接返回。
    if path_value.is_absolute():

        # 返回 runtime 写出的绝对路径。
        return path_value

    # external 占位路径不对应本地文件。
    if value.startswith("<external>/"):

        # 返回 None，避免把外部路径误拼到本地 run 目录。
        return None

    # 已存在的相对路径可能来自当前工作目录，优先解析为绝对路径。
    if path_value.exists():

        # 返回当前工作目录下已存在文件的绝对路径。
        return path_value.resolve()

    # 其他相对路径按 run_dir 内部产物处理。
    return run_dir / path_value

# 根据 readiness 和目标策略决定是否真正执行外部验证。
def _resolve_external_run(
    run_external: bool,
    *,
    readiness: str,
    external_target: str,
    allow_static_external: bool = False,
) -> bool:
    """根据 readiness 和目标策略决定是否真正执行外部验证。

    参数:
        run_external: 调用方是否请求外部验证。
        readiness: 当前流程声明的验证准备级别。
        external_target: 外部验证执行目标。
        allow_static_external: 是否允许 static readiness 也直接触发外部验证。

    返回:
        当前调用是否可以继续执行外部验证。

    异常:
        ValueError: 当调用方试图绕过 remote-first 约束时抛出。
    """

    # 调用方没有请求外部验证时，直接关闭外部执行。
    if not run_external:

        # 返回 False，保留纯静态或本地解析流程。
        return False

    # 未达到 compile readiness 且未显式允许 static external 时，外部验证降级关闭。
    if not allow_static_external and not readiness_at_least(readiness, "compile"):

        # 返回 False，避免 readiness 不足时触发外部工具链。
        return False

    # 本 facade 保持 remote-first 策略，local 只允许用户显式批准后传入。
    if external_target != "local":

        # 阻止默认路径绕过远程优先验证约束。
        raise ValueError(
            "> ERR: [Python] External validation is remote-first. Use the remote validation "
            "flow, or pass external_target='local' only after the user explicitly approves "
            "local external validation."
        )

    # 外部验证请求、readiness 和 target 都满足时允许执行。
    return True
