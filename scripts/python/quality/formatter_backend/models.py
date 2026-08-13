"""定义内置 Verilog formatter 管线共享的数据模型。"""

# 延迟注解求值，避免递归模型类型在导入阶段互相解析。
from __future__ import annotations

# dataclass 负责承载 formatter 各阶段的解析结果和版面决策。
from dataclasses import dataclass, field

# 统一异常类型让调用方区分 formatter 严格规则失败和普通运行异常。
class VerilogFormatterError(Exception):
    """表示源文件无法在严格 formatter 规则下完成处理。"""

# 参数声明模型保留原始声明信息和 formatter 补齐的展示元数据。
@dataclass
class ParamDecl:
    """描述 parameter 或 localparam 声明在参数块中的可渲染形态。"""

    # 关键字区分 parameter 与 localparam，影响声明行渲染前缀。
    keyword: str  # 参数声明关键字

    # 参数名用于排序、复用和最终输出声明左侧。
    name: str  # 参数标识符

    # 参数取值保留解析后的右侧表达式文本。
    value: str  # 参数表达式文本

    # 声明规格保存 signed、range 等关键字前后的附加片段。
    decl_spec: str = ""  # 参数声明附加规格

    # 行尾注释来自原始 RTL 或 formatter 推导的说明。
    comment: str = ""  # 参数行尾说明

    # 前导注释需要跟随参数移动，避免重排后语义丢失。
    leading_comments: list[str] = field(default_factory=list)  # 参数前导注释行

    # 原文片段用于保底回退和调试解析差异。
    raw_text: str = ""  # 参数声明原始文本

    # 合成标志区分 formatter 补齐项和用户源码中的真实声明。
    synthetic: bool = False  # 参数是否由 formatter 合成

# 参数簇模型把同一展示小节内的参数收拢在一起。
@dataclass
class ParamRenderCluster:
    """记录参数渲染小节及其来源。"""

    # 参数列表保持该小节内的最终渲染顺序。
    params: list[ParamDecl] = field(default_factory=list)  # 小节内参数声明

    # 小节标签会显示为参数块中的中文或领域标题。
    label: str = ""  # 参数小节标题

    # 来源标识说明该小节来自配置前缀、原注释或兜底聚类。
    source: str = "none"  # 参数小节来源

# 端口声明模型保存端口声明行和 formatter 分组所需的元数据。
@dataclass
class PortDecl:
    """描述 module 端口声明及其协议分组信息。"""

    # 端口方向决定 input、output、inout 的基础排序层级。
    direction: str  # 端口方向

    # 位宽文本保留原始 range，避免格式化改变总线语义。
    width: str  # 端口位宽表达式

    # 端口名用于协议识别、连接生成和最终声明输出。
    name: str  # 端口标识符

    # 行尾注释记录用户原有说明或 formatter 补充说明。
    comment: str = ""  # 端口行尾说明

    # 顶层分组承载 AXI、SPI、UART 等协议族归类。
    group: str = ""  # 端口协议分组

    # 小节名称对应端口块内的中文标题。
    section: str = ""  # 端口展示小节

    # signed 标记影响声明关键字顺序和位宽解释。
    signed: bool = False  # 端口是否带 signed

    # unpacked 维度保留数组端口的声明后缀。
    unpacked: str = ""  # 端口 unpacked 维度

    # attributes 保存 Verilog 属性前缀，渲染时必须贴近声明。
    attributes: str = ""  # 端口属性文本

    # 原始端口文本用于无法安全重排时回退。
    raw_text: str = ""  # 端口声明原文

    # 合成端口用于 formatter 补齐缺失声明时与源码端口区分。
    synthetic: bool = False  # 端口是否由 formatter 合成

    # 子分组进一步细分同一小节中的信号族。
    subgroup: str = ""  # 端口子分组标签

    # 子分组模式控制 section 与 subgroup 的优先展示关系。
    subgroup_mode: str = "section_first"  # 子分组展示策略

    # 通用子分组开关避免未知协议被过度切分。
    allow_generic_subgroup: bool = True  # 是否允许通用子分组

