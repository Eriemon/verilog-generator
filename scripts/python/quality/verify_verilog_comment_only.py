#!/usr/bin/env python3
"""校验 Verilog 注释增强结果是否只改变行注释和空白。"""

# future annotations 避免 CLI 类型提示在运行期求值。
from __future__ import annotations

# 标准库负责参数解析、错误输出、路径读取和运行期字节码策略。
import argparse
import sys
from pathlib import Path

# 脚本通常在治理流程中批量运行，不应在源码树旁生成 pyc。
sys.dont_write_bytecode = True  # 禁止写入 __pycache__

# 多字符运算符文本集中维护，避免 tokenizer 逐项硬编码分支。
STR_MULTI_CHAR_OPERATORS = (
    "<<<= >>>= === !== ==? !=? <<< >>> <<= >>= ** <= >= == != && || "
    "-> => ++ -- += -= *= /= %= << >> :: .*"
)  # Verilog 多字符运算符空格分隔文本

# 按长度降序匹配，保证 <<< 在 << 之前被消费。
TUPLE_MULTI_CHAR_OPERATORS = tuple(STR_MULTI_CHAR_OPERATORS.split())  # tokenizer 优先尝试的运算符序列

# 注释专用异常用于把校验失败和系统异常分开处理。
class CommentOnlyError(Exception):
    """表示 Verilog 注释增强结果改变了源码 token 或注释规则。"""

# create_parser 只定义 CLI 合同，不读取文件。
def create_parser() -> argparse.ArgumentParser:
    """
    创建 comment-only 校验脚本的参数解析器。

    :return: 已注册 before、after 和 require-comment-delta 参数的解析器。
    """

    # 描述文本保持旧 CLI 的英文 help 风格。
    str_description = "Verify a Verilog rewrite only changed comments and whitespace."  # argparse 描述文本

    # parser 负责保留既有位置参数和选项名称。
    parser = argparse.ArgumentParser(description=str_description)  # comment-only 校验参数解析器

    # before 是格式化前或注释前的基线文件。
    parser.add_argument("before", help="Baseline formatted Verilog file")

    # after 是添加行注释后的候选文件。
    parser.add_argument("after", help="Comment-annotated Verilog file")

    # require-comment-delta 用于防止空跑注释器。
    parser.add_argument(
        "--require-comment-delta",
        action="store_true",
        help="Fail when the annotated file has no added or changed // line comments.",
    )

    # 返回完整 parser 交给 main 解析。
    return parser

# line_col 将字符偏移转换为用户可读位置。
def line_col(str_text: str, int_index: int) -> tuple[int, int]:
    """
    计算字符偏移对应的 1-based 行列号。

    :param str_text: 需要定位的完整源码文本。
    :param int_index: 源码文本中的字符偏移。
    :return: 由行号和列号组成的二元组。
    """

    # 行号通过统计偏移前的换行符得到。
    int_line = str_text.count("\n", 0, int_index) + 1  # 偏移所在的 1-based 行号

    # 最近换行位置决定当前列号起点。
    int_last_newline = str_text.rfind("\n", 0, int_index)  # 偏移前最近的换行符位置

    # 没有前置换行时，列号直接来自偏移。
    if int_last_newline == -1:

        # 文件首行列号从 1 开始。
        return int_line, int_index + 1

    # 非首行列号需要扣除最近换行符位置。
    return int_line, int_index - int_last_newline

# is_identifier_start 判断 Verilog 标识符起始字符。
def is_identifier_start(str_char: str) -> bool:
    """
    判断字符是否可以作为 Verilog 标识符起始字符。

    :param str_char: 当前 tokenizer 正在检查的单个字符。
    :return: True 表示该字符可开启普通或转义相关标识符。
    """

    # Verilog 标识符允许字母、下划线、美元符和反引号宏起始。
    return str_char.isalpha() or str_char in "_$`"

# is_identifier_part 判断 Verilog 标识符后续字符。
def is_identifier_part(str_char: str) -> bool:
    """
    判断字符是否可以继续组成 Verilog 标识符。

    :param str_char: 当前 tokenizer 正在检查的单个字符。
    :return: True 表示该字符可作为标识符后续部分。
    """

    # 后续字符额外允许数字，保持旧 tokenizer 的宽松策略。
    return str_char.isalnum() or str_char in "_$`"

