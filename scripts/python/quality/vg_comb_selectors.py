"""解释 formatter 类型化选择节点并建立静态输出逐位映射。"""

# 延迟注解求值避免运行时扩大模型依赖。
from __future__ import annotations

# 正则只解释类型化 select 节点的常量端点文本。
import re

# Any 限定 formatter JSON 兼容事实边界。
from typing import Any

# 静态输出模型冻结成功映射或单一局部原因。
from .vg_comb_model import StaticOutputMap

# 常量真值辅助函数只解释 formatter 已确认的整数字面量。
def constant_truth_value(dict_expression: dict[str, Any]) -> bool | None:
    """把简单 Verilog 整数字面量转换为可确定真值。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        可确定常量的布尔值；非整数字面量或含未知位时返回 None。
    """

    # 只有 formatter 明确标记的常量节点可进入字面量转换。
    if str(dict_expression.get("kind") or "") != "constant":

        # 其他节点依赖运行时值，不能静态折叠。
        return None

    # 去除数字分隔符并统一进制标志大小写。
    str_value = str(dict_expression.get("value") or "").replace("_", "").lower()  # 规范化字面量文本

    # x、z 与问号位都不具有确定布尔值。
    if any(str_unknown in str_value for str_unknown in ("x", "z", "?")):

        # 未知位禁止按零或非零常量剪枝。
        return None

    # 数值转换失败时保持局部未知，不抛出到组合锥主流程。
    try:

        # 定宽 Verilog 字面量从撇号后读取进制和数值载荷。
        if "'" in str_value:

            # 位宽位于撇号之前，不参与数值转换。
            str_payload = str_value.split("'", 1)[1]  # 进制标志与数字载荷

            # 首字符是 Verilog 进制标志。
            str_base = str_payload[:1]  # 当前字面量进制标志

            # 显式映射限制为受支持的二、八、十和十六进制。
            int_base = {"b": 2, "o": 8, "d": 10, "h": 16}.get(str_base)  # Python 转换基数

            # 未识别进制不能形成确定常量值。
            if int_base is None:

                # 保守返回未知，避免错误剪枝。
                return None

            # 数字载荷非零即为逻辑真。
            return int(str_payload[1:], int_base) != 0

        # 无撇号普通整数按十进制解释。
        return int(str_value, 10) != 0

    # 非法数字载荷保留为不可确定条件。
    except ValueError:

        # 解析缺口不应中断其他目标分析。
        return None

# 综合可达性辅助函数统一剪除常量三目的死分支。
def runtime_operands(dict_expression: dict[str, Any]) -> list[dict[str, Any]]:
    """返回综合后仍可达的类型化操作数。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        已剪除常量三目死分支的操作数列表。
    """

    # 只保留满足类型化表达式合同的字典操作数。
    list_operands = [  # 当前节点的全部类型化操作数
        dict_operand  # 可继续递归遍历的操作数节点
        for dict_operand in dict_expression.get("operands", [])  # formatter 原始操作数
        if isinstance(dict_operand, dict)  # 排除非表达式叶值
    ]

    # 非三目节点或非标准三操作数形状保持原始可达集合。
    if str(dict_expression.get("kind") or "") != "ternary" or len(list_operands) != 3:

        # 普通节点的全部类型化操作数都可达。
        return list_operands

    # 常量条件允许在综合前确定唯一可达分支。
    bool_condition = constant_truth_value(list_operands[0])  # 三目条件的确定真值

    # 运行时条件必须保留条件、真分支和假分支。
    if bool_condition is None:

        # 未知条件禁止静态剪除任一分支。
        return list_operands

    # 常真选择第一分支，常假选择第二分支。
    return [list_operands[1] if bool_condition else list_operands[2]]

