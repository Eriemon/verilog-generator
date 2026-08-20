"""提供 WaveJSON lane 的字段解析和递归收集辅助。"""

# 标准库负责时间数值校验和 lane 数据模型。
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

# WaveformLane 保存一条 signal 的时间参数和归一化映射。
@dataclass
class WaveformLane:
    """描述一条 WaveJSON signal lane 的时间边界。"""

    # lane 的结构路径供错误定位和结果索引使用。
    path: str  # 当前 lane 的结构路径

    # 归一化副本中的 signal 映射由对齐器追加尾部字符。
    signal: dict[str, Any]  # 当前 lane 的可变 signal 映射

    # 周期倍率决定结束差值可以补充多少字符。
    period: int  # 当前 lane 的正整数周期倍率

    # 相位保留 WaveJSON 声明的水平起点。
    phase: Decimal  # 当前 lane 的水平起始相位

    # 字符跨度用于换算 lane 的有效结束时间。
    character_count: int  # 当前 lane 占用的字符周期数

# 解析有限时间数值，避免浮点误差破坏整周期补齐。
def finite_decimal(obj_numeric_value: Any, str_field_path: str) -> Decimal:
    """返回有限 Decimal 时间数值。

    :param obj_numeric_value: 待解析的数字或数字文本。
    :param str_field_path: 数值所在的配置或 WaveJSON 路径。
    :return: 可比较的有限 Decimal，unit=WaveDrom time slot。
    :raises ValueError: 输入不是有限数字时抛出。
    """

    # 布尔值不能作为时间数值，避免被解释成零或一。
    if isinstance(obj_numeric_value, bool):

        # 错误绑定到实际字段，阻止配置错误进入几何计算。
        raise ValueError(f"> ERR: [Python] {str_field_path} must be a finite number.")

    # Decimal 从字符串构造，避免二进制浮点舍入影响周期判断。
    try:

        # 解析整数、浮点数和 JSON 数字文本。
        decimal_value = Decimal(str(obj_numeric_value))  # 当前字段的精确数值

    # 非数字文本不能参与时间轴排序。
    except (InvalidOperation, ValueError):

        # 统一为带字段路径的输入合同错误。
        raise ValueError(f"> ERR: [Python] {str_field_path} must be a finite number.") from None

    # NaN 和无穷值无法形成可审计的结束边界。
    if not decimal_value.is_finite():

        # 拒绝不可排序或不可序列化的时间值。
        raise ValueError(f"> ERR: [Python] {str_field_path} must be a finite number.")

    # 返回经过有限性校验的 Decimal。
    return decimal_value

# 读取一条 lane 的正整数 period。
def lane_period(dict_signal: Mapping[str, Any], str_field_path: str) -> int:
    """校验并返回 WaveJSON lane 的周期倍率。

    :param dict_signal: 当前 signal lane 映射，shape=mapping，dtype=JSON object。
    :param str_field_path: 当前 signal 的结构路径。
    :return: 正整数 period，unit=WaveDrom time slots per character。
    :raises ValueError: period 不是正整数时抛出。
    """

    # WaveJSON 缺省 period 表示单周期字符跨度。
    obj_period_value = dict_signal.get("period", 1)  # 输入 lane 声明的周期倍率

    # 统一以 Decimal 检查整数性。
    decimal_period = finite_decimal(obj_period_value, f"{str_field_path}.period")  # 经过校验的周期倍率

    # 只有正整数 period 能够用完整字符补齐尾部。
    if decimal_period <= 0 or decimal_period != decimal_period.to_integral_value():

        # 非整周期会产生无法表达的半个 WaveDrom 字符。
        raise ValueError(f"> ERR: [Python] {str_field_path}.period must be a positive integer.")

    # 返回后续字符数量计算使用的 Python 整数。
    return int(decimal_period)

