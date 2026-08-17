"""执行 Verilog 声明标识符的数字 token 与功能语义门禁。"""

# future annotations 延迟解析声明候选的联合类型。
from __future__ import annotations

# dataclass 固化每条声明候选的最小检查事实。
from dataclasses import dataclass

# Any 描述规则资产和 formatter AST 的异构字典字段。
from typing import Any

# rulebook 是命名词表的唯一运行时配置来源。
from scripts.python.validation.rulebook import load_verilog_rulebook

# 规则模型统一 VG 结果状态和 finding 输出格式。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# VgFacts 提供 formatter AST 聚合后的源码事实。
from .vg_semantic_facts import VgFacts

# 四项配置共同限定数字白名单、词缀剥离和无意义词判断。
NAMING_POLICY_KEYS = (
    "digit_token_allowlist",  # VG156 合法数字 token
    "semantic_prefixes",  # VG158 可重复剥离的声明前缀
    "semantic_suffixes",  # VG158 可重复剥离的声明后缀
    "meaningless_tokens",  # VG158 不足以单独表达功能的词
)

# DeclarationCandidate 保存命名规则所需的声明名称和真实位置。
@dataclass(frozen=True)
class DeclarationCandidate:
    """保存命名门禁实际检查的一条变量声明。

    属性:
        name: formatter AST 中确认的声明标识符。
        path: 声明所在文件的相对路径。
        line: 已确认的一基源码行，未知时为 None。
        context: 声明所属的 AST 集合名称。
    """

    # name 是 VG156 和 VG158 共同分析的原始标识符。
    name: str  # formatter AST 确认的声明名称

    # path 将 finding 绑定到原始源码文件。
    path: str  # 声明所在文件的相对路径

    # line 只接受 parser 明确给出的正整数位置。
    line: int | None  # 未知位置保持 None，禁止伪造行号

    # context 帮助用户定位参数、端口或子程序局部声明。
    context: str  # 声明所属的 AST 集合名称

# evaluate_identifier_gate 是 VG156 与 VG158 的共享入口。
def evaluate_identifier_gate(str_gate_id: str, facts: VgFacts) -> VgEvaluation:
    """执行数字 token 或功能型命名检查。

    参数:
        str_gate_id: 只允许 `VG156` 或 `VG158`。
        facts: formatter AST 聚合后的 Verilog 事实。
    返回:
        与门禁严重级别和适用性一致的结构化结果。
    异常:
        ValueError: 命名规则资产缺少必需列表或包含空条目。
    """

    # 一次收集声明，确保两个门禁使用完全相同的覆盖范围。
    tuple_candidates, tuple_incomplete = _declaration_candidates(facts)  # 声明候选与不完整解析证据

    # 每次评估从权威 JSON 读取词表，避免实现内置业务枚举。
    dict_policy = _naming_policy()  # 已校验的命名规则资产

    # VG156 只检查按下划线切分后的数字 token。
    if str_gate_id == "VG156":

        # 数字 token finding 与功能语义 finding 保持相互独立。
        list_findings = _digit_token_findings(tuple_candidates, dict_policy)  # 未授权数字 token 证据

    # VG158 只检查剥离受管前后缀后的功能词。
    elif str_gate_id == "VG158":

        # 未知功能词允许通过，仅全部无意义 token 时失败。
        list_findings = _functional_name_findings(tuple_candidates, dict_policy)  # 无功能语义名称证据

    # 其他标识符门禁编号属于调用方编程错误。
    else:

        # 返回 error 而不是把未知门禁误判成通过。
        return VgEvaluation("error", True, message=f"Unsupported identifier gate: {str_gate_id}")

    # 已确认违规优先于局部解析不完整，保留确定失败结论。
    if list_findings:

        # 每条声明产生独立 finding，便于逐项修复。
        return failed(*list_findings)

    # 子程序声明覆盖不完整时不得宣称全量通过。
    if tuple_incomplete:

        # inconclusive 携带真实 parser 原因和源码位置。
        return inconclusive(
            "Subprogram declarations were not parsed completely.",
            *tuple_incomplete,
        )

    # 没有声明时标记为不适用，其余情况给出确定通过结论。
    return passed(
        applicable=bool(tuple_candidates),
        message="All declaration identifiers satisfy the naming policy.",
    )

