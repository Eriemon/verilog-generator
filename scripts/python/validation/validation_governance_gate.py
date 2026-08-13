"""validate_verilog_skill 的远程状态、外部治理与 audit helper。"""

# future annotations 让本模块的类型提示保持前向引用友好。
from __future__ import annotations

# JSON 解析用于读取 effectiveness 报告与混合日志尾部的结构化载荷。
import json

# subprocess 类型标注用于描述 facade 传入的子进程执行器返回值。
import subprocess

# dataclass 负责把治理 helper 共享的路径与回调收束到一个上下文对象里。
from dataclasses import dataclass

# pathlib 用来表达 settings、skill 与治理脚本的路径语义。
from pathlib import Path

# Any 允许 facade 把兼容回调透传进当前 helper。
from typing import Any

# GovernanceGateContext 汇总治理 helper 需要复用的路径、命令与回调。
@dataclass(frozen=True)
class GovernanceGateContext:
    """
    保存治理 helper 需要反复读取的路径与回调。

    :param path_skill_root: 当前 readable-verilog-generator skill 根目录。
    :param path_project_root: 当前仓库根目录。
    :param path_manage_docs_script: agents-md-generator 的 docs 治理脚本路径。
    :param str_python_executable: validate 当前进程使用的 Python 解释器路径。
    :param str_remote_validate_module: 远程验证模块入口名。
    :param func_run: facade 透传的子进程执行函数。
    :param func_cleanup_residuals: facade 透传的常规残留清理函数。
    :param func_cleanup_audit_retry_local_artifacts: facade 透传的 audit 重试局部清理函数。
    :param func_validation_workspace_root: facade 透传的 validate 工作目录解析函数。
    :return: 不返回业务值；实例化完成即表示治理上下文已可供 helper 复用。
    """

    # path_skill_root 指向当前 skill 主体目录，供 audit 命令直接复用。
    path_skill_root: Path  # 当前 skill 根目录

    # path_project_root 指向仓库根目录，供外部治理脚本固定 cwd 使用。
    path_project_root: Path  # 当前仓库根目录

    # path_manage_docs_script 记录 agents-md-generator 的 docs 治理入口。
    path_manage_docs_script: Path  # docs 治理脚本路径

    # str_python_executable 保留 facade 当前解释器，避免子进程环境漂移。
    str_python_executable: str  # 当前 Python 解释器路径

    # str_remote_validate_module 指向远程验证模块入口名。
    str_remote_validate_module: str  # 远程验证模块名

    # func_run 是 facade 透传进来的子进程执行回调。
    func_run: Any  # 子进程执行函数

    # func_cleanup_residuals 负责常规 validate 残留清理。
    func_cleanup_residuals: Any  # 常规残留清理回调

    # func_cleanup_audit_retry_local_artifacts 负责 audit 重试前的局部清理。
    func_cleanup_audit_retry_local_artifacts: Any  # audit 重试局部清理回调

    # func_validation_workspace_root 用于解析 validate 子进程应当使用的工作目录。
    func_validation_workspace_root: Any  # validate 工作目录解析回调

