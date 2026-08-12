"""提取或渲染仅包含参数和端口声明的 Verilog 外部接口 stub。"""

# future annotations 延后解析路径和集合类型。
from __future__ import annotations

# argparse 定义提取与 manifest 渲染的稳定 CLI 合同。
import argparse

# json 读取项目 IP 接口 manifest。
import json

# re 负责屏蔽注释、定位 module 和验证标识符。
import re

# sys 提供标准错误流和进程退出码入口。
import sys

# pathlib 统一处理文件与 Vivado 源目录输入。
from pathlib import Path

# Any 和 Sequence 描述 manifest 与稳定输入序列。
from typing import Any, Sequence

# 非代码 token 屏蔽后保持原字符下标，便于从原文切片。
NON_CODE_TOKEN_PATTERN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*.*?\*/', re.DOTALL)  # 字符串与 Verilog 注释 token

# module 定义定位支持常见的 automatic 修饰形式。
MODULE_PATTERN = re.compile(r"\bmodule\s+(?:automatic\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\b")  # module 定义起点

# endmodule 标记限定单个定义的源码边界。
ENDMODULE_PATTERN = re.compile(r"(?<![A-Za-z0-9_$])endmodule(?![A-Za-z0-9_$])")  # module 定义终点

# Verilog 标识符白名单避免 manifest 注入任意源码片段。
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")  # 普通 Verilog 标识符

# 旧式模块体只提取公开参数和端口方向声明。
PUBLIC_DECLARATION_PATTERN = re.compile(r"(?ms)^[ \t]*(?:parameter|localparam|input|output|inout)\b.*?;")  # 旧式公开声明语句

# 允许 manifest 使用静态位宽表达式所需的保守字符集合。
WIDTH_PATTERN = re.compile(r"^[A-Za-z0-9_$+\-*/(): \t]+$")  # 端口区间表达式白名单

# 允许 parameter 默认值保留常见 Verilog 字面量和简单表达式。
VALUE_PATTERN = re.compile(r'^[A-Za-z0-9_$+\-*/():{}\'",. \t]+$')  # 参数默认值白名单

# header 单分支条件保护只移除控制行并保留其中接口声明。
HEADER_GUARD_PATTERN = re.compile(r"(?m)^[ \t]*`(?:ifdef|ifndef|endif)\b[^\r\n]*(?:\r?\n)?")  # 单分支保护行

# else/elsif 代表多套接口候选，提取器不能猜测宏环境。
HEADER_ALTERNATIVE_PATTERN = re.compile(r"(?m)^[ \t]*`(?:else|elsif)\b")  # 多分支保护标记

# 旧式端口声明规则分离方向、类型/位宽前缀和名称列表。
OLD_STYLE_PORT_PREFIX_TEXT = r"^(?P<direction>input|output|inout)\s+"  # 方向字段规则

# 名称字段允许共享类型关键字和 packed width 前缀。
OLD_STYLE_PORT_NAMES_TEXT = r"(?P<prefix>(?:(?:wire|reg|logic|signed|unsigned)\s+|\[[^\]\r\n]+\]\s*)*)(?P<names>.+);$"  # 名称字段规则

# 两段规则拼接后覆盖一条完整旧式端口声明。
OLD_STYLE_PORT_PATTERN_TEXT = OLD_STYLE_PORT_PREFIX_TEXT + OLD_STYLE_PORT_NAMES_TEXT  # 旧式端口字段

# 预编译旧式端口规则供每条公开声明复用。
OLD_STYLE_PORT_PATTERN = re.compile(OLD_STYLE_PORT_PATTERN_TEXT, re.DOTALL)  # 多名称端口声明

# 旧式 module header 的最后一个括号只允许普通端口名称列表。
OLD_STYLE_HEADER_PATTERN = re.compile(r"\((?P<ports>[A-Za-z0-9_$, \t\r\n]+)\)\s*;$")  # header 端口列表

# 拆分后的单名称端口声明从分号前提取最终标识符。
PORT_DECLARATION_NAME_PATTERN = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\s*;$")  # 声明端口名称

# StubExtractionError 表示外部接口来源不足或不唯一。
class StubExtractionError(ValueError):
    """报告无法安全生成外部接口 stub 的确定原因。"""

# _mask_non_code 屏蔽字符串和注释但保留换行与字符位置。
def _mask_non_code(str_source: str) -> str:
    """返回与源码等长的非代码 token 屏蔽文本。

    参数:
        str_source: 待扫描的 Verilog/SystemVerilog 源码。
    返回:
        注释和字符串替换为空格、换行位置保持不变的文本。
    """

    # token 替换保留换行，确保后续切片位置与原文一致。
    def replace_token(obj_match: re.Match[str]) -> str:
        """用空格屏蔽单个 token 并保留其换行符。

        参数:
            obj_match: 当前字符串或注释 token 的正则匹配。
        返回:
            与 token 等长且只保留换行符的屏蔽文本。
        """

        # token 原文决定需要保留的换行布局。
        str_token = obj_match.group(0)  # 当前字符串或注释 token

        # 每个非换行字符替换为空格，维持全局字符下标。
        return "".join(
            str_character if str_character in "\r\n" else " "
            for str_character in str_token
        )

    # 一次替换全部字符串与注释 token。
    return NON_CODE_TOKEN_PATTERN.sub(replace_token, str_source)

