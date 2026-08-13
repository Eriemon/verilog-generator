"""提供控制流头部、语句与 case item 片段收集。"""

# annotations 延后解析收集器方法的类型标注。
from __future__ import annotations

# 正则工具用于识别控制头部与 case item 边界。
import re

# ControlNodeCollectorsMixin 汇总不依赖共享状态的文本片段收集器。
class ControlNodeCollectorsMixin:
    """集中维护控制流解析所需的无状态片段收集器。"""

    # 在头部文本中寻找与指定开括号配对的闭括号。
    def _find_balanced_close_index(self, text: str, open_index: int) -> int:
        """
        在控制流头部中定位成对括号的闭合位置。

        参数:
            text: 需要扫描的单行控制流头部文本。
            open_index: 首个左括号在文本中的下标。
        返回:
            int: 与首个左括号配对的右括号下标；未找到时返回 -1。
        """

        # depth 记录从指定左括号开始的嵌套层数。
        int_depth = 0  # 当前括号嵌套深度

        # 逐字符扫描，直到找到与首括号配对的闭括号。
        for int_index, str_char in enumerate(text[open_index:], start=open_index):

            # 新遇到左括号时增加嵌套层数。
            if str_char == "(":

                # 新左括号会让待匹配的嵌套层数向内推进一层。
                int_depth = int_depth + 1  # 左括号增加后的括号深度

            # 右括号则尝试关闭当前层。
            elif str_char == ")":

                # 当前右括号会抵消掉最近打开的一层括号深度。
                int_depth = int_depth - 1  # 右括号消耗后的括号深度

                # 深度回到零时说明当前控制头已经闭合。
                if int_depth == 0:

                    # 返回与首括号配对的闭括号位置。
                    return int_index

        # 扫描结束仍未闭合时返回 -1 供调用方处理。
        return -1

    # 判断多行收集到的 if 头部是否已经闭合。
    def _if_condition_is_closed(self, text: str) -> bool:
        """
        判断 if 条件头的括号是否已经完整闭合。

        参数:
            text: 已拼接的 if 或 else if 头部文本。
        返回:
            bool: 条件括号已经闭合时返回 True，否则返回 False。
        """

        # 去掉外围空白，统一处理 else if 变体。
        str_working = text.strip()  # 待检测的 if 条件头文本

        # else if 只保留真正参与括号匹配的 if 部分。
        if str_working.startswith("else "):

            # 移除 else 之后再判断 if 条件闭合状态。
            str_working = str_working[len("else ") :].strip()  # 去掉 else 的 if 条件文本

        # 不是 if 开头的文本不需要本函数判定括号闭合。
        if not str_working.startswith("if"):

            # 非 if 文本视为无需补全括号。
            return True

        # 查找条件表达式第一个左括号所在的位置。
        int_open_index = str_working.find("(")  # if 条件起始括号的字符下标

        # 没有左括号时说明条件头还未成形。
        if int_open_index == -1:

            # 当前文本尚不足以判定为闭合的 if 条件。
            return False

        # 括号匹配成功时说明条件头已经闭合。
        return self._find_balanced_close_index(str_working, int_open_index) != -1

    # 收集跨多行书写的 if 条件头。
    def _collect_if_header(self, lines: list[str], start: int) -> tuple[str, int]:
        """
        从多行源码中收集完整的 if 条件头文本。

        参数:
            lines: 控制流源码行列表。
            start: if 头部起始行下标。
        返回:
            tuple[str, int]: 拼接后的 if 头部文本，以及主体开始前的下一行下标。
        异常:
            VerilogFormatterError: if 条件括号直到源码结束都未闭合时抛出。
        """

        # 逐行收集去掉注释后的 if 头部片段。
        list_collected: list[str] = []  # if 头部文本片段

        # index 指向当前正在拼接的源码行。
        int_index = start  # if 头部扫描游标

        # 持续向后收集，直到条件头完整闭合。
        while int_index < len(lines):

            # 去掉当前行尾注释，避免括号匹配被注释内容干扰。
            str_raw, _ = self._split_comment(self._normalize_statement_line(lines[int_index].strip()))  # 当前行去注释后的源码正文与注释尾部

            # 非空片段才并入 if 头部缓冲区。
            if str_raw.strip():

                # 保留去注释后的有效源码片段。
                list_collected.append(str_raw.strip())

            # 把已收集片段拼成一条逻辑上的 if 头部。
            str_joined = " ".join(list_collected)  # 当前累计的 if 头部文本

            # 条件头一旦闭合，就返回主体起始位置。
            if str_joined and self._if_condition_is_closed(str_joined):

                # 返回完整 if 头部与主体开始行号。
                return str_joined, int_index + 1

            # 继续读取下一行补全 if 条件头。
            int_index += 1  # 下一条待收集的源码行

        # 扫描到文件末尾仍未闭合时必须阻断。
        self._raise_control_error(
            "unsupported_shape",
            lines[start].strip(),
            "Balance the if-condition parentheses before formatting.",
        )

    # 略过空白行与行注释，定位下一条真实控制语句。
    def _skip_ignorable_control_lines(self, lines: list[str], index: int) -> int:
        """
        跳过空白行和行注释，返回下一条有效控制语句位置。

        参数:
            lines: 控制流源码行列表。
            index: 起始扫描下标。
        返回:
            int: 第一条非空且非行注释语句的下标；若不存在，则返回源码长度。
        """

        # 让扫描游标从调用方指定的位置起步，逐行向后寻找有效控制语句。
        int_index = index  # 跳过空白和注释时的扫描游标

        # 逐行前进直到遇到有效控制语句或源码末尾。
        while int_index < len(lines):

            # 规范化当前行后判断它是否可以忽略。
            str_statement = self._normalize_statement_line(lines[int_index].strip())  # 当前待判断语句文本

            # 空白行和行注释都不属于有效控制语句。
            if not str_statement or str_statement.startswith("//"):

                # 当前物理行不承载控制语义时，扫描继续滑到下一条候选源码行。
                int_index += 1  # 下一条待检查的源码行

                # 当前行不会成为控制结构入口。
                continue

            # 找到第一条有效控制语句后立即停止。
            break

        # 返回跳过空白和注释后的最终位置。
        return int_index

    # 收集 label-only case item 的连续主体片段。
    def _collect_case_item_lines(self, lines: list[str], start: int) -> tuple[list[str], int]:
        """
        为多行 case item 收集直到下一个 item 或 endcase 之前的源码片段。

        参数:
            lines: 控制流源码行列表。
            start: 当前 case item 主体的起始下标。
        返回:
            tuple[list[str], int]: 收集到的源码片段列表，以及原始源码中的结束位置。
        """

        # 当前 case item 的源码片段按原顺序保存下来。
        list_collected: list[str] = []  # 当前 case item 的源码片段

        # index 指向当前正在读取的原始源码行。
        int_index = start  # case item 片段扫描游标

        # block_depth 用于避免 begin/end 嵌套体内的 label 误判。
        int_block_depth = 0  # begin/end 嵌套深度

        # case_depth 用于避免内层 case 过早截断外层 item 收集。
        int_case_depth = 0  # 嵌套 case 的当前深度

        # 持续收集，直到遇到外层同级的下一个 item 或 endcase。
        while int_index < len(lines):

            # 规范化当前行，便于做 begin/end 和 case 深度判断。
            str_statement = self._normalize_statement_line(lines[int_index].strip())  # 当前 case item 片段文本

            # 只有回到外层深度且已经收集过内容后，新的 label 或 endcase 才意味着停止。
            if (
                str_statement
                and not str_statement.startswith("//")
                and list_collected
                and int_block_depth == 0
                and int_case_depth == 0
            ):

                # 下一条同级 item 或 endcase 出现时，当前 item 收集到此结束。
                if str_statement.startswith("endcase") or re.match(r"^(.+?)\s*:\s*", str_statement):

                    # 当前 case item 的源码片段已经收集完整。
                    break

            # 把原始物理行原样加入当前 item 片段列表。
            list_collected.append(lines[int_index].strip())

            # 只有非空且非注释行才参与嵌套深度统计。
            if str_statement and not str_statement.startswith("//"):

                # 嵌套 case 会额外推高 case 深度。
                if re.match(r"^(?:case|casez|casex)\b", str_statement):

                    # 新进入一层内嵌 case 时同步增加深度。
                    int_case_depth = int_case_depth + 1  # 进入内层 case 后的深度

                # endcase 会关闭一层 case 深度。
                elif str_statement.startswith("endcase"):

                    # 读到 endcase 时把内嵌 case 深度回退一层。
                    int_case_depth = max(0, int_case_depth - 1)  # endcase 回退后的 case 深度

                # 非 endcase 行再根据 begin/end 调整 block 深度。
                if not str_statement.startswith("endcase"):

                    # 先统计当前行里显式写出的 begin 数量。
                    int_begin_count = len(re.findall(r"\bbegin\b", str_statement))  # 当前行里的 begin 数量

                    # 再记录同一行里会抵消 begin 深度增长的 end 数量。
                    int_end_count = len(re.findall(r"\bend\b", str_statement))  # 当前行内抵消 begin 的 end 数量

                    # 用 begin 与 end 的差值表达这一行对 block 深度的净影响。
                    int_depth_delta = int_begin_count - int_end_count  # 当前行对 block 深度的净变化

                    # 把这一行引入的嵌套变化合并回累计 block 深度。
                    int_block_depth = int_block_depth + int_depth_delta  # 更新后的 case item block 深度

            # 当前物理行已经并入 case item 片段列表，扫描继续滑到下一条候选源码行。
            int_index += 1  # case item 片段扫描推进后的下一行位置

        # 返回当前 item 收集到的所有片段及其结束位置。
        return list_collected, int_index

    # 收集从指定位置开始的完整单语句文本。
    def _collect_statement_text(self, lines: list[str], start: int) -> tuple[str, int]:
        """
        从原始源码行列表中收集一条完整的单语句文本。

        参数:
            lines: 原始源码行列表。
            start: 当前语句的起始下标。
        返回:
            tuple[str, int]: 聚合后的单语句文本，以及消费后的下一行位置。
        """

        # 直接复用基于片段数组的语句收集器。
        return self._collect_statement_text_from_fragments(lines, start)
