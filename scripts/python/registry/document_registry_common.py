"""提供文档注册化扫描与事实归一化的共享能力。"""

# 延迟注解兼容目标 Python。
from __future__ import annotations

# 导入扫描所需标准库。
from collections import defaultdict
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

# 默认相似度阈值在不同技能之间保持同一候选口径。
FLOAT_DEFAULT_SIMILARITY_THRESHOLD = 0.78  # 模糊重复候选最低相似度。

# 短标题和常见提示语不参与模糊比较，减少无意义候选。
INT_DEFAULT_MINIMUM_CHARACTERS = 80  # 模糊比较最小规范化字符数。

# 公开长选项模式用于发现需要注册的脚本接口事实。
OPTION_PATTERN = re.compile(r"--[a-z][a-z0-9-]*")  # CLI 长选项匹配规则。

# 配置文件名是判断技能是否显式启用文档门禁的唯一来源。
STR_GOVERNANCE_CONFIG_PATH = "config/registry/document-governance.json"  # 文档门禁配置相对路径。

# 初始化会复制这四份可发布 JSON Schema 到目标技能注册根。
TUPLE_DOCUMENT_SCHEMA_FILES = (  # 文档治理可发布 schema 文件名。
    "document-governance.schema.json",  # 可选门禁配置 schema。
    "document.schema.json",  # 文档职责目录 schema。
    "knowledge.schema.json",  # 知识指针索引 schema。
    "migration.schema.json",  # 首次迁移复核 schema。
)

