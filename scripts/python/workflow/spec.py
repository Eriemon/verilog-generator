"""Verilog 生成规格的脚手架、归一化和安全性校验。"""

# 未来注解避免运行期解析复杂类型，保持 Python 3.10+ 兼容。
from __future__ import annotations

# 标准库：复制用户规格、读写 JSON，并校验相对路径。
import copy
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# 当前运行时只开放 RTL 规格，保留元组便于后续兼容检查复用。
TARGETS = ("rtl",)  # 支持的生成目标集合

# RTL 方言固定为 Verilog，其他方言在验证阶段明确拒绝。
RTL_DIALECTS = ("verilog",)  # 支持的 RTL 语言方言集合

# 顶层规格允许从用户 JSON 透传的字段白名单。
SPEC_FIELDS = (
    "name",  # 规格名称，最终会归一化为安全模块名
    "target",  # 生成目标，当前必须为 rtl
    "rtl_dialect",  # RTL 方言，当前必须为 verilog
    "rtl_style_profile",  # RTL 风格配置，当前支持 erie_strict 或空值
    "design_requirements",  # 设计约束的结构化补充信息
    "streamability",  # 是否支持流式处理的业务标记
    "interface_family",  # 接口族，例如 native 或 AXI
    "interface_profile",  # 接口族的细化配置
    "pipeline_required",  # 是否要求流水化设计计划
    "codegen_plan_required",  # 是否要求生成代码计划文件
    "codegen_plan_path",  # 外部代码计划文件相对路径
    "description",  # 人类可读设计说明
    "interfaces",  # 端口和接口描述
    "behavior",  # 行为需求条目
    "clock",  # 时钟描述
    "reset",  # 复位描述
    "constraints",  # 综合和编码约束
    "outputs",  # 期望输出文件列表
    "file_role_confirmations",  # 文件相对路径到 design/testbench 角色的确认映射
    "notes",  # 额外设计备注
    "semantic_checkpoints",  # 语义检查点列表
    "subfunctions",  # 子功能拆分描述
    "workflow",  # 工作流模板和流程选项
    "performance",  # 性能目标和资源约束
)

# 子功能规格允许保留的字段白名单。
SUBFUNCTION_FIELDS = (
    "name",  # 子功能名称
    "inputs",  # 子功能输入说明
    "outputs",  # 子功能输出说明
    "behavior",  # 子功能行为条目
    "constraints",  # 子功能局部约束
    "dependencies",  # 依赖的其他子功能名称
    "source_references",  # 外部参考材料路径或标识
    "test_intent",  # 子功能测试意图
    "semantic_checkpoints",  # 子功能语义检查点
)

# 这些字段内部统一转换成带 id/evidence 的信息字典。
INFO_DICTIONARY_FIELDS = ("behavior", "constraints", "test_intent")  # 信息条目字段集合

# 规格校验失败时抛出的统一异常类型。
class SpecError(ValueError):
    """表示输入规格不满足当前 RTL 生成器的结构或取值约束。"""

# 模块名清洗需要在 CLI、脚手架和归一化路径共用。
def sanitize_name(name: str) -> str:
    """
    将任意用户名称转换为安全的 Verilog 模块名候选。

    :param name: 用户提供的规格名或模块名原文。
    :return: 仅包含单词字符和下划线、且不以数字开头的名称。
    """

    # 把非单词字符折叠成下划线，避免生成非法文件名或模块名。
    str_cleaned = re.sub(r"\W+", "_", name.strip())  # 初步清洗后的名称

    # 移除首尾分隔符，保留中间下划线表达用户原有分词。
    str_cleaned = str_cleaned.strip("_")  # 去除边缘下划线后的名称

    # 空名称会导致输出路径和模块名不可用，因此使用稳定默认名兜底。
    if not str_cleaned:

        # 返回固定默认名，确保调用方始终拿到可用标识符。
        return "generated_design"

    # Verilog 标识符不能以数字开头，这里用设计前缀保持可读。
    if str_cleaned[0].isdigit():

        # 添加前缀后仍保留原始数字信息，方便用户追踪来源。
        str_cleaned = f"design_{str_cleaned}"  # 补齐合法前缀后的名称

    # 返回可用于规格、文件名和模块名的稳定名称。
    return str_cleaned

