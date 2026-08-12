"""在 formatter backend 内构建组合锥可消费的类型化表达式事实。"""

# 延迟求值类型注解，保持 formatter 导入阶段轻量。
from __future__ import annotations

# 正则只处理 formatter 已经隔离的表达式和控制头，不扫描原始文件。
import re

# 不可变词元与遍历上下文减少递归参数漂移。
from dataclasses import dataclass, replace

# Any 描述 formatter JSON 叶节点，Iterator 描述无副作用词元流。
from typing import Any, Iterator

# 二元优先级与 Verilog-2001 常见表达式绑定规则一致。
dict__binary_precedence = {  # 决定二元表达式树结合顺序并直接影响组合操作锥依赖结构
    "||": 1,  # 最低层逻辑或
    "&&": 2,  # 次低层逻辑与
    "|": 3,  # 按位或结合层
    "^": 4,  # 按位异或结合层
    "~^": 4,  # 按位同或结合层
    "^~": 4,  # 按位同或别名
    "&": 5,  # 按位与结合层
    "==": 6,  # 二态相等比较层
    "!=": 6,  # 二态不等比较层
    "===": 6,  # 四态全等比较层
    "!==": 6,  # 四态非全等比较层
    "<": 7,  # 小于比较
    "<=": 7,  # 小于等于比较
    ">": 7,  # 大于比较
    ">=": 7,  # 大于等于比较
    "<<": 8,  # 逻辑左移
    ">>": 8,  # 逻辑右移
    "<<<": 8,  # 算术左移
    ">>>": 8,  # 算术右移
    "+": 9,  # 加法结合层
    "-": 9,  # 减法结合层
    "*": 10,  # 乘法结合层
    "/": 10,  # 除法结合层
    "%": 10,  # 取模结合层
    "**": 11,  # 最高层幂运算
}

# 前缀集合同时覆盖逻辑、按位和归约一元运算。
frozenset__unary_operators: frozenset[str] = frozenset(  # 触发一元操作节点生成的前缀符号
    {"!", "~", "+", "-", "&", "|", "^", "~&", "~|", "~^", "^~"}  # 逻辑、按位及归约一元符号
)

# 多字符运算符按长度优先，防止词元被短前缀截断。
tuple__multi_operators = tuple(  # 防止复合操作被拆成多个错误操作节点的匹配序列
    sorted(  # 为分词器生成确定的匹配次序
        {
            "<<<",  # 三字符带符号左移符
            ">>>",  # 三字符带符号右移符
            "===",  # 四态全等比较符
            "!==",  # 四态非全等比较符
            "**",  # 右结合指数运算符
            "&&",  # 短路逻辑与符
            "||",  # 短路逻辑或符
            "==",  # 二态相等比较符
            "!=",  # 二态不等比较符
            "<=",  # 小于等于
            ">=",  # 大于等于
            "<<",  # 两字符无符号左移符
            ">>",  # 两字符无符号右移符
            "~&",  # 归约与非
            "~|",  # 归约或非
            "~^",  # 归约同或
            "^~",  # 归约同或别名
        },
        key=len,  # 长运算符必须先匹配
        reverse=True,  # 从最长文本向最短文本排序
    )
)

# 词元保存源内偏移，供真实操作节点生成稳定编号。
@dataclass(frozen=True)
class ExpressionToken:
    """保存一个表达式词元及其源内偏移。

    参数:
        text: 当前词元的原始 Verilog 文本。
        offset: 当前词元在已隔离表达式中的字符偏移。
    """

    # 词元文本用于识别运算符、常量和标识符。
    text: str  # 原始词元文本

    # 源内偏移参与 occurrence_id，避免相同操作被错误合并。
    offset: int  # 表达式内字符偏移

# 专用异常只表示局部表达式无法形成受支持的类型化事实。
class ExpressionParseError(ValueError):
    """表示 formatter 无法把已识别表达式转换为类型化事实。"""

# 遍历上下文把过程、循环和控制栈作为一个不可变递归合同。
@dataclass(frozen=True)
class NodeFactContext:
    """保存 formatter 控制树递归所需的稳定上下文。

    参数:
        facts: 当前 module 正在累积的表达式事实列表。
        prefix: 当前控制树位置的稳定编号前缀。
        line: 当前节点对应的近似源码行号。
        process_kind: formatter 判定的过程类型。
        iterations: 外层常量循环累计展开次数。
        from_for: 当前节点是否位于任一 for 循环体。
        controls: 当前节点继承的类型化控制表达式栈。
        skip_first_reset: 是否跳过时序块首个异步复位条件。
    """

    # 事实列表由所有递归分支共享并按遍历顺序追加。
    facts: list[dict[str, Any]]  # 当前 module 的组合表达式事实

    # 位置前缀为操作节点生成不可合并的稳定编号。
    prefix: str  # 当前控制树位置前缀

    # 行号用于把门禁发现定位到赋值附近。
    line: int  # 当前递归节点的基础行号

    # 过程类型用于识别寄存器 D 端和组合生产者。
    process_kind: str  # 区分连续、组合、时序或未知驱动过程

    # 累计迭代数表示 elaboration 后的硬件复制倍数。
    iterations: int  # 当前节点累计展开次数

    # 循环标记把对应目标交给 VG147 负责。
    from_for: bool  # 当前节点是否来自 for 循环

    # 控制栈用于把条件运算和分支选择计入目标组合锥。
    controls: tuple[dict[str, Any], ...]  # 继承的控制表达式

    # 异步复位条件不属于功能 D 路径，首个条件需要排除。
    skip_first_reset: bool  # 是否跳过首个复位条件

    # formatter 已识别的复位信号用于校验首条件确实是规范复位条件。
    reset_signal: str  # 当前时序过程的复位信号名

    # 分支路径记录目标是否覆盖完整控制树，供锁存器切点判定使用。
    branch_path: tuple[dict[str, Any], ...] = ()  # 当前赋值所在的运行时分支路径

