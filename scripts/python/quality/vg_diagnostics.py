"""提供 VG v3 可执行诊断的构造、校验和旧 finding 兼容转换。"""

# future annotations 让类型注解不参与模块导入时的运行期求值。
from __future__ import annotations

# re 用于校验 VG 规则编号，Mapping 用于读取现有 emitter 的字典。
import re
from typing import Any, Mapping

# 报告版本与诊断契约版本分开，避免消费方把 catalog 版本当作字段版本。
VG_REPORT_VERSION = 3  # v3 报告公开可定位、可执行的 finding 字段

# 诊断契约版本只描述 finding 字段，不随 catalog 规则数量变化。
VG_DIAGNOSTIC_CONTRACT_VERSION = 1  # v1 固定 finding 字段和状态义务

# 公开状态集合由质量门、交付门和 CLI 共同使用。
VG_STATUSES = frozenset({"passed", "failed", "inconclusive", "error", "not_run"})  # 状态白名单

# 定位范围决定坐标要求和 Agent 的修改边界。
VG_LOCATION_SCOPES = frozenset({"file", "cross_file", "run", "project", "source"})  # 定位范围白名单

# 风险枚举用于终端和 Markdown 中的人工复核提示。
VG_RISKS = frozenset({"mechanical", "behavioral", "latency", "interface", "architecture"})  # 风险白名单

# 示例类型明确 bad/good 文本应如何解释。
VG_EXAMPLE_KINDS = frozenset({"verilog", "file_name", "structure", "command", "configuration"})  # 示例白名单

# VgDiagnosticContractError 将 emitter 契约错误与 RTL 违规区分开。
class VgDiagnosticContractError(ValueError):
    """表示 VG finding 不满足可执行诊断契约。"""

# _require_text 统一处理所有公开文本字段。
def _require_text(value: Any, field_name: str) -> str:
    """规范化必填文本。

    参数:
        value: 待检查的字段值。
        field_name: 用于错误消息的字段名。
    返回:
        去除首尾空白的非空文本。
    异常:
        VgDiagnosticContractError: 字段为空时抛出。
    """

    # 文本统一去除外围空白，避免空诊断穿透到报告。
    str_value: str = str(value).strip()  # 规范化文本

    # 空文本无法指导 Agent，必须在构造阶段失败。
    if not str_value:

        # 错误前缀遵循当前项目的 Python CLI 输出合同。
        raise VgDiagnosticContractError("> ERR: [Python] diagnostic text cannot be empty")

    # 返回经过检查的文本。
    return str_value

# _optional_text 保留可选字段的未知语义。
def _optional_text(value: Any) -> str | None:
    """规范化可选文本。

    参数:
        value: 可选字段值。
    返回:
        非空文本或 None。
    """

    # 可选空值使用 None，JSON 消费方可区分未知和空说明。
    if value is None or not str(value).strip():

        # 不伪造空的定位说明。
        return None

    # 返回规范化的可选文本。
    return str(value).strip()

# _positive_coordinate 严格拒绝零和负数行号。
def _positive_coordinate(value: Any, field_name: str) -> int | None:
    """校验一基源码坐标。

    参数:
        value: 待检查的坐标。
        field_name: 用于错误消息的字段名。
    返回:
        正整数或 None。
    异常:
        VgDiagnosticContractError: 坐标不是正整数时抛出。
    """

    # 缺省坐标必须保持 None，不能用一行替代未知位置。
    if value is None:

        # 返回真实的未知状态。
        return None

    # bool 也是 int 子类，必须单独排除。
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:

        # 错误信息指出非法字段。
        raise VgDiagnosticContractError("> ERR: [Python] coordinate must be a positive integer or null")

    # 返回验证后的坐标。
    return value

