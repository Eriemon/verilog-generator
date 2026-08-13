"""渲染 Verilog-2001 生成流程使用的分阶段模型提示词。"""

# 延迟注解解析，避免运行期求值嵌套 JSON 类型。
from __future__ import annotations

# 标准库负责 JSON 序列化、路径识别和通用负载标注。
import json
from pathlib import Path
from typing import Any

# erie_strict header 合同需要逐字注入 prompt，避免模型回退到旧模板。
from ..header_contract import prompt_header_contract_text

# 运行时模块提供人工决策、模板选择、计划分解和约束摘要。
from scripts.python.existing_rtl.intervention import decision_applies

# 接口模板选择错误会被转写进 prompt，而不是中断普通渲染。
from .interface_templates import InterfaceTemplateError, select_interface_template

# 计划分解让 staged prompt 可按 subfunction 裁剪。
from .planning import decompose_spec

# 模式模板和 reference tag 注入 prompt，供后续 RTL/testbench 对齐。
from .pattern_templates import summarize_pattern_templates
from scripts.python.existing_rtl.semantic_contract import SEMANTIC_RESULT_TAG

# RTL Markdown 约束摘要与 vector hash tag 是 prompt hard gate 的来源。
from .verilog_gate_catalog import summarize_constraints_for_prompt
from .spec import normalize_spec
from .vectors import VECTOR_HASH_TAG

# use-case 模板选择结果会影响板级 ADC/DAC 生成提示。
from .use_case_templates import UseCaseTemplateError, select_use_case_template, summarize_use_case_template

# prompt 阶段白名单同时约束 CLI 参数和 staged workflow 调用。
PROMPT_STAGES: tuple[str, ...] = (  # 防止未登记阶段名请求不存在的工作流产物
    "summarize",  # 生成规格证据摘要产物
    "decompose",  # 生成子功能依赖拆分产物
    "augment",  # 生成信息字典补全产物
    "review",  # 生成计划审查 Markdown 产物
    "requirements",  # 生成需求归一化 JSON 合同
    "codegen_plan",  # 生成 RTL 代码计划 JSON 合同
    "tests",  # 生成语义测试计划 JSON 合同
    "pseudocode",  # 生成 reference 行为草图文档
    "python",  # 生成 Python oracle 与向量产物
    "rtl",  # 生成最终 Verilog RTL 与 testbench
)

# 注释语言白名单和生成器 prompt 中的语言约束一一对应。
COMMENT_LANGUAGES: tuple[str, ...] = ("zh", "en")  # 允许注释语言代码

# prompt 预算只影响上下文裁剪，不改变 manifest 合同。
PROMPT_BUDGETS: tuple[str, ...] = ("normal", "compact", "repair")  # 支持的上下文预算档位

# staged prompt 中嵌入 JSON 章节时统一使用双空格缩进，避免散落匿名常量。
PROMPT_JSON_INDENT = 2  # prompt JSON 代码块的统一缩进宽度

# _prompt_json_block_text 统一渲染 staged prompt 里的 JSON 小节。
def _prompt_json_block_text(obj_payload: Any) -> str:
    """
    把 staged prompt 上下文片段转成统一缩进的 JSON 文本。

    参数:
        obj_payload: 需要嵌入 prompt 的任意 JSON 可序列化对象。
    返回:
        双空格缩进且保留中文的 JSON 文本。
    """

    # 返回统一格式的 JSON 段落，减少 `_staged_prompt_text` 中的重复序列化细节。
    return json.dumps(obj_payload, indent=PROMPT_JSON_INDENT, ensure_ascii=False)

# 路径后缀到 fenced-code 语言名的映射保持旧版输出命名。
LANGUAGE_BY_SUFFIX: dict[str, str] = {  # 保证输出块使用抽取器可识别的围栏语言
    ".v": "verilog",  # RTL 和 testbench 围栏使用 Verilog 高亮
    ".tcl": "tcl",  # 工具脚本围栏保持 Tcl 语法标识
    ".xdc": "xdc",  # 约束产物围栏标记为 XDC
    ".py": "python",  # Python oracle 围栏供 reference 阶段抽取
    ".json": "json",  # 计划、manifest 和向量围栏保持 JSON 解析语义
    ".md": "markdown",  # 审查和伪代码围栏保留 Markdown 语义
}

# render_prompt 是 workflow 和 CLI 共享的 prompt 入口。
def render_prompt(
    spec: dict[str, Any],
    target: str | None = None,
    stage: str | None = None,
    **kwargs: Any,
) -> str:
    """渲染完整 RTL prompt 或指定 staged prompt。

    参数:
        spec: 原始 Verilog 生成规格，shape 为 JSON object，dtype 为 dict，单位不适用。
        target: 可选目标类型，shape 为标量字符串或 None，dtype 为 str | None，单位不适用。
        stage: 可选 workflow 阶段，shape 为标量字符串或 None，dtype 为 str | None，单位不适用。
        kwargs: 兼容旧版关键字参数，包括 context_manifest、evidence、memory、budget 和 decision。

    返回:
        可直接发送给模型 provider 的 Markdown prompt 文本。

    异常:
        stage、comment_language 或 budget 不在白名单内时抛出 ValueError。
    """

    # dict_normalized_spec 是所有 prompt 渲染路径共享的规范化规格。
    dict_normalized_spec = normalize_spec(spec, target=target)  # 规范化后的 RTL 规格

    # dict_options 集中保留旧版关键字参数，避免公开入口继续膨胀。
    dict_options = _prompt_options(kwargs)  # prompt 渲染上下文选项

    # staged prompt 需要先校验阶段名，再按工作流上下文渲染。
    if stage:

        # str_stage 是经过白名单校验的 staged workflow 阶段。
        str_stage = _require_stage(stage)  # 规范化后的阶段名

        # 返回指定阶段 prompt，保持旧版 stage 参数语义。
        return _render_staged_prompt(dict_normalized_spec, str_stage, dict_options)

    # 非 staged 调用渲染完整 RTL 生成任务 prompt。
    return _render_rtl_prompt(dict_normalized_spec, dict_options)

# require_comment_language 校验 prompt 注释语言选项。
def require_comment_language(comment_language: str) -> str:
    """归一化并校验注释语言代码。

    参数:
        comment_language: 调用方传入的注释语言代码。

    返回:
        小写后的合法语言代码。

    异常:
        语言代码不在 COMMENT_LANGUAGES 中时抛出 ValueError。
    """

    # str_normalized_language 使用小写值兼容 CLI 和 Python API 输入。
    str_normalized_language = comment_language.lower()  # 规范化后的注释语言

    # 注释语言必须来自白名单，避免 prompt 写入模糊语言指令。
    if str_normalized_language not in COMMENT_LANGUAGES:

        # 语言错误直接暴露允许集合，减少 CLI 用户二次查文档。
        raise ValueError(f"> ERR: [Python] Comment language must be one of {', '.join(COMMENT_LANGUAGES)}.")

    # 返回已经规范化的语言代码。
    return str_normalized_language

# require_prompt_budget 校验 prompt 上下文预算档位。
def require_prompt_budget(budget: str) -> str:
    """归一化并校验 prompt 预算档位。

    参数:
        budget: 调用方传入的预算名称。

    返回:
        小写后的合法预算名称。

    异常:
        预算名称不在 PROMPT_BUDGETS 中时抛出 ValueError。
    """

    # str_normalized_budget 使用小写值兼容 CLI 参数输入。
    str_normalized_budget = budget.lower()  # 规范化后的预算档位

    # 预算档位必须来自白名单，避免上下文裁剪行为不可预测。
    if str_normalized_budget not in PROMPT_BUDGETS:

        # 预算错误暴露所有档位，方便 workflow 配置回填。
        raise ValueError(f"> ERR: [Python] Prompt budget must be one of {', '.join(PROMPT_BUDGETS)}.")

    # 返回已经规范化的预算档位。
    return str_normalized_budget

# _prompt_options 汇总旧版 render_prompt 关键字参数。
def _prompt_options(dict_kwargs: dict[str, Any]) -> dict[str, Any]:
    """把旧版 render_prompt 关键字参数整理成内部上下文字典。

    参数:
        dict_kwargs: render_prompt 收到的任意关键字参数。

    返回:
        包含上下文、证据、语言、预算和人工决策的内部选项字典。
    """

    # str_comment_language 先通过公开校验器，保证 staged 和 RTL 路径一致。
    str_comment_language = require_comment_language(str(dict_kwargs.get("comment_language", "zh")))  # 注释语言

    # str_budget 决定上下文裁剪上限和 prompt 中展示的预算标签。
    str_budget = require_prompt_budget(str(dict_kwargs.get("budget", "normal")))  # prompt 预算档位

    # 返回内部上下文字典，字段名保持和旧版关键字参数接近。
    return {
        "context_manifest": dict_kwargs.get("context_manifest"),  # 上一阶段 manifest
        "context_dir": dict_kwargs.get("context_dir"),  # 上一阶段 artifact 目录
        "evidence": dict_kwargs.get("evidence"),  # workflow 证据摘要
        "memory": dict_kwargs.get("memory"),  # prompt memory 约束
        "comment_language": str_comment_language,  # 已校验注释语言
        "vector_contract": dict_kwargs.get("vector_contract"),  # reference 向量合同
        "codegen_plan": dict_kwargs.get("codegen_plan"),  # 上游代码生成计划
        "subfunction": dict_kwargs.get("subfunction"),  # 可选子功能范围
        "budget": str_budget,  # 已校验上下文预算
        "decision": dict_kwargs.get("decision"),  # 人工决策约束
    }

