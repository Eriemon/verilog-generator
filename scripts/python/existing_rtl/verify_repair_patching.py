"""verify-repair 的 RTL patch facade，保持旧导入面稳定。"""

# 延迟类型求值，避免 facade 在类型解析阶段扩大耦合。
from __future__ import annotations

# patch 写回阶段的稳定公开入口从 apply 子模块转发。
from .verify_repair_patch_apply import (
    RtlMutationContext,
    apply_rtl_patch as _apply_rtl_patch,
    decision_allows_apply,
    handle_rtl_mutation,
)

# post-apply 证据与人工确认入口继续复用 apply 子模块。
from .verify_repair_patch_apply import (
    post_apply_equivalence_payload,
    post_apply_validation_payload,
    read_decision,
    write_rtl_intervention,
)

# patch 类别与候选生成 helper 从 categories 子模块转发。
from .verify_repair_patch_categories import (
    build_root_cause_evidence,
    declared_reg_outputs,
    extract_reset_block,
    extract_reset_block_match,
    extract_reset_block_span,
)

# patch 类别补丁生成 helper 继续从 categories 子模块转发。
from .verify_repair_patch_categories import (
    inferred_output_assignment,
    insert_output_register_assignments as _insert_output_register_assignments,
    insert_reset_assignments as _insert_reset_assignments,
    insert_state_hold_branch as _insert_state_hold_branch,
)

# patch 风险与输出推断 helper 继续从 categories 子模块转发。
from .verify_repair_patch_categories import (
    missing_output_register_assignments as _missing_output_register_assignments,
    patch_case_default_completion,
    patch_missing_reset_initialization,
    patch_output_register_completion,
)

# patch 风险等级和信号推断 helper 继续从 categories 子模块转发。
from .verify_repair_patch_categories import (
    patch_risk_level as _patch_risk_level,
    patch_state_hold_completion,
    reset_assignment,
    select_patch_candidate as _select_patch_candidate,
    signal_widths,
)

# patch 计划与策略 helper 从 plan 子模块转发。
from .verify_repair_patch_plan import (
    build_patch_candidate,
    build_rtl_patch_plan,
    multi_file_patch_plan as _multi_file_patch_plan,
    recommended_action,
    rtl_mutation_policy,
)

# patch 策略补充 helper 继续从 plan 子模块转发。
from .verify_repair_patch_plan import (
    rtl_no_candidate_policy as _rtl_no_candidate_policy,
    rtl_wait_confirmation as _rtl_wait_confirmation,
    source_mutation_policy,
    tb_mutation_policy,
    write_candidate_and_compare as _write_candidate_and_compare,
)

