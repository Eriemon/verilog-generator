"""预检 requirements 确认与代码生成规划辅助逻辑。"""

# 启用前向引用标注，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 导入 requirements 规划阶段需要的标准库能力。
import copy
import json
import re
from dataclasses import dataclass
from typing import Any

# 导入接口模板解析相关的本地辅助能力。
from .interface_templates import (
    InterfaceTemplateError,
    resolve_interface_template,
    select_interface_template,
)

# 导入模板摘要组装时依赖的本地辅助能力。
from .refined_templates import summarize_refined_templates
from .use_case_templates import select_use_case_template, summarize_use_case_template

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

# 把调用侧 requirements 参数合并回统一 spec 结构。
def apply_requirement_defaults(
    raw_spec: dict[str, Any], *,
    design_requirements: dict[str, Any] | None = None,
    pipeline_required: bool | None = None,
    streamability: str | None = None, interface_family: str | None = None,
    interface_profile: dict[str, Any] | None = None,
    confirmation: RequirementConfirmation | None = None,
) -> dict[str, Any]:
    """
    把调用侧传入的 requirements 参数合并回统一 spec 结构。

    :param raw_spec: 原始规格字典。
    :param design_requirements: 调用侧追加的 requirements 字典。
    :param pipeline_required: 调用侧显式指定的 pipeline 要求。
    :param streamability: 调用侧显式指定的流式能力。
    :param interface_family: 调用侧显式指定的接口家族。
    :param interface_profile: 调用侧显式指定的接口 profile。
    :param confirmation: 调用侧提供的 requirements 确认信息对象。
    :return: 合并完成后的规格字典副本。
    """

    # 深拷贝原始规格，避免调用侧对象被就地修改。
    dict_spec = copy.deepcopy(raw_spec)  # 合并中的规格副本

    # 提取目标类型，供后续写回 design_requirements 复用。
    str_target = str(dict_spec.get("target") or "")  # 规格目标类型

    # 先把调用侧确认对象规范成稳定形态，避免后续分支反复判空。
    requirement_confirmation_state = _normalize_requirement_confirmation(confirmation)  # 规范化后的确认信息对象

    # 先整理 requirements 基线，后续所有默认值都从这份基线继续合并。
    dict_base_requirements = _merged_requirement_base(dict_spec, design_requirements)  # 当前规格的 requirements 基线副本

    # 计算最终要写回规格的流式能力。
    str_resolved_streamability = (
        streamability  # 调用侧显式给出的流式能力
        or dict_base_requirements.get("streamability")  # requirements 基线中的流式能力
        or dict_spec.get("streamability")  # 规格顶层已有的流式能力
        or detect_streamability(dict_spec)  # 启发式推断得到的流式能力
    )  # 合并后的流式能力

    # 计算最终要写回规格的接口家族。
    str_resolved_interface_family = (
        interface_family  # 调用侧显式给出的接口家族
        or dict_base_requirements.get("interface_family")  # requirements 基线中的接口家族
        or dict_spec.get("interface_family")  # 规格顶层已有的接口家族
        or detect_interface_family(dict_spec, str(str_resolved_streamability))  # 启发式推断出的接口家族
    )  # 合并后的接口家族

    # 读取规格已有的接口 profile 配置。
    obj_existing_interface_profile = dict_spec.get("interface_profile", {})  # 原始接口 profile 配置

    # 把接口 profile 规范成可继续补齐的字典副本。
    dict_resolved_interface_profile = (
        copy.deepcopy(obj_existing_interface_profile)  # 复用规格中已有的接口 profile 副本
        if isinstance(obj_existing_interface_profile, dict)  # 仅在原值确实是字典时保留其键值
        else {}  # 非字典输入统一回退为空接口 profile
    )  # 合并中的接口 profile

    # requirements 基线中存在接口 profile 时继续向当前副本补齐。
    if isinstance(dict_base_requirements.get("interface_profile"), dict):

        # 合并 requirements 基线里的接口 profile。
        dict_resolved_interface_profile.update(copy.deepcopy(dict_base_requirements["interface_profile"]))

    # 调用侧显式给出接口 profile 时覆盖现有默认值。
    if interface_profile:

        # 合并调用侧显式接口 profile。
        dict_resolved_interface_profile.update(copy.deepcopy(interface_profile))

    # 计算最终要写回规格的 pipeline_required 标志。
    obj_pipeline_required_source: Any = (
        pipeline_required  # 调用侧显式给出的 pipeline 开关
        if pipeline_required is not None  # 调用侧明确传值时优先采用该结果
        else dict_base_requirements.get(  # 否则回退到 requirements 或规格顶层的历史配置
            "pipeline_required",  # 优先读取 requirements 快照里的 pipeline 标志
            dict_spec.get("pipeline_required", True),  # requirements 缺失时回退到规格顶层默认值
        )
    )  # pipeline_required 的原始来源值

    # 把多来源 pipeline 标志折算成稳定的布尔结果，便于统一写回 spec 和 requirements。
    bool_resolved_pipeline_required = bool(obj_pipeline_required_source)  # 规范化后的 pipeline_required 标志

    # 计算最终要写回规格的用户确认标志。
    bool_resolved_confirmed = (
        bool(requirement_confirmation_state.confirmed_by_user)  # 调用侧显式给出的确认状态
        if requirement_confirmation_state.confirmed_by_user is not None  # 只有本次明确传值时才覆盖历史确认状态
        else bool(dict_base_requirements.get("confirmed_by_user", False))  # 否则沿用 requirements 基线中的确认状态
    )  # 合并后的用户确认标志

    # 计算最终要写回规格的确认说明文本。
    str_resolved_notes = (
        requirement_confirmation_state.confirmation_notes  # 优先使用本次显式传入的确认说明
        if requirement_confirmation_state.confirmation_notes is not None  # 只有本次明确给出说明时才覆盖原有备注
        else str(  # 否则回退到 requirements 或规格顶层的历史说明
            dict_base_requirements.get(  # 优先读取 requirements 快照里的确认说明
                "confirmation_notes",  # 优先读取 requirements 快照中的人工确认备注
                dict_spec.get("confirmation_notes", ""),  # requirements 缺失时回退到规格顶层说明
            )
            or ""  # 没有历史备注时统一回退为空字符串
        )
    )  # 合并后的确认说明文本

    # 对总线类接口补齐接口 profile 默认字段。
    if str_resolved_interface_family in {"axi_stream", "axi4", "axi4_lite", "ahb", "apb"}:

        # 根据接口家族填充缺失的 profile 缺省值。
        dict_resolved_interface_profile = _apply_interface_defaults(  # 补齐总线接口缺省字段后的 profile
            str(str_resolved_interface_family),  # 当前确认后的接口家族
            dict_resolved_interface_profile,  # 待补齐默认字段的 interface_profile
        )

    # 把 pipeline_required 回写到规格顶层。
    dict_spec["pipeline_required"] = bool_resolved_pipeline_required  # 顶层 pipeline 开关

    # 把流式能力回写到规格顶层。
    dict_spec["streamability"] = str_resolved_streamability  # 顶层 streamability 标记

    # 把接口家族回写到规格顶层。
    dict_spec["interface_family"] = str_resolved_interface_family  # 顶层 interface_family 选择

    # 把接口 profile 回写到规格顶层。
    dict_spec["interface_profile"] = dict_resolved_interface_profile  # 顶层 interface_profile 快照

    # 保底写回 codegen_plan_required，默认沿用 staged flow 的启用状态。
    dict_spec["codegen_plan_required"] = bool(dict_spec.get("codegen_plan_required", True))  # staged flow 是否继续要求预先产出 codegen plan

    # 原样保留 codegen_plan_path，避免在 defaults 阶段修改显式路径。
    dict_spec["codegen_plan_path"] = dict_spec.get("codegen_plan_path")  # 外部显式提供的 codegen plan 路径

    # 组装设计需求快照，供后续验证和产物输出复用。
    dict_design_requirements = {
        "target": str_target,  # requirements 快照对应的规格目标
        "pipeline_required": bool_resolved_pipeline_required,  # 规范化后的 pipeline 开关
        "streamability": str_resolved_streamability,  # 规范化后的流式能力
        "interface_family": str_resolved_interface_family,  # 确认后的接口家族
        "interface_profile": dict_resolved_interface_profile,  # 补齐缺省值后的接口 profile
        "confirmed_by_user": bool_resolved_confirmed,  # 用户是否已完成 requirements 确认
        "confirmation_notes": str_resolved_notes,  # 最终保留的确认说明文本
    }  # 标准化后的 design_requirements 快照

    # 把标准化后的 design_requirements 写回规格顶层。
    dict_spec["design_requirements"] = dict_design_requirements  # 顶层 design_requirements 标准化快照

    # 返回合并完成后的规格副本。
    return dict_spec

# 校验 requirements 确认信息是否满足进入生成阶段的最低条件。
def validate_requirement_confirmation(spec: dict[str, Any]) -> None:
    """
    校验 requirements 确认信息是否完整。

    :param spec: 待校验的规格字典。
    :return: 无返回值；校验通过时静默返回。
    :raises ValueError: 当确认信息缺失或不完整时抛出。
    """

    # 收集 requirements 确认阶段遗留的问题列表。
    list_issues = _requirement_confirmation_issues(spec, require_confirmed=True)  # 进入生成前必须补齐的问题列表

    # 只要仍存在问题就阻止进入代码生成阶段。
    if list_issues:

        # 抛出符合 current-project 规范的错误消息。
        raise ValueError("> ERR: [Python] requirements confirmation is incomplete.")

# 要求 staged flow 已显式启用 codegen plan 产物。
def require_codegen_plan_enabled(spec: dict[str, Any]) -> None:
    """
    要求当前规格显式启用 codegen plan 工作流。

    :param spec: 待校验的规格字典。
    :return: 无返回值；启用时静默返回。
    :raises ValueError: 当未启用 codegen plan 时抛出。
    """

    # 未显式启用 codegen_plan_required 时直接阻止继续执行。
    if not spec.get("codegen_plan_required"):

        # 直接报告 staged flow 缺少 codegen plan 开关这一阻断条件。
        raise ValueError("> ERR: [Python] v1 staged flow requires codegen_plan_required=true.")