# Pratt 解析器只消费一个 formatter 已隔离表达式。
class ExpressionParser:
    """使用确定优先级把 Verilog-2001 表达式解析为字典 AST。"""

    # 初始化阶段只分词并建立当前表达式的稳定编号空间。
    def __init__(self, str_expression: str, str_occurrence_prefix: str) -> None:
        """初始化单个表达式解析器。

        参数:
            str_expression: formatter 已分离出的表达式文本。
            str_occurrence_prefix: 当前表达式在 formatter 报告中的稳定位置。

        返回:
            本方法初始化解析状态，不返回业务值。
        """

        # 一次性固化词元，便于安全前视且不重复运行正则。
        self._tuple_tokens = tuple(_tokenize(str_expression))  # 当前表达式全部词元

        # 游标始终指向下一枚尚未消费的词元。
        self._int_index = 0  # 当前词元索引

        # 前缀把不同 assign、always 和控制条件的节点隔离。
        self._str_occurrence_prefix = str_occurrence_prefix  # 操作编号位置前缀

        # 操作序号只对真实运算和动态选择递增。
        self._int_operation_index = 0  # 当前表达式操作序号

    # 完整解析入口拒绝空表达式和尾随未知词元。
    def parse(self) -> dict[str, Any]:
        """解析完整表达式并拒绝未消费词元。

        参数:
            self: 当前单表达式解析器实例。

        返回:
            formatter 组合锥可消费的类型化表达式根节点。

        异常:
            ExpressionParseError: 表达式为空或包含未支持结构。
        """

        # 空右值无法产生可靠类型化事实。
        if not self._tuple_tokens:

            # 统一错误前缀满足当前项目可诊断异常合同。
            raise ExpressionParseError("> ERR: [Python] empty Verilog expression")

        # 最低优先级入口解析完整 Pratt 表达式。
        dict_expression = self._parse_expression(0)  # 完整表达式根节点

        # 解析完成后仍有词元说明当前语法未被完整消费。
        if self._peek() is not None:

            # 前视结果此时必然存在，保存其文本供局部诊断。
            str_unexpected = self._peek().text  # 首个未消费词元文本

            # 禁止忽略尾随语法后按不完整操作数放行。
            raise ExpressionParseError(
                f"> ERR: [Python] unexpected Verilog token {str_unexpected!r}"
            )

        # 返回唯一根节点供赋值或控制事实引用。
        return dict_expression

    # 表达式循环按优先级逐层收拢二元和三目运算。
    def _parse_expression(self, int_min_precedence: int) -> dict[str, Any]:
        """按最小优先级解析一棵 Pratt 表达式子树。

        参数:
            self: 当前表达式解析器实例。
            int_min_precedence: 本层允许结合的最低二元优先级。

        返回:
            已消费词元形成的类型化表达式子树。
        """

        # 前缀解析提供当前层初始左操作数。
        dict_left = self._parse_prefix()  # 当前层已完成的左侧子树

        # 持续吸收满足优先级约束的后缀运算。
        while True:

            # 前视决定当前层是结束、三目还是二元结合。
            obj_token = self._peek()  # 下一枚尚未消费词元

            # 表达式末尾直接返回当前子树。
            if obj_token is None:

                # 没有后续运算符时停止当前优先级层。
                break

            # 最外层问号开启右结合三目表达式。
            if obj_token.text == "?" and int_min_precedence <= 0:

                # 三目专用方法消费问号、冒号和两个分支。
                dict_left = self._parse_ternary(dict_left, obj_token)  # 三目合并后的左树

                # 继续检查三目结果之后是否还有外层运算。
                continue

            # 查表确定下一词元是否为当前层可结合的二元操作。
            int_precedence = dict__binary_precedence.get(obj_token.text)  # 下一运算符优先级

            # 未知词元或更低优先级由上层解析处理。
            if int_precedence is None or int_precedence < int_min_precedence:

                # 当前层结合范围已经结束。
                break

            # 二元专用方法消费运算符并递归解析右操作数。
            dict_left = self._parse_binary(dict_left, obj_token, int_precedence)  # 二元合并结果

        # 返回当前优先级层建立的完整子树。
        return dict_left

    # 三目解析单独封装，降低主 Pratt 循环的分支复杂度。
    def _parse_ternary(
        self,
        dict_condition: dict[str, Any],
        obj_token: ExpressionToken,
    ) -> dict[str, Any]:
        """解析已识别问号后的三目表达式。

        参数:
            self: 当前表达式解析器实例。
            dict_condition: 问号左侧的条件子树。
            obj_token: 已前视但尚未消费的问号词元。

        返回:
            包含条件、真分支和假分支的三目操作节点。
        """

        # 消费已经由调用方确认的问号词元。
        self._advance()

        # 真分支从最低优先级重新解析。
        dict_true = self._parse_expression(0)  # 三目真分支

        # 冒号是三目结构的强制分隔符。
        self._expect(":")

        # 假分支同样允许嵌套完整表达式。
        dict_false = self._parse_expression(0)  # 三目假分支

        # 三目节点贡献一个真实选择操作。
        return self._operation(
            "ternary",
            "?:",
            (dict_condition, dict_true, dict_false),
            obj_token.offset,
        )

    # 二元解析根据结合性计算右操作数最低优先级。
    def _parse_binary(
        self,
        dict_left: dict[str, Any],
        obj_token: ExpressionToken,
        int_precedence: int,
    ) -> dict[str, Any]:
        """解析一个已确认优先级的二元操作。

        参数:
            self: 当前表达式解析器实例。
            dict_left: 当前二元运算的左操作数。
            obj_token: 已前视的二元运算符词元。
            int_precedence: 当前运算符的优先级。

        返回:
            包含左右操作数的二元操作节点。
        """

        # 消费调用方已确认的二元运算符。
        self._advance()

        # 幂运算右结合，其余二元运算按下一优先级解析右侧。
        int_next_precedence = (  # 右操作数递归使用的最低优先级
            int_precedence  # 幂运算保留当前优先级以实现右结合
            if obj_token.text == "**"  # 仅指数运算采用右结合
            else int_precedence + 1  # 其他二元运算提高右侧最低优先级
        )

        # 右操作数递归吸收更高优先级运算。
        dict_right = self._parse_expression(int_next_precedence)  # 当前二元右操作数

        # 当前运算符对应一个真实组合操作节点。
        return self._operation(
            "binary",
            obj_token.text,
            (dict_left, dict_right),
            obj_token.offset,
        )

    # 前缀解析区分一元、分组、常量和标识符四种起点。
    def _parse_prefix(self) -> dict[str, Any]:
        """解析前缀运算、括号、常量、标识符和选择器。

        参数:
            self: 当前表达式解析器实例。

        返回:
            当前前缀起点形成的类型化表达式子树。

        异常:
            ExpressionParseError: 当前词元不能作为受支持的表达式起点。
        """

        # 前缀解析必须先消费一枚起始词元。
        obj_token = self._advance()  # 当前前缀起始词元

        # 一元运算以高优先级递归读取唯一操作数。
        if obj_token.text in frozenset__unary_operators:

            # 一元操作数允许继续嵌套其他一元运算。
            dict_operand = self._parse_expression(12)  # 一元运算操作数

            # 一元运算符形成一个真实组合操作节点。
            return self._operation(
                "unary",
                obj_token.text,
                (dict_operand,),
                obj_token.offset,
            )

        # 左括号开启一个完整分组表达式。
        if obj_token.text == "(":

            # 分组内部从最低优先级解析。
            dict_expression = self._parse_expression(0)  # 括号内表达式

            # 缺失右括号必须局部标记解析失败。
            self._expect(")")

            # 括号结果仍可能紧跟位选或部分选择。
            return self._parse_postfix(dict_expression)

        # 左花括号开启普通 Verilog 拼接表达式。
        if obj_token.text == "{":

            # 拼接本身是位布线节点，内部表达式仍需递归追踪操作和依赖。
            return self._parse_concatenation(obj_token)

        # Verilog 数值常量是无操作叶节点。
        if _is_constant(obj_token.text):

            # 常量保留文本和偏移，不生成 occurrence_id。
            return {
                "kind": "constant",
                "value": obj_token.text,
                "offset": obj_token.offset,
            }

        # 合法标识符是可追踪数据依赖叶节点。
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", obj_token.text):

            # 标识符名称供组合锥查找同 module 上游生产者。
            dict_identifier = {  # 当前标识符叶节点
                "kind": "identifier",  # 节点类别供依赖遍历识别
                "name": obj_token.text,  # 被引用的 Verilog 信号名称
                "offset": obj_token.offset,  # 标识符在表达式内的位置
            }

            # 标识符之后可能附带静态或动态选择器。
            return self._parse_postfix(dict_identifier)

        # 其他前缀形状超出本轮受支持的 formatter 类型事实合同。
        raise ExpressionParseError(
            f"> ERR: [Python] unsupported Verilog prefix token {obj_token.text!r}"
        )

    # 拼接解析保留内部表达式树，但不把纯位重排虚增为逻辑操作。
    def _parse_concatenation(self, obj_open: ExpressionToken) -> dict[str, Any]:
        """解析一个逗号分隔的普通 Verilog 拼接。

        参数:
            self: 当前表达式解析器实例。
            obj_open: 已消费的左花括号词元。

        返回:
            包含全部拼接元素且没有操作编号的布线节点。

        异常:
            ExpressionParseError: 拼接为空或缺少逗号、右花括号。
        """

        # 空拼接没有可追踪数据依赖，属于不支持的非法结构。
        if self._peek() is not None and self._peek().text == "}":

            # 禁止把空拼接静默表示成零操作叶节点。
            raise ExpressionParseError(
                "> ERR: [Python] empty Verilog concatenation"
            )

        # 第一个元素使用完整表达式优先级解析。
        list_operands = [self._parse_expression(0)]  # 当前拼接的有序元素

        # 后续逗号逐个引入新的完整表达式元素。
        while self._peek() is not None and self._peek().text == ",":

            # 消费分隔逗号后才能解析下一个元素。
            self._advance()

            # 每个拼接元素保留自己的类型化操作子树。
            list_operands.append(self._parse_expression(0))

        # 右花括号强制闭合当前拼接节点。
        self._expect("}")

        # concat 节点仅组合位段，不生成 occurrence_id。
        return {
            "kind": "concat",  # 供组合锥识别的布线节点类别
            "operands": list_operands,  # 按源码顺序保存的拼接元素
            "offset": obj_open.offset,  # 左花括号在表达式内的位置
        }

    # 后缀解析把连续选择器逐层包装在基础表达式外部。
    def _parse_postfix(self, dict_base: dict[str, Any]) -> dict[str, Any]:
        """解析静态或动态位选和部分选择。

        参数:
            self: 当前表达式解析器实例。
            dict_base: 选择器左侧的基础表达式节点。

        返回:
            包含全部连续选择器的类型化表达式节点。
        """

        # 只要下一词元是左方括号就继续包装一层选择节点。
        while self._peek() is not None and self._peek().text == "[":

            # 左方括号偏移标识该选择操作的源码位置。
            expression_token_open: ExpressionToken = self._advance()  # 定位动态选择操作的左方括号词元

            # 第一段表达式表示位索引或范围起点。
            dict_first = self._parse_expression(0)  # 选择器首个索引表达式

            # 默认模式表示单个位选择。
            str_mode = "bit"  # 当前选择器模式

            # 操作数首项是被选择对象，后续项是索引表达式。
            list_operands = [dict_base, dict_first]  # 当前选择节点操作数

            # 冒号、加宽或减宽语法需要第二个索引表达式。
            if self._peek() is not None and self._peek().text in {":", "+:", "-:"}:

                # 消费范围模式并保留原始运算符文本。
                str_mode = self._advance().text  # 当前范围选择模式文本

                # 范围终点或宽度成为第三个操作数。
                list_operands.append(self._parse_expression(0))

            # 每一层选择器都必须由右方括号闭合。
            self._expect("]")

            # 全常量索引在 elaboration 后属于静态连线。
            bool_static = all(  # 当前选择器是否可静态确定
                dict_item.get("kind") == "constant"  # 索引节点必须全部为常量
                for dict_item in list_operands[1:]  # 跳过被选择的基础表达式
            )

            # 动态选择生成真实操作编号，静态选择保留空编号。
            dict_base = {  # 包装后的选择表达式节点
                "kind": "select",  # 选择节点类别
                "operator": str_mode,  # bit、冒号或变宽模式
                "operands": list_operands,  # 基础表达式及索引操作数
                "dynamic": not bool_static,  # 是否需要运行时选择硬件
                "occurrence_id": (  # 动态选择对应的真实操作编号
                    self._next_occurrence(expression_token_open.offset)  # 使用左方括号源码偏移
                    if not bool_static  # 仅动态选择生成操作节点
                    else ""  # 静态连线不消耗组合操作预算
                ),
                "offset": expression_token_open.offset,  # 选择器在表达式内的位置
            }

        # 返回附加全部连续选择器后的表达式。
        return dict_base

    # 所有真实操作节点通过同一工厂获得稳定字段和编号。
    def _operation(
        self,
        str_kind: str,
        str_operator: str,
        tuple_operands: tuple[dict[str, Any], ...],
        int_offset: int,
    ) -> dict[str, Any]:
        """构造带稳定出现编号的运算节点。

        参数:
            self: 当前表达式解析器实例。
            str_kind: unary、binary 或 ternary 节点类型。
            str_operator: 当前节点的 Verilog 运算符文本。
            tuple_operands: 当前运算的有序操作数节点。
            int_offset: 运算符在表达式内的字符偏移。

        返回:
            带真实出现编号的类型化操作节点。
        """

        # 字典字段是组合锥分析器唯一消费的类型化表达式合同。
        return {
            "kind": str_kind,
            "operator": str_operator,
            "operands": list(tuple_operands),
            "occurrence_id": self._next_occurrence(int_offset),
            "offset": int_offset,
        }

    # 编号同时包含 formatter 位置、操作序号和表达式内偏移。
    def _next_occurrence(self, int_offset: int) -> str:
        """为当前真实语法出现生成不可合并的稳定编号。

        参数:
            self: 当前表达式解析器实例。
            int_offset: 当前运算符在表达式内的字符偏移。

        返回:
            在当前 formatter 事实范围内唯一的操作出现编号。
        """

        # 每次调用只对应一个真实运算或动态选择节点。
        self._int_operation_index += 1  # 为当前真实操作分配下一序号

        # 稳定前缀防止不同赋值中相同偏移的操作被合并。
        return (
            f"{self._str_occurrence_prefix}:"
            f"op{self._int_operation_index}@{int_offset}"
        )

    # 前视方法不改变词元游标。
    def _peek(self) -> ExpressionToken | None:
        """返回当前词元但不推进游标。

        参数:
            self: 当前表达式解析器实例。

        返回:
            下一枚词元；表达式已经消费完毕时返回 None。
        """

        # 游标越过词元末尾表示表达式已经消费完毕。
        if self._int_index >= len(self._tuple_tokens):

            # None 明确通知 Pratt 循环结束当前表达式层。
            return None

        # 返回当前词元但不修改索引。
        return self._tuple_tokens[self._int_index]

    # 推进方法是唯一修改词元游标的位置。
    def _advance(self) -> ExpressionToken:
        """返回当前词元并推进游标。

        参数:
            self: 当前表达式解析器实例。

        返回:
            推进前游标指向的词元。

        异常:
            ExpressionParseError: 表达式已经结束却仍请求词元。
        """

        # 先前视以复用统一的表达式末尾判定。
        obj_token = self._peek()  # 当前待消费词元

        # 调用方越过表达式末尾属于局部语法错误。
        if obj_token is None:

            # 不制造虚假词元，直接阻断当前表达式事实。
            raise ExpressionParseError(
                "> ERR: [Python] unexpected end of Verilog expression"
            )

        # 成功取得词元后再推进游标一次。
        self._int_index += 1  # 游标跨过刚刚返回的词元

        # 返回消费的真实词元供调用方解析。
        return obj_token

    # 固定词元消费用于括号、方括号和三目冒号。
    def _expect(self, str_text: str) -> None:
        """消费指定词元，否则产生可诊断的解析失败。

        参数:
            self: 当前表达式解析器实例。
            str_text: 当前语法位置要求出现的词元文本。

        返回:
            词元匹配时只推进游标，不返回业务值。

        异常:
            ExpressionParseError: 实际词元与要求文本不一致。
        """

        # 读取并消费当前语法位置的实际词元。
        obj_token = self._advance()  # 当前实际词元

        # 不匹配时保留期望值和实际值供局部诊断。
        if obj_token.text != str_text:

            # 当前表达式停止解析，但不会污染其他目标事实。
            raise ExpressionParseError(
                f"> ERR: [Python] expected {str_text!r}, got {obj_token.text!r}"
            )

