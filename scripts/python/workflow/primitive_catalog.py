"""读取并校验 AMD-Xilinx 原语、XPM 和项目级 IP 语义目录。"""

# future annotations 延后解析动态 JSON profile 的类型。
from __future__ import annotations

# deepcopy 隔离缓存中的已验证事实，避免调用方写回资产副本。
from copy import deepcopy

# json 读取随 skill 发布的固定原语资产。
import json

# lru_cache 让同一进程只校验一次固定清单。
from functools import lru_cache

# pathlib 以模块位置解析安装内的资产路径。
from pathlib import Path

# Any 描述原语 JSON 中受控的动态字段。
from typing import Any

# 原语资产必须和当前 Python 模块一起安装，不能依赖工作目录。
primitive_asset_path = Path(__file__).resolve().parents[3] / "assets" / "xilinx_primitive_semantics.json"  # AMD-Xilinx 原语资产路径

# 固定 UNISIM 名称集合阻止扫描器白名单无边界扩张。
tuple_expected_unisim_primitives = (  # 固定 UNISIM 原语清单
    "IBUF", "IBUFDS", "IBUFDS_GTE2", "IBUFDS_GTE3", "IBUFDS_GTE4",  # 差分和单端输入
    "OBUF", "OBUFT", "OBUFDS", "OBUFTDS", "IOBUF", "IOBUFDS",  # 输出、三态和双向 IO 原语
    "BUFG", "BUFGCE", "BUFGCTRL", "BUFH", "BUFHCE", "BUFIO", "BUFR",  # 时钟缓冲
    "MMCME2_ADV", "PLLE2_ADV", "MMCME3_ADV", "PLLE3_ADV", "MMCME4_ADV", "PLLE4_ADV",  # 时钟管理
    "FDRE", "FDSE", "FDCE", "FDPE", "LDCE", "LDPE",  # 触发器和锁存器
    "RAMB18E1", "RAMB36E1", "RAMB18E2", "RAMB36E2", "FIFO18E1", "FIFO36E1",  # 7 系列和 UltraScale 存储
    "FIFO18E2", "FIFO36E2", "DSP48E1", "DSP48E2", "STARTUPE2", "STARTUPE3",  # FIFO、DSP 和启动边界
    "ICAPE2", "ICAPE3", "DNA_PORT", "USR_ACCESSE2", "GTXE2_CHANNEL", "GTHE3_CHANNEL", "GTYE4_CHANNEL",  # 配置和收发器
)

# 固定 XPM 名称集合覆盖 CDC、FIFO 和 memory 模板。
tuple_expected_xpm_primitives = (  # loader 用该元组拒绝缺失或新增的 XPM 名称
    "xpm_cdc_array_single", "xpm_cdc_async_rst", "xpm_cdc_gray", "xpm_cdc_handshake",  # CDC 基础模板
    "xpm_cdc_low_latency_handshake", "xpm_cdc_pulse", "xpm_cdc_single", "xpm_cdc_sync_rst",  # CDC 控制模板
    "xpm_fifo_async", "xpm_fifo_axif", "xpm_fifo_axil", "xpm_fifo_axis", "xpm_fifo_sync",  # XPM 异步与同步 FIFO 模板的固定名称
    "xpm_memory_dpdistram", "xpm_memory_dprom", "xpm_memory_sdpram", "xpm_memory_spram",  # 分布式和单口 memory
    "xpm_memory_sprom", "xpm_memory_tdpram",  # XPM 单口 ROM 与双口 memory 模板的固定名称
)

# 项目级 IP 只提供 manifest 分类，不进入可递归 primitive 实现索引。
tuple_expected_project_ip_categories = (  # 固定项目 IP manifest 分类
    "vio", "ila", "clock_wizard", "processor_system_reset", "block_memory_generator",  # 调试、时钟和复位 IP
    "fifo_generator", "axi_memory_mapped", "axi_stream", "gt_phy", "generic_ip",  # 存储、AXI、GT 和通用 IP
)

# load_primitive_semantic_catalog 是原语目录的公开稳定入口。
def load_primitive_semantic_catalog() -> dict[str, Any]:
    """读取并返回通过清单校验的 AMD-Xilinx 语义目录。

    参数:
        无。
    返回:
        包含三个公开命名空间和统一 profiles 索引的独立目录副本。
    异常:
        ValueError: 资产 schema、清单或 profile 字段不满足治理合同。
    """

    # 每次公开返回都断开缓存引用，防止调用方改变后续扫描事实。
    dict_catalog = deepcopy(_load_primitive_catalog_cached())  # 本次调用的原语目录快照

    # 返回与缓存隔离的可读目录。
    return dict_catalog

# 缓存只保存经过 schema、清单和字段检查的目录。
@lru_cache(maxsize=1)

