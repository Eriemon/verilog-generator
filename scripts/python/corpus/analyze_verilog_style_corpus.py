#!/usr/bin/env python3
"""从理想和反例 Verilog 语料中提取可复现的风格统计指标。"""

# future annotations 让类型提示保持静态用途，不影响脚本运行时兼容性。
from __future__ import annotations

# 标准库负责 CLI 参数、JSON 写入、正则提取、统计量和路径遍历。
import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

# 批量治理脚本不应在源码目录旁生成 __pycache__。
sys.dont_write_bytecode = True  # 关闭当前脚本运行时的 pyc 写入

# Verilog 源文件后缀集中维护，供目录递归筛选复用。
SET_VERILOG_SUFFIXES = {".v", ".sv", ".vh", ".svh"}  # 允许进入语料统计的 Verilog 文件后缀

# 端口声明正则保留旧脚本对 input/output/inout 行的宽松识别。
RE_PORT_DECL = re.compile(  # 端口声明行匹配器
    r"^\s*(input|output|inout)\b(?P<body>.*?)(?:,|;)?\s*(?://.*)?$",  # 捕获方向后的声明主体
    re.MULTILINE,  # 允许从完整源码文本中逐行查找端口声明
)

# 标识符正则用于端口名和参数名的轻量文本提取。
RE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")  # Verilog 普通标识符匹配器

# 区域横幅只统计 Erie 模板认可的中文区域名称。
STR_REGION_BANNER_PATTERN = (
    r"//[-\s]*(?:配置参数区域|状态参数区域|模块实例化信号|计数信号|状态机信号|"
    r"寄存器信号|标志信号|编码信号|译码信号|其他信号|输出信号|其他信号连线|"
    r"输出信号连线|输出信号处理区域|状态机区域|状态任务处理区域|主要任务处理区域|"
    r"生成块区域|参数检查区域|初始化区域|模块实例化区域)[-\s]*//"
)  # 中文区域横幅正则文本

# 区域横幅正则单独编译，避免每个文件重复解析长表达式。
RE_REGION = re.compile(STR_REGION_BANNER_PATTERN)  # 中文区域横幅匹配器

# 占位注释命中会降低风格分，帮助暴露模板未清理痕迹。
RE_PLACEHOLDER_COMMENT = re.compile(  # 占位或模板化注释匹配器
    r"(?:端口信号注释|参数解释说明中文|默认值,?参数解释|必须要有的注释|此模板未使用|占位|placeholder)",  # 占位词组集合
    re.IGNORECASE,  # 同时识别英文 placeholder 的大小写变体
)

# Verilog 端口方向关键字不能被误当成端口名。
SET_PORT_DIRECTIONS = {"input", "output", "inout"}  # 端口方向关键字集合

# 读取语料时按项目常见编码优先级尝试。
TUPLE_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "latin-1")  # 语料读取编码候选

# 输出报告的数值字段顺序保持旧 JSON 结构。
TUPLE_METRIC_FIELDS = (
    "lines",  # 源文件总行数指标
    "code_lines",  # 非注释代码行数指标
    "comment_lines",  # 含有 // 的行数指标
    "comment_density",  # 注释相对代码行的密度指标
    "tab_indented_lines",  # tab 缩进行数指标
    "space_indented_lines",  # 空格缩进行数指标
    "block_comment_markers",  # 块注释边界标记数量指标
    "ports",  # 端口声明数量指标
    "prefixed_ports",  # 带项目前缀端口数量指标
    "parameters",  # parameter 声明数量指标
    "c_parameters",  # C_ 前缀参数数量指标
    "region_banners",  # 中文区域横幅数量指标
    "has_bilingual_header",  # 双语头部是否存在的二值指标
    "placeholder_comment_hits",  # 占位注释命中数量指标
    "always_blocks",  # always 块数量指标
    "assigns",  # assign 语句数量指标
)  # summary 区域逐项统计的指标字段

