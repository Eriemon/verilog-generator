"""构建 Verilog generator 命令行解析器。"""

# future annotations 保持类型提示轻量，避免运行期求值导入额外对象。
from __future__ import annotations

# 标准库导入限定在 argparse、路径和类型工具，避免 CLI parser 依赖运行时副作用。
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

# 只导入 parser 需要的枚举常量，命令执行逻辑继续留在 cli.py。
from .interface_contract import INTERFACE_TARGETS
from .prompt import COMMENT_LANGUAGES, PROMPT_BUDGETS, PROMPT_STAGES
from scripts.python.version import __version__
from scripts.python.validation.validation import READINESS_LEVELS
from scripts.python.existing_rtl.verify_repair import AUTOMATION_MODES, TB_LANGUAGES, TB_MODES

# 命令处理器签名必须和 argparse set_defaults(func=...) 保持一致。
CommandHandler = Callable[[argparse.Namespace], int]  # argparse 命令处理器类型

@dataclass(frozen=True)
class ArgSpec:
    """描述一个 argparse 参数的 flags 与关键字参数。"""

    # flags 保留 argparse 原始短/长参数顺序。
    flags: tuple[str, ...]  # argparse 参数名集合

    # kwargs 保留 argparse.add_argument 的关键字配置。
    kwargs: Mapping[str, Any] = field(default_factory=dict)  # argparse 参数配置

@dataclass(frozen=True)
class CommandSpec:
    """描述一个子命令及其通用 trace/state 需求。"""

    # name 是公开 CLI 子命令名，不能随内部函数重命名而变化。
    name: str  # CLI 子命令名

    # help_text 保持安装后帮助文本合同稳定。
    help_text: str  # CLI 帮助文本

    # handler_key 显式索引 cli.py 传入的处理器映射。
    handler_key: str  # 命令处理器键

    # arguments 仅描述该子命令专属参数。
    arguments: tuple[ArgSpec, ...] = ()  # 子命令参数规格

    # trace 控制追加式执行轨迹参数是否出现在该子命令上。
    trace: bool = False  # 是否注册 trace 参数

    # state 标记该命令是否会写入 workflow-state 记录。
    state: bool = False  # workflow-state 参数注册布尔值

# arg 是规格表的最小构造器，集中处理 flags 的不可变转换。
def arg(*flags: str, **kwargs: Any) -> ArgSpec:
    """把参数声明压成不可变规格，供后续统一注册。

    参数:
        flags: argparse 位置参数或选项名。
        kwargs: 传给 argparse.add_argument 的关键字配置。

    返回:
        不可变的参数规格对象。
    """

    # 参数 flags 使用 tuple 保存，避免后续注册阶段被意外改写。
    tuple_flags = tuple(flags)  # 注册 argparse 时使用的短参和长参名称

    # 返回不可变参数规格，供命令注册阶段直接展开。
    return ArgSpec(tuple_flags, kwargs)

