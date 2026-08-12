"""validate_verilog_skill 的工作区边界与残留清理 helper。"""

# future annotations 让工作区上下文类型提示保持前向引用友好。
from __future__ import annotations

# shutil 提供目录树删除能力，供 smoke 与 pycache 清理复用。
import shutil

# time 负责在 Windows 句柄短占用场景下执行短暂重试等待。
import time

# dataclass 用来收束工作区 helper 需要共享的路径上下文。
from dataclasses import dataclass

# pathlib 负责表达 skill 根、项目根与 smoke 根路径。
from pathlib import Path

# WorkspaceGateContext 汇总工作区清理 helper 反复使用的路径边界。
@dataclass(frozen=True)
class WorkspaceGateContext:
    """
    保存工作区清理 helper 需要复用的路径上下文。

    :param path_skill_root: 当前 readable-verilog-generator skill 根目录。
    :param path_project_root: 当前仓库根目录。
    :param path_workspace_root: 当前 validate 子进程默认工作目录。
    :return: 不返回业务值；实例化完成即表示路径边界已可供 helper 复用。
    """

    # path_skill_root 用于限制 skill 内删除边界。
    path_skill_root: Path  # 当前 skill 根目录

    # path_project_root 用于把诊断路径压成项目相对路径。
    path_project_root: Path  # 当前仓库根目录

    # path_workspace_root 用于把相对工件路径锚定到 validate 当前工作根。
    path_workspace_root: Path  # validate 工作目录根

# verify_no_residuals 检查当前 worker 目录和 skill 主体里是否还残留禁止产物。
def verify_no_residuals(
    settings: dict,
    smoke_dir: Path,
    workspace_context: WorkspaceGateContext,
) -> None:
    """
    检查 smoke 目录与 skill 根下是否还有禁止残留。

    :param settings: Verilog skill 治理配置字典。
    :param smoke_dir: 当前 validate worker 使用的 smoke 目录。
    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 不返回业务值；通过时表示没有禁止残留留在工作区里。
    :raises AssertionError: 当 smoke 或 skill 根内仍存在禁止残留时抛出。
    """

    # path_setting 只在真正需要解析 smoke 根目录时才延迟导入。
    from scripts.python.workflow.config import path_setting

    # 先准备残留摘要列表，后续命中时统一压成单行错误消息。
    list_residuals: list[str] = []  # 禁止残留路径摘要列表

    # 再把 forbidden_residuals 读成集合，方便按名称和路径片段双重匹配。
    set_forbidden_names = set(settings.get("validation", {}).get("forbidden_residuals", []))  # 禁止残留名称集合

    # 同步解析配置里的 smoke 根目录，供后续边界判断复用。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # 配置定义的 smoke 根目录

    # 当前 worker 目录如果仍然存在，说明 validate 主流程还留下了本轮残留。
    if smoke_dir.exists():

        # 先把当前 worker 目录转成项目相对路径，再记录到残留摘要里。
        list_residuals.append(project_relative(smoke_dir, workspace_context))

    # 再递归遍历 skill 根目录下的全部条目，继续检查禁止残留名称。
    for path_entry in workspace_context.path_skill_root.rglob("*"):

        # 先尝试把条目解析成规范绝对路径，处理并发删除导致的瞬态缺失。
        try:

            # 规范路径供后续 smoke 根排除和 relative_to 边界判断复用。
            path_resolved = path_entry.resolve()  # 当前条目的规范绝对路径

        # 并发清理已经删除的条目不构成真正残留，直接跳过即可。
        except FileNotFoundError:

            # 条目已在扫描过程中消失时，不再把它算作残留。
            continue

        # smoke 根目录本身由主流程单独管理，不应在 skill 根残留扫描里重复计数。
        if path_resolved == path_smoke_root:

            # 命中 smoke 根目录本体时，直接进入下一条扫描。
            continue

        # 先排除 smoke 根目录内部的条目，避免把运行区内容再算进 skill 主体残留。
        try:

            # 能相对到 smoke 根时，说明当前条目属于运行目录内部内容。
            path_resolved.relative_to(path_smoke_root)

            # smoke 根内部条目由本轮 worker 清理逻辑处理，这里直接跳过。
            continue

        # 不在 smoke 根内部的条目继续参加 skill 主体残留扫描。
        except ValueError:

            # 当前条目不属于 smoke 根内部，继续下面的禁止名称检查。
            pass

        # 命中禁止名称或任一路径片段时，就需要把它登记为残留。
        if path_entry.name in set_forbidden_names or any(
            str_part in set_forbidden_names for str_part in path_entry.parts
        ):

            # 把 skill 相对路径压进摘要，避免在错误文本里泄露本机绝对路径。
            list_residuals.append(path_entry.relative_to(workspace_context.path_skill_root).as_posix())

    # 发现任何残留时，都要把排序后的摘要显式抛给上层 validate。
    if list_residuals:

        # 先把残留列表排序并压成单行，方便人工逐项清理。
        str_residual_summary = ", ".join(sorted(list_residuals))  # 排序后的残留摘要

        # 用统一 ERR 前缀暴露当前仍然存在的禁止残留。
        raise AssertionError("> ERR: [Python] Residual validation artifacts remain: " + str_residual_summary)

