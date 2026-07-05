"""VerilogFormatter 的只读 RTL 评分辅助函数。

该模块只根据源码文本计算风险、格式化收益和结构化改写风险，不生成改写后的 RTL。
评分结果用于在微格式化、保持原样和受控结构化改写之间做保守决策。
"""

# 未来注解保持运行时轻量，避免类型提示在导入阶段产生额外依赖。
from __future__ import annotations

# 正则库负责所有 Verilog 文本的轻量结构识别。
import re

# Path 类型用于在报告中保留可选源码路径。
from pathlib import Path

# Any 保留配置和 JSON 报告的开放字段形态。
from typing import Any

# 评分报告保持 dict 形态，兼容现有 JSON/wire shape。
ScoreReport = dict[str, Any]  # 单文件评分报告类型别名

# 微格式化动作不改变 RTL 结构，只允许排版类输出变化。
MICRO_TRANSFORMS = (  # 可在低风险路径中开放的格式化动作
    "indent",  # 缩进整理动作
    "spacing",  # 空格整理动作
    "blank_lines",  # 空行整理动作
    "trailing_whitespace",  # 尾随空白清理动作
    "comment_alignment",  # 注释对齐动作
    "ordered_header_layout",  # 模块头布局整理动作
)

# 结构化动作默认阻断，避免只读评分阶段误触发语义改写。
STRUCTURAL_TRANSFORMS = (  # 需要更强语义保护的结构化改写动作
    "port_sort",  # 端口排序动作
    "param_sort",  # 参数排序动作
    "signal_rename",  # 信号重命名动作
    "region_reorder",  # 区域重排动作
    "always_split",  # always 拆分动作
    "output_bridge",  # 输出桥接动作
    "inline_wire_rewrite",  # inline wire 改写动作
    "header_rebuild",  # 模块头重建动作
    "reset_synthesis",  # reset 合成动作
)

# 入口函数聚合单个 Verilog 文本的所有评分维度。
def score_verilog_source(source: str, source_path: Path | None, config: dict[str, Any]) -> ScoreReport:
    """生成单个 Verilog-like 源码的 JSON 可序列化评分报告。

    参数:
        source: 待评分的 Verilog/SystemVerilog 源码文本。
        source_path: 源码路径；内存文本可传入 None。
        config: formatter 配置字典，读取其中的 scoring.thresholds 覆盖阈值。

    返回:
        返回保持既有字段名的评分报告，供 formatter 调度层消费。
    """

    # 指标字典先汇总文本结构，后续硬门禁和风险标记都复用同一份证据。
    dict_metrics = _collect_metrics(source)  # RTL 文本结构指标

    # 硬门禁只记录明显不平衡或无法安全解析的文本状态。
    list_hard_gates = _collect_hard_gates(source, dict_metrics)  # 阻断写回的硬失败原因

    # 诊断列表保留 lint 级别风险，不直接改变返回字段结构。
    list_diagnostics = _collect_diagnostics(source, dict_metrics)  # 软件可显示的诊断项

    # 风险标记用于压低改写收益，避免高风险 RTL 被自动结构化。
    set_risk_flags = _collect_risk_flags(source, dict_metrics)  # 结构化改写风险标签集合

    # metrics 中继续暴露排序后的风险标签，兼容原有报告字段。
    dict_metrics["risk_flags"] = sorted(set_risk_flags)  # 报告中稳定排序的风险标签

    # 语法置信度综合硬门禁和高风险结构，控制是否允许继续格式化。
    int_syntax_confidence = _syntax_confidence(  # 语法可信度评分
        dict_metrics,  # 结构指标输入
        list_hard_gates,  # 硬门禁输入
        set_risk_flags,  # 风险标签输入
    )

    # 格式评分只惩罚排版和命名类问题，不表示结构化改写收益。
    int_format_score = _format_score(source, dict_metrics)  # 微格式化标准度评分

    # 改写需求评分衡量结构问题收益，但会被风险评分继续约束。
    int_rewrite_need_score = _rewrite_need_score(  # 结构化改写需求评分
        source,  # 原始 RTL 文本
        dict_metrics,  # 改写收益依赖的结构指标
        set_risk_flags,  # 限制收益上限的风险标签
    )

    # 改写风险评分用于阻断自动结构化输出。
    int_rewrite_risk_score = _rewrite_risk_score(dict_metrics, set_risk_flags)  # 结构化改写风险评分

    # 阈值从配置中读取，缺省时由 _decision 内部使用保守默认值。
    dict_thresholds = config.get("scoring", {}).get("thresholds", {})  # 评分阈值配置

    # 决策字符串保持既有枚举，避免上游分发逻辑需要同步调整。
    str_decision = _decision(  # formatter 后续处理决策
        hard_fail=bool(list_hard_gates),  # 硬失败布尔值
        syntax_confidence=int_syntax_confidence,  # 语法可信度
        format_score=int_format_score,  # 微格式化评分
        rewrite_need_score=int_rewrite_need_score,  # 改写需求评分
        rewrite_risk_score=int_rewrite_risk_score,  # 改写风险评分
        thresholds=dict_thresholds,  # 配置阈值
    )

    # 结构化动作始终进入阻断列表，保持本模块只读评分边界。
    list_blocked_transforms = list(STRUCTURAL_TRANSFORMS)  # 当前评分阶段禁止的动作

    # 硬门禁存在时连微格式化也不开放，避免对不平衡 RTL 写回。
    list_allowed_transforms = [] if list_hard_gates else list(MICRO_TRANSFORMS)  # 当前允许的动作

    # 返回字段名保持原样，保护外部 JSON 消费方。
    return {
        "file": str(source_path) if source_path is not None else "",
        "syntax_confidence": int_syntax_confidence,
        "format_score": int_format_score,
        "rewrite_need_score": int_rewrite_need_score,
        "rewrite_risk_score": int_rewrite_risk_score,
        "decision": str_decision,
        "allowed_transforms": list_allowed_transforms,
        "blocked_transforms": list_blocked_transforms,
        "hard_fail": bool(list_hard_gates),
        "hard_gates": list_hard_gates,
        "diagnostics": list_diagnostics,
        "metrics": dict_metrics,
    }