# 端口版面信息把排序权重与渲染标签集中为不可变结构。
@dataclass(frozen=True)
class PortLayoutInfo:
    """保存端口声明在 formatter 输出中的排序与分区结果。"""

    # 分组名称决定布局排序器把端口放入哪个接口区域。
    group: str = ""  # 布局接口区域名

    # 小节名称对应最终输出中的端口标题。
    section: str = ""  # 端口小节标签

    # 小节权重确保协议通道按固定阅读顺序排列。
    section_rank: int = 0  # 小节排序权重

    # 方向权重让 input、output、inout 在同组内稳定排序。
    direction_rank: int = 0  # 端口方向权重

    # 成员权重描述同一通道内的信号优先级。
    member_rank: int = 0  # 端口成员排序权重

    # 来源种类标记用户端口、推导端口或兜底端口。
    kind: str = "user"  # 端口布局来源类别

# 输出信号布局记录 output 端口转内部信号时需要继承的声明形态。
@dataclass(frozen=True)
class OutputSignalLayout:
    """描述 output 端口派生内部信号时沿用的声明布局。"""

    # 端口排序权重让派生信号跟随原端口的阅读位置。
    port_rank: int = 0  # 原端口排序权重

    # 协议分组用于把派生 signal 放回对应区域。
    group: str = ""  # 输出端口协议分组

    # 子分组保留 AXIS m/s、UART tx/rx 一类更细的接口槽位标签。
    subgroup: str = ""  # 输出端口协议子分组

    # 子分组模式决定 subgroup 是否需要先转换成协议槽位横幅标题。
    subgroup_mode: str = "section_first"  # 输出端口子分组展示策略

    # 小节标签保持 output 信号与端口块的通道一致。
    section: str = ""  # 输出信号小节标签

    # 位宽文本直接复用端口声明的 range。
    width: str = ""  # 输出信号位宽

    # signed 标记保证内部信号与端口算术语义一致。
    signed: bool = False  # 输出信号是否 signed

    # unpacked 维度保留数组端口的信号声明后缀。
    unpacked: str = ""  # 输出信号 unpacked 维度

    # 属性文本需要在派生信号声明中继续贴近标识符。
    attributes: str = ""  # 输出信号属性文本

# assign 源端布局用于把连续赋值语句放入合适的小节。
@dataclass(frozen=True)
class AssignSourceLayout:
    """记录 assign 语句左值关联端口后的展示位置。"""

    # 协议分组让 assign 语句跟随相关端口区域。
    group: str = ""  # assign 关联协议分组

    # 子分组用于细分同一协议区中的信号族。
    subgroup: str = ""  # assign 关联子分组

    # 小节标签决定 assign 输出前的注释标题。
    section: str = ""  # assign 所属小节

    # 端口权重让 assign 语句贴近对应端口顺序。
    port_rank: int = 0  # 关联端口排序权重

# 实例信号布局描述模块例化关联信号的版面位置。
@dataclass(frozen=True)
class InstanceSignalLayout:
    """保存实例连接信号在声明区中的排序信息。"""

    # 模块名用于区分不同子模块实例产生的连接信号。
    module_name: str = ""  # 子模块名称

    # 协议分组来自连接端口的接口语义。
    group: str = ""  # 实例信号协议分组

    # 小节标签把实例信号放入对应声明区域。
    section: str = ""  # 实例信号小节

    # 实例权重保持多个 module instance 的原始出现顺序。
    instance_rank: int = 0  # 实例出现顺序

    # 关联权重保持同一实例端口连接的顺序。
    association_rank: int = 0  # 端口关联顺序

    # 声明索引用于最终稳定排序时打破同权重并列。
    decl_index: int = 0  # 信号声明原始索引

