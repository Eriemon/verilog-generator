"""提供 Verilog generation workflows 的命令行入口。"""

# future annotations 让命令处理器类型提示保持惰性求值。
from __future__ import annotations

# 标准库仅负责参数命名空间、JSON 输出、错误流和路径值。
import argparse
import sys
from typing import Callable

# 运行时导入按功能分组，保持 CLI 层只做命令映射和错误出口。
from . import cli_audit_commands as cli_audit
from . import cli_existing_commands as cli_existing
from . import cli_feedback_commands as cli_feedback
from . import cli_generation_commands as cli_generation
from . import cli_workflow_commands as cli_workflow
from .extractor import ExtractionError
from .spec import SpecError
from .cli_parser import build_cli_parser

# CommandHandler 描述 argparse 子命令处理器的统一签名。
CommandHandler = Callable[[argparse.Namespace], int]  # CLI 子命令处理器类型

# _command_handlers 保持 parser 规格和处理器实现的唯一连接点。
def _command_handlers() -> dict[str, CommandHandler]:
    """返回 CLI 子命令到处理函数的稳定映射。

    参数:
        无外部业务参数，命令表由模块导入的命令实现共同组成。
    返回:
        子命令 handler_key 到处理函数的映射，供 argparse 子解析器绑定。
    """

    # 该映射必须覆盖 cli_parser.py 中的全部 handler_key。
    return {
        "scaffold": cli_generation.cmd_scaffold,
        "write-spec": cli_generation.cmd_write_spec,
        "prompt": cli_generation.cmd_prompt,
        "extract": cli_generation.cmd_extract,
        "validate": cli_generation.cmd_validate,
        "quality-gate": cli_generation.cmd_quality_gate,

        # workflow 组保持 staged、batch 和只读路由命令的入口。
        "review": cli_workflow.cmd_review,
        "run-workflow": cli_workflow.cmd_run_workflow,
        "run-batch": cli_workflow.cmd_run_batch,
        "route-workflow": cli_workflow.cmd_route_workflow,

        # audit 组覆盖向量、接口、语义模型和规格证据拆解。
        "audit-vectors": cli_audit.cmd_audit_vectors,
        "audit-interface": cli_audit.cmd_audit_interface,
        "audit-semantic": cli_audit.cmd_audit_semantic_model,
        "ingest-spec": cli_audit.cmd_ingest_spec,
        "decompose": cli_audit.cmd_decompose,

        # feedback 组覆盖反思、人工干预解析和评价命令。
        "reflect": cli_feedback.cmd_reflect,
        "optimize-prompt": cli_feedback.cmd_optimize_prompt,
        "resolve-intervention": cli_feedback.cmd_resolve_intervention,
        "eval": cli_feedback.cmd_eval,
        "eval-skill": cli_feedback.cmd_eval_skill,

        # existing RTL 组覆盖分析、受控修改、语义比较和 verify-repair。
        "analyze-existing": cli_existing.cmd_analyze_existing,
        "improve-existing": cli_existing.cmd_improve_existing,
        "compare-semantics": cli_existing.cmd_compare_semantics,
        "verify-existing": cli_existing.cmd_verify_existing,
        "run-cases": cli_existing.cmd_run_cases,
    }

# build_parser 暴露给测试和 __main__ 共用同一解析器。
def build_parser() -> argparse.ArgumentParser:
    """构建 verilog-gen 的 argparse 解析器。

    参数:
        无外部业务参数，解析器配置来自 cli_parser.py 的命令规格表。
    返回:
        已绑定全部子命令处理器的 argparse.ArgumentParser 实例。
    """

    # parser 结构由 cli_parser.py 的规格表控制，cli.py 只提供处理器映射。
    return build_cli_parser(_command_handlers())

# main 负责把 argparse 入口错误转换为稳定退出码。
def main(argv: list[str] | None = None) -> int:
    """执行 verilog-gen CLI 主入口。

    参数:
        argv: 可选命令行参数列表；为 None 时由 argparse 读取进程参数。
    返回:
        CLI 退出码，0 表示成功，可预期输入错误返回 2。
    """

    # parser 构造集中复用 build_parser，确保测试和入口一致。
    parser = build_parser()  # verilog-gen 参数解析器

    # argparse 负责把 argv 转为命令处理器需要的命名空间。
    args = parser.parse_args(argv)  # CLI 参数命名空间

    # 业务错误统一转换成 CLI 退出码 2。
    try:

        # 子命令处理器返回值是 CLI 退出码来源。
        return int(args.func(args))

    # 捕获用户输入或规格解析错误，避免输出 Python traceback。
    except (ExtractionError, SpecError, ValueError) as exc:

        # 将可预期输入错误写入 stderr。
        print(f"> ERR: [Python] CLI 输入错误: {exc}", file=sys.stderr)

        # argparse 风格输入错误使用退出码 2。
        return 2

# 直接执行模块时进入 CLI 主流程。
if __name__ == "__main__":

    # 模块直接执行时使用 SystemExit 传递 CLI 退出码。
    raise SystemExit(main())