# module 入口附加连续赋值和过程赋值的类型化表达式事实。
def attach_expression_facts(list_modules: list[dict[str, Any]]) -> None:
    """原位为 formatter module 报告附加组合表达式事实。

    参数:
        list_modules: formatter 已构建的 module 字典列表。

    返回:
        本函数原位写入 comb_expressions，不返回业务值。
    """

    # module 索引参与稳定操作编号，避免跨作用域合并节点。
    for int_module_index, dict_module in enumerate(list_modules):

        # 当前 module 的所有赋值事实按源码结构顺序累积。
        list_facts: list[dict[str, Any]] = []  # 当前 module 组合表达式事实

        # 连续赋值已经由 formatter 分离出左右值和行号。
        for int_assign_index, dict_assign in enumerate(dict_module.get("assigns", [])):

            # 每条 assign 直接形成一个 continuous 驱动事实。
            list_facts.append(
                _expression_fact(
                    str(dict_assign.get("lhs") or ""),
                    str(dict_assign.get("rhs") or ""),
                    f"m{int_module_index}:assign{int_assign_index}",
                    int(
                        dict_assign.get("line_start")
                        or dict_module.get("line_start")
                        or 1
                    ),
                    "continuous",
                )
            )

        # always 控制树需要继承过程类型、循环倍数和控制表达式。
        for int_always_index, dict_always in enumerate(dict_module.get("always", [])):

            # 不可变上下文确保 then、else 和 case 分支互不修改控制栈。
            context = NodeFactContext(  # 当前 always 根遍历上下文
                facts=list_facts,  # 当前 module 共享的事实列表
                prefix=f"m{int_module_index}:always{int_always_index}",  # always 驱动编号前缀
                line=int(  # 为当前 always 事实建立一基源码定位
                    dict_always.get("line_start")  # 优先采用过程自身起始行
                    or dict_module.get("line_start")  # 缺失时回退 module 起始行
                    or 1  # 最终提供有效的一基行号
                ),  # always 根节点定位行号
                process_kind=str(dict_always.get("trigger_kind") or "unknown"),  # formatter 过程分类
                iterations=1,  # 根上下文尚未进入循环
                from_for=False,  # 根上下文归属普通表达式
                controls=(),  # 根上下文没有父控制条件
                skip_first_reset=bool(dict_always.get("reset")),  # 是否排除首个异步复位条件
                reset_signal=str(dict_always.get("reset") or ""),  # formatter 已识别复位信号
            )

            # 从 formatter 控制树根开始递归抽取赋值事实。
            _append_node_facts(list(dict_always.get("nodes", [])), context)

        # module 报告只新增一个类型化事实字段，不改变既有结构节点。
        dict_module["comb_expressions"] = list_facts  # 暴露给 VG 语义引擎的类型化事实

