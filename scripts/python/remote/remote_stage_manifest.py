"""远端验证 staging 的 tracked ``.agents`` 清单与复制实现。"""

# 标准库负责 JSON 清单、Git 索引查询和普通文件复制。
import json
import shutil
import subprocess

# pathlib 同时约束本地路径和 manifest 中的平台无关路径。
from pathlib import Path, PurePosixPath

# 将递归清单收进允许的 .agents 目录，避免污染候选项目根布局。
STAGING_AGENTS_MANIFEST = ".agents/staging-manifest.json"  # staging 内的 tracked 文件清单路径

# manifest 版本变更必须显式升级读取合同。
STAGING_AGENTS_MANIFEST_VERSION = 1  # 当前 tracked .agents 清单格式版本

# _validate_manifest_path 校验 manifest 中单个 .agents 相对路径。
def _validate_manifest_path(str_value: str) -> Path:
    """把 manifest 路径转换为受控的 ``.agents`` 相对路径。

    :param str_value: manifest 中使用 POSIX 分隔符的相对路径。
    :return: 可安全挂到本地 ``.agents`` 根下的 Path。
    :raises RuntimeError: 路径为空、绝对、含反斜杠或目录穿越时抛出。
    """

    # manifest 固定 POSIX 形式，拒绝平台相关反斜杠避免双重解释。
    if not str_value or "\\" in str_value:

        # 空路径和反斜杠路径都不满足稳定 manifest 合同。
        raise RuntimeError(f"> ERR: [Python] Invalid staged .agents manifest path: {str_value!r}")

    # PurePosixPath 只解析 manifest 协议，不受当前宿主平台影响。
    path_posix = PurePosixPath(str_value)  # manifest 中的平台无关相对路径

    # 绝对路径和父目录片段都可能逃逸 staging 的 .agents 根。
    if path_posix.is_absolute() or ".." in path_posix.parts:

        # 路径边界异常必须失败关闭，不能继续复制其他条目。
        raise RuntimeError(f"> ERR: [Python] Unsafe staged .agents manifest path: {str_value!r}")

    # 当前平台 Path 只接收已验证的 POSIX 路径片段。
    return Path(*path_posix.parts)

# _read_staged_manifest 读取无 Git staging 的受控 tracked 文件集合。
def _read_staged_manifest(path_project_root: Path) -> tuple[Path, ...]:
    """读取并验证 staging 项目根的 tracked ``.agents`` manifest。

    :param path_project_root: 不含 Git 索引的 staging 项目根。
    :return: 排序、去重且仍存在的 ``.agents`` 相对文件路径。
    :raises RuntimeError: manifest 缺失、损坏或声明不安全文件时抛出。
    """

    # manifest 位于 staging 项目根，避免把协议文件混入治理事实源目录。
    path_manifest = path_project_root / STAGING_AGENTS_MANIFEST  # 当前 staging 的 tracked 清单

    # 无 Git 且无 manifest 时无法证明治理文件边界。
    if not path_manifest.is_file():

        # 缺失 manifest 必须失败关闭，不能退回整个 .agents 复制。
        raise RuntimeError(f"> ERR: [Python] Missing staged .agents manifest: {path_manifest}")

    # JSON 解析错误统一转换为稳定的远端 staging 领域错误。
    try:

        # UTF-8 是 manifest 唯一编码，保证本地与远端解释一致。
        dict_manifest = json.loads(path_manifest.read_text(encoding="utf-8"))  # 原始 manifest 对象

    # 非法 JSON 不能提供可信的 tracked 文件边界。
    except (OSError, json.JSONDecodeError) as exc:

        # 错误保留 manifest 路径，便于定位损坏的 retained run。
        raise RuntimeError(f"> ERR: [Python] Invalid staged .agents manifest: {path_manifest}") from exc

    # 版本必须精确匹配，禁止未知格式被旧代码误读。
    if not isinstance(dict_manifest, dict) or dict_manifest.get("version") != STAGING_AGENTS_MANIFEST_VERSION:

        # 结构或版本不匹配时停止递归 staging。
        raise RuntimeError(f"> ERR: [Python] Unsupported staged .agents manifest: {path_manifest}")

    # tracked_files 必须是字符串列表，不能接受隐式类型转换。
    list_values = dict_manifest.get("tracked_files")  # manifest 声明的 POSIX 相对路径列表

    # 非列表或含非字符串条目的 manifest 不能进入路径解析。
    if not isinstance(list_values, list) or not all(isinstance(value_item, str) for value_item in list_values):

        # 非字符串条目会破坏路径边界验证。
        raise RuntimeError(f"> ERR: [Python] Invalid tracked_files in staged manifest: {path_manifest}")

    # manifest 写入合同要求路径已经排序且去重。
    if list_values != sorted(set(list_values)):

        # 非规范列表可能掩盖重复覆盖或非确定性 source digest。
        raise RuntimeError(f"> ERR: [Python] Non-canonical staged .agents manifest: {path_manifest}")

    # 每个声明文件都要在当前 staging 中存在且不是符号链接。
    list_relative_files: list[Path] = []  # 已验证的 .agents 相对文件路径

    # 逐项验证路径语法、文件存在性和符号链接边界。
    for str_value in list_values:

        # 协议路径先转换为受控本地相对路径。
        path_relative = _validate_manifest_path(str_value)  # 当前 manifest 条目的本地相对形式

        # 声明文件固定挂在 staging 项目 .agents 根下。
        path_source = path_project_root / ".agents" / path_relative  # manifest 声明的治理文件

        # 缺失文件或符号链接都使 manifest 与当前 staging 不一致。
        if not path_source.is_file() or path_source.is_symlink():

            # 不一致时停止，避免递归包使用不完整或越界治理事实源。
            raise RuntimeError(f"> ERR: [Python] Invalid staged .agents file: {path_source}")

        # 通过所有边界检查后加入确定性复制集合。
        list_relative_files.append(path_relative)

    # 输入已验证为排序列表，tuple 保持调用方只读语义。
    return tuple(list_relative_files)