# _naming_policy 验证四组词表可以安全驱动字符串剥离与比较。
def _naming_policy() -> dict[str, Any]:
    """读取并校验命名门禁所需的规则资产。

    参数:
        无。
    返回:
        包含数字白名单、受管词缀和无意义 token 的命名字典。
    异常:
        ValueError: 必需字段不是非空字符串列表或含有空字符串。
    """

    # raw 字典保持 JSON 权威源原貌，当前函数只读取 naming 子树。
    dict_naming = dict(load_verilog_rulebook().raw.get("naming", {}))  # 命名门禁配置副本

    # 逐项验证可避免空后缀导致无限剥离循环。
    for str_key in NAMING_POLICY_KEYS:

        # 当前值先按原始类型检查，禁止静默转换错误配置。
        list_policy_entries = dict_naming.get(str_key)  # 当前命名规则字段的候选列表

        # 所有字段必须是至少含一个条目的列表。
        if not isinstance(list_policy_entries, list) or not list_policy_entries:

            # 配置错误必须阻断，而不是放宽命名规则。
            raise ValueError(f"> ERR: [Python] Verilog naming policy requires non-empty {str_key}.")

        # 空字符串会破坏词缀剥离或制造无意义白名单命中。
        if any(not isinstance(item, str) or not item for item in list_policy_entries):

            # 报错包含字段名，便于直接定位权威 JSON。
            raise ValueError(f"> ERR: [Python] Verilog naming policy requires string entries in {str_key}.")

    # 返回已经完成结构校验的命名规则。
    return dict_naming

# _declaration_candidates 遍历模块和子程序中的全部变量声明集合。
def _declaration_candidates(
    facts: VgFacts,
) -> tuple[tuple[DeclarationCandidate, ...], tuple[VgFinding, ...]]:
    """从唯一 formatter AST 收集模块与子程序变量声明。

    参数:
        facts: formatter AST 聚合后的 Verilog 事实。
    返回:
        有序声明候选元组和阻止确定通过的不完整解析 finding 元组。
    """

    # 候选列表保持源文件、模块和 AST 集合的稳定遍历顺序。
    list_candidates: list[DeclarationCandidate] = []  # 已确认的变量声明候选

    # 不完整列表只记录会影响子程序声明覆盖的 parser 原因。
    list_incomplete: list[VgFinding] = []  # 无法确认完整覆盖的解析证据

    # 每个源文件报告已经由共享 formatter AST 生成。
    for source_facts in facts.sources:

        # 模块是参数、端口、普通声明和子程序的共同容器。
        for dict_module in source_facts.report.get("modules", []) or []:

            # 模块级集合由独立辅助函数处理，限制主遍历的嵌套深度。
            _append_module_candidates(list_candidates, dict_module, source_facts.relative_path)

            # 子程序成员和不完整状态通过同一辅助函数成对收集。
            _append_subprogram_candidates(
                list_candidates,
                list_incomplete,
                dict_module,
                source_facts.relative_path,
            )

    # 元组冻结顺序，防止后续规则意外修改共享收集结果。
    return tuple(list_candidates), tuple(list_incomplete)

# _append_module_candidates 收集一个模块的四类顶层声明。
def _append_module_candidates(
    list_candidates: list[DeclarationCandidate],
    dict_module: dict[str, Any],
    str_path: str,
) -> None:
    """追加一个模块中的参数、端口和内部声明。

    参数:
        list_candidates: 按源码顺序累积的声明候选列表。
        dict_module: formatter AST 中的单个模块字典。
        str_path: 模块所在文件的相对路径。
    返回:
        None；结果原地追加到 list_candidates。
    """

    # 模块级集合覆盖参数、局部参数、端口及内部声明。
    for str_collection in ("params", "localparams", "ports", "decls"):

        # 当前集合中的每个条目最多贡献一个名称候选。
        for dict_item in dict_module.get(str_collection, []) or []:

            # 缺失名称的 AST 条目由追加函数安全忽略。
            _append_candidate(list_candidates, dict_item, str_path, str_collection)