# 控制树分派函数把不同节点交给语义专用处理方法。
def _append_node_facts(
    list_nodes: list[dict[str, Any]],
    context: NodeFactContext,
) -> None:
    """递归转换 formatter 控制树中的赋值、分支和循环。

    参数:
        list_nodes: 当前控制树层级的 formatter 节点列表。
        context: 当前层级不可变遍历上下文。

    返回:
        本函数把赋值事实追加到共享列表，不返回业务值。
    """

    # 节点索引用于生成稳定位置前缀和近似行号。
    for int_node_index, dict_node in enumerate(list_nodes):

        # 当前节点上下文只替换位置字段，其他语义继续继承。
        node_context = replace(  # 当前 formatter 节点遍历上下文
            context,  # 继承当前层过程和控制语义
            prefix=f"{context.prefix}:node{int_node_index}",  # 当前节点稳定位置
            line=context.line + int_node_index,  # 当前节点近似源码行号
        )

        # kind 决定当前节点的专用处理路径。
        str_kind = str(dict_node.get("kind") or "")  # 当前 formatter 节点类型

        # 普通 statement 只尝试提取最外层赋值。
        if str_kind == "statement":

            # 语句处理不会递归其文本，完成后直接进入下一个节点。
            _append_statement_fact(dict_node, node_context)

            # 当前 statement 已处理完毕。
            continue

        # loop 节点更新累计展开倍数并递归 children。
        if str_kind == "loop":

            # 循环专用方法负责未知边界的负一标记。
            _append_loop_facts(dict_node, node_context)

            # 当前 loop 的子树已完整处理。
            continue

        # 条件节点把非复位条件加入 then 和 alternate 控制栈。
        if str_kind == "if":

            # 条件专用方法保证两个分支使用独立不可变上下文。
            _append_if_facts(dict_node, node_context, int_node_index)

            # 当前条件的两个分支均已处理。
            continue

        # case 节点为每个非 default 分支增加 selector 编号。
        if str_kind == "case":

            # case 专用方法遍历所有互斥项，组合锥随后做并集计数。
            _append_case_facts(dict_node, node_context)

            # 当前 case 的全部 item 已处理。
            continue

        # 未知容器仍遍历 formatter 明确提供的两个子节点数组。
        list_children = (  # 未知容器的可见子节点
            list(dict_node.get("children", []))  # 容器主子节点
            + list(dict_node.get("alternate", []))  # 容器备用分支节点
        )

        # 仅依赖结构化 children，禁止回退扫描节点原始文本。
        _append_node_facts(
            list_children,
            replace(node_context, skip_first_reset=False),
        )