# signal 声明模型覆盖 wire、reg、logic 等内部信号声明。
@dataclass
class SignalDecl:
    """描述内部信号声明及其声明修饰信息。"""

    # 声明类型保存 wire、reg、logic 等 Verilog 关键字。
    kind: str  # 信号声明类型

    # 位宽文本保留 packed range，避免 formatter 改写总线宽度。
    width: str  # 信号位宽表达式

    # 信号名是排序、连接和重名检查的核心标识。
    name: str  # 信号标识符

    # 初始值保留声明中的赋值表达式。
    init: str = ""  # 信号声明初始值

    # 行尾注释展示信号用途或原始说明。
    comment: str = ""  # 信号行尾说明

    # signed 标记影响算术表达式和声明关键字。
    signed: bool = False  # 信号是否 signed

    # unpacked 维度保存数组式信号声明后缀。
    unpacked: str = ""  # 信号 unpacked 维度

    # 属性文本需要原样贴回信号声明前缀。
    attributes: str = ""  # 信号属性文本

    # 后缀承载声明末尾无法归入标准字段的片段。
    suffix: str = ""  # 信号声明保留后缀

    # 前导注释跟随信号移动，保护用户说明。
    leading_comments: list[str] = field(default_factory=list)  # 信号前导注释行

# 连续赋值模型保留 assign 左右两侧表达式和伴随注释。
@dataclass
class AssignStmt:
    """表示一条连续赋值语句的可渲染内容。"""

    # 左值用于排序、依赖分析和声明区关联。
    lhs: str  # assign 左侧表达式

    # 右值保留原始组合表达式文本。
    rhs: str  # assign 右侧表达式

    # 行尾注释承载原始说明或 formatter 添加的意图说明。
    comment: str = ""  # assign 行尾说明

    # 前导注释需要随 assign 语句整体移动。
    leading_comments: list[str] = field(default_factory=list)  # assign 前导注释行

    # 延迟片段保留 Verilog assign 中的时序延迟写法。
    delay: str = ""  # assign 延迟修饰

# body block 模型是 parser 与 renderer 之间的通用块载体。
@dataclass
class BodyBlock:
    """承载 module body 中不同语义块的统一容器。"""

    # 块类型选择 signal、assign、always、instance 等 renderer 分支。
    block_type: str  # renderer 分发块类型

    # 来源标签区分 parser 识别、formatter 合成和原文兜底片段。
    source: str  # parser 到 renderer 的来源标签

    # 载荷保存具体块模型，类型由 block_type 决定。
    payload: object | None = None  # body 块具体载荷

    # 目标信号列表用于调度 always/assign 与声明区域关联。
    targets: list[str] = field(default_factory=list)  # body 块写入目标

    # 触发类型描述 always 块或过程块的时序属性。
    trigger_kind: str = "unknown"  # body 块触发类别

    # 状态引用标记帮助 renderer 区分组合逻辑和状态逻辑。
    references_state: bool = False  # body 块是否引用状态

# 左值引用模型用于展开复杂赋值目标。
@dataclass
class LValueRef:
    """记录过程赋值左值的基础名、结构类型和成员关系。"""

    # 完整文本保留切片、拼接或成员访问写法。
    text: str  # 左值完整文本

    # 基础名用于与 signal 声明和端口声明建立关联。
    base: str  # 左值基础标识符

    # 左值类型区分 simple、concat、index 等解析形态。
    kind: str = "simple"  # 左值结构类型

    # 复杂标志帮助 renderer 判断是否需要谨慎重排。
    is_complex: bool = False  # 左值是否包含复杂结构

    # 成员列表保存拼接或层级左值中的子目标。
    members: list["LValueRef"] = field(default_factory=list)  # 左值子成员

# case item 模型保存 case 分支标签及其内部控制节点。
@dataclass
class CaseItem:
    """描述 case 语句中的单个分支。"""

    # 标签文本对应 case item 的匹配表达式。
    label: str  # case 分支标签

    # 子节点保留该分支内部的控制流或语句树。
    children: list["ControlNode"] = field(default_factory=list)  # case 分支子节点

    # 块标签记录 begin/end 命名块或 formatter 推导的小节名。
    block_label: str = ""  # case 分支块标签

    # 前导注释属于当前 case 分支，不能在控制树重建时丢失。
    leading_comments: list[str] = field(default_factory=list)  # case 分支标签前的语义注释

