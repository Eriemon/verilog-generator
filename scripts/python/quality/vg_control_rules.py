"""实现 always、case、循环和仿真构造相关 RTL VG 门禁。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# re 在 formatter AST 可信文本和结构字段内匹配控制构造。
import re

# Callable 描述固定编号到控制规则函数的路由表。
from typing import Callable

# facts 提供可信 module 文本和结构化 always、声明事实。
from .vg_semantic_facts import VgFacts, iter_trusted_modules

# 实例规则独立负责 VG097 端口连接位宽检查。
from .vg_instance_rules import _connection_port_width_match

# models 统一逐门禁状态和定位证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# reset rules 提供统一的复位、清零和置位名称角色判断。
from .vg_reset_rules import is_reset_control_name

# 共享位宽事实确保实例连接与表达式规则采用同一受限求值语义。
from .vg_value_facts import (
    constant_integer,
    module_parameter_values,
)

# 确定数位的定宽字面量在标识符提取前作为原子常量处理。
DETERMINISTIC_BASED_LITERAL_PATTERN = re.compile(  # Verilog-2001 确定定宽常量
    r"(?<![A-Za-z0-9_$])"
    r"(?:"
    r"\d+'[sS]?[bB][01_]+"
    r"|\d+'[sS]?[oO][0-7_]+"
    r"|\d+'[sS]?[dD][0-9_]+"
    r"|\d+'[sS]?[hH][0-9a-fA-F_]+"
    r")"
    r"(?![A-Za-z0-9_$])",  # 禁止只匹配畸形字面量的合法前缀
)

# evaluate_control_gate 把固定编号路由到控制结构规则实现。
def evaluate_control_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行控制结构规则组中的指定 VG 门禁。

    参数:
        str_gate_id: 当前执行的固定 VG 控制门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前控制结构规则的逐门禁结论。
    """

    # 路由表只包含统一 VG 语义引擎分配给本模块的激活编号。
    dict_evaluators: dict[str, Callable[[VgFacts], VgEvaluation]] = {  # 固定编号到控制规则函数的映射
        "VG079": _comb_blocking,  # 组合块赋值操作符检查
        "VG080": _for_body_nonindex_arithmetic,  # procedural for 循环体非索引算术检查
        "VG081": _repeat_constant_count,  # repeat 常量次数检查
        "VG087": _synth_no_reset_override,  # 综合复位属性覆盖检查
        "VG091": _array_index_simple,  # 数组索引形态检查
        "VG092": _loop_at_least_once,  # 循环至少执行一次检查
        "VG095": _latch_no_gate_primitive,  # 门级锁存器描述检查
        "VG097": _connection_port_width_match,  # 实例端口连接位宽检查
        "VG108": _case_default_not_xz,  # default 未知态检查
        "VG111": _case_has_default,  # case 默认分支完整性检查
        "VG114": _synth_no_full_case_attr,  # full_case 综合指令检查
        "VG117": _sensitivity_separator,  # 敏感列表分隔符检查
        "VG123": _for_constant_bounds,  # for 常量边界检查
        "VG124": _initial_forbidden,  # initial 综合边界检查
        "VG126": _sensitivity_complete_minimal,  # 组合敏感列表精确性检查
        "VG128": _case_kind,  # casex/casez 使用检查
        "VG129": _sequential_nonblocking,  # 时序块赋值操作符检查
        "VG131": _assignment_delay,  # 赋值延时控制检查
        "VG135": _latch_separate_from_comb,  # 锁存与普通组合逻辑分离检查
        "VG143": _simulation_system_tasks,  # 仿真系统任务检查
    }

    # engine 已保证编号属于本模块，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _for_body_nonindex_arithmetic 限制 procedural 循环只更新索引变量。
