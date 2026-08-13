"""从模型 Markdown 响应中抽取 manifest、代码块和补丁块。"""

# 延迟注解解析，保持运行时导入轻量。
from __future__ import annotations

# 标准库负责 manifest 解析、围栏匹配、路径防逃逸和轻量数据模型。
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# ExtractionError 标识模型响应无法安全抽取为文件。
class ExtractionError(ValueError):
    """表示模型响应的 manifest、代码围栏或输出路径不满足抽取契约。

    参数:
        本异常不定义额外业务参数，沿用 ValueError 的消息入参。

    返回:
        异常类本身不产生业务返回值。
    """

# FencedBlock 保存单个 Markdown fenced code block 的元信息和正文。
@dataclass(frozen=True)
class FencedBlock:
    """保存一个 fenced code block 的 info 字段和内容文本。

    参数:
        info: 代码围栏起始行中语言、path 和 patch 等声明文本。
        content: 代码围栏内部原始文本，不包含结尾围栏。

    返回:
        数据类实例用于后续 manifest 匹配、文件写出或补丁应用。
    """

    # info 保留 fence 起始行中的完整声明文本。
    info: str  # 围栏 info 字段

    # content 保留 fence 内部正文，写文件时再统一补末尾换行。
    content: str  # 围栏正文文本

    # path 属性从 info 中提取 path= 声明。
    @property

    # 属性访问时即时解析 path，避免数据类额外缓存派生字段。
    def path(self) -> str | None:
        """返回围栏 info 中声明的相对路径。

        参数:
            本属性不接收外部业务参数。

        返回:
            path= 后的相对路径；未声明时返回 None。
        """

        # path_from_info 统一处理引号裁剪和 token 查找。
        return path_from_info(self.info)

# Markdown fenced code block 使用多行正则抽取 info 和 content。
FENCE_RE = re.compile(  # Markdown fenced code block 提取模式
    r"^```(?P<info>[^\n`]*)\n(?P<content>.*?)(?:\n)?^```[ \t]*$",  # 围栏 info 与正文捕获表达式
    re.MULTILINE | re.DOTALL,  # 跨行匹配并允许正文包含换行
)

# parse_fenced_blocks 是模型响应到结构块的第一步。
def parse_fenced_blocks(text: str) -> list[FencedBlock]:
    """解析模型响应中的全部 Markdown fenced code block。

    参数:
        text: 模型返回的完整 Markdown 响应文本。

    返回:
        按出现顺序排列的 fenced code block 结构列表。
    """

    # list_blocks 保存所有 code fence 的 info 和正文，保留原始顺序。
    list_blocks = [  # 模型响应中的代码围栏块
        FencedBlock(match.group("info").strip(), match.group("content"))  # 单个围栏块
        for match in FENCE_RE.finditer(text)  # 逐个匹配 Markdown fence
    ]

    # 返回给 manifest 解析和文件块分类流程复用。
    return list_blocks

# parse_manifest 从无 path/patch 的 JSON 围栏中寻找 manifest。
def parse_manifest(text: str) -> dict[str, Any]:
    """从模型响应中提取包含 files 列表的 JSON manifest。

    参数:
        text: 模型返回的完整 Markdown 响应文本。

    返回:
        已解析的 manifest 字典，必须包含列表类型 files 字段。

    异常:
        当没有合法 manifest 时抛出 ExtractionError。
    """

    # 只扫描 fenced block，拒绝把散落正文当作 manifest。
    for block in parse_fenced_blocks(text):

        # str_language 区分 JSON manifest 与普通文件代码块。
        str_language = _language_from_info(block.info)  # 围栏声明语言

        # 非 JSON 围栏不可能是 manifest。
        if str_language != "json":

            # 跳过非 manifest 代码块。
            continue

        # 带 path 或 patch 的 JSON 块属于文件内容或补丁，不是 manifest。
        if block.path is not None or patch_marker_from_info(block.info) is not None:

            # 避免误把 JSON artifact 解析成 manifest。
            continue

        # JSON 解析失败说明该块不是可用 manifest，继续寻找下一个候选。
        try:

            # dict_candidate 是期望中的 manifest 对象，后续仍做结构校验。
            dict_candidate = json.loads(block.content)  # JSON 解码后的 manifest 候选

        # JSON 解码失败时继续寻找其它 manifest 候选。
        except json.JSONDecodeError:

            # 非法 JSON 围栏不阻断其它候选。
            continue

        # manifest 必须是对象且 files 字段为列表。
        if isinstance(dict_candidate, dict) and isinstance(dict_candidate.get("files"), list):

            # 返回第一个合法 manifest，保持旧行为。
            return dict_candidate

    # 没找到 manifest 时给出固定可见错误。
    raise ExtractionError("> ERR: [Python] Response does not contain a JSON manifest with a files list.")

