"""实现 workflow、batch 和 route-workflow 相关 CLI 子命令。

stdout_protocol: json
"""

# future annotations 让 argparse 类型提示不增加运行期导入负担。
from __future__ import annotations

# 标准库负责 argparse 命名空间和 JSON 输出。
import argparse
import json
import sys

# batch 入口复用公开 facade API，保持 CLI 与公开合同一致。
from scripts.python.facade.verilog_api import run_verilog_batch
from scripts.python.quality.deliverable_gate import run_verilog_deliverable_gate

# CLI support 统一处理外部验证策略和显式 JSON 请求读取。
from .cli_support import cli_run_external, read_json_anywhere
from .workflow import run_workflow
from .workflow_router import route_verilog_entry
from .workspace import require_write_path, write_json

# review 输入缺失时使用稳定 code，方便上层直接判定。
TARGET_OR_CODE_REQUIRED = "TARGET_OR_CODE_REQUIRED"  # review 必需输入缺失错误码

# 非 Verilog-2001 后缀统一按 HDL 方言越界处理。
UNSUPPORTED_VERILOG_DIALECT = "UNSUPPORTED_VERILOG_DIALECT"  # review 方言边界错误码

# _review_error_report 构造 review 的确定性失败报告。
def _review_error_report(code: str, message: str, *, target: str | None = None) -> dict[str, object]:
    """构造 review CLI 使用的失败报告。

    参数:
        code: 稳定错误代码。
        message: 面向终端和 Markdown 的错误说明。
        target: 可选目标路径文本。
    返回:
        与交付门禁字段对齐的失败报告字典。
    """

    # issue 结构与 deliverable gate 的 issue 字典保持同形。
    dict_issue = {
        "code": code,  # 稳定错误代码
        "rule": code,  # 按 rule 字段暴露，便于统一聚合
        "severity": "error",  # review 输入错误始终阻断交付
        "message": message,  # 用户可见诊断文本
        "path": target,  # 可选目标路径
    }  # review 单条输入错误

    # 返回字段覆盖公开 review 契约要求的交付状态和问题聚合。
    return {
        "version": 1,  # review 报告版本
        "command": "review",  # 生成报告的 CLI 子命令
        "target": target,  # 本次 review 目标
        "delivery_ready": False,  # 输入错误不能交付
        "repair_required": True,  # 需要用户修正输入后再跑
        "rerun_required_after_repair": True,  # 修正后必须重跑 review
        "delivery_issues_by_rule": {code: 1},  # 按稳定 code 汇总
        "issues": [dict_issue],  # 详细问题列表
    }

# _write_optional_review_reports 按用户请求写出 review JSON/Markdown。
def _write_optional_review_reports(args: argparse.Namespace, report: dict[str, object]) -> None:
    """写出 review 的可选报告文件。

    参数:
        args: argparse 解析后的 review 参数。
        report: 已生成的 review 报告。
    返回:
        无返回值；必要时写出 JSON 或 Markdown。
    """

    # JSON 报告供机器读取。
    if args.report_json:

        # 输出路径仍走 workspace 写入边界。
        path_report_json = require_write_path(args.report_json, purpose="review JSON report")  # review JSON 输出路径

        # 写入机器可读报告。
        write_json(path_report_json, report)

    # Markdown 报告供用户扫读。
    if args.report_md:

        # Markdown 报告路径沿用 workspace 写入边界。
        path_report_md = require_write_path(args.report_md, purpose="review Markdown report")  # 用户扫读版报告目标文件

        # 生成简洁 Markdown，不复制完整源码。
        path_report_md.write_text(_render_review_markdown(report), encoding="utf-8")

