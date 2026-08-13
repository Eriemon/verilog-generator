"""requirements 推断与基线提取辅助逻辑。"""

# 启用前向引用标注，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 导入 requirements 推断阶段需要的标准库能力。
import copy
from dataclasses import dataclass
from typing import Any

# 声明流式能力判定允许使用的标准取值。
STREAMABILITY_VALUES = ("streamable", "non_streamable", "unknown")  # 流式能力枚举

# 声明 requirements 阶段支持的接口家族枚举。
INTERFACE_FAMILIES = ("native", "axi_stream", "axi4", "axi4_lite", "ahb", "apb", "custom")  # 接口家族枚举

# 声明 AXI4 接口支持的变体取值。
AXI4_VARIANTS = ("axi4_full", "axi4_lite")  # AXI4 变体枚举

@dataclass(frozen=True)
class RequirementConfirmation:
    """
    汇总 requirements 确认阶段需要回写的用户确认信息。

    :param confirmed_by_user: 用户是否已经显式确认 requirements。
    :param confirmation_notes: 用户确认过程中保留的说明文本。
    """

    # 标记用户是否已经显式确认当前 requirements 约束。
    confirmed_by_user: bool | None = None  # 用户是否显式确认当前 requirements

    # 保留用户确认阶段附带的补充说明文本。
    confirmation_notes: str | None = None  # 用户确认阶段保留的备注文本

# 声明 AXI4 与 AHB/APB 共用的角色取值。
AXI4_ROLES = ("master", "slave")  # 主从角色枚举

# 声明 AXI4 访问模式的允许取值。
AXI4_MODES = ("read", "write", "read_write")  # AXI4 访问模式枚举

# 预先展开 AXI4 相关枚举文本，避免报错消息在多处重复拼接。
AXI4_VARIANT_LABELS = ", ".join(AXI4_VARIANTS)  # AXI4 变体枚举的展示文本

# 预先展开 AXI4 角色枚举文本，供错误消息直接复用。
AXI4_ROLE_LABELS = ", ".join(AXI4_ROLES)  # AXI4 主从角色枚举的展示文本

# 预先展开 AXI4 访问模式枚举文本，供错误消息直接复用。
AXI4_MODE_LABELS = ", ".join(AXI4_MODES)  # AXI4 读写模式枚举的展示文本

# 声明所有接口模板 profile 共用的基础键集合。
INTERFACE_TEMPLATE_PROFILE_KEYS = ("template_id",)  # 模板 profile 的公共键

# 列出 AXI Stream 场景下允许保留到 interface_profile 的字段。
AXI_STREAM_PROFILE_KEYS = (
    "keep_ready",  # 是否在模板继承后继续保留 ready 握手
    "keep_last",  # 是否在模板继承后继续保留 last 帧尾语义
    "data_width",  # 数据通道的位宽配置
    "clock_reset_domain",  # 流接口绑定的时钟复位域
    *INTERFACE_TEMPLATE_PROFILE_KEYS,  # 所有接口模板共享的基础 profile 键
)

# 汇总 AXI4 规格确认阶段可沿用的总线配置字段。
AXI4_PROFILE_KEYS = (
    "axi4_variant",  # 记录 full/lite 变体选择
    "role",  # 记录 master/slave 角色
    "read_write_mode",  # 记录读写访问模式
    "data_width",  # 记录数据总线位宽
    "addr_width",  # 记录地址总线位宽
    "id_width",  # 记录事务 ID 位宽
    "burst_support",  # 记录是否启用 burst 访问
    "max_burst_len",  # 记录 burst 最大长度
    "clock_reset_domain",  # 记录 AXI4 所属时钟复位域
    *INTERFACE_TEMPLATE_PROFILE_KEYS,  # 叠加接口模板共享键
)

# 收集 AHB 接口确认阶段允许继续保留的字段。
AHB_PROFILE_KEYS = (
    "role", "data_width",  # AHB 主从角色和数据位宽
    "addr_width", "clock_reset_domain",  # AHB 地址空间宽度和时钟域
    *INTERFACE_TEMPLATE_PROFILE_KEYS,  # APB 场景继续复用的公共模板键
)

# 收集 APB 外设接口确认阶段允许沿用的字段。
APB_PROFILE_KEYS = (
    "role", "data_width",  # APB 外设主从角色和数据位宽
    "addr_width", "clock_reset_domain",  # APB 外设地址空间宽度和时钟域
    *INTERFACE_TEMPLATE_PROFILE_KEYS,  # 继承接口模板公共键
)

