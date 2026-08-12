"""实现生成、提取、验证和 Verilog 质量门相关 CLI 子命令。"""

# future annotations 避免 argparse 类型提示在运行期强制求值。
from __future__ import annotations

# 标准库只负责 argparse 命名空间、JSON 输出和路径参数。
import argparse
import json
import sys
from pathlib import Path

# prompt 报告和 readiness helper 支撑生成链路。
from .cli_support import (
    PromptReportContext,
    build_prompt_report_payload,
    cli_run_external,
)

# JSON、manifest 和状态 helper 支撑命令副作用边界。
from .cli_support import (
    read_json,
    read_manifest,
    record_state,
    resolve_codegen_plan,
    synth_readiness_payload,
)

# 生成链路依赖提取、prompt、规格和 trace 能力。
from .extractor import extract_response
from .prompt import render_prompt
from .spec import read_spec, scaffold_spec, write_spec
from .trace import append_trace_event, safe_path, spec_summary

# 验证链路依赖 Verilog 质量门和 validation report。
from scripts.python.quality.quality_gate import run_verilog_quality_gate, write_quality_gate_report
from scripts.python.validation.validation import validate_generated

# workspace helper 负责路径边界和文件写入。
from .workspace import require_workspace_path, require_write_path, write_json, write_text

# cmd_scaffold 创建 RTL JSON 规格模板。
def cmd_scaffold(args: argparse.Namespace) -> int:
    """处理 scaffold 子命令。

    参数:
        args: argparse 解析出的 scaffold 命名空间。

    返回:
        规格模板写入成功时返回 0。
    """

    # 输出路径必须通过 workspace 写入边界检查。
    path_spec_output = require_write_path(args.out, purpose="spec output")  # 规格模板输出路径

    # scaffold_spec 负责生成默认 RTL 模板内容。
    dict_spec_template = scaffold_spec("rtl", name=args.name)  # RTL 规格模板对象

    # 将模板写入用户指定位置。
    write_spec(path_spec_output, dict_spec_template)

    # 记录模板生成事件，便于 workflow-state 追踪。
    record_state(args, "scaffold", {"target": "rtl", "output": path_spec_output})

    # scaffold 只生成文件，成功写入即返回 0。
    return 0

