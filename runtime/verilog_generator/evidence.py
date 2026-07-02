"""把规格文档转换为可追踪的 evidence 索引。"""

# 延迟注解解析，避免运行期处理复杂泛型类型。
from __future__ import annotations

# 标准库依赖负责 JSON sidecar、轻量文本清理和工作区路径约束。
import json
import re
from pathlib import Path
from typing import Any

# 规格模块提供对外异常类型和稳定名称规范化规则。
from .spec import SpecError, sanitize_name

# 证据入口只接受轻量文本格式，避免把二进制或复杂文档误纳入索引。
SUPPORTED_SOURCE_SUFFIXES = (".md", ".txt", ".tex")  # 允许纳入 evidence 索引的文本规格格式

# 公开入口把规格源文件和可选 sidecar 转成 evidence JSON 结构。
def ingest_sources(
    sources: list[Path],
    *,
    root: Path | None = None,
    sidecars: list[Path] | None = None,
) -> dict[str, Any]:
    """读取本地规格源并生成 evidence 索引。

    参数:
        sources: 用户指定的文本规格路径或 glob 模式。
        root: 路径安全边界；缺省为当前工作目录。
        sidecars: 可选 JSON sidecar，用于补充人工整理的证据条目。

    返回:
        版本为 2 的 evidence 字典，包含 sources 和 items 两个对外字段。

    异常:
        SpecError: 当输入路径、文件格式或 sidecar 内容不符合 evidence 约束时抛出。
    """

    # root 是所有输入路径必须留在其中的安全边界。
    path_base = (root or Path.cwd()).resolve()  # evidence 工作区根目录

    # glob 展开后去重，保持输入顺序和文件系统排序稳定。
    list_expanded_sources = _expand_sources(sources, path_base)  # 待读取源文件

    # 没有命中文件时直接报错，避免后续生成空 evidence 误导规划阶段。
    if not list_expanded_sources:

        # SpecError 是 CLI 入口统一捕获并展示给用户的异常类型。
        raise SpecError("> ERR: [Python] No source files matched.")

    # sources 字段记录每个输入文档的稳定 id、相对路径和格式。
    list_documents: list[dict[str, Any]] = []  # 输出 artifact 的文档来源清单

    # items 字段承载可被规划阶段匹配的段落证据。
    list_items: list[dict[str, Any]] = []  # 输出 artifact 的段落证据清单

    # 文档 id 需要全局唯一，避免同名文件产生引用歧义。
    set_used_ids: set[str] = set()  # 已分配文档 id

    # 逐个源文件提取段落块，文件级异常会带出用户传入的路径。
    for source_path in list_expanded_sources:

        # 所有源文件都必须存在且位于 root 内部。
        path_resolved = _require_inside_root(source_path, path_base)  # 受信源文件路径

        # 后缀控制清理规则，也用于 sources.kind。
        str_suffix = path_resolved.suffix.lower()  # 源文件扩展名

        # 非支持格式直接拒绝，防止把未知格式当普通文本吞掉。
        if str_suffix not in SUPPORTED_SOURCE_SUFFIXES:

            # 错误消息保留支持列表，方便用户快速改正输入。
            str_supported = ", ".join(SUPPORTED_SOURCE_SUFFIXES)  # 支持的后缀列表

            # 抛出规格错误，维持既有 CLI 失败语义。
            raise SpecError(f"> ERR: [Python] Unsupported source type {str_suffix!r}; expected one of {str_supported}.")

        # document_id 由文件 stem 派生，并在冲突时追加编号。
        str_document_id = _unique_document_id(path_resolved, set_used_ids)  # 稳定文档 id

        # 对外记录相对路径，避免 evidence 泄漏本机绝对目录。
        str_relative_path = path_resolved.relative_to(path_base).as_posix()  # 文档相对路径

        # 文档索引字段形状属于 artifact 兼容面，不能随内部重构改名。
        list_documents.append(
            {
                "source_id": str_document_id,  # 文档级来源 id
                "path": str_relative_path,  # 工作区相对路径
                "kind": str_suffix.lstrip("."),  # 文档格式
            }
        )

        # 段落按文档内顺序编号，保证引用 id 可复现。
        for block_index, dict_block in enumerate(_paragraph_blocks(path_resolved), start=1):

            # 段落条目只保存轻量定位和清理后的文本。
            list_items.append(
                {
                    "source_id": f"{str_document_id}:p{block_index:03d}",  # 段落来源 id
                    "document_id": str_document_id,  # 所属文档 id
                    "location": f"{str_relative_path}:{dict_block['start_line']}-{dict_block['end_line']}",  # 行号范围
                    "text": dict_block["text"],  # 清理后的段落文本
                }
            )

    # sidecar 条目追加在源文件段落之后，保留人工补充证据的优先顺序。
    list_sidecar_items = _load_sidecar_items(sidecars or [], path_base)  # sidecar 证据条目

    # extend 保持 sources 和 sidecars 的合并顺序。
    list_items.extend(list_sidecar_items)

    # 返回字段和 version 维持历史 artifact 语义。
    return {"version": 2, "sources": list_documents, "items": list_items}

