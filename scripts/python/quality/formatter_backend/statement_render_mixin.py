"""为 VerilogFormatterEngine 提供端口、过程块和实例语句的渲染辅助。"""

# 延迟注解求值，避免 mixin 拆分后出现运行期类型循环依赖。
from __future__ import annotations

# 标准库只用于语句文本的局部识别和路径型兼容注解。
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

# banner 工具维持 formatter 对分组注释的既有输出风格。
from .banners import display_width, extract_banner_title, is_banner_line, make_banner

# 实例词法扫描器独立处理注释、字符串和 canonical 偏移映射。
from .instance_lexing import compact_instance_with_offsets

# AST 模型类只描述已解析结构，不在本 mixin 中新增解析协议。
from .models import (
    VerilogFormatterError,
    # 参数和端口模型服务于 module header 渲染。
    ParamDecl,
    ParamRenderCluster,
    PortDecl,
    PortLayoutInfo,
    # 输出和实例布局模型由 always/instance 渲染路径复用。
    OutputSignalLayout,
    AssignSourceLayout,
    InstanceSignalLayout,
    # body 声明模型保留 formatter backend 对外字段不变。
    SignalDecl,
    AssignStmt,
    BodyBlock,
    LValueRef,
    # 控制流模型承载过程语句、case、generate 和实例化结构。
    CaseItem,
    ControlNode,
    AlwaysBlock,
    InstanceBlock,
    GenerateBlock,
    # raw 与预处理模型保留无法结构化重写的源码片段。
    InitialBlock,
    FunctionBlock,
    TaskBlock,
    RawBlock,
    PreprocessorConditional,
    # header 元数据由顶层 module 渲染继续复用。
    HeaderMetadata,
)

# 文本读取工具保留给继承链中的旧入口，避免拆分 mixin 破坏导入面。
from .textio import read_verilog_text

# 实例端口连接注释前缀需要避开路径字面量误判。
INSTANCE_CONNECTION_COMMENT_PREFIX = "//" + "."  # legacy 实例端口连接注释前缀

# compact helper 保留 formatter 内部兼容入口并委派给独立词法扫描器。
def _compact_instance_with_offsets(text: str) -> tuple[str, tuple[int, ...], str]:
    """压缩实例 trivia 并保留 canonical 字符到原文的偏移。

    参数:
        text: formatter 已收集的完整实例声明文本。
    返回:
        canonical 文本、逐字符原文偏移和不完整原因。
    """

    # 独立扫描器负责普通文本、字符串、转义和两类注释状态。
    return compact_instance_with_offsets(text)

# 括号匹配 helper 跳过字符串中的括号字符。
def _matching_paren(text: str, int_open: int) -> int:
    """返回忽略字符串内容后的匹配右括号下标。

    参数:
        text: 待扫描的 canonical 实例文本。
        int_open: 已确认左括号的字符下标。
    返回:
        配对右括号下标；无法闭合时返回 -1。
    """

    # 深度从目标左括号开始统计嵌套结构。
    int_depth = 0  # 当前圆括号嵌套深度

    # 字符串状态防止参数字符串中的括号参与结构匹配。
    bool_in_string = False  # 当前字符是否处于双引号字符串

    # 转义状态区分字符串终止引号和普通引号字符。
    bool_escaped = False  # 前一字符是否转义当前字符

    # 从调用方确认的左括号位置逐字符向后扫描。
    for int_index in range(int_open, len(text)):

        # 当前字符决定字符串状态或括号深度变化。
        str_char = text[int_index]  # 圆括号配对扫描字符

        # 状态 helper 统一推进字符串、转义和括号深度。
        tuple_scan_state = _advance_paren_scan_state(  # 当前字符处理后的完整括号扫描状态
            str_char,  # 本轮括号状态迁移输入字符
            int_depth,  # 当前圆括号深度
            bool_in_string,  # 当前字符串范围状态
            bool_escaped,  # 当前转义状态
        )

        # 拆出下一字符使用的括号深度。
        int_depth = tuple_scan_state[0]  # 当前字符处理后的圆括号深度

        # 拆出下一字符使用的字符串范围状态。
        bool_in_string = tuple_scan_state[1]  # 当前字符处理后的字符串内标记

        # 拆出下一字符使用的转义状态。
        bool_escaped = tuple_scan_state[2]  # 当前字符处理后的反斜杠转义标记

        # 第四项只在目标左括号已经闭合时为真。
        if tuple_scan_state[3]:

            # 返回真实字符下标供关联区切片。
            return int_index

    # 扫描耗尽仍未回到零表示实例括号不完整。
    return -1

# 括号扫描 helper 在单个字符边界更新全部结构状态。
def _advance_paren_scan_state(
    str_char: str,
    int_depth: int,
    bool_in_string: bool,
    bool_escaped: bool,
) -> tuple[int, bool, bool, bool]:
    """推进一枚字符对应的括号匹配状态。

    参数:
        str_char: 当前扫描字符。
        int_depth: 当前圆括号深度。
        bool_in_string: 当前是否位于双引号字符串。
        bool_escaped: 前一字符是否转义当前字符。

    返回:
        更新后的深度、字符串状态、转义状态和目标闭合标记。
    """

    # 字符串内部只维护引号和转义，不读取括号语义。
    if bool_in_string:

        # 复用引号 helper 保持连续反斜杠的既有处理方式。
        tuple_string_state = _advance_quoted_text_state(str_char, bool_escaped)  # 当前字符串字符处理后的双状态

        # 字符串内容不会闭合调用方目标括号。
        return int_depth, tuple_string_state[0], tuple_string_state[1], False

    # 双引号打开字符串，后续括号暂时失去结构作用。
    if str_char == '"':

        # 新字符串从未转义状态开始。
        return int_depth, True, False, False

    # 左括号增加一层待闭合深度。
    if str_char == "(":

        # 嵌套参数或 actual 分组都必须成对闭合。
        return int_depth + 1, False, False, False

    # 普通字符不改变括号扫描状态。
    if str_char != ")":

        # 非结构字符直接沿用当前深度。
        return int_depth, False, False, False

    # 右括号抵消最近一层左括号。
    int_next_depth = int_depth - 1  # 当前右括号消费后的剩余深度

    # 深度回到零表示命中调用方目标左括号。
    return int_next_depth, False, False, int_next_depth == 0

# 引号状态 helper 只处理字符串内部的闭合与反斜杠奇偶关系。
def _advance_quoted_text_state(str_char: str, bool_escaped: bool) -> tuple[bool, bool]:
    """推进双引号字符串内部的扫描状态。

    参数:
        str_char: 当前字符串字符。
        bool_escaped: 前一字符是否转义当前字符。

    返回:
        更新后的字符串内状态和下一字符转义状态。
    """

    # 未转义双引号结束当前字符串范围。
    if str_char == '"' and not bool_escaped:

        # 闭合引号不会把转义状态传播到下一字符。
        return False, False

    # 反斜杠奇偶关系决定下一枚引号是否仍处于字符串内。
    if str_char == "\\":

        # 连续两个反斜杠会相互抵消转义状态。
        return True, not bool_escaped

    # 普通字符消费上一字符留下的转义状态。
    return True, False

# 顶层关联切分 helper 只在所有嵌套结构之外识别逗号。
def _split_association_ranges(text: str, int_start: int, int_end: int) -> list[tuple[int, int]]:
    """按顶层逗号切分关联区并返回绝对 canonical 范围。

    参数:
        text: 包含关联区的 canonical 实例文本。
        int_start: 关联区第一个字符下标。
        int_end: 关联区右边界的非包含下标。
    返回:
        每个关联条目的有序起止下标列表。
    """

    # 已闭合条目按源码顺序进入范围列表。
    list_ranges: list[tuple[int, int]] = []  # 顶层关联字符范围

    # 统一深度覆盖圆括号、花括号和方括号内的逗号。
    int_depth = 0  # 当前嵌套结构深度

    # 首条关联从调用方提供的关联区起点开始。
    int_item_start = int_start  # 当前关联条目起始下标

    # 只扫描参数或端口外层括号内部的字符。
    for int_index in range(int_start, int_end):

        # 当前字符用于更新嵌套深度或关闭一个顶层条目。
        str_char = text[int_index]  # 当前关联区字符

        # 任一开括号都会屏蔽其内部逗号的关联分隔语义。
        if str_char in "({[":

            # 进入表达式嵌套结构。
            int_depth += 1  # 当前嵌套深度增加一层

        # 任一闭括号结束对应的表达式嵌套层。
        elif str_char in ")}]":

            # 离开表达式嵌套结构。
            int_depth -= 1  # 当前嵌套深度减少一层

        # 深度为零的逗号才是 formal association 分隔符。
        elif str_char == "," and int_depth == 0:

            # 当前范围不包含分隔逗号自身。
            list_ranges.append((int_item_start, int_index))

            # 下一条关联从逗号后一个字符开始。
            int_item_start = int_index + 1  # 后续关联起始下标

    # 最后一个关联没有尾随逗号，需要在扫描结束时提交。
    if int_item_start < int_end or text[int_start:int_end].strip():

        # 最后范围延伸到调用方提供的非包含右边界。
        list_ranges.append((int_item_start, int_end))

    # 返回保持声明顺序的所有关联范围。
    return list_ranges

# 单条关联解析 helper 统一 named 与 positional 的字段形状。
def _association_record(text: str, int_start: int, int_end: int, int_position: int) -> dict[str, object]:
    """解析一个关联并保留 actual 的 canonical 偏移。

    参数:
        text: 包含当前关联的 canonical 实例文本。
        int_start: 当前关联起始下标。
        int_end: 当前关联非包含结束下标。
        int_position: 当前关联在所属列表中的零基位置。
    返回:
        formal、actual、样式、空连接和 actual 范围字段。
    """

    # 未裁剪右边界用于 positional actual 覆盖尾置 trivia。
    int_item_end = int_end  # 当前关联在 canonical 列表中的原始非包含终点

    # 起始空白不属于 actual 的权威字符范围。
    while int_start < int_end and text[int_start].isspace():

        # 游标推进到当前关联第一个可见字符。
        int_start += 1  # 去除关联左侧 canonical 空白

    # 结束空白同样需要从关联范围中排除。
    while int_end > int_start and text[int_end - 1].isspace():

        # 非包含右边界向左收缩到最后一个可见字符之后。
        int_end -= 1  # 去除关联右侧 canonical 空白

    # 当前条目文本用于判断 named association 语法。
    str_item = text[int_start:int_end]  # 去除外围空白的关联文本

    # named 匹配只负责 formal 外层，actual 内容已由括号感知切分保护。
    match_named = re.fullmatch(  # named formal 与 actual 捕获结果
        r"\.(?P<formal>[A-Za-z_]\w*)\s*\((?P<actual>.*)\)",  # 点名关联形态
        str_item,  # 当前独立关联文本
    )

    # named 关联需要把 actual 局部范围平移回实例 canonical 坐标。
    if match_named:

        # actual 起点由关联起点加命名捕获组局部偏移得到。
        int_actual_start = int_start + match_named.start("actual")  # 点名实际参数规范文本首字符位置

        # actual 终点保持非包含边界，便于原文切片。
        int_actual_end = int_start + match_named.end("actual")  # 点名实际参数规范文本非包含终点

        # named 结果保留 formal 名称以及显式空括号语义。
        return {
            "formal_name": match_named.group("formal"),  # 被调用侧 formal 名称
            "position": int_position,  # 当前关联声明位置
            "actual_text": match_named.group("actual").strip(),  # actual 可见文本
            "actual_start": int_actual_start,  # actual canonical 起始偏移
            "actual_end": int_actual_end,  # actual canonical 非包含结束偏移
            "explicit_unconnected": not match_named.group("actual").strip(),  # 是否显式空括号
            "style": "named",  # 当前关联采用点名形式
        }

    # 点号起始条目声称采用 named 语法，缺失括号时不得降级为 positional。
    if str_item.startswith("."):

        # invalid 样式由实例级入口统一转换为稳定失败原因。
        return {
            "formal_name": "",  # 畸形点名条目没有权威 formal
            "position": int_position,  # 保留声明位置用于诊断
            "actual_text": "",  # 缺少合法外层括号时不猜测 actual
            "actual_start": int_start,  # 失败记录保持字段形状稳定
            "actual_end": int_start,  # 空范围阻止失败记录被绑定
            "explicit_unconnected": False,  # 畸形语法不同于显式空连接
            "style": "invalid",  # 实例入口据此执行失败关闭
        }

    # positional 关联的完整条目就是 actual 表达式范围。
    return {
        "formal_name": "",  # 位置关联不携带 formal 名称
        "position": int_position,  # 调用方用于 formal 顺序绑定的位置
        "actual_text": str_item.strip(),  # positional actual 文本
        "actual_start": int_start,  # 位置实参沿用完整条目左边界
        "actual_end": int_item_end,  # 保留尾置 trivia 后的条目边界
        "explicit_unconnected": not str_item.strip(),  # 是否为空位置连接
        "style": "positional",  # 当前关联采用位置形式
    }

# 公开实例关联入口组合 identity、参数区、数组范围与端口区结果。
def parse_instance_associations(text: str) -> dict[str, object]:
    """解析实例身份、参数和端口关联，不产生任何渲染副作用。

    参数:
        text: formatter 收集的完整实例声明原文。
    返回:
        实例身份、关联列表、解析状态和局部失败原因。
    """

    # canonical 文本、偏移表和失败原因必须来自同一次无副作用扫描。
    tuple_compact = _compact_instance_with_offsets(text)  # 当前实例的词法压缩三元组

    # canonical 文本驱动后续模块名和括号结构匹配。
    str_compact = tuple_compact[0]  # 已移除 trivia 的实例文本

    # 偏移表把 actual canonical 范围换算回实例原文。
    tuple_offsets = tuple_compact[1]  # canonical 字符对应的原文偏移

    # 独立原因字段阻止词法半成品进入结构化关联解析。
    str_compact_reason = tuple_compact[2]  # 当前实例词法不完整原因

    # 未闭合或游离的词法结构必须保持实例局部失败。
    if str_compact_reason:

        # 失败只污染当前实例并保留稳定诊断原因。
        return {"parse_complete": False, "unsupported_reason": str_compact_reason}

    # 模块名前缀是后续参数区和实例名扫描的起点。
    match_module = re.match(r"^(?P<module>[A-Za-z_]\w*)", str_compact)  # 实例模块名匹配

    # 缺模块名或分号说明当前文本不是完整实例声明。
    if not match_module or not str_compact.endswith(";"):

        # 前缀/终止符错误只污染当前实例。
        return {"parse_complete": False, "unsupported_reason": "invalid_instance_prefix_or_terminator"}

    # 游标从模块名结束处进入可选参数覆盖区。
    int_cursor = match_module.end()  # canonical 实例扫描游标

    # 模块名后的分隔空白不属于参数井号或实例名。
    while int_cursor < len(str_compact) and str_compact[int_cursor].isspace():

        # 跳过 canonical 前缀分隔空格。
        int_cursor += 1  # 参数区候选起点

    # 无参数实例保持空 override 列表。
    list_parameters: list[dict[str, object]] = []  # 参数覆盖关联记录

    # 井号表示模块名之后存在参数覆盖外层括号。
    if int_cursor < len(str_compact) and str_compact[int_cursor] == "#":

        # 参数左括号必须位于井号之后。
        int_open = str_compact.find("(", int_cursor + 1)  # 参数覆盖左括号下标

        # 左括号存在时再运行字符串感知的配对扫描。
        int_close = (  # 参数覆盖右括号下标
            _matching_paren(str_compact, int_open)  # 定位参数列表闭合位置
            if int_open >= 0  # 只有真实左括号才能进入配对扫描
            else -1  # 缺少左括号按未闭合处理
        )

        # 参数括号无法闭合时禁止猜测后续实例名边界。
        if int_close < 0:

            # 局部原因明确指出参数关联区未闭合。
            return {"parse_complete": False, "unsupported_reason": "unclosed_parameter_associations"}

        # 顶层逗号切分结果逐项转换为统一关联记录。
        list_parameters = [
            _association_record(str_compact, int_start, int_end, int_position)  # 当前参数关联记录
            for int_position, (int_start, int_end) in enumerate(  # 逐项携带参数声明位置
                _split_association_ranges(str_compact, int_open + 1, int_close)  # 参数关联范围
            )
        ]  # 有序参数覆盖关联

        # 参数区闭合后游标进入实例名和数组范围部分。
        int_cursor = int_close + 1  # 参数区后的扫描起点

    # 参数区或模块名后的空白需要在实例名匹配前跳过。
    while int_cursor < len(str_compact) and str_compact[int_cursor].isspace():

        # 推进到实例标识符首字符。
        int_cursor += 1  # 实例名候选起点

    # 实例头匹配同时保留可选静态数组范围和端口左括号。
    match_instance = re.match(  # 实例标识符、数组范围和端口头匹配结果
        r"(?P<name>[A-Za-z_]\w*)\s*(?P<array>\[[^\]]+\])?\s*\(",  # 实例名、数组和端口头
        str_compact[int_cursor:],  # 参数区之后的 canonical 文本
    )  # 实例身份匹配结果

    # 缺实例名或端口左括号时无法形成结构化关联。
    if not match_instance:

        # 保留当前实例原文并报告稳定的身份/端口区原因。
        return {"parse_complete": False, "unsupported_reason": "invalid_instance_name_or_port_section"}

    # 匹配末字符就是端口列表外层左括号。
    int_port_open = int_cursor + match_instance.end() - 1  # 端口关联左括号下标

    # 端口右括号必须由同一括号感知 helper 定位。
    int_port_close = _matching_paren(str_compact, int_port_open)  # 端口关联右括号下标

    # 端口区既要闭合，闭合后也只能剩余实例分号。
    if int_port_close < 0 or str_compact[int_port_close + 1 :].strip() != ";":

        # 尾随额外语句或缺右括号都按端口区未闭合处理。
        return {"parse_complete": False, "unsupported_reason": "unclosed_port_associations"}

    # 端口关联按源码位置转换成 named/positional 统一记录。
    list_ports = [
        _association_record(str_compact, int_start, int_end, int_position)  # 当前端口关联记录
        for int_position, (int_start, int_end) in enumerate(  # 逐项携带端口声明位置
            _split_association_ranges(str_compact, int_port_open + 1, int_port_close)  # 端口关联范围
        )
    ]  # 有序端口关联

    # 条目级畸形 named 语法不得被 mixed 或 positional 分类掩盖。
    if any(item["style"] == "invalid" for item in [*list_parameters, *list_ports]):

        # 当前实例不提供可用于层级绑定的权威关联列表。
        return {"parse_complete": False, "unsupported_reason": "malformed_named_association"}

    # 所有关联样式用于识别非法 named/positional 混用。
    list_styles = [  # 实例关联样式序列
        str(item["style"])  # 当前参数或端口关联样式
        for item in [*list_parameters, *list_ports]  # 遍历实例全部关联记录
    ]  # 保持参数区后接端口区的声明顺序

    # 空实例、统一样式和混用样式分别得到稳定类别。
    str_style = (  # 当前实例总体关联风格
        "mixed"  # 同一实例存在多种关联形式
        if len(set(list_styles)) > 1  # named 与 positional 同时出现
        else (list_styles[0] if list_styles else "empty")  # 统一样式或空列表
    )

    # named formal 用于阻断同一实例中的重复连接。
    list_formals = [  # 非空 formal 名称序列
        str(item["formal_name"])  # 当前点名关联 formal
        for item in [*list_parameters, *list_ports]  # 遍历参数和端口关联
        if item["formal_name"]  # positional 关联没有 formal 名称
    ]

    # 同名 formal 重复出现会使绑定关系不唯一。
    if len(list_formals) != len(set(list_formals)):

        # 重复 formal 只让当前实例解析不完整。
        return {"parse_complete": False, "unsupported_reason": "duplicate_named_association"}

    # actual canonical 偏移需要逐项换算为原始实例字符偏移。
    for dict_item in [*list_parameters, *list_ports]:

        # 起始下标当前仍处于 canonical 实例坐标系。
        int_actual_start = int(dict_item["actual_start"])  # 当前实参在规范实例中的左边界

        # 结束下标保持非包含边界语义。
        int_actual_end = int(dict_item["actual_end"])  # actual canonical 结束偏移

        # 起始字符映射回原文；空尾部使用实例文本长度兜底。
        dict_item["actual_start"] = (  # actual 原实例文本起始偏移
            tuple_offsets[int_actual_start]  # canonical 字符对应的原文位置
            if int_actual_start < len(tuple_offsets)  # 起始位置仍在字符映射范围内
            else len(text)  # 空尾部 actual 位于实例文本末端
        )

        # 下一枚 canonical 结构字符的原文偏移完整覆盖尾置 trivia。
        dict_item["actual_end"] = (
            tuple_offsets[int_actual_end]  # actual 后继逗号或右括号的原文位置
            if int_actual_end > int_actual_start and int_actual_end < len(tuple_offsets)  # 存在后继结构字符
            else tuple_offsets[int_actual_end - 1] + 1  # 文本尾部退化为最后字符之后
            if int_actual_end > int_actual_start  # 非空 actual 仍需非包含结束边界
            else dict_item["actual_start"]  # 空 actual 起止位置相同
        )  # actual 原实例文本非包含结束偏移

    # 返回 renderer 和 report parser 共用的无副作用结构结果。
    return {
        "module_name": match_module.group("module"),  # 被例化模块标识符
        "instance_name": match_instance.group("name"),  # 当前实例标识符
        "array_range_text": match_instance.group("array") or "",  # 可选静态实例数组范围
        "parameter_overrides": list_parameters,  # 有序参数覆盖事实
        "port_associations": list_ports,  # 有序端口连接事实
        "association_style": str_style,  # named、positional、mixed 或 empty
        "parse_complete": str_style != "mixed",  # 混用形式禁止权威绑定
        "unsupported_reason": "mixed_association_style" if str_style == "mixed" else "",  # 局部解析原因
    }

