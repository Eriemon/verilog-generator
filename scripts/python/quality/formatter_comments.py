"""从 formatter 输入文本提取带真实位置的 Verilog 注释事实。"""

# future annotations 延迟解析注释事实的动态字段类型。
from __future__ import annotations

# re 匹配固定 header 版本行，dataclass 固化词法位置，Any 描述异构字段。
import re
from dataclasses import dataclass
from typing import Any

# banner 识别器是区域注释与普通分组注释的唯一判定来源。
from .formatter_backend.banners import is_banner_line

# 固定版本字段只覆盖 formatter 双语文件头中的明确版本行。
HEADER_VERSION_FIELD_TEXT = r"^//\s*(?:Version|当前版本)\s*:\s*V\d+(?:\.\d+)*\s*$"  # 固定版本字段正则文本

# 编译后的版本字段模式供每条 header 注释执行确定性匹配。
HEADER_VERSION_FIELD_PATTERN = re.compile(HEADER_VERSION_FIELD_TEXT, re.IGNORECASE)  # 固定版本字段模式

# 固定历史记录必须以日期列开头，防止普通 `// v2 ...` 注释借用文件头豁免。
HEADER_HISTORY_DATE_TEXT = r"(?:\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日)"  # 固定历史日期列

# 历史记录文本组合日期列和版本列，排除缺少日期的普通说明。
HEADER_HISTORY_RECORD_TEXT = rf"^//\s*{HEADER_HISTORY_DATE_TEXT}\s+V\d+(?:\.\d+)*\b"  # 固定历史记录正则文本

# 编译后的历史记录模式供 header 注释分类器复用。
HEADER_HISTORY_RECORD_PATTERN = re.compile(HEADER_HISTORY_RECORD_TEXT, re.IGNORECASE)  # 固定历史记录模式

# CommentLocation 聚合单条注释的位置参数，避免构造函数依赖游离整数。
@dataclass(frozen=True)
class CommentLocation:
    """保存注释事实需要的源码位置。

    属性:
        start: 注释零基起始字符偏移。
        end: 注释零基排他结束字符偏移。
        line_start: 注释一基起始行号。
        line_end: 注释一基结束行号。
        source_line_start: 注释起始行的零基行首偏移。
    """

    # 注释在完整源码中的首字符位置。
    start: int  # 用于切片和列号计算的零基起点

    # 注释之后首字符的位置。
    end: int  # 用于切片和文件头边界判断的排他终点

    # 注释首字符所在行。
    line_start: int  # 对外报告使用的一基起始行号

    # 注释末字符所在行。
    line_end: int  # 对外报告使用的一基结束行号

    # 注释起始行在完整源码中的首字符位置。
    source_line_start: int  # 用于换算一基起始列号的零基行首

