"""requirements 规范化与确认校验辅助逻辑。"""

# 启用前向引用标注，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 导入 requirements 规范化阶段需要的标准库能力。
import copy
from typing import Any

# 导入接口模板解析相关的本地辅助能力。
from .interface_templates import InterfaceTemplateError, resolve_interface_template

# 导入 AXI4 共享标签与枚举常量。
from .requirements_extract import (
    AXI4_MODE_LABELS,
    AXI4_MODES,
    AXI4_ROLE_LABELS,
    AXI4_ROLES,
    AXI4_VARIANT_LABELS,
    AXI4_VARIANTS,
)

# 导入 requirements 共享的接口家族与确认类型。
from .requirements_extract import (
    INTERFACE_FAMILIES,
    NATIVE_FORBIDDEN_PROFILE_KEYS,
    STREAMABILITY_VALUES,
    RequirementConfirmation,
    _merged_requirement_base,
    _normalize_requirement_confirmation,
)

# 导入 requirements 启发式推断辅助入口。
from .requirements_extract import detect_interface_family, detect_streamability

# 把调用侧 requirements 参数合并回统一 spec 结构。
def apply_requirement_defaults(
    raw_spec: dict[str, Any],
    *,
    design_requirements: dict[str, Any] | None = None,
    pipeline_required: bool | None = None, streamability: str | None = None,
    interface_family: str | None = None, interface_profile: dict[str, Any] | None = None,
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
