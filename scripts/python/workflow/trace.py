"""分阶段生成工作流的 JSONL trace 记录、读取和路径脱敏工具。"""

# 未来注解避免运行期解析联合类型，保持模块导入轻量。
from __future__ import annotations

# 标准库：写 JSONL、生成 UTC 时间戳，并处理相对路径展示。
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# workflow 阶段调用该函数追加一条结构化 trace。
def append_trace_event(trace_path: Path | None, event: dict[str, Any], *, cwd: Path | None = None) -> None:
    """
    向 JSONL trace 文件追加一条带 UTC 时间戳的事件。

    :param trace_path: trace 文件路径；为 None 时函数不产生文件副作用。
    :param event: 待写入的事件字典，内部 Path 会按 cwd 脱敏为相对路径。
    :param cwd: 可选路径脱敏根目录；为空时使用当前工作目录。
    :return: 无返回值；启用 trace 时会创建父目录并追加一行 JSON。
    """

    # trace 未启用时直接返回，避免调用方到处写条件分支。
    if trace_path is None:

        # 不写文件也不创建目录，保持无 trace 配置的零副作用语义。
        return

    # 路径脱敏根目录默认使用当前工作目录，确保 trace 不记录本机绝对路径。
    path_root = (cwd or Path.cwd()).resolve()  # trace 路径脱敏根目录

    # trace 记录分步组装，避免事件载荷掩盖时间戳字段的审计职责。
    dict_record: dict[str, Any] = {}  # 汇总本次 workflow 阶段 trace 的最终 JSON 字典

    # 时间戳必须由 trace 层生成，保证所有事件使用同一种 UTC 格式。
    dict_record["timestamp"] = datetime.now(timezone.utc).isoformat()  # 标记该阶段事件发生时间的 UTC 字符串

    # 事件载荷写入前先递归脱敏 Path，避免 trace 泄漏本机绝对路径。
    dict_sanitized_event = dict(_sanitize_value(event, path_root))  # 已脱敏事件载荷

    # 脱敏后的事件字段合并进同一行 JSONL 记录。
    dict_record.update(dict_sanitized_event)

    # 写入前创建父目录，让调用方只需指定目标 trace 文件。
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    # 使用追加模式写单行 JSON，便于长流程逐步恢复和审计。
    with trace_path.open("a", encoding="utf-8") as file_handle:

        # 每个事件单独一行，sort_keys 保持 diff 和测试输出稳定。
        file_handle.write(json.dumps(dict_record, ensure_ascii=False, sort_keys=True) + "\n")

# 测试和评估工具通过该函数读取 JSONL trace。
def read_trace(trace_path: Path) -> list[dict[str, Any]]:
    """
    读取 JSONL trace 文件并返回事件列表。

    :param trace_path: trace JSONL 文件路径。
    :return: 按文件顺序解析出的事件字典列表；文件不存在时返回空列表。
    :raises json.JSONDecodeError: 当非空行不是合法 JSON 时由解析器抛出。
    """

    # 事件列表保留文件中的顺序，方便测试按流程阶段断言。
    list_events: list[dict[str, Any]] = []  # 已解析 trace 事件

    # trace 文件不存在代表流程未写事件，读取方按空列表处理。
    if not trace_path.exists():

        # 返回空事件列表，保持调用方无需捕获 FileNotFoundError。
        return list_events

    # 逐行读取 JSONL，允许文件中存在空白行。
    for str_line in trace_path.read_text(encoding="utf-8").splitlines():

        # 空行不代表事件，跳过以兼容手工查看或拼接后的文件。
        if not str_line.strip():

            # 继续读取后续 trace 行。
            continue

        # 非空行必须是 JSON object，解析失败时暴露原始 JSON 错误。
        list_events.append(json.loads(str_line))

    # 返回解析后的事件列表。
    return list_events

