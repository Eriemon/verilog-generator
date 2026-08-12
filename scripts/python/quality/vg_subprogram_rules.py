"""实现 function 与 task 相关 RTL VG 门禁。"""

# future annotations 延后解析规则模型类型。
from __future__ import annotations

# re 只在 formatter 确认的子程序边界内定位构造。
import re

# facts 提供 formatter AST 确认的 module 和子程序结构。
from .vg_semantic_facts import VgFacts, iter_trusted_modules

# models 统一逐门禁结论与证据格式。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# value facts 提供与其他 VG 规则一致的受限位宽求值。
from .vg_value_facts import module_parameter_values, parse_width

# evaluate_subprogram_gate 按固定编号路由 function/task 规则。
def evaluate_subprogram_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行 function/task 精确子程序门禁。

    参数:
        str_gate_id: 当前执行的固定 VG 子程序编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前子程序规则的逐门禁结论。
    """

    # VG106 只判断函数直接调用自身的情况。
    if str_gate_id == "VG106":

        # 递归规则消费 formatter 的 functions 边界。
        return _function_recursion(facts)

    # VG115 比较 task 调用实参与声明形参的静态位宽。
    if str_gate_id == "VG115":

        # task 接口规则只消费 formatter 确认的 module 和 task 边界。
        return _task_io_width_match(facts)

    # VG121 比较 function 声明宽度与返回赋值表达式宽度。
    if str_gate_id == "VG121":

        # function 返回规则在每个 module 作用域内独立求宽。
        return _function_return_width(facts)

    # VG127 禁止 function 或 task 直接写 module 作用域状态。
    if str_gate_id == "VG127":

        # 全局写入规则排除子程序自己的形参、局部变量和返回变量。
        return _subprogram_no_global_write(facts)

    # VG133 只扫描 task 内部的时序控制。
    if str_gate_id == "VG133":

        # task 时序规则不扩大到 module 其他区域。
        return _task_timing_control(facts)

    # 本模块剩余固定入口为 VG139。
    return _function_nonblocking(facts)

# _task_io_width_match 比较 task 输入与输出形式端口的声明位宽。
def _task_io_width_match(facts: VgFacts) -> VgEvaluation:
    """检查 task 的 input 与 output 形式端口位宽是否一致。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG115 的确定结论或证据不足结论。
    """

    # findings 保存 input 与 output 形参的确定宽度差异。
    list_findings: list[VgFinding] = []  # task 形式端口宽度违规

    # unknown 防止复杂区间被默认视为宽度一致。
    bool_unknown = False  # 是否存在无法静态求宽的形式端口

    # applicable 只有同时存在 input 和 output 形参时成立。
    bool_applicable = False  # 是否存在可比较的 task 接口

    # 每个 module 的 parameter 和 task 声明作用域独立。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # parameter 支持形式端口中的简单符号区间。
        dict_parameter_values = module_parameter_values(dict_module)  # 当前 module 整数参数

        # formatter tasks 集合限定声明解析范围。
        for dict_task in dict_module.get("tasks", []):

            # 单个 task 的适用性、未知状态和违规由辅助函数集中判断。
            tuple_result = _task_port_width_result(  # 当前 task 端口宽度检查结果
                source_facts.relative_path,  # 当前 task 所在 RTL 相对路径
                dict_task,  # formatter 提供的当前 task 字典
                dict_parameter_values,  # 当前 module 的整数 parameter
            )

            # 当前 task 的结果字段分别汇总到设计级状态。
            bool_task_applicable, bool_task_unknown, obj_finding = tuple_result  # 单个 task 三态结论

            # 适用标记在任一 task 可比较后保持为真。
            bool_applicable = bool_applicable or bool_task_applicable  # 设计是否已有适用 task

            # 未知标记在任一 task 无法求宽后保持为真。
            bool_unknown = bool_unknown or bool_task_unknown  # 设计是否已有未知 task

            # 只有确定不一致的 task 才携带 finding。
            if obj_finding is not None:

                # 证据顺序保持 formatter 的声明顺序。
                list_findings.append(obj_finding)

    # 确定违规优先于同一设计中的其他未知 task。
    if list_findings:

        # 返回全部 task 接口差异证据。
        return failed(*list_findings)

    # 已有适用 task 但区间无法求值时必须 fail-closed。
    if bool_applicable and bool_unknown:

        # 不确定结论指明形式端口宽度证据不足。
        return inconclusive("task 输入或输出形式端口位宽无法静态确定。")

    # 全部适用 task 宽度一致时确定通过。
    return passed(applicable=bool_applicable)

# _task_port_width_result 判断单个 task 的接口宽度合同。
def _task_port_width_result(
    str_path: str,
    dict_task: dict[str, object],
    dict_parameter_values: dict[str, int],
) -> tuple[bool, bool, VgFinding | None]:
    """返回单个 task 的适用性、未知状态和可选违规证据。

    参数:
        str_path: 当前 task 所在 RTL 相对路径。
        dict_task: formatter AST 中的 task 字典。
        dict_parameter_values: 当前 module 的整数 parameter。
    返回:
        适用标记、未知标记和可选 finding。
    """

    # 当前 task 文本只来自 formatter 已确认的边界。
    str_task_text = _block_text(dict_task)  # 当前 task 的可信源码

    # 形式端口按源码顺序保留方向和静态位宽。
    list_formals = _formal_ports(str_task_text, dict_parameter_values)  # 当前 task 形式端口

    # 两侧列表分别保存输入语义和输出语义的位宽。
    list_input_widths: list[int | None] = []  # 当前 task 的输入侧形参宽度

    # 输出列表独立容纳 output 与 inout 方向。
    list_output_widths: list[int | None] = []  # 当前 task 的输出侧形参宽度

    # 逐项分类避免丢失同一声明中的端口顺序。
    for str_direction, int_width in list_formals:

        # input 只进入输入侧集合。
        if str_direction == "input":

            # 当前形参贡献一个输入位宽事实。
            list_input_widths.append(int_width)

        # output 和 inout 均承担输出连接语义。
        if str_direction in {"output", "inout"}:

            # output 或 inout 形参补充被比较的输出侧宽度。
            list_output_widths.append(int_width)

    # 没有输入或输出的一侧时不满足本规则适用结构。
    if not list_input_widths or not list_output_widths:

        # 单向 task 不参与输入输出位宽合同。
        return False, False, None

    # 任一未知区间都会破坏确定比较能力。
    if any(int_width is None for int_width in (*list_input_widths, *list_output_widths)):

        # 已适用但无法求宽的 task 必须传播未知状态。
        return True, True, None

    # 所有 input 与 output 形参必须共享同一个静态位宽。
    set_widths = set(list_input_widths + list_output_widths)  # 当前 task 形式端口宽度集合

    # 单一宽度满足 task 输入输出一致合同。
    if len(set_widths) == 1:

        # 当前 task 形成确定通过证据。
        return True, False, None

    # task 定义行稳定定位整组接口声明。
    int_line = int(dict_task.get("line_start") or 1)  # 当前 task 定义的一基行号

    # 名称仅用于形成最小可读证据。
    str_task_name = _subprogram_name(str_task_text, "task") or "<unknown>"  # 当前 task 名称

    # finding 列出已确认的不一致宽度。
    obj_finding = VgFinding(  # 当前 task 的端口宽度差异证据
        str_path,  # 违规证据所属 RTL 文件
        int_line,  # 当前 task 定义行
        "task 输入与输出形式端口位宽不一致。",  # VG115 用户诊断
        f"{str_task_name}: widths {sorted(set_widths)}",  # task 名称与已确认宽度集合
    )

    # 当前 task 适用、可确定且存在违规。
    return True, False, obj_finding

# _function_return_width 要求 function 显式声明返回位宽。
def _function_return_width(facts: VgFacts) -> VgEvaluation:
    """检查 function 声明是否包含显式返回区间。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG121 的确定性执行结论。
    """

    # findings 保存缺少显式返回区间的 function 定义。
    list_findings: list[VgFinding] = []  # function 返回声明违规

    # applicable 表明至少存在一个 formatter 确认的 function。
    bool_applicable = False  # 当前设计是否包含 function

    # formatter functions 集合限定所有声明检查边界。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 每个 function 声明独立检查返回区间。
        for dict_function in dict_module.get("functions", []):

            # 找到 function 后规则进入适用状态。
            bool_applicable = True  # 已确认至少一个 function 声明

            # 声明首行来自 formatter 已确认的 function block。
            str_function_text = _block_text(dict_function)  # 当前待核对返回区间的 function 源码

            # 支持 automatic、signed 和显式方括号返回区间。
            obj_match = re.search(  # 供 VG121 区分显式返回区间与默认标量声明
                r"\bfunction\b\s+(?:automatic\s+)?(?:signed\s+)?(\[[^]]+\]\s+)?([A-Za-z_]\w*)\s*;",  # 捕获可选返回区间和 function 标识符
                str_function_text,  # 本次返回区间检查的源码范围
            )

            # 无法识别名称的声明由 formatter 可信边界兜底，不猜测通过。
            if obj_match is None:

                # 定义位置提供可追溯的不完整声明证据。
                int_line = int(dict_function.get("line_start") or 1)  # 无法识别返回声明的 function 定义行

                # 不支持的声明形状按缺少受支持显式位宽处理。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,
                        int_line,
                        "function 未提供可识别的显式返回位宽。",
                        str_function_text.splitlines()[0] if str_function_text else "",
                    )
                )

                # 当前定义已经形成失败证据，继续核对下一 function。
                continue

            # 第一捕获组存在才表示声明了显式返回区间。
            if obj_match.group(1) is not None:

                # 显式区间已满足合同，继续核对下一 function。
                continue

            # 标量默认宽度不等于显式指定返回值位宽。
            int_line = int(dict_function.get("line_start") or 1)  # 缺少显式返回区间的 function 定义行

            # finding 指向缺少方括号区间的 function 名称。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "function 必须显式指定返回值位宽。",
                    obj_match.group(2),
                )
            )

    # 任一缺失显式区间都触发 VG121。
    if list_findings:

        # 失败结论保留全部 function 定义证据。
        return failed(*list_findings)

    # 所有 function 均显式声明返回区间时确定通过。
    return passed(applicable=bool_applicable)

# _subprogram_no_global_write 禁止子程序直接修改 module 作用域对象。
def _subprogram_no_global_write(facts: VgFacts) -> VgEvaluation:
    """检查 function 与 task 是否写入非局部 module 状态。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG127 的确定性执行结论。
    """

    # findings 保存每个子程序中的首层全局写入证据。
    list_findings: list[VgFinding] = []  # 子程序全局写入违规

    # applicable 表明设计中存在 function 或 task。
    bool_applicable = False  # 当前设计是否包含子程序

    # 每个 module 的全局符号表独立建立。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 端口和内部声明都属于 module 作用域对象。
        set_global_names = _module_global_names(dict_module)  # 当前 module 可写全局名称

        # 两类子程序共享相同局部作用域判定。
        for str_collection, str_kind in (("functions", "function"), ("tasks", "task")):

            # formatter 集合保证扫描不越过子程序边界。
            for dict_block in dict_module.get(str_collection, []):

                # 找到任一子程序后规则进入适用状态。
                bool_applicable = True  # 已确认至少一个可分析子程序

                # 单个 block 的全局写入先形成独立证据列表。
                list_block_findings = _global_write_findings_for_block(  # 当前子程序全局写入证据
                    source_facts.relative_path,  # 当前子程序所在 RTL 相对路径
                    dict_module,  # 当前子程序所属 module 字典
                    dict_block,  # formatter 提供的当前子程序字典
                    str_kind,  # function 或 task 声明种类
                    set_global_names,  # 当前 module 作用域对象名称
                )

                # 设计级证据保持 module 和子程序遍历顺序。
                list_findings.extend(list_block_findings)

    # 任一全局写入都触发 VG127。
    if list_findings:

        # 失败结论保留全部子程序写入位置。
        return failed(*list_findings)

    # 没有全局写入时报告真实子程序适用状态。
    return passed(applicable=bool_applicable)

# _module_global_names 收集 module 端口和内部声明名称。
def _module_global_names(dict_module: dict[str, object]) -> set[str]:
    """返回当前 module 可被子程序写入的全局对象名称。

    参数:
        dict_module: formatter AST 中的 module 字典。
    返回:
        非空端口和内部声明名称集合。
    """

    # names 汇总端口和内部声明中的非空名称。
    set_names: set[str] = set()  # 左值所有权判定使用的 module 名称集合

    # 两类 formatter 声明集合共享 name 字段合同。
    for str_collection in ("ports", "decls"):

        # 每条记录独立过滤空名称。
        for dict_item in dict_module.get(str_collection, []):

            # 名称文本作为后续赋值所有权键。
            str_name = str(dict_item.get("name") or "")  # 当前 module 声明名称

            # 匿名或不完整声明不进入所有权集合。
            if str_name:

                # 集合天然去重同名声明记录。
                set_names.add(str_name)

    # 返回当前 module 的可写全局对象集合。
    return set_names

# _global_write_findings_for_block 收集单个子程序的全局写入。
def _global_write_findings_for_block(
    str_path: str,
    dict_module: dict[str, object],
    dict_block: dict[str, object],
    str_kind: str,
    set_global_names: set[str],
) -> list[VgFinding]:
    """返回一个 function 或 task 中确定的全局写入证据。

    参数:
        str_path: 当前子程序所在 RTL 相对路径。
        dict_module: 当前子程序所属 module 字典。
        dict_block: formatter AST 中的子程序字典。
        str_kind: 固定为 function 或 task 的声明种类。
        set_global_names: 当前 module 作用域对象名称。
    返回:
        当前子程序中的全部确定全局写入证据。
    """

    # 当前子程序文本来自 formatter 已确认 span。
    str_text = _block_text(dict_block)  # 当前 function 或 task 源码

    # parameter 只用于解析当前子程序局部声明的符号区间。
    dict_parameter_values = module_parameter_values(dict_module)  # 局部区间使用的整数参数

    # 参数和局部声明名称均可在子程序内合法写入。
    set_local_names = set(_local_widths(str_text, dict_parameter_values))  # 子程序局部名称

    # function 名称是隐式返回变量，不属于全局写入。
    str_subprogram_name = _subprogram_name(str_text, str_kind)  # 当前子程序名称

    # 可识别名称加入局部集合，排除返回变量赋值。
    if str_subprogram_name is not None:

        # task 名称通常不作为左值，统一登记不扩大风险。
        set_local_names.add(str_subprogram_name)

    # 赋值模式只接受简单或带选择的标识符左值。
    str_pattern = r"(?m)^\s*([A-Za-z_]\w*)(?:\s*\[[^]]+\])?\s*(?:<=|=)(?!=)"  # 子程序赋值左值

    # block findings 只保存属于 module 声明表的赋值目标。
    list_findings: list[VgFinding] = []  # 单个子程序块的全局写入证据

    # 逐条赋值判断基础名称所属作用域。
    for obj_assignment in re.finditer(str_pattern, str_text):

        # 捕获组只保留左值基础标识符。
        str_target = obj_assignment.group(1)  # 当前子程序赋值目标名称

        # 局部变量、形式端口和 function 返回变量允许写入。
        if str_target in set_local_names:

            # 当前赋值不触及 module 状态。
            continue

        # 未在 module 声明表中的名称不由本规则猜测所有权。
        if str_target not in set_global_names:

            # 其他语义错误由对应解析或声明规则负责。
            continue

        # 块起始行加匹配偏移定位全局写入。
        int_line = int(dict_block.get("line_start") or 1) + str_text.count(  # 全局写入的一基文件行号
            "\n",  # 按源码换行计算块内偏移
            0,  # 从子程序文本起点开始计数
            obj_assignment.start(),  # 截止当前赋值起点
        )

        # finding 指明子程序种类和被修改的全局名称。
        list_findings.append(
            VgFinding(
                str_path,
                int_line,
                "子程序直接写入 module 作用域对象。",
                f"{str_kind} writes {str_target}",
            )
        )

    # 返回当前 block 的稳定证据顺序。
    return list_findings

# _function_recursion 识别函数体对自身名称的调用。
def _function_recursion(facts: VgFacts) -> VgEvaluation:
    """只在函数确实调用自身时报告递归。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG106 的确定性执行结论。
    """

    # findings 保存每个直接递归函数的定义位置。
    list_findings: list[VgFinding] = []  # 直接递归调用证据集合

    # applicable 表明设计中至少存在一个 formatter 函数块。
    bool_applicable = False  # 当前设计是否包含可分析 function

    # 每个 module 的函数名称只在本 module 作用域内判断。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # formatter functions 列表限定所有词法扫描边界。
        for dict_function in dict_module.get("functions", []):

            # 找到函数块后规则即进入适用状态。
            bool_applicable = True  # 函数递归规则已有真实适用对象

            # block text 只来自 formatter 已切分的函数范围。
            str_text = _block_text(dict_function)  # 当前函数的可信源码文本

            # 函数声明匹配提取当前定义名称。
            obj_name: re.Match[str] | None = re.search(  # 当前函数声明名称匹配
                r"\bfunction\b(?:\s+\[[^]]+\])?\s+(\w+)",  # Verilog-2001 函数名模式
                str_text,  # 当前 formatter 函数文本
                flags=re.IGNORECASE,  # Verilog 关键字大小写兼容
            )

            # 无法提取函数名时不能构造直接递归证据。
            if obj_name is None:

                # 当前函数保留为适用对象，但不伪造递归命中。
                continue

            # 声明之后再次出现同名调用才属于直接递归。
            obj_recursive_call: object | None = re.search(  # 函数体中的直接递归调用匹配
                rf"\b{re.escape(obj_name.group(1))}\s*\(",  # 当前函数的同名调用模式
                str_text[obj_name.end() :],  # 排除声明自身的文本范围
            )

            # 普通非递归函数不产生 finding。
            if obj_recursive_call is None:

                # 当前函数通过递归检查，继续处理下一定义。
                continue

            # 函数起始行定位定义和递归调用所在块。
            int_line = int(dict_function.get("line_start") or 1)  # 递归函数的一基定义行号

            # finding 保留函数名，便于审查者直接定位调用链。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,  # 递归函数所在 RTL 文件
                    int_line,  # formatter 提供的函数定义行
                    "function 存在直接递归调用。",  # VG106 的用户诊断
                    obj_name.group(1),  # 发生自调用的函数名称
                )
            )

    # 任一直接递归函数都触发 VG106。
    if list_findings:

        # 失败结论保留全部函数证据。
        return failed(*list_findings)

    # 没有递归时仍报告是否实际存在 function。
    return passed(applicable=bool_applicable)

# _task_timing_control 配置 task 专属时序构造模式。
def _task_timing_control(facts: VgFacts) -> VgEvaluation:
    """只在 task 内部出现延时或事件控制时报告。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG133 的确定性执行结论。
    """

    # 延时、事件和 wait 只能在 task 边界内触发本规则。
    return _subprogram_pattern(facts, "tasks", r"(?:#\s*\d+|@\s*\(|\bwait\s*\()", "task 内部包含时序控制。")

# _function_nonblocking 配置 function 专属赋值模式。
def _function_nonblocking(facts: VgFacts) -> VgEvaluation:
    """只在 function 内部出现非阻塞赋值时报告。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG139 的确定性执行结论。
    """

    # 非阻塞操作符只在 function 边界内触发本规则。
    return _subprogram_pattern(facts, "functions", r"<=", "function 内部包含非阻塞赋值。")

# _subprogram_pattern 复用 formatter 子程序边界执行局部扫描。
def _subprogram_pattern(facts: VgFacts, str_collection: str, str_pattern: str, str_message: str) -> VgEvaluation:
    """在 formatter AST 子程序块内执行构造扫描。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        str_collection: module 报告中的 functions 或 tasks 集合名。
        str_pattern: 只在子程序文本内执行的构造正则。
        str_message: 规则命中后的中文诊断。
    返回:
        当前子程序模式的确定性执行结论。
    """

    # findings 保存每个命中子程序的定义位置。
    list_findings: list[VgFinding] = []  # 子程序构造违规证据

    # applicable 表明目标子程序集合至少包含一项。
    bool_applicable = False  # 当前设计是否存在目标子程序

    # module 边界防止不同层级的子程序文本串扰。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # collection 由固定 evaluator 选择，不接受外部任意字段。
        for dict_block in dict_module.get(str_collection, []):

            # 找到目标子程序后规则进入适用状态。
            bool_applicable = True  # 已确认至少一个目标子程序块

            # 可信文本只来自 formatter block 字段。
            str_text = _block_text(dict_block)  # 当前子程序的完整可信文本

            # 没有目标构造的子程序保持通过。
            if re.search(str_pattern, str_text, flags=re.IGNORECASE) is None:

                # 当前块无违规构造，继续扫描下一子程序。
                continue

            # 子程序起始行是 formatter 提供的稳定定位。
            int_line = int(dict_block.get("line_start") or 1)  # 违规子程序的一基定义行

            # 首行证据避免在报告中复制完整函数或 task。
            str_evidence = str_text.splitlines()[0] if str_text else ""  # 子程序声明首行证据

            # finding 绑定当前文件、定义行和目标构造。
            list_findings.append(VgFinding(source_facts.relative_path, int_line, str_message, str_evidence))

    # 任一命中子程序都使当前固定门禁失败。
    if list_findings:

        # 返回全部子程序定义证据。
        return failed(*list_findings)

    # 无违规时保留适用状态供报告审计。
    return passed(applicable=bool_applicable)

# _subprogram_name 提取旧式 Verilog function 或 task 声明名称。
def _subprogram_name(str_text: str, str_kind: str) -> str | None:
    """返回受支持子程序声明中的名称。

    参数:
        str_text: formatter 确认的完整子程序文本。
        str_kind: 固定为 function 或 task 的声明种类。
    返回:
        声明名称；不支持的声明形式返回 None。
    """

    # task 声明在可选 automatic 后直接给出名称。
    if str_kind == "task":

        # 只接纳 formatter 当前支持的旧式 task 头。
        obj_task = re.search(r"\btask\b\s+(?:automatic\s+)?([A-Za-z_]\w*)\s*;", str_text)  # task 名称匹配

        # 匹配失败显式传播未知名称。
        return None if obj_task is None else obj_task.group(1)

    # function 名称可能位于 automatic、signed 和返回区间之后。
    obj_function = re.search(  # 当前 function 声明名称匹配结果
        r"\bfunction\b\s+(?:automatic\s+)?(?:signed\s+)?(?:\[[^]]+\]\s+)?([A-Za-z_]\w*)\s*;",  # 旧式 function 名称模式
        str_text,  # formatter 确认的 function 文本
    )

    # 匹配失败不猜测 ANSI 风格或类型化声明。
    return None if obj_function is None else obj_function.group(1)

# _formal_ports 读取旧式子程序形式端口声明。
def _formal_ports(str_text: str, dict_parameter_values: dict[str, int]) -> list[tuple[str, int | None]]:
    """按源码顺序返回子程序形式端口方向和位宽。

    参数:
        str_text: formatter 确认的 function 或 task 文本。
        dict_parameter_values: 当前 module 的整数 parameter。
    返回:
        每个形式端口的方向与静态位宽列表。
    """

    # formals 保持位置参数调用所依赖的声明顺序。
    list_formals: list[tuple[str, int | None]] = []  # 子程序形式端口合同

    # 每个旧式端口声明独占一条以分号结束的语句。
    str_pattern = r"(?m)^\s*(input|output|inout)\s+(?:(?:reg|wire)\s+)?(?:signed\s+)?(\[[^]]+\])?\s*([^;]+);"  # 形式端口声明

    # 逐条声明展开可能存在的逗号分隔名称。
    for obj_match in re.finditer(str_pattern, str_text):

        # 当前声明方向对同一行所有名称生效。
        str_direction = obj_match.group(1)  # 当前形式端口的声明方向

        # 缺少区间时共享解析器返回标量宽度一。
        int_width = parse_width(str(obj_match.group(2) or ""), dict_parameter_values)  # 形式端口静态位宽

        # 名称列表只接纳简单标识符。
        for str_name in obj_match.group(3).split(","):

            # 去除外围空白后验证标识符形状。
            str_formal_name = str_name.strip()  # 当前形式端口名称

            # 非简单名称超出当前高置信子集，不登记伪端口。
            if re.fullmatch(r"[A-Za-z_]\w*", str_formal_name) is None:

                # 调用侧的数量或未知宽度会使异常声明 fail-closed。
                continue

            # 方向和位宽按声明顺序加入合同。
            list_formals.append((str_direction, int_width))

    # 返回当前子程序的全部受支持形式端口。
    return list_formals

# _local_widths 建立子程序形参和局部变量位宽表。
def _local_widths(str_text: str, dict_parameter_values: dict[str, int]) -> dict[str, int | None]:
    """返回子程序内可识别声明名称及其静态位宽。

    参数:
        str_text: formatter 确认的子程序文本。
        dict_parameter_values: 当前 module 的整数 parameter。
    返回:
        形参和局部变量名称到静态位宽的映射。
    """

    # widths 同时覆盖端口方向声明和局部数据声明。
    dict_widths: dict[str, int | None] = {}  # 子程序局部符号位宽表

    # 声明模式覆盖端口、reg、wire 和 integer 的旧式语法。
    str_pattern = (
        r"(?m)^\s*(?:input|output|inout|reg|wire|integer)\s+"
        r"(?:(?:reg|wire)\s+)?(?:signed\s+)?(\[[^]]+\])?\s*([^;]+);"
    )  # 子程序局部声明模式

    # 每条声明独立展开逗号分隔名称。
    for obj_match in re.finditer(str_pattern, str_text):

        # integer 未显式区间时按 Verilog 整数宽度 32 处理。
        str_declaration = obj_match.group(0).lstrip()  # 当前声明原文

        # 其余无区间声明按标量处理。
        int_width = (  # 当前局部声明的静态位宽
            32  # 无区间 integer 的 Verilog 固定宽度
            if str_declaration.startswith("integer") and obj_match.group(1) is None  # 当前声明是否为默认 integer
            else parse_width(str(obj_match.group(1) or ""), dict_parameter_values)  # 端口或数据声明区间宽度
        )

        # 同一声明中的名称共享类型和位宽。
        for str_name in obj_match.group(2).split(","):

            # 局部名称必须是简单标识符。
            str_local_name = str_name.strip()  # 当前局部符号名称

            # 初始化或复杂声明不进入高置信符号表。
            if re.fullmatch(r"[A-Za-z_]\w*", str_local_name) is None:

                # 复杂局部声明由上层未知路径处理。
                continue

            # 局部作用域名称覆盖 module 同名符号。
            dict_widths[str_local_name] = int_width  # 登记子程序局部位宽

    # 返回当前子程序可用于求宽和所有权判断的符号表。
    return dict_widths

# _block_text 兼容 formatter 的 lines 与 text 两种块表示。
def _block_text(dict_block: dict[str, object]) -> str:
    """统一读取 formatter block 的原始行或文本字段。

    参数:
        dict_block: formatter AST 中的 function 或 task 字典。
    返回:
        保持行序的可信子程序文本。
    """

    # 新版 formatter 优先提供有序 lines 字段。
    list_lines = dict_block.get("lines", [])  # 当前子程序的 formatter 行序

    # 列表字段可以无损恢复子程序多行文本。
    if isinstance(list_lines, list):

        # 行拼接不改变 formatter 已确认的边界。
        return "\n".join(str(str_line) for str_line in list_lines)

    # 旧格式缺少行列表时退回受信任的 text 字段。
    return str(dict_block.get("text") or "")
