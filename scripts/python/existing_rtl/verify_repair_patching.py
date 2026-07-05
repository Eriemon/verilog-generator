"""verify-repair 的 RTL patch 生成、策略判定和写回辅助函数。"""

# 延迟类型求值，降低 helper 之间的导入耦合。
from __future__ import annotations

# diff、JSON、正则、复制和路径处理支撑 RTL patch 生命周期。
import difflib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 语义比较用于候选 patch 和 post-apply 证据。
from .existing_rtl_improvement import compare_semantics
from scripts.python.validation.validation import validate_generated
from .verify_repair_support import validation_spec
from .verify_repair_testbench import backup_path
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
        str_compare_result_path = _write_candidate_and_compare(  # 候选语义比较报告路径
            list_source_paths=list_source_paths,  # 单文件候选对应的源 RTL
            path_candidate_dir=path_candidate_dir,  # 候选 RTL 写出目录
            path_out_dir=path_out_dir,  # diff 与 compare 工件目录

            # 候选比较会回填 plan 状态和候选索引列表。
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
def _write_candidate_and_compare(
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
        return _rtl_wait_confirmation(list_blockers)

    # 没有候选时根据模式返回报告或确认状态。
    if not bool_has_candidate:

        # 构造无候选策略。
        return _rtl_no_candidate_policy(str_automation_mode, list_blockers)

    # conservative 永远不自动应用 RTL patch。
    if str_automation_mode == "conservative":

        # 有候选时需要人工确认。
        return _rtl_wait_confirmation(list_blockers)

    # semi_auto 始终需要人工确认。
    if str_automation_mode == "semi_auto":

        # 半自动模式不写回源文件。
        return _rtl_wait_confirmation(list_blockers)

    # 任一 blocker 都阻断 auto_apply。
    if list_blockers:

        # 保留 blocker 供人工诊断。
        return _rtl_wait_confirmation(list_blockers)

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
def _rtl_no_candidate_policy(str_automation_mode: str, list_blockers: list[str]) -> dict[str, Any]:
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
        return _rtl_wait_confirmation(list_blockers)

    # auto_apply 无候选时不能应用，只报告。
    return {
        "policy": "report_only",
        "applied": False,
        "confirmation_required": False,
        "apply_blockers": list_blockers,
    }

# 等待人工确认的策略 shape 在多处分支复用。
def _rtl_wait_confirmation(list_blockers: list[str]) -> dict[str, Any]:
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
        return _multi_file_patch_plan(
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
    tuple_patch_selection = _select_patch_candidate(  # 随后拆成补丁类别、候选 RTL 全文、行号提示与根因说明
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
        "risk_level": _patch_risk_level(str_patch_category, bool_candidate_available),
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
def _multi_file_patch_plan(
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

# patch 候选优先级保持原实现顺序。
def _select_patch_candidate(
    *,
    str_source_text: str,
    dict_analysis: dict[str, Any],
    str_reset_name: str,
) -> tuple[str, str | None, list[int], str]:
    """
    按优先级选择 RTL patch 候选。

    :param str_source_text: 待扫描和修补的 RTL 源码全文。
    :param dict_analysis: 端口、状态元素和寄存输出分析结果。
    :param str_reset_name: 分析阶段确认的复位信号名。
    :return: patch 类别、候选文本、行号提示和风险级别。
    """

    # 首选 reset 初始化补全，因为它是低风险 auto_apply 类别。
    tuple_reset_candidate = patch_missing_reset_initialization(  # reset 初始化候选文本与行号
        str_source_text,  # 用于查找 else-if 链和非阻塞赋值的 RTL 全文
        dict_analysis=dict_analysis,  # 提供输出和状态信号宽度
        str_reset_name=str_reset_name,  # 定位 reset 分支的信号名
    )

    # reset 候选文本为空时继续尝试后续模式。
    str_candidate_text = tuple_reset_candidate[0]  # reset 初始化候选 RTL 全文

    # reset 候选行号用于确认插入位置。
    list_patch_lines = tuple_reset_candidate[1]  # reset 初始化新增行号

    # 命中 reset 补全时直接返回。
    if str_candidate_text and list_patch_lines:

        # reset 初始化补全是唯一允许 auto_apply 的低风险候选。
        return (
            "reset_initialization_completion",
            str_candidate_text,
            list_patch_lines,
            "reset branch assigns some staged outputs but misses at least one reset initialization assignment",
        )

    # 其次尝试 case default 补全。
    tuple_case_candidate = patch_case_default_completion(str_source_text)  # case default 候选文本与行号

    # case 候选文本为空时继续尝试 hold 分支。
    str_candidate_text = tuple_case_candidate[0]  # case default 候选 RTL 全文

    # case 候选行号用于人工审查插入位置。
    list_patch_lines = tuple_case_candidate[1]  # case default 新增行号

    # case default 属于控制覆盖面变更。
    if str_candidate_text and list_patch_lines:

        # case default 改变控制覆盖面，必须等待人工确认。
        return (
            "case_default_completion",
            str_candidate_text,
            list_patch_lines,
            "case statement lacks a default branch and can leave control state behavior underspecified",
        )

    # 再尝试 state hold 分支补全。
    tuple_hold_candidate = patch_state_hold_completion(  # else-if 链缺失保持分支时的候选文本与行号
        str_source_text,  # 用于查找 output reg reset-only 模式的 RTL 全文
        dict_analysis=dict_analysis,  # 提供需要 hold 的状态信号集合
    )

    # hold 候选文本为空时继续尝试 output reg。
    str_candidate_text = tuple_hold_candidate[0]  # hold 分支候选生成后的 RTL 全文

    # hold 候选行号标记 else 分支插入区域。
    list_patch_lines = tuple_hold_candidate[1]  # hold 分支插入点的行号提示

    # hold 候选命中后进入中风险确认路径。
    if str_candidate_text and list_patch_lines:

        # hold 分支可能影响时序保持语义，保持人工确认边界。
        return (
            "state_hold_clear_completion",
            str_candidate_text,
            list_patch_lines,
            "clocked conditional updates are missing an explicit hold branch for assigned state or output signals",
        )

    # 最后尝试 output register 补全。
    tuple_output_candidate = patch_output_register_completion(  # output reg 只有 reset 赋值时的 active 分支候选
        str_source_text,  # 待扫描的 RTL 全文
        dict_analysis=dict_analysis,  # 提供 output reg 和 input 映射信息
    )

    # output 候选文本为空时最终返回无候选。
    str_candidate_text = tuple_output_candidate[0]  # output reg active 补全后的 RTL 全文

    # output 候选行号用于定位 active 分支赋值。
    list_patch_lines = tuple_output_candidate[1]  # output reg active 分支新增行号

    # output reg 候选会新增数据通路赋值。
    if str_candidate_text and list_patch_lines:

        # output reg 新增数据通路赋值，继续要求人工确认。
        return (
            "output_register_completion",
            str_candidate_text,
            list_patch_lines,
            "an output register is initialized but never updated in the active branch, "
            "indicating a missing registered datapath assignment",
        )

    # 没有稳定 patch 模式。
    return "none", None, [], "no stable low-risk RTL patch pattern was detected"

# risk level 文本保持旧报告契约。
def _patch_risk_level(str_patch_category: str, bool_candidate_available: bool) -> str:
    """
    返回 patch 类别对应的风险级别。

    :param str_patch_category: 当前候选命中的 RTL patch 类别。
    :param bool_candidate_available: 是否已经生成可比较的候选 RTL。
    :return: low、medium 或 blocked 风险等级。
    """

    # 没有候选即 blocked。
    if not bool_candidate_available:

        # blocked 表示无法自动处理。
        return "blocked"

    # reset 初始化补全是唯一低风险类别。
    if str_patch_category == "reset_initialization_completion":

        # 返回 low 保持 auto_apply 测试预期。
        return "low"

    # 其他候选为 medium，需要确认。
    return "medium"

# reset 初始化补全扫描 reset begin 内缺失的输出/state 赋值。
def patch_missing_reset_initialization(
    str_source_text: str,
    *,
    dict_analysis: dict[str, Any],
    str_reset_name: str,
) -> tuple[str | None, list[int]]:
    """
    补全 reset 分支缺失的初始化赋值。

    :param str_source_text: 待修改的 RTL 源码全文。
    :param dict_analysis: 状态元素和输出端口位宽分析结果。
    :param str_reset_name: reset block 匹配所需的复位信号名。
    :return: 候选 RTL 文本和插入位置行号；无法补全时返回 None 与空列表。
    """

    # 没有 reset 信号无法定位 reset 分支。
    if not str_reset_name:

        # 不产生候选。
        return None, []

    # 信号宽度来自分析结果。
    dict_signal_widths = signal_widths(dict_analysis)  # 需要考虑 reset 初始化的信号宽度

    # 待插入的 reset 赋值。
    list_patch_targets: list[str] = []  # 缺失的 reset 赋值语句

    # 逐个检查输出和状态信号。
    for str_signal, int_width in dict_signal_widths.items():

        # 只有源文件中已有该信号赋值时才尝试补 reset。
        if not re.search(rf"\b{re.escape(str_signal)}\s*<=\s*", str_source_text):

            # 信号未在时序逻辑中出现非阻塞赋值时不推断 reset 缺口。
            continue

        # reset block 文本用于判断哪些 state/output 已有初始化。
        str_reset_block = extract_reset_block(str_source_text, str_reset_name)  # 用于判断既有初始化覆盖面的 reset 分支文本

        # 缺少 reset block 时不能生成候选。
        if str_reset_block is None:

            # reset 结构不稳定时停止该低风险补全模式。
            return None, []

        # 已经有 reset 赋值则跳过。
        if re.search(rf"\b{re.escape(str_signal)}\s*<=\s*", str_reset_block):

            # 既有 reset 赋值已经覆盖该信号，避免重复初始化。
            continue

        # 记录需要插入的 reset 赋值。
        list_patch_targets.append(reset_assignment(str_signal, int_width))

    # 没有缺失赋值时不生成候选。
    if not list_patch_targets:

        # 所有可观察信号都已具备 reset 初始化。
        return None, []

    # 插入 reset 赋值并返回行号。
    return _insert_reset_assignments(
        str_source_text=str_source_text,
        str_reset_name=str_reset_name,
        list_patch_targets=list_patch_targets,
    )

# reset 赋值插到 reset begin 后一行。
def _insert_reset_assignments(
    *,
    str_source_text: str,
    str_reset_name: str,
    list_patch_targets: list[str],
) -> tuple[str | None, list[int]]:
    """
    在 reset begin 后插入初始化赋值。

    :param str_source_text: 原始 RTL 源码全文。
    :param str_reset_name: 复位信号名，用于保留调用方语义上下文。
    :param list_patch_targets: 需要补入 reset block 的初始化赋值语句。
    :return: 插入后的候选 RTL 文本和新增赋值所在行号。
    """

    # 源文本按行处理，保留原缩进。
    list_lines = str_source_text.splitlines()  # RTL 源文本行

    # patched_lines 收集修改后的文本。
    list_patched_lines: list[str] = []  # 插入 reset 初始化后的 RTL 行序列

    # inserted_line_numbers 记录插入行号。
    list_inserted_line_numbers: list[int] = []  # reset 初始化新增赋值所在行号

    # reset begin 文本模式保留复位名转义，避免信号名中的特殊字符影响正则。
    str_regex_reset_pattern: str = rf"if\s*\(\s*!?{re.escape(str_reset_name)}\s*\)\s*begin"  # 复位条件 begin 行的正则文本

    # reset begin 正则用于定位新增初始化语句的插入点。
    pattern_regex_reset_begin: re.Pattern[str] = re.compile(str_regex_reset_pattern)  # 定位 reset 分支起始行的正则匹配器

    # 逐行复制，并在 reset begin 后插入。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先保留原行。
        list_patched_lines.append(str_line)

        # 命中 reset begin 后插入补丁行。
        if pattern_regex_reset_begin.search(str_line):

            # 插入缩进比 reset begin 多一级。
            str_indent = re.match(r"\s*", str_line).group(0) + "    "  # reset 赋值缩进

            # 写入每条 reset 赋值。
            for int_offset, str_assignment in enumerate(list_patch_targets, start=1):

                # 将缺失初始化放在 reset begin 后方。
                list_patched_lines.append(f"{str_indent}{str_assignment}")

                # 记录新增 reset 赋值对应的候选行号。
                list_inserted_line_numbers.append(int_index + int_offset)

    # 只在确实插入时返回候选文本。
    if list_inserted_line_numbers:

        # 候选文本保留源文件末尾换行约定。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # 未命中 reset begin 时说明正则模式无法安全定位插入点。
    return None, []

# case default 补全在 endcase 前插入空 default。
def patch_case_default_completion(str_source_text: str) -> tuple[str | None, list[int]]:
    """
    为缺少 default 的 case 语句生成补全候选。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :return: 已补入空 default 分支的候选文本和插入行号；没有目标时返回 None。
    """

    # 行列表用于在 endcase 前插入 default 且不破坏原缩进。
    list_lines = str_source_text.splitlines()  # case/default 补丁扫描的源行序列

    # patched_lines 保留原始 case 行并夹入 default 分支。
    list_patched_lines: list[str] = []  # 插入 default 分支后的 RTL 行序列

    # 插入行号帮助人工在 diff 中定位新增 default。
    list_inserted_line_numbers: list[int] = []  # default begin/end 两行的候选行号

    # inside_case 追踪当前扫描是否位于一个 case 块内。
    bool_inside_case = False  # 当前扫描位置是否处于 case/endcase 区间

    # has_default 避免给已经覆盖默认分支的 case 重复插入。
    bool_case_has_default = False  # 当前 case 是否已包含 default

    # case indent 用于让新增 default 与原分支对齐。
    str_case_indent = ""  # 新增 default 分支沿用的 case 子语句缩进

    # 遍历每一行查找 case/endcase。
    for int_index, str_line in enumerate(list_lines, start=1):

        # stripped 用于关键字匹配。
        str_stripped = str_line.strip()  # 去缩进后的 RTL 行

        # case 起始重置状态。
        if str_stripped.startswith("case ") or str_stripped.startswith("case("):

            # 进入新的 case 后开始收集 default 状态。
            bool_inside_case = True  # 记录后续行需要寻找 default 或 endcase

            # 新 case 初始视为尚未覆盖 default 分支。
            bool_case_has_default = False  # 当前 case 尚未发现 default 分支

            # default 缩进比 case 多一级。
            str_case_indent = re.match(r"\s*", str_line).group(0) + "    "  # default 分支插入缩进

        # default 已存在时记录。
        if bool_inside_case and str_stripped.startswith("default"):

            # 当前 case 不需要补丁。
            bool_case_has_default = True  # 记录该 case 已覆盖 default 分支

        # endcase 前补 default。
        if bool_inside_case and str_stripped.startswith("endcase") and not bool_case_has_default:

            # 插入 default begin，显式保留未列举状态的空动作。
            list_patched_lines.append(f"{str_case_indent}default: begin")

            # 插入 default end，与 begin 成对保持 Verilog 结构完整。
            list_patched_lines.append(f"{str_case_indent}end")

            # 行号指向插入的 default begin/end，供 patch plan 展示。
            list_inserted_line_numbers.extend([int_index, int_index + 1])  # default begin/end 的候选行号

            # endcase 前完成补齐后退出当前 case 追踪。
            bool_inside_case = False  # endcase 前已完成 default 补全

        # 原 RTL 行必须保留，候选 patch 只做最小插入。
        list_patched_lines.append(str_line)

        # 扫描到 endcase 时关闭当前 case 状态。
        if str_stripped.startswith("endcase"):

            # 离开 case 后避免下一段逻辑误用旧状态。
            bool_inside_case = False  # 扫描离开当前 case/endcase 区间

    # 有插入时返回候选文本。
    if list_inserted_line_numbers:

        # 候选文本保留源 RTL 的末尾换行约定。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # 所有 case 都已有 default，或没有稳定 case/endcase 区间。
    return None, []

# state hold 补全给 clocked else-if 链加显式 hold 分支。
def patch_state_hold_completion(str_source_text: str, *, dict_analysis: dict[str, Any]) -> tuple[str | None, list[int]]:
    """
    为缺少 hold 分支的时序逻辑生成候选。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param dict_analysis: 提供状态寄存器名称的结构分析结果。
    :return: 已插入 hold 分支的候选文本和行号；无法插入时返回 None。
    """

    # 没有 else-if 或已有 else begin 时不处理。
    if "else if" not in str_source_text or re.search(r"else\s+begin", str_source_text):

        # 既无 else-if 链或已有 else 分支时不适合插入 hold。
        return None, []

    # 只处理源中确有非阻塞赋值的状态/输出信号。
    list_stateful_signals = [
        str_name  # 在时序逻辑中出现过赋值的状态或输出信号
        for str_name in signal_widths(dict_analysis)  # 分析得到的可保持信号宽度表
        if re.search(rf"\b{re.escape(str_name)}\s*<=", str_source_text)  # 源码里存在非阻塞赋值
    ]  # 需要 hold 的信号列表

    # 没有可 hold 信号时不生成候选。
    if not list_stateful_signals:

            # 没有可保持的状态信号时跳过该 patch 类别。
            return None, []

    # 插入 hold 分支。
    return _insert_state_hold_branch(str_source_text, list_stateful_signals)

# state hold 分支插入到连续 end 的边界处。
def _insert_state_hold_branch(str_source_text: str, list_stateful_signals: list[str]) -> tuple[str | None, list[int]]:
    """
    插入 state/output hold 分支。

    :param str_source_text: 原始 RTL 源码全文。
    :param list_stateful_signals: 需要在 hold 分支显式自保持的寄存器名称。
    :return: 增加 else begin hold 分支后的候选文本和插入行号。
    """

    # 行扫描用于寻找 else-if 链末尾的连续 end。
    list_lines = str_source_text.splitlines()  # hold 分支插入扫描的源行序列

    # patched_lines 会在插入点前后保留原始 RTL 顺序。
    list_patched_lines: list[str] = []  # hold 分支补丁输出行序列

    # 插入行号记录每条 hold 赋值，便于人工核对。
    list_inserted_line_numbers: list[int] = []  # hold 赋值新增行号

    # 逐行查找连续 end 位置。
    for int_index, str_line in enumerate(list_lines, start=1):

        # stripped line 只用于判断连续 end 和 else-if 链。
        str_stripped = str_line.strip()  # else-if 链尾判定使用的去缩进文本

        # 简单启发式定位 else-if 链结束点。
        bool_insertion_point = (
            str_stripped == "end"  # 当前行是候选链尾 end
            and int_index > 1  # 需要上一行存在
            and list_lines[int_index - 2].strip() == "end"  # 上一行也是 end
            and "else if" in "\n".join(list_lines[: int_index - 1])  # 前文出现 else-if 链
        )  # 是否命中 hold 分支插入点

        # 命中后先插入 else hold。
        if bool_insertion_point:

            # 缩进沿用当前 end。
            str_indent = re.match(r"\s*", str_line).group(0)  # hold 分支外层缩进

            # 子语句缩进多一级。
            str_child_indent = str_indent + "    "  # 自保持赋值使用的内层缩进

            # 插入 else begin。
            list_patched_lines.append(f"{str_indent}else begin")

            # 每个信号保持自赋值。
            for int_offset, str_signal in enumerate(list_stateful_signals, start=1):

                # 插入当前状态信号的保持赋值。
                list_patched_lines.append(f"{str_child_indent}{str_signal} <= {str_signal};")

                # 记录 hold 赋值在候选文件中的行号。
                list_inserted_line_numbers.append(int_index + int_offset)

            # 结束新增 hold 分支，保持 Verilog 块结构完整。
            list_patched_lines.append(f"{str_indent}end")

        # 保留原 RTL 行。
        list_patched_lines.append(str_line)

    # 有插入时返回候选。
    if list_inserted_line_numbers:

        # 有候选时保留源文件末尾换行，避免 diff 产生额外噪声。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # 未定位到链尾时不生成 hold patch。
    return None, []

# output register 补全寻找只 reset 不更新的 output reg。
def patch_output_register_completion(
    str_source_text: str,
    *,
    dict_analysis: dict[str, Any],
) -> tuple[str | None, list[int]]:
    """
    为缺少 active 分支更新的 output reg 生成候选。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param dict_analysis: 输出端口和信号角色分析结果。
    :return: 已补入输出寄存器赋值的候选文本和行号；无缺失项时返回 None。
    """

    # 先识别声明为 output reg 的端口。
    set_reg_outputs = declared_reg_outputs(str_source_text)  # RTL 中声明为 reg 的输出端口

    # 只考虑分析端口中属于 output reg 的项。
    list_outputs = [
        dict_item  # 分析器确认且源码声明为 reg 的 output 端口
        for dict_item in dict_analysis.get("ports", [])  # output reg 候选来自顶层端口分析结果
        if dict_item.get("direction") == "output"  # 只分析 output 方向端口
        and str(dict_item.get("name") or "") in set_reg_outputs  # 只保留源码声明为 reg 的输出
    ]  # 候选 output reg 端口

    # 没有 output reg 时不处理。
    if not list_outputs:

        # 没有 output reg 时该补全类别不适用。
        return None, []

    # reset 名称用于划分 reset 和 active 分支赋值。
    str_reset_name = next(  # 用于区分 reset body 与 active body 的复位端口名
        (
            dict_item["name"]  # reset 角色端口名
            for dict_item in dict_analysis.get("ports", [])  # reset 名称来自顶层端口角色分析
            if dict_item.get("role") == "reset"  # 只选择 reset 角色
        ),
        "",  # 缺少 reset 时关闭 output-reg active 补全
    )  # RTL 复位信号名

    # reset block 和 span 同时用于判断赋值位置。
    str_reset_block = extract_reset_block(str_source_text, str_reset_name) if str_reset_name else None  # reset 分支文本

    # span 用字符位置判断是否在 reset 中。
    tuple_reset_span = extract_reset_block_span(str_source_text, str_reset_name) if str_reset_name else None  # reset 分支字符范围

    # 缺少 reset block 时不能稳定判断。
    if str_reset_block is None or tuple_reset_span is None:

        # reset 结构缺失时无法区分 reset 与 active 赋值。
        return None, []

    # 找出只在 reset 中赋值的 output reg。
    list_missing_outputs = _missing_output_register_assignments(  # 需要补 active 更新的 output reg 赋值语句
        str_source_text=str_source_text,  # 用于查找 output reg 赋值位置的 RTL 全文
        dict_analysis=dict_analysis,  # 推断 active 分支赋值来源的结构分析
        list_outputs=list_outputs,  # 只在 reset 中赋值的候选 output reg 集合
        tuple_reset_span=tuple_reset_span,  # reset body 字符范围
    )  # active 分支缺失的 output reg 赋值

    # 没有缺失输出时不生成候选。
    if not list_missing_outputs:

        # 返回无候选。
        return None, []

    # 插入 active 分支赋值。
    return _insert_output_register_assignments(str_source_text, list_missing_outputs)

# output reg 缺失分析输出具体赋值语句。
def _missing_output_register_assignments(
    *,
    str_source_text: str,
    dict_analysis: dict[str, Any],
    list_outputs: list[dict[str, Any]],
    tuple_reset_span: tuple[int, int],
) -> list[str]:
    """
    识别只在 reset 中赋值的 output reg。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param dict_analysis: 输出端口角色和名称分析结果。
    :param list_outputs: 结构分析得到的 output port 条目。
    :param tuple_reset_span: reset body 在源文本中的字符范围。
    :return: 需要在时序 active 分支补赋值的语句列表。
    """

    # missing outputs 保存需要补入 active 分支的非阻塞赋值。
    list_missing_outputs: list[str] = []  # 需要插入 active 分支的 output reg 赋值

    # 逐个 output reg 检查赋值位置。
    for dict_output in list_outputs:

        # 输出名用于搜索该 output reg 的所有非阻塞赋值。
        str_name = str(dict_output["name"])  # 正在检查 active 分支覆盖面的 output reg 名称

        # 查找所有非阻塞赋值。
        list_assignments = list(re.finditer(rf"\b{re.escape(str_name)}\s*<=", str_source_text))  # 该 output 的赋值位置

        # 是否存在 reset 内赋值。
        bool_has_reset_assignment = any(  # 当前 output reg 是否具备 reset 初始化
            tuple_reset_span[0] <= match_assignment.start() < tuple_reset_span[1]  # 赋值位置落在 reset body 内
            for match_assignment in list_assignments  # reset 范围判断使用的赋值位置集合
        )  # reset 分支是否赋值

        # 是否存在 active 分支赋值。
        bool_has_non_reset_assignment = any(  # 当前 output reg 是否具备 active 更新
            not (tuple_reset_span[0] <= match_assignment.start() < tuple_reset_span[1])  # 赋值起点位于 reset body 之外
            for match_assignment in list_assignments  # 遍历当前 output reg 的非阻塞赋值位置
        )  # 非 reset 区间内是否已有输出更新

        # 没有 reset 赋值时不属于该补全类别。
        if not bool_has_reset_assignment:

            # 跳过不稳定候选。
            continue

        # 已有 active 赋值时不需要补。
        if bool_has_non_reset_assignment:

            # 跳过已覆盖输出。
            continue

        # 推断 active 分支赋值。
        list_missing_outputs.append(inferred_output_assignment(str_name, dict_analysis))

    # 返回缺失赋值列表。
    return list_missing_outputs

# output reg 赋值插到 else begin 后。
def _insert_output_register_assignments(
    str_source_text: str,
    list_missing_outputs: list[str],
) -> tuple[str | None, list[int]]:
    """
    插入 output reg active 分支赋值。

    :param str_source_text: 原始 RTL 源码全文。
    :param list_missing_outputs: 需要插入到 active 分支的赋值语句。
    :return: 插入输出寄存器赋值后的候选文本和行号。
    """

    # 行扫描用于在 active else begin 后插入 output reg 更新。
    list_lines = str_source_text.splitlines()  # 寻找时序 active else begin 的原始 RTL 行序列

    # patched_lines 保留原 RTL 并插入 active 分支赋值。
    list_patched_lines: list[str] = []  # output reg 补丁输出行序列

    # inserted line numbers 对应新增的 output reg 赋值。
    list_inserted_line_numbers: list[int] = []  # active 分支新增赋值行号

    # 记录是否插入。
    bool_inserted = False  # 是否已经插入 output reg 赋值

    # 查找 active else begin。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 原行先写入，后续命中 active 分支再追加赋值。
        list_patched_lines.append(str_line)

        # 去缩进后的控制行用于识别 active else begin 结构。
        str_stripped = str_line.strip()  # active else begin 匹配使用的去缩进文本

        # 命中 active branch 后插入赋值。
        if str_stripped == "end else begin" or str_stripped.endswith("else begin"):

            # 赋值缩进比 else begin 多一级。
            str_indent = re.match(r"\s*", str_line).group(0) + "    "  # active 赋值使用的内层缩进

            # 逐条插入缺失赋值。
            for int_offset, str_assignment in enumerate(list_missing_outputs, start=1):

                # 添加 active 赋值。
                list_patched_lines.append(f"{str_indent}{str_assignment}")

                # 记录新增 output reg 赋值的候选行号。
                list_inserted_line_numbers.append(int_index + int_offset)

            # 已插入后避免重复处理后续 else begin。
            bool_inserted = True  # output reg active 赋值已经插入

    # 有插入则返回候选。
    if bool_inserted:

        # 有候选时保留末尾换行，保持写回文件格式稳定。
        return "\n".join(list_patched_lines) + "\n", list_inserted_line_numbers

    # active 分支定位失败时不生成候选。
    return None, []

# root cause evidence 连接诊断 finding、patch reason 和 checkpoint。
def build_root_cause_evidence(
    dict_diagnosis: dict[str, Any],
    dict_verification_plan: dict[str, Any],
    *,
    str_patch_reason: str,
) -> list[str]:
    """
    生成 patch candidate 的根因证据摘要。

    :param dict_diagnosis: 诊断阶段的 finding 和 outcome。
    :param dict_verification_plan: checkpoint 与 focus signal 计划。
    :param str_patch_reason: 当前 patch 候选命中的根因说明。
    :return: 写入 patch plan 的根因证据文本列表。
    """

    # 诊断 finding 和 patch reason 是最核心证据。
    list_evidence = [str(dict_diagnosis["findings"][0]), str_patch_reason]  # 根因证据文本

    # focus signals 帮助人工定位波形。
    list_focus_signals = [
        str(item_signal)  # 波形排查时展示的关注信号名
        for item_signal in dict_verification_plan.get("focus_signals", [])  # 验证计划中的 focus signal 字段
        if str(item_signal)  # 过滤空信号名
    ]  # 关注信号列表

    # 最多展示四个信号，避免 payload 太大。
    if list_focus_signals:

        # 添加 focus signals 摘要。
        list_evidence.append("focus_signals: " + ", ".join(list_focus_signals[:4]))

    # 最多展示两个 checkpoint。
    for dict_target in dict_verification_plan.get("verification_targets", [])[:2]:

        # checkpoint 证据优先展示行为描述，缺失时退回目标名。
        str_description = str(  # root_cause_evidence 中展示的 checkpoint 文本
            dict_target.get("description") or dict_target.get("name") or ""  # 行为描述优先，目标名兜底
        ).strip()

        # 非空描述才进入证据。
        if str_description:

            # 添加 checkpoint 证据。
            list_evidence.append("checkpoint: " + str_description)

    # 返回证据列表。
    return list_evidence

# reset block 提取是多个 patch 类别的基础。
def extract_reset_block(str_source_text: str, str_reset_name: str) -> str | None:
    """
    提取 reset 分支 body 文本。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param str_reset_name: 用于定位 reset 条件的信号名。
    :return: reset begin/end 块文本；未找到时返回 None。
    """

    # reset block match 决定是否能安全截取 body。
    match_reset = extract_reset_block_match(str_source_text, str_reset_name)  # reset block 正则匹配

    # 缺失 match 时返回 None。
    if not match_reset:

        # 没有稳定 reset block。
        return None

    # 返回命名分组 body。
    return match_reset.group("body")

# reset block match 使用非贪婪正则定位 else 前文本。
def extract_reset_block_match(str_source_text: str, str_reset_name: str) -> re.Match[str] | None:
    """
    返回 reset 分支的正则匹配对象。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param str_reset_name: reset 条件中应出现的信号名。
    :return: 匹配 reset begin/end 文本的正则结果；未找到时返回 None。
    """

    # reset block 模式截取 reset 分支 body，供赋值覆盖分析复用。
    str_reset_block_pattern = (
        rf"if\s*\(\s*!?{re.escape(str_reset_name)}\s*\)\s*begin(?P<body>.*?)end\s+else"  # reset body 到 active else 的捕获模式
    )  # 复位分支 body 提取正则文本

    # reset block 正则用于同时支持 body 文本和字符 span。
    pattern_regex_pattern: re.Pattern[str] = re.compile(  # reset body 提取和 span 定位共用的正则对象
        str_reset_block_pattern,  # 捕获 reset body 到 active else 前
        re.DOTALL,  # 允许 reset body 跨多行
    )  # reset block 正则

    # 正则匹配结果供 reset body 和 span helper 复用。
    return pattern_regex_pattern.search(str_source_text)

# reset span 用于区分 reset/non-reset 赋值。
def extract_reset_block_span(str_source_text: str, str_reset_name: str) -> tuple[int, int] | None:
    """
    返回 reset 分支 body 的字符范围。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :param str_reset_name: reset 条件中应出现的信号名。
    :return: reset block 字符范围；未找到时返回 None。
    """

    # reset span 复用同一匹配，避免 body 和 span 使用不同正则。
    match_reset = extract_reset_block_match(str_source_text, str_reset_name)  # reset span 使用的正则匹配

    # 没有 match 时无法判断 span。
    if not match_reset:

        # 无法定位 reset body 时不做 reset/non-reset 赋值区分。
        return None

    # 返回 body 分组 span。
    return match_reset.span("body")

# signal width 同时覆盖输出端口和 state elements。
def signal_widths(dict_analysis: dict[str, Any]) -> dict[str, int]:
    """
    提取 output/state 信号宽度。

    :param dict_analysis: 包含 ports 和 state_elements 的结构分析结果。
    :return: 信号名到位宽的映射。
    """

    # 收集信号宽度。
    dict_widths: dict[str, int] = {}  # 输出和状态信号宽度表

    # 输出端口优先进入宽度表。
    for dict_item in dict_analysis.get("ports", []):

        # 只处理 output。
        if dict_item.get("direction") == "output":

            # width 缺失时按 1 bit 处理单比特输出。
            dict_widths[str(dict_item["name"])] = int(dict_item.get("width") or 1)  # output 端口位宽

    # state elements 补充进入宽度表。
    for dict_item in dict_analysis.get("state_elements", []):

        # state element 名称用于补充 output 表未覆盖的寄存器宽度。
        str_name = str(dict_item["name"])  # 状态寄存器宽度表键名

        # output 已有记录时不覆盖。
        if str_name not in dict_widths:

            # width 缺失时按 1 bit 处理未显式标注的状态寄存器。
            dict_widths[str_name] = int(dict_item.get("width") or 1)  # 状态信号位宽

    # 返回宽度表。
    return dict_widths

# reset assignment 文本按位宽选择 Verilog literal。
def reset_assignment(str_signal: str, int_width: int) -> str:
    """
    生成 reset 初始化赋值语句。

    :param str_signal: RTL 信号名，shape=scalar，dtype=str，unit=Verilog identifier。
    :param int_width: 信号位宽，shape=scalar，dtype=int，unit=bit。
    :return: 非阻塞 reset 赋值文本，shape=scalar，dtype=str，unit=Verilog statement。
    """

    # 单 bit 使用 1'b0。
    if int_width <= 1:

        # 返回单 bit reset 赋值。
        return f"{str_signal} <= 1'b0;"

    # 多 bit 使用 width'd0。
    return f"{str_signal} <= {int_width}'d0;"

# output reg 声明提取使用 Verilog-2001 常见写法。
def declared_reg_outputs(str_source_text: str) -> set[str]:
    """
    提取声明为 output reg 的端口名。

    :param str_source_text: 待扫描的 RTL 源码全文。
    :return: 在 output reg 声明中出现的信号名集合。
    """

    # 使用 set 推导保持唯一端口名。
    return {
        match_output.group("name")
        for match_output in re.finditer(
            r"output\s+reg(?:\s*\[[^\]]+\])?\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
            str_source_text,
        )
    }

# output active 赋值优先从同名 input 推断。
def inferred_output_assignment(str_signal: str, dict_analysis: dict[str, Any]) -> str:
    """
    推断 output register 的 active 分支赋值。

    :param str_signal: output reg 信号名，shape=scalar，dtype=str，unit=Verilog identifier。
    :param dict_analysis: 结构分析 payload，shape=dict，dtype=dict[str, Any]，unit=analysis fields。
    :return: active 分支非阻塞赋值文本，shape=scalar，dtype=str，unit=Verilog statement。
    """

    # 去掉 o_ 前缀后匹配 i_ 前缀输入。
    str_suffix = str_signal[2:] if str_signal.startswith("o_") else str_signal  # output 对应的数据后缀

    # 查找同名或 i_ 前缀输入。
    for dict_port in dict_analysis.get("ports", []):

        # 输入端口才可作为赋值来源。
        if dict_port.get("direction") != "input":

            # 跳过非输入端口。
            continue

        # 输入端口名称。
        str_port_name = str(dict_port.get("name") or "")  # 候选输入端口名

        # 匹配 i_<suffix> 或 suffix。
        if str_port_name == f"i_{str_suffix}" or str_port_name == str_suffix:

            # 返回 input 到 output 的寄存赋值。
            return f"{str_signal} <= {str_port_name};"

    # 找不到输入时保持自赋值 hold。
    return f"{str_signal} <= {str_signal};"

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
    return _apply_rtl_patch(dict_apply_context)

# 应用 RTL patch 前逐文件备份。
def _apply_rtl_patch(
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