def _for_body_nonindex_arithmetic(facts: VgFacts) -> VgEvaluation:
    """检查 procedural for 循环是否算术更新非索引变量。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        非索引算术更新的失败证据或适用性结论。
    """

    # findings 汇总非循环变量的算术写入证据。
    list_findings: list[VgFinding] = []  # 非循环变量算术写入证据

    # applicable 区分无循环输入与检查后合规。
    bool_applicable = False  # 是否发现 procedural for 循环

    # 循环模式同时捕获索引、更新目标和主体。
    str_loop_pattern = (  # 单层 procedural 循环及其 begin/end 主体
        r"\bfor\s*\(\s*([A-Za-z_]\w*)\s*=.*?;.*?;\s*"
        r"([A-Za-z_]\w*)\s*=.*?\)\s*begin(?P<body>.*?)\bend\b"
    )

    # 赋值模式只覆盖当前规则能可靠判断的简单语句。
    str_assignment_pattern = r"\b([A-Za-z_]\w*)\s*(?:=|<=)\s*([^;]+);"  # 循环体简单赋值

    # 每个可信 module 独立排除 generate 区域并检查 procedural 循环。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # generate 区域由 elaboration 复制硬件，不属于 procedural datapath 限制。
        str_procedural_text = re.sub(  # 移除 elaboration 专用文本后的 module 内容
            r"\bgenerate\b.*?\bendgenerate\b",  # elaboration 区域边界模式
            "",  # 删除命中区域而不引入替代文本
            str_module_text,  # 当前 formatter 可信 module 文本
            flags=re.DOTALL | re.IGNORECASE,  # 跨行且忽略关键字大小写
        )

        # 逐个循环核对头部更新目标和循环体赋值。
        for obj_loop in re.finditer(str_loop_pattern, str_procedural_text, flags=re.DOTALL | re.IGNORECASE):

            # 当前 module 已提供可审查的 procedural 循环。
            bool_applicable = True  # 已发现规则适用的 procedural 循环

            # 捕获组一是循环索引。
            str_loop_index = obj_loop.group(1)  # 当前循环索引变量

            # 捕获组二是循环头的更新目标。
            str_update_target = obj_loop.group(2)  # 当前循环更新目标

            # 循环头必须更新声明的同一索引变量。
            if str_update_target != str_loop_index:

                # 循环头位置映射回原始 module 的一基行号。
                int_line = int_base_line + str_procedural_text.count("\n", 0, obj_loop.start())  # 循环头一基行号

                # 记录循环头破坏索引约束的确定证据。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,
                        int_line,
                        "for 循环头更新了非循环索引变量。",
                        obj_loop.group(0).split("begin", 1)[0].strip(),
                        "BLOCKER",
                    )
                )

            # 循环体文本限定后续赋值扫描范围。
            # 命名捕获组提供 formatter 边界内的循环体文本。
            str_body = obj_loop.group("body")  # formatter 边界内的循环体文本

            # 每条简单赋值独立判断目标角色和算术运算。
            for obj_assignment in re.finditer(str_assignment_pattern, str_body):

                # 两个捕获组分别表示写入目标和右值表达式。
                str_lvalue, str_expression = obj_assignment.groups()  # 当前写入目标和右值

                # 索引更新或非算术赋值不属于违规对象。
                if str_lvalue == str_loop_index or re.search(r"[+\-*/%]", str_expression) is None:

                    # 继续检查同一循环体中的其他赋值。
                    continue

                # 把循环体内偏移换算为原 module 的一基源码行号。
                # 循环体相对偏移叠加主体起点得到 module 内位置。
                int_offset = obj_loop.start("body") + obj_assignment.start()  # 当前赋值的 module 文本偏移

                # module 内位置用于计算稳定的一基行号。
                int_line = int_base_line + str_procedural_text.count("\n", 0, int_offset)  # 当前赋值一基行号

                # 非索引变量的算术更新作为 warning 证据进入统一报告。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,
                        int_line,
                        "procedural for 循环体对非循环变量执行了算术更新。",
                        obj_assignment.group(0).strip(),
                        "WARNING",
                    )
                )

    # 任一确定证据都使本门禁失败。
    if list_findings:

        # 保留全部循环证据，便于一次修复多个位置。
        return failed(*list_findings)

    # 没有违规时报告规则是否实际检查过 procedural 循环。
    return passed(applicable=bool_applicable)

# _synth_no_reset_override 禁止综合指令覆盖 RTL 复位语义。
def _synth_no_reset_override(facts: VgFacts) -> VgEvaluation:
    """检查综合属性是否显式禁用、移除或覆盖复位行为。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        复位属性覆盖规则的确定失败或适用性结论。
    """

    # findings 保存可信 module 范围内的复位覆盖指令。
    list_findings: list[VgFinding] = []  # 综合复位覆盖证据

    # 出现复位信号或复位覆盖指令时规则才具有适用性。
    bool_applicable = False  # 当前目标是否包含复位语义

    # 模式只接受明确的 reset_override 或带工具前缀的复位修改指令。
    str_override_pattern = (
        r"\breset_override\b"
        r"|\b(?:synthesis|synopsys|altera_attribute|xilinx)\b[^\n]*"
        r"(?:reset|clear|preset)[^\n]*(?:disable|remove|override|force)"
    )  # 明确改变复位属性的综合指令模式

    # 每个 formatter 确认的 module 独立定位证据。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 端口和内部声明提供不受注释文本影响的控制名称事实。
        tuple_declared_names = tuple(  # 当前 module 的端口与内部声明名称
            str(dict_item.get("name") or "")  # 当前结构化声明名称
            for str_collection in ("ports", "decls")  # 端口与内部声明集合
            for dict_item in dict_module.get(str_collection, []) or []  # 当前声明记录
            if str(dict_item.get("name") or "")  # 忽略缺少常量标识符的记录
        )

        # always 的 reset 字段补充 formatter 已确认的控制身份。
        tuple_always_resets = tuple(  # 当前 module 时序块的复位字段
            str(dict_always.get("reset") or "")  # formatter 确认的复位名称
            for dict_always in dict_module.get("always", []) or []  # 当前过程块记录
            if str(dict_always.get("reset") or "")  # 排除没有复位的过程块
        )

        # 任一结构化名称命中共享角色模式即使规则进入适用状态。
        if any(
            is_reset_control_name(str_name)
            for str_name in tuple_declared_names + tuple_always_resets
        ):

            # 标记含复位信号的 module 已进入规则审查范围。
            bool_applicable = True  # 当前 module 含有可审查的复位语义

        # 明确工具覆盖指令必须逐条报告。
        for obj_match in re.finditer(str_override_pattern, str_module_text, flags=re.IGNORECASE):

            # 覆盖指令即使没有显式复位信号也应触发本规则。
            bool_applicable = True  # 覆盖指令本身也使规则适用

            # 把 module 内偏移转换为报告使用的一基源码行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 指令所在一基行号

            # 记录覆盖指令的文件、行号和原始证据文本。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "禁止使用综合工具覆盖 RTL 复位属性。",
                    obj_match.group(0).strip(),
                )
            )

    # 确定覆盖指令优先返回失败。
    return failed(*list_findings) if list_findings else passed(applicable=bool_applicable)

