"""提供 Verilog artifact 验证流程的稳定兼容入口。"""

# future annotations 避免运行期解析 Path 与 Any 组合类型。
from __future__ import annotations

# Path 是公开 validate_generated API 的路径输入类型。
from collections.abc import Callable
from pathlib import Path
from typing import Any

# validation_impl 承载历史验证实现，本模块负责保持公开 API 和 mock patch 点稳定。
from . import validation_impl as _impl

# READINESS_LEVELS 暴露给 CLI parser 和 preflight 脚本。
READINESS_LEVELS = _impl.READINESS_LEVELS  # readiness 等级的公开兼容元组

# ERROR_SOURCES 供报告消费者识别 issue 来源。
ERROR_SOURCES = _impl.ERROR_SOURCES  # validation issue 来源枚举

# ValidationIssue 保持历史 dataclass 类型，避免旧报告断言失效。
ValidationIssue = _impl.ValidationIssue  # 单条验证问题的数据模型

# ValidationReport 保持历史 dataclass 类型，避免 facade 返回类型变化。
ValidationReport = _impl.ValidationReport  # 验证结果报告的数据模型

# 原始实现函数用于避免 hook 注入后的递归委派。
func__original_simulator_config: Callable[..., Any] = _impl._simulator_config  # 原始仿真配置解析函数

# 原始选择函数用于 wrapper 默认行为。
func__original_select_simulator_backend: Callable[..., Any] = _impl._select_simulator_backend  # 原始仿真后端选择函数

# 原始工具列表函数用于 wrapper 默认行为。
func__original_backend_tools: Callable[..., Any] = _impl._backend_tools  # 原始后端工具映射函数

# 原始命令执行函数用于 wrapper 默认行为。
func__original_run_tool: Callable[..., Any] = _impl._run_tool  # 原始外部工具执行函数

# require_readiness 保持 readiness 参数的旧入口。
def require_readiness(readiness: str) -> str:
    """校验 readiness 等级并返回规范化字符串。

    参数:
        readiness: 用户请求的验证阶段名称。

    返回:
        实现模块认可的 readiness 字符串。
    """

    # readiness 规则继续由实现模块维护，避免双份常量漂移。
    return _impl.require_readiness(readiness)

# readiness_at_least 供 CLI 和 integration 判断外部工具要求。
def readiness_at_least(readiness: str, stage: str) -> bool:
    """判断当前 readiness 是否已经覆盖目标阶段。

    参数:
        readiness: 当前请求的验证阶段。
        stage: 需要比较的目标阶段。

    返回:
        readiness 等级不低于目标阶段时返回 True。
    """

    # 阶段顺序仍以实现模块为唯一来源。
    return _impl.readiness_at_least(readiness, stage)

