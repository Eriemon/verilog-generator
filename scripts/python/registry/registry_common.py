"""提供命令注册源加载、校验、摘要和 SQLite 元数据共享能力。"""

# 延迟注解求值避免运行时解析复合类型。
from __future__ import annotations

# 标准库依赖负责摘要、JSON、SQLite 与路径处理。
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

# 数据库结构版本用于拒绝不兼容的生成索引。
INT_SCHEMA_VERSION = 2  # 当前命令与文档注册表数据库结构版本

# trigram 同时支持中文连续文本和英文子串召回。
STR_FTS_TOKENIZER = "trigram"  # SQLite FTS5 分词器名称

# 字段集合约束每条 JSON 指令都包含执行与风险信息。
SET_REQUIRED_COMMAND_FIELDS = set(  # 命令记录的完整字段合同
    "aliases boundaries category entrypoint examples id invocation_templates outputs "
    "parameters prerequisites related_command_ids risk subcommand summary title when_to_use".split()
)

# 工作流字段集合约束跨命令流程的最小可检索结构。
SET_REQUIRED_WORKFLOW_FIELDS = {"aliases", "boundaries", "id", "steps", "summary", "title"}  # 工作流字段合同

# 标量文本字段必须使用非空字符串，subcommand 允许空字符串表示根命令。
SET_REQUIRED_COMMAND_TEXT_FIELDS = {"category", "entrypoint", "id", "summary", "title", "when_to_use"}  # 文本字段合同

# 列表字段保存可展示文本或命令关系，关系列表允许为空。
SET_COMMAND_LIST_FIELDS = {"boundaries", "invocation_templates", "prerequisites", "related_command_ids"}  # 列表字段合同

# 专用异常让 CLI 将注册源问题映射到稳定退出码。
class RegistryError(RuntimeError):
    """表示注册源或检索数据库不满足公开合同。"""

# 技能根解析器统一显式路径和脚本相对默认值。
def resolve_skill_root(path_candidate: Path | None = None) -> Path:
    """解析技能根目录。

    参数：path_candidate 为调用方显式提供的技能根目录，可为空。
    返回：规范化后的技能根目录绝对路径。
    """

    # 显式参数优先，便于构建器检查临时技能夹具。
    if path_candidate is not None:

        # resolve 消除相对工作目录差异。
        return path_candidate.resolve()

    # 当前文件位于 scripts/python/registry 下，向上三级即技能根。
    return Path(__file__).resolve().parents[3]

# 注册源寻址器保持所有调用方使用同一目录合同。
def registry_root(path_skill_root: Path) -> Path:
    """返回技能中的命令注册源目录。

    参数：path_skill_root 为技能源码根目录。
    返回：config/registry 目录路径。
    """

    # JSON 和生成数据库共同位于固定配置子目录。
    return path_skill_root / "config" / "registry"

# 数据库寻址器隔离生成工件的固定文件名。
def database_path(path_skill_root: Path) -> Path:
    """返回生成式 SQLite 索引路径。

    参数：path_skill_root 为技能源码根目录。
    返回：registry.sqlite3 文件路径。
    """

    # 数据库与 JSON 同目录，便于安装包完整携带。
    return registry_root(path_skill_root) / "registry.sqlite3"

# 安全路径解析器阻止清单把构建器引向注册源目录外部。
def resolve_registry_source(path_registry_root: Path, str_relative_path: str) -> Path:
    """解析并约束清单声明的 JSON 源路径。

    参数：path_registry_root 为注册源根目录；str_relative_path 为清单相对路径。
    返回：位于注册源根目录内的规范化 JSON 路径。
    异常：路径逃逸根目录或后缀不是 JSON 时抛出 RegistryError。
    """

    # 相对路径对象用于独立拒绝绝对路径输入。
    path_relative = Path(str_relative_path)  # 清单声明的相对路径

    # 两端都规范化后再检查父子关系，覆盖 .. 路径段。
    path_root_resolved = path_registry_root.resolve()  # 规范化注册源根目录

    # 候选路径规范化用于识别父目录逃逸。
    path_source_resolved = (path_registry_root / path_relative).resolve()  # 规范化候选路径

    # 绝对路径和父目录逃逸都违反 JSON 源边界。
    if path_relative.is_absolute() or not path_source_resolved.is_relative_to(path_root_resolved):

        # 错误保留原始清单值，便于定位不安全记录。
        raise RegistryError(
            f"> ERR: [Python] registry source is outside registry root: {str_relative_path}"
        )

    # 注册源只接受 JSON，避免把任意文件内容纳入摘要。
    if path_source_resolved.suffix.casefold() != ".json":

        # 调用方应修正清单而不是猜测文件格式。
        raise RegistryError(f"> ERR: [Python] registry source must be JSON: {str_relative_path}")

    # 返回已通过目录和格式边界的路径。
    return path_source_resolved

# 规范编码器为数据库载荷和 CLI JSON 提供稳定顺序。
def canonical_json(dict_payload: dict[str, Any]) -> str:
    """编码稳定的单对象 JSON 文本。

    参数：dict_payload 为待编码的 JSON 兼容映射。
    返回：保留中文并按键排序的单行 JSON。
    """

    # 固定键顺序减少索引重建产生的无意义差异。
    return json.dumps(dict_payload, ensure_ascii=False, sort_keys=True)

# 字符串列表判定器复用在别名、示例、边界和关系字段。
def is_string_list(obj_value: object, *, bool_allow_empty: bool) -> bool:
    """判断对象是否为符合空值策略的字符串列表。

    参数：obj_value 为待检查对象；bool_allow_empty 控制是否允许空列表。
    返回：列表容器和全部元素类型满足合同时为 True。
    """

    # 非列表容器不能保持 JSON 数组顺序合同。
    if not isinstance(obj_value, list):

        # 调用方根据 False 生成具体字段诊断。
        return False

    # 必需内容列表不能以空数组绕过语义合同。
    if not bool_allow_empty and not obj_value:

        # 空列表不满足必需内容字段。
        return False

    # 每个元素都必须是去除空白后仍非空的字符串。
    return all(isinstance(obj_item, str) and bool(obj_item.strip()) for obj_item in obj_value)