# _directory_source_files 收集单个目录中的 Verilog 源文件。
def _directory_source_files(path_source_root: Path) -> list[Path]:
    """递归收集目录中的 Verilog/SystemVerilog 文件。

    参数:
        path_source_root: 已确认存在的源目录。
    返回:
        按路径排序的 ``.v`` 与 ``.sv`` 文件。
    """

    # 目录扫描结果只保留普通 Verilog/SystemVerilog 文件。
    return [
        path_child  # 当前目录中的 Verilog 源文件
        for path_child in sorted(path_source_root.rglob("*"))  # 确定顺序遍历目录
        if path_child.is_file() and path_child.suffix.lower() in {".v", ".sv"}  # 过滤支持后缀
    ]

# _source_files 展开文件和目录为确定顺序的 Verilog 源文件。
def _source_files(source_paths: Sequence[Path]) -> list[Path]:
    """展开调用方提供的 Verilog 文件和目录。

    参数:
        source_paths: 显式源文件或待递归扫描的目录。
    返回:
        去重并按路径排序的 ``.v`` 与 ``.sv`` 文件。
    异常:
        StubExtractionError: 来源不存在或显式文件不是 Verilog 源。
    """

    # 路径键去重避免文件和其父目录同时输入时重复定义。
    dict_files: dict[str, Path] = {}  # 规范路径键到实际源文件

    # 逐个展开调用方显式授权的来源。
    for source_path in source_paths:

        # 所有路径先规范为 Path，兼容字符串式调用方。
        path_normalized = Path(source_path)  # 当前待展开来源

        # 单文件仅接受 Verilog/SystemVerilog 后缀。
        if path_normalized.is_file():

            # 不相关文件不能进入模块定义扫描。
            if path_normalized.suffix.lower() not in {".v", ".sv"}:

                # 显式错误避免调用方误以为 manifest 或日志已被解析。
                raise StubExtractionError(f"> ERR: [Python] 不支持的外部接口源文件: {path_normalized}")

            # 规范绝对路径作为稳定去重键。
            dict_files[str(path_normalized.resolve())] = path_normalized  # 登记显式源文件

            # 单文件完成登记后继续处理下一来源。
            continue

        # 目录递归覆盖 Vivado XPM、UNISIM 和项目 IP 生成树。
        if path_normalized.is_dir():

            # 目录 helper 已完成后缀过滤和稳定排序。
            for path_child in _directory_source_files(path_normalized):

                # 规范键消除目录输入之间的重叠文件。
                dict_files[str(path_child.resolve())] = path_child  # 登记目录内源文件

            # 当前目录完成展开后继续处理下一来源。
            continue

        # 不存在的来源必须 fail closed，不能退化为空结果。
        raise StubExtractionError(f"> ERR: [Python] 外部接口源不存在: {path_normalized}")

    # 稳定顺序保证重复定义诊断和输出可重现。
    return [dict_files[str_key] for str_key in sorted(dict_files)]

# _module_blocks 提取单个源文件中的目标 module 定义原文。
def _module_blocks(str_source: str, set_module_names: set[str]) -> list[tuple[str, str]]:
    """提取源文本中显式请求的完整 module 块。

    参数:
        str_source: 单个 Verilog/SystemVerilog 文件文本。
        set_module_names: 调用方请求的模块名集合。
    返回:
        模块名与从 ``module`` 到 ``endmodule`` 的原文列表。
    异常:
        StubExtractionError: 请求模块缺少闭合 ``endmodule``。
    """

    # 屏蔽注释和字符串，防止其中的 module 文本形成假定义。
    str_masked = _mask_non_code(str_source)  # 与原文等长的结构扫描文本

    # blocks 保持源文件内定义顺序。
    list_blocks: list[tuple[str, str]] = []  # 请求模块的完整定义块

    # 每个 module 起点独立查找其后最近的 endmodule。
    for module_match in MODULE_PATTERN.finditer(str_masked):

        # 未请求模块无需执行定义终点扫描。
        str_module_name = module_match.group("name")  # 当前 module 定义名

        # 只有显式白名单模块允许进入接口提取。
        if str_module_name not in set_module_names:

            # 跳过与显式白名单无关的 vendor 实现。
            continue

        # 当前定义之后的首个 endmodule 限定完整模块块。
        end_match = ENDMODULE_PATTERN.search(str_masked, module_match.end())  # 当前定义终点

        # 未闭合定义不能生成看似可信的接口。
        if end_match is None:

            # 错误直接包含模块名，便于定位损坏的 vendor 源。
            raise StubExtractionError(f"> ERR: [Python] 模块缺少 endmodule: {str_module_name}")

        # 从原文切片保留声明中的字符串参数和格式。
        str_block = str_source[module_match.start() : end_match.end()]  # 当前完整 module 原文

        # 当前请求定义进入文件内命中集合。
        list_blocks.append((str_module_name, str_block))

    # 返回当前文件内命中的请求模块。
    return list_blocks