# cleanup_residuals 清理当前 worker 允许自动删除的 smoke、state 与缓存产物。
def cleanup_residuals(
    settings: dict,
    smoke_dir: Path,
    workspace_context: WorkspaceGateContext,
) -> None:
    """
    清理本轮 validate 允许自动删除的 smoke、state 与缓存。

    :param settings: Verilog skill 治理配置字典。
    :param smoke_dir: 当前 validate worker 使用的 smoke 目录。
    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 不返回业务值；通过时表示当前 worker 的常规残留已被回收。
    """

    # 先删除当前 worker 自己负责的 smoke 目录。
    remove_inside_smoke_root(settings, smoke_dir)

    # 再删除 skill 根里可能残留的 workflow-state.json。
    remove_inside_skill(workspace_context.path_skill_root / "workflow-state.json", workspace_context)

    # 继续逆序清理全部 __pycache__ 目录，降低父目录先删导致的 Windows 失败概率。
    for path_cache_dir in sorted(workspace_context.path_skill_root.rglob("__pycache__"), reverse=True):

        # 每个缓存目录都经过 skill 根边界检查后再删除。
        remove_inside_skill(path_cache_dir, workspace_context)

    # 最后尝试裁掉已经完全清空的 smoke 根目录壳。
    prune_empty_smoke_root(settings)

# cleanup_audit_retry_local_artifacts 只回收当前 worker 自己负责的 audit 局部残留。
def cleanup_audit_retry_local_artifacts(
    settings: dict,
    smoke_dir: Path,
    workspace_context: WorkspaceGateContext,
) -> None:
    """
    清理 audit 重试只归属当前 worker 的本地残留。

    :param settings: Verilog skill 治理配置字典。
    :param smoke_dir: 当前 validate worker 使用的 smoke 目录。
    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 不返回业务值；通过时表示当前 worker 的局部残留已被回收。
    """

    # audit 局部重试直接复用常规残留清理，确保不会误删并行兄弟目录。
    cleanup_residuals(settings, smoke_dir, workspace_context)

# cleanup_audit_runtime_artifacts 清空允许 audit 重新生成的运行区内容。
def cleanup_audit_runtime_artifacts(
    settings: dict,
    smoke_dir: Path,
    workspace_context: WorkspaceGateContext,
) -> None:
    """
    清空 audit 重试后允许重新生成的运行产物。

    :param settings: Verilog skill 治理配置字典。
    :param smoke_dir: 当前 validate worker 使用的 smoke 目录。
    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 不返回业务值；通过时表示 audit 可重建的运行产物已被清空。
    """

    # path_setting 只在真正需要解析 smoke 根目录时才延迟导入。
    from scripts.python.workflow.config import path_setting

    # 先回收常规残留，确保 workflow-state 和 pycache 不再干扰 audit 重试。
    cleanup_residuals(settings, smoke_dir, workspace_context)

    # 再解析 audit 运行区对应的 smoke 根目录，供整棵运行树回收使用。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # audit 运行区的 smoke 根目录

    # smoke 根本身不存在时，说明当前没有运行产物需要额外回收。
    if not path_smoke_root.exists():

        # 没有运行目录时，当前 helper 直接结束即可。
        return

    # 继续逆序清理 smoke 根内部条目，确保深层目录先于父级被移除。
    for path_entry in sorted(path_smoke_root.iterdir(), reverse=True):

        # 每个条目都继续走 smoke 根边界检查后的删除流程。
        remove_inside_smoke_root(settings, path_entry)

    # 运行条目清空后，再尝试裁掉已经空掉的 smoke 根目录壳。
    prune_empty_smoke_root(settings)

