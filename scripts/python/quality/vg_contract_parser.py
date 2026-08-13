"""执行参数合同、资源结构、信号生存性与握手语义门禁。"""

# future annotations 让内部节点类型可以引用自身。
from __future__ import annotations

# re 负责受限合同词法和 Verilog 标识符扫描。
import re

# dataclass 保存解析节点与参数环境的稳定事实。
from dataclasses import dataclass

# typing 仅描述 formatter 报告中的动态字典结构。
from typing import Any, Iterable

# VgFacts 提供一次 formatter 解析后的全部源码与模块报告。
from .vg_semantic_facts import VgFacts

# VgEvaluation 统一新增规则的公开状态和证据模型。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 参数合同字段使用固定名称，避免模块名作用域重新进入公开接口。
PARAMETER_CONSTRAINTS_KEY = "parameter_constraints"  # design_requirements 中的参数合同键

# 大型 packed 查表的默认深度分析阈值由 catalog 传入。
PACKED_LOOKUP_LIMIT_KEY = "packed_dynamic_lookup_block_bits"  # packed 动态查表阈值键

# 合同 id 必须可稳定地进入报告和后续索引。
CONSTRAINT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")  # 合同 id 的保守命名规则

# Verilog 标识符扫描只接受普通参数名称，不接受系统标识符。
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # 参数标识符校验规则

# 词法匹配覆盖所有允许的运算符和字面量类别。
TOKEN_PATTERN = re.compile(  # TOKEN_PATTERN 在第 34 行保存当前计算结果。
    r"\s+|(?:\d+)'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ]+|\d+|"
    r"[A-Za-z_][A-Za-z0-9_]*|&&|\|\||<=|>=|==|!=|[()+\-*/%<>!]"
)  # 参数表达式词法模式

# ExpressionToken 保存受限表达式的单个词法单元。
@dataclass(frozen=True)
class ExpressionToken:
    """保存参数合同表达式中的词法单元。"""

    # kind 区分标识符、字面量和运算符。
    kind: str  # 词法单元类别

    # text 保留原始文本，便于错误证据回显。
    text: str  # 词法单元文本