# is_number_part 判断数字 token 的宽松组成字符。
def is_number_part(str_char: str) -> bool:
    """
    判断字符是否可以继续组成 Verilog 数字字面量。

    :param str_char: 当前 tokenizer 正在检查的单个字符。
    :return: True 表示该字符可作为数字 token 的一部分。
    """

    # Verilog 数字可能包含进制标记、未知值、下划线和小数点。
    return str_char.isalnum() or str_char in "_'$?."

# consume_string 保留字符串字面量原文并处理转义。
def consume_string(str_text: str, int_start: int, str_source_label: str) -> tuple[str, int]:
    """
    从双引号起点消费一个 Verilog 字符串字面量。

    :param str_text: 正在扫描的 Verilog 源码文本。
    :param int_start: 字符串起始双引号位置。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :return: 字符串 token 原文和下一个扫描偏移。
    :raises CommentOnlyError: 字符串缺少结束双引号时抛出。
    """

    # 起始双引号已经由调用方确认，扫描从下一个字符开始。
    int_index = int_start + 1  # 当前字符串扫描位置

    # 循环直到找到未转义的结束双引号。
    while int_index < len(str_text):

        # 当前字符决定是否处理转义或结束字符串。
        str_char = str_text[int_index]  # 字符串内部当前字符

        # 反斜杠转义会保护后一个字符。
        if str_char == "\\":

            # 跳过转义符和被转义字符。
            int_index += 2  # 转义后的下一个候选位置

            # 继续扫描字符串后续内容。
            continue

        # 未转义双引号结束当前字符串。
        if str_char == '"':

            # 返回包含双引号边界的字符串 token。
            return str_text[int_start : int_index + 1], int_index + 1

        # 普通字符只推进一个位置。
        int_index += 1  # 下一处字符串字符偏移

    # 计算错误位置，帮助用户定位未闭合字符串。
    tuple_location = line_col(str_text, int_start)  # 未闭合字符串起点行列

    # 字符串未闭合会让 token 对比失去意义。
    raise CommentOnlyError(
        f"> ERR: [Python] Unterminated string literal in {str_source_label}:{tuple_location[0]}:{tuple_location[1]}"
    )

# consume_line_comment 跳过 Verilog 单行注释。
def consume_line_comment(str_text: str, int_start: int) -> int:
    """
    消费从 // 开始的 Verilog 单行注释。

    :param str_text: 正在扫描的 Verilog 源码文本。
    :param int_start: 单行注释起始斜杠位置。
    :return: 注释后下一行起点；文件结尾注释返回文本长度。
    """

    # 单行注释到换行符或文件结尾为止。
    int_newline = str_text.find("\n", int_start + 2)  # 注释后的换行符位置

    # 没有换行时，注释延伸到文件结尾。
    if int_newline == -1:

        # 返回文本长度表示扫描结束。
        return len(str_text)

    # 保留旧行为：消费换行符，让扫描直接进入下一行。
    return int_newline + 1

# consume_block_comment 跳过 Verilog 块注释。
def consume_block_comment(str_text: str, int_start: int, str_source_label: str) -> int:
    """
    消费从 /* 开始的 Verilog 块注释。

    :param str_text: 正在扫描的 Verilog 源码文本。
    :param int_start: 块注释起始斜杠位置。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :return: 块注释结束后的扫描偏移。
    :raises CommentOnlyError: 块注释缺少结束标记时抛出。
    """

    # 块注释以第一个 */ 作为结束边界。
    int_end = str_text.find("*/", int_start + 2)  # 块注释结束标记位置

    # 没有结束标记时报告原始注释起点。
    if int_end == -1:

        # 行列号用于指出未闭合块注释的开头。
        tuple_location = line_col(str_text, int_start)  # 未闭合块注释起点行列

        # 未闭合块注释无法安全进行 token 对比。
        raise CommentOnlyError(
            f"> ERR: [Python] Unterminated block comment in {str_source_label}:{tuple_location[0]}:{tuple_location[1]}"
        )

    # 返回结束标记之后的位置。
    return int_end + 2

