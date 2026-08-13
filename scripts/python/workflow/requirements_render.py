"""requirements 载荷与规划产物构建辅助逻辑。"""

# 启用前向引用标注，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 导入 requirements 渲染阶段需要的标准库能力。
import copy
from typing import Any

# 导入接口模板解析相关的本地辅助能力。
from .interface_templates import InterfaceTemplateError, select_interface_template

# 导入模板摘要组装时依赖的本地辅助能力。
from .pattern_templates import summarize_pattern_templates
from .use_case_templates import select_use_case_template, summarize_use_case_template

# 导入 requirements 确认阶段沉淀好的开放问题与合同校验能力。
from .requirements_normalize import _requirement_confirmation_issues

# 生成 requirements 阶段结构化载荷，供 staged flow 后续环节复用。
def build_requirements_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """
    生成写入 codegen 前阶段的结构化 requirements 产物。

    :param spec: 当前规格字典。
    :return: 可落盘的 requirements 结构化载荷。
    """

    # 深拷贝用户确认后的 design_requirements，避免后续写回时污染原规格。
    dict_requirements = (
        copy.deepcopy(spec.get("design_requirements", {}))  # 从 spec 深拷贝 design_requirements
        if isinstance(spec.get("design_requirements"), dict)  # 仅在 design_requirements 为字典时保留其内容
        else {}  # 其他类型统一回退为空 requirements 正文
    )  # design_requirements 的隔离副本

    # 汇总接口模板的匹配结果。
    dict_interface_template = _interface_template_summary(spec)  # interface 模板摘要

    # 汇总 use case 模板的匹配结果。
    dict_use_case_template = _use_case_template_summary(spec)  # 给 planning 阶段复用的场景模板快照

    # 汇总 pattern template 的匹配结果，避免后续阶段重复扫描模板。
    list_pattern_templates = summarize_pattern_templates(spec)  # 细化模板摘要列表

    # 只保留 pattern template 的模板标识列表，方便后续报告直接消费。
    list_selected_pattern_template_ids = [  # 供计划产物和报告消费的 pattern template ID 顺序表
        dict_item["template_id"] for dict_item in list_pattern_templates  # 提取每个细化模板的稳定标识
    ]

    # 深拷贝接口 profile，确保产物构建不回写原规格。
    dict_interface_profile = (  # requirements 产物里单独保留的接口 profile 快照
        copy.deepcopy(spec.get("interface_profile", {}))  # 复制顶层接口字段，避免下游产物回写 spec
        if isinstance(spec.get("interface_profile"), dict)  # 只有已有结构化 profile 时才保留这些键值
        else {}  # 缺少结构化 profile 时回退为空对象，等待后续默认补齐
    )

    # 预先生成 requirements 摘要，统一给上层产物复用。
    dict_requirements_summary = _requirements_summary(spec)  # 供审阅摘要和 staged workflow 快速引用的概览

    # 返回 codegen 前阶段需要落盘的 requirements 产物。
    return {
        "version": 1,  # requirements 产物版本号
        "name": spec.get("name"),  # 当前规格的模块名
        "target": spec.get("target"),  # 当前规格的目标类型
        "pipeline_required": bool(spec.get("pipeline_required", True)),  # 是否要求流水线实现
        "streamability": spec.get("streamability"),  # 当前规格的流式处理属性
        "interface_family": spec.get("interface_family"),  # 已确认的接口家族
        "interface_profile": dict_interface_profile,  # 规范化后的接口 profile
        "requirements_summary": dict_requirements_summary,  # 供上层快速消费的要求摘要
        "design_requirements": dict_requirements,  # 用户确认后的设计要求正文
        "confirmed_by_user": bool(dict_requirements.get("confirmed_by_user")),  # 用户是否完成确认
        "selected_interface_template_id": dict_interface_template["selected_template_id"],  # 选中的接口模板 ID
        "interface_template": dict_interface_template,  # 接口模板匹配摘要
        "selected_use_case_template_id": dict_use_case_template["id"],  # 选中的 use case 模板 ID
        "use_case_template": dict_use_case_template,  # use case 模板匹配摘要
        "selected_pattern_template_ids": list_selected_pattern_template_ids,  # 选中的细化模板 ID 列表
        "pattern_templates": list_pattern_templates,  # 给 codegen 直接复用的细化模板建议全集
    }

