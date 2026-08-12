"""定义组合操作锥分析的不可变中间结果。"""

# 延迟求值类型注解，保持运行时仅依赖标准库值对象。
from __future__ import annotations

# 组合锥结果只需要不可变值对象，不引入分析阶段副作用。
from dataclasses import dataclass, field

# Literal 把循环存在性限定为三态证据，避免布尔值吞掉未知状态。
from typing import Literal

# formatter 的一基源码范围是实现身份的定位权威源。
from .formatter_backend.models import SourceSpan

# 标准 VG 发现模型承载组合锥评估后的可报告证据。
from .vg_rule_models import VgFinding

# 循环存在性必须区分确定不存在、确定存在和局部未知。
LoopPresence = Literal["absent", "present", "unknown"]  # 材料化图中的循环存在性三态

# 模块实现身份由来源路径、模块名和定义范围共同确定。
@dataclass(frozen=True)
class ModuleImplementationIdentity:
    """保存一个 source module 定义的唯一身份。

    参数:
        relative_path: 模块定义所属 RTL 文件的相对路径。
        module_name: 当前 module 声明名称。
        definition_span: 当前定义在完整源码中的一基范围。
    """

    # 路径区分不同 RTL 文件中的同名 module 定义。
    relative_path: str  # 模块定义所属相对路径

    # 模块名用于引用方建立候选实现集合。
    module_name: str  # 当前 module 声明名称

    # 定义范围区分同文件中的重复声明并稳定排序。
    definition_span: SourceSpan  # 当前 module 定义源码范围

# 参数值同时保留数值和定宽有符号语义。
@dataclass(frozen=True)
class ParameterValue:
    """保存一次参数求值的确定值或局部未知原因。

    参数:
        name: parameter 或 localparam 名称。
        value: 可确定的整数值，未知时为 None。
        width: 声明或字面量推导的位宽，未知时为 None。
        signed: 有符号属性，未知时为 None。
        unknown_reason: 当前参数无法确定的精确原因。
    """

    # 名称是环境内按声明顺序查找参数的稳定键。
    name: str  # 参数或局部参数名称

    # 数值未知时保持 None，禁止默认替换为零。
    value: int | None  # 参数整数值

    # 位宽进入 specialization fingerprint，避免同值异宽合并。
    width: int | None  # 参数位宽

    # signed 属性进入 fingerprint，保持算术扩展语义。
    signed: bool | None  # 参数有符号属性

    # 原因文本只描述当前参数的局部求值缺口。
    unknown_reason: str = ""  # 参数未知原因

# 特化缓存键把 source implementation 与参数指纹绑定。
@dataclass(frozen=True)
class SpecializationKey:
    """保存不可变模块特化缓存键。

    参数:
        implementation: 被特化的 source module 实现身份。
        fingerprint: 包含值、位宽、符号和未知原因的稳定摘要。
    """

    # 实现身份阻止同名多定义共享特化缓存。
    implementation: ModuleImplementationIdentity  # 被特化的模块实现

    # 指纹只由确定序列化内容生成，不依赖 Python 随机哈希。
    fingerprint: str  # 完整参数语义序列的确定性摘要

# 默认 definition root 把每个 source 定义纳入独立分析入口。
@dataclass(frozen=True)
class DefinitionRoot:
    """保存一个 source definition 的默认特化入口。

    参数:
        identity: 当前 source module 实现身份。
        specialization: 当前实现默认参数环境的特化键。
    """

    # root 身份用于最终报告定位到具体定义。
    identity: ModuleImplementationIdentity  # 当前定义身份

    # 默认特化键用于复用已经物化的不可变模块图。
    specialization: SpecializationKey  # 当前定义默认环境键

# FrozenFact 是字典事实的递归不可变所有权边界。
@dataclass(frozen=True)
class FrozenFact:
    """保存按键排序的不可变事实字段。

    参数:
        fields: 字典序排列且值已递归冻结的键值元组。
    """

    # tuple-of-pairs 防止缓存值向调用方暴露可变字典。
    fields: tuple[tuple[str, object], ...]  # 排序后的不可变事实字段