# extract_response 是 CLI 和 workflow 共同使用的落盘入口。
def extract_response(text: str, out_dir: Path) -> list[Path]:
    """按照 manifest 把模型响应中的文件块和补丁块写入输出目录。

    参数:
        text: 模型返回的完整 Markdown 响应文本。
        out_dir: 所有输出文件必须落在该目录内。

    返回:
        实际写入或修改过的文件路径列表，顺序先 manifest files 后 patches。

    异常:
        manifest、围栏声明、路径安全或补丁 marker 不合法时抛出 ExtractionError。
    """

    # 响应正文不得在 code fence 外夹带解释性 prose。
    _reject_text_outside_fences(text)

    # manifest 决定允许出现和写出的文件集合。
    dict_manifest = parse_manifest(text)  # 响应中的 JSON manifest

    # 所有 fenced block 需要再次分类为文件块和补丁块。
    list_blocks = parse_fenced_blocks(text)  # 响应中的全部围栏块

    # manifest file 路径先规范化并去重。
    list_manifest_paths = _manifest_paths(dict_manifest)  # manifest 声明的普通文件路径

    # manifest patch 条目保留 path 与 marker 的组合键。
    list_patch_entries = _manifest_patches(dict_manifest)  # manifest 声明的补丁条目

    # 分类结果按路径或 path+marker 查找对应 fenced block。
    tuple_blocks = _classify_file_blocks(  # 文件围栏和补丁围栏的分类索引
        list_blocks,  # 原始响应围栏块序列
        list_manifest_paths,  # manifest 允许写出的普通文件路径
        list_patch_entries,  # manifest 允许应用的补丁条目
    )  # 普通文件块和补丁块索引

    # dict_blocks_by_path 提供普通文件路径到 fenced block 的索引。
    dict_blocks_by_path = tuple_blocks[0]  # 普通文件围栏索引

    # dict_patch_blocks 提供补丁 path/marker 到 fenced block 的索引。
    dict_patch_blocks = tuple_blocks[1]  # 补丁围栏索引

    # list_written_paths 按写入顺序记录输出文件。
    list_written_paths: list[Path] = []  # 已写入或修改的输出路径

    # 普通文件必须逐个存在对应代码围栏。
    for str_rel_path in list_manifest_paths:

        # fencedblock_file 是 manifest 普通文件对应的 fenced block。
        fencedblock_file = dict_blocks_by_path.get(str_rel_path)  # 当前 manifest 文件块

        # 缺少文件块时立即失败，避免产生半完整输出。
        if fencedblock_file is None:

            # 错误消息包含 manifest path 便于定位模型漏块。
            raise ExtractionError(
                f"> ERR: [Python] Missing fenced code block for manifest path {str_rel_path!r}."
            )

        # path_output 是经过目录逃逸检查后的最终写入路径。
        path_output = safe_output_path(out_dir, str_rel_path)  # 普通文件输出路径

        # 父目录按需创建，支持 manifest 中的子目录结构。
        path_output.parent.mkdir(parents=True, exist_ok=True)

        # 写出文件内容并保证末尾换行稳定。
        path_output.write_text(fencedblock_file.content.rstrip() + "\n", encoding="utf-8")

        # 记录写出的普通文件路径。
        list_written_paths.append(path_output)

    # patch 条目在普通文件写完后再应用，支持 patch 作用于刚写出的文件。
    for dict_patch in list_patch_entries:

        # tuple_patch_key 与分类索引保持同一规范化口径。
        tuple_patch_key = (dict_patch["path"], dict_patch["marker"])  # 补丁围栏查找使用的 path/marker 键

        # manifest 声明的 patch 必须有对应围栏。
        if tuple_patch_key not in dict_patch_blocks:

            # 错误中同时包含路径和 marker，便于模型修复。
            raise ExtractionError(
                "> ERR: [Python] Missing fenced patch block for manifest patch path "
                f"{dict_patch['path']!r} marker {dict_patch['marker']!r}."
            )

        # path_output 是补丁目标文件的安全路径。
        path_output = safe_output_path(out_dir, dict_patch["path"])  # 补丁目标输出路径

        # marker 中间内容由 fenced block 正文替换。
        _apply_patch_block(
            path_output,
            dict_patch["marker"],
            dict_patch_blocks[tuple_patch_key].content,
        )

        # 同一文件如果先写后 patch，不重复登记。
        if path_output not in list_written_paths:

            # 补丁修改过的既有文件也需要返回给调用方。
            list_written_paths.append(path_output)

    # 返回写入或修改的路径清单。
    return list_written_paths