# 读取缓存目录并保证每个返回 profile 都已完成字段归一化。
def _load_primitive_catalog_cached() -> dict[str, Any]:
    """读取一次原语资产并建立可查询 profile 索引。

    参数:
        无。
    返回:
        进程内缓存使用的只读语义目录副本。
    异常:
        ValueError: 原语资产的顶层或端口事实不合法。
    """

    # JSON 读取使用安装相对路径，独立于调用方工作目录。
    dict_payload = json.loads(primitive_asset_path.read_text(encoding="utf-8"))  # 原语资产 JSON 载荷

    # 顶层 schema 和 kind 是防止误读其他配置文件的边界。
    if not isinstance(dict_payload, dict) or dict_payload.get("schema_version") != 1:

        # schema 漂移必须在扫描启动前失败。
        raise ValueError("> ERR: [Python] Xilinx primitive semantic catalog schema is invalid.")

    # kind 固定为 AMD-Xilinx 原语目录，禁止把 VG 规则目录混入。
    if dict_payload.get("kind") != "amd-xilinx-primitive-semantics":

        # kind 错误意味着资产职责已经发生漂移。
        raise ValueError("> ERR: [Python] Xilinx primitive semantic catalog kind is invalid.")

    # 每个 namespace 先校验 UNISIM 名称集合和 profile 类型，再进行 profile 归一化。
    _validate_namespace(dict_payload, "unisim", tuple_expected_unisim_primitives)

    # 校验 XPM 名称集合，保证 CDC/FIFO 清单不发生漂移。
    _validate_namespace(dict_payload, "xpm", tuple_expected_xpm_primitives)

    # 校验项目 IP manifest 分类，避免把工程 IP 当成原语实现。
    _validate_namespace(dict_payload, "project_ip", tuple_expected_project_ip_categories)

    # 归一化 UNISIM profile 并保留公开 namespace，使端口事实可供 VG097 和调用方查询。
    dict_unisim = _normalize_namespace(dict_payload["unisim"], "UNISIM")  # UNISIM 端口和边界事实

    # 把 XPM CDC/FIFO profile 归一化为层级规则可消费的边界事实。
    dict_xpm = _normalize_namespace(dict_payload["xpm"], "XPM")  # XPM CDC/FIFO 端口边界事实

    # 归一化项目 IP manifest，但不把它们当成可递归原语。
    dict_project_ip = _normalize_namespace(dict_payload["project_ip"], "PROJECT_IP")  # 项目 IP manifest 事实

    # profiles 索引把三类来源折叠成一个统一名称查询表。
    dict_source_profiles: dict[str, dict[str, Any]] = {}  # 收集三类公开来源的 profile

    # 将可直接建模的 UNISIM 原语加入统一索引。
    dict_source_profiles.update(dict_unisim)

    # 将 XPM 边界 profile 加入统一索引。
    dict_source_profiles.update(dict_xpm)

    # 将不递归展开的 project IP manifest 加入统一索引。
    dict_source_profiles.update(dict_project_ip)

    # 为名称查询建立与 namespace 解耦的 profile 快照。
    dict_profiles = {
        str_name: deepcopy(dict_profile)  # 每个查询条目独立持有 profile
        for str_name, dict_profile in dict_source_profiles.items()  # 逐项复制归一化 profile
    }

    # 返回重新组装的结构，避免暴露未归一化的 JSON 子对象。
    dict_result = {  # 组装带 namespace、profiles 和 counts 的完整目录
        **dict_payload,  # 保留资产中的版本、kind 和验证范围
        "unisim": dict_unisim,  # 发布归一化后的 UNISIM namespace
        "xpm": dict_xpm,  # 发布 CDC/FIFO 原语的归一化 namespace
        "project_ip": dict_project_ip,  # 发布项目 IP manifest namespace
        "profiles": dict_profiles,  # 发布统一名称查询索引
        "counts": {  # 发布三个 namespace 的清单计数
            "unisim": len(dict_unisim),  # UNISIM 清单数量
            "xpm": len(dict_xpm),  # CDC/FIFO XPM 名称数量
            "project_ip": len(dict_project_ip),  # 项目 IP 分类数量
        },
    }  # 完整原语目录结果

    # 返回给缓存装饰器保存的已验证目录。
    return dict_result

