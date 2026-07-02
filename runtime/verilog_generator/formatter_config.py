"""加载随 skill 发布的 Verilog formatter 配置。

该模块只负责读取随包携带的 formatter defaults、profile 和 template JSON，
并按 defaults -> profile -> template -> overrides 的顺序深合并配置。
调用方据此创建与 verilog-formatter 结构模型一致的本地后端。
"""

# 延迟类型注解求值，避免运行时导入额外类型依赖
from __future__ import annotations

# 标准库依赖用于读取 JSON 与定位随包配置资源
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

# 本地 formatter 后端工厂负责消费合并后的配置字典
from .formatter_backend import create_backend

# skill 主体根目录用于定位 assets 内的 formatter 配置副本
SKILL_ROOT = Path(__file__).resolve().parents[2]  # skill 主体目录

# formatter 配置目录保存 defaults、profiles 与 templates 三类 JSON
FORMATTER_CONFIG_DIR = SKILL_ROOT / "assets" / "verilog_formatter_config"  # formatter 配置根目录

# profile 目录承载命令行模式级别的默认覆盖
PROFILE_DIR = FORMATTER_CONFIG_DIR / "profiles"  # formatter profile 文件所在目录

# template 目录承载项目风格模板的最后一层覆盖
TEMPLATE_DIR = FORMATTER_CONFIG_DIR / "templates"  # 风格模板 JSON 文件所在目录

# formatter 配置错误沿用 ValueError 语义，便于 CLI 统一报告
class FormatterConfigError(ValueError):
    """表示 formatter 配置、profile 或 template 无法装配。"""

# JSON 加载保持独立函数，便于测试替换和错误定位
def load_formatter_json(path: Path) -> Any:
    """
    按 UTF-8 读取 formatter JSON 文件。

    :param path: 待读取的 JSON 文件路径。
    :return: JSON 解析得到的 Python 结构，通常为 dict。
    """

    # 统一使用 UTF-8，避免 Windows 默认编码影响随包配置
    with path.open("r", encoding="utf-8") as handle:

        # 返回原始 JSON 结构，后续合并逻辑负责解释字段
        return json.load(handle)

# 深合并只处理字典层级，其余值保持覆盖语义
def deep_merge(base: Any, override: Any) -> Any:
    """
    递归合并 formatter 配置字典。

    :param base: 作为基础层的配置结构。
    :param override: 覆盖基础层的配置结构。
    :return: 深拷贝后的合并结果，调用方可安全继续修改。
    """

    # 仅当两侧都是字典时才进入字段级合并
    if isinstance(base, dict) and isinstance(override, dict):

        # 复制基础配置，避免 profile/template 覆盖污染调用者传入对象
        dict_merged = deepcopy(base)  # 当前合并层的独立副本

        # 逐项套用覆盖配置，保持 formatter JSON 的层级覆盖语义
        for key, override_value in override.items():

            # 已存在字段继续深合并，保留 defaults/profile/template 的嵌套层级
            if key in dict_merged:

                # 递归合并当前字段，避免覆盖层抹掉同级未声明字段
                merged_value = deep_merge(dict_merged[key], override_value)  # 已有字段的递归合并值

            # 新增字段不需要合并，只需隔离调用方传入对象
            else:

                # 深拷贝新增字段，避免后续修改影响原始 override 结构
                merged_value = deepcopy(override_value)  # 新增字段的独立覆盖值

            # 写回当前字段的最终合并值
            dict_merged[key] = merged_value  # 当前字段合并后的配置值

        # 返回本层合并好的配置副本
        return dict_merged

    # 非字典节点使用覆盖值替换基础值
    return deepcopy(override)