# prune_empty_smoke_root 只在 smoke 根完全为空时移除目录壳。
def prune_empty_smoke_root(settings: dict) -> None:
    """
    仅在 smoke 根已空时裁剪目录壳。

    :param settings: Verilog skill 治理配置字典。
    :return: 不返回业务值；通过时表示 smoke 根目录壳已按需裁剪。
    """

    # path_setting 只在真正需要解析 smoke 根目录时才延迟导入。
    from scripts.python.workflow.config import path_setting

    # 先解析目录壳裁剪所针对的 smoke 根目录。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # 待裁剪目录壳所在的 smoke 根目录

    # smoke 根已经不存在时，没有目录壳需要继续裁剪。
    if not path_smoke_root.exists():

        # 根目录本身不存在时，当前 helper 直接结束即可。
        return

    # 先只尝试读取一个目录项，借此判断 smoke 根当前是否为空。
    try:

        # 只要能读出一个子项，就说明 smoke 根仍然不能裁掉。
        next(path_smoke_root.iterdir())

    # StopIteration 表示目录已经为空，此时才允许继续删掉目录壳。
    except StopIteration:

        # 目录已经为空时，再尝试把 smoke 根目录壳一并移除。
        try:

            # 当前 smoke 根目录壳已经不承载任何内容，可以直接删除。
            path_smoke_root.rmdir()

        # 并发清理如果已经删掉目录壳，本轮也视为成功。
        except FileNotFoundError:

            # 目录壳已被其他流程抢先删除时，不再重复报错。
            return

    # 并发清理可能在 exists 之后就已经删掉根目录，本轮同样保持幂等成功。
    except FileNotFoundError:

        # 根目录已在目录项探测前被其他流程删除时，不再重复报错。
        return

# remove_inside_skill 负责在 skill 根目录边界内安全删除文件或目录。
def remove_inside_skill(path_target: Path, workspace_context: WorkspaceGateContext) -> None:
    """
    在 skill 根目录边界内安全删除文件或目录。

    :param path_target: 待删除或展示的目标路径。
    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 不返回业务值；通过时表示目标已删除或已不存在。
    :raises AssertionError: 当目标路径越过 skill 根删除边界时抛出。
    """

    # 先把目标解析成规范绝对路径，处理并发删除导致的瞬态缺失。
    try:

        # 规范路径供 skill 根边界判断与后续删除逻辑复用。
        path_resolved = path_target.resolve()  # 目标的规范绝对路径

    # 目标已被其他流程删除时，不需要继续执行边界检查或删除动作。
    except FileNotFoundError:

        # 当前目标已经不存在时，当前 helper 保持幂等成功。
        return

    # 目标在解析后已经不存在时，也不需要继续删除。
    if not path_resolved.exists():

        # 其他流程已删除目标时，当前 helper 直接结束即可。
        return

    # 删除前先确认目标仍然位于 skill 根边界以内。
    try:

        # 能相对到 skill 根时，说明当前目标属于允许自动删除的边界范围。
        path_resolved.relative_to(workspace_context.path_skill_root.resolve())

    # 越界路径必须立刻阻断，避免 validate 误删仓库外内容。
    except ValueError as exc:

        # 用统一错误文本明确指出越界删除请求被拒绝。
        raise AssertionError(f"> ERR: [Python] Refusing to remove outside skill root: {path_resolved}") from exc

    # 继续区分目录与文件删除路径，保持删除语义清晰。
    try:

        # 目录目标走带重试的目录树删除逻辑。
        if path_resolved.is_dir():

            # Windows 目录句柄短占用时，交给重试版目录删除 helper 处理。
            remove_tree_with_retry(path_resolved)

        # 单文件目标直接执行 unlink 即可。
        else:

            # 当前目标是单文件时，不需要递归删除逻辑。
            path_resolved.unlink()

    # 目录或文件在边界检查后被其他流程删除时，当前 helper 仍保持幂等成功。
    except FileNotFoundError:

        # 目标已经消失时，不再把并发清理暴露成失败。
        return