# 统一写入受管 JSON。
def write_json_file(path_target: Path, dict_payload: dict[str, Any]) -> None:
    """原子写入单个文档注册化 JSON 文件。

    参数：path_target 为目标路径；dict_payload 为 JSON 兼容载荷。
    返回：无业务返回值，成功时替换目标文件。
    """

    # 父目录按已审查的固定布局创建。
    path_target.parent.mkdir(parents=True, exist_ok=True)

    # 同目录临时文件保证替换过程不会留下半写入 JSON。
    path_temporary = path_target.with_suffix(path_target.suffix + ".tmp")  # 当前 JSON 临时路径。

    # 规范序列化保留中文并稳定键顺序。
    str_payload = json.dumps(dict_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"  # 待写入 JSON 正文。

    # UTF-8 写入完成后再原子替换正式文件。
    path_temporary.write_text(str_payload, encoding="utf-8")

    # 正式路径只接收完整且可解析的临时文件。
    os.replace(path_temporary, path_target)

# 定位文档治理配置。
def governance_config_path(path_skill_root: Path) -> Path:
    """返回技能内文档治理配置路径。

    参数：path_skill_root 为待治理技能根目录。
    返回：config/registry 下的固定配置路径。
    """

    # POSIX 相对合同通过 Path 在当前平台解析。
    return path_skill_root / Path(STR_GOVERNANCE_CONFIG_PATH)

# 读取文档治理启用状态。
def document_governance_status(path_skill_root: Path) -> dict[str, Any]:
    """读取技能的可选文档治理状态。

    参数：path_skill_root 为待检查技能根目录。
    返回：包含 enabled、configured 和配置路径的状态映射。
    异常：配置不是 JSON 对象时抛出 ValueError。
    """

    # 固定配置路径不存在时门禁保持关闭。
    path_config = governance_config_path(path_skill_root)  # 文档治理配置路径。

    # 未配置技能无需创建任何目录或默认文件。
    if not path_config.is_file():

        # skipped 由上层命令根据 enabled 派生。
        return {"configured": False, "enabled": False, "config": str(path_config)}

    # 已存在配置使用 UTF-8 JSON 读取。
    object_payload = json.loads(path_config.read_text(encoding="utf-8"))  # 文档治理配置载荷。

    # 顶层非对象无法提供稳定启用合同。
    if not isinstance(object_payload, dict):

        # 请求调用方修复显式配置而不是猜测默认值。
        raise ValueError("> ERR: [Python] document governance config must be a JSON object")

    # 只有严格布尔真值才表示用户已启用门禁。
    return {
        "configured": True,
        "enabled": bool(object_payload.get("enabled", False)),
        "config": str(path_config),
        "governance": object_payload,
    }

# 收集受管 Markdown 文档。
def document_paths(path_skill_root: Path) -> list[Path]:
    """收集技能入口与 references 下的 Markdown 文档。

    参数：path_skill_root 为待扫描技能根目录。
    返回：按技能相对路径排序的 Markdown 文件列表。
    异常：技能根不存在或不是目录时抛出 ValueError。
    """

    # 无效技能根不能形成可信的文档扫描结果。
    if not path_skill_root.is_dir():

        # 稳定错误前缀便于 CLI 映射为请求错误。
        raise ValueError("> ERR: [Python] skill root must be an existing directory")

    # 入口文档存在时作为首个受管文档参与职责检查。
    path_skill_document = path_skill_root / "SKILL.md"  # 技能入口文档路径。

    # 文档集合先保留可能存在的技能入口。
    list_paths: list[Path] = []  # 当前技能受管 Markdown 文件。

    # 缺少 SKILL.md 时仍允许扫描 references 并由上层报告事实。
    if path_skill_document.is_file():

        # 技能入口固定排在 references 文档之前。
        list_paths.append(path_skill_document)

    # references 是首版唯一纳入的知识正文目录。
    path_references = path_skill_root / "references"  # 技能知识文档根目录。

    # 不存在 references 的简单技能仍可只治理 SKILL.md。
    if path_references.is_dir():

        # 递归发现知识目录下全部 Markdown 文档。
        list_paths.extend(path_references.rglob("*.md"))

    # 相对路径排序保证跨平台重复运行顺序稳定。
    return sorted(list_paths, key=lambda path_item: path_item.relative_to(path_skill_root).as_posix())

# 按围栏和空行拆分 Markdown。
def markdown_blocks(str_text: str) -> list[str]:
    """把 Markdown 正文切分为可比较的语义块。

    参数：str_text 为单份 Markdown 完整正文。
    返回：去除块外空白后的段落、列表、表格或代码块列表。
    """

    # 输出列表按原文顺序保存可比较块。
    list_blocks: list[str] = []  # Markdown 语义块集合。

    # 当前行缓冲区在空行或围栏结束时落盘。
    list_current_lines: list[str] = []  # 当前尚未提交的块内容。

    # 围栏状态确保代码块内部空行不会被拆开。
    bool_in_fence = False  # 当前是否位于 Markdown 代码围栏内。

    # 逐行处理能够保留命令块的完整边界。
    for str_line in str_text.splitlines():

        # 围栏行切换代码区域状态并始终进入当前块。
        if str_line.lstrip().startswith("```"):

            # 围栏标记属于代码块事实的一部分。
            list_current_lines.append(str_line)

            # 开始或结束围栏均通过同一布尔状态表达。
            bool_in_fence = not bool_in_fence  # 切换后的围栏状态。

            # 结束围栏时立即保存完整代码块。
            if not bool_in_fence:

                # 当前围栏块去除外围空白后进入输出。
                list_blocks.append("\n".join(list_current_lines).strip())

                # 新缓冲区承接围栏之后的普通正文。
                list_current_lines = []  # 围栏结束后的空块缓冲区。

            # 当前围栏行已经处理完成。
            continue

        # 围栏内部所有行按原样保留，包括空行。
        if bool_in_fence:

            # 代码行属于同一个不可拆分的接口事实块。
            list_current_lines.append(str_line)

            # 当前代码行无需进入普通段落逻辑。
            continue

        # 普通正文遇到空行时提交已有缓冲区。
        if not str_line.strip():

            # 连续空行不能产生空语义块。
            if list_current_lines:

                # 普通段落保留内部换行并去除外围空白。
                list_blocks.append("\n".join(list_current_lines).strip())

                # 下一段正文使用新的空缓冲区。
                list_current_lines = []  # 普通段落结束后的空缓冲区。

            # 空行只承担分隔作用。
            continue

        # 非空普通行追加到当前段落或列表块。
        list_current_lines.append(str_line)

    # 文件末尾没有空行时仍需提交最后一个块。
    if list_current_lines:

        # 尾块与前面的普通段落使用同一归一化方式。
        list_blocks.append("\n".join(list_current_lines).strip())

    # 空字符串已经在分块过程中被排除。
    return list_blocks

# 规范化文档块空白。
def normalize_block(str_block: str) -> str:
    """生成用于精确重复比较的稳定块文本。

    参数：str_block 为单个 Markdown 语义块。
    返回：空白折叠后的可复现比较文本。
    """

    # 所有空白序列折叠为单个空格，消除换行格式差异。
    return " ".join(str_block.split())

# 汇总单文档扫描事实。
def append_document_scan_facts(
    path_skill_root: Path,
    path_document: Path,
    dict_scan_state: dict[str, Any],
) -> None:
    """把单份 Markdown 的扫描事实追加到聚合容器。

    参数：path_skill_root 为技能根；path_document 为文档；dict_scan_state 为聚合扫描容器。
    数据约束：输入是路径与 JSON 兼容字典，不涉及数值 shape、dtype 或物理 unit。
    返回：无业务返回值，结果追加到调用方提供的容器。
    """

    # 相对路径避免扫描报告泄漏本机绝对位置。
    str_relative_path = path_document.relative_to(path_skill_root).as_posix()  # 技能内文档路径。

    # UTF-8 是技能 Markdown 的统一编码合同。
    str_document_text = path_document.read_text(encoding="utf-8")  # 当前文档完整正文。

    # 内容摘要把职责和知识指针绑定到当前权威正文版本。
    str_document_sha256 = hashlib.sha256(str_document_text.encode("utf-8")).hexdigest()  # 文档内容摘要。

    # 文档清单只保存稳定路径与原始内容哈希。
    dict_scan_state["documents"].append(  # 当前文档扫描事实。
        {
            "path": str_relative_path,  # 技能内权威路径。
            "sha256": str_document_sha256,  # 当前正文摘要。
        }
    )

    # 文档级知识单元保证没有标题的 Markdown 也能注册。
    dict_scan_state["knowledge_units"].append(  # 当前文档知识指针候选。
        {
            "source_path": str_relative_path,  # 知识正文来源路径。
            "source_anchor": "",  # 文档级候选没有章节锚点。
            "content_sha256": str_document_sha256,  # 正文漂移检测摘要。
        }
    )

    # 每个非空语义块贡献精确重复与模糊候选事实。
    for int_block_index, str_block in enumerate(markdown_blocks(str_document_text)):

        # 折叠空白后形成跨格式稳定的比较文本。
        str_normalized_block = normalize_block(str_block)  # 当前块规范化正文。

        # 空块不参与任何重复分析。
        if not str_normalized_block:

            # 继续处理下一语义块。
            continue

        # 摘要键用于精确重复分组。
        str_block_sha256 = hashlib.sha256(str_normalized_block.encode("utf-8")).hexdigest()  # 规范化块摘要。

        # 位置、序号与短预览共同形成 Agent 复核证据。
        dict_scan_state["block_locations"][str_block_sha256].append(  # 当前块来源证据。
            {
                "path": str_relative_path,  # 块来源文档。
                "block_index": str(int_block_index),  # 文档内块序号。
                "preview": str_normalized_block[:240],  # Agent 复核短预览。
            }
        )

        # 短块不进入高成本模糊比较。
        if len(str_normalized_block) >= INT_DEFAULT_MINIMUM_CHARACTERS:

            # 长块保留内存比较所需正文，不直接复制到最终索引。
            dict_scan_state["long_blocks"].append(  # 当前长块比较事实。
                {
                    "path": str_relative_path,  # 长块来源文档。
                    "block_index": int_block_index,  # 长块来源序号。
                    "sha256": str_block_sha256,  # 长块内容摘要。
                    "text": str_normalized_block,  # 本轮内存比较文本。
                }
            )

    # 文档中的公开长选项形成脚本接口事实。
    for str_option in OPTION_PATTERN.findall(str_document_text):

        # 集合去重避免同一文档重复出现制造伪覆盖。
        dict_scan_state["option_sources"][str_option].add(str_relative_path)

# 比较一对长文本块。
def fuzzy_candidate(
    dict_left_block: dict[str, Any],
    dict_right_block: dict[str, Any],
) -> dict[str, Any] | None:
    """比较两个长块并返回可复核候选。

    参数：dict_left_block 与 dict_right_block 为规范化长块事实。
    返回：达到阈值时返回候选映射，否则返回 None。
    """

    # 同一文档的内容不属于跨文档职责去重范围。
    if dict_left_block["path"] == dict_right_block["path"]:

        # None 表示当前块对无需 Agent 复核。
        return None

    # 完全相同块已经进入精确重复队列。
    if dict_left_block["sha256"] == dict_right_block["sha256"]:

        # 避免同一事实出现两个裁决入口。
        return None

    # 标准库比较器禁用长度相关垃圾字符启发式。
    object_matcher = SequenceMatcher(  # 当前长块对比较器。
        None,  # 不定义额外垃圾字符集合。
        str(dict_left_block["text"]),  # 左侧规范化正文。
        str(dict_right_block["text"]),  # 右侧规范化正文。
        autojunk=False,  # 固定算法行为。
    )

    # 长度上界不足时跳过完整动态规划。
    if object_matcher.real_quick_ratio() < FLOAT_DEFAULT_SIMILARITY_THRESHOLD:

        # 当前块对不可能达到公开阈值。
        return None

    # 字符多重集上界进一步缩小完整比较集合。
    if object_matcher.quick_ratio() < FLOAT_DEFAULT_SIMILARITY_THRESHOLD:

        # 当前块对的快速上界不足。
        return None

    # 只有可能达标的候选执行完整相似度计算。
    float_similarity = object_matcher.ratio()  # 当前块对最终相似度。

    # 真实比率仍可能低于候选阈值。
    if float_similarity < FLOAT_DEFAULT_SIMILARITY_THRESHOLD:

        # 低相似块不进入 Agent 复核队列。
        return None

    # 最终报告只保留摘要、位置与短预览。
    return {
        "similarity": round(float_similarity, 6),  # 六位小数稳定报告差异。
        "left": {
            "path": dict_left_block["path"],  # 左侧来源路径。
            "block_index": dict_left_block["block_index"],  # 左侧块序号。
            "sha256": dict_left_block["sha256"],  # 左侧内容摘要。
            "preview": str(dict_left_block["text"])[:240],  # 左侧短预览。
        },
        "right": {
            "path": dict_right_block["path"],  # 右侧来源路径。
            "block_index": dict_right_block["block_index"],  # 右侧块序号。
            "sha256": dict_right_block["sha256"],  # 右侧内容摘要。
            "preview": str(dict_right_block["text"])[:240],  # 右侧短预览。
        },
    }

# 收集跨文档模糊候选。
def find_fuzzy_candidates(list_long_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """收集达到固定阈值的跨文档长块对。

    参数：list_long_blocks 为按文档扫描顺序生成的长块事实。
    返回：按输入顺序排列的模糊重复候选。
    """

    # 输出列表保持确定性扫描顺序。
    list_candidates: list[dict[str, Any]] = []  # 跨文档模糊候选集合。

    # 左侧索引限制右侧只访问尚未配对的后续块。
    for int_left_index, dict_left_block in enumerate(list_long_blocks):

        # 后续切片保证每个无序块对只出现一次。
        for dict_right_block in list_long_blocks[int_left_index + 1:]:

            # 单对比较器返回候选或 None。
            dict_candidate = fuzzy_candidate(dict_left_block, dict_right_block)  # 当前块对比较结果。

            # 只有达到阈值的候选才进入报告。
            if dict_candidate is not None:

                # 候选顺序与文档扫描顺序一致。
                list_candidates.append(dict_candidate)

    # 完整候选集合交给 Agent 逐项复核。
    return list_candidates

# 汇总文档扫描结果。
def scan_skill_documents(path_skill_root: Path) -> dict[str, Any]:
    """扫描技能文档并返回注册化候选事实。

    参数：path_skill_root 为待治理技能根目录。
    返回：包含文档、精确重复、接口事实和知识单元的结构化报告。
    """

    # 单一状态对象集中保存本轮扫描聚合容器。
    dict_scan_state: dict[str, Any] = {  # 当前技能扫描聚合状态。
        "block_locations": defaultdict(list),  # 块摘要到来源位置。
        "option_sources": defaultdict(set),  # CLI 选项到来源文档。
        "documents": [],  # 受管文档基础事实。
        "knowledge_units": [],  # 文档级知识指针候选。
        "long_blocks": [],  # 达到最小长度的语义块事实。
    }

    # 每份受管 Markdown 独立贡献扫描事实。
    for path_document in document_paths(path_skill_root):

        # 单文档辅助函数负责读取和分块。
        append_document_scan_facts(
            path_skill_root,  # 当前技能根。
            path_document,  # 当前 Markdown 路径。
            dict_scan_state,  # 本轮统一扫描状态。
        )

    # 精确重复只保留至少两个来源位置的组。
    list_exact_duplicates = [  # 跨位置精确重复组。
        {
            "sha256": str_block_sha256,  # 规范化重复块摘要。
            "locations": list_locations,  # 全部重复来源位置。
        }
        for str_block_sha256, list_locations in sorted(dict_scan_state["block_locations"].items())  # 稳定遍历块来源。
        if len(list_locations) > 1  # 排除未重复的单一来源块。
    ]

    # 每个选项形成一条可映射到 command 记录的接口事实。
    list_interface_facts = [  # CLI 长选项注册候选。
        {
            "kind": "option",  # 当前事实类型。
            "value": str_option,  # 需要注册的长选项。
            "sources": sorted(set_sources),  # 出现该选项的文档路径。
        }
        for str_option, set_sources in sorted(dict_scan_state["option_sources"].items())  # 稳定遍历选项来源。
    ]

    # 长块对通过预筛后形成模糊候选。
    list_fuzzy_candidates = find_fuzzy_candidates(dict_scan_state["long_blocks"])  # 模糊重复候选集合。

    # 顶层报告公开算法参数和全部候选事实。
    return {
        "ok": True,  # 扫描过程完成且输入有效。
        "skill_root": str(path_skill_root),  # 调用者提供的技能根路径。
        "document_count": len(dict_scan_state["documents"]),  # 受管 Markdown 数量。
        "documents": dict_scan_state["documents"],  # 文档路径与摘要。
        "exact_duplicates": list_exact_duplicates,  # 规范化精确重复组。
        "fuzzy_candidates": list_fuzzy_candidates,  # 达到固定阈值的长块对。
        "similarity_policy": {
            "algorithm": "difflib.SequenceMatcher",  # 确定性相似度算法。
            "threshold": FLOAT_DEFAULT_SIMILARITY_THRESHOLD,  # 模糊候选阈值。
            "minimum_characters": INT_DEFAULT_MINIMUM_CHARACTERS,  # 最小块长度。
        },
        "interface_facts": list_interface_facts,  # 待注册脚本接口事实。
        "knowledge_units": dict_scan_state["knowledge_units"],  # 供初始化器消费的知识事实。
    }

# 生成稳定文档标识。
def document_id(str_relative_path: str) -> str:
    """根据技能内路径生成稳定文档标识。

    参数：str_relative_path 为技能根下的 POSIX Markdown 路径。
    返回：以 document 开头的可读注册标识。
    """

    # 后缀移除后把非字母数字字符统一替换为连字符。
    str_stem = str_relative_path.removesuffix(".md").casefold()  # 文档路径去后缀结果。

    # 连续分隔符折叠后形成跨平台一致标识。
    str_slug = re.sub(r"[^a-z0-9]+", "-", str_stem).strip("-")  # 文档路径标识片段。

    # 空标识只可能来自无效路径，使用摘要保持确定性。
    if not str_slug:

        # 摘要避免产生空主键。
        str_slug = hashlib.sha256(str_relative_path.encode("utf-8")).hexdigest()[:12]  # 回退标识片段。

    # document 前缀与 command、workflow、knowledge 命名空间区分。
    return f"document.{str_slug}"

# 构造待复核目录草案。
def initial_catalog_records(dict_scan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """构造文档职责草案和知识指针草案。

    参数：dict_scan 为当前技能扫描报告。
    返回：文档目录记录和知识指针记录两个列表。
    """

    # 每份 Markdown 获得一条待 Agent 复核的唯一职责记录。
    list_catalog_documents = [  # 文档职责草案记录。
        {
            "id": document_id(str(dict_document["path"])),  # 稳定文档标识。
            "path": dict_document["path"],  # 权威 Markdown 路径。
            "responsibility": "PENDING_AGENT_REVIEW",  # 待填写唯一职责。
            "summary": "PENDING_AGENT_REVIEW",  # 待填写检索摘要。
            "content_sha256": dict_document["sha256"],  # 初始化正文摘要。
            "status": "draft",  # Agent 复核前状态。
        }
        for dict_document in dict_scan["documents"]  # 遍历受管文档事实。
    ]

    # 知识记录只保存权威正文指针和待复核摘要。
    list_knowledge_records = [  # 文档级知识指针草案。
        {
            "id": str(dict_document["id"]).replace("document.", "knowledge.", 1),  # 知识标识。
            "kind": "knowledge",  # 查询命名空间。
            "title": dict_document["path"],  # 初始路径标题。
            "summary": "PENDING_AGENT_REVIEW",  # 待填写语义摘要。
            "source_path": dict_document["path"],  # 知识指针指向的正文路径。
            "source_anchor": "",  # 文档级指针没有锚点。
            "content_sha256": dict_document["content_sha256"],  # 权威正文摘要。
            "keywords": [],  # 待填写检索关键词。
        }
        for dict_document in list_catalog_documents  # 每份文档对应一个知识指针。
    ]

    # 两类草案记录交给初始化载荷构造器。
    return list_catalog_documents, list_knowledge_records

# 构造迁移复核任务。
def initial_migration_reviews(dict_scan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """构造重复和脚本接口复核队列。

    参数：dict_scan 为当前技能扫描报告。
    返回：exact_reviews、fuzzy_reviews 与 interface_reviews 三个列表。
    """

    # 精确重复逐组形成唯一裁决入口。
    list_exact_reviews = [  # 精确重复复核任务。
        {
            "id": f"exact.{dict_candidate['sha256'][:16]}",  # 稳定候选标识。
            "decision": "pending",  # Agent 选择保留或去重。
            "authoritative_location": "",  # 去重时填写权威来源。
            "rationale": "",  # Agent 记录裁决理由。
            "evidence": dict_candidate,  # 绑定扫描位置证据。
        }
        for dict_candidate in dict_scan["exact_duplicates"]  # 遍历精确重复组。
    ]

    # 模糊候选保留相似度和当前用户确认需求。
    list_fuzzy_reviews = [  # 模糊重复复核任务。
        {
            "id": f"fuzzy.{int_index:04d}",  # 稳定顺序标识。
            "decision": "pending",  # Agent 决定保留或去重。
            "authoritative_location": "",  # 去重时填写权威位置。
            "rationale": "",  # Agent 记录语义判断。
            "needs_user_confirmation": False,  # 不确定时由 Agent 改为真。
            "evidence": dict_candidate,  # 相似度和来源证据。
        }
        for int_index, dict_candidate in enumerate(dict_scan["fuzzy_candidates"], start=1)  # 稳定编号候选。
    ]

    # 脚本接口需要映射命令记录或明确判定为非公开事实。
    list_interface_reviews = [  # 脚本接口复核任务。
        {
            "kind": dict_fact["kind"],  # 接口事实类型。
            "value": dict_fact["value"],  # 长选项文本。
            "sources": dict_fact["sources"],  # Markdown 来源证据。
            "decision": "pending",  # 接口事实尚未完成映射。
            "command_ids": [],  # mapped 时登记命令标识。
            "rationale": "",  # not_public 时填写理由。
        }
        for dict_fact in dict_scan["interface_facts"]  # 遍历全部接口事实。
    ]

    # 三类队列使用固定字段名进入迁移记录。
    return {
        "exact_reviews": list_exact_reviews,  # 精确重复裁决队列。
        "fuzzy_reviews": list_fuzzy_reviews,  # 模糊重复裁决队列。
        "interface_reviews": list_interface_reviews,  # 接口映射裁决队列。
    }

# 加载文档治理 schema。
def document_schema_payloads() -> dict[str, dict[str, Any]]:
    """读取控制器自带的四份文档治理 JSON Schema。

    参数：无外部业务参数。
    返回：schema 文件名到 JSON 对象的映射。
    异常：模板缺失、JSON 非法或顶层非对象时抛出 OSError、JSONDecodeError 或 ValueError。
    """

    # 控制器所有者目录是可发布 schema 的权威来源。
    path_owner_registry_root = Path(__file__).resolve().parents[3] / "config" / "registry"  # 所有者注册根。

    # 结果映射保留固定文件名。
    dict_schemas: dict[str, dict[str, Any]] = {}  # schema 文件名到载荷。

    # 四份模板逐一读取并验证顶层结构。
    for str_schema_name in TUPLE_DOCUMENT_SCHEMA_FILES:

        # 当前模板路径不得从目标技能推导。
        path_schema_source = path_owner_registry_root / str_schema_name  # 当前 schema 模板路径。

        # UTF-8 JSON 保留 schema 标题和约束。
        object_schema = json.loads(path_schema_source.read_text(encoding="utf-8"))  # 当前 schema 载荷。

        # 顶层非对象不能表达 JSON Schema。
        if not isinstance(object_schema, dict):

            # 错误绑定损坏模板名称。
            raise ValueError(f"> ERR: [Python] document schema template must be an object: {str_schema_name}")

        # 已验证模板加入初始化文件集合。
        dict_schemas[str_schema_name] = object_schema  # 当前 schema 文件载荷。

    # 完整模板集合交给初始化器写入。
    return dict_schemas

# 构造文档治理初始载荷。
def initial_governance_payloads(path_skill_root: Path) -> dict[str, dict[str, Any]]:
    """构造首次文档注册化所需的全部 JSON 载荷。

    参数：path_skill_root 为显式启用治理的技能根目录。
    返回：相对路径到 JSON 对象的映射，所有裁决保持 pending。
    异常：扫描或 schema 模板加载失败时传播 ValueError、OSError 或 JSONDecodeError。
    """

    # 当前文档字节状态绑定全部初始化草案。
    dict_scan = scan_skill_documents(path_skill_root)  # 首次文档扫描报告。

    # 职责记录和知识指针由同一文档集合生成。
    tuple_initial_records = initial_catalog_records(dict_scan)  # 文档与知识草案二元组。

    # 元组首项是文档职责记录。
    list_catalog_documents = tuple_initial_records[0]  # 文档目录草案。

    # 元组次项是知识指针记录。
    list_knowledge_records = tuple_initial_records[1]  # 知识索引草案。

    # 重复和接口事实保持待 Agent 裁决。
    dict_reviews = initial_migration_reviews(dict_scan)  # 首次迁移复核队列。

    # 业务 JSON 文件使用固定相对布局。
    dict_payloads = {  # 文档治理初始化文件集合。
        "document-governance.json": {  # 可选门禁主配置。
            "schema_version": 1,  # 文档治理配置版本。
            "enabled": True,  # 用户显式启用后的状态。
            "status": "draft",  # finalize 前保持草案。
            "catalog": "documents/catalog.json",  # 主配置引用的职责目录。
            "knowledge_index": "knowledge/index.json",  # 主配置引用的知识索引。
            "migration": "migrations/initial-document-registration.json",  # 首次迁移记录。
            "similarity_policy": dict_scan["similarity_policy"],  # 固定相似度策略。
            "scan_scope": ["SKILL.md", "references/**/*.md"],  # 受管 Markdown 边界。
        },
        "documents/catalog.json": {  # 文档唯一职责目录。
            "schema_version": 1,  # 文档目录版本。
            "status": "draft",  # 文档职责目录草案状态。
            "documents": list_catalog_documents,  # 文档职责记录。
        },
        "knowledge/index.json": {  # 权威 Markdown 知识指针索引。
            "schema_version": 1,  # 知识索引版本。
            "status": "draft",  # 知识指针索引草案状态。
            "records": list_knowledge_records,  # 权威 Markdown 指针。
        },
        "migrations/initial-document-registration.json": {  # 首次注册的语义复核证据。
            "schema_version": 1,  # 迁移记录版本。
            "status": "pending_agent_review",  # Agent 尚未裁决。
            "user_confirmation": "not_required",  # 当前无确认请求。
            "source_documents": dict_scan["documents"],  # 初始化源摘要。
            **dict_reviews,  # 三类复核队列。
        },
    }

    # 可发布 schema 与业务草案一同初始化。
    dict_payloads.update(document_schema_payloads())

    # 完整文件集合交给原子写入器。
    return dict_payloads

# 初始化文档治理文件。
def initialize_document_governance(path_skill_root: Path) -> dict[str, Any]:
    """写入文档注册化草案文件。

    参数：path_skill_root 为已显式授权的技能根目录。
    返回：包含写入文件和扫描计数的状态映射。
    异常：已存在配置时抛出 ValueError，避免覆盖人工裁决。
    """

    # 已配置技能必须使用 check、finalize 或人工修订流程。
    path_config = governance_config_path(path_skill_root)  # 固定治理配置路径。

    # 初始化不能覆盖现有草案或完成态配置。
    if path_config.exists():

        # 明确拒绝覆盖保护 Agent 与用户已做的裁决。
        raise ValueError("> ERR: [Python] document governance is already configured")

    # 全部草案载荷在任何写入前构造完成。
    dict_payloads = initial_governance_payloads(path_skill_root)  # 初始化 JSON 文件集合。

    # 注册根由固定配置路径的父目录确定。
    path_registry_root = path_config.parent  # 文档注册治理根目录。

    # 每份文件使用独立原子替换并按路径稳定写入。
    for str_relative_path, dict_payload in sorted(dict_payloads.items()):

        # 目标路径始终位于受审查的 config/registry 布局内。
        write_json_file(path_registry_root / str_relative_path, dict_payload)

    # 初始化只声明草案，未声称通过最终门禁。
    return {
        "ok": True,
        "enabled": True,
        "status": "draft",
        "written_files": sorted(dict_payloads),
        "document_count": len(dict_payloads["documents/catalog.json"]["documents"]),
    }

# 读取并校验 JSON 对象。
def read_json_object(path_source: Path, str_label: str) -> dict[str, Any]:
    """读取并校验单个注册化 JSON 对象。

    参数：path_source 为 JSON 路径；str_label 为诊断名称。
    返回：解析后的顶层映射。
    异常：文件缺失、JSON 非法或顶层非对象时抛出 ValueError。
    """

    # 缺失受管文件说明初始化或人工迁移不完整。
    if not path_source.is_file():

        # 诊断同时指出逻辑名称和实际路径。
        raise ValueError(f"> ERR: [Python] missing {str_label}: {path_source}")

    # JSON 解码错误交由调用方映射为门禁退出码。
    object_payload = json.loads(path_source.read_text(encoding="utf-8"))  # 当前注册化 JSON 载荷。

    # 结构化门禁只接受顶层对象。
    if not isinstance(object_payload, dict):

        # 列表等顶层结构不能提供版本和状态字段。
        raise ValueError("> ERR: [Python] document registry JSON must be an object: " + str_label)

    # 返回已验证映射供语义门禁继续检查。
    return object_payload

# 安全解析注册表相对路径。
def resolve_governance_file(path_registry_root: Path, str_relative_path: str) -> Path:
    """解析并约束文档治理配置引用的文件路径。

    参数：path_registry_root 为 registry 根；str_relative_path 为配置相对路径。
    返回：位于 registry 根内的规范化路径。
    异常：绝对路径或父目录逃逸时抛出 ValueError。
    """

    # 相对路径对象用于拒绝绝对位置。
    path_relative = Path(str_relative_path)  # 配置声明的相对路径。

    # 根和候选均规范化后检查包含关系。
    path_root_resolved = path_registry_root.resolve()  # 规范化 registry 根目录。

    # 候选路径必须解析后仍位于同一根目录。
    path_candidate = (path_registry_root / path_relative).resolve()  # 规范化治理文件路径。

    # 配置不得读取或写入 registry 根外文件。
    if path_relative.is_absolute() or not path_candidate.is_relative_to(path_root_resolved):

        # 路径边界错误保留原始配置值。
        raise ValueError(f"> ERR: [Python] governance path is outside registry root: {str_relative_path}")

    # 合法候选交给调用方读取。
    return path_candidate

# 加载文档治理持久数据。
def load_governance_documents(path_skill_root: Path) -> dict[str, Any]:
    """加载已启用技能的文档治理文件集合。

    参数：path_skill_root 为已配置技能根目录。
    返回：包含路径和四份 JSON 对象的映射。
    异常：配置未启用、路径越界或文件无效时抛出 ValueError。
    """

    # 启用状态必须来自持久配置。
    dict_status = document_governance_status(path_skill_root)  # 当前技能文档治理状态。

    # 未启用技能不应进入持久治理加载路径。
    if not dict_status["enabled"]:

        # 调用方应把未启用状态处理为 skipped。
        raise ValueError("> ERR: [Python] document governance is not enabled")

    # 主配置路径来自已验证启用状态。
    path_config = Path(str(dict_status["config"]))  # 已启用主配置的绝对路径。

    # 配置父目录约束所有相对治理文件。
    path_registry_root = path_config.parent  # 文档治理 registry 根目录。

    # 配置对象提供目录、知识和迁移相对路径。
    dict_governance = dict_status["governance"]  # 文档治理配置对象。

    # 三个引用字段必须是非空相对路径文本。
    dict_paths: dict[str, Path] = {}  # 治理文档逻辑名称到路径。

    # 固定逻辑名称映射到配置字段。
    for str_label, str_field_name in (
        ("catalog", "catalog"),
        ("knowledge_index", "knowledge_index"),
        ("migration", "migration"),
    ):

        # 缺失或空路径不能形成可审计闭环。
        object_relative_path = dict_governance.get(str_field_name)  # 当前配置路径值。

        # 只接受非空字符串路径。
        if not isinstance(object_relative_path, str) or not object_relative_path.strip():

            # 诊断直接指向缺失字段。
            raise ValueError(f"> ERR: [Python] document governance missing path: {str_field_name}")

        # 安全解析后的路径供统一读取。
        dict_paths[str_label] = resolve_governance_file(  # 当前逻辑名称的安全文件路径。
            path_registry_root,  # 文档治理注册根。
            object_relative_path,  # 当前 catalog、knowledge 或 migration 引用值。
        )

    # 返回路径与载荷便于 finalize 原位更新状态。
    return {
        "config_path": path_config,
        "governance": dict_governance,
        "catalog_path": dict_paths["catalog"],
        "catalog": read_json_object(dict_paths["catalog"], "document catalog"),
        "knowledge_path": dict_paths["knowledge_index"],
        "knowledge": read_json_object(dict_paths["knowledge_index"], "knowledge index"),
        "migration_path": dict_paths["migration"],
        "migration": read_json_object(dict_paths["migration"], "migration record"),
    }

# 校验文档治理状态。
def validate_current_governance_status(
    dict_documents: dict[str, Any],
    bool_require_current: bool,
) -> None:
    """验证四份治理文件的生命周期状态一致。

    参数：dict_documents 为治理文件集合；bool_require_current 控制是否要求完成态。
    返回：无业务返回值，状态合法时直接结束。
    异常：状态不满足持续门禁或 finalize 前置条件时抛出 ValueError。
    """

    # finalize 前允许 reviewed 草案，持续门禁只接受 current。
    if not bool_require_current:

        # 草案状态由后续迁移复核门禁继续检查。
        return

    # 主配置必须最后晋级为 current。
    if dict_documents["governance"].get("status") != "current":

        # 草案配置不能通过持续门禁。
        raise ValueError("> ERR: [Python] document governance status is not current")

    # 目录与知识索引必须和主配置同步完成。
    if (
        dict_documents["catalog"].get("status") != "current"
        or dict_documents["knowledge"].get("status") != "current"
    ):

        # 部分完成状态表示 finalize 未闭环。
        raise ValueError("> ERR: [Python] document governance files are not current")

    # 迁移记录使用 finalized 表示裁决不可被脚本覆盖。
    if dict_documents["migration"].get("status") != "finalized":

        # 未完成迁移不能作为持久治理证据。
        raise ValueError("> ERR: [Python] document migration is not finalized")

# 校验文档目录记录。
def validate_catalog_records(
    dict_catalog: dict[str, Any],
    dict_scan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """验证文档职责目录并返回实时扫描路径映射。

    参数：dict_catalog 为职责目录；dict_scan 为实时 Markdown 扫描。
    返回：技能相对路径到实时扫描事实的映射。
    异常：目录结构、职责字段或内容摘要不合法时抛出 ValueError。
    """

    # 文档容器必须是一文档一记录的列表。
    object_catalog_documents = dict_catalog.get("documents")  # 文档职责记录容器。

    # 非列表结构无法执行路径对照。
    if not isinstance(object_catalog_documents, list):

        # 结构错误阻止 finalize 和持续门禁。
        raise ValueError("> ERR: [Python] document catalog documents must be a list")

    # 两个映射分别代表登记状态和当前磁盘状态。
    dict_catalog_by_path = {  # 登记路径到职责记录。
        str(dict_item.get("path", "")): dict_item  # 当前职责记录。
        for dict_item in object_catalog_documents  # 遍历登记文档。
    }

    # 实时扫描映射用于摘要漂移检查。
    dict_scan_by_path = {  # 当前路径到扫描事实。
        str(dict_item["path"]): dict_item  # 当前扫描记录。
        for dict_item in dict_scan["documents"]  # 遍历磁盘文档。
    }

    # 重复目录记录或文档增删都属于集合漂移。
    if (
        len(dict_catalog_by_path) != len(object_catalog_documents)
        or set(dict_catalog_by_path) != set(dict_scan_by_path)
    ):

        # 不自动修改目录，保留新增、删除或重复证据。
        raise ValueError("> ERR: [Python] document set drift detected")

    # 每份文档必须具备 Agent 复核的职责、摘要和当前哈希。
    for str_path, dict_catalog_document in sorted(dict_catalog_by_path.items()):

        # 两个语义字段均不得保留脚本占位符。
        for str_field_name in ("responsibility", "summary"):

            # 当前字段值用于统一非空检查。
            object_value = dict_catalog_document.get(str_field_name)  # 当前语义字段值。

            # 空值或占位值阻止完成。
            if (
                not isinstance(object_value, str)
                or not object_value.strip()
                or object_value == "PENDING_AGENT_REVIEW"
            ):

                # 诊断定位具体文档和字段。
                raise ValueError(f"> ERR: [Python] document {str_path} has pending {str_field_name}")

        # 登记摘要必须等于当前权威正文摘要。
        if dict_catalog_document.get("content_sha256") != dict_scan_by_path[str_path]["sha256"]:

            # 不自动刷新摘要，要求重新复核正文变化。
            raise ValueError(f"> ERR: [Python] document content drift detected: {str_path}")

    # 当前路径映射供知识指针校验复用。
    return dict_scan_by_path

# 校验知识索引记录。
def validate_knowledge_records(
    dict_knowledge: dict[str, Any],
    dict_scan_by_path: dict[str, dict[str, Any]],
) -> None:
    """验证知识指针覆盖、摘要和正文绑定。

    参数：dict_knowledge 为知识索引；dict_scan_by_path 为实时文档映射。
    返回：无业务返回值，全部知识指针合法时直接结束。
    异常：记录结构、来源、摘要、哈希或覆盖不合法时抛出 ValueError。
    """

    # records 必须是结构化列表。
    object_knowledge_records = dict_knowledge.get("records")  # 知识指针记录容器。

    # 错误容器不能生成 SQLite 知识记录。
    if not isinstance(object_knowledge_records, list):

        # 结构错误阻止联合索引构建。
        raise ValueError("> ERR: [Python] knowledge index records must be a list")

    # 来源集合用于证明每份 Markdown 至少有一个知识入口。
    set_knowledge_sources: set[str] = set()  # 已登记知识来源路径。

    # 每条记录核对来源、摘要和内容哈希。
    for dict_record in object_knowledge_records:

        # 来源必须回到当前受管 Markdown。
        str_source_path = str(dict_record.get("source_path", ""))  # 当前知识来源路径。

        # 悬空来源无法成为权威指针。
        if str_source_path not in dict_scan_by_path:

            # 诊断显示无效来源路径。
            raise ValueError(f"> ERR: [Python] knowledge source is not managed: {str_source_path}")

        # 摘要必须由 Agent 填写。
        object_summary = dict_record.get("summary")  # 当前知识检索摘要。

        # 空摘要或脚本占位符不能支持知识查询。
        if (
            not isinstance(object_summary, str)
            or not object_summary.strip()
            or object_summary == "PENDING_AGENT_REVIEW"
        ):

            # 诊断绑定知识标识。
            raise ValueError(
                f"> ERR: [Python] knowledge record has pending summary: {dict_record.get('id', '')}"
            )

        # 知识指针摘要绑定权威正文当前版本。
        if dict_record.get("content_sha256") != dict_scan_by_path[str_source_path]["sha256"]:

            # 漂移必须重新复核，不能静默刷新。
            raise ValueError(f"> ERR: [Python] knowledge content drift detected: {str_source_path}")

        # 当前来源已经具备可检索入口。
        set_knowledge_sources.add(str_source_path)

    # 所有受管文档都必须进入知识索引。
    if set_knowledge_sources != set(dict_scan_by_path):

        # 缺少指针的文档违反全量注册要求。
        raise ValueError("> ERR: [Python] knowledge index does not cover every document")

# 校验重复内容裁决。
def validate_duplicate_reviews(
    dict_migration: dict[str, Any],
    dict_scan: dict[str, Any],
    *,
    bool_require_current: bool,
    bool_user_confirmed: bool,
) -> None:
    """验证迁移状态和精确、模糊重复裁决。

    参数：dict_migration 为迁移记录；dict_scan 为实时扫描；bool_require_current 为状态模式；bool_user_confirmed 为本次确认信号。
    返回：无业务返回值，全部重复裁决合法时直接结束。
    异常：迁移未复核、裁决 pending 或缺少用户确认时抛出 ValueError。
    """

    # finalize 前迁移必须由 Agent 标记复核完成。
    if not bool_require_current and dict_migration.get("status") != "agent_reviewed":

        # 脚本不得替代 Agent 语义裁决。
        raise ValueError("> ERR: [Python] migration requires Agent review")

    # 从实时扫描重建候选身份，阻止旧裁决只凭相同数量继续通过。
    dict_expected_reviews = initial_migration_reviews(dict_scan)  # 当前候选身份和证据。

    # 精确和模糊队列共享同一身份与明确裁决合同。
    for str_review_field in ("exact_reviews", "fuzzy_reviews"):

        # 没有候选时允许空列表。
        object_reviews = dict_migration.get(str_review_field, [])  # 当前重复复核记录容器。

        # 非列表结构不能形成逐项审计记录。
        if not isinstance(object_reviews, list):

            # 诊断明确损坏字段。
            raise ValueError(f"> ERR: [Python] migration {str_review_field} must be a list")

        # 标识及证据必须逐项对应当前确定性扫描结果。
        dict_actual_identity: dict[str, Any] = {}  # 已持久化裁决的标识到证据映射。

        # 逐项构造映射，以便同时发现重复标识。
        for dict_review in object_reviews:

            # 字符串标识作为迁移证据的稳定主键。
            str_review_id = str(dict_review.get("id", ""))  # 当前持久裁决标识。

            # 保存当前裁决绑定的扫描证据。
            dict_actual_identity[str_review_id] = dict_review.get("evidence")  # 当前持久证据。

        # 当前扫描重新生成的身份映射是比较基准。
        dict_expected_identity: dict[str, Any] = {}  # 当前候选的标识到证据映射。

        # 逐项登记确定性扫描生成的候选。
        for dict_review in dict_expected_reviews[str_review_field]:

            # 当前扫描标识使用初始化阶段同一生成规则。
            str_expected_review_id = str(dict_review.get("id", ""))  # 当前候选标识。

            # 保存当前候选的完整位置或相似度证据。
            dict_expected_identity[str_expected_review_id] = dict_review.get("evidence")  # 当前候选证据。

        # 正文变化导致的候选替换必须重新进入 Agent 复核。
        if (
            len(dict_actual_identity) != len(object_reviews)
            or dict_actual_identity != dict_expected_identity
        ):

            # 不允许旧裁决冒充当前扫描证据。
            raise ValueError(f"> ERR: [Python] stale duplicate review evidence: {str_review_field}")

        # 每项必须选择保留或去重。
        for dict_review in object_reviews:

            # pending 和未知值都阻止完成。
            if dict_review.get("decision") not in {"keep", "deduplicate"}:

                # 诊断绑定候选标识。
                raise ValueError(f"> ERR: [Python] pending duplicate review: {dict_review.get('id', '')}")

            # Agent 标记不确定时必须获得当前用户明确确认。
            if bool(dict_review.get("needs_user_confirmation", False)) and not bool_user_confirmed:

                # 历史状态不能替代本次用户授权。
                raise ValueError("> ERR: [Python] duplicate review requires explicit user confirmation")

# 加载已注册命令事实。
def load_registered_commands(path_skill_root: Path) -> dict[str, dict[str, Any]]:
    """加载清单声明的全部命令记录。

    参数：path_skill_root 为当前技能根目录。
    返回：命令标识到完整 JSON 记录的映射。
    异常：清单、源路径或 commands 容器无效时抛出 ValueError。
    """

    # 命令清单位于文档治理配置同一 registry 根。
    path_registry_root = governance_config_path(path_skill_root).parent  # 当前注册根目录。

    # manifest 是命令源集合的唯一入口。
    dict_manifest = read_json_object(path_registry_root / "manifest.json", "registry manifest")  # 命令清单。

    # source_files 必须是字符串列表。
    object_source_files = dict_manifest.get("source_files")  # 命令源相对路径容器。

    # 错误容器无法安全迭代。
    if not isinstance(object_source_files, list):

        # 缺失命令源清单阻止接口映射完成。
        raise ValueError("> ERR: [Python] registry manifest source_files must be a list")

    # 结果映射支持标识存在性和选项文本检查。
    dict_commands_by_id: dict[str, dict[str, Any]] = {}  # 命令标识到注册记录。

    # 每份源只读取 commands，工作流源自然贡献空列表。
    for object_relative_path in object_source_files:

        # 清单路径必须是非空字符串。
        if not isinstance(object_relative_path, str) or not object_relative_path:

            # 非字符串路径不能进入安全解析。
            raise ValueError("> ERR: [Python] registry source path must be non-empty text")

        # 安全解析并读取当前命令源。
        path_source = resolve_governance_file(path_registry_root, object_relative_path)  # 当前命令源路径。

        # 当前源必须是 JSON 对象。
        dict_source = read_json_object(path_source, f"registry source {object_relative_path}")  # 当前命令源。

        # 缺失 commands 允许空列表。
        object_commands = dict_source.get("commands", [])  # 当前命令记录容器。

        # 非列表结构阻止映射验证。
        if not isinstance(object_commands, list):

            # 诊断绑定当前源路径。
            raise ValueError(
                f"> ERR: [Python] registry source commands must be a list: {object_relative_path}"
            )

        # 每条命令必须是带稳定标识的对象。
        for dict_command in object_commands:

            # 标量或数组记录不能作为命令证据。
            if not isinstance(dict_command, dict):

                # 非对象命令记录无法提供稳定标识和接口文本。
                raise ValueError(
                    f"> ERR: [Python] registry command must be an object: {object_relative_path}"
                )

            # 标识字符串化后登记完整记录。
            dict_commands_by_id[str(dict_command.get("id", ""))] = dict_command  # 当前命令记录。

    # 返回完整命令映射。
    return dict_commands_by_id

# 校验单个接口映射。
def validate_mapped_interface(
    dict_review: dict[str, Any],
    dict_commands_by_id: dict[str, dict[str, Any]],
) -> None:
    """验证一条 mapped 接口事实。

    参数：dict_review 为接口裁决；dict_commands_by_id 为真实命令记录。
    返回：无业务返回值，映射真实时直接结束。
    异常：命令标识缺失、未知或记录不含接口文本时抛出 ValueError。
    """

    # mapped 事实必须声明至少一个命令标识。
    object_command_ids = dict_review.get("command_ids")  # 当前接口命令标识容器。

    # 空映射不能证明脚本用法已注册。
    if not isinstance(object_command_ids, list) or not object_command_ids:

        # 诊断绑定接口值。
        raise ValueError(f"> ERR: [Python] mapped interface has no command ids: {dict_review.get('value', '')}")

    # 收集真实命令记录供接口文本证据检查。
    list_command_records: list[dict[str, Any]] = []  # 当前接口映射命令记录。

    # 每个声明标识必须真实存在。
    for object_command_id in object_command_ids:

        # 字符串化后使用稳定标识查找。
        str_command_id = str(object_command_id)  # 当前映射命令标识。

        # 未注册目标说明映射无效。
        if str_command_id not in dict_commands_by_id:

            # 诊断显示缺失命令。
            raise ValueError(f"> ERR: [Python] interface maps unknown command id: {str_command_id}")

        # 真实记录进入接口文本检查集合。
        list_command_records.append(dict_commands_by_id[str_command_id])

    # 接口值必须实际出现在至少一条映射 JSON 记录中。
    str_interface_value = str(dict_review.get("value", ""))  # 当前接口事实文本。

    # 规范 JSON 保留参数、模板和示例中的选项文本。
    bool_registered = any(  # 当前接口是否真实登记。
        str_interface_value in json.dumps(dict_command, ensure_ascii=False)  # 单条命令文本证据。
        for dict_command in list_command_records  # 遍历映射命令记录。
    )

    # 纯语义猜测不能替代注册源证据。
    if not bool_registered:

        # 诊断绑定缺失接口文本。
        raise ValueError(f"> ERR: [Python] interface value is not registered: {str_interface_value}")

# 校验接口复核记录。
def validate_interface_reviews(
    path_skill_root: Path,
    dict_migration: dict[str, Any],
    dict_scan: dict[str, Any],
) -> None:
    """验证脚本接口裁决覆盖和命令映射。

    参数：path_skill_root 为技能根；dict_migration 为迁移记录；dict_scan 为实时扫描。
    返回：无业务返回值，接口复核闭环时直接结束。
    异常：覆盖不全、裁决 pending 或映射无证据时抛出 ValueError。
    """

    # 接口复核数量必须与当前扫描事实一致。
    object_interface_reviews = dict_migration.get("interface_reviews", [])  # 接口复核容器。

    # 错误容器不能形成逐项裁决。
    if not isinstance(object_interface_reviews, list):

        # 结构错误阻止完成。
        raise ValueError("> ERR: [Python] interface_reviews must be a list")

    # 数量差异表示新增、删除或遗漏接口事实。
    if len(object_interface_reviews) != len(dict_scan["interface_facts"]):

        # 要求重新扫描和 Agent 复核。
        raise ValueError("> ERR: [Python] interface review coverage is incomplete")

    # 接口值、类型和来源共同绑定当前扫描事实，避免等长旧队列蒙混通过。
    list_actual_interface_identity: list[tuple[Any, Any, Any]] = []  # 已持久接口事实身份。

    # 保留迁移记录顺序形成三字段身份。
    for dict_review in object_interface_reviews:

        # 类型、值与来源共同标识单条持久接口事实。
        tuple_review_identity = (  # 当前持久接口身份。
            dict_review.get("kind"),  # 当前接口类型。
            dict_review.get("value"),  # 当前接口值。
            dict_review.get("sources"),  # 当前接口来源集合。
        )

        # 加入实际身份序列供整体比较。
        list_actual_interface_identity.append(tuple_review_identity)

    # 当前扫描身份序列作为持续门禁基准。
    list_expected_interface_identity: list[tuple[Any, Any, Any]] = []  # 当前扫描接口事实身份。

    # 扫描事实使用同一三字段身份结构。
    for dict_fact in dict_scan["interface_facts"]:

        # 类型、值与来源共同标识单条实时接口事实。
        tuple_fact_identity = (  # 当前实时接口身份。
            dict_fact.get("kind"),  # 当前扫描接口类型。
            dict_fact.get("value"),  # 当前扫描接口值。
            dict_fact.get("sources"),  # 当前扫描接口来源集合。
        )

        # 加入预期身份序列供顺序稳定比较。
        list_expected_interface_identity.append(tuple_fact_identity)

    # 任一事实替换都要求重新登记映射结论。
    if list_actual_interface_identity != list_expected_interface_identity:

        # 诊断明确指出接口复核证据陈旧。
        raise ValueError("> ERR: [Python] stale interface review evidence")

    # mapped 项单独收集，not_public 项只需明确理由。
    list_mapped_reviews: list[dict[str, Any]] = []  # 需要命令证据的接口裁决。

    # 每项必须明确 mapped 或 not_public。
    for dict_review in object_interface_reviews:

        # 未知裁决值阻止完成。
        if dict_review.get("decision") not in {"mapped", "not_public"}:

            # 诊断绑定接口文本。
            raise ValueError(f"> ERR: [Python] pending interface review: {dict_review.get('value', '')}")

        # mapped 项进入真实命令证据检查。
        if dict_review.get("decision") == "mapped":

            # 保持迁移记录中的稳定顺序。
            list_mapped_reviews.append(dict_review)

    # 没有 mapped 接口的简单技能不强制拥有命令注册源。
    if not list_mapped_reviews:

        # 明确结束本项条件门禁。
        return

    # 一次加载命令记录后复用到全部 mapped 事实。
    dict_commands_by_id = load_registered_commands(path_skill_root)  # 当前真实命令记录。

    # 每条 mapped 事实独立验证。
    for dict_review in list_mapped_reviews:

        # 单项验证器检查标识和选项文本。
        validate_mapped_interface(dict_review, dict_commands_by_id)

# 汇总文档治理门禁。
def validate_document_governance(
    path_skill_root: Path,
    *,
    bool_require_current: bool,
    bool_user_confirmed: bool = False,
) -> dict[str, Any]:
    """验证已启用文档治理的完整性和当前性。

    参数：path_skill_root 为技能根；bool_require_current 控制完成态；bool_user_confirmed 表示显式确认。
    返回：加载后的治理对象及当前扫描事实。
    异常：任一职责、裁决、确认、接口或漂移门禁失败时抛出 ValueError。
    """

    # 一次加载确保本轮使用同一组持久文件。
    dict_documents = load_governance_documents(path_skill_root)  # 当前治理文件集合。

    # 四份文件生命周期状态必须一致。
    validate_current_governance_status(dict_documents, bool_require_current)

    # 实时扫描提供文档集合、摘要和接口事实。
    dict_scan = scan_skill_documents(path_skill_root)  # 当前权威 Markdown 事实。

    # 文档目录校验返回知识索引所需路径映射。
    dict_scan_by_path = validate_catalog_records(dict_documents["catalog"], dict_scan)  # 当前文档路径映射。

    # 知识记录必须覆盖并绑定全部权威 Markdown。
    validate_knowledge_records(dict_documents["knowledge"], dict_scan_by_path)

    # 重复候选必须由 Agent 明确裁决。
    validate_duplicate_reviews(
        dict_documents["migration"],  # 当前迁移复核记录。
        dict_scan,  # 当前重复候选身份和证据。
        bool_require_current=bool_require_current,  # 当前状态模式。
        bool_user_confirmed=bool_user_confirmed,  # 本次用户确认信号。
    )

    # 脚本接口事实必须进入命令注册源或明确判定非公开。
    validate_interface_reviews(path_skill_root, dict_documents["migration"], dict_scan)

    # finalize 复用实时扫描结果更新完成统计。
    dict_documents["scan"] = dict_scan  # 本轮实时扫描报告。

    # 返回完整治理上下文。
    return dict_documents

# 完成文档治理状态晋级。
def finalize_document_governance(path_skill_root: Path, *, bool_user_confirmed: bool) -> dict[str, Any]:
    """把已由 Agent 复核的文档治理草案晋级为完成态。

    参数：path_skill_root 为技能根；bool_user_confirmed 表示当前用户显式确认不确定裁决。
    返回：完成态状态和文档、知识记录计数。
    异常：任何复核或漂移门禁失败时抛出 ValueError。
    """

    # 完成前先执行全部职责、裁决和摘要门禁。
    dict_documents = validate_document_governance(  # 已通过完成前门禁的治理文件。
        path_skill_root,  # 当前待完成技能根。
        bool_require_current=False,  # 允许 Agent reviewed 草案进入完成流程。
        bool_user_confirmed=bool_user_confirmed,  # 当前用户确认信号。
    )

    # 主配置最后落盘，但先在内存中准备 current 状态。
    dict_documents["governance"]["status"] = "current"  # 主配置完成态。

    # 文档职责目录同步晋级。
    dict_documents["catalog"]["status"] = "current"  # 文档目录完成态。

    # 知识指针索引同步晋级。
    dict_documents["knowledge"]["status"] = "current"  # 知识索引完成态。

    # 迁移记录使用不可再自动覆盖的 finalized 状态。
    dict_documents["migration"]["status"] = "finalized"  # 迁移记录完成态。

    # 用户确认字段记录本次是否处理不确定裁决。
    dict_documents["migration"]["user_confirmation"] = (  # 迁移确认结果。
        "confirmed"  # 本次获得显式用户确认。
        if bool_user_confirmed  # 当前命令携带确认信号。
        else "not_required"  # 没有候选要求额外确认。
    )

    # 首先落盘文档职责完成态。
    write_json_file(dict_documents["catalog_path"], dict_documents["catalog"])

    # 其次落盘知识指针完成态。
    write_json_file(dict_documents["knowledge_path"], dict_documents["knowledge"])

    # 迁移证据在主配置之前持久化。
    write_json_file(dict_documents["migration_path"], dict_documents["migration"])

    # 最后更新主配置，避免提前暴露 current。
    write_json_file(dict_documents["config_path"], dict_documents["governance"])

    # 完成载荷公开全量文档和知识指针计数。
    return {
        "ok": True,
        "enabled": True,
        "status": "current",
        "document_count": len(dict_documents["catalog"]["documents"]),
        "knowledge_count": len(dict_documents["knowledge"]["records"]),
        "user_confirmation": dict_documents["migration"]["user_confirmation"],
    }