# 唯一性校验器阻止命令或工作流标识发生覆盖。
def validate_unique_ids(
    list_commands: list[dict[str, Any]],
    list_workflows: list[dict[str, Any]],
) -> None:
    """校验命令和工作流标识唯一。

    参数：list_commands 为命令记录；list_workflows 为工作流记录。
    返回：无业务返回值，合法输入直接结束。
    异常：标识重复时抛出 RegistryError。
    """

    # 两类记录使用独立命名空间，但各自内部不得重复。
    for str_kind, list_records in (("command", list_commands), ("workflow", list_workflows)):

        # 字符串化标识避免数字与文本比较口径漂移。
        list_record_ids = [str(dict_record.get("id", "")) for dict_record in list_records]  # 当前记录标识列表

        # 重复标识会使 SQLite 主键和关系解析产生歧义。
        if len(list_record_ids) != len(set(list_record_ids)):

            # 错误前缀满足仓库可见异常协议。
            raise RegistryError(f"> ERR: [Python] duplicate {str_kind} id detected")

# 关系校验器保证所有检索导航都能落到真实命令。
def validate_relations(
    list_commands: list[dict[str, Any]],
    list_workflows: list[dict[str, Any]],
) -> None:
    """校验命令关系和工作流步骤引用。

    参数：list_commands 为命令记录；list_workflows 为工作流记录。
    返回：无业务返回值，引用完整时直接结束。
    异常：引用未知命令时抛出 RegistryError。
    """

    # 命令标识集合是两类引用的共同目标命名空间。
    set_command_ids = {str(dict_command["id"]) for dict_command in list_commands}  # 可引用命令标识

    # 每条相关命令边必须指向已加载记录。
    for dict_command in list_commands:

        # 当前命令标识用于生成可操作诊断。
        str_command_id = str(dict_command["id"])  # 关系源命令标识

        # 空值在 JSON 合同中无意义，转换时直接忽略。
        for str_related_id in map(str, dict_command["related_command_ids"]):

            # 未注册目标会造成查询结果中的断链。
            if str_related_id not in set_command_ids:

                # 错误同时展示关系源和缺失目标。
                raise RegistryError(
                    f"> ERR: [Python] command {str_command_id} references "
                    f"unknown related command id: {str_related_id}"
                )

    # 工作流步骤同样只允许引用公开命令记录。
    for dict_workflow in list_workflows:

        # 缺失标识仍使用稳定占位符生成诊断。
        str_workflow_id = str(dict_workflow.get("id", "<missing-id>"))  # 当前工作流标识

        # 步骤顺序保留在 JSON 中，校验只检查目标存在性。
        for str_step_id in map(str, dict_workflow.get("steps", [])):

            # 未知步骤不能进入可交付检索索引。
            if str_step_id not in set_command_ids:

                # 错误明确指出失效的工作流边。
                raise RegistryError(
                    f"> ERR: [Python] workflow {str_workflow_id} references "
                    f"unknown command id: {str_step_id}"
                )

# 命令基础字段校验器负责标量文本和字符串列表。
def validate_command_text_fields(dict_command: dict[str, Any], str_command_id: str) -> None:
    """校验命令文本与列表字段。

    参数：dict_command 为命令记录；str_command_id 为诊断标识。
    返回：无业务返回值，字段合法时直接结束。
    异常：文本或列表字段无效时抛出 RegistryError。
    """

    # 必需标量字段不能使用空文本或其他 JSON 类型。
    for str_text_field in SET_REQUIRED_COMMAND_TEXT_FIELDS:

        # strip 后空字符串不具备可检索或展示语义。
        if not isinstance(dict_command[str_text_field], str) or not dict_command[str_text_field].strip():

            # 诊断绑定命令和具体字段。
            raise RegistryError(
                f"> ERR: [Python] command {str_command_id} {str_text_field} must be non-empty text"
            )

    # 根命令允许空 subcommand，但类型必须固定为字符串。
    if not isinstance(dict_command["subcommand"], str):

        # 禁止数字或空值进入 CLI 动作字段。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} subcommand must be text")

    # 命令列表字段只允许非空字符串元素。
    for str_list_field in SET_COMMAND_LIST_FIELDS:

        # 只有 related_command_ids 允许命令没有相关边。
        bool_allow_empty = str_list_field == "related_command_ids"  # 当前字段空列表策略

        # 错误容器或空文本元素会破坏检索和关系校验。
        if not is_string_list(dict_command[str_list_field], bool_allow_empty=bool_allow_empty):

            # 诊断明确违反合同的列表字段。
            raise RegistryError(
                f"> ERR: [Python] command {str_command_id} {str_list_field} must be a string list"
            )

# 双语内容校验器负责别名和正反例嵌套结构。
def validate_command_language_fields(dict_command: dict[str, Any], str_command_id: str) -> None:
    """校验命令别名和示例。

    参数：dict_command 为命令记录；str_command_id 为诊断标识。
    返回：无业务返回值，双语内容合法时直接结束。
    异常：别名或示例结构无效时抛出 RegistryError。
    """

    # 别名必须同时覆盖中文和英文自然语言问询。
    dict_aliases = dict_command.get("aliases", {})  # 当前命令双语别名

    # 正反例共同表达推荐路径和禁止边界。
    dict_examples = dict_command.get("examples", {})  # 当前命令示例集合

    # 两个嵌套字段必须是固定键集合的映射。
    if not isinstance(dict_aliases, dict) or set(dict_aliases) != {"zh", "en"}:

        # 字符串等错误类型不能延迟到检索文本投影。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} aliases has invalid fields")

    # 正反例映射同样拒绝额外或缺失键。
    if not isinstance(dict_examples, dict) or set(dict_examples) != {"valid", "invalid"}:

        # 严格键集合防止示例协议静默扩展。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} examples has invalid fields")

    # 每种语言至少提供一个非空别名。
    for str_language in ("zh", "en"):

        # 别名必须是可直接加入检索文本的字符串列表。
        if not is_string_list(dict_aliases[str_language], bool_allow_empty=False):

            # 诊断保留语言标识便于修复源文件。
            raise RegistryError(
                f"> ERR: [Python] command {str_command_id} aliases.{str_language} must be a string list"
            )

    # 两类示例都至少包含一条非空文本。
    for str_example_kind in ("valid", "invalid"):

        # 示例列表直接进入问询结果，必须可稳定展示。
        if not is_string_list(dict_examples[str_example_kind], bool_allow_empty=False):

            # 诊断明确正例或反例类型。
            raise RegistryError(
                f"> ERR: [Python] command {str_command_id} examples.{str_example_kind} must be a string list"
            )