# 文本证据匹配入口根据 token 重叠返回最相关证据引用。
def evidence_refs_for_text(evidence: dict[str, Any] | None, text: str, *, limit: int = 2) -> list[dict[str, str]]:
    """为需求文本查找重叠度最高的 evidence 引用。

    参数:
        evidence: ingest_sources 生成的索引；None 或空字典表示无证据。
        text: 待匹配的行为、约束或测试意图文本。
        limit: 最多返回的引用数量。

    返回:
        每个引用包含 source_id、location 和 kind，供计划条目写入 evidence 字段。
    """

    # 没有证据索引时保持宽容，调用方无需额外分支。
    if not evidence:

        # 空列表表示没有可附加来源。
        return []

    # 查询 token 使用和索引文本相同的轻量规则。
    set_query_tokens = _tokens(text)  # 查询 token 集合

    # 空文本或无有效 token 时不返回弱匹配。
    if not set_query_tokens:

        # 空列表避免为无语义文本附加随机证据。
        return []

    # scored 保存 overlap 分数和原始 item，排序后再裁剪 limit。
    list_scored: list[tuple[int, dict[str, Any]]] = []  # 待排序的证据候选及重叠分数

    # evidence.items 可能来自用户 JSON，因此逐项检查类型。
    for item in evidence.get("items", []):

        # 非 dict 条目无法读取 text/source_id，直接跳过。
        if not isinstance(item, dict):

            # 宽容跳过异常条目，避免单个污染项阻断全部规划。
            continue

        # token 交集个数作为稳定且可解释的轻量相关度。
        int_overlap = len(set_query_tokens.intersection(_tokens(str(item.get("text", "")))))  # 查询文本与证据正文的 token 重叠数

        # 只有存在重叠的段落才进入候选。
        if int_overlap:

            # 保留原始 item，后续引用字段从中读取。
            list_scored.append((int_overlap, item))

    # 分数降序、source_id 升序，保证相同输入输出顺序可复现。
    list_scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("source_id", ""))))

    # refs 只暴露下游需要的轻量引用字段。
    list_refs: list[dict[str, str]] = []  # evidence 引用列表

    # 按 limit 裁剪，保持调用方可控的 prompt 体积。
    for _, item in list_scored[:limit]:

        # kind 缺省为 text，兼容旧 sidecar 和段落条目。
        list_refs.append(
            {
                "source_id": str(item.get("source_id", "")),  # 证据来源 id
                "location": str(item.get("location", "")),  # 证据定位
                "kind": str(item.get("kind", "text")),  # 证据类型
            }
        )

    # 返回按相关度排序的引用列表。
    return list_refs

