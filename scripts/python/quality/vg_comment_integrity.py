"""执行配置驱动的 VG150 注释语义完整性门禁。"""

# future annotations 延后解析内部数据模型，保持运行期导入轻量。
from __future__ import annotations

# 正则只用于读取注释末尾的结构化字母数字或中文尾串。
import re
from dataclasses import dataclass
from typing import Any

# 规则源是注释语义阈值和证据短语的唯一配置来源。
from scripts.python.validation.rulebook import load_verilog_rulebook

# 复用既有 formatter AST 注释候选，避免引入第二套 Verilog 解析器。
from .quality_gate_comment_rules import _comment_reuse_candidates_for_module
from .quality_gate_common import CJK_PATTERN
from .quality_gate_types import CommentReuseCandidate
from .vg_rule_models import VgEvaluation, VgFinding, passed
from .vg_semantic_facts import VgFacts

# 末尾尾串只接受连续的中文、英文或数字，标点仍作为语义边界。
TAIL_PATTERN = re.compile(r"[0-9A-Za-z\u3400-\u9fff]+$")  # 注释末尾可轮换字符的匹配器

@dataclass(frozen=True)
class CommentIntegrityConfig:
    """保存已校验的 VG150 运行阈值。"""

    # markers 是高置信度流程/证据短语，不承载业务实体词表。
    markers: tuple[str, ...]  # 配置化流程和证据短语

    # minimum_family_size 控制结构化旋转族的最小实体数量。
    minimum_family_size: int  # 形成尾族所需的最小实体数

    # minimum_distinct_tails 控制族内必须出现的不同尾串数量。
    minimum_distinct_tails: int  # 尾族所需的不同尾串数

    # minimum_chinese_prefix_chars 防止孤立短词被当作尾族。
    minimum_chinese_prefix_chars: int  # 尾串前方的最小中文字符数

    # tail_minimum_length 定义候选尾串下界。
    tail_minimum_length: int  # 候选尾串最小长度

    # tail_maximum_length 定义候选尾串上界。
    tail_maximum_length: int  # 候选尾串最大长度

    # minimum_variable_positions 控制尾串中变化位置的最小数量。
    minimum_variable_positions: int  # 尾串中必须变化的位置数

    # period_minimum 定义周期长度下界。
    period_minimum: int  # 周期长度下界

    # period_maximum 定义周期长度上界。
    period_maximum: int  # 周期长度上界

    # minimum_complete_cycles 定义必须重复的完整周期数。
    minimum_complete_cycles: int  # 必须重复的完整周期数

    # short_tail_minimum_length 控制建族后的短尾复用检测下界。
    short_tail_minimum_length: int  # 建族后短尾长度下界

@dataclass(frozen=True)
class RotationFamily:
    """保存一次已经满足高置信度条件的尾串族。"""

    # candidates 是按源码顺序组成族的实体候选。
    candidates: tuple[CommentReuseCandidate, ...]  # 组成尾族的实体候选

    # tails 与 candidates 一一对应，保留原始尾串用于证据报告。
    tails: tuple[str, ...]  # 与候选一一对应的尾串

    # tail_length 用于对齐后续短尾检测的位置集合。
    tail_length: int  # 尾族固定长度

    # variable_positions 是族内发生轮换的尾串位置。
    variable_positions: tuple[int, ...]  # 族内发生轮换的位置

