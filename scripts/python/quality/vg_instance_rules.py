"""实现模块实例端口与连接表达式位宽相关的 RTL VG 门禁。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# 语义事实提供可信 module 集合。
from .vg_semantic_facts import VgFacts, VgSourceFacts, iter_trusted_modules

# 规则模型统一逐门禁状态和定位证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 实例事实 helper 负责参数覆盖、端口连接与静态位宽求值。
from .vg_value_facts import (
    _instance_parameter_overrides,
    _named_port_connections,
    _parse_instance_sections,
    expression_width,
    module_instance_parameter_values,
    module_widths,
)

# _resolve_instance_connections 只在结构和参数环境都可信时返回可比较连接。
def _resolve_instance_connections(
    dict_instance: dict[str, object],
    dict_child: dict[str, object],
    dict_parent_parameters: dict[str, int],
) -> tuple[dict[str, int | None], list[tuple[str, str]]] | None:
    """解析单个实例的参数环境、子模块端口宽度与命名连接。

    参数:
        dict_instance: formatter AST 中的单个实例事实。
        dict_child: 实例引用的子模块声明事实。
        dict_parent_parameters: 父模块中可静态求值的整数 parameter。
    返回:
        可靠的子模块端口宽度表与命名连接；事实不足时返回 ``None``。
    """

    # 子模块名确定实例文本中参数区和端口区的起始位置。
    str_child_name = str(dict_instance.get("module_name") or "")  # 被例化模块名

    # formatter 原文保留关联项的括号和分隔符。
    str_instance_text = str(dict_instance.get("text") or "")  # formatter 保留的实例原文

    # 配对括号解析确保参数关联不会混入端口连接。
    tuple_sections = _parse_instance_sections(  # 参数区与端口区解析结果
        str_instance_text,  # 待分区的实例声明原文
        str_child_name,  # 定位参数区起点的引用名称
    )  # 已分离的实例关联项

    # 无法稳定分区的实例不能继续比较连接位宽。
    if tuple_sections is None:

        # 结构事实不足时由调用方统一保留未知状态。
        return None

    # 参数覆盖和端口项来自互不重叠的实例文本区间。
    list_parameter_items, list_port_items = tuple_sections  # 参数项与端口项

    # 受限求值器只接受能够安全解析的整数参数覆盖。
    dict_parameter_overrides = _instance_parameter_overrides(  # 已验证的实例参数覆盖
        dict_child,  # 提供参数声明顺序的子模块事实
        list_parameter_items,  # 实例原文中的参数关联项
        dict_parent_parameters,  # 覆盖表达式可引用的父模块整数环境
    )  # 子模块参数替换表

    # 禁止在参数解析失败后继续使用子模块默认值。
    if dict_parameter_overrides is None:

        # 参数环境未知时由调用方统一保留未知状态。
        return None

    # 子模块端口宽度叠加当前实例已验证的参数覆盖。
    dict_child_widths = module_widths(  # 当前实例参数化后的端口位宽表
        dict_child,  # 提供端口声明的子模块结构事实
        parameter_overrides=dict_parameter_overrides,  # 替换声明默认值的整数映射
    )  # 子模块声明宽度

    # 端口区只接受能够明确对应端口名的连接形式。
    list_connections = _named_port_connections(list_port_items)  # 命名端口连接

    # 位置连接或无法提取的连接列表不能安全对应端口名。
    if not list_connections:

        # 连接关系未知时由调用方统一保留未知状态。
        return None

    # 两类事实同时可信后才允许进入逐连接位宽比较。
    return dict_child_widths, list_connections

# _evaluate_instance_widths 对单个实例生成确定冲突或未知事实。
def _evaluate_instance_widths(
    source_facts: VgSourceFacts,
    dict_parent: dict[str, object],
    dict_instance: dict[str, object],
    dict_modules: dict[str, dict[str, object]],
    dict_parent_widths: dict[str, int | None],
    dict_parent_parameters: dict[str, int],
) -> tuple[list[VgFinding], list[VgFinding]]:
    """比较单个实例的全部命名端口连接。

    参数:
        source_facts: 当前父模块所属的源码事实。
        dict_parent: formatter AST 中的父模块事实。
        dict_instance: formatter AST 中的当前实例事实。
        dict_modules: 目标 RTL 与外部 stub 合并后的模块接口表。
        dict_parent_widths: 父模块信号位宽表。
        dict_parent_parameters: 父模块实例参数整数环境。
    返回:
        确定位宽冲突与静态事实不足发现项。
    """

    # 当前实例定位统一用于冲突与未知发现项。
    int_line = int(dict_instance.get("line_start") or dict_parent.get("line_start") or 1)  # 当前实例的一基行号

    # 当前扫描位置的引用名用于查询合并后的接口表。
    str_child_name = str(dict_instance.get("module_name") or "")  # 当前引用接口名

    # 缺失的子模块定义必须进入未知分支，不能猜测端口宽度。
    dict_child = dict_modules.get(str_child_name)  # 本轮可见的子模块接口

    # 外部 IP 或缺失模块接口无法静态比较。
    if dict_child is None:

        # 返回逐实例未知定位，指导调用方补充对应接口 stub。
        return [], [
            VgFinding(
                source_facts.relative_path,
                int_line,
                "缺少外部模块接口定义，无法静态比较端口位宽。",
                f"{str_child_name} {dict_instance.get('instance_name') or ''}".strip(),
            )
        ]

    # 结构、参数和连接解析集中在 helper 中，避免部分事实泄漏到比较阶段。
    tuple_connection_facts = _resolve_instance_connections(  # 当前实例可比较事实
        dict_instance,  # 当前 formatter 实例结构
        dict_child,  # 合并接口表命中的子模块声明
        dict_parent_parameters,  # 父模块可静态求值的整数参数
    )  # 端口声明与连接输入

    # 任一结构事实不足都必须保持未知，不能形成确定通过。
    if tuple_connection_facts is None:

        # 返回当前实例的解析缺口，避免非空 stub 仍产生空 findings。
        return [], [
            VgFinding(
                source_facts.relative_path,
                int_line,
                "实例参数区、端口区或参数覆盖无法静态解析。",
                f"{str_child_name} {dict_instance.get('instance_name') or ''}".strip(),
            )
        ]

    # helper 的成功结果同时提供端口声明宽度与实际连接。
    dict_child_widths, list_connections = tuple_connection_facts  # 比较输入

    # 单实例分别收集确定冲突与未知连接。
    list_findings: list[VgFinding] = []  # 当前实例的确定位宽冲突

    # 未知连接单独保留，避免掩盖同一扫描中的确定冲突。
    list_unknown_findings: list[VgFinding] = []  # 当前实例的静态事实缺口

    # 每个命名端口连接独立比较。
    for str_port_name, str_expression in list_connections:

        # 去除外围空白，避免空连接被误当成普通表达式。
        str_expression = str_expression.strip()  # 去除连接表达式外围空白

        # 显式空连接不携带可比较位宽。
        if not str_expression:

            # 空连接没有驱动表达式，无需执行宽度比较。
            continue

        # 查询子模块声明中该命名端口的静态宽度。
        int_port_width = dict_child_widths.get(str_port_name)  # 子模块端口位宽

        # 使用父模块信号表计算实际连接表达式的静态宽度。
        int_signal_width = expression_width(str_expression, dict_parent_widths)  # 父模块连接位宽

        # 参数关联、未知端口或复杂表达式都必须保留不确定状态。
        if int_port_width is None or int_signal_width is None:

            # 记录具体端口、表达式和两侧求值结果，供 stub 修订直接定位。
            list_unknown_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "模块端口或连接表达式位宽无法静态确定。",
                    (
                        f"{str_child_name} {dict_instance.get('instance_name') or ''} "
                        f".{str_port_name}({str_expression}) "
                        f"port_width={int_port_width} signal_width={int_signal_width}"
                    ).strip(),
                )
            )

            # 跳过无法可靠比较的当前连接。
            continue

        # 两侧位宽一致时继续检查其他连接。
        if int_port_width == int_signal_width:

            # 当前连接已确认同宽，继续检查其余端口。
            continue

        # 记录确定的两侧位宽冲突及其连接原文。
        list_findings.append(
            VgFinding(
                source_facts.relative_path,
                int_line,
                "模块端口与实例连接信号位宽不一致。",
                f".{str_port_name}({str_expression})",
            )
        )

    # 返回当前实例的两类事实，由全局规则统一决定优先级。
    return list_findings, list_unknown_findings

# _connection_port_width_match 比较已解析子模块端口与父模块连接信号位宽。
def _connection_port_width_match(facts: VgFacts) -> VgEvaluation:
    """检查命名实例连接两侧的静态可知位宽。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        已知连接的位宽匹配结论，或静态事实不足时的不确定结论。
    """

    # 外部 stub 先建立接口表，目标 RTL 的真实定义具有更高优先级。
    dict_modules = {
        str(dict_module.get("name") or ""): dict_module  # 外部 module 名称对应其接口事实
        for dict_module in facts.external_modules  # 遍历调用方提供的外部 module stub
    }  # 外部模块接口

    # 本轮目标中的真实定义覆盖同名外部 stub。
    dict_modules.update({
        str(dict_module.get("name") or ""): dict_module  # module 名称对应其 formatter 结构事实
        for _, dict_module, _, _ in iter_trusted_modules(facts)  # 遍历可信 module 生成接口索引
    })  # 本轮扫描可见的 module 接口

    # findings 收集所有确定的实例端口位宽冲突。
    list_findings: list[VgFinding] = []  # 实例端口位宽冲突证据

    # 未知 findings 为外部接口提供逐实例定位。
    list_unknown_findings: list[VgFinding] = []  # 静态事实不足的实例证据

    # applicable 区分“没有实例”与“实例已被实际检查”。
    bool_applicable = False  # 是否发现模块实例

    # 父模块声明表用于解析连接表达式位宽。
    for source_facts, dict_parent, _, _ in iter_trusted_modules(facts):

        # 建立当前父模块中可静态求值的信号位宽表。
        dict_parent_widths = module_widths(dict_parent)  # 父模块信号位宽表

        # 父模块整数 parameter 可参与子实例的参数覆盖表达式。
        dict_parent_parameters = module_instance_parameter_values(dict_parent)  # 父模块实例参数环境

        # formatter AST 保留每个实例的模块名、原文和行号。
        for dict_instance in dict_parent.get("instances", []) or []:

            # 发现实例后，本规则对当前扫描目标具有适用性。
            bool_applicable = True  # 发现实例即进入连接检查

            # 单实例 helper 隔离结构解析与逐端口分支。
            tuple_instance_findings = _evaluate_instance_widths(  # 当前实例的确定与未知发现项
                source_facts,  # 当前源码定位事实
                dict_parent,  # 当前父模块结构
                dict_instance,  # 待检查的 formatter 实例
                dict_modules,  # 可见模块接口表
                dict_parent_widths,  # 父模块信号位宽
                dict_parent_parameters,  # 覆盖表达式可见的父级常量
            )  # 单实例比较结果

            # 两类发现项分别累计，最终保持确定冲突优先。
            list_instance_findings, list_instance_unknowns = tuple_instance_findings  # 单实例结果分区

            # 确定冲突进入最高优先级结果集合。
            list_findings.extend(list_instance_findings)

            # 静态缺口仅在没有确定冲突时决定 inconclusive。
            list_unknown_findings.extend(list_instance_unknowns)

    # 确定冲突优先于同一目标中的未知连接。
    if list_findings:

        # 返回全部已确认的端口连接位宽冲突。
        return failed(*list_findings)

    # 存在未解析接口或表达式时不得报告确定通过。
    if list_unknown_findings:

        # 保留静态事实不足状态，避免把未知连接误判为同宽。
        return inconclusive(
            "存在无法静态确定的模块接口或连接表达式位宽。",
            *list_unknown_findings,
        )

    # 全部可见命名连接均同宽，或目标没有实例。
    return passed(applicable=bool_applicable)