# _synth_no_full_case_attr 禁止以 full_case 指令替代 RTL 默认分支。
def _synth_no_full_case_attr(facts: VgFacts) -> VgEvaluation:
    """检查 formatter 可信 module 中的 full_case 综合指令。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        full_case 指令的确定失败或真实 case 结构的适用性结论。
    """

    # findings 保存每条 full_case 指令的精确位置。
    list_findings: list[VgFinding] = []  # full_case 指令证据

    # applicable 只在可信 module 中发现真实 case 结构时置位。
    bool_applicable = False  # full_case 检查是否遇到真实 case 语句

    # 逐 module 扫描，避免顶层噪声形成误报。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 汇总本轮 module 是否包含可审查的 case 控制结构。
        bool_applicable = bool_applicable or bool(  # 累积此前与当前 module 的 case 适用性
            re.search(r"\bcase[xz]?\s*\(", str_module_text, flags=re.IGNORECASE)  # 当前 module 的 case 结构命中
        )  # 当前及既有 module 的 case 适用性

        # full_case 关键字无论位于注释还是属性中都属于工具指令。
        for obj_match in re.finditer(r"\bfull_case\b", str_module_text, flags=re.IGNORECASE):

            # full_case 的 module 内偏移用于形成精确源码定位。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # full_case 工具指令的一基行号

            # 记录工具指令位置，供失败报告直接指向源码。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "避免使用 full_case 综合属性。",
                    obj_match.group(0),
                )
            )

    # 存在指令时确定失败，否则对真实 case 结构报告适用通过。
    return failed(*list_findings) if list_findings else passed(applicable=bool_applicable)

# _repeat_constant_count 要求 repeat 次数只依赖字面量或声明常量。
def _repeat_constant_count(facts: VgFacts) -> VgEvaluation:
    """检查 repeat 次数表达式是否保持综合期常量。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        变量次数对应的失败结论，或常量 repeat 的适用通过结论。
    """

    # findings 保存所有引用运行时变量的 repeat 头部。
    list_findings: list[VgFinding] = []  # repeat 变量次数证据

    # applicable 区分没有 repeat 与全部 repeat 均使用常量。
    bool_applicable = False  # 是否发现 repeat 控制结构

    # 每个可信 module 独立定位 repeat 头部。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # repeat 只能引用当前 module 声明的综合期常量。
        set_constants = _constant_names(dict_module)  # 当前 module 的常量名称

        # 捕获 repeat 括号内的次数表达式。
        for obj_match in re.finditer(r"\brepeat\s*\(\s*([^)]*?)\s*\)", str_module_text, flags=re.IGNORECASE):

            # 任何 repeat 结构都使当前规则具有适用性。
            bool_applicable = True  # 至少一次规则正在审查当前 repeat

            # 保留原始次数表达式用于常量身份判断。
            str_count_expression = obj_match.group(1).strip()  # repeat 次数表达式文本

            # 字面量和已声明常量满足本规则。
            if _constant_expression(str_count_expression, set_constants):

                # 当前 repeat 不引用运行时变量，继续检查其余语句。
                continue

            # 命中偏移转换为源码一基行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 变量 repeat 头部行号

            # 记录运行时变量参与 repeat 次数计算的确定证据。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "repeat 次数必须使用字面量或声明常量。",
                    obj_match.group(0),
                )
            )

    # 任一变量次数都形成确定失败，否则返回适用性结论。
    return failed(*list_findings) if list_findings else passed(applicable=bool_applicable)

# _array_index_simple 限制方括号索引为单一信号或数字。
def _array_index_simple(facts: VgFacts) -> VgEvaluation:
    """检查数组或向量索引是否为简单标识符或数字。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        复杂索引对应的失败结论，或简单索引的适用通过结论。
    """

    # findings 保存包含算术或组合表达式的索引访问。
    list_findings: list[VgFinding] = []  # 复杂方括号索引证据

    # applicable 记录可信 module 中是否存在索引访问。
    bool_applicable = False  # 是否发现数组或向量索引

    # 逐 module 扫描表达式中的单层方括号访问。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 声明宽度由冒号排除，剩余方括号按索引访问处理。
        for obj_match in re.finditer(r"\b\w+\s*\[\s*([^\]:]+?)\s*\]", str_module_text):

            # 发现索引访问后，本规则进入实际执行路径。
            bool_applicable = True  # 当前 module 含有可审查索引

            # 提取索引内部文本，忽略外围空白。
            str_index_expression = obj_match.group(1).strip()  # 当前方括号索引表达式

            # 单个标识符或十进制数字属于简单索引。
            if re.fullmatch(r"(?:[A-Za-z_]\w*|\d+)", str_index_expression):

                # 当前索引形态简单，继续检查其他访问。
                continue

            # 复杂索引的匹配起点提供稳定源码定位。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 复杂索引访问行号

            # 记录完整访问片段，便于拆分索引计算与数组读取。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "数组索引应使用简单信号，避免直接嵌入表达式。",
                    obj_match.group(0),
                )
            )

    # 复杂索引优先失败；没有违规时保留真实适用性。
    return failed(*list_findings) if list_findings else passed(applicable=bool_applicable)

# _loop_at_least_once 拒绝静态可证明为零次或负次数的 repeat。
def _loop_at_least_once(facts: VgFacts) -> VgEvaluation:
    """检查静态可求值的 repeat 是否至少执行一次。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        零次循环的失败结论、未知次数的不确定结论或适用通过结论。
    """

    # findings 保存静态次数小于一的 repeat 语句。
    list_findings: list[VgFinding] = []  # 不执行 repeat 的定位证据

    # applicable 记录是否发现 repeat 控制结构。
    bool_applicable = False  # 当前目标是否包含 repeat

    # unknown 防止复杂或运行时次数被误报为确定通过。
    bool_unknown = False  # 是否存在不可静态求值的 repeat 次数

    # 每个 module 使用自身 parameter 值解析 repeat 次数。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 参数整数表与位宽规则共享同一受限求值语义。
        dict_parameter_values = module_parameter_values(dict_module)  # 当前 module 的整数参数表

        # 捕获所有 repeat 次数表达式。
        for obj_match in re.finditer(r"\brepeat\s*\(\s*([^)]*?)\s*\)", str_module_text, flags=re.IGNORECASE):

            # 发现 repeat 后，本规则对当前输入适用。
            bool_applicable = True  # 当前 module 含有 repeat 语句

            # 受限求值器解析数字或简单参数表达式。
            int_repeat_count = constant_integer(obj_match.group(1), dict_parameter_values)  # repeat 的静态执行次数

            # 运行时变量或复杂表达式无法证明至少执行一次。
            if int_repeat_count is None:

                # 保留未知状态，由 VG081 另行报告变量次数建议。
                bool_unknown = True  # 当前 repeat 次数无法静态确认

                # 当前语句不能安全比较次数，继续收集其他确定违规。
                continue

            # 正次数满足至少执行一次的合同。
            if int_repeat_count >= 1:

                # 当前 repeat 已证明会进入循环主体。
                continue

            # 非正次数的源码位置用于确定失败报告。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 零次 repeat 的源码行号

            # 记录可证明不会执行循环主体的 repeat。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "循环次数必须静态保证至少执行一次。",
                    obj_match.group(0),
                )
            )

    # 确定的零次循环优先于同一目标中的未知次数。
    if list_findings:

        # 返回全部可证明不执行的 repeat 证据。
        return failed(*list_findings)

    # 仍有未知次数时不得形成确定通过。
    if bool_unknown:

        # 调用方需要先把次数约束为可静态求值表达式。
        return inconclusive("存在无法静态证明至少执行一次的 repeat 次数。")

    # 全部 repeat 均为正次数，或目标不含 repeat。
    return passed(applicable=bool_applicable)