# _reject_block_comment 把 after 文件中的块注释视为违规。
def _reject_block_comment(str_text: str, int_index: int, str_source_label: str) -> None:
    """
    对注释增强后的块注释给出统一错误。

    :param str_text: 正在扫描的 Verilog 源码文本。
    :param int_index: 块注释或悬空结束标记的位置。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :return: 不返回业务值；函数总是抛出异常。
    :raises CommentOnlyError: after 文件包含块注释时抛出。
    """

    # 行列号帮助用户定位被禁止的块注释。
    tuple_location = line_col(str_text, int_index)  # 块注释违规行列

    # 注释器只能新增或修改 // 行注释，不能引入块注释。
    raise CommentOnlyError(
        f"> ERR: [Python] Block comments are not allowed in annotated file: "
        f"{str_source_label}:{tuple_location[0]}:{tuple_location[1]}"
    )

# _consume_code_token 消费一个非空白、非注释的 Verilog token。
def _consume_code_token(str_text: str, int_index: int, str_source_label: str) -> tuple[str, int]:
    """
    从当前偏移消费一个 Verilog 代码 token。

    :param str_text: 正在扫描的 Verilog 源码文本。
    :param int_index: 当前 token 起始偏移。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :return: token 原文和下一个扫描偏移。
    """

    # 当前字符决定 token 的消费策略。
    str_char = str_text[int_index]  # 非注释 token 起始字符

    # 字符串字面量必须保留原文参与 token 对比。
    if str_char == '"':

        # consume_string 已处理转义和未闭合错误。
        tuple_string_scan = consume_string(str_text, int_index, str_source_label)  # 字符串 token 扫描结果

        # 返回字符串原文和结束偏移。
        return tuple_string_scan[0], tuple_string_scan[1]

    # 反斜杠起始的 Verilog escaped identifier 到空白为止。
    if str_char == "\\":

        # 转义标识符 token 从反斜杠开始。
        int_start = int_index  # 转义标识符起点

        # 跳过起始反斜杠。
        int_index += 1  # 转义标识符正文起点

        # 转义标识符持续到下一个空白。
        while int_index < len(str_text) and not str_text[int_index].isspace():

            # 继续扩展转义标识符。
            int_index += 1  # 转义标识符候选结束位置

        # 返回完整转义标识符。
        return str_text[int_start:int_index], int_index

    # 普通标识符和宏标识符走相同扫描规则。
    if is_identifier_start(str_char):

        # 标识符起点用于截取 token 原文。
        int_start = int_index  # 普通标识符起点

        # 跳过首字符后继续扫描后续字符。
        int_index += 1  # 普通标识符正文偏移

        # 标识符后续字符按 Verilog 宽松规则消费。
        while int_index < len(str_text) and is_identifier_part(str_text[int_index]):

            # 扩展当前标识符。
            int_index += 1  # 普通标识符结束偏移

        # 返回普通标识符 token。
        return str_text[int_start:int_index], int_index

    # 数字 token 使用宽松字符集合覆盖常见 Verilog 字面量。
    if str_char.isdigit():

        # 数字 token 起点用于截取原文。
        int_start = int_index  # 数字字面量起点

        # 跳过首位数字。
        int_index += 1  # 数字字面量正文偏移

        # 进制、未知值和下划线都归入同一数字 token。
        while int_index < len(str_text) and is_number_part(str_text[int_index]):

            # 扩展当前数字字面量。
            int_index += 1  # 数字字面量结束偏移

        # 返回完整数字字面量。
        return str_text[int_start:int_index], int_index

    # 多字符运算符需要先于单字符兜底匹配。
    for str_operator in TUPLE_MULTI_CHAR_OPERATORS:

        # startswith 保证只接受当前偏移处的运算符。
        if str_text.startswith(str_operator, int_index):

            # 返回命中的 Verilog 运算符。
            return str_operator, int_index + len(str_operator)

    # 其余符号作为单字符 token 处理。
    return str_char, int_index + 1