# 注释和字符串剥离让结构统计尽量不受文本内容干扰。
def _strip_comments_and_strings(source: str) -> str:
    """移除注释和字符串内容，保留轻量语法统计所需的主体文本。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本。

    返回:
        返回替换注释和字符串后的文本，用于正则结构统计。
    """

    # 块注释先移除，避免其中的 // 或字符串字面量影响后续规则。
    str_text = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)  # 去掉块注释后的文本

    # 行注释再移除，避免注释中的关键字被误计为 RTL 结构。
    str_text = re.sub(r"//.*", " ", str_text)  # 去掉行注释后的文本

    # 字符串内容替换为空字符串字面量，避免关键字统计误伤消息文本。
    str_text = re.sub(r'"(?:\\.|[^"\\])*"', '""', str_text)  # 去掉字符串内容后的文本

    # 返回供指标统计使用的净化文本。
    return str_text

# 指标收集函数集中维护报告 metrics 字段。
def _collect_metrics(source: str) -> dict[str, Any]:
    """收集只读评分需要的 Verilog 结构和排版指标。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本。

    返回:
        返回保持原有键名的指标字典。
    """

    # 净化后的代码用于结构关键字统计，降低注释和字符串误判。
    str_code = _strip_comments_and_strings(source)  # 去注释和字符串后的 Verilog 文本

    # 原始行列表用于保留行数、尾随空白和注释行统计。
    list_lines = source.splitlines()  # 原始源码行列表

    # 模块名列表用于检测多模块和 module/endmodule 是否匹配。
    list_module_names = re.findall(  # 源码中声明的模块名
        r"(?m)^\s*module\s+([A-Za-z_]\w*)\b",  # 模块声明名模式
        str_code,  # 去注释后的 RTL 文本
    )

    # 端口名用于命名规范和 output redeclare 风险统计。
    list_ports = _extract_port_names(str_code)  # 模块头端口名列表

    # always 块列表用于重置、拆分风险和多目标赋值统计。
    list_always_blocks = _extract_always_blocks(str_code)  # always 块文本列表

    # 每个 always 块的赋值目标集合用于识别多目标 always。
    list_always_target_sets = [
        _extract_always_targets(str_block)  # 当前 always 块的赋值目标集合
        for str_block in list_always_blocks  # 当前 always 块文本
    ]  # always 块赋值目标集合列表

    # 复杂左值数量用于识别不可安全拆分的寄存器赋值。
    int_complex_lvalue_count = _count_complex_lvalues(str_code)  # 复杂左值赋值数量

    # 预处理开启数量用于和 `endif 数量配对检查。
    int_preprocessor_open = len(  # 预处理条件开启数量
        re.findall(r"(?m)^\s*`(?:ifdef|ifndef|if)\b", source)  # 条件编译开启匹配
    )

    # 预处理关闭数量用于识别缺失 `endif 的硬失败。
    int_preprocessor_close = len(  # 预处理条件关闭数量
        re.findall(r"(?m)^\s*`endif\b", source)  # 条件编译关闭匹配
    )

    # 返回 metrics 字典，键名保持与原报告兼容。
    return {
        "line_count": len(list_lines),
        "module_count": len(list_module_names),
        "module_names": list_module_names,
        "endmodule_count": len(re.findall(r"\bendmodule\b", str_code)),
        "begin_count": len(re.findall(r"\bbegin\b", str_code)),
        "end_count": len(re.findall(r"\bend\b", str_code)),
        "case_count": len(re.findall(r"\b(?:case|casez|casex)\b", str_code)),
        "endcase_count": len(re.findall(r"\bendcase\b", str_code)),
        "generate_count": len(re.findall(r"\bgenerate\b", str_code)),
        "endgenerate_count": len(re.findall(r"\bendgenerate\b", str_code)),
        "preprocessor_open_count": int_preprocessor_open,
        "preprocessor_close_count": int_preprocessor_close,
        "port_names": list_ports,
        "unprefixed_port_count": sum(
            1
            for str_name in list_ports
            if not re.match(r"^(?:i_|o_|io_)", str_name)
        ),
        "always_count": len(list_always_blocks),
        "multi_target_always_count": sum(
            1
            for set_targets in list_always_target_sets
            if len(set_targets) > 1
        ),
        "case_always_multi_target_count": sum(
            1
            for str_block, set_targets in zip(list_always_blocks, list_always_target_sets)
            if len(set_targets) > 1 and re.search(r"\bcase", str_block)
        ),
        "inline_wire_assign_count": len(re.findall(r"\bwire\b[^;\n]*=", str_code)),
        "complex_lvalue_count": int_complex_lvalue_count,
        "function_task_initial_count": len(
            re.findall(r"\b(?:function|task|initial)\b", str_code)
        ),
        "generate_or_preprocessor_count": int_preprocessor_open
        + len(re.findall(r"\bgenerate\b", str_code)),
        "vendor_instance_count": _count_vendor_instances(str_code),
        "comment_line_count": sum(
            1
            for str_line in list_lines
            if str_line.strip().startswith("//")
        ),
        "trailing_whitespace_count": sum(
            1
            for str_line in list_lines
            if str_line.rstrip() != str_line
        ),
        "dense_statement_lines": sum(
            1
            for str_line in list_lines
            if str_line.count(";") >= 2 or ("module " in str_line and ");" in str_line)
        ),
        "missing_reset_always_count": _count_missing_reset_always(list_always_blocks),
        "output_redecl_count": _count_output_redecls(str_code, list_ports),
    }