# evaluate_comment_integrity_gate 维护 VG150 的统一入口。
def evaluate_comment_integrity_gate(facts: VgFacts) -> VgEvaluation:
    """检查实体注释是否伪造流程证据或承载结构化无关尾串。

    参数:
        facts: 当前目标共享的 formatter AST 与源码事实。
    返回:
        `passed`、`failed` 或 `error` 状态的 VG150 结论。
    异常:
        不向调用方抛出配置异常，配置问题转换为 `error` 结论。
    """

    # 规则配置损坏时必须返回 error，禁止退回隐式默认阈值。
    try:

        # 规则加载失败时由下方 except 转换为阻断性 error。
        comment_integrity_config_vg150_config = _load_comment_integrity_config()  # 已校验的 VG150 配置

    # 配置加载异常必须在函数边界转换为稳定的 error 结论。
    except (KeyError, TypeError, ValueError) as exc:

        # 配置错误本身就是不可交付状态，保留稳定诊断而不泄露绝对路径。
        return VgEvaluation("error", True, message=f"VG150 rule configuration is invalid: {exc}")

    # list_findings 保存按文件、行号和检测阶段排序的 VG150 证据。
    list_findings: list[VgFinding] = []  # VG150 发现项列表

    # bool_applicable 区分“存在实体注释但通过”和“没有可分析实体”。
    bool_applicable = False  # 是否存在可分析的实体注释

    # 每个 source 只消费一次 formatter AST 和原始行，避免报告与门禁漂移。
    for source_fact in facts.sources:

        # formatter 报告中的 module 才能提供可信的实体绑定关系。
        for dict_module in source_fact.report.get("modules", []) or []:

            # 候选复用 VG066 的实体覆盖范围，但由本规则独立解释语义。
            list_candidates = _comment_reuse_candidates_for_module(  # 保存含行号、标签和正文的共享候选
                dict_module,  # 提供实体声明边界的 formatter 模块映射
                list(source_fact.lines),  # 当前文件源码行
                source_fact.relative_path,  # 报告中的相对路径
            )  # 完成带行号和实体标签的候选绑定

            # 没有足够中文字符的候选已由共享 helper 过滤。
            if not list_candidates:

                # 当前 module 没有可分析的实体注释。
                continue

            # 至少存在一个实体候选时，VG150 对当前目标具有适用性。
            bool_applicable = True  # 当前目标存在可分析的实体候选

            # 直接证据短语按实体逐条定位，确保 finding 落到实际注释行。
            list_findings.extend(
                _workflow_evidence_findings(
                    list_candidates,
                    comment_integrity_config_vg150_config.markers,
                )
            )

            # 同一实体类别内寻找固定长度、周期重复且位置变化充分的尾串族。
            dict_groups = _group_candidates_by_label(list_candidates)  # 按实体类别分组

            # 每个实体类别独立验证尾串周期，避免跨类别拼接证据。
            for str_label, group_candidates in dict_groups.items():

                # group_candidates 已按候选行号排序，便于验证周期和后续短尾。
                rotation_family = _find_rotation_family(  # 当前类别的尾族探测入口
                    group_candidates,  # 同类别候选
                    comment_integrity_config_vg150_config,  # 复用同一份已校验阈值
                )  # 尾族判定完成

                # 只有完成族判定后才决定是否跳过当前类别。
                if rotation_family is None:

                    # 普通中文说明或单个孤立词不满足高置信度结构证据。
                    continue

                # 先报告完整尾族，随后只检查族结束后的短尾复用。
                list_findings.append(  # 追加完整尾族聚合证据
                    _rotation_family_finding(rotation_family, str_label)
                )

                # 追加族建立后的短尾证据。
                list_findings.extend(
                    _short_tail_findings(
                        group_candidates,
                        rotation_family,
                        comment_integrity_config_vg150_config,
                        str_label,
                    )
                )

    # 发现任何确定证据都必须阻断交付。
    if list_findings:

        # 按路径、行号和消息排序，保证 JSON 与 Markdown 报告稳定。
        list_findings.sort(  # 稳定公开报告顺序
            key=lambda finding: (finding.path, finding.line, finding.message)
        )

        # 失败结论保留全部定位证据，供交付报告展示。
        return VgEvaluation(
            "failed",
            bool_applicable,
            tuple(list_findings),
            "实体注释包含流程证据措辞或结构化无关尾串。",
        )

    # 候选存在但无高置信度违规时仍返回 applicable=True，便于审查者区分低风险候选与无候选。
    return passed(applicable=bool_applicable)