# 源路径展开器支持普通路径和相对 root 的 glob。
def _expand_sources(sources: list[Path], root: Path) -> list[Path]:
    """展开 evidence 源路径和 glob 模式。

    参数:
        sources: 用户传入的普通路径或 glob 模式列表。
        root: 相对路径和 glob 展开的工作区根目录。

    返回:
        返回按输入顺序去重后的解析路径列表。
    """

    # expanded 保留首次出现顺序，seen 用于去重。
    list_expanded: list[Path] = []  # 展开后的源文件列表

    # resolved Path 作为去重键，避免同一文件被多种相对路径重复读取。
    set_seen: set[Path] = set()  # 已加入的解析路径

    # 逐个用户输入展开 glob 或普通路径。
    for source_path in sources:

        # glob 检测沿用 pathlib 支持的通配字符集合。
        str_raw_source = str(source_path)  # 用户输入路径文本

        # glob 模式只在 root 下展开，普通相对路径也挂到 root 下。
        if any(char in str_raw_source for char in "*?[]"):

            # sorted 让文件系统返回顺序不会影响 evidence artifact。
            list_matches = sorted(root.glob(str_raw_source))  # glob 命中的路径

        # 普通路径不执行 glob，只按 root 边界补全相对路径。
        else:

            # 绝对路径保持原样，相对路径以 root 为基准。
            list_matches = [source_path if source_path.is_absolute() else root / source_path]  # 普通路径候选

        # 将候选解析为绝对路径并去重。
        for match_path in list_matches:

            # resolve 统一符号链接和相对路径表现。
            path_resolved = match_path.resolve()  # 去重用解析路径

            # 首次出现的路径才进入结果，保持稳定顺序。
            if path_resolved not in set_seen:

                # 记录解析路径，避免后续重复加入。
                set_seen.add(path_resolved)

                # 输出解析后的路径，后续安全检查会再次确认 root 边界。
                list_expanded.append(path_resolved)

    # 返回展开后的候选路径。
    return list_expanded

# 路径安全检查器确保输入文件存在且没有越过 root。
def _require_inside_root(path: Path, root: Path) -> Path:
    """校验输入路径存在且位于 workspace root 内。

    参数:
        path: 用户提供或展开后的待读取路径。
        root: 允许读取 evidence 源的工作区根目录。

    返回:
        返回解析后且已确认位于 root 内部的文件路径。

    异常:
        SpecError: 当路径越过 root 或不是普通文件时抛出。
    """

    # 统一解析后再做 relative_to，避免 ../ 绕过边界。
    path_resolved = path.resolve()  # 解析后的输入路径

    # relative_to 是本模块的核心目录边界检查。
    try:

        # relative_to 调用只用于验证解析路径仍在 root 内。
        path_resolved.relative_to(root)

    # 越界路径不能纳入 evidence 索引。
    except ValueError as exc:

        # 使用用户传入的路径写入诊断，便于定位错误输入。
        raise SpecError(f"> ERR: [Python] Source path is outside the current workspace: {path}") from exc

    # 只接受真实文件，不把目录当成文本源读取。
    if not path_resolved.is_file():

        # 文件缺失或目录输入都使用同一类 SpecError。
        raise SpecError(f"> ERR: [Python] Source path is not a file: {path}")

    # 返回已解析且受 root 保护的文件路径。
    return path_resolved

# 文档 id 分配器基于文件名生成唯一 source_id。
def _unique_document_id(path: Path, used_ids: set[str]) -> str:
    """为 evidence 文档分配稳定唯一 id。

    参数:
        path: 当前正在索引的规格源文件路径。
        used_ids: 本次 ingest 已经分配过的文档 id 集合。

    返回:
        返回基于文件名且必要时追加编号的唯一文档 id。
    """

    # sanitize_name 保持和规格模块一致的名称安全规则。
    str_base_id = sanitize_name(path.stem).lower()  # 文档 id 基础名

    # candidate 会在冲突时追加 _2、_3 等后缀。
    str_candidate_id = str_base_id  # 当前候选文档 id

    # 编号从 2 开始，保留首个文件使用裸 stem 的历史行为。
    int_suffix_index = 2  # 冲突后缀编号

    # 循环直到找到未使用的 id。
    while str_candidate_id in used_ids:

        # 冲突编号保持可读，不引入哈希以便人工追踪。
        str_candidate_id = f"{str_base_id}_{int_suffix_index}"  # 冲突后的候选文档 id

        # 下一次冲突继续递增编号。
        int_suffix_index += 1  # 下一轮冲突检查使用的后缀编号

    # 记录已分配 id，供后续同名文件检查。
    used_ids.add(str_candidate_id)

    # 返回当前文件的唯一文档 id。
    return str_candidate_id

