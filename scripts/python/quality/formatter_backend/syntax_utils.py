"""提供 Verilog formatter 共享的语法、表达式和命名工具。"""
from __future__ import annotations

# 正则用于解析 Verilog 片段、表达式 token 和语句边界。
import re

# strict mode 需要构造统一 formatter 异常。
from .models import VerilogFormatterError

# 语法工具 mixin 集中承载 formatter 后端共享的小型解析逻辑。
class SyntaxUtilsMixin:
    """提供表达式规范化、命名归一和语句解析辅助能力。"""

    # raw block 行首闭合关键字会影响当前行缩进。
    RAW_BLOCK_LEADING_CLOSE_PATTERN = re.compile(  # raw 行首闭合匹配
        r"^(?:endcase|endfunction|endtask|endgenerate|endmodule|endspecify|end)\b(?P<rest>.*)$"  # Verilog 块闭合关键字
    )

    # 单行 Verilog 文本按 `//` 分离代码和右侧注释。
    def _split_comment(self, entry: str) -> tuple[str, str]:
        """
        拆分一行 Verilog 中的代码片段和行注释。

        :param entry: 可能包含 `//` 注释的单行文本。
        :return: 代码文本和去掉外围空白后的注释文本。
        """

        # 没有行注释标记时，整行都属于代码部分。
        if "//" not in entry:

            # 空注释字符串让调用方沿用无注释路径。
            return entry, ""

        # 拆分一次即可保留注释正文中的额外 `//`。
        str_raw, str_comment = entry.split("//", 1)  # 代码片段和注释片段

        # 注释正文去掉两端空白，代码片段保持原有空白给上游处理。
        return str_raw, str_comment.strip()

    # 顶层切分只在括号深度为零时识别分隔符。
    def _split_top_level(self, text: str, delimiter: str) -> list[str]:
        """
        按顶层分隔符切分表达式或关联列表。

        :param text: 待切分的 Verilog 片段。
        :param delimiter: 只在括号外生效的单字符分隔符。
        :return: 去掉空项后的顶层片段列表。
        """

        # list_result 保存已经遇到完整顶层分隔符的片段。
        list_result: list[str] = []  # 顶层切分结果

        # list_current 累积当前片段的原始字符。
        list_current: list[str] = []  # 当前顶层片段字符

        # int_depth 跟踪圆括号、方括号和花括号的嵌套层级。
        int_depth = 0  # 当前括号深度

        # 逐字符扫描可以避免误切端口表达式中的逗号或分号。
        for str_char in text:

            # 左括号类字符让后续分隔符暂时失效。
            if str_char in "([{":

                # 进入更深层级后，只有配对闭合才会回到顶层。
                int_depth += 1  # 顶层切分括号深度增加

            # 右括号类字符结束一层嵌套。
            elif str_char in ")]}":

                # 保持原算法行为，不额外钳制异常负深度。
                int_depth -= 1  # 顶层切分括号深度减少

            # 当前字符是顶层分隔符时结束一个片段。
            if str_char == delimiter and int_depth == 0:

                # 完成的片段立即去除外围空白后入列。
                list_result.append("".join(list_current).strip())

                # 下一个片段从空字符缓存开始。
                list_current = []  # 下一个顶层片段字符缓存

            # 普通字符继续归入当前片段。
            else:

                # 保留字符原貌，后续规范化函数再处理空白。
                list_current.append(str_char)

        # 扫描结束后，最后一个片段没有尾随分隔符也要输出。
        if list_current:

            # 末尾片段同样去掉外围空白。
            list_result.append("".join(list_current).strip())

        # 空片段不参与调用方的参数、端口或 for 子句处理。
        return [item for item in list_result if item]

    # 命名前缀应用前先剥离 formatter 已管理的旧前缀。
    def _apply_prefix(self, name: str, prefix: str) -> str:
        """
        给信号基础名应用指定前缀。

        :param name: 原始信号名或已经带旧前缀的名称。
        :param prefix: 目标命名前缀。
        :return: 带目标前缀且不会重复前缀的信号名。
        """

        # str_stripped_name 是去掉已知前缀后的信号基础名。
        str_stripped_name = self._strip_known_prefixes(name)  # 前缀剥离后的信号名

        # 基础名已经以目标前缀开头时不重复追加。
        return f"{prefix}{str_stripped_name}" if not str_stripped_name.startswith(prefix) else str_stripped_name

    # 后缀驱动的信号分类规则来自 formatter 配置。
    def _signal_category_suffix_rules(self) -> tuple[tuple[tuple[str, ...], str], ...]:
        """
        返回计数器、标志、编码器和解码器后缀对应的前缀规则。

        :param: 无外部业务参数。
        :return: 后缀集合与目标前缀组成的不可变规则表。
        """

        # 调用方按顺序匹配，先命中的分类决定最终前缀。
        return (
            (("_counter", "_count", "_cnt"), self.config["naming"]["counter_prefix"]),
            (("_flag", "_flg"), self.config["naming"]["flag_prefix"]),
            (("_encode", "_enc"), self.config["naming"]["encoder_prefix"]),
            (("_decode", "_dec"), self.config["naming"]["decoder_prefix"]),
        )

    # 已管理分类前缀用于识别不应重复添加的信号名。
    def _managed_signal_category_prefixes(self) -> tuple[str, ...]:
        """
        返回 formatter 会自动管理的信号分类前缀。

        :param: 无外部业务参数。
        :return: 计数器、标志、编码器和解码器前缀。
        """

        # 这些前缀在剥离旧命名时需要被统一移除。
        return (
            self.config["naming"]["counter_prefix"],
            self.config["naming"]["flag_prefix"],
            self.config["naming"]["encoder_prefix"],
            self.config["naming"]["decoder_prefix"],
        )

    # 分类前缀检查用于避免把已有规范名再次重命名。
    def _has_managed_signal_category_prefix(self, name: str) -> bool:
        """
        判断信号名是否已经带有 formatter 管理的分类前缀。

        :param name: 待检查的信号名。
        :return: 已带管理前缀时返回 True。
        """

        # str_lower_name 统一大小写比较，兼容输入中的大小写差异。
        str_lower_name = name.lower()  # 小写信号名

        # 任一管理前缀命中即可视为已分类。
        return any(
            str_lower_name.startswith(str_prefix.lower())
            for str_prefix in self._managed_signal_category_prefixes()
        )

    # 后缀匹配把 legacy 命名转换为配置化分类前缀。
    def _match_signal_category_suffix(self, name: str) -> tuple[str, str] | None:
        """
        根据信号后缀判断需要追加的分类前缀。

        :param name: 原始信号名。
        :return: 命中时返回目标前缀和剥离后缀的基础名。
        """

        # str_stripped_name 先移除已有方向或分类前缀。
        str_stripped_name = self._strip_known_prefixes(name)  # 待匹配后缀的基础名

        # str_lower_name 用于大小写不敏感的后缀判断。
        str_lower_name = str_stripped_name.lower()  # 小写基础名

        # 分类规则按配置顺序扫描，保持旧 formatter 的优先级。
        for tuple_suffixes, str_prefix in self._signal_category_suffix_rules():

            # 同一分类可能接受多个历史后缀。
            for str_suffix in tuple_suffixes:

                # 未命中当前后缀时继续尝试同类其他后缀。
                if not str_lower_name.endswith(str_suffix):

                    # 当前后缀没有提供分类依据。
                    continue

                # str_base_name 是去掉语义后缀后的真实信号基础名。
                str_base_name = str_stripped_name[: -len(str_suffix)].rstrip("_")  # 分类后缀剥离后的名称

                # 只有后缀没有基础名时拒绝重命名。
                if not str_base_name:

                    # 空基础名无法组成合法信号名。
                    return None

                # 返回目标前缀和基础名，供调用方拼接新名称。
                return str_prefix, str_base_name

        # 没有分类后缀时不触发后缀驱动重命名。
        return None

    # 后缀驱动归一化只在明确命中分类后缀时生效。
    def _normalize_suffix_driven_signal_name(self, name: str) -> str | None:
        """
        将带语义后缀的信号名转换为配置化前缀命名。

        :param name: 原始信号名。
        :return: 命中后缀规则时返回新名称，否则返回 None。
        """

        # tuple_match 保存命中的目标前缀和基础名。
        tuple_match = self._match_signal_category_suffix(name)  # 后缀分类匹配结果

        # 没有分类后缀时交给其他命名规则处理。
        if tuple_match is None:

            # None 表示本函数没有产生改名建议。
            return None

        # 拆出前缀和基础名用于应用统一前缀逻辑。
        str_prefix, str_base_name = tuple_match  # 分类前缀和基础名

        # 复用前缀 helper，避免重复添加已存在的分类前缀。
        return self._apply_prefix(str_base_name, str_prefix)

    # 内部输出基础名优先使用后缀分类规则。
    def _normalize_internal_output_base_name(self, name: str) -> str:
        """
        归一化内部输出信号的基础名。

        :param name: 原始输出相关信号名。
        :return: 去除旧前缀后可继续追加输出后缀的基础名。
        """

        # str_category_name 保存后缀分类命中的规范名。
        str_category_name = self._normalize_suffix_driven_signal_name(name)  # 后缀驱动分类名

        # 后缀规则命中时优先保留其语义分类。
        if str_category_name is not None:

            # 分类名已经完成前缀处理，可以直接返回。
            return str_category_name

        # 无分类后缀时只剥离已知旧前缀。
        return self._strip_known_prefixes(name)

    # 输入端口命名需要特殊处理时钟和复位。
    def _normalize_input_port_name(self, base: str) -> str:
        """
        归一化输入端口基础名。

        :param base: 已剥离方向后缀和旧前缀的端口基础名。
        :return: 配置化的输入端口名称。
        """

        # str_lower_base 用于识别常见时钟和复位别名。
        str_lower_base = base.lower()  # 小写输入端口基础名

        # 常见 clock 别名统一映射为默认时钟名。
        if str_lower_base in {"clk", "clock"}:

            # 默认时钟名来自 reset_clock 配置。
            return self.config["reset_clock"]["default_clock"]

        # 低有效复位别名统一映射为默认复位名。
        if str_lower_base in {"rstn", "rst_n", "resetn", "reset_n", "aresetn", "areset_n", "arstn", "arst_n"}:

            # 配置默认复位通常携带低有效语义。
            return self.config["reset_clock"]["default_reset"]

        # 高有效复位别名使用高有效默认名。
        if str_lower_base in {"rst", "reset", "areset", "arst"}:

            # helper 按配置生成高有效复位名。
            return self._default_high_reset_name()

        # 普通输入端口使用配置化输入前缀。
        return self._apply_prefix(base, self.config["naming"]["input_prefix"])

    # 已知前缀剥离为所有命名归一化提供共同起点。
    def _strip_known_prefixes(self, name: str) -> str:
        """
        循环剥离 formatter 管理的方向和分类前缀。

        :param name: 原始信号或端口名。
        :return: 去掉已知前缀和内部输出后缀后的基础名。
        """

        # list_known_prefixes 按配置列出所有可重复剥离的前缀。
        list_known_prefixes = [
            self.config["naming"]["input_prefix"],  # 输入端口前缀
            self.config["naming"]["output_prefix"],  # 输出端口前缀
            self.config["naming"]["inout_prefix"],  # 双向端口前缀
            self.config["naming"]["parameter_prefix"],  # 参数前缀
            self.config["naming"]["state_prefix"],  # 状态枚举前缀
            self.config["naming"]["register_prefix"],  # 寄存器前缀
            self.config["naming"]["counter_prefix"],  # 计数器前缀
            self.config["naming"]["state_signal_prefix"],  # 状态信号前缀
            self.config["naming"]["encoder_prefix"],  # 编码器前缀
            self.config["naming"]["decoder_prefix"],  # 解码器前缀
            self.config["naming"]["flag_prefix"],  # 标志信号前缀
        ]  # 可剥离命名前缀表

        # str_working_name 在循环剥离过程中持续更新。
        str_working_name = name  # 当前待剥离名称

        # bool_changed 控制多轮剥离，处理叠加前缀。
        bool_changed = True  # 本轮是否剥离过前缀

        # 只要本轮剥掉过前缀，就继续尝试下一轮。
        while bool_changed:

            # 每轮开始先假定没有变化。
            bool_changed = False  # 本轮尚未剥离前缀

            # 逐个尝试配置中的已知前缀。
            for str_prefix in list_known_prefixes:

                # 命中前缀时从当前名称头部移除。
                if str_working_name.startswith(str_prefix):

                    # 保留剩余部分给下一轮继续判断。
                    str_working_name = str_working_name[len(str_prefix) :]  # 移除一个已知前缀后的名称

                    # 标记本轮发生变化，外层 while 需要继续。
                    bool_changed = True  # 触发下一轮前缀检查

        # 内部输出后缀属于生成器私有命名，也应从基础名中剥离。
        if str_working_name.endswith(self.config["naming"]["internal_output_suffix"]):

            # 去掉内部输出后缀，恢复用户语义基础名。
            str_working_name = str_working_name[: -len(self.config["naming"]["internal_output_suffix"])]  # 剥离内部输出后缀

        # 返回可供后续添加方向或分类前缀的基础名。
        return str_working_name

    # 端口方向短后缀 `_i` 和 `_o` 只在末尾出现时剥离。
    def _strip_terminal_port_direction_suffix(self, name: str) -> str:
        """
        移除端口名末尾的 `_i` 或 `_o` 方向后缀。

        :param name: 原始端口名。
        :return: 去掉末尾方向后缀后的端口名。
        """

        # 只有长度足够的名称才可能安全移除两字符后缀。
        if len(name) > 2 and (name.endswith("_i") or name.endswith("_o")):

            # 返回去掉方向后缀的端口基础名。
            return name[:-2]

        # 无方向后缀时保持原名。
        return name

    # 端口基础名先剥离配置前缀，再剥离末尾方向后缀。
    def _normalize_port_base_name(self, name: str) -> str:
        """
        归一化端口名的基础部分。

        :param name: 原始端口名。
        :return: 可重新套用方向前缀的基础名。
        """

        # 前缀剥离结果再进入 `_i/_o` 后缀剥离。
        return self._strip_terminal_port_direction_suffix(self._strip_known_prefixes(name))

    # always 头部规范化只处理空白和 begin 拼接样式。
    def _normalize_always_header(self, header: str) -> str:
        """
        规范化 always 头部的敏感列表和 begin 空白。

        :param header: 原始 always 头部文本。
        :return: 统一空白后的 always 头部文本。
        """

        # str_normalized 保留原始头部语义，只收敛格式差异。
        str_normalized = header.replace("always@", "always@")  # always 头部工作文本

        # 星号敏感列表统一写为 always@(*)。
        str_normalized = re.sub(r"always\s*@\s*\*", "always@(*)", str_normalized)  # 星号敏感列表

        # 带括号敏感列表去掉 @ 与左括号之间的空白。
        str_normalized = re.sub(r"always\s*@\s*\(", "always@(", str_normalized)  # 括号敏感列表

        # begin 紧跟右括号，保持 formatter 既有输出样式。
        str_normalized = str_normalized.replace(") begin", ")begin")  # always begin 拼接文本

        # 返回规范化后的 always 头部。
        return str_normalized

    # begin 头部识别同时接受命名块写法。
    def _is_begin_header(self, text: str) -> bool:
        """
        判断文本是否为 begin 或带 label 的 begin 头部。

        :param text: 待检查的语句行。
        :return: 是 begin 头部时返回 True。
        """

        # str_code_part 去掉右侧注释后再判断 begin 结构。
        str_code_part, _ = self._split_comment(text.strip())  # 去注释后的 begin 候选

        # str_stripped_code 去除外围空白以兼容缩进行。
        str_stripped_code = str_code_part.strip()  # begin 候选语句

        # 三种 begin 形式都表示打开一个显式块。
        return (
            str_stripped_code == "begin"
            or str_stripped_code.startswith("begin:")
            or str_stripped_code.startswith("begin :")
        )

    # 表达式空白规范化先修复数字字面量，再走 token 级格式化。
    def _normalize_expression_spacing(self, text: str) -> str:
        """
        规范化 Verilog 表达式中的空白。

        :param text: 原始表达式文本。
        :return: token 级空白规范化后的表达式。
        """

        # str_expression 先修复基数数字字面量内部的空白。
        str_expression = self._normalize_based_number_spacing(text.strip())  # 数字字面量预处理后的表达式

        # 空表达式直接返回，避免 tokenizer 产生无意义 token。
        if not str_expression:

            # 保持调用方传入的空表达式语义。
            return str_expression

        # list_tokens 保存表达式 tokenizer 识别出的最小语法片段。
        list_tokens = self._tokenize_expression_segment(str_expression)  # 表达式 token 序列

        # token 序列交给统一 formatter 决定运算符周围空白。
        return self._normalize_expression_tokens(list_tokens)

    # 基数数字字面量中的空格会破坏 Verilog 词法，需要先压紧。
    def _normalize_based_number_spacing(self, text: str) -> str:
        """
        去除 Verilog 基数数字字面量内部的非法空白。

        :param text: 原始表达式文本。
        :return: 修复 size、base 和 digits 间空白后的文本。
        """

        # 替换时只删除字面量内部空白，不影响表达式其他位置。
        return re.sub(
            r"(?P<size>\d+)\s*'\s*(?P<base>[sS]?[bBoOdDhH])\s*(?P<digits>[0-9a-fA-F_xXzZ?]+)",
            lambda match: (
                f"{match.group('size')}'"
                f"{match.group('base')}"
                f"{match.group('digits')}"
            ),
            text,
        )

    # 声明规格只规范化方括号范围，不处理 direction 或 signed。
    def _normalize_decl_spec_spacing(self, text: str) -> str:
        """
        规范化声明规格中的 packed range 空白。

        :param text: 声明规格文本。
        :return: range 内部表达式规范化后的声明规格。
        """

        # 空规格表示声明没有位宽，直接保持为空。
        if not text:

            # 调用方据此省略位宽片段。
            return text

        # 每个方括号范围独立规范化，避免影响其他声明关键字。
        return re.sub(r"\[[^\]]+\]", lambda match: self._normalize_decl_range_token(match.group(0)), text)

    # 参数声明规格需要在非 range 末尾保留一个分隔空格。
    def _format_param_decl_spec(self, decl_spec: str) -> str:
        """
        格式化 parameter/localparam 的声明规格。

        :param decl_spec: 参数类型、signed 或位宽片段。
        :return: 可直接拼接参数名的声明规格文本。
        """

        # str_decl_spec_value 保存 range 规范化后的声明规格。
        str_decl_spec_value = self._normalize_decl_spec_spacing(decl_spec)  # 参数声明规格文本

        # 没有声明规格时不插入多余空格。
        if not str_decl_spec_value:

            # 空字符串让调用方直接输出参数名。
            return ""

        # 位宽 range 已经以 `]` 结尾时后续拼接逻辑会处理间隔。
        return str_decl_spec_value if str_decl_spec_value.endswith("]") else f"{str_decl_spec_value} "

    # 单个 packed range token 只在确认方括号完整时处理。
    def _normalize_decl_range_token(self, text: str) -> str:
        """
        规范化 `[left:right]` 或单表达式 range token。

        :param text: 含方括号的声明 range 文本。
        :return: range 内表达式空白规范化后的文本。
        """

        # 非完整方括号 token 保持原样，避免误改其他语法。
        if not text.startswith("[") or not text.endswith("]"):

            # 调用方仍可把原 token 拼回声明。
            return text

        # str_expression 是去掉方括号后的 range 内部文本。
        str_expression = text[1:-1].strip()  # range 内部表达式

        # 空 range 不是合法声明，但 formatter 不在这里修复语义。
        if not str_expression:

            # 保守返回原 token。
            return text

        # int_colon_index 定位 range 内顶层冒号。
        int_colon_index = self._find_top_level_colon(str_expression)  # 顶层冒号位置

        # 没有顶层冒号时按单表达式 range 处理。
        if int_colon_index == -1:

            # 单表达式仍需要走表达式空白规范化。
            return f"[{self._normalize_expression_spacing(str_expression)}]"

        # str_left_range 是冒号左侧的高位或基址表达式。
        str_left_range = self._normalize_expression_spacing(str_expression[:int_colon_index].strip())  # range 左表达式

        # str_right_range 是冒号右侧的低位或宽度表达式。
        str_right_range = self._normalize_expression_spacing(str_expression[int_colon_index + 1 :].strip())  # range 右表达式

        # packed range 输出不在冒号两侧保留空格。
        return f"[{str_left_range}:{str_right_range}]"

    # indexed part-select 只在顶层识别 `+:` 或 `-:`。
    def _find_top_level_indexed_part_select(self, text: str) -> tuple[int, str] | None:
        """
        查找表达式顶层的 indexed part-select 操作符。

        :param text: 方括号内部的表达式文本。
        :return: 命中时返回操作符位置和操作符文本。
        """

        # int_depth 跟踪括号层级，只有零深度才允许识别 part-select。
        int_depth = 0  # 当前扫描括号深度

        # bool_in_string 避免字符串字面量中的 `+:` 被当成操作符。
        bool_in_string = False  # 是否处在字符串字面量中

        # int_index 指向当前扫描字符。
        int_index = 0  # 当前扫描下标

        # 至少剩两个字符时才可能匹配 `+:` 或 `-:`。
        while int_index < len(text) - 1:

            # str_char 是当前扫描字符。
            str_char = text[int_index]  # 当前扫描字符

            # 未转义双引号会切换字符串扫描状态。
            if self._is_unescaped_double_quote(text, int_index):

                # 字符串状态翻转后继续看下一个字符。
                bool_in_string = not bool_in_string  # 字符串扫描状态翻转

                # 引号本身不参与括号深度或操作符匹配。
                int_index += 1  # 越过当前引号后的下标

                # 继续扫描字符串状态变化后的字符。
                continue

            # 字符串内部内容全部跳过。
            if bool_in_string:

                # 字符串字符不影响 Verilog 表达式层级。
                int_index += 1  # 字符串内部下一个字符下标

                # 继续扫描后续字符。
                continue

            # 分隔符 helper 统一维护圆、方和花括号深度。
            int_depth = self._advance_group_depth(str_char, int_depth)  # 当前字符处理后的表达式层级

            # 顶层两字符候选才可能是 indexed part-select。
            str_operator = text[int_index : int_index + 2]  # indexed part-select 候选操作符

            # 顶层 `+:` 或 `-:` 表示 indexed part-select。
            if int_depth == 0 and str_operator in {"+:", "-:"}:

                # 返回位置和操作符，调用方据此拆分左右表达式。
                return int_index, str_operator

            # 当前字符处理完成，进入下一字符。
            int_index += 1  # part-select 扫描下一字符下标

        # 扫描完整个文本仍未找到顶层 indexed part-select。
        return None

    # 引号 helper 判断当前位置是否打开或关闭字符串范围。
    def _is_unescaped_double_quote(self, text: str, int_index: int) -> bool:
        """判断当前字符是否为未转义双引号。

        :param text: 当前完整表达式文本。
        :param int_index: 待判断字符下标。
        :return: 当前字符可切换字符串状态时返回 True。
        """

        # 非双引号字符不会改变字符串扫描状态。
        if text[int_index] != '"':

            # 调用方可直接继续普通结构扫描。
            return False

        # 起始引号或前一字符非反斜杠时属于未转义引号。
        return int_index == 0 or text[int_index - 1] != "\\"

    # 分隔符深度 helper 统一处理圆、方和花括号层级。
    def _advance_group_depth(self, str_char: str, int_depth: int) -> int:
        """根据单个分隔符字符推进表达式嵌套深度。

        :param str_char: 当前扫描字符。
        :param int_depth: 当前表达式嵌套深度。
        :return: 当前字符处理后的非负嵌套深度。
        """

        # 左分隔符打开一层嵌套表达式。
        if str_char in "([{":

            # 深层范围中的操作符不属于调用方顶层。
            return int_depth + 1

        # 右分隔符关闭最近一层嵌套表达式。
        if str_char in ")]}":

            # 异常多出的右分隔符保持零深度下限。
            return max(0, int_depth - 1)

        # 普通字符不改变表达式层级。
        return int_depth

    # part-select 左侧若包含二元算术，紧凑化会损害可读性。
    def _has_obvious_arithmetic_operator(self, text: str) -> bool:
        """
        判断表达式中是否存在明确的二元算术操作符。

        :param text: 待检查的表达式文本。
        :return: 存在非一元算术操作时返回 True。
        """

        # list_tokens 用于区分一元正负号和二元算术操作。
        list_tokens = self._tokenize_expression_segment(text.strip())  # 算术符号判定 token

        # str_previous_symbol 保存前一个 token，辅助判断当前符号是否为一元。
        str_previous_symbol: str | None = None  # 前一个表达式 token

        # set_arithmetic_ops 只包含会影响 part-select 紧凑策略的算术符号。
        set_arithmetic_ops = {"+", "-", "*", "/", "%", "<<", ">>"}  # 二元算术候选符号

        # set_operator_context 表示后续正负号可被视为一元符号的位置。
        set_operator_context = {
            "(",  # 子表达式起点
            "{",  # 拼接表达式起点
            ",",  # 参数或拼接分隔
            "?",  # 三目条件起点
            ":",  # 三目分支起点
            "=",  # 赋值右侧起点
            "==",  # 比较右侧起点
            "!=",  # 不等比较的右操作数起点
            "===",  # case equality 右侧起点
            "!==",  # 全等不等比较右操作数起点
            "<=",  # 比较或非阻塞后续起点
            ">=",  # 大于等于比较右操作数起点
            "<<",  # 移位右侧起点
            ">>",  # 右移表达式右操作数起点
            "+",  # 加法右侧起点
            "-",  # 减法右侧起点
            "*",  # 乘法右侧起点
            "/",  # 除法右侧起点
            "%",  # 取模右侧起点
            "&",  # 按位与右侧起点
            "|",  # 按位或右侧起点
            "^",  # 异或右侧起点
            "<",  # 小于比较右操作数起点
            ">",  # 大于比较右操作数起点
            "&&",  # 逻辑与右侧起点
            "||",  # 逻辑或右侧起点
        }  # 一元符号上下文集合

        # 按 token 顺序判断算术符号是否为二元操作。
        for str_token in list_tokens:

            # 只有候选算术符号需要进一步区分一元或二元。
            if str_token in set_arithmetic_ops:

                # bool_unary 表示当前正负号处在表达式起点或操作符之后。
                bool_unary = str_token in {"+", "-"} and (  # 算术符号一元判定
                    str_previous_symbol is None  # 表达式开头的正负号
                    or str_previous_symbol in set_operator_context  # 操作符后的正负号
                )  # 当前符号是否为一元正负号

                # 非一元算术操作会让 part-select 左侧保持带空格格式。
                if not bool_unary:

                    # 调用方据此避免把复杂算术压成紧凑格式。
                    return True

            # 当前 token 成为下一轮的一元上下文依据。
            str_previous_symbol = str_token  # 算术扫描上一 token

        # 没有发现明确二元算术操作。
        return False

    # 方括号 token 根据内容选择紧凑或表达式规范化形式。
    def _compact_expression_bracket_token(self, text: str) -> str:
        """
        规范化单个方括号表达式 token。

        :param text: tokenizer 保留的完整方括号 token。
        :return: 适合声明 range 或 part-select 的方括号文本。
        """

        # 非方括号 token 不属于本函数处理范围。
        if not text.startswith("[") or not text.endswith("]"):

            # 调用方继续按普通 token 处理。
            return text

        # str_expression 是方括号内部表达式。
        str_expression = text[1:-1].strip()  # 方括号内部文本

        # 空方括号保持原样，避免隐藏上游语法异常。
        if not str_expression:

            # 保守返回原始 token。
            return text

        # tuple_indexed_part_select 定位顶层 `+:` 或 `-:` 操作符。
        tuple_indexed_part_select = self._find_top_level_indexed_part_select(str_expression)  # 顶层 part-select 定位结果

        # indexed part-select 需要按左右表达式分别处理。
        if tuple_indexed_part_select is not None:

            # 拆出操作符位置和操作符文本。
            int_operator_index, str_operator = tuple_indexed_part_select  # part-select 操作符位置和值

            # str_left_expression 是基址表达式。
            str_left_expression = str_expression[:int_operator_index].strip()  # part-select 基址表达式

            # str_right_expression 是宽度表达式。
            str_right_expression = str_expression[int_operator_index + len(str_operator) :].strip()  # part-select 宽度表达式

            # 左右任一侧缺失时仅压缩空白，不尝试推断语义。
            if not str_left_expression or not str_right_expression:

                # str_compact_expression 保留原 token 内容但移除所有空白。
                str_compact_expression = re.sub(r"\s+", "", str_expression)  # 非完整 part-select 紧凑文本

                # 返回紧凑方括号 token。
                return f"[{str_compact_expression}]"

            # 简单基址表达式使用传统紧凑 part-select 样式。
            if not self._has_obvious_arithmetic_operator(str_left_expression):

                # str_compact_left 去除基址表达式内部空白。
                str_compact_left = re.sub(r"\s+", "", str_left_expression)  # 紧凑 part-select 基址

                # str_compact_right 去除宽度表达式内部空白。
                str_compact_right = re.sub(r"\s+", "", str_right_expression)  # 紧凑 part-select 宽度

                # 紧凑 indexed part-select 不在操作符两侧保留空格。
                return f"[{str_compact_left}{str_operator}{str_compact_right}]"

            # 复杂基址表达式保留运算符空白以避免可读性下降。
            str_normalized_left = self._normalize_expression_spacing(str_left_expression)  # 规范化 part-select 基址

            # 宽度表达式同样走普通表达式规范化。
            str_normalized_right = self._normalize_expression_spacing(str_right_expression)  # 规范化 part-select 宽度

            # 复杂 indexed part-select 在操作符两侧保留空格。
            return f"[{str_normalized_left} {str_operator} {str_normalized_right}]"

        # int_colon_index 定位普通 range 的顶层冒号。
        int_colon_index = self._find_top_level_colon(str_expression)  # 方括号顶层冒号位置

        # 没有冒号时按简单下标或单表达式处理。
        if int_colon_index == -1:

            # str_compact_expression 去除下标表达式内部空白。
            str_compact_expression = re.sub(r"\s+", "", str_expression)  # 紧凑下标表达式

            # 单下标 token 使用紧凑形式。
            return f"[{str_compact_expression}]"

        # str_left_range 是冒号左侧表达式。
        str_left_range = self._normalize_expression_spacing(str_expression[:int_colon_index].strip())  # 方括号左 range

        # str_right_range 是冒号右侧表达式。
        str_right_range = self._normalize_expression_spacing(str_expression[int_colon_index + 1 :].strip())  # 方括号右 range

        # 普通 range 冒号两侧不保留空格。
        return f"[{str_left_range}:{str_right_range}]"

    # 方括号 token 需要整体保留，避免 range 内部被普通运算符规则拆散。
    def _read_bracket_token_end(self, text: str, int_start_index: int) -> int:
        """
        读取从 `[` 开始的完整方括号 token 结束位置。

        :param text: 表达式全文。
        :param int_start_index: 左方括号所在下标。
        :return: 方括号 token 的右开结束下标。
        """

        # int_depth 从首个左方括号开始计数。
        int_depth = 1  # 方括号嵌套深度

        # int_close_index 指向待检查字符。
        int_close_index = int_start_index + 1  # 方括号扫描下标

        # 扫描到深度归零或文本结束为止。
        while int_close_index < len(text) and int_depth > 0:

            # 左方括号表示进入嵌套 range。
            if text[int_close_index] == "[":

                # 嵌套深度增加后需要更多右方括号闭合。
                int_depth += 1  # 方括号嵌套进入

            # 右方括号闭合当前 range 层级。
            elif text[int_close_index] == "]":

                # 深度归零后当前字符也属于 token。
                int_depth -= 1  # 方括号嵌套退出

            # 继续检查下一个字符。
            int_close_index += 1  # 方括号扫描推进

        # 返回右开边界，未闭合时自然落到文本末尾。
        return int_close_index

    # 字符串 token 保留引号和内部内容。
    def _read_string_token_end(self, text: str, int_start_index: int) -> int:
        """
        读取双引号字符串 token 的结束位置。

        :param text: 表达式全文。
        :param int_start_index: 起始双引号下标。
        :return: 字符串 token 的右开结束下标。
        """

        # int_close_index 从起始引号之后开始找闭合引号。
        int_close_index = int_start_index + 1  # 字符串扫描下标

        # 扫描直到遇到未转义双引号或文本结束。
        while int_close_index < len(text):

            # 未转义双引号闭合当前字符串 token。
            if text[int_close_index] == '"' and text[int_close_index - 1] != "\\":

                # 右边界需要包含闭合引号本身。
                int_close_index += 1  # 字符串闭合后的右开下标

                # 找到闭合引号后停止扫描。
                break

            # 普通字符串字符继续向后扫描。
            int_close_index += 1  # 字符串扫描推进

        # 返回字符串 token 的右开边界。
        return int_close_index

    # 宏 token 由反引号和后续标识符字符组成。
    def _read_macro_token_end(self, text: str, int_start_index: int) -> int:
        """
        读取 Verilog 宏引用 token 的结束位置。

        :param text: 表达式全文。
        :param int_start_index: 反引号所在下标。
        :return: 宏 token 的右开结束下标。
        """

        # int_close_index 从反引号后的第一个字符开始。
        int_close_index = int_start_index + 1  # 宏名扫描下标

        # 宏名接受字母数字、下划线和美元符号。
        while int_close_index < len(text) and (text[int_close_index].isalnum() or text[int_close_index] in "_$"):

            # 当前字符仍属于宏名。
            int_close_index += 1  # 宏名扫描推进

        # 返回宏引用 token 的右开边界。
        return int_close_index

    # 普通标识符和系统函数名共享同一字符集合。
    def _read_identifier_token_end(self, text: str, int_start_index: int) -> int:
        """
        读取标识符 token 的结束位置。

        :param text: 表达式全文。
        :param int_start_index: 标识符起始下标。
        :return: 标识符 token 的右开结束下标。
        """

        # int_close_index 从首字符之后继续扫描。
        int_close_index = int_start_index + 1  # 标识符扫描下标

        # Verilog 标识符后续字符接受字母数字、下划线和美元符号。
        while int_close_index < len(text) and (text[int_close_index].isalnum() or text[int_close_index] in "_$"):

            # 当前字符仍属于标识符。
            int_close_index += 1  # 标识符扫描推进

        # 返回标识符 token 的右开边界。
        return int_close_index

    # 数字 token 保留基数、未知态和下划线。
    def _read_number_token_end(self, text: str, int_start_index: int) -> int:
        """
        读取 Verilog 数字字面量 token 的结束位置。

        :param text: 表达式全文。
        :param int_start_index: 数字首字符下标。
        :return: 数字 token 的右开结束下标。
        """

        # int_close_index 从首位数字之后开始扫描。
        int_close_index = int_start_index + 1  # 数字字面量扫描下标

        # Verilog 数字可包含基数标记、未知态、问号和下划线。
        while int_close_index < len(text) and re.fullmatch(r"[A-Za-z0-9_'xXzZ?]", text[int_close_index]):

            # 当前字符仍属于数字字面量。
            int_close_index += 1  # 数字字面量扫描推进

        # 返回数字 token 的右开边界。
        return int_close_index

    # 多字符运算符必须先于单字符运算符匹配。
    def _match_multi_char_operator(
        self,
        text: str,
        int_start_index: int,
        tuple_multi_char_ops: tuple[str, ...],
    ) -> str | None:
        """
        尝试从当前位置匹配多字符 Verilog 运算符。

        :param text: 表达式全文。
        :param int_start_index: 当前扫描下标。
        :param tuple_multi_char_ops: 按优先级排列的多字符运算符。
        :return: 命中的运算符文本，未命中时返回 None。
        """

        # 按传入顺序匹配，保证三字符运算符优先于两字符运算符。
        for str_operator in tuple_multi_char_ops:

            # 当前位置以该运算符开头即视为命中。
            if text.startswith(str_operator, int_start_index):

                # 返回命中的完整运算符 token。
                return str_operator

        # 没有多字符运算符从当前位置开始。
        return None

    # 表达式 tokenizer 保留 Verilog 字面量、宏、括号 token 和多字符运算符。
    def _tokenize_expression_segment(self, text: str) -> list[str]:
        """
        将表达式文本切分为 formatter 可处理的 token 序列。

        :param text: 原始表达式文本。
        :return: 保留 Verilog 词法边界的 token 列表。
        """

        # list_tokens 收集按原表达式顺序产生的 token。
        list_tokens: list[str] = []  # 表达式 token 列表

        # int_index 指向 tokenizer 主循环的当前字符。
        int_index = 0  # tokenizer 当前下标

        # tuple_multi_char_ops 按最长优先列出需要整体保留的运算符。
        tuple_multi_char_ops = ("===", "!==", "<=", ">=", "==", "!=", "&&", "||", "<<", ">>", "+:", "-:")  # 多字符运算符

        # set_single_char_ops 是可独立成 token 的单字符运算符。
        set_single_char_ops = set("=+-*/%&|^<>?:~!")  # 单字符运算符集合

        # set_punctuation 保存表达式中的分组和分隔符。
        set_punctuation = set("(){}.,;")  # 表达式标点集合

        # 主循环每次消费一个完整 token。
        while int_index < len(text):

            # str_char 是 tokenizer 本轮检查的字符。
            str_char = text[int_index]  # tokenizer 当前字符

            # 空白只承担分隔作用，不进入 token 序列。
            if str_char.isspace():

                # 跳过空白后继续扫描下一个 token。
                int_index += 1  # 空白后的扫描下标

                # 空白不生成 token。
                continue

            # 方括号 range 或 part-select 作为整体 token 保留。
            if str_char == "[":

                # int_close_index 是方括号 token 的右开边界。
                int_close_index = self._read_bracket_token_end(text, int_index)  # 方括号 token 结束下标

                # 追加完整方括号片段，内部稍后单独规范化。
                list_tokens.append(text[int_index:int_close_index])

                # 下一轮从方括号 token 后继续。
                int_index = int_close_index  # 方括号 token 后的扫描下标

                # 方括号 token 已消费。
                continue

            # 字符串字面量作为整体 token 保留。
            if str_char == '"':

                # int_close_index 是字符串 token 的右开边界。
                int_close_index = self._read_string_token_end(text, int_index)  # 字符串 token 结束下标

                # 追加包含引号的字符串 token。
                list_tokens.append(text[int_index:int_close_index])

                # 下一轮从字符串 token 后继续。
                int_index = int_close_index  # 字符串 token 后的扫描下标

                # 字符串 token 已消费。
                continue

            # 反引号宏作为整体 token 保留。
            if str_char == "`":

                # int_close_index 是宏 token 的右开边界。
                int_close_index = self._read_macro_token_end(text, int_index)  # 宏 token 结束下标

                # 追加完整宏名。
                list_tokens.append(text[int_index:int_close_index])

                # 下一轮从宏 token 后继续。
                int_index = int_close_index  # 宏 token 后的扫描下标

                # 宏 token 已消费。
                continue

            # 标识符或系统函数名按连续标识符字符切分。
            if str_char.isalpha() or str_char in "_$":

                # int_close_index 是标识符 token 的右开边界。
                int_close_index = self._read_identifier_token_end(text, int_index)  # 标识符 token 结束下标

                # 追加完整标识符。
                list_tokens.append(text[int_index:int_close_index])

                # 下一轮从标识符后继续。
                int_index = int_close_index  # 标识符 token 后的扫描下标

                # 标识符 token 已消费。
                continue

            # 数字字面量保留基数和未知态字符。
            if str_char.isdigit():

                # int_close_index 是数字 token 的右开边界。
                int_close_index = self._read_number_token_end(text, int_index)  # 数字 token 结束下标

                # 追加完整数字字面量。
                list_tokens.append(text[int_index:int_close_index])

                # 下一轮从数字 token 后继续。
                int_index = int_close_index  # 数字 token 后的扫描下标

                # 数字 token 已消费。
                continue

            # 多字符运算符优先匹配，避免被拆成单字符。
            str_operator = self._match_multi_char_operator(text, int_index, tuple_multi_char_ops)  # 多字符运算符候选

            # 命中多字符运算符时整体追加。
            if str_operator is not None:

                # 追加完整运算符 token。
                list_tokens.append(str_operator)

                # 扫描下标跨过整个运算符。
                int_index += len(str_operator)  # 多字符运算符后的扫描下标

                # 多字符运算符已消费。
                continue

            # 单字符标点或运算符直接作为一个 token。
            if str_char in set_punctuation or str_char in set_single_char_ops:

                # 追加单字符 token。
                list_tokens.append(str_char)

                # 进入下一个字符。
                int_index += 1  # 单字符 token 后的扫描下标

                # 单字符 token 已消费。
                continue

            # 未识别字符保守作为单字符 token 保留。
            list_tokens.append(str_char)

            # 未识别字符只消费当前一个字符。
            int_index += 1  # 未识别 token 后的扫描下标

        # 返回完整 token 序列。
        return list_tokens

    # 表达式拼接前经常需要去掉刚追加的尾随空格。
    def _trim_expression_parts_space(self, list_parts: list[str]) -> None:
        """
        原地移除表达式输出片段末尾的空格。

        :param list_parts: 正在累积的表达式输出片段。
        :return: 无返回值，直接修改 list_parts。
        """

        # 只清理 formatter 主动插入的空格片段。
        while list_parts and list_parts[-1] == " ":

            # 删除尾部空格，避免逗号、分号或右括号前出现空白。
            list_parts.pop()

    # 逗号和分号需要先清理前导空格再决定是否补后置空格。
    def _append_expression_separator(
        self,
        list_parts: list[str],
        str_token: str,
        str_upcoming_symbol: str | None,
    ) -> None:
        """
        向表达式输出片段追加逗号或分号。

        :param list_parts: 正在累积的表达式输出片段。
        :param str_token: 当前分隔符 token。
        :param str_upcoming_symbol: 下一个 token，可能为 None。
        :return: 无返回值，结果直接追加到 list_parts。
        """

        # 分隔符前不保留 formatter 插入的空格。
        self._trim_expression_parts_space(list_parts)

        # 当前分隔符按原 token 追加。
        list_parts.append(str_token)

        # 逗号后如果还跟普通表达式，需要保留一个空格。
        if str_token == "," and str_upcoming_symbol not in {None, ")", "}", ";"}:

            # 空格隔开后续参数或拼接元素。
            list_parts.append(" ")

    # 右括号和点号都会吃掉前一个多余空格。
    def _append_expression_tight_token(self, list_parts: list[str], str_token: str) -> None:
        """
        追加不能带前置空格的紧贴 token。

        :param list_parts: 正在累积的表达式输出片段。
        :param str_token: 右括号、右花括号或点号。
        :return: 无返回值，结果直接追加到 list_parts。
        """

        # 右侧闭合符和成员访问点前不允许保留空格。
        self._trim_expression_parts_space(list_parts)

        # 追加当前紧贴 token。
        list_parts.append(str_token)

    # 一元操作符只在表达式起点或操作符之后成立。
    def _is_expression_unary_operator(self, str_token: str, str_previous_symbol: str | None) -> bool:
        """
        判断当前运算符是否应按一元运算符格式化。

        :param str_token: 当前运算符 token。
        :param str_previous_symbol: 前一个 token，可能为 None。
        :return: 当前 token 是一元运算符时返回 True。
        """

        # 只有这些符号在 Verilog 表达式中可能作为一元运算符。
        if str_token not in {"~", "!", "+", "-", "&", "|", "^"}:

            # 其他运算符都按二元或三目操作处理。
            return False

        # 表达式开头的一元符号紧贴后续操作数。
        if str_previous_symbol is None:

            # 没有前驱 token 时当前符号只能解释为一元。
            return True

        # 操作符或开分组之后的一元符号不应插入前置空格。
        return str_previous_symbol in {
            "(", "{", ",", "?", ":", "=", "==", "!=", "===", "!==", "<=", ">=", "<<", ">>",
            "+", "-", "*", "/", "%", "&", "|", "^", "<", ">", "&&", "||",
        }

    # 表达式运算符负责在二元操作两侧补空格。
    def _append_expression_operator(
        self,
        list_parts: list[str],
        str_token: str,
        str_previous_symbol: str | None,
        str_upcoming_symbol: str | None,
    ) -> bool:
        """
        追加表达式运算符并返回它是否按一元处理。

        :param list_parts: 正在累积的表达式输出片段。
        :param str_token: 当前运算符 token。
        :param str_previous_symbol: 前一个 token，可能为 None。
        :param str_upcoming_symbol: 下一个 token，可能为 None。
        :return: 当前运算符按一元格式化时返回 True。
        """

        # bool_unary 标记当前符号是否紧贴后续操作数。
        bool_unary = self._is_expression_unary_operator(str_token, str_previous_symbol)  # 当前运算符一元状态

        # 一元运算符在表达式起点或开括号后不保留前置空格。
        if bool_unary:

            # 表达式起点附近清理可能存在的空格。
            if str_previous_symbol in {None, "(", "{"}:

                # 删除 formatter 早先追加的空格。
                self._trim_expression_parts_space(list_parts)

            # 一元符号直接贴近操作数。
            list_parts.append(str_token)

        # 二元和三目相关运算符两侧按普通表达式空白处理。
        else:

            # 运算符前只保留一个空格。
            self._trim_expression_parts_space(list_parts)

            # 前面已有内容时补一个操作符前空格。
            if list_parts:

                # 让二元运算符与左操作数分隔。
                list_parts.append(" ")

            # 追加当前运算符。
            list_parts.append(str_token)

            # 后续不是闭合符、分隔符或结尾时补一个操作符后空格。
            if str_upcoming_symbol not in {None, ")", "}", ";", ","}:

                # 让右操作数和当前运算符保持一个空格。
                list_parts.append(" ")

        # 调用方需要记住该 token 是否一元，影响下一个普通 atom 的空格。
        return bool_unary

    # 普通 atom 根据前一个 token 决定是否补空格。
    def _append_expression_atom(self, list_parts: list[str], str_token: str, bool_previous_unary: bool) -> None:
        """
        追加普通表达式 atom。

        :param list_parts: 正在累积的表达式输出片段。
        :param str_token: 当前普通 token。
        :param bool_previous_unary: 前一个 token 是否是一元运算符。
        :return: 无返回值，结果直接追加到 list_parts。
        """

        # 普通 atom 前如果已有相邻 atom，需要补一个空格。
        if list_parts:

            # 开分组、成员访问点和一元符号之后不补空格。
            if list_parts[-1] not in {" ", "(", "{", "."} and not bool_previous_unary:

                # 插入普通 token 之间的分隔空格。
                list_parts.append(" ")

        # 追加当前普通 token。
        list_parts.append(str_token)

    # 运算符 token 集中判断，避免主格式化循环堆积分支。
    def _is_expression_operator_token(self, str_token: str) -> bool:
        """
        判断 token 是否属于表达式运算符。

        :param str_token: 待检查 token。
        :return: token 是运算符时返回 True。
        """

        # 这些 token 需要按一元、二元或三目运算符规则处理。
        return str_token in {
            "===", "!==", "<=", ">=", "==", "!=", "&&", "||", "<<", ">>", "+:", "-:",
            "=", "+", "-", "*", "/", "%", "&", "|", "^", "<", ">", "?", ":", "~", "!",
        }

    # token 序列归一化是表达式格式化的最后一步。
    def _normalize_expression_tokens(self, tokens: list[str]) -> str:
        """
        将 token 序列拼接为规范化表达式文本。

        :param tokens: tokenizer 输出的表达式 token 序列。
        :return: 空白规范化后的表达式文本。
        """

        # 空 token 序列对应空表达式。
        if not tokens:

            # 调用方保留空表达式语义。
            return ""

        # list_parts 保存逐 token 追加的输出片段。
        list_parts: list[str] = []  # 表达式输出片段

        # str_previous_symbol 记录上一 token，辅助判断一元运算符。
        str_previous_symbol: str | None = None  # 上一个表达式 token

        # bool_previous_unary 影响普通 atom 是否与前一 token 分隔。
        bool_previous_unary = False  # 上一个 token 是否为一元运算符

        # 按 token 顺序生成规范化表达式。
        for int_index, str_token in enumerate(tokens):

            # str_upcoming_symbol 用于判断当前逗号或运算符后是否补空格。
            str_upcoming_symbol = tokens[int_index + 1] if int_index + 1 < len(tokens) else None  # 下一个表达式 token

            # 逗号和分号具有专门的后置空格策略。
            if str_token in {",", ";"}:

                # 分隔符 helper 会原地追加到输出片段。
                self._append_expression_separator(list_parts, str_token, str_upcoming_symbol)

                # 分隔符之后的一元状态重置。
                str_previous_symbol = str_token  # 分隔符成为上一 token

                # 分隔符本身不会贴合下一个 atom。
                bool_previous_unary = False  # 分隔符不是一元运算符

                # 分隔符分支不再进入其他拼接路径。
                continue

            # 右括号和点号前需要收紧空格。
            if str_token in {")", "}", "."}:

                # 紧贴 token helper 会清理前置空格。
                self._append_expression_tight_token(list_parts, str_token)

                # 当前 token 成为下一轮上下文。
                str_previous_symbol = str_token  # 紧贴 token 成为上一 token

                # 闭合符或点号之后按普通 token 间距判断。
                bool_previous_unary = False  # 紧贴 token 不是一元运算符

                # 紧贴 token 分支不再进入普通 atom 路径。
                continue

            # 方括号 token 内部有独立的 range/part-select 规则。
            if str_token.startswith("[") and str_token.endswith("]"):

                # 方括号 token 先规范化内部表达式再追加。
                list_parts.append(self._compact_expression_bracket_token(str_token))

                # 方括号 token 作为普通 atom 结束一元状态。
                str_previous_symbol = str_token  # 方括号 token 成为上一 token

                # 方括号 token 后续不再继承一元贴合规则。
                bool_previous_unary = False  # 方括号 token 不是一元运算符

                # 方括号 token 分支不再进入普通 atom 路径。
                continue

            # 左括号和左花括号直接打开分组。
            if str_token in {"(", "{"}:

                # 开分组符不需要前置整理。
                list_parts.append(str_token)

                # 后续 token 根据开分组上下文判断一元符号。
                str_previous_symbol = str_token  # 开分组符成为上一 token

                # 开分组符自身不按一元符号记录。
                bool_previous_unary = False  # 开分组符不是一元运算符

                # 开分组符分支不再进入普通 atom 路径。
                continue

            # 运算符需要区分一元和二元格式。
            if self._is_expression_operator_token(str_token):

                # helper 写入运算符，并返回它是否按一元形式处理。
                if self._append_expression_operator(
                    list_parts,  # 运算符输出目标片段
                    str_token,  # 当前运算符 token
                    str_previous_symbol,  # 上一个 token
                    str_upcoming_symbol,  # 下一个 token
                ):

                    # 一元运算符后，下一普通片段需要贴合当前符号。
                    bool_previous_unary = True  # 下一普通片段不补前置空格

                # 二元或三目符号之后恢复普通空格规则。
                else:

                    # 后续普通片段可以按常规相邻 token 规则补空格。
                    bool_previous_unary = False  # 下一普通片段使用常规空格

                # 当前运算符成为下一轮上下文。
                str_previous_symbol = str_token  # 运算符成为上一 token

                # 运算符分支不再进入普通 atom 路径。
                continue

            # 剩余 token 按普通 atom 规则追加。
            self._append_expression_atom(list_parts, str_token, bool_previous_unary)

            # 普通 atom 结束一元状态。
            str_previous_symbol = str_token  # 普通 atom 成为上一 token

            # 下一个 atom 需要重新按普通间距判断。
            bool_previous_unary = False  # 普通 atom 不是一元运算符

        # 拼接所有片段并移除表达式两端空白。
        return "".join(list_parts).strip()

    # 带括号的控制头只规范化括号内部表达式。
    def _normalize_parenthesized_expression(self, text: str, prefix: str) -> str:
        """
        规范化指定关键字后的括号表达式。

        :param text: 原始控制头或语句文本。
        :param prefix: 需要匹配的关键字前缀。
        :return: 括号内部表达式规范化后的文本。
        """

        # 非目标前缀文本不属于本次处理范围。
        if not text.startswith(prefix):

            # 保持调用方传入文本不变。
            return text

        # int_open_index 是前缀后预期左括号的位置。
        int_open_index = len(prefix)  # 控制头左括号候选下标

        # 前缀后没有左括号时不能安全解析表达式范围。
        if int_open_index >= len(text) or text[int_open_index] != "(":

            # 保守返回原文本。
            return text

        # int_close_index 定位与左括号匹配的右括号。
        int_close_index = self._find_matching_paren_in_text(text, int_open_index)  # 控制头右括号位置

        # 括号不闭合时不尝试重排文本。
        if int_close_index == -1:

            # 保持异常控制头原样，交给上层兼容路径。
            return text

        # str_expression 保存括号内部规范化后的条件或选择表达式。
        str_expression = self._normalize_expression_spacing(text[int_open_index + 1 : int_close_index])  # 括号内部表达式

        # str_remainder 保留右括号后的 begin、label 或其他尾部文本。
        str_remainder = text[int_close_index + 1 :].strip()  # 括号右侧尾随文本

        # begin 紧跟右括号时沿用 formatter 的无空格样式。
        if not str_remainder or str_remainder.startswith("begin"):

            # 返回无额外空格的控制头。
            return f"{prefix}({str_expression}){str_remainder}"

        # 其他尾部文本前保留一个空格。
        return f"{prefix}({str_expression}) {str_remainder}"

    # 循环控制头按分号拆分三段表达式后逐段规范化。
    def _normalize_for_header(self, text: str) -> str:
        """
        规范化 Verilog for 控制头中的三个表达式段。

        :param text: 原始 for 控制头文本。
        :return: 分号分隔段规范化后的 for 头部。
        """

        # 只有紧凑 `for(` 形式进入本函数处理。
        if not text.startswith("for("):

            # 非 for 头部保持原样。
            return text

        # int_close_index 定位 for 条件括号的闭合位置。
        int_close_index = self._find_matching_paren_in_text(text, 3)  # for 头部右括号位置

        # 括号不闭合时不能安全拆分三段表达式。
        if int_close_index == -1:

            # 保留原始 for 头部文本。
            return text

        # str_payload 是 for 括号内部的 init/condition/step 文本。
        str_payload = text[4:int_close_index]  # for 括号内部文本

        # list_parts 按顶层分号拆出 for 的三个组成段。
        list_parts = self._split_top_level(str_payload, ";")  # for 头部顶层分号片段

        # list_normalized_parts 逐段应用普通表达式空白规则。
        list_normalized_parts = [self._normalize_expression_spacing(str_part) for str_part in list_parts]  # for 头部规范化片段

        # str_remainder 保留右括号后的 begin 或 label。
        str_remainder = text[int_close_index + 1 :].strip()  # for 头部尾随文本

        # 循环控制三段之间固定使用分号加空格。
        return f"for({'; '.join(list_normalized_parts)}){str_remainder}"

    # 顶层冒号查找需要避开 part-select 和赋值类操作符。
    def _find_top_level_colon(self, text: str) -> int:
        """
        查找表达式顶层的普通冒号。

        :param text: 待扫描文本。
        :return: 顶层普通冒号下标，未找到时返回 -1。
        """

        # int_depth 跟踪括号层级，非零时忽略冒号。
        int_depth = 0  # 顶层冒号扫描括号深度

        # int_index 指向顶层冒号扫描的当前字符。
        int_index = 0  # 顶层冒号扫描下标

        # 按字符扫描以区分 range、三目和 label 边界。
        while int_index < len(text):

            # str_char 是顶层冒号扫描本轮字符。
            str_char = text[int_index]  # 顶层冒号扫描字符

            # 分隔符 helper 保持顶层冒号扫描的非负深度。
            int_depth = self._advance_group_depth(str_char, int_depth)  # 当前字符处理后的冒号扫描层级

            # 非冒号或嵌套冒号都不能作为结果。
            if str_char != ":" or int_depth != 0:

                # 继续扫描下一字符。
                int_index += 1  # 非候选字符处理后的下标

                # 当前字符不满足顶层冒号条件。
                continue

            # str_previous_char 用于识别 `+:` 或 `-:`。
            str_previous_char = text[int_index - 1] if int_index > 0 else ""  # 冒号前一字符

            # str_next_char 用于识别 `:=` 风格或其他赋值尾部。
            str_next_char = text[int_index + 1] if int_index + 1 < len(text) else ""  # 冒号后一字符

            # part-select 或赋值相关冒号不是普通顶层冒号。
            if str_previous_char in "+-" or str_next_char == "=":

                # 跳过当前冒号继续扫描后续文本。
                int_index += 1  # 跳过特殊冒号后的下标

                # 当前冒号不作为结果返回。
                continue

            # 返回第一个普通顶层冒号位置。
            return int_index

            # 当前字符处理完成后推进扫描。
            int_index += 1  # 顶层冒号扫描下一下标

        # 未发现普通顶层冒号。
        return -1

    # 过程赋值、连续赋值和 label 前缀在这里统一重排空白。
    def _normalize_assignment_like_statement(self, text: str) -> str:
        """
        规范化赋值类语句中操作符两侧的空白。

        :param text: 原始语句文本。
        :return: 赋值操作符和两侧表达式规范化后的语句。
        """

        # str_stripped_text 去掉外围空白后判断语句形态。
        str_stripped_text = text.strip()  # 去空白后的语句文本

        # 只有分号结尾语句才按赋值语句处理。
        if not str_stripped_text.endswith(";"):

            # 非完整语句保持原文本。
            return str_stripped_text

        # str_prefix 保留连续赋值的 assign 关键字。
        str_prefix = ""  # 赋值语句前缀

        # str_body 去掉末尾分号后用于查找赋值操作符。
        str_body = str_stripped_text[:-1].strip()  # 去分号后的赋值主体

        # 连续赋值需要保留 assign 前缀。
        if str_body.startswith("assign "):

            # 连续赋值前缀单独保存，右侧主体继续规范化。
            str_prefix = "assign "  # 连续赋值前缀

            # 去掉 assign 后只检查实际赋值主体。
            str_body = str_body[len(str_prefix) :].strip()  # assign 后的赋值主体

            # 带 delay 的 assign 暂不重排，避免改变延迟语义文本。
            if str_body.startswith("#"):

                # 返回原始完整语句。
                return str_stripped_text

        # str_label_prefix 保存过程块 label 前缀。
        str_label_prefix = ""  # 赋值语句块标签前缀

        # int_colon_index 定位可能的 label 冒号。
        int_colon_index = self._find_top_level_colon(str_body)  # 赋值主体顶层冒号

        # 顶层冒号存在时，检查它是否分隔 label 和赋值主体。
        if int_colon_index != -1:

            # str_label_head 保存冒号左侧的标签候选。
            str_label_head = str_body[:int_colon_index]  # label 候选文本

            # str_label_tail 保存冒号右侧的赋值候选。
            str_label_tail = str_body[int_colon_index + 1 :]  # label 后赋值候选文本

            # label 后必须有赋值且不能是 begin 块。
            if (
                str_label_tail.strip()
                and not str_label_tail.strip().startswith("begin")
                and self._find_procedural_assignment_operators(str_label_tail)
            ):

                # label 前缀保留冒号和一个空格。
                str_label_prefix = f"{str_label_head.strip()}: "  # 规范化 label 前缀

                # 赋值主体切换为 label 后的文本。
                str_body = str_label_tail.strip()  # label 后赋值主体

        # list_assignment_positions 保存主体内可用赋值操作符位置。
        list_assignment_positions = self._find_procedural_assignment_operators(str_body)  # 赋值操作符位置列表

        # 未找到赋值操作符时不改写语句。
        if not list_assignment_positions:

            # 原语句可能是函数调用或其他过程语句。
            return str_stripped_text

        # int_operator_index 只取最左侧赋值操作符。
        int_operator_index = list_assignment_positions[0]  # 首个赋值操作符位置

        # str_operator 区分非阻塞和阻塞赋值。
        str_operator = "<=" if str_body.startswith("<=", int_operator_index) else "="  # 赋值操作符文本

        # str_lhs 规范化赋值左侧表达式。
        str_lhs = self._normalize_expression_spacing(str_body[:int_operator_index].strip())  # 赋值左表达式

        # str_rhs 规范化赋值右侧表达式。
        str_rhs = self._normalize_expression_spacing(str_body[int_operator_index + len(str_operator) :].strip())  # 赋值右表达式

        # 重新拼接 assign、label、lhs、operator 和 rhs。
        return f"{str_prefix}{str_label_prefix}{str_lhs} {str_operator} {str_rhs};"

    # 点名参数或端口关联只规范化括号内部表达式。
    def _normalize_named_association_line(self, text: str) -> str:
        """
        规范化 `.name(expr)` 形式关联行。

        :param text: 原始参数或端口关联行。
        :return: actual 表达式规范化后的关联行。
        """

        # match_association 只匹配完整点名关联行。
        match_association: re.Match[str] | None = re.match(  # 点名关联匹配结果
            r"^(?P<head>\.\w+)\((?P<expr>.*)\)(?P<suffix>,?)$",  # formal、actual 和尾逗号捕获模式
            text,  # 待匹配的关联行文本
        )

        # 非点名关联保持原样。
        if not match_association:

            # 调用方可能继续按普通语句处理。
            return text

        # str_expression 是括号内部 actual 表达式。
        str_expression = self._normalize_expression_spacing(match_association.group("expr").strip())  # 点名关联 actual 表达式

        # 保留 `.formal(` 前缀和可选尾逗号。
        return f"{match_association.group('head')}({str_expression}){match_association.group('suffix')}"

    # 语句空白替换规则保持旧 formatter 的执行顺序。
    def _statement_spacing_substitutions(self) -> tuple[tuple[str, str], ...]:
        """
        返回普通语句规范化使用的正则替换表。

        :param: 无外部业务参数。
        :return: 按执行顺序排列的 pattern/replacement 二元组。
        """

        # 替换顺序会影响 always、if 和 begin 的最终拼接形态。
        return (
            (r"always\s*@\s*\*", "always@(*)"),
            (r"always\s*@\s*\(", "always@("),
            (r"\belse\s+if\s*\(", "else if("),
            (r"\bif\s*\(", "if("),
            (r"\bcase\s*\(", "case("),
            (r"\bfor\s*\(", "for("),
            (r"\bwhile\s*\(", "while("),
            (r"if\(\s*!", "if(!"),
            (r"\)\s*begin", ")begin"),
            (r":\s*begin\b", ":begin"),
            (r"\bbegin\s*:\s*", "begin:"),
            (r"\)\s*:\s*", "):"),
            (r"\bend\s+else\b", "end else"),
        )

    # 普通语句代码先做关键字空白替换，再进入表达式级规范化。
    def _normalize_statement_code(self, code: str) -> str:
        """
        规范化单行 Verilog 代码部分。

        :param code: 已去掉右侧注释的代码文本。
        :return: 关键字、关联、赋值和控制头空白规范化后的代码。
        """

        # str_normalized 是逐步应用规则的工作文本。
        str_normalized = code  # 语句规范化工作文本

        # str_stripped 去掉外围空白后用于判断是否跳过处理。
        str_stripped = str_normalized.strip()  # 去外围空白语句

        # 空行、预处理和块注释不进入语句规范化。
        if (
            not str_stripped
            or str_stripped.startswith("//")
            or str_stripped.startswith("`")
            or "/*" in str_stripped
            or "*/" in str_stripped
        ):

            # 返回去空白文本，保持这些行的原始语义。
            return str_stripped

        # 按既定顺序应用关键字和 begin 空白替换。
        for str_pattern, str_replacement in self._statement_spacing_substitutions():

            # 当前替换只处理语法关键字附近的空白。
            str_normalized = re.sub(str_pattern, str_replacement, str_normalized)  # 语句正则替换结果

        # 点名关联表达式先做 actual 侧规范化。
        str_normalized = self._normalize_named_association_line(str_normalized)  # 点名关联规范化结果

        # 赋值语句再规范化赋值操作符两侧。
        str_normalized = self._normalize_assignment_like_statement(str_normalized)  # 赋值语句规范化结果

        # 条件控制头括号内部表达式规范化。
        str_normalized = self._normalize_parenthesized_expression(str_normalized, "if")  # if 头部规范化结果

        # 分支链控制头括号内部表达式规范化。
        str_normalized = self._normalize_parenthesized_expression(str_normalized, "else if")  # 分支链头部规范化结果

        # case 选择表达式规范化。
        str_normalized = self._normalize_parenthesized_expression(str_normalized, "case")  # 选择分支头部规范化结果

        # 循环条件表达式规范化。
        str_normalized = self._normalize_parenthesized_expression(str_normalized, "while")  # 条件循环头部规范化结果

        # 循环头部按分号拆分后逐段规范化。
        str_normalized = self._normalize_for_header(str_normalized)  # 三段循环头部规范化结果

        # 条件分支头还需要复用控制节点格式化中的条件折叠逻辑。
        if str_normalized.startswith("if(") or str_normalized.startswith("else if("):

            # 条件 header helper 负责剥离冗余括号和统一 begin 位置。
            str_normalized = self._normalize_if_condition_header(str_normalized)  # if 条件头最终文本

        # 返回所有规则处理后的语句代码。
        return str_normalized

    # 单行语句规范化会先拆右侧注释，避免注释内容被正则改写。
    def _normalize_statement_line(self, line: str) -> str:
        """
        规范化一整行 Verilog 语句。

        :param line: 原始单行 Verilog 文本。
        :return: 代码和右侧注释分别规范化后的文本。
        """

        # str_stripped 去掉外围空白后判断行类型。
        str_stripped = line.strip()  # 去外围空白行文本

        # 空行、注释、预处理和块注释边界保持原样。
        if (
            not str_stripped
            or str_stripped.startswith("//")
            or str_stripped.startswith("`")
            or "/*" in str_stripped
            or "*/" in str_stripped
        ):

            # 返回去外围空白后的特殊行文本。
            return str_stripped

        # str_raw_code 和 str_comment 分别保存代码和行注释。
        str_raw_code, str_comment = self._split_comment(str_stripped)  # 单行代码和注释片段

        # str_normalized_code 只对代码片段应用语法规范化。
        str_normalized_code = self._normalize_statement_code(str_raw_code.strip())  # 规范化后的代码片段

        # 存在右侧注释时需要拼回注释。
        if str_comment:

            # 只有注释没有代码时按纯注释行输出。
            if not str_normalized_code:

                # 逗号开头注释保持旧兼容拼接方式。
                return f"//{str_comment}" if str_comment.startswith(",") else f"// {str_comment}"

            # 普通代码行在代码和注释之间保留一个空格。
            return (
                f"{str_normalized_code} //{str_comment}"
                if str_comment.startswith(",")
                else f"{str_normalized_code} // {str_comment}"
            )

        # 无右侧注释时直接返回规范化代码。
        return str_normalized_code

    # raw block 缩进调整只关心行首闭合关键字数量。
    def _raw_block_leading_close_count(self, line: str) -> int:
        """
        判断 raw 行是否以块闭合关键字开头。

        :param line: raw block 中的一行文本。
        :return: 行首闭合关键字命中时返回 1，否则返回 0。
        """

        # 命中预编译模式即表示当前行先闭合一层缩进。
        return 1 if self.RAW_BLOCK_LEADING_CLOSE_PATTERN.match(line.strip()) else 0

    # 去掉行首闭合关键字后，剩余文本用于计算后续打开块。
    def _strip_raw_block_leading_close(self, line: str) -> str:
        """
        移除 raw 行首部的块闭合关键字。

        :param line: raw block 中的一行文本。
        :return: 去掉闭合关键字后的剩余文本。
        """

        # match_close 捕获闭合关键字后的剩余文本。
        match_close = self.RAW_BLOCK_LEADING_CLOSE_PATTERN.match(line.strip())  # raw 缩进闭合关键字匹配

        # 命中时只返回闭合关键字后面的剩余部分。
        return match_close.group("rest").lstrip() if match_close else line.strip()

    # 单行缩进增量由 begin/case 打开数减去 end/endcase 闭合数得到。
    def _statement_indent_delta(self, line: str) -> int:
        """
        计算当前 raw 行对下一行缩进层级的影响。

        :param line: 已规范化的 raw 行文本。
        :return: 打开块数量减去闭合块数量后的缩进增量。
        """

        # str_raw_code 去掉右侧注释后参与结构关键字统计。
        str_raw_code, _ = self._split_comment(line.strip())  # raw 行代码片段

        # str_working 先移除行首闭合关键字，避免重复计算当前行缩进。
        str_working = self._strip_raw_block_leading_close(str_raw_code)  # 缩进增量统计文本

        # int_open_count 统计 begin 关键字数量。
        int_open_count = len(re.findall(r"\bbegin\b", str_working))  # begin 打开数量

        # case/casez/casex 同样会打开一个缩进层级。
        int_open_count += len(re.findall(r"\b(?:case|casez|casex)\b", str_working))  # 选择块打开数量

        # int_close_count 统计普通过程块闭合数量。
        int_close_count = len(re.findall(r"\bend\b", str_working))  # end 闭合数量

        # 选择块闭合关键字会额外减少下一行缩进。
        int_close_count += len(re.findall(r"\bendcase\b", str_working))  # 选择块闭合数量

        # 正数表示下一行加深缩进，负数表示下一行减小缩进。
        return int_open_count - int_close_count

    # strict mode 错误使用统一 formatter 异常承载摘要和建议。
    def _strict_error(self, category: str, statement: str, suggestion: str) -> VerilogFormatterError:
        """
        构造 strict mode 下的 formatter 异常。

        :param category: strict 检查类别。
        :param statement: 触发错误的原始语句。
        :param suggestion: 面向调用方的修复建议。
        :return: 带摘要和建议的 VerilogFormatterError。
        """

        # str_summary 压缩过长语句，避免错误信息过宽。
        str_summary = self._summarize_statement(statement)  # strict 错误语句摘要

        # 返回统一异常对象，调用方负责抛出。
        return VerilogFormatterError(f"Strict mode [{category}]: {str_summary}. Suggestion: {suggestion}")

    # 错误摘要压缩连续空白并限制最大长度。
    def _summarize_statement(self, statement: str, limit: int = 120) -> str:
        """
        生成适合错误信息展示的单行语句摘要。

        :param statement: 原始语句文本。
        :param limit: 摘要最大字符数。
        :return: 压缩空白并按长度截断后的摘要。
        """

        # str_compact 把多行和连续空白压缩为单空格。
        str_compact = " ".join(statement.split())  # 单行语句摘要

        # 短语句可以完整展示。
        if len(str_compact) <= limit:

            # 返回未截断摘要。
            return str_compact

        # 过长语句末尾用省略号表示截断。
        return str_compact[: limit - 3] + "..."

    # 缩进字符串由 formatter 配置的 indent_unit 重复组成。
    def _indent(self, level: int) -> str:
        """
        生成指定层级的 Verilog 缩进。

        :param level: 缩进层级。
        :return: 对应层级的缩进字符串。
        """

        # 直接复用实例配置的缩进单元。
        return self.indent_unit * level