# statement 处理只接受 formatter 节点中的顶层赋值形状。
def _append_statement_fact(
    dict_node: dict[str, Any],
    context: NodeFactContext,
) -> None:
    """把一个 formatter statement 转换为赋值事实。

    参数:
        dict_node: 当前 formatter statement 节点。
        context: 当前 statement 的不可变遍历上下文。

    返回:
        匹配赋值时追加一条事实，否则不修改事实列表。
    """

    # 只拆分 formatter 已隔离的 statement，不读取原始文件。
    tuple_assignment = _split_assignment(  # 当前语句的左右值
        str(dict_node.get("text") or "")  # formatter statement 完整文本
    )

    # 非赋值 statement 对目标组合锥没有直接贡献。
    if tuple_assignment is None:

        # 调用方继续处理同层后续节点。
        return

    # 构造右值类型化表达式，并保留局部解析错误。
    dict_fact = _expression_fact(  # 当前过程赋值事实
        tuple_assignment[0],  # 已分离赋值左值
        tuple_assignment[1],  # 已分离赋值右值
        context.prefix,  # 当前 statement 驱动位置
        context.line,  # 当前 statement 近似源码行
        context.process_kind,  # 当前 always 或 continuous 类型
        tuple_assignment[2],  # 保留阻塞或非阻塞赋值语义
    )

    # 累计循环倍数决定 VG147 的 elaborated 操作计数。
    dict_fact["loop_iterations"] = context.iterations  # 当前语句累计展开倍数

    # 循环归属标记决定 VG146/VG147 的唯一报告所有者。
    dict_fact["from_for"] = context.from_for  # 当前语句的循环门禁归属

    # 当前控制栈把条件、case 表达式和 selector 传给组合锥。
    dict_fact["controls"] = list(context.controls)  # 当前语句继承的控制条件快照

    # 分支路径独立于操作计数，用于证明组合赋值是否覆盖所有运行时路径。
    dict_fact["branch_path"] = list(context.branch_path)  # 当前赋值的覆盖路径快照

    # 全局追加序号为同一过程内的 SSA 版本关系提供稳定顺序。
    dict_fact["sequence_index"] = len(context.facts)  # 当前 module 内赋值事实顺序

    # 事实列表保持 formatter 控制树遍历顺序。
    context.facts.append(dict_fact)

# loop 处理把常量迭代次数乘入外层累计倍数。
def _append_loop_facts(
    dict_node: dict[str, Any],
    context: NodeFactContext,
) -> None:
    """递归提取一个 formatter loop 的赋值事实。

    参数:
        dict_node: 当前 formatter loop 节点。
        context: 进入循环前的不可变遍历上下文。

    返回:
        本函数递归追加循环体事实，不返回业务值。
    """

    # 简单常量递增 for 可以静态计算展开次数。
    int_loop_iterations = _for_iterations(  # 当前循环自身展开次数
        str(dict_node.get("header") or "")  # formatter 保留的循环控制头
    )

    # 任一层未知边界会使整个嵌套展开倍数不可确定。
    int_nested_iterations = (  # 进入循环体后的累计展开次数
        -1  # 任一未知边界传播不可确定哨兵
        if context.iterations < 0 or int_loop_iterations < 0  # 外层或当前层不可展开
        else context.iterations * int_loop_iterations  # 嵌套循环硬件复制倍数
    )

    # 子上下文标记循环归属并关闭首复位条件跳过。
    loop_context = replace(  # 当前循环体遍历上下文
        context,  # 继承循环外过程类型与控制栈
        iterations=int_nested_iterations,  # 更新嵌套展开倍数
        from_for=True,  # 循环体目标归属 VG147
        skip_first_reset=False,  # 循环体不再识别过程首复位条件
    )

    # formatter children 是唯一被递归的循环体事实来源。
    _append_node_facts(list(dict_node.get("children", [])), loop_context)

# 条件处理排除异步复位首条件，其余条件进入两侧控制栈。
def _append_if_facts(
    dict_node: dict[str, Any],
    context: NodeFactContext,
    int_node_index: int,
) -> None:
    """递归提取一个 formatter 条件节点的两个分支。

    参数:
        dict_node: 当前 formatter 条件节点。
        context: 进入条件前的不可变遍历上下文。
        int_node_index: 条件在当前节点列表中的序号。

    返回:
        本函数递归追加两个分支事实，不返回业务值。
    """

    # 只有时序块首个条件且 formatter 标记 reset 时才排除控制计数。
    str_condition = _parenthesized_body(  # formatter 已隔离的条件表达式
        str(dict_node.get("header") or "")  # 当前 if 控制头
    )

    # 只有首条件与 formatter 识别出的复位信号规范匹配时才排除。
    bool_is_reset = (  # 当前条件是否为规范异步复位分支
        context.skip_first_reset  # always 确实声明了异步复位
        and int_node_index == 0  # 复位分支必须是过程首条件
        and _is_canonical_reset_condition(str_condition, context.reset_signal)  # 条件只测试复位本身
    )

    # 编译期常量条件只遍历可达分支，避免死分支制造硬件操作误报。
    bool_constant_condition = _constant_condition_value(str_condition)  # 常量条件真值或 None

    # 默认沿用父控制栈，复位条件不会追加功能选择操作。
    tuple_next_controls = context.controls  # 两个分支共同继承的控制栈

    # 普通功能条件需要解析并计入分支目标组合锥。
    if not bool_is_reset and bool_constant_condition is None:

        # formatter header 已隔离完整条件控制头。
        # 局部解析失败被保存为 unsupported 控制事实。
        dict_condition = _safe_parsed_expression(  # 当前条件类型化表达式
            str_condition,  # 已隔离的功能条件表达式
            f"{context.prefix}:condition",  # 条件操作编号前缀
        )

        # 不可变元组确保 then 和 alternate 获得同一条件快照。
        tuple_next_controls = context.controls + (dict_condition,)  # 追加当前功能条件

    # 只有运行时条件需要记录互斥分支；常量条件的可达分支等同无条件路径。
    bool_runtime_condition = bool_constant_condition is None and not bool_is_reset  # 是否形成运行时选择

    # 为真、假覆盖路径生成同一个条件分支键。
    str_branch_id = f"{context.prefix}:if{int_node_index}"  # 当前条件的稳定覆盖编号

    # 标记当前条件是否显式描述了假分支覆盖。
    bool_has_alternate = bool(dict_node.get("alternate"))  # 是否显式覆盖条件假分支

    # 真分支先继承进入当前条件前的覆盖路径。
    tuple_then_path = context.branch_path  # 真分支默认继承父覆盖路径

    # 假分支也从同一父路径开始构造覆盖事实。
    tuple_alternate_path = context.branch_path  # 假分支默认继承父覆盖路径

    # 运行时条件才会在两侧路径中留下互斥选择记录。
    if bool_runtime_condition:

        # 真侧记录用于判定目标是否覆盖当前条件成立路径。
        tuple_then_path += ({"id": str_branch_id, "branch": "then", "complete": bool_has_alternate},)  # 追加当前条件真侧覆盖项

        # 假侧记录与真侧共享分支键但保留相反极性。
        tuple_alternate_path += ({"id": str_branch_id, "branch": "else", "complete": bool_has_alternate},)  # 追加当前条件假侧覆盖项

    # then 分支使用独立位置前缀并关闭复位跳过。
    then_context = replace(  # 当前条件真分支上下文
        context,  # 继承条件前的过程与循环属性
        prefix=f"{context.prefix}:then",  # 真分支操作编号空间
        controls=tuple_next_controls,  # 真分支完整控制栈
        branch_path=tuple_then_path,  # 真分支覆盖路径
        skip_first_reset=False,  # 子分支不再排除复位条件
    )

    # 真分支中的所有目标继承当前功能条件。
    if bool_constant_condition is None or bool_constant_condition:

        # 非常量或常真条件保留真分支事实。
        _append_node_facts(list(dict_node.get("children", [])), then_context)

    # alternate 分支共享条件操作，但保留独立事实编号空间。
    alternate_context = replace(  # 当前条件假分支上下文
        context,  # 复用条件前的过程与循环属性
        prefix=f"{context.prefix}:alternate",  # 假分支操作编号空间
        controls=tuple_next_controls,  # 假分支完整控制栈
        branch_path=tuple_alternate_path,  # 假分支覆盖路径
        skip_first_reset=False,  # 备用分支不是过程首条件
    )

    # 假分支同样参与目标操作并集，而不是只取最大路径。
    if bool_constant_condition is None or not bool_constant_condition:

        # 非常量或常假条件保留假分支事实。
        _append_node_facts(list(dict_node.get("alternate", [])), alternate_context)

