"""实现信号声明与驱动来源相关 RTL PG 门禁。"""

# future annotations 延后解析规则模型类型。
from __future__ import annotations

# re 用于从可信 module 原文恢复 output 端口的 net 类型。
import re

# facts 提供 formatter AST 确认的 module 结构。
from .rtl_pg_facts import PgFacts, iter_trusted_modules

# models 统一逐门禁结论与证据格式。
from .rtl_pg_models import PgEvaluation, PgFinding, failed, passed

# evaluate_driver_gate 把固定编号路由到三条驱动规则。
def evaluate_driver_gate(str_gate_id: str, facts: PgFacts) -> PgEvaluation:
    """执行过程赋值、多驱动和 wire 初始化门禁。

    参数:
        str_gate_id: 当前执行的固定 PG 驱动门禁编号。
        facts: formatter AST 构建的可信扫描事实。
    返回:
        当前驱动规则的逐门禁结论。
    """

    # PG1069 专门检查过程块对 net 类型的驱动。
    if str_gate_id == "PG1069":

        # wire 过程赋值需要独立声明类型事实。
        return _procedural_wire_assignment(facts)

    # PG1070 聚合连续赋值和 always 两类独立来源。
    if str_gate_id == "PG1070":

        # 多驱动检查按 module 隔离同名信号。
        return _multiple_drivers(facts)

    # 本模块剩余固定入口为 PG1071。
    return _wire_inline_assignment(facts)

# _procedural_wire_assignment 检查 always 目标与 wire 声明交集。
def _procedural_wire_assignment(facts: PgFacts) -> PgEvaluation:
    """检查过程块是否驱动 wire 信号。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        PG1069 的确定性执行结论。
    """

    # findings 保留每个过程驱动使用点。
    list_findings: list[PgFinding] = []  # wire 过程赋值证据集合

    # 声明类型只在当前 module 内有效。
    for source_facts, dict_module, str_module_text, _ in iter_trusted_modules(facts):

        # wire 集合同时覆盖内部 net 和默认或显式 net 类型端口。
        set_wires = _declared_wires(dict_module, str_module_text)  # 当前 module 的 net 类型信号

        # formatter 已把每个 always 的左值整理为 targets。
        for dict_always in dict_module.get("always", []):

            # 同一过程块中的每个目标都要核对声明类型。
            for str_target in dict_always.get("targets", []):

                # reg 类型或未知声明目标不属于本条规则。
                if str(str_target) not in set_wires:

                    # 当前目标无需报告，继续检查其他左值。
                    continue

                # always 起始行提供稳定且保守的过程驱动位置。
                int_line = int(dict_always.get("line_start") or 1)  # 过程块驱动 wire 的一基行号

                # finding 记录具体 wire 名，便于直接修复声明或赋值方式。
                list_findings.append(
                    PgFinding(
                        source_facts.relative_path,  # 违规过程块所在 RTL 文件
                        int_line,  # formatter 提供的过程块起始行
                        "过程块对 wire 信号赋值。",  # PG1069 的用户诊断
                        str(str_target),  # 被过程块驱动的 wire 名称
                    )
                )

    # 至少一个确定交集即触发 PG1069。
    if list_findings:

        # 失败结论保留全部过程驱动证据。
        return failed(*list_findings)

    # 没有过程驱动 wire 时门禁通过。
    return passed(applicable=False)

# _multiple_drivers 按独立 assign/always 来源统计每个左值。
def _multiple_drivers(facts: PgFacts) -> PgEvaluation:
    """检查同一信号是否存在多个独立驱动源。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        PG1070 的确定性执行结论。
    """

    # findings 保存跨来源计数超过一的信号。
    list_findings: list[PgFinding] = []  # 多驱动信号证据集合

    # 每个 module 维护独立计数，避免层级间同名误报。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 字典值表示某信号被多少个独立结构驱动。
        dict_driver_counts: dict[str, int] = {}  # 当前 module 的信号驱动来源计数

        # 每条连续 assign 都是一个独立驱动来源。
        for dict_assign in dict_module.get("assigns", []):

            # lhs 是 formatter 归一化后的连续赋值目标。
            str_target = str(dict_assign.get("lhs") or "")  # 当前 assign 的目标信号

            # 空目标不会形成有效驱动来源。
            if not str_target:

                # formatter 缺失目标时跳过当前条目。
                continue

            # 连续赋值来源计入当前目标。
            dict_driver_counts[str_target] = dict_driver_counts.get(str_target, 0) + 1  # 累加 assign 驱动来源

        # 每个 always 对同一目标最多贡献一个独立来源。
        for dict_always in dict_module.get("always", []):

            # set 去除同一过程块内的重复分支赋值。
            set_targets = {
                str(str_target)  # 归一化当前过程目标名
                for str_target in dict_always.get("targets", [])  # formatter 识别的过程左值
                if str(str_target)  # 排除空目标占位
            }  # 当前 always 的唯一驱动目标

            # 每个目标为当前 always 增加一个来源。
            for str_target in set_targets:

                # 独立过程块来源计入当前目标。
                dict_driver_counts[str_target] = dict_driver_counts.get(str_target, 0) + 1  # 登记当前过程块的独立所有权

        # 只报告来源数量超过一的有效信号。
        for str_target, int_count in dict_driver_counts.items():

            # 单来源信号满足 PG1070。
            if int_count <= 1:

                # 当前目标无需生成多驱动证据。
                continue

            # module 起始行作为跨多个来源的稳定聚合位置。
            int_line = int(dict_module.get("line_start") or 1)  # 多驱动信号所在 module 起始行

            # finding 在 evidence 中保留信号名和来源数量。
            list_findings.append(
                PgFinding(
                    source_facts.relative_path,  # 多驱动信号所在 RTL 文件
                    int_line,  # 当前 module 的一基起始行
                    "同一信号存在多个独立驱动源。",  # 提示合并或移除冲突驱动结构
                    f"{str_target}: {int_count}",  # 目标信号及独立来源数量
                )
            )

    # 任一多驱动信号都使固定门禁失败。
    if list_findings:

        # 返回全部多驱动聚合证据。
        return failed(*list_findings)

    # 没有来源冲突时门禁通过。
    return passed(applicable=False)

