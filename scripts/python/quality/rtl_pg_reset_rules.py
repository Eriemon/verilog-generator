"""实现复位结构、一致性与触发极性相关 RTL PG 门禁。"""

# future annotations 延后解析 PG 数据模型类型。
from __future__ import annotations

# re 只在 formatter AST 已确认的头部和控制节点中提取标识符。
import re

# Callable 描述固定编号到复位规则函数的路由表。
from typing import Any, Callable, Iterator

# facts 提供可信 module 与结构化 always 事实。
from .rtl_pg_facts import PgFacts, iter_trusted_modules

# models 统一逐门禁状态和定位证据。
from .rtl_pg_models import PgEvaluation, PgFinding, failed, passed

# 复位、清零和置位名称只用于识别控制信号角色，不推断普通数据信号。
RESET_NAME_PATTERN = re.compile(  # 常见复位类控制信号名称
    r"(?:^|_)(?:rst|reset|clear|clr|preset|set)(?:_|$)|^(?:rst|reset|clear|clr|preset|set)",  # 受控名称边界
    flags=re.IGNORECASE,  # Verilog 标识符角色匹配不依赖大小写风格
)

# set/preset 与 reset/clear 分开识别，用于禁止同块双向异步控制。
SET_NAME_PATTERN = re.compile(r"(?:^|_)(?:set|preset)(?:_|$)|^(?:set|preset)", flags=re.IGNORECASE)  # 置位类信号名称

# reset-only 模式排除置位名称，防止角色集合重叠。
RESET_ONLY_NAME_PATTERN = re.compile(  # 复位或清零类信号名称
    r"(?:^|_)(?:rst|reset|clear|clr)(?:_|$)|^(?:rst|reset|clear|clr)",  # 排除 set/preset 名称
    flags=re.IGNORECASE,  # 支持常见大写信号风格
)

# evaluate_reset_gate 把固定编号路由到复位规则实现。
def evaluate_reset_gate(str_gate_id: str, facts: PgFacts) -> PgEvaluation:
    """执行复位结构规则组中的指定 PG 门禁。

    参数:
        str_gate_id: 当前执行的固定 PG 复位门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前复位规则的逐门禁结论。
    """

    # 路由表只包含 rtl_pg_engine 分配给本模块的激活编号。
    dict_evaluators: dict[str, Callable[[PgFacts], PgEvaluation]] = {  # 固定编号到复位规则函数的映射
        "PG1004": _no_sync_async_mix,  # 同一复位线不得混用同步和异步风格
        "PG1006": _one_reset_per_always,  # 单个 always 只允许一个复位控制
        "PG1012": _loop_no_reset_logic_mix,  # 循环体不得同时承载复位与普通逻辑
        "PG1022": _no_async_reset_as_data,  # 异步复位不得进入普通数据赋值
        "PG1029": _no_internal_async_source,  # 异步复位必须来自输入端口
        "PG1031": _no_set_reset_pair,  # 单个 always 不得并用异步 set 和 reset
        "PG1039": _ff_no_mixed_reset_style,  # 全部触发器必须在复位分支初始化
        "PG1042": _ff_no_mixed_reset_style,  # 同一时序块目标必须统一复位覆盖
        "PG1045": _ff_reset_condition_match,  # 异步触发极性必须匹配复位条件
        "PG1047": _no_logic_in_async_path,  # 异步复位网络不得插入组合运算
    }

    # engine 已保证编号属于本模块，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _no_async_reset_as_data 禁止异步复位控制进入普通赋值右值。
def _no_async_reset_as_data(facts: PgFacts) -> PgEvaluation:
    """检查异步复位信号是否被当作普通数据信号使用。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        复位信号进入赋值右值时失败，否则按异步复位事实通过。
    """

    # findings 保存异步复位信号进入数据赋值的位置。
    list_findings: list[PgFinding] = []  # 异步复位数据用途证据

    # applicable 区分没有异步复位与检查后合规。
    bool_applicable = False  # 是否发现异步复位控制信号

    # 每个 module 独立提取异步控制和赋值语句。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 所有时序块的非时钟复位边沿合并为当前控制集合。
        set_async_resets = {  # 当前 module 的异步复位信号
            str_signal  # 当前非时钟边沿信号
            for dict_always in _sequential_always(dict_module)  # 遍历时序过程块
            for _, str_signal in _edge_events(dict_always)  # 遍历非时钟边沿事件
            if RESET_NAME_PATTERN.search(str_signal)  # 只接受复位类角色
        }

        # 非空集合证明规则具有实际检查对象。
        bool_applicable = bool_applicable or bool(set_async_resets)  # 累积异步复位适用性

        # 单一模式一次定位任一复位信号，避免逐信号嵌套扫描右值。
        str_reset_pattern = "|".join(  # 当前 module 的异步复位标识符模式
            rf"\b{re.escape(str_signal)}\b"  # 当前复位信号的完整词边界
            for str_signal in sorted(set_async_resets)  # 稳定合并全部异步复位名称
        )

        # 逐条普通赋值核对右值是否引用异步复位。
        for str_lhs, str_rhs, int_offset_line in _module_assignments(str_module_text):

            # 空模式或未命中右值时没有复位数据用途。
            obj_reset_use = re.search(str_reset_pattern, str_rhs) if str_reset_pattern else None  # 当前右值复位引用

            # 未引用异步复位时继续检查其他赋值。
            if obj_reset_use is None:

                # 当前右值保持普通数据语义。
                continue

            # 同名自赋值不构成复位进入其他数据管脚。
            if str_lhs == obj_reset_use.group(0):

                # 当前赋值只维持复位网络自身。
                continue

            # 赋值所在行直接定位复位数据用途。
            list_findings.append(
                PgFinding(
                    source_facts.relative_path,
                    int_base_line + int_offset_line,
                    "异步复位信号被连接到普通数据赋值右值。",
                    f"{str_lhs} = {str_rhs}",
                )
            )

    # 任一数据用途都违反异步复位角色边界。
    if list_findings:

        # 返回全部可定位的复位数据用途。
        return failed(*list_findings)

    # 没有异步复位时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _no_internal_async_source 要求异步复位直接来自 module 输入。
