"""为实例关联解析提供注释感知的词法压缩。"""

# 延迟类型注解求值，保持扫描器容器声明兼容当前 Python 版本。
from __future__ import annotations

# dataclass 集中管理一次实例扫描的有限状态和同步偏移序列。
from dataclasses import dataclass, field

# 实例扫描器把所有可变状态限制在单次 compact 调用对象内。
@dataclass
class InstanceLexScanner:
    """逐字符压缩实例 trivia 并保存可逆偏移。

    参数:
        text: formatter 收集的完整实例声明原文。
    """

    # 输入文本在扫描期间保持只读。
    text: str  # 当前实例声明原文

    # canonical 字符按实例原始顺序累积。
    chars: list[str] = field(default_factory=list)  # 已保留的 canonical 字符

    # 每个 canonical 字符拥有同索引的原文偏移。
    offsets: list[int] = field(default_factory=list)  # canonical 字符来源偏移

    # normal 是每次扫描的确定入口状态。
    state: str = "normal"  # 当前词法状态

    # 游标始终指向下一枚尚未消费的字符。
    offset: int = 0  # 当前原文扫描偏移

    # trivia 只在下一枚可见字符前提交一个空格。
    has_pending_space: bool = False  # 是否缓存待写 canonical 空格

    # 首枚 trivia 偏移承担 canonical 分隔符来源位置。
    pending_offset: int = 0  # 当前待写空格的原文来源偏移

    # 顶层循环依据当前状态选择唯一字符消费器。
    def compact(self) -> tuple[str, tuple[int, ...], str]:
        """返回 canonical 文本、偏移表和不完整原因。

        参数:
            self: 当前实例专属词法扫描状态。
        返回:
            canonical 文本、逐字符原文偏移和稳定失败原因。
        """

        # 每轮只由当前词法状态消费至少一个原文字符。
        while self.offset < len(self.text):

            # 注释状态只寻找各自终止边界，不保留正文。
            if self.state in {"line_comment", "block_comment"}:

                # 注释处理器负责换行或块终止符转换。
                self._consume_comment()

                # 当前字符已经由注释状态完整消费。
                continue

            # 字符串状态原样保留空白、斜杠、星号和转义字符。
            if self.state in {"string", "string_escape"}:

                # 字符串处理器维护引号与转义状态。
                self._consume_string()

                # 当前字符已经由字符串状态完整消费。
                continue

            # normal 状态识别 trivia、字符串入口和普通文本。
            str_reason = self._consume_normal()  # 当前字符的可选失败原因

            # 游离终止符属于不可恢复的实例词法错误。
            if str_reason:

                # 失败结果不得携带可能被误用的半成品文本。
                return "", (), str_reason

        # 块注释到达文件尾仍未闭合时保持明确失败。
        if self.state == "block_comment":

            # 专用原因让调用方区别语法不完整与普通注释。
            return "", (), "unclosed_block_comment"

        # 字符串或尾随转义未闭合时同样禁止猜测实例结构。
        if self.state in {"string", "string_escape"}:

            # 字符串不完整原因保持在当前实例局部。
            return "", (), "unclosed_string_literal"

        # 成功结果冻结偏移表，阻止调用方破坏索引对齐。
        return "".join(self.chars), tuple(self.offsets), ""

    # 注释处理器只消费 trivia，不写 canonical 字符。
    def _consume_comment(self) -> None:
        """消费一枚行注释或块注释字符。

        参数:
            self: 当前实例专属词法扫描状态。
        返回:
            无；游标和词法状态在当前对象内更新。
        """

        # 当前字符与后一字符共同识别块注释终止符。
        str_char = self.text[self.offset]  # 当前注释正文字符

        # 后一字符在原文末尾使用空字符串兜底。
        str_next = self._next_char()  # 当前字符之后的可选字符

        # 行注释在首枚换行字符处恢复 normal 状态。
        if self.state == "line_comment":

            # 回车或换行都足以结束当前行注释。
            if str_char in "\r\n":

                # 换行结束行注释并恢复普通词法含义。
                self.state = "normal"  # 换行后的文本恢复普通扫描

            # 行注释正文逐字符跳过。
            self.offset += 1  # 推进到下一枚原文字符

            # 行注释处理完当前字符后立即返回。
            return

        # 块注释只在相邻星号和斜杠处闭合。
        if str_char == "*" and str_next == "/":

            # 终止符关闭块注释状态。
            self.state = "normal"  # 块注释闭合后恢复普通扫描

            # 星号和斜杠必须在同一状态转换中成对消费。
            self.offset += 2  # 成对消费块注释终止符

            # 块注释终止符已经完整消费。
            return

        # 未命中终止符时继续扫描块注释正文。
        self.offset += 1  # 推进到下一枚块注释字符

    # 字符串处理器原样保留内部空白和注释形状。
    def _consume_string(self) -> None:
        """消费一枚字符串正文或转义字符。

        参数:
            self: 当前实例专属词法扫描状态。
        返回:
            无；字符、偏移、游标和状态在当前对象内更新。
        """

        # 当前字符无条件属于字符串 actual 内容。
        str_char = self.text[self.offset]  # 当前字符串字符

        # canonical 文本保留字符串中的全部字符。
        self.chars.append(str_char)

        # 偏移表同步记录当前字符串字符来源。
        self.offsets.append(self.offset)

        # 转义状态只保护当前一枚字符，随后恢复字符串正文。
        if self.state == "string_escape":

            # 被保护字符消费后回到普通字符串正文。
            self.state = "string"  # 已消费转义目标字符

        # 普通字符串中的反斜杠保护下一枚字符。
        elif str_char == "\\":

            # 下一轮不得把被转义引号解释成字符串结束。
            self.state = "string_escape"  # 下一字符不解释为引号或注释

        # 未转义双引号结束当前字符串。
        elif str_char == '"':

            # 字符串闭合后恢复实例普通文本扫描。
            self.state = "normal"  # 后续字符恢复普通词法含义

        # 字符串游标越过刚保存字符，使转义边界只覆盖下一轮。
        self.offset += 1  # 字符串状态消费后的原文位置

    # 普通文本处理器识别注释、空白、引号和可见字符。
    def _consume_normal(self) -> str:
        """消费一枚 normal 状态字符并返回可选失败原因。

        参数:
            self: 当前实例专属词法扫描状态。
        返回:
            空字符串表示继续扫描，否则返回稳定词法失败原因。
        """

        # 当前字符与后一字符共同识别双字符注释边界。
        str_char = self.text[self.offset]  # 当前普通文本字符

        # 普通态仅用 lookahead 分辨斜杠和星号组成的边界。
        str_next = self._next_char()  # 普通态定界符判断所需后继字符

        # 行注释入口等效为 token 间空白。
        if str_char == "/" and str_next == "/":

            # 行注释状态从当前双斜杠之后开始消费。
            self._begin_comment("line_comment")

            # 行注释入口成功后继续外层状态循环。
            return ""

        # 块注释入口同样等效为 token 间空白。
        if str_char == "/" and str_next == "*":

            # 块注释状态从当前起始符之后开始消费。
            self._begin_comment("block_comment")

            # 块注释入口成功后继续寻找对应终止符。
            return ""

        # 游离块终止符没有合法起始状态，必须立即失败。
        if str_char == "*" and str_next == "/":

            # 稳定原因阻止半成品进入关联解析。
            return "unexpected_block_comment_terminator"

        # 普通空白延迟为下一枚可见字符前的单一分隔符。
        if str_char.isspace():

            # 连续 trivia 只登记首枚来源偏移。
            self._mark_pending_space()

            # 当前空白本身不进入 canonical 文本。
            self.offset += 1  # 跳过当前原文空白字符

            # 空原因表示当前空白已安全消费。
            return ""

        # 可见字符到来前提交此前缓存的唯一 canonical 空格。
        self._flush_pending_space()

        # 普通字符按原顺序进入 canonical 文本。
        self.chars.append(str_char)

        # 当前字符偏移与 canonical 索引保持一一对应。
        self.offsets.append(self.offset)

        # 双引号让下一轮进入字符串词法状态。
        if str_char == '"':

            # 起始引号已保存，下一字符按字符串正文消费。
            self.state = "string"  # 当前引号已经作为字符串首字符保存

        # 普通态完成字符保存后将游标移向下一词法位置。
        self.offset += 1  # 普通字符消费后的原文位置

        # 空原因表示当前字符已被完整消费。
        return ""

    # 注释入口统一登记 trivia 分隔作用并消费起始符。
    def _begin_comment(self, str_comment_state: str) -> None:
        """进入指定注释状态并消费起始符。

        参数:
            str_comment_state: line_comment 或 block_comment 状态名。
        返回:
            无；待写空格、状态和游标在当前对象内更新。
        """

        # 注释前已有 token 时才需要保留分隔作用。
        self._mark_pending_space()

        # 调用方只传入两种已验证注释状态。
        self.state = str_comment_state  # 当前注释正文的扫描类别

        # 两种注释起始符均由两个字符组成。
        self.offset += 2  # 成对消费斜杠和第二枚定界字符

    # trivia 记录器只保留连续区域的首枚偏移。
    def _mark_pending_space(self) -> None:
        """在已有 token 后登记一个待写分隔空格。

        参数:
            self: 当前实例专属词法扫描状态。
        返回:
            无；待写状态和首枚 trivia 偏移在当前对象内更新。
        """

        # 领先 trivia 或已经缓存的连续 trivia 不重复登记。
        if not self.chars or self.has_pending_space:

            # 当前 trivia 不需要改变已记录的分隔边界。
            return

        # 首枚 trivia 触发一个待写 canonical 空格。
        self.has_pending_space = True  # 下一枚可见字符前需要写入空格

        # 偏移映射选择当前连续 trivia 区的起始位置。
        self.pending_offset = self.offset  # canonical 空格的原文来源偏移

    # 可见字符到来时提交待写 canonical 分隔符。
    def _flush_pending_space(self) -> None:
        """把待写分隔符同步追加到字符和偏移序列。

        参数:
            self: 当前实例专属词法扫描状态。
        返回:
            无；没有待写空格时保持当前对象不变。
        """

        # 没有 trivia 边界时无需写入分隔字符。
        if not self.has_pending_space:

            # 空操作保持两个 canonical 序列长度不变。
            return

        # canonical 空格维持相邻 token 的必要边界。
        self.chars.append(" ")

        # 同步保存该空格对应的首枚 trivia 偏移。
        self.offsets.append(self.pending_offset)

        # 当前 trivia 边界已经提交完成。
        self.has_pending_space = False  # 清除待写空格状态

    # lookahead helper 集中处理实例原文末尾边界。
    def _next_char(self) -> str:
        """返回当前游标之后的一枚字符。

        参数:
            self: 当前实例专属词法扫描状态。
        返回:
            原文后一字符；当前字符位于末尾时返回空字符串。
        """

        # 后一字符索引用于边界检查和实际读取。
        int_next_offset = self.offset + 1  # 当前游标之后的原文偏移

        # 原文末尾之外没有可读取字符。
        if int_next_offset >= len(self.text):

            # 空字符串避免调用方额外处理 None。
            return ""

        # 边界内字符可直接按索引读取。
        return self.text[int_next_offset]

# 函数 facade 为 formatter mixin 保留稳定调用合同。
def compact_instance_with_offsets(text: str) -> tuple[str, tuple[int, ...], str]:
    """压缩实例 trivia 并保留 canonical 字符到原文的偏移。

    参数:
        text: formatter 收集的完整实例声明原文。
    返回:
        canonical 文本、逐字符原文偏移和稳定失败原因。
    """

    # 每次调用创建独立扫描器，避免不同实例共享可变状态。
    instance_lex_scanner = InstanceLexScanner(text)  # 当前实例专属词法扫描器

    # 扫描结果直接保持 formatter 现有三元组兼容合同。
    return instance_lex_scanner.compact()