# _manifest_paths 校验 manifest.files 并返回规范化路径。
def _manifest_paths(manifest: dict[str, Any]) -> list[str]:
    """读取 manifest.files 中的普通文件路径。

    参数:
        manifest: parse_manifest 返回的 manifest 字典。

    返回:
        去重后的相对路径列表，顺序与 manifest.files 一致。

    异常:
        文件条目缺少 path 或出现重复路径时抛出 ExtractionError。
    """

    # set_seen_paths 用于捕获 manifest 内重复声明。
    set_seen_paths: set[str] = set()  # 已出现的普通文件路径

    # list_paths 保留 manifest.files 的原始顺序。
    list_paths: list[str] = []  # 规范化普通文件路径

    # manifest.files 每项都应是带 path 的对象。
    for dict_file_entry in manifest["files"]:

        # 非对象或空 path 都说明 manifest 不完整。
        if not isinstance(dict_file_entry, dict) or not dict_file_entry.get("path"):

            # 文件条目缺少路径时阻断抽取。
            raise ExtractionError("> ERR: [Python] Every manifest file entry must contain a path.")

        # str_rel_path 统一使用 POSIX 风格相对路径。
        str_rel_path = normalize_manifest_path(str(dict_file_entry["path"]))  # manifest 普通文件路径

        # 同一路径不能出现两次。
        if str_rel_path in set_seen_paths:

            # 重复路径会导致覆盖顺序不确定。
            raise ExtractionError(f"> ERR: [Python] Duplicate manifest path {str_rel_path!r}.")

        # 记录已见路径和输出顺序。
        set_seen_paths.add(str_rel_path)

        # 保存规范化路径。
        list_paths.append(str_rel_path)

    # 返回普通文件路径序列。
    return list_paths

