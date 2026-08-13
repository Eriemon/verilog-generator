"""根据验证报告生成 staged Verilog 反思诊断与修复提示。"""

# 启用更直接的前向引用标注写法。
from __future__ import annotations

# 标准库依赖负责 JSON 文本序列化。
import json
from typing import Any

# 工件图摘要负责补充可疑产物定位信息。
from scripts.python.workflow.artifact_graph import suspect_artifacts_from_graph

# 计划分解器负责把输入计划收敛成统一结构。
from scripts.python.workflow.planning import decompose_spec

# 用例模板接口负责补充场景化生成上下文。
from scripts.python.workflow.use_case_templates import select_use_case_template, summarize_use_case_template

# 错误来源顺序同时决定分类与修复动作的优先级。
ERROR_SOURCES = (
    "spec_issue",  # 规格本身冲突、缺项或输出契约不一致
    "dependency_issue",  # 依赖子函数、接口或上游模块存在问题
    "testbench_issue",  # 验证激励、对拍逻辑或参考向量异常
    "current_module_issue",  # 当前模块实现或注释可审查性存在缺陷
    "toolchain_issue",  # 工具链、环境或配置导致执行失败
    "insufficient_debug",  # 调试证据不足，暂时无法定位根因
    "needs_human_intervention",  # 自动化证据不足，需要人工决策
)  # 反思流程允许识别的错误来源顺序表

# 默认动作映射为每一种来源提供首选修复策略。
ACTION_BY_SOURCE = {
    "spec_issue": "revise_plan",  # 先修规格和输出契约
    "dependency_issue": "fix_dependency",  # 先修依赖链路
    "testbench_issue": "fix_testbench",  # 先修验证资产
    "current_module_issue": "regenerate_current",  # 回到当前模块重生成
    "toolchain_issue": "fix_toolchain",  # 先修工具环境
    "insufficient_debug": "augment_tests",  # 先补调试与测试证据
    "needs_human_intervention": "ask_human",  # 直接切换人工决策
}  # 错误来源到默认修复动作的映射表

# 自由文本报告通过这组关键词映射到结构化错误来源。
SOURCE_KEYWORDS = {
    "toolchain_issue": (  # 工具链类文本命中的关键词集合
        "toolchain_issue",  # 工具不可用、超时或启动失败
        "required tool",  # 工具缺失通常意味着环境未就绪
        "failed to start",  # 工具启动失败通常说明运行环境不可用
        "timed out",  # 工具执行超时通常说明环境或调用路径异常
    ),
    "spec_issue": (  # 规格缺项或期望输出路径异常
        "spec_issue",  # validation/stage 已明确把失败归类为规格歧义
        "expected output file is missing",  # 期望产物缺失通常说明输出契约不完整
        "generated path does not exist",  # 生成路径不存在通常说明计划路径约定失效
    ),
    "dependency_issue": (  # 上游模块、声明或依赖链异常
        "dependency_issue",  # 报告已直接指出依赖链异常
        "undefined module",  # 实例化的下游模块没有可解析定义
        "undefined reference",  # 外部引用缺失通常来自依赖对象未生成
        "not declared",  # 标识符未声明时常见于接口或依赖遗漏
        "previous subfunction",  # 前序子函数失败会阻断当前阶段继续验证
        "dependency",  # 文本显式出现 dependency 时优先归到依赖问题
    ),
    "testbench_issue": (  # 验证基准、testbench 或行为检查异常
        "testbench_issue",  # 报告已直接指出 testbench 资产问题
        "testbench",  # 文本直接提到 testbench 时通常是验证资产异常
        "main() entry point",  # 入口函数缺失会导致测试脚本无法驱动
        "pass behavior",  # pass 行为异常代表对拍或断言逻辑失真
        "fail behavior",  # fail 行为异常代表失败分支验证方式不正确
        "verification case",  # 验证用例本身异常时应先回到测试资产
    ),
    "current_module_issue": (  # 当前模块实现质量或综合约束异常
        "current_module_issue",  # 报告已直接指出当前模块本体异常
        "placeholder",  # 占位实现未替换意味着当前模块未真正落地
        "not synthesizable",  # 不可综合问题通常需要回改当前 RTL
        "top module",  # top module 相关报错通常落在当前模块边界
        "failed:",  # 通用 failed 前缀常用于当前模块静态或行为失败
        "pragma",  # pragma 不兼容往往需要修改当前模块实现细节
        "cfg",  # cfg 相关文本常反映当前模块配置或生成内容不匹配
        "reviewability",  # 可审查性问题说明当前模块结构或注释失衡
        "comment",  # comment 相关失败通常要回到当前模块的可读性治理
        "fsm",  # FSM 相关失败多半来自当前模块时序或状态机实现
    ),
    "insufficient_debug": (  # 证据不足，暂时无法精确定位
        "insufficient_debug",  # 报告已直接说明调试证据不足
        "cannot pinpoint",  # 无法精确定位时需要先补定位证据
        "limited test cases",  # 用例过少意味着当前回归覆盖不足
    ),
}  # 文本关键词到错误来源的匹配表

# 语义执行摘要只关心这一组核心字段。
SEMANTIC_KEYS = (
    "semantic_ready",  # 语义执行是否达到可判定状态
    "mismatched_cases",  # 失配用例详情
    "checkpoint_drift",  # 检查点漂移明细
    "failed_cases",  # 失败用例编号列表
    "localization_confidence",  # 定位置信度
)  # 语义摘要允许透传的字段集合

# 轨迹摘要默认只保留最近若干次事件，避免 prompt 膨胀。
MAX_TRAJECTORY_EVENTS = 12  # 轨迹摘要保留的最近事件数量

# 工件回溯只读取最近若干次事件，避免把陈旧路径带回修复提示。
MAX_TRACE_ARTIFACT_EVENTS = 8  # 回溯可疑工件时读取的最近事件数量

# 仅在目标列表中追加未出现过的字符串，保证顺序去重。
def _append_unique_str(list_target: list[str], str_value: str) -> None:
    """按首次出现顺序向列表追加唯一字符串。

    参数:
        list_target: 需要原位追加的目标字符串列表。
        str_value: 当前候选字符串值。

    返回:
        None。
    """

    # 只有尚未出现的新值才需要追加。
    if str_value not in list_target:

        # 把新的唯一值追加到结果末尾。
        list_target.append(str_value)

# 判断文本中是否包含指定关键词集合。
def _contains_any_keyword(str_text: str, tuple_keywords: tuple[str, ...]) -> bool:
    """判断文本是否命中任一关键词。

    参数:
        str_text: 已统一大小写的待匹配文本。
        tuple_keywords: 当前来源对应的关键词元组。

    返回:
        命中任一关键词时返回 True，否则返回 False。
    """

    # 逐个关键词检查文本是否存在匹配片段。
    for str_keyword in tuple_keywords:

        # 一旦命中任一关键词即可提前返回。
        if str_keyword in str_text:

            # 返回命中结果，避免继续做无意义匹配。
            return True

    # 关键词全部落空后，说明当前来源没有命中文本证据。
    return False

# 把任意 JSON 兼容对象序列化成缩进文本。
def _to_pretty_json(json_compatible_value: Any) -> str:
    """把 JSON 兼容对象序列化成易读文本。

    参数:
        json_compatible_value: 需要序列化的 JSON 兼容对象。

    返回:
        适合直接嵌入提示词的缩进 JSON 文本。
    """

    # 统一使用 UTF-8 友好的格式输出诊断对象。
    str_pretty_json = json.dumps(  # 供提示词嵌入的格式化 JSON 文本
        json_compatible_value,  # 需要序列化的原始对象
        indent=2,  # 统一使用两空格缩进提升嵌入可读性
        ensure_ascii=False,  # 保留中文而不是转成 ASCII 转义
    )  # 带缩进且保留中文的 JSON 文本

    # 返回序列化后的 JSON 文本。
    return str_pretty_json

# 根据文本报告与执行轨迹归类错误来源。
def classify_report(
    report_text: str,
    trace_events: list[dict[str, Any]] | None = None,
) -> list[str]:
    """从报告文本和轨迹事件推断错误来源列表。

    参数:
        report_text: 原始验证报告文本。
        trace_events: 反思与验证阶段记录的轨迹事件列表。

    返回:
        按优先级和首次出现顺序去重后的错误来源列表。
    """

    # 统一转成小写文本，避免关键词匹配受大小写影响。
    str_lowered_report = report_text.lower()  # 用于关键词匹配的报告文本

    # 轨迹事件缺省时按空列表处理，避免后续反复判空。
    list_trace_events: list[dict[str, Any]] = trace_events or []  # 当前轨迹事件列表

    # 结果列表按首次命中顺序记录错误来源。
    list_sources: list[str] = []  # 推断得到的错误来源列表

    # 先吸收轨迹事件中已经结构化标注过的错误来源。
    for dict_event in list_trace_events:

        # 当前事件可能携带若干结构化来源标签。
        list_event_sources = (
            dict_event.get("error_sources", []) or []  # 当前事件携带的错误来源标签列表
        )

        # 逐个检查事件来源是否属于受支持枚举。
        for raw_source in list_event_sources:

            # 统一把来源项转换成字符串，避免异常类型干扰判断。
            str_source = str(raw_source)  # 当前事件来源的字符串形式

            # 非受支持的 source 对当前反思器没有解释力。
            if str_source in ERROR_SOURCES:

                # 事件来源需要按首次出现顺序追加到结果。
                _append_unique_str(list_sources, str_source)

    # 再基于自由文本关键词补齐遗漏的错误来源。
    for str_source_name, tuple_keywords in SOURCE_KEYWORDS.items():

        # 关键词命中时，把对应来源加入结果。
        if _contains_any_keyword(str_lowered_report, tuple_keywords):

            # 文本推断出的来源同样按首次出现顺序追加。
            _append_unique_str(list_sources, str_source_name)

    # 仍无法定位来源时，明确标记需要人工介入。
    if not list_sources:

        # 用统一的人机协同来源兜底，避免结果为空。
        _append_unique_str(list_sources, "needs_human_intervention")

    # 返回按优先级去重后的错误来源列表。
    return list_sources