# ContractParser 将允许的表达式语法转换为简单节点元组。
class ContractParser:
    """解析参数合同允许的整数和布尔表达式。"""

    # 初始化 parser 的词法游标和原始表达式。
    def __init__(self, expression: str) -> None:
        """创建一个受限合同表达式解析器。

        参数:
            expression: 待检查的参数合同表达式。
        返回:
            无；解析器只保存词法和游标状态。
        """

        # 保存原始文本以便诊断非法字符位置。
        self.expression = expression  # 当前合同原文

        # 词法阶段同时完成非法字符拒绝。
        self.tokens = self._tokenize(expression)  # 当前合同的完整词法序列

        # 游标从第一个有效 token 开始。
        self.index = 0  # 当前读取位置

    # parse 返回节点和其引用的参数名称集合。
    def parse(self) -> tuple[tuple[Any, ...], frozenset[str]]:
        """解析完整合同并拒绝尾部未消费 token。

        参数:
            无。
        返回:
            语法节点和其中引用的参数名称集合。
        异常:
            ValueError: 表达式语法或词法不受支持。
        """

        # 空表达式没有可以执行的合同语义。
        if not self.tokens:

            # 通过明确错误保留 fail-closed 行为。
            raise ValueError("> ERR: [Python] parameter contract expression is empty.")

        # 逻辑或层级是完整表达式的最高入口。
        tuple_node = self._parse_or()  # 当前合同语法树

        # 未消费 token 表示出现了未允许的语法尾缀。
        if self.index != len(self.tokens):

            # 指出第一个未处理 token，避免静默截断合同。
            raise ValueError(f"> ERR: [Python] unexpected parameter contract token {self.tokens[self.index].text!r}.")

        # 从节点递归提取所有标识符，供自动适用规则使用。
        set_identifiers = frozenset(_collect_identifiers(tuple_node))  # 当前合同引用的参数集合

        # 返回完整节点和自动作用域事实。
        return tuple_node, set_identifiers

    # _tokenize 把输入转换为不含空白的受限 token。
    def _tokenize(self, expression: str) -> tuple[ExpressionToken, ...]:
        """词法化合同表达式并拒绝未覆盖字符。

        参数:
            expression: 待词法化的表达式文本。
        返回:
            固定顺序的词法单元元组。
        异常:
            ValueError: 出现未被允许的字符。
        """

        # 逐段匹配，避免正则模式在非法字符处跳过输入。
        list_tokens: list[ExpressionToken] = []  # 当前合同词法结果

        # offset 表示下一次匹配必须开始的位置。
        int_offset = 0  # 当前词法扫描偏移

        # 逐项读取最长可识别 token。
        while int_offset < len(expression):

            # 当前 token 必须从游标位置开始匹配。
            obj_match = TOKEN_PATTERN.match(expression, int_offset)  # 当前词法匹配

            # 缺失匹配说明表达式含有禁止字符。
            if obj_match is None:

                # 错误位置帮助调用方定位合同输入。
                raise ValueError(f"> ERR: [Python] unsupported parameter contract character at offset {int_offset}.")

            # 保留原 token 文本，空白单元只推进游标。
            str_text = obj_match.group(0)  # 当前 token 原文

            # 只有非空白 token 进入 parser。
            if not str_text.isspace():

                # 标识符和数字按文本形态分出解析类别。
                str_kind = (
                    "identifier"  # 参数标识符
                    if IDENTIFIER_PATTERN.fullmatch(str_text)  # 当前表达式 在第 145 行保存当前计算结果。
                    else "literal"  # 数值字面量或运算符
                )

                # 定宽字面量和裸数字都交给字面量解析器。
                if str_kind == "literal" and (
                    str_text[0].isdigit() or str_text[0] == "'"
                ):

                    # 记录字面量 token。
                    list_tokens.append(ExpressionToken("literal", str_text))

                # 其余 token 由递归下降 parser 按运算符处理。
                else:

                    # 任何标识符或运算符都保留原始文本。
                    list_tokens.append(ExpressionToken(str_kind, str_text))

            # 游标推进到当前 token 之后。
            int_offset = obj_match.end()  # 下一次词法扫描位置

        # 返回固定词法序列，避免调用方重新扫描。
        return tuple(list_tokens)

    # _peek 返回当前 token，末尾使用空 token 哨兵。
    def _peek(self) -> ExpressionToken:
        """读取当前 token 而不移动 parser 游标。

        参数:
            无。
        返回:
            当前 token 或表达式尾部哨兵。
        """

        # 尾部哨兵让解析函数可以统一判断结束条件。
        if self.index >= len(self.tokens):

            # 空文本不可能通过任何运算符匹配。
            return ExpressionToken("end", "")

        # 返回当前有效 token。
        return self.tokens[self.index]

    # _take 在 token 文本匹配时推进游标。
    def _take(self, text: str) -> bool:
        """尝试消费指定文本的当前 token。

        参数:
            text: 需要匹配的运算符或括号文本。
        返回:
            当前 token 匹配并已消费时为 True。
        """

        # 只有精确文本匹配才推进语法游标。
        if self._peek().text != text:

            # 不匹配时保留游标供上层继续选择分支。
            return False

        # 消费当前 token 并返回成功标记。
        self.index += 1  # 消费当前 token 后推进 parser 游标。

        # 返回 token 消费结果，供上层解析分支选择。
        return True

    # 逻辑或层级消费逻辑与节点并构造左结合语法树。
    def _parse_or(self) -> tuple[Any, ...]:
        """解析逻辑或表达式。

        参数:
            无；从 parser 当前游标继续读取。
        返回:
            逻辑或节点。
        """

        # 先解析更高优先级的逻辑与左操作数。
        tuple_node = self._parse_and()  # 逻辑或左节点

        # 同优先级运算符按左结合顺序展开。
        while self._take("||"):

            # 右侧继续从逻辑与层开始解析。
            tuple_node = ("binary", "||", tuple_node, self._parse_and())  # 逻辑或节点保留左结合顺序。

        # 返回当前优先级构造的节点。
        return tuple_node

    # 逻辑与层级继续消费比较节点，保留运算符优先级。
    def _parse_and(self) -> tuple[Any, ...]:
        """解析逻辑与表达式。

        参数:
            无；从 parser 当前游标继续读取。
        返回:
            逻辑与节点。
        """

        # 先解析比较表达式左操作数。
        tuple_node = self._parse_compare()  # 逻辑与左节点

        # 逻辑与按左结合方式串联。
        while self._take("&&"):

            # 右侧继续从比较层解析。
            tuple_node = ("binary", "&&", tuple_node, self._parse_compare())  # 逻辑与节点连接比较结果。

        # 返回逻辑与节点。
        return tuple_node

    # 比较层级拒绝链式比较，避免产生不同于 Verilog 的隐式含义。
    def _parse_compare(self) -> tuple[Any, ...]:
        """解析最多一个比较运算符。

        参数:
            无；从 parser 当前游标继续读取。
        返回:
            比较或算术节点。
        异常:
            ValueError: 出现链式比较时抛出。
        """

        # 先解析加减左操作数。
        tuple_node = self._parse_add()  # 比较左节点

        # 比较运算符集合保持与合同语法一致。
        if self._peek().text in {"<", "<=", ">", ">=", "==", "!="}:

            # 保存比较运算符并消费它。
            str_operator = self._peek().text  # 当前比较运算符

            # 推进比较运算符后的 parser 游标。
            self.index += 1  # 消费比较运算符后推进 parser 游标。

            # 比较右侧不能继续出现同优先级链式比较。
            tuple_right = self._parse_add()  # 比较右节点

            # 记录 tuple_node，供当前规则的后续分支使用。
            tuple_node = ("binary", str_operator, tuple_node, tuple_right)  # 组装比较节点供条件求值。

            # 链式比较会造成与 Verilog 不同的隐式语义。
            if self._peek().text in {"<", "<=", ">", ">=", "==", "!="}:

                # 需要调用方显式写出逻辑连接关系。
                raise ValueError("> ERR: [Python] chained parameter contract comparisons are not supported.")

        # 返回比较或加减节点。
        return tuple_node

    # 加减层级把乘除节点按左结合顺序串联。
    def _parse_add(self) -> tuple[Any, ...]:
        """解析加减表达式。

        参数:
            无；从 parser 当前游标继续读取。
        返回:
            加减节点。
        """

        # 乘除层提供左操作数。
        tuple_node = self._parse_mul()  # 加减左节点

        # 加减按左结合方式逐项生成节点。
        while self._peek().text in {"+", "-"}:

            # 消费当前加减运算符。
            str_operator = self._peek().text  # 当前加减运算符

            # 推进加减运算符后的 parser 游标。
            self.index += 1  # 消费加减运算符后推进 parser 游标。

            # 右操作数从乘除层重新开始。
            tuple_node = ("binary", str_operator, tuple_node, self._parse_mul())  # 组装加减节点保持左结合。

        # 返回加减节点。
        return tuple_node

    # 乘除层级把一元节点按左结合顺序串联。
    def _parse_mul(self) -> tuple[Any, ...]:
        """解析乘除取模表达式。

        参数:
            无；从 parser 当前游标继续读取。
        返回:
            乘除或取模节点。
        """

        # 一元层提供左操作数。
        tuple_node = self._parse_unary()  # 乘除左节点

        # 乘除取模按左结合方式逐项生成节点。
        while self._peek().text in {"*", "/", "%"}:

            # 消费当前乘除运算符。
            str_operator = self._peek().text  # 当前乘除运算符

            # 推进乘除运算符后的 parser 游标。
            self.index += 1  # 消费乘除运算符后推进 parser 游标。

            # 右操作数从一元层重新开始。
            tuple_node = ("binary", str_operator, tuple_node, self._parse_unary())  # 组装乘除节点保持左结合。

        # 返回乘除节点。
        return tuple_node

    # 一元层级先消费前缀，再把基本项交给 primary parser。
    def _parse_unary(self) -> tuple[Any, ...]:
        """解析逻辑非和算术负号。

        参数:
            无；从 parser 当前游标继续读取。
        返回:
            一元或基本项节点。
        """

        # 一元运算符递归读取连续前缀。
        if self._peek().text in {"!", "-"}:

            # 保存并消费当前一元运算符。
            str_operator = self._peek().text  # 当前一元运算符

            # 推进一元运算符后的 parser 游标。
            self.index += 1  # 消费一元运算符后推进 parser 游标。

            # 递归解析运算符之后的操作数。
            return ("unary", str_operator, self._parse_unary())

        # 没有一元前缀时进入基本项解析。
        return self._parse_primary()

    # primary 层级读取字面量、标识符和成对括号。
    def _parse_primary(self) -> tuple[Any, ...]:
        """解析字面量、标识符或括号表达式。

        参数:
            无；从 parser 当前游标继续读取。
        返回:
            基本项语法节点。
        异常:
            ValueError: 操作数缺失或括号不闭合时抛出。
        """

        # 左括号进入递归表达式并要求成对闭合。
        if self._take("("):

            # 括号内部仍从最高优先级入口开始解析。
            tuple_node = self._parse_or()  # 括号内部节点

            # 未闭合括号必须明确报错。
            if not self._take(")"):

                # 禁止把缺失括号当作表达式尾部。
                raise ValueError("> ERR: [Python] parameter contract parenthesis is not closed.")

            # 返回括号包裹后的语义节点。
            return tuple_node

        # 读取当前 token 作为字面量或标识符。
        obj_token = self._peek()  # 当前基本项 token

        # token 不是基本项时说明运算符位置缺少操作数。
        if obj_token.kind not in {"literal", "identifier"}:

            # 错误文本包含原始 token，方便修正规格。
            raise ValueError(f"> ERR: [Python] expected parameter contract operand, got {obj_token.text!r}.")

        # 消费已经确认的基本项 token。
        self.index += 1  # 消费基本项 token 后推进 parser 游标。

        # 标识符节点保持名称，数值节点立即转换为整数或未知。
        if obj_token.kind == "identifier":

            # 返回参数引用节点。
            return ("identifier", obj_token.text)

        # 字面量解析失败时保留 unknown，而非猜测数值。
        return ("literal", _literal_value(obj_token.text))

