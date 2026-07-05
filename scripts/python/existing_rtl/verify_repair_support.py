"""verify-repair 流程的输入规整、验证规格和日志诊断辅助函数。"""

# 延迟类型求值让 helper 之间可以保持轻量导入。
from __future__ import annotations

# 文件操作和路径类型用于 staging workspace。
import shutil
from pathlib import Path
from typing import Any

# 现有 RTL 分析入口负责加载用户规格文本。
from .existing_rtl import load_spec_text

# spec 名称净化和验证入口保持原有 wire contract。
from scripts.python.workflow.spec import sanitize_name
from scripts.python.validation.validation import READINESS_LEVELS, require_readiness

# 自动化模式定义写回权限边界。
AUTOMATION_MODES = ("conservative", "semi_auto", "auto_apply")  # verify-repair 允许的自动化模式

# testbench 模式定义 generate/augment 两条受控路径。
TB_MODES = ("generate", "augment")  # testbench 处理模式

# testbench 语言限定 Verilog 与 SystemVerilog 两种后端可解释形式。
TB_LANGUAGES = ("verilog", "systemverilog")  # testbench 输出语言集合

# automation mode 是越权保护边界，必须在入口处收敛。
def require_automation_mode(str_value: str) -> str:
    """规范化 verify-repair 的自动化模式。

    参数:
        str_value: CLI 或 facade 传入的自动化模式文本。

    返回:
        返回可写入验证计划和策略判断的标准小写模式。

    异常:
        ValueError: 当模式不属于保守、半自动或自动应用集合时抛出。
    """

    # 小写模式值用于兼容 CLI 和 facade 传入的大小写差异。
    str_normalized = str_value.lower()  # 规范化后的自动化模式

    # 未声明模式不能继续进入可能写文件的修复流程。
    if str_normalized not in AUTOMATION_MODES:

        # 明确列出允许值，保持旧错误消息可读。
        raise ValueError(f"> ERR: [Python] automation_mode must be one of {', '.join(AUTOMATION_MODES)}.")

    # 返回稳定模式文本供下游策略判断。
    return str_normalized

# tb_mode 决定 testbench 是生成还是增强既有文件。
def require_tb_mode(str_value: str) -> str:
    """规范化 testbench 处理模式。

    参数:
        str_value: CLI 或 facade 传入的 testbench 处理模式。

    返回:
        返回 `generate` 或 `augment` 的标准小写文本。

    异常:
        ValueError: 当模式不属于受支持的 testbench 处理路径时抛出。
    """

    # 小写模式值匹配内部常量。
    str_normalized = str_value.lower()  # 规范化后的 testbench 模式

    # 只允许受控的生成和增强两条路径。
    if str_normalized not in TB_MODES:

        # 入口错误必须阻断后续文件操作。
        raise ValueError(f"> ERR: [Python] tb_mode must be one of {', '.join(TB_MODES)}.")

    # 返回可直接写入报告的模式文本。
    return str_normalized

# tb_language 控制 Verilog/SystemVerilog testbench 生成边界。
def require_tb_language(str_value: str) -> str:
    """规范化 testbench 语言选择。

    参数:
        str_value: CLI 或 facade 传入的 testbench 语言文本。

    返回:
        返回验证流程可识别的标准 testbench 语言标签。

    异常:
        ValueError: 当语言不属于 Verilog/SystemVerilog 支持集合时抛出。
    """

    # 内部语言分支统一使用小写字符串。
    str_normalized = str_value.lower()  # 规范化后的 testbench 语言

    # 只允许本 skill 声明支持的 testbench 语言。
    if str_normalized not in TB_LANGUAGES:

        # 语言越界会影响仿真后端参数，必须提前失败。
        raise ValueError(f"> ERR: [Python] tb_language must be one of {', '.join(TB_LANGUAGES)}.")

    # 返回标准语言标签。
    return str_normalized

