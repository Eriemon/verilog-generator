"""
管理 readable-verilog-generator 的可选 skill 依赖。

stdout_protocol: mixed
本 CLI 的 JSON 类子命令向标准输出写入 JSON object；prompt 子命令输出面向用户的安装提示文本。
"""

# future annotations 避免运行期解析复杂类型标注。
from __future__ import annotations

# 标准库负责 CLI 解析、stdout 协议和兼容 patch 面保留。
import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

# 当前脚本位于 skill 主体 scripts 目录内。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 当前 skill 主体目录文本路径

# ensure_skill_root_on_path 只为 facade 导入拆分后的 toolchain 子模块准备路径。
def ensure_skill_root_on_path() -> None:
    """确保脚本直运行时可以导入拆分后的 toolchain 子模块。

    :param: 本函数不接收业务参数；只检查当前进程的 import path。
    :return: 不返回业务值；必要时把 skill 根目录放入 sys.path 首位。
    """

    # str_skill_root 保存 skill 根目录的字符串形式，便于和 sys.path 比较。
    str_skill_root = str(PATH_SKILL_ROOT)  # toolchain 子模块所在的 skill 根目录文本

    # 缺失 skill 根目录时才修改 sys.path，避免重复插入同一路径。
    if str_skill_root not in sys.path:

        # facade 导入拆分子模块前把 skill 根目录放入搜索路径。
        sys.path.insert(0, str_skill_root)

# 子模块拆分后仍要允许按脚本路径动态加载本 facade。
ensure_skill_root_on_path()

# toolchain 逻辑按职责拆分，facade 只保留 CLI 与兼容包装层。
from scripts.python.toolchain import dependency_fpga_route as module_dependency_fpga_route
from scripts.python.toolchain import dependency_install as module_dependency_install
from scripts.python.toolchain import dependency_state as module_dependency_state

# facade 兼容常量直接复用路由子模块的共享集合，避免两份旧名单分叉。
TUPLE_FPGA_AGENT_CHILD_SKILLS = module_dependency_fpga_route.TUPLE_FPGA_AGENT_CHILD_SKILLS  # facade 兼容的旧 FPGA-Agent 子技能集合

# FPGA_AGENT_CHILD_SKILLS 继续保留旧公开常量名，兼容历史读取方。
FPGA_AGENT_CHILD_SKILLS = TUPLE_FPGA_AGENT_CHILD_SKILLS  # 兼容旧调用方的 FPGA-Agent 子技能集合

# read_settings_file 继续通过 facade 暴露 defaults.json 读取入口。
def read_settings_file(path_settings: Path) -> dict[str, Any]:
    """读取 skill defaults.json 配置文件。

    :param path_settings: defaults.json 或测试替代配置文件路径。
    :return: 返回解析后的配置字典。
    数组合同：本函数返回配置字典；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_state 读取 settings 文件，保持旧调用入口不变。
    return module_dependency_state.read_settings_file(path_settings)

# read_skill_dependency_settings 继续通过 facade 暴露依赖配置解析入口。
def read_skill_dependency_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """读取 skill 依赖治理配置。

    :param settings: 已加载的 defaults.json 配置字典。
    :return: 返回 skill_dependencies 分区的规范化字典。
    数组合同：本函数返回配置字典；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_state 解析 skill_dependencies 分区。
    return module_dependency_state.read_skill_dependency_settings(settings)

