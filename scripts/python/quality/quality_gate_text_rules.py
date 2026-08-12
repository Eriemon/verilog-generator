"""封装文本层 quality gate 规则与行级样式检查。"""

# 延迟类型注解求值，避免模块导入阶段过早解析复杂联合类型。
from __future__ import annotations

# 复制原 quality gate 的基础标准库依赖，保持无第三方包可运行。
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# 双语 header 的字面合同与固定路径统一从共享模块读取。
from ..header_contract import default_header_paths, header_layout_config as shared_header_layout_config

# formatter_ast 与 rulebook 仍是这些子模块依赖的唯一结构化入口。
from .formatter_backend.banners import display_width
from .formatter_ast import build_ast_report_for_path, iter_verilog_sources, read_verilog_source
from scripts.python.validation.rulebook import load_verilog_rulebook

# 文本规则只直接构造质量门诊断对象。
from .quality_gate_types import QualityIssue

# 文件头分隔符与版本常量统一从 common 模块显式导入。
from .quality_gate_common import (
    HEADER_CHINESE_SEPARATOR,
    HEADER_ENGLISH_SEPARATOR,
    HEADER_VERSION_PATTERN,
    REQUIRED_CHINESE_HEADER_FIELDS,
    REQUIRED_ENGLISH_HEADER_FIELDS,
)

# 原始文本层的运行时消息与中文检查常量继续复用 common 模块。
from .quality_gate_common import CJK_PATTERN, DISPLAY_STRING_PATTERN

# 行级缩进与控制结构 helper 继续复用 common 模块实现。
from .quality_gate_common import (
    _comment_severity,
    _control_line_requires_begin,
    _display_width_with_tabs,
    _has_space_before_tab,
    _is_code_line,
)

# 行尾注释与文本裁剪 helper 继续复用 common 模块实现。
from .quality_gate_common import (
    _line_comment,
    _line_comment_start,
    _strip_line_comment,
    _style_severity,
)

# 注释语义与区域锚点 helper 继续复用 common 模块实现。
from .quality_gate_common import (
    _comment_has_meaningful_chinese,
    _is_hollow_chinese_comment,
    _is_placeholder_comment,
    _is_pure_line_comment,
    _region_banner_anchor_column,
    _runtime_message_prefixes,
)

