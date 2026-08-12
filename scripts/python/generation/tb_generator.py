#!/usr/bin/env python3
"""为单个 RTL 模块生成带语义注释的 Verilog 测试平台骨架。"""

# 延迟注解求值，避免 CLI 脚本导入时解析复杂类型。
from __future__ import annotations
# 标准库负责命令行、JSON 输入、正则解析和路径 IO。
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

# 捕获 Verilog 模块头，支持可选参数列表和 ANSI 风格端口列表。
MODULE_RE = re.compile(  # Verilog 模块声明匹配器
    r"(?:^|\n)\s*module\s+(\w+)"  # 模块关键字与模块名
    r"(?:\s*#\s*\((.*?)\))?"  # 可选参数声明列表
    r"\s*\((.*?)\);",  # ANSI 端口列表主体
    re.DOTALL,  # 允许模块头跨多行
)

# 识别常见时钟端口名，供测试平台自动生成翻转过程。
CLK_NAME_RE = re.compile(r"clk|clock", re.IGNORECASE)  # 时钟端口命名模式

# 识别常见复位端口名，供测试平台自动生成复位任务。
RST_NAME_RE = re.compile(r"rst|reset|arst|nrst", re.IGNORECASE)  # 复位端口命名模式

# 构建脚本命令行参数，保持旧 CLI 合同稳定。
def build_parser() -> argparse.ArgumentParser:
    """
    构造 tb_generator.py 的命令行解析器。

    :param: 本函数没有外部业务参数，所有选项在函数内部声明。
    :return: 已注册输入文件、输出路径、JSON 分析模式和 testbench 语言选项的解析器。
    """

    # 创建只描述 testbench 生成功能的解析器。
    parser = argparse.ArgumentParser(  # CLI 参数解析器
        description="Generate a Verilog-2001 testbench scaffold.",  # CLI 帮助摘要
    )

    # 注册待解析的 Verilog 或 rtl_analysis.json 输入路径。
    parser.add_argument("file", type=Path, help="Input Verilog module file.")

    # 注册测试平台时钟周期，默认沿用旧脚本的 10ns。
    parser.add_argument("--clk_period_ns", type=int, default=10)

    # 注册可选输出文件路径；缺省时与输入模块同目录。
    parser.add_argument("--output", "-o", type=Path)

    # 注册 JSON 分析输入模式，兼容 rtl_analysis.json 直读。
    parser.add_argument(
        "--analysis",
        action="store_true",
        help="Treat input as rtl_analysis.json instead of a Verilog source file.",
    )

    # 注册输出 testbench 语言，当前 skill 只生成 Verilog-2001。
    parser.add_argument(
        "--tb-language",
        choices=("verilog",),
        default="verilog",
    )

    # 返回供 main 解析命令行参数的 parser。
    return parser

# 执行 CLI 主流程，集中处理文件 IO 和退出码。
def main(argv: list[str] | None = None) -> int:
    """
    读取 RTL 输入并写出 testbench scaffold。

    :param argv: CLI 参数列表；为 None 时由 argparse 读取进程参数。
    :return: 进程退出码，0 表示生成成功，2 表示输入或解析失败。
    """

    # 解析调用方传入的 CLI 参数。
    args = build_parser().parse_args(argv)  # 解析后的命令行命名空间

    # 规范化输入路径，便于错误消息直接定位文件。
    path_source = args.file.resolve()  # 绝对输入文件路径

    # 缺失输入文件时不继续进入解析流程。
    if not path_source.is_file():

        # 向 stderr 输出 current-project 规范要求的错误前缀。
        print(f"> ERR: [Python] file not found: {path_source}", file=sys.stderr)

        # 文件不存在属于用户输入错误，返回稳定退出码。
        return 2

    # 读取 UTF-8 源码或分析 JSON，失败时保持旧脚本的非零退出码。
    try:

        # 保存输入文件文本，后续根据模式解析模块和端口。
        str_source_text: str = path_source.read_text(encoding="utf-8")  # 输入文件 UTF-8 文本

    # UTF-8 解码失败时给出可读错误，不产生部分输出。
    except UnicodeDecodeError as exc:

        # 报告编码错误和具体路径，便于用户重新导出源文件。
        print(
            f"> ERR: [Python] failed to read {path_source} with UTF-8: {exc}",
            file=sys.stderr,
        )

        # 编码错误同样归为输入错误。
        return 2

    # 分析输入内容并在解析失败时转换成 CLI 退出码。
    try:

        # 判断是否应将输入当作 rtl_analysis.json，而不是 Verilog 源码。
        bool_treat_as_analysis = (  # JSON 分析输入开关
            args.analysis or path_source.suffix.lower() == ".json"  # 显式参数或文件后缀命中
        )

        # 保留解析结果的 tuple，避免安全重命名误改公开变量名。
        tuple_module_ports = load_module_and_ports(  # 模块名和端口列表载荷
            str_source_text,  # 待解析的输入文本
            path_source,  # 错误消息使用的输入路径
            treat_as_analysis=bool_treat_as_analysis,  # JSON 或 Verilog 解析模式
        )

        # 取出生成 testbench 时使用的 DUT 模块名。
        str_module_name = tuple_module_ports[0]  # 待测模块名称

        # 取出生成声明、复位和端口映射时使用的端口清单。
        list_ports = tuple_module_ports[1]  # 标准化端口描述列表

    # 模块头或 JSON 字段不满足要求时停止生成。
    except ValueError as exc:

        # 将解析错误转换为符合 current-project 输出规范的 CLI 错误。
        print(f"> ERR: [Python] module parse failed: {exc}", file=sys.stderr)

        # 解析失败属于输入内容错误。
        return 2

    # 渲染完整 testbench 文本，保持旧输出中的关键 scaffold 标记。
    str_tb_text = generate_testbench(  # 生成后的测试平台源码
        str_module_name,  # DUT 模块名称
        list_ports,  # 标准化端口清单
        args.clk_period_ns,  # CLI 指定的时钟周期
        args.tb_language,  # CLI 指定的输出语言
    )

    # 计算输出路径，未指定时沿用 tb_<module>.v 命名。
    path_output = args.output or path_source.with_name(  # 最终 testbench 输出路径
        f"tb_{str_module_name}.v",  # 默认输出文件名
    )

    # 确保嵌套输出目录存在，满足 helper script smoke。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 写入完整 testbench 源码。
    path_output.write_text(str_tb_text, encoding="utf-8")

    # 输出简短状态，不把生成内容直接打印到终端。
    print(f"> INFO: [Python] generated testbench: {path_output}")

    # 成功写出文件后返回 0。
    return 0

