"""执行 Verilog validation 的外部工具链 readiness 阶段。"""

# future annotations 避免运行期解析复杂类型提示。
from __future__ import annotations

# dataclass 用于固定 readiness 回调依赖集合。
from dataclasses import dataclass

# json 仅用于 yosys 命令路径 quoting。
import json

# os 用于读取 simulator priority 环境覆盖。
import os

# Path 用于外部命令工作目录和临时 artifact 路径。
from pathlib import Path

# shutil 用于发现外部命令是否存在。
import shutil

# subprocess 用于执行外部命令并处理超时。
import subprocess

# sys 用于区分 VCS 输出文件名的 Windows 后缀。
import sys

# tempfile 用于隔离 simulator 临时工作目录。
import tempfile

# typing 保存回调函数类型和动态 JSON 映射类型。
from typing import Any, Callable

# 项目配置用于读取默认 simulator priority。
from scripts.python.workflow.config import load_settings
from .validation_models import ValidationIssue, readiness_at_least

# ReadinessDeps 保存外部执行层需要从 validation 编排层注入的可 patch 依赖。
@dataclass(frozen=True)
class ReadinessDeps:
    """保存 readiness 执行时需要回调的函数依赖。"""

    # rtl_files 返回包含 testbench 的全部 Verilog-like 文件。
    rtl_files: Callable[[Path], list[Path]]  # 全量 RTL 文件发现函数

    # rtl_source_files 返回非 testbench 的综合源文件。
    rtl_source_files: Callable[[Path], list[Path]]  # RTL 源文件发现函数

    # is_testbench 判断文件是否为 testbench。
    is_testbench: Callable[[Path], bool]  # testbench 判定函数

    # select_simulator_backend 允许测试 patch 后端选择。
    select_simulator_backend: Callable[[dict[str, Any]], dict[str, Any]]  # 仿真后端选择函数

    # simulator_config 读取 simulator priority 和策略。
    simulator_config: Callable[[dict[str, Any] | None], dict[str, Any]]  # 仿真配置读取函数

    # backend_tools 返回后端依赖工具集合。
    backend_tools: Callable[[str], tuple[str, ...]]  # 后端工具映射函数

    # run_tool 执行单条外部命令。
    run_tool: Callable[[list[str], Path, str, str], list[ValidationIssue]]  # 外部命令执行函数