# tokenize_verilog 生成忽略注释和空白后的 token 序列。
def tokenize_verilog(str_text: str, str_source_label: str, *, bool_reject_block_comments: bool) -> list[str]:
    """
    将 Verilog 源码转换为用于 comment-only 对比的 token 序列。

    :param str_text: 需要扫描的 Verilog 源码文本。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :param bool_reject_block_comments: True 时禁止遇到块注释。
    :return: 忽略空白和注释后的 token 列表。
    """

    # token 列表用于比较 before/after 的真实代码是否一致。
    list_tokens: list[str] = []  # Verilog 代码 token 序列

    # 当前扫描位置从文件开头开始。
    int_index = 0  # 当前 tokenizer 字符偏移

    # 主循环逐字符识别 Verilog token。
    while int_index < len(str_text):

        # 当前字符和后一字符共同识别注释或双字符起点。
        str_char = str_text[int_index]  # 当前扫描字符

        # 末尾没有后一字符时使用空字符串简化判断。
        str_next_char = str_text[int_index + 1] if int_index + 1 < len(str_text) else ""  # 当前字符后的相邻字符

        # 空白不会参与 comment-only 代码 token 对比。
        if str_char.isspace():

            # 跳过空白字符。
            int_index += 1  # 空白后的下一处扫描位置

            # 继续处理后续源码。
            continue

        # 单行注释只影响注释内容，不影响代码 token。
        if str_char == "/" and str_next_char == "/":

            # 消费整行注释。
            int_index = consume_line_comment(str_text, int_index)  # 单行注释后的扫描位置

            # 注释不加入 token 序列。
            continue

        # 块注释在 after 文件中被禁止，在 before 文件中可忽略。
        if str_char == "/" and str_next_char == "*":

            # after 文件不允许块注释增强。
            if bool_reject_block_comments:

                # 报告块注释违规位置。
                _reject_block_comment(str_text, int_index, str_source_label)

            # before 文件块注释只作为注释跳过。
            int_index = consume_block_comment(str_text, int_index, str_source_label)  # 块注释后的扫描位置

            # 块注释不进入 token 序列。
            continue

        # 悬空 */ 在 after 文件中也视为块注释违规。
        if str_char == "*" and str_next_char == "/" and bool_reject_block_comments:

            # 报告不应出现的块注释结束标记。
            _reject_block_comment(str_text, int_index, str_source_label)

        # 剩余情况都是需要参与代码比较的 token。
        tuple_token_scan = _consume_code_token(str_text, int_index, str_source_label)  # 代码 token 扫描结果

        # token 原文保留给 before/after 比较。
        list_tokens.append(tuple_token_scan[0])

        # 主循环从 token 结束位置继续。
        int_index = tuple_token_scan[1]  # 代码 token 后的扫描偏移

    # 返回完整 token 序列供 before/after 比较。
    return list_tokens

# extract_line_comments 收集源码中的 // 行注释文本。
def extract_line_comments(str_text: str, str_source_label: str, *, bool_reject_block_comments: bool) -> list[str]:
    """
    提取 Verilog 源码中的 // 行注释文本。

    :param str_text: 需要扫描的 Verilog 源码文本。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :param bool_reject_block_comments: True 时禁止遇到块注释。
    :return: 去掉 // 和两端空白后的行注释列表。
    """

    # 行注释列表用于判断注释增强是否真的改变了 RTL 注释。
    list_comments: list[str] = []  # 提取到的行注释文本

    # 当前扫描位置从文本开头开始。
    int_index = 0  # 当前注释扫描偏移

    # 逐字符查找注释和字符串边界。
    while int_index < len(str_text):

        # 注释提取只关心斜杠、星号和字符串边界。
        str_char = str_text[int_index]  # 注释提取当前字符

        # 文本末尾没有后一字符时使用空字符串。
        str_next_char = str_text[int_index + 1] if int_index + 1 < len(str_text) else ""  # 注释提取相邻字符

        # 单行注释内容需要纳入注释 delta 对比。
        if str_char == "/" and str_next_char == "/":

            # 注释终点是换行符或文件结尾。
            int_end = str_text.find("\n", int_index + 2)  # 单行注释结束换行位置

            # 文件末尾注释没有换行符。
            if int_end == -1:

                # 使用文本长度作为注释终点。
                int_end = len(str_text)  # 文件末尾注释结束位置

            # 去掉 // 和两端空白后保存。
            list_comments.append(str_text[int_index + 2 : int_end].strip())

            # 下一轮扫描从注释结束换行之后继续。
            int_index = int_end + 1  # 行注释消费后的偏移

            # 当前注释已经处理完成。
            continue

        # 块注释在 after 文件中不允许出现。
        if str_char == "/" and str_next_char == "*":

            # after 文件遇到块注释立即失败。
            if bool_reject_block_comments:

                # after 中的块注释会突破只允许 // 的边界。
                _reject_block_comment(str_text, int_index, str_source_label)

            # before 文件块注释不参与行注释 delta。
            int_index = consume_block_comment(str_text, int_index, str_source_label)  # before 块注释结束偏移

            # 块注释已经跳过。
            continue

        # 悬空 */ 在 after 文件中按块注释违规处理。
        if str_char == "*" and str_next_char == "/" and bool_reject_block_comments:

            # 悬空结束标记同样说明 after 引入了块注释结构。
            _reject_block_comment(str_text, int_index, str_source_label)

        # 字符串内部的 // 不应被误认为注释。
        if str_char == '"':

            # 跳过完整字符串字面量。
            tuple_string_scan = consume_string(str_text, int_index, str_source_label)  # 注释扫描中的字符串结果

            # 注释提取继续从字符串结束后扫描。
            int_index = tuple_string_scan[1]  # 字符串后的注释扫描偏移

            # 字符串内容不参与注释提取。
            continue

        # 普通字符继续向后扫描。
        int_index += 1  # 下一处注释扫描位置

    # 返回保持源码顺序的行注释文本。
    return list_comments