# 根据输入模式分派 Verilog 解析或 rtl_analysis.json 解析。
def load_module_and_ports(
    content: str,
    source: Path,
    *,
    treat_as_analysis: bool,
) -> tuple[str, list[dict[str, str | bool | None]]]:
    """
    从输入文本中提取模块名和标准化端口列表。

    :param content: Verilog 源码或 rtl_analysis.json 文本。
    :param source: 输入文件路径，用于错误消息说明问题来源。
    :param treat_as_analysis: 为 True 时按 rtl_analysis.json 字段读取端口。
    :return: 二元组，包含模块名和端口描述列表。
    :raises ValueError: 输入缺少模块声明、模块名或端口列表时抛出。
    """

    # JSON 分析报告已经包含结构化端口，优先走专门解析路径。
    if treat_as_analysis:

        # 返回 JSON 分析报告中的模块名和标准化端口列表。
        return _load_analysis_module_and_ports(content, source)

    # 去除注释后匹配模块声明，降低注释中 module 字样的干扰。
    str_commentless_text = strip_comments(content)  # 去注释后的 Verilog 文本

    # 在去注释文本中寻找第一个模块声明。
    module_match = MODULE_RE.search(str_commentless_text)  # 模块头匹配结果

    # 没有模块声明时给出带路径的解析错误。
    if not module_match:

        # 阻止生成无 DUT 名称的 testbench。
        raise ValueError(f"> ERR: [Python] no module declaration found in {source}")

    # 提取模块名，供 testbench 命名和 DUT 例化使用。
    str_module_name = module_match.group(1)  # 源码声明中的 DUT 名称

    # 从 Verilog 源码中收集生成信号声明所需的端口元数据。
    list_ports = extract_ports(content)  # Verilog 源码解析得到的端口描述列表

    # 返回生成器需要的模块名和端口清单。
    return str_module_name, list_ports

# 读取 rtl_analysis.json 中的模块名和端口信息。
def _load_analysis_module_and_ports(
    content: str,
    source: Path,
) -> tuple[str, list[dict[str, str | bool | None]]]:
    """
    从 rtl_analysis.json 文本中标准化模块端口。

    :param content: JSON 格式的 RTL 分析报告文本。
    :param source: JSON 文件路径，用于构造可定位错误消息。
    :return: 模块名和端口描述列表。
    :raises ValueError: JSON 缺少 module_info.name 或 ports 列表时抛出。
    """

    # 解析 JSON 报告，保留 object 类型直到字段结构验证完成。
    obj_payload: Any = json.loads(content)  # JSON 顶层载荷

    # 只有 dict 顶层才能提供 module_info 和 ports 字段。
    if not isinstance(obj_payload, dict):

        # 阻止非对象 JSON 被误当作 rtl_analysis 报告。
        raise ValueError(f"> ERR: [Python] analysis JSON at {source} must be an object.")

    # 取出 module_info 字段并验证对象结构。
    dict_module_info = obj_payload.get("module_info") or {}  # JSON 模块信息字段

    # module_info 非对象时不能继续读取 name。
    if not isinstance(dict_module_info, dict):

        # 报告结构错误，避免后续 AttributeError。
        raise ValueError(f"> ERR: [Python] analysis JSON at {source} has invalid module_info.")

    # 将 JSON 报告中的 DUT 名称归一化为空值可判定的字符串。
    str_module_name = str(dict_module_info.get("name") or "")  # JSON 报告声明的 DUT 名称

    # 取出端口数组，稍后逐项验证。
    list_port_payload = obj_payload.get("ports")  # JSON 原始端口字段

    # 缺少模块名或 ports 不是列表时给出兼容旧行为的错误。
    if not str_module_name or not isinstance(list_port_payload, list):

        # 模块名和端口列表是 testbench 生成的最小输入合同。
        raise ValueError(
            f"> ERR: [Python] analysis JSON at {source} is missing module_info.name or ports."
        )

    # 标准化 JSON 端口，过滤字段不完整的条目。
    list_normalized_ports = [  # 可直接渲染的端口描述列表
        _normalize_analysis_port(cast(dict[str, Any], dict_port))  # 单个 JSON 端口转换结果
        for dict_port in list_port_payload  # JSON ports 数组成员
        if isinstance(dict_port, dict)  # 只接受对象条目
        and dict_port.get("name")  # 端口名必须存在
        and dict_port.get("direction")  # 端口方向必须存在
    ]

    # 返回 JSON 中声明的模块名和端口。
    return str_module_name, list_normalized_ports

# 将单个 rtl_analysis.json 端口条目转换成脚本内部字典。
def _normalize_analysis_port(dict_port: dict[str, Any]) -> dict[str, str | bool | None]:
    """
    标准化 rtl_analysis.json 中的一条端口记录。

    :param dict_port: JSON ports 数组中的单个端口对象。
    :return: 包含方向、位宽上下界、名称、时钟标志和复位标志的端口字典。
    """

    # JSON width 字段缺省或类型异常时按 1 bit 处理。
    int_width = dict_port.get("width") if isinstance(dict_port.get("width"), int) else 1  # 端口位宽

    # 多位端口需要写出 msb，否则声明时省略范围。
    str_width_msb = str(int_width - 1) if int_width > 1 else None  # 端口范围最高位

    # 多位端口还要补齐最低位，单 bit 声明保持标量形式。
    str_width_lsb = "0" if int_width > 1 else None  # 端口范围最低位

    # 端口名统一转字符串，便于正则角色识别和 Verilog 输出。
    str_port_name = str(dict_port["name"])  # 端口名称

    # role 字段或命名模式命中时视为时钟端口。
    bool_is_clock = bool(  # 时钟端口标志
        dict_port.get("role") == "clock" or CLK_NAME_RE.search(str_port_name)  # clock role 或名称命中
    )

    # role 字段或命名模式命中时视为复位端口。
    bool_is_reset = bool(  # 复位端口标志
        dict_port.get("role") == "reset" or RST_NAME_RE.search(str_port_name)  # JSON reset 角色或复位命名命中
    )

    # 返回生成器内部统一使用的端口字典。
    return {
        "direction": str(dict_port["direction"]),
        "width_msb": str_width_msb,
        "width_lsb": str_width_lsb,
        "name": str_port_name,
        "is_clock": bool_is_clock,
        "is_reset": bool_is_reset,
    }