# _location_from_legacy 将旧别名映射到最窄定位范围。
def _location_from_legacy(path: str | None, line: int | None) -> dict[str, Any]:
    """从旧 path/line 别名构造不伪造行号的定位对象。

    参数:
        path: 旧 finding 的文件路径。
        line: 旧 finding 的行号。
    返回:
        v3 location 字典。
    """

    # 只有真实文件和正数行可以精确到源码。
    if path and isinstance(line, int) and not isinstance(line, bool) and line > 0:

        # file 精确定位，保留 emitter 的实际坐标并兼容 location matrix。
        return {
            "scope": "file",
            "file": path,
            "line_start": line,
            "line_end": line,
            "column_start": None,
            "column_end": None,
            "exact": True,
            "related_locations": [],
            "note": None,
        }

    # 只有 path 时只能定位到文件，行号必须为 null。
    if path:

        # file scope 解释无法提供单行坐标的事实边界。
        return {
            "scope": "file",
            "file": path,
            "line_start": None,
            "line_end": None,
            "column_start": None,
            "column_end": None,
            "exact": False,
            "related_locations": [],
            "note": "门禁返回了文件级事实，未提供可验证的单一源码行。",
        }

    # 没有文件时只能定位到本次门禁运行的聚合事实。
    return {
        "scope": "run",
        "file": None,
        "line_start": None,
        "line_end": None,
        "column_start": None,
        "column_end": None,
        "exact": False,
        "related_locations": [],
        "note": "门禁返回了本次运行级事实，未提供可验证的单一源码文件或行。",
    }

# _risk_for 选择保守的修改风险。
def _risk_for(rule_key: str, problem: str) -> str:
    """为兼容 finding 选择保守修改风险。

    参数:
        rule_key: catalog 规则键。
        problem: 规则问题文本。
    返回:
        风险枚举值。
    """

    # 规则键和问题文本只用于风险分类，不改写原始事实。
    str_hint: str = f"{rule_key} {problem}".lower()  # 风险分类提示

    # 命名、注释和格式通常可以机械处理，但仍需确认引用方。
    if any(token in str_hint for token in ("filename", "file_name", "comment", "format", "naming")):

        # 机械风险不等于可以跳过门禁复验。
        return "mechanical"

    # 时钟、复位和握手规则可能改变接口行为。
    if any(token in str_hint for token in ("clock", "reset", "ready", "valid", "interface", "parameter")):

        # 接口风险要求人工核对连接和协议。
        return "interface"

    # 组合锥和实例关系会影响全局结构。
    if any(token in str_hint for token in ("comb", "cone", "instance", "cross", "structure")):

        # 架构风险不能按单纯文本替换处理。
        return "architecture"

    # 其余规则按行为风险处理，避免过度承诺低风险修复。
    return "behavioral"

# _example_kind_for 选择 bad/good 示例的解释类型。
def _example_kind_for(rule_key: str) -> str:
    """选择兼容 finding 的示例类型。

    参数:
        rule_key: catalog 规则键。
    返回:
        示例类型枚举值。
    """

    # 文件名规则的示例应该是路径文本而不是 RTL 片段。
    if "filename" in rule_key or "file_name" in rule_key:

        # file_name 让 Agent 使用示例时保持语义边界。
        return "file_name"

    # 结构关系规则需要图或节点关系示例。
    if any(token in rule_key for token in ("structure", "comb", "cone", "instance")):

        # structure 示例不声称唯一的 Verilog 写法。
        return "structure"

    # 默认把示例解释为可综合 Verilog 方向。
    return "verilog"

