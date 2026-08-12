"""实现 FSM 初态、默认恢复、可达性和状态数量 RTL VG 门禁。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# re 在 formatter 确认的 module 文本内提取状态机结构。
import re

# deque 对已证明的状态转移图执行稳定广度优先遍历。
from collections import deque

# Callable 描述固定编号到 FSM 规则函数的路由表。
from typing import Callable

# facts 提供可信 module 文本和稳定的证据文件位置。
from .vg_semantic_facts import VgFacts, VgSourceFacts, iter_trusted_modules

# models 统一逐门禁状态和定位证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# reset rules 提供与其余复位门禁一致的名称角色判断。
from .vg_reset_rules import is_reset_name

# evaluate_fsm_gate 把固定编号路由到 FSM 规则实现。
def evaluate_fsm_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行 FSM 规则组中的指定 VG 门禁。

    参数:
        str_gate_id: 当前执行的固定 VG FSM 门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前 FSM 规则的逐门禁结论。
    """

    # 路由表只包含统一 VG 语义引擎分配给本模块的激活编号。
    dict_evaluators: dict[str, Callable[[VgFacts], VgEvaluation]] = {  # 固定编号到 FSM 规则函数的映射
        "VG086": _fsm_has_initial_state,  # 复位初态检查
        "VG094": _fsm_default_reset_regs,  # 非法编码恢复检查
        "VG098": _fsm_no_dead_unreachable,  # 状态可达性检查
        "VG112": _fsm_limit_state_count,  # 状态数量上限检查
        "VG119": _fsm_min_transition_flips,  # 状态转换翻转位数检查
        "VG144": _fsm_strict_three_processes,  # 严格三段式状态机检查
    }

    # engine 已保证编号属于本模块，直接执行唯一对应函数。
    return dict_evaluators[str_gate_id](facts)