# _collect_identifiers 递归收集合同节点中的参数引用。
def _collect_identifiers(node: tuple[Any, ...]) -> set[str]:
    """返回表达式节点引用的全部标识符。

    参数:
        node: 受限合同语法树节点。
    返回:
        当前节点及子节点引用的标识符集合。
    """

    # 初始化当前节点的名称集合。
    set_names: set[str] = set()  # 当前节点的参数名称集合

    # 标识符节点只有一个名称字段。
    if node[0] == "identifier":

        # 把参数名称加入当前合同作用域集合。
        set_names.add(str(node[1]))

    # 一元节点递归读取一个子节点。
    elif node[0] == "unary":

        # 合并一元操作数中的参数名称。
        set_names.update(_collect_identifiers(node[2]))

    # 二元节点递归读取左右节点。
    elif node[0] == "binary":

        # 左右参数集合共同形成约束自动作用域。
        set_names.update(_collect_identifiers(node[2]))

        # 合并右侧二元节点中的参数引用。
        set_names.update(_collect_identifiers(node[3]))

    # 返回当前节点及其子节点的名称集合。
    return set_names

# _literal_value 解析受限 Verilog 数字字面量。
def _literal_value(text: str) -> int | None:
    """把裸数字或定宽 Verilog 数字转换为非负整数。

    参数:
        text: Verilog 数字字面量原文。
    返回:
        可确定的整数；未知态或非法形式返回 None。
    """

    # 裸十进制数字直接转换，不引入 Python 表达式执行。
    if text.isdecimal():

        # 返回无定宽的十进制值。
        return int(text)

    # 定宽字面量必须符合 Verilog 的宽度、符号和进制结构。
    obj_match = re.fullmatch(r"(\d+)'([sS])?([bBoOdDhH])([0-9a-fA-F_xXzZ]+)", text)  # 校验定宽数字的进制和位宽。

    # 非法定宽形式保持未知。
    if obj_match is None:

        # 调用方会把未知值转为 inconclusive。
        return None

    # 未知态数字不能作为参数合同的确定整数。
    if any(char in obj_match.group(4).lower() for char in ("x", "z")):

        # 保守地拒绝含未知态的合同字面量。
        return None

    # 不同进制使用 int 的显式 base 转换。
    dict_bases = {"b": 2, "o": 8, "d": 10, "h": 16}  # Verilog 进制到 Python base 的映射

    # 读取不含下划线的数字部分。
    str_digits = obj_match.group(4).replace("_", "")  # 定宽字面量数字部分

    # 转换原始数值并按声明宽度截断。
    int_value = int(str_digits, dict_bases[obj_match.group(3).lower()])  # 定宽字面量原始整数

    # 截断宽度决定定宽字面量保留的低位数。
    int_width = int(obj_match.group(1)  # 定宽字面量声明位宽
    )

    # Verilog 定宽值只保留低位，避免把超宽常量传入合同。
    return int_value & ((1 << int_width) - 1)

