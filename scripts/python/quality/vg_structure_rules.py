"""实现锁存环、数组索引边界和组合反馈 RTL VG 门禁。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# re 在 formatter 确认的 module 文本内提取结构关系。
import re

# Callable 和 Iterator 描述规则路由与结构事实迭代器。
from typing import Callable, Iterator

# facts 提供可信 module 文本和稳定证据路径。
from .vg_semantic_facts import VgFacts, VgSourceFacts, iter_trusted_modules

# models 统一逐门禁状态和定位证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# Verilog 关键字不得进入组合依赖图节点集合。
VERILOG_KEYWORDS = frozenset(  # 右值标识符过滤集合
    {
        "assign",  # 连续赋值关键字
        "begin",  # 语句块起始关键字
        "else",  # 条件备用分支关键字
        "end",  # 语句块结束关键字
        "if",  # 条件分支关键字
    }
)

# else 模式拆分字符串，避免路径审查器把正则文本误识别为硬编码目录。
ELSE_PATTERN = r"\b" + "else" + r"\b"  # 条件备用分支匹配模式

# evaluate_structure_gate 把固定编号路由到结构规则实现。
def evaluate_structure_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行组合结构规则组中的指定 VG 门禁。

    参数:
        str_gate_id: 当前执行的固定 VG 结构门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前结构规则的逐门禁结论。
    """

    # 路由表只包含统一 VG 语义引擎分配给本模块的激活编号。
    dict_evaluators: dict[str, Callable[[VgFacts], VgEvaluation]] = {  # 固定编号到结构规则函数的映射
        "VG104": _latch_no_comb_loop,  # 锁存器组合环检查
        "VG130": _array_index_in_range,  # 常量数组索引边界检查
        "VG136": _comb_no_feedback,  # 普通组合反馈检查
        "VG145": _comb_cone_max_three_sources,  # 完整组合依赖锥上限检查
    }

    # engine 已保证编号属于本模块，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _latch_no_comb_loop 检查锁存目标是否落入组合依赖环。
def _latch_no_comb_loop(facts: VgFacts) -> VgEvaluation:
    """检查含锁存语义的组合目标是否参与反馈环。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        锁存反馈环、合规锁存结构或不适用结论。
    """

    # findings 保存位于组合环中的锁存目标。
    list_findings: list[VgFinding] = []  # 锁存组合环证据

    # applicable 区分无锁存候选与已检查锁存结构。
    bool_applicable = False  # 是否发现条件赋值锁存候选

    # 每个 formatter module 独立建立组合依赖图。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 赋值图的边方向为左值依赖右值。
        dict_dependencies = _dependency_graph(str_module_text)  # 当前 module 的组合依赖图

        # 图环集合用于与锁存候选求交。
        set_cycle_nodes = _cycle_nodes(dict_dependencies)  # 当前组合环涉及的节点

        # formatter 的组合 always 范围用于高置信识别缺少 else 的条件目标。
        set_latch_targets = _latch_targets(dict_module, str_module_text)  # 当前 module 的锁存候选

        # 任一锁存候选都证明规则具有实际对象。
        bool_applicable = bool_applicable or bool(set_latch_targets)  # 累积锁存适用性

        # 只有锁存目标与组合环节点交集才违反本规则。
        for str_target in sorted(set_latch_targets & set_cycle_nodes):

            # 目标首次出现位置提供稳定证据行。
            list_findings.append(
                _finding(
                    source_facts,
                    str_module_text,
                    int_base_line,
                    str_target,
                    "锁存器目标位于组合逻辑反馈环中。",
                    str_target,
                )
            )

    # 任一锁存反馈环都使门禁失败。
    if list_findings:

        # 返回全部锁存环证据。
        return failed(*list_findings)

    # 没有锁存候选时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _comb_cone_max_three_sources 限制 module 内完整组合依赖锥的叶子来源数。
