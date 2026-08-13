"""Verilog-only 生成、分析和验证公开 facade API。"""

# future annotations 避免注解在导入期求值。
from __future__ import annotations

# Any 用于兼容旧 facade 的自由配置字典；其余类型描述 spec bundle 输入。
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# runtime 路由器负责只分类请求、不触发生成执行。
from scripts.python.workflow.workflow_router import route_verilog_entry

# 统一 VG 门禁入口由质量模块直接提供，避免 facade 复制规则逻辑。
from scripts.python.quality.quality_gate import run_verilog_quality_gate

# spec_document 是规范文档、接口交叉核验和 WaveDrom 伴随包的唯一实现入口。
from scripts.python.workflow.spec_document import write_spec_bundle

# existing-RTL 相关公开能力从子模块重导出。
from .existing_rtl_api import (
    analyze_existing_verilog,
    compare_verilog_semantics,
    improve_existing_verilog,
    verify_existing_verilog,
)

# 质量门与工件验证能力从子模块重导出。
from .quality_api import (
    check_verilog_deliverable,
    check_verilog_quality,
    validate_verilog_artifacts,
)

# 代表性案例运行入口和常量从子模块重导出。
from .representative_cases import (
    DEFAULT_CASE_RUN_DIR,
    REPRESENTATIVE_BAD_CASE_IDS,
    REPRESENTATIVE_CASE_CATALOG,
    REPRESENTATIVE_CASE_ORDER,
    REPRESENTATIVE_IDEAL_CASE_IDS,
    run_verilog_cases,
)

# 工作流 facade 的类型与兼容辅助函数从子模块重导出。
from .workflow_api import (
    JsonSource,
    PathCollectionSource,
    _merged_option_dict,
)

# 工作流 facade 的公开执行入口从子模块重导出。
from .workflow_api import (
    load_default_workflow_config,
    load_workflow_result,
    render_verilog_prompt,
    run_verilog_batch,
    run_verilog_workflow,
)

# __all__ 固定 facade 对外导出的稳定符号集合。
__all__ = [
    "JsonSource",  # 公共 spec 输入类型
    "PathCollectionSource",  # 公共路径集合输入类型
    "DEFAULT_CASE_RUN_DIR",  # 代表性案例默认运行目录名
    "REPRESENTATIVE_BAD_CASE_IDS",  # 负例代表性案例 ID 集合
    "REPRESENTATIVE_CASE_CATALOG",  # 代表性案例目录映射
    "REPRESENTATIVE_CASE_ORDER",  # 代表性案例稳定排序
    "REPRESENTATIVE_IDEAL_CASE_IDS",  # 正例代表性案例 ID 集合
    "run_verilog_workflow",  # 主工作流执行入口
    "run_verilog_batch",  # 批量工作流执行入口
    "render_verilog_prompt",  # 提示词渲染入口
    "validate_verilog_artifacts",  # 工件验证入口
    "check_verilog_deliverable",  # 交付门入口
    "check_verilog_quality",  # 质量门入口
    "run_verilog_quality_gate",  # 统一 VG 门禁入口
    "analyze_existing_verilog",  # 既有 RTL 分析入口
    "improve_existing_verilog",  # 既有 RTL 精修入口
    "compare_verilog_semantics",  # RTL 语义比较入口
    "verify_existing_verilog",  # 既有 RTL 验证修复入口
    "run_verilog_cases",  # 代表性案例运行入口
    "route_verilog_request",  # 请求分类入口
    "load_default_workflow_config",  # 默认工作流配置加载入口
    "load_workflow_result",  # 工作流结果读取入口
]  # facade 导出符号表

# route_verilog_request 保留旧 facade 的请求分类入口。
def route_verilog_request(
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """分类 Verilog 请求，但不执行生成或验证。

    参数:
        config: 新式 facade 配置字典；缺省时仅使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 workflow_router 的请求分类结果字典。
    """

    # 路由白名单文本集中声明兼容字段，避免列表元素注释之间互相相似。
    str_allowed_keys_text = (
        "request_summary spec codegen_plan rtl testbench "
        "logs waveform validation artifact_dir remote_validation_requested"
    )  # 路由入口允许的兼容键文本

    # 再把键文本切成集合，只允许这些字段参与请求分类。
    set_allowed_keys = set(str_allowed_keys_text.split())  # 路由入口允许透传给分类器的字段集合

    # 兼容配置字典把新旧调用参数合并为统一入口视图。
    dict_options = _merged_option_dict(  # 归一化后的路由参数
        "route_verilog_request",  # 当前 facade 入口名称
        config,  # 新式配置字典
        legacy_options,  # 旧式关键字参数
        allowed_keys=set_allowed_keys,  # 允许的兼容字段集合
    )

    # 仅把归一化字段传给 runtime 路由器做分类判定。
    return route_verilog_entry(
        request_summary=str(dict_options.get("request_summary", "")),
        spec=dict_options.get("spec"),
        codegen_plan=dict_options.get("codegen_plan"),
        rtl=dict_options.get("rtl"),
        testbench=dict_options.get("testbench"),
        logs=dict_options.get("logs"),
        waveform=dict_options.get("waveform"),
        validation=dict_options.get("validation"),
        artifact_dir=dict_options.get("artifact_dir"),
        remote_validation_requested=bool(
            dict_options.get("remote_validation_requested", False),
        ),
    )

# write_verilog_specs 为生成、既有 RTL 和独立 CLI 共用同一 spec-first 核心。
def write_verilog_specs(
    spec: Mapping[str, Any] | Path,
    out_dir: Path,
    *,
    source_paths: Sequence[Path] | None = None,
    language: str = "zh",
    renderer: Callable[[Path, Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    写出每个 Verilog 模块的 ``*_spec.md``、WaveJSON 和 SVG 伴随包。

    :param spec: 单模块或多模块规范对象，也可以是 JSON 文件路径。
    :param out_dir: 交付工件根目录。
    :param source_paths: 可选 RTL 源文件，用于模块和端口严格交叉核验。
    :param language: Markdown 语言，支持 ``zh`` 或 ``en``。
    :param renderer: 可选 WaveDrom 渲染器替身，默认使用固定 runtime。
    :return: 模块级写出报告，包含 ``ok``、``spec_root`` 和 ``modules``。
    :raises ValueError: 规范字段、源接口或 WaveDrom 渲染失败时抛出。
    """

    # facade 不复制 spec 规则，只把统一入口暴露给上层调用方。
    return write_spec_bundle(
        spec,
        out_dir,
        source_paths=source_paths,
        language=language,
        renderer=renderer,
    )