# _wire_inline_assignment 读取 formatter 声明的 init 字段。
def _wire_inline_assignment(facts: PgFacts) -> PgEvaluation:
    """检查 wire 声明是否附带初始化表达式。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        PG1071 的确定性执行结论。
    """

    # findings 保存每个声明级初始化位置。
    list_findings: list[PgFinding] = []  # wire 内联初始化证据集合

    # 声明判断只消费 formatter 已识别的 decls。
    for source_facts, dict_module, _, _ in iter_trusted_modules(facts):

        # 逐条核对声明种类和初始化字段。
        for dict_decl in dict_module.get("decls", []):

            # 非 wire 声明不属于 PG1071。
            if str(dict_decl.get("kind") or "").lower() != "wire":

                # 当前声明无需检查内联 wire 初始化。
                continue

            # 空初始化字段代表普通 net 声明。
            if not str(dict_decl.get("init") or "").strip():

                # 当前 wire 没有声明级驱动。
                continue

            # formatter 声明行提供精确证据位置。
            int_line = int(dict_decl.get("line_start") or 1)  # wire 初始化声明的一基行号

            # finding 保留被初始化的具体 net 名。
            list_findings.append(
                PgFinding(
                    source_facts.relative_path,  # wire 声明所在 RTL 文件
                    int_line,  # formatter 提供的声明行号
                    "wire 声明包含内联初始化赋值。",  # 提示拆分声明与连续赋值
                    str(dict_decl.get("name") or ""),  # 被内联赋值的 wire 名称
                )
            )

    # 任一声明级初始化都触发 PG1071。
    if list_findings:

        # 返回全部声明证据，方便批量修复。
        return failed(*list_findings)

    # 没有 wire 内联初始化时门禁通过。
    return passed(applicable=False)

# _declared_wires 汇总内部 net 与默认 net 类型端口。
def _declared_wires(dict_module: dict[str, object], str_module_text: str) -> set[str]:
    """收集端口与内部声明中的 wire 信号。

    参数:
        dict_module: formatter AST 中的单个 module 报告。
        str_module_text: formatter 确认边界内的原始 module 文本。
    返回:
        当前 module 内可确认属于 wire/net 的信号名集合。
    """

    # 内部 wire 声明由 formatter kind 字段确定。
    set_wires = {
        str(dict_decl.get("name") or "")  # 当前内部 net 的信号名
        for dict_decl in dict_module.get("decls", []) or []  # formatter 识别的内部声明
        if str(dict_decl.get("kind") or "").lower() == "wire"  # 只保留 wire 类型
    }  # 当前 module 的内部 wire 集合

    # Verilog input/inout 端口在未声明变量类型时具有 net 语义。
    for dict_port in dict_module.get("ports", []) or []:

        # name 用于与 always targets 做精确集合匹配。
        str_name = str(dict_port.get("name") or "")  # 当前端口的规范信号名

        # direction 决定端口是否默认具有 net 驱动语义。
        str_direction = str(dict_port.get("direction") or "").lower()  # 当前端口方向

        # 输入和双向端口按 net 加入过程赋值禁用集合。
        if str_name and str_direction in {"input", "inout"}:

            # 端口 net 与内部 wire 使用同一集合判断。
            set_wires.add(str_name)

        # output 端口需要从源码区分默认或显式 wire 与 reg/logic。
        if str_name and str_direction == "output" and _output_port_is_net(str_module_text, str_name):

            # output net 与其他 wire 使用同一过程赋值禁用集合。
            set_wires.add(str_name)

    # 返回 module 作用域内的完整 net 名称集合。
    return set_wires

# _output_port_is_net 从原声明恢复 formatter 未保留的 output 类型。
def _output_port_is_net(str_module_text: str, str_port_name: str) -> bool:
    """判断 output 端口是否具有默认或显式 wire 语义。

    参数:
        str_module_text: formatter 确认边界内的原始 module 文本。
        str_port_name: 待检查的 output 端口名。
    返回:
        端口不是显式 reg/logic 时返回 True。
    """

    # 声明模式覆盖可选类型、signed 和位宽前缀。
    str_pattern = (  # 当前 output 端口声明的类型捕获模式
        rf"\boutput\s+(?:(wire|reg|logic)\s+)?(?:signed\s+)?"
        rf"(?:\[[^\]]+\]\s*)?{re.escape(str_port_name)}\b"
    )

    # 只在可信 module 文本内查找当前具名 output 声明。
    obj_match = re.search(str_pattern, str_module_text, flags=re.IGNORECASE | re.MULTILINE)  # 当前 output 声明匹配结果

    # 未找到声明时不猜测 net 类型，避免把未知端口误判为 wire。
    if obj_match is None:

        # 返回保守的非 net 结论。
        return False

    # 未显式声明变量类型或显式 wire 时都属于 net。
    return str(obj_match.group(1) or "wire").lower() == "wire"