# 模块头端口提取服务于命名和 redeclare 风险统计。
def _extract_port_names(code: str) -> list[str]:
    """从模块头部提取端口名列表。

    参数:
        code: 已剥离注释和字符串的 Verilog 文本。

    返回:
        返回按模块头顺序排列的端口名列表；未找到模块头时返回空列表。
    """

    # 端口名保持列表顺序，便于报告和后续统计复用。
    list_names: list[str] = []  # 按模块头顺序保留的端口名

    # 模块头匹配只覆盖轻量场景，复杂语法由硬风险保守处理。
    match_header = re.search(  # 模块头正则匹配结果
        r"\bmodule\s+[A-Za-z_]\w*\s*(?:#\s*\(.*?\)\s*)?\((?P<ports>.*?)\)\s*;",  # 模块头端口段模式
        code,  # 模块头搜索使用的净化 RTL 文本
        re.DOTALL,  # 允许端口跨多行
    )

    # 没有模块头时不猜测端口，避免从正文误提取标识符。
    if not match_header:

        # 空端口列表表示该文件无法用轻量模块头规则识别。
        return list_names

    # 逗号切分足够覆盖本评分器的轻量模块头场景。
    for str_raw_part in match_header.group("ports").split(","):

        # 每段先去掉外围空白，保留最后一个真实标识符作为端口名。
        str_part = str_raw_part.strip()  # 当前端口声明片段

        # 空片段通常来自尾随逗号，直接跳过以保持列表干净。
        if not str_part:

            # 尾随逗号不代表一个真实端口。
            continue

        # 标识符提取会同时拿到 direction/type/name，后续过滤关键字。
        list_identifiers = re.findall(r"[A-Za-z_]\w*", str_part)  # 当前片段内的标识符

        # 方向和类型关键字不是端口名，最后保留的标识符才是端口候选。
        list_identifiers = [
            str_item  # 当前片段内保留下来的端口候选
            for str_item in list_identifiers  # 当前候选标识符
            if str_item not in {"input", "output", "inout", "wire", "reg", "logic", "signed"}  # 排除端口声明关键字
        ]  # 去掉声明关键字后的标识符

        # 有候选时取最后一个，兼容 `input wire [7:0] data` 形态。
        if list_identifiers:

            # 最后一个标识符作为该声明片段的端口名。
            list_names.append(list_identifiers[-1])

    # 返回从模块头提取出的端口名列表。
    return list_names

# always 块切分为后续 reset 和多目标统计提供原始片段。
def _extract_always_blocks(code: str) -> list[str]:
    """提取轻量 always 块文本片段。

    参数:
        code: 已剥离注释和字符串的 Verilog 文本。

    返回:
        返回每个 always 到下一个 always 或 endmodule 前的文本片段。
    """

    # always 块列表保留原片段，供多个统计函数复用。
    list_blocks: list[str] = []  # 按源码顺序保留的 always 片段

    # 正则以保守边界切分，不尝试完整解析 Verilog 语法树。
    pattern_regex_always_block: re.Pattern[str] = re.compile(  # always 块轻量匹配器
        r"\balways\b(?P<body>.*?)(?=\balways\b|\bendmodule\b|$)",  # always 片段边界模式
        re.DOTALL,  # 允许 always 正文跨多行
    )

    # 每个匹配片段直接进入列表，保持原实现的统计范围。
    for match_block in pattern_regex_always_block.finditer(code):

        # group(0) 包含 always 关键字，后续 header 检查依赖该上下文。
        list_blocks.append(match_block.group(0))

    # 返回所有轻量切分出的 always 块。
    return list_blocks

