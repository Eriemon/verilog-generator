"""verify-repair 的 RTL patch 写回与 post-apply 证据辅助函数。"""

# 延迟类型求值，降低 helper 之间的导入耦合。
from __future__ import annotations

# JSON、复制和路径处理支撑 RTL patch 写回生命周期。
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 语义比较用于 post-apply equivalence 证据。
from .existing_rtl_improvement import compare_semantics
from .verify_repair_patch_plan import rtl_mutation_policy
from .verify_repair_support import validation_spec
from .verify_repair_testbench import backup_path
from scripts.python.validation.validation import validate_generated
from scripts.python.workflow.workspace import write_json

@dataclass(frozen=True)
class RtlMutationContext:
    """承载 RTL patch 写回阶段需要的稳定输入。"""

    # 源 RTL 顺序必须和 candidate_rtl_paths 对齐，写回时逐项 zip。
    list_source_paths: list[Path]  # 源 RTL 到候选 RTL 的配对顺序

    # 当前 run 目录统一承载 intervention 和 post-apply 证据。
    path_out_dir: Path  # RTL patch 写回阶段的工件根目录

    # 结构分析在写回后重新生成 validation spec，避免沿用旧文件内容。
    dict_analysis: dict[str, Any]  # post-apply validation 的结构事实

    # TB contract 提供活动 testbench 路径，写回验证会复制它到隔离 workspace。
    dict_tb_contract: dict[str, Any]  # active TB staging 的路径契约

    # patch candidate 保存候选 RTL 路径，并在写回后回填 backup/active 字段。
    dict_patch_candidate: dict[str, Any]  # 可写回 RTL 候选及审计字段

    # patch plan 保存候选可用性与人工确认文案，未应用时会写入 intervention。
    dict_rtl_patch_plan: dict[str, Any]  # 人工确认和风险说明来源

    # 自动化模式决定是否允许首轮 auto_apply，其他模式只能生成确认请求。
    str_automation_mode: str  # 写回授权策略名称

    # readiness 透传给写回后的 validation 与 equivalence，保持检查强度一致。
    str_readiness: str  # post-apply 检查强度档位

    # 外部验证开关只影响 post-apply validation，不改变 patch 写回判定。
    bool_run_external: bool  # 写回后 validation 的外部后端开关

    # decision 文件用于 resume 阶段批准或拒绝等待确认的 RTL patch。
    path_decision_source: Path | None  # 用户确认 JSON 的可选路径

