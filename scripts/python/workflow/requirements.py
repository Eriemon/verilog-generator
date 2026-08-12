"""预检 requirements 确认与代码生成规划辅助逻辑。"""

# 启用前向引用标注，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 重新导出 requirements 推断阶段的共享枚举常量。
from .requirements_extract import (
    AHB_PROFILE_KEYS,
    APB_PROFILE_KEYS,
    AXI4_MODE_LABELS,
    AXI4_MODES,
    AXI4_PROFILE_KEYS,
    AXI4_ROLE_LABELS,
)

# 继续导出 AXI4 标签、接口家族与确认类型。
from .requirements_extract import (
    AXI4_ROLES,
    AXI4_VARIANT_LABELS,
    AXI4_VARIANTS,
    AXI_STREAM_PROFILE_KEYS,
    INTERFACE_FAMILIES,
    RequirementConfirmation,
)

# 补齐 facade 仍需暴露的 profile 键与探测入口。
from .requirements_extract import (
    INTERFACE_TEMPLATE_PROFILE_KEYS,
    NATIVE_FORBIDDEN_PROFILE_KEYS,
    STREAMABILITY_VALUES,
    STREAM_KEYWORDS,
    detect_interface_family,
    detect_streamability,
)

# 重新导出 requirements 规范化阶段入口。
from .requirements_normalize import (
    apply_requirement_defaults,
    require_codegen_plan_enabled,
    validate_codegen_plan_payload,
    validate_requirement_confirmation,
)

# 重新导出 requirements 渲染阶段入口。
from .requirements_render import build_codegen_plan, build_requirements_payload