# 校验显式传入的 codegen plan 载荷是否满足生成前约束。
def validate_codegen_plan_payload(
    spec: dict[str, Any],
    payload: dict[str, Any],
    *,
    require_ready: bool,
) -> None:
    """
    校验显式传入的 codegen plan 载荷是否满足生成前约束。

    :param spec: 当前规格字典。
    :param payload: 外部提供的 codegen plan 载荷。
    :param require_ready: 是否要求该 plan 已达到 ready_for_generation 状态。
    :return: 无返回值；校验通过时静默返回。
    :raises ValueError: 当 plan 结构、关键字段或 readiness 状态不合法时抛出。
    """

    # codegen plan 载荷必须先满足最外层字典结构约束。
    if not isinstance(payload, dict):

        # 用统一错误前缀阻止非字典载荷继续参与校验。
        raise ValueError("> ERR: [Python] explicit codegen_plan_path must point to a JSON object.")

    # version 字段必须固定为当前受支持的版本号。
    if payload.get("version") != 1:

        # 用统一错误前缀报告 version 字段不匹配。
        raise ValueError("> ERR: [Python] explicit codegen plan must use version=1.")

    # name 字段必须与当前规格名称保持一致。
    if payload.get("name") != spec.get("name"):

        # 用统一错误前缀报告 plan 名称与 spec.name 不一致。
        raise ValueError("> ERR: [Python] explicit codegen plan name must match spec.name.")

    # target 字段必须与当前规格目标类型保持一致。
    if payload.get("target") != spec.get("target"):

        # 用统一错误前缀报告显式 plan 绑错了规格 target。
        raise ValueError("> ERR: [Python] explicit codegen plan target must match spec.target.")

    # 逐项检查核心决策子结构是否都保持字典形态。
    for str_required_field in ("interface_decision", "pipeline_strategy", "verification_strategy"):

        # 当前关键子结构不是字典时直接终止校验。
        if not isinstance(payload.get(str_required_field), dict):

            # 用统一错误前缀报告缺失或类型错误的关键子结构。
            raise ValueError(
                f"> ERR: [Python] explicit codegen plan must include object field `{str_required_field}`."
            )

    # open_questions 字段必须保持列表形态。
    if not isinstance(payload.get("open_questions", []), list):

        # 用统一错误前缀报告 open_questions 的结构错误。
        raise ValueError("> ERR: [Python] explicit codegen plan open_questions must be a list.")

    # ready_for_generation 字段必须保持布尔形态。
    if not isinstance(payload.get("ready_for_generation"), bool):

        # 用统一错误前缀报告 ready_for_generation 的类型错误。
        raise ValueError("> ERR: [Python] explicit codegen plan ready_for_generation must be a boolean.")

    # 提取当前 plan 尚未解决的开放问题列表。
    list_blockers = payload.get("open_questions", []) or ["Confirm the remaining design requirements."]  # 当前阻断生成的开放问题列表

    # 计算当前 plan 是否已经声明 ready_for_generation。
    bool_ready_for_generation = bool(payload.get("ready_for_generation"))  # 当前 plan 的 readiness 标志

    # 显式要求 ready 时，还要阻止带开放问题的未就绪 plan。
    if require_ready and (not bool_ready_for_generation or payload.get("open_questions")):

        # 拼接开放问题文本，供异常消息直接引用。
        str_blocker_summary = "; ".join(str(obj_item) for obj_item in list_blockers)  # 开放问题的单行摘要

        # 用统一错误前缀报告 plan 尚未达到可生成状态。
        raise ValueError(
            "> ERR: [Python] explicit codegen plan is not ready for generation: " + str_blocker_summary
    )

# 生成 requirements 阶段结构化载荷，供 staged flow 后续环节复用。
def build_requirements_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """
    生成写入 codegen 前阶段的结构化 requirements 产物。

    :param spec: 当前规格字典。
    :return: 可落盘的 requirements 结构化载荷。
    """

    # 深拷贝用户确认后的 design_requirements，避免后续写回时污染原规格。
    dict_requirements = (
        copy.deepcopy(spec.get("design_requirements", {}))  # 从 spec 深拷贝 design_requirements
        if isinstance(spec.get("design_requirements"), dict)  # 仅在 design_requirements 为字典时保留其内容
        else {}  # 其他类型统一回退为空 requirements 正文
    )  # design_requirements 的隔离副本

    # 汇总接口模板的匹配结果。
    dict_interface_template = _interface_template_summary(spec)  # interface 模板摘要

    # 汇总 use case 模板的匹配结果。
    dict_use_case_template = _use_case_template_summary(spec)  # 给 planning 阶段复用的场景模板快照

    # 汇总 refined template 的匹配结果，避免后续阶段重复扫描模板。
    list_refined_templates = summarize_refined_templates(spec)  # 细化模板摘要列表

    # 只保留 refined template 的模板标识列表，方便后续报告直接消费。
    list_selected_refined_template_ids = [  # 供计划产物和报告消费的 refined template ID 顺序表
        dict_item["template_id"] for dict_item in list_refined_templates  # 提取每个细化模板的稳定标识
    ]

    # 深拷贝接口 profile，确保产物构建不回写原规格。
    dict_interface_profile = (  # requirements 产物里单独保留的接口 profile 快照
        copy.deepcopy(spec.get("interface_profile", {}))  # 复制顶层接口字段，避免下游产物回写 spec
        if isinstance(spec.get("interface_profile"), dict)  # 只有已有结构化 profile 时才保留这些键值
        else {}  # 缺少结构化 profile 时回退为空对象，等待后续默认补齐
    )

    # 预先生成 requirements 摘要，统一给上层产物复用。
    dict_requirements_summary = _requirements_summary(spec)  # 供审阅摘要和 staged workflow 快速引用的概览

    # 返回 codegen 前阶段需要落盘的 requirements 产物。
    return {
        "version": 1,  # requirements 产物版本号
        "name": spec.get("name"),  # 当前规格的模块名
        "target": spec.get("target"),  # 当前规格的目标类型
        "pipeline_required": bool(spec.get("pipeline_required", True)),  # 是否要求流水线实现
        "streamability": spec.get("streamability"),  # 当前规格的流式处理属性
        "interface_family": spec.get("interface_family"),  # 已确认的接口家族
        "interface_profile": dict_interface_profile,  # 规范化后的接口 profile
        "requirements_summary": dict_requirements_summary,  # 供上层快速消费的要求摘要
        "design_requirements": dict_requirements,  # 用户确认后的设计要求正文
        "confirmed_by_user": bool(dict_requirements.get("confirmed_by_user")),  # 用户是否完成确认
        "selected_interface_template_id": dict_interface_template["selected_template_id"],  # 选中的接口模板 ID
        "interface_template": dict_interface_template,  # 接口模板匹配摘要
        "selected_use_case_template_id": dict_use_case_template["id"],  # 选中的 use case 模板 ID
        "use_case_template": dict_use_case_template,  # use case 模板匹配摘要
        "selected_refined_template_ids": list_selected_refined_template_ids,  # 选中的细化模板 ID 列表
        "refined_templates": list_refined_templates,  # 给 codegen 直接复用的细化模板建议全集
    }