# validate_generated 是所有本地 artifact 质量门的公开入口。
def validate_generated(
    spec: dict[str, Any],
    path: Path,
    target: str | None = None,
    **kwargs: Any,
) -> ValidationReport:
    """运行 Verilog 生成产物验证并返回结构化报告。

    :param spec: 已确认或待归一化的 Verilog 规格映射。
    :param path: 生成产物所在目录。
    :param target: 兼容旧调用方的目标类型，当前只接受 RTL。
    :param kwargs: 兼容旧调用方的关键字选项，包括 run_external、readiness、
        comment_language、semantic_contract、simulator_config 和 strict_generated_comments。
    :return: 保持历史字段结构的 ValidationReport。
    """

    # run_external 控制是否允许本地外部仿真或综合工具执行。
    bool_run_external = kwargs.pop("run_external", True)  # 外部验证开关

    # readiness 描述验证流程需要达到的阶段深度。
    str_readiness = kwargs.pop("readiness", "static")  # 验证阶段要求

    # comment_language 传递给生成 Verilog 注释语言检查。
    str_comment_language = kwargs.pop("comment_language", "zh")  # 注释语言要求

    # semantic_contract 用于比对 Python semantic 与 transcript。
    dict_semantic_contract = kwargs.pop("semantic_contract", None)  # 语义合同配置

    # simulator_config 描述调用方提供的仿真器优先级。
    dict_simulator_config = kwargs.pop("simulator_config", None)  # 仿真器优先级配置

    # strict_generated_comments 决定生成注释问题是否阻断验证。
    bool_strict_generated_comments = kwargs.pop("strict_generated_comments", True)  # 生成注释严格开关

    # 实现模块调用私有函数时需要看见当前模块可能被测试 patch 的 wrapper。
    tuple_previous_hooks = _install_patchable_backend_hooks()  # validation_impl 原始 hook 备份

    # 调用历史实现，并确保 finally 恢复模块级 hook。
    try:

        # validation_request 聚合实现层参数，公开 API 继续保持兼容 kwargs 入口。
        validation_request_validation_request: _impl.ValidationRequest = _impl.ValidationRequest(  # validation_impl 内部执行请求
            spec=spec,  # 原始 Verilog 规格
            path=path,  # 生成 artifact 根目录
            target=target,  # 兼容旧调用方的目标后端

            # 执行策略参数决定静态 gate 之后是否进入外部工具阶段。
            run_external=bool_run_external,  # 是否允许外部工具执行
            readiness=str_readiness,  # validation 阶段深度
            comment_language=str_comment_language,  # RTL 注释语言策略

            # 合同和环境参数传入实现层，保持 facade patch 点可控。
            semantic_contract=dict_semantic_contract,  # reference 语义合同
            simulator_config=dict_simulator_config,  # simulator 配置覆盖
            strict_generated_comments=bool_strict_generated_comments,  # 注释 gate 严格模式
        )

        # 所有实际 gate 仍由实现模块执行，本入口只维护兼容边界。
        return _impl.validate_generated(validation_request_validation_request)

    # 验证结束后必须恢复 validation_impl 中的可 patch hook。
    finally:

        # 恢复实现模块 hook，避免一次测试 patch 泄漏到后续验证。
        _restore_patchable_backend_hooks(tuple_previous_hooks)