# cmd_prompt 渲染单阶段模型 prompt。
def cmd_prompt(args: argparse.Namespace) -> int:
    """处理 prompt 子命令。

    参数:
        args: argparse 解析出的 prompt 命名空间。

    返回:
        prompt 与可选统计报告写入成功时返回 0。
    """

    # spec 输入必须是 workspace 内存在的 JSON 规格。
    path_spec = require_workspace_path(args.spec, purpose="spec path", must_exist=True)  # RTL 规格输入路径

    # read_spec 会校验 target=rtl 的规格结构。
    dict_spec = read_spec(path_spec, target="rtl")  # RTL 规格对象

    # context manifest 可选注入上一阶段产物清单。
    dict_context_manifest = read_manifest(args.context_manifest) if args.context_manifest else None  # 上阶段产物清单

    # evidence 可选注入规格理解阶段证据。
    dict_evidence = read_json(args.evidence) if args.evidence else None  # prompt 证据对象

    # memory 可选注入历史 prompt 修复经验。
    dict_memory = read_json(args.memory) if args.memory else None  # prompt 记忆对象

    # vector contract 可选注入 Python semantic 语义合同。
    dict_vector_contract = read_json(args.vector_contract) if args.vector_contract else None  # 向量语义合同

    # decision 可选注入人工裁决约束。
    dict_decision = read_json(args.decision) if args.decision else None  # 人工决策对象

    # codegen plan 从 spec 相对路径恢复，用于 staged prompt。
    dict_codegen_plan = resolve_codegen_plan(dict_spec, path_spec)  # prompt 代码生成计划

    # prompt 渲染由 prompt.py 负责，CLI 只传递已解析上下文。
    str_prompt_output = render_prompt(  # 组合规格、证据和记忆后的模型提示词
        dict_spec,  # 已通过 target 校验的 RTL 规格
        target="rtl",  # 固定 Verilog RTL 目标
        stage=args.stage,  # prompt 工作流阶段

        # 上下文与证据输入决定 prompt 的事实来源。
        context_manifest=dict_context_manifest,  # workflow 上一阶段产物索引
        context_dir=args.context_dir,  # 附加上下文目录
        evidence=dict_evidence,  # 规格理解证据
        memory=dict_memory,  # prompt 修复记忆

        # 生成约束输入决定输出 RTL 的注释、结构和聚焦范围。
        comment_language=args.comment_language,  # 生成 RTL 时要求的注释语言
        vector_contract=dict_vector_contract,  # semantic model 提供的向量约束
        codegen_plan=dict_codegen_plan,  # 分阶段模块生成计划
        subfunction=args.subfunction,  # 子功能聚焦范围
        budget=args.budget,  # prompt 预算档位
        decision=dict_decision,  # 人工决策约束
    )

    # prompt 输出路径必须允许写入。
    path_prompt_output = require_write_path(args.out, purpose="prompt output")  # prompt 输出路径

    # 将 prompt 文本写入目标文件。
    write_text(path_prompt_output, str_prompt_output)

    # prompt report payload 保持旧 stats JSON 合同。
    dict_prompt_report = build_prompt_report_payload(  # 保持旧 stats JSON 文件合同
        PromptReportContext(  # stats JSON 汇总所需的 prompt 元信息
            output=str_prompt_output,  # 已写出的完整 prompt 文本
            stage=args.stage,  # 当前渲染的 workflow 阶段
            budget=args.budget,  # 用于估算 prompt 规模的预算档位
            subfunction=args.subfunction,  # prompt 只展开的局部功能名

            # 报告上下文字段用于解释 prompt 规模来源。
            context_manifest=dict_context_manifest,  # stats 记录的上阶段产物索引
            context_dir=args.context_dir,  # stats 记录的附加上下文目录
            vector_contract=dict_vector_contract,  # stats 记录的测试向量约束
            decision=dict_decision,  # stats 记录的人工确认输入
        )
    )

    # stats_json 仅在用户显式请求时写入。
    if args.stats_json:

        # stats 输出也必须通过 workspace 写入边界。
        path_stats_output = require_write_path(args.stats_json, purpose="prompt stats output")  # prompt 统计 JSON 路径

        # prompt 报告写入 JSON 文件。
        write_json(path_stats_output, dict_prompt_report)

    # workflow-state 记录 prompt 输出和报告摘要。
    record_state(
        args,
        "prompt",
        {"target": "rtl", "stage": args.stage, "output": path_prompt_output, "stats": dict_prompt_report},
    )

    # trace 仅在用户提供 trace 路径时追加。
    if args.trace:

        # trace 事件保留 prompt 规模和规格摘要。
        append_trace_event(
            args.trace,
            {
                "event": "prompt",
                "target": "rtl",
                "stage": args.stage,
                "spec": spec_summary(dict_spec),
                "output": path_prompt_output,
                "prompt_stats": dict_prompt_report,
            },
        )

    # prompt 渲染成功即返回 0。
    return 0

# cmd_extract 从模型响应中拆出 manifest 声明的文件。
def cmd_extract(args: argparse.Namespace) -> int:
    """处理 extract 子命令。

    参数:
        args: argparse 解析出的 extract 命名空间。

    返回:
        response 中声明的产物提取成功时返回 0。
    """

    # response 输入必须存在于 workspace 内。
    path_response = require_workspace_path(args.response, purpose="response path", must_exist=True)  # 模型响应输入路径

    # artifact 输出目录必须通过写入边界检查。
    path_output_dir = require_write_path(args.out_dir, purpose="artifact output directory")  # 产物输出目录

    # 提取器负责解析 fenced response 并写出文件。
    list_extracted_files = extract_response(path_response.read_text(encoding="utf-8"), path_output_dir)  # 提取产物路径清单

    # workflow-state payload 保持 extract 旧字段。
    dict_extract_state = {  # extract 状态记录载荷
        "response": path_response,  # 原始模型响应文件
        "out_dir": path_output_dir,  # extract 写入 RTL 产物的目录
        "files": list_extracted_files,  # 提取出的产物路径清单
    }

    # workflow-state 记录响应来源和提取结果。
    record_state(args, "extract", dict_extract_state)

    # trace 记录安全路径文本，避免泄露不必要的本地路径细节。
    if args.trace:

        # safe_path 归一化每个提取文件路径。
        list_safe_files = [safe_path(path_file) for path_file in list_extracted_files]  # trace 提取文件清单

        # 追加 extract 事件到 trace JSONL。
        append_trace_event(
            args.trace,
            {"event": "extract", "response": path_response, "out_dir": path_output_dir, "files": list_safe_files},
        )

    # extract 成功写出产物即返回 0。
    return 0