# 端口分组 helper 共享的只读上下文。
@dataclass(frozen=True)
class PortMarkerContext:
    """封装单个端口分组判断过程中不会被 helper 修改的输入。"""

    # 正在累积的端口声明输出行。
    list_lines: list[str]  # 端口声明输出行缓存

    # 完整端口列表。
    ports: list[PortDecl]  # module header 原始端口顺序

    # 当前端口下标。
    index: int  # 当前端口在 ports 中的位置

    # 当前端口模型。
    port: PortDecl  # 当前正在处理的端口声明

    # 当前端口之前是否已经输出可见声明。
    bool_rendered_port: bool  # 空行插入时使用的可见端口状态

    # 当前端口相对游标的分组变化标记。
    tuple_flags: tuple[bool, bool, bool, bool]  # group/subgroup/section 边界标记

# 表达式顶层切分 helper 共享的可变扫描状态。
@dataclass
class ExpressionSplitState:
    """记录表达式顶层切分时正在累积的 token 和嵌套深度。"""

    # 已经完成的顶层表达式分段。
    list_result: list[list[str]]  # 顶层操作符左侧已提交分段

    # 当前仍在收集的表达式分段。
    list_current: list[str]  # 当前顶层表达式分段 token

    # 当前括号和花括号嵌套深度。
    int_depth: int  # 非零时屏蔽顶层操作符切分

    # 当前三元表达式嵌套深度。
    int_ternary_depth: int  # 非零时屏蔽目标操作符切分