# 单个 source module 定义只持有不可变 formatter 事实。
@dataclass(frozen=True)
class ModuleImplementation:
    """保存 source-only 模块实现及其材料化模板。

    参数:
        identity: 当前 source module 的唯一实现身份。
        ports: 按声明顺序冻结的端口事实。
        parameters: 按声明顺序冻结的公开参数事实。
        localparams: 按依赖顺序冻结的局部参数事实。
        functions: 当前模块本地函数定义事实。
        materialization_template: 生成特化图所需的冻结结构模板。
    """

    # identity 同时承担重复定义排序和引用歧义证据。
    identity: ModuleImplementationIdentity  # 当前模块实现身份

    # 端口次序支持 positional instance binding。
    ports: tuple[FrozenFact, ...]  # 当前模块端口事实

    # parameter 次序决定默认值和 positional override 求值顺序。
    parameters: tuple[FrozenFact, ...]  # 当前模块公开参数事实

    # localparam 在全部 parameter 完成后按声明顺序重算。
    localparams: tuple[FrozenFact, ...]  # 当前模块局部参数事实

    # 函数体随 specialization 一起物化，供调用点展开。
    functions: tuple[FrozenFact, ...]  # 当前模块本地函数事实

    # 模板包含组合表达式、generate、实例和存储驱动结构。
    materialization_template: FrozenFact  # 当前模块材料化模板

# 实现索引把 source definitions 与 external interfaces 分离。
@dataclass(frozen=True)
class ModuleImplementationIndex:
    """保存稳定排序的 source 实现和外部接口索引。

    参数:
        implementations: module 名到全部 source 实现的有序映射。
        external_interfaces: module 名到外部接口事实的有序映射。
    """

    # 重复定义保留在同名 tuple 中，禁止后写覆盖前写。
    implementations: tuple[tuple[str, tuple[ModuleImplementation, ...]], ...]  # source 实现索引

    # external interface 不进入可递归实现集合。
    external_interfaces: tuple[tuple[str, tuple[FrozenFact, ...]], ...]  # 外部接口索引

# 参数环境按声明顺序保存所有公开参数和局部参数。
@dataclass(frozen=True)
class ParameterEnvironment:
    """保存一次有序参数绑定结果及稳定指纹。

    参数:
        values: 按求值顺序排列的参数值元组。
        fingerprint: 对完整参数语义生成的确定性摘要。
    """

    # values 同时保留未知项，禁止缺失参数悄然消失。
    values: tuple[ParameterValue, ...]  # 有序参数求值结果

    # 指纹用于 SpecializationKey 和缓存复用。
    fingerprint: str  # 参数环境稳定指纹

# UnknownRegion 将不确定性限制在受影响目标集合。
@dataclass(frozen=True)
class UnknownRegion:
    """保存材料化阶段的局部不确定区域。

    参数:
        affected_targets: 读取当前未知结构的目标名称集合。
        reason: 当前区域无法完整材料化的精确原因。
        may_contain_loop: 未知区域是否可能包含运行期展开循环。
    """

    # 空 tuple 表示模块结构级未知，后续绑定层再细化目标。
    affected_targets: tuple[str, ...]  # 受未知结构影响的目标集合

    # 原因进入 inconclusive evidence，不用于控制流猜测。
    reason: str  # 局部不确定原因

    # 可能含循环时 VG147 必须保持 unknown 而非 absent。
    may_contain_loop: bool  # 未知区域是否可能包含循环

