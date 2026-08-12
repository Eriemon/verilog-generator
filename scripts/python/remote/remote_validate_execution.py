"""远端验证的 staging、helper 调用和 retained-run 摘要实现。"""

# 标准库负责 JSON 协议、远端请求子进程、临时包 staging 和清理。
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

# tarfile 和 time 负责确定性归档与 Windows 文件锁重试。
import tarfile
import time

# pathlib 负责本地路径。
from pathlib import Path

# Any 只用于 helper 上下文、JSON 载荷和输出流。
from typing import Any, Callable

# facade 在执行本模块前注册 sibling manifest 的稳定动态加载别名。
from readable_verilog_remote_stage_manifest import stage_project_agents, tracked_project_agent_files

# skill 主体根目录供 staging 和本地缓存清理复用。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 包含 runtime、scripts 与 config 的 skill 根目录

# 仓库根目录用于复制 smoke harness。
PATH_PROJECT_ROOT = PATH_SKILL_ROOT.parents[1]  # 当前 skill 仓库根目录

# 归档与清单文件名固定，保证远端单文件上传和解包路径可审计。
PACKAGE_ARCHIVE_NAME = "readable-verilog-generator-package.tar.gz"  # 远端验证包归档名

# 清单自身不纳入清单记录，避免清单内容形成自引用哈希。
PACKAGE_MANIFEST_NAME = "package-manifest.json"  # 归档内逐文件完整性清单名

# 新布局的每轮运行目录统一位于固定远端根的 runs 子目录。
REMOTE_RUNS_DIRECTORY = "runs"  # 新 retained run 的远端容器目录

# 旧根只用于 --run-id run-* 的只读兼容查询，不参与新运行写入。
LEGACY_REMOTE_ROOT = ".readable-verilog-generator-validation"  # 历史 retained 根目录

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

    # 只复制 Git tracked 项目控制事实源，不携带未跟踪的门禁报告和运行日志。
    stage_project_agents(PATH_PROJECT_ROOT, path_staged_agents)

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

    # 在归档前写入逐文件清单，避免递归上传缺失文件只能在 pytest 收集阶段暴露。
    write_package_manifest(path_package_root)

    # 返回 staging 包根，finally 中由 cleanup_package 删除。
    return path_package_root