# always 目标提取用于识别多寄存器赋值风险。
def _extract_always_targets(block: str) -> set[str]:
    """提取一个 always 块内被赋值的左值基础名。

    参数:
        block: 单个 always 块文本。

    返回:
        返回赋值目标基础名集合。
    """

    # 集合去重后用于判断一个 always 块是否驱动多个目标。
    set_targets: set[str] = set()  # always 块内赋值目标集合

    # 行首赋值目标能覆盖常见寄存器赋值和拼接赋值场景。
    for match_assignment in re.finditer(
        r"(?m)^\s*(?P<lhs>(?:\{[^;]+?\}|[A-Za-z_]\w*(?:\s*\[[^\]]+\])?))\s*(?:<=|=)",
        block,
    ):

        # 左值去空白后继续区分拼接和普通信号。
        str_lhs = match_assignment.group("lhs").strip()  # 当前赋值左值文本

        # 拼接赋值需要收集其中所有标识符，否则会低估拆分风险。
        if str_lhs.startswith("{"):

            # 拼接左值内的每个标识符都算一个被驱动目标。
            for str_name in re.findall(r"[A-Za-z_]\w*", str_lhs):

                # 目标名加入集合，重复赋值不增加目标数量。
                set_targets.add(str_name)

            # 拼接左值已经处理完，跳过普通基础名解析。
            continue

        # 普通左值只取基础名，忽略位选和数组下标。
        match_base = re.match(r"([A-Za-z_]\w*)", str_lhs)  # 普通左值基础名匹配

        # 匹配成功时记录基础信号名。
        if match_base:

            # 基础名加入目标集合，用于多目标 always 判定。
            set_targets.add(match_base.group(1))

    # 返回该 always 块的赋值目标集合。
    return set_targets

# 复杂左值计数用于阻断高风险 always 拆分。
def _count_complex_lvalues(code: str) -> int:
    """统计拼接或位选左值赋值的数量。

    参数:
        code: 已剥离注释和字符串的 Verilog 文本。

    返回:
        返回复杂左值赋值行数量。
    """

    # 计数器记录拼接赋值和带下标赋值的行数。
    int_count = 0  # 拼接或位选左值命中次数

    # 只统计行首赋值形态，保持原轻量评分器的保守范围。
    for match_lvalue in re.finditer(
        r"(?m)^\s*(?P<lhs>(?:\{[^;]+?\}|[A-Za-z_]\w*\s*\[[^\]]+\]))\s*(?:<=|=)",
        code,
    ):

        # 左值文本用于区分拼接和位选。
        str_lhs = match_lvalue.group("lhs")  # 当前复杂左值候选

        # 拼接或位选都会提升结构化改写风险。
        if str_lhs.startswith("{") or "[" in str_lhs:

            # 当前行计入复杂左值数量。
            int_count += 1  # 当前复杂左值行计入风险数量

    # 返回复杂左值计数。
    return int_count

# vendor/IP 实例计数用于提高结构化改写风险。
def _count_vendor_instances(code: str) -> int:
    """统计常见 vendor/IP 原语或实例名数量。

    参数:
        code: 已剥离注释和字符串的 Verilog 文本。

    返回:
        返回命中 vendor/IP 模式的次数。
    """

    # vendor 模式覆盖 Xilinx XPM、RAM/FIFO/BUF 等常见原语。
    tuple_vendor_patterns = (  # vendor/IP 轻量识别正则集合
        r"\bxpm_[A-Za-z0-9_]+\b",  # XPM 宏或实例名称
        r"\b(?:RAMB\d+|FIFO\d+|IBUF|OBUF|BUFG|IDELAY|ISERDESE?\d*)\b",  # Xilinx 常见原语名称
    )

    # 所有模式命中数量求和，保持原实现不去重的风险权重。
    return sum(len(re.findall(str_pattern, code)) for str_pattern in tuple_vendor_patterns)

# reset 缺失统计用于生成 lint 诊断。
def _count_missing_reset_always(always_blocks: list[str]) -> int:
    """统计没有明显 reset 分支的时序 always 块数量。

    参数:
        always_blocks: `_extract_always_blocks` 返回的 always 块文本列表。

    返回:
        返回疑似缺少 reset 的时序 always 块数量。
    """

    # 计数器只记录 posedge 时序块中看不到 reset 的场景。
    int_missing = 0  # 疑似缺少 reset 的 always 块数量

    # 每个 always 块独立判断，避免不同块之间互相污染。
    for str_block in always_blocks:

        # header 只取 begin 前内容，降低正文 reset 文本对时钟边沿判断的影响。
        str_header = str_block.split("begin", 1)[0]  # always 头部文本

        # 非 posedge 块不按时序 reset 缺失处理。
        if "posedge" not in str_header:

            # 组合逻辑或其它 always 形态不进入该诊断。
            continue

        # 同步 reset 或异步 negedge reset 都可降低缺失风险。
        if "negedge" not in str_header and not re.search(r"\bif\s*\(\s*!?\w*rst\w*", str_block):

            # 没有明显 reset 分支时追加计数。
            int_missing += 1  # 当前时序块计入 reset 缺失数量

    # 返回疑似缺少 reset 的时序块数量。
    return int_missing