# _build_command_specs 固定旧版 verilog-gen 的 help 顺序，保护 smoke 与安装后命令发现断言。
# _template_command_specs 这些入口覆盖从输入规格到模型响应拆包的前半段生成合同。
def _template_command_specs() -> tuple[CommandSpec, ...]:
    """构造模板、prompt 和提取类子命令规格。

    参数:
        无外部业务参数。

    返回:
        模板、prompt 与响应提取子命令规格元组。
    """

    # 固定返回顺序，保证验证类子命令 help 输出稳定。
    return (
        CommandSpec(  # scaffold 模板生成入口规格
            "scaffold",  # 子命令公开名称
            "Create a Verilog JSON generation spec template.",  # argparse help 文本
            "scaffold",  # 处理器映射键
            (
                arg("--out", required=True, type=Path),  # 模板 JSON 输出路径参数
                arg("--name", help="Optional design name used in generated paths."),  # 可选设计名参数
            ),
            state=True,  # scaffold 会写 workflow-state 记录模板位置
        ),
        CommandSpec(
            "write-spec",
            "Write strict module specs and WaveDrom companion artifacts.",
            "write-spec",
            (
                arg("--spec", required=True, type=Path),
                arg("--out-dir", required=True, type=Path),
                arg(
                    "--source",  # 可重复提供用于接口精确比对的 RTL 源文件
                    action="append",
                    type=Path,
                    help="Optional Verilog source for exact module/port cross-check.",
                ),
                arg("--language", choices=("zh", "en"), default="zh"),
            ),
            trace=True,
            state=True,
        ),
        CommandSpec(
            "prompt",
            "Render a model prompt from a JSON spec.",
            "prompt",
            (
                arg("--spec", required=True, type=Path),
                arg("--out", required=True, type=Path),
                arg("--stage", choices=PROMPT_STAGES, default="rtl"),
                arg("--context-manifest", type=Path, help="Prior-stage manifest JSON or fenced response."),
                arg("--context-dir", type=Path, help="Directory containing prior-stage artifacts."),
                arg("--evidence", type=Path, help="Evidence JSON for understanding stages."),
                arg("--memory", type=Path, help="Prompt memory JSON to inject into staged prompts."),
                arg("--vector-contract", type=Path, help="Reference vector contract JSON produced by audit-vectors."),
                arg(
                    "--decision",
                    type=Path,
                    help="Resolved human decision JSON to inject as a high-priority constraint.",
                ),
                arg(
                    "--subfunction",
                    help="Restrict staged prompt context to one subfunction and its direct dependencies.",
                ),
                arg("--budget", choices=PROMPT_BUDGETS, default="normal"),
                arg("--comment-language", choices=COMMENT_LANGUAGES, default="zh"),
                arg("--stats-json", type=Path, help="Optional prompt size/context statistics JSON output."),
            ),
            trace=True,
            state=True,
        ),
        CommandSpec(
            "extract",
            "Extract manifest-listed files from a model response.",
            "extract",
            (arg("--response", required=True, type=Path), arg("--out-dir", required=True, type=Path)),
            trace=True,
            state=True,
        ),
    )

# _validation_command_specs 这些入口统一暴露静态检查、外部目标选择和格式质量报告参数。
def _validation_command_specs() -> tuple[CommandSpec, ...]:
    """构造本地验证与 Verilog 质量门子命令规格。

    参数:
        无外部业务参数。

    返回:
        validate 和 quality-gate 子命令规格元组。
    """

    # 固定 workflow 命令顺序，避免 batch 与 resume 入口漂移。
    return (
        CommandSpec(
            "validate",
            "Validate generated Verilog artifacts.",
            "validate",
            (
                arg("--spec", required=True, type=Path),
                arg("--path", required=True, type=Path),
                arg("--no-external", action="store_true", help="Skip optional external tools even if installed."),
                arg(
                    "--external-target",
                    choices=("remote", "local"),
                    default="remote",
                    help="Explicit target for external tools when readiness requires compile/execute/implement.",
                ),
                arg("--readiness", choices=READINESS_LEVELS, default="static"),
                arg("--comment-language", choices=COMMENT_LANGUAGES, default="zh"),
                arg("--report-json", type=Path, help="Optional structured validation report JSON output."),
                arg("--semantic-contract", type=Path, help="Optional Python semantic contract JSON."),
            ),
            trace=True,
            state=True,
        ),
        CommandSpec(
            "quality-gate",
            "Run formatter-AST Erie style/comment/naming checks on Verilog RTL.",
            "quality-gate",
            (
                arg("--path", required=True, type=Path),
                arg(
                    "--non-strict",
                    action="store_true",
                    help="Downgrade style/comment findings for legacy reference analysis.",
                ),
                arg("--comment-language", choices=COMMENT_LANGUAGES, default="zh"),
                arg("--formatter-profile", default="formatter-normalize"),
                arg("--include-testbench", action="store_true"),
                arg("--vitis-wrapper", action="store_true"),
                arg("--report-json", type=Path),
                arg("--report-md", type=Path),
                arg("--warn-only", action="store_true"),
            ),
            trace=True,
            state=True,
        ),
    )

# _review_command_spec 构造真实 review gate 入口。
def _review_command_spec() -> CommandSpec:
    """构造 review 子命令规格。

    参数:
        无外部业务参数。

    返回:
        运行 Verilog 交付门禁的 review 子命令规格。
    """

    # review 命令必须进入 deliverable gate，而不是只做路由分类。
    return CommandSpec(
        "review",
        "Run the Verilog deliverable gate and print review findings.",
        "review",
        (
            arg("--target", type=Path),
            arg("--spec", type=Path),
            arg("--report-json", type=Path),
            arg("--report-md", type=Path),
            arg("--formatter-profile", default="formatter-normalize"),
            arg("--comment-language", choices=COMMENT_LANGUAGES, default="zh"),
            arg("--non-strict", action="store_true"),
        ),
    )