# 参数校验器确保每个 CLI 参数具有稳定字段和类型。
def validate_command_parameters(dict_command: dict[str, Any], str_command_id: str) -> None:
    """校验命令参数数组。

    参数：dict_command 为命令记录；str_command_id 为诊断标识。
    返回：无业务返回值，参数结构合法时直接结束。
    异常：参数容器、字段或值类型无效时抛出 RegistryError。
    """

    # 根命令可以没有参数，但容器必须保持数组类型。
    if not isinstance(dict_command["parameters"], list):

        # 字符串等容器会破坏参数逐项展示。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} parameters must be a list")

    # 逐条验证参数映射，避免数据库保存不可解释结构。
    for dict_parameter in dict_command["parameters"]:

        # 参数记录必须严格使用三个公开字段。
        if not isinstance(dict_parameter, dict) or set(dict_parameter) != {"name", "required", "description"}:

            # 结构错误绑定所属命令。
            raise RegistryError(f"> ERR: [Python] command {str_command_id} parameter has invalid fields")

        # 名称和说明必须是非空文本，required 必须是真布尔值。
        if (
            not isinstance(dict_parameter["name"], str)
            or not dict_parameter["name"].strip()
            or not isinstance(dict_parameter["description"], str)
            or not dict_parameter["description"].strip()
            or not isinstance(dict_parameter["required"], bool)
        ):

            # 类型错误不能依赖下游展示器临时转换。
            raise RegistryError(f"> ERR: [Python] command {str_command_id} parameter values are invalid")

# 输出和风险校验器保护自动化退出码与写入边界。
def validate_command_protocols(dict_command: dict[str, Any], str_command_id: str) -> None:
    """校验命令输出和风险协议。

    参数：dict_command 为命令记录；str_command_id 为诊断标识。
    返回：无业务返回值，协议合法时直接结束。
    异常：输出或风险协议无效时抛出 RegistryError。
    """

    # 输出合同固定声明格式以及成功、失败退出码。
    dict_outputs = dict_command["outputs"]  # 当前命令输出协议

    # 输出映射只允许三个稳定字段。
    if not isinstance(dict_outputs, dict) or set(dict_outputs) != {
        "failure_exit_codes",
        "format",
        "success_exit_codes",
    }:

        # 拒绝缺失退出码或格式说明的命令记录。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} outputs has invalid fields")

    # 格式必须是非空文本，两类退出码必须是非空整数列表。
    if (
        not isinstance(dict_outputs["format"], str)
        or not dict_outputs["format"].strip()
        or not all(
            isinstance(dict_outputs[str_code_kind], list)
            and bool(dict_outputs[str_code_kind])
            and all(isinstance(int_code, int) for int_code in dict_outputs[str_code_kind])
            for str_code_kind in ("success_exit_codes", "failure_exit_codes")
        )
    ):

        # 输出协议类型错误会误导自动化调用方。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} outputs values are invalid")

    # 风险合同固定声明等级、写入属性和警告文本。
    dict_risk = dict_command["risk"]  # 当前命令风险协议

    # 字符串等非映射值必须在索引写入前拒绝。
    if not isinstance(dict_risk, dict) or set(dict_risk) != {"level", "warning", "writes"}:

        # 诊断明确 risk 字段，防止查询阶段才崩溃。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} risk has invalid fields")

    # 风险等级和警告使用非空文本，写入属性必须是真布尔值。
    if (
        not isinstance(dict_risk["level"], str)
        or not dict_risk["level"].strip()
        or not isinstance(dict_risk["warning"], str)
        or not dict_risk["warning"].strip()
        or not isinstance(dict_risk["writes"], bool)
    ):

        # 类型错误会破坏人类风险输出和写入边界判断。
        raise RegistryError(f"> ERR: [Python] command {str_command_id} risk values are invalid")

# 命令结构校验器组合独立的字段族门禁。
def validate_commands(list_commands: list[dict[str, Any]]) -> None:
    """校验全部命令记录。

    参数：list_commands 为全部分类汇总后的命令记录。
    返回：无业务返回值，全部记录合法时直接结束。
    异常：记录为空、类型错误或字段合同无效时抛出 RegistryError。
    """

    # 空注册表无法支撑文档渐进披露。
    if not list_commands:

        # 明确提示事实源缺少命令记录。
        raise RegistryError("> ERR: [Python] registry contains no command records")

    # 每条命令依次通过基础、语言、参数和协议门禁。
    for dict_command in list_commands:

        # 非对象记录不能安全读取字段或进入 SQLite 载荷。
        if not isinstance(dict_command, dict):

            # 结构错误必须转为领域异常而不是泄漏 AttributeError。
            raise RegistryError("> ERR: [Python] command record must be a JSON object")

        # 标识缺失时使用占位符，避免校验器自身崩溃。
        str_command_id = str(dict_command.get("id", "<missing-id>"))  # 当前命令诊断标识

        # 额外字段和缺失字段都意味着 schema 漂移。
        if set(dict_command) != SET_REQUIRED_COMMAND_FIELDS:

            # 严格字段集合确保 SQLite 载荷可预测。
            raise RegistryError(f"> ERR: [Python] command {str_command_id} has an invalid field set")

        # 文本门禁验证标量说明与字符串列表。
        validate_command_text_fields(dict_command, str_command_id)

        # 语言门禁验证双语别名与正反示例。
        validate_command_language_fields(dict_command, str_command_id)

        # 参数门禁验证 CLI 参数记录结构。
        validate_command_parameters(dict_command, str_command_id)

        # 协议门禁验证退出码与写入风险边界。
        validate_command_protocols(dict_command, str_command_id)