# 控制节点模型表示 always、initial、generate 内部的控制流树。
@dataclass
class ControlNode:
    """描述过程块内部控制结构或普通语句节点。"""

    # 节点类型区分 if、case、stmt、block 等控制形态。
    kind: str  # 控制节点类型

    # 头部文本保存 if/case/for 等语句的声明行。
    header: str = ""  # 控制节点头部文本

    # 普通语句文本用于无子节点的叶子节点渲染。
    text: str = ""  # 控制节点语句文本

    # 标签记录命名块或 formatter 推导的展示名称。
    label: str = ""  # 控制节点展示标签

    # 子节点保存主分支或顺序块内部内容。
    children: list["ControlNode"] = field(default_factory=list)  # 主路径子节点

    # alternate 保存 else/默认路径中的控制节点。
    alternate: list["ControlNode"] = field(default_factory=list)  # 备选路径子节点

    # case items 保存 case 结构的所有分支。
    items: list[CaseItem] = field(default_factory=list)  # case 分支集合

# always block 模型聚合过程块头、目标信号和解析出的控制树。
@dataclass
class AlwaysBlock:
    """保存 always 过程块的原文、时序属性和控制流解析结果。"""

    # 头部文本保留 sensitivity list 或 always_comb 等声明。
    header: str  # always 块头部文本

    # 行列表保存无法完全结构化时的原始过程块内容。
    lines: list[str]  # always 块原始行

    # 写入目标帮助过程块与 signal 声明、状态依赖检查建立关系。
    targets: list[str]  # always 过程赋值目标集合

    # clock 保存时序过程块推导出的主时钟信号。
    clock: str = ""  # always 块时钟信号

    # reset 保存时序过程块推导出的复位信号。
    reset: str = ""  # always 块复位信号

    # 触发类别来自 sensitivity list，用于判定组合逻辑或时序逻辑。
    trigger_kind: str = "unknown"  # always 时序触发归类

    # 组合标志帮助 formatter 判断是否能安全调整语句位置。
    is_combinational: bool = False  # always 块是否为组合逻辑

    # case 标志用于渲染时选择 case 结构处理路径。
    contains_case: bool = False  # always 块是否包含 case

    # 条件分支存在标记会影响过程块缩进和控制树重建策略。
    contains_if: bool = False  # always 条件分支存在标记

    # 状态读取关系用于避免把寄存器依赖逻辑误判为纯组合片段。
    references_state: bool = False  # always 状态读取关系

    # 块种类用于把 main task、辅助过程和初始化逻辑分组。
    block_kind: str = "main_task"  # always 块业务类别

    # 控制节点保存解析后的过程块结构。
    nodes: list[ControlNode] = field(default_factory=list)  # always 控制节点树

    # 左值列表保存过程块内所有写入目标的结构化引用。
    lvalues: list[LValueRef] = field(default_factory=list)  # always 左值引用

    # 复杂左值标志提示 renderer 保守处理拼接或切片赋值。
    has_complex_lvalues: bool = False  # always 是否含复杂左值

    # 过程块前的用户说明在重排后仍需贴回同一 always 区域。
    leading_comments: list[str] = field(default_factory=list)  # always 区域前置说明行

# 源位置模型为跨层事实提供一基、闭区间位置合同。
@dataclass(frozen=True)
class SourceSpan:
    """描述 formatter 事实在源文件中的一基起止位置。"""

    # 起始行定位事实所属的完整源文件行。
    line_start: int  # 一基起始行号

    # 起始列区分同一行中的多个语法 occurrence。
    column_start: int  # 一基起始列号

    # 结束行覆盖跨行实例或函数定义的完整范围。
    line_end: int  # 一基结束行号

    # 结束列闭合当前事实最后一个源码字符。
    column_end: int  # 一基结束列号