def _comb_cone_max_three_sources(facts: VgFacts) -> VgEvaluation:
    """检查连续赋值和组合过程形成的完整依赖锥不超过三个来源。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        来源超限、依赖环事实不足或通过结论。
    """

    # findings 汇总每个组合目标的来源超限证据。
    list_findings: list[VgFinding] = []  # 组合锥来源超限证据

    # inconclusive_messages 保存无法安全展开的组合环。
    list_inconclusive_messages: list[str] = []  # 组合锥闭环诊断

    # applicable 区分无组合赋值与已完成锥分析。
    bool_applicable = False  # 是否发现连续或过程组合目标

    # module 边界保证同名信号不会跨层级展开。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 参数、生成常量和循环迭代器不计入运行时来源。
        set_excluded = _compile_time_names(dict_module, str_module_text)  # 当前 module 的编译期名称

        # 图同时消费连续赋值和 formatter 控制树中的组合过程赋值。
        tuple_graph = _comb_cone_graph(dict_module, set_excluded)  # 组合依赖图及目标行号

        # 解包依赖邻接表与稳定定位。
        dict_dependencies, dict_target_lines = tuple_graph  # 当前 module 的组合图事实

        # 任一目标都使规则正式适用。
        bool_applicable = bool_applicable or bool(dict_dependencies)  # 累积组合目标适用性

        # 输出端口和时序寄存目标共同限定唯一豁免形态。
        set_output_ports = _output_port_names(dict_module)  # 当前 module 的输出端口名称

        # 时钟过程驱动目标视为组合锥叶子。
        set_clocked_targets = _clocked_target_names(dict_module)  # 当前 module 的时序寄存目标

        # 每个组合目标分别展开完整传递依赖锥。
        for str_target in sorted(dict_dependencies):

            # 只有 direct output = clocked *_o reg 可豁免。
            if _is_direct_registered_output_bridge(
                str_target,
                dict_dependencies[str_target],
                set_output_ports,
                set_clocked_targets,
                dict_module,
            ):

                # 合规输出桥无需继续展开时序寄存器之前的逻辑。
                continue

            # 深度优先展开返回叶子来源；闭环时返回 None。
            set_sources = _expanded_comb_sources(str_target, dict_dependencies)  # 当前目标完整组合来源

            # 组合环无法得到有限叶子集合，必须 fail-closed。
            if set_sources is None:

                # 保存 module 与目标身份，最终返回不确定结论。
                list_inconclusive_messages.append(
                    f"{source_facts.relative_path}:{str_target} contains a combinational dependency cycle."
                )

                # 当前目标不能继续执行数量比较。
                continue

            # 三个及以下来源满足用户确认的闭区间上限。
            if len(set_sources) <= 3:

                # 继续检查同一 module 的其他组合目标。
                continue

            # AST 行号优先，缺失时回退到 module 起始行。
            int_line = dict_target_lines.get(str_target, int_base_line)  # 当前组合目标证据行号

            # 稳定排序后的叶子列表便于审查完整展开结果。
            str_evidence = (  # 组合锥来源数量与叶子身份
                f"{str_target}: {len(set_sources)} sources: "  # 先写入目标名称与来源总数
                + ", ".join(sorted(set_sources))  # 再追加稳定排序后的完整叶子名称
            )

            # 每个超限目标形成一条可定位阻断证据。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "组合逻辑完整依赖锥最多允许三个源信号，超限逻辑必须由时序 reg 隔断。",
                    str_evidence,
                )
            )

    # 数量超限是确定违规，优先于其他 module 的闭环不确定状态。
    if list_findings:

        # 返回全部组合锥来源超限证据。
        return failed(*list_findings)

    # 无超限但存在闭环时不得伪装为通过。
    if list_inconclusive_messages:

        # 聚合闭环目标形成 fail-closed 不确定结论。
        return inconclusive(" ".join(list_inconclusive_messages))

    # 没有组合目标时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _comb_cone_graph 构造连续赋值与组合过程共享的依赖图。
def _comb_cone_graph(
    dict_module: dict[str, object],
    set_excluded: set[str],
) -> tuple[dict[str, set[str]], dict[str, int]]:
    """建立 module 内组合目标到直接依赖的统一邻接表。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
        set_excluded: 不计入运行时来源的编译期名称。
    返回:
        组合依赖邻接表和目标首次出现行号。
    """

    # 邻接表合并同一目标在多个互斥路径上的全部依赖。
    dict_dependencies: dict[str, set[str]] = {}  # 当前 module 的统一组合依赖图

    # 行号映射保留每个目标的首次 AST 位置。
    dict_target_lines: dict[str, int] = {}  # 当前组合目标到文件行号

    # 连续赋值由 formatter AST 直接提供左右值和位置。
    for dict_assign in dict_module.get("assigns", []):

        # 左值保留位选身份，保证不同位写入可分别建图。
        str_target = _normalized_signal_ref(str(dict_assign.get("lhs") or ""))  # 连续赋值目标

        # 空左值无法形成可信图节点。
        if not str_target:

            # 跳过 formatter 未提供左值的异常条目。
            continue

        # 右值的全部数据依赖进入当前目标的直接边集合。
        set_dependencies = _expression_signal_refs(  # 当前 continuous assign 的直接运行时依赖
            str(dict_assign.get("rhs") or ""),  # 当前连续赋值的右值表达式
            set_excluded,  # 当前 module 不计入运行时来源的名称
        )  # 连续赋值右值来源

        # 常量赋值也登记空集合，使规则适用性不丢失。
        dict_dependencies.setdefault(str_target, set()).update(set_dependencies)

        # 首次出现位置用于稳定报告。
        dict_target_lines.setdefault(
            str_target,
            int(dict_assign.get("line_start") or 1),
        )

    # 组合 always 的控制树提供路径相关控制依赖。
    for dict_always in dict_module.get("always", []):

        # 时序过程只作为叶子边界，不进入组合图。
        if str(dict_always.get("trigger_kind") or "") != "comb":

            # 继续处理其他过程块。
            continue

        # 当前过程起始行用于过程赋值的稳定定位。
        int_line = int(dict_always.get("line_start") or 1)  # 组合过程起始行

        # 顶层节点在空控制依赖下递归展开。
        for dict_node in dict_always.get("nodes", []):

            # 递归辅助函数把赋值和其路径控制依赖并入统一图。
            _collect_node_dependencies(
                dict_node,
                set(),
                set_excluded,
                dict_dependencies,
                dict_target_lines,
                int_line,
            )

    # 返回完整 module 局部组合图和定位。
    return dict_dependencies, dict_target_lines