# find_first_module_index 定位 RTL 正文起点。
def find_first_module_index(str_text: str, str_source_label: str, *, bool_reject_block_comments: bool) -> int | None:
    """
    查找第一个 module 关键字在源码中的字符偏移。

    :param str_text: 需要扫描的 Verilog 源码文本。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :param bool_reject_block_comments: True 时禁止遇到块注释。
    :return: 第一个 module token 起点；不存在时返回 None。
    """

    # module 搜索从文件头开始，以排除头部说明。
    int_index = 0  # module 搜索当前偏移

    # 逐 token 搜索真正的 module 关键字。
    while int_index < len(str_text):

        # module 搜索需要识别注释和字符串以避免误命中。
        str_char = str_text[int_index]  # module 搜索当前字符

        # 文件尾没有相邻字符时，module 搜索使用空字符串占位。
        str_next_char = str_text[int_index + 1] if int_index + 1 < len(str_text) else ""  # module 搜索相邻字符

        # 空白不会影响 module 关键字定位。
        if str_char.isspace():

            # module 前的空白布局不影响关键字位置。
            int_index += 1  # module 搜索跳过空白后的偏移

            # 继续搜索源码正文。
            continue

        # 注释中的 module 文本不能作为 RTL 起点。
        if str_char == "/" and str_next_char == "/":

            # 整行说明注释直接越过，避免命中注释里的 module。
            int_index = consume_line_comment(str_text, int_index)  # module 搜索跳过行注释后的偏移

            # 注释后的 token 才可能是 RTL module。
            continue

        # 块注释在 after 文件中不允许，在 before 文件中可跳过。
        if str_char == "/" and str_next_char == "*":

            # after 文件不允许出现块注释。
            if bool_reject_block_comments:

                # after 中块注释会破坏行注释限定。
                _reject_block_comment(str_text, int_index, str_source_label)

            # before 文件跳过块注释内容。
            int_index = consume_block_comment(str_text, int_index, str_source_label)  # module 搜索跳过块注释后的偏移

            # 块注释不参与 module 搜索。
            continue

        # 字符串中的 module 字样不能作为 RTL 起点。
        if str_char == '"':

            # 字符串字面量整体越过，避免内部文本干扰 module 识别。
            tuple_string_scan = consume_string(str_text, int_index, str_source_label)  # module 搜索中的字符串结果

            # 字符串内部的 module 字样不参与关键字识别。
            int_index = tuple_string_scan[1]  # module 搜索跳过字符串后的偏移

            # 字符串之后继续寻找真实 Verilog token。
            continue

        # 标识符扫描可判断当前 token 是否正好是 module。
        if is_identifier_start(str_char):

            # 标识符起点用于截取候选关键字。
            int_start = int_index  # module 候选标识符起点

            # 跳过首字符后继续扫描后续标识符字符。
            int_index += 1  # module 候选标识符正文偏移

            # 消费完整标识符。
            while int_index < len(str_text) and is_identifier_part(str_text[int_index]):

                # 继续读取当前标识符字符。
                int_index += 1  # module 候选标识符结束偏移

            # 命中 module token 时返回起点。
            if str_text[int_start:int_index] == "module":

                # 返回 RTL 正文的 module 起点。
                return int_start

            # 其他标识符继续向后搜索。
            continue

        # 其他符号不是 module 起点。
        int_index += 1  # 下一处 module 搜索位置

    # 没有 module 时由调用方退回全文注释比较。
    return None