# 表达式节点模型冻结 formatter typed tree，供后续组合锥直接消费。
@dataclass(frozen=True)
class ExpressionNodeFact:
    """保存一个不可变、可递归序列化的表达式节点事实。"""

    # 节点类别区分运算、标识符、常量、选择和函数调用。
    node_kind: str  # formatter typed tree 节点类别

    # 文本保留 actual 根表达式或叶节点的源码含义。
    text: str  # 当前节点表达式文本

    # 运算符用于识别真实逻辑操作或函数 callee。
    operator: str  # 当前节点操作符文本

    # 操作类别把零成本 marker 与真实组合操作分开。
    operation_kind: str  # 冻结后的操作目录类别

    # 出现身份确保相同文本的不同源码位置不会合并。
    occurrence_id: str  # source-local 节点身份

    # 源位置让诊断能够回贴到 actual 的真实列范围。
    span: SourceSpan  # 当前节点完整源位置

    # 完整标志区分 report 权威路径与 legacy renderer 路径。
    span_complete: bool  # 源位置是否权威完整

    # 引用集合按源码首次出现顺序保存上游信号。
    references: tuple[str, ...] = ()  # 当前子树标识符引用

    # 子节点递归表达运算操作数和函数 actual。
    children: tuple["ExpressionNodeFact", ...] = ()  # 有序表达式子树

    # 附加属性冻结 select、call 等节点的专用元数据。
    attributes: tuple[tuple[str, object], ...] = ()  # 冻结节点扩展属性

    # 不支持原因只污染当前节点，不阻断整个 module。
    unsupported_reason: str = ""  # 局部类型化失败原因

# actual 构建上下文集中承载位置完整性和编号前缀。
@dataclass(frozen=True)
class ExpressionFactContext:
    """提供 actual 表达式的位置和 occurrence 身份上下文。"""

    # actual 的完整范围是内部节点定位的权威边界。
    span: SourceSpan  # 实际参数在完整源码中的行列区间

    # legacy 调用缺少完整文件上下文时保持 False。
    span_complete: bool  # actual span 是否完整

    # 编号前缀组合定义、调用点、关联位置和 actual span。
    occurrence_prefix: str  # 节点身份前缀

# actual 模型统一实例端口、参数覆盖和函数实参的表达式事实。
@dataclass(frozen=True)
class InstanceActualFact:
    """描述实例或函数调用中的一个 actual。"""

    # 原文必须原样保留，避免 enrichment 改变 formatter 输出。
    text: str  # actual 原始表达式文本

    # 类别区分端口、参数、函数实参或显式未连接。
    kind: str  # actual 业务类别

    # 默认位置只服务无权威上下文的兼容调用。
    span: SourceSpan = SourceSpan(1, 1, 1, 1)  # 兼容路径默认实际参数行列区间

    # report 主路径为 True，renderer/rename 兼容路径为 False。
    span_complete: bool = False  # 实际参数行列证据是否完备

    # 引用用于把调用方信号绑定到被调用模块 formal。
    references: tuple[str, ...] = ()  # actual 标识符引用

    # 静态段只记录 elaboration 后可确定的端点。
    static_lvalue_segments: tuple[dict[str, object], ...] = ()  # 静态端点片段

    # 表达式树复用 formatter parser，不在 VG 层重新解析。
    expression: ExpressionNodeFact | None = None  # 实际参数递归类型表达式树

    # 动态选择或未支持语法保留局部失败原因。
    unsupported_reason: str = ""  # actual 局部不支持原因

# 实例关联模型保留 formal、位置以及 actual 的绑定关系。
@dataclass(frozen=True)
class InstanceAssociation:
    """保存一个有序实例参数或端口关联。"""

    # named 关联保存 formal，positional 关联保持空字符串。
    formal_name: str  # 被调用模块 formal 名称

    # 位置在 named 和 positional 两种形式中都保持声明顺序。
    position: int  # 当前关联零基位置

    # actual 承载表达式、引用和局部失败信息。
    actual: InstanceActualFact  # 当前关联实际值

    # 空括号需要区别于缺少关联条目。
    explicit_unconnected: bool  # 是否显式空连接

    # 关联范围当前与 actual 范围对齐，供诊断定位。
    span: SourceSpan  # 当前关联源位置

    # 完整性沿用实例 block 的权威上下文状态。
    span_complete: bool = False  # 关联 span 完整性