# _require_stage 校验 staged workflow 阶段名称。
def _require_stage(stage: str) -> str:
    """归一化并校验 staged prompt 阶段名称。

    参数:
        stage: 调用方传入的阶段名称。

    返回:
        小写后的合法阶段名称。

    异常:
        阶段名称不在 PROMPT_STAGES 中时抛出 ValueError。
    """

    # str_normalized_stage 使用小写值兼容 CLI 和配置文件输入。
    str_normalized_stage = stage.lower()  # 规范化后的阶段名称

    # 阶段名必须来自白名单，避免 manifest 文件类型失配。
    if str_normalized_stage not in PROMPT_STAGES:

        # 阶段错误列出完整枚举，避免生成未知 manifest 类型。
        raise ValueError(f"> ERR: [Python] Stage must be one of {', '.join(PROMPT_STAGES)}.")

    # 返回已经规范化的阶段名。
    return str_normalized_stage

# _render_rtl_prompt 渲染非 staged 的完整 RTL 任务 prompt。
def _render_rtl_prompt(dict_spec: dict[str, Any], dict_options: dict[str, Any]) -> str:
    """渲染一次性 Verilog RTL 生成 prompt。

    参数:
        dict_spec: normalize_spec 输出的规范化规格。
        dict_options: _prompt_options 输出的渲染选项。

    返回:
        包含设计规则、模板上下文和 manifest 合同的 prompt 文本。
    """

    # str_comment_language 决定生成 RTL 注释规则使用中文还是英文。
    str_comment_language = str(dict_options["comment_language"])  # RTL 注释规则分支使用的语言代码

    # dict_manifest 是完整 RTL 生成的输出清单合同。
    dict_manifest = _manifest_for(dict_spec)  # 非 staged 生成使用的完整文件合同

    # list_rules 汇总基础 RTL、Markdown 约束、样式和注释规则。
    list_rules = _rtl_generation_rules(dict_spec, str_comment_language)  # 完整 RTL 设计规则

    # dict_base_context 把 _base_prompt 的旧参数收敛到单个上下文对象。
    dict_base_context = {  # 把一次性 RTL 规格、manifest 和模板正文合并给主提示词
        "spec": dict_spec,  # Generation spec 章节展示的规范化需求
        "title": "Verilog RTL generation task",  # 完整 RTL prompt 的 H1 标题
        "target_line": "Generate synthesizable Verilog-2001 RTL.",  # provider 看到的生成目标声明
        "rules": list_rules,  # Design rules 章节的可综合与注释约束
        "manifest": dict_manifest,  # Output contract 章节的文件清单示例
        "interface_template": _interface_template_context(dict_spec),  # 总线端口模板正文或选择错误
        "use_case_template": _use_case_template_context(dict_spec),  # ADC/DAC 板级模板正文或选择错误
        "pattern_templates": _pattern_template_context(dict_spec),  # 本地 RTL 参考模式摘要
    }

    # str_prompt 是完整 RTL prompt 主体，随后按需追加人工决策段落。
    str_prompt = _base_prompt(dict_base_context)  # 未追加可选段落的 prompt 文本

    # 返回带人工决策约束的最终 prompt。
    return _append_optional_sections(str_prompt, decision=dict_options.get("decision"))

# _rtl_generation_rules 组织完整 RTL prompt 的规则列表。
def _rtl_generation_rules(dict_spec: dict[str, Any], str_comment_language: str) -> list[str]:
    """组织完整 RTL 生成阶段使用的设计规则。

    参数:
        dict_spec: normalize_spec 输出的规范化规格。
        str_comment_language: 已校验的注释语言代码。

    返回:
        按 prompt 展示顺序排列的规则字符串列表。
    """

    # list_rules 先放入通用 RTL 可综合性和输出约束。
    list_rules = [  # 完整 RTL 生成规则列表
        "Implement the top module named exactly as spec.name.",  # 顶层模块名必须与规格一致
        "Declare every interface port with explicit direction and bit width.",  # 端口声明必须可综合且无隐式位宽
        "Use only Verilog-2001 syntax in design and testbench files.",  # 限定方言以匹配本仓库验证链
        (
            "Prefer standardized buses when choosing interfaces: AXI-Stream for streaming data, "
            "AXI4-Lite for control/status registers, AXI4 for memory-mapped bulk transfers, "
            "and AHB/APB when the platform requires them."
        ),
        (
            "When a design cannot naturally use a standard memory/control bus but still needs "
            "interface unification, extend AXI-Stream with explicit sideband metadata "
            "in interface_profile."
        ),
        "Use edge-triggered sequential logic for clocked state.",  # 时序状态必须由边沿触发寄存器承载
        (
            "Honor the reset object exactly; if reset.synchronous is true, do not put reset "
            "in an always-block sensitivity list."
        ),
        (
            "Every FSM must use three independent processes: a clocked current-state register, "
            "procedural combinational next-state logic, and separate state output/task logic."
        ),
        (
            "Use complete combinational assignments with safe defaults before if/case decisions "
            "to avoid unintended latches."
        ),
        (
            "Use explicit default branches in case/casex/casez statements unless the spec proves "
            "a complete one-hot or binary decode."
        ),
        (
            "Do not create raw gated clocks with logic operators; use clock-enable RTL unless an "
            "approved clock-gating wrapper is explicitly specified."
        ),
        (
            "Document CDC and reset assumptions in manifest checks when more than one clock/reset "
            "domain or asynchronous reset behavior is present."
        ),
        (
            "Keep datapath and control structure timing-reviewable by naming pipeline registers, "
            "avoiding hidden feedback, and keeping high-fanout enables visible."
        ),
        (
            "Avoid #delay controls, force/release, multiple drivers, unintended latches, and "
            "non-synthesizable constructs in RTL source files."
        ),
        (
            "Avoid Verilog function/task blocks in generated Verilog, especially synthesizable RTL; "
            "prefer explicit always/assign logic and inline testbench checks for easier waveform debugging."
        ),
    ]

    # Markdown 约束摘要来自专门模块，保持 prompt 与 lint 口径一致。
    list_rules.extend(_rtl_md_constraint_rules())

    # 完成基础约束后追加测试平台和占位符要求。
    list_rules.extend(
        [
            "Keep simulation-only system tasks inside testbench files.",
            "Include a focused self-checking testbench when requested by outputs.",
            "Cover reset, normal operation, boundary conditions, and every behavior item in the testbench.",
            "Do not leave TODO, FIXME, ellipses, placeholder text, undefined modules, "
            "or missing testbench entry modules.",
        ]
    )

    # erie_strict 和注释规则按规格与语言动态追加。
    list_rules.extend(_rtl_style_rules(dict_spec, str_comment_language))

    # 注释放置规则最后出现，让模型靠近输出合同时仍能看到。
    list_rules.extend(_comment_rules_for("rtl", str_comment_language))

    # 返回完整规则列表。
    return list_rules

# _base_prompt 拼装完整 RTL 生成 prompt 主体。
def _base_prompt(dict_context: dict[str, Any]) -> str:
    """拼装完整 RTL 生成 prompt 主体。

    参数:
        dict_context: 包含 spec、规则、manifest 和模板上下文的字典。

    返回:
        不含人工决策可选段落的 Markdown prompt 文本。
    """

    # dict_spec 是 prompt 中展示给模型的规范化规格。
    dict_spec = dict_context["spec"]  # 完整 RTL 规格

    # dict_manifest 是模型必须按原样覆盖文件数组的输出合同。
    dict_manifest = dict_context["manifest"]  # prompt 内嵌的 code-fence 输出清单

    # str_spec_json 使用缩进格式便于模型读取嵌套规格。
    str_spec_json = json.dumps(dict_spec, indent=2, ensure_ascii=False)  # 规格 JSON 文本

    # str_manifest_json 是 prompt 输出合同中的 manifest 示例。
    str_manifest_json = json.dumps(dict_manifest, indent=2, ensure_ascii=False)  # 模型必须回填的 manifest 示例

    # str_rules_text 把规则列表转成 Markdown bullet 列表。
    str_rules_text = _bullet_lines(dict_context["rules"])  # Markdown 规则段落

    # str_interface_template_text 展示本地接口模板或模板选择错误。
    str_interface_template_text = _format_interface_template_section(dict_context.get("interface_template"))  # 接口模板段

    # str_use_case_template_text 展示 ADC/DAC use-case 模板上下文。
    str_use_case_template_text = _format_use_case_template_section(dict_context.get("use_case_template"))  # 板级用例模板段

    # str_pattern_template_text 展示从仓库 RTL 中蒸馏出的设计模式。
    str_pattern_template_text = _format_pattern_template_section(dict_context.get("pattern_templates"))  # 模式模板段

    # 返回完整 Markdown prompt，长句在源文件中显式换行以通过 current-project 门禁。
    return f"""# {dict_context["title"]}

You are an expert hardware design generator. {dict_context["target_line"]}
Think through the design internally before writing files, but do not output that analysis.

## Generation spec

```json
{str_spec_json}
```

## Design rules

{str_rules_text}

{str_interface_template_text}

{str_use_case_template_text}

{str_pattern_template_text}

## Output contract

Return only fenced code blocks: first the manifest JSON, then one file block per manifest file.
Do not add prose, Markdown headings, explanations, bullet lists, or analysis outside code fences.

The manifest must preserve the `files` array exactly as requested.
It may fill the `checks` arrays with concise strings.

```json
{str_manifest_json}
```

Then return one fenced code block for every manifest file, and no extra file blocks.
Put the exact relative file path in the fence info as `path=<relative/path>`.

Path rules:

- Every manifest path must have exactly one matching code fence.
- Every code fence path must appear in the manifest.
- Paths must be relative, unique, case-exact, slash-exact, and must not contain `..`.
- Optional partial regeneration must use manifest `patches` entries with `path` and `marker`.
- Patch fences must include `path=<relative/path> patch=<marker>`.
- Patch target regions must be bounded by matching VERILOG-GEN-PATCH comments.
"""