# output redeclare 统计用于识别 bridge 候选风险。
def _count_output_redecls(code: str, ports: list[str]) -> int:
    """统计 output 端口被 reg/wire/logic 重新声明的次数。

    参数:
        code: 已剥离注释和字符串的 Verilog 文本。
        ports: 模块头端口名列表。

    返回:
        返回疑似 output redeclare 的端口数量。
    """

    # output 名称集合来自声明和 o_ 前缀端口，覆盖两类常见风格。
    set_output_names: set[str] = set()  # output 端口名集合

    # output 声明片段提取最后一个标识符作为端口名。
    for match_output in re.finditer(r"\boutput\b(?P<decl>[^,;)]*)", code):

        # 声明关键字不是端口名，需要先过滤。
        list_ids = [
            str_item  # output 声明中的端口候选
            for str_item in re.findall(r"[A-Za-z_]\w*", match_output.group("decl"))  # output 声明候选
            if str_item not in {"reg", "wire", "logic", "signed"}  # 排除 output 类型关键字
        ]  # output 声明片段中的候选标识符

        # 有候选时取最后一个作为 output 名称。
        if list_ids:

            # output 名称加入集合，用于后续 redeclare 查询。
            set_output_names.add(list_ids[-1])

    # o_ 前缀端口也作为 output 端口候选，保持原实现的启发式。
    set_output_names.update(str_name for str_name in ports if str_name.startswith("o_"))

    # redeclare 计数按 output 名称逐个查询。
    int_redecls = 0  # output 重新声明数量

    # 每个 output 名称只贡献一次风险计数。
    for str_name in set_output_names:

        # reg/wire/logic 声明里再次出现 output 名称时视为 bridge 候选。
        if re.search(rf"\b(?:reg|wire|logic)\b[^;\n]*\b{re.escape(str_name)}\b", code):

            # 当前 output 名称存在重新声明风险。
            int_redecls += 1  # 当前 output 名称计入重新声明风险

    # 返回 output redeclare 风险数量。
    return int_redecls

# 硬门禁收集不可安全写回的语法级风险。
def _collect_hard_gates(source: str, metrics: dict[str, Any]) -> list[str]:
    """收集会阻断写回的硬失败原因。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本。
        metrics: `_collect_metrics` 生成的指标字典。

    返回:
        返回硬失败标签列表。
    """

    # 硬门禁列表保持字符串标签，兼容原报告字段。
    list_gates: list[str] = []  # 硬失败原因列表

    # 块注释数量不平衡时，轻量分析无法可靠剥离注释。
    if source.count("/*") != source.count("*/"):

        # 未闭合块注释直接阻断写回。
        list_gates.append("unclosed_block_comment")

    # 字符串未闭合会破坏后续关键字统计。
    if _has_unclosed_string(source):

        # 未闭合字符串直接阻断写回。
        list_gates.append("unclosed_string")

    # module/endmodule 数量不匹配时，文件结构不完整。
    if metrics["module_count"] != metrics["endmodule_count"]:

        # 模块边界不平衡时不允许格式化写回。
        list_gates.append("module_endmodule_unbalanced")

    # 多类成对关键字共享同一个平衡检查入口。
    for str_open_key, str_close_key, str_label in (
        ("begin_count", "end_count", "begin_end_unbalanced"),
        ("case_count", "endcase_count", "case_endcase_unbalanced"),
        ("generate_count", "endgenerate_count", "generate_endgenerate_unbalanced"),
        ("preprocessor_open_count", "preprocessor_close_count", "preprocessor_unbalanced"),
    ):

        # 开闭数量不一致意味着结构改写风险过高。
        if metrics[str_open_key] != metrics[str_close_key]:

            # 对应不平衡标签加入硬门禁。
            list_gates.append(str_label)

    # 返回所有硬失败标签。
    return list_gates