# _latch_no_gate_primitive 识别交叉反馈 nand/nor 门锁存器。
def _latch_no_gate_primitive(facts: VgFacts) -> VgEvaluation:
    """检查是否使用交叉反馈基本门描述锁存器。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        门级锁存器的失败结论，或过程式锁存结构的适用通过结论。
    """

    # findings 保存成对交叉反馈 nand/nor 原语的位置。
    list_findings: list[VgFinding] = []  # 基本门锁存器证据

    # applicable 同时覆盖门级和过程式锁存描述。
    bool_applicable = False  # 是否发现可识别的锁存结构

    # 可信 module 文本用于恢复原语连接关系与行号。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 每个匹配项保存门类型、端口文本和匹配位置。
        list_primitives = list(  # 当前 module 的 nand/nor 原语列表
            re.finditer(  # 搜索当前 module 的 nand/nor 原语
                r"\b(?:nand|nor)\s*(?:\w+\s*)?\(([^;]+)\)\s*;",  # 带可选实例名的门原语形态
                str_module_text,  # formatter 确认的当前 module 原文
                flags=re.IGNORECASE,  # Verilog 原语关键字按大小写无关匹配
            )
        )

        # 不完整 if 赋值表示存在过程式锁存语义，可形成正例适用性。
        bool_applicable = bool_applicable or bool(  # 累积过程式锁存结构适用性
            re.search(  # 搜索当前 module 的过程式锁存候选
                r"\balways\s*@\s*\([^)]*\).*?\bif\s*\([^)]*\)\s*\w+\s*=",  # 不完整条件赋值形态
                str_module_text,  # 当前 module 的可信源码文本
                flags=re.IGNORECASE | re.DOTALL,  # 允许过程块跨越多行
            )  # 当前 module 的过程式锁存候选
        )  # 当前及既有 module 的锁存适用性

        # 每对基本门只比较一次，避免重复 finding。
        for int_left_index, obj_left in enumerate(list_primitives):

            # 左侧门的首端口是输出，其余端口是输入。
            list_left_ports = [str_port.strip() for str_port in obj_left.group(1).split(",")]  # 左侧原语端口列表

            # 端口不足时不能证明交叉反馈关系。
            if len(list_left_ports) < 2:

                # 跳过无法形成锁存结构的当前原语。
                continue

            # 只访问左侧之后的门，保持成对比较唯一。
            for obj_right in list_primitives[int_left_index + 1 :]:

                # 右侧门同样按输出在前的原语端口顺序解析。
                list_right_ports = [str_port.strip() for str_port in obj_right.group(1).split(",")]  # 右侧原语端口列表

                # 右侧端口不足时无法构成交叉反馈对。
                if len(list_right_ports) < 2:

                    # 当前右侧门不参与锁存器判定。
                    continue

                # 两个输出互相出现在对方输入端时形成交叉反馈。
                bool_cross_coupled = (  # 当前两门是否构成交叉反馈
                    list_left_ports[0] in list_right_ports[1:]  # 左门输出反馈到右门输入
                    and list_right_ports[0] in list_left_ports[1:]  # 右门输出反馈到左门输入
                )

                # 非交叉连接的普通逻辑门不属于本规则。
                if not bool_cross_coupled:

                    # 继续比较左侧门与其他候选原语。
                    continue

                # 门级交叉反馈使规则确定适用并违规。
                bool_applicable = True  # 当前 module 含有基本门锁存器

                # 以原语对首行作为稳定定位。
                int_line = int_base_line + str_module_text.count("\n", 0, obj_left.start())  # 交叉反馈门对起始行号

                # finding 合并两条门语句作为完整反馈证据。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,
                        int_line,
                        "禁止使用交叉反馈基本门描述锁存器。",
                        f"{obj_left.group(0)} {obj_right.group(0)}",
                    )
                )

    # 交叉反馈门对触发失败，过程式锁存描述则适用通过。
    return failed(*list_findings) if list_findings else passed(applicable=bool_applicable)