# _git_tracked_agent_files 从当前 checkout 的 Git 索引读取治理事实源。
def _git_tracked_agent_files(path_project_root: Path) -> tuple[Path, ...] | None:
    """尝试从 Git 索引读取 ``.agents`` tracked 文件。

    :param path_project_root: 候选项目根目录。
    :return: Git 仓库返回路径元组；非 Git staging 返回 None。
    :raises RuntimeError: Git 成功但返回非法路径时抛出。
    """

    # Git 命令使用 NUL 分隔输出，完整保留包含空格的 tracked 路径。
    list_git_command = [  # 查询项目 .agents tracked 文件的参数列表
        "git",  # Git 可执行程序
        "-C",  # 指定 Git 工作目录参数
        str(path_project_root),  # 当前项目根目录
        "ls-files",  # 查询索引文件列表
        "-z",  # 使用 NUL 分隔路径
        "--",  # 结束 Git 选项解析
        ".agents",  # 限定项目治理目录
    ]

    # Git 索引区分治理事实源和本地运行证据。
    completed_process_git = subprocess.run(  # 当前 checkout 的 .agents tracked 文件查询
        list_git_command,  # 仅查询 .agents tracked 文件的 argv
        capture_output=True,  # 捕获路径列表和失败诊断
        check=False,  # 非 Git staging 交给 manifest 回退
    )

    # 非 Git staging 由调用方读取首层生成的 manifest。
    if completed_process_git.returncode != 0:

        # None 明确区分“没有 Git 索引”和“Git 返回空 tracked 集合”。
        return None

    # -z 输出保留文件名中的空格，UTF-8 严格解码避免静默替换路径字节。
    str_stdout = completed_process_git.stdout.decode("utf-8")  # NUL 分隔的 tracked 路径文本

    # 只接受 .agents 内部相对路径，阻止异常索引输出逃逸 staging 目标。
    list_relative_files: list[Path] = []  # 相对于 .agents 根的受控 tracked 文件列表

    # 每个 Git 条目都要验证目录边界和当前工作树存在性。
    for str_tracked_path in filter(None, str_stdout.split("\0")):

        # Git 输出统一使用 POSIX 分隔符并固定带 .agents 前缀。
        path_tracked = PurePosixPath(str_tracked_path)  # 项目根相对的 tracked POSIX 路径

        # relative_to 同时验证条目确实位于 .agents 下。
        try:

            # staging 目标只保留去掉 .agents 前缀后的受控相对路径。
            path_relative_posix = path_tracked.relative_to(".agents")  # .agents 根相对 POSIX 路径

        # 非 .agents 路径说明 Git 输出不满足本函数的安全合同。
        except ValueError as exc:

            # 路径边界异常必须停止 staging，不能忽略后继续上传。
            raise RuntimeError(
                f"> ERR: [Python] Git returned a path outside .agents: {str_tracked_path}"
            ) from exc

        # 共用 manifest 路径校验，保持 Git 和递归 staging 语义一致。
        path_relative = _validate_manifest_path(path_relative_posix.as_posix())  # 受控本地相对路径

        # 工作树中已删除的 tracked 文件不属于当前候选快照。
        if (path_project_root / ".agents" / path_relative).is_file():

            # 只把当前工作树仍存在的 tracked 文件加入复制清单。
            list_relative_files.append(path_relative)

    # Git 输出通常已排序，这里显式排序保证 manifest 合同稳定。
    return tuple(sorted(list_relative_files, key=lambda path_item: path_item.as_posix()))

