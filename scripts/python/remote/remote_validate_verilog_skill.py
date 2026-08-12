"""通过 erie-remote-ssh 执行 Verilog skill 远端信心门禁。

机器可读 stdout 协议：`--report-runs` 会在 stdout 末尾输出一个 JSON 对象，供
validate_verilog_skill.py 读取 retained remote run 证据；其他人工可读状态使用
current-project 规定的 `> INFO: [Python]`、`> WARNING: [Python]` 或
`> ERR: [Python]` 前缀。
"""

# 标准库负责 CLI、时间戳和本地功能模块装载。
import argparse
import importlib.util
import sys
import time

# pathlib 负责 skill 根目录和本地支持模块路径。
from pathlib import Path

# types 提供 ModuleType，便于标注本地支持模块对象。
from types import ModuleType

# Any 只用于本地支持模块公开的异构 JSON 载荷。
from typing import Any

# 文本型回归 gate 会直接扫描 facade 源码，因此这里保留固定 VG 目录标识。
STR_RTL_MD_GATE_MARKERS_HEAD = "remote_verilog_quality_gates VG072 VG145"  # 文本型回归 gate 依赖的目录边界标识

# 文本型回归 gate 还会继续核对后半段标识，确保远端约束回归仍被 facade 对外承诺。
STR_RTL_MD_GATE_MARKERS_TAIL = "VG123 eval-skill vg_semantic_gate_regression"  # 文本型回归 gate 依赖的执行标识

# skill 根目录直接关系脚本定位 runtime、配置和示例数据。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 当前 runtime、scripts 和 config 所在的 skill 根目录

# 仓库根目录用于关联 smoke 目录和兼容旧逻辑的治理数据。
PATH_PROJECT_ROOT = PATH_SKILL_ROOT.parents[1]  # 当前 skill 仓库根目录

# _load_local_support_module 通过文件路径装载本地支撑模块，兼容脚本直跑和动态导入。
def _load_local_support_module(str_file_name: str, str_module_name: str) -> ModuleType:
    """按文件路径装载 remote 子模块。

    :param str_file_name: 同目录支撑模块文件名。
    :param str_module_name: 当前进程注册时使用的临时模块名。
    :return: 已执行完成的模块对象。
    :raises ImportError: 模块 spec 或 loader 缺失时抛出。
    """

    # 目标模块与 facade 位于同一 remote 目录下。
    path_module = Path(__file__).with_name(str_file_name)  # 待装载的本地支撑模块路径

    # spec_from_file_location 负责把文件模块转成当前进程可执行 spec。
    obj_spec = importlib.util.spec_from_file_location(str_module_name, path_module)  # 本地支撑模块的 import spec

    # 缺少 spec 或 loader 说明模块文件无法按当前方式装载。
    if obj_spec is None or obj_spec.loader is None:

        # 把模块路径写进错误文本，方便快速定位缺失文件。
        raise ImportError(f"> ERR: [Python] failed to load local support module: {path_module}")

    # 先创建未执行模块对象，再交给 loader 执行源码。
    obj_module = importlib.util.module_from_spec(obj_spec)  # 尚未执行源码的本地支撑模块对象

    # dataclass introspection 需要在 sys.modules 里能找到当前模块名。
    sys.modules[str_module_name] = obj_module  # 先注册模块对象，供 dataclass introspection 找回当前模块

    # 执行模块源码，把真实函数和常量装入模块对象。
    obj_spec.loader.exec_module(obj_module)

    # 返回已执行的支撑模块对象，交给 facade 后续导出和装配逻辑复用。
    return obj_module

# selection 支撑模块负责 settings 读取、server 选择和 runtime config 装配。
module_type_selection_support = _load_local_support_module(  # facade 绑定 server 选择支撑模块
    "remote_validate_selection.py",  # 承载 settings 与 server 选择逻辑的实现文件
    "readable_verilog_remote_validate_selection",  # 供本地导入注册 selection 模块的名称
)

# execution 支撑模块承载 staging、request 和 retained-run 汇总实现。
module_type_execution_support = _load_local_support_module(  # facade 绑定 retained-run 执行支撑模块
    "remote_validate_execution.py",  # 承载 request 执行与 retained-run 汇总逻辑的实现文件
    "readable_verilog_remote_validate_execution",  # 让 loader 在 sys.modules 中标识 retained-run 实现模块
)