# remove_inside_smoke_root 专门回收运行目录内部条目，避免 audit 清理误越界。
def remove_inside_smoke_root(settings: dict, path_target: Path) -> None:
    """
    在 smoke 根目录边界内安全删除文件或目录。

    :param settings: Verilog skill 治理配置字典。
    :param path_target: 待删除或展示的目标路径。
    :return: 不返回业务值；通过时表示目标已删除或已不存在。
    :raises AssertionError: 当目标路径越过 smoke 根删除边界时抛出。
    """

    # path_setting 只在真正需要解析 smoke 根目录时才延迟导入。
    from scripts.python.workflow.config import path_setting

    # 先解析本次运行目录真正对应的 smoke 根，用作删除边界。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # 运行目录的 smoke 根边界

    # 先把待删条目解析成规范路径，避免相对路径绕过运行区边界。
    try:

        # 解析后的绝对落点将决定这次运行区清理是否允许继续执行。
        path_resolved = path_target.resolve()  # 本轮待回收运行条目的绝对落点

    # 运行条目若已被兄弟清理链回收，就按成功处理。
    except FileNotFoundError:

        # 当前运行条目已经不存在时，不需要再重复删除。
        return

    # 解析后发现运行条目已消失，说明清理目标已经达成。
    if not path_resolved.exists():

        # 运行条目已被其他重试链移除时，直接结束当前清理。
        return

    # 删除前确认条目仍位于 smoke 根下面，防止误删运行区外路径。
    try:

        # 能相对到 smoke 根时，说明当前目标属于允许自动删除的运行区内容。
        path_resolved.relative_to(path_smoke_root)

    # 越界路径必须立刻阻断，避免 validate 误删运行区外内容。
    except ValueError as exc:

        # 用统一错误文本明确指出运行区越界删除请求被拒绝。
        raise AssertionError(f"> ERR: [Python] Refusing to remove outside smoke root: {path_resolved}") from exc

    # 根据条目类型选择目录树删除或单文件删除。
    try:

        # 运行子目录通常承载 case 产物，需要递归回收整个目录树。
        if path_resolved.is_dir():

            # smoke 子目录可能包含多层运行产物，需要递归删除。
            remove_tree_with_retry(path_resolved)

        # 单个运行文件直接 unlink 即可，不必走目录树删除。
        else:

            # 普通运行文件删除后即可释放当前产物。
            path_resolved.unlink()

    # 边界检查后若条目又被并发清理掉，当前 helper 仍按成功处理。
    except FileNotFoundError:

        # 并发删除完成时，不再把 smoke 目录清理暴露成失败。
        return

# remove_tree_with_retry 负责缓解 Windows 文件句柄短占用导致的 rmtree 失败。
def remove_tree_with_retry(path_target: Path, *, attempts: int = 5, delay_s: float = 0.1) -> None:
    """
    带短暂重试地删除目录树。

    :param path_target: 待删除的目录路径。
    :param attempts: 最多重试次数。
    :param delay_s: 两次重试之间的等待秒数。
    :return: 不返回业务值；通过时表示目录树已删除或已不存在。
    :raises OSError: 当重试耗尽后目录树仍无法删除时抛出。
    """

    # 先准备最后一次 OSError 容器，供重试耗尽后重新抛出。
    value_last_error: OSError | None = None  # 最后一次目录删除失败异常

    # 再按限定次数循环删除目录树，覆盖短暂句柄占用的瞬态失败。
    for _ in range(attempts):

        # 每次都完整尝试一次 rmtree，成功后立即结束当前 helper。
        try:

            # 当前目录已经经过上层边界检查，可以直接执行递归删除。
            shutil.rmtree(path_target)

            # 目录树删除成功后，当前 helper 直接结束。
            return

        # 目录已被其他流程删除时，当前 helper 保持幂等成功。
        except FileNotFoundError:

            # 目录树已经不存在时，不再继续重试。
            return

        # 其余系统级删除失败需要短暂等待后再试一次。
        except OSError as exc:

            # 先保存本轮删除失败异常，供重试耗尽后重新抛出。
            value_last_error = exc  # 最近一次目录删除失败异常

            # 失败后如果目录已经消失，说明清理目标其实已经达成。
            if not path_target.exists():

                # 目录树已在失败检查后消失时，不再继续重试。
                return

            # 目录仍然存在时，给 Windows 一点时间释放句柄后再重试。
            time.sleep(delay_s)

    # 重试耗尽且仍保存着系统异常时，需要把失败显式抛给上层。
    if value_last_error is not None:

        # 用统一错误文本和原始 cause 报告目录树删除最终失败。
        raise OSError("> ERR: [Python] directory removal failed after retries.") from value_last_error