def _no_internal_async_source(facts: PgFacts) -> PgEvaluation:
    """检查寄存器异步复位是否使用内部生成信号。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        内部异步复位来源、合规输入来源或不适用结论。
    """

    # findings 保存不属于输入端口的异步复位信号。
    list_findings: list[PgFinding] = []  # 内部异步复位来源证据

    # applicable 区分没有异步复位与输入来源合规。
    bool_applicable = False  # 是否发现异步复位边沿

    # module 端口作用域用于判断异步控制是否来自外部。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 输入端口集合是允许的直接异步复位来源。
        set_inputs = {  # 当前 module 的输入端口名称
            str(dict_port.get("name") or "")  # 当前端口规范名称
            for dict_port in dict_module.get("ports", [])  # 遍历 formatter 端口事实
            if str(dict_port.get("direction") or "").lower() == "input"  # 只保留输入方向
        }

        # 映射直接绑定每个复位边沿信号和对应 always 行号。
        dict_async_lines = {  # 当前 module 的异步复位使用位置
            str_signal: int(dict_always.get("line_start") or 1)  # 复位信号到过程块起始行
            for dict_always in _sequential_always(dict_module)  # 扫描内部来源检查对象
            for _, str_signal in _edge_events(dict_always)  # 提取其异步控制名称
            if RESET_NAME_PATTERN.search(str_signal)  # 限定为复位语义信号
        }

        # 非空映射证明规则具有实际对象。
        bool_applicable = bool_applicable or bool(dict_async_lines)  # 累积异步来源适用性

        # 每条异步复位线分别核对输入端口来源。
        for str_signal, int_line in dict_async_lines.items():

            # 输入端口直接驱动满足来源要求。
            if str_signal in set_inputs:

                # 继续检查其他异步复位信号。
                continue

            # 内部来源形成确定警告证据。
            list_findings.append(
                PgFinding(
                    source_facts.relative_path,
                    int_line,
                    "内部生成信号被用作寄存器异步复位。",
                    str_signal,
                )
            )

    # 任一内部复位来源都使门禁失败。
    if list_findings:

        # 返回全部内部异步来源证据。
        return failed(*list_findings)

    # 全部异步控制均来自输入端口时按实际发现范围通过。
    return passed(applicable=bool_applicable)

# _no_logic_in_async_path 禁止组合赋值生成异步复位网络。
def _no_logic_in_async_path(facts: PgFacts) -> PgEvaluation:
    """检查异步复位线路中是否插入组合逻辑运算。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        组合生成异步复位、直接输入复位或不适用结论。
    """

    # findings 保存由组合表达式生成的异步复位信号。
    list_findings: list[PgFinding] = []  # 异步复位组合路径证据

    # 组合路径适用性只由实际消费的异步复位网络决定。
    bool_applicable = False  # 是否发现待追踪的复位网络

    # 每个 module 独立匹配异步控制与连续赋值来源。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 非时钟复位边沿定义本轮需要追踪的网络名称。
        set_async_resets = {  # 当前 module 的异步复位网络
            str_signal  # 当前网络追踪起点
            for dict_always in _sequential_always(dict_module)  # 扫描组合路径检查对象
            for _, str_signal in _edge_events(dict_always)  # 提取被时序块消费的控制网
            if RESET_NAME_PATTERN.search(str_signal)  # 限定可追踪的复位网络
        }

        # 任一异步复位网络都证明规则适用。
        bool_applicable = bool_applicable or bool(set_async_resets)  # 累积复位网络适用性

        # 普通赋值列表同时提供网络来源和稳定相对行号。
        for str_lhs, str_rhs, int_offset_line in _module_assignments(str_module_text):

            # 只追踪被异步边沿实际消费的左值。
            if str_lhs not in set_async_resets:

                # 当前赋值不是异步复位网络来源。
                continue

            # 单一标识符或常量直连不属于插入逻辑运算。
            if re.fullmatch(r"\s*[~!]?[A-Za-z_]\w*\s*", str_rhs):

                # 简单直连或反相保持当前规则允许边界。
                continue

            # 组合表达式直接形成异步复位路径违规。
            list_findings.append(
                PgFinding(
                    source_facts.relative_path,
                    int_base_line + int_offset_line,
                    "异步复位线路由组合逻辑表达式生成。",
                    f"{str_lhs} = {str_rhs}",
                )
            )

    # 任一组合复位网络都使门禁失败。
    if list_findings:

        # 返回全部组合异步复位来源。
        return failed(*list_findings)

    # 所有复位网络均为直接来源时按实际追踪范围通过。
    return passed(applicable=bool_applicable)