# _evaluate_node 在确定参数环境中执行合同节点。
def _evaluate_node(node: tuple[Any, ...], values: dict[str, int]) -> int | None:
    """按受限整数语义求值单个合同节点。

    参数:
        node: 受限合同语法树节点。
        values: 当前模块公开参数的确定值环境。
    返回:
        可确定的整数或布尔值；未知结果返回 None。
    """

    # 字面量节点直接返回已解析的整数或未知。
    if node[0] == "literal":

        # 返回字面量值。
        return node[1]

    # 标识符节点从当前特化环境读取整数值。
    if node[0] == "identifier":

        # 不存在的名称保持未知，禁止默认零值。
        return values.get(str(node[1]))

    # 一元节点先求操作数。
    if node[0] == "unary":

        # 未知操作数不能被一元运算伪装成确定值。
        int_operand = _evaluate_node(node[2], values)  # 一元操作数值

        # 一元操作数未知时直接传播未知状态。
        if int_operand is None:

            # 保留 fail-closed 结论。
            return None

        # 逻辑非输出 Verilog 风格的一位布尔整数。
        if node[1] == "!":

            # 非零值视为真，结果取反。
            return int(not int_operand)

        # 当前唯一剩余一元运算为算术负号。
        return -int_operand

    # 二元节点递归求出左右值。
    int_left = _evaluate_node(node[2], values)  # 二元左操作数值

    # 右操作数参与逻辑、比较和算术三类求值。
    int_right = _evaluate_node(node[3], values)  # 二元右操作数值

    # 逻辑运算支持确定的短路结果和未知传播。
    if node[1] == "&&":

        # 左侧为零时，无需知道右侧即可确定结果为假。
        if int_left == 0:

            # Verilog 逻辑与结果为零。
            return 0

        # 右侧为零同样可以确定结果为假。
        if int_right == 0:

            # 返回逻辑与的确定假值。
            return 0

        # 任一侧未知且没有确定假值时保留未知。
        if int_left is None or int_right is None:

            # 禁止把未知条件当成真。
            return None

        # 两侧均为非零时返回逻辑真。
        return 1

    # 逻辑或在一侧为真时可以安全短路。
    if node[1] == "||":

        # 任一确定非零值使逻辑或为真。
        if int_left not in (None, 0) or int_right not in (None, 0):

            # 返回逻辑或的确定真值。
            return 1

        # 两侧均未知时不能确定结果。
        if int_left is None or int_right is None:

            # 传播未知状态。
            return None

        # 两侧均为零时返回假。
        return 0

    # 普通算术和比较在任一侧未知时全部保持未知。
    if int_left is None or int_right is None:

        # 未知输入不能得到可靠合同结论。
        return None

    # 比较运算统一返回一位布尔整数。
    dict_comparisons = {
        "<": int_left < int_right,  # 小于比较
        "<=": int_left <= int_right,  # 小于等于比较
        ">": int_left > int_right,  # 大于比较
        ">=": int_left >= int_right,  # 大于等于比较
        "==": int_left == int_right,  # 相等比较
        "!=": int_left != int_right,  # 不等比较
    }  # 比较运算结果表

    # 命中比较运算符时返回布尔整数。
    if node[1] in dict_comparisons:

        # Python 布尔值显式折算为 Verilog 风格整数。
        return int(dict_comparisons[node[1]])

    # 除法和取模必须先拒绝零除数。
    if node[1] in {"/", "%"} and int_right == 0:

        # 零除数保持未知，交由严格门禁阻断。
        return None

    # 算术运算只执行合同语法允许的操作。
    dict_arithmetic = {
        "+": int_left + int_right,  # 使用确定整数执行加法。
        "-": int_left - int_right,  # 使用确定整数执行减法。
        "*": int_left * int_right,  # 使用确定整数执行乘法。
        "/": int_left // int_right,  # 使用确定整数执行整除。
        "%": int_left % int_right,  # 使用确定整数执行取模。
    }  # 算术运算结果表

    # 返回已确认的算术运算结果。
    return dict_arithmetic.get(node[1])