# read_tool_dependency_settings 继续通过 facade 暴露 npm/Node 工具配置入口。
def read_tool_dependency_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """读取外部工具依赖治理配置。

    :param settings: 已加载的 defaults.json 配置字典。
    :return: 返回 tool_dependencies 分区的规范化字典。
    数组合同：本函数返回配置字典；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_state 解析固定 WaveDrom 版本合同。
    return module_dependency_state.read_tool_dependency_settings(settings)

# default_skills_root 继续通过 facade 暴露默认 skills 根目录入口。
def default_skills_root() -> Path:
    """返回默认 Codex skills 根目录。

    :param: 本函数不接收业务参数；只读取 CODEX_HOME 环境变量。
    :return: CODEX_HOME/skills 或默认用户配置目录下的 skills。
    """

    # 委托 dependency_state 计算默认 skills 根目录。
    return module_dependency_state.default_skills_root()

# default_plugin_cache 负责把插件缓存根继续暴露给依赖发现和插件随附 skill 扫描。
def default_plugin_cache() -> Path:
    """返回默认 Codex plugin cache 根目录。

    :param: 本函数不接收业务参数；只读取 CODEX_HOME 环境变量。
    :return: CODEX_HOME/plugins/cache 或默认用户配置目录下的插件缓存。
    """

    # 让 dependency_state 统一计算插件缓存根，保持发现口径一致。
    return module_dependency_state.default_plugin_cache()

# check_dependencies 继续通过 facade 暴露依赖状态汇总入口。
def check_dependencies(
    settings: dict[str, Any],
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """检查当前 Codex 环境中的 skill 依赖安装状态。

    :param settings: 已加载的 defaults.json 配置；包含依赖组、版本和路由策略。
    :param skills_root: 可选 Codex skills 根目录；测试可传临时目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录；用于发现插件内 skills。
    :param state_path: 可选依赖状态文件路径；用于跳过记录和 vendor 选择。
    :return: 返回保持旧 JSON 字段合同的依赖报告。
    数组合同：本函数返回 JSON object；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_state 生成依赖报告，保持 facade 调用面稳定。
    return module_dependency_state.check_dependencies(
        settings,
        skills_root=skills_root,
        plugin_cache=plugin_cache,
        state_path=state_path,
    )

# prompt_for_missing 继续通过 facade 暴露用户提示渲染入口。
def prompt_for_missing(report: dict[str, Any]) -> str:
    """根据依赖报告渲染用户可读提示文本。

    :param report: check_dependencies 生成的依赖报告。
    :return: 返回可直接展示给用户的提示文本。
    数组合同：本函数返回字符串提示；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_state 渲染缺失依赖提示文本。
    return module_dependency_state.prompt_for_missing(report)

# record_skip 继续通过 facade 暴露 recommended skip 写入入口。
def record_skip(
    settings: dict[str, Any],
    dependency_id: str,
    *,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """把 recommended 依赖标记为当前配置版本已跳过。

    :param settings: 已加载的 defaults.json 配置。
    :param dependency_id: 要跳过的 recommended 依赖 id。
    :param state_path: 可选状态文件路径；测试可传临时文件。
    :return: 返回写入后的状态字典。
    数组合同：本函数返回状态 JSON object；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_state 写回 recommended skip 状态。
    return module_dependency_state.record_skip(
        settings,
        dependency_id,
        state_path=state_path,
    )

