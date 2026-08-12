"""建立 source-only 模块实现索引并物化参数特化组合图。"""

# 延迟注解求值避免运行时解析递归值对象类型。
from __future__ import annotations

# 标准库 AST、哈希与文本工具支撑常量求值和稳定指纹。
import ast
import hashlib
import json
import re

# dataclass replace 用于保持实现对象不可变的引用状态更新。
from dataclasses import dataclass, replace

# Path 只为 formatter 恢复调用提供诊断来源身份。
from pathlib import Path

# Any 限定 formatter 兼容事实边界，不扩散到公开模型。
from typing import Any

# formatter 文本入口用于 defparam 失败后的受控结构恢复。
from .formatter_ast import build_ast_report_for_text

# 参数化循环物化复用 formatter 唯一表达式解析器，不在 VG 层实现第二套语法。
from .formatter_backend.expression_facts import ExpressionParseError, ExpressionParser

# 定义根和冻结事实是实现索引的基础值对象。
from .vg_comb_model import (
    DefinitionRoot,
    FrozenFact,
    ModuleImplementation,
    ModuleImplementationIdentity,
)

# 实现集合和参数值对象定义 specialization 输入合同。
from .vg_comb_model import (
    ModuleImplementationIndex,
    ParameterEnvironment,
    ParameterValue,
)

# hierarchy binding 模型冻结路径、端口映射和 producer 证据。
from .vg_comb_model import (
    HierarchyBinding,
    HierarchyGraph,
    ProducerRef,
    ScopedTarget,
    StaticOutputMap,
)

# 材料化输出、驱动分类与递归未知对象组成组合图结果。
from .vg_comb_model import (
    OutputDriverClass,
    SourceSpan,
    SpecializedModule,
    SpecializationKey,
    UnknownRegion,
)

# 冻结和解冻入口隔离 formatter 可变事实与共享缓存。
from .vg_comb_model import (
    freeze_fact,
    thaw_fact,
)

# selector 模块统一提供目标规范化和 output actual 静态逐位映射。
from .vg_comb_selectors import base_target, map_static_output_actual, static_target

# VgFacts 是 source reports 与 external interfaces 的发现阶段权威输入。
from .vg_semantic_facts import VgFacts

# 原语 profile 作为受治理的 synthetic boundary，不进入普通 source implementation。
from .vg_primitive_facts import primitive_output_boundary, primitive_profiles, primitive_module_interface

# 冻结映射入口保证模块缓存只接收字典事实。
def _frozen_mapping(value: object) -> FrozenFact:
    """把字典值收窄为 FrozenFact。

    参数:
        value: 需要进入模块实现或特化图的字典事实。

    返回:
        递归冻结后的 FrozenFact。

    异常:
        TypeError: 输入不是可冻结字典。
    """

    # 递归冻结先复制全部嵌套容器，再检查顶层映射类别。
    frozen_fact_value = freeze_fact(value)  # 当前输入的不可变所有权副本

    # 模块结构合同不允许标量或裸序列充当事实根。
    if not isinstance(frozen_fact_value, FrozenFact):

        # 类型错误明确阻止不受治理的缓存值进入特化图。
        raise TypeError("> ERR: [Python] module fact must be a mapping")

    # 收窄后的 FrozenFact 可安全存入共享 specialization cache。
    return frozen_fact_value

# 解冻入口只为当前求值建立一次性字典副本。
def _mapping(value: object) -> dict[str, Any]:
    """为单次求值恢复一个事实字典。

    参数:
        value: FrozenFact 或普通字典兼容值。

    返回:
        与缓存断开引用的普通字典。
    """

    # FrozenFact 必须递归复制后才能交给可变求值流程。
    if isinstance(value, FrozenFact):

        # 解冻结果与缓存内部 tuple-of-pairs 不共享容器引用。
        dict_thawed = thaw_fact(value)  # 当前事实的一次性可变副本

        # 防御性检查维持本函数始终返回字典的公开合同。
        return dict(dict_thawed) if isinstance(dict_thawed, dict) else {}

    # 普通字典调用者同样获得浅层独立顶层容器。
    if isinstance(value, dict):

        # 输入已经是求值期字典，无需经过冻结表示。
        return dict(value)

    # 非映射兼容值按空事实处理，不污染其他模块。
    return {}

# 模块范围恢复统一兼容显式 span 与旧行号字段。
def _module_span(module: dict[str, Any]) -> SourceSpan:
    """读取 formatter 模块定义范围。

    参数:
        module: formatter module 报告字典。

    返回:
        当前模块的一基定义范围。
    """

    # 新 formatter 报告优先提供完整四坐标 span。
    dict_span = module.get("span")  # 当前模块显式源码范围映射

    # 完整映射路径保留同行多定义的列身份。
    if isinstance(dict_span, dict):

        # 缺失单项坐标只在当前模块内回落到一基最小值。
        return SourceSpan(
            int(dict_span.get("line_start") or 1),  # 模块定义起始行
            int(dict_span.get("column_start") or 1),  # 模块定义起始列
            int(dict_span.get("line_end") or dict_span.get("line_start") or 1),  # 模块定义结束行
            int(dict_span.get("column_end") or 1),  # 模块定义结束列
        )

    # 旧报告只含行号时仍构造稳定但列信息有限的定义身份。
    return SourceSpan(
        int(module.get("line_start") or 1),  # 旧报告模块起始行
        int(module.get("column_start") or 1),  # 旧报告可选起始列
        int(module.get("line_end") or module.get("line_start") or 1),  # 旧报告模块结束行
        int(module.get("column_end") or 1),  # 旧报告可选结束列
    )

# 材料化模板恢复为与 formatter 新旧报告兼容的独立字典。
def _module_template(module: dict[str, Any]) -> dict[str, Any]:
    """建立向后兼容的模块材料化模板。

    参数:
        module: formatter module 报告字典。

    返回:
        包含参数、生成结构、实例和组合事实的独立模板字典。
    """

    # Task 2 formatter 优先提供已经聚合的材料化模板。
    dict_explicit = module.get("comb_materialization_template")  # 当前模块显式材料化模板

    # 显式模板只复制顶层，冻结入口随后取得深层所有权。
    if isinstance(dict_explicit, dict):

        # 返回副本避免 index 注释引用状态时改写 formatter 报告。
        return dict(dict_explicit)

    # 旧报告按公共字段合成等价模板以维持向后兼容。
    return {
        "parameters": list(module.get("params", [])),  # 公开参数声明顺序
        "localparams": list(module.get("localparams", [])),  # 局部参数求值顺序
        "continuous_assigns": list(module.get("assigns", [])),  # 连续赋值结构
        "comb_expressions": list(module.get("comb_expressions", [])),  # 类型化目标表达式
        "control_processes": list(module.get("always", [])),  # always 控制树
        "generates": list(module.get("generates", [])),  # generate 控制结构
        "instances": list(module.get("instances", [])),  # 模块级实例目录
        "functions": list(module.get("functions", [])),  # 本地函数定义
        "storage_driver_templates": list(module.get("storage_driver_templates", [])),  # 存储切点事实
    }

# 单文件恢复仅在 defparam 使 formatter 丢失整个模块时启用。
def _source_modules(source: Any) -> list[dict[str, Any]]:
    """读取或恢复一个 source fact 的模块报告。

    参数:
        source: VgSourceFacts 或保持相同属性合同的兼容对象。

    返回:
        当前文件内按源码顺序排列的模块字典列表。
    """

    # 正常 formatter 报告中的模块无需任何恢复处理。
    list_modules = list(source.report.get("modules", []) or [])  # 当前 source 已解析模块集合

    # diagnostic 只用于识别已批准的 defparam 局部恢复条件。
    bool_has_defparam_diagnostic = any(  # 当前文件是否因 defparam 严格诊断失败
        "defparam" in str(item.get("message") or "").lower()  # 诊断文本明确提到 defparam
        for item in source.report.get("diagnostics", []) or []  # 当前 source 全部 formatter 诊断
        if isinstance(item, dict)  # 仅结构化诊断包含 message 字段
    )

    # 已有模块或非 defparam 失败不能进入恢复路径。
    if list_modules or not bool_has_defparam_diagnostic:

        # 原始模块顺序直接成为实现索引输入。
        return list_modules

    # 真实 VgSourceFacts 保存原文，恢复路径继续通过 formatter 而非自建 parser。
    str_source = str(getattr(source, "source", ""))  # 当前 RTL 文件原始文本

    # 有原文时仅屏蔽 defparam 语句并重新提取其余可靠结构。
    if str_source:

        # 注释替换保持行数不变，使恢复后的定义范围仍指向原文件。
        str_recovered_source = re.sub(  # 去除 defparam 语义后的 formatter 恢复文本
            r"(?m)^(\s*)defparam\b[^;]*;",  # 单行 defparam 语句范围
            r"\1// defparam specialization unsupported",  # 保留缩进与固定未知标记
            str_source,  # 当前 source 原始 Verilog 文本
        )

        # 独立 formatter 报告恢复端口、实例与 generate 控制树。
        dict_recovered_report = build_ast_report_for_text(  # 屏蔽 defparam 后的结构报告
            str_recovered_source,  # 保持原行号的恢复文本
            source_path=Path(source.relative_path),  # 当前文件相对路径用于诊断
        )

        # 恢复模块继续携带 defparam 固定局部不支持原因。
        list_modules = list(dict_recovered_report.get("modules", []) or [])  # 恢复得到的模块集合

        # 每个恢复模块的模板追加相同的局部 unknown 标记。
        for dict_module in list_modules:

            # 非字典兼容项不参与模板增强。
            if not isinstance(dict_module, dict):

                # 继续寻找 formatter 返回的结构化模块条目。
                continue

            # 模板副本保留恢复的实例和 generate 事实。
            dict_template = _module_template(dict_module)  # 当前恢复模块材料化模板

            # 固定原因阻止 defparam 被当作已完成特化。
            dict_template["unsupported_construct"] = "defparam specialization unsupported"  # defparam 局部未知标记

            # 增强模板重新挂回模块报告供 index 冻结。
            dict_module["comb_materialization_template"] = dict_template  # 保留恢复结构的模板

        # formatter 成功恢复至少一个模块时立即返回可信结构。
        if list_modules:

            # 非 defparam 结构已完整保留，固定 unknown 留到材料化阶段。
            return list_modules

    # 无法恢复原文时以路径 stem 保留 definition root 和固定 unknown。
    str_inferred_name = (  # 当前 source placeholder 的模块身份名称
        source.relative_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]  # 相对路径文件 stem
    )

    # placeholder 只声明已知身份，不伪造端口或实例语义。
    return [
        {
            "name": str_inferred_name,  # 路径推导的 placeholder 名称
            "line_start": 1,  # placeholder 定义起始行
            "line_end": int(source.report.get("source_metrics", {}).get("lines") or 1),  # 文件末行
            "ports": [],  # 未恢复的端口事实
            "params": [],  # 未恢复的公开参数事实
            "localparams": [],  # 未恢复的局部参数事实
            "functions": [],  # 未恢复的本地函数事实
            "comb_materialization_template": {
                "comb_expressions": [],  # 未恢复的组合表达式
                "instances": [],  # 未恢复的实例目录
                "generates": [],  # 未恢复的生成结构
                "functions": [],  # 未恢复的函数定义
                "storage_driver_templates": [],  # 未恢复的存储驱动
                "unsupported_construct": "defparam specialization unsupported",  # 固定局部原因
            },
        }
    ]

# 单个模块字典转换成不共享 formatter 容器的不可变实现。
def _implementation_from_module(source: Any, module: dict[str, Any]) -> ModuleImplementation | None:
    """构造一个 source module 实现。

    参数:
        source: 当前模块所属的 VgSourceFacts。
        module: formatter 当前模块报告字典。

    返回:
        名称完整时返回 ModuleImplementation，否则返回 None。
    """

    # 空模块名无法成为引用方可解析的 implementation identity。
    str_name = str(module.get("name") or "")  # 当前 module 声明名称

    # formatter 兼容空项只影响当前条目。
    if not str_name:

        # 无名称模块不进入 source-only 实现索引。
        return None

    # 路径、名称和定义范围共同形成重复定义安全身份。
    module_identity = ModuleImplementationIdentity(  # 当前 source module 实现身份
        relative_path=source.relative_path,  # 定义所属相对路径
        module_name=str_name,  # 当前模块声明名称
        definition_span=_module_span(module),  # 当前定义的一基源码范围
    )

    # 所有深层 formatter 事实在进入索引前递归冻结。
    return ModuleImplementation(
        identity=module_identity,  # 当前实现唯一身份
        ports=tuple(_frozen_mapping(item) for item in module.get("ports", []) or []),  # 端口事实
        parameters=tuple(_frozen_mapping(item) for item in module.get("params", []) or []),  # 参数事实
        localparams=tuple(_frozen_mapping(item) for item in module.get("localparams", []) or []),  # 局部参数
        functions=tuple(_frozen_mapping(item) for item in module.get("functions", []) or []),  # typed 函数定义
        materialization_template=_frozen_mapping(_module_template(module)),  # 模块材料化模板
    )

# 实现排序键按路径和完整源码范围建立跨运行稳定次序。
def _implementation_sort_key(
    implementation: ModuleImplementation,
) -> tuple[str, int, int, int, int]:
    """返回 source module 实现的稳定排序键。

    参数:
        implementation: 已冻结且具有定义身份的模块实现。

    返回:
        路径、起止行列组成的字典序比较元组。
    """

    # 完整范围可区分同文件内重名甚至同行声明的实现。
    return (
        implementation.identity.relative_path,
        implementation.identity.definition_span.line_start,
        implementation.identity.definition_span.column_start,
        implementation.identity.definition_span.line_end,
        implementation.identity.definition_span.column_end,
    )

# 实现索引入口汇总 source definitions 并隔离 external interfaces。
def build_module_implementation_index(facts: VgFacts) -> ModuleImplementationIndex:
    """从 source reports 建立稳定且不覆盖重复定义的实现索引。

    参数:
        facts: 本次 VG 运行的 formatter 可信事实集合。

    返回:
        source implementations 与 external interfaces 分离的不可变索引。
    """

    # module 名映射到全部 source 定义，重复项不覆盖。
    dict_grouped: dict[str, list[ModuleImplementation]] = {}  # source 实现临时分组

    # 每个纳入分析的 RTL source 独立恢复模块集合。
    for source in facts.sources:

        # 恢复列表仍保持当前文件的源码定义顺序。
        for dict_module in _source_modules(source):

            # 非字典兼容项无法提供 module 公共字段。
            if not isinstance(dict_module, dict):

                # 局部跳过不会影响同文件其他定义。
                continue

            # 模块转换统一执行 identity 构造与深层冻结。
            module_implementation = _implementation_from_module(source, dict_module)  # 当前模块不可变实现

            # 无名称兼容模块不进入实现索引。
            if module_implementation is None:

                # 继续收集当前 source 的后续有效定义。
                continue

            # 同名模块追加到列表，禁止后定义覆盖先定义。
            dict_grouped.setdefault(module_implementation.identity.module_name, []).append(module_implementation)

    # 外部接口按 module 名分组，但永不进入 source implementation 候选。
    dict_external_grouped: dict[str, list[FrozenFact]] = {}  # external interface 临时分组

    # 每个受治理 stub 只提供接口边界，不提供可展开实现。
    for dict_interface in facts.external_modules:

        # external schema 兼容 module_name 和历史 name 字段。
        str_name = str(dict_interface.get("module_name") or dict_interface.get("name") or "")  # 外部模块名

        # 空名称接口不能参与实例引用解析。
        if str_name:

            # 接口事实冻结后追加到独立 external lookup。
            dict_external_grouped.setdefault(str_name, []).append(_frozen_mapping(dict_interface))

    # source 定义数量用于区分唯一实现和重复实现。
    dict_source_counts = {  # module 名到 source 定义数量
        str_name: len(list_items)  # 当前 module 的实现数量
        for str_name, list_items in dict_grouped.items()  # 全部 source 实现分组
    }

    # external 名称集合支持常数时间引用分类。
    set_external_names = set(dict_external_grouped)  # 仅有外部接口的 module 名集合

    # 第二遍在全部定义已知后标注每个实例引用状态。
    for str_name, list_items in tuple(dict_grouped.items()):

        # 新列表替换临时未标注实现，旧冻结对象保持不可变。
        dict_grouped[str_name] = [
            _annotate_implementation_references(  # 附加实例引用局部原因的新实现
                item,  # 当前 source module 实现
                dict_source_counts,  # 全局 source 定义数量
                set_external_names,  # 全局 external interface 名称
            )
            for item in list_items  # 当前同名 module 的全部实现
        ]

    # 外层 module 名和内层 implementation identity 都采用稳定排序。
    tuple_implementations = tuple(  # 最终 source implementation 索引
        (
            str_name,  # 当前 module 名索引键
            tuple(sorted(list_items, key=_implementation_sort_key)),  # 当前名称的稳定实现元组
        )
        for str_name, list_items in sorted(dict_grouped.items())  # module 名按字典序排列
    )

    # 外部接口使用相同 module 名字典序形成不可变 lookup。
    tuple_external_interfaces = tuple(  # 不参与递归实现解析的 external 接口索引
        (str_name, tuple(list_items))  # 每个 external 名称及其冻结接口集合
        for str_name, list_items in sorted(dict_external_grouped.items())  # 外部 module 名排序
    )

    # 原语 profile 只承载端口方向和 output boundary，不伪造可递归 source 实现。
    tuple_primitive_profiles = tuple(  # 原语 profile 的冻结索引，供 exact-name synthetic boundary 查询
        (
            str_name,  # catalog 中的 exact primitive module 名称
            _frozen_mapping(dict_profile),  # 隔离当前 profile 的端口与 boundary 事实
        )
        for str_name, dict_profile in sorted(  # 按名称建立稳定查找序列
            primitive_profiles(getattr(facts, "primitive_catalog", {})).items()  # 兼容未提供原语目录的旧事实对象
        )
    )  # 当前 catalog 的原语 profile 查找序列

    # 三类索引在类型层保持物理分离，防止 external 或 primitive 被错误递归展开。
    return ModuleImplementationIndex(
        tuple_implementations,
        tuple_external_interfaces,
        tuple_primitive_profiles,
    )

# 引用分类只根据完整 source/external 名录生成固定原因。
def _reference_reason(
    module_name: str,
    current_name: str,
    source_counts: dict[str, int],
    external_names: set[str],
) -> str:
    """分类一个实例 module 引用的实现状态。

    参数:
        module_name: 当前实例引用的 module 名称。
        current_name: 当前实现自身的 module 名称。
        source_counts: source module 名到定义数量的映射。
        external_names: 仅存在外部接口的 module 名集合。

    返回:
        唯一可绑定时为空字符串，否则为精确局部原因。
    """

    # 自引用会在相同 specialization key 再入，必须局部截断。
    if module_name == current_name:

        # 固定原因供层次遍历识别递归边界。
        return "recursive module specialization"

    # 多个 source definition 无法由普通 module 名唯一选择。
    if source_counts.get(module_name, 0) > 1:

        # 重复实现保留全部 identity，但当前引用保持未知。
        return "duplicate module implementation"

    # 唯一 source implementation 可以继续递归材料化。
    if source_counts.get(module_name, 0) == 1:

        # 空原因表示实现绑定没有局部缺口。
        return ""

    # 仅有 external interface 时只能形成本地边界。
    if module_name in external_names:

        # 接口 stub 不提供可展开的内部组合图。
        return "external interface has no implementation"

    # 名录均无命中表示引用缺少任何受治理实现。
    return "missing module implementation"

# 第二遍引用标注把完整名录信息写回冻结材料化模板。
def _annotate_implementation_references(
    implementation: ModuleImplementation,
    source_counts: dict[str, int],
    external_names: set[str],
) -> ModuleImplementation:
    """为实现模板中的实例附加引用分类。

    参数:
        implementation: 尚未标注引用状态的 source 实现。
        source_counts: source module 名到定义数量的映射。
        external_names: 仅有外部接口的 module 名集合。

    返回:
        材料化模板拥有局部 reference_unknown_reason 的新实现。
    """

    # 单次解冻副本承载引用分类，不改变初始实现对象。
    dict_template = _mapping(implementation.materialization_template)  # 当前实现材料化模板副本

    # 标注列表保持 formatter 实例源码顺序。
    list_annotated: list[dict[str, Any]] = []  # 已附加引用状态的实例事实

    # 每个 flat instance 独立计算实现绑定原因。
    for dict_item in dict_template.get("instances", []) or []:

        # 非字典兼容项无法承载 module_name 或原因字段。
        if not isinstance(dict_item, dict):

            # 局部跳过不影响同模块其他实例。
            continue

        # 新字典避免向初始冻结实例泄漏写操作。
        dict_clone = dict(dict_item)  # 当前实例的可变标注副本

        # module_name 是引用分类的查找键。
        str_module_name = str(dict_clone.get("module_name") or "")  # 当前实例引用的模块名

        # 固定原因写入实例事实，选中 generate 分支后再转 UnknownRegion。
        dict_clone["reference_unknown_reason"] = _reference_reason(  # 指出本实例为何无法唯一解析到可递归展开的源码模块定义
            str_module_name,  # 需要在全局实现索引中解析的被引用模块名称
            implementation.identity.module_name,  # 用于识别直接自引用的当前实现模块名称
            source_counts,  # 区分唯一实现与重复实现的源码定义数量名录
            external_names,  # 区分仅有接口合同实现的外部模块名称集合
        )

        # 标注副本按原出现顺序进入新实例目录。
        list_annotated.append(dict_clone)

    # 模板只替换实例集合，其余事实保持原冻结值语义。
    dict_template["instances"] = list_annotated  # 完成引用分类的实例目录

    # replace 构造新不可变实现并重新取得模板深层所有权。
    return replace(
        implementation,  # 保留 identity、端口、参数和函数事实
        materialization_template=_frozen_mapping(dict_template),  # 标注后的冻结模板
    )

# Verilog 定宽字面量正则同时提取位宽、符号、进制和数字段。
verilog_literal_pattern = re.compile(  # 参数常量字面量解析规则
    r"(?:(?P<width>\d+))?'(?P<signed>[sS])?(?P<base>[bBoOdDhH])(?P<digits>[0-9a-fA-F_xXzZ?]+)"  # Verilog 整数字面量形状
)

# 字面量解析保留未知位而不把 x/z 强制折叠为零。
def _literal_value(match: re.Match[str]) -> tuple[int | None, int | None, bool, str]:
    """解析一个 Verilog 定宽整数字面量。

    参数:
        match: verilog_literal_pattern 产生的完整匹配。

    返回:
        value、width、signed 和 unknown_reason 四元组。
    """

    # 下划线只改善源码可读性，不参与数值转换。
    str_digits = match.group("digits").replace("_", "")  # 当前字面量的纯数字段

    # 显式位宽缺失时保持 None，由声明规格继续补充。
    int_width = int(match.group("width")) if match.group("width") else None  # 当前字面量位宽

    # s 标记决定字面量自身的有符号属性。
    bool_signed = bool(match.group("signed"))  # 当前字面量是否显式 signed

    # 任意 x、z 或问号位都会使常量数值不可确定。
    if re.search(r"[xXzZ?]", str_digits):

        # 未知位仍保留显式位宽和符号证据。
        return None, int_width, bool_signed, "unknown literal bits"

    # 进制字符映射到 Python int 接受的 radix。
    int_base = {"b": 2, "o": 8, "d": 10, "h": 16}[match.group("base").lower()]  # 字面量进制

    # 完整数字段转换成确定整数并返回无错误原因。
    return int(str_digits, int_base), int_width, bool_signed, ""

