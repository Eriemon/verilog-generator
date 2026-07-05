"""根据 workflow trace 生成下一轮 prompt 补丁和可复用记忆。"""

# 延迟注解解析，避免运行期求值嵌套泛型。
from __future__ import annotations

# 标准库提供路径对象和 JSON-like 负载类型标注。
from pathlib import Path
from typing import Any

# 运行时模块提供计划规范化和 trace 读取能力。
from scripts.python.workflow.planning import decompose_spec
from scripts.python.workflow.trace import read_trace

# trace 文本约束规则保持旧版顺序，确保 prompt patch 输出稳定。
CONSTRAINT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (  # trace 关键词到 prompt 约束的映射表
    (
        ("dependency_issue", "undefined module", "undefined reference", "not declared"),  # 依赖或声明缺失迹象
        (
            "Re-check subfunction interface compatibility, dimensions, and dependency outputs "
            "before regenerating the failing module."
        ),  # 依赖失败后复核上下游接口的 prompt 约束
    ),
    (
        ("testbench", "main() entry point"),  # 测试平台或入口函数相关诊断
        (
            "Generate or repair a self-checking testbench with explicit PASS/FAIL behavior "
            "and coverage for every behavior item."
        ),  # testbench 失败后要求补齐自检覆盖的 prompt 约束
    ),
    (
        ("toolchain_issue", "required tool", "failed to start", "timed out"),  # 外部工具不可用或超时迹象
        (
            "Separate toolchain availability/configuration failures from code edits; "
            "only rewrite code when the tool output identifies code errors."
        ),  # 工具链异常和代码错误分流的 prompt 约束
    ),
    (
        ("placeholder", "todo", "fixme", "ellipsis"),  # 未完成代码占位痕迹
        "Remove all placeholders and produce complete executable code for every manifest file.",  # 完整实现约束
    ),
    (
        ("spec_issue", "expected output file is missing"),  # 规格或产物覆盖缺口
        (
            "Audit the plan outputs and evidence before code regeneration; "
            "missing requested files indicate a plan or manifest coverage issue."
        ),  # 缺失产物先回查计划覆盖的 prompt 约束
    ),
    (
        ("reviewability", "comment"),  # 注释可审查性相关信号
        (
            "Preserve the requested comment language and add same-line semantic comments for "
            "ports, signals, always blocks, assigns, testbench checks, and construct end lines."
        ),  # RTL 可审查性不足时使用的注释补强约束
    ),
    (
        ("fsm", "state register", "next-state"),  # 状态机结构缺口
        (
            "For RTL regeneration, use a three-block FSM with explicit state register, "
            "next-state logic, and output logic sections."
        ),  # 状态机结构失败后的 RTL 分块约束
    ),
    (
        ("semantic model", "run_tests"),  # Python 参考模型合同信号
        (
            "Preserve the Python semantic model API and mirror its deterministic "
            "verification vectors in the downstream testbench."
        ),  # 参考模型合同漂移时的向量同步约束
    ),
    (
        ("semantic output drift", "wrong_final_output"),  # 最终输出语义漂移
        (
            "When a final output mismatches the Python oracle, restate the exact output contract "
            "and regenerate only the logic responsible for those drift keys."
        ),  # 最终输出漂移时限制重生成范围的约束
    ),
    (
        ("checkpoint drift", "checkpoint_divergence"),  # 中间检查点漂移
        (
            "Preserve and compare intermediate checkpoints so the next attempt can localize "
            "the mismatch before the final output stage."
        ),  # 中间检查点漂移后的定位约束
    ),
    (
        ("case order drift", "case_order_drift"),  # 用例顺序漂移
        "Keep case ids and transcript order stable across Python oracle, vectors, and Verilog testbench output.",  # 用例顺序稳定约束
    ),
    (
        ("weak_test_oracle", "augment_tests"),  # 测试 oracle 覆盖不足
        "Strengthen boundary cases, negative cases, and checkpoint coverage before escalating to human debugging.",  # 测试增强约束
    ),
    (
        ("ambiguous_spec_rule",),  # 规格规则冲突
        (
            "Call out the conflicting spec rule explicitly and preserve the ambiguity "
            "for structured human resolution instead of guessing."
        ),  # 规格冲突进入人工确认前的保留约束
    ),
    (
        ("needs_human_intervention", "ask_human"),  # 人工介入信号
        (
            "Summarize the unresolved ambiguity as a precise hardware-design question "
            "before another generation attempt."
        ),  # 人工介入后收敛问题描述的约束
    ),
)

