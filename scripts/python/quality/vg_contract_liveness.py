"""汇总信号读写事实并执行生存性门禁。"""

# future annotations 让信号事实的递归结构保持延迟求值。
from __future__ import annotations

# re 支持声明名称在顶层源码中的保守使用计数。
import re

# typing 描述 formatter 报告中的动态表达式节点。
from typing import Any

# 生存性门禁依赖共享 RTL 事实与统一评估状态。
from .vg_semantic_facts import VgFacts
from .vg_rule_models import VgEvaluation, VgFinding, failed, passed

# 复用模块枚举与源码定位证据构造。
from .vg_contract_parameter import _finding, _source_modules

# _identifier_names 从表达式节点中提取数据流引用名称。
def _identifier_names(node: Any) -> set[str]:
    """递归返回 formatter 表达式节点中的标识符集合。

    参数:
        node: formatter 表达式或控制节点。
    返回:
        当前节点及其子节点引用的信号名称集合。
    """

    # 非字典节点没有可读取的标识符事实。
    if not isinstance(node, dict):

        # 结构缺失不猜测信号使用关系。
        return set()

    # 初始化当前节点的引用集合。
    set_names: set[str] = set()  # 当前表达式引用名称

    # formatter identifier 节点直接提供 name 字段。
    if node.get("kind") == "identifier" or node.get("node_kind") == "identifier":

        # 只接受非空普通标识符。
        str_name = str(node.get("name") or "")  # 当前节点名称

        # 仅保存有名称的声明，供后续数据流集合使用。
        if str_name:

            # 记录当前信号引用。
            set_names.add(str_name)

    # children/operands 都是现有 formatter 的结构化子节点容器。
    for str_key in ("children", "operands"):

        # 递归读取当前容器的所有子节点。
        for obj_child in node.get(str_key, []) or []:

            # 合并子节点中的标识符。
            set_names.update(_identifier_names(obj_child))

    # 二元表达式有时使用显式 left/right 字段。
    for str_key in ("left", "right"):

        # 递归读取可能存在的左右节点。
        set_names.update(_identifier_names(node.get(str_key)))

    # 返回当前节点的全部信号引用。
    return set_names

