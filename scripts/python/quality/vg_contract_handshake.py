"""识别握手通道并执行消费使能门禁。"""

# future annotations 让握手 profile 的动态类型保持延迟解析。
from __future__ import annotations

# re 和 typing 支持端口角色识别及动态 profile 结构。
import re
from typing import Any

# 握手门禁读取共享 RTL 事实和统一评估状态。
from .vg_semantic_facts import VgFacts
from .vg_rule_models import VgEvaluation, VgFinding, failed, passed

# 复用模块枚举与源码定位证据构造。
from .vg_contract_liveness import _identifier_names
from .vg_contract_parameter import _finding, _source_modules

# _coerce_channel_list 过滤公开 profile 中的握手通道对象。
def _coerce_channel_list(obj_candidate: object) -> list[dict[str, Any]]:
    """返回只含字典项的握手通道列表。

    参数:
        obj_candidate: requirements 或 profile 中的原始候选值。
    返回:
        可读取 valid/ready/payload 字段的通道对象列表。
    """

    # 非列表不具备公开通道数组语义。
    if not isinstance(obj_candidate, list):

        # 结构错误由上游规范检查处理，这里保持无候选。
        return []

    # 过滤非对象项，避免后续角色读取发生类型漂移。
    return [item for item in obj_candidate if isinstance(item, dict)]

# _configured_handshake_channels 读取无模块名的显式握手角色。
def _configured_handshake_channels(facts: VgFacts) -> list[dict[str, Any]]:
    """返回 requirements 或 interface profile 的显式通道。

    参数:
        facts: 当前 RTL 与设计需求事实。
    返回:
        已过滤且不带模块作用域的握手通道对象列表。
    """

    # requirements 角色优先于 profile 角色。
    dict_requirements = facts.spec.get("design_requirements", {})  # 读取顶层设计需求

    # 只有对象需求才能提供握手通道字段。
    if isinstance(dict_requirements, dict):

        # 读取 requirements 原始通道候选。
        obj_requirements_channels: object = dict_requirements.get("handshake_channels", [])  # requirements 原始通道值

        # 将 requirements 通道值过滤成结构化列表。
        list_channels = _coerce_channel_list(obj_requirements_channels)  # requirements 通道列表

        # 显式 requirements 角色存在时不再回退到 profile。
        if list_channels:

            # 返回已过滤的 requirements 通道。
            return list_channels

    # requirements 缺失时读取顶层 interface_profile。
    dict_profile = facts.spec.get("interface_profile", {})  # 当前接口画像

    # 非对象 profile 不具备显式通道语义。
    if not isinstance(dict_profile, dict):

        # 保持无通道的适用性结论。
        return []

    # profile 通道仍然不携带模块名或层级约束。
    return _coerce_channel_list(dict_profile.get("handshake_channels", []))