# 函数 formal 模型记录声明顺序、方向和宽度文本。
@dataclass(frozen=True)
class FunctionFormalFact:
    """保存 Verilog function 的一个位置 formal。"""

    # 名称用于 function actual 的位置绑定结果展示。
    name: str  # 函数形参标识符

    # 位置严格沿用声明顺序，不按名称排序。
    position: int  # formal 零基位置

    # Verilog-2001 function formal 当前只接受 input。
    direction: str  # 函数形参输入方向

    # 宽度保留 signed 和 range 的声明文本。
    width_text: str  # formal 位宽文本

    # 声明位置用于后续将 formal 诊断回贴源码。
    span: SourceSpan  # 函数形参声明行列区间

# 函数定义事实由唯一 formatter function block parser 产生。
@dataclass(frozen=True)
class FunctionDefinitionFact:
    """保存 formatter 解析出的 function 定义事实。"""

    # 定义名同时是 Verilog function 的隐式返回目标。
    name: str  # function 定义名称

    # formal 元组保留 parser 捕获的声明顺序。
    formals: tuple[FunctionFormalFact, ...]  # 有序 formal 集合

    # 返回目标供函数体表达式绑定到调用点。
    return_target: str  # function 返回信号名

    # 函数体表达式保存 formatter 分离出的返回赋值事实。
    body_expressions: tuple[dict[str, object], ...]  # 函数体表达式事实

    # 定义范围覆盖 function 到 endfunction。
    span: SourceSpan  # function 定义源位置

    # 完整标志决定函数体是否可以进入跨层 tracing。
    parse_complete: bool  # function 定义是否完整

    # 递归或不支持声明只在当前 function 内标记。
    unsupported_reason: str = ""  # 函数定义无法完整展开的局部原因

# 函数调用事实保存 callee、位置 actual 和调用点身份。
@dataclass(frozen=True)
class FunctionCallFact:
    """保存 formatter 表达式树中的 function 调用事实。"""

    # callee 与同 module function definition 建立绑定。
    callee: str  # 被调用 function 名称

    # actual 元组严格按照调用表达式位置排列。
    actuals: tuple[InstanceActualFact, ...]  # 有序函数实参

    # 调用点范围区分同行同文本的多个 function call。
    call_site_span: SourceSpan  # function 调用源位置

    # 完整标志控制递归展开和不确定证据传播。
    parse_complete: bool  # 函数调用事实是否可完整绑定

    # 递归边或 actual 失败原因保持调用点局部化。
    unsupported_reason: str = ""  # function call 局部原因

# 实例块模型保存 module instantiation 原文和连接摘要。
@dataclass
class InstanceBlock:
    """描述一个子模块例化块及其基础识别信息。"""

    # 例化原文保留参数覆盖和端口连接的完整文本。
    text: str  # 实例块原始文本

    # 模块名用于按子模块类型归组连接信号。
    module_name: str = ""  # 被例化模块名

    # 实例名用于稳定排序和诊断输出。
    instance_name: str = ""  # 子模块实例名

    # 参数标志说明该实例是否包含参数覆盖列表。
    has_params: bool = False  # 实例是否带参数覆盖

    # 前导注释保留实例块之前的用户说明。
    leading_comments: list[str] = field(default_factory=list)  # 实例前导注释行

    # 默认位置让旧 renderer/rename 单参数调用保持有效。
    span: SourceSpan = SourceSpan(1, 1, 1, 1)  # 实例块源位置

    # report 主路径才提供可进入 gate 的权威位置。
    span_complete: bool = False  # 实例 span 完整性

    # 关联风格区分 named、positional、mixed 和 empty。
    association_style: str = ""  # 实例关联风格

    # 端口关联按实例声明顺序保存。
    port_associations: tuple[InstanceAssociation, ...] = ()  # 实例端口关联

    # 参数覆盖独立于端口 formal 绑定保存。
    parameter_overrides: tuple[InstanceAssociation, ...] = ()  # 实例参数覆盖

    # 数组范围保留静态 instance array 的原始声明。
    array_range_text: str = ""  # 实例数组范围文本

    # 完整解析要求括号、风格和 formal 唯一性均有效。
    parse_complete: bool = False  # 实例关联是否完整

    # 不支持原因只影响当前实例，不清空其他 module 事实。
    unsupported_reason: str = ""  # 实例局部失败原因