# 基于 requirements 产物继续构建 codegen plan，供生成阶段和审阅阶段共用。
def build_codegen_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """
    在正式生成代码前构建确定性的结构化 codegen plan。

    :param spec: 当前规格字典。
    :return: 供 staged workflow 复用的 codegen plan 产物。
    """

    # 先复用 requirements 产物，避免不同阶段重复推导基础结论。
    dict_requirements_payload = build_requirements_payload(spec)  # codegen plan 继续复用的 requirements 产物

    # 汇总当前仍待补齐的开放问题。
    list_open_questions = _codegen_open_questions(spec)  # 影响 codegen readiness 的开放问题

    # 只要没有开放问题，就认为 plan 已达到 ready_for_generation。
    bool_ready_for_generation = not list_open_questions  # 只有没有待确认问题时才允许直接进入生成阶段

    # 复用 requirements 阶段已经选中的接口模板摘要。
    dict_interface_template = dict_requirements_payload["interface_template"]  # 接口决策区继续复用的模板选择结果

    # 复用 requirements 阶段已经锁定的场景模板，避免 plan 和 requirements 各自走出不同结论。
    dict_use_case_template = dict_requirements_payload["use_case_template"]  # 场景策略区继续复用的 use case 选择结果

    # 复用 requirements 阶段已经整理好的 pattern template 列表。
    list_pattern_templates = dict_requirements_payload["pattern_templates"]  # 后续提示词和审阅都要复用的细化模板集合

    # 单独保留 pattern template ID 列表，方便计划产物消费。
    list_selected_pattern_template_ids = [  # codegen plan 中单独保留的细化模板 ID 顺序表
        dict_item["template_id"] for dict_item in list_pattern_templates  # 提取给 plan 落盘的模板标识顺序
    ]  # 细化模板 ID 列表

    # 深拷贝接口 profile，避免覆盖原规格。
    dict_interface_profile = (
        copy.deepcopy(spec.get("interface_profile", {}))  # 复制顶层接口字段给 plan 独立消费
        if isinstance(spec.get("interface_profile"), dict)  # 只有结构化 profile 才允许原样带入 plan
        else {}  # 缺少结构化 profile 时给 plan 留空，避免误带非法值
    )  # codegen plan 使用的 interface_profile 快照

    # 先把 design_requirements 规范成字典，避免 plan 组装阶段重复做类型保护。
    dict_design_requirements = (  # 保证 confirmed_by_user 读取时拿到稳定字典
        spec.get("design_requirements")  # 保留规格中原始的 requirements 对象
        if isinstance(spec.get("design_requirements"), dict)  # 只有字典形态才允许继续读取确认字段
        else {}  # 非字典 design_requirements 统一回退为空字典
    )

    # 汇总接口层面的关键决策。
    dict_interface_decision = {  # codegen 需要保留的接口层决策快照
        "family": spec.get("interface_family"),  # 当前 plan 锁定的接口家族
        "profile": dict_interface_profile,  # 供接口提示词直接消费的 profile 快照
        "confirmed": bool(dict_design_requirements.get("confirmed_by_user")),  # requirements 是否已被用户确认
        "selected_interface_template_id": dict_interface_template["selected_template_id"],  # 已选接口模板 ID
        "template_selection_reason": dict_interface_template["selection_reason"],  # 模板选择原因
        "template_path": dict_interface_template["path"],  # 已选模板来源路径
        "port_naming_policy": dict_interface_template["port_naming_policy"],  # 端口命名约束
    }

    # 汇总流水线策略，明确默认行为。
    dict_pipeline_strategy = {  # 把 pipeline_required 映射成生成阶段可消费的策略对象
        "required": bool(spec.get("pipeline_required", True)),  # 是否强制采用流水实现
        "strategy": (  # 流水约束对应的策略标签
            "pipeline_required"  # 规格仍要求流水实现时写入 required 标签
            if spec.get("pipeline_required", True)  # 规格顶层仍要求流水线
            else "pipeline_optional"  # 用户显式放松流水约束时切到 optional 标签
        ),
        "notes": "Use a pipelined implementation unless the user explicitly disables the pipeline requirement.",  # 生成阶段遵循的默认实现说明
    }

    # 汇总模块拆分策略，优先沿用规范化后的子功能计划。
    dict_module_partition = {  # 记录顶层模块与规范化子功能之间的拆分关系
        "top": spec.get("name"),  # 顶层模块名
        "subfunctions": [  # 当前 plan 中保留的子功能名称列表
            dict_item.get("name")  # 单个子功能名称
            for dict_item in spec.get("subfunctions", [])  # 规格里声明的全部子功能条目
            if isinstance(dict_item, dict)  # 只保留结构化子功能条目
        ]
        or [spec.get("name")],  # 子功能缺失时退回单模块实现
        "decomposition_strategy": (  # 代码生成阶段沿用的模块拆分原则
            "follow the normalized subfunction plan and keep interface boundaries explicit"  # 按子功能计划拆分并保留接口边界
        ),
    }

    # 汇总位宽策略，明确参考模型与参数化宽度的保留原则。
    dict_signal_width_strategy = {  # 给 codegen 传递位宽推导和 RTL 风格约束
        "policy": "infer from the semantic model range and preserve parameterized widths where practical",  # 位宽推导策略说明
        "rtl_style_profile": spec.get("rtl_style_profile"),  # 影响位宽写法的 RTL 风格配置
    }

    # 汇总时钟与复位策略，供后续代码生成阶段直接消费。
    dict_reset_clock_strategy = {  # 隔离保存 clock/reset 配置，供后续生成时直接消费
        "clock": (  # 供 codegen 直接读取的时钟配置快照
            copy.deepcopy(spec.get("clock", {}))  # 复制时钟配置，避免后续 stage 就地修改 spec
            if isinstance(spec.get("clock"), dict)  # 只保留结构化时钟配置
            else {}  # 缺少合法时钟对象时回退为空配置
        ),
        "reset": (  # 供 codegen 直接读取的复位配置快照
            copy.deepcopy(spec.get("reset", {}))  # 复制复位配置，避免后续 stage 污染原 spec
            if isinstance(spec.get("reset"), dict)  # 只保留结构化复位配置
            else {}  # 缺少合法复位对象时回退为空配置
        ),
    }

    # 汇总验证策略，保持 staged workflow 的最低验证要求。
    dict_verification_strategy = {  # staged workflow 对验证资产的最低交付要求
        "python_reference_required": True,  # 必须保留 Python semantic 作为行为基线
        "self_checking_testbench_required": True,  # 必须生成自检式 testbench
        "readiness_target": "static",  # 当前 readiness 收敛到静态可验证阶段
        "checkpoint_driven_validation": True,  # 通过 checkpoint 清单驱动后续验证
    }

    # 组装默认 codegen plan 主体。
    dict_plan = {  # codegen 阶段直接消费的完整计划骨架
        "version": 1,  # codegen plan 结构版本号
        "name": spec.get("name"),  # 当前规格对应的顶层模块名
        "target": spec.get("target"),  # 当前工作流面向的目标类型
        "requirements_summary": dict_requirements_payload["requirements_summary"],  # requirements 阶段的压缩摘要
        "selected_use_case_template_id": dict_use_case_template["id"],  # 选中的 use case 模板标识
        "use_case_template": dict_use_case_template,  # use case 模板摘要正文
        "selected_pattern_template_ids": list_selected_pattern_template_ids,  # 命中的细化模板 ID 列表
        "pattern_templates": list_pattern_templates,  # 命中的细化模板摘要集合
        "interface_decision": dict_interface_decision,  # 接口层关键决策快照
        "pipeline_strategy": dict_pipeline_strategy,  # 流水线与时序策略摘要
        "module_partition": dict_module_partition,  # 模块拆分与子功能布局
        "subfunction_dependency_graph": _subfunction_dependency_graph(spec),  # 子功能依赖关系图
        "signal_width_strategy": dict_signal_width_strategy,  # 位宽推导与参数化策略
        "reset_clock_strategy": dict_reset_clock_strategy,  # 时钟与复位策略摘要
        "verification_strategy": dict_verification_strategy,  # 验证资产交付要求
        "critical_behavior_checkpoints": _critical_behavior_checkpoints(spec),  # 行为关键检查点列表
        "semantic_checkpoints": _semantic_checkpoints(spec),  # 语义一致性检查点列表
        "syntax_risk_checks": _syntax_risk_checks(spec),  # 语法风险排查项列表
        "open_questions": list_open_questions,  # 当前仍未闭合的开放问题
        "ready_for_generation": bool_ready_for_generation,  # 是否允许进入代码生成阶段
    }

    # 只在 workflow 已经是结构化对象时读取生成阶段覆写配置。
    dict_workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}  # codegen plan 覆写来源

    # 仅在 workflow 为字典时读取 codegen_plan_override。
    dict_override = (  # workflow 中附带的 codegen_plan_override 显式覆写块
        dict_workflow.get("codegen_plan_override")  # workflow 字典中显式给出的 override
        if dict_workflow  # 只有 workflow 含有效键值时才继续读取 override
        else None  # 其余类型统一视作没有 override
    )

    # 应用显式 override，并补回未覆写的关键字段。
    if isinstance(dict_override, dict):

        # 深拷贝后合并 override，避免调用方共享引用。
        dict_plan.update(copy.deepcopy(dict_override))

        # override 未提供 open_questions 时仍保留默认推导结果。
        if "open_questions" not in dict_override:

            # 维持默认开放问题列表，避免 override 静默清空阻断项。
            dict_plan["open_questions"] = list_open_questions  # 恢复默认推导出的开放问题列表

        # override 未提供 readiness 时，根据 open_questions 自动回推。
        if "ready_for_generation" not in dict_override:

            # 当 override 未声明 readiness 时，沿用默认开放问题推导结果。
            dict_plan["ready_for_generation"] = not dict_plan.get("open_questions")  # 用 open_questions 自动回推 readiness

    # 返回最终 codegen plan。
    return dict_plan