# 基于 requirements 产物继续构建 codegen plan，供生成阶段和审阅阶段共用。
def build_codegen_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """
    在正式生成代码前构建确定性的结构化 codegen plan。

    :param spec: 当前规格字典。
    :return: 供 staged workflow 复用的 codegen plan 产物。
    """

    # 先复用 requirements 产物，避免不同阶段重复推导基础结论。
    dict_requirements_payload = build_requirements_payload(spec)  # codegen plan 继续复用的 requirements 产物

    # 汇总当前仍待补齐的开放问题。
    list_open_questions = _codegen_open_questions(spec)  # 影响 codegen readiness 的开放问题

    # 只要没有开放问题，就认为 plan 已达到 ready_for_generation。
    bool_ready_for_generation = not list_open_questions  # 只有没有待确认问题时才允许直接进入生成阶段

    # 复用 requirements 阶段已经选中的接口模板摘要。
    dict_interface_template = dict_requirements_payload["interface_template"]  # 接口决策区继续复用的模板选择结果

    # 复用 requirements 阶段已经锁定的场景模板，避免 plan 和 requirements 各自走出不同结论。
    dict_use_case_template = dict_requirements_payload["use_case_template"]  # 场景策略区继续复用的 use case 选择结果

    # 复用 requirements 阶段已经整理好的 refined template 列表。
    list_refined_templates = dict_requirements_payload["refined_templates"]  # 后续提示词和审阅都要复用的细化模板集合

    # 单独保留 refined template ID 列表，方便计划产物消费。
    list_selected_refined_template_ids = [  # codegen plan 中单独保留的细化模板 ID 顺序表
        dict_item["template_id"] for dict_item in list_refined_templates  # 提取给 plan 落盘的模板标识顺序
    ]  # 细化模板 ID 列表

    # 深拷贝接口 profile，避免覆盖原规格。
    dict_interface_profile = (
        copy.deepcopy(spec.get("interface_profile", {}))  # 复制顶层接口字段给 plan 独立消费
        if isinstance(spec.get("interface_profile"), dict)  # 只有结构化 profile 才允许原样带入 plan
        else {}  # 缺少结构化 profile 时给 plan 留空，避免误带非法值
    )  # codegen plan 使用的 interface_profile 快照

    # 先把 design_requirements 规范成字典，避免 plan 组装阶段重复做类型保护。
    dict_design_requirements = (  # 保证 confirmed_by_user 读取时拿到稳定字典
        spec.get("design_requirements")  # 保留规格中原始的 requirements 对象
        if isinstance(spec.get("design_requirements"), dict)  # 只有字典形态才允许继续读取确认字段
        else {}  # 非字典 design_requirements 统一回退为空字典
    )

    # 汇总接口层面的关键决策。
    dict_interface_decision = {  # codegen 需要保留的接口层决策快照
        "family": spec.get("interface_family"),  # 当前 plan 锁定的接口家族
        "profile": dict_interface_profile,  # 供接口提示词直接消费的 profile 快照
        "confirmed": bool(dict_design_requirements.get("confirmed_by_user")),  # requirements 是否已被用户确认
        "selected_interface_template_id": dict_interface_template["selected_template_id"],  # 已选接口模板 ID
        "template_selection_reason": dict_interface_template["selection_reason"],  # 模板选择原因
        "template_path": dict_interface_template["path"],  # 已选模板来源路径
        "port_naming_policy": dict_interface_template["port_naming_policy"],  # 端口命名约束
    }

    # 汇总流水线策略，明确默认行为。
    dict_pipeline_strategy = {  # 把 pipeline_required 映射成生成阶段可消费的策略对象
        "required": bool(spec.get("pipeline_required", True)),  # 是否强制采用流水实现
        "strategy": (  # 流水约束对应的策略标签
            "pipeline_required"  # 规格仍要求流水实现时写入 required 标签
            if spec.get("pipeline_required", True)  # 规格顶层仍要求流水线
            else "pipeline_optional"  # 用户显式放松流水约束时切到 optional 标签
        ),  # 供提示词直接引用的流水策略标签
        "notes": "Use a pipelined implementation unless the user explicitly disables it.",  # 生成阶段遵循的默认实现说明
    }

    # 汇总模块拆分策略，优先沿用规范化后的子功能计划。
    dict_module_partition = {  # 记录顶层模块与规范化子功能之间的拆分关系
        "top": spec.get("name"),  # 顶层模块名
        "subfunctions": [  # 当前 plan 中保留的子功能名称列表
            dict_item.get("name")  # 单个子功能名称
            for dict_item in spec.get("subfunctions", [])  # 规格里声明的全部子功能条目
            if isinstance(dict_item, dict)  # 只保留结构化子功能条目
        ]
        or [spec.get("name")],  # 子功能缺失时退回单模块实现
        "decomposition_strategy": (  # 代码生成阶段沿用的模块拆分原则
            "follow the normalized subfunction plan and keep interface boundaries explicit"  # 按子功能计划拆分并保留接口边界
        ),  # 代码生成阶段遵循的拆分原则
    }

    # 汇总位宽策略，明确参考模型与参数化宽度的保留原则。
    dict_signal_width_strategy = {  # 给 codegen 传递位宽推导和 RTL 风格约束
        "policy": "infer from the reference model range and preserve parameterized widths where practical",  # 位宽推导策略说明
        "rtl_style_profile": spec.get("rtl_style_profile"),  # 影响位宽写法的 RTL 风格配置
    }

    # 汇总时钟与复位策略，供后续代码生成阶段直接消费。
    dict_reset_clock_strategy = {  # 隔离保存 clock/reset 配置，供后续生成时直接消费
        "clock": (  # 供 codegen 直接读取的时钟配置快照
            copy.deepcopy(spec.get("clock", {}))  # 复制时钟配置，避免后续 stage 就地修改 spec
            if isinstance(spec.get("clock"), dict)  # 只保留结构化时钟配置
            else {}  # 缺少合法时钟对象时回退为空配置
        ),
        "reset": (  # 供 codegen 直接读取的复位配置快照
            copy.deepcopy(spec.get("reset", {}))  # 复制复位配置，避免后续 stage 污染原 spec
            if isinstance(spec.get("reset"), dict)  # 只保留结构化复位配置
            else {}  # 缺少合法复位对象时回退为空配置
        ),
    }

    # 汇总验证策略，保持 staged workflow 的最低验证要求。
    dict_verification_strategy = {  # staged workflow 对验证资产的最低交付要求
        "python_reference_required": True,  # 必须保留 Python reference 作为行为基线
        "self_checking_testbench_required": True,  # 必须生成自检式 testbench
        "readiness_target": "static",  # 当前 readiness 收敛到静态可验证阶段
        "checkpoint_driven_validation": True,  # 通过 checkpoint 清单驱动后续验证
    }

    # 组装默认 codegen plan 主体。
    dict_plan = {  # codegen 阶段直接消费的完整计划骨架
        "version": 1,  # codegen plan 结构版本号
        "name": spec.get("name"),  # 当前规格对应的顶层模块名
        "target": spec.get("target"),  # 当前工作流面向的目标类型
        "requirements_summary": dict_requirements_payload["requirements_summary"],  # requirements 阶段的压缩摘要
        "selected_use_case_template_id": dict_use_case_template["id"],  # 选中的 use case 模板标识
        "use_case_template": dict_use_case_template,  # use case 模板摘要正文
        "selected_refined_template_ids": list_selected_refined_template_ids,  # 命中的细化模板 ID 列表
        "refined_templates": list_refined_templates,  # 命中的细化模板摘要集合
        "interface_decision": dict_interface_decision,  # 接口层关键决策快照
        "pipeline_strategy": dict_pipeline_strategy,  # 流水线与时序策略摘要
        "module_partition": dict_module_partition,  # 模块拆分与子功能布局
        "subfunction_dependency_graph": _subfunction_dependency_graph(spec),  # 子功能依赖关系图
        "signal_width_strategy": dict_signal_width_strategy,  # 位宽推导与参数化策略
        "reset_clock_strategy": dict_reset_clock_strategy,  # 时钟与复位策略摘要
        "verification_strategy": dict_verification_strategy,  # 验证资产交付要求
        "critical_behavior_checkpoints": _critical_behavior_checkpoints(spec),  # 行为关键检查点列表
        "semantic_checkpoints": _semantic_checkpoints(spec),  # 语义一致性检查点列表
        "syntax_risk_checks": _syntax_risk_checks(spec),  # 语法风险排查项列表
        "open_questions": list_open_questions,  # 当前仍未闭合的开放问题
        "ready_for_generation": bool_ready_for_generation,  # 是否允许进入代码生成阶段
    }

    # 只在 workflow 已经是结构化对象时读取生成阶段覆写配置。
    dict_workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}  # codegen plan 覆写来源

    # 仅在 workflow 为字典时读取 codegen_plan_override。
    dict_override = (  # workflow 中附带的 codegen_plan_override 显式覆写块
        dict_workflow.get("codegen_plan_override")  # workflow 字典中显式给出的 override
        if dict_workflow  # 只有 workflow 含有效键值时才继续读取 override
        else None  # 其余类型统一视作没有 override
    )

    # 应用显式 override，并补回未覆写的关键字段。
    if isinstance(dict_override, dict):

        # 深拷贝后合并 override，避免调用方共享引用。
        dict_plan.update(copy.deepcopy(dict_override))

        # override 未提供 open_questions 时仍保留默认推导结果。
        if "open_questions" not in dict_override:

            # 维持默认开放问题列表，避免 override 静默清空阻断项。
            dict_plan["open_questions"] = list_open_questions  # 恢复默认推导出的开放问题列表

        # override 未提供 readiness 时，根据 open_questions 自动回推。
        if "ready_for_generation" not in dict_override:

            # 当 override 未声明 readiness 时，沿用默认开放问题推导结果。
            dict_plan["ready_for_generation"] = not dict_plan.get("open_questions")  # 用 open_questions 自动回推 readiness

    # 返回最终 codegen plan。
    return dict_plan

# 汇总跨阶段复用的 requirements 关键摘要，减少上层重复解包 spec。
def _requirements_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """
    生成供 requirements 与 plan 共用的紧凑摘要。

    :param spec: 当前规格字典。
    :return: 关键需求字段的结构化摘要。
    """

    # 提取接口模板选择结论，用于说明接口约束来源。
    dict_interface_template = _interface_template_summary(spec)  # 接口模板选择摘要

    # 提取 use case 模板选择结论，用于说明场景约束来源。
    dict_use_case_template = _use_case_template_summary(spec)  # use case 模板选择摘要

    # 收集 refined template 命中结果，供摘要和报告复用。
    list_refined_templates = summarize_refined_templates(spec)  # 命中的 refined template 摘要列表

    # 单独保留 refined template ID 列表，方便摘要消费者快速比较。
    list_selected_refined_template_ids = [  # 供调用方比对模板选择稳定性的 ID 列表
        dict_item["template_id"] for dict_item in list_refined_templates  # 提取每个 refined template 的唯一标识
    ]

    # 摘要阶段只关心确认备注，所以这里把 requirements 缩成最小可读字典。
    dict_design_requirements = (  # 保证 confirmation_notes 读取时始终面对字典
        spec.get("design_requirements")  # 保留规格里原始的 requirements 对象
        if isinstance(spec.get("design_requirements"), dict)  # 只有字典形态才可能携带确认备注
        else {}  # requirements 缺失时按没有确认备注处理
    )

    # 返回供多阶段共用的 requirements 摘要。
    return {
        "target": spec.get("target"),
        "rtl_dialect": spec.get("rtl_dialect"),
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "interface_family": spec.get("interface_family"),
        "selected_interface_template_id": dict_interface_template["selected_template_id"],
        "selected_use_case_template_id": dict_use_case_template["id"],
        "selected_refined_template_ids": list_selected_refined_template_ids,
        "confirmation_notes": dict_design_requirements.get("confirmation_notes", ""),
    }

# 汇总 interface template 的匹配结果，给 requirements 和 plan 提供同一份摘要。
def _interface_template_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """
    汇总接口模板的选择结果，供 requirements 与 plan 产物复用。

    :param spec: 当前规格字典。
    :return: 接口模板选择摘要。
    """

    # 先尝试根据当前规格选择匹配的接口模板。
    try:

        # 读取当前规格命中的接口模板对象，后续会把它压缩成稳定摘要。
        dict_selected_template = select_interface_template(spec)  # 已选中的接口模板对象

    # 模板选择失败时，将异常转成结构化摘要返回给上层。
    except InterfaceTemplateError as exc:

        # 返回模板选择失败时的兜底摘要，保证上层仍能看到失败原因和默认策略。
        return {
            "selected_template_id": None,  # 未能选择到接口模板
            "selection_reason": str(exc),  # 选择失败的具体原因
            "path": None,  # 失败时没有模板路径
            "port_naming_policy": "strict_preferred",  # 默认仍偏好严格命名策略
        }

    # 当前接口家族不需要本地标准模板时返回 not_applicable 摘要。
    if not dict_selected_template:

        # 返回不需要标准接口模板时的摘要，保留 not_applicable 命名策略结论。
        return {
            "selected_template_id": None,  # 当前场景没有选中接口模板
            "selection_reason": "no standard local interface template is required for this interface family",  # 不需要模板的原因
            "path": None,  # 没有对应模板路径
            "port_naming_policy": "not_applicable",  # 当前场景不适用命名策略
        }

    # 当前接口家族命中标准模板时，返回模板身份信息与命名约束摘要。
    return {
        "selected_template_id": dict_selected_template["template_id"],  # 命中的标准接口模板标识
        "selection_reason": dict_selected_template["selection_reason"],  # 本次模板命中的归因说明
        "path": str(dict_selected_template["path"]),  # 模板文件路径
        "port_naming_policy": dict_selected_template["strict_naming_policy"],  # 端口严格命名策略
    }

# 汇总 use case 模板的选择结果，供 requirements 产物写回。
def _use_case_template_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """
    汇总 use case 模板的选择结果。

    :param spec: 待分析的规格字典。
    :return: use case 模板选择摘要。
    """

    # 先选择最匹配的 use case 模板。
    dict_selected_template = select_use_case_template(spec)  # 已选择的 use case 模板

    # 返回 use case 模板的结构化摘要。
    return summarize_use_case_template(dict_selected_template)