# 工作流结构校验器阻止无效步骤容器进入关系校验。
def validate_workflows(list_workflows: list[dict[str, Any]]) -> None:
    """校验工作流记录字段和嵌套容器。

    参数：list_workflows 为全部工作流记录。
    返回：无业务返回值，全部记录合法时直接结束。
    异常：记录类型或字段合同无效时抛出 RegistryError。
    """

    # 每条工作流独立执行严格字段集合校验。
    for dict_workflow in list_workflows:

        # 非对象记录不能建立稳定工作流关系。
        if not isinstance(dict_workflow, dict):

            # 统一领域异常保持 CLI 退出码稳定。
            raise RegistryError("> ERR: [Python] workflow record must be a JSON object")

        # 标识用于后续具体诊断。
        str_workflow_id = str(dict_workflow.get("id", "<missing-id>"))  # 当前工作流诊断标识

        # 严格字段集合避免生成数据库静默接受 schema 漂移。
        if set(dict_workflow) != SET_REQUIRED_WORKFLOW_FIELDS:

            # 错误绑定工作流标识，便于直接修复源文件。
            raise RegistryError(f"> ERR: [Python] workflow {str_workflow_id} has an invalid field set")

        # aliases 必须是中英文别名映射。
        if not isinstance(dict_workflow["aliases"], dict):

            # 非映射别名会破坏检索文本投影。
            raise RegistryError(f"> ERR: [Python] workflow {str_workflow_id} aliases must be an object")

        # steps 必须保持有序命令标识列表。
        if not isinstance(dict_workflow["steps"], list):

            # 字符串步骤会被错误拆成单字符关系，必须拒绝。
            raise RegistryError(f"> ERR: [Python] workflow {str_workflow_id} steps must be a list")

# 注册源加载器按清单顺序聚合分类文件。
def load_registry_manifest(path_registry_root: Path) -> dict[str, Any]:
    """读取并校验命令注册清单基础字段。

    参数：path_registry_root 为 config/registry 目录。
    返回：已验证 schema 版本、command_schema 和 source_files 的清单。
    异常：文件、JSON 或清单字段无效时抛出 RegistryError。
    """

    # manifest 是全部命令、工作流和文档注册源的入口。
    path_manifest = path_registry_root / "manifest.json"  # 注册源清单路径

    # 文件与 JSON 错误统一转换为领域异常。
    try:

        # UTF-8 读取保留中文说明。
        object_manifest = json.loads(path_manifest.read_text(encoding="utf-8"))  # 注册清单载荷

    # 清单不可读时保留底层诊断。
    except (OSError, json.JSONDecodeError) as object_error:

        # 构建器使用稳定索引错误退出码。
        raise RegistryError(f"> ERR: [Python] cannot load registry manifest: {object_error}") from object_error

    # 顶层必须是 JSON 对象。
    if not isinstance(object_manifest, dict):

        # 列表或标量不能提供清单字段。
        raise RegistryError("> ERR: [Python] registry manifest must be a JSON object")

    # schema 版本必须和当前构建器一致。
    if object_manifest.get("schema_version") != INT_SCHEMA_VERSION:

        # 禁止猜测跨版本兼容性。
        raise RegistryError("> ERR: [Python] registry manifest schema_version is incompatible")

    # command_schema 必须是非空相对路径文本。
    object_command_schema = object_manifest.get("command_schema")  # 命令 schema 配置值

    # 错误类型不能进入路径解析器。
    if not isinstance(object_command_schema, str) or not object_command_schema:

        # 缺失结构合同阻止加载。
        raise RegistryError("> ERR: [Python] registry manifest command_schema must be a string")

    # source_files 必须是非空字符串列表。
    object_source_files = object_manifest.get("source_files")  # 命令与工作流源路径容器

    # 空清单不能产生可用索引。
    if not isinstance(object_source_files, list) or not object_source_files:

        # 诊断明确 source_files 容器合同。
        raise RegistryError("> ERR: [Python] registry manifest source_files must be a non-empty list")

    # 每个源路径必须是非空文本。
    if not all(isinstance(object_path, str) and object_path for object_path in object_source_files):

        # 禁止隐式字符串化路径。
        raise RegistryError("> ERR: [Python] registry manifest source_files must contain strings")

    # 类型收窄后的清单供后续加载步骤复用。
    return object_manifest

# 命令 schema 加载器验证可发布结构文件可读取。
def validate_command_schema(path_registry_root: Path, dict_manifest: dict[str, Any]) -> None:
    """验证 manifest 声明的命令 JSON Schema。

    参数：path_registry_root 为注册根；dict_manifest 为已验证清单。
    返回：无业务返回值，schema 为 JSON 对象时直接结束。
    异常：路径、文件或 JSON 无效时抛出 RegistryError。
    """

    # 安全路径解析阻止 schema 逃逸注册根。
    path_command_schema = resolve_registry_source(  # manifest 声明的命令 schema 安全绝对路径。
        path_registry_root,  # 当前注册根目录。
        str(dict_manifest["command_schema"]),  # command_schema 相对路径字段值。
    )

    # 文件和 JSON 错误转换为领域异常。
    try:

        # 运行时字段校验与可发布 schema 共享事实边界。
        object_command_schema = json.loads(path_command_schema.read_text(encoding="utf-8"))  # 命令 schema 载荷

    # 解析位置保留在错误文本中。
    except (OSError, json.JSONDecodeError) as object_error:

        # 构建器据此拒绝生成数据库。
        raise RegistryError(f"> ERR: [Python] cannot load command schema: {object_error}") from object_error

    # 顶层非对象不能表达 properties 合同。
    if not isinstance(object_command_schema, dict):

        # 数组或标量 schema 无效。
        raise RegistryError("> ERR: [Python] command schema must be a JSON object")

