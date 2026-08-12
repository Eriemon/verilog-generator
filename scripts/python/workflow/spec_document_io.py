"""规格文档的公共 IO 入口。

本模块只承载 ``render_spec_bundle`` 和 ``read_spec_document`` 两个公共
便捷入口；核心规格归一化、验证和原子写入逻辑仍由 ``spec_document``
维护，避免形成第二套合同实现。
"""

# 延迟解析联合类型，保持与规格核心模块一致的导入行为。
from __future__ import annotations

# JSON 文件读取和公共类型只依赖标准库。
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# 语义化别名保持原有公共导入路径和参数合同。
def render_spec_bundle(
    spec: Mapping[str, Any] | Path,
    out_dir: Path,
    *,
    source_paths: Sequence[Path] | None = None,
    language: str = "zh",
    renderer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """调用 ``write_spec_bundle`` 的语义化公共别名。

    参数:
        spec: 已加载规格对象或 JSON 文件路径。
        out_dir: 交付工件根目录。
        source_paths: 可选 RTL 文件集合。
        language: 文档语言。
        renderer: 可选 WaveDrom 渲染替身。

    返回:
        与 ``write_spec_bundle`` 相同的发布报告。
    """

    # 延迟导入避免核心模块在定义完成前形成循环依赖。
    from scripts.python.workflow.spec_document import write_spec_bundle

    # 统一委托避免两个公共入口出现行为漂移。
    return write_spec_bundle(
        spec,
        out_dir,
        source_paths=source_paths,
        language=language,
        renderer=renderer,
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