# create_parser 只声明公开 CLI，不读取文件。
def create_parser() -> argparse.ArgumentParser:
    """
    创建 Verilog 风格语料分析脚本的参数解析器。

    :param: 此函数不接收外部业务参数。
    :return: 已注册 ideal、bad、out 和 sample-size 参数的解析器。
    """

    # 描述文本沿用英文，保持既有 --help 风格。
    str_description = "Analyze style metrics from ideal/bad Verilog corpora."  # argparse 描述文本

    # parser 只承担命令行合同声明职责。
    parser = argparse.ArgumentParser(description=str_description)  # 语料分析参数解析器

    # ideal 指向正例语料根目录或单文件。
    parser.add_argument("--ideal", type=Path, required=True, help="Path to ideal Verilog corpus.")

    # bad 指向反例或压力语料根目录或单文件。
    parser.add_argument("--bad", type=Path, required=True, help="Path to bad/stress Verilog corpus.")

    # out 是 JSON 指标报告写入位置。
    parser.add_argument("--out", type=Path, required=True, help="Output JSON metrics path.")

    # sample-size 控制低风格分样本数量。
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Number of lowest-style-score samples to retain.",
    )

    # 返回完整解析器给 main 调用。
    return parser

# iter_sources 统一处理单文件和目录语料输入。
def iter_sources(path_root: Path) -> Iterable[Path]:
    """
    按稳定顺序产出输入根下的 Verilog 源文件。

    :param path_root: 语料根目录或单个 Verilog 文件路径。
    :return: 可迭代的 Verilog 源文件路径序列。
    """

    # 单文件输入只在后缀受支持时产出自身。
    if path_root.is_file():

        # 后缀判断使用小写以兼容大写扩展名。
        if path_root.suffix.lower() in SET_VERILOG_SUFFIXES:

            # yield 保持旧脚本对单文件输入的惰性行为。
            yield path_root

        # 单文件路径处理完毕后不再递归。
        return

    # 目录输入按路径名排序，保证报告可复现。
    for path_source in sorted(path_root.rglob("*")):

        # 只统计真实文件，跳过目录和非 Verilog 文本。
        if path_source.is_file() and path_source.suffix.lower() in SET_VERILOG_SUFFIXES:

            # 将候选源文件交给后续指标提取。
            yield path_source

# read_text 以多编码尝试读取语料，避免单个遗留文件中断统计。
def read_text(path_source: Path) -> tuple[str, str]:
    """
    读取 Verilog 文本并返回实际采用的编码名称。

    :param path_source: 待读取的 Verilog 文件路径。
    :return: 二元组，包含源码文本和解码编码标签。
    """

    # 原始字节只读取一次，后续在内存中尝试不同编码。
    bytes_raw = path_source.read_bytes()  # Verilog 文件原始字节

    # 编码候选按最常见到最兜底排序。
    for str_encoding in TUPLE_TEXT_ENCODINGS:

        # 解码失败时继续尝试下一个编码。
        try:

            # 成功解码后立即返回文本和编码标签。
            return bytes_raw.decode(str_encoding), str_encoding

        # UnicodeDecodeError 表示当前编码不适合该文件。
        except UnicodeDecodeError:

            # 继续尝试后续编码候选。
            continue

    # 最终兜底保留可读文本，避免极端文件打断批量报告。
    return bytes_raw.decode("latin-1", errors="replace"), "latin-1-replace"

# strip_line_comment 用于区分代码内容和尾随单行注释。
def strip_line_comment(str_line: str) -> str:
    """
    移除一行中字符串字面量外部的 Verilog 单行注释。

    :param str_line: 待分析的单行 Verilog 文本。
    :return: 去除行注释后的代码片段。
    """

    # 字符串状态避免把字符串内部的 // 误判成注释。
    bool_in_string = False  # 当前扫描位置是否位于双引号字符串内

    # index 从行首开始向后扫描。
    int_index = 0  # 当前字符偏移

    # 至少保留一个后续字符才能判断 //。
    while int_index < len(str_line) - 1:

        # 当前字符决定字符串状态和注释起点。
        str_char = str_line[int_index]  # 当前扫描字符

        # 双引号可能切换字符串状态。
        if str_char == '"':

            # 被反斜杠转义的双引号不结束字符串。
            bool_escaped = int_index > 0 and str_line[int_index - 1] == "\\"  # 当前引号是否被转义

            # 只有未转义引号才改变字符串状态。
            if not bool_escaped:

                # 切换字符串内部/外部状态。
                bool_in_string = not bool_in_string  # 更新后的字符串扫描状态

        # 字符串外部的 // 表示单行注释起点。
        if not bool_in_string and str_char == "/" and str_line[int_index + 1] == "/":

            # 返回注释之前的代码文本。
            return str_line[:int_index]

        # 普通字符推进一个位置。
        int_index += 1  # 下一处扫描偏移

    # 未发现行注释时返回原始行。
    return str_line