# RTL 写回流程串联用户决策、自动应用和应用后验证证据。
def handle_rtl_mutation(
    obj_context: RtlMutationContext,
) -> tuple[dict[str, Any], Path | None, Path | None, Path | None]:
    """
    按策略和 decision 文件处理 RTL patch 写回。

    :param obj_context: RTL patch 写回所需的源文件、候选、策略和验证上下文。
    :return: 实际 mutation 策略、intervention 路径、validation 路径和 equivalence 路径。
    """

    # 写回阶段保持源 RTL 顺序，后续与 candidate_rtl_paths 配对覆盖。
    list_source_paths = obj_context.list_source_paths  # 本轮待写回源 RTL 顺序表

    # run 目录用于 intervention、candidate 回填和 post-apply 报告落盘。
    path_out_dir = obj_context.path_out_dir  # RTL mutation 工件输出目录

    # 结构分析只在 patch 应用后验证中复用，不参与是否写回的授权判断。
    dict_analysis = obj_context.dict_analysis  # post-apply validation 的结构输入

    # TB contract 提供 active testbench，写回后会复制到隔离验证目录。
    dict_tb_contract = obj_context.dict_tb_contract  # active TB 路径契约

    # patch candidate 同时提供候选 RTL 路径和 apply blocker。
    dict_patch_candidate = obj_context.dict_patch_candidate  # 候选 RTL 与审计字段载荷

    # patch plan 用于判断候选是否存在，并生成需要人工确认的说明。
    dict_rtl_patch_plan = obj_context.dict_rtl_patch_plan  # 候选可用性和确认上下文

    # automation mode 与 policy 共同决定首轮是否允许直接应用。
    str_automation_mode = obj_context.str_automation_mode  # 调用方请求的写回模式

    # readiness 只传递给 post-apply validation/equivalence，保持原验证强度。
    str_readiness = obj_context.str_readiness  # 写回后验证强度档位

    # 外部验证开关不影响 patch 选择，只影响写回后的验证后端。
    bool_run_external = obj_context.bool_run_external  # post-apply 外部工具执行开关

    # decision_source 存在时代表 resume 阶段，需要读取用户确认结果。
    path_decision_source = obj_context.path_decision_source  # 可选用户决策文件

    # 默认 mutation 策略先反映 automation mode 和候选 blocker。
    dict_policy = rtl_mutation_policy(str_automation_mode, dict_patch_candidate)  # RTL 写回的默认授权策略

    # 没有候选时只可能写 intervention。
    if not dict_rtl_patch_plan.get("candidate_available"):

        # 需要确认时写 intervention。
        path_intervention = (
            write_rtl_intervention(path_out_dir, dict_patch_candidate, dict_rtl_patch_plan)  # 无候选但需确认时的人工说明
            if dict_policy.get("confirmation_required")  # 策略要求人工确认
            else None  # report-only 路径不写 intervention
        )  # RTL 人工确认文件路径

        # 返回未应用状态。
        return dict_policy, path_intervention, None, None

    # decision 文件可覆盖等待确认状态。
    dict_decision = (
        read_decision(path_decision_source)  # resume 阶段读取用户决策 JSON
        if path_decision_source is not None  # 调用方提供 decision 文件
        else None  # 首轮运行没有用户决策
    )  # 用户提供的 RTL patch 应用决策

    # should_apply 汇总 decision 和 auto_apply 安全边界。
    bool_should_apply = False  # 本轮是否应写回 RTL

    # intervention 仅在等待人工确认时落盘。
    path_intervention: Path | None = None  # 等待用户确认时写出的 intervention 路径

    # 有 decision 时以 decision 为准。
    if dict_decision is not None:

        # 只有 apply/approve/confirm 才允许写回。
        bool_should_apply = decision_allows_apply(dict_decision)  # 用户 decision 是否明确批准写回

    # 无 decision 时，只有真正 auto_apply 且无 blocker 才写回。
    else:

        # 自动写回要求策略、请求模式和 blocker 同时满足安全边界。
        bool_auto_apply_without_decision = (
            dict_policy.get("policy") == "auto_apply"  # 策略允许自动写回
            and str_automation_mode == "auto_apply"  # 调用方请求自动应用
            and not dict_patch_candidate.get("apply_blockers")  # 候选没有自动应用阻断项
        )  # 未提供 decision 时是否仍满足 auto_apply 条件

        # auto_apply 安全边界满足时直接写回，否则生成确认请求。
        if bool_auto_apply_without_decision:

            # auto_apply 安全边界满足，允许直接覆盖源 RTL。
            bool_should_apply = True  # 当前分支确认本轮可写回源 RTL

        # 其他情况写 intervention 等待人工确认。
        else:

            # 写出人工确认请求。
            path_intervention = write_rtl_intervention(  # 等待用户确认的 rtl_intervention.json 路径
                path_out_dir,  # 人工确认文件落在当前 run 目录
                dict_patch_candidate,  # 展示候选路径和阻断原因
                dict_rtl_patch_plan,  # 展示 patch 类别和根因证据
            )

    # 未获准应用时返回默认策略。
    if not bool_should_apply:

        # 返回等待状态。
        return dict_policy, path_intervention, None, None

    # 写回上下文保持私有，避免扩大公开 API。
    dict_apply_context = {  # 让写回阶段同时读取源 RTL、候选 RTL、测试平台契约和用户决策
        "list_source_paths": list_source_paths,  # 写回目标 RTL 源文件集合
        "path_out_dir": path_out_dir,  # 应用后验证工件所在运行目录
        "dict_analysis": dict_analysis,  # 应用后验证所需结构分析产物
        "dict_tb_contract": dict_tb_contract,  # 活动测试平台分阶段契约
        "dict_patch_candidate": dict_patch_candidate,  # 将回填备份文件的补丁候选
        "str_automation_mode": str_automation_mode,  # 应用策略需要保留的自动化模式
        "str_readiness": str_readiness,  # 写回后验证等级
        "bool_run_external": bool_run_external,  # 写回后是否运行外部验证
        "dict_decision": dict_decision,  # 用户确认或拒绝写回的决策载荷
    }  # RTL 补丁覆盖写回阶段所需的隔离上下文

    # 写回 candidate RTL 并生成 post-apply 证据。
    return apply_rtl_patch(dict_apply_context)

