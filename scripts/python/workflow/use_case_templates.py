"""管理本地板级用例模板的显式选择、校验与摘要。"""

# 延迟注解解析，避免导入期展开复杂 JSON 类型。
from __future__ import annotations

# 标准库依赖负责深拷贝、JSON 资产读取、ID 校验、缓存和路径处理。
import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# skill 根目录用于把资产路径转换成发布包内相对路径。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # skill 安装根目录

# 板级用例模板目录随 skill assets 一起发布。
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "use_case_templates"  # 用例模板资产目录

# catalog 记录可选模板 id、目录和参数化提示。
CATALOG_PATH = TEMPLATE_ROOT / "catalog.json"  # 用例模板 catalog 路径

# workflow 配置中模板 ID 只允许小写字母、数字和下划线。
ID_PATTERN = re.compile(r"^[a-z0-9_]+$")  # 模板 ID 格式正则

# 自定义异常让调用方能区分模板选择错误和普通 ValueError。
class UseCaseTemplateError(ValueError):
    """表示请求的板级用例模板无法解析或资源不完整。"""

# catalog 读取入口负责校验发布包内模板目录。
def load_use_case_template_catalog() -> dict[str, Any]:
    """读取并校验板级用例模板目录。

    参数:
        无。

    返回:
        catalog 字典，包含 version=1 和 templates 列表。

    异常:
        UseCaseTemplateError: catalog 缺失模板列表或模板条目结构不合法。
    """

    # catalog 是发布包资产，读取后先做最小 schema 校验。
    dict_template_catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))  # 用例模板 catalog 载荷

    # templates 是后续选择器遍历的主数据。
    list_template_entries = dict_template_catalog.get("templates")  # catalog 原始模板列表

    # catalog 版本和模板列表必须同时有效。
    if dict_template_catalog.get("version") != 1 or not isinstance(list_template_entries, list):

        # 保留旧版错误文本，方便外部测试继续匹配。
        raise UseCaseTemplateError(
            "> ERR: [Python] Use-case template catalog must use version=1 and include templates."
        )

    # 逐条校验，避免损坏条目延迟到 prompt 拼接阶段才暴露。
    for dict_template_entry in list_template_entries:

        # 单个 catalog 条目需要提供 manifest 定位和摘要字段。
        _validate_catalog_entry(dict_template_entry)

    # 返回缓存对象；调用方若要修改应自行深拷贝。
    return dict_template_catalog

# catalog 读取结果可缓存，因为 assets 在一次运行中视为只读。
load_use_case_template_catalog = lru_cache(maxsize=1)(load_use_case_template_catalog)  # 带缓存的 catalog 读取入口

# 公开枚举器返回 catalog 条目的深拷贝。
def list_use_case_templates() -> list[dict[str, Any]]:
    """返回可供 workflow 显式选择的用例模板清单副本。

    参数:
        无。

    返回:
        返回 catalog templates 的深拷贝列表，调用方可安全修改。
    """

    # 深拷贝保护缓存 catalog，避免调用方污染后续选择逻辑。
    return [copy.deepcopy(item) for item in load_use_case_template_catalog()["templates"]]

# workflow ID 校验器集中处理空值、格式和存在性检查。
def validate_use_case_template_id(template_id: str) -> str:
    """规范化并校验 workflow.use_case_template_id。

    参数:
        template_id: workflow 配置中声明的模板 ID。

    返回:
        返回去除空白且确认存在于 catalog 的模板 ID。

    异常:
        UseCaseTemplateError: 当 ID 为空、格式非法或不存在于 catalog 时抛出。
    """

    # workflow 字段可能来自 YAML/JSON，先转成去空白字符串。
    str_normalized_id = str(template_id or "").strip()  # 规范化后的模板 ID

    # 空字符串没有明确选择语义，应由调用方在进入本函数前过滤。
    if not str_normalized_id:

        # 复用旧版错误文本，保持用户配置报错稳定。
        raise UseCaseTemplateError(
            "> ERR: [Python] workflow.use_case_template_id must be a non-empty string when provided."
        )

    # 格式约束避免路径穿越或大小写别名进入资产解析。
    if not ID_PATTERN.fullmatch(str_normalized_id):

        # 复用旧版错误文本。
        raise UseCaseTemplateError(
            "> ERR: [Python] workflow.use_case_template_id must use lowercase letters, digits, and underscores only."
        )

    # catalog 模板列表用于检查 ID 是否真实存在。
    list_template_entries = load_use_case_template_catalog()["templates"]  # catalog 模板列表

    # 用生成器判断存在性，避免为了布尔结果复制模板条目。
    bool_has_match = any(  # catalog 是否包含请求的模板 ID
        item.get("template_id") == str_normalized_id  # 当前条目是否匹配请求 ID
        for item in list_template_entries  # catalog 内全部模板条目
    )

    # 不存在的 ID 应返回可选值清单，便于用户修正 workflow 配置。
    if not bool_has_match:

        # 保留旧版拼接格式，避免改变测试或日志匹配。
        raise UseCaseTemplateError(
            "> ERR: [Python] workflow.use_case_template_id="
            + repr(str_normalized_id)
            + " is not valid. Expected one of: "
            + ", ".join(item["template_id"] for item in list_template_entries)
            + "."
        )

    # 返回规范化 ID，后续 resolve 不再重复处理空白。
    return str_normalized_id

