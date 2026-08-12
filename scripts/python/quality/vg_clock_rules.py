"""实现派生时钟来源相关 RTL VG 门禁。"""

# future annotations 延后解析规则模型类型。
from __future__ import annotations

# re 用于恢复时钟边沿和非时钟连接位置。
import re

# Callable 描述固定编号到时钟规则函数的路由表。
from typing import Callable

# facts 提供 formatter AST 确认的 module 结构。
from .vg_semantic_facts import VgFacts, iter_trusted_modules

# 实例端口时钟角色按完整下划线语义段识别。
from .clock_name_roles import is_clock_name

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

# _derived_clock_gate 实现组合来源与寄存器来源的共享检查。
def _derived_clock_gate(facts: VgFacts, *, combination_clock: bool) -> VgEvaluation:
    """检查时序 always 的时钟是否来自指定驱动类型。

    参数:
        facts: formatter AST 构建的可信扫描事实。
        combination_clock: True 检查组合来源，False 检查寄存器来源。
    返回:
        指定派生时钟类型的失败、不确定或适用通过结论。
    """

    # 布尔参数明确当前检查组合派生还是寄存器派生来源。
    bool_combination_clock = combination_clock  # 是否检查组合逻辑派生时钟

    # findings 汇总所有 module 中的确定违规来源。
    list_findings: list[VgFinding] = []  # 本轮确认的派生时钟证据

    # applicable 标记至少发现一个可解析的时序 always。
    bool_applicable = False  # 当前设计是否存在时钟来源检查对象

    # 每个 module 独立计算驱动来源，禁止跨层级混淆同名信号。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 连续赋值目标属于组合逻辑输出来源。
        set_continuous_targets = {
            str(dict_assign.get("lhs") or "")  # 当前连续赋值的左值信号
            for dict_assign in dict_module.get("assigns", [])  # formatter 识别的连续赋值
        }  # 当前 module 的连续赋值目标

        # 组合 always 目标稍后与连续赋值目标合并。
        set_combinational_targets: set[str] = set()  # 当前 module 的组合过程输出

        # 时序 always 目标用于识别寄存器输出时钟。
        set_sequential_targets: set[str] = set()  # 当前 module 的寄存器驱动信号

        # 第一遍只建立信号到驱动类型的事实集合。
        for dict_always in dict_module.get("always", []):

            # 同一 always 的重复左值只计为一个来源信号。
            set_targets = {
                str(str_target)  # 归一化 formatter 返回的目标名
                for str_target in dict_always.get("targets", [])  # 当前 always 驱动目标
            }  # 当前 always 的唯一目标集合

            # formatter 明确认定的组合块进入组合来源集合。
            if bool(dict_always.get("is_combinational")):

                # 组合目标可触发 VG089。
                set_combinational_targets.update(set_targets)

            # 其余块按时序来源收集，后续仍要求 clock 字段可解析。
            else:

                # 时序目标可触发 VG107。
                set_sequential_targets.update(set_targets)

        # 第二遍检查每个时序块实际使用的时钟信号。
        for dict_always in dict_module.get("always", []):

            # 组合块本身没有寄存器时钟管脚语义。
            if bool(dict_always.get("is_combinational")):

                # 当前块不属于时钟使用点。
                continue

            # clock 字段由 formatter AST 从敏感沿中提取。
            str_clock = str(dict_always.get("clock") or "")  # 派生来源检查使用的时钟名称

            # 时序块缺少可解析时钟时不能安全放行。
            if not str_clock:

                # 不确定状态按 active BLOCKER 的 fail-closed 语义处理。
                return inconclusive("时序 always 的时钟来源无法由 formatter AST 确定。")

            # 找到确定时钟后，该规则对当前设计适用。
            bool_applicable = True  # 已确认至少一个可分析的时钟使用点

            # 两条门禁分别选择组合来源集合或寄存器来源集合。
            set_forbidden_sources = (
                set_continuous_targets | set_combinational_targets  # VG089 禁止的组合来源
                if bool_combination_clock  # 当前执行组合派生时钟检查
                else set_sequential_targets  # VG107 禁止的寄存器来源
            )  # 当前门禁禁止作为时钟的信号集合

            # 普通输入时钟不在派生来源集合中，应保持通过。
            if str_clock not in set_forbidden_sources:

                # 当前时钟来源安全，继续检查其他时序块。
                continue

            # always 起始行是 formatter 提供的稳定证据位置。
            int_line = int(dict_always.get("line_start") or 1)  # 派生时钟使用点的一基行号

            # 诊断文本明确区分组合派生和寄存器派生。
            str_message = (
                "组合逻辑输出被用作时钟。"  # 指出门控或组合网络产生的时钟
                if bool_combination_clock  # 当前检查组合逻辑来源
                else "寄存器输出被用作其他寄存器的时钟。"  # 指出级联寄存器时钟来源
            )  # 当前时钟门禁的用户诊断

            # finding 保留文件、行号、诊断和具体时钟名。
            list_findings.append(VgFinding(source_facts.relative_path, int_line, str_message, str_clock))

    # 任一确定派生时钟都使当前固定门禁失败。
    if list_findings:

        # 失败结论保留全部时钟使用点。
        return failed(*list_findings)

    # 没有违规时仍报告规则是否实际检查过时序块。
    return passed(applicable=bool_applicable)