# 删除 Verilog 注释，辅助模块头和声明提取。
def strip_comments(text: str) -> str:
    """
    移除 Verilog 块注释和行注释。

    :param text: 原始 Verilog 源码文本。
    :return: 去除注释后的文本，保留换行以减少声明匹配偏移。
    """

    # 先删除可能跨行的块注释。
    str_without_block_comments = re.sub(  # 移除块注释后的源码
        r"/\*.*?\*/",  # Verilog 块注释模式
        "",  # 块注释替换为空文本
        text,  # 原始 Verilog 文本
        flags=re.DOTALL,  # 允许块注释跨行
    )

    # 再删除单行注释，得到端口解析使用的文本。
    return re.sub(r"//.*", "", str_without_block_comments)

# 追加非空顶层片段，避免 split 主循环嵌套过深。
def append_top_level_part(list_parts: list[str], text: str, int_start: int, int_end: int) -> None:
    """把指定范围内的非空端口片段追加到结果列表。

    :param list_parts: 顶层端口片段结果列表。
    :param text: 原始端口列表文本。
    :param int_start: 当前片段起始索引。
    :param int_end: 当前片段结束索引。
    :return: 无返回值；必要时向结果列表追加片段。
    """

    # 截取当前端口片段并去掉外围空白。
    str_part = text[int_start:int_end].strip()  # 当前端口片段

    # 只保存非空片段，避免连续逗号造成空条目。
    if str_part:

        # 追加供端口解析器逐项处理的片段。
        list_parts.append(str_part)

# 按顶层逗号拆分端口片段，忽略位宽和参数括号内部逗号。
def split_top_level_commas(text: str) -> list[str]:
    """
    将端口声明列表按顶层逗号切分。

    :param text: 模块头或声明语句中的端口列表文本。
    :return: 去掉首尾空白后的端口片段列表。
    """

    # 收集拆分后的端口声明片段。
    list_parts: list[str] = []  # 顶层逗号分割结果

    # 记录当前片段在原文本中的起始位置。
    int_start = 0  # 当前片段起点索引

    # 记录括号或方括号嵌套深度，避免误切位宽表达式。
    int_depth = 0  # 当前括号嵌套深度

    # 逐字符扫描，只有顶层逗号才作为切分点。
    for int_index, str_char in enumerate(text):

        # 左括号会提升嵌套深度。
        if str_char in "([":

            # 进入端口范围或参数表达式。
            int_depth += 1  # 更新后的嵌套深度

        # 右括号会降低嵌套深度，但不允许深度变负。
        elif str_char in ")]":

            # 离开端口范围或参数表达式。
            int_depth = max(0, int_depth - 1)  # 修正后的嵌套深度

        # 顶层逗号标记一个完整端口片段结束。
        elif str_char == "," and int_depth == 0:

            # 逗号前的片段交给 helper 做空值过滤。
            append_top_level_part(list_parts, text, int_start, int_index)

            # 下一个端口片段从逗号后一位开始。
            int_start = int_index + 1  # 下一片段起点索引

    # 尾部片段同样交给 helper 做空值过滤。
    append_top_level_part(list_parts, text, int_start, len(text))

    # 返回所有顶层片段。
    return list_parts

# 从 Verilog 文本中提取 ANSI 和 body-style 端口声明。
def extract_ports(text: str) -> list[dict[str, str | bool | None]]:
    """
    解析 Verilog 模块端口并去重。

    :param text: 原始 Verilog 源码文本。
    :return: 按出现顺序排列的标准化端口描述列表。
    """

    # 去除注释，避免注释中的 input/output 干扰声明扫描。
    str_commentless_text = strip_comments(text)  # 去注释后的源码

    # 保存端口解析结果，顺序与源码声明保持一致。
    list_ports: list[dict[str, str | bool | None]] = []  # 标准化端口列表

    # 记录已加入的端口名，避免 ANSI 和 body-style 重复。
    set_seen_names: set[str] = set()  # 已解析端口名集合

    # 搜索源码中的首个模块头，供 ANSI 端口声明解析。
    module_match = MODULE_RE.search(str_commentless_text)  # 待拆分的 ANSI 模块头

    # 模块头存在时先解析括号内声明。
    if module_match:

        # ANSI 端口可以继承前一个片段的方向和位宽。
        dict_state: dict[str, str | None] = {  # ANSI 端口解析状态
            "direction": None,  # 当前继承的端口方向
            "width_msb": None,  # 当前继承的范围最高位
            "width_lsb": None,  # 当前继承的范围最低位
        }

        # 逐个解析模块头中的顶层端口片段。
        _collect_ports_from_pieces(
            split_top_level_commas(module_match.group(3) or ""),
            dict_state,
            list_ports,
            set_seen_names,
        )

    # body-style 声明按语句分别解析，不跨声明继承状态。
    for decl_match in re.finditer(
        r"^\s*(input|output|inout)\b.*?;",
        str_commentless_text,
        re.MULTILINE,
    ):

        # 每条声明单独维护方向和位宽状态。
        dict_state = {  # body-style 声明解析状态
            "direction": None,  # 当前声明中的端口方向
            "width_msb": None,  # 当前声明中的范围最高位
            "width_lsb": None,  # 当前声明中的范围最低位
        }

        # 将当前声明语句拆成一个或多个端口片段。
        _collect_ports_from_pieces(
            split_top_level_commas(decl_match.group(0)),
            dict_state,
            list_ports,
            set_seen_names,
        )

    # 返回去重后的端口列表。
    return list_ports

# 将多个端口片段解析后追加到端口列表。
def _collect_ports_from_pieces(
    list_pieces: list[str],
    dict_state: dict[str, str | None],
    list_ports: list[dict[str, str | bool | None]],
    set_seen_names: set[str],
) -> None:
    """
    解析端口片段集合并按名称去重。

    :param list_pieces: 顶层逗号切出的端口片段。
    :param dict_state: 当前声明继承的方向和位宽状态。
    :param list_ports: 待追加的端口结果列表。
    :param set_seen_names: 已收集端口名集合。
    :return: 本函数原地更新 list_ports 和 set_seen_names，无业务返回值。
    """

    # 顺序解析端口片段，保留源码中的端口排列。
    for str_piece in list_pieces:

        # 解析单个端口片段，无法识别时返回 None。
        dict_parsed_port = _parse_port_piece(str_piece, dict_state)  # 当前片段解析结果

        # 过滤空片段、方向缺失片段和重复端口名。
        if dict_parsed_port and dict_parsed_port["name"] not in set_seen_names:

            # 将新端口追加到渲染输入列表。
            list_ports.append(dict_parsed_port)

            # 记录端口名，防止后续 body-style 声明重复加入。
            set_seen_names.add(str(dict_parsed_port["name"]))