# 公开选择入口只响应 workflow.use_case_template_id 的显式配置。
def select_use_case_template(spec: dict[str, Any]) -> dict[str, Any] | None:
    """按 spec.workflow 中的显式 ID 选择板级用例模板。

    参数:
        spec: 已解析的规格字典，可能包含 workflow.use_case_template_id。

    返回:
        返回完整模板 payload；未显式选择模板时返回 None。
    """

    # workflow 缺失或不是对象时视为没有显式模板选择。
    dict_workflow_config = spec.get("workflow", {}) if isinstance(spec.get("workflow"), dict) else {}  # 规格中的 workflow 子配置

    # use_case_template_id 是唯一触发模板加载的字段。
    raw_requested_id = dict_workflow_config.get("use_case_template_id")  # 用户请求的模板 ID

    # None 和空字符串都表示不使用板级用例模板。
    if raw_requested_id in (None, ""):

        # 无显式选择时返回 None，保持旧版调用方分支语义。
        return None

    # 显式选择时加载完整模板 payload。
    return resolve_use_case_template(validate_use_case_template_id(str(raw_requested_id)))

# 公开解析器加载模板 manifest 与工件正文。
def resolve_use_case_template(template_id: str) -> dict[str, Any]:
    """加载模板 manifest 和工件内容，形成 prompt 可消费的 payload。

    参数:
        template_id: 已配置或调用方指定的模板 ID。

    返回:
        返回包含 catalog 元数据、manifest 元数据和工件正文的模板 payload。

    异常:
        UseCaseTemplateError: 当模板 ID、manifest 或工件资源不合法时抛出。
    """

    # 先规范化 ID，后续所有错误信息都使用同一个稳定值。
    str_normalized_id = validate_use_case_template_id(template_id)  # resolve 阶段使用的稳定模板 ID

    # 从 catalog 中定位模板条目；validate 已保证至少有一个匹配项。
    dict_template_entry = next(  # catalog 中匹配请求 ID 的模板条目
        item  # 当前 catalog 候选模板条目
        for item in load_use_case_template_catalog()["templates"]  # 按 catalog 顺序查找首个同名模板
        if item.get("template_id") == str_normalized_id  # 仅接受请求 ID 对应的条目
    )

    # directory 字段定位模板 manifest 和所有相对工件。
    path_template_dir = TEMPLATE_ROOT / str(dict_template_entry["directory"])  # 模板目录

    # manifest 是模板目录下的结构化入口文件。
    path_manifest = path_template_dir / "manifest.json"  # 模板 manifest 路径

    # manifest 缺失时说明 assets 打包不完整。
    if not path_manifest.exists():

        # 报告具体模板 ID，便于定位损坏资产。
        raise UseCaseTemplateError(f"> ERR: [Python] Use-case template {str_normalized_id!r} is missing manifest.json.")

    # manifest 载入后继续做版本和 ID 一致性校验。
    dict_manifest = json.loads(path_manifest.read_text(encoding="utf-8"))  # 模板 manifest 载荷

    # manifest 版本必须与当前 runtime 解析逻辑一致。
    if dict_manifest.get("version") != 1:

        # 旧版错误文本保留 manifest 版本要求。
        raise UseCaseTemplateError(
            f"> ERR: [Python] Use-case template {str_normalized_id!r} manifest must use version=1."
        )

    # manifest ID 必须回指 catalog 中的同一模板。
    if dict_manifest.get("template_id") != str_normalized_id:

        # 阻断 catalog 和 manifest 错配，避免加载错误模板正文。
        raise UseCaseTemplateError(
            f"> ERR: [Python] Use-case template {str_normalized_id!r} manifest template_id does not match catalog."
        )

    # artifacts 列表描述需要注入 prompt 或报告的模板工件。
    list_artifact_entries = dict_manifest.get("artifacts")  # manifest 工件条目列表

    # 至少需要一个工件，否则模板没有可消费内容。
    if not isinstance(list_artifact_entries, list) or not list_artifact_entries:

        # 保留旧版错误文本，提示 manifest 内容不完整。
        raise UseCaseTemplateError(
            f"> ERR: [Python] Use-case template {str_normalized_id!r} manifest must contain artifacts."
        )

    # payload 从 catalog 条目开始扩展，保留原有 catalog 元数据。
    dict_template_payload = copy.deepcopy(dict_template_entry)  # 模板选择返回载荷

    # selection_reason 说明该模板来自用户显式 workflow 配置。
    dict_template_payload["selection_reason"] = "selected by explicit workflow.use_case_template_id"  # 选择原因

    # template_root 以字符串形式暴露，便于 JSON artifact 序列化。
    dict_template_payload["template_root"] = str(path_template_dir)  # 模板目录文本

    # manifest_path 记录实际读取的 manifest 位置。
    dict_template_payload["manifest_path"] = str(path_manifest)  # manifest 路径文本

    # display_name 优先使用 manifest 中的展示名，兼容 catalog 兜底。
    dict_template_payload["display_name"] = dict_manifest.get("display_name", dict_template_entry.get("display_name"))  # 报告展示用模板名称

    # applicable_scenarios 是供 prompt/报告展示的适用场景。
    dict_template_payload["applicable_scenarios"] = copy.deepcopy(dict_manifest.get("applicable_scenarios", []))  # 适用场景

    # parameterization_points 允许 manifest 覆盖 catalog 的参数化提示。
    list_parameterization_points = dict_manifest.get(  # manifest 优先的参数化提示源
        "parameterization_points",  # manifest 参数化字段名
        dict_template_entry.get("parameterization_points", []),  # catalog 参数化提示兜底值
    )

    # 参数化提示需要深拷贝，避免调用方改写 manifest 派生数据。
    dict_template_payload["parameterization_points"] = copy.deepcopy(list_parameterization_points)  # prompt 参数化提示列表

    # source_projects 允许 manifest 提供更精确的来源项目说明。
    list_source_projects = dict_manifest.get(  # manifest 优先的模板来源清单
        "source_projects",  # manifest 来源项目字段名
        dict_template_entry.get("source_projects", []),  # catalog 来源项目兜底值
    )

    # 来源清单需要深拷贝，避免报告端修改回流到 payload 源数据。
    dict_template_payload["source_projects"] = copy.deepcopy(list_source_projects)  # 模板来源项目列表

    # summary 兼容 manifest 和 catalog 两处摘要。
    dict_template_payload["summary"] = str(dict_manifest.get("summary") or dict_template_entry.get("summary") or "")  # 模板摘要

    # artifacts 载入每个工件正文并保留相对路径信息。
    dict_template_payload["artifacts"] = [
        _load_artifact(path_template_dir, item)  # 载入后的模板工件
        for item in list_artifact_entries  # manifest 中声明的工件
    ]  # 模板工件载荷列表

    # 返回完整 payload，供 prompt 和报告模块消费。
    return dict_template_payload