# build_remote_validation_command 维持 validate facade 公开的远程命令拼装顺序。
def build_remote_validation_command(
    settings_path: Path,
    remote_server: str | None,
    governance_gate_context: GovernanceGateContext,
    *,
    report_runs: bool = False,
    run_id: str | None = None,
) -> list[str]:
    """
    组装远程验证脚本命令。

    :param settings_path: 当前 validate 使用的 settings 文件路径。
    :param remote_server: 本次运行显式指定的服务器标识；为空时不额外透传。
    :param governance_gate_context: 远程验证命令拼装所依赖的治理上下文。
    :param report_runs: 是否切换到 report-runs 只读报告模式。
    :param run_id: report-runs 需要精确读取的 outer retained run 标识。
    :return: 返回可直接交给 subprocess 的 argv 列表。
    """

    # 先写出远程验证始终需要的模块入口和 settings 参数。
    list_command = [  # 远程验证基础命令
        governance_gate_context.str_python_executable,  # 远程验证沿用当前解释器
        "-m",  # 远程验证通过模块入口执行
        governance_gate_context.str_remote_validate_module,  # 当前 skill 远程验证模块
        "--settings",  # 远程验证 settings 参数名
        str(settings_path),  # 当前 validate 使用的 settings 文件路径
    ]

    # 只有调用方显式给出服务器时，才在命令尾部追加覆盖参数。
    if remote_server:

        # 把本次运行覆盖的服务器标识透传给远程验证模块。
        list_command.extend(["--server", remote_server])

    # 只读报告模式需要限制返回最近一次 run，避免枚举整段历史目录。
    if report_runs:

        # report-runs 保持 validate 既有的 `--max-runs 1` 顺序与取值。
        list_command.extend(["--report-runs", "--max-runs", "1"])

        # 调用方提供本轮 run id 时必须精确透传，禁止重新猜测最近目录。
        if run_id:

            # run id 使用独立 argv 项，避免 shell 拼接改变其路径语义。
            list_command.extend(["--run-id", run_id])

    # 把完整 argv 列表返回给 facade，保持 shell 转义语义稳定。
    return list_command

# resolve_remote_server 只暴露 remote_selection 已确认选择里的 server_id。
def resolve_remote_server(settings: dict) -> str | None:
    """
    读取项目已确认远程服务器；未配置时返回 None。

    :param settings: Verilog skill 治理配置字典。
    :return: 返回 server_id 字符串；当没有确认选择时返回 None。
    """

    # 远程选择 helper 只在真正需要服务器选择时才延迟导入。
    from scripts.python.remote.remote_selection import resolve_confirmed_remote_server

    # 先解析 remote-selection.local.json 对应的完整选择载荷。
    dict_selection = resolve_confirmed_remote_server(settings)  # 项目本地远程选择载荷

    # 没有确认选择时直接把“未配置”语义交还给上层 gate。
    if not dict_selection:

        # 用 None 保持 validate 旧逻辑里的“跳过默认远程服务器”语义。
        return None

    # 把唯一需要向外暴露的 server_id 规范成字符串返回。
    return str(dict_selection["server_id"])

# resolve_required_remote_validation_state 负责 require-remote 前的本地状态校验。
def resolve_required_remote_validation_state(
    settings: dict,
    *,
    explicit_server: str | None = None,
) -> dict[str, str | dict]:
    """
    校验远程验证前置状态，并返回最小远程上下文。

    :param settings: Verilog skill 治理配置字典。
    :param explicit_server: 命令行显式指定的远程服务器标识。
    :return: 返回包含 server_id 与 remote_runtime_config 的状态字典。
    :raises AssertionError: 当旧远程状态未迁移、远程选择缺失或连接清单不存在时抛出。
    """

    # remote_setting 和 remote_selection 只在 require-remote 解析时才需要加载。
    from scripts.python.remote.remote_selection import (
        remote_runtime_config_relpath,
        resolve_confirmed_remote_server,
    )
    from scripts.python.workflow.config import remote_setting

    # 先取出 settings loader 注入的远程状态元数据。
    dict_meta = settings.get("__verilog_settings_meta__", {})  # 远程状态元数据

    # legacy_remote_state 表示仍存在旧版远程选择状态文件。
    bool_has_legacy_remote_state = isinstance(dict_meta, dict) and bool(dict_meta.get("legacy_remote_state"))  # 是否检测到旧远程状态

    # local_settings_loaded 表示项目本地 .settings 已覆盖旧远程状态。
    bool_has_project_settings = isinstance(dict_meta, dict) and bool(dict_meta.get("local_settings_loaded"))  # 是否已加载项目本地配置

    # 旧远程状态未迁移时必须先阻断，避免误连到遗留服务器。
    if bool_has_legacy_remote_state and not bool_has_project_settings:

        # 把迁移阻断以统一 ERR 前缀抛给上层 confidence gate。
        raise AssertionError(
            "> ERR: [Python] legacy remote validation state must be migrated before running remote gate."
        )

    # 先把 CLI 显式指定的服务器裁成无首尾空白的候选值。
    str_server_id = (explicit_server or "").strip()  # 当前运行候选服务器标识

    # 未传显式服务器时，需要从项目本地确认选择里补出默认目标。
    if not str_server_id:

        # 读取项目本地 remote-selection.local.json 里的已确认服务器。
        dict_selection = resolve_confirmed_remote_server(settings)  # 项目本地确认选择

        # 显式参数和项目本地确认选择都缺失时，远程 gate 没有安全目标。
        if not dict_selection:

            # 用统一错误文本告知上层必须补齐显式或已确认的服务器来源。
            raise AssertionError(
                "> ERR: [Python] remote validation requires an explicit or confirmed server selection."
            )

        # 把已确认选择里的 server_id 规范成字符串，交给后续命令拼装。
        str_server_id = str(dict_selection["server_id"])  # 已确认服务器标识

    # server_list.local.json 是远程执行前必须存在的本地连接清单。
    path_server_list = Path(remote_setting(settings, "server_list"))  # 本地远程服务器清单路径

    # 缺少连接清单时必须阻断，避免 remote_validate 进入半配置状态。
    if not path_server_list.exists():

        # 把缺少 server_list.local.json 的状态显式暴露给上层 gate。
        raise AssertionError("> ERR: [Python] remote validation requires server_list.local.json.")

    # 最终状态只保留 validate 外层真正需要复用的两个字段。
    return {
        "server_id": str_server_id,
        "remote_runtime_config": remote_runtime_config_relpath(settings),
    }

