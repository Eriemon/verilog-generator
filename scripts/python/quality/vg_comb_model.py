"""定义组合操作锥分析的不可变中间结果。"""

# 延迟求值类型注解，保持运行时仅依赖标准库值对象。
from __future__ import annotations

# 组合锥结果只需要不可变值对象，不引入分析阶段副作用。
from dataclasses import dataclass, field

# 标准 VG 发现模型承载组合锥评估后的可报告证据。
from .vg_rule_models import VgFinding

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
    inconclusive_reasons: tuple[str, ...] = field(default_factory=tuple)  # 局部不确定原因

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