# _load_comment_integrity_config 读取规则资产并完成边界校验。
def _load_comment_integrity_config() -> CommentIntegrityConfig:
    """从规则源读取并校验 VG150 配置。

    参数:
        无。
    返回:
        已完成类型和边界校验的不可变配置。
    异常:
        KeyError、TypeError、ValueError: 规则资产缺失或类型不合法。
    """

    # raw_rules 保留规则文件的扩展区域，避免代码内置业务词表。
    raw_rules = load_verilog_rulebook().raw  # 规则资产原始字典

    # dict_comments 保存注释规则区域。
    dict_comments = raw_rules.get("comments")  # 注释规则区域

    # 注释区域必须是对象，才能安全读取语义完整性配置。
    if not isinstance(dict_comments, dict):

        # 缺少 comments 区域时不能安全推断注释语义。
        raise ValueError("> ERR: [Python] comments section is missing")

    # dict_integrity 是本门禁唯一消费的语义完整性配置。
    dict_integrity = dict_comments.get("semantic_integrity")  # VG150 语义完整性区域

    # 语义完整性区域缺失时必须阻断而不是使用隐式默认值。
    if not isinstance(dict_integrity, dict):

        # 缺少配置时 fail-closed，防止静默放行幻觉注释。
        raise ValueError("> ERR: [Python] comments.semantic_integrity is missing")

    # markers 必须是非空字符串列表，短语由资产文件维护。
    raw_markers = dict_integrity.get("workflow_evidence_markers")  # 配置化流程证据短语

    # 短语区域类型不正确时不能执行直接证据判定。
    if not isinstance(raw_markers, list):

        # 类型漂移会使匹配结果不可审计。
        raise ValueError("> ERR: [Python] workflow_evidence_markers must be a list")
    
    # tuple_markers 保存规则资产提供的直接匹配键。
    tuple_markers = tuple(  # 去空白后的不可变短语集合
        str(item).strip() for item in raw_markers if str(item).strip()  # 过滤空短语
    )  # 完成非空短语的规范化集合

    # 空短语集合等价于关闭直接证据规则，必须 fail-closed。
    if not tuple_markers:

        # 短语配置为空时等价于关闭直接证据规则。
        raise ValueError("> ERR: [Python] workflow_evidence_markers must not be empty")

    # rotation_rules 统一承载结构化尾串判定阈值。
    dict_rotation = dict_integrity.get("rotation_tail")  # 结构化尾族阈值

    # 尾族阈值类型不正确时不能执行结构推断。
    if not isinstance(dict_rotation, dict):

        # 缺少旋转族阈值时不能执行结构推断。
        raise ValueError("> ERR: [Python] rotation_tail is missing")

    # 读取族大小阈值，控制需要多少实体才能启动周期证据。
    int_min_family = _positive_int(dict_rotation, "minimum_family_size")  # 尾族实体数量下界

    # 读取尾串多样性阈值，防止完全相同的注释触发。
    int_min_distinct = _positive_int(dict_rotation, "minimum_distinct_tails")  # 尾串多样性下界

    # 读取中文前缀阈值，过滤孤立短词。
    int_min_prefix = _positive_int(dict_rotation, "minimum_chinese_prefix_chars")  # 中文前缀下界

    # 读取尾串长度下界，确定最短完整族尾串。
    int_tail_min = _positive_int(dict_rotation, "tail_minimum_length")  # 尾串长度下界

    # 读取尾串长度上界，限制搜索空间。
    int_tail_max = _positive_int(dict_rotation, "tail_maximum_length")  # 尾串长度上界

    # 读取变化位置阈值，要求尾串具有结构差异。
    int_min_variable = _positive_int(dict_rotation, "minimum_variable_positions")  # 变化位置下界

    # 下界只接纳相邻候选的短步长。
    int_period_min = _positive_int(dict_rotation, "period_minimum")  # 近邻距离值

    # 上界拒绝跨越过宽的窗口。
    int_period_max = _positive_int(dict_rotation, "period_maximum")  # 远端距离值

    # 读取完整周期数阈值，要求重复轮次充分。
    int_cycles = _positive_int(dict_rotation, "minimum_complete_cycles")  # 完整周期数下界

    # 读取短尾长度下界，控制建族后的复用检测。
    int_short_min = _positive_int(dict_rotation, "short_tail_minimum_length")  # 短尾长度下界

    # 尾串长度上下界必须保持可搜索区间。
    if int_tail_min > int_tail_max:

        # 无效区间会让规则静默跳过所有候选。
        raise ValueError("> ERR: [Python] tail_minimum_length must not exceed tail_maximum_length")

    # 周期搜索区间必须保持可解释。
    if int_period_min > int_period_max:

        # 无效周期区间会让重复证据无法验证。
        raise ValueError("> ERR: [Python] period_minimum must not exceed period_maximum")

    # 短尾下界必须严格小于完整尾长。
    if int_short_min >= int_tail_min:

        # 否则短尾检测会与完整族检测重叠。
        raise ValueError("> ERR: [Python] short_tail_minimum_length must be below tail_minimum_length")

    # 不同尾串数量不能超过最小族长度。
    if int_min_distinct > int_min_family:

        # 不可满足的族条件必须在加载阶段报告。
        raise ValueError("> ERR: [Python] minimum_distinct_tails must fit within minimum_family_size")

    # 变化位置数量不能超过最大尾串长度。
    if int_min_variable > int_tail_max:

        # 不可满足的变化位置条件必须 fail-closed。
        raise ValueError("> ERR: [Python] minimum_variable_positions must fit within tail_maximum_length")

    # 返回不可变配置，后续检测不再读取 JSON。
    return CommentIntegrityConfig(  # 完成边界校验后的不可变配置
        tuple_markers,  # 直接流程证据短语

        # 族大小和尾串多样性共同决定是否进入结构判定。
        int_min_family,  # 形成尾族所需的实体数量
        int_min_distinct,  # 尾族必须包含的不同尾串数
        int_min_prefix,  # 尾串前方的中文语义长度

        # 尾串长度和变化列阈值限定结构化证据的形状。
        int_tail_min,  # 完整尾串长度下界
        int_tail_max,  # 完整尾串长度上界
        int_min_variable,  # 发生轮换的最少位置数

        # 周期和短尾参数控制建族后的持续检查。
        int_period_min,  # 传出短周期扫描的最小步长
        int_period_max,  # 传出长周期扫描的最大步长
        int_cycles,  # 需要观测的完整周期数
        int_short_min,  # 建族后短尾检测下界
    )

