"""实现反思、干预解析和评估相关 CLI 子命令。"""

# future annotations 避免 argparse 类型提示触发运行期导入。
from __future__ import annotations

# 标准库负责 argparse 命名空间与 JSON 文本输出。
import argparse
import json

# 评估模块分别处理 trace 指标和 skill-effectiveness 指标。
from .evaluation import write_eval_metrics
from .skill_effectiveness import evaluate_skill_effectiveness

# 反思链路负责从报告、trace 和计划构造修复材料。
from .reflection import build_diagnosis, build_intervention, build_repair_plan, generate_repair_prompt

# 干预与 prompt 优化模块处理人工回答和历史 trace。
from .intervention import resolve_intervention
from .optimizer import build_prompt_memory, optimize_prompt_from_trace

# 共享 helper 负责 JSON 读取和状态记录。
from .cli_support import read_json, record_state
from .spec import read_spec
from .trace import read_trace
from .workspace import require_workspace_path, require_write_path, write_json, write_text

# cmd_reflect 根据验证报告生成修复 prompt 和诊断材料。
def cmd_reflect(args: argparse.Namespace) -> int:
    """处理 reflect 子命令并写出修复提示词。

    参数:
        args: argparse 解析出的 reflect 命名空间，包含 plan、report、report_json、trace、out 和可选辅助输出路径。

    返回:
        命令退出码；成功写出主修复 prompt 时返回 0。
    """

    # plan 输入必须是已存在的 RTL 计划或规格文件。
    path_plan = require_workspace_path(args.plan, purpose="plan path", must_exist=True)  # 修复反思计划路径

    # read_spec 保留旧 CLI 对 target=rtl 的结构校验。
    dict_plan = read_spec(path_plan, target="rtl")  # RTL 修复反思计划

    # 普通文本报告与 JSON 报告共享同一个后续反思入口。
    str_report_text = _read_reflection_report(args)  # 验证报告文本

    # validation_json 在 --report-json 缺省时为空。
    dict_validation_json = read_json(args.report_json) if args.report_json else None  # 结构化验证报告

    # trace 只在路径存在时参与修复上下文。
    list_trace_events = read_trace(args.trace) if args.trace and args.trace.exists() else []  # 历史 workflow trace 事件

    # reflection 模块生成给模型或人工审阅的修复 prompt。
    str_repair_prompt = generate_repair_prompt(  # 面向模型或人工修复的反思 prompt
        str_report_text,  # 失败现象和验证日志正文
        dict_plan,  # 与失败相关的 RTL 生成计划
        list_trace_events,  # workflow 历史事件链
        dict_validation_json,  # 机器可读验证诊断
        None,  # reflect CLI 不额外绑定参考模型合同
        None,  # reflect CLI 不额外绑定测试向量合同
    )

    # repair prompt 输出路径必须允许写入。
    path_output = require_write_path(args.out, purpose="repair prompt output")  # 修复 prompt 输出路径

    # 写入可读修复 prompt。
    write_text(path_output, str_repair_prompt)

    # repair plan 为机器可读的下一步动作建议。
    dict_repair_plan = build_repair_plan(  # 可被后续步骤消费的修复动作清单
        str_report_text,  # 用于提取失败类别的报告正文
        dict_plan,  # 修复动作需要对齐的阶段计划
        list_trace_events,  # 定位失败阶段的 trace 片段
        dict_validation_json,  # 结构化验证错误集合
        None,  # repair_plan 不绑定参考模型合同比对
        None,  # repair_plan 不绑定测试向量合同比对
    )

    # diagnosis 汇总错误类型和上下文证据。
    dict_diagnosis = build_diagnosis(dict_plan, list_trace_events, dict_validation_json, None)  # 反思诊断摘要

    # 可选输出由用户显式路径控制。
    _write_reflection_side_outputs(args, dict_repair_plan, dict_diagnosis, str_report_text, dict_validation_json)

    # workflow-state 记录主修复 prompt。
    record_state(args, "reflect", {"output": path_output})

    # reflect 成功写出主 prompt 即返回 0。
    return 0