# 汇总 native 接口必须拒绝的所有总线特定 profile 字段。
NATIVE_FORBIDDEN_PROFILE_KEYS = frozenset(  # native 模式下不允许残留的 profile 键全集
    (*AXI_STREAM_PROFILE_KEYS, *AXI4_PROFILE_KEYS, *AHB_PROFILE_KEYS, *APB_PROFILE_KEYS)  # 汇总所有总线专属字段
)

# 记录启发式流式判定时优先扫描的语义关键词。
STREAM_KEYWORDS = (
    "stream", "packet", "frame",  # 直接描述持续流、分包或分帧输入
    "sample", "line", "token",  # 描述逐样本、逐行或逐 token 推进
    "vector", "sequence", "sliding window",  # 描述顺序向量流和窗口滑动模式
    "throughput", "ii", "pipeline",  # 描述吞吐、II 和流水线约束
    "valid", "ready", "last",  # 描述流接口握手与帧尾信号
    "data", "keep", "user",  # 描述数据通道与 AXIS 侧带语义
)  # 流式特征关键词集合

# 根据规格与证据推断任务是否更接近流式处理。
def detect_streamability(spec: dict[str, Any], evidence: dict[str, Any] | None = None) -> str:
    """
    根据规格文本和外部证据推断任务的流式处理属性。

    :param spec: 待分析的规格字典。
    :param evidence: 可选的外部证据集合。
    :return: `streamable`、`non_streamable` 或 `unknown` 之一。
    """

    # 优先读取规格顶层显式声明的流式能力。
    str_explicit_streamability = str(spec.get("streamability") or "")  # 顶层显式流式能力

    # 命中合法显式值时直接返回，避免继续做启发式猜测。
    if str_explicit_streamability in STREAMABILITY_VALUES:

        # 返回用户或上游已经确认过的流式能力。
        return str_explicit_streamability

    # 这里单独抽取 requirements，是为了优先复用其中已经人工确认过的接口家族结论。
    dict_design_requirements = (
        spec.get("design_requirements")  # 保留规格里原始的 requirements 容器
        if isinstance(spec.get("design_requirements"), dict)  # 只有字典形态才存在可复用的家族字段
        else {}  # requirements 缺失或结构异常时直接退回启发式推断路径
    )  # 供接口家族判定复用的 requirements 字典

    # requirements 存在显式流式结论时直接复用人工确认结果。
    if dict_design_requirements:

        # 提取 requirements 区块内显式给出的流式能力。
        str_requirement_streamability = str(dict_design_requirements.get("streamability") or "")  # requirements 层流式能力

        # requirements 中存在合法显式值时直接采用该结果。
        if str_requirement_streamability in STREAMABILITY_VALUES:

            # 返回 requirements 层已经确认的流式能力。
            return str_requirement_streamability

    # 汇总规格和证据文本，供后续关键词判定复用。
    str_text_blob = _spec_text_blob(spec, evidence, include_argument_values=True)  # 含 arguments 的统一小写文本

    # 命中强指向流接口的关键词时直接判定为流式任务。
    if "m_axi" in str_text_blob or "axis" in str_text_blob or "axi-stream" in str_text_blob:

        # 返回强流式信号驱动下的判定结果。
        return "streamable"

    # 命中流水线或逐项处理语义时也视为流式任务。
    if "for each index below length" in str_text_blob or "ii=1" in str_text_blob or "pipeline" in str_text_blob:

        # 返回流水线特征驱动下的判定结果。
        return "streamable"

    # 使用通用流式关键词做最后一轮启发式匹配。
    if any(str_keyword in str_text_blob for str_keyword in STREAM_KEYWORDS):

        # 返回关键词命中后的流式判定结果。
        return "streamable"

    # 默认把未命中流式特征的任务视为非流式。
    return "non_streamable"

