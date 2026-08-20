"""在 WaveJSON 进入 WaveDrom 前统一真实 lane 的公共结束时间。"""

# 深复制隔离调用方对象，Decimal 保证周期比较不受浮点误差影响。
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

# 复用 lane 解析和结束时间计算，保持公共函数只负责编排策略。
from .waveform_alignment_support import WaveformLane, collect_lanes, effective_end

# WaveformAlignmentResult 保存归一化 JSON 和每条 lane 的结束位置。
@dataclass(frozen=True)
class WaveformAlignmentResult:
    """返回 WaveJSON 公共结束时间归一化结果。"""

    # 归一化后的 JSON 可直接写入 JSON5 并交给 renderer。
    normalized_wavejson: dict[str, Any]  # 已完成公共右边界归一化的 WaveJSON

    # 结束位置映射供调用方验证所有 lane 的公共边界。
    lane_effective_ends: Mapping[str, Decimal]  # 结构路径到结束时间的映射

# 更新 lane 的字符跨度，覆盖 wave/node 归一化后的实际长度。
def _refresh_character_count(lane: WaveformLane, bool_include_node: bool) -> None:
    """刷新一条 lane 的有效字符跨度。

    :param lane: 归一化副本中的可变 lane。
    :param bool_include_node: 是否把 node 长度计入可见结束位置。
    :return: 不返回业务值；lane 的 character_count 会被更新。
    """

    # wave 是所有 lane 必须具备的主时间轴。
    int_wave_length = len(lane.signal["wave"])  # 当前 wave 字符长度

    # node 只有在策略启用时才参与公共结束时间。
    int_node_length = len(lane.signal.get("node", "")) if bool_include_node else 0  # 当前 node 标记跨度

    # 取参与策略的最大跨度供 effective_end 使用。
    lane.character_count = max(int_wave_length, int_node_length)  # 当前 lane 的有效字符跨度

# 解析策略并返回对齐模式和 node 参与标记。
def _resolve_policy(dict_policy: Mapping[str, Any]) -> tuple[str, bool]:
    """读取 waveform alignment 的两个执行策略。

    :param dict_policy: settings 或参数化 case 提供的策略映射。
    :return: 对齐模式和 node 是否参与结束时间的二元组。
    :raises ValueError: 策略模式未知时抛出。
    """

    # 默认模式延展到最长 lane，严格模式只接受已等长输入。
    str_mode = str(dict_policy.get("mode", "extend_to_max_end"))  # 当前对齐策略名称

    # 未知模式禁止静默选择可能改变语义的行为。
    if str_mode not in {"extend_to_max_end", "reject_mismatch"}:

        # 错误只指出策略合同，不嵌入当前 case 内容。
        raise ValueError("> ERR: [Python] waveform alignment mode is unsupported.")

    # node 默认纳入公共结束时间，防止标记落在 wave 右端之外。
    bool_include_node = bool(dict_policy.get("include_node_extent", True))  # node 是否参与结束时间

    # 返回已完成校验的策略值。
    return str_mode, bool_include_node

# 收集 lane 并在计算结束前覆盖 node 的字符跨度。
def _prepare_lanes(
    list_normalized_signals: list[Any],
    bool_include_node: bool,
) -> list[WaveformLane]:
    """返回已完成输入校验的归一化 lane 列表。

    :param list_normalized_signals: 归一化 signal 数组，shape=(n,), dtype=JSON values。
    :param bool_include_node: 是否把 node 字符跨度纳入时间轴。
    :return: 可被尾部延展的 lane 列表，unit=WaveDrom time slots。
    :raises ValueError: 输入没有真实 lane 时抛出。
    """

    # 递归收集真实 lane，保留原始 group 和 spacer 结构。
    list_lanes = collect_lanes(list_normalized_signals)  # 归一化副本中的真实 lane

    # spacer-only 图不能生成可验证的公共结束时间。
    if not list_lanes:

        # 让调用方看到具体的 WaveJSON 内容缺失原因。
        raise ValueError("> ERR: [Python] wavejson.signal must contain a waveform lane.")

    # 刷新默认字符跨度，并为 node 超出 wave 的 lane 预留延续字符。
    for lane in list_lanes:

        # 当前策略决定 node 是否参与公共结束位置。
        _refresh_character_count(lane, bool_include_node)  # 更新当前 lane 的初始跨度

        # node 比 wave 长时，必须先延长 wave 覆盖节点位置。
        if bool_include_node and len(lane.signal.get("node", "")) > len(lane.signal["wave"]):

            # 计算 wave 需要覆盖的现有 node 字符数量。
            int_node_wave_padding = len(lane.signal["node"]) - len(lane.signal["wave"])  # wave 覆盖 node 的补齐数

            # 点号延续最后状态，不改变已有 wave 字符。
            lane.signal["wave"] = lane.signal["wave"] + "." * int_node_wave_padding  # 覆盖 node 的 wave 尾部

            # 补齐后重新计算该 lane 的字符跨度。
            _refresh_character_count(lane, bool_include_node)  # 更新 node 覆盖后的跨度

    # 返回可以安全参与公共结束时间计算的 lane 列表。
    return list_lanes