# _fsm_strict_three_processes 禁止连续赋值下一状态并要求三个独立过程。
def _fsm_strict_three_processes(facts: VgFacts) -> VgEvaluation:
    """检查 FSM 是否采用状态寄存、组合转移和独立输出三段式结构。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        非三段式状态机的失败证据或适用性结论。
    """

    # findings 汇总每个 module 的三段式结构违规。
    list_findings: list[VgFinding] = []  # 严格三段式状态机违规证据

    # applicable 区分普通数据通路与可识别 FSM。
    bool_applicable = False  # 是否发现状态常量和状态角色信号

    # 每个 module 的状态角色与过程集合必须独立判定。
    for source_facts, dict_module, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 状态常量是进入 FSM 结构门禁的必要事实。
        if not _declared_states(str_module_text):

            # 普通 module 不属于本规则对象。
            continue

        # 当前状态与下一状态支持仓库常用的两组明确别名。
        tuple_roles = _fsm_state_roles(str_module_text)  # 当前 module 的状态角色信号

        # 缺少任一状态角色时不能证明严格三段式结构。
        if tuple_roles is None:

            # 已有状态常量使规则适用，并形成确定结构失败。
            bool_applicable = True  # 当前 module 是不完整 FSM 候选

            # 状态角色缺失时定位到首个状态常量。
            list_findings.append(
                _finding(
                    source_facts,
                    str_module_text,
                    int_base_line,
                    "ST_",
                    "FSM 必须显式声明当前状态和下一状态寄存器。",
                    "missing current/next state roles",
                )
            )

            # 当前 module 无法继续建立三类过程。
            continue

        # 解包已确认存在的当前状态和下一状态名称。
        str_current_state, str_next_state = tuple_roles  # 当前状态与下一状态信号

        # 完整状态角色说明严格三段式规则正式适用。
        bool_applicable = True  # 当前 module 是可识别 FSM

        # 两个状态角色都必须声明为可由过程块驱动的 reg。
        set_reg_names = {  # 收集当前模块内可作为当前状态和下一状态存储单元的全部 reg 名称
            str(dict_decl.get("name") or "")  # 读取可由 FSM 过程块赋值的内部信号名称
            for dict_decl in dict_module.get("decls", [])  # 逐条检查当前 module 的内部信号声明
            if str(dict_decl.get("kind") or "").lower() == "reg"  # 状态存储候选必须明确声明为 reg 类型
        }

        # wire 状态角色不能满足严格过程式三段结构。
        if not {str_current_state, str_next_state} <= set_reg_names:

            # 诊断明确列出缺少 reg 声明的状态角色。
            set_missing_regs = {str_current_state, str_next_state} - set_reg_names  # 非 reg 状态角色

            # 状态声明违规定位到首个缺失名称。
            list_findings.append(
                _finding(
                    source_facts,
                    str_module_text,
                    int_base_line,
                    sorted(set_missing_regs)[0],
                    "FSM 当前状态和下一状态必须声明为 reg 并由过程块赋值。",
                    ", ".join(sorted(set_missing_regs)),
                )
            )

        # 连续赋值列表由 formatter AST 提供，避免正文误匹配。
        list_assigns = list(dict_module.get("assigns", []))  # 当前 module 的连续赋值集合

        # 下一状态禁止由任何连续 assign 驱动。
        list_next_assigns = [  # 驱动下一状态的连续赋值
            dict_assign  # 当前连续赋值事实
            for dict_assign in list_assigns  # 遍历当前 module 全部 assign
            if _lvalue_base(str(dict_assign.get("lhs") or "")) == str_next_state  # 只选择下一状态目标
        ]

        # 连续下一状态赋值直接违反用户确认的硬门禁。
        if list_next_assigns:

            # 首条 assign 的 AST 行号用于精确定位。
            int_line = int(list_next_assigns[0].get("line_start") or int_base_line)  # 连续赋值文件行号

            # 报告禁止形态和目标信号。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "FSM 下一状态禁止使用 continuous assign，必须放在独立组合过程。",
                    f"assign {str_next_state} = ...",
                )
            )

        # formatter always 事实用于验证三个过程相互独立。
        list_always = list(dict_module.get("always", []))  # 当前 module 的全部过程块

        # 第一段必须是时序过程且只承担当前状态更新。
        list_state_registers = [  # 当前状态时序寄存过程
            dict_always  # 可能承担组合状态转移职责的过程事实
            for dict_always in list_always  # 第二段只从组合触发且写下一状态的过程产生
            if str(dict_always.get("trigger_kind") or "") != "comb"  # 排除组合过程
            and str_current_state in _always_target_bases(dict_always)  # 必须写当前状态
            and str_next_state not in _always_target_bases(dict_always)  # 不得同时写下一状态
        ]

        # 第二段必须是只写下一状态的组合过程。
        list_next_logic = [  # 下一状态组合转移过程
            dict_always  # 可能承担状态输出或任务职责的过程事实
            for dict_always in list_always  # 从全部过程筛选第三段状态逻辑
            if str(dict_always.get("trigger_kind") or "") == "comb"  # 必须是组合过程
            and str_next_state in _always_target_bases(dict_always)  # 必须写下一状态
            and str_current_state not in _always_target_bases(dict_always)  # 不得反写当前状态
        ]

        # 第三段必须引用状态且写入状态角色以外的输出或任务信号。
        list_output_logic = [  # 状态输出或任务过程
            dict_always  # 当前候选过程块
            for dict_always in list_always  # 遍历全部过程块
            if _always_references_signal(dict_always, str_current_state)  # 必须消费当前状态
            and bool(_always_target_bases(dict_always) - {str_current_state, str_next_state})  # 必须写其他信号
            and str_current_state not in _always_target_bases(dict_always)  # 第三段不得更新当前状态
            and str_next_state not in _always_target_bases(dict_always)  # 第三段不得更新下一状态
        ]

        # 三类过程必须各自存在，并且由三个不同 always 块承担。
        bool_three_processes = bool(list_state_registers and list_next_logic and list_output_logic)  # 三段职责完整性

        # 缺少任一独立段时形成确定阻断证据。
        if not bool_three_processes:

            # 诊断列出三类过程的识别数量，便于直接修复。
            str_evidence = (  # 三类过程识别数量
                f"state_register={len(list_state_registers)}, "
                f"next_logic={len(list_next_logic)}, "
                f"output_logic={len(list_output_logic)}"
            )

            # 状态机结构失败统一定位到当前状态信号。
            list_findings.append(
                _finding(
                    source_facts,
                    str_module_text,
                    int_base_line,
                    str_current_state,
                    "FSM 必须由独立的状态寄存、组合下一状态和状态输出三个过程组成。",
                    str_evidence,
                )
            )

    # 任一 module 违反严格三段式合同都使门禁失败。
    if list_findings:

        # 返回全部可定位的三段式结构违规。
        return failed(*list_findings)

    # 没有 FSM 时不适用，其余情况通过。
    return passed(applicable=bool_applicable)