# run_work_folder_gate 负责桥接 agents-md-generator 的外部治理脚本。
def run_work_folder_gate(
    governance_gate_context: GovernanceGateContext,
    *,
    require_external: bool = True,
) -> dict[str, str]:
    """
    运行 AGENTS 文档治理 gate，并保留既有 advisory 语义。

    :param governance_gate_context: 外部治理命令所依赖的路径与回调上下文。
    :param require_external: 是否要求 manage_docs.py 必须存在。
    :return: 返回 passed、advisory 或 skipped 的治理结果摘要。
    :raises FileNotFoundError: 当严格模式要求 manage_docs.py 必须存在但实际缺失时抛出。
    :raises SystemExit: 当治理脚本失败且不属于允许降级或重试的场景时抛出。
    """

    # 外部治理脚本缺失时，先根据当前调用场景判断是阻断还是跳过。
    if not governance_gate_context.path_manage_docs_script.exists():

        # 严格路径下不允许静默跳过 manage_docs.py。
        if require_external:

            # 用缺脚本错误阻断发布型或严格型 validate 路径。
            raise FileNotFoundError("> ERR: [Python] Missing manage_docs.py gate script.")

        # 开发期允许跳过，但必须在终端里留下可见提示。
        print("> INFO: [Python] optional work-folder gate skipped because manage_docs.py is unavailable.")

        # 把 skipped 摘要交还给 facade，供最终 handoff 记录。
        return {"status": "skipped", "reason": "missing_external_governance"}

    # 先按既有顺序拼出 work-folder-gate 命令。
    list_work_folder_gate_command = [  # 外部 docs 治理命令
        governance_gate_context.str_python_executable,  # 治理脚本沿用当前解释器
        str(governance_gate_context.path_manage_docs_script),  # docs 治理入口脚本
        "work-folder-gate",  # docs 治理 gate 子命令
        ".",  # 当前仓库根作为治理目标
        "--skill-dir",  # skill 目录参数名
        "skills/readable-verilog-generator",  # 当前 skill 相对仓库根目录
        "--mode",  # 治理模式参数名
        "development",  # 开发期允许 dirty worktree advisory
    ]

    # 先缓存外部治理脚本需要固定使用的仓库根 cwd。
    path_governance_cwd = governance_gate_context.path_project_root  # 外部治理命令工作目录

    # 再执行首次外部治理命令，并把非零退出码留给当前 helper 继续解释。
    completed_process_result = governance_gate_context.func_run(  # 首次 work-folder-gate 结果
        list_work_folder_gate_command,  # 首次外部治理命令参数
        cwd=path_governance_cwd,  # 首次外部治理工作目录
        allow_failure=True,  # 首次外部治理允许 helper 自行解释非零退出
    )

    # 零退出码表示 AGENTS、目录与分支治理前置条件都已通过。
    if completed_process_result.returncode == 0:

        # 把通过态压成 validate 既有的 passed 摘要。
        return {"status": "passed", "reason": "external_governance_ok"}

    # 先合并 stdout 和 stderr，覆盖“stdout 有 JSON、stderr 有 traceback”的混合失败。
    str_combined_output = f"{completed_process_result.stdout}\n{completed_process_result.stderr}"  # 首次治理失败的完整诊断文本

    # 只有 validate 可自清理的瞬时产物缺失，才值得执行一次补偿重试。
    if _is_transient_work_folder_gate_failure(str_combined_output):

        # 复用同一 cwd 再跑一次治理命令，确认首次失败是否只是瞬态竞态。
        completed_process_retry = governance_gate_context.func_run(  # 补偿重试结果
            list_work_folder_gate_command,  # 补偿重试沿用的治理命令参数
            cwd=path_governance_cwd,  # 补偿重试沿用的仓库根工作目录
            allow_failure=True,  # 补偿重试仍由 helper 解释非零退出
        )

        # 重试成功时直接恢复到 passed，说明首次失败只是短暂竞态。
        if completed_process_retry.returncode == 0:

            # 把补偿重试后的真实通过状态交还给 facade。
            return {"status": "passed", "reason": "external_governance_ok"}

        # 重试仍失败时，后续判断统一改用最新诊断。
        completed_process_result = completed_process_retry  # 重试后继续沿用的最终治理结果

    # 尝试从最终 stdout 里提取结构化 JSON 诊断，兼容 manage_docs 既有输出。
    try:

        # 把最后一次治理输出里的 JSON 载荷解析出来，供 advisory 判定复用。
        dict_payload = parse_json_object(completed_process_result.stdout)  # 治理 JSON 诊断

    # 非 JSON 输出说明治理脚本没有给出可解析摘要，只能透传退出码。
    except ValueError:

        # 用 SystemExit 保持 validate 对外部治理失败的旧退出语义。
        raise SystemExit(completed_process_result.returncode) from None

    # 只有 dirty worktree 的 branch-gate 失败才允许降级为 advisory。
    if _is_advisory_work_folder_gate_failure(dict_payload):

        # 开发期继续向下验证前，先给终端打出显式 advisory 提示。
        print("> WARNING: [Python] work-folder gate reported only in-progress branch governance issues.")

        # 把开发期 advisory 摘要交给 facade，保持最终 handoff 语义稳定。
        return {"status": "advisory", "reason": "dirty_worktree_only"}

    # 其余治理失败继续保留原始非零退出码。
    raise SystemExit(completed_process_result.returncode)