# 汇总跨阶段复用的 requirements 关键摘要，减少上层重复解包 spec。
def _requirements_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """
    生成供 requirements 与 plan 共用的紧凑摘要。

    :param spec: 当前规格字典。
    :return: 关键需求字段的结构化摘要。
    """

    # 提取接口模板选择结论，用于说明接口约束来源。
    dict_interface_template = _interface_template_summary(spec)  # 接口模板选择摘要

    # 提取 use case 模板选择结论，用于说明场景约束来源。
    dict_use_case_template = _use_case_template_summary(spec)  # use case 模板选择摘要

    # 收集 pattern template 命中结果，供摘要和报告复用。
    list_pattern_templates = summarize_pattern_templates(spec)  # 命中的 pattern template 摘要列表

    # 单独保留 pattern template ID 列表，方便摘要消费者快速比较。
    list_selected_pattern_template_ids = [  # 供调用方比对模板选择稳定性的 ID 列表
        dict_item["template_id"] for dict_item in list_pattern_templates  # 提取每个 pattern template 的唯一标识
    ]

    # 摘要阶段只关心确认备注，所以这里把 requirements 缩成最小可读字典。
    dict_design_requirements = (  # 保证 confirmation_notes 读取时始终面对字典
        spec.get("design_requirements")  # 保留规格里原始的 requirements 对象
        if isinstance(spec.get("design_requirements"), dict)  # 只有字典形态才可能携带确认备注
        else {}  # requirements 缺失时按没有确认备注处理
    )

    # 返回供多阶段共用的 requirements 摘要。
    return {
        "target": spec.get("target"),
        "rtl_dialect": spec.get("rtl_dialect"),
        "pipeline_required": bool(spec.get("pipeline_required", True)),
        "streamability": spec.get("streamability"),
        "interface_family": spec.get("interface_family"),
        "selected_interface_template_id": dict_interface_template["selected_template_id"],
        "selected_use_case_template_id": dict_use_case_template["id"],
        "selected_pattern_template_ids": list_selected_pattern_template_ids,
        "confirmation_notes": dict_design_requirements.get("confirmation_notes", ""),
    }