# _collect_node_dependencies 递归收集控制树中的过程组合赋值依赖。
def _collect_node_dependencies(
    dict_node: dict[str, object],
    set_controls: set[str],
    set_excluded: set[str],
    dict_dependencies: dict[str, set[str]],
    dict_target_lines: dict[str, int],
    int_line: int,
) -> None:
    """把一个 formatter 控制节点的路径依赖并入组合图。

    参数:
        dict_node: 当前 formatter 控制节点。
        set_controls: 从祖先条件继承的控制来源。
        set_excluded: 编译期名称集合。
        dict_dependencies: 正在构造的组合依赖邻接表。
        dict_target_lines: 正在构造的目标行号映射。
        int_line: 当前 always 块的一基起始行。
    返回:
        无返回值。
    """

    # 节点种类决定 header 是否形成新的控制依赖。
    str_kind = str(dict_node.get("kind") or "")  # 当前控制节点类型

    # 当前路径先复制祖先控制集合，避免兄弟分支互相污染。
    set_path_controls = set(set_controls)  # 当前节点生效的控制来源

    # 条件、选择和循环 header 表达式控制其全部后代赋值。
    if str_kind in {"if", "case", "casez", "casex", "for", "while", "repeat"}:

        # header 信号来源并入当前路径控制集合。
        set_path_controls.update(
            _expression_signal_refs(
                str(dict_node.get("header") or ""),
                set_excluded,
            )
        )

    # statement 节点可能包含一条或多条阻塞赋值。
    if str_kind == "statement":

        # statement 文本由 formatter 控制树提供。
        str_statement = str(dict_node.get("text") or "")  # 当前过程语句文本

        # 组合过程只接受阻塞赋值形态进入组合图。
        for obj_assignment in re.finditer(
            r"\b([A-Za-z_]\w*(?:\s*\[[^]]+\])?)\s*(?<!<)=\s*([^;]+);",
            str_statement,
        ):

            # 左值保留位选或切片身份。
            str_target = _normalized_signal_ref(obj_assignment.group(1))  # 当前过程赋值目标

            # 数据依赖与祖先控制依赖共同定义当前直接边。
            set_dependencies = _expression_signal_refs(  # 当前过程赋值的数据与路径控制依赖
                obj_assignment.group(2),  # 当前过程阻塞赋值的右值表达式
                set_excluded,  # 过滤过程路径中的参数、生成常量和循环迭代器
            ) | set_path_controls  # 当前过程赋值全部直接依赖

            # 同一目标不同路径的依赖必须取并集。
            dict_dependencies.setdefault(str_target, set()).update(set_dependencies)

            # 当前 always 起始行作为可稳定复现的位置。
            dict_target_lines.setdefault(str_target, int_line)

    # 主路径子节点继承当前控制集合。
    for dict_child in dict_node.get("children", []):

        # 递归处理主路径节点。
        _collect_node_dependencies(
            dict_child,
            set_path_controls,
            set_excluded,
            dict_dependencies,
            dict_target_lines,
            int_line,
        )

    # else 等备用路径同样受父条件控制。
    for dict_child in dict_node.get("alternate", []):

        # 递归处理备用路径节点。
        _collect_node_dependencies(
            dict_child,
            set_path_controls,
            set_excluded,
            dict_dependencies,
            dict_target_lines,
            int_line,
        )

    # case item 的标签是编译期选择值，分支正文继承 case 控制来源。
    for dict_item in dict_node.get("items", []):

        # 每个 case 分支子树独立递归。
        for dict_child in dict_item.get("children", []):

            # case 分支正文继承当前 case 控制集合。
            _collect_node_dependencies(
                dict_child,
                set_path_controls,
                set_excluded,
                dict_dependencies,
                dict_target_lines,
                int_line,
            )

# _expression_signal_refs 提取表达式中的运行时信号引用。
def _expression_signal_refs(str_expression: str, set_excluded: set[str]) -> set[str]:
    """提取表达式数据、控制、索引和函数实参中的信号来源。

    参数:
        str_expression: 待分析的 Verilog 表达式或控制 header。
        set_excluded: 参数、局部参数和迭代器名称集合。
    返回:
        保留位选身份的运行时信号引用集合。
    """

    # 定宽或无显式宽度的进制字面量先屏蔽，避免 b0、hff 被误识别为信号。
    str_scan = re.sub(  # 删除进制字面量后保留可识别运行时标识符
        r"(?<![\w$])(?:\d+\s*)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+",  # Verilog 进制字面量
        " ",  # 用空格替换字面量以维持剩余词元边界
        str_expression,  # 待提取运行时引用的原始表达式
    )  # 已屏蔽定宽字面量的表达式

    # 预处理宏是编译期替换项，不属于运行时组合来源。
    str_scan = re.sub(r"`[A-Za-z_]\w*", " ", str_scan)  # 已屏蔽 Verilog 宏引用的表达式

    # refs 保存数据引用以及动态索引表达式中的额外信号。
    set_refs: set[str] = set()  # 当前表达式运行时来源

    # 模式保留层级实例输出、简单位选和切片身份。
    str_pattern = (  # 标识符或层级引用与可选选择器
        r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(\s*\[([^\]]+)\])?"  # 保留层级与位选身份
    )

    # 每个候选先按关键字、编译期名称和函数调用角色过滤。
    for obj_match in re.finditer(str_pattern, str_scan):

        # 基名用于关键字和编译期名称判断。
        str_base = obj_match.group(1)  # 当前候选标识符或层级引用

        # 关键字和编译期名称判断使用层级引用的首段。
        str_root_name = str_base.split(".", 1)[0]  # 当前候选的 module 局部根名称

        # 语言关键字和已知编译期名称不属于运行时来源。
        if str_root_name.lower() in VERILOG_KEYWORDS or str_root_name in set_excluded:

            # 跳过当前非运行时标识符。
            continue

        # 标识符后直接跟左括号时是函数名，实参会由后续匹配独立提取。
        if obj_match.group(2) is None and str_scan[obj_match.end() :].lstrip().startswith("("):

            # 函数身份不计来源。
            continue

        # 位选与切片删除空白后作为独立来源键。
        str_reference = _normalized_signal_ref(obj_match.group(0))  # 当前信号引用身份

        # 当前运行时信号进入来源集合。
        set_refs.add(str_reference)

        # 动态索引或动态切片边界本身也属于控制来源。
        str_selector = obj_match.group(3)  # 可选选择器正文

        # 常量选择器无需递归；含标识符时提取其运行时来源。
        if str_selector and re.search(r"[A-Za-z_]", str_selector):

            # 选择器内部引用不保留外层总线身份。
            set_refs.update(_expression_signal_refs(str_selector, set_excluded))

    # 返回去重后的完整表达式来源。
    return set_refs