# is_code_line 判断一行在剥离注释后是否仍包含代码。
def is_code_line(str_line: str) -> bool:
    """
    判断 Verilog 源码行是否包含非注释代码。

    :param str_line: 待分析的单行 Verilog 文本。
    :return: True 表示该行剥离单行注释后仍有内容。
    """

    # 去除行注释和两端空白后再判定。
    str_stripped = strip_line_comment(str_line).strip()  # 去注释后的非空判断文本

    # bool 转换保持旧脚本的空行处理语义。
    return bool(str_stripped)

# port_name_from_decl_body 从端口声明体内提取最后一个标识符作为端口名。
def port_name_from_decl_body(str_body: str) -> str | None:
    """
    从端口声明正文中提取端口名。

    :param str_body: PORT_DECL_RE 捕获到的声明正文。
    :return: 识别到的端口名；无法识别时返回 None。
    """

    # 位宽范围不是端口名，先替换为空格。
    str_without_ranges = re.sub(r"\[[^\]]+\]", " ", str_body)  # 移除位宽范围后的声明体

    # 常见类型修饰符也不参与端口名候选。
    str_without_types = re.sub(  # 移除类型修饰符后的声明体
        r"\b(?:wire|reg|logic|signed|unsigned)\b",  # 端口声明类型修饰符集合
        " ",  # 用空格替换以保留标识符分隔边界
        str_without_ranges,  # 已移除位宽范围的端口声明体
    )

    # 标识符列表先初始化，再按方向关键字过滤。
    list_identifiers: list[str] = []  # 声明体内过滤方向关键字后的标识符

    # 正则扫描结果按声明文本顺序保留。
    for str_item in RE_IDENTIFIER.findall(str_without_types):

        # 方向关键字已经由正则前缀处理，不应进入端口名候选。
        if str_item not in SET_PORT_DIRECTIONS:

            # 追加真实端口名候选，最后一个候选会作为端口名。
            list_identifiers.append(str_item)

    # 没有标识符时无法可靠给出端口名。
    if not list_identifiers:

        # None 保持旧脚本对异常声明的跳过语义。
        return None

    # Verilog 声明中最后一个标识符通常是端口名。
    return list_identifiers[-1]

# relative_path_text 让报告路径在语料根内保持相对形式。
def relative_path_text(path_source: Path, path_root: Path) -> str:
    """
    生成报告中展示的源文件路径文本。

    :param path_source: 当前 Verilog 源文件路径。
    :param path_root: 用户传入的语料根路径。
    :return: 根内相对路径或原始路径文本。
    """

    # Python 3.9+ 的 is_relative_to 可避免异常式控制流。
    if path_source.is_relative_to(path_root):

        # 根内文件使用相对路径，报告在不同机器上更稳定。
        return str(path_source.relative_to(path_root))

    # 根外文件保留完整路径，避免丢失来源。
    return str(path_source)

