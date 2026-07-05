"""评分驱动 formatter 写回前的安全路由辅助。"""

# 延迟注解解析，避免 dataclass 类型在导入期产生额外依赖。
from __future__ import annotations

# 标准库依赖只用于文本正则和不可变路由结果。
import re
from dataclasses import dataclass
from typing import Any

# 写回路由结果使用不可变结构，避免后续阶段误改评分证据。
@dataclass(frozen=True)
class FormatRouteResult:
    """描述 formatter 写文件前选择的动作、报告和候选文本。"""

    # decision 记录路由层最终选择的处理分支。
    decision: str  # 路由决策名称

    # action 映射到调用方后续执行的格式化动作。
    action: str  # 调用方动作标识

    # report 携带评分、差异和拒绝原因等结构化证据。
    report: dict[str, Any]  # 格式化路由报告

    # text 只在允许写回或预览时保存候选 Verilog 文本。
    text: str | None  # 候选输出文本

    # message 为 CLI 或上层诊断提供短说明。
    message: str  # 面向调用方的路由说明

# 轻量文本整理只做空白和缩进，不触碰语义结构。
def micro_format_text(source: str, indent_unit: str = "\t") -> str:
    """执行不改名、不重排 RTL 构造的轻量文本整理。

    参数:
        source: 待轻量整理的 Verilog 源码文本。
        indent_unit: 每级缩进使用的字符串。

    返回:
        返回只归一化换行、局部空白和缩进后的 Verilog 文本。
    """

    # 统一换行符，避免 Windows/Linux 输入差异影响行级缩进判断。
    str_normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")  # 统一后的源码文本

    # 逐行累积微格式化结果，后续只追加或删除末尾空行。
    list_rendered_lines: list[str] = []  # 已生成的输出行

    # 缩进层级只由 begin/case/generate/end 等结构关键字驱动。
    int_indent_level = 0  # 当前输出行缩进层级

    # 连续空行计数用于把空白段压缩到最多两行。
    int_blank_count = 0  # 当前连续空行数量

    # 逐行处理 Verilog 文本，保持原始 RTL 构造顺序。
    for str_raw_line in str_normalized_source.split("\n"):

        # 去除行尾和外侧空白，后续重新施加规范缩进。
        str_stripped_line = str_raw_line.rstrip().strip()  # 当前行去外侧空白后的文本

        # 空行只在已有正文后保留最多两行。
        if not str_stripped_line:

            # 记录连续空白段长度，避免输出中出现过长空洞。
            int_blank_count += 1  # 连续空行计数

            # 保留短空白段以维持模块内视觉分隔。
            if int_blank_count <= 2 and list_rendered_lines:

                # 将允许保留的空行加入输出序列。
                list_rendered_lines.append("")

            # 当前输入行已完全处理，进入下一行。
            continue

        # 遇到非空行后重新开始统计空白段。
        int_blank_count = 0  # 非空行后的空白计数复位

        # 分离 Verilog 行尾注释，避免后续操作符空格规则误改注释内容。
        tuple_code_comment = _split_line_comment(str_stripped_line)  # 代码和注释的二元片段

        # 代码片段只参与保守空白归一化。
        str_code_part = tuple_code_comment[0]  # 当前行注释前的代码文本

        # 注释片段原样保留语义文本，只在拼回时补 `//`。
        str_comment_part = tuple_code_comment[1]  # 当前行注释文本

        # 对代码区域执行保守空格归一化。
        str_normalized_code = _normalize_micro_code(str_code_part.strip())  # 操作符和逗号空格归一化后的代码

        # 重新拼回代码与注释，保持注释语义文本不被正则处理。
        str_line_body = _join_code_comment(str_normalized_code, str_comment_part)  # 带注释的输出行主体

        # 缩进判定使用小写代码前缀，避免关键字大小写影响结构识别。
        str_leading_key = str_normalized_code.strip().lower()  # 当前行结构判定键

        # end/else 类行先回退缩进，再输出当前行。
        if _dedents_before_line(str_leading_key):

            # 防止不平衡输入把缩进层级扣到负数。
            int_indent_level = max(0, int_indent_level - 1)  # 回退后的缩进层级

        # 当前行使用回退后的缩进层级写入输出。
        list_rendered_lines.append(f"{indent_unit * int_indent_level}{str_line_body}" if str_line_body else "")

        # begin/case/generate 类行影响后续行的缩进层级。
        int_indent_level = max(0, int_indent_level + _indent_delta_after_line(str_leading_key))  # 下一行缩进层级

    # 删除文件末尾多余空行，保证只保留单个最终换行。
    while list_rendered_lines and list_rendered_lines[-1] == "":

        # 末尾空白不承载 RTL 语义，可以安全移除。
        list_rendered_lines.pop()

    # 调用方和测试都期望 formatter 输出以换行结尾。
    return "\n".join(list_rendered_lines) + "\n"