# 汇总 interface template 的匹配结果，给 requirements 和 plan 提供同一份摘要。
def _interface_template_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """
    汇总接口模板的选择结果，供 requirements 与 plan 产物复用。

    :param spec: 当前规格字典。
    :return: 接口模板选择摘要。
    """

    # 先尝试根据当前规格选择匹配的接口模板。
    try:

        # 读取当前规格命中的接口模板对象，后续会把它压缩成稳定摘要。
        dict_selected_template = select_interface_template(spec)  # 已选中的接口模板对象

    # 模板选择失败时，将异常转成结构化摘要返回给上层。
    except InterfaceTemplateError as exc:

        # 返回模板选择失败时的兜底摘要，保证上层仍能看到失败原因和默认策略。
        return {
            "selected_template_id": None,  # 未能选择到接口模板
            "selection_reason": str(exc),  # 选择失败的具体原因
            "path": None,  # 失败时没有模板路径
            "port_naming_policy": "strict_preferred",  # 默认仍偏好严格命名策略
        }

    # 当前接口家族不需要本地标准模板时返回 not_applicable 摘要。
    if not dict_selected_template:

        # 返回不需要标准接口模板时的摘要，保留 not_applicable 命名策略结论。
        return {
            "selected_template_id": None,  # 当前场景没有选中接口模板
            "selection_reason": "no standard local interface template is required for this interface family",  # 不需要模板的原因
            "path": None,  # 没有对应模板路径
            "port_naming_policy": "not_applicable",  # 当前场景不适用命名策略
        }

    # 当前接口家族命中标准模板时，返回模板身份信息与命名约束摘要。
    return {
        "selected_template_id": dict_selected_template["template_id"],  # 命中的标准接口模板标识
        "selection_reason": dict_selected_template["selection_reason"],  # 本次模板命中的归因说明
        "path": str(dict_selected_template["path"]),  # 模板文件路径
        "port_naming_policy": dict_selected_template["strict_naming_policy"],  # 端口严格命名策略
    }

# 汇总 use case 模板的选择结果，供 requirements 产物写回。
def _use_case_template_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """
    汇总 use case 模板的选择结果。

    :param spec: 待分析的规格字典。
    :return: use case 模板选择摘要。
    """

    # 先选择最匹配的 use case 模板。
    dict_selected_template = select_use_case_template(spec)  # 已选择的 use case 模板

    # 返回 use case 模板的结构化摘要。
    return summarize_use_case_template(dict_selected_template)

# 汇总进入代码生成前仍需向用户确认的开放问题。
def _codegen_open_questions(spec: dict[str, Any]) -> list[str]:
    """
    汇总进入代码生成前仍需向用户确认的开放问题。

    :param spec: 待分析的规格字典。
    :return: 进入生成前仍需确认的问题列表。
    """

    # 初始化开放问题列表。
    list_questions: list[str] = []  # 代码生成前的开放问题列表

    # 开放问题阶段只需要 confirmed 状态，因此这里只保留可安全读取的 requirements 字典。
    bool_has_structured_requirements = isinstance(spec.get("design_requirements", {}), dict)  # requirements 是否可直接读取 confirmed 等字段

    # 根据结构化判定结果生成开放问题阶段可读取的 requirements 快照。
    dict_requirements = spec.get("design_requirements", {}) if bool_has_structured_requirements else {}  # 开放问题判定使用的 requirements 快照

    # 读取当前规格声明的接口家族，供后续多个分支复用。
    str_interface_family = str(spec.get("interface_family") or "")  # 当前规格绑定的接口家族名

    # 提前取出 interface_profile，避免后面每个接口分支都重复读取同一份配置。
    obj_interface_profile = spec.get("interface_profile", {})  # 规格里的原始 interface_profile 对象

    # 只在 interface_profile 真正是字典时保留其键值，其余类型统一视作未填写。
    dict_interface_profile = (
        obj_interface_profile if isinstance(obj_interface_profile, dict) else {}  # 可安全读取接口字段的 profile 字典
    )

    # 非 Verilog 的 RTL 目标在生成前需要再次确认。
    if spec.get("target") == "rtl" and spec.get("rtl_dialect") != "verilog":

        # 提醒用户确认目标是否真的是 Verilog-2001。
        list_questions.append("Confirm the design is intended for Verilog-2001.")

    # 用户尚未确认 requirements 时必须补问关键约束。
    if not dict_requirements.get("confirmed_by_user"):

        # requirements 尚未确认时，必须把最关键的目标、流水线和接口选择重新摆到用户面前。
        list_questions.append("Confirm the target, pipeline requirement, and interface choice with the user.")

    # 流式任务在未确认接口家族时必须继续追问。
    if spec.get("streamability") == "streamable" and not str_interface_family:

        # 提醒用户在候选接口家族里做出明确选择。
        list_questions.append(
            "Confirm whether the streamable task should use AXI-Stream, AXI4, AXI4-Lite, "
            "AHB, APB, native, or custom interfaces."
        )

    # 按接口家族补充生成前仍需确认的接口字段问题。
    list_questions.extend(_interface_family_open_questions(str_interface_family, dict_interface_profile))

    # 把 requirements 合同问题补充到开放问题列表中。
    for str_issue in _requirement_confirmation_issues(spec, require_confirmed=False):

        # 仅补充当前列表中尚不存在的问题，避免重复输出。
        if str_issue not in list_questions:

            # 把尚未出现的合同问题追加成开放问题。
            list_questions.append(str_issue)

    # 返回汇总后的开放问题列表。
    return list_questions