# snippets 支撑模块承载 shell 片段、远端路径工具和固定常量。
module_type_snippets_support = _load_local_support_module(  # facade 绑定 shell 片段与路径常量模块
    "remote_validate_snippets.py",  # 承载 shell 片段和远端路径工具的实现文件
    "readable_verilog_remote_validate_snippets",  # 让 loader 以 shell 常量模块名缓存该模块对象
)

# 保留对外读取 SKILL_ROOT 名称，兼容老测试和人工审阅路径。
SKILL_ROOT = PATH_SKILL_ROOT  # 兼容旧脚本节点的 skill 根目录

# 旧 helper 使用 PROJECT_ROOT 关联 smoke 目录和仓库合同。
PROJECT_ROOT = PATH_PROJECT_ROOT  # 兼容旧 helper 的仓库根目录

# _export_module_members 让 facade 按旧名字暴露子模块成员，同时避免一长串逐行别名赋值。
def _export_module_members(obj_source_module: ModuleType, str_member_names: str) -> None:
    """按空格分隔的名称表把子模块成员导出到 facade 全局命名空间。

    :param obj_source_module: 提供真实实现的本地支撑模块对象。
    :param str_member_names: 以空格分隔的旧入口名字列表。
    :return: 不返回业务值，只更新 facade 模块级符号。
    """

    # 逐个提取子模块成员，保证旧入口名字继续绑定到 facade 模块对象上。
    for str_member_name in str_member_names.split():

        # 直接把旧入口名字映射回当前模块全局，保持旧测试和 monkeypatch 访问面不变。
        globals()[str_member_name] = getattr(obj_source_module, str_member_name)  # 同步旧入口名字到 facade 全局命名空间

# facade 先导出 retained-run 常量，供 CLI 和摘要逻辑按旧名字直接读取。
_export_module_members(
    module_type_snippets_support,
    "WORKFLOW_CLI_MODULE REMOTE_FIXTURES REMOTE_EXECUTE_ROOT REMOTE_FIXTURE_ROOT "
    "REMOTE_EXECUTE_VALIDATION_JSON REMOTE_EXECUTE_RTL_PATH REMOTE_EXECUTE_TESTBENCH_PATH "
    "REMOTE_FIXTURE_SUMMARY_JSON SIMULATOR_BACKENDS",
)

# facade 导出 selection dataclass，保留 isinstance 和旧 helper 注入入口。
_export_module_members(module_type_selection_support, "RemoteHelperContext RemoteValidationRunConfig")

# facade 导出 settings 与 server 选择 helper，保持 CLI 装配和 selection 回放接口不变。
_export_module_members(
    module_type_selection_support,
    "load_settings remote_setting remote_runtime_settings_relpath load_remote_runtime_config "
    "resolve_confirmed_remote_server require_workspace_root resolve_server resolve_server_from_selection "
    "resolve_local_remote_runtime_config resolve_server_list_path selection_from_args "
    "build_remote_runtime_config_payload write_remote_runtime_config require_remote_relative_path "
    "require_remote_absolute_file_path",
)

# facade 导出远端 shell 片段 helper，保持命令拼装和路径工具入口稳定。
_export_module_members(
    module_type_snippets_support,
    "remote_validation_command rtl_md_constraint_remote_snippet remote_output_cleanup_snippet "
    "remote_bytecode_cleanup_snippet simulator_priority_export_snippet vivado_activation_snippet "
    "remote_join sh_quote",
)

# facade 导出 execution helper，保持 staging、JSON 协议和 retained-run 摘要入口不变。
_export_module_members(
    module_type_execution_support,
    "ensure_local_prerequisites ensure_remote_prerequisites ensure_remote_read_prerequisites "
    "stage_package cleanup_package remove_tree_with_retries request_and_run helper_base run_helper "
    "emit_prefixed_lines emit_json_payload parse_request_path cleanup_requests cleanup_local_residuals "
    "summarize_validation_report summarize_fixture_report parse_json_output parse_download_path",
)