# RTL 默认规格集中维护，避免脚手架和缺省输出分支出现漂移。
def _rtl_defaults(name: str) -> dict[str, Any]:
    """
    构造当前 RTL 目标的完整默认规格。

    :param name: 已清洗的规格名，用于默认模块名和输出路径。
    :return: 包含端口、约束、输出和 workflow 占位字段的规格字典。
    """

    # 默认端口采用最小 valid/data 握手，便于生成器在缺规格时仍能产出示例。
    dict_defaults = {
        "name": name,  # 规格名和默认模块名
        "target": "rtl",  # 当前生成器支持的唯一目标
        "rtl_dialect": "verilog",  # 默认 RTL 语言方言
        "rtl_style_profile": "erie_strict",  # 默认启用本 skill 的严格 RTL 风格
        "design_requirements": {},  # 用户可追加的结构化需求
        "streamability": "unknown",  # 未确认时不假设流式能力
        "interface_family": None,  # 未确认时不绑定具体总线协议
        "interface_profile": {},  # 接口族的补充配置
        "pipeline_required": True,  # 默认要求计划里说明流水设计
        "codegen_plan_required": True,  # 默认要求代码生成计划
        "codegen_plan_path": None,  # 未指定外部计划文件
        "description": "Implement a synthesizable Verilog-2001 RTL module.",  # 默认设计说明
        "interfaces": {  # 默认接口对象
            "ports": [  # 默认端口列表
                {"name": "i_clk", "direction": "input", "width": 1, "role": "clock"},  # 时钟端口
                {"name": "i_rstn", "direction": "input", "width": 1, "role": "reset"},  # 复位端口
                {"name": "i_valid", "direction": "input", "width": 1},  # 输入有效信号
                {"name": "i_data", "direction": "input", "width": 8},  # 输入数据总线
                {"name": "o_valid", "direction": "output", "width": 1},  # 输出有效信号
                {"name": "o_data", "direction": "output", "width": 8},  # 输出数据总线
            ],
        },  # 默认端口集合
        "behavior": [  # 默认行为说明列表
            "Describe cycle-by-cycle behavior, latency, handshakes, and corner cases here.",  # 默认行为说明文本
        ],  # 默认行为说明占位文本
        "clock": {"name": "i_clk", "edge": "posedge", "frequency_mhz": 100},  # 默认时钟约束
        "reset": {"name": "i_rstn", "active": "low", "synchronous": False},  # 默认低有效异步复位
        "constraints": [  # 默认 RTL 编码约束列表
            "Use synthesizable Verilog-2001.",  # 综合语言约束
            (
                "Use low-active reset with `negedge i_rstn` and explicit reset behavior "  # 低有效复位约束前半句
                "unless the confirmed spec overrides it."  # 允许确认规格覆盖默认复位策略
            ),  # 默认复位约束
            "Avoid delays, system tasks, force/release, dynamic constructs, and multiple drivers.",  # 禁止仿真专用结构
        ],  # 默认约束会在缺省输入路径中整体复用
        "outputs": [  # 默认输出文件对象列表
            {"path": f"rtl/{name}.v", "kind": "source", "language": "verilog"},  # RTL 源文件路径
            {"path": f"tb/tb_{name}.v", "kind": "testbench", "language": "verilog"},  # 测试平台路径
        ],  # 默认输出文件列表
        "file_role_confirmations": {},  # 尚未确认任何普通命名文件角色
        "notes": [],  # 用户备注列表
        "semantic_checkpoints": [],  # 顶层语义检查点
        "subfunctions": [],  # 子功能列表
        "workflow": {},  # 工作流扩展配置
        "performance": {},  # 性能目标扩展配置
    }

    # 返回新的字典对象，调用方可继续深拷贝或局部覆盖。
    return dict_defaults

# 外部调用使用该函数创建最小可编辑规格。
def scaffold_spec(target: str = "rtl", name: str | None = None) -> dict[str, Any]:
    """
    创建当前生成器支持的默认 RTL 规格。

    :param target: 请求的生成目标；当前仅接受 ``rtl``。
    :param name: 可选模块名；为空时使用 ``rtl_module``。
    :return: 包含默认端口、约束和输出路径的规格字典。
    :raises SpecError: 当目标不是 ``rtl`` 时抛出。
    """

    # 先校验目标，防止上层误以为其他 target 已被脚手架支持。
    _require_target(target)

    # 规格名在进入默认模板前完成清洗，避免默认输出路径含非法字符。
    str_spec_name = sanitize_name(name or "rtl_module")  # 默认规格名

    # 返回调用方可直接序列化或继续覆盖的 RTL 默认规格。
    return _rtl_defaults(str_spec_name)