# 解析单个端口片段并维护方向/位宽继承状态。
def _parse_port_piece(
    piece: str,
    state: dict[str, str | None],
) -> dict[str, str | bool | None] | None:
    """
    将单个 Verilog 端口片段解析为标准化字典。

    :param piece: 逗号切分后的单个端口片段。
    :param state: 当前端口声明继承的方向和位宽状态。
    :return: 解析成功时返回端口字典；无法识别时返回 None。
    """

    # 去除片段外围空白和语句末尾分号。
    str_piece = piece.strip().rstrip(";")  # 清理后的端口片段

    # 空片段不参与端口生成。
    if not str_piece:

        # 返回 None 表示没有可用端口。
        return None

    # 删除默认赋值，避免端口名解析吃到表达式。
    str_piece = re.sub(r"\s*=\s*.*$", "", str_piece)  # 去默认值后的端口片段

    # 解析 input/output/inout 并更新继承状态。
    str_piece = _consume_direction(str_piece, state)  # 去方向关键字后的片段

    # 删除 wire/reg/logic/tri 等声明类型关键字。
    str_piece = _consume_net_type(str_piece)  # 去网络类型后的片段

    # 消除 signed/unsigned 后让范围解析直接面对位宽或端口名。
    str_piece = _consume_signedness(str_piece)  # 去符号修饰后的片段

    # 提取位宽范围并更新继承状态。
    str_piece = _consume_range(str_piece, state)  # 去位宽范围后的片段

    # 从剩余片段开头提取合法 Verilog 标识符。
    name_match = re.match(r"^([a-zA-Z_]\w*)\b", str_piece)  # 端口名匹配结果

    # 缺少端口名或方向时无法生成有效声明。
    if not name_match or not state.get("direction"):

        # 返回 None 表示该片段不是完整端口。
        return None

    # 保存该片段真正声明的 Verilog 标识符。
    str_name = name_match.group(1)  # 当前片段端口名

    # 返回标准化端口字典。
    return {
        "direction": state["direction"],
        "width_msb": state.get("width_msb"),
        "width_lsb": state.get("width_lsb"),
        "name": str_name,
        "is_clock": bool(CLK_NAME_RE.search(str_name)),
        "is_reset": bool(RST_NAME_RE.search(str_name)),
    }

# 识别并消费端口方向关键字。
def _consume_direction(piece: str, state: dict[str, str | None]) -> str:
    """
    更新端口方向状态并返回剩余片段。

    :param piece: 当前待解析端口片段。
    :param state: 需要原地更新的端口解析状态。
    :return: 去掉方向关键字后的片段；未命中时返回原片段。
    """

    # 匹配 Verilog 端口方向关键字。
    direction_match = re.match(r"^(input|output|inout)\b\s*(.*)$", piece)  # 方向匹配结果

    # 没有方向关键字时沿用现有状态。
    if not direction_match:

        # 返回原片段，让后续类型和名称解析继续处理。
        return piece

    # 记录当前片段显式声明的端口方向。
    state["direction"] = direction_match.group(1)  # 当前端口方向

    # 新方向声明会重置位宽继承，除非后续重新解析到范围。
    state["width_msb"] = None  # 当前端口范围最高位

    # 新方向声明会重置位宽最低位。
    state["width_lsb"] = None  # 当前端口范围最低位

    # 返回方向关键字后的剩余端口片段。
    return direction_match.group(2).strip()

# 识别并消费 Verilog 网络或变量类型关键字。
def _consume_net_type(piece: str) -> str:
    """
    去除端口片段开头的网络类型关键字。

    :param piece: 当前待解析端口片段。
    :return: 去掉 wire/reg/logic/tri 后的片段；未命中时返回原片段。
    """

    # 匹配常见端口声明类型。
    type_match = re.match(r"^(?:wire|reg|logic|tri)\b\s*(.*)$", piece)  # 网络类型匹配结果

    # 未声明网络类型时保持片段不变。
    if not type_match:

        # 返回原片段供后续符号和范围解析。
        return piece

    # 返回类型关键字之后的端口片段。
    return type_match.group(1).strip()

# 识别并消费 signed/unsigned 修饰。
def _consume_signedness(piece: str) -> str:
    """
    去除端口片段开头的符号修饰。

    :param piece: 当前待解析端口片段。
    :return: 去掉 signed/unsigned 后的片段；未命中时返回原片段。
    """

    # 匹配 signed 或 unsigned 修饰。
    signed_match = re.match(r"^(?:signed|unsigned)\b\s*(.*)$", piece)  # 符号修饰匹配结果

    # 未声明符号修饰时保持片段不变。
    if not signed_match:

        # 返回原片段供位宽解析使用。
        return piece

    # 返回符号修饰之后的端口片段。
    return signed_match.group(1).strip()

# 识别并消费位宽范围。
def _consume_range(piece: str, state: dict[str, str | None]) -> str:
    """
    解析端口范围并更新位宽状态。

    :param piece: 当前待解析端口片段。
    :param state: 需要原地更新的端口解析状态。
    :return: 去掉位宽范围后的片段；未命中时返回原片段。
    """

    # 匹配 Verilog 方括号范围表达式。
    range_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", piece)  # 位宽范围匹配结果

    # 没有范围时沿用之前的位宽状态。
    if not range_match:

        # 返回原片段供端口名解析。
        return piece

    # 拆分 msb:lsb 范围表达式。
    list_width_parts = [  # 位宽上下界文本
        str_item.strip()  # 单侧范围表达式
        for str_item in range_match.group(1).split(":")  # msb:lsb 原始范围
    ]

    # 只有标准二元范围才更新位宽状态。
    if len(list_width_parts) == 2:

        # 将声明中的左侧范围保存为后续端口继承的最高位。
        state["width_msb"] = list_width_parts[0]  # 当前声明范围最高位

        # 将声明中的右侧范围保存为后续端口继承的最低位。
        state["width_lsb"] = list_width_parts[1]  # 当前声明范围最低位

    # 返回范围表达式后的剩余端口片段。
    return range_match.group(2).strip()