# StatementRenderMixin 是 formatter backend 的结构化语句渲染层。
class StatementRenderMixin:
    """集中渲染 formatter 已解析出的端口、控制节点和实例节点。"""

    # 端口渲染入口保持原始端口顺序和分组注释协议。
    def _render_ports(self, ports: list[PortDecl]) -> list[str]:
        """
        渲染 module 头部端口声明列表。

        :param ports: 已解析出的端口声明模型列表。
        :return: 带一级缩进、分组注释和右侧说明的端口声明行。
        """

        # 收集 module 端口声明区的逐行输出，调用方继续负责拼接换行。
        list_lines: list[str] = []  # 端口声明区输出行

        # 记录最近输出的总线分组，避免重复生成 banner。
        str_current_group = ""  # 当前端口总线分组

        # 记录最近输出的端口小节标题，控制 section 注释边界。
        str_current_section = ""  # 当前端口 section 标题

        # 记录最近输出的协议子组，避免普通布局重复插入 subgroup 注释。
        str_current_subgroup = ""  # 普通布局最近可见的子组标题

        # 标记是否已经输出过真实端口，用于决定空行隔断。
        bool_rendered_port = False  # 已输出端口标记

        # 按原始顺序渲染端口，保证 formatter wire output 稳定。
        for index, port in enumerate(ports):

            # synthetic 端口只参与内部布局，不进入 module 端口列表。
            if port.synthetic:

                # synthetic 项不产生任何可见行，继续处理后续真实端口。
                continue

            # raw_text 端口保持用户原文，避免格式化器改写未知声明。
            if self._append_raw_port_line(list_lines, port):

                # raw_text 已经完成一次可见端口输出。
                bool_rendered_port = True  # raw_text 端口输出完成

                # raw_text 分支不再执行结构化端口声明拼接。
                continue

            # 组合三个已输出标题，交给分组 helper 维护端口区层级边界。
            tuple_current_markers = (  # 端口标题去重所需的可见游标
                str_current_group,  # 最近输出的总线 banner 标题
                str_current_section,  # 最近输出的端口 section 标题
                str_current_subgroup,  # 最近输出的协议子组标题
            )  # 本端口渲染前已出现的标题集合

            # 分组 helper 会按既有顺序输出 banner、section 和 subgroup。
            tuple_group_state = self._append_port_group_markers(  # 端口分组渲染后的游标状态
                list_lines,  # 端口声明输出行
                ports,  # 完整端口列表，用于识别尾部和单槽分组
                index,  # 当前端口位置
                port,  # 正在决定分组标题的端口
                tuple_current_markers,  # 调用前的分组游标
                bool_rendered_port,  # 当前端口前是否已有可见声明
            )

            # 更新当前端口组游标，供下一个端口判断是否切换分组。
            str_current_group = tuple_group_state[0]  # 最新端口总线分组

            # 更新当前 section 游标，避免重复输出同一标题。
            str_current_section = tuple_group_state[1]  # 最新端口 section 标题

            # 更新 subgroup 游标，供下一端口判断协议子组是否已经可见。
            str_current_subgroup = tuple_group_state[2]  # 下一轮子组比较基准

            # 结构化声明行统一处理属性、位宽、尾逗号和右侧端口说明。
            self._append_port_declaration_line(list_lines, ports, index, port)

            # 标记至少一个普通端口已经进入输出区域。
            bool_rendered_port = True  # 普通端口输出完成

        # 返回 module 头部需要插入的端口声明行。
        return list_lines

    # raw_text 端口由旧解析路径交给渲染层原样输出。
    def _append_raw_port_line(self, list_lines: list[str], port: PortDecl) -> bool:
        """
        在端口带有 raw_text 时追加原始端口声明。

        :param list_lines: 正在累积的端口声明输出行。
        :param port: 当前端口模型。
        :return: 当前端口已经由 raw_text 分支输出时为 `True`。
        """

        # 没有 raw_text 的端口继续走结构化声明拼接路径。
        if not port.raw_text:

            # 返回 False 让调用方继续处理分组和声明字段。
            return False

        # raw_text 直接带一级缩进输出，保留用户手写端口形态。
        list_lines.append(f"{self._indent(1)}{port.raw_text}")

        # 告知调用方当前端口已经完成输出。
        return True

    # 端口分组标记集中维护 group、section 和 subgroup 三个游标。
    def _append_port_group_markers(
        self,
        list_lines: list[str],
        ports: list[PortDecl],
        index: int,
        port: PortDecl,
        # 当前游标以 tuple 传入，避免 helper 参数继续膨胀。
        tuple_current_markers: tuple[str, str, str],
        bool_rendered_port: bool,
    ) -> tuple[str, str, str]:
        """
        输出端口分组相关注释并返回更新后的游标。

        :param list_lines: 正在累积的端口声明输出行。
        :param ports: 完整端口列表，用于判断单槽分组 banner 是否冗余。
        :param index: 当前端口在端口列表中的下标。
        :param port: 当前端口模型。
        :param tuple_current_markers: 最近输出的 group、section 和 subgroup 标题。
        :param bool_rendered_port: 之前是否已输出过可见端口。
        :return: 更新后的 group、section 和 subgroup 游标。
        """

        # 拆开三个游标，后续逻辑按 group、section、subgroup 独立更新。
        str_current_group, str_current_section, str_current_subgroup = tuple_current_markers  # 当前端口前的分组游标

        # tuple_flags 描述当前端口相对上一端口的分组边界变化。
        tuple_flags = self._resolve_port_marker_flags(  # 当前端口的分组边界标记
            port,  # 用于计算标题边界的端口
            str_current_group,  # 上一条可见总线 banner
            str_current_section,  # 最近输出的 section 标题
            str_current_subgroup,  # 最近输出的子组标题
        )

        # port_marker_context 汇集只读端口边界信息，后续 helper 只更新游标。
        port_marker_context = PortMarkerContext(  # 当前端口的分组输出上下文
            list_lines=list_lines,  # module 端口声明输出缓存
            ports=ports,  # 当前 module 的完整端口声明顺序
            index=index,  # 当前端口在原始端口列表中的下标
            port=port,  # 正在决定标题边界的端口模型
            bool_rendered_port=bool_rendered_port,  # 空行隔断依赖的前置可见端口状态
            tuple_flags=tuple_flags,  # group、subgroup 和 section 的切换标记
        )

        # 总线分组切换会输出 banner 并重置 section/subgroup 游标。
        tuple_group_markers = self._append_port_group_boundary(  # 总线 banner 处理后的游标
            port_marker_context,  # 当前端口边界判断所需上下文
            tuple_current_markers,  # 进入本端口前的可见标题游标
        )

        # subgroup_first 模式先输出协议子组标题。
        tuple_subgroup_first_markers = self._append_port_subgroup_first_boundary(  # 子组优先标题后的游标
            port_marker_context,  # 复用同一个端口分组上下文
            tuple_group_markers,  # 总线 banner 阶段后的标题游标
        )

        # section 标题表达端口方向或功能区域。
        tuple_section_markers = self._append_port_section_boundary(  # section 标题处理后的游标
            port_marker_context,  # 当前端口和空行状态
            tuple_subgroup_first_markers,  # 子组优先阶段后的标题游标
        )

        # 普通布局在 section 后追加 subgroup 注释。
        tuple_final_markers = self._append_port_plain_subgroup_boundary(  # 普通子组标题处理后的游标
            port_marker_context,  # 当前端口和边界标记
            tuple_section_markers,  # section 阶段后的标题游标
        )

        # 返回三个游标给端口主循环继续推进。
        return tuple_final_markers

    # 总线分组边界负责 banner 和空行。
    def _append_port_group_boundary(
        self,
        port_marker_context: PortMarkerContext,
        tuple_current_markers: tuple[str, str, str],
    ) -> tuple[str, str, str]:
        """
        处理端口总线分组切换时的 banner 与游标重置。

        :param port_marker_context: 当前端口的分组输出上下文。
        :param tuple_current_markers: 进入 helper 前的 group、section 和 subgroup 游标。
        :return: group、section 和 subgroup 游标。
        """

        # 当前游标用于在未切换总线时保持标题状态。
        str_current_group, str_current_section, str_current_subgroup = tuple_current_markers  # 进入总线边界前的标题游标

        # 当前端口是否需要输出新的总线 banner。
        bool_group_changed = port_marker_context.tuple_flags[0]  # 总线 banner 刷新标记

        # subgroup-first 端口在 section 空行判断上有独立顺序。
        bool_subgroup_first = port_marker_context.tuple_flags[1]  # 协议子组先于 section 输出

        # section 切换在普通布局下需要提前插入视觉空行。
        bool_section_changed = port_marker_context.tuple_flags[3]  # 一级端口标题刷新标记

        # 分组或普通 section 切换前插入视觉空行，保持端口区可读。
        if bool_group_changed or (not bool_subgroup_first and bool_section_changed):

            # 空行插入由公共 helper 去重，避免出现连续空白行。
            self._ensure_single_blank_line_before_cluster(
                port_marker_context.list_lines,
                port_marker_context.bool_rendered_port,
            )

        # 未进入新总线分组时保持现有三个游标。
        if not bool_group_changed:

            # 返回原游标，后续 helper 继续处理 section/subgroup。
            return str_current_group, str_current_section, str_current_subgroup

        # 新分组标题成为后续端口判断分组切换的基准。
        str_current_group = self._append_port_group_banner(  # 最新输出的总线分组标题
            port_marker_context.list_lines,  # banner 写入的端口声明行缓存
            port_marker_context.ports,  # 完整端口列表用于判断单槽分组
            port_marker_context.index,  # 当前端口在列表中的位置
            port_marker_context.port,  # 当前触发分组切换的端口
        )

        # 新分组内部必须重新输出 section 和 subgroup。
        return str_current_group, "", ""

    # subgroup-first 边界负责优先协议标题。
    def _append_port_subgroup_first_boundary(
        self,
        port_marker_context: PortMarkerContext,
        tuple_current_markers: tuple[str, str, str],
    ) -> tuple[str, str, str]:
        """
        处理 subgroup-first 布局下的协议子组标题。

        :param port_marker_context: 当前端口的分组输出上下文。
        :param tuple_current_markers: 总线边界处理后的标题游标。
        :return: group、section 和 subgroup 游标。
        """

        # 当前游标在子组优先分支中只会重置 section。
        str_current_group, str_current_section, str_current_subgroup = tuple_current_markers  # 子组优先处理前游标

        # 子组优先模式要求先输出协议标题再输出端口方向标题。
        bool_subgroup_first = port_marker_context.tuple_flags[1]  # 当前端口采用子组优先布局

        # 只有进入新的协议子组时才需要追加子组标题。
        bool_subgroup_changed = port_marker_context.tuple_flags[2]  # 协议子组标题刷新标记

        # 只有 subgroup-first 且子组切换时才输出优先标题。
        if not (bool_subgroup_first and bool_subgroup_changed):

            # 保持现有 section/subgroup 游标。
            return tuple_current_markers

        # 子组优先标题输出后，该子组成为当前端口组内的可见标题。
        str_current_subgroup = self._append_port_subgroup_first_header(  # 子组优先模式下的最新 subgroup
            port_marker_context.list_lines,  # 协议标题写入的端口行缓存
            port_marker_context.port,  # 触发子组优先标题的端口模型
            str_current_group,  # 当前端口所属的总线分组标题
        )

        # 子组切换后 section 在新子组内重新开始。
        return str_current_group, "", str_current_subgroup

    # section 边界负责端口方向或功能区标题。
    def _append_port_section_boundary(
        self,
        port_marker_context: PortMarkerContext,
        tuple_current_markers: tuple[str, str, str],
    ) -> tuple[str, str, str]:
        """
        输出端口 section 标题并维护普通布局下的 subgroup 游标。

        :param port_marker_context: 当前端口的分组输出上下文。
        :param tuple_current_markers: 子组优先阶段后的标题游标。
        :return: group、section 和 subgroup 游标。
        """

        # 当前总线游标不会在 section helper 中改变。
        str_current_group, str_current_section, str_current_subgroup = tuple_current_markers  # section 处理前标题游标

        # 当前端口对象决定 section 标题文本是否需要刷新。
        port_decl_port: PortDecl = port_marker_context.port  # 当前待渲染端口模型

        # 没有新 section 时保持现有游标。
        if not port_decl_port.section or port_decl_port.section == str_current_section:

            # 当前端口不需要额外 section 标题。
            return tuple_current_markers

        # subgroup-first 模式下 section 标题紧跟在协议子组标题之后。
        bool_subgroup_first = port_marker_context.tuple_flags[1]  # 当前端口的子组优先布局标记

        # section 标题输出后成为当前端口区的可见一级标题。
        str_current_section = self._append_port_section_header(  # 当前端口区最新 section 标题
            port_marker_context.list_lines,  # section 注释写入的端口行缓存
            port_decl_port,  # 当前端口提供 section 标题文本
            bool_subgroup_first,  # 子组优先布局影响 section 前空行
            str_current_section,  # helper 入口处的可见 section 标题
            port_marker_context.bool_rendered_port,  # section 标题前是否已有端口声明
        )

        # subgroup-first 布局保留已输出的 subgroup 游标。
        if bool_subgroup_first:

            # 返回新 section 和原 subgroup。
            return str_current_group, str_current_section, str_current_subgroup

        # 普通布局中 section 之后的 subgroup 需要重新确认。
        return str_current_group, str_current_section, ""

    # 普通 subgroup 边界负责 section 之后的协议子标题。
    def _append_port_plain_subgroup_boundary(
        self,
        port_marker_context: PortMarkerContext,
        tuple_current_markers: tuple[str, str, str],
    ) -> tuple[str, str, str]:
        """
        处理普通布局下的 subgroup 标题或游标清空。

        :param port_marker_context: 当前端口的分组输出上下文。
        :param tuple_current_markers: section 阶段后的标题游标。
        :return: group、section 和 subgroup 游标。
        """

        # 当前 group 和 section 只透传给端口主循环。
        str_current_group, str_current_section, str_current_subgroup = tuple_current_markers  # 普通子组处理前游标

        # 子组优先端口已在前一个 helper 输出协议标题。
        bool_subgroup_first = port_marker_context.tuple_flags[1]  # 是否已经采用子组优先流程

        # subgroup-first 布局已在前置 helper 中处理。
        if bool_subgroup_first:

            # 保持 subgroup-first 已输出的协议标题。
            return tuple_current_markers

        # 普通布局只有子组变化时才追加子标题。
        bool_subgroup_changed = port_marker_context.tuple_flags[2]  # 普通布局的子组刷新标记

        # section 文本单独命名，避免后续判定重复访问端口模型。
        str_port_section = port_marker_context.port.section  # 当前端口的 section 文本

        # 结构标签可以作为 formatter 布局标题，不属于对象级语义注释。
        bool_section_is_structured = self._comment_looks_like_structured_label(str_port_section)  # section 是否为结构标签

        # 非结构化 section 是归属当前端口的语义注释，必须直接贴近声明。
        bool_has_semantic_section = bool(str_port_section and not bool_section_is_structured)  # 是否携带对象级语义注释

        # 自动 subgroup 标题会隔断语义注释与端口声明，因此只更新游标而不输出标题。
        if bool_subgroup_changed and bool_has_semantic_section:

            # 记录当前 subgroup，避免同一端口区的后续声明重复触发标题判断。
            str_current_subgroup = port_marker_context.port.subgroup  # 已消费但未渲染的协议子组

            # 保持语义 section 紧邻它所描述的首个端口。
            return str_current_group, str_current_section, str_current_subgroup

        # 普通布局在当前 section 内追加协议子标题。
        if bool_subgroup_changed:

            # 普通布局标题输出后成为当前 section 内的可见 subgroup。
            str_current_subgroup = self._append_port_subgroup_header_line(  # 最新普通布局子组标题
                port_marker_context.list_lines,  # 子组标题写入的端口行缓存
                port_marker_context.port,  # 提供普通子组标题的端口模型
                port_marker_context.bool_rendered_port,  # 子组标题前是否已有端口声明
            )

            # 返回刷新后的普通 subgroup 游标。
            return str_current_group, str_current_section, str_current_subgroup

        # 无 subgroup 的端口会断开后续子组复用。
        if not port_marker_context.port.subgroup:

            # 清空 subgroup 游标，保证后续有子组端口可以重新输出标题。
            return str_current_group, str_current_section, ""

        # 有 subgroup 但未切换时保留原游标。
        return tuple_current_markers

    # 端口分组状态计算只读当前端口和上一轮游标。
    def _resolve_port_marker_flags(
        self,
        port: PortDecl,
        str_current_group: str,
        str_current_section: str,
        str_current_subgroup: str,
    ) -> tuple[bool, bool, bool, bool]:
        """
        计算当前端口是否触发 group、subgroup 或 section 边界。

        :param port: 当前端口模型。
        :param str_current_group: 最近输出的总线分组标题。
        :param str_current_section: 最近输出的 section 标题。
        :param str_current_subgroup: 最近输出的 subgroup 标题。
        :return: group、subgroup_first、subgroup 和 section 的切换标记。
        """

        # 端口 group 与上一 banner 不同表示进入新的总线区。
        bool_group_changed = bool(port.group and port.group != str_current_group)  # 端口进入新总线区

        # subgroup_first 模式把协议子标题放在 section 之前。
        bool_subgroup_first = port.subgroup_mode == "subgroup_first" and bool(port.subgroup)  # 协议子组前置模式

        # str_next_subgroup 在新分组中视为空，保证首个子组标题可见。
        str_next_subgroup = "" if bool_group_changed else str_current_subgroup  # 用于比较的上一子组

        # 当前端口子组与可见子组不同时需要输出子标题。
        bool_subgroup_changed = bool(port.subgroup and port.subgroup != str_next_subgroup)  # 协议子标题需刷新

        # str_next_section 在新分组或新子组内视为空，避免标题跨组复用。
        str_next_section = self._next_port_section_cursor(  # 本轮 section 比较基准
            bool_group_changed,  # 当前端口是否进入新总线分组
            bool_subgroup_first,  # 当前端口是否采用子组优先布局
            bool_subgroup_changed,  # 当前端口是否进入新协议子组
            str_current_section,  # 当前端口区的可见 section
        )  # 用于比较的上一 section

        # 当前端口 section 与比较基准不同才需要输出一级标题。
        bool_section_changed = bool(port.section and port.section != str_next_section)  # 一级端口说明需刷新

        # 调用方按 group、subgroup_first、subgroup、section 的顺序消费标记。
        return bool_group_changed, bool_subgroup_first, bool_subgroup_changed, bool_section_changed

    # section 游标在 group 或 subgroup_first 子组切换时需要重置。
    def _next_port_section_cursor(
        self,
        bool_group_changed: bool,
        bool_subgroup_first: bool,
        bool_subgroup_changed: bool,
        str_current_section: str,
    ) -> str:
        """
        返回用于比较当前端口 section 的上一标题。

        :param bool_group_changed: 当前端口是否切换总线分组。
        :param bool_subgroup_first: 当前端口是否采用子组优先布局。
        :param bool_subgroup_changed: 当前端口是否切换协议子组。
        :param str_current_section: 最近输出的 section 标题。
        :return: 参与本轮 section 比较的上一标题。
        """

        # 新分组或子组优先布局的新子组都会重新开始 section。
        if bool_group_changed or (bool_subgroup_first and bool_subgroup_changed):

            # 返回空标题使当前 section 可以重新输出。
            return ""

        # 未跨越分组边界时复用上一 section 标题。
        return str_current_section

    # 总线分组 banner 保留旧版单槽分组抑制逻辑。
    def _append_port_group_banner(
        self,
        list_lines: list[str],
        ports: list[PortDecl],
        index: int,
        port: PortDecl,
    ) -> str:
        """
        按需要输出端口总线分组 banner。

        :param list_lines: 正在累积的端口声明输出行。
        :param ports: 完整端口列表，用于识别单槽协议分组。
        :param index: 当前端口在端口列表中的下标。
        :param port: 当前端口模型。
        :return: 已切换到的总线分组标题。
        """

        # 单槽协议分组由子标题表达时抑制冗余 banner。
        if not self._should_suppress_port_group_banner(ports, index):

            # banner 文本统一由 banners 工具生成，保持宽度算法一致。
            list_lines.append(f"{self._indent(1)}{make_banner(port.group, 'bus')}")

        # 返回当前分组标题给调用方更新游标。
        return port.group

    # subgroup_first 布局先输出协议子组标题。
    def _append_port_subgroup_first_header(
        self,
        list_lines: list[str],
        port: PortDecl,
        str_current_group: str,
    ) -> str:
        """
        输出 subgroup_first 布局下的协议子组标题。

        :param list_lines: 正在累积的端口声明输出行。
        :param port: 当前端口模型。
        :param str_current_group: 当前总线分组标题。
        :return: 已输出的 subgroup 标题。
        """

        # 子组标题前保留视觉空行，延续旧 formatter 版式。
        if list_lines and list_lines[-1] != "":

            # 插入单个空行，避免 banner 与协议子标题粘连。
            list_lines.append("")

        # subgroup_first 标题需要带上当前 group 上下文。
        str_header = self._render_port_subgroup_header(port, str_current_group or port.group)  # 带分组上下文的子组标题

        # 子组标题使用一级缩进追加到端口声明区。
        list_lines.append(f"{self._indent(1)}{str_header}")

        # 调用方用这个标题阻止同一 subgroup 重复输出。
        return port.subgroup

    # section 标题表达端口声明区内的方向或功能片段。
    def _append_port_section_header(
        self,
        list_lines: list[str],
        port: PortDecl,
        bool_subgroup_first: bool,
        str_current_section: str,
        bool_rendered_port: bool,
    ) -> str:
        """
        输出端口 section 标题。

        :param list_lines: 正在累积的端口声明输出行。
        :param port: 当前端口模型。
        :param bool_subgroup_first: 当前端口是否采用子组优先布局。
        :param str_current_section: 最近输出的 section 标题。
        :param bool_rendered_port: 之前是否已输出过可见端口。
        :return: 已输出的 section 标题。
        """

        # 同一 subgroup 内连续 section 之间需要空行隔断。
        if bool_subgroup_first and str_current_section:

            # 空行插入仍由公共 helper 去重。
            self._ensure_single_blank_line_before_cluster(list_lines, bool_rendered_port)

        # section 注释保留原 formatter 的 `//标题` 形式。
        list_lines.append(f"{self._indent(1)}//{port.section}")

        # 返回当前 section 标题给调用方更新游标。
        return port.section

    # 普通布局下 subgroup 标题出现在 section 之后。
    def _append_port_subgroup_header_line(
        self,
        list_lines: list[str],
        port: PortDecl,
        bool_rendered_port: bool,
    ) -> str:
        """
        输出普通布局下的端口 subgroup 标题。

        :param list_lines: 正在累积的端口声明输出行。
        :param port: 当前端口模型。
        :param bool_rendered_port: 之前是否已输出过可见端口。
        :return: 已输出的 subgroup 标题。
        """

        # subgroup 标题和前一个端口声明之间保持单空行分隔。
        self._ensure_single_blank_line_before_cluster(list_lines, bool_rendered_port)

        # 普通 subgroup 注释保留 `//标题` 的旧输出样式。
        list_lines.append(f"{self._indent(1)}//{port.subgroup}")

        # 返回当前子组标题给调用方更新游标。
        return port.subgroup

    # 单条端口声明拼接集中处理属性、signed、位宽和 unpacked 维度。
    def _append_port_declaration_line(
        self,
        list_lines: list[str],
        ports: list[PortDecl],
        index: int,
        port: PortDecl,
    ) -> None:
        """
        追加一条结构化端口声明。

        :param list_lines: 正在累积的端口声明输出行。
        :param ports: 完整端口列表，用于决定尾逗号。
        :param index: 当前端口在端口列表中的下标。
        :param port: 当前端口模型。
        :return: 无返回值，声明行直接追加到 list_lines。
        """

        # 端口声明尾逗号完全沿用原列表位置。
        str_suffix = "," if index < len(ports) - 1 else ""  # 端口声明尾逗号

        # attributes 是声明前缀，存在时需附带空格。
        str_attributes = f"{port.attributes} " if port.attributes else ""  # 端口属性前缀

        # signed 修饰符只在原端口标记为 signed 时输出。
        str_signed = "signed " if port.signed else ""  # signed 声明片段

        # 规格化位宽空白，但不改写位宽表达式本身。
        str_normalized_width = self._normalize_decl_spec_spacing(port.width)  # 规范化位宽文本

        # 位宽为空时保持旧输出中 direction 后直接接 name 的行为。
        str_width = f"{str_normalized_width}" if str_normalized_width else ""  # 端口位宽片段

        # unpacked 维度原样接在端口名之后。
        str_unpacked = f"{port.unpacked}" if port.unpacked else ""  # unpacked 维度片段

        # 拼出单条端口声明，后续统一追加右侧注释。
        str_code = (
            f"{self._indent(1)}{str_attributes}{port.direction} "
            f"{str_signed}{str_width}{port.name}{str_unpacked}{str_suffix}"
        )  # 端口声明代码文本

        # 端口右侧注释沿用原始 comment，没有 comment 时使用兼容 fallback。
        list_lines.append(self._append_trailing_comment(str_code, port.comment, "port signal"))

    # always 区域渲染保留输出布局标签和控制节点展开逻辑。
    def _render_always_region(
        self, region: str, blocks: list[AlwaysBlock], output_target_layouts: dict[str, OutputSignalLayout] | None = None
    ) -> list[str]:
        """
        渲染 always_comb、always_ff 或普通 always 区域。

        :param region: REGION_TITLES 中的 always 区域键。
        :param blocks: 已解析出的 always 块列表。
        :param output_target_layouts: 可选的输出信号布局映射。
        :return: 区域标题、always 块内容和块间空行组成的输出行。
        """

        # 没有 always 块时不输出区域标题。
        if not blocks:

            # 空列表表示调用方无需插入该区域。
            return []

        # list_lines 以区域标题开头，后续逐块追加 always 文本。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES[region]}"]  # always 区域输出行

        # str_current_label 用于输出目标布局聚类，初始值不对应真实标签。
        str_current_label = "__start__"  # 当前输出布局标签

        # bool_rendered_block 标记是否至少输出过一个 always 块。
        bool_rendered_block = False  # always 块输出标记

        # 按解析顺序渲染 always 块，保持源文件中的过程块顺序。
        for block in blocks:

            # str_label 是当前 always 块归属的输出布局标签。
            str_label = self._format_output_layout_label(  # always 聚类标签文本
                self._resolve_output_always_layout(  # always 块到输出布局的解析结果
                    block,  # 用于推导输出目标的 always 块
                    output_target_layouts,  # 输出信号布局查找表
                )  # 当前 always 块关联的布局对象
            )  # always 块布局分组标签

            # 只有调用方提供输出布局时才插入布局聚类注释。
            if output_target_layouts is not None:

                # 聚类 helper 会按标签变化插入必要空行和前导注释。
                str_current_label = self._begin_output_label_cluster(  # 输出布局聚类后的当前标签
                    list_lines,  # always 布局标签插入目标
                    str_label,  # 当前 always 归属标签
                    block.leading_comments,  # 标签切换时可复用的前导注释
                    str_current_label,  # 上一个可见输出布局标签
                    bool_rendered_block,  # 当前区域是否已有 always 块
                )

            # always 块自身的前导注释保持一级缩进。
            list_lines.extend(self._render_leading_comments(block.leading_comments, 1))

            # str_header 统一补齐 begin，避免后续控制节点脱离块作用域。
            str_header = self._normalize_always_header(block.header)  # 规范化 always 头部

            # 已经带 begin 的 header 直接保留，否则追加 begin。
            if not str_header.endswith("begin"):

                # 压缩写法的 always 头部需要补齐 begin。
                str_header = f"{str_header}begin"  # 带 begin 的 always 头部

            # 输出 always 头部行。
            list_lines.append(f"{self._indent(1)}{str_header}")

            # 结构化控制节点优先渲染，raw 行作为兼容退路。
            if block.nodes:

                # 控制节点在 always 内使用二级缩进。
                list_lines.extend(self._render_control_nodes(block.nodes, 2))

            # 没有结构化节点时回退到 raw 行渲染。
            else:

                # raw 行保持解析不到控制树时的旧渲染路径。
                list_lines.extend(self._render_raw_block_lines(block.lines, 2))

            # always 块闭合保持一级缩进。
            list_lines.append(f"{self._indent(1)}end")

            # always 块之间保留空行。
            list_lines.append("")

            # 标记本区域已产生可见 always 块。
            bool_rendered_block = True  # always 区域已有实际块输出

        # 理论上 blocks 非空就会渲染，保留旧防御分支。
        if not bool_rendered_block:

            # 没有可见块时不返回孤立区域标题。
            return []

        # 返回完整 always 区域输出行。
        return list_lines

    # generate 区域渲染负责包裹 generate/endgenerate 边界。
    def _render_generate_region(self, generate_blocks: list[GenerateBlock]) -> list[str]:
        """
        渲染 module body 中的 generate 区域。

        :param generate_blocks: 已解析出的 generate 块列表。
        :return: 带区域标题和 generate 包裹行的输出列表。
        """

        # generate 列表为空时不能留下孤立的 generate 区域 banner。
        if not generate_blocks:

            # 调用方据此直接跳过 generate 分区拼接。
            return []

        # generate 分区先输出区域标题，再输出成对的 generate 关键字。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES['generate_block']}"]  # generate 分区标题行集合

        # 按解析顺序渲染每个 generate 块。
        for generate_block in generate_blocks:

            # generate 前导注释位于 generate 关键字之前。
            list_lines.extend(self._render_leading_comments(generate_block.leading_comments, 1))

            # generate 关键字单独成行，保持 Verilog 块边界清晰。
            list_lines.append(f"{self._indent(1)}generate")

            # generate 内部控制节点使用二级缩进。
            list_lines.extend(self._render_control_nodes(generate_block.nodes, 2))

            # endgenerate 与 generate 关键字缩进对齐。
            list_lines.append(f"{self._indent(1)}endgenerate")

            # 多个 generate 块之间保留空行。
            list_lines.append("")

        # generate 区域末尾保留空行，隔开后续 body 分区。
        list_lines.append("")

        # 调用方把这些行放在声明区之后、实例区之前。
        return list_lines

    # function 区域保持原始 raw 行，不尝试重排函数内部语句。
    def _render_function_region(self, function_blocks: list[FunctionBlock]) -> list[str]:
        """
        渲染 Verilog function 区域。

        :param function_blocks: 已解析出的 function 块列表。
        :return: 带区域标题和原始 function 行的输出列表。
        """

        # function 列表为空时不能输出空的函数分区。
        if not function_blocks:

            # 调用方会继续检查 task、instance 等后续分区。
            return []

        # function 分区以固定标题开头，函数体随后按 raw 行追加。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES['function_block']}"]  # 函数定义分区输出

        # function 内容以 raw 行渲染，避免改写用户函数体。
        for block in function_blocks:

            # function 前导注释保留在函数声明之前。
            list_lines.extend(self._render_leading_comments(block.leading_comments, 1))

            # raw 行路径保留函数内部原始语句结构。
            list_lines.extend(self._render_raw_block_lines(block.lines, 1))

            # 相邻 function 之间保留空行，避免 endfunction 与下一声明粘连。
            list_lines.append("")

        # function 分区末尾额外空行用于隔离后续 Verilog 分区。
        list_lines.append("")

        # 调用方按原始函数顺序拼接这一段。
        return list_lines

    # task 区域与 function 一样保持 raw 行渲染。
    def _render_task_region(self, task_blocks: list[TaskBlock]) -> list[str]:
        """
        渲染 Verilog task 区域。

        :param task_blocks: 已解析出的 task 块列表。
        :return: 带区域标题和原始 task 行的输出列表。
        """

        # task 列表为空时不产生任务分区标题。
        if not task_blocks:

            # 返回空列表让调用方保持当前 body 分区顺序。
            return []

        # task 分区以固定标题开头，任务体随后按 raw 行追加。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES['task_block']}"]  # 任务定义分区输出

        # task 内容以 raw 行渲染，保留手写任务体。
        for block in task_blocks:

            # task 前导注释保留在 task 声明之前。
            list_lines.extend(self._render_leading_comments(block.leading_comments, 1))

            # raw 行路径避免重排 task 内部时序语句。
            list_lines.extend(self._render_raw_block_lines(block.lines, 1))

            # 相邻 task 之间留出边界，避免两个任务定义视觉粘连。
            list_lines.append("")

        # task 分区末尾隔开后续实例或过程块内容。
        list_lines.append("")

        # 调用方按 task 原始顺序拼接这一段。
        return list_lines

    # initial 区域根据解析结果选择控制树或 raw 行渲染。
    def _render_initial_region(self, initial_blocks: list[InitialBlock], region: str) -> list[str]:
        """
        渲染 initial 或相关初始化区域。

        :param initial_blocks: 已解析出的 initial 块列表。
        :param region: REGION_TITLES 中的初始化区域键。
        :return: 区域标题和 initial 块内容组成的输出行。
        """

        # initial 列表为空时不产生初始化分区标题。
        if not initial_blocks:

            # 调用方会继续拼接 always、instance 等其他分区。
            return []

        # list_lines 以调用方指定区域标题开始。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES[region]}"]  # 初始化分区标题行集合

        # 按解析顺序渲染 initial 块。
        for block in initial_blocks:

            # initial 块前导注释保持一级缩进。
            list_lines.extend(self._render_leading_comments(block.leading_comments, 1))

            # initial 头部先规范化语句空白，再按一级缩进输出。
            list_lines.append(f"{self._indent(1)}{self._normalize_statement_line(block.header)}")

            # 结构化节点优先渲染，raw 行作为兼容路径。
            if block.nodes:

                # initial 结构化节点缩进到过程块内部。
                list_lines.extend(self._render_control_nodes(block.nodes, 2))

            # 没有结构化节点时保留 initial 块原始语句顺序。
            else:

                # 无控制树时保持 raw 行顺序。
                list_lines.extend(self._render_raw_block_lines(block.lines, 2))

            # initial 块统一补齐 end。
            list_lines.append(f"{self._indent(1)}end")

            # 相邻 initial 块之间保留空行，避免两个 end/header 粘连。
            list_lines.append("")

        # initial 分区末尾隔开后续过程块或实例区。
        list_lines.append("")

        # 调用方把这些初始化块放回 module body。
        return list_lines

    # 实例头部功能注释只在归属明确且没有既有前导说明时提升。
    def _promote_instance_header_comment(self, instance: InstanceBlock) -> InstanceBlock:
        """
        把可确认属于实例头的行尾功能注释提升为前导纯注释。

        :param instance: parser 产出的实例块。
        :return: 可安全提升时返回替换后的实例块，否则返回原对象。
        """

        # 已有前导说明时无法安全判断两条注释的相对语义。
        if instance.leading_comments:

            # 保留原实例对象，避免重排用户已经建立的说明顺序。
            return instance

        # 按原始换行拆分实例文本，只检查第一个可见声明行。
        list_instance_lines = instance.text.splitlines()  # 待检查的实例原始行

        # 空白前缀不应影响实例头识别。
        for int_line_index, str_instance_line in enumerate(list_instance_lines):

            # 空行继续留在原位置，并寻找第一个可见实例行。
            if not str_instance_line.strip():

                # 跳过实例文本开头的空白行。
                continue

            # 拆开首个可见行的代码和行尾注释。
            str_code, str_comment = self._split_comment(str_instance_line)  # 实例头代码与说明

            # 只提升明确位于实例端口左括号后的行尾说明。
            if not str_comment or not str_code.rstrip().endswith("("):

                # 非实例头说明保持原位，不猜测参数或端口注释归属。
                return instance

            # 从实例头移除已确认可提升的行尾注释。
            list_instance_lines[int_line_index] = str_code.rstrip()  # 已移除可提升说明的实例头

            # 返回不可变替换结果，保持 parser 原对象及其他字段不变。
            return replace(
                instance,
                text="\n".join(list_instance_lines),
                leading_comments=[f"//{str_comment}"],
            )

        # 没有可见实例行时保持原对象。
        return instance

    # 实例区域先渲染结构化实例，再追加无法结构化的 raw block。
    def _render_instance_region(self, instances: list[InstanceBlock], raw_blocks: list[RawBlock]) -> list[str]:
        """
        渲染 module body 中的实例化区域。

        :param instances: 已解析出的结构化实例块。
        :param raw_blocks: 保留为原始文本的实例相关块。
        :return: 实例区域标题和所有实例/raw block 的输出行。
        """

        # 实例和 raw block 都为空时不产生实例分区标题。
        if not instances and not raw_blocks:

            # 返回空列表让调用方跳过实例分区拼接。
            return []

        # list_lines 以实例区域标题开始。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES['instance_block']}"]  # 实例区域输出行

        # 先输出结构化实例，保持解析后的主要实例顺序。
        for instance in instances:

            # 提升归属明确的实例头功能注释，不修改 parser 持有的原对象。
            instance_block_render: InstanceBlock = self._promote_instance_header_comment(instance)  # 当前实例的安全渲染副本

            # 实例前导注释保持一级缩进。
            list_lines.extend(self._render_leading_comments(instance_block_render.leading_comments, 1))

            # 实例块按 canonical 或 legacy 路径渲染。
            list_lines.extend(self._render_instance_block(instance_block_render, 1))

            # 相邻结构化实例之间保留空行，便于区分模块实例。
            list_lines.append("")

        # raw block 追加在结构化实例之后，保留旧分区顺序。
        for block in raw_blocks:

            # raw block 前导注释保持一级缩进。
            list_lines.extend(self._render_leading_comments(block.leading_comments, 1))

            # raw block 内容不做实例关联重排。
            list_lines.extend(self._render_raw_block_lines(block.lines, 1))

            # 相邻 raw block 之间保留空行，避免原始片段粘连。
            list_lines.append("")

        # 返回完整实例区域输出行。
        return list_lines

    # 控制节点列表渲染只负责顺序拼接，单节点细节交给分发 helper。
    def _render_control_nodes(self, nodes: list[ControlNode], indent_level: int) -> list[str]:
        """
        渲染过程块或 generate 块内部的控制节点列表。

        :param nodes: 已解析出的控制节点序列。
        :param indent_level: 当前节点列表使用的缩进层级。
        :return: 每个控制节点渲染后的 Verilog 行。
        """

        # list_lines 保持节点渲染后的原始顺序。
        list_lines: list[str] = []  # 控制节点输出行

        # 控制节点必须按 parser 产出的顺序逐个展开。
        for int_node_index, node in enumerate(nodes):

            # 纯注释只有紧邻实例 statement 时才属于实例说明布局。
            bool_comment_precedes_instance = False  # 当前 comment 是否直接说明后续实例

            # 同级下一个节点提供纯注释的唯一安全归属边界。
            if node.kind == "comment" and int_node_index + 1 < len(nodes):

                # 读取当前 comment 后的直接兄弟节点。
                control_node_next = nodes[int_node_index + 1]  # 注释后的同级控制节点

                # 只有 statement 节点才可能是实例声明。
                if control_node_next.kind == "statement":

                    # 复用实例渲染识别入口，避免维护第二套实例起点规则。
                    list_instance_preview = self._render_control_instance_statement(  # 后续 statement 的可选实例输出
                        control_node_next.text,  # 紧邻 comment 的 statement 文本
                        indent_level,  # 当前控制树缩进层级
                    )

                    # 非 None 结果确认当前纯注释紧邻真实实例。
                    bool_comment_precedes_instance = list_instance_preview is not None  # 实例说明布局标志

            # VG063 要求实例说明上方恰有一个空行。
            if bool_comment_precedes_instance and (not list_lines or list_lines[-1] != ""):

                # 插入唯一空行，兼容首次提升和二次控制树解析。
                list_lines.append("")

            # 紧邻实例的同级纯注释已经承担实例前导说明职责。
            bool_has_instance_leading_comment = (  # 当前 statement 是否已有独立前导说明
                node.kind == "statement"  # 只有 statement 节点可能承载实例声明
                and int_node_index > 0  # 首个节点前不存在可归属的兄弟注释
                and nodes[int_node_index - 1].kind == "comment"  # 直接前驱必须是纯注释节点
            )

            # 已有实例说明时直接走保守实例渲染，禁止再次提升行尾说明。
            if bool_has_instance_leading_comment:

                # 带前导说明的 statement 仍需先确认自身确实是实例。
                list_instance_with_leading_comment = self._render_control_instance_statement(  # 保守实例渲染结果
                    node.text,  # 当前 statement 的实例候选文本
                    indent_level,  # 沿用该 generate 分支的实例输出层级
                    has_leading_comment=True,  # 阻止重排第二条功能说明
                )

                # 识别成功后直接消费保守结果，避免通用分发再次提升注释。
                if list_instance_with_leading_comment is not None:

                    # 保留已有前导说明和实例头行尾说明的原始相对归属。
                    list_lines.extend(list_instance_with_leading_comment)

                    # 当前 statement 已经完成渲染，不再进入通用节点分发。
                    continue

            # 单节点 helper 返回一个完整结构片段，调用方只负责拼接。
            list_lines.extend(self._render_control_node(node, indent_level))

        # 返回当前层级的全部控制节点输出。
        return list_lines

    # 单个控制节点按 kind 分发到具体渲染 helper。
    def _render_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染一个 ControlNode。

        :param node: 当前控制节点。
        :param indent_level: 当前节点使用的缩进层级。
        :return: 当前节点对应的 Verilog 输出行。
        """

        # dict_renderers 固定 parser kind 与渲染函数的绑定，避免分支链重复判断。
        dict_renderers: dict[str, Callable[[ControlNode, int], list[str]]] = {  # parser kind 到渲染函数的静态映射
            "statement": self._render_statement_control_node,  # statement 可能包含普通赋值或内嵌实例
            "comment": self._render_comment_control_node,  # comment 保留 parser 捕获的独立注释
            "if": self._render_if_node,  # if 节点按条件块规则缩进
            "case": self._render_case_node,  # case 节点展开 selector 和分支项
            "loop": self._render_loop_control_node,  # loop 节点覆盖 for、while 和 repeat
            "block": self._render_block_control_node,  # block 节点保留显式 begin/end 层级
            "always_block": self._render_always_control_node,  # always_block 来源于 generate 内过程块
            "generate": self._render_generate_control_node,  # generate 节点递归渲染内部结构
            "initial_block": self._render_initial_control_node,  # initial_block 保留仿真初始化阶段
            "function_block": self._render_raw_control_node,  # function_block 走原始文本保守输出
            "task_block": self._render_raw_control_node,  # task_block 保留任务体源码形态
            "else": self._render_else_control_node,  # else 节点补齐独立分支渲染
        }

        # callable_renderer 是当前 kind 命中的节点渲染函数。
        func_renderer: Callable[[ControlNode, int], list[str]] | None = dict_renderers.get(node.kind)  # 当前节点 kind 对应的渲染函数

        # 已知节点类型交给分发表中的 helper。
        if func_renderer is not None:

            # 返回命中 kind 的结构化渲染结果。
            return func_renderer(node, indent_level)

        # 未识别 kind 不输出内容，保持旧渲染器的静默兼容行为。
        return []

    # comment 控制节点按普通语句行输出。
    def _render_comment_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染 parser 捕获的独立注释控制节点。

        :param node: 当前 comment 节点。
        :param indent_level: 当前节点使用的缩进层级。
        :return: 带目标缩进的单行注释。
        """

        # 注释按普通语句行渲染，保留当前缩进层级。
        return [self._render_statement_line(node.text, indent_level)]

    # function/task 控制节点按 raw block 输出。
    def _render_raw_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染需要保留原始文本顺序的控制节点。

        :param node: function_block 或 task_block 控制节点。
        :param indent_level: 当前节点使用的缩进层级。
        :return: 按 raw 行渲染后的输出行。
        """

        # raw 行路径保留函数和任务体的原始文本顺序。
        return self._render_raw_block_lines(node.text.splitlines(), indent_level)

    # statement 控制节点先尝试识别实例化，否则按普通语句逐行输出。
    def _render_statement_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染 ControlNode 中的 statement 节点。

        :param node: statement 类型控制节点。
        :param indent_level: 当前语句使用的缩进层级。
        :return: 实例化块或普通语句行。
        """

        # list_rendered_instance 保存从 statement 文本中识别出的实例化渲染结果。
        list_rendered_instance = self._render_control_instance_statement(  # statement 内嵌实例输出
            node.text,  # statement 节点原始文本
            indent_level,  # 当前控制语句缩进层级
        )

        # statement 文本如果是实例化语句，就交给实例渲染路径。
        if list_rendered_instance is not None:

            # 返回实例化渲染结果，避免再按普通语句拆行。
            return list_rendered_instance

        # list_lines 收集普通 statement 的非空规范化行。
        list_lines: list[str] = []  # 普通控制语句输出行

        # statement 文本可能包含多行，需要逐行规范化。
        for raw_line in node.text.splitlines():

            # str_normalized 是去掉外围空白后的 Verilog 单行语句。
            str_normalized = self._normalize_statement_line(raw_line.strip())  # 规范化控制语句行

            # 空行不进入过程块输出。
            if str_normalized:

                # 普通语句行使用当前控制节点缩进。
                list_lines.append(self._render_statement_line(str_normalized, indent_level))

        # 返回普通 statement 的渲染行。
        return list_lines

    # loop 控制节点保留 parser 捕获的 header 和 label。
    def _render_loop_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染 for/while 等循环控制节点。

        :param node: loop 类型控制节点。
        :param indent_level: 循环头使用的缩进层级。
        :return: loop begin/end 包裹后的输出行。
        """

        # str_suffix 保留循环标签，没有标签时仅输出 begin。
        str_suffix = f"begin:{node.label}" if node.label else "begin"  # 循环 begin 后缀

        # list_lines 从循环头开始，随后追加循环体子节点。
        list_lines = [f"{self._indent(indent_level)}{node.header}{str_suffix}"]  # loop 头部和主体容器

        # 循环体子节点缩进加深一级。
        list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # 循环尾部 end 与循环头缩进对齐。
        list_lines.append(f"{self._indent(indent_level)}end")

        # 调用方直接拼接这组 loop 行到父控制块。
        return list_lines

    # 显式 block 节点输出 begin/end，并保留可选 label。
    def _render_block_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染显式 begin/end 控制块。

        :param node: block 类型控制节点。
        :param indent_level: begin 使用的缩进层级。
        :return: begin/end 包裹后的输出行。
        """

        # str_suffix 只在 block 带标签时追加。
        str_suffix = f":{node.label}" if node.label else ""  # block 标签后缀

        # list_lines 从 begin 行开始收集显式块内容。
        list_lines = [f"{self._indent(indent_level)}begin{str_suffix}"]  # 显式 begin 块行集合

        # block 子节点缩进加深一级。
        list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # block 尾部 end 与 begin 缩进对齐。
        list_lines.append(f"{self._indent(indent_level)}end")

        # 调用方需要完整 begin/end 包裹行保持层级闭合。
        return list_lines

    # generate 内嵌 always 节点需要在当前层级补齐 begin/end。
    def _render_always_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染嵌套在控制树中的 always 块。

        :param node: always_block 类型控制节点。
        :param indent_level: always 头部使用的缩进层级。
        :return: always begin/end 包裹后的输出行。
        """

        # str_header 统一 always 头部空白并准备 begin 后缀。
        str_header = self._normalize_always_header(node.header)  # 规范化 always 控制头

        # 控制树中的 always 头部必须带 begin。
        if not str_header.endswith("begin"):

            # 压缩 header 需要补齐 begin 以包裹 children。
            str_header = f"{str_header}begin"  # 带 begin 的 always 控制头

        # list_lines 从 always 头部开始收集控制片段。
        list_lines = [f"{self._indent(indent_level)}{str_header}"]  # 嵌套 always 块行集合

        # always 内部语句相对过程块头部缩进一级。
        list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # always 结束行回到过程块头部缩进。
        list_lines.append(f"{self._indent(indent_level)}end")

        # 父节点通过这组行嵌入 generate 或 block 内部。
        return list_lines

    # 嵌套 generate 节点保留 generate/endgenerate 关键字。
    def _render_generate_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染控制树内部的 generate 节点。

        :param node: generate 类型控制节点。
        :param indent_level: generate 关键字使用的缩进层级。
        :return: generate/endgenerate 包裹后的输出行。
        """

        # list_lines 从 generate 关键字开始。
        list_lines = [f"{self._indent(indent_level)}generate"]  # 内嵌生成域起始行

        # generate 内部声明和控制节点缩进一级。
        list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # endgenerate 回到 generate 关键字的列。
        list_lines.append(f"{self._indent(indent_level)}endgenerate")

        # 父控制块使用这组行保留 generate 作用域。
        return list_lines

    # initial 控制节点在 children 为空时回退到 raw text。
    def _render_initial_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染控制树内部的 initial 块。

        :param node: initial_block 类型控制节点。
        :param indent_level: initial 头部使用的缩进层级。
        :return: initial begin/end 包裹后的输出行。
        """

        # str_header 保留 initial 头部的规范化语句文本。
        str_header = self._normalize_statement_line(node.header)  # 控制树 initial 头部

        # list_lines 从 initial 头部开始。
        list_lines = [f"{self._indent(indent_level)}{str_header}"]  # 内嵌初始化过程起始行

        # children 存在时优先使用结构化控制树。
        if node.children:

            # initial 的结构化语句缩进到过程块内部。
            list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # 没有 children 但保留 raw text 时按原始行渲染。
        elif node.text:

            # raw text 使用 initial 内部缩进层级。
            list_lines.extend(self._render_raw_block_lines(node.text.splitlines(), indent_level + 1))

        # initial 结束行闭合到过程块头部。
        list_lines.append(f"{self._indent(indent_level)}end")

        # 父节点用这组行表达 initial 的完整作用域。
        return list_lines

    # else 控制节点输出 else begin/end，并保留 label。
    def _render_else_control_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染独立 else 控制节点。

        :param node: else 类型控制节点。
        :param indent_level: else 使用的缩进层级。
        :return: else begin/end 包裹后的输出行。
        """

        # str_suffix 只在 else 节点带标签时追加。
        str_suffix = f":{node.label}" if node.label else ""  # 独立 else 的 block 标签

        # list_lines 从 else begin 行开始，确保独立 else 也有显式作用域。
        list_lines = [f"{self._indent(indent_level)}else begin{str_suffix}"]  # 独立 else 的作用域开头

        # else 内部语句缩进到 begin/end 之间。
        list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # else 尾部 end 与 else 关键字缩进对齐。
        list_lines.append(f"{self._indent(indent_level)}end")

        # 父 if 链外的 else 节点通过这组行保持完整块形态。
        return list_lines

    # 控制语句中的实例化片段复用实例块渲染路径。
    def _render_control_instance_statement(
        self,
        text: str,
        indent_level: int,
        *,
        has_leading_comment: bool = False,
    ) -> list[str] | None:
        """
        尝试把控制语句文本识别并渲染为实例化块。

        :param text: statement 控制节点中的原始文本。
        :param indent_level: 实例化块使用的缩进层级。
        :param has_leading_comment: 同级前驱是否已经提供实例功能说明。
        :return: 识别成功时返回实例化输出行，否则返回 `None`。
        """

        # list_normalized_lines 保存非空语句行，供实例起始判断使用。
        list_normalized_lines: list[str] = []  # 控制语句规范化行

        # 控制节点文本可能包含多行实例化写法。
        for raw_line in text.splitlines():

            # str_stripped 是当前原始行去除外围空白后的文本。
            str_stripped = raw_line.strip()  # 控制语句原始行内容

            # 空行不参与实例化起始判断。
            if str_stripped:

                # 规范化后的语句行保留给实例 parser。
                list_normalized_lines.append(self._normalize_statement_line(str_stripped))

        # 没有可见语句行时不能构造实例。
        if not list_normalized_lines:

            # 返回 None 让调用方继续按普通 statement 处理。
            return None

        # str_first_line 用于判断第一行是否像模块实例开头。
        str_first_line = list_normalized_lines[0]  # 实例候选首行

        # str_next_line 提供跨行实例声明的第二行辅助判断。
        str_next_line = list_normalized_lines[1] if len(list_normalized_lines) > 1 else ""  # 实例候选次行

        # 非实例化语句交回普通 statement 渲染路径。
        if not self._is_instance_start_line(str_first_line, str_next_line):

            # 返回 None 表示没有识别到实例化结构。
            return None

        # instance_block 是从规范化 statement 文本解析出的实例模型。
        instance_block_instance_block: InstanceBlock = self._parse_instance_block(  # 控制语句内实例模型
            "\n".join(list_normalized_lines)  # 规范化后的多行实例文本
        )

        # 已有兄弟前导说明时保留行尾注释，否则允许安全提升唯一说明。
        instance_block_render: InstanceBlock = (  # 控制树实例渲染副本
            instance_block_instance_block  # 双注释实例保持 parser 原始归属
            if has_leading_comment  # 同级纯注释已经承担前导说明职责
            else self._promote_instance_header_comment(instance_block_instance_block)  # 单行尾说明允许提升
        )

        # list_lines 收集控制树实例的可选说明和实例本体。
        list_lines: list[str] = []  # 控制树实例输出行

        # 首次提升出的实例说明需要满足 VG063 的上方单空行。
        if instance_block_render.leading_comments:

            # 空行作为实例说明簇的固定边界。
            list_lines.append("")

            # 前导说明与实例块保持同一控制层级。
            list_lines.extend(self._render_leading_comments(instance_block_render.leading_comments, indent_level))

        # 实例本体沿用统一 canonical 或 legacy 渲染路径。
        list_lines.extend(self._render_instance_block(instance_block_render, indent_level))

        # 返回包含提升注释和实例本体的完整片段。
        return list_lines

    # 条件分支节点输出 begin、children 和可选 alternate。
    def _render_if_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染 if 控制节点。

        :param node: if 类型控制节点。
        :param indent_level: if 头部使用的缩进层级。
        :return: if/else-if/else 链对应的输出行。
        """

        # str_header 是带 begin 后缀的条件分支头部。
        str_header = self._format_if_header(node.header, node.label)  # 条件分支头部文本

        # list_lines 从规范化后的条件头部开始。
        list_lines = [f"{self._indent(indent_level)}{str_header}"]  # 条件分支节点输出行

        # 条件主分支 children 使用下一层缩进。
        list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # alternate 存在时追加 else-if 或 else 分支。
        if node.alternate:

            # alternate_node 只取 parser 约定的第一个 alternate 节点。
            alternate_node = node.alternate[0]  # 条件节点 alternate 分支

            # alternate helper 负责 else-if 链和普通 else 块。
            self._append_if_alternate(list_lines, alternate_node, indent_level)

        # 没有 alternate 时直接闭合 if 主分支。
        else:

            # 单分支 if 的 end 与 if 头部缩进对齐。
            list_lines.append(f"{self._indent(indent_level)}end")

        # 调用方需要 if 主分支和 alternate 分支一起落入父块。
        return list_lines

    # else-if 链以 `end else if` 形式延续上一层 if。
    def _render_else_if_chain(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染 else-if 链中的一个 if 节点。

        :param node: alternate 中的 if 控制节点。
        :param indent_level: else-if 头部使用的缩进层级。
        :return: 当前 else-if 分支及其后续 alternate 的输出行。
        """

        # list_lines 以 `end else if` 头部延续上一分支。
        list_lines = [  # 链式条件分支行集合
            f"{self._indent(indent_level)}end else {self._format_if_header(node.header, node.label)}"  # 闭合上一分支并打开当前条件
        ]

        # else-if 主体 children 使用下一层缩进。
        list_lines.extend(self._render_control_nodes(node.children, indent_level + 1))

        # 后续 alternate 继续延长 else-if 链或收束为 else。
        if node.alternate:

            # alternate_node 是当前 else-if 后续的唯一 alternate。
            alternate_node = node.alternate[0]  # else-if 后续分支

            # 复用 alternate helper 保持普通 if 和 else-if 的闭合方式一致。
            self._append_if_alternate(list_lines, alternate_node, indent_level)

        # 链尾没有 alternate 时闭合当前分支。
        else:

            # 链尾 end 与 else-if 头部缩进对齐。
            list_lines.append(f"{self._indent(indent_level)}end")

        # 返回当前 else-if 片段。
        return list_lines

    # alternate 分支 helper 统一处理 else-if 和普通 else。
    def _append_if_alternate(self, list_lines: list[str], alternate_node: ControlNode, indent_level: int) -> None:
        """
        向 if/else-if 输出行追加 alternate 分支。

        :param list_lines: 正在累积的 if 链输出行。
        :param alternate_node: parser 捕获到的 alternate 节点。
        :param indent_level: alternate 头部使用的缩进层级。
        :return: 无返回值，结果直接追加到 list_lines。
        """

        # alternate 为 if 时保持 `end else if` 链式输出。
        if alternate_node.kind == "if":

            # else-if 递归 helper 会自行闭合链尾。
            list_lines.extend(self._render_else_if_chain(alternate_node, indent_level))

        # 普通 else 节点输出 `end else begin` 包裹。
        else:

            # str_suffix 保留 else 节点可选 label。
            str_suffix = f":{alternate_node.label}" if alternate_node.label else ""  # else 分支标签后缀

            # else 头部同时闭合上一分支并打开新分支。
            list_lines.append(f"{self._indent(indent_level)}end else begin{str_suffix}")

            # else 子节点缩进到新打开的 begin/end 内。
            list_lines.extend(self._render_control_nodes(alternate_node.children, indent_level + 1))

            # else 尾部 end 与 if 头部缩进对齐。
            list_lines.append(f"{self._indent(indent_level)}end")

    # case 节点只渲染含有效控制内容的 case item。
    def _render_case_node(self, node: ControlNode, indent_level: int) -> list[str]:
        """
        渲染 case 控制节点。

        :param node: case 类型控制节点。
        :param indent_level: case 头部使用的缩进层级。
        :return: case/endcase 包裹后的输出行。
        """

        # list_visible_items 过滤掉只含注释或空内容的 case item。
        list_visible_items = [
            item  # 保留仍有可见控制内容的 case item
            for item in node.items  # 遍历 parser 捕获到的全部 case item
            if self._has_noncomment_control_content(item.children)  # 仅保留有非注释内容的分支
        ]  # 可渲染 case item 列表

        # 没有可见分支时整个 case 不输出。
        if not list_visible_items:

            # 返回空列表，避免生成只有 case/endcase 的空结构。
            return []

        # list_lines 从规范化后的 case 头部开始。
        list_lines = [f"{self._indent(indent_level)}{self._normalize_statement_line(node.header)}"]  # case 节点输出行

        # 每个可见 case item 都输出 begin/end 包裹。
        for item in list_visible_items:

            # str_suffix 决定 case 标签后是否打开带命名 label 的 begin 块。
            str_suffix = f"begin:{item.block_label}" if item.block_label else "begin"  # case 分支作用域声明

            # VG063 要求分支前导注释上方保留唯一空行。
            if item.leading_comments and list_lines[-1] != "":

                # 空字符串渲染为不带缩进的规范空行，第二次 formatter 不会继续累加。
                list_lines.append("")

            # case item 前导注释与标签使用同一级缩进并保持源码顺序。
            list_lines.extend(self._render_leading_comments(item.leading_comments, indent_level + 1))

            # case item 标签缩进比 case 头部深一级。
            list_lines.append(f"{self._indent(indent_level + 1)}{item.label}:{str_suffix}")

            # case item children 缩进再加深一级。
            list_lines.extend(self._render_control_nodes(item.children, indent_level + 2))

            # case item 的 end 与 item 标签缩进对齐。
            list_lines.append(f"{self._indent(indent_level + 1)}end")

        # endcase 与 case 头部缩进对齐。
        list_lines.append(f"{self._indent(indent_level)}endcase")

        # 调用方用这些行替换原始 case 控制节点。
        return list_lines

    # 条件规范化前先剥离不改变语义的外层括号。
    def _strip_redundant_outer_parens(self, expression: str) -> str:
        """
        移除表达式最外层的冗余括号。

        :param expression: 待处理的 Verilog 表达式文本。
        :return: 去掉冗余外层括号后的表达式。
        """

        # str_normalized 保存当前正在检查的表达式文本。
        str_normalized = expression.strip()  # 去除外围空白后的表达式

        # 只有首尾都是括号时才可能存在可剥离外层。
        while str_normalized.startswith("(") and str_normalized.endswith(")"):

            # int_close_index 定位首个左括号匹配到的右括号。
            int_close_index = self._find_matching_paren_in_text(str_normalized, 0)  # 外层括号匹配位置

            # 匹配括号不在末尾时说明外层括号不是整体包裹。
            if int_close_index != len(str_normalized) - 1:

                # 保留原表达式，避免剥掉必要的局部括号。
                break

            # str_candidate 是去掉一层括号后的候选表达式。
            str_candidate = str_normalized[1:-1].strip()  # 去掉一层括号后的表达式

            # 空括号不能继续剥离为有效条件。
            if not str_candidate:

                # 终止循环并保留上一轮有效文本。
                break

            # 候选表达式成为下一轮括号检查对象。
            str_normalized = str_candidate  # 当前可继续检查的表达式

        # 返回最终保留下来的表达式。
        return str_normalized

    # 简单条件 token 可按信号位宽改写成显式比较。
    def _normalize_simple_if_condition_tokens(self, tokens: list[str]) -> str | None:
        """
        将单信号或单取反信号条件规范化为显式比较。

        :param tokens: 条件表达式 token 序列。
        :return: 可识别简单条件时返回规范化表达式，否则返回 `None`。
        """

        # str_name 保存被判断的信号名。
        str_name = ""  # 简单条件信号名

        # str_operator 保存一元取反操作符。
        str_operator = ""  # 简单条件取反操作符

        # 单 token 条件表示直接判断信号真值。
        if len(tokens) == 1 and re.fullmatch(r"[A-Za-z_]\w*", tokens[0]):

            # 记录无取反操作的信号名。
            str_name = tokens[0]  # 单 token 条件信号名

        # 两 token 条件只接受 `~sig` 或 `!sig`。
        elif len(tokens) == 2 and tokens[0] in {"~", "!"} and re.fullmatch(r"[A-Za-z_]\w*", tokens[1]):

            # 记录取反操作符，后续根据位宽选择比较值。
            str_operator = tokens[0]  # 一元取反 token

            # 取反表达式的第二个 token 是信号名。
            str_name = tokens[1]  # 取反条件信号名

        # 复杂条件交给通用表达式规范化路径。
        else:

            # None 表示本 helper 无法识别该 token 形态。
            return None

        # str_width_class 来自信号宽度分析，区分单比特和多比特条件。
        str_width_class = self._classify_signal_width(str_name)  # 条件信号宽度分类

        # 单比特信号用 1'b0/1'b1 显式比较。
        if str_width_class == "single":

            # 取反时比较 0，否则比较 1。
            return f"{str_name} == 1'b0" if str_operator else f"{str_name} == 1'b1"

        # 多比特信号用零值或大于零比较表达真值。
        if str_width_class == "multi":

            # 取反时比较 0，否则比较大于 0。
            return f"{str_name} == 0" if str_operator else f"{str_name} > 0"

        # 未知位宽时仅做通用 token 间距规范化。
        return self._normalize_expression_tokens(tokens)

    # 包裹括号判断需要确认首个左括号覆盖完整 token 序列。
    def _condition_tokens_have_wrapping_parens(self, tokens: list[str]) -> bool:
        """
        判断 token 序列是否被一对完整外层括号包裹。

        :param tokens: 条件表达式 token 序列。
        :return: 首尾括号包裹整个序列时为 `True`。
        """

        # 首尾不是括号时不可能是完整外层包裹。
        if len(tokens) < 2 or tokens[0] != "(" or tokens[-1] != ")":

            # 直接返回 False，避免后续深度扫描。
            return False

        # int_depth 追踪小括号和花括号的嵌套层级。
        int_depth = 0  # 外层括号检测深度

        # 扫描 token，寻找外层括号第一次回到零深度的位置。
        for index, token in enumerate(tokens):

            # 左括号和拼接花括号都会增加嵌套深度。
            if token in {"(", "{"}:

                # 当前 token 打开一层括号或拼接结构。
                int_depth += 1  # 进入更深括号层级

            # 右括号和右花括号降低嵌套深度。
            elif token in {")", "}"}:

                # 当前 token 关闭一层括号或拼接结构。
                int_depth = max(0, int_depth - 1)  # 离开一层括号层级

            # 深度回到零时，只有位于最后一个 token 才是完整包裹。
            if int_depth == 0:

                # 只有最后一个 token 闭合外层括号时才算整体包裹。
                return index == len(tokens) - 1

        # 扫描结束仍未闭合时不视为完整外层括号。
        return False

    # 原子 if 条件用于决定递归规范化时是否保留括号。
    def _is_atomic_if_condition_tokens(self, tokens: list[str]) -> bool:
        """
        判断 token 序列是否为单个可直接比较的 if 条件。

        :param tokens: 条件表达式 token 序列。
        :return: 去掉外层括号后可按简单条件处理时为 `True`。
        """

        # list_working 是允许剥离外层括号的临时 token 序列。
        list_working = list(tokens)  # 待剥离外层括号的 token 序列

        # 连续剥掉只起包裹作用的外层括号。
        while self._condition_tokens_have_wrapping_parens(list_working):

            # 去掉首尾括号后继续判断是否仍有外层包裹。
            list_working = list_working[1:-1]  # 剥离一层外层括号后的 token 序列

        # 简单条件 helper 能识别则说明该条件是原子条件。
        return self._normalize_simple_if_condition_tokens(list_working) is not None

    # 顶层表达式切分只在括号和三元表达式之外识别操作符。
    def _split_top_level_expression_tokens(self, tokens: list[str], operator: str) -> list[list[str]]:
        """
        按指定顶层二元操作符切分表达式 token。

        :param tokens: 待切分的表达式 token 序列。
        :param operator: 目标顶层操作符，例如 `||` 或 `&&`。
        :return: 顶层操作符分隔出的 token 分段列表。
        """

        # expression_split_state 持有本轮顶层切分的分段结果和嵌套深度。
        expression_split_state = ExpressionSplitState(  # 顶层表达式切分扫描状态
            list_result=[],  # 已提交的顶层表达式分段
            list_current=[],  # 当前正在累积的表达式分段
            int_depth=0,  # 括号与花括号嵌套深度
            int_ternary_depth=0,  # 三元表达式嵌套深度
        )

        # 顺序扫描 token，保持原表达式 token 顺序。
        for token in tokens:

            # 单 token helper 维护括号深度、三元深度和当前分段。
            expression_split_state = self._consume_top_level_split_token(  # 当前 token 消费后的扫描状态
                token,  # 当前扫描 token
                operator,  # 目标顶层切分操作符
                expression_split_state,  # 本轮扫描状态
            )

        # 末尾剩余分段写入结果。
        if expression_split_state.list_current:

            # 保存最后一个未被操作符结尾的分段。
            expression_split_state.list_result.append(expression_split_state.list_current)

        # 返回所有顶层切分片段。
        return expression_split_state.list_result

    # 单个 token 更新顶层表达式切分状态。
    def _consume_top_level_split_token(
        self,
        token: str,
        operator: str,
        expression_split_state: ExpressionSplitState,
    ) -> ExpressionSplitState:
        """
        根据一个 token 更新顶层操作符切分状态。

        :param token: 当前扫描到的表达式 token。
        :param operator: 目标顶层操作符。
        :param expression_split_state: 当前表达式分段和嵌套深度。
        :return: 更新后的表达式切分扫描状态。
        """

        # 括号类 token 只调整嵌套深度，不参与顶层操作符匹配。
        expression_split_state_grouping = self._consume_grouping_split_token(  # 括号 token 处理后的扫描状态
            token,  # 用来识别括号边界的当前 token
            expression_split_state,  # 括号 helper 直接更新的扫描状态
        )

        # 命中括号 token 时直接返回深度更新结果。
        if expression_split_state_grouping is not None:

            # 括号 helper 已经完成当前 token 的追加和深度变更。
            return expression_split_state_grouping

        # 非零括号深度中的 token 不参与顶层操作符切分。
        if expression_split_state.int_depth != 0:

            # 括号内部普通 token 直接进入当前分段。
            expression_split_state.list_current.append(token)

            # 保持括号内部状态继续扫描。
            return expression_split_state

        # 三元表达式和顶层操作符共享同一层级判断。
        expression_split_state_operator = self._consume_top_level_operator_token(  # 顶层操作符处理后的扫描状态
            token,  # 用来识别三元和目标操作符的当前 token
            operator,  # 本轮递归切分关注的顶层操作符
            expression_split_state,  # 操作符 helper 直接更新的扫描状态
        )

        # helper 命中问号、冒号或顶层切分操作符时直接返回。
        if expression_split_state_operator is not None:

            # operator helper 已处理 token 是否进入当前分段。
            return expression_split_state_operator

        # 普通 token 追加到当前分段。
        expression_split_state.list_current.append(token)

        # 返回普通 token 处理后的状态。
        return expression_split_state

    # 括号 token 的消费只维护深度和当前分段。
    def _consume_grouping_split_token(
        self,
        token: str,
        expression_split_state: ExpressionSplitState,
    ) -> ExpressionSplitState | None:
        """
        消费顶层表达式切分中的括号类 token。

        :param token: 当前扫描 token。
        :param expression_split_state: 当前表达式分段和嵌套深度。
        :return: 命中括号时返回更新状态，否则返回 `None`。
        """

        # 左括号和花括号进入更深层表达式。
        if token in {"(", "{"}:

            # 括号 token 属于当前分段，深度随后递增。
            expression_split_state.list_current.append(token)

            # 深度递增后屏蔽括号内部的顶层操作符。
            expression_split_state.int_depth += 1  # 进入括号后的嵌套深度

            # 返回进入括号后的状态。
            return expression_split_state

        # 右括号和右花括号回到上一层表达式。
        if token in {")", "}"}:

            # 右括号 token 仍属于当前表达式分段。
            expression_split_state.list_current.append(token)

            # 深度不允许降到负数，保持原容错语义。
            expression_split_state.int_depth = max(0, expression_split_state.int_depth - 1)  # 离开括号后的嵌套深度

            # 返回离开一层括号后的状态。
            return expression_split_state

        # 非括号 token 交由调用方继续判断。
        return None

    # 三元和操作符 token 的消费只在顶层括号深度为零时使用。
    def _consume_top_level_operator_token(
        self,
        token: str,
        operator: str,
        expression_split_state: ExpressionSplitState,
    ) -> ExpressionSplitState | None:
        """
        消费顶层表达式切分中的三元和目标操作符 token。

        :param token: 当前扫描 token。
        :param operator: 目标顶层切分操作符。
        :param expression_split_state: 当前表达式分段和嵌套深度。
        :return: 命中三元或目标操作符时返回更新状态，否则返回 `None`。
        """

        # 问号表示进入三元表达式分支，并保留在当前 token 分段中。
        if token == "?":

            # 三元表达式问号本身仍属于当前条件表达式。
            expression_split_state.list_current.append(token)

            # 问号增加三元深度，后续目标操作符暂不切分。
            expression_split_state.int_ternary_depth += 1  # 进入三元表达式后的深度

            # 返回进入一层三元表达式后的状态。
            return expression_split_state

        # 冒号闭合最近一个三元问号，并保留在当前 token 分段中。
        if token == ":" and expression_split_state.int_ternary_depth > 0:

            # 冒号保留为 true/false 两个表达式之间的分隔符。
            expression_split_state.list_current.append(token)

            # 冒号闭合一层三元表达式。
            expression_split_state.int_ternary_depth -= 1  # 离开三元表达式后的深度

            # 返回离开一层三元表达式后的状态。
            return expression_split_state

        # 目标操作符只在非三元表达式顶层生效。
        if token == operator and expression_split_state.int_ternary_depth == 0:

            # 顶层操作符左侧分段非空时写入结果。
            self._append_top_level_split_segment(expression_split_state)

            # 当前操作符本身不属于任何分段。
            expression_split_state.list_current = []  # 操作符右侧重新开始收集

            # 返回切断当前分段后的状态。
            return expression_split_state

        # 非三元或目标操作符 token 交还给普通路径追加。
        return None

    # 顶层切分结果追加 helper 负责过滤空分段。
    def _append_top_level_split_segment(self, expression_split_state: ExpressionSplitState) -> None:
        """
        将非空表达式分段写入顶层切分结果。

        :param expression_split_state: 当前表达式分段和分段结果。
        :return: 不返回业务值，直接更新扫描状态中的结果列表。
        """

        # 空分段来自连续或边界操作符，不能进入结果列表。
        if not expression_split_state.list_current:

            # 没有 token 时保持结果列表不变。
            return

        # 保存操作符左侧已经收集完成的分段。
        expression_split_state.list_result.append(expression_split_state.list_current)

    # 字符串条件先经过通用表达式空白规范化，再按 token 规则处理。
    def _normalize_if_condition_expression(self, expression: str, preserve_wrapping_parens: bool = False) -> str:
        """
        规范化 if 条件表达式文本。

        :param expression: 原始条件表达式文本。
        :param preserve_wrapping_parens: 递归调用时是否保留必要外层括号。
        :return: 规范化后的条件表达式文本。
        """

        # str_normalized 是先经过通用表达式空白处理的条件文本。
        str_normalized = self._normalize_expression_spacing(expression)  # 初步规范化条件表达式

        # list_tokens 保存条件表达式 token，供 if 专用规则递归处理。
        list_tokens = self._tokenize_expression_segment(str_normalized)  # 条件表达式 token 序列

        # 返回 token 级 if 条件规范化结果。
        return self._normalize_if_condition_tokens(list_tokens, preserve_wrapping_parens=preserve_wrapping_parens)

    # token 级 if 条件规范化负责处理外层括号、逻辑运算和简单信号。
    def _normalize_if_condition_tokens(self, tokens: list[str], preserve_wrapping_parens: bool = False) -> str:
        """
        递归规范化 if 条件 token 序列。

        :param tokens: 条件表达式 token 序列。
        :param preserve_wrapping_parens: 当前递归层是否需要保留必要括号。
        :return: 规范化后的条件表达式文本。
        """

        # 空 token 序列对应空条件文本。
        if not tokens:

            # 返回空字符串，保持调用方的原始容错行为。
            return ""

        # 完整外层括号先剥离，再根据递归上下文决定是否补回。
        if self._condition_tokens_have_wrapping_parens(tokens):

            # list_inner_tokens 是去掉首尾括号后的内部条件。
            list_inner_tokens: list[str] = tokens[1:-1]  # 外层括号内部 token

            # str_inner 保存内部条件的规范化结果。
            str_inner = self._normalize_if_condition_tokens(list_inner_tokens)  # 内部条件规范化文本

            # 非原子内部条件在上层逻辑运算中需要保留括号。
            if preserve_wrapping_parens and not self._is_atomic_if_condition_tokens(list_inner_tokens):

                # 补回括号以保持原逻辑优先级。
                return f"({str_inner})"

            # 原子条件或顶层条件无需保留冗余括号。
            return str_inner

        # 先按低优先级逻辑运算符切分顶层表达式。
        for operator in ("||", "&&"):

            # list_parts 是当前操作符切分出的顶层分段。
            list_parts = self._split_top_level_expression_tokens(tokens, operator)  # 顶层逻辑运算分段

            # 多段说明当前操作符确实出现在顶层。
            if len(list_parts) > 1:

                # 递归规范化每一段，并在需要时保留分段括号。
                return f" {operator} ".join(
                    self._normalize_if_condition_tokens(part, preserve_wrapping_parens=True) for part in list_parts
                )

        # 一元取反包裹复杂条件时保留取反符和内部括号。
        if len(tokens) > 2 and tokens[0] in {"~", "!"} and self._condition_tokens_have_wrapping_parens(tokens[1:]):

            # str_inner 保存取反括号内部的规范化条件。
            str_inner = self._normalize_if_condition_tokens(tokens[2:-1])  # 取反内部条件文本

            # 返回保留取反结构的条件表达式。
            return f"{tokens[0]}({str_inner})"

        # str_normalized_simple 保存可识别简单条件的显式比较结果。
        str_normalized_simple = self._normalize_simple_if_condition_tokens(tokens)  # 简单条件规范化结果

        # 简单条件识别成功时直接返回显式比较。
        if str_normalized_simple is not None:

            # 返回单信号条件规范化结果。
            return str_normalized_simple

        # 复杂表达式退回通用 token 间距规范化。
        return self._normalize_expression_tokens(tokens)

    # 条件 header 规范化只替换括号内表达式。
    def _normalize_if_condition_header(self, text: str) -> str:
        """
        规范化 if 或 else-if 头部中的条件表达式。

        :param text: 原始 if/else-if 头部文本。
        :return: 条件表达式规范化后的头部文本。
        """

        # str_working 是去掉外围空白后的控制头。
        str_working = text.strip()  # 规范化前控制头文本

        # else-if 头部保留两个关键字。
        if self._is_else_if_header(str_working):

            # str_prefix 标记当前头部关键字前缀。
            str_prefix = "else if"  # else-if 头部前缀

        # 普通 if 头部只保留 if 前缀。
        elif str_working.startswith("if"):

            # str_prefix 标记普通条件头前缀。
            str_prefix = "if"  # 单分支条件关键字

        # 不是 if 头部时不改写原文本。
        else:

            # 返回原文本，保持非条件 header 的兼容行为。
            return text

        # int_open_index 定位条件表达式的左括号。
        int_open_index = str_working.find("(", len(str_prefix))  # 条件左括号位置

        # 找不到左括号时不猜测条件范围。
        if int_open_index == -1:

            # 返回原文本，避免破坏异常 header。
            return text

        # int_close_index 定位与条件左括号匹配的右括号。
        int_close_index = self._find_matching_paren_in_text(str_working, int_open_index)  # 条件右括号位置

        # 括号不闭合时保持原 header。
        if int_close_index == -1:

            # 返回原文本，让上游严格检查处理异常结构。
            return text

        # str_normalized_condition 是括号内部条件的规范化文本。
        str_normalized_condition = self._normalize_if_condition_expression(  # if 括号内条件文本
            str_working[int_open_index + 1 : int_close_index]  # 原始条件表达式片段
        )  # 规范化 if 条件表达式

        # str_remainder 保留条件右括号之后的 begin、label 或其他尾部文本。
        str_remainder = str_working[int_close_index + 1 :].strip()  # if 头部尾随文本

        # begin 紧跟右括号时沿用旧 formatter 的无空格拼接方式。
        if not str_remainder or str_remainder.startswith("begin"):

            # 返回无额外空格的条件头。
            return f"{str_prefix}({str_normalized_condition}){str_remainder}"

        # 其他尾部文本前保留一个空格。
        return f"{str_prefix}({str_normalized_condition}) {str_remainder}"

    # 条件 header 格式化负责补齐 begin 和可选 label。
    def _format_if_header(self, header: str, label: str = "") -> str:
        """
        格式化 if/else-if 控制头。

        :param header: 原始 if 控制头文本。
        :param label: 可选 begin label。
        :return: 规范化条件后追加 begin 后缀的控制头。
        """

        # str_suffix 根据 label 决定 begin 后缀。
        str_suffix = f"begin:{label}" if label else "begin"  # if 头部 begin/label 片段

        # str_normalized_header 先做普通语句规范化，再规范化条件。
        str_normalized_header = self._normalize_if_condition_header(  # 带规范化条件的 if 头部
            self._normalize_statement_line(header)  # 先压缩空白后的 header
        )  # 已规范化条件的控制头

        # 返回带 begin 后缀的 if header。
        return f"{str_normalized_header}{str_suffix}"

    # raw block 行渲染负责按 begin/end 变化调整缩进。
    def _render_raw_block_lines(self, lines: list[str], indent_level: int) -> list[str]:
        """
        渲染无法结构化解析的 Verilog 源码行。

        :param lines: 原始 Verilog 行列表。
        :param indent_level: raw block 起始缩进层级。
        :return: 规范化语句和注释后的输出行。
        """

        # list_rendered 收集 raw block 的最终输出行。
        list_rendered: list[str] = []  # raw block 缩进归一化结果

        # int_current_indent 跟随 begin/end 深度动态变化。
        int_current_indent = indent_level  # 当前 raw 行缩进层级

        # raw 行按原始顺序逐行规范化。
        for raw_line in lines:

            # str_normalized 是当前 raw 行的规范化语句文本。
            str_normalized = self._normalize_statement_line(raw_line.strip())  # 规范化 raw 行文本

            # 空 raw 行不进入输出。
            if not str_normalized:

                # 跳过空行，保持 formatter 现有压缩策略。
                continue

            # 注释行不参与 begin/end 缩进增减。
            if str_normalized.startswith("//"):

                # 注释行按当前缩进直接输出。
                list_rendered.append(self._render_statement_line(str_normalized, int_current_indent))

                # 注释行处理完成，继续下一行。
                continue

            # int_leading_closes 表示该行开头先闭合了多少层块。
            int_leading_closes = self._raw_block_leading_close_count(str_normalized)  # 行首闭合层数

            # 行首闭合会先降低当前行缩进。
            if int_leading_closes:

                # 缩进不会低于 raw block 的入口层级。
                int_current_indent = max(indent_level, int_current_indent - int_leading_closes)  # 闭合后的缩进层级

            # 输出当前 raw 语句行。
            list_rendered.append(self._render_statement_line(str_normalized, int_current_indent))

            # 根据当前行的 begin/end 净变化更新下一行缩进。
            int_current_indent = max(  # raw 下一行缩进层级
                indent_level,  # raw block 入口缩进下限
                int_current_indent + self._statement_indent_delta(str_normalized),  # 当前语句对下一行的缩进影响
            )

        # 返回 raw block 渲染行。
        return list_rendered

    # banner 注释行统一重新生成，保证宽度和风格一致。
    def _normalize_banner_comment_line(self, line: str) -> str:
        """
        规范化 banner 注释行。

        :param line: 原始注释行。
        :return: 普通注释原样返回，banner 注释返回重建后的文本。
        """

        # 非 banner 注释不做改写。
        if not is_banner_line(line):

            # 返回原行，保留普通注释内容。
            return line

        # str_title 提取 banner 中央标题。
        str_title = extract_banner_title(line)  # banner 标题文本

        # str_banner_kind 根据标题语义选择 bus 或 region 样式。
        str_banner_kind = "bus" if ("总线" in str_title or "接口" in str_title) else "region"  # banner 重建样式

        # 返回按当前 banner 工具重建后的注释行。
        return make_banner(str_title, str_banner_kind)

    # display_width 不处理 tab，这里补齐 tab 等宽展开。
    def _display_width_with_tabs(self, text: str) -> int:
        """
        计算包含 tab 的显示宽度。

        :param text: 待计算显示宽度的文本。
        :return: 按 tab 等于 4 列展开后的显示宽度。
        """

        # int_width 累加每个字符的显示宽度。
        int_width = 0  # 文本显示宽度累计值

        # 逐字符计算宽度，兼容中文宽字符。
        for char in text:

            # tab 按 formatter 约定折算为 4 列。
            if char == "\t":

                # 制表符固定展开为四列显示宽度。
                int_width += 4  # tab 显示宽度增量

            # 非 tab 字符使用 banner 宽度工具计算。
            else:

                # 中文宽字符和普通字符交给 display_width 判断。
                int_width += display_width(char)  # 普通字符显示宽度增量

        # 返回累计显示宽度。
        return int_width

    # 右侧注释追加函数同时支持列对齐和 tab 分隔两种风格。
    def _append_trailing_comment(self, code: str, comment: str, fallback: str | None = None) -> str:
        """
        给一行 Verilog 代码追加右侧注释。

        :param code: 已带缩进的 Verilog 代码文本。
        :param comment: 原始右侧注释内容。
        :param fallback: comment 为空时可使用的默认注释。
        :return: 带右侧注释或原样代码的输出行。
        """

        # str_text 保存清理后的注释文本。
        str_text = (comment or "").strip()  # 右侧注释文本

        # 没有注释时根据兼容模式和 fallback 决定是否追加默认说明。
        if not str_text:

            # 示例兼容模式或无 fallback 时不新增注释。
            if self._example_compat_enabled() or fallback is None:

                # 没有可用注释文本时保持代码原样。
                return code

            # 使用 fallback 作为当前行的兼容注释文本。
            str_text = fallback  # 缺失注释时的兼容说明

        # str_marker 补齐 Verilog 行注释前缀。
        str_marker = f"//{str_text}" if str_text.startswith(",") else f"// {str_text}"  # Verilog 右侧注释标记

        # dict_formatter_config 读取 formatter 注释对齐配置。
        dict_formatter_config = self.config.get("formatter", {})  # formatter 配置字典

        # str_comment_style 决定使用 tab 分隔还是列对齐。
        str_comment_style = dict_formatter_config.get("trailing_comment_style", "column")  # 右侧注释样式

        # tab 样式直接用制表符分隔代码和注释。
        if str_comment_style == "tab":

            # 返回 tab 分隔的右侧注释行。
            return f"{code}\t{str_marker}"

        # object_configured_column 保存用户配置的注释列。
        obj_object_configured_column: object = dict_formatter_config.get(  # 用户配置的右侧注释列
            "trailing_comment_column",  # formatter 配置中的列号键
            self.TRAILING_COMMENT_COLUMN,  # 未配置时使用类级默认列
        )

        # 配置列可能不是合法整数，需要兜底。
        try:

            # int_comment_column 是最终使用的目标注释列。
            int_comment_column = max(1, int(obj_object_configured_column))  # 右侧注释目标列

        # 非整数配置回退到默认列。
        except (TypeError, ValueError):

            # 默认列来自 formatter 常量。
            int_comment_column = self.TRAILING_COMMENT_COLUMN  # 默认右侧注释列

        # int_target_prefix_width 是注释标记前应达到的显示宽度。
        int_target_prefix_width = int_comment_column - 1  # 注释前目标宽度

        # int_code_width 是代码部分的显示宽度，Tab 必须按 formatter 约定展开。
        int_code_width = self._display_width_with_tabs(code)  # 代码显示宽度

        # str_padding 根据代码宽度补齐到目标列，过长时至少保留一个空格。
        str_padding = (  # 代码和注释之间的对齐空白
            " " * (int_target_prefix_width - int_code_width)  # 对齐到目标注释列所需空格
            if int_code_width < int_target_prefix_width  # 代码短于目标列时执行列对齐
            else " "  # 代码越过目标列时仍保留可读分隔
        )

        # 返回列对齐后的右侧注释行。
        return f"{code}{str_padding}{str_marker}"

    # 前导注释渲染保持传入顺序并过滤空注释。
    def _render_leading_comments(self, comments: list[str], indent_level: int) -> list[str]:
        """
        渲染模型携带的前导注释。

        :param comments: 原始前导注释列表。
        :param indent_level: 注释使用的缩进层级。
        :return: 非空注释规范化后的输出行。
        """

        # 返回渲染后的非空前导注释。
        return [self._render_statement_line(comment, indent_level) for comment in comments if comment.strip()]

    # 单行语句渲染统一处理 banner 注释、普通语句和右侧注释。
    def _render_statement_line(self, text: str, indent_level: int) -> str:
        """
        渲染一行 Verilog 语句或注释。

        :param text: 原始语句或注释文本。
        :param indent_level: 当前行使用的缩进层级。
        :return: 带缩进和规范化注释的输出行。
        """

        # str_stripped 是去掉外围空白后的待渲染文本。
        str_stripped = text.strip()  # 待渲染单行文本

        # 行注释只需要处理 banner 规范化。
        if str_stripped.startswith("//"):

            # str_normalized_comment 是可能被重建的 banner 注释。
            str_normalized_comment = self._normalize_banner_comment_line(str_stripped)  # 规范化注释行

            # 返回带当前缩进的注释行。
            return f"{self._indent(indent_level)}{str_normalized_comment}"

        # tuple_comment_parts 拆分代码和右侧注释。
        tuple_comment_parts = self._split_comment(str_stripped)  # 语句与右侧注释片段

        # str_normalized_code 先规范化语句，再兼容 banner 形式。
        str_normalized_code = self._normalize_banner_comment_line(  # 单行语句规范化代码
            self._normalize_statement_line(tuple_comment_parts[0].strip())  # 去掉右侧注释后的代码片段
        )

        # str_code 是带缩进的代码片段，空语句保留缩进占位。
        str_code = (  # 带缩进的单行代码
            f"{self._indent(indent_level)}{str_normalized_code}"  # 非空代码按目标缩进输出
            if str_normalized_code  # 有可渲染代码时保留内容
            else self._indent(indent_level)  # 空代码只保留缩进占位
        )

        # 存在右侧注释时交给统一对齐函数处理。
        if tuple_comment_parts[1]:

            # 返回追加右侧注释后的语句行。
            return self._append_trailing_comment(str_code, tuple_comment_parts[1], None)

        # 无右侧注释时去掉尾部空白。
        return str_code.rstrip()

    # 实例块优先尝试 canonical 解析，失败时保留 legacy 行级渲染。
    def _render_instance_block(self, instance: InstanceBlock, indent_level: int) -> list[str]:
        """
        渲染单个 Verilog 实例化块。

        :param instance: 已解析出的实例块模型。
        :param indent_level: 实例首行使用的缩进层级。
        :return: canonical 或 legacy 路径生成的实例化行。
        """

        # list_canonical_lines 保存可结构化实例的规范化输出。
        list_canonical_lines = self._render_instance_block_canonical(instance, indent_level)  # canonical 实例输出行

        # canonical 成功时不再走 legacy 文本路径。
        if list_canonical_lines is not None:

            # 返回结构化实例渲染结果。
            return list_canonical_lines

        # 解析失败时保留 legacy 渲染行为。
        return self._render_instance_block_legacy(instance, indent_level)

    # 实例分组注释用于 legacy 实例端口组之间插入空行。
    def _is_instance_group_comment(self, text: str) -> bool:
        """
        判断注释是否是实例参数或端口分组标题。

        :param text: 原始注释文本。
        :return: 形如普通标题注释且不是端口连接注释时为 `True`。
        """

        # str_stripped 是去除外围空白后的注释候选。
        str_stripped = text.strip()  # 实例注释候选文本

        # 非注释或端口连接注释都不是分组标题。
        if not str_stripped.startswith("//") or str_stripped.startswith(INSTANCE_CONNECTION_COMMENT_PREFIX):

            # 返回 False，避免把端口连接注释误当分组标题。
            return False

        # str_body 是去掉 `//` 后的标题正文。
        str_body = str_stripped[2:].strip()  # 实例分组注释正文

        # 分组标题必须非空且不能包含端口连接括号。
        return bool(str_body) and "(" not in str_body and ")" not in str_body

    # canonical 实例渲染只处理无注释、括号可匹配的实例文本。
    def _render_instance_block_canonical(self, instance: InstanceBlock, indent_level: int) -> list[str] | None:
        """
        用结构化解析结果渲染实例化块。

        :param instance: 已解析出的实例块模型。
        :param indent_level: 实例首行使用的缩进层级。
        :return: 可结构化时返回规范化实例行，否则返回 `None`。
        """

        # dict_parsed 保存模块名、实例名、参数关联和端口关联。
        dict_parsed = self._parse_instance_for_render(instance.text)  # canonical 实例解析结果

        # 含注释或结构不完整的实例交给 legacy 路径。
        if dict_parsed is None:

            # None 表示当前实例不能安全重排。
            return None

        # list_lines 收集 canonical 实例输出行。
        list_lines: list[str] = []  # canonical 实例渲染行

        # 带参数实例需要先输出 `module #(`。
        if dict_parsed["params"]:

            # 参数块头部使用实例当前缩进。
            list_lines.append(f"{self._indent(indent_level)}{dict_parsed['module_name']} #(")

            # 参数关联逐项输出，保持原关联顺序。
            for index, item in enumerate(dict_parsed["params"]):

                # str_suffix 只在非最后一个参数关联后追加逗号。
                str_suffix = "," if index < len(dict_parsed["params"]) - 1 else ""  # 参数关联尾逗号

                # str_association 是规范化后的参数关联表达式。
                str_association = self._normalize_instance_association(item)  # 参数关联文本

                # 参数关联行使用实例内部缩进。
                str_code = f"{self._indent(indent_level + 1)}{str_association}{str_suffix}"  # 参数关联代码行

                # 参数关联同线注释说明参数映射关系。
                str_comment = self._instance_association_comment(str_association, "parameter")  # 参数关联中文说明

                # 输出带同线注释的参数关联。
                list_lines.append(self._append_trailing_comment(str_code, str_comment, None))

            # 参数块结束后紧接实例名和端口左括号。
            list_lines.append(f"{self._indent(indent_level)}){dict_parsed['instance_name']}(")

        # 无参数实例直接输出模块名和实例名。
        else:

            # str_instance_head 是无参数实例的首行代码。
            str_instance_head = f"{dict_parsed['module_name']} {dict_parsed['instance_name']}("  # 无参数实例首行文本

            # 无参数实例首行是 `module instance(`。
            list_lines.append(f"{self._indent(indent_level)}{str_instance_head}")

        # 端口关联逐项输出，保持原关联顺序。
        for index, item in enumerate(dict_parsed["ports"]):

            # str_suffix 只在非最后一个端口关联后追加逗号。
            str_suffix = "," if index < len(dict_parsed["ports"]) - 1 else ""  # 端口关联尾逗号

            # str_association 是规范化后的端口关联表达式。
            str_association = self._normalize_instance_association(item)  # 端口关联文本

            # 端口关联行使用实例内部缩进。
            str_code = f"{self._indent(indent_level + 1)}{str_association}{str_suffix}"  # 端口关联代码行

            # 端口关联同线注释说明接口连线关系。
            str_comment = self._instance_association_comment(str_association, "port")  # 端口关联中文说明

            # 输出带同线注释的端口关联。
            list_lines.append(self._append_trailing_comment(str_code, str_comment, None))

        # 实例化语句闭合行与实例首行缩进对齐。
        list_lines.append(f"{self._indent(indent_level)});")

        # 返回 canonical 实例输出行。
        return list_lines

    # legacy 实例渲染保留带注释或复杂文本的原始行级结构。
    def _render_instance_block_legacy(self, instance: InstanceBlock, indent_level: int) -> list[str]:
        """
        按旧行级规则渲染实例化块。

        :param instance: 已解析出的实例块模型。
        :param indent_level: 实例首行使用的缩进层级。
        :return: legacy 规则渲染后的实例化行。
        """

        # list_lines 保留无法 canonical 化的实例原始行级结构。
        list_lines: list[str] = []  # 保守实例路径的逐行结果

        # str_phase 表示当前行位于实例根部、参数区、端口区或结束区。
        str_phase = "root"  # legacy 实例解析相位

        # bool_first_code_line 用于识别无参数实例的首个左括号。
        bool_first_code_line = True  # legacy 首个代码行标记

        # legacy 路径按原始实例文本逐行处理。
        for raw_line in instance.text.splitlines():

            # str_stripped 是当前实例原始行去除外围空白后的文本。
            str_stripped = raw_line.strip()  # legacy 实例当前行文本

            # legacy 路径压缩空白行，避免实例输出产生双空行。
            if not str_stripped:

                # 跳过空行，保持旧 formatter 的压缩行为。
                continue

            # 注释行需要根据当前 phase 决定缩进。
            if self._append_legacy_instance_comment_line(list_lines, str_stripped, str_phase, indent_level):

                # 注释行不改变 phase。
                continue

            # 代码行先拆出原有右侧注释，再规范化实例语句空白。
            tuple_comment_parts, str_normalized = self._normalize_legacy_instance_code_line(  # legacy 代码行与注释
                str_stripped,  # 当前非注释实例行的原始内容
            )

            # 缩进 helper 根据规范化代码行推进 params/ports/done 相位。
            int_current_indent, str_phase = self._legacy_instance_line_layout(  # 当前代码行缩进与相位
                str_normalized,  # 规范化后的实例代码行
                str_phase,  # 进入当前代码行前的解析相位
                indent_level,  # 实例首行缩进层级
            )

            # str_code 是加缩进后的当前实例代码行。
            str_code = f"{self._indent(int_current_indent)}{str_normalized}"  # legacy 实例输出代码

            # 输出 helper 保留已有注释，并为缺注释的实例关联补同线说明。
            self._append_legacy_instance_code_line(
                list_lines,  # legacy 实例输出行缓存
                tuple_comment_parts,  # 当前代码行拆出的原始注释
                str_normalized,  # 当前实例代码行文本
                str_code,  # 已加缩进的实例代码行
                str_phase,  # 当前代码行所属相位
            )

            # 无参数实例首行以左括号结尾时直接进入端口区。
            str_phase = self._next_legacy_instance_phase(  # 首行特例处理后的相位
                instance,  # 当前实例块模型
                bool_first_code_line,  # 当前行是否为首个代码行
                str_normalized,  # 用于判断首行左括号的代码文本
                str_phase,  # 普通布局判断后的相位
            )

            # 首个代码行判断只执行一次。
            bool_first_code_line = False  # legacy 首行判断已完成

        # canonical 失败时调用方使用这些行保持原始实例结构。
        return list_lines

    # legacy 实例中的独立注释行保持原始文本，只校正缩进和分组空行。
    def _append_legacy_instance_comment_line(
        self,
        list_lines: list[str],
        str_stripped: str,
        str_phase: str,
        indent_level: int,
    ) -> bool:
        """
        渲染 legacy 实例中的独立注释行。

        :param list_lines: legacy 实例输出行缓存。
        :param str_stripped: 去除外围空白后的当前原始行。
        :param str_phase: 当前实例行级解析相位。
        :param indent_level: 实例首行缩进层级。
        :return: 当前行被识别并输出为注释行时返回 `True`。
        """

        # 非注释行继续交给代码路径处理。
        if not str_stripped.startswith("//"):

            # 返回 False 表示调用方仍需处理代码文本。
            return False

        # int_comment_indent 在参数和端口关联区比实例首行深一级。
        int_comment_indent = indent_level + 1 if str_phase in {"params", "ports"} else indent_level  # legacy 注释缩进层级

        # 分组注释前需要单空行，避免和前一条端口关联粘连。
        if self._needs_blank_before_legacy_instance_group_comment(list_lines, str_stripped, str_phase):

            # 插入单空行分隔实例参数或端口分组。
            list_lines.append("")

        # 注释行按计算出的缩进层级渲染。
        list_lines.append(self._render_statement_line(str_stripped, int_comment_indent))

        # 告知主循环当前行已经完成渲染。
        return True

    # legacy 实例分组注释只在参数和端口关联区前插入空行。
    def _needs_blank_before_legacy_instance_group_comment(
        self,
        list_lines: list[str],
        str_stripped: str,
        str_phase: str,
    ) -> bool:
        """
        判断 legacy 实例分组注释前是否需要插入空行。

        :param list_lines: legacy 实例输出行缓存。
        :param str_stripped: 去除外围空白后的当前原始行。
        :param str_phase: 当前实例行级解析相位。
        :return: 需要在当前分组注释前补空行时返回 `True`。
        """

        # 根部注释不是参数或端口分组边界。
        if str_phase not in {"params", "ports"}:

            # 根部注释不插入额外空行。
            return False

        # 只有实例分组说明才需要隔开上一条代码。
        if not self._is_instance_group_comment(str_stripped):

            # 普通说明注释沿用原相邻位置。
            return False

        # 文件开头或实例块开头没有上一行可分隔。
        if not list_lines:

            # 没有上一行时无需空行。
            return False

        # 已经存在空行时避免重复插入。
        if list_lines[-1] == "":

            # 保持单空行约束。
            return False

        # 紧跟另一条注释时不再插入额外空行。
        return not list_lines[-1].lstrip().startswith("//")

    # legacy 实例代码行先归一化空白，再保留原右侧注释片段。
    def _normalize_legacy_instance_code_line(self, str_stripped: str) -> tuple[tuple[str, str], str]:
        """
        规范化 legacy 实例代码行并保留行内注释。

        :param str_stripped: 去除外围空白后的当前原始行。
        :return: 原始代码/注释二元组以及规范化后的代码文本。
        """

        # tuple_comment_parts 拆分当前行代码和右侧注释。
        tuple_comment_parts = self._split_comment(str_stripped)  # legacy 行代码与注释

        # str_normalized 是当前实例代码行的规范化文本。
        str_normalized = self._normalize_statement_line(tuple_comment_parts[0].strip())  # legacy 实例代码行

        # 模块名与参数左括号之间保持一个空格。
        str_normalized = re.sub(  # 规范化模块名与参数块间距
            r"^(?P<module>[A-Za-z_]\w*)\s*#\s*\($",  # 只匹配模块名后紧跟参数左括号的首行
            r"\g<module> #(",  # 保留模块名并插入标准空格
            str_normalized,  # 当前 legacy 实例代码行
        )

        # 返回拆分出的右侧注释和规范化后的代码行。
        return tuple_comment_parts, str_normalized

    # 单行参数覆盖实例头只在参数括号完整且余下文本是实例端口开头时成立。
    def _is_single_line_parameterized_instance_header(self, text: str) -> bool:
        """
        判断一行是否包含完整参数覆盖和实例端口左括号。

        :param text: 待识别的 legacy 实例代码行。
        :return: 参数覆盖与实例端口头都完整时返回 `True`。
        """

        # 前缀必须从模块标识符开始并紧接参数覆盖左括号。
        match_prefix = re.match(r"^[A-Za-z_]\w*\s*#\s*\(", text)  # 模块名和参数覆盖前缀

        # 非参数化实例或前方含赋值文本时直接拒绝。
        if match_prefix is None:

            # 返回 False，保持非目标行的既有 legacy 相位。
            return False

        # 正则前缀最后一个字符就是 `#(` 的参数左括号。
        int_parameter_open = match_prefix.end() - 1  # 参数覆盖外层左括号位置

        # 复用共享括号匹配器定位完整参数覆盖的右括号。
        int_parameter_close = self._find_matching_paren_in_text(text, int_parameter_open)  # 参数覆盖外层右括号位置

        # 未闭合参数覆盖不能提前进入端口关联区。
        if int_parameter_close < 0:

            # 返回 False，让异常或多行文本沿用原有保守路径。
            return False

        # 参数覆盖之后只允许实例名、可选数组范围和端口左括号。
        str_remainder = text[int_parameter_close + 1 :].strip()  # 参数覆盖后的实例头余量

        # 完整匹配避免接受赋值、额外 token 或已经包含端口内容的行。
        return bool(
            re.fullmatch(
                r"[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*\(",
                str_remainder,
            )
        )

    # legacy 实例代码行布局同时决定当前行缩进和后续解析相位。
    def _legacy_instance_line_layout(
        self,
        str_normalized: str,
        str_phase: str,
        indent_level: int,
    ) -> tuple[int, str]:
        """
        根据 legacy 实例代码行计算缩进和解析相位。

        :param str_normalized: 规范化后的当前实例代码行。
        :param str_phase: 进入当前代码行前的实例解析相位。
        :param indent_level: 实例首行缩进层级。
        :return: 当前代码行缩进层级和处理后的解析相位。
        """

        # 完整单行参数覆盖实例头已经打开端口列表。
        if self._is_single_line_parameterized_instance_header(str_normalized):

            # 当前实例头保持根缩进，后续关联直接按 ports phase 处理。
            return indent_level, "ports"

        # 参数块开头保持实例首行缩进，并切换到 params phase。
        if re.search(r"#\s*\($", str_normalized):

            # 后续行进入参数关联区。
            return indent_level, "params"

        # 参数块闭合并进入实例端口区。
        if re.match(r"^\)\s*\w+\s*\($", str_normalized):

            # 端口块起始行与实例首行缩进对齐。
            return indent_level, "ports"

        # 实例闭合行回到实例首行缩进。
        if str_normalized == ");":

            # phase 标记为 done，后续理论上不再有端口行。
            return indent_level, "done"

        # 参数区和端口区内部连接行缩进加深一级。
        if str_phase in {"params", "ports"}:

            # 参数或端口关联使用内部缩进。
            return indent_level + 1, str_phase

        # 根部其他行保持实例首行缩进。
        return indent_level, str_phase

    # legacy 实例代码行输出时补齐实例关联同线注释。
    def _append_legacy_instance_code_line(
        self,
        list_lines: list[str],
        tuple_comment_parts: tuple[str, str],
        str_normalized: str,
        str_code: str,
        str_phase: str,
    ) -> None:
        """
        输出 legacy 实例代码行并补齐关联注释。

        :param list_lines: legacy 实例输出行缓存。
        :param tuple_comment_parts: 当前行拆出的代码和右侧注释。
        :param str_normalized: 规范化后的当前实例代码行。
        :param str_code: 已加入目标缩进的实例代码行。
        :param str_phase: 当前代码行所属实例相位。
        :return: 不返回业务值，直接追加输出行。
        """

        # 有右侧注释时保留注释并按 formatter 配置对齐。
        if tuple_comment_parts[1]:

            # 追加带对齐的右侧注释行。
            list_lines.append(self._append_trailing_comment(str_code, tuple_comment_parts[1], None))

            # 已有注释的代码行无需补生成说明。
            return

        # 参数区和端口区缺注释的关联行需要补同线说明。
        if str_phase in {"params", "ports"} and self._is_instance_association_text(str_normalized):

            # 参数和端口关联使用不同中文说明。
            str_mapping_kind = "parameter" if str_phase == "params" else "port"  # 当前实例关联类别

            # 生成当前实例关联的中文同线说明。
            str_comment = self._instance_association_comment(str_normalized, str_mapping_kind)  # 实例关联中文说明

            # 追加带同线注释的 legacy 实例关联。
            list_lines.append(self._append_trailing_comment(str_code, str_comment, None))

            # 已完成缺省关联注释补齐。
            return

        # 无右侧注释时直接追加代码行。
        list_lines.append(str_code)

    # 无参数 legacy 实例首行之后直接进入端口关联区。
    def _next_legacy_instance_phase(
        self,
        instance: InstanceBlock,
        bool_first_code_line: bool,
        str_normalized: str,
        str_phase: str,
    ) -> str:
        """
        处理 legacy 实例首行对后续相位的影响。

        :param instance: 已解析出的实例块模型。
        :param bool_first_code_line: 当前代码行是否为实例块首个代码行。
        :param str_normalized: 规范化后的当前实例代码行。
        :param str_phase: 普通布局判断后的解析相位。
        :return: 下一行应使用的解析相位。
        """

        # 无参数实例的首个左括号代表端口列表已经开始。
        if bool_first_code_line and not instance.has_params and str_normalized.endswith("("):

            # 后续行按端口关联缩进处理。
            return "ports"

        # 其他情况沿用普通布局判断得到的相位。
        return str_phase

    # canonical 实例解析拒绝含注释文本，只处理纯实例化语句。
    def _parse_instance_for_render(self, text: str) -> dict[str, object] | None:
        """
        从实例文本中提取 canonical 渲染所需字段。

        :param text: 原始实例化文本。
        :return: 包含模块名、实例名、参数和端口关联的字典，失败时返回 `None`。
        """

        # str_compact 是剔除空白行后的单行实例文本。
        str_compact = self._compact_instance_text_for_render(text)  # canonical 实例单行文本

        # 无法安全压缩的实例继续使用 legacy 渲染。
        if str_compact is None:

            # 保留原始行级结构，避免丢失注释或不完整语句。
            return None

        # tuple_module_parts 提取模块名和模块名后的剩余文本。
        tuple_module_parts = self._parse_instance_module_prefix(str_compact)  # 模块名前缀解析结果

        # 无模块名前缀时不能进入 canonical 输出。
        if tuple_module_parts is None:

            # 交给 legacy 路径保留异常实例文本。
            return None

        # 模块解析结果同时包含后续参数或端口片段。
        str_module_name, str_remainder, int_module_end = tuple_module_parts  # canonical 模块名前缀字段

        # tuple_param_parts 解析可选参数关联并返回端口片段。
        tuple_param_parts = self._parse_instance_parameter_section(  # 参数区解析结果
            str_compact,  # 完整单行实例文本
            str_remainder,  # 模块名后的剩余文本
            int_module_end,  # 模块名结束下标
        )

        # 参数块缺失括号时不能做结构化渲染。
        if tuple_param_parts is None:

            # 结构不完整时保留 legacy fallback。
            return None

        # 参数解析结果提供参数关联列表和端口区剩余文本。
        list_params, str_remainder = tuple_param_parts  # canonical 参数列表与端口片段

        # tuple_port_parts 解析实例名和端口关联列表。
        tuple_port_parts = self._parse_instance_port_section(str_remainder)  # 端口区解析结果

        # 端口区不完整时继续走 legacy 路径。
        if tuple_port_parts is None:

            # 保守输出原始实例，避免生成错误括号结构。
            return None

        # 端口解析结果提供实例名和端口关联列表。
        str_instance_name, list_ports = tuple_port_parts  # canonical 实例名与端口关联

        # 返回 canonical 渲染所需字段。
        return {
            "module_name": str_module_name,
            "instance_name": str_instance_name,
            "params": list_params,
            "ports": list_ports,
        }

    # canonical 实例文本必须没有注释且整体闭合。
    def _compact_instance_text_for_render(self, text: str) -> str | None:
        """
        将可安全 canonical 渲染的实例文本压缩为单行。

        :param text: 原始实例化文本。
        :return: 单行实例文本；包含注释或未闭合时返回 `None`。
        """

        # 含行注释或块注释的实例必须保留 legacy 路径。
        if any("//" in line or "/*" in line or "*/" in line for line in text.splitlines()):

            # 结构化重排不能吞掉用户原注释。
            return None

        # str_compact 把实例文本压成单行，便于括号匹配和切分。
        str_compact = " ".join(line.strip() for line in text.splitlines() if line.strip())  # 单行实例文本

        # canonical 实例必须以 `);` 闭合。
        if not str_compact.endswith(");"):

            # 未闭合语句保持原始 legacy 输出更可靠。
            return None

        # 返回可继续做括号匹配的单行实例文本。
        return str_compact

    # canonical 实例前缀解析只提取模块名和剩余文本。
    def _parse_instance_module_prefix(self, str_compact: str) -> tuple[str, str, int] | None:
        """
        解析实例化语句开头的模块名。

        :param str_compact: 单行实例文本。
        :return: 模块名、模块名后文本和模块名结束下标；失败时返回 `None`。
        """

        # match_module 捕获实例化语句开头的模块名。
        match_module = re.match(r"^(?P<module>[A-Za-z_]\w*)", str_compact)  # 实例模块名匹配结果

        # 没有模块名时不能构造 canonical 结果。
        if not match_module:

            # 无法识别模块名前缀时保留原文本。
            return None

        # str_module_name 保存实例化模块名。
        str_module_name = match_module.group("module")  # 实例化模块名

        # str_remainder 保存模块名之后的参数和端口片段。
        str_remainder = str_compact[match_module.end() :].strip()  # 模块名后的实例文本

        # 返回模块名前缀字段供后续解析继续使用。
        return str_module_name, str_remainder, match_module.end()

    # canonical 参数区解析支持没有参数块的普通实例。
    def _parse_instance_parameter_section(
        self,
        str_compact: str,
        str_remainder: str,
        int_module_end: int,
    ) -> tuple[list[str], str] | None:
        """
        解析实例可选参数关联区。

        :param str_compact: 单行实例文本。
        :param str_remainder: 模块名后的剩余实例文本。
        :param int_module_end: 模块名结束下标。
        :return: 参数关联列表和端口区文本；参数区异常时返回 `None`。
        """

        # 无参数块时直接把剩余文本交给端口解析。
        if not str_remainder.startswith("#"):

            # 空参数列表表示实例没有参数 override。
            return [], str_remainder

        # int_hash_index 定位参数块起点。
        int_hash_index = str_compact.find("#", int_module_end)  # 参数块井号位置

        # int_open_index 定位参数块左括号。
        int_open_index = str_compact.find("(", int_hash_index)  # 参数块左括号位置

        # 找不到参数左括号时结构不完整。
        if int_open_index == -1:

            # 参数 override 缺左括号时不能 canonical 渲染。
            return None

        # int_close_index 定位参数块右括号。
        int_close_index = self._find_matching_paren_in_text(str_compact, int_open_index)  # 参数块右括号位置

        # 参数括号不闭合时不能 canonical 渲染。
        if int_close_index == -1:

            # 参数 override 右括号缺失会破坏结构化输出。
            return None

        # str_params_text 是参数块内部文本。
        str_params_text = str_compact[int_open_index + 1 : int_close_index].strip()  # 参数块内部关联串

        # list_params 保存顶层逗号切分后的参数关联。
        list_params = self._split_top_level(str_params_text, ",") if str_params_text else []  # 参数关联切分结果

        # str_after_params 是参数块之后的实例名和端口关联文本。
        str_after_params = str_compact[int_close_index + 1 :].strip()  # 参数块后的实例文本

        # 返回参数关联和后续端口区片段。
        return list_params, str_after_params

    # canonical 端口区解析提取实例名和端口关联。
    def _parse_instance_port_section(self, str_remainder: str) -> tuple[str, list[str]] | None:
        """
        解析实例名和端口关联区。

        :param str_remainder: 参数区之后或模块名之后的剩余实例文本。
        :return: 实例名和端口关联列表；端口区异常时返回 `None`。
        """

        # int_open_port 定位端口关联左括号。
        int_open_port = str_remainder.find("(")  # 端口块左括号位置

        # 找不到端口左括号时不能 canonical 渲染。
        if int_open_port == -1:

            # 端口列表缺失时逐行保守输出。
            return None

        # str_instance_name 保存端口左括号之前的实例名。
        str_instance_name = str_remainder[:int_open_port].strip()  # 实例名文本

        # 实例名不能为空。
        if not str_instance_name:

            # 无实例名会生成非法实例语句。
            return None

        # int_close_port 定位端口关联右括号。
        int_close_port = self._find_matching_paren_in_text(str_remainder, int_open_port)  # 端口块右括号位置

        # 端口括号必须闭合且后面只剩分号。
        if int_close_port == -1 or str_remainder[int_close_port + 1 :].strip() != ";":

            # 端口列表尾部异常时保持 legacy 输出。
            return None

        # str_ports_text 是端口关联内部文本。
        str_ports_text = str_remainder[int_open_port + 1 : int_close_port].strip()  # 端口块内部关联串

        # list_ports 保存顶层逗号切分后的端口关联。
        list_ports = self._split_top_level(str_ports_text, ",") if str_ports_text else []  # 端口关联列表

        # 返回实例名和端口关联列表。
        return str_instance_name, list_ports

    # 文本括号匹配需要跳过字符串字面量中的括号字符。
    def _find_matching_paren_in_text(self, text: str, open_index: int) -> int:
        """
        查找给定左括号匹配的右括号位置。

        :param text: 待扫描文本。
        :param open_index: 左括号所在下标。
        :return: 匹配右括号下标，失败时返回 -1。
        """

        # int_depth 记录从 open_index 开始的括号嵌套深度。
        int_depth = 0  # 文本括号扫描深度

        # bool_in_string 标记当前扫描位置是否在双引号字符串内。
        bool_in_string = False  # 当前扫描是否处于字符串内

        # int_index 是当前扫描下标。
        int_index = open_index  # 括号扫描下标

        # 从给定左括号开始向后扫描。
        while int_index < len(text):

            # str_char 是当前扫描字符。
            str_char = text[int_index]  # 当前扫描字符

            # 字符 helper 统一维护引号状态、括号深度和命中标记。
            int_depth, bool_in_string, bool_match_found = self._advance_paren_scan_character(  # 单字符扫描后的括号状态
                text,  # 完整文本用于判断转义引号
                int_index,  # 当前字符下标
                str_char,  # helper 用来判断括号和引号的字符
                int_depth,  # 进入当前字符前的括号深度
                bool_in_string,  # 进入当前字符前的字符串状态
            )

            # 命中匹配右括号时返回当前位置。
            if bool_match_found:

                # 当前下标就是给定左括号对应的右括号。
                return int_index

            # 扫描下一个字符。
            int_index += 1  # 下一扫描字符下标

        # 未找到匹配右括号。
        return -1

    # 单字符扫描 helper 负责引号状态和括号深度。
    def _advance_paren_scan_character(
        self,
        text: str,
        int_index: int,
        str_char: str,
        int_depth: int,
        bool_in_string: bool,
    ) -> tuple[int, bool, bool]:
        """
        根据一个字符推进括号匹配状态。

        :param text: 正在扫描的完整文本。
        :param int_index: 当前字符下标。
        :param str_char: 当前扫描字符。
        :param int_depth: 进入当前字符前的括号深度。
        :param bool_in_string: 进入当前字符前是否位于字符串内。
        :return: 更新后的括号深度、字符串状态和匹配命中标记。
        """

        # 未转义双引号会切换字符串状态。
        if self._is_unescaped_double_quote(text, int_index):

            # 引号字符本身不参与括号深度统计。
            return int_depth, not bool_in_string, False

        # 字符串内括号不参与结构匹配。
        if bool_in_string:

            # 维持字符串状态并跳过括号判断。
            return int_depth, bool_in_string, False

        # 左括号增加嵌套深度。
        if str_char == "(":

            # 进入一层括号后，后续右括号需要先抵消这层深度。
            return int_depth + 1, bool_in_string, False

        # 右括号降低深度，回到零时即为匹配位置。
        if str_char == ")":

            # int_next_depth 是消费当前右括号后的括号深度。
            int_next_depth = int_depth - 1  # 当前右括号消费后的深度

            # bool_match_found 表示起始左括号已经闭合。
            bool_match_found = int_next_depth == 0  # 当前字符是否为匹配右括号

            # 返回右括号消费后的扫描状态。
            return int_next_depth, bool_in_string, bool_match_found

        # 普通字符不会影响括号匹配状态。
        return int_depth, bool_in_string, False

    # 双引号只有在未被反斜杠转义时才切换字符串状态。
    def _is_unescaped_double_quote(self, text: str, int_index: int) -> bool:
        """
        判断当前位置是否为未转义双引号。

        :param text: 正在扫描的完整文本。
        :param int_index: 当前字符下标。
        :return: 当前字符为未转义双引号时返回 `True`。
        """

        # 非双引号字符不影响字符串状态。
        if text[int_index] != '"':

            # 返回 False 表示继续按普通字符处理。
            return False

        # 文本开头的双引号不可能被前一字符转义。
        if int_index == 0:

            # 起始双引号直接切换字符串状态。
            return True

        # 前一字符不是反斜杠时，当前双引号未被转义。
        return text[int_index - 1] != "\\"

    # 实例关联规范化只压缩空白并规范化 actual 表达式。
    def _normalize_instance_association(self, item: str) -> str:
        """
        规范化一条实例参数或端口关联。

        :param item: 原始关联文本。
        :return: 空白规范化后的关联表达式。
        """

        # str_stripped 把关联文本压缩为单空格分隔形式。
        str_stripped = " ".join(item.strip().split())  # 压缩空白后的关联文本

        # match_association 捕获 `.formal(actual)` 形式，用于只规范化 actual 部分。
        match_association = re.match(  # formal/actual 点名关联匹配
            r"^\.(?P<formal>[A-Za-z_]\w*)\((?P<actual>.*)\)$",  # formal 名和 actual 表达式捕获
            str_stripped,  # 已压缩空白的关联文本
        )  # 点名端口或参数关联匹配结果

        # 非点名关联按普通表达式规范化。
        if not match_association:

            # 返回通用表达式空白规范化结果。
            return self._normalize_expression_spacing(str_stripped)

        # str_formal 是端口或参数形式名。
        str_formal = match_association.group("formal")  # 点名关联左侧端口或参数名

        # str_actual 是规范化空白后的 actual 表达式。
        str_actual = self._normalize_expression_spacing(match_association.group("actual").strip())  # 关联 actual 表达式

        # 返回点名关联的紧凑形式。
        return f".{str_formal}({str_actual})"

    # _is_instance_association_text 判断一行是否为点名实例关联。
    def _is_instance_association_text(self, text: str) -> bool:
        """
        判断文本是否是 `.formal(actual)` 形式的实例关联。

        :param text: 已规范化的实例行文本。
        :return: 是点名实例关联时返回 True。
        """

        # str_code 去掉尾逗号后识别 formal/actual 结构。
        str_code = text.strip().rstrip(",")  # 去除尾逗号后的实例关联候选

        # 点名参数和端口关联都使用同一种结构。
        return re.match(r"^\.[A-Za-z_]\w*\(.*\)$", str_code) is not None

    # _instance_association_comment 生成实例参数或端口关联同线注释。
    def _instance_association_comment(self, association: str, kind: str) -> str:
        """
        生成实例参数或端口关联的中文同线说明。

        :param association: 已规范化的 `.formal(actual)` 关联文本。
        :param kind: `parameter` 或 `port`，用于选择中文说明模板。
        :return: 实例关联同线注释正文。
        """

        # str_code 去掉尾逗号，便于复用一个正则解析 formal 和 actual。
        str_code = association.strip().rstrip(",")  # 关联文本主体

        # str_association_pattern 解析 `.formal(actual)` 两侧语义。
        str_association_pattern = r"^\.(?P<formal>[A-Za-z_]\w*)\((?P<actual>.*)\)$"  # 实例关联解析正则

        # match_association 捕获被实例模块端口和当前模块连接表达式。
        match_association = re.match(str_association_pattern, str_code)  # 实例关联解析结果

        # 解析失败时给出最小但可读的中文说明。
        if not match_association:

            # 无法拆分时仍说明这是实例关联映射。
            return "实例关联语义映射"

        # str_formal 是被实例模块侧的参数或端口名。
        str_formal = match_association.group("formal")  # 被实例模块侧接口名

        # str_actual 是当前模块侧表达式。
        str_actual = match_association.group("actual").strip()  # actual 连接表达式

        # 参数关联说明配置值来源。
        if kind == "parameter":

            # 参数映射只强调 formal 被当前表达式配置。
            return f"{str_formal}参数映射为{str_actual}"

        # 端口悬空或空 actual 明确标记为预留连接。
        if not str_actual:

            # 空连接通常表示端口预留。
            return f"{str_formal}端口预留连接"

        # 普通端口连接说明 formal 到当前信号或表达式。
        return f"{str_formal}端口连接{str_actual}"