# _execution_command_specs 这些入口保护 regular/deep_review 与 batch/workflow routing 合同。
def _execution_command_specs() -> tuple[CommandSpec, ...]:
    """构造端到端 workflow 与 batch 路由子命令规格。

    参数:
        无外部业务参数。

    返回:
        workflow、batch 和 route-workflow 子命令规格元组。
    """

    # 固定 workflow 入口顺序，便于用户按证据构建流程阅读 help。
    return (
        _review_command_spec(),
        CommandSpec(
            "run-workflow",
            "Run or resume an end-to-end staged workflow.",
            "run-workflow",
            (
                arg("--spec", type=Path, help="Input spec path for a new run."),
                arg("--out-dir", type=Path, help="Run directory for a new workflow execution."),
                arg("--resume", type=Path, help="Existing run directory to resume."),
                arg("--decision", type=Path, help="Resolved decision JSON for resume or replay."),
                arg("--evidence", type=Path, help="Optional evidence JSON used during initial decomposition."),
                arg("--model-provider", choices=("mock", "manual", "command"), default="manual"),
                arg("--model-command", help="External command used by the command provider."),
                arg("--generation-mode", choices=("regular", "deep_review"), default=None),
                arg(
                    "--stream",
                    action=argparse.BooleanOptionalAction,
                    default=None,
                    help="Use provider streaming when supported.",
                ),
                arg("--readiness", choices=READINESS_LEVELS, default="static"),
                arg("--max-attempts", type=int, default=3),
                arg(
                    "--no-external",
                    action="store_true",
                    help="Skip external tool execution during workflow validation.",
                ),
                arg(
                    "--external-target",
                    choices=("remote", "local"),
                    default="remote",
                    help="Explicit target for external tools when readiness requires compile/execute/implement.",
                ),
                arg("--comment-language", choices=COMMENT_LANGUAGES, default="zh"),
                arg("--model-timeout", type=int, default=120),
                arg("--stop-on-human", action=argparse.BooleanOptionalAction, default=True),
            ),
        ),
        CommandSpec(
            "run-batch",
            "Run multiple spec-to-RTL workflow cases and summarize their results.",
            "run-batch",
            (
                arg("--spec", required=True, action="append", type=Path),
                arg("--out-dir", required=True, type=Path),
                arg("--workflow-config", type=Path),
                arg("--evidence", type=Path),
                arg("--model-provider", choices=("mock", "manual", "command"), default="manual"),
                arg("--model-command", help="External command used by the command provider."),
                arg("--generation-mode", choices=("regular", "deep_review"), default=None),
                arg(
                    "--stream",
                    action=argparse.BooleanOptionalAction,
                    default=None,
                    help="Use provider streaming when supported.",
                ),
                arg("--readiness", choices=READINESS_LEVELS, default="static"),
                arg("--max-attempts", type=int, default=3),
                arg(
                    "--no-external",
                    action="store_true",
                    help="Skip external tool execution during workflow validation.",
                ),
                arg(
                    "--external-target",
                    choices=("remote", "local"),
                    default="remote",
                    help="Explicit target for external tools when readiness requires compile/execute/implement.",
                ),
                arg("--comment-language", choices=COMMENT_LANGUAGES, default="zh"),
                arg("--model-timeout", type=int, default=120),
                arg("--stop-on-human", action=argparse.BooleanOptionalAction, default=True),
            ),
        ),
        CommandSpec(
            "route-workflow",
            "Classify the safest Verilog workflow entry without executing it.",
            "route-workflow",
            (
                arg("--request-json", required=True, type=Path),
                arg("--artifact-dir", type=Path),
                arg("--out", required=True, type=Path),
            ),
        ),
    )

