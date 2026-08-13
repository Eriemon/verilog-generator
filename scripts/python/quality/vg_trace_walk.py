"""递归遍历组合表达式、函数、本地事实和时序存储投影。"""

# 延迟注解求值避免递归函数签名在导入阶段提前解析。
from __future__ import annotations

# dataclass 定义栈内聚合器，Any 限定 formatter 异构节点边界。
from dataclasses import dataclass
from typing import Any

# walk 只读取冻结模型，不拥有 graph 构建或跨调用缓存。
from .vg_comb_model import LoopPresence, ProducerRef, ScopedTarget, SpecializedModule

# 选择器用于提取表达式引用并归一化静态端点。
from .vg_comb_selectors import base_target, reference_targets

# session 模块先提供递归上下文、冻结结果和调用级索引容器。
from .vg_trace_session import (
    TraceContext,
    TraceResult,
    TraceSession,
)

# session 查询函数把递归 walk 与索引内部布局隔离。
from .vg_trace_session import (
    _drivers_for_context,
    _local_facts_for_context,
    _mapping,
    _module_for_context,
    operation_occurrence_id,
)

# 可变累加器只存在于单次递归调用栈内。
@dataclass
class TraceAccumulator:
    """累加 tracing 过程中的操作、循环状态和原因。

    参数:
        operations: 当前已知 operation occurrence 集合。
        loop_presence: 当前已合并的循环三态。
        reasons: 当前局部未知原因集合。
    """

    # set 允许共享 producer 从多条路径到达时自然去重。
    operations: set[str]  # 当前已知 operation occurrence

    # absent 是没有任何可达循环证据时的合并单位元。
    loop_presence: LoopPresence = "absent"  # 当前合并后的循环三态

    # set 防止同一未知边界经重复引用产生重复诊断。
    reasons: set[str] | None = None  # 当前局部原因集合

    # 默认集合必须逐实例创建，禁止跨 trace 调用共享。
    def __post_init__(self) -> None:
        """为缺省 reasons 创建当前累加器私有集合。

        参数:
            self: 当前 tracing 累加器。

        返回:
            无；仅初始化未提供的 reasons 容器。
        """

        # 调用方未提供集合时创建当前 accumulator 私有容器。
        if self.reasons is None:

            # 新集合隔离并行或嵌套 trace 调用的未知原因。
            self.reasons = set()  # 当前累加器私有的未知原因集合

# 循环三态合并严格实现 present 优先和 unknown 保守传播表。
def merge_loop_presence(left: LoopPresence, right: LoopPresence) -> LoopPresence:
    """合并两个可达子锥的循环存在性。

    参数:
        left: 已累计的循环存在性。
        right: 新子锥的循环存在性。

    返回:
        present、absent 或 unknown 的固定表结果。
    """

    # 任一路径已证明循环存在时，其他未知边界不能改变所有权。
    if left == "present" or right == "present":

        # present 由 VG147 独占报告。
        return "present"

    # 两侧都明确无循环时才能得到 absent。
    if left == "absent" and right == "absent":

        # 明确无循环的组合锥归属于 VG146。
        return "absent"

    # 其余组合至少含一个 unknown 且没有 present 证据。
    return "unknown"

# typed tree 子节点兼容旧 operands 与公共 children 字段。
def _children(expression: dict[str, Any]) -> list[dict[str, Any]]:
    """读取表达式的有序结构化子节点。

    参数:
        expression: formatter typed expression 节点。

    返回:
        仅包含字典子节点的独立列表。
    """

    # legacy comb facts 使用 operands，actual 公共树使用 children。
    obj_children = expression.get("operands", expression.get("children", []))  # 当前节点原始子项

    # 非序列子项不能形成稳定递归顺序。
    if not isinstance(obj_children, (list, tuple)):

        # 空列表让当前节点按叶节点处理。
        return []

    # 每个字典子项复制顶层，防止 tracing 修改 formatter 事实。
    return [dict(obj_item) for obj_item in obj_children if isinstance(obj_item, dict)]

# operation 节点判定只接受冻结目录标记的真实运行时操作。
def _is_counted_operation(expression: dict[str, Any]) -> bool:
    """判断 typed expression 节点是否消耗组合操作预算。

    参数:
        expression: formatter typed expression 节点。

    返回:
        节点是非 marker 的真实运行时 operation 时为 True。
    """

    # operation_kind 是公共 typed tree 的预算类别权威字段。
    str_operation_kind = str(expression.get("operation_kind") or "")  # 当前节点预算类别

    # 旧树缺少 operation_kind 时以 kind 排除叶节点和调用 marker。
    str_kind = str(expression.get("node_kind") or expression.get("kind") or "")  # 当前节点语法类别

    # identifier、constant 与 function_call marker 都是零成本结构节点。
    if str_operation_kind in {"identifier", "constant", "marker"} or str_kind in {
        "identifier",
        "constant",
        "function_call",
    }:

        # 零成本节点仍可能有需递归的 actual 或 operands。
        return False

    # 只有具备 occurrence_id 的目录操作才能生成稳定 occurrence。
    return bool(expression.get("occurrence_id"))

# TraceResult 合并器维持 operation 去重、循环表与原因稳定性。
def _merge_result(accumulator: TraceAccumulator, result: TraceResult) -> None:
    """把一个子锥结果并入当前可变累加器。

    参数:
        accumulator: 当前递归帧的可变累加器。
        result: 已冻结的子锥 tracing 结果。

    返回:
        无；三个聚合维度原位更新。
    """

    # operation 集合按完整 occurrence ID 去重。
    accumulator.operations.update(result.operation_ids)

    # 循环状态严格使用固定三态合并表。
    accumulator.loop_presence = merge_loop_presence(  # 当前累加器合并后的循环三态
        accumulator.loop_presence,  # 既有累计循环状态
        result.loop_presence,  # 新子锥提供的循环状态
    )

    # reasons 在 __post_init__ 后必为当前 accumulator 私有集合。
    if accumulator.reasons is not None:

        # 子锥局部原因沿真实数据依赖传播。
        accumulator.reasons.update(result.inconclusive_reasons)