# _no_sync_async_mix 禁止同一复位信号跨时序块混用同步与异步模式。
def _no_sync_async_mix(facts: PgFacts) -> PgEvaluation:
    """检查同一复位线路是否同时承担同步和异步复位。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        复位模式混用时失败，否则按实际复位结构通过。
    """

    # usages 保存每条复位线出现过的模式与首个定位事实。
    dict_usages: dict[str, list[tuple[str, str, int]]] = {}  # 信号到模式、路径和行号列表

    # 每个时序过程块的首层复位条件决定同步或异步使用方式。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 组合过程块不属于触发器复位模式检查范围。
        for dict_always in _sequential_always(dict_module):

            # 首个 if 条件提供当前块实际消费的复位信号。
            dict_first_if = _first_if_node(dict_always)  # 当前时序块的首层条件节点

            # 控制信号仅从 formatter 首层条件中提取。
            str_signal = _condition_control_signal(dict_first_if)  # 首层复位类控制信号

            # 非复位条件块不参与本规则。
            if not str_signal:

                # 普通数据条件交给其他语义规则处理。
                continue

            # 敏感列表包含该信号边沿时属于异步复位，否则属于同步复位。
            set_async_signals = {str_name for _, str_name in _edge_events(dict_always)}  # 当前块异步边沿信号集合

            # 当前控制信号是否出现在边沿事件中决定复位风格。
            str_style = "async" if str_signal in set_async_signals else "sync"  # 当前复位线使用模式

            # always 起始行作为复位使用点的稳定定位。
            int_line = int(dict_always.get("line_start") or 1)  # 当前复位使用点的一基行号

            # 保留全部模式事实以便发现跨块混用。
            dict_usages.setdefault(str_signal, []).append((str_style, source_facts.relative_path, int_line))

    # findings 为每条发生模式混用的复位线生成一条确定证据。
    list_findings: list[PgFinding] = []  # 同步与异步混用证据

    # 信号名排序保证多文件报告顺序稳定。
    for str_signal in sorted(dict_usages):

        # 同时出现两个模式才违反规则。
        set_styles = {str_style for str_style, _, _ in dict_usages[str_signal]}  # 当前复位线的模式集合

        # 单一模式符合复位线路一致性要求。
        if set_styles != {"async", "sync"}:

            # 当前信号没有跨模式使用，无需生成 finding。
            continue

        # 第一处使用点足以定位需要统一的复位线路。
        _, str_path, int_line = dict_usages[str_signal][0]  # 当前混用复位线的首个证据位置

        # 每条混用复位线只生成一个稳定证据。
        list_findings.append(
            PgFinding(str_path, int_line, "同一复位线路同时用于同步和异步复位。", str_signal)
        )

    # 任一混用线路都使固定门禁失败。
    if list_findings:

        # 返回全部复位线路混用证据。
        return failed(*list_findings)

    # 至少发现一条复位使用事实时规则具有适用性。
    return passed(applicable=bool(dict_usages))