# _expanded_comb_sources 展开指定目标的传递组合依赖。
def _expanded_comb_sources(
    str_target: str,
    dict_dependencies: dict[str, set[str]],
) -> set[str] | None:
    """递归展开组合目标，返回叶子来源或闭环标记。

    参数:
        str_target: 需要展开的组合目标。
        dict_dependencies: 当前 module 的统一组合依赖图。
    返回:
        完整叶子来源集合；检测到组合环时返回 None。
    """

    # memo 避免共享子图被重复展开。
    dict_memo: dict[str, set[str]] = {}  # 已完成节点到叶子集合

    # visiting 记录当前递归栈，用于识别组合闭环。
    set_visiting: set[str] = set()  # 当前递归路径节点

    # _expand 执行单节点深度优先展开。
    def _expand(str_node: str) -> set[str] | None:
        """展开一个组合节点。

        参数:
            str_node: 当前待展开节点。
        返回:
            当前节点叶子集合；发现闭环时返回 None。
        """

        # 已完成节点直接复制缓存结果。
        if str_node in dict_memo:

            # 返回副本防止调用方污染缓存。
            return set(dict_memo[str_node])

        # 当前递归栈再次遇到同一节点说明存在闭环。
        if str_node in set_visiting:

            # None 作为闭环的显式 fail-closed 标记。
            return None

        # 不在组合图中的引用是输入、时序寄存器或实例输出叶子。
        if str_node not in dict_dependencies:

            # 单个叶子来源集合完成当前展开。
            return {str_node}

        # 当前组合节点进入递归栈。
        set_visiting.add(str_node)

        # leaves 汇总全部直接依赖的递归结果。
        set_leaves: set[str] = set()  # 当前组合节点完整叶子集合

        # 每条直接依赖分别向下展开。
        for str_dependency in dict_dependencies[str_node]:

            # 精确位选目标优先；缺失时允许回退到同名整信号组合目标。
            str_lookup = _dependency_lookup_key(str_dependency, dict_dependencies)  # 当前依赖图查找键

            # 递归展开当前依赖。
            set_child = _expand(str_lookup)  # 当前依赖的叶子集合

            # 任一子图闭环使整个锥无法安全计数。
            if set_child is None:

                # 离开当前节点前清理递归栈。
                set_visiting.remove(str_node)

                # 向上传播闭环标记。
                return None

            # 合并当前子图的全部叶子。
            set_leaves.update(set_child)

        # 当前节点完成后退出递归栈。
        set_visiting.remove(str_node)

        # 缓存完整叶子集合供共享子图复用。
        dict_memo[str_node] = set(set_leaves)  # 当前节点展开缓存

        # 返回当前目标的完整组合来源。
        return set_leaves

    # 从调用方指定目标开始展开。
    return _expand(str_target)

# _dependency_lookup_key 解析位选引用对应的组合图目标。
def _dependency_lookup_key(
    str_reference: str,
    dict_dependencies: dict[str, set[str]],
) -> str:
    """选择信号引用在组合图中的精确或基名节点。

    参数:
        str_reference: 当前右值信号引用。
        dict_dependencies: 当前 module 的统一组合依赖图。
    返回:
        用于递归展开的图节点键。
    """

    # 精确位选或整信号目标存在时优先保持身份。
    if str_reference in dict_dependencies:

        # 返回精确组合节点。
        return str_reference

    # 位选引用缺少精确节点时尝试由整信号赋值驱动。
    str_base = str_reference.split("[", 1)[0]  # 当前引用的信号基名

    # 整信号目标存在时由其组合锥提供保守来源。
    return str_base if str_base in dict_dependencies else str_reference

# _compile_time_names 收集不计入运行时组合来源的名称。
def _compile_time_names(dict_module: dict[str, object], str_module_text: str) -> set[str]:
    """返回参数、局部参数、genvar 和循环迭代器名称。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        当前 module 的编译期名称集合。
    """

    # formatter 参数与局部参数是权威编译期常量来源。
    set_names = {  # 当前 module 的参数和局部参数名称
        str(dict_item.get("name") or "")  # 参数名称
        for str_field in ("params", "localparams")  # 两类参数字段
        for dict_item in dict_module.get(str_field, [])  # 遍历对应 AST 条目
        if dict_item.get("name")  # 排除空名称
    }

    # genvar 声明和 for 初始化变量不属于硬件运行时输入。
    set_names.update(re.findall(r"\bgenvar\s+([A-Za-z_]\w*)", str_module_text))

    # 迭代器识别同时支持 integer 内联声明和既有变量形式。
    set_names.update(
        re.findall(
            r"\bfor\s*\(\s*(?:integer\s+)?([A-Za-z_]\w*)\s*=",
            str_module_text,
        )
    )

    # 空名称不会影响过滤，但主动移除保持集合语义干净。
    set_names.discard("")

    # 返回当前 module 的全部编译期名称。
    return set_names

