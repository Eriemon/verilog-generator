"""通过 erie-remote-ssh 执行 Verilog skill 远端信心门禁。

机器可读 stdout 协议：`--report-runs` 会在 stdout 末尾输出一个 JSON 对象，供
validate_verilog_skill.py 读取 retained remote run 证据；其他人工可读状态使用
current-project 规定的 `> INFO: [Python]`、`> WARNING: [Python]` 或
`> ERR: [Python]` 前缀。
"""

# 标准库负责 CLI、JSON 协议、远端请求子进程、临时包 staging 和清理。
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

# dataclass 将远程 helper 的重复参数收束为单个上下文对象。
from dataclasses import dataclass

# pathlib 负责本地路径，PurePosixPath 负责远端工作目录内 POSIX 路径。
from pathlib import Path, PurePosixPath

# Any 只用于远端 runtime 配置和 JSON 报告这类异构载荷。
from typing import Any

# skill 主体根目录供直运行脚本定位 runtime 包和发布内容。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 包含 runtime、scripts 与 config 的 skill 根目录

# 仓库根目录用于复制 smoke 目录和保持旧公开常量兼容。
PATH_PROJECT_ROOT = PATH_SKILL_ROOT.parents[1]  # 当前 skill 仓库根目录

# workflow CLI 统一切到 scripts/python/workflow 官方模块入口。
WORKFLOW_CLI_MODULE = "scripts.python.workflow.cli"  # workflow 官方 CLI 模块名

# 远端固定 fixture 覆盖组合逻辑、流水线和 ready-valid 协议。
REMOTE_FIXTURES = (  # 远端内联 fixture 脚本生成三类 RTL 回归用例
    "comb_parity_mux",  # 覆盖组合奇偶校验与 mux 输出选择链路
    "pipeline_delay",  # 覆盖多拍寄存器延迟和复位后的数据推进
    "ready_valid_slice",  # 覆盖 ready-valid 反压握手与数据保持约束
)

# remote_execute attempt-001 是远端主流程的稳定证据根。
REMOTE_EXECUTE_ROOT = PurePosixPath("_smoke_runs") / "remote_execute" / "attempt-001"  # 主流程证据相对根

# remote_fixtures 保存三类小用例聚合报告。
REMOTE_FIXTURE_ROOT = PurePosixPath("_smoke_runs") / "remote_fixtures"  # fixture 证据相对根

# validation.json 提供主流程 ok、metrics 和产物映射。
REMOTE_EXECUTE_VALIDATION_JSON = REMOTE_EXECUTE_ROOT / "validation.json"  # 主流程 JSON 证据路径

# erie_adapter.v 是 retained run 摘要中的 RTL 人工复核入口。
REMOTE_EXECUTE_RTL_PATH = (REMOTE_EXECUTE_ROOT / "rtl" / "generated" / "rtl" / "erie_adapter.v")  # RTL 产物 retained 地址

# erie_adapter_tb.v 是 retained run 摘要中的仿真激励入口。
REMOTE_EXECUTE_TESTBENCH_PATH = (REMOTE_EXECUTE_ROOT / "rtl" / "generated" / "tb" / "erie_adapter_tb.v")  # 失败复盘的 testbench 入口

# summary.json 汇总三类远端 fixture 的执行状态。
REMOTE_FIXTURE_SUMMARY_JSON = REMOTE_FIXTURE_ROOT / "summary.json"  # fixture 汇总 JSON 证据路径

# simulator 后端枚举必须与 runtime validation 后端名称保持一致。
SIMULATOR_BACKENDS = ("xsim", "vcs_verdi", "iverilog")  # 可持久化的仿真后端

# 旧测试和调用方仍读取 SKILL_ROOT 名称，保留别名避免扩大改动面。
SKILL_ROOT = PATH_SKILL_ROOT  # 兼容旧脚本入口的 skill 根目录

# 旧 helper 使用 PROJECT_ROOT 访问 smoke 目录，别名保持公开合同稳定。
PROJECT_ROOT = PATH_PROJECT_ROOT  # 兼容旧 helper 的仓库根目录

@dataclass(frozen=True)
class RemoteHelperContext:
    """保存一次 erie-remote-ssh helper 调用所需的稳定参数。

    :param path_helper: erie-remote-ssh 的本地 helper 脚本路径。
    :param path_remote_settings: erie-remote-ssh 的 settings 文件路径。
    :param path_server_list: 本地私有 server_list.local.json 路径。
    :param str_server: 已由用户显式指定或项目确认的远程服务器标识。
    :param int_timeout: run-request 执行远程请求时使用的秒级超时。
    """

    # helper 脚本必须存在且是普通文件。
    path_helper: Path  # 创建和执行远端 request 的 helper 脚本路径

    # remote settings 由 erie-remote-ssh 解释，不复制到远端。
    path_remote_settings: Path  # helper 读取服务器策略的 settings 文件路径

    # server list 保存本地私有连接信息，禁止进入远端包。
    path_server_list: Path  # 本地私有服务器清单路径

    # server id/name 来自用户确认或命令行覆盖。
    str_server: str  # 本轮远端验证目标服务器

    # timeout 只用于执行已创建的远端请求。
    int_timeout: int  # 远端请求执行超时时间

@dataclass(frozen=True)
class RemoteValidationRunConfig:
    """保存一次远端 retained run 的上传和执行参数。

    :param path_package_root: 本地 staging 包根目录。
    :param str_remote_parent: 本次远端 retained run 父目录。
    :param str_remote_skill: 远端上传后的 skill 工作区。
    :param str_remote_python: 远端 Python 命令。
    :param bool_cleanup_outputs: 是否清理远端 smoke 输出。
    :param dict_toolchain_selection: 已确认的远端工具链选择。
    :param str_remote_runtime_config_path: 远端 runtime 配置相对路径。
    """

    # staging 包是 request-upload 的本地源目录。
    path_package_root: Path  # 本地 staging 包根目录

    # retained 父目录承载本次远端运行证据。
    str_remote_parent: str  # 本次远端 retained run 父目录

    # skill 工作区是远端 bash gate 的执行根。
    str_remote_skill: str  # 远端上传后的 skill 工作区

    # 远端 Python 命令来自 skill settings。
    str_remote_python: str  # 远端 Python 命令

    # cleanup_outputs 与 --cleanup-remote 共享用户清理意图。
    bool_cleanup_outputs: bool  # 是否清理远端 smoke 输出

    # 工具链选择来自远端 workdir 中已确认的 runtime 配置。
    dict_toolchain_selection: dict[str, Any]  # 已确认的远端工具链选择

    # runtime 配置路径用于多 Vivado 候选提示和审计。
    str_remote_runtime_config_path: str  # 远端 runtime 配置相对路径

# _ensure_runtime_import_path 只在需要 runtime helper 时调整导入路径。
def _ensure_runtime_import_path() -> None:
    """确保脚本从仓库任意位置运行时可导入 runtime 包。

    :param: 此函数没有外部业务参数。
    :return: 不返回业务值；执行后当前进程可解析 skill-local runtime 包。
    """

    # 禁止验证脚本生成 pyc，避免污染 installable skill 目录。
    sys.dont_write_bytecode = True  # 当前进程是否写入 Python 字节码缓存

    # sys.path 用字符串比较，避免 Path 对象和字符串混用造成重复插入。
    str_skill_root = str(PATH_SKILL_ROOT)  # runtime 包所在目录的字符串形式

    # 仅在缺少 skill 根目录时补入导入路径，避免重复改变搜索顺序。
    if str_skill_root not in sys.path:

        # 脚本直运行时需要把 skill 根目录放在 import 搜索最前面。
        sys.path.insert(0, str_skill_root)

# load_settings 包装 runtime 配置加载器，避免模块导入阶段触碰 sys.path。
def load_settings(path_settings: Path) -> dict[str, Any]:
    """读取 Verilog skill settings 配置。

    :param path_settings: 本次远端验证使用的 settings JSON 路径。
    :return: 配置加载器解析后的 settings 字典。
    """

    # runtime 包在函数内延迟导入，消除 import-time sys.path 副作用。
    _ensure_runtime_import_path()

    # 配置加载器定义 skill-local settings 的合并与默认值规则。
    from scripts.python.workflow.config import load_settings as func_load_settings

    # 返回 runtime 配置加载器的原始字典结果，保持调用方可见字段不变。
    return func_load_settings(path_settings)

# remote_setting 包装配置读取器，统一处理 remote.integration 兼容字段。
def remote_setting(dict_settings: dict[str, Any], str_key: str) -> str:
    """读取 remote 配置中的指定字符串字段。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param str_key: remote 配置字段名。
    :return: 解析后的远程配置字符串。
    """

    # runtime 包在需要读取配置字段时再进入导入路径。
    _ensure_runtime_import_path()

    # remote_setting 负责兼容旧 remote 字段和 integration 字段。
    from scripts.python.workflow.config import remote_setting as func_remote_setting

    # 返回字符串配置，缺失和类型错误由 runtime helper 统一抛出。
    return func_remote_setting(dict_settings, str_key)

# remote_runtime_settings_relpath 包装 runtime 默认远端配置相对路径。
def remote_runtime_settings_relpath() -> str:
    """返回远端 workdir 中 verilog.remote.json 的默认相对路径。

    :param: 此函数没有外部业务参数。
    :return: 远端 runtime 配置文件的 POSIX 相对路径。
    """

    # 延迟导入避免脚本被测试加载时写入 import 路径。
    _ensure_runtime_import_path()

    # runtime 配置模块保存当前 skill 的远端 runtime 配置路径约定。
    from scripts.python.workflow.config import (
        remote_runtime_settings_relpath as func_remote_runtime_settings_relpath,
    )

    # 返回调用方用于提示用户持久化工具链选择的路径。
    return func_remote_runtime_settings_relpath()

# load_remote_runtime_config 作为公开包装函数供 smoke 测试和 CLI 复用。
def load_remote_runtime_config(path_config: Path) -> dict[str, Any]:
    """读取远端工作目录内的 runtime toolchain 配置副本。

    :param path_config: 本地下载或项目本地保存的 verilog.remote.json 路径。
    :return: 规范化后的 remote runtime 配置字典。
    """

    # remote_selection 模块实现字段校验和默认结构整理。
    _ensure_runtime_import_path()

    # 延迟导入避免 import-time 访问项目本地远程状态。
    from scripts.python.remote.remote_selection import (
        load_remote_runtime_config as func_load_remote_runtime_config,
    )

    # 直接返回 runtime helper 的解析结果，保持旧 API 形状。
    return func_load_remote_runtime_config(path_config)

# resolve_confirmed_remote_server 包装项目本地已确认远程服务器选择。
def resolve_confirmed_remote_server(dict_settings: dict[str, Any]) -> dict[str, Any] | None:
    """解析项目本地已确认的远程服务器选择。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :return: 选择载荷；未确认时返回 None。
    """

    # 远程选择读取仅在真正需要服务器时发生。
    _ensure_runtime_import_path()

    # remote_selection 负责读取 .settings/remote-selection.local.json。
    from scripts.python.remote.remote_selection import (
        resolve_confirmed_remote_server as func_resolve_confirmed_remote_server,
    )

    # 返回 None 表示用户尚未确认远程服务器。
    return func_resolve_confirmed_remote_server(dict_settings)

