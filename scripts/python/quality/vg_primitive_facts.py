"""提供 AMD-Xilinx 原语 profile 的查询、冲突解析和接口投影。"""

# future annotations 延后解析动态 JSON profile 的类型。
from __future__ import annotations

# deepcopy 隔离调用方对已验证目录的修改。
from copy import deepcopy

# Any 与 Mapping 描述 JSON profile 的受控动态字段。
from typing import Any, Mapping

# 原语目录 loader 是默认资产的唯一读取入口。
from ..workflow.verilog_gate_catalog import load_primitive_semantic_catalog

# 允许的 hierarchy output boundary 名称。
set_output_boundaries = frozenset(  # 原语输出边界集合
    {"transparent", "state_cut", "clock_source", "multi_driver", "opaque", "inout"}  # 允许的输出边界枚举
)

# VG132 只接受显式时钟角色，CE/reset/data 不会被名称猜测放行。
set_clock_port_roles = frozenset(  # 原语时钟角色集合
    {"clock_input", "clock_output", "clock_feedback", "clock_capable"}  # 允许的时钟角色枚举
)

# load_primitive_facts 是名称到 profile 的公开查询入口。
def load_primitive_facts(
    module_name: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """读取一个原语 profile，未知名称返回 ``None``。

    参数:
        module_name: Verilog 实例引用的精确原语名称。
        catalog: 可选的完整目录或最小 profile mapping。
    返回:
        独立 profile 副本；未列入固定库存时返回 ``None``。
    """

    # 名称只允许精确匹配，禁止隐式扩张白名单。
    str_name = str(module_name or "").strip()  # 原语查询名称

    # 空名称不具备可审计的 profile 身份。
    if not str_name:

        # 调用方沿用静态事实不足状态。
        return None

    # 缺省目录自动读取固定资产，显式目录复用已验证快照。
    dict_catalog = _catalog_copy(catalog)  # 本次查询的目录副本

    # profiles 索引保留命名空间的统一查找语义。
    dict_profile = _profile_mapping(dict_catalog).get(str_name)  # 查询到的原语 profile

    # 未列名模块不获得通用 primitive 特权。
    if not isinstance(dict_profile, Mapping):

        # 未知名称必须由上层报告为 inconclusive。
        return None

    # 公开查询结果与缓存目录断开引用。
    dict_result = deepcopy(dict(dict_profile))  # profile 独立副本

    # 兼容最小化 profile 夹具时补齐稳定名称字段。
    # 为查询结果补上稳定的公开名称字段。
    dict_result.setdefault("name", str_name)  # profile 对外显示名称

    # 为下游实例绑定补上模块身份字段。
    dict_result.setdefault("module_name", str_name)  # profile 实例绑定名称

    # 为缺少标识的夹具建立稳定 profile_id。
    dict_result.setdefault(  # 按库名与模块名生成可追踪 profile_id
        "profile_id",
        f"{dict_result.get('library', 'UNKNOWN')}::{str_name}",
    )  # profile 稳定身份

    # 返回不暴露内部可变目录。
    return dict_result

# resolve_primitive_profile 解析显式 profile、内置 profile 和冲突状态。
def resolve_primitive_profile(
    module_name: str,
    *,
    explicit_profile: Mapping[str, Any] | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """解析原语 profile，并在事实冲突时 fail-closed。

    参数:
        module_name: 需要解析的原语名称。
        explicit_profile: RTL 或接口来源提供的显式 profile。
        catalog: 可选的完整内置目录。
    返回:
        包含 ``status`` 和 ``profile`` 的解析结果字典。
    """

    # wrapper 和裸 profile 使用同一显式事实入口。
    dict_explicit = _unwrap_profile(explicit_profile)  # 显式 profile 副本

    # module_name 规范化后作为所有结果的稳定键。
    str_name = str(module_name or "").strip()  # 原语名称

    # 内置 profile 由固定 loader 负责清单和字段校验。
    dict_builtin = load_primitive_facts(str_name, catalog=catalog)  # 内置 profile 副本

    # 显式 profile 先完成最小字段归一化。
    if dict_explicit is not None:

        # 归一化过程同时检查 profile 名称一致性。
        dict_explicit = _normalize_explicit_profile(str_name, dict_explicit)  # 显式 profile 事实

    # 两种来源都缺失时不猜测原语语义。
    if dict_builtin is None and dict_explicit is None:

        # 未列入固定库存的名称只能返回 inconclusive。
        return {
            "status": "inconclusive",
            "module_name": str_name,
            "profile": None,
            "reason": "primitive is not present in the governed AMD-Xilinx inventory",
        }

    # 两种来源同时存在时执行语义冲突比较。
    if dict_explicit is not None and dict_builtin is not None:

        # 显式 profile 只比较它实际声明的语义字段。
        list_fields = dict_explicit.get("_provided_semantic_fields")  # 显式语义字段

        # 语义完全一致时显式来源可以覆盖内置来源标记。
        if _semantic_projection(dict_explicit, fields=list_fields) == _semantic_projection(
            dict_builtin,
            fields=list_fields,
        ):

            # source 字段保留来源优先级证据。
            dict_explicit["source"] = "explicit"  # 保留显式来源优先级证据

            # 兼容 resolved profile 的公开结果形状。
            return {
                "status": "passed",
                "module_name": str_name,
                "profile": dict_explicit,
            }

        # 语义冲突不得被任一来源静默覆盖。
        return {
            "status": "inconclusive",
            "module_name": str_name,
            "profile": None,
            "builtin_profile": dict_builtin,
            "reason": "explicit primitive profile conflicts with the built-in catalog",
        }

    # 只有显式来源时仍需保留其受治理事实身份。
    if dict_explicit is not None:

        # 显式 profile 不会扩张默认白名单，只覆盖当前调用。
        dict_explicit["source"] = "explicit"  # 标记仅有显式接口事实的来源

        # 返回通过状态供调用方绑定当前实例。
        return {"status": "passed", "module_name": str_name, "profile": dict_explicit}

    # 只有内置来源时使用固定资产的已验证语义。
    dict_builtin["source"] = "built_in"  # 标记采用固定资产的内置来源

    # 内置 profile 已完成固定资产校验并可直接绑定实例。
    return {"status": "passed", "module_name": str_name, "profile": dict_builtin}

# coerce_primitive_catalog 将 gate 参数统一为完整目录快照。
def coerce_primitive_catalog(
    primitive_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把完整 catalog、resolved profile 或单 profile 统一成完整目录。

    参数:
        primitive_profile: 可选完整目录、resolved wrapper 或裸 profile。
    返回:
        可供一次 VG 扫描共享的目录副本。
    """

    # 默认路径总是从固定资产建立独立目录。
    dict_catalog = _catalog_copy(None)  # 默认原语目录

    # 无显式覆盖时返回默认目录副本。
    if primitive_profile is None:

        # 不共享可变容器，保证 facts 快照隔离。
        return dict_catalog

    # 完整 catalog 直接保留其固定 namespace。
    if _looks_like_catalog(primitive_profile):

        # 重新建立 profiles 兼容索引。
        return _catalog_copy(primitive_profile)

    # resolved wrapper 只抽取当前调用的显式 profile。
    dict_profile = _unwrap_profile(primitive_profile)  # 覆盖 profile 事实

    # 非 profile mapping 不改变默认目录。
    if dict_profile is None:

        # 调用方仍可从默认目录得到其他原语事实。
        return dict_catalog

    # profile 名称是覆盖和冲突记录的唯一键。
    str_name = str(dict_profile.get("module_name") or dict_profile.get("name") or "").strip()  # 覆盖名称

    # 缺少名称时保持默认目录并 fail-closed 由上层处理。
    if not str_name:

        # 不能把无名 profile 绑定到任意实例。
        return dict_catalog

    # 使用公开 resolver 保持显式/内置冲突语义一致。
    dict_resolution = resolve_primitive_profile(  # 解析当前覆盖 profile 的冲突状态
        str_name,  # 待绑定的原语名称
        explicit_profile=dict_profile,  # 外部声明的接口事实
        catalog=dict_catalog,  # 当前扫描共享的固定目录
    )  # 当前覆盖解析结果

    # 读取 resolver 选定的可绑定语义事实。
    dict_profile_resolved = dict_resolution.get("profile")  # 解析后可绑定的 profile

    # 通过状态时把当前 profile 写入统一查询索引。
    if isinstance(dict_profile_resolved, Mapping):

        # 同名覆盖不改变其他固定库存条目。
        _insert_profile(dict_catalog, str_name, dict(dict_profile_resolved))

    # 冲突状态写入目录 metadata，规则层可局部保持未知。
    if dict_resolution.get("status") != "passed":

        # conflicts 显式保留 fail-closed 原因。
        dict_catalog.setdefault("conflicts", {})[str_name] = str(  # 登记显式 profile 的冲突原因
            dict_resolution.get("reason") or "profile conflict"  # 保留可审计的冲突文本
        )  # 完成冲突原因转换

    # 返回本轮原语目录快照。
    return dict_catalog

# primitive_profiles 返回供 hierarchy builder 使用的独立 profile mapping。
def primitive_profiles(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """返回目录中的原语 profile mapping。

    参数:
        catalog: 已加载或已归一化的原语目录。
    返回:
        名称到独立 profile 副本的 mapping。
    """

    # 只返回可被规则消费的 mapping profile。
    return {
        str_name: deepcopy(dict_profile)
        for str_name, dict_profile in _profile_mapping(catalog).items()
        if isinstance(dict_profile, Mapping)
    }

# primitive_module_interface 投影为现有 VG097 module 接口事实。
def primitive_module_interface(profile: Mapping[str, Any]) -> dict[str, Any]:
    """把 profile 转为 ``module_widths`` 可消费的接口字典。

    参数:
        profile: 已归一化的原语 profile。
    返回:
        包含 module 名和端口列表的接口事实。
    """

    # profile name 是 synthetic module 的稳定身份。
    str_name = str(profile.get("module_name") or profile.get("name") or "")  # 合成模块的原语身份

    # 端口列表保留 profile 声明顺序，支持 positional association。
    list_ports: list[dict[str, Any]] = []  # 原语端口接口列表

    # 逐端口转换宽度文本和角色事实。
    for str_port, dict_port in dict(profile.get("ports", {})).items():

        # JSON loader 已保证 width 是正整数。
        int_width = int(dict_port.get("width", 1))  # 原语端口整数宽度

        # 标量沿用空 width，总线转换为 Verilog 闭区间文本。
        str_width = "" if int_width == 1 else f"[{int_width - 1}:0]"  # VG097 端口宽度文本

        # 当前 profile port 转成 formatter 兼容字典。
        list_ports.append(
            {
                "name": str_port,
                "direction": str(dict_port.get("direction") or ""),
                "width": str_width,
                "roles": list(dict_port.get("roles", [])),
            }
        )

    # 接口只描述边界，不伪造原语内部 implementation。
    return {
        "name": str_name,
        "module_name": str_name,
        "ports": list_ports,
        "primitive_profile": deepcopy(dict(profile)),
    }

# primitive_port_is_clock_role 是 VG132 role-aware 判断入口。
def primitive_port_is_clock_role(
    catalog: Mapping[str, Any],
    module_name: str,
    port_name: str,
) -> bool:
    """判断原语端口是否明确声明为时钟角色。

    参数:
        catalog: 当前 VG 扫描使用的原语目录。
        module_name: 实例引用的原语名称。
        port_name: 实例连接的 formal 端口名。
    返回:
        profile 明确声明时钟角色时返回 ``True``。
    """

    # profile 查询失败时回到通用命名规则，不获得原语特权。
    dict_profile = load_primitive_facts(module_name, catalog=catalog)  # 当前原语 profile

    # 未列名模块不能自动放行普通 data port。
    if dict_profile is None:

        # VG132 保持原有命名规则的 fail-closed 边界。
        return False

    # 端口缺失同样不能被猜测为时钟。
    dict_port = dict_profile.get("ports", {}).get(port_name, {})  # 连接 formal 的已登记端口事实

    # 只接受显式 role 集合中的时钟类别。
    return bool(set(dict_port.get("roles", [])) & set_clock_port_roles)

# primitive_output_boundary 返回端口级跨层传播策略。
def primitive_output_boundary(
    profile: Mapping[str, Any],
    port_name: str,
) -> str:
    """返回 transparent、state_cut、clock_source 或 opaque。

    参数:
        profile: 已归一化的原语 profile。
        port_name: 当前 output formal 名称。
    返回:
        受控 output boundary 字符串，未知时为 ``opaque``。
    """

    # 缺失 output 策略必须默认局部 opaque。
    str_boundary = str(profile.get("outputs", {}).get(port_name) or "opaque")  # 当前输出的跨层传播边界

    # 未知 boundary 不得获得透明传播权限。
    if str_boundary not in set_output_boundaries:

        # 不认识的工具版本语义保持 opaque。
        return "opaque"

    # 返回已验证 boundary。
    return str_boundary

# _catalog_copy 统一默认目录、完整目录和最小测试夹具。
def _catalog_copy(catalog: Mapping[str, Any] | None) -> dict[str, Any]:
    """返回可修改的完整目录副本。

    参数:
        catalog: 可选完整目录或最小 namespace mapping。
    返回:
        包含 profiles 索引的目录副本。
    """

    # 默认 loader 已完成资产 schema 和清单校验。
    if catalog is None:

        # 默认返回值再次复制以隔离调用方。
        return deepcopy(load_primitive_semantic_catalog())

    # 显式 catalog 先断开所有嵌套引用。
    dict_copy = deepcopy(dict(catalog))  # 显式目录副本

    # 完整命名空间只需恢复 profiles 索引。
    if _looks_like_catalog(dict_copy):

        # 已有 profiles 是 loader 归一化后的权威快照，不因 namespace 旁路修改而漂移。
        if not isinstance(dict_copy.get("profiles"), Mapping):

            # 缺少索引时才合并三类公开来源，保证 resolver 不会漏查固定库存。
            dict_copy["profiles"] = {
                **dict(dict_copy.get("unisim", {})),  # 将已登记的 UNISIM 原语加入名称查找表
                **dict(dict_copy.get("xpm", {})),  # 纳入可查询的 XPM profile
                **dict(dict_copy.get("project_ip", {})),  # 纳入仅 manifest 的项目 IP 分类
            }  # 三个来源合并后的统一查询索引

        # 完整目录索引可直接供调用方查询。
        return dict_copy

    # 最小夹具也规范化为完整目录形状。
    dict_profiles = dict(_profile_mapping(dict_copy))  # 最小夹具 profile 索引

    # 兼容目录的固定字段保持与默认资产相同。
    return {
        "schema_version": 1,
        "kind": "amd-xilinx-primitive-semantics",
        "unisim": dict(dict_copy.get("unisim", {})),
        "xpm": dict(dict_copy.get("xpm", {})),
        "project_ip": dict(dict_copy.get("project_ip", {})),
        "profiles": dict_profiles,
    }

# _profile_mapping 支持完整目录和最小 mapping 两种输入。
def _profile_mapping(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    """返回统一名称到 profile 的 mapping。

    参数:
        catalog: 原语目录或最小 profile mapping。
    返回:
        可供名称查询的 mapping。
    """

    # profiles 是归一化目录的首选查询索引。
    dict_profiles = catalog.get("profiles")  # 统一 profile 索引

    # 已有索引直接复用不可变调用期视图。
    if isinstance(dict_profiles, Mapping):

        # profile 值仍由上层 deepcopy。
        return dict_profiles

    # 缺少索引时按公开 namespace 合并。
    dict_merged: dict[str, Any] = {}  # 统一名称索引承接各公开命名空间

    # 三个 namespace 按固定顺序建立名称索引。
    for str_namespace in ("unisim", "xpm", "project_ip"):

        # 缺失 namespace 不影响其他 profile 查询。
        dict_namespace = catalog.get(str_namespace, {})  # 当前命名空间的 profile 集合

        # 仅 mapping namespace 可以参与名称查找。
        if isinstance(dict_namespace, Mapping):

            # 后续 namespace 只覆盖同名兼容夹具条目。
            dict_merged.update(dict_namespace)

    # 返回合并索引。
    return dict_merged

# _looks_like_catalog 区分完整目录和裸 profile mapping。
def _looks_like_catalog(value: Mapping[str, Any]) -> bool:
    """判断 mapping 是否含原语目录命名空间。

    参数:
        value: 待判断的 mapping。
    返回:
        含有目录 namespace 时返回 ``True``。
    """

    # 命中任一固定 namespace 即视作目录。
    return any(
        str_name in value
        for str_name in ("unisim", "xpm", "project_ip", "profiles")
    )

# _unwrap_profile 兼容 resolved wrapper 与裸 profile。
def _unwrap_profile(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """提取 profile mapping，其他对象返回 ``None``。

    参数:
        value: resolved wrapper、裸 profile 或空值。
    返回:
        profile 独立副本；非 profile 输入返回 ``None``。
    """

    # 非 mapping 不能成为显式语义来源。
    if not isinstance(value, Mapping):

        # 字符串白名单不会被接受。
        return None

    # resolved wrapper 的 profile 字段优先。
    dict_profile = value.get("profile")  # wrapper 中携带的显式 profile

    # wrapper 中的 profile 是解析结果的唯一有效语义载荷。
    if isinstance(dict_profile, Mapping):

        # 返回与 wrapper 断开引用的 profile。
        return deepcopy(dict(dict_profile))

    # 裸 profile 至少需要一个语义字段。
    if any(
        str_field in value
        for str_field in ("ports", "outputs", "clock_capable", "support_level")
    ):

        # 显式 profile 直接复制。
        return deepcopy(dict(value))

    # 其他 mapping 不是 profile。
    return None

# _normalize_explicit_profile 补齐外部 profile 的最小安全字段。
def _normalize_explicit_profile(str_name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """校验显式 profile 并补齐默认边界。

    参数:
        str_name: 调用方要求绑定的模块名。
        profile: 显式 profile 副本。
    返回:
        归一化 profile。
    异常:
        ValueError: profile 名称与调用方不一致。
    """

    # 保留调用方实际声明的语义字段，默认值不制造冲突。
    list_provided_fields = [  # 记录显式来源真正声明过的语义字段
        str_field  # 当前字段名进入冲突比较白名单
        for str_field in ("ports", "outputs", "clock_capable", "support_level")  # 固定语义字段范围
        if str_field in profile  # 仅保留调用方实际提供的字段
    ]  # 实际提供字段列表结束

    # profile 复制后再补充兼容字段。
    dict_result = deepcopy(profile)  # 拷贝显式事实以便补齐安全边界

    # 显式名称缺省时沿用函数参数。
    str_profile_name = str(  # 解析显式 profile 声明的模块身份
        dict_result.get("module_name") or dict_result.get("name") or str_name  # 兼容两种名称字段
    ).strip()  # 显式 profile 名

    # 一个 profile 不能绑定到另一个 module。
    if str_profile_name != str_name:

        # 名称冲突必须明确报错而不是猜测。
        raise ValueError("> ERR: [Python] explicit primitive profile name does not match module_name.")

    # 绑定调用方名称，防止显式 profile 借用其他模块身份。
    dict_result["name"] = str_name  # 显式 profile 对外名称

    # 绑定合成实例查找所需的模块名称。
    dict_result["module_name"] = str_name  # 显式 profile 模块名称

    # 缺省库名把外部事实隔离在 UNISIM/XPM 固定库存之外。
    dict_result.setdefault("library", "EXPLICIT")  # 外部 profile 的库来源

    # 缺省 family 说明该事实来自外部接口边界。
    dict_result.setdefault("family", "external_interface")  # 外部 profile 的族类别

    # 缺省安全级别只允许边界级语义，不伪造内部实现。
    dict_result.setdefault("support_level", "P2_BOUNDARY")  # 外部 profile 的支持级别

    # 缺省时钟能力为否，避免名称猜测放行 VG132。
    dict_result.setdefault("clock_capable", False)  # 外部 profile 的时钟能力

    # 缺省空端口表使未声明接口保持不可判定。
    dict_result.setdefault("ports", {})  # 外部 profile 的端口事实

    # 缺省空输出表使未知输出保持 opaque 边界。
    dict_result.setdefault("outputs", {})  # 外部 profile 的输出边界

    # 保存冲突判定需要比较的语义字段集合。
    dict_result["_provided_semantic_fields"] = list_provided_fields or [  # 记录冲突比较所需的语义字段
        "ports",  # 端口事实参与 VG097/VG132
        "outputs",  # 输出边界参与 VG146/VG147
        "clock_capable",  # 时钟能力参与原语身份判断
        "support_level",  # 支持级别参与可信范围判断
    ]  # 显式 profile 的冲突字段记录

    # 返回显式 profile。
    return dict_result

# _semantic_projection 只比较四个目标门禁使用的语义字段。
def _semantic_projection(
    profile: Mapping[str, Any],
    *,
    fields: Any = None,
) -> dict[str, Any]:
    """返回 profile 冲突比较用的规范字段。

    参数:
        profile: 待比较的 profile。
        fields: 显式来源声明的字段列表。
    返回:
        只含指定语义字段的字典。
    """

    # 没有字段列表时比较完整的四类 profile 语义。
    set_fields = set(fields or ("ports", "outputs", "clock_capable", "support_level"))  # 本次冲突比较字段

    # 建立只含语义字段的规范投影。
    dict_projection: dict[str, Any] = {}  # 冲突比较的规范投影

    # ports 影响 VG097 接口宽度和 VG132 formal 角色。
    if "ports" in set_fields:

        # 深拷贝避免比较过程持有调用方容器。
        # 端口事实决定位宽匹配和 formal 角色。
        dict_projection["ports"] = deepcopy(dict(profile.get("ports", {})))  # 保留端口语义

    # outputs 影响 VG146/VG147 跨层边界。
    if "outputs" in set_fields:

        # 保留端口级 boundary 选择。
        # 输出边界决定跨层传播是否透明。
        dict_projection["outputs"] = deepcopy(dict(profile.get("outputs", {})))  # 保留输出边界

    # clock_capable 影响 profile 的时钟能力元数据。
    if "clock_capable" in set_fields:

        # 布尔值统一后进入冲突比较。
        # 时钟能力决定原语端口是否能承载时钟角色。
        dict_projection["clock_capable"] = bool(profile.get("clock_capable", False))  # 保留时钟能力

    # support_level 影响原语覆盖级别和报告说明。
    if "support_level" in set_fields:

        # 缺省空字符串而不猜测未知级别。
        # 支持级别决定报告中的语义可信范围。
        dict_projection["support_level"] = str(profile.get("support_level") or "")  # 保留支持级别

    # 返回规范投影。
    return dict_projection

# _insert_profile 更新完整目录的 namespace 和统一 profiles 索引。
def _insert_profile(dict_catalog: dict[str, Any], str_name: str, profile: dict[str, Any]) -> None:
    """把同名显式 profile 写入正确命名空间。

    参数:
        dict_catalog: 可修改的完整目录。
        str_name: profile 模块名。
        profile: 已归一化 profile。
    返回:
        无；目录对象原位更新。
    """

    # library 字段决定显式 profile 的兼容 namespace。
    str_library = str(profile.get("library") or "EXPLICIT").upper()  # 解析 profile 的库来源

    # XPM 前缀与 UNISIM library 字段共同决定外部 profile 的归档位置。
    str_namespace = (
        "xpm"  # XPM 名称使用 xpm_ 前缀
        if str_name.startswith("xpm_")  # 识别 XPM profile 的名称前缀分支
        else "unisim"  # UNISIM profile 写入固定库
        if str_library == "UNISIM"  # 选择 UNISIM 固定命名空间
        else "profiles"  # 未知库只保留兼容索引
    )  # 完成显式 profile 的命名空间选择

    # 同名 profile 只覆盖当前 namespace 条目。
    dict_catalog.setdefault(str_namespace, {})[str_name] = deepcopy(profile)  # 更新公开命名空间条目

    # profiles 索引同步覆盖，保证后续查询一致。
    dict_catalog.setdefault("profiles", {})[str_name] = deepcopy(profile)  # 同步统一查询索引
