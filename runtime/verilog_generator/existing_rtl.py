"""把现有 Verilog RTL 解析成稳定的结构化分析合同。"""

# 延后解析注解，避免类型提示在导入时触发前向引用问题。
from __future__ import annotations

# 基础库导入用于正则解析、JSON 序列化和路径读写。
import json
import re
from pathlib import Path
from typing import Any

# 工作区工具负责把结构化结果稳定写入 JSON 文件。
from .workspace import write_json

# 提取模块头里的模块名、参数列表和端口列表。
MODULE_RE = re.compile(  # 模块头正则同时负责捕获模块名、参数段和端口段
    r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\((?P<params>.*?)\))?\s*\((?P<ports>.*?)\)\s*;",  # 模块头需要同时抓取模块名、参数区和端口区
    re.DOTALL,  # 允许参数列表和端口列表跨多行匹配
)  # 模块声明头部匹配正则

# 提取 reg/wire/integer 声明及其位宽片段。
DECL_RE = re.compile(r"\b(reg|wire|integer)\b\s*(\[[^\]]+\]\s*)?([^;]+);")  # 提取普通信号声明里的类型、位宽和名称片段

# 提取端口方向、位宽和名称片段。
PORT_DECL_RE = re.compile(r"\b(input|output|inout)\b\s*(?:reg\s+|wire\s+)?(\[[^\]]+\]\s*)?([^;,\)]+)")  # 从模块头端口片段里提取方向、位宽和端口名

# 定位 always 敏感列表，便于后续切块分析。
ALWAYS_RE = re.compile(r"(?m)^\s*always\s*@\s*\((.*?)\)")  # 提取 always 敏感列表正文

# 提取 assign 语句的左值信号名。
ASSIGN_RE = re.compile(r"(?m)^\s*assign\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")  # 提取 assign 连续赋值的左值信号名

# 提取过程赋值语句的左值信号名。
ASSIGNED_SIGNAL_RE = re.compile(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:<=|=)")  # 提取过程赋值语句左值信号名

# 提取子模块例化时的模块类型和实例名。
INSTANTIATION_RE = re.compile(  # 子模块例化正则负责识别被例化模块类型与实例名
    r"(?m)^\s*(?!module\b|endmodule\b|if\b|for\b|while\b|case\b|assign\b|always\b)"  # 先排除模块定义与流程控制等非例化语句
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\(.*?\))?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",  # 再捕获例化模块类型和实例名
    re.DOTALL,  # 允许参数覆盖和端口映射跨行展开
)  # 子模块例化匹配正则

# 提取源码里可能代表信号名的标识符。
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")  # 扫描源码中可能代表信号名的普通标识符

# 这些 Verilog 关键字不能被当作普通信号名回收。
KEYWORD_BLACKLIST = {
    "if",  # if 属于流程控制关键字，不能被视作信号名
    "else",  # else 只表示上一条件失败后的分支入口，不代表任何硬件信号
    "case",  # case 属于分支关键字，不能被视作信号名
    "endcase",  # endcase 属于分支闭合关键字，不能被视作信号名
    "begin",  # begin 属于块边界关键字，不能被视作信号名
    "end",  # end 只是 begin 对应的块闭合标记，不应进入信号引用集合
    "assign",  # assign 属于连续赋值关键字，不能被视作信号名
    "always",  # always 属于过程块关键字，不能被视作信号名
    "module",  # module 属于模块声明关键字，不能被视作信号名
    "endmodule",  # endmodule 属于模块闭合关键字，不能被视作信号名
    "posedge",  # posedge 属于边沿描述关键字，不能被视作信号名
    "negedge",  # negedge 仅描述下降沿触发条件，不代表可被赋值或引用的信号
    "or",  # or 属于敏感列表连接关键字，不能被视作信号名
}  # 需要从信号候选里排除的 Verilog 关键字集合

