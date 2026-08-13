"""verify-repair 的 RTL patch 计划、策略与候选工件辅助函数。"""

# 延迟类型求值，降低 helper 之间的导入耦合。
from __future__ import annotations

# diff 和路径处理支撑候选 RTL 工件写出。
import difflib
from pathlib import Path
from typing import Any

# 语义比较用于候选 patch 与源 RTL 的等价检查。
from .existing_rtl_improvement import compare_semantics
from .verify_repair_patch_categories import (
    build_root_cause_evidence,
    patch_risk_level,
    select_patch_candidate,
)

# patch candidate 同时包含候选文件、阻断原因和人工决策上下文。
def build_patch_candidate(
    *,
    # 源 RTL 列表决定是否允许自动生成可应用候选。
    list_source_paths: list[Path],
    # run 目录承载 patch candidate、diff 和 compare 证据。
    path_out_dir: Path,
    # 结构分析结果提供端口、状态寄存器和 reset 信号。
    dict_analysis: dict[str, Any],
    # 诊断结果提供 outcome 和根因 finding。
    dict_diagnosis: dict[str, Any],
    # 验证计划提供 checkpoint 与 focus signal 证据。
    dict_verification_plan: dict[str, Any],
    # 自动化模式决定 report、confirm 或 auto_apply 边界。
    str_automation_mode: str,
    # readiness 原样传给语义比较和验证后端。
    str_readiness: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    生成 RTL patch candidate 和 patch plan。

    :param list_source_paths: 用户提供的 RTL 源文件路径列表。
    :param path_out_dir: verify-repair 本轮运行工件目录。
    :param dict_analysis: 端口、状态寄存器和复位信号的结构分析结果。
    :param dict_diagnosis: verify-repair 诊断出的 outcome 与根因 finding。
    :param dict_verification_plan: checkpoint、关注信号和验证目标计划。
    :param str_automation_mode: conservative、semi_auto 或 auto_apply 自动化策略。
    :param str_readiness: 透传给语义比较和验证后端的准备等级。
    :return: patch candidate payload 与内部 patch plan。
    """

    # 候选 RTL 文件统一放到 run 目录下，避免直接覆盖源文件。
    path_candidate_dir = path_out_dir / "patch_candidate_artifacts" / "rtl"  # 候选 RTL 目录

    # 确保候选目录存在。
    path_candidate_dir.mkdir(parents=True, exist_ok=True)

    # compare_result_path 在有单文件候选时才会产生。
    str_compare_result_path: str | None = None  # 语义比较报告路径

    # staged candidate 同时服务 candidate_artifacts 和 candidate_rtl_paths 两个旧字段。
    list_staged_candidates: list[str] = []  # 已写入 run 目录的候选 RTL 文件路径

    # backup path 只有真正应用 patch 后才会填充。
    list_backup_paths: list[str] = []  # RTL 源文件备份路径列表

    # active path 初始即用户传入源文件路径。
    list_active_paths = [str(path_source) for path_source in list_source_paths]  # 当前活动 RTL 源文件路径

    # apply blockers 汇总自动应用阻断原因。
    list_apply_blockers: list[str] = []  # RTL patch 自动应用阻断原因

    # patch plan 负责识别具体修复类别和候选文本。
    dict_patch_plan = build_rtl_patch_plan(  # 候选生成、风险判定和写回门禁的计划 payload
        list_source_paths=list_source_paths,  # 待分析的 RTL 源文件集合
        dict_analysis=dict_analysis,  # 端口和状态元素分析结果
        dict_diagnosis=dict_diagnosis,  # verify-repair 诊断摘要
        dict_verification_plan=dict_verification_plan,  # checkpoint 与关注信号计划
    )

    # 多文件 patch 需要人工协调。
    if len(list_source_paths) != 1:

        # 多源输入不能自动写回。
        list_apply_blockers.append("multiple_source_files")

    # 没有候选时记录阻断原因。
    if not dict_patch_plan.get("candidate_available"):

        # 自动应用需要真实候选文件。
        list_apply_blockers.append("no_patch_candidate")

    # 单文件候选可生成 diff 和语义比较证据。
    if dict_patch_plan.get("candidate_available") and len(list_source_paths) == 1:

        # 写出候选 RTL 并执行比较。
        str_compare_result_path = write_candidate_and_compare(  # 候选语义比较报告路径
            list_source_paths=list_source_paths,  # 单文件候选对应的源 RTL
            path_candidate_dir=path_candidate_dir,  # 候选 RTL 写出目录
            path_out_dir=path_out_dir,  # diff 与 compare 工件目录

            # compare helper 需要回填候选索引、计划状态与自动应用阻断项。
            dict_patch_plan=dict_patch_plan,  # 已选择的 patch plan
            list_staged_candidates=list_staged_candidates,  # 回填候选路径的列表
            list_apply_blockers=list_apply_blockers,  # 比较失败时追加阻断原因
            str_readiness=str_readiness,  # compare_semantics 使用的验证等级
        )

    # 没有可比较候选时仍写空 diff 文件。
    else:

        # 空 diff 是 release/测试所需的稳定工件。
        (path_out_dir / "rtl_patch_diff.txt").write_text("", encoding="utf-8")

        # 缺少候选时不能声明等价 ready。
        dict_patch_plan["equivalence_ready"] = False  # 无候选时强制关闭等价通过标记

    # 返回 patch candidate payload 和 patch plan。
    return (
        {
            "version": 1,
            "automation_mode": str_automation_mode,
            "diagnosis_outcome": dict_diagnosis["outcome"],
            "candidate_artifacts": list_staged_candidates,
            "candidate_rtl_paths": list_staged_candidates,
            "backup_rtl_paths": list_backup_paths,
            "active_rtl_paths": list_active_paths,
            "compare_result_path": str_compare_result_path,
            "equivalence_ready": bool(dict_patch_plan.get("equivalence_ready")),
            "apply_blockers": list_apply_blockers,
            "patch_category": dict_patch_plan.get("patch_category", "none"),
            "risk_level": dict_patch_plan.get("risk_level", "blocked"),
            "target_line_hints": dict_patch_plan.get("target_line_hints", []),
            "root_cause_evidence": dict_patch_plan.get("root_cause_evidence", []),
            "auto_apply_eligible": bool(dict_patch_plan.get("apply_gate", {}).get("allowed_for_auto_apply")),
            "recommended_action": recommended_action(str_automation_mode, dict_diagnosis["outcome"]),
            "root_cause_hypothesis": dict_diagnosis["findings"][0],
        },
        dict_patch_plan,
    )

# 单文件候选生成完整 diff 和 compare_semantics 证据。
def write_candidate_and_compare(
    *,
    # 只允许单个源文件进入候选写出路径。
    list_source_paths: list[Path],
    # 候选 RTL 文件落在 run 目录隔离区。
    path_candidate_dir: Path,
    # diff 与 compare 工件写入同一个 verify-repair run。
    path_out_dir: Path,
    # patch plan 提供 candidate_text 并接收 compare 状态。
    dict_patch_plan: dict[str, Any],
    # staged candidates 需要回填到 patch_candidate.json。
    list_staged_candidates: list[str],
    # 比较失败会在此列表追加 equivalence blocker。
    list_apply_blockers: list[str],
    # readiness 透传给 compare_semantics。
    str_readiness: str,
) -> str | None:
    """
    写出单文件候选 patch 并运行语义比较。

    :param list_source_paths: 只包含一个待修复 RTL 文件的路径列表。
    :param path_candidate_dir: 隔离候选 RTL 文件写入目录。
    :param path_out_dir: diff、compare 和验证报告所在运行目录。
    :param dict_patch_plan: 包含 candidate_text 并接收比较状态的 patch plan。
    :param list_staged_candidates: 需要回填 candidate 路径的列表。
    :param list_apply_blockers: 语义比较失败时追加阻断原因的列表。
    :param str_readiness: compare_semantics 使用的验证准备等级。
    :return: transform validation 报告路径；比较未生成报告时返回 None。
    """

    # 原始 RTL 路径只允许单文件。
    path_source = list_source_paths[0]  # 待修复 RTL 源文件

    # candidate_text 是即将落到隔离候选目录的完整 RTL。
    str_candidate_text = str(dict_patch_plan["candidate_text"])  # 将写入候选文件的 RTL 全文

    # 候选文件沿用源文件名。
    path_candidate = path_candidate_dir / path_source.name  # 候选 RTL 路径

    # 写出候选 RTL。
    path_candidate.write_text(str_candidate_text, encoding="utf-8")

    # 记录候选路径。
    list_staged_candidates.append(str(path_candidate))

    # unified diff 记录用户源文件和候选文件的逐行差异。
    str_diff_text = "\n".join(  # 候选 RTL 与源 RTL 的 unified diff 文本
        difflib.unified_diff(  # 保持旧 rtl_patch_diff.txt 的 unified diff 格式
            path_source.read_text(encoding="utf-8").splitlines(),  # 原始 RTL 文本行
            str_candidate_text.splitlines(),  # 候选 RTL 文本行
            fromfile=str(path_source),  # diff 左侧文件名
            tofile=str(path_candidate),  # diff 右侧文件名
            lineterm="",  # 保持 difflib 行尾稳定
        )
    )

    # 写出 diff 工件。
    (path_out_dir / "rtl_patch_diff.txt").write_text(
        str_diff_text + ("\n" if str_diff_text else ""),
        encoding="utf-8",
    )

    # 比较原 RTL 和候选 RTL 的语义证据。
    dict_compare_result = compare_semantics(  # 候选 patch 语义比较结果
        path_source,  # 原始 RTL 源文件
        path_candidate,  # run 目录中的候选 RTL 文件
        out_dir=path_out_dir / "patch_candidate_compare",  # 候选比较工件目录
        run_external=False,  # patch 候选阶段只做静态语义比较
        readiness=str_readiness,  # 沿用调用方请求的 readiness
    )

    # 更新 patch plan 中的比较状态。
    dict_patch_plan["compare_status"] = dict_compare_result["status"]  # 候选比较执行状态

    # passed 才能视为等价 ready。
    dict_patch_plan["equivalence_ready"] = dict_compare_result["status"] == "passed"  # 候选是否通过语义比较

    # 比较失败时阻断自动应用。
    if dict_compare_result["status"] != "passed":

        # 记录等价证据不足。
        list_apply_blockers.append("equivalence_not_ready")

    # 返回 transform validation 路径。
    return dict_compare_result["transform_validation_path"]

# source mutation 是 TB/RTL 两类写回的总策略摘要。
def source_mutation_policy(
    dict_tb_mutation: dict[str, Any],
    dict_rtl_mutation: dict[str, Any],
) -> dict[str, Any]:
    """
    合并 testbench 与 RTL mutation 策略。

    :param dict_tb_mutation: testbench 写回策略和实际应用状态。
    :param dict_rtl_mutation: RTL patch 写回策略和确认状态。
    :return: 兼容旧 source_mutation 字段的汇总策略 payload。
    """

    # TB 写回优先代表本轮实际 source mutation。
    str_policy = (
        dict_tb_mutation["policy"]  # TB 已写回时 source_mutation 以 TB 策略为主
        if dict_tb_mutation.get("applied")  # 仅 TB 实际落盘时采用 TB policy
        else dict_rtl_mutation["policy"]  # 否则沿用 RTL patch 策略
    )  # 汇总后暴露给旧 source_mutation 字段的策略名

    # applied 任一为真即说明源码被改写。
    bool_applied = bool(dict_tb_mutation.get("applied")) or bool(dict_rtl_mutation.get("applied"))  # 汇总应用状态

    # confirmation_required 任一为真即需要人工决策。
    bool_confirmation_required = (
        bool(dict_tb_mutation.get("confirmation_required"))  # TB 写回分支是否仍等待用户决策
        or bool(dict_rtl_mutation.get("confirmation_required"))  # RTL patch 分支是否仍等待用户决策
    )  # 汇总人工确认需求

    # 返回旧 schema 兼容的 source mutation payload。
    return {
        "policy": str_policy,
        "applied": bool_applied,
        "confirmation_required": bool_confirmation_required,
    }

# testbench mutation 策略由模式和自动化模式决定。
def tb_mutation_policy(str_automation_mode: str, dict_tb_contract: dict[str, Any]) -> dict[str, Any]:
    """
    生成 testbench mutation 策略摘要。

    :param str_automation_mode: 当前 verify-repair 的自动化策略。
    :param dict_tb_contract: testbench 分阶段契约和活动路径信息。
    :return: 描述 testbench 是否写回、是否等待确认的策略 payload。
    """

    # active path 表示本轮报告应展示的 testbench 文件。
    str_active_path = dict_tb_contract.get("active_testbench_path")  # 用户或生成流程当前使用的 TB 路径

    # backup 只有 auto_apply augment 时存在。
    str_backup_path = dict_tb_contract.get("backup_testbench_path")  # TB 备份路径

    # generate 模式不改写用户 TB。
    if dict_tb_contract.get("tb_mode") != "augment":

        # 标记为 run 目录生成工件。
        return {"policy": "generated_in_run_dir", "applied": False, "confirmation_required": False}

    # conservative 只报告候选。
    if str_automation_mode == "conservative":

        # 不需要确认，因为不会写回。
        return {
            "policy": "report_only",
            "applied": False,
            "confirmation_required": False,
            "active_testbench_path": str_active_path,
        }

    # semi_auto 需要人工确认。
    if str_automation_mode == "semi_auto":

        # 保留候选和备份字段，供上层展示。
        return {
            "policy": "confirm_before_apply",
            "applied": False,
            "confirmation_required": True,
            "active_testbench_path": str_active_path,
            "backup_testbench_path": str_backup_path,
        }

    # auto_apply 已由 TB helper 写回。
    return {
        "policy": "auto_apply",
        "applied": True,
        "confirmation_required": False,
        "active_testbench_path": str_active_path,
        "backup_testbench_path": str_backup_path,
    }

# RTL mutation 策略显式保护多文件、无候选和非低风险类别。
def rtl_mutation_policy(str_automation_mode: str, dict_patch_candidate: dict[str, Any]) -> dict[str, Any]:
    """
    生成 RTL mutation 策略摘要。

    :param str_automation_mode: conservative、semi_auto 或 auto_apply 请求模式。
    :param dict_patch_candidate: 候选 RTL、阻断项和风险类别摘要。
    :return: 描述 RTL patch 是否可写回、是否等待确认的策略 payload。
    """

    # apply blockers 是候选生成阶段发现的自动写回门禁。
    list_blockers = list(dict_patch_candidate.get("apply_blockers", []))  # 阻止 RTL 自动写回的具体门禁原因

    # 有候选 RTL 才可能进入写回。
    bool_has_candidate = bool(dict_patch_candidate.get("candidate_rtl_paths"))  # 是否存在候选 RTL

    # patch category 用于 auto_apply 降级说明。
    str_patch_category = str(dict_patch_candidate.get("patch_category") or "none")  # 自动化策略判定使用的 RTL patch 类别

    # 只有低风险 reset 初始化补全可自动应用。
    bool_auto_apply_eligible = bool(dict_patch_candidate.get("auto_apply_eligible"))  # 是否允许 auto_apply

    # 多源输入必须人工确认。
    if "multiple_source_files" in list_blockers:

        # 多文件补丁可能跨层级联动，不能自动写回。
        return rtl_wait_confirmation(list_blockers)

    # 没有候选时根据模式返回报告或确认状态。
    if not bool_has_candidate:

        # 构造无候选策略。
        return rtl_no_candidate_policy(str_automation_mode, list_blockers)

    # conservative 永远不自动应用 RTL patch。
    if str_automation_mode == "conservative":

        # 有候选时需要人工确认。
        return rtl_wait_confirmation(list_blockers)

    # semi_auto 始终需要人工确认。
    if str_automation_mode == "semi_auto":

        # 半自动模式不写回源文件。
        return rtl_wait_confirmation(list_blockers)

    # 任一 blocker 都阻断 auto_apply。
    if list_blockers:

        # 保留 blocker 供人工诊断。
        return rtl_wait_confirmation(list_blockers)

    # 非低风险 patch 降级到人工确认。
    if not bool_auto_apply_eligible:

        # downgrade_reason 必须说明被降级的类别。
        return {
            "policy": "confirm_before_apply",
            "requested_policy": "auto_apply",
            "applied": False,
            "confirmation_required": True,
            "apply_blockers": [],
            "downgrade_reason": f"patch_category_requires_confirmation:{str_patch_category}",
        }

    # 低风险、无 blocker 的 auto_apply 可以应用。
    return {
        "policy": "auto_apply",
        "applied": True,
        "confirmation_required": False,
        "apply_blockers": [],
        "candidate_artifact_count": len(dict_patch_candidate.get("candidate_artifacts", [])),
    }

# 无候选时的 RTL 策略分支保持原语义。
def rtl_no_candidate_policy(str_automation_mode: str, list_blockers: list[str]) -> dict[str, Any]:
    """
    生成无 RTL patch 候选时的策略。

    :param str_automation_mode: 当前自动化模式。
    :param list_blockers: 阻止生成或应用 RTL patch 的原因列表。
    :return: 无候选场景下的 report-only 或确认策略。
    """

    # conservative 只报告。
    if str_automation_mode == "conservative":

        # 没有候选也不需要人工确认。
        return {
            "policy": "report_only",
            "applied": False,
            "confirmation_required": False,
            "apply_blockers": list_blockers,
        }

    # semi_auto 等待人工确认是否继续。
    if str_automation_mode == "semi_auto":

        # 保持旧策略为 confirm_before_apply。
        return rtl_wait_confirmation(list_blockers)

    # auto_apply 无候选时不能应用，只报告。
    return {
        "policy": "report_only",
        "applied": False,
        "confirmation_required": False,
        "apply_blockers": list_blockers,
    }

# 等待人工确认的策略 shape 在多处分支复用。
def rtl_wait_confirmation(list_blockers: list[str]) -> dict[str, Any]:
    """
    返回 RTL patch 等待人工确认的策略。

    :param list_blockers: 需要展示给用户的写回阻断项。
    :return: confirm_before_apply 策略 payload。
    """

    # 返回统一确认策略 payload。
    return {
        "policy": "confirm_before_apply",
        "applied": False,
        "confirmation_required": True,
        "apply_blockers": list_blockers,
    }

# recommended_action 给 patch candidate 人读建议。
def recommended_action(str_automation_mode: str, str_outcome: str) -> str:
    """
    根据自动化模式和诊断结果生成建议动作。

    :param str_automation_mode: 当前 verify-repair 自动化模式。
    :param str_outcome: 诊断阶段输出的 pass、fail 或 blocked 类结果。
    :return: 面向报告调用方的人读建议动作标识。
    """

    # conservative 只报告发现。
    if str_automation_mode == "conservative":

        # 保守模式只暴露诊断结果，不准备写回动作。
        return "report_findings_only"

    # semi_auto 准备候选并等待确认。
    if str_automation_mode == "semi_auto":

        # 返回旧建议文本。
        return "prepare_candidate_and_wait_for_confirmation"

    # 已经 pass 时不需要 patch。
    if str_outcome == "pass":

        # 返回无需修复。
        return "no_patch_required"

    # auto_apply 在安全时应用。
    return "auto_apply_when_safe"

# patch plan 按固定优先级识别低风险 RTL 修复。
def build_rtl_patch_plan(
    *,
    list_source_paths: list[Path],
    dict_analysis: dict[str, Any],
    dict_diagnosis: dict[str, Any],
    dict_verification_plan: dict[str, Any],
) -> dict[str, Any]:
    """
    生成 RTL patch plan。

    :param list_source_paths: 待分析的 RTL 源文件路径列表。
    :param dict_analysis: 端口、状态元素和复位信号分析结果。
    :param dict_diagnosis: 根因诊断摘要。
    :param dict_verification_plan: 验证 checkpoint 与关注信号计划。
    :return: 描述候选可用性、风险级别和根因证据的 patch plan。
    """

    # 多文件输入只生成阻断计划。
    if len(list_source_paths) != 1:

        # 返回人工协调所需 evidence。
        return multi_file_patch_plan(
            list_source_paths=list_source_paths,  # 多个源文件需要人工协调
            dict_diagnosis=dict_diagnosis,  # 根因摘要进入 plan evidence
            dict_verification_plan=dict_verification_plan,  # checkpoint 摘要进入 plan evidence
        )

    # 单文件 RTL 是唯一可产生候选 patch 的路径。
    path_source = list_source_paths[0]  # 单个待修复 RTL 源文件

    # 读取源文本用于正则 patch。
    str_source_text = path_source.read_text(encoding="utf-8")  # 用于模式扫描和候选生成的 RTL 全文

    # reset 名称来自分析端口角色。
    str_reset_name = next(  # 分析端口中标记为 reset 的信号名
        (
            dict_item["name"]  # 分析器标记为 reset 的端口名
            for dict_item in dict_analysis.get("ports", [])  # 顶层端口分析结果
            if dict_item.get("role") == "reset"  # 只接受 reset 角色
        ),  # 分析端口中的 reset 角色候选
        "",  # 缺少 reset 角色时保持空字符串
    )

    # 依次尝试已支持的 patch 模式。
    tuple_patch_selection = select_patch_candidate(  # 随后拆成补丁类别、候选 RTL 全文、行号提示与根因说明
        str_source_text=str_source_text,  # 待扫描的源 RTL 完整文本
        dict_analysis=dict_analysis,  # 端口和状态元素结构分析产物
        str_reset_name=str_reset_name,  # 复位端口名称
    )

    # 将 tuple 拆分为具名局部量，保持旧 helper 返回值不变。
    str_patch_category = tuple_patch_selection[0]  # 当前命中的 RTL patch 类别

    # 候选文本只在 run 目录内生成和比较。
    str_candidate_text = tuple_patch_selection[1]  # 候选 RTL 全文或空候选

    # 行号提示用于 patch_candidate.json 面向人工审查。
    list_patch_lines = tuple_patch_selection[2]  # patch 新增或影响的 RTL 行号

    # 根因说明进入 root_cause_evidence。
    str_patch_reason = tuple_patch_selection[3]  # patch 模式命中的英文根因短语

    # 候选可用性必须同时有文本和行号。
    bool_candidate_available = bool(str_candidate_text and list_patch_lines)  # 是否生成有效候选

    # 只有 reset 初始化补全仍允许 auto_apply。
    bool_low_risk_auto = str_patch_category == "reset_initialization_completion"  # 是否低风险自动应用类别

    # 没有候选时写入 no_patch_candidate blocker。
    list_blockers = [] if bool_candidate_available else ["no_patch_candidate"]  # apply_gate 中的自动写回阻断项

    # 返回 patch plan，candidate_text 仅在 run 内消费。
    return {
        "version": 1,
        "candidate_available": bool_candidate_available,
        "risk_level": patch_risk_level(str_patch_category, bool_candidate_available),
        "patch_category": str_patch_category if bool_candidate_available else "none",
        "target_source_files": [str(path_source)],
        "target_line_hints": list_patch_lines,
        "root_cause_hypothesis": dict_diagnosis["findings"][0],
        "root_cause_evidence": build_root_cause_evidence(
            dict_diagnosis,
            dict_verification_plan,
            str_patch_reason=str_patch_reason,
        ),
        "expected_interface_stable": True,
        "expected_checkpoint_stable": True,
        "apply_gate": {
            "allowed_for_auto_apply": bool_low_risk_auto and bool_candidate_available,
            "blockers": list_blockers,
        },
        "candidate_text": str_candidate_text or "",
    }

# 多文件 patch plan 显式阻断自动应用。
def multi_file_patch_plan(
    *,
    list_source_paths: list[Path],
    dict_diagnosis: dict[str, Any],
    dict_verification_plan: dict[str, Any],
) -> dict[str, Any]:
    """
    生成多文件输入的阻断型 patch plan。

    :param list_source_paths: 多个 RTL 源文件路径。
    :param dict_diagnosis: 诊断阶段识别的 outcome 和 finding。
    :param dict_verification_plan: 当前验证计划中的 checkpoint 摘要。
    :return: 明确阻断自动应用的 patch plan。
    """

    # 返回 blocked plan，保持旧字段完整。
    return {
        "version": 1,
        "candidate_available": False,
        "risk_level": "blocked",
        "patch_category": "none",
        "target_source_files": [str(path_source) for path_source in list_source_paths],
        "target_line_hints": [],
        "root_cause_hypothesis": dict_diagnosis["findings"][0],
        "root_cause_evidence": build_root_cause_evidence(
            dict_diagnosis,
            dict_verification_plan,
            str_patch_reason="multiple source files require coordinated human review",
        ),
        "expected_interface_stable": True,
        "expected_checkpoint_stable": True,
        "apply_gate": {
            "allowed_for_auto_apply": False,
            "blockers": ["multiple_source_files"],
        },
        "candidate_text": "",
    }