# SpecializedModule 是参数绑定后的递归冻结组合图。
@dataclass(frozen=True)
class SpecializedModule:
    """保存一个参数环境下物化完成的模块结构。

    参数:
        key: 当前实现与参数环境组成的特化缓存键。
        parameter_environment: 当前模块有序参数环境。
        ports: 已特化的端口事实。
        comb_expressions: 已选择生成分支并展开循环的组合事实。
        instances: 已选择生成分支并展开数组的实例事实。
        functions: 随参数环境物化的本地函数事实。
        storage_drivers: 在寄存器或锁存器处截断的驱动事实。
        loop_presence: 当前特化图的循环存在性三态。
        unknown_regions: 材料化过程中产生的局部未知区域。
    """

    # key 是缓存所有权和递归 visiting 检测的唯一依据。
    key: SpecializationKey  # 当前模块特化键

    # 环境保留完整指纹输入，便于诊断缓存差异。
    parameter_environment: ParameterEnvironment  # 当前模块参数环境

    # 端口事实冻结后可安全供多个调用路径复用。
    ports: tuple[FrozenFact, ...]  # 当前特化模块端口事实

    # 组合表达式只包含当前参数环境选中的结构。
    comb_expressions: tuple[FrozenFact, ...]  # 当前特化组合表达式事实

    # 实例列表包含 generate 和静态数组展开后的 occurrence。
    instances: tuple[FrozenFact, ...]  # 当前特化子模块实例事实

    # 函数事实与调用点共享同一参数环境。
    functions: tuple[FrozenFact, ...]  # 当前特化本地函数事实

    # 存储驱动明确形成组合锥传播切点。
    storage_drivers: tuple[FrozenFact, ...]  # 当前特化存储驱动事实

    # 三态循环证据避免 unknown 被错误归为普通组合路径。
    loop_presence: LoopPresence  # 当前特化图循环存在性

    # 未知区域只污染读取相关结构的后续目标。
    unknown_regions: tuple[UnknownRegion, ...]  # 当前特化局部未知区域

# 作用域目标把 definition root、实例路径和特化身份绑定到端点。
@dataclass(frozen=True)
class ScopedTarget:
    """保存 hierarchy graph 内唯一的静态目标身份。

    参数:
        root: 当前图所属的 source definition root 身份。
        instance_path: 从 root 到当前模块 occurrence 的实例路径。
        specialization: 当前路径节点对应的模块特化键。
        target: 当前模块内规范化的静态端点名称。
    """

    # root 防止不同顶层定义中的相同实例路径发生合并。
    root: ModuleImplementationIdentity  # 当前层级图的定义根身份

    # 每个实例数组或 generate occurrence 都必须保留在路径段中。
    instance_path: tuple[str, ...]  # 从 root 到当前模块的完整实例路径

    # 参数指纹区分同一路径下不同参数环境的模块结构。
    specialization: SpecializationKey  # 当前路径节点的特化身份

    # 端点保留常量位选或切片，动态选择不得伪装成静态目标。
    target: str  # 当前模块内静态端点

# 父子 binding 同时保存输入 actual 和逐位 output 映射。
@dataclass(frozen=True)
class HierarchyBinding:
    """保存一个实例 occurrence 的跨模块端口绑定。

    参数:
        parent: 父模块特化键。
        parent_path: 父模块 occurrence 的完整路径。
        child: 子模块特化键。
        child_path: 子模块 occurrence 的完整路径。
        child_output: 当前映射的子模块输出 formal。
        output_bit_map: 子输出位到父静态端点的有序映射。
        input_actuals: 子输入 formal 到父 actual 事实的有序绑定。
        source_span: 当前实例声明的一基源码范围。
    """

    # 父特化身份决定 actual 表达式的求值作用域。
    parent: SpecializationKey  # 父模块特化键

    # 父路径用于生成调用方作用域内的 producer 身份。
    parent_path: tuple[str, ...]  # 父模块完整实例路径

    # 子特化身份包含实例参数覆盖后的完整语义指纹。
    child: SpecializationKey  # 子模块特化键

    # 子路径编码实例名以及 generate 和数组 occurrence。
    child_path: tuple[str, ...]  # 子模块完整实例路径

    # 每条 binding 只描述一个 child output formal。
    child_output: str  # 被映射的子模块输出名称

    # 映射顺序严格遵循 child output 从高位到低位。
    output_bit_map: tuple[tuple[str, str], ...]  # 子输出位到父端点位的映射

    # 输入绑定按 child formal 声明顺序冻结，named 与 positional 结果一致。
    input_actuals: tuple[tuple[str, FrozenFact], ...]  # 子输入 formal 到 actual 事实

    # 实例范围定位混用、越界或不完整连接的局部证据。
    source_span: tuple[int, int, int, int]  # 实例声明源码范围