# 应用 RTL patch 前逐文件备份。
def apply_rtl_patch(
    dict_apply_context: dict[str, Any],
) -> tuple[dict[str, Any], Path | None, Path | None, Path | None]:
    """
    写回 RTL candidate 并生成 post-apply 证据。

    :param dict_apply_context: 包含源文件、候选文件、TB 契约和验证开关的私有上下文。
    :return: 已应用策略、空 intervention、post-apply validation 路径和 equivalence 路径。
    """

    # 从私有上下文恢复原有局部名，便于后续 payload 字段保持不变。
    list_source_paths = dict_apply_context["list_source_paths"]  # 待覆盖写回的 RTL 源文件列表

    # run 目录用于 post-apply validation 和 equivalence 工件。
    path_out_dir = dict_apply_context["path_out_dir"]  # 当前 verify-repair run 目录

    # 分析结果和 TB contract 传给 post-apply validation。
    dict_analysis = dict_apply_context["dict_analysis"]  # RTL 结构分析结果

    # TB contract 提供 post-apply validation 需要的 active testbench。
    dict_tb_contract = dict_apply_context["dict_tb_contract"]  # active TB 和 workspace 审计契约

    # patch candidate 会在写回后回填 backup 和 active 路径。
    dict_patch_candidate = dict_apply_context["dict_patch_candidate"]  # RTL patch 候选工件 payload

    # automation mode 决定 applied policy 的兼容字段。
    str_automation_mode = dict_apply_context["str_automation_mode"]  # 调用方请求的自动化模式

    # readiness 与 external 开关继续透传给验证器。
    str_readiness = dict_apply_context["str_readiness"]  # 写回后验证的 readiness 等级

    # external 开关决定写回后 validation 是否调用仿真后端。
    bool_run_external = dict_apply_context["bool_run_external"]  # 写回后是否调用外部仿真后端

    # decision 为 None 表示 auto_apply 直接写回。
    dict_decision = dict_apply_context["dict_decision"]  # 控制 confirm_before_apply 写回的用户决策

    # candidate_rtl_paths 字段需要恢复为 Path 后才能读取候选文本。
    list_candidate_paths = [  # 按候选顺序绑定源 RTL 与隔离候选 RTL
        Path(str_path)  # run 目录内已经比较过的候选 RTL 文件
        for str_path in dict_patch_candidate.get("candidate_rtl_paths", [])  # patch_candidate.json 中的候选路径
    ]  # 即将覆盖源文件的候选 RTL 路径列表

    # 备份路径列表。
    list_backups: list[str] = []  # 写回前 RTL 备份路径

    # 写回后的活动路径列表。
    list_active_paths: list[str] = []  # 写回后的 RTL 活动路径

    # 逐个源文件写回候选。
    for path_source, path_candidate in zip(list_source_paths, list_candidate_paths):

        # 写回前构造备份路径。
        path_backup = backup_path(path_source)  # 当前 RTL 源文件备份路径

        # 保存原始 RTL。
        shutil.copyfile(path_source, path_backup)

        # 记录备份路径。
        list_backups.append(str(path_backup))

        # 用候选内容覆盖源文件。
        path_source.write_text(path_candidate.read_text(encoding="utf-8"), encoding="utf-8")

        # 记录活动路径。
        list_active_paths.append(str(path_source))

    # 回填 patch_candidate，后续重新写出 JSON。
    dict_patch_candidate["backup_rtl_paths"] = list_backups  # 写回前备份路径回填字段

    # active_rtl_paths 反映写回后的真实源文件位置。
    dict_patch_candidate["active_rtl_paths"] = list_active_paths  # 写回后活动 RTL 路径回填字段

    # post_apply_validation.json 证明写回后的 RTL/TB 仍能通过统一验证入口。
    path_post_apply_validation = write_json(  # 保存覆盖后验证报告，供 host 打开回归证据
        path_out_dir / "post_apply_validation.json",  # 覆盖后验证报告文件位置
        post_apply_validation_payload(  # 复跑 staged RTL/TB 的验证报告正文
            list_source_paths,  # 等价比较要复核的已覆盖 RTL 源文件集合
            dict_analysis,  # validation spec 使用的结构分析
            dict_tb_contract,  # 活动测试平台的分阶段契约
            str_readiness=str_readiness,  # post-apply 验证使用的 readiness 等级
            bool_run_external=bool_run_external,  # 是否执行外部后端
        ),
    )  # 应用后验证报告文件

    # post_apply_equivalence.json 证明被覆盖的源文件仍等同于候选 RTL。
    path_post_apply_equivalence = write_json(  # verification_result 索引使用的 post-apply equivalence 路径
        path_out_dir / "post_apply_equivalence.json",  # 写回后等价报告路径
        post_apply_equivalence_payload(  # 比较写回源文件和候选 RTL 的报告正文
            list_source_paths,  # 已被候选覆盖后的 RTL 源文件集合
            path_out_dir=path_out_dir,  # compare 工件目录根
            dict_patch_candidate=dict_patch_candidate,  # 候选 RTL 路径来源
            str_readiness=str_readiness,  # 等价比较 readiness
        ),
    )  # post-apply equivalence JSON 路径

    # applied policy 需要区分无 decision 的自动写回和确认后写回。
    str_applied_policy = (
        "auto_apply"  # 无人工 decision 的自动写回
        if str_automation_mode == "auto_apply" and dict_decision is None  # auto_apply 且首轮直接应用
        else "confirm_before_apply"  # 由用户确认后执行写回
    )  # 已应用 patch 对外呈现的策略名

    # applied policy 记录实际完成写回后的可审计状态。
    dict_applied_policy = {
        "policy": str_applied_policy,  # 自动写回或确认后写回的策略名
        "applied": True,  # 该分支已经完成源 RTL 覆盖写回
        "confirmation_required": False,  # 写回完成后不再等待确认
        "backup_rtl_paths": list_backups,  # 写回前备份文件路径
        "active_rtl_paths": list_active_paths,  # 写回完成后的源 RTL 路径
        "patch_category": dict_patch_candidate.get("patch_category", "none"),  # 已应用 patch 类别
    }  # 已应用 RTL mutation 策略

    # 返回应用结果和证据路径。
    return dict_applied_policy, None, path_post_apply_validation, path_post_apply_equivalence