# _is_advisory_work_folder_gate_failure 只放行当前开发期脏树提示。
def _is_advisory_work_folder_gate_failure(payload: dict) -> bool:
    """
    识别仅由 dirty worktree 触发的分支治理失败。

    :param payload: work-folder-gate 输出里的 JSON 诊断载荷。
    :return: 返回布尔值；True 表示当前失败可以按开发期 advisory 降级。
    """

    # 先读取 errors 字段，确认当前 JSON 诊断是否只包含单一失败。
    list_errors = payload.get("errors", [])  # work-folder-gate 错误列表

    # 多错误或非列表错误都不允许走 dirty worktree advisory 降级。
    if not isinstance(list_errors, list) or len(list_errors) != 1:

        # 只有单一 branch-gate dirty worktree 错误才允许继续往下判断。
        return False

    # 取出唯一错误文本，后续同时检查 branch-gate 前缀与固定片段。
    str_error = list_errors[0]  # 唯一错误消息文本

    # 非字符串错误无法可靠参与文本片段判断。
    if not isinstance(str_error, str):

        # 结构异常时宁可维持阻断，也不误放行 advisory。
        return False

    # 先匹配 branch-gate 前缀，确保当前失败来自分支治理而不是文档或目录治理。
    bool_mentions_branch_gate = "branch-gate:" in str_error  # 是否命中 branch-gate 前缀

    # 再匹配 dirty worktree 固定片段，确保当前 advisory 语义没有漂移。
    str_dirty_worktree_fragment = "worktree must be clean before continuing under strict branch governance"  # 脏工作树固定诊断片段

    # 只有命中固定片段，才说明当前失败是开发期允许降级的脏树状态。
    bool_mentions_dirty_worktree = str_dirty_worktree_fragment in str_error  # 是否命中脏树片段

    # 任一片段缺失时，都不应按 dirty worktree advisory 放行。
    if not bool_mentions_branch_gate or not bool_mentions_dirty_worktree:

        # 当前错误并不符合唯一允许的 advisory 模式。
        return False

    # 继续取出 branch_gate 子对象，复核结构化决策是否仍然对齐旧契约。
    dict_branch_gate = payload.get("branch_gate", {})  # 分支治理结构化诊断

    # 缺少结构化 branch_gate 对象时，不允许仅凭文本片段放行。
    if not isinstance(dict_branch_gate, dict):

        # 没有结构化决策时，当前失败不能被视为安全 advisory。
        return False

    # 再次检查 decision，确保仍然是 strict branch governance 的 blocked 语义。
    if dict_branch_gate.get("decision") != "blocked":

        # 不是 blocked 决策时，当前 JSON 形状已经偏离旧 advisory 合同。
        return False

    # 最后读取 reasons，确保阻断原因只保留 dirty worktree 这一项。
    list_reasons = dict_branch_gate.get("reasons", [])  # branch_gate 阻断原因列表

    # 只有完全匹配旧单因子 reasons 列表，才允许以 advisory 继续验证。
    return (
        isinstance(list_reasons, list)
        and list_reasons == ["worktree must be clean before continuing under strict branch governance"]
    )