# 静态输出映射在成功位对与局部失败原因之间保持互斥合同。
@dataclass(frozen=True)
class StaticOutputMap:
    """保存 child output 到 parent actual 的静态逐位映射。

    参数:
        bit_pairs: 按 child 高位到低位排列的端点位对。
        unknown_reason: 当前 actual 无法静态映射的精确原因。
    """

    # 位对次序直接承载 Verilog 拼接从左到右的高低位语义。
    bit_pairs: tuple[tuple[str, str], ...]  # child 位标识到 parent 位端点映射

    # 空原因表示映射完整，非空原因只污染当前 output actual。
    unknown_reason: str = ""  # 输出连接局部未知原因

# 输出驱动分类决定跨层 tracing 是展开、截止还是局部 unknown。
OutputDriverClass = Literal[  # 跨层输出驱动的封闭分类集合
    "combinational",  # 可继续展开的纯组合输出
    "storage_q",  # 直接时序或锁存器输出切点
    "exact_q_bridge",  # 零操作直连内部 Q 的输出
    "mixed",  # 同一输出存在组合与存储混合驱动
    "unresolved_net_boundary",  # inout 或具有解析语义的网络边界
    "unknown",  # 当前输出无法可靠分类
]

# producer 引用把驱动类别、作用域端点和源码位置冻结为图边证据。
@dataclass(frozen=True)
class ProducerRef:
    """保存一个 hierarchy endpoint 的已知或未知生产者。

    参数:
        kind: continuous、instance_output、storage_q 或 unknown 等类别。
        scoped_target: 生产者所在模块作用域内的静态端点。
        source_span: 生产者事实的一基源码范围。
        unknown_reason: 当前生产者无法继续展开的局部原因。
    """

    # kind 供 tracing 区分本地表达式、子输出和边界切点。
    kind: str  # 当前生产者类别

    # 目标身份携带 definition root、路径和 specialization。
    scoped_target: ScopedTarget  # 生产者作用域端点

    # 源范围支持把跨层失败证据定位到实例或驱动事实。
    source_span: tuple[int, int, int, int]  # 当前生产者源码范围

    # unknown producer 与同端点已知 producer 并存，不覆盖已知证据。
    unknown_reason: str = ""  # 当前生产者局部未知原因

# hierarchy graph 返回前冻结模块节点、binding 和 endpoint driver 目录。
@dataclass(frozen=True)
class HierarchyGraph:
    """保存一个 definition root 的不可变跨模块绑定图。

    参数:
        root: 当前图的默认 source definition 入口。
        modules: 完整实例路径到已物化模块的稳定映射。
        bindings: 按父子路径和 output 名稳定排序的端口绑定。
        endpoint_drivers: 作用域目标到全部 producer 的稳定映射。
    """

    # root 决定所有 ScopedTarget 的顶层定义身份。
    root: DefinitionRoot  # 当前 hierarchy graph 的定义根

    # 模块路径包含 root 空路径和每个可唯一展开的子实例。
    modules: tuple[tuple[tuple[str, ...], SpecializedModule], ...]  # 路径到特化模块目录

    # binding 只包含输出可静态映射且方向合同允许的实例边。
    bindings: tuple[HierarchyBinding, ...]  # 父子模块端口绑定集合

    # 同一普通 net endpoint 可以保存多个已知或未知 producer。
    endpoint_drivers: tuple[tuple[ScopedTarget, tuple[ProducerRef, ...]], ...]  # 端点驱动目录