# write_package_manifest 为远端包生成确定性的逐文件内容清单。
def write_package_manifest(path_package_root: Path) -> Path:
    """为 staging 包写入逐文件路径、大小和 SHA-256 清单。

    :param path_package_root: 已完成复制的本地 staging 包根目录。
    :return: 写入后的 package-manifest.json 路径。
    :raises RuntimeError: staging 中出现符号链接或文件读取失败时抛出。
    """

    # 当前 staging 的逐文件清单路径。
    path_manifest = path_package_root / PACKAGE_MANIFEST_NAME  # 清单写入目标

    # 清单中的稳定文件记录。
    list_records: list[dict[str, object]] = []  # 不包含清单自身

    # 排序后写入，保证不同文件系统枚举顺序不会改变清单字节。
    list_paths = sorted(  # staging 中待处理的有序路径
        (path_item for path_item in path_package_root.rglob("*") if path_item != path_manifest),  # 排除清单自身
        key=lambda path_item: path_item.relative_to(path_package_root).as_posix(),  # 按相对路径稳定排序
    )

    # 逐项检查 staging 中的路径类型和内容。
    for path_file in list_paths:

        # 归档边界不接受符号链接，避免远端解包逃逸 staging 根。
        if path_file.is_symlink():

            # 发现链接时立即停止，避免产生不可审计的归档。
            raise RuntimeError(f"> ERR: [Python] Staging package contains symlink: {path_file}")

        # 目录由归档格式保留，清单只记录普通文件内容。
        if not path_file.is_file():

            # 目录不产生文件级哈希记录。
            continue

        # 文件路径和原始字节共同形成远端完整性事实。
        bytes_file = path_file.read_bytes()  # 当前文件的原始字节

        # 将路径、大小和内容摘要写入清单记录。
        list_records.append(
            {
                "path": path_file.relative_to(path_package_root).as_posix(),
                "sha256": hashlib.sha256(bytes_file).hexdigest(),
                "size": len(bytes_file),
            }
        )

    # 归档完整性清单载荷，键顺序和 LF 结尾保持稳定。
    dict_manifest = {"files": list_records, "schema": 1}  # 固定 schema 版本

    # 统一 JSON 键顺序和 LF 结尾，保证 manifest 可复核。
    path_manifest.write_text(
        json.dumps(dict_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # 返回清单路径供归档和调用方复核。
    return path_manifest

# package_archive_path 计算 staging 旁边的唯一归档路径。
def package_archive_path(path_package_root: Path) -> Path:
    """返回当前 staging 包对应的 tar.gz 归档路径。

    :param path_package_root: 本地 staging 包根目录。
    :return: 与 staging 同级的归档文件路径。
    """

    # 归档放在 staging 外并使用固定文件名，便于远端请求与日志审计。
    return path_package_root.parent / PACKAGE_ARCHIVE_NAME

# add_package_archive_entries 写入 staging 中的全部目录和文件条目。
def add_package_archive_entries(path_package_root: Path, archive: tarfile.TarFile) -> None:
    """把 staging 路径按稳定顺序写入已打开的 tar 流。

    :param path_package_root: 已完成 staging 的本地包根目录。
    :param archive: 已配置为写入确定性 gzip 流的 tar 文件对象。
    :return: 不返回业务值；发现符号链接时抛出异常。
    :raises RuntimeError: staging 含符号链接时抛出。
    """

    # staging 中待归档的有序路径。
    list_paths = sorted(  # 归档顺序固定
        path_package_root.rglob("*"),  # 包含目录和文件条目
        key=lambda path_item: path_item.relative_to(path_package_root).as_posix(),  # 按相对路径排序
    )

    # 逐项写入目录和文件条目。
    for path_item in list_paths:

        # 符号链接不允许进入跨主机验证包。
        if path_item.is_symlink():

            # 发现链接时立即停止归档。
            raise RuntimeError(f"> ERR: [Python] Staging package contains symlink: {path_item}")

        # 归档条目使用 POSIX 相对路径，避免 Windows 分隔符污染远端。
        str_archive_name = path_item.relative_to(path_package_root).as_posix()  # 归档内相对名称

        # 读取当前路径的 tar 元数据。
        tar_info_tar_info: tarfile.TarInfo = archive.gettarinfo(  # 当前归档条目
            str(path_item),  # 当前 staging 路径
            arcname=str_archive_name,  # 归档内使用的相对名称
        )

        # 归零修改时间，保持不同主机生成结果稳定。
        tar_info_tar_info.mtime = 0  # 固定归档修改时间

        # 归零归档用户标识，避免本地账户信息进入包。
        tar_info_tar_info.uid = 0  # 固定归档用户标识

        # 归零归档组标识，避免本地账户信息进入包。
        tar_info_tar_info.gid = 0  # 固定归档组标识

        # 清空归档用户名，避免平台名称影响包字节。
        tar_info_tar_info.uname = ""  # 固定归档用户名

        # 清空归档组名，避免平台名称影响包字节。
        tar_info_tar_info.gname = ""  # 固定归档组名

        # 普通文件需要把原始字节写入 tar 条目。
        if path_item.is_file():

            # 打开当前文件并写入归档。
            with path_item.open("rb") as file_source:

                # 保留 tar 元数据与文件内容的对应关系。
                archive.addfile(tar_info_tar_info, file_source)

        # 目录只写入目录元数据，不附带文件流。
        else:

            # 写入空内容的目录条目。
            archive.addfile(tar_info_tar_info)

# create_package_archive 将完整 staging 树压缩成可原子上传的单文件。
def create_package_archive(path_package_root: Path) -> Path:
    """创建包含清单的确定性 tar.gz 远端验证包。

    :param path_package_root: 已完成 staging 的本地包根目录。
    :return: 可交给 erie-remote-ssh request-upload 的归档路径。
    :raises RuntimeError: staging 含符号链接或归档路径不安全时抛出。
    """

    # 归档前刷新逐文件完整性清单，兼容 staging 后追加事实文件的调用方。
    write_package_manifest(path_package_root)

    # 当前 run 的上传归档路径。
    path_archive = package_archive_path(path_package_root)  # 归档位于 staging 同级

    # 同一 staging run 重试时先替换旧归档，避免 append 造成重复成员。
    if path_archive.exists():

        # 删除旧归档，保证本次归档从空文件开始。
        path_archive.unlink()

    # 使用固定 gzip 头、tar 元数据和排序路径生成可复核归档。
    with path_archive.open("wb") as file_archive:

        # 固定 gzip mtime，避免相同 staging 在不同时间产生不同归档。
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=file_archive,
            mtime=0,
        ) as gzip_stream:

            # tar 直接写入固定 gzip 流，远端可解包到 workspace 父目录。
            with tarfile.open(fileobj=gzip_stream, mode="w") as archive:

                # 写入 staging 中的全部稳定归档条目。
                add_package_archive_entries(path_package_root, archive)

    # 归档必须在返回前成为普通文件，防止 request-upload 读取半成品。
    if not path_archive.is_file():

        # 归档缺失时拒绝进入远端上传阶段。
        raise RuntimeError(f"> ERR: [Python] Package archive was not created: {path_archive}")

    # 返回已完成的本地归档。
    return path_archive

# cleanup_package_archive 只删除当前 staging 同级的归档文件。
def cleanup_package_archive(path_package_root: Path) -> None:
    """清理本地远端验证归档，不影响 retained 远端证据。

    :param path_package_root: 本地 staging 包根目录。
    :return: 归档不存在时无操作。
    :raises AssertionError: 归档路径不在受控 staging tmp 目录时拒绝删除。
    """

    # 当前 staging 对应归档。
    path_archive = package_archive_path(path_package_root)  # 固定名称的临时归档

    # 归档不存在时清理操作保持幂等。
    if not path_archive.exists():

        # 归档缺失时直接结束清理。
        return

    # 删除边界与 staging 根保持一致，防止路径推导扩大范围。
    if path_archive.parent != path_package_root.parent or path_archive.name != PACKAGE_ARCHIVE_NAME:

        # 路径不符合本流程约束时拒绝删除。
        raise AssertionError(f"> ERR: [Python] Refusing to remove unexpected package archive: {path_archive}")

    # 删除当前 run 的临时归档，不影响远端 retained 证据。
    path_archive.unlink()

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

    # 只允许删除 reports/tmp 下由本流程生成的 readable-verilog-generator-* staging 目录。
    if path_resolved.parent.name != "tmp" or not path_resolved.name.startswith("readable-verilog-generator-"):

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

    # --no-project 固定关闭 erie-remote-ssh 的隐式项目自动发现，保持所有子命令语义一致。
    return [
        "--no-project",
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
    exact_run_id: str | None = None,
    dict_dependencies: dict[str, Any],
) -> dict[str, Any]:
    """读取远端 retained run 列表并汇总摘要证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param int_max_runs: 最多返回的运行条目数量。
    :param exact_run_id: 调用方要求精确汇总的 outer run 标识；省略时按时间选择。
    :param dict_dependencies: 汇总 retained-run 列表所需的回调集合。
    :return: 包含 remote_root、runs 和 status 的稳定字典。
    :raises ValueError: int_max_runs 小于 1 时抛出。
    """

    # 至少要读取一条 run，--report-runs 在 0 条上没有意义。
    if int_max_runs < 1:

        # 直接抛出带前缀错误，提示调用方修正 CLI 参数。
        raise ValueError("> ERR: [Python] --max-runs must be at least 1.")

    # 自动化 exact 模式不枚举远端根，避免并发运行改变“最新”目录含义。
    if exact_run_id is not None:

        # 新 run 使用 validation_ 前缀；旧 run-* 仅允许进入固定历史根的只读查询。
        bool_new_run = re.fullmatch(r"validation_[A-Za-z0-9_-]+", exact_run_id) is not None  # 新布局身份是否安全

        # 旧 run-* 只允许读取历史根，不参与新运行写入。
        bool_legacy_run = re.fullmatch(r"run-[A-Za-z0-9_-]+", exact_run_id) is not None  # 历史身份是否安全

        # outer run 名称只能是单个安全目录片段，禁止路径穿越和任意路径读取。
        if not bool_new_run and not bool_legacy_run:

            # 不安全标识必须在构造任何远端读取路径前失败关闭。
            raise ValueError("> ERR: [Python] --run-id must be a safe validation_* or legacy run-* directory name.")

        # 旧身份只从明确的历史根读取，不把历史目录迁移或写入新根。
        str_effective_root = LEGACY_REMOTE_ROOT if bool_legacy_run else str_remote_root  # 精确查询使用的远端根

        # 保持稳定返回协议，同时只汇总调用方明确绑定的一轮证据。
        return {
            "remote_root": str_effective_root,
            "runs": [dict_dependencies["summarize_remote_run"](remote_context, str_effective_root, exact_run_id)],
            "status": "ok",
        }

    # 新布局只枚举固定根下的 runs 子目录，历史根不参与默认“最新”选择。
    list_base = dict_dependencies["helper_base"](remote_context)  # retained root 列表查询参数

    # file-list 只读取新布局的 runs 容器，不会触碰旧历史根。
    str_remote_runs_root = dict_dependencies["remote_join"](str_remote_root, REMOTE_RUNS_DIRECTORY)  # 新布局 runs 容器路径

    # 查询模式会先读取 validation_* 目录，再按时间倒序筛出最新证据。
    completed_process_listing = dict_dependencies["run_helper"](  # file-list 目录枚举的 helper 执行结果
        remote_context.path_helper,  # 远端 helper 可执行入口
        [  # file-list 子命令参数序列
            "file-list",  # retained root 目录枚举子命令
            *list_base,  # helper 基础连接参数
            "--server",  # 指定目录枚举目标服务器
            remote_context.str_server,  # retained 根目录所在服务器名
            "--path",  # 指定待查询 retained 根目录
            str_remote_runs_root,  # 新 retained run 容器地址
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

        # 字典型目录列表直接暴露 entries 字段，供后续 validation_* 过滤逻辑复用。
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
        and str(dict_item.get("name", "")).startswith("validation_")  # 仅保留新 validation_* 目录
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

# 定位 outer retained run 中最新的 timestamped smoke 证据根。
def _discover_remote_smoke_run(
    remote_context: Any,
    str_remote_reports: str,
    *,
    dict_dependencies: dict[str, Any],
    bool_direct_reports: bool = False,
) -> str:
    """枚举远程 reports 并返回最新的 smoke 运行目录。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_reports: 当前 retained skill 的 reports 根目录。
    :param dict_dependencies: 文件枚举、JSON 解析与路径组合回调。
    :param bool_direct_reports: 新布局下 reports 本身就是唯一证据根。
    :return: 最新 timestamped smoke 目录的远程路径。
    """

    # 新布局不再创建 reports/smoke_runs_*，直接把 reports 作为本轮唯一证据根。
    if bool_direct_reports:

        # 返回 canonical reports 路径，所有证据文件都直接落在该目录内。
        return str_remote_reports

    # 兼容旧 retained run 的只读摘要，仍然读取 reports 直接子项。
    list_helper_arguments = [  # reports 直接子项枚举参数
        "file-list",  # 只读文件列表子命令
        *dict_dependencies["helper_base"](remote_context),  # 当前连接基础参数
        "--server",  # 服务器选项名
        remote_context.str_server,  # retained run 所在服务器
        "--path",  # 枚举根路径选项名
        str_remote_reports,  # 当前 retained skill 的 reports 根目录
    ]

    # 回调别名缩短调用表达式，同时保留可测试依赖注入。
    func_run_helper = dict_dependencies["run_helper"]  # 远程 helper 执行回调

    # 远程枚举调用保留非零状态，以便把不可读 reports 稳定降级为可诊断的 missing 证据定位。
    completed_process_listing = func_run_helper(  # 判断 reports 是否可读并为后续唯一选定本轮证据根提供直接子项载荷
        remote_context.path_helper,  # 实际执行只读 file-list 请求的 helper 入口路径
        list_helper_arguments,  # 限定服务器和 reports 根目录的枚举参数
        allow_failure=True,  # 缺失 reports 时改为生成可诊断的证据缺失摘要
        quiet_on_failure=True,  # 报告聚合阶段不重复打印已由状态表达的 helper 错误
    )

    # 枚举失败时保留空载荷，让下游用 missing 路径产生可诊断结果。
    if completed_process_listing.returncode == 0:

        # 只解析成功 helper 输出，避免把错误文本当成目录列表。
        dict_listing = dict_dependencies["parse_json_output"](completed_process_listing.stdout)  # 用于筛选 timestamped 目录的枚举载荷

    # helper 非零时不解析错误文本，而是进入证据缺失路径。
    else:

        # 空载荷让下游按证据缺失处理。
        dict_listing = {}  # 枚举失败时的空目录载荷

    # 非字典载荷不可信，按无可用目录处理。
    list_entries = dict_listing.get("entries", []) if isinstance(dict_listing, dict) else []  # reports 直接子项

    # 候选列表只收集约定前缀的直接子目录。
    list_run_names = []  # 可用 timestamped smoke 目录名

    # 逐项校验 helper 载荷，忽略文件与异常项。
    for dict_entry in list_entries:

        # 只有目录类型和命名合同同时成立才可作为证据根。
        if (
            isinstance(dict_entry, dict)
            and dict_entry.get("type") == "dir"
            and str(dict_entry.get("name", "")).startswith("smoke_runs_")
        ):

            # 只有符合类型与前缀合同的目录才参与最新项选择。
            list_run_names.append(str(dict_entry["name"]))

    # 目录名内嵌可排序时间戳，升序最后一项即最新运行。
    list_run_names.sort()

    # missing 占位使后续下载保持原有的可选证据语义。
    str_run_name = list_run_names[-1] if list_run_names else "smoke_runs_missing"  # 选中的 smoke 目录名

    # 所有下游证据路径从这一个 timestamped 根展开。
    return dict_dependencies["remote_join"](str_remote_reports, str_run_name)

# 封装 retained run 的对外摘要协议。
def _build_remote_run_summary(
    str_run_name: str,
    str_remote_skill: str,
    str_remote_smoke_run: str,
    dict_evidence: dict[str, dict[str, Any]],
    dict_agent_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组合 outer run 定位和三类远程证据摘要。

    :param str_run_name: outer retained run 名称。
    :param str_remote_skill: 远程上传 skill 根目录。
    :param str_remote_smoke_run: 本轮 timestamped smoke 证据根。
    :param dict_evidence: completion、pytest、execute、fixture 和测试证据摘要。
    :param dict_agent_review: Agent 自动审核摘要；旧 retained run 缺失时为 None。
    :return: report-runs 对外输出的单轮摘要。
    """

    # 运行定位与验证证据分组表达，组合后仍维持单层稳定协议。
    return {
        "run": str_run_name,
        "remote_skill": str_remote_skill,
        "smoke_run": str_remote_smoke_run,
        "agent_review": dict_agent_review,
    } | {
        "completion": dict_evidence["completion"],
        "pytest": dict_evidence["pytest"],
        "remote_execute": dict_evidence["remote_execute"],
        "fixtures": dict_evidence["fixtures"],
        "test_evidence": dict_evidence["test_evidence"],
    }

# _map_retained_paths 固化新旧 retained run 的读取路径。
def _map_retained_paths(
    remote_context: Any,
    str_remote_root: str,
    str_run_name: str,
    *,
    dict_dependencies: dict[str, Any],
    dict_remote_paths: dict[str, str],
) -> dict[str, str]:
    """解析 retained run 的 outer、workspace、reports 和证据路径。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 当前读取的远端 retained 根目录。
    :param str_run_name: 当前 retained run 名称。
    :param dict_dependencies: report-runs 使用的路径与下载回调。
    :param dict_remote_paths: 远端证据相对路径映射。
    :return: 新旧布局统一的 retained 路径映射。
    """

    # 新 validation_* 运行采用 runs/<id>/{workspace,reports}；旧 run-* 保持历史布局只读读取。
    bool_new_layout = str_run_name.startswith("validation_")  # 当前 run 是否采用新 workspace/reports 布局

    # 新布局的 outer run 根位于固定 remote_root/runs 下，旧布局直接位于历史根下。
    str_remote_parent = (  # 新旧布局的 outer run 绝对路径
        dict_dependencies["remote_join"](str_remote_root, REMOTE_RUNS_DIRECTORY, str_run_name)  # 新布局 outer 路径
        if bool_new_layout  # 新布局条件
        else dict_dependencies["remote_join"](str_remote_root, str_run_name)  # 旧布局兼容路径
    )

    # 上传 skill 在新布局的 workspace 子目录，旧 retained run 保持原始直接目录。
    str_remote_skill = (  # 上传源码的 workspace 路径
        dict_dependencies["remote_join"](str_remote_parent, "workspace", "readable-verilog-generator")  # 新 workspace 源码路径
        if bool_new_layout  # 新布局源码条件
        else dict_dependencies["remote_join"](str_remote_parent, "readable-verilog-generator")  # 旧 skill 源码路径
    )

    # 新布局的报告目录直接位于 outer run 根；旧布局沿用 skill/reports。
    str_remote_reports = (  # 当前 run 的直接报告目录
        dict_dependencies["remote_join"](str_remote_parent, "reports")  # 新布局直接报告路径
        if bool_new_layout  # 新布局报告条件
        else dict_dependencies["remote_join"](str_remote_skill, "reports")  # 旧布局报告路径
    )

    # 选中最新 timestamped 目录，供 execution、fixture 和 pytest 共用。
    str_remote_smoke_run = _discover_remote_smoke_run(  # 选择本轮最新时间序列目录，供所有证据复用
        remote_context,  # 使用已验证服务器的远程会话上下文
        str_remote_reports,  # 从 outer run 的报告容器开始发现结果
        dict_dependencies=dict_dependencies,  # 注入 report-runs 的路径与下载契约
        bool_direct_reports=bool_new_layout,  # 新布局直接读取 reports 子项而不套 smoke_runs
    )

    # execution、RTL 和 testbench 路径全部从同一 smoke 根展开。
    str_execute_validation_json = dict_dependencies["remote_join"](  # 记录执行校验文件位置，便于回看原始状态
        str_remote_smoke_run,  # 将校验文件挂到选定时间序列根下
        dict_remote_paths["execute_validation_json"],  # 使用注册表声明的 validation JSON 相对位置
    )

    # readable RTL 产物路径需要独立保留，供 execution 摘要追溯。
    str_execute_rtl_path = dict_dependencies["remote_join"](  # 保留 readable RTL 地址，支撑远程工件追溯
        str_remote_smoke_run,  # 沿用同一时间序列目录保证工件成套
        dict_remote_paths["execute_rtl_path"],  # 读取 execute 证据注册的 RTL 相对位置
    )

    # 仿真激励入口路径需要独立保留，供失败回放定位。
    str_execute_testbench_path = dict_dependencies["remote_join"](  # 保存 testbench 地址，供失败复跑直接取用
        str_remote_smoke_run,  # 让激励文件与 execution JSON 保持同一证据根
        dict_remote_paths["execute_testbench_path"],  # 使用验证入口注册的激励相对位置
    )

    # 返回 report-runs 下游下载器共用的路径合同。
    return {
        "remote_parent": str_remote_parent,
        "remote_skill": str_remote_skill,
        "remote_reports": str_remote_reports,
        "remote_smoke_run": str_remote_smoke_run,
        "execute_validation_json": str_execute_validation_json,
        "execute_rtl_path": str_execute_rtl_path,
        "execute_testbench_path": str_execute_testbench_path,
        "new_layout": "1" if bool_new_layout else "0",
    }

# _download_phase_summaries 读取 targeted、regression、full 阶段摘要。
def _download_phase_summaries(
    remote_context: Any,
    str_run_name: str,
    *,
    dict_dependencies: dict[str, Any],
    dict_remote_paths: dict[str, str],
    dict_paths: dict[str, str],
) -> dict[str, Any]:
    """下载三阶段 pytest 原始摘要。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_run_name: 当前 retained run 名称。
    :param dict_dependencies: report-runs 使用的下载回调。
    :param dict_remote_paths: 远端阶段摘要相对路径映射。
    :param dict_paths: 已解析的 retained 路径映射。
    :return: targeted、regression、full 三阶段原始摘要。
    """

    # 阶段容器按固定顺序保存三类独立 pytest 事实。
    dict_phase_summaries: dict[str, Any] = {}  # 三阶段 pytest 原始载荷

    # 每个阶段绑定自己的远程 JSON，避免 aggregate 冒充阶段结果。
    for str_phase, str_path_key in (
        ("targeted", "pytest_targeted_summary_json"),
        ("regression", "pytest_regression_summary_json"),
        ("full", "pytest_full_summary_json"),
    ):

        # 循环每次下载一个阶段文件，随后由映射承载通过数、跳过数和退出码。
        dict_phase_summaries[str_phase] = _download_retained_summary(  # 该项收集当前阶段的通过数、跳过数与退出码，供一致性门禁逐项比较
            remote_context,  # 当前阶段下载会话
            dict_paths["remote_smoke_run"],  # 时间序列证据根
            str_run_name,  # 当前 outer run 的阶段归档标识
            dict_dependencies=dict_dependencies,  # 阶段下载回调
            dict_remote_paths=dict_remote_paths,  # 阶段路径注册表
            str_remote_path_key=str_path_key,  # 当前阶段的注册键
            str_local_filename=f"remote_pytest_{str_phase}_summary.json",  # 本地阶段文件名
        )

    # 返回三阶段具名映射，供 aggregate 摘要器统一读取。
    return dict_phase_summaries

# _download_core_payloads 读取 execution、fixture 和 aggregate pytest 证据。
def _download_core_payloads(
    remote_context: Any,
    str_run_name: str,
    *,
    dict_dependencies: dict[str, Any],
    dict_remote_paths: dict[str, str],
    dict_paths: dict[str, str],
) -> dict[str, Any]:
    """下载 retained run 的核心验证载荷。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_run_name: 当前 retained run 名称。
    :param dict_dependencies: report-runs 使用的下载回调。
    :param dict_remote_paths: 远端证据相对路径映射。
    :param dict_paths: 已解析的 retained 路径映射。
    :return: execution、fixture 和 aggregate pytest 原始载荷。
    """

    # execution JSON 保留模拟执行的原始状态与工件索引。
    dict_execute_report = dict_dependencies["download_json_optional"](  # 载入执行 JSON 供门禁摘要读取
        remote_context,  # 复用当前远程会话完成文件传输
        dict_paths["execute_validation_json"],  # 读取已映射的远端校验文件
        dict_dependencies["remote_join"](  # 计算本地执行证据保存位置
            "readable-verilog-generator-report",  # 本地 retained 报告容器
            str_run_name,  # 以本轮 outer 名称划分证据
            "remote_execute_validation.json",  # 本地 execution JSON 文件名
        ),
    )

    # fixture 摘要单独保存，保持固定样例回归的证据边界。
    dict_fixture_summary = _download_retained_summary(  # 下载固定 fixture 汇总文件
        remote_context,  # 当前远程文件下载上下文
        dict_paths["remote_smoke_run"],  # fixture 所在的时间序列证据根
        str_run_name,  # fixture 结果对应的 outer run
        dict_dependencies=dict_dependencies,  # fixture 下载所需回调
        dict_remote_paths=dict_remote_paths,  # fixture 注册路径集合
        str_remote_path_key="fixture_summary_json",  # fixture JSON 的注册键
        str_local_filename="remote_fixture_summary.json",  # 本地 fixture 归档文件名
    )

    # aggregate pytest 摘要提供总计数，阶段摘要另行下载。
    dict_pytest_summary = _download_retained_summary(  # 取得 aggregate pytest 总结文件
        remote_context,  # 沿用当前报告查询的连接
        dict_paths["remote_smoke_run"],  # aggregate 文件的时间序列根
        str_run_name,  # 将总计数挂到 outer run
        dict_dependencies=dict_dependencies,  # aggregate 下载依赖集合
        dict_remote_paths=dict_remote_paths,  # 让 aggregate 下载与阶段注册表保持同一来源
        str_remote_path_key="pytest_summary_json",  # 通过注册键定位 aggregate JSON
        str_local_filename="remote_pytest_summary.json",  # 将 aggregate 副本写入本地归档
    )

    # 返回核心三类原始载荷，供外层编排器继续组合。
    return {
        "execute": dict_execute_report,
        "fixture": dict_fixture_summary,
        "pytest": dict_pytest_summary,
    }

# _download_identity_payloads 读取测试总表、completion 和 Agent 审核文件。
def _download_identity_payloads(
    remote_context: Any,
    str_run_name: str,
    *,
    dict_dependencies: dict[str, Any],
    dict_remote_paths: dict[str, str],
    dict_paths: dict[str, str],
) -> dict[str, Any]:
    """下载 retained run 的身份与终止状态载荷。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_run_name: 当前 retained run 名称。
    :param dict_dependencies: report-runs 使用的下载回调。
    :param dict_remote_paths: 远端证据相对路径映射。
    :param dict_paths: 已解析的 retained 路径映射。
    :return: test_evidence、completion 和 agent_review 原始载荷。
    """

    # 测试总表记录环境、哈希和阶段身份，缺失时由上层标记不可用。
    dict_test_evidence = _download_retained_summary(  # 读取测试身份总表
        remote_context,  # 使用当前服务器会话下载总表
        dict_paths["remote_smoke_run"],  # 总表所在时间序列目录
        str_run_name,  # 当前 outer run 归档键
        dict_dependencies=dict_dependencies,  # 总表下载回调
        dict_remote_paths=dict_remote_paths,  # 总表注册路径
        str_remote_path_key="test_evidence_json",  # 身份总表键
        str_local_filename="remote_test_evidence.json",  # 本地身份总表文件名
    )

    # completion 文件用于核对运行标识和最终终止状态。
    dict_completion = _download_retained_summary(  # 该对象保存本轮运行标识、终止状态和成功标记，供状态门禁比较
        remote_context,  # 复用 retained 查询连接
        dict_paths["remote_smoke_run"],  # completion 所在时间序列目录
        str_run_name,  # 将终止状态绑定 outer run
        dict_dependencies=dict_dependencies,  # completion 下载回调
        dict_remote_paths=dict_remote_paths,  # completion 注册路径
        str_remote_path_key="completion_json",  # completion 清单注册键名
        str_local_filename="remote_completion.json",  # 将终止清单落到本地 retained 证据区
    )

    # Agent 审核文件位于 outer 根，旧 retained run 缺失时返回 None。
    dict_agent_review = dict_dependencies["download_json_optional"](  # 读取 Agent 自动审核结果
        remote_context,  # 当前审核文件下载上下文
        dict_dependencies["remote_join"](dict_paths["remote_parent"], "agent_review.json"),  # outer 根审核文件
        dict_dependencies["remote_join"](  # 本地审核归档位置
            "readable-verilog-generator-report",  # 把审核副本写入本地报告索引根
            str_run_name,  # 以 outer run 区分审核副本
            "agent_review.json",  # 保持审核文件名稳定
        ),
    )

    # 返回身份、终止和审核三类载荷，供对外摘要携带。
    return {
        "test_evidence": dict_test_evidence,
        "completion": dict_completion,
        "agent_review": dict_agent_review,
    }

# _download_retained_payloads 读取 retained run 的原始 JSON 证据。
def _download_retained_payloads(
    remote_context: Any,
    str_run_name: str,
    *,
    dict_dependencies: dict[str, Any],
    dict_remote_paths: dict[str, str],
    dict_paths: dict[str, str],
) -> dict[str, Any]:
    """下载 retained run 的全部原始 JSON 载荷。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_run_name: 当前 retained run 名称。
    :param dict_dependencies: report-runs 使用的下载回调。
    :param dict_remote_paths: 远端证据相对路径映射。
    :param dict_paths: 已解析的 retained 路径映射。
    :return: 核心、阶段和身份载荷的统一映射。
    """

    # 核心 execution、fixture 和 aggregate 证据先单独下载。
    dict_core_payloads = _download_core_payloads(  # 取得 execution、fixture、aggregate 三类核心载荷
        remote_context,  # 核心文件下载共用的远程会话
        str_run_name,  # 用 outer 标识隔离核心证据副本
        dict_dependencies=dict_dependencies,  # 核心下载依赖
        dict_remote_paths=dict_remote_paths,  # 核心路径配置
        dict_paths=dict_paths,  # 已解析路径映射
    )

    # targeted、regression、full 摘要保持独立文件读取。
    dict_phase_summaries = _download_phase_summaries(  # 读取三个阶段的独立 pytest 文件
        remote_context,  # 阶段文件读取所需的远程会话
        str_run_name,  # 将阶段副本绑定到同一 outer 标识
        dict_dependencies=dict_dependencies,  # 阶段下载依赖
        dict_remote_paths=dict_remote_paths,  # 阶段路径配置
        dict_paths=dict_paths,  # 阶段路径根
    )

    # 测试身份、completion 和 Agent 审核载荷最后归档。
    dict_identity_payloads = _download_identity_payloads(  # 身份与审核载荷
        remote_context,  # 身份下载上下文
        str_run_name,  # 身份归档键
        dict_dependencies=dict_dependencies,  # 身份下载依赖
        dict_remote_paths=dict_remote_paths,  # 身份路径配置
        dict_paths=dict_paths,  # 身份路径根
    )

    # 合并三类具名载荷，保持 report-runs 的既有字段协议。
    return {
        **dict_core_payloads,
        "phases": dict_phase_summaries,
        **dict_identity_payloads,
    }

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

    # 统一解析新旧布局，避免主汇总函数重复维护远程目录语义。
    dict_paths = _map_retained_paths(  # retained 路径映射
        remote_context,  # 当前远程 helper 上下文
        str_remote_root,  # 固定根或旧根
        str_run_name,  # 当前 retained run 名称
        dict_dependencies=dict_dependencies,  # 路径回调集合
        dict_remote_paths=dict_remote_paths,  # 远端证据相对路径
    )

    # 统一下载 retained run 的原始载荷，保留旧 run-* 的只读兼容性。
    dict_payloads = _download_retained_payloads(  # retained 原始证据映射
        remote_context,  # 载入 retained 证据所需的复用连接对象
        str_run_name,  # 以 outer 标识选择本地下载分区
        dict_dependencies=dict_dependencies,  # 下载回调集合
        dict_remote_paths=dict_remote_paths,  # 向下载器传递执行文件注册表
        dict_paths=dict_paths,  # 已解析的 retained 路径
    )

    # pytest 摘要同时携带 targeted、regression、full 三阶段原始结果。
    dict_pytest_report = dict_dependencies["summarize_pytest_report"](  # 该对象保存总通过数、跳过数和阶段状态，供 report-runs 输出
        dict_payloads["pytest"],  # aggregate pytest 原始载荷
        dict_phase_summaries=dict_payloads["phases"],  # targeted、regression、full 阶段原始载荷
    )

    # execution 摘要保留 RTL、testbench 和 validation JSON 的远程定位。
    dict_validation_report = dict_dependencies["summarize_validation_report"](  # 该对象保存执行成功标记、后端和工件定位，供远程门禁核验
        dict_payloads["execute"],  # 交给摘要器读取模拟执行原始 JSON
        rtl_path=dict_paths["execute_rtl_path"],  # RTL 远程路径
        testbench_path=dict_paths["execute_testbench_path"],  # 失败回放所需的激励入口
        validation_json=dict_paths["execute_validation_json"],  # validation JSON 原始证据位置
    )

    # fixture 摘要维持固定最小案例的独立回归边界。
    dict_fixtures_report = dict_dependencies["summarize_fixture_report"](  # 提炼固定样例回归结果，供远程门禁检查
        dict_payloads["fixture"],  # 输入已下载的最小案例汇总文件
    )

    # 对外协议按稳定键名汇总完成、pytest、execute、fixture 和测试总表证据。
    dict_remote_evidence = {  # 组装 report-runs 对外需要的完整证据分组
        "completion": dict_payloads["completion"],  # 记录完成清单中的最终状态
        "pytest": dict_pytest_report,  # 携带各阶段 pytest 的压缩计数
        "remote_execute": dict_validation_report,  # 保留模拟执行的工具链摘要
        "fixtures": dict_fixtures_report,  # 保留固定 fixture 的独立结论
        "test_evidence": {  # 测试总表分组
            "available": isinstance(dict_payloads["test_evidence"], dict),  # 总表可用性
            "remote": dict_payloads["test_evidence"],  # 远程总表载荷
            "phase_summaries": dict_payloads["phases"],  # 阶段原始映射
        },
    }

    # 统一协议函数保证 report-runs 输出新旧布局共有的键名。
    return _build_remote_run_summary(
        str_run_name,
        dict_paths["remote_skill"],
        dict_paths["remote_smoke_run"],
        dict_remote_evidence,
        dict_payloads["agent_review"],
    )

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
def summarize_pytest_report(
    dict_report: dict[str, Any] | None,
    *,
    dict_phase_summaries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总权威远程 pytest 的精确计数和耗时。

    :param dict_report: 下载得到的 pytest JSON；旧 retained run 缺失时为 None。
    :param dict_phase_summaries: targeted、regression 和 full 阶段 JSON 映射。
    :return: 包含 available、ok、计数和耗时的稳定摘要。
    """

    # 旧 retained run 没有 pytest JSON 时显式标记不可用，禁止误当完整证据。
    if not isinstance(dict_report, dict):

        # 缺失结构化计数时保留原因，要求调用方重新执行远程 gate。
        return {
            "available": False,
            "ok": False,
            "reason": "remote pytest summary is unavailable",
            "phases": {},
        }

    # 计数统一转为整数，避免下载 JSON 中的宽松类型进入发布证据。
    int_passed = int(dict_report.get("passed", 0))  # pytest 通过用例数

    # 跳过数独立保留，防止发布摘要把未执行项并入通过数。
    int_skipped = int(dict_report.get("skipped", 0))  # pytest 跳过用例数

    # 只有明确 passed 且至少执行一个用例时，结构化 pytest 证据才算通过。
    bool_ok = dict_report.get("status") == "passed" and int_passed > 0  # pytest 摘要是否满足通过契约

    # 阶段摘要只暴露机器所需字段，原始 evidence 仍由 test_evidence 分组保留。
    dict_phase_report: dict[str, Any] = {}  # 面向 report-runs 的阶段摘要映射

    # 优先使用调用方单独下载的阶段摘要，兼容旧嵌入式 phases 字段。
    dict_phase_inputs: dict[str, Any] | None = dict_phase_summaries  # 阶段摘要输入

    # 没有独立阶段文件时才回退到旧 aggregate 内嵌字段。
    if dict_phase_inputs is None:

        # 旧 retained report 可能把阶段摘要嵌在 aggregate JSON 内。
        obj_embedded_phases: object = dict_report.get("phases", {})  # 旧报告中的嵌入式阶段对象

        # 只有字典结构才能安全进入阶段循环。
        dict_phase_inputs = obj_embedded_phases if isinstance(obj_embedded_phases, dict) else {}  # 规范阶段输入

    # 每个阶段独立收敛 status、count、命令摘要和时间戳。
    for str_phase, dict_phase in dict_phase_inputs.items():

        # 正常阶段对象进入可用摘要分支。
        if isinstance(dict_phase, dict):

            # 可用字段保持稳定，原始 test_evidence 分组另保留完整载荷。
            dict_phase_report[str_phase] = {  # 当前阶段对外摘要
                "available": True,  # 阶段 JSON 已成功读取
                "ok": dict_phase.get("status") == "passed" and dict_phase.get("exit_code") == 0,  # 阶段是否通过
                "status": str(dict_phase.get("status", "")),  # 阶段状态文本
                "passed": int(dict_phase.get("passed", 0)),  # 当前阶段已通过的测试数量
                "skipped": int(dict_phase.get("skipped", 0)),  # 当前阶段被 pytest 跳过的测试数量
                "count": int(dict_phase.get("count", 0)),  # 阶段覆盖合计数
                "exit_code": int(dict_phase.get("exit_code", 0)),  # 阶段退出码
                "command_hash": str(dict_phase.get("command_hash", "")),  # 阶段命令摘要
                "timestamp": str(dict_phase.get("timestamp", "")),  # 阶段完成时间
            }

        # 非对象阶段必须显式标记不可用，不能猜测状态。
        else:

            # 缺失结构的阶段仍保留名称，供调用方精确定位缺口。
            dict_phase_report[str_phase] = {  # 当前阶段结构不可用摘要
                "available": False,  # 阶段结构缺失
                "ok": False,  # 不可用阶段不能通过
            }

    # 保留精确计数、耗时、阶段和原始摘要行，便于机器门禁与人工复核使用同一事实源。
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
        "phases": dict_phase_report,
        "remote_test_evidence": dict_report.get("remote_test_evidence", {}),
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