# extract_rtl_line_comments 只比较 module 之后的 RTL 注释。
def extract_rtl_line_comments(str_text: str, str_source_label: str, *, bool_reject_block_comments: bool) -> list[str]:
    """
    提取 RTL 正文区域中的 // 行注释文本。

    :param str_text: 需要扫描的 Verilog 源码文本。
    :param str_source_label: 错误消息中展示的来源文件标签。
    :param bool_reject_block_comments: True 时禁止遇到块注释。
    :return: module 之后的行注释列表；无 module 时返回全文行注释。
    """

    # 查找第一个 module，用于忽略文件头部说明注释。
    int_module_index = find_first_module_index(  # RTL 正文起点偏移
        str_text,  # 需要搜索 module 的完整源码
        str_source_label,  # 错误消息中的来源标签
        bool_reject_block_comments=bool_reject_block_comments,  # 是否禁止 after 块注释
    )

    # 没有 module 时只能退回全文注释比较。
    if int_module_index is None:

        # 返回全文范围内的行注释。
        return extract_line_comments(
            str_text,  # 无 module 时使用完整源码
            str_source_label,  # 全文注释提取的诊断标签
            bool_reject_block_comments=bool_reject_block_comments,  # fallback 阶段是否拒绝块注释
        )

    # 只比较 RTL 正文中的行注释，避免文件头版权注释影响 delta。
    return extract_line_comments(
        str_text[int_module_index:],  # 从 module 起点开始的 RTL 正文
        str_source_label,  # RTL 正文注释提取诊断标签
        bool_reject_block_comments=bool_reject_block_comments,  # RTL 正文阶段是否拒绝 after 块注释
    )

# first_difference 生成人类可读的首个 token 差异摘要。
def first_difference(list_before_tokens: list[str], list_after_tokens: list[str]) -> str:
    """
    生成 before/after token 序列的首个差异摘要。

    :param list_before_tokens: 基线文件的 Verilog token 序列。
    :param list_after_tokens: 注释增强文件的 Verilog token 序列。
    :return: 首个 token 差异或 token 数量差异的摘要文本。
    """

    # zip 只比较共同长度范围内的首个不同 token。
    for int_index, tuple_tokens in enumerate(zip(list_before_tokens, list_after_tokens)):

        # 解包 before/after 的同位置 token。
        str_before_token, str_after_token = tuple_tokens  # 同位置 token 对

        # token 不同时返回最短可定位摘要。
        if str_before_token != str_after_token:

            # repr 保留空白、转义和特殊符号信息。
            return f"first difference at token {int_index}: before={str_before_token!r}, after={str_after_token!r}"

    # 共同长度都相同但列表不等，只可能是 token 数量不同。
    return f"token count differs: before={len(list_before_tokens)}, after={len(list_after_tokens)}"

