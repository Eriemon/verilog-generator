"""提供 validation readiness 阶段的依赖装配与兼容 helper。"""

# future annotations 避免运行期解析复杂 Callable 类型。
from __future__ import annotations

# dataclass 收拢 readiness helper 依赖，避免函数签名继续膨胀。
from dataclasses import dataclass

# Callable 负责描述 readiness 依赖回调签名。
from collections.abc import Callable

# Path 负责 artifact 根目录与命令执行目录类型。
from pathlib import Path

# Any 兼容 simulator 配置和超时异常对象。
from typing import Any

# ValidationIssue 是 readiness helper 对外返回的稳定诊断模型。
from .validation_models import ValidationIssue

# validation_readiness 承载真实外部工具链执行实现。
from .validation_readiness import ReadinessDeps, run_rtl_readiness

# readiness wrapper 保留旧 patch 点使用的 simulator/tool 函数。
from .validation_readiness import backend_tools as func_backend_tools
from .validation_readiness import run_tool as func_run_tool
from .validation_readiness import select_simulator_backend as func_select_simulator_backend

# readiness utility wrappers 兼容旧私有 helper 名称。
from .validation_readiness import short_output as func_short_output
from .validation_readiness import timeout_output as func_timeout_output
from .validation_readiness import yosys_quote as func_yosys_quote

# simulator_config 在本模块中重命名，避免与参数同名。
from .validation_readiness import simulator_config as read_simulator_config

# ReadinessHookSet 汇总 validation_impl 暴露给 readiness helper 的兼容 hook。
@dataclass(frozen=True)
class ReadinessHookSet:
    """保存 readiness helper 需要的兼容回调集合。"""

    # rtl_files 为 readiness 提供 artifact 树里的全量 RTL 候选文件入口。
    rtl_files: Callable[[Path], list[Path]]  # readiness 全量 RTL 候选文件扫描回调

    # rtl_source_files 过滤掉 testbench，供综合/综合前门禁使用。
    rtl_source_files: Callable[[Path], list[Path]]  # 过滤 testbench 后的 RTL 源扫描回调

    # is_testbench 区分 DUT 源和仿真驱动文件。
    is_testbench: Callable[[Path], bool]  # 区分 DUT 源和仿真驱动文件的判断回调

    # select_simulator_backend 选择当前机器可执行的最高优先级后端。
    select_simulator_backend: Callable[[dict[str, Any]], dict[str, Any]]  # 选择可执行后端的兼容回调

    # simulator_config_reader 负责解析本轮 readiness 的 simulator 配置。
    simulator_config_reader: Callable[[dict[str, Any] | None], dict[str, Any]]  # 解析 simulator 配置的兼容回调

    # backend_tools 提供后端需要的命令白名单。
    backend_tools: Callable[[str], tuple[str, ...]]  # 返回后端工具白名单的兼容回调

    # run_tool 承接外部命令执行与测试替身注入。
    run_tool: Callable[[list[str], Path, str, str], list[ValidationIssue]]  # 执行外部命令的兼容回调

# 基于调用方提供的 hook 执行 readiness 阶段。
def run_rtl_readiness_with_hooks(
    spec: dict[str, Any],
    root: Path,
    readiness: str,
    run_external: bool,
    simulator_config: dict[str, Any] | None,
    hook_set: ReadinessHookSet,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """基于调用方提供的 hook 执行 readiness 阶段。

    :param spec: 已归一化的 Verilog 规格。
    :param root: 生成 artifact 根目录。
    :param readiness: 已校验的 readiness 深度。
    :param run_external: 是否允许调用外部工具。
    :param simulator_config: 可选 simulator 配置覆盖。
    :param hook_set: validation_impl 传入的兼容 hook 集合。
    :return: readiness 诊断列表和工具链运行 metrics。
    """

    # readiness_deps_obj_deps 把 facade 兼容 hook 组装成 validation_readiness 约定的依赖对象。
    readiness_deps_obj_deps: ReadinessDeps = ReadinessDeps(  # readiness 执行阶段使用的依赖合同
        rtl_files=hook_set.rtl_files,  # 让 readiness 拿到 artifact 树里的全部 RTL 候选文件
        rtl_source_files=hook_set.rtl_source_files,  # 为综合/仿真路径过滤出非 testbench RTL 源文件
        is_testbench=hook_set.is_testbench,  # 判断当前文件是否属于 testbench 侧逻辑

        # 后端选择和命令执行继续走 validation_impl 暴露的兼容 hook。
        select_simulator_backend=hook_set.select_simulator_backend,  # 选择当前机器实际可执行的 simulator 后端
        simulator_config=hook_set.simulator_config_reader,  # 读取本轮 readiness 要使用的 simulator 配置
        backend_tools=hook_set.backend_tools,  # 查询所选后端对应的外部工具白名单
        run_tool=hook_set.run_tool,  # 通过 facade patch 点执行外部命令
    )

    # 委托 readiness helper 执行具体工具链逻辑。
    return run_rtl_readiness(spec, root, readiness, run_external, simulator_config, readiness_deps_obj_deps)

# 返回 simulator 配置。
def _simulator_config(simulator_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回 simulator 配置。

    :param simulator_config: 可选调用方配置覆盖。
    :return: readiness helper 归一化后的 simulator 配置。
    """

    # 委托 readiness helper 读取配置。
    return read_simulator_config(simulator_config)

# 返回可用 simulator 后端。
def _select_simulator_backend(sim_config: dict[str, Any]) -> dict[str, Any]:
    """返回可用 simulator 后端。

    :param sim_config: 已归一化的 simulator 配置。
    :return: readiness helper 选出的后端描述。
    """

    # 委托 readiness helper 选择后端。
    return func_select_simulator_backend(sim_config)

# 返回 simulator 后端工具集合。
def _backend_tools(name: str) -> tuple[str, ...]:
    """返回 simulator 后端工具集合。

    :param name: simulator 后端名称。
    :return: 后端需要的外部工具命令名集合。
    """

    # 委托 readiness helper 返回工具列表。
    return func_backend_tools(name)

# 执行外部命令。
def _run_tool(command: list[str], root: Path, label: str, stage: str) -> list[ValidationIssue]:
    """执行外部命令。

    :param command: 将要执行的命令和参数。
    :param root: 命令执行所依附的 artifact 根目录。
    :param label: 诊断中使用的工具标签。
    :param stage: validation 阶段名称。
    :return: 外部命令失败或缺工具时产生的 ValidationIssue 列表。
    """

    # 委托 readiness helper 执行外部工具。
    return func_run_tool(command, root, label, stage)

# 返回 yosys 命令可用的路径字面量。
def _yosys_quote(path: str) -> str:
    """返回 yosys 命令可用的路径字面量。

    :param path: 需要嵌入 yosys 脚本的路径文本。
    :return: readiness helper 生成的安全引用文本。
    """

    # 委托 readiness helper 进行 JSON quoting。
    return func_yosys_quote(path)

# 返回截断后的工具输出。
def _short_output(text: str, *, limit: int = 20000) -> str:
    """返回截断后的工具输出。

    :param text: 原始外部工具输出。
    :param limit: 保留的最大字符数。
    :return: 截断后的输出文本。
    """

    # 委托 readiness helper 截断输出。
    return func_short_output(text, limit=limit)

# 返回超时异常中的 stdout/stderr。
def _timeout_output(exc: Any) -> str:
    """返回超时异常中的 stdout/stderr。

    :param exc: subprocess 超时异常或兼容对象。
    :return: readiness helper 提取出的超时输出文本。
    """

    # 委托 readiness helper 提取超时输出。
    return func_timeout_output(exc)