# cmd_optimize_prompt 根据 trace 生成更稳健的下一轮 prompt。
def cmd_optimize_prompt(args: argparse.Namespace) -> int:
    """处理 optimize-prompt 子命令并写出优化后的提示词。

    参数:
        args: argparse 解析出的 optimize-prompt 命名空间，包含 trace、plan、out 和可选 memory_out。

    返回:
        命令退出码；优化 prompt 和可选 memory 写入完成时返回 0。
    """

    # trace 输入用于提取失败阶段和上下文裁剪经验。
    path_trace = require_workspace_path(args.trace, purpose="trace path", must_exist=True)  # prompt 优化 trace 路径

    # plan 输入提供当前 RTL 设计目标。
    path_plan = require_workspace_path(args.plan, purpose="plan path", must_exist=True)  # prompt 优化计划路径

    # read_spec 保持 target=rtl 的旧行为。
    dict_plan = read_spec(path_plan, target="rtl")  # prompt 优化使用的 RTL 计划

    # optimized prompt 输出必须通过写入边界检查。
    path_output = require_write_path(args.out, purpose="optimized prompt output")  # 优化后 prompt 输出路径

    # optimizer 根据 trace 和 plan 生成下一轮 prompt 文本。
    str_optimized_prompt = optimize_prompt_from_trace(path_trace, dict_plan)  # trace 驱动的优化 prompt

    # 写入优化后的 prompt。
    write_text(path_output, str_optimized_prompt)

    # memory_out 仅在用户显式请求时写入。
    if args.memory_out:

        # prompt memory 记录可复用的失败经验。
        path_memory_output = require_write_path(args.memory_out, purpose="prompt memory output")  # prompt 记忆输出路径

        # 写入结构化 prompt memory。
        write_json(path_memory_output, build_prompt_memory(path_trace, dict_plan))

    # workflow-state 记录 prompt 优化产物。
    record_state(args, "optimize_prompt", {"trace": path_trace, "plan": path_plan, "output": path_output})

    # optimize-prompt 成功即返回 0。
    return 0

# cmd_resolve_intervention 将人工回答转换为 workflow 决策。
def cmd_resolve_intervention(args: argparse.Namespace) -> int:
    """处理 resolve-intervention 子命令并写出决策文件。

    参数:
        args: argparse 解析出的 resolve-intervention 命名空间，包含 intervention、answer、out 和 memory_out。

    返回:
        命令退出码；决策和 memory 文件写入成功时返回 0。
    """

    # intervention JSON 必须来自受控 workspace 路径。
    path_intervention = require_workspace_path(args.intervention, purpose="intervention path", must_exist=True)  # 干预请求路径

    # answer 文件保存用户或上游系统给出的自然语言回答。
    path_answer = require_workspace_path(args.answer, purpose="answer path", must_exist=True)  # 干预回答路径

    # resolve_intervention 输出 workflow decision 和记忆。
    tuple_resolution = resolve_intervention(  # 干预解析得到的决策与记忆
        read_json(path_intervention),  # 干预请求对象
        path_answer.read_text(encoding="utf-8"),  # 干预回答文本
    )

    # tuple 拆分后分别写入决策与 memory 文件。
    dict_decision, dict_memory = tuple_resolution  # 干预解析产物

    # decision 文件承载 workflow 下一步可直接读取的人工选择。
    path_output = require_write_path(args.out, purpose="decision output")  # 干预决策输出路径

    # memory 文件承载后续 prompt 可复用的人工偏好或约束。
    path_memory_output = require_write_path(args.memory_out, purpose="memory output")  # 干预记忆输出路径

    # 写入 workflow decision。
    write_json(path_output, dict_decision)

    # 写入可复用 memory。
    write_json(path_memory_output, dict_memory)

    # workflow-state 记录干预解析结果路径。
    record_state(
        args,
        "resolve_intervention",
        {"intervention": path_intervention, "output": path_output, "memory": path_memory_output},
    )

    # 干预解析已持久化到 decision 和 memory 后返回成功码。
    return 0

# cmd_eval 从 workflow trace 生成本次运行指标。
def cmd_eval(args: argparse.Namespace) -> int:
    """处理 eval 子命令并生成 workflow 指标报告。

    参数:
        args: argparse 解析出的 eval 命名空间，包含 trace、out 和状态记录开关。

    返回:
        命令退出码；评估指标 JSON 写入成功时返回 0。
    """

    # trace 输入必须存在。
    path_trace = require_workspace_path(args.trace, purpose="trace path", must_exist=True)  # 评估 trace 输入路径

    # eval 报告路径保存 trace 指标的机器可读结果。
    path_output = require_write_path(args.out, purpose="evaluation output")  # 运行指标输出路径

    # write_eval_metrics 负责实际指标计算和 JSON 写入。
    write_eval_metrics(path_trace, path_output)

    # workflow-state 保存 trace 与指标文件的对应关系。
    record_state(args, "eval", {"trace": path_trace, "output": path_output})

    # trace 指标写入完成后返回 CLI 成功码。
    return 0