# adapt_dependencies 继续通过 facade 暴露 helper 路径适配入口。
def adapt_dependencies(
    settings: dict[str, Any],
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """发现已安装依赖的 helper 路径并写入状态文件。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 adapted、blocked 和 state_path 字段。
    数组合同：本函数返回适配摘要 JSON object；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_install 写回 helper 路径适配结果。
    return module_dependency_install.adapt_dependencies(
        settings,
        skills_root=skills_root,
        plugin_cache=plugin_cache,
        state_path=state_path,
    )

# fpga_developer_status 继续通过 facade 暴露 vendor 状态汇总入口。
def fpga_developer_status(
    settings: dict[str, Any],
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """汇总 FPGA developer skill 的 vendor 可用性和用户选择状态。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 vendor、available_vendors、selection_required 等路由字段。
    数组合同：本函数返回路由状态 JSON object；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_fpga_route 汇总 vendor developer skill 状态。
    return module_dependency_fpga_route.fpga_developer_status(
        settings,
        skills_root=skills_root,
        plugin_cache=plugin_cache,
        state_path=state_path,
    )

# select_fpga_vendor 继续通过 facade 暴露 vendor 选择写入入口。
def select_fpga_vendor(
    settings: dict[str, Any],
    vendor_id: str,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """保存用户选择的 FPGA developer vendor。

    :param settings: 已加载的 defaults.json 配置。
    :param vendor_id: 用户选择的 vendor id。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 selected_vendor、selected_skill 和 state_path。
    数组合同：本函数返回选择摘要 JSON object；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_fpga_route 写回 vendor 选择结果。
    return module_dependency_fpga_route.select_fpga_vendor(
        settings,
        vendor_id,
        skills_root=skills_root,
        plugin_cache=plugin_cache,
        state_path=state_path,
    )

# fpga_route 继续通过 facade 暴露 FPGA workflow 路由查询入口。
def fpga_route(
    settings: dict[str, Any],
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """报告当前 FPGA workflow 应使用的 developer skill 或 fallback 状态。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 ready、selection_required、selection_stale 或 manual_fallback_available 状态。
    数组合同：本函数返回路由摘要 JSON object；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_fpga_route 生成当前 FPGA workflow 路由结果。
    return module_dependency_fpga_route.fpga_route(
        settings,
        skills_root=skills_root,
        plugin_cache=plugin_cache,
        state_path=state_path,
    )

# print_json 继续通过 facade 暴露 machine-readable JSON 输出入口。
def print_json(payload: dict[str, Any]) -> None:
    """向 stdout 写出机器可读 JSON object。

    :param payload: 需要输出给上层程序解析的 JSON object。
    :return: 不返回业务值；JSON 文本写入 stdout。
    数组合同：本函数输出 JSON 文本到 stdout；shape、dtype 和 unit 不适用。
    """

    # 委托 dependency_state 输出 JSON，保持旧 stdout 协议不变。
    module_dependency_state.print_json(payload)

# install_missing 保留 facade 上的 subprocess patch 面，再委托安装子模块执行。
def install_missing(
    settings: dict[str, Any],  # defaults.json 依赖配置来源
    # report 与 check_dependencies 的字段合同保持一致。
    report: dict[str, Any],  # 当前依赖缺失报告
    # dependency_id 允许 CLI 只处理一个依赖。
    dependency_id: str | None = None,  # 单依赖过滤条件
    *,
    # installer 允许测试注入可控 helper。
    installer: Path | None = None,  # skill-installer helper 路径
    # fallback 默认关闭以阻止隐式安装。
    allow_fpga_agent_fallback: bool = False,  # FPGA-Agent fallback 开关
    # confirm 由 CLI 显式传入外部副作用确认。
    confirm: bool = False,  # npm/Node 工具安装确认
) -> dict[str, Any]:  # 安装摘要字典
    """安装报告中缺失的依赖 skill，并保留 facade 上的 subprocess patch 面。

    :param settings: 已加载的 defaults.json 配置。
    :param report: check_dependencies 生成的依赖报告。
    :param dependency_id: 可选依赖 id；为空时安装全部缺失 required/recommended。
    :param installer: 可选 skill-installer helper 路径。
    :param allow_fpga_agent_fallback: 是否允许安装 FPGA-Agent 手动 fallback。
    :param confirm: 是否已由 CLI 显式确认外部工具安装副作用。
    :return: 返回 installed、skipped 和 restart_required 等安装摘要。
    数组合同：本函数返回安装摘要 JSON object；shape、dtype 和 unit 不适用。
    """

    # facade patch 面需要同步到安装子模块，保持测试 monkey patch 行为不变。
    module_dependency_install.subprocess = subprocess  # 把 facade 上可 patch 的 subprocess 句柄同步给安装子模块

    # 委托 dependency_install 执行实际安装逻辑。
    return module_dependency_install.install_missing(
        settings,
        report,
        dependency_id,
        installer=installer,
        allow_fpga_agent_fallback=allow_fpga_agent_fallback,
        confirm=confirm,
    )

# cleanup_fpga_agent_skills 保留 facade 上的时间与旧公开常量兼容面。
def cleanup_fpga_agent_skills(
    settings: dict[str, Any],
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    backup_root: Path | None = None,
    yes: bool = False,
) -> dict[str, Any]:
    """在 developer skill 可用后迁移旧 FPGA-Agent 子技能目录，并保留 facade 兼容常量。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param backup_root: 可选备份根目录。
    :param yes: 是否确认执行移动操作。
    :return: 返回 moved、backup_dir、skills_root、plugin_cache 和 developer_vendors。
    数组合同：本函数返回 cleanup 摘要 JSON object；shape、dtype 和 unit 不适用。
    """

    # cleanup 继续同步 facade 上的时间函数和旧公开常量，保持历史兼容面稳定。
    module_dependency_fpga_route.time = time  # cleanup 使用的时间函数同步到子模块

    # cleanup 继续同步 tuple 常量，保证旧 FPGA-Agent 子技能集合读取口径一致。
    module_dependency_fpga_route.TUPLE_FPGA_AGENT_CHILD_SKILLS = TUPLE_FPGA_AGENT_CHILD_SKILLS  # tuple 常量同步到子模块

    # cleanup 继续同步兼容常量名，保证历史读取方看到相同的子技能集合。
    module_dependency_fpga_route.FPGA_AGENT_CHILD_SKILLS = FPGA_AGENT_CHILD_SKILLS  # 兼容常量同步到子模块

    # 委托 dependency_fpga_route 执行实际 cleanup 逻辑。
    return module_dependency_fpga_route.cleanup_fpga_agent_skills(
        settings,
        skills_root=skills_root,
        plugin_cache=plugin_cache,
        backup_root=backup_root,
        yes=yes,
    )

# main 是命令行入口，负责解析参数和分派子命令。
class CliContext(NamedTuple):
    """保存 CLI 解析后的共享上下文，避免各子命令重复计算路径。"""

    # parser 负责渲染 CLI 错误和安装确认错误。
    parser: argparse.ArgumentParser  # 依赖治理 CLI parser

    # namespace_args 保存当前子命令及其选项。
    namespace_args: argparse.Namespace  # argparse 解析后的子命令参数

    # dict_settings 保存 defaults.json 的依赖治理配置。
    dict_settings: dict[str, Any]  # 当前 skill 的依赖配置字典

    # path_skills_root 指向可选 skill 的扫描根目录。
    path_skills_root: Path  # 本次依赖扫描的 skills 根目录

    # path_plugin_cache 指向插件随附 skill 的缓存根目录。
    path_plugin_cache: Path  # Codex 插件缓存目录

    # path_state 指向依赖状态持久化文件。
    path_state: Path  # 依赖治理状态文件路径

# _load_cli_context 解析 CLI 并加载共享配置路径。
def _load_cli_context(argv: list[str] | None) -> CliContext:
    """解析 CLI 并加载所有子命令共享的配置与路径。

    :param argv: 可选命令行参数列表；为 None 时使用 argparse 默认的 sys.argv。
    :return: 返回包含 parser、参数、配置和路径的 CLI 上下文。
    :raises AssertionError: 当 parser.error 异常路径意外返回时抛出。
    """

    # CLI parser 集中声明所有子命令，入口只负责分派。
    parser = build_parser()  # 依赖治理 CLI 解析器

    # argparse.Namespace 保留子命令和可选路径参数。
    namespace_args: argparse.Namespace = parser.parse_args(argv)  # 本次 CLI 调用参数

    # settings 文件提供依赖组、状态路径和 FPGA 路由策略。
    dict_settings: dict[str, Any] = read_settings_file(namespace_args.settings)  # skill 治理配置

    # skills_root 可由测试或 CLI 显式覆盖。
    path_skills_root = namespace_args.skills_root or default_skills_root()  # 本次依赖扫描使用的 skills 根目录

    # 从 CLI 或默认 helper 解析插件缓存根目录。
    path_plugin_cache = namespace_args.plugin_cache or default_plugin_cache()  # 当前插件 skill 的缓存根目录

    # state_path 可能来自 CLI 覆盖或 defaults.json。
    try:

        # 依赖状态路径在所有写状态子命令间共享。
        path_state = namespace_args.state_path or read_skill_dependency_settings(dict_settings)["state_path"]  # 依赖状态文件路径

    # 配置错误应交给 argparse 以 CLI 方式展示。
    except ValueError as exc:

        # parser.error 会打印 usage 并抛出 SystemExit。
        parser.error(str(exc))

        # parser.error 正常不会返回；该异常只保护类型检查和异常链。
        raise AssertionError("> ERR: [Python] parser.error returned unexpectedly.") from exc

    # 返回所有子命令共享的上下文，避免重复解析路径。
    return CliContext(
        parser,
        namespace_args,
        dict_settings,
        path_skills_root,
        path_plugin_cache,
        path_state,
    )

# _run_query_command 处理只读查询子命令。
def _run_query_command(context: CliContext) -> int | None:
    """执行不改变依赖状态的 check、prompt 或 fpga-route 子命令。

    :param context: 已解析的 CLI 共享上下文。
    :return: 已处理时返回 0；当前命令不属于查询命令时返回 None。
    """

    # 读取命令名称，后续分支只按 parser 白名单分派。
    str_command = context.namespace_args.command  # 当前依赖治理子命令名称

    # check 子命令输出完整 JSON 报告。
    if str_command == "check":

        # 依赖报告保留旧 JSON 字段合同。
        dict_report = check_dependencies(  # check 子命令完整依赖报告
            context.dict_settings,  # defaults.json 解析后的治理配置
            skills_root=context.path_skills_root,  # 本次扫描的 skills 根目录
            plugin_cache=context.path_plugin_cache,  # 本次扫描的插件缓存目录
            state_path=context.path_state,  # 本次读取的依赖状态文件
        )

        # JSON stdout 是该 CLI 的显式机器可读协议。
        print_json(dict_report)

        # readiness 不满足时返回一，JSON 仍保留完整机器协议。
        return 0 if bool(dict_report.get("ok")) else 1

    # prompt 子命令输出供 agent 展示给用户的安装提示。
    if str_command == "prompt":

        # prompt 先复用 check_dependencies，避免提示和 JSON 报告口径漂移。
        dict_report = check_dependencies(  # prompt 渲染前的依赖报告
            context.dict_settings,  # prompt 复用依赖配置以生成同口径提示
            skills_root=context.path_skills_root,  # prompt 需要展示的 skills 扫描根
            plugin_cache=context.path_plugin_cache,  # prompt 需要展示的插件缓存根
            state_path=context.path_state,  # skip 和 vendor 状态来源文件
        )

        # prompt 文本保留原子命令 stdout 合同。
        sys.stdout.write(prompt_for_missing(dict_report) + "\n")

        # prompt 只负责渲染提示，不因缺失依赖返回失败。
        return 0

    # fpga-route 子命令只报告当前路由状态。
    if str_command == "fpga-route":

        # 路由报告不写状态，除非选择子命令已提前执行。
        dict_route = fpga_route(  # workflow 路由查询结果
            context.dict_settings,  # 路由查询需要配置中的 vendor 策略
            skills_root=context.path_skills_root,  # 路由查询使用的 skills 根目录
            plugin_cache=context.path_plugin_cache,  # 路由查询使用的插件缓存根
            state_path=context.path_state,  # 已保存 vendor 选择来源文件
        )

        # 路由状态使用 JSON stdout 暴露给上层命令。
        print_json(dict_route)

        # fpga-route 查询成功后返回 0。
        return 0

    # 当前命令不属于只读查询命令，交给后续分派器处理。
    return None

# _run_skip_command 记录用户跳过的依赖。
def _run_skip_command(context: CliContext) -> int:
    """记录用户跳过的依赖并返回机器可读摘要。

    :param context: 已解析的 skip 子命令上下文。
    :return: 成功记录后的进程退出码 0。
    """

    # 依赖编号来自 argparse 的必选参数。
    str_dependency_id = context.namespace_args.dependency_id  # 用户跳过的依赖编号

    # 状态文件写入由 record_skip 统一处理。
    record_skip(  # 将 skip 记录持久化到依赖状态文件
        context.dict_settings,  # 当前 skill 的依赖配置
        str_dependency_id,  # 本次跳过的依赖编号
        state_path=context.path_state,  # skip 记录写入的依赖状态文件
    )

    # skip 结果仍输出机器可读摘要。
    print_json({"skipped": str_dependency_id, "state_path": str(context.path_state)})

    # skip 写入成功后返回 0。
    return 0

# _run_select_fpga_vendor_command 记录 FPGA vendor 选择。
def _run_select_fpga_vendor_command(context: CliContext) -> int:
    """记录 FPGA vendor 选择并输出后续路由所需的状态。

    :param context: 已解析的 select-fpga-vendor 子命令上下文。
    :return: 成功保存 vendor 选择后的进程退出码 0。
    """

    # vendor 选择会写入 dependency state。
    dict_selection = select_fpga_vendor(  # vendor 选择写入结果
        context.dict_settings,  # vendor 写入前读取配置中的厂商映射
        context.namespace_args.vendor_id,  # 用户指定的 FPGA vendor id
        skills_root=context.path_skills_root,  # 用于验证 vendor skill 是否安装
        plugin_cache=context.path_plugin_cache,  # 用于验证插件内 vendor skill
        state_path=context.path_state,  # vendor 选择持久化目标文件
    )

    # 选择结果使用 JSON stdout 供上层自动读取。
    print_json(dict_selection)

    # vendor 选择成功后返回 0。
    return 0

# _run_adapt_command 刷新已安装依赖的辅助路径。
def _run_adapt_command(context: CliContext) -> int:
    """刷新已安装依赖的辅助路径状态。

    :param context: 已解析的 adapt 子命令上下文。
    :return: 成功刷新状态后的进程退出码 0。
    """

    # adapt 会根据当前安装状态刷新状态文件中的 helper 路径。
    dict_adaptation = adapt_dependencies(  # helper 路径适配结果
        context.dict_settings,  # 适配流程读取依赖 helper 的配置来源
        skills_root=context.path_skills_root,  # 依赖 helper 搜索的 skills 根
        plugin_cache=context.path_plugin_cache,  # 依赖 helper 搜索的插件缓存根
        state_path=context.path_state,  # 适配结果写入的状态文件
    )

    # 适配结果使用 JSON stdout 暴露给调用方。
    print_json(dict_adaptation)

    # adapt 成功完成后返回 0。
    return 0

# _run_cleanup_command 执行旧 FPGA-Agent 子技能清理。
def _run_cleanup_command(context: CliContext) -> int:
    """在显式确认后迁移旧 FPGA-Agent 子技能。

    :param context: 已解析的 cleanup-fpga-agent-skills 子命令上下文。
    :return: 成功生成清理报告后的进程退出码 0。
    """

    # 清理动作必须由 --yes 显式确认。
    dict_cleanup = cleanup_fpga_agent_skills(  # cleanup 子命令迁移报告
        context.dict_settings,  # 清理旧集合前确认 developer fallback 策略
        skills_root=context.path_skills_root,  # 本次清理扫描的 skills 根目录
        plugin_cache=context.path_plugin_cache,  # 用于确认 developer skill 是否已安装
        backup_root=context.namespace_args.backup_root,  # 用户覆盖的备份目录
        yes=context.namespace_args.yes,  # 显式确认是否允许移动目录
    )

    # 清理结果使用 JSON stdout 报告移动清单和备份目录。
    print_json(dict_cleanup)

    # cleanup 只要完成报告生成就视为命令成功。
    return 0

# _run_state_command 分派状态变更子命令。
def _run_state_command(context: CliContext) -> int | None:
    """分派会改变依赖状态的非安装子命令。

    :param context: 已解析的 CLI 共享上下文。
    :return: 已处理时返回子命令退出码；否则返回 None。
    """

    # 按状态命令路由读取当前子命令。
    str_command = context.namespace_args.command  # 状态分派使用的子命令名称

    # skip 子命令把推荐依赖记录为用户跳过。
    if str_command == "skip":

        # skip 处理由独立 helper 保持状态文件和 stdout 合同。
        return _run_skip_command(context)

    # select-fpga-vendor 子命令固定后续 FPGA developer 路由。
    if str_command == "select-fpga-vendor":

        # vendor 处理由独立 helper 保持状态写入合同。
        return _run_select_fpga_vendor_command(context)

    # adapt 子命令把已安装依赖的辅助路径写入状态文件。
    if str_command == "adapt":

        # adapt 处理由独立 helper 保持 helper 路径合同。
        return _run_adapt_command(context)

    # cleanup 子命令把旧 FPGA-Agent 子技能移动到备份目录。
    if str_command == "cleanup-fpga-agent-skills":

        # cleanup 处理由独立 helper 保持显式确认合同。
        return _run_cleanup_command(context)

    # 当前命令不属于状态变更命令，交给安装分派器处理。
    return None

# _run_install_command 执行需要显式确认的安装子命令。
def _run_install_command(context: CliContext) -> int | None:
    """在显式确认后执行依赖安装，并保持原 JSON 输出合同。

    :param context: 已解析的 install 子命令上下文。
    :return: 已处理时返回 0；当前命令不是 install 时返回 None。
    """

    # 非 install 命令不产生安装副作用。
    if context.namespace_args.command != "install":

        # 交给 main 的不可达保护继续判断命令是否有效。
        return None

    # 安装会产生外部副作用，缺少显式确认时必须 fail-closed。
    if not context.namespace_args.yes:

        # parser.error 会打印 usage 并阻止安装 helper 被调用。
        context.parser.error("install requires --yes after the user confirms installation.")

    # 安装前再次生成缺失依赖报告，避免使用过期状态。
    dict_report = check_dependencies(  # install 前重新计算的依赖报告
        context.dict_settings,  # install 前复查 required/recommended 依赖清单
        skills_root=context.path_skills_root,  # 安装前复查的 skills 根目录
        plugin_cache=context.path_plugin_cache,  # 安装前复查的插件缓存目录
        state_path=context.path_state,  # 安装前复查读取的状态文件
    )

    # install_missing 负责筛选单个依赖和执行 installer。
    dict_install = install_missing(  # install 子命令执行摘要
        context.dict_settings,  # install_missing 需要安装规格和 fallback 策略
        dict_report,  # 刚生成的缺失依赖报告
        context.namespace_args.dependency_id,  # 可选单依赖过滤条件
        installer=context.namespace_args.installer,  # 测试或用户覆盖的 installer 脚本
        allow_fpga_agent_fallback=context.namespace_args.allow_fpga_agent_fallback,  # 是否允许安装旧 FPGA-Agent fallback
        confirm=context.namespace_args.yes,  # npm/Node 工具安装沿用 CLI 的显式确认
    )

    # 安装结果使用 JSON stdout 供调用方确认是否需要重启。
    print_json(dict_install)

    # install 子命令执行完 installer 调用后返回成功。
    return 0

# main 负责拼接三类子命令分派结果。
def main(argv: list[str] | None = None) -> int:
    """执行依赖治理 CLI 子命令。

    :param argv: 可选命令行参数列表；为 None 时使用 argparse 默认的 sys.argv。
    :return: 进程退出码；成功路径返回 0。
    :raises AssertionError: 当 argparse 已限制的未知命令仍然进入分派末尾时抛出。
    """

    # CLI parser 和路径配置统一由上下文 helper 解析。
    cli_context_runtime = _load_cli_context(argv)  # 当前 CLI 调用的共享上下文

    # 先处理不改变依赖状态的查询命令。
    int_query_result = _run_query_command(cli_context_runtime)  # 查询命令退出码或 None

    # 查询命令已完成时直接返回其机器可读结果。
    if int_query_result is not None:

        # 查询命令成功完成时退出码为 0。
        return int_query_result

    # 再处理 skip、vendor、adapt 和 cleanup 状态命令。
    int_state_result = _run_state_command(cli_context_runtime)  # 状态命令退出码或 None

    # 状态命令已完成时直接返回其写入结果。
    if int_state_result is not None:

        # 状态命令成功完成时退出码为 0。
        return int_state_result

    # 最后处理需要显式确认的 install 命令。
    int_install_result = _run_install_command(cli_context_runtime)  # 安装命令退出码或 None

    # install 命令已完成时直接返回其结果。
    if int_install_result is not None:

        # 安装命令成功完成时退出码为 0。
        return int_install_result

    # argparse required=True 正常会拦截未知命令。
    raise AssertionError(f"> ERR: [Python] Unhandled command: {cli_context_runtime.namespace_args.command}")

# build_parser 声明所有依赖治理子命令和共享路径选项。
def build_parser() -> argparse.ArgumentParser:
    """构造依赖治理 CLI parser。

    :param: 本函数不接收业务参数；parser 结构由脚本内子命令合同固定。
    :return: 已注册 check、prompt、skip、adapt、install 和 FPGA 路由子命令的 parser。
    """

    # 主 parser 描述脚本总体用途。
    parser = argparse.ArgumentParser(description="Manage readable-verilog-generator skill dependencies.")  # 主命令解析器

    # 顶层 parser 也接受共享路径参数，兼容旧命令写法。
    _add_common_args(parser)

    # 子命令解析器要求显式 command，避免默认动作不明确。
    sub_parsers_action_subcommands: argparse._SubParsersAction[argparse.ArgumentParser] = parser.add_subparsers(  # 顶层依赖治理子命令注册表
        dest="command",  # argparse Namespace 中保存子命令名的字段
        required=True,  # 没有子命令时直接显示用法错误
    )

    # check 子命令只读取并输出依赖状态。
    argument_parser_check: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # check 子命令 parser 对象
        "check",  # 输出 JSON 依赖报告的子命令名
        help="Check installed dependency skills and print JSON.",  # check 子命令帮助文本
    )  # 只读依赖检查解析器

    # check 允许测试覆盖 settings、skills 根和状态路径。
    _add_common_args(argument_parser_check)

    # prompt 子命令把缺失依赖整理成用户可读提示。
    argument_parser_prompt: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # prompt 文本渲染命令的 parser
        "prompt",  # 渲染用户安装提示的子命令名
        help="Render a user-facing installation prompt.",  # 说明 prompt 输出给用户阅读
    )  # 用户提示渲染解析器

    # prompt 使用同一组路径参数生成缺失依赖提示。
    _add_common_args(argument_parser_prompt)

    # adapt 子命令刷新依赖 helper 路径。
    argument_parser_adapt: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # adapt 写状态解析器
        "adapt",  # 适配 helper 路径的子命令名
        help="Persist discovered dependency adaptations.",  # 说明 adapt 写入 helper 路径
    )

    # adapt 复用 settings、skills-root、plugin-cache 和 state-path。
    _add_common_args(argument_parser_adapt)

    # 旧 argparse 兼容路径依赖 set_defaults。
    argument_parser_adapt.set_defaults(command="adapt")

    # skip 子命令记录推荐依赖跳过选择。
    argument_parser_skip: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # recommended 跳过解析器
        "skip",  # 记录推荐依赖跳过的子命令名
        help="Record a recommended dependency as skipped.",  # 说明 skip 只记录推荐依赖
    )

    # skip 需要共享路径选项定位状态文件。
    _add_common_args(argument_parser_skip)

    # dependency_id 指向 defaults.json 中 recommended 依赖 id。
    argument_parser_skip.add_argument("dependency_id")

    # select-fpga-vendor 子命令持久化 FPGA vendor 选择。
    argument_parser_select_vendor: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # vendor 选择解析器
        "select-fpga-vendor",  # 固定 FPGA vendor 的子命令名
        help="Persist the user-selected FPGA vendor for developer skill routing.",  # vendor 选择帮助文本
    )

    # vendor 选择也允许测试覆盖路径。
    _add_common_args(argument_parser_select_vendor)

    # vendor_id 只能是治理配置当前支持的厂商键。
    argument_parser_select_vendor.add_argument("vendor_id", choices=("amd_xilinx", "pangomicro"))

    # fpga-route 子命令输出当前 FPGA skill 路由。
    argument_parser_route: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # FPGA 路由查询解析器
        "fpga-route",  # 只读查询当前 FPGA workflow 路由的子命令名
        help="Report the selected FPGA developer skill route.",  # 说明 route 不写状态文件
    )

    # route 查询使用共享路径选项寻找已安装 skill。
    _add_common_args(argument_parser_route)

    # route 显式设置命令名，兼容早期 argparse 子解析行为。
    argument_parser_route.set_defaults(command="fpga-route")

    # cleanup 子命令迁移旧 FPGA-Agent skill。
    argument_parser_cleanup: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # 旧 FPGA-Agent 迁移解析器
        "cleanup-fpga-agent-skills",  # 迁移旧 FPGA-Agent skill 的子命令名
        help="Move legacy FPGA-Agent Vivado/Vitis skills to a backup directory.",  # 迁移子命令帮助文本
    )

    # cleanup 需要共享路径选项定位 skills 根目录。
    _add_common_args(argument_parser_cleanup)

    # backup-root 允许测试或用户指定迁移目录。
    argument_parser_cleanup.add_argument(
        "--backup-root",
        type=Path,
        help="Override backup root for moved FPGA-Agent skills.",
    )

    # yes 是避免误移动本地 skill 的确认开关。
    argument_parser_cleanup.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation to move legacy FPGA-Agent skills.",
    )

    # install 子命令调用 skill-installer helper。
    argument_parser_install: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # 用户确认后的安装解析器
        "install",  # 调用 skill-installer 的安装子命令名
        help="Install missing dependencies after user confirmation.",  # 说明 install 会触发外部安装
    )

    # install 也需要共享路径选项读取配置和状态。
    _add_common_args(argument_parser_install)

    # dependency-id 支持只安装某一个缺失依赖。
    argument_parser_install.add_argument("--dependency-id", help="Install only one dependency id.")

    # installer 覆盖用于 smoke 中注入 fake installer。
    argument_parser_install.add_argument(
        "--installer",
        type=Path,
        help="Override skill-installer helper script.",
    )

    # yes 明确表示用户已确认安装副作用。
    argument_parser_install.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation that the user approved installation.",
    )

    # FPGA-Agent fallback 需要额外确认，优先使用 developer skill。
    argument_parser_install.add_argument(
        "--allow-fpga-agent-fallback",
        action="store_true",
        help="Explicitly allow FPGA-Agent-Skills fallback installation when no developer skill exists.",
    )

    # 返回完整 parser 供 main 或测试直接使用。
    return parser

# _add_common_args 给 parser 追加共享的 settings、路径和状态参数。
def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """给 parser 添加 settings、skills-root、plugin-cache 和 state-path 参数。

    :param parser: argparse 解析器或子解析器；调用后会原地增加共享参数。
    :return: 不返回业务值；参数定义直接写入传入 parser。
    """

    # settings 默认指向 skill 内 defaults.json。
    parser.add_argument("--settings", type=Path, default=PATH_SKILL_ROOT / "config" / "defaults.json")

    # skills-root 用于测试和非默认 Codex home。
    parser.add_argument("--skills-root", type=Path, help="Override Codex skills root for checks.")

    # plugin-cache 用于发现插件随附的 skills。
    parser.add_argument("--plugin-cache", type=Path, help="Override Codex plugin cache root for checks.")

    # state-path 用于测试隔离或用户指定状态文件。
    parser.add_argument("--state-path", type=Path, help="Override dependency state path.")

# 脚本直运行时进入 CLI main。
if __name__ == "__main__":

    # SystemExit 使用 main 的退出码。
    raise SystemExit(main())