# 根据接口家族补充生成前仍需确认的字段问题。
def _interface_family_open_questions(
    str_interface_family: str,
    dict_interface_profile: dict[str, Any],
) -> list[str]:
    """
    根据接口家族补充生成前仍需确认的字段问题。

    :param str_interface_family: 当前规格绑定的接口家族名。
    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :return: 当前接口家族对应的开放问题列表。
    """

    # AXI Stream 场景需要补问 ready/last/data_width 三类关键字段。
    if str_interface_family == "axi_stream":

        # 返回 AXI Stream 场景仍需确认的字段问题。
        return _axi_stream_open_questions(dict_interface_profile)

    # AXI4 场景需要补问关键总线配置字段。
    if str_interface_family == "axi4":

        # 把 AXI4 helper 产出的字段追问原样返回给上层。
        return _axi4_open_questions(dict_interface_profile)

    # AXI4-Lite 场景需要补问角色、模式和位宽字段。
    if str_interface_family == "axi4_lite":

        # 直接返回 AXI4-Lite 模板定稿前缺失的最小配置字段问题。
        return _family_field_open_questions(
            dict_interface_profile,
            ("role", "read_write_mode", "data_width", "addr_width"),
            "Confirm the AXI4-Lite configuration field `{field}`.",
        )

    # AHB/APB 场景需要补问角色和宽度字段。
    if str_interface_family in {"ahb", "apb"}:

        # 直接返回 AHB/APB 模板最依赖的角色与总线宽度缺口问题。
        return _family_field_open_questions(
            dict_interface_profile,
            ("role", "data_width", "addr_width"),
            f"Confirm the {str_interface_family.upper()} configuration field `{{field}}`.",
        )

    # 没有匹配接口家族时返回空问题列表。
    return []

# 汇总 AXI Stream 场景仍需确认的关键字段问题。
def _axi_stream_open_questions(dict_interface_profile: dict[str, Any]) -> list[str]:
    """
    汇总 AXI Stream 场景仍需确认的关键字段问题。

    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :return: AXI Stream 场景的开放问题列表。
    """

    # 返回 AXI Stream 握手与数据位宽仍未确认的追问列表。
    return [
        str_question
        for str_field_name, str_question in (
            ("keep_ready", "Confirm whether AXI-Stream ready handshake should be retained."),
            ("keep_last", "Confirm whether AXI-Stream last should be retained."),
            ("data_width", "Confirm the AXI-Stream data width."),
        )
        if str_field_name not in dict_interface_profile
    ]

# 汇总 AXI4 变体、burst 与地址数据宽度等仍需确认的关键字段问题。
def _axi4_open_questions(dict_interface_profile: dict[str, Any]) -> list[str]:
    """
    汇总 AXI4 场景仍需确认的关键字段问题。

    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :return: AXI4 场景的开放问题列表。
    """

    # 先声明 AXI4 模板定稿前必须确认的基础字段集合。
    tuple_axi4_required_fields = (
        "axi4_variant",  # AXI4 Full/Lite 变体选择
        "role",  # master/slave 角色选择
        "read_write_mode",  # 读写模式选择
        "data_width",  # 数据总线位宽
        "addr_width",  # 地址总线位宽
        "burst_support",  # 是否允许 burst 访问
    )  # AXI4 基础字段集合

    # 提前固定 AXI4 基础字段追问模板，避免后面函数调用行过长。
    str_axi4_question_template = "Confirm the AXI4 configuration field `{field}`."  # AXI4 基础字段追问模板

    # 先收集 AXI4 的基础字段问题。
    list_questions = _family_field_open_questions(  # AXI4 基础字段缺口问题列表
        dict_interface_profile,  # 当前 AXI4 profile 字段快照
        tuple_axi4_required_fields,  # 驱动 role、位宽与 burst 缺口枚举的字段集合
        str_axi4_question_template,  # 把字段名映射成统一英文追问句的模板
    )

    # AXI4 Full 模式下还需要确认 id_width。
    if dict_interface_profile.get("axi4_variant") == "axi4_full" and "id_width" not in dict_interface_profile:

        # 追加 AXI4 Full 变体缺少 id_width 的问题。
        list_questions.append("Confirm the AXI4 full id width.")

    # 开启 burst_support 时还需要确认最大 burst 长度。
    if bool(dict_interface_profile.get("burst_support")) and "max_burst_len" not in dict_interface_profile:

        # 追加 burst 长度上限仍未确认的问题。
        list_questions.append("Confirm the AXI4 maximum burst length.")

    # 返回 AXI4 场景的开放问题列表。
    return list_questions

# 按字段模板汇总接口家族的缺口问题。
def _family_field_open_questions(
    dict_interface_profile: dict[str, Any],
    tuple_required_fields: tuple[str, ...],
    str_question_template: str,
) -> list[str]:
    """
    按字段模板汇总接口家族的缺口问题。

    :param dict_interface_profile: 可安全读取接口字段的 profile 字典。
    :param tuple_required_fields: 当前接口家族要求确认的字段集合。
    :param str_question_template: 使用 `{field}` 占位的追问模板。
    :return: 当前字段集合对应的开放问题列表。
    """

    # 返回缺失字段对应的开放问题列表。
    return [
        str_question_template.format(field=str_field_name)
        for str_field_name in tuple_required_fields
        if str_field_name not in dict_interface_profile
    ]