# 为 staged Verilog 修复流程生成完整提示词。
def generate_repair_prompt(
    report_text: str,
    plan: dict[str, Any],
    trace_events: list[dict[str, Any]] | None = None,
    validation_json: dict[str, Any] | None = None,
    artifact_graph: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> str:
    """生成包含诊断、计划与验证证据的修复提示词。

    参数:
        report_text: 原始验证报告文本。
        plan: 当前阶段对应的实现计划。
        trace_events: 历史反思与验证轨迹事件列表。
        validation_json: 结构化 validation 报告对象。
        artifact_graph: 当前阶段的工件依赖图摘要。
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        供修复器直接消费的完整修复提示词。
    """

    # 先把输入计划规范化，避免不同入口结构影响后续逻辑。
    dict_normalized_plan = decompose_spec(plan)  # 规范化后的实现计划

    # 轨迹事件缺省时统一按空列表处理。
    list_trace_events: list[dict[str, Any]] = trace_events or []  # 当前修复上下文中的轨迹事件

    # 修复计划负责给出错误来源、动作与上下文需求。
    dict_repair_plan = build_repair_plan(  # 后续修复动作与上下文约束
        report_text,  # 原始验证报告文本
        dict_normalized_plan,  # 规范化后的计划骨架
        list_trace_events,  # 用来追溯可疑工件的近期轨迹窗口
        validation_json,  # validation JSON 提供的结构化报告对象
        artifact_graph,  # 当前阶段的工件依赖图摘要
        stage_verification,  # 用 stage issue 文本筛第一批可疑子函数
    )  # 结构化修复计划

    # repair plan 先复用诊断快照，再决定动作和上下文。
    dict_diagnosis = build_diagnosis(  # 先产出定位快照，再决定修复动作
        dict_normalized_plan,  # 提供计划骨架，便于诊断器识别候选子函数与依赖
        list_trace_events,  # 回放最近尝试序列，判断哪些自动路线已经走过
        validation_json,  # 补入 metrics 与 issue 细节，支撑失败类型归因
        stage_verification,  # 复用 stage gate 已算出的来源、ready 与推荐动作
    )  # 当前问题的结构化诊断结果

    # 计划 JSON 会直接嵌入 prompt，供后续修复器引用。
    str_plan_json = _to_pretty_json(dict_normalized_plan)  # 规范化计划的 JSON 文本

    # 分类结果 JSON 用来解释主要错误来源与行动建议。
    str_classification_json = _to_pretty_json(dict_repair_plan)  # 修复计划的 JSON 文本

    # 差分诊断 JSON 负责补足定位与下一步动作推理。
    str_diagnosis_json = _to_pretty_json(dict_diagnosis)  # 差分诊断的 JSON 文本

    # 轨迹摘要帮助修复器理解最近尝试过什么。
    str_trace_json = _to_pretty_json(  # 最近轨迹摘要的 JSON 文本
        _trajectory_summary(list_trace_events)  # 最近若干次轨迹事件的轻量摘要
    )

    # 结构化验证 JSON 直接保留原始字段，避免丢失诊断上下文。
    str_validation_json = _to_pretty_json(validation_json or {})  # 验证报告的 JSON 文本

    # 工件图摘要帮助修复器理解当前失败波及的产物范围。
    str_graph_json = _to_pretty_json(  # 工件图轻量摘要的 JSON 文本
        _graph_summary(artifact_graph)  # 适合嵌入 prompt 的工件图摘要
    )

    # Stage verifier 结果比纯文本报告更适合作为高置信门禁证据。
    str_gate_json = _to_pretty_json(  # stage gate 序列化后的 JSON 文本
        stage_verification or {}  # stage verifier 输出的原始结构化结果
    )  # stage verifier 门禁结果的 JSON 文本

    # 用例模板上下文用于保留场景约束与板级信息。
    str_use_case_template_json = _to_pretty_json(  # 用例模板约束与场景摘要
        _use_case_template_context(dict_normalized_plan)  # 当前计划匹配到的模板上下文摘要
    )

    # 报告正文在嵌入前去掉尾部多余空白，减少 prompt 噪音。
    str_trimmed_report = report_text.rstrip()  # 去尾空白后的原始验证报告

    # 组装最终提示词，保持诊断证据与行动说明的节顺序稳定。
    str_repair_prompt = (
        "# Repair prompt\n\n"
        "You are repairing a staged Verilog generation result. Use the implementation plan,\n"
        "validation report, and error-source classification below.\n\n"
        "## Error-source classification\n\n"
        "```json\n"
        f"{str_classification_json}\n"
        "```\n\n"
        "## Differential diagnosis\n\n"
        "```json\n"
        f"{str_diagnosis_json}\n"
        "```\n\n"
        "## Implementation plan\n\n"
        "```json\n"
        f"{str_plan_json}\n"
        "```\n\n"
        "## Generation trajectory summary\n\n"
        "```json\n"
        f"{str_trace_json}\n"
        "```\n\n"
        "## Structured validation report\n\n"
        "```json\n"
        f"{str_validation_json}\n"
        "```\n\n"
        "## Artifact graph summary\n\n"
        "```json\n"
        f"{str_graph_json}\n"
        "```\n\n"
        "## Verifier gate result\n\n"
        "```json\n"
        f"{str_gate_json}\n"
        "```\n\n"
        "## Use-case template context\n\n"
        "```json\n"
        f"{str_use_case_template_json}\n"
        "```\n\n"
        "## Validation report\n\n"
        "```text\n"
        f"{str_trimmed_report}\n"
        "```\n\n"
        "## Repair instructions\n\n"
        "- If the source is `spec_issue`, revise the implementation plan or requested outputs\n"
        "  before regenerating code.\n"
        "- If the source is `dependency_issue`, inspect dependent subfunctions and interface\n"
        "  compatibility before editing the current module.\n"
        "- If the source is `testbench_issue`, repair the self-checking testbench and\n"
        "  reference-vector comparison before changing design logic.\n"
        "- If the source is `current_module_issue`, regenerate only the failing module or\n"
        "  testbench when possible.\n"
        "- If the source is `toolchain_issue`, fix tool availability/configuration first; do\n"
        "  not rewrite code unless the tool output points to code errors.\n"
        "- If a Verifier gate result is present, prioritize its `recommended_action`,\n"
        "  interface-drift issues, vector-hash issues, and dependency mismatches over\n"
        "  textual guesses.\n"
        "- If a use-case template is selected, preserve its family-specific board-level\n"
        "  guidance, parameterization points, and provenance unless the repair explicitly\n"
        "  proves they caused the failure.\n"
        "- If semantic drift is visible but localization is weak, strengthen cases or\n"
        "  checkpoints before escalating to a human.\n"
        "- If the source is `needs_human_intervention`, summarize the unresolved ambiguity\n"
        "  and ask a precise hardware-design question.\n"
        "- Preserve the original output contract: manifest JSON first, then exact\n"
        "  `path=<relative/path>` code fences.\n"
        "- Keep the repaired output verifiable, executable, and implementable.\n"
    )  # 最终输出给修复器的完整提示词

    # 返回完整修复提示词。
    return str_repair_prompt

# 从计划中提取用例模板上下文，便于 prompt 保留场景约束。
def _use_case_template_context(plan: dict[str, Any]) -> dict[str, Any]:
    """返回当前计划对应的用例模板摘要。

    参数:
        plan: 已规范化的当前实现计划。

    返回:
        适合嵌入修复提示词的用例模板摘要字典。
    """

    # 先根据计划内容选择最合适的用例模板。
    dict_use_case_template = select_use_case_template(plan)  # 当前计划匹配到的用例模板

    # 再把模板收敛成适合 prompt 的轻量摘要。
    dict_template_summary = summarize_use_case_template(  # prompt 需要的模板轻量摘要
        dict_use_case_template  # select_use_case_template 选中的模板对象
    )

    # 返回模板摘要上下文。
    return dict_template_summary

# 根据首个错误来源推断默认修复动作。
def resolution_action(sources: list[str]) -> str:
    """返回错误来源对应的默认修复动作。

    参数:
        sources: 已按优先级排序的错误来源列表。

    返回:
        当前来源列表对应的默认修复动作字符串。
    """

    # 没有来源时直接要求人工介入。
    if not sources:

        # 返回人工介入动作，避免误判自动修复方向。
        return "ask_human"

    # 先取最高优先级的首个来源。
    str_primary_source = sources[0]  # 当前错误来源列表中的主来源

    # 根据主来源查默认动作，不存在时仍回退到 ask_human。
    str_action = ACTION_BY_SOURCE.get(  # 首个来源映射出的默认动作
        str_primary_source,  # 当前来源列表中的首要来源
        "ask_human",  # 缺省动作始终回退到人工确认
    )

    # 返回解析出的默认修复动作。
    return str_action

# 判断轨迹中是否已经做过 augment_tests 尝试。
def _has_prior_augment_tests(trace_events: list[dict[str, Any]]) -> bool:
    """判断轨迹是否已经出现过 augment_tests 动作。

    参数:
        trace_events: 当前阶段可用的轨迹事件列表。

    返回:
        轨迹中出现过 reflect + augment_tests 时返回 True。
    """

    # 顺序检查所有轨迹事件，寻找增强测试的历史动作。
    for dict_event in trace_events:

        # 当前事件名决定它是否属于反思阶段。
        str_event_name = str(dict_event.get("event", ""))  # 当前轨迹事件名称

        # 当前动作名用于判断是否已经尝试过增强测试。
        str_action_name = str(dict_event.get("action", ""))  # 当前轨迹动作名称

        # 一旦发现 reflect + augment_tests 组合即可返回真。
        if str_event_name == "reflect" and str_action_name == "augment_tests":

            # 返回已经尝试过增强测试的结果。
            return True

    # 遍历结束仍未发现对应动作时返回假。
    return False

# 根据语义摘要判断是否属于弱测试预言机场景。
def _has_weak_test_oracle(dict_semantic: dict[str, Any]) -> bool:
    """判断是否存在失配用例但缺少定位漂移。

    参数:
        dict_semantic: 当前阶段提取出的语义执行摘要。

    返回:
        有失配用例且没有 checkpoint 漂移时返回 True。
    """

    # 是否存在语义失配用例决定是否真的有失败证据。
    bool_has_mismatched_cases = bool(  # 是否存在失配用例证据
        dict_semantic.get("mismatched_cases")  # 语义摘要里的失配用例列表
    )

    # 是否缺少 checkpoint 漂移决定是否无法定位根因。
    bool_has_checkpoint_drift = bool(  # 是否已经具备 checkpoint 级定位线索
        dict_semantic.get("checkpoint_drift")  # 语义摘要里的 checkpoint 漂移列表
    )

    # 返回弱测试预言机判断结果。
    return bool_has_mismatched_cases and not bool_has_checkpoint_drift

# 根据结构化来源与上下文推断下一步建议动作。
def _recommended_next_action(
    dict_semantic: dict[str, Any],
    bool_prior_augments: bool,
    list_structured_sources: list[str],
) -> str:
    """返回诊断阶段建议的下一步动作。

    参数:
        dict_semantic: 当前阶段提取出的语义执行摘要。
        bool_prior_augments: 是否已经尝试过 augment_tests。
        list_structured_sources: 来自 gate 或 validation JSON 的结构化来源列表。

    返回:
        诊断阶段建议执行的下一步动作字符串。
    """

    # 规格规则含糊时优先请人确认，而不是盲目自动修复。
    if "spec_issue" in list_structured_sources:

        # 返回人工确认动作，避免继续放大规格歧义。
        return "ask_human"

    # 先根据语义摘要判断是否存在弱测试预言机场景。
    bool_weak_test_oracle = _has_weak_test_oracle(  # 当前是否属于弱测试预言机场景
        dict_semantic  # 当前门禁结果提炼出的语义定位摘要
    )

    # 首次遇到弱测试预言机时，应优先补测试和检查点。
    if bool_weak_test_oracle and not bool_prior_augments:

        # 返回增强测试动作，先补足定位证据。
        return "augment_tests"

    # 已经补过测试仍无法定位时，自动化价值已经有限。
    if bool_weak_test_oracle and bool_prior_augments:

        # 返回人工介入动作，避免重复消耗自动化轮次。
        return "ask_human"

    # 结构化来源明确指向依赖问题时优先修依赖。
    if "dependency_issue" in list_structured_sources:

        # 返回依赖修复动作，先处理上游问题。
        return "fix_dependency"

    # 仍存在失配用例时，默认继续局部重生成当前模块。
    if dict_semantic.get("mismatched_cases"):

        # 返回当前模块重生成动作。
        return "regenerate_current"

    # 没有额外线索时，仍优先回到当前模块做一次保守重生成。
    return "regenerate_current"

# 收集语义摘要里的失败用例编号。
def _collect_failing_cases(dict_semantic: dict[str, Any]) -> list[str]:
    """返回去重后的失败用例编号列表。

    参数:
        dict_semantic: 当前阶段提取出的语义执行摘要。

    返回:
        适合后续修复器引用的失败用例编号字符串列表。
    """

    # 结果列表按首次出现顺序记录失败用例编号。
    list_failing_cases: list[str] = []  # 对拍失败后需要回归的 case_id 字符串列表

    # 先吸收带 case_id 的失配用例。
    for dict_case in dict_semantic.get("mismatched_cases", []) or []:

        # 仅处理字典结构的失配项，避免异常对象影响流程。
        if isinstance(dict_case, dict):

            # 把 case_id 统一转成字符串，便于后续去重。
            str_case_id = str(dict_case.get("case_id"))  # 失配用例中的 case_id

            # 只有非空 case_id 才值得加入结果。
            if str_case_id:

                # 失配 case_id 需要按首次出现顺序收集。
                _append_unique_str(list_failing_cases, str_case_id)

    # 再补充 failed_cases 字段中的失败用例编号。
    for raw_case_id in dict_semantic.get("failed_cases", []) or []:

        # 把失败用例统一转成字符串，避免类型不一致。
        str_case_id = str(raw_case_id)  # failed_cases 中的 case_id 字符串

        # 失败 case_id 也需要按首次出现顺序收集。
        _append_unique_str(list_failing_cases, str_case_id)

    # 返回去重后的失败用例编号列表。
    return list_failing_cases

# 汇总语义摘要中暴露出来的漂移键信息。
def _collect_drift_keys(dict_semantic: dict[str, Any]) -> list[str]:
    """返回排序后的 checkpoint 漂移键列表。

    参数:
        dict_semantic: 当前阶段提取出的语义执行摘要。

    返回:
        去重并排序后的 checkpoint 漂移键字符串列表。
    """

    # 先用集合去重，再在最后统一排序。
    set_drift_keys: set[str] = set()  # 采集到的漂移键集合

    # 两类字段都可能携带 drift_keys，需要统一扫描。
    list_semantic_sources = [  # 需要统一扫描 drift_keys 的语义条目来源
        dict_semantic.get("mismatched_cases", []) or [],  # 语义失配条目列表
        dict_semantic.get("checkpoint_drift", []) or [],  # checkpoint 漂移条目列表
    ]

    # 逐组遍历语义来源，收集每个条目的 drift_keys。
    for list_semantic_items in list_semantic_sources:

        # 再逐个检查语义条目是否包含 drift_keys。
        for semantic_item in list_semantic_items:

            # 条目 helper 隔离类型判断和键规范化，主流程只负责跨来源聚合。
            set_drift_keys.update(_semantic_item_drift_keys(semantic_item))

    # 对去重后的漂移键排序，保证输出稳定。
    list_drift_keys = sorted(set_drift_keys)  # 已去重且按字典序稳定输出的漂移键列表

    # 返回排序后的漂移键列表。
    return list_drift_keys

# 单条语义记录 helper 负责规范化并去重漂移键。
def _semantic_item_drift_keys(semantic_item: Any) -> set[str]:
    """规范化单个语义条目携带的 drift_keys。

    参数:
        semantic_item: mismatched_cases 或 checkpoint_drift 中的单个条目。

    返回:
        已转成字符串并移除空值的漂移键集合。
    """

    # 非字典条目没有约定的 drift_keys 字段。
    if not isinstance(semantic_item, dict):

        # 空集合允许调用方直接执行 set.update。
        return set()

    # 集合推导同时完成字符串规范化、空值过滤和条目内去重。
    return {
        str(raw_drift_key)
        for raw_drift_key in semantic_item.get("drift_keys", []) or []
        if str(raw_drift_key)
    }

# 计算诊断结果中应该聚焦的可疑目标列表。
def _diagnosis_targets(
    plan: dict[str, Any],
    list_suspect_subfunctions: list[str],
    list_dependencies: list[str],
) -> list[str]:
    """返回诊断结果中的主可疑目标列表。

    参数:
        plan: 当前阶段对应的实现计划。
        list_suspect_subfunctions: 已定位到的可疑子函数列表。
        list_dependencies: 计划中声明的依赖名称列表。

    返回:
        供诊断结果使用的主可疑目标列表。
    """

    # 已经定位到具体子函数时，优先使用子函数集合。
    if list_suspect_subfunctions:

        # 返回已定位的可疑子函数列表。
        return list_suspect_subfunctions

    # 尚未定位到子函数但能看出依赖链时，回退到依赖列表。
    if list_dependencies:

        # 返回依赖名列表，提示先检查上游模块。
        return list_dependencies

    # 默认把当前计划名作为最后的定位兜底。
    str_plan_name = str(plan.get("name"))  # 当前计划名称字符串

    # 返回包含计划名的单元素列表作为兜底目标。
    return [str_plan_name]

# 根据漂移与疑点数量评估是否已经命中定位。
def _localization_hit(
    dict_semantic: dict[str, Any],
    list_suspect_subfunctions: list[str],
) -> bool:
    """判断诊断是否已经取得足够明确的定位线索。

    参数:
        dict_semantic: 当前阶段提取出的语义执行摘要。
        list_suspect_subfunctions: 已定位到的可疑子函数列表。

    返回:
        已具备 checkpoint 漂移或唯一可疑子函数时返回 True。
    """

    # 存在 checkpoint 漂移时，说明语义定位已经命中一部分路径。
    if dict_semantic.get("checkpoint_drift"):

        # checkpoint 漂移已经给出直接的定位信号。
        return True

    # 仅命中单个可疑子函数时，也视为较高置信度定位。
    if len(list_suspect_subfunctions) == 1:

        # 只有一个候选子函数时，定位已经足够聚焦。
        return True

    # 其他场景仍视为定位不充分。
    return False

# 构建差分诊断结果，供 repair plan 与 prompt 共享。
def build_diagnosis(
    plan: dict[str, Any],
    trace_events: list[dict[str, Any]] | None = None,
    validation_json: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据计划、轨迹与验证结果构建差分诊断。

    参数:
        plan: 当前阶段对应的实现计划。
        trace_events: 历史反思与验证轨迹事件列表。
        validation_json: 结构化 validation 报告对象。
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        供修复计划与提示词复用的结构化差分诊断结果。
    """

    # 诊断阶段先把缺失轨迹收敛成空列表，避免历史状态分支散落。
    list_trace_events: list[dict[str, Any]] = trace_events or []  # 当前诊断阶段可用的轨迹事件列表

    # 语义摘要负责承接语义执行、漂移与定位相关证据。
    dict_semantic = _semantic_summary(  # 汇总后续定位要依赖的语义证据
        validation_json,  # 从 validation 结果读取 semantic_execution 与失配线索
        stage_verification,  # 若 gate 已给出 ready/drift，就直接沿用其判定
    )

    # 可疑子函数列表帮助诊断定位当前最值得排查的模块。
    list_suspect_subfunctions = _suspect_subfunctions(  # 给修复器圈出首批排查目标
        plan,  # 提供计划里的子函数清单与依赖关系作为候选空间
        stage_verification,  # 直接读取 stage issue 文本里已经命中的子函数名
        dict_semantic,  # 再用 drift key 与置信度补充文本定位不足的部分
    )

    # 历史上是否已经尝试过 augment_tests 会影响下一步建议。
    bool_prior_augments = _has_prior_augment_tests(  # 判断补测试路线是否已尝试过
        list_trace_events  # 只需看历史事件序列就能判断是否做过 augment_tests
    )

    # 弱测试预言机场景表示已有失败证据但定位仍不够。
    bool_weak_test_oracle = _has_weak_test_oracle(  # 识别“有失败但还定不准”的局面
        dict_semantic  # 联合 mismatch、drift 与定位置信度做判定
    )

    # 结构化来源优先于文本猜测，用于建议下一步动作。
    list_structured_sources = (
        _sources_from_stage_verification(stage_verification)  # stage gate 已标注的错误来源
        or _sources_from_validation_json(validation_json)  # gate 缺失时再用 validation JSON 补来源
    )  # Stage gate 或 validation JSON 中的结构化来源

    # 规格歧义是优先级最高的人工确认条件。
    bool_ambiguous_spec_rule = "spec_issue" in list_structured_sources  # 是否存在规格歧义

    # 下一步动作需要综合语义线索、历史动作与结构化来源。
    str_recommended_next_action = _recommended_next_action(  # 当前诊断建议执行的下一步动作
        dict_semantic,  # 用于判断定位是否充分的语义摘要
        bool_prior_augments,  # 是否已经尝试过 augment_tests
        list_structured_sources,  # 已按优先级收集的结构化错误来源
    )

    # 失败用例编号用于提示修复器补足针对性验证。
    list_failing_cases = _collect_failing_cases(  # 抽出后续回归必须覆盖的失败 case
        dict_semantic  # 失败编号已经沉淀在语义摘要里，直接复用即可
    )

    # 漂移键信息用于定位最值得补充的检查点与依赖。
    list_drift_keys = _collect_drift_keys(  # 收集最能暴露偏移位置的 drift 键
        dict_semantic  # 漂移键既能回推子函数，也能提示该补哪些 checkpoint
    )

    # 依赖名列表用于缺少子函数定位时的上游回退。
    list_dependencies = _dependency_names(plan)  # 计划中声明的依赖名称列表

    # 诊断目标列表按子函数、依赖、计划名三级优先级选择。
    list_diagnosis_targets = _diagnosis_targets(  # 生成最终建议先看的定位目标
        plan,  # 必要时可从计划名回退，避免完全失去检索锚点
        list_suspect_subfunctions,  # 有明确命中的子函数时优先沿子函数层面排查
        list_dependencies,  # 子函数没有线索时，再退回到依赖链层面追根
    )

    # 自动调试是否已经耗尽，用于判断是否该切换人工介入。
    bool_auto_debug_exhausted = (
        bool_weak_test_oracle and bool_prior_augments  # 弱测试预言机且已补过测试时视为自动调试耗尽
    )  # 当前自动调试是否已经耗尽

    # 定位命中结果用于修复器评估是否继续本地化修复。
    bool_localization_hit = _localization_hit(  # 判断证据是否足以支撑定点修复
        dict_semantic,  # 结合 ready、drift 与失败样本看定位是否站得住
        list_suspect_subfunctions,  # 若连候选子函数都没有，就不能算定位命中
    )

    # 汇总结构化诊断结果，供 repair plan 与 prompt 共用。
    dict_diagnosis = {
        "version": 1,  # build_diagnosis 输出的固定版本号
        "semantic_ready": dict_semantic.get("semantic_ready"),  # 当前诊断看到的语义就绪位
        "failing_cases": list_failing_cases,  # 当前诊断汇总出的失败用例编号
        "checkpoint_drift": dict_semantic.get("checkpoint_drift", []),  # 当前诊断看到的 checkpoint 漂移明细
        "drift_keys": list_drift_keys,  # 从漂移明细抽取出的 checkpoint 键名
        "suspect_subfunctions": list_diagnosis_targets,  # 诊断阶段建议优先排查的目标集合
        "localization_confidence": dict_semantic.get("localization_confidence"),  # 语义摘要给出的定位置信度
        "weak_test_oracle": bool_weak_test_oracle,  # 失败存在但定位仍弱时的告警标记
        "ambiguous_spec_rule": bool_ambiguous_spec_rule,  # 是否已经命中规格歧义优先规则
        "recommended_next_action": str_recommended_next_action,  # 诊断阶段建议采取的下一步动作
        "suggested_case_ids": list_failing_cases,  # 后续回归必须优先覆盖的失败用例编号
        "suggested_checkpoints": list_drift_keys or list_dependencies,  # 优先补充的 checkpoint 或依赖线索
        "auto_debug_exhausted": bool_auto_debug_exhausted,  # 自动调试路径是否已经基本耗尽
        "auto_debug_before_human": bool_auto_debug_exhausted,  # 人工介入前是否已尝试完自动调试
        "localization_hit": bool_localization_hit,  # 诊断证据是否已经收敛到可直接下手的落点
    }  # 供 repair_plan 与 prompt 复用的诊断快照

    # 返回差分诊断结果。
    return dict_diagnosis

# 综合诊断与 gate 推荐，决定最终修复动作。
def _repair_action(
    sources: list[str],
    diagnosis: dict[str, Any],
    stage_verification: dict[str, Any] | None,
) -> str:
    """返回当前修复计划应执行的最终动作。

    参数:
        sources: 已按优先级排序的错误来源列表。
        diagnosis: 当前问题的结构化诊断结果。
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        修复计划最终采用的动作字符串。
    """

    # 诊断已经明确要求人工介入时，直接返回 ask_human。
    if diagnosis.get("recommended_next_action") == "ask_human":

        # 返回人工介入动作，避免后续逻辑覆盖诊断结论。
        return "ask_human"

    # 诊断已经明确要求增强测试时，直接保留该动作。
    if diagnosis.get("recommended_next_action") == "augment_tests":

        # 返回增强测试动作，优先补足定位证据。
        return "augment_tests"

    # 只有 stage_verification 真的是字典时才读取 gate 建议。
    if isinstance(stage_verification, dict):

        # gate 推荐动作可能比默认映射更贴近当前失败场景。
        str_gate_action = str(  # stage gate 推荐的动作字符串
            stage_verification.get("recommended_action", "")  # gate 推荐的动作字段
        )

        # gate 给出明确且非 ask_human 的动作时优先采用。
        if str_gate_action and str_gate_action != "ask_human":

            # 返回 gate 推荐动作，优先使用更强证据。
            return str_gate_action

    # 其余场景回退到来源驱动的默认修复动作。
    return resolution_action(sources)

# 构建后续修复器消费的结构化修复计划。
def build_repair_plan(
    report_text: str,
    plan: dict[str, Any],
    trace_events: list[dict[str, Any]] | None = None,
    validation_json: dict[str, Any] | None = None,
    artifact_graph: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回错误来源、动作与上下文需求组成的修复计划。

    参数:
        report_text: 原始验证报告文本。
        plan: 当前阶段对应的实现计划。
        trace_events: 历史反思与验证轨迹事件列表。
        validation_json: 结构化 validation 报告对象。
        artifact_graph: 当前阶段的工件依赖图摘要。
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        供 prompt 与人工介入逻辑复用的结构化修复计划。
    """

    # 修复计划阶段同样先把缺失轨迹规整为空列表，避免来源回退逻辑分叉。
    list_trace_events: list[dict[str, Any]] = trace_events or []  # 当前修复计划可用的轨迹事件列表

    # 修复动作判定先共享同一份诊断快照，避免来源分析与行动建议脱节。
    dict_diagnosis = build_diagnosis(  # 先统一诊断，再做动作分流
        plan,  # 提供计划骨架，便于诊断结果引用同一批子函数与依赖
        list_trace_events,  # 让动作决策知道哪些自动尝试已经失败过
        validation_json,  # 把 issue、metrics 与 message 一并纳入动作判断
        stage_verification,  # 沿用 gate 已给出的来源判断与推荐结论
    )  # 当前修复计划依赖的结构化诊断结果

    # 错误来源按 gate、validation JSON、自由文本三层证据逐级回退。
    list_sources = (
        _sources_from_stage_verification(stage_verification)  # 首选 gate 已结构化排序的来源
        or _sources_from_validation_json(validation_json)  # gate 缺证时改读 validation issues
        or classify_report(report_text, list_trace_events)  # 只有结构化来源都缺失时才回退文本推断
    )  # 当前修复计划识别出的错误来源列表

    # 主错误来源决定了后续 scope、上下文和人工问题模板。
    str_primary_source = (
        list_sources[0] if list_sources else "needs_human_intervention"  # 首个来源缺失时回退到人工介入
    )  # 当前修复计划的主错误来源

    # 修复动作会综合诊断结论与 gate 推荐做最终选择。
    str_action = _repair_action(  # 产出本轮真正执行的修复动作
        list_sources,  # 来源顺序决定动作分流优先级
        dict_diagnosis,  # 诊断快照补充定位强弱与自动调试耗尽状态
        stage_verification,  # 若 gate 已推荐动作，则把它作为重要参考
    )  # 当前修复计划的最终动作

    # 可疑工件列表帮助修复器聚焦到最值得修改的输出。
    list_suspect_artifacts = _suspect_artifacts(  # 汇总最值得优先检查的工件路径
        validation_json,  # 先读取 validation issue 里自带 path 的失败工件
        list_trace_events,  # 再从最近轨迹补输出目录、报告名与派生路径
        artifact_graph,  # 工件图能补 target 与节点关系，帮助串起上下游
        stage_verification,  # gate 若已点名失败工件，应直接并入候选列表
    )  # 当前失败最可疑的工件路径列表

    # augment_tests 场景固定只扩增测试与检查点，不直接改设计。
    if str_action == "augment_tests":

        # 在增强测试模式下，重生成范围固定为 tests_and_checkpoints。
        str_regeneration_scope = "tests_and_checkpoints"  # augment_tests 场景下的固定 scope

    # 其他动作按主来源和工件上下文推断重生成范围。
    else:

        # 常规动作根据主来源与可疑工件推断重生成范围。
        str_regeneration_scope = _regeneration_scope(  # 常规修复动作对应的重生成范围
            str_primary_source,  # 人工提问要围绕当前阻塞主因展开
            list_suspect_artifacts,  # 由图、报告与轨迹汇总出的可疑工件列表
        )

    # 所需上下文用于约束修复器必须读取哪些证据。
    list_required_context = _required_context(  # 修复器必须读取的证据上下文清单
        str_primary_source  # 当前动作推导所依据的主错误来源
    )

    # ask_human 场景需要向用户提出更精确的问题。
    if str_action == "ask_human":

        # 只在 ask_human 场景下构造具体的人机协同问题。
        str_human_question = _human_question(  # 生成需要用户补决策的定向问题
            str_primary_source,  # 主来源决定问题该落到规格、依赖还是验证维度
            validation_json,  # 优先复用结构化 issue 里最具体的报错措辞
            report_text,  # 结构化信息不够时再借原始报告补上下文
        )

    # 其他动作不需要额外的人机问题。
    else:

        # 非 ask_human 场景显式置空 human_question。
        str_human_question = None  # 非 ask_human 动作时无需额外构造人工问题

    # stage_ready 只在 stage_verification 为字典时才允许读取。
    if isinstance(stage_verification, dict):

        # 透传 stage gate 的 ready 标记，便于后续策略判断。
        bool_stage_ready = stage_verification.get("ready")  # 当前 stage gate 是否已判定 ready

    # 缺少 stage gate 时，ready 字段保持为空。
    else:

        # 没有 stage gate 结果时显式置空。
        bool_stage_ready = None  # 当前轮次没有 stage gate，因此 ready 状态未知

    # 汇总结构化修复计划，供 prompt 与人工介入逻辑复用。
    dict_repair_plan = {
        "error_sources": list_sources,  # 当前修复计划识别出的全部错误来源
        "primary_source": str_primary_source,  # 当前修复计划识别出的主错误来源
        "action": str_action,  # 当前修复计划最终采用的动作
        "suspect_artifacts": list_suspect_artifacts,  # 由报告、轨迹和工件图共同收敛出的高优先级产物
        "regeneration_scope": str_regeneration_scope,  # 本轮重生成或补修应聚焦的范围
        "required_context": list_required_context,  # 修复器必须先读取的证据清单
        "human_question": str_human_question,  # 留给人工补足关键决策的最终提问文本
        "needs_human_intervention": str_action == "ask_human",  # 当前动作是否已经切换到人工协同
        "plan_name": plan.get("name"),  # 当前 repair plan 对应的计划名称
        "stage_ready": bool_stage_ready,  # stage gate 是否认为当前阶段已经 ready
        "diagnosis": dict_diagnosis,  # 修复动作决策时复用的差分诊断快照
    }  # 当前问题的结构化修复计划

    # 返回结构化修复计划。
    return dict_repair_plan

# 从报告文本中提炼最多若干条可读观察。
def _report_observations(report_text: str) -> list[str]:
    """返回从文本报告中截取的非空观察行。

    参数:
        report_text: 原始验证报告文本。

    返回:
        按原始顺序截取的前若干条非空观察行列表。
    """

    # 结果列表按报告顺序保留非空观察行。
    list_observations: list[str] = []  # 从报告中提炼出的观察行

    # 逐行扫描文本报告，提炼前若干条非空观察。
    for str_line in report_text.splitlines():

        # 去掉首尾空白后再判断该行是否有效。
        str_stripped_line = str_line.strip()  # 当前报告行的去空白版本

        # 非空报告行才值得保留下来作为观察。
        if str_stripped_line:

            # 先记录当前有效观察行。
            list_observations.append(str_stripped_line)

            # 最多保留前八条观察，避免输出过长。
            if len(list_observations) >= 8:

                # 达到上限后立即停止扫描。
                break

    # 返回截取后的观察行列表。
    return list_observations

# 生成需要人工介入时的结构化提问对象。
def build_intervention(
    repair_plan: dict[str, Any],
    report_text: str,
    validation_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成 ask_human 场景使用的人工介入请求对象。

    参数:
        repair_plan: 当前问题的结构化修复计划。
        report_text: 原始验证报告文本。
        validation_json: 结构化 validation 报告对象。

    返回:
        供上层直接展示或转发的人工介入请求对象。
    """

    # 先尝试从结构化验证结果中提炼 issue 文本。
    list_issue_messages = _issue_messages(  # 结构化 issue 文本优先作为人工观察项
        validation_json  # 让结构化 issue 文本优先成为人工观察项
    )

    # 缺少结构化 issue 文本时，再回退到自由文本观察。
    if list_issue_messages:

        # 优先使用结构化 issue 文本作为 observations。
        list_observations = list_issue_messages  # 人工介入对象中的 observations 列表

    # 没有结构化 issue 时，从纯文本报告中截取观察行。
    else:

        # 回退到纯文本报告的前若干条观察。
        list_observations = _report_observations(  # 从纯文本报告截取的兜底观察项
            report_text  # 当结构化 issue 缺失时回退到原始报告文本
        )

    # 汇总人工介入请求对象，保持输出结构稳定。
    dict_intervention = {
        "version": 1,  # ask_human 载荷的固定 schema 版本
        "action": "ask_human",  # 当前人工介入请求的固定动作
        "primary_source": repair_plan.get("primary_source"),  # 当前最需要人工裁决的阻塞来源
        "question": repair_plan.get("human_question")  # repair plan 已生成的定向澄清问题
        or "Please clarify the unresolved hardware-design ambiguity.",  # 缺省时退回通用硬件歧义澄清语句
        "observations": list_observations,  # 需要先呈现给人工的失败观察列表
        "attempted_actions": repair_plan.get("required_context", []),  # 已要求修复器读取过的上下文清单
        "expected_answer_format": {  # 建议人工回复时遵循的字段骨架
            "decision": "one concise design decision or debugging direction",  # 需要用户给出的核心决策
            "evidence": "spec section, waveform observation, or tool report line when available",  # 支撑该决策的证据形式
            "constraints": "any new interface/timing/resource constraints",  # 新增约束应明确写出的类别
        },  # 人工回答建议采用的结构
    }  # ask_human 场景输出的结构化人工介入请求

    # 返回人工介入请求对象。
    return dict_intervention

# 把最近若干次轨迹事件压缩成 prompt 友好的摘要。
def _trajectory_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """返回最近轨迹事件的轻量摘要。

    参数:
        events: 当前阶段可用的完整轨迹事件列表。

    返回:
        只保留关键调试字段的最近轨迹摘要列表。
    """

    # 结果列表按事件顺序记录轻量化后的轨迹摘要。
    list_summary: list[dict[str, Any]] = []  # 轨迹事件的轻量摘要列表

    # 只保留最近若干次事件，避免 prompt 膨胀。
    list_recent_events = events[-MAX_TRAJECTORY_EVENTS:]  # 限制 prompt 膨胀后的最近轨迹窗口

    # 逐个把事件裁剪成 prompt 需要的字段。
    for dict_event in list_recent_events:

        # 当前事件的轻量摘要只保留关键调试字段。
        dict_event_summary = {
            "event": dict_event.get("event"),  # 轨迹事件名称
            "attempt_id": dict_event.get("attempt_id"),  # 当前尝试轮次标识
            "stage": dict_event.get("stage"),  # 当前轨迹记录所处的阶段名称
            "readiness": dict_event.get("readiness"),  # 当前尝试记录的就绪度状态
            "ok": dict_event.get("ok"),  # 本次事件是否走到成功出口
            "errors": dict_event.get("errors"),  # 事件里记录的错误摘要集合
            "warnings": dict_event.get("warnings"),  # 事件里记录的警告摘要集合
            "error_sources": dict_event.get("error_sources", []),  # 该事件归档过的结构化来源标签
            "action": dict_event.get("action"),  # 事件当时实际执行的修复或验证动作
        }  # 单条轨迹事件的轻量摘要

        # 把当前事件摘要追加到结果列表。
        list_summary.append(dict_event_summary)

    # 返回轻量化后的轨迹摘要列表。
    return list_summary

# 从 validation JSON 中提取并排序错误来源。
def _sources_from_validation_json(
    validation_json: dict[str, Any] | None,
) -> list[str]:
    """从 validation JSON 中提取错误来源列表。

    参数:
        validation_json: 结构化 validation 报告对象。

    返回:
        先 error 后 warning 的去重错误来源列表。
    """

    # 缺少 validation JSON 时直接返回空列表。
    if not validation_json:

        # 返回空来源列表，表示没有结构化 validation 证据。
        return []

    # error 级来源优先于 warning 级来源进入最终列表。
    list_error_sources: list[str] = []  # severity 为 error 的来源列表

    # 其他来源延后追加，保留较低优先级提示。
    list_other_sources: list[str] = []  # 非 error severity 的来源列表

    # 逐个检查 validation issues，提取受支持的 source 字段。
    for issue_item in validation_json.get("issues", []) or []:

        # 这里只接受字典 issue，保证 source 与 severity 可安全读取。
        if not isinstance(issue_item, dict):

            # 结构异常的 stage issue 既不可信也无法稳定分类。
            continue

        # 这里直接取 validation issue 自带的 source 标签，不再做文本猜测。
        str_source = str(issue_item.get("source", ""))  # 当前 validation issue 的 source 字符串

        # 未纳入 ERROR_SOURCES 的标签，说明当前模块还不会基于它做动作分流。
        if str_source not in ERROR_SOURCES:

            # 未知来源不参与当前模块的结构化分类。
            continue

        # validation issue 的 severity 只影响主来源桶与次级来源桶的归属。
        str_severity = str(  # 先把等级标签收敛成统一比较格式，避免大小写混用
            issue_item.get("severity", "")  # issue 上报的原始 severity 文本
        ).lower()  # 统一后的 stage issue severity 文本

        # error 级来源优先进入高优先级来源桶。
        if str_severity == "error":

            # 只在首次出现时把来源放入 error 桶。
            _append_unique_str(list_error_sources, str_source)

        # 其他 severity 的来源放入次级来源桶。
        else:

            # 只在首次出现时把来源放入次级来源桶。
            _append_unique_str(list_other_sources, str_source)

    # 先复制 error 桶，后续再补充未出现过的次级来源。
    list_sources = list(list_error_sources)  # 合并前的高优先级来源副本

    # 把未进入 error 桶的其他来源追加到结果末尾。
    for str_source in list_other_sources:

        # 次级来源只有尚未出现在结果中时才追加。
        if str_source not in list_sources:

            # 把次级来源追加到结果列表末尾。
            list_sources.append(str_source)

    # 返回合并后的来源列表。
    return list_sources

# 从 stage verifier 结果中提取并排序错误来源。
def _sources_from_stage_verification(
    stage_verification: dict[str, Any] | None,
) -> list[str]:
    """从 stage verifier 结果中提取错误来源列表。

    参数:
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        综合 error_sources 与 issues 的去重错误来源列表。
    """

    # 缺少 stage verifier 结果时直接返回空列表。
    if not stage_verification:

        # 返回空来源列表，表示没有 stage gate 证据。
        return []

    # 显式 error_sources 字段拥有最高优先级。
    list_sources: list[str] = []  # stage verifier 中的主来源列表

    # warning 级来源在主来源之后追加。
    list_warning_sources: list[str] = []  # stage verifier 中的 warning 来源列表

    # 先读取 verifier 直接给出的 error_sources 字段。
    for raw_source in stage_verification.get("error_sources", []) or []:

        # 统一把来源项转换成字符串，避免异常类型影响判断。
        str_source = str(raw_source)  # stage verifier 显式给出的 source 字符串

        # 只保留受支持且尚未出现过的来源项。
        if str_source in ERROR_SOURCES:

            # 显式来源按首次出现顺序加入主来源列表。
            _append_unique_str(list_sources, str_source)

    # 再扫描 issue 明细，补充 error 与 warning 来源。
    for issue_item in stage_verification.get("issues", []) or []:

        # 先做结构检查，避免后面读取 source/severity 时碰到脏数据。
        if not isinstance(issue_item, dict):

            # 非字典条目没有稳定字段可读，直接跳过即可。
            continue

        # 这里只接受 stage verifier 显式上报的 source，不额外猜测文本含义。
        str_source = str(issue_item.get("source", ""))  # 当前 stage verifier issue 的 source 字段

        # 非受支持来源不参与当前模块的结构化分类。
        if str_source not in ERROR_SOURCES:

            # 跳过当前不受支持的来源类型。
            continue

        # severity 会影响该来源能否进入高优先级来源列表。
        str_severity = str(  # 统一 severity 大小写，便于后续一致比较
            issue_item.get("severity", "")  # stage issue 随条目携带的原始等级标签
        ).lower()  # 归一化后的 severity 文本

        # error 级来源需要提升到主来源列表。
        if str_severity == "error":

            # 错误级来源必须立即提升到主来源列表。
            _append_unique_str(list_sources, str_source)

        # 非 error 级来源延后追加为 warning 来源。
        else:

            # warning 来源只在尚未出现在主列表时才记录。
            if str_source not in list_sources:

                # 把 warning 来源记录到次级列表。
                _append_unique_str(list_warning_sources, str_source)

    # 先复制主来源，再补充 warning 来源。
    list_combined_sources = list(list_sources)  # 合并前的主来源副本

    # 把未进入主列表的 warning 来源补到结果末尾。
    for str_source in list_warning_sources:

        # warning 来源只有尚未出现时才追加。
        if str_source not in list_combined_sources:

            # 把 warning 来源追加到结果列表末尾。
            list_combined_sources.append(str_source)

    # stage gate 的 error 与 warning 来源已经按优先级合并完成。
    return list_combined_sources

# 结合多类证据汇总最可疑的工件路径。
def _suspect_artifacts(
    validation_json: dict[str, Any] | None,
    trace_events: list[dict[str, Any]],
    artifact_graph: dict[str, Any] | None = None,
    stage_verification: dict[str, Any] | None = None,
) -> list[str]:
    """返回当前失败最可疑的工件路径列表。

    参数:
        validation_json: 结构化 validation 报告对象。
        trace_events: 当前阶段可用的轨迹事件列表。
        artifact_graph: 当前阶段的工件依赖图摘要。
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        按证据优先级去重后的可疑工件路径列表。
    """

    # 结果列表按证据强度与出现顺序记录可疑工件。
    list_artifacts: list[str] = []  # 汇总后的可疑工件路径列表

    # 图推断出的可疑工件优先进入结果。
    for raw_artifact in suspect_artifacts_from_graph(artifact_graph):

        # 工件路径统一转成字符串，便于比较与输出。
        str_artifact = str(raw_artifact)  # 工件图提供的工件路径字符串

        # 图推断出的工件按首次出现顺序追加。
        _append_unique_str(list_artifacts, str_artifact)

    # validation JSON 中的 issues 也可能直接指向问题路径。
    for issue_item in (validation_json or {}).get("issues", []) or []:

        # stage gate 只有显式给出 path，才能把失败直接落到具体产物。
        if isinstance(issue_item, dict) and issue_item.get("path"):

            # 把 issue path 统一转换成字符串。
            str_issue_path = str(issue_item["path"])  # validation issue 指向的工件路径

            # validation issue 指向的路径按首次出现顺序追加。
            _append_unique_str(list_artifacts, str_issue_path)

    # stage verifier issues 同样可能直接标明失败工件。
    for issue_item in (stage_verification or {}).get("issues", []) or []:

        # 没有 path 的 issue 只能说明失败类型，无法直接落到具体工件。
        if isinstance(issue_item, dict) and issue_item.get("path"):

            # 这里抽取的是 stage gate 已明确指出的失败工件路径。
            str_issue_path = str(issue_item["path"])  # stage gate 已定位出的工件路径

            # 让 gate 指出的路径优先进入可疑工件集合。
            _append_unique_str(list_artifacts, str_issue_path)

    # 再从最近若干次轨迹事件中补充输出、路径与报告文件线索。
    for dict_event in trace_events[-MAX_TRACE_ARTIFACT_EVENTS:]:

        # 逐个扫描轨迹事件中可能承载工件路径的字段。
        for str_key in ("output", "path", "report"):

            # 当前字段值可能就是产物路径或报告路径。
            raw_value = dict_event.get(str_key)  # 当前轨迹字段对应的原始值

            # 只有字段非空时才值得作为可疑工件线索。
            if raw_value:

                # 把轨迹字段统一转换成字符串路径。
                str_value = str(raw_value)  # 轨迹事件中的工件线索字符串

                # 把轨迹字段值按首次出现顺序追加。
                _append_unique_str(list_artifacts, str_value)

    # 图、报告和轨迹三路证据已经汇总完成。
    return list_artifacts

# 从完整工件图中裁剪 prompt 需要的轻量摘要。
def _graph_summary(artifact_graph: dict[str, Any] | None) -> dict[str, Any]:
    """返回适合嵌入 prompt 的工件图摘要。

    参数:
        artifact_graph: 当前阶段的完整工件依赖图对象。

    返回:
        只保留关键统计与可疑工件的轻量摘要字典。
    """

    # 缺少工件图时直接返回空摘要。
    if not artifact_graph:

        # 返回空字典，表示当前没有可用工件图上下文。
        return {}

    # 先提取工件图里推断出的可疑工件列表。
    list_suspect_artifacts = suspect_artifacts_from_graph(  # 工件图直接推断出的可疑产物
        artifact_graph  # 当前阶段的完整工件依赖图对象
    )

    # 汇总适合 prompt 的轻量工件图摘要。
    dict_graph_summary = {
        "name": artifact_graph.get("name"),  # 工件图名称
        "target": artifact_graph.get("target"),  # 工件图指向的当前目标名称
        "node_count": len(artifact_graph.get("nodes", []) or []),  # 工件图节点数量
        "edge_count": len(artifact_graph.get("edges", []) or []),  # 工件图边数量
        "suspect_artifacts": list_suspect_artifacts,  # 工件图单独推断出的可疑产物集合
    }  # 工件图的轻量摘要结果

    # 返回轻量工件图摘要。
    return dict_graph_summary

# 根据主来源与可疑工件推断合适的重生成范围。
def _regeneration_scope(
    primary_source: str,
    suspect_artifacts: list[str],
) -> str:
    """返回当前修复动作建议的重生成范围。

    参数:
        primary_source: 当前修复计划识别出的主错误来源。
        suspect_artifacts: 当前失败最可疑的工件路径列表。

    返回:
        对应修复动作建议采用的重生成范围字符串。
    """

    # 规格问题必须先回到计划与输出契约层面修正。
    if primary_source == "spec_issue":

        # 返回计划与请求输出级别的重生成范围。
        return "plan_and_requested_outputs"

    # 依赖问题优先回到依赖子函数链路进行修复。
    if primary_source == "dependency_issue":

        # 该问题更像是上游子函数接口或实现异常。
        return "dependency_subfunctions"

    # testbench 问题应聚焦在验证资产与参考向量。
    if primary_source == "testbench_issue":

        # 该问题更像是验证资产而非设计逻辑本身。
        return "testbench_and_reference_vectors"

    # 工具链问题需要先修复环境与配置，而不是改设计。
    if primary_source == "toolchain_issue":

        # 该问题首先需要恢复工具链、cfg 或环境可用性。
        return "toolchain_configuration"

    # 自动定位不足或明确要求人工时，不应继续自动重生成。
    if primary_source in {"insufficient_debug", "needs_human_intervention"}:

        # 继续自动重生成的价值不高，应等待新的人工指令。
        return "blocked_until_human_guidance"

    # 已经有明确可疑工件时，只需局部重生成当前模块。
    if suspect_artifacts:

        # 返回局部当前模块范围，避免扩大修改面。
        return "current_module_only"

    # 其余情况默认回到当前阶段整体重跑。
    return "current_stage"

# 为不同错误来源给出必须携带的上下文清单。
def _required_context(primary_source: str) -> list[str]:
    """返回当前主来源对应的必需上下文列表。

    参数:
        primary_source: 当前修复计划识别出的主错误来源。

    返回:
        修复器必须优先读取的上下文清单。
    """

    # 把各类来源映射到后续修复器必须读取的上下文集合。
    dict_context_by_source = {
        "spec_issue": ["evidence.json", "plan.json", "audit.md"],  # 规格冲突时必须回看的证据清单
        "dependency_issue": [  # 依赖链问题要求带上的关键上下文
            "upstream manifests",  # 上游工件与依赖声明摘要
            "subfunction interfaces",  # 子函数接口契约与端口定义
            "failing case ids",  # 最近失败用例编号与触发条件
        ],  # 依赖链问题需要回看的上下文集合
        "testbench_issue": [  # 验证资产异常时必须先核对的材料
            "semantic vectors",  # 参考向量与黄金数据来源
            "testbench file",  # 自检 testbench 或驱动脚本正文
            "validation report",  # 最近一次失败时的验证日志
        ],  # testbench 问题对应的上下文集合
        "current_module_issue": [  # 当前模块本体异常时必须对照的材料
            "current source file",  # 当前生成出的 RTL 或脚本正文
            "prior-stage artifact",  # 上一阶段稳定产物或中间导出物
            "tool output",  # 工具侧报出的综合、lint 或仿真信息
        ],  # 当前模块问题对应的上下文集合
        "toolchain_issue": ["tool path", "cfg file", "environment setup"],  # 工具链问题要求回看的环境材料
        "insufficient_debug": [  # 调试证据不足时需要额外补齐的材料
            "waveform/logs",  # 波形、日志等运行期观测证据
            "failing vectors",  # 触发失败的关键输入向量
            "suspect dependency list",  # 当前怀疑会传染故障的依赖列表
        ],  # 调试证据不足时需要补齐的上下文集合
        "needs_human_intervention": [  # 进入人工协同时必须附带的说明材料
            "spec evidence",  # 需要人工决策时必须附带的规格证据
            "attempt history",  # 已尝试路线与失败历史摘要
            "precise open question",  # 需要人工直接回答的精确问题
        ],  # 人工介入场景对应的上下文集合
    }  # 错误来源到必需上下文清单的映射

    # 读取当前主来源对应的上下文清单，缺失时回退到通用集合。
    list_required_context = dict_context_by_source.get(  # 当前来源对应的上下文清单
        primary_source,  # 缺失映射时回退到通用报告与轨迹上下文
        ["validation report", "trace"],  # 未命中映射时回退到通用报告与轨迹上下文
    )

    # 返回上下文清单。
    return list_required_context

# 在 ask_human 场景下构造最有针对性的问题。
def _human_question(
    primary_source: str,
    validation_json: dict[str, Any] | None,
    report_text: str,
) -> str:
    """返回 ask_human 场景下应抛给用户的问题。

    参数:
        primary_source: 当前修复计划识别出的主错误来源。
        validation_json: 结构化 validation 报告对象。
        report_text: 原始验证报告文本。

    返回:
        最适合当前阻塞原因的人机协同问题文本。
    """

    # 测试覆盖不足时，优先向用户请求额外波形或检查点。
    if primary_source == "insufficient_debug":

        # 返回缺少定位证据时的标准追问。
        return (
            "The current tests cannot pinpoint the failing subfunction. "
            "Which additional waveform, intermediate signal, or reference "
            "checkpoint should be used?"
        )

    # 规格冲突时，优先要求用户明确约束优先级。
    if primary_source == "spec_issue":

        # 返回规格歧义场景下的标准追问。
        return (
            "Which source requirement or interface constraint should take "
            "precedence for the missing or conflicting spec item?"
        )

    # 先尝试从结构化 issue 文本中挑选最具体的 blocker。
    list_messages = _issue_messages(validation_json)  # issue 文本候选列表

    # 存在结构化 issue 文本时，直接基于首条 issue 追问。
    if list_messages:

        # 返回基于首条 issue 的人工追问。
        return f"Please resolve this blocker: {list_messages[0]}"

    # 默认回退到报告中的第一条非空文本。
    list_report_observations = _report_observations(  # 从自由文本报告提炼的观察候选
        report_text  # 从原始报告里抽取第一条可读观察
    )

    # 报告中存在有效观察时，优先使用第一条作为追问。
    if list_report_observations:

        # 返回来自文本报告首条观察的人工追问。
        return list_report_observations[0]

    # 其余情况统一回退到通用硬件设计歧义追问。
    return "Please clarify the unresolved hardware-design ambiguity."

# 提取 validation JSON 中的可读 issue 文本。
def _issue_messages(validation_json: dict[str, Any] | None) -> list[str]:
    """返回 validation JSON 中的 issue message 列表。

    参数:
        validation_json: 结构化 validation 报告对象。

    返回:
        按原始顺序抽取出的 issue message 字符串列表。
    """

    # 结果列表按 issues 原始顺序保留 message 文本。
    list_messages: list[str] = []  # 供人工问题与 observations 复用的 issue 文本列表

    # 逐个检查 validation issues，抽取 message 字段。
    for issue_item in (validation_json or {}).get("issues", []) or []:

        # 只有包含 message 的字典 issue 才值得保留。
        if isinstance(issue_item, dict) and issue_item.get("message"):

            # 把 message 统一转换成字符串，避免类型不一致。
            str_message = str(issue_item["message"])  # 当前 issue 的 message 文本

            # 把当前 issue 文本追加到结果列表。
            list_messages.append(str_message)

    # 返回按原始顺序收集的 issue 文本列表。
    return list_messages

# 从 stage gate 或 validation JSON 中提取语义执行摘要。
def _semantic_summary(
    validation_json: dict[str, Any] | None,
    stage_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    """返回语义执行相关的统一摘要。

    参数:
        validation_json: 结构化 validation 报告对象。
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        统一字段名后的语义执行摘要字典。
    """

    # stage verifier 一旦显式提供语义字段，应优先采用它。
    if isinstance(stage_verification, dict):

        # 逐个检查关键语义字段是否出现在 stage gate 中。
        for str_key in SEMANTIC_KEYS:

            # 命中任一语义字段后，就以 stage gate 结果为准。
            if str_key in stage_verification:

                # 汇总 stage gate 提供的语义摘要字段。
                dict_stage_semantic = {
                    "semantic_ready": stage_verification.get("semantic_ready"),  # stage gate 是否给出可判定语义结果
                    "mismatched_cases": stage_verification.get("mismatched_cases", []),  # stage gate 上报的失配用例列表
                    "checkpoint_drift": stage_verification.get("checkpoint_drift", []),  # stage gate 识别到的 checkpoint 漂移列表
                    "failed_cases": stage_verification.get("failed_cases", []),  # stage gate 记录的失败用例编号
                    "localization_confidence": stage_verification.get("localization_confidence"),  # stage gate 给出的定位置信度
                }  # 来自 stage verifier 的语义摘要

                # 返回来自 stage gate 的语义摘要。
                return dict_stage_semantic

    # validation JSON 里的 metrics 节点承接离线语义执行摘要。
    if isinstance(validation_json, dict):

        # 先取出 metrics 节点，准备继续读取语义执行摘要。
        dict_metrics = validation_json.get("metrics", {})  # validation JSON 中的 metrics 节点

    # 缺少 validation JSON 时，metrics 退回为空字典。
    else:

        # 没有 validation JSON 时显式使用空 metrics。
        dict_metrics = {}  # 缺少 validation JSON 时的 metrics 占位

    # 只有 metrics 真的是字典时，才继续读取 semantic_execution。
    if isinstance(dict_metrics, dict):

        # 再从 metrics 中读取 semantic_execution 摘要。
        dict_semantic_execution = dict_metrics.get(  # metrics 节点中的语义执行摘要对象
            "semantic_execution",  # validation metrics 下的语义执行节点名
            {},
        )

    # 非字典 metrics 无法稳定读取字段，回退为空对象。
    else:

        # metrics 结构异常时，语义摘要直接回退为空对象。
        dict_semantic_execution = {}  # metrics 异常时使用的空语义摘要对象

    # 只有语义摘要节点确实是字典时才值得继续透传。
    if not isinstance(dict_semantic_execution, dict):

        # 返回空字典，表示当前没有可用语义摘要。
        return {}

    # 汇总 validation JSON 中真正与语义定位相关的字段。
    dict_semantic = {
        "semantic_ready": dict_semantic_execution.get("semantic_ready"),  # validation metrics 中的语义就绪位
        "mismatched_cases": dict_semantic_execution.get("mismatched_cases", []),  # validation metrics 中的失配用例列表
        "checkpoint_drift": dict_semantic_execution.get("checkpoint_drift", []),  # validation metrics 里的 checkpoint 漂移列表
        "failed_cases": dict_semantic_execution.get("failed_cases", []),  # validation metrics 里的失败用例编号
        "localization_confidence": dict_semantic_execution.get("localization_confidence"),  # 离线语义执行对定位稳定性的自评等级
    }  # 从 validation metrics 归一化出的语义摘要

    # 返回 validation JSON 提供的语义摘要。
    return dict_semantic

# 从计划中收集所有依赖名称。
def _dependency_names(plan: dict[str, Any]) -> list[str]:
    """返回计划中声明过的依赖名称列表。

    参数:
        plan: 当前阶段对应的实现计划。

    返回:
        按首次出现顺序收集的依赖名称字符串列表。
    """

    # 结果列表直接服务于依赖修复与 suggested_checkpoints 回退逻辑。
    list_dependency_names: list[str] = []  # 供 suggested_checkpoints 和依赖修复共用的依赖名列表

    # 逐个扫描子函数定义，提取 dependencies 字段。
    for subfunction_item in plan.get("subfunctions", []) or []:

        # 只处理字典结构的子函数定义。
        if not isinstance(subfunction_item, dict):

            # 非字典子函数定义无法稳定读取字段，直接跳过。
            continue

        # 再逐个读取当前子函数声明的依赖名称。
        for raw_dependency in subfunction_item.get("dependencies", []) or []:

            # 依赖名统一转成字符串，避免类型不一致。
            str_dependency_name = str(raw_dependency)  # 当前依赖名称字符串

            # 依赖名称按首次出现顺序收集。
            _append_unique_str(list_dependency_names, str_dependency_name)

    # 返回依赖名称列表。
    return list_dependency_names

# 从计划中收集所有具名子函数名称。
def _subfunction_names(plan: dict[str, Any]) -> list[str]:
    """返回计划中所有具名子函数名称。

    参数:
        plan: 当前阶段对应的实现计划。

    返回:
        按首次出现顺序收集的具名子函数名称列表。
    """

    # 结果列表按计划中的原始顺序保留子函数名称。
    list_subfunction_names: list[str] = []  # 计划中的具名子函数名称列表

    # 逐个扫描子函数定义，收集 name 字段。
    for subfunction_item in plan.get("subfunctions", []) or []:

        # 只有带 name 的字典子函数定义才参与收集。
        if isinstance(subfunction_item, dict) and subfunction_item.get("name"):

            # 把子函数名统一转成字符串。
            str_subfunction_name = str(  # 把子函数名称规范成可匹配的字符串
                subfunction_item["name"]  # 当前循环命中的子函数 name 字段
            )  # 当前子函数名称字符串

            # 子函数名称按首次出现顺序收集。
            _append_unique_str(list_subfunction_names, str_subfunction_name)

    # 返回具名子函数名称列表。
    return list_subfunction_names

# 从 stage verifier issue 文本中收集命中的子函数名。
def _suspects_from_stage_issues(
    list_plan_subfunction_names: list[str],
    stage_verification: dict[str, Any] | None,
) -> list[str]:
    """从 stage verifier issue 文本中提取可疑子函数。

    参数:
        list_plan_subfunction_names: 计划中所有具名子函数名称。
        stage_verification: stage verifier 输出的结构化 gate 结果。

    返回:
        在 issue message 中命中的子函数名称列表。
    """

    # 结果列表按首次命中顺序记录文本命中的子函数。
    list_stage_suspects: list[str] = []  # 由 stage issue message 命中的子函数列表

    # 缺少 stage gate 时无需继续扫描 issue 文本。
    if not isinstance(stage_verification, dict):

        # 返回空列表，表示当前没有可扫描的 stage issue 文本。
        return []

    # 顺序扫描 stage issues 的 message 字段，寻找子函数名命中。
    for issue_item in stage_verification.get("issues", []) or []:

        # 非字典 issue 无法稳定读取 message 字段。
        if not isinstance(issue_item, dict):

            # 跳过结构异常的 issue 条目。
            continue

        # 把 message 统一转成字符串，便于做包含匹配。
        str_issue_message = str(issue_item.get("message", ""))  # 当前 stage issue 的 message 正文

        # 逐个检查子函数名是否出现在当前 issue 文本中。
        for str_subfunction_name in list_plan_subfunction_names:

            # 文本命中子函数名时，说明该子函数值得优先排查。
            if str_subfunction_name in str_issue_message:

                # 把当前命中的子函数加入结果列表。
                _append_unique_str(list_stage_suspects, str_subfunction_name)

    # 返回 issue 文本命中的子函数列表。
    return list_stage_suspects

# 从子函数定义中收集 checkpoint 信号名集合。
def _checkpoint_signals(subfunction_item: dict[str, Any]) -> set[str]:
    """从单个子函数定义中提取 checkpoint 信号集合。

    参数:
        subfunction_item: 单个子函数的计划定义字典。

    返回:
        当前子函数声明过的 checkpoint 信号名称集合。
    """

    # 结果集合用于和 drift key 做高效的包含匹配。
    set_checkpoint_signals: set[str] = set()  # 当前子函数声明的 checkpoint 信号名称集合

    # 顺序扫描 semantic_checkpoints，提取其中声明的信号名。
    for checkpoint_item in subfunction_item.get("semantic_checkpoints", []) or []:

        # 非字典 checkpoint 无法安全读取 signals 列表。
        if not isinstance(checkpoint_item, dict):

            # 结构异常的 checkpoint 条目不参与信号回推。
            continue

        # 当前 checkpoint 中显式声明的信号列表可能为空。
        list_signals = checkpoint_item.get("signals", []) or []  # 当前 checkpoint 定义的信号列表

        # 把所有非空信号名统一加入集合，供 drift 匹配复用。
        for raw_signal in list_signals:

            # 信号名统一转成字符串，避免类型不一致。
            str_signal = str(raw_signal)  # 当前 checkpoint 信号名字符串

            # 非空信号名才值得作为 drift 匹配线索。
            if str_signal:

                # 记录当前 checkpoint 信号名，便于稍后匹配 drift key。
                set_checkpoint_signals.add(str_signal)

    # 返回当前子函数声明的 checkpoint 信号集合。
    return set_checkpoint_signals

# 判断单个子函数是否命中了任一 drift key。
def _matches_drift_keys(
    str_subfunction_name: str,
    set_checkpoint_signals: set[str],
    list_drift_keys: list[str],
) -> bool:
    """判断子函数名或其 checkpoint 信号是否命中任一 drift key。

    参数:
        str_subfunction_name: 当前被检查的子函数名称。
        set_checkpoint_signals: 当前子函数声明的 checkpoint 信号集合。
        list_drift_keys: 已去重并排序的 drift key 列表。

    返回:
        子函数名或任一 checkpoint 信号命中 drift key 时返回 True。
    """

    # 逐个扫描 drift key，寻找名称或信号级命中。
    for str_drift_key in list_drift_keys:

        # 子函数名直接命中 drift key 时，可视为高置信定位。
        if str_subfunction_name in str_drift_key:

            # 返回命中结果，避免继续扫描其他 drift key。
            return True

        # 再逐个检查 checkpoint 信号是否命中当前 drift key。
        for str_signal in set_checkpoint_signals:

            # 信号命中 drift key 时，同样说明该子函数值得优先排查。
            if str_signal and str_signal in str_drift_key:

                # 返回命中结果，避免继续做重复判断。
                return True

    # 所有 drift key 都未命中时返回假。
    return False

# 根据 issue 文本与 checkpoint 漂移推断可疑子函数。
def _suspect_subfunctions(
    plan: dict[str, Any],
    stage_verification: dict[str, Any] | None,
    semantic: dict[str, Any],
) -> list[str]:
    """返回最值得优先排查的可疑子函数列表。

    参数:
        plan: 当前阶段对应的实现计划。
        stage_verification: stage verifier 输出的结构化 gate 结果。
        semantic: 当前阶段提取出的语义执行摘要。

    返回:
        按定位证据优先级去重后的可疑子函数名称列表。
    """

    # 子函数名称清单会被 issue 文本扫描和 drift 匹配共同复用。
    list_plan_subfunction_names = _subfunction_names(  # 计划里所有具名子函数名称
        plan  # 先收集计划里所有具名子函数
    )  # 当前计划中所有具名子函数名称

    # 先利用 stage issue 文本拿到一批直观命中的候选子函数。
    list_suspects = _suspects_from_stage_issues(  # 先从 stage issue 文本抽第一批命中项
        list_plan_subfunction_names,  # 用计划里的子函数名逐个匹配 issue 文本
        stage_verification,  # stage gate 的 issue 列表是最直接的文本定位入口
    )  # 由 stage issue 文本直接命中的可疑子函数列表

    # checkpoint 漂移键用于做更细粒度的定位回推。
    list_drift_keys = _collect_drift_keys(  # 再利用 drift 键把候选范围继续收窄
        semantic  # drift 键通常能把问题指向更具体的检查点或子函数
    )  # 当前语义摘要暴露出的 drift key 列表

    # 存在漂移键时，再逐个对子函数做信号级匹配。
    if list_drift_keys:

        # 顺序扫描计划中的子函数定义，寻找和 drift key 最相关的对象。
        for subfunction_item in plan.get("subfunctions", []) or []:

            # 只有带 name 的字典子函数定义才值得继续匹配。
            if not isinstance(subfunction_item, dict) or not subfunction_item.get("name"):

                # 缺少名字或结构异常的子函数定义无法参与定位。
                continue

            # 统一字符串形式后，才能稳定和 drift key 做包含匹配。
            str_subfunction_name = str(  # 当前候选子函数的规范化名称
                subfunction_item["name"]  # 读取计划里声明的子函数 name 字段
            )  # 用于与 drift key 比对的子函数名

            # 先收集该子函数声明过的 checkpoint 信号集合。
            set_checkpoint_signals = _checkpoint_signals(  # 当前子函数声明过的 checkpoint 信号集合
                subfunction_item  # 当前子函数的计划定义字典
            )  # 该子函数声明过的 checkpoint 信号集合

            # 命中 drift key 时，把当前子函数加入可疑列表。
            if _matches_drift_keys(
                str_subfunction_name,
                set_checkpoint_signals,
                list_drift_keys,
            ):

                # 把 drift 证据回推到当前子函数，便于后续优先修复。
                _append_unique_str(list_suspects, str_subfunction_name)

    # 已定位到具体子函数时，直接返回收集结果。
    if list_suspects:

        # 返回按首次命中顺序收集的可疑子函数列表。
        return list_suspects

    # 没有具体子函数但存在漂移时，回退到当前计划名。
    if semantic.get("checkpoint_drift"):

        # 先把计划名统一转成字符串，用作最低粒度的定位兜底。
        str_plan_name = str(plan.get("name"))  # checkpoint 漂移存在但子函数未命中时的计划名兜底

        # 返回仅包含计划名的单元素列表。
        return [str_plan_name]

    # 缺少足够定位线索时返回空列表。
    return []
