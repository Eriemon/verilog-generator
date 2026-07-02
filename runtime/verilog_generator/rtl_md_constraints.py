"""读取并校验 RTL Markdown 约束目录。"""

# 延迟注解解析，避免导入期处理复杂类型。
from __future__ import annotations

# 标准库依赖负责 JSON 资产读取、缓存和路径定位。
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# 约束目录资产随 skill 打包，runtime 只读取这个稳定 JSON。
ASSET_PATH = Path(__file__).resolve().parents[2] / "assets" / "rtl_md_constraints.json"  # RTL Markdown 约束目录路径

# 公开入口读取并缓存约束目录，供 prompt 和 static lint 共用。
@lru_cache(maxsize=1)

# load_rtl_md_constraints 是 runtime 读取约束资产的唯一入口。
def load_rtl_md_constraints() -> dict[str, Any]:
    """读取稳定的 Verilog RTL Markdown 约束目录。

    参数:
        无。

    返回:
        约束目录字典，包含 rules、计数摘要和包内一致性元数据。

    异常:
        ValueError: catalog 缺少规则、计数不一致或包含旧编号元数据。
    """

    # JSON 资产读取后立即做结构校验，避免损坏 catalog 进入 prompt。
    dict_payload = json.loads(ASSET_PATH.read_text(encoding="utf-8"))  # RTL Markdown 约束目录载荷

    # 校验规则计数、唯一 id、执行级别和语义命名要求。
    _validate_catalog(dict_payload)

    # 返回缓存的目录对象，调用方应只读使用。
    return dict_payload

# prompt 摘要入口把完整 catalog 压缩成生成提示词片段。
def summarize_constraints_for_prompt(*, max_rules_per_group: int = 5) -> str:
    """生成适合注入 prompt 的 RTL Markdown 约束摘要。

    参数:
        max_rules_per_group: 预留兼容参数；当前摘要继续列出每个 topic 的所有规则。

    返回:
        多行英文约束摘要，保留既有 prompt 文案和规则 id 形式。
    """

    # 参数保留给旧调用方，当前版本不裁剪 topic 内规则。
    del max_rules_per_group

    # 读取已校验 catalog，保证摘要中的计数字段可信。
    dict_catalog = load_rtl_md_constraints()  # 已校验约束目录

    # rules 列表后续会按 topic 聚合。
    list_rules = list(dict_catalog["rules"])  # catalog 中的规则对象列表

    # topic 顺序固定排序，保证 prompt 文本可复现。
    list_topic_order = sorted({str(rule["topic"]) for rule in list_rules})  # 规则主题顺序

    # MUST 说明保留旧版英文措辞，强调自动 lint 会覆盖高置信阻断规则。
    str_must_summary = (  # prompt 中说明 MUST 规则阻断属性的固定英文句
        "MUST rules are blocking error constraints. "  # MUST 阻断级规则说明前半句
        "High-confidence MUST rules are also checked by static lint."  # MUST 自动 lint 覆盖说明后半句
    )

    # REC 说明保留旧版英文措辞，强调偏离建议规则时必须记录理由。
    str_rec_summary = (  # prompt 中说明 REC 偏离需要记录理由的固定英文句
        "REC rules are default warning-level preferences. "  # REC 建议级规则说明前半句
        "Record any REC deviation in manifest checks with a concrete reason."  # REC 偏离记录要求后半句
    )

    # manifest 说明要求 REC 偏离必须有 implementation 或 reviewability 证据。
    str_manifest_summary = (  # prompt 中要求 manifest 记录偏离证据的固定英文句
        "Manifest checks must record implementation_assessment or "  # manifest 记录要求前半句
        "reviewability_assessment evidence for every relevant REC deviation."  # REC 偏离证据要求后半句
    )

    # 覆盖行集中展示总数、MUST/error 数和 REC/warning 数。
    str_coverage_summary = (  # prompt 中展示 catalog 规则计数的覆盖摘要
        f"Coverage: {dict_catalog['total_rules']} rules, "  # catalog 总规则数量片段
        f"{dict_catalog['required_rules']} MUST/error rules, "  # MUST/error 规则数量片段
        f"{dict_catalog['advisory_rules']} REC/warning rules."  # 建议规则数量片段
    )

    # 摘要头部保留旧版英文说明，避免改变 prompt 兼容性。
    list_lines = [
        "RTL Markdown constraints:",  # 摘要标题
        str_must_summary,  # 阻断规则级别说明行
        str_rec_summary,  # 建议规则偏离说明行
        str_manifest_summary,  # manifest 证据要求说明行
        str_coverage_summary,  # catalog 三类计数覆盖行
    ]  # prompt 摘要行

    # 每个 topic 单独列出规则 id、severity 和 enforcement。
    for str_topic in list_topic_order:

        # 当前 topic 下的规则保持 catalog 原始顺序。
        list_topic_rules = [rule for rule in list_rules if rule["topic"] == str_topic]  # 当前主题规则列表

        # 逐条规则生成 prompt 片段，再合并为主题摘要。
        list_rule_fragments = [
            f"{rule['id']}({rule['severity']}/{rule['enforcement']})"  # 单条规则的 prompt 摘要片段
            for rule in list_topic_rules  # 当前主题内按 catalog 顺序渲染的规则
        ]  # 当前主题规则摘要片段

        # 单行渲染规则 id 和执行级别，方便 prompt 扫描。
        str_rendered_rules = ", ".join(list_rule_fragments)  # 当前主题的规则摘要文本

        # 将主题摘要追加到 prompt 片段中。
        list_lines.append(f"- {str_topic}: {str_rendered_rules}")

    # 返回与旧实现一致的换行拼接文本。
    return "\n".join(list_lines)