# intervention 文件用于把 RTL patch 决策交还给用户。
def write_rtl_intervention(
    path_out_dir: Path,
    dict_patch_candidate: dict[str, Any],
    dict_rtl_patch_plan: dict[str, Any],
) -> Path:
    """
    写出 RTL patch 人工确认请求。

    :param path_out_dir: 当前 verify-repair 运行目录。
    :param dict_patch_candidate: 候选文件、阻断项和类别摘要。
    :param dict_rtl_patch_plan: 根因证据和候选可用性计划。
    :return: 写出的 rtl_intervention.json 路径。
    """

    # observations 包含根因和 blocker。
    list_observations = [
        dict_rtl_patch_plan.get("root_cause_hypothesis", ""),  # patch plan 推断的根因说明
        *[str(item) for item in dict_patch_candidate.get("apply_blockers", [])],  # 自动写回阻断原因
    ]  # 人工确认问题背景

    # intervention payload 让 semi-auto/resume 用户明确作出写回决策。
    dict_payload = {  # rtl_intervention.json 的人工确认请求正文
        "version": 1,  # 人工确认 payload schema 版本
        "action": "ask_human",  # 上层流程需要暂停并询问用户
        "primary_source": "rtl_mutation_confirmation",  # 确认请求来源于 RTL 写回
        "question": "是否应用当前 RTL 修复补丁并进入回归验证？",  # 展示给用户的确认问题
        "observations": list_observations,  # 根因与 blocker 摘要
        "attempted_actions": [  # 方便用户判断候选是否值得应用的已执行动作
            "generated rtl_patch_plan",  # 已生成结构化修复计划
            "generated rtl_patch_diff",  # 已生成人工审查 diff
            "prepared candidate RTL",  # 已准备隔离候选 RTL
        ],
        "expected_answer_format": {  # resume 决策文件需要包含的字段说明
            "decision": "apply_rtl_patch or reject_rtl_patch",  # 是否允许写回 RTL patch
            "evidence": "why this patch should or should not be applied",  # 用户判断依据
            "constraints": "extra constraints to preserve during apply",  # 写回时需要保留的额外约束
        },
    }  # RTL 人工确认 payload

    # 写出固定文件名供 resume 读取。
    return write_json(path_out_dir / "rtl_intervention.json", dict_payload)