# 入口参数规整集中在一个函数里，避免主流程重复校验。
def normalize_verify_options(
    *,
    str_automation_mode: str,
    str_tb_mode: str,
    str_tb_language: str,
    str_readiness: str,
) -> tuple[str, str, str, str]:
    """规范化 verify-repair 的四个策略型入口参数。

    参数:
        str_automation_mode: 控制自动写回权限的模式文本。
        str_tb_mode: 控制 testbench 生成或增强的模式文本。
        str_tb_language: 控制 testbench 后端语言的文本。
        str_readiness: 控制静态、编译或执行阶段的 readiness 文本。

    返回:
        返回自动化模式、testbench 模式、语言和 readiness 的标准化四元组。
    """

    # 自动化模式决定是否允许自动写回 RTL/TB。
    str_checked_automation_mode = require_automation_mode(str_automation_mode)  # 安全后的自动化模式

    # testbench 模式决定 generate/augment 分支。
    str_checked_tb_mode = require_tb_mode(str_tb_mode)  # 安全后的 testbench 模式

    # testbench 语言决定生成 `.v` 还是 `.sv` 语义。
    str_checked_tb_language = require_tb_language(str_tb_language)  # 安全后的 testbench 语言

    # readiness 复用 validation 模块的层级规则。
    str_checked_readiness = require_readiness(str_readiness)  # 安全后的 readiness 级别

    # 返回固定顺序供 core 解包。
    return (
        str_checked_automation_mode,
        str_checked_tb_mode,
        str_checked_tb_language,
        str_checked_readiness,
    )

# 用户规格文本可能来自 markdown、JSON 或调用方已组装对象。
def load_optional_spec_text(spec_source: str | Path | dict[str, Any] | None) -> str | None:
    """读取可选规格文本，缺失时保持 None。

    参数:
        spec_source: 用户传入的规格路径、文本、结构化字典或空值。

    返回:
        返回 loader 解析后的规格文本；输入缺失时返回 None。
    """

    # 复用既有 loader，保证 dict/path/string 三种输入兼容。
    str_spec_text = load_spec_text(spec_source)  # 供分析和验证计划使用的规格文本

    # 返回 None 或文本，不在这里做语义解释。
    return str_spec_text

# RTL staging 复制源文件到验证 workspace，避免直接在源目录跑验证。
def stage_sources(list_source_paths: list[Path], path_target_dir: Path) -> list[Path]:
    """复制 RTL 源文件到验证 workspace。

    参数:
        list_source_paths: 待复制的原始 RTL 源文件路径列表。
        path_target_dir: verify-repair workspace 中接收 staged RTL 的目录。

    返回:
        返回复制后供 validation 读取的 workspace 内路径列表。
    """

    # staging 目录必须存在，后续 validation 使用相对路径。
    path_target_dir.mkdir(parents=True, exist_ok=True)

    # 收集复制后的 workspace 路径。
    list_staged_paths: list[Path] = []  # validation workspace 中的 RTL 路径

    # 逐个复制源文件，保留文件名以匹配 include 和报告展示。
    for path_source in list_source_paths:

        # 目标路径只改变目录，不改变文件名。
        path_target = path_target_dir / path_source.name  # 当前源文件的 staging 目标

        # 复制到隔离 workspace。
        shutil.copyfile(path_source, path_target)

        # 记录 validation 后续读取的路径。
        list_staged_paths.append(path_target)

    # 返回所有 staged RTL 文件。
    return list_staged_paths

# 行为规格摘要写入 verification_plan，便于人工复核目标。
def spec_summary(str_spec_text: str | None) -> str:
    """生成外部行为说明的短摘要。

    参数:
        str_spec_text: 用户提供的外部行为说明文本，允许为空。

    返回:
        返回 verification_plan 中展示的单行规格摘要。
    """

    # 缺失规格时给出显式说明，避免空字符串误读为已覆盖。
    if not str_spec_text:

        # 英文文本保持既有报告契约。
        return "No external behavioral note was provided."

    # 压平换行，报告里只保留短摘要。
    str_compact = " ".join(line.strip() for line in str_spec_text.splitlines() if line.strip())  # 单行规格摘要

    # 保持旧摘要长度边界。
    return str_compact[:240]