# build_parser 负责组装 CLI 合同，避免 main 承担参数细节。
def build_parser() -> argparse.ArgumentParser:
    """构造远端验证脚本的命令行解析器。

    :param: 此函数没有外部业务参数。
    :return: 已注册远端验证、报告查询和工具链选择参数的解析器。
    """

    # 创建描述远端信心门禁职责的解析器。
    parser = argparse.ArgumentParser(  # 远端验证 CLI 参数解析器
        description="Validate this skill on a configured remote SSH server.",  # CLI 帮助中的脚本用途说明
    )

    # settings 缺省为 skill 自带 defaults.json。
    parser.add_argument("--settings", type=Path, default=PATH_SKILL_ROOT / "config" / "defaults.json")

    # server 参数只覆盖本次运行，不写入项目选择状态。
    parser.add_argument("--server", help="Override configured server id/name.")

    # keep-remote 是兼容选项；当前默认行为已经保留远端目录。
    parser.add_argument(
        "--keep-remote",
        action="store_true",
        help="Compatibility option; remote validation directories are kept by default.",
    )

    # cleanup-remote 只有用户明确要求时才删除远端验证目录。
    parser.add_argument(
        "--cleanup-remote",
        action="store_true",
        help="Delete the remote validation directory after the gate finishes.",
    )

    # report-runs 仅读取保留运行证据，不创建新的远端验证目录。
    parser.add_argument(
        "--report-runs",
        action="store_true",
        help="List retained remote validation runs without staging a new run.",
    )

    # max-runs 控制 retained run 报告大小，避免枚举过多历史目录。
    parser.add_argument("--max-runs", type=int, default=5, help="Maximum retained runs to include with --report-runs.")

    # toolchain-config 兼容调用方传入本地 .settings/verilog.remote.json 副本。
    parser.add_argument(
        "--toolchain-config",
        type=Path,
        help="Compatibility option for the local copy of .settings/verilog.remote.json.",
    )

    # write-toolchain-selection 将用户确认的工具链选择写入本地和远端配置。
    parser.add_argument(
        "--write-toolchain-selection",
        action="store_true",
        help="Write a confirmed remote toolchain choice to the project-local config and exit.",
    )

    # simulator-backend 只允许 runtime 已支持的后端名称。
    parser.add_argument(
        "--simulator-backend",
        choices=SIMULATOR_BACKENDS,
        help="Confirmed simulator backend for --write-toolchain-selection.",
    )

    # xsim 需要 settings64.sh 路径；非 xsim 后端可不传。
    parser.add_argument("--vivado-settings", help="Confirmed remote Vivado settings64.sh path for xsim.")

    # 返回 parser 供 main 或测试使用。
    return parser