# freeze_fact 是缓存写入的唯一递归所有权入口。
def freeze_fact(value: object) -> object:
    """递归冻结 formatter 事实值。

    参数:
        value: dict、list、tuple、FrozenFact 或受支持标量。

    返回:
        不包含可变 dict/list 的递归冻结值。

    异常:
        TypeError: 值包含不受支持的可变对象或非标量类型。
    """

    # 已冻结事实可以安全复用同一不可变对象。
    if isinstance(value, FrozenFact):

        # 缓存输入已经拥有独立不可变所有权。
        return value

    # 字典按字符串键排序并递归冻结每个值。
    if isinstance(value, dict):

        # FrozenFact 明确标识 tuple-of-pairs 来源于映射而非序列。
        return FrozenFact(
            tuple(
                (str(key), freeze_fact(item))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            )
        )

    # 列表和元组都冻结成保持原顺序的 tuple。
    if isinstance(value, (list, tuple)):

        # 序列次序承载端口、参数、分支和操作数语义。
        return tuple(freeze_fact(item) for item in value)

    # 允许的标量集合与 JSON 事实边界一致。
    if value is None or isinstance(value, (str, int, bool)):

        # 标量不可变且无需复制。
        return value

    # 未知对象禁止进入跨特化共享缓存。
    raise TypeError(f"> ERR: [Python] unsupported frozen fact value: {type(value).__name__}")

# thaw_fact 只为一次求值建立防御性可变副本。
def thaw_fact(value: object) -> object:
    """把冻结事实恢复为单次求值副本。

    参数:
        value: freeze_fact 产生的递归冻结值。

    返回:
        与缓存断开引用的 dict/list 或原始标量副本。
    """

    # FrozenFact 恢复成新字典，禁止调用方接触缓存内部容器。
    if isinstance(value, FrozenFact):

        # 每次调用均递归创建新的键值容器。
        return {key: thaw_fact(item) for key, item in value.fields}

    # 冻结序列恢复成新列表以支持一次性求值变换。
    if isinstance(value, tuple):

        # 子元素继续递归复制潜在嵌套映射。
        return [thaw_fact(item) for item in value]

    # 标量不可变，可直接返回等价值。
    return value