# 延展一条 lane 并保持已有 wave/node 前缀。
def _extend_lane(
    lane: WaveformLane,
    decimal_target_end: Decimal,
    decimal_original_end: Decimal,
    str_mode: str,
    bool_include_node: bool,
) -> None:
    """把单条 lane 补齐到公共结束时间。

    :param lane: 归一化副本中的可变 lane。
    :param decimal_target_end: 全部 lane 的公共目标结束时间。
    :param decimal_original_end: 当前 lane 的原始结束时间。
    :param str_mode: 对齐模式。
    :param bool_include_node: 是否刷新 node 对应的可见跨度。
    :return: 不返回业务值；lane 映射会被尾部点号更新。
    :raises ValueError: 当前 lane 无法按完整 period 补齐时抛出。
    """

    # 当前 lane 与公共右边界之间的时间差。
    decimal_end_delta = decimal_target_end - decimal_original_end  # 当前 lane 的结束差值

    # 已经达到最长边界的 lane 不需要写入延续字符。
    if decimal_end_delta == 0:

        # 保持当前 lane 的 wave/node 语义不变。
        return

    # 严格拒绝模式不允许自动修改输入。
    if str_mode == "reject_mismatch":

        # 错误只引用动态结构路径，方便调用方定位。
        raise ValueError(f"> ERR: [Python] waveform lane {lane.path} does not share the common end.")

    # 结束差值必须由完整 period 组成，不能制造半周期字符。
    decimal_append_cycles = decimal_end_delta / Decimal(lane.period)  # 当前 lane 追加的周期数

    # 不整除或负差值都无法安全延展。
    if decimal_append_cycles < 0 or decimal_append_cycles != decimal_append_cycles.to_integral_value():

        # fail-closed 防止视觉补齐掩盖真实 phase/period 冲突。
        raise ValueError(f"> ERR: [Python] waveform lane {lane.path} cannot be extended by whole cycles.")

    # 将完整周期数转换成点号数量。
    int_append_cycles = int(decimal_append_cycles)  # 当前 lane 尾部追加的字符数量

    # 点号延续最后状态，保留原有跳变和 data 语义。
    lane.signal["wave"] = lane.signal["wave"] + "." * int_append_cycles  # 追加后的 wave 尾部

    # node 存在时同步追加空节点，保持字符索引关系。
    if isinstance(lane.signal.get("node"), str):

        # wave 的新长度决定 node 所需的对齐字符数。
        int_node_padding = len(lane.signal["wave"]) - len(lane.signal["node"])  # node 需要补齐的长度

        # 只在 node 较短时补充空节点，不修改现有标记。
        if int_node_padding > 0:

            # 点号表示该周期没有新的 node 标记。
            lane.signal["node"] = lane.signal["node"] + "." * int_node_padding  # node 对齐后的空标记

    # 追加后刷新 lane 跨度，供最终公共边界校验使用。
    _refresh_character_count(lane, bool_include_node)  # 更新追加后的 lane 跨度

# 建立最终 lane 结束位置映射。
def _build_end_map(list_lanes: list[WaveformLane]) -> dict[str, Decimal]:
    """计算所有 lane 的有效结束时间。

    :param list_lanes: 已完成尾部归一化的 lane 列表。
    :return: lane 结构路径到 Decimal 结束时间的映射。
    """

    # 逐条 lane 写入动态结构路径和结束位置。
    dict_aligned_ends: dict[str, Decimal] = {}  # 归一化后的 lane 结束位置映射

    # 保持 lane 源顺序，方便生成稳定审计报告。
    for lane in list_lanes:

        # 每个 lane 的路径和值构成 alignment receipt 的最小证据。
        dict_aligned_ends[lane.path] = effective_end(lane)  # 当前 lane 的最终结束位置

    # 返回完整结束位置映射。
    return dict_aligned_ends