# 段落切分器把连续非空行压成单个 evidence item。
def _paragraph_blocks(path: Path) -> list[dict[str, Any]]:
    """把文本源文件切分为带行号的段落块。

    参数:
        path: 已通过边界校验的 evidence 文本源文件。

    返回:
        返回按源文件顺序排列的段落块列表。
    """

    # blocks 按文档顺序保留段落文本和起止行号。
    list_blocks: list[dict[str, Any]] = []  # 段落块列表

    # current 累积当前段落中已清理的行。
    list_current_lines: list[str] = []  # 当前段落行

    # start_line 在遇到段落首行时更新。
    int_start_line = 1  # 当前段落起始行

    # 文本源按 UTF-8 尝试读取，非法字符忽略以兼容外部资料。
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):

        # 不同格式在进入段落前先做轻量清理。
        str_cleaned_line = _clean_line(raw_line, path.suffix.lower())  # 清理后的行文本

        # 空行标记段落结束。
        if not str_cleaned_line:

            # 只有当前段落已有内容时才生成块。
            if list_current_lines:

                # end_line 使用空行前一行，保留源文档定位。
                list_blocks.append(_make_block(list_current_lines, int_start_line, line_number - 1))

                # 清空当前段落，等待下一段开始。
                list_current_lines = []  # 下一段重新累积清理后的文本行

            # 空行本身不进入证据文本。
            continue

        # 第一个非空行决定段落起始行号。
        if not list_current_lines:

            # 记录当前段落首行位置。
            int_start_line = line_number  # 当前段落的源文件首行号

        # 将清理后的文本加入当前段落。
        list_current_lines.append(str_cleaned_line)

    # 文件结束时收尾最后一个未闭合段落。
    if list_current_lines:

        # 旧逻辑以有效清理行数计算末行；这里保持同样语义。
        int_end_line = int_start_line + len(list_current_lines) - 1  # 文件末段结束行

        # 将末段追加到输出块列表。
        list_blocks.append(_make_block(list_current_lines, int_start_line, int_end_line))

    # 返回文档内所有段落块。
    return list_blocks

# 段落块构造器统一压缩空白并保留行号范围。
def _make_block(lines: list[str], start_line: int, end_line: int) -> dict[str, Any]:
    """构造单个 evidence 段落块。

    参数:
        lines: 已清理但尚未合并的段落行文本。
        start_line: 段落在源文件中的起始行号。
        end_line: 段落在源文件中的结束行号。

    返回:
        返回包含 start_line、end_line 和 text 的段落块字典。
    """

    # 多行段落合并为单行文本，减少 prompt 中无意义换行。
    str_text = re.sub(r"\s+", " ", " ".join(lines)).strip()  # 段落文本

    # 字段名保持 ingest_sources 输出兼容。
    return {"start_line": start_line, "end_line": end_line, "text": str_text}