# 目标级结果保留操作编号，便于失败证据追溯到真实语法节点。
@dataclass(frozen=True)
class CombTargetCone:
    """保存一个静态目标端点的组合操作集合与可判定性。

    参数:
        path: 目标所属 Verilog 文件的相对路径。
        module: 目标所属 module 名称。
        target: 去除静态位选后的目标信号名称。
        line: 目标驱动语句的最早源码行号。
        operation_ids: 可达真实操作语法节点的唯一编号集合。
        contains_for: 当前目标是否包含可展开 for 循环赋值。
        inconclusive_reasons: 无法确定完整操作锥的局部原因。
        definition_root: 当前 cone 所属 source definition root。
        instance_path: 从 definition root 到当前模块的完整 occurrence 路径。
        specialization_fingerprint: 当前模块参数特化指纹。
        loop_presence: 当前目标可达锥的循环存在性三态。
    """

    # 文件路径用于把门禁发现定位到具体 RTL 来源。
    path: str  # Verilog 文件相对路径

    # module 名称区分同一文件内可能重名的目标信号。
    module: str  # 目标所属 module

    # 目标名称是组合操作预算的独立核算端点。
    target: str  # 静态目标信号名称

    # 最早驱动行号为门禁报告提供稳定定位。
    line: int  # 目标驱动的最早源码行

    # 操作编号集合去重分支汇合时重复可达的同一语法节点。
    operation_ids: frozenset[str] = field(default_factory=frozenset)  # 可达操作编号集合

    # 循环归属决定由 VG146 还是 VG147 报告该目标。
    contains_for: bool = False  # 是否包含 for 展开赋值

    # 不确定原因与已确定的超限证据同时保留。
    inconclusive_reasons: tuple[str, ...] = field(default_factory=tuple)  # 当前目标无法闭合分析的原因集合

    # definition root 缺省为 None 以兼容旧 module-local 构造入口。
    definition_root: ModuleImplementationIdentity | None = None  # 当前 cone 的 source 定义根身份

    # 根模块路径同样显式包含 root module 名，便于跨层证据展示。
    instance_path: tuple[str, ...] = field(default_factory=tuple)  # 当前目标完整实例路径

    # 参数指纹区分同源码定义的不同特化 occurrence。
    specialization_fingerprint: str = ""  # 当前目标所属模块特化指纹

    # 三态循环归属允许 unknown 同时进入 VG146 与 VG147。
    loop_presence: LoopPresence = "absent"  # 当前目标可达锥循环存在性

    # 兼容字段与三态字段在冻结后保持单一语义。
    def __post_init__(self) -> None:
        """统一旧 contains_for 与新 loop_presence 字段。

        参数:
            self: 当前待冻结的组合锥结果。

        返回:
            无；仅通过 frozen dataclass 受控写入同步兼容字段。
        """

        # 旧调用方传入 contains_for=True 时提升为 present。
        if self.contains_for and self.loop_presence == "absent":

            # object.__setattr__ 是 frozen dataclass 初始化后的唯一同步入口。
            object.__setattr__(self, "loop_presence", "present")

        # 新调用方只在 present 状态下向旧接口暴露 contains_for=True。
        if self.loop_presence == "present" and not self.contains_for:

            # unknown 明确保留 False，防止 VG147 独占未知归属。
            object.__setattr__(self, "contains_for", True)

    # 属性统一计算集合基数，避免调用方重复实现计数语义。
    @property

    # 计数属性向 VG146/VG147 提供统一预算使用量。
    def operation_count(self) -> int:
        """返回当前目标可达的不同真实操作节点数。

        参数:
            self: 当前目标的不可变组合锥结果。

        返回:
            去重后的真实操作语法节点数量。
        """

        # 唯一编号集合的基数就是预算使用量。
        return len(self.operation_ids)

# 超限发现适配器把不可变组合锥转换成标准 VG 证据。
def build_over_limit_finding(
    obj_cone: CombTargetCone,
    int_limit: int,
) -> VgFinding:
    """构造包含计数、上限和时序化建议的超限证据。

    参数:
        obj_cone: 已确认超过预算的目标组合锥。
        int_limit: 目录配置允许的最大操作节点数。

    返回:
        可直接写入 VG 评估结果的失败发现。
    """

    # 建议优先打断组合路径，不鼓励仅做布尔表达式换写。
    str_message = (  # 面向 RTL 修改者的时序化建议
        "组合逻辑操作锥超过强预算；优先加入流水寄存器、注册标志或预译码，并将复杂 FSM 条件拆为多周期时序步骤。"
        "这些修改可能改变可见延迟；若协议延迟不可变化，必须阻断并进行人工架构审查。"
    )

    # 证据明确列出端点、真实操作数和生效预算。
    str_evidence = (  # 供测试和人工审查使用的稳定计数证据
        f"{obj_cone.module}.{obj_cone.target}: "
        f"{obj_cone.operation_count} operations, limit {int_limit}"
    )

    # 标准发现对象保留路径和最早驱动行号。
    return VgFinding(obj_cone.path, obj_cone.line, str_message, str_evidence)

# 不确定发现适配器禁止局部解析缺口被当作零操作放行。
def build_unknown_finding(obj_cone: CombTargetCone) -> VgFinding:
    """构造仅污染当前目标的不确定分析证据。

    参数:
        obj_cone: 含 formatter 局部不确定原因的目标组合锥。

    返回:
        可直接写入不确定 VG 评估的风险发现。
    """

    # 发现正文保持修改者可读，并把具体原因留在 evidence 字段。
    return VgFinding(
        obj_cone.path,
        obj_cone.line,
        "当前目标的组合操作锥包含 formatter 无法确定的结构，禁止按低计数放行。",
        f"{obj_cone.module}.{obj_cone.target}: "
        + "; ".join(obj_cone.inconclusive_reasons),
    )
