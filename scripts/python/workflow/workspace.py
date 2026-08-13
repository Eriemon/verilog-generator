"""工作区路径安全与工作流状态索引工具。"""

# 延迟解析类型标注，避免运行时为前向标注引入额外依赖。
from __future__ import annotations

# 标准库依赖覆盖 JSON 状态文件、上下文根目录覆盖和跨平台路径解析。
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator

# 规格错误类型用于向上层报告可读的路径与状态文件问题。
from .spec import SpecError

# 默认状态文件名保持已有 wire path。
DEFAULT_STATE_PATH = Path("workflow-state.json")  # 工作流状态默认写入文件

# 参考输入目录不能被生成流程覆盖。
PROTECTED_WRITE_DIRS = {"Spec2RTL-Agent"}  # 写入保护目录名集合

# ContextVar 允许测试或嵌套工作流临时改写根目录。
context_var_workspace_root_override: ContextVar[Path | None] = ContextVar(  # 临时根目录覆盖槽
    "verilog_generator_workspace_root",  # 跨上下文共享的覆盖槽名称
    default=None,  # 未覆盖时回退到当前进程目录
)

# 根目录探测只依赖仓库标记和治理入口文件。
WORKSPACE_ROOT_MARKERS = (".git", "AGENTS.md")  # 可识别工作区根的标记文件

# 当前根目录解析入口
def workspace_root() -> Path:
    """
    返回当前工作流使用的工作区根目录。

    :param: 无参数；读取当前上下文覆盖或进程目录。
    :return: 已解析的绝对路径；有上下文覆盖时使用覆盖值，否则使用当前进程目录。
    """

    # 读取当前上下文中的临时根目录覆盖。
    path_override = context_var_workspace_root_override.get()  # 上下文覆盖根目录

    # 覆盖值存在时，路径安全边界必须跟随调用方指定的根目录。
    if path_override is not None:

        # 返回规范化后的覆盖目录，避免相对路径污染后续边界检查。
        return path_override.resolve()

    # 没有覆盖时，当前进程目录就是本轮工作流的根目录边界。
    return Path.cwd().resolve()

# 工作区根目录发现入口
def find_workspace_root(start: Path | None = None) -> Path | None:
    """
    从给定路径向上查找仓库或治理根目录。

    :param start: 起始路径；为文件时从父目录开始，为空时从当前目录开始。
    :return: 命中的工作区根目录；未找到 `.git` 或 `AGENTS.md` 时返回 `None`。
    """

    # 起始路径先规范化，确保后续 parent 遍历稳定。
    path_base = Path(start or Path.cwd()).resolve()  # 根目录搜索起点

    # 文件路径从父目录搜索，目录路径从自身搜索。
    path_search_start = path_base if path_base.is_dir() else path_base.parent  # 首个候选目录

    # 逐级向上查找治理或 Git 标记。
    for path_candidate in (path_search_start, *path_search_start.parents):

        # 任一标记命中即可把该目录视为工作区根。
        if any((path_candidate / str_marker).exists() for str_marker in WORKSPACE_ROOT_MARKERS):

            # 返回规范化目录，供调用方记录或作为边界使用。
            return path_candidate.resolve()

    # 没有任何标记命中时，交给调用方决定是否报错。
    return None

# 工作区根目录强制解析入口
def require_workspace_root(*, purpose: str = "project-local state", start: Path | None = None) -> Path:
    """
    查找工作区根目录，找不到时抛出面向用户的规格错误。

    :param purpose: 错误消息中的业务用途，用于说明本次解析服务的状态或产物。
    :param start: 起始路径；为空时从当前目录开始。
    :return: 已解析的工作区根目录。
    :raises SpecError: 当前目录树中没有 `.git` 或 `AGENTS.md` 标记。
    """

    # 复用宽松查找函数，保留找不到时返回 None 的语义。
    path_root = find_workspace_root(start)  # 可选工作区根目录

    # 缺少根目录会破坏本地状态文件和相对产物路径边界。
    if path_root is None:

        # 报错文本保持原有说明重点，提醒调用方从项目根运行或传入覆盖。
        raise SpecError(
            f"> ERR: [Python] Could not locate a workspace root for {purpose}. "
            "Run this command from a project root containing .git or AGENTS.md, or pass an explicit path override."
        )

    # 返回确定存在的根目录，供配置和远程验证入口复用。
    return path_root

# 临时根目录覆盖上下文
@contextmanager