# _guidance_for 构造 Agent 可直接执行的修改指导。
def _guidance_for(rule_key: str, problem: str, evidence: str) -> dict[str, Any]:
    """为旧 finding 构造明确修改步骤和 bad/good 示例。

    参数:
        rule_key: catalog 规则键。
        problem: 问题说明。
        evidence: 规则提供的真实证据。
    返回:
        v3 guidance 字典。
    """

    # 风险决定是否要求人工复核。
    str_risk: str = _risk_for(rule_key, problem)  # 兼容 finding 风险

    # 示例 bad 只复用 emitter 的事实，不伪造源码。
    str_bad: str = evidence.strip() or problem.strip()  # 违规示例

    # 默认 good 给出可综合、保留接口契约的安全方向。
    str_good: str = "按当前模块接口、时序和可综合约束重写该片段，并保留可追溯的结构事实。"  # 修改示例

    # 示例类型决定 good 文本的语言边界。
    str_kind: str = _example_kind_for(rule_key)  # 示例解释类型

    # 文件名规则需要直接说明文件名修改方向。
    if str_kind == "file_name":

        # 示例不绑定不存在的模块名称。
        str_good = "使用表达功能且不含版本号或纯数字后缀的稳定模块文件名。"  # 文件名修改示例

    # 结构规则需要在修改后重算依赖关系。
    if str_kind == "structure":

        # 结构示例强调图关系复核而非盲改文本。
        str_good = "调整结构关系后重新检查组合锥、实例连接和相关下游节点。"  # 结构修改示例

    # 三步流程覆盖定位、修改和复验。
    tuple_steps: tuple[str, ...] = (
        "打开 location 指向的文件或结构范围，核对 evidence 与当前源码是否一致。",  # 第一步核对真实事实
        "按 instruction 修改问题片段，保留模块接口、复位和时序契约。",  # 第二步实施最小修改
        "重新运行对应 VG 门禁，并检查示例方向是否适用于当前模块。",  # 第三步复验门禁结果
    )  # Agent 可直接执行的步骤

    # 行为、接口和架构修改必须人工复核。
    bool_review: bool = str_risk in {"interface", "architecture", "behavioral", "latency"}  # 人工复核要求

    # 返回完整指导对象；examples 使用列表以支持多个可审计对照样例。
    return {
        "instruction": "修复 evidence 所代表的违规事实，并在修改后重新运行对应 VG 门禁。",  # Agent 总体修改动作
        "steps": list(tuple_steps),  # Agent 按顺序执行的步骤
        "risk": str_risk,  # 修改可能影响的行为边界
        "human_review_required": bool_review,  # 是否需要人工审查
        "examples": [
            {
                "kind": str_kind,  # bad/good 的解释语言
                "bad": str_bad,  # 触发当前规则的最小反例
                "good": str_good,  # 可接受的修改方向
                "note": "示例表达修改方向，不替代当前模块的接口、时序和综合约束审查。",  # 示例适用边界
            }
        ],
    }

