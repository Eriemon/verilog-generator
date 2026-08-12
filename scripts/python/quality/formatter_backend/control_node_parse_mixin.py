"""为 VerilogFormatterEngine 提供控制流节点解析辅助。"""

# future annotations 让 mixin 拆分后的类型提示保持延迟求值。
from __future__ import annotations

# Callable 用于给局部解析器别名补齐静态类型。
from typing import Callable

# 正则工具用于识别 begin/end、case item 和循环头部形态。
import re

# formatter 模型承载控制流树节点和 case item 结构。
from .control_node_classifier import ControlNodeClassifierMixin
from .control_node_collectors import ControlNodeCollectorsMixin
from .control_node_normalize import ControlNodeNormalizeMixin
from .models import CaseItem, ControlNode, InitialBlock, VerilogFormatterError

# 维护控制流节点解析职责的 mixin。
class ControlNodeParseMixin(ControlNodeClassifierMixin, ControlNodeCollectorsMixin, ControlNodeNormalizeMixin):
    """维护 if/case/loop/generate 相关的控制流语法解析逻辑。"""

    # 解析一个 control block 中的顺序节点列表。
    def _parse_control_nodes(
        self,
        lines: list[str],
        start: int,
        terminators: set[str],
        context: str,
    ) -> tuple[list[ControlNode], int]:
        """
        递归解析当前控制块内的控制流节点。

        参数:
            lines: 已按物理行拆分的控制流源码。
            start: 当前递归层的起始行下标。
            terminators: 遇到这些终止关键字时结束当前层解析。
            context: procedural 或 generate 上下文标签。
        返回:
            tuple[list[ControlNode], int]: 当前层生成的控制流节点列表，以及停止解析时的下一行位置。
        异常:
            VerilogFormatterError: 控制流结构不闭合或形态不受支持时抛出。
        """

        # 当前递归层把解析出的节点顺序累积到列表中。
        list_nodes: list[ControlNode] = []  # 当前控制块的节点结果

        # index 指向本层下一条待分析的源码行。
        int_index = start  # 当前递归层的扫描游标

        # 逐行消费当前控制块，直到命中终止符或源码末尾。
        while int_index < len(lines):

            # 先把当前行整理成便于语法判断的规范化文本。
            str_statement = self._normalize_statement_line(lines[int_index].strip())  # 当前控制流语句文本

            # 纯空白行不产生节点，只推进扫描位置。
            if not str_statement:

                # 跳过空白行后继续在同一层查找有效语句。
                int_index += 1  # 跳过后的下一行位置

                # 当前层无需为纯空白行生成节点。
                continue

            # begin/end 层级里遇到 else 时，要把控制权交回上层 if 解析器。
            if "end" in terminators and str_statement.startswith("else"):

                # 让上层分支逻辑继续接管 else 解析。
                break

            # 命中显式终止关键字时结束当前递归层。
            if self._matches_terminator(str_statement, terminators):

                # 把终止行留给上层消费。
                break

            # 注释、原文保留块和必须拒绝的 procedural 项先走专门派发。
            tuple_preserved_result = self._parse_preserved_control_entry(lines, int_index, context, str_statement)  # 需要直接保留或拒绝的特殊入口结果

            # helper 命中特殊入口时，把返回节点和游标统一并回主循环。
            if tuple_preserved_result is not None:

                # helper 返回的节点列表和新游标统一并回主循环。
                list_new_nodes, int_index = tuple_preserved_result  # 特殊入口生成的节点与新的扫描位置

                # 把这批特殊入口节点并回当前控制块结果列表。
                list_nodes.extend(list_new_nodes)

                # 当前源码行已经由第一级派发消费完成。
                continue

            # begin/case/if/generate/for 等结构化节点统一交给第二级派发。
            tuple_structured_result = self._parse_structured_control_entry(lines, int_index, context, str_statement)  # 结构化控制入口的派发结果

            # helper 命中结构化入口时，把节点和游标统一并回主循环。
            if tuple_structured_result is not None:

                # 取出结构化入口返回的节点列表和更新后的扫描位置。
                list_new_nodes, int_index = tuple_structured_result  # 结构化入口生成的节点与新的扫描位置

                # 把这一批结构化节点并回当前控制块结果列表。
                list_nodes.extend(list_new_nodes)

                # 当前源码行已经转换成结构化节点并完成回写。
                continue

            # 普通单语句控制项按 statement 节点收集。
            tuple_statement_result = self._collect_statement_text(lines, int_index)  # statement 文本和下一行位置

            # 取出当前 statement 的完整文本。
            str_statement_text = tuple_statement_result[0]  # 当前 statement 的聚合文本

            # 把这条普通语句封装成控制流叶子节点。
            list_nodes.append(ControlNode(kind="statement", text=str_statement_text))

            # 跳到 statement 消费后的下一行继续扫描。
            int_index = tuple_statement_result[1]  # statement 结束后的下一行位置

        # 返回本层节点列表以及停止时的游标位置。
        return list_nodes, int_index

    # 处理注释、原文保留块和必须拒绝的 procedural 入口。
    def _parse_preserved_control_entry(
        self,
        lines: list[str],
        start: int,
        context: str,
        statement: str,
    ) -> tuple[list[ControlNode], int] | None:
        """
        处理需要直接保留、直接跳过或立即拒绝的控制入口。

        参数:
            lines: 已按物理行拆分的控制流源码。
            start: 当前待处理的源码行下标。
            context: procedural 或 generate 上下文标签。
            statement: 当前行的规范化语句文本。
        返回:
            tuple[list[ControlNode], int] | None: 命中时返回生成节点和新的游标位置，否则返回 None。
        异常:
            VerilogFormatterError: procedural 特殊构造混入控制树时抛出。
        """

        # 行注释需要按 comment 节点保留在控制树里。
        if statement.startswith("//"):

            # 直接返回 comment 节点和下一条源码位置。
            return [ControlNode(kind="comment", text=statement)], start + 1

        # 块注释整体跳过，避免把注释内容误判成控制语句。
        if statement.startswith("/*"):

            # 从块注释的下一行开始继续找闭合位置。
            int_next_index = start + 1  # 块注释扫描游标

            # 持续向后扫描，直到遇到块注释的闭合标记。
            while int_next_index < len(lines):

                # 命中闭合标记时，把闭合行一并消费掉。
                if "*/" in lines[int_next_index]:

                    # 返回块注释后的下一条源码位置。
                    return [], int_next_index + 1

                # 闭合尚未出现时，继续检查下一条注释行。
                int_next_index += 1  # 块注释扫描推进后的下一行位置

            # 扫描到源码末尾时，返回当前停住的位置交给上层收口。
            return [], int_next_index

        # generate 内的 initial 需要先提取为专用原文块节点。
        if context == "generate" and statement.startswith("initial"):

            # 先提取 generate 域里的完整 initial 原文块。
            initial_block_node, int_next_index = self._parse_initial_block(lines, start)  # generate initial 原文块与结束位置

            # 把原文块转换成 runtime 使用的结构化 initial 节点。
            return [
                ControlNode(
                    kind="initial_block",
                    header=initial_block_node.header,
                    text="\n".join(initial_block_node.lines),
                    children=initial_block_node.nodes,
                )
            ], int_next_index

        # generate 域里的 function 定义在 runtime 阶段保留完整原文。
        if context == "generate" and statement.startswith("function "):

            # 先提取 generate function 的完整原文块和结束位置。
            function_block, int_next_index = self._parse_function_block(lines, start)  # function 原文块与主扫描游标的新位置

            # 返回单个 function 原文块节点，供 formatter 后续原样回放。
            return [ControlNode(kind="function_block", text="\n".join(function_block.lines))], int_next_index

        # generate 域里的 task 定义同样保留原始块，避免提前改写内部语义。
        if context == "generate" and statement.startswith("task "):

            # 先提取 generate task 的完整原文块，并拿到 task 结束后的源码位置。
            task_block, int_next_index = self._parse_task_block(lines, start)  # task 原文块与 task 结束后的源码位置

            # 返回单个 task 原文块节点，保持 task 内部局部语义不被提前改写。
            return [ControlNode(kind="task_block", text="\n".join(task_block.lines))], int_next_index

        # procedural 特殊构造混入当前 control parser 时必须立即拒绝。
        if statement.startswith(("initial", "task ", "function ", "endtask", "endfunction")):

            # 这类 procedural 特殊构造必须先在外层归一化。
            self._raise_control_error(
                self._control_shape_category(context),
                statement,
                "Remove unsupported procedural constructs before formatting this control block.",
            )

        # 没有命中特殊入口时，把处理权交还给主循环。
        return None

    # 处理 begin/case/if/generate/for 等结构化控制入口。
    def _parse_structured_control_entry(
        self,
        lines: list[str],
        start: int,
        context: str,
        statement: str,
    ) -> tuple[list[ControlNode], int] | None:
        """
        处理可直接分派到专用解析器的结构化控制入口。

        参数:
            lines: 已按物理行拆分的控制流源码。
            start: 当前待处理的源码行下标。
            context: procedural 或 generate 上下文标签。
            statement: 当前行的规范化语句文本。
        返回:
            tuple[list[ControlNode], int] | None: 命中时返回生成节点和新的游标位置，否则返回 None。
        异常:
            VerilogFormatterError: 结构不闭合或 shape 不稳定时抛出。
        """

        # begin 头命中时递归解析其内部节点。
        if self._is_begin_header(statement):

            # 提取 begin 头上可选的命名块标签。
            str_label = self._extract_block_label(statement)  # 当前 begin 头部中的块标签

            # 递归拉平这个 begin/end 对应的内部控制流子树。
            list_children, int_next_index = self._parse_control_nodes(lines, start + 1, {"end"}, context)  # 当前 begin 块的子节点列表与结束位置

            # 递归返回的位置必须能对上当前 begin 的闭合 end。
            if int_next_index >= len(lines) or not self._matches_terminator(
                self._normalize_statement_line(lines[int_next_index].strip()),
                {"end"},
            ):

                # begin/end 未闭合时不能继续猜测 block 边界。
                self._raise_control_error(
                    "unsupported_shape",
                    statement,
                    "Balance begin/end pairs before formatting.",
                )

            # generate block 或带 label 的 begin 需要保留显式 block 节点。
            if str_label or context == "generate":

                # 返回带 label 或 generate 语义的 block 节点。
                return [ControlNode(kind="block", label=str_label, children=list_children)], int_next_index + 1

            # 普通 procedural begin 只把子节点直接平铺回父层。
            return list_children, int_next_index + 1

        # case 头部交给 case 解析器处理。
        if statement.startswith("case"):

            # 递归解析完整 case 节点和结束位置。
            node_case, int_next_index = self._parse_case_node(lines, start, context)  # case 节点解析结果

            # 返回单个 case 节点给主循环并回当前控制块。
            return [node_case], int_next_index

        # 条件分支头统一交给 if 解析器构造分支树。
        if statement.startswith("if ") or statement.startswith("if(") or self._is_else_if_header(statement):

            # 递归构造当前条件分支树和结束位置。
            node_if, int_next_index = self._parse_if_node(lines, start, context)  # 当前条件分支节点与结束位置

            # 返回完整条件分支树，供父层继续维护后续节点顺序。
            return [node_if], int_next_index

        # generate 嵌套 generate 时要先提取内部控制区域。
        if context == "generate" and statement.startswith("generate"):

            # 先裁剪出当前内层 generate 的内部控制流源码。
            list_generate_lines, int_next_index = self._parse_generate_inner_lines(lines, start)  # generate 内部源码

            # 再对裁剪后的内层 generate 片段执行一次完整控制流解析。
            list_generate_nodes, int_consumed = self._parse_control_nodes(list_generate_lines, 0, set(), "generate")  # 嵌套 generate 的子节点与消费行数

            # 子片段未完整消费时，说明内层 generate 仍含不稳定结构。
            if int_consumed != len(list_generate_lines):

                # 当前内层 generate 需要先被规整成稳定形态。
                self._raise_control_error(
                    "unsupported_generate_shape",
                    "\n".join(list_generate_lines),
                    "Simplify nested generate control flow so it can be normalized safely.",
                )

            # 返回保留内层控制树的 generate 节点。
            return [ControlNode(kind="generate", children=list_generate_nodes)], int_next_index

        # genvar 声明在 generate 控制流里按普通 statement 保留。
        if context == "generate" and statement.startswith("genvar "):

            # 返回单个 statement 节点保留 genvar 原语句。
            return [ControlNode(kind="statement", text=statement)], start + 1

        # 循环头与循环体交给专门的 loop 解析路径处理。
        if statement.startswith("for"):

            # 递归提取当前 loop 节点和结束位置。
            node_loop, int_next_index = self._parse_loop_node(lines, start, context)  # 当前循环节点与结束位置

            # 返回单个 loop 节点给主循环继续挂接。
            return [node_loop], int_next_index

        # generate 中的 always 会在外层拆解后作为嵌套节点接回。
        if context == "generate" and statement.startswith("always"):

            # 复用既有逻辑解析 nested always 生成的节点序列。
            list_nested_nodes, int_next_index = self._parse_nested_always_nodes(lines, start)  # nested always 节点序列

            # 直接返回 nested always 产出的节点列表。
            return list_nested_nodes, int_next_index

        # 没有命中结构化入口时，把处理权交还给主循环。
        return None

    # 提取 generate/endgenerate 之间真正参与控制流解析的内部行。
    def _parse_generate_inner_lines(self, lines: list[str], start: int) -> tuple[list[str], int]:
        """
        把 generate block 裁剪成内部控制流源码片段。

        参数:
            lines: 原始源码行列表。
            start: generate 头部所在的起始下标。
        返回:
            tuple[list[str], int]: generate 内部源码片段，以及原始源码中的下一个位置。
        """

        # 先调用既有 generate block 收集器拿到完整块。
        list_block_lines, int_next_index = self._collect_generate_block(lines, start)  # generate 完整块与结束位置

        # 把外层 generate 壳去掉，只保留内部控制流源码。
        return self._extract_generate_inner_lines(list_block_lines), int_next_index

    # 解析 for-loop 头部和主体。
    def _parse_loop_node(self, lines: list[str], start: int, context: str) -> tuple[ControlNode, int]:
        """
        解析 procedural 或 generate 里的 for-loop 节点。

        参数:
            lines: 控制流源码行列表。
            start: for 头部所在的起始下标。
            context: 当前解析上下文标签。
        返回:
            tuple[ControlNode, int]: 组装好的 loop 节点，以及消费后的下一行位置。
        异常:
            VerilogFormatterError: for 头部括号、主体边界或单语句形态不稳定时抛出。
        """

        # 规范化当前行，便于解析 for 头部文本。
        str_statement = self._normalize_statement_line(lines[start].strip())  # 当前 for 头部文本

        # 入口必须真的是 for 语句。
        if not str_statement.startswith("for"):

            # 非 for 文本进入此解析器时视为调用方结构错误。
            self._raise_control_error(
                self._control_shape_category(context),
                str_statement,
                "Use a stable procedural for-loop form before formatting.",
            )

        # 找出 for 条件列表的起始左括号。
        int_open_index = str_statement.find("(")  # for 头部左括号位置

        # 缺少左括号时无法切分循环头与循环体。
        if int_open_index == -1:

            # 当前循环头必须先补齐括号。
            self._raise_control_error(
                self._control_shape_category(context),
                str_statement,
                "Balance the for-loop parentheses before formatting.",
            )

        # 计算与首个左括号配对的右括号位置。
        int_close_index = self._find_balanced_close_index(str_statement, int_open_index)  # for 头部右括号位置

        # 找不到闭括号时说明循环头不完整。
        if int_close_index == -1:

            # 不完整的 for 头会让循环体边界无法可靠判断。
            self._raise_control_error(
                self._control_shape_category(context),
                str_statement,
                "Balance the for-loop parentheses before formatting.",
            )

        # 取出闭括号之前的规范化 for 头部。
        str_header = str_statement[: int_close_index + 1].strip()  # loop 节点头部文本

        # 剩余文本用于判断 begin、inline body 或 lookahead body。
        str_remainder = str_statement[int_close_index + 1 :].strip()  # for 头部之后的剩余文本

        # 内联 begin:label 头可以直接识别出命名块形式。
        match_begin = re.match(r"^begin(?:\s*:\s*(\w+))?$", str_remainder)  # 单行 begin 头匹配结果

        # begin 形式的循环体需要递归解析直到 end。
        if match_begin:

            # 收集 begin/end 包裹的 loop 主体控制流节点。
            list_children, int_next_index = self._parse_control_nodes(lines, start + 1, {"end"}, context)  # loop begin 体的节点和结束位置

            # 递归返回的位置必须指向匹配的 end。
            if int_next_index >= len(lines) or not self._matches_terminator(
                self._normalize_statement_line(lines[int_next_index].strip()),
                {"end"},
            ):

                # generate 和 procedural 需要不同的修复建议文本。
                if context == "generate":

                    # generate 循环缺少 end 时，提示用户补齐显式块闭合。
                    str_suggestion = "Close every generate loop body with 'end'."  # generate loop 缺失 end 时的修复建议

                # procedural 上下文改用过程块循环的补全提示。
                else:

                    # procedural for-loop 缺少主体时，只需要提示用户补出稳定可解析的过程块主体。
                    str_suggestion = "Close every procedural for-loop body with 'end' before formatting."  # 过程块 loop 缺失 end 时的修复提示

                # 缺少 end 会让 loop body 的边界不可信。
                self._raise_control_error(
                    self._control_shape_category(context),
                    str_statement,
                    str_suggestion,
                )

            # 返回保留 label 的 loop 节点和 end 之后的位置。
            return (
                ControlNode(
                    kind="loop",
                    header=str_header,
                    label=match_begin.group(1) or "",
                    children=list_children,
                ),
                int_next_index + 1,
            )

        # 头部同行若还带剩余内容，则按单语句 loop body 处理。
        if str_remainder:

            # 内联控制结构会让单语句 loop body 变得不稳定。
            if str_remainder.startswith(("if ", "if(", "case", "for", "while", "else")):

                # 这类 loop 体必须改写成显式 begin/end 或稳定单语句。
                self._raise_control_error(
                    "unsupported_shape",
                    str_statement,
                    "Use a stable single procedural statement or an explicit begin/end loop body before formatting.",
                )

            # 以内联剩余文本和后续源码片段共同拼出完整单语句 loop body。
            list_loop_body_fragments = [str_remainder, *lines[start + 1 :]]  # loop 单语句收集用的片段数组

            # 基于片段数组收敛 loop 单语句文本及其消费数量。
            str_body_statement, int_consumed = self._collect_statement_text_from_fragments(list_loop_body_fragments, 0)  # loop 单语句主体文本与消费片段数

            # 单语句 loop body 必须以分号稳定收尾。
            if not self._statement_has_top_level_semicolon(str_body_statement):

                # 缺少分号会让循环体边界无法稳定识别。
                self._raise_control_error(
                    "unsupported_shape",
                    str_statement,
                    "Terminate each single-statement loop body with ';' before formatting.",
                )

            # 返回单语句 loop 节点和消费后的源码位置。
            return (
                ControlNode(
                    kind="loop",
                    header=str_header,
                    children=[ControlNode(kind="statement", text=str_body_statement)],
                ),
                start + int_consumed,
            )

        # 当前行不带 loop body 时，需要向后跳过空白和注释。
        int_lookahead = self._skip_ignorable_control_lines(lines, start + 1)  # loop body 实际起点

        # 还有后续行可供解析时，尝试识别显式 begin、if、case 或单语句。
        if int_lookahead < len(lines):

            # 规范化 lookahead 行后再判断主体类型。
            str_next_statement = self._normalize_statement_line(lines[int_lookahead].strip())  # loop body 首行文本

            # begin 形式的主体使用独立 block 递归解析。
            if self._is_begin_header(str_next_statement):

                # 记住 lookahead begin 是否显式标了块名。
                str_label = self._extract_block_label(str_next_statement)  # loop 主体 begin 的标签名

                # 递归读取这个 lookahead begin 主体里的控制流子树。
                list_children, int_next_index = self._parse_control_nodes(lines, int_lookahead + 1, {"end"}, context)  # loop 主体 begin 子树与结束位置

                # 缺少 end 时不能继续猜测 loop body 边界。
                if int_next_index >= len(lines) or not self._matches_terminator(
                    self._normalize_statement_line(lines[int_next_index].strip()),
                    {"end"},
                ):

                    # procedural loop 的主体必须先补齐显式 end。
                    self._raise_control_error(
                        self._control_shape_category(context),
                        str_statement,
                        "Close every procedural for-loop body with 'end' before formatting.",
                    )

                # 返回带 begin label 的 loop 节点。
                return (
                    ControlNode(kind="loop", header=str_statement, label=str_label, children=list_children),
                    int_next_index + 1,
                )

            # case 作为 loop body 时把 case 节点挂到 loop 子节点中。
            if str_next_statement.startswith("case"):

                # 解析 loop 主体里命中的 case 结构。
                node_case, int_next_index = self._parse_case_node(lines, int_lookahead, context)  # loop 主体中的 case 节点与结束位置

                # 把 case 节点包装成 loop 的唯一子节点。
                return ControlNode(kind="loop", header=str_statement, children=[node_case]), int_next_index

            # 条件分支作为 loop body 时递归构建单个条件节点。
            if str_next_statement.startswith("if ") or str_next_statement.startswith("if("):

                # 解析 loop 主体里继续嵌套的条件分支。
                node_if, int_next_index = self._parse_if_node(lines, int_lookahead, context)  # loop 主体中的嵌套条件节点与结束位置

                # 返回带单个 if 子节点的 loop 节点。
                return ControlNode(kind="loop", header=str_statement, children=[node_if]), int_next_index

            # 其他情况按普通单语句 loop body 收集。
            str_single_statement, int_next_index = self._collect_statement_text(lines, int_lookahead)  # lookahead 单语句主体文本与结束位置

            # 返回单语句形式的 loop 节点。
            return (
                ControlNode(
                    kind="loop",
                    header=str_statement,
                    children=[ControlNode(kind="statement", text=str_single_statement)],
                ),
                int_next_index,
            )

        # generate 循环和 procedural 循环在缺少主体时使用不同建议。
        if context == "generate":

            # generate 域要求显式给出 begin:label 或单语句主体。
            str_suggestion = "Use 'for(...) begin:label' or 'for(...) statement;' style generate loops."  # generate 循环缺失主体时的修复建议

        # procedural 路径需要切换成普通过程块循环的修复提示。
        else:

            # procedural 域只要求给出稳定可解析的循环主体。
            str_suggestion = "Provide a stable procedural for-loop body before formatting."  # 过程块循环缺失主体时的修复提示

        # 没有主体的 loop 无法安全格式化。
        self._raise_control_error(self._control_shape_category(context), str_statement, str_suggestion)

    # 解析 if 节点及其主体。
    def _parse_if_node(self, lines: list[str], start: int, context: str) -> tuple[ControlNode, int]:
        """
        解析 if 或 else if 节点。

        参数:
            lines: 控制流源码行列表。
            start: if 头部起始行下标。
            context: 当前 procedural 或 generate 上下文。
        返回:
            tuple[ControlNode, int]: 解析完成的 if 节点，以及消费后的下一行位置。
        异常:
            VerilogFormatterError: if 主体缺失、begin/end 不闭合或主体结构不稳定时抛出。
        """

        # 声明 else 分支解析器别名的可调用类型。
        func_parse_else: Callable[[list[str], int, str, str], tuple[list[ControlNode], int]]  # else 解析器类型

        # 缓存 else 分支解析器，避免后续内联调用行过长。
        func_parse_else = self._parse_else_branch  # else 分支解析器引用

        # 声明 if 内联主体解析器别名的可调用类型。
        func_parse_inline: Callable[[list[str], int, str, str], tuple[list[ControlNode], int]]  # if 内联解析器类型

        # 缓存内联主体解析器，避免条件头同行主体调用过长。
        func_parse_inline = self._parse_inline_control_remainder  # if 内联主体解析器引用

        # 声明通用控制块解析器别名的可调用类型。
        func_parse_nodes: Callable[[list[str], int, set[str], str], tuple[list[ControlNode], int]]  # if 块递归解析器类型

        # 缓存通用控制块解析器，避免 begin 体递归调用过长。
        func_parse_nodes = self._parse_control_nodes  # if begin 体递归解析器引用

        # 先收集跨多行书写的完整 if 头部。
        tuple_if_header = self._collect_if_header(lines, start)  # if 头部文本和主体起始位置

        # 提取收集完成的 if 头部文本。
        str_if_header = tuple_if_header[0].strip()  # 规范化后的 if 头部文本

        # 主体通常从 if 头部结束后的下一行开始。
        int_body_start = tuple_if_header[1]  # if 主体起始位置

        # 把 if 头部拆成条件头与头部同行剩余文本。
        str_header, str_remainder = self._split_if_condition(str_if_header)  # if 条件头与同行剩余文本

        # label 仅在 begin:label 形态下填写。
        str_label = ""  # if 主体 begin 标签

        # children 承载当前 if 分支的主体节点。
        list_children: list[ControlNode] = []  # if 主体节点列表

        # next_index 指向当前 if 解析完成后的下一行。
        int_next_index = int_body_start  # if 节点结束位置

        # remainder 以 begin 开头时，优先按 begin/end 主体解析。
        if str_remainder.startswith("begin"):

            # 先抽取内联 begin 的可选 label。
            str_label = self._extract_block_label(str_remainder)  # 内联 begin 标签

            # 尝试解析单行 begin ... end 内联体。
            list_inline_children = self._parse_inline_begin_body(str_remainder, context)  # 单行内联 begin 体节点

            # 单行 begin/end 成功命中时，主体已在当前行内闭合。
            if list_inline_children is not None:

                # 先接住单行 begin/end 解析出来的主体节点列表，供当前 if 直接复用。
                list_children = list_inline_children  # 单行 begin/end 解析得到的 if 主体节点列表

                # 头部同行里的内联主体已经全部消费完毕，结束位置保持在当前 if 头之后。
                int_next_index = int_body_start  # 单行 begin/end 主体消费后的下一行位置

            # 非单行 begin/end 时退回普通 begin block 递归解析。
            else:

                # 递归解析显式 begin/end 包裹的完整 if 主体。
                list_children, int_next_index = self._parse_control_nodes(lines, int_body_start, {"end"}, context)  # if begin 体节点和结束位置

                # 跳过 end 之前可能留下的空白和注释。
                int_next_index = self._skip_ignorable_control_lines(lines, int_next_index)  # end 候选位置

                # 递归返回越过源码末尾时，说明 if 主体没有闭合。
                if int_next_index >= len(lines):

                    # begin 体必须先补齐 end 才能进入 formatter。
                    self._raise_control_error(
                        self._control_shape_category(context),
                        str_if_header,
                        "Close every if/else block with 'end' before formatting.",
                    )

                # 规范化 end 或 end else 所在的候选行。
                str_terminator_line = self._normalize_statement_line(lines[int_next_index].strip())  # if 终止行文本

                # end 与 else 写在同一逻辑行时，需要拆分出 else 部分单独处理。
                if str_terminator_line.startswith("end else"):

                    # 截出与 end 写在同一逻辑行里的 else 文本，供后续分支解析器直接复用。
                    str_inline_else_line = str_terminator_line[len("end") :].strip()  # 与 end 同行的 else 文本

                    # 构造命中 end else 组合行时的 if 主节点，后续只补挂 alternate。
                    node_if = ControlNode(kind="if", header=str_header, label=str_label, children=list_children)  # 命中 end else 组合行时的 if 主节点

                    # 解析与当前 end 同行拼接出现的 else 分支，并暂存其完整返回结果。
                    tuple_inline_else_result = func_parse_else(lines, int_next_index, context, str_inline_else_line)  # end 同行 else 结果

                    # 从同行 else 的解析结果里拆出 alternate 子节点和结束位置。
                    node_if.alternate, int_next_index = tuple_inline_else_result  # 同行 else 分支节点与结束位置

                    # 返回已挂好 alternate 的 if 节点。
                    return node_if, int_next_index

                # 正常 end 行需要前进到其后的下一行。
                if self._matches_terminator(str_terminator_line, {"end"}):

                    # 消费掉当前分支块对应的 end。
                    int_next_index += 1  # 当前条件块结束后的下一行位置

                # 既不是 end 也不是 else 时，说明 begin/end 配对形态不稳定。
                elif not str_terminator_line.startswith(
                    "else"
                ):

                    # 当前 if block 必须先闭合 end 才能继续处理。
                    self._raise_control_error(
                        self._control_shape_category(context),
                        str_if_header,
                        "Close every if/else block with 'end' before formatting.",
                    )

        # remainder 仍有内容时，说明主体紧跟在 if 条件头后面。
        elif str_remainder:

            # 把条件头同行残留的主体文本交给 inline 控制流解析器，并暂存返回结果。
            tuple_inline_body_result = func_parse_inline(lines, int_body_start - 1, str_remainder, context)  # if 头同行主体结果

            # 从 inline 主体解析结果里拆出子节点列表和结束位置。
            list_children, int_next_index = tuple_inline_body_result  # 条件头同行主体对应的节点列表与结束位置

        # 头部后没有剩余文本时，需要到下一条有效语句里找主体。
        else:

            # 跳过空白和注释，定位真正的主体起点。
            int_lookahead = self._skip_ignorable_control_lines(lines, int_body_start)  # if 主体实际起点

            # lookahead 仍在源码范围内时，尝试识别不同主体形态。
            if int_lookahead < len(lines):

                # 规范化主体首行后再判断其结构。
                str_next_statement = self._normalize_statement_line(lines[int_lookahead].strip())  # if 主体首行

                # begin 头说明主体是显式 begin/end block。
                if self._is_begin_header(str_next_statement):

                    # 记录 lookahead begin 是否显式给出了标签。
                    str_label = self._extract_block_label(str_next_statement)  # lookahead begin 头里的标签名

                    # 递归解析 lookahead begin/end 对应的主体子树，并暂存返回结果。
                    tuple_begin_body_result = func_parse_nodes(lines, int_lookahead + 1, {"end"}, context)  # lookahead begin 主体的递归解析结果

                    # 取出 lookahead begin 体的子节点和对应闭合位置。
                    list_children, int_next_index = tuple_begin_body_result  # lookahead begin 主体节点列表与返回位置

                    # 跳过 begin 体尾部的空白与注释，把游标落到真正的 end 候选行。
                    int_next_index = self._skip_ignorable_control_lines(lines, int_next_index)  # lookahead begin 对应的 end 候选行

                    # 缺少 end 会让 lookahead begin 主体无法可靠闭合。
                    if int_next_index >= len(lines) or not self._matches_terminator(
                        self._normalize_statement_line(lines[int_next_index].strip()),
                        {"end"},
                    ):

                        # 条件分支主体中的 begin block 必须先补齐 end。
                        self._raise_control_error(
                            self._control_shape_category(context),
                            str_if_header,
                            "Close every if/else block with 'end' before formatting.",
                        )

                    # 消费掉 lookahead begin 对应的 end 后，把游标推进到后续 else 检查起点。
                    int_next_index += 1  # lookahead begin 对应 end 之后的下一行位置

                    # 用显式 begin/end 主体构造一个完整的 if 节点。
                    node_if = ControlNode(kind="if", header=str_header, label=str_label, children=list_children)  # 已绑定 lookahead begin 主体的 if 节点

                    # 继续拼接这个 begin 主体后面可能跟随的 alternate 链。
                    int_next_index = self._attach_if_alternate(node_if, lines, int_next_index, context)  # 挂接 alternate 后的 if 结束位置

                    # 返回已经处理完 alternate 的 if 节点。
                    return node_if, int_next_index

                # case 作为 if 主体时，case 节点就是唯一子节点。
                if str_next_statement.startswith("case"):

                    # 解析当前条件分支主体中命中的 case 结构。
                    node_case, int_next_index = self._parse_case_node(lines, int_lookahead, context)  # 当前条件分支主体中的 case 节点与结束位置

                    # 用单个 case 节点作为当前 if 的主体。
                    list_children = [node_case]  # 仅包含 case 子节点的 if 主体

                    # 为 case 主体额外包一层 if，保持后续 alternate 挂接路径统一。
                    node_if = ControlNode(kind="if", header=str_header, label=str_label, children=list_children)  # 以 case 为唯一主体的 if 节点

                    # 沿统一 alternate 路径继续消费 case 主体后面的 else 或 else if。
                    int_next_index = self._attach_if_alternate(node_if, lines, int_next_index, context)  # case 主体补挂 alternate 后的结束位置

                    # 这一分支已经得到完整的 if/case/alternate 组合节点。
                    return node_if, int_next_index

                # lookahead 仍然是 if 时，说明主体是嵌套 if。
                if str_next_statement.startswith("if ") or str_next_statement.startswith("if("):

                    # 递归解析内嵌的 if 子节点。
                    node_child_if, int_next_index = self._parse_if_node(lines, int_lookahead, context)  # if 主体嵌套 if

                    # 把内嵌 if 作为当前 if 的唯一子节点。
                    list_children = [node_child_if]  # 仅包含嵌套条件节点的 if 主体

                    # 把嵌套条件分支收束为父层 if 节点，统一复用 alternate 挂接路径。
                    node_if = ControlNode(kind="if", header=str_header, label=str_label, children=list_children)  # 以嵌套条件节点作为主体的父层 if 节点

                    # 继续挂接嵌套条件主体后面的 alternate 分支。
                    int_next_index = self._attach_if_alternate(node_if, lines, int_next_index, context)  # 嵌套条件主体挂接 alternate 后的结束位置

                    # 返回父层 if 节点及其结束位置。
                    return node_if, int_next_index

            # 到文件末尾仍然没有找到主体时，应立即报错。
            if int_body_start >= len(lines):

                # 缺少主体的 if 不能靠 formatter 猜测补全。
                self._raise_control_error(
                    "unsupported_shape",
                    str_if_header,
                    "Provide a body for each if/else branch before formatting.",
                )

            # 把普通单语句主体收拢成叶子节点文本，供当前 if 的兜底路径使用。
            str_single_statement, int_next_index = self._collect_statement_text(lines, int_body_start)  # 从 lookahead 起点收集到的 if 单语句主体

            # 把普通单语句主体包装成统一的 statement 子节点。
            list_children = [ControlNode(kind="statement", text=str_single_statement)]  # 单 statement 形式的 if 主体节点

        # 在所有主体分支收束完成后统一构造通用 if 节点。
        node_if = ControlNode(kind="if", header=str_header, label=str_label, children=list_children)  # 等待挂接 alternate 的通用 if 节点

        # 用统一路径补挂这个 if 节点后面的 else / else if 分支。
        int_next_index = self._attach_if_alternate(node_if, lines, int_next_index, context)  # 完整 if 链挂接 alternate 后的结束位置

        # 返回完整 if 节点和消费后的下一行位置。
        return node_if, int_next_index

    # 解析 else 分支主体。
    def _parse_else_branch(
        self,
        lines: list[str],
        start: int,
        context: str,
        else_line: str,
    ) -> tuple[list[ControlNode], int]:
        """
        解析 else 或 else if 分支。

        参数:
            lines: 控制流源码行列表。
            start: else 所在的起始下标。
            context: 当前 procedural 或 generate 上下文。
            else_line: 已规范化的 else 或 else if 头部文本。
        返回:
            tuple[list[ControlNode], int]: else 分支节点列表，以及消费后的下一行位置。
        异常:
            VerilogFormatterError: else 主体不闭合或形态不稳定时抛出。
        """

        # 声明 else 同行主体解析器别名的可调用类型。
        func_parse_inline: Callable[[list[str], int, str, str], tuple[list[ControlNode], int]]  # else 同行主体解析使用的 callable 类型

        # 缓存 else 同行主体解析器，避免 inline else 主体调用行过长。
        func_parse_inline = self._parse_inline_control_remainder  # else 同行主体解析器引用

        # 声明跨行 else begin 递归入口别名的可调用类型。
        func_parse_nodes: Callable[[list[str], int, set[str], str], tuple[list[ControlNode], int]]  # 跨行 else begin 递归使用的 callable 类型

        # 缓存跨行 else begin 递归入口，避免 else block 调用行过长。
        func_parse_nodes = self._parse_control_nodes  # 跨行 else begin 递归入口引用

        # else if 本质上复用 if 解析器处理。
        if self._is_else_if_header(else_line):

            # 当前 else if 与源码行完全一致时，可直接在原数组中递归解析。
            if else_line == self._normalize_statement_line(lines[start].strip()):

                # 递归构造 else if 节点。
                node_alternate_if, int_next_index = self._parse_if_node(lines, start, context)  # 原地 else if 节点

                # else if 作为 alternate 链中的单节点列表返回。
                return [node_alternate_if], int_next_index

            # 同行拼接出的 else if 需要先合成虚拟片段列表。
            list_synthetic_lines = [else_line, *lines[start + 1 :]]  # 供 else if 递归解析的虚拟源码

            # 在虚拟数组里递归解析 else if。
            node_alternate_if, int_consumed = self._parse_if_node(list_synthetic_lines, 0, context)  # 虚拟 else if 节点

            # 把虚拟数组消费量换算回原始源码下标。
            return [node_alternate_if], start + int_consumed

        # else 之后紧跟的剩余文本用于判断 begin、inline body 或 lookahead body。
        str_remainder = else_line[len("else") :].strip()  # else 头之后的剩余文本

        # remainder 以 begin 开头时按 begin/end 体处理。
        if str_remainder.startswith("begin"):

            # 记录 else 分支 begin 体携带的可选命名块标签。
            str_alt_label = self._extract_block_label(str_remainder)  # else begin 头里声明的命名块标签

            # 递归解析 else begin 体内部节点，并暂存完整返回结果。
            tuple_else_begin_result = func_parse_nodes(lines, start + 1, {"end"}, context)  # else begin 体的递归解析结果

            # 取出 else begin 体里的子节点以及匹配到的 end 位置。
            list_alt_children, int_next_index = tuple_else_begin_result  # else begin 主体节点与对应 end 位置

            # 缺少 end 时 else 分支边界不稳定。
            if int_next_index >= len(lines) or not self._matches_terminator(
                self._normalize_statement_line(lines[int_next_index].strip()),
                {"end"},
            ):

                # else begin/end 必须先补齐 end 后再交给 formatter。
                self._raise_control_error(
                    self._control_shape_category(context),
                    else_line,
                    "Close every else branch with 'end' before formatting.",
                )

            # 返回显式 label 的 else 节点和 end 之后的位置。
            return [ControlNode(kind="else", label=str_alt_label, children=list_alt_children)], int_next_index + 1

        # 同行紧跟的内容存在时按 inline control body 继续解析。
        if str_remainder:

            # 把 else 后的内联主体交给专用解析器处理，并暂存完整返回结果。
            tuple_inline_else_body_result = func_parse_inline(lines, start, str_remainder, context)  # else 同行主体的解析结果

            # 取出 else 同行主体对应的节点列表和结束位置。
            list_children, int_next_index = tuple_inline_else_body_result  # else 同行主体节点与结束位置

            # 返回普通 else 节点及其结束位置。
            return [ControlNode(kind="else", children=list_children)], int_next_index

        # 略过空白和注释后，定位 else 真正开始承载主体的位置。
        int_lookahead = self._skip_ignorable_control_lines(lines, start + 1)  # else 跨行主体的首个有效语句位置

        # lookahead 仍然有效时，继续识别主体形态。
        if int_lookahead < len(lines):

            # 规范化主体首行，便于判断 begin/case/if。
            str_next_statement = self._normalize_statement_line(lines[int_lookahead].strip())  # else 主体首条有效语句文本

            # 显式 begin 头说明 else 主体是 begin/end block。
            if self._is_begin_header(str_next_statement):

                # 提取 lookahead else begin 声明出来的命名块标签。
                str_alt_label = self._extract_block_label(str_next_statement)  # lookahead else begin 的命名块标签

                # 跨行 else 的 begin/end 主体需要回到通用控制树解析器中递归展开。
                tuple_lookahead_else_begin_result = func_parse_nodes(lines, int_lookahead + 1, {"end"}, context)  # 跨行 else begin 结果

                # 取出跨行 else begin 主体对应的子节点，以及原数组中命中的 end 行位置。
                list_alt_children, int_next_index = tuple_lookahead_else_begin_result  # 跨行 else begin 主体节点与 end 行位置

                # 缺少 end 时说明 else begin 体还不稳定。
                if int_next_index >= len(lines) or not self._matches_terminator(
                    self._normalize_statement_line(lines[int_next_index].strip()),
                    {"end"},
                ):

                    # else block 的 begin/end 必须先闭合。
                    self._raise_control_error(
                        self._control_shape_category(context),
                        else_line,
                        "Close every else branch with 'end' before "
                        "formatting.",
                    )

                # 把 begin 体封装成显式命名块形式的 else 节点。
                return [ControlNode(kind="else", label=str_alt_label, children=list_alt_children)], int_next_index + 1

            # case 作为 else 主体时，把 case 节点挂入 else。
            if str_next_statement.startswith("case"):

                # 把 else 主体首条语句识别为 case 后，递归提取整个 case 结构。
                node_case, int_next_index = self._parse_case_node(lines, int_lookahead, context)  # else 主体命中的 case 节点与其结束位置

                # 返回仅包含 case 子节点的 else 节点。
                return [ControlNode(kind="else", children=[node_case])], int_next_index

            # 条件分支作为 else 主体时继续递归解析条件树。
            if str_next_statement.startswith("if ") or str_next_statement.startswith("if("):

                # 把 else 主体首条语句识别为条件分支后，递归提取整棵子条件树。
                node_child_if, int_next_index = self._parse_if_node(lines, int_lookahead, context)  # else 主体里的嵌套 if 节点与其结束位置

                # 返回仅包含嵌套 if 的 else 节点。
                return [ControlNode(kind="else", children=[node_child_if])], int_next_index

        # 把非控制流形态的 else 主体收集成一条完整单语句。
        str_single_statement, int_next_index = self._collect_statement_text(lines, start + 1)  # else 跨行单语句主体与其结束位置

        # 用普通 statement 子节点承载不含控制流关键字的 else 主体。
        return [
            ControlNode(
                kind="else",
                children=[ControlNode(kind="statement", text=str_single_statement)],
            )
        ], int_next_index

    # 解析 case/casez/casex 节点及其 item 列表。
    def _parse_case_node(self, lines: list[str], start: int, context: str) -> tuple[ControlNode, int]:
        """
        解析 case 节点及其各个 item 主体。

        参数:
            lines: 控制流源码行列表。
            start: case 头部起始下标。
            context: 当前 procedural 或 generate 上下文。
        返回:
            tuple[ControlNode, int]: 解析完成的 case 节点，以及消费后的下一行位置。
        异常:
            VerilogFormatterError: case item 缺少主体、begin/end 不闭合或 endcase 缺失时抛出。
        """

        # 规范化 case 头部文本，作为 ControlNode 的 header。
        str_header = self._normalize_statement_line(lines[start].strip())  # case 头部文本

        # 构造空的 case 节点，后续逐个追加 item。
        node_case = ControlNode(kind="case", header=str_header)  # 当前 case 控制节点

        # index 指向下一个待分析的 case item 行。
        int_index = start + 1  # case item 扫描游标

        # 逐行解析 case item，直到命中 endcase。
        while int_index < len(lines):

            # 当前行先做控制流层面的规范化。
            str_statement = self._normalize_statement_line(lines[int_index].strip())  # case item 原始语句文本

            # 去掉行尾注释后再判断真正的 case item 语义。
            tuple_comment_split = self._split_comment(str_statement)  # case item 去注释后的文本

            # 取出去注释后的正文部分。
            str_statement = tuple_comment_split[0].strip()  # case item 正文文本

            # 空白行不构成 item 主体，直接跳过。
            if not str_statement:

                # 继续寻找下一条有效 case item。
                int_index += 1  # case item 的下一行位置

                # 空白行不产生任何节点。
                continue

            # 纯行注释不参与 case item 解析。
            if str_statement.startswith("//"):

                # 注释行直接略过。
                int_index += 1  # 行注释后的下一行位置

                # 当前 case item 仍未开始。
                continue

            # 遇到 endcase 时结束当前 case 节点。
            if str_statement.startswith("endcase"):

                # 返回构造完成的 case 节点和 endcase 之后的位置。
                return node_case, int_index + 1

            # begin 形式的 case item 需要递归解析其内部节点。
            match_begin_item = re.match(r"^(.+?)\s*:\s*begin(?:\s*:\s*(\w+))?$", str_statement)  # begin case item 匹配结果

            # 命中 <item>: begin[:label] 形态时进入 begin/end 分支。
            if match_begin_item:

                # 递归提取当前 begin 形态 case item 内部的控制流节点。
                list_children, int_next_index = self._parse_control_nodes(lines, int_index + 1, {"end"}, context)  # case item begin 子树与结束位置

                # begin 体结束位置必须指向与之配对的 end。
                if int_next_index >= len(lines) or not self._matches_terminator(
                    self._normalize_statement_line(lines[int_next_index].strip()),
                    {"end"},
                ):

                    # generate item 和普通 case item 需要落到不同的异常类别。
                    if context == "generate":

                        # generate 域里的 case item 继续沿用 generate 异常类别。
                        str_category = "generate_normalization_violation"  # generate 域的 case item 异常类别

                    # 普通 case 路径需要切换到专门的 case 归一化异常类别。
                    else:

                        # 普通 case item 使用专门的 case 归一化异常类别。
                        str_category = "case_normalization_violation"  # 普通 case item 的异常类别

                    # 缺少 end 时当前 case item block 不可安全格式化。
                    self._raise_control_error(
                        str_category,
                        str_statement,
                        "Close every case item block with 'end' before formatting.",
                    )

                # 把 begin item 的 label 与子节点封装成 CaseItem。
                node_case.items.append(
                    CaseItem(
                        match_begin_item.group(1),
                        list_children,
                        match_begin_item.group(2) or "",
                    )
                )

                # 跳到这个 begin case item 结束后的下一条源码继续扫描。
                int_index = int_next_index + 1  # 当前 begin case item 收尾后的下一行位置

                # 当前 begin case item 已经解析完毕。
                continue

            # 先把单行 case item 拆成选择标签和右侧主体文本。
            match_inline_item = re.match(r"^(.+?)\s*:\s*(.+)$", str_statement)  # 当前 case 行是否命中 inline item 结构

            # 命中 <item>: <body> 形态时继续判断主体结构。
            if match_inline_item:

                # 提取冒号左侧保留的 case 选择标签。
                str_label = match_inline_item.group(1)  # 当前 inline case item 的选择标签

                # 提取 label 之后的主体文本。
                str_item_body = match_inline_item.group(2).strip()  # case item 主体文本

                # 控制流主体需要交给单控制节点解析器处理。
                if str_item_body.startswith(("if ", "if(", "case", "for", "while", "else")):

                    # 把 inline case item 主体和后续源码拼成虚拟片段数组，供控制节点解析器复用。
                    list_item_fragments = [str_item_body, *lines[int_index + 1 :]]  # inline 控制流 case item 的虚拟源码片段

                    # 在虚拟片段数组里递归恢复 inline 控制流 case item。
                    list_child_nodes, int_consumed = self._parse_single_control_node(list_item_fragments, 0, context)  # inline 控制流 case item 的子节点与消费数

                    # 把控制流主体包装为对应的 CaseItem。
                    node_case.items.append(CaseItem(str_label, list_child_nodes))

                    # 把虚拟消费数换算为原始源码游标。
                    int_index += int_consumed  # inline 控制流 case item 结束后的下一行位置

                    # 当前 inline 控制流 case item 已处理完毕。
                    continue

                # 把 inline case item 的单语句主体与后续源码拼成统一片段数组。
                list_item_fragments = [str_item_body, *lines[int_index + 1 :]]  # inline 单语句 case item 的虚拟源码片段

                # 在统一片段数组里收敛出一条可独立落地的 case item 单语句文本。
                str_statement_text, int_consumed = self._collect_statement_text_from_fragments(list_item_fragments, 0)  # inline case item 收敛出的完整单语句

                # 单语句 case item 必须以顶层分号稳定结束。
                if not self._statement_has_top_level_semicolon(str_statement_text):

                    # 缺少分号时不能可靠识别 case item 边界。
                    self._raise_control_error(
                        "case_normalization_violation",
                        str_statement,
                        "Terminate each single-statement case item with ';' before formatting.",
                    )

                # 把单语句主体封装成 CaseItem。
                node_case.items.append(
                    CaseItem(
                        str_label,
                        [ControlNode(kind="statement", text=str_statement_text)],
                    )
                )

                # 更新原始源码游标到当前 item 结束之后。
                int_index += int_consumed  # 单语句 case item 结束后的下一行位置

                # 当前 inline 单语句 case item 已完成。
                continue

            # 仅有 label、主体另起行时，需要向后收集一个完整 item 片段。
            match_label_only = re.match(r"^(.+?)\s*:\s*$", str_statement)  # 仅含 label 的 case item 匹配结果

            # 命中 label-only item 时，再向后收集其主体块。
            if match_label_only:

                # 保存冒号左侧单独占行的 case item 标签。
                str_label = match_label_only.group(1)  # label-only case item 的选择标签

                # 跳过空白和注释，定位真正的 item 主体。
                int_lookahead = self._skip_ignorable_control_lines(lines, int_index + 1)  # label-only item 主体起点

                # 紧跟 endcase 或文件结束都意味着当前 item 没有主体。
                if int_lookahead >= len(lines):

                    # case item 至少要有一条稳定主体语句。
                    self._raise_control_error(
                        "case_normalization_violation",
                        str_statement,
                        "Each case item must contain at least one statement.",
                    )

                # 收集 label-only case item 对应的整段主体源码片段。
                list_body_lines, int_next_index = self._collect_case_item_lines(lines, int_lookahead)  # label-only item 片段与结束位置

                # 主体片段为空时说明当前 item 仍然不完整。
                if not list_body_lines:

                    # 空主体 item 不能继续进入 control parser。
                    self._raise_control_error(
                        "case_normalization_violation",
                        str_statement,
                        "Each case item must contain at least one statement.",
                    )

                # 在局部片段数组里递归恢复 label-only case item 的控制流节点。
                list_children, int_consumed = self._parse_control_nodes(list_body_lines, 0, set(), context)  # label-only item 的节点列表与消费数

                # 未完全消费 body 片段时说明 item 仍有不稳定结构。
                if int_consumed != len(list_body_lines):

                    # 当前 item 必须先改写成稳定 begin/end 或单语句形态。
                    self._raise_control_error(
                        "case_normalization_violation",
                        str_statement,
                        "Use '<item>: begin ... end' or a stable single-statement case item before formatting.",
                    )

                # 把多行主体封装成当前 label 的 CaseItem。
                node_case.items.append(CaseItem(str_label, list_children))

                # 把局部片段消费位置折返为原始源码数组里的下一个 item 起点。
                int_index = int_next_index  # label-only item 对应的原始源码续扫位置

                # 当前 label-only case item 已经处理完成。
                continue

            # 既不匹配 begin item、inline item，也不匹配 label-only item 时直接报错。
            self._raise_control_error(
                "case_normalization_violation",
                str_statement,
                "Use '<item>: begin ... end' or '<item>: statement;' style case items.",
            )

        # 扫描到文件末尾仍未遇到 endcase，说明 case block 没有闭合。
        self._raise_control_error(
            "generate_normalization_violation" if context == "generate" else "case_normalization_violation",
            str_header,
            "Close every case block with endcase before formatting.",
        )