# _latch_separate_from_comb 要求锁存赋值块不混入独立组合输出。
def _latch_separate_from_comb(facts: VgFacts) -> VgEvaluation:
    """检查锁存过程块是否混合驱动其他组合逻辑目标。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        混合职责过程块的失败结论，或独立锁存块的适用通过结论。
    """

    # findings 保存同时驱动锁存目标与普通组合目标的 always 块。
    list_findings: list[VgFinding] = []  # 锁存与组合逻辑混合证据

    # applicable 只对可识别的不完整 if 锁存结构置位。
    bool_applicable = False  # 是否发现过程式锁存候选

    # 逐 module 检查 formatter 可信范围内的组合 always 块。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 受限模式只处理 begin/end 包裹的组合过程块。
        for obj_always in re.finditer(
            r"\balways\s*@\s*\(\s*\*\s*\)\s*begin(.*?)\bend\b",
            str_module_text,
            flags=re.IGNORECASE | re.DOTALL,
        ):

            # 当前过程块正文用于恢复条件赋值与全部驱动目标。
            str_body = obj_always.group(1)  # 组合 always 的正文文本

            # 没有终止 else 的单分支赋值是本规则可识别的锁存候选。
            obj_latch_assignment = re.search(r"\bif\s*\([^)]*\)\s*(\w+)\s*=", str_body, flags=re.IGNORECASE)  # 条件锁存赋值匹配结果

            # 不是可识别锁存形态时不进入职责分离检查。
            if obj_latch_assignment is None or re.search(r"\belse\b", str_body, flags=re.IGNORECASE):

                # 当前过程块没有不完整条件赋值语义。
                continue

            # 已识别锁存候选后，本规则对当前目标适用。
            bool_applicable = True  # 当前过程块含有锁存赋值

            # 条件赋值目标是允许留在锁存块中的唯一驱动对象。
            str_latch_target = obj_latch_assignment.group(1)  # 当前锁存器输出目标

            # 收集过程块中所有简单赋值左值以识别额外组合职责。
            set_assignment_targets = set(re.findall(r"\b(\w+)\s*=", str_body))  # 当前过程块驱动目标集合

            # 只有锁存目标时已经满足职责分离要求。
            if set_assignment_targets <= {str_latch_target}:

                # 当前锁存过程块没有混入独立组合输出。
                continue

            # always 匹配起点换算为源码行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_always.start())  # 混合职责 always 起始行号

            # 记录整个过程块首行和额外驱动目标。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "锁存器应与其他组合逻辑分开描述。",
                    obj_always.group(0).splitlines()[0],
                )
            )

    # 混合职责块失败，否则返回真实锁存候选适用性。
    return failed(*list_findings) if list_findings else passed(applicable=bool_applicable)

# _comb_blocking 要求无边沿事件的 always 使用阻塞赋值。
def _comb_blocking(facts: VgFacts) -> VgEvaluation:
    """检查组合 always 是否错误使用非阻塞赋值。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG079 的确定性执行结论。
    """

    # combinational=True 只选择不含 posedge/negedge 的过程块。
    return _always_assignment_gate(
        facts,
        combinational=True,
        forbidden_operator="<=",
        message="组合逻辑必须使用阻塞赋值。",
    )

# _sequential_nonblocking 要求边沿触发 always 使用非阻塞赋值。
def _sequential_nonblocking(facts: VgFacts) -> VgEvaluation:
    """检查时序 always 是否错误使用阻塞赋值。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG129 的确定性执行结论。
    """

    # combinational=False 只选择含 posedge/negedge 的过程块。
    return _always_assignment_gate(
        facts,
        combinational=False,
        forbidden_operator="=",
        message="时序逻辑必须使用非阻塞赋值。",
    )

# _case_has_default 检查每个 case 块的默认覆盖分支。
def _case_has_default(facts: VgFacts) -> VgEvaluation:
    """检查每个 case 块是否包含 default 分支。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG111 的确定性执行结论。
    """

    # findings 保存所有缺少 default 的独立 case 块。
    list_findings: list[VgFinding] = []  # 缺失默认分支的定位证据

    # applicable 区分没有 case 和所有 case 均完整。
    bool_applicable = False  # default 覆盖规则尚未遇到可审查 case 块

    # case 匹配只在 formatter 已确认的 module 文本内执行。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # DOTALL 让 case 正文跨行匹配到对应 endcase。
        for obj_match in re.finditer(
            r"\bcase[xz]?\s*\([^)]*\)(.*?)\bendcase\b",
            str_module_text,
            flags=re.IGNORECASE | re.DOTALL,
        ):

            # 发现 case 即标记本规则适用。
            bool_applicable = True  # 当前 module 含有 case 块

            # 正文存在 default 标签时当前 case 满足规则。
            if re.search(r"\bdefault\s*:", obj_match.group(1), flags=re.IGNORECASE) is not None:

                # 继续检查同一 module 中的其他 case。
                continue

            # 匹配起点结合 module 基线得到 case 一基行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 缺少 default 的 case 行号

            # finding 仅保留 case 首行，避免报告包含大段正文。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,  # 缺少默认分支的 RTL 文件
                    int_line,  # case 关键字所在一基行号
                    "case 语句缺少 default 分支。",  # VG111 诊断文本
                    obj_match.group(0).splitlines()[0],  # case 首行证据
                )
            )

    # 任一 case 缺失 default 都使门禁失败。
    if list_findings:

        # 返回全部缺失位置便于一次修复。
        return failed(*list_findings)

    # 没有 case 时不适用，存在且完整时适用通过。
    return passed(applicable=bool_applicable)

# _case_default_not_xz 禁止 default 分支驱动未知态字面量。
def _case_default_not_xz(facts: VgFacts) -> VgEvaluation:
    """检查 case default 分支是否驱动 X/Z。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG108 的确定性执行结论。
    """

    # 模式限定 default 标签之后同一语句内的 X/Z 字面量。
    return _trusted_pattern_gate(
        facts,
        r"\bdefault\s*:[^;\n]*(?:\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]*[xXzZ][0-9a-fA-F_xXzZ?]*)",
        "case default 分支驱动了 X/Z。",
    )

# _sensitivity_separator 拒绝竖线形式的敏感列表分隔符。
def _sensitivity_separator(facts: VgFacts) -> VgEvaluation:
    """检查敏感列表是否使用非法竖线分隔符。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG117 的确定性执行结论。
    """

    # 模式同时识别单竖线和逻辑或，合法 or 关键字不会命中。
    return _trusted_pattern_gate(
        facts,
        r"\balways\s*@\s*\([^)]*(?:\|\||(?<!\|)\|(?!\|))[^)]*\)",
        "敏感列表使用了非法竖线分隔符。",
    )