# 供 `_raw_text_rules` 复用的拆分 helper，专门处理检查文件头、缩进、块注释和行级占位注释。
def _raw_text_rules(
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查文件头、缩进、块注释和行级占位注释。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :return: 原始文本规则产生的质量门诊断列表。
    """

    # list_issues 汇总当前文件的原始文本诊断。
    list_issues: list[QualityIssue] = []  # 行级文本规则诊断

    # list_lines 用 splitlines 保持原有行号口径。
    list_lines = str_text.splitlines()  # 源文件逐行文本

    # str_style_severity 决定格式规则的严重级别。
    str_style_severity = _style_severity(strict)  # 格式规则严重级别

    # 首行 timescale 是 Erie 模板约束。
    if not str_text.startswith("`timescale 1ns / 1ps"):

        # 缺少 timescale 时定位到第一行。
        list_issues.append(
            QualityIssue(
                "VG001",
                str_style_severity,
                "File must start with `timescale 1ns / 1ps`.",
                str_rel_path,
                1,
                "file.preamble",
            )
        )

    # 文件头规则单独拆分，保持本函数分支复杂度可控。
    list_issues.extend(_header_rules(str_text, str_rel_path, strict=strict))

    # 块注释规则单独扫描，避免原始文本入口承担过多分支。
    list_issues.extend(_block_comment_rules(str_text, list_lines, str_rel_path, strict=strict))

    # 区域内行尾注释必须从区域横幅右侧 // 的显示列开始尽量对齐。
    list_issues.extend(_region_comment_anchor_rules(list_lines, str_rel_path, strict=strict))

    # `$display` 运行时消息的可读前缀合同在原始文本层统一执行。
    list_issues.extend(_runtime_message_rules(str_text, str_rel_path, strict=strict))

    # 行级缩进、控制结构和占位注释规则逐行检查。
    for int_line_no, str_line in enumerate(list_lines, start=1):

        # 当前行规则集中在辅助函数里，避免本函数继续膨胀。
        list_issues.extend(_line_text_rules(str_line, int_line_no, str_rel_path, str_style_severity, strict))

    # 文件末尾换行检查保证 formatter 输出可稳定拼接。
    list_issues.extend(_final_newline_rules(str_text, list_lines, str_rel_path, str_style_severity))

    # 全文件注释语言检查避免中文交付中残留纯英文说明。
    list_issues.extend(_file_comment_language_rules(list_lines, str_rel_path, strict, comment_language))

    # 返回原始文本规则诊断。
    return list_issues

# 供 `_block_comment_rules` 复用的拆分 helper，专门处理检查源码是否残留块注释标记。
def _block_comment_rules(
    str_text: str,
    list_lines: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查源码是否残留块注释标记。

    :param str_text: 当前 Verilog 源码文本。
    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释问题升级为 error。
    :return: 块注释规则产生的诊断列表。
    """

    # 没有块注释标记时直接返回，避免逐行扫描。
    if "/*" not in str_text and "*/" not in str_text:

        # 文件没有触发 VG002 的候选文本。
        return []

    # list_issues 保存每个块注释标记所在行的诊断。
    list_issues: list[QualityIssue] = []  # 块注释行级诊断

    # 逐行定位块注释开始或结束标记。
    for int_line_no, str_line in enumerate(list_lines, start=1):

        # 当前行没有块注释标记时跳过。
        if "/*" not in str_line and "*/" not in str_line:

            # 保持行号扫描继续。
            continue

        # Erie 生成 RTL 只允许 // 行注释。
        list_issues.append(
            QualityIssue(
                "VG002",
                _comment_severity(strict),
                "Block comments are forbidden; use // line comments only.",
                str_rel_path,
                int_line_no,
                "comments.line_only",
            )
        )

    # 返回全部块注释诊断。
    return list_issues

# 供 `_runtime_message_rules` 复用的拆分 helper，专门处理检查 `$display` 运行时消息是否满足统一前缀合同。
def _runtime_message_rules(
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 `$display` 运行时消息是否满足统一前缀合同。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把运行时消息问题升级为 error。
    :return: `$display` 运行时消息相关诊断列表。
    """

    # list_issues 保存运行时消息前缀不合规的 VG069 诊断。
    list_issues: list[QualityIssue] = []  # 运行时消息前缀诊断集合

    # 当前文件生效的人类可读前缀和机器 transcript 豁免前缀。
    tuple_human_prefixes, tuple_machine_prefixes = _runtime_message_prefixes()  # `$display` 前缀合同

    # 运行时消息扫描要先剥离真实 `//` 注释，避免注释示例里的旧 `$display` 误报 VG069。
    str_scan_text = "\n".join(_strip_line_comment(str_line) for str_line in str_text.split("\n"))  # 去行注释后的 `$display` 扫描文本。

    # 逐个扫描 `$display("...")` 的首个字符串字面量。
    for match_display in DISPLAY_STRING_PATTERN.finditer(str_scan_text):

        # str_message 保留 `$display` 首字符串参数的原始文本内容。
        str_message = match_display.group(1)  # 当前 `$display` 的首字符串字面量

        # 机器 transcript 行沿用既有协议前缀，不参与人类可读前缀检查。
        if any(str_message.startswith(str_prefix) for str_prefix in tuple_machine_prefixes):

            # 机器可读输出已命中豁免名单。
            continue

        # 已满足统一的人类可读前缀时不再报错。
        if any(str_message.startswith(str_prefix) for str_prefix in tuple_human_prefixes):

            # 当前 `$display` 文本已满足 Verilog 前缀合同。
            continue

        # 用匹配起点反推源码行号，便于把 VG069 精确落点到 display 行。
        int_line_no = str_scan_text.count("\n", 0, match_display.start()) + 1  # `$display` 所在源码行号

        # 记录统一运行时消息前缀诊断。
        list_issues.append(
            QualityIssue(
                "VG069",
                _style_severity(strict),
                "Human-readable Verilog `$display` text must start with ` > INFO: [Verilog]`, "
                "` > WARNING: [Verilog]`, or ` > ERR: [Verilog]`.",
                str_rel_path,
                int_line_no,
                "runtime_messages.display_prefix",
            )
        )

    # 返回全部运行时消息诊断。
    return list_issues

# 供 `_region_comment_anchor_rules` 复用的拆分 helper，专门处理检查区域横幅覆盖范围内的代码行尾注释起点。
def _region_comment_anchor_rules(
    list_lines: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查区域横幅覆盖范围内的代码行尾注释起点。

    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释对齐问题升级为 error。
    :return: 区域注释锚点对齐诊断列表。
    """

    # list_issues 汇总每个区域内行尾注释偏离锚点的 VG060 结果。
    list_issues: list[QualityIssue] = []  # 区域锚点偏移诊断集合

    # int_anchor_column 记录当前区域横幅最右侧 // 的显示列。
    int_anchor_column: int | None = None  # 当前区域注释锚点显示列

    # 逐行扫描区域横幅和区域内代码注释。
    for int_line_no, str_line in enumerate(list_lines, start=1):

        # 区域横幅会刷新后续代码行的注释锚点。
        int_banner_anchor = _region_banner_anchor_column(str_line)  # 当前行横幅锚点列

        # 命中横幅时只更新锚点，不检查横幅自身。
        if int_banner_anchor is not None:

            # 当前区域后续行尾注释以该列为起点。
            int_anchor_column = int_banner_anchor  # 当前区域横幅右侧 // 显示列

            # 横幅行属于纯注释行，继续扫描下一行。
            continue

        # 没进入任何区域时不执行 VG060。
        if int_anchor_column is None:

            # 文件头和 module 参数区可能没有区域横幅。
            continue

        # 纯注释行和空行不属于“代码行尾注释”。
        if not _is_code_line(str_line):

            # always/assign/reg 等上方一行的前置语义注释自然在这里豁免。
            continue

        # 只检查真实 // 行尾注释。
        int_comment_index = _line_comment_start(str_line)  # 当前行注释起始下标

        # 没有行尾注释时交给注释覆盖规则处理。
        if int_comment_index < 0:

            # VG060 只处理已有注释的起点。
            continue

        # code_width 使用去掉注释和尾随空白后的显示宽度。
        int_code_width = _display_width_with_tabs(str_line[:int_comment_index].rstrip())  # 注释前代码显示宽度

        # 注释实际起点是 // 之前文本的显示宽度。
        int_actual_column = _display_width_with_tabs(str_line[:int_comment_index])  # 当前 // 实际显示列

        # 代码未越过区域锚点时，行尾注释必须落在横幅右侧 // 列。
        if int_code_width < int_anchor_column:

            # int_expected_column 是横幅锚点定义的统一注释起点。
            int_expected_column = int_anchor_column  # 区域横幅锚点显示列

        # 代码已经越过锚点时，只允许在代码后留一个显示列。
        else:

            # int_expected_column 是当前代码结束后的第一个合法注释列。
            int_expected_column = int_code_width + 1  # 长代码后的最早注释列

        # 当前注释已经位于最早可注释列时通过。
        if int_actual_column == int_expected_column:

            # 行尾注释满足尽可能对齐。
            continue

        # 任意随意右移或过早出现都需要报告。
        list_issues.append(
            QualityIssue(
                "VG060",
                _style_severity(strict),
                f"Inline comment must start at display column {int_expected_column}, "
                f"aligned from region anchor column {int_anchor_column}; got {int_actual_column}.",
                str_rel_path,
                int_line_no,
                "comments.region_anchor",
            )
        )

    # 返回区域锚点对齐诊断。
    return list_issues

# 供 `_final_newline_rules` 复用的拆分 helper，专门处理检查文件是否以换行结束。
def _final_newline_rules(
    str_text: str,
    list_lines: list[str],
    str_rel_path: str,
    str_style_severity: str,
) -> list[QualityIssue]:
    """
    检查文件是否以换行结束。

    :param str_text: 当前 Verilog 源码文本。
    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: 格式规则严重级别。
    :return: 最终换行诊断列表。
    """

    # 空文件由其他规则报告，这里只处理非空文件末尾。
    if not str_text or str_text.endswith("\n"):

        # 最终换行规则无需诊断。
        return []

    # len(list_lines) 对无末尾换行文件仍能定位最后一行。
    return [
        QualityIssue(
            "VG005",
            str_style_severity,
            "File must end with exactly one final newline.",
            str_rel_path,
            len(list_lines),
            "format.final_newline",
        )
    ]

# 供 `_file_comment_language_rules` 复用的拆分 helper，专门处理检查整文件说明注释是否符合中文优先策略。
def _file_comment_language_rules(
    list_lines: list[str],
    str_rel_path: str,
    strict: bool,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查整文件说明注释是否符合中文优先策略。

    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :return: 注释语言诊断列表。
    """

    # 非中文交付模式不要求 CJK 注释覆盖。
    if comment_language != "zh":

        # 调用方可能显式选择英文注释。
        return []

    # str_comment_text 聚合所有行注释正文。
    str_comment_text = " ".join(_line_comment(str_line) for str_line in list_lines)  # 全文件行注释正文

    # 有注释且没有中文字符时登记文件级语言问题。
    if str_comment_text and not CJK_PATTERN.search(str_comment_text):

        # 文件级诊断不绑定具体源码行。
        return [
            QualityIssue(
                "VG040",
                _comment_severity(strict),
                "Generated explanatory comments should be Chinese-first.",
                str_rel_path,
                rule="comments.language",
            )
        ]

    # 中文注释要求已满足。
    return []

# 供 `_line_text_rules` 复用的拆分 helper，专门处理检查单行文本的缩进、控制结构和注释占位问题。
def _line_text_rules(
    str_line: str,
    int_line_no: int,
    str_rel_path: str,
    str_style_severity: str,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单行文本的缩进、控制结构和注释占位问题。

    :param str_line: 当前正在判断的单行文本。
    :param int_line_no: int_line_no 整数值，表示行号或计数。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: str_style_severity 文本值，供质量门规则匹配。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 单行文本规则产生的质量门诊断列表。
    """

    # str_indent_stripped 用于识别纯注释行。
    str_indent_stripped = str_line.lstrip()  # 去除左侧空白后的当前行

    # bool_pure_comment_line 表示该行没有 RTL 代码。
    bool_pure_comment_line = str_indent_stripped.startswith("//")  # 当前行是否为纯注释

    # str_code_line 去掉注释后用于控制结构 begin/end 检查。
    str_code_line = _strip_line_comment(str_line).strip()  # 当前行不含 // 注释的 RTL 代码

    # str_comment 保存当前行 // 后的注释正文。
    str_comment = _line_comment(str_line)  # 当前行行尾或整行注释

    # list_issues 保存当前行产生的诊断。
    list_issues: list[QualityIssue] = []  # 当前行文本规则诊断

    # 单行格式规则先报告空白和缩进问题。
    list_issues.extend(
        _line_format_issues(str_line, int_line_no, str_rel_path, str_style_severity, bool_pure_comment_line)
    )

    # 控制结构规则单独检查 if/case/for 等 begin/end。
    list_issues.extend(_line_control_issues(str_code_line, int_line_no, str_rel_path, str_style_severity))

    # 注释语义规则拦截模板占位文本。
    list_issues.extend(_placeholder_comment_issues(str_comment, int_line_no, str_rel_path, strict))

    # 返回当前行产生的诊断。
    return list_issues

# 供 `_line_format_issues` 复用的拆分 helper，专门处理检查单行尾随空白和 Tab 缩进约束。
def _line_format_issues(
    str_line: str,
    int_line_no: int,
    str_rel_path: str,
    str_style_severity: str,
    bool_pure_comment_line: bool,
) -> list[QualityIssue]:
    """
    检查单行尾随空白和 Tab 缩进约束。

    :param str_line: 当前正在判断的单行文本。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: 格式规则严重级别。
    :param bool_pure_comment_line: 当前行是否为纯注释。
    :return: 单行格式诊断列表。
    """

    # list_issues 保存当前行空白类问题。
    list_issues: list[QualityIssue] = []  # 单行格式诊断

    # 行尾空白会破坏格式稳定性。
    if str_line.rstrip() != str_line:

        # 尾随空白定位到当前行。
        list_issues.append(
            QualityIssue(
                "VG003",
                str_style_severity,
                "Trailing whitespace is not allowed.",
                str_rel_path,
                int_line_no,
                "format.trailing_space",
            )
        )

    # 非注释行不允许使用空格缩进。
    if not bool_pure_comment_line and re.match(r" {2,}\S", str_line):

        # Erie RTL 缩进约定使用 Tab。
        list_issues.append(
            QualityIssue(
                "VG004",
                str_style_severity,
                "RTL indentation must use Tab characters, not space indentation.",
                str_rel_path,
                int_line_no,
                "format.tab_indent",
            )
        )

    # Tab 前混入空格时登记缩进问题。
    if _has_space_before_tab(str_line, bool_pure_comment_line):

        # 混合缩进会导致 formatter diff 不稳定。
        list_issues.append(
            QualityIssue(
                "VG004",
                str_style_severity,
                "Do not mix spaces before Tab indentation.",
                str_rel_path,
                int_line_no,
                "format.tab_indent",
            )
        )

    # 返回当前行空白类诊断。
    return list_issues

# 供 `_line_control_issues` 复用的拆分 helper，专门处理检查单行控制语句是否显式使用 begin/end。
def _line_control_issues(
    str_code_line: str,
    int_line_no: int,
    str_rel_path: str,
    str_style_severity: str,
) -> list[QualityIssue]:
    """
    检查单行控制语句是否显式使用 begin/end。

    :param str_code_line: 去掉行注释后的 RTL 代码。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: 格式规则严重级别。
    :return: 控制语句诊断列表。
    """

    # 控制语句必须显式使用 begin/end。
    if not _control_line_requires_begin(str_code_line):

        # 当前行不是需要整改的控制语句。
        return []

    # 单行控制语句在 Erie 生成风格中不可接受。
    return [
        QualityIssue(
            "VG025",
            str_style_severity,
            "Control statements must use explicit begin/end blocks.",
            str_rel_path,
            int_line_no,
            "control.begin_end",
        )
    ]

# 供 `_placeholder_comment_issues` 复用的拆分 helper，专门处理检查单行注释是否仍是模板占位文字。
def _placeholder_comment_issues(
    str_comment: str,
    int_line_no: int,
    str_rel_path: str,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单行注释是否仍是模板占位文字。

    :param str_comment: 行注释正文。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释问题升级为 error。
    :return: 占位注释诊断列表。
    """

    # 没有占位注释时直接通过。
    if not str_comment or not _is_placeholder_comment(str_comment):

        # 当前行注释不是模板噪音。
        return []

    # 占位注释使用 comment severity，非 strict 时可降级。
    return [
        QualityIssue(
            "VG041",
            _comment_severity(strict),
            "Comments must describe real RTL intent, not template or placeholder text.",
            str_rel_path,
            int_line_no,
            "comments.semantic",
        )
    ]

# 供 `_header_rules` 复用的拆分 helper，专门处理检查标准双语文件头和 formatter 兼容字段拼写。
def _header_rules(str_text: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查标准双语文件头和 formatter 兼容字段拼写。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 文件头规则诊断列表。
    """

    # list_issues 保存文件头诊断。
    list_issues: list[QualityIssue] = []  # 文件头规则诊断

    # str_severity 使用格式规则 severity。
    str_severity = _style_severity(strict)  # 文件头规则严重级别

    # str_pre_module 是 module 声明之前的文件头区域。
    str_pre_module = _pre_module_region(str_text)  # module 前文本区域

    # 双语分隔标记必须同时存在。
    if (
        HEADER_ENGLISH_SEPARATOR not in str_pre_module
        or HEADER_CHINESE_SEPARATOR not in str_pre_module
    ):

        # 双语头缺失时定位到第一行。
        list_issues.append(
            QualityIssue(
                "VG007",
                str_severity,
                "Standard bilingual header with English/Chinese sections is required.",
                str_rel_path,
                1,
                "header.bilingual",
            )
        )

    # list_missing_english 记录缺失的英文头字段。
    list_missing_english = [  # 双语文件头中缺失的英文模板字段
        str_field  # 未在英文文件头区域出现的字段名
        for str_field in REQUIRED_ENGLISH_HEADER_FIELDS  # 遍历英文必填字段
        if not re.search(rf"//\s*{re.escape(str_field)}\s*:", str_pre_module)  # 文件头未命中该字段
    ]

    # list_missing_chinese 记录中文文件头模板字段缺口。
    list_missing_chinese = [  # 中文文件头区域仍缺失的必填字段
        str_field  # 未在中文文件头区域出现的中文字段名
        for str_field in REQUIRED_CHINESE_HEADER_FIELDS  # 遍历中文必填字段
        if not re.search(rf"//\s*{re.escape(str_field)}\s*:", str_pre_module)  # 中文头未命中该字段
    ]

    # 英文字段缺失时聚合成一条诊断。
    if list_missing_english:

        # str_message 保持英文头字段诊断的旧文案前缀。
        str_message = (
            "English header is missing required field(s): "  # 英文头缺失字段诊断前缀
            + ", ".join(list_missing_english)  # 附加英文缺失字段名
        )  # 英文头缺失字段诊断文本

        # 追加英文头字段诊断。
        list_issues.append(
            QualityIssue("VG007", str_severity, str_message, str_rel_path, 1, "header.english_fields")
        )

    # 中文头字段缺失时聚合成一条诊断。
    if list_missing_chinese:

        # str_message 保持中文头字段诊断的旧文案前缀和字段列表。
        str_message = (
            "Chinese header is missing required field(s): "  # 中文头缺失字段诊断前缀
            + ", ".join(list_missing_chinese)  # 附加中文缺失字段名
        )  # 中文头缺失字段诊断文本

        # 追加中文头字段诊断。
        list_issues.append(
            QualityIssue("VG007", str_severity, str_message, str_rel_path, 1, "header.chinese_fields")
        )

    # References/Dependencies 只能命中两种合法 header 形态，并且不允许 tab 与旧拼写。
    list_issues.extend(_header_reference_dependency_issues(str_pre_module, str_rel_path, str_severity))

    # Description/Simulations 字段必须回到固定路径合同，不能保留自由摘要或 tb 简写。
    list_issues.extend(_header_document_path_issues(str_text, str_pre_module, str_rel_path, str_severity))

    # header 结束后只允许保留一个空行再进入 module，不能再泄露额外摘要注释。
    list_issues.extend(_header_postamble_issues(str_text, str_pre_module, str_rel_path, str_severity))

    # 当前版本和历史记录必须具备真实可追溯内容。
    list_issues.extend(_header_version_history_issues(str_pre_module, str_rel_path, str_severity))

    # 返回文件头诊断。
    return list_issues

# 供 `_header_reference_dependency_issues` 复用的拆分 helper，专门处理检查 header 的 References/Dependencies 是否满足 none_mode 或 table_mode。
def _header_reference_dependency_issues(
    str_pre_module: str,
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 header 的 References/Dependencies 是否满足 none_mode 或 table_mode。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: References/Dependencies 相关诊断列表。
    """

    # list_issues 保存 References/Dependencies 版式诊断。
    list_issues = _header_reference_dependency_legacy_layout_issues(  # header 参考资料与依赖文件版式诊断
        str_pre_module,  # module 之前的 header 源码文本
        str_rel_path,  # 当前文件的相对报告路径
        str_severity,  # 当前 header 规则对应的严重级别
    )

    # tuple_modes_and_issues 汇总英中两段解析出的合法模式与局部诊断。
    tuple_modes_and_issues = _header_reference_dependency_modes_and_issues(  # header 参考资料与依赖文件的模式聚合结果
        str_pre_module,  # 同一份 header 文本用于双语模式聚合
        str_rel_path,  # 模式聚合诊断落点路径
        str_severity,  # 模式聚合阶段沿用的严重级别
    )

    # list_modes 记录英中两段各自解析出的合法形态，用于检查全局是否一致。
    list_modes = tuple_modes_and_issues[0]  # 英中头部的 reference/dependency 总模板形态

    # 先合并每个语言段内部产生的所有 VG068 诊断。
    list_issues.extend(tuple_modes_and_issues[1])

    # 英文和中文若分别命中了不同合法模式，也属于第三种非法总模板。
    if len(set(list_modes)) > 1:

        # 双语文件头必须整体收敛到同一种总模板。
        list_issues.append(
            QualityIssue(
                "VG068",
                str_severity,
                "English and Chinese References/Dependencies sections must use "
                "the same global mode (`none_mode` or `table_mode`).",
                str_rel_path,
                1,
                "header.reference_dependency_mode",
            )
        )

    # 返回 References/Dependencies 版式诊断。
    return list_issues

# 供 `_header_reference_dependency_legacy_layout_issues` 复用的拆分 helper，专门处理检查 References/Dependencies 区域是否仍残留历史 tab 版式或旧拼写。
def _header_reference_dependency_legacy_layout_issues(
    str_pre_module: str,
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 References/Dependencies 区域是否仍残留历史 tab 版式或旧拼写。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: 与历史残留相关的 VG068 诊断列表。
    """

    # list_issues 保存 tab 与旧拼写等历史残留诊断。
    list_issues: list[QualityIssue] = []  # header 历史版式残留诊断

    # 旧拼写必须走新规则码 VG068，而不是继续混在 VG007 中。
    str_legacy_references_field = "Reference" + "s:"  # 拆分保留旧拼写兼容检测

    # 只要 header 区出现 tab，就说明仍在依赖历史制表布局。
    if "\t" in str_pre_module:

        # 新合同要求 header 只能使用空格前缀和固定列头模板。
        list_issues.append(
            QualityIssue(
                "VG068",
                str_severity,
                "Header must use exact space-based layout; tabs are not allowed "
                "in References/Dependencies sections.",
                str_rel_path,
                1,
                "header.reference_dependency_spacing",
            )
        )

    # References 的旧拼写必须被统一阻断。
    if str_legacy_references_field in str_pre_module:

        # 英文标签只能是 Referrences，不能回落到历史标准拼写。
        list_issues.append(
            QualityIssue(
                "VG068",
                str_severity,
                "Header must use exact `Referrences` spelling in the English section.",
                str_rel_path,
                1,
                "header.references_spelling",
            )
        )

    # 返回历史残留诊断。
    return list_issues

# 供 `_header_reference_dependency_layouts` 复用的拆分 helper，专门处理References/Dependencies 规则使用的英中双语精确版式定义。
def _header_reference_dependency_layouts() -> dict[str, dict[str, str]]:
    """
    返回 References/Dependencies 规则使用的英中双语精确版式定义。

    参数:
        本函数没有业务参数，固定返回当前 header 规则的双语布局常量。
    :return: 以语言名为键的 header 布局定义字典。
    """

    # 读取共享 header 合同布局，后续只抽取 References/Dependencies 相关字段。
    dict_layout = shared_header_layout_config()  # 共享 header 合同布局副本

    # 返回英中双语的 References/Dependencies 精确版式映射。
    return {
        "english": {
            "separator_marker": HEADER_ENGLISH_SEPARATOR,
            "references_line_none": str(dict_layout["english"]["references_line_none"]),
            "references_line_table": str(dict_layout["english"]["references_line_table"]),
            "references_heading": f"// {dict_layout['english']['references_table_header']}",
            "dependencies_line_none": str(dict_layout["english"]["dependencies_line_none"]),
            "dependencies_line_table": str(dict_layout["english"]["dependencies_line_table"]),
            "dependencies_heading": f"// {dict_layout['english']['dependencies_table_header']}",
            "version_prefix": "// Version:",
        },
        "chinese": {
            "separator_marker": HEADER_CHINESE_SEPARATOR,
            "references_line_none": str(dict_layout["chinese"]["references_line_none"]),
            "references_line_table": str(dict_layout["chinese"]["references_line_table"]),
            "references_heading": f"// {dict_layout['chinese']['references_table_header']}",
            "dependencies_line_none": str(dict_layout["chinese"]["dependencies_line_none"]),
            "dependencies_line_table": str(dict_layout["chinese"]["dependencies_line_table"]),
            "dependencies_heading": f"// {dict_layout['chinese']['dependencies_table_header']}",
            "version_prefix": "// 当前版本:",
        },
    }

# 供 `_header_reference_dependency_modes_and_issues` 复用的拆分 helper，专门处理汇总英中 References/Dependencies 区域解析出的合法模式与局部诊断。
def _header_reference_dependency_modes_and_issues(
    str_pre_module: str,
    str_rel_path: str,
    str_severity: str,
) -> tuple[list[str], list[QualityIssue]]:
    """
    汇总英中 References/Dependencies 区域解析出的合法模式与局部诊断。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: 已识别的合法模式列表，以及全部语言段局部诊断。
    """

    # list_modes 记录各语言段成功识别出的 none/table 合法模式。
    list_modes: list[str] = []  # 英中头部 reference/dependency 总模板形态

    # list_issues 聚合同一 header 下各语言段产生的局部诊断。
    list_issues: list[QualityIssue] = []  # 各语言段局部 VG068 诊断

    # 英中两段分别执行精确版式检查。
    for str_language, dict_layout in _header_reference_dependency_layouts().items():

        # 当前语言段解析出的模式与局部诊断分开承接，避免主流程再解 tuple 下标。
        tuple_language_mode = _header_reference_dependency_mode_for_language(  # 当前语言段的模式与局部诊断
            str_pre_module,  # 当前被聚合的 header 原文
            dict_layout,  # 当前语言布局常量
            str_language,  # 当前语言标签
            str_rel_path,  # 当前语言诊断落点路径
            str_severity,  # 当前语言诊断严重级别
        )

        # str_mode 只有在当前语言段成功识别出合法模式时才非空。
        str_mode = tuple_language_mode[0]  # 当前语言段识别出的合法模式

        # list_language_issues 汇总当前语言段内部的局部 VG068 诊断。
        list_language_issues = tuple_language_mode[1]  # 当前语言段局部诊断列表

        # 先合并该语言段内的所有 VG068 诊断。
        list_issues.extend(list_language_issues)

        # 只有成功识别合法形态时，才把它纳入英中一致性比较。
        if str_mode:

            # 当前语言段已识别到合法 none/table 模板。
            list_modes.append(str_mode)

    # 返回全部合法模式与局部诊断。
    return list_modes, list_issues

# 供 `_header_reference_dependency_mode_for_language` 复用的拆分 helper，专门处理检查某一语言段的 References/Dependencies 是否满足精确合法形态。
def _header_reference_dependency_mode_for_language(
    str_pre_module: str,
    dict_layout: dict[str, str],
    str_language: str,
    str_rel_path: str,
    str_severity: str,
) -> tuple[str | None, list[QualityIssue]]:
    """
    检查某一语言段的 References/Dependencies 是否满足精确合法形态。

    :param str_pre_module: module 声明之前的源码文本。
    :param dict_layout: 当前语言段的精确版式定义。
    :param str_language: `english` 或 `chinese`。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: 识别出的合法模式，以及对应诊断列表。
    """

    # list_issues 保存当前语言段的 VG068 诊断。
    list_issues: list[QualityIssue] = []  # 单语言 header 参考资料与依赖文件诊断

    # list_section_lines 是当前语言分隔横幅之下、下一个横幅之前的源码行。
    list_section_lines = _header_language_section_lines(str_pre_module, dict_layout["separator_marker"])  # 当前语言段源码行

    # 未命中该语言横幅时由 VG007 报 bilingual header，不重复报 VG068。
    if not list_section_lines:

        # 缺少整段头部时跳过局部 References/Dependencies 形态诊断。
        return None, list_issues

    # tuple_indexes 保存 References/Dependencies/Version 三个字段的稳定起始位置。
    tuple_indexes = _header_reference_dependency_indexes(  # 当前语言头段字段起始索引集合
        list_section_lines,  # 当前语言横幅下的源码行列表
        dict_layout,  # 当前语言字段布局定义
    )

    # 缺字段由 VG007 处理，这里只检查已经找到的 block 形态。
    if tuple_indexes is None:

        # 缺局部字段时不额外伪造 VG068。
        return None, list_issues

    # 三个字段索引用于切分 References 与 Dependencies 两个局部 block。
    int_reference_index, int_dependency_index, int_version_index = tuple_indexes  # 当前语言字段起始索引

    # 字段顺序若颠倒，说明当前头段已经不是合法模板。
    if not (int_reference_index < int_dependency_index < int_version_index):

        # References/Dependencies/Version 的先后顺序属于固定结构合同。
        list_issues.append(
            _header_reference_dependency_order_issue(
                str_language,
                str_rel_path,
                str_severity,
            )
        )

        # 字段顺序已错乱，后续 block 形态不再可信。
        return None, list_issues

    # 当前语言段的 References/Dependencies 形态与局部诊断交由 block helper 统一裁决。
    tuple_mode_scan = _header_reference_dependency_mode_issues_for_sections(  # 当前语言段 block 形态诊断结果
        list_section_lines,  # 当前语言段的原始源码片段
        dict_layout,  # 当前语言对应的字段布局
        str_language,  # 交给 helper 区分英中语段
        str_rel_path,  # block 诊断落点路径
        str_severity,  # block 形态问题严重级别
        tuple_indexes,  # 三个关键字段的起始索引
    )

    # 这里把 tuple 第一项提升成上游直接消费的总模板名。
    str_mode = tuple_mode_scan[0]  # tuple 第一项对应 block 汇总后的模板名

    # list_mode_issues 只保存两个局部 block 产生的补充诊断。
    list_mode_issues = tuple_mode_scan[1]  # tuple 第二项承接局部 block 诊断列表

    # 先合并当前语言段 block 形态诊断，再决定是否返回合法模式。
    list_issues.extend(list_mode_issues)

    # 返回该语言段成功识别到的合法总模式。
    return str_mode, list_issues

# 供 `_header_reference_dependency_indexes` 复用的拆分 helper，专门处理单语言 header 段内 References/Dependencies/Version 字段的起始索引。
def _header_reference_dependency_indexes(
    list_section_lines: list[str],
    dict_layout: dict[str, str],
) -> tuple[int, int, int] | None:
    """
    返回单语言 header 段内 References/Dependencies/Version 字段的起始索引。

    :param list_section_lines: 当前语言段的源码行列表。
    :param dict_layout: 当前语言段的精确版式定义。
    :return: 三个字段的起始索引；任一缺失时返回 None。
    """

    # int_reference_index 用于切分参考资料局部 block。
    int_reference_index = _find_exact_line_index(  # 当前语言参考资料字段起始行索引
        list_section_lines,  # 当前语言段的源码行列表
        dict_layout["references_line_none"],  # 参考资料 none_mode 整行模板
        dict_layout["references_line_table"],  # 参考资料 table 形态字段起始整行
    )

    # int_dependency_index 锚定依赖文件字段起始位置，供后续切分局部 block。
    int_dependency_index = _find_exact_line_index(  # 当前语言依赖文件字段起始行索引
        list_section_lines,  # 依赖字段查找仍复用同一段源码行
        dict_layout["dependencies_line_none"],  # 依赖 none_mode 整行模板
        dict_layout["dependencies_line_table"],  # 依赖 table 形态字段起始整行
    )

    # int_version_index 标记版本字段位置，用来截断依赖 block 的尾部范围。
    int_version_index = _find_prefix_line_index(  # 当前语言版本字段起始行索引
        list_section_lines,  # 版本字段定位同样基于当前语言段源码
        dict_layout["version_prefix"],  # 版本字段稳定前缀
    )

    # 缺字段由 VG007 处理，这里不额外伪造 VG068。
    if int_reference_index is None or int_dependency_index is None or int_version_index is None:

        # 任一字段缺失时无法继续做 block 形态检查。
        return None

    # 返回三个字段的稳定起始位置。
    return int_reference_index, int_dependency_index, int_version_index

# 供 `_header_reference_dependency_order_issue` 复用的拆分 helper，专门处理构造单语言 header 中字段顺序错误的 VG068 诊断。
def _header_reference_dependency_order_issue(
    str_language: str,
    str_rel_path: str,
    str_severity: str,
) -> QualityIssue:
    """
    构造单语言 header 中字段顺序错误的 VG068 诊断。

    :param str_language: `english` 或 `chinese`。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: 字段顺序错误对应的质量问题对象。
    """

    # 返回 References/Dependencies/Version 顺序错误的固定诊断。
    return QualityIssue(
        "VG068",
        str_severity,
        f"{str_language.capitalize()} header must keep `Referrences`, "
        "`Dependencies`, and version fields in the canonical order.",
        str_rel_path,
        1,
        "header.reference_dependency_order",
    )

# 供 `_header_reference_dependency_shape_issue` 复用的拆分 helper，专门处理构造单语言 References/Dependencies 局部 block 形态非法时的 VG068 诊断。
def _header_reference_dependency_shape_issue(
    str_language: str,
    str_rel_path: str,
    str_severity: str,
) -> QualityIssue:
    """
    构造单语言 References/Dependencies 局部 block 形态非法时的 VG068 诊断。

    :param str_language: `english` 或 `chinese`。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: block 既非 none_mode 也非 table_mode 时的质量问题对象。
    """

    # 返回局部 block 非法时使用的固定诊断。
    return QualityIssue(
        "VG068",
        str_severity,
        f"{str_language.capitalize()} References/Dependencies section "
        "must match exact `none_mode` or `table_mode` layout.",
        str_rel_path,
        1,
        "header.reference_dependency_shape",
    )

# 供 `_header_reference_dependency_mixed_mode_issue` 复用的拆分 helper，专门处理构造单语言 References/Dependencies 混用 None 与 table 形态时的 VG068 诊断。
def _header_reference_dependency_mixed_mode_issue(
    str_language: str,
    str_rel_path: str,
    str_severity: str,
) -> QualityIssue:
    """
    构造单语言 References/Dependencies 混用 None 与 table 形态时的 VG068 诊断。

    :param str_language: `english` 或 `chinese`。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: None/table 混用时的质量问题对象。
    """

    # 返回 References 与 Dependencies 模板不一致时的固定诊断。
    return QualityIssue(
        "VG068",
        str_severity,
        f"{str_language.capitalize()} References/Dependencies section "
        "cannot mix `None` and table layouts.",
        str_rel_path,
        1,
        "header.reference_dependency_mixed_mode",
    )

# 供 `_header_reference_dependency_table_rows_issue` 复用的拆分 helper，专门处理构造单语言 table_mode 缺少真实数据行时的 VG068 诊断。
def _header_reference_dependency_table_rows_issue(
    str_language: str,
    str_rel_path: str,
    str_severity: str,
) -> QualityIssue:
    """
    构造单语言 table_mode 缺少真实数据行时的 VG068 诊断。

    :param str_language: `english` 或 `chinese`。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: table_mode 缺真实数据行时的质量问题对象。
    """

    # 返回 table_mode 只有表头、没有真实数据行时的固定诊断。
    return QualityIssue(
        "VG068",
        str_severity,
        f"{str_language.capitalize()} table_mode header must contain "
        "at least one real reference or dependency data row.",
        str_rel_path,
        1,
        "header.reference_dependency_table_rows",
    )

# 供 `_header_reference_dependency_mode_issues_for_sections` 复用的拆分 helper，专门处理检查单语言 header 中 References/Dependencies 两个局部 block 的合法形态。
def _header_reference_dependency_mode_issues_for_sections(
    list_section_lines: list[str],
    dict_layout: dict[str, str],
    str_language: str,
    str_rel_path: str,
    str_severity: str,
    tuple_indexes: tuple[int, int, int],
) -> tuple[str | None, list[QualityIssue]]:
    """
    检查单语言 header 中 References/Dependencies 两个局部 block 的合法形态。

    :param list_section_lines: 当前语言段的源码行列表。
    :param dict_layout: 当前语言段的精确版式定义。
    :param str_language: `english` 或 `chinese`。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :param tuple_indexes: 参考资料、依赖文件、版本字段的起始索引集合。
    :return: 合法模式，以及当前语言段 block 形态诊断列表。
    """

    # int_reference_index 等索引用于切分 References 与 Dependencies 两个 block。
    int_reference_index, int_dependency_index, int_version_index = tuple_indexes  # 当前语言段字段起始索引

    # list_reference_block 是参考资料 section 的完整源码行。
    list_reference_block = _trim_header_section_block(  # 当前语言参考资料块
        list_section_lines[int_reference_index:int_dependency_index]  # 参考资料字段到依赖字段之前的源码切片
    )

    # list_dependency_block 是依赖文件 section 的完整源码行。
    list_dependency_block = _trim_header_section_block(  # 当前语言依赖文件块
        list_section_lines[int_dependency_index:int_version_index]  # 依赖字段到版本字段之前的源码切片
    )

    # 先归类参考资料 block 的模板与真实数据行数量。
    tuple_reference_mode = _classify_header_section_block(  # 当前语言参考资料块分类结果
        list_reference_block,  # 当前语言参考资料块源码行
        dict_layout["references_line_none"],  # 参考资料 none_mode 模板
        dict_layout["references_line_table"],  # 参考资料 table_mode 字段行
        dict_layout["references_heading"],  # 参考资料 table_mode 列头行
    )

    # str_reference_mode 是参考资料 block 的合法模式。
    str_reference_mode = tuple_reference_mode[0]  # 当前语言参考资料块合法模式

    # int_reference_rows 是参考资料 block 的真实数据行数量。
    int_reference_rows = tuple_reference_mode[1]  # 当前语言参考资料块真实数据行数

    # 再归类依赖文件 block 的模板与真实数据行数量。
    tuple_dependency_mode = _classify_header_section_block(  # 当前语言依赖文件块分类结果
        list_dependency_block,  # 当前语言依赖文件块源码行
        dict_layout["dependencies_line_none"],  # 依赖 none_mode 模板
        dict_layout["dependencies_line_table"],  # 依赖 table_mode 字段行
        dict_layout["dependencies_heading"],  # 依赖 table_mode 列头行
    )

    # str_dependency_mode 是依赖文件 block 的合法模式。
    str_dependency_mode = tuple_dependency_mode[0]  # 当前语言依赖文件块合法模式

    # int_dependency_rows 是依赖文件 block 的真实数据行数量。
    int_dependency_rows = tuple_dependency_mode[1]  # 当前语言依赖文件块真实数据行数

    # 任一 block 不满足精确合法形态时直接报告 VG068。
    if str_reference_mode is None or str_dependency_mode is None:

        # 只要有一段既不是 none_mode 也不是 table_mode，该语言头段即视为非法。
        return None, [_header_reference_dependency_shape_issue(str_language, str_rel_path, str_severity)]

    # References 和 Dependencies 必须同时属于同一种总模板。
    if str_reference_mode != str_dependency_mode:

        # None + table 混用会形成第三种非法形态。
        return None, [_header_reference_dependency_mixed_mode_issue(str_language, str_rel_path, str_severity)]

    # table_mode 至少一段必须包含真实数据行；另一段允许只有表头零数据行。
    if str_reference_mode == "table_mode" and (int_reference_rows + int_dependency_rows) == 0:

        # 只有表头而完全没有真实数据行，不满足新合同里的 table_mode。
        return None, [_header_reference_dependency_table_rows_issue(str_language, str_rel_path, str_severity)]

    # 将两个局部 block 收敛后的合法总模板回传给上游语言段检查。
    return str_reference_mode, []

# 供 `_header_language_section_lines` 复用的拆分 helper，专门处理截取某个语言横幅下、下一个语言横幅前的源码行。
def _header_language_section_lines(str_pre_module: str, str_separator_marker: str) -> list[str]:
    """
    截取某个语言横幅下、下一个语言横幅前的源码行。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_separator_marker: 当前语言横幅的识别标记。
    :return: 当前语言段的源码行列表。
    """

    # list_lines 保留 header 源码的原始物理行顺序。
    list_lines = str_pre_module.splitlines()  # header 物理行列表

    # int_start_index 记录目标语言横幅所在行号。
    int_start_index: int | None = None  # 当前语言横幅行索引

    # 逐行查找当前语言横幅。
    for int_index, str_line in enumerate(list_lines):

        # 横幅行命中后，从它下一行开始截取当前语言段正文。
        if str_separator_marker in str_line:

            # 当前语言段的正文从横幅之后开始。
            int_start_index = int_index + 1  # 当前语言段正文起始索引

            # 命中目标横幅后即可停止继续向下查找。
            break

    # 没有该语言横幅时返回空列表，由上游决定是否跳过。
    if int_start_index is None:

        # 当前 header 不含该语言段。
        return []

    # list_section_lines 收集当前语言横幅之下、下一横幅之前的所有行。
    list_section_lines: list[str] = []  # 当前语言段正文源码行

    # 从当前横幅之后继续扫描，遇到另一个语言横幅即停止。
    for str_line in list_lines[int_start_index:]:

        # 下一个语言横幅意味着当前语言段已经结束。
        if HEADER_ENGLISH_SEPARATOR in str_line or HEADER_CHINESE_SEPARATOR in str_line:

            # 命中后继横幅时停止收集当前语言段。
            break

        # 当前行仍属于正在解析的语言段。
        list_section_lines.append(str_line)

    # 返回当前语言段源码行列表。
    return list_section_lines

# 供 `_find_exact_line_index` 复用的拆分 helper，专门处理在源码行列表中查找与候选整行完全相等的首个索引。
def _find_exact_line_index(list_lines: list[str], *tuple_candidates: str) -> int | None:
    """
    在源码行列表中查找与候选整行完全相等的首个索引。

    :param list_lines: 待查找的源码行列表。
    :param tuple_candidates: 允许命中的多个整行文本。
    :return: 命中时返回首个索引，否则返回 None。
    """

    # 逐行扫描，直到命中某个候选整行。
    for int_index, str_line in enumerate(list_lines):

        # 只有整行完全匹配时才算命中精确版式。
        if any(str_line == str_candidate for str_candidate in tuple_candidates):

            # 返回首个命中的整行索引。
            return int_index

    # 没有找到任何候选整行。
    return None

# 供 `_find_prefix_line_index` 复用的拆分 helper，专门处理查找首个以指定前缀开头的源码行索引。
def _find_prefix_line_index(list_lines: list[str], str_prefix: str) -> int | None:
    """
    查找首个以指定前缀开头的源码行索引。

    :param list_lines: 待查找的源码行列表。
    :param str_prefix: 目标前缀文本。
    :return: 命中时返回索引，否则返回 None。
    """

    # 逐行查找版本字段等稳定起始前缀。
    for int_index, str_line in enumerate(list_lines):

        # 只要前缀匹配，即可视为对应字段起点。
        if str_line.startswith(str_prefix):

            # 返回首个前缀命中位置。
            return int_index

    # 没有找到目标前缀。
    return None

# 供 `_trim_header_section_block` 复用的拆分 helper，专门处理去掉 References/Dependencies block 尾部的分隔空行。
def _trim_header_section_block(list_block_lines: list[str]) -> list[str]:
    """
    去掉 References/Dependencies block 尾部的分隔空行。

    :param list_block_lines: 未裁剪的 section block 源码行。
    :return: 去掉尾部分隔行后的 block 行列表。
    """

    # list_trimmed 复制一份，避免原地修改切片结果影响调用方。
    list_trimmed = list(list_block_lines)  # 待裁剪的 section block 行列表

    # 尾部允许出现 `//` 分隔行或真正空白行，需要统一裁掉。
    while list_trimmed and list_trimmed[-1].strip() in {"", "//"}:

        # 尾部空白分隔不属于 block 本体。
        list_trimmed.pop()

    # 返回裁剪后的稳定 block 行列表。
    return list_trimmed

# 供 `_classify_header_section_block` 复用的拆分 helper，专门处理归类单个 References/Dependencies block 的形态，并返回真实数据行数量。
def _classify_header_section_block(
    list_block_lines: list[str],
    str_none_line: str,
    str_table_line: str,
    str_heading_line: str,
) -> tuple[str | None, int]:
    """
    归类单个 References/Dependencies block 的形态，并返回真实数据行数量。

    :param list_block_lines: 当前 section block 的源码行列表。
    :param str_none_line: 当前 block 在 none_mode 下的唯一合法整行。
    :param str_table_line: 当前 block 在 table_mode 下的字段起始整行。
    :param str_heading_line: 当前 block 在 table_mode 下的固定列头整行。
    :return: block 模式，以及真实数据行数量；非法形态返回 `(None, 0)`。
    """

    # none_mode 只能是单行字段加 None。
    if list_block_lines == [str_none_line]:

        # 单行 None 形态没有真实表格数据行。
        return "none_mode", 0

    # table_mode 至少要有字段行和固定列头两行。
    if len(list_block_lines) >= 2 and list_block_lines[0] == str_table_line and list_block_lines[1] == str_heading_line:

        # list_data_rows 只统计列头之后的真实数据行。
        list_data_rows = list_block_lines[2:]  # table_mode 列头之后的数据行

        # 表格数据行必须全部保持 `// ` 前缀，且不能回落到 None 占位。
        if all(str_line.startswith("// ") and str_line != "// None" for str_line in list_data_rows):

            # 返回合法 table_mode 以及真实数据行数量。
            return "table_mode", len(list_data_rows)

# 既不是合法 none_mode，也不是合法 table_mode。

    # 返回空模式标记，提示调用方当前块不满足任一合法版式。
    return None, 0

# 供 `_header_document_path_issues` 复用的拆分 helper，专门处理检查 Description/Simulations 是否回到固定路径合同。
def _header_document_path_issues(
    str_text: str,
    str_pre_module: str,
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查双语 header 的说明文档与仿真工程字段是否保持固定路径合同。

    :param str_text: 当前 Verilog 源码文本。
    :param str_pre_module: module 声明之前的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: Description/Simulations 路径相关诊断列表。
    """

    # 先定位首个 module 锚点，后续用模块名重建固定文档路径合同。
    tuple_module_anchor = _first_module_anchor(str_text)  # 首个 module 锚点

    # 缺少 module 声明时无法推导 `{module_name}` 的目标值，直接跳过本规则。
    if tuple_module_anchor is None:

        # 无 module 锚点时不生成路径合同诊断。
        return []

    # 从锚点里提取模块名，供 Description/Simulations 固定路径模板实例化。
    _, str_module_name = tuple_module_anchor  # 当前 header 对应的模块名

    # 按模块名重建双语固定路径，避免沿用旧 header 的自由文本。
    dict_header_paths = default_header_paths(str_module_name)  # 双语固定路径集合

    # 读取共享布局前缀，拼出四条必须逐字出现的目标行。
    dict_layout = shared_header_layout_config()  # 双语 header 共享布局

    # 组装英中四条精确路径行，后续统一检查是否全部存在。
    tuple_expected_lines = (  # Description/Simulations 精确合同行
        f"{dict_layout['english']['description_prefix']}{dict_header_paths['english']['description']}",  # English Description 固定路径行
        f"{dict_layout['english']['simulations_prefix']}{dict_header_paths['english']['simulations']}",  # English 仿真字段必须保留小写 testbench/vivado 路径
        f"{dict_layout['chinese']['description_prefix']}{dict_header_paths['chinese']['description']}",  # Chinese 模块说明固定路径行
        f"{dict_layout['chinese']['simulations_prefix']}{dict_header_paths['chinese']['simulations']}",  # Chinese 仿真工程固定路径行
    )

    # 四条路径行全部命中时，说明 header 已满足 Description/Simulations 路径合同。
    if all(str_expected_line in str_pre_module for str_expected_line in tuple_expected_lines):

        # 已满足固定路径合同，不再追加任何 VG068。
        return []

    # 任意一条固定路径行缺失时，统一追加 Description/Simulations 合同诊断。
    return [
        QualityIssue(
            "VG068",
            str_severity,
            "Header Description/Simulations fields must use the fixed bilingual path contract "
            "(`description/testbench` in English and `Description/TestBench` in Chinese).",
            str_rel_path,
            1,
            "header.description_simulations_paths",
        )
    ]

# 供 `_header_postamble_issues` 复用的拆分 helper，专门处理检查标准 header 后只能保留一个空行再进入 module。
def _header_postamble_issues(
    str_text: str,
    str_pre_module: str,
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查标准 header 结束后只能保留一个空行再进入 module。

    :param str_text: 当前 Verilog 源码文本。
    :param str_pre_module: module 声明之前的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: header 收尾与 module 邻接关系的诊断列表。
    """

    # 只有双语标准 header 已经出现时，才继续检查 header 收尾与 module 邻接合同。
    if HEADER_ENGLISH_SEPARATOR not in str_pre_module or HEADER_CHINESE_SEPARATOR not in str_pre_module:

        # 没有完整双语 header 锚点时，不在这里补充 postamble 诊断。
        return []

    # 为 postamble 检查重新抓取 module 锚点，后续据此回溯 header 尾部。
    tuple_module_anchor = _first_module_anchor(str_text)  # postamble 检查使用的 module 锚点

    # 缺少 module 声明时无法判断 header 收尾位置，直接返回空结果。
    if tuple_module_anchor is None:

        # 无 module 锚点时跳过 postamble 邻接检查。
        return []

    # 取出首个 module 的一基行号，作为回溯 header 末尾的起点。
    int_module_line_no, _ = tuple_module_anchor  # 首个 module 的一基行号

    # 按物理行拆分源码，供零基索引回溯连续空白区。
    list_lines = str_text.splitlines()  # 当前文件物理行列表

    # 从 module 上一行开始向上扫描，定位 header 尾部空白段。
    int_scan_index = int_module_line_no - 2  # 从 module 上一行开始回溯的零基索引

    # 连续空白计数器用于验证是否恰好只保留一个空行。
    int_blank_line_count = 0  # header 末尾到 module 之间的连续空白行数量

    # 逐行回溯 module 前的空白区，统计 header 结束到 module 之间的真实空行数。
    while int_scan_index >= 0 and not list_lines[int_scan_index].strip():

        # 每遇到一条空白行就递增间隔计数。
        int_blank_line_count += 1  # 已统计到的连续空白行数量

        # 继续向上检查上一条物理行是否仍为空白。
        int_scan_index -= 1  # 下一次 while 判断使用的零基索引

    # 初始化诊断容器，统一收集 spacing 与 trailing-summary 两类问题。
    list_issues: list[QualityIssue] = []  # 当前 header 收尾合同诊断列表

    # 先验证标准 header 与 module 之间是否恰好只有一个空行。
    if int_blank_line_count != 1:

        # 空白行数量不符合合同就追加 module spacing 诊断。
        list_issues.append(
            QualityIssue(
                "VG067",
                str_severity,
                "Standard header must be followed by exactly one blank line before `module`.",
                str_rel_path,
                max(int_module_line_no - 1, 1),
                "header.module_spacing",
            )
        )

    # 再验证 header 最后一条非空白内容是否就是中文历史记录正文。
    if int_scan_index < 0 or not _is_chinese_history_record_line(list_lines[int_scan_index]):

        # 中文历史记录后若还残留摘要注释或其他内容，就追加 trailing-summary 诊断。
        list_issues.append(
            QualityIssue(
                "VG067",
                str_severity,
                "Header must end at the last Chinese history record; do not keep extra summary comments "
                "between the header and `module`.",
                str_rel_path,
                max(int_module_line_no - 1, 1),
                "header.trailing_summary_comment",
            )
        )

    # 返回 header 收尾合同阶段收集到的全部诊断。
    return list_issues

# 供 `_first_module_anchor` 复用的拆分 helper，专门处理首个 module 声明的一基行号和模块名。
def _first_module_anchor(str_text: str) -> tuple[int, str] | None:
    """
    返回首个 module 声明的一基行号和模块名。

    :param str_text: 当前 Verilog 源码文本。
    :return: 命中时返回 `(line_no, module_name)`，否则返回 None。
    """

    # 逐行扫描首个 module 声明，避免跨行正则推断行号。
    for int_line_no, str_line in enumerate(str_text.splitlines(), start=1):

        # obj_match 用于提取首个 module 名。
        obj_match = re.match(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_]*)\b", str_line)  # module 声明匹配结果

        # 命中时返回一基行号和模块名。
        if obj_match is not None:

            # 当前行就是首个 module 声明锚点。
            return int_line_no, obj_match.group(1)

    # 未找到 module 声明。
    return None

# 供 `_is_chinese_history_record_line` 复用的拆分 helper，专门处理一行 header 注释是否为中文历史记录正文。
def _is_chinese_history_record_line(str_line: str) -> bool:
    """
    判断一行 header 注释是否为中文历史记录正文。

    :param str_line: 当前源码行。
    :return: 命中中文历史记录正文时返回 True。
    """

    # 非纯注释行不可能是中文历史记录。
    if not _is_pure_line_comment(str_line):

        # 只有 header 注释行才参与中文历史记录判断。
        return False

    # str_body 统一取出去掉 `//` 之后的可见正文。
    str_body = _line_comment(str_line)  # 中文历史记录候选正文

    # 中文历史记录必须包含中文日期和版本号，避免误把表头当成正文。
    return re.search(r"\d{4}年\d{1,2}月\d{1,2}日", str_body) is not None and re.search(
        r"\bV\d+\.\d+\b",
        str_body,
    ) is not None

# 供 `_header_version_history_issues` 复用的拆分 helper，专门处理检查双语文件头中的版本号和修订历史记录。
def _header_version_history_issues(
    str_pre_module: str,
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查双语文件头中的版本号和修订历史记录。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: 版本和历史记录诊断列表。
    """

    # list_issues 保存版本和历史记录诊断。
    list_issues: list[QualityIssue] = []  # 版本历史诊断集合

    # 英文 Version 字段沿用历史模板命名。
    tuple_english_version_field = ("Version", "header.version.english")  # 英文版本字段规则

    # 中文当前版本字段用于双语文件头一致性检查。
    tuple_chinese_version_field = ("当前版本", "header.version.chinese")  # 中文版本字段规则

    # 先检查英文版本字段，保持现有报告顺序。
    list_version_fields = [tuple_english_version_field]  # 版本字段格式检查表

    # 再检查中文当前版本字段。
    list_version_fields += [tuple_chinese_version_field]  # 补充中文当前版本字段

    # 逐个检查已存在的版本字段。
    for str_field, str_rule in list_version_fields:

        # str_version_value 保留字段原始值，避免 _extract_header_field 只取标识符。
        str_version_value = _extract_header_field_value(str_pre_module, str_field)  # 当前版本字段值

        # 缺失字段由 VG007 必填字段检查负责。
        if not str_version_value:

            # 当前字段不存在，跳过格式检查。
            continue

        # 版本号必须是 Vx.y。
        if not HEADER_VERSION_PATTERN.fullmatch(str_version_value):

            # 非标准版本会破坏生成历史和人工追踪。
            list_issues.append(
                QualityIssue(
                    "VG007",
                    str_severity,
                    f"Header field `{str_field}` must use Vx.y version format, got `{str_version_value}`.",
                    str_rel_path,
                    1,
                    str_rule,
                )
            )

    # 修订历史区必须至少包含一条带日期和版本号的记录。
    if not _header_has_history_record(str_pre_module):

        # 只有表头没有记录时不能视为可追溯历史。
        list_issues.append(
            QualityIssue(
                "VG007",
                str_severity,
                "Header history must contain at least one dated record with a Vx.y version.",
                str_rel_path,
                1,
                "header.history_records",
            )
        )

    # 返回版本历史诊断。
    return list_issues

# 供 `_extract_header_field_value` 复用的拆分 helper，专门处理从文件头中读取指定字段的完整值。
def _extract_header_field_value(str_pre_module: str, str_field: str) -> str:
    """
    从文件头中读取指定字段的完整值。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_field: 需要提取的文件头字段名。
    :return: 字段值文本，未找到时返回空字符串。
    """

    # 版本字段行统一形如 // Field: value 或 // 字段: value。
    str_pattern = rf"(?m)^\s*//\s*{re.escape(str_field)}\s*:\s*(?P<value>.*?)\s*$"  # 指定头字段整行正则

    # 正则匹配结果保留 value 分组，避免只抽取版本标识符。
    obj_match = re.search(str_pattern, str_pre_module)  # 指定头字段匹配结果

    # 找不到目标字段时交给必填字段规则报告。
    if obj_match is None:

        # 缺字段由必填字段规则处理。
        return ""

    # 返回完整字段值，供版本格式精确检查。
    return obj_match.group("value").strip()

# 供 `_header_has_history_record` 复用的拆分 helper，专门处理文件头修订历史区是否至少有一条真实记录。
def _header_has_history_record(str_pre_module: str) -> bool:
    """
    判断文件头修订历史区是否至少有一条真实记录。

    :param str_pre_module: module 声明之前的源码文本。
    :return: 存在日期和 Vx.y 版本号记录时返回 True。
    """

    # 逐行扫描注释正文，避开 History 表头。
    for str_line in str_pre_module.splitlines():

        # 非注释行不是 header 历史记录。
        if _line_comment_start(str_line) < 0:

            # 跳过空行或横幅线。
            continue

        # str_body 是去掉 // 后的 header 行正文。
        str_body = _line_comment(str_line)  # header 注释正文

        # 表头和字段名不算历史记录。
        if _is_history_heading_line(str_body):

            # 当前行只是 History 标题或表头。
            continue

        # 历史正文必须同时出现年份和 Vx.y 版本号。
        if re.search(r"\b\d{4}(?:/|年|-)\d{1,2}", str_body) and re.search(r"\bV\d+\.\d+\b", str_body):

            # 找到真实历史记录。
            return True

    # 未找到真实历史正文。
    return False

# 供 `_is_history_heading_line` 复用的拆分 helper，专门处理一行 header 注释是否属于历史字段或表头。
def _is_history_heading_line(str_body: str) -> bool:
    """
    判断一行 header 注释是否属于历史字段或表头。

    :param str_body: 去掉 // 后的注释正文。
    :return: 该行只是历史标题或表头时返回 True。
    """

    # str_normalized 去掉外侧空白用于表头判断。
    str_normalized = str_body.strip()  # 历史行表头判定文本

    # 空行和字段标题都不是历史记录。
    if not str_normalized:

        # 空注释行跳过。
        return True

    # 英文/中文历史字段标题。
    if str_normalized in {"History:", "修订历史:"}:

        # 字段标题不是记录。
        return True

    # 表格表头不算记录。
    return str_normalized.lower().startswith("time") or str_normalized.startswith("时间")

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
    _raw_text_rules
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