# _render_review_markdown 生成 review 摘要。
def _render_review_markdown(report: dict[str, object]) -> str:
    """把 review 报告渲染成简洁 Markdown。

    参数:
        report: review JSON 报告。
    返回:
        Markdown 文本。
    """

    # issue 字段可能来自 deliverable gate 或输入错误报告。
    obj_raw_issues = report.get("issues", [])  # review 报告里的原始 issues 字段

    # Markdown 渲染只遍历 list 形态的 finding 集合。
    list_issues = obj_raw_issues if isinstance(obj_raw_issues, list) else []  # 可迭代 review issue 列表

    # 标题文本分段构造，避免 Markdown 标记被 Python 注释门禁误判。
    str_title = "#" + " Verilog Review"  # review Markdown 一级标题

    # findings 标题单独保存，后续列表中只引用变量。
    str_findings_title = "##" + " Findings"  # findings 小节标题文本

    # Markdown 先展示交付状态。
    list_lines = [  # 渲染给用户阅读的审查结论、修复状态和发现项正文
        str_title,  # 用户报告开头显示审查命令标题
        "",  # 分隔标题和交付状态清单的空行
        f"- delivery_ready: {bool(report.get('delivery_ready'))}",  # 交付可用性状态行
        f"- repair_required: {bool(report.get('repair_required'))}",  # 是否需要修复的状态行
        f"- rerun_required_after_repair: {bool(report.get('rerun_required_after_repair'))}",  # 修复后复跑要求状态行
        "",  # 分隔状态清单和问题明细小节的空行
        str_findings_title,  # 用户报告中引出问题明细的小节标题
    ]

    # 没有 issue 时显式写出空状态。
    if not list_issues:

        # review 通过时保留可读结论。
        list_lines.append("- None")

    # 逐条列出 code、severity 和 message。
    for obj_issue in list_issues:

        # 只处理字典 issue，避免异常对象进入 Markdown。
        if isinstance(obj_issue, dict):

            # code 优先，其次回落 rule 字段。
            str_code = str(obj_issue.get("code") or obj_issue.get("rule") or "UNKNOWN")  # 用户可见 finding 编号

            # severity 便于用户区分阻断和提示。
            str_severity = str(obj_issue.get("severity") or "unknown")  # finding 严重级别

            # message 只取单行，避免 Markdown 被日志污染。
            str_message = str(obj_issue.get("message") or "").replace("\n", " ")  # finding 单行说明

            # 追加一条 finding。
            list_lines.append(f"- `{str_code}` [{str_severity}] {str_message}")

    # Markdown 以换行结尾。
    return "\n".join(list_lines) + "\n"

# cmd_review 执行真实 Verilog review 门禁。
def cmd_review(args: argparse.Namespace) -> int:
    """处理 review 子命令。

    参数:
        args: argparse 解析后的 review 参数命名空间。
    返回:
        review 通过时返回 0，否则返回 1。
    """

    # 缺少真实目标时不能降级到 route-only 分类。
    if args.target is None or not args.target.is_file():

        # 构造稳定输入错误报告。
        dict_report = _review_error_report(  # 缺目标结构化报告
            TARGET_OR_CODE_REQUIRED,  # 缺输入时的稳定错误码
            "A readable Verilog review requires an existing .v target.",  # 缺目标时的用户提示
            target=str(args.target) if args.target is not None else None,  # 原始 target 参数回显
        )

        # 方言拒绝也需要同步写出 JSON/Markdown 报告。
        _write_optional_review_reports(args, dict_report)

        # 同步输出 JSON，终端调用能直接看到 code。
        sys.stdout.write(json.dumps(dict_report, indent=2, ensure_ascii=False) + "\n")

        # 输入错误返回失败。
        return 1

    # review 当前只接受 Verilog-2001 .v 文件。
    if args.target.suffix.lower() != ".v":

        # 越界后缀统一给出方言拒绝 finding。
        dict_report = _review_error_report(  # 越界方言结构化报告
            UNSUPPORTED_VERILOG_DIALECT,  # 越界 HDL 后缀错误码
            "Unsupported HDL dialect; only .v targets are accepted by this skill.",  # 越界后缀提示
            target=str(args.target),  # 被拒绝的用户输入路径
        )

        # 按请求写出报告文件。
        _write_optional_review_reports(args, dict_report)

        # 同步输出 JSON。
        sys.stdout.write(json.dumps(dict_report, indent=2, ensure_ascii=False) + "\n")

        # 方言越界返回失败。
        return 1

    # 真实 review 进入交付门禁闭环。
    dict_report = run_verilog_deliverable_gate(  # 承载交付结论、修复要求和问题编号的审查报告
        args.target,  # 本次审查需要进入交付门禁的 Verilog 文件
        strict=not args.non_strict,  # 默认严格交付模式
        comment_language=args.comment_language,  # 交付门禁采用的注释语言策略
        formatter_profile=args.formatter_profile,  # 控制格式化抽象语法检查的配置名
        include_testbench=False,  # 单文件审查默认不扩大到测试平台文件
    )

    # 标注 CLI 来源，便于上层归档。
    dict_report["command"] = "review"  # 报告来源子命令

    # 标注本次 review 的目标文件路径。
    dict_report["target"] = str(args.target)  # review 目标路径

    # 写出可选报告。
    _write_optional_review_reports(args, dict_report)

    # stdout 直接输出完整报告，VG 编号会随 issues 可见。
    sys.stdout.write(json.dumps(dict_report, indent=2, ensure_ascii=False) + "\n")

    # delivery_ready 是唯一成功条件。
    return 0 if dict_report.get("delivery_ready") else 1