# _has_transient_artifact_marker 统一识别 validate 会主动清理的局部运行产物路径。
def _has_transient_artifact_marker(text: str) -> bool:
    """
    判断文本是否命中 validate 可自动清理的瞬时产物路径。

    :param text: 需要检查的诊断文本。
    :return: 返回布尔值；True 表示文本里已经出现可自动清理的瞬时产物路径。
    """

    # 只把 smoke 目录和 Python 缓存目录视为 validate 可以自动回收的瞬态产物。
    return (
        "_smoke_runs" in text
        or "reports/smoke_runs_" in text.replace("\\", "/")
        or "__pycache__" in text
    )

# _is_dirty_worktree_branch_gate_message 统一匹配开发期允许 advisory 的脏树分支治理文案。
def _is_dirty_worktree_branch_gate_message(message: str) -> bool:
    """
    判断错误消息是否对应 dirty worktree 的 branch-gate advisory。

    :param message: 需要检查的单条错误消息文本。
    :return: 返回布尔值；True 表示消息匹配开发期允许 advisory 的固定文案。
    """

    # branch-gate 前缀和 dirty worktree 片段必须同时存在，才能视为同一类 advisory。
    return (
        "branch-gate:" in message
        and "worktree must be clean before continuing under strict branch governance" in message
    )

