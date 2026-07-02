"""加载并选择本地精化 Verilog 设计模板。"""

# 延迟注解解析，避免导入期处理嵌套 JSON 类型。
from __future__ import annotations

# 标准库依赖负责模板元数据复制、JSON catalog 读取和路径拼接。
import copy
import json
from pathlib import Path
from typing import Any

# config 模块提供 skill 根目录，避免硬编码安装路径。
from .config import skill_root

# 精化模板目录位于 skill assets 内，随发布包一起分发。
TEMPLATE_ROOT = skill_root() / "assets" / "refined_verilog_templates"  # 精化模板资产目录

# catalog 记录模板 id、选择条件和正文文件相对路径。
CATALOG_PATH = TEMPLATE_ROOT / "catalog.json"  # 精化模板 catalog 路径

# 公开读取器加载精化模板 catalog。
def load_refined_template_catalog() -> dict[str, Any]:
    """读取并校验精化 Verilog 模板 catalog。

    参数:
        无。

    返回:
        catalog 字典，至少包含非空 templates 列表。

    异常:
        ValueError: catalog 不是对象，或 templates 字段缺失/为空。
    """

    # catalog 是本地资产，但仍做最小 schema 校验。
    dict_payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))  # 精化模板 catalog 载荷

    # 顶层必须是 JSON 对象，才能读取 templates 字段。
    if not isinstance(dict_payload, dict):

        # 错误消息保留原始关键信息，并补齐项目脚本前缀。
        raise ValueError(f"> ERR: [Python] Refined template catalog must be a JSON object: {CATALOG_PATH}")

    # templates 是后续选择器遍历的主数据。
    list_templates = dict_payload.get("templates")  # catalog 原始模板列表

    # 模板列表缺失或为空时无法继续选择。
    if not isinstance(list_templates, list) or not list_templates:

        # 错误消息使用项目脚本前缀，便于 CLI 统一收敛。
        raise ValueError("> ERR: [Python] Refined template catalog must contain a non-empty templates list.")

    # 返回 catalog 载荷，调用方只读使用。
    return dict_payload

# 公开枚举器返回模板元数据的深拷贝。
def list_refined_templates() -> list[dict[str, Any]]:
    """列出全部本地精化模板元数据。

    参数:
        无。

    返回:
        catalog templates 的深拷贝，避免调用方污染原始 catalog。
    """

    # 深拷贝保护 catalog 结构，调用方可以安全修改返回对象。
    return [copy.deepcopy(item) for item in load_refined_template_catalog()["templates"]]