# _handshake_channels 从 profile 或端口名称生成候选通道角色。
def _handshake_channels(facts: VgFacts, dict_module: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """返回当前模块可确认的 valid/ready 通道。

    参数:
        facts: 当前 RTL 与接口角色事实。
        dict_module: formatter 生成的模块事实。
    返回:
        已确认的握手通道角色元组。
    """

    # 读取不绑定模块名的显式握手角色。
    list_configured_channels = _configured_handshake_channels(facts)  # 当前 profile 通道候选

    # 显式通道保留结构化字段并检查两端信号是否存在。
    list_channels: list[dict[str, Any]] = []  # 当前模块显式通道

    # 建立当前模块端口名称集合用于角色匹配。
    set_port_names = {
        str(dict_port.get("name") or "")  # 当前端口名称
        for dict_port in dict_module.get("ports", []) or []  # 遍历当前模块端口
    }  # 当前模块端口名称集合

    # 读取无模块作用域的通道对象。
    for dict_channel in list_configured_channels:

        # 显式通道必须是对象且至少包含 valid/ready。
        if not isinstance(dict_channel, dict):

            # 畸形通道交给 inconclusive 分支，不猜测角色。
            continue

        # 读取通道两侧和稳定 id。
        str_valid = str(dict_channel.get("valid") or "")  # 通道 valid 信号

        # 保存 ready 角色名称，后续控制分析需要精确引用。
        str_ready = str(dict_channel.get("ready") or "")  # ready 角色名称供消费控制匹配。

        # 把接口数据角色留在 finding 证据中，但不影响传输触发条件。
        str_payload = str(dict_channel.get("payload") or "")  # 当前通道 payload 信号

        # 只有当前模块可见两侧才建立显式通道。
        if str_valid in set_port_names and str_ready in set_port_names:

            # 保存显式角色和来源标记。
            list_channels.append(
                {
                    "id": str(dict_channel.get("id") or "channel"),
                    "valid": str_valid,
                    "ready": str_ready,
                    "payload": str_payload,
                    "source": "profile",
                }
            )

    # 无显式角色时按共同前缀配对 terminal valid/ready 端口。
    if list_channels:

        # 显式角色已足够，不再增加名称推断造成重复。
        return tuple(list_channels)

    # 端口名称仅用于发现候选，不作为违规结论的唯一依据。
    dict_candidates: dict[str, dict[str, str]] = {}  # 通道前缀到 valid/ready 端口的候选表

    # 逐个端口寻找终止 valid/ready 角色。
    for str_name in set_port_names:

        # 只接受以 valid 或 ready 结尾的语义端口。
        obj_match = re.fullmatch(r"(?:i_|o_)?(.+?)_(valid|ready)", str_name, re.IGNORECASE)  # 识别端口末尾的 valid/ready 角色。

        # 无法配对的端口不构成握手通道。
        if obj_match is None:

            # 普通数据端口不是握手候选。
            continue

        # 共同前缀用于把同一通道两侧配对。
        str_prefix = obj_match.group(1).casefold()  # 当前通道共同前缀

        # 保存当前端口的握手角色类别。
        str_role = obj_match.group(2).casefold()  # 当前候选角色

        # 保存该前缀对应的 valid 或 ready 端口。
        dict_candidates.setdefault(str_prefix, {})[str_role] = str_name  # 按共同前缀暂存角色端口。

    # 只返回两侧都存在且唯一的通道。
    for str_prefix, dict_roles in dict_candidates.items():

        # 缺少一侧时不能形成可靠握手通道。
        if "valid" not in dict_roles or "ready" not in dict_roles:

            # 保留其他前缀的配对机会。
            continue

        # 候选名称配对只作为发现来源，后续仍需数据流消费证据。
        list_channels.append(
            {
                "id": str_prefix,
                "valid": dict_roles["valid"],
                "ready": dict_roles["ready"],
                "payload": "",
                "source": "inferred",
            }
        )

    # 返回固定前缀顺序的通道元组。
    return tuple(sorted(list_channels, key=lambda dict_item: str(dict_item["id"])))

# _control_names 读取 formatter control 节点中的信号名称。
def _control_names(obj_control: dict[str, Any]) -> set[str]:
    """返回一个控制表达式中的标识符集合。

    参数:
        obj_control: formatter 控制表达式节点。
    返回:
        控制节点引用的信号名称集合。
    """

    # 当前 control 可能直接是 expression fact 或 nested node。
    return _identifier_names(obj_control)

# evaluate_handshake_gate 执行 VG155 的消费使能证明。
def evaluate_handshake_gate(facts: VgFacts) -> VgEvaluation:
    """检查消费行为是否同时受同一通道 valid 和 ready 控制。

    参数:
        facts: 当前 RTL 与接口角色事实。
    返回:
        VG155 的通过或失败结论。
    """

    # 收集握手通道和违规消费证据。
    list_findings: list[VgFinding] = []  # VG155 握手完整性证据

    # 发现握手通道后进入 ready-valid 适用范围。
    bool_applicable = False  # 当前 RTL 是否存在可确认通道

    # 每个模块独立分析端口通道与组合/时序赋值控制。
    for obj_source, dict_module in _source_modules(facts):

        # 角色识别优先使用 profile，名称推断只作为候选发现。
        tuple_channels = _handshake_channels(facts, dict_module)  # 当前模块的 valid/ready 通道

        # 没有可确认通道时不强行猜测自定义协议。
        if not tuple_channels:

            # 继续检查其他模块的显式通道。
            continue

        # 当前模块存在握手分析对象。
        bool_applicable = True  # 当前模块存在可验证的握手角色。

        # 每条组合表达式的 controls 代表其真实消费使能条件。
        for dict_expression in dict_module.get("comb_expressions", []) or []:

            # 合并当前赋值表达式的全部控制名称。
            set_controls: set[str] = set()  # 当前赋值的控制信号集合

            # 遍历当前组合赋值的控制条件。
            for dict_control in dict_expression.get("controls", []) or []:

                # 控制节点递归展开 valid/ready 引用。
                set_controls.update(_control_names(dict_control))

            # 没有控制条件的普通赋值不是传输消费事件。
            if not set_controls:

                # 继续检查其他赋值。
                continue

            # 当前赋值可能属于多个候选通道，逐通道确认。
            for dict_channel in tuple_channels:

                # 只有触及通道一侧的行为才进入消费完整性检查。
                bool_touches_channel = bool(  # 判断赋值控制是否触及该通道角色。
                    {dict_channel["valid"], dict_channel["ready"]} & set_controls  # 角色集合与控制集合求交。
                )  # 当前赋值是否使用该通道控制

                # 与该通道无关的控制条件不构成该通道消费。
                if not bool_touches_channel:

                    # 继续检查下一条通道。
                    continue

                # 两侧同时出现表示当前消费已具备完整握手条件。
                if {dict_channel["valid"], dict_channel["ready"]}.issubset(set_controls):

                    # 当前通道消费条件完整。
                    continue

                # valid-only 或 ready-only 控制时，下一条 finding 需要指出缺失的另一侧角色。
                str_missing = (  # 该字符串用于机器报告当前消费行为缺少的 valid 或 ready 控制角色。
                    dict_channel["ready"]  # valid-only 消费需要补齐 ready 控制角色。
                    if dict_channel["valid"] in set_controls  # 仅出现 valid 时输出 ready 缺口证据。
                    else dict_channel["valid"]  # 未出现 valid 时输出 valid 缺口证据。
                )  # 当前消费缺失的握手信号

                # 保存消费赋值目标，便于定位握手违规行为。
                str_target = str(dict_expression.get("target") or "<unknown>")  # 当前消费赋值目标

                # 生成不依赖目标命名的握手违规证据。
                list_findings.append(
                    _finding(
                        obj_source,
                        dict_module,
                        line=int(dict_expression.get("line") or 1),
                        message="传输消费行为没有同时使用同一通道的 valid 和 ready。",
                        evidence=str_target,
                        metadata=(
                            ("channel", f"{dict_channel['id']}:{dict_channel['valid']}/{dict_channel['ready']}"),
                            ("valid", dict_channel["valid"]),
                            ("ready", dict_channel["ready"]),
                            ("payload", dict_channel.get("payload", "")),
                            ("controls", sorted(set_controls)),
                            ("missing", str_missing),
                        ),
                    )
                )

    # 任何握手 finding 都是确定的完整性违规。
    if list_findings:

        # 统一返回 failed 并保留所有通道证据。
        return failed(*list_findings)

    # 无通道时不适用，有通道但无违规时通过。
    return passed(applicable=bool_applicable)