# _payload_has_only_transient_artifact_errors 判断 JSON 载荷是否只包含可安全重试的瞬态工件错误。
def _payload_has_only_transient_artifact_errors(payload: dict) -> bool:
    """
    判断 JSON 诊断是否只包含瞬态运行产物错误与 dirty worktree advisory。

    :param payload: 外部治理工具或 audit 工具返回的 JSON 诊断载荷。
    :return: 返回布尔值；True 表示当前失败可以执行一次安全补偿重试。
    """

    # 先读取 errors 列表，后续逐条排除非瞬态治理失败。
    list_errors = payload.get("errors", [])  # JSON 诊断错误列表

    # 空列表或非列表都不能说明当前失败属于可安全补偿的瞬态模式。
    if not isinstance(list_errors, list) or not list_errors:

        # 没有标准 errors 列表时，当前失败不进入补偿重试路径。
        return False

    # 用标记位确保 errors 列表里至少真实命中过一次瞬态工件路径。
    bool_saw_transient_artifact = False  # 是否已发现瞬态工件错误

    # 逐条筛掉瞬态工件错误与 dirty worktree advisory 之外的所有问题。
    for value_error in list_errors:

        # 非字符串错误不具备稳定的文本匹配语义。
        if not isinstance(value_error, str):

            # 结构异常时不冒险重试，直接保持阻断。
            return False

        # 命中瞬态工件路径时，记录当前 JSON 确实携带了可补偿的错误类型。
        if _has_transient_artifact_marker(value_error):

            # 只要看见一次真实瞬态工件错误，就允许后面继续检查其余条目。
            bool_saw_transient_artifact = True  # 当前 JSON 已命中过可补偿的瞬态工件错误

            # 当前条目已经确认为瞬态工件错误，继续检查后续 errors 条目。
            continue

        # dirty worktree advisory 允许与瞬态工件错误同时出现。
        if _is_dirty_worktree_branch_gate_message(value_error):

            # 当前条目属于开发期 advisory，不会阻止补偿重试继续判断。
            continue

        # 只要夹带任何其他治理失败，就不再把整次失败视为瞬态模式。
        return False

    # 必须至少看见过一次真正的瞬态工件错误，才说明当前 JSON 值得补偿重试。
    return bool_saw_transient_artifact

# _is_transient_work_folder_gate_failure 让 work-folder-gate 与 audit 共享同一套瞬态工件判据。
def _is_transient_work_folder_gate_failure(output: str) -> bool:
    """
    判断 work-folder-gate 失败是否属于瞬时运行产物缺失。

    :param output: 子命令完整输出文本。
    :return: 返回布尔值；True 表示当前失败值得执行一次补偿重试。
    """

    # work-folder-gate 与 audit 都由 validate 主动清理同一类本地产物。
    return _is_transient_audit_artifact_failure(output)

# verify_skill_effectiveness 复核 eval-skill 写出的 summary.ok 字段。
def verify_skill_effectiveness(report_path: Path) -> None:
    """
    读取 effectiveness 报告并在 summary.ok 非 True 时失败。

    :param report_path: 需要读取并校验的 skill-effectiveness 报告路径。
    :return: 不返回业务值；通过时表示当前 effectiveness 报告满足本地 confidence gate。
    :raises AssertionError: 当 summary.ok 不是严格布尔 True 时抛出。
    """

    # 先把 effectiveness JSON 报告完整读取出来，供摘要判断复用。
    dict_payload = json.loads(report_path.read_text(encoding="utf-8"))  # effectiveness 报告载荷

    # 再取出 summary 子对象，便于失败时把关键摘要原样带回给上层。
    dict_summary = dict_payload.get("summary", {})  # effectiveness 摘要对象

    # 用严格布尔判断复刻 validate 既有的 summary.ok 通过语义。
    value_summary_ok = dict_summary.get("ok")  # summary.ok 原始字段值

    # 只有布尔 True 才算真正通过，字符串或数字真值都不被接受。
    bool_gate_ok = isinstance(value_summary_ok, bool) and value_summary_ok  # summary.ok 是否严格为 True

    # summary.ok 不通过时，必须把摘要信息显式带进阻断错误里。
    if not bool_gate_ok:

        # 把 summary 原样拼进错误消息，便于定位具体 effectiveness 子项。
        raise AssertionError(f"> ERR: [Python] Skill-effectiveness gate failed: {dict_summary}")