# _single_clock_edge 禁止同一时钟同时作为上升沿和下降沿触发源。
def _single_clock_edge(facts: VgFacts) -> VgEvaluation:
    """检查同一时钟是否混合使用 posedge 与 negedge。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        双边沿使用的失败结论，或单边沿设计的适用通过结论。
    """

    # findings 汇总单个 module 内部确认的双边沿冲突。
    list_findings: list[VgFinding] = []  # module-local 双边沿时钟证据

    # unknown 标记无法从时序块文本确认边沿的情况。
    bool_unknown = False  # 是否存在边沿信息不完整的时序块

    # applicable 记录是否提取到至少一条确定时钟边沿。
    bool_applicable = False  # 是否存在可检查的时钟边沿事实

    # 逐 module 读取时序 always 的 clock 和 text 字段。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 同名端口在不同 module 中不是同一物理时钟，边沿集合必须局部建立。
        dict_module_edges: dict[str, set[str]] = {}  # 当前 module 的时钟边沿集合

        # 当前 module 内首次位置用于局部冲突定位。
        dict_module_locations: dict[str, tuple[str, int]] = {}  # 当前 module 的时钟位置映射

        # 每个时序块独立提取与 clock 字段对应的边沿关键字。
        for dict_always in dict_module.get("always", []):

            # 组合过程块不参与时钟边沿规则。
            if bool(dict_always.get("is_combinational")):

                # 当前块没有 posedge/negedge 时钟语义。
                continue

            # formatter 提供当前时序块的时钟信号名。
            str_clock = str(dict_always.get("clock") or "")  # 当前边沿触发的时钟名称

            # header 与 lines 是 formatter AST 暴露的稳定过程块文本字段。
            str_always_text = "\n".join(  # 当前时序 always 的可信文本
                [
                    str(dict_always.get("header") or ""),  # always 敏感列表头部
                    *[str(str_line) for str_line in dict_always.get("lines", []) or []],  # 过程块正文行
                ]
            )

            # 时钟名称缺失时不能安全比较边沿集合。
            if not str_clock:

                # 标记未知时钟边沿事实。
                bool_unknown = True  # 当前时序块缺少时钟名称

                # 无时钟名的过程块不能加入边沿映射。
                continue

            # 只匹配当前 formatter 时钟字段对应的边沿声明。
            obj_edge_match = re.search(  # 当前时钟边沿匹配结果
                rf"\b(posedge|negedge)\s+{re.escape(str_clock)}\b",  # 只接受显式边沿与目标时钟名
                str_always_text,  # formatter AST 导出的 always 可信文本
                flags=re.IGNORECASE,  # Verilog 关键字按大小写无关匹配
            )

            # 缺少原始边沿关键字时保持未知。
            if obj_edge_match is None:

                # 时序块存在但边沿类型无法确定。
                bool_unknown = True  # 当前时钟缺少可靠边沿类型

                # 当前块不能形成确定边沿事实。
                continue

            # 把当前边沿加入当前 module 对应时钟的集合。
            dict_module_edges.setdefault(str_clock, set()).add(obj_edge_match.group(1).lower())

            # 已提取到确定时钟边沿时标记规则适用。
            bool_applicable = True  # 至少一个 module 提供确定边沿

            # 首次位置用于双边沿冲突报告。
            dict_module_locations.setdefault(
                str_clock,
                (source_facts.relative_path, int(dict_always.get("line_start") or 1)),
            )

        # 当前 module 的双边沿时钟逐一形成 finding。
        list_findings.extend(  # 当前 module 内同一时钟混用两个边沿的证据
            VgFinding(  # 当前双边沿时钟的诊断对象
                dict_module_locations[str_clock][0],  # 时钟首次出现文件
                dict_module_locations[str_clock][1],  # 时钟首次出现行号
                "避免同时使用同一时钟的上升沿和下降沿。",  # 双边沿混用诊断文本
                str_clock,  # 混用双边沿的时钟名称
            )
            for str_clock, set_edges in sorted(dict_module_edges.items())  # 稳定遍历当前 module 边沿集合
            if {"posedge", "negedge"} <= set_edges  # 当前 module 的同一时钟同时出现两个边沿
        )

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

    # 每个 module 独立建立时钟身份并检查本模块数据路径。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 当前 module 的时钟集合只来自本模块时序 always。
        set_module_clocks = {  # 当前 module 已识别的时钟名称
            str(dict_always["clock"])  # formatter 确认的时钟标识符
            for dict_always in dict_module.get("always", []) or []  # 遍历当前 module 过程块
            if not bool(dict_always.get("is_combinational")) and dict_always.get("clock")  # 只保留确定时钟字段
        }

        # 没有时钟的 module 不参与时钟数据化判断。
        if not set_module_clocks:

            # 继续检查下一个独立 module。
            continue

        # 当前 module 至少存在一个可审查时钟。
        bool_applicable = True  # 已进入 module-local 时钟用途检查

        # 连续赋值的任一侧出现时钟都属于普通数据路径使用。
        for dict_assign in dict_module.get("assigns", []):

            # formatter assign 通过稳定 lhs/rhs 字段重建可审查文本。
            str_assign_text = (  # 当前连续赋值的规范证据文本
                f"assign {dict_assign.get('lhs') or ''} = {dict_assign.get('rhs') or ''};"  # 左右表达式保持 AST 原值
            )

            # 每个当前 module 时钟分别检查 assign 文本。
            for str_clock in sorted(set_module_clocks):

                # 没有时钟标识符时当前 assign 与规则无关。
                if re.search(rf"\b{re.escape(str_clock)}\b", str_assign_text) is None:

                    # 继续检查其他时钟身份。
                    continue

                # formatter assign 行号提供稳定定位。
                int_line = int(dict_assign.get("line_start") or 1)  # 时钟数据化 assign 行号

                # 记录时钟进入连续赋值数据路径的证据。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,
                        int_line,
                        "时钟信号只能连接到寄存器时钟管脚。",
                        str_assign_text,
                    )
                )

        # 命名实例端口可通过端口名区分 clock 与普通数据连接。
        for str_clock in sorted(set_module_clocks):

            # 捕获连接当前时钟信号的命名端口。
            for obj_match in re.finditer(rf"\.(\w+)\s*\(\s*{re.escape(str_clock)}\s*\)", str_module_text):

                # 只有完整时钟语义段命名的端口才视为合法时钟管脚。
                if is_clock_name(obj_match.group(1)):

                    # 当前连接明确指向时钟形式端口。
                    continue

                # module 基线结合匹配偏移形成源码行号。
                int_line = int_base_line + str_module_text.count("\n", 0, obj_match.start())  # 时钟进入普通实例端口的行号

                # 记录普通实例端口使用时钟信号的证据。
                list_findings.append(
                    VgFinding(
                        source_facts.relative_path,
                        int_line,
                        "时钟信号连接到了非时钟实例端口。",
                        obj_match.group(0),
                    )
                )

    # 任一普通数据用途都使门禁失败。
    if list_findings:

        # 返回全部非时钟连接证据。
        return failed(*list_findings)

    # 已识别时钟仅出现在合法时钟用途时适用通过。
    return passed(applicable=bool_applicable)