# 该函数作为 contextmanager 暴露临时工作区根目录。
def use_workspace_root(root: Path) -> Iterator[Path]:
    """
    在 `with` 作用域内临时指定工作区根目录。

    :param root: 调用方确认的工作区根路径。
    :return: 迭代器产出规范化后的根目录，并在退出时恢复之前的覆盖值。
    """

    # 上下文入口先解析根目录，避免后续调用重复处理相对路径。
    path_resolved_root = Path(root).resolve()  # 上下文内使用的绝对工作区根目录

    # ContextVar token 用于 finally 中精确恢复旧值。
    context_token = context_var_workspace_root_override.set(path_resolved_root)  # 覆盖恢复令牌

    # 让调用方在临时根目录边界内执行路径解析。
    try:

        # 产出规范化根目录，便于调用方同步记录运行目录。
        yield path_resolved_root

    # 无论作用域如何退出，都必须撤销 ContextVar 覆盖。
    finally:

        # 退出上下文时恢复原覆盖值，避免影响外层工作流。
        context_var_workspace_root_override.reset(context_token)

# 工作区内部路径校验入口
def require_workspace_path(path: Path, *, purpose: str = "path", must_exist: bool = False) -> Path:
    """
    解析并校验路径必须留在当前工作区根目录内。

    :param path: 需要解析的绝对或相对路径。
    :param purpose: 错误消息中的路径用途。
    :param must_exist: 为 `True` 时要求路径已经存在。
    :return: 已解析且仍位于工作区内的路径。
    :raises SpecError: 路径不存在、无法解析或逃出当前工作区。
    """

    # 当前工作区根决定所有相对路径和越界检查的边界。
    path_root = workspace_root()  # 当前工作区根目录

    # 相对路径以工作区根为锚点，绝对路径保留原始位置再做边界检查。
    path_candidate = path if path.is_absolute() else path_root / path  # 待解析路径

    # 严格解析可选存在性检查，并把系统异常转换为领域错误。
    try:

        # 解析符号链接和相对片段，得到最终安全检查对象。
        path_resolved = path_candidate.resolve(strict=must_exist)  # 规范化后的候选路径

    # 缺失路径需要转换为领域级规格错误。
    except FileNotFoundError:

        # 缺失路径在 must_exist 场景下说明调用方输入无效。
        raise SpecError(f"> ERR: [Python] path missing: {purpose} does not exist: {path}") from None

    # 平台路径解析失败时保留底层异常文本。
    except OSError as exc:

        # 解析失败通常来自非法路径或平台文件系统错误。
        raise SpecError(f"> ERR: [Python] path resolve failed: could not resolve {purpose}: {path}: {exc}") from exc

    # 通过 relative_to 确认解析结果没有逃出工作区根目录。
    try:

        # 仅验证相对关系；返回值不参与后续 wire shape。
        path_resolved.relative_to(path_root)

    # 越界路径必须以工作区安全错误返回。
    except ValueError as exc:

        # 越界路径会让生成流程写到仓库外，必须阻断。
        raise SpecError(
            f"> ERR: [Python] path escaped workspace: {purpose} must stay inside the current workspace: {path}"
        ) from exc

    # 返回规范化路径，供读写封装继续使用。
    return path_resolved

