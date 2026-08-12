"""读取、校验并渲染统一 Verilog VG 门禁目录。"""

# future annotations 延后解析目录载荷的类型标注。
from __future__ import annotations

# json 读取随技能发布的固定门禁资产。
import json

# lru_cache 避免同一进程重复读取和校验 catalog。
from functools import lru_cache

# pathlib 以模块位置稳定解析资产路径。
from pathlib import Path

# typing 描述 JSON 目录中的动态字段。
from typing import Any

# 原语 loader 保持独立职责，同时从既有 catalog 模块公开导出。
from .primitive_catalog import load_primitive_semantic_catalog

# 资产路径相对技能根解析，安装后无需依赖当前工作目录。
ASSET_PATH = Path(__file__).resolve().parents[3] / "assets" / "verilog_quality_gates.json"  # 统一 VG catalog 路径

# 编号序列保留既有 VG 空洞，并追加 VG150 注释完整性门禁。
EXPECTED_GATE_IDS = (  # 128 个实际发射的统一 VG 编号
    *(f"VG{int_index:03d}" for int_index in range(0, 16)),  # 首段连续编号
    *(f"VG{int_index:03d}" for int_index in range(20, 26)),  # 第二段连续编号
    "VG030",  # 原生检查单点编号一
    "VG031",  # 原生检查单点编号二
    "VG040",  # 中段原生规则起点
    "VG041",  # 中段原生规则次项
    "VG042",  # 中段原生规则末项
    *(f"VG{int_index:03d}" for int_index in range(50, 151)),  # 主体连续编号至 VG150
)

# 目录只允许阻断和警告两种治理等级。
ALLOWED_LEVELS = frozenset({"BLOCKER", "WARNING"})  # 合法 catalog 等级

# v0.7.0 不再允许同版本保留 reserved 占位。
ALLOWED_STATUSES = frozenset({"active"})  # 合法 catalog 状态

# v6 统一目录的固定版本、总数、状态和等级计数。
EXPECTED_CATALOG_COUNTS = (6, 128, 128, 0, 128, 0, 57, 71)  # 统一 catalog 不变量期望元组

# 组合逻辑预算配置键由目录统一拥有，规则实现不得内置业务阈值。
COMB_OPERATION_LIMIT_KEY = "max_combinational_operations_per_target"  # 每目标组合操作上限键

# load_verilog_quality_gates 是目录读取与结构校验的唯一入口。
@lru_cache(maxsize=1)

# 缓存后的函数仍以校验成功作为返回前提。
def load_verilog_quality_gates() -> dict[str, Any]:
    """读取并校验随 skill 打包的统一 Verilog VG 门禁目录。

    参数:
        无。
    返回:
        已通过编号、状态和计数一致性校验的目录字典。
    异常:
        ValueError: 目录缺项、编号漂移或计数不一致。
    """

    # UTF-8 读取保证中文摘要在 prompt 和报告中稳定呈现。
    dict_payload = json.loads(ASSET_PATH.read_text(encoding="utf-8"))  # 原始 catalog JSON 载荷

    # 任一编号、状态或计数漂移都在返回前阻断。
    _validate_catalog(dict_payload)

    # 调用方只会收到已验证的固定目录。
    return dict_payload