# 基础信号只用于精确端点不存在时的保守依赖回退。
def base_target(str_target: str) -> str:
    """把静态选择目标规范为当前基础信号。

    参数:
        str_target: formatter 赋值事实中的目标文本。

    返回:
        去除首个选择器并清理空白后的基础信号名称。
    """

    # 第一个左方括号之前的文本就是当前基础目标。
    return str_target.split("[", 1)[0].strip()

# 位选和切片使用不同文本形状，单独封装可降低引用提取分支数。
def static_selector_text(list_indices: list[str], str_separator: str) -> str:
    """把 formatter 常量索引恢复为位选或切片正文。

    参数:
        list_indices: 按源码顺序保存的常量索引文本。
        str_separator: bit 标记或 formatter 保留的切片分隔符。

    返回:
        可直接放入方括号的静态选择器正文。
    """

    # 单比特选择只需要第一个常量索引。
    if str_separator == "bit":

        # 保持原有单索引端点文本。
        return list_indices[0]

    # 切片按 formatter 给出的分隔符连接两个边界。
    return f"{list_indices[0]}{str_separator}{list_indices[1]}"

# 选择节点辅助函数负责恢复常量位选并隔离动态选择。
def selected_reference_targets(dict_expression: dict[str, Any]) -> set[str]:
    """提取一个选择表达式引用的静态或保守基础端点。

    参数:
        dict_expression: kind 为 select 的 formatter 表达式节点。

    返回:
        可完整恢复时返回精确选择端点，否则返回基础信号集合。
    """

    # 第一个操作数是被选择对象，其余操作数描述索引或切片边界。
    list_operands = list(dict_expression.get("operands", []))  # 选择节点的基础值与索引节点

    # 缺少类型化基础值时没有可信引用可供上游追踪。
    if not list_operands or not isinstance(list_operands[0], dict):

        # 空集合让调用方保持当前表达式的局部依赖边界。
        return set()

    # 基础表达式可能自身包含可解析的静态选择端点。
    set_base_targets = reference_targets(list_operands[0])  # 被选择对象对应的基础端点集合

    # 动态索引或多基础引用只能保守回退到整信号。
    bool_static_single_base = (  # 当前选择是否具备唯一静态基础端点
        not bool(dict_expression.get("dynamic"))  # formatter 已确认索引不是运行时表达式
        and len(set_base_targets) == 1  # 选择器只能附着到一个明确基础端点
    )

    # 无法精确恢复时仍保留所有基础生产者依赖。
    if not bool_static_single_base:

        # 去除嵌套选择器，避免构造不存在的精确生产者名称。
        return {base_target(str_item) for str_item in set_base_targets}

    # 常量索引文本按 formatter 操作数顺序组成位选或切片。
    list_indices = [  # 当前选择器包含的常量索引文本
        str(dict_item.get("value") or "")  # 单个索引或切片边界文本
        for dict_item in list_operands[1:]  # 跳过第一个基础表达式操作数
        if isinstance(dict_item, dict)  # 仅接受 formatter 类型化索引节点
    ]

    # 任一索引节点缺失都会使精确选择器无法重建。
    if len(list_indices) != len(list_operands) - 1:

        # 不完整索引退回基础生产者，防止伪造静态端点。
        return {base_target(str_item) for str_item in set_base_targets}

    # formatter operator 区分单比特选择和带方向的切片。
    str_separator = str(dict_expression.get("operator") or "bit")  # 选择器种类或切片分隔符

    # 选择器文本保持原有位选与切片的格式语义。
    str_selector = static_selector_text(list_indices, str_separator)  # 已恢复的常量选择器正文

    # 唯一基础端点从单元素集合中确定取出。
    str_base_target = next(iter(set_base_targets))  # 选择器附着的基础端点文本

    # 返回与静态左值端点格式一致的引用名称。
    return {f"{str_base_target}[{str_selector}]"}