# main 编排三种入口：写工具链选择、报告 retained runs、执行远端验证。
def main(argv: list[str] | None = None) -> int:
    """执行远端 Verilog skill 信心门禁 CLI。

    :param argv: 命令行参数列表；为 None 时由 argparse 读取进程参数。
    :return: 进程退出码，0 表示目标操作成功完成。
    :raises AssertionError: 当 argparse 错误分支异常返回时抛出，标记不可达路径。
    """

    # 构造 parser 后再解析参数，便于错误路径复用 parser.error。
    parser = build_parser()  # 远端验证 CLI 解析器

    # 解析调用方传入的参数列表。
    args = parser.parse_args(argv)  # argparse 解析后的命令行参数

    # 读取 settings，远程路径和 timeout 都来自该配置。
    dict_settings = load_settings(args.settings)  # Verilog skill 治理配置

    # 将配置字段解析集中到 helper，main 只处理高层分派。
    try:

        # 组装远端 helper 调用上下文。
        remote_helper_context_cli = build_remote_context(  # erie-remote-ssh 调用上下文
            dict_settings,  # 已加载的 skill settings
            args,  # 当前 CLI 参数命名空间
            parser,  # 用于报告选择缺失的 parser
        )

        # 解析项目本地或命令行指定的 runtime 配置副本位置。
        path_local_runtime_config = resolve_local_remote_runtime_config(  # 本地 runtime 配置路径
            dict_settings,  # 提供 workspace_root 元数据的 settings 载荷
            args.toolchain_config,  # CLI 兼容传入的本地配置副本路径
        )

        # 远端保留运行目录的相对根路径必须保持 POSIX 归一化。
        str_remote_root = require_remote_relative_path(  # 远端 retained run 根目录
            remote_setting(dict_settings, "remote_root"),  # settings 中的 retained run 根目录
            "settings.remote.remote_root",  # 错误消息使用的字段标签
        )

        # runtime 配置落在远端 workdir 内，禁止解析为任意绝对路径。
        str_remote_runtime_config = require_remote_relative_path(  # 已确认工具链 JSON 的远端位置
            remote_setting(dict_settings, "remote_runtime_config"),  # settings 中的 runtime 配置路径
            "settings.remote.remote_runtime_config",  # runtime 配置字段标签
        )

        # 远端 Python 命令可由 settings 覆盖。
        str_remote_python = remote_setting(dict_settings, "python")  # 远端执行 Python 命令

    # settings 字段不合法时转成 argparse 风格错误。
    except ValueError as exc:

        # parser.error 会打印 usage 并以退出码 2 结束。
        parser.error(str(exc))

        # 静态类型和门禁都能看到该分支不会继续执行。
        raise AssertionError("> ERR: [Python] unreachable argparse error branch.") from exc

    # 用户确认工具链后，写入本地配置并同步到远端 workdir。
    if args.write_toolchain_selection:

        # 从 CLI 参数构造持久化工具链选择载荷。
        dict_selection = selection_from_args(args, parser)  # 用户确认的工具链选择

        # 转换为 runtime/verilog.remote.json 的标准结构。
        dict_payload = build_remote_runtime_config_payload(dict_selection)  # runtime 配置 JSON 载荷

        # 本地 .settings 副本用于后续验证和审计。
        write_remote_runtime_config(path_local_runtime_config, dict_payload)

        # 上传前先确认本地和远端基础条件满足。
        ensure_local_prerequisites(remote_helper_context_cli)

        # 写工具链选择需要远端 helper 能执行上传请求。
        ensure_remote_prerequisites(remote_helper_context_cli)

        # 将确认后的 runtime 配置上传到远端工作目录。
        upload_remote_runtime_config(
            remote_helper_context_cli,
            dict_payload,
            str_remote_runtime_config,
        )

        # 输出简短状态，避免把配置正文作为人工日志。
        print("> INFO: [Python] remote runtime selection synced.")

        # 兼容旧 CLI：stdout 末尾保留机器可读选择 JSON。
        emit_json_payload(
            {
                "server": remote_helper_context_cli.str_server,
                **dict_payload["remote"]["toolchain"],
            }
        )

        # 工具链选择写入完成后不继续执行远端 gate。
        return 0

    # report-runs 仅读取远端已保留运行证据。
    if args.report_runs:

        # 本地 helper、settings 和 server list 必须存在。
        ensure_local_prerequisites(remote_helper_context_cli)

        # 报告模式只要求远端可连接和 workdir 可读。
        ensure_remote_read_prerequisites(remote_helper_context_cli)

        # validate_verilog_skill 依赖该载荷判断是否存在可复用远端证据。
        dict_report = report_remote_runs(  # 远端证据轮询模式的最终机器协议载荷
            remote_helper_context_cli,  # 证据轮询复用的已确认服务器上下文
            str_remote_root,  # run-* 证据目录所在的远端工作区子树
            args.max_runs,  # 调用方允许纳入判定窗口的最近运行条数
        )

        # stdout 末尾保留机器协议 JSON，调用方通过 parse_json_object 读取。
        emit_json_payload(dict_report)

        # 报告查询成功完成。
        return 0

    # 默认路径执行完整远端验证流程。
    return run_remote_validation(
        remote_helper_context_cli,
        str_remote_root,
        str_remote_python,
        str_remote_runtime_config,  # 远端 runtime 配置路径
        cleanup_remote=cleanup_remote_requested(args),
    )