# 日志 excerpt 只用于报告摘要，不参与真实日志保存。
def excerpt(str_text: str, int_limit: int = 320) -> str:
    """压缩日志文本为报告摘要。

    参数:
        str_text: 原始编译或仿真日志文本。
        int_limit: 摘要允许保留的最大字符数。

    返回:
        返回压平换行后的截断日志摘要。
    """

    # 去掉空行并压成单行，便于 JSON 报告展示。
    str_compact = " ".join(line.strip() for line in str_text.splitlines() if line.strip())  # 单行日志摘要

    # 按旧逻辑截断，避免报告膨胀。
    return str_compact[:int_limit]

# 文件名约定用于从多源输入里自动识别 testbench。
def is_testbench(path_source: Path) -> bool:
    """判断路径是否像 testbench 文件。

    参数:
        path_source: 待识别的源文件路径。

    返回:
        返回文件名是否符合 testbench 命名启发式。
    """

    # stem 小写后匹配常见 TB 命名。
    str_stem = path_source.stem.lower()  # 不含后缀的文件名

    # 保持旧启发式：tb_、_tb 或 testbench。
    return str_stem.endswith("_tb") or str_stem.startswith("tb_") or "testbench" in str_stem

# 多源输入拆成 RTL 源和可选 testbench。
def split_sources(
    list_source_paths: list[Path],
    *,
    path_explicit_testbench: Path | None,
) -> tuple[list[Path], Path | None]:
    """拆分 RTL 源文件和 testbench 源文件。

    参数:
        list_source_paths: 用户传入的所有 RTL/testbench 候选源文件。
        path_explicit_testbench: 用户显式指定的 testbench 路径。

    返回:
        返回 RTL 源文件列表和最终选择的 testbench 路径。
    """

    # 显式 testbench 优先于文件名自动识别。
    if path_explicit_testbench is not None:

        # 显式 TB 场景下，剩余文件逐个确认后进入 RTL 源集合。
        list_rtl_sources: list[Path] = []  # 显式 TB 分离后的 RTL 源

        # 逐个排除用户指定的 testbench。
        for path_source in list_source_paths:

            # 显式 TB 本身不参与 RTL 分析。
            if path_source.resolve() == path_explicit_testbench.resolve():

                # 跳过 testbench 文件。
                continue

            # 保留真实 RTL 源文件。
            list_rtl_sources.append(path_source)

        # 返回调用方指定的 TB。
        return list_rtl_sources, path_explicit_testbench

    # 未显式传入时使用命名启发式寻找第一个 TB。
    path_detected_tb = next((path_source for path_source in list_source_paths if is_testbench(path_source)), None)  # 自动识别的 TB

    # 自动识别 TB 场景下，逐个源文件决定是否保留为 RTL。
    list_rtl_sources = []  # 自动识别 TB 后的 RTL 源

    # 排除自动检测到的 testbench。
    for path_source in list_source_paths:

        # 命中自动 TB 时不加入 RTL 源。
        if path_detected_tb is not None and path_source.resolve() == path_detected_tb.resolve():

            # 跳过自动识别的 testbench。
            continue

        # 保留用于 RTL 分析的源文件。
        list_rtl_sources.append(path_source)

    # 返回拆分结果。
    return list_rtl_sources, path_detected_tb

# verification_plan 是 verify-repair 的核心人读计划工件。
def build_verification_plan(
    dict_analysis: dict[str, Any],
    *,
    str_spec_text: str | None,
    str_tb_mode: str,
    str_tb_language: str,
    str_automation_mode: str,
) -> dict[str, Any]:
    """根据 RTL 分析结果生成验证计划。

    参数:
        dict_analysis: existing RTL 分析阶段产出的模块、端口和验证目标。
        str_spec_text: 用户提供的外部规格说明文本。
        str_tb_mode: 已标准化的 testbench 处理模式。
        str_tb_language: 已标准化的 testbench 语言。
        str_automation_mode: 已标准化的自动化修复模式。

    返回:
        返回可写入 `verification_plan.json` 的兼容旧 schema 字典。
    """

    # focus signals 让诊断报告能指出最值得看波形的端口。
    list_focus_signals: list[str] = []  # 验证计划关注信号

    # 按分析顺序保留信号，重复信号只出现一次。
    for dict_mapping in dict_analysis.get("feature_mappings", []):

        # 每个 mapping 可能有多个 pin assignment。
        for dict_assignment in dict_mapping.get("pin_assignments", []):

            # pin_name 为空时跳过。
            str_signal = str(dict_assignment.get("pin_name") or "")  # 当前候选关注信号

            # 保留首次出现的有效信号。
            if str_signal and str_signal not in list_focus_signals:

                # 追加供报告和波形诊断使用的信号名。
                list_focus_signals.append(str_signal)

    # 返回保持旧 schema 的 verification plan。
    return {
        "version": 1,
        "top_module": dict_analysis["module_info"]["name"],
        "tb_mode": str_tb_mode,
        "tb_language": str_tb_language,
        "automation_mode": str_automation_mode,
        "focus_signals": list_focus_signals,
        "verification_targets": dict_analysis.get("verification_targets", []),
        "user_focus_summary": spec_summary(str_spec_text),
    }