# 记忆签名规则保持旧版优先级，避免历史 trace 产出的 memory 顺序漂移。
SIGNATURE_RULES: tuple[dict[str, Any], ...] = (  # 供_memory_entries把失败事件转换为可复用prompt记忆约束的规则序列
    {
        "source_terms": ("dependency_issue",),  # 依赖失败事件来源匹配词
        "text_terms": ("undefined module", "interface"),  # 接口或未定义模块文本
        "signature": "interface_or_dependency_mismatch",  # 接口依赖签名
        "constraint": (  # 接口依赖签名对应的 prompt memory 约束
            "Reconfirm upstream/downstream port names, dimensions, and subfunction dependency "
            "outputs before code generation."
        ),  # 接口依赖失败写入 memory 的复核提醒
    },
    {
        "source_terms": ("testbench_issue",),  # 测试平台失败事件来源匹配词
        "text_terms": ("testbench", "pass behavior", "fail behavior"),  # 自检行为文本
        "signature": "testbench_or_reference_vector_gap",  # testbench 覆盖签名
        "constraint": (  # testbench 覆盖签名对应的 prompt memory 约束
            "Generate self-checking PASS/FAIL tests that mirror the semantic model vectors "
            "and mention required verification cases."
        ),  # testbench 覆盖缺口写入 memory 的自检提醒
    },
    {
        "source_terms": (),  # 该规则只依赖 trace 文本
        "text_terms": ("semantic model", "run_tests"),  # 参考模型 API 文本
        "signature": "reference_model_contract_gap",  # 参考模型合同签名
        "constraint": "Preserve `run_tests()` and the Python CLI entrypoint, then mirror its vectors downstream.",  # 参考模型入口缺口写入记忆的保持约束
    },
    {
        "source_terms": (),  # 注释可审查性来自文本线索
        "text_terms": ("reviewability", "comment"),  # 注释质量相关文本
        "signature": "comment_reviewability_gap",  # 注释审查签名
        "constraint": (  # 注释可审查签名对应的 prompt memory 约束
            "Use the requested comment language and add same-line semantic comments for every "
            "RTL declaration, block, assign, construct end, and testbench case check."
        ),  # 注释审查失败写入 memory 的 RTL 注释提醒
    },
    {
        "source_terms": (),  # FSM 结构来自 trace 描述
        "text_terms": ("fsm", "state register", "next-state"),  # 状态机结构文本
        "signature": "rtl_fsm_structure_gap",  # FSM 结构签名
        "constraint": (  # FSM 结构签名对应的 prompt memory 约束
            "Use three-block RTL FSM style with state register, next-state logic, "
            "and output logic labels."
        ),  # FSM 结构缺口写入 memory 的样式提醒
    },
    {
        "source_terms": ("toolchain_issue",),  # 工具链类 error_sources
        "text_terms": ("required tool",),  # 缺少工具文本
        "signature": "toolchain_unavailable_or_failed",  # 工具链失败签名
        "constraint": (  # 工具链失败签名对应的 prompt memory 约束
            "Separate tool availability failures from code edits and rerun with "
            "the required readiness tool installed."
        ),  # 工具链失败写入 memory 的执行边界提醒
    },
    {
        "source_terms": ("spec_issue",),  # 规格失败事件来源匹配词
        "text_terms": ("evidence",),  # 证据覆盖文本
        "signature": "spec_or_evidence_gap",  # 规格证据签名
        "constraint": "Audit evidence coverage and requested outputs before regenerating implementation files.",  # 证据覆盖记忆
    },
    {
        "source_terms": (),  # 输出漂移来自语义报告文本
        "text_terms": ("semantic output drift", "wrong_final_output"),  # 最终输出不匹配文本
        "signature": "wrong_final_output",  # 最终输出漂移签名
        "constraint": (  # 最终输出漂移签名对应的 prompt memory 约束
            "Reconfirm case outputs against the Python oracle and focus regeneration "
            "on the drift keys reported by semantic validation."
        ),  # 最终输出漂移写入 memory 的重生成范围提醒
    },
    {
        "source_terms": (),  # checkpoint 漂移来自语义报告文本
        "text_terms": ("checkpoint drift", "checkpoint_divergence"),  # checkpoint 不一致文本
        "signature": "checkpoint_divergence",  # checkpoint 漂移签名
        "constraint": (  # checkpoint 漂移签名对应的 prompt memory 约束
            "Preserve intermediate checkpoints and use them to localize where the Verilog "
            "behavior diverges from the Python oracle."
        ),  # checkpoint 漂移写入 memory 的定位提醒
    },
    {
        "source_terms": (),  # case 顺序漂移来自 trace 文本
        "text_terms": ("case order drift", "case_order_drift"),  # 用例顺序不一致文本
        "signature": "case_order_drift",  # 用例顺序签名
        "constraint": (  # case 顺序漂移签名对应的 prompt memory 约束
            "Keep case ordering and case ids stable between the semantic contract "
            "and the Verilog transcript."
        ),  # 用例顺序漂移写入 memory 的排序提醒
    },
    {
        "source_terms": (),  # oracle 强度来自 trace 文本
        "text_terms": ("weak_test_oracle", "augment_tests"),  # 测试增强文本
        "signature": "weak_test_oracle",  # 测试 oracle 签名
        "constraint": "Add stronger boundary, negative, and checkpoint cases before escalating to a human.",  # 测试增强记忆
    },
    {
        "source_terms": (),  # 规格歧义来自结构化文本
        "text_terms": ("ambiguous_spec_rule",),  # 歧义规则文本
        "signature": "ambiguous_spec_rule",  # 规格歧义签名
        "constraint": (  # 规格歧义签名对应的 prompt memory 约束
            "Preserve the spec conflict explicitly and route it into structured human intervention "
            "rather than inferring behavior."
        ),  # 规格歧义写入 memory 的人工介入提醒
    },
)