# 分类源加载器聚合命令和工作流记录。
def load_command_workflow_sources(
    path_registry_root: Path,
    list_source_files: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读取清单声明的命令和工作流源。

    参数：path_registry_root 为注册根；list_source_files 为安全相对 JSON 路径。
    返回：命令记录与工作流记录两个列表。
    异常：路径、文件、schema 版本或记录容器无效时抛出 RegistryError。
    """

    # 两类输出按清单和文件内顺序聚合。
    list_commands: list[dict[str, Any]] = []  # 跨分类命令记录

    # 工作流使用独立集合保留步骤顺序。
    list_workflows: list[dict[str, Any]] = []  # 跨文件工作流记录

    # 清单是允许进入索引的唯一源集合。
    for str_relative_path in list_source_files:

        # 每个源路径必须保持在 registry 根内。
        path_source = resolve_registry_source(path_registry_root, str_relative_path)  # 当前注册源路径

        # 文件和 JSON 错误绑定当前相对路径。
        try:

            # UTF-8 解析保留中文注册内容。
            object_document = json.loads(path_source.read_text(encoding="utf-8"))  # 当前注册源载荷

        # 不可读源阻止构建。
        except (OSError, json.JSONDecodeError) as object_error:

            # 诊断显示具体源文件。
            raise RegistryError(
                f"> ERR: [Python] cannot load registry source {str_relative_path}: {object_error}"
            ) from object_error

        # 分类源顶层必须提供 schema_version 和记录容器。
        if not isinstance(object_document, dict):

            # 非对象源不能安全提取记录。
            raise RegistryError(
                f"> ERR: [Python] registry source {str_relative_path} must be a JSON object"
            )

        # 分类源版本必须与清单和代码一致。
        if object_document.get("schema_version") != INT_SCHEMA_VERSION:

            # 禁止混合版本源。
            raise RegistryError(
                f"> ERR: [Python] registry source {str_relative_path} has incompatible schema_version"
            )

        # 缺失单类记录时使用空列表。
        object_commands = object_document.get("commands", [])  # 当前源命令容器

        # 工作流容器独立读取。
        object_workflows = object_document.get("workflows", [])  # 当前源工作流容器

        # 两类记录都必须使用列表。
        if not isinstance(object_commands, list) or not isinstance(object_workflows, list):

            # 错误容器阻止聚合。
            raise RegistryError(
                f"> ERR: [Python] registry source {str_relative_path} records must be lists"
            )

        # 当前分类命令追加到全局集合。
        list_commands.extend(object_commands)

        # 当前源工作流追加到独立集合。
        list_workflows.extend(object_workflows)

    # 返回完整记录集合供字段和关系校验。
    return list_commands, list_workflows

# 注册源加载器组合清单、schema、字段和关系门禁。
def load_registry(
    path_skill_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """加载并校验清单、命令和工作流。

    参数：path_skill_root 为当前技能源码根目录。
    返回：清单、命令记录列表和工作流记录列表。
    异常：文件、JSON、字段或关系合同无效时抛出 RegistryError。
    """

    # 固定注册根约束所有清单相对路径。
    path_registry_root = registry_root(path_skill_root)  # 当前注册源目录

    # 基础清单先验证 schema 版本和路径容器。
    dict_manifest = load_registry_manifest(path_registry_root)  # 已通过结构校验的注册清单。

    # 可发布命令 schema 必须可读取。
    validate_command_schema(path_registry_root, dict_manifest)

    # 分类 JSON 源聚合为两类记录。
    tuple_registry_records = load_command_workflow_sources(  # 分类源聚合后的命令与工作流二元组。
        path_registry_root,  # 分类 JSON 的安全根目录。
        dict_manifest["source_files"],  # manifest 中的 source_files 清单值。
    )

    # 元组首项是命令记录集合。
    list_commands = tuple_registry_records[0]  # 从分类源加载的命令记录集合。

    # 元组次项是工作流记录集合。
    list_workflows = tuple_registry_records[1]  # 从分类源加载的工作流记录集合。

    # 字段合同先于关系校验。
    validate_commands(list_commands)

    # 工作流结构必须先于标识和关系校验。
    validate_workflows(list_workflows)

    # 两类标识分别保持唯一。
    validate_unique_ids(list_commands, list_workflows)

    # 所有关系边必须指向真实命令。
    validate_relations(list_commands, list_workflows)

    # 原始清单用于摘要，记录列表用于构建。
    return dict_manifest, list_commands, list_workflows

# 文档 schema 校验器逐份检查可发布结构合同。
def validate_document_schema_files(
    path_registry_root: Path,
    list_document_schemas: list[str],
) -> None:
    """验证清单声明的文档治理 JSON Schema 文件。

    参数：path_registry_root 为注册根；list_document_schemas 为 schema 相对路径。
    返回：无业务返回值，全部 schema 为 JSON 对象时直接结束。
    异常：路径、文件或 JSON 无效时抛出 RegistryError。
    """

    # 每份 schema 都必须保持在 registry 根内。
    for str_relative_path in list_document_schemas:

        # 安全解析当前 schema 路径。
        path_schema = resolve_registry_source(path_registry_root, str_relative_path)  # 当前文档 schema 路径

        # 文档 schema 读取失败统一转换为构建领域异常。
        try:

            # schema 作为对象参与摘要和发布。
            object_schema = json.loads(path_schema.read_text(encoding="utf-8"))  # 当前文档 schema 载荷

        # 诊断绑定具体 schema 文件。
        except (OSError, json.JSONDecodeError) as object_error:

            # 构建器拒绝不完整结构合同。
            raise RegistryError(
                f"> ERR: [Python] cannot load document schema {str_relative_path}: {object_error}"
            ) from object_error

        # 顶层非对象不能表达 JSON Schema。
        if not isinstance(object_schema, dict):

            # 文档结构合同不能使用数组或标量顶层。
            raise RegistryError(
                f"> ERR: [Python] document schema must be an object: {str_relative_path}"
            )

# 文档治理源加载器读取职责、知识和迁移 JSON 对象。
def load_document_source_objects(
    path_registry_root: Path,
    list_document_sources: list[str],
) -> dict[str, dict[str, Any]]:
    """加载清单声明的文档治理源对象。

    参数：path_registry_root 为注册根；list_document_sources 为源相对路径。
    返回：相对路径到 JSON 对象的映射。
    异常：路径、文件、JSON 或顶层类型无效时抛出 RegistryError。
    """

    # 结果映射按清单相对路径保存源对象。
    dict_sources: dict[str, dict[str, Any]] = {}  # 文档治理源对象集合

    # 每份源都必须安全解析且可读取。
    for str_relative_path in list_document_sources:

        # 文档源复用注册根路径约束。
        path_source = resolve_registry_source(path_registry_root, str_relative_path)  # 当前治理源路径

        # 文件和 JSON 错误绑定当前源。
        try:

            # UTF-8 保留中文职责与摘要。
            object_source = json.loads(path_source.read_text(encoding="utf-8"))  # 当前治理源载荷

        # 不可读源阻止联合索引构建。
        except (OSError, json.JSONDecodeError) as object_error:

            # 诊断显示清单相对路径。
            raise RegistryError(
                f"> ERR: [Python] cannot load document source {str_relative_path}: {object_error}"
            ) from object_error

        # 顶层必须是对象以提供状态和记录。
        if not isinstance(object_source, dict):

            # 非对象源不能进入 SQLite。
            raise RegistryError(
                f"> ERR: [Python] document source must be an object: {str_relative_path}"
            )

        # 当前源通过基础结构校验。
        dict_sources[str_relative_path] = object_source  # 相对路径对应的治理源对象

    # 返回完整源对象映射。
    return dict_sources

# 完成态文档源校验器提取两类 SQLite 记录。
def finalized_document_records(
    dict_sources: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """验证文档源完成态并返回职责与知识记录。

    参数：dict_sources 为文档治理源对象映射。
    返回：文档职责记录和知识指针记录两个列表。
    异常：必需源、状态、容器或标识无效时抛出 RegistryError。
    """

    # 四份固定事实源必须全部存在。
    tuple_required_sources = (  # 文档注册化必需源路径
        "document-governance.json",  # 可选门禁主配置
        "documents/catalog.json",  # 文档职责目录
        "knowledge/index.json",  # 权威 Markdown 知识指针
        "migrations/initial-document-registration.json",  # 首次迁移复核证据
    )

    # 缺失源逐项给出可定位诊断。
    for str_required_path in tuple_required_sources:

        # 任一源缺失都破坏完成态闭环。
        if str_required_path not in dict_sources:

            # 诊断命名缺失源。
            raise RegistryError(f"> ERR: [Python] missing document registry source: {str_required_path}")

    # 主配置必须处于 current。
    if dict_sources["document-governance.json"].get("status") != "current":

        # 草案不得进入可查询数据库。
        raise RegistryError("> ERR: [Python] document governance is not current")

    # 三份附属源必须同步完成。
    if (
        dict_sources["documents/catalog.json"].get("status") != "current"
        or dict_sources["knowledge/index.json"].get("status") != "current"
        or dict_sources["migrations/initial-document-registration.json"].get("status") != "finalized"
    ):

        # 部分完成状态不能生成联合索引。
        raise RegistryError("> ERR: [Python] document registry sources are not finalized")

    # 职责与知识记录使用独立列表容器。
    object_documents = dict_sources["documents/catalog.json"].get("documents")  # 文档职责容器

    # 知识记录从权威指针索引提取。
    object_knowledge = dict_sources["knowledge/index.json"].get("records")  # 知识指针容器

    # 两个错误容器统一拒绝。
    if not isinstance(object_documents, list) or not isinstance(object_knowledge, list):

        # SQLite 构建只接受列表记录。
        raise RegistryError("> ERR: [Python] document registry records must be lists")

    # 文档主键必须非空且唯一。
    list_document_ids = [  # 文档记录标识集合
        str(dict_record.get("id", ""))  # 当前文档标识
        for dict_record in object_documents  # 遍历职责记录
    ]

    # 空值或重复值破坏 documents 主键。
    if not all(list_document_ids) or len(list_document_ids) != len(set(list_document_ids)):

        # 主键错误阻止构建。
        raise RegistryError("> ERR: [Python] document ids must be non-empty and unique")

    # 知识主键使用独立命名空间。
    list_knowledge_ids = [  # 知识记录标识集合
        str(dict_record.get("id", ""))  # 当前知识标识
        for dict_record in object_knowledge  # 遍历知识指针
    ]

    # 空知识标识或重复知识标识破坏 knowledge 主键。
    if not all(list_knowledge_ids) or len(list_knowledge_ids) != len(set(list_knowledge_ids)):

        # 知识主键错误阻止构建。
        raise RegistryError("> ERR: [Python] knowledge ids must be non-empty and unique")

    # 返回已验证记录集合。
    return object_documents, object_knowledge

# 文档记录加载器把完成态职责目录和知识指针纳入 schema v2。
def load_document_records(
    path_skill_root: Path,
    dict_manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """加载已完成的文档目录和知识索引记录。

    参数：path_skill_root 为技能根；dict_manifest 为已校验注册清单。
    返回：文档职责记录和知识指针记录两个列表。
    异常：清单、路径、状态或记录结构无效时抛出 RegistryError。
    """

    # 两个清单分别声明文档治理源和可发布 schema。
    object_document_sources = dict_manifest.get("document_sources")  # 文档治理源路径容器

    # schema 路径使用独立容器。
    object_document_schemas = dict_manifest.get("document_schemas")  # 文档结构约束路径容器

    # 源文件清单必须是非空字符串列表。
    if not is_string_list(object_document_sources, bool_allow_empty=False):

        # 缺失文档源不能构建 schema v2。
        raise RegistryError("> ERR: [Python] registry manifest document_sources must be a string list")

    # schema 清单同样必须完整。
    if not is_string_list(object_document_schemas, bool_allow_empty=False):

        # 缺失 schema 会让 JSON 合同不可审查。
        raise RegistryError("> ERR: [Python] registry manifest document_schemas must be a string list")

    # 注册根约束全部文档路径。
    path_registry_root = registry_root(path_skill_root)  # 当前注册源根目录

    # 四份可发布 schema 必须是 JSON 对象。
    validate_document_schema_files(path_registry_root, object_document_schemas)

    # 文档职责、知识和迁移源按清单加载。
    dict_sources = load_document_source_objects(path_registry_root, object_document_sources)  # 治理源对象

    # 完成态校验后返回两类 SQLite 记录。
    return finalized_document_records(dict_sources)

# 摘要器把清单和全部声明源绑定为一个版本事实。
def source_digest(path_skill_root: Path, dict_manifest: dict[str, Any]) -> str:
    """计算注册源的确定性 SHA-256 摘要。

    参数：path_skill_root 为技能根；dict_manifest 为已校验清单。
    返回：清单和全部源文件内容的十六进制摘要。
    """

    # 所有相对文件名均以 registry 根为锚点。
    path_registry_root = registry_root(path_skill_root)  # 摘要输入根目录

    # manifest 自身也必须参与漂移检测。
    list_relative_paths = [  # 构建数据库摘要的全部事实源
        "manifest.json",  # 注册源清单
        str(dict_manifest["command_schema"]),  # 命令记录 schema
        *dict_manifest.get("source_files", []),  # 分类命令与工作流源
        *dict_manifest.get("document_schemas", []),  # 文档治理 JSON Schema
        *dict_manifest.get("document_sources", []),  # 文档职责、知识和迁移源
    ]

    # 单个摘要器按稳定路径顺序接收路径和内容。
    object_digest = hashlib.sha256()  # 注册源内容摘要器

    # 排序消除清单排列以外的遍历不确定性。
    for str_relative_path in sorted(list_relative_paths):

        # 当前输入路径仅用于读取，不会写入源文件。
        path_source = resolve_registry_source(path_registry_root, str_relative_path)  # 当前摘要输入文件

        # 路径进入摘要可区分内容相同但职责不同的文件。
        object_digest.update(str_relative_path.replace("\\", "/").encode("utf-8"))

        # 空字节划分路径和文件内容，避免拼接歧义。
        object_digest.update(b"\0")

        # 规范 JSON 消除 Git 换行与缩进转换，同时保留全部语义值和列表顺序。
        obj_source_data = json.loads(path_source.read_text(encoding="utf-8"))  # 当前 JSON 语义值

        # 紧凑稳定序列化把 JSON 语义值转换为跨平台摘要字节。
        bytes_canonical_json = json.dumps(  # 跨平台稳定的摘要输入
            obj_source_data,  # 已由注册加载流程验证的 JSON 数据
            ensure_ascii=False,  # 中文按 UTF-8 原文参与摘要
            sort_keys=True,  # 对象键顺序不影响生成数据库内容
            separators=(",", ":"),  # 空白格式不进入语义摘要
        ).encode("utf-8")

        # 当前规范 JSON 内容进入注册源摘要。
        object_digest.update(bytes_canonical_json)

        # 第二个分隔符划分相邻文件。
        object_digest.update(b"\0")

    # 十六进制文本便于写入 SQLite 元数据和 JSON 输出。
    return object_digest.hexdigest()

# 检索文本投影器选择稳定且有召回价值的字段。
def record_search_text(dict_record: dict[str, Any]) -> str:
    """把命令或工作流投影为中英文检索文本。

    参数：dict_record 为命令或工作流 JSON 记录。
    返回：由标识、标题、摘要、别名和参数说明组成的换行文本。
    """

    # 别名容器缺失时使用空映射兼容工作流最小结构。
    dict_aliases = dict_record.get("aliases", {})  # 当前记录别名映射

    # 中文别名与英文别名共同进入 FTS 文档。
    list_aliases = [*dict_aliases.get("zh", []), *dict_aliases.get("en", [])]  # 双语别名列表

    # 聚合入口把公开 operation 登记为参数，因此参数名与说明也必须可检索。
    list_parameter_parts = [  # 当前命令参数检索片段
        str(dict_parameter.get(str_field, ""))  # 保留参数名或人类说明
        for dict_parameter in dict_record.get("parameters", [])  # 工作流没有 parameters 时为空
        for str_field in ("name", "description")  # 固定顺序保证稳定索引文本
        if dict_parameter.get(str_field)  # 跳过空值
    ]

    # CLI 连字符名称追加空格化形式，使自然语言 release gate 可命中 release-gate。
    list_parameter_aliases = [  # 当前参数自然语言别名
        str_parameter_part.replace("-", " ")  # 只做稳定、可逆的连字符归一化
        for str_parameter_part in list_parameter_parts  # 遍历已验证的参数文本
        if "-" in str_parameter_part  # 无连字符文本无需重复
    ]

    # 字段顺序固定，保证相同 JSON 产生稳定检索文本。
    list_parts = [  # 当前记录检索字段
        str(dict_record.get("id", "")),  # 稳定记录标识
        str(dict_record.get("category", "")),  # 功能分类
        str(dict_record.get("entrypoint", "")),  # 公开 CLI 文件
        str(dict_record.get("subcommand", "")),  # 可选子命令
        str(dict_record.get("title", "")),  # 人类标题
        str(dict_record.get("summary", "")),  # 功能摘要
        str(dict_record.get("when_to_use", "")),  # 使用时机
        *map(str, list_aliases),  # 中英文自然语言别名
        *list_parameter_parts,  # 聚合子命令、选项和参数说明
        *list_parameter_aliases,  # 连字符 CLI 名称的自然语言形式
    ]

    # 空字段不产生多余检索分隔行。
    return "\n".join(str_part for str_part in list_parts if str_part)

# 元数据读取器把 SQLite 行转换为便于比较的映射。
def fetch_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    """读取 SQLite 索引元数据。

    参数：connection 为已打开的注册表数据库连接。
    返回：元数据键值字符串映射。
    异常：metadata 表缺失或不可读时抛出 RegistryError。
    """

    # 数据库结构错误统一转换为注册表领域异常。
    try:

        # 字符串化保持 SQLite 值与源推导值比较一致。
        return {
            str(row_key): str(row_value)
            for row_key, row_value in connection.execute("SELECT key, value FROM metadata")
        }

    # 损坏或旧 schema 都需要构建器重新生成数据库。
    except sqlite3.Error as object_error:

        # 原始 SQLite 诊断附加在统一错误前缀后。
        raise RegistryError(f"> ERR: [Python] cannot read registry metadata: {object_error}") from object_error

# 数据库门禁在每次问询前核对源摘要和 FTS 兼容性。
def ensure_database_current(
    path_skill_root: Path,
) -> tuple[sqlite3.Connection, dict[str, str]]:
    """打开并验证当前 SQLite 索引。

    参数：path_skill_root 为技能源码根目录。
    返回：保持打开的数据库连接和已验证元数据。
    异常：数据库缺失、损坏、过期或 FTS 不兼容时抛出 RegistryError。
    """

    # 生成数据库必须位于清单约定的固定路径。
    path_database = database_path(path_skill_root)  # 当前 SQLite 索引路径

    # 缺失数据库不能降级为直接扫描 JSON，避免隐藏构建漂移。
    if not path_database.is_file():

        # 错误给出明确的重建动作。
        raise RegistryError("> ERR: [Python] registry database is missing; run build_registry.py --write")

    # 源记录用于重新推导摘要与计数。
    tuple_registry = load_registry(path_skill_root)  # 已校验注册源三元组

    # schema v2 文档记录用于推导联合索引计数。
    tuple_document_records = load_document_records(path_skill_root, tuple_registry[0])  # 文档与知识记录

    # SQLite 打开和元数据读取作为单一资源初始化步骤。
    connection_database: sqlite3.Connection | None = None  # 失败路径也可关闭的连接

    # 初始化只读连接并验证数据库元数据。
    try:

        # URI 模式从文件系统层拒绝任何写事务。
        str_database_uri = f"{path_database.resolve().as_uri()}?mode=ro"  # 禁止数据库写入的 SQLite URI

        # 连接在成功返回后由调用方关闭。
        connection_database = sqlite3.connect(str_database_uri, uri=True)  # 注册表数据库连接

        # query_only 为只读 URI 再增加 SQLite 运行时防线。
        connection_database.execute("PRAGMA query_only = ON")

        # 元数据读取同时验证 metadata 表存在。
        dict_metadata = fetch_metadata(connection_database)  # 当前数据库元数据

    # 无法打开数据库时转换为稳定错误状态。
    except (RegistryError, sqlite3.Error) as object_error:

        # 元数据读取失败时也必须释放已建立连接。
        if connection_database is not None:

            # 关闭失败连接，避免阻塞后续索引重建。
            connection_database.close()

        # 已归一化的领域异常保持原始含义。
        if isinstance(object_error, RegistryError):

            # 包装领域异常并保留统一的 Python 错误协议。
            raise RegistryError(
                f"> ERR: [Python] registry database validation failed: {object_error}"
            ) from object_error

        # 动态数据库错误仍保留可见 Python 前缀。
        raise RegistryError(f"> ERR: [Python] cannot open registry database: {object_error}") from object_error

    # 防御不可达的空连接状态，避免依赖裸断言。
    if connection_database is None:

        # 连接初始化未成功时拒绝继续读取索引。
        raise RegistryError("> ERR: [Python] registry database connection was not initialized")

    # 期望元数据完全由当前 JSON 源和代码常量推导。
    dict_expected = {  # 当前源对应的数据库元数据
        "schema_version": str(INT_SCHEMA_VERSION),  # 代码支持的结构版本
        "source_sha256": source_digest(path_skill_root, tuple_registry[0]),  # 当前 JSON 摘要
        "command_count": str(len(tuple_registry[1])),  # 当前命令记录数
        "workflow_count": str(len(tuple_registry[2])),  # 当前工作流记录数
        "document_count": str(len(tuple_document_records[0])),  # 当前文档职责记录数
        "knowledge_count": str(len(tuple_document_records[1])),  # 当前知识指针记录数
        "fts_tokenizer": STR_FTS_TOKENIZER,  # 当前 FTS 分词器
    }

    # 任一字段不一致都表示数据库不能可靠回答当前文档用法。
    if any(dict_metadata.get(str_key) != str_value for str_key, str_value in dict_expected.items()):

        # 过期连接先关闭，避免 Windows 上阻塞重建替换。
        connection_database.close()

        # 调用方应运行构建器而不是继续查询旧内容。
        raise RegistryError("> ERR: [Python] registry database is stale or schema-incompatible")

    # FTS 表查询验证运行时具备兼容的 FTS5 支持。
    try:

        # count 查询不会读取或输出完整注册载荷。
        connection_database.execute("SELECT count(*) FROM command_fts").fetchone()

        # schema v2 同时验证知识 FTS 表可用。
        connection_database.execute("SELECT count(*) FROM knowledge_fts").fetchone()

    # 表损坏或 FTS 扩展不可用时拒绝问询。
    except sqlite3.Error as object_error:

        # 失败连接在抛出前必须释放。
        connection_database.close()

        # 错误明确归类为 FTS5 索引不可用。
        raise RegistryError(f"> ERR: [Python] registry FTS5 index is unavailable: {object_error}") from object_error

    # 成功连接交给查询或状态读取方使用。
    return connection_database, dict_metadata