# _validate_namespace 固定 namespace 的类型和精确名称集合。
def _validate_namespace(
    dict_payload: dict[str, Any],
    str_namespace: str,
    tuple_expected_names: tuple[str, ...],
) -> None:
    """校验一个公开原语 namespace 的清单和 profile 类型。

    参数:
        dict_payload: 已读取的 JSON 顶层对象。
        str_namespace: 当前校验的公开命名空间名称。
        tuple_expected_names: 该命名空间允许的精确名称集合。
    返回:
        无；不符合合同时抛出 ValueError。
    异常:
        ValueError: namespace 不是对象、清单漂移或 profile 非对象。
    """

    # namespace 必须是名称到 profile 的对象。
    dict_namespace = dict_payload.get(str_namespace)  # 当前公开命名空间

    # 非对象 namespace 不能支持精确名称校验。
    if not isinstance(dict_namespace, dict):

        # 空值或数组无法支持按名称的事实查询。
        raise ValueError(f"> ERR: [Python] Xilinx primitive namespace {str_namespace} must be an object.")

    # 清单比较同时拦截漏项和未授权新增名称。
    if set(dict_namespace) != set(tuple_expected_names):

        # 固定库存漂移必须由资产维护者显式修订。
        raise ValueError(
            f"> ERR: [Python] Xilinx primitive namespace {str_namespace} "
            "does not match the fixed inventory."
        )

    # 每个名称都必须携带对象 profile。
    if any(not isinstance(dict_profile, dict) for dict_profile in dict_namespace.values()):

        # 标量 profile 无法描述端口方向和输出边界。
        raise ValueError(f"> ERR: [Python] Xilinx primitive namespace {str_namespace} contains a non-object profile.")

# _normalize_namespace 为一个公开 namespace 统一补齐 profile 字段。
def _normalize_namespace(
    dict_namespace: dict[str, Any],
    str_library: str,
) -> dict[str, dict[str, Any]]:
    """返回 namespace 内每个 profile 的独立归一化副本。

    参数:
        dict_namespace: 已通过名称和类型校验的 namespace mapping。
        str_library: 归一化 profile 使用的库标识。
    返回:
        原语名称到归一化 profile 的 mapping。
    """

    # 每个 profile 单独归一化，保留名称顺序并建立端口边界事实。
    dict_result = {
        str_name: _normalize_profile(str_name, dict_profile, str_library)  # 当前名称的可审计 profile
        for str_name, dict_profile in dict_namespace.items()  # 将固定名称交给 profile 归一化
    }  # 当前 namespace 的归一化结果

    # 返回 namespace 副本，调用方不会接触原始 JSON 对象。
    return dict_result

# _normalize_profile 统一 profile 的身份、端口和 output boundary 字段。
def _normalize_profile(
    str_name: str,
    dict_profile: dict[str, Any],
    str_library: str,
) -> dict[str, Any]:
    """返回一个可被 VG 规则消费的 profile 副本。

    参数:
        str_name: 当前 profile 在固定清单中的名称。
        dict_profile: JSON 中的原始 profile 对象。
        str_library: profile 所属的 UNISIM、XPM 或项目 IP 库。
    返回:
        带稳定身份、端口和 output boundary 字段的 profile。
    异常:
        ValueError: 端口或输出 boundary 字段类型不合法。
    """

    # 复制 JSON profile，避免缓存对象被写回。
    dict_result = deepcopy(dict_profile)  # 当前 profile 的可修改副本

    # 用固定清单名称覆盖资产内部名称，先写入 profile 的可读显示名称。
    dict_result["name"] = str_name  # profile 对外显示名称

    # 再写入实例绑定使用的模块名称。
    dict_result["module_name"] = str_name  # profile 实例绑定名称

    # 明确 profile 的公开库来源。
    dict_result["library"] = str_library  # profile 所属公开库

    # 缺省元数据只描述可信范围，并用 family 供报告按器件族聚合原语来源。
    dict_result["family"] = str(dict_result.get("family") or "unknown")  # profile 器件族来源

    # support_level 表明规则最多能信任到哪一层语义。
    dict_result["support_level"] = str(dict_result.get("support_level") or "P3_OPAQUE")  # profile 语义可信层级

    # clock_capable 只由资产事实决定，不从模块名称推断。
    dict_result["clock_capable"] = bool(dict_result.get("clock_capable", False))  # profile 是否具备时钟能力

    # 缺省空 ports 保持未知接口不可比较。
    dict_ports = dict_result.get("ports", {})  # 当前 profile 的端口 mapping

    # 端口对象校验失败时阻断本次目录加载。
    if not isinstance(dict_ports, dict):

        # 非对象端口表不能用于 VG097 位宽判断。
        raise ValueError(f"> ERR: [Python] Xilinx primitive {str_name} ports must be an object.")

    # 端口方向、宽度和角色通过独立函数统一校验。
    dict_result["ports"] = _normalize_ports(dict_ports)  # 归一化后的端口事实

    # 缺省空 outputs 使未登记 output 保持 opaque。
    dict_outputs = dict_result.get("outputs", {})  # 当前 profile 的输出边界 mapping

    # 输出边界对象校验失败时阻断跨层传播判断。
    if not isinstance(dict_outputs, dict):

        # 非对象 output 表会让 hierarchy 传播失去边界。
        raise ValueError(f"> ERR: [Python] Xilinx primitive {str_name} outputs must be an object.")

    # 只有字符串 boundary 才能进入规则层传播判断。
    dict_result["outputs"] = {
        str_port: str_boundary  # 保留端口级 output boundary
        for str_port, str_boundary in dict_outputs.items()  # 遍历资产声明的 output 端口
        if isinstance(str_boundary, str)  # 丢弃无法解释的 boundary 值
    }  # 当前 profile 的 output boundary 事实

    # families 与 gate_policy 是报告和规则路由使用的稳定元数据。
    dict_result["families"] = [str(dict_result["family"])]  # 可检索的 profile 族列表

    # gate_policy 记录四个目标门禁各自的解释边界。
    dict_result["gate_policy"] = {  # VG097/132/146/147 的规则策略
        "vg097": "interface_width",  # 端口方向与宽度比较
        "vg132": "role_aware_clock_port",  # 显式时钟角色比较
        "vg146": "transparent_or_local_inconclusive",  # 组合输出边界策略
        "vg147": "transparent_or_local_inconclusive",  # 循环组合输出边界策略
    }

    # 返回归一化 profile。
    return dict_result