# build_comment_facts 扫描真实注释并屏蔽字符串字面量中的标记。
def build_comment_facts(str_source: str, int_header_end: int) -> list[dict[str, Any]]:
    """提取行注释和块注释的文本、类别与真实源码位置。

    参数:
        str_source: formatter 实际消费的完整 Verilog 源文本。
        int_header_end: formatter 识别的固定双语文件头排他结束偏移。
    返回:
        按源码顺序排列的注释事实字典列表。
    """

    # 结果列表保持词法出现顺序，供 VG157 生成确定性 finding。
    list_facts: list[dict[str, Any]] = []  # 当前源码中已经确认的注释事实

    # 字符游标在单次线性扫描中推进，避免用正则误读字符串内容。
    int_index = 0  # 当前待检查字符的零基偏移

    # 行号始终使用一基值，与公开 AST 位置合同一致。
    int_line = 1  # 当前字符所在的源码行号

    # 当前行首偏移用于计算注释的真实起始列。
    int_line_start = 0  # 当前源码行的零基起始偏移

    # 字符串状态阻止 `//` 或 `/*` 字面文本被当作注释。
    bool_in_string = False  # 当前游标是否位于双引号字符串内

    # 顺序扫描直到消费完整源码。
    while int_index < len(str_source):

        # 当前字符决定换行、字符串或注释分支。
        str_char = str_source[int_index]  # 当前扫描字符

        # 换行先更新行号和行首，再继续下一字符。
        if str_char == "\n":

            # 一基行号推进到下一源码行。
            int_line += 1  # 将后续字符定位到下一条源码行

            # 下一字符位置即新行的零基起点。
            int_line_start = int_index + 1  # 下一源码行起始偏移

            # 字符游标越过当前换行符。
            int_index += 1  # 越过已经计入行号的换行字符

            # 当前换行已经完整处理，无需进入其他词法分支。
            continue

        # 字符串内部只识别转义和关闭引号。
        if bool_in_string:

            # 反斜杠连同后一字符整体跳过，防止转义引号提前结束字符串。
            if str_char == "\\" and int_index + 1 < len(str_source):

                # 游标跨过转义符及其目标字符。
                int_index += 2  # 同时越过转义符和被转义字符

                # 转义序列已经消费，继续扫描字符串后续内容。
                continue

            # 未转义双引号关闭当前字符串。
            if str_char == '"':

                # 后续字符恢复普通 Verilog 词法扫描。
                bool_in_string = False  # 当前字符串已经闭合

            # 普通字符串字符只需推进一个位置。
            int_index += 1  # 越过已经检查的字符串字符

            # 字符串内容不得进入注释识别路径。
            continue

        # 普通状态遇到双引号时进入字符串屏蔽范围。
        if str_char == '"':

            # 记录字符串状态，直到后续未转义双引号关闭。
            bool_in_string = True  # 当前游标开始进入字符串

            # 起始引号本身已经消费。
            int_index += 1  # 越过开启字符串屏蔽范围的双引号

            # 字符串起点不参与注释标记识别。
            continue

        # 双斜杠开启延续到当前行尾的行注释。
        if str_source.startswith("//", int_index):

            # 行注释尾部取当前行换行符，文件末尾则取文本长度。
            int_end = str_source.find("\n", int_index)  # 当前行注释排他结束偏移

            # 最后一行没有换行符时使用完整源码长度。
            if int_end < 0:

                # 文件末尾作为当前行注释的排他边界。
                int_end = len(str_source)  # 无末尾换行时的注释结束偏移

            # 保留注释标记与正文，便于版本规则提供最小证据。
            str_text = str_source[int_index:int_end]  # 当前行注释完整文本

            # 聚合行注释位置，确保构造事实时不会调换多个整数参数。
            comment_location_comment_location: CommentLocation = CommentLocation(  # 当前行注释的完整位置范围
                int_index,  # 行注释起始字符偏移
                int_end,  # 行注释排他结束偏移
                int_line,  # 行注释起始行号
                int_line,  # 行注释结束行号
                int_line_start,  # 行注释所在行的行首偏移
            )

            # 将真实偏移和类别转换为公开注释事实。
            dict_fact = _comment_fact(  # 当前行注释的公开事实
                str_source,  # 完整源码文本
                str_text,  # 行注释原始文本
                comment_location_comment_location,  # 行注释位置范围
                int_header_end,  # 固定文件头排他边界
                "line",  # 行注释词法类别
            )

            # 结果列表继续保持词法出现顺序。
            list_facts.append(dict_fact)

            # 游标直接到行尾，下一轮统一处理换行符。
            int_index = int_end  # 当前行注释后的继续扫描位置

            # 行注释正文不再逐字符扫描。
            continue

        # 斜杠星号开启可能跨行的块注释。
        if str_source.startswith("/*", int_index):

            # 查找首个关闭标记；未闭合时保留到文件末尾供其他门禁处理。
            int_closing = str_source.find("*/", int_index + 2)  # 块注释关闭标记偏移

            # 已闭合范围需要包含两个字符的关闭标记。
            int_end = len(str_source) if int_closing < 0 else int_closing + 2  # 块注释排他结束偏移

            # 完整块注释文本用于统计覆盖行数和版本标记。
            str_text = str_source[int_index:int_end]  # 当前块注释完整文本

            # 结束行由块正文包含的换行数量确定。
            int_line_end = int_line + str_text.count("\n")  # 当前块注释的一基结束行号

            # 聚合块注释位置，保留跨行范围和起始行列计算基准。
            comment_location_comment_location: CommentLocation = CommentLocation(  # 当前块注释的完整位置范围
                int_index,  # 块注释起始字符偏移
                int_end,  # 包含关闭标记后的块终点
                int_line,  # 块注释起始行号
                int_line_end,  # 块注释结束行号
                int_line_start,  # 块注释起始行的行首偏移
            )

            # 保存块注释事实，禁止后续规则重新解析原始源码。
            dict_fact = _comment_fact(  # 当前块注释的公开事实
                str_source,  # 用于块注释同线前缀判断的完整源码
                str_text,  # 块注释原始文本
                comment_location_comment_location,  # 块注释位置范围
                int_header_end,  # 判定块注释是否落在固定头中的边界
                "block",  # 块注释词法类别
            )

            # 块注释与行注释共享同一顺序化结果容器。
            list_facts.append(dict_fact)

            # 行号一次推进块注释内部的全部换行。
            int_line += str_text.count("\n")  # 跳过块正文覆盖的全部源码行

            # 跨行块注释结束后更新当前行的真实起点。
            if "\n" in str_text:

                # 最后一个换行符后一字符是块结束行的行首。
                int_line_start = int_index + str_text.rfind("\n") + 1  # 块结束行起始偏移

            # 游标跳到块注释之后，避免正文中的标记被重复识别。
            int_index = int_end  # 防止块正文里的伪标记再次进入词法识别

            # 当前块注释已经完整消费。
            continue

        # 普通代码字符不改变词法状态，只推进游标。
        int_index += 1  # 越过不改变词法状态的普通代码字符

    # 返回按源码顺序冻结的注释事实列表。
    return list_facts