# 按策略把不等长 lane 延展到最长 lane 的公共结束位置。
def align_wavejson_ends(
    wavejson: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
) -> WaveformAlignmentResult:
    """延展 WaveJSON lane 尾部，使所有真实 lane 共享结束时间。

    :param wavejson: 含非空 signal 数组的 WaveJSON 映射。
    :param policy: 由 settings 或参数化 case 提供的对齐策略。
    :return: 含归一化 WaveJSON 和 lane 结束时间映射的结果。
    :raises TypeError: WaveJSON 顶层或 signal 容器类型错误时抛出。
    :raises ValueError: lane 时间无法安全补齐或输入字段非法时抛出。
    """

    # 顶层映射是 WaveJSON 的最小输入合同。
    if not isinstance(wavejson, Mapping):

        # 非映射对象没有稳定的 signal 字段。
        raise TypeError("> ERR: [Python] wavejson must be an object.")

    # signal 数组不能为空，否则没有公共时间轴可建立。
    list_signal_values = wavejson.get("signal")  # 原始 signal 数组

    # 先拒绝错误容器，避免递归阶段产生不明确异常。
    if not isinstance(list_signal_values, list) or not list_signal_values:

        # 错误字段与 SpecDocument 的 WaveJSON 合同一致。
        raise ValueError("> ERR: [Python] wavejson.signal must be a non-empty list.")

    # 策略缺省为空映射，具体策略值由配置或调用方提供。
    dict_policy = dict(policy or {})  # 当前 waveform 对齐策略

    # 统一解析模式和 node 结束时间开关，避免主流程重复分支。
    tuple_policy = _resolve_policy(dict_policy)  # 已校验的对齐策略二元组

    # 取出字符串策略名称供延展 helper 分派。
    str_mode = tuple_policy[0]  # 当前 lane 尾部处理模式

    # 取出 node 是否参与结束时间的布尔策略。
    bool_include_node = tuple_policy[1]  # node 是否影响公共边界

    # 复制顶层和嵌套 signal，后续追加只作用于归一化副本。
    dict_normalized_wavejson = deepcopy(dict(wavejson))  # 可安全修改的 WaveJSON 副本

    # 读取复制后的 signal 数组，确保 lane 引用不指向原始输入。
    list_normalized_signals = dict_normalized_wavejson.get("signal")  # 归一化副本的 signal 数组

    # deepcopy 后结构仍必须满足顶层 list 合同。
    if not isinstance(list_normalized_signals, list):

        # 这是输入映射与副本结构不一致的内部错误。
        raise TypeError("> ERR: [Python] normalized wavejson.signal must be a list.")

    # 递归收集并准备真实 lane，处理 node 比 wave 更长的输入。
    list_lanes = _prepare_lanes(list_normalized_signals, bool_include_node)  # 已校验的 lane 列表

    # 计算所有 lane 的原始有效结束时间。
    dict_original_ends = {lane.path: effective_end(lane) for lane in list_lanes}  # 原始 lane 结束位置映射

    # 最长 lane 决定整张图的公共右边界。
    decimal_target_end = max(dict_original_ends.values())  # 所有 lane 的目标结束位置

    # 逐条 lane 委托完整 period、node 和 mode 约束的延展逻辑。
    for lane in list_lanes:

        # 单条 lane helper 保留原有 wave/node 前缀。
        _extend_lane(
            lane,
            decimal_target_end,
            dict_original_ends[lane.path],
            str_mode,
            bool_include_node,
        )

    # 重新计算所有 lane 的结束位置，形成最终审计结果。
    dict_aligned_ends = _build_end_map(list_lanes)  # 封装所有 lane 的共同边界证据

    # 所有真实 lane 必须落在完全相同的 Decimal 位置。
    if len(set(dict_aligned_ends.values())) != 1:

        # 这是对齐器内部不变量被破坏时的最终保护。
        raise ValueError("> ERR: [Python] waveform alignment did not produce a common end.")

    # 返回 JSON 副本与动态结束映射，调用方决定是否写入或继续渲染。
    return WaveformAlignmentResult(
        normalized_wavejson=dict_normalized_wavejson,  # 交给 spec bundle 写出的 WaveJSON
        lane_effective_ends=dict_aligned_ends,  # 返回给调用方复核的结束位置
    )