# 当前模块 input formal 通过 binding 返回 parent actual typed tree。
def _input_actual(
    context: TraceContext,
    reference: str,
) -> tuple[TraceContext, dict[str, Any]] | None:
    """查找 child input formal 对应的 parent actual 事实。

    参数:
        context: 当前 child 作用域 tracing 上下文。
        reference: 当前表达式读取的 child input 名称。

    返回:
        parent 上下文与 actual 事实；当前引用不是已绑定 input 时为 None。
    """

    # 位选 input 使用基础 formal 名查找 binding。
    str_formal = base_target(reference)  # 当前引用对应的 child input formal

    # 每条 output binding 携带同一实例的完整 input_actuals 快照。
    for hierarchy_binding in context.graph.bindings:

        # child occurrence 与 specialization 必须精确匹配当前作用域。
        if hierarchy_binding.child_path != context.current.instance_path:

            # 其他 child occurrence 的同名 formal 不能共享 actual。
            continue

        # 参数特化不一致时不能复用端口连接。
        if hierarchy_binding.child != context.current.specialization:

            # 继续查找当前 occurrence 的其他 output binding。
            continue

        # 输入目录恢复为 formal 到冻结 actual 的只读查找表。
        dict_inputs = dict(hierarchy_binding.input_actuals)  # 当前实例 input formal 绑定目录

        # 当前引用不是 input formal 时保留本地或未驱动语义。
        if str_formal not in dict_inputs:

            # 继续检查同实例其他 binding 的等价 input 快照。
            continue

        # parent 模块 key 与路径共同形成 actual 的求值作用域。
        scoped_parent = ScopedTarget(  # 唯一定位子模块输入端口在父模块中的实参来源
            context.current.root,  # 限定端口绑定所属的顶层定义入口
            hierarchy_binding.parent_path,  # 指向承载实例实参的父级 occurrence
            hierarchy_binding.parent,  # 区分父模块不同参数特化的实现快照
            str_formal,  # 以子模块输入端口名关联对应连接项
        )

        # 调用栈和 visiting 沿端口返回 parent 时保持不变。
        parent_context = TraceContext(  # child input 对应的 parent actual 上下文
            context.graph,  # 当前 definition root 的层次图
            scoped_parent,  # 端口 actual 的调用方求值端点
            context.loop_iteration_tuple,  # 继承当前循环迭代身份
            context.function_call_stack,  # 继承当前函数调用栈
            context.visiting,  # 继承当前递归访问集合
            context.session,  # 复用同一 definition root 的调用级索引
        )

        # actual 解冻后只读取 expression、span 与 unsupported reason。
        return parent_context, _mapping(dict_inputs[str_formal])

    # 没有 binding 时该引用由其他本地语义处理。
    return None

# 函数查找只接受名称匹配且 parse_complete 的本地冻结定义。
def _function_definition(module: SpecializedModule, callee: str) -> tuple[dict[str, Any] | None, str]:
    """查找当前特化模块内的本地函数定义。

    参数:
        module: 当前 occurrence 的特化模块。
        callee: function_call marker 引用的函数名。

    返回:
        完整函数事实与空原因；缺失或不完整时返回 None 和稳定原因。
    """

    # 函数目录按 source 声明顺序查找同名定义。
    for frozen_function in module.functions:

        # 解冻副本用于名称、完整性和函数体读取。
        dict_function = _mapping(frozen_function)  # 当前候选本地函数事实

        # 其他函数名与当前 call marker 无关。
        if str(dict_function.get("name") or "") != callee:

            # 继续查找当前模块后续函数定义。
            continue

        # formatter 明确不完整的函数定义不能形成确定展开。
        if not bool(dict_function.get("parse_complete", True)):

            # 优先保留函数事实自身的局部 unsupported reason。
            str_reason = str(dict_function.get("unsupported_reason") or "incomplete function definition")  # 当前函数缺口

            # 当前定义不完整时返回精确的函数局部原因。
            return None, f"{callee}: {str_reason}"

        # 名称匹配且完整的定义可供当前 call site 展开。
        return dict_function, ""

    # 不存在本地定义时保持当前 target inconclusive。
    return None, f"{callee}: missing function definition"

# actual 表达式提取兼容公共 actual fact 与 legacy parser 节点。
def _actual_expression(value: object) -> dict[str, Any]:
    """读取函数或实例 actual 的 typed expression 根。

    参数:
        value: actual fact、legacy actual 节点或 FrozenFact。

    返回:
        typed expression 字典；缺失时为空字典。
    """

    # actual 根先恢复为普通字典。
    dict_actual = _mapping(value)  # 当前 actual 兼容事实

    # 公共 actual fact 把 typed tree 保存在 expression 字段。
    obj_expression = dict_actual.get("expression")  # 当前 actual 的 typed tree 候选

    # 完整 expression 字典直接作为递归入口。
    if isinstance(obj_expression, dict):

        # 顶层复制避免递归过程接触缓存对象。
        return dict(obj_expression)

    # legacy function_call actual 本身可能就是 typed node wrapper。
    return dict_actual if dict_actual.get("kind") or dict_actual.get("node_kind") else {}

# actual 辅助入口隔离实参兼容恢复与调用方求值循环。
def _trace_function_actuals(
    context: TraceContext,
    module: SpecializedModule,
    expression: dict[str, Any],
    bindings: dict[str, dict[str, Any]],
    callee: str,
    accumulator: TraceAccumulator,
) -> list[dict[str, Any]]:
    """追踪函数调用的全部 actual 并返回有序 typed roots。

    参数:
        context: 当前函数调用方 tracing 上下文。
        module: 当前调用方特化模块。
        expression: function_call typed marker。
        bindings: 外层函数 formal 到 actual 的绑定。
        callee: 当前被调用函数名。
        accumulator: 当前 call site 的可变聚合器。

    返回:
        与调用位置一致的 actual typed expression 列表。
    """

    # actuals 优先使用公共 facts；legacy marker 则由 operands 位置对应。
    list_actual_values = list(expression.get("actuals", []) or [])  # 当前调用有序 actual facts

    # 缺少独立 actual facts 时 operands 就是调用实参表达式。
    if not list_actual_values:

        # 复制 legacy operands 以保持位置 formal binding。
        list_actual_values = list(_children(expression))  # 当前调用 legacy actual 节点

    # 每个 actual 先在调用方作用域计数与追踪引用。
    list_actual_expressions = [_actual_expression(obj_actual) for obj_actual in list_actual_values]  # 保持 formal 位置的实参表达式目录

    # actual typed tree 不完整时保留其他已知 actual 的操作下界。
    for int_position, dict_actual_expression in enumerate(list_actual_expressions):

        # 空 actual tree 只污染当前 formal 位置。
        if not dict_actual_expression:

            # 原因包含 callee 和位置，避免同一调用多个缺口合并。
            accumulator.reasons.add(f"{callee}: function actual {int_position} is incomplete")

            # 当前缺失位置不阻断其他完整 actual 的追踪。
            continue

        # 外层 formal binding 需要先替换 actual 中可能引用的函数 formal。
        trace_result_result_actual = _trace_expression(context, module, dict_actual_expression, bindings)  # actual 调用方结果

        # actual 操作与依赖在进入函数体之前并入调用结果。
        _merge_result(accumulator, trace_result_result_actual)

    # formal 绑定继续使用与源码位置一致的 typed roots。
    return list_actual_expressions