# 生成完整测试平台源码。
def generate_testbench(
    module_name: str,
    ports: list[dict[str, str | bool | None]],
    clk_period_ns: int,
    tb_language: str,
) -> str:
    """
    渲染包含 DUT 例化、复位任务、激励和保守阻断的 testbench。

    :param module_name: 待测 Verilog 模块名称。
    :param ports: 标准化端口描述列表。
    :param clk_period_ns: 测试平台时钟周期，单位 ns。
    :param tb_language: 输出语言，当前仅支持 verilog。
    :return: 带语义行尾注释的 testbench 源码文本。
    """

    # 根据 DUT 名称派生顶层 testbench 模块名。
    str_tb_name = f"tb_{module_name}"  # 生成文件中的 testbench 模块名

    # 收集未带 Python 生成注释的 Verilog 源码行。
    list_lines: list[str] = []  # testbench 源码行缓存

    # 写入文件头、timescale 和模块声明。
    _append_header_lines(list_lines, str_tb_name, module_name, clk_period_ns)

    # 写入端口声明和时钟翻转过程。
    _append_signal_lines(list_lines, ports)

    # 写入启动监控 initial 块。
    _append_start_monitor_lines(list_lines)

    # 提取复位端口，供复位任务和主激励复用。
    list_reset_ports = [  # 自动识别出的复位端口
        dict_port  # 复位端口描述
        for dict_port in ports  # 全部 DUT 端口
        if dict_port["is_reset"]  # 命中复位角色的端口
    ]

    # 如果存在复位端口则追加 apply_reset 任务。
    _append_reset_task_lines(list_lines, list_reset_ports)

    # 写入 DUT 例化和端口映射。
    _append_dut_instance_lines(list_lines, module_name, ports)

    # 写入波形、激励、保守 fatal 和仿真结束流程。
    _append_main_stimulus_lines(list_lines, str_tb_name, ports, list_reset_ports)

    # 写入仿真超时保护和模块结束。
    _append_timeout_lines(list_lines, str_tb_name)

    # 为 Verilog 行追加语义注释并拼成最终文件文本。
    return "\n".join(add_semantic_comments(list_lines, str_tb_name, module_name))

# 批量追加 Verilog 行，集中管理空行和片段拼接。
def _append_many(list_lines: list[str], tuple_lines: tuple[str, ...]) -> None:
    """
    将一组 Verilog 行追加到输出缓存。

    :param list_lines: testbench 源码行缓存。
    :param tuple_lines: 按顺序追加的 Verilog 行。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 逐行追加调用方提供的 Verilog 片段。
    for str_line in tuple_lines:

        # 保留空行和缩进，让生成文件保持可读。
        list_lines.append(str_line)

# 写入测试平台文件头和模块开头。
def _append_header_lines(
    list_lines: list[str],
    tb_name: str,
    module_name: str,
    clk_period_ns: int,
) -> None:
    """
    追加 testbench 顶部说明、timescale、模块声明和时钟参数。

    :param list_lines: testbench 源码行缓存。
    :param tb_name: testbench 模块名称。
    :param module_name: DUT 模块名称。
    :param clk_period_ns: 时钟周期，单位 ns。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 写入头部注释和时钟周期参数。
    _append_many(
        list_lines,
        (
            f"// Auto-generated Erie testbench scaffold for {module_name}",
            "// Semantic vector hash placeholder: ERIE_VECTOR_HASH <pending>",
            "`timescale 1ns / 1ps",
            "",
            f"module {tb_name};",
            "",
            f"    localparam CLK_PERIOD = {clk_period_ns};",
            "",
        ),
    )

# 写入端口声明和时钟过程。
def _append_signal_lines(
    list_lines: list[str],
    ports: list[dict[str, str | bool | None]],
) -> None:
    """
    根据端口方向生成 testbench 信号声明和时钟翻转过程。

    :param list_lines: testbench 源码行缓存。
    :param ports: 标准化端口描述列表。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 逐个端口生成 reg/wire 声明。
    for dict_port in ports:

        # 时钟端口始终作为 reg 并初始化为 0。
        if dict_port["is_clock"]:

            # 写入时钟驱动信号声明。
            list_lines.append(f"    reg {dict_port['name']} = 1'b0;")

            # 当前端口已处理，继续下一个端口。
            continue

        # 根据端口位宽渲染声明范围。
        str_decl_width = render_width(dict_port)  # Verilog 位宽声明片段

        # DUT 输出在 testbench 中声明为 wire。
        if dict_port["direction"] == "output":

            # 写入输出观测信号声明。
            list_lines.append(f"    wire{str_decl_width} {dict_port['name']};")

        # 输入和 inout 在骨架中按 reg 驱动。
        else:

            # 写入输入或双向端口驱动声明。
            list_lines.append(f"    reg{str_decl_width} {dict_port['name']};")

    # 信号声明和时钟过程之间保留空行。
    list_lines.append("")

    # 为所有时钟端口生成半周期翻转过程。
    for dict_port in ports:

        # 非时钟端口不需要 always 翻转。
        if not dict_port["is_clock"]:

            # 跳过普通输入、输出和复位端口。
            continue

        # 写入时钟翻转 always 块。
        list_lines.append(
            f"    always #(CLK_PERIOD/2) {dict_port['name']} = ~{dict_port['name']};"
        )

    # 时钟过程结束后保留空行。
    list_lines.append("")

# 启动监控块只报告 scaffold 已开始运行。
def _append_start_monitor_lines(list_lines: list[str]) -> None:
    """
    追加 testbench 启动状态输出。

    :param list_lines: testbench 源码行缓存。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 写入一个只报告启动状态的监控块。
    _append_many(
        list_lines,
        (
            "    initial begin",
            '        $display("[TB_MONITOR] Time: %0t | Starting tb_generator scaffold.", $time);',
            "    end",
            "",
        ),
    )

# 根据复位端口写入 apply_reset 任务。
def _append_reset_task_lines(
    list_lines: list[str],
    reset_ports: list[dict[str, str | bool | None]],
) -> None:
    """
    在存在复位端口时追加复位任务。

    :param list_lines: testbench 源码行缓存。
    :param reset_ports: 自动识别出的复位端口列表。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 没有复位端口时不生成 apply_reset 任务。
    if not reset_ports:

        # 直接返回，主激励会使用固定延时替代复位。
        return

    # 选择第一个复位端口作为骨架复位控制信号。
    str_reset_name = str(reset_ports[0]["name"])  # 复位端口名称

    # 低有效复位通常带 _n，其他复位按高有效处理。
    str_active_value = "1'b0" if "_n" in str_reset_name.lower() else "1'b1"  # 复位有效值

    # 释放复位时使用有效值的反相。
    str_inactive_value = "1'b1" if str_active_value == "1'b0" else "1'b0"  # 复位释放值

    # 写入复位任务，保持旧脚本的等待周期。
    _append_many(
        list_lines,
        (
            "    //测试任务: apply_reset - 施加并释放复位",
            "    task apply_reset;",
            "        begin",
            f"            {str_reset_name} = {str_active_value};",
            "            #(CLK_PERIOD * 3);",
            f"            {str_reset_name} = {str_inactive_value};",
            "            #(CLK_PERIOD * 2);",
            "        end",
            "    endtask",
            "",
        ),
    )

# DUT 例化段负责把同名端口接回测试平台信号。
def _append_dut_instance_lines(
    list_lines: list[str],
    module_name: str,
    ports: list[dict[str, str | bool | None]],
) -> None:
    """
    追加 DUT 实例和端口连接。

    :param list_lines: testbench 源码行缓存。
    :param module_name: 待测模块名称。
    :param ports: 标准化端口描述列表。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 写入 DUT 例化起始行。
    list_lines.append(f"    {module_name} u_dut (")

    # 生成逐端口同名连接列表。
    list_port_maps = [  # DUT 端口映射行列表
        f"        .{dict_port['name']}({dict_port['name']})"  # 单个同名端口连接
        for dict_port in ports  # 全部标准化端口
    ]

    # 将端口映射按 Verilog 逗号规则连接。
    list_lines.append(",\n".join(list_port_maps))

    # 结束 DUT 例化并追加空行。
    _append_many(list_lines, ("    );", ""))