# case 处理为所有 item 共享控制表达式，并区分 default selector。
def _append_case_facts(
    dict_node: dict[str, Any],
    context: NodeFactContext,
) -> None:
    """递归提取一个 formatter case 节点的全部分支。

    参数:
        dict_node: 当前 formatter case 节点。
        context: 进入 case 前的不可变遍历上下文。

    返回:
        本函数递归追加所有 case item 事实，不返回业务值。
    """

    # case 控制头最外层括号包含被译码表达式。
    str_case_expression = _parenthesized_body(  # 承载分支译码比较及选择节点计数的公共控制表达式
        str(dict_node.get("header") or "")  # case 节点完整控制头文本
    )

    # 控制表达式解析失败会局部传播到该 case 驱动的目标。
    dict_case_control = _safe_parsed_expression(  # case 公共控制表达式
        str_case_expression,  # case 括号内被译码表达式
        f"{context.prefix}:control",  # 公共控制操作编号前缀
    )

    # 每个 item 使用控制表达式副本保存独立 selector_id。
    for int_item_index, dict_item in enumerate(dict_node.get("items", [])):

        # 浅复制足够隔离新增 selector_id，内部表达式树保持只读。
        dict_item_control = dict(dict_case_control)  # 当前 case item 控制事实

        # 非 default 标签本身也可能包含可综合表达式，必须进入操作并集。
        str_item_label = str(dict_item.get("label") or "").strip()  # 当前 case 标签文本

        # default 不增加 selector，其余 item 各对应一次真实选择操作。
        bool_default_item = (  # 当前 item 是否为 default
            str_item_label.lower() == "default"  # 标准化标签后识别默认项
        )

        # 非默认标签需要解析其表达式，并把标签运算纳入控制锥。
        if not bool_default_item:

            # 容器节点让公共 case 表达式与标签表达式共享同一个控制事实。
            dict_item_control = {
                "kind": "case_match",  # case 匹配控制节点类型
                "operands": [  # 被译码表达式与当前标签表达式
                    dict_case_control,  # case 公共译码表达式
                    _safe_parsed_expression(  # 当前 case 标签的类型化表达式
                        str_item_label,  # 传入解析器的未改写标签原文
                        f"{context.prefix}:item{int_item_index}:label",  # 标签节点稳定编号前缀
                    ),
                ],
            }

        # 显式空编号把 default 语义传递给组合锥分析器。
        dict_item_control["selector_id"] = (  # 当前 item 的分支选择编号
            ""  # default 明确不增加额外选择操作
            if bool_default_item  # 默认分支复用前序译码结果
            else f"{context.prefix}:item{int_item_index}:selector"  # 显式分支选择节点
        )

        # item 位置前缀保证不同分支中的操作编号不可合并。
        item_context = replace(  # 当前 case item 遍历上下文
            context,  # 继承 case 外层过程和循环属性
            prefix=f"{context.prefix}:item{int_item_index}",  # 当前 item 编号空间
            controls=context.controls + (dict_item_control,),  # 追加当前 item 控制事实
            skip_first_reset=False,  # case item 不参与首复位识别
        )

        # 所有互斥分支事实最终按目标做操作节点并集。
        _append_node_facts(list(dict_item.get("children", [])), item_context)

# 单条赋值事实保存目标、驱动来源、循环归属和类型化右值。
def _expression_fact(
    str_target: str,
    str_expression: str,
    str_prefix: str,
    int_line: int,
    str_process_kind: str,
    str_assignment_operator: str = "=",
) -> dict[str, Any]:
    """构造一条目标表达式事实并保留局部解析错误。

    参数:
        str_target: formatter 分离出的赋值左值。
        str_expression: formatter 分离出的赋值右值。
        str_prefix: 当前赋值事实的稳定位置前缀。
        int_line: 当前赋值附近的源码行号。
        str_process_kind: continuous、comb、seq 或 unknown。
        str_assignment_operator: 当前赋值使用的 = 或 <=。

    返回:
        可供组合锥分析器消费的单条赋值事实。
    """

    # 基础字段在解析成功或失败时都保持稳定存在。
    dict_fact: dict[str, Any] = {  # 当前目标表达式事实
        "target": str_target.strip(),  # 去除左值外围空白
        "line": int_line,  # 赋值附近源码行号
        "process_kind": str_process_kind,  # 当前驱动过程类别
        "assignment_operator": str_assignment_operator,  # 阻塞或非阻塞语义
        "driver_id": str_prefix.split(":node", 1)[0],  # assign 或 always 独立来源
        "loop_iterations": 1,  # 非循环事实默认一次硬件实例
        "from_for": False,  # 调用方随后覆盖循环归属
        "controls": [],  # 调用方随后附加控制栈
        "parse_error": "",  # 成功路径保持空错误文本
    }

    # 左值动态选择无法映射到一个 elaboration 后静态端点。
    match_lvalue_select = re.search(r"\[([^]]+)\]", str_target)  # 左值首个选择器

    # 仅十进制常量位选或常量范围被视为静态目标。
    bool_dynamic_lvalue = (  # 左值是否包含动态选择
        match_lvalue_select is not None  # 左值确实包含选择器
        and re.fullmatch(  # 检查索引是否能映射到唯一静态硬件端点
            r"\s*\d+\s*(?::\s*\d+\s*)?",  # 受支持的常量位选或范围
            match_lvalue_select.group(1),  # 方括号内部索引文本
        )
        is None  # 未匹配常量形状即视为动态左值
    )

    # 动态左值只污染当前事实，不阻止同文件其他目标分析。
    if bool_dynamic_lvalue:

        # 局部原因明确指出静态端点合同未满足。
        dict_fact["parse_error"] = "dynamic lvalue selection is not a static endpoint"  # 静态端点合同缺口

        # None 表示不存在可消费的类型化右值根节点。
        dict_fact["expression"] = None  # 动态左值不附带可放行表达式树

        # 返回保留定位与驱动来源的局部不确定事实。
        return dict_fact

    # 右值解析失败必须转换成事实字段，而不是中断整个文件。
    try:

        # 成功时保存完整类型化表达式树。
        dict_fact["expression"] = _parsed_expression(str_expression, str_prefix)  # 类型化右值根节点

    # 专用解析异常仅影响当前目标事实。
    except ExpressionParseError as obj_error:

        # 缺失表达式树禁止组合锥按零操作放行。
        dict_fact["expression"] = None  # 解析失败禁止按空树计数

        # 错误文本进入目标级 inconclusive evidence。
        dict_fact["parse_error"] = str(obj_error)  # 保存当前右值的局部错误

    # 无论成功或失败都返回稳定事实形状。
    return dict_fact