# 函数体辅助入口只负责兼容 typed body facts 并聚合每条表达式。
def _trace_function_body(
    context: TraceContext,
    module: SpecializedModule,
    function_fact: dict[str, Any],
    function_bindings: dict[str, dict[str, Any]],
    callee: str,
    accumulator: TraceAccumulator,
) -> None:
    """追踪一个完整函数定义的全部 body expressions。

    参数:
        context: 已压入当前 call site 的函数体上下文。
        module: 当前调用方特化模块。
        function_fact: formatter 提供的完整函数定义事实。
        function_bindings: 当前函数 formal 到 actual typed roots 的绑定。
        callee: 当前被调用函数名。
        accumulator: 当前 call site 的可变聚合器。

    返回:
        无；函数体 operation、loop 和原因原位并入 accumulator。
    """

    # body_expressions 允许 formatter 和兼容 fixture 提供两种节点外形。
    for obj_body_fact in function_fact.get("body_expressions", []) or []:

        # 函数体事实恢复成普通字典读取 typed expression。
        dict_body_fact = _mapping(obj_body_fact)  # 当前函数体表达式事实

        # 公共 body fact 以 expression 字段保存 typed tree。
        obj_body_expression = dict_body_fact.get("expression")  # 当前函数体 typed tree 候选

        # 先以空根表示当前 body fact 尚未提供可追踪表达式。
        dict_body_expression = {}  # 当前可追踪函数体表达式根

        # 公共函数事实优先使用独立 typed expression 字段。
        if isinstance(obj_body_expression, dict):

            # 顶层复制隔离冻结函数事实缓存。
            dict_body_expression = dict(obj_body_expression)  # 公共函数体 typed expression 副本

        # legacy fixture 允许 body fact 本身充当 typed node。
        elif dict_body_fact.get("kind") or dict_body_fact.get("node_kind"):

            # 兼容节点复制后进入统一表达式追踪入口。
            dict_body_expression = dict_body_fact  # legacy fixture 提供的函数体表达式节点

        # 缺失 typed body 必须局部 fail-closed，严禁重解析 expression_text。
        if not dict_body_expression:

            # 原因保留函数名和不完整函数体边界。
            accumulator.reasons.add(f"{callee}: function body expression is incomplete")

            # 当前缺失 body fact 不阻断同一函数的其他完整事实。
            continue

        # 函数体 operation 使用 push 后完整调用栈生成 occurrence ID。
        trace_result_result_body = _trace_expression(  # 当前函数体表达式结果
            context,  # 带当前 call-site 栈帧的函数体上下文
            module,  # 函数体所属 occurrence 特化模块
            dict_body_expression,  # 当前函数体 typed expression
            function_bindings,  # 当前函数 formal actual 绑定
        )

        # 多条函数体赋值按 occurrence 集合合并。
        _merge_result(accumulator, trace_result_result_body)

# 函数调用身份 helper 隔离 span 回退和递归栈判定。
def _function_call_identity(
    context: TraceContext,
    expression: dict[str, Any],
    str_callee: str,
) -> tuple[str, bool]:
    """构造函数调用栈元素并判断当前路径是否递归再入。

    参数:
        context: 当前调用方 tracing 上下文。
        expression: function_call typed marker。
        str_callee: 当前被调用函数名。

    返回:
        稳定调用栈元素与当前函数是否已在调用栈中的布尔值。
    """

    # call-site span 构成完整函数调用栈元素。
    dict_call_span = _mapping(expression.get("span"))  # 当前函数调用一基源码范围

    # 缺少权威范围时退回 marker occurrence_id，仍保持调用点隔离。
    str_call_span = (
        f"{int(dict_call_span.get('line_start') or 1)}:{int(dict_call_span.get('column_start') or 1)}-"
        f"{int(dict_call_span.get('line_end') or dict_call_span.get('line_start') or 1)}:"
        f"{int(dict_call_span.get('column_end') or 1)}"
        if dict_call_span  # 权威调用范围存在时构造 span 文本
        else str(expression.get("occurrence_id") or "unknown-call-site")  # 缺失 span 时的调用点身份
    )  # 当前函数调用的稳定位置文本

    # 栈元素同时携带函数名与 call site，区分同一函数体多次调用。
    str_stack_item = f"{str_callee}@{str_call_span}"  # 当前函数调用栈元素

    # 同一函数名在完整调用栈再入时形成局部递归边界。
    bool_recursive = any(  # 同名 callee 是否已出现在外层调用路径
        str_item.startswith(f"{str_callee}@")  # 当前栈帧是否属于同名函数
        for str_item in context.function_call_stack  # 遍历从外到内的完整调用栈
    )  # 当前调用是否递归再入同名函数

    # 两项身份必须来自同一次 span 解析和调用栈检查。
    return str_stack_item, bool_recursive