# 命名 JSON 解析同时支持 bare name 与显式文件路径
def _resolve_named_json(name: str | None, directory: Path, kind: str) -> Path | None:
    """解析随包配置名称或外部 JSON 文件路径。

    参数:
        name: profile/template 的裸名称、JSON 文件名、显式路径或 None。
        directory: 随包受控配置目录，用于优先查找内置 JSON。
        kind: 错误消息中的配置类别名称，例如 profile 或 template。
    返回:
        解析后的 JSON 路径；name 为空时返回 None 表示跳过该配置层。
    异常:
        FormatterConfigError: 内置目录和显式路径均无法定位目标 JSON。
    """

    # 空名称表示调用方不希望叠加对应配置层
    if not name:

        # 返回 None 告知装配流程跳过这一层配置
        return None

    # 将 profile/template 名称规范化为 JSON 文件名
    path_candidate = Path(name)  # 调用方传入的名称或路径

    # 裸名称默认映射到同名 .json 配置文件
    if path_candidate.suffix != ".json":

        # 补齐后缀后继续按随包文件名和显式路径两种方式查找
        path_candidate = path_candidate.with_suffix(".json")  # 带 JSON 后缀的候选名

    # 先查随包目录，保证默认 profile/template 从 skill assets 读取
    path_direct = directory / path_candidate.name  # 随包目录下的候选文件

    # 命中随包文件时直接返回该路径
    if path_direct.exists():

        # 优先返回 assets 中的受控 profile/template 文件
        return path_direct

    # 调用方也可以传入显式 JSON 文件路径，便于本地扩展配置
    if path_candidate.is_file():

        # 显式文件路径解析为绝对路径，避免后续工作目录变化影响读取
        return path_candidate.resolve()

    # 未找到配置时保留 kind/name 信息，便于 CLI 输出可读错误
    raise FormatterConfigError(f"> ERR: [Python] formatter {kind} not found: {name}")

# formatter 配置装配是 CLI 与运行时 formatter AST 检查共享的入口
def assemble_formatter_config(
    profile: str | None = "formatter-normalize",
    template: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    组装随包 formatter 后端配置。

    :param profile: formatter profile 名称或 JSON 路径，默认使用 normalize 模式。
    :param template: formatter template 名称或 JSON 路径，用于叠加风格模板。
    :param overrides: 调用方提供的最终覆盖配置。
    :return: 已按 defaults、profile、template、overrides 顺序合并的配置字典。
    :raises FormatterConfigError: defaults、profile 或 template 无法定位时抛出。
    """

    # defaults 是所有 formatter 配置层的必需基础
    path_defaults = FORMATTER_CONFIG_DIR / "defaults.json"  # formatter 默认配置路径

    # 缺失 defaults 表示随包 assets 不完整，应阻止后端继续创建
    if not path_defaults.exists():

        # 错误消息保留路径，便于开发者定位缺失资源
        raise FormatterConfigError(f"> ERR: [Python] Vendored formatter defaults are missing: {path_defaults}")

    # 读取基础配置后再叠加 profile/template/overrides
    dict_config = load_formatter_json(path_defaults)  # 当前累计的 formatter 配置

    # profile/template 名称解析允许 None、裸名称和显式 JSON 路径
    path_profile = _resolve_named_json(profile, PROFILE_DIR, "profile")  # 命令模式配置文件路径

    # template 覆盖放在 profile 之后，保留风格模板最终调整能力
    path_template = _resolve_named_json(template, TEMPLATE_DIR, "template")  # 风格模板配置文件路径

    # profile 文件存在时先套用命令模式约定的 formatter 行为
    if path_profile is not None:

        # profile 调整默认配置中的归一化和自动修复策略
        dict_config = deep_merge(dict_config, load_formatter_json(path_profile))  # 命令模式覆盖后的配置字典

    # template 文件存在时再叠加项目风格模板的细粒度偏好
    if path_template is not None:

        # template 只改动模板关心的排版和命名偏好
        dict_config = deep_merge(dict_config, load_formatter_json(path_template))  # 风格模板调整后的配置字典

    # 调用方 overrides 拥有最高优先级，用于测试或 CLI 临时覆盖
    if overrides:

        # overrides 层保留最高优先级，支持调用方临时指定检查策略
        dict_config = deep_merge(dict_config, overrides)  # 最终覆盖后的 formatter 配置

    # 返回可直接传给 formatter backend factory 的配置字典
    return dict_config

# 后端创建函数隐藏配置装配细节，供 runtime 检查直接调用
def create_formatter_backend(
    profile: str | None = "formatter-normalize",
    template: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Any:
    """
    创建 formatter AST 检查使用的随包后端。

    :param profile: formatter profile 名称或 JSON 路径。
    :param template: formatter template 名称或 JSON 路径。
    :param overrides: 调用方提供的最终覆盖配置。
    :return: 由 formatter backend factory 创建的后端实例。
    """

    # 先组装配置，再交给后端工厂创建实际解析/渲染对象
    return create_backend(
        assemble_formatter_config(
            profile=profile,
            template=template,
            overrides=overrides,
        )
    )