# 解析包装器保持调用点不依赖解析器内部游标实现。
def _parsed_expression(str_expression: str, str_prefix: str) -> dict[str, Any]:
    """解析一个 formatter 已隔离表达式。

    参数:
        str_expression: formatter 已分离的表达式文本。
        str_prefix: 当前表达式的稳定位置前缀。

    返回:
        完整类型化表达式根节点。

    异常:
        ExpressionParseError: 表达式包含未支持或不完整语法。
    """

    # 每次调用创建独立解析器，操作序号不会跨表达式泄漏。
    return ExpressionParser(str_expression, str_prefix).parse()

# 控制条件解析失败时返回显式 unsupported 节点。
def _safe_parsed_expression(str_expression: str, str_prefix: str) -> dict[str, Any]:
    """把控制条件解析失败保留为目标局部不确定事实。

    参数:
        str_expression: formatter 已分离的控制表达式文本。
        str_prefix: 当前控制条件的稳定位置前缀。

    返回:
        类型化表达式根节点或带 parse_error 的 unsupported 节点。
    """

    # 控制表达式失败不应中断 module 其他目标的事实构建。
    try:

        # 解析成功时直接返回类型化控制树。
        return _parsed_expression(str_expression, str_prefix)

    # 专用异常转换为组合锥可识别的局部缺口。
    except ExpressionParseError as obj_error:

        # unsupported 节点保留稳定 operands 字段供统一遍历。
        return {
            "kind": "unsupported",
            "parse_error": str(obj_error),
            "operands": [],
        }

# statement 拆分只接受最外层阻塞或非阻塞赋值。
def _split_assignment(str_statement: str) -> tuple[str, str, str] | None:
    """拆分 formatter statement 中最外层阻塞或非阻塞赋值。

    参数:
        str_statement: formatter 已隔离的完整 statement 文本。

    返回:
        左值、右值和赋值操作符三元组；非赋值语句返回 None。
    """

    # DOTALL 允许 formatter 保留的多行右值被一次完整捕获。
    match_assignment = re.match(  # 识别能够建立目标操作锥的顶层赋值并排除其他过程语句
        r"\s*([A-Za-z_$][\w$]*(?:\s*\[[^]]+\])?)\s*(<=|=)\s*(.*?)\s*;\s*(?://[^\r\n]*)?\s*$",  # 行尾注释不能隐藏赋值事实
        str_statement,  # 待分类的完整过程语句文本
        flags=re.DOTALL,  # 允许右值跨越多行
    )

    # 非赋值 statement 不产生目标事实。
    if match_assignment is None:

        # None 让调用方跳过当前语句并继续控制树遍历。
        return None

    # 捕获组依次是左值、赋值符与完整右值。
    return match_assignment.group(1), match_assignment.group(3), match_assignment.group(2)

# 常量条件助手只折叠无歧义的一位 Verilog 字面量。
def _constant_condition_value(str_condition: str) -> bool | None:
    """返回一位常量条件的确定真值。

    参数:
        str_condition: formatter 已隔离的一位 Verilog 条件文本。

    返回:
        条件可确定时返回布尔真值，其他表达式返回 None。
    """

    # 外围括号不改变一位常量条件的真假。
    str_value = str_condition.strip().strip("() ").lower()  # 标准化条件文本

    # 只接受确定的零和一，含 x/z 的条件不能静态裁剪。
    if str_value in {"0", "1'b0", "1'd0", "1'h0"}:

        # 零值条件只保留 alternate 分支。
        return False

    # 一值条件只保留 then 分支。
    if str_value in {"1", "1'b1", "1'd1", "1'h1"}:

        # 真值返回给调用方执行确定的可达性裁剪。
        return True

    # 非字面量条件保持未知，禁止静态排除任一运行时分支。
    return None

# 复位识别要求条件只测试 formatter 已确认的复位信号。
def _is_canonical_reset_condition(str_condition: str, str_reset_signal: str) -> bool:
    """判断首条件是否为可安全排除的规范复位测试。

    参数:
        str_condition: 时序过程首个 if 的条件文本。
        str_reset_signal: formatter 从敏感表识别出的复位信号名。

    返回:
        条件只测试该复位信号、一元取反或与一位常量比较时返回 True，否则返回 False。
    """

    # 复杂逻辑、比较组合或其他信号不得借 reset 标记跳过计数。
    str_normalized = re.sub(r"\s+", "", str_condition)  # 删除无语义空白

    # 规范化复位名称，避免敏感表外围空白影响精确匹配。
    str_reset = str_reset_signal.strip()  # formatter 提供的复位信号

    # 空复位名不能参与后续精确匹配。
    if not str_reset:

        # formatter 未识别复位信号时必须保留首条件计数。
        return False

    # 直接测试及一元取反是最简复位条件。
    if str_normalized in {str_reset, f"!{str_reset}", f"~{str_reset}"}:

        # 纯复位控制不会形成普通数据 D 锥中的功能组合操作。
        return True

    # Erie 模板常用 reset == 1'b0；仅允许复位名与一位二进制常量精确比较。
    str_escaped_reset = re.escape(str_reset)  # 防止转义字符改变正则边界

    # 相等操作集合同时支持二态与四态复位比较写法。
    str_comparison = r"(?:==|!=|===|!==)"  # 允许二态和四态相等比较

    # 复位比较常量限制为确定的一位零或一，拒绝宽值和未知态。
    str_reset_literal = r"(?:1'b[01]|[01])"  # 仅允许确定的一位零或一

    # 常量位于比较任一侧都只描述同一个异步复位控制。
    return bool(
        re.fullmatch(  # 精确拒绝复位比较之外的附加逻辑
            rf"(?:{str_escaped_reset}{str_comparison}{str_reset_literal}|"
            rf"{str_reset_literal}{str_comparison}{str_escaped_reset})",
            str_normalized,
        )
    )

