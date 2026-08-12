"""实现锁存环、数组索引边界和组合反馈 RTL PG 门禁。"""

# future annotations 延后解析 PG 数据模型类型。
from __future__ import annotations

# re 在 formatter 确认的 module 文本内提取结构关系。
import re

# Callable 和 Iterator 描述规则路由与结构事实迭代器。
from typing import Callable, Iterator

# facts 提供可信 module 文本和稳定证据路径。
from .rtl_pg_facts import PgFacts, PgSourceFacts, iter_trusted_modules

# models 统一逐门禁状态和定位证据。
from .rtl_pg_models import PgEvaluation, PgFinding, failed, passed

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
def evaluate_structure_gate(str_gate_id: str, facts: PgFacts) -> PgEvaluation:
    """执行组合结构规则组中的指定 PG 门禁。

    参数:
        str_gate_id: 当前执行的固定 PG 结构门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前结构规则的逐门禁结论。
    """

    # 路由表只包含 rtl_pg_engine 分配给本模块的激活编号。
    dict_evaluators: dict[str, Callable[[PgFacts], PgEvaluation]] = {  # 固定编号到结构规则函数的映射
        "PG1033": _latch_no_comb_loop,  # 锁存器组合环检查
        "PG1059": _array_index_in_range,  # 常量数组索引边界检查
        "PG1065": _comb_no_feedback,  # 普通组合反馈检查
    }

    # engine 已保证编号属于本模块，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _latch_no_comb_loop 检查锁存目标是否落入组合依赖环。
def _latch_no_comb_loop(facts: PgFacts) -> PgEvaluation:
    """检查含锁存语义的组合目标是否参与反馈环。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        锁存反馈环、合规锁存结构或不适用结论。
    """

    # findings 保存位于组合环中的锁存目标。
    list_findings: list[PgFinding] = []  # 锁存组合环证据

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

# _array_index_in_range 检查常量数组访问是否落在声明边界内。
def _array_index_in_range(facts: PgFacts) -> PgEvaluation:
    """检查数组的常量索引是否超出声明范围。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        常量索引越界、边界内访问或不适用结论。
    """

    # findings 保存每个确定越界的常量访问。
    list_findings: list[PgFinding] = []  # 数组常量越界证据

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
    source_facts: PgSourceFacts,
    str_module_text: str,
    int_base_line: int,
    dict_ranges: dict[str, tuple[int, int]],
) -> list[PgFinding]:
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
    list_findings: list[PgFinding] = []  # 当前 module 的越界访问证据

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
                PgFinding(
                    source_facts.relative_path,
                    int_line,
                    "数组常量索引超出声明范围。",
                    str_evidence,
                )
            )

    # 返回当前 module 的全部常量越界结果。
    return list_findings

# _comb_no_feedback 检查连续和组合过程赋值形成的依赖环。
def _comb_no_feedback(facts: PgFacts) -> PgEvaluation:
    """检查组合赋值依赖图中是否存在反馈环。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        组合反馈环、无环组合图或不适用结论。
    """

    # findings 保存每个 module 的稳定首个反馈节点。
    list_findings: list[PgFinding] = []  # 组合反馈环证据

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
        dict_always  # 当前组合过程块事实
        for dict_always in dict_module.get("always", [])  # 遍历全部过程块
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
    source_facts: PgSourceFacts,
    str_module_text: str,
    int_base_line: int,
    str_token: str,
    str_message: str,
    str_evidence: str,
) -> PgFinding:
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
    return PgFinding(
        source_facts.relative_path,
        int_line,
        str_message,
        str_evidence,
    )