# _append_subprogram_candidates 收集一个模块内的 function 和 task 声明。
def _append_subprogram_candidates(
    list_candidates: list[DeclarationCandidate],
    list_incomplete: list[VgFinding],
    dict_module: dict[str, Any],
    str_path: str,
) -> None:
    """追加子程序成员，并记录影响声明覆盖的 parser 原因。

    参数:
        list_candidates: 按源码顺序累积的声明候选列表。
        list_incomplete: 影响确定通过的解析 finding 列表。
        dict_module: formatter AST 中的单个模块字典。
        str_path: 模块所在文件的相对路径。
    返回:
        None；两个列表均按遍历结果原地更新。
    """

    # function 与 task 共享形式参数和局部声明结构。
    for str_collection in ("functions", "tasks"):

        # 每个子程序分别报告成员和局部解析状态。
        for dict_subprogram in dict_module.get(str_collection, []) or []:

            # 两类成员都是 VG156 与 VG158 的变量声明目标。
            for str_member_collection in ("formals", "local_declarations"):

                # 子程序成员保留其真实行号和所属上下文。
                for dict_item in dict_subprogram.get(str_member_collection, []) or []:

                    # 上下文包含子程序类型和成员集合，便于定位。
                    _append_candidate(
                        list_candidates,
                        dict_item,
                        str_path,
                        f"{str_collection}.{str_member_collection}",
                    )

            # 只有影响声明覆盖的原因才会阻止确定通过。
            str_reason = str(dict_subprogram.get("unsupported_reason") or "")  # 子程序解析失败原因

            # 可恢复的语句级限制不应污染命名门禁状态。
            if _declaration_parse_is_incomplete(str_reason):

                # 保留 parser 提供的真实原因作为最小失败证据。
                list_incomplete.append(
                    VgFinding(
                        path=str_path,
                        line=_source_line(dict_subprogram),
                        message="子程序变量声明未被 formatter 完整识别，命名门禁无法给出确定通过结论。",
                        evidence=str_reason,
                        severity="BLOCKER",
                    )
                )

# _append_candidate 过滤无名条目并统一构造声明事实。
def _append_candidate(
    list_candidates: list[DeclarationCandidate],
    dict_item: dict[str, Any],
    str_path: str,
    str_context: str,
) -> None:
    """在标识符存在时追加一条稳定声明候选。

    参数:
        list_candidates: 按源码顺序累积的声明候选列表。
        dict_item: formatter AST 中的单条声明。
        str_path: 声明所在文件的相对路径。
        str_context: 声明所属的 AST 集合名称。
    返回:
        None；结果原地追加到 list_candidates。
    """

    # 空白名称不代表真实声明，不能制造无意义命名 finding。
    str_name = str(dict_item.get("name") or "").strip()  # 规范化后的声明标识符

    # 没有名称的恢复条目由 AST 不完整状态单独负责。
    if not str_name:

        # 原列表保持不变。
        return

    # 候选只保留命名门禁需要的最小字段。
    list_candidates.append(
        DeclarationCandidate(
            name=str_name,
            path=str_path,
            line=_source_line(dict_item),
            context=str_context,
        )
    )

# _source_line 拒绝用默认值伪造未知位置。
def _source_line(dict_item: dict[str, Any]) -> int | None:
    """只返回 formatter 已确认的正整数源码行。

    参数:
        dict_item: formatter AST 声明或子程序字典。
    返回:
        已确认的一基正整数行号，未知时返回 None。
    """

    # 声明成员优先直接公开 line_start。
    int_line = dict_item.get("line_start")  # 声明条目的直接起始行

    # 布尔值不是合法行号，int 检查与正值约束共同过滤无效位置。
    if isinstance(int_line, int) and not isinstance(int_line, bool) and int_line > 0:

        # 直接位置已经足够精确。
        return int_line

    # 子程序容器把真实范围放在 span 字典中。
    dict_span = dict_item.get("span")  # 子程序或恢复条目的范围字段

    # 未知或显式 None 的 span 不得继续索引。
    if isinstance(dict_span, dict):

        # 只读取范围起始行，不猜测内部成员位置。
        int_line = dict_span.get("line_start")  # 子程序范围的一基起始行

        # 复用正整数约束，拒绝布尔值和零值。
        if isinstance(int_line, int) and not isinstance(int_line, bool) and int_line > 0:

            # 返回 parser 明确记录的范围起点。
            return int_line

    # 位置缺失时保持未知，交由报告层展示。
    return None