# _positive_int 读取严格正整数配置。
def _positive_int(dict_values: dict[str, Any], str_key: str) -> int:
    """读取一个严格为正的整数配置值。

    参数:
        dict_values: 当前配置对象。
        str_key: 待读取的配置键。
    返回:
        配置中的正整数值。
    异常:
        ValueError: 配置缺失、类型错误或数值非正。
    """

    # bool 虽是 int 子类，但不能作为阈值通过配置校验。
    raw_value = dict_values.get(str_key)  # 待校验的原始阈值

    # bool 不能作为数字阈值，且所有阈值必须严格为正。
    if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value <= 0:

        # 报出字段名，便于规则资产修复而不暴露运行路径。
        raise ValueError("> ERR: [Python] positive integer is required: " + str_key)

    # 返回已经通过类型和范围校验的阈值。
    return raw_value

# _workflow_evidence_findings 定位直接流程/证据短语。
def _workflow_evidence_findings(
    list_candidates: list[CommentReuseCandidate],
    tuple_markers: tuple[str, ...],
) -> list[VgFinding]:
    """定位包含配置化流程/证据短语的实体注释。

    参数:
        list_candidates: 已绑定实体的注释候选。
        tuple_markers: 规则资产提供的流程/证据短语。
    返回:
        每个首个短语命中的实体 finding。
    """

    # list_findings 保留每个实体的首个命中，避免同一行重复刷屏。
    list_findings: list[VgFinding] = []  # 直接短语命中发现项

    # 每个实体按源码顺序只检查一次首个短语。
    for candidate in list_candidates:

        # 只要一个高置信度短语命中即可判定流程证据注释。
        str_marker = next(  # 当前实体命中的首个配置短语
            (marker for marker in tuple_markers if marker in candidate.str_comment),  # 首个命中短语
            None,  # 未命中时返回空
        )

        # 未命中短语的实体仍可参与结构化尾族分析。
        if str_marker is None:

            # 普通中文实体说明继续交给结构化尾族规则。
            continue

        # 证据文本同时保留实体类别、名称和命中的配置短语。
        list_findings.append(  # 记录实体注释中的流程证据
            VgFinding(
                path=candidate.str_rel_path,
                line=candidate.int_line_no or 1,
                message="实体注释不得承载流程、图谱、测试或评审证据措辞。",
                evidence=(
                    f"{candidate.str_label}:{candidate.str_name} marker={str_marker} "
                    f"comment={candidate.str_comment}"
                ),
            )
        )

    # 返回稳定的实体顺序结果。
    return list_findings