# _simulator_config 保持 existing_rtl_improvement 的私有导入兼容。
def _simulator_config(simulator_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回验证流程使用的仿真器选择配置。

    参数:
        simulator_config: 调用方显式提供的仿真器配置。

    返回:
        实现模块整理后的仿真器选择配置。
    """

    # 真实配置解析仍由实现模块处理。
    return func__original_simulator_config(simulator_config)

# _select_simulator_backend 是测试和 improvement 依赖的可 patch 选择点。
def _select_simulator_backend(dict_sim_config: dict[str, Any]) -> dict[str, Any]:
    """选择当前机器可用的最高优先级仿真后端。

    参数:
        dict_sim_config: 已解析的仿真器优先级配置。

    返回:
        当前机器可执行的仿真后端描述。
    """

    # 默认 wrapper 直接委派；单测 patch 本函数时 validate_generated 会把 patch 转给实现模块。
    return func__original_select_simulator_backend(dict_sim_config)

# _backend_tools 保持已有 RTL refine 流程的工具列表查询兼容。
def _backend_tools(name: str) -> tuple[str, ...]:
    """返回指定仿真后端需要的外部工具名称。

    参数:
        name: 仿真后端名称。

    返回:
        后端执行所需的外部工具名称元组。
    """

    # 工具映射仍由实现模块维护，避免配置漂移。
    return func__original_backend_tools(name)

# _run_tool 是外部工具执行的测试替身插入点。
def _run_tool(command: list[str], root: Path, label: str, stage: str) -> list[ValidationIssue]:
    """执行外部工具命令并转换为 validation issue。

    参数:
        command: 需要执行的外部工具命令参数。
        root: 外部工具运行的工作目录。
        label: 报告中展示的工具标签。
        stage: 当前验证阶段名称。

    返回:
        外部工具输出转换得到的验证问题列表。
    """

    # 默认 wrapper 直接委派；单测 patch 本函数时 readiness 路径会使用 patch 后对象。
    return func__original_run_tool(command, root, label, stage)

# _install_patchable_backend_hooks 把当前模块 hook 注入历史实现。
def _install_patchable_backend_hooks() -> tuple[Any, Any, Any, Any]:
    """安装可 patch hook，并返回实现模块原始对象。

    参数:
        无外部业务参数。

    返回:
        validation_impl 调用前的四个 hook 对象。
    """

    # 保存当前后端选择入口，防止一次 mock 泄漏到后续 readiness 用例。
    func_previous_select: Callable[..., Any] = _impl._select_simulator_backend  # 恢复时重新接回默认后端探测逻辑

    # 保存当前命令执行入口，保证外部工具 mock 只覆盖本次调用。
    func_previous_run_tool: Callable[..., Any] = _impl._run_tool  # 恢复时重新接回 subprocess 执行路径

    # 保存当前工具映射入口，避免 improvement 查询被测试替身污染。
    func_previous_backend_tools: Callable[..., Any] = _impl._backend_tools  # 恢复时重新接回 xsim/vcs/iverilog 工具表

    # 保存当前配置解析入口，确保环境变量优先级恢复到调用前。
    func_previous_simulator_config: Callable[..., Any] = _impl._simulator_config  # 恢复时重新接回 settings/env 优先级解析

    # 注入当前模块中的 wrapper 或测试 patch 后对象。
    _impl._select_simulator_backend = _select_simulator_backend  # 本次验证使用的后端选择 hook

    # 注入工具执行 hook，使 unittest.mock.patch 可拦截真实 readiness 命令。
    _impl._run_tool = _run_tool  # 本次验证使用的外部工具执行 hook

    # 注入后端工具映射，保持私有兼容函数一致。
    _impl._backend_tools = _backend_tools  # 本次验证使用的工具映射 hook

    # 注入仿真配置入口，保持调用链可被同一模块观测。
    _impl._simulator_config = _simulator_config  # 本次验证使用的配置解析 hook

    # 返回完整备份元组给 finally 恢复。
    return (
        func_previous_select,
        func_previous_run_tool,
        func_previous_backend_tools,
        func_previous_simulator_config,
    )

# _restore_patchable_backend_hooks 还原 validation_impl 的模块级 hook。
def _restore_patchable_backend_hooks(tuple_previous_hooks: tuple[Any, Any, Any, Any]) -> None:
    """恢复 validation_impl 在本次验证前的 hook 对象。

    参数:
        tuple_previous_hooks: validate_generated 调用前保存的 hook 备份。

    返回:
        无返回值。
    """

    # 第一个元素是调用前的后端选择入口。
    func_previous_select: Callable[..., Any] = tuple_previous_hooks[0]  # 待恢复的后端选择函数

    # 第二个元素是调用前的外部工具执行入口。
    func_previous_run_tool: Callable[..., Any] = tuple_previous_hooks[1]  # 待恢复的工具执行函数

    # 第三个元素是调用前的后端工具映射入口。
    func_previous_backend_tools: Callable[..., Any] = tuple_previous_hooks[2]  # 待恢复的工具映射函数

    # 第四个元素是调用前的仿真配置入口。
    func_previous_simulator_config: Callable[..., Any] = tuple_previous_hooks[3]  # 待恢复的配置解析函数

    # 还原仿真后端选择函数。
    _impl._select_simulator_backend = func_previous_select  # 恢复调用前的后端选择 hook

    # 还原外部工具执行函数。
    _impl._run_tool = func_previous_run_tool  # 恢复调用前的命令执行 hook

    # 还原工具列表函数。
    _impl._backend_tools = func_previous_backend_tools  # 恢复调用前的工具映射 hook

    # 还原仿真配置函数。
    _impl._simulator_config = func_previous_simulator_config  # 恢复调用前的配置解析 hook