# _for_constant_bounds 检查 for 三段式的可综合常量边界。
def _for_constant_bounds(facts: VgFacts) -> VgEvaluation:
    """检查 for 初始化、边界和更新是否为常量可综合形式。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG123 的确定性执行结论。
    """

    # findings 保存初始化、边界或步进不受支持的循环。
    list_findings: list[VgFinding] = []  # 不可综合 for 循环证据

    # applicable 区分没有目标 for 和已完成检查。
    bool_applicable = False  # 是否发现受支持形状的 for 语句

    # 只在可信 module 文本内匹配标准三段式 for。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 三段式循环只能引用当前 module 声明的 parameter 或 localparam。
        set_constants = _constant_names(dict_module)  # 当前 module 的循环常量集合

        # 捕获循环变量、初值、边界和值更新表达式。
        for obj_match in re.finditer(
            r"\bfor\s*\(\s*(\w+)\s*=\s*([^;]+);\s*\1\s*[<>]=?\s*([^;]+);\s*([^)]*)\)",
            str_module_text,
            flags=re.IGNORECASE,
        ):

            # 发现标准 for 形状即标记本规则适用。
            bool_applicable = True  # 当前 module 含有目标 for 语句

            # 第一捕获组是循环索引变量。
            str_index = obj_match.group(1)  # 当前 for 的索引名称

            # 初值必须只由数字或已声明常量组成。
            str_start = obj_match.group(2).strip()  # 当前 for 的初始化表达式

            # 边界必须只由数字或已声明常量组成。
            str_bound = obj_match.group(3).strip()  # 当前 for 的比较边界表达式

            # 更新仅允许索引加减确定十进制步长。
            str_update = obj_match.group(4).strip()  # 当前 for 的索引更新表达式

            # 独立布尔值便于报告和调试三段式条件。
            bool_constant_start = _constant_expression(str_start, set_constants)  # 初值是否为常量表达式

            # 边界允许复用 parameter/localparam 名称。
            bool_constant_bound = _constant_expression(str_bound, set_constants)  # 边界是否为常量表达式

            # 更新必须保持同一索引并使用确定整数步长。
            bool_valid_update = re.fullmatch(  # 更新表达式是否为受支持形式
                rf"{re.escape(str_index)}\s*=\s*{re.escape(str_index)}\s*[+-]\s*\d+|"
                rf"{re.escape(str_index)}\s*[+-]=\s*\d+",
                str_update,  # 参与步进模式验证的原始更新文本
            ) is not None

            # 三项中任一不满足即形成确定违规。
            if not (bool_constant_start and bool_constant_bound and bool_valid_update):

                # 循环头部偏移与当前模块起点合成文件行号。
                int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 不合规 for 所在行

                # finding 保留完整 for 头部作为修复证据。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,  # 不合规循环所在 RTL 文件
                        int_line,  # 帮助用户定位循环头部的一基行号
                        "for 循环边界或更新不是常量可综合形式。",  # 指示初值、边界或步进需改为常量形式
                        obj_match.group(0),  # 原始 for 头部
                    )
                )

    # 任一不合规循环都使门禁失败。
    if list_findings:

        # 返回全部 for 证据便于一次修复。
        return failed(*list_findings)

    # 没有目标循环时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _initial_forbidden 禁止设计 RTL 中的 initial 仿真构造。
def _initial_forbidden(facts: VgFacts) -> VgEvaluation:
    """检查设计 RTL 是否包含 initial 块。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG124 的确定性执行结论。
    """

    # 单词边界避免把 initial 当作标识符子串。
    return _trusted_pattern_gate(facts, r"\binitial\b", "设计 RTL 中出现 initial 块。")

# _case_kind 禁止会放宽未知态匹配的 casex 和 casez。
def _case_kind(facts: VgFacts) -> VgEvaluation:
    """检查设计 RTL 是否使用 casex 或 casez。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG128 的确定性执行结论。
    """

    # case 本身不命中，只有带 x/z 后缀的形式触发。
    return _trusted_pattern_gate(facts, r"\bcase[xz]\s*\(", "设计 RTL 使用了 casex/casez。")

# _assignment_delay 禁止连续和过程赋值中的延时控制。
def _assignment_delay(facts: VgFacts) -> VgEvaluation:
    """检查赋值语句是否包含延时控制。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG131 的确定性执行结论。
    """

    # 模式覆盖 assign #delay 和赋值目标前的过程延时。
    return _trusted_pattern_gate(
        facts,
        r"(?:\bassign\s+)?#\s*\(?[^;\n]*\)?\s*\w+\s*(?:<=|=)|\bassign\s*#",
        "赋值语句包含延时控制。",
    )

# _simulation_system_tasks 禁止设计 RTL 中的典型仿真系统任务。
def _simulation_system_tasks(facts: VgFacts) -> VgEvaluation:
    """检查设计 RTL 是否包含仿真系统任务。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG143 的确定性执行结论。
    """

    # 第一阶段固定覆盖 display、finish 和 stop 三类任务。
    return _trusted_pattern_gate(facts, r"\$(?:display|finish|stop)\b", "设计 RTL 中出现仿真系统任务。")