# 递归收集 signal group 中的真实 lane。
def collect_lanes(
    list_signal_values: list[Any],
    *,
    str_parent_path: str = "signal",
) -> list[WaveformLane]:
    """按源顺序收集 WaveJSON signal lane。

    :param list_signal_values: 当前 signal 数组，shape=(n,), dtype=JSON values。
    :param str_parent_path: 当前数组的结构路径。
    :return: 真实 lane 列表，unit=WaveDrom time slots；spacer 不在结果中。
    :raises TypeError: signal/group 结构不是映射或数组时抛出。
    :raises ValueError: wave、node 或时间字段非法时抛出。
    """

    # 结果列表保持源顺序，供调用方按原结构更新副本。
    list_lanes: list[WaveformLane] = []  # 当前 signal 树的真实 lane

    # 逐项递归，路径同时作为稳定的审计标识。
    for int_index, obj_signal_value in enumerate(list_signal_values):

        # 当前对象路径用于错误报告和结束时间映射。
        str_signal_path = f"{str_parent_path}[{int_index}]"  # 当前 signal 结构路径

        # mapping 有 wave 时是 lane，没有 wave 时是 spacer。
        if isinstance(obj_signal_value, Mapping):

            # deepcopy 后的对象是 dict；转换兼容外部 Mapping 调用。
            dict_signal = obj_signal_value if isinstance(obj_signal_value, dict) else dict(obj_signal_value)  # 当前 signal 映射

            # 空 mapping 只占布局空间，不参与时间轴统计。
            if "wave" not in dict_signal:

                # 保留 spacer，并继续扫描同层项目。
                continue

            # wave 必须是非空字符串，才能定义字符周期数。
            str_wave = dict_signal.get("wave")  # 当前 lane 的 wave 文本

            # 拒绝把列表或空文本当成可扩展 waveform。
            if not isinstance(str_wave, str) or not str_wave:

                # 错误定位保留当前结构路径。
                raise ValueError(f"> ERR: [Python] {str_signal_path}.wave must be a non-empty string.")

            # node 缺省为空字符串，存在时必须能按字符索引。
            str_node = dict_signal.get("node", "")  # 当前 lane 的节点标记

            # 非字符串 node 无法和 wave 使用同一时间槽。
            if str_node and not isinstance(str_node, str):

                # 拒绝模糊的渲染阶段错误。
                raise ValueError(f"> ERR: [Python] {str_signal_path}.node must be a string.")

            # 读取 signal 声明的字符展开倍率。
            int_period = lane_period(dict_signal, str_signal_path)  # 输入 lane 的周期倍率

            # 保留 signal 在全局时间轴上的声明起点。
            decimal_phase = finite_decimal(dict_signal.get("phase", 0), f"{str_signal_path}.phase")  # lane 的精确起始相位

            # node 可能比 wave 更长，先覆盖两者的最大字符跨度。
            int_character_count = max(len(str_wave), len(str_node))  # 当前 lane 的有效字符跨度

            # 先组装归一化副本中的可变 lane 对象。
            obj_lane = WaveformLane(  # 当前 lane 的时间和映射容器
                path=str_signal_path,  # 返回结果使用的结构索引
                signal=dict_signal,  # 尾部延展操作使用的副本引用
                period=int_period,  # 整周期差值计算使用的倍率
                phase=decimal_phase,  # 输入时间轴声明的起点
                character_count=int_character_count,  # wave/node 共同决定的跨度
            )

            # 将 lane 放入结果列表并保持原始 signal 顺序。
            list_lanes.append(obj_lane)

            # mapping 已经是完整 lane，不再把其字段当作 group 递归。
            continue

        # list 表示 group，首个字符串成员仅作为标题。
        if isinstance(obj_signal_value, list):

            # 空 group 没有可收集的 lane。
            if not obj_signal_value:

                # 继续处理外层 signal 数组。
                continue

            # 字符串首项是 group 标题，不参与 lane 解析。
            int_group_start = 1 if isinstance(obj_signal_value[0], str) else 0  # group 内容起点

            # 将 group 内 lane 追加到同一源顺序结果。
            list_lanes.extend(
                collect_lanes(
                    obj_signal_value[int_group_start:],
                    str_parent_path=str_signal_path,
                )
            )

            # 当前 group 完成后继续处理下一个外层项目。
            continue

        # 其他类型无法表达合法的 WaveJSON signal 树。
        raise TypeError(f"> ERR: [Python] {str_signal_path} must be a signal object or group.")

    # 返回稳定的真实 lane 集合。
    return list_lanes

# 计算 lane 的 phase 加字符跨度结束位置。
def effective_end(lane: WaveformLane) -> Decimal:
    """返回一条 lane 的有效结束时间。

    :param lane: 已完成 period、phase 和字符跨度校验的 lane。
    :return: 以 WaveDrom 时间槽表示的结束位置，dtype=Decimal。
    """

    # phase 是起点，字符跨度乘 period 是占用宽度。
    decimal_end = lane.phase + Decimal(lane.character_count * lane.period)  # 当前 lane 的有效结束位置

    # 返回用于公共边界比较的 Decimal。
    return decimal_end