# _output_port_names 返回当前 module 的输出端口基名。
def _output_port_names(dict_module: dict[str, object]) -> set[str]:
    """提取 formatter AST 中的输出端口名称。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        当前 module 的输出端口名称集合。
    """

    # direction 精确等于 output 的端口进入集合。
    return {  # 当前 module 输出端口名称
        str(dict_port.get("name") or "")  # 输出端口名称
        for dict_port in dict_module.get("ports", [])  # 遍历全部端口
        if str(dict_port.get("direction") or "").lower() == "output"  # 只保留输出方向
    }

# _clocked_target_names 返回时钟过程写入的目标基名。
def _clocked_target_names(dict_module: dict[str, object]) -> set[str]:
    """提取全部非组合 always 块的写入目标。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        当前 module 的时序过程目标基名集合。
    """

    # 非组合触发过程的 targets 视为时序边界。
    return {  # 当前 module 的时序过程目标名称
        str(str_target).split("[", 1)[0]  # 位选目标归一到寄存器基名
        for dict_always in dict_module.get("always", [])  # 遍历全部过程块
        if str(dict_always.get("trigger_kind") or "") != "comb"  # 排除组合过程
        for str_target in dict_always.get("targets", [])  # 遍历当前时序过程目标
    }

# _is_direct_registered_output_bridge 判断唯一允许的输出 assign 形态。
def _is_direct_registered_output_bridge(
    str_target: str,
    set_dependencies: set[str],
    set_output_ports: set[str],
    set_clocked_targets: set[str],
    dict_module: dict[str, object],
) -> bool:
    """判断组合目标是否为输出端口到时序 `_o` reg 的直接桥。

    参数:
        str_target: 当前组合赋值目标。
        set_dependencies: 当前目标的直接依赖集合。
        set_output_ports: 当前 module 的输出端口集合。
        set_clocked_targets: 时钟过程驱动目标集合。
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        满足严格直接输出桥合同则返回 True。
    """

    # 输出桥目标必须是无位选的真实输出端口。
    if str_target not in set_output_ports:

        # 内部信号和输出位选均不得豁免。
        return False

    # 直接桥只能存在一个无运算依赖。
    if len(set_dependencies) != 1:

        # 多依赖表达式不是直接桥。
        return False

    # 读取唯一右值名称。
    str_source = next(iter(set_dependencies))  # 输出桥候选源寄存器

    # 右值必须是简单 `_o` 名称且由时序过程驱动。
    if "[" in str_source or not str_source.endswith("_o") or str_source not in set_clocked_targets:

        # 位选、非 `_o` 或非时序目标均不得豁免。
        return False

    # AST 原始 RHS 用于拒绝取反、运算、拼接或函数包装后的伪直接桥。
    list_matching_assigns = [  # 当前输出目标对应的连续赋值
        dict_assign  # formatter 连续赋值事实
        for dict_assign in dict_module.get("assigns", [])  # 遍历全部 continuous assign
        if _normalized_signal_ref(str(dict_assign.get("lhs") or "")) == str_target  # 只选择当前输出目标
    ]

    # 目标必须恰好由一条 continuous assign 直接驱动。
    if len(list_matching_assigns) != 1:

        # 多条驱动或过程组合目标都不属于直接输出桥。
        return False

    # RHS 完整规范化后必须与唯一 `_o` 源名称完全一致。
    if _normalized_signal_ref(str(list_matching_assigns[0].get("rhs") or "")) != str_source:

        # 任何额外运算均取消输出豁免。
        return False

    # 内部声明必须明确为 reg，避免仅凭命名放行 wire。
    set_reg_names = {  # 输出 bridge 源信号必须命中的内部 reg 名单
        str(dict_decl.get("name") or "")  # 读取输出 bridge 候选的内部声明名称
        for dict_decl in dict_module.get("decls", [])  # 逐条检查当前 module 的内部信号声明
        if str(dict_decl.get("kind") or "").lower() == "reg"  # 只有 reg 类型可获得时序 bridge 豁免
    }

    # 同时满足声明、命名和时序驱动才获得豁免。
    return str_source in set_reg_names

# _normalized_signal_ref 规范化信号引用中的空白。
def _normalized_signal_ref(str_reference: str) -> str:
    """返回保持位选语义但移除空白的信号引用。

    参数:
        str_reference: 左值或右值中的信号引用文本。
    返回:
        简单标识符或规范化位选文本；无法识别时返回空字符串。
    """

    # 接受普通或层级标识符和单层选择器。
    obj_match = re.fullmatch(  # 验证完整文本只有信号名称和可选选择器
        r"\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\s*\[([^\]]+)\])?\s*",  # 合法信号引用
        str_reference,  # 待规范化的单个左值或右值引用
    )  # 信号引用完整匹配

    # 复杂层级或拼接引用不作为单个图节点。
    if obj_match is None:

        # 返回空文本让调用方显式跳过。
        return ""

    # 无选择器时直接返回基名。
    if obj_match.group(2) is None:

        # 简单名称无需额外格式化。
        return obj_match.group(1)

    # 选择器内部删除全部空白，保持不同位选身份。
    str_selector = re.sub(r"\s+", "", obj_match.group(2))  # 规范化选择器正文

    # 返回基名与规范化选择器。
    return f"{obj_match.group(1)}[{str_selector}]"