# _header_end 定位 module header 的顶层分号。
def _header_end(str_module_block: str) -> int:
    """返回 module header 顶层分号之后的字符下标。

    参数:
        str_module_block: 从 ``module`` 到 ``endmodule`` 的完整定义。
    返回:
        包含 header 顶层分号的切片终点。
    异常:
        StubExtractionError: header 括号不平衡或缺少顶层分号。
    """

    # 结构扫描文本屏蔽字符串内括号和分号。
    str_masked = _mask_non_code(str_module_block)  # module 块的结构扫描文本

    # depth 跟踪 parameter/port 括号嵌套。
    int_depth = 0  # 当前圆括号深度

    # 从 module 关键字之后扫描首个顶层分号。
    for int_index, str_character in enumerate(str_masked):

        # 左括号进入 parameter 或端口列表。
        if str_character == "(":

            # 递增括号层级后继续扫描。
            int_depth += 1  # 进入当前 header 圆括号

            # 当前字符完成处理后继续扫描 header。
            continue

        # 右括号退出当前 parameter 或端口列表。
        if str_character == ")":

            # 负深度代表 header 括号结构损坏。
            int_depth -= 1  # 退出当前 header 圆括号

            # 任何时刻都不允许右括号多于左括号。
            if int_depth < 0:

                # 损坏 header 不能产生可信 stub。
                raise StubExtractionError("> ERR: [Python] module header 括号不平衡")

            # 当前右括号完成处理后继续扫描 header。
            continue

        # 顶层分号结束 module header。
        if str_character == ";" and int_depth == 0:

            # 返回包含分号的切片终点。
            return int_index + 1

    # 缺少 header 分号时拒绝输出。
    raise StubExtractionError("> ERR: [Python] module header 缺少顶层分号")

# _flatten_header_guards 展开没有替代分支的接口条件保护。
def _flatten_header_guards(str_header: str) -> str:
    """移除不改变接口候选集合的 header 条件保护行。

    参数:
        str_header: 保留 vendor 条件编译行的 module header。
    返回:
        保留受保护声明且移除单分支控制行的 header。
    异常:
        StubExtractionError: header 存在 else 或 elsif 替代接口。
    """

    # 多分支 header 需要真实宏环境才能选择唯一接口。
    if HEADER_ALTERNATIVE_PATTERN.search(str_header) is not None:

        # 禁止把两个替代分支同时拼入 stub。
        raise StubExtractionError("> ERR: [Python] module header 含条件编译替代分支")

    # 单分支 timing guard 只控制仿真参数可见性，stub 保留其中声明。
    return HEADER_GUARD_PATTERN.sub("", str_header)

# _normalize_old_style_declaration 拆分旧式多名称端口声明。
def _normalize_old_style_declaration(str_declaration: str) -> list[str]:
    """把旧式 ``input A, B`` 拆成 formatter 可解析的独立声明。

    参数:
        str_declaration: 单条 parameter、localparam 或端口方向声明。
    返回:
        原声明或共享方向/位宽前缀的逐名称声明。
    异常:
        StubExtractionError: 端口名称不是普通 Verilog 标识符。
    """

    # parameter 和 localparam 不属于旧式端口方向声明。
    declaration_match = OLD_STYLE_PORT_PATTERN.fullmatch(str_declaration)  # 旧式端口字段匹配

    # 非端口公开声明不需要执行名称拆分。
    if declaration_match is None:

        # 非端口声明保持 vendor 原文。
        return [str_declaration]

    # 方向和类型/位宽前缀由同一声明中的全部名称共享。
    str_direction = declaration_match.group("direction")  # 当前端口方向

    # 类型和 packed width 前缀复制到每个拆分名称。
    str_prefix = declaration_match.group("prefix")  # 当前类型与位宽前缀

    # 顶层旧式端口名称以逗号分隔。
    list_names = [
        str_name.strip()  # 去除名称两侧布局空白
        for str_name in declaration_match.group("names").split(",")  # 拆分同方向端口名称
    ]  # 当前声明中的端口名称

    # 所有拆分名称必须保持普通标识符语义。
    for str_name in list_names:

        # 非法名称可能来自复杂声明，不能安全进行文本重写。
        if IDENTIFIER_PATTERN.fullmatch(str_name) is None:

            # 报告原始名称帮助缩小不支持的 vendor 语法。
            raise StubExtractionError(f"> ERR: [Python] 无法拆分旧式端口名称: {str_name}")

    # 每个名称生成一条共享方向和位宽的独立声明。
    return [
        f"{str_direction} {str_prefix}{str_name};"  # formatter 可解析的单名称端口声明
        for str_name in list_names  # 保持 vendor 端口顺序
    ]