# 根据接口家族补齐 interface_profile 缺省字段。
def _apply_interface_defaults(interface_family: str, profile: dict[str, Any]) -> dict[str, Any]:
    """
    根据接口家族补齐 interface_profile 缺省字段。

    :param interface_family: 已确认的接口家族。
    :param profile: 待补齐的接口 profile。
    :return: 补齐默认字段后的 profile 副本。
    """

    # 深拷贝 profile，避免默认值写回调用侧对象。
    dict_payload = copy.deepcopy(profile)  # 补齐中的接口 profile 副本

    # 为 AXI Stream 场景补齐默认时钟复位域。
    if interface_family == "axi_stream":

        # 把 AXI Stream 默认握手时钟域补进 profile，省去下游再次猜测端口名。
        dict_payload.setdefault("clock_reset_domain", {"clock": "i_axis_aclk", "reset": "i_axis_arstn"})

    # 为 AXI4 memory-mapped 场景补齐统一的 ACLK/ARESETN 命名域。
    elif interface_family == "axi4":

        # 把 AXI4 memory-mapped 默认时钟域补进 profile，保持总线命名和模板一致。
        dict_payload.setdefault("clock_reset_domain", {"clock": "i_axi_aclk", "reset": "i_axi_arstn"})

    # 为 AXI4-Lite 场景补齐变体和时钟复位域。
    elif interface_family == "axi4_lite":

        # 保底写入 AXI4-Lite 变体标识。
        dict_payload.setdefault("axi4_variant", "axi4_lite")

        # AXI4-Lite 默认不支持 burst。
        dict_payload.setdefault("burst_support", False)

        # 把 AXI4-Lite 控制总线默认时钟域补进 profile，便于后续模板解析。
        dict_payload.setdefault("clock_reset_domain", {"clock": "i_axi_aclk", "reset": "i_axi_arstn"})

    # 为 AHB 场景写入总线常见的 HCLK/HRSTN 默认时钟域。
    elif interface_family == "ahb":

        # 把 AHB 总线常用的 HCLK/HRSTN 组合补进 profile。
        dict_payload.setdefault("clock_reset_domain", {"clock": "i_ahb_hclk", "reset": "i_ahb_hrstn"})

    # 为 APB 外设场景写入常见的 PCLK/PRSTN 默认时钟域。
    elif interface_family == "apb":

        # 把 APB 外设常用的 PCLK/PRSTN 组合补进 profile。
        dict_payload.setdefault("clock_reset_domain", {"clock": "i_apb_pclk", "reset": "i_apb_prstn"})

    # 返回补齐默认值后的 profile 副本。
    return dict_payload

# 严格校验 AXI Stream interface_profile 的必填字段和值类型。
def _validate_axi_stream_profile(profile: Any) -> None:
    """
    严格校验 AXI Stream interface_profile。

    :param profile: 待校验的接口 profile。
    :return: 无返回值；校验通过时静默返回。
    :raises ValueError: 当 profile 结构或字段不合法时抛出。
    """

    # 仅接受字典形态的 AXI Stream profile。
    if not isinstance(profile, dict):

        # 直接阻断非对象形态的 AXI Stream profile。
        raise ValueError("> ERR: [Python] AXI-Stream interface_profile must be an object.")

    # 声明 AXI Stream 必填的布尔字段集合。
    tuple_required_bool_keys = ("keep_ready", "keep_last")  # AXI Stream 必填布尔字段

    # 逐项校验布尔字段是否存在且类型正确。
    for str_field_name in tuple_required_bool_keys:

        # 当前字段不是布尔值时立即报错。
        if not isinstance(profile.get(str_field_name), bool):

            # 抛出当前缺失或类型错误的布尔字段信息。
            raise ValueError(f"> ERR: [Python] AXI-Stream interface_profile requires boolean `{str_field_name}`.")

    # 校验 data_width 是否为正整数。
    if not isinstance(profile.get("data_width"), int) or int(profile["data_width"]) <= 0:

        # 抛出 data_width 非法的错误消息。
        raise ValueError("> ERR: [Python] AXI-Stream interface_profile requires a positive integer `data_width`.")

# 单独校验 AXI4 profile，确保 memory-mapped 总线字段在进入 codegen 前已经闭合。
def _validate_axi4_profile(profile: Any) -> None:
    """
    严格校验 AXI4 interface_profile。

    :param profile: 待校验的接口 profile。
    :return: 无返回值；校验通过时静默返回。
    :raises ValueError: 当 profile 结构或字段不合法时抛出。
    """

    # AXI4 profile 必须是键值对象，后续校验才有字段可读。
    if not isinstance(profile, dict):

        # 非对象形态无法表达 burst、role 等关键字段，直接阻断。
        raise ValueError("> ERR: [Python] AXI4 interface_profile must be an object.")

    # 校验 AXI4 变体是否在允许集合内。
    if profile.get("axi4_variant") not in AXI4_VARIANTS:

        # 报告 AXI4 变体非法。
        raise ValueError(
            f"> ERR: [Python] AXI4 interface_profile requires `axi4_variant` in {AXI4_VARIANT_LABELS}."
        )

    # 校验 AXI4 主从角色是否在允许集合内。
    if profile.get("role") not in AXI4_ROLES:

        # 报告 AXI4 主从角色非法。
        raise ValueError(
            f"> ERR: [Python] AXI4 interface_profile requires `role` in {AXI4_ROLE_LABELS}."
        )

    # 校验 AXI4 读写模式是否在允许集合内。
    if profile.get("read_write_mode") not in AXI4_MODES:

        # 报告 AXI4 读写模式非法。
        raise ValueError(
            f"> ERR: [Python] AXI4 interface_profile requires `read_write_mode` in {AXI4_MODE_LABELS}."
        )

    # 逐项校验 AXI4 的核心宽度字段。
    for str_field_name in ("data_width", "addr_width"):

        # 当前宽度字段不是正整数时立即报错。
        if not isinstance(profile.get(str_field_name), int) or int(profile[str_field_name]) <= 0:

            # 报告当前宽度字段非法。
            raise ValueError(f"> ERR: [Python] AXI4 interface_profile requires a positive integer `{str_field_name}`.")

    # AXI4 Full 额外要求提供正整数 id_width。
    if profile.get("axi4_variant") == "axi4_full":

        # 当前场景下继续校验 id_width。
        if not isinstance(profile.get("id_width"), int) or int(profile["id_width"]) <= 0:

            # 报告 AXI4 Full 的 id_width 非法。
            raise ValueError("> ERR: [Python] AXI4 full interface_profile requires a positive integer `id_width`.")

    # burst_support 必须是布尔值。
    if not isinstance(profile.get("burst_support"), bool):

        # 报告 burst_support 类型非法。
        raise ValueError("> ERR: [Python] AXI4 interface_profile requires boolean `burst_support`.")

    # 启用 burst 时继续校验 max_burst_len。
    if profile.get("burst_support") and (
        not isinstance(profile.get("max_burst_len"), int) or int(profile["max_burst_len"]) <= 0
    ):

        # 报告 burst 场景下缺失合法 max_burst_len 的阻断错误。
        raise ValueError(
            "> ERR: [Python] AXI4 interface_profile requires positive integer `max_burst_len` "
            "when burst_support=true."
        )

# 单独校验 AXI4-Lite profile，确保控制寄存器类总线字段在进入 codegen 前已经定稿。
def _validate_axi4_lite_profile(profile: Any) -> None:
    """
    严格校验 AXI4-Lite interface_profile。

    :param profile: 待校验的接口 profile。
    :return: 无返回值；校验通过时静默返回。
    :raises ValueError: 当 profile 结构或字段不合法时抛出。
    """

    # AXI4-Lite 校验阶段只接受结构化的 profile 对象。
    if not isinstance(profile, dict):

        # 非对象形态无法承载 AXI4-Lite 的角色和位宽配置，直接阻断。
        raise ValueError("> ERR: [Python] AXI4-Lite interface_profile must be an object.")

    # 校验 AXI4-Lite 主从角色是否合法。
    if profile.get("role") not in AXI4_ROLES:

        # 把 AXI4-Lite 角色约束写成稳定的 staged-flow 报错。
        raise ValueError(
            f"> ERR: [Python] AXI4-Lite interface_profile requires `role` in {AXI4_ROLE_LABELS}."
        )

    # 校验 AXI4-Lite 读写模式是否合法。
    if profile.get("read_write_mode") not in AXI4_MODES:

        # 把 AXI4-Lite 读写模式约束写成稳定的 staged-flow 报错。
        raise ValueError(
            f"> ERR: [Python] AXI4-Lite interface_profile requires `read_write_mode` in {AXI4_MODE_LABELS}."
        )

    # 逐项校验 AXI4-Lite 的宽度字段。
    for str_field_name in ("data_width", "addr_width"):

        # 当前宽度字段缺失或非正整数时，不允许继续进入控制总线模板生成。
        if not isinstance(profile.get(str_field_name), int) or int(profile[str_field_name]) <= 0:

            # 把出错字段名直接带进报错，方便调用方定位缺失项。
            raise ValueError(
                f"> ERR: [Python] AXI4-Lite interface_profile requires a positive integer `{str_field_name}`."
            )

# 严格校验 AHB/APB 这类简化总线 profile 的核心字段。
def _validate_simple_bus_profile(profile: Any, family: str) -> None:
    """
    严格校验 AHB/APB 等简化总线 interface_profile。

    :param profile: 待校验的接口 profile。
    :param family: 当前总线家族名称。
    :return: 无返回值；校验通过时静默返回。
    :raises ValueError: 当 profile 结构或字段不合法时抛出。
    """

    # 预先计算当前总线家族的大写展示名。
    str_family_label = family.upper()  # 大写总线家族名称

    # 仅接受字典形态的总线 profile。
    if not isinstance(profile, dict):

        # 抛出 profile 结构非法的错误消息。
        raise ValueError("> ERR: [Python] interface_profile must be an object.")

    # 校验总线角色是否合法。
    if profile.get("role") not in AXI4_ROLES:

        # 报告总线角色非法。
        raise ValueError("> ERR: [Python] interface_profile requires a valid role.")

    # 逐项校验总线的宽度字段。
    for str_field_name in ("data_width", "addr_width"):

        # 简化总线的位宽字段一旦不是正整数，就无法可靠生成地址和数据通道。
        if not isinstance(profile.get(str_field_name), int) or int(profile[str_field_name]) <= 0:

            # 统一返回简化总线宽度配置缺失的阻断消息。
            raise ValueError("> ERR: [Python] interface_profile requires positive integer widths.")

# 汇总 requirements 确认阶段尚未满足的约束项。
def _requirement_confirmation_issues(
    spec: dict[str, Any],
    *,
    require_confirmed: bool,
) -> list[str]:
    """
    汇总 requirements 确认阶段尚未满足的约束项。

    :param spec: 待分析的规格字典。
    :param require_confirmed: 是否要求 confirmed_by_user 已显式为真。
    :return: 尚未满足的约束文本列表。
    """

    # 只有结构化 requirements 才能继续承载 target 与 confirmed 等合同字段。
    dict_requirements = spec.get("design_requirements") if isinstance(spec.get("design_requirements"), dict) else None  # 合同校验使用的 requirements 字典或空值

    # design_requirements 不是字典时直接返回缺失问题。
    if dict_requirements is None:

        # 生成路径要求看到完整 requirements 对象，非生成路径则允许静默跳过。
        return ["Generation calls require a `design_requirements` object."] if require_confirmed else []

    # 初始化问题收集列表。
    list_issues: list[str] = []  # requirements 确认问题列表

    # design_requirements.target 必须与规格顶层 target 保持一致。
    if dict_requirements.get("target") != spec.get("target"):

        # 记录 target 不一致问题。
        list_issues.append("design_requirements.target must match spec.target.")

    # 进入生成阶段时要求 confirmed_by_user 已被显式确认。
    if require_confirmed and not dict_requirements.get("confirmed_by_user"):

        # 记录缺少用户确认的问题。
        list_issues.append("Generation calls require design_requirements.confirmed_by_user=true.")

    # 继续汇总 requirements 合同层面的约束问题。
    list_issues.extend(
        _requirement_contract_issues(
            spec,
            dict_requirements,
            strict_profile_validation=require_confirmed,
        )
    )

    # 返回汇总后的问题列表。
    return list_issues