# _fsm_state_roles 返回当前 module 中可识别的状态角色名称。
def _fsm_state_roles(str_module_text: str) -> tuple[str, str] | None:
    """识别当前状态和下一状态的明确命名对。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        当前状态与下一状态名称；缺少明确命名对时返回 None。
    """

    # 命名对按仓库推荐形式优先，再兼容常见 current_state 形式。
    tuple_aliases = (  # 可接受的当前状态与下一状态命名对
        ("state_current", "state_next"),  # 仓库推荐命名
        ("current_state", "next_state"),  # 常见等价命名
        ("state_q", "state_d"),  # 寄存器数据端命名
    )

    # 只有两个名称都在 module 中作为独立标识符出现才返回。
    for str_current, str_next in tuple_aliases:

        # 当前命名对必须同时存在。
        if re.search(rf"\b{re.escape(str_current)}\b", str_module_text) and re.search(
            rf"\b{re.escape(str_next)}\b",
            str_module_text,
        ):

            # 返回首个按优先级匹配的明确命名对。
            return str_current, str_next

    # 没有明确状态角色时交由调用方形成失败结论。
    return None

# _always_target_bases 返回过程块的左值基名集合。
def _always_target_bases(dict_always: dict[str, object]) -> set[str]:
    """提取一个 always 块写入的信号基名。

    参数:
        dict_always: formatter AST 中的过程块事实。
    返回:
        当前过程块全部左值基名集合。
    """

    # targets 已由 formatter 聚合过程块内全部赋值目标。
    return {  # 当前过程块的左值基名集合
        _lvalue_base(str(str_target))  # 位选和切片统一到声明基名
        for str_target in dict_always.get("targets", [])  # 遍历 formatter 目标集合
    }

# _always_references_signal 判断过程源码是否消费指定信号。
def _always_references_signal(dict_always: dict[str, object], str_signal: str) -> bool:
    """判断一个 always 块是否引用指定信号。

    参数:
        dict_always: formatter AST 中的过程块事实。
        str_signal: 需要确认的状态信号名称。
    返回:
        过程正文引用该信号时返回 True。
    """

    # lines 保留控制条件和表达式，适合精确标识符匹配。
    str_body = "\n".join(str(str_line) for str_line in dict_always.get("lines", []))  # 当前过程正文

    # 独立词边界防止状态名子串误命中。
    return re.search(rf"\b{re.escape(str_signal)}\b", str_body) is not None

# _lvalue_base 返回左值表达式的声明基名。
def _lvalue_base(str_lvalue: str) -> str:
    """提取左值位选、切片或简单名称的基名。

    参数:
        str_lvalue: formatter 返回的左值表达式。
    返回:
        左值开头的标识符基名；无法识别时返回空字符串。
    """

    # 左值必须以普通 Verilog 标识符开头。
    obj_match = re.match(r"\s*([A-Za-z_]\w*)", str_lvalue)  # 左值基名匹配

    # 缺失标识符时返回空文本，避免伪造角色。
    return obj_match.group(1) if obj_match else ""