# 汇总生成阶段必须显式防守的语法和接口风险。
def _syntax_risk_checks(spec: dict[str, Any]) -> list[str]:
    """
    根据当前规格补齐生成前必须守住的语法与接口边界检查项。

    :param spec: 含接口族、模板选择和 RTL 风格约束的规范化规格。
    :return: 供 requirements 阶段写入提示词的风险检查条目列表。
    """

    # 写入所有设计都必须满足的基础风险约束。
    list_checks = [
        (
            "Prevent placeholder text, undefined symbols, and missing output artifacts "
            "before code generation."
        ),
        (
            "Keep the implementation aligned with the executable Python semantic model "
            "and the staged verification flow."
        ),
    ]  # 所有生成任务共享的基础风险项

    # 读取当前规格选择的接口族名称。
    str_interface_family = str(spec.get("interface_family") or "").lower()  # 当前接口族分类键

    # 读取当前规格绑定的用例模板。
    dict_use_case_template = select_use_case_template(spec)  # ADC/DAC 等业务模板

    # 收集已细化的 Verilog 模板提示。
    list_pattern_templates = summarize_pattern_templates(spec)  # 后续需要保留的模板线索

    # 读取当前 RTL 风格配置。
    str_rtl_style_profile = str(spec.get("rtl_style_profile") or "").lower()  # RTL 风格剖面名

    # 在默认流水线场景补充结构性约束。
    if spec.get("pipeline_required", True):

        # 把流水线必须成立的结构约束追加到风险清单。
        list_checks.append(
            "Reject non-pipelined implementations unless the user explicitly disables the pipeline requirement."
        )

    # 根据 AXI-Stream 语义补充 ready/last 和分组约束。
    if str_interface_family == "axi_stream":

        # 把 AXI-Stream 握手和分组规则成组追加到风险清单。
        list_checks.extend(
            [
                (
                    "Do not silently add or remove AXI-Stream ready/last semantics; "
                    "use the confirmed interface profile."
                ),
                (
                    "Preserve Erie-style bus port grouping for AXI-Stream channels "
                    "instead of flattening the interface declaration."
                ),
            ]
        )

    # 根据 AXI4 全功能总线补充角色与通道约束。
    if str_interface_family == "axi4":

        # 把 AXI4 主协议约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve the confirmed AXI4 variant, role, widths, and burst policy "
                    "across the generated interface."
                ),
                (
                    "Preserve Erie-style bus port grouping for AXI4 channels "
                    "instead of flattening the interface declaration."
                ),
            ]
        )

    # 根据 AXI4-Lite 总线补充寄存器访问约束。
    if str_interface_family == "axi4_lite":

        # 把 AXI4-Lite 访问约束和声明约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve the confirmed AXI4-Lite role, read/write mode, and "
                    "register-map widths across the generated interface."
                ),
                (
                    "Preserve Erie-style bus port grouping for AXI4-Lite channels "
                    "instead of flattening the interface declaration."
                ),
            ]
        )

    # 根据 AHB/APB 总线补充角色、位宽和时钟域约束。
    if str_interface_family in {"ahb", "apb"}:

        # 先把接口族标识转换成用于提示词的大写标签。
        str_interface_family_label = str_interface_family.upper()  # 报文里使用的大写接口族名

        # 把 AHB/APB 的接口保持约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve the confirmed "
                    + str_interface_family_label
                    + " role, widths, and clock/reset domain across the generated interface."
                ),
                (
                    "Preserve Erie-style bus port grouping for "
                    + str_interface_family_label
                    + " channels instead of flattening the interface declaration."
                ),
            ]
        )

    # 用例模板存在时要求保留其来源和参数化意图。
    if dict_use_case_template:

        # 把模板来源和参数化意图约束追加到风险清单。
        list_checks.append(
            "Preserve the selected ADC/DAC use-case template family `"
            + str(dict_use_case_template.get("template_id"))
            + "` and keep its provenance, parameterization points, and "
            + "board-level sideband intent visible in generated artifacts."
        )

    # 有细化模板时显式要求保留对应模式提示。
    if list_pattern_templates:

        # 把已选细化模板的提示语追加到风险清单。
        list_checks.append(
            "Preserve the selected improved Verilog pattern hints: "
            + ", ".join(item["template_id"] for item in list_pattern_templates)
            + "."
        )

    # Erie 严格风格需要附加固定的版式和命名约束。
    if str_rtl_style_profile == "erie_strict":

        # 把 Erie 严格风格的固定版式和命名约束成组登记到风险清单。
        list_checks.extend(
            [
                (
                    "Preserve Erie strict RTL style rules, including single-reg always blocks, "
                    "strict naming, and region order."
                ),
                (
                    "Preserve the Erie bilingual header with version, revision date, "
                    "and revision history blocks."
                ),
                (
                    "When an FSM is present, use `state_current`, `state_next`, "
                    "and `ST_*` naming consistently."
                ),
                (
                    "Preserve Erie module instance naming with `_Inst` suffixes "
                    "and `gen_*` generate labels."
                ),
                (
                    "Keep AXI/AXIS/APB/AHB ports grouped by channel and role "
                    "instead of flattening the bus declaration list."
                ),
            ]
        )

    # 返回已经整理好的风险检查条目。
    return list_checks