# _old_style_header_ports 提取旧式 module header 的公开端口白名单。
def _old_style_header_ports(str_header: str) -> set[str]:
    """返回旧式 module header 明确列出的端口名称。

    参数:
        str_header: 不含 ANSI 方向关键字的 module header。
    返回:
        可从模块体接纳方向声明的公开端口名集合。
    异常:
        StubExtractionError: header 端口列表缺失或含非法名称。
    """

    # 最后一个普通括号列表对应旧式 module 端口顺序。
    port_list_match = OLD_STYLE_HEADER_PATTERN.search(str_header)  # 旧式 header 端口列表

    # 无法识别端口列表时不能扫描任意 body input/output。
    if port_list_match is None:

        # 拒绝把 function/task 局部端口误当成模块接口。
        raise StubExtractionError("> ERR: [Python] 无法识别旧式 module 端口列表")

    # header 名称以逗号分隔并去除布局空白。
    list_port_names = [
        str_name.strip()  # 去除 header 端口名两侧空白
        for str_name in port_list_match.group("ports").split(",")  # 拆分公开端口顺序
    ]  # 旧式 header 端口名

    # 所有 header 名称必须是普通 Verilog 标识符。
    for str_name in list_port_names:

        # 非法名称意味着当前旧式 header 超出安全支持范围。
        if IDENTIFIER_PATTERN.fullmatch(str_name) is None:

            # 报告原始字段帮助定位 vendor 特殊语法。
            raise StubExtractionError(f"> ERR: [Python] 非法旧式 header 端口名: {str_name}")

    # 集合用于过滤 module body 中同名之外的局部端口声明。
    return set(list_port_names)

# _old_style_public_declarations 恢复旧式 header 的参数和端口方向。
def _old_style_public_declarations(str_body: str, str_header: str) -> list[str]:
    """提取旧式 module 的公开参数和 header 端口声明。

    参数:
        str_body: module header 之后到 endmodule 的原文。
        str_header: 已展开单分支保护的旧式 module header。
    返回:
        保持 vendor 顺序的公开参数与逐名称端口声明。
    异常:
        StubExtractionError: header 端口缺少唯一方向声明。
    """

    # header 白名单防止 function/task 局部端口泄漏。
    set_header_ports = _old_style_header_ports(str_header)  # 允许接纳方向声明的端口名

    # 屏蔽 body 注释和字符串，确保声明边界只来自代码。
    str_masked_body = _mask_non_code(str_body)  # body 公开声明扫描文本

    # 所有公开关键字候选保留原始声明顺序。
    list_matches = list(PUBLIC_DECLARATION_PATTERN.finditer(str_masked_body))  # body 声明候选

    # 未找到端口时默认允许参数扫描到 body 末尾。
    int_first_port = len(str_masked_body)  # 参数接纳范围终点

    # 首个端口方向之前的 parameter/localparam 属于模块接口前导区。
    for declaration_match in list_matches:

        # 端口方向关键字标记模块参数前导区终点。
        if re.match(r"[ \t]*(?:input|output|inout)\b", declaration_match.group(0)):

            # 保存首条 module 端口方向声明位置。
            int_first_port = declaration_match.start()  # 首个公开端口声明位置

            # 后续端口不再影响参数接纳边界。
            break

    # 输出分别收集参数前导区和 header 指定端口。
    list_declarations: list[str] = []  # 旧式 module 的公开接口声明

    # seen 集合防止 body 中重复方向声明进入 stub。
    set_seen_ports: set[str] = set()  # 已恢复方向的 header 端口名

    # 逐候选判断其是否属于公开接口。
    for declaration_match in list_matches:

        # 原文切片保留参数字面量和声明格式。
        str_declaration = str_body[declaration_match.start() : declaration_match.end()].strip()  # 当前声明原文

        # 参数只在首个模块端口声明之前接纳。
        if re.match(r"(?:parameter|localparam)\b", str_declaration):

            # 位于接口前导区的参数保持 vendor 声明顺序。
            if declaration_match.start() < int_first_port:

                # 模块参数进入旧式 stub 的接口前导区。
                list_declarations.append(str_declaration)

            # parameter 候选处理完成后跳过端口分支。
            continue

        # 多名称旧式端口拆成 formatter 可解析的单名称声明。
        for str_port_declaration in _normalize_old_style_declaration(str_declaration):

            # 分号前的最终标识符是当前声明端口名。
            port_name_match = PORT_DECLARATION_NAME_PATTERN.search(str_port_declaration)  # 当前声明端口名

            # 无法定位名称的声明不可能匹配 header 白名单。
            if port_name_match is None:

                # 保守跳过无法证明属于 module header 的声明。
                continue

            # 只有 header 明确列出的端口允许进入 stub。
            str_port_name = port_name_match.group("name")  # 当前方向声明的端口名

            # 局部端口和重复声明均不属于公开接口增量。
            if str_port_name not in set_header_ports or str_port_name in set_seen_ports:

                # 局部或重复声明不能改变公开接口。
                continue

            # 首次命中的 header 端口保留其方向和位宽。
            list_declarations.append(str_port_declaration)

            # 记录端口已恢复，供重复过滤和完整性检查复用。
            set_seen_ports.add(str_port_name)

    # 每个 header 端口都必须找到唯一方向声明。
    if set_seen_ports != set_header_ports:

        # 报告缺失名称，避免生成方向不完整的旧式 stub。
        str_missing_ports = ", ".join(sorted(set_header_ports - set_seen_ports))  # 缺少方向声明的端口

        # 不完整方向集合不能生成可被 VG097 信任的 stub。
        raise StubExtractionError(f"> ERR: [Python] 旧式端口缺少方向声明: {str_missing_ports}")

    # 返回参数前导区和完整端口方向声明。
    return list_declarations