# cmd_eval_skill 运行 skill-effectiveness 评价集。
def cmd_eval_skill(args: argparse.Namespace) -> int:
    """处理 eval-skill 子命令并生成技能有效性报告。

    参数:
        args: argparse 解析出的 eval-skill 命名空间，包含 evals、out、remote_runs_json 和 require_remote。

    返回:
        命令退出码；summary.ok 为真时返回 0，否则返回 1。
    """

    # evals 输入是 skill-local 评价用例 JSON。
    path_evals = require_workspace_path(args.evals, purpose="skill eval cases", must_exist=True)  # skill 评价用例路径

    # eval-skill 输出路径保存完整评价报告。
    path_output = require_write_path(args.out, purpose="skill effectiveness output")  # skill 有效性报告路径

    # remote runs 可选注入远程验证证据。
    dict_remote_runs = read_json(args.remote_runs_json) if args.remote_runs_json else None  # 远程验证运行摘要

    # evaluate_skill_effectiveness 产出 summary.ok 作为命令退出依据。
    dict_report = evaluate_skill_effectiveness(  # 汇总每个 eval case 的技能有效性报告
        path_evals,  # 本次评价使用的 eval case 清单
        path_output,  # 评价报告写入位置
        remote_runs_report=dict_remote_runs,  # remote_validate 汇总出的运行证据
        require_remote=bool(args.require_remote),  # 是否强制要求远程证据
    )

    # bool_report_ok 提取 summary.ok，避免终端输出直接读取结构化报告对象。
    bool_report_ok = bool(dict_report["summary"]["ok"])  # skill 有效性总体验收状态

    # workflow-state 记录评价总体通过状态。
    record_state(args, "eval_suite", {"evals": path_evals, "output": path_output, "ok": dict_report["summary"]["ok"]})

    # summary.ok 是 eval-skill 的唯一成功判据。
    return 0 if bool_report_ok else 1

# _read_reflection_report 汇总 reflect 支持的两种报告输入。
def _read_reflection_report(args: argparse.Namespace) -> str:
    """读取 reflect 命令的文本或 JSON 报告输入。

    参数:
        args: argparse 解析出的 reflect 命名空间，包含 report 和 report_json。

    返回:
        可交给 reflection 模块分析的报告文本。

    异常:
        ValueError: report 与 report_json 都缺失，无法构造修复上下文。
    """

    # 文本报告缺省为空，后续可由 JSON 报告补齐。
    str_report_text = ""  # reflect 输入报告文本

    # --report 优先提供用户可读报告正文。
    if args.report:

        # report 路径必须存在且处于 workspace 内。
        path_report = require_workspace_path(args.report, purpose="report path", must_exist=True)  # 文本报告输入路径

        # 读取 UTF-8 报告正文。
        str_report_text = path_report.read_text(encoding="utf-8")  # 文本报告正文

    # --report-json 在缺少文本报告时转成可读 JSON 文本。
    if args.report_json and not str_report_text:

        # 保留原始 JSON 字段，便于 reflection 看到完整诊断证据。
        dict_validation_json = read_json(args.report_json)  # reflection 使用的验证诊断对象

        # JSON 格式化文本作为 reflection 输入。
        str_report_text = json.dumps(dict_validation_json, indent=2, ensure_ascii=False)  # 格式化验证报告文本

    # reflect 至少需要一种报告来源。
    if not str_report_text:

        # 缺少报告时阻止生成空修复 prompt。
        raise ValueError("> ERR: [Python] reflect requires --report or --report-json")

    # 返回可供 reflection 模块消费的报告文本。
    return str_report_text

# _write_reflection_side_outputs 写入 reflect 的可选辅助产物。
def _write_reflection_side_outputs(
    args: argparse.Namespace,
    dict_repair_plan: dict,
    dict_diagnosis: dict,
    report_text: str,
    dict_validation_json: dict | None,
) -> None:
    """按用户显式路径写入 reflect 辅助报告。

    参数:
        args: argparse 解析出的 reflect 命名空间，包含 repair_plan、diagnosis_out 和 intervention_out。
        dict_repair_plan: build_repair_plan 生成的结构化修复动作清单。
        dict_diagnosis: build_diagnosis 生成的失败诊断摘要。
        report_text: reflect 读取到的原始报告文本。
        dict_validation_json: 可选的机器可读验证诊断。

    返回:
        无返回值；函数只在用户提供路径时写入辅助 JSON 文件。
    """

    # repair_plan 辅助文件供自动修复编排读取。
    if args.repair_plan:

        # 修复计划路径由用户显式指定，避免默认产生额外文件。
        path_repair_plan = require_write_path(args.repair_plan, purpose="repair plan output")  # 修复计划输出路径

        # 写入结构化修复计划。
        write_json(path_repair_plan, dict_repair_plan)

    # diagnosis 辅助文件供人工审查失败类型和证据来源。
    if args.diagnosis_out:

        # 诊断摘要路径由用户显式指定，避免覆盖主 prompt 输出。
        path_diagnosis = require_write_path(args.diagnosis_out, purpose="diagnosis output")  # 诊断摘要输出路径

        # 写入结构化诊断摘要。
        write_json(path_diagnosis, dict_diagnosis)

    # 只有 ask_human 场景才生成 intervention 请求。
    if args.intervention_out and dict_repair_plan.get("action") == "ask_human":

        # intervention 请求文件承载需要用户回答的问题和证据。
        path_intervention = require_write_path(args.intervention_out, purpose="intervention output")  # 人工干预输出路径

        # build_intervention 将修复计划转成可确认的人工问题。
        dict_intervention = build_intervention(dict_repair_plan, report_text, dict_validation_json)  # 人工干预请求对象

        # 写入人工干预请求。
        write_json(path_intervention, dict_intervention)