# _sensitivity_complete_minimal 比较显式列表与正文真实读取集合。
def _sensitivity_complete_minimal(facts: VgFacts) -> VgEvaluation:
    """检查显式组合敏感列表是否完整且无冗余。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        VG126 的通过、失败或不确定结论。
    """

    # findings 保存敏感列表集合与读取集合不相等的过程块。
    list_findings: list[VgFinding] = []  # 组合敏感列表遗漏或冗余证据

    # applicable 只对显式且非边沿触发的 always 生效。
    bool_applicable = False  # 是否发现需要集合比较的组合 always

    # 结构化 always 事实比跨行正则更可靠。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 已知信号集合过滤关键字、局部变量和其他文本标识符。
        set_known_signals = _known_signals(dict_module)  # 当前 module 的端口与内部信号名

        # formatter 为每个过程块提供 header、lines 和 targets。
        for dict_always in dict_module.get("always", []):

            # header 用于识别边沿、通配符和显式敏感列表。
            str_header = str(dict_always.get("header") or "")  # 当前 always 头部文本

            # posedge/negedge 过程块属于时序逻辑，不进入组合集合比较。
            bool_edge_triggered = re.search(  # 当前过程块是否由边沿事件触发
                r"\b(?:posedge|negedge)\b",  # 时钟或复位边沿关键字模式
                str_header,  # 组合敏感列表分类使用的 always 头部
                flags=re.IGNORECASE,  # Verilog 边沿关键字按大小写不敏感识别
            ) is not None

            # 边沿触发或 @* 已由语言机制管理敏感性。
            if bool_edge_triggered or "*" in str_header:

                # 当前过程块不属于显式组合敏感列表规则。
                continue

            # 剩余无边沿 always 视为组合过程块。
            bool_applicable = True  # 当前 module 含显式组合敏感列表

            # lines 拼接后用于提取正文中的已知信号标识符。
            str_lines = "\n".join(  # 当前 always 的完整正文文本
                str(str_line)  # formatter 单行文本归一化
                for str_line in dict_always.get("lines", [])  # 按原顺序遍历过程块源码行
            )

            # header 中括号内容是显式敏感列表。
            obj_sensitivity: re.Match[str] | None = re.search(  # 显式敏感列表匹配结果
                r"@\s*\(([^)]*)\)",  # 捕获 @ 括号内部的显式列表文本
                str_header,  # 待解析的组合 always 头部
            )

            # formatter 缺少可解析 header 时不能伪装成通过。
            if obj_sensitivity is None:

                # inconclusive 明确要求修复解析事实或改用 @*。
                return inconclusive("formatter AST 未提供可解析的组合敏感列表。")

            # or 是分隔关键字，不属于信号集合。
            set_sensitivity = set(re.findall(r"\b[A-Za-z_]\w*\b", obj_sensitivity.group(1))) - {"or"}  # 显式敏感信号集合

            # targets 是正文写入信号，不应计为读取依赖。
            set_targets = {  # 当前 always 的写入目标集合
                str(str_item)  # 归一化过程块目标名
                for str_item in dict_always.get("targets", [])  # formatter 识别的全部左值
            }

            # 读取集合只保留 module 已知信号并扣除写入目标。
            set_reads = (  # 当前 always 正文实际读取的信号集合
                set(re.findall(r"\b[A-Za-z_]\w*\b", str_lines)) & set_known_signals  # 正文标识符与 module 信号交集
            ) - set_targets

            # 完整且最小要求两个集合精确相等。
            if set_sensitivity != set_reads:

                # formatter 提供过程块起始行作为稳定定位。
                int_line = int(dict_always.get("line_start") or 1)  # 敏感列表违规的一基行号

                # 排序集合保证报告证据确定性。
                str_evidence = f"sensitivity={sorted(set_sensitivity)} reads={sorted(set_reads)}"  # 声明集合与读取集合对比

                # finding 让用户直接看到遗漏和冗余项。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,  # 敏感列表违规所在 RTL 文件
                        int_line,  # always 头部的一基行号
                        "组合敏感列表存在遗漏或冗余。",  # 提示显式列表必须精确等于正文读取集合
                        str_evidence,  # 排序后的集合差异证据
                    )
                )

    # 任一集合不相等都使门禁失败。
    if list_findings:

        # 返回所有显式组合列表问题。
        return failed(*list_findings)

    # 没有目标块时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _always_assignment_gate 根据过程块类型检查禁止的赋值操作符。
def _always_assignment_gate(
    facts: VgFacts,
    *,
    combinational: bool,
    forbidden_operator: str,
    message: str,
) -> VgEvaluation:
    """在 formatter AST 已识别的 always 范围内检查赋值操作符。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        combinational: True 选择组合块，False 选择时序块。
        forbidden_operator: 当前过程块类型禁止的赋值操作符。
        message: 命中时写入 finding 的诊断文本。
    返回:
        VG079 或 VG129 的确定性执行结论。
    """

    # findings 保存过程块内每个禁止操作符使用点。
    list_findings: list[VgFinding] = []  # 赋值操作符违规证据

    # applicable 区分没有目标过程块和目标块全部合规。
    bool_applicable = False  # 是否发现当前类型的 always 过程块

    # 过程块分类和正文来自 formatter AST。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 每个 always 独立判断组合或时序属性。
        for dict_always in dict_module.get("always", []):

            # 操作符规则借助 header 把过程块分为组合和时序两类。
            str_header = str(dict_always.get("header") or "")  # 赋值操作符分类使用的 always 头部

            # 无 posedge/negedge 的显式列表和 @* 都按组合块处理。
            bool_is_comb = re.search(  # 当前过程块是否属于组合逻辑
                r"\b(?:posedge|negedge)\b",  # 过程块类型判定使用的边沿模式
                str_header,  # 赋值操作符规则检查的 always 头部
                flags=re.IGNORECASE,  # 组合与时序分类忽略关键字大小写差异
            ) is None

            # 跳过与当前规则目标类型不一致的过程块。
            if bool_is_comb != combinational:

                # 另一条固定规则会检查相反类型。
                continue

            # 发现目标过程块即标记规则适用。
            bool_applicable = True  # 当前 module 含待检查的过程块类型

            # 操作符扫描需要保留正文换行以回算违规行号。
            str_lines = "\n".join(  # 赋值操作符匹配使用的过程块正文
                str(str_line)  # 转换 formatter 保存的单行值
                for str_line in dict_always.get("lines", [])  # 保留操作符使用点的源码顺序
            )

            # 非阻塞和阻塞赋值需要不同的负向断言边界。
            str_pattern = (  # 当前规则禁止的赋值操作符模式
                r"\b\w+(?:\[[^]]+\])?\s*<=\s*"  # 组合块禁止的非阻塞赋值模式
                if forbidden_operator == "<="  # VG079 选择非阻塞操作符
                else r"\b\w+(?:\[[^]]+\])?\s*(?<![<>=!])=(?!=)"  # VG129 选择阻塞赋值模式
            )

            # 每个禁止操作符使用点都形成独立 finding。
            for obj_match in re.finditer(str_pattern, str_lines):

                # 过程块起始行结合正文偏移得到一基行号。
                int_line = int(dict_always.get("line_start") or 1) + str_lines.count("\n", 0, obj_match.start())  # 操作符违规行号

                # finding 保留目标和操作符片段。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,  # 操作符违规所在 RTL 文件
                        int_line,  # 违规赋值的一基行号
                        message,  # 组合或时序规则诊断文本
                        obj_match.group(0).strip(),  # 违规目标与操作符片段
                    )
                )

    # 任一操作符违规都使对应固定门禁失败。
    if list_findings:

        # 返回全部过程块赋值问题。
        return failed(*list_findings)

    # 对应过程块不存在时标为不适用，存在且操作符合规时通过。
    return passed(applicable=bool_applicable)