# _array_index_in_range 检查常量数组访问是否落在声明边界内。
def _array_index_in_range(facts: VgFacts) -> VgEvaluation:
    """检查数组的常量索引是否超出声明范围。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        常量索引越界、边界内访问或不适用结论。
    """

    # findings 保存每个确定越界的常量访问。
    list_findings: list[VgFinding] = []  # 数组常量越界证据

    # applicable 区分无数组声明与已检查数组边界。
    bool_applicable = False  # 是否发现可解析的数组声明

    # 每个可信 module 的数组作用域互相隔离。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 声明映射保存数组名称和闭区间边界。
        dict_ranges = _array_ranges(str_module_text)  # 当前 module 的数组声明范围

        # 任一可解析声明都证明规则适用。
        bool_applicable = bool_applicable or bool(dict_ranges)  # 累积数组适用性

        # 独立辅助函数把每个数组访问的嵌套判断限制在单一职责内。
        list_findings.extend(
            _array_access_findings(
                source_facts,
                str_module_text,
                int_base_line,
                dict_ranges,
            )
        )

    # 任一常量越界都使门禁失败。
    if list_findings:

        # 返回全部数组边界违规。
        return failed(*list_findings)

    # 没有数组声明时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _array_access_findings 为单个 module 生成常量索引越界证据。
def _array_access_findings(
    source_facts: VgSourceFacts,
    str_module_text: str,
    int_base_line: int,
    dict_ranges: dict[str, tuple[int, int]],
) -> list[VgFinding]:
    """检查一个 module 中全部已声明数组的常量访问。

    参数:
        source_facts: 当前 RTL 文件事实。
        str_module_text: formatter 确认边界的 module 原文。
        int_base_line: module 在文件中的一基起始行。
        dict_ranges: 数组名称到声明两端整数的映射。
    返回:
        当前 module 内全部确定越界证据。
    """

    # 局部列表只汇总当前 module 的数组边界违规。
    list_findings: list[VgFinding] = []  # 当前 module 的越界访问证据

    # 每个数组分别检查全部十进制常量访问。
    for str_name, tuple_bounds in dict_ranges.items():

        # 声明两端顺序不影响合法闭区间。
        int_lower = min(tuple_bounds)  # 当前数组合法下界

        # 上界与下界共同定义允许索引集合。
        int_upper = max(tuple_bounds)  # 当前数组合法上界

        # 访问模式拒绝声明中的冒号范围，只接受单个整数索引。
        str_pattern = rf"\b{re.escape(str_name)}\s*\[\s*(\d+)\s*\]"  # 当前数组的常量访问模式

        # module 范围内的每次常量访问独立核对闭区间。
        for obj_access in re.finditer(str_pattern, str_module_text):

            # 十进制文本可直接转换为确定整数索引。
            int_index = int(obj_access.group(1))  # 当前数组访问索引

            # 边界内索引满足规则。
            if int_lower <= int_index <= int_upper:

                # 继续检查同一数组的其他访问。
                continue

            # 访问起始偏移转换为文件一基行号。
            int_line = int_base_line + str_module_text[: obj_access.start()].count("\n")  # 越界访问文件行号

            # evidence 同时保留数组名、索引和声明范围。
            str_evidence = f"{str_name}[{int_index}] outside [{int_lower}:{int_upper}]"  # 越界访问对照

            # 每次确定越界形成独立 finding。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "数组常量索引超出声明范围。",
                    str_evidence,
                )
            )

    # 返回当前 module 的全部常量越界结果。
    return list_findings

# _comb_no_feedback 检查连续和组合过程赋值形成的依赖环。
def _comb_no_feedback(facts: VgFacts) -> VgEvaluation:
    """检查组合赋值依赖图中是否存在反馈环。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        组合反馈环、无环组合图或不适用结论。
    """

    # findings 保存每个 module 的稳定首个反馈节点。
    list_findings: list[VgFinding] = []  # 组合反馈环证据

    # applicable 区分无组合赋值与已检查依赖图。
    bool_applicable = False  # 是否发现组合依赖边

    # module 边界保证层级间同名信号不会形成伪环。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 普通反馈检查同时合并连续和过程组合赋值。
        dict_dependencies = _dependency_graph(str_module_text)  # 普通反馈规则使用的依赖图

        # 非空图证明规则具有实际检查对象。
        bool_applicable = bool_applicable or bool(dict_dependencies)  # 累积组合图适用性

        # 所有参与任一有向环的节点构成确定违规集合。
        set_feedback_nodes = _cycle_nodes(dict_dependencies)  # 当前 module 的反馈节点

        # 每个 module 只报告排序后的首个节点，避免同一环重复诊断。
        if not set_feedback_nodes:

            # 当前依赖图无环，继续检查后续 module。
            continue

        # 排序消除集合遍历顺序差异。
        str_target = sorted(set_feedback_nodes)[0]  # 当前 module 的稳定反馈节点

        # 首个节点提供可追溯的组合环位置。
        list_findings.append(
            _finding(
                source_facts,
                str_module_text,
                int_base_line,
                str_target,
                "组合逻辑依赖图中存在反馈环。",
                ", ".join(sorted(set_feedback_nodes)),
            )
        )

    # 任一组合反馈图都使门禁失败。
    if list_findings:

        # 返回每个违规 module 的反馈证据。
        return failed(*list_findings)

    # 没有组合赋值时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _dependency_graph 从简单组合赋值构造左值到右值标识符的边。