# _interface_only 将完整 module 块收缩为参数和端口声明。
def _interface_only(str_module_block: str) -> str:
    """把完整模块定义收缩成无实现的接口 stub。

    参数:
        str_module_block: 从安装源中提取的完整模块定义。
    返回:
        仅保留公开参数和端口声明的模块文本。
    """

    # header 保留 ANSI 参数与端口声明的原始文本。
    int_header_end = _header_end(str_module_block)  # module header 切片终点

    # header 原文承载 ANSI 参数和端口的完整公开合同。
    str_header = str_module_block[:int_header_end].strip()  # 待输出的 module header 原文

    # 单分支条件保护展开后保留其中 timing parameter。
    str_header = _flatten_header_guards(str_header)  # formatter 可消费的 module header

    # ANSI header 已包含端口方向，不再扫描可能属于 function/task 的 body 端口。
    bool_ansi_ports = bool(re.search(r"\b(?:input|output|inout)\b", _mask_non_code(str_header)))  # 是否为 ANSI 端口

    # 旧式 module header 需要补回 body 前导的公开声明。
    list_declarations: list[str] = []  # 旧式参数与端口声明

    # 仅旧式 header 需要从模块体恢复方向声明。
    if not bool_ansi_ports:

        # endmodule 之前的 body 提供参数和 header 端口方向。
        str_body = str_module_block[int_header_end:]  # 待筛选的 module body 原文

        # body helper 只接纳 header 白名单端口，忽略局部 function/task 端口。
        list_declarations = _old_style_public_declarations(str_body, str_header)  # 旧式公开接口声明

    # 每个 stub 只包含接口声明和显式结束标记。
    list_lines = [str_header, *list_declarations, "endmodule"]  # stub 有序文本行

    # 单个结尾换行便于多个 module 稳定拼接。
    return "\n".join(list_lines).rstrip() + "\n"

# extract_interface_stubs 从安装源或项目生成源提取显式模块白名单。
def extract_interface_stubs(
    source_paths: Sequence[Path],
    module_names: Sequence[str],
) -> str:
    """从 Verilog 源文件或目录提取纯接口 stub。

    参数:
        source_paths: Vivado XPM、UNISIM 或项目 IP 的源文件和目录。
        module_names: 必须精确提取且各自只定义一次的模块名。
    返回:
        按调用方模块顺序拼接的纯接口 Verilog 文本。
    异常:
        StubExtractionError: 来源、名称或模块定义不满足唯一接口合同。
    """

    # 模块名先执行格式和重复校验，避免不稳定输出。
    list_module_names = list(module_names)  # 保持调用方请求顺序的模块名

    # 空白名单必须在扫描 vendor 目录之前拒绝。
    if not list_module_names:

        # 空白名单可能意外复制全部 vendor 模块，必须拒绝。
        raise StubExtractionError("> ERR: [Python] 至少需要一个显式模块名")

    # 每个模块名必须是普通 Verilog 标识符。
    for str_module_name in list_module_names:

        # 非法名称不得进入正则或输出文本。
        if IDENTIFIER_PATTERN.fullmatch(str_module_name) is None:

            # 报告原始名称帮助修复 catalog 或调用参数。
            raise StubExtractionError(f"> ERR: [Python] 非法模块名: {str_module_name}")

    # 重复请求没有独立语义，应在读取文件前拒绝。
    if len(set(list_module_names)) != len(list_module_names):

        # 避免同一接口在输出中重复定义。
        raise StubExtractionError("> ERR: [Python] 模块白名单包含重复名称")

    # definitions 收集跨文件定义，后续统一检查缺失与歧义。
    dict_definitions: dict[str, list[str]] = {
        str_module_name: []  # 当前模块命中的完整定义块
        for str_module_name in list_module_names  # 初始化每个显式请求
    }  # 模块名到定义原文列表

    # 所有输入文件按稳定顺序扫描请求模块。
    for source_path in _source_files(source_paths):

        # utf-8-sig 兼容常见工具生成的 BOM，错误字符保持 fail closed。
        str_source = source_path.read_text(encoding="utf-8-sig")  # 当前 Verilog 源文本

        # 当前文件内的请求定义登记到跨文件集合。
        for str_module_name, str_module_block in _module_blocks(
            str_source,
            set(list_module_names),
        ):

            # 保留全部命中以检测跨文件重复定义。
            dict_definitions[str_module_name].append(str_module_block)

    # 缺失和重复模块必须在生成任何输出前统一拒绝。
    for str_module_name in list_module_names:

        # 当前名称的定义数量决定接口是否唯一。
        int_definition_count = len(dict_definitions[str_module_name])  # 当前模块定义数量

        # 每个请求名称必须在全部来源中恰好定义一次。
        if int_definition_count != 1:

            # 区分缺失与重复，便于用户调整源根或模块白名单。
            str_reason = "缺失" if int_definition_count == 0 else f"重复 {int_definition_count} 次"  # 唯一性失败原因

            # 唯一性错误阻止输出部分 stub bundle。
            raise StubExtractionError(f"> ERR: [Python] 模块定义{str_reason}: {str_module_name}")

    # 按调用方请求顺序渲染确定的唯一接口。
    list_stubs = [
        _interface_only(dict_definitions[str_module_name][0])  # 当前唯一 module 定义的接口 stub
        for str_module_name in list_module_names  # 保持 catalog 或调用方顺序
    ]  # 已验证接口 stub 列表

    # 模块之间保留一个空行并以单换行结束。
    return "\n".join(str_stub.rstrip() for str_stub in list_stubs) + "\n"