# 单行清理器为 Markdown 和 LaTeX 去除最常见标记噪声。
def _clean_line(line: str, suffix: str) -> str:
    """清理单行 Markdown、LaTeX 或纯文本证据。

    参数:
        line: 源文件中的原始单行文本。
        suffix: 源文件的小写扩展名。

    返回:
        返回去除常见标记并压缩空白后的文本。
    """

    # 所有格式都先去掉首尾空白。
    str_text = line.strip()  # 当前行文本

    # LaTeX 清理只做浅层命令剥离，不尝试完整解析 TeX。
    if suffix == ".tex":

        # 百分号注释去除时保留转义百分号。
        str_text = re.sub(r"(?<!\\)%.*", "", str_text).strip()  # 去掉未转义百分号后的行文本

        # section 标题保留语义前缀，方便后续证据匹配。
        str_text = re.sub(r"\\(?:sub)*section\*?\{([^{}]+)\}", r"section: \1", str_text)  # 标题命令正文

        # 连续剥离最多三层常见命令包裹，避免复杂正则无限循环。
        str_command_pattern = r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}"  # 一层 LaTeX 命令包装模式

        # 固定迭代次数避免复杂嵌套命令造成清理循环失控。
        for _ in range(3):

            # 命令参数中的正文保留，命令名本身丢弃。
            str_text = re.sub(str_command_pattern, r"\1", str_text)  # 剥离一层 LaTeX 命令包装后的文本

        # 删除剩余裸命令名和花括号。
        str_text = re.sub(r"\\[A-Za-z]+\*?", "", str_text)  # 删除残留 LaTeX 命令后的文本

        # 花括号对证据匹配帮助很小，统一去掉。
        str_text = str_text.replace("{", "").replace("}", "")  # 去掉分组花括号后的文本

    # Markdown 只需要去掉标题前缀，正文标记交给轻量 token 规则处理。
    elif suffix == ".md":

        # Markdown 标题标记去除后保留标题文本。
        str_text = re.sub(r"^#+\s*", "", str_text)  # 去掉 Markdown 标题井号后的文本

    # 最终压缩空白，保证 token 匹配输入稳定。
    return re.sub(r"\s+", " ", str_text).strip()

# token 提取器为轻量 evidence 匹配提供统一规则。
def _tokens(text: str) -> set[str]:
    """提取 evidence 匹配使用的轻量 token 集合。

    参数:
        text: 待索引或待查询的文本。

    返回:
        返回长度大于 2 的小写 ASCII token 集合。
    """

    # 只保留长度大于 2 的 ASCII 单词和数字片段，降低噪声词影响。
    return {token for token in re.findall(r"[A-Za-z0-9_]+", text.lower()) if len(token) > 2}