# trace 事件中经常需要压缩记录规格关键信息。
def spec_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """
    从完整规格中提取适合写入 trace 的摘要字段。

    :param spec: 已归一化或部分归一化的规格字典。
    :return: 包含 name、target、subfunctions 和 outputs 的轻量摘要。
    """

    # 子功能摘要只保留字典条目的 name，跳过异常形态以保证 trace 稳定。
    list_subfunctions = [
        item.get("name")  # 子功能名称
        for item in spec.get("subfunctions", [])  # 原始子功能条目
        if isinstance(item, dict)  # 只摘要结构化子功能
    ]  # 子功能名称列表

    # 输出摘要只保留字典条目的 path，避免 trace 写入完整输出配置。
    list_outputs = [
        item.get("path")  # 输出文件路径
        for item in spec.get("outputs", [])  # 原始输出条目
        if isinstance(item, dict)  # 只摘要结构化输出对象
    ]  # 输出路径列表

    # 返回轻量摘要，调用方可直接合并进 trace 事件。
    return {
        "name": spec.get("name"),  # 规格名称
        "target": spec.get("target"),  # 生成目标
        "subfunctions": list_subfunctions,  # trace 摘要中的子功能名称集合
        "outputs": list_outputs,  # trace 摘要中的输出路径集合
    }

# trace 和状态文件使用该函数避免写出本机绝对路径。
def safe_path(path: Path | str, root: Path | None = None) -> str:
    """
    将路径转换为相对 root 的 POSIX 字符串，越界时只保留文件名。

    :param path: 待展示的路径对象或字符串。
    :param root: 可选脱敏根目录；为空时使用当前工作目录。
    :return: root 内路径的 POSIX 相对形式，或 ``<external>/<name>``。
    """

    # 脱敏基准目录用于判断路径是否仍在当前工作区内。
    path_base = (root or Path.cwd()).resolve()  # 脱敏基准目录

    # 输入统一转换成 Path，兼容调用方传入字符串。
    path_candidate = Path(path)  # 待脱敏路径

    # 尽量解析符号链接和相对段；解析失败时退回绝对路径。
    try:

        # resolve 会尽力归一化路径，便于 relative_to 做工作区判断。
        path_resolved = path_candidate.resolve()  # 归一化候选路径

    # 某些平台或坏路径可能触发 OSError，trace 仍应尽量可写。
    except OSError:

        # absolute 保留可展示的绝对形式，后续仍会按 root 脱敏。
        path_resolved = path_candidate.absolute()  # 回退绝对路径

    # 工作区内路径写成相对 POSIX 形式，避免泄漏本机根目录。
    try:

        # relative_to 成功说明路径位于脱敏根目录下。
        return path_resolved.relative_to(path_base).as_posix()

    # 越过 root 的外部路径只暴露文件名，保留足够的人类定位信息。
    except ValueError:

        # 返回固定外部前缀，避免 trace 泄漏用户私有目录。
        return f"<external>/{path_candidate.name}"

# JSON-like 事件值递归清洗，Path 会转换为安全展示字符串。
def _sanitize_value(value: Any, root: Path) -> Any:
    """
    递归清洗 trace 事件中的路径值。

    :param value: 待写入 JSONL trace 的任意事件字段值。
    :param root: 路径脱敏使用的工作区根目录。
    :return: 可安全传给 JSON 编码器的清洗后值。
    """

    # Path 对象必须在写 trace 前脱敏。
    if isinstance(value, Path):

        # 返回相对 root 或外部文件名形式。
        return safe_path(value, root)

    # 字典值递归清洗，保留原 key 不改 wire shape。
    if isinstance(value, dict):

        # 返回键不变、值已清洗的新字典。
        return {
            key: _sanitize_value(item, root)  # 递归清洗后的字典值
            for key, item in value.items()
        }

    # 列表值递归清洗，保持原顺序。
    if isinstance(value, list):

        # 返回元素已清洗的新列表。
        return [_sanitize_value(item, root) for item in value]

    # 元组在 JSON 中按列表写出，保持旧行为。
    if isinstance(value, tuple):

        # 元组字段转成 JSON array 前也要逐个脱敏。
        return [_sanitize_value(item, root) for item in value]

    # 标量值不需要清洗，原样交给 JSON 编码器。
    return value