# validation spec 把 existing RTL workspace 伪装成生成结果供统一 validator 复用。
def validation_spec(
    dict_analysis: dict[str, Any],
    list_staged_sources: list[Path],
    str_testbench_rel_path: str,
) -> dict[str, Any]:
    """构造 validate_generated 可消费的规格对象。

    参数:
        dict_analysis: existing RTL 分析阶段产出的模块、端口和验证目标。
        list_staged_sources: 已复制到 workspace 的 RTL 源文件路径。
        str_testbench_rel_path: validation spec 中记录的 testbench 相对路径。

    返回:
        返回 `validate_generated` 可消费的生成结果兼容规格字典。
    """

    # outputs 先放 RTL 源文件，再补 testbench。
    list_outputs: list[dict[str, str]] = []  # validation spec 的输出文件列表

    # staged source 用 workspace 相对路径描述。
    for path_source in list_staged_sources:

        # 每个 RTL 文件保持 Verilog 源文件类型。
        list_outputs.append(
            {
                "path": f"rtl/{path_source.name}",
                "kind": "source",
                "language": "verilog",
            }
        )

    # testbench 作为验证入口交给 validator。
    list_outputs.append(
        {
            "path": str_testbench_rel_path,
            "kind": "testbench",
            "language": "verilog",
        }
    )

    # semantic checkpoints 逐项投影，保留分析阶段推断的 check_id。
    list_semantic_checkpoints: list[dict[str, Any]] = []  # validation 用于语义比较的 checkpoint 列表

    # 将每个 verification target 转成 validator 所需字段。
    for int_index, dict_item in enumerate(dict_analysis.get("verification_targets", [])):

        # 单个 checkpoint payload 保持 validator 字段名。
        dict_checkpoint = {
            "id": dict_item.get("check_id", f"checkpoint_{int_index + 1}"),  # validator 语义点稳定标识
            "category": dict_item.get("category", "behavior"),  # validator 语义点归类
            "signals": dict_item.get("signals", []),  # 参与语义比对的信号集合
            "verification_hint": dict_item.get("description", ""),  # 修复判断面向人的提示文本
            "text": dict_item.get("description", ""),  # 语义比对复用的目标描述
        }  # 单个语义 checkpoint payload

        # 追加到 validation spec 的 checkpoint 列表。
        list_semantic_checkpoints.append(dict_checkpoint)

    # 返回统一 validator 的输入结构。
    return {
        "name": sanitize_name(str(dict_analysis["module_info"]["name"])),
        "target": "rtl",
        "rtl_dialect": "verilog",
        "description": "Existing RTL verify-repair staged validation workspace.",
        "interfaces": {"ports": dict_analysis.get("ports", [])},
        "behavior": [
            dict_item.get("description", dict_item.get("name", ""))
            for dict_item in dict_analysis.get("verification_targets", [])
        ],
        "clock": {
            "name": next(
                (dict_item["name"] for dict_item in dict_analysis.get("ports", []) if dict_item.get("role") == "clock"),
                "clk",
            )
        },
        "reset": {
            "name": next(
                (dict_item["name"] for dict_item in dict_analysis.get("ports", []) if dict_item.get("role") == "reset"),
                "rst_n",
            )
        },
        "constraints": ["Preserve existing RTL behavior while validating staged testbench coverage."],
        "outputs": list_outputs,
        "semantic_checkpoints": list_semantic_checkpoints,
    }

