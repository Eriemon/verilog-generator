"""选择本地总线接口模板并加载模板正文。"""

# 延迟注解解析，避免运行期解析联合类型。
from __future__ import annotations

# 标准库依赖负责深拷贝模板元数据、读取 catalog 和缓存静态配置。
import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# workflow 文件位于 skill/scripts/python/workflow，向上三级到 skill 根。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # 当前 workflow 模块所属的 skill 根目录

# 接口模板资产目录固定在 skill assets 下。
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "interface_templates"  # 本地接口模板资产目录

# catalog 描述可选择模板及其约束字段。
CATALOG_PATH = TEMPLATE_ROOT / "catalog.json"  # 接口模板 catalog 文件路径

# 受支持的接口族集合，保持 select_interface_template 的过滤边界明确。
SUPPORTED_INTERFACE_FAMILIES = {"axi_stream", "axi4", "axi4_lite", "ahb", "apb"}  # 可走本地模板选择的接口族

# 模板错误使用专门异常，便于上游区分规格问题和资产问题。
class InterfaceTemplateError(ValueError):
    """表示本地接口模板无法解析或 catalog 格式不合法。"""

# catalog 读取带缓存，避免每次生成 prompt 都重复读取 JSON。
@lru_cache(maxsize=1)

# 缓存后的 catalog 读取函数负责保护本地模板选择的最小 schema。
def load_interface_template_catalog() -> dict[str, Any]:
    """读取并校验接口模板 catalog。

    参数:
        无外部业务参数；函数固定读取随 skill 发布的接口模板 catalog。

    返回:
        catalog JSON 字典，要求 version=1 且 templates 为列表。

    异常:
        InterfaceTemplateError: catalog 版本或 templates 字段不符合本地模板合同。
    """

    # catalog 是本地可信资产，但仍检查最小 schema 以便开发期快速发现损坏。
    dict_catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))  # 接口模板目录

    # version 和 templates 是选择逻辑依赖的最小字段。
    if dict_catalog.get("version") != 1 or not isinstance(dict_catalog.get("templates"), list):

        # 自定义异常让上游区分模板配置问题和普通规格问题。
        raise InterfaceTemplateError(
            "> ERR: [Python] Interface template catalog must use version=1 and include templates."
        )

    # 返回缓存的 catalog；调用方如需修改必须自行复制。
    return dict_catalog

# 公开枚举器返回所有模板元数据的深拷贝。
def list_interface_templates() -> list[dict[str, Any]]:
    """列出本地可用接口模板。

    参数:
        无外部业务参数；函数读取缓存后的接口模板 catalog。

    返回:
        catalog templates 的深拷贝，调用方修改不会污染缓存。
    """

    # 深拷贝保护 lru_cache 中的 catalog 免受调用方修改。
    return [copy.deepcopy(item) for item in load_interface_template_catalog()["templates"]]

# 公开选择器从规格中的 interface_family/profile 选择模板。
def select_interface_template(spec: dict[str, Any]) -> dict[str, Any] | None:
    """根据规范化规格选择接口模板。

    参数:
        spec: Verilog 生成规格，可能包含 interface_family 和 interface_profile。

    返回:
        模板 payload；不支持的 interface_family 返回 None。
    """

    # 顶层接口族决定是否需要本地模板辅助。
    str_interface_family = str(spec.get("interface_family") or "")  # 规格接口族

    # profile 只接受 dict，避免字符串等异常输入影响选择逻辑。
    profile = spec.get("interface_profile", {}) if isinstance(spec.get("interface_profile"), dict) else {}  # 接口画像

    # 非受支持接口族不强行套模板，交给普通 RTL 生成路径。
    if str_interface_family not in SUPPORTED_INTERFACE_FAMILIES:

        # None 表示没有可选模板，不是错误。
        return None

    # 受支持接口族进入完整模板解析流程。
    return resolve_interface_template(str_interface_family, profile)