# 锚点相对路径校验入口
def require_workspace_path_from(
    anchor: Path,
    path: Path,
    *,
    purpose: str = "path",
    must_exist: bool = False,
) -> Path:
    """
    以某个文件或目录为锚点解析路径，并最终限制在工作区内。

    :param anchor: 相对路径的参考文件或目录。
    :param path: 需要解析的绝对或相对路径。
    :param purpose: 错误消息中的路径用途。
    :param must_exist: 为 `True` 时允许沿锚点父链寻找已存在的相对路径。
    :return: 已解析且位于当前工作区内的路径。
    :raises SpecError: 最终路径不存在、无法解析或逃出当前工作区。
    """

    # 文件锚点使用父目录，目录锚点使用自身。
    path_base = anchor if anchor.is_dir() else anchor.parent  # 相对路径解析锚点

    # 绝对路径不受锚点影响，但仍会进入统一的工作区边界检查。
    if path.is_absolute():

        # 保留绝对路径原值，避免错误拼接到锚点目录。
        path_candidate = path  # 待校验绝对路径

    # 相对路径先按锚点目录解释，必要时再沿父链查找。
    else:

        # 默认先按锚点目录解析相对路径。
        path_candidate = path_base / path  # 待校验相对路径

        # 必须存在但锚点下未命中时，向上查找同名相对产物。
        if must_exist and not path_candidate.exists():

            # 父链搜索必须停在本轮工作流的根目录内。
            path_root = workspace_root()  # 父级查找使用的工作区边界

            # 锚点父链提供历史产物和配置文件的兼容查找范围。
            list_search_roots = [path_base, *path_base.parents]  # 可搜索目录列表

            # 沿锚点父链寻找第一个工作区内已存在的目标。
            for path_search_root in list_search_roots:

                # 跳过已经逃出工作区的父级目录。
                try:

                    # 解析搜索目录，确保父链没有越过工作区边界。
                    path_search_root.resolve().relative_to(path_root)

                # 父目录已经越界时停止使用它作为搜索来源。
                except ValueError:

                    # 工作区外目录不能作为相对产物查找来源。
                    continue

                # 在当前父级目录下组合用户请求的相对路径。
                path_resolved_candidate = path_search_root / path  # 父级查找候选路径

                # 命中已存在路径时，使用该路径进入最终安全校验。
                if path_resolved_candidate.exists():

                    # 记录第一个命中的候选，保持原有父链优先级。
                    path_candidate = path_resolved_candidate  # 父链中第一个存在路径

                    # 已命中的最近候选应终止父链查找。
                    break

    # 最终统一执行工作区边界、存在性和符号链接解析。
    return require_workspace_path(path_candidate, purpose=purpose, must_exist=must_exist)

# 工作区写路径校验入口
def require_write_path(path: Path, *, purpose: str = "output path") -> Path:
    """
    校验写入路径必须位于工作区内且不指向受保护参考目录。

    :param path: 调用方准备写入的路径。
    :param purpose: 错误消息中的写入用途。
    :return: 已解析且允许写入的路径。
    :raises SpecError: 路径越界或目标位于受保护目录。
    """

    # 写入路径不要求预先存在，但必须先通过工作区边界检查。
    path_resolved = require_workspace_path(path, purpose=purpose, must_exist=False)  # 允许写入的候选路径

    # 阻止生成流程覆盖仓库内的参考输入目录。
    _reject_protected_write(path_resolved, purpose)

    # 返回已经完成边界和保护目录检查的写入路径。
    return path_resolved

# 相对产物路径校验入口
def require_relative_artifact_path(path: str, *, purpose: str = "artifact path") -> str:
    """
    校验写入 manifest 的产物路径必须是安全的 POSIX 相对路径。

    :param path: JSON 或 manifest 中记录的产物路径字符串。
    :param purpose: 错误消息中的产物路径用途。
    :return: 原始路径字符串；调用方依赖该 wire shape 写入 manifest。
    :raises SpecError: 路径包含反斜杠、绝对路径、盘符或不安全路径段。
    """

    # manifest 路径统一使用正斜杠，避免跨平台解析歧义。
    if "\\" in path:

        # 反斜杠会让 Windows 与 POSIX 对同一字段产生不同解释。
        raise SpecError(
            f"> ERR: [Python] artifact path separator invalid: {purpose} must use forward slashes: {path!r}"
        )

    # POSIX 视图用于检查分段和绝对路径。
    path_posix = PurePosixPath(path)  # POSIX 产物路径视图

    # Windows 解析视图专门拦截盘符和反斜杠平台根。
    path_windows = PureWindowsPath(path)  # Windows 盘符和绝对路径检查视图

    # 产物 manifest 只允许相对路径，不能携带根目录或盘符。
    if path_posix.is_absolute() or path_windows.is_absolute() or path_windows.drive:

        # 绝对路径会泄漏本机目录结构并破坏可移植性。
        raise SpecError(f"> ERR: [Python] artifact path absolute: {purpose} must be relative: {path!r}")

    # 空段、当前目录和父目录片段都会削弱 manifest 的安全边界。
    if any(str_part in ("", ".", "..") for str_part in path_posix.parts):

        # 不安全片段可能让下游解包或复制产物时逃出目标目录。
        raise SpecError(
            f"> ERR: [Python] artifact path unsafe segment: {purpose} contains unsafe segment: {path!r}"
        )

    # manifest 首段不能指向受保护参考输入目录。
    if path_posix.parts and path_posix.parts[0] in PROTECTED_WRITE_DIRS:

        # 参考目录只读，不能作为生成产物命名空间。
        raise SpecError(
            f"> ERR: [Python] artifact path protected: {purpose} must not target "
            f"protected reference directories: {path!r}"
        )

    # 返回原字符串，保持调用方写入 JSON 的字段内容不变。
    return path