# 根据规格文本推断默认接口家族。
def detect_interface_family(
    spec: dict[str, Any], streamability: str | None = None, evidence: dict[str, Any] | None = None
) -> str | None:
    """
    根据规格与证据文本推断默认接口家族。

    :param spec: 待分析的规格字典。
    :param streamability: 上游已经推断出的流式能力。
    :param evidence: 可选的外部证据集合。
    :return: 推断得到的接口家族；无法判断时返回 `None`。
    """

    # 优先读取规格顶层显式声明的接口家族。
    str_explicit_interface_family = str(spec.get("interface_family") or "")  # 顶层已给定的接口家族取值

    # 顶层显式值合法时直接返回，避免后续猜测覆盖人工确认。
    if str_explicit_interface_family in INTERFACE_FAMILIES:

        # 返回规格顶层已经确认的接口家族。
        return str_explicit_interface_family

    # 只保留 requirements 里已经结构化的合同快照，供接口家族判定直接读取。
    bool_has_structured_requirements = isinstance(spec.get("design_requirements"), dict)  # requirements 是否已经是结构化字典

    # 根据结构化判定结果生成后续可直接读取字段的 requirements 字典。
    dict_design_requirements = spec.get("design_requirements") if bool_has_structured_requirements else {}  # 接口家族判定可读的 requirements 字典

    # requirements 存在显式接口家族时直接沿用人工确认结果。
    if dict_design_requirements:

        # 读取 requirements 层已经确认的接口家族字段。
        str_requirement_interface_family = str(dict_design_requirements.get("interface_family") or "")  # requirements 层保留的接口家族

        # requirements 中存在合法值时直接复用。
        if str_requirement_interface_family in INTERFACE_FAMILIES:

            # 返回 requirements 层已经确认的接口家族。
            return str_requirement_interface_family

    # 汇总规格和证据文本，供接口家族关键词匹配复用。
    str_text_blob = _spec_text_blob(spec, evidence)  # 不含 arguments 的统一小写文本

    # 命中 APB 关键词时直接锁定 APB 总线场景。
    if "apb" in str_text_blob:

        # APB 语义已经足够明确，直接返回 APB。
        return "apb"

    # 命中 AHB 关键词时直接判定为 AHB 总线协议，而不是继续落到更泛化的 AXI 推断。
    if "ahb" in str_text_blob:

        # AHB 语义已经足够明确，直接回到 AHB 总线家族。
        return "ahb"

    # 识别 AXI4-Lite 控制寄存器类接口场景。
    if (
        "axi4-lite" in str_text_blob
        or "axi lite" in str_text_blob
        or "axi4_lite" in str_text_blob
        or "axil" in str_text_blob
    ):

        # 控制寄存器语义优先归到 AXI4-Lite。
        return "axi4_lite"

    # 识别 AXI4 memory-mapped 主从或 DMA 场景。
    if (
        "axi4" in str_text_blob
        or "m_axi" in str_text_blob
        or "memory mapped" in str_text_blob
        or "burst" in str_text_blob
        or "dma" in str_text_blob
    ):

        # 这些关键词都更接近 AXI4 memory-mapped 语义。
        return "axi4"

    # 识别 AXI Stream 或明确流式任务场景。
    if (
        "axi-stream" in str_text_blob
        or "axis" in str_text_blob
        or "tvalid" in str_text_blob
        or "tready" in str_text_blob
        or streamability == "streamable"
    ):

        # 流接口握手语义更适合回到 AXI Stream。
        return "axi_stream"

    # 针对寄存器控制类关键词回退到 AXI4-Lite。
    if any(str_keyword in str_text_blob for str_keyword in ("register", "control", "status", "configuration", "csr")):

        # 返回寄存器控制语义更常见的 AXI4-Lite 总线选择。
        return "axi4_lite"

    # 无法从文本中稳定推断时返回空值，交给上游继续确认。
    return None