# 自动 lint 覆盖集合用于 static_lint 选择可执行的规则。
def automated_constraint_ids() -> set[str]:
    """返回由自动静态检查覆盖的规则 id 集合。

    参数:
        无。

    返回:
        enforcement 以 automated_ 开头的规则 id 集合。
    """

    # 自动规则集合读取同一份 catalog，保证 static lint 规则和发布资产一致。
    dict_catalog = load_rtl_md_constraints()  # static lint 选择自动规则的 catalog

    # 只暴露自动化规则，prompt-only 和 review-only 规则不进入静态 lint。
    return {
        str(rule["id"])  # 自动化规则 id
        for rule in dict_catalog["rules"]  # catalog 规则对象
        if str(rule.get("enforcement", "")).startswith("automated_")  # 自动执行级别
    }

# catalog 校验器集中保护包内 RTL Markdown 约束资产。
def _validate_catalog(payload: dict[str, Any]) -> None:
    """校验 RTL Markdown 约束 catalog 的内部一致性。

    参数:
        payload: 从 rtl_md_constraints.json 读取出的 catalog 字典。

    返回:
        无；校验失败时抛出 ValueError。

    异常:
        ValueError: catalog 缺少规则、计数字段不一致或规则元数据不合法。
    """

    # rules 是 catalog 的主数据，所有计数和摘要都从它派生。
    list_rules = payload.get("rules")  # catalog 原始规则列表

    # 缺少规则数组说明资产损坏。
    if not isinstance(list_rules, list) or not list_rules:

        # 阻止空 catalog 进入 prompt 或 static lint。
        raise ValueError("> ERR: [Python] RTL constraint catalog must contain a non-empty rules array.")

    # total_rules 必须等于实际规则数量。
    if payload.get("total_rules") != len(list_rules):

        # 计数不一致意味着资产打包时没有同步摘要字段。
        raise ValueError("> ERR: [Python] RTL constraint catalog total_rules does not match rules length.")

    # required_rules 记录会阻断生成或审查的 error 规则数量。
    int_required_rules = sum(1 for rule in list_rules if rule.get("severity") == "error")  # MUST 阻断规则总数

    # advisory_rules 记录允许带理由偏离的 warning 规则数量。
    int_advisory_rules = sum(1 for rule in list_rules if rule.get("severity") == "warning")  # REC 建议规则总数

    # required/advisory 两个摘要计数必须和 rules 内容一致。
    if payload.get("required_rules") != int_required_rules or payload.get("advisory_rules") != int_advisory_rules:

        # 阻止错误计数误导报告和 prompt 覆盖信息。
        raise ValueError("> ERR: [Python] RTL constraint catalog severity counts are inconsistent.")

    # 规则 id 需要唯一且非空，供 lint 和 prompt 稳定引用。
    list_rule_ids = [str(rule.get("id") or "") for rule in list_rules]  # catalog 规则 id 列表

    # 重复或空 id 会让 issue code 与规则映射产生歧义。
    if len(list_rule_ids) != len(set(list_rule_ids)) or any(not item for item in list_rule_ids):

        # 直接拒绝不可靠的规则目录。
        raise ValueError("> ERR: [Python] RTL constraint catalog rule ids must be non-empty and unique.")

    # shuffle_seed 是当前发布包的稳定性护栏。
    if payload.get("shuffle_seed") != 20260609:

        # seed 变化说明 catalog 顺序可能被意外重排。
        raise ValueError("> ERR: [Python] RTL constraint catalog must preserve the shuffled package seed.")

    # semantic_rule_names 必须启用，避免旧编号式规则重新进入包。
    if not payload.get("semantic_rule_names"):

        # 规则名称必须保持语义化，不能回退到章节编号。
        raise ValueError("> ERR: [Python] RTL constraint catalog must use semantic rule names.")

    # enforcement 只允许这四种执行级别。
    set_allowed_enforcement = {
        "automated_error",  # 自动阻断级检查
        "automated_warning",  # 自动 warning 级检查
        "prompt_warning",  # prompt 提示但不自动 lint
        "review_error",  # 人工 review 阻断级检查
    }  # catalog 允许的执行级别

    # enforcement_counts 从实际规则重新统计后对比 payload 摘要。
    dict_enforcement_counts: dict[str, int] = {}  # 按 enforcement 聚合的规则数量

    # 逐条规则检查旧编号字段、摘要长度和执行级别。
    for rule in list_rules:

        # 旧章节字段不能随规则资产进入可发布目录。
        for str_banned_field in ("section", "小节", "章节"):

            # 发现旧编号元数据时直接失败，避免引用过期文档结构。
            if str_banned_field in rule:

                # 错误中包含 rule id，方便定位坏规则。
                raise ValueError(
                    f"> ERR: [Python] RTL constraint rule {rule.get('id')} still contains old numbering metadata."
                )

        # summary 过短通常说明规则正文抽取失败。
        str_summary = str(rule.get("summary") or "")  # 当前规则的人类可读摘要

        # 摘要至少需要能表达具体约束。
        if len(str_summary) < 8:

            # 报告缺失摘要的规则 id。
            raise ValueError(f"> ERR: [Python] RTL constraint rule {rule.get('id')} has an incomplete summary.")

        # enforcement 决定规则进入自动 lint、prompt 或 review。
        str_enforcement = str(rule.get("enforcement") or "")  # 当前规则执行级别

        # 未知执行级别会让下游不知道如何处理规则。
        if str_enforcement not in set_allowed_enforcement:

            # 保留未知 enforcement 的 repr，便于排查空白或拼写错误。
            raise ValueError(
                f"> ERR: [Python] RTL constraint rule {rule.get('id')} has unknown enforcement {str_enforcement!r}."
            )

        # error 规则不能降级为 prompt warning。
        if rule.get("severity") == "error" and str_enforcement == "prompt_warning":

            # 阻止 MUST/error 规则被错误标成仅提示。
            raise ValueError(
                f"> ERR: [Python] RTL constraint rule {rule.get('id')} downgrades an error rule to prompt warning."
            )

        # 统计执行级别数量，用于最终和 payload 摘要对比。
        dict_enforcement_counts[str_enforcement] = (
            dict_enforcement_counts.get(str_enforcement, 0) + 1  # 当前 enforcement 累积后的规则数量
        )

    # enforcement_counts 必须和从 rules 派生出的统计一致。
    if payload.get("enforcement_counts") != dict_enforcement_counts:

        # 计数字段不同步时提示重新生成 catalog 摘要。
        raise ValueError("> ERR: [Python] RTL constraint catalog enforcement_counts are inconsistent.")