# _declaration_parse_is_incomplete 维护会影响声明覆盖的封闭原因集合。
def _declaration_parse_is_incomplete(str_reason: str) -> bool:
    """判断局部失败是否会影响子程序变量声明覆盖。

    参数:
        str_reason: formatter AST 报告的 unsupported_reason。
    返回:
        原因会影响形式参数或局部声明覆盖时为 True。
    """

    # 语句级 unsupported_reason 不在集合中，因此不会误阻断命名门禁。
    return str_reason in {
        "unsupported_subprogram_local_declaration",
        "unsupported_function_declaration",
        "unsupported_task_declaration",
        "missing_function_definition",
        "missing_task_definition",
    }

# _digit_token_findings 实现按下划线切分的精确白名单规则。
def _digit_token_findings(
    tuple_candidates: tuple[DeclarationCandidate, ...],
    dict_policy: dict[str, Any],
) -> list[VgFinding]:
    """为每个含未授权数字 token 的声明生成一条 finding。

    参数:
        tuple_candidates: 按源码顺序排列的声明候选。
        dict_policy: 已校验的命名规则资产。
    返回:
        每个违规声明至多一条 VG156 finding。
    """

    # 白名单按完整小写 token 比较，大小写变化不扩大允许范围。
    set_allowlist = {str(item).lower() for item in dict_policy["digit_token_allowlist"]}  # 合法数字 token 集合

    # finding 顺序与声明遍历顺序一致，保证报告确定性。
    list_findings: list[VgFinding] = []  # 已确认的 VG156 违规

    # 每条声明独立按下划线切分，不跨 token 拼接。
    for declaration_candidate in tuple_candidates:

        # 先创建当前声明的独立违规 token 容器。
        list_invalid_tokens: list[str] = []  # 当前声明中的未授权数字 token

        # 下划线是唯一 token 分隔符，空 token 不含数字并自然通过。
        for str_token in declaration_candidate.name.split("_"):

            # 没有数字字符的 token 不属于 VG156 检查对象。
            if not any(str_char.isdigit() for str_char in str_token):

                # 继续分析当前声明的下一个 token。
                continue

            # 完整小写 token 命中白名单时允许使用该数字标识。
            if str_token.lower() in set_allowlist:

                # 白名单不允许部分匹配，当前完整 token 已确认合法。
                continue

            # 其余含数字 token 都作为当前声明的违规证据。
            list_invalid_tokens.append(str_token)

        # 没有违规数字 token 的声明直接进入下一条。
        if not list_invalid_tokens:

            # 当前声明满足 VG156。
            continue

        # finding 同时给出原名、声明上下文和精确违规 token。
        list_findings.append(
            VgFinding(
                path=declaration_candidate.path,
                line=declaration_candidate.line,
                message="变量声明名称包含未授权的数字 token。",
                evidence=(
                    f"name={declaration_candidate.name}; context={declaration_candidate.context}; "
                    f"invalid_tokens={','.join(list_invalid_tokens)}"
                ),
                severity="BLOCKER",
            )
        )

    # 空列表表示所有已解析声明均满足数字 token 规则。
    return list_findings

