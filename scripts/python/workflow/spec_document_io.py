"""规格文档的公共 IO 入口。

本模块只承载 ``render_spec_bundle`` 和 ``read_spec_document`` 两个公共
便捷入口；核心规格归一化、验证和原子写入逻辑仍由 ``spec_document``
维护，避免形成第二套合同实现。
"""

# 延迟解析联合类型，保持与规格核心模块一致的导入行为。
from __future__ import annotations

# JSON 文件读取和公共类型只依赖标准库。
import inspect
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# renderer 适配器把 settings 选择和测试替身兼容逻辑集中在 IO 层。
def render_waveform_with_settings(
    func_renderer: Callable[..., Any],
    renderer: Callable[..., Any] | None,
    path_json: Path,
    path_svg: Path,
    runtime_settings: Mapping[str, Any] | None,
    wavejson: Mapping[str, Any],
) -> dict[str, Any]:
    """调用 renderer，并保持真实 runtime 与测试替身的参数合同。

    参数:
        func_renderer: 当前选中的 renderer 函数。
        renderer: 调用方显式注入的替身；默认 renderer 时为 None。
        path_json: 已写入的 WaveJSON 文件路径。
        path_svg: SVG 输出路径。
        runtime_settings: 外部工具 settings authority。
        wavejson: 已完成校验的 WaveJSON 对象。
    返回:
        renderer 返回的单图报告映射。

    异常:
        TypeError: 默认 renderer 的调用错误原样传回；显式替身错误时尝试对象形式。
    """

    # 测试替身和真实 runtime 共享路径输入，但 settings 只传给显式声明的 renderer。
    try:

        # 读取签名以判断 renderer 是否声明 settings 参数。
        dict_renderer_parameters = inspect.signature(func_renderer).parameters  # renderer 参数签名

        # 记录 renderer 是否接受外部 policy。
        bool_renderer_accepts_settings = "settings" in dict_renderer_parameters  # renderer settings 支持状态

        # 默认 renderer 接收 settings，确保工具策略来源唯一。
        if renderer is None and bool_renderer_accepts_settings:

            # 真实 runtime 使用路径和 authority settings 渲染。
            return func_renderer(path_json, path_svg, settings=runtime_settings)

        # 显式替身保持历史双参数调用合同。
        return func_renderer(path_json, path_svg)

    # 仅在显式替身不接受路径形式时尝试对象形式。
    except TypeError:

        # 默认 runtime 的 TypeError 必须原样暴露。
        if renderer is None:

            # 默认 runtime 的错误必须保持原始堆栈，避免隐藏工具失败。
            raise

        # 显式替身接收已校验的 WaveJSON 对象。
        return func_renderer(wavejson, path_svg)

# 语义化别名保持原有公共导入路径和参数合同。
def render_spec_bundle(
    spec: Mapping[str, Any] | Path,
    out_dir: Path,
    *,
    source_paths: Sequence[Path] | None = None,
    language: str = "zh",
    renderer: Callable[..., Any] | None = None,
    waveform_policy: Mapping[str, Any] | None = None,
    runtime_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """调用 ``write_spec_bundle`` 的语义化公共别名。

    参数:
        spec: 已加载规格对象或 JSON 文件路径。
        out_dir: 交付工件根目录。
        source_paths: 可选 RTL 文件集合。
        language: 文档语言。
        renderer: 可选 WaveDrom 渲染替身。
        waveform_policy: 可选的 lane 结束时间策略。
        runtime_settings: 可选的外部工具 settings。

    返回:
        与 ``write_spec_bundle`` 相同的发布报告。
    """

    # 延迟导入避免核心模块在定义完成前形成循环依赖。
    from scripts.python.workflow.spec_document import write_spec_bundle

    # 统一委托避免两个公共入口出现行为漂移。
    return write_spec_bundle(
        # 规格与产物根保持公共入口的原始路径合同。
        spec,
        out_dir,

        # 源文件与语言参数只影响文档和接口交叉核验。
        source_paths=source_paths,
        language=language,

        # renderer、对齐策略和 runtime settings 共同决定波形发布链。
        renderer=renderer,
        waveform_policy=waveform_policy,
        runtime_settings=runtime_settings,
    )

# 文件读取入口与 bundle 公共接口共享同一归一化规则。
def read_spec_document(path_spec: Path) -> dict[str, Any]:
    """读取 JSON 文件并返回归一化规格。

    参数:
        path_spec: 规格 JSON 文件路径。

    返回:
        归一化后的规格文档字典。

    异常:
        SpecDocumentError: 文件不可读或规格字段无效时抛出。
    """

    # 延迟导入避免核心模块在定义完成前形成循环依赖。
    from scripts.python.workflow.spec_document import SpecDocumentError, normalize_spec_document

    # 读取入口与 bundle 使用相同的 UTF-8 约定。
    try:

        # 先解析 JSON，再交给唯一归一化入口。
        dict_raw = json.loads(path_spec.read_text(encoding="utf-8"))  # 从 JSON 文件读取原始规格对象

    # 文件问题向上转换为稳定领域异常。
    except (OSError, json.JSONDecodeError) as exc:

        # 保持与 write_spec_bundle 相同的错误协议。
        raise SpecDocumentError(
            "> ERR: [Python] cannot read spec JSON: {}".format(exc)
        ) from exc

    # 返回严格校验后的文档对象。
    return normalize_spec_document(dict_raw)