# require_workspace_root 包装项目根发现逻辑。
def require_workspace_root(*, purpose: str) -> Path:
    """查找当前工作区根目录。

    :param purpose: 调用方说明，用于工作区缺失时的错误文本。
    :return: 已确认的工作区根目录路径。
    """

    # workspace helper 只在解析项目本地配置文件位置时需要。
    _ensure_runtime_import_path()

    # runtime.workspace 承载 AGENTS/workspace marker 的根发现策略。
    from scripts.python.workflow.workspace import require_workspace_root as func_require_workspace_root

    # 返回 Path 对象供调用方继续拼接 .settings 路径。
    return func_require_workspace_root(purpose=purpose)

# build_parser 集中声明 CLI 合同，避免 main 混入参数细节。
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
        remote_helper_context_remote_helper_context = build_remote_context(  # erie-remote-ssh 调用上下文
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
        ensure_local_prerequisites(remote_helper_context_remote_helper_context)

        # 写工具链选择需要远端 helper 能执行上传请求。
        ensure_remote_prerequisites(remote_helper_context_remote_helper_context)

        # 将确认后的 runtime 配置上传到远端工作目录。
        upload_remote_runtime_config(
            remote_helper_context_remote_helper_context,
            dict_payload,
            str_remote_runtime_config,
        )

        # 输出简短状态，避免把配置正文作为人工日志。
        print("> INFO: [Python] remote runtime selection synced.")

        # 兼容旧 CLI：stdout 末尾保留机器可读选择 JSON。
        emit_json_payload(
            {
                "server": remote_helper_context_remote_helper_context.str_server,
                **dict_payload["remote"]["toolchain"],
            }
        )

        # 工具链选择写入完成后不继续执行远端 gate。
        return 0

    # report-runs 仅读取远端已保留运行证据。
    if args.report_runs:

        # 本地 helper、settings 和 server list 必须存在。
        ensure_local_prerequisites(remote_helper_context_remote_helper_context)

        # 报告模式只要求远端可连接和 workdir 可读。
        ensure_remote_read_prerequisites(remote_helper_context_remote_helper_context)

        # validate_verilog_skill 依赖该载荷判断是否存在可复用远端证据。
        dict_report = report_remote_runs(  # 远端证据轮询模式的最终机器协议载荷
            remote_helper_context_remote_helper_context,  # 证据轮询复用的已确认服务器上下文
            str_remote_root,  # run-* 证据目录所在的远端工作区子树
            args.max_runs,  # 调用方允许纳入判定窗口的最近运行条数
        )

        # stdout 末尾保留机器协议 JSON，调用方通过 parse_json_object 读取。
        emit_json_payload(dict_report)

        # 报告查询成功完成。
        return 0

    # 默认路径执行完整远端验证流程。
    return run_remote_validation(
        remote_helper_context_remote_helper_context,
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

    # timeout 缺省值与旧脚本保持一致。
    int_timeout = int(dict_settings.get("remote", {}).get("timeout_s", 120))  # 远端请求超时秒数

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

    # 上传包内部保持 erie-verilog-generator 子目录名。
    str_remote_skill = remote_join(str_remote_parent, "erie-verilog-generator")  # 远端 skill 包目录

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
    print("> INFO: [Python] Erie Verilog generator remote confidence gate passed.")

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
                str(run_config.path_package_root / "erie-verilog-generator"),
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

# resolve_server 读取显式服务器或项目已确认服务器。
def resolve_server(
    dict_settings: dict[str, Any],
    str_arg_server: str | None,
    parser: argparse.ArgumentParser,
) -> str:
    """解析本次远端验证使用的服务器标识。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param str_arg_server: CLI 显式传入的服务器标识。
    :param parser: 当前 CLI parser，用于报告缺少选择。
    :return: 远端服务器 id/name。
    :raises AssertionError: 当 parser.error 异常返回时抛出，标记不可达路径。
    """

    # 命令行显式服务器优先，不写回项目状态。
    if str_arg_server:

        # 返回本次 CLI 覆盖值。
        return str_arg_server

    # 没有 CLI 覆盖时读取项目已确认选择。
    dict_selection = resolve_server_from_selection(dict_settings)  # 项目本地远程选择

    # 已确认选择提供 server_id。
    if dict_selection:

        # server_id 转字符串传给 erie-remote-ssh。
        return str(dict_selection["server_id"])

    # 未确认服务器时要求用户选择，避免默认误连远端。
    parser.error(
        "> ERR: [Python] Remote server is not confirmed. Pass --server after the user selects a target."
    )

    # parser.error 已退出；该 raise 仅服务静态分析。
    raise AssertionError("> ERR: [Python] unreachable missing remote server branch.")

# resolve_server_from_selection 隔离远程选择读取，便于测试替换。
def resolve_server_from_selection(dict_settings: dict[str, Any]) -> dict[str, Any] | None:
    """读取项目本地确认的远程服务器选择。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :return: 选择字典；未确认时返回 None。
    """

    # 直接委托 runtime 选择解析器。
    return resolve_confirmed_remote_server(dict_settings)

# resolve_local_remote_runtime_config 决定本地 verilog.remote.json 副本路径。
def resolve_local_remote_runtime_config(
    dict_settings: dict[str, Any],
    path_arg_config: Path | None = None,
) -> Path:
    """解析项目本地 remote runtime 配置文件路径。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param path_arg_config: CLI 显式传入的工具链配置路径。
    :return: 规范化后的本地 verilog.remote.json 路径。
    """

    # 显式路径用于兼容旧调用方。
    if path_arg_config:

        # 返回展开后的绝对路径，避免后续 cwd 改变含义。
        return path_arg_config.expanduser().resolve()

    # settings meta 可直接提供 workspace root。
    dict_meta = dict_settings.get("__verilog_settings_meta__", {})  # settings 加载器注入的元信息

    # workspace_root 缺失时稍后用 workspace marker 自动发现。
    path_workspace_root: Path | None = None  # 项目工作区根目录候选

    # settings meta 中的 workspace_root 优先于 cwd 自动发现。
    if isinstance(dict_meta, dict) and dict_meta.get("workspace_root"):

        # 将 meta 中的路径文本转为 Path 对象。
        path_workspace_root = Path(str(dict_meta.get("workspace_root")))  # settings meta 指定的项目根目录

    # 没有 meta 根目录时从当前 cwd 向上查找 AGENTS/workspace marker。
    if path_workspace_root is None:

        # workspace helper 会在失败时给出 purpose 说明。
        path_workspace_root = require_workspace_root(purpose="local remote runtime config")  # 自动发现的项目根目录

    # runtime 配置副本固定落在项目本地 .settings 下。
    return (path_workspace_root / remote_runtime_settings_relpath()).resolve()

# resolve_server_list_path 规范化 server list 路径但不做 fallback 猜测。
def resolve_server_list_path(path_configured: Path) -> Path:
    """规范化 erie-remote-ssh server list 路径。

    :param path_configured: settings 中配置的 server list 路径。
    :return: 展开用户目录后的绝对路径。
    """

    # server_list.local.json 是本地私有文件，只在本机解析。
    path_resolved = path_configured.expanduser().resolve()  # 规范化后的 server list 路径

    # 返回路径对象供 prerequisite 检查使用。
    return path_resolved

# selection_from_args 将 CLI 选择转换成持久化字段。
def selection_from_args(namespace_args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, str | bool]:
    """从 CLI 参数构造远端工具链选择。

    :param namespace_args: CLI 参数解析结果。
    :param parser: 当前 CLI parser，用于报告缺少后端或 settings64.sh。
    :return: 包含 simulator_backend、确认标记、时间戳和可选 settings64.sh 的字典。
    """

    # simulator_backend 是写配置模式的必填字段。
    str_backend = namespace_args.simulator_backend  # 用户为远端验证确认的仿真后端

    # 缺少后端时无法写出有效 runtime 配置。
    if not str_backend:

        # parser.error 使用退出码 2 报告用户输入错误。
        parser.error("> ERR: [Python] --write-toolchain-selection requires --simulator-backend.")

    # 记录用户确认时间，便于审计工具链选择来源。
    dict_selection: dict[str, str | bool] = {  # 工具链选择载荷
        "simulator_backend": str(str_backend),  # 已确认仿真后端
        "confirmed_by_user": True,  # 该配置来自用户明确选择
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),  # UTC 更新时间
    }

    # xsim 必须指定 Vivado settings64.sh，否则远端多版本时不可判定。
    if str_backend == "xsim":

        # xsim 没有 settings64.sh 时立即阻断。
        if not namespace_args.vivado_settings:

            # 提示用户补充远端绝对路径。
            parser.error("> ERR: [Python] --simulator-backend xsim requires --vivado-settings.")

        # 校验并保存 settings64.sh 绝对 POSIX 路径。
        dict_selection["vivado_settings64"] = require_remote_absolute_file_path(  # xsim 的 Vivado 激活脚本路径
            namespace_args.vivado_settings,  # 用户确认的远端 settings64.sh 路径
            "--vivado-settings",  # 非 xsim 附带 Vivado 路径的 CLI 错误标签
        )

    # 非 xsim 后端也允许额外保存 Vivado 路径，供后续切回 xsim 使用。
    elif namespace_args.vivado_settings:

        # 额外路径同样必须满足远端绝对 POSIX 文件路径规则。
        dict_selection["vivado_settings64"] = require_remote_absolute_file_path(  # 非 xsim 后端附带记录的 Vivado 路径
            namespace_args.vivado_settings,  # 非 xsim 后端附带保存的 Vivado 路径
            "--vivado-settings",  # CLI 参数名用于错误提示
        )

    # 返回可直接交给 build_remote_runtime_config_payload 的选择字典。
    return dict_selection

# build_remote_runtime_config_payload 生成 verilog.remote.json 标准结构。
def build_remote_runtime_config_payload(dict_selection: dict[str, Any]) -> dict[str, Any]:
    """把工具链选择封装为 remote runtime 配置载荷。

    :param dict_selection: 用户确认的工具链选择字段。
    :return: 可写入 verilog.remote.json 的标准配置字典。
    """

    # 基础结构保留 env/tools 空字典，方便未来远端运行时扩展。
    dict_payload: dict[str, Any] = {  # runtime 配置 JSON 根对象
        "version": 1,  # runtime 配置结构版本
        "remote": {  # 远端运行时配置分组
            "toolchain": {  # 仿真工具链选择分组
                "simulator_backend": dict_selection["simulator_backend"],  # validation 后端优先级使用的确认值
            },
            "env": {},  # 预留远端环境变量覆盖
            "tools": {},  # 预留远端工具路径覆盖
        },
    }

    # Vivado settings64.sh 只有存在且非空时写入。
    str_vivado_settings = str(dict_selection.get("vivado_settings64") or "")  # 可选 Vivado settings64.sh 路径

    # 空字符串不进入配置，避免误导非 xsim 后端。
    if str_vivado_settings.strip():

        # 保存去除首尾空白后的远端绝对路径。
        dict_payload["remote"]["toolchain"]["vivado_settings64"] = str_vivado_settings.strip()  # xsim 激活脚本路径

    # 返回标准结构供本地写入和远端上传共用。
    return dict_payload

# write_remote_runtime_config 按稳定格式写入本地 runtime 配置副本。
def write_remote_runtime_config(path_config: Path, dict_payload: dict[str, Any]) -> None:
    """写入 verilog.remote.json 配置文件。

    :param path_config: 本地目标配置路径。
    :param dict_payload: 待写入的 runtime 配置字典。
    :return: 不返回业务值；文件写入完成即表示配置已落盘。
    """

    # 确保项目本地 .settings 目录存在。
    path_config.parent.mkdir(parents=True, exist_ok=True)

    # 以 UTF-8 和稳定缩进写出，便于审计 diff。
    path_config.write_text(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# ensure_local_prerequisites 校验本地 helper、settings 和 server list。
def ensure_local_prerequisites(remote_context: RemoteHelperContext) -> None:
    """校验 erie-remote-ssh 本地调用文件是否齐备。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :return: 不返回业务值；缺失或类型错误会抛出异常。
    :raises FileNotFoundError: helper、settings 或 server list 缺失时抛出。
    :raises ValueError: helper 路径存在但不是普通文件时抛出。
    """

    # 三个本地文件分别承担 request 创建、插件配置和私有连接信息。
    tuple_required_paths = (  # ensure_local_prerequisites 按标签报错的三项本地文件表
        (remote_context.path_helper, "erie-remote-ssh helper"),  # 创建 request 文件的 Python helper 脚本
        (remote_context.path_remote_settings, "erie-remote-ssh settings"),  # 限制 helper 行为的插件设置文件
        (remote_context.path_server_list, "server list"),  # 保存服务器登录信息的本地私有清单
    )

    # 逐项检查本地前置文件是否存在。
    for path_required, str_label in tuple_required_paths:

        # 缺失文件会导致 erie-remote-ssh 无法安全执行。
        if not path_required.exists():

            # 错误文本包含标签和路径，便于用户补齐配置。
            raise FileNotFoundError(f"> ERR: [Python] Missing {str_label}: {path_required}")

    # helper 必须是普通脚本文件，不能是目录。
    if not remote_context.path_helper.is_file():

        # 非文件 helper 表示配置指向错误目标。
        raise ValueError(f"> ERR: [Python] Remote helper is not a file: {remote_context.path_helper}")

# ensure_remote_prerequisites 执行完整远端验证前置探测。
def ensure_remote_prerequisites(remote_context: RemoteHelperContext) -> None:
    """运行远端连接、软件扫描和 workspace 检查。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :return: 不返回业务值；任一 helper 子命令失败会退出当前进程。
    """

    # 完整预检把同一组连接 flags 传给发现、连通性、软件扫描和工作区检查。
    list_base = helper_base(remote_context)  # 五段远端预检共享的连接参数切片

    # discover 确认配置可被 helper 读取。
    run_helper(remote_context.path_helper, ["discover", *list_base, "--json"])

    # list 输出候选服务器，方便日志审计目标。
    run_helper(remote_context.path_helper, ["list", *list_base])

    # check 确认目标服务器连接可用。
    run_helper(remote_context.path_helper, ["check", *list_base, "--server", remote_context.str_server])

    # scan-software 确认远端工具链候选。
    run_helper(remote_context.path_helper, ["scan-software", *list_base, "--server", remote_context.str_server])

    # workspace-check 确认远端工作目录可用。
    run_helper(remote_context.path_helper, ["workspace-check", *list_base, "--server", remote_context.str_server])

# ensure_remote_read_prerequisites 只校验读取 retained runs 所需能力。
def ensure_remote_read_prerequisites(remote_context: RemoteHelperContext) -> None:
    """运行报告模式需要的远端读取前置检查。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :return: 不返回业务值；远端不可读时 helper 会失败。
    """

    # 报告模式只读命令共用 settings 和 server list。
    list_base = helper_base(remote_context)  # retained run 查询共用的 helper 参数

    # discover 先确认 helper 配置有效。
    run_helper(remote_context.path_helper, ["discover", *list_base, "--json"])

    # list 保留旧日志行为。
    run_helper(remote_context.path_helper, ["list", *list_base])

    # check 确认目标服务器可连接。
    run_helper(remote_context.path_helper, ["check", *list_base, "--server", remote_context.str_server])

    # report-runs 不需要软件扫描，只要求 workspace 可读。
    run_helper(remote_context.path_helper, ["workspace-check", *list_base, "--server", remote_context.str_server])

# upload_remote_runtime_config 将确认配置写入远端 workdir。
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

    # 临时上传副本放在 reports/tmp 下，避免进入 skill 包。
    path_temp_dir = (  # 上传 verilog.remote.json 前使用的本地临时目录
        remote_context.path_helper.resolve().parents[1] / "reports" / "tmp" / "verilog-generator-runtime-upload"  # helper 项目 reports/tmp 子目录
    )

    # 确保临时目录存在。
    path_temp_dir.mkdir(parents=True, exist_ok=True)

    # 本地副本文件名固定，便于清理。
    path_local_copy = path_temp_dir / "verilog.remote.json"  # 待上传的临时配置文件

    # 将 payload 写成真实文件后交给 erie-remote-ssh 上传。
    write_remote_runtime_config(path_local_copy, dict_payload)

    # 记录创建的请求文件，finally 中统一清理。
    list_request_paths: list[Path] = []  # 上传配置产生的 request 文件

    # mkdir 和 upload 两步必须成对清理本地 request。
    try:

        # 远端配置父目录可能尚未存在。
        list_request_paths.append(
            request_and_run(
                remote_context,
                "request-mkdir",
                [
                    "--path",
                    str(PurePosixPath(str_remote_runtime_config).parent),
                    "--reason",
                    "prepare remote Verilog runtime settings directory",
                ],
            )
        )

        # 上传本地临时 verilog.remote.json 到远端相对路径。
        list_request_paths.append(
            request_and_run(
                remote_context,
                "request-upload",
                [
                    "--local",
                    str(path_local_copy),
                    "--remote",
                    str_remote_runtime_config,
                    "--reason",
                    "write remote Verilog runtime settings",
                    "--confirm-sensitive-local-upload",
                ],
                run_request_args=["--confirm-sensitive-local-upload"],
            )
        )

    # 上传结束后清理本地 request 和临时副本。
    finally:

        # 删除本地 request 文件。
        cleanup_requests(list_request_paths)

        # 临时配置副本不能长期保留在 reports/tmp。
        if path_local_copy.exists():

            # 删除已上传的临时 JSON 副本。
            path_local_copy.unlink()

# download_remote_runtime_config 下载并解析远端 runtime 配置。
def download_remote_runtime_config(
    remote_context: RemoteHelperContext,
    str_remote_runtime_config: str,
) -> dict[str, Any]:
    """下载远端 workdir 中的 verilog.remote.json。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_runtime_config: 远端 runtime 配置相对路径。
    :return: 解析后的 runtime 配置字典。
    :raises FileNotFoundError: 远端配置缺失或下载失败时抛出。
    """

    # file-download 会根据 helper settings 把相对目标限制在 downloads_dir 下。
    str_local_download_name = "verilog.remote.download.json"  # runtime 配置下载缓存文件名

    # file-download 需要 helper settings 与 server list 两类本地配置。
    list_base = helper_base(remote_context)  # runtime 配置下载共用的 helper settings/server-list 参数

    # 尝试下载远端配置；缺失时保留自定义错误文本。
    completed_process_runtime_download = run_helper(  # verilog.remote.json 下载探测的 helper 结果
        remote_context.path_helper,  # 执行 file-download 的远端 helper 脚本
        [
            "file-download",  # 下载 verilog.remote.json 的 helper 子命令
            *list_base,  # runtime 配置下载阶段的 settings/server-list 参数
            "--server",  # 目标服务器参数名
            remote_context.str_server,  # 已确认远端服务器
            "--remote",  # 远端文件参数名
            str_remote_runtime_config,  # 远端 workdir 中的 verilog.remote.json 相对路径
            "--local",  # 本地下载目标参数名
            str_local_download_name,  # helper downloads_dir 下的下载缓存文件
        ],
        allow_failure=True,  # 由调用方转换缺失配置错误
        quiet_on_failure=True,  # 缺失远端配置时不转发 helper 噪声
    )

    # 成功下载时按 helper stdout 读取真实落盘位置。
    if completed_process_runtime_download.returncode == 0:

        # helper stdout 中包含受 settings 约束后的真实落盘路径。
        path_local_copy = parse_download_path(completed_process_runtime_download.stdout)  # 下载成功后的 runtime 配置副本

    # 失败时保留占位路径，后续统一转换为领域错误。
    else:

        # 占位路径只用于 exists 检查和错误分支，不参与读取。
        path_local_copy = Path(str_local_download_name)  # 下载失败时的本地副本占位路径

    # 下载失败或文件未生成都说明用户尚未持久化远端工具链选择。
    if completed_process_runtime_download.returncode != 0 or not path_local_copy.exists():

        # 错误文本指向远端 workdir 内应存在的配置路径。
        raise FileNotFoundError(
            f"> ERR: [Python] Remote validation requires {str_remote_runtime_config} "
            "in the selected remote workdir before external validation can continue."
        )

    # 解析并返回下载到本地的配置副本。
    return load_remote_runtime_config(path_local_copy)

# stage_package 复制 skill 主体和 tests/smoke harness 到临时上传包。
def stage_package(path_helper: Path, str_run_id: str) -> Path:
    """创建远端验证使用的本地 staging 包。

    :param path_helper: erie-remote-ssh helper 脚本路径，用于定位 reports/tmp。
    :param str_run_id: 本次远端 retained run id。
    :return: staging 包根目录路径。
    """

    # reports/tmp 位于 erie-remote-ssh 项目根下。
    path_remote_project = path_helper.resolve().parents[1]  # erie-remote-ssh 项目根目录

    # 每次 run 使用独立 staging 目录。
    path_package_root = (  # 当前 run 上传前使用的本地 staging 根
        path_remote_project / "reports" / "tmp" / f"erie-verilog-generator-{str_run_id}"  # run 专属临时上传包目录
    )

    # 复用 run id 时先删除旧 staging 目录。
    cleanup_package(path_package_root)

    # 上传目标目录保持与仓库根近似的结构。
    path_target = path_package_root / "erie-verilog-generator"  # 上传包工作区根

    # skill 源码复制到 skills/erie-verilog-generator 下。
    path_staged_skill = path_target / "skills" / "erie-verilog-generator"  # staging 中的 skill 目录

    # smoke harness 位于 staging 工作区根的 tests/smoke。
    path_staged_smoke = path_target / "tests" / "smoke"  # 远端回归所需的 tests/smoke 副本

    # 过滤运行产物、报告和缓存，避免远端包携带本地验证垃圾。
    obj_copytree_ignore_patterns = shutil.ignore_patterns(  # staging copytree 的产物排除规则
        "__pycache__",  # Python 缓存目录
        "*.pyc",  # Python 字节码文件
        "_smoke_runs",  # 本地 smoke 运行产物
        "reports",  # 本地治理报告目录
        "workflow-state.json",  # 本地 workflow 状态文件
    )

    # 复制 skill 主体目录。
    shutil.copytree(PATH_SKILL_ROOT, path_staged_skill, ignore=obj_copytree_ignore_patterns)

    # 复制仓库根 tests/smoke 目录。
    shutil.copytree(PATH_PROJECT_ROOT / "tests" / "smoke", path_staged_smoke, ignore=obj_copytree_ignore_patterns)

    # staging 根写 AGENTS marker，帮助 workspace-root discovery。
    (path_package_root / "AGENTS.md").write_text(
        "# Remote Validation Workspace\n\n"
        "This marker file is created only for remote confidence-gate staging so\n"
        "workspace-root discovery can resolve project-local state paths.\n",
        encoding="utf-8",
    )

    # 上传包工作区根也写 marker，覆盖远端执行 cwd 的根发现路径。
    (path_target / "AGENTS.md").write_text(
        "# Remote Validation Packaged Workspace\n\n"
        "This marker file is created only for remote confidence-gate staging so\n"
        "workspace-root discovery can resolve project-local state paths from the\n"
        "uploaded package root.\n",
        encoding="utf-8",
    )

    # 返回 staging 包根，finally 中由 cleanup_package 删除。
    return path_package_root

# cleanup_package 安全删除本地 staging 包。
def cleanup_package(path_package_root: Path) -> None:
    """删除 reports/tmp 下的远端验证 staging 包。

    :param path_package_root: 待删除的 staging 包根目录。
    :return: 不返回业务值；目录不存在时直接返回。
    :raises AssertionError: 路径不符合 staging 目录约束时拒绝删除。
    """

    # 不存在说明没有 staging 内容需要清理。
    if not path_package_root.exists():

        # 缺失目录是幂等清理路径。
        return

    # 删除前解析绝对路径，避免相对路径绕过 tmp 限制。
    path_resolved = path_package_root.resolve()  # 待删除 staging 目录绝对路径

    # 只允许删除 reports/tmp/erie-verilog-generator-run-* 形态目录。
    if path_resolved.parent.name != "tmp" or not path_resolved.name.startswith("erie-verilog-generator-run-"):

        # 路径异常时拒绝递归删除。
        raise AssertionError(f"> ERR: [Python] Refusing to remove unexpected package path: {path_package_root}")

    # 使用带重试的删除处理 Windows 文件锁。
    remove_tree_with_retries(path_package_root)

# remove_tree_with_retries 在 Windows 上处理短暂文件锁。
def remove_tree_with_retries(path_target: Path, *, attempts: int = 5, delay_s: float = 0.2) -> None:
    """带重试删除目录树。

    :param path_target: 待删除目录。
    :param attempts: 删除尝试次数。
    :param delay_s: 每次 PermissionError 后的基础等待秒数。
    :return: 不返回业务值；删除成功或目录缺失时结束。
    :raises PermissionError: 所有重试都被 Windows 文件锁阻断时重新抛出最后一次错误。
    """

    # 按次数重试删除目录。
    for int_attempt in range(attempts):

        # 删除目录树时捕获常见竞态。
        try:

            # 递归删除 staging 目录。
            shutil.rmtree(path_target)

            # 删除成功后直接返回。
            return

        # 目录已被其他清理路径删除时视为成功。
        except FileNotFoundError:

            # 幂等删除完成。
            return

        # Windows 短暂文件锁会抛 PermissionError。
        except PermissionError as exc:

            # 最后一次仍被锁定时把当前异常作为 cause 抛出。
            if int_attempt == attempts - 1:

                # 错误文本保持 current-project 前缀。
                raise PermissionError(
                    "> ERR: [Python] failed to remove staging package after retries."
                ) from exc

            # 等待时间随尝试次数线性增加。
            time.sleep(delay_s * (int_attempt + 1))

# request_and_run 创建 erie-remote-ssh request 并立即执行。
def request_and_run(
    remote_context: RemoteHelperContext,
    str_operation: str,
    list_operation_args: list[str],
    *,
    run_request_args: list[str] | None = None,
) -> Path:
    """创建并执行一个 erie-remote-ssh request。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_operation: request-* 子命令名。
    :param list_operation_args: 传给 request 子命令的业务参数。
    :param run_request_args: 追加给 run-request 的兼容参数。
    :return: 本地 request 文件路径。
    """

    # request 生命周期要求创建和执行阶段使用同一组本地连接配置。
    list_base = helper_base(remote_context)  # request 文件创建与执行共用的连接参数切片

    # 当前操作必须先落成本地 request 草稿，之后 run-request 才能执行。
    completed_process_create_request = run_helper(  # parse_request_path 需要解析的 request 创建 stdout
        remote_context.path_helper,  # 写出本地 request JSON 的 helper 脚本
        [
            str_operation,  # request-* 子命令名称
            *list_base,  # request 创建阶段的 settings/server-list 参数
            "--server",  # request 创建命令的目标服务器选项
            remote_context.str_server,  # 本次 request 绑定的远端服务器
            *list_operation_args,  # request 业务参数
        ],
    )

    # 从 helper 输出中提取 request 文件路径。
    path_request = parse_request_path(completed_process_create_request.stdout)  # 创建出的 request 文件路径

    # 兼容 upload request 的敏感上传确认参数。
    list_extra_run_args = run_request_args or []  # run-request 追加参数

    # 执行 request。
    run_helper(
        remote_context.path_helper,
        [
            "run-request",
            *list_base,
            "--request",
            str(path_request),
            "--execute",
            "--timeout",
            str(remote_context.int_timeout),
            *list_extra_run_args,
        ],
    )

    # 返回 request 路径，供 finally 清理。
    return path_request

# helper_base 生成 erie-remote-ssh 的配置参数。
def helper_base(remote_context: RemoteHelperContext) -> list[str]:
    """生成 erie-remote-ssh 子命令通用参数。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :return: 包含 settings 和 server list 的参数列表。
    """

    # 参数顺序保持旧脚本行为。
    return [
        "--settings",
        str(remote_context.path_remote_settings),
        "--config",
        str(remote_context.path_server_list),
    ]

# run_helper 调用 erie-remote-ssh helper 并转发短日志。
def run_helper(
    path_helper: Path,
    list_args: list[str],
    *,
    allow_failure: bool = False,
    quiet_on_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    """运行 erie-remote-ssh helper 子命令。

    :param path_helper: erie-remote-ssh helper 脚本路径。
    :param list_args: helper 子命令参数。
    :param allow_failure: 是否允许非零退出码返回给调用方处理。
    :param quiet_on_failure: 失败时是否抑制 stdout/stderr 转发。
    :return: subprocess.CompletedProcess 结果对象。
    :raises SystemExit: helper 失败且 allow_failure 为 False 时退出。
    """

    # helper 必须使用当前 Python 并启用 UTF-8。
    list_command = [sys.executable, "-X", "utf8", str(path_helper), *list_args]  # helper 子进程命令数组

    # 只打印子命令名，避免终端输出完整结构化 request 参数。
    str_helper_action = list_args[0] if list_args else "unknown"  # 当前 helper 子命令名称

    # 打印短状态，不把完整命令行当成报告正文。
    print(f"> INFO: [Python] remote helper action: {str_helper_action}")

    # 子进程继承环境后显式打开 Python UTF-8。
    dict_env = os.environ.copy()  # helper 子进程环境变量

    # PYTHONUTF8 缺省值不覆盖用户已有设置。
    dict_env.setdefault("PYTHONUTF8", "1")

    # 执行 helper，stdout/stderr 由本函数按策略转发。
    completed_process_helper = subprocess.run(  # erie-remote-ssh helper 子进程结果
        list_command,  # 当前 helper 子进程命令
        text=True,  # stdout/stderr 按文本读取
        encoding="utf-8",  # helper 输出按 UTF-8 解码
        errors="replace",  # 非法字节替换后继续转发日志
        capture_output=True,  # 由本函数统一加前缀转发
        check=False,  # 退出码由 allow_failure 策略处理
        env=dict_env,  # 带 PYTHONUTF8 默认值的子进程环境
    )

    # quiet_on_failure 允许调用方探测缺失远端文件而不污染日志。
    bool_failed_quietly = completed_process_helper.returncode != 0 and quiet_on_failure  # 是否静默失败

    # stdout 存在且不静默时逐行加前缀转发。
    if completed_process_helper.stdout and not bool_failed_quietly:

        # helper stdout 可能包含 JSON 或 request 路径；日志侧只做前缀转发。
        emit_prefixed_lines(
            completed_process_helper.stdout,
            stream=sys.stdout,
            str_prefix="> INFO: [Python] remote stdout:",
        )

    # 错误流单独走 stderr，避免破坏 report-runs 的 stdout JSON 尾协议。
    if completed_process_helper.stderr and not bool_failed_quietly:

        # 远端错误流必须避开 stdout 末尾的机器可读 JSON。
        emit_prefixed_lines(
            completed_process_helper.stderr,
            stream=sys.stderr,  # helper stderr 统一转发到当前 stderr
            str_prefix="> ERR: [Python] remote stderr:",  # 远端错误日志前缀
        )

    # 非零退出码默认直接结束当前脚本。
    if completed_process_helper.returncode != 0 and not allow_failure:

        # SystemExit 使用 helper 原退出码，保持旧 CLI 语义。
        raise SystemExit(completed_process_helper.returncode)

    # 返回结果供探测型调用方继续解析。
    return completed_process_helper

# emit_prefixed_lines 转发多行文本时保持 current-project 前缀。
def emit_prefixed_lines(text_output: str, *, stream: Any, str_prefix: str) -> None:
    """把子进程输出按行加前缀写到指定流。

    :param text_output: 子进程原始 stdout 或 stderr。
    :param stream: 目标输出流，通常是 sys.stdout 或 sys.stderr。
    :param str_prefix: 每一行前追加的 current-project 前缀。
    :return: 不返回业务值；输出完成即结束。
    """

    # rstrip 仅去掉尾部换行，保留中间空行。
    str_trimmed = text_output.rstrip()  # 去掉尾部空白后的输出文本

    # 空输出不需要写入任何日志。
    if not str_trimmed:

        # 直接返回避免产生空前缀行。
        return

    # 逐行转发，避免直接打印大块 JSON 或表格。
    for str_line in str_trimmed.splitlines():

        # 每行都带前缀，满足人工日志边界。
        stream.write(f"{str_prefix} {str_line}\n")

# emit_json_payload 输出机器可读 JSON 协议。
def emit_json_payload(dict_payload: dict[str, Any]) -> None:
    """把机器可读 JSON 载荷写到 stdout。

    :param dict_payload: 待输出的 JSON 对象。
    :return: 不返回业务值；stdout 末尾写入完整 JSON 对象。
    """

    # 机器协议由 validate_verilog_skill.py 的 parse_json_object 从 stdout 末尾解析。
    str_payload = json.dumps(dict_payload, indent=2, ensure_ascii=False)  # 机器可读 JSON 文本

    # 使用 stdout.write 保持 JSON 原样，避免 print 添加额外装饰。
    sys.stdout.write(str_payload + "\n")

# parse_request_path 从 helper 输出中提取 request 文件。
def parse_request_path(str_output: str) -> Path:
    """解析 erie-remote-ssh 创建的 request 路径。

    :param str_output: helper request-* 子命令 stdout。
    :return: request 文件路径。
    :raises AssertionError: helper 未输出 request 路径时抛出。
    """

    # helper 按 `request: path` 格式输出 request 文件。
    for str_line in str_output.splitlines():

        # 只接受 request 前缀行。
        if str_line.startswith("request:"):

            # 冒号后的文本是本地 request 文件路径。
            return Path(str_line.split(":", 1)[1].strip())

    # request-* 未产生 request: 行时说明 helper 输出协议损坏。
    raise AssertionError("> ERR: [Python] erie-remote-ssh did not print a request path.")

# cleanup_requests 清理本轮 helper 生成的 request 草稿。
def cleanup_requests(list_paths: list[Path]) -> None:
    """删除本轮创建的 erie-remote-ssh request 文件。

    :param list_paths: request 文件路径列表。
    :return: 不返回业务值；删除失败只输出 warning。
    """

    # 逐个清理 request 文件。
    for path_request in list_paths:

        # 单个 request 清理失败不能掩盖主流程结果。
        try:

            # 只删除仍然存在的本地 request 文件。
            if path_request.exists():

                # unlink 只移除本轮 helper 生成的临时 request 文件。
                path_request.unlink()

        # Windows 文件锁或权限问题只报告 warning。
        except OSError as exc:

            # request 文件残留只影响本地整洁度，不改变远端验证结果。
            print(f"> WARNING: [Python] request cleanup failed for {path_request}: {exc}", file=sys.stderr)

# cleanup_local_residuals 删除 skill 目录下的 Python 缓存目录。
def cleanup_local_residuals() -> None:
    """清理本地 skill 源码目录中的 __pycache__ 残留。

    :param: 此函数没有外部业务参数。
    :return: 不返回业务值；缓存目录会被尽力删除。
    """

    # 反向排序确保先删子目录再删父目录。
    for path_cache in sorted(PATH_SKILL_ROOT.rglob("__pycache__"), reverse=True):

        # 只删除目录，避免误删同名普通文件。
        if path_cache.is_dir():

            # pycache 是运行产物，不属于 skill 源码。
            shutil.rmtree(path_cache, ignore_errors=True)

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
        remote_context = RemoteHelperContext(  # 兼容旧六参入口时临时组装出的远端 helper 上下文
            path_helper=Path(args[0]),  # 旧入口传入的 helper 脚本路径
            path_remote_settings=Path(args[1]),  # 旧入口传入的远端配置路径
            path_server_list=Path(args[2]),  # 旧入口传入的 server-list 路径
            str_server=str(args[3]),  # 旧入口选择的目标服务器名
            int_timeout=120,  # 旧入口沿用的普通命令超时秒数
        )  # 由旧参数组装出的 helper 上下文

        # 旧入口的后两项对应远端 retained 根和最大 run 数。
        return _report_remote_runs_with_context(remote_context, str(args[4]), int(args[5]))

    # 参数不匹配时给出明确错误，避免下游 unpack 报错难定位。
    raise TypeError("> ERR: [Python] report_remote_runs 只接受 3 个 context 参数或 6 个 legacy 参数")

# _report_remote_runs_with_context 承载 report_remote_runs 的实际实现。
def _report_remote_runs_with_context(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    int_max_runs: int,
) -> dict[str, Any]:
    """读取远端 retained run 列表并下载摘要证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param int_max_runs: 最多返回的最近运行数量。
    :return: 包含 remote_root、runs 和 status 的报告字典。
    :raises ValueError: int_max_runs 小于 1 时抛出。
    """

    # 至少需要读取一个 run，否则 report-runs 没有意义。
    if int_max_runs < 1:

        # 参数错误按普通异常暴露给调用者。
        raise ValueError("> ERR: [Python] --max-runs must be at least 1.")

    # file-list 只需要基础连接配置和目标服务器。
    list_base = helper_base(remote_context)  # retained root 列表查询参数

    # 轮询模式先读取 run-* 名称，再按时间戳选择最近证据。
    completed_process_listing = run_helper(  # 轮询 retained 证据目录名的 file-list 子进程结果
        remote_context.path_helper,  # 执行只读目录枚举的 helper 脚本
        [
            "file-list",  # 只读取目录项而不拉取文件内容
            *list_base,  # 轮询 retained 根目录时的连接 flags
            "--server",  # 指定目录枚举发生在哪台服务器
            remote_context.str_server,  # 保存历史 run 目录的服务器名
            "--path",  # 指定 helper 要枚举的远端目录
            str_remote_root,  # 包含 run- 时间戳目录的父目录
        ],
        allow_failure=True,  # retained root 不存在时返回空报告
        quiet_on_failure=True,  # 缺少 retained root 时不污染 stderr
    )

    # 不可读取 retained 根时仍输出缺证据状态，而不是让上层解析失败。
    if completed_process_listing.returncode != 0:

        # status 区分远端缺失和正常空列表。
        return {"remote_root": str_remote_root, "runs": [], "status": "missing_or_unreadable"}

    # helper 输出中可能带前缀文本，解析其中 JSON 对象。
    dict_listing = parse_json_output(completed_process_listing.stdout)  # run-* 目录列表的 JSON 协议对象

    # entries 字段保存远端目录项。
    if isinstance(dict_listing, dict):

        # file-list 成功时 entries 是后续 run-* 过滤的数据源。
        list_entries = dict_listing.get("entries", [])  # retained root 下的原始 entries 字段

    # 非字典输出视为无可用目录项。
    else:

        # 空 entries 让 report-runs 返回 ok 但无 run 证据。
        list_entries = []  # 非字典 file-list 响应对应的空 run 目录项

    # 只保留 run-* 目录名，并按名称时间戳排序。
    list_run_names = sorted(  # 按 run-* 名称时间戳排序的 retained 目录名
        str(dict_item["name"])  # 单个 retained run 目录名
        for dict_item in list_entries  # 遍历 helper 返回的目录项
        if isinstance(dict_item, dict)  # 只处理字典目录项
        and dict_item.get("type") == "dir"  # 只保留远端目录
        and str(dict_item.get("name", "")).startswith("run-")  # 只保留 run-* retained 目录
    )

    # 取最近 N 个 run，并按新到旧输出。
    list_selected_runs = list(reversed(list_run_names[-int_max_runs:]))  # 按新到旧返回的最近 retained run 名称

    # 下载每个 run 的 execute 和 fixture 报告摘要。
    list_runs: list[dict[str, Any]] = []  # --report-runs stdout 中 runs 字段的条目列表

    # 逐个 retained run 下载摘要，避免压缩成难调试的列表推导。
    for str_run_name in list_selected_runs:

        # 每个 run 输出一个 remote_execute/fixtures 摘要对象。
        list_runs.append(summarize_remote_run(remote_context, str_remote_root, str_run_name))

    # 返回 validate/eval 可消费的稳定报告结构。
    return {"remote_root": str_remote_root, "runs": list_runs, "status": "ok"}

# summarize_remote_run 下载单个 retained run 的关键报告。
def summarize_remote_run(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    str_run_name: str,
) -> dict[str, Any]:
    """汇总单个 retained remote run 的执行和 fixture 证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param str_run_name: 单个 run-* 目录名。
    :return: 包含 remote_execute 和 fixtures 摘要的字典。
    """

    # 每个 run 内的 skill 目录固定为 erie-verilog-generator。
    str_remote_skill = remote_join(str_remote_root, str_run_name, "erie-verilog-generator")  # 当前 retained run 的上传 skill 根

    # 主流程门禁的原始 JSON 提供 ok、metrics 与 spec_outputs。
    str_execute_validation_json = remote_join(  # remote_execute 摘要读取的原始 validation 报告
        str_remote_skill,  # validation.json 所属 run 的 skill 根目录
        str(REMOTE_EXECUTE_VALIDATION_JSON),  # attempt-001 保存的执行门禁报告
    )

    # RTL 产物用于人工复核 mock workflow 是否生成预期适配器。
    str_execute_rtl_path = remote_join(  # 摘要中记录 erie_adapter.v 的 retained 地址
        str_remote_skill,  # 当前 run 上传包内的 skill 根
        str(REMOTE_EXECUTE_RTL_PATH),  # 适配器源码相对位置
    )

    # testbench 产物用于定位仿真激励和断言失败上下文。
    str_execute_testbench_path = remote_join(  # 失败复盘入口中的仿真平台文件名
        str_remote_skill,  # testbench 位于本次上传包的 skill 树内
        str(REMOTE_EXECUTE_TESTBENCH_PATH),  # 仿真平台相对位置
    )

    # 主门禁 JSON 缺失时摘要仍可标记 unavailable。
    dict_execute_report = download_json_optional(  # remote_execute 摘要的原始报告字典
        remote_context,  # 主门禁报告下载使用的 helper 上下文
        str_execute_validation_json,  # 主门禁 validation 源文件
        remote_join("erie-verilog-generator-report", str_run_name, "remote_execute_validation.json"),  # 主门禁 JSON 缓存文件
    )

    # 三个 fixture 的汇总缺失时保留空列表。
    dict_fixture_summary = download_json_optional(  # 三项小用例回归的聚合结果
        remote_context,  # fixture 汇总下载使用的 helper 上下文
        remote_join(str_remote_skill, str(REMOTE_FIXTURE_SUMMARY_JSON)),  # 三项 fixture 汇总源文件
        remote_join("erie-verilog-generator-report", str_run_name, "remote_fixture_summary.json"),  # fixture 汇总缓存文件
    )

    # 返回 retained run 的统一摘要。
    return {
        "run": str_run_name,
        "remote_skill": str_remote_skill,
        "remote_execute": summarize_validation_report(
            dict_execute_report,
            rtl_path=str_execute_rtl_path,
            testbench_path=str_execute_testbench_path,
            validation_json=str_execute_validation_json,
        ),
        "fixtures": summarize_fixture_report(dict_fixture_summary),
    }

# download_json_optional 下载远端 JSON 文件，失败时返回 None。
def download_json_optional(
    remote_context: RemoteHelperContext,
    str_remote_path: str,
    str_local_path: str,
) -> dict[str, Any] | None:
    """尝试下载远端 JSON 报告。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_path: 远端 JSON 文件相对路径。
    :param str_local_path: 本地下载目标路径。
    :return: 解析后的 JSON 字典；下载失败或文件缺失时返回 None。
    """

    # file-download 查询单个 JSON 证据时复用连接配置。
    list_base = helper_base(remote_context)  # JSON 报告下载 helper 参数

    # 下载远端 JSON 报告，失败时由调用方按证据缺失处理。
    completed_process_json_download = run_helper(  # 单个 retained JSON 证据下载结果
        remote_context.path_helper,  # 下载 JSON 证据的 helper 脚本
        [
            "file-download",  # JSON 证据下载子命令
            *list_base,  # JSON 下载阶段的 settings/server-list 参数
            "--server",  # JSON 下载目标服务器选项
            remote_context.str_server,  # JSON 证据所在远端服务器
            "--remote",  # 远端 JSON 文件选项
            str_remote_path,  # retained run 中的远端 JSON 路径
            "--local",  # 本地 JSON 下载目标选项
            str_local_path,  # reports/downloads 下的本地 JSON 路径
        ],
        allow_failure=True,  # 可选证据缺失时返回 None
        quiet_on_failure=True,  # 可选证据缺失时不转发 helper 噪声
    )

    # 下载失败表示该 run 缺少对应证据。
    if completed_process_json_download.returncode != 0:

        # 缺失可选 JSON 不阻断报告模式。
        return None

    # helper stdout 中包含真实下载到的本地路径。
    path_downloaded = parse_download_path(completed_process_json_download.stdout)  # helper 实际写出的 JSON 文件路径

    # helper 成功但文件不存在时仍按证据缺失处理。
    if not path_downloaded.exists():

        # 文件缺失返回 None，由摘要函数标记 unavailable。
        return None

    # 读取并解析下载到本地的 JSON 报告。
    return json.loads(path_downloaded.read_text(encoding="utf-8"))

# summarize_validation_report 压缩 validation.json 为稳定 remote_execute 摘要。
def summarize_validation_report(
    dict_report: dict[str, Any] | None,
    *,
    rtl_path: str | None = None,
    testbench_path: str | None = None,
    validation_json: str | None = None,
) -> dict[str, Any]:
    """汇总单个 validation.json 的关键字段。

    :param dict_report: validation.json 解析结果；缺失时为 None。
    :param rtl_path: 远端 RTL 产物路径。
    :param testbench_path: 远端 testbench 产物路径。
    :param validation_json: 远端 validation.json 路径。
    :return: 面向 eval-skill 的压缩摘要。
    """

    # 报告缺失时只标记不可用。
    if not dict_report:

        # available=false 让上层区分缺证据和失败证据。
        return {"available": False}

    # spec_outputs 只保留 path 字段，避免报告过大。
    list_outputs = sorted(  # validation.json 声明的 spec output 路径集合
        str(dict_output["path"])  # 单个规范输出路径
        for dict_output in dict_report.get("spec_outputs", [])  # 遍历原始 spec_outputs
        if isinstance(dict_output, dict) and dict_output.get("path")  # 仅保留含 path 的输出项
    )

    # metrics 可能缺失，先收束成字典。
    dict_metrics = (  # validation.json 中用于提取后端和工具列表的 metrics 字典
        dict_report.get("metrics", {})  # 原始 metrics 字段
        if isinstance(dict_report.get("metrics"), dict)  # metrics 必须是字典
        else {}  # 异常 metrics 结构按空字典处理
    )

    # 主摘要字段保持旧 eval 消费结构。
    dict_summary: dict[str, Any] = {  # validation 摘要载荷
        "available": True,  # validation.json 已成功读取
        "ok": dict_report.get("ok"),  # 原始验证结果
        "selected_simulator_backend": dict_metrics.get("selected_simulator_backend"),  # 实际仿真后端
        "executed_tools": dict_metrics.get("executed_tools", []),  # 实际执行工具列表
        "outputs": list_outputs,  # 规范输出路径列表
    }

    # 主 RTL 路径存在时写入摘要。
    if rtl_path:

        # 该键让评估报告指向主流程生成的 Verilog 适配器。
        dict_summary["rtl_path"] = rtl_path  # 适配器 Verilog 源码位置

    # 只有保留 testbench 位置时才补充仿真激励入口。
    if testbench_path:

        # 该键让失败分析能打开远端 testbench 激励。
        dict_summary["testbench_path"] = testbench_path  # 失败复跑所需的 testbench 文件

    # 原始 JSON 位置存在时才暴露 metrics 溯源入口。
    if validation_json:

        # 该键保留 metrics 与工具执行清单的原始 JSON 来源。
        dict_summary["validation_json"] = validation_json  # 原始验证报告文件位置

    # 返回压缩摘要。
    return dict_summary

# summarize_fixture_report 压缩 remote fixture summary。
def summarize_fixture_report(dict_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    """汇总远端 fixture 验证结果。

    :param dict_summary: remote_fixtures/summary.json 解析结果；缺失时为 None。
    :return: fixture 摘要列表。
    """

    # summary 缺失时返回空列表。
    if not dict_summary:

        # 空列表表示没有可用 fixture 证据。
        return []

    # fixtures 字段必须是列表才可继续解析。
    list_fixtures = dict_summary.get("fixtures", [])  # remote_fixtures summary 中的原始 fixture 列表

    # 非列表字段视为无可用 fixture。
    if not isinstance(list_fixtures, list):

        # 防御异常报告结构。
        return []

    # 逐项提取 eval 需要的稳定字段。
    return [
        {
            "name": dict_item.get("name"),
            "ok": dict_item.get("ok"),
            "selected_simulator_backend": dict_item.get("selected_simulator_backend"),
            "executed_tools": dict_item.get("executed_tools", []),
            "rtl_path": dict_item.get("rtl_path"),
            "testbench_path": dict_item.get("testbench_path"),
            "validation_json": dict_item.get("validation_json"),
        }
        for dict_item in list_fixtures
        if isinstance(dict_item, dict)
    ]

# parse_json_output 从带日志的 helper stdout 中提取 JSON 对象。
def parse_json_output(str_output: str) -> dict[str, Any]:
    """解析 helper 输出中的第一个 JSON 对象。

    :param str_output: helper stdout 文本。
    :return: JSON 对象字典。
    :raises ValueError: 输出中没有 JSON 对象时抛出。
    """

    # JSON 对象可能被 helper 前后缀日志包裹。
    int_start = str_output.find("{")  # JSON 对象起始位置

    # 从最后一个右花括号截断，兼容前缀日志。
    int_end = str_output.rfind("}")  # JSON 对象结束位置

    # 起止位置不合法说明 helper 协议异常。
    if int_start < 0 or int_end < int_start:

        # 报告没有找到 JSON 对象。
        raise ValueError("> ERR: [Python] No JSON object found in erie-remote-ssh output.")

    # 截取并解析 JSON 对象。
    return json.loads(str_output[int_start : int_end + 1])

# parse_download_path 从 helper stdout 中提取下载路径。
def parse_download_path(str_output: str) -> Path:
    """解析 erie-remote-ssh file-download 输出中的本地路径。

    :param str_output: file-download 子命令 stdout。
    :return: 已下载文件的本地路径。
    :raises AssertionError: helper 未输出 downloaded 路径时抛出。
    """

    # helper 按 `downloaded: path` 输出文件路径。
    for str_line in str_output.splitlines():

        # 只处理 downloaded 前缀行。
        if str_line.startswith("downloaded:"):

            # downloaded 冒号后的文本是 reports/downloads 中的真实落盘路径。
            return Path(str_line.split(":", 1)[1].strip())

    # 缺少 downloaded 行说明 helper 协议异常。
    raise AssertionError("> ERR: [Python] erie-remote-ssh did not print a downloaded path.")

# remote_validation_command 拼装远端 bash 验证脚本。
def remote_validation_command(
    str_remote_skill: str,
    str_remote_python: str,
    *,
    cleanup_outputs: bool = False,
    toolchain_selection: dict[str, Any] | None = None,
    remote_runtime_config_path: str | None = None,
) -> str:
    """生成远端执行的 bash 信心门禁脚本。

    :param str_remote_skill: 远端上传后的 skill 工作区路径。
    :param str_remote_python: 远端 Python 命令。
    :param cleanup_outputs: 是否清理远端 smoke 输出。
    :param toolchain_selection: 已确认的远端工具链选择。
    :param remote_runtime_config_path: 远端 runtime 配置相对路径。
    :return: 可交给 `bash -lc` 执行的脚本文本。
    """

    # Python 命令进入 shell 前必须单引号转义。
    str_py = sh_quote(str_remote_python)  # 主远端 bash 脚本使用的 Python 命令

    # cleanup 片段根据用户是否要求删除远端输出决定。
    str_cleanup_snippet = remote_output_cleanup_snippet(cleanup_outputs, str_remote_python)  # smoke 输出处置脚本片段

    # fixture 名称通过环境变量传给远端 Python 内联脚本。
    str_fixture_names = " ".join(REMOTE_FIXTURES)  # 空格分隔的 fixture 名称

    # 缺省情况下由远端工具探测选择仿真后端。
    str_selected_vivado = ""  # 未持久化选择时为空的 Vivado settings64.sh 路径

    # 缺省后端为空，表示按 xsim/vcs_verdi/iverilog 优先级自动选择。
    str_selected_backend = ""  # 未持久化选择时为空的仿真后端名称

    # 已持久化工具链选择时提取后端和 Vivado 路径。
    if toolchain_selection:

        # Vivado 路径只在 xsim 后端需要。
        str_selected_vivado = str(toolchain_selection.get("vivado_settings64") or "")  # 已确认的 Vivado 激活脚本路径

        # 后端名称用于覆盖 VERILOG_GENERATOR_SIMULATOR_PRIORITY。
        str_selected_backend = str(toolchain_selection.get("simulator_backend") or "")  # 已确认的仿真后端名称

    # simulator priority 片段负责导出配置后端。
    str_simulator_priority_snippet = simulator_priority_export_snippet(str_selected_backend)  # 导出 simulator priority 的 shell 片段

    # Vivado 激活片段必须在工具探测之前执行。
    str_vivado_snippet = vivado_activation_snippet(  # 工具探测前执行的 Vivado 激活片段
        str_selected_vivado,  # 用户确认的 settings64.sh 路径
        str_selected_backend,  # 用户确认的 simulator backend
        remote_runtime_config_path,  # 多 Vivado 候选提示中的配置位置
    )

    # RTL Markdown 约束远端回归独立生成片段。
    str_rtl_md_snippet = rtl_md_constraint_remote_snippet(str_remote_python)  # RTL Markdown 约束回归脚本

    # bytecode 清理当前保持 retained workspace，不删除远端缓存。
    str_bytecode_cleanup = remote_bytecode_cleanup_snippet(str_remote_python)  # 远端执行结束后的 pycache 保留/清理片段

    # 返回完整远端 bash 脚本，关键字符串由 smoke 测试断言。
    return f"""
set -eu
cd {sh_quote(str_remote_skill)}
export PYTHONPATH="skills/erie-verilog-generator${{PYTHONPATH:+:$PYTHONPATH}}"
{str_py} --version
{str_vivado_snippet}
{str_simulator_priority_snippet}
for tool in xvlog xelab xsim vcs verdi iverilog vvp yosys; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool=present"
  else
    echo "$tool=missing"
  fi
done
if command -v xvlog >/dev/null 2>&1 && command -v xelab >/dev/null 2>&1 && command -v xsim >/dev/null 2>&1; then
  expected_sim_backend=xsim
elif command -v vcs >/dev/null 2>&1 && command -v verdi >/dev/null 2>&1; then
  expected_sim_backend=vcs_verdi
elif command -v iverilog >/dev/null 2>&1 && command -v vvp >/dev/null 2>&1; then
  expected_sim_backend=iverilog
else
  echo "No supported simulator backend is available on the remote server." >&2
  exit 1
fi
if command -v yosys >/dev/null 2>&1; then
  yosys_available=1
else
  yosys_available=0
fi
{str_py} -m compileall -q \
  skills/erie-verilog-generator/scripts \
  tests
{str_py} -m tests.smoke.run_smoke --settings skills/erie-verilog-generator/config/defaults.json
{str_rtl_md_snippet}
if [ -n "$configured_simulator_backend" ]; then
  export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"
  expected_sim_backend="$configured_simulator_backend"
fi
{str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
  --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --out-dir _smoke_runs/remote_execute \
  --model-provider mock \
  --readiness execute \
  --external-target local
{str_py} -m {WORKFLOW_CLI_MODULE} validate \
  --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --path _smoke_runs/remote_execute/attempt-001/rtl/generated \
  --readiness execute \
  --external-target local
EXPECTED_SIM_BACKEND="$expected_sim_backend" {str_py} - <<'PY'
import json
import os
from pathlib import Path
expected = os.environ["EXPECTED_SIM_BACKEND"]
validation = json.loads(Path("_smoke_runs/remote_execute/attempt-001/validation.json").read_text(encoding="utf-8"))
metrics = validation["metrics"]
assert metrics["selected_simulator_backend"] == expected, metrics
assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]) if expected == "xsim" else True, metrics
if expected == "iverilog":
    assert "xsim" in metrics["missing_preferred_backends"], metrics
    assert "vcs_verdi" in metrics["missing_preferred_backends"], metrics
PY
mkdir -p _smoke_runs/remote_fixtures
REMOTE_FIXTURES="{str_fixture_names}" EXPECTED_SIM_BACKEND="$expected_sim_backend" {str_py} - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

WORKFLOW_CLI_MODULE = "scripts.python.workflow.cli"

fixtures = os.environ["REMOTE_FIXTURES"].split()
expected = os.environ["EXPECTED_SIM_BACKEND"]
summary = {{"fixtures": []}}
for name in fixtures:
    spec = Path("skills/erie-verilog-generator/assets/examples/remote_fixtures") / name / "spec.json"
    generated = Path("skills/erie-verilog-generator/assets/examples/remote_fixtures") / name / "generated"
    report_json = Path("_smoke_runs/remote_fixtures") / name / "validation.json"
    report_json.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        WORKFLOW_CLI_MODULE,
        "validate",
        "--spec",
        str(spec),
        "--path",
        str(generated),
        "--readiness",
        "execute",
        "--external-target",
        "local",
        "--report-json",
        str(report_json),
    ]
    subprocess.run(command, check=True)
    report = json.loads(report_json.read_text(encoding="utf-8"))
    metrics = report["metrics"]
    assert report["ok"] is True, report
    assert metrics["selected_simulator_backend"] == expected, metrics
    if expected == "xsim":
        assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]), metrics
    outputs = report.get("spec_outputs", [])
    summary["fixtures"].append({{
        "name": name,
        "ok": report["ok"],
        "selected_simulator_backend": metrics["selected_simulator_backend"],
        "executed_tools": metrics["executed_tools"],
        "rtl_path": str(generated / "rtl" / (name + ".v")),
        "testbench_path": str(generated / "tb" / (name + "_tb.v")),
        "validation_json": str(report_json),
        "outputs": outputs,
    }})
Path("_smoke_runs/remote_fixtures/summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
if [ "$yosys_available" -eq 1 ]; then
  {str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
    --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
    --out-dir _smoke_runs/remote_implement \
    --model-provider mock \
    --readiness implement \
    --external-target local
  {str_py} - <<'PY'
import json
from pathlib import Path
result = json.loads(Path("_smoke_runs/remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "passed", result
PY
else
  set +e
  {str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
    --spec skills/erie-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
    --out-dir _smoke_runs/remote_implement \
    --model-provider mock \
    --readiness implement \
    --external-target local
  impl_status=$?
  set -e
  if [ "$impl_status" -eq 0 ]; then
    echo "Expected implement readiness to block when yosys is missing." >&2
    exit 1
  fi
  {str_py} - <<'PY'
import json
from pathlib import Path
result = json.loads(Path("_smoke_runs/remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "blocked_toolchain", result
validation = json.loads(Path("_smoke_runs/remote_implement/attempt-001/validation.json").read_text(encoding="utf-8"))
assert any(
    item.get("tool") == "yosys" and item.get("source") == "toolchain_issue"
    for item in validation["issues"]
), validation
PY
fi
{str_cleanup_snippet}
{str_bytecode_cleanup}
""".strip()

# rtl_md_constraint_remote_snippet 生成 RTL Markdown 约束远端回归片段。
def rtl_md_constraint_remote_snippet(str_remote_python: str) -> str:
    """生成远端 RTL Markdown 约束回归脚本片段。

    :param str_remote_python: 远端 Python 命令。
    :return: 可嵌入主 bash 脚本的约束回归片段。
    """

    # Python 命令进入 shell 前必须转义。
    str_py = sh_quote(str_remote_python)  # RTL Markdown 片段使用的 Python 命令

    # 模板中的 __PY__ 占位符稍后替换为转义后的 Python 命令。
    str_template = r"""
mkdir -p _smoke_runs/remote_rtl_md_constraints
__PY__ - <<'PY'
from pathlib import Path

from scripts.python.workflow.prompt import render_prompt
from scripts.python.workflow.rtl_md_constraints import load_rtl_md_constraints, summarize_constraints_for_prompt
from scripts.python.quality.static_lint import lint_generated_rtl


def spec(name="remote_rtl_md_constraints"):
    return {
        "name": name,
        "description": "Remote RTL Markdown constraint regression fixture.",
        "behavior": ["Register one input bit."],
        "constraints": [],
        "notes": [],
        "clock": {"name": "clk", "edge": "posedge"},
        "reset": {"name": "rst_n", "active": "low", "synchronous": False},
        "interfaces": {
            "ports": [
                {"name": "clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "rst_n", "direction": "input", "width": 1, "role": "reset"},
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 1},
            ]
        },
        "outputs": [{"path": f"rtl/{name}.v", "kind": "source", "language": "verilog"}],
    }


catalog = load_rtl_md_constraints()
assert catalog["total_rules"] == 68, catalog
assert catalog["required_rules"] == 47, catalog
assert catalog["advisory_rules"] == 21, catalog
prompt = render_prompt(spec(), stage="rtl")
for marker in (
    "RTL Markdown constraints",
    "MUST_CASE_HAS_DEFAULT",
    "MUST_ASSIGN_WIDTH_MATCH",
    "REC_LITERAL_EXPLICIT_BASE_WIDTH",
):
    assert marker in prompt, marker
summary = summarize_constraints_for_prompt(max_rules_per_group=3)
assert "MUST rules are blocking error constraints" in summary, summary
assert "REC rules are default warning-level preferences" in summary, summary

bad_dir = Path("_smoke_runs/remote_rtl_md_constraints/bad")
bad_dir.mkdir(parents=True, exist_ok=True)
(bad_dir / "bad_constraints.v").write_text(
    "\n".join(
        [
            "module bad_constraints(input wire clk, input wire rst_n, input wire [3:0] a, output reg y);",
            "wire gated_clk = clk & rst_n;",
            "initial y = 1'b0;",
            "always @(a || rst_n) begin",
            "  if (a == 4'bx) begin",
            "    y <= 1'b1;",
            "  end",
            "  case (a)",
            "    4'b0001: y = 1'b1;",
            "  endcase",
            "end",
            "for (i = start; i < LIMIT; i = i + 1) begin",
            "  y = y;",
            "end",
            "endmodule",
            "",
        ]
    ),
    encoding="utf-8",
)
codes = {issue.code for issue in lint_generated_rtl(spec("bad_constraints"), bad_dir)}
for expected in (
    "WIRE_INIT",
    "SIM_ONLY",
    "SENS_OR_SEPARATOR",
    "XZ_LITERAL",
    "CASE_DEFAULT",
    "COMB_NONBLOCKING_ASSIGN",
    "FOR_CONST_BOUNDS",
):
    assert expected in codes, codes

good_dir = Path("_smoke_runs/remote_rtl_md_constraints/good")
good_dir.mkdir(parents=True, exist_ok=True)
(good_dir / "good_constraints.v").write_text(
    "\n".join(
        [
            "module good_constraints(input wire clk, input wire rst_n, input wire [3:0] a, output reg y);",
            "always @(posedge clk or negedge rst_n) begin",
            "  if (!rst_n) begin",
            "    y <= 1'b0;",
            "  end else begin",
            "    y <= a[0];",
            "  end",
            "end",
            "endmodule",
            "",
        ]
    ),
    encoding="utf-8",
)
assert lint_generated_rtl(spec("good_constraints"), good_dir) == []
PY
__PY__ -m scripts.python.workflow.cli eval-skill \
  --evals skills/erie-verilog-generator/evals/evals.json \
  --out _smoke_runs/remote_eval_skill.json \
  --no-state
__PY__ - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("_smoke_runs/remote_eval_skill.json").read_text(encoding="utf-8"))
summary = report["summary"]
assert summary["ok"] is True, summary
assert summary["case_count"] >= 30, summary
case = next((item for item in report["cases"] if item.get("id") == "rtl_md_constraints_gate"), None)
assert case and case.get("passed") is True, case
PY
""".strip()

    # 替换占位符后返回可嵌入主脚本的片段。
    return str_template.replace("__PY__", str_py)

# remote_output_cleanup_snippet 生成远端输出保留或清理片段。
def remote_output_cleanup_snippet(cleanup_outputs: bool, str_remote_python: str = "python3") -> str:
    """生成远端 smoke 输出处理脚本片段。

    :param cleanup_outputs: 是否删除远端 smoke 输出。
    :param str_remote_python: 远端 Python 命令。
    :return: 可嵌入主 bash 脚本的输出处理片段。
    """

    # 用户要求清理时使用 Python 安全删除，避免 shell rm -rf。
    if cleanup_outputs:

        # 清理片段中的 Python 命令同样需要 shell 单引号转义。
        str_py = sh_quote(str_remote_python)  # 安全清理片段使用的 Python 命令

        # 返回安全清理片段，测试断言其中不含 rm -rf。
        return f"""{str_py} - <<'PY'
import shutil
from pathlib import Path

for rel in ("_smoke_runs",):
    path = Path(rel)
    if path.exists():
        if not path.is_dir():
            raise SystemExit(f"Refusing to remove non-directory output path: {{path}}")
        shutil.rmtree(path)
state = Path("workflow-state.json")
if state.exists():
    if not state.is_file():
        raise SystemExit(f"Refusing to remove non-file workflow state path: {{state}}")
    state.unlink()
PY"""

    # 默认保留远端输出，方便用户复查 retained run。
    return "echo 'remote_outputs_retained=_smoke_runs workflow-state.json'"

# remote_bytecode_cleanup_snippet 保留远端 bytecode 清理策略说明。
def remote_bytecode_cleanup_snippet(str_remote_python: str) -> str:
    """生成远端 bytecode 清理策略片段。

    :param str_remote_python: 远端 Python 命令；当前仅用于保持函数签名兼容。
    :return: 表示 retained workspace 不删除 bytecode 的 echo 片段。
    """

    # retained 验证工作区需要完整证据，不主动删除远端缓存。
    return "echo 'remote_bytecode_cleanup_skipped=retained_validation_workspace'"

# simulator_priority_export_snippet 生成仿真后端优先级片段。
def simulator_priority_export_snippet(str_selected_backend: str) -> str:
    """生成远端仿真后端选择 shell 片段。

    :param str_selected_backend: 用户确认的仿真后端；空字符串表示自动选择。
    :return: 设置 configured_simulator_backend 的 shell 片段。
    """

    # 空后端表示按远端工具可用性自动选择。
    if not str_selected_backend:

        # 自动模式不导出 VERILOG_GENERATOR_SIMULATOR_PRIORITY。
        return "configured_simulator_backend=''\necho 'simulator_backend_selection=auto_priority'"

    # 已确认后端要通过环境变量覆盖 runtime 默认优先级。
    # 非空后端写入 shell 变量并记录选择来源。
    return (
        f"configured_simulator_backend={sh_quote(str_selected_backend)}\n"
        f"echo 'simulator_backend_selection={str_selected_backend}'"
    )

# vivado_activation_snippet 生成 xsim 所需 Vivado settings64.sh 激活片段。
def vivado_activation_snippet(
    str_selected_vivado: str = "",
    str_selected_backend: str = "",
    remote_runtime_config_path: str | None = None,
) -> str:
    """生成远端 Vivado settings64.sh 发现和激活脚本。

    :param str_selected_vivado: 用户确认的 settings64.sh 绝对路径。
    :param str_selected_backend: 用户确认的仿真后端。
    :param remote_runtime_config_path: 用于提示用户持久化选择的配置路径。
    :return: 可嵌入主 bash 脚本的 Vivado 激活片段。
    """

    # 提示路径用于多版本 Vivado 时告诉用户写入哪个远端配置文件。
    str_config_hint = str(  # 多版本 Vivado 阻断提示中的配置路径
        remote_runtime_config_path or remote_runtime_settings_relpath()  # 多版本 Vivado 时用户需更新的 runtime 配置路径
    )

    # 非 xsim 后端不需要 Vivado 激活。
    if str_selected_backend and str_selected_backend != "xsim":

        # 仍输出短状态，便于远端日志说明为何跳过 Vivado。
        return "echo 'vivado_settings=not_required_for_selected_backend'"

    # 返回保守发现逻辑：多候选时阻断并要求用户确认。
    return f"""
selected_vivado_settings={sh_quote(str_selected_vivado)}
toolchain_config_hint={sh_quote(str_config_hint)}
vivado_candidates_file="$(mktemp)"
for candidate in \
  "${{XILINX_VIVADO:-}}/settings64.sh" \
  "${{XILINX_VIVADO:-}}/../settings64.sh" \
  /tools/Xilinx/Vivado/*/settings64.sh \
  /tools/Xilinx/Vitis/*/settings64.sh \
  /opt/Xilinx/Vivado/*/settings64.sh; do
  if [ -f "$candidate" ]; then
    readlink -f "$candidate"
  fi
done | sort -u > "$vivado_candidates_file"
vivado_candidate_count="$(wc -l < "$vivado_candidates_file" | tr -d ' ')"
if [ -n "$selected_vivado_settings" ]; then
  if ! grep -Fx "$selected_vivado_settings" "$vivado_candidates_file" >/dev/null 2>&1; then
    echo "Configured Xilinx settings64.sh was not found on the remote server: $selected_vivado_settings" >&2
    echo "Available Xilinx toolchain choices:" >&2
    cat "$vivado_candidates_file" >&2
    exit 2
  fi
  echo "vivado_settings=$selected_vivado_settings"
  # shellcheck disable=SC1090
  . "$selected_vivado_settings"
elif [ "$vivado_candidate_count" -gt 1 ]; then
  echo "TOOLCHAIN_SELECTION_REQUIRED=1" >&2
  echo "Multiple Xilinx toolchain settings64.sh candidates were detected. Ask the user to choose one" >&2
  echo "and persist it in: $toolchain_config_hint" >&2
  echo "Available Xilinx toolchain choices:" >&2
  cat "$vivado_candidates_file" >&2
  exit 2
elif ! command -v xvlog >/dev/null 2>&1 || ! command -v xelab >/dev/null 2>&1 || ! command -v xsim >/dev/null 2>&1; then
  if [ "$vivado_candidate_count" -eq 1 ]; then
    auto_vivado_settings="$(cat "$vivado_candidates_file")"
    echo "vivado_settings=$auto_vivado_settings"
    # shellcheck disable=SC1090
    . "$auto_vivado_settings"
  fi
fi
""".strip()

# require_remote_relative_path 校验远端 workdir 内相对路径。
def require_remote_relative_path(str_value: str, str_label: str) -> str:
    """校验远端相对 POSIX 路径。

    :param str_value: 待校验路径文本。
    :param str_label: 错误消息中的配置字段名。
    :return: 归一化后的 POSIX 相对路径。
    :raises ValueError: 路径为空、绝对、含反斜杠或含父目录时抛出。
    """

    # 去除首尾空白后检查远端相对路径空值。
    str_raw = str_value.strip()  # 待校验的远端相对路径文本

    # 空路径会导致远端 helper 目标不明确。
    if not str_raw:

        # 空相对路径无法映射到远端 workdir 内的稳定目标。
        raise ValueError("> ERR: [Python] remote relative path must not be empty.")

    # 远端路径必须使用 POSIX 分隔符。
    if "\\" in str_raw:

        # 反斜杠可能表示本地 Windows 路径，远端 helper 不能安全解释。
        raise ValueError("> ERR: [Python] remote relative path must use POSIX separators.")

    # PurePosixPath 负责归一化 POSIX 片段。
    path_remote = PurePosixPath(str_raw)  # POSIX 路径对象

    # retained run 和配置路径必须相对远端 workdir。
    if path_remote.is_absolute() or str_raw.startswith("~"):

        # retained run 只能落在配置的远端 workdir 内部。
        raise ValueError(
            "> ERR: [Python] remote path must be relative to the configured remote workdir."
        )

    # 路径片段决定 retained run 是否会逃出配置的远端工作区。
    tuple_parts = path_remote.parts  # 防止相对路径含当前目录或父目录穿越的片段序列

    # 空片段、当前目录和父目录都不允许。
    if not tuple_parts or any(str_part in {"", ".", ".."} for str_part in tuple_parts):

        # 父目录穿越会突破远端 workdir 约束。
        raise ValueError(
            "> ERR: [Python] remote relative path must be normalized without parent traversal."
        )

    # 返回 POSIX 字符串供 shell 和 helper 使用。
    return path_remote.as_posix()

# require_remote_absolute_file_path 校验远端绝对文件路径。
def require_remote_absolute_file_path(str_value: str, str_label: str) -> str:
    """校验远端绝对 POSIX 文件路径。

    :param str_value: 待校验路径文本。
    :param str_label: 错误消息中的参数名。
    :return: 归一化后的绝对 POSIX 路径。
    :raises ValueError: 路径为空、非绝对、含反斜杠或含父目录时抛出。
    """

    # 去除首尾空白后检查远端绝对路径空值。
    str_raw = str_value.strip()  # 待校验的远端绝对文件路径文本

    # settings64.sh 路径不能为空。
    if not str_raw:

        # 空 settings64.sh 无法用于远端工具链激活。
        raise ValueError("> ERR: [Python] remote absolute file path must not be empty.")

    # 绝对 settings64.sh 值不能混入 Windows 分隔符。
    if "\\" in str_raw:

        # settings64.sh 参数同样禁止 Windows 反斜杠。
        raise ValueError("> ERR: [Python] remote absolute file path must use POSIX separators.")

    # 绝对文件路径使用 PurePosixPath 拆解 root 与剩余片段。
    path_remote = PurePosixPath(str_raw)  # 绝对路径对象用于检查 root 和后续片段

    # settings64.sh 必须是远端绝对路径，不能依赖 shell 展开。
    if not path_remote.is_absolute() or str_raw.startswith("~"):

        # settings64.sh 需要绝对路径，避免远端 cwd 或 shell 展开影响结果。
        raise ValueError(
            "> ERR: [Python] remote file path must be absolute on the remote server."
        )

    # 根目录后的片段不能包含当前目录或父目录。
    if any(str_part in {"", ".", ".."} for str_part in path_remote.parts[1:]):

        # settings64.sh 路径不得通过父目录片段绕过用户确认值。
        raise ValueError(
            "> ERR: [Python] remote absolute file path must be normalized without parent traversal."
        )

    # 返回 POSIX 字符串供配置写入和 shell 比较。
    return path_remote.as_posix()

# remote_join 拼接远端 workdir 内相对路径。
def remote_join(*tuple_parts: str) -> str:
    """拼接多个远端相对 POSIX 路径片段。

    :param tuple_parts: 一个或多个远端相对路径片段。
    :return: 归一化后的 POSIX 相对路径。
    """

    # 收集校验后的路径片段。
    list_normalized: list[str] = []  # 拼接前的 POSIX 路径片段

    # 逐个校验片段，禁止任何片段含父目录或绝对路径。
    for int_index, str_part in enumerate(tuple_parts):

        # 每个片段都按远端相对路径校验。
        str_part_label = f"remote path part {int_index}"  # 错误消息中的片段序号

        # 单个片段校验后再拆成 PurePosixPath parts。
        str_value = require_remote_relative_path(str_part, str_part_label)  # 已归一化的远端路径片段

        # PurePosixPath.parts 保留多级片段。
        list_normalized.extend(PurePosixPath(str_value).parts)

    # 拼回 POSIX 相对路径。
    return PurePosixPath(*list_normalized).as_posix()

# sh_quote 对 shell 单引号参数做安全转义。
def sh_quote(str_value: str) -> str:
    """用单引号转义 shell 参数。

    :param str_value: 待嵌入 bash 脚本的原始文本。
    :return: 可放入 shell 命令行的单引号字符串。
    """

    # 单引号内部的单引号按 POSIX shell 习惯拆开转义。
    return "'" + str_value.replace("'", "'\"'\"'") + "'"

# 脚本入口只在直接执行时触发 main。
if __name__ == "__main__":

    # 将 main 的退出码交给解释器。
    sys.exit(main())