# _manifest_identifier 读取并验证 manifest 标识符字段。
def _manifest_identifier(dict_item: dict[str, Any], str_key: str) -> str:
    """读取 manifest 中的普通 Verilog 标识符。

    参数:
        dict_item: 包含待校验字段的 manifest 对象。
        str_key: 待读取的字段名。
    返回:
        已验证的普通 Verilog 标识符。
    异常:
        StubExtractionError: 字段不是普通 Verilog 标识符。
    """

    # 字段统一转成字符串后执行完整匹配。
    str_value = str(dict_item.get(str_key) or "")  # manifest 标识符值

    # 标识符必须完整匹配，不能包含切换语句上下文的字符。
    if IDENTIFIER_PATTERN.fullmatch(str_value) is None:

        # 报告字段名和值，便于修复治理 manifest。
        raise StubExtractionError(f"> ERR: [Python] 非法 manifest {str_key}: {str_value}")

    # 返回已验证的普通标识符。
    return str_value

# _render_parameter_lines 渲染单个 module 的可选 parameter 区。
def _render_parameter_lines(list_parameters: list[dict[str, Any]]) -> list[str]:
    """渲染 manifest parameter 列表。

    参数:
        list_parameters: 当前 module 的参数对象列表。
    返回:
        空列表或完整的 ``#(...)`` 文本行。
    异常:
        StubExtractionError: 参数名或默认值不满足安全白名单。
    """

    # 无参数模块不输出井号括号区。
    if not list_parameters:

        # 空列表让调用方直接连接 module 名和端口区。
        return []

    # 参数区从标准井号括号开始。
    list_lines = ["#("]  # parameter 区文本行

    # 每个参数独立验证名称和值。
    for int_index, dict_parameter in enumerate(list_parameters):

        # 参数名只接受普通 Verilog 标识符。
        str_parameter_name = _manifest_identifier(dict_parameter, "name")  # 当前参数名

        # 默认值限制为简单 Verilog 表达式字符。
        str_parameter_value = str(dict_parameter.get("value") or "")  # 当前参数默认值

        # 空值或结构控制字符不能进入 parameter 声明。
        if not str_parameter_value or VALUE_PATTERN.fullmatch(str_parameter_value) is None:

            # 拒绝可注入语句分隔符的默认值。
            raise StubExtractionError(f"> ERR: [Python] 非法 parameter 默认值: {str_parameter_name}")

        # 最后一项不输出逗号。
        str_suffix = "," if int_index < len(list_parameters) - 1 else ""  # 参数分隔符

        # 当前参数按 manifest 顺序加入参数区。
        list_lines.append(f"    parameter {str_parameter_name} = {str_parameter_value}{str_suffix}")

    # 闭合 parameter 区后由调用方继续输出端口区。
    list_lines.append(")")

    # 返回完整 parameter 区文本行。
    return list_lines

# _render_port_lines 渲染单个 module 的必需端口区。
def _render_port_lines(list_ports: list[dict[str, Any]]) -> list[str]:
    """渲染 manifest 端口列表。

    参数:
        list_ports: 当前 module 的端口对象列表。
    返回:
        完整的 ANSI 端口区文本行。
    异常:
        StubExtractionError: 端口列表为空或字段不满足安全白名单。
    """

    # 至少一个端口才能形成可用于实例连接检查的接口。
    if not list_ports:

        # 空接口通常表示治理 manifest 遗漏。
        raise StubExtractionError("> ERR: [Python] manifest 模块缺少端口")

    # ANSI 端口区始终以左括号开始。
    list_lines = ["("]  # 端口区文本行

    # 逐端口验证方向、名称和可选位宽。
    for int_index, dict_port in enumerate(list_ports):

        # manifest direction 字段先转换为稳定字符串。
        str_direction = str(dict_port.get("direction") or "")  # 待验证的 manifest 方向字段

        # 非法方向不能退化为默认 input。
        if str_direction not in {"input", "output", "inout"}:

            # 报告原始方向帮助修复 manifest。
            raise StubExtractionError(f"> ERR: [Python] 非法端口方向: {str_direction}")

        # 端口名必须是普通 Verilog 标识符。
        str_port_name = _manifest_identifier(dict_port, "name")  # 当前端口名

        # width 省略时渲染标量，存在时按闭区间文本保留。
        str_width = str(dict_port.get("width") or "").strip()  # 当前端口区间

        # 区间表达式不能包含语句或注释控制字符。
        if str_width and WIDTH_PATTERN.fullmatch(str_width) is None:

            # 报告端口名帮助定位 manifest 字段。
            raise StubExtractionError(f"> ERR: [Python] 非法端口位宽: {str_port_name}")

        # 输入与输出默认使用 wire，满足纯接口声明用途。
        str_width_prefix = f"[{str_width}] " if str_width else ""  # 当前端口区间前缀

        # 端口分隔符只出现在仍有后续端口时。
        str_suffix = "," if int_index < len(list_ports) - 1 else ""  # 端口分隔符

        # 当前端口按 manifest 顺序加入 ANSI 声明区。
        list_lines.append(f"    {str_direction} wire {str_width_prefix}{str_port_name}{str_suffix}")

    # 标准分号闭合 module 端口区。
    list_lines.append(");")

    # 返回完整端口区文本行。
    return list_lines