# _render_staged_prompt 渲染 staged workflow 中的单阶段 prompt。
def _render_staged_prompt(dict_spec: dict[str, Any], str_stage: str, dict_options: dict[str, Any]) -> str:
    """渲染 staged Spec-to-Verilog 工作流的单阶段 prompt。

    参数:
        dict_spec: normalize_spec 输出的规范化规格。
        str_stage: 已校验的 staged workflow 阶段名。
        dict_options: _prompt_options 输出的渲染选项。

    返回:
        指定阶段的 Markdown prompt 文本。
    """

    # dict_plan 是按子功能拆分后的生成计划。
    dict_plan = decompose_spec(dict_spec)  # 完整子功能计划

    # dict_scoped_plan 在 subfunction 模式下只保留目标子功能。
    dict_scoped_plan = _scope_plan(dict_plan, _optional_str(dict_options.get("subfunction")))  # 当前 prompt 计划范围

    # dict_manifest 描述该阶段必须产出的文件。
    dict_manifest = _stage_manifest_for(dict_scoped_plan, str_stage)  # 阶段输出 manifest

    # tuple_guidance 包含阶段标题、目标和规则列表。
    tuple_guidance = _stage_guidance(  # 当前 stage 的标题、目标和规则集合
        dict_scoped_plan,  # 已按 subfunction 裁剪的计划
        str_stage,  # 决定阶段说明和 manifest 类型的 stage 键
        str(dict_options["comment_language"]),  # RTL 注释规则使用的语言代码
        _optional_dict(dict_options.get("vector_contract")),  # Python oracle 产出的可选向量合同
    )  # 当前 stage 标题、目标和生成规则

    # dict_context 收集上游 artifact 摘要，compact 预算会裁剪文本。
    dict_context = _artifact_context(  # 注入 prompt 的前序产物裁剪视图
        _optional_dict(dict_options.get("context_manifest")),  # 上一阶段 manifest 清单
        _optional_path(dict_options.get("context_dir")),  # 上一阶段 artifact 根目录
        budget=str(dict_options["budget"]),  # 控制单文件注入长度的预算档位
    )  # 注入 prompt 的上游 artifact 摘要

    # dict_memory_constraints 是 prompt memory 过滤后的约束对象。
    dict_memory_constraints = _memory_constraints(  # trace 记忆中可复用的生成约束
        _optional_dict(dict_options.get("memory")),  # 历史 trace 汇总出的 memory 对象
        str_stage,  # 后续 memory 过滤可按 stage 收敛
        subfunction=_optional_str(dict_options.get("subfunction")),  # memory 只应影响的目标子功能
        budget=str(dict_options["budget"]),  # 后续 memory 裁剪可按预算收敛
    )  # 历史失败经验转成的本轮生成约束

    # dict_decision_context 只在人工决策适用于当前子功能时注入。
    dict_decision_context = _decision_context(dict_options)  # 当前 prompt 可见的人工决策

    # dict_render_context 汇总 _staged_prompt_text 模板的全部章节输入。
    dict_render_context = {  # 绑定 Markdown 渲染器逐章读取的键名、裁剪载荷、记忆约束和模板片段
        "stage_title": tuple_guidance[0],  # Markdown 一级标题使用的阶段标题
        "stage_goal": tuple_guidance[1],  # Stage goal 行展示的目标句
        "stage_rules": tuple_guidance[2],  # Stage rules 章节的约束清单
        "plan": dict_scoped_plan,  # Subfunction implementation plan 章节的 JSON 载荷
        "manifest": dict_manifest,  # Output contract 章节要求回填的文件清单
        "artifact_context": dict_context,  # Prior artifact context 章节的裁剪文本
        "evidence": _evidence_context(  # prompt 中 Evidence context 章节的裁剪载荷
            _optional_dict(dict_options.get("evidence")),  # workflow 收集的候选证据
            dict_scoped_plan,  # 当前计划范围，保留给证据过滤策略
            str(dict_options["budget"]),  # compact 预算减少证据条数
        ),  # 当前 stage 可见的裁剪证据
        "memory": dict_memory_constraints,  # Prompt memory constraints 章节的 trace 经验
        "vector_contract": dict_options.get("vector_contract") or {},  # Reference vector contract 章节的向量哈希
        "requirements": _design_requirements_context(dict_scoped_plan),  # Design requirements 章节的需求摘要
        "codegen_plan": dict_options.get("codegen_plan") or {},  # Code generation plan 章节的上游计划
        "decision": dict_decision_context,  # Human decision constraints 章节的人工输入
        "budget": str(dict_options["budget"]),  # prompt 头部展示的预算标签
        "subfunction": dict_options.get("subfunction") or "all",  # prompt 头部展示的子功能范围
        "interface_template": _interface_template_context(dict_scoped_plan),  # 总线端口合同段落的模板正文或失败原因
        "use_case_template": _use_case_template_context(dict_scoped_plan),  # ADC/DAC 场景段落的板级示例和侧带约束
        "pattern_templates": _pattern_template_context(dict_scoped_plan),  # 仓库 RTL 模式段落引用的模式模板摘要
    }

    # 返回 staged prompt 文本。
    return _staged_prompt_text(dict_render_context)

# _staged_prompt_text 把 staged 上下文字典渲染为 Markdown。
def _staged_prompt_text(dict_context: dict[str, Any]) -> str:
    """把 staged prompt 上下文渲染为 Markdown 文本。

    参数:
        dict_context: _render_staged_prompt 组装出的上下文字典。

    返回:
        单阶段 workflow prompt 文本。
    """

    # str_spec_json 展示当前子功能范围内的计划。
    str_spec_json = _prompt_json_block_text(dict_context["plan"])  # 子功能计划 JSON

    # str_manifest_json 是该阶段的输出清单合同。
    str_manifest_json = _prompt_json_block_text(dict_context["manifest"])  # stage 文件清单示例 JSON

    # str_context_json 展示上游 artifact 摘要。
    str_context_json = _prompt_json_block_text(dict_context["artifact_context"])  # 产物上下文 JSON

    # str_evidence_json 展示当前 prompt 可用的证据摘要。
    str_evidence_json = _prompt_json_block_text(dict_context["evidence"])  # 上游证据裁剪结果

    # str_memory_json 展示历史失败经验约束。
    str_memory_json = _prompt_json_block_text(dict_context["memory"])  # 历史失败约束 JSON

    # str_vector_json 写入 Reference vector contract 章节，约束 RTL testbench 的 case_id 和 hash。
    str_vector_json = _prompt_json_block_text(dict_context["vector_contract"])  # testbench case 对齐依据

    # str_requirements_json 展示需求归一化上下文。
    str_requirements_json = _prompt_json_block_text(dict_context["requirements"])  # 需求上下文 JSON

    # str_codegen_plan_json 写入 Code generation plan 章节，避免 RTL 阶段重新发明模块边界。
    str_codegen_plan_json = _prompt_json_block_text(dict_context["codegen_plan"])  # 已确认模块边界依据

    # str_decision_json 展示人工决策约束。
    str_decision_json = _prompt_json_block_text(dict_context["decision"])  # 人工决策 JSON

    # str_interface_template_text 让模型看到本地总线端口合同或阻断原因。
    str_interface_template_text = _format_interface_template_section(dict_context.get("interface_template"))  # 总线模板说明

    # str_use_case_template_text 让模型继承板级用例的 sideband 和参数化约定。
    str_use_case_template_text = _format_use_case_template_section(dict_context.get("use_case_template"))  # 板级模板说明

    # str_pattern_template_text 提供可借鉴但不能照抄的本地 RTL 结构。
    str_pattern_template_text = _format_pattern_template_section(dict_context.get("pattern_templates"))  # 精炼模式说明

    # str_rules_text 把阶段规则转成 Markdown bullet。
    str_rules_text = _bullet_lines(dict_context["stage_rules"])  # 阶段规则段落

    # 返回 staged prompt 文本，保持旧版章节名称和关键 tag。
    return f"""# {dict_context["stage_title"]}

You are implementing a staged Spec-to-Verilog workflow.
Stage goal: {dict_context["stage_goal"]}
Use the subfunction plan as the source of truth.
Think internally, but output only the requested fenced blocks.
Prompt budget: {dict_context["budget"]}. Target subfunction: {dict_context["subfunction"]}.

## Subfunction implementation plan

```json
{str_spec_json}
```

## Stage rules

{str_rules_text}

## Evidence context

```json
{str_evidence_json}
```

## Prior artifact context

```json
{str_context_json}
```

## Prompt memory constraints

```json
{str_memory_json}
```

## Design requirements

```json
{str_requirements_json}
```

{str_interface_template_text}

{str_use_case_template_text}

{str_pattern_template_text}

## Code generation plan

```json
{str_codegen_plan_json}
```

## Reference vector contract

When this object is non-empty, generated downstream testbenches must mirror these cases.
They must include the exact comment `{VECTOR_HASH_TAG} <sha256>`.

```json
{str_vector_json}
```

## Human decision constraints

```json
{str_decision_json}
```

## Output contract

Return only fenced code blocks: first the manifest JSON, then one file block per manifest file.
The manifest must preserve the `files` array exactly as requested.
Fill `checks` with concise evidence for coverage, verification, feasibility, and reviewability.

```json
{str_manifest_json}
```

Every file block must use `path=<relative/path>`.
Every path must match the manifest exactly.
"""

