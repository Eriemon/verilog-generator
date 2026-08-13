"""封装协议排序、header 语义与 reset 语义规则。"""

# 延迟类型注解求值，避免模块导入阶段过早解析复杂联合类型。
from __future__ import annotations

# 复制原 quality gate 的基础标准库依赖，保持无第三方包可运行。
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# formatter_ast 与 rulebook 仍是这些子模块依赖的唯一结构化入口。
from .formatter_backend.banners import display_width
from .formatter_ast import build_ast_report_for_path, iter_verilog_sources, read_verilog_source
from scripts.python.validation.rulebook import load_verilog_rulebook

# 协议排序规则只直接依赖诊断类型与协议顺序上下文。
from .quality_gate_types import ProtocolOrderIssueContext, QualityIssue

# 低有效复位名称统一按下划线语义段识别。
from .reset_name_roles import is_low_active_reset_name

# 协议 token 集合与通用小工具从 common 模块显式导入。
from .quality_gate_common import (
    APB_PORT_TOKENS,
    APB_REQUEST_TOKENS,
    APB_RESPONSE_TOKENS,
    AXIS_CONTROL_TOKENS,
    AXIS_DATA_TOKENS,
    AXIS_PORT_TOKENS,
)

# 协议排序诊断还需要 common 中的行号与格式 helper。
from .quality_gate_common import (
    _as_line,
    _strip_line_comment,
    _style_severity,
)

# 供 `_header_semantic_rules` 复用的拆分 helper，专门处理检查 header 中声明的模块名是否匹配真实 module 名。
def _header_semantic_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 header 中声明的模块名是否匹配真实 module 名。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: header 语义一致性诊断列表。
    """

    # list_issues 保存 header 语义诊断。
    list_issues: list[QualityIssue] = []  # header 语义诊断集合

    # module_name 来自 formatter AST 的真实声明。
    str_module_name = str(dict_module.get("name") or "")  # 真实 module 声明名

    # 空 module 名由旧命名规则处理。
    if not str_module_name:

        # 没有真实 module 名时无法比较 header。
        return list_issues

    # 只读取 module 前文本作为文件头区域。
    str_pre_module = _pre_module_region(str_text)  # 待解析的文件头说明区

    # 英文和中文 header 均可提供 module 名。
    tuple_header_names = (  # header 中可比较的 module 名字段
        ("header.module_name.english", _extract_header_field(str_pre_module, "Module Name")),  # 英文头声明名
        ("header.module_name.chinese", _extract_header_field(str_pre_module, "模块名称")),  # 中文头声明名
    )

    # 逐个可用 header 字段检查一致性。
    for str_rule, str_header_name in tuple_header_names:

        # 缺失字段由 VG007 处理，这里只检查已存在的字段。
        if not str_header_name:

            # 跳过缺失字段。
            continue

        # header module 名必须和真实 module 名一致。
        if str_header_name != str_module_name:

            # 语义不一致会误导后续生成、注释和验证流程。
            list_issues.append(
                QualityIssue(
                    "VG051",
                    _style_severity(strict),
                    f"Header module name `{str_header_name}` does not match module declaration `{str_module_name}`.",
                    str_rel_path,
                    1,
                    str_rule,
                )
            )

    # 返回 header 语义诊断。
    return list_issues

# 供 `_protocol_port_order_rules` 复用的拆分 helper，专门处理检查常见协议端口是否按 rulebook 声明的 section 顺序排列。
def _protocol_port_order_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查常见协议端口是否按 rulebook 声明的 section 顺序排列。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 协议端口顺序诊断列表。
    """

    # list_ports 保持 formatter AST 声明顺序和行号。
    list_ports = list(dict_module.get("ports", []) or [])  # 端口 AST 条目顺序

    # 空端口列表无需检查。
    if not list_ports:

        # 返回空诊断。
        return []

    # 当前规则覆盖 rulebook 声明的 AXI/AXIS/APB section 顺序。
    tuple_protocol_tokens = ("axi", "axis", "apb")  # 支持检查的协议 token

    # list_issues 保存协议端口诊断。
    list_issues: list[QualityIssue] = []  # 协议端口顺序诊断集合

    # 逐个协议 token 判断是否出现协议端口。
    for str_protocol in tuple_protocol_tokens:

        # 每个协议单独计算 section 序列和第一处回退诊断。
        list_issues.extend(_protocol_order_issues_for_protocol(list_ports, str_protocol, str_rel_path, strict=strict))

    # 返回协议端口顺序诊断。
    return list_issues