# iter_skill_files 枚举可参与发布与残留审计的 skill 普通文件。
def iter_skill_files(workspace_context: WorkspaceGateContext) -> list[Path]:
    """
    列出 skill 根下可参与文本审计的普通文件。

    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 返回通过过滤条件的 Path 对象列表。
    """

    # 先准备需要跳过的目录片段集合。
    set_ignored_parts = {"__pycache__", "_smoke_runs", "reports"}  # 需要跳过的目录片段

    # 再准备需要跳过的特定文件名集合。
    set_ignored_names = {"RELEASE_RECEIPT.json"}  # 需要跳过的生成元数据文件名

    # 继续准备结果列表，后续逐个追加通过过滤条件的文件。
    list_files: list[Path] = []  # 通过过滤条件的 skill 文件列表

    # 最后递归遍历 skill 根目录下的全部条目。
    for path_entry in workspace_context.path_skill_root.rglob("*"):

        # 非普通文件不属于文本审计目标，直接跳过即可。
        if not path_entry.is_file():

            # 当前条目不是普通文件时，不再参与后续过滤。
            continue

        # 先把当前文件相对 skill 根的路径片段读成集合。
        set_relative_parts = set(path_entry.relative_to(workspace_context.path_skill_root).parts)  # 当前文件相对路径片段集合

        # 命中缓存或报告目录时，当前文件不进入文本审计。
        if set_relative_parts & set_ignored_parts:

            # 当前文件位于忽略目录内部时，直接跳过即可。
            continue

        # 命中特定生成元数据文件名时，当前文件不进入文本审计。
        if path_entry.name in set_ignored_names:

            # 当前文件是生成元数据时，直接跳过即可。
            continue

        # Python 字节码产物不属于可审计源码内容。
        if path_entry.suffix.lower() in {".pyc", ".pyo"}:

            # 当前文件是 Python 字节码产物时，直接跳过即可。
            continue

        # 通过全部过滤条件后，再把当前文件追加到结果列表里。
        list_files.append(path_entry)

    # 把完整结果列表返回给上层审计 helper 复用。
    return list_files

# project_relative 负责把路径压成项目相对形式，供诊断输出复用。
def project_relative(path_target: Path, workspace_context: WorkspaceGateContext) -> str:
    """
    把路径转换成项目相对形式，供诊断输出复用。

    :param path_target: 待转换的路径对象。
    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 返回项目相对路径；若不在项目根内则回退到原始字符串。
    """

    # 先尝试把目标路径压成相对项目根的 POSIX 路径。
    try:

        # 成功相对化时，统一返回 POSIX 分隔符形式。
        return path_target.relative_to(workspace_context.path_project_root).as_posix()

    # 项目根外路径无法相对化时，只能回退到原始字符串表示。
    except ValueError:

        # 保留原始字符串可以避免在错误摘要里丢失定位信息。
        return str(path_target)

# project_artifact_path 负责把相对工件路径锚定到 validate 当前工作根。
def project_artifact_path(
    path_target: str | Path,
    workspace_context: WorkspaceGateContext,
) -> Path:
    """
    把相对工件路径锚定到当前 validate 工作根。

    :param path_target: 需要解析的字符串或 Path 形式工件路径。
    :param workspace_context: 工作区清理与路径映射依赖的上下文。
    :return: 返回绝对工件路径；绝对输入保持原样，相对输入锚定到工作根。
    """

    # 先把输入统一转换成 Path 对象，便于后续判断是否已经绝对化。
    path_candidate = Path(path_target)  # 待解析的工件路径

    # 绝对路径通常来自显式输入，当前 helper 不再额外叠加工作根。
    if path_candidate.is_absolute():

        # 绝对路径保持原样返回，避免改变调用方显式给出的目标位置。
        return path_candidate

    # 相对路径需要锚定到 validate 当前工作根，避免安装态拼错父目录。
    return workspace_context.path_workspace_root / path_candidate