# metric_dict 提取单个文件的所有统计字段。
def metric_dict(path_source: Path, path_root: Path) -> dict[str, Any]:
    """
    提取单个 Verilog 文件的风格统计字典。

    :param path_source: 待统计的 Verilog 源文件路径。
    :param path_root: 用户传入的语料根路径。
    :return: 与旧版 CorpusFileMetrics.to_dict 等价的指标字典。
    """

    # 文本读取同时记录实际编码，供报告排查语料来源差异。
    tuple_text_encoding = read_text(path_source)  # 源码文本和编码名称

    # 元组拆分让后续指标表达更清晰。
    str_text, str_encoding = tuple_text_encoding  # Verilog 源码文本和解码标签

    # splitlines 保持旧脚本不保留换行符的统计方式。
    list_lines = str_text.splitlines()  # 源文件按行拆分后的文本列表

    # 代码行统计只计算剥离行注释后仍非空的行。
    list_code_lines = [str_line for str_line in list_lines if is_code_line(str_line)]  # 非注释代码行列表

    # 注释行沿用旧脚本的简单 // 包含判断。
    list_comment_lines = [str_line for str_line in list_lines if "//" in str_line]  # 含有单行注释标记的行列表

    # 端口名称由所有端口声明行解析得到。
    list_ports = extract_ports(str_text)  # 当前文件识别出的端口名列表

    # 参数名称直接从 parameter 声明中提取。
    list_parameters = re.findall(r"\bparameter\s+([A-Za-z_][A-Za-z0-9_$]*)", str_text)  # parameter 名称列表

    # C_ 前缀参数列表先初始化，便于逐项解释筛选条件。
    list_c_parameters: list[str] = []  # C_ 前缀参数名称列表

    # C_ 命名比例需要从全部 parameter 名称中筛出命中项。
    for str_name in list_parameters:

        # C_ 前缀参数是 Erie 风格评分中的正向特征。
        if str_name.startswith("C_"):

            # 记录当前命中项，供后续参数比例计算。
            list_c_parameters.append(str_name)

    # 注释密度默认归零，避免空文件触发除零。
    float_comment_density = 0.0  # 注释行数相对代码行数的密度

    # 有代码行时才计算真实注释密度。
    if list_code_lines:

        # 注释行数沿用旧脚本的简单 // 行计数。
        float_comment_density = len(list_comment_lines) / len(list_code_lines)  # 有代码行时的注释密度

    # 相对路径文本保持报告跨环境可比较。
    str_report_path = relative_path_text(path_source, path_root)  # 报告中的文件路径文本

    # 英文头部标记来自 Erie 既有模板。
    str_english_header = "////////////////////////////////////English"  # 双语头部中的英文分隔线

    # 中文头部标记来自 Erie 既有模板。
    str_chinese_header = "///////////////////////////////////Chinese"  # 双语头部中的中文分隔线

    # 双语头部需要两个标记同时存在。
    int_has_bilingual_header = int(str_english_header in str_text and str_chinese_header in str_text)  # 双语头部命中标志

    # 指标字典字段名保持旧报告结构。
    return {
        "path": str_report_path,
        "encoding": str_encoding,
        "lines": len(list_lines),
        "code_lines": len(list_code_lines),
        "comment_lines": len(list_comment_lines),
        "comment_density": float_comment_density,
        "tab_indented_lines": sum(1 for str_line in list_lines if str_line.startswith("\t")),
        "space_indented_lines": sum(1 for str_line in list_lines if re.match(r" {2,}\S", str_line)),
        "block_comment_markers": str_text.count("/*") + str_text.count("*/"),
        "ports": len(list_ports),
        "prefixed_ports": sum(1 for str_name in list_ports if str_name.startswith(("i_", "o_", "io_"))),
        "parameters": len(list_parameters),
        "c_parameters": len(list_c_parameters),
        "region_banners": len(RE_REGION.findall(str_text)),
        "has_bilingual_header": int_has_bilingual_header,
        "placeholder_comment_hits": len(RE_PLACEHOLDER_COMMENT.findall(str_text)),
        "always_blocks": len(re.findall(r"\balways\s*@", str_text)),
        "assigns": len(re.findall(r"\bassign\b", str_text)),
    }

# extract_ports 封装端口声明扫描，避免主统计函数过长。
def extract_ports(str_text: str) -> list[str]:
    """
    从 Verilog 文本中提取端口名列表。

    :param str_text: 待扫描的 Verilog 源码文本。
    :return: 成功识别出的端口名列表。
    """

    # 端口列表按源码出现顺序保留。
    list_ports: list[str] = []  # 已识别端口名列表

    # 遍历所有方向声明匹配项。
    for match_port in RE_PORT_DECL.finditer(str_text):

        # 声明体中最后一个有效标识符视为端口名。
        str_name = port_name_from_decl_body(match_port.group("body"))  # 当前端口声明提取出的名称

        # 无法识别名称的声明不进入统计。
        if str_name:

            # 追加到端口列表供前缀评分使用。
            list_ports.append(str_name)

    # 返回当前文件全部端口名。
    return list_ports