# 引用端点提取保留可静态确定的位选和切片。
def reference_targets(dict_expression: dict[str, Any]) -> set[str]:
    """提取表达式引用的精确静态端点，动态选择回退基础信号。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        当前表达式引用的静态端点或保守基础信号集合。
    """

    # 静态选择可重建为与左值一致的规范端点文本。
    if dict_expression.get("kind") == "select":

        # 选择节点由专用辅助函数处理静态索引和动态回退。
        return selected_reference_targets(dict_expression)

    # 普通节点递归合并全部数据引用。
    set_references: set[str] = set()  # 当前普通表达式累计的引用端点

    # 标识符叶节点直接贡献自身名称。
    if dict_expression.get("kind") == "identifier":

        # 名称保持 formatter 输出，选择器仅由选择节点补充。
        set_references.add(str(dict_expression.get("name") or ""))

    # 复合表达式只遍历综合后可达的操作数。
    for dict_operand in runtime_operands(dict_expression):

        # 只有类型化字典节点才能递归提取引用。
        if isinstance(dict_operand, dict):

            # 子树端点合并后由集合去除重复引用。
            set_references.update(reference_targets(dict_operand))

    # 返回普通表达式完整的去重引用集合。
    return set_references

# 常量折叠只接受完全不含标识符或动态选择的表达式树。
def is_constant_expression(dict_expression: dict[str, Any]) -> bool:
    """判断表达式是否完全由常量叶节点构成。

    参数:
        dict_expression: formatter 输出的类型化表达式节点。

    返回:
        全部叶节点可在 elaboration 阶段确定时返回 True。
    """

    # 节点种类决定常量叶、运行时叶与复合表达式的分流。
    str_kind = str(dict_expression.get("kind") or "")  # 当前待判定节点的 formatter 种类

    # 常量叶节点自身满足折叠条件。
    if str_kind == "constant":

        # 字面量无需继续检查子树。
        return True

    # 标识符、未支持结构和动态选择依赖运行时信号。
    if str_kind in {"identifier", "unsupported"} or bool(dict_expression.get("dynamic")):

        # 任一运行时依赖都会阻止整棵子树常量折叠。
        return False

    # 复合节点只检查 formatter 已类型化的操作数子树。
    list_operands = [  # 当前复合表达式的类型化操作数
        dict_item  # 可递归判断常量性的操作数节点
        for dict_item in dict_expression.get("operands", [])  # formatter 提供的全部操作数
        if isinstance(dict_item, dict)  # 排除不具备表达式合同的叶值
    ]

    # 非空操作数全部为常量时，当前运算也可在 elaboration 阶段折叠。
    return bool(list_operands) and all(is_constant_expression(dict_item) for dict_item in list_operands)

# 顶层覆盖判定把 formatter 事实转换成统一的递归路径合同。
def facts_cover_all_paths(list_facts: list[dict[str, Any]]) -> bool:
    """判断同一目标的事实集合是否覆盖完整运行时控制树。

    参数:
        list_facts: 同一静态端点的全部 formatter 驱动事实。

    返回:
        存在无条件赋值或每层决策的两个分支均完整时返回 True。
    """

    # 每条赋值只保留 formatter 记录的互斥分支路径。
    list_paths = [  # 当前目标全部赋值的运行时分支路径
        list(dict_fact.get("branch_path", []))  # 单条事实的可变递归副本
        for dict_fact in list_facts  # 收集同一端点各赋值的控制路径
    ]

    # 顶层与嵌套层使用同一个分支完备性证明算法。
    return paths_cover_all(list_paths)