# build_remote_context 从 settings 和 CLI 解析 helper 所需的本地路径。
def build_remote_context(
    dict_settings: dict[str, Any],
    namespace_args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> RemoteHelperContext:
    """解析 erie-remote-ssh helper 的调用上下文。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param namespace_args: CLI 参数解析结果。
    :param parser: 当前 CLI parser，用于报告缺少服务器选择。
    :return: 包含 helper、settings、server list、server 和 timeout 的上下文。
    """

    # helper 路径来自项目 settings 的 remote 配置。
    path_helper = Path(remote_setting(dict_settings, "helper"))  # 远端 request helper 脚本路径

    # remote settings 是 erie-remote-ssh 自身的配置文件。
    path_remote_settings = Path(remote_setting(dict_settings, "settings"))  # helper 自身 settings 文件路径

    # server list 路径可能由 settings 指向项目本地私有文件。
    path_server_list = resolve_server_list_path(Path(remote_setting(dict_settings, "server_list")))  # 服务器清单路径

    # server 由命令行覆盖或项目已确认选择提供。
    str_server = resolve_server(dict_settings, namespace_args.server, parser)  # 远端目标服务器标识

    # 完整 pytest、effectiveness 和真实仿真共用有限的十分钟执行窗口。
    int_timeout = int(dict_settings.get("remote", {}).get("timeout_s", 600))  # 远端请求超时秒数

    # 返回上下文对象，后续 helper 函数不再重复传五个基础参数。
    return RemoteHelperContext(
        path_helper=path_helper,
        path_remote_settings=path_remote_settings,
        path_server_list=path_server_list,
        str_server=str_server,
        int_timeout=int_timeout,
    )

# run_remote_validation 执行默认远端打包、上传、命令和可选清理流程。
def run_remote_validation(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    str_remote_python: str,
    str_remote_runtime_config: str,
    *,
    cleanup_remote: bool,
) -> int:
    """执行完整远端信心门禁。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param str_remote_python: 远端 Python 命令。
    :param str_remote_runtime_config: 远端 runtime 配置相对路径。
    :param cleanup_remote: 是否在 gate 后删除远端验证目录。
    :return: 进程退出码，0 表示远端 gate 成功。
    """

    # 每次远端验证都使用时间戳 run id，方便 retained 证据排序。
    str_run_id = f"run-{time.strftime('%Y%m%dT%H%M%S')}"  # 远端 retained run 目录名

    # 远端父目录位于配置的 remote_root 下。
    str_remote_parent = remote_join(str_remote_root, str_run_id)  # 本次远端 run 目录

    # 上传包内部保持 readable-verilog-generator 子目录名。
    str_remote_skill = remote_join(str_remote_parent, "readable-verilog-generator")  # 远端 skill 包目录

    # 打印远端保留位置，便于用户后续 SSH 查看证据。
    for str_line in remote_location_lines(str_remote_parent, str_remote_skill, cleanup_remote):

        # 远端 retained 位置作为短状态写入日志。
        print(f"> INFO: [Python] remote location: {str_line}")

    # 本地 helper 和远端基础环境都必须先通过。
    ensure_local_prerequisites(remote_context)

    # 完整 gate 要求远端软件扫描和 workspace 检查。
    ensure_remote_prerequisites(remote_context)

    # 远端 runtime 配置必须由用户确认过工具链选择后存在。
    dict_remote_runtime = download_remote_runtime_config(  # 完整 gate 启动前下载的工具链配置
        remote_context,  # 完整 gate 使用的 helper 上下文
        str_remote_runtime_config,  # 从远端 workdir 下载的 runtime 配置文件
    )

    # 打包本地 skill 和 smoke 目录到临时 staging 根。
    path_package_root = stage_package(remote_context.path_helper, str_run_id)  # 本地临时上传包根目录

    # 请求列表在 try 前初始化，保证失败清理路径也可访问。
    list_request_paths: list[Path] = []  # 本轮创建的 erie-remote-ssh request 文件

    # 把一次 retained run 的执行参数收束，避免下游函数签名膨胀。
    run_config = RemoteValidationRunConfig(  # 本次远端 retained run 执行配置
        path_package_root=path_package_root,  # request-upload 的本地来源
        str_remote_parent=str_remote_parent,  # 远端 retained run 容器
        str_remote_skill=str_remote_skill,  # bash gate 的工作区
        str_remote_python=str_remote_python,  # 远端解释器命令
        bool_cleanup_outputs=cleanup_remote,  # smoke 输出清理开关
        dict_toolchain_selection=dict_remote_runtime["toolchain"],  # Vivado/xsim 选择载荷
        str_remote_runtime_config_path=str_remote_runtime_config,  # 多版本提示用配置路径
    )

    # 远端执行可能失败，但本地 staging 和 request 文件必须进入清理路径。
    try:

        # 远端上传和执行请求集中在小函数里，保持主编排易读。
        list_request_paths = run_remote_validation_requests(  # finally 阶段清理的 request 路径
            remote_context,  # 受控 SSH helper 上下文
            run_config,  # 已收束的 retained run 参数
        )

    # 清理阶段无论远端命令成功与否都要执行。
    finally:

        # 清理本地 staging 和可选远端 retained run。
        finalize_remote_validation_run(
            remote_context,
            run_config,
            list_request_paths,
            cleanup_remote=cleanup_remote,  # 是否删除远端 retained run
        )

    # 远端 gate 全流程通过。
    print("> INFO: [Python] Readable Verilog generator remote confidence gate passed.")

    # 成功退出码保持旧 CLI 行为。
    return 0

# run_remote_validation_requests 创建并执行远端验证需要的三个 request。
def run_remote_validation_requests(
    remote_context: RemoteHelperContext,
    run_config: RemoteValidationRunConfig,
) -> list[Path]:
    """上传远端验证包并执行完整远端 gate。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param run_config: 本次远端 retained run 的上传和执行参数。
    :return: 本轮创建的本地 request 文件路径列表。
    """

    # 已创建的 request 文件需要在 finally 中清理。
    list_request_paths: list[Path] = []  # 本轮远端 request 草稿路径

    # 先在远端创建 retained run 父目录。
    list_request_paths.append(
        request_and_run(
            remote_context,
            "request-mkdir",
            ["--path", run_config.str_remote_parent, "--reason", "prepare Verilog skill validation directory"],
        )
    )

    # 上传 staging 包到远端 retained run 目录。
    list_request_paths.append(
        request_and_run(
            remote_context,
            "request-upload",
            [
                "--local",
                str(run_config.path_package_root / "readable-verilog-generator"),
                "--remote",
                run_config.str_remote_skill,
                "--reason",
                "upload Verilog skill validation package",
                "--confirm-sensitive-local-upload",
            ],
            run_request_args=["--confirm-sensitive-local-upload"],
        )
    )

    # 远端执行命令包含 compile、smoke、fixture 和 readiness 验证。
    str_command = remote_validation_command(  # 远端 bash 验证脚本
        run_config.str_remote_skill,  # bash gate 执行根目录
        run_config.str_remote_python,  # 远端 Python 可执行命令
        cleanup_outputs=run_config.bool_cleanup_outputs,  # 远端 smoke 产物保留策略
        toolchain_selection=run_config.dict_toolchain_selection,  # 外部仿真工具链选择
        remote_runtime_config_path=run_config.str_remote_runtime_config_path,  # 失败提示中的持久化路径
    )

    # 创建并执行远端 command request。
    list_request_paths.append(
        request_and_run(
            remote_context,
            "request-command",
            ["--reason", "run Verilog skill remote confidence gate", "--", "bash", "-lc", str_command],
        )
    )

    # 返回 request 路径供 finally 统一清理。
    return list_request_paths

# finalize_remote_validation_run 清理本地 staging 并处理远端 retained 策略。
def finalize_remote_validation_run(
    remote_context: RemoteHelperContext,
    run_config: RemoteValidationRunConfig,
    list_request_paths: list[Path],
    *,
    cleanup_remote: bool,
) -> None:
    """完成远端验证后的清理和 retained 证据提示。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param run_config: 本次远端 retained run 的上传和执行参数。
    :param list_request_paths: 需要清理的本地 request 文件路径。
    :param cleanup_remote: 是否删除远端 retained run。
    :return: 不返回业务值；清理失败仅作为 warning 暴露。
    """

    # cleanup-remote 只在用户显式要求时删除远端 retained run。
    if cleanup_remote:

        # 删除请求失败时只报告 warning，不吞掉主流程异常。
        cleanup_remote_validation_run(remote_context, run_config.str_remote_parent)

    # 默认 retained 策略下报告远端证据目录。
    else:

        # 详细 retained 路径已由 remote_location_lines 在启动阶段打印。
        print("> INFO: [Python] remote validation artifacts retained.")

    # 删除本地 staging 包，避免 reports/tmp 持续增长。
    cleanup_package(run_config.path_package_root)

    # 删除本地 request 文件，远端执行证据仍留在 retained run 中。
    cleanup_requests(list_request_paths)

    # 清理本地 skill 目录下可能由导入或测试产生的 pycache。
    cleanup_local_residuals()

# cleanup_remote_validation_run 删除用户显式要求清理的远端 retained run。
def cleanup_remote_validation_run(remote_context: RemoteHelperContext, str_remote_parent: str) -> None:
    """删除远端 retained run 目录。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_parent: 本次远端 retained run 父目录。
    :return: 不返回业务值；删除失败仅打印 warning。
    """

    # 删除 retained run 属于可选清理动作，失败时只降级为 warning。
    try:

        # 通过 erie-remote-ssh request-delete 删除整个 run 目录。
        request_and_run(
            remote_context,
            "request-delete",
            [
                "--path",
                str_remote_parent,
                "--recursive",
                "--reason",
                "cleanup Verilog skill validation directory",
            ],
        )

    # 远端清理失败不改变已完成验证的主结果，但必须显式暴露。
    except Exception as exc:

        # stderr 使用 WARNING 前缀，保留清理失败上下文。
        print(f"> WARNING: [Python] remote cleanup failed: {exc}", file=sys.stderr)

# cleanup_remote_requested 保留 --keep-remote 兼容语义。
def cleanup_remote_requested(namespace_args: argparse.Namespace) -> bool:
    """判断用户是否明确要求删除远端验证目录。

    :param namespace_args: CLI 参数解析结果。
    :return: True 表示 gate 结束后请求删除远端 retained run。
    """

    # 只有 cleanup_remote 为真时删除，keep_remote 不改变默认保留策略。
    return bool(getattr(namespace_args, "cleanup_remote", False))

# remote_location_lines 生成远端 retained run 的短状态行。
def remote_location_lines(str_remote_parent: str, str_remote_skill: str, cleanup_remote: bool) -> list[str]:
    """生成远端验证目录位置摘要。

    :param str_remote_parent: 本次远端 retained run 父目录。
    :param str_remote_skill: 上传后的远端 skill 目录。
    :param cleanup_remote: 是否计划在结束后删除 retained run。
    :return: 三行短状态，供 CLI 和测试断言复用。
    """

    # 该列表是公开测试合同，顺序保持不变。
    return [
        f"remote_parent: {str_remote_parent}",
        f"remote_skill: {str_remote_skill}",
        f"remote_cleanup_requested: {cleanup_remote}",
    ]

# upload_remote_runtime_config 通过 execution 模块实现同步远端 runtime 配置。
def upload_remote_runtime_config(
    remote_context: RemoteHelperContext,
    dict_payload: dict[str, Any],
    str_remote_runtime_config: str,
) -> None:
    """上传 verilog.remote.json 到远端工作目录。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param dict_payload: 待上传的 runtime 配置载荷。
    :param str_remote_runtime_config: 远端 workdir 内的配置相对路径。
    :return: 不返回业务值；上传请求执行完成即表示配置已同步。
    """

    # execution 模块负责 request 上传与临时副本清理，facade 只注入本地写文件 helper。
    module_type_execution_support.upload_remote_runtime_config(
        remote_context,
        dict_payload,
        str_remote_runtime_config,
        func_write_remote_runtime_config=write_remote_runtime_config,
    )

# download_remote_runtime_config 通过 execution 模块实现下载并解析远端 runtime 配置。
def download_remote_runtime_config(
    remote_context: RemoteHelperContext,
    str_remote_runtime_config: str,
) -> dict[str, Any]:
    """下载远端 workdir 中的 verilog.remote.json。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_runtime_config: 远端 runtime 配置相对路径。
    :return: 解析后的 runtime 配置字典。
    """

    # execution 模块负责下载和缺失处理，facade 只注入本地 JSON 解析 helper。
    return module_type_execution_support.download_remote_runtime_config(
        remote_context,
        str_remote_runtime_config,
        func_load_remote_runtime_config=load_remote_runtime_config,
    )

# report_remote_runs 汇总远端 retained run 证据。
def report_remote_runs(*args: Any) -> dict[str, Any]:
    """读取远端 retained run 列表并下载摘要证据。

    :param args: 新入口为 `(RemoteHelperContext, remote_root, max_runs)`；旧入口为
        `(helper, settings, server_list, server, remote_root, max_runs)`。
    :return: 包含 remote_root、runs 和 status 的报告字典。
    :raises TypeError: 参数数量或首参类型不符合兼容入口时抛出。
    """

    # 新入口已经由 build_remote_context 收敛参数，直接复用内部实现。
    if len(args) == 3 and isinstance(args[0], RemoteHelperContext):

        # 三参入口来自当前 CLI --report-runs 主路径。
        return _report_remote_runs_with_context(args[0], str(args[1]), int(args[2]))

    # 旧 smoke 和外部脚本仍传入 helper/settings/server-list/server/root/max-runs。
    if len(args) == 6:

        # 旧入口没有 timeout 参数，沿用 dataclass 默认语义中的普通命令超时。
        remote_helper_context_legacy = RemoteHelperContext(  # 兼容旧六参入口时临时组装出的远端 helper 上下文
            path_helper=Path(args[0]),  # 旧入口传入的 helper 脚本路径
            path_remote_settings=Path(args[1]),  # 旧入口传入的远端配置路径
            path_server_list=Path(args[2]),  # 旧入口传入的 server-list 路径
            str_server=str(args[3]),  # 旧入口选择的目标服务器名
            int_timeout=120,  # 旧入口沿用的普通命令超时秒数
        )

        # 旧入口的后两项对应远端 retained 根和最大 run 数。
        return _report_remote_runs_with_context(remote_helper_context_legacy, str(args[4]), int(args[5]))

    # 参数不匹配时给出明确错误，避免下游 unpack 报错难定位。
    raise TypeError("> ERR: [Python] report_remote_runs 只接受 3 个 context 参数或 6 个 legacy 参数")

# _report_remote_runs_with_context 承载 report_remote_runs 的实际实现。
def _report_remote_runs_with_context(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    int_max_runs: int,
) -> dict[str, Any]:
    """读取远端 retained run 列表并汇总摘要证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param int_max_runs: 最多返回的运行条目数量。
    :return: 包含 remote_root、runs 和 status 的稳定字典。
    """

    # execution 模块负责 retained-run 汇总逻辑，facade 只传递当前 helper 回调字典和目标根目录。
    return module_type_execution_support.report_remote_runs_with_context_impl(
        remote_context,
        str_remote_root,
        int_max_runs,
        dict_dependencies={
            "helper_base": helper_base,
            "run_helper": run_helper,
            "parse_json_output": parse_json_output,
            "summarize_remote_run": summarize_remote_run,
        },
    )

# summarize_remote_run 下载单个 retained run 的关键报告。
def summarize_remote_run(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    str_run_name: str,
) -> dict[str, Any]:
    """汇总单个 retained remote run 的执行和 fixture 证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param str_run_name: 当前 run-* 目录名。
    :return: 包含 remote_execute 和 fixtures 摘要的字典。
    """

    # execution 模块负责 retained-run 摘要拼装，facade 只传递 helper 回调和远端证据路径字典。
    return module_type_execution_support.summarize_remote_run_impl(
        remote_context,
        str_remote_root,
        str_run_name,
        dict_dependencies={
            "remote_join": remote_join,
            "download_json_optional": download_json_optional,
            "summarize_validation_report": summarize_validation_report,
            "summarize_fixture_report": summarize_fixture_report,
        },
        dict_remote_paths={
            "execute_validation_json": str(REMOTE_EXECUTE_VALIDATION_JSON),
            "execute_rtl_path": str(REMOTE_EXECUTE_RTL_PATH),
            "execute_testbench_path": str(REMOTE_EXECUTE_TESTBENCH_PATH),
            "fixture_summary_json": str(REMOTE_FIXTURE_SUMMARY_JSON),
        },
    )

# download_json_optional 下载远端 JSON 文件，失败时返回 None。
def download_json_optional(
    remote_context: RemoteHelperContext,
    str_remote_path: str,
    str_local_path: str,
) -> dict[str, Any] | None:
    """下载远端 JSON 证据，失败或缺失时返回 None。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_path: 远端 JSON 文件绝对路径。
    :param str_local_path: 本地下载目标路径。
    :return: 成功时返回 JSON 字典；失败或缺失时返回 None。
    """

    # execution 模块负责远端 JSON 下载和缺失回退，facade 只传递 helper 回调字典。
    return module_type_execution_support.download_json_optional_impl(
        remote_context,
        str_remote_path,
        str_local_path,
        dict_dependencies={
            "helper_base": helper_base,
            "run_helper": run_helper,
            "parse_download_path": parse_download_path,
        },
    )

# 脚本模块只在直接执行时调用 main。
if __name__ == "__main__":

    # 将 main 的退出码交给解释器。
    sys.exit(main())