# 写入波形、初始化、激励和保守 fatal 流程。
def _append_main_stimulus_lines(
    list_lines: list[str],
    tb_name: str,
    ports: list[dict[str, str | bool | None]],
    reset_ports: list[dict[str, str | bool | None]],
) -> None:
    """
    追加主 initial 块中的波形、输入初始化和 smoke 激励。

    :param list_lines: testbench 源码行缓存。
    :param tb_name: testbench 模块名称。
    :param ports: 标准化端口描述列表。
    :param reset_ports: 自动识别出的复位端口列表。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 写入波形输出配置。
    _append_many(
        list_lines,
        (
            "    initial begin",
            f'        $dumpfile("{tb_name}_waves.vcd");',
            f"        $dumpvars(0, {tb_name});",
            "",
        ),
    )

    # 所有可驱动输入先初始化到零。
    _append_input_assignments(list_lines, ports, zero_value)

    # 复位存在时调用任务，否则等待两个时钟周期。
    if reset_ports:

        # 调用自动生成的复位任务。
        list_lines.append("        apply_reset;")

    # 没有复位端口时使用固定延时让初始信号稳定。
    else:

        # 等待两个时钟周期后进入 nominal 激励。
        list_lines.append("        #(CLK_PERIOD * 2);")

    # 写入 nominal smoke case。
    _append_nominal_case_lines(list_lines, ports)

    # 写入 boundary smoke case 和仿真结束语句。
    _append_boundary_case_lines(list_lines, ports)

# 追加所有输入或双向非时钟端口赋值。
def _append_input_assignments(
    list_lines: list[str],
    ports: list[dict[str, str | bool | None]],
    value_builder: Any,
) -> None:
    """
    按端口列表追加输入和双向端口赋值。

    :param list_lines: testbench 源码行缓存。
    :param ports: 标准化端口描述列表。
    :param value_builder: 根据端口返回 Verilog 字面量的函数。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 遍历所有可驱动端口，排除时钟端口。
    for dict_port in ports:

        # 只对 input/inout 且非时钟端口生成赋值。
        if dict_port["direction"] not in ("input", "inout") or dict_port["is_clock"]:

            # 跳过输出端口和时钟端口。
            continue

        # 计算当前端口的激励字面量。
        str_literal = value_builder(dict_port)  # 当前端口赋值字面量

        # 写入端口赋值语句。
        list_lines.append(f"        {dict_port['name']} = {str_literal};")

# nominal case 使用非零输入触发一次保守观察。
def _append_nominal_case_lines(
    list_lines: list[str],
    ports: list[dict[str, str | bool | None]],
) -> None:
    """
    追加 nominal 输入激励和保守 fatal 说明。

    :param list_lines: testbench 源码行缓存。
    :param ports: 标准化端口描述列表。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 分隔初始化阶段和 nominal case。
    _append_many(list_lines, ("", "        // Case 1: nominal smoke input pattern"))

    # 非复位输入使用示例值进入 nominal case。
    for dict_port in ports:

        # 跳过输出、时钟和复位端口。
        if (
            dict_port["direction"] not in ("input", "inout")
            or dict_port["is_clock"]
            or dict_port["is_reset"]
        ):

            # 该端口不属于 nominal 激励目标。
            continue

        # 计算 nominal 示例激励值。
        str_literal = example_value(dict_port)  # nominal case 输入字面量

        # 写入 nominal 激励赋值。
        list_lines.append(f"        {dict_port['name']} = {str_literal};")

    # 等待输出稳定并报告观察值。
    list_lines.append("        #(CLK_PERIOD * 2);")

    # 选择第一个输出端口作为 smoke 观察对象。
    str_observed_output = next(  # nominal case 观察输出
        (
            str(dict_port["name"])  # nominal 观测输出端口名
            for dict_port in ports  # nominal 观察候选端口全集
            if dict_port["direction"] == "output"  # 只打印 DUT 输出端口
        ),  # 首个输出端口名称
        "",  # 没有输出端口时跳过观测打印
    )

    # 有输出端口时打印具体观测值。
    if str_observed_output:

        # 写入输出观察 display。
        list_lines.append(
            (
                f'        $display("[TB_DATA] Time: %0t | Observed '
                f'{str_observed_output}=%0h", $time, {str_observed_output});'
            )
        )

    # 明确说明该 scaffold 不能声明自检通过。
    _append_many(
        list_lines,
        (
            (
                '        $display("[TB_DATA] Time: %0t | Scaffold observed nominal '
                'output; add module-specific expected values before using as '
                'self-checking evidence.", $time);'
            ),
            (
                '        $fatal(1, "[TB_ERROR] Scaffold requires module-specific '
                'expected comparisons before PASS can be claimed.");'
            ),
            "",
        ),
    )

# boundary case 使用最大值激励暴露端口宽度问题。
def _append_boundary_case_lines(
    list_lines: list[str],
    ports: list[dict[str, str | bool | None]],
) -> None:
    """
    追加 boundary 输入激励和正常仿真结束语句。

    :param list_lines: testbench 源码行缓存。
    :param ports: 标准化端口描述列表。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 写入 boundary case 标题。
    _append_many(list_lines, ("        // Case 2: boundary smoke input pattern",))

    # 非复位输入使用最大值进入 boundary case。
    for dict_port in ports:

        # boundary 阶段只改写可驱动的非复位数据端口。
        if (
            dict_port["direction"] not in ("input", "inout")
            or dict_port["is_clock"]
            or dict_port["is_reset"]
        ):

            # 输出、时钟或复位端口保持上一阶段的控制策略。
            continue

        # 计算当前数据端口可表达的最大激励值。
        str_literal = max_value(dict_port)  # boundary 阶段端口最大值

        # 写入 boundary 阶段的数据端口赋值。
        list_lines.append(f"        {dict_port['name']} = {str_literal};")

    # 写入 boundary 观察和结束语句。
    _append_many(
        list_lines,
        (
            "        #(CLK_PERIOD * 2);",
            (
                '        $display("[TB_DATA] Time: %0t | Scaffold observed boundary '
                'output; expected-value checks are still required.", $time);'
            ),
            "",
            "        #(CLK_PERIOD * 4);",
            '        $display("[TB_INFO] Simulation Finished!");',
            "        $finish;",
            "    end",
            "",
        ),
    )