# 行注释拆分依赖字符扫描状态，不能用简单字符串 split。
def _split_line_comment(line: str) -> tuple[str, str]:
    """在不进入字符串字面量的前提下拆分 Verilog `//` 注释。

    参数:
        line: 单行 Verilog 文本。

    返回:
        返回注释前代码和注释文本组成的二元组。
    """

    # bool_escaped 标记前一个字符是否为转义符。
    bool_escaped = False  # 字符串扫描中的转义状态

    # bool_in_string 标记扫描位置是否处于双引号字符串内。
    bool_in_string = False  # 当前扫描是否位于字符串字面量

    # int_index 是手写扫描游标，便于处理转义和双字符注释前缀。
    int_index = 0  # 当前扫描字符下标

    # 逐字符查找字符串外的行尾注释起点。
    while int_index < len(line):

        # 读取当前字符用于状态机判断。
        str_char = line[int_index]  # 当前扫描字符

        # 转义后的字符不参与引号或注释判断。
        if bool_escaped:

            # 转义状态只影响紧邻的一个字符。
            bool_escaped = False  # 已消费转义字符

            # 游标前进到下一个字符继续扫描。
            int_index += 1  # 下一字符下标

            # 当前字符已经完成处理。
            continue

        # 反斜杠会转义下一个字符。
        if str_char == "\\":

            # 标记下一轮扫描需要跳过语义判断。
            bool_escaped = True  # 下一字符处于转义保护下

            # 游标前进，等待下一轮消费被转义字符。
            int_index += 1  # 反斜杠后的扫描下标

            # 当前反斜杠不可能是注释起点。
            continue

        # 双引号切换字符串区域状态。
        if str_char == '"':

            # Verilog 字符串内部的 `//` 不应被当作行注释。
            bool_in_string = not bool_in_string  # 字符串内外状态

            # 游标前进到引号后的字符。
            int_index += 1  # 引号后的扫描下标

            # 引号已经完成处理。
            continue

        # 只有字符串外的 `//` 才是注释起点。
        if not bool_in_string and line.startswith("//", int_index):

            # 注释前代码去掉右侧空白，注释文本去掉前导空白。
            return line[:int_index].rstrip(), line[int_index + 2 :].strip()

        # 普通字符不改变状态，只推进扫描游标。
        int_index += 1  # 普通字符后的扫描下标

    # 没有找到行尾注释时，整行都视作代码。
    return line, ""

# 代码和注释拼接统一在这里处理，避免主流程散落格式细节。
def _join_code_comment(code: str, comment: str) -> str:
    """按 Verilog 行尾注释格式拼回代码和注释片段。

    参数:
        code: 注释前的代码片段。
        comment: 不含 `//` 前缀的注释文本。

    返回:
        返回按 Verilog 行尾注释风格拼接后的单行文本。
    """

    # 没有注释时直接返回代码片段。
    if not comment:

        # 保持纯代码行不额外添加空白。
        return code

    # 只有注释时输出合法的 Verilog 注释行。
    if not code:

        # `rstrip` 避免空注释产生尾部空格。
        return f"// {comment}".rstrip()

    # 代码和注释之间固定一个空格加 `//`。
    return f"{code} // {comment}".rstrip()