# 汇总 requirements 合同层面的结构与语义问题。
def _requirement_contract_issues(
    spec: dict[str, Any],
    requirements: dict[str, Any],
    *,
    strict_profile_validation: bool,
) -> list[str]:
    """
    汇总 requirements 合同层面的结构与语义问题。

    :param spec: 顶层规格字典。
    :param requirements: 已存在的 design_requirements 字典。
    :param strict_profile_validation: 是否启用严格 profile 校验。
    :return: 合同层面尚未满足的问题列表。
    """

    # 初始化合同问题列表。
    list_issues: list[str] = []  # requirements 合同问题列表

    # 读取 staged flow 是否显式启用了 codegen plan。
    bool_codegen_plan_enabled = bool(spec.get("codegen_plan_required"))  # 是否满足显式开启 codegen plan 的要求

    # staged flow 要求 codegen_plan_required 已显式启用。
    if not bool_codegen_plan_enabled:

        # 记录 staged flow 必须启用 codegen plan 的约束。
        list_issues.append("This v1 staged flow requires codegen_plan_required=true.")

    # requirements 中的 pipeline_required 需要先满足基础类型约束。
    if not isinstance(requirements.get("pipeline_required"), bool):

        # 标记 pipeline_required 类型非法。
        list_issues.append("design_requirements.pipeline_required must be a boolean.")

    # pipeline_required 类型正确时继续校验与规格顶层是否一致。
    elif bool(requirements["pipeline_required"]) != bool(spec.get("pipeline_required", True)):

        # 标记 pipeline_required 与规格顶层不一致。
        list_issues.append("design_requirements.pipeline_required must match spec.pipeline_required.")

    # 读取 requirements 中确认过的流式能力。
    str_requirement_streamability = str(requirements.get("streamability") or "")  # design_requirements.streamability 的当前值

    # streamability 必须落在允许枚举里。
    if str_requirement_streamability not in STREAMABILITY_VALUES:

        # 标记 streamability 取值非法。
        list_issues.append(f"streamability must be one of {', '.join(STREAMABILITY_VALUES)}.")

    # 合法的流式能力还必须与规格顶层保持同步。
    elif str_requirement_streamability != str(spec.get("streamability") or ""):

        # design_requirements 一旦确认了流式能力，就必须与规格顶层保持同一结论。
        list_issues.append("design_requirements.streamability must match spec.streamability.")

    # 单独提取已经确认的接口家族，供后续枚举和一致性校验复用。
    str_requirement_interface_family = str(requirements.get("interface_family") or "")  # requirements 中待校验的接口家族文本

    # interface_family 非空时必须落在允许枚举里。
    if requirements.get("interface_family") is not None and str_requirement_interface_family not in INTERFACE_FAMILIES:

        # requirements 里的接口家族必须仍然落在仓库约定的允许枚举中。
        list_issues.append(f"interface_family must be one of {', '.join(INTERFACE_FAMILIES)}.")

    # requirements 与规格顶层必须引用同一个接口家族结论。
    elif str_requirement_interface_family != str(spec.get("interface_family") or ""):

        # requirements 一旦锁定接口家族，就必须与规格顶层保留同一结论。
        list_issues.append("design_requirements.interface_family must match spec.interface_family.")

    # 提取当前 requirements 下记录的接口 profile 快照。
    dict_requirement_profile = (
        requirements.get("interface_profile")  # 保留 requirements 里原始的 interface_profile 对象
        if isinstance(requirements.get("interface_profile"), dict)  # 只有字典形态才允许继续做结构一致性检查
        else None  # 其余类型统一交给结构错误分支处理
    )  # 规范化后的 requirements interface_profile

    # interface_profile 必须是字典对象。
    if dict_requirement_profile is None:

        # 标记 interface_profile 结构非法。
        list_issues.append("design_requirements.interface_profile must be an object.")

        # interface_profile 结构非法时无需继续深挖后续字段。
        return list_issues

    # 接口 profile 的内容也必须与规格顶层完全一致。
    if dict_requirement_profile != spec.get("interface_profile", {}):

        # requirements 中保存的接口 profile 快照必须与规格顶层保持完全一致。
        list_issues.append("design_requirements.interface_profile must match spec.interface_profile.")

    # 流式任务在生成前必须锁定明确的接口家族选择。
    if str_requirement_streamability == "streamable" and not str_requirement_interface_family:

        # 流式任务没有接口家族结论时，生成阶段无法继续选择总线模板。
        list_issues.append("Streamable tasks require an explicit interface_family confirmation before generation.")

    # 汇总接口家族与 profile 语义层面的约束问题。
    list_issues.extend(
            _interface_family_semantic_issues(
            str_requirement_interface_family,
            dict_requirement_profile,
            strict_profile_validation,
        )
    )

    # 汇总时钟复位域与端口声明之间的一致性问题。
    list_issues.extend(_clock_reset_domain_issues(spec))

    # 返回汇总后的合同问题列表。
    return list_issues

# 汇总接口家族与 interface_profile 之间的语义一致性问题。
def _interface_family_semantic_issues(
    interface_family: Any,
    profile: dict[str, Any],
    strict_profile_validation: bool,
) -> list[str]:
    """
    汇总接口家族与 interface_profile 之间的语义一致性问题。

    :param interface_family: 已确认的接口家族。
    :param profile: 与接口家族关联的 interface_profile。
    :param strict_profile_validation: 是否启用严格 profile 校验。
    :return: 接口家族语义问题列表。
    """

    # 初始化接口语义问题列表。
    list_issues: list[str] = []  # 接口语义问题列表

    # 自定义接口必须保留可执行的 profile 细节。
    if interface_family == "custom" and not profile:

        # 标记自定义接口缺少 profile 细节。
        list_issues.append("Custom interfaces require a non-empty interface_profile.")

    # native 接口不允许混入 AXI 系列专有 profile 字段。
    if interface_family == "native":

        # 找出当前 profile 中不该出现的 AXI 专有字段。
        list_forbidden_keys = sorted(str_key for str_key in profile if str_key in NATIVE_FORBIDDEN_PROFILE_KEYS)  # native 禁用字段

        # 一旦发现禁用字段就把完整字段名集合直接报告出去。
        if list_forbidden_keys:

            # 把 native 接口的越界字段名拼成一条集中报错。
            list_issues.append(
                "Native interfaces must not use AXI-specific interface_profile keys: "
                + ", ".join(list_forbidden_keys)
                + "."
            )

    # 严格模式下先校验 AXI Stream profile 自身是否完整。
    if strict_profile_validation and interface_family == "axi_stream":

        # 用 try/except 把底层校验失败转成问题条目。
        try:

            # 执行 AXI Stream profile 的必填字段与类型校验。
            _validate_axi_stream_profile(profile)

        # 直接透传 AXI Stream 校验器抛出的原始错误消息。
        except ValueError as exc:

            # AXI Stream profile 一旦缺字段或字段类型不合法，就把原始阻断原因直接带回上层。
            list_issues.append(str(exc))

    # 严格模式下继续校验 AXI4 profile 的结构完整性。
    if strict_profile_validation and interface_family == "axi4":

        # 用 try/except 把 AXI4 校验异常折叠成问题条目。
        try:

            # 执行 AXI4 profile 的必填字段与组合约束校验。
            _validate_axi4_profile(profile)

        # 原样保留 AXI4 校验器产生的异常文本。
        except ValueError as exc:

            # AXI4 profile 若缺少关键字段或字段组合冲突，直接透传底层阻断原因。
            list_issues.append(str(exc))

    # 严格模式下继续校验 AXI4-Lite profile 的关键字段。
    if strict_profile_validation and interface_family == "axi4_lite":

        # 用 try/except 承接 AXI4-Lite 校验异常。
        try:

            # 逐项核对 AXI4-Lite profile 是否补齐了角色、位宽等生成必需字段。
            _validate_axi4_lite_profile(profile)

        # 原样保留 AXI4-Lite 校验器返回的异常文本。
        except ValueError as exc:

            # AXI4-Lite profile 若缺少角色或位宽约束，直接透传底层阻断原因。
            list_issues.append(str(exc))

    # 严格模式下对 AHB/APB 这类简化总线做字段校验。
    if strict_profile_validation and interface_family in {"ahb", "apb"}:

        # 用 try/except 把简化总线校验异常转成问题条目。
        try:

            # 执行 AHB/APB profile 的角色与位宽校验。
            _validate_simple_bus_profile(profile, str(interface_family))

        # 原样保留简化总线校验器生成的异常文本。
        except ValueError as exc:

            # 简化总线 profile 一旦缺角色或位宽，就把原始阻断原因直接带回上层。
            list_issues.append(str(exc))

    # 严格模式下还要验证接口家族能否映射到合法模板。
    if strict_profile_validation and interface_family in {"axi_stream", "axi4", "axi4_lite", "ahb", "apb"}:

        # 用 try/except 捕获模板解析阶段的配置错误。
        try:

            # 执行接口模板解析，确认当前 profile 可以落到标准模板。
            resolve_interface_template(str(interface_family), profile)

        # 透传模板解析阶段返回的异常文本。
        except InterfaceTemplateError as exc:

            # 模板解析阶段如果无法映射标准接口模板，就把解析错误原样透传给上层。
            list_issues.append(str(exc))

    # 返回汇总后的接口语义问题列表。
    return list_issues

# 汇总 interface_profile.clock_reset_domain 与端口声明之间的一致性问题。
def _clock_reset_domain_issues(spec: dict[str, Any]) -> list[str]:
    """
    汇总 interface_profile.clock_reset_domain 与端口声明之间的一致性问题。

    :param spec: 待分析的规格字典。
    :return: 时钟复位域相关问题列表。
    """

    # 仅 RTL 目标需要校验 clock_reset_domain。
    if spec.get("target") != "rtl":

        # 非 RTL 目标直接返回空列表。
        return []

    # 读取规格顶层的 interface_profile。
    obj_interface_profile = spec.get("interface_profile", {})  # 顶层 interface_profile 配置

    # interface_profile 不是字典时无法继续做结构校验。
    if not isinstance(obj_interface_profile, dict):

        # 结构不合法时把问题留给其他校验路径处理。
        return []

    # 先读取 interface_profile 中记录的时钟复位域原值，后续再按真实类型决定是否继续深挖。
    raw_clock_reset_domain = obj_interface_profile.get("clock_reset_domain")  # profile 中声明的时钟复位域原值

    # 未声明 clock_reset_domain 时无需继续校验。
    if raw_clock_reset_domain in (None, ""):

        # 没有显式时钟复位域时静默返回。
        return []

    # clock_reset_domain 存在时必须是字典对象。
    if not isinstance(raw_clock_reset_domain, dict):

        # 返回结构非法的问题条目。
        return ["interface_profile.clock_reset_domain must be an object when provided."]

    # 结构确认后，把时钟复位域对象收敛成可安全读取字段的字典。
    dict_clock_reset_domain = raw_clock_reset_domain  # 已通过结构检查的时钟复位域配置

    # 初始化时钟复位域问题列表。
    list_issues: list[str] = []  # 时钟复位域问题列表

    # 只保留能提供 ports 列表的 interfaces 对象，再据此建立端口索引。
    dict_ports_by_name = _interface_ports_by_name(spec)  # 时钟复位域校验复用的端口索引

    # 逐项校验 clock/reset 两个时钟域字段。
    for str_field_name in ("clock", "reset"):

        # 把当前 clock/reset 字段对应的端口问题并入总问题列表。
        list_issues.extend(
            _clock_reset_domain_field_issues(
                str_field_name,
                dict_clock_reset_domain,
                dict_ports_by_name,
            )
        )

    # 把与规格顶层 clock/reset 名称不一致的问题追加到结果集合。
    list_issues.extend(_clock_reset_domain_alignment_issues(spec, dict_clock_reset_domain))

    # 返回汇总后的时钟复位域问题列表。
    return list_issues

