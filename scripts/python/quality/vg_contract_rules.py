"""兼容导出参数、资源、生存性与握手合同门禁。"""

# 公开事实与评估模型继续从原模块路径提供兼容导入。
from .vg_semantic_facts import VgFacts
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 参数表达式类型和解析辅助保持原模块的可发现接口。
from .vg_contract_parser import (
    CONSTRAINT_ID_PATTERN,
    IDENTIFIER_PATTERN,
    PARAMETER_CONSTRAINTS_KEY,
    PACKED_LOOKUP_LIMIT_KEY,
    TOKEN_PATTERN,
)

# 解析器类型继续从同一实现模块公开。
from .vg_contract_parser import ContractParser, ExpressionToken

# 解析器递归辅助保持内部兼容导入路径。
from .vg_contract_parser import (
    _collect_identifiers,
    _evaluate_node,
    _literal_value,
    _module_parameter_values,
)

# 参数合同辅助和入口保持原模块的内部兼容接口。
from .vg_contract_parameter import (
    _constraint_records,
    _finding,
    _parameter_contract_findings,
    _source_modules,
    evaluate_parameter_gate,
)

# 资源门禁入口与解析辅助继续从原模块路径导出。
from .vg_contract_resource import _dynamic_selectors, _packed_width, evaluate_resource_gate

# 生存性门禁入口与信号事实辅助继续从原模块路径导出。
from .vg_contract_liveness import (
    _fallback_read_names,
    _identifier_names,
    _module_declarations,
    _module_signal_facts,
    evaluate_liveness_gate,
)

# 握手门禁入口与通道识别辅助继续从原模块路径导出。
from .vg_contract_handshake import (
    _coerce_channel_list,
    _configured_handshake_channels,
    _control_names,
    _handshake_channels,
    evaluate_handshake_gate,
)