# 递归覆盖证明要求同一决策的 then 与 else 子树分别完整。
def paths_cover_all(list_paths: list[list[dict[str, Any]]]) -> bool:
    """递归证明当前父分支下的全部运行时路径均有赋值。

    参数:
        list_paths: 当前父分支下各赋值尚未消费的决策路径。

    返回:
        存在无条件赋值或任一完整决策的两侧子树均覆盖时返回 True。
    """

    # 空尾路径表示当前父分支内存在无条件赋值。
    if any(not list_path for list_path in list_paths):

        # 无条件赋值覆盖当前父分支的所有后续运行时选择。
        return True

    # 同层决策编号用于分别尝试可证明完整的控制树。
    set_ids = {  # 当前父分支下出现的决策编号
        str(list_path[0].get("id") or "")  # 每条路径的首个未消费决策
        for list_path in list_paths  # 遍历当前父分支的全部赋值路径
    }

    # 任一决策的两个分支都完整即可覆盖当前父分支。
    return any(
        decision_paths_cover_all(list_paths, str_id)  # 分别证明该决策两侧子树
        for str_id in set_ids  # 尝试当前层出现的全部决策编号
    )

# 单决策辅助函数隔离 then 与 else 路径，防止空尾跨分支误覆盖。
def decision_paths_cover_all(
    list_paths: list[list[dict[str, Any]]],
    str_id: str,
) -> bool:
    """证明一个决策编号的 then 与 else 子树分别完整。

    参数:
        list_paths: 当前父分支下各赋值尚未消费的决策路径。
        str_id: 当前需要证明的 formatter 决策编号。

    返回:
        两个分支均存在且各自递归覆盖全部路径时返回 True。
    """

    # 两侧路径必须独立收集，禁止一侧空尾替另一侧证明完整。
    dict_branch_paths: dict[str, list[list[dict[str, Any]]]] = {  # then 与 else 的剩余子路径
        "then": [],  # 当前决策真分支的剩余路径
        "else": [],  # 当前决策假分支的剩余路径
    }

    # 只消费编号匹配且 formatter 明确含 alternate 的完整决策。
    for list_path in list_paths:

        # 首节点描述当前赋值在该层选择的决策与分支。
        dict_decision = list_path[0]  # 当前路径首个未消费决策

        # 其他编号或缺少 alternate 的决策不能证明当前控制树完整。
        if str(dict_decision.get("id") or "") != str_id or not bool(dict_decision.get("complete")):

            # 保留路径给其他决策编号尝试，不纳入当前分支证明。
            continue

        # formatter 只接受 then 与 else 两种运行时分支极性。
        str_branch = str(dict_decision.get("branch") or "")  # 当前路径选择的分支极性

        # 未知极性不能参与完整性证明。
        if str_branch in dict_branch_paths:

            # 消费当前决策后把剩余子路径归入对应分支。
            dict_branch_paths[str_branch].append(list_path[1:])

    # 两侧必须各自存在赋值路径，并分别递归证明完整。
    return all(
        list_branch_paths and paths_cover_all(list_branch_paths)  # 当前分支非空且完整
        for list_branch_paths in dict_branch_paths.values()  # then 与 else 分开验证
    )

# 选择器编号区分 case default、显式 case 项和普通 if 条件。
def selector_id(
    dict_control: dict[str, Any],
    str_target: str,
    int_index: int,
) -> str:
    """从条件根节点派生一次真实控制选择操作编号。

    参数:
        dict_control: formatter 输出的控制表达式节点。
        str_target: 当前控制条件约束的基础目标。
        int_index: 控制条件在当前事实控制栈中的序号。

    返回:
        稳定选择操作编号；case default 返回空字符串。
    """

    # case 项由 formatter 显式提供 selector_id，default 值为空。
    if "selector_id" in dict_control:

        # 保留空值语义，防止 default 分支虚增选择操作。
        return str(dict_control.get("selector_id") or "")

    # 普通控制条件优先派生自真实根操作节点编号。
    str_root_id = str(dict_control.get("occurrence_id") or "")  # 控制根节点编号

    # 有根操作编号时生成与语法位置稳定关联的选择编号。
    if str_root_id:

        # 后缀区分条件表达式自身操作与分支选择操作。
        return f"{str_root_id}:selector"

    # 纯标识符条件没有操作编号，使用目标和控制序号生成稳定编号。
    return f"{str_target}:control{int_index}:selector"