# run_rtl_readiness 是外部 readiness 阶段的主入口。
def run_rtl_readiness(
    spec: dict[str, Any],
    root: Path,
    readiness: str,
    run_external: bool,
    simulator_config: dict[str, Any] | None,
    deps: ReadinessDeps,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """按 readiness 请求执行 compile/execute/implement 阶段。

    :param spec: 当前 Verilog 生成结果的结构化规格。
    :param root: artifact 根目录，外部工具在此基础上定位输入。
    :param readiness: 请求的验证深度，支持 static/compile/execute/implement。
    :param run_external: 是否允许真实调用外部工具链。
    :param simulator_config: 调用方显式传入的 simulator 配置覆盖。
    :param deps: 外部工具执行和文件发现依赖集合。
    :return: validation issues 与 readiness metrics。
    """

    # dict_sim_config 是本轮实际使用的 simulator 配置。
    dict_sim_config = deps.simulator_config(simulator_config)  # 仿真后端选择配置

    # dict_metrics 初始化所有 readiness 阶段的执行证据。
    dict_metrics = _initial_readiness_metrics(dict_sim_config)  # readiness 结构化度量

    # static readiness 不需要外部工具。
    if readiness == "static":

        # 静态阶段直接返回默认 metrics。
        return [], dict_metrics

    # 禁止外部执行时必须用 error 明确阻断。
    if not run_external:

        # not-run 状态体现本轮没有真实外部验证证据。
        _mark_external_not_run(dict_metrics, readiness)

        # 缺失外部证据时返回每个要求工具的阻断问题。
        # tuple_required_tools 是 readiness 阶段要求的真实外部工具证据。
        tuple_required_tools = _required_tools_for_readiness(readiness, dict_sim_config, deps)  # 未运行时的工具要求

        # 返回每个未运行工具对应的阻断诊断。
        return _external_not_run_errors(tuple_required_tools, readiness), dict_metrics

    # list_issues 收集 simulator 和 implement 阶段问题。
    list_issues: list[ValidationIssue] = []  # readiness 阶段诊断集合

    # dict_selection 是根据配置和 PATH 选择出的仿真后端。
    dict_selection = deps.select_simulator_backend(dict_sim_config)  # 后端选择结果

    # list_missing_backend_names 记录所有不可用优先后端名称。
    list_missing_backend_names: list[str] = []  # 不可用优先后端名称列表

    # 逐项展开 missing_preferred，保持 metrics 类型稳定。
    for dict_backend in dict_selection["missing_preferred"]:

        # 当前缺失后端名称写入 metrics 辅助列表。
        list_missing_backend_names.append(str(dict_backend["name"]))

    # metrics 记录所有缺失的优先后端。
    dict_metrics["missing_preferred_backends"] = list_missing_backend_names  # 不可用优先后端名称

    # 没有可用后端时 compile/execute readiness 必须阻断。
    if not dict_selection["backend"]:

        # 返回一条聚合的工具链问题，detail 中保留缺失工具列表。
        return [_no_simulator_backend_issue(readiness, dict_selection["missing_preferred"])], dict_metrics

    # dict_backend 是本轮选中的 simulator 后端。
    dict_backend = dict_selection["backend"]  # 当前选中的 simulator 后端

    # str_backend_name 进入 metrics 和命令分发。
    str_backend_name = str(dict_backend["name"])  # simulator 后端名称

    # metrics 记录实际选中的后端名称。
    dict_metrics["selected_simulator_backend"] = str_backend_name  # 实际执行的 simulator 后端

    # fallback warning 告诉用户优先后端为什么被跳过。
    list_issues.extend(_fallback_warnings(str_backend_name, dict_selection["missing_preferred"], readiness))

    # simulator 阶段执行 compile 或 execute。
    tuple_simulator = _run_simulator_backend(str_backend_name, spec, root, readiness, deps)  # 仿真执行结果

    # list_simulator_issues 是 simulator 阶段返回的诊断。
    list_simulator_issues = tuple_simulator[0]  # 仿真阶段诊断

    # list_executed_tools 是 simulator 阶段已经尝试的工具。
    list_executed_tools = tuple_simulator[1]  # 仿真阶段已执行工具

    # 合并 simulator 诊断。
    list_issues.extend(list_simulator_issues)

    # 记录 simulator 阶段工具执行证据。
    dict_metrics["executed_tools"] = list_executed_tools  # 已执行 simulator 工具列表

    # 根据 simulator 诊断更新 compile/execute 状态。
    _mark_simulator_status(dict_metrics, list_simulator_issues, readiness)

    # implement 阶段额外执行 yosys 综合烟测。
    if readiness_at_least(readiness, "implement"):

        # implement issues 会原位追加到 list_issues 和 metrics。
        _run_implementation_readiness(spec, root, deps, list_issues, dict_metrics)

    # 返回 readiness 阶段全部诊断和 metrics。
    return list_issues, dict_metrics

# simulator_config 读取受控 simulator priority。
def simulator_config(simulator_config_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回 simulator selection_policy 和 priority 配置。

    :param simulator_config_payload: 调用方显式传入的 simulator 配置节点。
    :return: 标准化后的 selection_policy 和 priority 列表。
    """

    # dict_raw 保存显式传入或默认配置中的 simulator 节点。
    dict_raw = simulator_config_payload  # simulator 配置原始节点

    # 未显式传入时读取随包 defaults。
    if dict_raw is None:

        # 配置读取失败不能阻断静态验证，回退空配置。
        try:

            # settings 是 config/defaults.json 和本地覆盖合并后的结果。
            dict_settings = load_settings()  # 运行时设置

            # validation.simulators 是 simulator policy 的唯一来源。
            dict_raw = dict_settings.get("validation", {}).get("simulators", {})  # 默认 simulator 配置

        # defaults 读取失败时保留默认后端顺序，避免静态流程被配置文件阻断。
        except Exception:

            # 配置不可读时使用默认 priority。
            dict_raw = {}  # 配置读取失败时的空 simulator 配置

    # list_priority 保留 raw priority 的类型检查输入。
    list_priority = dict_raw.get("priority") if isinstance(dict_raw, dict) else None  # 原始 priority 字段

    # 非列表或空列表时使用默认后端优先级。
    if not isinstance(list_priority, list) or not list_priority:

        # 默认顺序优先 xsim，再兼容商业和开源后端。
        list_priority = ["xsim", "vcs_verdi", "iverilog"]  # 默认 simulator 后端顺序

    # 环境变量允许 CI 临时覆盖 simulator 优先级。
    str_env_priority = os.environ.get("VERILOG_GENERATOR_SIMULATOR_PRIORITY")  # 环境变量 simulator priority

    # 环境变量存在时按逗号拆分并去空。
    if str_env_priority:

        # list_env_priority 保存环境变量解析出的后端顺序。
        list_env_priority: list[str] = []  # 环境变量覆盖后的后端顺序

        # 逐项清理逗号分隔的后端名称。
        for str_item in str_env_priority.split(","):

            # str_backend_name 是去除空白后的候选后端。
            str_backend_name = str_item.strip()  # 环境变量中的后端名称

            # 空字符串不进入 priority。
            if not str_backend_name:

                # 继续处理下一个环境变量片段。
                continue

            # 有效后端名称追加到环境变量 priority。
            list_env_priority.append(str_backend_name)

        # CI 或本地 shell 指定的顺序优先于配置文件。
        list_priority = list_env_priority  # VERILOG_GENERATOR_SIMULATOR_PRIORITY 解析结果

    # selection_policy 只有 dict 配置时读取，否则回退 fallback。
    if isinstance(dict_raw, dict):

        # dict 配置可显式指定 selection_policy。
        str_selection_policy = str(dict_raw.get("selection_policy", "fallback"))  # 后端选择策略

    # 非 dict 配置不能读取 selection_policy，只能使用 fallback。
    else:

        # 非 dict 配置回退到 fallback 策略。
        str_selection_policy = "fallback"  # 默认后端选择策略

    # 返回结构保持旧 validation metrics 契约。
    return {
        "selection_policy": str_selection_policy,
        "priority": [str(str_item) for str_item in list_priority],
    }

# select_simulator_backend 根据 priority 和 PATH 选择第一个可用后端。
def select_simulator_backend(dict_sim_config: dict[str, Any]) -> dict[str, Any]:
    """返回可用 simulator 后端和缺失的优先后端。

    :param dict_sim_config: 已标准化的 simulator priority 配置。
    :return: 选中的 backend 结构和不可用优先后端清单。
    """

    # list_missing_preferred 记录每个不可用候选的缺失工具。
    list_missing_preferred: list[dict[str, Any]] = []  # 不可用优先后端列表

    # 按配置 priority 逐个尝试。
    for str_backend_name in dict_sim_config["priority"]:

        # tuple_tools 是该后端要求的命令集合。
        tuple_tools = backend_tools(str_backend_name)  # 后端工具集合

        # 未知后端记录为缺失但不抛异常。
        if not tuple_tools:

            # unknown-backend 提示配置值不受支持。
            list_missing_preferred.append({"name": str_backend_name, "missing_tools": ["<unknown-backend>"]})

            # 当前后端不可用，保留缺失证据后检查下一个候选。
            continue

        # list_missing_tools 保存 PATH 中找不到的工具。
        list_missing_tools: list[str] = []  # 当前后端缺失的工具列表

        # 逐个工具检查 PATH 可用性。
        for str_tool in tuple_tools:

            # 工具存在时继续检查下一个工具。
            if shutil.which(str_tool) is not None:

                # 当前工具可用。
                continue

            # 缺失工具追加到当前后端诊断。
            list_missing_tools.append(str_tool)

        # 有任一工具缺失时该后端不可用。
        if list_missing_tools:

            # 缺失详情进入 warning 或 error detail。
            list_missing_preferred.append({"name": str_backend_name, "missing_tools": list_missing_tools})

            # 继续尝试下一个候选后端。
            continue

        # 找到首个完整后端后停止搜索。
        return {
            "backend": {"name": str_backend_name, "tools": tuple_tools},
            "missing_preferred": list_missing_preferred,
        }

    # 所有候选都不可用时返回空 backend。
    return {"backend": None, "missing_preferred": list_missing_preferred}

# backend_tools 描述每个 simulator 后端的命令依赖。
def backend_tools(name: str) -> tuple[str, ...]:
    """返回 simulator 后端需要的命令集合。

    :param name: simulator 后端名称。
    :return: 后端必须存在于 PATH 中的工具命令元组。
    """

    # dict_tool_map 集中记录受支持后端，避免分支里重复工具列表。
    dict_tool_map = {  # simulator 后端到工具列表的映射
        "xsim": ("xvlog", "xelab", "xsim"),  # Vivado xsim 编译展开运行工具链
        "vcs_verdi": ("vcs", "verdi"),  # Synopsys VCS 与 Verdi 可用性检查工具链
        "iverilog": ("iverilog", "vvp"),  # Icarus Verilog 编译和 vvp 运行工具链
    }

    # 未知后端返回空元组，由选择逻辑生成诊断。
    return dict_tool_map.get(name, ())

# run_tool 执行单条外部命令并转换为 ValidationIssue。
def run_tool(command: list[str], root: Path, label: str, stage: str) -> list[ValidationIssue]:
    """执行外部命令并返回工具链诊断。

    :param command: 外部工具命令及参数。
    :param root: 命令执行工作目录。
    :param label: 诊断消息中展示的工具步骤名称。
    :param stage: readiness 阶段名称。
    :return: 外部命令产生的 validation issues。
    """

    # str_resolved_tool 使用 PATH 中的绝对工具路径，找不到则保留原命令。
    str_resolved_tool = shutil.which(command[0])  # PATH 解析后的工具路径

    # list_run_command 是实际传给 subprocess 的命令。
    list_run_command = [str_resolved_tool or command[0], *command[1:]]  # 实际执行命令

    # subprocess 统一捕获 stdout/stderr，避免污染调用方日志。
    try:

        # completed_process 保存命令退出状态和输出。
        completed_process = subprocess.run(  # 当前外部工具命令的退出码和捕获输出
            list_run_command,  # 已解析工具路径后的实际命令
            cwd=root,  # 外部工具在 artifact 根目录执行
            capture_output=True,  # stdout/stderr 进入诊断摘要
            text=True,  # 输出按文本截断和拼接
            timeout=30,  # 单个外部工具最多等待秒数
            check=False,  # 非零退出码转为 ValidationIssue
        )

    # 工具超时属于真实执行失败，需要保留截断后的 stdout/stderr。
    except subprocess.TimeoutExpired as exc:

        # str_detail 保留截断后的超时输出。
        str_detail = short_output(timeout_output(exc))  # 超时输出摘要

        # 超时按工具链 error 返回。
        return [
            ValidationIssue(
                "error",
                f"{label} failed to run: {exc}",
                stage=stage,
                source="toolchain_issue",
                tool=command[0],
                detail=str_detail,
            )
        ]

    # 进程无法启动时转换成工具链诊断，调用方无需处理系统异常。
    except OSError as exc:

        # OSError 通常表示工具不可执行或工作目录不可用。
        return [
            ValidationIssue(
                "error",
                f"{label} failed to run: {exc}",
                stage=stage,
                source="toolchain_issue",
                tool=command[0],
            )
        ]

    # 非零退出码转换为 error，并保存截断输出。
    if completed_process.returncode != 0:

        # str_output 优先展示 stderr，缺失时展示 stdout。
        str_output = short_output((completed_process.stderr or completed_process.stdout or "").strip())  # 命令失败输出

        # 返回单条工具链失败诊断。
        return [
            ValidationIssue(
                "error",
                f"{label} failed.",
                stage=stage,
                source="toolchain_issue",
                tool=command[0],
                detail=str_output,
            )
        ]

    # 零退出码表示该命令通过。
    return []

# _initial_readiness_metrics 构建 readiness 阶段的 metrics 框架。
def _initial_readiness_metrics(dict_sim_config: dict[str, Any]) -> dict[str, Any]:
    """返回 readiness 阶段默认 metrics。

    :param dict_sim_config: 本轮使用的 simulator 选择配置。
    :return: 带默认阶段状态和后端信息的 metrics。
    """

    # execution_status 明确列出每个外部阶段当前状态。
    dict_execution_status = {  # readiness 阶段执行状态
        "static": "passed",  # 静态阶段已在进入 readiness 前完成
        "compile": "not_requested",  # 默认未请求编译证据
        "simulation": "not_requested",  # 默认未请求仿真运行证据
        "implement": "not_requested",  # 默认未请求综合证据
    }

    # metrics 字段保持旧 validation report 结构。
    return {
        "selected_simulator_backend": None,
        "executed_tools": [],
        "missing_preferred_backends": [],
        "selection_policy": dict_sim_config["selection_policy"],
        "execution_status": dict_execution_status,
    }

# _mark_external_not_run 标记被策略禁止的外部阶段。
def _mark_external_not_run(dict_metrics: dict[str, Any], readiness: str) -> None:
    """把请求到但未执行的外部阶段标记为 not_run。

    :param dict_metrics: 需要原位更新的 readiness metrics。
    :param readiness: 当前请求的 readiness 深度。
    :return: None，结果写回 dict_metrics。
    """

    # compile readiness 起所有外部阶段都至少需要编译证据。
    dict_metrics["execution_status"]["compile"] = "not_run"  # compile 阶段未运行外部工具

    # execute readiness 需要仿真执行证据。
    if readiness_at_least(readiness, "execute"):

        # execute 阶段缺少真实仿真运行证据。
        dict_metrics["execution_status"]["simulation"] = "not_run"  # 仿真状态保持为策略性未执行

    # implement readiness 还需要实现或综合证据。
    if readiness_at_least(readiness, "implement"):

        # implement 阶段缺少综合或实现烟测证据。
        dict_metrics["execution_status"]["implement"] = "not_run"  # 综合状态保持为策略性未执行

# _mark_simulator_status 汇总 simulator 阶段成功或失败。
def _mark_simulator_status(
    dict_metrics: dict[str, Any],
    list_simulator_issues: list[ValidationIssue],
    readiness: str,
) -> None:
    """根据 simulator issues 更新 compile 和 simulation 状态。

    :param dict_metrics: 需要原位更新的 readiness metrics。
    :param list_simulator_issues: simulator 后端返回的阶段诊断。
    :param readiness: 当前请求的 readiness 深度。
    :return: None，状态写回 dict_metrics。
    """

    # list_compile_issues 只包含 compile 阶段诊断。
    list_compile_issues: list[ValidationIssue] = []  # compile 阶段诊断列表

    # 逐条筛选 compile 阶段诊断，避免压缩诊断逻辑。
    for issue in list_simulator_issues:

        # 非 compile 阶段诊断不影响 compile 状态。
        if issue.stage != "compile":

            # 继续检查下一条 simulator 诊断。
            continue

        # compile 阶段诊断进入状态判定列表。
        list_compile_issues.append(issue)

    # compile 状态由 compile 阶段 error 决定。
    dict_metrics["execution_status"]["compile"] = "failed" if _has_error(list_compile_issues) else "passed"  # compile 执行结果

    # execute readiness 才需要更新 simulation 状态。
    if readiness_at_least(readiness, "execute"):

        # 仿真运行问题单独聚合，避免 compile error 被重复计入。
        list_execute_issues: list[ValidationIssue] = []  # 待判定 simulation 状态的问题集合

        # 逐条筛选 execute 阶段诊断。
        for issue in list_simulator_issues:

            # compile 阶段问题已经提前决定 compile 状态。
            if issue.stage != "execute":

                # 当前诊断属于 compile 或其他阶段，继续寻找 execute 诊断。
                continue

            # execute 阶段诊断进入 simulation 状态判定。
            list_execute_issues.append(issue)

        # simulation 状态只看 execute 阶段是否出现 error。
        dict_metrics["execution_status"]["simulation"] = "failed" if _has_error(list_execute_issues) else "passed"  # 仿真阶段聚合状态

# _run_implementation_readiness 执行 yosys 综合级 smoke。
def _run_implementation_readiness(
    spec: dict[str, Any],
    root: Path,
    deps: ReadinessDeps,
    list_issues: list[ValidationIssue],
    dict_metrics: dict[str, Any],
) -> None:
    """在 implement readiness 下运行 yosys 综合检查。

    :param spec: 当前 Verilog 模块规格。
    :param root: artifact 根目录。
    :param deps: 文件发现和命令执行依赖。
    :param list_issues: 需要原位追加的总诊断列表。
    :param dict_metrics: 需要原位更新的 readiness metrics。
    :return: None，诊断和状态写回传入对象。
    """

    # yosys 不存在时 _require_tool 会追加阻断诊断。
    if not _require_tool("yosys", "implement", list_issues):

        # 工具缺失时不再构造综合命令。
        return

    # list_source_files 是非 testbench 的 RTL 综合输入。
    list_source_files: list[str] = []  # yosys 读取的 RTL 源文件

    # 逐个展开 RTL 源文件路径，保持命令构造可审计。
    for path_source in deps.rtl_source_files(root):

        # yosys 命令只接受字符串路径。
        list_source_files.append(str(path_source))

    # str_top 是待综合顶层名。
    str_top = str(spec.get("name") or "")  # yosys synth 顶层模块名

    # str_read_command 复用 yosys read_verilog 命令格式。
    # list_yosys_inputs 保存 yosys 命令中已经转义的 RTL 路径。
    list_yosys_inputs: list[str] = []  # yosys read_verilog 的转义输入路径

    # 逐个路径 quoting，保证包含空格的本地路径不会破坏命令。
    for str_path in list_source_files:

        # 转义后的路径片段进入 read_verilog 子句。
        list_yosys_inputs.append(yosys_quote(str_path))

    # str_read_command 是 yosys 综合命令的 read_verilog 子句。
    str_read_command = "read_verilog " + " ".join(list_yosys_inputs)  # yosys 综合前读取 RTL 的 read_verilog 子句

    # list_yosys_command 是综合 readiness 的外部命令。
    list_yosys_command = ["yosys", "-q", "-p", f"{str_read_command}; synth -top {str_top}; stat"]  # 对 str_top 执行 synth/stat 的 yosys 命令

    # list_implement_issues 保存 yosys 执行结果。
    list_implement_issues = deps.run_tool(list_yosys_command, root, "yosys synthesis", "implement")  # yosys 综合 readiness 诊断

    # yosys 诊断并入本轮 readiness 总诊断。
    list_issues.extend(list_implement_issues)

    # executed_tools 记录 yosys 已被尝试。
    dict_metrics["executed_tools"].append("yosys")

    # implement 状态只由本轮 yosys 烟测结果决定。
    dict_metrics["execution_status"]["implement"] = "failed" if _has_error(list_implement_issues) else "passed"  # yosys 烟测状态

# _fallback_warnings 为后备 simulator 选择生成 warning。
def _fallback_warnings(
    selected: str,
    missing_preferred: list[dict[str, Any]],
    readiness: str,
) -> list[ValidationIssue]:
    """返回优先后端不可用时的 warning 列表。

    :param selected: 最终选中的可用 simulator 后端名称。
    :param missing_preferred: 位于 selected 之前但不可用的后端清单。
    :param readiness: 当前请求的 readiness 深度。
    :return: fallback 选择产生的 warning 诊断列表。
    """

    # list_warnings 保存每个被跳过后端的说明。
    list_warnings: list[ValidationIssue] = []  # simulator fallback 警告集合

    # compile readiness 的 fallback 归入 compile，execute 及以上归入 execute。
    str_stage = "compile" if readiness == "compile" else "execute"  # fallback warning 所属阶段

    # 每个缺失后端生成一条 warning。
    for dict_backend in missing_preferred:

        # str_missing_tools 展示缺失工具名。
        str_missing_tools = ", ".join(dict_backend["missing_tools"])  # 当前后端缺失工具列表

        # warning 不阻断后备后端执行，但保留选择证据。
        list_warnings.append(
            ValidationIssue(
                "warning",
                (
                    f"Preferred simulator backend {dict_backend['name']!r} is unavailable; "
                    f"selected {selected!r}. Missing tools: {str_missing_tools}."
                ),
                stage=str_stage,
                source="toolchain_issue",
                tool=dict_backend["name"],
            )
        )

    # 返回所有 fallback warning。
    return list_warnings

# _no_simulator_backend_issue 生成没有可用 simulator 的阻断诊断。
def _no_simulator_backend_issue(readiness: str, missing_backends: list[dict[str, Any]]) -> ValidationIssue:
    """返回无可用 simulator 后端的 error 诊断。

    :param readiness: 当前请求的 readiness 深度。
    :param missing_backends: 所有候选后端的缺失工具详情。
    :return: 聚合后的 simulator 不可用 error 诊断。
    """

    # list_missing_details 聚合所有候选后端的缺失工具。
    list_missing_details: list[str] = []  # 后端缺失详情片段

    # 逐个后端展开缺失工具，便于 readable gate 审计。
    for dict_backend in missing_backends:

        # str_missing_tools 展示当前后端缺失命令。
        str_missing_tools = ", ".join(dict_backend["missing_tools"])  # 当前后端缺失工具名

        # 当前后端的缺失摘要进入 detail。
        list_missing_details.append(f"{dict_backend['name']}: {str_missing_tools}")

    # str_detail 是 validation issue detail 中的聚合说明。
    str_detail = "; ".join(list_missing_details)  # simulator 后端缺失工具聚合说明

    # readiness 为 compile 时归入 compile，否则归入 execute。
    str_stage = "compile" if readiness == "compile" else "execute"  # 问题所属阶段

    # 返回聚合后的工具链阻断问题。
    return ValidationIssue(
        "error",
        (
            f"No configured simulator backend is available for readiness {readiness!r}. "
            "Provide xsim, VCS+Verdi, or iverilog/vvp, or rerun with --no-external."
        ),
        stage=str_stage,
        source="toolchain_issue",
        detail=str_detail,
    )

# _run_simulator_backend 分发到具体 simulator 后端。
def _run_simulator_backend(
    name: str,
    spec: dict[str, Any],
    root: Path,
    readiness: str,
    deps: ReadinessDeps,
) -> tuple[list[ValidationIssue], list[str]]:
    """运行选中的 simulator 后端。

    :param name: 已选中的 simulator 后端名称。
    :param spec: 当前 Verilog 模块规格。
    :param root: artifact 根目录。
    :param readiness: 当前请求的 readiness 深度。
    :param deps: 文件发现和命令执行依赖。
    :return: 后端诊断列表和真实执行过的工具名列表。
    """

    # list_all_files 包含 RTL 源文件和 testbench。
    list_all_files: list[str] = []  # simulator 输入文件列表

    # 逐个展开 simulator 输入文件，保持路径转换可审计。
    for path_item in deps.rtl_files(root):

        # simulator 命令使用字符串路径。
        list_all_files.append(str(path_item))

    # simulator 后端需要从 spec 输出推导可展开或可运行的 testbench 顶层。
    str_tb_top = _testbench_top(spec)  # simulator elaborate 或 run 使用的 testbench 顶层模块名

    # xsim 后端使用 xvlog/xelab/xsim。
    if name == "xsim":

        # xsim 分支会继续拆分 xvlog/xelab/xsim 三个步骤。
        return _run_xsim(list_all_files, str_tb_top, readiness, deps)

    # VCS/Verdi 后端使用 verdi availability 和 vcs。
    if name == "vcs_verdi":

        # VCS/Verdi 分支先探测 Verdi，再执行 VCS 编译或运行。
        return _run_vcs_verdi(list_all_files, readiness, deps)

    # Icarus 后端使用 iverilog 生成镜像，并用 vvp 运行。
    if name == "iverilog":

        # Icarus 分支覆盖 iverilog 编译和 vvp 运行证据。
        return _run_iverilog(list_all_files, readiness, deps)

    # 未知后端在选择阶段理论上不会出现，但保留诊断。
    return [
        ValidationIssue(
            "error",
            f"Unknown simulator backend {name!r}.",
            stage="compile",
            source="toolchain_issue",
            tool=name,
        )
    ], []

# _run_xsim 执行 Vivado xsim 编译、展开和可选运行。
def _run_xsim(
    all_files: list[str],
    tb_top: str,
    readiness: str,
    deps: ReadinessDeps,
) -> tuple[list[ValidationIssue], list[str]]:
    """运行 xsim 后端。

    :param all_files: simulator 需要编译的 RTL 和 testbench 文件。
    :param tb_top: testbench 顶层模块名。
    :param readiness: 当前请求的 readiness 深度。
    :param deps: 命令执行依赖集合。
    :return: xsim 后端诊断列表和已执行工具名列表。
    """

    # list_issues 收集 xsim 各步骤诊断。
    list_issues: list[ValidationIssue] = []  # xvlog/xelab/xsim 分阶段诊断集合

    # list_executed_tools 记录 xsim 调用过的工具。
    list_executed_tools: list[str] = []  # xsim 后端实际命令证据列表

    # 临时目录隔离 xsim work library 和快照。
    with tempfile.TemporaryDirectory() as str_temp_dir:

        # path_work_dir 是 xsim 工作目录。
        path_work_dir = Path(str_temp_dir)  # 隔离 Vivado xsim work library 的临时目录

        # list_xvlog_command 是 xvlog 编译命令。
        list_xvlog_command = ["xvlog"]  # 编译 all_files 的 xvlog 命令骨架

        # 添加所有 RTL/testbench 文件。
        list_xvlog_command.extend(all_files)

        # xvlog 必须先编译全部 RTL/testbench，失败会阻止 xelab 展开。
        list_xvlog_issues = deps.run_tool(list_xvlog_command, path_work_dir, "xsim xvlog compile", "compile")  # xvlog 对全部 RTL/testbench 的编译诊断

        # xvlog 编译诊断进入后续 elaborate 阻断判断。
        list_issues.extend(list_xvlog_issues)

        # xvlog 出现在证据列表中，表示编译步骤已尝试。
        list_executed_tools.append("xvlog")

        # compile error 时不继续 elaborate。
        if _has_error(list_issues):

            # 返回已获得的 compile 证据。
            return list_issues, list_executed_tools

        # xelab 命令把 testbench 顶层展开成后续 xsim 可运行的 sim_snap。
        list_xelab_command = ["xelab", tb_top, "-s", "sim_snap"]  # 为 tb_top 生成 sim_snap 的 xelab 命令

        # xelab 诊断决定 compile readiness 是否已经拥有可运行快照。
        list_xelab_issues = deps.run_tool(list_xelab_command, path_work_dir, "xsim xelab elaborate", "compile")  # xelab 构建 sim_snap 的展开诊断

        # xelab 展开诊断决定是否能生成可运行快照。
        list_issues.extend(list_xelab_issues)

        # xelab 证据表明 Vivado snapshot 构建已尝试。
        list_executed_tools.append("xelab")

        # execute readiness 需要运行 xsim -R。
        if readiness_at_least(readiness, "execute") and not _has_error(list_issues):

            # list_xsim_command 是历史兼容的批处理运行命令。
            list_xsim_command = ["xsim", "sim_snap", "-R"]  # 运行 sim_snap 的 xsim 批处理命令

            # xsim 运行诊断是 execute readiness 的真实仿真证据。
            list_xsim_issues = deps.run_tool(list_xsim_command, path_work_dir, "xsim simulation", "execute")  # xsim 批处理运行 testbench 的诊断

            # xsim 运行诊断作为 execute readiness 的仿真证据。
            list_issues.extend(list_xsim_issues)

            # xsim 出现在证据列表中，表示 testbench 已尝试运行。
            list_executed_tools.append("xsim")

    # 返回 xsim 诊断和执行工具列表。
    return list_issues, list_executed_tools

# _run_vcs_verdi 执行 VCS/Verdi 后端。
def _run_vcs_verdi(
    all_files: list[str],
    readiness: str,
    deps: ReadinessDeps,
) -> tuple[list[ValidationIssue], list[str]]:
    """运行 VCS/Verdi 后端。

    :param all_files: simulator 需要编译的 RTL 和 testbench 文件。
    :param readiness: 当前请求的 readiness 深度。
    :param deps: 命令执行依赖集合。
    :return: VCS/Verdi 后端诊断列表和已执行工具名列表。
    """

    # VCS/Verdi 分支需要同时保留 Verdi 探测和 VCS 编译/运行问题。
    list_issues: list[ValidationIssue] = []  # Verdi availability 与 VCS 编译/运行诊断集合

    # 商业后端工具记录区分许可证探测、图形环境探测和 VCS 主流程。
    list_executed_tools: list[str] = []  # 商业后端执行过的工具名

    # 临时目录隔离 simv 输出。
    with tempfile.TemporaryDirectory() as str_temp_dir:

        # path_work_dir 隔离 VCS 编译产物和临时镜像。
        path_work_dir = Path(str_temp_dir)  # 隔离 VCS simv 输出的临时目录

        # str_simv_name 兼容 Windows 和 Unix 输出名。
        str_simv_name = "simv.exe" if sys.platform.startswith("win") else "simv"  # 平台相关 VCS 可执行镜像文件名

        # path_simv 是 VCS 输出镜像路径。
        path_simv = path_work_dir / str_simv_name  # VCS -o 参数使用的 simv 输出路径

        # 先检查 verdi 可用性，保持旧后端依赖语义。
        list_verdi_issues = deps.run_tool(["verdi", "-version"], path_work_dir, "Verdi availability check", "compile")  # Verdi 版本探测产生的工具可用性诊断

        # 合并 Verdi 可用性诊断。
        list_issues.extend(list_verdi_issues)

        # Verdi 版本探测已真实执行，后续 VCS 仍可能被阻断。
        list_executed_tools.append("verdi")

        # verdi 缺失时不继续执行 vcs。
        if _has_error(list_issues):

            # Verdi 不可用时结束该后端，避免误报 VCS 证据。
            return list_issues, list_executed_tools

        # list_vcs_command 是基础 VCS 编译命令。
        list_vcs_command = ["vcs", "-full64", "-o", str(path_simv), *all_files]  # VCS 对 all_files 生成 simv 的基础命令

        # execute readiness 使用 -R 直接运行。
        if readiness_at_least(readiness, "execute"):

            # -R 插入位置保持旧命令顺序。
            list_vcs_command.insert(2, "-R")

            # list_vcs_execute_issues 保存 VCS 运行诊断。
            list_vcs_execute_issues = deps.run_tool(list_vcs_command, path_work_dir, "VCS simulation", "execute")  # VCS 带 -R 的仿真运行诊断

            # VCS -R 诊断同时覆盖编译和仿真运行。
            list_issues.extend(list_vcs_execute_issues)

        # compile readiness 只编译。
        else:

            # list_vcs_compile_issues 保存 VCS 编译诊断。
            list_vcs_compile_issues = deps.run_tool(list_vcs_command, path_work_dir, "VCS compile", "compile")  # VCS compile readiness 的编译诊断

            # compile readiness 只吸收 VCS 编译诊断。
            list_issues.extend(list_vcs_compile_issues)

        # vcs 出现在证据列表中，表示该后端主命令已尝试。
        list_executed_tools.append("vcs")

    # 返回 VCS/Verdi 结果。
    return list_issues, list_executed_tools

# _run_iverilog 执行 iverilog 编译和可选 vvp 运行。
def _run_iverilog(
    all_files: list[str],
    readiness: str,
    deps: ReadinessDeps,
) -> tuple[list[ValidationIssue], list[str]]:
    """运行 iverilog/vvp 后端。

    :param all_files: simulator 需要编译的 RTL 和 testbench 文件。
    :param readiness: 当前请求的 readiness 深度。
    :param deps: 命令执行依赖集合。
    :return: iverilog/vvp 后端诊断列表和已执行工具名列表。
    """

    # iverilog 分支需要同时汇总编译镜像构建和 vvp 运行问题。
    list_issues: list[ValidationIssue] = []  # iverilog 编译和 vvp 运行诊断集合

    # 工具证据区分 iverilog 构建和 vvp 运行。
    list_executed_tools: list[str] = []  # Icarus 工具调用记录

    # compile readiness 使用 -tnull 只编译。
    if readiness == "compile":

        # list_command 先保存编译器入口，再按文件类型追加参数。
        list_command = ["iverilog"]  # 使用 -tnull 前的 iverilog 编译命令骨架

        # -tnull 让编译不生成输出镜像。
        list_command.extend(["-tnull", *all_files])

        # -tnull 编译诊断是 compile readiness 的 iverilog 证据。
        list_compile_issues = deps.run_tool(list_command, Path.cwd(), "iverilog compile", "compile")  # iverilog -tnull 静态编译诊断

        # -tnull 编译诊断决定 compile readiness 是否通过。
        list_issues.extend(list_compile_issues)

        # iverilog 编译器已在 compile readiness 中真实调用。
        list_executed_tools.append("iverilog")

        # compile readiness 到此结束。
        return list_issues, list_executed_tools

    # execute readiness 先构建 sim.vvp 再运行 vvp。
    with tempfile.TemporaryDirectory() as str_temp_dir:

        # path_work_dir 是 iverilog 临时目录。
        path_work_dir = Path(str_temp_dir)  # 隔离 sim.vvp 生成的临时目录

        # path_sim_image 是 vvp 可执行镜像。
        path_sim_image = path_work_dir / "sim.vvp"  # iverilog -o 生成的 vvp 镜像路径

        # list_command 是 iverilog 构建命令。
        list_command = ["iverilog"]  # 生成 sim.vvp 前的 iverilog 命令骨架

        # 输出 sim.vvp 并包含所有输入文件。
        list_command.extend(["-o", str(path_sim_image), *all_files])

        # build 步骤必须先生成 sim.vvp，vvp 才能运行 testbench。
        list_build_issues = deps.run_tool(list_command, path_work_dir, "iverilog executable build", "execute")  # iverilog 生成 sim.vvp 的构建诊断

        # build 诊断决定是否存在后续可运行的 sim.vvp。
        list_issues.extend(list_build_issues)

        # iverilog 构建步骤已在 execute readiness 中真实调用。
        list_executed_tools.append("iverilog")

        # build 通过后才运行 vvp。
        if not _has_error(list_build_issues):

            # list_vvp_command 是 testbench 运行命令。
            list_vvp_command = ["vvp", str(path_sim_image)]  # 运行刚生成 sim.vvp 的 vvp 命令

            # list_vvp_issues 保存 testbench 运行阶段诊断。
            list_vvp_issues = deps.run_tool(list_vvp_command, path_work_dir, "vvp testbench", "execute")  # vvp 执行 self-checking testbench 的诊断

            # vvp 诊断是 Icarus execute readiness 的运行证据。
            list_issues.extend(list_vvp_issues)

            # vvp 出现在证据列表中，表示 testbench 镜像已尝试运行。
            list_executed_tools.append("vvp")

    # 返回 iverilog/vvp 执行结果。
    return list_issues, list_executed_tools

# _testbench_top 推导 testbench 顶层模块名。
def _testbench_top(spec: dict[str, Any]) -> str:
    """根据 spec outputs 推导 testbench 顶层名。

    :param spec: 当前 Verilog 模块规格。
    :return: testbench 顶层模块名。
    """

    # outputs 中的 testbench 路径优先。
    for dict_output in spec.get("outputs", []) or []:

        # 非 dict 输出项跳过。
        if not isinstance(dict_output, dict):

            # 继续检查后续输出项。
            continue

        # str_path 是输出 artifact 路径。
        str_path = str(dict_output.get("path", ""))  # 输出路径

        # kind=testbench 或路径含 _tb. 都视为 testbench。
        if dict_output.get("kind") == "testbench" or "_tb." in str_path.lower():

            # str_stem 来自 spec 输出路径的文件名主体，用于 simulator 顶层推导。
            str_stem = Path(str_path).stem  # 由 spec testbench 路径推导出的顶层模块名

            # 非空 stem 直接返回。
            if str_stem:

                # 返回 testbench 顶层名。
                return str_stem

    # 默认回退到 <module>_tb。
    return f"{spec.get('name', 'tb')}_tb"

# _required_tools_for_readiness 生成外部证据要求的工具清单。
def _required_tools_for_readiness(
    readiness: str,
    dict_sim_config: dict[str, Any] | None,
    deps: ReadinessDeps,
) -> tuple[tuple[str, str], ...]:
    """返回 readiness 请求所需的外部工具与阶段。

    :param readiness: 当前请求的 readiness 深度。
    :param dict_sim_config: 可复用的 simulator 配置，缺省时从 deps 读取。
    :param deps: 提供 simulator 配置和后端工具映射的依赖集合。
    :return: 去重后的工具名和所属阶段元组。
    """

    # dict_config 缺省时重新读取 simulator 配置。
    dict_config = dict_sim_config or deps.simulator_config(None)  # readiness 工具要求使用的 simulator 配置

    # list_required 保存去重后的工具阶段元组。
    list_required: list[tuple[str, str]] = []  # 外部工具要求列表

    # compile 及以上需要 simulator backend 工具。
    if readiness_at_least(readiness, "compile"):

        # compile 请求归入 compile，execute/implement 归入 execute。
        str_stage = "compile" if readiness == "compile" else "execute"  # 工具要求所属阶段

        # 每个 priority 后端的工具都作为可能要求列出。
        for str_backend in dict_config["priority"]:

            # 逐工具去重追加。
            _append_backend_tools(list_required, deps.backend_tools(str_backend), str_stage)

    # implement readiness 额外要求 yosys。
    if readiness_at_least(readiness, "implement"):

        # yosys 只在未出现时追加。
        _append_unique_tool(list_required, "yosys", "implement")

    # 返回不可变 tuple 供诊断构造使用。
    return tuple(list_required)

# _append_backend_tools 追加后端工具集合。
def _append_backend_tools(list_required: list[tuple[str, str]], tuple_tools: tuple[str, ...], stage: str) -> None:
    """把后端工具追加到 required 列表并去重。

    :param list_required: 需要原位追加的工具要求列表。
    :param tuple_tools: 当前后端要求的工具命令元组。
    :param stage: 这些工具对应的 readiness 阶段。
    :return: None，结果写回 list_required。
    """

    # 每个工具单独按名称去重。
    for str_tool in tuple_tools:

        # 追加当前工具。
        _append_unique_tool(list_required, str_tool, stage)

# _append_unique_tool 按工具名去重追加。
def _append_unique_tool(list_required: list[tuple[str, str]], tool_name: str, stage: str) -> None:
    """当工具名尚未出现时追加工具阶段元组。

    :param list_required: 需要原位追加的工具要求列表。
    :param tool_name: 候选外部工具命令名。
    :param stage: 工具对应的 readiness 阶段。
    :return: None，结果写回 list_required。
    """

    # set_existing_tools 保存已经出现的工具名。
    set_existing_tools: set[str] = set()  # 已记录工具名集合

    # 从 required 元组中提取工具名，避免重复添加同一命令。
    for str_existing_tool, _ in list_required:

        # 当前已存在工具名加入去重集合。
        set_existing_tools.add(str_existing_tool)

    # 工具名未出现才追加，阶段使用第一次出现的语义。
    if tool_name not in set_existing_tools:

        # 首次出现的工具绑定其最早要求阶段。
        list_required.append((tool_name, stage))

# _external_not_run_errors 把未运行外部验证转换成 error。
def _external_not_run_errors(required_tools: tuple[tuple[str, str], ...], readiness: str) -> list[ValidationIssue]:
    """返回外部工具未运行造成的阻断诊断。

    :param required_tools: readiness 要求的工具名和阶段元组。
    :param readiness: 当前请求的 readiness 深度。
    :return: 每个未运行工具对应的 error 诊断列表。
    """

    # list_issues 保存每个要求工具对应的未运行 error。
    list_issues: list[ValidationIssue] = []  # 未运行外部工具阻断诊断

    # 每个要求工具生成一条明确 error。
    for str_tool, str_stage in required_tools:

        # str_message 绑定具体工具名和 readiness，用于解释缺少真实外部证据。
        str_message = (  # 当前工具缺少真实外部验证证据的阻断说明
            f"External tool {str_tool!r} was not run because readiness "  # 工具名和 readiness 前半句
            f"{readiness!r} requires real external validation evidence."  # readiness 缺证据说明
        )

        # 当前工具的未运行诊断追加到结果集合。
        list_issues.append(
            ValidationIssue(
                "error",
                str_message,
                stage=str_stage,
                source="toolchain_issue",
                tool=str_tool,
            )
        )

    # 返回全部未运行工具诊断。
    return list_issues

# _require_tool 检查 implement 阶段即时依赖。
def _require_tool(tool_name: str, stage: str, list_issues: list[ValidationIssue]) -> bool:
    """检查工具当前是否仍在 PATH 中。

    :param tool_name: 需要即时确认的外部工具命令名。
    :param stage: 工具缺失时归属的 readiness 阶段。
    :param list_issues: 需要原位追加缺失诊断的列表。
    :return: 工具可用时返回 True，否则追加 error 后返回 False。
    """

    # shutil.which 成功表示可以继续执行。
    if shutil.which(tool_name):

        # 工具存在，允许后续命令。
        return True

    # 工具临执行前缺失时追加 error。
    list_issues.append(
        ValidationIssue(
            "error",
            f"External tool {tool_name!r} became unavailable before execution.",
            stage=stage,
            source="toolchain_issue",
            tool=tool_name,
        )
    )

    # 返回 False 阻止调用方继续构造命令。
    return False

# _has_error 判断诊断集合是否包含 error。
def _has_error(list_issues: list[ValidationIssue]) -> bool:
    """返回诊断集合中是否存在 error。

    :param list_issues: 待检查的 validation issues。
    :return: 任一诊断 severity 为 error 时返回 True。
    """

    # severity 精确为 error 才阻断后续外部步骤。
    return any(issue.severity == "error" for issue in list_issues)

# yosys_quote 使用 JSON 字符串规则保护路径空格。
def yosys_quote(path: str) -> str:
    """返回 yosys 命令中安全使用的路径字面量。

    :param path: 需要嵌入 yosys 命令字符串的文件路径。
    :return: JSON 字符串规则转义后的路径。
    """

    # json.dumps 可以生成带引号和转义的可读路径。
    return json.dumps(path)

# short_output 截断工具输出，避免报告无限膨胀。
def short_output(text: str, *, limit: int = 20000) -> str:
    """返回长度受限的工具输出。

    :param text: 外部工具原始输出。
    :param limit: 保留的最大字符数。
    :return: 原始输出或带截断标记的输出摘要。
    """

    # 未超过限制时原样返回。
    if len(text) <= limit:

        # 输出长度安全。
        return text

    # 超长输出追加截断标记。
    return text[:limit] + "\n...<truncated>..."

# timeout_output 从 TimeoutExpired 中提取 stdout/stderr。
def timeout_output(exc: subprocess.TimeoutExpired) -> str:
    """返回超时异常中的 stdout/stderr 摘要。

    :param exc: subprocess 超时异常对象。
    :return: 合并 stdout/stderr 后的可读摘要。
    """

    # list_parts 保存非空输出片段。
    list_parts: list[str] = []  # 超时输出片段

    # stdout/stderr 都可能是 bytes、str 或 None。
    for str_label, obj_stream in (("stdout", exc.stdout), ("stderr", exc.stderr)):

        # bytes 输出需要容错解码。
        if isinstance(obj_stream, bytes):

            # bytes 使用 replacement 保留可读内容。
            str_text = obj_stream.decode(errors="replace")  # 解码后的输出

        # str 或 None 按字符串处理。
        else:

            # None 回退为空字符串。
            str_text = obj_stream or ""  # 文本输出

        # 非空输出进入最终摘要。
        if str_text.strip():

            # 保存带来源标签的片段。
            list_parts.append(f"{str_label}:\n{str_text.strip()}")

    # 用空行分隔 stdout/stderr。
    return "\n\n".join(list_parts)
