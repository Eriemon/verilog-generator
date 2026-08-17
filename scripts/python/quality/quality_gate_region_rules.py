"""封装区域归属、输出镜像与 rulebook 一致性规则。"""

# 延迟类型注解求值，避免模块导入阶段过早解析复杂联合类型。
from __future__ import annotations

# 复制原 quality gate 的基础标准库依赖，保持无第三方包可运行。
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# formatter_ast 与 rulebook 仍是这些子模块依赖的唯一结构化入口。
from .formatter_backend.banners import display_width
from .formatter_ast import build_ast_report_for_path, iter_verilog_sources, read_verilog_source
from .declaration_region_policy import (
    declaration_region_title,
    instance_signal_names_from_module,
    resolve_declaration_region,
)
from scripts.python.validation.rulebook import load_verilog_rulebook

# 区域归属规则只直接依赖质量门诊断与输出区域上下文类型。
from .quality_gate_types import OutputAssignRegionContext, QualityIssue

# 区域横幅与命名语义 helper 继续从 common 模块显式复用。
from .quality_gate_common import (
    REGION_KEYWORDS,
    _always_references_state_task,
    _as_line,
    _line_region_titles,
    _looks_decoder,
    _looks_encoder,
)

# 区域归属还需要输出端口、标签与严重级别相关 helper。
from .quality_gate_common import (
    _expected_instance_regions,
    _module_output_ports,
    _nearest_region_title,
    _span_item_label,
    _style_severity,
)

# 供 `_region_ownership_rules` 复用的拆分 helper，专门处理检查关键 AST 节点的源码行是否归属正确区域。
def _region_ownership_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查关键 AST 节点的源码行是否归属正确区域。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 区域归属诊断列表。
    """

    # list_issues 保存 AST 区域归属诊断。
    list_issues: list[QualityIssue] = []  # 区域归属诊断集合

    # dict_region_by_line 记录每个区域横幅出现的行号。
    dict_region_by_line = _line_region_titles(str_text)  # 源码中的区域横幅行

    # 没有任何横幅时由 VG031 负责报告。
    if not dict_region_by_line:

        # 无法判断具体 AST 归属。
        return list_issues

    # 输出端口集合用于识别 output bridge assign。
    set_output_ports = _module_output_ports(dict_module)  # output bridge 目标端口集合

    # VG052 仍只检查 output bridge，不混入新的 VG061 通用归属。
    tuple_output_bridge_args = (dict_module, set_output_ports, dict_region_by_line, str_rel_path)  # VG052 位置参数

    # 调用旧专项检查器，保留原有输出连线错误文案。
    list_output_bridge_issues = _output_assign_region_issues(*tuple_output_bridge_args, strict=strict)  # 输出桥接诊断

    # VG052 保持兼容，避免输出连线规则编号漂移。
    list_issues.extend(list_output_bridge_issues)

    # VG061 使用 module、区域索引和输出端口集合推导通用结构归属。
    tuple_general_region_args = (dict_module, dict_region_by_line, set_output_ports, str_rel_path)  # 通用归属位置参数

    # 调用新增通用检查器，补齐参数、声明和过程块区域归属。
    list_general_region_issues = _general_region_ownership_issues(*tuple_general_region_args, strict=strict)  # 通用归属诊断

    # VG061 覆盖参数、声明、过程块、实例化等通用归属。
    list_issues.extend(list_general_region_issues)

    # VG070/VG071 直接把模块接口输出当作真源，校验输出声明区、桥接区和处理区的分组标签与顺序是否逐字镜像。
    list_issues.extend(
        _output_mirror_rules(
            dict_module,  # 当前 module AST
            dict_region_by_line,  # 区域横幅索引
            str_rel_path,  # 当前 Verilog 相对路径
            strict=strict,  # 复用 strict 决定是否阻断
        )
    )

    # 返回区域归属诊断。
    return list_issues

# 供 `_general_region_ownership_issues` 复用的拆分 helper，专门处理检查参数、声明、assign、过程块和实例化的区域归属。
def _general_region_ownership_issues(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_output_ports: set[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查参数、声明、assign、过程块和实例化的区域归属。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_output_ports: 顶层 output 端口名集合。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :return: 通用区域归属诊断列表。
    """

    # list_issues 保存 VG061 通用区域归属诊断。
    list_issues: list[QualityIssue] = []  # 通用区域归属诊断集合

    # localparam、声明、assign 和 always 的期望区域由专门迭代器统一给出。
    for region_item in _iter_region_expectations(dict_module, set_output_ports):

        # 每个期望项只产生零条或一条 VG061。
        list_issues.extend(
            _region_owner_issue_for_item(
                region_item["item"],
                region_item["label"],
                region_item["regions"],
                dict_region_by_line,
                str_rel_path,
                strict=strict,
                str_rule=region_item["rule"],
            )
        )

    # generate、initial、function、task 和实例化使用固定区域。
    for region_item in _iter_fixed_region_expectations(dict_module):

        # 固定结构直接携带期望区域，避免在主循环里重复分支。
        list_issues.extend(
            _region_owner_issue_for_item(
                region_item["item"],
                region_item["label"],
                region_item["regions"],
                dict_region_by_line,
                str_rel_path,
                strict=strict,
                str_rule=region_item["rule"],
            )
        )

    # 返回通用区域归属诊断。
    return list_issues