# validation report 的 issue 列表转成诊断日志输入。
def diagnostic_inputs(
    obj_validation_report: Any,
    *,
    str_readiness: str,
    bool_run_external: bool,
) -> tuple[str, str, bool]:
    """从 validation report 提取编译日志、仿真日志和执行状态。

    参数:
        obj_validation_report: validator 返回的报告对象，需提供 issues 和 ok()。
        str_readiness: 当前 verify-repair 允许推进到的验证阶段。
        bool_run_external: 是否允许执行外部编译或仿真。

    返回:
        返回编译日志文本、仿真日志文本和外部仿真是否执行的布尔值。
    """

    # compile stage issue 组成编译日志。
    list_compile_lines: list[str] = []  # validator 记录的编译阶段问题

    # 逐条提取 compile issue，保持 issue.format() 的旧格式。
    for obj_issue in obj_validation_report.issues:

        # 只收集 compile 阶段问题。
        if obj_issue.stage == "compile":

            # 编译问题保留验证器标准人读文本。
            list_compile_lines.append(obj_issue.format())

    # execute stage issue 组成仿真日志。
    list_simulation_lines: list[str] = []  # validator 记录的仿真阶段问题

    # 逐条提取 execute issue，避免把 compile 诊断混进仿真日志。
    for obj_issue in obj_validation_report.issues:

        # 仿真阶段问题单独汇入 simulation transcript。
        if obj_issue.stage == "execute":

            # format() 保留验证报告中的 stage/code/message。
            list_simulation_lines.append(obj_issue.format())

    # 只有外部验证被允许时才解析 readiness，保持旧短路异常语义。
    if bool_run_external:

        # 当前 readiness 在固定阶段表中的位置。
        int_readiness_index = READINESS_LEVELS.index(str_readiness)  # 当前验证准备阶段序号

        # execute 是允许写入仿真执行 transcript 的最低阶段。
        int_execute_index = READINESS_LEVELS.index("execute")  # execute 阶段序号

        # readiness 达到 execute 及以上才视为外部执行发生。
        bool_readiness_allows_execute = int_readiness_index >= int_execute_index  # readiness 是否允许执行

    # 未请求外部验证时不能把 validator 状态记为仿真执行。
    else:

        # 禁止外部执行路径生成 PASS transcript。
        bool_readiness_allows_execute = False  # 未运行外部验证时的执行状态

    # execute readiness 及以上才允许把外部仿真状态记为已执行。
    bool_executed = bool_run_external and bool_readiness_allows_execute  # 外部仿真是否真实完成执行阶段

    # 执行且 validator 通过时补充标准 PASS transcript。
    if bool_executed and obj_validation_report.ok():

        # 保持旧测试依赖的完成标签。
        list_simulation_lines.append("[TB_INFO] Simulation Finished!")

        # 保持机器可读 PASS 结果。
        list_simulation_lines.append('VERILOG-GEN-RESULT {"case_id":"nominal","status":"PASS","outputs":{}}')

    # 返回三元组供诊断分类。
    return "\n".join(list_compile_lines), "\n".join(list_simulation_lines), bool_executed