# cmd_validate 校验生成的 Verilog 产物。
def cmd_validate(args: argparse.Namespace) -> int:
    """处理 validate 子命令。

    参数:
        args: argparse 解析出的 validate 命名空间。

    返回:
        验证通过时返回 0；存在阻断问题时返回 1。
    """

    # spec 输入决定验证规则和设计目标。
    path_spec = require_workspace_path(args.spec, purpose="spec path", must_exist=True)  # validate 使用的 RTL 规格文件

    # artifact path 指向待验证的 RTL 或产物目录。
    path_artifact = require_workspace_path(args.path, purpose="artifact path", must_exist=True)  # 待验证产物路径

    # semantic_contract 可选提供语义参考检查。
    dict_semantic_contract = read_json(args.semantic_contract) if args.semantic_contract else None  # 参考语义合同

    # run_external 统一执行 remote-first 本地外部工具保护策略。
    bool_run_external = cli_run_external(args.no_external, args.external_target, args.readiness)  # 外部工具执行开关

    # validate_generated 返回结构化 report，CLI 负责展示和落盘。
    validation_report = validate_generated(  # 汇总静态、接口和可选外部验证的报告对象
        read_spec(path_spec, target="rtl"),  # 与产物匹配的 RTL 规格
        path_artifact,  # 待检查的 RTL 文件或产物目录
        target="rtl",  # validate 子命令固定检查 RTL 目标

        # readiness 和注释语言决定 validation 执行深度。
        run_external=bool_run_external,  # 外部工具开关
        readiness=args.readiness,  # readiness 验证深度
        comment_language=args.comment_language,  # 输出注释语言
        semantic_contract=dict_semantic_contract,  # semantic model 提供的语义对照合同
    )

    # validation report 保持原 stdout 展示合同，便于上层 smoke 收集完整诊断。
    sys.stdout.write(validation_report.format() + "\n")

    # 仅在用户指定 report_json 时写结构化报告。
    if args.report_json:

        # report_json 路径必须可写。
        path_report_json = require_write_path(args.report_json, purpose="validation report")  # 验证报告 JSON 路径

        # 写入完整 validation report。
        write_json(path_report_json, validation_report.to_dict())

        # synth readiness 与 report 放在同一目录。
        path_synth_readiness = require_write_path(  # synth readiness 摘要输出路径
            args.report_json.parent / "synth_readiness.json",  # validation report 同目录摘要
            purpose="synth readiness output",  # workspace 写入用途标签
        )

        # synth readiness 只保留下游关心的工具执行摘要。
        write_json(path_synth_readiness, synth_readiness_payload(validation_report.to_dict(), readiness=args.readiness))

    # workflow-state 记录 validation 是否通过。
    record_state(
        args,
        "validate",
        {"target": "rtl", "path": path_artifact, "ok": validation_report.ok(), "report_json": args.report_json},
    )

    # trace 仅在显式传入时记录 issues。
    if args.trace:

        # issues 需要转换为 JSON 可序列化字典。
        list_issue_dicts = [issue.to_dict() for issue in validation_report.issues]  # 验证发现项清单

        # validate trace payload 保留旧字段名。
        dict_validate_trace = {  # validate trace 事件载荷
            "event": "validate",  # trace 事件类型
            "target": "rtl",  # validate 子命令固定目标
            "path": path_artifact,  # 被验证的产物路径
            "ok": validation_report.ok(),  # 验证通过状态
            "issues": list_issue_dicts,  # 验证发现项字典列表
        }

        # 追加 validate 事件。
        append_trace_event(
            args.trace,
            dict_validate_trace,
        )

    # 验证失败时返回 1，保持旧 CLI 合同。
    return 0 if validation_report.ok() else 1