# 写入超时保护和模块结束。
def _append_timeout_lines(list_lines: list[str], tb_name: str) -> None:
    """
    追加超时保护 initial 块和 testbench 结束行。

    :param list_lines: testbench 源码行缓存。
    :param tb_name: testbench 模块名称。
    :return: 本函数原地更新 list_lines，无业务返回值。
    """

    # 写入超时保护，避免仿真无限运行。
    _append_many(
        list_lines,
        (
            "    initial begin",
            "        #(CLK_PERIOD * 200);",
            '        $display("FAIL: simulation timeout");',
            "        $finish;",
            "    end",
            "",
            f"endmodule //结束测试平台: {tb_name}",
            "",
        ),
    )

# 为生成的 Verilog 行补充语义行尾注释。
def add_semantic_comments(
    lines: list[str],
    tb_name: str,
    module_name: str,
) -> list[str]:
    """
    给未自带注释的 Verilog 物理行追加中文语义注释。

    :param lines: 未统一追加注释的 Verilog 源码行。
    :param tb_name: testbench 模块名称。
    :param module_name: DUT 模块名称。
    :return: 已补充语义注释的 Verilog 物理行列表。
    """

    # 保存处理后的物理行，兼容含内嵌换行的端口映射片段。
    list_rendered: list[str] = []  # 注释补齐后的 Verilog 行

    # 逐行拆分，保证端口映射中的每个物理行都有注释。
    for str_line in lines:

        # 空字符串也需要保留为空行。
        list_physical_lines = str_line.splitlines() or [str_line]  # 当前逻辑行拆出的物理行

        # 逐个处理物理行。
        for str_physical_line in list_physical_lines:

            # 判断该行是否已经空白或自带注释。
            str_stripped = str_physical_line.strip()  # 去空白后的 Verilog 行

            # 空行、纯注释行或已有行尾注释不重复追加。
            if not str_stripped or str_stripped.startswith("//") or "//" in str_physical_line:

                # 原样保留已有注释和空行。
                list_rendered.append(str_physical_line)

                # 当前物理行已经处理完毕。
                continue

            # 为普通 Verilog 语句追加语义说明。
            list_rendered.append(
                f"{str_physical_line} //{semantic_comment_for_line(str_stripped, tb_name, module_name)}"
            )

    # 返回注释补齐后的行列表。
    return list_rendered

# 根据 Verilog 行内容选择对应语义注释。
def semantic_comment_for_line(stripped: str, tb_name: str, module_name: str) -> str:
    """
    为单条 Verilog 语句选择中文行尾注释。

    :param stripped: 去除首尾空白后的 Verilog 物理行。
    :param tb_name: testbench 模块名称。
    :param module_name: DUT 模块名称。
    :return: 描述该 Verilog 行用途的中文注释文本。
    """

    # 优先匹配模块结构、参数和端口相关语句。
    str_structure_comment = _semantic_structure_comment(stripped, tb_name, module_name)  # 结构类注释候选

    # 结构类语句命中后直接返回。
    if str_structure_comment:

        # 返回模块、参数、信号或实例相关注释。
        return str_structure_comment

    # 再匹配仿真控制、波形和输出语句。
    str_runtime_comment = _semantic_runtime_comment(stripped)  # 仿真运行类注释候选

    # 运行类语句命中后直接返回。
    if str_runtime_comment:

        # 返回 display、dump、finish 或延时相关注释。
        return str_runtime_comment

    # 最后匹配通用赋值和块结束语句。
    return _semantic_fallback_comment(stripped)

# 匹配模块结构相关 Verilog 语句。
def _semantic_structure_comment(stripped: str, tb_name: str, module_name: str) -> str:
    """
    返回模块声明、信号声明或实例连接的语义注释。

    :param stripped: 去除首尾空白后的 Verilog 物理行。
    :param tb_name: testbench 模块名称。
    :param module_name: DUT 模块名称。
    :return: 命中时返回中文注释；未命中时返回空字符串。
    """

    # timescale 决定仿真时间单位和精度。
    if stripped.startswith("`timescale"):

        # 返回时间单位说明。
        return "时间单位: 测试平台使用1ns/1ps仿真精度"

    # testbench 模块声明行标记验证对象。
    if stripped.startswith("module "):

        # 返回模块声明说明。
        return f"测试平台: {tb_name} - 验证{module_name}接口行为"

    # 时钟周期参数控制所有延时。
    if stripped.startswith("localparam"):

        # 返回时钟参数说明。
        return "参数: CLK_PERIOD - 定义测试平台时钟周期"

    # reg 声明用于驱动 DUT 输入、时钟或复位。
    if stripped.startswith("reg "):

        # 返回驱动信号说明。
        return "测试信号: 驱动DUT输入或时钟复位"

    # wire 声明用于观察 DUT 输出。
    if stripped.startswith("wire "):

        # 返回输出观测说明。
        return "观测信号: 连接DUT输出用于自检"

    # DUT 实例起始行连接待测模块。
    if stripped.startswith(f"{module_name} "):

        # 返回 DUT 例化说明。
        return f"模块实例: {module_name}/u_dut - 例化待测模块"

    # 端口映射行连接同名 testbench 信号。
    if stripped.startswith("."):

        # 返回端口映射说明。
        return "端口映射: 连接DUT端口与测试平台信号"

    # 未命中结构类语句。
    return ""