# _render_manifest_module 渲染单个治理 manifest 模块。
def _render_manifest_module(dict_module: dict[str, Any]) -> str:
    """把单个 manifest 模块渲染为 ANSI 风格接口 stub。

    参数:
        dict_module: 单个项目 IP 的参数和端口接口对象。
    返回:
        ANSI 风格的无实现模块文本。
    异常:
        StubExtractionError: 模块接口字段不满足安全白名单。
    """

    # 模块名作为 stub 的公开接口标识。
    str_module_name = _manifest_identifier(dict_module, "name")  # 当前 manifest 模块名

    # 参数缺省为空列表，由 helper 决定是否输出参数区。
    list_parameters = list(dict_module.get("parameters") or [])  # 当前模块参数清单

    # 端口列表交给端口 helper 执行非空和字段验证。
    list_ports = list(dict_module.get("ports") or [])  # 当前模块端口清单

    # 输出从 module 名开始按 Verilog-2001 ANSI 形式组装。
    list_lines = [f"module {str_module_name}"]  # 当前 stub 文本行

    # 可选参数区紧跟 module 名。
    list_lines.extend(_render_parameter_lines(list_parameters))

    # 必需端口区和空实现结束标记闭合模块。
    list_lines.extend(_render_port_lines(list_ports))

    # 端口区之后立即结束无实现 stub。
    list_lines.append("endmodule")

    # 单个模块以换行结束，便于稳定拼接。
    return "\n".join(list_lines) + "\n"

# render_manifest_stubs 将治理 manifest 渲染为多模块接口 bundle。
def render_manifest_stubs(dict_manifest: dict[str, Any]) -> str:
    """渲染项目 IP 接口 manifest。

    参数:
        dict_manifest: 含 ``modules`` 数组的治理 JSON 对象。
    返回:
        按 manifest 顺序拼接的纯接口 Verilog 文本。
    异常:
        StubExtractionError: manifest 为空、重复或含非法接口字段。
    """

    # modules 是 manifest 的唯一必需顶层业务字段。
    list_modules = list(dict_manifest.get("modules") or [])  # 待渲染模块清单

    # 空模块集合无法为 VG097 提供任何接口事实。
    if not list_modules:

        # 空 manifest 不能消除任何 VG097 未知项。
        raise StubExtractionError("> ERR: [Python] manifest 缺少 modules")

    # 模块名先统一验证，防止输出重复定义。
    list_module_names = [
        _manifest_identifier(dict_module, "name")  # 用于重复检测的接口名
        for dict_module in list_modules  # 遍历待渲染项目 IP
    ]  # manifest 模块名列表

    # 同名模块只能由一份 manifest 接口对象负责。
    if len(set(list_module_names)) != len(list_module_names):

        # 重复接口会使 VG097 的 stub 选择产生歧义。
        raise StubExtractionError("> ERR: [Python] manifest 包含重复模块名")

    # 所有模块独立渲染后按声明顺序拼接。
    list_stubs = [
        _render_manifest_module(dict_module)  # 已验证项目 IP 接口文本
        for dict_module in list_modules  # 遍历唯一模块对象
    ]  # 项目 IP stub 列表

    # manifest bundle 使用统一空行分隔所有项目 IP 接口。
    return "\n".join(str_stub.rstrip() for str_stub in list_stubs) + "\n"