# _audit_command_specs 这些入口服务 Inversion 与 Reviewer 模式的前置证据构建。
def _audit_command_specs() -> tuple[CommandSpec, ...]:
    """构造向量、接口、参考模型和规格分解审计子命令规格。

    参数:
        无外部业务参数。

    返回:
        审计与规格摄取子命令规格元组。
    """

    # 固定反馈入口顺序，保持反思、决策和评价命令分组清晰。
    return (
        CommandSpec(
            "audit-vectors",
            "Create a semantic contract from semantic vectors JSON.",
            "audit-vectors",
            (arg("--vectors", required=True, type=Path), arg("--out", required=True, type=Path)),
            state=True,
        ),
        CommandSpec(
            "audit-interface",
            "Extract a stable interface contract from Python or Verilog artifacts.",
            "audit-interface",
            (
                arg("--target", required=True, choices=INTERFACE_TARGETS),
                arg("--path", required=True, type=Path),
                arg("--out", required=True, type=Path),
            ),
            trace=True,
            state=True,
        ),
        CommandSpec(
            "audit-semantic",
            "Execute a Python semantic model and emit a semantic contract.",
            "audit-semantic",
            (arg("--path", required=True, type=Path), arg("--out", required=True, type=Path)),
            trace=True,
            state=True,
        ),
        CommandSpec(
            "ingest-spec",
            "Ingest local text, Markdown, or TeX sources into evidence JSON.",
            "ingest-spec",
            (
                arg("--source", required=True, action="append", type=Path),
                arg("--sidecar", action="append", type=Path),
                arg("--out", required=True, type=Path),
            ),
            state=True,
        ),
        CommandSpec(
            "decompose",
            "Normalize a spec into a subfunction implementation plan.",
            "decompose",
            (
                arg("--spec", required=True, type=Path),
                arg("--evidence", type=Path),
                arg("--out", required=True, type=Path),
            ),
            state=True,
        ),
    )

# _feedback_command_specs 这些入口覆盖修复提示、人工决策归一化和 skill-effectiveness 评估。
def _feedback_command_specs() -> tuple[CommandSpec, ...]:
    """构造反思、干预解析与 eval 子命令规格。

    参数:
        无外部业务参数。

    返回:
        反馈、干预解析和 eval 子命令规格元组。
    """

    # 固定既有 RTL 入口顺序，保留 analyze/improve/compare/verify 的使用路径。
    return (
        CommandSpec(
            "reflect",
            "Create a repair prompt from a validation report and plan.",
            "reflect",
            (
                arg("--report", type=Path),
                arg("--report-json", type=Path),
                arg("--plan", required=True, type=Path),
                arg("--out", required=True, type=Path),
                arg("--repair-plan", type=Path),
                arg("--intervention-out", type=Path),
                arg("--diagnosis-out", type=Path),
            ),
            trace=True,
            state=True,
        ),
        CommandSpec(
            "optimize-prompt",
            "Generate a targeted prompt patch from trace history.",
            "optimize-prompt",
            (
                arg("--trace", required=True, type=Path),
                arg("--plan", required=True, type=Path),
                arg("--out", required=True, type=Path),
                arg("--memory-out", type=Path),
            ),
            state=True,
        ),
        CommandSpec(
            "resolve-intervention",
            "Convert a human answer into a decision and prompt memory.",
            "resolve-intervention",
            (
                arg("--intervention", required=True, type=Path),
                arg("--answer", required=True, type=Path),
                arg("--out", required=True, type=Path),
                arg("--memory-out", required=True, type=Path),
            ),
            trace=True,
            state=True,
        ),
        CommandSpec(
            "eval",
            "Compute workflow metrics from a trace JSONL file.",
            "eval",
            (arg("--trace", required=True, type=Path), arg("--out", required=True, type=Path)),
            state=True,
        ),
        CommandSpec(
            "eval-skill",
            "Run deterministic skill-effectiveness checks from eval cases.",
            "eval-skill",
            (
                arg(
                    "--workspace-root",
                    type=Path,
                    help="Explicit existing workspace root for eval inputs, outputs, and state.",
                ),
                arg("--evals", required=True, type=Path),
                arg("--out", required=True, type=Path),
                arg("--remote-runs-json", type=Path, help="Optional retained remote run summary JSON."),
                arg(
                    "--require-remote",
                    action="store_true",
                    help="Fail unless retained remote validation evidence is provided and healthy.",
                ),
            ),
            state=True,
        ),
    )

