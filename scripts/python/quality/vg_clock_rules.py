"""实现派生时钟来源相关 RTL VG 门禁。"""

# future annotations 延后解析规则模型类型。
from __future__ import annotations

# re 用于恢复时钟边沿和非时钟连接位置。
import re

# Callable 描述固定编号到时钟规则函数的路由表。
from typing import Callable

# facts 提供 formatter AST 确认的 module 结构。
from .vg_semantic_facts import VgFacts, VgSourceFacts, iter_trusted_modules

# 实例端口时钟角色按完整下划线语义段识别。
from .clock_name_roles import is_clock_name

# 原语 profile 为 IBUFDS/BUFG 等专用时钟端口提供 role-aware 语义。
from .vg_primitive_facts import primitive_port_is_clock_role, primitive_profiles

# models 统一逐门禁结论与证据格式。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# evaluate_clock_gate 把固定编号路由到对应的时钟规则实现。
def evaluate_clock_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行时钟语义域中的指定固定 VG 门禁。

    参数:
        str_gate_id: 当前执行的固定 VG 时钟门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前时钟来源规则的逐门禁结论。
    """

    # 路由表覆盖单域、来源、门控、边沿和非时钟连接规则。
    dict_evaluators: dict[str, Callable[[VgFacts], VgEvaluation]] = {  # 固定编号到时钟规则函数的映射
        "VG073": _single_clock_domain,  # 单时钟域建议检查
        "VG089": _combinational_clock_source,  # 组合逻辑派生时钟检查
        "VG096": _gated_clock,  # 门控时钟建议检查
        "VG107": _registered_clock_source,  # 寄存器输出时钟检查
        "VG120": _single_clock_edge,  # 同时使用双边沿检查
        "VG132": _clock_only_clock_pin,  # 时钟作为数据信号检查
    }

    # engine 已保证编号属于时钟域，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _single_clock_domain 要求一个设计目标只使用一个时钟信号。
def _single_clock_domain(facts: VgFacts) -> VgEvaluation:
    """检查 formatter 识别的时序过程块是否共享单一时钟域。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        多时钟域失败、未知时钟不确定或单域适用通过结论。
    """

    # findings 保存单个 module 内部出现的多时钟域证据。
    list_findings: list[VgFinding] = []  # module-local 多时钟域定位

    # unknown 标记存在时序块但 formatter 未能抽取时钟。
    bool_unknown = False  # 是否存在无法识别时钟的时序块

    # applicable 记录是否实际发现至少一个时序过程块。
    bool_applicable = False  # 是否存在可审查的时序时钟语义

    # 每个 module 独立构造时钟集合，避免不同接口名称相互污染。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 当前 module 的时钟名称不传播到其他 module。
        set_module_clocks: set[str] = set()  # 当前 module 的确定时钟域集合

        # 当前 module 的首次出现位置用于局部多域诊断。
        dict_clock_locations: dict[str, int] = {}  # 当前时钟到一基行号的映射

        # 组合块不属于时钟域统计范围。
        for dict_always in dict_module.get("always", []):

            # 组合过程块没有时钟边沿语义。
            if bool(dict_always.get("is_combinational")):

                # 跳过组合块，继续检查其他过程块。
                continue

            # 发现时序块即证明规则对当前设计适用。
            bool_applicable = True  # 至少一个时序块进入时钟域检查

            # formatter 的 clock 字段是时钟域身份权威。
            str_clock = str(dict_always.get("clock") or "")  # 当前时序块的时钟信号名

            # 无法提取时钟时保持 fail-closed。
            if not str_clock:

                # 记录未知事实，但继续收集其他确定时钟域。
                bool_unknown = True  # 当前时序块缺少可靠时钟名

                # 当前块不能安全加入时钟域集合。
                continue

            # 将确定时钟加入当前 module 的域集合。
            set_module_clocks.add(str_clock)

            # 首次出现位置用于多域诊断，重复时钟无需覆盖。
            dict_clock_locations.setdefault(
                str_clock,
                int(dict_always.get("line_start") or 1),
            )

        # 两个及以上时钟只在当前 module 内形成违规。
        if len(set_module_clocks) > 1:

            # 每个局部时钟域都保留一条定位，便于确认模块内部边界。
            list_findings.extend(  # 当前 module 的多时钟域定位证据
                VgFinding(  # 当前独立时钟域的诊断对象
                    source_facts.relative_path,  # 当前时钟所在源码文件
                    dict_clock_locations[str_clock],  # 当前时钟首次出现的一基行号
                    "建议单个模块仅使用一个时钟域。",  # VG073 用户诊断
                    str_clock,  # 当前独立时钟域名称
                )
                for str_clock in sorted(set_module_clocks)  # 稳定排序当前 module 时钟域
            )

    # 任一 module 内部存在多时钟域时返回全部局部证据。
    if list_findings:

        # 返回全部独立时钟域的定位证据。
        return failed(*list_findings)

    # 仍有无法识别的时序时钟时不能确认单域。
    if bool_unknown:

        # 未知时钟可能引入第二个域，必须保持不确定状态。
        return inconclusive("存在无法由 formatter AST 确定的时序时钟域。")

    # 每个 module 均为单域时通过；没有时序块则不适用。
    return passed(applicable=bool_applicable)

# _combinational_clock_source 保持 VG089 的既有组合来源语义。
def _combinational_clock_source(facts: VgFacts) -> VgEvaluation:
    """检查组合逻辑输出是否被用作时钟。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        组合派生时钟的逐门禁结论。
    """

    # VG089 禁止任何组合网络输出作为时钟。
    return _derived_clock_gate(facts, combination_clock=True)

# _gated_clock 复用组合来源事实识别门控时钟。
def _gated_clock(facts: VgFacts) -> VgEvaluation:
    """检查逻辑门控后的信号是否被用作寄存器时钟。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        门控时钟的失败或直连时钟的适用通过结论。
    """

    # 当前 formatter 的组合来源集合覆盖 assign 和组合 always 门控输出。
    return _derived_clock_gate(facts, combination_clock=True)

# _registered_clock_source 保持 VG107 的既有寄存器来源语义。
def _registered_clock_source(facts: VgFacts) -> VgEvaluation:
    """检查寄存器输出是否被用作其他寄存器时钟。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        寄存器派生时钟的逐门禁结论。
    """

    # VG107 选择时序过程块的驱动目标作为禁止来源。
    return _derived_clock_gate(facts, combination_clock=False)

# _derived_clock_module 处理一个 module 的驱动来源和时钟使用点。
def _derived_clock_module(
    source_facts: VgSourceFacts,
    dict_module: dict[str, object],
    bool_combination_clock: bool,
) -> tuple[list[VgFinding], bool, bool]:
    """计算单个 module 的派生时钟 findings、适用性和未知状态。

    参数:
        source_facts: 当前 module 所属源码的稳定路径事实。
        dict_module: formatter 提供的单个 module 结构事实。
        bool_combination_clock: True 检查组合来源，False 检查寄存器来源。
    返回:
        ``(findings, applicable, unknown)`` 三元组。
    """

    # 连续赋值天然属于组合来源集合。
    set_continuous_targets = {str(dict_assign.get("lhs") or "") for dict_assign in dict_module.get("assigns", [])}  # 连续赋值目标

    # 组合和时序过程块目标分开收集，避免跨来源误判。
    set_combinational_targets: set[str] = set()  # 组合过程目标

    # 时序过程目标另行保存，供寄存器派生时钟检查使用。
    set_sequential_targets: set[str] = set()  # 时序过程目标

    # 第一遍建立每个 always 的驱动目标集合。
    for dict_always in dict_module.get("always", []):

        # 当前 always 的目标集合去重后再合并到来源索引。
        set_targets = {str(str_target) for str_target in dict_always.get("targets", [])}  # 当前 always 目标

        # formatter 的组合标记决定目标所属来源。
        if bool(dict_always.get("is_combinational")):

            # 组合目标用于 VG089。
            set_combinational_targets.update(set_targets)

        # 非组合过程块的目标归入时序来源。
        else:

            # 时序目标用于 VG107。
            set_sequential_targets.update(set_targets)

    # 当前门禁只禁止所选来源集合中的时钟。
    set_forbidden_sources: set[str] = set_sequential_targets  # 默认禁止寄存器驱动来源

    # 组合派生门禁还要纳入连续赋值和组合过程目标。
    if bool_combination_clock:

        # VG089 的禁止来源由两类组合目标合并而成。
        set_forbidden_sources = set_continuous_targets | set_combinational_targets  # 组合派生禁止来源

    # findings 保存当前 module 的确定违规，unknown 表示缺失 clock 字段。
    list_findings: list[VgFinding] = []  # 当前 module 的确定 findings

    # 适用性只在发现确定时钟名称后置为 True。
    bool_applicable = False  # 当前 module 是否适用

    # 第二遍检查每个时序块实际使用的时钟信号。
    for dict_always in dict_module.get("always", []):

        # 组合块没有寄存器时钟管脚语义。
        if bool(dict_always.get("is_combinational")):

            # 组合过程不进入派生时钟检查。
            continue

        # clock 字段由 formatter AST 从敏感沿中提取。
        str_clock = str(dict_always.get("clock") or "")  # 当前时序块的时钟名称

        # 时序块缺少时钟名称时保持未知，禁止确定通过。
        if not str_clock:

            # 调用方会把 unknown 转成统一 inconclusive 结论。
            return list_findings, bool_applicable, True

        # 找到确定时钟后规则对当前设计适用。
        bool_applicable = True  # 已确认当前 module 存在可分析时钟

        # 普通输入时钟不在禁止来源中，继续检查下一个块。
        if str_clock not in set_forbidden_sources:

            # 当前时钟来源安全。
            continue

        # always 起始行是 formatter 提供的稳定证据位置。
        int_line = int(dict_always.get("line_start") or 1)  # 派生时钟使用点行号

        # 诊断文本区分组合派生与寄存器派生。
        str_message = "组合逻辑输出被用作时钟。" if bool_combination_clock else "寄存器输出被用作其他寄存器的时钟。"  # 当前门禁诊断文本

        # 记录当前 module 的确定违规。
        list_findings.append(
            VgFinding(source_facts.relative_path, int_line, str_message, str_clock)
        )

    # 当前 module 没有缺失时钟的时序块。
    return list_findings, bool_applicable, False

# _derived_clock_gate 实现组合来源与寄存器来源的共享检查。
def _derived_clock_gate(facts: VgFacts, *, combination_clock: bool) -> VgEvaluation:
    """检查时序 always 的时钟是否来自指定驱动类型。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        combination_clock: True 检查组合来源，False 检查寄存器来源。
    返回:
        指定派生时钟类型的失败、不确定或适用通过结论。
    """

    # findings 汇总所有 module 中的确定违规来源。
    list_findings: list[VgFinding] = []  # 所有 module 的派生时钟 findings

    # applicable 标记至少发现一个可解析的时序 always。
    bool_applicable = False  # 全局规则适用性

    # 每个 module 独立计算驱动来源，禁止跨层级混淆同名信号。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # module helper 同时返回确定 findings、适用性和未知状态。
        tuple_module_result = _derived_clock_module(  # 当前 module 的派生时钟原始结果
            source_facts,  # 当前 module 源码定位事实
            dict_module,  # 当前 module 结构事实
            combination_clock,  # 当前派生时钟来源类型
        )

        # 分开累计三类 module-local 结果。
        list_module_findings, bool_module_applicable, bool_unknown = tuple_module_result  # 当前 module 的 findings、适用性和未知标记

        # 合并当前 module 的确定派生时钟 findings。
        list_findings.extend(list_module_findings)

        # 任一 module 适用时整轮规则保持适用。
        bool_applicable = bool_applicable or bool_module_applicable  # 累计规则适用性

        # 任一 module 时钟字段未知都禁止整轮确定通过。
        if bool_unknown:

            # 保持既有 fail-closed 诊断文本。
            return inconclusive("时序 always 的时钟来源无法由 formatter AST 确定。")

    # 任一确定派生时钟都使当前固定门禁失败。
    if list_findings:

        # 失败结论保留全部时钟使用点。
        return failed(*list_findings)

    # 没有违规时仍报告规则是否实际检查过时序块。
    return passed(applicable=bool_applicable)

# _single_clock_edge_module 解析一个 module 的时钟边沿集合。
def _single_clock_edge_module(
    source_facts: VgSourceFacts,
    dict_module: dict[str, object],
) -> tuple[list[VgFinding], bool, bool]:
    """返回单个 module 的双边沿 findings、未知状态和适用性。

    参数:
        source_facts: 当前 module 所属源码的稳定路径事实。
        dict_module: formatter 提供的单个 module 结构事实。
    返回:
        ``(findings, unknown, applicable)`` 三元组。
    """

    # 同名端口在不同 module 中不是同一物理时钟，集合必须局部建立。
    dict_module_edges: dict[str, set[str]] = {}  # 当前 module 的时钟边沿集合

    # 记录每个时钟首次出现位置，供冲突 finding 定位。
    dict_module_locations: dict[str, tuple[str, int]] = {}  # 时钟首次位置

    # 缺失时钟名称或边沿关键字时维持未知状态。
    bool_unknown = False  # 当前 module 是否存在未知边沿

    # 至少一个确定边沿时当前 module 适用。
    bool_applicable = False  # 当前 module 适用性

    # 每个时序块独立提取与 clock 字段对应的边沿关键字。
    for dict_always in dict_module.get("always", []):

        # 组合过程块不参与时钟边沿规则。
        if bool(dict_always.get("is_combinational")):

            # 当前块没有 posedge/negedge 时钟语义。
            continue

        # formatter 提供当前时序块的时钟信号名。
        str_clock = str(dict_always.get("clock") or "")  # 当前时序块时钟名称

        # header 与 lines 是 formatter AST 暴露的稳定过程块文本字段。
        list_always_lines = [  # 当前 always 的原始行片段
            str(dict_always.get("header") or ""),  # always 敏感列表头部
            *[str(str_line) for str_line in dict_always.get("lines", []) or []],  # 过程块正文行
        ]

        # 把头部和正文合并为边沿匹配使用的可信文本。
        str_always_text = "\n".join(list_always_lines)  # always 可信文本

        # 时钟名称缺失时不能安全比较边沿集合。
        if not str_clock:

            # 标记未知时钟边沿事实，并继续检查其他过程块。
            bool_unknown = True  # 当前块缺少时钟名称

            # 无时钟名的过程块不能加入边沿映射。
            continue

        # 只匹配当前 formatter 时钟字段对应的边沿声明。
        obj_edge_match = re.search(  # 当前时钟边沿匹配结果
            rf"\b(posedge|negedge)\s+{re.escape(str_clock)}\b",  # 只匹配目标时钟的显式边沿
            str_always_text,  # formatter 导出的 always 文本
            flags=re.IGNORECASE,  # Verilog 关键字大小写无关
        )

        # 缺少原始边沿关键字时保持未知。
        if obj_edge_match is None:

            # 当前块存在但边沿类型无法确定。
            bool_unknown = True  # 当前块缺少可靠边沿类型

            # 当前块不能形成确定边沿事实。
            continue

        # 把当前边沿加入当前 module 对应时钟的集合。
        dict_module_edges.setdefault(str_clock, set()).add(obj_edge_match.group(1).lower())

        # 已提取到确定时钟边沿时标记规则适用。
        bool_applicable = True  # 当前 module 至少有一个确定边沿

        # 首次位置用于双边沿冲突报告。
        tuple_location = (source_facts.relative_path, int(dict_always.get("line_start") or 1))  # 时钟首次文件和行号

        # 只保留当前时钟的第一处位置，保证报告稳定。
        dict_module_locations.setdefault(str_clock, tuple_location)  # 保留首次出现位置

    # 当前 module 的双边沿时钟逐一形成 finding。
    list_findings: list[VgFinding] = []  # 当前 module 双边沿 findings

    # 只有同一时钟同时出现两个边沿时才生成冲突证据。
    for str_clock, set_edges in sorted(dict_module_edges.items()):

        # 单边沿时钟不触发 VG110。
        if {"posedge", "negedge"} > set_edges:

            # 当前时钟边沿集合安全，继续检查下一个时钟。
            continue

        # 当前 module 的双边沿时钟形成一条定位稳定的 finding。
        list_findings.append(
            VgFinding(
                dict_module_locations[str_clock][0],
                dict_module_locations[str_clock][1],
                "避免同时使用同一时钟的上升沿和下降沿。",
                str_clock,
            )
        )

    # 将当前 module 的局部结论交给总规则合并。
    return list_findings, bool_unknown, bool_applicable

# _single_clock_edge 禁止同一时钟同时作为上升沿和下降沿触发源。
def _single_clock_edge(facts: VgFacts) -> VgEvaluation:
    """检查同一时钟是否混合使用 posedge 与 negedge。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        双边沿使用的失败结论，或单边沿设计的适用通过结论。
    """

    # findings 汇总单个 module 内部确认的双边沿冲突。
    list_findings: list[VgFinding] = []  # 所有 module 的双边沿 findings

    # 任一 module 出现不完整边沿事实都保持未知。
    bool_unknown = False  # 全局未知状态

    # 至少一个确定边沿时整轮规则适用。
    bool_applicable = False  # 全局适用性

    # 逐 module 合并局部时钟边沿事实，保持同名时钟隔离。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # helper 返回当前 module 的确定、未知和适用性三类结果。
        tuple_module_result = _single_clock_edge_module(source_facts, dict_module)  # 当前 module 边沿结果

        # 合并 module-local findings 与状态标记。
        list_findings.extend(tuple_module_result[0])  # 累计当前 module 双边沿 findings

        # 未知边沿事实按 module 结果向上传播。
        bool_unknown = bool_unknown or tuple_module_result[1]  # 累计未知边沿标记

        # 确定边沿事实按 module 结果向上传播。
        bool_applicable = bool_applicable or tuple_module_result[2]  # 当前 module 有确定边沿即保持适用

    # 任一双边沿时钟都使规则失败。
    if list_findings:

        # 返回全部混用边沿的时钟证据。
        return failed(*list_findings)

    # 边沿信息不完整时禁止报告确定通过。
    if bool_unknown:

        # 未知块可能引入另一个边沿类型。
        return inconclusive("存在无法由 formatter AST 确定的时钟边沿。")

    # 至少一个已知边沿时适用通过，否则规则不适用。
    return passed(applicable=bool_applicable)

# _module_clock_names 提取单个 module 内 formatter 已确认的时钟名称。
def _module_clock_names(dict_module: dict[str, object]) -> set[str]:
    """返回当前 module 的确定时钟名称集合。

    参数:
        dict_module: formatter 提供的单个 module 结构事实。
    返回:
        仅包含非组合 always 且存在 clock 字段的名称集合。
    """

    # 只有时序过程块的 clock 字段可以定义时钟身份。
    return {
        str(dict_always["clock"])
        for dict_always in dict_module.get("always", []) or []
        if not bool(dict_always.get("is_combinational")) and dict_always.get("clock")
    }

# _primitive_clock_connections 读取原语 profile 声明的专用时钟管脚连接。
def _primitive_clock_connections(
    dict_module: dict[str, object],
    dict_primitive_catalog: dict[str, object],
) -> set[tuple[str, str]]:
    """返回 module 内原语时钟端口与信号的连接对。

    参数:
        dict_module: formatter 提供的单个 module 结构事实。
        dict_primitive_catalog: 已校验的原语 profile catalog。
    返回:
        ``(port_name, signal_name)`` 形式的专用时钟连接集合。
    """

    # 结果只保留简单命名连接，复杂表达式继续走普通数据路径判断。
    set_clock_connections: set[tuple[str, str]] = set()  # 原语专用时钟连接

    # 每个实例独立读取 module 名称和 formatter 原文。
    for dict_instance in dict_module.get("instances", []) or []:

        # profile 查询需要精确的实例模块名，不能依赖端口名称猜测。
        str_instance_module = str(dict_instance.get("module_name") or "")  # 原语实例模块名

        # 实例原文限定了可安全识别的简单命名连接形式。
        str_instance_text = str(dict_instance.get("text") or "")  # 实例命名连接原文

        # 只提取一个标识符作为连接信号的简单命名端口。
        for obj_profile_match in re.finditer(r"\.(\w+)\s*\(\s*(\w+)\s*\)", str_instance_text):

            # profile 的 clock role 是允许的专用时钟边界。
            if primitive_port_is_clock_role(
                dict_primitive_catalog,
                str_instance_module,
                obj_profile_match.group(1),
            ):

                # 保存端口与信号，供本 module 的时钟名称逐一匹配。
                set_clock_connections.add(
                    (obj_profile_match.group(1), obj_profile_match.group(2))
                )

    # 返回不可变语义内容的可变局部副本，调用方只读使用。
    return set_clock_connections

# _primitive_clock_output_signals 把 profile 的 clock-output 绑定提升为时钟源事实。
def _primitive_clock_output_signals(
    dict_module: dict[str, object],
    dict_primitive_catalog: dict[str, object],
) -> set[str]:
    """返回当前 module 内由已登记原语产生的时钟输出信号。

    参数:
        dict_module: formatter 提供的单个 module 结构事实。
        dict_primitive_catalog: 已校验的原语 profile catalog。
    返回:
        当前 module 的 clock-output 实际信号集合。
    """

    # 目录 profile 是 output 方向和 clock role 的唯一来源。
    dict_profiles = primitive_profiles(dict_primitive_catalog)  # 当前扫描的原语 profile 快照

    # 结果只保存简单命名连接对应的信号。
    set_output_signals: set[str] = set()  # 原语 clock-output 对应的实际信号

    # 每个实例只解析简单命名端口，复杂表达式保持未知边界。
    for dict_instance in dict_module.get("instances", []) or []:

        # 未登记模块不能凭端口名称制造时钟源。
        dict_profile = dict_profiles.get(str(dict_instance.get("module_name") or ""))  # 当前实例 profile

        # 无 profile 时交给未知实例 fail-closed 分支。
        if not isinstance(dict_profile, dict):

            # 不从端口名称猜测未知模块的输出方向。
            continue

        # 保存实例原文，限定后续正则只处理简单命名连接。
        str_instance_text = str(dict_instance.get("text") or "")  # 当前原语实例原文

        # 只把 output 方向且明确带 clock role 的端口视为时钟源。
        for obj_match in re.finditer(
            r"\.(\w+)\s*\(\s*(\w+)\s*\)",
            str_instance_text,
        ):

            # 端口 profile 决定连接方向和时钟角色。
            dict_port = dict(dict_profile.get("ports", {}).get(obj_match.group(1), {}))  # 当前 formal 的 profile 端口

            # 输入或未知方向不能形成原语时钟源。
            if str(dict_port.get("direction") or "").lower() != "output":

                # 当前端口不属于 output boundary。
                continue

            # 显式 clock role 才能提升为时钟信号。
            if set(dict_port.get("roles", [])) & {"clock_output", "clock_feedback", "clock_capable"}:

                # 保存真实连接信号供 module-local 数据用途检查。
                set_output_signals.add(obj_match.group(2))

    # 返回当前 module 的稳定信号集合。
    return set_output_signals

# _unknown_clock_instance_findings 为未登记实例生成 VG132 的不确定证据。
def _unknown_clock_instance_findings(
    source_facts: VgSourceFacts,
    dict_module: dict[str, object],
    set_known_module_names: set[str],
) -> list[VgFinding]:
    """返回无法确定时钟角色的未登记实例发现项。

    参数:
        source_facts: 当前 module 所属的源码文件事实。
        dict_module: formatter 提供的单个 module 结构事实。
        set_known_module_names: 本轮已知 source、external 和 primitive 名称。
    返回:
        未登记实例对应的 fail-closed finding 列表。
    """

    # 未登记实例可能隐藏 clock-output，禁止按无时钟用途通过。
    list_findings: list[VgFinding] = []  # 未登记实例的不确定证据

    # 每个实例独立核对模块名称是否属于受治理边界。
    for dict_instance in dict_module.get("instances", []) or []:

        # 实例模块名是原语和外部接口查找的精确键。
        str_module_name = str(dict_instance.get("module_name") or "")  # 当前实例引用的 module 名称

        # 已知模块的时钟角色由 source/profile 事实继续判断。
        if not str_module_name or str_module_name in set_known_module_names:

            # 当前实例不属于未知边界。
            continue

        # 实例起始行用于生成跨机器稳定定位。
        int_line = int(dict_instance.get("line_start") or dict_module.get("line_start") or 1)  # 未登记实例起始行

        # 证据同时保留模块和实例名称。
        str_evidence = f"{str_module_name} {dict_instance.get('instance_name') or ''}".strip()  # 未登记实例证据

        # 未登记模块缺少可审计的时钟端口角色。
        list_findings.append(
            VgFinding(
                source_facts.relative_path,
                int_line,
                "未登记原语或外部模块的时钟角色无法静态确定。",
                str_evidence,
            )
        )

    # 返回当前 module 的不确定发现。
    return list_findings

# _clock_assign_findings 生成时钟出现在连续赋值两侧的确定证据。
def _clock_assign_findings(
    source_facts: VgSourceFacts,
    dict_module: dict[str, object],
    set_module_clocks: set[str],
) -> list[VgFinding]:
    """检查一个 module 的连续赋值是否使用时钟信号。

    参数:
        source_facts: 当前源码的稳定相对路径事实。
        dict_module: formatter 提供的单个 module 结构事实。
        set_module_clocks: 当前 module 的确定时钟名称集合。
    返回:
        连续赋值数据化时钟的 finding 列表。
    """

    # 当前 helper 只产生确定违规，不负责规则适用性判定。
    list_findings: list[VgFinding] = []  # 连续赋值时钟违规

    # 每条 assign 由 formatter 提供 lhs、rhs 和起始行号。
    for dict_assign in dict_module.get("assigns", []):

        # 重建稳定 assign 文本，供正则和 finding evidence 共用。
        str_assign_text = f"assign {dict_assign.get('lhs') or ''} = {dict_assign.get('rhs') or ''};"  # assign 证据文本

        # 同一 assign 可能同时包含多个 module-local 时钟名称。
        for str_clock in sorted(set_module_clocks):

            # 不出现当前时钟时，当前 assign 不产生 finding。
            if re.search(rf"\b{re.escape(str_clock)}\b", str_assign_text) is None:

                # 继续检查当前 assign 的其他时钟名称。
                continue

            # formatter 行号提供可复现的连续赋值定位。
            int_line = int(dict_assign.get("line_start") or 1)  # assign 的一基行号

            # 连续赋值把时钟带入普通数据路径，生成确定违规。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "时钟信号只能连接到寄存器时钟管脚。",
                    str_assign_text,
                )
            )

    # 返回当前 module 的连续赋值 findings。
    return list_findings

# _clock_instance_findings 生成普通实例端口接收时钟信号的证据。
def _clock_instance_findings(
    source_facts: VgSourceFacts,
    str_module_text: str,
    int_base_line: int,
    set_module_clocks: set[str],
    set_profile_clock_connections: set[tuple[str, str]],
) -> list[VgFinding]:
    """检查时钟信号是否连接到非时钟实例端口。

    参数:
        source_facts: 当前源码的稳定相对路径事实。
        str_module_text: formatter 提供的 module 原文。
        int_base_line: module 原文对应的一基起始行号。
        set_module_clocks: 当前 module 的确定时钟名称集合。
        set_profile_clock_connections: 原语专用时钟端口连接集合。
    返回:
        时钟进入普通实例端口的 finding 列表。
    """

    # 当前 helper 只比较命名端口，保持与既有 formatter 证据边界一致。
    list_findings: list[VgFinding] = []  # 普通实例端口时钟违规

    # 每个时钟名称独立匹配 module 原文中的命名连接。
    for str_clock in sorted(set_module_clocks):

        # 捕获当前时钟连接到的端口名。
        for obj_match in re.finditer(rf"\.(\w+)\s*\(\s*{re.escape(str_clock)}\s*\)", str_module_text):

            # 完整时钟名称或 profile 专用时钟角色均属于合法连接。
            bool_clock_port = is_clock_name(obj_match.group(1))  # 端口名称的通用时钟角色

            # 原语 profile 的专用时钟端口也属于合法边界。
            bool_profile_port = (obj_match.group(1), str_clock) in set_profile_clock_connections  # profile 时钟连接标记

            # 合法时钟端口不属于普通数据路径。
            if bool_clock_port or bool_profile_port:

                # 当前命名连接已经有明确的时钟语义。
                continue

            # module 原文偏移换算为稳定的一基源码行号。
            int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 普通端口连接行号

            # 记录时钟进入普通实例端口的确定违规。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "时钟信号连接到了非时钟实例端口。",
                    obj_match.group(0),
                )
            )

    # 返回当前 module 的实例端口 findings。
    return list_findings

# _known_clock_module_names 汇总本轮可解释实例模块的 exact-name 边界。
def _known_clock_module_names(facts: VgFacts) -> set[str]:
    """返回 source、external 和 primitive 的已知模块名称集合。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前扫描闭包内可以继续解释时钟角色的模块名称。
    """

    # 集合同时承载 RTL source、external stub 与原语清单。
    set_known_module_names: set[str] = set()  # 本轮受治理的实例模块名称

    # 收集目标 RTL 中所有可信 source module 名称。
    for _, dict_module, _, _ in iter_trusted_modules(facts):

        # source module 名称是实例引用的精确身份。
        str_source_name = str(dict_module.get("name") or "")  # 当前 source module 名称

        # 将 source 名称加入已知边界集合。
        set_known_module_names.add(str_source_name)

    # 收集显式 external interface 的 module 名称。
    for dict_module in facts.external_modules:

        # external schema 的名称字段需要兼容历史 name 与 module_name。
        str_external_name = str(dict_module.get("name") or dict_module.get("module_name") or "")  # 从 schema 解析实例查找键

        # 将 external 接口身份登记到已知边界集合。
        set_known_module_names.add(str_external_name)

    # 复制原语名称索引，避免遍历过程中重复构建 profile mapping。
    dict_primitive_profiles = primitive_profiles(  # 当前原语名称到 profile 的索引
        getattr(facts, "primitive_catalog", {})  # 兼容未提供原语目录的旧事实对象
    )

    # 将固定原语名称加入已知边界集合。
    for str_primitive_name in dict_primitive_profiles:

        # 原语名称由 catalog exact-name 清单授权。
        set_known_module_names.add(str_primitive_name)

    # 空名称不具备可审计的 module 身份。
    set_known_module_names.discard("")

    # 返回本轮固定的 exact-name 边界。
    return set_known_module_names

# _clock_only_clock_pin 禁止已识别时钟进入普通数据连接。
def _clock_only_clock_pin(facts: VgFacts) -> VgEvaluation:
    """检查时钟信号是否被连续赋值或普通实例端口当作数据使用。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        时钟数据化连接的失败结论，或纯时钟用途的适用通过结论。
    """

    # findings 保存时钟进入普通数据路径的确定证据。
    list_findings: list[VgFinding] = []  # 时钟非时钟用途证据

    # applicable 区分无时钟输入与各 module 已完成局部检查。
    bool_applicable = False  # 是否识别出至少一个 module-local 时钟

    # source、external 和 primitive 名称共同定义本轮已知实例边界。
    set_known_module_names = _known_clock_module_names(facts)  # 当前扫描的 exact-name 边界

    # 未登记实例的角色缺口独立于时序 always 事实保留。
    list_unknown_findings: list[VgFinding] = []  # 未登记模块的 fail-closed 证据

    # 每个 module 独立建立时钟身份并检查本模块数据路径。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 当前 module 的时钟集合起初来自本模块时序 always。
        set_module_clocks = _module_clock_names(dict_module)  # 当前 module 已识别的时钟名称

        # profile clock-output 是没有 always 时仍可确认的时钟源。
        set_profile_clock_outputs = _primitive_clock_output_signals(  # 汇总 IBUFDS、BUFG 等原语输出网供 VG132 追踪其是否误接普通数据端口并阻断确定通过
            dict_module,  # 当前 module 的 formal-to-signal 连接文本
            getattr(facts, "primitive_catalog", {}),  # 说明 formal 端口如何参与 VG132 时钟角色解析
        )

        # 将 profile clock-output 与 always 时钟统一到 module-local 集合。
        set_module_clocks.update(set_profile_clock_outputs)

        # 未登记实例可能提供未知时钟输出，先记录不确定证据。
        list_unknown_findings.extend(
            _unknown_clock_instance_findings(
                source_facts,
                dict_module,
                set_known_module_names,
            )
        )

        # unknown instance 或 profile clock-output 均使规则具有审查对象。
        if set_profile_clock_outputs or list_unknown_findings:

            # profile 输出或未知实例都证明当前规则存在审查对象。
            bool_applicable = True  # profile 或未知实例使 VG132 进入适用状态

        # 没有时钟的 module 不参与时钟数据化判断。
        if not set_module_clocks:

            # 继续检查下一个独立 module。
            continue

        # 当前 module 至少存在一个可审查时钟。
        bool_applicable = True  # 已进入 module-local 时钟用途检查

        # 结构化实例事实用于识别 profile 专用时钟端口。
        set_profile_clock_connections = _primitive_clock_connections(  # 当前 module 的 profile 时钟连接
            dict_module,  # 当前 module 实例结构
            getattr(facts, "primitive_catalog", {}),  # 当前扫描使用的原语目录
        )

        # 连续赋值的任一侧出现时钟都属于普通数据路径使用。
        list_findings.extend(
            _clock_assign_findings(source_facts, dict_module, set_module_clocks)
        )

        # 命名实例端口可通过端口名区分 clock 与普通数据连接。
        list_findings.extend(  # 累计普通实例端口违规
            _clock_instance_findings(
                source_facts,  # 当前源码路径事实
                # module 原文用于解析命名端口。
                str_module_text,  # 当前 module 原文
                # module 原文基线用于恢复 finding 行号。
                int_base_line,  # 当前 module 起始行号
                # 时钟名称决定哪些连接需要审查。
                set_module_clocks,  # 当前 module 时钟名称
                # profile 专用端口允许合法时钟连接。
                set_profile_clock_connections,  # profile 专用时钟端口
            )
        )

    # 任一普通数据用途都使门禁失败。
    if list_findings:

        # 返回全部非时钟连接证据。
        return failed(*list_findings)

    # 未登记模块可能隐藏时钟角色，不能按确定通过处理。
    if list_unknown_findings:

        # 保留全部未知实例定位，等待显式 stub 或原语 profile。
        return inconclusive(
            "存在未登记原语或外部模块，无法确定时钟角色。",
            *list_unknown_findings,
        )

    # 已识别时钟仅出现在合法时钟用途时适用通过。
    return passed(applicable=bool_applicable)