# 分析现有 RTL，输出单模块分析和工程级拓扑摘要。
def analyze_existing_rtl(
    source_paths: list[Path],
    *,
    spec_text: str | None = None,
    module_name: str | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """分析一组现有 RTL 源文件并生成结构化分析结果。

    参数:
        source_paths: 需要参与工程级拓扑分析的 RTL 源文件路径列表。
        spec_text: 可选的规格说明文本，用于补充特征映射语义。
        module_name: 可选的目标模块名；提供时优先锁定该模块。
        out_dir: 可选的输出目录；提供时额外写出 JSON 和 Markdown 工件。

    返回:
        包含 `analysis` 与 `project_analysis` 的结果字典；若提供 `out_dir`，还会返回各工件路径。

    异常:
        ValueError: 当全部输入源码都找不到合法 Verilog 模块声明时抛出。
    """

    # 先把所有输入 RTL 源文件读取成按绝对路径索引的源码字典。
    dict_source_texts = {path.resolve(): path.read_text(encoding="utf-8", errors="ignore") for path in source_paths}  # 输入 RTL 绝对路径到源码正文的映射

    # 工程级分析先决定候选顶层和模块拓扑。
    dict_project_analysis = _project_analysis(dict_source_texts, module_name=module_name)  # 多文件工程的模块拓扑与顶层候选摘要

    # 再根据工程级结论选出要做深入解析的单个模块。
    dict_selected_module = _select_module(dict_source_texts, dict_project_analysis["selected_top_module"])  # 本轮深入分析的目标模块记录

    # 没有找到合法模块时，必须阻断后续分析并给出明确错误。
    if dict_selected_module is None:

        # 用带规则前缀的错误文本提示调用方输入源码里缺少模块声明。
        raise ValueError("> ERR: [Python] No Verilog module declaration was found in the provided source files.")

    # 为选中的模块构建稳定的结构化分析载荷。
    dict_analysis = _build_analysis_payload(  # 选中模块的结构化分析载荷入口
        dict_selected_module["path"],  # 选中模块所在源文件路径
        dict_selected_module["name"],  # 选中模块的模块名
        dict_selected_module["text"],  # 选中模块对应的原始源码正文
        spec_text=spec_text,  # 透传给模块分析载荷的规格说明文本
    )  # 选中模块的结构化分析结果

    # 提供输出目录时，同时把分析结果和解释文档落盘。
    if out_dir is not None:

        # 先确保输出目录存在，避免后续写文件失败。
        out_dir.mkdir(parents=True, exist_ok=True)

        # 主分析 JSON 保存模块级行为与接口摘要。
        path_analysis_json = write_json(out_dir / "rtl_analysis.json", dict_analysis)  # 模块级分析 JSON 输出路径

        # 工程分析 JSON 保存顶层候选与模块依赖关系。
        path_project_analysis_json = write_json(out_dir / "project_analysis.json", dict_project_analysis)  # 工程级分析 JSON 输出路径

        # 设计解释 Markdown 用于给后续审阅或提示词拼装提供自然语言摘要。
        path_design_explanation = out_dir / "design_explanation.md"  # 设计解释 Markdown 的目标文件路径

        # 把模块分析和工程拓扑拼成可读的说明文档。
        path_design_explanation.write_text(  # 设计解释 Markdown 写盘动作
            _design_explanation_markdown(dict_analysis, dict_project_analysis),
            encoding="utf-8",
        )

        # 写盘模式需要把内存结果和工件路径一起返回。
        return {
            "analysis": dict_analysis,
            "analysis_path": path_analysis_json,
            "project_analysis": dict_project_analysis,
            "project_analysis_path": path_project_analysis_json,
            "design_explanation_path": path_design_explanation,
        }

    # 纯内存模式只返回两个核心分析结果。
    return {"analysis": dict_analysis, "project_analysis": dict_project_analysis}

# 把规格来源统一转换为可直接拼进提示词或报告的文本。
def load_spec_text(spec_source: str | Path | dict[str, Any] | None) -> str | None:
    """把规格来源归一化成 UTF-8 文本。

    参数:
        spec_source: 规格来源；可以是路径、字典载荷，或 `None`。

    返回:
        规格文本；当调用方未提供规格来源时返回 `None`。
    """

    # 没有规格输入时，保持空值让上游走纯结构推断路径。
    if spec_source is None:

        # 空规格来源直接返回 None。
        return None

    # 结构化规格字典需要先序列化成稳定 JSON 文本。
    if isinstance(spec_source, dict):

        # 序列化时保留中文，方便后续提示词直接复用。
        return json.dumps(spec_source, indent=2, ensure_ascii=False)

    # 文件型规格来源统一转成 Path 后按 UTF-8 容错读取。
    path_spec_source = Path(spec_source)  # 规格源文件的标准化路径对象

    # 返回规格文件正文，供特征映射和提示词拼装复用。
    return path_spec_source.read_text(encoding="utf-8", errors="ignore")

# 基于现有分析结果生成 refine_existing 的变换计划骨架。
def build_transform_plan(
    analysis: dict[str, Any],
    *,
    transform_goal: str,
    expected_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """为 refine_existing 工作流构建稳定的计划态合同。

    参数:
        analysis: `analyze_existing_rtl` 产出的模块级分析结果。
        transform_goal: 本轮变换目标，例如 `merge_assist` 或 `optimize_assist`。
        expected_outputs: 调用方希望后续流程生成的目标工件描述列表。

    返回:
        包含语义不变量、期望工件和验证策略的计划字典。
    """

    # 模块名会被写入多条不变量说明，先统一转成字符串。
    str_module_name = str(analysis["module_info"]["name"])  # 当前变换计划绑定的模块名

    # 输出端口列表定义了哪些可观察行为必须在变换后保持稳定。
    list_output_names = [  # 当前模块全部对外可观察输出端口名称
        item["name"]  # 单个 public output 端口名称
        for item in analysis.get("ports", [])  # 遍历模块分析里登记的全部端口
        if item.get("direction") == "output"  # 只保留 direction 为 output 的端口
    ]  # 当前模块全部 public output 名称

    # 状态机状态和计数器信号需要额外纳入时序一致性保护。
    list_state_signal_names = [  # 需要额外保护推进语义的状态类信号名称
        item["name"]  # 单个状态类信号名称
        for item in analysis.get("state_elements", [])  # 遍历状态元素分析记录
        if item.get("role") in {"fsm_state", "counter"}  # 只保留状态机状态或计数器角色
    ]  # 需要保留推进语义的状态类信号名称

    # 计划默认带上端口合同和 reset 行为这两个基础不变量。
    list_semantic_invariants = [
        {
            "id": "port_contract",  # 保护 public port 宽度和方向不被改写
            "text": (  # 端口合同不变量的自然语言说明
                f"Preserve the public port contract of `{str_module_name}` exactly, "
                "including widths and directions."
            ),
        },
        {
            "id": "reset_behavior",  # 保护 reset 触发的初始化行为
            "text": "Preserve reset-driven initialization behavior for outputs and sequential state.",  # reset 行为不变量的自然语言说明
        },
    ]  # refine_existing 默认启用的语义不变量列表

    # 存在对外输出时，额外约束这些可观察端口的行为稳定性。
    if list_output_names:

        # 可观察输出不变量明确告诉后续流程哪些端口不能被悄悄改语义。
        list_semantic_invariants.append(
            {
                "id": "observable_outputs",  # 保护全部 output 端口的可观察行为
                "text": "Keep these observable outputs stable and reviewable: " + ", ".join(list_output_names) + ".",  # 输出端口稳定性不变量的自然语言说明
            }
        )

    # 存在状态推进信号时，额外约束状态/计数器的时序推进语义。
    if list_state_signal_names:

        # 状态推进不变量保护 FSM 和计数器在变换后仍保持同类时序行为。
        list_semantic_invariants.append(
            {
                "id": "state_progression",  # 保护状态机与计数器的推进轨迹
                "text": "Preserve state/counter progression for " + ", ".join(list_state_signal_names) + ".",  # 状态推进不变量的自然语言说明
            }
        )

    # 返回 refine_existing 计划态合同，供后续 wrapper、validation 与等价性流程复用。
    return {
        "version": 1,  # 变换计划合同版本号
        "mode": "refine_existing",  # 当前合同对应 existing RTL 细化模式
        "source_artifacts": [str(item) for item in analysis["provenance"]["source_paths"]],  # 变换计划依赖的源文件工件路径
        "transform_goal": transform_goal,  # 调用方要求的本轮变换目标
        "semantic_invariants": list_semantic_invariants,  # 变换过程中必须保持的语义不变量清单
        "expected_outputs": expected_outputs,  # 调用方声明的目标输出工件描述
        "verification_strategy": {
            "interface_consistency": True,  # 优先检查接口合同是否保持一致
            "checkpoint_consistency": True,  # 优先检查语义检查点是否保持一致
            "testbench_consistency": True,  # 优先检查测试平台契约是否保持一致
            "preferred_simulator_order": ["xsim", "vcs_verdi", "iverilog"],  # 推荐的仿真器尝试顺序
            "optional_qor_tool": "yosys",  # 可选的 QoR 对比工具
        },
    }

# 组装单模块分析载荷，供 refine、verify 与解释文档复用。
def _build_analysis_payload(source_path: Path, module_name: str, text: str, *, spec_text: str | None) -> dict[str, Any]:
    """为选中模块构建稳定的结构化分析载荷。

    参数:
        source_path: 选中模块所在源文件路径。
        module_name: 需要解析的目标模块名。
        text: 目标模块对应的原始 RTL 文本。
        spec_text: 可选规格说明文本，用于增强特征映射语义。

    返回:
        可直接写入 JSON 的模块分析结果字典。
    """

    # 先去掉源码注释，避免端口和声明正则被注释文本污染。
    str_stripped = _strip_comments(text)  # 去注释后的 RTL 正文

    # 端口提取结果定义了接口合同和时钟复位角色。
    list_ports = _extract_ports(module_name, str_stripped)  # 模块端口清单

    # 声明提取结果提供寄存器、连线和整数信号的基础元数据。
    list_declarations = _extract_declarations(str_stripped)  # 普通信号声明清单

    # always 块分析结果后续会驱动状态、特征和分解候选推断。
    list_always_blocks = _extract_always_blocks(text)  # 模块里的 always 过程块摘要

    # 时钟端口集合用于 clock_reset_info 合同。
    list_clock_signals = [  # 模块输入输出里识别出的时钟信号名称
        item["name"]  # 单个时钟端口名称
        for item in list_ports  # 遍历全部端口分析记录
        if item.get("role") == "clock"  # 只保留角色被识别为 clock 的端口
    ]  # 时钟信号名称列表

    # 复位端口集合用于 clock_reset_info 合同。
    list_reset_signals = [  # 模块输入输出里识别出的复位信号名称
        item["name"]  # 单个复位端口名称
        for item in list_ports  # 依次检查结构分析阶段识别出的所有端口
        if item.get("role") == "reset"  # 过滤出会影响初始化语义的复位端口
    ]  # 复位信号名称列表

    # 时序赋值命中的声明会进一步升级为状态元素。
    list_state_elements = _extract_state_elements(list_declarations, list_always_blocks)  # 需要被视作状态承载体的声明集合

    # 结构特征候选是后续行为映射和验证目标生成的基础。
    list_feature_candidates = _feature_candidates(  # 从端口、状态和过程块推断出的结构特征候选
        module_name,  # 当前分析目标模块名
        list_ports,  # 端口分析结果清单
        list_state_elements,  # 状态承载信号清单
        list_always_blocks,  # always 过程块结构摘要
    )

    # 特征映射把结构候选与规格描述拼成可验证语义。
    list_feature_mappings = _feature_mappings(  # 结构候选与规格说明融合后的行为映射清单
        ports=list_ports,  # 提供接口角色和方向信息的端口集合
        state_elements=list_state_elements,  # 提供内部状态语义的声明集合
        always_blocks=list_always_blocks,  # 提供期望输出推断依据的过程块摘要
        feature_candidates=list_feature_candidates,  # 提供默认行为候选的结构化条目
        spec_text=spec_text,  # 提供外部规格语义补充的原始文本
    )

    # 验证目标把特征映射转成后续 verify 流程更容易消费的检查清单。
    list_verification_targets = _verification_targets(  # 面向验证阶段的检查目标集合
        module_name,  # 用于生成检查描述的目标模块名
        list_ports,  # 用于补齐公开观测点的端口清单
        list_state_elements,  # 用于识别 counter 等内部状态的元素清单
        list_feature_mappings,  # 用于派生检查项的行为映射结果
    )

    # 分解候选用来提示潜在的块级拆分边界。
    list_decomposition_candidates = _decomposition_candidates(  # 基于过程块推断的模块拆分候选
        list_always_blocks,  # 作为候选来源的过程块摘要
        list_ports,  # 作为公开边界参考的端口清单
        list_state_elements,  # 作为内部边界参考的状态元素清单
    )

    # 返回模块分析 JSON 所需的完整结构化载荷。
    return {
        "version": 1,  # 模块分析合同版本号
        "mode": "analyze_existing",  # 当前载荷对应 existing RTL 分析模式
        "module_info": {
            "name": module_name,  # 返回载荷绑定的目标模块名
            "parameter_count": len(_extract_parameters(module_name, str_stripped)),  # 目标模块参数个数
            "port_count": len(list_ports),  # 目标模块端口个数
        },
        "clock_reset_info": {
            "clock_signals": list_clock_signals,  # 识别出的时钟信号名称列表
            "reset_signals": list_reset_signals,  # 识别出的复位信号名称列表
        },
        "ports": list_ports,  # 模块端口元数据列表
        "state_elements": list_state_elements,  # 模块状态元素列表
        "always_blocks": list_always_blocks,  # 模块过程块摘要列表
        "feature_candidates": list_feature_candidates,  # 结构特征候选列表
        "feature_mappings": list_feature_mappings,  # 行为特征映射列表
        "verification_targets": list_verification_targets,  # 后续验证目标列表
        "decomposition_candidates": list_decomposition_candidates,  # 建议拆分边界列表
        "provenance": {
            "source_paths": [str(source_path)],  # 当前分析结果引用的源文件路径列表
            "spec_source_provided": bool(spec_text),  # 是否提供了外部规格说明
            "analyzer": "existing_rtl",  # 生成本载荷的分析器名称
        },
    }

# 在多文件输入里挑出最适合继续深入分析的目标模块。
def _select_module(texts: dict[Path, str], module_name: str | None) -> dict[str, Any] | None:
    """从输入源码集合里选出需要深入解析的目标模块。

    参数:
        texts: 源文件绝对路径到源码正文的映射。
        module_name: 调用方显式指定的目标模块名；缺省时自动选择。

    返回:
        命中模块时返回包含路径、模块名和源码正文的记录；否则返回 `None`。
    """

    # 逐个源文件扫描模块声明，优先返回第一个满足过滤条件的模块。
    for path_source, str_source_text in texts.items():

        # 单个源文件里可能定义多个模块，需要逐个匹配模块声明。
        for match_module in MODULE_RE.finditer(_strip_comments(str_source_text)):

            # 提取当前命中的模块名，供过滤和返回记录复用。
            str_detected_name = match_module.group(1)  # 当前匹配到的模块名

            # 调用方显式锁定模块名时，只接受完全匹配的模块。
            if module_name and str_detected_name != module_name:

                # 当前模块名不是调用方要求的目标模块，继续查找。
                continue

            # 未指定模块名时，默认跳过 *_tb 形式的测试平台模块。
            if not module_name and str_detected_name.lower().endswith("_tb"):

                # 自动选择主模块时不把 testbench 当作默认分析目标。
                continue

            # 命中可接受模块后，立即返回后续分析需要的最小记录。
            return {
                "path": path_source,  # 目标模块所在源文件路径
                "name": str_detected_name,  # 目标模块名
                "text": str_source_text,  # 目标模块对应的源文件正文
            }

    # 全部源码都没有命中可接受模块时，交给上游决定是否报错。
    return None

# 汇总多文件 RTL 工程的模块拓扑、例化边和顶层候选。
def _project_analysis(texts: dict[Path, str], *, module_name: str | None = None) -> dict[str, Any]:
    """分析多文件 RTL 工程的模块清单和顶层候选。

    参数:
        texts: 源文件绝对路径到源码正文的映射。
        module_name: 调用方显式指定的目标顶层模块名；缺省时自动排序选择。

    返回:
        包含模块摘要、例化边和顶层候选的工程级分析字典。

    异常:
        ValueError: 当全部输入源码都找不到合法 Verilog 模块声明时抛出。
    """

    # 先收集全部非 testbench 模块，为后续拓扑推断打基础。
    list_modules: list[dict[str, Any]] = []  # 工程里发现的模块摘要记录

    # 模块名集合用于后续识别合法子模块例化边。
    set_module_names: set[str] = set()  # 工程里出现过的非 testbench 模块名集合

    # 逐个文件扫描模块声明，并保留去注释后的文本供后续复用。
    for path_source, str_source_text in texts.items():

        # 去掉注释后再做模块扫描，避免注释里的 module 文本误伤匹配。
        str_stripped = _strip_comments(str_source_text)  # 当前源文件去注释后的 RTL 文本

        # 单个文件可能包含多个模块定义，需要逐个登记。
        for match_module in MODULE_RE.finditer(str_stripped):

            # 提取当前模块名，作为拓扑节点的稳定主键。
            str_module_name = match_module.group(1)  # 当前扫描命中的模块名

            # testbench 模块不参与主工程拓扑和顶层推断。
            if str_module_name.lower().endswith("_tb"):

                # 跳过 testbench，避免把测试平台混入主设计拓扑。
                continue

            # 先登记工程中出现过的模块名，供后续例化过滤使用。
            set_module_names.add(str_module_name)  # 当前工程已发现的模块名集合

            # 每个模块保留原始文本和去注释文本，减少后续重复处理。
            list_modules.append(
                {
                    "name": str_module_name,  # 模块名
                    "path": str(path_source),  # 模块所在源文件路径
                    "text": str_source_text,  # 模块所在源文件原始正文
                    "stripped": str_stripped,  # 模块所在源文件去注释后的正文
                }
            )

    # 工程里没有任何合法模块时，后续拓扑分析无法继续。
    if not list_modules:

        # 用固定前缀的错误消息阻断调用方继续走错误输入。
        raise ValueError("> ERR: [Python] No Verilog module declaration was found in the provided source files.")

    # 例化边记录模块之间的父子依赖关系。
    list_edges: list[dict[str, str]] = []  # 父模块到子模块的例化边列表

    # 任何被别人例化过的模块都不能作为默认顶层候选。
    set_instantiated_children: set[str] = set()  # 被其他模块例化过的子模块名集合

    # 逐个检查模块正文，把真实子模块例化转成父子拓扑边。
    for dict_module in list_modules:

        # 当前模块内部可能例化多个子模块，需要逐个登记边关系。
        for str_child_name in _instantiated_modules(dict_module["stripped"], set_module_names):

            # 记录一条从父模块到子模块的例化边。
            list_edges.append(
                {
                    "parent": dict_module["name"],  # 例化发起方模块名
                    "child": str_child_name,  # 被例化子模块名
                }
            )

            # 被例化模块会从默认顶层候选里剔除。
            set_instantiated_children.add(str_child_name)  # 已经作为子模块出现过的模块名集合

    # 没有被任何模块例化的节点，是默认顶层候选的第一批来源。
    list_top_candidates = [  # 默认顶层候选模块名列表
        dict_module["name"]  # 单个默认顶层候选模块名
        for dict_module in list_modules  # 遍历工程里登记的全部模块
        if dict_module["name"] not in set_instantiated_children  # 只保留未出现在 child 侧的模块
    ]  # 初筛后仍可能成为工程顶层的模块名列表

    # 调用方显式指定模块名时，直接把它当作选中的顶层。
    if module_name:

        # 显式指定的模块名拥有最高优先级。
        str_selected_top = module_name  # 最终选中的顶层模块名

    # 否则按扇出、端口规模和字典序挑一个默认顶层。
    else:

        # 没有默认顶层候选时，退回全部模块名参与排序。
        list_ranked_candidates = sorted(  # 自动推断顶层时使用的排序后候选清单
            list_top_candidates or [dict_module["name"] for dict_module in list_modules],  # 候选模块名列表
            key=lambda name: (  # 根据扇出、端口规模和名字稳定排序
                -sum(1 for dict_edge in list_edges if dict_edge["parent"] == name),  # 父边越多，越像工程入口
                -next(  # 端口数量越多，越倾向于成为工程入口
                    (
                        len(_extract_ports(name, dict_module["stripped"]))  # 当前候选模块的端口数量
                        for dict_module in list_modules  # 遍历全部模块摘要
                        if dict_module["name"] == name  # 找到名字匹配的模块记录
                    ),
                    0,  # 找不到匹配模块时退回零端口
                ),
                name,  # 最后用字典序保证排序稳定
            ),
        )  # 自动排序后的顶层候选列表

        # 排序后的第一个候选即为默认选中的顶层。
        str_selected_top = list_ranked_candidates[0]  # 自动推断得到的顶层模块名

    # 返回工程级拓扑和顶层候选摘要。
    return {
        "version": 1,  # 工程分析合同版本号
        "module_count": len(list_modules),  # 工程里合法模块总数
        "selected_top_module": str_selected_top,  # 当前工程最终认定的顶层模块名
        "top_candidates": sorted(list_top_candidates or [dict_module["name"] for dict_module in list_modules]),  # 候选顶层模块名的稳定排序结果
        "modules": [
            {
                "name": dict_module["name"],  # 当前模块记录的逻辑名
                "path": dict_module["path"],  # 当前模块正文所在文件路径
                "port_count": len(_extract_ports(dict_module["name"], dict_module["stripped"])),  # 模块端口数量
            }
            for dict_module in list_modules  # 遍历全部模块摘要并生成对外返回视图
        ],
        "instantiation_edges": list_edges,  # 模块间例化边列表
    }

# 扫描单个去注释后的模块文本，找出合法子模块例化名。
def _instantiated_modules(stripped: str, module_names: set[str]) -> list[str]:
    """提取单个模块正文中出现的合法子模块例化名。

    参数:
        stripped: 去掉注释后的模块源码文本。
        module_names: 当前工程里已知的合法模块名集合。

    返回:
        当前模块正文中例化过的子模块名列表，保持首次出现顺序。
    """

    # 例化结果需要保持首次出现顺序，方便后续做稳定拓扑输出。
    list_instantiated: list[str] = []  # 当前模块正文里识别出的子模块例化名列表

    # 扫描全部例化语句，并只保留工程里已知模块名。
    for match_instantiation in INSTANTIATION_RE.finditer(stripped):

        # group(1) 对应被例化的模块类型名。
        str_child_name = match_instantiation.group(1)  # 当前例化语句命中的子模块名

        # 只有工程内已知模块且尚未登记过的子模块才写入结果。
        if str_child_name in module_names and str_child_name not in list_instantiated:

            # 首次命中的合法子模块名按出现顺序登记。
            list_instantiated.append(str_child_name)

    # 返回当前模块正文里识别出的全部子模块例化名。
    return list_instantiated

# 把结构化分析结果渲染成便于人工审阅的 Markdown 设计说明。
def _design_explanation_markdown(analysis: dict[str, Any], project_analysis: dict[str, Any]) -> str:
    """把模块分析和工程拓扑整理成人可读 Markdown 说明。

    参数:
        analysis: 单模块结构化分析结果字典。
        project_analysis: 工程级拓扑与顶层候选摘要字典。

    返回:
        适合写入 `design_explanation.md` 的 Markdown 正文字符串。
    """

    # 设计说明标题需要稳定引用当前模块名。
    str_module_name = str(analysis["module_info"]["name"])  # 设计说明所绑定的模块名

    # 特征映射列表会被渲染成逐条可读的行为摘要。
    list_feature_lines = [  # “Feature Mapping Summary” 小节的 Markdown 行列表
        f"- `{item['name']}`: {item.get('description', '').strip() or 'derived from ports, state, and always blocks.'}"  # 单条行为特征摘要
        for item in analysis.get("feature_mappings", [])  # 遍历全部特征映射记录
    ]

    # 验证目标列表会被渲染成逐条检查说明。
    list_verification_lines = [  # “Verification Targets” 小节的逐条检查说明
        f"- `{item['check_id']}`: {item.get('description', '').strip() or 'analysis-derived verification target.'}"  # 单条验证检查摘要
        for item in analysis.get("verification_targets", [])  # 遍历全部验证目标记录
    ]

    # 分解候选列表会被渲染成按源码区间说明的拆分建议。
    list_decomposition_lines = [  # “Decomposition Candidates” 小节的拆分建议条目
        f"- `{item['module_name']}` lines {item['line_range'][0]}-{item['line_range'][1]}: {item['role']}"  # 单条拆分建议摘要
        for item in analysis.get("decomposition_candidates", [])  # 遍历全部分解候选记录
    ]

    # 接口摘要单独整理成列表，避免主模板里嵌套过长推导。
    list_interface_lines = [  # “Interface Summary” 小节的端口摘要条目
        (
            f"- `{dict_port['direction']} {dict_port['name']}` "
            f"width={int(dict_port.get('width') or 1)} "
            f"role={dict_port.get('role', 'data')}"
        )  # 单个接口端口的摘要行
        for dict_port in analysis.get("ports", [])  # 遍历全部端口记录
    ]

    # 最终返回整份 Markdown 正文，供 write_text 直接写盘。
    return (
        "\n".join(
            [
                f"# Design Explanation: {str_module_name}",  # 标题行
                "",  # 标题与工程拓扑之间的空行
                "## Project Topology",  # 工程拓扑小节标题
                f"- Selected top module: `{project_analysis['selected_top_module']}`",  # 当前工程选中的顶层模块
                f"- Module count: {project_analysis['module_count']}",  # 当前工程模块总数
                "",  # 工程拓扑与接口摘要之间的空行
                "## Interface Summary",  # 接口摘要小节标题
                *(list_interface_lines or ["- No ports were inferred from the selected module."]),  # 端口摘要列表或缺省提示
                "",  # 接口摘要与特征映射之间的空行
                "## Feature Mapping",  # 特征映射小节标题
                *(list_feature_lines or ["- No explicit feature mapping was inferred."]),  # 特征映射列表或缺省提示
                "",  # 特征映射与验证目标之间的空行
                "## Verification Targets",  # 验证目标小节标题
                *(list_verification_lines or ["- No verification targets were inferred."]),  # 验证目标列表或缺省提示
                "",  # 验证目标与分解候选之间的空行
                "## Decomposition Candidates",  # 分解候选小节标题
                *(list_decomposition_lines or ["- No decomposition candidates were inferred."]),  # 分解候选列表或缺省提示
                "",  # 末尾保留一个空行，方便下游直接拼接
            ]
        )
        + "\n"
    )

# 提取目标模块参数名，供 parameter_count 和后续合同摘要复用。
def _extract_parameters(module_name: str, text: str) -> list[str]:
    """提取目标模块声明里的参数名列表。

    参数:
        module_name: 需要定位的目标模块名。
        text: 包含目标模块声明的 RTL 文本。

    返回:
        目标模块参数名列表；没有参数时返回空列表。
    """

    # 先锁定目标模块的正则匹配结果，避免重复扫描模块头。
    match_module = _module_match(module_name, text)  # 目标模块声明的正则匹配结果

    # 参数片段为空时，说明当前模块没有 parameter/localparam 头部。
    str_params = (match_module.group("params") or "").strip()  # 模块头参数片段的原始文本

    # 无参数模块直接返回空列表，避免后续做无意义的 token 提取。
    if not str_params:

        # 当前模块声明里没有参数片段可供提取。
        return []

    # 返回参数标识符列表，并过滤 parameter/localparam 这类关键词。
    return [  # 模块参数名列表
        item  # 单个参数标识符
        for item in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", str_params)  # 扫描参数片段里的全部标识符
        if item not in {"parameter", "localparam"}  # 过滤声明关键字本身
    ]

# 提取目标模块端口信息，供接口合同和角色推断复用。
def _extract_ports(module_name: str, text: str) -> list[dict[str, Any]]:
    """提取目标模块端口的方向、位宽和角色信息。

    参数:
        module_name: 需要定位的目标模块名。
        text: 包含目标模块声明的 RTL 文本。

    返回:
        目标模块端口元数据列表。
    """

    # 先拿到 module 头部匹配结果，确保后续字段都来自同一个声明。
    match_module = _module_match(module_name, text)  # 当前目标模块头部的正则匹配结果

    # 模块头里的 ports 分组保存了原始端口片段。
    str_header = match_module.group("ports")  # 模块头端口区域的原始文本

    # 使用字典去重，保证重复声明时以后写入的记录覆盖前项。
    dict_ports: dict[str, dict[str, Any]] = {}  # 端口名到端口元数据的映射

    # 逐个端口声明片段解析方向、位宽和名字列表。
    for match_port in PORT_DECL_RE.finditer(str_header):

        # group(1) 保存当前端口声明的方向关键字。
        str_direction = match_port.group(1)  # 当前端口声明的方向

        # group(2) 保存当前端口声明的位宽区间片段。
        int_width = _width_from_range(match_port.group(2))  # 当前端口声明推断出的位宽

        # group(3) 可能包含逗号分隔的多个端口名，需要先切开并过滤空项。
        list_names = [  # 当前端口声明片段拆出的名称列表
            item  # 单个端口名
            for item in _split_names(match_port.group(3))  # 拆分当前端口声明里的名称片段
            if item  # 过滤掉空字符串名称
        ]  # 这条声明语句里实际解析出的端口名列表

        # 一个声明片段里可能包含多个同方向端口，需要逐个写入元数据。
        for str_name in list_names:

            # 为当前端口生成最终元数据记录。
            dict_ports[str_name] = {
                "name": str_name,  # 当前端口名
                "direction": str_direction,  # 当前端口方向
                "width": int_width,  # 当前端口位宽
                "role": _port_role(str_name),  # 当前端口推断出的角色
            }

    # 返回去重后的端口元数据列表。
    return list(dict_ports.values())

# 提取普通 reg/wire/integer 声明，供状态元素和角色推断复用。
def _extract_declarations(text: str) -> list[dict[str, Any]]:
    """提取 RTL 正文里的普通信号声明列表。

    参数:
        text: 去注释后的 RTL 文本。

    返回:
        普通信号声明元数据列表。
    """

    # 声明列表会被后续状态元素和角色推断流程复用。
    list_declarations: list[dict[str, Any]] = []  # 普通信号声明元数据列表

    # 逐个声明片段解析类型、位宽和具体信号名。
    for match_declaration in DECL_RE.finditer(text):

        # group(1) 保存声明类型，例如 reg、wire 或 integer。
        str_kind = match_declaration.group(1)  # 当前声明片段的类型关键字

        # group(2) 保存位宽片段，统一交给宽度解析函数处理。
        int_width = _width_from_range(match_declaration.group(2))  # 当前声明片段推断出的位宽

        # group(3) 可能包含多个逗号分隔的声明名，需要逐个处理。
        for str_name in _split_names(match_declaration.group(3)):

            # 空名称或关键字误匹配都不能进入最终声明列表。
            if not str_name or str_name in KEYWORD_BLACKLIST:

                # 当前拆分结果不是合法信号名，直接跳过。
                continue

            # 把当前合法声明写入输出列表。
            list_declarations.append(
                {
                    "name": str_name,  # 当前声明信号名
                    "kind": str_kind,  # 当前声明类型
                    "width": int_width,  # 当前声明位宽
                    "role": _signal_role(str_name),  # 当前声明推断出的信号角色
                }
            )

    # 返回全部普通信号声明元数据。
    return list_declarations

# 提取每个 always 过程块的边界、敏感列表和读写信号摘要。
def _extract_always_blocks(text: str) -> list[dict[str, Any]]:
    """从模块正文里抽取 always 过程块的结构摘要。

    参数:
        text: 去注释后的模块源码文本。

    返回:
        每个 always 过程块的类型、源码区间、读写信号和角色列表。
    """

    # 汇总结果按源码出现顺序保存，供后续状态/功能分析复用。
    list_blocks: list[dict[str, Any]] = []  # 模块中所有 always 过程块的结构摘要列表

    # 先把全部 always 起点找出来，便于按相邻起点切分正文。
    list_matches = list(ALWAYS_RE.finditer(text))  # 模块正文里全部 always 声明起点

    # 按命中顺序逐个切分出过程块正文。
    for index_block, match_always in enumerate(list_matches):

        # 当前 always 关键字在全文里的起始字符位置。
        int_start = match_always.start()  # 当前过程块正文的起始偏移

        # 下一处 always 起点或 endmodule 位置作为本块的结束边界。
        int_end = (
            list_matches[index_block + 1].start()  # 用下一处 always 作为当前块终点
            if index_block + 1 < len(list_matches)  # 还有后续 always 块时使用下一块起点
            else text.rfind("endmodule")  # 最后一块退回到 endmodule 位置
        )  # 当前过程块正文的结束偏移

        # 找不到 endmodule 时退回全文末尾，避免切分失败。
        if int_end == -1:

            # 使用全文长度补齐边界。
            int_end = len(text)  # 无 endmodule 命中时的兜底结束偏移

        # 截取当前 always 过程块的源码正文。
        str_block_text = text[int_start:int_end].strip()  # 当前过程块对应的 RTL 片段

        # 敏感列表文本会用于区分时序块与组合块。
        str_sensitivity = match_always.group(1).strip()  # 当前过程块的敏感列表字符串

        # 赋值目标信号需要保持首次出现顺序。
        list_assigned_signals: list[str] = []  # 当前过程块写入过的信号名列表

        # 扫描块内赋值语句，提取写信号集合。
        for match_assignment in ASSIGNED_SIGNAL_RE.finditer(str_block_text):

            # group(1) 对应赋值语句左侧信号名。
            str_signal_name = match_assignment.group(1)  # 当前赋值命中的目标信号名

            # 只保留非关键字且尚未登记过的目标信号。
            if (
                str_signal_name not in list_assigned_signals
                and str_signal_name not in KEYWORD_BLACKLIST
            ):

                # 按首次出现顺序登记写信号。
                list_assigned_signals.append(str_signal_name)

        # 读信号同样保持首次出现顺序，便于后续生成说明。
        list_referenced_signals: list[str] = []  # 当前过程块引用过的非关键字信号名列表

        # 扫描块内所有标识符，收集读依赖信号。
        for str_token in IDENT_RE.findall(str_block_text):

            # 已知关键字和已登记写信号不再重复作为读依赖输出。
            if str_token in KEYWORD_BLACKLIST or str_token in list_assigned_signals:

                # 跳过不需要写入 referenced 列表的标识符。
                continue

            # 尚未收录的普通标识符按首次出现顺序写入。
            if str_token not in list_referenced_signals:

                # 记录当前过程块引用过的输入/状态信号。
                list_referenced_signals.append(str_token)

        # 起始字符偏移换算成 1-based 行号，供报告引用源码区间。
        int_start_line = text[:int_start].count("\n") + 1  # 当前过程块的起始行号

        # 结束字符偏移同样换算成 1-based 行号。
        int_end_line = text[:int_end].count("\n") + 1  # 当前过程块的结束行号

        # 根据敏感列表关键字区分时序逻辑还是组合逻辑。
        if "posedge" in str_sensitivity or "negedge" in str_sensitivity:

            # 边沿触发敏感列表说明当前块属于时序逻辑。
            str_block_kind = "sequential"  # 边沿触发的过程块视作时序逻辑

        # 其余敏感列表统一按组合逻辑处理。
        else:

            # 电平敏感或通配敏感列表默认视作组合逻辑。
            str_block_kind = "combinational"  # 其余过程块默认视作组合逻辑

        # 写入当前过程块的完整结构摘要。
        list_blocks.append(
            {
                "block_id": f"always_{index_block + 1}",  # 稳定的过程块编号
                "kind": str_block_kind,  # 时序或组合类型
                "sensitivity": str_sensitivity,  # 原始敏感列表
                "line_range": [int_start_line, int_end_line],  # 源码行号区间
                "assigned_signals": list_assigned_signals,  # 过程块写信号列表
                "referenced_signals": list_referenced_signals,  # 过程块读信号列表
                "role": _always_block_role(list_assigned_signals),  # 基于写信号推断的功能角色
            }
        )

    # 返回模块中全部 always 过程块的结构摘要。
    return list_blocks

# 从声明列表中筛出被时序 always 块写入的状态元素。
def _extract_state_elements(
    declarations: list[dict[str, Any]], always_blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """识别被时序逻辑驱动的状态元素声明。

    参数:
        declarations: 模块内普通信号声明元数据列表。
        always_blocks: 模块内 always 过程块结构摘要列表。

    返回:
        需要视作状态承载体的声明元数据列表。
    """

    # 时序块写入过的信号名会被视为潜在寄存状态。
    set_sequential_assigned_signals = {  # 全部时序过程块写入过的信号名集合
        str_signal_name  # 单个被时序块驱动的信号名
        for dict_block in always_blocks  # 遍历全部 always 过程块摘要
        if dict_block["kind"] == "sequential"  # 只保留时序过程块的写信号
        for str_signal_name in dict_block["assigned_signals"]  # 展开当前时序块写入的全部信号
    }

    # 结果保持原声明顺序，方便后续说明和验证复用。
    list_state_elements: list[dict[str, Any]] = []  # 被识别为状态元素的声明列表

    # 逐个检查声明是否属于时序写目标。
    for dict_declaration in declarations:

        # 不在时序写集合里的声明不算状态元素。
        if dict_declaration["name"] not in set_sequential_assigned_signals:

            # 跳过纯组合临时量或未被时序逻辑写入的声明。
            continue

        # 保留时序写目标声明，作为状态元素输出。
        list_state_elements.append(dict_declaration)

    # 返回最终识别出的状态元素清单。
    return list_state_elements

# 基于端口、状态和过程块摘要生成结构化功能候选。
def _feature_candidates(
    module_name: str,
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
    always_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成后续行为映射所需的功能候选条目。

    参数:
        module_name: 当前分析目标模块名。
        ports: 模块端口元数据列表。
        state_elements: 状态元素元数据列表。
        always_blocks: always 过程块结构摘要列表。

    返回:
        供特征映射和验证目标复用的功能候选列表。
    """

    # 输出端口会被拼进 reset 和 counter 类候选，保证可观测性。
    list_output_names = [
        item["name"]  # 单个可观测输出端口名
        for item in ports  # 遍历端口元数据并筛出 output 方向
        if item.get("direction") == "output"  # 只保留 output 方向的观测端口
    ]

    # 默认先建立 reset 行为候选，作为公共基础语义。
    list_candidates = [
        {
            "feature_id": "FC001",  # 默认 reset 行为候选编号
            "name": f"{module_name} reset behavior",  # reset 行为候选标题
            "description": "Reset initializes outputs and sequential state to known values.",  # reset 行为摘要
            "signals": [item["name"] for item in ports if item.get("role") == "reset"] + list_output_names,  # reset 行为相关信号
        }
    ]  # 当前模块的结构功能候选列表

    # 每个 always 过程块都会映射成一个结构功能候选。
    for index_block, dict_block in enumerate(always_blocks, start=1):

        # 从过程块角色和写信号生成单条候选摘要。
        list_candidates.append(
            {
                "feature_id": f"FC{index_block + 1:03d}",  # always 过程块对应的候选编号
                "name": f"{dict_block['role'].replace('_', ' ')} block {index_block}",  # always 过程块候选标题
                "description": (
                    f"{dict_block['kind']} logic covering "
                    f"{', '.join(dict_block['assigned_signals']) or 'no detected assignments'}."
                ),  # always 过程块候选摘要
                "signals": dict_block["assigned_signals"],  # 当前过程块的主要写信号
            }
        )

    # 计数器类状态值得额外生成单独候选，方便验证节奏行为。
    if any(item["role"] == "counter" for item in state_elements):

        # 把 counter 状态与可观测输出绑成独立功能候选。
        list_candidates.append(
            {
                "feature_id": "FC900",  # 计数器推进行为候选编号
                "name": "counter progression",  # 计数器推进行为标题
                "description": "Counter/state timing progression remains observable through public outputs.",  # 计数器推进行为摘要
                "signals": [item["name"] for item in state_elements if item["role"] == "counter"] + list_output_names,  # 计数器推进相关信号
            }
        )

    # 返回当前模块的全部结构功能候选。
    return list_candidates

# 把结构候选和可选规格说明融合成可验证的特征映射。
def _feature_mappings(
    *,
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
    always_blocks: list[dict[str, Any]],
    feature_candidates: list[dict[str, Any]],
    spec_text: str | None,
) -> list[dict[str, Any]]:
    """生成模块功能候选与规格说明之间的映射结果。

    参数:
        ports: 模块端口元数据列表。
        state_elements: 状态元素元数据列表。
        always_blocks: always 过程块结构摘要列表。
        feature_candidates: 结构分析阶段生成的功能候选列表。
        spec_text: 可选的行为规格说明文本。

    返回:
        每条功能候选对应的可验证引脚映射与期望输出列表。
    """

    # 端口和状态元素都可能被规格描述引用，因此合并成统一匹配池。
    list_known_signals = ports + state_elements  # 可参与规格匹配的全部已知信号元数据

    # 没有规格文本时，直接根据结构候选生成默认映射。
    if not spec_text:

        # 返回只依赖结构分析的默认特征映射。
        return [
            {
                "feature_id": dict_candidate["feature_id"],  # 继承结构候选编号
                "name": dict_candidate["name"],  # 继承结构候选名称
                "pin_assignments": [
                    {
                        "pin_name": str_signal_name,  # 当前引脚名
                        "role": _mapping_role(str_signal_name, ports, state_elements),  # 当前引脚在映射中的角色
                        "assignment": "preserve current behavior",  # 默认要求保持现有行为
                        "note": "Derived from structural analysis without an external specification.",  # 默认映射来源说明
                    }
                    for str_signal_name in dict_candidate["signals"]
                ],  # 当前特征候选的引脚映射列表
                "stimulus_strategy": (
                    "Use reset, nominal transitions, and observable outputs "
                    "to confirm the structural behavior."
                ),  # 默认激励策略
                "expected_outputs": _expected_outputs(dict_candidate["signals"], ports, always_blocks),  # 结构分析推断的期望输出
            }
            for dict_candidate in feature_candidates
        ]

    # 有规格文本时，逐条构造语义更强的映射结果。
    list_mappings: list[dict[str, Any]] = []  # 基于规格说明构造出的特征映射列表

    # 逐条规格特征生成对应的映射记录。
    for index_feature, dict_feature in enumerate(_parse_spec_features(spec_text), start=1):

        # 先按描述文本匹配出现过的已知信号名。
        list_matched_signals = _match_feature_signals(  # 基于规格描述匹配相关信号
            dict_feature["description"],  # 当前规格特征的正文描述
            list_known_signals,  # 可参与匹配的全部已知信号
        )  # 这条规格特征命中的相关信号名列表

        # reset 描述如果没直接命中信号，则退回全部 reset 端口。
        if (
            not list_matched_signals
            and "reset" in dict_feature["description"].lower()
        ):

            # 给 reset 类特征补上显式复位端口。
            list_matched_signals = [
                item["name"]  # 参与 reset 语义兜底的复位端口名
                for item in ports  # 遍历全部端口以筛出 reset 角色
                if item.get("role") == "reset"  # 只保留角色已识别为 reset 的端口
            ]  # reset 语义的兜底匹配信号列表

        # 写入当前规格特征的完整映射结果。
        list_mappings.append(
            {
                "feature_id": f"FM{index_feature:03d}",  # 规格特征映射编号
                "name": dict_feature["name"],  # 规格特征名称
                "pin_assignments": [
                    {
                        "pin_name": str_signal_name,  # 当前规格特征绑定的引脚名
                        "role": _mapping_role(str_signal_name, ports, state_elements),  # 该引脚在规格映射里的职责角色
                        "assignment": "drive or observe according to the described scenario",  # 基于规格说明的引脚操作建议
                        "note": "Matched from the supplied behavioral notes.",  # 当前映射来自外部规格文本匹配
                    }
                    for str_signal_name in list_matched_signals
                ],  # 当前规格特征的引脚映射列表
                "stimulus_strategy": "Drive reset/control inputs, then observe the mapped public outputs and counters.",  # 规格映射推荐激励策略
                "expected_outputs": _expected_outputs(list_matched_signals, ports, always_blocks),  # 基于匹配信号推断的期望输出
            }
        )

    # 返回全部规格驱动的特征映射结果。
    return list_mappings

# 把特征映射和状态信息整理成验证阶段可直接消费的目标清单。
def _verification_targets(
    module_name: str,
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
    feature_mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成验证阶段使用的检查目标列表。

    参数:
        module_name: 当前分析目标模块名。
        ports: 模块端口元数据列表。
        state_elements: 状态元素元数据列表。
        feature_mappings: 已生成的特征映射列表。

    返回:
        供 verify 工作流直接消费的检查目标列表。
    """

    # 输出端口是 reset 及行为验证最常见的观测点。
    list_output_names = [
        item["name"]  # 单个用于验证观测的输出端口名
        for item in ports  # 遍历全部端口并筛出输出方向
        if item.get("direction") == "output"  # 只保留 output 方向的可观测端口
    ]

    # 默认先构造 reset 后输出稳定性的检查目标。
    list_targets = [
        {
            "check_id": "reset_outputs_known",  # reset 检查目标编号
            "category": "reset",  # reset 检查目标类别
            "signals": [item["name"] for item in ports if item.get("role") == "reset"] + list_output_names,  # reset 相关的控制与观测信号
            "description": f"Verify `{module_name}` drives known output values after reset release.",  # reset 检查目标说明
        }
    ]  # 面向验证阶段的检查目标列表

    # 若检测到 counter 状态，则补充计数推进检查目标。
    if any(item["role"] == "counter" for item in state_elements):

        # 单独增加计数器推进的检查条目。
        list_targets.append(
            {
                "check_id": "counter_progression",  # 计数器推进检查编号
                "category": "checkpoint",  # 计数器推进检查类别
                "signals": [item["name"] for item in state_elements if item["role"] == "counter"],  # 计数器状态信号列表
                "description": "Verify timer/counter progression across phase transitions.",  # 计数器推进检查说明
            }
        )

    # 把每条带期望输出的特征映射转成独立检查目标。
    for dict_mapping in feature_mappings:

        # 没有期望输出的特征映射不生成额外验证目标。
        if not dict_mapping["expected_outputs"]:

            # 跳过不可直接观测的特征映射。
            continue

        # 为当前特征映射追加一条检查目标。
        list_targets.append(
            {
                "check_id": dict_mapping["feature_id"].lower(),  # 由特征映射编号派生的检查编号
                "category": "feature_mapping",  # 特征映射检查类别
                "signals": [item["pin_name"] for item in dict_mapping["pin_assignments"]],  # 当前检查目标关心的引脚列表
                "description": dict_mapping["name"],  # 复用特征映射名称作为检查说明
            }
        )

    # 返回最终生成的验证目标清单。
    return list_targets

# 根据过程块边界与公共接口信号推断可拆分的逻辑子块候选。
def _decomposition_candidates(
    always_blocks: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成模块级分解建议候选列表。

    参数:
        always_blocks: 模块内 always 过程块结构摘要列表。
        ports: 模块端口元数据列表。
        state_elements: 状态元素元数据列表。

    返回:
        每个 always 过程块对应的潜在子模块拆分候选列表。
    """

    # 公共端口名决定了拆分候选的可见边界。
    set_public_names = {item["name"] for item in ports}  # 对外公开的端口名集合

    # 状态元素名决定了拆分候选需要保留的内部状态边界。
    set_state_names = {item["name"] for item in state_elements}  # 内部状态元素名集合

    # 结果按过程块出现顺序输出，方便人工审阅。
    list_candidates: list[dict[str, Any]] = []  # 模块拆分候选列表

    # 为每个过程块生成一条候选记录。
    for index_block, dict_block in enumerate(always_blocks, start=1):

        # 写信号和读信号的交集部分构成拆分边界候选。
        list_boundary_signals = sorted(  # 当前过程块关联的对外/状态边界信号列表
            {
                str_signal_name  # 当前过程块涉及的单个边界信号名
                for str_signal_name in [  # 合并当前过程块的写信号和读信号
                    *dict_block["assigned_signals"],  # 当前过程块显式写入的边界信号
                    *dict_block["referenced_signals"],  # 当前过程块读取依赖的边界信号
                ]
                if (  # 只保留对外接口或状态边界相关的信号
                    str_signal_name in set_public_names  # 当前信号属于公开接口边界
                    or str_signal_name in set_state_names  # 当前信号属于状态寄存边界
                )
            }
        )

        # 写入当前过程块对应的拆分候选。
        list_candidates.append(
            {
                "candidate_id": f"DC{index_block:03d}",  # 拆分候选编号
                "module_name": f"u_block_{index_block}",  # 建议子模块实例名
                "role": dict_block["role"],  # 过程块推断的功能角色
                "kind": dict_block["kind"],  # 过程块属于时序或组合逻辑
                "line_range": dict_block["line_range"],  # 过程块所在源码区间
                "boundary_signals": list_boundary_signals,  # 拆分边界需要暴露的信号列表
            }
        )

    # 返回全部拆分候选。
    return list_candidates

# 从规格说明文本中拆出标题化特征段落，供信号匹配使用。
def _parse_spec_features(spec_text: str) -> list[dict[str, str]]:
    """把规格说明文本解析成特征段落列表。

    参数:
        spec_text: 用户提供的规格说明原文。

    返回:
        由标题和正文描述组成的特征段落列表。
    """

    # 解析结果按正文顺序保存，方便后续映射回说明文档。
    list_sections: list[dict[str, str]] = []  # 规格说明拆出的特征段落列表

    # 仅识别二级标题作为特征段起点，保持规则简单稳定。
    pattern_heading: re.Pattern[str] = re.compile(r"(?m)^##\s+(.+?)\s*$")  # Markdown 二级标题匹配模式

    # 先收集全部标题命中，用于计算每段正文边界。
    list_matches = list(pattern_heading.finditer(spec_text))  # 规格说明中的全部二级标题命中

    # 没有标题时，把全文压平成单段说明。
    if not list_matches:

        # 逐行压平为单条描述，保留原文有效信息。
        str_normalized = " ".join(  # 无标题规格说明的压平描述
            line.strip()  # 去掉单行首尾空白后的正文
            for line in spec_text.splitlines()  # 逐行遍历原始规格说明文本
            if line.strip()  # 跳过规格说明里的空白行
        )

        # 返回默认命名的单段规格说明。
        return [
            {
                "name": "Provided specification",  # 无标题规格说明的默认段名
                "description": str_normalized,  # 压平后的说明正文
            }
        ]

    # 按标题顺序切出各自对应的正文描述。
    for index_section, match_heading in enumerate(list_matches):

        # 当前标题正文的起点是标题结束位置。
        int_start = match_heading.end()  # 当前规格段正文的起始偏移

        # 下一标题起点或全文末尾作为当前段结束边界。
        int_end = (  # 当前规格段正文的结束偏移
            list_matches[index_section + 1].start()  # 下一段标题的起始偏移
            if index_section + 1 < len(list_matches)  # 仍有下一段标题时使用其起点
            else len(spec_text)  # 最后一段正文延伸到全文结尾
        )

        # 逐行压平当前段正文，便于后续做关键词匹配。
        str_description = " ".join(  # 当前规格段的正文描述
            line.strip()  # 去掉当前正文行首尾空白后的文本
            for line in spec_text[int_start:int_end].splitlines()  # 逐行遍历当前标题下的正文
            if line.strip()  # 跳过当前段正文里的空白行
        )

        # 写入当前规格段解析结果。
        list_sections.append(
            {
                "name": match_heading.group(1).strip(),  # 当前规格段标题
                "description": str_description,  # 当前规格段正文
            }
        )

    # 返回全部规格特征段列表。
    return list_sections

# 根据描述文本和已知信号/角色信息匹配相关信号名。
def _match_feature_signals(
    description: str,
    known_signals: list[dict[str, Any]],
) -> list[str]:
    """从特征描述中匹配对应的已知信号名。

    参数:
        description: 当前特征的自然语言描述文本。
        known_signals: 可参与匹配的已知信号元数据列表。

    返回:
        命中的信号名列表，保持首次出现顺序。
    """

    # 先统一成小写文本，降低后续关键词匹配复杂度。
    str_lowered_description = description.lower()  # 当前特征描述的小写版本

    # 结果按已知信号遍历顺序输出，保持稳定。
    list_matched: list[str] = []  # 当前特征描述命中的信号名列表

    # 逐个已知信号检查名字或角色关键词是否出现在描述中。
    for dict_signal in known_signals:

        # 当前元数据记录的信号名。
        str_signal_name = dict_signal["name"]  # 当前正在检查的信号名

        # 同时支持原始下划线形式和空格化别名匹配。
        set_tokens = {
            str_signal_name.lower(),  # 原始下划线形式的小写别名
            str_signal_name.lower().replace("_", " "),  # 下划线转空格后的别名
        }  # 当前信号支持匹配的文本别名集合

        # 角色字段可补充“reset/counter/state”等语义关键词。
        str_role = str(dict_signal.get("role") or "")  # 当前信号推断出的角色名

        # 名称命中或角色语义命中时都认为相关。
        if (
            any(str_token in str_lowered_description for str_token in set_tokens)
            or _role_keyword(str_role, str_lowered_description)
        ):

            # 命中过的信号名按首次出现顺序保留一次。
            if str_signal_name not in list_matched:

                # 记录当前描述关联到的信号名。
                list_matched.append(str_signal_name)

    # 返回最终匹配出的信号名列表。
    return list_matched

# 用角色到关键词的映射补充自然语言描述的信号匹配能力。
def _role_keyword(role: str, lowered: str) -> bool:
    """判断描述文本是否命中了给定角色的关键词。

    参数:
        role: 信号推断出的语义角色名。
        lowered: 已转成小写的描述文本。

    返回:
        描述文本是否包含该角色对应的任一关键词。
    """

    # 角色到关键词的映射决定了语义匹配覆盖面。
    dict_role_keywords = {
        "clock": ["clock"],  # 时钟角色的关键词列表
        "reset": ["reset", "rst"],  # 复位角色的关键词列表
        "counter": ["count", "timer", "clock output"],  # 计数器角色的关键词列表
        "fsm_state": ["state", "phase", "sequence"],  # 状态机角色的关键词列表
        "output_register": ["output", "observe"],  # 输出寄存器角色的关键词列表
        "control": ["request", "enable", "control"],  # 控制角色的关键词列表
    }  # 角色到关键词列表的映射表

    # 只要任一关键词命中，就认为描述涵盖了该角色。
    return any(keyword in lowered for keyword in dict_role_keywords.get(role, []))

# 根据映射信号与过程块信息推断验证阶段应观察的输出。
def _expected_outputs(
    signals: list[str],
    ports: list[dict[str, Any]],
    always_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成特征映射对应的期望输出说明。

    参数:
        signals: 当前特征映射关联的信号名列表。
        ports: 模块端口元数据列表。
        always_blocks: always 过程块结构摘要列表。

    返回:
        可直接交给验证层消费的期望输出列表。
    """

    # 输出端口名集合用于快速判断信号是否可直接观测。
    set_output_names = {
        item["name"]  # 单个可直接观测的输出端口名
        for item in ports  # 遍历模块端口并筛出 output 方向
        if item.get("direction") == "output"  # 只保留 output 方向的公开端口
    }  # 当前模块输出端口名集合

    # 先尝试从当前映射信号里直接找到可观测输出。
    list_expected: list[dict[str, Any]] = []  # 当前特征映射的期望输出列表

    # 逐个关联信号检查其是否本身就是公开输出端口。
    for str_signal_name in signals:

        # 信号本身是输出端口时，直接生成一条观测期望。
        if str_signal_name in set_output_names:

            # 当前输出端口的观测要求直接来源于映射信号。
            list_expected.append(
                {
                    "pin_name": str_signal_name,  # 需要观测的公开输出端口
                    "expected_value": "observe behavioral change at the public output",  # 期望看到行为变化
                    "check_timing": "after the next relevant clock or control event",  # 推荐观测时机
                }
            )

    # 若映射信号本身不含输出，则退回过程块写输出的关系推断。
    if not list_expected and set_output_names:

        # 先筛出写到公开输出端口的过程块。
        list_blocks_with_outputs = [
            dict_block  # 当前写过公开输出的过程块摘要
            for dict_block in always_blocks  # 遍历全部过程块并筛选输出驱动关系
            if any(  # 只保留真正写到公开输出端口的过程块
                str_signal_name in set_output_names  # 当前写信号是否属于公开输出端口
                for str_signal_name in dict_block["assigned_signals"]  # 遍历当前过程块写入的全部信号
            )
        ]  # 写过公开输出端口的过程块列表

        # 从这些过程块里补推导一批默认输出期望。
        for dict_block in list_blocks_with_outputs:

            # 当前过程块的每个输出写信号都可以形成默认观测点。
            for str_signal_name in dict_block["assigned_signals"]:

                # 仅保留真实公开输出端口。
                if str_signal_name in set_output_names:

                    # 用过程块角色语义生成默认输出预期。
                    list_expected.append(
                        {
                            "pin_name": str_signal_name,  # 当前默认观测的公开输出端口
                            "expected_value": f"follow {dict_block['role']} semantics",  # 期望体现过程块角色语义
                            "check_timing": "after the associated sequential update",  # 推荐在相关更新后观测
                        }
                    )

    # 返回最终整理出的期望输出列表。
    return list_expected

# 在模块源码文本里定位指定模块声明的正则匹配结果。
def _module_match(module_name: str, text: str) -> re.Match[str]:
    """定位指定模块名对应的 module 声明匹配结果。

    参数:
        module_name: 需要定位的目标模块名。
        text: 包含模块声明的源码文本。

    返回:
        与目标模块名对应的正则匹配对象。

    异常:
        ValueError: 当源码文本中找不到目标模块时抛出。
    """

    # 顺序扫描全部模块声明，直到命中目标模块名。
    for match_module in MODULE_RE.finditer(text):

        # 匹配到目标模块名时直接返回对应匹配对象。
        if match_module.group(1) == module_name:

            # 返回当前目标模块的声明匹配结果。
            return match_module

    # 找不到目标模块时，用统一错误前缀告知调用方。
    raise ValueError(
        f"> ERR: [Python] Module {module_name!r} was not found in the provided Verilog source."
    )

# 拆分单条端口声明中的多个名称并去掉类型修饰词。
def _split_names(chunk: str) -> list[str]:
    """从端口声明片段中提取最终端口名列表。

    参数:
        chunk: 可能包含类型修饰词和多个名称的端口声明片段。

    返回:
        解析得到的端口名列表。
    """

    # 先移除 signed/wire/reg/logic 等不属于名字本体的修饰词。
    str_cleaned = re.sub(  # 清洗掉类型修饰词后的端口声明片段
        r"\b(?:signed|unsigned|wire|reg|logic)\b",  # 需要从声明片段里剔除的修饰词
        " ",  # 用空格替换修饰词以保留分隔边界
        chunk,  # 原始端口声明片段
    )

    # 按逗号切开后逐项去掉首尾空白。
    list_parts = [part.strip() for part in str_cleaned.split(",")]  # 端口声明片段拆分后的候选项列表

    # 结果按声明顺序保留有效名字。
    list_names: list[str] = []  # 从当前声明片段提取出的端口名列表

    # 逐项提取末尾合法标识符作为端口名。
    for str_part in list_parts:

        # 只取当前片段末尾的 Verilog 标识符。
        match_name = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", str_part)  # 当前片段末尾标识符匹配结果

        # 命中合法端口名时写入结果。
        if match_name:

            # 保留声明顺序追加当前端口名。
            list_names.append(match_name.group(1))

    # 返回当前声明片段中提取出的全部端口名。
    return list_names

# 把 Verilog 位宽区间片段换算成整数位宽。
def _width_from_range(raw: str | None) -> int | None:
    """把位宽区间文本转换成整数位宽。

    参数:
        raw: 形如 `[7:0]` 的位宽区间片段，可为空。

    返回:
        成功时返回位宽整数；无位宽时返回 1；无法解析时返回 `None`。
    """

    # 缺省位宽片段按单 bit 信号处理。
    if not raw:

        # 返回缺省的一位宽度。
        return 1

    # 解析形如 [msb:lsb] 的位宽区间。
    match_range = re.search(  # 位宽区间片段的正则匹配结果
        r"\[\s*(\d+)\s*:\s*(\d+)\s*\]",  # 提取 msb 与 lsb 两个常量端点
        raw,  # 原始位宽区间文本
    )

    # 不符合常见常量区间格式时返回未知。
    if not match_range:

        # 调用方可据此把位宽视作未解析。
        return None

    # 根据上下界差值换算出最终位宽。
    return abs(int(match_range.group(1)) - int(match_range.group(2))) + 1

# 去掉 RTL 文本中的块注释和行注释，保留源码结构。
def _strip_comments(text: str) -> str:
    """移除 RTL 文本中的块注释和行注释。

    参数:
        text: 原始 RTL 源码文本。

    返回:
        去掉注释后的源码文本。
    """

    # 先删除跨行块注释，避免后续行注释处理跨越错误边界。
    str_without_block_comments = re.sub(  # 去掉块注释后的源码文本
        r"/\*.*?\*/",  # 匹配跨行块注释正文
        "",  # 用空字符串完全删除块注释
        text,  # 原始 RTL 源码文本
        flags=re.DOTALL,  # 允许块注释跨多行匹配
    )

    # 再删除单行注释，保留换行结构。
    return re.sub(r"//[^\n\r]*", "", str_without_block_comments)

# 根据端口名称猜测该端口承担的接口角色。
def _port_role(name: str) -> str:
    """根据端口名称推断接口角色。

    参数:
        name: 端口名。

    返回:
        端口对应的粗粒度角色标签。
    """

    # 统一转成小写，降低关键词判断复杂度。
    str_lowered_name = name.lower()  # 当前端口名的小写形式

    # 时钟命名优先识别为 clock 角色。
    if "clk" in str_lowered_name or "clock" in str_lowered_name:

        # 当前端口名体现了时钟语义。
        return "clock"

    # reset/rst 命名优先识别为 reset 角色。
    if "rst" in str_lowered_name or "reset" in str_lowered_name:

        # 当前端口名体现了复位语义。
        return "reset"

    # valid/ready/done/status 等命名优先归为状态输出。
    if any(
        str_token in str_lowered_name
        for str_token in ("valid", "ready", "done", "status")
    ):

        # 当前端口名更像状态或握手结果输出。
        return "status"

    # request/enable/mode/sel 等命名归为控制输入。
    if any(
        str_token in str_lowered_name
        for str_token in ("req", "request", "enable", "mode", "sel")
    ):

        # 当前端口名主要承担控制作用。
        return "control"

    # data/addr 等命名通常代表通用数据通路。
    if any(str_token in str_lowered_name for str_token in ("data", "addr", "clock")):

        # 当前端口名更像普通数据或地址通路。
        return "data"

    # 无明显语义时退回通用 signal 标签。
    return "signal"

# 根据内部信号名称推断其更细的状态/寄存角色。
def _signal_role(name: str) -> str:
    """根据内部信号名称推断结构角色。

    参数:
        name: 内部声明信号名。

    返回:
        信号对应的内部结构角色标签。
    """

    # 统一成小写后做关键词匹配。
    str_lowered_name = name.lower()  # 当前内部信号名的小写形式

    # state 命名优先识别成状态机状态寄存器。
    if "state" in str_lowered_name:

        # 当前信号名体现状态机语义。
        return "fsm_state"

    # count/timer 类命名优先识别成计数器。
    if any(str_token in str_lowered_name for str_token in ("cnt", "count", "timer")):

        # 当前信号名体现计数或计时语义。
        return "counter"

    # 颜色灯或 p_ 前缀常见于输出寄存器。
    if (
        str_lowered_name.startswith("p_")
        or any(
            str_token in str_lowered_name
            for str_token in ("red", "yellow", "green")
        )
    ):

        # 当前信号名更像驱动可见输出的寄存器。
        return "output_register"

    # 其余时序声明默认视作普通寄存器。
    return "register"

# 根据 always 块的写信号名称推断该块承担的主要逻辑角色。
def _always_block_role(assigned_signals: list[str]) -> str:
    """根据 always 块写信号推断其功能角色。

    参数:
        assigned_signals: 当前过程块写入的信号名列表。

    返回:
        当前过程块对应的粗粒度功能角色标签。
    """

    # 合并写信号名文本，方便统一做关键词判断。
    str_lowered_signal_names = " ".join(  # 当前过程块写信号名的小写拼接文本
        str_signal_name.lower()  # 单个写信号的小写名称
        for str_signal_name in assigned_signals  # 逐个拼接当前过程块的写信号名
    )

    # 涉及 state 的过程块优先视作状态迁移逻辑。
    if "state" in str_lowered_signal_names:

        # 当前过程块主要负责状态切换。
        return "state_transition"

    # 涉及计数器类信号时视作计数更新逻辑。
    if any(
        str_token in str_lowered_signal_names
        for str_token in ("cnt", "count", "timer")
    ):

        # 当前过程块主要负责计数或定时更新。
        return "counter_update"

    # 涉及颜色灯、valid、data 等输出类信号时视作输出更新逻辑。
    if any(
        str_token in str_lowered_signal_names
        for str_token in ("red", "yellow", "green", "valid", "data")
    ):

        # 当前过程块更像输出更新路径。
        return "output_update"

    # 其他情况退回通用逻辑分区标签。
    return "logic_partition"

# 根据信号是否属于端口或内部状态，推断它在映射中的验证角色。
def _mapping_role(
    signal: str,
    ports: list[dict[str, Any]],
    state_elements: list[dict[str, Any]],
) -> str:
    """推断单个信号在行为映射中的验证角色。

    参数:
        signal: 当前待判断的信号名。
        ports: 模块端口元数据列表。
        state_elements: 状态元素元数据列表。

    返回:
        验证映射中使用的角色标签。
    """

    # 端口优先决定验证角色，因为它们直接关联可驱动/可观测接口。
    for dict_port in ports:

        # 只处理名字命中的端口记录。
        if dict_port["name"] == signal:

            # 输入端口根据原始角色再细分成 control 或 data_input。
            if dict_port.get("direction") == "input":

                # reset/control 输入优先归到控制类。
                return (
                    "control"
                    if dict_port.get("role") in {"reset", "control"}
                    else "data_input"
                )

            # 输出端口根据接口方向映射成可观测状态或普通数据输出。
            if dict_port.get("direction") == "output":

                # status/signal 类输出优先视作观测状态。
                return (
                    "status"
                    if dict_port.get("role") in {"status", "signal"}
                    else "data_output"
                )

    # 端口未命中时，再检查其是否属于内部状态元素。
    for dict_state_element in state_elements:

        # 名字命中的状态元素复用其结构角色。
        if dict_state_element["name"] == signal:

            # 当前信号属于内部状态元素。
            return dict_state_element.get("role", "internal_state")

    # 其余情况统一视作内部状态。
    return "internal_state"