# 字面量替换同时收集表达式的显式宽度和符号提示。
def _replace_literals(expression: str) -> tuple[str, int | None, bool | None, str]:
    """把 Verilog 字面量替换成 Python 整数文本。

    参数:
        expression: 待安全求值的 Verilog 常量表达式。

    返回:
        替换后文本、末个显式位宽、符号属性和未知原因。
    """

    # 最后一个显式字面量位宽作为声明缺失时的局部提示。
    int_width: int | None = None  # 当前表达式最近字面量的位宽

    # 符号提示随最近一个 Verilog 字面量更新。
    bool_signed: bool | None = None  # 当前表达式最近字面量的 signed 属性

    # 任意未知位原因阻止表达式被求成确定整数。
    str_reason = ""  # 当前表达式字面量未知原因

    # 内层替换器共享宽度、符号和未知原因聚合状态。
    def replace_literal(match: re.Match[str]) -> str:
        """替换当前匹配的 Verilog 字面量。

        参数:
            match: 当前字面量正则匹配。

        返回:
            可供 Python AST 解析的十进制整数文本。
        """

        # 聚合字段由每次匹配更新，最终随替换文本一起返回。
        nonlocal int_width, bool_signed, str_reason

        # 专用解析器保留当前字面量完整定宽语义。
        tuple_literal = _literal_value(match)  # 当前字面量的值、位宽、符号和未知原因

        # 只有显式位宽才覆盖此前提示。
        if tuple_literal[1] is not None:

            # 最近显式位宽供 ParameterValue 声明缺失时使用。
            int_width = tuple_literal[1]  # 当前表达式最近显式字面量位宽

        # signed 提示始终对应当前匹配字面量。
        bool_signed = tuple_literal[2]  # 当前表达式最近字面量符号属性

        # 未知位原因一旦出现就进入表达式求值结果。
        if tuple_literal[3]:

            # 记录当前字面量无法确定的精确原因。
            str_reason = tuple_literal[3]  # 阻止当前表达式产生确定值的字面量原因

        # 未知数值使用零占位仅供语法解析，原因会阻止结果放行。
        return "0" if tuple_literal[0] is None else str(tuple_literal[0])

    # 正则替换完成后同时返回聚合的字面量语义提示。
    return verilog_literal_pattern.sub(replace_literal, expression), int_width, bool_signed, str_reason

# 操作符转换只覆盖安全常量 AST 支持的 Verilog 子集。
def _python_expression(expression: str) -> tuple[str, int | None, bool | None, str]:
    """把受支持 Verilog 常量操作符转换成 Python 语法。

    参数:
        expression: parameter、generate 或循环常量表达式。

    返回:
        Python 表达式文本和字面量语义提示。
    """

    # 第一步替换 Verilog 定宽字面量并保留其语义提示。
    tuple_replaced = _replace_literals(expression.strip())  # 替换文本、位宽、符号与原因

    # 文本分量随后执行 Verilog 到 Python 操作符转换。
    str_text = tuple_replaced[0]  # 字面量已转换的表达式文本

    # Verilog 逻辑与或映射到 Python 布尔操作符。
    str_text = str_text.replace("&&", " and ").replace("||", " or ")  # 逻辑与或转换结果

    # 仅独立感叹号映射为 not，保留不等号操作符。
    str_text = re.sub(r"!(?!=)", " not ", str_text)  # 逻辑非转换后的表达式

    # 去除外围空白并原样返回字面量语义提示。
    return str_text.strip(), tuple_replaced[1], tuple_replaced[2], tuple_replaced[3]

# AST 求值器只执行白名单常量节点，不调用 eval。
def _eval_ast(node: ast.AST, values: dict[str, int]) -> int:
    """递归求值白名单 Python 常量 AST。

    参数:
        node: 已解析且尚未执行的 Python AST 节点。
        values: 当前参数和循环变量整数环境。

    返回:
        当前节点的整数求值结果。

    异常:
        ValueError: 节点或引用不在受支持常量子集内。
    """

    # Expression 包装节点只负责转发其 body 的整数结果。
    if isinstance(node, ast.Expression):

        # 根节点不改变常量表达式语义。
        return _eval_ast(node.body, values)

    # 整数与布尔常量统一转换为 Python int。
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, bool)):

        # 布尔结果以零或一进入 Verilog 条件判断。
        return int(node.value)

    # Name 节点只能读取当前显式参数和循环变量环境。
    if isinstance(node, ast.Name):

        # 缺失引用必须保持未知，不能默认成零。
        if node.id not in values:

            # 错误文本保留缺失标识符供 ParameterValue 诊断。
            raise ValueError(f"> ERR: [Python] unknown parameter reference: {node.id}")

        # 环境映射只保存已确定整数值。
        return int(values[node.id])

    # 一元操作递归求值唯一操作数后应用白名单函数。
    if isinstance(node, ast.UnaryOp):

        # 子表达式必须先得到确定整数。
        int_operand = _eval_ast(node.operand, values)  # 当前一元操作数值

        # 操作类映射避免执行任意 AST 节点。
        dict_operations = {  # 受支持一元操作处理器
            ast.UAdd: lambda value: value,  # 一元正号
            ast.USub: lambda value: -value,  # 一元负号
            ast.Invert: lambda value: ~value,  # 按位取反
            ast.Not: lambda value: int(not value),  # 逻辑取反
        }

        # AST 操作类只允许命中显式白名单。
        function_handler = dict_operations.get(type(node.op))  # 当前一元操作处理器

        # 未登记操作禁止进入参数求值。
        if function_handler is None:

            # 固定前缀使异常符合产品可见诊断合同。
            raise ValueError("> ERR: [Python] unsupported unary parameter operation")

        # 白名单处理结果统一收窄为 int。
        return int(function_handler(int_operand))

    # 二元算术、移位和按位操作分别求值左右子树。
    if isinstance(node, ast.BinOp):

        # 左操作数在当前参数环境中递归求值。
        int_left = _eval_ast(node.left, values)  # 当前二元操作左值

        # 右操作数使用相同环境且不产生副作用。
        int_right = _eval_ast(node.right, values)  # 当前二元操作右值

        # 运算白名单覆盖 Verilog 参数常用整数操作。
        dict_operations = {  # 限定常量表达式二元语法节点能够执行的纯整数运算集合
            ast.Add: lambda a, b: a + b,  # 合成常量表达式两个整数操作数的加法结果
            ast.Sub: lambda a, b: a - b,  # 保留左右顺序的整数参数减法结果
            ast.Mult: lambda a, b: a * b,  # 计算静态数组或位宽常量所需的整数乘积
            ast.FloorDiv: lambda a, b: a // b,  # 对参数整数执行向下取整的整除行为

            # 参数表达式中的斜杠遵循整数参数除法语义。
            ast.Div: lambda a, b: a // b,  # 对 Verilog 参数除法舍弃非整数商部分
            ast.Mod: lambda a, b: a % b,  # 取得静态参数除法后的整数余数
            ast.LShift: lambda a, b: a << b,  # 按已知位数左移常量整数的位模式
            ast.RShift: lambda a, b: a >> b,  # 移除常量低位并保留算术右移后的整数结果

            # 位运算保留整数每一位的组合常量语义。
            ast.BitOr: lambda a, b: a | b,  # 合并两个常量整数的置位比特集合
            ast.BitAnd: lambda a, b: a & b,  # 保留两个常量整数共同置位的比特
            ast.BitXor: lambda a, b: a ^ b,  # 保留两个常量整数取值不同的比特
            ast.Pow: lambda a, b: a**b,  # 计算参数化规模表达式使用的整数幂
        }

        # 当前操作类映射到唯一受控处理器。
        function_handler = dict_operations.get(type(node.op))  # 当前二元操作处理器

        # 白名单外操作保持参数级 unknown。
        if function_handler is None:

            # 产品异常前缀支持统一质量门检查。
            raise ValueError("> ERR: [Python] unsupported binary parameter operation")

        # 二元处理结果收窄为整数供父节点继续运算。
        return int(function_handler(int_left, int_right))

    # BoolOp 保持短表达式的逻辑真值语义。
    if isinstance(node, ast.BoolOp):

        # 所有布尔操作数先求成整数真值。
        list_items = [_eval_ast(item, values) for item in node.values]  # 当前逻辑操作数值

        # ast.And 只有全部非零时结果为一。
        if isinstance(node.op, ast.And):

            # all 的布尔结果转换成 Verilog 条件整数。
            return int(all(list_items))

        # ast.Or 任一非零时结果为一。
        if isinstance(node.op, ast.Or):

            # 任一逻辑操作数非零即可产生 Verilog 真值一。
            return int(any(list_items))

        # 其他布尔节点不属于转换器生成的安全子集。
        raise ValueError("> ERR: [Python] unsupported boolean parameter operation")

    # Compare 支持 Verilog generate 和 loop 的关系条件。
    if isinstance(node, ast.Compare):

        # 链式比较从最左表达式开始逐段验证。
        int_left = _eval_ast(node.left, values)  # 当前比较段左值

        # 操作符与比较项按 Python AST 契约一一对应。
        for operator, comparator in zip(node.ops, node.comparators):

            # 当前比较段右值在相同参数环境中求值。
            int_right = _eval_ast(comparator, values)  # 当前比较段右值

            # 映射同时表达操作支持性和本段真假结果。
            dict_comparisons = {  # 受支持关系操作的当前真值
                ast.Eq: int_left == int_right,  # 相等比较
                ast.NotEq: int_left != int_right,  # 不等比较
                ast.Lt: int_left < int_right,  # 小于比较
                ast.LtE: int_left <= int_right,  # 小于等于比较
                ast.Gt: int_left > int_right,  # 大于比较
                ast.GtE: int_left >= int_right,  # 大于等于比较
            }

            # 不支持操作或任一比较为假都会使完整链结果为零。
            if type(operator) not in dict_comparisons or not dict_comparisons[type(operator)]:

                # 零表示当前关系条件不成立。
                return 0

            # 右值成为下一段链式比较的左值。
            int_left = int_right  # 下一比较段左操作数

        # 所有关系段均成立时返回一。
        return 1

    # IfExp 对应转换后的常量条件选择表达式。
    if isinstance(node, ast.IfExp):

        # 只求值被条件选中的分支，避免未选分支未知引用污染结果。
        ast_branch = node.body if _eval_ast(node.test, values) else node.orelse  # 当前条件选中的 AST 分支

        # 选中分支递归产生最终整数值。
        return _eval_ast(ast_branch, values)

    # 其余 AST 类型不得执行函数调用、属性访问或容器构造。
    raise ValueError(f"> ERR: [Python] unsupported parameter expression node: {type(node).__name__}")

# 常量表达式入口把所有异常转换成局部 unknown_reason。
def _evaluate(expression: object, values: dict[str, int]) -> tuple[int | None, int | None, bool | None, str]:
    """安全求值一个 Verilog 常量表达式。

    参数:
        expression: 整数或 Verilog 表达式文本。
        values: 当前可见的已确定参数环境。

    返回:
        value、literal width、signed 和 unknown_reason 四元组。
    """

    # Python bool 输入直接映射为一位无符号 Verilog 真值。
    if isinstance(expression, bool):

        # 布尔常量无需进入 AST parser。
        return int(expression), 1, False, ""

    # 已是整数的 override 或默认值可以直接采用。
    if isinstance(expression, int):

        # 裸整数不携带显式 Verilog 位宽或符号声明。
        return expression, None, None, ""

    # 其他兼容输入规范化成待解析常量表达式文本。
    str_text = str(expression or "").strip()  # 当前参数常量表达式

    # 空文本明确形成缺失表达式原因。
    if not str_text:

        # 缺失值保持 None，禁止静默取零。
        return None, None, None, "missing parameter expression"

    # 操作符转换同时保留 Verilog 字面量宽度和符号提示。
    tuple_python_expression = _python_expression(str_text)  # Python 文本、位宽、符号和未知原因

    # 字面量含未知位时不再执行占位 AST。
    if tuple_python_expression[3]:

        # 返回未知数值并保留定宽语义和精确原因。
        return None, tuple_python_expression[1], tuple_python_expression[2], tuple_python_expression[3]

    # AST 解析与白名单求值异常都转成当前参数局部原因。
    try:

        # eval 模式只建立表达式树，不执行源码文本。
        ast_parsed = ast.parse(tuple_python_expression[0], mode="eval")  # 当前常量表达式 AST

        # 白名单递归求值成功时返回确定整数。
        return _eval_ast(ast_parsed, values), tuple_python_expression[1], tuple_python_expression[2], ""

    # 语法、未知引用和非法算术只污染当前参数。
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as error:

        # 异常文本进入 fingerprint，确保不同未知原因不共享缓存。
        return None, tuple_python_expression[1], tuple_python_expression[2], str(error)