# _one_reset_per_always 限制单个时序块只使用一个复位类异步控制。
def _one_reset_per_always(facts: PgFacts) -> PgEvaluation:
    """检查每个时序 always 的复位类控制信号数量。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        单块使用多个复位类信号时失败，否则按实际结构通过。
    """

    # findings 保存含多个复位控制的过程块。
    list_findings: list[PgFinding] = []  # 多复位控制证据

    # applicable 区分无复位结构与复位结构合规。
    bool_applicable = False  # 是否发现复位类异步控制

    # 每个时序块独立统计异步复位类边沿信号。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # formatter 已分类的时序块逐个统计控制信号。
        for dict_always in _sequential_always(dict_module):

            # 时钟边沿已由 formatter 的 clock 字段排除。
            set_controls = {  # 当前块的异步复位类控制集合
                str_signal  # 当前边沿信号名称
                for _, str_signal in _edge_events(dict_always)  # 遍历敏感列表边沿事件
                if RESET_NAME_PATTERN.search(str_signal)  # 只保留复位、清零或置位角色
            }

            # 任一复位控制都证明规则适用。
            bool_applicable = bool_applicable or bool(set_controls)  # 累积复位控制适用性

            # 零个或一个复位控制均满足单信号约束。
            if len(set_controls) <= 1:

                # 当前 always 不产生多复位证据。
                continue

            # 排序后的信号集合形成确定性证据。
            int_line = int(dict_always.get("line_start") or 1)  # 多复位 always 的一基行号

            # 信号排序消除集合遍历顺序差异。
            str_evidence = ", ".join(sorted(set_controls))  # 当前块的全部复位类信号

            # 每个超限 always 形成独立 finding。
            list_findings.append(
                PgFinding(source_facts.relative_path, int_line, "同一 always 使用了多个复位类信号。", str_evidence)
            )

    # 任一过程块超出单复位约束即失败。
    if list_findings:

        # 返回全部多复位过程块证据。
        return failed(*list_findings)

    # 没有复位控制时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _loop_no_reset_logic_mix 禁止 for 循环内部切分复位与普通逻辑分支。
def _loop_no_reset_logic_mix(facts: PgFacts) -> PgEvaluation:
    """检查循环体是否同时包含复位分支和普通逻辑分支。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        循环内混合复位和普通逻辑时失败，否则按循环事实通过。
    """

    # findings 保存包含复位 if/else 的循环节点。
    list_findings: list[PgFinding] = []  # 循环复位混合证据

    # applicable 区分无相关循环与循环结构合规。
    bool_applicable = False  # 是否发现带复位的时序循环结构

    # formatter 控制树保留 loop 与嵌套 if/else 的从属关系。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 每个时序块独立检查循环层级。
        for dict_always in _sequential_always(dict_module):

            # 辅助函数独立分析当前过程块的循环节点。
            tuple_loop_result = _loop_reset_mix_findings(source_facts.relative_path, dict_always)  # 当前 always 的循环检查结果

            # 元组首项表示当前过程块是否出现复位循环。
            bool_current_applicable = tuple_loop_result[0]  # 当前过程块的循环适用性

            # 元组次项保留当前过程块全部混合分支证据。
            list_current_findings = tuple_loop_result[1]  # 当前过程块的循环违规列表

            # 任一过程块发现复位循环即累积适用性。
            bool_applicable = bool_applicable or bool_current_applicable  # 累积循环规则适用范围

            # 当前过程块的混合证据并入全局结果。
            list_findings.extend(list_current_findings)

    # 任一循环混合结构都使门禁失败。
    if list_findings:

        # 返回全部循环混合证据。
        return failed(*list_findings)

    # 合规正例的循环位于复位分支之外，仍需标记适用。
    if not bool_applicable:

        # 补充识别 reset 外层包围 loop 的合规结构。
        bool_applicable = _has_reset_and_loop(facts)  # 跨父子层级存在复位与循环的合规结构

    # 没有复位和循环组合时不适用。
    return passed(applicable=bool_applicable)

# _loop_reset_mix_findings 分析单个时序过程块的循环复位结构。
def _loop_reset_mix_findings(str_path: str, dict_always: dict[str, Any]) -> tuple[bool, list[PgFinding]]:
    """检查一个时序过程块中的循环复位分支。

    参数:
        str_path: 当前 RTL 文件的稳定相对路径。
        dict_always: formatter AST 中的单个时序 always。
    返回:
        是否发现复位循环以及全部混合分支证据。
    """

    # 局部列表只保存当前 always 的循环混合证据。
    list_findings: list[PgFinding] = []  # 当前过程块的循环违规

    # applicable 表示至少一个循环后代引用复位信号。
    bool_applicable = False  # 当前过程块是否包含复位循环

    # 深度遍历当前 always 的可信控制树。
    for dict_node in _walk_nodes(dict_always.get("nodes", [])):

        # 只检查循环节点的后代控制结构。
        if str(dict_node.get("kind") or "") != "loop":

            # 非循环节点不形成本规则对象。
            continue

        # 含复位类条件的 if 节点说明循环涉及复位语义。
        list_reset_ifs = [  # 当前循环内的复位条件节点
            dict_child  # 循环后代中的控制节点
            for dict_child in _walk_nodes(dict_node.get("children", []))  # 递归遍历循环体
            if str(dict_child.get("kind") or "") == "if"  # 只判断 if 条件
            and _condition_control_signal(dict_child)  # 条件引用复位类信号
        ]

        # 没有复位条件的普通循环无需检查分支混合。
        if not list_reset_ifs:

            # 继续查找后续循环节点。
            continue

        # 发现复位条件后标记当前过程块适用。
        bool_applicable = True  # 当前循环直接包含复位条件

        # 没有 alternate 时复位和普通逻辑尚未在循环内混合。
        if not any(dict_item.get("alternate") for dict_item in list_reset_ifs):

            # 继续检查其他循环节点。
            continue

        # formatter 目前只给 loop 所属 always 行号。
        int_line = int(dict_always.get("line_start") or 1)  # 混合循环所属过程块行号

        # 循环头部作为违规结构的可读证据。
        str_evidence = str(dict_node.get("header") or "for")  # 当前违规循环头部

        # 每个混合循环形成独立 finding。
        list_findings.append(
            PgFinding(
                str_path,
                int_line,
                "复位分支和普通逻辑出现在同一循环中。",
                str_evidence,
            )
        )

    # 返回当前 always 的适用性和全部证据。
    return bool_applicable, list_findings