# generate block 模型承载 generate 区域的原文和控制树。
@dataclass
class GenerateBlock:
    """保存 generate 区域的声明头、原始行和解析节点。"""

    # 头部文本保留 generate 关键字附近的工具特定写法。
    header: str = "generate"  # generate 区域声明头

    # 原始行承载 parser 未拆开的 generate 子语句，渲染时可原样回放。
    lines: list[str] = field(default_factory=list)  # generate 保底回放源码行

    # 控制节点保存 for-generate、if-generate 等结构。
    nodes: list[ControlNode] = field(default_factory=list)  # generate 控制节点

    # 区域前说明通常解释参数化硬件结构，必须随 generate 保留。
    leading_comments: list[str] = field(default_factory=list)  # generate 区域说明行

# initial block 模型保存仿真初始化或寄存器初始化语句。
@dataclass
class InitialBlock:
    """描述 initial 过程块的原文和可选控制树。"""

    # 头部文本保留 initial begin 的展开形式或用户原始写法。
    header: str = "initial begin"  # initial 入口声明行

    # 原始行保存仿真初值、显示语句或寄存器初始化片段。
    lines: list[str] = field(default_factory=list)  # initial 初始化源码行

    # 控制节点表达 initial 内部可解析的条件或顺序结构。
    nodes: list[ControlNode] = field(default_factory=list)  # initial 内部控制树

    # 初始化说明常解释仿真夹具意图，重排时需要跟随该块。
    leading_comments: list[str] = field(default_factory=list)  # initial 初始化说明行

    # 块种类让 renderer 使用独立于 always 的初始化处理分支。
    block_kind: str = "initial_block"  # initial 渲染分类标记

# function block 模型保存 Verilog function 的原始行。
@dataclass
class FunctionBlock:
    """保存 Verilog function 块的文本与前导注释。"""

    # 原始行保持函数端口、局部声明和返回赋值的连续文本。
    lines: list[str] = field(default_factory=list)  # function 完整源码行

    # 函数说明通常描述组合辅助逻辑，必须随 function 一起移动。
    leading_comments: list[str] = field(default_factory=list)  # function 语义说明行

    # definition 由 function block parser 产生，VG 层只消费结构化事实。
    definition: FunctionDefinitionFact | None = None  # 结构化函数定义事实

# task block 模型保存 Verilog task 的调用式过程内容。
@dataclass
class TaskBlock:
    """保存 Verilog task 块的文本与前导注释。"""

    # 原始行保留任务端口、顺序语句和结束边界。
    lines: list[str] = field(default_factory=list)  # task 过程源码行

    # 任务前说明常描述复用过程意图，渲染时需绑定 task。
    leading_comments: list[str] = field(default_factory=list)  # task 过程说明行

# raw block 模型用于保留 formatter 尚未识别的 module body 片段。
@dataclass
class RawBlock:
    """承载需要原样保留的 module body 文本块。"""

    # 原始行直接写回输出，避免 formatter 误改未知语法。
    lines: list[str] = field(default_factory=list)  # 原样保留的源码行

    # 未识别区域前的说明不能脱离原文，否则保底回放会失去上下文。
    leading_comments: list[str] = field(default_factory=list)  # raw block 上下文说明

# 预处理条件模型保留 `ifdef/`else 两侧解析出的条目。
@dataclass
class PreprocessorConditional:
    """描述 Verilog 预处理条件块及其真假分支内容。"""

    # 指令文本保存 `ifdef、`ifndef 等条件类型。
    directive: str  # 预处理条件指令

    # 符号名是条件编译的宏开关。
    symbol: str  # 条件宏符号

    # 真分支条目按声明类别分桶保存。
    true_items: dict[str, list]  # 条件成立分支内容

    # 假分支条目保存 `else 后的可渲染内容。
    false_items: dict[str, list] = field(default_factory=dict)  # 条件不成立分支内容

    # 前导注释保留预处理块之前的上下文说明。
    leading_comments: list[str] = field(default_factory=list)  # 预处理块前导注释行

    # else 标记区分显式空分支和没有 else 的条件块。
    has_else: bool = False  # 预处理块是否包含 else