# summarize_number 对同一指标在语料中的分布做基本统计。
def summarize_number(list_values: list[float]) -> dict[str, float]:
    """
    汇总一组数值的平均值、中位数和边界值。

    :param list_values: 待汇总的浮点数值列表。
    :return: 包含 avg、median、min、max 的统计字典。
    """

    # 空语料下所有数值汇总归零。
    if not list_values:

        # 保持旧 JSON 字段完整，便于下游读取。
        return {"avg": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}

    # 平均值使用简单算术平均。
    float_avg = float(sum(list_values) / len(list_values))  # 指标平均值

    # 中位数交给标准库处理奇偶长度。
    float_median = float(statistics.median(list_values))  # 指标中位数

    # 最小值展示该指标在语料中的下界。
    float_min = float(min(list_values))  # 指标最小值

    # 最大值展示该指标在语料中的上界。
    float_max = float(max(list_values))  # 指标最大值

    # 返回字段名保持旧报告结构。
    return {
        "avg": float_avg,
        "median": float_median,
        "min": float_min,
        "max": float_max,
    }

# style_score 计算低风格样本排序用的启发式分数。
def style_score(dict_item: dict[str, Any]) -> float:
    """
    根据单文件指标计算启发式风格分。

    :param dict_item: metric_dict 产出的单文件指标字典。
    :return: 分数越低表示越值得人工查看。
    """

    # 分数从零开始累加正负特征。
    float_score = 0.0  # 当前文件启发式风格分

    # 有端口时，端口前缀比例作为正向特征。
    if dict_item["ports"]:

        # i_/o_/io_ 前缀越完整，得分越高。
        float_score += dict_item["prefixed_ports"] / dict_item["ports"]  # 端口命名前缀贡献

    # 有参数时，C_ 参数比例作为正向特征。
    if dict_item["parameters"]:

        # 项目参数前缀越完整，正例语料特征越明显。
        float_score += dict_item["c_parameters"] / dict_item["parameters"]  # 参数命名前缀贡献

    # 注释密度按 1.0 封顶，避免超密注释支配分数。
    float_score += min(dict_item["comment_density"], 1.0)  # 注释密度贡献

    # 区域横幅按 4 个封顶，鼓励结构化但不过度奖励。
    float_score += min(dict_item["region_banners"] / 4.0, 1.0)  # 区域横幅贡献

    # 双语头部是模板完整性的正向信号。
    float_score += dict_item["has_bilingual_header"]  # 双语头部贡献

    # 块注释标记在该项目风格中通常是不鼓励的格式。
    float_score -= min(dict_item["block_comment_markers"], 3) * 0.25  # 块注释扣分

    # 占位注释命中直接提示模板未清理。
    float_score -= min(dict_item["placeholder_comment_hits"], 5) * 0.5  # 占位注释扣分

    # 空格缩进比例用于暴露和项目 tab 缩进偏好的偏差。
    float_score -= min(dict_item["space_indented_lines"] / max(dict_item["lines"], 1), 1.0)  # 空格缩进扣分

    # 返回排序使用的最终分数。
    return float_score