# _fsm_min_transition_flips 检查首个可证明状态转换的编码翻转量。
def _fsm_min_transition_flips(facts: VgFacts) -> VgEvaluation:
    """检查可解析 FSM 转换是否采用低翻转编码。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        高翻转转换的失败证据或适用性结论。
    """

    # findings 汇总高翻转状态转换证据。
    list_findings: list[VgFinding] = []  # 高翻转状态转换证据

    # applicable 区分无 FSM 输入与检查后合规。
    bool_applicable = False  # 是否识别出至少两个定宽状态常量

    # 状态模式捕获名称、声明宽度和二进制编码。
    str_state_pattern = (  # 状态名、声明宽度和二进制编码捕获模式
        r"\b(?:localparam|parameter)\s*(?:\[[^\]]+\]\s*)?"
        r"([A-Za-z_]\w*)\s*=\s*(\d+)'[bB]([01_]+)"
    )

    # 每个可信 module 独立构造状态常量和赋值目标序列。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 当前 module 的状态常量形成局部编码表。
        dict_states = {  # 状态名称到无分隔符二进制编码的映射
            obj_match.group(1): obj_match.group(3).replace("_", "")  # 状态名对应规范编码
            for obj_match in re.finditer(str_state_pattern, str_module_text)  # 遍历当前 module 状态声明
        }

        # 少于两个状态时不存在可比较转换。
        if len(dict_states) < 2:

            # 继续检查下一个可信 module。
            continue

        # 只保留状态寄存器非阻塞赋值中的声明状态名称。
        # 先提取所有状态寄存器的非阻塞赋值目标。
        list_destinations = re.findall(  # 状态寄存器赋值目标序列
            r"\bstate\w*\s*<=\s*([A-Za-z_]\w*)\s*;",  # 状态寄存器赋值模式
            str_module_text,  # 当前 formatter 可信 module 文本
            flags=re.IGNORECASE,  # 状态寄存器命名不区分大小写
        )

        # 只保留已在当前 module 声明的确定状态名称。
        list_destinations = [str_name for str_name in list_destinations if str_name in dict_states]  # 已声明状态序列

        # 需要至少两个不同目标才能形成可比较转换。
        if len(set(list_destinations)) < 2:

            # 当前 module 没有足够的确定转换证据。
            continue

        # 当前批次以稳定出现顺序选择首个可证明转换。
        # 已发现可比较状态转换。
        bool_applicable = True  # 当前规则具有实际检查对象

        # 稳定序列的首个状态作为源状态。
        str_source_state = list_destinations[0]  # 首个转换的源状态

        # 稳定序列的第二个状态作为目标状态。
        str_destination_state = list_destinations[1]  # 首个转换的目标状态

        # 查询源状态的二进制编码。
        str_source_bits = dict_states[str_source_state]  # 源状态二进制编码

        # 查询目标状态的二进制编码。
        str_destination_bits = dict_states[str_destination_state]  # 目标状态二进制编码

        # 同位置编码比较得到翻转总数。
        int_flips = sum(  # 同位置编码比特的翻转总数
            str_left != str_right  # 当前编码位置是否发生翻转
            for str_left, str_right in zip(str_source_bits, str_destination_bits)  # 对齐比较两侧编码
        )

        # 全部状态只有一个有效位时采用 one-hot 翻转上限。
        bool_one_hot = all(str_bits.count("1") == 1 for str_bits in dict_states.values())  # 是否为 one-hot 编码

        # one-hot 转换允许一位撤销和一位置位，其他编码只允许一位变化。
        int_allowed_flips = 2 if bool_one_hot else 1  # 当前编码允许的最大翻转数

        # 只有同宽编码才能执行确定的逐位比较。
        if len(str_source_bits) == len(str_destination_bits) and int_flips > int_allowed_flips:

            # 目标状态首次出现位置用于生成稳定定位。
            obj_match = re.search(rf"\b{re.escape(str_destination_state)}\b", str_module_text)  # 目标状态首个位置

            # 缺失匹配时保守回退到 module 起点。
            int_match_offset = obj_match.start() if obj_match else 0  # 缺失定位时回退 module 起点

            # 文本偏移换算为一基文件行号。
            int_line = int_base_line + str_module_text.count("\n", 0, int_match_offset)  # 转换证据一基行号

            # 报告确定超出编码策略的状态转换。
            list_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    f"状态转换同时翻转 {int_flips} 位，超过当前编码允许的 {int_allowed_flips} 位。",
                    f"{str_source_state}->{str_destination_state}",
                )
            )

    # 任一高翻转转换都使本门禁失败。
    if list_findings:

        # 返回全部高翻转状态转换证据。
        return failed(*list_findings)

    # 没有违规时报告规则是否实际比较过状态转换。
    return passed(applicable=bool_applicable)