# verify_audit_skill_report 解析 audit JSON，并在 errors 非空时保持阻断。
def verify_audit_skill_report(output: str) -> None:
    """
    解析 audit 输出中的 JSON 对象，并在 errors 非空时失败。

    :param output: audit_skill 标准输出与日志混合文本。
    :return: 不返回业务值；通过时表示 audit JSON 没有阻塞错误。
    :raises AssertionError: 当 audit JSON 的 errors 列表非空时抛出。
    """

    # 先从 audit 输出里抽出最后一个 JSON object，兼容前置日志噪声。
    dict_payload = parse_json_object(output)  # audit 结构化 JSON 载荷

    # 再读取 errors 列表，用于判断 skill audit 是否报告了阻断项。
    list_errors = dict_payload.get("errors", [])  # audit 阻塞错误列表

    # 只有列表型且非空的 errors 才构成真正的 audit 阻断。
    if isinstance(list_errors, list) and list_errors:

        # 把多条 audit 错误压成一行，方便 validate 外层直接显示。
        str_joined_errors = "; ".join(str(item) for item in list_errors)  # 压平后的 audit 错误摘要

        # 用统一 ERR 前缀把 audit 阻断显式抛给 facade。
        raise AssertionError("> ERR: [Python] Skill audit reported blocking errors: " + str_joined_errors)

# run_audit_skill 执行 skill audit，并在瞬态产物缺失时执行一次补偿重试。
def run_audit_skill(
    settings: dict,
    smoke_dir: Path,
    governance_gate_context: GovernanceGateContext,
) -> None:
    """
    运行 skill audit；遇到瞬时产物缺失时清理后重试一次。

    :param settings: Verilog skill 治理配置字典。
    :param smoke_dir: 当前 validate worker 使用的 smoke 目录。
    :param governance_gate_context: audit 命令、工作目录与清理回调所在的治理上下文。
    :return: 不返回业务值；通过时表示 skill audit 最终没有阻塞错误。
    :raises SystemExit: 当 audit 最终失败且不属于通过或补偿成功场景时抛出。
    """

    # path_setting 只在真正需要解析 audit_skill 路径时才延迟导入。
    from scripts.python.workflow.config import path_setting

    # 先按配置解析 audit_skill 脚本路径，再拼出既有的 audit 命令顺序。
    list_command = [  # skill audit 命令
        governance_gate_context.str_python_executable,  # audit 子进程沿用当前解释器
        str(path_setting(settings, "audit_skill")),  # 配置里的 audit_skill 脚本路径
        str(governance_gate_context.path_skill_root),  # audit 命令要检查的 skill 目录
    ]

    # 正式执行 audit 前，先清理本轮 worker 自己负责的常规残留。
    governance_gate_context.func_cleanup_residuals(settings, smoke_dir)

    # 先解析 audit 子进程应当使用的 validate 工作目录。
    path_audit_cwd = governance_gate_context.func_validation_workspace_root()  # audit 子进程工作目录

    # 把 audit 命令真正发给外部审计脚本，并暂存第一次返回值。
    completed_process_result = governance_gate_context.func_run(  # 首轮 audit 子进程返回值
        list_command,  # 首次 audit 命令参数
        cwd=path_audit_cwd,  # 首次 audit 工作目录
        allow_failure=True,  # 首次 audit 允许 helper 自行解释非零退出
    )

    # 零退出码时仍需继续复查 audit JSON 里的 errors 字段。
    if completed_process_result.returncode == 0:

        # 只有 audit JSON 也保持无阻塞错误时，当前 gate 才算真正通过。
        verify_audit_skill_report(completed_process_result.stdout)

        # 首次 audit 已完全通过时，当前 helper 直接结束。
        return

    # 先合并 stdout 与 stderr，覆盖 traceback 和 JSON-only 两类瞬态失败模式。
    str_combined_output = f"{completed_process_result.stdout}\n{completed_process_result.stderr}"  # 首次 audit 失败完整诊断

    # 命中瞬态工件失败时，只允许执行一次局部清理后的补偿重试。
    if _is_transient_audit_artifact_failure(str_combined_output):

        # 先只清理当前 worker 归属的局部 audit 运行产物，避免误删并行兄弟目录。
        governance_gate_context.func_cleanup_audit_retry_local_artifacts(settings, smoke_dir)

        # 在完成局部清理后重新取一次审计结果，专门验证竞态是否已经消失。
        completed_process_retry = governance_gate_context.func_run(  # 清理后的 audit 复检返回值
            list_command,  # audit 补偿重试命令参数
            cwd=path_audit_cwd,  # audit 补偿重试工作目录
            allow_failure=True,  # audit 补偿重试允许 helper 自行解释非零退出
        )

        # 重试成功时，仍然需要复查 audit JSON 里的 errors 字段。
        if completed_process_retry.returncode == 0:

            # 只有重试后的 JSON 报告也保持无阻塞错误，当前 audit gate 才算通过。
            verify_audit_skill_report(completed_process_retry.stdout)

            # 补偿重试通过后，当前 helper 不再继续向上抛退出码。
            return

        # 重试仍失败时，把最新退出码按旧语义透传给 facade。
        raise SystemExit(completed_process_retry.returncode)

    # 其余非瞬态失败继续把首次 audit 的原始退出码透传出去。
    raise SystemExit(completed_process_result.returncode)