# _group_candidates_by_label 隔离不同实体类别的周期证据。
def _group_candidates_by_label(
    list_candidates: list[CommentReuseCandidate],
) -> dict[str, list[CommentReuseCandidate]]:
    """按 formatter 实体类别分组候选。

    参数:
        list_candidates: 当前 module 的实体注释候选。
    返回:
        实体类别到源码顺序候选列表的映射。
    """

    # dict_groups 隔离不同实体类别，避免跨类别拼出伪周期。
    dict_groups: dict[str, list[CommentReuseCandidate]] = {}  # 实体类别分组结果

    # 同一 label 的候选保持源码顺序用于周期检测。
    for candidate in list_candidates:

        # 将当前实体追加到对应类别，保持源码顺序不变。
        dict_groups.setdefault(candidate.str_label, []).append(candidate)

    # 返回保留源码顺序的分组结果。
    return dict_groups

# _find_rotation_family 识别结构化尾串轮换族。
def _find_rotation_family(
    list_candidates: list[CommentReuseCandidate],
    config: CommentIntegrityConfig,
) -> RotationFamily | None:
    """寻找满足数量、变化位置和重复周期的尾串族。

    参数:
        list_candidates: 同一 formatter 实体类别的候选。
        config: 已校验的 VG150 阈值。
    返回:
        首个满足高置信度条件的尾串族，找不到时返回 None。
    """

    # 尾长从下界到上界递增搜索，优先保留稳定中文前缀。
    for int_tail_length in range(config.tail_minimum_length, config.tail_maximum_length + 1):

        # 当前尾长只收集仍保留中文语义前缀的实体。
        list_records: list[tuple[CommentReuseCandidate, str]] = []  # 当前尾长的实体与尾串配对

        # 逐个候选提取当前长度的尾串。
        for candidate in list_candidates:

            # 单行调用保持尾串提取的参数和用途可见。
            str_tail = _candidate_tail(candidate.str_comment, int_tail_length, config.minimum_chinese_prefix_chars)  # 当前实体的固定长度尾串

            # 提取失败的候选不参与当前尾长的结构判断。
            if str_tail is None:

                # 当前实体没有满足前缀和尾长边界。
                continue

            # 保存实体与尾串的配对，后续窗口继续沿用源码顺序。
            list_records.append((candidate, str_tail))

        # 候选数量不足时不能形成可审计尾族。
        if len(list_records) < config.minimum_family_size:

            # 实体数量不足时不能形成结构证据。
            continue

        # 在源码顺序窗口中寻找至少两轮的固定周期。
        for int_start in range(0, len(list_records) - config.minimum_family_size + 1):

            # 当前窗口固定为配置的最小族大小。
            list_window = list_records[int_start : int_start + config.minimum_family_size]  # 当前源码顺序窗口

            # 提取窗口尾串序列，供多样性和周期检查共同消费。
            tuple_tails = tuple(item[1] for item in list_window)  # 窗口尾串序列

            # 不同尾串不足时更像普通重复说明。
            if len(set(tuple_tails)) < config.minimum_distinct_tails:

                # 该窗口缺乏可区分的尾部变化，直接跳过。
                continue

            # 计算尾串中发生轮换的字符位置。
            tuple_variable_positions = tuple(_variable_positions(tuple_tails))  # 变化位置不可变集合

            # 变化位置不足时不判定为结构化轮换。
            if len(tuple_variable_positions) < config.minimum_variable_positions:

                # 结构差异没有达到配置下限，保守继续。
                continue

            # 只有完整周期重复时才建立尾族。
            if not _has_repeated_period(tuple_tails, config):

                # 没有完整重复周期时保守放行，避免误伤普通中文。
                continue

            # 尾族证据成立，返回候选和变化位置。
            return RotationFamily(
                tuple(item[0] for item in list_window),  # 尾族内的实体候选
                tuple_tails,  # 尾族内的固定尾串序列
                int_tail_length,  # 当前尾族的固定尾长
                tuple_variable_positions,  # 尾族内发生轮换的位置
            )

    # 没有高置信度结构族时保持通过。
    return None