# _stage_guidance 返回指定阶段的标题、目标和规则。
def _stage_guidance(
    dict_spec: dict[str, Any],
    str_stage: str,
    str_comment_language: str = "zh",
    dict_vector_contract: dict[str, Any] | None = None,
) -> tuple[str, str, list[str]]:
    """生成 staged prompt 中的阶段说明和规则列表。

    参数:
        dict_spec: 当前子功能范围计划。
        str_stage: 已校验的阶段名称。
        str_comment_language: 已校验的注释语言代码。
        dict_vector_contract: 可选 semantic vector 合同。

    返回:
        由阶段标题、阶段目标和规则列表组成的三元组。
    """

    # list_common_rules 是规划类阶段共享的最小可验证性约束。
    list_common_rules = _common_stage_rules()  # staged prompt 通用规则

    # requirements 阶段只整理已确认需求，不生成代码。
    if str_stage == "requirements":

        # 返回需求归一化阶段说明。
        return _requirements_stage_guidance()

    # codegen_plan 阶段生成结构化实现计划。
    if str_stage == "codegen_plan":

        # 返回代码生成计划阶段说明。
        return _codegen_plan_stage_guidance()

    # python 阶段生成可执行 semantic model。
    if str_stage == "python":

        # 返回 Python semantic 阶段说明。
        return _python_stage_guidance(dict_spec, list_common_rules)

    # rtl 阶段生成最终 Verilog 产物。
    if str_stage == "rtl":

        # RTL 阶段需要绑定 Python oracle、向量合同和 RTL 样式规则。
        return _rtl_stage_guidance(dict_spec, str_comment_language, dict_vector_contract, list_common_rules)

    # dict_titles 保存规划类阶段的固定标题。
    dict_titles = {  # 保证规划阶段提示词标题与历史回归样本逐字一致
        "summarize": "Specification evidence summarization",  # 规格证据摘要 prompt 标题
        "decompose": "Subfunction decomposition planning",  # 子功能拆分 prompt 标题
        "augment": "Information dictionary augmentation",  # 信息字典补全 prompt 标题
        "review": "Plan verifier review",  # 计划审查 Markdown prompt 标题
        "tests": "Semantic test oracle generation",  # 语义测试 oracle prompt 标题
        "pseudocode": "Pseudocode reference behavior generation",  # reference 行为草图 prompt 标题
    }

    # 返回规划类阶段的兜底说明。
    return (
        dict_titles.get(str_stage, "Verilog planning stage"),
        "Prepare evidence-backed planning artifacts for Verilog generation.",
        list_common_rules,
    )

# _common_stage_rules 返回 staged prompt 的共享规则。
def _common_stage_rules() -> list[str]:
    """返回 staged prompt 规划类阶段共享的规则。

    参数:
        无外部业务参数。

    返回:
        共享阶段规则字符串列表。
    """

    # 返回所有阶段都应继承的可验证性规则。
    return [
        "Carry forward every subfunction dependency and interface from the plan.",
        "Do not use TODO, FIXME, ellipses, or placeholder text.",
        "For each subfunction, state how its behavior will be verified by generated artifacts.",
        "Make the stage output verifiable, executable where applicable, and ready for the next stage.",
    ]

# _requirements_stage_guidance 返回需求归一化阶段规则。
def _requirements_stage_guidance() -> tuple[str, str, list[str]]:
    """返回 requirements 阶段的 prompt 说明。

    参数:
        无外部业务参数。

    返回:
        requirements 阶段标题、目标和规则列表。
    """

    # 返回需求阶段固定说明。
    return (
        "Confirmed requirement normalization",
        "Normalize user-confirmed Verilog design requirements into a stable pre-generation contract.",
        [
                "Summarize target, pipeline requirement, streamability classification, "
                "interface family, and confirmed profile.",
            "Do not invent missing confirmation data; record unresolved requirements as open questions.",
        ],
    )

# _codegen_plan_stage_guidance 返回 codegen_plan 阶段规则。
def _codegen_plan_stage_guidance() -> tuple[str, str, list[str]]:
    """返回 codegen_plan 阶段的 prompt 说明。

    参数:
        无外部业务参数。

    返回:
        codegen_plan 阶段标题、目标和规则列表。
    """

    # list_rules 保留旧版 codegen plan 字段合同。
    list_rules = [  # codegen_plan 阶段规则
        (
            "Create `requirements_summary`, `interface_decision`, `pipeline_strategy`, "
            "`module_partition`, `signal_width_strategy`, `reset_clock_strategy`, "
            "`verification_strategy`, `syntax_risk_checks`, `open_questions`, "
            "and `ready_for_generation`."
        ),
        (
            "If confirmation data is incomplete, put the blocker in `open_questions` "
            "and keep `ready_for_generation` false."
        ),
    ]

    # Markdown 约束摘要进入计划阶段，提前暴露 RTL hard gates。
    list_rules.extend(_rtl_md_constraint_rules())

    # 计划阶段输出是 JSON 合同，不允许提前生成 RTL 源码。
    return ("Pre-generation code plan", "Produce a structured implementation plan before Verilog code.", list_rules)

# _python_stage_guidance 定义 Python oracle 阶段的 API 和向量输出要求。
def _python_stage_guidance(
    dict_spec: dict[str, Any],
    list_common_rules: list[str],
) -> tuple[str, str, list[str]]:
    """返回 Python semantic 阶段的 prompt 说明。

    参数:
        dict_spec: 当前子功能范围计划。
        list_common_rules: staged prompt 共享规则。

    返回:
        Python 阶段标题、目标和规则列表。
    """

    # str_design_name 用于构造 semantic vector 文件路径。
    str_design_name = str(dict_spec["name"])  # 设计名称

    # list_rules 保留 semantic model 的可执行 API 合同。
    list_rules = [  # semantic model 必须暴露给语义门禁的行为合同
        "Generate deterministic Python functions/classes for every subfunction.",  # 每个子功能都要有可执行参考行为
        "Expose `run_tests()` and a command-line entry that exits 0 on PASS and nonzero on FAIL.",  # CLI 自测结果驱动门禁退出码
        (
            "Expose `run_case(case)` that accepts one reference-vector case object "
            "and returns normalized outputs as plain JSON-compatible Python values."
        ),
        (
            "Expose optional `collect_checkpoints(case)` that returns JSON-compatible "
            "intermediate observables for localization."
        ),
        (
            f"Include deterministic `REFERENCE_VECTORS` and write the same cases to "
            f"`model/{str_design_name}_vectors.json` when requested by the manifest."
        ),
        "Avoid external dependencies and random behavior unless seeded deterministically.",  # semantic model 必须可离线复现
    ]

    # 通用规则追加在阶段专属合同之后。
    list_rules.extend(list_common_rules)

    # Python 阶段产物会成为后续 RTL testbench 的金标准。
    return (
        "Executable Python semantic model generation",
        "Create an executable Python model that serves as the golden reference for testbench comparison.",
        list_rules,
    )

# _rtl_stage_guidance 绑定 RTL 实现阶段的 oracle、testbench 和样式规则。
def _rtl_stage_guidance(
    dict_spec: dict[str, Any],
    str_comment_language: str,
    dict_vector_contract: dict[str, Any] | None,
    list_common_rules: list[str],
) -> tuple[str, str, list[str]]:
    """返回 RTL 阶段的 prompt 说明。

    参数:
        dict_spec: 当前子功能范围计划。
        str_comment_language: 已校验的注释语言代码。
        dict_vector_contract: 可选 semantic vector 合同。
        list_common_rules: staged prompt 共享规则。

    返回:
        RTL 阶段标题、目标和规则列表。
    """

    # str_design_name 用于指向前序 Python semantic model。
    str_design_name = str(dict_spec["name"])  # semantic model 文件名使用的设计标识

    # list_rules 汇总 RTL 阶段的 reference、testbench 和实现合同。
    list_rules = [  # RTL 产物必须满足的 oracle 和可综合性规则
        f"Treat `model/{str_design_name}_model.py` as the prior-stage reference artifact.",  # 绑定前序 Python oracle
        "Mirror the Python semantic model's observable behavior.",  # RTL 输出必须对齐 oracle 可观测结果
        "Implement the top module and submodule structure with explicit ports and reset behavior.",  # 模块边界必须可综合审查
        "Honor the confirmed pipeline requirement.",  # 流水线承诺不能在 RTL 阶段丢失
        "If pipeline_required is true, do not emit a non-pipelined design.",  # 强制流水线需求优先于简化实现
        (
            "Generate a self-checking Verilog testbench with explicit success/failure behavior "
            "and unified human-readable `$display` prefixes that mirror the Python "
            "semantic model's verification vectors."
        ),
        (
            f"The testbench must emit one machine-readable transcript line per case using "
            f"the prefix `{SEMANTIC_RESULT_TAG}` followed by one JSON object."
        ),
    ]

    # 向量合同规则只有在 Python semantic 阶段已经产出合同时才追加。
    list_rules.extend(_vector_contract_rules(dict_vector_contract))

    # RTL 阶段复用完整生成规则中的 bus、时序、约束、样式和注释要求。
    list_rules.extend(_rtl_generation_rules(dict_spec, str_comment_language))

    # 阶段通用可验证性规则放在末尾，提醒输出可交接。
    list_rules.extend(list_common_rules)

    # RTL 阶段输出要同时满足可综合代码和自检 testbench 合同。
    return (
        "RTL implementation generation",
        "Create synthesizable Verilog-2001 artifacts using the reference behavior as the semantic contract.",
        list_rules,
    )

# _stage_manifest_for 生成 staged workflow 的输出 manifest。
def _stage_manifest_for(dict_spec: dict[str, Any], str_stage: str) -> dict[str, Any]:
    """生成指定 staged prompt 阶段的 manifest 合同。

    参数:
        dict_spec: 当前子功能范围计划。
        str_stage: 已校验的阶段名称。

    返回:
        包含 target、name、stage、top、files 和 checks 的 manifest 字典。
    """

    # list_files 根据阶段固定产物或规格输出计算。
    list_files = _stage_files(dict_spec, str_stage)  # 当前阶段文件合同

    # 返回 staged manifest，顶层字段保持旧版 schema。
    return {
        "target": "rtl",  # 生成目标
        "name": dict_spec["name"],  # manifest 关联的规格名称
        "stage": str_stage,  # workflow 用于恢复和 trace 的阶段键
        "top": dict_spec["name"],  # 顶层模块名
        "files": list_files,  # 阶段文件清单
        "checks": _checks_template(),  # 检查项模板
    }

