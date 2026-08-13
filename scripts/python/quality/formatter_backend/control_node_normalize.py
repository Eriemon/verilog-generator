"""提供控制流节点形态归一化辅助。"""

# annotations 延后解析归一化方法的类型标注。
from __future__ import annotations

# 正则工具用于拆分内联控制语句。
import re

# models 提供归一化后的控制节点和 strict 解析异常。
from .models import ControlNode, VerilogFormatterError

# ControlNodeNormalizeMixin 负责把受支持的内联形态转换为统一节点。
class ControlNodeNormalizeMixin:
    """集中维护 if/else 内联节点与 strict 异常归一化。"""

    # 用统一前缀抛出 control parser 的 strict error。
    def _raise_control_error(self, category: str, statement: str, suggestion: str) -> None:
        """
        把 control parser 的 strict 异常包装成 current-project 前缀格式。

        参数:
            category: strict 检查类别。
            statement: 触发异常的原始语句。
            suggestion: 面向调用方的修复建议。
        返回:
            None: 本函数总是抛出异常，不向调用方返回业务值。
        异常:
            VerilogFormatterError: 以统一错误前缀抛出 strict 诊断。
        """

        # 保留 strict 诊断作为异常链，外层消息只负责提供项目统一前缀。
        raise VerilogFormatterError("> ERR: [Python] control parser strict failure.") from self._strict_error(
            category,
            statement,
            suggestion,
        )

    # 把 if 或 else if 文本切分成条件头和后续主体。
    def _split_if_condition(self, text: str) -> tuple[str, str]:
        """
        把 if 头部拆成条件部分与余下的主体文本。

        参数:
            text: 原始 if 或 else if 文本。
        返回:
            tuple[str, str]: 条件头文本，以及条件头之后的剩余内容。
        异常:
            VerilogFormatterError: if 头部不完整或缺少括号时抛出。
        """

        # 先去掉外围空白，便于处理 else if 变体。
        str_working = text.strip()  # 待切分的 if 头部文本

        # else if 需要先去掉 else 前缀，再按 if 头处理。
        if str_working.startswith("else "):

            # 保留 else 之后真正的 if 条件文本。
            str_working = str_working[len("else ") :].strip()  # 去掉 else 之后的 if 头部

        # 不以 if 开头的文本不能进入条件切分流程。
        if not str_working.startswith("if"):

            # 当前文本必须先整理成稳定的 if(...) 形态。
            self._raise_control_error(
                "unsupported_shape",
                text,
                "Use a stable 'if(...)' control-flow form before formatting.",
            )

        # 定位条件列表的首个左括号。
        int_open_index = str_working.find("(")  # if 条件左括号位置

        # 缺少左括号时无法确认条件范围。
        if int_open_index == -1:

            # 这种 if 形态不能进入后续分支解析。
            self._raise_control_error(
                "unsupported_shape",
                text,
                "Use a stable 'if(...)' control-flow form before formatting.",
            )

        # 利用通用括号匹配逻辑寻找对应的闭括号。
        int_close_index = self._find_balanced_close_index(str_working, int_open_index)  # if 条件右括号位置

        # 闭括号缺失时条件头仍然不完整。
        if int_close_index == -1:

            # 条件表达式必须先补齐括号后才能继续归一化。
            self._raise_control_error(
                "unsupported_shape",
                text,
                "Balance the if-condition parentheses before formatting.",
            )

        # 返回完整 if 条件头以及其后续文本。
        return str_working[: int_close_index + 1].strip(), str_working[int_close_index + 1 :].strip()

    # 解析形如 begin ... end 的单行内联 block 主体。
    def _parse_inline_begin_body(self, remainder: str, context: str) -> list[ControlNode] | None:
        """
        尝试解析单行 begin/end 包裹的内联主体。

        参数:
            remainder: if/else/loop 头部后紧跟的 begin 及其余文本。
            context: 当前 procedural 或 generate 上下文。
        返回:
            list[ControlNode] | None: 成功识别并解析时返回节点列表；不属于单行 begin/end 形态时返回 None。
        异常:
            VerilogFormatterError: begin/end 内联体仍包含不稳定结构时抛出。
        """

        # 匹配 begin、可选 label 以及其后可能跟随的内联主体文本。
        match_begin = re.match(r"^begin(?:\s*:\s*(\w+))?(?P<body>.*)$", remainder)  # 内联 begin 头匹配结果

        # 不是 begin 头时，本函数不负责处理。
        if not match_begin:

            # 让调用方继续尝试其他主体解析路径。
            return None

        # 提取 begin 之后紧随的内联主体文本。
        str_body = match_begin.group("body").strip()  # begin 之后的内联 body 文本

        # 只有 begin 而没有同行 body 时，交还给普通 begin/end 递归逻辑。
        if not str_body:

            # 这种情形不是单行 begin/end 主体。
            return None

        # 单行 begin body 必须在同一逻辑文本里以 end 收尾。
        if not re.search(r"\bend\b\s*$", str_body):

            # 未在同行闭合时不按内联 begin/end 处理。
            return None

        # 去掉结尾的 end 关键字，仅保留真正的 body 语句。
        str_body = re.sub(r"\bend\b\s*$", "", str_body).strip()  # 去掉结尾 end 后的 body 文本

        # begin 紧接 end 表示这是一个空块。
        if not str_body:

            # 返回空子节点列表表示空 begin/end。
            return []

        # 按分号顶层拆分出内联 begin/end 中的每条语句片段。
        list_fragments = [f"{entry.strip()};" for entry in self._split_top_level(str_body, ";") if entry.strip()]  # 内联 begin/end 语句片段列表

        # 递归解析这些语句片段对应的控制流节点。
        list_nodes, int_consumed = self._parse_control_nodes(list_fragments, 0, set(), context)  # 内联 body 节点和消费数

        # 未完全消费所有片段时，说明内联 block 仍然不稳定。
        if int_consumed != len(list_fragments):

            # 内联 begin/end 需要先简化成稳定片段后再归一化。
            self._raise_control_error(
                "unsupported_shape",
                remainder,
                "Simplify the inline begin/end block so it can be normalized safely.",
            )

        # 返回内联 begin/end 成功解析后的节点列表。
        return list_nodes

    # 给已经构造好的 if 节点补挂后续的 else / else if 分支。
    def _attach_if_alternate(
        self,
        node_if: ControlNode,
        lines: list[str],
        start: int,
        context: str,
    ) -> int:
        """
        从指定位置继续解析 if 节点的 alternate 分支。

        参数:
            node_if: 已经构造好的 if 主节点。
            lines: 当前控制流源码行列表。
            start: 可能出现 else 或 else if 的起始下标。
            context: 当前 procedural 或 generate 上下文。
        返回:
            int: 处理完 alternate 后的下一行位置；若没有 alternate，则返回跳过空白后的原位置。
        """

        # 先略过空白和注释，确定 alternate 链真正开始的位置。
        int_next_index = self._skip_ignorable_control_lines(lines, start)  # alternate 链的首个有效语句位置

        # 已经到达源码末尾时，说明当前 if 没有后续 alternate。
        if int_next_index >= len(lines):

            # 直接返回当前位置作为 if 节点结束位置。
            return int_next_index

        # 规范化当前位置的候选语句，用来判断是否进入 else 链。
        str_else_line = self._normalize_statement_line(lines[int_next_index].strip())  # 当前位置可能出现的 else 头文本

        # else-if 需要继续递归扩展 alternate 链。
        if self._is_else_if_header(str_else_line):

            # 递归解析这个 else-if，并保留它结束后的游标位置。
            node_alternate_if, int_next_index = self._parse_if_node(lines, int_next_index, context)  # alternate 链里的下一层条件节点与结束位置

            # alternate 以单节点列表形式挂回当前 if。
            node_if.alternate = [node_alternate_if]  # 以单节点列表挂接的 else-if 分支

            # 返回 else if 链消费后的下一行位置。
            return int_next_index

        # 普通 else 由 else 分支解析器接管。
        if str_else_line.startswith("else"):

            # 解析 else 分支的子节点列表。
            node_if.alternate, int_next_index = self._parse_else_branch(lines, int_next_index, context, str_else_line)  # else 分支节点列表与结束位置

        # 返回无论是否命中 else 后的当前位置。
        return int_next_index

    # 解析头部同行残留的控制流主体。
    def _parse_inline_control_remainder(
        self,
        lines: list[str],
        start: int,
        remainder: str,
        context: str,
    ) -> tuple[list[ControlNode], int]:
        """
        解析 if/else/loop 头部同行残留的 inline 控制流主体。

        参数:
            lines: 原始源码行列表。
            start: 当前头部在原始列表中的位置。
            remainder: 条件头或 else 关键字后余下的文本。
            context: 当前 procedural 或 generate 上下文。
        返回:
            tuple[list[ControlNode], int]: inline 主体解析出的节点列表，以及消费后的下一行位置。
        异常:
            VerilogFormatterError: inline 单语句缺少顶层分号时抛出。
        """

        # 先把同行剩余文本规范化，便于判断它的语法类别。
        str_normalized = self._normalize_statement_line(remainder.strip())  # inline 主体规范化文本

        # inline case 需要构造虚拟片段后复用 case 解析器。
        if str_normalized.startswith("case"):

            # 让 case 解析器从虚拟片段数组头部开始消费。
            list_inline_lines = [str_normalized, *lines[start + 1 :]]  # inline case 的虚拟源码

            # 解析虚拟数组中的 case 节点。
            node_case, int_consumed = self._parse_case_node(list_inline_lines, 0, context)  # inline case 节点和消费数

            # 把虚拟消费数换算回原始源码位置。
            return [node_case], start + int_consumed

        # inline if 同样通过虚拟片段复用 if 解析器。
        if str_normalized.startswith("if ") or str_normalized.startswith("if("):

            # 把 inline 条件主体扩展成一组虚拟源码片段，供 if 解析器沿原逻辑消费。
            list_inline_lines = [str_normalized, *lines[start + 1 :]]  # inline 条件语句对应的虚拟源码数组

            # 在虚拟片段数组里递归恢复完整的 inline 条件子树。
            node_if, int_consumed = self._parse_if_node(list_inline_lines, 0, context)  # inline 条件节点与虚拟源码消费数

            # 把虚拟数组消费量折返为原始源码数组位置，便于调用方继续前进。
            return [node_if], start + int_consumed

        # 其他内联主体统一按普通单语句路径收集。
        str_statement, int_consumed = self._collect_statement_text_from_fragments(  # inline 单语句文本与消费数
            [str_normalized, *lines[start + 1 :]],  # inline 主体及后续源码片段
            0,  # 从虚拟片段数组头部开始收集
        )

        # 单语句主体必须以顶层分号稳定收尾。
        if not self._statement_has_top_level_semicolon(str_statement):

            # 缺少分号时不能可靠切出 inline 主体边界。
            self._raise_control_error(
                "unsupported_shape",
                str_normalized,
                "Terminate each inline control-flow statement with ';' before formatting.",
            )

        # 返回仅包含单语句节点的 inline 主体结果。
        return [ControlNode(kind="statement", text=str_statement)], start + int_consumed