# 公开解析器支持显式 template_id 或按 role/read_write_mode 默认匹配。
def resolve_interface_template(interface_family: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """解析单个接口模板并加载模板正文。

    参数:
        interface_family: 接口族名称，例如 axi_stream 或 axi4_lite。
        profile: 可选接口画像，支持 template_id、role 和 read_write_mode。

    返回:
        深拷贝后的模板元数据，并附加 content、selection_reason 和 strict_naming_policy。

    异常:
        InterfaceTemplateError: 显式模板 id 无效或 catalog 中没有匹配画像的模板。
    """

    # profile 缺省按空对象处理，兼容旧调用方。
    dict_profile = profile or {}  # 本次模板解析使用的接口画像

    # 显式 template_id 优先于自动匹配。
    str_requested_id = str(dict_profile.get("template_id") or "").strip()  # 请求模板 id

    # role/read_write_mode 统一小写，便于和 catalog 匹配。
    str_role = str(dict_profile.get("role") or "").strip().lower()  # 接口角色

    # read_write_mode 用于区分只读、只写和读写模板。
    str_read_write_mode = str(dict_profile.get("read_write_mode") or "").strip().lower()  # 读写模式

    # 候选模板先按 interface_family 过滤。
    list_candidates = [
        item  # 当前接口族候选模板
        for item in load_interface_template_catalog()["templates"]  # catalog 模板元数据
        if item.get("interface_family") == interface_family  # 接口族匹配
    ]  # 同接口族模板候选

    # 显式 template_id 分支提供最确定的模板选择。
    if str_requested_id:

        # 显式 id 只在当前接口族候选内查找。
        list_matches = [
            item  # 显式 id 匹配的模板
            for item in list_candidates  # 待按画像筛选的接口族模板
            if item.get("template_id") == str_requested_id  # template_id 完全一致
        ]  # 显式模板匹配结果

        # 显式 id 不存在时报告配置错误。
        if not list_matches:

            # 错误文本保留 profile 字段名，方便用户定位规格。
            raise InterfaceTemplateError(
                f"> ERR: [Python] interface_profile.template_id={str_requested_id!r} "
                f"is not valid for interface_family={interface_family!r}."
            )

        # 第一个匹配项即为最终模板。
        dict_selected = list_matches[0]  # 显式 id 命中的模板元数据

        # selection_reason 会进入返回 payload 供诊断展示。
        str_reason = "selected by explicit interface_profile.template_id"  # 显式模板选择原因

    # 没有显式 id 时退回到接口族、角色和读写模式的默认画像匹配。
    else:

        # 默认匹配同时检查角色和读写模式。
        list_matches = [
            item  # 当前默认匹配模板
            for item in list_candidates  # 进入默认画像筛选的模板
            if _role_matches(item, str_role) and _mode_matches(item, str_read_write_mode)  # 画像匹配
        ]  # 默认匹配结果

        # 没有默认匹配说明 catalog 或规格画像不兼容。
        if not list_matches:

            # 错误文本包含三个筛选条件，方便用户判断缺哪个模板。
            raise InterfaceTemplateError(
                f"> ERR: [Python] No local interface template matches interface_family={interface_family!r}, "
                f"role={str_role!r}, read_write_mode={str_read_write_mode!r}."
            )

        # catalog 顺序决定默认选择优先级。
        dict_selected = list_matches[0]  # 默认规则命中的模板元数据

        # 记录默认选择依据。
        str_reason = "selected by interface_family, role, and read_write_mode defaults"  # 默认模板选择原因

    # 返回 payload 必须深拷贝，避免 content 等运行期字段污染 catalog 缓存。
    dict_payload = copy.deepcopy(dict_selected)  # 返回给调用方的模板 payload

    # 模板正文路径相对于 TEMPLATE_ROOT。
    path_template = TEMPLATE_ROOT / str(dict_payload["path"])  # 本地模板正文文件路径

    # content 是调用方真正插入 prompt/约束的模板正文。
    dict_payload["content"] = path_template.read_text(encoding="utf-8")  # 已加载的模板正文

    # selection_reason 解释模板为何被选中。
    dict_payload["selection_reason"] = str_reason  # 模板选择诊断说明

    # strict_naming_policy 是下游生成约束字段，保持旧版固定值。
    dict_payload["strict_naming_policy"] = "strict_preferred"  # 下游使用的命名约束策略

    # 返回完整模板 payload。
    return dict_payload

# 角色匹配器实现 duplex 模板对 master/slave 的兼容。
def _role_matches(item: dict[str, Any], role: str) -> bool:
    """判断模板角色是否兼容规格画像角色。

    参数:
        item: catalog 中的单个模板元数据。
        role: 规格画像中的接口角色，已经由调用方统一为小写。

    返回:
        True 表示模板角色可用于当前规格画像。
    """

    # catalog 中缺失 role 时按空字符串处理。
    str_template_role = str(item.get("role") or "").lower()  # 模板角色

    # duplex 模板可用于空角色、duplex、master 和 slave。
    if str_template_role == "duplex":

        # 兼容全双工模板覆盖常见单向角色画像。
        return role in {"", "duplex", "master", "slave"}

    # 普通模板只接受空角色或完全匹配的角色。
    return role in {"", str_template_role}

# 读写模式匹配器实现 read_write 模板对 read/write 的兼容。
def _mode_matches(item: dict[str, Any], read_write_mode: str) -> bool:
    """判断模板读写模式是否兼容规格画像模式。

    参数:
        item: catalog 中的单个模板元数据。
        read_write_mode: 规格画像中的读写模式，已经由调用方统一为小写。

    返回:
        True 表示模板读写模式可用于当前规格画像。
    """

    # 模板的读写模式缺失时按空模式处理，用于兼容旧 catalog 条目。
    str_template_mode = str(item.get("read_write_mode") or "").lower()  # 模板读写模式

    # read_write 模板可覆盖只读、只写和读写画像。
    if str_template_mode == "read_write":

        # 空画像也允许匹配通用 read_write 模板。
        return read_write_mode in {"", "read", "write", "read_write"}

    # 普通模板只接受空模式或完全匹配模式。
    return read_write_mode in {"", str_template_mode}