# _fsm_has_initial_state 验证复位路径上的常量状态入口。
def _fsm_has_initial_state(facts: VgFacts) -> VgEvaluation:
    """检查状态寄存器是否在复位路径进入声明的常量初态。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        初态缺失时的失败结论或已检查通过结论。
    """

    # findings 保存缺少可信复位初态的 FSM 证据。
    list_findings: list[VgFinding] = []  # 当前规则的违规证据

    # 出现 ST_* 常量时规则才具有适用性。
    bool_applicable = False  # 当前目标是否包含可识别 FSM

    # 每个 formatter 确认的 module 独立检查复位入口。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 状态集合只来自可信 module 范围内的参数声明。
        set_states = _declared_states(str_module_text)  # 当前 module 的状态常量

        # 没有状态常量的 module 不属于本规则对象。
        if not set_states:

            # 继续处理下一个可信 module。
            continue

        # 当前 module 已出现可识别状态机结构。
        bool_applicable = True  # 标记规则具有实际检查对象

        # 复位入口必须是状态集合中的一个常量。
        str_initial_state = _reset_initial_state(str_module_text)  # 当前复位入口状态

        # 已声明的常量初态满足规则。
        if str_initial_state in set_states:

            # 继续检查其他状态机。
            continue

        # 缺少常量初态时保留 state_current 附近的稳定位置。
        list_findings.append(
            _finding(
                source_facts,
                str_module_text,
                int_base_line,
                "state_current",
                "FSM 状态寄存器必须在复位路径进入已声明的常量初态。",
                str_initial_state or "missing constant reset state",
            )
        )

    # 任一状态机缺少初态都形成确定失败。
    if list_findings:

        # 返回全部可定位的初态违规。
        return failed(*list_findings)

    # 没有违规时保留规则是否实际消费了 FSM 事实。
    return passed(applicable=bool_applicable)

# _fsm_default_reset_regs 验证非法编码的确定恢复路径。
def _fsm_default_reset_regs(facts: VgFacts) -> VgEvaluation:
    """检查状态 case 的 default 分支是否恢复到确定状态。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        default 恢复违规、事实不足或通过结论。
    """

    # 独立列表汇总 default 保持未知态或缺失分支的诊断。
    list_findings: list[VgFinding] = []  # 默认恢复规则的违规证据

    # default 检查只对包含状态声明的 module 生效。
    bool_applicable = False  # 是否发现默认恢复检查对象

    # 每个 formatter 确认的 module 独立检查默认分支。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 状态集合限制 default 恢复目标的合法范围。
        set_states = _declared_states(str_module_text)  # default 允许恢复到的状态集合

        # 普通数据通路没有状态声明，无需检查非法编码恢复。
        if not set_states:

            # 跳过当前非状态机 module。
            continue

        # 已声明状态说明 default 恢复合同适用于当前 module。
        bool_applicable = True  # 已进入状态恢复检查路径

        # case 主体必须明确选择 state_current。
        str_case_body = _state_case_body(str_module_text)  # 当前状态转移 case 主体

        # 缺少可信 case 时不得把状态常量误判为合规状态机。
        if str_case_body is None:

            # 返回事实不足结论而非伪造通过。
            return inconclusive("发现 FSM 状态常量，但无法建立 case(state_current) 事实。")

        # default 文本和恢复状态分别保留用于判定与证据。
        str_default_body = _default_branch_body(str_case_body)  # 当前 default 分支文本

        # 常量恢复目标必须来自已声明状态集合。
        str_recovery_state = _assigned_constant_state(str_default_body or "")  # 当前默认恢复状态

        # 已声明的确定恢复状态满足规则。
        if str_recovery_state in set_states:

            # 当前 default 合规，转向后续 module。
            continue

        # default 未恢复到常量状态时形成确定违规。
        list_findings.append(
            _finding(
                source_facts,
                str_module_text,
                int_base_line,
                "default",
                "FSM default 分支必须把 state_next 恢复到已声明的确定状态。",
                str_default_body.strip() if str_default_body else "missing default branch",
            )
        )

    # 任一 default 恢复违规都形成确定失败。
    if list_findings:

        # 返回全部可定位的恢复违规。
        return failed(*list_findings)

    # 默认恢复规则无命中时返回其实际适用范围。
    return passed(applicable=bool_applicable)