# 匹配仿真运行相关 Verilog 语句。
def _semantic_runtime_comment(stripped: str) -> str:
    """
    返回时钟、任务、波形、输出和仿真控制语句的语义注释。

    :param stripped: 去除首尾空白后的 Verilog 物理行。
    :return: 命中时返回中文注释；未命中时返回空字符串。
    """

    # always 语句生成周期性时钟。
    if stripped.startswith("always "):

        # 返回时钟过程说明。
        return "时钟过程: 按半周期翻转测试时钟"

    # initial 块承载初始化、激励或超时保护。
    if stripped.startswith("initial "):

        # 返回 initial 阶段说明。
        return "测试阶段: 执行初始化、激励和自检"

    # task 声明定义可复用复位流程。
    if stripped.startswith("task "):

        # 返回复位任务说明。
        return "测试任务: apply_reset - 初始化并释放DUT复位"

    # endtask 标记复位流程结束。
    if stripped.startswith("endtask"):

        # 返回任务结束说明。
        return "结束测试任务: apply_reset"

    # 波形文件名语句设置 VCD 输出。
    if stripped.startswith("$dumpfile"):

        # 返回波形文件说明。
        return "波形输出: 设置VCD文件名"

    # dumpvars 语句选择记录层级。
    if stripped.startswith("$dumpvars"):

        # 返回波形层级说明。
        return "波形输出: 记录测试平台层级信号"

    # 未命中运行类语句。
    return ""

# 匹配通用 Verilog 语句。
def _semantic_fallback_comment(stripped: str) -> str:
    """
    返回通用控制、输出、赋值和块结束语句的语义注释。

    :param stripped: 去除首尾空白后的 Verilog 物理行。
    :return: 描述该行用途的中文注释。
    """

    # begin 进入复位任务或 initial 块体。
    if stripped == "begin":

        # 返回过程开始说明。
        return "任务过程: 开始执行复位步骤"

    # display 输出测试平台状态或阻断原因。
    if stripped.startswith("$display"):

        # 返回结果输出说明。
        return "结果输出: 打印测试平台状态或阻断原因"

    # finish 结束仿真运行。
    if stripped.startswith("$finish"):

        # 返回仿真结束说明。
        return "仿真控制: 结束测试平台运行"

    # 延时、事件或 repeat 语句用于等待信号稳定。
    if stripped.startswith("#") or stripped.startswith("@") or stripped.startswith("repeat"):

        # 返回时序控制说明。
        return "时序控制: 等待测试平台信号稳定"

    # end 语句关闭当前 Verilog 块。
    if stripped.startswith("end"):

        # 返回代码块结束说明。
        return "结束代码块: 当前测试过程"

    # 赋值语句驱动 testbench 信号。
    if "=" in stripped:

        # 返回激励赋值说明。
        return "激励赋值: 设置测试平台驱动信号"

    # 默认注释用于少数结构性标点行。
    return "测试语句: 保持自检流程可审查"

# 渲染端口声明中的位宽片段。
def render_width(port: dict[str, str | bool | None]) -> str:
    """
    根据端口字典生成 Verilog 位宽声明。

    :param port: 标准化端口描述字典。
    :return: 形如 `` [msb:lsb]`` 的声明片段；单 bit 端口返回空字符串。
    """

    # 单 bit 端口没有 width_msb 字段。
    if not port.get("width_msb"):

        # 返回空字符串以生成标量声明。
        return ""

    # 多 bit 端口保留原始 msb/lsb 文本。
    return f" [{port['width_msb']}:{port['width_lsb']}]"

# 生成端口清零用的 Verilog 字面量。
def zero_value(port: dict[str, str | bool | None]) -> str:
    """
    生成端口初始化为 0 的 Verilog 字面量。

    :param port: 标准化端口描述字典。
    :return: 与端口位宽匹配的零值字面量。
    """

    # boundary 字面量需要知道可覆盖的端口位数。
    int_width = resolved_width(port)  # boundary 端口整数位宽

    # 单 bit 端口使用 1'b0。
    if int_width <= 1:

        # 返回标量零值。
        return "1'b0"

    # 多 bit 端口使用宽度限定的二进制零。
    return f"{int_width}'b0"

# 生成端口最大值用的 Verilog 字面量。
def max_value(port: dict[str, str | bool | None]) -> str:
    """
    生成端口 boundary case 使用的最大值字面量。

    :param port: 标准化端口描述字典。
    :return: 与端口位宽匹配的全 1 或近似全 F 字面量。
    """

    # 清零字面量需要知道声明范围能覆盖多少 bit。
    int_width = resolved_width(port)  # 清零阶段端口整数位宽

    # 单 bit 端口最大值为 1。
    if int_width <= 1:

        # 返回标量一值。
        return "1'b1"

    # 多 bit 端口使用足够覆盖宽度的十六进制 F 串。
    return f"{int_width}'h" + ("F" * max(1, (int_width + 3) // 4))

# 生成 nominal case 使用的示例字面量。
def example_value(port: dict[str, str | bool | None]) -> str:
    """
    生成端口 nominal case 使用的示例激励值。

    :param port: 标准化端口描述字典。
    :return: 与端口位宽匹配的简单非零字面量。
    """

    # nominal smoke 图样根据端口位数选择可综合字面量。
    int_width = resolved_width(port)  # nominal 图样端口宽度

    # 单 bit nominal 输入直接翻到高电平。
    if int_width <= 1:

        # 返回单 bit nominal 高电平。
        return "1'b1"

    # 8 bit 及以上端口沿用旧脚本的 A5 smoke 图样。
    if int_width >= 8:

        # 返回 8'hA5 示例值。
        return "8'hA5"

    # 小位宽多 bit 端口使用十进制 1。
    return f"{int_width}'d1"

# 根据 msb/lsb 字段计算端口整数位宽。
def resolved_width(port: dict[str, str | bool | None]) -> int:
    """
    解析端口位宽，无法解析表达式时回退为 1。

    :param port: 标准化端口描述字典。
    :return: 整数位宽；参数化或缺失范围默认返回 1。
    """

    # 读取声明范围左侧文本，参数化表达式会在转换阶段回退。
    str_msb = port.get("width_msb")  # 待解析范围最高位

    # 读取声明范围右侧文本，缺失时按标量端口处理。
    str_lsb = port.get("width_lsb")  # 待解析范围最低位

    # 缺少任一边界时按标量处理。
    if str_msb is None or str_lsb is None:

        # 返回标量位宽。
        return 1

    # 仅对纯整数字面量范围计算位宽。
    try:

        # 计算 Verilog [msb:lsb] 对应的包含式宽度。
        return int(str(str_msb)) - int(str(str_lsb)) + 1

    # 参数化范围不能静态求值时保守回退。
    except ValueError:

        # 返回标量位宽，避免生成非法 Python 异常。
        return 1

# 脚本入口只在直接执行时触发。
if __name__ == "__main__":

    # 将 main 返回码转换为进程退出码。
    raise SystemExit(main())