# decision 文件由调用方或用户提供。
def read_decision(path_decision: Path) -> dict[str, Any]:
    """
    读取 RTL patch decision JSON。

    :param path_decision: 用户或 resume 流程提供的决策文件路径。
    :return: 解析后的决策 payload。
    """

    # 直接解析 JSON，错误交给调用栈暴露。
    return json.loads(path_decision.read_text(encoding="utf-8"))

# decision 文本中出现明确 apply/approve/confirm 才允许写回。
def decision_allows_apply(dict_decision: dict[str, Any]) -> bool:
    """
    判断 decision payload 是否允许应用 RTL patch。

    :param dict_decision: 用户决策 payload。
    :return: decision 文本明确包含同意关键词时返回 True。
    """

    # 统一小写 decision 文本。
    str_text = str(dict_decision.get("decision") or "").lower()  # 用户决策文本

    # 只接受明确同意关键词。
    return any(str_token in str_text for str_token in ("apply", "approve", "confirm"))

# post-apply validation 重新构造隔离 workspace 验证写回后的 RTL。
def post_apply_validation_payload(
    list_source_paths: list[Path],
    dict_analysis: dict[str, Any],
    dict_tb_contract: dict[str, Any],
    *,
    str_readiness: str,
    bool_run_external: bool,
) -> dict[str, Any]:
    """
    生成 RTL 写回后的 validation 报告 payload。

    :param list_source_paths: 已写回或待复核的 RTL 源文件路径。
    :param dict_analysis: 构造 validation spec 的结构分析结果。
    :param dict_tb_contract: 提供 active testbench 路径的契约 payload。
    :param str_readiness: validate_generated 使用的准备等级。
    :param bool_run_external: 是否启用外部仿真后端。
    :return: validate_generated 产出的报告字典。
    """

    # post-apply workspace 复用 active TB 所在验证树旁路，便于收集证据。
    path_workspace_root = (
        Path(dict_tb_contract["active_testbench_path"]).parent.parent / "post_apply_workspace"  # 写回后验证 workspace 位置
    )  # 写回后隔离验证 workspace 根目录

    # RTL staging 目录保存写回后的源文件副本。
    path_rtl_dir = path_workspace_root / "rtl"  # 写回后 RTL staging 目录

    # TB staging 目录保存 active testbench 的验证副本。
    path_tb_dir = path_workspace_root / "tb"  # validate_generated 读取 active TB 副本的目录

    # 创建 staging 目录。
    path_rtl_dir.mkdir(parents=True, exist_ok=True)

    # testbench 目录必须和 RTL staging 同时存在。
    path_tb_dir.mkdir(parents=True, exist_ok=True)

    # staged source 列表传给统一 validator 重新生成 spec。
    list_staged_sources: list[Path] = []  # validate_generated 将读取的 RTL staging 文件

    # 复制当前 RTL 源文件到 post-apply workspace。
    for path_source in list_source_paths:

        # 目标路径保持文件名。
        path_target = path_rtl_dir / path_source.name  # 当前源 RTL 的 staging 副本路径

        # 复制 RTL。
        shutil.copyfile(path_source, path_target)

        # 记录 staged RTL。
        list_staged_sources.append(path_target)

    # active TB 是 patch 后验证使用的 testbench 基准。
    path_active_tb = Path(dict_tb_contract["active_testbench_path"])  # 写回后验证使用的活动 TB 路径

    # validator 仍使用 `.v` 后缀命名。
    path_staged_tb = path_tb_dir / path_active_tb.with_suffix(".v").name  # validate_generated 读取的 TB staging 路径

    # active TB 复制到 post-apply workspace。
    shutil.copyfile(path_active_tb, path_staged_tb)

    # validation spec 让统一验证器重新读取 post-apply workspace。
    dict_spec = validation_spec(  # post-apply validation_report 的输入规格
        dict_analysis,  # 原分析结构继续作为验证约束来源
        list_staged_sources,  # post-apply workspace 中的 RTL 副本
        f"tb/{path_staged_tb.name}",  # validator 读取的 testbench 相对路径
    )  # 写回后统一验证器输入 spec

    # 运行统一验证器确认写回后 workspace 仍可被同一检查链消费。
    obj_report = validate_generated(  # post_apply_validation_payload 返回的验证报告对象
        dict_spec,  # 写回后验证 spec
        path_workspace_root,  # 隔离验证 workspace
        target="rtl",  # 继续验证 RTL 目标
        run_external=bool_run_external,  # 是否启用外部仿真后端
        readiness=str_readiness,  # 调用方请求的 readiness
        comment_language="zh",  # 既有中文注释检查语言
        strict_generated_comments=False,  # 现有 RTL 诊断不启用生成态逐行注释硬门禁
    )  # 写回后 validation 报告对象

    # 返回 JSON payload。
    return obj_report.to_dict()