# 静态端点规范化保留位选和切片，只删除无语义空白。
def static_target(str_target: str) -> str:
    """返回保留常量选择器的规范静态目标。

    参数:
        str_target: formatter 赋值事实中的原始目标文本。

    返回:
        删除空白但保留位选或切片的静态端点名称。
    """

    # 空白不参与端点身份，选择器文本则必须完整保留。
    return "".join(str_target.split())

# 扩展属性在 JSON 边界可能是字典或键值对序列。
def _attributes(value: object) -> dict[str, object]:
    """恢复表达式节点的扩展属性映射。

    参数:
        value: formatter 输出的 attributes 字典或键值序列。

    返回:
        与输入容器断开引用的普通属性字典。
    """

    # 普通字典只复制顶层，当前流程不会修改嵌套值。
    if isinstance(value, dict):

        # 返回副本避免选择分析污染 formatter 报告。
        return dict(value)

    # dataclass 冻结序列在 JSON 或 thaw 后保持键值对形状。
    if isinstance(value, (list, tuple)):

        # 非法键值对由 dict 转换异常回落为空属性。
        try:

            # 字符串键统一兼容 JSON 和 FrozenFact 表示。
            return {str(obj_key): obj_item for obj_key, obj_item in value}

        # 结构不完整只使当前节点失去扩展属性。
        except (TypeError, ValueError):

            # 空属性让调用方采用保守映射原因。
            return {}

    # 其他兼容值不携带可解释扩展属性。
    return {}

# 表达式子节点兼容 formatter 的 children 与旧 operands 字段。
def _children(expression: dict[str, Any]) -> list[dict[str, Any]]:
    """读取一个类型化表达式节点的有序子节点。

    参数:
        expression: formatter typed expression 节点字典。

    返回:
        仅包含结构化子节点的独立列表。
    """

    # 新 dataclass 使用 children，旧组合事实使用 operands。
    obj_children = expression.get("children", expression.get("operands", []))  # 当前节点原始子项集合

    # 非序列兼容值不能形成稳定的操作数顺序。
    if not isinstance(obj_children, (list, tuple)):

        # 空列表表示当前节点没有可解释子表达式。
        return []

    # 只保留能继续读取 node_kind、text 和 operator 的字典节点。
    return [dict_item for dict_item in obj_children if isinstance(dict_item, dict)]

# 节点类别字段兼容 dataclass JSON 与旧 formatter typed tree。
def _node_kind(expression: dict[str, Any]) -> str:
    """返回类型化表达式节点的规范类别。

    参数:
        expression: formatter typed expression 节点字典。

    返回:
        小写 node_kind 或 kind 文本。
    """

    # 两代字段统一后供静态 lvalue 分派使用。
    return str(expression.get("node_kind") or expression.get("kind") or "").lower()

# 常量节点只接受不含未知位的十进制索引文本。
def _constant_index(expression: dict[str, Any]) -> int | None:
    """读取 select 子节点中的静态整数索引。

    参数:
        expression: kind 为 constant 的 formatter 子节点。

    返回:
        可确定十进制整数；其他字面量返回 None。
    """

    # 非常量节点不得被当作静态位选边界。
    if _node_kind(expression) != "constant":

        # None 阻止运行期索引进入逐位映射。
        return None

    # 当前 formatter fixture 为 select 边界保存普通十进制文本。
    str_text = str(expression.get("text") or expression.get("value") or "").strip()  # 待解释索引文本

    # 只放行可无歧义转换的有符号十进制整数。
    if not re.fullmatch(r"[+-]?\d+", str_text):

        # 参数化、未知位或定宽字面量由上层局部化为未知映射。
        return None

    # Python 整数转换保持负索引文本的数值身份。
    return int(str_text, 10)