# 建立 interface.ports 的具名端口索引。
def _interface_ports_by_name(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    建立 interface.ports 的具名端口索引。

    :param spec: 待分析的规格字典。
    :return: 端口名到端口定义的映射。
    """

    # 先把 interfaces 原值收敛成可安全读取的字典，再统一构造具名端口索引。
    dict_interfaces = spec.get("interfaces", {}) if isinstance(spec.get("interfaces", {}), dict) else {}  # 时钟复位端口索引使用的接口字典

    # 返回以端口名为键的端口索引。
    return {
        str(dict_port.get("name")): dict_port  # 当前端口名到完整端口定义的映射项
        for dict_port in dict_interfaces.get("ports", [])  # 从接口端口列表里逐个抽取具名端口
        if isinstance(dict_port, dict) and dict_port.get("name")  # 只保留带 name 的合法端口定义
    }

# 汇总单个 clock/reset 字段的端口一致性问题。
def _clock_reset_domain_field_issues(
    str_field_name: str,
    dict_clock_reset_domain: dict[str, Any],
    dict_ports_by_name: dict[str, dict[str, Any]],
) -> list[str]:
    """
    汇总单个 clock/reset 字段的端口一致性问题。

    :param str_field_name: 当前正在校验的 clock/reset 字段名。
    :param dict_clock_reset_domain: 已通过结构检查的时钟复位域配置。
    :param dict_ports_by_name: 端口名到端口定义的映射。
    :return: 当前字段对应的问题列表。
    """

    # 读取当前字段声明的信号名。
    str_signal_name = str(dict_clock_reset_domain.get(str_field_name) or "")  # 当前 clock/reset 信号名

    # 当前字段为空时直接返回缺失问题。
    if not str_signal_name:

        # 标记 clock_reset_domain 当前字段为空。
        return [f"interface_profile.clock_reset_domain.{str_field_name} must not be empty."]

    # 查找与当前信号名匹配的端口定义。
    dict_port = dict_ports_by_name.get(str_signal_name)  # 当前信号名对应的端口定义

    # 端口不存在时直接返回缺口问题。
    if not dict_port:

        # 返回当前信号未出现在 interfaces.ports 中的问题。
        return [
            (
                f"interface_profile.clock_reset_domain.{str_field_name}="  # 当前字段前缀
                f"{str_signal_name!r} must exist in interfaces.ports."  # 缺失端口时的约束正文
            )
        ]

    # 初始化当前字段的问题列表。
    list_issues: list[str] = []  # 当前 clock/reset 字段对应的问题列表

    # 端口方向必须是 input。
    if str(dict_port.get("direction") or "").lower() != "input":

        # 标记当前时钟或复位端口方向非法。
        list_issues.append(
            f"interface_profile.clock_reset_domain.{str_field_name}={str_signal_name!r} must be an input port."
        )

    # 时钟与复位端口都要求位宽为 1。
    if _normalized_port_width(dict_port) != 1:

        # 标记当前时钟或复位端口位宽非法。
        list_issues.append(
            f"interface_profile.clock_reset_domain.{str_field_name}={str_signal_name!r} must have width 1."
        )

    # 返回当前字段汇总后的问题列表。
    return list_issues

# 规整端口位宽，解析失败时返回非法哨兵值。
def _normalized_port_width(dict_port: dict[str, Any]) -> int:
    """
    规整端口位宽，解析失败时返回非法哨兵值。

    :param dict_port: 单个端口定义字典。
    :return: 可比较的整数位宽；解析失败时返回 -1。
    """

    # 尝试把端口宽度字段转换成后续校验可比较的整数。
    try:

        # 返回成功解析后的整数位宽。
        return int(dict_port.get("width", 1) or 1)

    # 位宽无法解析时回退成非法哨兵值。
    except (TypeError, ValueError):

        # 返回宽度解析失败时统一使用的非法哨兵值。
        return -1

# 汇总 clock_reset_domain 与规格顶层 clock/reset 命名的对齐问题。
def _clock_reset_domain_alignment_issues(
    spec: dict[str, Any],
    dict_clock_reset_domain: dict[str, Any],
) -> list[str]:
    """
    汇总 clock_reset_domain 与规格顶层 clock/reset 命名的对齐问题。

    :param spec: 待分析的规格字典。
    :param dict_clock_reset_domain: 已通过结构检查的时钟复位域配置。
    :return: 命名对齐问题列表。
    """

    # 初始化命名对齐问题列表。
    list_issues: list[str] = []  # 顶层时钟复位命名对齐问题列表

    # 读取规格顶层声明的 clock 名称。
    str_declared_clock = str((spec.get("clock") or {}).get("name") or "")  # 规格顶层 clock 名称

    # 读取规格顶层声明的复位信号名，用来确认 profile 没有偷偷改指向别的复位线。
    str_declared_reset = str((spec.get("reset") or {}).get("name") or "")  # 顶层锁定的基准复位信号名

    # clock_reset_domain.clock 已声明时要求与 spec.clock.name 一致。
    if (
        str_declared_clock
        and str(dict_clock_reset_domain.get("clock") or "")
        and str_declared_clock != str(dict_clock_reset_domain["clock"])
    ):

        # profile 一旦手工指定 clock 字段，就不能偏离规格顶层已经确认的工作时钟名。
        list_issues.append("interface_profile.clock_reset_domain.clock must match spec.clock.name.")

    # clock_reset_domain.reset 已声明时要求继续锁定到规格顶层同名复位端口。
    if (
        str_declared_reset
        and str(dict_clock_reset_domain.get("reset") or "")
        and str_declared_reset != str(dict_clock_reset_domain["reset"])
    ):

        # profile 中显式绑定 reset 字段时，也必须回指规格顶层已经确认的复位名。
        list_issues.append("interface_profile.clock_reset_domain.reset must match spec.reset.name.")

    # 输出汇总后的顶层时钟复位命名对齐问题列表。
    return list_issues

# 汇总进入代码生成前仍需向用户确认的开放问题。
def _codegen_open_questions(spec: dict[str, Any]) -> list[str]:
    """
    汇总进入代码生成前仍需向用户确认的开放问题。

    :param spec: 待分析的规格字典。
    :return: 进入生成前仍需确认的问题列表。
    """

    # 初始化开放问题列表。
    list_questions: list[str] = []  # 代码生成前的开放问题列表

    # 开放问题阶段只需要 confirmed 状态，因此这里只保留可安全读取的 requirements 字典。
    bool_has_structured_requirements = isinstance(spec.get("design_requirements", {}), dict)  # requirements 是否可直接读取 confirmed 等字段

    # 根据结构化判定结果生成开放问题阶段可读取的 requirements 快照。
    dict_requirements = spec.get("design_requirements", {}) if bool_has_structured_requirements else {}  # 开放问题判定使用的 requirements 快照

    # 读取当前规格声明的接口家族，供后续多个分支复用。
    str_interface_family = str(spec.get("interface_family") or "")  # 当前规格绑定的接口家族名

    # 提前取出 interface_profile，避免后面每个接口分支都重复读取同一份配置。
    obj_interface_profile = spec.get("interface_profile", {})  # 规格里的原始 interface_profile 对象

    # 只在 interface_profile 真正是字典时保留其键值，其余类型统一视作未填写。
    dict_interface_profile = (
        obj_interface_profile if isinstance(obj_interface_profile, dict) else {}  # 可安全读取接口字段的 profile 字典
    )

    # 非 Verilog 的 RTL 目标在生成前需要再次确认。
    if spec.get("target") == "rtl" and spec.get("rtl_dialect") != "verilog":

        # 提醒用户确认目标是否真的是 Verilog-2001。
        list_questions.append("Confirm the design is intended for Verilog-2001.")

    # 用户尚未确认 requirements 时必须补问关键约束。
    if not dict_requirements.get("confirmed_by_user"):

        # requirements 尚未确认时，必须把最关键的目标、流水线和接口选择重新摆到用户面前。
        list_questions.append("Confirm the target, pipeline requirement, and interface choice with the user.")

    # 流式任务在未确认接口家族时必须继续追问。
    if spec.get("streamability") == "streamable" and not str_interface_family:

        # 提醒用户在候选接口家族里做出明确选择。
        list_questions.append(
            "Confirm whether the streamable task should use AXI-Stream, AXI4, AXI4-Lite, "
            "AHB, APB, native, or custom interfaces."
        )

    # 按接口家族补充生成前仍需确认的接口字段问题。
    list_questions.extend(_interface_family_open_questions(str_interface_family, dict_interface_profile))

    # 把 requirements 合同问题补充到开放问题列表中。
    for str_issue in _requirement_confirmation_issues(spec, require_confirmed=False):

        # 仅补充当前列表中尚不存在的问题，避免重复输出。
        if str_issue not in list_questions:

            # 把尚未出现的合同问题追加成开放问题。
            list_questions.append(str_issue)

    # 返回汇总后的开放问题列表。
    return list_questions

# 根据接口家族补充生成前仍需确认的字段问题。
def _interface_family_open_questions(
    str_interface_family: str,
    dict_interface_profile: dict[str, Any],
) -> list[str]:
    """
    根据接口家族补充生成前仍需确认的字段问题。

    :param str_interface_family: 当前规格绑定的接口家族名。
    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :return: 当前接口家族对应的开放问题列表。
    """

    # AXI Stream 场景需要补问 ready/last/data_width 三类关键字段。
    if str_interface_family == "axi_stream":

        # 返回 AXI Stream 场景仍需确认的字段问题。
        return _axi_stream_open_questions(dict_interface_profile)

    # AXI4 场景需要补问关键总线配置字段。
    if str_interface_family == "axi4":

        # 把 AXI4 helper 产出的字段追问原样返回给上层。
        return _axi4_open_questions(dict_interface_profile)

    # AXI4-Lite 场景需要补问角色、模式和位宽字段。
    if str_interface_family == "axi4_lite":

        # 直接返回 AXI4-Lite 模板定稿前缺失的最小配置字段问题。
        return _family_field_open_questions(
            dict_interface_profile,
            ("role", "read_write_mode", "data_width", "addr_width"),
            "Confirm the AXI4-Lite configuration field `{field}`.",
        )

    # AHB/APB 场景需要补问角色和宽度字段。
    if str_interface_family in {"ahb", "apb"}:

        # 直接返回 AHB/APB 模板最依赖的角色与总线宽度缺口问题。
        return _family_field_open_questions(
            dict_interface_profile,
            ("role", "data_width", "addr_width"),
            f"Confirm the {str_interface_family.upper()} configuration field `{{field}}`.",
        )

    # 没有匹配接口家族时返回空问题列表。
    return []

# 汇总 AXI Stream 场景仍需确认的关键字段问题。
def _axi_stream_open_questions(dict_interface_profile: dict[str, Any]) -> list[str]:
    """
    汇总 AXI Stream 场景仍需确认的关键字段问题。

    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :return: AXI Stream 场景的开放问题列表。
    """

    # 返回 AXI Stream 握手与数据位宽仍未确认的追问列表。
    return [
        str_question
        for str_field_name, str_question in (
            ("keep_ready", "Confirm whether AXI-Stream ready handshake should be retained."),
            ("keep_last", "Confirm whether AXI-Stream last should be retained."),
            ("data_width", "Confirm the AXI-Stream data width."),
        )
        if str_field_name not in dict_interface_profile
    ]

# 汇总 AXI4 变体、burst 与地址数据宽度等仍需确认的关键字段问题。
def _axi4_open_questions(dict_interface_profile: dict[str, Any]) -> list[str]:
    """
    汇总 AXI4 场景仍需确认的关键字段问题。

    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :return: AXI4 场景的开放问题列表。
    """

    # 先声明 AXI4 模板定稿前必须确认的基础字段集合。
    tuple_axi4_required_fields = (
        "axi4_variant",  # AXI4 Full/Lite 变体选择
        "role",  # master/slave 角色选择
        "read_write_mode",  # 读写模式选择
        "data_width",  # 数据总线位宽
        "addr_width",  # 地址总线位宽
        "burst_support",  # 是否允许 burst 访问
    )  # AXI4 基础字段集合

    # 提前固定 AXI4 基础字段追问模板，避免后面函数调用行过长。
    str_axi4_question_template = "Confirm the AXI4 configuration field `{field}`."  # AXI4 基础字段追问模板

    # 先收集 AXI4 的基础字段问题。
    list_questions = _family_field_open_questions(  # AXI4 基础字段缺口问题列表
        dict_interface_profile,  # 当前 AXI4 profile 字段快照
        tuple_axi4_required_fields,  # 驱动 role、位宽与 burst 缺口枚举的字段集合
        str_axi4_question_template,  # 把字段名映射成统一英文追问句的模板
    )

    # AXI4 Full 模式下还需要确认 id_width。
    if dict_interface_profile.get("axi4_variant") == "axi4_full" and "id_width" not in dict_interface_profile:

        # 追加 AXI4 Full 变体缺少 id_width 的问题。
        list_questions.append("Confirm the AXI4 full id width.")

    # 开启 burst_support 时还需要确认最大 burst 长度。
    if bool(dict_interface_profile.get("burst_support")) and "max_burst_len" not in dict_interface_profile:

        # 追加 burst 长度上限仍未确认的问题。
        list_questions.append("Confirm the AXI4 maximum burst length.")

    # 返回 AXI4 场景的开放问题列表。
    return list_questions

# 按字段模板汇总接口家族的缺口问题。
def _family_field_open_questions(
    dict_interface_profile: dict[str, Any],
    tuple_required_fields: tuple[str, ...],
    str_question_template: str,
) -> list[str]:
    """
    按字段模板汇总接口家族的缺口问题。

    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :param tuple_required_fields: 当前接口家族要求确认的字段集合。
    :param str_question_template: 使用 `{field}` 占位的追问模板。
    :return: 当前字段集合对应的开放问题列表。
    """

    # 返回缺失字段对应的开放问题列表。
    return [
        str_question_template.format(field=str_field_name)
        for str_field_name in tuple_required_fields
        if str_field_name not in dict_interface_profile
    ]

# 汇总生成阶段必须显式防守的语法和接口风险
def _syntax_risk_checks(spec: dict[str, Any]) -> list[str]:
    """
    根据当前规格补齐生成前必须守住的语法与接口边界检查项。

    :param spec: 含接口族、模板选择和 RTL 风格约束的规范化规格。
    :return: 供 requirements 阶段写入提示词的风险检查条目列表。
    """

    # 写入所有设计都必须满足的基础风险约束。
    list_checks = [
        (
            "Prevent placeholder text, undefined symbols, and missing output artifacts "
            "before code generation."
        ),  # 先阻断占位文本、未定义符号和缺失产物
        (
            "Keep the implementation aligned with the executable Python reference model "
            "and the staged verification flow."
        ),  # 要求生成实现持续对齐 Python 参考模型和分阶段校验流程
    ]  # 所有生成任务共享的基础风险项

    # 读取当前规格选择的接口族名称。
    str_interface_family = str(spec.get("interface_family") or "").lower()  # 当前接口族分类键

    # 读取当前规格绑定的用例模板。
    dict_use_case_template = select_use_case_template(spec)  # ADC/DAC 等业务模板

    # 收集已细化的 Verilog 模板提示。
    list_refined_templates = summarize_refined_templates(spec)  # 后续需要保留的模板线索

    # 读取当前 RTL 风格配置。
    str_rtl_style_profile = str(spec.get("rtl_style_profile") or "").lower()  # RTL 风格剖面名

    # 在默认流水线场景补充结构性约束。
    if spec.get("pipeline_required", True):

        # 把流水线必须成立的结构约束追加到风险清单。
        list_checks.append(
            "Reject non-pipelined implementations unless the user explicitly disables the pipeline requirement."
        )

    # 根据 AXI-Stream 语义补充 ready/last 和分组约束。
    if str_interface_family == "axi_stream":

        # 把 AXI-Stream 握手和分组规则成组追加到风险清单。
        list_checks.extend(
            [
                (
                    "Do not silently add or remove AXI-Stream ready/last semantics; "
                    "use the confirmed interface profile."
                ),  # 约束 ready/last 语义必须与已确认接口画像一致
                (
                    "Preserve Erie-style bus port grouping for AXI-Stream channels "
                    "instead of flattening the interface declaration."
                ),  # 保留 Erie 风格的 AXI-Stream 通道分组声明
            ]
        )

    # 根据 AXI4 全功能总线补充角色与通道约束。
    if str_interface_family == "axi4":

        # 把 AXI4 主协议约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve the confirmed AXI4 variant, role, widths, and burst policy "
                    "across the generated interface."
                ),  # 保留 AXI4 变体、角色、位宽和 burst 策略
                (
                    "Preserve Erie-style bus port grouping for AXI4 channels "
                    "instead of flattening the interface declaration."
                ),  # 防止 AXI4 多通道端口被错误压平成普通离散信号
            ]
        )

    # 根据 AXI4-Lite 总线补充寄存器访问约束。
    if str_interface_family == "axi4_lite":

        # 把 AXI4-Lite 访问约束和声明约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve the confirmed AXI4-Lite role, read/write mode, and "
                    "register-map widths across the generated interface."
                ),  # 保留 AXI4-Lite 角色、读写模式和寄存器位宽配置
                (
                    "Preserve Erie-style bus port grouping for AXI4-Lite channels "
                    "instead of flattening the interface declaration."
                ),  # 保持 AXI4-Lite 控制寄存器接口沿用 Erie 约定的分组形式
            ]
        )

    # 根据 AHB/APB 总线补充角色、位宽和时钟域约束。
    if str_interface_family in {"ahb", "apb"}:

        # 先把接口族标识转换成用于提示词的大写标签。
        str_interface_family_label = str_interface_family.upper()  # 报文里使用的大写接口族名

        # 把 AHB/APB 的接口保持约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve the confirmed "
                    + str_interface_family_label
                    + " role, widths, and clock/reset domain across the generated interface."
                ),  # 保留总线角色、位宽和时钟复位域配置
                (
                    "Preserve Erie-style bus port grouping for "
                    + str_interface_family_label
                    + " channels instead of flattening the interface declaration."
                ),  # 保留 Erie 风格的总线通道分组声明
            ]
        )

    # 用例模板存在时要求保留其来源和参数化意图。
    if dict_use_case_template:

        # 把模板来源和参数化意图约束追加到风险清单。
        list_checks.append(
            "Preserve the selected ADC/DAC use-case template family `"
            + str(dict_use_case_template.get("template_id"))
            + "` and keep its provenance, parameterization points, and "
            + "board-level sideband intent visible in generated artifacts."
        )

    # 有细化模板时显式要求保留对应模式提示。
    if list_refined_templates:

        # 把已选细化模板的提示语追加到风险清单。
        list_checks.append(
            "Preserve the selected refined Verilog pattern hints: "
            + ", ".join(item["template_id"] for item in list_refined_templates)
            + "."
        )

    # Erie 严格风格需要附加固定的版式和命名约束。
    if str_rtl_style_profile == "erie_strict":

        # 把 Erie 严格风格的固定版式和命名约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve Erie strict RTL style rules, including single-reg always blocks, "
                    "strict naming, and region order."
                ),  # 保留 Erie strict 的时序块、命名和区域顺序规则
                (
                    "Preserve the Erie bilingual header with version, revision date, "
                    "and revision history blocks."
                ),  # 保留 Erie 双语文件头和修订历史区
                (
                    "When an FSM is present, use `state_current`, `state_next`, "
                    "and `ST_*` naming consistently."
                ),  # 保持状态机状态寄存器与枚举名的一致命名
                (
                    "Preserve Erie module instance naming with `_Inst` suffixes "
                    "and `gen_*` generate labels."
                ),  # 保留模块例化名和 generate 标签的 Erie 约定
                (
                    "Keep AXI/AXIS/APB/AHB ports grouped by channel and role "
                    "instead of flattening the bus declaration list."
                ),  # 保留总线端口按通道和角色组织的声明顺序
            ]
        )

    # 返回已经整理好的风险检查条目。
    return list_checks

# 汇总 requirements 阶段必须覆盖的关键行为检查点
def _critical_behavior_checkpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    根据规格文本和显式断言配置生成关键行为检查点清单。

    :param spec: 可能包含 semantic_checkpoints、reset 和 behavior 信息的规格字典。
    :return: 供 requirements 阶段写入提示词的行为检查点列表。
    """

    # 关键行为检查点分支只关心“有没有显式列表”，因此这里只保留列表形态的用户输入。
    bool_has_explicit_checkpoints = isinstance(spec.get("semantic_checkpoints"), list)  # 是否存在用户显式提供的行为检查点列表

    # 只有结构化列表才允许直接作为行为检查点输入复用。
    list_explicit_checkpoints = spec.get("semantic_checkpoints") if bool_has_explicit_checkpoints else []  # 用户手工提供的行为检查点列表

    # 显式检查点存在时直接复用专门的转换逻辑。
    if list_explicit_checkpoints:

        # 返回按显式配置整理好的行为检查点。
        return _semantic_checkpoints(spec)

    # 先写入所有设计都应具备的复位已知态检查点。
    list_checkpoints: list[dict[str, Any]] = [
        {
            "id": "reset_known_state",  # 默认复位检查点标识
            "category": "reset",  # 该检查点属于复位类约束
            "signals": [str((spec.get("reset") or {}).get("name") or "rst_n")],  # 复位观测信号列表
            "verification_hint": "Check reset-driven initialization before nominal traffic.",  # 复位校验提示语
            "text": "Reset and initial conditions must drive outputs to a known state.",  # 复位必须到达已知态的主描述
        }
    ]  # 默认行为检查点列表

    # 逐项把 behavior 描述转换成 requirements 阶段可消费的检查点。
    for index, item in enumerate(spec.get("behavior", []) or [], start=1):

        # 提取当前行为条目的主描述文本。
        str_behavior_text = item.get("text") if isinstance(item, dict) else str(item)  # 当前行为描述文本

        # 收集当前规格中最值得先观测的前两个输出信号。
        list_observed_outputs = [
            dict_port["name"]  # 当前输出端口名
            for dict_port in spec.get("interfaces", {}).get("ports", [])  # 接口端口候选集合
            if isinstance(dict_port, dict) and dict_port.get("direction") == "output"  # 只保留输出口
        ][:2]  # 最多保留两个代表性输出口

        # 把当前行为描述登记成 requirements 检查点。
        list_checkpoints.append(
            {
                "id": f"behavior_{index}",  # 当前行为检查点标识
                "category": "behavior",  # 该检查点属于功能行为类约束
                "signals": list_observed_outputs,  # 当前行为优先观测的输出信号
                "verification_hint": "Capture this behavior in the Python checkpoint payload and the RTL transcript.",  # 行为校验提示语
                "text": str_behavior_text,  # 把当前 behavior 语句原样写入检查点描述区
            }
        )

    # 返回 requirements 阶段整理好的关键行为检查点列表。
    return list_checkpoints

# 规范化显式或推导得到的语义检查点清单
def _semantic_checkpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    把显式语义检查点配置和自动补齐的输出观测项统一整理成标准结构。

    :param spec: 可能包含 semantic_checkpoints 和 interfaces.ports 的规格字典。
    :return: 已补齐默认字段和输出观测项的语义检查点列表。
    """

    # 这里先探测 semantic_checkpoints 是否已经是可直接展开的列表契约，再决定是否进入逐项标准化。
    bool_has_explicit_checkpoints = isinstance(spec.get("semantic_checkpoints"), list)  # 当前语义检查点输入是否已满足列表契约

    # 这里的显式列表只服务于语义检查点整理，不和行为检查点路径共用语义。
    list_explicit_checkpoints = spec.get("semantic_checkpoints") if bool_has_explicit_checkpoints else []  # 语义检查点整理使用的显式列表

    # 显式检查点存在时优先逐项整理用户指定内容。
    if list_explicit_checkpoints:

        # 初始化显式检查点整理后的结果列表。
        list_checkpoints: list[dict[str, Any]] = []  # 显式检查点整理结果

        # 逐项规范化用户给出的显式检查点。
        for index, item in enumerate(list_explicit_checkpoints, start=1):

            # 非字典条目按最小行为检查点格式包装。
            if not isinstance(item, dict):

                # 把当前简单条目转换成标准检查点对象。
                list_checkpoints.append(
                    {
                        "id": f"checkpoint_{index}",  # 当前检查点默认标识
                        "category": "behavior",  # 当前检查点默认归类为行为约束
                        "signals": [],  # 简单条目没有显式绑定信号
                        "verification_hint": "",  # 简单条目没有额外提示语
                        "text": str(item),  # 用条目文本直接作为检查点描述
                    }
                )

                # 当前简单条目处理完成后继续看下一个检查点。
                continue

            # 复制当前字典条目，避免直接修改调用方原始数据。
            dict_payload = copy.deepcopy(item)  # 当前显式检查点的独立副本

            # 补齐当前检查点的稳定标识字段。
            dict_payload["id"] = str(dict_payload.get("id") or f"checkpoint_{index}")  # 检查点标识

            # 补齐当前检查点的类别字段。
            dict_payload["category"] = str(dict_payload.get("category") or "behavior")  # 检查点类别

            # 补齐当前检查点的信号列表字段。
            dict_payload["signals"] = list(dict_payload.get("signals") or [])  # 检查点绑定信号列表

            # 补齐当前检查点的校验提示字段。
            dict_payload["verification_hint"] = str(dict_payload.get("verification_hint") or "")  # 检查点校验提示语

            # 补齐当前检查点的主描述文本字段。
            dict_payload["text"] = str(  # 当前检查点对外暴露的主描述文本
                dict_payload.get("text")  # 优先沿用显式给出的主描述
                or dict_payload.get("description")  # 没有 text 时回退到 description
                or dict_payload["id"]  # 再没有文本时退回稳定检查点标识
            )  # 检查点主描述文本

            # 把规范化后的显式检查点加入结果列表。
            list_checkpoints.append(dict_payload)

    # 没有显式检查点时回退到自动推导逻辑。
    else:

        # 读取按行为和复位规则自动推导出的默认检查点。
        list_checkpoints = _critical_behavior_checkpoints(spec)  # 自动推导的检查点列表

    # 逐个扫描输出端口，确保每个输出至少有一个 observe 检查点。
    for dict_port in spec.get("interfaces", {}).get("ports", []) or []:

        # 过滤掉非输出端口和非字典端口描述。
        if not isinstance(dict_port, dict) or dict_port.get("direction") != "output":

            # 当前端口不需要追加 observe 检查点。
            continue

        # 计算当前输出端口是否已经存在对应的 observe 检查点。
        bool_has_observe_checkpoint = any(  # 当前输出端口是否已经存在 observe 检查点
            dict_item.get("id") == f"observe_{dict_port['name']}"  # 命中的 observe 检查点标识
            for dict_item in list_checkpoints  # 已整理的检查点集合
            if isinstance(dict_item, dict)  # 只检查字典形态的有效检查点
        )

        # 已有 observe 检查点时不再重复追加。
        if bool_has_observe_checkpoint:

            # 当前输出端口已经具备观测项，继续处理下一个端口。
            continue

        # 为当前输出端口追加默认的观测检查点。
        list_checkpoints.append(
            {
                "id": f"observe_{dict_port['name']}",  # 当前输出口的观测检查点标识
                "category": "observable_output",  # 该检查点属于输出观测类约束
                "signals": [dict_port["name"]],  # 当前检查点只绑定一个输出口
                "verification_hint": (
                    f"Keep `{dict_port['name']}` visible in transcript outputs "
                    "and checkpoint payloads."
                ),  # 输出观测提示语
                "text": (
                    f"Observe output `{dict_port['name']}` when validating "
                    "behavior and regression drift."
                ),  # 输出观测主描述文本
            }
        )

    # 返回补齐完成后的语义检查点列表。
    return list_checkpoints

# 汇总子功能之间的依赖关系图，供 requirements 和规划阶段复用
def _subfunction_dependency_graph(spec: dict[str, Any]) -> dict[str, Any]:
    """
    根据规格中的 subfunctions 定义构造轻量依赖图结构。

    :param spec: 可能包含 subfunctions、name 和语义检查点信息的规格字典。
    :return: 含 nodes 与 edges 的子功能依赖图字典。
    """

    # 初始化依赖图中的节点集合。
    list_nodes: list[dict[str, Any]] = []  # 子功能节点列表

    # 初始化依赖图中的边集合。
    list_edges: list[dict[str, Any]] = []  # 子功能依赖边列表

    # 读取规格里声明的子功能条目集合。
    list_subfunctions = (
        list(spec.get("subfunctions", []) or [])  # 复制规格里原始的子功能列表
        if isinstance(spec.get("subfunctions", []) or [], list)  # 只有列表形态才允许继续展开依赖图
        else []  # 其余类型统一回退为空子功能集合
    )  # 规范化后的子功能列表

    # 没有子功能时回退到只包含顶层模块的最小图。
    if not list_subfunctions:

        # 返回仅含顶层节点的保底依赖图。
        return {
            "nodes": [
                {
                    "id": str(spec.get("name")),  # 顶层模块节点标识
                    "name": str(spec.get("name")),  # 顶层模块节点名称
                    "test_intent": [],  # 顶层模块默认无子功能测试意图
                }
            ],
            "edges": [],  # 没有子功能时不存在依赖边
        }

    # 逐项把子功能配置转换成依赖图节点和边。
    for dict_subfunction in list_subfunctions:

        # 跳过无名称或非法结构的子功能条目。
        if not isinstance(dict_subfunction, dict) or not dict_subfunction.get("name"):

            # 当前条目无法成为有效子功能节点，继续处理下一个条目。
            continue

        # 提取当前子功能的标准化名称。
        str_subfunction_name = str(dict_subfunction["name"])  # 当前子功能名称

        # 整理当前子功能的测试意图文本列表。
        list_test_intent = [
            dict_item.get("text") if isinstance(dict_item, dict) else str(dict_item)  # 当前测试意图文本
            for dict_item in dict_subfunction.get("test_intent", []) or []  # 当前子功能的测试意图条目
        ]  # 供依赖图节点持久化的测试意图文本快照

        # 整理当前子功能绑定的语义检查点标识列表。
        list_semantic_checkpoint_ids = [
            dict_item.get("id")  # 当前语义检查点标识
            for dict_item in dict_subfunction.get("semantic_checkpoints", [])  # 当前子功能的语义检查点条目
            if isinstance(dict_item, dict)  # 只保留字典形态的有效检查点
        ]  # 当前子功能的语义检查点标识列表

        # 把当前子功能登记成依赖图节点。
        list_nodes.append(
            {
                "id": str_subfunction_name,  # 当前子功能节点标识
                "name": str_subfunction_name,  # 当前子功能节点名称
                "test_intent": list_test_intent,  # 供下游规划阶段读取的测试意图快照
                "semantic_checkpoints": list_semantic_checkpoint_ids,  # 当前子功能绑定的检查点标识
            }
        )

        # 逐项展开当前子功能声明的依赖关系。
        for dependency in dict_subfunction.get("dependencies", []) or []:

            # 把当前依赖关系登记成从依赖项指向子功能的有向边。
            list_edges.append(
                {
                    "from": str(dependency),  # 依赖源子功能名称
                    "to": str_subfunction_name,  # 依赖目标子功能名称
                    "kind": "subfunction_dependency",  # 当前依赖边的固定类型
                }
            )

    # 返回已经整理完成的子功能依赖图。
    return {"nodes": list_nodes, "edges": list_edges}