# 字符串闭合检查避免正则统计跨行误判。
def _has_unclosed_string(source: str) -> bool:
    """判断源码中是否存在跨行未闭合字符串。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本。

    返回:
        返回 True 表示发现疑似未闭合字符串。
    """

    # 块注释状态跨行保留，避免注释中的引号触发字符串状态。
    bool_in_block_comment = False  # 是否处于块注释内部

    # 逐行扫描可以保持原实现的轻量状态机边界。
    for str_line in source.splitlines():

        # 转义状态只在当前行内有效。
        bool_escaped = False  # 当前字符是否被反斜杠转义

        # 字符串状态也按当前行判断，跨行未闭合即为风险。
        bool_in_string = False  # 当前行是否处于字符串内部

        # 下标用于支持多字符注释边界判断。
        int_index = 0  # 当前扫描位置

        # 逐字符扫描当前行，维持注释和字符串状态。
        while int_index < len(str_line):

            # 块注释内只寻找闭合符号。
            if bool_in_block_comment:

                # 闭合块注释后恢复普通扫描。
                if str_line.startswith("*/", int_index):

                    # 块注释结束，跳过闭合符。
                    bool_in_block_comment = False  # 块注释状态复位

                    # 扫描位置越过块注释闭合符，避免重复读取星号和斜杠。
                    int_index += 2  # 跳过 `*/` 的两个字符

                    # 当前行继续从闭合符后扫描。
                    continue

                # 块注释普通字符不参与字符串判断。
                int_index += 1  # 块注释内部普通字符推进一位

                # 块注释未闭合前不执行字符串扫描。
                continue

            # 非字符串状态下识别块注释开启。
            if not bool_in_string and str_line.startswith("/*", int_index):

                # 块注释开始后引号不再影响字符串状态。
                bool_in_block_comment = True  # 进入跨字符块注释状态

                # 扫描位置越过块注释开启符，避免下一轮重复命中。
                int_index += 2  # 越过块注释开启符

                # 块注释内容交给下一轮状态处理。
                continue

            # 行注释只在普通文本状态下截断当前行扫描。
            if not bool_in_string and str_line.startswith("//", int_index):

                # 行注释后内容不参与字符串闭合判断。
                break

            # 当前字符用于处理转义和引号状态。
            str_char = str_line[int_index]  # 当前扫描字符

            # 前一字符是反斜杠时，当前字符只消耗转义状态。
            if bool_escaped:

                # 转义字符不会改变字符串开闭。
                bool_escaped = False  # 当前转义字符已经消耗

                # 转义后的字符不会改变字符串状态，扫描继续推进。
                int_index += 1  # 跳过被转义字符

                # 转义处理完成后进入下一个字符。
                continue

            # 反斜杠开启一次转义状态。
            if str_char == "\\":

                # 下一个字符被视为转义内容。
                bool_escaped = True  # 下一个字符按转义内容处理

                # 反斜杠本身已经处理，扫描继续推进。
                int_index += 1  # 跳过反斜杠字符

                # 等待下一轮消费被转义字符。
                continue

            # 双引号切换字符串状态。
            if str_char == '"':

                # 字符串进入或退出由同一个状态变量表达。
                bool_in_string = not bool_in_string  # 双引号切换当前行字符串状态

            # 普通字符推进扫描位置。
            int_index += 1  # 当前普通字符扫描完毕

        # 行结束仍在字符串内，说明字符串未在本行闭合。
        if bool_in_string:

            # 轻量评分器不尝试修复跨行字符串，直接报告风险。
            return True

    # 所有行扫描结束后未发现未闭合字符串。
    return False

# 诊断收集提供 lint 级别提示。
def _collect_diagnostics(source: str, metrics: dict[str, Any]) -> list[dict[str, str]]:
    """收集不会直接阻断格式化的诊断信息。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本。
        metrics: `_collect_metrics` 生成的指标字典。

    返回:
        返回诊断字典列表，字段保持原有 code/severity/message。
    """

    # 诊断列表保持字典形态，兼容 JSON 报告消费者。
    list_diagnostics: list[dict[str, str]] = []  # lint 诊断项列表

    # 缺少 reset 的 always 块只提示，不让 formatter 自动合成 reset。
    if metrics["missing_reset_always_count"]:

        # 顺序块 reset 缺失会提升人工审查必要性。
        list_diagnostics.append(
            {
                "code": "missing_reset",
                "severity": "lint",
                "message": "Sequential always block lacks an obvious reset; formatter must not synthesize reset logic.",
            }
        )

    # active-low 命名却比较 1'b1 时提示 reset 极性风险。
    if re.search(r"\bif\s*\(\s*\w*rstn\s*==\s*1'b1", source):

        # 极性疑似不一致时只报告风险，不做自动改写。
        list_diagnostics.append(
            {
                "code": "reset_polarity_risk",
                "severity": "lint",
                "message": "Reset polarity looks inconsistent with active-low naming.",
            }
        )

    # 返回所有 lint 级诊断。
    return list_diagnostics

# 风险标记是结构化改写决策的核心证据。
def _collect_risk_flags(source: str, metrics: dict[str, Any]) -> set[str]:
    """根据指标生成结构化改写风险标签。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本。
        metrics: `_collect_metrics` 生成的指标字典。

    返回:
        返回风险标签集合。
    """

    # 集合用于去重同类风险标签。
    set_flags: set[str] = set()  # 当前源码命中的结构风险标签

    # 多模块文件不适合自动结构化改写。
    if metrics["module_count"] > 1:

        # 多模块标签限制自动改写范围。
        set_flags.add("multi_module")

    # vendor/IP 实例通常带有工具链或参数约束。
    if metrics["vendor_instance_count"]:

        # vendor/IP 标签降低结构化改写意愿。
        set_flags.add("vendor_or_ip_instance")

    # generate 或预处理条件会让文本结构依赖编译时配置。
    if metrics["generate_or_preprocessor_count"]:

        # 条件结构标签阻断激进改写。
        set_flags.add("generate_or_preprocessor")

    # function/task/initial 往往承载非简单组合逻辑。
    if metrics["function_task_initial_count"]:

        # 过程性结构标签提高人工审查权重。
        set_flags.add("function_task_initial")

    # 复杂左值增加 always 拆分和信号重写风险。
    if metrics["complex_lvalue_count"]:

        # 复杂左值标签保护拼接和位选赋值。
        set_flags.add("complex_lvalue")

    # inline wire assign 可能需要结构化提取。
    if metrics["inline_wire_assign_count"]:

        # inline wire 标签表达存在轻量整理收益。
        set_flags.add("inline_wire_assign")

    # case always 多目标或多目标加复杂左值都不适合自动拆分。
    if metrics["case_always_multi_target_count"] or (
        metrics["multi_target_always_count"] and metrics["complex_lvalue_count"]
    ):

        # unsafe always 标签显式阻断 always_split。
        set_flags.add("unsafe_always_split")

    # 丰富人工注释意味着改写要更谨慎，避免破坏说明上下文。
    if metrics["comment_line_count"] >= 10:

        # 人工注释丰富时降低结构化改写积极性。
        set_flags.add("rich_human_comments")

    # output redeclare 可能需要 bridge，但仍应由受控流程处理。
    if metrics["output_redecl_count"]:

        # output bridge 候选只作为风险标签返回。
        set_flags.add("output_bridge_candidate")

    # 返回去重后的风险标签集合。
    return set_flags