# 用户 JSON 入口集中在这里归一化，其他模块只消费稳定 shape。
def normalize_spec(raw: dict[str, Any], target: str | None = None) -> dict[str, Any]:
    """
    将用户输入规格归一化为生成器内部稳定结构。

    :param raw: 从 JSON 或调用方传入的原始规格对象。
    :param target: 可选外部目标覆盖值；为空时读取规格自身 target。
    :return: 已补齐默认字段并完成结构校验的规格字典。
    :raises SpecError: 当输入不是对象、目标冲突或字段结构非法时抛出。
    """

    # 顶层规格必须是 JSON object，避免列表或标量进入后续字段访问。
    if not isinstance(raw, dict):

        # 顶层非对象无法安全读取字段，因此直接报告规格根节点类型错误。
        raise SpecError("> ERR: [Python] Spec must be a JSON object.")

    # 外部 target 覆盖优先，其次使用原始规格字段，最后回落到 rtl。
    str_requested_target = _require_target(str(target or raw.get("target") or "rtl"))  # 归一化目标

    # 保留用户原始 target 字段用于冲突检测。
    raw_target_value = raw.get("target")  # 原始规格中的目标值

    # 明确拒绝 target 覆盖与规格内 target 不一致的输入。
    if raw_target_value and str(raw_target_value).lower() != str_requested_target:

        # 覆盖参数和规格内目标冲突时，按单目标生成器约束拒绝输入。
        raise SpecError("> ERR: [Python] Spec target must be 'rtl'.")

    # 规格名缺失时借用脚手架默认值，再统一做安全名称清洗。
    str_name = sanitize_name(str(raw.get("name") or scaffold_spec("rtl")["name"]))  # 安全规格名

    # 先构建完整默认规格，再只拷贝白名单字段，避免未知字段污染内部结构。
    dict_spec = scaffold_spec("rtl", name=str_name)  # 待归一化的完整规格

    # 仅允许已声明字段覆盖默认规格，保留扩展字段的显式治理边界。
    for str_key, spec_value in raw.items():

        # 白名单字段通过深拷贝进入内部规格，避免后续修改影响调用方对象。
        if str_key in SPEC_FIELDS:

            # 深拷贝用户字段，保证归一化过程不会反向修改原始输入。
            dict_spec[str_key] = copy.deepcopy(spec_value)  # 用户字段副本

    # 名称再次清洗，覆盖用户直接传入 name 后可能引入的非法字符。
    dict_spec["name"] = sanitize_name(str(dict_spec["name"]))  # 最终规格名称

    # 当前版本固定目标和 RTL 方言，避免默认模板被用户覆盖成不支持状态。
    dict_spec["target"] = "rtl"  # 固定 RTL 目标

    # RTL 方言只接受 Verilog，并将空值归一化为默认值。
    dict_spec["rtl_dialect"] = _normalize_rtl_dialect(dict_spec.get("rtl_dialect"))  # 归一化 RTL 方言

    # 风格配置接受空值或 erie_strict，其他值交给用户修正。
    dict_spec["rtl_style_profile"] = _normalize_rtl_style_profile(  # 归一化 RTL 风格配置
        dict_spec.get("rtl_style_profile")  # 用户提供的 RTL 风格字段
    )

    # 根据用户是否显式给出字段，决定是否回填默认输出、说明和约束。
    _apply_rtl_output_defaults(
        dict_spec,
        outputs_explicit="outputs" in raw,
        description_explicit="description" in raw,
        constraints_explicit="constraints" in raw,
    )

    # 结构化设计需求必须保持对象形态，非对象输入降级为空对象。
    dict_spec["design_requirements"] = _normalize_design_requirements(  # 结构化设计需求
        dict_spec.get("design_requirements")  # 用户提供的设计需求字段
    )

    # 流式能力使用受控枚举，避免下游 prompt 拼装出现任意标签。
    dict_spec["streamability"] = _normalize_streamability(dict_spec.get("streamability"))  # 流式能力枚举

    # 接口族只接受已知总线族或空值，未知族需要用户显式补充适配。
    dict_spec["interface_family"] = _normalize_interface_family(  # 接口族枚举或空值
        dict_spec.get("interface_family")  # 用户提供的接口族字段
    )

    # 接口配置保持字典，非字典输入不会被当作结构化配置使用。
    dict_spec["interface_profile"] = _normalize_interface_profile(  # 接口补充配置对象
        dict_spec.get("interface_profile")  # 用户提供的接口配置字段
    )

    # 流水需求默认为真，用户传入布尔兼容值时按 Python bool 归一。
    dict_spec["pipeline_required"] = _normalize_pipeline_required(  # 流水化需求标记
        dict_spec.get("pipeline_required")  # 用户提供的流水需求字段
    )

    # 代码计划开关决定后续流程是否强制生成可审查实现路线。
    dict_spec["codegen_plan_required"] = _normalize_codegen_plan_required(  # 代码计划需求标记
        dict_spec.get("codegen_plan_required")  # 用户输入的计划强制开关
    )

    # 代码计划路径在规格层只做字符串化，不提前假设文件系统边界。
    dict_spec["codegen_plan_path"] = _normalize_codegen_plan_path(  # 代码计划路径
        dict_spec.get("codegen_plan_path")  # 用户提供的计划路径字段
    )

    # 文件角色确认在规格层只做 JSON shape 与 POSIX 词法校验。
    dict_spec["file_role_confirmations"] = _normalize_file_role_confirmations(  # 文件角色确认映射
        dict_spec.get("file_role_confirmations")  # 用户提供的路径到角色映射
    )

    # 顶层语义检查点会被后续验证和报告按统一字段读取。
    dict_spec["semantic_checkpoints"] = normalize_checkpoint_items(  # 验证报告锚点列表
        dict_spec.get("semantic_checkpoints")  # 用户声明的全局语义锚点
    )

    # 子功能逐项归一化，保留原始顺序用于后续代码生成和报告。
    dict_spec["subfunctions"] = [
        normalize_subfunction(subfunction_item, int_index)  # 子功能归一化结果
        for int_index, subfunction_item in enumerate(dict_spec.get("subfunctions", []))  # 子功能原始枚举
    ]  # 归一化子功能列表

    # 完成所有默认值和归一化后再做结构校验，错误更接近最终消费形态。
    _validate_shape(dict_spec)

    # 返回下游生成器可直接消费的稳定规格。
    return dict_spec