# formal 绑定 helper 只接受同时存在的结构化 formal 与 typed actual。
def _bind_function_actuals(
    dict_function_fact: dict[str, Any],
    list_actual_expressions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """按声明位置建立本地函数 formal 到调用方 actual 的绑定。

    参数:
        dict_function_fact: 当前 callee 的结构化函数事实。
        list_actual_expressions: 按实参位置排列的 typed actual 列表。

    返回:
        只包含名称和 actual 均完整的函数绑定字典。
    """

    # formal 声明按 position 与 actual 对齐，缺失位置不生成猜测绑定。
    list_formals = list(dict_function_fact.get("formals", []) or [])  # 当前函数有序 formal 目录

    # 返回字典只包含结构化 formal 和同位置完整 actual。
    return {
        str(dict_formal.get("name") or ""): list_actual_expressions[int_position]  # formal 名到 typed actual 的绑定
        for int_position, dict_formal in enumerate(list_formals)  # 按声明位置遍历 formal
        if isinstance(dict_formal, dict)  # 只接受结构化 formal 事实
        and int_position < len(list_actual_expressions)  # 跳过缺失 actual 的 formal
        and bool(list_actual_expressions[int_position])  # 只绑定完整 typed actual
    }

# 函数调用先计 actual，再以完整 call stack 进入冻结函数体。
def _trace_function_call(
    context: TraceContext,
    module: SpecializedModule,
    expression: dict[str, Any],
    bindings: dict[str, dict[str, Any]],
) -> TraceResult:
    """追踪一个 function_call marker 的 actual 与本地函数体。

    参数:
        context: 当前调用方 tracing 上下文。
        module: 当前调用方特化模块。
        expression: function_call typed marker。
        bindings: 外层函数 formal 到 actual 的绑定。

    返回:
        marker 零成本但 actual 与函数体完整展开的 tracing 结果。
    """

    # 每次函数调用拥有独立聚合器，最后再并入外层表达式。
    accumulator = TraceAccumulator(set())  # 当前 call site 的操作与原因

    # callee 名用于本地函数定义查找和递归调用栈检测。
    str_callee = str(expression.get("callee") or expression.get("operator") or "")  # 当前被调用函数名

    # 调用身份 helper 同时给出稳定栈元素和递归再入状态。
    tuple_call_identity = _function_call_identity(context, expression, str_callee)  # 当前调用身份与递归状态

    # 第一项是将要压入函数调用栈的稳定 call-site 文本。
    str_stack_item = tuple_call_identity[0]  # 拼接函数体 occurrence ID 的调用点帧

    # 第二项标识同名函数是否已在外层调用路径中出现。
    bool_recursive = tuple_call_identity[1]  # 当前调用是否递归再入

    # 同一函数名在当前完整调用栈再入视为递归函数边界。
    if bool_recursive:

        # 递归函数只污染当前 call site，不阻断其他 producer。
        accumulator.reasons.add(f"{str_callee}: recursive function call")

        # 已知 actual 为空时仍返回当前调用点的局部递归原因。
        return _freeze_accumulator(accumulator)

    # actual 兼容恢复与调用方求值由独立辅助入口完成。
    list_actual_expressions = _trace_function_actuals(  # 保存按形参声明位置排列并已核算操作的实参表达式
        context,  # 在调用方 occurrence 中核算实参操作
        module,  # 提供实参引用所依赖的本地驱动目录
        expression,  # 提供按源码顺序排列的函数实参节点
        bindings,  # 解析嵌套函数调用中的外层形参引用
        str_callee,  # 给实参缺口诊断附加被调用函数身份
        accumulator,  # 汇入该调用点已经确定的操作与局部原因
    )

    # 本地函数定义缺失或不完整时保留已知 actual 下界。
    tuple_function_lookup = _function_definition(module, str_callee)  # 当前 callee 的定义与原因查询结果

    # 函数事实与失败原因分别供后续完整性分支读取。
    dict_function_fact = tuple_function_lookup[0]  # 当前 callee 的结构化函数事实

    # 查询失败原因只在函数事实缺失时传播到当前 call site。
    str_function_reason = tuple_function_lookup[1]  # 当前 callee 的局部查询失败原因

    # 缺失函数体不能按零操作 marker 放行。
    if dict_function_fact is None:

        # 精确原因只属于当前 call site。
        accumulator.reasons.add(str_function_reason)

        # 已追踪的 actual 下界与函数定义缺口一起返回。
        return _freeze_accumulator(accumulator)

    # 本次调用只绑定同时存在 formal 与 typed actual 的声明位置。
    dict_function_bindings = _bind_function_actuals(dict_function_fact, list_actual_expressions)  # 函数体读取形参时使用的实参表达式表

    # push 后的完整栈用于函数体内全部 operation occurrence。
    context_function = TraceContext(  # 已压入 call site 的函数体上下文
        context.graph,  # 函数调用所在的层次绑定图
        context.current,  # 函数体仍属于调用方模块 occurrence
        context.loop_iteration_tuple,  # 继承调用点循环迭代身份
        context.function_call_stack + (str_stack_item,),  # 压入当前函数调用点
        context.visiting,  # 继承数据依赖递归访问集合
        context.session,  # 函数展开复用同一 root 的调用级索引
    )

    # 函数体辅助入口使用已绑定 formal 和完整调用栈聚合结果。
    _trace_function_body(
        context_function,
        module,
        dict_function_fact,
        dict_function_bindings,
        str_callee,
        accumulator,
    )

    # 函数调用 marker 本身不增加任何 operation。
    return _freeze_accumulator(accumulator)

# identifier helper 保持函数 formal 截断与普通作用域引用两种语义。
def _trace_identifier_expression(
    context: TraceContext,
    expression: dict[str, Any],
    dict_bindings: dict[str, dict[str, Any]],
    accumulator: TraceAccumulator,
) -> TraceResult:
    """追踪 identifier 叶节点的函数绑定或普通信号来源。

    参数:
        context: 当前表达式所属 tracing 上下文。
        expression: identifier typed 节点。
        dict_bindings: 函数 formal 到调用方 actual 的绑定。
        accumulator: 已包含 identifier 自身 operation 的聚合器。

    返回:
        identifier 上游依赖合并后的冻结结果。
    """

    # 两代 typed tree 分别使用 name 与 text 保存标识符名称。
    str_reference = str(expression.get("name") or expression.get("text") or "")  # 当前引用名称

    # formal actual 已在进入函数体前按调用方上下文完整追踪。
    if str_reference in dict_bindings:

        # 函数体中的 formal 读取不重复核算调用方 actual。
        return _freeze_accumulator(accumulator)

    # 空标识符不形成可查询的作用域 endpoint。
    if not str_reference:

        # 空叶节点没有可继续递归的数据来源。
        return _freeze_accumulator(accumulator)

    # 引用目标继承 definition root、path 和 specialization。
    scoped_reference = ScopedTarget(  # 唯一定位当前实例和参数特化下的标识符驱动来源
        context.current.root,  # 限定标识符所属的顶层定义入口
        context.current.instance_path,  # 隔离同名标识符所在的实例 occurrence
        context.current.specialization,  # 隔离同模块不同参数特化的驱动事实
        str_reference,  # 关联 formatter 提取的静态信号引用
    )

    # visiting 与调用栈沿数据依赖保持完整。
    trace_result_result_reference = trace_reference(context, str_reference, scoped_reference)  # identifier 上游组合锥

    # identifier 的上游操作和局部原因进入当前表达式。
    _merge_result(accumulator, trace_result_result_reference)

    # identifier 没有其他结构子节点需要递归。
    return _freeze_accumulator(accumulator)

# select helper 只恢复静态端点，不重复遍历 selector 索引子树。
def _trace_select_expression(
    context: TraceContext,
    expression: dict[str, Any],
    accumulator: TraceAccumulator,
) -> TraceResult:
    """追踪位选或切片恢复出的全部静态基础端点。

    参数:
        context: 当前表达式所属 tracing 上下文。
        expression: select typed 节点。
        accumulator: 已包含 select 自身 operation 的聚合器。

    返回:
        全部静态端点上游结果合并后的冻结结果。
    """

    # 每个静态或保守基础引用独立进入作用域 tracing。
    for str_reference in reference_targets(expression):

        # 引用目标保留常量位选或切片文本。
        scoped_reference = ScopedTarget(  # 位选或切片恢复出的精确数据端点
            context.current.root,  # 选择表达式所属根定义
            context.current.instance_path,  # 选择表达式所在实例路径
            context.current.specialization,  # 选择表达式采用的模块特化
            str_reference,  # select 恢复出的静态目标
        )

        # select 的基础端点追踪不能重复计算选择操作本身。
        trace_result_result_reference = trace_reference(context, str_reference, scoped_reference)  # select 基础端点上游组合锥

        # 当前静态端点的上游结果进入 select 聚合器。
        _merge_result(accumulator, trace_result_result_reference)

    # selector 索引子树不重复作为独立 data producer 遍历。
    return _freeze_accumulator(accumulator)

# 表达式递归在 typed tree 上同时完成 operation 计数与引用 tracing。
def _trace_expression(
    context: TraceContext,
    module: SpecializedModule,
    expression: dict[str, Any],
    bindings: dict[str, dict[str, Any]] | None = None,
) -> TraceResult:
    """追踪一个 typed expression 子树的操作与数据依赖。

    参数:
        context: 当前表达式所属 tracing 上下文。
        module: 当前作用域特化模块。
        expression: formatter typed expression 根节点。
        bindings: 函数 formal 到调用方 actual tree 的可选绑定。

    返回:
        当前表达式子树的冻结 tracing 结果。
    """

    # 缺省绑定为空映射，避免在递归中反复构造 None 分支。
    dict_bindings = bindings or {}  # 当前函数 formal actual 绑定目录

    # 当前表达式使用独立累加器聚合子树结果。
    accumulator = TraceAccumulator(set())  # 当前表达式子树聚合器

    # 公共 node_kind 与 legacy kind 统一为单一分派值。
    str_kind = str(expression.get("node_kind") or expression.get("kind") or "")  # 当前 typed node 类别

    # function_call 由专用入口保证 actual 先计和 marker 零成本。
    if str_kind == "function_call":

        # 当前函数调用结果已经包含 actual、body 和调用栈语义。
        return _trace_function_call(context, module, expression, dict_bindings)

    # 真实 operation 按完整上下文生成 occurrence ID。
    if _is_counted_operation(expression):

        # 当前节点只贡献一次稳定 occurrence。
        accumulator.operations.add(operation_occurrence_id(context, expression))

    # identifier 叶节点进入本地 producer 或 child input binding。
    if str_kind == "identifier":

        # 专用 helper 处理 formal actual 截断和普通信号引用。
        return _trace_identifier_expression(context, expression, dict_bindings, accumulator)

    # select 使用 selector 模块恢复精确静态端点，避免基础值重复 tracing。
    if str_kind == "select":

        # 专用 helper 保证静态选择器不被作为独立数据子树重复计算。
        return _trace_select_expression(context, expression, accumulator)

    # 普通运算节点按 typed tree 原顺序递归所有可达子节点。
    for dict_child in _children(expression):

        # 子节点继承相同作用域、循环身份和函数调用栈。
        trace_result_result_child = _trace_expression(context, module, dict_child, dict_bindings)  # 当前操作数子树结果

        # 共享引用经完整 occurrence ID 集合自动去重。
        _merge_result(accumulator, trace_result_result_child)

    # 返回当前表达式完整的已知操作下界与局部原因。
    return _freeze_accumulator(accumulator)

# accumulator 冻结入口统一排序原因并保留操作集合。
def _freeze_accumulator(accumulator: TraceAccumulator) -> TraceResult:
    """把调用栈内可变聚合器转换成 TraceResult。

    参数:
        accumulator: 已完成当前递归帧聚合的可变对象。

    返回:
        operation、loop 和 reasons 均稳定冻结的 tracing 结果。
    """

    # __post_init__ 保证 reasons 非 None，兼容类型检查仍显式回落空集合。
    set_reasons = accumulator.reasons or set()  # 当前递归帧原因集合

    # 原因按字典序冻结，operation 集合保留集合身份语义。
    return TraceResult(
        frozenset(accumulator.operations),
        accumulator.loop_presence,
        tuple(sorted(set_reasons)),
    )

# reference 入口优先处理 child input binding，再进入普通作用域目标。
def trace_reference(
    context: TraceContext,
    reference: str,
    target: ScopedTarget | None = None,
) -> TraceResult:
    """追踪当前作用域内一个静态引用的完整组合上游。

    参数:
        context: 当前读取引用的 tracing 上下文。
        reference: formatter 提取的静态或基础引用名称。
        target: 可选的预构造作用域目标。

    返回:
        当前引用可达的 operation、loop 与局部原因。
    """

    # 调用方未提供端点时使用当前作用域构造引用目标。
    scoped_reference = target or ScopedTarget(  # 当前引用的作用域静态端点
        context.current.root,  # 引用继承当前 definition root
        context.current.instance_path,  # 引用继承当前 occurrence 路径
        context.current.specialization,  # 引用继承当前参数特化
        reference,  # formatter 提供的静态引用名称
    )

    # input formal 先返回 parent actual typed tree 计算调用方操作。
    tuple_actual = _input_actual(context, reference)  # 可选 child formal 到 parent actual 绑定

    # 找到 child input binding 时不把 formal 当成本地未驱动信号。
    if tuple_actual is not None:

        # actual fact 同时携带 typed tree 完整性和局部 unsupported reason。
        parent_context, dict_actual = tuple_actual  # parent actual 上下文与事实

        # actual 表达式是不重解析 text 的唯一数据入口。
        dict_expression = _actual_expression(dict_actual)  # parent actual 的唯一 typed tree 入口

        # 缺失 typed tree 只污染当前 formal 返回路径。
        if not dict_expression:

            # 固定原因指出不完整实例 actual 边界。
            return TraceResult(frozenset(), "unknown", (f"{reference}: instance actual is incomplete",))

        # actual 在 parent occurrence 作用域内生成 operation occurrence。
        module_parent = _module_for_context(  # actual 求值所需的 parent occurrence 特化模块
            context,  # 携带 parent occurrence 共用的调用级索引
            parent_context.current,  # actual 所属 parent 作用域目标
        )

        # parent occurrence 缺失时 graph 本身不完整。
        if module_parent is None:

            # 原因携带 parent path 供局部定位。
            return TraceResult(frozenset(), "unknown", (f"{reference}: parent occurrence is missing",))

        # typed actual 支持保留已知 operation 下界和引用传播。
        trace_result_result_actual = _trace_expression(parent_context, module_parent, dict_expression)  # 当前 actual 的调用方作用域结果

        # actual fact 明确不完整时在已知下界上附加局部原因。
        str_reason = str(dict_actual.get("unsupported_reason") or "")  # 当前 actual 局部不支持原因

        # 权威 span 缺失同样禁止把 actual 视为完整 occurrence 证据。
        if str_reason or not bool(dict_actual.get("span_complete", True)):

            # 原因集合扩展但不丢弃已知 actual operation。
            set_reasons = set(trace_result_result_actual.inconclusive_reasons)  # 当前 actual 已知原因副本

            # 优先保存 formatter 精确 unsupported reason。
            set_reasons.add(str_reason or f"{reference}: instance actual span is incomplete")

            # loop unknown 表示不完整 actual 可能隐藏循环或函数展开。
            return TraceResult(trace_result_result_actual.operation_ids, "unknown", tuple(sorted(set_reasons)))

        # 完整 actual 直接返回调用方作用域结果。
        return trace_result_result_actual

    # 普通本地引用进入作用域目标递归。
    reference_context = TraceContext(  # 当前引用对应的 tracing 上下文
        context.graph,  # 普通信号引用所在的层次绑定图
        scoped_reference,  # 已确定作用域的引用目标
        context.loop_iteration_tuple,  # 引用沿用调用点循环迭代身份
        context.function_call_stack,  # 引用沿用调用点函数栈
        context.visiting,  # 引用沿用当前依赖访问集合
        context.session,  # 引用递归复用同一 definition root 的索引
    )

    # 统一目标入口负责环检测、本地 producer 和实例 output。
    return _trace_scoped_target(reference_context)

# 本地数据 helper 负责 typed expression 缺口和过程前版本截断。
def _trace_local_data_expression(
    context: TraceContext,
    context_fact: TraceContext,
    module: SpecializedModule,
    fact: dict[str, Any],
    accumulator: TraceAccumulator,
) -> None:
    """把一个本地事实的数据表达式结果并入聚合器。

    参数:
        context: 当前事实目标所属 tracing 上下文。
        context_fact: 已绑定事实循环身份的 tracing 上下文。
        module: 当前 occurrence 特化模块。
        fact: 已解冻的本地组合事实。
        accumulator: 当前事实的数据和控制聚合器。

    返回:
        无；数据表达式结果或局部缺口原地并入 accumulator。
    """

    # 右值 typed expression 是本地数据锥主入口。
    obj_expression = fact.get("expression")  # 当前事实右值 typed tree

    # 缺失表达式时保留局部 unknown 而不是零操作通过。
    if not isinstance(obj_expression, dict):

        # 原因只归属当前静态 target。
        accumulator.reasons.add(f"{context.current.target}: missing typed expression")

        # 缺失 typed tree 后不能继续猜测数据依赖。
        return

    # 过程类别与赋值操作符共同决定同目标前版本截断语义。
    str_process_kind = str(fact.get("process_kind") or "")  # 当前驱动事实的过程类别

    # 组合过程中的阻塞赋值读取同目标时指向过程内前一版本。
    bool_blocking_comb = str_process_kind == "comb" and str(fact.get("assignment_operator") or "=") == "="  # 是否截断同目标自引用

    # 非阻塞或非组合赋值不建立前版本截断绑定。
    dict_version_bindings = {base_target(str(fact.get("target") or "")): {}} if bool_blocking_comb else {}  # 前版本截断绑定

    # 右值操作和引用按 typed tree 递归。
    trace_result_result_expression = _trace_expression(  # 当前事实数据表达式结果
        context_fact,  # 携带当前循环迭代身份的事实上下文
        module,  # 数据表达式所属的特化模块
        dict(obj_expression),  # 本地驱动的右值表达式副本
        dict_version_bindings,  # 过程前版本截断绑定
    )

    # 数据表达式的操作和上游引用并入当前事实。
    _merge_result(accumulator, trace_result_result_expression)

# 本地 controls helper 隔离控制节点的循环和缺口处理。
def _trace_local_controls(
    context: TraceContext,
    context_fact: TraceContext,
    module: SpecializedModule,
    fact: dict[str, Any],
    accumulator: TraceAccumulator,
) -> None:
    """把一个本地事实的全部控制表达式并入聚合器。

    参数:
        context: 当前事实目标所属 tracing 上下文。
        context_fact: 已绑定事实循环身份的 tracing 上下文。
        module: 当前 occurrence 特化模块。
        fact: 已解冻的本地组合事实。
        accumulator: 当前事实的数据和控制聚合器。

    返回:
        无；控制表达式结果或局部缺口原地并入 accumulator。
    """

    # controls 中比较、逻辑和选择操作同样属于目标完整组合锥。
    for obj_control in fact.get("controls", []) or []:

        # 非字典控制事实不能提供 typed expression。
        if not isinstance(obj_control, dict):

            # 局部缺口不影响同一事实其他完整控制节点。
            accumulator.reasons.add(f"{context.current.target}: control expression is incomplete")

            # 当前缺失控制节点不阻断后续 controls。
            continue

        # 每个控制 typed tree 使用同一 fact loop identity。
        trace_result_result_control = _trace_expression(context_fact, module, dict(obj_control))  # 当前控制表达式结果

        # 控制表达式的操作和上游依赖并入当前事实。
        _merge_result(accumulator, trace_result_result_control)

# 单个本地组合事实计算自身 operation、loop 与上游引用。
def _trace_local_fact(
    context: TraceContext,
    module: SpecializedModule,
    fact: dict[str, Any],
) -> TraceResult:
    """追踪一个特化模块本地组合驱动事实。

    参数:
        context: 当前事实目标所属 tracing 上下文。
        module: 当前 occurrence 特化模块。
        fact: 已解冻的本地组合事实。

    返回:
        当前事实表达式和控制依赖的 tracing 结果。
    """

    # 当前事实独立聚合 expression、controls 和 loop 状态。
    accumulator = TraceAccumulator(set())  # 当前本地驱动事实聚合器

    # parser 缺口保留局部原因且不读取缺失 typed tree。
    str_error = str(fact.get("parse_error") or "")  # 当前事实解析缺口

    # 解析失败时没有可信 expression 可继续追踪。
    if str_error:

        # 原因携带当前静态目标便于定位。
        accumulator.reasons.add(f"{context.current.target}: {str_error}")

        # 解析缺口禁止把当前事实误判为零操作。
        return _freeze_accumulator(accumulator)

    # 已物化循环事实携带完整迭代 tuple。
    tuple_iterations = tuple(int(obj_item) for obj_item in fact.get("loop_iteration_tuple", ()) or ())  # 当前事实循环身份

    # from_for 或非空迭代元组都证明当前数据路径含循环。
    loop_presence_str_fact_loop: LoopPresence = (  # 当前事实可达路径的循环三态
        "present" if bool(fact.get("from_for")) or tuple_iterations else "absent"  # 事实自身循环证据
    )

    # operation occurrence 使用当前事实完整循环迭代身份。
    context_fact = TraceContext(  # 本地事实 operation occurrence 上下文
        context.graph,  # 本地驱动事实所在的层次绑定图
        context.current,  # 当前事实作用域目标
        tuple_iterations,  # 当前事实静态循环迭代身份
        context.function_call_stack,  # 本地事实沿用目标函数栈
        context.visiting,  # 本地事实沿用目标访问集合
        context.session,  # 本地事实复用同一 root 的调用级索引
    )

    # 数据和控制分别委派给低复杂度 helper，仍共享同一事实聚合器。
    _trace_local_data_expression(context, context_fact, module, fact, accumulator)

    # 控制节点与数据表达式共享同一循环身份和聚合器。
    _trace_local_controls(context, context_fact, module, fact, accumulator)

    # 当前事实自身 loop 状态与数据/控制上游合并。
    accumulator.loop_presence = merge_loop_presence(accumulator.loop_presence, loop_presence_str_fact_loop)  # 当前事实完整循环状态

    # 返回单事实已知下界与局部原因。
    return _freeze_accumulator(accumulator)

# storage synthetic endpoints 分别暴露 D expression 与 enable controls。
def _trace_storage_projection(
    context: TraceContext,
    module: SpecializedModule,
) -> TraceResult | None:
    """追踪 storage D 或 enable synthetic endpoint。

    参数:
        context: 当前 synthetic endpoint tracing 上下文。
        module: 当前 occurrence 特化模块。

    返回:
        D/enable tracing 结果；普通 endpoint 返回 None。
    """

    # 后缀决定当前 endpoint 是否属于 storage projection。
    str_target = context.current.target  # 当前作用域静态目标名称

    # 普通 endpoint 不进入 storage projection 分支。
    if not str_target.endswith("$D") and not str_target.endswith("$enable"):

        # None 让调用方继续普通本地和实例 producer tracing。
        return None

    # synthetic 后缀之前的名称对应原 storage Q target。
    str_storage_target = str_target.rsplit("$", 1)[0]  # 当前 projection 对应的 Q 目标

    # 完整 Q endpoint 键隔离不同实例和参数特化下的同名寄存器。
    scoped_storage = ScopedTarget(  # 当前 synthetic endpoint 对应的 Q 身份
        context.current.root,  # 保持当前 definition root
        context.current.instance_path,  # 保持当前 occurrence 路径
        context.current.specialization,  # 保持当前参数特化
        str_storage_target,  # 移除 synthetic 后缀后的 Q target
    )

    # 批量入口从 session 常数时间读取；兼容上下文只解冻一次完整目录。
    tuple_storage_facts = (  # 当前 Q target 的全部时序驱动事实
        context.session.dict_storage_facts.get(scoped_storage, ())  # 复用调用级 storage 索引
        if context.session is not None  # 批量和公开单目标入口始终提供 session
        else tuple(  # 兼容直接内部调用的无 session 路径
            dict_fact  # 保留匹配 Q target 的普通事实字典
            for dict_fact in (_mapping(item) for item in module.storage_drivers)  # 每条时序事实只解冻一次
            if str(dict_fact.get("target") or "").replace(" ", "") == str_storage_target  # 只选择当前 Q target
        )
    )

    # 当前 projection 聚合匹配 storage facts 的 expression 或 controls。
    accumulator = TraceAccumulator(set())  # 隔离 D 或 enable 端点的操作与未知原因

    # 同一 Q target 的多个 storage facts 保持全部 producer 证据。
    for dict_fact in tuple_storage_facts:

        # storage D 只读取数据表达式，不读取 enable controls。
        if str_target.endswith("$D"):

            # typed expression 是 D 锥唯一入口。
            obj_expression = dict_fact.get("expression")  # 当前 storage D 数据表达式根

            # 缺失 D typed tree 必须局部 fail-closed。
            if not isinstance(obj_expression, dict):

                # 原因只污染当前 D endpoint。
                accumulator.reasons.add(f"{str_target}: missing storage data expression")

                # 当前缺失 D 不阻断同一 Q target 的其他 storage facts。
                continue

            # D operation 使用当前 child occurrence 身份，但不传播到 Q。
            trace_result_result_data = _trace_expression(context, module, dict(obj_expression))  # 当前 storage D 数据锥结果

            # D 数据锥只并入当前 synthetic endpoint。
            _merge_result(accumulator, trace_result_result_data)

            # D projection 已处理完当前 storage fact。
            continue

        # enable projection 逐项追踪 storage fact 的控制表达式。
        for obj_control in dict_fact.get("controls", []) or []:

            # 非结构化 control 只形成当前 enable endpoint 局部原因。
            if not isinstance(obj_control, dict):

                # 继续保留同一 storage fact 其他可确定 controls。
                accumulator.reasons.add(f"{str_target}: incomplete storage enable expression")

                # 当前缺失 control 不阻断后续完整 enable 表达式。
                continue

            # 每个 enable control 都在当前 synthetic endpoint 内独立追踪。
            trace_result_result_enable = _trace_expression(context, module, dict(obj_control))  # 当前存储使能控制锥结果

            # enable 控制锥并入当前 synthetic endpoint。
            _merge_result(accumulator, trace_result_result_enable)

    # 没有匹配 storage fact 表示 graph projection 与模块快照不一致。
    if not tuple_storage_facts:

        # 固定原因避免 synthetic endpoint 静默按零操作通过。
        accumulator.reasons.add(f"{str_target}: storage driver is missing")

    # D/enable projection 不继承 Q producer 的 storage cut。
    return _freeze_accumulator(accumulator)

# 作用域目标入口先做分支环检测，再复用完整身份 memo。
def _trace_scoped_target(context: TraceContext) -> TraceResult:
    """追踪一个 ScopedTarget 的全部可达组合 producer。

    参数:
        context: 当前目标及完整递归身份上下文。

    返回:
        当前目标的冻结 tracing 结果；环路结果不会进入 memo。
    """

    # 再入同一完整 ScopedTarget 形成跨层组合环。
    if context.current in context.visiting:

        # bounded 原因包含可读路径和目标，禁止无限递归。
        str_path = "/".join(context.current.instance_path)  # 当前环路作用域路径

        # 组合环在当前 endpoint 局部失败关闭。
        return TraceResult(frozenset(), "unknown", (f"cycle at {str_path}:{context.current.target}",))

    # memo 键覆盖所有会改变 operation occurrence 身份的上下文维度。
    tuple_memo_key = (  # 纳入调用身份，避免跨循环或函数调用复用错误结果
        context.current,  # 锁定递归结果所属的完整作用域端点
        context.loop_iteration_tuple,  # 区分同一端点的循环展开 occurrence
        context.function_call_stack,  # 区分同一函数体的不同调用点展开
    )

    # 只有同一调用级 session 内允许读取 memo。
    if context.session is not None and tuple_memo_key in context.session.dict_memo:

        # TraceResult 已冻结，可安全由多个上游目标共享。
        return context.session.dict_memo[tuple_memo_key]

    # 未命中缓存时执行原有 Q cut、producer 与 unknown 聚合逻辑。
    trace_result_result = _trace_scoped_target_uncached(context)  # 当前完整身份首次计算结果

    # 环检测必须依赖当前 visiting 分支，含环原因的结果禁止跨兄弟路径缓存。
    bool_has_cycle = any(str_reason.startswith("cycle at ") for str_reason in trace_result_result.inconclusive_reasons)  # 当前结果是否包含分支环

    # 其他 pass、unknown 或 inconclusive 结果仅依赖完整 memo 键，可在当前 root 内复用。
    if context.session is not None and not bool_has_cycle:

        # 缓存只写入当前调用级 session，不修改 graph 或模块级状态。
        context.session.dict_memo[tuple_memo_key] = trace_result_result  # 保存无环分支的冻结结果

    # 调用方获得与未缓存路径完全相同的冻结结果。
    return trace_result_result

# endpoint producer helper 合并实例输出、Q cut 与局部 unknown 边界。
def _merge_endpoint_producers(
    context: TraceContext,
    context_entered: TraceContext,
    accumulator: TraceAccumulator,
) -> None:
    """把当前 endpoint 的非本地 producer 证据并入聚合器。

    参数:
        context: 当前作用域目标的原始 tracing 上下文。
        context_entered: 已把当前目标加入 visiting 的递归上下文。
        accumulator: 当前作用域目标的 producer 聚合器。

    返回:
        无；跨层结果或局部未知边界原地并入 accumulator。
    """

    # endpoint producer 目录补充实例输出、Q cut 与局部 unknown。
    for producer_ref in _drivers_for_context(context):

        # 本地 continuous/combinational 已由完整 expression facts 处理。
        if producer_ref.kind in {"continuous", "combinational"}:

            # 跳过目录摘要，防止同一事实重复 tracing。
            continue

        # storage Q 与 exact Q bridge 都是组合传播切点。
        if producer_ref.kind in {"storage_q", "exact_q_bridge"}:

            # Q 端不继承 D、enable 或内部前级 operation。
            continue

        # 确定实例 output 进入 child formal 的作用域目标。
        if producer_ref.kind == "instance_output":

            # child output 继承 parent 的 visiting 集合以检测跨层组合环。
            context_child = TraceContext(  # child output 跨层递归上下文
                context.graph,  # 实例输出跨层追踪使用的绑定图
                producer_ref.scoped_target,  # 实例输出指向的 child formal
                context.loop_iteration_tuple,  # 继承 parent 循环迭代身份
                context.function_call_stack,  # 继承 parent 函数调用栈
                context_entered.visiting,  # 继承已登记 parent 的访问集合
                context.session,  # child output 复用同一 root 的索引
            )

            # child path 与 specialization 共同限定跨层 producer 的 occurrence 身份。
            trace_result_result_child = _trace_scoped_target(context_child)  # child output 的跨层组合锥结果

            # 跨层 child 结果并入 parent endpoint。
            _merge_result(accumulator, trace_result_result_child)

            # 当前实例输出 producer 已完成处理。
            continue

        # unresolved boundary 和其他 unknown producer 只污染当前 endpoint。
        str_reason = producer_ref.unknown_reason or f"{producer_ref.kind}: unresolved producer boundary"  # 当前 producer 局部原因

        # 原因与同 endpoint 已知 producer 并存，不覆盖已知操作下界。
        accumulator.reasons.add(str_reason)

        # 未展开 producer 可能隐藏循环，除非其他路径已证明 present。
        accumulator.loop_presence = merge_loop_presence(accumulator.loop_presence, "unknown")  # 未展开 producer 的保守循环影响

# 未缓存实现保留 Q cut、本地 producer、实例 output 与 unknown 边界。
def _trace_scoped_target_uncached(context: TraceContext) -> TraceResult:
    """计算一个未命中 memo 的作用域目标。

    参数:
        context: 已完成环检测且未命中 memo 的 tracing 上下文。

    返回:
        当前目标按既有 producer 语义聚合的冻结结果。
    """

    # 当前 occurrence 缺失时 graph 无法提供本地 facts。
    module = _module_for_context(context)  # 当前目标所属特化模块

    # 不存在模块节点只污染当前 endpoint。
    if module is None:

        # 原因区分 graph occurrence 缺失与 HDL module 实现缺失。
        return TraceResult(frozenset(), "unknown", (f"{context.current.target}: module occurrence is missing",))

    # storage synthetic endpoints 只读取对应 D 或 enable 事实。
    result_storage = _trace_storage_projection(context, module)  # 当前目标的可选 storage 投影结果

    # projection 已经完整处理时不再遍历 Q producer 目录。
    if result_storage is not None:

        # 独立 D/enable 结果供 child path target 单独检查。
        return result_storage

    # visiting 仅为当前递归分支增加目标，兄弟 producer 相互独立。
    context_entered = TraceContext(  # 已登记当前目标的递归上下文
        context.graph,  # 当前目标递归入口使用的层次图
        context.current,  # 当前待追踪作用域目标
        context.loop_iteration_tuple,  # 当前目标沿用外层循环身份
        context.function_call_stack,  # 当前目标沿用外层函数栈
        context.visiting | {context.current},  # 把当前目标加入分支访问集合
        context.session,  # 递归分支复用同一 definition root 的索引
    )

    # 当前目标聚合全部本地和跨层 producer。
    accumulator = TraceAccumulator(set())  # 当前作用域目标聚合器

    # 本地组合事实通过调用级索引保留精确匹配与静态位汇聚语义。
    tuple_local_facts = _local_facts_for_context(context, module)  # 当前 endpoint 的有序本地驱动事实

    # 每条本地驱动独立贡献 operation 和数据依赖。
    for dict_fact in tuple_local_facts:

        # 本地事实 tracing 使用已登记 visiting 的上下文。
        trace_result_result_fact = _trace_local_fact(context_entered, module, dict_fact)  # 当前本地驱动事实结果

        # 本地驱动的完整数据和控制锥并入当前端点。
        _merge_result(accumulator, trace_result_result_fact)

    # 非本地 producer 由专用 helper 处理，保持本函数聚焦目标级编排。
    _merge_endpoint_producers(context, context_entered, accumulator)

    # 当前模块已证明 present 时只在有本地事实的可达目标上合并。
    if tuple_local_facts and module.loop_presence == "present":

        # present 优先于其他 unknown 边界并由 VG147 独占。
        accumulator.loop_presence = merge_loop_presence(accumulator.loop_presence, "present")  # 本地循环事实证明含循环

    # 没有 present 但模块未知区域影响当前目标时保持 unknown。
    if accumulator.loop_presence != "present" and module.loop_presence == "unknown" and accumulator.reasons:

        # unknown 允许 VG146/VG147 同时保守评估已知下界。
        accumulator.loop_presence = "unknown"  # 当前目标受模块未知循环区域影响

    # 返回当前作用域目标完整聚合结果。
    return _freeze_accumulator(accumulator)