# _existing_rtl_command_specs 这些入口保护既有 RTL 审查、语义比较和验证修复主路径。
def _existing_rtl_command_specs() -> tuple[CommandSpec, ...]:
    """构造现有 RTL 分析、受控修改和 verify-repair 子命令规格。

    参数:
        无外部业务参数。

    返回:
        既有 RTL 分析、比较和 verify-repair 子命令规格元组。
    """

    # 返回 tuple 让该分组命令在运行期不可被追加或重排。
    return (
        CommandSpec(
            "analyze-existing",
            "Analyze existing Verilog modules into rtl_analysis.json.",
            "analyze-existing",
            (
                arg("--source", required=True, action="append", type=Path),
                arg("--out-dir", required=True, type=Path),
                arg("--spec-source", type=Path),
                arg("--module-name"),
            ),
            state=True,
        ),
        CommandSpec(
            "improve-existing",
            "Create a controlled improvement plan for existing RTL.",
            "improve-existing",
            (
                arg("--source", required=True, type=Path),
                arg("--out-dir", required=True, type=Path),
                arg(
                    "--goal",
                    required=True,
                    choices=(
                        "tb_scaffold",
                        "style_improve",
                        "partition_assist",
                        "optimize_assist",
                        "merge_assist",
                    ),
                ),
                arg("--analysis", type=Path),
                arg("--spec-source", type=Path),
                arg("--candidate-artifacts-dir", type=Path),
                arg("--baseline-artifacts-dir", type=Path),
                arg("--readiness", choices=READINESS_LEVELS, default="static"),
                arg("--tb-language", choices=TB_LANGUAGES, default="verilog"),
            ),
            state=True,
        ),
        CommandSpec(
            "compare-semantics",
            "Compare two RTL implementations for interface and checkpoint drift.",
            "compare-semantics",
            (
                arg("--baseline", required=True, type=Path),
                arg("--candidate", required=True, type=Path),
                arg("--out-dir", required=True, type=Path),
                arg("--no-external", action="store_true"),
                arg("--external-target", choices=("remote", "local"), default="remote"),
                arg("--readiness", choices=READINESS_LEVELS, default="static"),
            ),
            state=True,
        ),
        CommandSpec(
            "verify-existing",
            "Run the existing RTL verify-repair workflow.",
            "verify-existing",
            (
                arg("--source", required=True, action="append", type=Path),
                arg("--out-dir", required=True, type=Path),
                arg("--spec-source", type=Path),
                arg("--module-name"),
                arg("--testbench-source", type=Path),
                arg("--decision-source", type=Path),
                arg("--tb-mode", choices=TB_MODES, default="generate"),
                arg("--tb-language", choices=TB_LANGUAGES, default="verilog"),
                arg("--automation-mode", choices=AUTOMATION_MODES, required=True),
                arg("--readiness", choices=READINESS_LEVELS, default="static"),
                arg("--no-external", action="store_true"),
                arg("--external-target", choices=("remote", "local"), default="remote"),
            ),
            state=True,
        ),
        CommandSpec(
            "run-cases",
            "Run representative existing-RTL cases and write governed outputs.",
            "run-cases",
            (
                arg("--case", action="append", help="Optional representative case_id to run; may be repeated."),
                arg("--out-dir", type=Path, default=Path("runs") / "representative-10"),
            ),
        ),
    )

# _build_command_specs 汇总所有分组，保持旧版 verilog-gen 的 help 顺序。
def _build_command_specs() -> tuple[CommandSpec, ...]:
    """按公开 CLI 顺序汇总全部子命令规格。

    参数:
        无外部业务参数。

    返回:
        全部 verilog-gen 子命令规格元组。
    """

    # 星号展开保留各分组内部顺序，并让最终 tuple 不共享可变容器。
    return (
        *_template_command_specs(),
        *_validation_command_specs(),
        *_execution_command_specs(),
        *_audit_command_specs(),
        *_feedback_command_specs(),
        *_existing_rtl_command_specs(),
    )

# 子命令 tuple 是 build_cli_parser 的唯一注册来源。
tuple_command_specs = _build_command_specs()  # 复用同一份 verilog-gen 命令合同生成 argparse 子命令