# _module_declarations 统一收集 formatter 的声明和子程序事实。
def _module_declarations(
    dict_module: dict[str, Any],
    source_lines: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """返回当前模块的名称、类别和行号事实。

    参数:
        dict_module: formatter 生成的模块事实。
        source_lines: 当前源码文件的物理行元组。
    返回:
        供 VG153/VG154 复用的声明事实表。
    """

    # 该字典把每个名称映射到 VG154 生存性报告所需的类别、行号和原始 formatter 事实。
    dict_declarations: dict[str, dict[str, Any]] = {}  # 为 VG154 建立名称到类别、行号和原始事实的生存性索引

    # 按 formatter 类别保留每个声明的原始对象和定位信息。
    for str_key in ("decls", "params", "localparams", "functions", "tasks"):

        # 读取当前类别的声明列表。
        for dict_item in dict_module.get(str_key, []) or []:

            # 名称缺失的声明交给 formatter 解析错误处理。
            str_name = str(dict_item.get("name") or "")  # 当前声明名称

            # 旧 formatter 的 task 事实没有 name，回到 task 声明行提取标识符。
            if not str_name and str_key == "tasks":

                # 读取 task 起始行作为名称恢复来源。
                int_line = int(dict_item.get("line_start") or 1)  # task 头部的一基行号

                # 读取 task header 文本作为名称恢复输入。
                str_header = source_lines[int_line - 1] if int_line <= len(source_lines) else ""  # 提取 task 声明头部

                # 解析 task header 的名称 token。
                obj_task = re.search(r"\btask\s+(?:automatic\s+)?([A-Za-z_][A-Za-z0-9_]*)", str_header)  # 当前 task 名称匹配

                # 仅接受普通 Verilog task 标识符。
                if obj_task is not None:

                    # 保存从 task header 恢复的名称。
                    str_name = obj_task.group(1)  # 当前 task 的恢复名称

            # 仅把有名称的声明纳入后续生存性分析。
            if str_name:

                # 保存声明类别、行号和原始事实。
                dict_declarations[str_name] = {
                    "kind": str_key,  # 当前声明类别
                    "line": int(dict_item.get("line_start") or 1),  # 当前声明起始行
                    "object": dict_item,  # formatter 原始声明事实
                }

    # 返回统一声明表。
    return dict_declarations

# _fallback_read_names 补足 formatter 尚未展开的普通名称读取。
def _fallback_read_names(
    dict_declarations: dict[str, dict[str, Any]],
    source_lines: tuple[str, ...],
    function_calls: Iterable[dict[str, Any]] = (),
) -> set[str]:
    """按声明类别补足源码中的真实读取名称。

    参数:
        dict_declarations: 当前模块声明、函数和任务的统一表。
        source_lines: 当前源码文件的物理行元组。
        function_calls: formatter 收集的函数调用事实。
    返回:
        可确认在声明之外出现的名称集合。
    """

    # 去掉单行注释后再做保守词法计数。
    list_code_lines = [line.split("//", 1)[0] for line in source_lines]  # 当前模块可执行源码行

    # function/task body 属于子程序内部语义，不把其目标写入顶层读取集合。
    for dict_declaration in dict_declarations.values():

        # 读取 function/task 的源码范围。
        if dict_declaration.get("kind") not in {"functions", "tasks"}:

            # 普通信号不需要排除源码范围。
            continue

        # formatter 对子程序提供起止行，缺失时只跳过起始行。
        int_start = int(dict_declaration.get("object", {}).get("line_start") or 1)  # 子程序范围起始行

        # 读取同一子程序范围的结束位置。
        int_end = int(dict_declaration.get("object", {}).get("line_end") or int_start)  # 当前子程序结束行

        # 清空子程序行，避免把内部实现当作模块可观察使用。
        for int_index in range(max(1, int_start), min(len(list_code_lines), int_end) + 1):

            # 保留行号长度，后续正则证据仍可稳定定位。
            list_code_lines[int_index - 1] = ""  # 隐去子程序内部实现行

    # 合并已过滤的源码行供名称扫描。
    str_code = "\n".join(list_code_lines)  # 顶层可观察源码

    # 结构化 function_calls 是函数使用的唯一可靠来源。
    set_reads = {  # 从结构化 function_calls 建立初始读取集合。
        str(dict_call.get("callee") or "")  # 当前函数调用的被调名称
        for dict_call in function_calls  # 遍历 formatter 函数调用事实
        if dict_call.get("callee")  # 仅保留有被调名称的调用事实
    }

    # 逐项补足普通信号和 task 调用，避免函数体返回赋值造成假使用。
    for str_name, dict_declaration in dict_declarations.items():

        # 当前名称类别决定后续的调用判定策略。
        str_kind = str(dict_declaration.get("kind") or "")  # 选择 function/task 或 signal 语义

        # function 的声明名可能在函数返回赋值中重复，不能用词频判定调用。
        if str_kind == "functions":

            # 结构化 function_calls 已在上面提供真实调用名称。
            continue

        # task 没有返回赋值，词频大于一可作为调用的保守证据。
        int_occurrences = len(re.findall(rf"\b{re.escape(str_name)}\b", str_code))  # 当前声明名称出现次数

        # 普通信号和 task 调用都要求声明外至少再出现一次。
        if int_occurrences > 1:

            # 文本补足只增加读取候选，不覆盖结构化驱动事实。
            set_reads.add(str_name)

    # 返回可确认的真实读取集合。
    return set_reads

# _module_signal_facts 汇总模块声明、驱动和读取事实。
def _module_signal_facts(dict_module: dict[str, Any], source_lines: tuple[str, ...]) -> dict[str, Any]:
    """返回 VG153/VG154 所需的声明、驱动和读取集合。

    参数:
        dict_module: formatter 生成的模块事实。
        source_lines: 当前源码文件的物理行元组。
    返回:
        声明、端口、驱动和读取集合组成的结构化事实。
    """

    # 读取模块声明、函数和任务的统一事实表。
    dict_declarations = _module_declarations(dict_module, source_lines)  # 当前模块内部声明表

    # ports 记录输入/输出方向，供无驱动检查识别边界驱动。
    dict_ports = {
        str(dict_port.get("name") or ""): str(dict_port.get("direction") or "")  # 端口方向事实
        for dict_port in dict_module.get("ports", []) or []  # 遍历模块端口
        if dict_port.get("name")  # 只保留有名称的端口
    }  # 当前模块端口方向表

    # drivers 收集 continuous assign、过程 target 和已知模块输入。
    set_drivers: set[str] = {
        str(dict_port_name)  # 输入端口在当前模块边界上已有外部驱动
        for dict_port_name, str_direction in dict_ports.items()  # 遍历端口方向
        if str_direction in {"input", "inout"}  # 输入和 inout 视为边界驱动
    }  # 当前模块确定驱动集合

    # 声明初始化同样是可确认的本地驱动，不能因未进入 comb_expressions 而误报 VG153。
    for str_name, dict_declaration in dict_declarations.items():

        # formatter 将 wire/reg/logic 初始化保存在声明事实的 init 字段。
        if str(dict_declaration["object"].get("init") or "").strip():

            # 记录 inline declaration assignment 的驱动目标。
            set_drivers.add(str_name)

    # reads 保存 formatter 表达式和实例 actual 事实引用。
    set_reads: set[str] = set()  # 当前模块确定读取集合

    # comb_expressions 已包含 continuous/procedural 的结构化表达式。
    for dict_expression in dict_module.get("comb_expressions", []) or []:

        # target 是该表达式的确定驱动目标。
        str_target = str(dict_expression.get("target") or "")  # 当前表达式目标

        # 读取目标存在时才记录其驱动证据。
        if str_target:

            # 同一 target 可能在多个 branch 中重复出现，集合去重。
            set_drivers.add(str_target)

        # 递归读取右值表达式和控制条件中的名称。
        set_reads.update(_identifier_names(dict_expression.get("expression")))

        # 遍历当前表达式的控制条件引用。
        for dict_control in dict_expression.get("controls", []) or []:

            # 控制条件同样属于真实读取。
            set_reads.update(_identifier_names(dict_control))

    # 实例端口 actual references 是父模块的读取或驱动边界事实。
    for dict_instance in dict_module.get("instances", []) or []:

        # 结构不完整的实例只贡献已确认 actual references。
        for dict_association in dict_instance.get("port_associations", []) or []:

            # actual references 由 formatter 结构化提供。
            dict_actual = dict_association.get("actual", {}) or {}  # 当前实例 actual 事实

            # 跳过输入端口，继续关注输出端口驱动。
            set_reads.update(str(str_name) for str_name in dict_actual.get("references", []) or [])  # actual 引用集合

    # fallback 文本扫描补足 formatter 未展开的信号和 task 引用。
    set_reads.update(
        _fallback_read_names(
            dict_declarations,
            source_lines,
            dict_module.get("function_calls", []) or [],
        )
    )

    # 返回新增规则需要的统一事实字典。
    return {
        "declarations": dict_declarations,  # 内部 parameter/localparam/reg/wire 声明
        "ports": dict_ports,  # 模块端口方向
        "drivers": set_drivers,  # 已知驱动集合
        "reads": set_reads,  # 已知读取集合
    }

# evaluate_liveness_gate 执行 VG153 和 VG154。
def evaluate_liveness_gate(facts: VgFacts, str_gate_id: str) -> VgEvaluation:
    """检查读取无驱动和声明无使用问题。

    参数:
        facts: 当前 RTL 的共享 formatter 事实。
        str_gate_id: VG153 或 VG154 固定编号。
    返回:
        对应生存性规则的通过或失败结论。
    """

    # 收集指定规则的全部 finding。
    list_findings: list[VgFinding] = []  # 当前生存性规则证据

    # 存在内部声明或端口时进入生存性适用范围。
    bool_applicable = False  # 当前 RTL 是否出现候选声明

    # 每个模块独立计算声明、读取和驱动关系。
    for obj_source, dict_module in _source_modules(facts):

        # 当前模块的结构化生存性事实。
        dict_signal_facts = _module_signal_facts(dict_module, obj_source.lines)  # 当前模块声明驱动读取事实

        # 保存模块声明表，供驱动和读取集合复用。
        dict_declarations = dict_signal_facts["declarations"]  # 当前模块声明表

        # 保存确定驱动集合，区分边界和过程驱动。
        set_drivers = dict_signal_facts["drivers"]  # 当前模块驱动集合

        # 保存表达式读取集合，识别生存性使用关系。
        set_reads = dict_signal_facts["reads"]  # 当前模块读取集合

        # VG153 只检查内部声明和输出端口，不检查 parameter/localparam。
        if str_gate_id == "VG153":

            # 读取且没有驱动的内部声明属于确定阻断。
            for str_name, dict_declaration in dict_declarations.items():

                # 参数和 localparam 没有过程驱动语义。
                if dict_declaration["kind"] in {"params", "localparams"}:

                    # 交给 VG154 检查是否被使用。
                    continue

                # 只对真正被读取的声明执行无驱动判断。
                if str_name not in set_reads:

                    # 未被读取的未驱动声明不属于 VG153。
                    continue

                # 当前声明已经有驱动时通过该符号。
                bool_applicable = True  # 读取到已驱动声明，规则适用且该项通过。

                # 已有驱动的输出端口不产生无驱动证据。
                if str_name in set_drivers:

                    # 已驱动符号无需产生新的无驱动证据。
                    continue

                # 被读取但无驱动的符号生成 BLOCKER finding。
                list_findings.append(
                    _finding(
                        obj_source,
                        dict_module,
                        line=int(dict_declaration["line"]),
                        message="信号被读取但没有可确认的驱动源。",
                        evidence=str_name,
                        metadata=(("symbol", str_name), ("driver_state", "undriven"), ("read_state", "read")),
                    )
                )

            # 输出端口没有驱动也属于 VG153，但仅在确实存在输出端口时适用。
            for str_name, str_direction in dict_signal_facts["ports"].items():

                # 只检查 output，inout 由边界驱动事实处理。
                if str_direction != "output":

                    # 输入和 inout 不属于无驱动输出候选。
                    continue

                # 输出端口进入适用范围，即使其驱动最终缺失。
                bool_applicable = True  # 输出端口进入无驱动检查候选。

                # 读取到的声明已参与可观察逻辑。
                if str_name in set_drivers:

                    # 已知驱动的输出通过检查。
                    continue

                # 无任何合法驱动的输出端口生成 finding。
                list_findings.append(
                    _finding(
                        obj_source,
                        dict_module,
                        line=None,
                        message="输出端口没有可确认的驱动源。",
                        evidence=str_name,
                        metadata=(("symbol", str_name), ("driver_state", "undriven_output")),
                    )
                )

        # VG154 检查内部声明的实际使用情况。
        else:

            # 只有内部声明才属于未使用规则的候选集合。
            for str_name, dict_declaration in dict_declarations.items():

                # 当前声明没有任何读取使用时生成 warning finding。
                bool_applicable = True  # 内部声明进入未使用检查候选。

                # 已读取声明不产生未使用警告。
                if str_name in set_reads:

                    # 已使用声明不产生 VG154 证据。
                    continue

                # 端口仅作为接口契约，不因未在本模块消费而报警。
                if str_name in dict_signal_facts["ports"]:

                    # 跳过所有端口声明。
                    continue

                # 记录未使用声明类别、名称和声明行。
                str_object_kind = dict_declaration["object"].get("kind")  # formatter signal 原始类别

                # 统一为 finding 暴露可读的声明类别。
                str_decl_kind = str(str_object_kind or dict_declaration["kind"])  # 当前 finding 类别证据

                # 追加未使用声明的结构化 finding。
                list_findings.append(
                    _finding(
                        obj_source,
                        dict_module,
                        line=int(dict_declaration["line"]),
                        message="声明未参与当前模块的可观察逻辑。",
                        evidence=str_name,
                        metadata=(
                            ("symbol", str_name),
                            ("declaration_kind", str_decl_kind),
                            ("use_state", "unused"),
                        ),
                    )
                )

    # VG153 的确定违规使用 failed，未发现候选时不适用。
    if list_findings:

        # VG154 保持 WARNING 级 finding，但状态仍由统一 strict 策略解释。
        return failed(*list_findings)

    # 无 finding 时返回适用性事实。
    return passed(applicable=bool_applicable)