# _trusted_pattern_gate 在可信 module 边界内执行文本型控制规则。
def _trusted_pattern_gate(facts: VgFacts, str_pattern: str, str_message: str) -> VgEvaluation:
    """扫描指定控制构造并生成精确行号证据。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        str_pattern: 当前固定规则的正则模式。
        str_message: 命中时写入 finding 的诊断文本。
    返回:
        包含全部命中证据的失败结论或不适用通过结论。
    """

    # findings 保存可信 module 中的全部正则命中。
    list_findings: list[VgFinding] = []  # 当前文本规则的违规证据

    # module 文本排除 formatter 无法确认的顶层噪声。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 多行和大小写选项覆盖常见 Verilog 书写差异。
        for obj_match in re.finditer(str_pattern, str_module_text, flags=re.IGNORECASE | re.MULTILINE):

            # 匹配偏移结合 module 基线得到一基源码行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 当前模式命中行号

            # finding 保留原始匹配片段便于修复。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,  # 违规控制构造所在 RTL 文件
                    int_line,  # 当前命中的一基源码行号
                    str_message,  # 当前固定规则的诊断文本
                    obj_match.group(0).strip(),  # 正则命中的原始代码片段
                )
            )

    # 任一确定命中都使固定门禁失败。
    if list_findings:

        # 返回全部命中，避免只修复第一处后重复往返。
        return failed(*list_findings)

    # 没有目标构造时文本规则按不适用通过。
    return passed(applicable=False)

# _constant_names 汇总单个 module 内允许引用的常量符号。
def _constant_names(dict_module: dict[str, object]) -> set[str]:
    """收集当前 module 的 parameter 与 localparam 名称。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        当前 module 中可用于循环常量表达式的名称集合。
    """

    # names 只保存当前 module 的综合期常量身份。
    set_names: set[str] = set()  # 当前 module 的 parameter 与 localparam 名称

    # params 和 localparams 都可作为当前 module 的综合期常量。
    for str_collection in ("params", "localparams"):

        # 空名称被排除，避免放宽任意表达式。
        set_names.update(
            str(dict_item.get("name") or "")  # 当前常量声明名称
            for dict_item in dict_module.get(str_collection, []) or []  # 遍历当前常量声明集合
            if str(dict_item.get("name") or "")  # 排除 formatter 空名称
        )

    # 返回当前 module 的已声明常量名。
    return set_names

# _constant_expression 限定 for 初值和边界的符号来源。
def _constant_expression(str_expression: str, set_constants: set[str]) -> bool:
    """判断简单表达式是否只由数字和已声明常量组成。

    参数:
        str_expression: for 初值或边界表达式文本。
        set_constants: 已声明 parameter/localparam 名称集合。
    返回:
        True 表示没有变量标识符或全部标识符均为常量。
    """

    # 确定定宽常量先替换为空白，避免 d0、hFF 等数位被当作标识符。
    str_without_literals = DETERMINISTIC_BASED_LITERAL_PATTERN.sub(" ", str_expression)  # 待提取符号的表达式

    # 标识符集合忽略数字、合法定宽常量和运算符，只核对符号身份。
    set_identifiers = set(  # 表达式中剩余的真实标识符
        re.findall(r"\b[A-Za-z_]\w*\b", str_without_literals)  # 去除常量后的符号集合
    )

    # 纯数字表达式或常量子集满足综合期边界要求。
    return not set_identifiers or set_identifiers <= set_constants

# _known_signals 为敏感列表分析限定有效读取标识符。
def _known_signals(dict_module: dict[str, object]) -> set[str]:
    """收集当前 module 的端口与内部信号名。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
    返回:
        当前 module 作用域内的端口和声明信号集合。
    """

    # names 排除关键字、常量和过程局部文本标识符。
    set_names: set[str] = set()  # 当前 module 的已知信号名称

    # ports 与 decls 使用同一 name 字段。
    for str_collection in ("ports", "decls"):

        # 只登记非空信号名。
        set_names.update(
            str(dict_item.get("name") or "")  # 当前端口或内部声明名称
            for dict_item in dict_module.get(str_collection, []) or []  # 遍历 formatter 声明集合
            if str(dict_item.get("name") or "")  # 防止空声明污染敏感信号全集
        )

    # 返回敏感列表和正文读取分析使用的信号全集。
    return set_names