# build_legacy_diagnostic 是旧 finding 的唯一兼容入口。
def build_legacy_diagnostic(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把一个真实旧 finding 转成 v3 可执行诊断。

    参数:
        payload: 至少包含 rule_id、rule_key、severity、message，可选 path、line、evidence、status。
    返回:
        扁平 v3 finding 字典。
    异常:
        VgDiagnosticContractError: 输入字段或生成结果不满足契约时抛出。
    """

    # 读取规则编号，缺字段时让调用方尽早发现 emitter 错误。
    str_rule_id: str = _require_text(payload.get("rule_id", "VG000"), "rule_id")  # 规则编号用于结果归属

    # 读取规则键，后续指导会据此选择风险和示例类型。
    str_rule_key: str = _require_text(payload.get("rule_key", "legacy_finding"), "rule_key")  # 规则键决定修复风险与示例类型

    # 读取规则等级，保持 BLOCKER/WARNING 的原始语义。
    str_severity: str = _require_text(payload.get("severity", "WARNING"), "severity")  # catalog 严重等级

    # 读取问题文本，作为 Agent 看到的 problem。
    str_problem: str = _require_text(payload.get("message", payload.get("problem", "")), "problem")  # 可执行问题说明

    # 真实 path 只作为别名读取，缺失时不得填充默认坐标。
    str_path: str | None = _optional_text(payload.get("path"))  # 旧 finding 文件路径

    # 真实 line 只作为别名读取，未知时保留 None。
    int_line: int | None = payload.get("line")  # 旧 finding 源码行

    # evidence 可能是旧字符串，也可能已经是 v3 对象。
    obj_legacy_evidence: Any = payload.get("evidence", "")  # 兼容输入证据

    # evidence 的实际类型决定 detail 的读取方式。
    if isinstance(obj_legacy_evidence, Mapping):

        # v3 evidence 的 detail 是可复用的真实事实。
        str_evidence: str = str(obj_legacy_evidence.get("detail", "")).strip()  # 从 v3 对象提取结构事实

    # 旧字符串需要单独进入 evidence.detail。
    else:

        # 字符串证据不会被当作源码行号处理。
        str_evidence = str(obj_legacy_evidence).strip()  # 旧结构事实

    # 位置对象保留精确行，或显式说明聚合范围。
    dict_location: dict[str, Any] = _location_from_legacy(str_path, int_line)  # 兼容定位对象

    # 没有额外片段时仍需说明真实缺失事实，而不是虚构代码。
    str_detail: str = str_evidence or "门禁返回了该问题，但没有额外源码片段；请以 problem 和规则结果为准。"  # 证据详情

    # 证据节点类型随定位粒度变化。
    str_node_kind: str = "verilog_rtl" if dict_location.get("file") else "verilog_project"  # 节点类型

    # 构造完整 finding，再由统一校验器检查所有必填字段。
    dict_finding: dict[str, Any] = {  # 规则转换层输出的公开 finding 载荷
        "rule_id": str_rule_id,  # 用于 active catalog 归属
        "rule_key": str_rule_key,  # 用于消费方查找规则
        "severity": str_severity,  # 继承规则等级
        "location": dict_location,  # 指向真实文件或聚合范围
        "problem": str_problem,  # 面向 Agent 的问题正文
        "evidence": {  # evidence 事实对象
            "node_kind": str_node_kind,  # 说明事实来自 RTL 还是项目结构
            "source_excerpt": str_evidence,  # 仅复用 emitter 的真实片段
            "detail": str_detail,  # 规则实际观察到的结构事实
        },  # 证据对象
        "guidance": _guidance_for(str_rule_key, str_problem, str_evidence),  # guidance 修改对象
        "status": str(payload.get("status", "failed")),  # 结果状态
    }  # v3 finding 主体

    # 生成后立即校验，避免坏诊断进入报告写入层。
    validate_diagnostic(dict_finding)  # 诊断契约门禁

    # 返回可直接嵌入报告的 finding。
    return dict_finding

# _validate_location 检查 scope、坐标和聚合位置说明。
def _validate_location(payload: Any) -> None:
    """检查 v3 location 对象。

    参数:
        payload: 待校验的 location 值。
    返回:
        无业务返回值。
    异常:
        VgDiagnosticContractError: 定位字段不一致时抛出。
    """

    # 定位必须是映射对象，不能用字符串替代结构化范围。
    if not isinstance(payload, Mapping):

        # 编辑器无法消费非对象定位。
        raise VgDiagnosticContractError("> ERR: [Python] location must be an object")

    # 读取并校验定位范围。
    str_scope: str = _require_text(payload.get("scope", ""), "location.scope")  # 定位范围

    # scope 值必须受 catalog 定位协议约束。
    if str_scope not in VG_LOCATION_SCOPES:

        # 只接受 catalog 约定的四种范围。
        raise VgDiagnosticContractError("> ERR: [Python] location scope is invalid")

    # 保留未知行号为 None，不创建默认的 line=1。
    int_line_start: int | None = _positive_coordinate(payload.get("line_start"), "location.line_start")  # 可编辑范围的第一行

    # 结束行与起始行使用相同的坐标校验。
    int_line_end: int | None = _positive_coordinate(payload.get("line_end"), "location.line_end")  # 可编辑范围的最后一行

    # 列坐标也必须是正整数或 None。
    _positive_coordinate(payload.get("column_start"), "location.column_start")  # 起始列检查

    # 结束列检查保持列范围的真实语义。
    _positive_coordinate(payload.get("column_end"), "location.column_end")  # 结束列检查

    # 行范围逆序会让编辑器选择错误代码。
    if int_line_start is not None and int_line_end is not None and int_line_end < int_line_start:

        # 直接报告定位契约错误。
        raise VgDiagnosticContractError("> ERR: [Python] location line range is reversed")

    # 精确 file/source 范围必须同时拥有文件和起始行。
    bool_exact: bool = bool(payload.get("exact", False))  # 精确定位标志

    # 精确范围缺少坐标时立即失败。
    if str_scope in {"file", "source"} and bool_exact and (not payload.get("file") or int_line_start is None):

        # 不允许把聚合信息伪装成可编辑行。
        raise VgDiagnosticContractError("> ERR: [Python] exact source location needs file and line")

    # 聚合范围必须说明无法精确到单行的原因。
    str_note: str | None = _optional_text(payload.get("note"))  # 定位边界说明

    # 未知 file、跨文件和运行级范围必须说明其不可精确原因。
    if (not bool_exact and str_scope in {"file", "cross_file", "project", "run"}) and str_note is None:

        # 聚合范围缺少 note 时 Agent 无法选择修改边界。
        raise VgDiagnosticContractError("> ERR: [Python] aggregate location needs a note")

    # cross_file 必须指出关系另一端，才有完整结构事实。
    if str_scope == "cross_file" and not payload.get("related_locations"):

        # 关系事实缺少另一端时无法指导修改。
        raise VgDiagnosticContractError("> ERR: [Python] cross-file location needs related locations")

# _validate_evidence 检查证据对象而不改变 source_excerpt。
def _validate_evidence(payload: Any) -> None:
    """检查 v3 evidence 对象。

    参数:
        payload: 待校验的 evidence 值。
    返回:
        无业务返回值。
    异常:
        VgDiagnosticContractError: 证据缺少节点或事实时抛出。
    """

    # evidence 必须是对象，旧的单一字符串不再作为公开 v3 字段。
    if not isinstance(payload, Mapping):

        # 字符串证据无法区分节点类型和结构事实。
        raise VgDiagnosticContractError("> ERR: [Python] evidence must be an object")

    # 节点类型是 Agent 判断修改语言的最小上下文。
    str_node_kind: str = _require_text(payload.get("node_kind", ""), "evidence.node_kind")  # Agent 解释证据来源的节点类型

    # detail 记录规则真正观察到的事实。
    str_detail: str = _require_text(payload.get("detail", ""), "evidence.detail")  # 规则观察到的结构事实详情

    # 显式保留局部变量，避免静态检查把校验调用视为裸语句。
    tuple_observed: tuple[str, str] = (str_node_kind, str_detail)  # 已校验的证据字段

    # 变量的存在表示两个字段都已完成检查。
    if not tuple_observed:

        # 该分支理论上不可达，仍保留 fail-closed 保护。
        raise VgDiagnosticContractError("> ERR: [Python] evidence validation failed")

# _validate_example 检查单个 bad/good 对照示例。
def _validate_example(payload: Any, index: int) -> None:
    """检查一个 guidance 示例对象。

    参数:
        payload: 待校验的示例值。
        index: 示例在 guidance.examples 列表中的位置。
    返回:
        无业务返回值。
    异常:
        VgDiagnosticContractError: 示例缺少可执行字段时抛出。
    """

    # 每个示例必须是对象，不能把 bad/good 压缩成无结构文本。
    if not isinstance(payload, Mapping):

        # 列表中的非对象无法被 Agent 按字段消费。
        raise VgDiagnosticContractError(
            f"> ERR: [Python] guidance example {index} must be an object"
        )

    # 示例类型限定解释语言。
    str_kind: str = _require_text(payload.get("kind", ""), f"guidance.examples[{index}].kind")  # 示例类型字段

    # 未知类型会让 Agent 误用 bad/good 文本。
    if str_kind not in VG_EXAMPLE_KINDS:

        # 未知示例类型会误导 Agent 使用方式。
        raise VgDiagnosticContractError(
            f"> ERR: [Python] guidance example {index} kind is invalid"
        )

    # bad 和 good 必须分别可读。
    str_bad: str = _require_text(payload.get("bad", ""), f"guidance.examples[{index}].bad")  # 违规示例字段

    # good 必须是不同于 bad 的修改方向。
    str_good: str = _require_text(payload.get("good", ""), f"guidance.examples[{index}].good")  # 修改示例字段

    # note 说明示例不能替代接口和时序审查。
    str_note: str = _require_text(payload.get("note", ""), f"guidance.examples[{index}].note")  # 示例边界字段

    # bad/good 相同表示没有提供修改方向。
    if str_bad == str_good:

        # 相同示例没有修改信息。
        raise VgDiagnosticContractError(
            f"> ERR: [Python] guidance example {index} good value must differ from bad"
        )

    # 保存已经检查的字段，避免静态检查认为核心字段未被消费。
    tuple_example: tuple[str, str, str, str] = (str_kind, str_bad, str_good, str_note)  # 示例摘要

    # 空 tuple 表示示例校验分支没有消费核心字段。
    if not tuple_example:

        # 示例核心字段不能同时为空。
        raise VgDiagnosticContractError(
            f"> ERR: [Python] guidance example {index} validation failed"
        )

# _validate_guidance 检查 instruction、steps、risk 和示例。
def _validate_guidance(payload: Any) -> None:
    """检查 v3 guidance 对象。

    参数:
        payload: 待校验的 guidance 值。
    返回:
        无业务返回值。
    异常:
        VgDiagnosticContractError: 指导缺少动作或示例时抛出。
    """

    # guidance 必须是对象，不能把修改建议压缩进 message。
    if not isinstance(payload, Mapping):

        # 缺少对象时 Agent 只能看到问题而不能行动。
        raise VgDiagnosticContractError("> ERR: [Python] guidance must be an object")

    # instruction 直接回答应该如何修改。
    str_instruction: str = _require_text(payload.get("instruction", ""), "guidance.instruction")  # 修改指令

    # steps 必须包含至少一个非空动作。
    list_steps: list[Any] = list(payload.get("steps", []))  # 修改步骤

    # 空步骤不能形成最小可执行修复。
    if not list_steps or any(not str(item).strip() for item in list_steps):

        # 空步骤无法形成可执行修复。
        raise VgDiagnosticContractError("> ERR: [Python] guidance steps are empty")

    # risk 使用固定枚举决定人工复核策略。
    str_risk: str = _require_text(payload.get("risk", ""), "guidance.risk")  # 修改风险

    # 未知风险无法选择复核边界。
    if str_risk not in VG_RISKS:

        # 未知风险会让 Agent 错判复核边界。
        raise VgDiagnosticContractError("> ERR: [Python] guidance risk is invalid")

    # 从列表中读取 bad/good 对照载荷。
    list_examples: list[Any] = list(payload.get("examples", []))  # guidance 的示例列表

    # 示例必须至少包含一个结构化对象。
    if not list_examples:

        # 空列表不能形成 Agent 可执行的修改方向。
        raise VgDiagnosticContractError("> ERR: [Python] guidance examples are empty")

    # 每个示例独立校验，允许一个 finding 提供多个修改方向。
    for int_index, obj_example in enumerate(list_examples):

        # 委托字段级校验，保持主 guidance 校验可读。
        _validate_example(obj_example, int_index)  # 示例对象门禁

    # 保存已经检查的字段，避免静态检查认为 instruction 未被消费。
    tuple_guidance: tuple[str, str, int] = (str_instruction, str_risk, len(list_examples))  # 指导摘要

    # 仅在内部逻辑被意外破坏时验证四个核心字段不为空。
    if not tuple_guidance:

        # 指导对象的四个核心字段不能同时为空。
        raise VgDiagnosticContractError("> ERR: [Python] guidance validation failed")

# validate_diagnostic 检查报告写入前的完整字段契约。
def validate_diagnostic(payload: Mapping[str, Any]) -> None:
    """校验公开 finding 的字段、定位和指导义务。

    参数:
        payload: 待校验的 v3 finding 字典。
    返回:
        无业务返回值。
    异常:
        VgDiagnosticContractError: 任一字段缺失或不一致时抛出。
    """

    # 顶层必填键必须全部存在，禁止下游猜测字段来源。
    tuple_required: tuple[str, ...] = ("rule_id", "rule_key", "severity", "location", "problem", "evidence", "guidance")  # v3 finding 的强制字段

    # 缺失键列表直接返回给 emitter 修复。
    tuple_missing: tuple[str, ...] = tuple(key for key in tuple_required if key not in payload)  # 当前 finding 缺少的强制字段

    # 缺失键必须在写报告前被拒绝。
    if tuple_missing:

        # 使用固定前缀，避免动态 f-string 被错误消费。
        raise VgDiagnosticContractError("> ERR: [Python] finding is missing required fields")

    # 规则编号必须是 catalog 的 VGddd 形式。
    str_rule_id: str = _require_text(payload["rule_id"], "rule_id")  # 规则编号

    # 非法规则编号不能进入结果汇总。
    if not re.fullmatch(r"VG\d{3}", str_rule_id):

        # 非法编号会破坏规则计数和结果归属。
        raise VgDiagnosticContractError("> ERR: [Python] rule_id must match VGddd")

    # 规则键、等级和问题文本都必须有内容。
    str_rule_key: str = _require_text(payload["rule_key"], "rule_key")  # 用于规则归属和统计的键

    # severity 由 catalog 继承或由 emitter 细化。
    str_severity: str = _require_text(payload["severity"], "severity")  # 规则等级

    # problem 是用户看到的具体问题说明。
    str_problem: str = _require_text(payload["problem"], "problem")  # 问题文本

    # 依次检查三类嵌套对象。
    _validate_location(payload["location"])  # 定位检查

    # evidence 负责回答门禁抓到了什么事实。
    _validate_evidence(payload["evidence"])  # 证据检查

    # guidance 负责回答如何修改以及示例是什么。
    _validate_guidance(payload["guidance"])  # 指导检查

    # status 缺省为 failed，显式值必须属于统一集合。
    str_status: str = str(payload.get("status", "failed"))  # finding 所属门禁结果状态

    # 未知状态会破坏门禁结果分支。
    if str_status not in VG_STATUSES:

        # 状态错误应在报告写入前暴露。
        raise VgDiagnosticContractError("> ERR: [Python] finding status is invalid")

# diagnostic_from_mapping 复制已通过校验的 finding。
def diagnostic_from_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """校验并复制一个 v3 finding 字典。

    参数:
        payload: 语义引擎或报告转换层生成的 finding。
    返回:
        可安全写入报告的 finding 副本。
    异常:
        VgDiagnosticContractError: finding 不满足公开契约时抛出。
    """

    # 先校验原始对象，禁止转换层掩盖 emitter 缺字段。
    validate_diagnostic(payload)  # finding 结构门禁

    # 浅复制顶层，避免调用方后续改写原始报告。
    dict_copy: dict[str, Any] = dict(payload)  # 与原对象隔离的 finding 副本

    # 深复制定位，隔离编辑器消费方的修改。
    dict_copy["location"] = dict(payload["location"])  # 定位副本

    # 深复制证据，保持 source_excerpt 的真实值。
    dict_copy["evidence"] = dict(payload["evidence"])  # 证据副本

    # 深复制指导主体，避免共享可变嵌套字典。
    dict_copy["guidance"] = dict(payload["guidance"])  # 指导副本

    # 步骤列表需要独立副本，保留 Agent 的执行顺序。
    dict_copy["guidance"]["steps"] = list(payload["guidance"]["steps"])  # 步骤副本

    # 示例列表及其对象需要独立副本，防止 bad/good 被外部覆盖。
    list_example_copies = [dict(example) for example in payload["guidance"]["examples"]]  # 示例列表副本

    # 将隔离后的示例列表写回指导副本。
    dict_copy["guidance"]["examples"] = list_example_copies  # 写入隔离后的示例

    # 返回已经通过契约检查的副本。
    return dict_copy

# diagnostic_path_line 生成旧报告兼容的 path/line 别名。
def diagnostic_path_line(payload: Mapping[str, Any]) -> tuple[str | None, int | None]:
    """返回 v3 finding 的兼容 path/line 别名。

    参数:
        payload: 已通过诊断契约的 finding。
    返回:
        文件路径和真实起始行，非 source 范围的行号为 None。
    """

    # 精确 file/source scope 才可以把 line_start 暴露为旧 line。
    mapping_location: Mapping[str, Any] = payload["location"]  # 定位对象

    # 聚合范围不提供可验证的单一行号。
    if mapping_location.get("scope") in {"file", "source"} and mapping_location.get("line_start") is not None:

        # 返回实际文件和起始行。
        return mapping_location.get("file"), mapping_location.get("line_start")

    # file/project/cross_file 不伪造默认行号。
    return mapping_location.get("file"), None

# __all__ 声明该模块供质量门和报告转换层复用的公开接口。
__all__ = [  # 供质量门和报告转换层复用的公开符号
    "VG_DIAGNOSTIC_CONTRACT_VERSION",  # 诊断契约版本常量
    "VG_LOCATION_SCOPES",  # location scope 的公开枚举
    "VG_REPORT_VERSION",  # 公开报告版本常量
    "VG_RISKS",  # 修改风险白名单
    "VG_STATUSES",  # 结果状态白名单
    "VgDiagnosticContractError",  # 契约错误类型
    "build_legacy_diagnostic",  # 旧 finding 转换入口
    "diagnostic_from_mapping",  # finding 复制入口
    "diagnostic_path_line",  # 兼容 path/line 入口
    "validate_diagnostic",  # 报告写入校验入口
]