# _fsm_no_dead_unreachable 验证复位初态到全部状态的转移闭包。
def _fsm_no_dead_unreachable(facts: VgFacts) -> VgEvaluation:
    """检查所有声明状态是否能从复位初态到达并具有转移。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        死状态、不可达状态、事实不足或通过结论。
    """

    # 图诊断列表同时容纳不可达节点和缺少后继边的节点。
    list_findings: list[VgFinding] = []  # 状态图规则的违规证据

    # 可达性规则只在发现状态节点时进入适用态。
    bool_applicable = False  # 是否发现可建立图的 FSM 候选

    # 每个 formatter 确认的 module 独立建立状态图。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 状态集合定义本轮图分析的节点全集。
        set_states = _declared_states(str_module_text)  # 当前图分析的节点全集

        # 空节点集合无法形成状态图，也不构成违规。
        if not set_states:

            # 转向下一个可能包含状态声明的 module。
            continue

        # 非空节点集合使状态图规则正式适用。
        bool_applicable = True  # 已发现状态图分析对象

        # 初态是图遍历的唯一起点。
        str_initial_state = _reset_initial_state(str_module_text)  # 可达性遍历的入口状态

        # 状态 case 提供本轮图的全部候选边。
        str_case_body = _state_case_body(str_module_text)  # 图边提取使用的 case 主体

        # 任一关键事实缺失时不能建立可信有向图。
        if str_initial_state not in set_states or str_case_body is None:

            # 返回事实不足结论而非猜测图结构。
            return inconclusive("FSM 缺少可信复位初态或 case(state_current) 转移图。")

        # 转移字典只保留声明状态之间的明确 state_next 赋值。
        dict_transitions = _state_transitions(str_case_body, set_states)  # 当前 FSM 有向图

        # 广度优先遍历计算复位入口的可达闭包。
        set_reachable = _reachable_states(str_initial_state, dict_transitions)  # 初态可达状态集合

        # 声明但不在闭包中的节点属于确定不可达状态。
        set_unreachable = set_states - set_reachable  # 当前 FSM 的不可达状态

        # 没有任何已声明后继边的节点属于确定死状态。
        set_without_transition = {  # 当前 FSM 没有后继边的状态
            str_state  # 当前待检查状态
            for str_state in set_states  # 遍历全部声明状态
            if not dict_transitions.get(str_state)  # 选择没有可信后继边的状态
        }

        # 合并集合避免同一状态重复报告死状态和不可达状态。
        for str_state in sorted(set_unreachable | set_without_transition):

            # 状态名本身提供稳定且最小的图违规证据。
            list_findings.append(
                _finding(
                    source_facts,
                    str_module_text,
                    int_base_line,
                    str_state,
                    "FSM 状态必须能从复位初态到达且具有可证明的后继转移。",
                    str_state,
                )
            )

    # 任一死状态或不可达状态都形成确定失败。
    if list_findings:

        # 返回全部可定位的状态图违规。
        return failed(*list_findings)

    # 状态图闭合时返回本轮是否实际分析过节点。
    return passed(applicable=bool_applicable)

# _fsm_limit_state_count 验证单个状态机的规模边界。
def _fsm_limit_state_count(facts: VgFacts) -> VgEvaluation:
    """检查单个 FSM 的声明状态数量不超过四十。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        状态超量时的失败结论或已检查通过结论。
    """

    # 规模诊断只保存状态数量越过边界的 module。
    list_findings: list[VgFinding] = []  # 状态规模规则的违规证据

    # 数量规则只统计明确声明的状态常量。
    bool_applicable = False  # 是否发现可计数的状态集合

    # 每个 formatter 确认的 module 分别统计状态数量。
    for source_facts, _, str_module_text, int_base_line in iter_trusted_modules(facts):

        # 状态集合按名称去重，避免重复文本放大计数。
        set_states = _declared_states(str_module_text)  # 当前 module 的去重计数集合

        # 没有状态节点时数量为零且规则不适用。
        if not set_states:

            # 跳过当前无状态声明的 module。
            continue

        # 非空集合进入四十状态边界判断。
        bool_applicable = True  # 已统计至少一个 FSM 状态集合

        # 四十及以下状态满足当前规模边界。
        if len(set_states) <= 40:

            # 当前规模合规，继续统计其他 module。
            continue

        # 第四十一个排序状态提供稳定定位词元。
        str_overflow_state = sorted(set_states)[40]  # 首个超过数量边界的状态

        # 超量状态机形成一条带实际计数的确定证据。
        list_findings.append(
            _finding(
                source_facts,
                str_module_text,
                int_base_line,
                str_overflow_state,
                "单个 FSM 的状态数量不得超过四十。",
                f"state_count={len(set_states)}",
            )
        )

    # 任一状态机超量都形成确定失败。
    if list_findings:

        # 返回全部可定位的规模违规。
        return failed(*list_findings)

    # 全部状态集合在边界内时报告实际计数适用性。
    return passed(applicable=bool_applicable)