# _no_set_reset_pair 禁止同一时序块并用异步置位和异步复位。
def _no_set_reset_pair(facts: PgFacts) -> PgEvaluation:
    """检查单个 always 是否同时包含异步 set 与 reset 控制。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        set/reset 并用时失败，否则按异步控制事实通过。
    """

    # findings 保存双向异步控制过程块。
    list_findings: list[PgFinding] = []  # set/reset 并用证据

    # applicable 区分没有异步控制与控制结构合规。
    bool_applicable = False  # 是否发现 set 或 reset 类异步控制

    # 逐过程块比较两类控制信号集合。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 每个时序块的控制集合互不混合。
        for dict_always in _sequential_always(dict_module):

            # 只消费敏感列表中的非时钟边沿事件。
            set_signals = {str_signal for _, str_signal in _edge_events(dict_always)}  # 当前块异步控制信号

            # set/preset 名称形成置位角色集合。
            set_set_controls = {str_signal for str_signal in set_signals if SET_NAME_PATTERN.search(str_signal)}  # 置位类控制集合

            # rst/reset/clear 名称形成复位角色集合。
            set_reset_controls = {  # 复位类控制集合
                str_signal  # 当前复位类边沿信号
                for str_signal in set_signals  # 遍历当前敏感列表的异步控制
                if RESET_ONLY_NAME_PATTERN.search(str_signal)  # 仅选择复位或清零角色
            }

            # 任一异步控制角色都证明规则适用。
            bool_applicable = bool_applicable or bool(set_set_controls or set_reset_controls)  # 累积异步控制适用性

            # 两类集合必须同时非空才属于禁止组合。
            if not set_set_controls or not set_reset_controls:

                # 单一控制角色符合本规则。
                continue

            # 合并并排序控制信号便于稳定修复。
            int_line = int(dict_always.get("line_start") or 1)  # 双向异步控制的一基行号

            # 合并后的信号列表用于解释双向控制来源。
            str_evidence = ", ".join(sorted(set_set_controls | set_reset_controls))  # set/reset 信号列表

            # 每个双向控制块形成独立 finding。
            list_findings.append(
                PgFinding(
                    source_facts.relative_path,
                    int_line,
                    "同一 always 同时使用异步 set 和 reset。",
                    str_evidence,
                )
            )

    # 任一双向异步控制块都使门禁失败。
    if list_findings:

        # 返回全部 set/reset 并用证据。
        return failed(*list_findings)

    # 没有相关异步控制时不适用。
    return passed(applicable=bool_applicable)

# _ff_no_mixed_reset_style 要求复位时序块的全部触发器目标都在复位分支赋值。
def _ff_no_mixed_reset_style(facts: PgFacts) -> PgEvaluation:
    """检查同一时序块内触发器目标是否统一接受复位。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        部分目标缺少复位赋值时失败，否则按复位块事实通过。
    """

    # findings 保存复位覆盖不完整的时序块。
    list_findings: list[PgFinding] = []  # 混合复位覆盖证据

    # applicable 区分没有复位块与复位覆盖完整。
    bool_applicable = False  # 是否发现可判定的复位时序块

    # 首层复位 if 的 children 是复位分支赋值范围。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 每个时序块独立比较完整目标与复位目标。
        for dict_always in _sequential_always(dict_module):

            # 没有首层复位条件的块不属于本规则。
            dict_first_if = _first_if_node(dict_always)  # 当前时序块的首层 if

            # 普通时序逻辑不具备复位覆盖比较基础。
            if not _condition_control_signal(dict_first_if):

                # 无复位角色的首层条件退出本次极性比较。
                continue

            # formatter targets 提供整个时序块的写入目标集合。
            bool_applicable = True  # 当前时序块具有首层复位入口

            # targets 统一裁剪数组索引以比较寄存器基名。
            set_targets = {str(str_item).split("[")[0] for str_item in dict_always.get("targets", [])}  # 全部触发器目标

            # reset children 限定复位分支真实赋值范围。
            set_reset_targets = _assigned_targets(dict_first_if.get("children", []))  # 复位分支赋值目标

            # 差集直接表示未接受复位赋值的目标。
            set_missing = set_targets - set_reset_targets  # 未被复位分支覆盖的目标

            # 所有目标均复位时当前块合规。
            if not set_missing:

                # 当前块不存在混合复位覆盖。
                continue

            # 缺失目标排序后形成确定性证据。
            int_line = int(dict_always.get("line_start") or 1)  # 混合复位时序块的一基行号

            # 排序保证多目标证据稳定。
            str_evidence = ", ".join(sorted(set_missing))  # 未接受复位赋值的触发器目标

            # 每个覆盖不完整的时序块形成独立 finding。
            list_findings.append(
                PgFinding(
                    source_facts.relative_path,
                    int_line,
                    "同一时序块中存在未统一复位的触发器目标。",
                    str_evidence,
                )
            )

    # 任一目标覆盖不完整都使门禁失败。
    if list_findings:

        # 返回全部混合复位覆盖证据。
        return failed(*list_findings)

    # 没有复位时序块时不适用。
    return passed(applicable=bool_applicable)