# select 节点展开为按源码高位到低位排列的 parent 端点。
def _select_endpoints(expression: dict[str, Any]) -> tuple[tuple[str, ...], str]:
    """展开一个常量 bit 或 slice 选择节点。

    参数:
        expression: kind 为 select 的 formatter typed tree。

    返回:
        parent 端点元组和局部未知原因。
    """

    # dynamic 属性是禁止静态映射的权威标记。
    dict_attributes = _attributes(expression.get("attributes"))  # 当前 select 扩展属性

    # 运行期索引无法绑定唯一 parent endpoint。
    if bool(dict_attributes.get("dynamic") or expression.get("dynamic")):

        # 原因与 formatter actual 合同保持一致。
        return (), "dynamic selection is not a static instance endpoint"

    # 第一个 child 是基础标识符，其余 child 是 bit 或 slice 边界。
    list_children = _children(expression)  # 当前 select 基础值和边界节点

    # 缺少基础值或边界时无法恢复完整静态端点。
    if len(list_children) < 2:

        # 固定原因只污染当前 output association。
        return (), "output selection is incomplete"

    # 当前合同只允许 identifier 作为 output lvalue 的基础端点。
    dict_base = list_children[0]  # 被选择的 parent 基础信号节点

    # 其他基础表达式不能伪造成可写静态端点。
    if _node_kind(dict_base) != "identifier":

        # 复杂基础值保持当前输出局部未知。
        return (), "output selection base is not static"

    # identifier text 是最终 parent 端点的基础名称。
    str_base = str(dict_base.get("text") or dict_base.get("value") or "").strip()  # parent 基础信号名称

    # bit select 只需要一个确定索引。
    if str(expression.get("operator") or "bit") == "bit":

        # 第二个 child 提供唯一 bit 索引。
        int_index = _constant_index(list_children[1])  # 当前 bit select 常量索引

        # 未知索引禁止输出虚假位对。
        if int_index is None:

            # 原因延续 dynamic endpoint 的 fail-closed 边界。
            return (), "dynamic selection is not a static instance endpoint"

        # 单比特端点保持 Verilog 方括号表示。
        return (f"{str_base}[{int_index}]",), ""

    # slice 需要左右两个常量边界并保留声明方向。
    if str(expression.get("operator") or "") == ":" and len(list_children) >= 3:

        # 左边界对应拼接中的高位侧首元素。
        int_left = _constant_index(list_children[1])  # 当前 slice 左边界

        # 右边界决定包含端点和迭代方向。
        int_right = _constant_index(list_children[2])  # 当前 slice 右边界

        # 任一边界未知都不能确定 parent 端点数量。
        if int_left is None or int_right is None:

            # 参数化 slice 在当前阶段保持局部未知。
            return (), "output selection is not statically bounded"

        # 步长保持源码从左边界到右边界的位顺序。
        int_step = -1 if int_left >= int_right else 1  # 当前 slice 索引方向

        # range 终点偏移一步以包含 Verilog 右边界。
        tuple_endpoints = tuple(  # slice 声明方向对应的 parent 位端点序列
            f"{str_base}[{int_index}]"  # 当前 slice 静态 parent 位端点
            for int_index in range(int_left, int_right + int_step, int_step)  # 按声明方向包含两端
        )  # 当前 slice 从左到右的端点序列

        # 完整边界产生无 unknown reason 的静态映射片段。
        return tuple_endpoints, ""

    # 其他 select operator 不属于批准的 bit/slice 子集。
    return (), "unsupported output selection operator"