# summarize_constraints_for_prompt 将完整目录注入生成与审查提示。
def summarize_constraints_for_prompt(*, max_rules_per_group: int = 5) -> str:
    """渲染包含全部 128 条统一 VG 门禁的稳定提示词摘要。

    参数:
        max_rules_per_group: 兼容旧调用方的保留参数，不裁剪门禁。
    返回:
        按主题列出固定 VG 编号、级别、状态和摘要的多行文本。
    """

    # 保留参数兼容旧调用方，但固定目录禁止裁剪。
    del max_rules_per_group

    # 复用唯一 loader，避免 prompt 旁路 catalog 校验。
    dict_catalog = load_verilog_quality_gates()  # 已验证的统一 VG 目录

    # 列表副本用于按 catalog 顺序分组渲染。
    list_rules = list(dict_catalog["rules"])  # 128 条固定规则记录

    # dict.fromkeys 保留 topic 首次出现顺序。
    list_topic_order = list(  # catalog 中稳定的主题顺序
        dict.fromkeys(  # 去重主题且保留首次出现顺序
            str(dict_rule["topic"])  # 当前规则所属主题
            for dict_rule in list_rules  # 按固定编号顺序遍历规则
        )
    )

    # 头部声明 active、WARNING 和 reserved 的交付语义。
    list_prompt_lines: list[str] = [  # 四行文字向生成模型声明阻断规则、严格警告和目录数量的判定方法
        "Verilog quality gates:",  # 标明后续内容属于统一 VG 门禁摘要
        "Active BLOCKER gates require passed status; any other status blocks delivery.",  # 告知模型阻断级规则采用非通过即失败语义
        "Active WARNING gates also require passed status in strict mode.",  # 告知模型警告级规则在严格交付时必须通过
        (
            f"Coverage: {dict_catalog['total_rules']} fixed gates, "
            f"{dict_catalog['active_rules']} active gates."
        ),  # 从已验证目录动态呈现总数及激活数量
    ]

    # 每个主题聚合为一行，仍保留全部固定编号。
    for str_topic in list_topic_order:

        # 当前主题规则保持原始编号顺序。
        list_topic_rules = [  # 当前主题的固定规则记录
            dict_rule  # 保留完整 catalog 字段
            for dict_rule in list_rules  # 扫描全部固定规则
            if dict_rule["topic"] == str_topic  # 只选择当前主题
        ]

        # 每个片段同时展示编号、等级、状态和摘要。
        list_fragments = [  # 当前主题的 prompt 规则片段
            f"{dict_rule['gate_id']}[{dict_rule['level']}/{dict_rule['status']}]: {dict_rule['summary']}"  # 单条规则摘要
            for dict_rule in list_topic_rules  # 按编号顺序渲染主题规则
        ]

        # 主题行进入最终多行提示文本。
        list_prompt_lines.append(f"- {str_topic}: " + " | ".join(list_fragments))

    # 换行连接便于模型逐主题读取完整目录。
    return "\n".join(list_prompt_lines)

# active_vg_gate_ids 提供当前实际执行编号的稳定顺序。
def active_vg_gate_ids() -> tuple[str, ...]:
    """返回按目录顺序排列的激活 VG 门禁编号。

    参数:
        无。
    返回:
        128 条激活门禁的固定编号元组。
    """

    # 激活编号查询不能绕过 catalog 的结构与计数验证。
    dict_catalog = load_verilog_quality_gates()  # active 过滤所使用的目录载荷

    # 元组保持 catalog 顺序，供测试和报告稳定比较。
    return tuple(
        str(dict_rule["gate_id"])  # 当前激活规则的固定编号
        for dict_rule in dict_catalog["rules"]  # 遍历全部 128 条目录记录
        if dict_rule["status"] == "active"  # 排除 reserved 指导规则
    )

# automated_constraint_ids 保持旧集合型调用合同。
def automated_constraint_ids() -> set[str]:
    """返回兼容旧调用方的激活 VG 门禁编号集合。

    参数:
        无。
    返回:
        当前阶段会执行的固定 VG 编号集合。
    """

    # 集合仅改变容器形状，不引入第二套自动化规则清单。
    return set(active_vg_gate_ids())

# _validate_catalog 集中执行固定编号和阶段计数不变量。
def _validate_catalog(dict_payload: dict[str, Any]) -> None:
    """校验固定 VG 目录的编号、状态、级别与摘要计数。

    参数:
        dict_payload: 从 JSON 资产读取的目录载荷。
    返回:
        无；校验通过即正常返回。
    异常:
        ValueError: 目录结构或固定合同发生漂移。
    """

    # rules 是后续所有固定合同检查的基础列表。
    list_rules = dict_payload.get("rules")  # catalog 的规则记录集合

    # 统一目录必须始终完整包含 128 条实际规则。
    if not isinstance(list_rules, list) or len(list_rules) != 128:

        # 缺项或额外项都会破坏固定编号合同。
        raise ValueError("> ERR: [Python] Verilog VG catalog must contain exactly 128 rules.")

    # 编号列表用于一次性核对连续性、顺序和唯一性。
    list_gate_ids = [  # catalog 实际固定编号顺序
        str(dict_rule.get("gate_id") or "")  # 当前规则编号或空占位
        for dict_rule in list_rules  # 遍历全部 128 条记录
    ]

    # 精确元组比较同时防止重复、跳号和乱序。
    if tuple(list_gate_ids) != EXPECTED_GATE_IDS:

        # 编号漂移会使报告和迁移映射失去稳定主键。
        raise ValueError("> ERR: [Python] Verilog VG catalog ids do not match the emitted rule sequence.")

    # 独立配置校验避免扩大固定目录主校验器的分支复杂度。
    _validate_comb_operation_limit(dict_payload)

    # 每条规则都必须使用受控等级、状态并提供可读摘要。
    for dict_rule in list_rules:

        # 未知等级无法映射 strict 交付语义。
        if dict_rule.get("level") not in ALLOWED_LEVELS:

            # 状态诊断指向需要改回 active 或 reserved 的具体编号。
            raise ValueError(f"> ERR: [Python] RTL VG gate {dict_rule.get('gate_id')} has an invalid level.")

        # 未知状态无法区分执行规则与预留指导。
        if dict_rule.get("status") not in ALLOWED_STATUSES:

            # 诊断包含具体编号，便于修复 catalog。
            raise ValueError(f"> ERR: [Python] RTL VG gate {dict_rule.get('gate_id')} has an invalid status.")

        # 过短摘要不能形成可用 prompt 或报告说明。
        if len(str(dict_rule.get("summary") or "")) < 8:

            # 诊断包含具体编号，便于补全规则语义。
            raise ValueError(f"> ERR: [Python] RTL VG gate {dict_rule.get('gate_id')} has an incomplete summary.")

    # 重算值防止 catalog 顶层声明与逐规则事实不一致。
    tuple_actual_counts = _catalog_counts(dict_payload, list_rules)  # 版本及阶段计数实值

    # 任一声明值或重算值不符都阻断 catalog 加载。
    if tuple_actual_counts != EXPECTED_CATALOG_COUNTS:

        # 单一错误文本保持 CLI 与测试诊断稳定。
        raise ValueError("> ERR: [Python] RTL VG catalog status or level counts are inconsistent.")