# _manifest_patches 校验 manifest.patches 并返回规范化条目。
def _manifest_patches(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """读取 manifest.patches 中的补丁路径和 marker。

    参数:
        manifest: parse_manifest 返回的 manifest 字典。

    返回:
        每项包含 path 和 marker 的补丁条目列表。

    异常:
        patches 字段类型错误、条目缺字段或重复 path/marker 时抛出 ExtractionError。
    """

    # set_seen_keys 用于阻止同一 path/marker 重复应用。
    set_seen_keys: set[tuple[str, str]] = set()  # 已出现的补丁键

    # list_patches 按 manifest 补丁声明顺序保存规范化条目。
    list_patches: list[dict[str, str]] = []  # 规范化补丁条目

    # obj_raw_patches 允许缺失或 None，表示没有 patch。
    obj_raw_patches = manifest.get("patches", [])  # manifest 原始 patches 字段

    # None 与缺失字段等价于空补丁列表。
    if obj_raw_patches is None:

        # 返回空集合，保持旧兼容行为。
        return list_patches

    # patches 字段必须是列表。
    if not isinstance(obj_raw_patches, list):

        # 非列表 patches 会让后续语义不明确。
        raise ExtractionError("> ERR: [Python] Manifest patches must be a list when present.")

    # 每个 patch 条目必须提供 path 和 marker。
    for dict_patch in obj_raw_patches:

        # 非对象或缺字段时拒绝抽取。
        if not isinstance(dict_patch, dict) or not dict_patch.get("path") or not dict_patch.get("marker"):

            # patch 需要同时定位文件和 marker 区间。
            raise ExtractionError("> ERR: [Python] Every manifest patch entry must contain path and marker.")

        # str_rel_path 与普通文件路径使用同一规范化函数。
        str_rel_path = normalize_manifest_path(str(dict_patch["path"]))  # patch 目标相对路径

        # str_marker 限制为安全 token，避免替换边界匹配歧义。
        str_marker = normalize_patch_marker(str(dict_patch["marker"]))  # patch 替换区间标识

        # tuple_key 用于阻止同一目标区间被 manifest 重复声明。
        tuple_key = (str_rel_path, str_marker)  # manifest patch 唯一性键

        # 同一补丁键只能声明一次。
        if tuple_key in set_seen_keys:

            # 重复 patch 会导致替换内容不确定。
            raise ExtractionError(
                f"> ERR: [Python] Duplicate manifest patch path {str_rel_path!r} marker {str_marker!r}."
            )

        # 记录已见补丁键。
        set_seen_keys.add(tuple_key)

        # 保存规范化补丁条目。
        list_patches.append({"path": str_rel_path, "marker": str_marker})

    # 返回补丁条目序列。
    return list_patches

# _classify_file_blocks 把 fenced block 索引到普通文件或补丁。
def _classify_file_blocks(
    blocks: list[FencedBlock],
    manifest_paths: list[str],
    patch_entries: list[dict[str, str]],
) -> tuple[dict[str, FencedBlock], dict[tuple[str, str], FencedBlock]]:
    """按照 manifest 声明分类模型响应中的文件围栏。

    参数:
        blocks: parse_fenced_blocks 返回的全部围栏块。
        manifest_paths: manifest.files 中允许的普通文件路径。
        patch_entries: manifest.patches 中允许的 path/marker 条目。

    返回:
        普通文件索引和补丁索引组成的二元组。

    异常:
        围栏缺少 path、重复声明或未在 manifest 中声明时抛出 ExtractionError。
    """

    # set_manifest_paths 用于常量时间判断文件块是否被声明。
    set_manifest_paths = set(manifest_paths)  # manifest 允许的普通文件路径集合

    # set_patch_keys 用于判断补丁围栏是否被 manifest 声明。
    set_patch_keys = {  # manifest 中允许出现的补丁围栏键
        (dict_patch["path"], dict_patch["marker"])  # 单个补丁声明的 path/marker 组合
        for dict_patch in patch_entries  # 遍历 manifest.patches 条目
    }  # manifest 允许的补丁键集合

    # dict_blocks_by_path 保存普通文件围栏。
    dict_blocks_by_path: dict[str, FencedBlock] = {}  # 普通文件 path 到围栏块

    # dict_patch_blocks 保存补丁围栏。
    dict_patch_blocks: dict[tuple[str, str], FencedBlock] = {}  # patch 键到围栏块

    # 逐个围栏分类，manifest JSON 围栏会被跳过。
    for block in blocks:

        # str_language 用于把 manifest JSON 围栏排除出文件分类。
        str_language = _language_from_info(block.info)  # 当前围栏语言声明

        # 没有 path/patch 的 JSON 围栏就是 manifest，不参与文件分类。
        if str_language == "json" and not block.path and not patch_marker_from_info(block.info):

            # manifest 围栏已经由 parse_manifest 消费。
            continue

        # str_block_path 是普通文件或补丁目标路径。
        str_block_path = block.path  # 围栏声明的 path

        # 文件围栏必须声明 path。
        if not str_block_path:

            # 缺少 path 时无法安全落盘。
            raise ExtractionError(
                f"> ERR: [Python] File code block is missing a path=<relative/path> fence info: {block.info!r}."
            )

        # str_rel_path 与 manifest 路径使用同一规范化口径。
        str_rel_path = normalize_manifest_path(str_block_path)  # 围栏声明的规范化路径

        # str_patch_marker 不为空时说明该围栏需要替换目标 marker 区间。
        str_patch_marker = patch_marker_from_info(block.info)  # 当前围栏补丁 marker

        # patch 围栏按 path+marker 分类。
        if str_patch_marker:

            # tuple_patch_key 是补丁围栏的规范化索引键。
            tuple_patch_key = (str_rel_path, normalize_patch_marker(str_patch_marker))  # 围栏补丁键

            # 同一补丁围栏只能出现一次。
            if tuple_patch_key in dict_patch_blocks:

                # 重复补丁围栏会导致应用内容不确定。
                raise ExtractionError(
                    f"> ERR: [Python] Duplicate code fence patch path {str_rel_path!r} marker {tuple_patch_key[1]!r}."
                )

            # 未在 manifest 声明的补丁围栏不允许落盘。
            if tuple_patch_key not in set_patch_keys:

                # 阻止模型额外修改未授权 marker。
                raise ExtractionError(
                    "> ERR: [Python] Code fence patch path "
                    f"{str_rel_path!r} marker {tuple_patch_key[1]!r} is not declared in manifest."
                )

            # 登记合法补丁围栏。
            dict_patch_blocks[tuple_patch_key] = block  # 合法补丁围栏索引项

            # 当前围栏已分类，继续下一个。
            continue

        # 普通文件围栏路径不能重复。
        if str_rel_path in dict_blocks_by_path:

            # 重复文件围栏会导致写入内容不确定。
            raise ExtractionError(f"> ERR: [Python] Duplicate code fence path {str_rel_path!r}.")

        # 普通文件必须在 manifest.files 中声明。
        if str_rel_path not in set_manifest_paths:

            # 阻止模型写出 manifest 外文件。
            raise ExtractionError(f"> ERR: [Python] Code fence path {str_rel_path!r} is not declared in manifest.")

        # 登记合法普通文件围栏。
        dict_blocks_by_path[str_rel_path] = block  # 合法普通文件围栏索引项

    # 返回两个索引供 extract_response 写文件和应用补丁。
    return dict_blocks_by_path, dict_patch_blocks

# _reject_text_outside_fences 防止模型在围栏外夹带解释文本。
def _reject_text_outside_fences(text: str) -> None:
    """确认模型响应除 fenced code block 外没有 prose 文本。

    参数:
        text: 模型返回的完整 Markdown 响应文本。

    返回:
        没有业务返回值；发现围栏外文本时抛出异常。

    异常:
        当围栏外存在非空文本时抛出 ExtractionError。
    """

    # int_cursor 记录上一个围栏的结束位置。
    int_cursor = 0  # 当前已扫描文本偏移

    # list_outside_parts 收集所有围栏外片段。
    list_outside_parts: list[str] = []  # 围栏外文本片段

    # 每个 fence 前面的文本都属于围栏外片段。
    for match in FENCE_RE.finditer(text):

        # 保存当前 fence 之前的 prose 片段。
        list_outside_parts.append(text[int_cursor : match.start()])

        # 游标推进到当前 fence 之后。
        int_cursor = match.end()  # 下一个围栏外片段的起始偏移

    # 最后一个 fence 之后的文本也需要检查。
    list_outside_parts.append(text[int_cursor:])

    # str_outside 是所有围栏外文本拼接后的可见内容。
    str_outside = "".join(list_outside_parts).strip()  # 去空白后的围栏外文本

    # 存在 prose 时拒绝抽取，避免模型解释混入产物。
    if str_outside:

        # str_first_line 提供最短定位信息。
        str_first_line = str_outside.splitlines()[0].strip()  # 围栏外首行文本

        # 错误消息包含首行，方便上游提示模型重试。
        raise ExtractionError(
            f"> ERR: [Python] Response contains prose outside fenced code blocks: {str_first_line!r}."
        )

# path_from_info 是外部可用的 path= 解析 helper。
def path_from_info(info: str) -> str | None:
    """从 fenced code block 的 info 文本中提取 path= 值。

    参数:
        info: Markdown code fence 起始行中的声明文本。

    返回:
        path= 后的原始值；不存在时返回 None。
    """

    # path 字段允许带外层引号，这里只取出模型声明的相对文件名。
    return _value_from_info(info, "path")

# patch_marker_from_info 为补丁围栏提取目标 marker 名称。
def patch_marker_from_info(info: str) -> str | None:
    """从 fenced code block 的 info 文本中提取 patch= marker。

    参数:
        info: Markdown code fence 起始行中的声明文本。

    返回:
        patch= 后的 marker；不存在时返回 None。
    """

    # patch 字段只暴露 marker 名称，后续再做字符集合校验。
    return _value_from_info(info, "patch")

# _value_from_info 提取 info 中指定 key 的等号值。
def _value_from_info(info: str, key: str) -> str | None:
    """从 fence info 的空格分隔 token 中提取 key=value。

    参数:
        info: Markdown code fence 起始行中的声明文本。
        key: 需要提取的字段名，例如 path 或 patch。

    返回:
        去掉外层单双引号后的字段值；未找到时返回 None。
    """

    # 空 info 没有任何 key=value 声明。
    if not info:

        # 未声明字段时返回 None。
        return None

    # 逐个 token 查找目标 key。
    for str_token in info.split():

        # 只接受 key= 前缀，避免误匹配语言名。
        if str_token.startswith(f"{key}="):

            # str_raw_value 保留等号右侧值，随后裁剪引号。
            str_raw_value = str_token.split("=", 1)[1]  # key 对应的原始 token 值

            # 返回去掉外层引号后的值。
            return str_raw_value.strip("\"'")

    # 未找到目标 key。
    return None

# normalize_manifest_path 校验 manifest 和 fence 中的相对路径文本。
def normalize_manifest_path(path: str | None) -> str:
    """规范化 manifest 或 fence 中声明的相对 POSIX 路径。

    参数:
        path: manifest path 字段或 fence info 中的 path= 值。

    返回:
        去除首尾空白后的相对路径文本。

    异常:
        缺少路径、使用反斜杠或清理后为空时抛出 ExtractionError。
    """

    # None 和空字符串都不能定位输出文件。
    if not path:

        # 路径缺失时阻断抽取。
        raise ExtractionError("> ERR: [Python] Path is required.")

    # Windows 反斜杠会绕开 POSIX manifest 约定。
    if "\\" in path:

        # manifest 路径必须跨平台稳定。
        raise ExtractionError(f"> ERR: [Python] Path must use forward slashes, got {path!r}.")

    # str_raw_path 去除模型可能添加的外侧空白。
    str_raw_path = path.strip()  # 清理后的 manifest 路径

    # 清理后为空仍然非法。
    if not str_raw_path:

        # 空路径无法安全落盘。
        raise ExtractionError("> ERR: [Python] Path must not be empty.")

    # 返回供 safe_output_path 继续做绝对路径和 .. 检查。
    return str_raw_path

# normalize_patch_marker 校验 patch marker 的字符集合。
def normalize_patch_marker(marker: str | None) -> str:
    """规范化 manifest 或 fence 中声明的 patch marker。

    参数:
        marker: manifest patch marker 或 fence info 中的 patch= 值。

    返回:
        去除首尾空白后的 marker 字符串。

    异常:
        缺少 marker 或包含不支持字符时抛出 ExtractionError。
    """

    # marker 是定位替换区间的必需标识。
    if not marker:

        # 缺失 marker 时无法应用 patch。
        raise ExtractionError("> ERR: [Python] Patch marker is required.")

    # str_cleaned_marker 去除外侧空白，保留内部合法符号。
    str_cleaned_marker = marker.strip()  # 清理后的 patch marker

    # marker 只允许安全 token 字符。
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", str_cleaned_marker):

        # 不支持字符会破坏 marker 查找或报告可读性。
        raise ExtractionError(f"> ERR: [Python] Patch marker contains unsupported characters: {marker!r}.")

    # 返回规范化 marker。
    return str_cleaned_marker

# safe_output_path 确保 manifest 路径不会逃逸输出目录。
def safe_output_path(out_dir: Path, relative_path: str) -> Path:
    """把 manifest 相对路径解析为输出目录内的安全绝对路径。

    参数:
        out_dir: 允许写出的输出目录根。
        relative_path: manifest 或 patch 声明的相对路径。

    返回:
        经过 resolve 和 relative_to 校验的目标路径。

    异常:
        绝对路径、盘符路径、点段路径或目录逃逸路径会抛出 ExtractionError。
    """

    # str_normalized_path 先执行 manifest 路径基础规范化。
    str_normalized_path = normalize_manifest_path(relative_path)  # 规范化相对路径文本

    # path_posix 用于按 manifest 的 POSIX slash 语义拆分路径。
    path_posix = PurePosixPath(str_normalized_path)  # POSIX 相对路径对象

    # path_windows 用于识别 Windows 绝对路径和盘符。
    path_windows = PureWindowsPath(str_normalized_path)  # Windows 路径检查对象

    # 绝对路径和 Windows 盘符都不允许写出。
    if path_posix.is_absolute() or path_windows.is_absolute() or path_windows.drive:

        # 拒绝任何不受 out_dir 约束的路径。
        raise ExtractionError(f"> ERR: [Python] Refusing absolute output path {relative_path!r}.")

    # 空段、当前目录和上级目录会造成歧义或逃逸。
    if any(str_part in ("", ".", "..") for str_part in path_posix.parts):

        # 拒绝危险路径片段。
        raise ExtractionError(f"> ERR: [Python] Refusing unsafe output path {relative_path!r}.")

    # path_root 是输出目录的真实路径。
    path_root = out_dir.resolve()  # 规范化输出目录根

    # path_candidate 是拼接 manifest 路径后的真实目标。
    path_candidate = (path_root / Path(*path_posix.parts)).resolve()  # 候选输出文件路径

    # 目录逃逸检查确保 candidate 位于 root 下。
    try:

        # relative_to 成功表示目标没有逃逸 out_dir。
        path_candidate.relative_to(path_root)

    # resolve 后不在输出目录内时按目录逃逸处理。
    except ValueError as exc:

        # resolve 后仍不在输出目录内时阻断。
        raise ExtractionError(
            f"> ERR: [Python] Refusing path outside output directory: {relative_path!r}."
        ) from exc

    # 返回安全候选路径。
    return path_candidate

# _apply_patch_block 根据 begin/end marker 替换目标文件中的片段。
def _apply_patch_block(path: Path, marker: str, content: str) -> None:
    """把 patch fenced block 的正文替换到目标文件 marker 区间。

    参数:
        path: 需要应用补丁的目标文件路径。
        marker: manifest 和 fence 共同声明的 patch marker。
        content: 用于替换 begin/end marker 中间区域的文本。

    返回:
        没有业务返回值；目标文件会被原地更新。

    异常:
        目标文件不存在、marker 数量异常或 begin/end 顺序错误时抛出 ExtractionError。
    """

    # patch 只能应用到已经存在的文件。
    if not path.exists():

        # 目标不存在时不创建新文件，避免 patch 语义漂移。
        raise ExtractionError(f"> ERR: [Python] Patch target file does not exist: {path}")

    # str_text 读取完整目标文本，后续按行替换 marker 区间。
    str_text = path.read_text(encoding="utf-8")  # patch 目标文件原文

    # list_lines 保留原文件逐行内容。
    list_lines = str_text.splitlines()  # patch 目标文件行序列

    # str_begin_token 定位允许被 patch 替换区间的起始边界。
    str_begin_token = f"VERILOG-GEN-PATCH-BEGIN {marker}"  # patch 区间起始边界

    # str_end_token 定位允许被 patch 替换区间的结束边界。
    str_end_token = f"VERILOG-GEN-PATCH-END {marker}"  # patch 区间结束边界

    # list_begin_indices 收集所有起始边界所在行，必须最终唯一。
    list_begin_indices = [  # patch 起始边界行索引集合
        int_index  # 命中起始边界的文件行索引
        for int_index, str_line in enumerate(list_lines)  # 扫描补丁目标文件的候选结束行
        if str_begin_token in str_line  # 当前行包含起始边界文本
    ]

    # list_end_indices 收集所有结束边界所在行，必须最终唯一。
    list_end_indices = [  # patch 结束边界行索引集合
        int_index  # 命中结束边界的文件行索引
        for int_index, str_line in enumerate(list_lines)  # 遍历目标文件原始行
        if str_end_token in str_line  # 当前行包含结束边界文本
    ]

    # begin 和 end marker 都必须唯一。
    if len(list_begin_indices) != 1 or len(list_end_indices) != 1:

        # marker 数量异常会让替换范围不确定。
        raise ExtractionError(
            "> ERR: [Python] Patch marker "
            f"{marker!r} must appear exactly once as begin and end markers in {path.name}."
        )

    # int_begin_index 是唯一可替换区间起始边界行。
    int_begin_index = list_begin_indices[0]  # patch 起始边界行索引

    # int_end_index 是唯一可替换区间结束边界行。
    int_end_index = list_end_indices[0]  # patch 结束边界行索引

    # begin 必须在 end 之前。
    if int_begin_index >= int_end_index:

        # 反向 marker 会导致替换范围非法。
        raise ExtractionError(
            f"> ERR: [Python] Patch marker {marker!r} has an invalid begin/end order in {path.name}."
        )

    # list_replacement_lines 去除末尾空白后按行替换 marker 中间内容。
    list_replacement_lines = content.rstrip().splitlines()  # patch 替换正文行

    # list_updated_lines 保留 begin/end marker 行，只替换二者之间的内容。
    list_updated_lines = [  # 保留 marker 边界并替换中间内容后的文件行
        *list_lines[: int_begin_index + 1],  # begin marker 及其之前的原文件行
        *list_replacement_lines,  # fenced patch 正文拆出的替换行
        *list_lines[int_end_index:],  # end marker 及其之后的原文件行
    ]  # 应用 patch 后的目标文件行序列

    # 写回目标文件并统一末尾换行。
    path.write_text("\n".join(list_updated_lines) + "\n", encoding="utf-8")

# _language_from_info 解析 fence info 中的语言 token。
def _language_from_info(info: str) -> str:
    """返回 fence info 第一个 token 表示的语言名。

    参数:
        info: Markdown code fence 起始行中的声明文本。

    返回:
        小写语言 token；info 为空时返回空字符串。
    """

    # 空 info 没有语言声明。
    if not info:

        # 返回空字符串便于直接比较。
        return ""

    # str_language 只取第一个 token，避免 path/patch 干扰语言判断。
    str_language = info.split(maxsplit=1)[0].lower()  # 判断 manifest JSON 围栏时使用的语言 token

    # 返回规范化语言名。
    return str_language