# create_parser 定义接口 stub 生成命令的公开参数合同。
def create_parser() -> argparse.ArgumentParser:
    """创建外部接口 stub 命令行解析器。

    返回:
        含 ``extract`` 与 ``render-manifest`` 子命令的参数解析器。
    """

    # 顶层说明强调输出只含接口，不是可替代 vendor 仿真模型的实现。
    parser = argparse.ArgumentParser(  # 约束外部接口 stub 的顶层命令合同
        description="生成供 VG097 静态位宽检查使用的纯 Verilog 外部接口 stub。",  # 解释命令用途
    )

    # 子命令必须显式选择来源类型，避免把 JSON 当 Verilog 目录扫描。
    subparsers = parser.add_subparsers(dest="command", required=True)  # 来源类型子命令

    # extract 从受信任 Verilog/SystemVerilog 源中按白名单提取接口。
    extract_parser = subparsers.add_parser(  # vendor 源提取子命令
        "extract",  # vendor 源提取命令名
        help="从 Verilog/SystemVerilog 文件或目录提取指定 module 接口。",  # 解释提取来源
    )

    # source 可重复指定多个 Vivado 安装源目录或单文件。
    extract_parser.add_argument(
        "--source",
        action="append",
        required=True,
        type=Path,
        help="Verilog/SystemVerilog 文件或目录；可重复。",
    )

    # module 必须显式列举，禁止无界复制 vendor 源中的全部 module。
    extract_parser.add_argument(
        "--module",
        action="append",
        required=True,
        help="需要提取的 module 名称；可重复。",
    )

    # output 是唯一写入目标，调用方负责选择生成物目录。
    extract_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="生成的纯接口 Verilog 文件。",
    )

    # render-manifest 从受治理 JSON 接口描述渲染项目生成 IP stub。
    manifest_parser = subparsers.add_parser(  # 项目 IP manifest 子命令
        "render-manifest",  # manifest 渲染命令名
        help="从项目 IP 接口 manifest 渲染纯接口 stub。",  # 解释 manifest 用途
    )

    # manifest 必须是调用方显式提供的 JSON 文件。
    manifest_parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="包含 modules 数组的 UTF-8 JSON manifest。",
    )

    # output 与 extract 共用相同的纯接口 Verilog 输出合同。
    manifest_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="生成的纯接口 Verilog 文件。",
    )

    # 返回完整 CLI 解析器供测试和 main 复用。
    return parser

# _render_cli_output 根据已解析子命令生成纯接口文本。
def _render_cli_output(namespace_args: argparse.Namespace) -> str:
    """根据 CLI 参数生成接口 stub 文本。

    参数:
        namespace_args: ``create_parser`` 已验证的命令行参数。
    返回:
        可直接写入 ``.v`` 文件的纯接口文本。
    异常:
        StubExtractionError: 输入无法形成唯一可信接口。
        OSError: manifest 无法读取。
        json.JSONDecodeError: manifest 不是合法 JSON。
    """

    # extract 仅向既有安全提取器传递显式来源和 module 白名单。
    if namespace_args.command == "extract":

        # 保持 CLI 顺序并由提取器完成去重、缺失和重复定义检查。
        return extract_interface_stubs(namespace_args.source, namespace_args.module)

    # 其余唯一合法子命令是 render-manifest。
    dict_manifest = json.loads(namespace_args.manifest.read_text(encoding="utf-8"))  # 项目 IP 接口 manifest

    # JSON 顶层必须是对象，不能把数组隐式包装为 modules。
    if not isinstance(dict_manifest, dict):

        # 明确拒绝无法承载 schema 字段的 JSON 顶层值。
        raise StubExtractionError("> ERR: [Python] manifest 顶层必须是 JSON 对象")

    # 由 manifest 渲染器继续执行字段白名单和重复定义检查。
    return render_manifest_stubs(dict_manifest)

# main 连接参数解析、纯接口生成与确定退出码。
def main(argv: list[str] | None = None) -> int:
    """执行外部接口 stub 命令。

    参数:
        argv: 可选命令行参数；为 ``None`` 时读取真实进程参数。
    返回:
        0 表示输出成功，2 表示输入、解析或写入失败。
    """

    # argparse 负责必需参数、子命令和 Path 类型转换。
    namespace_args = create_parser().parse_args(argv)  # 外部接口 stub 命令行参数

    # 输入解析和输出写入统一映射为可诊断的退出码 2。
    try:

        # 子命令只生成文本，不在 helper 中产生文件副作用。
        str_stub = _render_cli_output(namespace_args)  # 待写入纯接口 Verilog

        # 输出父目录必须已由调用方治理创建，CLI 不扩展目录范围。
        namespace_args.output.write_text(str_stub, encoding="utf-8")

    # 文件系统、JSON 和安全提取错误都表示调用方输入不可用。
    except (OSError, json.JSONDecodeError, StubExtractionError) as exc:

        # 移除业务异常既有前缀，下面统一只输出一次。
        str_detail = str(exc).removeprefix("> ERR: [Python] ").strip()  # 无类别前缀的错误详情

        # 输出失败原因但不打印 traceback。
        print(f"> ERR: [Python] 生成外部接口 stub 失败: {str_detail}", file=sys.stderr)

        # 退出码 2 表示输入、解析或写入错误。
        return 2

    # 成功信息包含规范输出路径和 module 数量。
    print(f"> INFO: [Python] 已生成外部接口 stub: {namespace_args.output.resolve()}")

    # 退出码 0 表示输出已完整写入。
    return 0

# 模块直接执行时把 main 返回值映射为进程退出码。
if __name__ == "__main__":

    # SystemExit 保持 ``python -m`` 调用的标准退出行为。
    raise SystemExit(main())