# post-apply equivalence 比较源文件和候选 RTL。
def post_apply_equivalence_payload(
    list_source_paths: list[Path],
    *,
    path_out_dir: Path,
    dict_patch_candidate: dict[str, Any],
    str_readiness: str,
) -> dict[str, Any]:
    """
    生成 RTL 写回后的等价检查 payload。

    :param list_source_paths: 写回后的 RTL 源文件路径。
    :param path_out_dir: post-apply equivalence 工件输出目录。
    :param dict_patch_candidate: 提供 candidate_rtl_paths 的候选 payload。
    :param str_readiness: compare_semantics 使用的验证准备等级。
    :return: 等价比较状态和报告路径；无候选时返回 skipped。
    """

    # 没有候选路径时跳过。
    if not dict_patch_candidate.get("candidate_rtl_paths"):

        # 返回 skipped 状态。
        return {"status": "skipped"}

    # compare_semantics 复核写回源 RTL 与候选 RTL 是否保持一致。
    dict_result = compare_semantics(  # 应用后等价报告收录的语义比较证据
        list_source_paths[0],  # 写回后的源 RTL
        Path(dict_patch_candidate["candidate_rtl_paths"][0]),  # 本轮应用的候选 RTL
        out_dir=path_out_dir / "post_apply_equivalence_compare",  # 写回后等价比较目录
        run_external=False,  # post-apply 等价检查保持静态比较
        readiness=str_readiness,  # 沿用调用方 readiness
    )  # post-apply 等价比较结果

    # 返回旧字段名。
    return {
        "status": dict_result["status"],
        "transform_validation_path": dict_result["transform_validation_path"],
        "equivalence_path": dict_result["equivalence_path"],
    }