# verify_comment_only 串联文件读取、token 对比和注释 delta 校验。
def verify_comment_only(
    path_before: Path,
    path_after: Path,
    *,
    bool_require_comment_delta: bool = False,
    require_comment_delta: bool = False,
) -> None:
    """
    校验 after 文件是否只改变了 Verilog 行注释和空白。

    :param path_before: 注释增强前的基线 Verilog 文件路径。
    :param path_after: 注释增强后的候选 Verilog 文件路径。
    :param bool_require_comment_delta: True 时要求 RTL 行注释确实发生变化。
    :param require_comment_delta: 旧版调用方保留的兼容关键字。
    :return: 校验通过时不返回业务值。
    :raises CommentOnlyError: token 改变、块注释违规或注释未变化时抛出。
    """

    # 旧版公开 API 使用未加类型前缀的关键字，内部继续使用治理后的布尔命名。
    bool_effective_require_comment_delta = bool_require_comment_delta or require_comment_delta  # 最终生效的注释变化要求

    # before 文件按 UTF-8 读取，保持旧脚本编码合同。
    str_before_text = path_before.read_text(encoding="utf-8")  # 基线 Verilog 源码文本

    # after 文件按 UTF-8 读取，保持与 before 对称。
    str_after_text = path_after.read_text(encoding="utf-8")  # 注释增强后的 Verilog 源码文本

    # before token 允许既有块注释存在。
    list_before_tokens = tokenize_verilog(str_before_text, str(path_before), bool_reject_block_comments=False)  # 基线代码 token 序列

    # after token 禁止注释器引入块注释。
    list_after_tokens = tokenize_verilog(str_after_text, str(path_after), bool_reject_block_comments=True)  # 注释增强后的代码 token 序列

    # 代码 token 必须完全一致，才说明只改了注释或空白。
    if list_before_tokens != list_after_tokens:

        # 首个差异摘要帮助定位被意外改写的代码。
        str_difference = first_difference(list_before_tokens, list_after_tokens)  # 首个 token 差异摘要

        # token 差异是 comment-only 校验的硬失败。
        raise CommentOnlyError("> ERR: [Python] Code tokens differ after comment annotation: " + str_difference)

    # 调用方要求注释器必须产生行注释变化时才做 delta 对比。
    if bool_effective_require_comment_delta:

        # before 注释只统计 RTL 正文区域。
        list_before_comments = extract_rtl_line_comments(  # 基线 RTL 行注释
            str_before_text,  # 基线文件源码
            str(path_before),  # 基线文件路径标签
            bool_reject_block_comments=False,  # before 允许历史块注释
        )

        # after 注释同样只统计 RTL 正文区域。
        list_after_comments = extract_rtl_line_comments(  # 注释增强后的 RTL 行注释
            str_after_text,  # 注释增强后的源码
            str(path_after),  # after 文件路径标签
            bool_reject_block_comments=True,  # after 禁止新增块注释
        )

        # 注释完全相同时说明注释器没有产生有效变化。
        if list_before_comments == list_after_comments:

            # require-comment-delta 的目的就是捕获空跑。
            raise CommentOnlyError(
                "> ERR: [Python] No added or changed RTL line comments "
                "found after comment annotation."
            )

# main 负责 CLI 参数解析和退出码映射。
def main() -> int:
    """
    执行 comment-only CLI 校验并返回进程退出码。

    :param: 此 CLI 入口没有显式业务参数。
    :return: 0 表示校验通过，1 表示文件读取或 comment-only 校验失败。
    """

    # argparse 保持旧脚本的参数错误处理方式。
    namespace_args: argparse.Namespace = create_parser().parse_args()  # before/after 路径和 delta 开关

    # before 路径由调用方位置参数提供。
    path_before = Path(namespace_args.before)  # 基线 Verilog 文件路径

    # after 路径指向注释增强后的候选文件。
    path_after = Path(namespace_args.after)  # 注释增强 Verilog 文件路径

    # comment-only 校验中的业务异常转换为退出码 1。
    try:

        # 保持旧 CLI 参数语义。
        verify_comment_only(path_before, path_after, bool_require_comment_delta=namespace_args.require_comment_delta)

    # 文件系统、编码和业务校验失败都面向用户输出错误。
    except (OSError, UnicodeDecodeError, CommentOnlyError) as exc:

        # 业务异常可能已经在深层函数中带有标准错误前缀。
        str_error_text = str(exc)  # 原始异常文本

        # 避免把 current-project 错误前缀重复拼入最终错误正文。
        if str_error_text.startswith("> ERR: [Python]"):

            # 深层业务错误只保留正文，外层负责终端错误协议。
            str_error_text = str_error_text.removeprefix("> ERR: [Python]").strip()  # 归一化后的错误正文

        # stderr 输出采用 current-project 错误前缀。
        sys.stderr.write("> ERR: [Python] Comment-only verification failed: " + str_error_text + "\n")

        # 未带前缀的系统错误同样按失败退出处理。
        return 1

    # 成功信息采用 current-project 标准 INFO 前缀。
    print("> INFO: [Python] Comment-only verification successful.")

    # 0 表示 after 文件只改变了允许的注释或空白。
    return 0

# 脚本直运行时将 main 返回值交给 shell。
if __name__ == "__main__":

    # SystemExit 保留 CLI 退出码语义。
    raise SystemExit(main())