# 汇总规格与证据中的文本片段，供关键词判定逻辑复用。
def _spec_text_blob(
    spec: dict[str, Any],
    evidence: dict[str, Any] | None = None,
    *,
    include_argument_values: bool = False,
) -> str:
    """
    汇总规格与证据中的文本片段并拼接成统一小写文本。

    :param spec: 待分析的规格字典。
    :param evidence: 可选的外部证据集合。
    :param include_argument_values: 是否把接口 arguments 中的值也纳入文本。
    :return: 归一化后可用于关键词匹配的小写文本。
    """

    # 初始化文本片段收集列表。
    list_fragments: list[str] = []  # 供关键词匹配使用的原始文本片段

    # 读取规格的主描述文本。
    obj_description = spec.get("description")  # 顶层描述字段

    # 仅当 description 为字符串时才收录进文本片段列表。
    if isinstance(obj_description, str):

        # 收录规格主描述文本。
        list_fragments.append(obj_description)

    # 逐段收录行为、约束和备注文本。
    for str_section_key in ("behavior", "constraints", "notes"):

        # 读取当前文本区段的条目列表。
        list_section_items = spec.get(str_section_key, []) or []  # 当前区段原始条目列表

        # 逐项提取当前区段中的可用文本。
        for obj_section_item in list_section_items:

            # 直接收录纯字符串条目。
            if isinstance(obj_section_item, str):

                # 把字符串条目追加进文本片段列表。
                list_fragments.append(obj_section_item)

            # 对字典条目仅提取 text 字段。
            elif isinstance(obj_section_item, dict) and obj_section_item.get("text"):

                # 收录字典条目中的 text 文本。
                list_fragments.append(str(obj_section_item["text"]))

    # 只保留结构化 interfaces，对端口名和角色文本做统一关键词聚合。
    dict_interfaces = spec.get("interfaces") if isinstance(spec.get("interfaces"), dict) else {}  # 文本语料聚合使用的接口描述字典

    # 逐项收录 ports 中的端口字段值。
    for dict_port in dict_interfaces.get("ports", []) or []:

        # 只处理字典形态的端口条目。
        if isinstance(dict_port, dict):

            # 把当前端口的所有字段值展开进文本片段列表。
            list_fragments.extend(str(obj_field_value) for obj_field_value in dict_port.values())

    # 需要时再补充 interfaces.arguments 中的值。
    if include_argument_values:

        # 逐项收录 arguments 中的参数字段值。
        for dict_argument in dict_interfaces.get("arguments", []) or []:

            # 只处理字典形态的参数条目。
            if isinstance(dict_argument, dict):

                # 把当前参数的所有字段值展开进文本片段列表。
                list_fragments.extend(str(obj_field_value) for obj_field_value in dict_argument.values())

    # 证据存在时再补充前若干条证据文本。
    if evidence:

        # 限定证据采样窗口，避免远端或长上下文文本过载。
        list_evidence_items = evidence.get("items", [])[:12]  # 采样后的证据条目列表

        # 逐项提取证据文本。
        for dict_evidence_item in list_evidence_items:

            # 只收录带 text 字段的字典条目。
            if isinstance(dict_evidence_item, dict) and dict_evidence_item.get("text"):

                # 把证据文本追加进文本片段列表。
                list_fragments.append(str(dict_evidence_item["text"]))

    # 把所有文本片段规整成统一小写 blob。
    str_text_blob = " ".join(str_fragment.lower() for str_fragment in list_fragments)  # 小写文本匹配串

    # 返回汇总后的统一文本。
    return str_text_blob

# 单独抽出 requirements 基线合并逻辑，避免默认值主流程被初始化细节淹没。
def _merged_requirement_base(
    spec: dict[str, Any],
    design_requirements: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    提取并合并 requirements 基线快照。

    :param spec: 当前待处理的规格字典。
    :param design_requirements: 调用侧额外传入的 requirements 覆写。
    :return: 可继续补齐的 requirements 基线副本。
    """

    # 规格中已有 requirements 且结构合法时，先深拷贝一份作为本轮合并基线。
    if isinstance(spec.get("design_requirements"), dict):

        # 深拷贝已有 requirements，避免修改时反向污染原始 spec。
        dict_base_requirements = copy.deepcopy(spec["design_requirements"])  # 合并前的 requirements 基线副本

    # 规格中没有结构化 requirements 时，从空白基线开始拼装本轮合同快照。
    else:

        # 非字典输入统一回退为空 requirements 基线。
        dict_base_requirements = {}  # 缺省 requirements 基线

    # 调用侧显式给出 design_requirements 时覆盖到基线配置。
    if design_requirements:

        # 合并调用侧 design_requirements，保留调用方优先级。
        dict_base_requirements.update(copy.deepcopy(design_requirements))

    # 返回已经吸收调用侧覆写的 requirements 基线。
    return dict_base_requirements

# 把调用侧 requirements 参数合并回统一 spec 结构前，先归一化用户确认载荷。
def _normalize_requirement_confirmation(
    confirmation: RequirementConfirmation | None,
) -> RequirementConfirmation:
    """
    规范化 requirements 确认信息输入。

    :param confirmation: 调用侧传入的确认对象。
    :return: 始终可安全读取字段的确认对象。
    """

    # 调用侧未提供确认对象时，回退到全空的默认确认载荷。
    if confirmation is None:

        # 返回一个字段全部为空的默认确认对象。
        return RequirementConfirmation()

    # 直接复用调用侧已经构造好的确认对象。
    return confirmation