# optimize_prompt_from_trace 将 trace 诊断整理成下一轮 prompt patch。
def optimize_prompt_from_trace(trace_path: Path, plan: dict[str, Any]) -> str:
    """根据历史 trace 为下一轮 staged generation 构造 prompt patch。

    参数:
        trace_path: workflow trace JSONL 或 JSON 文件路径。
        plan: 已有 codegen plan 或规格字典。

    返回:
        可追加到下一轮 prompt 的 Markdown 补丁文本。
    """

    # trace 事件提供失败模式和恢复线索。
    list_events = read_trace(trace_path)  # 历史 workflow 事件

    # 规范化计划用于稳定读取设计名和子功能列表。
    dict_normalized_plan = decompose_spec(plan)  # 规范化后的 RTL 生成计划

    # 约束列表按规则顺序去重，保持 prompt patch 可预测。
    list_constraints = _derive_constraints(list_events)  # 下一轮 prompt 约束

    # 子功能摘要在 Context 区域显示，帮助人工确认覆盖范围。
    str_subfunctions = _subfunction_summary(dict_normalized_plan)  # 子功能名称摘要

    # prompt patch 头部保持旧版 Markdown wire shape。
    list_lines = [  # 供optimize_prompt_from_trace拼接返回Markdown补丁正文的行序列
        f"# Prompt patch: {dict_normalized_plan['name']}",  # 标识目标设计名称的补丁标题行
        "",  # 标题和正文之间的 Markdown 空行
        "Apply these incremental constraints to the next staged generation prompt.",  # 固定说明文本
        "",  # 说明和约束标题之间的 Markdown 空行
        "## Targeted Constraints",  # 约束章节标题
        "",  # 约束标题后的 Markdown 空行
    ]

    # 有具体约束时逐条追加，空约束时保留旧版兜底文本。
    if list_constraints:

        # 约束条目必须保持规则匹配顺序。
        list_lines.extend(f"- {str_constraint}" for str_constraint in list_constraints)

    # trace 没有命中失败模式时不强行发明 prompt 约束。
    else:

        # 兜底约束明确要求保留既有 staged prompt 合同。
        list_lines.append(
            "- No concrete failure pattern was found; keep the existing staged prompt contract unchanged."
        )

    # Context 区域汇总 trace 数量、子功能和输出合同边界。
    list_lines.extend(
        [
            "",  # 约束列表和 Context 标题之间的 Markdown 空行
            "## Context",  # 上下文章节标题
            "",  # Context 标题后的 Markdown 空行
            f"- Trace events analyzed: {len(list_events)}",  # trace 事件数量
            f"- Subfunctions: {str_subfunctions}",  # 子功能摘要
            "- Preserve exact manifest/code-fence output contract.",  # 输出合同保护提醒
        ]
    )

    # 返回带末尾换行的 Markdown 文本，保持旧版 CLI 输出习惯。
    return "\n".join(list_lines) + "\n"