# _declared_states 提取参数声明中的状态节点全集。
def _declared_states(str_module_text: str) -> set[str]:
    """返回 module 中由 parameter 或 localparam 声明的 ST_* 状态名。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        去重后的状态常量名称集合。
    """

    # 集合按状态名去重并隔离不同声明语句。
    set_states: set[str] = set()  # 参数声明中累计得到的状态名称

    # 只在参数声明的分号边界内提取 ST_* 赋值名称。
    for obj_declaration in re.finditer(
        r"\b(?:localparam|parameter)\b(?P<body>[^;]*);",
        str_module_text,
        flags=re.IGNORECASE,  # 参数关键字大小写不敏感
    ):

        # 当前声明可能包含一个或多个逗号分隔状态常量。
        list_matches = re.findall(  # 当前声明中的状态赋值名称
            r"\b(ST_[A-Za-z0-9_]+)\s*=",  # 状态常量赋值模式
            obj_declaration.group("body"),  # 当前参数声明主体
        )

        # 合并当前声明中已证明的状态名称。
        set_states.update(list_matches)

    # 返回供四条 FSM 规则共享的节点集合。
    return set_states

# _reset_initial_state 提取复位分支的常量状态入口。
def _reset_initial_state(str_module_text: str) -> str | None:
    """返回复位条件直接赋给 state_current 的常量状态。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        已证明的 ST_* 初态；无法证明时返回空值。
    """

    # 候选模式只要求 if 分支直接赋予 state_current 一个 ST_* 常量。
    str_pattern = (  # 复位初态候选分支模式
        r"\bif\s*\((?P<condition>[^)]*)\)"
        r"\s*(?:begin\b\s*)?\s*state_current\s*<=\s*(?P<state>ST_[A-Za-z0-9_]+)\s*;"
    )

    # 每个候选条件都通过共享复位角色函数核验标识符边界。
    for obj_match in re.finditer(str_pattern, str_module_text, flags=re.IGNORECASE):

        # 条件标识符集合排除操作符和数值字面量。
        tuple_identifiers = tuple(  # 当前 if 条件中的 Verilog 标识符
            re.findall(r"[A-Za-z_]\w*", obj_match.group("condition"))  # 条件内候选控制名称
        )

        # 只有明确包含 reset-only 角色的条件才能证明初态。
        if any(is_reset_name(str_identifier) for str_identifier in tuple_identifiers):

            # 返回与可信复位条件直接关联的常量状态。
            return obj_match.group("state")

    # 缺少可信复位候选时保持未知，不推断动态右值。
    return None

# _state_case_body 限定状态转移分支的可信词法范围。
def _state_case_body(str_module_text: str) -> str | None:
    """返回 case(state_current) 的可信文本主体。

    参数:
        str_module_text: formatter 确认边界的 module 原文。
    返回:
        状态 case 主体；无法定位时返回空值。
    """

    # case 与 endcase 边界必须完整存在且选择 state_current。
    obj_match = re.search(  # 当前 module 的状态 case 匹配结果
        r"\bcase\s*\(\s*state_current\s*\)(?P<body>.*?)\bendcase\b",  # 状态 case 边界模式
        str_module_text,  # 待匹配状态 case 的 module 文本
        flags=re.IGNORECASE | re.DOTALL,  # 允许状态 case 跨行匹配
    )

    # 只返回 formatter module 范围内的完整 case 主体。
    return obj_match.group("body") if obj_match else None

# _default_branch_body 提取状态 case 的非法编码处理分支。
def _default_branch_body(str_case_body: str) -> str | None:
    """返回状态 case 的 default 分支文本。

    参数:
        str_case_body: 已确认的 case(state_current) 主体。
    返回:
        default 分支主体；无法定位时返回空值。
    """

    # 下一状态标签或 case 结尾限定 default 分支范围。
    obj_match = re.search(  # 当前状态 case 的 default 分支匹配结果
        r"^\s*default\s*:\s*(?P<body>.*?)(?=^\s*(?:ST_[A-Za-z0-9_]+|default)\s*:|\Z)",  # 默认分支范围模式
        str_case_body,  # 待匹配 default 的状态 case 主体
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,  # 按行识别 default 标签
    )

    # 未出现 default 时返回空值供规则形成确定违规。
    return obj_match.group("body") if obj_match else None