# cmd_run_workflow 执行或恢复 staged workflow。
def cmd_run_workflow(args: argparse.Namespace) -> int:
    """处理 run-workflow 子命令。

    参数:
        args: argparse 解析后的 run-workflow 参数命名空间。

    返回:
        workflow 通过时返回 0，否则返回 1。

    异常:
        ValueError: 当新 workflow 缺少 spec 或 out-dir 时抛出。
    """

    # 外部工具执行策略必须先解析，避免 workflow 内部绕过 remote-first 保护。
    bool_run_external = cli_run_external(args.no_external, args.external_target, args.readiness)  # workflow 外部工具开关

    # resume 模式复用已有 run 目录，不要求 spec/out-dir。
    if args.resume:

        # 恢复 workflow 时只允许覆盖显式 CLI 选项。
        dict_workflow_result = run_workflow(  # 恢复已有 run 目录的 workflow 结果
            resume_dir=args.resume,  # 待恢复 workflow 目录
            decision_path=args.decision,  # 新运行预加载的人工确认文件
            generation_mode=args.generation_mode,  # 生成模式覆盖选项

            # 运行策略参数只覆盖本次恢复过程。
            stream=args.stream,  # provider 流式输出策略
            stop_on_human=args.stop_on_human,  # 人工阻断停止策略
            run_external=bool_run_external,  # 外部工具执行策略
            comment_language=args.comment_language,  # 输出注释语言
            model_timeout_s=args.model_timeout,  # 模型调用超时秒数
        )

    # 没有 resume 时启动新的 RTL workflow。
    else:

        # 新 workflow 必须同时提供输入 spec 和输出目录。
        if not args.spec or not args.out_dir:

            # 缺少任一必需路径时不能猜测默认位置。
            raise ValueError("> ERR: [Python] New workflow runs require --spec and --out-dir.")

        # 新 workflow 入口承接 regular/deep_review 主路径。
        dict_workflow_result = run_workflow(  # 新建 workflow 的完整运行记录
            spec_path=args.spec,  # 用户提供的 RTL 规格文件
            target="rtl",  # 固定 Verilog RTL 目标
            out_dir=args.out_dir,  # 新运行写入 trace 和阶段产物的目录
            decision_path=args.decision,  # 人工决策文件路径
            evidence_path=args.evidence,  # 规格证据文件路径

            # provider 参数决定模型调用方式。
            provider_name=args.model_provider,  # 模型提供方名称
            provider_command=args.model_command,  # 外部模型命令行
            generation_mode=args.generation_mode,  # 常规或深度审查生成模式
            stream=args.stream,  # 模型流式输出策略

            # 验证和修复策略决定 workflow 退出条件。
            readiness=args.readiness,  # 静态、语义或外部验证深度
            max_attempts=args.max_attempts,  # 自动修复循环的最大次数
            stop_on_human=args.stop_on_human,  # 命中人工确认点时是否停止
            run_external=bool_run_external,  # remote-first 策略解析后的外部执行许可
            comment_language=args.comment_language,  # 传递给 RTL 生成阶段的注释语言
            model_timeout_s=args.model_timeout,  # 单次 provider 调用的超时秒数
        )

    # workflow 结果以 JSON 形式输出，便于脚本调用。
    sys.stdout.write(json.dumps(dict_workflow_result, indent=2, ensure_ascii=False) + "\n")

    # 只有 workflow 明确 passed 才返回 0。
    return 0 if dict_workflow_result.get("status") == "passed" else 1