# 文件头元数据模型对应 Xilinx/Vivado 风格注释头字段。
@dataclass
class HeaderMetadata:
    """保存 RTL 文件头注释中可识别的工程元数据。"""

    # 公司字段对应 Vivado 模板头部的 Company 项。
    company: str = ""  # 文件头公司名称

    # 工程师字段记录原始文件作者或维护者。
    engineer: str = ""  # 文件头工程师名称

    # 创建日期来自模板头的 Create Date 项。
    create_date: str = ""  # 文件创建日期文本

    # 修订日期保留最近修改时间说明。
    revision_date: str = ""  # 文件修订日期文本

    # 设计名字段用于生成或保留模块级设计说明。
    design_name: str = ""  # 文件头设计名称

    # 模块名字段对应当前 RTL module 名称。
    module_name: str = ""  # 文件头模块名称

    # 描述字段保存头部说明段的主体文本。
    description: str = ""  # 文件头设计描述

    # 仿真字段记录 testbench 或验证环境说明。
    simulations: str = ""  # 文件头仿真说明

    # 参考字段保存外部文档、IP 或规范链接说明。
    references: str = ""  # 文件头参考资料

    # 多行参考资料表保留 table_mode 下的列头和数据行。
    reference_lines: list[str] = field(default_factory=list)  # 文件头参考资料表格行

    # 版本字段保留用户维护的 RTL 版本号。
    version: str = ""  # 文件头版本文本

    # 依赖行保存 Dependencies 段落的原始多行内容。
    dependency_lines: list[str] = field(default_factory=list)  # 文件头依赖说明行

    # 历史行保存未拆分语言的修订记录。
    history_lines: list[str] = field(default_factory=list)  # 文件头修订历史行

    # 英文历史行用于双语文件头的英文记录区域。
    history_lines_en: list[str] = field(default_factory=list)  # 英文修订历史行

    # 中文历史行用于双语文件头的中文记录区域。
    history_lines_cn: list[str] = field(default_factory=list)  # 中文修订历史行

    # 额外行保留当前解析器无法归入固定字段的头部内容。
    extra_lines: list[str] = field(default_factory=list)  # 文件头额外保留行

    # 模块功能中文说明位于 header 之后、module 之前，需独立保真。
    module_purpose_comment: str = ""  # 模块功能中文说明

# 显式导出清单稳定 formatter backend 其它模块的导入边界。
__all__ = [  # formatter_backend.models 对外类型名
    "VerilogFormatterError",  # formatter 严格规则异常类型
    "ParamDecl",  # 单个参数声明模型
    "ParamRenderCluster",  # 参数渲染小节模型
    "PortDecl",  # 端口声明模型
    "PortLayoutInfo",  # 端口版面排序模型
    "OutputSignalLayout",  # output 派生信号布局模型
    "AssignSourceLayout",  # assign 关联版面模型
    "InstanceSignalLayout",  # 实例连接信号布局模型
    "SignalDecl",  # 内部信号声明模型
    "AssignStmt",  # 连续赋值语句模型
    "BodyBlock",  # module body 通用块模型
    "LValueRef",  # 过程赋值左值引用模型
    "CaseItem",  # case 分支模型
    "ControlNode",  # 过程控制树节点模型
    "AlwaysBlock",  # always 过程块模型
    "InstanceBlock",  # 子模块例化块模型
    "GenerateBlock",  # generate 区域模型
    "InitialBlock",  # initial 初始化区域类型
    "FunctionBlock",  # function 保留块类型
    "TaskBlock",  # task 过程保留块类型
    "RawBlock",  # 原样保留文本块模型
    "PreprocessorConditional",  # 预处理条件块模型
    "HeaderMetadata",  # 文件头元数据模型
]