# _ff_reset_condition_match 要求异步边沿与首层复位条件的有效电平一致。
def _ff_reset_condition_match(facts: PgFacts) -> PgEvaluation:
    """检查异步复位触发边沿是否匹配复位条件极性。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        边沿与条件极性不一致时失败，否则按异步复位事实通过。
    """

    # findings 保存异步触发和条件极性不一致的过程块。
    list_findings: list[PgFinding] = []  # 复位极性错配证据

    # applicable 区分无异步复位与极性匹配。
    bool_applicable = False  # 是否发现可比较的异步复位条件

    # 每个时序块只比较首层复位条件对应的异步边沿。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # formatter 时序分类限定极性检查范围。
        for dict_always in _sequential_always(dict_module):

            # 辅助函数独立完成单个过程块的极性比较。
            tuple_polarity_result = _reset_polarity_finding(source_facts.relative_path, dict_always)  # 当前 always 的极性检查结果

            # 元组首项表示当前过程块是否具有可比较异步复位。
            bool_current_applicable = tuple_polarity_result[0]  # 当前过程块的极性适用性

            # 元组次项是可选极性错配证据。
            pg_finding = tuple_polarity_result[1]  # 当前过程块的可选错配 finding

            # 任一可比较过程块都使规则具有适用性。
            bool_applicable = bool_applicable or bool_current_applicable  # 累积极性检查适用范围

            # 合规过程块没有 finding，无需追加证据。
            if pg_finding is None:

                # 继续检查其他时序过程块。
                continue

            # 当前错配证据进入最终报告。
            list_findings.append(pg_finding)

    # 任一极性错配都使门禁失败。
    if list_findings:

        # 返回全部触发极性错配证据。
        return failed(*list_findings)

    # 没有可比较异步复位时不适用。
    return passed(applicable=bool_applicable)

# _reset_polarity_finding 比较单个时序过程块的边沿与条件极性。
def _reset_polarity_finding(
    str_path: str,
    dict_always: dict[str, Any],
) -> tuple[bool, PgFinding | None]:
    """返回一个时序过程块的复位极性检查结果。

    参数:
        str_path: 当前 RTL 文件的稳定相对路径。
        dict_always: formatter AST 中的单个时序 always。
    返回:
        是否具有可比较异步复位以及可选错配证据。
    """

    # 首层条件是复位角色和有效电平的唯一来源。
    dict_first_if = _first_if_node(dict_always)  # 当前时序块首层条件

    # 复位信号从首层条件角色中提取。
    str_signal = _condition_control_signal(dict_first_if)  # 当前首层复位信号

    # 普通条件不具备复位极性语义。
    if not str_signal:

        # 同步复位缺少异步边沿，因此退出极性对照。
        return False, None

    # 查找同一信号在敏感列表中的异步触发边沿。
    dict_edges = {str_name: str_edge for str_edge, str_name in _edge_events(dict_always)}  # 异步信号到触发边沿映射

    # 只比较首层复位信号对应的边沿。
    str_edge = dict_edges.get(str_signal, "")  # 当前复位信号的异步边沿

    # 同步复位没有异步边沿可供本规则判断。
    if not str_edge:

        # 返回不适用且没有证据。
        return False, None

    # formatter 控制节点头部保留条件极性表达式。
    str_header = str(dict_first_if.get("header") or "")  # 当前首层复位条件文本

    # 条件中的取反或零比较表示低有效。
    bool_active_low = _condition_is_active_low(str_header, str_signal)  # 当前条件是否表达低有效

    # negedge 匹配低有效，posedge 匹配高有效。
    bool_matches = (  # 当前边沿与有效电平是否一致
        (str_edge == "negedge" and bool_active_low)  # 下降沿与低有效条件配对
        or (str_edge == "posedge" and not bool_active_low)  # 上升沿与高有效条件配对
    )

    # 匹配的触发沿和条件无需生成 finding。
    if bool_matches:

        # 返回已比较且没有错配证据。
        return True, None

    # always 起始行定位极性错配位置。
    int_line = int(dict_always.get("line_start") or 1)  # 极性错配过程块的一基行号

    # 证据同时展示敏感列表边沿和首层条件。
    str_evidence = f"{str_edge} {str_signal}; {str_header}"  # 当前触发沿与条件对照

    # 返回已比较状态和唯一错配证据。
    return True, PgFinding(
        str_path,
        int_line,
        "异步复位触发边沿与复位条件极性不一致。",
        str_evidence,
    )