# 声明位宽优先于字面量提示并支持参数化范围。
def _decl_width(declaration: dict[str, Any], values: dict[str, int]) -> int | None:
    """从参数声明规格求位宽。

    参数:
        declaration: formatter parameter 声明字典。
        values: 当前可见的已确定参数环境。

    返回:
        可确定的正位宽，无法确定时为 None。
    """

    # 已结构化的整数 width 无需重新解析声明文本。
    int_explicit = declaration.get("width")  # formatter 显式位宽字段

    # 正整数或兼容整数值直接成为参数位宽。
    if isinstance(int_explicit, int):

        # formatter 已完成该字段的结构解析。
        return int_explicit

    # 历史报告将范围保存在 decl_spec 或字符串 width 中。
    str_spec = str(declaration.get("decl_spec") or int_explicit or "")  # 当前参数声明规格

    # 方括号捕获上下界表达式并保留参数引用。
    match_range = re.search(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", str_spec)  # 参数位宽范围匹配

    # 无范围声明时让调用方继续采用字面量位宽提示。
    if match_range is None:

        # None 明确表示声明本身未规定位宽。
        return None

    # 上界在当前已确定参数环境中独立求值。
    tuple_upper = _evaluate(match_range.group(1), values)  # 声明范围上界求值结果

    # 下界使用同一环境，允许降序或升序范围。
    tuple_lower = _evaluate(match_range.group(2), values)  # 声明范围下界求值结果

    # 任一边界未知都会使声明宽度保持未知。
    if tuple_upper[3] or tuple_lower[3] or tuple_upper[0] is None or tuple_lower[0] is None:

        # 不完整范围不能伪造一个默认位宽。
        return None

    # Verilog packed range 位宽包含上下界两个端点。
    return abs(tuple_upper[0] - tuple_lower[0]) + 1

# 单项参数求值合并表达式数值、声明宽度和符号规则。
def _parameter_value(
    declaration: dict[str, Any],
    expression: object,
    values: dict[str, int],
) -> ParameterValue:
    """求值一个 parameter 或 localparam 声明。

    参数:
        declaration: 当前参数声明事实。
        expression: 已选择的 override 或默认表达式。
        values: 当前表达式可见的整数环境。

    返回:
        保留位宽、符号和未知原因的 ParameterValue。
    """

    # 参数名是环境插入顺序和 fingerprint 条目的稳定键。
    str_name = str(declaration.get("name") or "")  # 当前参数声明名称

    # 常量求值保留字面量自身的宽度、符号和未知原因。
    tuple_evaluated = _evaluate(expression, values)  # 当前参数的值、位宽、符号与未知原因

    # 声明范围在当前先前参数环境中求值。
    int_declared_width = _decl_width(declaration, values)  # 当前参数声明位宽

    # 显式声明宽度优先于字面量推导提示。
    int_width = int_declared_width if int_declared_width is not None else tuple_evaluated[1]  # 最终参数位宽

    # decl_spec 文本兼容 signed 关键字的历史 formatter 报告。
    str_spec = str(declaration.get("decl_spec") or "")  # 当前参数附加声明规格

    # 新报告可能直接提供布尔 signed 字段。
    obj_declared_signed = declaration.get("signed")  # 当前参数显式符号字段

    # 字段、关键字和字面量提示按权威程度选择最终 signed。
    bool_signed = (  # 当前参数最终有符号属性
        bool(obj_declared_signed)  # 结构化 signed 字段优先
        if obj_declared_signed is not None  # formatter 已明确给出符号属性
        else (True if "signed" in str_spec.split() else tuple_evaluated[2])  # 回落到声明文本或字面量
    )

    # ParameterValue 保留未知原因，使缓存指纹不会误合并。
    return ParameterValue(
        str_name,
        tuple_evaluated[0],
        int_width,
        bool_signed,
        tuple_evaluated[3],
    )

# 确定值映射只暴露无 unknown_reason 的参数项。
def _environment_values(environment: ParameterEnvironment | None) -> dict[str, int]:
    """提取环境内全部已确定整数值。

    参数:
        environment: 父模块或当前模块参数环境。

    返回:
        仅包含确定值的名称到整数临时映射。
    """

    # definition root 之前没有父参数环境。
    if environment is None:

        # 空映射表示 override actual 不能引用父参数。
        return {}

    # 过滤未知项，避免 None 被错误转换为零。
    return {
        item.name: int(item.value)  # 当前确定参数的整数值
        for item in environment.values  # 保持环境声明顺序遍历
        if item.value is not None and not item.unknown_reason  # 只纳入无未知原因的项
    }

# 指纹序列化固定字段顺序后使用 SHA-256 摘要。
def _fingerprint(values: tuple[ParameterValue, ...]) -> str:
    """生成不依赖进程随机哈希的参数指纹。

    参数:
        values: 按声明和求值顺序排列的参数结果。

    返回:
        完整编码 value、width、signed 和 unknown_reason 的 SHA-256 十六进制摘要。
    """

    # 每个参数完整编码名称、值、位宽、符号和未知原因。
    list_payload = [  # 参数环境稳定序列化载荷
        {
            "name": item.name,  # 当前参数名称
            "value": item.value,  # 当前参数整数值或 None
            "width": item.width,  # 当前参数位宽或 None
            "signed": item.signed,  # 当前参数符号属性或 None
            "unknown_reason": item.unknown_reason,  # 当前参数局部未知原因
        }
        for item in values  # 按声明与 localparam 求值顺序编码
    ]

    # 紧凑 JSON 和字段排序消除空白及字典插入顺序差异。
    str_encoded = json.dumps(  # 进入 SHA-256 的确定性 JSON 文本
        list_payload,  # 完整参数语义载荷
        ensure_ascii=True,  # 非 ASCII 字符使用稳定转义
        separators=(",", ":"),  # 删除不影响语义的空白
        sort_keys=True,  # 每个参数字段按字典序编码
    )

    # 十六进制摘要不依赖 Python 进程随机 hash seed。
    return hashlib.sha256(str_encoded.encode("utf-8")).hexdigest()

# association 兼容 named、positional 与简化调用字典。
def _override_parts(override: dict[str, Any]) -> tuple[str, int, object]:
    """归一化 named 或 positional 参数覆盖事实。

    参数:
        override: formatter association 或简化测试覆盖字典。

    返回:
        formal 名、位置和 actual 表达式三元组。
    """

    # formal 字段为空表示 positional override。
    str_formal = str(  # 当前参数覆盖的 formal 名称
        override.get("formal")  # formatter association 公共字段
        or override.get("name")  # 兼容简化 named override
        or override.get("parameter")  # 兼容参数专用字段
        or ""  # positional override 保持空名称
    )

    # position 在 named 情况也保留，供稳定身份和诊断使用。
    int_position = int(override.get("position") or 0)  # 当前覆盖在关联列表中的位置

    # actual 兼容结构化 association 和直接值输入。
    obj_actual = override.get(  # 保留实例参数关联右侧表达式，等待父模块参数环境完成求值
        "actual",  # 新 formatter 关联事实保存的实际参数语法对象
        override.get("value", override.get("expression", "")),  # 旧事实直接保存的实际参数表达式
    )

    # 结构化 actual 优先读取 formatter 保留的原始文本。
    if isinstance(obj_actual, dict):

        # text 缺失时兼容 value 或 expression 字段。
        obj_actual = obj_actual.get("text", obj_actual.get("value", obj_actual.get("expression", "")))  # actual 表达式文本

    # 三元组由 bind_parameter_environment 分派到 named 或 positional 表。
    return str_formal, int_position, obj_actual

# 参数绑定严格区分父环境 override 求值和子环境默认值求值。
def bind_parameter_environment(
    parent: SpecializedModule | None,
    implementation: ModuleImplementation,
    overrides: tuple[dict[str, Any], ...],
) -> ParameterEnvironment:
    """按 Verilog 声明顺序绑定参数并重算 localparam。

    参数:
        parent: override actual 的求值环境；definition root 可传 None。
        implementation: 需要建立 child 环境的 source module 实现。
        overrides: named 或 positional 参数覆盖事实元组。

    返回:
        含稳定语义指纹的有序 ParameterEnvironment。
    """

    # override actual 只能在父 specialization 的参数环境中求值。
    dict_parent_values = _environment_values(  # 父模块可见的确定参数映射
        parent.parameter_environment if parent is not None else None  # root 调用没有父环境
    )

    # named 表按 formal 名查找覆盖表达式。
    dict_named: dict[str, object] = {}  # 当前 child 的 named 参数覆盖

    # positional 表按 association 位置查找覆盖表达式。
    dict_positional: dict[int, object] = {}  # association 位置到实际表达式的绑定表

    # 每项覆盖只进入一种查找表。
    for dict_override in overrides:

        # 归一化结果保留 actual 原始表达式对象。
        tuple_override = _override_parts(dict_override)  # formal、位置与 actual 的覆盖三元组

        # 非空 formal 使用 named binding。
        if tuple_override[0]:

            # 后续同名项沿用最后一次显式关联语义。
            dict_named[tuple_override[0]] = tuple_override[2]  # 当前 formal 的覆盖表达式

        # 空 formal 使用声明位置绑定。
        else:

            # 位置映射只影响对应 child parameter 声明。
            dict_positional[tuple_override[1]] = tuple_override[2]  # 当前位置的覆盖表达式

    # child 默认值按声明顺序只看到此前已确定 child 参数。
    dict_child_values: dict[str, int] = {}  # 当前 child 已完成的确定参数映射

    # 结果列表先保存全部 parameter，再追加 localparam。
    list_results: list[ParameterValue] = []  # 当前 child 有序参数求值结果

    # parameter 顺序决定 positional binding 和默认值依赖可见性。
    for int_position, frozen_declaration in enumerate(implementation.parameters):

        # 当前声明只在本次求值中解冻成独立字典。
        dict_declaration = _mapping(frozen_declaration)  # 当前公开参数声明

        # 名称用于 named override 和 child 环境插入。
        str_name = str(dict_declaration.get("name") or "")  # 当前公开参数名称

        # named override 优先于 positional 和默认表达式。
        if str_name in dict_named:

            # override actual 在父环境中求值，不能引用尚未绑定的 child 参数。
            parameter_value_result = _parameter_value(  # 当前 named override 求值结果
                dict_declaration,  # 当前 child 参数声明
                dict_named[str_name],  # 当前 formal 的 actual 表达式
                dict_parent_values,  # 父 specialization 参数环境
            )

        # 未命名覆盖按 child parameter 声明位置匹配。
        elif int_position in dict_positional:

            # positional actual 同样只读取父环境。
            parameter_value_result = _parameter_value(  # 按声明位置选择的覆盖求值结果
                dict_declaration,  # 被当前位置覆盖的 child 参数声明
                dict_positional[int_position],  # 该 association 位置的 actual 语法
                dict_parent_values,  # actual 可读取的父参数整数映射
            )

        # 无覆盖参数使用自身默认表达式。
        else:

            # formatter 新旧字段统一选择默认值文本。
            obj_expression = dict_declaration.get(  # 未提供实例参数覆盖时，在已绑定子参数环境中求值的声明默认式
                "value",  # 新 formatter 参数声明保存的默认常量表达式
                dict_declaration.get("default", dict_declaration.get("expression", "")),  # 旧报告参数默认表达式回退链
            )

            # 默认值只读取此前已最终确定的 child parameter。
            parameter_value_result = _parameter_value(  # 依赖此前 child 参数的默认值结果
                dict_declaration,  # 未被实例 association 覆盖的参数声明
                obj_expression,  # 当前默认表达式
                dict_child_values,  # 已完成的先前 child 参数环境
            )

        # 未知项也必须按声明位置进入 fingerprint。
        list_results.append(parameter_value_result)

        # 只有无未知原因的整数值才能供后续默认值引用。
        if parameter_value_result.value is not None and not parameter_value_result.unknown_reason:

            # 当前确定值进入 child 顺序求值环境。
            dict_child_values[parameter_value_result.name] = parameter_value_result.value  # 新确定参数值

    # 全部公开参数完成后再按声明顺序重算 localparam。
    for frozen_declaration in implementation.localparams:

        # localparam 声明同样通过一次性解冻副本读取。
        dict_declaration = _mapping(frozen_declaration)  # 当前局部参数声明

        # 新旧 formatter 字段统一选择局部参数表达式。
        obj_expression = dict_declaration.get(  # 从 localparam 声明恢复重算输入
            "value",  # 新 formatter 保存的局部常量表达式
            dict_declaration.get("default", dict_declaration.get("expression", "")),  # 旧报告表达式回退链
        )

        # localparam 可以读取全部已确定 parameter 和先前 localparam。
        parameter_value_result = _parameter_value(  # 当前局部参数求值结果
            dict_declaration,  # 当前 localparam 声明
            obj_expression,  # 当前 localparam 表达式
            dict_child_values,  # 已完成的 child 参数环境
        )

        # 局部参数继续进入完整环境与 fingerprint。
        list_results.append(parameter_value_result)

        # 确定 localparam 可供后续 localparam 依赖。
        if parameter_value_result.value is not None and not parameter_value_result.unknown_reason:

            # 顺序环境更新遵循声明出现次序。
            dict_child_values[parameter_value_result.name] = parameter_value_result.value  # 新确定局部参数值

    # tuple 固化参数顺序并成为 fingerprint 的唯一输入。
    tuple_results = tuple(list_results)  # 完整有序参数与局部参数结果

    # 环境摘要完整包含未知原因和类型语义。
    return ParameterEnvironment(tuple_results, _fingerprint(tuple_results))

# definition root 复用同一绑定器并传入空 override 集合。
def build_default_parameter_environment(
    implementation: ModuleImplementation,
) -> ParameterEnvironment:
    """建立一个 source definition 的默认参数环境。

    参数:
        implementation: 需要按声明默认值求值的模块实现。

    返回:
        不含实例覆盖的有序参数环境。
    """

    # None 父环境确保所有 parameter 采用自身默认表达式。
    return bind_parameter_environment(None, implementation, ())

# 控制节点条件优先读取结构化字段，再回落到 header 括号内容。
def _condition_text(node: dict[str, Any]) -> str:
    """从 generate/control 节点提取条件文本。

    参数:
        node: formatter 或简化材料化控制节点。

    返回:
        去除 if/case 外层语法的常量表达式文本。
    """

    # 简化测试和未来 formatter 可直接提供 condition/expression 字段。
    obj_direct = node.get("condition", node.get("expression", ""))  # 当前节点结构化条件值

    # 非空结构字段无需从语法 header 再次切片。
    if obj_direct:

        # 统一字符串表示供常量 evaluator 使用。
        return str(obj_direct)

    # 现有 formatter 把 if/case 条件保存在 header 中。
    str_header = str(node.get("header") or "")  # 当前控制节点 header 文本

    # 最外层括号内容就是常量条件表达式。
    match_condition = re.search(r"\((.*)\)", str_header)  # header 条件括号匹配

    # 无括号兼容值直接使用完整 header 文本。
    return match_condition.group(1).strip() if match_condition else str_header.strip()

# 循环迭代恢复支持显式列表和 formatter header 两种事实形态。
def _iteration_values(node: dict[str, Any], values: dict[str, int]) -> tuple[int, ...] | None:
    """求 generate/procedural for 的静态迭代值。

    参数:
        node: 包含 range 或 Verilog for header 的控制节点。
        values: 当前参数整数环境。

    返回:
        完整迭代值元组；无法静态求值时为 None。
    """

    # 简化事实可以直接提供完整迭代值序列。
    obj_explicit = node.get("iterations", node.get("iteration_values"))  # 显式循环迭代集合

    # 显式序列无需解析 Verilog for header。
    if isinstance(obj_explicit, (list, tuple)):

        # 每个迭代值统一收窄为整数。
        return tuple(int(item) for item in obj_explicit)

    # formatter loop 节点把初始化、条件和步进保存在 header。
    str_header = str(node.get("header") or node.get("for") or "")  # 当前循环 header 文本

    # 正则同时捕获变量、起点、关系符、终点和步进文本。
    match_loop = re.search(  # 受支持静态 for 结构匹配
        r"for\s*\(\s*(?:genvar\s+|integer\s+)?([A-Za-z_$][\w$]*)\s*=\s*([^;]+);"
        r"\s*\1\s*(<=|<|>=|>)\s*([^;]+);\s*([^)]*)\)",  # 静态循环五段式 header
        str_header,  # 当前 formatter 循环头
    )

    # 不匹配结构保持当前循环局部未知。
    if match_loop is None:

        # None 由材料化调用方转换成精确 unknown region。
        return None

    # 五个捕获组完整描述一个静态整数循环。
    str_variable, str_start_text, str_operator, str_stop_text, str_step_text = match_loop.groups()  # 循环结构字段

    # 起点可以引用当前 specialization 参数。
    tuple_start = _evaluate(str_start_text, values)  # 循环初始值求值结果

    # 终点在同一参数环境中独立求值。
    tuple_stop = _evaluate(str_stop_text, values)  # 循环比较边界求值结果

    # 任一边界未知都禁止猜测迭代数量。
    if tuple_start[3] or tuple_stop[3] or tuple_start[0] is None or tuple_stop[0] is None:

        # 未知边界交由目标级材料化证据处理。
        return None

    # 默认支持常见递增一形式。
    int_step = 1  # 当前循环整数步长

    # 自减或减等一映射为负一步长。
    if "--" in str_step_text or re.search(r"-=\s*1", str_step_text):

        # 递减循环每次减一。
        int_step = -1  # 自减循环步长

    # 加等或减等常量直接给出绝对步长。
    elif match_step := re.search(r"[+-]=\s*(\d+)", str_step_text):

        # 操作符符号决定步长方向。
        int_step = int(match_step.group(1)) * (-1 if "-=" in str_step_text else 1)  # 复合赋值步长

    # i = i +/- N 形式从右值提取常量步长。
    elif match_assign := re.search(rf"{re.escape(str_variable)}\s*[+-]\s*(\d+)", str_step_text):

        # 数字段给出每次迭代移动量。
        int_amount = int(match_assign.group(1))  # 显式赋值步长绝对值

        # 步进表达式中的减号决定负方向。
        int_step = -int_amount if "-" in str_step_text else int_amount  # 显式赋值最终步长

    # 关系符映射为引用确定 stop 的纯比较函数。
    function_compare = {  # 当前循环边界比较器
        "<": lambda item: item < tuple_stop[0],  # 严格递增上界
        "<=": lambda item: item <= tuple_stop[0],  # 包含递增上界
        ">": lambda item: item > tuple_stop[0],  # 严格递减下界
        ">=": lambda item: item >= tuple_stop[0],  # 包含递减下界
    }[str_operator]

    # 结果按硬件展开次序保存每个 genvar/integer 值。
    list_result: list[int] = []  # 当前循环完整迭代值序列

    # 游标从已确定起点开始应用比较和步进。
    int_current = tuple_start[0]  # 当前待判定循环变量值

    # 安全上限阻止畸形零步长输入导致无限展开。
    while function_compare(int_current) and len(list_result) < 100000:

        # 当前满足条件的值对应一个硬件 occurrence。
        list_result.append(int_current)

        # 下一迭代严格应用解析后的整数步长。
        int_current += int_step  # 应用已验证非零方向的循环步长

    # tuple 既保持次序又阻止缓存消费者改写迭代空间。
    return tuple(list_result)

# occurrence 克隆把祖先循环和数组索引合并成完整身份。
def _clone_with_iteration(value: dict[str, Any], iterations: tuple[int, ...]) -> dict[str, Any]:
    """为物化 occurrence 附加完整迭代身份。

    参数:
        value: 待克隆的表达式、实例或控制事实。
        iterations: 从外层到内层的完整迭代值元组。

    返回:
        不共享可变容器且带 iteration_tuple 的事实副本。
    """

    # 顶层字典复制阻止循环身份写回模板缓存。
    dict_cloned = dict(value)  # 当前 occurrence 的独立事实副本

    # instance array 预先附加的索引位于祖先 generate 迭代之后。
    tuple_existing = tuple(int(item) for item in dict_cloned.get("loop_iteration_tuple", ()))  # 既有数组索引元组

    # 完整元组始终遵循外层到内层再到数组的顺序。
    tuple_complete_iterations = iterations + tuple_existing  # 当前 occurrence 完整循环身份

    # 兼容字段继续向已有消费者暴露同一迭代内容。
    dict_cloned["iteration_tuple"] = list(tuple_complete_iterations)  # 兼容迭代身份列表

    # 公共字段明确命名 loop_iteration_tuple。
    dict_cloned["loop_iteration_tuple"] = list(tuple_complete_iterations)  # 权威循环身份列表

    # 过程循环的动态选择在已知迭代环境中必须物化成静态 bit endpoint。
    str_target = str(dict_cloned.get("target") or "")  # 当前 occurrence 的目标文本

    # formatter 保留的 RHS 供当前参数环境消解循环索引。
    str_expression_text = str(dict_cloned.get("expression_text") or "")  # 当前 occurrence 的右值文本

    # 有序变量目录把嵌套选择器映射到外层至内层迭代值。
    list_iteration_variables: list[str] = []  # 按首次出现顺序收集的循环索引变量

    # target 与 RHS 中的单标识符选择器共享当前完整迭代环境。
    for str_variable in re.findall(
        r"\[\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\]",
        f"{str_target} {str_expression_text}",
    ):

        # 同一循环变量多次使用仍只消费一个迭代值。
        if str_variable not in list_iteration_variables:

            # 首次出现次序与外层到内层迭代元组保持一致。
            list_iteration_variables.append(str_variable)

    # 迭代元组从外到内与目标中的动态索引顺序一一对应。
    if list_iteration_variables and len(list_iteration_variables) <= len(tuple_complete_iterations):

        # 只替换明确的单标识符索引，不猜测任意算术选择器。
        for str_variable, int_iteration in zip(
            list_iteration_variables,
            tuple_complete_iterations[-len(list_iteration_variables):],
        ):

            # 当前变量生成同时适用于 target 与 RHS 的选择器匹配文本。
            str_selector_pattern = rf"\[\s*{re.escape(str_variable)}\s*\]"  # 当前动态选择器匹配模式

            # 当前迭代值使用 Verilog 常量位选文本写回。
            str_static_selector = f"[{int_iteration}]"  # 当前迭代对应的静态选择器

            # target 只替换首个当前变量选择器，保留其他嵌套维度。
            str_target_updated = re.sub(str_selector_pattern, str_static_selector, str_target, count=1)  # 当前静态目标

            # 后续循环变量继续基于已替换目标处理。
            str_target = str_target_updated  # 当前累计静态目标文本

            # RHS 中同一变量的全部选择器共用当前硬件迭代值。
            str_expression_updated = re.sub(str_selector_pattern, str_static_selector, str_expression_text)  # 当前静态 RHS

            # 更新后的 RHS 是下一层迭代变量继续消解的输入。
            str_expression_text = str_expression_updated  # 已累计外层索引替换的 RHS

        # 完整替换后目标可进入静态 endpoint 索引。
        dict_cloned["target"] = str_target  # 当前 occurrence 的静态目标端点

        # formatter 的动态 lvalue 原因已被当前特化环境消解。
        if str(dict_cloned.get("parse_error") or "") == "dynamic lvalue selection is not a static endpoint":

            # 清空已消解原因，保留其他解析错误不变。
            dict_cloned["parse_error"] = ""  # 当前动态 lvalue 原因已经消解

        # 从 formatter 已隔离的 RHS 构建当前静态 occurrence 的 typed tree。
        if bool(dict_cloned.get("from_for")) and str_expression_text:

            # 每个迭代使用不同前缀，嵌套 operation ID 再由 tracing 绑定完整 tuple。
            str_prefix = f"{dict_cloned.get('driver_id') or 'loop'}:iter{tuple_complete_iterations}"  # 当前静态循环 RHS 的 source-local occurrence 前缀

            # 解析失败只污染当前迭代，不影响其他已知 clone。
            try:

                # 唯一 formatter parser 为当前静态 RHS 建立 typed tree。
                expression_parser = ExpressionParser(str_expression_text, str_prefix)  # 当前迭代 RHS parser

                # 完整消费结果替换原始含动态索引的表达式树。
                dict_cloned["expression"] = expression_parser.parse()  # 静态化后的当前迭代数据依赖树

            # 专用异常只写入当前静态 occurrence。
            except ExpressionParseError as error:

                # 精确原因阻止失败 RHS 被当作零操作。
                dict_cloned["parse_error"] = str(error)  # 当前迭代 RHS 解析原因

    # 操作或驱动编号存在时也附加迭代身份防止节点合并。
    str_occurrence = str(dict_cloned.get("occurrence_id") or dict_cloned.get("driver_id") or "")  # 既有源码身份

    # 空身份事实只保留结构化迭代字段。
    if str_occurrence:

        # 不同循环 occurrence 获得不同 source-local 操作编号。
        dict_cloned["occurrence_id"] = f"{str_occurrence}:iter{tuple_complete_iterations}"  # 展开后操作身份

    # 返回不共享模板顶层容器的 occurrence 事实。
    return dict_cloned

# 静态 instance array 按声明方向逐项展开。
def _expand_instance_array(instance: dict[str, Any], values: dict[str, int]) -> list[dict[str, Any]]:
    """展开静态 instance array 为独立 occurrence。

    参数:
        instance: formatter 实例事实或简化数组实例字典。
        values: 当前参数整数环境。

    返回:
        每个静态数组索引对应的实例事实列表。
    """

    # 新旧 formatter 字段统一读取 instance array 范围。
    obj_range_value = instance.get(  # 当前实例数组范围事实
        "array_range",  # 结构化数组范围字段
        instance.get("array_range_text", instance.get("range")),  # 文本与兼容字段
    )

    # 普通单实例不产生额外数组 occurrence。
    if obj_range_value is None:

        # 返回独立字典避免调用方修改原模板。
        return [dict(instance)]

    # 结构化二元范围直接提供左右端点。
    if isinstance(obj_range_value, (list, tuple)) and len(obj_range_value) == 2:

        # 端点保持原表达式对象，随后在参数环境中求值。
        obj_left, obj_right = obj_range_value  # 当前数组左右边界表达式

    # 文本范围需要先提取方括号内两个端点。
    else:

        # 正则允许参数化边界和任意外围空白。
        match_range = re.search(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", str(obj_range_value))  # 数组范围文本匹配

        # 无法识别范围时保留原实例并延后精确 unknown 分类。
        if match_range is None:

            # 不猜测数组维度或索引方向。
            return [dict(instance)]

        # 两个捕获组分别保存声明左端点和右端点。
        obj_left, obj_right = match_range.groups()  # 当前数组文本边界表达式

    # 左边界在当前 specialization 参数环境中求值。
    tuple_start = _evaluate(obj_left, values)  # 数组声明左索引求值结果

    # 右边界使用同一参数环境并保留声明方向。
    tuple_stop = _evaluate(obj_right, values)  # 数组声明右索引求值结果

    # 未知边界禁止按单元素数组伪放行。
    if tuple_start[3] or tuple_stop[3] or tuple_start[0] is None or tuple_stop[0] is None:

        # 原实例事实保留待后续绑定层报告局部未知。
        return [dict(instance)]

    # step 保持 Verilog 声明的升序或降序方向。
    int_step = 1 if tuple_stop[0] >= tuple_start[0] else -1  # 当前数组索引步进方向

    # 输出列表按声明方向排列每个静态 occurrence。
    list_expanded: list[dict[str, Any]] = []  # 当前实例数组展开结果

    # 包含右端点的 range 构造完整数组索引集合。
    for int_index in range(tuple_start[0], tuple_stop[0] + int_step, int_step):

        # 每个数组元素获得独立顶层事实字典。
        dict_clone = dict(instance)  # 当前数组元素实例副本

        # 结构化索引供 hierarchy path 编码直接读取。
        dict_clone["instance_index"] = int_index  # 当前实例数组元素索引

        # 数组索引先保存为末维，祖先 generate 迭代随后前置合并。
        dict_clone["loop_iteration_tuple"] = [int_index]  # 当前数组元素局部迭代身份

        # 实例名附加索引以区分同一声明的多个 occurrence。
        str_name = str(dict_clone.get("instance_name") or "")  # 当前数组基础实例名

        # 空名称兼容实例仍可依靠结构化 index 区分。
        if str_name:

            # 路径可读名称使用 Verilog 方括号索引表示。
            dict_clone["instance_name"] = f"{str_name}[{int_index}]"  # 当前数组元素实例名

        # 结果顺序与声明索引方向保持一致。
        list_expanded.append(dict_clone)

    # 调用方随后合并祖先 generate 迭代元组。
    return list_expanded

# defparam 检测递归覆盖模板字典、序列和诊断字符串。
def _contains_defparam(value: object) -> bool:
    """递归检测材料化模板中的 defparam。

    参数:
        value: 当前模板子树。

    返回:
        任意层级包含 defparam 语句或类别时为 True。
    """

    # 字典节点先检查结构类别和源码文本，再递归全部字段。
    if isinstance(value, dict):

        # 类型化 defparam 节点直接命中。
        if str(value.get("kind") or "").lower() == "defparam":

            # 当前模板包含未支持的层次参数改写。
            return True

        # formatter raw statement 可能只在 text 中保留关键字。
        if "defparam" in str(value.get("text") or "").lower():

            # 文本命中同样形成固定特化 unknown。
            return True

        # 任意嵌套字段命中即可确认当前模板包含 defparam。
        return any(_contains_defparam(item) for item in value.values())

    # 列表和元组保持元素顺序递归搜索。
    if isinstance(value, (list, tuple)):

        # 容器任一元素命中即可提前结束。
        return any(_contains_defparam(item) for item in value)

    # 恢复 placeholder 使用字符串字段保存固定原因。
    if isinstance(value, str):

        # 大小写不影响 defparam 关键字识别。
        return "defparam" in value.lower()

    # 其他标量不可能携带 defparam 结构。
    return False

# 文本归一化仅用于同一 formatter 报告内关联 statement 与 instance。
def _normalized_verilog(value: object) -> str:
    """归一化 Verilog 片段用于结构事实关联。

    参数:
        value: formatter 保留的实例或 statement 文本。

    返回:
        去除全部空白后的稳定比较文本。
    """

    # 删除空白但保留所有 Verilog 标点和标识符。
    return "".join(str(value or "").split())

# statement 与 flat instance 的关联优先使用完整原文，再使用双身份回退。
def _statement_instance(
    node: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """把 generate statement 关联回结构化实例事实。

    参数:
        node: formatter generate 控制树中的 statement 节点。
        catalog: 模块级 parser 已识别的结构化实例目录。

    返回:
        命中时返回保留引用分类的实例副本，否则返回 None。
    """

    # statement 原文归一化后用于精确比较 flat instance text。
    str_text = _normalized_verilog(node.get("text"))  # 当前 generate statement 规范文本

    # 空 statement 不可能承载实例声明。
    if not str_text:

        # None 让调用方保留原控制节点。
        return None

    # 实例目录按 formatter 源码顺序寻找第一个唯一匹配。
    for dict_instance in catalog:

        # 完整实例文本是最强关联证据。
        str_instance_text = _normalized_verilog(dict_instance.get("text"))  # 当前目录实例规范文本

        # module 与 instance 双名称用于空白或原文裁剪差异回退。
        str_module_name = str(dict_instance.get("module_name") or "")  # 当前目录实例模块名

        # 实例声明名区分同一 child module 的多个 occurrence。
        str_instance_name = str(dict_instance.get("instance_name") or "")  # 当前目录实例声明名

        # 完整规范文本相等时不需要弱身份回退。
        if str_instance_text and str_instance_text == str_text:

            # 副本保留 parameter/port associations 和引用分类。
            dict_clone = dict(dict_instance)  # 当前精确匹配实例副本

            # kind 让材料化递归把该 statement 分派到实例路径。
            dict_clone["kind"] = "instance"  # 恢复后的控制树节点类别

            # 当前目录项已唯一绑定到 generate statement。
            return dict_clone

        # 原文差异时要求 module 和 instance 两个名称同时出现。
        if (
            str_module_name
            and str_instance_name
            and str_module_name in str_text
            and str_instance_name in str_text
        ):

            # 双身份命中仍复制完整结构化实例事实。
            dict_clone = dict(dict_instance)  # 当前身份回退匹配实例副本

            # 实例类别替换 formatter 的普通 statement 类别。
            dict_clone["kind"] = "instance"  # 回退关联后的节点类别

            # 返回后祖先 generate 控制结构继续负责分支和循环选择。
            return dict_clone

    # 目录无匹配时保持普通 statement 语义。
    return None

# 控制树恢复只替换确定匹配的实例 statement，并深复制其余容器。
def _restore_instances_in_nodes(
    value: object,
    catalog: list[dict[str, Any]],
) -> object:
    """递归恢复 generate 控制树中的实例节点。

    参数:
        value: 当前 generate/control 模板子树。
        catalog: 已标注引用状态的模块级实例目录。

    返回:
        不共享可变容器且 statement 实例已类型化的控制树。
    """

    # 列表节点逐项递归并创建新的可变容器。
    if isinstance(value, list):

        # 元素顺序承载 generate 控制流和源码顺序。
        return [_restore_instances_in_nodes(item, catalog) for item in value]

    # 非字典标量无需实例关联。
    if not isinstance(value, dict):

        # 原不可变标量可安全复用等价值。
        return value

    # 只有 formatter statement 节点需要尝试关联 flat instance。
    if str(value.get("kind") or "").lower() == "statement":

        # 关联结果包含完整实例 associations 和引用状态。
        dict_matched = _statement_instance(value, catalog)  # 当前 statement 的实例匹配结果

        # 确定命中时以结构化实例节点替换普通 statement。
        if dict_matched is not None:

            # 实例节点随后在选中分支和循环 occurrence 中展开。
            return dict_matched

    # 普通字典逐字段递归复制并恢复潜在嵌套 statement。
    return {
        str_key: _restore_instances_in_nodes(item, catalog)  # 当前字段的恢复副本
        for str_key, item in value.items()  # 保持原字段插入顺序
    }

# flat instance 是否归属 generate 决定它能否直接进入基础实例列表。
def _instance_is_generate_owned(
    instance: dict[str, Any],
    generates: object,
) -> bool:
    """判断 flat instance 是否属于 generate 控制树。

    参数:
        instance: 模块级 formatter 实例事实。
        generates: 当前模块的 generate 模板集合。

    返回:
        generate 原文包含当前实例文本或身份时为 True。
    """

    # 规范化 generate 文本便于执行不受空白影响的成员判断。
    str_generate_text = _normalized_verilog(generates)  # 当前模块全部 generate 结构文本

    # 实例原文能提供比名称组合更精确的归属证据。
    str_instance_text = _normalized_verilog(instance.get("text"))  # 当前 flat instance 原文

    # 完整实例文本出现在 generate 中时直接确认受控归属。
    if str_instance_text and str_instance_text in str_generate_text:

        # 精确文本命中无需使用名称回退规则。
        return True

    # 模块名用于兼容 formatter 未保存完整实例原文的报告。
    str_module_name = str(instance.get("module_name") or "")  # 被实例化模块名称

    # 实例名与模块名共同降低回退匹配的误判概率。
    str_instance_name = str(instance.get("instance_name") or "")  # 当前实例标识符

    # 只有两个名称都存在且同时落在 generate 文本中才确认归属。
    return bool(
        str_module_name
        and str_instance_name
        and str_module_name in str_generate_text
        and str_instance_name in str_generate_text
    )

# generate-if 节点只物化被参数环境选中的分支。
def _materialize_if_node(
    node: dict[str, Any],
    values: dict[str, int],
    iterations: tuple[int, ...],
    expressions: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """物化一个 generate-if 节点。

    参数:
        node: 当前 generate-if 事实。
        values: 当前参数和循环变量整数环境。
        iterations: 外层循环迭代身份。
        expressions: 特化共享的组合事实输出列表。
        instances: 特化共享的实例事实输出列表。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        选中分支是否包含确定循环。
    """

    # 条件求值同时返回不支持表达式的局部原因。
    tuple_condition = _evaluate(_condition_text(node), values)  # 条件值、宽度、符号和原因

    # 未知条件必须截断当前 generate 分支，不能猜测结构。
    if tuple_condition[3] or tuple_condition[0] is None:

        # 局部 unknown 只覆盖当前无法选择的 generate 区域。
        unknowns.append(UnknownRegion((), "unknown generate condition", True))

        # 未展开的未知分支不能证明存在确定循环。
        return False

    # 布尔条件决定 then 或 else 子树，且保持 formatter 原始顺序。
    obj_selected = (
        node.get("children", node.get("then", []))  # 条件为真时选择 then 子树
        if tuple_condition[0]  # 已确定的非零 Verilog 条件值
        else node.get("alternate", node.get("else", []))  # 条件为假时选择 else 子树
    )  # 当前参数环境选中的 generate-if 子树

    # 递归物化只访问已经确定的一个分支。
    return _materialize_nodes(
        obj_selected,
        values,
        iterations,
        expressions,
        instances,
        unknowns,
    )

# generate-case 节点按 selector 选择首个匹配项或 default。
def _materialize_case_node(
    node: dict[str, Any],
    values: dict[str, int],
    iterations: tuple[int, ...],
    expressions: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """物化一个 generate-case 节点。

    参数:
        node: 当前 generate-case 事实。
        values: 当前参数和循环变量整数环境。
        iterations: 外层循环迭代身份。
        expressions: 特化共享的组合事实输出列表。
        instances: 特化共享的实例事实输出列表。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        选中 case 分支是否包含确定循环。
    """

    # selector 求值结果保留无法静态解释时的原因。
    tuple_selector = _evaluate(_condition_text(node), values)  # selector 值及类型信息

    # 未知 selector 无法安全决定任何 case 分支。
    if tuple_selector[3] or tuple_selector[0] is None:

        # 当前 case 区域以 may-contain-loop 保守截断。
        unknowns.append(UnknownRegion((), "unknown generate case selector", True))

        # 未选择 case 项时不报告确定循环。
        return False

    # 空列表表示尚未命中普通 case 项。
    obj_selected: object = []  # 当前 selector 选中的节点集合

    # default 单独保留，只有普通标签均未命中时采用。
    obj_default: object = []  # 当前 case 的默认节点集合

    # Verilog case 采用源码顺序的首项匹配语义。
    for dict_item in node.get("items", []) or []:

        # 单标签和值列表统一为可迭代序列。
        obj_labels = dict_item.get("labels", dict_item.get("values", dict_item.get("value", [])))  # 当前 case 项标签

        # 标量标签包装后与 formatter 列表形式共享判断路径。
        list_labels = list(obj_labels) if isinstance(obj_labels, (list, tuple)) else [obj_labels]  # 规范化标签列表

        # default 项延后使用，避免覆盖后续普通匹配项。
        if any(str(obj_label).lower() == "default" for obj_label in list_labels):

            # 保存 default 子树但继续扫描显式标签。
            obj_default = dict_item.get("children", dict_item.get("body", []))  # 默认 case 子树

            # default 本身不终止显式标签查找。
            continue

        # 任一标签与 selector 相等即可选中当前源码项。
        if any(_evaluate(obj_label, values)[0] == tuple_selector[0] for obj_label in list_labels):

            # 首个匹配项的子树成为唯一物化目标。
            obj_selected = dict_item.get("children", dict_item.get("body", []))  # 命中的 case 子树

            # Verilog case 不再考察后续项。
            break

    # 没有普通标签命中时才回落到 default。
    if not obj_selected:

        # 缺失 default 时仍保持空节点集合。
        obj_selected = obj_default  # 最终选中的 case 子树

    # 递归物化确定的 case 子树并传播循环存在性。
    return _materialize_nodes(
        obj_selected,
        values,
        iterations,
        expressions,
        instances,
        unknowns,
    )

# generate-for 节点以稳定迭代元组展开其受控子树。
def _materialize_loop_node(
    node: dict[str, Any],
    values: dict[str, int],
    iterations: tuple[int, ...],
    expressions: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """物化一个静态 generate 或过程循环节点。

    参数:
        node: 当前循环控制事实。
        values: 当前参数和外层循环变量环境。
        iterations: 外层完整迭代元组。
        expressions: 特化共享的组合事实输出列表。
        instances: 特化共享的实例事实输出列表。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        边界确定时为 True，否则为 False。
    """

    # 循环边界只由已知参数和外层迭代变量求值。
    list_iteration_values = _iteration_values(node, values)  # 当前层静态迭代值

    # 边界无法静态确定时禁止部分展开循环体。
    if list_iteration_values is None:

        # 未知循环可能隐藏任意数量的组合 occurrence。
        unknowns.append(UnknownRegion((), "unknown generate loop bounds", True))

        # 不完整展开不能作为确定循环证据。
        return False

    # header 回退提取兼容 formatter 未单列 variable 的事实。
    match_variable = re.search(  # 从 generate 循环 header 恢复变量名
        r"for\s*\(\s*(?:genvar\s+|integer\s+)?([A-Za-z_$][\w$]*)",  # 变量捕获规则
        str(node.get("header") or ""),  # 当前 generate 循环头文本
    )  # 循环变量名称匹配结果

    # 显式 variable 优先于 header 正则恢复结果。
    str_variable = str(  # 用于嵌套边界求值的循环变量名称
        node.get("variable")  # formatter 显式循环变量字段
        or (match_variable.group(1) if match_variable else "")  # header 恢复名称
    )  # 当前循环变量名称

    # formatter 的 children 与 body 两种结构统一为当前循环体。
    obj_body = node.get("children", node.get("body", []))  # 待按迭代复制的循环体

    # 每次迭代获得独立整数环境与追加后的 occurrence 身份。
    for int_item in list_iteration_values:

        # 子环境隔离循环变量，避免污染兄弟迭代。
        dict_child_values = dict(values)  # 当前迭代参数与循环变量环境

        # 有可恢复变量名时才写入迭代值。
        if str_variable:

            # 当前层变量值供嵌套边界和表达式求值使用。
            dict_child_values[str_variable] = int_item  # 当前循环变量绑定

        # 子树输出直接汇入当前 specialization 的稳定列表。
        _materialize_nodes(
            obj_body,
            dict_child_values,
            iterations + (int_item,),
            expressions,
            instances,
            unknowns,
        )

    # 已知边界即证明当前特化包含静态循环。
    return True

# 实例节点展开数组维度并附加外层循环身份。
def _materialize_instance_node(
    node: dict[str, Any],
    values: dict[str, int],
    iterations: tuple[int, ...],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """物化一个普通或数组模块实例节点。

    参数:
        node: 当前实例事实。
        values: 当前参数和循环变量整数环境。
        iterations: 外层 generate 循环身份。
        instances: 特化共享的实例事实输出列表。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        实例自身不代表循环，恒为 False。
    """

    # 实例数组按声明方向生成独立 occurrence。
    for dict_expanded in _expand_instance_array(node, values):

        # 外层循环身份放在数组下标之前，保持路径顺序。
        instances.append(_clone_with_iteration(dict_expanded, iterations))

        # 引用解析失败原因已由实现索引阶段局部标注。
        str_reason = str(dict_expanded.get("reference_unknown_reason") or "")  # 当前实例引用未知原因

        # 只有真实原因才追加未知区域，正常引用不产生噪声。
        if str_reason:

            # 递归原因需要保守标记潜在循环，其余引用失败保持局部。
            unknowns.append(UnknownRegion((), str_reason, "recursive" in str_reason))

    # 实例数组展开不等同于 generate/procedural loop。
    return False

# 控制节点分派保持每类 generate 语义相互隔离。
def _materialize_node(
    node: dict[str, Any],
    values: dict[str, int],
    iterations: tuple[int, ...],
    expressions: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """递归选择一个 generate/control 节点。

    参数:
        node: 当前控制、语句或实例节点。
        values: 当前参数和循环变量整数环境。
        iterations: 从外到内的完整循环迭代元组。
        expressions: 当前特化共享的组合事实输出列表。
        instances: 当前特化共享的实例事实输出列表。
        unknowns: 当前特化共享的局部未知区域列表。

    返回:
        当前节点或子树是否包含确定循环。
    """

    # formatter 的 kind/type 两种节点类别字段统一为小写分派键。
    str_kind = str(node.get("kind") or node.get("type") or "").lower()  # 当前控制节点类别

    # 条件节点交由单一职责 helper 完成分支选择。
    if str_kind in {"if", "generate_if", "gen_if"}:

        # helper 只访问选中的 generate-if 子树。
        return _materialize_if_node(node, values, iterations, expressions, instances, unknowns)

    # case 节点需要遵守首项匹配与 default 回退语义。
    if str_kind in {"case", "generate_case", "gen_case"}:

        # selector 求值和标签选择封装在 case 专用 helper 中。
        return _materialize_case_node(node, values, iterations, expressions, instances, unknowns)

    # generate 和 procedural for 共享静态边界展开合同。
    if str_kind in {"for", "loop", "generate_for", "gen_for", "procedural_for"}:

        # 循环 helper 为每个迭代追加完整 occurrence 身份。
        return _materialize_loop_node(node, values, iterations, expressions, instances, unknowns)

    # 含 module_name 的兼容节点即使缺少 kind 也按实例处理。
    if str_kind in {"instance", "module_instance"} or "module_name" in node:

        # 实例 helper 同时展开 instance array 与引用未知原因。
        return _materialize_instance_node(node, values, iterations, instances, unknowns)

    # 普通组合 statement 直接进入当前特化表达式目录。
    if str_kind in {"statement", "assign", "expression"} or "target" in node:

        # 克隆操作隔离 formatter 原始事实并附加循环身份。
        expressions.append(_clone_with_iteration(node, iterations))

        # 单条表达式不单独证明控制树存在循环。
        return False

    # 容器节点递归访问其 nodes 或 children 字段。
    return _materialize_nodes(
        node.get("nodes", node.get("children", [])),
        values,
        iterations,
        expressions,
        instances,
        unknowns,
    )

# 节点序列入口聚合任意子树的确定循环标志。
def _materialize_nodes(
    nodes: object,
    values: dict[str, int],
    iterations: tuple[int, ...],
    expressions: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """递归物化一个控制节点序列。

    参数:
        nodes: 当前层级的节点字典或节点序列。
        values: 当前参数和循环变量整数环境。
        iterations: 当前完整循环迭代元组。
        expressions: 当前特化共享的组合事实列表。
        instances: 当前特化共享的实例事实列表。
        unknowns: 当前特化共享的未知区域列表。

    返回:
        任意节点包含确定循环时为 True。
    """

    # 标量节点包装后与 formatter 序列采用同一遍历路径。
    list_sequence = list(nodes) if isinstance(nodes, (list, tuple)) else [nodes]  # 当前层节点序列

    # 循环存在性从 False 开始按子树结果累积。
    bool_contains_loop = False  # 当前层是否发现确定循环

    # 源码顺序遍历保证表达式与实例 occurrence 输出稳定。
    for obj_node in list_sequence:

        # 非字典占位项没有可物化的结构字段。
        if isinstance(obj_node, dict):

            # 已发现循环后仍遍历剩余节点，但保留 True 聚合值。
            bool_node_loop = _materialize_node(  # 当前控制子树是否展开出确定循环
                obj_node,  # 当前结构化控制节点
                values,  # 当前参数和循环变量环境
                iterations,  # 当前祖先迭代身份
                expressions,  # 共享组合事实输出列表
                instances,  # 共享实例输出列表
                unknowns,  # 共享局部未知区域列表
            )  # 当前节点是否包含确定循环

            # 兄弟节点循环证据通过逻辑或保留。
            bool_contains_loop = bool_node_loop or bool_contains_loop  # 当前层累计循环存在性

    # 返回聚合标志供上层 specialization 计算 loop_presence。
    return bool_contains_loop

# 过程控制树只恢复循环 occurrence 身份，不复制表达式语义。
def _process_loop_tuples(
    value: object,
    values: dict[str, int],
    prefix: tuple[int, ...] = (),
) -> list[tuple[int, ...]] | None:
    """从过程控制树恢复静态循环迭代身份。

    参数:
        value: formatter control_processes 子树。
        values: 当前 specialization 参数整数环境。
        prefix: 外层过程循环已经确定的迭代元组。

    返回:
        循环 occurrence 元组列表；存在未知边界时为 None。
    """

    # 序列节点按源码顺序拼接各子树的 occurrence 元组。
    if isinstance(value, list):

        # 当前层收集器保持外层到内层的稳定顺序。
        list_collected: list[tuple[int, ...]] = []  # 已恢复的过程循环身份

        # 每个兄弟控制节点独立递归恢复。
        for obj_item in value:

            # 子树返回 None 表示至少一个边界未知。
            list_child = _process_loop_tuples(obj_item, values, prefix)  # 当前子树迭代身份

            # 任一未知子树都会使当前过程循环集合不完整。
            if list_child is None:

                # 调用方随后把相关 procedural-for 标为局部 unknown。
                return None

            # 已知子树元组按原顺序追加。
            list_collected.extend(list_child)

        # 完成当前序列的全部静态循环恢复。
        return list_collected

    # 非字典叶子不含可识别控制节点。
    if not isinstance(value, dict):

        # 空列表表示已知不存在循环，而非求值失败。
        return []

    # kind 字段决定当前控制节点是否为循环。
    str_kind = str(value.get("kind") or "").lower()  # 当前过程控制节点类别

    # 过程 for 与 formatter loop 别名进入静态迭代恢复。
    if str_kind in {"for", "loop", "procedural_for"}:

        # 循环边界采用与 generate-for 相同的常量求值器。
        list_iteration_values = _iteration_values(value, values)  # 当前过程循环静态迭代值

        # 无法确定边界时不伪造 occurrence 数量。
        if list_iteration_values is None:

            # None 向上传播到表达式局部 unknown 处理。
            return None

        # header 回退恢复未结构化提供的循环变量名。
        match_variable = re.search(  # 从过程循环 header 恢复变量名
            r"for\s*\(\s*(?:genvar\s+|integer\s+)?([A-Za-z_$][\w$]*)",  # 过程头首个赋值变量捕获式
            str(value.get("header") or ""),  # 当前过程循环头文本
        )  # 过程循环变量名称匹配结果

        # 显式字段优先，正则结果仅用于兼容旧 formatter 报告。
        str_variable = str(  # 用于递归控制树求值的过程循环变量
            value.get("variable")  # formatter 显式过程循环变量
            or (match_variable.group(1) if match_variable else "")  # 缺少结构字段时采用正则捕获标识符
        )  # 当前过程循环变量名称

        # 每个确定迭代产生一个或多个最内层 occurrence 元组。
        list_results: list[tuple[int, ...]] = []  # 当前过程循环展开结果

        # 按 Verilog 循环方向展开迭代值。
        for int_item in list_iteration_values:

            # 子环境隔离当前循环变量值。
            dict_child_values = dict(values)  # 当前过程迭代整数环境

            # 可识别变量名时提供给嵌套循环边界求值。
            if str_variable:

                # 当前迭代绑定只在子树递归期间有效。
                dict_child_values[str_variable] = int_item  # 当前过程循环变量绑定

            # 嵌套子树把当前迭代追加到外层 prefix。
            list_children = _process_loop_tuples(  # 当前迭代下的内层循环身份
                value.get("children", value.get("body", [])),  # 当前过程循环体
                dict_child_values,  # 绑定本次迭代值的参数环境
                prefix + (int_item,),  # 外层到当前层的完整前缀
            )  # 当前迭代的内层 occurrence 元组

            # 内层任意未知边界使整个过程循环集合不完整。
            if list_children is None:

                # None 保留未知边界而不退化为零次循环。
                return None

            # 没有内层循环时当前层迭代本身就是 occurrence。
            list_results.extend(list_children or [prefix + (int_item,)])

        # 返回当前循环按外到内排列的完整身份元组。
        return list_results

    # 非循环控制容器继续扫描可能承载循环的标准子字段。
    list_collected: list[tuple[int, ...]] = []  # 当前容器发现的嵌套循环身份

    # formatter 控制树可能把子节点分布在四类字段中。
    for str_key in ("nodes", "children", "alternate", "items"):

        # 当前字段独立恢复并保持字段声明顺序。
        list_child = _process_loop_tuples(value.get(str_key, []), values, prefix)  # 当前子字段循环身份

        # 未知边界必须传播，不能只返回其他已知兄弟结果。
        if list_child is None:

            # 上层据此将依赖过程循环的表达式标为 unknown。
            return None

        # 已知结果追加到当前控制容器集合。
        list_collected.extend(list_child)

    # 普通容器完成全部潜在子树扫描。
    return list_collected

# 组合事实优先使用类型化目录，旧报告回退到连续赋值。
def _specialization_expressions(template: dict[str, Any]) -> list[dict[str, Any]]:
    """建立 specialization 的初始组合表达式列表。

    参数:
        template: 当前模块的可变材料化模板副本。

    返回:
        与 formatter 报告断开引用的表达式字典列表。
    """

    # 新版 formatter 的 comb_expressions 已统一连续赋值和过程表达式。
    list_expressions = [
        dict(dict_item)  # 当前组合事实独立顶层副本
        for dict_item in template.get("comb_expressions", []) or []  # 类型化组合事实目录
        if isinstance(dict_item, dict)  # 忽略非结构化兼容占位项
    ]  # 当前特化初始组合表达式

    # 旧报告缺少新目录时保留连续赋值分析能力。
    if not list_expressions:

        # 回退事实仍复制顶层，避免写入 loop_iteration_tuple 时污染模板。
        list_expressions.extend(
            dict(dict_item)  # 当前连续赋值独立顶层副本
            for dict_item in template.get("continuous_assigns", []) or []  # 旧 formatter assign 目录
            if isinstance(dict_item, dict)  # 只保留结构化 assign 事实
        )

    # 调用方随后在此独立列表上展开过程循环 occurrence。
    return list_expressions

# flat instance 目录只接纳不受 generate 控制的基础实例。
def _materialize_flat_instances(
    template: dict[str, Any],
    catalog: list[dict[str, Any]],
    values: dict[str, int],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> None:
    """物化模块级非 generate 实例。

    参数:
        template: 当前模块的材料化模板。
        catalog: formatter 模块级实例目录。
        values: 当前参数整数环境。
        instances: 特化共享的实例输出列表。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        无；结果直接追加到调用方拥有的列表。
    """

    # 实例按 formatter 源码顺序进入基础 occurrence 列表。
    for dict_item in catalog:

        # generate 受控实例只能在分支选择后由控制树物化。
        if _instance_is_generate_owned(dict_item, template.get("generates", []) or []):

            # 跳过 flat 重复项，避免同一实例被计算两次。
            continue

        # 实现解析阶段提供唯一绑定失败的精确局部原因。
        str_reason = str(dict_item.get("reference_unknown_reason") or "")  # 阻止该实例进入子实现的解析原因

        # 正常唯一绑定实例不产生未知区域。
        if str_reason:

            # 递归引用保守标记潜在循环，其他失败保持非循环未知。
            unknowns.append(UnknownRegion((), str_reason, "recursive" in str_reason))

        # 实例数组在当前参数环境下展开为独立 occurrence。
        instances.extend(_expand_instance_array(dict_item, values))

# generate 控制树恢复实例节点后执行参数驱动的结构选择。
def _materialize_generate_trees(
    template: dict[str, Any],
    catalog: list[dict[str, Any]],
    values: dict[str, int],
    expressions: list[dict[str, Any]],
    instances: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """物化模块的全部 generate 控制树。

    参数:
        template: 当前模块材料化模板。
        catalog: 可用于恢复 statement 实例的模块级目录。
        values: 当前参数整数环境。
        expressions: 特化共享的组合事实输出列表。
        instances: 特化共享的实例事实输出列表。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        任一选中 generate 子树包含确定循环时为 True。
    """

    # formatter statement 中的实例原文先恢复为结构化实例节点。
    obj_restored_generates = _restore_instances_in_nodes(  # 把 generate statement 原文替换为可展开实例事实
        template.get("generates", []) or [],  # 尚未恢复实例引用的生成控制森林
        catalog,  # 用于匹配 statement 原文的模块实例目录
    )  # 已恢复实例节点的 generate 控制树

    # 初始状态尚未观察到确定 generate 循环。
    bool_contains_loop = False  # 当前模块 generate 循环存在性

    # 只有列表形控制树能够保持多个 generate 块的源码顺序。
    list_generates = obj_restored_generates if isinstance(obj_restored_generates, list) else []  # generate 根节点列表

    # 每个 generate 根独立选择分支并展开 occurrence。
    for obj_generate in list_generates:

        # 非字典兼容项不包含可解释控制结构。
        if not isinstance(obj_generate, dict):

            # 跳过占位项而不影响其他 generate 根。
            continue

        # 子树循环结果与已有结果执行逻辑或聚合。
        bool_contains_loop = (  # 汇总当前 generate 根的静态循环证据
            _materialize_nodes(  # 仅展开参数环境选中的控制子树
                obj_generate.get("nodes", obj_generate),  # 当前 generate 根节点集合
                values,  # 当前 specialization 参数整数环境
                (),  # 根 generate 尚无外层迭代身份
                expressions,  # 接收选中分支表达式 occurrence 的累积容器
                instances,  # 接收选中分支模块实例 occurrence 的累积容器
                unknowns,  # 接收未知条件与引用失败证据的累积容器
            )
            or bool_contains_loop  # 保留先前 generate 根发现的循环
        )

    # 返回确定 generate-for 的聚合存在性。
    return bool_contains_loop

# formatter 的过程循环标记展开为逐次组合 occurrence。
def _expand_procedural_expressions(
    template: dict[str, Any],
    values: dict[str, int],
    expressions: list[dict[str, Any]],
    unknowns: list[UnknownRegion],
) -> bool:
    """展开带 from_for 标记的过程组合表达式。

    参数:
        template: 当前模块材料化模板。
        values: 当前参数整数环境。
        expressions: 可原位替换的组合事实列表。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        至少一个过程循环被确定展开时为 True。
    """

    # 控制树提供 formatter 未直接写入表达式的嵌套循环身份。
    list_process_iterations = _process_loop_tuples(  # 从 always 控制树恢复迭代身份
        template.get("control_processes", []) or [],  # always 控制树目录
        values,  # 过程 for 边界可引用的确定参数值
    )  # 已恢复的过程循环迭代元组

    # 初始状态尚未展开任何过程循环表达式。
    bool_contains_loop = False  # 过程循环存在性

    # tuple 快照允许遍历期间安全替换原列表成员。
    for dict_expression in tuple(expressions):

        # 非循环表达式保持原样，不附加虚假的迭代身份。
        if not bool(dict_expression.get("from_for")):

            # 继续检查其余组合事实。
            continue

        # 旧 formatter 可能直接给出单层循环次数。
        int_count = int(dict_expression.get("loop_iterations") or 0)  # 显式过程循环迭代次数

        # 正整数次数优先生成稳定的零基 occurrence。
        if int_count > 0:

            # 原聚合表达式由逐次克隆事实替代。
            expressions.remove(dict_expression)

            # 每个 index 作为唯一过程循环身份附加。
            expressions.extend(
                _clone_with_iteration(dict_expression, (int_index,))
                for int_index in range(int_count)
            )

            # 成功展开证明当前特化含确定过程循环。
            bool_contains_loop = True  # 显式次数证明存在过程循环

            # 显式次数已经完整处理当前表达式。
            continue

        # 新控制树能提供嵌套或参数化的静态迭代元组。
        if list_process_iterations:

            # 移除未区分 occurrence 的 formatter 聚合事实。
            expressions.remove(dict_expression)

            # 外到内元组逐一克隆为独立组合事实。
            expressions.extend(
                _clone_with_iteration(dict_expression, tuple_iteration)
                for tuple_iteration in list_process_iterations
            )

            # 至少一个控制树元组证明存在确定循环。
            bool_contains_loop = True  # 控制树元组证明存在过程循环

            # 当前表达式已由控制树 occurrence 完整替换。
            continue

        # 无显式次数且控制树未知时保留局部不确定证据。
        unknowns.append(
            UnknownRegion(
                (str(dict_expression.get("target") or ""),),
                "unknown procedural for bounds",
                True,
            )
        )

    # 返回过程循环是否被可靠展开。
    return bool_contains_loop

# 未知参数只在实际影响模块结构时升级为特化未知区域。
def _append_parameter_unknowns(
    template: dict[str, Any],
    environment: ParameterEnvironment,
    unknowns: list[UnknownRegion],
) -> None:
    """追加结构相关的未知参数原因。

    参数:
        template: 当前模块材料化模板。
        environment: 已求值的参数环境。
        unknowns: 特化共享的局部未知区域列表。

    返回:
        无；命中的未知原因直接追加到列表。
    """

    # 没有未知参数时避免序列化完整模板。
    if not any(parameter_value.unknown_reason for parameter_value in environment.values):

        # 正常参数环境无需增加 unknown region。
        return

    # 稳定 JSON 文本用于保守判断参数名是否参与结构模板。
    str_structural_text = json.dumps(template, ensure_ascii=True, sort_keys=True)  # 可搜索的结构模板文本

    # 每个未知参数独立判断，避免一个原因污染无关参数。
    for parameter_value in environment.values:

        # 原因存在且名称落入模板时才影响当前物化结构。
        if parameter_value.unknown_reason and re.search(
            rf"\b{re.escape(parameter_value.name)}\b",
            str_structural_text,
        ):

            # 精确原因携带参数名，便于调用路径定位。
            unknowns.append(
                UnknownRegion(
                    (),
                    f"unknown parameter {parameter_value.name}: {parameter_value.unknown_reason}",
                    True,
                )
            )

# 参数环境物化入口组装不可变 SpecializedModule 并复用缓存。
def materialize_specialization(
    implementation: ModuleImplementation,
    environment: ParameterEnvironment,
    cache: dict[SpecializationKey, SpecializedModule],
) -> SpecializedModule:
    """按参数环境物化不可变模块组合图并写入缓存。

    参数:
        implementation: 需要物化的 source module 实现。
        environment: 已按声明顺序完成的参数环境。
        cache: 仅以 SpecializationKey 为键的调用方缓存。

    返回:
        可跨调用路径安全复用的 frozen SpecializedModule。
    """

    # 实现身份和规范参数指纹共同形成唯一缓存键。
    specialization_key = SpecializationKey(implementation.identity, environment.fingerprint)  # 当前特化缓存身份

    # 完全相同的实现与参数环境必须复用同一不可变对象。
    if specialization_key in cache:

        # 返回缓存对象维持身份级复用合同。
        return cache[specialization_key]

    # 材料化只操作从冻结实现中恢复的独立模板副本。
    dict_template = _mapping(implementation.materialization_template)  # 当前实现可变求值模板

    # 已知参数整数值为 generate 和数组范围求值提供环境。
    dict_values = _environment_values(environment)  # 当前 specialization 整数参数环境

    # 初始组合事实优先来自 formatter 类型化目录。
    list_expressions = _specialization_expressions(dict_template)  # 待展开组合表达式列表

    # 模块级实例目录为 generate statement 恢复和基础实例提供来源。
    list_catalog = [
        dict(dict_item)  # 当前实例事实独立顶层副本
        for dict_item in dict_template.get("instances", []) or []  # 供 flat 与 generate 恢复共享的目录
        if isinstance(dict_item, dict)  # 忽略非结构化兼容条目
    ]  # 当前实现实例目录

    # 两类输出列表由本次物化独占，完成后统一冻结。
    list_instances: list[dict[str, Any]] = []  # 已物化实例 occurrence

    # unknown region 保持局部原因与 may-contain-loop 信息。
    list_unknowns: list[UnknownRegion] = []  # 当前特化未知区域

    # 非 generate 基础实例先按模块源码顺序进入输出。
    _materialize_flat_instances(
        dict_template,
        list_catalog,
        dict_values,
        list_instances,
        list_unknowns,
    )

    # generate 选择与循环展开追加受控表达式和实例。
    bool_generate_loop = _materialize_generate_trees(  # generate 控制树循环存在性
        dict_template,  # 当前实现材料化模板
        list_catalog,  # 模块级实例恢复目录
        dict_values,  # 展开 always 循环边界所需的确定参数值
        list_expressions,  # 可追加的组合事实列表
        list_instances,  # 可追加的实例 occurrence 列表
        list_unknowns,  # 可追加的局部未知区域列表
    )  # 是否存在确定 generate 循环

    # 过程循环 occurrence 与 generate 循环共同决定已知循环存在性。
    bool_process_loop = _expand_procedural_expressions(  # 过程循环展开存在性
        dict_template,  # 当前实现过程控制模板
        dict_values,  # specialization 参数整数环境
        list_expressions,  # 待展开的组合事实列表
        list_unknowns,  # 未知循环边界输出列表
    )  # 是否存在确定过程循环

    # defparam 不参与参数绑定，固定为当前模块局部不支持区域。
    if _contains_defparam(dict_template):

        # 固定原因与恢复路径合同保持一致。
        list_unknowns.append(UnknownRegion((), "defparam specialization unsupported", True))

    # 未知参数只在结构模板实际引用时影响 specialization。
    _append_parameter_unknowns(dict_template, environment, list_unknowns)

    # 任一未知区域可含循环时，loop_presence 必须保持 unknown。
    str_loop_presence = (
        "unknown"  # 未知区域可能隐藏循环时保持保守三态
        if any(unknown_region.may_contain_loop for unknown_region in list_unknowns)  # 潜在循环证据
        else "present"  # 任一已知循环完成展开时为 present
        if bool_generate_loop or bool_process_loop  # 任一结构已证明包含循环
        else "absent"  # 无已知或潜在循环时为 absent
    )  # 当前特化循环存在性三态

    # 输出边界冻结所有可变事实，保证缓存对象不可被调用方污染。
    specialized_module = SpecializedModule(  # 完整冻结后的模块特化组合图
        key=specialization_key,  # 实现身份和参数指纹组成的缓存键
        parameter_environment=environment,  # 完整有序参数语义
        ports=implementation.ports,  # source 实现冻结端口目录
        comb_expressions=tuple(_frozen_mapping(dict_item) for dict_item in list_expressions),  # 物化组合事实
        instances=tuple(_frozen_mapping(dict_item) for dict_item in list_instances),  # 物化实例 occurrence
        functions=implementation.functions,  # source 实现本地函数定义

        # 存储驱动保留为组合锥分析的寄存器切点证据。
        storage_drivers=tuple(  # 寄存器与锁存器驱动切点的冻结事实
            _frozen_mapping(dict_item)  # 当前存储驱动模板冻结副本
            for dict_item in dict_template.get("storage_driver_templates", []) or []  # formatter 存储切点目录
            if isinstance(dict_item, dict)  # 只接受结构化驱动事实
        ),
        loop_presence=str_loop_presence,  # 综合已知循环与潜在循环证据的三态结果
        unknown_regions=tuple(list_unknowns),  # 局部未知原因与潜在循环标记
    )  # 当前参数环境的不可变组合图

    # 缓存写入发生在对象完全构造之后，避免暴露半成品。
    cache[specialization_key] = specialized_module  # 保存可复用 specialization

    # 返回值与缓存条目保持同一对象身份。
    return specialized_module

# 默认根枚举为每个 source 实现建立可追踪的入口特化键。
def enumerate_definition_roots(
    index: ModuleImplementationIndex,
) -> tuple[DefinitionRoot, ...]:
    """为每个 source module 定义建立默认特化 root。

    参数:
        index: 已稳定排序的 source-only 模块实现索引。

    返回:
        与实现索引顺序一致的默认 DefinitionRoot 元组。
    """

    # 根列表顺序严格继承稳定的实现索引顺序。
    list_roots: list[DefinitionRoot] = []  # 默认 definition root 集合

    # 每个 module 名下可能保留多个重复 source 实现供独立报告。
    for _, tuple_implementations in index.implementations:

        # 每个 source 定义都拥有自己的实现身份和默认参数指纹。
        for module_implementation in tuple_implementations:

            # 默认环境按声明依赖顺序求值并生成规范指纹。
            parameter_environment_default = build_default_parameter_environment(module_implementation)  # 当前实现默认参数环境

            # root 把定义身份与对应默认特化键显式关联。
            definition_root = DefinitionRoot(  # 当前 source 实现的默认分析入口
                module_implementation.identity,  # 当前 source 定义身份
                SpecializationKey(  # 默认参数环境对应的 specialization 身份
                    module_implementation.identity,  # 当前默认特化实现身份
                    parameter_environment_default.fingerprint,  # 默认参数语义指纹
                ),
            )  # 将定义范围与其默认参数指纹绑定的根记录

            # 入口按实现索引次序追加，供顶层逐一定义审计。
            list_roots.append(definition_root)

    # 元组输出防止调用方改变默认根顺序。
    return tuple(list_roots)

# 实现索引查找保留同名 source 定义的完整有序集合。
def lookup_module_implementations(
    index: ModuleImplementationIndex,
    module_name: str,
) -> tuple[ModuleImplementation, ...]:
    """查找 module 名对应的全部 source 实现。

    参数:
        index: source-only 模块实现索引。
        module_name: 引用方需要解析的 module 名称。

    返回:
        零个、一个或多个稳定排序的 source 实现。
    """

    # 字典视图只用于按名称检索，元组值本身保持不可变。
    return dict(index.implementations).get(module_name, ())

# 模块引用原因区分重复、缺失与 external-only 三种边界。
def module_reference_unknown_reason(
    index: ModuleImplementationIndex,
    module_name: str,
) -> str:
    """返回模块引用无法唯一绑定时的局部原因。

    参数:
        index: source 与 external interface 分离的实现索引。
        module_name: 当前实例引用的 module 名称。

    返回:
        唯一 source 实现时为空字符串，否则为精确原因。
    """

    # 查找结果完整保留同名实现数量供唯一性判断。
    tuple_implementations = lookup_module_implementations(index, module_name)  # 当前名称的 source 实现集合

    # 多个 source 定义无法在静态分析中任意选择一个。
    if len(tuple_implementations) > 1:

        # 固定原因供实例局部 unknown 和诊断复用。
        return "duplicate module implementation"

    # 恰有一个 source 实现时引用可继续跨模块展开。
    if tuple_implementations:

        # 空原因表示实现绑定唯一且可材料化。
        return ""

    # external interface 仅有端口合同，没有可遍历实现体。
    if module_name in dict(index.external_interfaces):

        # 原因明确区分 external-only 与真正缺失。
        return "external-only module implementation"

    # source 和 external 索引均无此名称时报告缺失实现。
    return "missing module implementation"

# 递归路径按完整 specialization key 检测再入而非只看模块名。
def mark_recursive_specialization(
    key: SpecializationKey,
    visiting: tuple[SpecializationKey, ...],
) -> UnknownRegion | None:
    """在特化键再入时返回局部递归截断证据。

    参数:
        key: 即将进入的模块特化键。
        visiting: 当前递归路径上的特化键元组。

    返回:
        再入时返回递归 UnknownRegion，首次进入时返回 None。
    """

    # 同一实现与参数指纹再次进入表示当前调用路径形成递归环。
    if key in visiting:

        # 局部截断保留潜在组合循环信息而不阻断其他根。
        return UnknownRegion((), "recursive module specialization", True)

    # 首次进入当前特化时无需产生未知区域。
    return None

# 端口位宽解释复用 specialization 参数环境，不引入第二套表达式求值器。
def _port_width(
    port: dict[str, Any],
    environment: ParameterEnvironment,
) -> int | None:
    """求一个模块端口在当前参数环境下的确定位宽。

    参数:
        port: formatter 端口事实字典。
        environment: 端口所属 SpecializedModule 的参数环境。

    返回:
        正整数位宽；参数化范围无法确定时为 None。
    """

    # 空 width 表示 Verilog 标量端口。
    obj_width = port.get("width")  # formatter 端口位宽字段

    # 结构化正整数可以直接作为位宽。
    if isinstance(obj_width, int) and not isinstance(obj_width, bool):

        # 非正值不符合可映射端口合同。
        return obj_width if obj_width > 0 else None

    # 声明文本只解释单个 packed range。
    str_width = str(obj_width or "").strip()  # 当前端口 packed range 文本

    # 没有范围的端口按一位标量处理。
    if not str_width:

        # 标量连接与 child_width 一保持一致。
        return 1

    # 方括号内左右边界允许引用当前 specialization 参数。
    match_range = re.fullmatch(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", str_width)  # packed range 边界匹配

    # 非标准范围文本保持局部未知，不从原文猜测位宽。
    if match_range is None:

        # None 由 output mapping 转成 child width unknown。
        return None

    # 参数环境只暴露已经确定且无原因的整数值。
    dict_values = _environment_values(environment)  # 当前端口范围可见参数值

    # 左边界使用批准的常量表达式 evaluator。
    tuple_left = _evaluate(match_range.group(1), dict_values)  # packed range 左边界结果

    # 右边界在相同 specialization 环境中求值。
    tuple_right = _evaluate(match_range.group(2), dict_values)  # packed range 右边界结果

    # 任一边界未知都会使逐位 output mapping 不完整。
    if tuple_left[3] or tuple_right[3] or tuple_left[0] is None or tuple_right[0] is None:

        # 不伪造默认总线宽度。
        return None

    # packed range 包含左右两个端点。
    return abs(tuple_left[0] - tuple_right[0]) + 1

# 端口查找统一从冻结声明目录恢复一次性字典。
def _module_ports(module: SpecializedModule) -> list[dict[str, Any]]:
    """恢复 SpecializedModule 的有序端口事实。

    参数:
        module: 当前已物化模块图。

    返回:
        按声明顺序排列的独立端口字典列表。
    """

    # 每个 FrozenFact 单独解冻，保持缓存值不可变。
    return [_mapping(frozen_port) for frozen_port in module.ports]

# 输出端口的 resolved-net 属性优先形成不可反向唯一绑定的边界。
def _port_is_unresolved_boundary(port: dict[str, Any]) -> bool:
    """判断端口是否属于 resolved-net 或 inout 本地边界。

    参数:
        port: 当前 output 或 inout 端口事实。

    返回:
        不允许建立唯一 child producer 时为 True。
    """

    # inout 具有双向或多驱动语义，禁止反向唯一 output binding。
    if str(port.get("direction") or "").lower() == "inout":

        # 父级只记录 unresolved local boundary。
        return True

    # 普通 tri 与 wire 可以拥有多个已知 producer，不自动 unresolved。
    set_unresolved_net_types = {"wand", "wor", "triand", "trior", "tri1", "tri0"}  # 有解析语义的 net 类型

    # pull primitive 和 open-drain 通过单独 resolution_kind 标记。
    set_resolution_kinds = {  # 具有有线解析或隐式上拉下拉语义的网络类别
        "wand",  # 有线与解析网络
        "wor",  # 有线或解析网络
        "triand",  # 三态有线与解析网络
        "trior",  # 三态有线或解析网络
        "tri1",  # 默认上拉三态网络
        "tri0",  # 默认下拉三态网络
        "pullup",  # 显式上拉解析类别
        "pulldown",  # 显式下拉解析类别
        "open_drain",  # 开漏多驱动解析类别
    }  # 禁止唯一反向绑定的解析类别

    # 两个 typed 字段任一命中即可形成本地解析边界。
    return (
        str(port.get("net_type") or "").lower() in set_unresolved_net_types
        or str(port.get("resolution_kind") or "").lower() in set_resolution_kinds
    )

# branch_path 的 complete=False 证明组合过程存在保持旧值的路径。
def _comb_fact_is_latch(fact: dict[str, Any]) -> bool:
    """判断一个组合驱动事实是否形成锁存器切点。

    参数:
        fact: SpecializedModule 中的组合表达式事实。

    返回:
        任一分支明确不完整时为 True。
    """

    # 只有组合过程的不完整路径产生 latch，continuous 不参与。
    if str(fact.get("process_kind") or "") != "comb":

        # 非组合过程由 storage template 或其他分类处理。
        return False

    # branch_path 保留 formatter 对控制覆盖完整性的结构化判断。
    list_branch_path = fact.get("branch_path", []) or []  # 当前驱动控制分支路径

    # 任一明确 complete=False 的决策形成存储保持路径。
    return any(
        isinstance(dict_step, dict) and not bool(dict_step.get("complete", True))
        for dict_step in list_branch_path
    )

# exact Q bridge 只接受零操作 identifier 直连存储目标。
def _identifier_expression_target(expression: object) -> str:
    """读取纯 identifier 表达式的目标名称。

    参数:
        expression: formatter typed expression 根节点。

    返回:
        纯标识符名称；其他表达式返回空字符串。
    """

    # 缺少结构化节点时不能声明 exact bridge。
    if not isinstance(expression, dict):

        # 空名称让 driver classifier 继续普通组合路径。
        return ""

    # 两代 typed tree 字段统一判断 identifier 类别。
    str_kind = str(expression.get("node_kind") or expression.get("kind") or "").lower()  # 当前表达式节点类别

    # 运算、选择或函数调用均不是零成本 Q bridge。
    if str_kind != "identifier":

        # 空名称阻止复杂表达式越过存储切点。
        return ""

    # name/text/value 兼容 formatter 与测试夹具的 identifier 字段命名。
    return str(
        expression.get("name") or expression.get("text") or expression.get("value") or ""
    ).strip()

# 输出驱动事实收集保持材料化顺序，并把 seq 镜像与组合事实分开。
def _output_driver_facts(
    module: SpecializedModule,
    port_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """收集一个输出端口的全部、组合和存储驱动事实。

    参数:
        module: 当前 child specialization。
        port_name: 需要分类的 output formal 名称。

    返回:
        全部表达式事实、真实组合事实和直接存储事实。
    """

    # 全部表达式事实保留 parse_error 和未知过程，供 fail-closed 判断使用。
    list_target_facts = [  # 未知驱动检测需要同时查看解析错误和过程类别
        _mapping(frozen_fact)  # 解冻当前模块的单条表达式事实
        for frozen_fact in module.comb_expressions  # 按材料化顺序遍历模块表达式目录
        if base_target(str(_mapping(frozen_fact).get("target") or "")) == port_name  # 只收集左值匹配当前端口的事实
    ]

    # seq 赋值由 storage template 负责，不再作为组合 producer。
    list_comb_facts = [  # 当前 output 的真实组合事实
        dict_fact  # 保留 continuous 或 comb 驱动
        for dict_fact in list_target_facts  # 扫描当前 formal 的表达式事实
        if str(dict_fact.get("process_kind") or "") in {"continuous", "comb"}  # 排除 seq 镜像
    ]

    # 直接同名 storage template 把 formal 定义为 Q 端。
    list_storage_facts = [  # 当前 output 的直接存储事实
        _mapping(frozen_fact)  # 解冻存储模板
        for frozen_fact in module.storage_drivers  # 遍历特化模块存储目录
        if str(_mapping(frozen_fact).get("target") or "") == port_name  # 严格匹配 formal
    ]

    # 三类事实共享同一次稳定筛选结果，避免分类入口重复遍历。
    return list_target_facts, list_comb_facts, list_storage_facts

# 直接驱动分类先处理 mixed、显式 storage 和 latch 三类确定性切点。
def _direct_output_driver_class(
    list_comb_facts: list[dict[str, Any]],
    list_storage_facts: list[dict[str, Any]],
) -> OutputDriverClass | None:
    """返回由直接组合和存储事实确定的驱动类别。

    参数:
        list_comb_facts: 当前 output 的 continuous 或 comb 事实。
        list_storage_facts: 当前 output 的直接存储模板。

    返回:
        mixed 或 storage_q；尚不能确定时返回 None。
    """

    # 组合与存储事实并存时必须局部 fail-closed，不能任意选边。
    if list_comb_facts and list_storage_facts:

        # mixed 保留冲突事实，阻止不可信的跨层展开。
        return "mixed"

    # 任一直接 storage template 都把 formal 定义为 Q 端。
    if list_storage_facts:

        # 父级在 Q 处截止，D 与 enable 仍由 child 自身目标分析。
        return "storage_q"

    # 不完整组合覆盖形成 latch，因此同样属于存储切点。
    if any(_comb_fact_is_latch(dict_fact) for dict_fact in list_comb_facts):

        # latch 输出不向父级传播内部 D 锥。
        return "storage_q"

    # 剩余事实需要继续检查 exact bridge、unknown 或普通组合驱动。
    return None

# exact Q bridge 只允许单一零操作 identifier 指向内部存储目标。
def _is_exact_output_q_bridge(
    module: SpecializedModule,
    list_comb_facts: list[dict[str, Any]],
) -> bool:
    """判断组合事实是否构成内部存储目标的精确直连桥。

    参数:
        module: 当前 child specialization。
        list_comb_facts: 当前 output 的真实组合事实。

    返回:
        单一 identifier 直连内部 storage target 时返回 True。
    """

    # 多驱动或无驱动都不能证明零操作 identifier 直连。
    if len(list_comb_facts) != 1:

        # 非单一组合事实不属于 exact Q bridge。
        return False

    # identifier 名称用于匹配内部 storage template 的目标。
    str_bridge_target = _identifier_expression_target(  # 潜在内部 Q 名称
        list_comb_facts[0].get("expression")  # 唯一组合事实的 typed expression
    )

    # 空名称或任一同名 storage target 共同决定桥接结论。
    return bool(str_bridge_target) and any(
        str(_mapping(frozen_fact).get("target") or "") == str_bridge_target
        for frozen_fact in module.storage_drivers
    )

# parse_error 和未知过程类别会阻止当前 output 的组合展开。
def _has_unknown_output_driver_fact(list_target_facts: list[dict[str, Any]]) -> bool:
    """判断输出表达式事实中是否存在未知或解析失败的驱动。

    参数:
        list_target_facts: 当前 output 的全部表达式事实。

    返回:
        存在 parse_error 或未知 process_kind 时返回 True。
    """

    # 任一解析错误都使当前端口的驱动锥不再可信。
    bool_has_parse_error = any(  # 当前 output 解析错误状态
        str(dict_fact.get("parse_error") or "")  # 读取单条事实解析错误
        for dict_fact in list_target_facts  # 检查每条驱动的过程类别
    )  # 当前 output 是否包含解析错误

    # 除 continuous、comb、seq 外的过程类别没有受支持的分类语义。
    bool_has_unknown_process = any(  # 当前 output 未知过程状态
        str(dict_fact.get("process_kind") or "") not in {"continuous", "comb", "seq"}  # 识别不支持的过程类别
        for dict_fact in list_target_facts  # 扫描当前 output 全部事实
    )  # 当前 output 是否包含未知过程

    # 任一未知证据只污染当前 output，并强制局部 fail-closed。
    return bool_has_parse_error or bool_has_unknown_process

# _primitive_output_driver_class 将 profile output boundary 映射到组合图类别。
def _primitive_output_driver_class(
    module: SpecializedModule,
    port_name: str,
) -> OutputDriverClass | None:
    """返回原语输出边界类别；普通 RTL module 返回 ``None``。

    参数:
        module: 当前 child specialization。
        port_name: 需要跨层绑定的 output formal 名称。
    返回:
        原语边界对应的驱动类别；没有 primitive profile 时返回 ``None``。
    """

    # 普通 RTL module 继续沿用 expression/storage 事实分类。
    if module.primitive_profile is None:

        # None 表示调用方应执行既有 RTL 分类路径。
        return None

    # profile 已在 facts 阶段冻结，避免再次读取 Vivado 库或猜测内部逻辑。
    dict_profile = _mapping(module.primitive_profile)  # 当前原语 profile 快照

    # output boundary 是原语跨层传播的唯一受治理事实。
    str_boundary = primitive_output_boundary(dict_profile, port_name)  # 当前 output 边界

    # 透明和时钟源输出继续沿层级向父端传播。
    if str_boundary in {"transparent", "clock_source"}:

        # P1/P2 透明边界可继续参与组合 source closure。
        return "combinational"

    # state_cut 在 Q 或存储输出处截断上游 ownership。
    if str_boundary == "state_cut":

        # 原语 D/enable/reset 输入由独立 endpoint 继续审查。
        return "storage_q"

    # 多驱动和双向 pad 不建立唯一反向 ownership。
    if str_boundary in {"multi_driver", "inout"}:

        # 只保留 resolved boundary 的局部语义。
        return "unresolved_net_boundary"

    # P3 opaque 或未知 boundary 只污染当前 output target。
    return "unknown"

# 公开驱动分类入口决定 child output 是否展开、截止或局部未知。
def classify_output_driver(
    module: SpecializedModule,
    port_name: str,
) -> OutputDriverClass:
    """分类一个 SpecializedModule 输出端口的驱动语义。

    参数:
        module: 当前 child specialization。
        port_name: 需要跨层绑定的 output formal 名称。

    返回:
        combinational、Q cut、resolved boundary、mixed 或 unknown 类别。
    """

    # 端口 typed fact 提供方向与 resolved-net 分类。
    dict_port = next(  # 与请求 formal 名完全匹配的 child 端口事实
        (
            dict_item  # 返回名称匹配的端口事实
            for dict_item in _module_ports(module)  # 遍历当前 child 的有序端口目录
            if str(dict_item.get("name") or "") == port_name  # 严格匹配请求 formal 名
        ),
        {},
    )  # 当前名称对应的端口事实

    # 缺失 output formal 无法建立可信驱动分类。
    if not dict_port:

        # unknown 只污染当前关联输出。
        return "unknown"

    # 原语没有 RTL expression/storage facts，直接消费 profile boundary。
    optional_primitive_class = _primitive_output_driver_class(module, port_name)  # 原语 output 分类

    # None 只表示当前 module 不是原语 synthetic boundary。
    if optional_primitive_class is not None:

        # 透明、Q cut、resolved boundary 和 opaque 均已在 helper 中 fail-closed 分类。
        return optional_primitive_class

    # inout 和 resolved-net 类型必须在 child 边界截止。
    if _port_is_unresolved_boundary(dict_port):

        # 不生成反向唯一 child binding。
        return "unresolved_net_boundary"

    # 三类事实由同一 helper 按材料化顺序收集，避免分类分支重复扫描。
    tuple_driver_facts = _output_driver_facts(  # 一次读取端口事实以维持原始材料化顺序
        module,  # 提供当前子模块的表达式和存储目录
        port_name,  # 限定本次事实收集的输出端口名称
    )

    # 全部事实用于后续 parse_error 与未知过程的 fail-closed 判断。
    list_target_facts: list[dict[str, Any]] = tuple_driver_facts[0]  # output 表达式事实

    # 组合事实用于 latch、Q bridge 和普通 producer 分类。
    list_comb_facts: list[dict[str, Any]] = tuple_driver_facts[1]  # 可参与跨层展开的组合驱动事实

    # 存储事实用于直接 Q 端和 mixed 驱动判断。
    list_storage_facts: list[dict[str, Any]] = tuple_driver_facts[2]  # 同名 storage 事实

    # mixed、直接 storage 和 latch 可由当前 formal 的直接事实立即确定。
    optional_direct_class = _direct_output_driver_class(  # 为空时继续检查直连桥和未知驱动
        list_comb_facts,  # 检查组合驱动冲突和锁存器切点
        list_storage_facts,  # 检查同名存储模板形成的时序切点
    )

    # 已确定的直接类别优先于 bridge 和 unknown 判定。
    if optional_direct_class is not None:

        # 返回 mixed 或 storage_q，保持原有分类优先级。
        return optional_direct_class

    # 单一零操作 identifier 指向内部 Q 时在该切点截止。
    if _is_exact_output_q_bridge(module, list_comb_facts):

        # bridge 自身不引入逻辑，父级无需继续展开内部 D 锥。
        return "exact_q_bridge"

    # parse_error 或未知过程阻止当前 output 的组合展开。
    if _has_unknown_output_driver_fact(list_target_facts):

        # unknown 驱动与其他端口隔离。
        return "unknown"

    # 至少一个完整 continuous/comb 事实形成可展开组合 producer。
    if list_comb_facts:

        # 普通 wire 和 tri 可在 parent endpoint 合并多个 producer。
        return "combinational"

    # 没有任何已知驱动事实时保持 unknown。
    return "unknown"

# 一基 span 统一转换成模型要求的四整数元组。
def _span_tuple(value: object) -> tuple[int, int, int, int]:
    """恢复 formatter span 的稳定四坐标元组。

    参数:
        value: span 字典或兼容缺失值。

    返回:
        一基起止行列四元组。
    """

    # 完整字典优先读取四个公共坐标字段。
    if isinstance(value, dict):

        # 缺失坐标回落到有效一基最小值。
        return (
            int(value.get("line_start") or 1),
            int(value.get("column_start") or 1),
            int(value.get("line_end") or value.get("line_start") or 1),
            int(value.get("column_end") or 1),
        )

    # 兼容路径没有权威位置时使用固定占位范围。
    return (1, 1, 1, 1)

# 实例路径段把 generate 迭代附加到尚未带索引的实例名。
def _instance_path_segment(instance: dict[str, Any]) -> str:
    """建立一个实例 occurrence 的可读路径段。

    参数:
        instance: 已物化的实例事实。

    返回:
        包含数组或 generate 迭代索引的实例路径段。
    """

    # 实例名是路径段的基础身份。
    str_name = str(instance.get("instance_name") or "")  # 当前实例声明名称

    # loop_iteration_tuple 已按外层到内层再到数组排序。
    tuple_iterations = tuple(int(obj_item) for obj_item in instance.get("loop_iteration_tuple", ()) or ())  # 当前实例迭代身份

    # array 展开已把末维索引写入 instance_name，避免重复附加。
    str_suffix = "".join(f"[{int_item}]" for int_item in tuple_iterations)  # 完整迭代路径后缀

    # 已含相同后缀的数组实例直接保留 formatter 名称。
    if str_suffix and str_name.endswith(str_suffix):

        # 当前路径段已经编码全部迭代身份。
        return str_name

    # generate occurrence 把完整迭代元组附加到实例名。
    return f"{str_name}{str_suffix}"

# 实现索引按完整 identity 查找 definition root 或 child 候选。
def _implementation_by_identity(
    index: ModuleImplementationIndex,
    identity: ModuleImplementationIdentity,
) -> ModuleImplementation | None:
    """按完整模块实现身份查找 source 定义。

    参数:
        index: source-only 实现索引。
        identity: 路径、模块名和定义范围组成的身份。

    返回:
        精确匹配的实现；索引中不存在时为 None。
    """

    # 同名实现集合保留重复定义，必须继续比较完整 identity。
    for module_implementation in lookup_module_implementations(index, identity.module_name):

        # dataclass 相等比较覆盖路径和完整源码范围。
        if module_implementation.identity == identity:

            # 返回唯一精确身份匹配项。
            return module_implementation

    # 未找到精确 source 定义时不退回同名任意实现。
    return None

# association 风格归一化成 child formal 名到 actual 事实的有序映射。
def _bind_port_associations(
    ports: list[dict[str, Any]],
    instance: dict[str, Any],
) -> tuple[tuple[tuple[str, dict[str, Any]], ...], str]:
    """绑定实例 named 或 positional 端口 association。

    参数:
        ports: child 模块按声明顺序排列的端口事实。
        instance: 当前已物化实例事实。

    返回:
        formal 到 actual 的有序绑定和实例局部原因。
    """

    # formatter 已声明解析不完整时，空关联不得被解释成合法空连接。
    if not bool(instance.get("parse_complete", True)):

        # formatter 原因直接进入层级绑定诊断，缺失时使用稳定兜底文本。
        str_parse_reason = str(  # 当前实例关联不完整原因
            instance.get("unsupported_reason")  # 优先保留 formatter 的精确失败原因
            or "instance associations are incomplete"  # 缺失原因时使用稳定兜底文本
        )  # 当前实例关联不完整诊断文本

        # 不完整实例只能返回带原因的空绑定。
        return (), str_parse_reason

    # mixed 风格不能安全推断未命名位置与命名 formal 的组合语义。
    str_style = str(instance.get("association_style") or "")  # 当前实例端口关联风格

    # 批准合同仅放行纯 named、纯 positional 或空集合。
    if str_style == "mixed":

        # 原因只污染当前 instance outputs。
        return (), "mixed port association style"

    # 关联列表保持实例源码出现顺序。
    list_associations = instance.get("port_associations", []) or []  # 当前实例端口关联事实

    # formal 名到端口声明位置用于 named lookup。
    dict_positions = {  # child formal 名称到声明序号的唯一查找目录
        str(dict_port.get("name") or ""): int_position  # 保存当前 formal 的 positional 序号
        for int_position, dict_port in enumerate(ports)  # 按 child 端口声明顺序编号
    }  # child formal 名称到声明位置

    # 临时映射按 formal 名保存 actual 并检测重复。
    dict_bound: dict[str, dict[str, Any]] = {}  # 当前实例已绑定端口 actual

    # 每项 association 独立选择 named 或 positional formal。
    for int_fallback_position, obj_association in enumerate(list_associations):

        # 非结构化 association 无法提供 actual 或 formal。
        if not isinstance(obj_association, dict):

            # 当前实例连接目录不完整。
            return (), "port association is not structured"

        # named formal 为空时使用 association position。
        str_formal = str(obj_association.get("formal_name") or obj_association.get("formal") or "")  # 当前 named association 指定的 child formal 名称

        # position 字段缺失时沿用关联出现位置。
        int_position = int(obj_association.get("position", int_fallback_position))  # 当前 positional association 对应的 child 声明序号

        # named 与 positional 分别解析到 child 声明名称。
        if str_formal:

            # 未知 named formal 不能绑定到任意端口。
            if str_formal not in dict_positions:

                # 精确原因携带无法识别的 formal 名称。
                return (), f"unknown child port formal: {str_formal}"

            # named 路径直接采用声明名称。
            str_name = str_formal  # 当前 association 绑定 formal

        # positional association 按 child 声明顺序解释。
        else:

            # 越界位置只污染当前实例。
            if int_position < 0 or int_position >= len(ports):

                # 原因携带实际越界位置。
                return (), f"positional port association out of range: {int_position}"

            # child 端口声明次序是 positional binding 权威源。
            str_name = str(ports[int_position].get("name") or "")  # 当前位置对应 formal 名

        # 重复 formal 不能以后写覆盖先写。
        if str_name in dict_bound:

            # 固定原因指出重复 child formal。
            return (), f"duplicate child port association: {str_name}"

        # actual 字段在 JSON 边界已经是普通字典。
        obj_actual = obj_association.get("actual", {})  # 当前 association actual 事实

        # 非字典 actual 不能进入 typed binding。
        if not isinstance(obj_actual, dict):

            # 当前 formal 的连接事实不完整。
            return (), f"child port actual is not structured: {str_name}"

        # 绑定副本与 instance 模板断开顶层引用。
        dict_bound[str_name] = dict(obj_actual)  # 当前 formal 的 actual 独立副本

    # 输出元组严格按 child 端口声明顺序排列。
    tuple_bound = tuple(  # 按 child 声明顺序冻结 formal 到 actual 的绑定
        (str_name, dict_bound[str_name])  # 保存一个已验证 formal 的 actual 独立副本
        for str_name in (str(dict_port.get("name") or "") for dict_port in ports)  # 恢复端口声明次序
        if str_name in dict_bound  # 跳过当前实例未连接的 child formal
    )  # 当前实例声明顺序 formal binding

    # 完整绑定目录不携带局部原因。
    return tuple_bound, ""

# parent endpoint 收集器允许普通 wire 保存多个 producer。
def _append_endpoint_driver(
    drivers: dict[ScopedTarget, list[ProducerRef]],
    endpoint: ScopedTarget,
    producer: ProducerRef,
) -> None:
    """向一个作用域端点追加独立 producer 证据。

    参数:
        drivers: hierarchy builder 的可变 endpoint 目录。
        endpoint: parent 或本地作用域内的静态目标。
        producer: 当前已知或未知生产者引用。

    返回:
        无；producer 原位追加到 builder 列表。
    """

    # setdefault 只创建当前 endpoint 私有的 producer 列表。
    drivers.setdefault(endpoint, []).append(producer)

# local expression facts 先建立每个模块自身的 endpoint producer。
def _append_local_drivers(
    root: DefinitionRoot,
    path: tuple[str, ...],
    module: SpecializedModule,
    drivers: dict[ScopedTarget, list[ProducerRef]],
) -> None:
    """把模块内组合与存储驱动写入 endpoint 目录。

    参数:
        root: 当前 hierarchy graph 定义根。
        path: 当前模块 occurrence 完整路径。
        module: 当前路径的 SpecializedModule。
        drivers: hierarchy builder 的可变 endpoint 目录。

    返回:
        无；本地 producer 原位追加到目录。
    """

    # 组合事实按材料化顺序保留同 endpoint 的多个驱动。
    for frozen_fact in module.comb_expressions:

        # 防御性副本承载 target、过程类别和位置读取。
        dict_fact = _mapping(frozen_fact)  # 当前本地组合驱动事实

        # 空目标无法形成可查询 endpoint。
        str_target = str(dict_fact.get("target") or "").replace(" ", "")  # 当前驱动静态目标

        # 缺少目标时跳过当前兼容事实。
        if not str_target:

            # 继续收集同模块其他有效驱动。
            continue

        # 当前模块作用域与 target 共同定义 endpoint。
        scoped_endpoint = ScopedTarget(root.identity, path, module.key, str_target)  # 当前本地驱动 endpoint

        # continuous 需要与 instance_output 精确合并供测试查询。
        str_kind = "continuous" if str(dict_fact.get("process_kind") or "") == "continuous" else "combinational"  # 本地 producer 类别

        # parse_error 作为同 endpoint 未知 producer 保留。
        str_reason = str(dict_fact.get("parse_error") or "")  # 当前本地驱动解析原因

        # line/source_column 转换成单点近似范围。
        int_line = int(dict_fact.get("line") or 1)  # 当前驱动一基源码行

        # formatter source_column 定位表达式起始列。
        int_column = int(dict_fact.get("source_column") or 1)  # 当前驱动一基源码列

        # producer 自身 scoped_target 与 endpoint 相同，tracing 后续再读取表达式。
        producer_ref = ProducerRef(  # 当前组合事实对应的本地 producer 证据
            str_kind,  # 区分连续赋值与组合过程驱动
            scoped_endpoint,  # 指向当前模块内被驱动的静态端点
            (int_line, int_column, int_line, int_column),  # 保存表达式起点的单点范围
            str_reason,  # 携带仅属于当前事实的解析缺口
        )  # 当前本地组合 producer

        # 普通 endpoint 保留所有已知与未知 producer。
        _append_endpoint_driver(drivers, scoped_endpoint, producer_ref)

    # storage target 也必须保留为 child 自身可分析的 Q endpoint。
    for frozen_fact in module.storage_drivers:

        # 防御性副本隔离冻结 cache。
        dict_fact = _mapping(frozen_fact)  # 当前本地存储驱动事实

        # storage target 形成显式 Q 切点。
        str_target = str(dict_fact.get("target") or "").replace(" ", "")  # 当前存储输出目标

        # 空目标没有可查询切点身份。
        if not str_target:

            # 继续收集其他存储目标。
            continue

        # 当前路径下的 Q endpoint 独立于父级 output actual。
        scoped_endpoint = ScopedTarget(root.identity, path, module.key, str_target)  # 当前存储 Q endpoint

        # storage producer 在此处截止，不追踪其 expression 到父级。
        producer_ref = ProducerRef("storage_q", scoped_endpoint, (1, 1, 1, 1))  # 当前 Q 切点 producer

        # 保存 child 自身 target 供后续单独检查 D/enable 锥。
        _append_endpoint_driver(drivers, scoped_endpoint, producer_ref)

        # storage D 使用独立 synthetic endpoint，避免 parent Q tracing 继承前级操作。
        scoped_data_endpoint = ScopedTarget(root.identity, path, module.key, f"{str_target}$D")  # 隔离当前存储目标 D 锥的 synthetic endpoint

        # D endpoint 指回同一 storage fact，由 tracing 单独读取 expression。
        data_producer_ref = ProducerRef("storage_d", scoped_data_endpoint, (1, 1, 1, 1))  # synthetic D endpoint 的本地 storage 数据入口

        # D 锥独立进入全目标枚举，但不反向连接到 Q endpoint。
        _append_endpoint_driver(drivers, scoped_data_endpoint, data_producer_ref)

        # enable endpoint 让时序控制条件继续接受自身组合预算检查。
        scoped_enable_endpoint = ScopedTarget(root.identity, path, module.key, f"{str_target}$enable")  # 隔离当前存储目标控制锥的 synthetic endpoint

        # enable producer 由 tracing 读取 storage fact 的 controls 集合。
        enable_producer_ref = ProducerRef("storage_enable", scoped_enable_endpoint, (1, 1, 1, 1))  # synthetic enable endpoint 的控制读取入口

        # 没有显式 enable 时空控制锥仍保持零操作可判定端点。
        _append_endpoint_driver(drivers, scoped_enable_endpoint, enable_producer_ref)

# actual 的静态 parent 目标用于未知实例和映射失败的局部证据。
def _actual_targets(actual: dict[str, Any]) -> tuple[str, ...]:
    """提取 output actual 可安全定位的 parent 基础目标。

    参数:
        actual: formatter InstanceActualFact 字典。

    返回:
        去重且保持首次出现顺序的静态基础目标元组。
    """

    # references 已由 typed tree 排除函数名和常量。
    list_references = actual.get("references", []) or []  # 当前 actual 标识符引用

    # 字典保持首次出现顺序并完成去重。
    return tuple(dict.fromkeys(str(obj_item) for obj_item in list_references if str(obj_item)))

# 未知实例或 output mapping 失败只污染其 actual 可达 endpoint。
def _append_unknown_actual_drivers(
    root: DefinitionRoot,
    parent_path: tuple[str, ...],
    parent: SpecializedModule,
    instance: dict[str, Any],
    reason: str,
    drivers: dict[ScopedTarget, list[ProducerRef]],
) -> int:
    """为实例 actual 引用追加局部 unknown producer。

    参数:
        root: 当前 hierarchy graph 定义根。
        parent_path: 当前实例所属父模块路径。
        parent: 当前父模块 specialization。
        instance: 无法完整绑定的实例事实。
        reason: 当前实例或 output mapping 精确原因。
        drivers: hierarchy builder 的可变 endpoint 目录。

    返回:
        成功定位并追加的 parent endpoint 数量。
    """

    # parent input formal 是实例的已知数据来源，不能反向当作未知 child producer。
    set_parent_inputs: set[str] = set()  # 当前 parent 的纯 input formal 基础名称

    # 计数用于判断是否需要升级为父 occurrence 级未知边界。
    int_endpoint_count = 0  # 已定位的受影响 parent endpoint 数量

    # 声明方向逐项证明哪些 actual 只能作为未知实例的输入来源。
    for dict_port in _module_ports(parent):

        # 仅纯 input formal 需要阻止反向污染。
        if str(dict_port.get("direction") or "").lower() == "input":

            # 当前输入名称加入已知 source 端点集合。
            set_parent_inputs.add(str(dict_port.get("name") or ""))

    # 其余关联可能在实现缺失时承载未知 output actual。
    for obj_association in instance.get("port_associations", []) or []:

        # 非字典关联无法提供 actual 引用。
        if not isinstance(obj_association, dict):

            # 继续寻找同实例其他结构化关联。
            continue

        # actual typed facts 提供可局部化的 parent 标识符引用。
        obj_actual = obj_association.get("actual", {})  # 当前未知关联 actual

        # 非字典 actual 没有安全 endpoint。
        if not isinstance(obj_actual, dict):

            # 跳过无法定位的当前关联。
            continue

        # 每个引用基础目标独立保存 unknown producer。
        for str_target in _actual_targets(obj_actual):

            # 输入端口只把值送入未知实例，不是该实例对 parent 的驱动。
            if base_target(str_target) in set_parent_inputs:

                # 跳过方向已由 parent 声明证明为 source 的 actual。
                continue

            # unknown endpoint 位于调用方作用域。
            scoped_endpoint = ScopedTarget(root.identity, parent_path, parent.key, str_target)  # 受未知实例影响的 parent endpoint

            # producer 指回未知实例边并携带声明位置。
            producer_ref = ProducerRef(  # 未知实例只作用于当前 actual 引用的端点
                "unknown_instance",  # 标记生产者来自不可解析的 child 实现
                scoped_endpoint,  # 定位父模块内受影响的静态目标
                _span_tuple(instance.get("span")),  # 使用实例声明范围定位证据
                reason,  # 保留缺失、重复或 external-only 的精确原因
            )  # 当前实例连接的未知 producer

            # 已知同端点 producer 继续保留，不被 unknown 覆盖。
            _append_endpoint_driver(drivers, scoped_endpoint, producer_ref)

            # 当前 actual 已成功恢复一个受影响端点。
            int_endpoint_count += 1  # 累计已经定位的父模块静态端点

    # 调用方据此区分局部恢复与完全无法定位连接。
    return int_endpoint_count

# hierarchy builder 状态把共享输出容器从递归参数中收拢。
@dataclass
class HierarchyBuildState:
    """保存一次层级图构建的共享输入与可变收集器。

    参数:
        root: 当前 definition root。
        index: source-only 模块实现索引。
        cache: 调用方 specialization cache。
        modules: 模块 occurrence 收集器。
        bindings: 单向端口绑定收集器。
        drivers: endpoint producer 收集器。
    """

    # 定义根确定全部 ScopedTarget 的源码身份。
    root: DefinitionRoot  # 当前层级图定义根

    # 实现索引用于唯一解析每个 child module。
    index: ModuleImplementationIndex  # 当前 source-only 实现索引

    # 特化缓存确保相同 key 只物化一次。
    cache: dict[SpecializationKey, SpecializedModule]  # 当前调用方共享特化缓存

    # occurrence 列表保留实例路径和对应特化模块。
    modules: list[tuple[tuple[str, ...], SpecializedModule]]  # 待冻结的模块 occurrence

    # binding 列表保留成功建立的单向 output 连接。
    bindings: list[HierarchyBinding]  # 待冻结的层级端口绑定

    # producer 目录允许一个 parent endpoint 同时拥有多个驱动。
    drivers: dict[ScopedTarget, list[ProducerRef]]  # 待冻结的端点生产者目录

# 完全无法恢复连接时，把未知边界限制在当前父 occurrence。
def _append_parent_unknown_boundary(
    state: HierarchyBuildState,
    parent_path: tuple[str, ...],
    parent: SpecializedModule,
    instance: dict[str, Any],
    reason: str,
) -> None:
    """让当前父 occurrence 的全部组合目标保持 fail-closed。

    参数:
        state: 当前层级图构建状态。
        parent_path: 解析失败实例所属父模块路径。
        parent: 当前父模块特化。
        instance: 无法恢复连接的实例事实。
        reason: formatter 提供的精确不完整原因。

    返回:
        无；unknown producer 原位追加到父 occurrence 目标。
    """

    # 已登记本地 driver 包含组合、存储投影和其他实例输出目标。
    set_targets: set[str] = {  # 当前父 occurrence 已知静态目标集合
        scoped_endpoint.target  # 保留当前静态端点文本
        for scoped_endpoint in state.drivers  # 遍历构建期 endpoint 目录
        if scoped_endpoint.instance_path == parent_path  # 限制在当前父 occurrence
        and scoped_endpoint.specialization == parent.key  # 限制在当前参数特化
    }

    # comb facts 补充尚未进入 driver 摘要的静态目标。
    for frozen_fact in parent.comb_expressions:

        # 解冻当前表达式以读取 formatter 保存的目标字段。
        dict_fact = _mapping(frozen_fact)  # 当前组合表达式的可读字段映射

        # 目标文本用于补齐尚未登记 producer 的父端点。
        str_target = str(dict_fact.get("target") or "")  # 当前组合表达式的静态目标文本

        # 空目标无法形成作用域端点。
        if str_target:

            # 规范化目标空白后并入父 occurrence 边界集合。
            set_targets.add(static_target(str_target))

    # 无静态目标时仍生成一个稳定占位，禁止两条 gate 静默不适用。
    if not set_targets:

        # 占位端点保证无连接线索的父 occurrence 仍产生保守锥。
        set_targets.add("<hierarchy>")

    # 每个父 occurrence 目标独立承载同一实例解析边界。
    for str_target in sorted(set_targets):

        # 作用域端点把 unknown 限制在当前定义根、路径和特化。
        scoped_endpoint = ScopedTarget(  # 当前父模块实例的受影响端点
            state.root.identity,  # 保持当前层级图定义根身份
            parent_path,  # 限制在解析失败实例所属父路径
            parent.key,  # 限制在当前父模块参数特化
            str_target,  # 选择当前需要保守标记的静态目标
        )  # 完成父路径、特化和静态目标的作用域键构造

        # 未知 producer 保留实例源码范围和 formatter 原因。
        producer_ref = ProducerRef(  # 当前实例解析边界的生产者证据
            "unknown_instance",  # 标记不可展开的实例生产者类别
            scoped_endpoint,  # 把未知边界绑定到当前父端点
            _span_tuple(instance.get("span")),  # 保留解析失败实例的源码范围
            reason,  # 保留 formatter 或绑定阶段的精确原因
        )  # 当前父端点的未知实例生产者

        # 同端点已有的确定 producer 必须继续保留。
        _append_endpoint_driver(state.drivers, scoped_endpoint, producer_ref)

# child occurrence 上下文避免单端口处理器重复传递相关字段。
@dataclass(frozen=True)
class ChildOccurrence:
    """冻结一个已解析 child occurrence 的端口绑定上下文。

    参数:
        parent_path: child 所属父模块实例路径。
        parent: 父模块特化对象。
        child_path: child 自身完整实例路径。
        child: child 特化对象。
        instance: 已物化实例事实。
        ports: child 有序端口事实。
        actuals: child formal 到 parent actual 的绑定。
        input_actuals: 仅包含 input formal 的冻结绑定。
    """

    # parent_path 用于构造 parent actual 的 ScopedTarget。
    parent_path: tuple[str, ...]  # 父模块完整实例路径

    # 父模块特化决定 parent endpoint 的缓存身份与参数环境。
    parent: SpecializedModule  # 提供 parent ScopedTarget 所需 specialization key 的父模块

    # child_path 保留 generate 和数组迭代身份。
    child_path: tuple[str, ...]  # 当前 child 完整实例路径

    # child 提供端口宽度、驱动分类和内部 target。
    child: SpecializedModule  # 当前已物化 child 特化

    # instance 提供声明位置与端口关联事实。
    instance: dict[str, Any]  # 当前 child 的已物化实例事实

    # ports 按 child 声明顺序保存 positional 权威次序。
    ports: tuple[dict[str, Any], ...]  # 当前 child 有序端口事实

    # actuals 已统一 named 与 positional 连接风格。
    actuals: dict[str, dict[str, Any]]  # named 和 positional 统一后的 formal-to-actual 查找目录

    # input_actuals 随每个 output binding 一起冻结。
    input_actuals: tuple[tuple[str, FrozenFact], ...]  # 当前 child 输入 formal 绑定

# 单个 unresolved child output 只向 parent 写入边界 producer。
def _append_unresolved_output(
    state: HierarchyBuildState,
    occurrence: ChildOccurrence,
    port_name: str,
    output_map: StaticOutputMap,
) -> None:
    """追加 inout 或 resolved-net output 的本地边界生产者。

    参数:
        state: 当前层级图构建状态。
        occurrence: 已解析 child occurrence 上下文。
        port_name: 当前 child output formal 名称。
        output_map: 已验证的 StaticOutputMap 兼容对象。

    返回:
        无；边界生产者原位追加到 parent endpoint 目录。
    """

    # bit_pairs 已由 selector 校验宽度和静态端点完整性。
    for _, str_parent_target in output_map.bit_pairs:

        # parent endpoint 位于实例调用方作用域。
        scoped_parent = ScopedTarget(  # resolved actual 在调用方作用域内的静态端点
            state.root.identity,  # 继承当前层级图 definition root 身份
            occurrence.parent_path,  # 定位实例所属 parent occurrence
            occurrence.parent.key,  # 绑定 parent 参数特化身份
            str_parent_target,  # 选择当前映射到的 parent 位端点
        )  # 当前 resolved actual 对应的 parent 静态端点

        # producer 指向 child formal 但禁止反向唯一 tracing。
        scoped_child = ScopedTarget(  # resolved producer 指向的 child formal 边界
            state.root.identity,  # 保持与 parent endpoint 相同定义根
            occurrence.child_path,  # 定位当前 child occurrence
            occurrence.child.key,  # 采用解析网络 formal 所属的 child 特化键
            port_name,  # 选择当前解析网络 output formal
        )  # 当前解析网络边界对应的 child formal

        # unresolved producer 保留实例声明位置。
        producer_ref = ProducerRef(  # 当前 parent 位的 resolved-net 边界证据
            "unresolved_net_boundary",  # 禁止唯一反向 tracing 的生产者类别
            scoped_child,  # 指向提供边界语义的 child formal
            _span_tuple(occurrence.instance.get("span")),  # 定位当前实例声明范围
        )  # 标记无法唯一反向追踪的解析网络生产者

        # parent endpoint 允许多个 boundary producer 并存。
        _append_endpoint_driver(state.drivers, scoped_parent, producer_ref)

# 静态实例数组把标量 formal 到整向量 actual 的连接投影为当前元素位。
def _map_occurrence_output_actual(
    actual: dict[str, Any],
    child_width: int,
    instance: dict[str, Any],
) -> StaticOutputMap:
    """建立包含实例数组元素语义的 output actual 映射。

    参数:
        actual: 当前 child output formal 对应的 typed parent actual。
        child_width: 当前 child output formal 的特化位宽。
        instance: 已物化且可能携带 instance_index 的实例事实。

    返回:
        当前 occurrence 的静态逐位映射或局部未知原因。
    """

    # 普通实例和显式 selector 继续由共享 selector 算法完整处理。
    output_map_current = map_static_output_actual(actual, child_width)  # 基础 child-to-parent 静态映射

    # 只有标量 formal 连接整标识符的实例数组需要逐元素投影。
    obj_index = instance.get("instance_index")  # 当前数组 occurrence 的声明索引

    # typed expression 用于确认 actual 是整标识符而非显式 selector。
    dict_expression = actual.get("expression")  # 用于辨别整标识符与显式选择器的 parent actual tree

    # 整标识符名称为空时不应用数组元素隐式投影。
    str_identifier = _identifier_expression_target(dict_expression)  # 整标识符 actual 的静态名称

    # 三项条件共同限定 Verilog 实例数组的标量逐元素连接。
    if (
        isinstance(obj_index, int)
        and child_width == 1
        and str_identifier
    ):

        # Verilog 实例数组的标量端口按 occurrence 索引连接对应向量位。
        str_parent_bit = f"{str_identifier}[{obj_index}]"  # 当前数组元素对应的 parent bit endpoint

        # 单位 child formal 固定映射到当前 parent bit。
        return StaticOutputMap((("0", str_parent_bit),))

    # 其他形状沿用 selector 的宽度、concat 和动态选择诊断。
    return output_map_current

# mixed 或 unknown output 只污染自身映射到的 parent 位。
def _append_unknown_output(
    state: HierarchyBuildState,
    occurrence: ChildOccurrence,
    port_name: str,
    output_map: StaticOutputMap,
    output_driver_class_current: OutputDriverClass,
) -> None:
    """追加无法确定 child 驱动语义的局部 unknown producer。

    参数:
        state: 当前层级图构建状态。
        occurrence: 已解析 child occurrence 上下文。
        port_name: 当前 child output formal 名称。
        output_map: 已验证的 StaticOutputMap 兼容对象。
        output_driver_class_current: mixed 或 unknown 驱动分类。

    返回:
        无；unknown producer 原位追加到相关 parent 位。
    """

    # 每个 parent 位保留相同 driver 分类原因。
    for _, str_parent_target in output_map.bit_pairs:

        # parent endpoint 与其他已知 producer 共享目录项。
        scoped_parent = ScopedTarget(  # 不确定 child output 影响的调用方端点
            state.root.identity,  # 使用 unknown producer 所属层级图的根身份
            occurrence.parent_path,  # 定位被不确定 output 污染的调用方路径
            occurrence.parent.key,  # 采用受影响 parent 模块的特化键
            str_parent_target,  # 选择当前受污染的 parent 位端点
        )  # 当前不确定 child output 影响的 parent 静态端点

        # unknown producer 指向 child formal 作用域。
        scoped_child = ScopedTarget(  # unknown producer 指回的 child formal 端点
            state.root.identity,  # 复用对应 unknown parent endpoint 的根身份
            occurrence.child_path,  # 定位产生不确定驱动的 child 路径
            occurrence.child.key,  # 采用无法分类 output 所属 child 特化键
            port_name,  # 选择无法分类的 output formal
        )  # 当前无法分类驱动的 child output formal

        # 原因精确包含驱动分类。
        producer_ref = ProducerRef(  # 当前 parent 位的 child 输出未知证据
            "unknown_instance_output",  # 标记实例输出驱动语义不确定
            scoped_child,  # 指向发生 mixed 或 unknown 的 child formal
            _span_tuple(occurrence.instance.get("span")),  # 定位发生 mixed 或 unknown 驱动的实例声明
            f"{output_driver_class_current} output driver",  # 保存当前 formal 的精确分类原因
        )  # 保留 mixed 或 unknown 分类的局部生产者证据

        # 不覆盖同 endpoint 的 continuous 或其他实例 producer。
        _append_endpoint_driver(state.drivers, scoped_parent, producer_ref)

# 确定单向 output 同时写入 binding 和 parent producer 目录。
def _append_known_output(
    state: HierarchyBuildState,
    occurrence: ChildOccurrence,
    port_name: str,
    output_map: StaticOutputMap,
    output_driver_class_current: OutputDriverClass,
) -> None:
    """追加 combinational 或 Q cut output 的确定层级绑定。

    参数:
        state: 当前层级图构建状态。
        occurrence: 已解析 child occurrence 上下文。
        port_name: 当前 child output formal 名称。
        output_map: 已验证的 StaticOutputMap 兼容对象。
        output_driver_class_current: combinational、storage_q 或 exact_q_bridge。

    返回:
        无；binding 与逐位 producer 原位追加到构建状态。
    """

    # 冻结 child output 到 parent actual 的完整连接合同。
    hierarchy_binding = HierarchyBinding(  # 当前 child formal 的确定单向跨层连接
        parent=occurrence.parent.key,  # 绑定调用方模块的参数特化身份
        parent_path=occurrence.parent_path,  # 定位调用方模块 occurrence
        child=occurrence.child.key,  # 记录确定 output producer 所属 child 特化键
        child_path=occurrence.child_path,  # 定位被调用模块 occurrence
        child_output=port_name,  # 记录提供生产者的 output formal
        output_bit_map=output_map.bit_pairs,  # 保存 child 位到 parent 端点的映射
        input_actuals=occurrence.input_actuals,  # 携带 child 输入连接供锥内代换
        source_span=_span_tuple(occurrence.instance.get("span")),  # 定位实例声明范围
    )  # 当前确定 child output 的单向层级绑定

    # binding 顺序稍后按路径和 formal 稳定排序。
    state.bindings.append(hierarchy_binding)

    # 每个 parent 位保存来自 child formal 的 producer。
    for _, str_parent_target in output_map.bit_pairs:

        # parent endpoint 使用实际连接的静态 bit 名称。
        scoped_parent = ScopedTarget(  # 已知 child producer 驱动的 parent 静态端点
            state.root.identity,  # 使用确定 binding 所属层级图的根身份
            occurrence.parent_path,  # 定位接收已知 child 驱动的 parent 路径
            occurrence.parent.key,  # 采用确定 parent endpoint 的特化键
            str_parent_target,  # 选择当前 output 位映射到的 parent 端点
        )  # 当前 child output 映射到的 parent 静态端点

        # producer target 位于 child specialization 作用域。
        scoped_child = ScopedTarget(  # parent producer 反向定位的 child output formal
            state.root.identity,  # 让 child producer 与其确定 binding 共享源码定义域
            occurrence.child_path,  # 定位提供确定驱动的 child 路径
            occurrence.child.key,  # 采用已确认 output producer 的 child 特化键
            port_name,  # 选择提供驱动的 output formal
        )  # 当前 parent producer 指向的 child output formal

        # combinational 精确采用 instance_output，Q 类保留切点类别。
        str_kind = "instance_output" if output_driver_class_current == "combinational" else output_driver_class_current  # 当前跨层生产者类别

        # producer span 定位到实例声明。
        producer_ref = ProducerRef(  # 当前 parent 位的确定跨层生产者证据
            str_kind,  # 区分组合展开与两类 Q 端切点
            scoped_child,  # 指向提供驱动的 child formal
            _span_tuple(occurrence.instance.get("span")),  # 定位建立确定 output binding 的实例声明
        )  # 当前 parent 位对应的确定 child 生产者证据

        # 普通 wire/tri endpoint 可合并多个 child producers。
        _append_endpoint_driver(state.drivers, scoped_parent, producer_ref)

# 单端口处理器隔离 width、selector 和 driver class 三类决策。
def _append_child_output_port(
    state: HierarchyBuildState,
    occurrence: ChildOccurrence,
    port: dict[str, Any],
) -> None:
    """处理一个 child output 或 inout formal 的 parent 绑定。

    参数:
        state: 当前层级图构建状态。
        occurrence: 已解析 child occurrence 上下文。
        port: 当前 child 端口事实。

    返回:
        无；成功或局部未知结果写入构建状态。
    """

    # formal 名查找对应 actual 事实。
    str_port_name = str(port.get("name") or "")  # 当前 child 输出 formal 名称

    # 缺失连接没有 parent endpoint 可供定位。
    if str_port_name not in occurrence.actuals:

        # 未连接 formal 不生成伪 producer。
        return

    # 当前 child 参数环境确定 formal packed width。
    int_child_width = _port_width(port, occurrence.child.parameter_environment)  # 当前 child 输出的特化位宽

    # 未知位宽只污染当前 actual 引用。
    if int_child_width is None:

        # 合成单关联实例事实复用统一局部 unknown 写入入口。
        _append_unknown_actual_drivers(
            state.root,
            occurrence.parent_path,
            occurrence.parent,
            {"port_associations": [{"actual": occurrence.actuals[str_port_name]}]},
            "child output width is unknown",
            state.drivers,
        )

        # 未知位宽不能建立逐位绑定。
        return

    # selector 模块只消费 typed actual tree 并输出有序位对。
    static_output_map_output_actual_mapping: StaticOutputMap = _map_occurrence_output_actual(  # 当前 occurrence 的静态逐位 child-to-parent 连接
        occurrence.actuals[str_port_name],  # 读取当前 output 对应的 parent actual 事实
        int_child_width,  # 使用特化后 child formal 位宽校验映射
        occurrence.instance,  # 应用静态 instance array 的当前元素索引
    )  # 当前 child formal 到 parent actual 的静态逐位映射

    # mapping 原因只污染当前 actual 可达端点。
    if static_output_map_output_actual_mapping.unknown_reason:

        # 保留精确 dynamic、concat 或 width mismatch 原因。
        _append_unknown_actual_drivers(
            state.root,
            occurrence.parent_path,
            occurrence.parent,
            {"port_associations": [{"actual": occurrence.actuals[str_port_name]}]},
            static_output_map_output_actual_mapping.unknown_reason,
            state.drivers,
        )

        # 不完整位对不能写入 hierarchy binding。
        return

    # child 驱动类别决定展开、Q 截止或局部未知。
    output_driver_class_current = classify_output_driver(occurrence.child, str_port_name)  # 当前 child 输出驱动分类

    # inout 和 resolved-net 都只在 parent 建立本地边界 producer。
    if str(port.get("direction") or "").lower() == "inout" or output_driver_class_current == "unresolved_net_boundary":

        # resolved 类型不建立唯一反向 binding。
        _append_unresolved_output(state, occurrence, str_port_name, static_output_map_output_actual_mapping)

        # 当前 formal 已按解析网络语义完成局部处理。
        return

    # mixed 或 unknown driver 只产生局部 unknown producer。
    if output_driver_class_current in {"mixed", "unknown"}:

        # 不确定分类不进入确定 binding 列表。
        _append_unknown_output(
            state,
            occurrence,
            str_port_name,
            static_output_map_output_actual_mapping,
            output_driver_class_current,
        )

        # 当前 formal 的 unknown 证据已经写入 parent 位目录。
        return

    # 其余批准类别均建立确定单向 output binding。
    _append_known_output(
        state,
        occurrence,
        str_port_name,
        static_output_map_output_actual_mapping,
        output_driver_class_current,
    )

# occurrence 输出入口只筛选 child 到 parent 的有效方向。
def _append_child_outputs(
    state: HierarchyBuildState,
    occurrence: ChildOccurrence,
) -> None:
    """处理一个 child occurrence 的全部 output 与 inout formal。

    参数:
        state: 当前层级图构建状态。
        occurrence: 已解析 child occurrence 上下文。

    返回:
        无；各端口独立写入确定或未知生产者。
    """

    # 端口声明顺序保证 binding 与诊断输出稳定。
    for dict_port in occurrence.ports:

        # input formal 只存在于 input_actuals，不产生 parent driver。
        if str(dict_port.get("direction") or "").lower() not in {"output", "inout"}:

            # 继续处理其余 child 端口。
            continue

        # 单端口处理器负责宽度、选择器和驱动类别分流。
        _append_child_output_port(state, occurrence, dict_port)

# _primitive_specialized_module 建立无内部 RTL 的原语 synthetic module。
def _primitive_specialized_module(
    primitive_profile: dict[str, Any],
) -> tuple[SpecializedModule, dict[str, Any]]:
    """从原语 profile 建立特化模块和有序接口。

    参数:
        primitive_profile: 已通过 catalog 校验的原语 profile。
    返回:
        ``(specialized_module, interface)`` 二元组。
    """

    # 原语身份不依赖当前 RTL 路径，避免实例间共享 source definition。
    str_module_name = str(primitive_profile.get("module_name") or primitive_profile.get("name") or "")  # 原语模块名

    # 原语 identity 使用固定伪来源，不与真实 RTL 路径混合。
    module_identity = ModuleImplementationIdentity(  # 原语身份
        "<amd-xilinx-primitive>",  # 固定伪来源隔离 catalog 原语与 RTL source
        str_module_name,  # 使用 catalog exact-name 作为 identity 的 module 名
        SourceSpan(0, 0, 0, 0),  # 原语没有真实 RTL 源码范围
    )

    # 固定 specialization key 让同名原语的 profile boundary 可复用。
    specialization_key = SpecializationKey(module_identity, "primitive")  # 原语特化键

    # 原语 profile 不承载参数覆盖环境。
    parameter_environment = ParameterEnvironment((), "primitive")  # 原语不携带实例参数覆盖环境

    # profile 端口顺序是 positional association 的唯一受治理来源。
    dict_interface = primitive_module_interface(primitive_profile)  # 原语接口投影

    # 端口序列冻结后才能进入 hierarchy cache。
    tuple_ports = tuple(_frozen_mapping(dict_port) for dict_port in dict_interface.get("ports", []))  # 冻结端口序列

    # synthetic module 只暴露接口和 output boundary，不伪造内部实现。
    specialized_module = SpecializedModule(  # 原语 synthetic module，作为无内部 RTL 的 child
        key=specialization_key,  # 复用原语 identity 形成稳定 specialization key
        parameter_environment=parameter_environment,  # 原语不接受实例参数覆盖
        ports=tuple_ports,  # 按 profile 顺序提供 positional/named 端口接口
        comb_expressions=(),  # 原语内部组合表达式由 boundary 语义取代
        instances=(),  # 原语不展开任何内部实例
        functions=(),  # 原语不伪造函数定义
        storage_drivers=(),  # 原语存储切点由 output boundary 表示
        loop_presence="absent",  # 原语没有 generate/loop 事实
        unknown_regions=(),  # catalog 已验证的 profile 不增加未知区域
        primitive_profile=_frozen_mapping(primitive_profile),  # 保存冻结 profile 供 output 分类
    )

    # 同时返回接口，避免主流程再次读取 profile 产生漂移。
    return specialized_module, dict_interface

# _primitive_input_actuals 保留原语 input formal 与 actual 的绑定事实。
def _primitive_input_actuals(
    dict_interface: dict[str, Any],
    dict_actuals: dict[str, Any],
) -> tuple[tuple[str, FrozenFact], ...]:
    """从原语接口和已解析 actuals 提取 input 绑定。

    参数:
        dict_interface: 原语 profile 的有序接口投影。
        dict_actuals: `_bind_port_associations` 返回的 formal 到 actual 映射。
    返回:
        可安全进入 ChildOccurrence 的冻结 input actual 元组。
    """

    # 只有存在 actual 的 input formal 才参与父级 source closure。
    return tuple(
        (
            str(dict_port.get("name") or ""),
            _frozen_mapping(dict_actuals[str(dict_port.get("name") or "")]),
        )
        for dict_port in dict_interface.get("ports", [])
        if str(dict_port.get("direction") or "").lower() == "input"
        and str(dict_port.get("name") or "") in dict_actuals
    )

# _build_primitive_instance_node 把 Xilinx 原语物化为 profile boundary。
def _build_primitive_instance_node(
    state: HierarchyBuildState,
    parent_path: tuple[str, ...],
    parent: SpecializedModule,
    instance: dict[str, Any],
    primitive_profile: dict[str, Any],
) -> None:
    """建立原语 child occurrence，并复用统一 output binding 逻辑。

    参数:
        state: 当前层级图构建状态。
        parent_path: 当前实例所属父模块路径。
        parent: 当前父模块特化。
        instance: 已物化的原语实例事实。
        primitive_profile: 已校验的原语 profile。
    返回:
        无；成功 child 或局部 unknown 结果写入构建状态。
    """

    # helper 返回已经冻结的原语 child 和有序接口。
    tuple_primitive_result: tuple[SpecializedModule, dict[str, Any]] = _primitive_specialized_module(primitive_profile)  # 原语 child 与接口二元结果

    # 第一个返回元素是无内部 RTL 的 synthetic child specialization。
    tuple_specialized_module = tuple_primitive_result[0]  # 原语 child specialization 供 occurrence 和 hierarchy 使用

    # 第二个返回元素保存 catalog 端口顺序与方向事实。
    tuple_dict_interface = tuple_primitive_result[1]  # 原语接口供 positional binding 和 input closure 使用

    # 接口 profile 再次冻结，避免普通字典进入 occurrence。
    tuple_ports = tuple(_frozen_mapping(dict_port) for dict_port in tuple_dict_interface.get("ports", []))  # 原语端口序列

    # profile port 顺序是 positional association 的唯一受治理来源。
    tuple_binding = _bind_port_associations([dict(_mapping(frozen_port)) for frozen_port in tuple_ports], instance)  # 按 profile 端口顺序绑定 formal 与实例 actual

    # 结构不完整时只污染当前 primitive instance actual。
    if tuple_binding[1]:

        # 保留 parser 的局部原因，不把不完整实例扩散到其他层级。
        _append_unknown_actual_drivers(state.root, parent_path, parent, instance, tuple_binding[1], state.drivers)

        # 未知绑定不再继续建立 child occurrence。
        return

    # 成功绑定后建立供 input closure 和 output endpoint 复用的 formal-to-actual 目录。
    dict_actuals = dict(tuple_binding[0])  # 原语每个 formal 对应的父级 actual 表达式

    # 只从 input formal 建立父级 source closure 绑定。
    tuple_input_actuals = _primitive_input_actuals(tuple_dict_interface, dict_actuals)  # 原语 input formal 的父级 actual 绑定

    # 统一 occurrence 模型承载 primitive output boundary。
    occurrence = ChildOccurrence(  # 保存父级作用域、接口方向和连接表达式，供原语输出驱动写回父端点
        parent_path,  # 当前原语实例所在的父模块路径
        parent,  # 当前父模块 specialization
        parent_path + (_instance_path_segment(instance),),  # 原语 child 的稳定实例路径
        tuple_specialized_module,  # 保存 catalog 生成的无内部 RTL 原语 child，用于 output boundary 分类
        instance,  # 当前原语实例事实
        tuple(tuple_dict_interface.get("ports", [])),  # 原语接口端口
        dict_actuals,  # 提供每个 formal 对应的父级 actual 表达式，供 output endpoint 回写
        tuple_input_actuals,  # 保存 input formal 的 source closure 绑定，供来源审查
    )

    # 统一 child output 处理器消费 primitive profile 的 output boundary。
    _append_child_outputs(state, occurrence)

    # 原语没有内部 child，但 hierarchy node 仍保留稳定 specialization identity。
    _build_hierarchy_node(state, occurrence.child_path, tuple_specialized_module, (tuple_specialized_module.key,))

# _primitive_profile_for_instance 查询当前实例的 exact-name primitive profile。
def _primitive_profile_for_instance(
    state: HierarchyBuildState,
    instance: dict[str, Any],
) -> dict[str, Any]:
    """返回当前实例的原语 profile，不匹配时返回空字典。

    参数:
        state: 当前层级图构建状态。
        instance: 已物化的实例事实。
    返回:
        exact-name 命中的独立 profile 字典，或空字典。
    """

    # 只按 exact module name 查询，禁止原语前缀或后缀模糊放行。
    str_module_name = str(instance.get("module_name") or "")  # 当前实例引用名

    # 将索引复制成当前查询快照，避免后续调用改变 profile 解析结果。
    dict_profiles = dict(state.index.primitive_profiles)  # 当前 catalog profile 索引

    # _mapping 同时隔离 FrozenFact 与普通字典引用。
    return _mapping(dict_profiles.get(str_module_name))

# 原语分支尝试器负责命中 profile 后的 synthetic child 消费。
def _try_build_primitive_instance_node(
    state: HierarchyBuildState,
    parent_path: tuple[str, ...],
    parent: SpecializedModule,
    instance: dict[str, Any],
) -> bool:
    """尝试构建命中的原语 child，并报告是否已消费当前实例。

    参数:
        state: 当前层级图构建状态。
        parent_path: 当前实例所属父模块路径。
        parent: 当前父模块特化。
        instance: 已物化实例事实。
    返回:
        命中 catalog 原语并完成局部处理时返回 True，否则返回 False。
    """

    # exact-name 查询保证未知模块不会误套用相似原语 profile。
    dict_primitive_profile = _primitive_profile_for_instance(state, instance)  # 当前实例的 catalog exact-name profile

    # 未命中 profile 时交回普通 RTL implementation 解析路径。
    if not dict_primitive_profile:

        # False 表示调用方仍需处理普通 source/external module。
        return False

    # 参数化宽度未被当前静态事实解析时，禁止把默认 WIDTH=1 当成实例实宽。
    if instance.get("parameter_overrides") and dict_primitive_profile.get("width_parameter"):

        # 只污染当前原语实例的 actual，保留 fail-closed 原因供 VG097/VG146/VG147 使用。
        _append_unknown_actual_drivers(
            state.root,  # 当前层级图的 definition root
            parent_path,  # 原语实例所在的父模块路径
            parent,  # 父模块 specialization 与端点方向事实
            instance,  # 带参数覆盖的原语实例事实
            "parameterized primitive width requires explicit profile",  # 未解析 WIDTH 的局部原因
            state.drivers,  # 写入受影响 actual 的 unknown producer
        )

        # 已命中原语但无法安全建模，调用方不得退回普通 RTL 猜测。
        return True

    # 命中 profile 时只建立 synthetic boundary，不读取 Vivado 库源码。
    _build_primitive_instance_node(
        state,  # 更新当前层级的 driver 与 hierarchy 目录
        parent_path,  # 标记原语实例所属父级路径
        parent,  # 提供父级端点与 specialization 上下文
        instance,  # 传入已物化的原语实例事实
        dict_primitive_profile,  # 传入 exact-name catalog boundary profile
    )

    # 原语 child 已完成局部 output binding，调用方不再继续 source 展开。
    return True

# 不完整实例处理器负责恢复 actual 影响并截断当前层级边。
def _handle_incomplete_instance(
    state: HierarchyBuildState,
    parent_path: tuple[str, ...],
    parent: SpecializedModule,
    instance: dict[str, Any],
) -> bool:
    """处理 formatter 未完成解析的实例并报告是否已截断。

    参数:
        state: 当前层级图构建状态。
        parent_path: 当前实例所属父模块路径。
        parent: 当前父模块特化。
        instance: 已物化实例事实。
    返回:
        实例关联不完整并已写入局部 unknown 证据时返回 True，否则返回 False。
    """

    # 完整实例不应在此 helper 中提前退出普通解析路径。
    if bool(instance.get("parse_complete", True)):

        # False 表示调用方可以继续 profile 或 source implementation 解析。
        return False

    # formatter 的局部原因必须原样进入 unknown producer 证据。
    str_parse_reason = str(  # 当前实例结构化解析不完整原因
        instance.get("unsupported_reason")  # 优先读取 formatter 的局部失败原因
        or "instance associations are incomplete"  # 缺失原因时保持稳定诊断
    )  # 当前实例结构化解析失败文本

    # 尽可能先从残存 actual 恢复受影响的父端点。
    int_recovered_endpoints = _append_unknown_actual_drivers(  # 成功定位的父端点数量
        state.root,  # 使用当前层级图定义根
        parent_path,  # 在解析失败实例的调用方作用域定位 actual
        parent,  # 依据父端口方向排除输入来源
        instance,  # 读取仍可恢复的结构化 actual
        str_parse_reason,  # 把解析失败原因写入未知生产者
        state.drivers,  # 更新当前构建期端点目录
    )  # 当前实例能够恢复的受影响父端点数量

    # 完全无法定位 actual 时升级为当前父 occurrence 的保守边界。
    if int_recovered_endpoints == 0:

        # 父 occurrence 边界阻止空连接事实让预算门静默通过。
        _append_parent_unknown_boundary(state, parent_path, parent, instance, str_parse_reason)

    # 不完整关联已经局部化，调用方不得继续解析 child 参数或端口方向。
    return True

# 单实例解析器负责唯一实现、参数绑定与 child occurrence 构造。
def _build_instance_node(
    state: HierarchyBuildState,
    parent_path: tuple[str, ...],
    parent: SpecializedModule,
    instance: dict[str, Any],
    visiting: tuple[SpecializationKey, ...],
) -> None:
    """解析一个实例并递归建立其 child hierarchy 节点。

    参数:
        state: 当前层级图构建状态。
        parent_path: 当前实例所属父模块路径。
        parent: 当前父模块特化。
        instance: 已物化实例事实。
        visiting: 当前递归路径上的 specialization keys。

    返回:
        无；成功 child 或局部 unknown 结果写入构建状态。
    """

    # 实例段附加 generate 或数组迭代身份。
    tuple_child_path = parent_path + (_instance_path_segment(instance),)  # 含 generate 或数组索引的 child occurrence 路径

    # 不完整关联由专用 helper 局部化，完整实例继续进入 profile/source 路径。
    if _handle_incomplete_instance(state, parent_path, parent, instance):

        # helper 已写入当前父端点 unknown 证据，不能再猜测 child。
        return

    # 原语 helper 返回 True 时已经完成 boundary 绑定，普通路径不得重复消费。
    if _try_build_primitive_instance_node(state, parent_path, parent, instance):

        # 原语 child 已完成局部 output binding，不再进入 source implementation 路径。
        return

    # index 阶段已经局部分类缺失、重复和 external-only 引用。
    str_reference_reason = str(instance.get("reference_unknown_reason") or "")  # 当前 child 实现解析原因

    # 不唯一实现不能生成确定 child graph。
    if str_reference_reason:

        # 相关 actual 保留局部 unknown producer。
        _append_unknown_actual_drivers(
            state.root,  # 使用递归截断实例所属的 definition root
            parent_path,  # 把 unknown 归属到实例调用方路径
            parent,  # 使用递归边调用方特化定位 endpoint
            instance,  # 读取当前实例的 actual 引用与源码范围
            str_reference_reason,  # 保留索引阶段的精确解析原因
            state.drivers,  # 写入递归边影响的 parent producer 目录
        )

        # 当前实例缺少唯一实现，停止进入参数绑定阶段。
        return

    # 唯一 source 实现才能进入参数绑定和材料化。
    tuple_candidates = lookup_module_implementations(  # 当前 child 模块名对应的全部 source 实现
        state.index,  # 查询当前构建快照的实现索引
        str(instance.get("module_name") or ""),  # 使用实例声明的 child module 名
    )  # 当前 child module 的 source 实现候选

    # 防御性唯一性检查覆盖手工 SpecializedModule fixture。
    if len(tuple_candidates) != 1:

        # 缺失或重复原因在当前调用点重新精确分类。
        str_reason = module_reference_unknown_reason(  # 防御性检查发现的 child 实现不唯一原因
            state.index,  # 在当前实现索引中分类缺失或重复
            str(instance.get("module_name") or ""),  # 携带当前实例引用的模块名
        )  # 当前 child 无法唯一绑定的精确原因

        # 只污染当前实例 actual 引用的 endpoint。
        _append_unknown_actual_drivers(state.root, parent_path, parent, instance, str_reason, state.drivers)

        # 防御性索引检查失败后不猜测同名实现。
        return

    # 参数覆盖事实保持实例声明顺序。
    tuple_overrides = tuple(  # 按声明顺序冻结 child 参数覆盖事实
        dict(obj_item)  # 复制当前参数覆盖事实以隔离实例模板
        for obj_item in instance.get("parameter_overrides", []) or []  # 按声明顺序遍历 override
        if isinstance(obj_item, dict)  # 排除不具备参数覆盖合同的值
    )  # 当前实例的有序参数覆盖集合

    # override actual 在父 module 参数环境中求值。
    parameter_environment_child = bind_parameter_environment(  # 在父参数环境中求值得到 child 参数绑定
        parent,  # 提供 override 表达式所需父模块参数值
        tuple_candidates[0],  # 使用唯一 child source 实现的参数声明
        tuple_overrides,  # 应用当前实例的有序参数覆盖
    )  # 当前 child specialization 的参数环境

    # 子实现与参数指纹构造唯一 specialization key。
    specialization_key_child = SpecializationKey(  # 唯一标识当前 child source 与参数组合
        tuple_candidates[0].identity,  # 绑定 child 定义的完整 source 身份
        parameter_environment_child.fingerprint,  # 区分求值后的参数环境
    )  # 当前 child specialization 的缓存身份

    # 再入同一 key 形成局部递归边，不能无限展开。
    unknown_recursive = mark_recursive_specialization(specialization_key_child, visiting)  # 当前 child 递归截断证据

    # 递归边只污染当前实例 actual。
    if unknown_recursive is not None:

        # 固定原因保留递归 module specialization 文本。
        _append_unknown_actual_drivers(
            state.root,  # 继承当前图的 definition root
            parent_path,  # 把递归 unknown 归属到调用方路径
            parent,  # 使用父模块 specialization 定位 endpoint
            instance,  # 读取递归实例的 actual 引用
            unknown_recursive.reason,  # 保存递归特化截断原因
            state.drivers,  # 写入当前层级图 producer 目录
        )

        # 当前递归边已局部化，停止继续物化同一特化键。
        return

    # cache 只以完整 SpecializationKey 复用不可变模块图。
    specialized_module_child = materialize_specialization(  # 当前唯一 child occurrence 的特化模块图
        tuple_candidates[0],  # 读取已唯一解析的 child source 实现
        parameter_environment_child,  # 应用已求值的 child 参数环境
        state.cache,  # 复用调用方共享的 specialization cache
    )  # 当前 child 的已物化模块图

    # 端口声明顺序是 positional association 的权威来源。
    tuple_ports = tuple(_module_ports(specialized_module_child))  # positional 绑定使用的 child 声明顺序端口目录

    # named 与 positional 归一化后共享同一 formal mapping。
    tuple_binding = _bind_port_associations(list(tuple_ports), instance)  # 当前实例 formal bindings 和原因

    # 不完整 binding 只污染当前 instance actual。
    if tuple_binding[1]:

        # output 方向未知时保守标记全部 actual 引用。
        _append_unknown_actual_drivers(
            state.root,  # 使用端口绑定缺口所属的 definition root
            parent_path,  # 把 binding unknown 归属到调用方路径
            parent,  # 使用关联错误调用方特化定位 endpoint
            instance,  # 读取不完整端口关联的 actual 引用
            tuple_binding[1],  # 保存 mixed 或非法 association 的精确原因
            state.drivers,  # 写入 association 缺口影响的 producer 目录
        )

    # formal lookup 供 input 和 output 分别处理。
    dict_actuals = dict(tuple_binding[0])  # 当前 child formal 到 parent actual 目录

    # input_actuals 只保存 child input formal，顺序与端口声明一致。
    tuple_input_actuals = tuple(  # 按 child 端口声明顺序冻结全部 input actual
        (  # 当前 input formal 与 parent actual 的不可变绑定
            str(dict_port.get("name") or ""),  # 保存 child input formal 名称
            _frozen_mapping(dict_actuals[str(dict_port.get("name") or "")]),  # 冻结对应 parent actual
        )
        for dict_port in tuple_ports  # 按 child 端口声明顺序遍历
        if str(dict_port.get("direction") or "").lower() == "input"  # 只保留输入方向
        and str(dict_port.get("name") or "") in dict_actuals  # 跳过未连接输入 formal
    )  # 当前 child 的有序输入 formal 绑定

    # occurrence 对象集中后续端口处理所需上下文。
    occurrence = ChildOccurrence(  # 集中当前 child 的路径、特化和端口绑定上下文
        parent_path,  # 保存当前实例所属父模块路径
        parent,  # 保存父模块 specialization
        tuple_child_path,  # 保存含迭代身份的 child 完整路径
        specialized_module_child,  # 保存已物化 child specialization

        # 下半组保存实例事实与已经归一化的端口连接目录。
        instance,  # 保存当前实例事实和源码范围
        tuple_ports,  # 保存 child 声明顺序端口目录
        dict_actuals,  # 保存 formal-to-actual 查找目录
        tuple_input_actuals,  # 保存有序 input actual 冻结目录
    )  # 当前已解析 child occurrence 的冻结绑定上下文

    # output 与 inout formal 独立建立 parent producer。
    _append_child_outputs(state, occurrence)

    # 即使某个 output 无法绑定，child 自身其他 target 仍需保留。
    _build_hierarchy_node(
        state,
        tuple_child_path,
        specialized_module_child,
        visiting + (specialization_key_child,),
    )

# 子图 builder 只登记当前 occurrence 并把每个实例交给单实例解析器。
def _build_hierarchy_node(
    state: HierarchyBuildState,
    path: tuple[str, ...],
    module: SpecializedModule,
    visiting: tuple[SpecializationKey, ...],
) -> None:
    """递归建立一个 SpecializedModule occurrence 的 hierarchy 子图。

    参数:
        state: 当前层级图构建状态。
        path: 当前模块 occurrence 完整路径。
        module: 当前路径的 SpecializedModule。
        visiting: 当前递归路径上的 specialization keys。

    返回:
        无；当前 occurrence 与 child 子图原位写入构建状态。
    """

    # 当前 occurrence 无论能否继续递归都必须保留在 modules 中。
    state.modules.append((path, module))

    # 模块自身组合与存储 target 先进入 endpoint 目录。
    _append_local_drivers(state.root, path, module, state.drivers)

    # 每个已物化实例事实按源码和迭代顺序建立 child occurrence。
    for frozen_instance in module.instances:

        # 防御性副本用于端口绑定、路径构造和局部错误归属。
        dict_instance = _mapping(frozen_instance)  # 当前待解析的 child instance 事实

        # 单实例解析器隔离唯一实现、参数和 output binding 决策。
        _build_instance_node(state, path, module, dict_instance, visiting)

# scoped target 排序键显式展开 dataclass 字段，避免依赖对象比较实现。
def _scoped_target_sort_key(target: ScopedTarget) -> tuple[object, ...]:
    """返回 hierarchy endpoint 的稳定排序键。

    参数:
        target: 需要在冻结目录中排序的作用域端点。

    返回:
        definition、path、specialization 和 target 组成的比较元组。
    """

    # 完整字段序列确保跨进程输出次序一致。
    return (
        target.root.relative_path,
        target.root.module_name,
        target.root.definition_span.line_start,
        target.root.definition_span.column_start,

        # occurrence 与 specialization 字段在源码身份相同后继续区分端点。
        target.instance_path,
        target.specialization.fingerprint,
        target.target,
    )

# hierarchy graph 入口从默认 definition root 递归构建冻结绑定图。
def build_hierarchy_bindings(
    root: DefinitionRoot,
    index: ModuleImplementationIndex,
    cache: dict[SpecializationKey, SpecializedModule],
) -> HierarchyGraph:
    """建立一个 definition root 的不可变 hierarchy binding 图。

    参数:
        root: 需要分析的 source definition 默认特化入口。
        index: source-only 模块实现索引。
        cache: 调用方拥有的 specialization cache。

    返回:
        模块路径、输出绑定和 endpoint producer 均稳定冻结的图。

    异常:
        ValueError: root identity 不存在于 source implementation index。
    """

    # root 必须按完整定义身份查找，不能任取同名实现。
    module_implementation = _implementation_by_identity(index, root.identity)  # 与 root 完整源码身份一致的模块实现

    # 缺失精确实现表示 root 与 index 不属于同一发现快照。
    if module_implementation is None:

        # 产品错误前缀满足统一诊断合同。
        raise ValueError("> ERR: [Python] definition root implementation is missing")

    # 默认参数环境必须产生 root 记录中的同一 specialization key。
    parameter_environment_root = build_default_parameter_environment(module_implementation)  # root 默认参数环境

    # 根模块通过共享 cache 物化或复用。
    specialized_module_root = materialize_specialization(  # 当前层级图入口对应的参数特化根模块
        module_implementation,  # 使用已按完整身份匹配的 root 实现
        parameter_environment_root,  # 应用 root 默认参数环境
        cache,  # 从公开入口向根模块物化传入共享特化缓存
    )  # 当前 hierarchy graph 的已物化根模块

    # module path 从 root module 名开始，空路径不利于用户证据展示。
    tuple_root_path = (root.identity.module_name,)  # definition root 可读实例路径

    # builder 列表只在当前调用栈内可变。
    list_modules: list[tuple[tuple[str, ...], SpecializedModule]] = []  # 收集实例路径与已物化模块的 occurrence 对

    # output bindings 在完成递归后统一稳定排序。
    list_bindings: list[HierarchyBinding] = []  # hierarchy 单向端口绑定

    # endpoint lookup 不写回 cache，返回前冻结为 tuple。
    dict_drivers: dict[ScopedTarget, list[ProducerRef]] = {}  # 作用域端点 producer 目录

    # 状态对象集中递归共享输入和待冻结收集器。
    build_state = HierarchyBuildState(  # 聚合递归构建共享输入与三个可变收集器
        root,  # 保存 definition root 与图身份
        index,  # 保存 source-only 实现发现快照
        cache,  # 保存调用方 specialization cache

        # 三个可变收集器只在当前调用栈内共享。
        list_modules,  # 接收递归发现的模块 occurrence
        list_bindings,  # 接收确定单向 output bindings
        dict_drivers,  # 接收每个作用域端点的 producers
    )  # 当前 hierarchy graph 的调用栈内构建状态

    # root key 首先进入 visiting，检测子实例回到同一 specialization。
    _build_hierarchy_node(
        build_state,
        tuple_root_path,
        specialized_module_root,
        (specialized_module_root.key,),
    )

    # 模块 occurrence 按完整实例路径稳定排序。
    tuple_modules = tuple(sorted(list_modules, key=lambda tuple_item: tuple_item[0]))  # 冻结模块路径目录

    # binding 排序不依赖遍历实现细节。
    tuple_bindings = tuple(  # 按父路径、子路径和 formal 冻结层级绑定次序
        sorted(  # 复制排序避免修改递归收集器
            list_bindings,  # 读取全部确定 output bindings
            key=lambda hierarchy_binding: (  # 显式提取稳定绑定排序字段
                hierarchy_binding.parent_path,  # 先比较调用方 occurrence 路径
                hierarchy_binding.child_path,  # 再比较被调用方 occurrence 路径

                # 路径相同的多 output binding 最后按 formal 名排序。
                hierarchy_binding.child_output,  # 最后比较 child output formal
            ),  # 完成单个 binding 的稳定排序键
        )
    )  # 冻结 hierarchy binding 集合

    # producer 排序保持同 endpoint 多驱动证据确定输出。
    tuple_endpoint_drivers = tuple(  # 冻结已排序 endpoint 到 producer 元组目录
        (  # 当前 endpoint 及其稳定 producer 集合
            scoped_endpoint,  # 保存完整作用域静态端点身份

            # producer 子序列按语义与定位字段独立排序后冻结。
            tuple(  # 冻结当前 endpoint 的全部 producer
                sorted(  # 复制排序避免改写构建期 producer 列表
                    list_producers,  # 读取当前 endpoint 的多驱动证据
                    key=lambda producer_ref: (  # 显式提取 producer 稳定排序字段
                        producer_ref.kind,  # 先比较驱动语义类别
                        producer_ref.scoped_target.instance_path,  # 再比较 producer occurrence 路径

                        # 同路径 producer 再按目标、位置与局部原因消除遍历差异。
                        producer_ref.scoped_target.target,  # 再比较 producer 静态目标
                        producer_ref.source_span,  # 再比较源码声明范围
                        producer_ref.unknown_reason,  # 最后比较局部未知原因
                    ),  # 组合驱动类别、路径、目标、位置与原因的排序键
                )  # 完成当前 endpoint 的 producer 排序
            ),  # 冻结当前 endpoint 的 producer 元组
        )  # 完成 endpoint 目录条目

        # endpoint 自身先按完整作用域身份排序，再冻结 producer 列表。
        for scoped_endpoint, list_producers in sorted(  # 按 endpoint 身份遍历 producer 目录
            dict_drivers.items(),  # 读取构建期 endpoint producer 映射
            key=lambda tuple_item: _scoped_target_sort_key(tuple_item[0]),  # 使用完整 ScopedTarget 排序键
        )
    )  # 冻结 endpoint 到 producer 目录

    # 返回对象不暴露 builder 的可变容器。
    return HierarchyGraph(root, tuple_modules, tuple_bindings, tuple_endpoint_drivers)

# endpoint 查询建立调用栈内临时 lookup，不修改 hierarchy graph 或 cache。
def drivers_for_endpoint(
    graph: HierarchyGraph,
    endpoint: ScopedTarget,
) -> tuple[ProducerRef, ...]:
    """查询一个 hierarchy endpoint 的全部 producer。

    参数:
        graph: 已冻结的 hierarchy binding 图。
        endpoint: 需要定位的作用域静态目标。

    返回:
        已知和未知 producer 的稳定元组；未驱动时为空元组。
    """

    # dict 只在当前调用期建立，不写入共享 specialization cache。
    dict_drivers = dict(graph.endpoint_drivers)  # 当前冻结图的 endpoint producer 查找表

    # 精确查询结果决定是否需要 whole-target 保守回退。
    tuple_exact = dict_drivers.get(endpoint, ())  # 当前静态端点的精确 producer

    # 精确 bit/slice producer 优先，避免 whole-vector 保守边界覆盖已知映射。
    if tuple_exact:

        # 已定位的 producer 直接返回。
        return tuple_exact

    # 未知位宽只能登记在 whole target；任一具体 bit read 都必须继承该边界。
    str_base = base_target(endpoint.target)  # 当前静态选择器的基础目标

    # 只有 bit/slice endpoint 才存在不同的 whole target。
    if str_base != endpoint.target:

        # 完整作用域身份保持不变，仅把查询端点降为 whole target。
        scoped_base = ScopedTarget(endpoint.root, endpoint.instance_path, endpoint.specialization, str_base)  # 当前 bit/slice 回退查询使用的 whole endpoint

        # whole producer 是精确映射缺失时的保守上游边界。
        return dict_drivers.get(scoped_base, ())

    # whole endpoint 自身也没有 producer 时保持未驱动语义。
    return ()