# 语法置信度将硬失败和高风险结构折算成百分制。
def _syntax_confidence(metrics: dict[str, Any], hard_gates: list[str], risk_flags: set[str]) -> int:
    """计算轻量语法置信度。

    参数:
        metrics: `_collect_metrics` 生成的指标字典；当前保留用于签名兼容。
        hard_gates: 硬失败标签列表。
        risk_flags: 结构化改写风险标签集合。

    返回:
        返回 0 到 100 的语法置信度。
    """

    # 初始满分再按风险扣减，保持原有评分方向。
    int_score = 100  # 语法置信度初始分

    # 硬门禁会显著降低置信度。
    int_score -= 40 if hard_gates else 0  # 硬门禁扣分

    # generate 或预处理条件降低轻量解析确定性。
    int_score -= 10 if "generate_or_preprocessor" in risk_flags else 0  # 条件结构扣分

    # function/task/initial 降低结构理解确定性。
    int_score -= 10 if "function_task_initial" in risk_flags else 0  # 过程块扣分

    # vendor/IP 实例降低可安全改写置信度。
    int_score -= 10 if "vendor_or_ip_instance" in risk_flags else 0  # 工具原语降低置信度

    # 分数限制在百分制范围内。
    return max(0, min(100, int_score))

# 格式评分只衡量微格式化标准度。
def _format_score(source: str, metrics: dict[str, Any]) -> int:
    """计算微格式化标准度评分。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本。
        metrics: `_collect_metrics` 生成的指标字典。

    返回:
        返回 0 到 100 的格式评分。
    """

    # 初始满分再按格式问题扣减。
    int_score = 100  # 格式标准度初始分

    # 密集语句行通常需要空格、换行和缩进整理。
    int_score -= min(12, metrics["dense_statement_lines"] * 8)  # 密集语句扣分

    # assign 等号缺少空格属于微格式化问题。
    int_score -= min(12, len(re.findall(r"\bassign\b[^;\n]*(?<!\s)=(?!\s)", source)) * 6)  # assign 空格扣分

    # 尾随空白只影响文本格式，不影响 RTL 语义。
    int_score -= min(10, metrics["trailing_whitespace_count"])  # 尾随空白扣分

    # 未带方向前缀的端口降低项目命名一致性。
    int_score -= min(12, metrics["unprefixed_port_count"] * 4)  # 端口命名扣分

    # inline wire assign 说明声明和赋值粘连，降低排版评分。
    int_score -= 10 if metrics["inline_wire_assign_count"] else 0  # 声明赋值粘连降低排版分

    # output redeclare 候选提示 bridge 风险，微格式化评分略降。
    int_score -= 8 if metrics["output_redecl_count"] else 0  # output 重声明扣分

    # 多目标 always 降低自动整理信心。
    int_score -= 8 if metrics["multi_target_always_count"] else 0  # 多目标 always 扣分

    # 极短但密集的源码最高只给 90，避免误判为已标准化。
    if metrics["line_count"] <= 10 and metrics["dense_statement_lines"]:

        # 小文件密集写法仍需要人工关注。
        int_score = min(int_score, 90)  # 极短密集源码的评分上限

    # 格式评分最终裁剪到百分制区间。
    return max(0, min(100, int_score))

# 改写需求评分衡量结构整理收益。
def _rewrite_need_score(source: str, metrics: dict[str, Any], risk_flags: set[str]) -> int:
    """计算结构化改写需求评分。

    参数:
        source: 原始 Verilog/SystemVerilog 源码文本；当前保留用于签名兼容。
        metrics: `_collect_metrics` 生成的指标字典。
        risk_flags: 结构化改写风险标签集合。

    返回:
        返回 0 到 100 的改写需求评分。
    """

    # 初始为零，只在发现结构化收益时累加。
    int_score = 0  # 结构化改写需求初始分

    # inline wire assign 有整理为独立声明/assign 的潜在需求。
    int_score += 20 if metrics["inline_wire_assign_count"] else 0  # inline wire 收益加分

    # 未规范端口名可能需要命名治理。
    int_score += 15 if metrics["unprefixed_port_count"] else 0  # 端口命名收益加分

    # 密集语句越多，整理收益越高。
    int_score += 20 if metrics["dense_statement_lines"] >= 2 else (10 if metrics["dense_statement_lines"] else 0)  # 密集语句收益加分

    # 多目标 always 可能需要拆分，但仍受风险评分控制。
    int_score += 20 if metrics["multi_target_always_count"] else 0  # always 整理收益加分

    # output redeclare 候选可能需要 bridge 处理。
    int_score += 15 if metrics["output_redecl_count"] else 0  # 输出重声明提升桥接治理收益

    # vendor/IP 或多模块场景下，即使有收益也限制自动需求上限。
    if "vendor_or_ip_instance" in risk_flags or "multi_module" in risk_flags:

        # 高风险结构把需求分压到人工审查范围。
        int_score = min(int_score, 30)  # 高风险源码的需求评分上限

    # 改写需求评分最终裁剪到百分制区间。
    return max(0, min(100, int_score))