# _candidate_tail 提取指定长度的结构化注释尾串。
def _candidate_tail(str_comment: str, int_tail_length: int, int_min_prefix_chars: int) -> str | None:
    """提取指定长度的注释尾串并校验中文前缀。

    参数:
        str_comment: 已去除注释标记的实体注释正文。
        int_tail_length: 本次尝试的尾串长度。
        int_min_prefix_chars: 尾串前方要求保留的中文字符数。
    返回:
        满足边界的尾串；无法提取时返回 None。
    """

    # 尾串前的标点被视为语义边界，不参与轮换字符统计。
    str_body = str_comment.strip()  # 去除首尾空白的注释正文

    # 从正文末端提取连续的可轮换字符序列。
    match_tail = TAIL_PATTERN.search(str_body)  # 连续尾串匹配结果

    # 没有连续尾串时不能进行结构检测。
    if match_tail is None:

        # 没有连续尾串的注释不参与结构检测。
        return None

    # 尾串必须有足够长度，且其前方仍保留可读中文语义。
    str_suffix = match_tail.group(0)  # 注释末尾连续字母数字或中文

    # 尾串不足指定长度时跳过当前长度。
    if len(str_suffix) < int_tail_length:

        # 当前候选不足指定尾长。
        return None

    # 去掉候选尾串后，保留前方用于中文语义校验的正文。
    str_prefix = str_body[: match_tail.start()] + str_suffix[: -int_tail_length]  # 尾串前语义正文

    # 尾串前方必须保留足够中文语义，避免孤立词误报。
    if len(CJK_PATTERN.findall(str_prefix)) < int_min_prefix_chars:

        # 无语义前缀的孤立词不会形成高置信度族。
        return None

    # 返回尾串本身，保留 ASCII/数字以兼容受控的编码尾标记。
    return str_suffix[-int_tail_length :]

# _variable_positions 计算尾串的变化列。
def _variable_positions(tuple_tails: tuple[str, ...]) -> list[int]:
    """返回尾串中出现多个字符的位置。

    参数:
        tuple_tails: 固定长度的尾串序列。
    返回:
        发生字符变化的零基位置列表。
    """

    # 尾串长度固定；空序列没有可比较的尾串列。
    if not tuple_tails:

        # 返回空变化位置集合。
        return []

    # 逐列计算出现多个字符的位置。
    return [
        int_position
        for int_position in range(len(tuple_tails[0]))
        if len({tail[int_position] for tail in tuple_tails}) > 1
    ]

# _has_repeated_period 验证完整的重复周期。
def _has_repeated_period(
    tuple_tails: tuple[str, ...],
    config: CommentIntegrityConfig,
) -> bool:
    """判断尾串序列是否包含配置范围内的完整重复周期。

    参数:
        tuple_tails: 固定长度尾串序列。
        config: 已校验的周期阈值。
    返回:
        序列满足完整周期重复时返回 True。
    """

    # 只有达到完整周期轮数才进入比较，避免两三个偶然重复误报。
    for int_period in range(config.period_minimum, config.period_maximum + 1):

        # 当前周期长度对应的最少观测元素数。
        int_required = int_period * config.minimum_complete_cycles  # 当前周期所需的最少元素数

        # 当前周期长度无法完成配置要求的轮数。
        if len(tuple_tails) < int_required:

            # 当前序列长度不足以完成此步长的两轮观测。
            continue

        # 逐项比较相邻周期，避免只凭首尾两个样本下结论。
        if all(
            tuple_tails[int_index] == tuple_tails[int_index + int_period]
            for int_index in range(len(tuple_tails) - int_period)
        ):

            # 所有已观测周期位置一致，证据成立。
            return True

    # 没有稳定周期时不判定。
    return False