# 循环边界计算仅支持可在 elaboration 阶段确定的简单递增形状。
def _for_iterations(str_header: str) -> int:
    """计算简单常量递增 for 的展开次数，未知形状返回负一。

    参数:
        str_header: formatter 保留的完整 for 控制头。

    返回:
        非负静态展开次数；未支持或非常量形状返回负一。
    """

    # 同一循环变量必须出现在初始化、条件和加一更新中。
    match_loop = re.fullmatch(  # 简单常量递增循环匹配结果
        r"for\s*\(\s*([A-Za-z_]\w*)\s*=\s*(\d+)\s*;"
        r"\s*\1\s*(<|<=)\s*(\d+)\s*;"
        r"\s*\1\s*=\s*\1\s*\+\s*1\s*\)",
        str_header,  # formatter for 控制头全文
    )

    # 复杂更新、动态边界或其他循环形状保持不确定。
    if match_loop is None:

        # 负一是组合锥识别非常量展开的稳定哨兵。
        return -1

    # 初始化常量是展开区间的闭起点。
    int_start = int(match_loop.group(2))  # 循环初始值

    # 小于等于条件把停止常量转换为开区间上界。
    int_stop = (  # 展开区间开上界
        int(match_loop.group(4))  # 条件中的停止常量
        + (1 if match_loop.group(3) == "<=" else 0)  # 闭上界转换增量
    )

    # 反向空区间产生零次展开，不制造负操作数。
    return max(0, int_stop - int_start)

# 控制头提取只读取最外层首尾括号之间的表达式。
def _parenthesized_body(str_header: str) -> str:
    """返回控制头最外层括号内的表达式。

    参数:
        str_header: formatter 提供的 if、case 或 for 控制头。

    返回:
        最外层首个左括号与最后右括号之间的文本。

    异常:
        ExpressionParseError: 控制头缺少可配对的最外层括号。
    """

    # 首个左括号标记控制表达式开始位置。
    int_open = str_header.find("(")  # 最外层左括号位置

    # 最后右括号允许内部表达式继续包含成对括号。
    int_close = str_header.rfind(")")  # 最外层右括号位置

    # 缺失或反向括号无法隔离可靠控制表达式。
    if int_open < 0 or int_close <= int_open:

        # 局部异常会由控制事实包装器转换为不确定结论。
        raise ExpressionParseError(
            "> ERR: [Python] control header has no balanced expression"
        )

    # 返回括号内部文本，不包含控制关键字和界定符。
    return str_header[int_open + 1 : int_close]

# 分词器按源内顺序产生带偏移的 Verilog 表达式词元。
def _tokenize(str_expression: str) -> Iterator[ExpressionToken]:
    """逐字产生 Verilog 表达式词元。

    参数:
        str_expression: formatter 已隔离的 Verilog 表达式文本。

    返回:
        按源码顺序惰性产生 ExpressionToken 的迭代器。

    异常:
        ExpressionParseError: 遇到当前类型化合同不支持的字符。
    """

    # 字符游标始终指向下一段尚未分词的文本。
    int_index = 0  # 当前字符偏移

    # 持续扫描直到完整消费表达式文本。
    while int_index < len(str_expression):

        # 当前字符用于快速处理空白和单字符运算符。
        str_char = str_expression[int_index]  # 当前待分词字符

        # 空白只分隔词元，不形成表达式节点。
        if str_char.isspace():

            # 跳过一个空白字符并继续扫描。
            int_index += 1  # 空白字符不进入词元流

            # 当前空白无需产生词元。
            continue

        # 多字符运算符必须在标识符和单字符运算符前匹配。
        str_operator = next(  # 当前偏移匹配的最长多字符运算符
            (
                str_item  # 当前候选复合运算符
                for str_item in tuple__multi_operators  # 按长度顺序尝试候选
                if str_expression.startswith(str_item, int_index)  # 匹配当前字符位置
            ),  # 第一个匹配项就是最长运算符
            "",  # 无复合运算符时返回空文本
        )

        # 成功匹配时一次推进完整运算符长度。
        if str_operator:

            # 词元偏移保留运算符首字符位置。
            yield ExpressionToken(str_operator, int_index)

            # 游标跨过完整多字符运算符。
            int_index += len(str_operator)  # 跨过当前复合运算符全文

            # 当前运算符已经完成分词。
            continue

        # 标识符允许 Verilog 系统命名中的美元符号。
        match_identifier = re.match(  # 当前偏移的标识符匹配
            r"[A-Za-z_$][A-Za-z0-9_$]*",  # Verilog 普通及系统标识符形状
            str_expression[int_index:],  # 从当前字符开始的剩余表达式
        )

        # 匹配标识符时保留完整名称而不是逐字符输出。
        if match_identifier is not None:

            # 匹配文本是当前标识符词元。
            str_text = match_identifier.group(0)  # 完整标识符文本

            # 标识符词元供数据依赖提取使用。
            yield ExpressionToken(str_text, int_index)

            # 游标跨过完整标识符。
            int_index += len(str_text)  # 跨过当前完整标识符

            # 当前标识符已经完成分词。
            continue

        # 数值模式覆盖十进制与常见定宽进制常量。
        match_number = re.match(  # 当前偏移的数值常量匹配
            r"(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|\d+",  # 定宽或十进制常量
            str_expression[int_index:],  # 当前偏移之后的待分词文本
        )

        # 数值常量作为无操作叶节点整体输出。
        if match_number is not None:

            # 匹配文本保留原始位宽、进制和未知位字符。
            str_text = match_number.group(0)  # 完整数值常量文本

            # 数值词元不生成组合操作编号。
            yield ExpressionToken(str_text, int_index)

            # 游标跨过完整数值常量。
            int_index += len(str_text)  # 跨过当前完整数值字面量

            # 当前数值已经完成分词。
            continue

        # 加宽和减宽选择模式不在普通二元运算符集合中。
        if str_expression.startswith("+:", int_index) or str_expression.startswith("-:", int_index):

            # 两字符范围模式作为一个选择器词元输出。
            yield ExpressionToken(str_expression[int_index : int_index + 2], int_index)

            # 游标跨过两个模式字符。
            int_index += 2  # 跨过加宽或减宽模式的两个字符

            # 当前范围模式已经完成分词。
            continue

        # 剩余受支持界定符和运算符均为单字符词元。
        if str_char in "()[]{}?:,+-*/%&|^~!<>":

            # 单字符词元保留当前偏移。
            yield ExpressionToken(str_char, int_index)

            # 游标推进到下一字符。
            int_index += 1  # 单字符词元只推进一个位置

            # 当前单字符已经完成分词。
            continue

        # 任何其他字符都不能被静默忽略或按标识符处理。
        raise ExpressionParseError(
            f"> ERR: [Python] unsupported Verilog character {str_char!r} "
            f"at offset {int_index}"
        )

# 常量判定与分词器使用同一 Verilog 数值文本合同。
def _is_constant(str_token: str) -> bool:
    """判断词元是否为十进制或定宽 Verilog 常量。

    参数:
        str_token: 待分类的完整表达式词元文本。

    返回:
        词元满足受支持数值常量语法时返回 True。
    """

    # 完整匹配防止只识别数值前缀而忽略尾随非法字符。
    return (
        re.fullmatch(
            r"(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|\d+",
            str_token,
        )
        is not None
    )