# 供 `_iter_fixed_region_expectations` 复用的拆分 helper，专门处理generate、initial、function、task 和实例化的区域期望项。
def _iter_fixed_region_expectations(dict_module: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回 generate、initial、function、task 和实例化的区域期望项。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 可直接传给区域归属检查的固定期望项列表。
    """

    # list_items 汇总不依赖命名推导的固定 AST 归属规则。
    list_items: list[dict[str, Any]] = []  # 固定 AST 区域期望项

    # generate 块只允许出现在生成块区域。
    tuple_generate_check = ("generates", ("生成块区域",), "regions.generate")  # generate 块区域规则

    # initial 块允许用于初始化或参数检查前置断言。
    tuple_initial_check = ("initials", ("初始化区域", "参数检查区域"), "regions.initial")  # initial 区域规则

    # function 定义兼容历史名称和当前规范区域。
    tuple_function_check = ("functions", ("函数区域", "函数定义区域"), "regions.function")  # 函数定义标题兼容映射

    # task 定义兼容普通任务和状态任务区域。
    tuple_task_check = ("tasks", ("任务区域", "任务定义区域", "状态任务处理区域"), "regions.task")  # task AST 归属映射

    # 固定结构检查先从 generate 规则开始。
    list_fixed_region_checks = [tuple_generate_check]  # 固定结构区域规则表

    # initial 规则保持在 generate 后，贴近规范区域顺序。
    list_fixed_region_checks += [tuple_initial_check]  # initial 结构区域规则

    # function 规则覆盖工具函数定义。
    list_fixed_region_checks += [tuple_function_check]  # function 固定检查入口

    # task 追加在 function 之后，保持工具过程定义的检查顺序。
    list_fixed_region_checks += [tuple_task_check]  # 任务定义检查入口

    # 逐个 AST 集合生成统一结构，供 VG061 复用。
    for str_collection_name, tuple_expected_regions, str_rule in list_fixed_region_checks:

        # 当前集合的每个条目共享同一个规范区域集合。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # 固定结构的诊断标签优先使用 AST span 推导结果。
            list_items.append(
                {
                    "item": dict_item,
                    "label": _span_item_label(dict_item),
                    "regions": tuple_expected_regions,
                    "rule": str_rule,
                }
            )

    # 当前 module 的 generate span 用于逐项判定实例上下文。
    list_generates = list(dict_module.get("generates", []) or [])  # 当前实例归属判断使用的 generate 边界

    # 实例期望区域由其源码位置和可信 generate span 共同决定。
    for dict_instance in dict_module.get("instances", []) or []:

        # 追加带上下文区域期望的实例检查项。
        list_items.append(
            {
                "item": dict_instance,
                "label": _span_item_label(dict_instance),
                "regions": _expected_instance_regions(
                    dict_instance,
                    list_generates,
                ),
                "rule": "regions.instance",
            }
        )

    # 返回固定结构区域期望。
    return list_items

# 汇总 localparam、声明、assign 与 always 这几类动态区域期望项。
def _iter_region_expectations(
    dict_module: dict[str, Any],
    set_output_ports: set[str],
) -> list[dict[str, Any]]:
    """
    返回 localparam、声明、assign 和 always 的区域期望项。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param set_output_ports: 顶层 output 端口名集合。
    :return: 可直接传给区域归属检查的期望项列表。
    """

    # list_items 保存动态结构的区域期望。
    list_items: list[dict[str, Any]] = []  # 动态 AST 区域期望项

    # 实例连接集合来自 formatter actual 引用事实，不再把普通连线宽泛放行到实例区。
    set_instance_signal_names = instance_signal_names_from_module(dict_module)  # 当前模块实例连接信号名

    # 命名前缀由 rulebook 权威配置提供，区域门禁不内置另一份业务枚举。
    dict_naming = load_verilog_rulebook().raw.get("naming", {})  # formatter 命名策略

    # localparam 是实际出现在 module body 区域中的参数实体。
    for dict_param in dict_module.get("localparams", []) or []:

        # 当前 localparam 可能是状态编码或普通配置常量。
        list_items.append(_localparam_region_expectation(dict_param))

    # 内部声明按命名语义放入对应信号区域。
    for dict_decl in dict_module.get("decls", []) or []:

        # 当前声明的期望区域由名称和声明类型共同决定。
        list_items.append(
            {
                "item": dict_decl,
                "label": _span_item_label(dict_decl),
                "regions": _expected_decl_regions(
                    dict_decl,
                    set_output_ports,
                    set_instance_signal_names,
                    dict_naming,
                ),
                "rule": "regions.declaration",
            }
        )

    # assign 按 output bridge 和普通连线分流。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 当前 assign 的期望区域由左值决定。
        list_items.append(_assign_region_expectation(dict_assign, set_output_ports))

    # always 块根据目标信号和状态引用分配到输出、状态机或主任务区域。
    for dict_always in dict_module.get("always", []) or []:

        # 当前 always 的 header 用于诊断定位。
        list_items.append(
            {
                "item": dict_always,
                "label": str(dict_always.get("header") or "always"),
                "regions": _expected_always_regions(dict_always),
                "rule": "regions.always",
            }
        )

    # 返回所有动态区域期望。
    return list_items

# 供 `_localparam_region_expectation` 复用的拆分 helper，专门处理构造单个 localparam 的区域期望项。
def _localparam_region_expectation(dict_param: dict[str, Any]) -> dict[str, Any]:
    """
    构造单个 localparam 的区域期望项。

    :param dict_param: formatter AST localparam 条目。
    :return: 区域期望项字典。
    """

    # str_name 用于区分状态参数和普通局部常量。
    str_name = str(dict_param.get("name") or "")  # localparam 区域判定名称

    # tuple_expected_regions 表示该 localparam 允许出现的区域。
    tuple_expected_regions = ("状态参数区域",) if str_name.startswith("ST_") else ("配置参数区域",)  # localparam 期望区域

    # localparam 期望项交给 VG061 的统一定位逻辑处理。
    return {
        "item": dict_param,
        "label": _span_item_label(dict_param),
        "regions": tuple_expected_regions,
        "rule": "regions.localparam",
    }

# 依据 assign 左值是否桥接输出端口来生成对应区域期望。
def _assign_region_expectation(dict_assign: dict[str, Any], set_output_ports: set[str]) -> dict[str, Any]:
    """
    构造单条 assign 的区域期望项。

    :param dict_assign: formatter AST assign 条目。
    :param set_output_ports: 顶层 output 端口名集合。
    :return: 区域期望项字典。
    """

    # str_lhs 用于判断 assign 是否直接驱动顶层输出。
    str_lhs = str(dict_assign.get("lhs") or "")  # assign 输出桥接判定左值

    # 输出端口桥接必须在专门连线区域。
    if str_lhs in set_output_ports or str_lhs.startswith("o_"):

        # tuple_expected_regions 指向 output bridge 规范区域。
        tuple_expected_regions = ("输出信号连线",)  # 输出桥接 assign 期望区域

    # 普通组合连线落入其他信号连线区域。
    else:

        # tuple_expected_regions 指向非 output bridge 连线区域。
        tuple_expected_regions = ("其他信号连线",)  # 普通 assign 期望区域

    # 当前 assign 的区域归属由统一定位逻辑生成最终 VG061 诊断。
    return {
        "item": dict_assign,
        "label": str_lhs or "assign",
        "regions": tuple_expected_regions,
        "rule": "regions.assign",
    }

# 供 `_region_owner_issue_for_item` 复用的拆分 helper，专门处理检查单个 AST 条目所在区域是否属于允许集合。
def _region_owner_issue_for_item(
    dict_item: dict[str, Any],
    str_label: str,
    tuple_expected_regions: tuple[str, ...],
    dict_region_by_line: dict[int, str],
    str_rel_path: str, *,
    strict: bool, str_rule: str,
) -> list[QualityIssue]:
    """
    检查单个 AST 条目所在区域是否属于允许集合。

    :param dict_item: formatter AST 条目。
    :param str_label: 诊断中展示的条目标签。
    :param tuple_expected_regions: 允许的区域标题集合。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :param str_rule: 规则子命名空间。
    :return: 当前条目的区域归属诊断列表。
    """

    # int_line_no 使用 AST 起始行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 区域归属定位行号

    # 缺少行号由 VG050 负责。
    if int_line_no is None:

        # 本规则无法定位无 span 条目。
        return []

    # str_region_title 是当前条目最近的区域横幅。
    str_region_title = _nearest_region_title(dict_region_by_line, int_line_no)  # 当前条目所属区域

    # 若当前行位于第一个区域前，保守跳过 module header 参数/端口等结构。
    if not str_region_title:

        # 没有区域上下文时不做归属判断。
        return []

    # 命中允许区域时通过。
    if str_region_title in tuple_expected_regions:

        # 区域归属符合预期。
        return []

    # 生成通用区域归属诊断。
    return [
        QualityIssue(
            "VG061",
            _style_severity(strict),
            f"Item `{str_label}` must be placed in {', '.join(tuple_expected_regions)}, "
            f"not `{str_region_title}`.",
            str_rel_path,
            int_line_no,
            rule=str_rule,
        )
    ]

# 供 `_expected_decl_regions` 复用的拆分 helper，专门处理内部声明允许出现的区域集合。
def _expected_decl_regions(
    dict_decl: dict[str, Any],
    set_output_signal_names: set[str],
    set_instance_signal_names: set[str],
    dict_naming: dict[str, Any],
) -> tuple[str, ...]:
    """
    返回内部声明允许出现的区域集合。

    :param dict_decl: formatter AST 内部声明条目。
    :param set_output_signal_names: 模块 output 端口关联的内部信号名集合。
    :param set_instance_signal_names: 实例端口 actual 引用的信号名集合。
    :param dict_naming: 权威规则资产中的命名分类配置。
    :return: 允许区域标题元组。
    """

    # 声明名称参与功能命名、output 和实例信号优先级判断。
    str_name = str(dict_decl.get("name") or "")  # 内部声明名称

    # 声明类型用于区分 reg、wire、integer 等基础类别。
    str_kind = str(dict_decl.get("kind") or "")  # 内部声明类型

    # 共享策略返回唯一内部区域，门禁与 formatter 不再维护两份优先级。
    str_region = resolve_declaration_region(  # 当前声明唯一允许出现的内部区域
        str_name,  # 当前声明名称
        str_kind,  # 当前声明类型
        set_output_signal_names,  # output 关联信号集合
        set_instance_signal_names,  # 实例端口 actual 引用集合
        dict_naming,  # 权威命名分类配置
    )

    # 横幅校验使用标题元组接口，即使共享策略只返回一个区域。
    return (declaration_region_title(str_region),)

# 根据 always 的目标信号和状态引用推导允许出现的区域集合。
def _expected_always_regions(dict_always: dict[str, Any]) -> tuple[str, ...]:
    """
    返回 always 块允许出现的区域集合。

    :param dict_always: formatter AST always 条目。
    :return: 允许区域标题元组。
    """

    # set_targets 保存 always 的赋值目标。
    set_targets = {str(item) for item in dict_always.get("targets", []) or []}  # always 赋值目标集合

    # 输出桥接内部寄存器属于输出信号处理区域。
    if any(str_target.endswith("_o") for str_target in set_targets):

        # 输出处理 always 应靠近输出信号处理区域。
        return ("输出信号处理区域",)

    # 状态寄存器和 next-state 组合块属于状态机区域。
    if "state_current" in set_targets or "state_next" in set_targets:

        # FSM 前两段归入状态机区域。
        return ("状态机区域",)

    # 引用状态但不更新状态寄存器的第三段逻辑属于状态任务处理区域。
    if _always_references_state_task(dict_always):

        # FSM 第三段归入状态任务处理区域。
        return ("状态任务处理区域",)

    # 其他 always 默认属于主要任务处理区域。
    return ("主要任务处理区域",)

# 供 `_output_assign_region_issues` 复用的拆分 helper，专门处理检查 output bridge assign 是否位于输出信号连线区域。
def _output_assign_region_issues(
    dict_module: dict[str, Any],
    set_output_ports: set[str],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 output bridge assign 是否位于输出信号连线区域。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param set_output_ports: 顶层 output 端口名集合。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :return: output bridge 区域归属诊断列表。
    """

    # list_issues 保存 output bridge assign 的区域诊断。
    list_issues: list[QualityIssue] = []  # output bridge 区域诊断

    # region_context 保存单条 assign 区域判断所需的共享信息。
    region_context = OutputAssignRegionContext(  # VG052 output bridge 区域判定证据
        set_output_ports=set_output_ports,  # 用于识别 assign 是否驱动顶层输出
        dict_region_by_line=dict_region_by_line,  # 用于从 assign 行回溯最近横幅
        str_rel_path=str_rel_path,  # 写入 VG052 诊断的文件路径
        strict=strict,  # 控制 VG052 是否阻断交付
    )

    # 逐条 assign 判断 output bridge 归属，避免普通内部连线误报。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 单条 assign helper 返回空列表或一条 VG052。
        list_issues.extend(_output_assign_region_issues_for_assign(dict_assign, region_context))

    # 返回 output bridge 区域诊断。
    return list_issues

# 供 `_output_assign_region_issues_for_assign` 复用的拆分 helper，专门处理检查单条 assign 是否违反 output bridge 区域归属。
def _output_assign_region_issues_for_assign(
    dict_assign: dict[str, Any],
    region_context: OutputAssignRegionContext,
) -> list[QualityIssue]:
    """
    检查单条 assign 是否违反 output bridge 区域归属。

    :param dict_assign: formatter AST 中的 assign 条目。
    :param region_context: output bridge assign 区域判断上下文。
    :return: 当前 assign 的区域归属诊断列表。
    """

    # str_lhs 标识当前 assign 是否正在驱动 output bridge。
    str_lhs = str(dict_assign.get("lhs") or "")  # output bridge 连续赋值左侧信号

    # 只检查 output bridge 语义的 assign。
    if str_lhs not in region_context.set_output_ports and not str_lhs.startswith("o_"):

        # 普通连线不属于输出桥接强规则。
        return []

    # int_line_no 用于把区域归属问题定位到 assign 起始行。
    int_line_no = _as_line(dict_assign.get("line_start"))  # output bridge assign 的源码起始行

    # 无行号时由 VG050 报告。
    if int_line_no is None:

        # 本规则依赖行号，缺失时跳过避免重复噪音。
        return []

    # str_region_title 是该 assign 前最近的区域横幅。
    str_region_title = _nearest_region_title(region_context.dict_region_by_line, int_line_no)  # assign 当前区域

    # 输出桥接位于正确区域时通过。
    if str_region_title == "输出信号连线":

        # assign 区域归属符合规范。
        return []

    # 区域归属错误会影响 formatter/审查对输出桥接的识别。
    return [
        QualityIssue(
            "VG052",
            _style_severity(region_context.strict),
            f"Output bridge assign `{str_lhs}` must be placed in 输出信号连线, "
            f"not `{str_region_title or 'unknown'}`.",
            region_context.str_rel_path,
            int_line_no,
            rule="regions.output_assign",
        )
    ]

# 供 `_output_mirror_rules` 复用的拆分 helper，专门处理检查输出信号、输出桥接和输出处理区域是否镜像模块接口输出定义。
def _output_mirror_rules(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查输出信号、输出桥接和输出处理区域是否镜像模块接口输出定义。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 输出镜像相关诊断列表。
    """

    # 先从模块接口抽取输出镜像真源。
    list_expected_outputs = _expected_output_mirror_items(dict_module)  # 模块接口输出镜像真源列表。

    # 没有顶层输出端口时不需要继续执行镜像检查。
    if not list_expected_outputs:

        # 空输出模块不会产生输出镜像诊断。
        return []

    # dict_expected_by_signal 保存内部桥接信号到真源条目的映射。
    dict_expected_by_signal: dict[str, dict[str, Any]] = {}  # 供输出声明区和输出处理区按 signal 回查接口真源。

    # 先按内部桥接 signal 建立输出声明区和处理区共用的索引。
    for dict_expected_output in list_expected_outputs:

        # 读取桥接信号键，缺键时无法把内部 `_o` 信号映射回接口输出。
        str_signal_name = str(dict_expected_output.get("signal") or "")  # 输出声明区和输出处理区共用的 signal 键。

        # 缺少内部桥接信号名时跳过当前真源条目。
        if not str_signal_name:

            # 空信号名不能作为信号级真源索引。
            continue

        # 用桥接键名回写声明区和处理区复用的真源条目。
        dict_expected_by_signal[str_signal_name] = dict_expected_output  # 让 signal 键能直接回到对应接口输出。

    # dict_expected_by_port 保存顶层输出端口到真源条目的映射。
    dict_expected_by_port: dict[str, dict[str, Any]] = {}  # 供输出桥接区按 port 回查接口真源。

    # 再按顶层输出 port 建立输出桥接区独有的索引。
    for dict_expected_output in list_expected_outputs:

        # 读取顶层输出 port 键，缺键时无法校验输出桥接 assign 的镜像关系。
        str_port_name = str(dict_expected_output.get("port") or "")  # 输出桥接区用来回查接口真源的 port 键。

        # 缺少顶层输出端口名时跳过当前真源条目。
        if not str_port_name:

            # 空端口名不能作为端口级真源索引。
            continue

        # 用端口键名回写输出桥接区域复用的真源条目。
        dict_expected_by_port[str_port_name] = dict_expected_output  # 把顶层输出端口名绑定到镜像真源，供桥接 assign 直接回查。

    # set_group_labels 保存接口真源允许切换到的组标签。
    set_group_labels: set[str] = set()  # 供输出三区识别合法切组时机的接口标签白名单。

    # 只收集接口真源中显式出现过的组标签。
    for dict_expected_output in list_expected_outputs:

        # 读取接口分组标签，供输出三区在注释切组时对齐接口文本。
        str_group_label = str(dict_expected_output.get("group_label") or "")  # 接口输出定义里显式声明的组标签文本。

        # 空组标签不参与切组白名单。
        if not str_group_label:

            # 无标签真源不应该扩大切组集合。
            continue

        # 把当前显式分组标签登记到输出镜像切组白名单。
        set_group_labels.add(str_group_label)  # 允许输出三区只切换到接口里真实存在的标签。

    # 把信号级真源索引键折叠成集合，供声明区和处理区筛选复用。
    set_signal_names = set(dict_expected_by_signal)  # 可映射回顶层输出的内部桥接信号集合。

    # 把端口级真源索引键折叠成集合，供输出桥接区域筛选复用。
    set_port_names = set(dict_expected_by_port)  # 需要镜像比较的顶层输出端口集合。

    # 先收集输出信号区域的实际条目。
    list_decl_items = _output_decl_mirror_items(  # 从输出信号区域抽取 signal 级镜像条目。
        dict_module, dict_region_by_line, set_signal_names, set_group_labels)  # 只保留可映射回接口输出的 `_o` 声明。

    # 再收集输出信号连线区域的实际条目。
    list_assign_items = _output_assign_mirror_items(  # 从输出桥接区域抽取 port 级镜像条目。
        dict_module, dict_region_by_line, set_port_names, set_group_labels)  # 只保留真正驱动顶层输出端口的桥接 assign。

    # 最后收集输出信号处理区域的实际条目。
    list_always_items = _output_always_mirror_items(  # 从输出处理区域抽取 always 级镜像条目。
        dict_module, dict_region_by_line, set_signal_names, set_group_labels)  # 只保留会驱动输出桥接信号的处理块。

    # list_issues 汇总所有输出镜像诊断。
    list_issues: list[QualityIssue] = []  # 输出镜像诊断集合。

    # 先比较输出信号区域的组标签逐字一致性。
    list_issues.extend(
        _output_group_label_mirror_issues(
            list_decl_items,
            dict_expected_by_signal,
            "输出信号",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 再比较输出信号区域的条目顺序是否与接口一致。
    list_issues.extend(
        _output_order_mirror_issues(
            list_expected_outputs,
            list_decl_items,
            "输出信号",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 接着比较输出信号连线区域的组标签逐字一致性。
    list_issues.extend(
        _output_group_label_mirror_issues(
            list_assign_items,
            dict_expected_by_port,
            "输出信号连线",
            "port",
            str_rel_path,
            strict=strict,
        )
    )

    # 然后比较输出信号连线区域的条目顺序是否与接口一致。
    list_issues.extend(
        _output_order_mirror_issues(
            list_expected_outputs,
            list_assign_items,
            "输出信号连线",
            "port",
            str_rel_path,
            strict=strict,
        )
    )

    # 再比较输出信号处理区域的组标签逐字一致性。
    list_issues.extend(
        _output_group_label_mirror_issues(
            list_always_items,
            dict_expected_by_signal,
            "输出信号处理区域",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 最后比较输出信号处理区域的条目顺序是否与接口一致。
    list_issues.extend(
        _output_order_mirror_issues(
            list_expected_outputs,
            list_always_items,
            "输出信号处理区域",
            "signal",
            str_rel_path,
            strict=strict,
        )
    )

    # 返回三个输出相关区域汇总后的镜像诊断。
    return list_issues

# 供 `_expected_output_mirror_items` 复用的拆分 helper，专门处理按模块接口顺序排列的输出镜像基线条目。
def _expected_output_mirror_items(dict_module: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回按模块接口顺序排列的输出镜像基线条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 按接口输出顺序排列的镜像基线条目列表。
    """

    # dict_output_bridges 保存顶层 output 到内部桥接信号的显式映射。
    dict_output_bridges: dict[str, str] = {}  # 顶层输出端口到内部桥接信号的显式绑定表。

    # 逐条扫描 assign，提取显式 output bridge 绑定关系。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 读取当前 assign 的左值，判断是否是顶层输出桥接。
        str_lhs = str(dict_assign.get("lhs") or "")  # 当前 assign 左值。

        # 只保留 `o_` 前缀的顶层输出桥接 assign。
        if not str_lhs.startswith("o_"):

            # 普通内部连线不会写入显式 bridge 绑定表。
            continue

        # 读取当前 assign 的右值，作为内部桥接信号名。
        str_rhs = str(dict_assign.get("rhs") or "")  # 当前 assign 右值。

        # 写入当前顶层输出端口对应的显式桥接信号。
        dict_output_bridges[str_lhs] = str_rhs  # 当前顶层输出端口对应的显式桥接信号名。

    # list_items 保存按接口顺序整理后的镜像真源条目。
    list_items: list[dict[str, Any]] = []  # 输出镜像真源条目列表。

    # 按模块接口原始顺序遍历全部端口。
    for dict_port in dict_module.get("ports", []) or []:

        # 只把 output 端口纳入输出镜像真源。
        if str(dict_port.get("direction") or "") != "output":

            # 非 output 端口不属于输出镜像合同。
            continue

        # 先读取当前顶层输出端口名称。
        str_port_name = str(dict_port.get("name") or "")  # 当前顶层输出端口名。

        # 端口名为空时无法参与镜像比较。
        if not str_port_name:

            # 跳过异常端口，避免构造空主键真源项。
            continue

        # 生成当前输出端口的真源组标签。
        str_group_label = _output_group_label_from_port(dict_port)  # 当前顶层输出端口对应的真源组标签。

        # 先推导当前输出端口的默认内部桥接信号名称。
        str_default_internal_signal = _default_output_bridge_signal_name(str_port_name)  # 当前输出端口的默认内部桥接信号名。

        # 再优先使用显式桥接绑定，否则回退到默认命名合同。
        str_internal_signal = dict_output_bridges.get(str_port_name) or str_default_internal_signal  # 当前输出端口最终使用的内部桥接信号名。

        # 追加当前顶层输出端口的镜像真源项。
        list_items.append(
            {
                "port": str_port_name,
                "signal": str_internal_signal,
                "group_label": str_group_label,
                "line": _as_line(dict_port.get("line_start")),
            }
        )  # 当前顶层输出端口对应的镜像真源条目。

    # 返回按接口顺序整理好的输出镜像真源。
    return list_items

# 供 `_output_group_label_from_port` 复用的拆分 helper，专门处理模块接口端口的输出镜像组标签。
def _output_group_label_from_port(dict_port: dict[str, Any]) -> str:
    """
    返回模块接口端口的输出镜像组标签。

    :param dict_port: formatter AST 端口条目。
    :return: 由 group 和 section 拼成的镜像组标签。
    """

    # 先读取接口级组注释正文。
    str_group = str(dict_port.get("group") or "").strip()  # 接口级组注释正文。

    # 再读取接口内子组注释正文。
    str_section = str(dict_port.get("section") or "").strip()  # 接口内子组注释正文。

    # group 和 section 同时存在时，需要把二者拼成完整镜像组标签。
    if str_group and str_section:

        # 复用既有 `group--section` 文本格式。
        return f"{str_group}--{str_section}"

    # 只有一级标签时直接返回非空标签。
    return str_group or str_section

# 供 `_default_output_bridge_signal_name` 复用的拆分 helper，专门处理顶层输出端口对应的默认内部 `_o` 信号名。
def _default_output_bridge_signal_name(str_port_name: str) -> str:
    """
    返回顶层输出端口对应的默认内部 `_o` 信号名。

    :param str_port_name: 顶层 output 端口名。
    :return: 默认内部输出桥接信号名。
    """

    # 先处理符合 `o_*` 约定的常规输出端口。
    if str_port_name.startswith("o_"):

        # 去掉顶层输出前缀后再追加内部桥接后缀。
        return f"{str_port_name[2:]}_o"

    # 其他命名保持原名再追加内部桥接后缀。
    return f"{str_port_name}_o"

# 供 `_output_decl_mirror_items` 复用的拆分 helper，专门处理收集输出信号区域中的实际 `_o` 信号镜像条目。
def _output_decl_mirror_items(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_expected_signals: set[str],
    set_group_labels: set[str],
) -> list[dict[str, Any]]:
    """
    收集输出信号区域中的实际 `_o` 信号镜像条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_expected_signals: 可映射回顶层输出的内部信号集合。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 输出信号区域的镜像条目列表。
    """

    # list_items 保存输出信号区域里实际出现的镜像条目。
    list_items: list[dict[str, Any]] = []  # 输出信号区域的实际镜像条目列表。

    # str_current_group_label 记录当前声明条目继承到的组标签。
    str_current_group_label = ""  # 输出信号区域当前生效的组标签。

    # 先按源码顺序扫描全部内部声明。
    for dict_decl in sorted(
        dict_module.get("decls", []) or [],
        key=lambda item: _as_line(item.get("line_start")) or 0,
    ):

        # 读取当前内部声明名称，判断它是否属于输出桥接信号。
        str_name = str(dict_decl.get("name") or "")  # 当前内部声明名称。

        # 只保留能映射回顶层输出的内部声明。
        if str_name not in set_expected_signals:

            # 其他声明不参与输出镜像比较。
            continue

        # 定位当前内部声明的源码行号。
        int_line_no = _as_line(dict_decl.get("line_start"))  # 当前内部声明起始行号。

        # 没有可信行号时无法回溯区域横幅。
        if int_line_no is None:

            # 跳过无 span 的内部声明。
            continue

        # 只接收落在输出信号区域的内部声明。
        if _nearest_region_title(dict_region_by_line, int_line_no) != "输出信号":

            # 其他区域里的 `_o` 声明不计入当前镜像区域。
            continue

        # 先提取当前声明条目的前导注释。
        list_leading_comments = dict_decl.get("leading_comments") or []  # 当前声明条目的前导注释列表。

        # 根据声明条目前导注释刷新当前生效组标签。
        str_current_group_label = _next_output_group_label(  # 当前声明条目继承到的接口组标签。
            list_leading_comments, str_current_group_label, set_group_labels  # 桥接区不能自造分组标题，只能沿用接口里真实存在的标签。
        )

        # 把当前输出信号声明记入镜像条目列表。
        list_items.append(
            {
                "signal": str_name,
                "group_label": str_current_group_label,
                "line": int_line_no,
            }
        )  # 当前输出信号声明对应的镜像条目。

    # 返回输出信号区域的实际镜像条目。
    return list_items

# 供 `_output_assign_mirror_items` 复用的拆分 helper，专门处理收集输出信号连线区域中的实际输出桥接条目。
def _output_assign_mirror_items(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_expected_ports: set[str],
    set_group_labels: set[str],
) -> list[dict[str, Any]]:
    """
    收集输出信号连线区域中的实际输出桥接条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_expected_ports: 需要镜像比较的顶层输出端口集合。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 输出信号连线区域的镜像条目列表。
    """

    # list_items 保存输出桥接区域里实际出现的镜像条目。
    list_items: list[dict[str, Any]] = []  # 输出桥接区域的实际镜像条目列表。

    # str_current_group_label 记录当前输出桥接条目生效的组标签。
    str_current_group_label = ""  # 输出桥接区域当前生效的组标签。

    # 按源码顺序扫描全部 assign 条目。
    for dict_assign in sorted(
        dict_module.get("assigns", []) or [],
        key=lambda item: _as_line(item.get("line_start")) or 0,
    ):

        # 提取当前候选 output bridge 的左值端口名。
        str_lhs = str(dict_assign.get("lhs") or "")  # 当前候选 output bridge 的左值端口名。

        # 只保留顶层输出桥接 assign。
        if str_lhs not in set_expected_ports:

            # 其他内部连线不属于输出镜像比较对象。
            continue

        # 读取当前候选 output bridge 的源码起始行号。
        int_line_no = _as_line(dict_assign.get("line_start"))  # 当前候选 output bridge 的源码起始行号。

        # 没有可信行号时无法确定区域归属。
        if int_line_no is None:

            # 跳过无 span 的输出桥接 assign。
            continue

        # 只接收落在输出信号连线区域的桥接 assign。
        if _nearest_region_title(dict_region_by_line, int_line_no) != "输出信号连线":

            # 其他区域里的输出桥接不计入当前镜像区域。
            continue

        # 先提取当前桥接条目的前导注释。
        list_leading_comments = dict_assign.get("leading_comments") or []  # 当前桥接条目的前导注释列表。

        # 根据桥接条目前导注释刷新当前生效组标签。
        str_current_group_label = _next_output_group_label(  # 当前桥接条目继承到的接口组标签。
            list_leading_comments, str_current_group_label, set_group_labels  # 处理区不能自造分组标题，只能沿用接口里真实存在的标签。
        )

        # 把当前输出桥接 assign 追加到镜像条目列表。
        list_items.append(
            {
                "port": str_lhs,
                "group_label": str_current_group_label,
                "line": int_line_no,
            }
        )  # 当前输出桥接 assign 对应的镜像条目。

    # 返回输出桥接区域的实际镜像条目。
    return list_items

# 供 `_output_always_mirror_items` 复用的拆分 helper，专门处理收集输出信号处理区域中的实际输出处理块镜像条目。
def _output_always_mirror_items(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_expected_signals: set[str],
    set_group_labels: set[str],
) -> list[dict[str, Any]]:
    """
    收集输出信号处理区域中的实际输出处理块镜像条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_expected_signals: 可映射回顶层输出的内部信号集合。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 输出信号处理区域的镜像条目列表。
    """

    # list_items 保存输出处理区域里实际出现的镜像条目。
    list_items: list[dict[str, Any]] = []  # 输出处理区域的实际镜像条目列表。

    # str_current_group_label 记录当前输出处理条目生效的组标签。
    str_current_group_label = ""  # 输出处理区域当前生效的组标签。

    # 按源码顺序扫描全部 always 块。
    for dict_always in sorted(
        dict_module.get("always", []) or [],
        key=lambda item: _as_line(item.get("line_start")) or 0,
    ):

        # 读取当前输出处理块的源码起始行号。
        int_line_no = _as_line(dict_always.get("line_start"))  # 锁定 always 起始行，后续用它回查最近的输出处理区域横幅。

        # 没有可信行号时无法回溯输出处理区域横幅。
        if int_line_no is None:

            # 跳过无 span 的 always 块。
            continue

        # 只接收落在输出信号处理区域的 always 块。
        if _nearest_region_title(dict_region_by_line, int_line_no) != "输出信号处理区域":

            # 其他区域里的 always 块不属于当前镜像区域。
            continue

        # list_targets 保存当前 always 真正命中的输出桥接目标。
        list_targets: list[str] = []  # 当前 always 命中的输出桥接目标列表。

        # 扫描当前 always 的赋值目标，只保留输出桥接信号。
        for item in dict_always.get("targets", []) or []:

            # 规范化当前目标信号名，便于与真源集合比较。
            str_target_name = str(item)  # 当前 always 目标信号名。

            # 只保留能映射回顶层输出的内部桥接目标。
            if str_target_name not in set_expected_signals:

                # 非输出桥接目标不参与当前镜像条目生成。
                continue

            # 记录当前 always 命中的输出桥接目标。
            list_targets.append(str_target_name)  # 当前 always 命中的输出桥接目标。

        # 没命中任何输出桥接目标时不参与镜像比较。
        if not list_targets:

            # 当前 always 与输出镜像无关。
            continue

        # 先提取当前处理块的前导注释。
        list_leading_comments = dict_always.get("leading_comments") or []  # 当前处理块的前导注释列表。

        # 根据处理块前导注释刷新当前生效组标签。
        str_current_group_label = _next_output_group_label(  # 当前处理块继承到的接口组标签。
            list_leading_comments, str_current_group_label, set_group_labels  # 只允许切换到接口真源里存在的组标签。
        )

        # 只记录当前 always 命中的首个输出桥接目标。
        list_items.append(
            {
                "signal": list_targets[0],
                "group_label": str_current_group_label,
                "line": int_line_no,
            }
        )  # 当前输出处理 always 对应的镜像条目。

    # 返回输出处理区域的实际镜像条目。
    return list_items

# 供 `_next_output_group_label` 复用的拆分 helper，专门处理当前输出条目生效的组标签。
def _next_output_group_label(
    list_leading_comments: list[str],
    str_current_group_label: str,
    set_group_labels: set[str],
) -> str:
    """
    返回当前输出条目生效的组标签。

    :param list_leading_comments: 当前 AST 条目的前导注释列表。
    :param str_current_group_label: 前一个已生效的组标签。
    :param set_group_labels: 接口真源允许出现的输出分组标签集合。
    :return: 当前条目生效的组标签。
    """

    # 先尝试读取当前条目的显式组注释标签。
    str_group_label = _leading_group_comment_label(list_leading_comments)  # 当前条目的显式组注释标签。

    # 只有命中接口真源允许的组标签时才切换当前组。
    if str_group_label and str_group_label in set_group_labels:

        # 使用当前条目的显式组标签覆盖上一组状态。
        return str_group_label

    # 否则沿用上一条已经生效的组标签。
    return str_current_group_label

# 供 `_leading_group_comment_label` 复用的拆分 helper，专门处理前导注释中的首个组标签正文。
def _leading_group_comment_label(list_leading_comments: list[str]) -> str:
    """
    返回前导注释中的首个组标签正文。

    :param list_leading_comments: formatter AST 暴露的前导注释列表。
    :return: 首个组标签正文；没有可用组标签时返回空字符串。
    """

    # 没有任何前导注释时不可能提取到组标签。
    if not list_leading_comments:

        # 让调用方继续沿用上一组标签。
        return ""

    # 只取当前条目前导注释里的首行作为组标签候选。
    str_first_comment = str(list_leading_comments[0] or "")  # 当前条目前导注释首行文本。

    # 把首行注释正文规范化成可比较的组标签文本。
    return _normalize_comment_label(str_first_comment)

# 供 `_normalize_comment_label` 复用的拆分 helper，专门处理去掉注释前缀后的纯注释正文。
def _normalize_comment_label(str_comment: str) -> str:
    """
    返回去掉注释前缀后的纯注释正文。

    :param str_comment: 原始注释文本。
    :return: 规范化后的注释正文。
    """

    # 先去掉注释两侧空白，保留纯正文比较视图。
    str_label = str(str_comment or "").strip()  # 去掉首尾空白后的注释正文。

    # 行注释标签需要先剥离 `//` 前缀再参与比较。
    if str_label.startswith("//"):

        # 去掉纯行注释前缀，保留真正的标签文本。
        str_label = str_label[2:].strip()  # 剥离 `//` 后的组标签正文。

    # 返回规范化后的组标签文本。
    return str_label

# 供 `_single_output_mirror_issue` 复用的拆分 helper，专门处理只包含一条输出镜像诊断的列表。
def _single_output_mirror_issue(
    str_code: str, str_message: str, str_rel_path: str, int_line_no: int | None,
    str_rule: str, *, strict: bool,
) -> list[QualityIssue]:
    """
    返回只包含一条输出镜像诊断的列表。

    :param str_code: 诊断规则编号。
    :param str_message: 诊断文案。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param int_line_no: 诊断落点行号。
    :param str_rule: 诊断子规则名。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 只包含一条输出镜像诊断的列表。
    """

    # 先组装调用点需要回报的单条输出镜像诊断对象。
    quality_issue_issue: QualityIssue = QualityIssue(  # 当前输出镜像 helper 要返回的唯一诊断对象。
        str_code, _style_severity(strict), str_message, str_rel_path, int_line_no, rule=str_rule  # 保持既有 QualityIssue 构造顺序，避免聚合口径漂移。
    )

    # 再包装成列表，复用现有质量门聚合接口。
    return [quality_issue_issue]

# 供 `_output_group_label_mirror_issues` 复用的拆分 helper，专门处理输出相关区域组标签文本漂移诊断。
def _output_group_label_mirror_issues(
    list_actual_items: list[dict[str, Any]], dict_expected_items: dict[str, dict[str, Any]],
    str_region_title: str, str_key_name: str, str_rel_path: str, *, strict: bool,
) -> list[QualityIssue]:
    """
    返回输出相关区域组标签文本漂移诊断。

    :param list_actual_items: 当前输出相关区域的实际条目列表。
    :param dict_expected_items: 当前条目类型对应的接口真源索引。
    :param str_region_title: 当前正在比较的输出相关区域标题。
    :param str_key_name: 当前条目类型的比较键名。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 组标签文本漂移诊断列表。
    """

    # 逐条比较当前区域条目的组标签文本。
    for dict_actual_item in list_actual_items:

        # 先读取当前条目的比较主键。
        str_item_key = str(dict_actual_item.get(str_key_name) or "")  # 当前条目的比较主键。

        # 再按主键回查接口真源条目。
        dict_expected_item = dict_expected_items.get(str_item_key) or {}  # 当前条目对应的接口真源条目。

        # 读取接口真源声明的组标签文本。
        str_expected_label = str(dict_expected_item.get("group_label") or "")  # 接口真源要求使用的组标签文本。

        # 真源没有组标签时不做逐字标签比较。
        if not str_expected_label:

            # 无标签真源只参与顺序比较，不参与 VG070。
            continue

        # 读取当前区域实际观察到的组标签文本。
        str_actual_label = str(dict_actual_item.get("group_label") or "")  # 当前区域实际使用的组标签文本。

        # 当前条目组标签逐字一致时直接通过。
        if str_actual_label == str_expected_label:

            # 当前条目没有发生组标签文本漂移。
            continue

        # 把接口真源标签漂移展开成 VG070 文案，直接回显期望标签和实际标签。
        str_issue_message = (
            f"`{str_region_title}` item `{str_item_key}` must use group label "
            f"`{str_expected_label}`, not `{str_actual_label or 'unknown'}`."
        )  # 诊断里同时回显接口标签文本和当前区域标签文本。

        # 让标签漂移定位到当前区域的实际条目，避免把问题报到接口真源定义处。
        return _single_output_mirror_issue(
            "VG070",
            str_issue_message,
            str_rel_path,
            _as_line(dict_actual_item.get("line")),
            "output.mirror.group_label",
            strict=strict,
        )

    # 当前区域没有发现组标签文本漂移。
    return []

# 供 `_output_order_mirror_issues` 复用的拆分 helper，专门处理输出相关区域顺序漂移诊断。
def _output_order_mirror_issues(
    list_expected_outputs: list[dict[str, Any]], list_actual_items: list[dict[str, Any]],
    str_region_title: str, str_key_name: str, str_rel_path: str, *, strict: bool,
) -> list[QualityIssue]:
    """
    返回输出相关区域顺序漂移诊断。

    :param list_expected_outputs: 按接口顺序排列的输出镜像真源。
    :param list_actual_items: 当前输出相关区域的实际条目列表。
    :param str_region_title: 当前正在比较的输出相关区域标题。
    :param str_key_name: 当前条目类型的比较键名。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把输出镜像问题升级为 error。
    :return: 顺序漂移诊断列表。
    """

    # 当前区域没有任何条目时无需生成顺序诊断。
    if not list_actual_items:

        # 空区域不会触发输出顺序比较。
        return []

    # list_actual_order 保存当前区域实际观察到的主键顺序。
    list_actual_order: list[str] = []  # 当前区域实际出现的主键顺序。

    # 逐条提取当前区域实际观察到的主键顺序。
    for dict_item in list_actual_items:

        # 读取当前实际条目的比较主键。
        str_actual_key = str(dict_item.get(str_key_name) or "")  # 当前实际条目的比较主键。

        # 记录当前实际条目的主键顺序。
        list_actual_order.append(str_actual_key)  # 当前区域实际出现的主键。

    # 把当前区域实际出现过的主键折叠成集合，便于裁剪真源顺序。
    set_actual_order = set(list_actual_order)  # 当前区域实际出现过的条目主键集合。

    # list_expected_order 保存当前区域按接口真源裁剪后的约束顺序。
    list_expected_order: list[str] = []  # 只保留当前区域实际出现过、且必须遵循接口顺序的主键列表。

    # 按模块接口真源顺序裁剪出当前区域需要遵循的主键顺序。
    for dict_expected_item in list_expected_outputs:

        # 读取当前真源条目的比较主键。
        str_expected_key = str(dict_expected_item.get(str_key_name) or "")  # 当前真源条目的比较主键。

        # 当前真源主键未出现在实际区域时不纳入比较。
        if str_expected_key not in set_actual_order:

            # 只比较当前区域实际出现过的输出条目。
            continue

        # 记录当前区域应遵循的真源主键顺序。
        list_expected_order.append(str_expected_key)  # 当前区域应遵循的真源主键。

    # 实际顺序与真源顺序一致时直接通过。
    if list_actual_order == list_expected_order:

        # 当前区域没有发生顺序漂移。
        return []

    # 把顺序漂移落点到当前区域首个实际条目上。
    int_line_no = _as_line(list_actual_items[0].get("line"))  # 当前区域首个实际条目对应的源码行号。

    # 先把期望顺序和实际顺序格式化成独立文本，避免最终 VG071 文案过长。
    str_expected_order_text = str(list_expected_order)  # VG071 文案里回显的接口真源顺序文本。

    # 再把当前区域实际顺序格式化成独立文本，供最终 VG071 文案直接拼接。
    str_actual_order_text = str(list_actual_order)  # VG071 文案里回显的当前区域顺序文本。

    # 再拼出当前 VG071 的最终诊断文案。
    str_issue_message = (
        f"`{str_region_title}` order must follow module interface outputs {str_expected_order_text}, "
        f"not {str_actual_order_text}."  # 用两组预格式化顺序文本拼出最终 VG071 文案。
    )

    # 让顺序漂移落在当前区域第一条实际条目上，避免把问题报到接口真源定义处。
    return _single_output_mirror_issue(
        "VG071",
        str_issue_message,
        str_rel_path,
        int_line_no,
        "output.mirror.order",
        strict=strict,
    )

# 供 `_rulebook_consistency_issues` 复用的拆分 helper，专门处理检查 rulebook JSON 是否仍是运行时规则的可信来源。
def _rulebook_consistency_issues(str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 rulebook JSON 是否仍是运行时规则的可信来源。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 规则源一致性诊断列表。
    """

    # 读取规则源失败时必须转成 VG059，而不是让质量门崩溃。
    try:

        # rulebook_source 汇总区域、fallback 注释和 profile 规则。
        rulebook_source = load_verilog_rulebook()  # Verilog 风格规则源

    # 规则源不可用说明门禁无法可信执行。
    except Exception as exc:

        # 返回阻断诊断，提示维护者修复规则源。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                f"Verilog rulebook cannot be loaded: {exc}",
                str_rel_path,
                rule="rulebook.load",
            )
        ]

    # 区域横幅顺序必须和 JSON 中 regions 保持一致。
    if tuple(rulebook_source.region_labels) != tuple(REGION_KEYWORDS):

        # 硬编码表和 JSON 漂移时区域归属结论不可信。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Runtime region labels drifted from assets/verilog_style_rules.json.",
                str_rel_path,
                rule="rulebook.region_drift",
            )
        ]

    # fallback 注释列表缺失时 VG056 无法可信执行。
    if not rulebook_source.fallback_comments:

        # 空 fallback 配置代表规则源结构漂移。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Rulebook must define comments.fallback_comments for deliverable gate.",
                str_rel_path,
                rule="rulebook.fallback_comments",
            )
        ]

    # runtime_messages 为空时，VG069 和参数检查尾部合同都失去机器真源支撑。
    dict_runtime_messages = rulebook_source.raw.get("runtime_messages") or {}  # 读取运行时消息规则分区，后续校验 VG069 真源是否齐全。

    # 人类可读 display 前缀必须在 rulebook 中显式声明。
    if not dict_runtime_messages.get("human_readable_display_prefixes"):

        # 缺少 display 前缀配置会让 VG069 失去机器真源。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Rulebook must define runtime_messages.human_readable_display_prefixes.",
                str_rel_path,
                rule="rulebook.runtime_messages",
            )
        ]

    # 机器 transcript 豁免前缀同样需要在 rulebook 中显式声明。
    if not dict_runtime_messages.get("machine_transcript_prefixes"):

        # 缺少 transcript 前缀配置会让机器输出豁免失去依据。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Rulebook must define runtime_messages.machine_transcript_prefixes.",
                str_rel_path,
                rule="rulebook.runtime_messages",
            )
        ]

    # 规则源一致时无诊断。
    return []

# 返回当前模块需要公开的兼容导出名称清单。
def _export_names() -> list[str]:
    """
    返回当前模块对外继续公开的兼容符号名。

    参数:
        无外部业务参数。

    :return: 稳定的兼容导出名称列表。
    """

    # str_exports_source 按旧测试与调用方依赖顺序保留兼容导出名原文。
    str_exports_source = """
    _region_ownership_rules
    _rulebook_consistency_issues
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
