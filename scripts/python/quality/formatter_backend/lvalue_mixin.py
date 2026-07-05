"""为 VerilogFormatterEngine 提供过程赋值左值提取能力。"""

# 延迟类型注解求值，避免 mixin 导入时解析前向类型。
from __future__ import annotations
# re 用于识别 Verilog 标识符、assign 语句和后缀边界。
import re
# NoReturn 标明严格错误 helper 一定抛出异常。
from typing import NoReturn
# LValueRef 承载左值结构，VerilogFormatterError 保持 formatter 统一异常类型。
from .models import LValueRef, VerilogFormatterError

# 左值 mixin 只依赖宿主 formatter 提供的分割、注释剥离和严格错误接口。
class LValueMixin:
    """解析 Verilog 过程赋值、assign 语句和拼接左值。"""

    # _raise_lvalue_error 统一包装左值解析失败的错误前缀。
    def _raise_lvalue_error(self, category: str, source_text: str, suggestion: str) -> NoReturn:
        """抛出带项目统一前缀的左值解析错误。

        :param category: 严格错误分类。
        :param source_text: 触发错误的原始文本。
        :param suggestion: 面向用户的修复建议。
        :return: 此函数不会正常返回。
        :raises VerilogFormatterError: 始终通过宿主严格错误接口抛出。
        """

        # 摘要复用宿主格式，避免错误消息打印过长源语句。
        str_summary = self._summarize_statement(source_text)  # 压缩后的错误定位语句。

        # 直接抛统一异常类，同时让消息满足 current-project 输出前缀门禁。
        raise VerilogFormatterError(
            f"> ERR: [Python] Strict mode [{category}]: {str_summary}. Suggestion: {suggestion}"
        )

    # _parse_lvalue 是过程赋值和 assign 共用的左值解析入口。
    def _parse_lvalue(
        self,
        text: str,
        category: str,
        suggestion: str,
        *,
        allow_concat: bool = False,
    ) -> LValueRef:
        """把左值文本解析成结构化引用。

        :param text: 待解析的左值文本。
        :param category: 严格错误分类。
        :param suggestion: 解析失败时给用户的修复建议。
        :param allow_concat: 是否允许顶层拼接左值。
        :return: 结构化左值引用。
        :raises VerilogFormatterError: 左值格式不满足 formatter 可稳定处理的子集。
        """

        # 先去掉调用方可能保留的语句分号和外侧空白。
        str_candidate = text.strip().rstrip(";")  # 规整后的左值候选文本。

        # 顶层拼接左值需要递归解析每个成员。
        if allow_concat and str_candidate.startswith("{") and str_candidate.endswith("}"):

            # 去掉外层花括号后再按顶层逗号切分成员。
            str_inner = str_candidate[1:-1].strip()  # 拼接成员文本。

            # 空拼接没有可提取目标，不能继续生成空引用。
            if not str_inner:

                # 空花括号说明赋值目标缺失。
                self._raise_lvalue_error(category, text, suggestion)

            # 拼接成员列表保持原始顺序，便于后续目标分析稳定。
            list_members: list[LValueRef] = []  # 拼接左值中的成员引用。

            # 按顶层逗号切分，保留成员内部括号或切片表达式。
            for str_member_text in self._split_top_level(str_inner, ","):

                # 每个拼接成员本身只能是普通左值，避免无限嵌套拼接。
                l_value_ref_lvalueref_member: LValueRef = self._parse_lvalue(  # 当前拼接成员的结构化左值。
                    str_member_text,  # 当前拼接成员原文。
                    category,  # 严格错误分类沿用调用方上下文。
                    suggestion,  # 成员解析失败时复用同一修复建议。
                    allow_concat=False,  # 拼接内部成员不再允许嵌套拼接。
                )

                # 保持成员扫描顺序，后续 base 展开时不重新排序。
                list_members.append(l_value_ref_lvalueref_member)

            # 非空文本理论上至少产生一个成员，这里保护宿主分割器异常返回。
            if not list_members:

                # 非空拼接未产生成员时说明分割结果不可用。
                self._raise_lvalue_error(category, text, suggestion)

            # 拼接左值没有单一 base，成员列表承载真实目标信号。
            return LValueRef(
                text=str_candidate,
                base="",
                kind="concat",
                is_complex=True,
                members=list_members,
            )

        # base 正则只接受常规 Verilog 标识符，并把剩余文本视为后缀。
        match_lvalue = re.match(r"^([A-Za-z_]\w*)(.*)$", str_candidate)  # base 与后缀的匹配结果。

        # 无合法 base 标识符时，formatter 无法稳定定位赋值目标。
        if not match_lvalue:

            # 非标识符起始的左值需要调用方先规整。
            self._raise_lvalue_error(category, text, suggestion)

        # group(1) 是赋值目标的基础信号名。
        str_base = match_lvalue.group(1)  # 左值基础信号名。

        # group(2) 是可选索引、切片或 indexed part-select 后缀。
        str_suffix = match_lvalue.group(2).strip()  # base 之后的后缀文本。

        # 没有后缀时属于最简单的信号赋值目标。
        if not str_suffix:

            # 简单左值不需要成员列表，也不标记复杂结构。
            return LValueRef(text=str_base, base=str_base, kind="simple", is_complex=False)

        # 后缀拆分结果用于判断 index、slice 或 indexed part-select。
        list_suffix_parts = self._split_lvalue_suffix_parts(str_suffix)  # 完整 [] 后缀片段。

        # 后缀必须全部是成对方括号，不能混入方法调用或未知表达式。
        if not list_suffix_parts:

            # 非方括号后缀无法保证 Verilog 左值语义稳定。
            self._raise_lvalue_error(category, text, suggestion)

        # 默认把所有方括号后缀视为普通索引。
        str_lvalue_kind = "index"  # 当前左值分类。

        # 任一后缀包含顶层 part-select 标记时，需要提升整体左值种类。
        for str_suffix_part in list_suffix_parts:

            # 去掉当前 [] 后检查内部顶层操作符。
            str_suffix_inner = str_suffix_part[1:-1].strip()  # 当前后缀内部表达式。

            # Verilog indexed part-select 的 +: 优先级高于普通 slice 判断。
            if self._has_top_level_token(str_suffix_inner, "+:"):

                # 记录 +: 方向，供后续布局或分析区分。
                str_lvalue_kind = "part_select_plus"  # 当前左值为 +: part-select。

                # 已确定最高优先级类别，可以停止后缀扫描。
                break

            # Verilog indexed part-select 的 -: 同样需要单独保留方向信息。
            if self._has_top_level_token(str_suffix_inner, "-:"):

                # 保存递减方向 part-select，避免后续把它降级为普通切片。
                str_lvalue_kind = "part_select_minus"  # 递减 part-select 左值分类。

                # 递减 part-select 已经决定分类，后面的后缀不再改变结果。
                break

            # 普通冒号只在尚未发现 part-select 时标记为 slice。
            if str_lvalue_kind == "index" and self._has_top_level_token(str_suffix_inner, ":"):

                # slice 覆盖普通 index，但不会覆盖 part-select 分类。
                str_lvalue_kind = "slice"  # 当前左值为普通范围切片。

        # 复杂左值保留完整后缀文本，便于渲染阶段回看原始目标。
        return LValueRef(
            text=f"{str_base}{str_suffix}",
            base=str_base,
            kind=str_lvalue_kind,
            is_complex=True,
        )

    # _extract_lvalue_bases 递归提取拼接或普通左值覆盖的 base。
    def _extract_lvalue_bases(self, lvalue: LValueRef) -> list[str]:
        """提取左值引用中真实信号 base。

        :param lvalue: 结构化左值引用。
        :return: 当前左值覆盖到的 base 信号列表。
        """

        # 拼接左值需要展开每个成员，否则会丢失真实赋值目标。
        if lvalue.kind == "concat":

            # 拼接成员 base 需要按成员顺序累计。
            list_bases: list[str] = []  # 拼接成员展开后的 base 信号。

            # 递归展开拼接成员，支持成员自身带索引或切片。
            for lvalueref_member in lvalue.members:

                # extend 保留成员内部可能返回的多个 base。
                list_bases.extend(self._extract_lvalue_bases(lvalueref_member))

            # 返回拼接中所有成员的 base，调用方再决定是否去重。
            return list_bases

        # 简单或切片左值只有一个 base；空 base 仅出现在无法直接归属的结构。
        return [lvalue.base] if lvalue.base else []

    # _find_lvalue_start 从赋值运算符左侧片段反向定位 base 起点。
    def _find_lvalue_start(self, prefix: str) -> int | None:
        """在赋值运算符左侧片段中定位左值起点。

        :param prefix: 位于赋值运算符左侧的语句片段。
        :return: 左值起始下标；无法稳定定位时返回 None。
        """

        # 从左侧片段末尾开始反向扫描。
        int_index = len(prefix) - 1  # 当前反向扫描位置。

        # 赋值运算符之前的空白不属于左值文本。
        while int_index >= 0 and prefix[int_index].isspace():

            # 反向越过运算符左侧的尾部空白。
            int_index -= 1  # 去掉尾部空白后的扫描位置。

        # 全空片段没有候选左值。
        if int_index < 0:

            # 片段为空时调用方应进入严格错误路径。
            return None

        # 先跨过连续的索引或切片后缀，最终落到 base 标识符末尾。
        while int_index >= 0 and prefix[int_index] == "]":

            # 当前右方括号必须能找到同层左方括号。
            int_bracket_start = self._find_matching_square_bracket(prefix, int_index)  # 匹配左方括号位置。

            # 方括号不匹配时，不能可靠定位左值起点。
            if int_bracket_start is None:

                # 不完整后缀由上层转化为严格格式错误。
                return None

            # 跳到当前后缀之前，继续查找 base 或前一个后缀。
            int_index = int_bracket_start - 1  # 后缀之前的扫描位置。

            # 后缀和 base 之间允许出现空白。
            while int_index >= 0 and prefix[int_index].isspace():

                # 跳过后缀和 base 之间的分隔空白。
                int_index -= 1  # 回退到更靠近 base 的字符。

        # 当前扫描位置应停在 base 标识符最后一个字符。
        int_ident_end = int_index  # base 标识符结束位置。

        # 反向越过 Verilog 标识符字符，找到 base 起点前一位。
        while int_index >= 0 and (prefix[int_index].isalnum() or prefix[int_index] == "_"):

            # 持续回退直到离开标识符字符范围。
            int_index -= 1  # base 起点前的候选位置。

        # 起点是反向扫描停止位置的后一位。
        int_ident_start = int_index + 1  # base 标识符起始位置。

        # 没有扫描到任何标识符字符。
        if int_ident_start > int_ident_end:

            # 运算符左侧不是 formatter 支持的稳定左值。
            return None

        # base 必须满足 Verilog 常规标识符形态。
        if not re.fullmatch(r"[A-Za-z_]\w*", prefix[int_ident_start : int_ident_end + 1]):

            # 非常规标识符留给调用方报告严格错误。
            return None

        # 返回可直接切片 prefix 的起始位置。
        return int_ident_start

    # _find_matching_square_bracket 负责后缀反向扫描时的括号配对。
    def _find_matching_square_bracket(self, text: str, close_index: int) -> int | None:
        """从右方括号向左寻找匹配的左方括号。

        :param text: 待扫描文本。
        :param close_index: 右方括号所在下标。
        :return: 匹配左方括号下标；括号不平衡时返回 None。
        """

        # 反向扫描时，右括号会增加待匹配层级。
        int_depth = 0  # 方括号反向嵌套深度。

        # 从指定右括号开始向左走，直到当前层深度归零。
        for int_index in range(close_index, -1, -1):

            # 记录当前字符，避免重复索引。
            str_char = text[int_index]  # 当前扫描字符。

            # 反向遇到右括号代表进入更深一层。
            if str_char == "]":

                # 右括号增加反向扫描深度。
                int_depth += 1  # 更新后的方括号深度。

            # 反向遇到左括号代表退出一层。
            elif str_char == "[":

                # 方括号配对在这一层完成一次抵消。
                int_depth -= 1  # 匹配左括号后的剩余方括号深度。

                # 深度归零时找到 close_index 对应的左括号。
                if int_depth == 0:

                    # 返回匹配位置给调用方继续反向定位 base。
                    return int_index

        # 扫描到开头仍未归零，说明方括号不匹配。
        return None

    # _split_lvalue_suffix_parts 将 base 后连续的方括号后缀拆成片段。
    def _split_lvalue_suffix_parts(self, suffix: str) -> list[str]:
        """把左值后缀拆分为多个完整方括号片段。

        :param suffix: base 之后的索引或切片后缀。
        :return: 每个顶层方括号片段；后缀非法时返回空列表。
        """

        # 按出现顺序收集 [] 后缀片段。
        list_parts: list[str] = []  # 完整方括号后缀片段列表。

        # int_index 指向 suffix 中尚未消费的位置。
        int_index = 0  # 当前扫描位置。

        # 逐段消费 suffix，任何非方括号内容都会让解析失败。
        while int_index < len(suffix):

            # 后缀片段之间允许空白。
            while int_index < len(suffix) and suffix[int_index].isspace():

                # 向前跳过两个后缀片段之间的空白。
                int_index += 1  # 下一个待检查后缀字符位置。

            # 消费完全部文本后结束拆分。
            if int_index >= len(suffix):

                # suffix 已完整拆分，无需继续外层循环。
                break

            # 后缀必须以 [ 开始，避免把函数调用或属性访问误收为索引。
            if suffix[int_index] != "[":

                # 空列表表示调用方应走严格错误路径。
                return []

            # 记录当前片段起点，用于保留原始括号文本。
            int_part_start = int_index  # 当前 [] 片段起始位置。

            # 当前片段内部允许嵌套方括号表达式。
            int_depth = 0  # 当前片段内部嵌套深度。

            # 扫描到当前片段的匹配右括号。
            while int_index < len(suffix):

                # 后缀扫描字符只关心方括号层级。
                str_char = suffix[int_index]  # 后缀片段当前字符。

                # 左括号增加嵌套深度。
                if str_char == "[":

                    # 嵌套左括号说明后缀内部还有一层索引表达式。
                    int_depth += 1  # 嵌套索引进入后的片段深度。

                # 右括号降低嵌套深度。
                elif str_char == "]":

                    # 右括号结束当前后缀表达式的一层嵌套。
                    int_depth -= 1  # 右括号关闭后的片段深度。

                    # 当前片段闭合后，把扫描位置推进到右括号之后。
                    if int_depth == 0:

                        # 跳过当前右括号，使外层循环检查下一个后缀。
                        int_index += 1  # 当前片段结束后的扫描位置。

                        # 当前 [] 片段已经完整收集。
                        break

                # 未闭合当前片段时继续推进扫描。
                int_index += 1  # 当前片段内部的下一字符位置。

            # 非零深度表示 suffix 中存在未闭合方括号。
            if int_depth != 0:

                # 未闭合方括号会让上层把后缀整体判为非法。
                return []

            # 当前片段包含左右方括号，后续类型判断还要检查内部顶层 token。
            str_part = suffix[int_part_start:int_index].strip()  # 当前完整 [] 后缀片段。

            # 保留原始括号片段，避免重新构造表达式文本。
            list_parts.append(str_part)

        # 返回按原始顺序拆出的全部后缀片段。
        return list_parts

    # _has_top_level_token 判断切片符号是否位于表达式顶层。
    def _has_top_level_token(self, text: str, token: str) -> bool:
        """判断 token 是否出现在文本顶层。

        :param text: 待扫描表达式文本。
        :param token: 需要查找的操作符或分隔 token。
        :return: token 位于括号外层时返回 True。
        """

        # token 查找需要统一维护三类括号的嵌套深度。
        int_depth = 0  # 顶层 token 扫描的表达式深度。

        # 顶层 token 扫描从表达式开头逐字符推进。
        int_index = 0  # token 查找的当前字符位置。

        # 线性扫描即可满足小片段表达式的顶层 token 判断。
        while int_index < len(text):

            # 当前字符既可能改变括号深度，也可能是 token 起点。
            str_char = text[int_index]  # token 扫描当前字符。

            # 任一开括号都会把后续 token 放入非顶层上下文。
            if str_char in "([{":

                # 进入一层括号上下文。
                int_depth += 1  # 更新后的表达式深度。

            # 任一闭括号退出一层；max 保护不平衡片段不产生负深度。
            elif str_char in ")]}":

                # 闭括号让后续字符回到更外层表达式环境。
                int_depth = max(0, int_depth - 1)  # 闭括号后的表达式深度。

            # 只有深度为零时，目标 token 才是当前表达式的顶层 token。
            elif int_depth == 0 and text.startswith(token, int_index):

                # 顶层命中即可提前结束扫描。
                return True

            # 推进到下一个字符继续扫描。
            int_index += 1  # 下一个待检查字符位置。

        # 扫描结束仍未命中顶层 token。
        return False

    # _collect_unique_lvalue_bases 汇总多个左值中的唯一目标信号。
    def _collect_unique_lvalue_bases(self, lvalues: list[LValueRef]) -> list[str]:
        """汇总多个左值中的唯一 base 信号名。

        :param lvalues: 已解析的左值列表。
        :return: 排序后的唯一 base 信号名列表。
        """

        # set 用于去重，最后排序保证输出稳定。
        set_bases: set[str] = set()  # 去重后的 base 信号集合。

        # 每个左值可能是简单信号、切片或拼接结构。
        for lvalueref_item in lvalues:

            # 统一通过提取函数展开拼接和复杂左值。
            set_bases.update(self._extract_lvalue_bases(lvalueref_item))

        # 使用排序结果保证后续渲染和测试输出稳定。
        return sorted(set_bases)

    # _find_procedural_assignment_operators 查找过程语句中的顶层赋值运算符。
    def _find_procedural_assignment_operators(self, fragment: str) -> list[int]:
        """定位过程语句片段中的顶层赋值运算符。

        :param fragment: 分号切分后的过程语句片段。
        :return: 顶层阻塞或非阻塞赋值运算符位置。
        """

        # 顶层赋值运算符位置按扫描顺序收集。
        list_positions: list[int] = []  # 顶层赋值运算符起始位置列表。

        # int_depth 为括号嵌套深度，非零时不识别顶层赋值。
        int_depth = 0  # 当前括号嵌套深度。

        # int_index 指向 fragment 中待扫描字符。
        int_index = 0  # 赋值运算符扫描位置。

        # 只识别顶层 = 和 <=，避免把表达式内部比较运算符误判为赋值。
        while int_index < len(fragment):

            # 赋值运算符扫描先按当前字符维护括号状态。
            str_char = fragment[int_index]  # 赋值扫描当前字符。

            # 进入括号内部后，赋值运算符不再作为本语句顶层目标。
            if str_char in "([{":

                # 开括号使扫描进入表达式内部。
                int_depth += 1  # 更新后的括号嵌套深度。

                # 括号字符处理完成，继续扫描后续字符。
                int_index += 1  # 开括号之后的扫描位置。

                # 当前分支已完成开括号处理。
                continue

            # 退出括号层级；异常不平衡时保持深度非负。
            if str_char in ")]}":

                # 闭括号使赋值扫描回到上一层表达式。
                int_depth = max(0, int_depth - 1)  # 闭括号后的赋值扫描深度。

                # 闭括号处理完成，继续扫描后续字符。
                int_index += 1  # 闭括号之后的扫描位置。

                # 当前分支已完成闭括号处理。
                continue

            # 括号内部的 = 或 <= 不作为当前语句顶层赋值。
            if int_depth != 0:

                # 非顶层字符只推进位置，不尝试识别赋值。
                int_index += 1  # 括号内部的下一字符位置。

                # 当前字符位于括号内部，不参与赋值运算符判断。
                continue

            # 非阻塞赋值 <= 是双字符运算符，需要一次性消费。
            if fragment.startswith("<=", int_index):

                # 记录 <= 的起始位置。
                list_positions.append(int_index)

                # 跳过整个 <= 运算符，避免把其中的 = 再次识别。
                int_index += 2  # 非阻塞赋值符之后的扫描位置。

                # <= 已经作为一个整体处理完毕。
                continue

            # 单字符 = 需要排除比较运算和 >=、<=、!= 等上下文。
            if str_char == "=":

                # previous 是 = 前一个字符，空字符串表示位于片段开头。
                str_previous_char = fragment[int_index - 1] if int_index > 0 else ""  # = 前一个字符。

                # following 是 = 后一个字符，空字符串表示位于片段末尾。
                str_following_char = fragment[int_index + 1] if int_index + 1 < len(fragment) else ""  # = 后一个字符。

                # 前后字符确认当前 = 不是比较运算符的一部分。
                if str_previous_char not in "<>!=" and str_following_char != "=":

                    # 记录阻塞赋值 = 的位置。
                    list_positions.append(int_index)

            # 当前字符处理结束，推进到下一位。
            int_index += 1  # 普通字符处理后的扫描位置。

        # 返回所有顶层赋值运算符位置，调用方再提取左侧目标。
        return list_positions

    # _extract_lvalue_candidate_before_operator 从赋值运算符左侧截出左值候选。
    def _extract_lvalue_candidate_before_operator(
        self,
        fragment: str,
        operator_index: int,
        category: str,
        suggestion: str,
    ) -> str:
        """从赋值运算符左侧提取候选左值文本。

        :param fragment: 分号切分后的语句片段。
        :param operator_index: 赋值运算符所在位置。
        :param category: 严格错误分类。
        :param suggestion: 解析失败时给用户的修复建议。
        :return: 可交给 _parse_lvalue 的候选左值文本。
        :raises VerilogFormatterError: 运算符左侧无法稳定定位左值。
        """

        # 只取赋值运算符左侧作为候选扫描范围。
        str_prefix = fragment[:operator_index].rstrip()  # 赋值运算符左侧文本。

        # 空左侧没有任何候选目标。
        if not str_prefix:

            # 空左侧说明当前赋值语句缺少目标。
            self._raise_lvalue_error(category, fragment, suggestion)

        # 拼接左值以 } 结尾，需要反向找到匹配的 {。
        if str_prefix.endswith("}"):

            # 花括号深度用于定位顶层拼接起点。
            int_depth = 0  # 反向扫描花括号深度。

            # 从末尾开始寻找顶层拼接左值起点。
            for int_index in range(len(str_prefix) - 1, -1, -1):

                # 拼接候选从右向左扫描，当前字符决定花括号深度。
                str_char = str_prefix[int_index]  # 拼接左值反向扫描字符。

                # 反向遇到 } 表示进入一层拼接。
                if str_char == "}":

                    # 记录还有一个待匹配的左花括号。
                    int_depth += 1  # 更新后的花括号深度。

                # 反向遇到 { 表示退出一层拼接。
                elif str_char == "{":

                    # 当前左花括号匹配一个右花括号层级。
                    int_depth -= 1  # 左花括号匹配后的剩余深度。

                    # 深度归零时，当前 { 就是拼接左值起点。
                    if int_depth == 0:

                        # 返回包含花括号的完整拼接左值。
                        return str_prefix[int_index:].strip()

            # 找不到匹配左花括号，说明拼接左值不完整。
            self._raise_lvalue_error(category, fragment, suggestion)

        # 普通左值通过反向扫描定位 base 起点。
        int_start = self._find_lvalue_start(str_prefix)  # 普通左值在 prefix 中的起始位置。

        # 普通左值定位失败时，当前语句不属于可稳定格式化的赋值形态。
        if int_start is None:

            # 缺少稳定 base 时交由严格错误提示用户规整语句。
            self._raise_lvalue_error(category, fragment, suggestion)

        # 返回从 base 起点到运算符前的完整左值文本。
        return str_prefix[int_start:].strip()

    # _extract_procedural_lvalues 从过程语句块中收集所有赋值左值。
    def _extract_procedural_lvalues(self, text: str, category: str, suggestion: str) -> list[LValueRef]:
        """从过程语句文本中提取所有赋值左值。

        :param text: 过程语句或语句块文本。
        :param category: 严格错误分类。
        :param suggestion: 解析失败时给用户的修复建议。
        :return: 提取出的左值引用列表。
        :raises VerilogFormatterError: 发现赋值迹象但无法解析任何稳定左值。
        """

        # 先剥离每行注释，只保留可参与赋值判断的代码。
        list_code_parts: list[str] = []  # 去掉注释后的非空代码片段。

        # 按行剥离行尾注释，避免注释里的 = 干扰赋值判断。
        for str_raw_line in text.splitlines():

            # 宿主 _split_comment 返回代码部分和注释部分，这里只用代码。
            str_code, _ = self._split_comment(str_raw_line)  # 当前行去掉注释后的代码。

            # 去掉行内代码外侧空白，方便后续拼接。
            str_statement = str_code.strip()  # 当前行规整后的有效语句。

            # 空行或纯注释行不参与赋值解析。
            if str_statement:

                # 使用空格拼接，保留跨行语句的 token 边界。
                list_code_parts.append(str_statement)

        # 多行过程语句统一成单行扫描文本。
        str_working = " ".join(list_code_parts)  # 用于过程赋值扫描的单行文本。

        # 没有赋值符号时，当前文本不产生左值。
        if "=" not in str_working:

            # 无赋值不是错误，返回空列表给调用方继续流程。
            return []

        # 左值结果列表保持扫描顺序。
        list_lvalues: list[LValueRef] = []  # 从过程语句中解析出的左值列表。

        # bool_saw_assignment 区分“没有赋值”和“赋值形态无法解析”。
        bool_saw_assignment = False  # 是否看到可疑赋值运算符。

        # 分号是过程语句的顶层边界，先切片再找赋值运算符。
        for str_fragment in self._split_top_level(str_working, ";"):

            # 单个分号片段用于定位当前语句中的赋值目标。
            str_statement = str_fragment.strip()  # 当前分号片段。

            # 空片段通常来自末尾分号或连续分号。
            if not str_statement:

                # 空分号片段不影响后续赋值目标收集。
                continue

            # 一个片段里可能存在多个顶层赋值运算符。
            for int_operator_index in self._find_procedural_assignment_operators(str_statement):

                # 看到赋值运算符后，若后续无法解析应报告严格错误。
                bool_saw_assignment = True  # 已发现顶层赋值运算符。

                # 从运算符左侧截出 formatter 支持的左值候选。
                str_candidate = self._extract_lvalue_candidate_before_operator(  # 当前赋值运算符左侧候选左值。
                    str_statement,  # 运算符所在的过程语句片段。
                    int_operator_index,  # 当前赋值运算符位置。
                    category,  # 当前语句片段的错误分类。
                    suggestion,  # 左侧候选缺失时的提示文本。
                )

                # 候选文本继续交给统一左值解析器。
                l_value_ref_lvalueref_item: LValueRef = self._parse_lvalue(  # 候选文本对应的结构化左值。
                    str_candidate,  # 已截取出的左值候选。
                    category,  # 解析当前候选使用的错误分类。
                    suggestion,  # 当前候选无法解析时的提示文本。
                    allow_concat=True,  # 过程赋值允许顶层拼接目标。
                )

                # 保留扫描顺序，供后续目标信号分析使用。
                list_lvalues.append(l_value_ref_lvalueref_item)

        # 看到赋值迹象却没有提取出任何左值，说明语句形态超出支持范围。
        if bool_saw_assignment and not list_lvalues:

            # 赋值符存在但没有目标，提示用户规整过程语句。
            self._raise_lvalue_error(category, text, suggestion)

        # 返回所有过程赋值左值。
        return list_lvalues

    # _extract_lvalues_from_text 是外部 mixin 调用的过程赋值左值入口。
    def _extract_lvalues_from_text(self, text: str, category: str) -> list[LValueRef]:
        """从文本中提取过程赋值左值。

        :param text: 待扫描文本。
        :param category: 严格错误分类。
        :return: 文本内所有过程赋值左值。
        """

        # 标准化错误建议，具体前缀由严格错误 helper 统一添加。
        return self._extract_procedural_lvalues(
            text,
            category,
            "请使用稳定的过程赋值左值，例如 foo、foo[idx]、foo[msb:lsb] 或 {foo, bar}。",
        )

    # _extract_target_bases_from_statement 提供过程语句目标 base 汇总。
    def _extract_target_bases_from_statement(self, text: str, category: str) -> list[str]:
        """从过程语句中提取唯一目标 base 信号。

        :param text: 待扫描的过程赋值语句。
        :param category: 严格错误分类。
        :return: 排序后的唯一目标 base 信号列表。
        """

        # 目标 base 汇总必须先保留过程语句中的所有写入目标。
        list_lvalues = self._extract_procedural_lvalues(  # 当前语句中所有过程赋值左值。
            text,  # 待扫描过程语句。
            category,  # 过程语句目标提取的错误分类。
            "请在格式化前使用稳定赋值目标，例如 foo、foo[idx]、foo[msb:lsb] 或 {foo, bar}。",  # 过程语句目标修复建议。
        )

        # 将复杂左值展开为唯一 base 信号列表。
        return self._collect_unique_lvalue_bases(list_lvalues)

    # _extract_assign_lvalue_bases 专门处理 continuous assign 语句左侧。
    def _extract_assign_lvalue_bases(self, text: str, category: str) -> list[str]:
        """从 continuous assign 语句中提取左侧 base 信号。

        :param text: 待扫描的 assign 语句。
        :param category: 严格错误分类。
        :return: assign 左侧覆盖到的 base 信号列表。
        """

        # 先移除行尾注释，避免注释中的等号干扰 assign 正则。
        str_line, _ = self._split_comment(text)  # 去掉行尾注释后的 assign 文本。

        # continuous assign 正则支持可选 delay/control 后接左值和右值。
        match_assign = re.search(  # continuous assign 的左侧与右侧匹配结果。
            r"\bassign\s+(?:#\s*(?:\([^)]*\)|\S+)\s+)?(.+?)\s*=\s*(.+?);$",  # assign 左右侧捕获模式。
            str_line.strip(),  # 去掉外侧空白后的 assign 候选行。
        )

        # 非 assign 语句不产生 continuous assign 左值。
        if not match_assign:

            # 返回空列表表示调用方无需处理该行。
            return []

        # assign 左侧也复用统一左值解析器。
        l_value_ref_lvalueref_item: LValueRef = self._parse_lvalue(  # assign 左侧解析出的结构化左值。
            match_assign.group(1).strip(),  # assign 左侧候选文本。
            category,  # continuous assign 解析错误分类。
            "assign 左侧需要使用稳定目标，例如 foo、foo[idx]、foo[msb:lsb] 或 {foo, bar}。",  # assign 左侧修复建议。
            allow_concat=True,  # continuous assign 左侧允许拼接。
        )

        # 返回 assign 左侧引用到的所有 base 信号。
        return self._extract_lvalue_bases(l_value_ref_lvalueref_item)

    # _extract_assign_lvalue_base 为单目标 assign 提供便捷提取。
    def _extract_assign_lvalue_base(self, text: str, category: str) -> str | None:
        """提取 continuous assign 语句的单一 base 信号。

        :param text: 待扫描的 assign 语句。
        :param category: 严格错误分类。
        :return: 仅当 assign 左侧恰好对应一个 base 时返回信号名，否则返回 None。
        """

        # 先获取 assign 左侧覆盖到的所有 base。
        list_bases = self._extract_assign_lvalue_bases(text, category)  # assign 左侧覆盖到的 base 信号。

        # 只有单目标 assign 才能被折叠为一个 base 名称。
        if len(list_bases) == 1:

            # 返回唯一 base，供调用方建立目标信号索引。
            return list_bases[0]

        # 多目标或无目标 assign 不能作为单一 base。
        return None