# 改写风险评分保护复杂 RTL 不被自动结构化。
def _rewrite_risk_score(metrics: dict[str, Any], risk_flags: set[str]) -> int:
    """计算结构化改写风险评分。

    参数:
        metrics: `_collect_metrics` 生成的指标字典；当前保留用于签名兼容。
        risk_flags: 结构化改写风险标签集合。

    返回:
        返回 0 到 100 的改写风险评分。
    """

    # 初始为零，只按风险标签累加。
    int_score = 0  # 结构化改写风险初始分

    # 多模块文件增加跨模块误改风险。
    int_score += 10 if "multi_module" in risk_flags else 0  # 多模块风险加分

    # vendor/IP 实例通常应保持局部文本稳定。
    int_score += 15 if "vendor_or_ip_instance" in risk_flags else 0  # vendor/IP 风险加分

    # generate 和预处理条件对文本改写尤其敏感。
    int_score += 20 if "generate_or_preprocessor" in risk_flags else 0  # 条件结构风险加分

    # 复杂左值增加 always 拆分误判风险。
    int_score += 15 if "complex_lvalue" in risk_flags else 0  # 复杂左值风险加分

    # function/task/initial 需要保守处理。
    int_score += 10 if "function_task_initial" in risk_flags else 0  # 过程块风险加分

    # 丰富人工注释可能绑定原始布局。
    int_score += 5 if "rich_human_comments" in risk_flags else 0  # 人工注释风险加分

    # unsafe always split 是最高优先级结构风险。
    int_score += 40 if "unsafe_always_split" in risk_flags else 0  # 多目标 case always 提升最高风险

    # 改写风险评分最终裁剪到百分制区间。
    return max(0, min(100, int_score))

# 决策函数把评分转换为上游可识别的处理策略。
def _decision(
    *,
    # 硬门禁输入决定是否直接禁止写回。
    hard_fail: bool,
    # 三类评分输入共同决定保持、微格式化或候选改写。
    syntax_confidence: int,
    format_score: int,
    rewrite_need_score: int,
    rewrite_risk_score: int,
    # 阈值输入来自配置，缺省值在函数体内兜底。
    thresholds: dict[str, Any],
) -> str:
    """根据评分和阈值返回 formatter 决策字符串。

    参数:
        hard_fail: 是否存在硬门禁失败。
        syntax_confidence: 语法置信度评分。
        format_score: 微格式化标准度评分。
        rewrite_need_score: 结构化改写需求评分。
        rewrite_risk_score: 结构化改写风险评分。
        thresholds: 配置中读取的 scoring.thresholds 字典。

    返回:
        返回既有决策枚举字符串。
    """

    # 硬失败时禁止写回，保护不完整 RTL。
    if hard_fail:

        # fail_no_write 是上游识别的硬阻断决策。
        return "fail_no_write"

    # 语法置信度低于阈值时只保留警告，不进行结构化候选推荐。
    if syntax_confidence < int(thresholds.get("syntax_preserve_min", 75)):

        # preserve_with_warnings 表示保留文本并提示风险。
        return "preserve_with_warnings"

    # 有改写收益但风险超过自动阈值时，仍然保持保守。
    if rewrite_need_score > 0 and rewrite_risk_score > int(thresholds.get("rewrite_risk_auto_max", 35)):

        # 风险高于收益时只输出警告。
        return "preserve_with_warnings"

    # 已达到标准化阈值时不需要修改。
    if format_score >= int(thresholds.get("already_standard_min", 95)):

        # already_standard 表示无需格式化动作。
        return "already_standard"

    # 微格式化阈值以上只允许安全排版动作。
    if format_score >= int(thresholds.get("micro_format_min", 85)):

        # preserve_micro_format 表示仅开放微格式化。
        return "preserve_micro_format"

    # 保留格式阈值以上不推荐结构化改写。
    if format_score >= int(thresholds.get("preserve_format_min", 70)):

        # preserve_format 表示原布局基本可接受。
        return "preserve_format"

    # 改写需求不足时保持格式，不引入额外结构变化。
    if rewrite_need_score <= int(thresholds.get("rewrite_need_min", 30)):

        # 需求不高时继续保守保持。
        return "preserve_format"

    # 改写风险超过自动阈值时只保留警告。
    if rewrite_risk_score > int(thresholds.get("rewrite_risk_auto_max", 35)):

        # 高风险候选必须由人工或受控流程处理。
        return "preserve_with_warnings"

    # 所有保守门禁通过后才标记为受控标准化候选。
    return "controlled_normalize_candidate"