# tracked_project_agent_files 统一 Git checkout 和无 Git staging 的文件边界。
def tracked_project_agent_files(path_project_root: Path) -> tuple[Path, ...]:
    """返回项目 ``.agents`` 下可信的治理事实源路径。

    :param path_project_root: Git checkout 或已生成 manifest 的 staging 根。
    :return: 相对于 ``.agents`` 根的稳定排序文件路径。
    :raises RuntimeError: 两种可信来源都不可用或内容非法时抛出。
    """

    # 首层开发 checkout 优先使用实时 Git 索引。
    tuple_git_files = _git_tracked_agent_files(path_project_root)  # Git 可用时的 tracked 路径集合

    # Git 返回集合时直接采用，包括合法的空 tracked 集合。
    if tuple_git_files is not None:

        # Git 空集合也是有效事实，不应错误回退到旧 manifest。
        return tuple_git_files

    # 无 Git 的 retained staging 只能使用首层生成的规范 manifest。
    return _read_staged_manifest(path_project_root)

# _write_staged_manifest 原子写入下一层递归 staging 所需的清单。
def _write_staged_manifest(path_staged_agents: Path, tuple_relative_files: tuple[Path, ...]) -> None:
    """在 staging 的 ``.agents`` 目录原子写入 tracked 文件 manifest。

    :param path_staged_agents: staging 内的 ``.agents`` 目标目录。
    :param tuple_relative_files: 本轮实际复制的 tracked 相对路径。
    :return: 不返回业务值；写入失败时保留底层异常。
    """

    # manifest 路径固定落在 staging 的 .agents 目录内。
    path_manifest = path_staged_agents.parent / STAGING_AGENTS_MANIFEST  # 最终 manifest 路径

    # 临时文件与最终文件同目录，replace 可提供同文件系统原子替换。
    path_temporary = path_manifest.with_name(path_manifest.name + ".tmp")  # manifest 临时写入路径

    # JSON 键排序、紧凑分隔和 LF 结尾共同定义确定性字节合同。
    dict_manifest = {  # 递归 staging 使用的最小 manifest 载荷
        "tracked_files": [path_item.as_posix() for path_item in tuple_relative_files],  # 排序后的路径
        "version": STAGING_AGENTS_MANIFEST_VERSION,  # manifest 合同版本
    }

    # 单行 JSON 避免格式器差异改变 source digest。
    str_manifest = json.dumps(  # 规范 manifest 文本
        dict_manifest,  # 已按固定键名构造的清单载荷
        ensure_ascii=False,  # 保留合法 Unicode 文件名
        separators=(",", ":"),  # 使用紧凑单行 JSON
        sort_keys=True,  # 键顺序跨运行稳定
    ) + "\n"

    # 先写临时文件，避免中断留下可被下一层读取的半截 JSON。
    path_temporary.write_text(str_manifest, encoding="utf-8", newline="\n")

    # 原子替换只在完整写入成功后发布 manifest。
    path_temporary.replace(path_manifest)

# stage_project_agents 复制治理事实源并发布递归 staging 清单。
def stage_project_agents(path_project_root: Path, path_staged_agents: Path) -> None:
    """把可信 ``.agents`` 文件复制到 staging 并写入 manifest。

    :param path_project_root: Git checkout 或无 Git staging 项目根。
    :param path_staged_agents: 新 staging 内的 ``.agents`` 目标目录。
    :return: 不返回业务值；边界或复制失败时保留异常。
    """

    # 文件集合必须先完整验证，再开始向新 staging 写入。
    tuple_relative_files = tracked_project_agent_files(path_project_root)  # 本轮治理事实源边界

    # 逐文件复制保留嵌套结构，同时不携带未跟踪的门禁报告和运行日志。
    for path_relative in tuple_relative_files:

        # 源路径固定挂在项目 .agents 根下。
        path_source = path_project_root / ".agents" / path_relative  # 当前 tracked 治理文件

        # 符号链接可能把 staging 内容指向仓库边界外，必须失败关闭。
        if path_source.is_symlink():

            # 拒绝符号链接，避免把仓库外内容带入远端验证载荷。
            raise RuntimeError(f"> ERR: [Python] Refusing symlinked .agents file: {path_source}")

        # 目标父目录按 tracked 相对路径创建，不复制任何额外目录内容。
        path_destination = path_staged_agents / path_relative  # staging 中的治理文件目标

        # 嵌套治理事实源需要先创建对应的 staging 父目录。
        path_destination.parent.mkdir(parents=True, exist_ok=True)

        # copy2 保留普通文件元数据，不跟随任何已拒绝的符号链接。
        shutil.copy2(path_source, path_destination)

    # 文件全部复制成功后再发布下一层递归 staging 清单。
    _write_staged_manifest(path_staged_agents, tuple_relative_files)