# 日志文本规则集中到私有 helper，主诊断函数只负责组装报告。
def _classify_log_outcome(
    *,
    str_compile_lower: str,
    str_simulation_lower: str,
    bool_executed: bool,
) -> str:
    """按旧优先级分类编译和仿真日志。

    参数:
        str_compile_lower: 已转成小写的编译日志。
        str_simulation_lower: 已转成小写的仿真日志。
        bool_executed: 外部仿真是否真实执行。

    返回:
        返回 verify-repair 诊断报告使用的 outcome 文本。
    """

    # 未执行外部仿真时不能推断真实 pass/fail。
    if not bool_executed:

        # 静态验证不提供真实仿真日志，因此只标记未运行。
        return "not_run"

    # 编译错误优先级最高。
    if any(token in str_compile_lower for token in ("syntax error", "** error", "fatal", "compile error")):

        # 编译失败优先提示修复语法或文件组织。
        return "compile_error"

    # testbench 错误标签代表 assertion 或显式检查失败。
    if "[tb_error]" in str_simulation_lower:

        # TB_ERROR 表示 testbench 检查明确失败。
        return "assertion_fail"

    # protocol violation 是协议/握手类诊断。
    if "protocol violation" in str_simulation_lower:

        # 协议违例需要优先查看握手或时序关系。
        return "protocol_violation"

    # timeout 文本代表活性或收敛问题。
    if "timeout" in str_simulation_lower:

        # timeout 暗示仿真活性或结束条件异常。
        return "timeout"

    # 标准完成标签和机器 PASS 同时出现才算 pass。
    if "[tb_info] simulation finished!" in str_simulation_lower and '"status":"pass"' in str_simulation_lower.replace(
        " ",
        "",
    ):

        # PASS 必须同时有完成标记和机器结果。
        return "pass"

    # unknown 保守交给人工复核，不触发自动补丁。
    return "unknown"

# 日志诊断是 verify-repair 的轻量分类器。
def diagnose_log_texts(*, compile_log: str, simulation_log: str, executed: bool) -> dict[str, Any]:
    """根据编译和仿真日志分类 verify-repair 结果。

    参数:
        compile_log: validator 或外部工具提供的编译日志文本。
        simulation_log: validator 或外部工具提供的仿真日志文本。
        executed: 外部仿真是否真实执行。

    返回:
        返回包含 outcome、日志摘要和 finding 的诊断报告字典。
    """

    # 小写日志用于大小写无关的模式匹配。
    str_compile_lower = compile_log.lower()  # 编译日志小写视图

    # 仿真日志同样转为小写视图。
    str_simulation_lower = simulation_log.lower()  # 仿真日志小写视图

    # 复用私有 helper 保持分类优先级集中。
    str_outcome = _classify_log_outcome(  # 日志分类 outcome
        str_compile_lower=str_compile_lower,  # 分类用编译日志
        str_simulation_lower=str_simulation_lower,  # 分类用仿真日志
        bool_executed=executed,  # 外部执行状态
    )

    # 诊断 finding 由 outcome 映射成人读说明。
    list_findings = _finding_for_outcome(str_outcome)  # 报告使用的诊断说明

    # 返回旧 schema 兼容的诊断 payload。
    return {
        "version": 1,
        "executed": executed,
        "outcome": str_outcome,
        "compile_log_excerpt": excerpt(compile_log),
        "simulation_log_excerpt": excerpt(simulation_log),
        "findings": list_findings,
    }

# outcome 文本映射为中文 finding，报告层不重复写分支。
def _finding_for_outcome(str_outcome: str) -> list[str]:
    """返回日志分类对应的人读诊断说明。

    参数:
        str_outcome: `_classify_log_outcome` 产出的诊断分类文本。

    返回:
        返回 verify-repair 报告中展示的人读 finding 列表。
    """

    # 编译错误需要先修正语法或文件组织。
    if str_outcome == "compile_error":

        # 返回单条 finding 保持旧 payload shape。
        return ["编译阶段发现错误，需要先修复语法或文件组织问题。"]

    # assertion 失败通常来自 TB 检查或显式错误。
    if str_outcome == "assertion_fail":

        # 返回仿真错误说明。
        return ["仿真期间出现断言或显式 TB 错误。"]

    # 协议违例要提示握手或时序关系。
    if str_outcome == "protocol_violation":

        # 返回协议违例说明。
        return ["日志显示握手或协议行为违例。"]

    # timeout 表示仿真没有收敛。
    if str_outcome == "timeout":

        # 返回超时说明。
        return ["仿真未按预期收敛，出现超时。"]

    # pass 只在机器结果和完成标签都存在时出现。
    if str_outcome == "pass":

        # 返回成功说明。
        return ["日志显示仿真收敛并给出 PASS 结果。"]

    # not_run 是静态验证路径。
    if str_outcome == "not_run":

        # 返回未执行外部仿真的说明。
        return ["当前流程未执行外部仿真，仅完成静态验证和工件生成。"]

    # unknown 交给人工复核。
    return ["日志未能归类到已知模式，需要人工复核。"]