# _functional_name_findings 检查受管词缀之外是否保留至少一个功能词。
def _functional_name_findings(
    tuple_candidates: tuple[DeclarationCandidate, ...],
    dict_policy: dict[str, Any],
) -> list[VgFinding]:
    """为剥离受管前后缀后仍无功能词的声明生成 finding。

    参数:
        tuple_candidates: 按源码顺序排列的声明候选。
        dict_policy: 已校验的命名规则资产。
    返回:
        每个无功能语义声明至多一条 VG158 finding。
    """

    # 前缀保持 JSON 顺序，重复剥离时先匹配配置靠前项。
    tuple_prefixes = tuple(str(item) for item in dict_policy["semantic_prefixes"])  # 受管声明前缀

    # 后缀与前缀使用相同的重复剥离合同。
    tuple_suffixes = tuple(str(item) for item in dict_policy["semantic_suffixes"])  # 受管声明后缀

    # 无意义 token 使用不区分大小写的精确集合比较。
    set_meaningless = {str(item).lower() for item in dict_policy["meaningless_tokens"]}  # 禁止单独承担语义的词

    # finding 保留声明顺序，方便逐行修复。
    list_findings: list[VgFinding] = []  # 剥离词缀后缺少功能词的声明证据

    # 每条候选独立剥离词缀并切分剩余主体。
    for declaration_candidate in tuple_candidates:

        # 重复剥离支持 `reg_flag_..._o` 这类组合词缀。
        str_core = _strip_managed_affixes(  # 不含受管前后缀的功能词主体
            declaration_candidate.name,  # 当前声明原始名称
            tuple_prefixes,  # 允许重复剥离的前缀
            tuple_suffixes,  # 允许重复剥离的后缀
        )

        # 空 token 不参与语义判断，剩余 token 统一转小写比较。
        list_tokens = [str_token.lower() for str_token in str_core.split("_") if str_token]  # 功能词候选

        # 至少一个未知或非无意义 token 即视为具备功能语义。
        if any(str_token not in set_meaningless for str_token in list_tokens):

            # 未知术语按合同允许通过，避免封闭词典误伤领域名称。
            continue

        # 空主体或全部无意义 token 都产生阻断 finding。
        list_findings.append(
            VgFinding(
                path=declaration_candidate.path,
                line=declaration_candidate.line,
                message="变量声明名称在剥离受管前后缀后缺少功能语义。",
                evidence=(
                    f"name={declaration_candidate.name}; core={str_core}; "
                    f"context={declaration_candidate.context}"
                ),
                severity="BLOCKER",
            )
        )

    # 空列表表示所有已解析声明都保留至少一个功能词。
    return list_findings

# _strip_managed_affixes 只处理权威配置明确声明的词缀。
def _strip_managed_affixes(
    str_name: str,
    tuple_prefixes: tuple[str, ...],
    tuple_suffixes: tuple[str, ...],
) -> str:
    """重复剥离配置声明的前缀和后缀，返回功能词主体。

    参数:
        str_name: 原始 Verilog 声明标识符。
        tuple_prefixes: 按配置顺序匹配的受管前缀。
        tuple_suffixes: 按配置顺序匹配的受管后缀。
    返回:
        去除受管词缀和边界下划线后的功能词主体。
    """

    # 主体从原始声明名称开始，每轮最多剥离一个前缀和一个后缀。
    str_core = str_name  # 当前尚未完成词缀剥离的名称主体

    # 首轮必须尝试匹配，后续由实际剥离动作决定是否继续。
    bool_changed = True  # 本轮是否成功移除至少一个受管词缀

    # 空词缀已经在规则加载阶段禁止，因此循环一定收敛。
    while bool_changed and str_core:

        # 新一轮默认没有变化，只有真实切片后才置回 True。
        bool_changed = False  # 当前剥离轮次的变化标志

        # 前缀按权威配置顺序寻找首个匹配项。
        for str_prefix in tuple_prefixes:

            # 只接受从名称起点完整匹配的受管前缀。
            if str_core.startswith(str_prefix):

                # 切片移除当前匹配前缀，保留其余主体原始大小写。
                str_core = str_core[len(str_prefix):]  # 移除一层受管前缀后的主体

                # 标记变化以允许下一轮继续剥离组合前缀。
                bool_changed = True  # 当前轮次已经移除前缀

                # 每轮只移除首个匹配前缀，避免顺序歧义。
                break

        # 后缀同样按权威配置顺序寻找首个匹配项。
        for str_suffix in tuple_suffixes:

            # 只接受从名称末尾完整匹配的受管后缀。
            if str_core.endswith(str_suffix):

                # 非空后缀保证负索引切片会缩短主体。
                str_core = str_core[: -len(str_suffix)]  # 移除一层受管后缀后的主体

                # 后缀缩短主体后必须再检查是否仍有受管词缀。
                bool_changed = True  # 当前轮次已经移除后缀

                # 每轮只移除首个匹配后缀，保持配置优先级。
                break

    # 边界下划线不是功能 token，不参与 VG158 判断。
    return str_core.strip("_")