# UTF-8 文本写入封装
def write_text(path: Path, text: str) -> Path:
    """
    将文本写入经过工作区保护的路径。

    :param path: 输出文件路径，可为相对路径或工作区内绝对路径。
    :param text: 写入内容，按 UTF-8 编码落盘。
    :return: 实际写入的规范化路径。
    :raises SpecError: 输出路径越界或命中受保护目录。
    """

    # 写入前先解析路径，保证父目录创建不会发生在工作区外。
    path_output = require_write_path(path)  # 通过保护规则的输出路径

    # 创建输出父目录，使上层调用无需重复处理目录存在性。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 统一使用 UTF-8，保持生成产物跨平台可读。
    path_output.write_text(text, encoding="utf-8")

    # 返回实际输出路径，便于 manifest 或日志记录。
    return path_output

# JSON 写入封装
def write_json(path: Path, data: dict[str, Any]) -> Path:
    """
    将字典序列化为缩进 JSON 后写入工作区内文件。

    :param path: 输出 JSON 文件路径。
    :param data: 可被 `json.dumps` 序列化的映射内容。
    :return: 实际写入的规范化路径。
    :raises SpecError: 输出路径越界或命中受保护目录。
    :raises TypeError: `data` 中存在 JSON 不支持的对象。
    """

    # JSON 文本保持既有缩进、中文保留和末尾换行格式。
    str_json_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"  # 待写入 JSON 文本

    # 复用文本写入封装，保持路径安全策略一致。
    return write_text(path, str_json_text)