# 单行代码空白归一化保持局部、可回滚。
def _normalize_micro_code(code: str) -> str:
    """对单行 Verilog 代码执行保守空白归一化。

    参数:
        code: 不含行尾注释的单行 Verilog 代码。

    返回:
        返回操作符、逗号和括号空白归一化后的代码。
    """

    # 空代码片段通常来自纯注释行。
    if not code:

        # 纯注释行没有需要格式化的代码区域。
        return ""

    # 逗号后补空格，避免端口列表挤在一起。
    str_normalized_code = re.sub(r",(?=\S)", ", ", code)  # 逗号分隔后的代码文本

    # 比较运算符两侧补空格，保持条件表达式可读。
    str_normalized_code = re.sub(r"\s*(<=|>=|==|!=)\s*", r" \1 ", str_normalized_code)  # 比较运算符空格

    # 非比较赋值符号两侧补空格。
    str_normalized_code = re.sub(r"(?<![<>=!])\s*=\s*(?![=])", " = ", str_normalized_code)  # 赋值运算符空格

    # 逻辑与或两侧补空格，保留表达式分组。
    str_normalized_code = re.sub(r"\s*(&&|\|\|)\s*", r" \1 ", str_normalized_code)  # 逻辑运算符空格

    # 单个按位与两侧补空格，避免误伤 `&&`。
    str_normalized_code = re.sub(r"\s*(?<!&)&(?!&)\s*", " & ", str_normalized_code)  # 按位与运算符空格

    # 多余空白折叠成单空格。
    str_normalized_code = re.sub(r"\s+", " ", str_normalized_code)  # 折叠后的代码文本

    # 去除括号内侧多余空格。
    str_normalized_code = str_normalized_code.replace("( ", "(").replace(" )", ")")  # 括号贴合后的代码文本

    # 去除分号和逗号前的空格。
    str_normalized_code = str_normalized_code.replace(" ;", ";").replace(" ,", ",")  # 标点贴合后的代码文本

    # 返回单行代码的最终保守归一化结果。
    return str_normalized_code.strip()

# 当前行缩进回退判断保持独立，便于测试关键字边界。
def _dedents_before_line(lower_code: str) -> bool:
    """判断当前行输出前是否需要先减少缩进。

    参数:
        lower_code: 已转小写的当前行代码片段。

    返回:
        返回 True 表示当前行输出前应回退一级缩进。
    """

    # end/else 关键字在当前行开始前回退到外层缩进。
    return lower_code.startswith(("end", "endcase", "endgenerate", "else"))

# 当前行缩进增量判断只影响后续行。
def _indent_delta_after_line(lower_code: str) -> int:
    """计算当前行对后续行缩进层级的影响。

    参数:
        lower_code: 已转小写的当前行代码片段。

    返回:
        返回当前行对下一行缩进层级的非负增量。
    """

    # int_delta 累积当前行打开或关闭结构后的缩进变化。
    int_delta = 0  # 后续行缩进增量

    # begin/case/generate 打开一个结构块。
    if re.search(r"\b(begin|case|casez|casex|generate)\b", lower_code) and not lower_code.startswith("end"):

        # 下一行进入结构块内部，需要增加一级缩进。
        int_delta += 1  # 打开结构块后的缩进增量

    # 单独 end 行抵消同一行中可能出现的打开关键字。
    if lower_code.startswith("end ") or lower_code == "end":

        # 该函数只返回非负缩进变化，外部再做全局下限保护。
        int_delta = max(0, int_delta - 1)  # end 行修正后的缩进增量

    # 返回供主循环更新后续行缩进层级。
    return int_delta