# cmd_run_batch 执行多个 spec-to-RTL workflow case。
def cmd_run_batch(args: argparse.Namespace) -> int:
    """处理 run-batch 子命令。

    参数:
        args: argparse 解析后的 run-batch 参数命名空间。

    返回:
        batch 汇总通过时返回 0，否则返回 1。
    """

    # 批量入口先统一解析外部工具策略，避免各 case 行为分叉。
    bool_run_external = cli_run_external(args.no_external, args.external_target, args.readiness)  # 批量外部工具策略

    # batch adapter 负责逐 case 调用 workflow 并汇总结果。
    dict_batch_result = run_verilog_batch(  # 批量运行各规格后的汇总报告
        args.spec,  # 按输入顺序执行的规格文件列表
        out_dir=args.out_dir,  # batch 产物根目录
        workflow_config=args.workflow_config,  # batch 工作流配置
        evidence=args.evidence,  # 所有 case 共享的规格证据文件

        # provider 参数在每个 batch case 中复用。
        provider_name=args.model_provider,  # 每个 case 复用的模型提供方
        provider_command=args.model_command,  # 命令式 provider 的调用模板
        generation_mode=args.generation_mode,  # regular 或 deep_review 生成模式
        stream=args.stream,  # 是否流式接收模型输出

        # 验证和超时策略在所有 batch case 中保持一致。
        readiness=args.readiness,  # 所有 case 共享的验证深度
        max_attempts=args.max_attempts,  # 每个 case 允许的修复轮数
        stop_on_human=args.stop_on_human,  # 人工确认点的停止策略
        run_external=bool_run_external,  # 批量运行中的外部执行许可
        external_target=args.external_target,  # local 或 remote 外部工具目标
        comment_language=args.comment_language,  # 批量 case 生成 RTL 时使用的注释语言
        model_timeout_s=args.model_timeout,  # 单次模型调用超时秒数
    )

    # batch 结果以 JSON 输出给调用方。
    sys.stdout.write(json.dumps(dict_batch_result, indent=2, ensure_ascii=False) + "\n")

    # batch 汇总状态 passed 才视为命令成功。
    return 0 if dict_batch_result.get("status") == "passed" else 1

# cmd_route_workflow 只分类入口，不执行 workflow。
def cmd_route_workflow(args: argparse.Namespace) -> int:
    """处理 route-workflow 子命令。

    参数:
        args: argparse 解析后的 route-workflow 参数命名空间。

    返回:
        路由决策成功写出后返回 0。
    """

    # request JSON 是显式传入的路由事实包。
    dict_request = read_json_anywhere(args.request_json)  # workflow 路由请求对象

    # route_verilog_entry 根据可用事实选择最安全入口。
    dict_route_decision = route_verilog_entry(  # 基于请求事实选择 workflow 入口
        request_summary=str(dict_request.get("request_summary") or dict_request.get("summary") or ""),  # 用户请求摘要
        spec=dict_request.get("spec"),  # 规格输入事实
        codegen_plan=dict_request.get("codegen_plan"),  # 代码计划事实
        rtl=dict_request.get("rtl"),  # RTL 输入事实
        testbench=dict_request.get("testbench"),  # testbench 验证事实

        # 运行证据字段共同决定是否进入修复或验证路径。
        logs=dict_request.get("logs"),  # 日志证据事实
        waveform=dict_request.get("waveform"),  # 波形证据事实
        validation=dict_request.get("validation"),  # 验证报告事实
        artifact_dir=args.artifact_dir or dict_request.get("artifact_dir"),  # 产物目录事实
        remote_validation_requested=bool(dict_request.get("remote_validation_requested", False)),  # 远程验证意图
    )

    # route 输出路径必须通过 workspace 写入边界。
    path_route_output = require_write_path(args.out, purpose="route decision output")  # 路由决策输出路径

    # 写入机器可读路由决策。
    write_json(path_route_output, dict_route_decision)

    # 同步输出路由决策，便于 CLI 调试。
    sys.stdout.write(json.dumps(dict_route_decision, indent=2, ensure_ascii=False) + "\n")

    # route-workflow 不执行设计动作，成功写出即返回 0。
    return 0