# _stage_files 返回指定阶段的文件清单。
def _stage_files(dict_spec: dict[str, Any], str_stage: str) -> list[dict[str, Any]]:
    """返回指定阶段应生成的文件清单。

    参数:
        dict_spec: 当前子功能范围计划。
        str_stage: 已校验的阶段名称。

    返回:
        manifest.files 字段使用的文件对象列表。
    """

    # str_design_name 统一用于计划、模型和审查文件命名。
    str_design_name = str(dict_spec["name"])  # 阶段文件名前缀

    # 单文件阶段通过映射保持旧版 path/kind/language。
    dict_single_file_by_stage = {  # 规划阶段到固定产物路径的映射
        "requirements": (f"plan/{str_design_name}_requirements.json", "requirements", "json"),  # 需求合同
        "codegen_plan": (f"plan/{str_design_name}_codegen_plan.json", "codegen_plan", "json"),  # 生成前结构化计划
        "tests": (f"plan/{str_design_name}_test_plan.json", "test_plan", "json"),  # 测试计划
        "decompose": (f"plan/{str_design_name}_decomposition.json", "decomposition", "json"),  # 拆分计划
        "augment": (f"plan/{str_design_name}_information_dictionary.json", "information_dictionary", "json"),  # 信息字典
        "review": (f"review/{str_design_name}_plan_review.md", "plan_review", "markdown"),  # 人工可读计划审查
        "pseudocode": (f"plan/{str_design_name}_pseudocode.md", "pseudocode", "markdown"),  # reference 行为草图
    }

    # Python 阶段需要同时产出 semantic model 和向量 JSON。
    if str_stage == "python":

        # 返回 Python semantic 双文件合同。
        return [
            {"path": f"model/{str_design_name}_model.py", "kind": "reference_model", "language": "python"},
            {"path": f"model/{str_design_name}_vectors.json", "kind": "reference_vectors", "language": "json"},
        ]

    # 映射命中的规划阶段返回单文件 manifest。
    if str_stage in dict_single_file_by_stage:

        # tuple_file_spec 保存 path、kind 和 language 三元组。
        tuple_file_spec = dict_single_file_by_stage[str_stage]  # 单文件阶段规格

        # 返回单文件阶段 manifest.files。
        return [{"path": tuple_file_spec[0], "kind": tuple_file_spec[1], "language": tuple_file_spec[2]}]

    # 默认阶段使用规格中声明的输出文件。
    return [_manifest_file_from_output(dict_output) for dict_output in dict_spec["outputs"]]

# _manifest_for 生成完整 RTL prompt 的 manifest。
def _manifest_for(dict_spec: dict[str, Any]) -> dict[str, Any]:
    """生成完整 RTL prompt 的 manifest 合同。

    参数:
        dict_spec: normalize_spec 输出的规范化规格。

    返回:
        包含 target、name、top、files 和 checks 的 manifest 字典。
    """

    # list_files 逐个规范化规格输出条目。
    list_files = [_manifest_file_from_output(dict_output) for dict_output in dict_spec["outputs"]]  # 输出文件清单

    # 返回完整 RTL manifest，字段形状保持旧版。
    return {
        "target": "rtl",
        "name": dict_spec["name"],
        "top": dict_spec["name"],
        "files": list_files,
        "checks": _checks_template(),
    }

# _manifest_file_from_output 把 spec 输出项转成 manifest 文件项。
def _manifest_file_from_output(dict_output: dict[str, Any]) -> dict[str, Any]:
    """把规格输出项转换为 manifest.files 条目。

    参数:
        dict_output: normalize_spec 输出中的单个 outputs 条目。

    返回:
        包含 path、kind 和 language 的 manifest 文件字典。
    """

    # str_path 是 manifest 和 code fence 必须精确匹配的相对路径。
    str_path = str(dict_output["path"])  # 输出文件相对路径

    # 返回单个 manifest 文件对象。
    return {
        "path": str_path,  # 文件相对路径
        "kind": dict_output.get("kind", "source"),  # 文件用途类型
        "language": dict_output.get("language", _language_from_path(str_path)),  # fenced-code 语言
    }

# _checks_template 返回 manifest 中的 checks 字段模板。
def _checks_template() -> dict[str, list[str]]:
    """返回 manifest checks 字段的空模板。

    参数:
        无外部业务参数。

    返回:
        所有检查分类映射到空字符串列表的字典。
    """

    # 返回检查项模板，键名保持 workflow 旧版合同。
    return {
        "spec_coverage": [],  # 规格覆盖证据
        "verification_plan": [],  # 验证计划证据
        "execution_plan": [],  # 执行计划证据
        "implementation_assessment": [],  # 实现可行性评估
        "reviewability_assessment": [],  # 可审查性评估
        "assumptions": [],  # 假设列表
        "known_limitations": [],  # 已知限制
    }

# _comment_rules_for 返回生成 RTL 的注释约束。
def _comment_rules_for(str_target: str, str_comment_language: str) -> list[str]:
    """返回指定输出目标的注释约束。

    参数:
        str_target: 输出目标类型，目前保留兼容参数。
        str_comment_language: 已校验的注释语言代码。

    返回:
        注释语言、放置和审查性规则列表。
    """

    # str_target 当前不分流规则，保留参数以兼容旧版 helper 调用。
    del str_target

    # 中文注释路径要求生成 RTL 默认中文说明。
    if str_comment_language == "zh":

        # str_language_rule 是中文注释语言约束。
        str_language_rule = (
            "Use Chinese comments by default; signal names, protocol names, tool names, "
            "and identifiers may remain in English."
        )  # 中文注释语言边界

        # str_rtl_labels 是三段式 FSM 的中文标签集合。
        str_rtl_labels = "`状态寄存器`, `次态逻辑`, and `输出逻辑`"  # 中文 FSM 标签

    # 英文注释路径禁止生成中文 prose。
    else:

        # str_language_rule 是英文注释语言约束。
        str_language_rule = "Use English comments only; do not use Chinese prose in generated comments."  # 英文注释规则

        # str_rtl_labels 是三段式 FSM 的英文标签集合。
        str_rtl_labels = "`State register`, `Next-state logic`, and `Output logic`"  # 英文 FSM 标签

    # 返回注释质量和放置规则。
    return [
        str_language_rule,
        (
            "Hard gate: every non-empty generated Verilog code line in RTL and testbench `.v` "
            "files must have a same-line explanatory comment in the requested comment language; "
            "blank lines and pure comment lines are the only exemptions."
        ),
        (
            "Comment placement is semantic, not decorative: module, macro, parameter, port, "
            "signal, assign, always, case/FSM branch, instance, generate, task/function, "
            "and testbench constructs must use `references/rules/verilog-comment-placement.md`."
        ),
        (
            "Pure leading comments may introduce blocks such as always, instances, generate branches, "
            "multiline macros, and testbench task/function helpers, but they never replace the required "
            "same-line comment on ordinary code statements."
        ),
        (
            "If you add a pure comment to introduce an `assign` subgroup, keep exactly one blank line "
            "above it unless it directly follows a region banner or a stacked pure-comment group."
        ),
        (
            "Inside `case(state_current)`, every `ST_*:begin` and `default:begin` branch must have a "
            "pure leading comment immediately above it and aligned with the branch label."
        ),
        (
            "Do not use generic filler comments such as `逐行中文注释`, `泛泛注释`, `这里处理逻辑`, "
            "`reset`, `state task`, `bypass path`, or placeholder wording; every comment must name "
            "the target construct, signal, condition, or verification purpose."
        ),
        f"If the RTL uses an FSM, it must use a three-block FSM style with fixed comment labels {str_rtl_labels}.",
        (
            "Use the manifest `checks.reviewability_assessment` field to summarize comment coverage, "
            "FSM structure, and any reviewability limitation."
        ),
    ]

# _rtl_md_constraint_rules 返回 Markdown 约束摘要。
def _rtl_md_constraint_rules() -> list[str]:
    """返回 RTL Markdown 约束摘要规则。

    参数:
        无外部业务参数。

    返回:
        单元素列表，元素为可放入 prompt 的约束摘要文本。
    """

    # 返回约束摘要列表，调用方可直接 extend 到规则序列。
    return [summarize_constraints_for_prompt(max_rules_per_group=5)]