# corpus_report 汇总一个语料根的文件级指标和低分样本。
def corpus_report(path_root: Path, *, int_sample_size: int) -> dict[str, Any]:
    """
    生成单个语料根的汇总报告。

    :param path_root: 语料根目录或单个 Verilog 文件路径。
    :param int_sample_size: 需要保留的低风格分样本数量。
    :return: 包含文件数、统计摘要、编码分布和低分样本的字典。
    """

    # 文件级指标先全部收集，便于后续多字段汇总。
    list_metrics = [metric_dict(path_source, path_root) for path_source in iter_sources(path_root)]  # 单文件指标列表

    # 各字段统计摘要保持旧版 field_names 顺序。
    dict_summary: dict[str, dict[str, float]] = {}  # 每个数值指标的分布摘要

    # 逐字段展开统计，避免压缩推导式掩盖字段来源。
    for str_field in TUPLE_METRIC_FIELDS:

        # 当前字段值统一转为 float，供 summarize_number 计算。
        list_field_values = [float(dict_item[str_field]) for dict_item in list_metrics]  # 当前指标的文件级数值

        # 汇总结果写回与旧报告相同的字段名。
        dict_summary[str_field] = summarize_number(list_field_values)  # 当前指标的分布摘要

    # 编码分布用于发现混入的非 UTF-8 语料。
    dict_encodings: dict[str, int] = {}  # 编码名称到文件数量的映射

    # 遍历文件级指标累加编码计数。
    for dict_item in list_metrics:

        # 当前文件编码从 read_text 结果传入。
        str_encoding = dict_item["encoding"]  # 当前文件使用的解码标签

        # 计数采用 get 兼容首次出现的编码。
        dict_encodings[str_encoding] = dict_encodings.get(str_encoding, 0) + 1  # 更新后的编码文件数量

    # 低风格样本按启发式分数升序截取。
    list_low_samples = sorted(list_metrics, key=style_score)[:int_sample_size]  # 低风格分样本列表

    # 样本条目附带 style_score，方便人工复核排序原因。
    list_low_sample_reports: list[dict[str, Any]] = []  # 带风格分的低分样本报告

    # 样本报告逐个追加，便于说明 style_score 的来源。
    for dict_item in list_low_samples:

        # 当前样本保留原指标并追加排序使用的分数。
        dict_sample = dict_item | {"style_score": style_score(dict_item)}  # 单个低分样本报告条目

        # 追加后的列表直接进入 JSON 报告。
        list_low_sample_reports.append(dict_sample)

    # 返回字段名保持旧 JSON 顶层结构。
    return {
        "file_count": len(list_metrics),
        "summary": dict_summary,
        "encodings": dict_encodings,
        "sample_low_style": list_low_sample_reports,
    }

# main 串联 CLI 解析、报告构造和 JSON 写入。
def main() -> int:
    """
    执行语料风格统计 CLI。

    :param: 此函数不接收外部业务参数，直接读取命令行。
    :return: 0 表示报告文件已成功写入。
    """

    # argparse 负责校验必填参数和基础类型。
    namespace_args: argparse.Namespace = create_parser().parse_args()  # 语料分析命令行参数

    # meta 说明报告只用于规则调参，不替代正式 RTL 质量门。
    str_report_note = (
        "Corpus traits are advisory; generated delivery RTL is enforced by "
        "scripts/python/quality/quality_gate.py and "
        "scripts/python/quality/formatter_ast.py."
    )  # 报告用途说明文本

    # meta 字段承载版本号和使用边界。
    dict_meta = {
        "version": 1,  # 报告格式版本
        "note": str_report_note,  # 报告用途说明
    }  # 报告元信息

    # 正例语料根解析为绝对路径，避免相对路径受工作目录影响。
    path_ideal_root = namespace_args.ideal.resolve()  # ideal 语料绝对路径

    # bad 语料同样规范化路径，防止报告受调用目录变化影响。
    path_bad_root = namespace_args.bad.resolve()  # 压力样本语料的规范化根路径

    # 最终报告顶层结构保持旧版 meta/ideal/bad 三段，方便下游兼容。
    dict_report = {
        "meta": dict_meta,  # 版本和用途边界
        "ideal": corpus_report(path_ideal_root, int_sample_size=namespace_args.sample_size),  # 正例语料统计
        "bad": corpus_report(path_bad_root, int_sample_size=namespace_args.sample_size),  # 反例语料统计
    }  # 待写入 JSON 报告载荷

    # 输出目录不存在时自动创建，保持旧 CLI 便利性。
    namespace_args.out.parent.mkdir(parents=True, exist_ok=True)

    # JSON 文件使用 UTF-8 和缩进，便于人工查看。
    namespace_args.out.write_text(
        json.dumps(dict_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 终端只输出简短状态，完整结构化数据写入文件。
    print(f"> INFO: [Python] Wrote corpus metrics to {namespace_args.out}")

    # 成功写入报告后返回 0。
    return 0

# 脚本入口保持直接运行时的退出码传递。
if __name__ == "__main__":

    # main 的返回值作为进程退出码。
    raise SystemExit(main())