# 摘要入口用于报告，不携带工件正文。
def summarize_use_case_template(payload: dict[str, Any] | None) -> dict[str, Any]:
    """压缩模板 payload，只保留报告和 artifact 元数据需要的字段。

    参数:
        payload: `resolve_use_case_template` 返回的模板 payload，允许为空。

    返回:
        返回报告层可直接序列化的轻量模板摘要字典。
    """

    # 未选择模板时返回固定空摘要，便于报告端无分支渲染。
    if not payload:

        # 保持旧版 no-template 摘要结构。
        return {
            "id": None,
            "display_name": None,
            "summary": "",
            "source_projects": [],
            "parameterization_points": [],
            "selection_reason": "no explicit use-case template selected",
            "artifacts": [],
        }

    # 已选择模板时只输出轻量字段和工件元数据。
    return {
        "id": payload.get("template_id"),
        "display_name": payload.get("display_name"),
        "summary": payload.get("summary", ""),
        "source_projects": copy.deepcopy(payload.get("source_projects", [])),
        "parameterization_points": copy.deepcopy(payload.get("parameterization_points", [])),
        "selection_reason": payload.get("selection_reason"),
        "artifacts": [
            {
                "kind": item.get("kind"),
                "path": item.get("relative_path"),
                "status": item.get("status"),
                "summary": item.get("summary"),
            }
            for item in payload.get("artifacts", [])
        ],
    }