# _comment_fact 把一个已确认词法注释转换成稳定报告字段。
def _comment_fact(
    str_source: str,
    str_text: str,
    location: CommentLocation,
    int_header_end: int,
    str_lexical_kind: str,
) -> dict[str, Any]:
    """构造单条注释的类别、位置与固定文件头豁免事实。

    参数:
        str_source: 完整 Verilog 源文本。
        str_text: 含注释标记的当前注释文本。
        location: 已聚合的注释字符范围和行号。
        int_header_end: formatter 固定文件头排他结束偏移。
        str_lexical_kind: `line` 或 `block` 词法类别。
    返回:
        可直接进入 formatter AST 报告的注释事实字典。
    """

    # 先限定 formatter 实际剥离的 module 前缀范围。
    bool_header_position = int_header_end > 0 and location.end <= int_header_end  # 是否位于 formatter header 范围

    # 单独计算固定行形态，避免把普通 module 前注释视为 header 版本字段。
    bool_fixed_header_line = _is_fixed_header_version_comment(str_text, str_lexical_kind)  # 是否匹配固定版本行

    # 只有同时满足位置和形态的注释才获得版本字样豁免。
    bool_header_exempt = bool_header_position and bool_fixed_header_line  # 当前注释是否属于可豁免版本行

    # 注释前的同一行文本区分纯注释与尾随注释。
    str_prefix = str_source[location.source_line_start:location.start]  # 当前注释之前的同线源码

    # 固定文件头优先于其余注释类别。
    if bool_header_exempt:

        # header 类别明确记录唯一版本字样豁免来源。
        str_kind = "header"  # formatter 已识别的固定文件头注释

    # 块注释保持独立类别，供交付门禁执行禁用策略。
    elif str_lexical_kind == "block":

        # block 类别不依赖同线前缀或 banner 外形。
        str_kind = "block"  # 供规则区分跨行注释的词法类别

    # 同行已有代码说明当前行是尾随说明。
    elif str_prefix.strip():

        # inline 类别覆盖声明和语句尾随注释。
        str_kind = "inline"  # 代码后的同线注释

    # 纯注释行只有标准横幅才属于区域导航。
    elif is_banner_line(str_text.strip()):

        # banner 类别允许其他注释规则按导航语义处理。
        str_kind = "region_banner"  # formatter 标准区域横幅

    # 其余纯注释行由分组或前导说明共同承载。
    else:

        # group_or_leading 不猜测具体绑定实体。
        str_kind = "group_or_leading"  # 分组或结构前导注释

    # 返回字段全部来自当前词法扫描，不制造未知位置。
    return {
        "text": str_text,  # 含标记的完整注释文本
        "kind": str_kind,  # formatter 消费方使用的注释类别
        "line_start": location.line_start,  # 一基起始行号
        "line_end": location.line_end,  # 一基结束行号
        "column_start": location.start - location.source_line_start + 1,  # 一基起始列号
        "header_exempt": bool_header_exempt,  # 固定文件头版本字样豁免标记
    }

# _is_fixed_header_version_comment 只识别 formatter 固定版本字段和历史记录。
def _is_fixed_header_version_comment(str_text: str, str_lexical_kind: str) -> bool:
    """判断候选注释是否是允许携带版本标记的固定 header 行。

    参数:
        str_text: 含注释标记的完整词法文本。
        str_lexical_kind: `line` 或 `block` 词法类别。
    返回:
        固定版本字段或带日期的历史记录返回 True，其余注释返回 False。
    """

    # 块注释不是 formatter 固定双语 header 的版本字段形态。
    if str_lexical_kind != "line":

        # 普通块注释始终进入 VG157 检查范围。
        return False

    # 两个正则分别覆盖版本字段和固定历史表记录。
    return bool(
        HEADER_VERSION_FIELD_PATTERN.fullmatch(str_text.strip())
        or HEADER_HISTORY_RECORD_PATTERN.match(str_text.strip())
    )