# _module_parameter_values 按声明顺序重算公开 parameter 的默认环境。
def _module_parameter_values(dict_module: dict[str, Any]) -> tuple[dict[str, int], tuple[str, ...]]:
    """返回当前模块的公开参数值和无法求值的参数名称。

    参数:
        dict_module: formatter 生成的模块事实。
    返回:
        确定参数环境与未知参数名称元组。
    """

    # 保存前序参数值，遵循 Verilog 声明顺序可见性。
    dict_values: dict[str, int] = {}  # 当前模块已确定的参数环境

    # 保存未知默认值，供 strict 规则生成证据。
    list_unknown_names: list[str] = []  # 当前模块无法求值的参数名称

    # parameter 只读取 formatter 的公开 params 区域。
    for dict_parameter in dict_module.get("params", []) or []:

        # 参数声明名称必须存在且保持普通标识符形态。
        str_name = str(dict_parameter.get("name") or "")  # 当前公开参数名称

        # 解析当前参数默认表达式。
        str_expression = str(dict_parameter.get("value") or "")  # 当前参数默认表达式

        # 空名称或非法表达式均记录为未知。
        if not IDENTIFIER_PATTERN.fullmatch(str_name):

            # 保留异常名称以便报告覆盖缺口。
            list_unknown_names.append(str_name or "<missing>")

            # 跳过名称非法的参数，继续检查后续声明。
            continue

        # 复用合同 parser 支持常量和前序参数引用。
        try:

            # 解析并执行参数默认表达式。
            tuple_node, _ = ContractParser(str_expression).parse()  # 默认值节点和引用集合

            # 仅把可确定的默认值放入参数环境。
            int_value = _evaluate_node(tuple_node, dict_values)  # 当前参数确定值

        # 默认表达式不属于受限合同语法时保持未知。
        except (TypeError, ValueError):

            # 用 None 进入未知参数集合。
            int_value = None  # 当前默认值无法求值

        # 未知值不能进入后续参数或约束环境。
        if int_value is None:

            # 记录当前参数未知原因。
            list_unknown_names.append(str_name)

            # 跳过无法确定的默认值，保留未知参数证据。
            continue

        # 保存确定的公开参数值。
        dict_values[str_name] = int_value  # 当前参数确定整数

    # localparam 参与宽度和资源计算，但不扩大 VG151 的公开 parameter 覆盖集合。
    for dict_localparam in dict_module.get("localparams", []) or []:

        # 局部常量必须沿声明顺序使用前序参数环境求值。
        str_name = str(dict_localparam.get("name") or "")  # 当前局部常量名称

        # 非法名称不进入资源计算环境，也不伪装成公开参数缺口。
        if not IDENTIFIER_PATTERN.fullmatch(str_name):

            # formatter 已负责报告非法 localparam 结构。
            continue

        # 复用同一受限 parser 解析局部常量默认表达式。
        str_expression = str(dict_localparam.get("value") or "")  # 当前局部常量默认表达式

        # 开始解析当前 localparam 的受限默认表达式。
        try:

            # 局部常量只能引用已经出现的参数或 localparam。
            tuple_node, _ = ContractParser(str_expression).parse()  # 局部常量默认值节点

            # 只有确定值才用于 packed 范围求值。
            int_value = _evaluate_node(tuple_node, dict_values)  # 当前局部常量确定值

        # 受限表达式之外的 localparam 保持未知。
        except (TypeError, ValueError):

            # 无法求值的 localparam 保持缺省未知，不扩大公开参数报告。
            int_value = None  # 当前局部常量无法确定

        # 确定的局部常量可作为后续声明的宽度环境。
        if int_value is not None:

            # 保存局部常量值供 packed/resource 规则使用。
            dict_values[str_name] = int_value  # 当前局部常量确定整数

    # 返回参数和局部常量环境，以及公开参数未知名称。
    return dict_values, tuple(list_unknown_names)