# catalog 条目校验器保护模板目录和摘要字段完整性。
def _validate_catalog_entry(template_entry: Any) -> None:
    """校验单个 catalog 模板条目的基础结构。

    参数:
        template_entry: 从 catalog templates 列表读取的原始条目。

    返回:
        无返回值；校验通过即静默结束。

    异常:
        UseCaseTemplateError: 当条目类型、必填字段或追踪字段不合法时抛出。
    """

    # catalog 条目必须是对象，才能读取模板 ID 和目录字段。
    if not isinstance(template_entry, dict):

        # 资产结构错误直接阻断加载。
        raise UseCaseTemplateError("> ERR: [Python] Use-case template catalog entries must be JSON objects.")

    # 这些字段是选择、展示和参数化提示的最小集合。
    for required_key in ("template_id", "directory", "display_name", "source_projects", "parameterization_points"):

        # 缺任一字段都会导致后续 payload 不完整。
        if required_key not in template_entry:

            # 报告缺失字段名，便于修复 catalog。
            raise UseCaseTemplateError(f"> ERR: [Python] Use-case template catalog entry missing {required_key}.")

    # template_id 必须满足 workflow ID 规则。
    _validate_template_field(str(template_entry["template_id"]), "template_id")

    # directory 是模板 manifest 的相对目录名。
    if not isinstance(template_entry["directory"], str) or not template_entry["directory"]:

        # 缺失目录无法定位模板资产。
        raise UseCaseTemplateError(
            f"> ERR: [Python] Use-case template {template_entry['template_id']!r} directory must be a non-empty string."
        )

    # source_projects 用于说明模板来源，不能为空。
    if not isinstance(template_entry["source_projects"], list) or not template_entry["source_projects"]:

        # 缺少来源会降低板级模板的可追溯性。
        raise UseCaseTemplateError(
            f"> ERR: [Python] Use-case template {template_entry['template_id']!r} "
            "source_projects must be a non-empty list."
        )

    # parameterization_points 是使用模板时必须关注的可变点。
    if not isinstance(template_entry["parameterization_points"], list) or not template_entry["parameterization_points"]:

        # 缺少参数化点会让模板在 prompt 中难以安全复用。
        raise UseCaseTemplateError(
            f"> ERR: [Python] Use-case template {template_entry['template_id']!r} "
            "parameterization_points must be a non-empty list."
        )

# 共享字段校验器同时服务 catalog 和 manifest。
def _validate_template_field(value: str, field_name: str) -> None:
    """校验模板标识符字段符合 workflow ID 规则。

    参数:
        value: 待校验的字段值。
        field_name: 用于错误诊断的字段名。

    返回:
        无返回值；校验通过即静默结束。

    异常:
        UseCaseTemplateError: 当字段值不符合模板 ID 格式时抛出。
    """

    # 标识符格式不合规时拒绝进入路径解析。
    if not ID_PATTERN.fullmatch(value):

        # 报告具体字段和值，便于定位资产问题。
        raise UseCaseTemplateError(
            f"> ERR: [Python] Use-case template {field_name} must use lowercase letters, "
            f"digits, and underscores only: {value!r}."
        )

# 工件加载器读取模板正文并补充路径元数据。
def _load_artifact(template_dir: Path, artifact_entry: dict[str, Any]) -> dict[str, Any]:
    """读取模板工件正文并补充路径元数据。

    参数:
        template_dir: 当前模板 manifest 所在的目录。
        artifact_entry: manifest 中声明的单个 artifact 条目。

    返回:
        返回包含 absolute_path、relative_path 和 content 的工件 payload。

    异常:
        UseCaseTemplateError: 当 artifact 结构不合法或正文文件缺失时抛出。
    """

    # manifest 中每个 artifact 必须是对象。
    if not isinstance(artifact_entry, dict):

        # 非对象条目无法读取 kind/path/status/summary。
        raise UseCaseTemplateError("> ERR: [Python] Use-case template artifacts must be JSON objects.")

    # artifact 最小字段覆盖正文定位、状态和摘要展示。
    for required_key in ("kind", "path", "status", "summary"):

        # 缺字段时阻断加载，避免返回半结构化 payload。
        if required_key not in artifact_entry:

            # 指出缺失字段，便于修复 manifest。
            raise UseCaseTemplateError(f"> ERR: [Python] Use-case template artifact missing {required_key}.")

    # path 字段相对于模板目录解析。
    path_artifact = template_dir / str(artifact_entry["path"])  # 模板工件路径

    # 工件正文必须真实存在。
    if not path_artifact.exists():

        # 报告完整路径，便于定位缺失文件。
        raise UseCaseTemplateError(f"> ERR: [Python] Use-case template artifact is missing: {path_artifact}")

    # payload 从 manifest 条目复制，后续补充路径和正文。
    dict_artifact_payload = copy.deepcopy(artifact_entry)  # 工件返回载荷

    # absolute_path 供调试和追踪使用。
    dict_artifact_payload["absolute_path"] = str(path_artifact)  # 工件绝对路径文本

    # relative_path 保持在 skill 包内可读，适合报告输出。
    dict_artifact_payload["relative_path"] = path_artifact.relative_to(SKILL_ROOT).as_posix()  # 包内相对路径

    # content 是 prompt 注入所需的工件正文。
    dict_artifact_payload["content"] = path_artifact.read_text(encoding="utf-8")  # 工件正文文本

    # 返回完整工件 payload。
    return dict_artifact_payload
