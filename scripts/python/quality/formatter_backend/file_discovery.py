"""发现 formatter 应处理的 Verilog/SystemVerilog 文件。"""

# 延迟类型注解求值，保持工具模块导入简单
from __future__ import annotations

# 标准库路径类型用于扫描文件和计算相对路径
from pathlib import Path

# include_extensions 支持用户写入 v 或 .v 两种形式
def normalize_extensions(include_extensions: list[str]) -> list[str]:
    """
    规范化 formatter 扫描扩展名。

    :param include_extensions: 配置中给出的扩展名列表。
    :return: 每个元素都带点号前缀的扩展名列表。
    """

    # 返回带点号的扩展名，供 glob/rglob 拼接模式使用
    return [extension if extension.startswith(".") else f".{extension}" for extension in include_extensions]

# 排除子路径统一转换为 tuple，方便和 Path.parts 做前缀比较
def _normalized_subpath_parts(raw_subpath: str) -> tuple[str, ...]:
    """
    将配置中的排除子路径拆成可比较的路径片段。

    :param raw_subpath: 配置文件中声明的目录或子路径文本。
    :return: 去掉空片段和当前目录标记后的路径片段元组。
    """

    # 兼容 Windows 反斜杠和配置里的正斜杠
    normalized_subpath = raw_subpath.replace("\\", "/")  # 统一分隔符后的子路径文本

    # 去掉空片段和当前目录片段，避免配置写法差异影响排除规则
    return tuple(part for part in normalized_subpath.split("/") if part and part != ".")

# 单个路径排除判断同时覆盖目录名黑名单和子路径前缀黑名单
def _is_excluded(
    path: Path,
    root: Path,
    exclude_dir_names: set[str],
    exclude_subpaths: list[tuple[str, ...]],
) -> bool:
    """
    判断候选 RTL 文件是否落在 formatter 扫描排除范围内。

    :param path: 当前扫描得到的候选文件路径。
    :param root: formatter 本轮扫描的根目录。
    :param exclude_dir_names: 需要整体跳过的目录名集合。
    :param exclude_subpaths: 需要按扫描根匹配的子路径前缀集合。
    :return: 命中任一排除规则时返回 True。
    """

    # 尽量使用相对路径比较，路径不在 root 下时退回原路径
    try:

        # 相对路径让目录名和子路径排除规则都基于扫描根解释
        relative_path = path.relative_to(root)  # 相对扫描根目录的路径

    # 路径不属于扫描根时使用原路径 parts，保持函数容错
    except ValueError:

        # 使用原始路径片段继续执行排除判断
        relative_path = path  # 无法相对化时的原始路径

    # Path.parts 是目录名和文件名组成的稳定元组
    tuple_parts = relative_path.parts  # 待检查路径片段

    # 任一父目录命中黑名单时排除该文件
    if any(part in exclude_dir_names for part in tuple_parts[:-1]):

        # 返回 True 表示该路径不应进入 formatter 输入集合
        return True

    # 子路径排除按前缀匹配，适配 reports/generated 等目录树
    for tuple_subpath_parts in exclude_subpaths:

        # 当前路径片段前缀命中配置子路径时排除
        if (
            len(tuple_parts) >= len(tuple_subpath_parts)
            and tuple(tuple_parts[: len(tuple_subpath_parts)]) == tuple_subpath_parts
        ):

            # 返回 True 表示该路径位于排除子路径下
            return True

    # 未命中任何排除规则时允许处理该文件
    return False

# 文件发现入口供 formatter CLI 和 runtime 检查共同使用
def iter_verilog_files(input_path: Path, config: dict) -> list[Path]:
    """
    根据 formatter 配置发现待处理 RTL 文件。

    :param input_path: 输入文件或目录路径。
    :param config: formatter 配置字典，包含 execution 扫描设置。
    :return: 去重并排序后的 RTL 文件绝对路径列表。
    """

    # include_extensions 控制 formatter 扫描哪些 RTL 后缀
    list_include_extensions = normalize_extensions(config["execution"]["include_extensions"])  # 扫描扩展名列表

    # 输入是文件时直接返回该文件，保持 CLI 单文件处理语义
    if input_path.is_file():

        # 返回解析后的绝对路径，避免后续工作目录变化影响读取
        return [input_path.resolve()]

    # recurse 默认为 True，兼容历史 formatter 配置行为
    bool_recurse = bool(config["execution"].get("recurse", True))  # 是否递归扫描目录

    # 根据 recurse 选择 glob 或 rglob 方法
    func_pattern_iter = input_path.rglob if bool_recurse else input_path.glob  # 目录扫描函数

    # 目录名排除集合用于快速过滤常见构建产物目录
    set_exclude_dir_names = set(config["execution"].get("workspace_scan_exclude_dir_names", []))  # 排除目录名集合

    # 子路径排除规则用于跳过工作区内的指定目录树
    list_exclude_subpaths = [
        _normalized_subpath_parts(subpath)  # 单条排除子路径的片段元组
        for subpath in config["execution"].get("workspace_scan_exclude_subpaths", [])  # 配置声明的排除子路径
    ]  # 排除子路径片段集合

    # files 收集所有扩展名扫描得到的候选 RTL 文件
    list_files: list[Path] = []  # formatter 输入文件候选集合

    # 每个扩展名单独扫描，保持配置顺序对调试可见
    for extension in list_include_extensions:

        # 追加当前扩展名匹配且未被排除的文件
        list_files.extend(
            path.resolve()
            for path in func_pattern_iter(f"*{extension}")
            if path.is_file()
            and not _is_excluded(path, input_path, set_exclude_dir_names, list_exclude_subpaths)
        )

    # 返回去重后的稳定排序结果，保证 formatter 批处理顺序可复现
    return sorted(set(list_files))