# _sequential_always 只迭代 formatter 已分类的时序过程块。
def _sequential_always(dict_module: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """返回当前 module 中的时序 always 事实。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        逐个产生 trigger_kind 为 seq 的 always 字典。
    """

    # trigger_kind 由 formatter 统一判定，避免规则自行重建解析器。
    for dict_always in dict_module.get("always", []):

        # 只产出 formatter 明确分类为时序的过程块。
        if str(dict_always.get("trigger_kind") or "") == "seq":

            # 调用方逐项消费时序 always 事实。
            yield dict_always

# _edge_events 提取非时钟边沿控制事件。
def _edge_events(dict_always: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """提取时序块敏感列表中的非时钟边沿信号。

    参数:
        dict_always: formatter AST 中的单个 always 报告。
    返回:
        按头部顺序排列的边沿与信号元组。
    """

    # header 是 formatter 规范化后的可信过程块头部。
    str_header = str(dict_always.get("header") or "")  # 当前 always 头部

    # formatter clock 字段用于排除真正的时钟边沿。
    str_clock = str(dict_always.get("clock") or "")  # formatter 识别的时钟信号

    # 排除时钟后只保留异步控制事件。
    return tuple(  # 当前 always 的非时钟边沿事件
        (obj_match.group(1).lower(), obj_match.group(2))  # 规范化边沿名称并保留信号名
        for obj_match in re.finditer(r"\b(posedge|negedge)\s+([A-Za-z_]\w*)", str_header, flags=re.IGNORECASE)  # 顺序扫描边沿事件
        if obj_match.group(2) != str_clock  # formatter 时钟不属于异步控制
    )

# _first_if_node 读取过程块控制树中的首层 if。
def _first_if_node(dict_always: dict[str, Any]) -> dict[str, Any]:
    """返回时序块控制树的首个顶层 if 节点。

    参数:
        dict_always: formatter AST 中的单个 always 报告。
    返回:
        首个顶层 if 字典；不存在时返回空字典。
    """

    # 只接受顶层 if，防止把普通逻辑深处条件误认为复位入口。
    for dict_node in dict_always.get("nodes", []):

        # 首个顶层 if 是时序复位入口的唯一候选。
        if str(dict_node.get("kind") or "") == "if":

            # 返回 formatter 保留的完整控制节点。
            return dict_node

    # 空字典让调用方按不适用处理。
    return {}

# _condition_control_signal 提取 if 条件中的复位类控制信号。
def _condition_control_signal(dict_node: dict[str, Any]) -> str:
    """提取控制节点条件中的首个复位类标识符。

    参数:
        dict_node: formatter 控制树中的 if 节点。
    返回:
        复位类信号名；条件不含此类信号时返回空字符串。
    """

    # header 由 formatter 保留条件表达式边界。
    str_header = str(dict_node.get("header") or "")  # 当前控制节点头部

    # 只从标识符集合中选择具有明确复位角色的名称。
    for str_signal in re.findall(r"\b[A-Za-z_]\w*\b", str_header):

        # 控制语句关键字不属于复位信号候选。
        if str_signal.lower() == "if":

            # 跳过控制关键字并检查下一个标识符。
            continue

        # 只返回具有复位、清零或置位角色的标识符。
        if RESET_NAME_PATTERN.search(str_signal):

            # 首个复位类信号定义当前条件角色。
            return str_signal

    # 普通条件不属于复位入口。
    return ""

# _walk_nodes 深度优先遍历 formatter 控制树。
def _walk_nodes(list_nodes: Any) -> Iterator[dict[str, Any]]:
    """递归遍历 formatter 控制节点及其分支。

    参数:
        list_nodes: 当前层级的控制节点列表。
    返回:
        深度优先产生全部节点字典。
    """

    # 非列表事实按空集合处理，保持规则 fail-safe。
    if not isinstance(list_nodes, list):

        # 无可信节点时终止当前生成器分支。
        return

    # children、alternate 和 items 覆盖 formatter 当前控制树分支字段。
    for dict_node in list_nodes:

        # 非字典节点不符合 formatter 控制树合同。
        if not isinstance(dict_node, dict):

            # 跳过损坏节点并继续保留其他可信事实。
            continue

        # 先产出当前节点，再按固定字段顺序遍历后代。
        yield dict_node

        # children 保存主分支后代。
        yield from _walk_nodes(dict_node.get("children", []))

        # alternate 保存 else 或 else-if 后代。
        yield from _walk_nodes(dict_node.get("alternate", []))

        # items 保存 case 等分项后代。
        yield from _walk_nodes(dict_node.get("items", []))

# _assigned_targets 收集控制节点内的过程赋值目标。
def _assigned_targets(list_nodes: Any) -> set[str]:
    """收集指定控制树分支内的赋值目标基名。

    参数:
        list_nodes: formatter 控制节点列表。
    返回:
        当前分支中所有赋值左值的基名集合。
    """

    # targets 只接受 statement 文本左侧的 Verilog 标识符。
    set_targets: set[str] = set()  # 当前控制分支赋值目标集合

    # 遍历所有后代 statement，避免遗漏 begin/end 内嵌赋值。
    for dict_node in _walk_nodes(list_nodes):

        # 只有 statement 节点可能直接承载过程赋值。
        if str(dict_node.get("kind") or "") != "statement":

            # 控制节点继续由遍历器展开，无需在此处理。
            continue

        # 阻塞与非阻塞赋值共享左值基名提取模式。
        str_text = str(dict_node.get("text") or "")  # 当前语句原文

        # 简单赋值模式只提取左值标识符及可选数组索引。
        obj_match = re.match(r"\s*([A-Za-z_]\w*)(?:\s*\[[^]]+\])?\s*(?:<=|=)", str_text)  # 简单过程赋值左值

        # 解析成功后登记目标基名。
        if obj_match is not None:

            # set 去重同一分支内的重复目标赋值。
            set_targets.add(obj_match.group(1))

    # 返回复位分支真实写入的目标集合。
    return set_targets

# _has_reset_and_loop 识别复位入口之外的合规循环结构。
def _has_reset_and_loop(facts: PgFacts) -> bool:
    """判断目标是否包含复位时序块与循环节点。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        同一时序块同时具备复位入口和循环时返回 True。
    """

    # 正例把循环放在 reset 的 else 分支，因此不能只扫描循环后代条件。
    for _, dict_module, _, _ in iter_trusted_modules(facts):

        # 每个时序块独立判断复位入口和循环共存。
        for dict_always in _sequential_always(dict_module):

            # 没有首层复位入口的循环不属于复位混合规则。
            if not _condition_control_signal(_first_if_node(dict_always)):

                # 缺少复位入口时循环不具备本规则适用性。
                continue

            # 任一后代 loop 证明当前复位块含循环结构。
            if any(
                str(dict_node.get("kind") or "") == "loop"
                for dict_node in _walk_nodes(dict_always.get("nodes", []))
            ):

                # 合规与违规循环均表明规则实际适用。
                return True

    # 没有复位与循环共存结构时规则不适用。
    return False

# _module_assignments 提取可信 module 中的简单赋值左右值和相对行号。
def _module_assignments(str_module_text: str) -> Iterator[tuple[str, str, int]]:
    """遍历 module 内可确定边界的连续或过程赋值。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        逐个产生左值基名、右值文本和零基相对行号。
    """

    # 模式只接受简单标识符左值并以分号限定右值范围。
    str_pattern = (  # 连续与过程赋值的统一词法模式
        r"(?:\bassign\s+)?\b([A-Za-z_]\w*)"
        r"(?:\s*\[[^]]+\])?\s*(?:<=|=)\s*([^;\n]+);"
    )

    # formatter 已确认 module 边界，词法扫描不会越过其他设计单元。
    for obj_match in re.finditer(str_pattern, str_module_text):

        # 左值基名用于区分复位网络自身与普通数据目标。
        str_lhs = obj_match.group(1)  # 当前简单赋值左值

        # 右值保留运算符和标识符供复位角色检查。
        str_rhs = obj_match.group(2).strip()  # 当前简单赋值右值

        # 起始偏移之前的换行数就是 module 内零基行偏移。
        int_offset_line = str_module_text[: obj_match.start()].count("\n")  # 当前赋值的相对行号

        # 调用方逐项消费稳定的赋值事实。
        yield str_lhs, str_rhs, int_offset_line

# _condition_is_active_low 识别首层条件是否表达低有效复位。
def _condition_is_active_low(str_header: str, str_signal: str) -> bool:
    """判断复位条件是否把指定信号视为低有效。

    参数:
        str_header: formatter 控制节点的 if 头部。
        str_signal: 需要判断极性的复位信号名。
    返回:
        条件为取反、按位取反或与零比较时返回 True。
    """

    # 转义信号名避免标识符中的特殊字符影响模式。
    str_escaped = re.escape(str_signal)  # 当前复位信号的安全正则文本

    # 低有效模式覆盖取反和显式零比较。
    str_low_pattern = (  # 低有效条件模式
        rf"(?:!|~)\s*{str_escaped}\b"
        rf"|\b{str_escaped}\s*(?:==|===)\s*(?:1\s*'\s*b\s*)?0\b"
    )

    # 任一明确低有效表达式均匹配 negedge 触发。
    return re.search(str_low_pattern, str_header, flags=re.IGNORECASE) is not None
