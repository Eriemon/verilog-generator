"""实现模块实例端口与连接表达式位宽相关的 RTL VG 门禁。"""

# future annotations 延后解析 VG 数据模型类型。
from __future__ import annotations

# Any 描述原语与 formatter 接口的动态字段。
from typing import Any

# 语义事实提供可信 module 集合。
from .vg_semantic_facts import VgFacts, VgSourceFacts, iter_trusted_modules

# 规则模型统一逐门禁状态和定位证据。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 原语 profile 投影为 VG097 可比较的 module 接口事实。
from .vg_primitive_facts import primitive_module_interface, primitive_profiles

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
    # 父模块事实用于确定实例参数与定位。
    dict_parent: dict[str, object],
    # 当前实例事实提供引用名称和原始关联项。
    dict_instance: dict[str, object],
    # 合并后的接口表提供子模块端口声明。
    dict_modules: dict[str, dict[str, object]],
    # 父模块宽度表用于求值连接表达式。
    dict_parent_widths: dict[str, int | None],
    # 父模块参数环境用于求值实例参数覆盖。
    dict_parent_parameters: dict[str, int],
    # 冲突原语集合维持显式来源与内置 profile 的 fail-closed 语义。
    set_primitive_conflicts: frozenset[str] = frozenset(),
) -> tuple[list[VgFinding], list[VgFinding]]:
    """比较单个实例的全部命名端口连接。

    参数:
        source_facts: 当前父模块所属的源码事实。
        dict_parent: formatter AST 中的父模块事实。
        dict_instance: formatter AST 中的当前实例事实。
        dict_modules: 目标 RTL 与外部 stub 合并后的模块接口表。
        dict_parent_widths: 父模块信号位宽表。
        dict_parent_parameters: 父模块实例参数整数环境。
        set_primitive_conflicts: 与显式外部接口冲突的原语名称集合。
    返回:
        确定位宽冲突与静态事实不足发现项。
    """

    # 当前实例定位统一用于冲突与未知发现项。
    int_line = int(dict_instance.get("line_start") or dict_parent.get("line_start") or 1)  # 当前实例的一基行号

    # 当前扫描位置的引用名用于查询合并后的接口表。
    str_child_name = str(dict_instance.get("module_name") or "")  # 当前引用接口名

    # built-in 与显式 external interface 冲突时保持局部 inconclusive。
    if str_child_name in set_primitive_conflicts:

        # 冲突不允许任意选择一个端口事实。
        return [], [_primitive_conflict_finding(source_facts, dict_instance, int_line, str_child_name)]

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

    # 逐端口比较移交独立 helper，避免实例解析和连接求值互相缠绕。
    return _evaluate_named_connections(  # 已解析连接的逐端口比较结果
        source_facts,
        # 当前实例事实保留原始实例名作为证据。
        dict_instance,
        # 子模块名称用于生成稳定 finding evidence。
        str_child_name,
        # 实例起始行号用于报告定位。
        int_line,
        # 解析后的子模块端口声明宽度。
        dict_child_widths,
        # 父模块信号宽度用于表达式求值。
        dict_parent_widths,
        # 解析后的命名连接列表。
        list_connections,
    )

# _primitive_conflict_finding 为原语与外部 stub 冲突生成稳定定位证据。
def _primitive_conflict_finding(
    source_facts: VgSourceFacts,
    dict_instance: dict[str, object],
    int_line: int,
    str_child_name: str,
) -> VgFinding:
    """构造原语接口冲突的局部 inconclusive finding。

    参数:
        source_facts: 当前父文件的稳定相对路径事实。
        dict_instance: 当前 formatter 实例事实。
        int_line: 已确定的实例起始行号。
        str_child_name: 发生冲突的原语模块名称。
    返回:
        指向当前原语实例的冲突定位证据。
    """

    # evidence 保留模块名和实例名，便于修订外部接口来源。
    str_evidence = f"{str_child_name} {dict_instance.get('instance_name') or ''}".strip()  # 冲突实例证据

    # 返回局部未知 finding，不把冲突扩散为整棵设计的错误。
    return VgFinding(
        source_facts.relative_path,
        int_line,
        "原语内置 profile 与显式外部接口事实冲突，无法静态比较端口位宽。",
        str_evidence,
    )