# 子功能条目可由顶层规格或独立测试直接调用归一化。
def normalize_subfunction(subfunction: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """
    将单个子功能描述归一化为稳定字段集合。

    :param subfunction: 用户提供的子功能对象。
    :param index: 子功能在列表中的零基序号，用于生成默认名称和 id。
    :return: 包含输入、输出、行为、依赖和检查点的子功能字典。
    :raises SpecError: 当子功能不是 JSON object 时抛出。
    """

    # 子功能必须保持对象形态，避免字符串需求被误当作结构化节点。
    if not isinstance(subfunction, dict):

        # 子功能不是对象时无法区分输入、输出、依赖和行为字段。
        raise SpecError("> ERR: [Python] Each subfunction must be a JSON object.")

    # 缺失名称时用序号生成稳定默认名，便于报告定位。
    str_name = sanitize_name(str(subfunction.get("name") or f"subfunction_{index + 1}"))  # 安全化子功能标识

    # 子功能所有列表类字段统一用 helper 展开，行为类字段再包成信息字典。
    dict_normalized = {
        "name": str_name,  # 子功能安全名称
        "inputs": _as_list(subfunction.get("inputs")),  # 子功能输入列表
        "outputs": _as_list(subfunction.get("outputs")),  # 子功能输出列表
        "behavior": normalize_info_items(subfunction.get("behavior"), "behavior"),  # 行为条目字典列表
        "constraints": normalize_info_items(subfunction.get("constraints"), "constraints"),  # 约束条目字典列表
        "dependencies": [  # 子功能依赖列表
            sanitize_name(str(dependency_item))  # 依赖子功能安全名称
            for dependency_item in _as_list(subfunction.get("dependencies"))  # 原始依赖条目
        ],
        "source_references": _as_list(subfunction.get("source_references")),  # 外部参考输入列表
        "test_intent": normalize_info_items(subfunction.get("test_intent"), "test_intent"),  # 测试意图条目
        "semantic_checkpoints": normalize_checkpoint_items(subfunction.get("semantic_checkpoints")),  # 检查点列表
    }

    # 返回结构化子功能，供顶层规格和 prompt 组装复用。
    return dict_normalized

# 检查点支持字符串和字典两种输入形态。
def normalize_checkpoint_items(value: Any) -> list[dict[str, Any]]:
    """
    将语义检查点输入转换为统一字典列表。

    :param value: 单个检查点、检查点列表或空值。
    :return: 每项包含 id、category、signals、verification_hint 和 text 的列表。
    """

    # 收集归一化后的检查点，保留输入顺序用于报告和验证计划。
    list_items: list[dict[str, Any]] = []  # 归一化检查点列表

    # 空值、单项和列表都会先通过 _as_list 展开为可遍历序列。
    for int_index, checkpoint_item in enumerate(_as_list(value), start=1):

        # 字典输入保留用户已有字段，只补齐生成器必需的默认键。
        if isinstance(checkpoint_item, dict):

            # 深拷贝避免 setdefault 修改调用方原始检查点。
            dict_payload = copy.deepcopy(checkpoint_item)  # 检查点字段副本

            # 缺失 id 时按顺序生成稳定编号，便于错误报告引用。
            dict_payload.setdefault("id", f"checkpoint_{int_index}")

            # 缺失分类时默认归入行为检查，匹配原有语义。
            dict_payload.setdefault("category", "behavior")

            # 信号列表默认空，后续验证器可按需补充。
            dict_payload.setdefault("signals", [])

            # 验证提示默认空字符串，避免下游字段缺失。
            dict_payload.setdefault("verification_hint", "")

            # 文本优先使用 text，其次 description，最后退回 id。
            dict_payload.setdefault(
                "text",
                str(dict_payload.get("text") or dict_payload.get("description") or dict_payload["id"]),
            )

            # 保留该检查点在输入中的顺序。
            list_items.append(dict_payload)

        # 非字典输入直接作为检查点文本，补齐统一元数据。
        else:

            # 字符串或标量检查点转换为默认行为检查点。
            list_items.append(
                {
                    "id": f"checkpoint_{int_index}",  # 自动检查点编号
                    "category": "behavior",  # 默认检查点分类
                    "signals": [],  # 默认无显式信号约束
                    "verification_hint": "",  # 默认无额外验证提示
                    "text": str(checkpoint_item),  # 用户提供的检查点文本
                }
            )

    # 返回完整检查点列表，空输入对应空列表。
    return list_items

# 行为、约束和测试意图共享同一种信息条目形态。
def normalize_info_items(value: Any, field: str) -> list[dict[str, Any]]:
    """
    将行为类字段转换为统一信息字典列表。

    :param value: 单个条目、条目列表或空值。
    :param field: 字段名，用于生成默认 id 前缀。
    :return: 每项包含 id、text、evidence 和 verification_cases 的列表。
    """

    # 每个输入项按原顺序转换，便于 prompt 和报告保持用户叙述顺序。
    list_items = [
        _normalize_info_item(info_item, field, int_index)  # 单条行为类信息
        for int_index, info_item in enumerate(_as_list(value))  # 原始信息条目枚举
    ]  # 归一化信息条目列表

    # 返回统一的信息条目列表。
    return list_items

# 单条信息既可来自字符串，也可来自已有字典。
def _normalize_info_item(item: Any, field: str, index: int) -> dict[str, Any]:
    """
    归一化单个行为类信息条目。

    :param item: 用户提供的信息条目，可以是字典或标量。
    :param field: 所属字段名，用于生成默认 id 前缀。
    :param index: 条目在展开列表中的零基序号。
    :return: 带 id、text、evidence 和 verification_cases 的字典。
    """

    # 字典条目保留用户扩展字段，同时补齐生成器需要的公共键。
    if isinstance(item, dict):

        # 文本字段兼容 text 与 description，避免重复维护两套输入格式。
        str_text = str(item.get("text") or item.get("description") or "")  # 信息条目正文

        # 深拷贝用户字典，防止归一化过程修改调用方对象。
        dict_payload = copy.deepcopy(item)  # 信息条目字段副本

        # 统一写入 text，确保后续消费不必再查 description。
        dict_payload["text"] = str_text  # 统一正文文本

        # 缺失 id 时根据字段名和位置生成稳定编号。
        dict_payload.setdefault("id", f"{field}_{index + 1}")

        # evidence 默认空列表，便于后续附加证据而不做存在性判断。
        dict_payload.setdefault("evidence", [])

        # verification_cases 默认空列表，兼容后续测试意图展开。
        dict_payload.setdefault("verification_cases", [])

        # 返回补齐后的用户条目。
        return dict_payload

    # 标量信息按正文处理，并补齐空证据与空验证用例。
    return {
        "id": f"{field}_{index + 1}",  # 自动信息条目编号
        "text": str(item),  # 标量条目的正文
        "evidence": [],  # 默认无证据来源
        "verification_cases": [],  # 默认无验证用例
    }

# 文件读取入口保持异常包装，便于 CLI 报告规格路径。
def read_spec(path: Path, target: str | None = None) -> dict[str, Any]:
    """
    从 JSON 文件读取并归一化生成规格。

    :param path: 规格 JSON 文件路径。
    :param target: 可选生成目标覆盖值；当前仅接受 ``rtl``。
    :return: 通过结构校验的规格字典。
    :raises SpecError: 当 JSON 无法解析或规格字段非法时抛出。
    """

    # JSON 解析错误需要带上文件路径，方便 CLI 用户定位输入文件。
    try:

        # 按 UTF-8 读取规格文件，保持中文需求和备注不被转义。
        dict_raw = json.loads(path.read_text(encoding="utf-8"))  # 原始规格对象

    # JSONDecodeError 需要转换成领域异常，避免上层暴露解析器类型细节。
    except json.JSONDecodeError as exc:

        # 包含路径和解析器原始信息，保留足够排障上下文。
        raise SpecError(f"> ERR: [Python] Invalid JSON spec {path}: {exc}") from exc

    # 读取成功后进入统一归一化路径，文件和内存输入共享校验逻辑。
    return normalize_spec(dict_raw, target=target)

# 写出规格主要供脚手架和测试 fixture 使用。
def write_spec(path: Path, spec: dict[str, Any]) -> None:
    """
    将规格字典以 UTF-8 JSON 写入目标路径。

    :param path: 目标 JSON 文件路径，父目录不存在时会创建。
    :param spec: 待写出的规格字典。
    :return: 无返回值；函数完成后目标文件包含格式化 JSON。
    """

    # 写文件前创建父目录，让脚手架命令可直接面向新目录输出。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 保留中文原文并写入末尾换行，方便版本控制 diff。
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# 所有目标校验统一在这里收敛。
def _require_target(target: str) -> str:
    """
    校验并返回当前生成器支持的规范目标名。

    :param target: 用户或调用方请求的目标名称。
    :return: 规范化后的目标名称，当前固定为 ``rtl``。
    :raises SpecError: 当目标不是 ``rtl`` 时抛出。
    """

    # 目标大小写不影响用户输入，内部统一使用小写枚举值。
    str_normalized = target.lower()  # 小写目标名

    # 当前生成器只支持 RTL，其他目标需要未来显式扩展。
    if str_normalized != "rtl":

        # 非 RTL 目标没有对应 prompt、验证或输出合同。
        raise SpecError("> ERR: [Python] Only target 'rtl' is supported.")

    # 返回内部使用的规范目标名。
    return str_normalized

# RTL 方言校验保持极窄边界。
def _normalize_rtl_dialect(value: Any) -> str:
    """
    将 RTL 方言字段归一化为唯一支持值。

    :param value: 用户提供的 RTL 方言字段。
    :return: 规范方言名 ``verilog``。
    :raises SpecError: 当方言不是 Verilog-2001 兼容输入时抛出。
    """

    # 空值和 verilog 都映射到唯一受支持方言。
    if value in (None, "", "verilog"):

        # 返回生成器内部唯一支持的 RTL 方言。
        return "verilog"

    # 其他方言可能改变语法和验证链，必须显式拒绝。
    raise SpecError("> ERR: [Python] Only Verilog-2001 is supported.")

# RTL 风格配置当前只识别 erie_strict。
def _normalize_rtl_style_profile(value: Any) -> str | None:
    """
    归一化 RTL 风格配置名称。

    :param value: 用户提供的风格配置值。
    :return: ``erie_strict`` 或空值。
    :raises SpecError: 当风格配置不是支持值时抛出。
    """

    # 空值表示不强制风格配置，保留调用方兼容入口。
    if value in (None, ""):

        # 返回空风格，让调用方按默认策略处理。
        return None

    # 风格名大小写不敏感，内部统一为小写。
    str_normalized = str(value).lower()  # 小写风格名

    # 只允许已治理的 erie_strict 风格进入生成流程。
    if str_normalized != "erie_strict":

        # 未知风格可能破坏 prompt 和验证预期，因此明确报错。
        raise SpecError("> ERR: [Python] Unknown rtl_style_profile. Supported value: erie_strict.")

    # 返回规范风格名。
    return str_normalized

# 设计需求字段必须保持字典形态。
def _normalize_design_requirements(value: Any) -> dict[str, Any]:
    """
    将设计需求字段转换为结构化字典。

    :param value: 用户提供的设计需求字段。
    :return: 深拷贝后的需求字典；非字典输入返回空字典。
    """

    # 字典输入深拷贝后返回，避免后续修改污染调用方对象。
    if isinstance(value, dict):

        # 返回结构化设计需求副本。
        return copy.deepcopy(value)

    # 非字典输入不携带可治理结构，降级为空需求对象。
    return {}

# 流式能力使用固定枚举，避免下游出现自由文本分支。
def _normalize_streamability(value: Any) -> str:
    """
    归一化流式能力枚举。

    :param value: 用户提供的流式能力字段。
    :return: ``streamable``、``non_streamable`` 或 ``unknown``。
    :raises SpecError: 当字段值不在受控枚举内时抛出。
    """

    # 空值代表未确认，统一转成 unknown。
    str_normalized = str(value or "unknown")  # 流式能力枚举值

    # 仅允许已定义的三态枚举。
    if str_normalized not in {"streamable", "non_streamable", "unknown"}:

        # 未知标签会让 prompt 和后续验证误读设计能力。
        raise SpecError("> ERR: [Python] streamability must be streamable, non_streamable, or unknown.")

    # 返回受控的流式能力枚举。
    return str_normalized

# 接口族限制为当前 workflow 能理解的总线类型。
def _normalize_interface_family(value: Any) -> str | None:
    """
    归一化接口族字段。

    :param value: 用户提供的接口族名称。
    :return: 受控接口族名称或空值。
    :raises SpecError: 当接口族不在支持集合内时抛出。
    """

    # 空接口族表示由 prompt 或后续确认流程决定。
    if value in (None, ""):

        # 返回空接口族，保留 native 默认以外的未确认状态。
        return None

    # 用户输入按字符串处理，保留原有大小写敏感行为。
    str_normalized = str(value)  # 接口族名称

    # 只接受已声明的接口族，避免生成器误解释自由文本协议。
    if str_normalized not in {"native", "axi_stream", "axi4", "axi4_lite", "ahb", "apb", "custom"}:

        # 长错误文案拆行保持可读，同时保留原始核心语义。
        raise SpecError(
            "> ERR: [Python] interface_family must be native, axi_stream, axi4, axi4_lite, ahb, apb, or custom."
        )

    # 返回受控接口族名称。
    return str_normalized

# 接口族补充配置必须保持字典。
def _normalize_interface_profile(value: Any) -> dict[str, Any]:
    """
    将接口族补充配置转换为字典。

    :param value: 用户提供的接口配置。
    :return: 深拷贝后的配置字典；非字典输入返回空字典。
    """

    # 字典输入深拷贝后返回，调用方原对象不受归一化影响。
    if isinstance(value, dict):

        # 返回接口配置副本。
        return copy.deepcopy(value)

    # 非字典配置不可结构化消费，降级为空对象。
    return {}

# pipeline_required 缺省为真，兼容旧规格的隐式行为。
def _normalize_pipeline_required(value: Any) -> bool:
    """
    归一化流水化需求开关。

    :param value: 用户提供的开关值。
    :return: 空值时返回 ``True``，其他值按 Python 布尔语义转换。
    """

    # 空值使用默认真，其他值按 Python truthiness 兼容旧输入。
    return True if value is None else bool(value)

# codegen_plan_required 缺省为真，保持设计计划强约束。
def _normalize_codegen_plan_required(value: Any) -> bool:
    """
    归一化代码生成计划需求开关。

    :param value: 用户提供的开关值。
    :return: 空值时返回 ``True``，其他值按 Python 布尔语义转换。
    """

    # 代码计划默认启用，避免 workflow 缺少可审查的实现路线。
    return True if value is None else bool(value)

# 代码计划路径允许空值，非空值统一转字符串。
def _normalize_codegen_plan_path(value: Any) -> str | None:
    """
    归一化外部代码计划路径字段。

    :param value: 用户提供的路径字段。
    :return: 空路径返回 ``None``，非空路径返回字符串。
    """

    # 空路径表示不绑定外部代码计划文件。
    if value in (None, ""):

        # 返回空路径标记。
        return None

    # 返回字符串路径，安全性由后续使用位置按上下文校验。
    return str(value)

# 文件角色确认只接受规范 POSIX 相对路径与两个冻结角色值。
def _normalize_file_role_confirmations(value: Any) -> dict[str, str]:
    """归一化文件相对路径到 design/testbench 的确认映射。

    参数:
        value: 用户提供的 JSON object 或空值。
    返回:
        与调用方容器隔离的路径角色字典。
    异常:
        SpecError: 容器、路径或角色不满足规格词法合同时抛出。
    """

    # 空值等价于尚未完成任何文件角色确认。
    if value is None:

        # 每次返回新字典，避免默认容器跨调用共享。
        return {}

    # JSON object 是确认映射唯一允许的容器形态。
    if not isinstance(value, dict):

        # 列表或标量无法建立稳定文件身份。
        raise SpecError("> ERR: [Python] file_role_confirmations must be an object.")

    # 结果容器不复用用户字典，保持归一化输出独立。
    dict_confirmations: dict[str, str] = {}  # 已通过词法检查的确认映射

    # 每个键值对都必须独立满足路径和角色合同。
    for obj_path, obj_role in value.items():

        # 非字符串键或值不能来自规范 JSON 文件角色映射。
        if not isinstance(obj_path, str) or not isinstance(obj_role, str):

            # 类型错误在路径解析前使用稳定规格异常报告。
            raise SpecError("> ERR: [Python] file role confirmation keys and values must be strings.")

        # 确认键必须直接采用非空 POSIX 相对路径写法。
        if not obj_path or "\\" in obj_path:

            # Windows 分隔符会产生跨平台身份漂移。
            raise SpecError("> ERR: [Python] file role confirmation paths must be POSIX relative paths.")

        # POSIX 与 Windows 绝对路径都不能越过后续扫描根。
        if PurePosixPath(obj_path).is_absolute() or PureWindowsPath(obj_path).is_absolute():

            # 绝对路径不属于可迁移规格合同。
            raise SpecError("> ERR: [Python] file role confirmation paths must be relative.")

        # 路径段用于拒绝父目录跳转与非规范身份。
        tuple_parts = PurePosixPath(obj_path).parts  # 当前确认路径的词法段

        # 点段、父段、空段与驱动器冒号都属于非法路径结构。
        if any(str_part in {".", "..", ""} for str_part in tuple_parts) or ":" in obj_path:

            # 越界或非规范路径必须由用户先行修正。
            raise SpecError("> ERR: [Python] file role confirmation path contains an invalid segment.")

        # PurePosixPath 折叠后的原文差异会形成重复身份风险。
        str_normalized_path = PurePosixPath(obj_path).as_posix()  # 规范 POSIX 相对路径

        # 调用方必须直接提供规范形式，规格层不静默改写身份。
        if str_normalized_path != obj_path:

            # 非规范键可能与另一确认键指向同一文件。
            raise SpecError("> ERR: [Python] file role confirmation path must be normalized.")

        # 只有 Verilog 与 SystemVerilog 文件参与角色确认。
        if PurePosixPath(str_normalized_path).suffix.casefold() not in {".v", ".sv"}:

            # 其他文件类型不属于 VG148/VG149 预检范围。
            raise SpecError("> ERR: [Python] file role confirmation paths must end in .v or .sv.")

        # 冻结角色值不接受别名或大小写自动改写。
        if obj_role not in {"design", "testbench"}:

            # 未知角色不能进入 quality preflight。
            raise SpecError("> ERR: [Python] file role confirmation values must be design or testbench.")

        # 保存已经验证的路径与角色原文。
        dict_confirmations[str_normalized_path] = obj_role  # 当前文件的确认角色

    # 返回独立映射，保证后续修改不影响用户输入。
    return dict_confirmations

# 用户没有显式字段时回填 RTL 默认输出、说明和约束。
def _apply_rtl_output_defaults(
    spec: dict[str, Any],
    *,
    outputs_explicit: bool,
    description_explicit: bool,
    constraints_explicit: bool,
) -> None:
    """
    按用户显式字段情况回填 RTL 输出、说明和约束默认值。

    :param spec: 已包含安全名称的规格字典。
    :param outputs_explicit: 用户是否显式提供 outputs 字段。
    :param description_explicit: 用户是否显式提供 description 字段。
    :param constraints_explicit: 用户是否显式提供 constraints 字段。
    :return: 无返回值；函数会原地更新规格字典。
    """

    # 输出路径和默认约束都依赖清洗后的规格名。
    str_name = spec["name"]  # 当前规格安全名称

    # 未显式指定输出时使用标准 rtl/tb 双文件布局。
    if not outputs_explicit:

        # 默认输出路径必须随规格名更新，避免 name 覆盖后仍写旧文件名。
        spec["outputs"] = [
            {"path": f"rtl/{str_name}.v", "kind": "source", "language": "verilog"},  # 主 RTL 输出路径
            {"path": f"tb/tb_{str_name}.v", "kind": "testbench", "language": "verilog"},  # 默认测试平台路径
        ]

    # 未显式指定说明时回填通用可综合 RTL 说明。
    if not description_explicit:

        # 英文说明是 prompt 基线的一部分，避免治理时改变生成语义。
        spec["description"] = "Implement a synthesizable Verilog-2001 RTL module."  # prompt 默认任务句

    # 未显式指定约束时从默认模板复用完整列表。
    if not constraints_explicit:

        # 调用默认模板取得约束，避免约束文本在多个位置漂移。
        spec["constraints"] = _rtl_defaults(str_name)["constraints"]  # 默认 RTL 约束列表

# 顶层结构校验确保生成器接收到可消费规格。
def _validate_shape(spec: dict[str, Any]) -> None:
    """
    校验归一化后顶层规格的结构边界。

    :param spec: 已经过默认值回填和字段归一化的规格字典。
    :return: 无返回值；结构非法时抛出异常。
    :raises SpecError: 当规格缺少可生成 RTL 所需字段或字段形态非法时抛出。
    """

    # 目标字段必须在归一化后仍保持 rtl。
    if spec["target"] != "rtl":

        # 目标字段漂移说明上游归一化合同被破坏。
        raise SpecError("> ERR: [Python] Spec target must be 'rtl'.")

    # RTL 方言必须保持 Verilog，避免后续模板生成不支持语法。
    if spec["rtl_dialect"] != "verilog":

        # 非 Verilog 方言没有对应的生成模板和静态校验流程。
        raise SpecError("> ERR: [Python] Only Verilog-2001 is supported.")

    # interfaces 必须是对象，端口列表位于该对象内部。
    if not isinstance(spec.get("interfaces"), dict):

        # 接口不是对象时无法承载 ports、协议和时序扩展字段。
        raise SpecError("> ERR: [Python] Spec interfaces must be an object.")

    # 端口列表是 RTL 生成的最小接口输入。
    list_ports = spec["interfaces"].get("ports")  # 顶层端口列表

    # 端口必须是非空列表，否则无法生成 module 声明。
    if not isinstance(list_ports, list) or not list_ports:

        # 没有端口列表时生成器无法形成合法 module 声明。
        raise SpecError("> ERR: [Python] Spec interfaces.ports must be a non-empty list.")

    # 逐个端口校验名称、方向和位宽。
    for port_item in list_ports:

        # 端口对象校验会报告具体字段类别。
        _validate_port(port_item)

    # 输出文件列表必须存在，后续写文件和报告都依赖它。
    if not isinstance(spec.get("outputs"), list) or not spec["outputs"]:

        # 缺少输出列表时 workflow 无法决定产物路径。
        raise SpecError("> ERR: [Python] Spec outputs must be a non-empty list.")

    # 逐个输出项校验相对路径和语言约束。
    for output_item in spec["outputs"]:

        # 输出对象校验会阻止绝对路径或目录穿越。
        _validate_output(output_item)

    # 这些字段必须保持列表，便于 prompt 拼接和报告按序遍历。
    for str_key in ("behavior", "constraints", "notes", "subfunctions"):

        # 非列表输入会让下游误把字符串逐字符处理，因此在这里拒绝。
        if not isinstance(spec.get(str_key), list):

            # 字段名直接进入诊断文本，帮助用户定位错误 JSON 节点。
            raise SpecError(f"> ERR: [Python] Spec {str_key} must be a list.")

    # workflow 扩展配置必须是对象，模板 id 校验在专用 helper 内完成。
    if not isinstance(spec.get("workflow"), dict):

        # workflow 不是对象时无法读取模板 id 和流程开关。
        raise SpecError("> ERR: [Python] Spec workflow must be an object.")

    # 校验 workflow 内部模板引用，避免无效模板进入后续路由。
    _validate_workflow(spec.get("workflow", {}))

    # performance 扩展配置必须是对象，后续资源约束按键读取。
    if not isinstance(spec.get("performance"), dict):

        # performance 不是对象时无法承载频率、资源或延迟目标。
        raise SpecError("> ERR: [Python] Spec performance must be an object.")

# 单个端口校验保持轻量，避免在规格层绑定过多 Verilog 语义。
def _validate_port(port: Any) -> None:
    """
    校验单个端口描述是否满足 module 声明的最低要求。

    :param port: 用户提供的端口条目。
    :return: 无返回值；端口非法时抛出异常。
    :raises SpecError: 当端口不是对象、缺名称、方向非法或位宽非法时抛出。
    """

    # 端口必须是对象，才能描述 name/direction/width 等字段。
    if not isinstance(port, dict):

        # 非对象端口无法表达声明所需的多个属性。
        raise SpecError("> ERR: [Python] Each port must be an object.")

    # 端口名是 module 声明的必要字段。
    if not port.get("name"):

        # 缺少端口名时无法生成可读接口。
        raise SpecError("> ERR: [Python] Each port requires a name.")

    # 端口方向限制为 Verilog module 声明支持的三类。
    if port.get("direction") not in {"input", "output", "inout"}:

        # 未知方向会直接生成非法端口声明。
        raise SpecError("> ERR: [Python] Port direction must be input, output, or inout.")

    # 位宽允许用户传入可转整数的值，兼容 JSON 字符串场景。
    try:

        # 缺失位宽时按单比特处理。
        int_width = int(port.get("width", 1))  # 端口位宽

    # 无法转整数的位宽统一报告正整数错误。
    except (TypeError, ValueError):

        # 隐藏内部转换异常类型，给用户稳定错误文案。
        raise SpecError("> ERR: [Python] Port width must be a positive integer.") from None

    # 零或负位宽在 Verilog 端口声明中无效。
    if int_width <= 0:

        # 非正数位宽无法转换为合法 Verilog 范围。
        raise SpecError("> ERR: [Python] Port width must be a positive integer.")

# 输出项校验主要守住路径安全和语言扩展名一致性。
def _validate_output(output: Any) -> None:
    """
    校验单个输出产物描述的路径安全和语言约束。

    :param output: 用户提供的输出条目。
    :return: 无返回值；输出条目非法时抛出异常。
    :raises SpecError: 当输出不是对象、路径不安全或扩展名不匹配时抛出。
    """

    # 输出项必须是对象，才能包含 path/kind/language。
    if not isinstance(output, dict):

        # 非对象输出无法携带路径、类型和语言信息。
        raise SpecError("> ERR: [Python] Each output must be an object.")

    # path 为空时无法决定写入位置。
    str_path = str(output.get("path") or "")  # 输出相对路径

    # 每个输出项都必须显式给出路径。
    if not str_path:

        # 缺少路径时无法在 workflow 中创建对应产物。
        raise SpecError("> ERR: [Python] Each output requires a path.")

    # 输出路径必须是安全相对路径，禁止反斜杠、绝对路径和目录穿越。
    if "\\" in str_path or str_path.startswith("/") or ".." in str_path.split("/"):

        # 错误文案包含原路径，便于用户定位非法输出项。
        raise SpecError(f"> ERR: [Python] Output path must be safe and relative: {str_path!r}")

    # 后缀用于校验 Verilog 输出扩展名。
    str_suffix = Path(str_path).suffix.lower()  # 输出文件后缀

    # 语言默认为 verilog，兼容旧规格缺省行为。
    str_language = str(output.get("language") or "verilog").lower()  # 输出文件语言

    # Verilog 源文件和 Verilog testbench 都必须使用 .v 后缀。
    if str_suffix != ".v":

        # Verilog 输出统一要求 .v，避免文件语言和扩展名冲突。
        raise SpecError("> ERR: [Python] Verilog RTL and Verilog testbench outputs must use .v paths.")

    # 当前 workflow 只允许 Verilog 语言标签。
    if str_language != "verilog":

        # 非 Verilog 产物当前没有生成和验证合同。
        raise SpecError("> ERR: [Python] Output language must be verilog.")

# 多形态用户输入统一转为列表。
def _as_list(value: Any) -> list[Any]:
    """
    将空值、列表或标量输入统一转换为列表。

    :param value: 用户提供的任意字段值。
    :return: 空值对应空列表，列表原样返回，标量包装为单元素列表。
    """

    # 空值代表没有条目，归一化为空列表。
    if value is None:

        # 返回空列表，避免调用方对 None 特判。
        return []

    # 原本就是列表时保持原对象顺序。
    if isinstance(value, list):

        # 返回列表输入，保留调用方传入的条目顺序。
        return value

    # 标量输入提升为单元素列表，兼容简单 JSON 写法。
    return [value]

# workflow 模板引用需要向模板注册表确认。
def _validate_workflow(workflow: dict[str, Any]) -> None:
    """
    校验 workflow 模板引用是否存在且类型正确。

    :param workflow: 归一化后的 workflow 配置字典。
    :return: 无返回值；模板引用非法时抛出异常。
    :raises SpecError: 当模板 id 不是字符串或注册表拒绝该 id 时抛出。
    """

    # 模板 id 为空时表示不启用模板路由。
    use_case_template_id = workflow.get("use_case_template_id")  # 工作流模板标识

    # 空模板引用无需进一步校验。
    if use_case_template_id in (None, ""):

        # 直接返回，保持无模板 workflow 的兼容行为。
        return

    # 模板 id 必须是字符串，避免注册表接收不可序列化类型。
    if not isinstance(use_case_template_id, str):

        # 非字符串模板 id 无法与注册表中的模板键做稳定匹配。
        raise SpecError("> ERR: [Python] workflow.use_case_template_id must be a string when provided.")

    # 延迟导入模板校验器，避免规格模块导入时拉起额外依赖图。
    from .use_case_templates import UseCaseTemplateError, validate_use_case_template_id

    # 将模板注册表异常转换为规格异常，统一对外错误类型。
    try:

        # 验证模板 id 是否存在且符合注册表命名约束。
        validate_use_case_template_id(use_case_template_id)

    # 模板注册表的领域错误需要对外包装成 SpecError。
    except UseCaseTemplateError as exc:

        # 保留原始错误文本，方便用户修正模板 id。
        raise SpecError(f"> ERR: [Python] Invalid workflow template: {exc}") from exc