# 递归 lvalue 展开只解释 identifier、select 和普通 concat。
def _lvalue_endpoints(
    expression: dict[str, Any],
    expected_width: int | None,
) -> tuple[tuple[str, ...], str]:
    """展开类型化 output actual 的有序 parent 端点。

    参数:
        expression: formatter actual 的 typed expression 根。
        expected_width: 上层已知的 child output 位宽提示。

    返回:
        从高位到低位排列的 parent 端点和局部未知原因。
    """

    # 节点类别决定当前 lvalue 片段的静态解释方法。
    str_kind = _node_kind(expression)  # 当前 lvalue typed node 类别

    # whole identifier 的宽度只能由 child formal 提供。
    if str_kind == "identifier":

        # 缺少宽度提示时 identifier 在 concat 中无法确定占用位数。
        if expected_width is None:

            # 精确原因区分未知 concat 宽度与普通映射失败。
            return (), "output concat width is unknown"

        # identifier text 是 parent whole-net 端点名称。
        str_name = str(expression.get("text") or expression.get("value") or "").strip()  # 待逐位展开的 parent whole-net 名称

        # 单比特 whole connection 保留裸名称，避免伪造 [0] 选择器。
        if expected_width == 1:

            # 一个端点直接承载 child 唯一输出位。
            return (str_name,), ""

        # 多位 whole connection 展开成高位到低位的静态 bit 端点。
        return tuple(
            f"{str_name}[{int_index}]"  # 当前 parent 总线静态位端点
            for int_index in range(expected_width - 1, -1, -1)  # child MSB 到 LSB 顺序
        ), ""

    # bit 和 slice 选择节点由专用边界解释器展开。
    if str_kind == "select":

        # select 自带静态宽度，不依赖上层 whole-net 提示。
        return _select_endpoints(expression)

    # concat 子项按源码顺序组成从高位到低位的 parent 端点。
    if str_kind == "concat":

        # concat 先记录每个片段，允许唯一 whole identifier 使用剩余宽度。
        list_segments: list[tuple[str, ...] | None] = []  # 当前 concat 片段端点或待定宽度标记

        # select 和嵌套 concat 可自行定宽，identifier 需要剩余宽度推导。
        for dict_child in _children(expression):

            # bare identifier 暂存为未知宽度片段。
            if _node_kind(dict_child) == "identifier":

                # None 保留该片段在源码中的相对位置。
                list_segments.append(None)

                # 继续解释后续静态片段。
                continue

            # 非 identifier 子节点必须从 typed tree 自行确定宽度。
            tuple_child = _lvalue_endpoints(dict_child, None)  # 当前 concat 静态子片段端点和原因

            # 任一片段未知都会使完整逐位配对不安全。
            if tuple_child[1]:

                # concat 统一返回批准的未知宽度原因。
                return (), "output concat width is unknown"

            # 已知片段按源码位置保存待最终扁平化。
            list_segments.append(tuple_child[0])

        # 多个 whole identifier 无法唯一分配 child 剩余位宽。
        int_unknown_count = sum(obj_segment is None for obj_segment in list_segments)  # 待定宽度片段数量

        # 没有 child 总宽度或存在多个待定片段时保持局部未知。
        if int_unknown_count and (expected_width is None or int_unknown_count != 1):

            # 不猜测多个总线片段各自占用位数。
            return (), "output concat width is unknown"

        # 已知片段位数从静态端点元组直接累计。
        int_known_width = sum(len(obj_segment) for obj_segment in list_segments if obj_segment is not None)  # concat 已知位数

        # 唯一待定 identifier 使用 child width 减去已知片段后的余量。
        int_remaining_width = (expected_width or int_known_width) - int_known_width  # 待定片段可占用位数

        # 非正余量表示 concat 与 child formal 宽度不一致。
        if int_unknown_count and int_remaining_width <= 0:

            # 返回零长度 parent 端点供上层宽度诊断。
            return (), "output concat width is unknown"

        # 重新遍历 children 以恢复唯一 identifier 的名称和源码位置。
        list_endpoints: list[str] = []  # 当前 concat 最终 parent 位端点

        # segment 与 child 一一对应并保持源码顺序。
        for dict_child, obj_segment in zip(_children(expression), list_segments):

            # 已知 select 或嵌套 concat 直接追加。
            if obj_segment is not None:

                # 已确定端点保持片段内部高位到低位次序。
                list_endpoints.extend(obj_segment)

                # 当前静态片段处理完成。
                continue

            # 唯一 whole identifier 用剩余位宽展开。
            tuple_identifier = _lvalue_endpoints(dict_child, int_remaining_width)  # 待定 identifier 推导端点

            # 正位宽提示保证 identifier 能完整展开。
            list_endpoints.extend(tuple_identifier[0])

        # 完整 concat 端点序列直接进入宽度一致性检查。
        return tuple(list_endpoints), ""

    # replication 与 streaming 应由 actual unsupported_reason 提前截断。
    return (), "unsupported output lvalue expression"