# _is_transient_audit_artifact_failure 负责识别可安全补偿的 audit 失败模式。
def _is_transient_audit_artifact_failure(output: str) -> bool:
    """
    判断 audit 失败是否属于瞬时运行产物缺失。

    :param output: 子命令完整输出文本，可能混合 traceback 与 JSON。
    :return: 返回布尔值；True 表示当前失败值得执行一次补偿重试。
    """

    # 先过滤掉完全不涉及 smoke 或 pycache 的普通治理失败。
    if not _has_transient_artifact_marker(output):

        # 没有瞬态工件路径时，当前失败不能进入补偿重试分支。
        return False

    # traceback 里已经明确点出 FileNotFoundError 时，可以直接视为瞬态缺失。
    if "FileNotFoundError" in output:

        # traceback 风格瞬态缺失不需要额外 JSON 解析就可以进入重试。
        return True

    # 没有 traceback 时，再尝试从 stdout-only 输出里解析 JSON 诊断。
    try:

        # 取出最后一个 JSON object，覆盖 stdout-only 的结构化失败场景。
        dict_payload = parse_json_object(output)  # JSON 诊断载荷

    # 非 JSON 输出又没有 traceback 时，当前失败不属于已知的瞬态模式。
    except ValueError:

        # 无法证明当前失败只涉及瞬态工件时，当前 helper 保持阻断。
        return False

    # 只有 JSON errors 里只含瞬态工件错误与 dirty worktree advisory 时，才允许重试。
    return _payload_has_only_transient_artifact_errors(dict_payload)

# parse_json_object 从混合日志尾部提取最后一个 JSON object。
def parse_json_object(output: str) -> dict:
    """
    从命令输出中向后搜索并解析 JSON object。

    :param output: 子命令标准输出与日志混合文本。
    :return: 返回最后一个可成功解析的 JSON object。
    :raises ValueError: 当输出里不存在任何可解析 JSON object 时抛出。
    """

    # 先记录所有左花括号位置，后面从尾部向前尝试可避开前置日志噪声。
    list_starts = [index for index, char in enumerate(output) if char == "{"]  # JSON 候选起点列表

    # 从最后一个左花括号开始回退，优先解析命令尾部的结构化摘要。
    for int_start in reversed(list_starts):

        # 先截出从当前花括号到文本末尾的候选片段。
        str_candidate = output[int_start:].strip()  # 当前 JSON 候选片段

        # 空候选不可能构成合法 JSON object。
        if not str_candidate:

            # 当前起点没有形成有效载荷时，继续尝试更早的左花括号。
            continue

        # 对当前候选片段执行 JSON 解析，失败时继续向前回退。
        try:

            # 把当前候选片段解析成 Python 对象，供 object 类型判断复用。
            dict_payload = json.loads(str_candidate)  # 当前候选解析结果

        # 普通日志里的花括号不构成合法 JSON 时，不应打断继续回退。
        except json.JSONDecodeError:

            # 当前候选解析失败时，继续尝试更早的起点。
            continue

        # validate 只接受 JSON object，数组或标量都不是预期的治理报告形态。
        if isinstance(dict_payload, dict):

            # 返回第一个从尾部成功命中的 JSON object，保持既有解析策略。
            return dict_payload

    # 所有候选都失败时，说明当前输出里根本没有可用 JSON object。
    raise ValueError("> ERR: [Python] No JSON object found in command output.")