# 公开选择器按规格或 existing RTL analysis 选择精化模板。
def select_refined_templates(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """选择适用于当前规格的精化模板并加载正文。

    参数:
        spec: 用户规格或 existing RTL analysis 字典。

    返回:
        已选模板 payload 列表；每个 payload 附带 Path 类型 path 和 content 文本。
    """

    # 先用规则匹配出模板 id，后续按 catalog 顺序输出。
    list_selected_ids = _match_template_ids(spec)  # 当前规格命中的模板 id

    # results 保存已经加载正文的模板 payload。
    list_results: list[dict[str, Any]] = []  # 已选择精化模板列表

    # catalog 顺序决定输出模板顺序，避免规则匹配顺序影响 prompt 稳定性。
    for dict_template in load_refined_template_catalog()["templates"]:

        # template_id 缺失时按空字符串处理，不会命中选择列表。
        str_template_id = str(dict_template.get("template_id") or "")  # 用于和规则命中列表对照的模板 id

        # 未命中的模板不加载正文，减少 IO。
        if str_template_id not in list_selected_ids:

            # 当前模板与规格特征无关。
            continue

        # path 字段是相对于 TEMPLATE_ROOT 的模板正文路径。
        str_rel_path = str(dict_template.get("path") or "")  # 模板正文相对路径

        # 转成绝对 Path，保留旧版返回语义。
        path_template = TEMPLATE_ROOT / str_rel_path  # 模板正文文件路径

        # 返回 payload 必须复制，不能把 path/content 写回 catalog。
        dict_payload = copy.deepcopy(dict_template)  # 当前命中模板 payload

        # path 返回 Path 对象，保持旧版调用方兼容。
        dict_payload["path"] = path_template  # 模板正文路径对象

        # content 是 prompt 拼接所需的模板正文。
        dict_payload["content"] = path_template.read_text(encoding="utf-8")  # 模板正文文本

        # 将命中模板加入返回列表。
        list_results.append(dict_payload)

    # 返回已加载模板 payload。
    return list_results

# 摘要入口为效果评估和报告输出提供轻量模板信息。
def summarize_refined_templates(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """返回当前规格命中的精化模板摘要。

    参数:
        spec: 用户规格或 existing RTL analysis 字典。

    返回:
        仅包含 template_id、title、path 和 selection_reason 的摘要列表。
    """

    # 摘要不包含模板正文，避免报告过大。
    return [
        {
            "template_id": item["template_id"],  # 模板 id
            "title": item.get("title"),  # 模板标题
            "path": str(item["path"]),  # 模板正文路径文本
            "selection_reason": item.get("selection_reason"),  # catalog 中记录的选择理由
        }
        for item in select_refined_templates(spec)
    ]

# 模板匹配入口区分 existing RTL analysis 和普通生成规格。
def _match_template_ids(spec: dict[str, Any]) -> list[str]:
    """匹配当前规格需要注入的精化模板 id。

    参数:
        spec: 用户规格或 existing RTL analysis 字典。

    返回:
        按规则顺序排列的模板 id 列表。
    """

    # analysis 字典同时包含 module_info 和 ports 时使用 existing RTL 匹配规则。
    if "module_info" in spec and "ports" in spec:

        # existing RTL analysis 已经抽取端口、状态和分解候选。
        return _match_from_analysis(spec)

    # 顶层 interface_family 优先表达用户明确接口意图。
    raw_top_interface_family = spec.get("interface_family")  # 顶层接口族字段

    # design_requirements 中的 interface_family 兼容旧规格形态。
    raw_nested_interface_family = (spec.get("design_requirements") or {}).get("interface_family")  # 嵌套接口族字段

    # interface_family 兼容顶层字段和 design_requirements 内字段。
    str_interface_family = str(raw_top_interface_family or raw_nested_interface_family or "").lower()  # 规格接口族

    # 规格文本用于关键词模板匹配。
    str_text = _spec_text(spec)  # 规格全文关键词池

    # selected 按旧版规则顺序累积模板 id。
    list_selected: list[str] = []  # 命中的模板 id

    # AXI4-Lite CSR 或控制寄存器描述命中 CSR shell 模板。
    if str_interface_family == "axi4_lite" or _has_any(
        str_text,
        ("csr", "register bank", "control register", "status register"),
    ):

        # CSR shell 模板帮助生成寄存器地址和读写路径。
        list_selected.append("axi4_lite_csr_shell")

    # ready/valid 或 AXI Stream 描述命中流接口切片模板。
    if str_interface_family == "axi_stream" or _has_any(
        str_text,
        ("ready valid", "ready/valid", "ready-valid", "tvalid", "tready"),
    ):

        # AXIS 模板提供 tvalid/tready 握手结构。
        list_selected.append("axis_ready_valid_slice")

    # AXI4、DMA 或 memory mapped 关键词命中互连端口组模板。
    if str_interface_family == "axi4" or _has_any(
        str_text,
        ("axi interconnect", "crossbar", "dma", "memory mapped", "burst transfer", "m_axi_"),
    ):

        # AXI 互连模板组织地址、数据和响应通道。
        list_selected.append("axi_interconnect_port_groups")

    # 卷积和滑窗关键词命中 load/store pipeline 模板。
    if _has_any(str_text, ("conv1d", "convolution", "ifm", "ofm", "line buffer", "sliding window", "weight buffer")):

        # 卷积模板覆盖 line buffer 和权重缓冲结构。
        list_selected.append("conv_load_store_pipeline")

    # FSM 或交通灯相位关键词命中状态迁移模板。
    if _has_any(str_text, ("fsm", "state machine", "traffic light", "phase transition", "state_current", "state_next")):

        # FSM 模板强调 state_current/state_next 分离。
        list_selected.append("fsm_state_transition")

    # 计数器关键词和状态/相位关键词同时出现时命中 bridge 模板。
    if _has_any(str_text, ("counter", "timer", "cnt", "reload", "phase duration")) and _has_any(
        str_text,
        ("state", "phase", "fsm"),
    ):

        # counter-state bridge 模板约束计数器和状态机交互。
        list_selected.append("counter_state_bridge")

    # wrapper/partition/merge 关键词命中顶层拼接模板。
    if _has_any(str_text, ("wrapper", "top-level stitching", "top level stitching", "partition", "merge", "recompose")):

        # wrapper 模板指导分区后的端口连接和顶层组合。
        list_selected.append("wrapper_top_stitching")

    # 输出寄存相关关键词单独命名，避免分支条件过长。
    tuple_output_register_tokens = (  # 触发输出寄存模板的关键词
        "phase output",  # 相位输出描述
        "output register",  # 输出寄存器描述
        "registered output",  # 已寄存输出描述
        "phase register",  # 相位寄存描述
        "phase transition",  # 相位迁移描述
        "traffic light",  # 交通灯状态输出描述
    )

    # phase output 或 registered output 关键词命中输出寄存模板。
    if _has_any(str_text, tuple_output_register_tokens):

        # 输出寄存模板帮助稳定相位输出和交通灯输出。
        list_selected.append("phase_output_registering")

    # semantic compare 和 checkpoint 关键词命中等价探针模板。
    if _has_any(str_text, ("equivalence", "semantic compare", "probe", "checkpoint probe")):

        # 等价探针模板提供语义比较观测点。
        list_selected.append("equivalence_wrapper_probe")

    # 返回按规则顺序命中的模板 id。
    return list_selected

# existing RTL analysis 匹配器使用抽取出的端口、状态和分解信息。
def _match_from_analysis(analysis: dict[str, Any]) -> list[str]:
    """从 existing RTL analysis 中匹配精化模板 id。

    参数:
        analysis: analyze-existing 生成的结构化分析字典。

    返回:
        根据端口、状态元素和分解候选命中的模板 id 列表。
    """

    # ports 来自 extractor 对模块端口的结构化抽取。
    list_ports: list[Any] = analysis.get("ports", []) or []  # analysis 端口列表

    # state_elements 描述寄存器、状态机和输出状态角色。
    list_state_elements: list[Any] = analysis.get("state_elements", []) or []  # analysis 状态元素列表

    # decomposition_candidates 说明是否存在可拆分子功能。
    list_decomposition: list[Any] = analysis.get("decomposition_candidates", []) or []  # analysis 分解候选列表

    # 文本池从模块名、端口名、状态角色和分解角色中提取。
    list_text_parts = [str((analysis.get("module_info") or {}).get("name") or "")]  # analysis 关键词片段

    # 端口名帮助识别 ready/valid 等接口模式。
    list_text_parts.extend(str(item.get("name") or "") for item in list_ports if isinstance(item, dict))

    # 状态元素角色帮助识别 FSM、counter 和输出寄存模式。
    list_text_parts.extend(str(item.get("role") or "") for item in list_state_elements if isinstance(item, dict))

    # 分解角色帮助识别 wrapper/top stitching 需求。
    list_text_parts.extend(str(item.get("role") or "") for item in list_decomposition if isinstance(item, dict))

    # 文本池统一小写，便于关键词匹配。
    str_text = " ".join(list_text_parts).lower()  # analysis 关键词池

    # analysis 匹配结果按端口、状态和分解信号的检查顺序累积。
    list_selected: list[str] = []  # analysis 规则命中的模板 id

    # ready 或 valid 端口名命中 AXIS ready/valid 切片。
    if any(
        "ready" in str(item.get("name", "")).lower() or "valid" in str(item.get("name", "")).lower()
        for item in list_ports
        if isinstance(item, dict)
    ):

        # ready/valid 端口暗示流式握手。
        list_selected.append("axis_ready_valid_slice")

    # fsm_state 角色命中 FSM 状态迁移模板。
    if any(item.get("role") == "fsm_state" for item in list_state_elements if isinstance(item, dict)):

        # FSM 状态元素需要状态迁移结构指导。
        list_selected.append("fsm_state_transition")

    # counter 角色和状态关键词同时出现时命中 bridge 模板。
    if any(item.get("role") == "counter" for item in list_state_elements if isinstance(item, dict)) and _has_any(
        str_text,
        ("state", "fsm", "phase"),
    ):

        # counter-state bridge 模板辅助计数器驱动状态切换。
        list_selected.append("counter_state_bridge")

    # 存在分解候选时需要 wrapper/top stitching 指导重组。
    if list_decomposition:

        # 分解候选意味着后续可能需要顶层拼接。
        list_selected.append("wrapper_top_stitching")

    # 输出角色状态元素命中输出寄存模板。
    if any("output" in str(item.get("role", "")).lower() for item in list_state_elements if isinstance(item, dict)):

        # 输出状态元素通常需要寄存输出约束。
        list_selected.append("phase_output_registering")

    # 返回 analysis 规则命中的模板 id。
    return list_selected

# 规格文本抽取器把多种字段合并为关键词池。
def _spec_text(spec: dict[str, Any]) -> str:
    """抽取规格中可用于模板关键词匹配的文本。

    参数:
        spec: 用户规格或规范化后的设计字典。

    返回:
        已小写化的关键词文本池。
    """

    # fragments 收集规格中适合关键词匹配的文本片段。
    list_fragments: list[str] = []  # 规格关键词片段

    # 顶层 name/description 直接进入文本池。
    for str_key in ("name", "description"):

        # 只收集字符串字段，避免结构字段被整体 repr 化。
        raw_field_value = spec.get(str_key)  # 顶层文本字段值

        # 字符串字段作为自然语言关键词来源。
        if isinstance(raw_field_value, str):

            # 追加顶层说明文本。
            list_fragments.append(raw_field_value)

    # behavior/constraints/notes 可能是字符串或信息字典。
    for str_key in ("behavior", "constraints", "notes"):

        # 语义列表条目可能是纯文本，也可能是 normalize 后的信息字典。
        for item in spec.get(str_key, []) or []:

            # 字符串条目直接加入文本池。
            if isinstance(item, str):

                # 保留用户原始行为或约束描述。
                list_fragments.append(item)

            # 字典条目通常来自 normalize 后的结构化需求。
            elif isinstance(item, dict):

                # 信息字典中的值都可能包含模板关键词。
                list_fragments.extend(str(raw_value) for raw_value in item.values())

    # interfaces 只有 dict 形态时才读取 ports。
    raw_interfaces = spec.get("interfaces")  # 原始接口描述对象

    # dict_interfaces 保证后续 ports 读取具备静态可推断类型。
    dict_interfaces: dict[str, Any] = raw_interfaces if isinstance(raw_interfaces, dict) else {}  # 接口描述对象

    # 端口名、方向、角色和位宽文本都可作为模板关键词。
    for port in dict_interfaces.get("ports", []) or []:

        # 只处理结构化端口对象。
        if isinstance(port, dict):

            # 端口字典的所有值都参与关键词匹配。
            list_fragments.extend(str(raw_value) for raw_value in port.values())

    # 返回小写文本，供 _has_any 做简单包含匹配。
    return " ".join(fragment.lower() for fragment in list_fragments)

# 关键词匹配器集中处理 any(token in text) 逻辑。
def _has_any(str_text: str, tuple_tokens: tuple[str, ...]) -> bool:
    """判断文本是否包含任一模板关键词。

    参数:
        str_text: 已归一化的小写文本池。
        tuple_tokens: 需要匹配的关键词集合。

    返回:
        任一关键词存在于文本池中时返回 True。
    """

    # token 集合较小，直接线性扫描保持可读。
    return any(str_token in str_text for str_token in tuple_tokens)