# 公开输出映射入口把 child 位序与 parent 静态端点逐一配对。
def map_static_output_actual(actual: dict[str, Any], child_width: int) -> StaticOutputMap:
    """建立 child output MSB 到 parent actual 的静态逐位映射。

    参数:
        actual: formatter InstanceActualFact 的 JSON 兼容字典。
        child_width: 被连接 child output formal 的确定正位宽。

    返回:
        完整 bit_pairs；失败时返回空映射和精确局部原因。
    """

    # formatter 已给出的 actual 原因优先于下游二次分类。
    str_reason = str(actual.get("unsupported_reason") or "")  # 当前 actual 权威不支持原因

    # dynamic、replication 和 streaming 原因必须原样透传。
    if str_reason:

        # 空位对阻止不支持 actual 形成伪 hierarchy binding。
        return StaticOutputMap((), str_reason)

    # 输出 formal 位宽必须是可展开的正整数。
    if not isinstance(child_width, int) or isinstance(child_width, bool) or child_width <= 0:

        # 非法宽度只污染当前 output association。
        return StaticOutputMap((), "child output width is unknown")

    # typed tree 是 output lvalue 结构和顺序的唯一解释来源。
    obj_expression = actual.get("expression")  # 当前 output actual 类型化表达式根

    # 缺少表达式树时不能从原始 text 重新解析连接语义。
    if not isinstance(obj_expression, dict):

        # 明确停止线防止 text 正则成为第二套 parser。
        return StaticOutputMap((), "output actual has no typed expression")

    # whole identifier 与顶层 concat 都可使用 child_width 消解唯一待定片段。
    int_width_hint = child_width if _node_kind(obj_expression) in {"identifier", "concat"} else None  # 输出根宽度提示

    # 递归展开只消费 formatter typed tree。
    tuple_endpoints = _lvalue_endpoints(obj_expression, int_width_hint)  # parent 端点序列和原因

    # 局部结构原因直接返回，不生成部分位对。
    if tuple_endpoints[1]:

        # 原因只归属当前 output actual。
        return StaticOutputMap((), tuple_endpoints[1])

    # child 与 parent 位数必须完全相等才能按位置绑定。
    if len(tuple_endpoints[0]) != child_width:

        # 精确文本同时报告两侧宽度，便于定位 concat 或 slice 错误。
        return StaticOutputMap(
            (),
            f"output width mismatch: child={child_width} parent={len(tuple_endpoints[0])}",
        )

    # child 位标识从 MSB 递减并与 parent 源码端点顺序配对。
    tuple_pairs = tuple(  # 保持 child MSB 至 LSB 与 parent 拼接端点顺序一一对应
        (str(int_child_bit), str_parent_endpoint)  # 保存当前 child 输出位号及其唯一 parent 静态端点
        for int_child_bit, str_parent_endpoint in zip(  # 同步遍历 child 位号和 parent lvalue 端点
            range(child_width - 1, -1, -1),  # 生成 child 输出从最高位递减到最低位的位号
            tuple_endpoints[0],  # 读取 parent lvalue 从左到右的静态端点顺序
        )
    )  # 冻结宽度已校验且两侧顺序完全对应的层级位连接证据

    # 成功映射不携带 unknown_reason。
    return StaticOutputMap(tuple_pairs)