# _rotation_family_finding 将尾族转换为统一 finding。
def _rotation_family_finding(rotation_family: RotationFamily, str_label: str) -> VgFinding:
    """把尾串族转换为一条可追溯 finding。

    参数:
        rotation_family: 已满足 VG150 阈值的尾串族。
        str_label: formatter 实体类别。
    返回:
        带行号和尾串证据的 finding。
    """

    # 证据只展示代表尾串和行号，不输出完整源码路径。
    str_lines = ",".join(  # 族内实体行号
        str(candidate.int_line_no or 1)  # 单个实体行号
        for candidate in rotation_family.candidates  # 按族内源码顺序遍历
    )  # 族内行号证据

    # 保留尾族原始顺序，便于审查周期证据。
    str_tails = "|".join(rotation_family.tails)  # 族内代表尾串

    # candidate_first 用于把聚合证据落到最早实体。
    comment_reuse_candidate_candidate_first: CommentReuseCandidate = rotation_family.candidates[0]  # 族内最早实体

    # 返回一条聚合 finding，避免同一族重复刷屏。
    return VgFinding(
        path=comment_reuse_candidate_candidate_first.str_rel_path,
        line=comment_reuse_candidate_candidate_first.int_line_no or 1,
        message=f"实体注释在 {str_label} 类别中形成结构化轮换尾串族，不能用无关词填充注释。",
        evidence=(
            f"label={str_label} lines={str_lines} tails={str_tails} "
            f"variable_positions={rotation_family.variable_positions}"
        ),
    )

# _short_tail_findings 检查尾族建立后的短尾复用。
def _short_tail_findings(
    list_candidates: list[CommentReuseCandidate],
    rotation_family: RotationFamily,
    config: CommentIntegrityConfig,
    str_label: str,
) -> list[VgFinding]:
    """检查尾族结束后是否复用学习到的短尾字符集合。

    参数:
        list_candidates: 同一实体类别的全部候选。
        rotation_family: 已建立的完整尾族。
        config: 已校验的 VG150 阈值。
        str_label: formatter 实体类别。
    返回:
        族结束后命中的短尾 finding 列表。
    """

    # dict_alphabets 只学习完整族的每个位置，禁止引入外部词库。
    dict_alphabets = {  # 完整尾族各位置允许出现的字符集合
        int_position: {tail[int_position] for tail in rotation_family.tails}  # 当前列字符集合
        for int_position in rotation_family.variable_positions  # 遍历变化列
    }  # 完整尾族位置字典

    # 通过候选对象身份定位族的结束位置，避免相同文本造成错误截断。
    int_family_end = 0  # 完整尾族在候选序列中的结束索引

    # 逐个候选查找完整尾族的最后一个对象。
    for int_index, candidate in enumerate(list_candidates):

        # 通过对象身份定位族末尾，避免同文案造成错误截断。
        if candidate is rotation_family.candidates[-1]:

            # 记录尾族之后首个候选的切片索引。
            int_family_end = int_index + 1  # 族结束后的首个候选索引

            # 族末尾已定位，无需继续扫描。
            break

    # 族结束后的短尾候选只报告一次，避免同一行多长度重复。
    list_findings: list[VgFinding] = []  # 短尾复用发现项

    # 仅扫描完整族之后的实体，避免把已建族样本重复报告。
    for candidate in list_candidates[int_family_end:]:

        # 由短到长尝试，首个命中即可定位当前实体。
        for int_short_length in range(config.short_tail_minimum_length, rotation_family.tail_length):

            # 从当前实体注释中提取正在尝试的短尾长度。
            str_tail = _candidate_tail(  # 当前实体尝试的短尾
                candidate.str_comment,  # 实体注释正文
                int_short_length,  # 当前短尾长度
                config.minimum_chinese_prefix_chars,  # 沿用配置的中文语义下限
            )  # 短尾提取结果

            # 无法提取指定长度时继续尝试下一个长度。
            if str_tail is None:

                # 当前长度不具备足够中文前缀。
                continue

            # 短尾从完整尾串末端对齐，要求每个字符都来自已学习位置集合。
            int_offset = rotation_family.tail_length - int_short_length  # 尾族位置对齐偏移

            # 短尾字符必须落在完整族的已学习位置集合中。
            if all(
                int_position in dict_alphabets
                and str_char in dict_alphabets[int_position]
                for int_position, str_char in enumerate(str_tail, start=int_offset)
            ):

                # 找到首个命中长度后立即报告该实体。
                list_findings.append(  # 记录族建立后的首个短尾命中
                    VgFinding(
                        path=candidate.str_rel_path,
                        line=candidate.int_line_no or 1,
                        message=f"实体注释在 {str_label} 尾族建立后复用短尾串，不能继续填充无关字符。",
                        evidence=f"tail={str_tail} learned_tail_length={rotation_family.tail_length}",
                    )
                )

                # 当前实体已经报告，不再重复检查更短长度。
                break

    # 返回按源码顺序排列的短尾证据。
    return list_findings