# cmd_quality_gate 运行 formatter-AST Verilog 质量门。
def cmd_quality_gate(args: argparse.Namespace) -> int:
    """处理 quality-gate 子命令。

    参数:
        args: argparse 解析出的 quality-gate 命名空间。

    返回:
        质量门通过或 warn-only 生效时返回 0；严格失败时返回 1。
    """

    # 待检查路径必须存在于 workspace 内。
    path_target = require_workspace_path(args.path, purpose="Verilog quality-gate path", must_exist=True)  # Verilog 检查目标

    # 质量门核心逻辑由 quality_gate.py 执行。
    quality_report = run_verilog_quality_gate(  # formatter-AST 与注释规则的检查结果
        path_target,  # 待扫描的 RTL 文件或目录
        strict=not args.non_strict,  # 是否严格处理发现项
        comment_language=args.comment_language,  # 注释语言
        formatter_profile=args.formatter_profile,  # formatter 配置档位
        include_testbench=args.include_testbench,  # 是否包含 testbench
        vitis_wrapper=args.vitis_wrapper,  # 是否使用 Vitis wrapper 规则
    )

    # quality-gate Markdown 保持原 stdout 展示合同，便于人工和 CI 日志审阅。
    sys.stdout.write(quality_report.to_markdown() + "\n")

    # JSON 报告路径仅在用户传参时解析写入边界。
    path_report_json = (  # 自动化消费的质量门 JSON 路径
        require_write_path(args.report_json, purpose="quality gate JSON report")  # 保存完整机器可读发现项
        if args.report_json  # 调用方请求结构化报告
        else None  # 跳过 JSON 产物
    )

    # Markdown 产物路径只服务人工审阅，不参与自动化 JSON 消费。
    path_report_markdown = (  # 人工审阅的质量门 Markdown 路径
        require_write_path(args.report_md, purpose="quality gate Markdown report")  # 保存可读质量门摘要
        if args.report_md  # 调用方请求审阅文档
        else None  # 不生成审阅文档
    )

    # 可选 JSON/Markdown 报告路径统一通过 writer 处理。
    write_quality_gate_report(
        quality_report,
        json_path=path_report_json,
        markdown_path=path_report_markdown,
    )

    # workflow-state 只记录轻量计数，完整 issue 留给报告文件或 trace。
    dict_quality_state = {  # 状态文件中的质量门计数载荷
        "path": path_target,  # 被质量门扫描的路径
        "ok": quality_report.ok(),  # 质量门通过状态
        "errors": quality_report.errors,  # 错误数量
        "warnings": quality_report.warnings,  # 告警数量
    }

    # workflow-state 记录质量门错误和告警计数。
    record_state(
        args,
        "quality_gate",
        dict_quality_state,
    )

    # trace 仅在显式传入时记录质量门发现项。
    if args.trace:

        # issue 对象需要转换为可序列化字典。
        list_issue_dicts = [issue.to_dict() for issue in quality_report.issues]  # 质量门发现项清单

        # trace payload 带完整 issue 明细，供 workflow 回放和失败定位使用。
        dict_quality_trace = {  # trace JSONL 中的质量门事件载荷
            "event": "quality_gate",  # 标识 Verilog 质量门事件
            "path": safe_path(path_target),  # 脱敏后的检查路径
            "ok": quality_report.ok(),  # formatter 与注释规则的综合通过状态
            "issues": list_issue_dicts,  # 质量门发现项字典列表
        }

        # 把质量门发现项追加到 workflow trace。
        append_trace_event(
            args.trace,
            dict_quality_trace,
        )

    # warn_only 允许用户只观察发现项而不让命令失败。
    return 0 if (quality_report.ok() or args.warn_only) else 1