# sidecar 读取器把人工证据 JSON 转成标准 items。
def _load_sidecar_items(sidecars: list[Path], root: Path) -> list[dict[str, Any]]:
    """读取人工 sidecar 并转换为 evidence items。

    参数:
        sidecars: 用户提供的 evidence sidecar JSON 文件路径。
        root: sidecar 及其 ref_path 必须位于其中的工作区根目录。

    返回:
        返回可追加到 evidence `items` 字段的标准条目列表。

    异常:
        SpecError: 当 sidecar JSON 或条目结构不满足约束时抛出。
    """

    # sidecar items 与源文档段落共用同一 evidence item 结构。
    list_items: list[dict[str, Any]] = []  # 人工 sidecar 转换出的证据条目

    # source_id 在本次 sidecar 读取范围内必须唯一。
    set_seen_ids: set[str] = set()  # 当前 sidecar 批次已使用的来源 id

    # 逐个 sidecar 文件解析 JSON。
    for sidecar_path in sidecars:

        # sidecar 路径也必须留在 root 内部。
        path_resolved = _require_inside_root(sidecar_path if sidecar_path.is_absolute() else root / sidecar_path, root)  # 已通过边界检查的 sidecar 文件

        # JSON 解析错误需要带上文件名，便于用户修复。
        try:

            # 读取 sidecar JSON，后续再校验顶层形态。
            payload = json.loads(path_resolved.read_text(encoding="utf-8"))  # 人工证据 JSON 载荷

        # JSON 语法错误直接定位到 sidecar 文件。
        except json.JSONDecodeError as exc:

            # SpecError 让 CLI 将 sidecar 错误归入规格输入问题。
            raise SpecError(f"> ERR: [Python] Invalid evidence sidecar JSON in {sidecar_path}: {exc}") from exc

        # sidecar 兼容两种形态：顶层列表，或带 items 字段的对象。
        raw_items = payload.get("items", payload if isinstance(payload, list) else [])  # 原始条目列表

        # items 必须是列表，否则无法稳定排序和编号。
        if not isinstance(raw_items, list):

            # 保留旧版错误文本，避免改变用户可见诊断。
            raise SpecError("> ERR: [Python] Evidence sidecar must be a list or an object with an items list.")

        # 按 sidecar 内顺序转换每个条目。
        for item_index, raw_item in enumerate(raw_items, start=1):

            # 每个条目必须是对象，才能承载 source_id/text 等字段。
            if not isinstance(raw_item, dict):

                # 非对象条目直接拒绝，防止默默丢失证据。
                raise SpecError("> ERR: [Python] Every evidence sidecar item must be an object.")

            # source_id 缺省时按 sidecar 文件名和序号生成。
            str_default_source_id = f"{path_resolved.stem}:sidecar{item_index:03d}"  # 缺省 sidecar 来源 id

            # 显式 source_id 优先，否则使用文件名和条目序号生成稳定 id。
            str_source_id = str(raw_item.get("source_id") or str_default_source_id)  # 人工证据来源 id

            # 重复 source_id 会让 evidence 引用歧义，必须阻断。
            if str_source_id in set_seen_ids:

                # 错误消息保留 repr，便于识别空格等不可见字符。
                raise SpecError(f"> ERR: [Python] Duplicate evidence sidecar source_id {str_source_id!r}.")

            # 记录 source_id，后续条目不得重复。
            set_seen_ids.add(str_source_id)

            # 转成标准 evidence item，字段缺省策略保持旧实现。
            dict_item = {
                "source_id": str_source_id,  # sidecar 条目来源 id
                "document_id": str(raw_item.get("document_id") or path_resolved.stem),  # 所属人工文档 id
                "location": str(raw_item.get("location") or f"{path_resolved.name}:item{item_index}"),  # 条目定位
                "kind": str(raw_item.get("kind") or "text"),  # sidecar 条目的匹配类别
                "text": str(raw_item.get("text") or ""),  # 证据正文
            }  # 标准 evidence item

            # ref_path 是可选字段，用于把条目指回工作区内具体文件。
            ref_path = raw_item.get("ref_path")  # sidecar 可选引用路径

            # ref_path 存在时必须经过 root 安全检查。
            if ref_path:

                # 对外只保存相对路径，避免泄漏本机绝对路径。
                dict_item["ref_path"] = _safe_ref_path(str(ref_path), root)  # 工作区内引用相对路径

            # text 为空的证据没有匹配价值，直接报错。
            if not dict_item["text"]:

                # 错误定位到 source_id，方便用户修复对应条目。
                raise SpecError(f"> ERR: [Python] Evidence sidecar item {str_source_id!r} must include text.")

            # 追加转换后的 sidecar 条目。
            list_items.append(dict_item)

    # 返回全部 sidecar 条目。
    return list_items

# ref_path 安全转换器只允许引用 root 内部路径。
def _safe_ref_path(ref_path: str, root: Path) -> str:
    """把 sidecar ref_path 转成安全的 workspace 相对路径。

    参数:
        ref_path: sidecar 条目声明的引用路径文本。
        root: 引用路径必须停留在其中的工作区根目录。

    返回:
        返回 POSIX 形式的 workspace 相对路径。

    异常:
        SpecError: 当 ref_path 解析后越过 root 边界时抛出。
    """

    # 输入可能是绝对路径，也可能是相对工作区路径。
    path_candidate = Path(ref_path)  # 用户在 sidecar 中声明的引用路径

    # 相对路径以 root 为基准解析。
    path_resolved = path_candidate if path_candidate.is_absolute() else root / path_candidate  # 待校验路径

    # resolved.relative_to(root) 是防目录逃逸的关键检查。
    try:

        # 将引用路径转换成 root 内部的相对路径。
        path_safe = path_resolved.resolve().relative_to(root)  # 安全后的工作区相对路径

    # ref_path 越界会泄漏或引用非项目文件，必须阻断。
    except ValueError as exc:

        # 抛出带原始 ref_path 的输入诊断。
        raise SpecError(
            f"> ERR: [Python] Evidence ref_path must stay inside the current workspace: {ref_path}"
        ) from exc

    # 返回 POSIX 形式，保证 JSON artifact 跨平台稳定。
    return path_safe.as_posix()
