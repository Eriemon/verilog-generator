"""远端验证的 staging、helper 调用和 retained-run 摘要实现。"""

# 标准库负责 JSON 协议、远端请求子进程、临时包 staging 和清理。
import json
import os
import shutil
import subprocess
import sys
import time

# pathlib 负责本地路径。
from pathlib import Path

# Any 只用于 helper 上下文、JSON 载荷和输出流。
from typing import Any, Callable

# skill 主体根目录供 staging 和本地缓存清理复用。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 包含 runtime、scripts 与 config 的 skill 根目录

# 仓库根目录用于复制 smoke harness。
PATH_PROJECT_ROOT = PATH_SKILL_ROOT.parents[1]  # 当前 skill 仓库根目录

# ensure_local_prerequisites 校验本地 helper、settings 和 server list。
def ensure_local_prerequisites(remote_context: Any) -> None:
    """校验 erie-remote-ssh 本地调用文件是否齐备。

    :param remote_context: 提供 helper、settings 和 server-list 路径属性的上下文对象。
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
def ensure_remote_prerequisites(remote_context: Any) -> None:
    """运行远端连接、软件扫描和 workspace 检查。

    :param remote_context: 提供 helper 参数和 server 字段的上下文对象。
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
def ensure_remote_read_prerequisites(remote_context: Any) -> None:
    """运行报告模式需要的远端读取前置检查。

    :param remote_context: 提供 helper 参数和 server 字段的上下文对象。
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
    remote_context: Any,
    dict_payload: dict[str, Any],
    str_remote_runtime_config: str,
    *,
    func_write_remote_runtime_config: Callable[[Path, dict[str, Any]], None],
) -> None:
    """上传 verilog.remote.json 到远端工作目录。

    :param remote_context: 提供 helper 参数的上下文对象。
    :param dict_payload: 待上传的 runtime 配置载荷。
    :param str_remote_runtime_config: 远端 workdir 内的配置相对路径。
    :param func_write_remote_runtime_config: 写本地 JSON 副本的回调。
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
    func_write_remote_runtime_config(path_local_copy, dict_payload)

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
                    str(Path(str_remote_runtime_config).parent).replace("\\", "/"),
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
    remote_context: Any,
    str_remote_runtime_config: str,
    *,
    func_load_remote_runtime_config: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """下载远端 workdir 中的 verilog.remote.json。

    :param remote_context: 提供 helper 参数的上下文对象。
    :param str_remote_runtime_config: 远端 runtime 配置相对路径。
    :param func_load_remote_runtime_config: 解析本地 JSON 副本的回调。
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
    return func_load_remote_runtime_config(path_local_copy)

# resolve_helper_skill_root 按 skill 标记定位 helper 所属根目录。
def resolve_helper_skill_root(path_helper: Path) -> Path:
    """定位 erie-remote-ssh helper 所属的 skill 根目录。

    :param path_helper: erie-remote-ssh helper Python 入口路径。
    :return: 包含 SKILL.md 的 helper skill 根；旧布局无标记时返回兼容根。
    """

    # helper 入口的嵌套层级会随依赖版本变化，SKILL.md 才是稳定边界。
    path_helper_resolved = path_helper.resolve()  # 向上查找稳定 skill 标记的绝对 helper 路径

    # 从 helper 所在目录逐级向上寻找最近的 skill 根标记。
    for path_candidate in path_helper_resolved.parents:

        # 最近的 SKILL.md 所在目录定义 helper 依赖的真实 skill 根。
        if (path_candidate / "SKILL.md").is_file():

            # 返回标记命中的根，避免依赖 helper 入口的嵌套深度。
            return path_candidate

    # 兼容没有 skill marker 的旧测试夹具和历史单层 helper 布局。
    return path_helper_resolved.parents[1]

# stage_package 复制 skill 主体、完整测试和治理事实源到临时上传包。
def stage_package(path_helper: Path, str_run_id: str) -> Path:
    """创建远端验证使用的本地 staging 包。

    :param path_helper: erie-remote-ssh helper 脚本路径，用于定位 reports/tmp。
    :param str_run_id: 本次远端 retained run id。
    :return: staging 包根目录路径。
    :raises FileNotFoundError: 本地安装态治理 skill 或全局 AGENTS 基线缺失时抛出。
    """

    # helper skill 根决定受上传策略允许的本地 staging 边界。
    path_remote_skill_root = resolve_helper_skill_root(path_helper)  # staging 所属的 helper skill 根目录

    # 每次 run 使用独立 staging 目录。
    path_package_root = (  # 当前 run 上传前使用的本地 staging 根
        path_remote_skill_root / "reports" / "tmp" / f"readable-verilog-generator-{str_run_id}"  # run 专属临时上传包目录
    )

    # 复用 run id 时先删除旧 staging 目录。
    cleanup_package(path_package_root)

    # 上传目标目录保持与仓库根近似的结构。
    path_target = path_package_root / "readable-verilog-generator"  # 上传包工作区根

    # skill 源码复制到 skills/readable-verilog-generator 下。
    path_staged_skill = path_target / "skills" / "readable-verilog-generator"  # staging 中的 skill 目录

    # 完整测试树位于 staging 工作区根，供远程 pytest 权威回归。
    path_staged_tests = path_target / "tests"  # 远端 pytest 与 smoke 共用的完整测试目录

    # 项目治理配置随验证包上传，供测试读取真实控制合同。
    path_staged_agents = path_target / ".agents"  # 远端治理回归所需的项目控制目录

    # 当前文档随验证包上传，供文档、handoff 和发布合同测试读取。
    path_staged_docs = path_target / "docs"  # 远端治理回归所需的项目文档目录

    # 远端 pytest 在已受管 reports 根下使用隔离 HOME，兼顾上传边界和目录治理。
    path_validation_codex = path_target / "reports" / ".validation-home" / ".codex"  # 可上传的隔离 Codex 根目录

    # 本地已安装的治理 skill 是远端治理测试所需的显式验证依赖。
    path_agents_generator_source = (
        Path.home() / ".codex" / "skills" / "agents-md-generator"  # 当前安装态的治理 skill 根目录
    )

    # 全局 AGENTS 基线与治理 skill 一同进入隔离 HOME。
    path_global_agents_source = Path.home() / ".codex" / "AGENTS.md"  # 当前 Codex 的全局受管规则

    # 缺失治理依赖时必须停止，不能退回远端用户环境形成伪绿结果。
    if not (path_agents_generator_source / "SKILL.md").is_file():

        # 错误包含缺失根目录，便于恢复本地安装后重试。
        raise FileNotFoundError(
            f"> ERR: [Python] Missing installed agents-md-generator: {path_agents_generator_source}"
        )

    # 全局基线缺失时，远端测试无法验证完整规则层级。
    if not path_global_agents_source.is_file():

        # 显式报告缺失文件而不是生成临时替代规则。
        raise FileNotFoundError(f"> ERR: [Python] Missing global AGENTS.md: {path_global_agents_source}")

    # 过滤运行产物、报告和缓存，避免远端包携带本地验证垃圾。
    obj_copytree_ignore_patterns = shutil.ignore_patterns(  # staging copytree 的产物排除规则
        "__pycache__",  # Python 缓存目录
        ".pytest_cache",  # 禁止跨候选复用 pytest 收集状态
        "*.pyc",  # Python 字节码文件
        "_smoke_runs",  # 本地 smoke 运行产物
        "reports",  # 本地治理报告目录
        "workflow-state.json",  # 本地 workflow 状态文件
        "active-session.json",  # 当前本地会话状态不属于候选源快照
    )

    # 复制 skill 主体目录。
    shutil.copytree(PATH_SKILL_ROOT, path_staged_skill, ignore=obj_copytree_ignore_patterns)

    # 复制完整测试树，让远程 pytest 与本地候选覆盖相同测试集合。
    shutil.copytree(PATH_PROJECT_ROOT / "tests", path_staged_tests, ignore=obj_copytree_ignore_patterns)

    # 复制项目控制配置，不携带当前机器的临时 active-session 状态。
    shutil.copytree(PATH_PROJECT_ROOT / ".agents", path_staged_agents, ignore=obj_copytree_ignore_patterns)

    # 复制当前治理文档，保证远程文档合同测试读取候选事实源。
    shutil.copytree(PATH_PROJECT_ROOT / "docs", path_staged_docs, ignore=obj_copytree_ignore_patterns)

    # 复制安装态治理 skill，让隔离 HOME 中的 pytest 使用已知依赖版本。
    shutil.copytree(
        path_agents_generator_source,
        path_validation_codex / "skills" / "agents-md-generator",
        ignore=obj_copytree_ignore_patterns,
    )

    # 写入当前全局受管基线，保持远端 AGENTS 层级与本地候选一致。
    path_validation_codex.mkdir(parents=True, exist_ok=True)

    # 复制规则文件前已创建隔离 Codex 根目录。
    shutil.copy2(path_global_agents_source, path_validation_codex / "AGENTS.md")

    # staging 根写 AGENTS marker，帮助 workspace-root discovery。
    (path_package_root / "AGENTS.md").write_text(
        "# Remote Validation Workspace\n\n"
        "This marker file is created only for remote confidence-gate staging so\n"
        "workspace-root discovery can resolve project-local state paths.\n",
        encoding="utf-8",
    )

    # 上传包工作区根使用真实受管 AGENTS，兼顾根发现和治理版本验证。
    shutil.copy2(PATH_PROJECT_ROOT / "AGENTS.md", path_target / "AGENTS.md")

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

    # 只允许删除 reports/tmp/readable-verilog-generator-run-* 形态目录。
    if path_resolved.parent.name != "tmp" or not path_resolved.name.startswith("readable-verilog-generator-run-"):

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
    remote_context: Any,
    str_operation: str,
    list_operation_args: list[str],
    *,
    run_request_args: list[str] | None = None,
) -> Path:
    """创建并执行一个 erie-remote-ssh request。

    :param remote_context: 提供 helper 参数和超时字段的上下文对象。
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
def helper_base(remote_context: Any) -> list[str]:
    """生成 erie-remote-ssh 子命令通用参数。

    :param remote_context: 提供 settings 和 server-list 路径字段的上下文对象。
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

# report_remote_runs_with_context_impl 承载 report_remote_runs 的真实实现。
def report_remote_runs_with_context_impl(
    remote_context: Any,
    str_remote_root: str,
    int_max_runs: int,
    *,
    dict_dependencies: dict[str, Any],
) -> dict[str, Any]:
    """读取远端 retained run 列表并汇总摘要证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param int_max_runs: 最多返回的运行条目数量。
    :param dict_dependencies: 汇总 retained-run 列表所需的回调集合。
    :return: 包含 remote_root、runs 和 status 的稳定字典。
    :raises ValueError: int_max_runs 小于 1 时抛出。
    """

    # 至少要读取一条 run，--report-runs 在 0 条上没有意义。
    if int_max_runs < 1:

        # 直接抛出带前缀错误，提示调用方修正 CLI 参数。
        raise ValueError("> ERR: [Python] --max-runs must be at least 1.")

    # file-list 只依赖 helper 基础参数和目标服务器。
    list_base = dict_dependencies["helper_base"](remote_context)  # retained root 列表查询参数

    # 查询模式会先读取 run-* 目录，再按时间倒序筛出最新证据。
    completed_process_listing = dict_dependencies["run_helper"](  # file-list 目录枚举的 helper 执行结果
        remote_context.path_helper,  # 远端 helper 可执行入口
        [  # file-list 子命令参数序列
            "file-list",  # retained root 目录枚举子命令
            *list_base,  # helper 基础连接参数
            "--server",  # 指定目录枚举目标服务器
            remote_context.str_server,  # retained 根目录所在服务器名
            "--path",  # 指定待查询 retained 根目录
            str_remote_root,  # retained run 根目录地址
        ],
        allow_failure=True,  # 允许目录缺失时返回非零并由上层转义
        quiet_on_failure=True,  # 缺失 retained 根目录时压低 helper 噪声
    )

    # 远端 retained 根目录不可读时，返回缺失状态而不是直接抛错。
    if completed_process_listing.returncode != 0:

        # status 明确标记远端缺失或不可读，方便上层汇总处理。
        return {"remote_root": str_remote_root, "runs": [], "status": "missing_or_unreadable"}

    # helper stdout 可能带前缀日志，这里统一回放 JSON 协议解析。
    dict_listing = dict_dependencies["parse_json_output"](completed_process_listing.stdout)  # run-* 目录列表的 JSON 载荷

    # entries 字段承载远端目录项，没有字典时按空列表处理。
    if isinstance(dict_listing, dict):

        # 字典型目录列表直接暴露 entries 字段，供后续 run-* 过滤逻辑复用。
        list_entries = dict_listing.get("entries", [])  # retained root 下的原始目录项列表

    # 非字典结果说明 helper 输出不满足目录列表协议，下面退化为空目录列表。
    else:

        # 退化为空目录列表后，report-runs 仍可稳定返回 ok 结构和空 runs。
        list_entries = []  # 非字典 file-list 结果按无可用 retained run 处理

    # 把 helper 返回的目录项压缩成可截断的 retained run 名称序列。
    list_run_names = sorted(  # 升序整理后的 retained run 名称列表
        str(dict_item["name"])  # 目录项名称统一转成字符串
        for dict_item in list_entries  # 逐个检查 retained 根目录返回的目录项
        if isinstance(dict_item, dict)  # 只接收字典型目录项
        and dict_item.get("type") == "dir"  # 排除文件和其他非目录项
        and str(dict_item.get("name", "")).startswith("run-")  # 仅保留 run-* 目录
    )

    # 取最近 N 条 run，再转换成从新到旧的呈现顺序。
    list_selected_runs = list(reversed(list_run_names[-int_max_runs:]))  # 最新优先的 retained run 名称列表

    # 逐个汇总 retained run 证据，构造 --report-runs 的 runs 字段。
    list_runs: list[dict[str, Any]] = []  # --report-runs stdout 中的 retained run 摘要列表

    # 为每个 run 生成 remote_execute 与 fixtures 的稳定摘要。
    for str_run_name in list_selected_runs:

        # 单个 run 摘要继续复用 facade 注入的 retained-run 汇总回调。
        list_runs.append(dict_dependencies["summarize_remote_run"](remote_context, str_remote_root, str_run_name))

    # 返回 validate_verilog_skill.py 依赖的稳定摘要结构。
    return {"remote_root": str_remote_root, "runs": list_runs, "status": "ok"}

# summarize_remote_run_impl 下载单个 retained run 的关键报告。
def _download_retained_summary(
    remote_context: Any, str_remote_skill: str, str_run_name: str,
    *,
    dict_dependencies: dict[str, Any], dict_remote_paths: dict[str, str],
    str_remote_path_key: str, str_local_filename: str,
) -> dict[str, Any]:
    """下载 retained run 中的一份结构化汇总证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_skill: 当前 retained run 的远端 skill 根目录。
    :param str_run_name: 当前 run-* 目录名。
    :param dict_dependencies: retained-run 摘要依赖的回调集合。
    :param dict_remote_paths: retained-run 摘要依赖的远端路径集合。
    :param str_remote_path_key: dict_remote_paths 中的证据路径键。
    :param str_local_filename: 下载到本地报告目录时使用的文件名。
    :return: 下载并解析后的 JSON 字典；缺失时沿用 optional 下载器语义。
    """

    # 统一展开远端证据位置，避免不同摘要类型产生路径规则漂移。
    str_remote_report = dict_dependencies["remote_join"](  # 当前 retained run 中待下载的结构化证据位置
        str_remote_skill,  # 当前 retained run 的远端 skill 根目录
        dict_remote_paths[str_remote_path_key],  # 当前证据类型在 skill 内的相对路径
    )

    # 统一把 retained 证据归档到按 run 隔离的本地报告目录。
    str_local_report = dict_dependencies["remote_join"](  # 按 run 隔离的本地结构化证据归档位置
        "readable-verilog-generator-report",  # retained JSON 的统一本地报告根目录
        str_run_name,  # 当前 retained run 对应的本地归档分区
        str_local_filename,  # 当前证据类型在本地归档时使用的稳定文件名
    )

    # 通过 optional 下载器保留缺失证据的失败关闭语义。
    return dict_dependencies["download_json_optional"](
        remote_context,  # 当前 run 的远程 helper 调用上下文
        str_remote_report,
        str_local_report,
    )

# 汇总单个 retained run 的执行、pytest 和 fixture 证据。
def summarize_remote_run_impl(
    remote_context: Any,
    str_remote_root: str,
    str_run_name: str,
    *,
    dict_dependencies: dict[str, Any],
    dict_remote_paths: dict[str, str],
) -> dict[str, Any]:
    """汇总单个 retained remote run 的执行和 fixture 证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param str_run_name: 当前 run-* 目录名。
    :param dict_dependencies: retained-run 摘要依赖的回调集合。
    :param dict_remote_paths: retained-run 摘要依赖的远端路径集合。
    :return: 包含 remote_execute 和 fixtures 摘要的字典。
    """

    # 先确定上传 skill 在当前 retained run 里的远端基目录，后续所有证据路径都从这里展开。
    str_remote_skill = dict_dependencies["remote_join"](  # 当前 retained run 的上传 skill 根目录，用来展开执行校验 JSON、可读 RTL、激励文件和夹具汇总的远端位置
        str_remote_root,  # settings.remote.remote_root 对应的 retained 根目录
        str_run_name,  # 当前这次 retained 记录的 run-* 子目录名
        "readable-verilog-generator",  # 远端 staging 后保留下来的 skill 目录名
    )

    # 先拼出 validation.json 的 retained 地址，后面据此下载执行证据。
    str_execute_validation_json = dict_dependencies["remote_join"](  # 下载 execution 校验 JSON 的来源地址
        str_remote_skill,  # 作为 validation.json 拼接起点的上传 skill 基目录
        dict_remote_paths["execute_validation_json"],  # validation.json 的相对证据路径
    )

    # 再给可读 RTL 产物保留 retained 定位，方便人工追查输出内容。
    str_execute_rtl_path = dict_dependencies["remote_join"](  # 人工回看 RTL 结果时使用的 retained 定位
        str_remote_skill,  # 把 readable RTL 的相对路径挂到这次上传目录下面
        dict_remote_paths["execute_rtl_path"],  # readable RTL 产物的相对路径
    )

    # 最后把 testbench 的 retained 定位单独留下，失败时可以直接回看激励。
    str_execute_testbench_path = dict_dependencies["remote_join"](  # 失败复盘激励文件时使用的 retained 定位
        str_remote_skill,  # 沿着这次上传目录定位失败回放所需的激励文件
        dict_remote_paths["execute_testbench_path"],  # remote_execute testbench 的相对路径
    )

    # 先下载 execution JSON 原始证据，供摘要函数抽取状态和工件信息。
    dict_execute_report = dict_dependencies["download_json_optional"](  # remote_execute 原始 JSON 证据
        remote_context,  # 当前 helper 调用上下文
        str_execute_validation_json,  # validation.json 的远端下载来源
        dict_dependencies["remote_join"](  # 组装 execution JSON 的本地落盘相对路径
            "readable-verilog-generator-report",  # execution 证据写入的本地报告根目录
            str_run_name,  # execution 证据归档到当前 run 的子目录
            "remote_execute_validation.json",  # execution JSON 在本地保存时使用的文件名
        ),
    )

    # fixture 汇总与 execution 证据分开下载，保留最小案例回归的独立事实边界。
    dict_fixture_summary = _download_retained_summary(  # remote_fixtures 汇总 JSON 证据
        remote_context,  # 下载 fixture 回归汇总时使用的远程 helper 上下文
        str_remote_skill,  # fixture 证据所在的 retained skill 根目录
        str_run_name,  # fixture 下载结果对应的 retained run 名称
        dict_dependencies=dict_dependencies,  # fixture 下载复用的路径与 JSON 回调
        dict_remote_paths=dict_remote_paths,  # fixture 汇总在远端 skill 内的路径配置
        str_remote_path_key="fixture_summary_json",  # fixture 汇总路径对应的配置键
        str_local_filename="remote_fixture_summary.json",  # fixture 本地归档文件名
    )

    # pytest 摘要独立下载，保证 retained run 能复核权威回归的精确计数和耗时。
    dict_pytest_summary = _download_retained_summary(  # 远程 pytest 结构化 JSON 证据
        remote_context,  # 下载 pytest 摘要时使用的远程 helper 上下文
        str_remote_skill,  # 作为权威回归摘要定位起点的远端 skill 目录
        str_run_name,  # 把权威回归计数隔离到本次 run 的本地证据分区
        dict_dependencies=dict_dependencies,  # 注入 pytest 证据下载与路径组合能力
        dict_remote_paths=dict_remote_paths,  # pytest 摘要在远端 skill 内的路径配置
        str_remote_path_key="pytest_summary_json",  # pytest 摘要路径对应的配置键
        str_local_filename="remote_pytest_summary.json",  # 供 require-remote 消费的本地 JSON 名称
    )

    # 返回单个 retained run 的统一摘要结构，供 report-runs 聚合输出。
    return {
        "run": str_run_name,
        "remote_skill": str_remote_skill,
        "pytest": dict_dependencies["summarize_pytest_report"](dict_pytest_summary),
        "remote_execute": dict_dependencies["summarize_validation_report"](
            dict_execute_report,
            rtl_path=str_execute_rtl_path,
            testbench_path=str_execute_testbench_path,
            validation_json=str_execute_validation_json,
        ),
        "fixtures": dict_dependencies["summarize_fixture_report"](dict_fixture_summary),
    }

# download_json_optional_impl 下载远端 JSON 文件，失败时返回 None。
def download_json_optional_impl(
    remote_context: Any,
    str_remote_path: str,
    str_local_path: str,
    *,
    dict_dependencies: dict[str, Any],
) -> dict[str, Any] | None:
    """下载远端 JSON 证据，失败或缺失时返回 None。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_path: 远端 JSON 文件绝对路径。
    :param str_local_path: 本地下载目标路径。
    :param dict_dependencies: 下载远端 JSON 所需的 helper 回调集合。
    :return: 成功时返回 JSON 字典；失败或缺失时返回 None。
    """

    # file-download 阶段仍复用 helper 基础参数和目标服务器配置。
    list_base = dict_dependencies["helper_base"](remote_context)  # JSON 下载命令依赖的 helper 参数

    # 先发起远端 JSON 下载请求，失败时交给上层把证据标成缺失。
    completed_process_json_download = dict_dependencies["run_helper"](  # file-download helper 执行结果
        remote_context.path_helper,  # 触发 retained 文件下载的 helper 入口路径
        [  # file-download 场景的命令参数清单
            "file-download",  # retained JSON 下载子命令
            *list_base,  # 沿用当前远端会话的基础连接参数
            "--server",  # 指定下载来源服务器
            remote_context.str_server,  # 当前 retained run 所在服务器名
            "--remote",  # 指定远端 JSON 文件地址
            str_remote_path,  # 待下载的远端 JSON 路径
            "--local",  # 指定本地落盘目标路径
            str_local_path,  # 本地报告目录中的 JSON 目标路径
        ],
        allow_failure=True,  # 允许可选证据缺失时返回非零
        quiet_on_failure=True,  # 下载失败时压低 helper 噪声
    )

    # helper 返回非零时视为可选证据缺失，不阻断 retained-run 摘要输出。
    if completed_process_json_download.returncode != 0:

        # 缺失远端 JSON 证据时返回 None，让上层统一转成 unavailable 摘要。
        return None

    # helper stdout 会打印真实下载落点，这里统一交给下载路径解析器处理。
    path_downloaded = dict_dependencies["parse_download_path"](  # helper 实际写出的本地 JSON 文件路径
        completed_process_json_download.stdout  # helper stdout 中的下载落点文本
    )

    # 下载路径不存在时仍按缺失证据处理，避免假定 helper 一定成功写盘。
    if not path_downloaded.exists():

        # 本地下载文件缺失时同样返回 None，让 retained-run 摘要走缺失分支。
        return None

    # 读取 helper 下载完成的 JSON 文件，并交给上层摘要逻辑继续压缩。
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

# summarize_pytest_report 把 retained pytest JSON 收敛为稳定的 report-runs 契约。
def summarize_pytest_report(dict_report: dict[str, Any] | None) -> dict[str, Any]:
    """汇总权威远程 pytest 的精确计数和耗时。

    :param dict_report: 下载得到的 pytest JSON；旧 retained run 缺失时为 None。
    :return: 包含 available、ok、计数和耗时的稳定摘要。
    """

    # 旧 retained run 没有 pytest JSON 时显式标记不可用，禁止误当完整证据。
    if not isinstance(dict_report, dict):

        # 缺失结构化计数时保留原因，要求调用方重新执行远程 gate。
        return {
            "available": False,
            "ok": False,
            "reason": "remote pytest summary is unavailable",
        }

    # 计数统一转为整数，避免下载 JSON 中的宽松类型进入发布证据。
    int_passed = int(dict_report.get("passed", 0))  # pytest 通过用例数

    # 跳过数独立保留，防止发布摘要把未执行项并入通过数。
    int_skipped = int(dict_report.get("skipped", 0))  # pytest 跳过用例数

    # 只有明确 passed 且至少执行一个用例时，结构化 pytest 证据才算通过。
    bool_ok = dict_report.get("status") == "passed" and int_passed > 0  # pytest 摘要是否满足通过契约

    # 保留精确计数、耗时和原始摘要行，便于机器门禁与人工复核使用同一事实源。
    return {
        "available": True,
        "ok": bool_ok,
        "status": str(dict_report.get("status", "")),
        "passed": int_passed,
        "skipped": int_skipped,
        "xfailed": int(dict_report.get("xfailed", 0)),
        "xpassed": int(dict_report.get("xpassed", 0)),
        "deselected": int(dict_report.get("deselected", 0)),
        "duration_seconds": float(dict_report.get("duration_seconds", 0.0)),
        "summary_line": str(dict_report.get("summary_line", "")),
    }

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
