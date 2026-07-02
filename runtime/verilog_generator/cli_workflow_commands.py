"""实现 workflow、batch 和 route-workflow 相关 CLI 子命令。

stdout_protocol: json
"""

# future annotations 让 argparse 类型提示不增加运行期导入负担。
from __future__ import annotations

# 标准库负责 argparse 命名空间和 JSON 输出。
import argparse
import json
import sys

# batch 入口复用 integration adapter，保持 CLI 与集成层合同一致。
from integration.verilog_adapter import run_verilog_batch

# CLI support 统一处理外部验证策略和显式 JSON 请求读取。
from .cli_support import cli_run_external, read_json_anywhere
from .workflow import run_workflow
from .workflow_router import route_verilog_entry
from .workspace import require_write_path, write_json

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