# _rtl_style_rules 返回 erie_strict 风格规则。
def _rtl_style_rules(dict_spec: dict[str, Any], str_comment_language: str) -> list[str]:
    """返回规格启用 erie_strict 时的 RTL 风格规则。

    参数:
        dict_spec: 当前 RTL 规格或子功能计划。
        str_comment_language: 已校验的注释语言代码。

    返回:
        erie_strict 规则列表；未启用该 profile 时返回空列表。
    """

    # 非 erie_strict 规格不追加本地严格样式约束。
    if str(dict_spec.get("rtl_style_profile") or "").lower() != "erie_strict":

        # 返回空列表表示没有额外样式约束。
        return []

    # str_inline_language 让 prompt 中的说明语言和注释语言保持一致。
    str_inline_language = "Chinese" if str_comment_language == "zh" else "English"  # 内联说明语言

    # 返回 erie_strict RTL 风格合同。
    return [
        "Apply the `erie_strict` RTL style profile as a hard generation constraint.",
        "Use Tab characters for all RTL indentation; do not use four-space indentation for code blocks.",
        "Use the fixed bilingual header literal contract below exactly; keep spelling, alignment, blank lines, "
        "path casing, and choose only one global Referrences/Dependencies mode:\n"
        + prompt_header_contract_text(),
        f"Use `{str_inline_language}` as the default language for inline explanatory prose outside the header.",
        (
            "Use low-active reset naming and conventions such as `i_rstn`, `i_axi_arstn`, "
            "`i_axis_arstn`, `i_ahb_hrstn`, and `i_apb_prstn` according to the bus type."
        ),
        (
            "Use clock naming conventions such as `i_clk`, `i_axi_aclk`, `i_axis_aclk`, "
            "`i_ahb_hclk`, and `i_apb_pclk` according to the bus type."
        ),
        (
            "Never declare module ports with `reg` or `wire` keywords; drive outputs through "
            "internal `_o` signals plus explicit `assign` statements."
        ),
        "Use `i_` for input ports, `o_` for output ports, and `io_` for bidirectional ports.",
        "Group port declarations into annotated interface regions.",
        "Use `C_` uppercase names for module parameters and `ST_` uppercase names for state parameters.",
        (
            "Use the required signal prefixes: `reg_`, `cnt_`, `state_`, `flag_`, `enc_`, "
            "`dec_`, and the `_o` suffix for internal output logic signals."
        ),
        "Do not duplicate signal prefixes.",
        (
            "When an FSM is present, use explicit `state_current` and `state_next` registers, "
            "`ST_*` localparams for every encoded state, and pure leading comments above each "
            "`ST_*:begin` and `default:begin` branch."
        ),
        (
            "Generated next-state logic must use blocking `state_next = ...;` assignments in its "
            "own combinational always block, must default to `state_next = state_current;`, and "
            "must never use a continuous assign for `state_next`."
        ),
        (
            "Expand every continuous and procedural combinational target through its complete "
            "module-local dependency cone. At most three runtime source references are allowed; "
            "moving logic into a combinational always block does not bypass this limit."
        ),
        (
            "Prefer exact output bridges such as `assign o_done = done_o;`, where `done_o` is a "
            "clocked internal reg. Only that single-signal bridge shape is exempt from the "
            "three-source combinational-cone limit."
        ),
        "Split sequential logic so that each `always` block assigns exactly one reg signal.",
        "Do not use `wire xxx = ...;`; declare the wire first and use a separate `assign` statement.",
        (
            "Follow the fixed region order: parameters, state parameters, instantiation signals, "
            "counters, state signals, regs, flags, encoders, decoders, other signals, output signals, "
            "other assigns, output assigns, output processing, FSM, state transition processing, "
            "main datapath processing, generate blocks, initialization, module instantiation, "
            "and finally parameter check when it exists."
        ),
        "Place the `参数检查区域` as the final internal region before `endmodule`.",
        "Only emit `参数检查区域` when the spec or validated constraints provide "
        "concrete parameter checks; never emit an empty shell block.",
        "Human-readable Verilog `$display` text must use ` > INFO: [Verilog]`, "
        "` > WARNING: [Verilog]`, or ` > ERR: [Verilog]`.",
        "Machine-readable transcript tags such as `VERILOG-GEN-RESULT` stay "
        "exempt from the human-readable prefix rule.",
        "Treat AXI, AXIS, APB, AHB, UART, SPI, and I2C as first-class standardized bus families.",
        (
            "For repeated or grouped interfaces, treat explicit group comments as the truth source, "
            "keep one explanatory comment immediately above each group, and leave exactly one blank line "
            "between adjacent groups."
        ),
        (
            "`输出信号`, `输出信号连线`, and `输出信号处理区域` must mirror the module interface "
            "group labels and output signal order exactly."
        ),
        "Prefer module instance names ending with `_Inst` and named `generate` labels beginning with `gen_`.",
        (
            "Preserve a header that includes version/revision/history fields, including version, "
            "revision date, and revision history, in both the English and Chinese sections."
        ),
        "For AXI/AXIS/APB/AHB interfaces, group ports by channel and role with nearby comments.",
        "If an FSM is present, implement a three-block state machine that matches the template exactly.",
    ]

# _design_requirements_context 整理 staged prompt 可见的需求上下文。
def _design_requirements_context(dict_spec: dict[str, Any]) -> dict[str, Any]:
    """整理 staged prompt 中的设计需求上下文。

    参数:
        dict_spec: 当前子功能范围计划。

    返回:
        包含需求、接口、模板和 RTL 风格字段的字典。
    """

    # dict_interface_template 反映本地接口模板选择结果。
    dict_interface_template = _interface_template_context(dict_spec)  # 已选择或失败的总线模板材料

    # dict_use_case_template_summary 是 use-case 模板的短摘要。
    dict_use_case_template_summary = summarize_use_case_template(  # 需求上下文展示的板级模板摘要
        _use_case_template_context(dict_spec)  # 依据规格选择出的 ADC/DAC 用例模板
    )  # 板级 use-case 的压缩摘要

    # list_pattern_templates 是精炼 RTL 模板摘要。
    list_pattern_templates = _pattern_template_context(dict_spec)  # 模式模板摘要列表

    # 返回需求上下文字段，键名保持旧版 prompt JSON 形状。
    return {
        "design_requirements": dict_spec.get("design_requirements", {}),  # 用户确认需求
        "pipeline_required": dict_spec.get("pipeline_required"),  # 流水线要求
        "streamability": dict_spec.get("streamability"),  # 流式化分类
        "interface_family": dict_spec.get("interface_family"),  # 接口族
        "interface_profile": dict_spec.get("interface_profile", {}),  # 接口画像
        "selected_interface_template_id": _selected_template_id(dict_interface_template),  # 接口模板 id
        "selected_use_case_template_id": dict_use_case_template_summary.get("id"),  # use-case 模板 id
        "use_case_template": dict_use_case_template_summary,  # ADC/DAC 场景模板摘要
        "selected_pattern_template_ids": [dict_item["template_id"] for dict_item in list_pattern_templates],  # 模式模板 id
        "pattern_templates": list_pattern_templates,  # 模式模板摘要
        "rtl_dialect": "verilog",  # RTL 方言
        "rtl_style_profile": dict_spec.get("rtl_style_profile"),  # RTL 风格 profile
    }

# _selected_template_id 安全读取接口模板 id。
def _selected_template_id(dict_interface_template: dict[str, Any] | None) -> Any:
    """读取接口模板摘要中的 template_id。

    参数:
        dict_interface_template: 接口模板上下文或 None。

    返回:
        template_id 字段值；缺少模板时返回 None。
    """

    # 没有模板上下文时直接返回 None。
    if not dict_interface_template:

        # None 表示没有选中接口模板。
        return None

    # 返回接口模板 id。
    return dict_interface_template.get("template_id")

# _interface_template_context 选择本地接口模板。
def _interface_template_context(dict_spec: dict[str, Any]) -> dict[str, Any] | None:
    """选择并返回本地接口模板上下文。

    参数:
        dict_spec: 当前 RTL 规格或子功能计划。

    返回:
        接口模板摘要字典；没有匹配或选择失败时返回错误摘要。
    """

    # 模板选择失败必须进入 prompt，提醒模型不要猜测接口。
    try:

        # 返回成功选择的接口模板。
        return select_interface_template(dict_spec)

    # 接口模板错误转换为 prompt 可见结构，不在渲染阶段抛出。
    except InterfaceTemplateError as exc:

        # 返回选择失败摘要，后续 formatter 会生成阻断提示。
        return {
            "template_id": None,  # 未选中模板
            "interface_family": dict_spec.get("interface_family"),  # 请求的接口族
            "selection_error": str(exc),  # 选择失败原因
            "content": "",  # 失败时没有模板正文
        }

# _use_case_template_context 选择本地 use-case 模板。
def _use_case_template_context(dict_spec: dict[str, Any]) -> dict[str, Any] | None:
    """选择并返回本地 use-case 模板上下文。

    参数:
        dict_spec: 当前 RTL 规格或子功能计划。

    返回:
        use-case 模板摘要字典；选择失败时返回错误摘要。
    """

    # use-case 模板选择失败也写入 prompt，避免模型静默发明模板。
    try:

        # 返回成功选择的 use-case 模板。
        return select_use_case_template(dict_spec)

    # use-case 模板错误转换为 prompt 可见结构。
    except UseCaseTemplateError as exc:

        # 失败摘要会在 prompt 中阻止模型继续猜测 use-case。
        return {
            "template_id": None,
            "selection_error": str(exc),
            "artifacts": [],  # 失败时没有模板 artifact
        }