# _evaluate_named_connections 只处理已经解析成功的命名连接。
def _evaluate_named_connections(
    source_facts: VgSourceFacts,
    # 当前实例事实用于扩展定位上下文。
    dict_instance: dict[str, object],
    # 子模块名称用于输出稳定证据。
    str_child_name: str,
    # 当前实例一基行号。
    int_line: int,
    # 子模块端口声明宽度表。
    dict_child_widths: dict[str, int | None],
    # 父模块信号声明宽度表。
    dict_parent_widths: dict[str, int | None],
    # 已解析的命名端口连接对。
    list_connections: list[tuple[str, str]],
) -> tuple[list[VgFinding], list[VgFinding]]:
    """比较一组命名连接并保留未知表达式的定位。

    参数:
        source_facts: 当前父文件的稳定相对路径事实。
        dict_instance: formatter AST 中的当前实例事实。
        str_child_name: 实例引用的子模块名称。
        int_line: 当前实例的一基起始行号。
        dict_child_widths: 子模块端口的静态声明宽度。
        dict_parent_widths: 父模块信号的静态声明宽度。
        list_connections: 已解析的命名端口与连接表达式对。
    返回:
        确定位宽冲突列表和静态未知列表。
    """

    # 确定冲突与静态未知必须分开，供上层维持 fail-closed 优先级。
    list_findings: list[VgFinding] = []  # 已确认的端口连接位宽冲突

    # 端口或信号宽度缺失时保留逐连接未知证据。
    list_unknown_findings: list[VgFinding] = []  # 无法静态求值的连接

    # 每一项已经是“端口名、连接表达式”的命名连接对。
    for str_port_name, str_expression in list_connections:

        # 空白表达式不携带可比较的信号事实。
        str_expression = str_expression.strip()  # 去除连接表达式首尾空白

        # 空连接保持未知，不把省略连接解释成零宽端口。
        if not str_expression:

            # 记录当前端口，便于调用方补充连接事实。
            list_unknown_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "命名端口连接为空，无法静态比较位宽。",
                    f"{str_child_name}.{str_port_name}",
                )
            )

            # 空连接证据已经写入，当前端口不再执行宽度比较。
            continue

        # 先读取当前命名端口的声明宽度，再独立求值连接表达式。
        int_port_width = dict_child_widths.get(str_port_name)  # 当前实例目标端口的静态宽度

        # 父模块连接表达式只在受限求值器确认时提供宽度。
        int_signal_width = expression_width(str_expression, dict_parent_widths)  # 父模块连接宽度

        # 任一侧未知时保留可复现的连接上下文。
        if int_port_width is None or int_signal_width is None:

            # 证据同时包含端口、信号和表达式，禁止静默降级为通过。
            list_unknown_findings.append(
                VgFinding(
                    source_facts.relative_path,
                    int_line,
                    "端口或连接表达式位宽无法静态确定。",
                    f"{str_child_name}.{str_port_name} "
                    f"port_width={int_port_width} signal_width={int_signal_width} "
                    f"expression={str_expression}",
                )
            )

            # 当前连接事实不足，继续处理同一实例的下一端口。
            continue

        # 相等宽度满足 VG097 的确定性比较。
        if int_port_width == int_signal_width:

            # 当前连接没有发现冲突，继续检查同一实例的其余端口。
            continue

        # 已知且不相等的两侧宽度形成确定违规。
        list_findings.append(
            VgFinding(
                source_facts.relative_path,
                int_line,
                "实例端口与连接表达式位宽不匹配。",
                f"{str_child_name}.{str_port_name} "
                f"port_width={int_port_width} signal_width={int_signal_width} "
                f"expression={str_expression}",
            )
        )

    # 上层依据两个集合决定 failed、inconclusive 或 passed。
    return list_findings, list_unknown_findings

# _primitive_conflict_names 集中计算与外部 stub 签名冲突的原语名称。
def _primitive_conflict_names(
    facts: VgFacts,
    dict_external_modules: dict[str, dict[str, object]],
) -> frozenset[str]:
    """返回内置 profile 与外部接口签名不一致的原语名称。

    参数:
        facts: 当前扫描目标的可信语义事实。
        dict_external_modules: 外部 stub 名称到接口事实的索引。
    返回:
        仅包含方向或宽度签名冲突的原语名称集合。
    """

    # 初始集合只遍历当前 catalog 的标准原语 profile。
    set_conflict_names: set[str] = set()  # 待过滤的原语冲突名称

    # 同名外部 stub 必须与内置端口签名逐一比较。
    for str_name, dict_profile in primitive_profiles(
        getattr(facts, "primitive_catalog", {})
    ).items():

        # 不同名的外部接口不构成语义覆盖冲突。
        if str_name not in dict_external_modules:

            # 当前 profile 没有同名外部来源，跳过比较。
            continue

        # 只有方向或宽度签名不一致时才进入 fail-closed 集合。
        if not _module_interface_compatible(
            primitive_module_interface(dict_profile),
            dict_external_modules[str_name],
        ):

            # 记录需要局部 inconclusive 的原语实例名称。
            set_conflict_names.add(str_name)

    # frozenset 防止下游扫描过程意外改变冲突证据。
    return frozenset(set_conflict_names)