# 汇总 requirements 阶段必须覆盖的关键行为检查点。
def _critical_behavior_checkpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    根据规格文本和显式断言配置生成关键行为检查点清单。

    :param spec: 可能包含 semantic_checkpoints、reset 和 behavior 信息的规格字典。
    :return: 供 requirements 阶段写入提示词的行为检查点列表。
    """

    # 关键行为检查点分支只关心“有没有显式列表”，因此这里只保留列表形态的用户输入。
    bool_has_explicit_checkpoints = isinstance(spec.get("semantic_checkpoints"), list)  # 是否存在用户显式提供的行为检查点列表

    # 只有结构化列表才允许直接作为行为检查点输入复用。
    list_explicit_checkpoints = spec.get("semantic_checkpoints") if bool_has_explicit_checkpoints else []  # 用户手工提供的行为检查点列表

    # 显式检查点存在时直接复用专门的转换逻辑。
    if list_explicit_checkpoints:

        # 返回按显式配置整理好的行为检查点。
        return _semantic_checkpoints(spec)

    # 先写入所有设计都应具备的复位已知态检查点。
    list_checkpoints: list[dict[str, Any]] = [
        {
            "id": "reset_known_state",  # 默认复位检查点标识
            "category": "reset",  # 该检查点属于复位类约束
            "signals": [str((spec.get("reset") or {}).get("name") or "rst_n")],  # 复位观测信号列表
            "verification_hint": "Check reset-driven initialization before nominal traffic.",  # 复位校验提示语
            "text": "Reset and initial conditions must drive outputs to a known state.",  # 复位必须到达已知态的主描述
        }
    ]  # 默认行为检查点列表

    # 逐项把 behavior 描述转换成 requirements 阶段可消费的检查点。
    for index, item in enumerate(spec.get("behavior", []) or [], start=1):

        # 提取当前行为条目的主描述文本。
        str_behavior_text = item.get("text") if isinstance(item, dict) else str(item)  # 当前行为描述文本

        # 收集当前规格中最值得先观测的前两个输出信号。
        list_observed_outputs = [
            dict_port["name"]  # 当前输出端口名
            for dict_port in spec.get("interfaces", {}).get("ports", [])  # 接口端口候选集合
            if isinstance(dict_port, dict) and dict_port.get("direction") == "output"  # 只保留输出口
        ][:2]  # 最多保留两个代表性输出口

        # 把当前行为描述登记成 requirements 检查点。
        list_checkpoints.append(
            {
                "id": f"behavior_{index}",  # 当前行为检查点标识
                "category": "behavior",  # 该检查点属于功能行为类约束
                "signals": list_observed_outputs,  # 当前行为优先观测的输出信号
                "verification_hint": "Capture this behavior in the Python checkpoint payload and the RTL transcript.",  # 行为校验提示语
                "text": str_behavior_text,  # 把当前 behavior 语句原样写入检查点描述区
            }
        )

    # 返回 requirements 阶段整理好的关键行为检查点列表。
    return list_checkpoints

# 规范化显式或推导得到的语义检查点清单。
def _semantic_checkpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    把显式语义检查点配置和自动补齐的输出观测项统一整理成标准结构。

    :param spec: 可能包含 semantic_checkpoints 和 interfaces.ports 的规格字典。
    :return: 已补齐默认字段和输出观测项的语义检查点列表。
    """

    # 这里先探测 semantic_checkpoints 是否已经是可直接展开的列表契约，再决定是否进入逐项标准化。
    bool_has_explicit_checkpoints = isinstance(spec.get("semantic_checkpoints"), list)  # 当前语义检查点输入是否已满足列表契约

    # 这里的显式列表只服务于语义检查点整理，不和行为检查点路径共用语义。
    list_explicit_checkpoints = spec.get("semantic_checkpoints") if bool_has_explicit_checkpoints else []  # 语义检查点整理使用的显式列表

    # 显式检查点存在时优先逐项整理用户指定内容。
    if list_explicit_checkpoints:

        # 初始化显式检查点整理后的结果列表。
        list_checkpoints: list[dict[str, Any]] = []  # 显式检查点整理结果

        # 逐项规范化用户给出的显式检查点。
        for index, item in enumerate(list_explicit_checkpoints, start=1):

            # 非字典条目按最小行为检查点格式包装。
            if not isinstance(item, dict):

                # 把当前简单条目转换成标准检查点对象。
                list_checkpoints.append(
                    {
                        "id": f"checkpoint_{index}",  # 当前检查点默认标识
                        "category": "behavior",  # 当前检查点默认归类为行为约束
                        "signals": [],  # 简单条目没有显式绑定信号
                        "verification_hint": "",  # 简单条目没有额外提示语
                        "text": str(item),  # 用条目文本直接作为检查点描述
                    }
                )

                # 当前简单条目处理完成后继续看下一个检查点。
                continue

            # 复制当前字典条目，避免直接修改调用方原始数据。
            dict_payload = copy.deepcopy(item)  # 当前显式检查点的独立副本

            # 补齐当前检查点的稳定标识字段。
            dict_payload["id"] = str(dict_payload.get("id") or f"checkpoint_{index}")  # 检查点标识

            # 补齐当前检查点的类别字段。
            dict_payload["category"] = str(dict_payload.get("category") or "behavior")  # 检查点类别

            # 补齐当前检查点的信号列表字段。
            dict_payload["signals"] = list(dict_payload.get("signals") or [])  # 检查点绑定信号列表

            # 补齐当前检查点的校验提示字段。
            dict_payload["verification_hint"] = str(dict_payload.get("verification_hint") or "")  # 检查点校验提示语

            # 补齐当前检查点的主描述文本字段。
            dict_payload["text"] = str(  # 当前检查点对外暴露的主描述文本
                dict_payload.get("text")  # 优先沿用显式给出的主描述
                or dict_payload.get("description")  # 没有 text 时回退到 description
                or dict_payload["id"]  # 再没有文本时退回稳定检查点标识
            )

            # 把规范化后的显式检查点加入结果列表。
            list_checkpoints.append(dict_payload)

    # 没有显式检查点时回退到自动推导逻辑。
    else:

        # 读取按行为和复位规则自动推导出的默认检查点。
        list_checkpoints = _critical_behavior_checkpoints(spec)  # 自动推导的检查点列表

    # 逐个扫描输出端口，确保每个输出至少有一个 observe 检查点。
    for dict_port in spec.get("interfaces", {}).get("ports", []) or []:

        # 过滤掉非输出端口和非字典端口描述。
        if not isinstance(dict_port, dict) or dict_port.get("direction") != "output":

            # 当前端口不需要追加 observe 检查点。
            continue

        # 计算当前输出端口是否已经存在对应的 observe 检查点。
        bool_has_observe_checkpoint = any(  # 当前输出端口是否已经存在 observe 检查点
            dict_item.get("id") == f"observe_{dict_port['name']}"  # 命中的 observe 检查点标识
            for dict_item in list_checkpoints  # 已整理的检查点集合
            if isinstance(dict_item, dict)  # 只检查字典形态的有效检查点
        )

        # 已有 observe 检查点时不再重复追加。
        if bool_has_observe_checkpoint:

            # 当前输出端口已经具备观测项，继续处理下一个端口。
            continue

        # 为当前输出端口追加默认的观测检查点。
        list_checkpoints.append(
            {
                "id": f"observe_{dict_port['name']}",  # 当前输出口的观测检查点标识
                "category": "observable_output",  # 该检查点属于输出观测类约束
                "signals": [dict_port["name"]],  # 当前检查点只绑定一个输出口
                "verification_hint": (
                    f"Keep `{dict_port['name']}` visible in transcript outputs "
                    "and checkpoint payloads."
                ),
                "text": (
                    f"Observe output `{dict_port['name']}` when validating "
                    "behavior and regression drift."
                ),
            }
        )

    # 返回补齐完成后的语义检查点列表。
    return list_checkpoints