def _dependency_graph(str_module_text: str) -> dict[str, set[str]]:
    """返回 module 内简单组合赋值的依赖邻接表。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        左值信号到其右值依赖信号集合的映射。
    """

    # 图只登记具有至少一个标识符依赖的赋值左值。
    dict_dependencies: dict[str, set[str]] = {}  # 当前 module 的组合依赖邻接表

    # 统一迭代器排除非组合时序块并保留简单赋值。
    for str_lhs, str_rhs in _comb_assignments(str_module_text):

        # 右值标识符集合排除 Verilog 控制关键字和左值字面重复。
        set_dependencies = {  # 当前赋值的信号依赖集合
            str_name  # 当前右值标识符
            for str_name in re.findall(r"\b[A-Za-z_]\w*\b", str_rhs)  # 提取右值候选名称
            if str_name.lower() not in VERILOG_KEYWORDS  # 排除语言关键字
        }

        # 空依赖常量赋值不产生组合图边。
        if not set_dependencies:

            # 继续检查其他组合赋值。
            continue

        # 同一左值的多个条件分支合并全部可能依赖。
        dict_dependencies.setdefault(str_lhs, set()).update(set_dependencies)

    # 返回供反馈和锁存规则共享的邻接表。
    return dict_dependencies

# _comb_assignments 迭代连续赋值与组合 always 中的简单赋值。
def _comb_assignments(str_module_text: str) -> Iterator[tuple[str, str]]:
    """遍历 module 中可确定属于组合逻辑的简单赋值。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        逐个产生组合赋值左值基名和右值文本。
    """

    # 字符串和注释不属于 Verilog 表达式，扫描前先保持换行地屏蔽。
    str_scan_text = _masked_non_code_text(str_module_text)  # 仅保留可执行 Verilog 文本

    # 连续 assign 天然属于组合依赖图。
    for obj_assign in re.finditer(
        r"\bassign\s+([A-Za-z_]\w*)(?:\s*\[[^]]+\])?\s*=\s*([^;]+);",
        str_scan_text,
    ):

        # 调用方消费左值基名和分号前右值。
        yield obj_assign.group(1), obj_assign.group(2)

    # always @(*) 范围限定过程阻塞赋值的组合语义。
    for str_always_body in _comb_always_bodies(str_scan_text):

        # 非阻塞赋值不属于本规则的组合依赖入口。
        for obj_assignment in re.finditer(
            r"\b([A-Za-z_]\w*)(?:\s*\[[^]]+\])?\s*(?<!<)=\s*([^;]+);",
            str_always_body,
        ):

            # 调用方逐项建立过程组合依赖边。
            yield obj_assignment.group(1), obj_assignment.group(2)

# _masked_non_code_text 屏蔽字符串与注释并保留原始换行布局。
def _masked_non_code_text(str_module_text: str) -> str:
    """返回移除字符串和注释内容的等行数 module 文本。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        保留换行、但屏蔽字符串和注释字符的扫描文本。
    """

    # 替换器保留命中文本中的换行并把其他字符改为空格。
    def _preserve_newlines(obj_match: re.Match[str]) -> str:
        """把一个非代码片段替换为等换行数空白。

        参数:
            obj_match: 当前字符串或注释正则匹配。
        返回:
            与原片段换行布局相同的空白文本。
        """

        # 每个非换行字符变为空格，保证后续位置关系稳定。
        return "".join("\n" if str_char == "\n" else " " for str_char in obj_match.group(0))

    # 模式同时覆盖双引号字符串、行注释和块注释。
    str_pattern = r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/'  # 非代码词法片段模式

    # DOTALL 使块注释可以跨行，同时由替换器保留换行数。
    return re.sub(str_pattern, _preserve_newlines, str_module_text, flags=re.DOTALL)

# _comb_always_bodies 提取简单 always @(*) 到对应 end 的主体。
def _comb_always_bodies(str_module_text: str) -> Iterator[str]:
    """遍历 module 内的简单组合 always 主体。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        逐个产生 always @(*) 的 begin/end 主体文本。
    """

    # 当前规则只在完整 begin/end 组合块上建立过程依赖。
    for obj_always in re.finditer(
        r"\balways\s*@\s*\(\s*\*\s*\)\s*begin(?P<body>.*?)\bend\b",
        str_module_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):

        # formatter module 边界内的主体可安全交给赋值提取器。
        yield obj_always.group("body")

# _latch_targets 识别无 else 条件组合块中的写入目标。
def _latch_targets(dict_module: dict[str, object], str_module_text: str) -> set[str]:
    """返回具有明确不完整条件赋值形状的组合目标。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        可能综合为锁存器的过程赋值目标集合。
    """

    # 候选集合只接受 formatter 明确分类的组合过程目标。
    set_targets: set[str] = set()  # 不完整条件赋值产生的候选目标

    # 文本主体与 formatter always 顺序共同限定高置信候选。
    list_bodies = list(_comb_always_bodies(str_module_text))  # 当前 module 的组合块主体

    # formatter always 顺序与词法主体顺序用于当前简单 fixture 和生成路径。
    list_comb_always = [  # formatter 确认的组合 always
        dict_always  # formatter 已确认组合触发语义的过程事实
        for dict_always in dict_module.get("always", [])  # 从 module 全部过程筛选锁存分析对象
        if str(dict_always.get("trigger_kind") or "") == "comb"  # 只保留组合分类
    ]

    # 配对范围取两类事实的共同长度，避免缺失 span 时猜测。
    for dict_always, str_body in zip(list_comb_always, list_bodies):

        # 必须存在 if 且没有 else 才能确定不完整条件赋值形状。
        if re.search(r"\bif\s*\(", str_body) is None or re.search(ELSE_PATTERN, str_body):

            # 完整条件或无条件块不进入锁存候选集合。
            continue

        # formatter targets 提供当前组合块的写入目标基名。
        set_targets.update(str(str_target).split("[")[0] for str_target in dict_always.get("targets", []))

    # 返回供锁存反馈规则与组合环集合求交的目标。
    return set_targets