# _assigned_constant_state 提取分支中的常量恢复目标。
def _assigned_constant_state(str_branch_body: str) -> str | None:
    """返回分支中赋给 state_next 的首个 ST_* 常量。

    参数:
        str_branch_body: 当前 case 分支的可信文本。
    返回:
        已证明的 ST_* 目标；无法定位时返回空值。
    """

    # 只接受 state_next 的直接阻塞赋值，不解释复杂表达式。
    obj_match = re.search(  # 当前分支的常量下一状态匹配结果
        r"\bstate_next\s*=\s*(ST_[A-Za-z0-9_]+)\s*;",  # 常量下一状态赋值模式
        str_branch_body,  # 待匹配常量恢复的分支主体
        flags=re.IGNORECASE,  # 状态常量名称大小写不敏感
    )

    # 动态或缺失右值保持未知状态。
    return obj_match.group(1) if obj_match else None

# _state_transitions 建立声明状态之间的有向图。
def _state_transitions(str_case_body: str, set_states: set[str]) -> dict[str, set[str]]:
    """从状态 case 分支建立声明状态之间的有向边。

    参数:
        str_case_body: 已确认的 case(state_current) 主体。
        set_states: 当前 module 已声明的状态集合。
    返回:
        每个声明状态到其已证明后继状态的映射。
    """

    # 每个声明状态预先获得空邻接集合，便于识别死状态。
    dict_transitions: dict[str, set[str]] = {  # 当前 FSM 的状态邻接表
        str_state: set()  # 当前状态尚未发现后继边
        for str_state in set_states  # 为每个声明节点创建邻接集合
    }

    # 每个 ST_* 标签的主体由下一个标签或 case 结尾限定。
    for obj_branch in re.finditer(
        r"^\s*(?P<state>ST_[A-Za-z0-9_]+)\s*:\s*(?P<body>.*?)"
        r"(?=^\s*(?:ST_[A-Za-z0-9_]+|default)\s*:|\Z)",
        str_case_body,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    ):

        # 分支标签是当前有向边的源节点。
        str_source_state = obj_branch.group("state")  # 当前分支的源状态

        # 未声明标签不进入本 FSM 的可信状态图。
        if str_source_state not in set_states:

            # 继续处理下一个状态分支。
            continue

        # 分支内所有直接常量赋值共同构成保守后继集合。
        set_targets = set(  # 当前分支已证明的常量后继状态
            re.findall(  # 当前分支中的后继状态集合
                r"\bstate_next\s*=\s*(ST_[A-Za-z0-9_]+)\s*;",  # 分支内后继状态赋值模式
                obj_branch.group("body"),  # 当前状态标签的分支主体
                flags=re.IGNORECASE,  # 后继状态名称大小写不敏感
            )
        )

        # 只保留声明状态之间的边，排除未知常量名称。
        dict_transitions[str_source_state].update(set_targets & set_states)

    # 返回供可达性遍历使用的完整邻接表。
    return dict_transitions

# _reachable_states 计算复位入口的图可达闭包。
def _reachable_states(str_initial_state: str, dict_transitions: dict[str, set[str]]) -> set[str]:
    """返回从复位初态沿已证明转移边可达的状态集合。

    参数:
        str_initial_state: 已证明的复位入口状态。
        dict_transitions: 声明状态之间的有向邻接表。
    返回:
        包含初态在内的全部可达状态集合。
    """

    # 初态自身始终属于可达闭包。
    set_reachable: set[str] = {str_initial_state}  # 已发现的可达状态

    # 队列只保存尚未展开后继边的状态。
    deque_pending: deque[str] = deque([str_initial_state])  # 待遍历状态队列

    # 广度优先遍历直到没有新的可达节点。
    while deque_pending:

        # 队首状态是本轮需要展开的源节点。
        str_state = deque_pending.popleft()  # 当前展开状态

        # 遍历当前状态所有已证明的后继边。
        for str_target in dict_transitions.get(str_state, set()):

            # 已访问节点无需重复入队。
            if str_target in set_reachable:

                # 继续检查下一个后继状态。
                continue

            # 首次发现的节点加入可达闭包。
            set_reachable.add(str_target)

            # 新节点入队以继续展开其后继边。
            deque_pending.append(str_target)

    # 队列耗尽后的集合就是初态可达闭包。
    return set_reachable

# _finding 统一 FSM 规则的路径和行号定位策略。
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

    # 首个词元偏移用于把 module 局部位置换算到文件行号。
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