# _pattern_template_context 汇总精炼 RTL 模板。
def _pattern_template_context(dict_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """汇总当前规格适用的精炼 RTL 模板。

    参数:
        dict_spec: 当前 RTL 规格或子功能计划。

    返回:
        summarize_pattern_templates 返回的模板摘要列表。
    """

    # 返回模式模板摘要，具体选择逻辑由 pattern_templates 模块维护。
    return summarize_pattern_templates(dict_spec)

# _format_interface_template_section 渲染接口模板 prompt 段。
def _format_interface_template_section(dict_interface_template: dict[str, Any] | None) -> str:
    """渲染接口模板 prompt 小节。

    参数:
        dict_interface_template: 接口模板上下文或 None。

    返回:
        Markdown 小节文本；没有模板时返回空字符串。
    """

    # 没有接口模板上下文时不输出该小节。
    if not dict_interface_template:

        # 空字符串让主模板保留兼容的段落间距。
        return ""

    # 模板选择失败时输出明确阻断说明。
    if dict_interface_template.get("selection_error"):

        # 返回选择失败提示，要求先修正 interface_profile。
        return (
            "## Interface template\n\n"
            f"Local interface template selection failed: {dict_interface_template['selection_error']}\n"
            "Do not generate RTL until the interface_profile.template_id, role, or read_write_mode is corrected.\n"
        )

    # str_content 是本地接口模板 Verilog 正文。
    str_content = str(dict_interface_template.get("content") or "").rstrip()  # 接口模板正文

    # 空模板正文不产生 prompt 小节。
    if not str_content:

        # 没有正文时返回空字符串。
        return ""

    # dict_metadata 展示模板选择和命名策略。
    dict_metadata = _interface_template_metadata(dict_interface_template)  # 接口模板元数据

    # str_metadata_json 是 prompt 中展示的接口模板元数据。
    str_metadata_json = json.dumps(dict_metadata, indent=2, ensure_ascii=False)  # 端口模板选择证据 JSON

    # 返回接口模板小节。
    return f"""## Interface template

Use this local standard interface template as the preferred port contract for the selected bus.
Preserve these signal names, parameter names, and Chinese comments unless the spec explicitly conflicts
with an existing confirmed port list.
Record any adaptation in the manifest reviewability checks and codegen plan.
If `read_write_mode` is `read` or `write`, use this full template as the naming reference.
Generate only the confirmed direction's required logic.

```json
{str_metadata_json}
```

```verilog
{str_content}
```
"""

# _interface_template_metadata 提取接口模板展示元数据。
def _interface_template_metadata(dict_interface_template: dict[str, Any]) -> dict[str, Any]:
    """提取接口模板 prompt 展示所需的元数据。

    参数:
        dict_interface_template: select_interface_template 返回的模板上下文。

    返回:
        适合 JSON 展示的接口模板元数据字典。
    """

    # 返回元数据字典，避免把完整模板对象直接暴露给 prompt。
    return {
        "template_id": dict_interface_template.get("template_id"),  # 本地接口模板唯一标识
        "interface_family": dict_interface_template.get("interface_family"),  # AXI/APB/AHB 等总线族
        "role": dict_interface_template.get("role"),  # 端口角色
        "read_write_mode": dict_interface_template.get("read_write_mode"),  # 读写模式
        "path": dict_interface_template.get("path"),  # 模板来源路径
        "selection_reason": dict_interface_template.get("selection_reason"),  # 选择原因
        "strict_naming_policy": dict_interface_template.get("strict_naming_policy"),  # 命名策略
        "clock": dict_interface_template.get("clock"),  # 时钟约定
        "reset": dict_interface_template.get("reset"),  # 复位约定
        "parameters": dict_interface_template.get("parameters", []),  # 参数列表
        "style_contract": {  # prompt 中展示的接口风格合同
            "port_grouping": "group ports by channel and role",  # 端口分组约束
            "clock_reset_naming": "follow Erie family-specific clock/reset naming",  # 时钟复位命名
            "inline_comment_language": "prefer Chinese explanatory comments",  # 注释语言偏好
        },
    }

# _format_use_case_template_section 渲染 use-case 模板小节。
def _format_use_case_template_section(dict_use_case_template: dict[str, Any] | None) -> str:
    """渲染 use-case 模板 prompt 小节。

    参数:
        dict_use_case_template: use-case 模板上下文或 None。

    返回:
        Markdown 小节文本；没有模板时返回空字符串。
    """

    # 没有 use-case 模板上下文时不输出该小节。
    if not dict_use_case_template:

        # 缺少 use-case 时完整 prompt 仍保持模板段落占位为空。
        return ""

    # use-case 选择失败时把修复字段写清楚，避免生成错误板级结构。
    if dict_use_case_template.get("selection_error"):

        # 返回选择失败提示，要求修正 workflow.use_case_template_id。
        return (
            "## Use-case template\n\n"
            f"Local use-case template selection failed: {dict_use_case_template['selection_error']}\n"
            "Do not generate RTL until workflow.use_case_template_id is corrected.\n"
        )

    # dict_metadata 压缩 use-case 来源、族类和 artifact 清单。
    dict_metadata = summarize_use_case_template(dict_use_case_template)  # use-case 模板元数据

    # str_metadata_json 是 prompt 中展示的 use-case 模板元数据。
    str_metadata_json = json.dumps(dict_metadata, indent=2, ensure_ascii=False)  # 板级模板摘要 JSON

    # str_artifacts_text 汇总模板 artifact 内容。
    str_artifacts_text = _use_case_artifact_sections(dict_use_case_template)  # 板级模板源码片段

    # 返回 use-case 模板小节。
    return f"""## Use-case template

Use this local ADC/DAC family template as board-level guidance when the design intent matches the selected use case.
Preserve its family-specific structure, provenance, parameterization points, and sideband expectations unless
the confirmed spec explicitly overrides them.
Record any adaptation in the manifest checks and codegen plan.

```json
{str_metadata_json}
```

{str_artifacts_text}
"""

# _use_case_artifact_sections 渲染 use-case 模板 artifact。
def _use_case_artifact_sections(dict_use_case_template: dict[str, Any]) -> str:
    """渲染 use-case 模板中的 artifact 代码块。

    参数:
        dict_use_case_template: use-case 模板上下文。

    返回:
        所有非空 artifact 拼接后的 Markdown 文本。
    """

    # list_sections 按模板声明顺序保存 artifact 小节。
    list_sections: list[str] = []  # use-case artifact 小节列表

    # 逐个 artifact 渲染代码围栏，空正文跳过。
    for dict_artifact in dict_use_case_template.get("artifacts", []):

        # str_content 是 artifact 原始正文。
        str_content = str(dict_artifact.get("content") or "").rstrip()  # 当前模板 artifact 的原始文本

        # 空 artifact 不应污染 prompt。
        if not str_content:

            # 跳过没有正文的 artifact。
            continue

        # str_relative_path 用于推导 fenced-code 语言。
        str_relative_path = str(  # artifact 来源路径决定 Markdown 围栏语言
            dict_artifact.get("relative_path") or dict_artifact.get("path") or ""  # 模板 artifact 的相对或绝对路径
        )  # 推导代码围栏语言的 artifact 路径

        # str_language 是当前 artifact 的 fenced-code 语言标签。
        str_language = _language_from_path(str_relative_path)  # artifact 语言标签

        # list_sections 追加当前 artifact 小节。
        list_sections.append(f"### {dict_artifact.get('kind')}\n\n```{str_language}\n{str_content}\n```\n")

    # 返回所有 artifact 小节。
    return "\n".join(list_sections)

# _format_pattern_template_section 渲染精炼 RTL 模板小节。
def _format_pattern_template_section(list_pattern_templates: list[dict[str, Any]] | None) -> str:
    """渲染精炼 RTL 设计模式 prompt 小节。

    参数:
        list_pattern_templates: summarize_pattern_templates 返回的模板摘要列表。

    返回:
        Markdown 小节文本；没有模板时返回空字符串。
    """

    # 没有模式模板时不输出该小节。
    if not list_pattern_templates:

        # 没有模式模板时不向模型提供额外设计范式。
        return ""

    # str_metadata_json 展示模式模板摘要列表。
    str_metadata_json = json.dumps(list_pattern_templates, indent=2, ensure_ascii=False)  # 模式模板索引 JSON

    # str_template_sections 渲染每个模板的 Verilog 代码块。
    str_template_sections = _pattern_template_sections(list_pattern_templates)  # 模式模板代码段

    # 返回模式模板小节。
    return (
        "## Verilog pattern templates\n\n"
        "Use these compact local patterns as design hints distilled from the repository reference RTL.\n"
        "Adapt them to the confirmed task instead of copying large reference modules verbatim.\n\n"
        "```json\n"
        + str_metadata_json
        + "\n```\n\n"
        + str_template_sections
    )

# _pattern_template_sections 读取并渲染模式模板正文。
def _pattern_template_sections(list_pattern_templates: list[dict[str, Any]]) -> str:
    """读取模式模板文件并渲染为 Verilog 代码块。

    参数:
        list_pattern_templates: summarize_pattern_templates 返回的模板摘要列表。

    返回:
        所有模板正文拼接后的 Markdown 文本。
    """

    # list_sections 按模板摘要顺序保存代码块。
    list_sections: list[str] = []  # 模式模板 Markdown 小节

    # 逐个读取模板文件，保持旧版 prompt 内嵌正文行为。
    for dict_template in list_pattern_templates:

        # path_template 是本地模式模板文件路径。
        path_template = Path(str(dict_template["path"]))  # 模式模板文件路径

        # str_content 是模板文件正文。
        str_content = path_template.read_text(encoding="utf-8").rstrip()  # 模式模板正文

        # 当前模板正文以 template_id 作为小节标题。
        list_sections.append(f"### {dict_template['template_id']}\n\n```verilog\n{str_content}\n```\n")

    # 返回模板代码块集合。
    return "\n".join(list_sections)

# _vector_contract_rules 返回 reference 向量合同规则。
def _vector_contract_rules(dict_vector_contract: dict[str, Any] | None) -> list[str]:
    """返回 semantic vector 合同对 RTL testbench 的约束。

    参数:
        dict_vector_contract: Python semantic 阶段生成的向量合同或 None。

    返回:
        向量合同规则列表；没有合同时返回空列表。
    """

    # 没有向量合同时不追加额外规则。
    if not dict_vector_contract:

        # 空列表表示无需同步 semantic vector。
        return []

    # 返回向量镜像和哈希注释规则。
    return [
        (
            "Mirror the semantic vector contract exactly: "
            f"case_count={dict_vector_contract.get('case_count')}, "
            f"case_ids={dict_vector_contract.get('case_ids')}."
        ),
        (
            f"Every generated testbench must include an adjacent comment `{VECTOR_HASH_TAG} "
            f"{dict_vector_contract.get('sha256')}` and use the same case ids."
        ),
    ]

# _append_optional_sections 按需追加人工决策小节。
def _append_optional_sections(str_prompt: str, *, decision: dict[str, Any] | None = None, **_: Any) -> str:
    """向 prompt 末尾追加人工决策约束小节。

    参数:
        str_prompt: 已渲染的 prompt 主体。
        decision: 可选人工决策字典。
        _: 兼容旧版调用中传入的其它关键字参数。

    返回:
        原 prompt 或追加决策小节后的 prompt。
    """

    # 没有人工决策时保持 prompt 主体不变。
    if not decision:

        # 返回未修改的 prompt。
        return str_prompt

    # str_decision_json 序列化人工决策，供模型按结构读取。
    str_decision_json = json.dumps(decision, indent=2, ensure_ascii=False)  # 追加到 prompt 末尾的人工约束

    # 返回追加 Human decision constraints 后的 prompt。
    return str_prompt + "\n## Human decision constraints\n\n```json\n" + str_decision_json + "\n```\n"

# _language_from_path 根据文件后缀推导 fenced-code 语言名。
def _language_from_path(str_path: str) -> str:
    """根据文件路径后缀推导 fenced-code 语言名。

    参数:
        str_path: manifest 或 artifact 中的相对路径文本。

    返回:
        fenced-code 语言标签；未知后缀返回 text。
    """

    # str_suffix 是路径后缀的小写形式。
    str_suffix = Path(str_path).suffix.lower()  # 文件后缀

    # 返回映射语言，未知后缀使用 text。
    return LANGUAGE_BY_SUFFIX.get(str_suffix, "text")

# _scope_plan 按可选 subfunction 裁剪计划。
def _scope_plan(dict_plan: dict[str, Any], str_subfunction: str | None) -> dict[str, Any]:
    """按 subfunction 名称裁剪 staged prompt 计划。

    参数:
        dict_plan: decompose_spec 输出的完整计划。
        str_subfunction: 可选子功能名称。

    返回:
        原计划或只包含目标子功能的浅拷贝计划。
    """

    # 没有指定子功能时保留完整计划。
    if not str_subfunction:

        # 返回原计划对象，保持旧版行为。
        return dict_plan

    # dict_scoped_plan 是可安全修改 subfunctions 字段的浅拷贝。
    dict_scoped_plan = dict(dict_plan)  # 子功能裁剪计划

    # list_subfunctions 只保留名称匹配的子功能字典。
    list_subfunctions = [  # 当前 staged prompt 可见子功能
        dict_item  # 子功能计划条目
        for dict_item in dict_plan.get("subfunctions", [])  # 原计划子功能列表
        if isinstance(dict_item, dict) and dict_item.get("name") == str_subfunction  # 名称匹配目标子功能
    ]

    # 裁剪后的子功能列表写回浅拷贝计划。
    dict_scoped_plan["subfunctions"] = list_subfunctions  # 目标子功能列表

    # 返回子功能范围计划。
    return dict_scoped_plan

# _artifact_context 读取上游 artifact 摘要。
def _artifact_context(
    dict_manifest: dict[str, Any] | None,
    path_context_dir: Path | None,
    *,
    budget: str = "normal",
) -> dict[str, Any]:
    """读取上游 artifact 文件并裁剪为 prompt 上下文。

    参数:
        dict_manifest: 上一阶段 manifest 或 None。
        path_context_dir: 上一阶段 artifact 根目录或 None。
        budget: prompt 预算档位。

    返回:
        包含 files 列表的上下文字典；缺少输入时返回空字典。
    """

    # 缺少 manifest 或目录时没有可注入 artifact。
    if not dict_manifest or not path_context_dir:

        # 返回空上下文。
        return {}

    # list_files 保存成功读取并裁剪的上游文件。
    list_files: list[dict[str, Any]] = []  # prompt 可见 artifact 摘要

    # int_limit 根据预算控制每个文件最大注入字符数。
    int_limit = 1200 if budget == "compact" else 5000  # 单文件注入字符上限

    # 逐个 manifest 文件尝试读取对应 artifact。
    for dict_entry in dict_manifest.get("files", []) or []:

        # 非对象或缺少 path 的条目不能映射到本地文件。
        if not isinstance(dict_entry, dict) or not dict_entry.get("path"):

            # 跳过无法解析的 manifest 条目。
            continue

        # path_artifact 是上游 artifact 的候选路径。
        path_artifact = path_context_dir / str(dict_entry["path"])  # 上游 artifact 路径

        # 只有实际存在的普通文件才进入 prompt。
        if path_artifact.exists() and path_artifact.is_file():

            # str_text 读取文本并容忍编码错误，避免单个 artifact 阻断 prompt。
            str_text = path_artifact.read_text(encoding="utf-8", errors="ignore")  # 上游 artifact 文本

            # list_files 记录 path、kind 和裁剪后的正文。
            # dict_file_summary 是 prompt 可见的单个上游 artifact 摘要。
            dict_file_summary = {
                "path": dict_entry["path"],  # manifest 中声明的相对路径
                "kind": dict_entry.get("kind"),  # 上游文件用途
                "text": str_text[:int_limit],  # 裁剪后的文件正文
            }

            # 收集该 artifact，保持 manifest 顺序。
            list_files.append(dict_file_summary)

    # 返回 artifact 上下文字典。
    return {"files": list_files}

# _evidence_context 裁剪 workflow 证据摘要。
def _evidence_context(
    dict_evidence: dict[str, Any] | None,
    dict_plan: dict[str, Any],
    str_budget: str,
) -> dict[str, Any]:
    """裁剪 staged prompt 可见的证据项。

    参数:
        dict_evidence: workflow 证据摘要或 None。
        dict_plan: 当前子功能范围计划，保留兼容参数。
        str_budget: prompt 预算档位。

    返回:
        包含 items 的证据上下文字典；没有证据时返回空字典。
    """

    # dict_plan 当前不参与裁剪，保留参数以兼容旧版调用语义。
    del dict_plan

    # 没有证据时返回空上下文。
    if not dict_evidence:

        # 空 memory 表示本阶段没有历史失败约束可注入。
        return {}

    # int_limit 根据预算控制最多注入多少条证据。
    int_limit = 6 if str_budget == "compact" else 16  # 证据条数上限

    # list_items 只在 evidence 是字典时读取 items。
    list_items = dict_evidence.get("items", []) if isinstance(dict_evidence, dict) else []  # 原始证据条目

    # 返回裁剪后的证据上下文。
    return {"items": list_items[:int_limit]}

# _memory_constraints 返回 prompt memory 约束。
def _memory_constraints(
    dict_memory: dict[str, Any] | None,
    str_stage: str,
    *,
    subfunction: str | None,
    budget: str,
) -> dict[str, Any]:
    """返回当前 staged prompt 可见的 prompt memory 约束。

    参数:
        dict_memory: prompt memory 字典或 None。
        str_stage: 当前阶段名称，保留兼容参数。
        subfunction: 当前子功能名称或 None，保留兼容参数。
        budget: 当前 prompt 预算，保留兼容参数。

    返回:
        原 memory 字典；没有 memory 时返回空字典。
    """

    # 这些参数保留给后续更细粒度 memory 过滤策略。
    del str_stage, subfunction, budget

    # 没有 memory 时返回空约束。
    if not dict_memory:

        # 返回空字典。
        return {}

    # 返回原始 memory，保持旧版全部注入行为。
    return dict_memory

# _decision_context 过滤与当前子功能无关的人工决策。
def _decision_context(dict_options: dict[str, Any]) -> dict[str, Any]:
    """返回适用于当前 subfunction 的人工决策上下文。

    参数:
        dict_options: _prompt_options 输出的渲染选项。

    返回:
        适用的 decision 字典；没有或不适用时返回空字典。
    """

    # dict_decision 是调用方传入的人工决策对象。
    dict_decision = _optional_dict(dict_options.get("decision"))  # 人工决策候选

    # str_subfunction 是当前 staged prompt 的子功能范围。
    str_subfunction = _optional_str(dict_options.get("subfunction"))  # 当前子功能名称

    # decision_applies 负责判断人工决策是否应用于该子功能。
    if decision_applies(dict_decision, str_subfunction):

        # 返回适用的人工决策对象。
        return dict_decision

    # 不适用时不向 prompt 注入该决策。
    return {}

# _optional_dict 只保留字典形态的可选输入。
def _optional_dict(obj_value: Any) -> dict[str, Any] | None:
    """把任意值收敛为可选字典。

    参数:
        obj_value: 调用方传入的任意上下文值。

    返回:
        输入本身或 None。
    """

    # 只有 dict 能作为结构化上下文进入内部 helper。
    if isinstance(obj_value, dict):

        # 返回原始字典，保持字段不变。
        return obj_value

    # 非字典输入视为未提供。
    return None

# _optional_path 只保留 Path 形态的可选输入。
def _optional_path(obj_value: Any) -> Path | None:
    """把任意值收敛为可选 Path。

    参数:
        obj_value: 调用方传入的上下文目录候选。

    返回:
        输入 Path 或 None。
    """

    # 只有 Path 对象能作为上下文目录直接使用。
    if isinstance(obj_value, Path):

        # 返回原始 Path。
        return obj_value

    # 字符串目录不在这里隐式转换，避免改变调用边界。
    return None

# _optional_str 只保留字符串形态的可选输入。
def _optional_str(obj_value: Any) -> str | None:
    """把任意值收敛为可选字符串。

    参数:
        obj_value: 调用方传入的字符串候选。

    返回:
        输入字符串或 None。
    """

    # 空字符串和非字符串都视为未提供。
    if isinstance(obj_value, str) and obj_value:

        # 返回原始字符串。
        return obj_value

    # 非有效字符串输入视为未提供。
    return None

# _bullet_lines 把规则序列转成 Markdown bullet 文本。
def _bullet_lines(list_rules: list[str]) -> str:
    """把规则字符串列表渲染为 Markdown bullet 列表。

    参数:
        list_rules: 规则字符串列表。

    返回:
        每行以短横线开头的 Markdown 文本。
    """

    # 每个规则独占一行，方便模型逐条遵守。
    return "\n".join(f"- {str_rule}" for str_rule in list_rules)