# _connection_port_width_match 比较已解析子模块端口与父模块连接信号位宽。
def _connection_port_width_match(facts: VgFacts) -> VgEvaluation:
    """检查命名实例连接两侧的静态可知位宽。

    参数:
        facts: formatter AST 构建的可信扫描事实。
    返回:
        已知连接的位宽匹配结论，或静态事实不足时的不确定结论。
    """

    # 原语内置 profile 先建立接口表，后续显式来源按优先级覆盖。
    dict_modules = {
        str_name: primitive_module_interface(dict_profile)  # 原语名称对应的内置接口事实
        for str_name, dict_profile in primitive_profiles(  # 原语 profile 迭代保持兼容缺省目录
            getattr(facts, "primitive_catalog", {})  # 兼容未提供原语目录的旧事实对象
        ).items()  # 遍历固定原语 profile
        if str_name not in getattr(facts, "primitive_catalog", {}).get("project_ip", {})  # 项目 IP 仅有 manifest 边界
    }

    # 外部 stub 覆盖同名内置 profile，目标 RTL 后续再拥有最高优先级。
    dict_modules.update({
        str(dict_module.get("name") or ""): dict_module  # 外部 module 名称对应其接口事实
        for dict_module in facts.external_modules  # 遍历调用方提供的外部 module stub
    })  # 外部模块接口

    # 记录内置与显式接口的语义冲突，避免覆盖后丢失 fail-closed 证据。
    dict_external_modules = {  # 外部 stub 名称到接口事实的索引
        str(dict_module.get("name") or dict_module.get("module_name") or ""): dict_module  # 外部 module 的稳定名称
        for dict_module in facts.external_modules  # 遍历调用方声明的 stub 集合
    }  # 当前扫描的外部接口索引

    # 只有同名且端口签名不一致时才记录 primitive conflict。
    frozenset_primitive_conflicts = _primitive_conflict_names(  # 当前轮次的 primitive conflict 名称
        facts,  # 当前扫描目标的原语 profile 事实
        dict_external_modules,  # 同名 external stub 的接口索引
    )

    # 本轮目标中的真实定义覆盖同名外部 stub。
    dict_modules.update({
        str(dict_module.get("name") or ""): dict_module  # module 名称对应其 formatter 结构事实
        for _, dict_module, _, _ in iter_trusted_modules(facts)  # 遍历可信 module 生成接口索引
    })  # 本轮扫描可见的 module 接口

    # 目标 RTL 的真实实现高于任何 profile 或 external stub。
    set_source_module_names = {  # 真实 RTL module 名称优先于外部来源
        str(dict_module.get("name") or "")  # 取得可信 module 的稳定名称
        for _, dict_module, _, _ in iter_trusted_modules(facts)  # 遍历本轮可信 RTL module
    }  # 当前扫描的真实 module 名称集合

    # RTL 真实定义存在时，外部冲突证据不再适用。
    frozenset_primitive_conflicts: frozenset[str] = frozenset(  # 过滤被真实 RTL 覆盖的冲突名称
        str_name  # 保留仍无真实定义的冲突原语
        for str_name in frozenset_primitive_conflicts  # 遍历初步冲突名称集合
        if str_name not in set_source_module_names  # 真实定义拥有最高优先级
    )  # 最终局部 fail-closed 冲突集合

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
            tuple_instance_findings = _evaluate_instance_widths(  # 单实例比较结果
                source_facts,  # 当前源码定位事实
                # 上游父模块结构作为参数覆盖的求值环境。
                dict_parent,  # 传入父模块结构事实
                # 当前待检查的实例事实。
                dict_instance,  # 传入当前实例事实
                # 当前轮次可见的模块接口。
                dict_modules,  # 传入可见模块接口
                # 当前父模块信号的已知位宽。
                dict_parent_widths,  # 传入父模块信号宽度
                # 父模块参数环境。
                dict_parent_parameters,  # 传入父模块参数环境
                # 显式 stub 冲突后的原语集合。
                frozenset_primitive_conflicts,  # 传入原语冲突集合
            )

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

# _module_interface_compatible 比较 VG097 所需的名称、方向和宽度三元组。
def _module_interface_compatible(
    dict_left: dict[str, Any],
    dict_right: dict[str, Any],
) -> bool:
    """判断两个 module 接口是否在端口语义上完全一致。

    参数:
        dict_left: 内置原语投影的 module 接口。
        dict_right: 外部 stub 或 RTL module 的接口事实。
    返回:
        端口名称、方向和宽度三元组完全一致时返回 ``True``。
    """

    # 内部签名只保留 VG097 冲突判定需要的三项事实。
    def _signature(dict_module: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
        """提取 module 接口的规范端口签名。

        参数:
            dict_module: 待投影的 module 接口字典。
        返回:
            按端口名称排序的方向与宽度签名元组。
        """

        # 端口签名排序后与来源顺序无关，适合冲突比较。
        return tuple(
            sorted(
                (
                    str(dict_port.get("name") or ""),
                    str(dict_port.get("direction") or "").lower(),
                    str(dict_port.get("width") or ""),
                )
                for dict_port in dict_module.get("ports", []) or []
                if isinstance(dict_port, dict)
            )
        )

    # 比较两侧签名，拒绝只凭模块名的宽免。
    return _signature(dict_left) == _signature(dict_right)