# _create_root_parser 固定安装后帮助页的程序名和描述。
def _create_root_parser() -> argparse.ArgumentParser:
    """创建 verilog-gen 根命令解析器。

    参数:
        无外部业务参数。

    返回:
        尚未挂载子命令的 argparse 根解析器。
    """

    # 返回的根对象随后挂载 --version 与所有子命令。
    return argparse.ArgumentParser(
        prog="verilog-gen",
        description="Prompt engineering CLI for Verilog-2001 RTL generation.",
    )

# build_cli_parser 是 cli.py 和测试共同依赖的 parser 合同入口。
def build_cli_parser(command_handlers: Mapping[str, CommandHandler]) -> argparse.ArgumentParser:
    """根据命令规格表创建 argparse 解析器。

    参数:
        command_handlers: cli.py 提供的子命令处理器映射。

    返回:
        已注册全部子命令和 func 分发的根解析器。
    """

    # 此根对象持有 verilog-gen 固定描述、--version 文案和所有子命令的 func 分发。
    argument_parser_command_parser = _create_root_parser()  # 共享版本文案和子命令 func 分发状态

    # 版本参数直接复用源码版本常量，避免 CLI 文案与发布版本漂移。
    argument_parser_command_parser.add_argument(
        "--version",
        action="version",
        version=f"erie-verilog-gen {__version__}",
    )

    # 子解析器集合承载所有具体工作流入口。
    sub_parsers_action_subparser_actions: argparse._SubParsersAction[argparse.ArgumentParser] = (
        argument_parser_command_parser.add_subparsers(  # 挂载公开子命令分发表
        dest="command",  # 子命令名称保存字段
        required=True,  # 没有子命令时让 argparse 报错
        )
    )

    # 每个命令规格独立注册，避免 build_cli_parser 再次膨胀。
    for command_spec in tuple_command_specs:

        # 单个子命令的参数和 handler 绑定由注册器完成。
        _register_command(sub_parsers_action_subparser_actions, command_spec, command_handlers)

    # 返回构造完成的 CLI parser。
    return argument_parser_command_parser

# _register_command 负责把数据表规格落到 argparse 对象。
def _register_command(
    subparser_actions: argparse._SubParsersAction[argparse.ArgumentParser],
    command_spec: CommandSpec,
    command_handlers: Mapping[str, CommandHandler],
) -> None:
    """注册单个子命令及其 trace/state 公共参数。

    参数:
        subparser_actions: argparse 子命令分发表。
        command_spec: 当前要注册的子命令规格。
        command_handlers: cli.py 提供的子命令处理器映射。

    返回:
        无返回值。
    """

    # 子命令 parser 持有该命令的所有专属参数。
    argument_parser_child_parser: argparse.ArgumentParser = subparser_actions.add_parser(  # 子命令专属解析器
        command_spec.name,  # argparse add_parser 使用的命令 token
        help=command_spec.help_text,  # 子命令帮助文本
    )  # 子命令解析器

    # 逐项注册参数，保持规格表顺序就是 argparse help 顺序。
    for argument_spec in command_spec.arguments:

        # kwargs 转成 dict，避免 argparse 持有只读 Mapping 引起意外。
        dict_argument_kwargs = dict(argument_spec.kwargs)  # argparse 参数关键字

        # 单项参数注册必须展开 flags，兼容一参和多别名参数。
        argument_parser_child_parser.add_argument(*argument_spec.flags, **dict_argument_kwargs)

    # trace 参数只出现在需要写追加 trace 的命令上。
    if command_spec.trace:

        # trace 公共参数语义沿用旧 CLI。
        argument_parser_child_parser.add_argument("--trace", type=Path, help="Optional append-only trace JSONL path.")

    # workflow-state 参数只出现在会记录状态副作用的命令上。
    if command_spec.state:

        # state 选项让命令把阶段性状态写到指定 JSON 文件。
        argument_parser_child_parser.add_argument("--state", type=Path, help="Optional workflow-state JSON path.")

        # no-state 允许 smoke 和路由命令禁用默认状态副作用。
        argument_parser_child_parser.add_argument(
            "--no-state",
            action="store_true",
            help="Disable workflow-state updates.",
        )

    # handler key 明确绑定到 cli.py 中的命令处理函数。
    command_handler_func_command_handler: CommandHandler = command_handlers[command_spec.handler_key]  # 子命令处理函数

    # argparse 通过 func 属性分发到 cli.py 中的处理器。
    argument_parser_child_parser.set_defaults(func=command_handler_func_command_handler)