# 汇总子功能之间的依赖关系图，供 requirements 和规划阶段复用。
def _subfunction_dependency_graph(spec: dict[str, Any]) -> dict[str, Any]:
    """
    根据规格中的 subfunctions 定义构造轻量依赖图结构。

    :param spec: 可能包含 subfunctions、name 和语义检查点信息的规格字典。
    :return: 含 nodes 与 edges 的子功能依赖图字典。
    """

    # 初始化依赖图中的节点集合。
    list_nodes: list[dict[str, Any]] = []  # 子功能节点列表

    # 初始化依赖图中的边集合。
    list_edges: list[dict[str, Any]] = []  # 子功能依赖边列表

    # 读取规格里声明的子功能条目集合。
    list_subfunctions = (
        list(spec.get("subfunctions", []) or [])  # 复制规格里原始的子功能列表
        if isinstance(spec.get("subfunctions", []) or [], list)  # 只有列表形态才允许继续展开依赖图
        else []  # 其余类型统一回退为空子功能集合
    )

    # 没有子功能时回退到只包含顶层模块的最小图。
    if not list_subfunctions:

        # 返回仅含顶层节点的保底依赖图。
        return {
            "nodes": [
                {
                    "id": str(spec.get("name")),  # 顶层模块节点标识
                    "name": str(spec.get("name")),  # 顶层模块节点名称
                    "test_intent": [],  # 顶层模块默认无子功能测试意图
                }
            ],
            "edges": [],  # 没有子功能时不存在依赖边
        }

    # 逐项把子功能配置转换成依赖图节点和边。
    for dict_subfunction in list_subfunctions:

        # 跳过无名称或非法结构的子功能条目。
        if not isinstance(dict_subfunction, dict) or not dict_subfunction.get("name"):

            # 当前条目无法成为有效子功能节点，继续处理下一个条目。
            continue

        # 提取当前子功能的标准化名称。
        str_subfunction_name = str(dict_subfunction["name"])  # 当前子功能名称

        # 整理当前子功能的测试意图文本列表。
        list_test_intent = [
            dict_item.get("text") if isinstance(dict_item, dict) else str(dict_item)  # 当前测试意图文本
            for dict_item in dict_subfunction.get("test_intent", []) or []  # 当前子功能的测试意图条目
        ]

        # 整理当前子功能绑定的语义检查点标识列表。
        list_semantic_checkpoint_ids = [
            dict_item.get("id")  # 当前语义检查点标识
            for dict_item in dict_subfunction.get("semantic_checkpoints", [])  # 当前子功能的语义检查点条目
            if isinstance(dict_item, dict)  # 只保留字典形态的有效检查点
        ]

        # 把当前子功能登记成依赖图节点。
        list_nodes.append(
            {
                "id": str_subfunction_name,  # 当前子功能节点标识
                "name": str_subfunction_name,  # 当前子功能节点名称
                "test_intent": list_test_intent,  # 供下游规划阶段读取的测试意图快照
                "semantic_checkpoints": list_semantic_checkpoint_ids,  # 当前子功能绑定的检查点标识
            }
        )

        # 逐项展开当前子功能声明的依赖关系。
        for dependency in dict_subfunction.get("dependencies", []) or []:

            # 把当前依赖关系登记成从依赖项指向子功能的有向边。
            list_edges.append(
                {
                    "from": str(dependency),  # 依赖源子功能名称
                    "to": str_subfunction_name,  # 依赖目标子功能名称
                    "kind": "subfunction_dependency",  # 当前依赖边的固定类型
                }
            )

    # 返回已经整理完成的子功能依赖图。
    return {"nodes": list_nodes, "edges": list_edges}