# 组合预算校验独立保持类型边界和正整数合同。
def _validate_comb_operation_limit(dict_payload: dict[str, Any]) -> None:
    """校验目录中的每目标组合操作预算。

    参数:
        dict_payload: 待校验的完整 Verilog VG 目录。
    返回:
        校验成功时不返回业务值。
    异常:
        ValueError: 配置根不是对象或预算不是正整数。
    """

    # 组合预算必须来自目录配置对象。
    dict_config = dict_payload.get("config")  # 组合操作门禁与其他目录配置

    # 标量配置根无法提供稳定的命名预算字段。
    if not isinstance(dict_config, dict):

        # 目录载荷类型错误属于启动前配置阻断。
        raise ValueError("> ERR: [Python] Verilog VG catalog config must be an object.")

    # object 注解保留运行时类型校验的必要性。
    obj_operation_limit: object = dict_config.get(COMB_OPERATION_LIMIT_KEY)  # 组合操作预算原始值

    # 布尔值虽然是整数子类，但不能代表组合操作预算。
    bool_valid_operation_limit = (  # 预算值是否满足正整数合同
        isinstance(obj_operation_limit, int)  # 仅接受 Python 整数
        and not isinstance(obj_operation_limit, bool)  # 显式排除 True 和 False
        and obj_operation_limit >= 1  # 至少允许一个真实操作节点
    )

    # 非正整数或其他类型会使强门禁阈值语义失真。
    if not bool_valid_operation_limit:

        # 错误信息保持公开目录合同可检索。
        raise ValueError("> ERR: [Python] Verilog VG combinational operation limit must be a positive integer.")

# _catalog_counts 同时统计顶层声明与逐规则真实数量。
def _catalog_counts(dict_payload: dict[str, Any], list_rules: list[dict[str, Any]]) -> tuple[Any, ...]:
    """构造 catalog 固定版本和阶段计数元组。

    参数:
        dict_payload: 包含顶层声明计数的目录载荷。
        list_rules: 已确认长度为 128 的规则记录。
    返回:
        可与固定 v3 不变量直接比较的八项元组。
    """

    # 元组前四项来自资产声明，后四项从规则记录重新计算。
    return (
        dict_payload.get("version"),  # catalog schema 版本
        dict_payload.get("total_rules"),  # 声明的总规则数
        dict_payload.get("active_rules"),  # 声明的激活规则数
        dict_payload.get("reserved_rules"),  # 声明的预留规则数
        sum(dict_rule["status"] == "active" for dict_rule in list_rules),  # 重算 active 数
        sum(dict_rule["status"] == "reserved" for dict_rule in list_rules),  # 逐规则统计尚未执行的指导占位数量
        sum(  # 重算激活 BLOCKER 数
            dict_rule["status"] == "active" and dict_rule["level"] == "BLOCKER"  # 当前规则是否为激活阻断项
            for dict_rule in list_rules  # 遍历全部固定规则
        ),
        sum(  # 校验 strict 模式消费的警告规则数量
            dict_rule["status"] == "active" and dict_rule["level"] == "WARNING"  # 当前规则是否为激活警告项
            for dict_rule in list_rules  # 从 128 条记录筛选激活警告项
        ),
    )