# 工作流状态追加入口
def update_workflow_state(
    state_path: Path | None,
    event: str,
    payload: dict[str, Any],
    *,
    enabled: bool = True,
) -> None:
    """
    向工作流状态 JSON 追加事件，并按事件类型维护索引桶。

    :param state_path: 状态文件路径；为空时写入 `workflow-state.json`。
    :param event: 事件名称，决定记录是否同步进入 plans、validation_reports 等索引桶。
    :param payload: JSON-like 映射；shape 为一层事件载荷映射，可嵌套 dict/list/tuple/Path。
    :param enabled: 为 `False` 时跳过所有文件 IO。
    :return: 无返回值；副作用是更新状态 JSON 文件。
    :raises SpecError: 状态路径越界、状态 JSON 无效或状态根不是对象。

    payload dtype 约束为 JSON 可序列化对象与 `Path`；`Path` 会转换为工作区相对字符串。
    payload unit 不适用；本函数处理的是离散工作流事件，不涉及数值单位。
    """

    # 禁用状态记录时，调用方只需要跳过文件 IO。
    if not enabled:

        # 直接退出，保持原有无副作用语义。
        return

    # 状态路径为空时使用默认文件名，并统一经过写入保护。
    path_state = require_write_path(state_path or DEFAULT_STATE_PATH, purpose="workflow state path")  # 状态文件路径

    # 读取或初始化状态对象，保留既有索引桶。
    dict_state = _read_state(path_state)  # 工作流状态对象

    # 单条事件记录包含 UTC 时间、事件名和清洗后的载荷。
    dict_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),  # 事件发生时间
        "event": event,  # 工作流事件名称
        **_sanitize(payload),  # 可写入 JSON 的事件载荷
    }

    # 主事件流保留完整记录，供诊断按时间顺序回放。
    dict_state.setdefault("events", []).append(dict_record)

    # 同步维护按事件类型划分的索引桶。
    _index_payload(dict_state, event, dict_record)

    # 状态目录可能是新的运行目录，写入前需要确保存在。
    path_state.parent.mkdir(parents=True, exist_ok=True)

    # 状态 JSON 保持排序键和末尾换行，便于治理 diff。
    path_state.write_text(
        json.dumps(dict_state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

# 状态文件读取辅助函数
def _read_state(path: Path) -> dict[str, Any]:
    """读取已有工作流状态；缺失文件时创建默认状态结构。

    :param path: 工作流状态 JSON 文件路径。
    :return: 可继续追加事件的状态对象。
    :raises SpecError: 状态文件不是合法 JSON 或顶层不是对象。
    """

    # 状态文件缺失代表当前工作流尚未写入任何事件。
    if not path.exists():

        # 默认状态结构必须保留所有既有 JSON 顶层字段。
        return {
            "version": 1,  # 状态文件格式版本
            "evidence": [],  # 证据记录索引
            "summaries": [],  # 摘要记录索引
            "plans": [],  # 计划记录索引
            "artifact_manifests": [],  # 产物清单索引
            "validation_reports": [],  # 验证报告索引
            "traces": [],  # 跟踪记录索引
            "prompt_memory": [],  # 提示词记忆索引
            "human_interventions": [],  # 人工介入记录索引
            "events": [],  # 完整事件流
        }

    # 读取 JSON 时保留原始解析异常上下文。
    try:

        # 状态文件只接受 UTF-8 JSON 文本。
        dict_loaded_state = json.loads(path.read_text(encoding="utf-8"))  # 已解析状态对象

    # JSON 语法错误需要保留原解析异常以便定位。
    except json.JSONDecodeError as exc:

        # 无效 JSON 会让后续追加破坏状态文件，必须立即阻断。
        raise SpecError(f"> ERR: [Python] Invalid workflow state JSON in {path}: {exc}") from exc

    # 顶层必须是对象，才能维护固定索引桶。
    if not isinstance(dict_loaded_state, dict):

        # 非对象状态无法合并事件记录。
        raise SpecError(f"> ERR: [Python] Workflow state must be a JSON object: {path}")

    # 旧状态文件可能缺少版本字段，读取时补齐默认版本。
    dict_loaded_state.setdefault("version", 1)

    # _read_state 逐项补齐这些事件桶，保证旧状态文件恢复后仍可追加记录。
    tuple_state_keys = (  # workflow-state.json 需要维持的顶层事件桶名称序列
        "evidence",  # ingest_spec 事件追加到这里供证据回放使用
        "summaries",  # summary 记录保留在这里供人读交接使用
        "plans",  # decompose 和 workflow 事件追加到这里供计划恢复使用
        "artifact_manifests",  # 生成与提取事件追加到这里供产物追踪使用
        "validation_reports",  # validate 和 eval 事件追加到这里供验证结果汇总使用
        "traces",  # trace 子对象追加到这里供工作流轨迹查看使用
        "prompt_memory",  # optimize_prompt 事件追加到这里供提示词演化使用
        "human_interventions",  # 人工介入事件追加到这里供决策闭环追踪使用
        "events",  # 所有事件追加到这里供时间顺序回放使用
    )

    # 逐项补齐缺失索引桶，避免旧状态文件破坏后续 append。
    for str_state_key in tuple_state_keys:

        # 缺失桶补空列表，已有桶不做类型或内容迁移。
        dict_loaded_state.setdefault(str_state_key, [])

    # 返回可继续追加事件的状态对象。
    return dict_loaded_state

# 事件索引桶维护辅助函数
def _index_payload(state: dict[str, Any], event: str, record: dict[str, Any]) -> None:
    """按照事件名把记录同步放入状态文件的索引桶。

    :param state: 可变的工作流状态对象。
    :param event: 当前记录所属的工作流事件名。
    :param record: 已清洗并带时间戳的事件记录。
    :return: 无返回值；副作用是更新 state 中的分类索引。
    """

    # 事件到索引桶的映射保持状态 JSON 的既有分类语义。
    dict_event_buckets = {
        "ingest_spec": "evidence",  # 规格摄取产生证据记录
        "decompose": "plans",  # 分解步骤产生计划记录
        "prompt": "artifact_manifests",  # 提示词阶段产生产物清单
        "model_generate": "artifact_manifests",  # 模型生成阶段产生产物清单
        "extract": "artifact_manifests",  # 提取阶段产生产物清单
        "validate": "validation_reports",  # 验证阶段产生验证报告
        "reflect": "plans",  # 反思阶段产生计划更新
        "optimize_prompt": "prompt_memory",  # 提示词优化进入提示词记忆
        "eval": "validation_reports",  # 单点评测产生验证报告
        "eval_suite": "validation_reports",  # 评测套件产生验证报告
        "human_intervention": "human_interventions",  # 人工介入进入人工索引
        "resolve_intervention": "human_interventions",  # 人工介入闭环进入人工索引
        "audit_interface": "artifact_manifests",  # 接口审计产生产物清单
        "audit_semantic_model": "artifact_manifests",  # 参考审计产生产物清单
        "verify_stage": "validation_reports",  # 分阶段验证产生验证报告
        "run_workflow": "plans",  # 工作流启动进入计划索引
        "resume_workflow": "plans",  # 工作流恢复进入计划索引
        "workflow_attempt": "validation_reports",  # 单次尝试进入验证报告
        "analyze_existing": "plans",  # 既有 RTL 分析进入计划索引
        "improve_existing": "plans",  # 既有 RTL 改进进入计划索引
        "compare_semantics": "validation_reports",  # 语义比较进入验证报告
        "verify_existing": "validation_reports",  # 既有 RTL 验证进入验证报告
    }

    # 未映射事件仍保留在 events 主流中，不强行放入索引桶。
    str_bucket = dict_event_buckets.get(event)  # 当前事件对应的索引桶

    # 已知事件需要同步进入对应索引，便于报告按类别读取。
    if str_bucket:

        # 将同一条记录追加到分类桶，保持与主事件流一致的内容。
        state.setdefault(str_bucket, []).append(record)

    # trace 字段单独汇总，方便查看完整工作流轨迹。
    if record.get("trace"):

        # trace 索引只保存 trace 子对象，保持历史字段结构。
        state.setdefault("traces", []).append(record["trace"])

# 受保护目录写入拒绝辅助函数
def _reject_protected_write(path: Path, purpose: str) -> None:
    """阻止写入路径落在工作区内的受保护参考目录。

    :param path: 已解析并准备写入的工作区内路径。
    :param purpose: 错误消息中的写入用途。
    :return: 无返回值；路径命中保护目录时抛错。
    :raises SpecError: 写入目标落在受保护参考目录。
    """

    # 保护目录判断使用本轮生成任务的根目录作为相对基准。
    path_root = workspace_root()  # 写入保护检查使用的工作区根目录

    # 工作区内路径使用相对分段，越界路径保留自身分段用于兜底检查。
    try:

        # 提取相对工作区的路径片段，便于检查首级目录。
        tuple_parts = path.relative_to(path_root).parts  # 工作区相对路径片段

    # 兜底处理理论上已经被上游挡住的越界路径。
    except ValueError:

        # 已解析路径理论上应在工作区内，兜底分支保留原路径片段。
        tuple_parts = path.parts  # 路径自身片段

    # 首级目录命中保护名单时，拒绝生成流程写入。
    if tuple_parts and tuple_parts[0] in PROTECTED_WRITE_DIRS:

        # 保护目录通常承载参考输入，不允许被运行产物覆盖。
        raise SpecError(
            f"> ERR: [Python] write target protected: {purpose} must not write into "
            f"protected reference directory {tuple_parts[0]!r}."
        )

# JSON 载荷清洗辅助函数
def _sanitize(value: Any) -> Any:
    """把工作流事件载荷转换为 JSON 可写入对象。

    :param value: 事件载荷中的任意 JSON-like 值。
    :return: 可被 json.dumps 写入的清洗后对象。
    """

    # Path 需要改写为稳定的工作区相对字符串。
    if isinstance(value, Path):

        # 使用安全路径表示，避免把本机绝对路径写入状态文件。
        return _safe_path(value)

    # 字典递归清洗值，保留原有键名作为 wire shape。
    if isinstance(value, dict):

        # 返回新的字典，避免原始 payload 被就地修改。
        return {key: _sanitize(item) for key, item in value.items()}

    # 列表递归清洗每个元素，保持 JSON 数组结构。
    if isinstance(value, list):

        # 返回新的列表，避免调用方持有的 payload 被修改。
        return [_sanitize(item) for item in value]

    # tuple 在 JSON 中用数组表达，语义上仍是有序集合。
    if isinstance(value, tuple):

        # tuple 转列表后继续递归清洗内部元素。
        return [_sanitize(item) for item in value]

    # 其他基础 JSON 值原样保留。
    return value

# 安全路径序列化辅助函数
def _safe_path(path: Path) -> str:
    """把路径转换为状态 JSON 中可记录的安全字符串。

    :param path: 需要写入状态文件的路径对象。
    :return: 工作区相对 POSIX 路径或外部路径占位文本。
    """

    # 状态文件脱敏时用工作区根判断路径是否可完整记录。
    path_root = workspace_root()  # 状态路径序列化的相对化基准

    # 工作区内路径记录为 POSIX 相对路径，便于跨平台比较。
    try:

        # 解析后相对化，避免状态文件记录符号链接或 `..` 片段。
        return path.resolve().relative_to(path_root).as_posix()

    # 外部路径进入状态文件时只能记录脱敏占位。
    except ValueError:

        # 工作区外路径只保留文件名，避免泄漏本机目录结构。
        return f"<external>/{path.name}"