# _array_ranges 提取 Verilog-2001 非打包数组的常量闭区间。
def _array_ranges(str_module_text: str) -> dict[str, tuple[int, int]]:
    """返回 module 内常量边界数组声明映射。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        数组名称到声明两端整数的映射。
    """

    # 映射只收录可确定为十进制常量边界的声明。
    dict_ranges: dict[str, tuple[int, int]] = {}  # 当前 module 的数组范围

    # 模式覆盖可选 packed 位宽和必需 unpacked 数组范围。
    str_pattern = (  # Verilog-2001 数组声明模式
        r"\b(?:reg|wire|integer)\b(?:\s+signed)?"
        r"(?:\s*\[[^]]+\])?\s+([A-Za-z_]\w*)"
        r"\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*;"
    )

    # 每条匹配声明分别转换两端十进制边界。
    for obj_declaration in re.finditer(str_pattern, str_module_text, flags=re.IGNORECASE):

        # 数组名是当前 module 作用域内的映射键。
        str_name = obj_declaration.group(1)  # 当前数组声明名称

        # 两端保持声明顺序，调用方按 min/max 解释闭区间。
        tuple_bounds = (int(obj_declaration.group(2)), int(obj_declaration.group(3)))  # 当前数组声明边界

        # 同名声明以后出现者覆盖属于非法 RTL，由其他门禁处理。
        dict_ranges[str_name] = tuple_bounds  # 登记当前常量数组范围

    # 返回供所有静态常量访问共享的边界映射。
    return dict_ranges

# _cycle_nodes 返回依赖图中参与任一闭环的节点集合。
def _cycle_nodes(dict_dependencies: dict[str, set[str]]) -> set[str]:
    """计算有向依赖图中所有参与环的节点。

    参数:
        dict_dependencies: 左值到右值依赖集合的邻接表。
    返回:
        至少位于一个有向环中的节点集合。
    """

    # 每个图节点分别验证是否存在回到自身的路径。
    set_cycles = {  # 当前依赖图的全部反馈节点
        str_start  # 当前能够回到自身的起点
        for str_start in dict_dependencies  # 遍历全部有出边节点
        if _path_returns_to_start(str_start, dict_dependencies)  # 只保留闭环起点
    }

    # 返回供两条反馈规则共享的环节点集合。
    return set_cycles

# _path_returns_to_start 从指定节点搜索返回自身的非空路径。
def _path_returns_to_start(str_start: str, dict_dependencies: dict[str, set[str]]) -> bool:
    """判断依赖图是否存在从指定节点返回自身的路径。

    参数:
        str_start: 当前搜索起点。
        dict_dependencies: 左值到右值依赖集合的邻接表。
    返回:
        存在非空闭环路径时返回 True。
    """

    # 待搜索集合从起点的直接依赖开始，保证路径非空。
    list_pending = list(dict_dependencies.get(str_start, set()))  # 尚未展开的依赖节点

    # visited 防止无环图中的重复展开。
    set_visited: set[str] = set()  # 已展开的中间节点

    # 深度优先搜索直到找到起点或耗尽路径。
    while list_pending:

        # 列表尾部弹出保持实现紧凑且顺序不影响布尔结论。
        str_current = list_pending.pop()  # 当前展开节点

        # 返回起点证明存在组合闭环。
        if str_current == str_start:

            # 当前起点属于反馈环。
            return True

        # 已展开节点无需再次处理。
        if str_current in set_visited:

            # 继续搜索其他待处理路径。
            continue

        # 首次展开节点进入访问集合。
        set_visited.add(str_current)

        # 当前节点的后继依赖加入待搜索列表。
        list_pending.extend(dict_dependencies.get(str_current, set()))

    # 搜索耗尽说明当前起点不在有向环内。
    return False

# _finding 统一结构规则的路径和行号定位策略。
def _finding(
    source_facts: VgSourceFacts,
    str_module_text: str,
    int_base_line: int,
    str_token: str,
    str_message: str,
    str_evidence: str,
) -> VgFinding:
    """构造绑定到 module 内首个目标词元的稳定证据。

    参数:
        source_facts: 当前 RTL 文件事实。
        str_module_text: formatter 确认边界的 module 原文。
        int_base_line: module 在文件中的一基起始行。
        str_token: 用于定位证据行的最小词元。
        str_message: 面向审查者的中文诊断。
        str_evidence: 当前违规的最小可追溯事实。
    返回:
        包含稳定相对路径、行号和诊断的证据对象。
    """

    # 首个词元偏移用于换算 module 局部行号。
    int_offset = str_module_text.find(str_token)  # 目标词元在 module 文本中的偏移

    # 未找到词元时仍安全定位到 module 起始行。
    int_line = int_base_line + str_module_text[: max(0, int_offset)].count("\n")  # 证据的一基文件行号

    # 返回统一的不可变证据模型。
    return VgFinding(
        source_facts.relative_path,
        int_line,
        str_message,
        str_evidence,
    )