# _normalize_ports 统一 namespace profile 的端口方向、宽度和角色。
def _normalize_ports(dict_ports: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """校验并复制一个 profile 的端口 mapping。

    参数:
        dict_ports: 原始端口名称到端口事实的 mapping。
    返回:
        端口名称到归一化端口事实的 mapping。
    异常:
        ValueError: 端口 profile、方向、宽度或角色类型非法。
    """

    # 每个端口单独校验，保证错误消息能指向 formal 名称。
    dict_result = {
        str_port: _normalize_port(str_port, dict_port)  # 当前 formal 的归一化事实
        for str_port, dict_port in dict_ports.items()  # 遍历原语声明的 formal 端口
    }  # 当前 profile 的端口事实

    # 返回不共享输入容器的端口 mapping。
    return dict_result

# _normalize_port 校验单个 formal 端口的最小安全事实。
def _normalize_port(str_name: str, value: Any) -> dict[str, Any]:
    """返回一个方向、宽度和角色都可比较的端口事实。

    参数:
        str_name: 当前 formal 端口名称。
        value: JSON 中的端口 profile 对象。
    返回:
        端口方向、宽度和角色已规范化的字典。
    异常:
        ValueError: 端口对象或其字段类型不符合 Verilog 合同。
    """

    # 端口必须是对象；对象校验失败时阻断当前原语 profile，禁止用字符串猜测方向。
    if not isinstance(value, dict):

        # 端口结构错误必须在目录加载阶段暴露。
        raise ValueError(f"> ERR: [Python] Xilinx primitive port {str_name} must be an object.")

    # 方向只接受 Verilog 的 input、output 和 inout。
    str_direction = str(value.get("direction") or "").lower()  # 当前 formal 的方向

    # 未知方向不能进入 VG097 的接口比较。
    if str_direction not in {"input", "output", "inout"}:

        # 未知方向无法建立 VG097 或 hierarchy 边界。
        raise ValueError(f"> ERR: [Python] Xilinx primitive port {str_name} has an invalid direction.")

    # 宽度必须是正整数，参数化宽度由后续显式接口事实处理。
    obj_width_candidate: object = value.get("width", 1)  # 当前 formal 的原始宽度字段

    # 布尔值虽然是 int 子类，也不能伪装成端口宽度。
    if not isinstance(obj_width_candidate, int) or isinstance(obj_width_candidate, bool) or obj_width_candidate < 1:

        # 不可比较的宽度不能被静默当成标量。
        raise ValueError(f"> ERR: [Python] Xilinx primitive port {str_name} width must be a positive integer.")

    # 通过类型收窄后保存整数宽度，供 VG097 直接比较。
    int_obj_width: int = obj_width_candidate  # 通过类型收窄的正整数宽度

    # roles 只接受字符串列表，未知角色不会自动获得时钟权限。
    list_roles = value.get("roles", [])  # 当前 formal 的角色列表

    # 角色列表类型错误会使 VG132 无法保持 fail-closed。
    if not isinstance(list_roles, list) or any(not isinstance(item, str) for item in list_roles):

        # 角色类型错误会破坏 VG132 的 fail-closed 判定。
        raise ValueError(f"> ERR: [Python] Xilinx primitive port {str_name} roles must be a string list.")

    # 返回稳定的端口投影，保留资产中的额外元数据。
    dict_result = {  # 复制端口事实并覆盖三个稳定字段
        **value,  # 保留端口的额外文档元数据
        "direction": str_direction,  # 写入小写 Verilog 方向
        "width": int_obj_width,  # 写入通过校验的正整数宽度
        "roles": list(dict.fromkeys(list_roles)),  # 去重后保留端口角色
    }  # 当前 formal 的可比较端口事实

    # 返回端口副本。
    return dict_result