# build_prompt_memory 将 trace 诊断转换为 workflow 可复用记忆。
def build_prompt_memory(trace_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """根据 trace 和计划生成 prompt memory 结构。

    参数:
        trace_path: workflow trace JSONL 或 JSON 文件路径。
        plan: 已有 codegen plan 或规格字典。

    返回:
        包含版本、目标和记忆条目的字典。
    """

    # prompt memory 只读取能复用到后续生成的失败线索。
    list_events = read_trace(trace_path)  # prompt memory 签名抽取的输入事件

    # 顶层 memory 字段从规范化计划读取，避免原始 spec 形态差异。
    dict_normalized_plan = decompose_spec(plan)  # memory 顶层名称和目标来源

    # 返回结构保持旧版 prompt memory wire shape。
    return {
        "version": 1,  # memory 结构版本
        "name": dict_normalized_plan["name"],  # 设计或计划名称
        "target": dict_normalized_plan["target"],  # 生成目标类型
        "entries": _memory_entries(list_events, dict_normalized_plan),  # 去重后的 trace 记忆条目
    }

# _derive_constraints 负责从 trace 文本中抽取 prompt 约束。
def _derive_constraints(list_events: list[dict[str, Any]]) -> list[str]:
    """从历史事件文本中提取下一轮 prompt 约束。

    参数:
        list_events: read_trace 返回的 workflow 事件列表。

    返回:
        按规则顺序去重后的英文 prompt 约束列表。
    """

    # 所有事件统一转成小写文本，兼容旧逻辑的字符串匹配方式。
    str_joined_events = "\n".join(str(dict_event).lower() for dict_event in list_events)  # trace 拼接文本

    # prompt patch 约束按 CONSTRAINT_RULES 命中顺序累积。
    list_constraints: list[str] = []  # 下一轮生成 prompt 中追加的约束条目

    # 逐条匹配历史规则，保持旧版约束文本和顺序。
    for tuple_keywords, str_constraint in CONSTRAINT_RULES:

        # 当前关键词组命中时追加对应约束。
        if _contains_any(str_joined_events, tuple_keywords):

            # 去重追加避免多个关键词命中同一约束时重复输出。
            _append_unique(list_constraints, str_constraint)

    # 返回 prompt patch 使用的约束列表。
    return list_constraints

# _memory_entries 负责把 trace 事件展开成去重记忆条目。
def _memory_entries(list_events: list[dict[str, Any]], dict_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """把 trace 事件转换为按子功能和阶段索引的记忆条目。

    参数:
        list_events: read_trace 返回的 workflow 事件列表。
        dict_plan: decompose_spec 输出的规范化计划。

    返回:
        去重后的 prompt memory entries 列表。
    """

    # 子功能列表缺省为星号，保留旧版全局记忆语义。
    list_subfunctions = _plan_subfunctions(dict_plan)  # prompt memory 适用的子功能名称

    # entries 先完整收集，再按 subfunction/stage/signature 去重。
    list_entries: list[dict[str, Any]] = []  # 原始记忆条目列表

    # 每个 trace 事件可能贡献多个签名。
    for dict_event in list_events:

        # 事件文本沿用旧版 str(event).lower() 匹配语义。
        str_joined_event = str(dict_event).lower()  # 单个事件小写文本

        # 事件签名规则输出错误签名和约束文本。
        list_signatures = _event_signatures(dict_event, str_joined_event)  # 当前事件命中的签名

        # 每个签名生成一条 memory entry。
        for str_signature, str_constraint in list_signatures:

            # 记忆条目保持 workflow state 使用的字段形状。
            list_entries.append(
                {
                    "subfunction": dict_event.get("subfunction") or list_subfunctions[0],  # 事件归属子功能
                    "stage": _event_stage(dict_event),  # 事件归属阶段或 readiness
                    "attempt_id": dict_event.get("attempt_id"),  # 原始尝试标识
                    "error_signature": str_signature,  # 可复用错误签名
                    "constraint": str_constraint,  # 下一轮 prompt 约束文本
                }
            )

    # 去重后返回给 prompt memory 写出流程。
    return _dedupe_entries(list_entries)

# _event_signatures 根据单个事件匹配可复用错误签名。
def _event_signatures(dict_event: dict[str, Any], str_joined_event: str) -> list[tuple[str, str]]:
    """提取单个 trace 事件命中的记忆签名。

    参数:
        dict_event: 单条 workflow trace 事件。
        str_joined_event: 事件字典转成小写后的文本。

    返回:
        由 error_signature 和 constraint 组成的二元组列表。
    """

    # error_sources 只有列表形态时才参与源分类匹配。
    set_sources = set(dict_event.get("error_sources", []) or [])  # 当前事件错误来源集合

    # 签名输出按 SIGNATURE_RULES 顺序累积。
    list_signatures: list[tuple[str, str]] = []  # 当前事件命中的签名列表

    # 逐条匹配来源和文本关键词，保持旧版优先级。
    for dict_rule in SIGNATURE_RULES:

        # 规则命中后生成一条 prompt memory 签名。
        if _signature_rule_matches(dict_rule, set_sources, str_joined_event):

            # 签名和约束文本来自静态规则表，不做运行时改写。
            tuple_signature = (dict_rule["signature"], dict_rule["constraint"])  # 记忆签名和约束

            # 当前事件命中的签名进入返回列表。
            list_signatures.append(tuple_signature)

    # 返回当前事件贡献的所有签名。
    return list_signatures

# _signature_rule_matches 判断签名规则是否命中当前事件。
def _signature_rule_matches(dict_rule: dict[str, Any], set_sources: set[Any], str_joined_event: str) -> bool:
    """判断一条签名规则是否匹配当前 trace 事件。

    参数:
        dict_rule: SIGNATURE_RULES 中的一条规则。
        set_sources: 当前事件的 error_sources 集合。
        str_joined_event: 当前事件的小写文本。

    返回:
        来源或文本关键词任一命中时返回 True。
    """

    # source_terms 为空时只依赖文本关键词。
    tuple_source_terms = tuple(dict_rule.get("source_terms", ()))  # 规则错误来源关键词

    # text_terms 为空时只依赖 error_sources。
    tuple_text_terms = tuple(dict_rule.get("text_terms", ()))  # 规则事件文本关键词

    # 来源命中沿用旧逻辑中的 error_sources 判断。
    bool_source_matched = bool(tuple_source_terms) and any(  # error_sources 是否命中该签名规则
        str_source in set_sources  # 当前来源是否在事件来源集合中
        for str_source in tuple_source_terms  # 规则要求的来源关键词
    )

    # 文本命中沿用旧逻辑中的 str(event).lower() 判断。
    bool_text_matched = _contains_any(str_joined_event, tuple_text_terms)  # 文本是否命中

    # 来源或文本任一路径命中即可生成签名。
    return bool_source_matched or bool_text_matched

# _plan_subfunctions 解析计划中的子功能名称列表。
def _plan_subfunctions(dict_plan: dict[str, Any]) -> list[str]:
    """从规范化计划中提取 prompt memory 适用的子功能名称。

    参数:
        dict_plan: decompose_spec 输出的规范化计划。

    返回:
        子功能名称列表；没有可用名称时返回 ``["*"]``。
    """

    # 子功能名只从 dict 条目提取，兼容旧版过滤逻辑。
    list_names = [
        str(dict_item.get("name"))  # 子功能名称
        for dict_item in dict_plan.get("subfunctions", [])  # 规范化计划子功能列表
        if isinstance(dict_item, dict) and dict_item.get("name")  # 只保留具名子功能
    ]  # 已解析子功能名称

    # 没有子功能时返回全局星号，保持旧版默认行为。
    return list_names or ["*"]

# _subfunction_summary 生成 prompt patch 中展示的子功能摘要。
def _subfunction_summary(dict_plan: dict[str, Any]) -> str:
    """生成 prompt patch Context 区域使用的子功能摘要文本。

    参数:
        dict_plan: decompose_spec 输出的规范化计划。

    返回:
        以逗号分隔的子功能名称字符串。
    """

    # 子功能摘要复用 memory 的解析规则，避免两个路径默认值不一致。
    list_subfunctions = _plan_subfunctions(dict_plan)  # prompt patch 展示的子功能名称

    # 返回 Markdown 列表项中使用的逗号分隔摘要。
    return ", ".join(list_subfunctions)

# _event_stage 解析 memory entry 的阶段字段。
def _event_stage(dict_event: dict[str, Any]) -> Any:
    """解析 trace 事件所属阶段或 readiness。

    参数:
        dict_event: 单条 workflow trace 事件。

    返回:
        事件中的 stage、readiness、event 或 ``unknown``。
    """

    # stage/readiness/event 按旧版优先级依次兜底。
    return dict_event.get("stage") or dict_event.get("readiness") or dict_event.get("event") or "unknown"

# _contains_any 统一处理小写文本的关键词匹配。
def _contains_any(str_text: str, tuple_keywords: tuple[str, ...]) -> bool:
    """判断文本是否包含任一关键词。

    参数:
        str_text: 已按调用方需要归一化的待检索文本。
        tuple_keywords: 需要依次匹配的关键词集合。

    返回:
        任一关键词出现在文本中时返回 True。
    """

    # any 保持短路匹配，避免无意义地扫描后续关键词。
    return any(str_keyword in str_text for str_keyword in tuple_keywords)

# _append_unique 保持约束列表的稳定去重追加语义。
def _append_unique(list_values: list[str], str_value: str) -> None:
    """向字符串列表追加不存在的值。

    参数:
        list_values: 需要原地更新的字符串列表。
        str_value: 候选字符串值。

    返回:
        无。
    """

    # 只有首次命中才写入，保持旧版 add helper 的去重行为。
    if str_value not in list_values:

        # 原地追加让调用方持有的列表保持同步。
        list_values.append(str_value)

# _dedupe_entries 按子功能、阶段和错误签名去重。
def _dedupe_entries(list_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去除重复的 prompt memory entry。

    参数:
        list_entries: 原始 prompt memory 条目列表。

    返回:
        保留首次出现顺序的去重条目列表。
    """

    # seen 记录已经输出过的 memory 业务键。
    set_seen: set[tuple[Any, Any, Any]] = set()  # 已出现的记忆去重键

    # 去重结果按输入顺序保存首次出现的条目。
    list_deduped: list[dict[str, Any]] = []  # 保留首次出现顺序的 memory 条目

    # 逐条检查 memory entry 的业务唯一键。
    for dict_entry in list_entries:

        # memory 去重仍使用旧版的三字段业务键。
        tuple_key = (  # 子功能、阶段和签名组成 memory 去重键
            dict_entry.get("subfunction"),  # 记忆适用子功能
            dict_entry.get("stage"),  # workflow 阶段或 readiness 名称
            dict_entry.get("error_signature"),  # 触发 prompt 记忆复用的错误签名
        )

        # 已见过的业务键直接跳过，保留首次条目。
        if tuple_key in set_seen:

            # 跳过重复条目，不改变输出顺序。
            continue

        # 记录当前业务键，避免后续重复输出。
        set_seen.add(tuple_key)

        # 首次出现的条目保留原字段内容。
        list_deduped.append(dict_entry)

    # 返回去重后的 memory 条目。
    return list_deduped