# 供 `_protocol_order_issues_for_protocol` 复用的拆分 helper，专门处理检查单个协议端口是否按 section 顺序声明。
def _protocol_order_issues_for_protocol(
    list_ports: list[dict[str, Any]],
    str_protocol: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个协议端口是否按 section 顺序声明。

    :param list_ports: formatter AST 中保持源码顺序的端口列表。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把协议顺序问题升级为 error。
    :return: 当前协议的端口顺序诊断列表。
    """

    # tuple_sections 定义当前协议端口从时钟复位到数据/响应通道的期望顺序。
    tuple_sections = _protocol_sections(str_protocol)  # 协议端口分组期望顺序

    # dict_section_rank 用于判断声明顺序是否回退。
    dict_section_rank = {  # 协议 section 顺序比较表
        str_section: int_index  # section 名称到顺序号
        for int_index, str_section in enumerate(tuple_sections)  # 保留 rulebook 声明次序
    }

    # list_seen_sections 提供 VG057 判断 section 是否回退的端口序列。
    list_seen_sections = _protocol_seen_sections(list_ports, str_protocol, dict_section_rank)  # 协议端口 section 扫描结果

    # 没有当前协议端口时无需检查。
    if not list_seen_sections:

        # 当前 module 未使用该协议。
        return []

    # protocol_context 集中保存 VG057 报告需要的排序证据。
    protocol_context = ProtocolOrderIssueContext(  # 封装 VG057 的 rank 表、合法顺序、路径和 strict
        dict_section_rank=dict_section_rank,  # section 名称到 rank 的比较表
        tuple_sections=tuple_sections,  # rulebook 声明的合法 section 次序
        str_protocol=str_protocol,  # 正在检查的协议族标识
        str_rel_path=str_rel_path,  # 触发诊断的 Verilog 相对路径
        strict=strict,  # 是否按交付门禁升级为 error
    )

    # 返回第一处 section 顺序回退诊断。
    return _protocol_order_violation_issue(list_seen_sections, protocol_context)

# 供 `_protocol_seen_sections` 复用的拆分 helper，专门处理按源码顺序收集属于指定协议的端口 section。
def _protocol_seen_sections(
    list_ports: list[dict[str, Any]],
    str_protocol: str,
    dict_section_rank: dict[str, int],
) -> list[tuple[str, str, int | None]]:
    """
    按源码顺序收集属于指定协议的端口 section。

    :param list_ports: formatter AST 中保持源码顺序的端口列表。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :param dict_section_rank: 当前协议 section 到顺序号的映射。
    :return: 端口名、section 和行号组成的扫描结果。
    """

    # list_seen_sections 承载后续 rank 回退检测所需的端口序列。
    list_seen_sections: list[tuple[str, str, int | None]] = []  # 协议端口声明顺序

    # 逐端口归类到 protocol section。
    for dict_port in list_ports:

        # str_port_name 是 formatter AST 解析出的端口名。
        str_port_name = str(dict_port.get("name") or "")  # 当前端口名

        # str_section 为空说明该端口不属于当前协议。
        str_section = _protocol_port_section(str_port_name, str_protocol)  # 当前端口所属协议 section

        # rulebook 未声明的 section 按协议 fallback 规则归一。
        str_section = _normalised_protocol_section(str_section, dict_section_rank)  # 可参与排序的 section

        # 非当前协议端口不参与排序。
        if not str_section:

            # 用户普通端口不等同于协议 other section。
            continue

        # 记录端口名、section 和 AST 行号。
        list_seen_sections.append((str_port_name, str_section, _as_line(dict_port.get("line_start"))))

    # 返回按声明顺序收集的协议端口。
    return list_seen_sections

# 供 `_normalised_protocol_section` 复用的拆分 helper，专门处理协议分类结果规范到当前 rulebook 的 section 集合。
def _normalised_protocol_section(str_section: str, dict_section_rank: dict[str, int]) -> str:
    """
    把协议分类结果规范到当前 rulebook 的 section 集合。

    :param str_section: 协议端口分类结果。
    :param dict_section_rank: 当前协议 section 到顺序号的映射。
    :return: 可参与排序的 section；不应参与时返回空字符串。
    """

    # 空 section 表示端口不属于当前协议。
    if not str_section:

        # 不参与协议排序。
        return ""

    # rulebook 已声明的 section 可直接使用。
    if str_section in dict_section_rank:

        # 保留原始分类结果。
        return str_section

    # AXI/AXIS 未识别协议端口可归入 other section。
    if "other" in dict_section_rank:

        # 使用 other 维持保守排序检查。
        return "other"

    # APB 等无 other section 的协议忽略未知专名端口。
    return ""

# 供 `_protocol_order_violation_issue` 复用的拆分 helper，专门处理协议端口 section 顺序中的第一处回退诊断。
def _protocol_order_violation_issue(
    list_seen_sections: list[tuple[str, str, int | None]],
    protocol_context: ProtocolOrderIssueContext,
) -> list[QualityIssue]:
    """
    返回协议端口 section 顺序中的第一处回退诊断。

    :param list_seen_sections: 端口名、section 和行号组成的扫描结果。
    :param protocol_context: 协议端口排序诊断上下文。
    :return: 至多一条协议顺序诊断。
    """

    # int_last_rank 保存此前出现过的最大 section rank。
    int_last_rank = -1  # 当前扫描到的最高 section 排名

    # str_last_section 用于错误消息指出回退边界。
    str_last_section = ""  # 上一个最高 section 名称

    # 按端口声明顺序查找 section rank 回退。
    for str_port_name, str_section, int_line in list_seen_sections:

        # int_rank 是当前端口所属 section 的排序等级。
        int_rank = protocol_context.dict_section_rank[str_section]  # 当前端口 section 排名

        # rank 回退说明端口分组顺序违反 rulebook。
        if int_rank < int_last_rank:

            # 协议端口顺序错误影响接口扫描和 wrapper 适配。
            return [
                QualityIssue(
                    "VG057",
                    _style_severity(protocol_context.strict),
                    (
                        f"{protocol_context.str_protocol.upper()} port `{str_port_name}` is in section "
                        f"`{str_section}` after `{str_last_section}`; expected order is "
                        f"{' -> '.join(protocol_context.tuple_sections)}."
                    ),
                    protocol_context.str_rel_path,
                    int_line,
                    "protocol.port_order",
                )
            ]

        # 更新已见最大 rank 和对应 section。
        if int_rank > int_last_rank:

            # 同步最高 rank，后续低 rank 端口会据此判定回退。
            int_last_rank = int_rank  # 已见最大 section rank

            # 记录最高 rank 的 section 名称，便于 VG057 展示前序分组。
            str_last_section = str_section  # 已见最大 section 名称

    # 没有 section 回退时通过。
    return []

# 供 `_protocol_sections` 复用的拆分 helper，专门处理协议端口 section 的合法顺序。
def _protocol_sections(str_protocol: str) -> tuple[str, ...]:
    """
    返回协议端口 section 的合法顺序。

    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :return: section 名称元组。
    """

    # dict_fallback_sections 保留 rulebook 读取失败时的内置保守顺序。
    dict_fallback_sections = {  # 协议 section fallback 顺序
        "axi": ("clock_reset", "aw", "w", "b", "ar", "r", "other"),  # AXI memory mapped 通道顺序
        "axis": ("clock_reset", "slave", "master", "control", "data", "other"),  # AXIS 端点先分组再归入通用信号
        "apb": ("clock_reset", "request", "response"),  # APB 请求先于响应
    }

    # 读取 rulebook 的 protocols 配置。
    try:

        # dict_protocols 是机器规则源中的协议配置。
        dict_protocols = load_verilog_rulebook().raw.get("protocols") or {}  # rulebook 协议配置

        # tuple_rulebook_sections 取出目标协议 section 列表。
        tuple_rulebook_sections = dict_protocols.get(f"{str_protocol}_sections") or ()  # 目标协议 section 配置

        # 非空配置优先使用 rulebook。
        if tuple_rulebook_sections:

            # 返回不可变元组，避免调用方修改规则表。
            return tuple(str(item) for item in tuple_rulebook_sections)

    # rulebook 失败时让 VG059 另行报告，这里只保持协议检查可运行。
    except Exception:

        # tuple_rulebook_sections 置空后会自然落回内置协议顺序。
        tuple_rulebook_sections = ()  # rulebook 异常后的协议 section 空配置

    # 返回保守 fallback，未知协议返回空元组。
    return dict_fallback_sections.get(str_protocol, ())

# 供 `_protocol_port_section` 复用的拆分 helper，专门处理端口名在指定协议中的 section。
def _protocol_port_section(str_port_name: str, str_protocol: str) -> str:
    """
    判断端口名在指定协议中的 section。

    :param str_port_name: Verilog 端口名。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :return: section 名称；不属于该协议时返回空字符串。
    """

    # str_lower_name 用于大小写无关的协议名和信号名判断。
    str_lower_name = str_port_name.lower()  # 归一化端口名

    # AXIS 必须先判断，避免 axis 被 axi 子串误归类。
    if str_protocol == "axis":

        # 返回 AXIS 数据或控制 section。
        return _axis_port_section(str_lower_name)

    # AXI memory-mapped 端口不能误吞 AXIS。
    if str_protocol == "axi":

        # 返回 AXI channel section。
        return _axi_port_section(str_lower_name)

    # APB 端口按 request/response 分类。
    if str_protocol == "apb":

        # APB 标准端口名映射到 request/response section。
        return _apb_port_section(str_lower_name)

    # 未知协议不参与检查。
    return ""

# 供 `_axi_port_section` 复用的拆分 helper，专门处理AXI memory-mapped 端口 section。
def _axi_port_section(str_lower_name: str) -> str:
    """
    判断 AXI memory-mapped 端口 section。

    :param str_lower_name: 小写端口名。
    :return: AXI section；非 AXI 端口返回空字符串。
    """

    # AXIS 端口不属于 AXI memory-mapped。
    if "axis" in str_lower_name:

        # 避免 axis 中的 axi 子串误报。
        return ""

    # 只处理名字里明确包含 axi 的端口。
    if "axi" not in str_lower_name:

        # 普通用户端口不属于 AXI。
        return ""

    # 时钟和复位属于 clock_reset section。
    if _is_protocol_clock_or_reset(str_lower_name):

        # protocol clock/reset 必须排在最前。
        return "clock_reset"

    # tuple_parts 按下划线切分，用于识别 awaddr/wdata 等 channel 前缀。
    tuple_parts = tuple(part for part in re.split(r"[^a-z0-9]+", str_lower_name) if part)  # 端口名分段

    # AXI 通道前缀顺序和 rulebook sections 一致。
    for str_section in ("aw", "w", "b", "ar", "r"):

        # 任一段以通道名开头即归入该通道。
        if any(str_part.startswith(str_section) for str_part in tuple_parts):

            # 返回识别到的 AXI 通道 section。
            return str_section

    # 明确属于 AXI 但未归类的端口归入 other。
    return "other"

# 细分 AXI Stream 端口在 data、control 与 endpoint 之间的 section 归属。
def _axis_port_section(str_lower_name: str) -> str:
    """
    判断 AXI Stream 端口 section。

    :param str_lower_name: 小写端口名。
    :return: AXIS section；非 AXIS 端口返回空字符串。
    """

    # AXIS 常见命名包含 axis 或 tdata/tvalid 等 stream 信号。
    bool_axis_named = "axis" in str_lower_name or _contains_any_token(str_lower_name, AXIS_PORT_TOKENS)  # AXIS 端口命名证据

    # 非 AXIS 端口不参与 AXIS 排序。
    if not bool_axis_named:

        # 当前端口不是 AXIS。
        return ""

    # AXIS 时钟复位必须排在最前。
    if _is_protocol_clock_or_reset(str_lower_name):

        # AXIS 时钟复位归入 clock_reset section。
        return "clock_reset"

    # str_endpoint_side 用于支持 s_axis/m_axis 端点成组的常见端口布局。
    str_endpoint_side = _axis_endpoint_side(str_lower_name)  # AXIS slave/master 端点侧别

    # 明确带 s_axis/m_axis 前缀的端口按端点侧别排序，不强制侧内 handshake/data 顺序。
    if str_endpoint_side:

        # 返回 slave 或 master section。
        return str_endpoint_side

    # tdata/tkeep/tstrb 是数据通道。
    if _contains_any_token(str_lower_name, AXIS_DATA_TOKENS):

        # AXIS payload 和 byte-enable 信号归入 data section。
        return "data"

    # 其余 AXIS 握手、帧尾和用户信息归入 control。
    if _contains_any_token(str_lower_name, AXIS_CONTROL_TOKENS):

        # AXIS 握手、帧尾和旁带信号归入 control section。
        return "control"

    # AXIS 专名端口未命中数据或控制 token 时归入 other。
    return "other"

# 供 `_axis_endpoint_side` 复用的拆分 helper，专门处理AXIS 端口名中的端点侧别。
def _axis_endpoint_side(str_lower_name: str) -> str:
    """
    返回 AXIS 端口名中的端点侧别。

    :param str_lower_name: 小写端口名。
    :return: slave、master 或空字符串。
    """

    # tuple_endpoint_rules 按 rulebook 顺序覆盖短前缀和长前缀两种端点写法。
    tuple_endpoint_rules = (  # AXIS 端点侧别识别规则
        ("slave", r"(^|_)s_axis(_|$)", "slave_axis"),  # slave 端点短前缀和长前缀
        ("master", r"(^|_)m_axis(_|$)", "master_axis"),  # 下游输出端点命名
    )

    # 按 slave、master 顺序识别显式端点分组。
    for str_section, str_short_pattern, str_long_token in tuple_endpoint_rules:

        # bool_short_endpoint_match 保证 s_axis 不会误命中普通字符串中间片段。
        bool_short_endpoint_match = re.search(str_short_pattern, str_lower_name) is not None  # 端点短前缀边界命中

        # bool_long_endpoint_match 兼容 slave_axis/master_axis 长前缀写法。
        bool_long_endpoint_match = str_long_token in str_lower_name  # 端点长前缀命中

        # bool_endpoint_match 汇总短前缀和长前缀两类端点命名。
        bool_endpoint_match = bool_short_endpoint_match or bool_long_endpoint_match  # 显式 AXIS 端点命中

        # 命中后返回端点级 section，侧内 tvalid/tdata 不再强排。
        if bool_endpoint_match:

            # 返回 rulebook 可排序的端点 section。
            return str_section

    # 不带端点侧别的 AXIS 端口交由 data/control fallback 分类。
    return ""

# 把 APB 端口细分到 clock、request 与 response 这几个 section。
def _apb_port_section(str_lower_name: str) -> str:
    """
    判断 APB 端口 section。

    :param str_lower_name: 小写端口名。
    :return: APB section；非 APB 端口返回空字符串。
    """

    # APB 端口可能显式包含 apb，也可能只使用 paddr/psel 等标准名。
    bool_apb_named = "apb" in str_lower_name or _contains_any_token(str_lower_name, APB_PORT_TOKENS)  # APB 前缀或 P* 标准信号命中标志

    # 普通用户端口不进入 APB section 顺序检查。
    if not bool_apb_named:

        # 空 section 表示该端口不参与 APB 排序。
        return ""

    # APB clock/reset 归入最前置 section。
    if _is_protocol_clock_or_reset(str_lower_name):

        # pclk/preset/prst 类信号必须早于请求和响应通道。
        return "clock_reset"

    # request 信号由 master 发起。
    if _contains_any_token(str_lower_name, APB_REQUEST_TOKENS):

        # 地址、选择、写数据和保护信号归入 request section。
        return "request"

    # response 信号由 slave 返回。
    if _contains_any_token(str_lower_name, APB_RESPONSE_TOKENS):

        # 读数据、ready 和错误信号归入 response section。
        return "response"

    # 未识别的 APB 端口不参与排序。
    return ""

# 供 `_contains_any_token` 复用的拆分 helper，专门处理小写端口名是否包含任一协议 token。
def _contains_any_token(str_lower_name: str, tuple_tokens: tuple[str, ...]) -> bool:
    """
    判断小写端口名是否包含任一协议 token。

    :param str_lower_name: 已转成小写的 Verilog 端口名。
    :param tuple_tokens: 协议端口名 token 集合。
    :return: 任一 token 出现在端口名中时返回 True。
    """

    # bool_has_token 复用协议分类中的任一 token 命中语义。
    bool_has_token = any(str_token in str_lower_name for str_token in tuple_tokens)  # 协议 token 命中标志

    # 返回 token 命中结果。
    return bool_has_token

# 供 `_is_protocol_clock_or_reset` 复用的拆分 helper，专门处理端口名是否表达协议 clock/reset。
def _is_protocol_clock_or_reset(str_lower_name: str) -> bool:
    """
    判断端口名是否表达协议 clock/reset。

    :param str_lower_name: 小写端口名。
    :return: clock 或 reset 端口返回 True。
    """

    # 常见 clock/reset token 覆盖 AXI、AXIS、APB、AHB 等命名。
    return any(str_token in str_lower_name for str_token in ("clk", "clock", "rst", "reset", "areset", "preset"))

# 供 `_extract_header_field` 复用的拆分 helper，专门处理从 module 前文件头中读取指定字段的首个值 token。
def _extract_header_field(str_pre_module: str, str_field: str) -> str:
    """
    从 module 前文件头中读取指定字段的首个值 token。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_field: 需要提取的文件头字段名。
    :return: 字段值的首个非空 token，未找到时返回空字符串。
    """

    # 字段行统一形如 // Field: value 或 // 字段: value。
    str_pattern = rf"(?m)^\s*//\s*{re.escape(str_field)}\s*:\s*(?P<value>.*?)\s*$"  # 文件头字段匹配正则

    # obj_match 定位字段行。
    obj_match = re.search(str_pattern, str_pre_module)  # 文件头字段匹配对象

    # 找不到字段时返回空字符串。
    if obj_match is None:

        # 缺字段由 VG007 负责报告。
        return ""

    # str_value 去掉外侧空白，便于从 tab 对齐字段中提取模块名。
    str_value = obj_match.group("value").strip()  # 字段原始值

    # 文件头字段可能保留制表对齐，只取第一个非空 token。
    list_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str_value)  # 字段中的 Verilog 标识符候选

    # 返回首个标识符候选。
    return list_tokens[0] if list_tokens else ""

# 供 `_reset_semantic_rules` 复用的拆分 helper，专门处理检查时序 always 的 reset 条件和低有效命名是否一致。
def _reset_semantic_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查时序 always 的 reset 条件和低有效命名是否一致。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: reset 深语义诊断列表。
    """

    # list_issues 保存 reset 条件诊断。
    list_issues: list[QualityIssue] = []  # reset 深语义诊断集合

    # 逐个时序 always 检查 reset 分支。
    for dict_always in dict_module.get("always", []) or []:

        # 单个 always 的 reset 极性和覆盖检查独立完成。
        list_issues.extend(_reset_semantic_issues_for_always(dict_always, str_rel_path, strict=strict))

    # 返回 reset 深语义诊断。
    return list_issues

# 供 `_reset_semantic_issues_for_always` 复用的拆分 helper，专门处理检查单个时序 always 的低有效 reset 分支。
def _reset_semantic_issues_for_always(
    dict_always: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个时序 always 的低有效 reset 分支。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 reset 语义问题升级为 error。
    :return: 当前 always 的 reset 语义诊断列表。
    """

    # 只检查 formatter 确认为时序块的 always。
    if dict_always.get("trigger_kind") != "seq":

        # 组合块不涉及 reset 极性。
        return []

    # str_reset 是 formatter 从敏感列表和 if 分支推断出的复位名。
    str_reset = str(dict_always.get("reset") or "")  # 时序块复位信号名

    # 没有 reset 或不是低有效命名时交给旧 VG021 处理。
    if not str_reset or not _is_low_active_reset_name(str_reset):

        # 本规则只判断低有效 reset 的分支条件是否反向。
        return []

    # int_base_line 是 always 起始行，行内偏移用于精确报告 reset 条件位置。
    int_base_line = _as_line(dict_always.get("line_start")) or 1  # always 块起始行号

    # list_always_lines 保持 formatter AST 提供的 always 内部源码行。
    list_always_lines = list(dict_always.get("lines", []) or [])  # always 内部源码行

    # int_reset_offset 定位 reset 分支 if 所在的 always 内部行。
    int_reset_offset = _find_reset_condition_offset(list_always_lines, str_reset)  # reset 条件行偏移

    # 找不到 reset if 条件时，敏感列表和正文不一致。
    if int_reset_offset is None:

        # 复位敏感列表没有对应复位分支会导致寄存器覆盖缺失。
        return [
            QualityIssue(
                "VG053",
                _style_severity(strict),
                f"Sequential always declares reset `{str_reset}` but no matching reset branch was found.",
                str_rel_path,
                int_base_line,
                rule="reset.coverage",
            )
        ]

    # str_code_line 用于忽略注释中的 reset 名称。
    str_code_line = _strip_line_comment(str(list_always_lines[int_reset_offset]))  # 去注释后的 reset 条件行

    # 条件符合低有效语义时通过。
    if _active_low_reset_condition_is_correct(str_code_line, str_reset):

        # reset 条件极性与命名一致。
        return []

    # 错误极性会导致复位覆盖语义反转。
    return [
        QualityIssue(
            "VG053",
            _style_severity(strict),
            f"Reset `{str_reset}` is low-active, but the reset branch condition is not low-active.",
            str_rel_path,
            int_base_line + int_reset_offset,
            rule="reset.condition_polarity",
        )
    ]

# 供 `_find_reset_condition_offset` 复用的拆分 helper，专门处理查找 always 内部首个 reset 条件行偏移。
def _find_reset_condition_offset(list_always_lines: list[Any], str_reset: str) -> int | None:
    """
    查找 always 内部首个 reset 条件行偏移。

    :param list_always_lines: always 内部源码行列表。
    :param str_reset: 低有效复位信号名。
    :return: reset 条件行偏移；未找到时返回 None。
    """

    # 遍历 always 内部行，查找 reset 分支 if。
    for int_offset, str_line in enumerate(list_always_lines):

        # 去注释文本避免注释中的 reset 名称触发覆盖判断。
        str_code_line = _strip_line_comment(str(str_line))  # 去注释后的 always 内部行

        # reset 条件通常在包含 if 和 reset 名的第一行。
        if str_reset in str_code_line and "if" in str_code_line:

            # 返回 reset 条件相对 always 起始行的偏移。
            return int_offset

    # 没有找到 reset 条件。
    return None

# 供 `_active_low_reset_condition_is_correct` 复用的拆分 helper，专门处理reset 条件行是否符合低有效复位约定。
def _active_low_reset_condition_is_correct(str_line: str, str_reset: str) -> bool:
    """
    判断 reset 条件行是否符合低有效复位约定。

    :param str_line: 去注释后的 Verilog 条件行。
    :param str_reset: 低有效复位信号名。
    :return: 条件表达低有效复位时返回 True。
    """

    # str_compact 去掉空白，统一比较不同代码风格。
    str_compact = re.sub(r"\s+", "", str_line)  # 去空白后的条件行

    # str_reset_pattern 是复位信号名的正则转义版本。
    str_reset_pattern = re.escape(str_reset)  # reset 名称正则

    # !rstn 或 ~rstn 是最直接的低有效判断。
    if re.search(rf"if\((?:!|~){str_reset_pattern}\)", str_compact):

        # 直接取反形式通过检查。
        return True

    # rstn == 0、rstn == 1'b0、rstn === 1'b0 等形式均表示低有效。
    tuple_low_patterns = (  # 可接受的低有效比较形式
        rf"{str_reset_pattern}={{2,3}}(?:1'b0|1'h0|1'd0|0)",  # reset 信号在比较左侧
        rf"(?:1'b0|1'h0|1'd0|0)={{2,3}}{str_reset_pattern}",  # 低电平常量在比较左侧
    )

    # 任一低有效比较形式命中即可通过。
    return any(re.search(str_pattern, str_compact, re.IGNORECASE) for str_pattern in tuple_low_patterns)

# 供 `_pre_module_region` 复用的拆分 helper，专门处理首个 module 声明之前的文件头区域。
def _pre_module_region(str_text: str) -> str:
    """
    返回首个 module 声明之前的文件头区域。

    :param str_text: 当前 Verilog 源码文本。
    :return: 模块声明前的源码区域文本。
    """

    # str_module_pattern 捕获文件中最早出现的 module 声明。
    str_module_pattern = r"(?m)^\s*module\s+[A-Za-z_][A-Za-z0-9_]*\b"  # 文件头截断锚点正则

    # obj_match 定位文件头和首个 module 主体的分界。
    obj_match = re.search(str_module_pattern, str_text)  # 首个 module 声明匹配对象

    # 找到 module 时返回前缀，否则返回全文。
    return str_text[: obj_match.start()] if obj_match else str_text

# 供 `_is_low_active_reset_name` 复用的拆分 helper，专门处理reset 信号名是否符合低有效约定。
def _is_low_active_reset_name(str_reset: str) -> bool:
    """
    判断 reset 信号名是否符合低有效约定。

    :param str_reset: reset 信号名称。
    :return: reset 名称表达低有效时返回 True。
    """

    # 共享角色函数同时支持 rstn、_rstn、_rst_n 和后续用途分段。
    return is_low_active_reset_name(str_reset)

# 返回当前模块需要公开的兼容导出名称清单。
def _export_names() -> list[str]:
    """
    返回当前模块对外继续公开的兼容符号名。

    参数:
        无外部业务参数。

    :return: 稳定的兼容导出名称列表。
    """

    # str_exports_source 按旧测试与调用方依赖顺序保留兼容导出名原文。
    str_exports_source = """
    _header_semantic_rules
    _protocol_port_order_rules
    _reset_semantic_rules
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
