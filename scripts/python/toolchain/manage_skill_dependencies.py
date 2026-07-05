"""
管理 erie-verilog-generator 的可选 skill 依赖。

stdout_protocol: mixed
本 CLI 的 JSON 类子命令向标准输出写入 JSON object；prompt 子命令输出面向用户的安装提示文本。
"""

# future annotations 避免运行期解析复杂类型标注。
from __future__ import annotations

# 标准库负责 CLI、JSON 状态、安装子进程和路径发现。
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

# pathlib、typing 和 URL 解析支撑路径发现、JSON 载荷标注与 GitHub 来源校验。
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, urlparse

# 当前脚本位于 skill 主体 scripts 目录内。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 当前 skill 主体目录，用于直运行时定位 runtime 包

# FPGA-Agent 旧集合中的子 skill 名称固定用于迁移清理。
TUPLE_FPGA_AGENT_CHILD_SKILLS = (
    "vivado-tcl",  # FPGA-Agent 旧集合中的 Vivado Tcl 自动化入口
    "vivado-sim",  # FPGA-Agent 旧集合中的 Vivado 仿真入口
    "vivado-synth",  # FPGA-Agent 旧集合中的 Vivado 综合入口
    "vivado-impl",  # FPGA-Agent 旧集合中的 Vivado 实现入口
    "vivado-analysis",  # FPGA-Agent 旧集合中的 Vivado 报告分析入口
    "vivado-constraints",  # FPGA-Agent 旧集合中的 Vivado 约束入口
    "vivado-debug",  # FPGA-Agent 旧集合中的 Vivado 调试入口
    "vitis-hls-synthesis",  # 旧 FPGA-Agent HLS 入口，清理时随 Vivado 子项一起迁移
)

# 旧公开常量名保持兼容，外部脚本可能直接读取该集合。
FPGA_AGENT_CHILD_SKILLS = TUPLE_FPGA_AGENT_CHILD_SKILLS  # 兼容旧调用方的 FPGA-Agent 子技能集合

# ensure_skill_root_on_path 将 runtime 包路径调整限制在显式调用阶段。
def ensure_skill_root_on_path() -> None:
    """确保脚本直运行时可以导入 skill 内 runtime 包。

    :param: 本函数不接收业务参数；只检查当前进程的 import path。
    :return: 不返回业务值；必要时把 skill 根目录放入 sys.path 首位。
    """

    # 转成字符串后再比较，避免 Path 对象和 sys.path 文本混用。
    str_skill_root = str(PATH_SKILL_ROOT)  # runtime 包所在的 skill 根目录文本

    # 仅在缺失时修改 sys.path，避免导入阶段产生副作用。
    if str_skill_root not in sys.path:

        # 脚本入口调用时把本 skill runtime 放在导入搜索路径前部。
        sys.path.insert(0, str_skill_root)

# read_skill_dependency_settings 延迟读取 defaults.json 中的依赖治理分区。
def read_skill_dependency_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """读取 skill 依赖治理配置。

    :param settings: 已加载的 defaults.json 配置字典。
    :return: 返回 skill_dependencies 分区的规范化字典。
    """

    # settings loader 需要 runtime 包路径已经可见。
    ensure_skill_root_on_path()

    # runtime 配置模块负责 defaults.json 结构校验。
    from scripts.python.workflow.config import skill_dependency_settings

    # 直接返回 runtime helper 的结果，避免调用方暴露 helper 变量。
    return skill_dependency_settings(settings)

# read_settings_file 延迟加载 defaults.json，保持脚本 import 阶段无路径副作用。
def read_settings_file(path_settings: Path) -> dict[str, Any]:
    """读取 skill defaults.json 配置文件。

    :param path_settings: defaults.json 或测试替代配置文件路径。
    :return: 返回解析后的配置字典。
    """

    # FPGA 路由 helper 同样来自 skill 内 runtime 包。
    ensure_skill_root_on_path()

    # runtime loader 统一处理 JSON 解析和默认路径字段。
    from scripts.python.workflow.config import load_settings

    # 调用 runtime loader 保持旧配置合同。
    return load_settings(path_settings)

# read_fpga_developer_routing_settings 延迟读取 FPGA developer 路由分区。
def read_fpga_developer_routing_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """读取 FPGA developer skill 路由配置。

    :param settings: 已加载的 defaults.json 配置字典。
    :return: 返回 vendor 映射、状态路径和 fallback 策略。
    """

    # 运行期再准备导入路径，避免模块导入时修改 sys.path。
    ensure_skill_root_on_path()

    # runtime 配置模块负责路由分区的字段校验。
    from scripts.python.workflow.config import fpga_developer_routing_settings

    # 返回 vendor 路由策略，调用方无需感知 runtime helper 名称。
    return fpga_developer_routing_settings(settings)

# main 是命令行入口，负责解析参数和分派子命令。
def main(argv: list[str] | None = None) -> int:
    """执行依赖治理 CLI 子命令。

    :param argv: 可选命令行参数列表；为 None 时使用 argparse 默认的 sys.argv。
    :return: 进程退出码；成功路径返回 0。
    :raises AssertionError: 当 argparse 已限制的未知命令仍然进入分派末尾时抛出。
    """

    # CLI parser 集中声明所有子命令，入口只负责分派。
    parser = build_parser()  # 依赖治理 CLI 解析器

    # argparse.Namespace 保留子命令和可选路径参数。
    namespace_args: argparse.Namespace = parser.parse_args(argv)  # 本次 CLI 调用参数

    # settings 文件提供依赖组、状态路径和 FPGA 路由策略。
    dict_settings: dict[str, Any] = read_settings_file(namespace_args.settings)  # skill 治理配置

    # skills_root 可由测试或 CLI 显式覆盖。
    path_skills_root = namespace_args.skills_root or default_skills_root()  # 本次依赖扫描使用的 skills 根目录

    # plugin cache 用于发现插件随附 skills。
    path_plugin_cache = namespace_args.plugin_cache or default_plugin_cache()  # Codex 插件缓存目录

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

    # check 子命令输出完整 JSON 报告。
    if namespace_args.command == "check":

        # 依赖报告保留旧 JSON 字段合同。
        dict_report = check_dependencies(  # check 子命令完整依赖报告
            dict_settings,  # defaults.json 解析后的治理配置
            skills_root=path_skills_root,  # 本次扫描的 skills 根目录
            plugin_cache=path_plugin_cache,  # 本次扫描的插件缓存目录
            state_path=path_state,  # 本次读取的依赖状态文件
        )

        # JSON stdout 是该 CLI 的显式机器可读协议。
        print_json(dict_report)

        # check 成功完成时退出码为 0。
        return 0

    # prompt 子命令输出供 agent 展示给用户的安装提示。
    if namespace_args.command == "prompt":

        # prompt 先复用 check_dependencies，避免提示和 JSON 报告口径漂移。
        dict_report = check_dependencies(  # prompt 渲染前的依赖报告
            dict_settings,  # prompt 复用依赖配置以生成同口径提示
            skills_root=path_skills_root,  # prompt 需要展示的 skills 扫描根
            plugin_cache=path_plugin_cache,  # prompt 需要展示的插件缓存根
            state_path=path_state,  # skip 和 vendor 状态来源文件
        )

        # prompt 文本保留原子命令 stdout 合同。
        sys.stdout.write(prompt_for_missing(dict_report) + "\n")

        # prompt 只负责渲染提示，不因缺失依赖返回失败。
        return 0

    # skip 子命令把推荐依赖记录为用户跳过。
    if namespace_args.command == "skip":

        # 状态文件写入由 record_skip 统一处理。
        record_skip(dict_settings, namespace_args.dependency_id, state_path=path_state)

        # skip 结果仍输出机器可读摘要。
        print_json({"skipped": namespace_args.dependency_id, "state_path": str(path_state)})

        # skip 写入成功后返回 0。
        return 0

    # select-fpga-vendor 子命令固定后续 FPGA developer 路由。
    if namespace_args.command == "select-fpga-vendor":

        # vendor 选择会写入 dependency state。
        dict_selection = select_fpga_vendor(  # vendor 选择写入结果
            dict_settings,  # vendor 写入前读取配置中的厂商映射
            namespace_args.vendor_id,  # 用户指定的 FPGA vendor id
            skills_root=path_skills_root,  # 用于验证 vendor skill 是否安装
            plugin_cache=path_plugin_cache,  # 用于验证插件内 vendor skill
            state_path=path_state,  # vendor 选择持久化目标文件
        )

        # 选择结果使用 JSON stdout 供上层自动读取。
        print_json(dict_selection)

        # vendor 选择成功后返回 0。
        return 0

    # fpga-route 子命令只报告当前路由状态。
    if namespace_args.command == "fpga-route":

        # 路由报告不写状态，除非选择子命令已提前执行。
        dict_route = fpga_route(  # workflow 路由查询结果
            dict_settings,  # 路由查询需要配置中的 vendor 策略
            skills_root=path_skills_root,  # 路由查询使用的 skills 根目录
            plugin_cache=path_plugin_cache,  # 路由查询使用的插件缓存根
            state_path=path_state,  # 已保存 vendor 选择来源文件
        )

        # 路由状态使用 JSON stdout 暴露给上层命令。
        print_json(dict_route)

        # fpga-route 查询成功后返回 0。
        return 0

    # adapt 子命令把已安装依赖的辅助路径写入状态文件。
    if namespace_args.command == "adapt":

        # adapt 会根据当前安装状态刷新状态文件中的 helper 路径。
        dict_adaptation = adapt_dependencies(  # helper 路径适配结果
            dict_settings,  # 适配流程读取依赖 helper 的配置来源
            skills_root=path_skills_root,  # 依赖 helper 搜索的 skills 根
            plugin_cache=path_plugin_cache,  # 依赖 helper 搜索的插件缓存根
            state_path=path_state,  # 适配结果写入的状态文件
        )

        # 适配结果使用 JSON stdout 暴露给调用方。
        print_json(dict_adaptation)

        # adapt 成功完成后返回 0。
        return 0

    # cleanup 子命令把旧 FPGA-Agent 子技能移动到备份目录。
    if namespace_args.command == "cleanup-fpga-agent-skills":

        # 清理动作必须由 --yes 显式确认。
        dict_cleanup = cleanup_fpga_agent_skills(  # cleanup 子命令迁移报告
            dict_settings,  # 清理旧集合前确认 developer fallback 策略
            skills_root=path_skills_root,  # 本次清理扫描的 skills 根目录
            plugin_cache=path_plugin_cache,  # 用于确认 developer skill 是否已安装
            backup_root=namespace_args.backup_root,  # 用户覆盖的备份目录
            yes=namespace_args.yes,  # 显式确认是否允许移动目录
        )

        # 清理结果使用 JSON stdout 报告移动清单和备份目录。
        print_json(dict_cleanup)

        # cleanup 只要完成报告生成就视为命令成功。
        return 0

    # install 子命令只在用户确认后调用 skill-installer helper。
    if namespace_args.command == "install":

        # 缺少 --yes 时沿用 argparse 错误路径阻止安装副作用。
        if not namespace_args.yes:

            # install 会拉取外部 skill，必须有用户确认。
            parser.error("install requires --yes after the user confirms installation.")

        # 安装前再次生成缺失依赖报告，避免使用过期状态。
        dict_report = check_dependencies(  # install 前重新计算的依赖报告
            dict_settings,  # install 前复查 required/recommended 依赖清单
            skills_root=path_skills_root,  # 安装前复查的 skills 根目录
            plugin_cache=path_plugin_cache,  # 安装前复查的插件缓存目录
            state_path=path_state,  # 安装前复查读取的状态文件
        )

        # install_missing 负责筛选单个依赖和执行 installer。
        dict_install = install_missing(  # install 子命令执行摘要
            dict_settings,  # install_missing 需要安装规格和 fallback 策略
            dict_report,  # 刚生成的缺失依赖报告
            namespace_args.dependency_id,  # 可选单依赖过滤条件
            installer=namespace_args.installer,  # 测试或用户覆盖的 installer 脚本
            allow_fpga_agent_fallback=namespace_args.allow_fpga_agent_fallback,  # 是否允许安装旧 FPGA-Agent fallback
        )

        # 安装结果使用 JSON stdout 供调用方确认是否需要重启。
        print_json(dict_install)

        # install 子命令执行完 installer 调用后返回成功。
        return 0

    # argparse required=True 正常会拦截未知命令。
    raise AssertionError(f"> ERR: [Python] Unhandled command: {namespace_args.command}")

# build_parser 声明所有依赖治理子命令和共享路径选项。
def build_parser() -> argparse.ArgumentParser:
    """构造依赖治理 CLI parser。

    :param: 本函数不接收业务参数；parser 结构由脚本内子命令合同固定。
    :return: 已注册 check、prompt、skip、adapt、install 和 FPGA 路由子命令的 parser。
    """

    # 主 parser 描述脚本总体用途。
    parser = argparse.ArgumentParser(description="Manage erie-verilog-generator skill dependencies.")  # 主命令解析器

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

    # cleanup 子命令迁移旧 FPGA-Agent 子技能。
    argument_parser_cleanup: argparse.ArgumentParser = sub_parsers_action_subcommands.add_parser(  # 旧 FPGA-Agent 迁移解析器
        "cleanup-fpga-agent-skills",  # 迁移旧 FPGA-Agent 子 skill 的子命令名
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

# _add_common_args 注册所有子命令共享的路径覆盖参数。
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

# check_dependencies 汇总 required、recommended 和 manual fallback 依赖状态。
def check_dependencies(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """检查当前 Codex 环境中的 skill 依赖安装状态。

    :param settings: 已加载的 defaults.json 配置；包含依赖组、版本和路由策略。
    :param skills_root: 可选 Codex skills 根目录；测试可传临时目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录；用于发现插件内 skills。
    :param state_path: 可选依赖状态文件路径；用于跳过记录和 vendor 选择。
    :return: 返回保持旧 JSON 字段合同的依赖报告；数组 shape/dtype/unit 不适用。
    """

    # 依赖配置包含 required、recommended、manual_fallback 和状态路径。
    dict_dependency_settings = read_skill_dependency_settings(settings)  # skill 依赖治理配置

    # skills_root 优先使用调用方覆盖，默认回落到 Codex home。
    path_skills_root = (skills_root or default_skills_root()).expanduser()  # 待扫描 skills 根目录

    # plugin_cache 覆盖项用于把插件随附 skill 纳入依赖扫描。
    path_plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()  # 待扫描插件缓存目录

    # state_path 用于读取 skipped recommended 和 FPGA vendor 选择。
    path_state = (state_path or dict_dependency_settings["state_path"]).expanduser()  # check 报告读取 skip/vendor 状态的文件

    # 状态文件不存在时 read_state 会返回默认结构。
    dict_state = read_state(path_state)  # 当前依赖治理状态

    # skipped recommended 只有指纹匹配当前配置版本时才生效。
    set_skipped = _active_skipped_recommended(dict_state, dict_dependency_settings, settings.get("version"))  # 有效跳过依赖 id 集合

    # FPGA developer 状态决定是否跳过旧 FPGA-Agent fallback。
    dict_developer_skills = fpga_developer_status(  # FPGA developer 覆盖旧 fallback 的判定依据
        settings,  # 当前 defaults 配置提供 vendor 路由策略
        skills_root=path_skills_root,  # developer skill 的本地搜索根
        plugin_cache=path_plugin_cache,  # developer skill 的插件搜索根
        state_path=path_state,  # developer 覆盖判定读取的状态文件
    )

    # 任一 vendor 可用就说明 developer skill 模式可用。
    bool_developer_present = bool(dict_developer_skills["available_vendors"])  # 是否已安装 vendor developer skill

    # developer skill 存在时默认不再强制 FPGA-Agent 手动 fallback。
    bool_fpga_agent_skipped = (  # FPGA-Agent fallback 是否由 developer skill 覆盖
        bool_developer_present and not dict_developer_skills["fpga_agent_required_when_developer_present"]  # developer 存在且策略允许覆盖旧 fallback
    )

    # required 依赖缺失会阻塞相关远程/Vivado 工作流。
    list_required: list[dict[str, Any]] = []  # required 依赖状态列表

    # 逐项检查 required 依赖组的 skill 集合。
    for dict_dependency in dict_dependency_settings["required"]:

        # 单个依赖状态保留 id、kind、url、missing_skills 和 skill_paths。
        dict_status = _dependency_status(dict_dependency, "required", path_skills_root, path_plugin_cache)  # required 依赖状态

        # required 状态按 defaults.json 顺序输出。
        list_required.append(dict_status)

    # manual_fallback 仅在显式请求时安装，但 check 报告仍展示可用性。
    list_manual_fallback: list[dict[str, Any]] = []  # 手动 fallback 依赖状态列表

    # 逐项检查手动 fallback 依赖组。
    for dict_dependency in dict_dependency_settings.get("manual_fallback", []):

        # fallback 依赖也复用通用依赖状态解析。
        dict_status = _dependency_status(dict_dependency, "manual_fallback", path_skills_root, path_plugin_cache)  # 手动 fallback 依赖安装状态

        # developer skill 已安装时把 FPGA-Agent 视为逻辑满足。
        if dict_dependency["id"] == "fpga-agent-skills" and bool_fpga_agent_skipped:

            # 覆盖 present 字段，避免提示用户再装旧集合。
            dict_status["present"] = True  # developer skill 已覆盖旧 FPGA-Agent 集合

            # developer skill 覆盖后不再报告缺失的 FPGA-Agent 子技能。
            dict_status["missing_skills"] = []  # 覆盖后不再提示旧子技能缺失

            # 报告中显式记录跳过原因，便于上层解释。
            dict_status["skipped_by_developer_skill"] = True  # 报告中保留 developer 覆盖证据

        # fallback 状态按配置顺序追加，便于 prompt 保持稳定输出。
        list_manual_fallback.append(dict_status)

    # recommended_all 保留所有 recommended 状态，报告可展示被跳过项。
    list_recommended_all = [  # 全量 recommended 依赖状态
        _dependency_status(dict_dependency, "recommended", path_skills_root, path_plugin_cache)  # 单个 recommended 依赖状态
        for dict_dependency in dict_dependency_settings["recommended"]  # 按配置顺序遍历 recommended 依赖
    ]

    # recommended 过滤掉当前版本仍有效的 skip 记录。
    list_recommended = [  # 参与缺失判定的 recommended 依赖
        dict_item for dict_item in list_recommended_all if dict_item["id"] not in set_skipped  # 排除当前版本有效 skip 项
    ]

    # required 缺失集合决定 required_ok。
    list_missing_required = [dict_item for dict_item in list_required if not dict_item["present"]]  # 缺失 required 依赖

    # missing_recommended 只影响推荐依赖提示，不绕过 required gate。
    list_missing_recommended = [  # 未被 skip 且仍缺失的 recommended 依赖
        dict_item for dict_item in list_recommended if not dict_item["present"]  # present 为 False 的推荐依赖
    ]

    # 组装旧 JSON 字段合同，避免上层 smoke 和用户脚本破坏。
    dict_report: dict[str, Any] = {  # 依赖检查总报告
        "version": 1,  # 报告 schema 版本
        "ok": not list_missing_required and not list_missing_recommended,  # 全部强制和推荐依赖是否满足
        "required_ok": not list_missing_required,  # required 依赖是否满足
        "recommended_ok": not list_missing_recommended,  # 未跳过的推荐依赖是否全部满足
        "skills_root": str(path_skills_root),  # 实际扫描的 skills 根目录
        "plugin_cache": str(path_plugin_cache),  # 实际扫描的插件缓存目录
        "state_path": str(path_state),  # 实际读取的依赖状态文件
        "developer_skills": dict_developer_skills,  # FPGA developer skill 路由状态
        "active_fpga_dependency_mode": "developer_skill" if bool_developer_present else "fpga_agent_manual_fallback",  # FPGA 依赖模式
        "fpga_agent_skipped_by_developer_skill": bool_fpga_agent_skipped,  # FPGA-Agent 是否被 developer skill 覆盖
        "required": list_required,  # required 依赖逐项状态列表
        "manual_fallback": list_manual_fallback,  # 仅显式允许时才安装的 fallback 状态
        "recommended": list_recommended_all,  # 全量 recommended 依赖状态列表
        "missing_required": list_missing_required,  # 缺失 required 依赖列表
        "missing_recommended": list_missing_recommended,  # prompt 需要询问安装或跳过的推荐依赖
        "skipped_recommended": sorted(set_skipped),  # 当前版本有效的 skipped recommended id
    }

    # 返回完整依赖报告供 CLI、smoke 和测试使用。
    return dict_report

# prompt_for_missing 把依赖报告转换成用户可读安装提示。
def prompt_for_missing(report: dict) -> str:
    """根据依赖报告渲染用户可读提示文本。

    :param report: check_dependencies 生成的依赖报告。
    :return: 返回可直接展示给用户的提示文本；不写状态文件。
    """

    # required 缺失会阻塞远程和 Vivado 相关能力。
    list_missing_required = report.get("missing_required", [])  # prompt 中必须先展示的阻塞依赖

    # recommended 缺失需要询问用户安装或跳过。
    list_missing_recommended = report.get("missing_recommended", [])  # prompt 中需要用户选择的推荐依赖

    # developer_skills 决定是否需要让用户选择 FPGA vendor。
    dict_developer_skills = report.get("developer_skills", {})  # FPGA developer 状态报告

    # selection_required 表示多个 vendor 同时可用且还未选择。
    bool_selection_required = bool(dict_developer_skills.get("selection_required"))  # 是否需要选择 FPGA vendor

    # manual_fallback 用于提示显式 fallback 路径。
    list_manual_fallback = report.get("manual_fallback", [])  # 手动 fallback 状态列表

    # 没有缺失也无需选择 vendor 时给出简短成功提示。
    if not list_missing_required and not list_missing_recommended and not bool_selection_required:

        # 成功提示保留 adapt 操作建议。
        return (
            "All erie-verilog-generator skill dependencies are installed. "
            "Run adapt after a fresh install to refresh project-local helper paths."
        )

    # lines 逐行拼接，保持原 prompt 文本合同。
    list_lines = [  # prompt 输出行集合
        "erie-verilog-generator dependency check found missing skills.",  # prompt 首行摘要
        "",  # 摘要和明细之间的空行
    ]

    # 多个 vendor 可用时先提示用户选择。
    if bool_selection_required:

        # vendor 选择提示保持原命令名。
        list_lines.append(
            "Multiple FPGA developer vendors are available. "
            "Ask the user which vendor to use for this FPGA workflow:"
        )

        # 逐个 vendor 输出 select-fpga-vendor 命令。
        for str_vendor_id in dict_developer_skills.get("available_vendors", []):

            # vendor 详情包含用户可读 label。
            dict_vendor = dict_developer_skills.get("vendors", {}).get(str_vendor_id, {})  # 单个 vendor 状态

            # 每个候选 vendor 单独成行，便于用户复制命令。
            list_lines.append(f"- {dict_vendor.get('label', str_vendor_id)}: select-fpga-vendor {str_vendor_id}")

        # 空行分隔 vendor 选择和依赖安装段落。
        list_lines.append("")

    # required 缺失必须明确说会阻塞工作流。
    if list_missing_required:

        # required 标题说明阻塞范围。
        list_lines.append(
            "Missing required dependency groups. "
            "These block remote/Vivado-related workflows until resolved:"
        )

        # 逐项列出依赖 id、URL 和缺失 skill。
        for dict_item in list_missing_required:

            # missing_skills 保持逗号拼接，延续旧提示格式。
            str_missing_skills = ", ".join(dict_item["missing_skills"])  # required 缺失 skill 文本

            # 单行提示保留 id、url 和缺失技能列表。
            list_lines.append(f"- {dict_item['id']}: {dict_item['url']} ({str_missing_skills})")

        # 空行分隔 required 和 fallback 段落。
        list_lines.append("")

    # fallback_missing 只展示尚未满足的手动 fallback。
    list_fallback_missing = [  # 尚未满足的手动 fallback 依赖
        dict_item for dict_item in list_manual_fallback if not dict_item.get("present")  # 尚未满足的 fallback 项
    ]

    # 手动 fallback 不能自动安装，必须提示需要明确用户方向。
    if list_fallback_missing:

        # fallback 标题强调优先使用 developer skills。
        list_lines.append("Manual fallback dependency groups are available only after explicit user direction:")

        # fallback 明细保留 URL，避免用户误把旧集合当成默认路径。
        for dict_item in list_fallback_missing:

            # fallback 缺失技能列表需要和依赖 id 同行展示。
            str_missing_skills = ", ".join(dict_item["missing_skills"])  # fallback 依赖缺失的 skill 名称文本

            # fallback 行保留 prefer vendor developer skills first 原语义。
            list_lines.append(
                f"- {dict_item['id']}: {dict_item['url']} ({str_missing_skills}); "
                "prefer vendor developer skills first."
            )

        # fallback 段落结束后留空行分隔 recommended 提示。
        list_lines.append("")

    # recommended 缺失需要用户选择安装或 skip。
    if list_missing_recommended:

        # recommended 标题说明用户可安装或跳过。
        list_lines.append(
            "Missing recommended dependency groups. "
            "Ask the user whether to install or skip them for this version:"
        )

        # 逐项列出 recommended 依赖。
        for dict_item in list_missing_recommended:

            # recommended 缺失技能列表用于后续 install 或 skip 选择。
            str_missing_skills = ", ".join(dict_item["missing_skills"])  # recommended 提示行中的缺失 skill 文本

            # recommended 行保留 id、url 和缺失技能列表。
            list_lines.append(f"- {dict_item['id']}: {dict_item['url']} ({str_missing_skills})")

        # 空行分隔 recommended 和最终安装说明。
        list_lines.append("")

    # 最终提醒安装需要用户确认和 Codex 重启。
    list_lines.append(
        "Install only after the user confirms. "
        "After installation, tell the user to restart Codex so new skills are discovered."
    )

    # 返回原函数约定的换行拼接文本。
    return "\n".join(list_lines)

# record_skip 记录用户对 recommended 依赖的跳过选择。
def record_skip(settings: dict, dependency_id: str, *, state_path: Path | None = None) -> dict:
    """把 recommended 依赖标记为当前配置版本已跳过。

    :param settings: 已加载的 defaults.json 配置。
    :param dependency_id: 要跳过的 recommended 依赖 id。
    :param state_path: 可选状态文件路径；测试可传临时文件。
    :return: 返回写入后的状态字典。
    :raises ValueError: dependency_id 不属于 recommended 依赖时抛出。
    数组合同：本函数只处理 JSON 字典状态；shape、dtype 和 unit 不适用。
    """

    # 依赖配置提供 recommended 列表和状态路径。
    dict_dependency_settings = read_skill_dependency_settings(settings)  # recommended 跳过状态配置

    # known 用于校验只能跳过 recommended 依赖。
    dict_known = {dict_item["id"]: dict_item for dict_item in dict_dependency_settings["recommended"]}  # recommended 依赖索引

    # 非 recommended 依赖不能通过 skip 绕过。
    if dependency_id not in dict_known:

        # skip 只允许 recommended，required 缺失必须安装。
        raise ValueError(f"> ERR: [Python] Only recommended dependencies can be skipped: {dependency_id}")

    # 状态路径优先使用调用方覆盖。
    path_state = (state_path or dict_dependency_settings["state_path"]).expanduser()  # skip 记录写入的状态文件

    # 读取现有状态并补齐默认结构。
    dict_state = read_state(path_state)  # 当前依赖状态

    # skipped 集合用于去重。
    set_skipped = set(dict_state.get("skipped_recommended", []))  # 已跳过 recommended 依赖集合

    # 新的 dependency_id 加入 skip 集合。
    set_skipped.add(dependency_id)

    # 状态文件中保持排序后的稳定列表。
    dict_state["skipped_recommended"] = sorted(set_skipped)  # 去重后稳定保存 skipped id

    # 指纹用于配置版本变化时自动失效旧 skip。
    dict_fingerprints = dict_state.setdefault("skipped_recommended_fingerprints", {})  # skip 指纹映射

    # 当前依赖指纹绑定 settings 版本、URL、skills 和安装规格。
    dict_fingerprints[dependency_id] = _dependency_fingerprint(dict_known[dependency_id], settings.get("version"))  # skip 指纹绑定当前依赖配置

    # 状态 schema 版本保持向后兼容。
    dict_state.setdefault("version", 1)

    # 更新时间让用户能追踪最近写入。
    dict_state["updated_at"] = utc_now()  # skip 状态最近更新时间

    # 写回状态文件。
    write_state(path_state, dict_state)

    # 返回新状态供 CLI 或测试断言。
    return dict_state

# adapt_dependencies 写入已安装依赖的本地 helper 路径。
def adapt_dependencies(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """发现已安装依赖的 helper 路径并写入状态文件。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 adapted、blocked 和 state_path 字段；数组 shape/dtype/unit 不适用。
    """

    # defaults.json 中的依赖配置提供状态路径。
    dict_dependency_settings = read_skill_dependency_settings(settings)  # 依赖适配状态配置

    # skills_root 用于定位已安装 remote-ssh skill。
    path_skills_root = (skills_root or default_skills_root()).expanduser()  # remote helper 搜索使用的 skills 根

    # plugin_cache 用于定位插件内依赖。
    path_plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()  # remote helper 搜索使用的插件缓存

    # state_path 用于保存适配结果。
    path_state = (state_path or dict_dependency_settings["state_path"]).expanduser()  # remote 适配结果写入文件

    # 先复用 check_dependencies 判断 required 是否满足。
    dict_report = check_dependencies(  # 适配前确认 required 依赖是否完整
        settings,  # 当前 defaults 配置提供 required 依赖清单
        skills_root=path_skills_root,  # 复查 remote-ssh skill 的 skills 根
        plugin_cache=path_plugin_cache,  # 复查 remote-ssh skill 的插件缓存
        state_path=path_state,  # 复查时读取相同状态文件
    )

    # required 缺失时不写入半适配状态。
    if dict_report["missing_required"]:

        # blocked 只返回缺失 required 的依赖 id。
        list_blocked = [dict_item["id"] for dict_item in dict_report["missing_required"]]  # 阻塞适配的 required 依赖

        # 返回阻塞信息，供 CLI JSON 输出。
        return {"adapted": [], "blocked": list_blocked, "state_path": str(path_state)}

    # 读取状态文件并准备 adaptations 分区。
    dict_state = read_state(path_state)  # 将要合并 adaptations 的现有状态

    # adaptations 保存依赖 helper 的绝对路径。
    dict_adaptations = dict_state.setdefault("adaptations", {})  # 依赖 helper 适配信息

    # adapted 记录本轮成功写入的依赖 id。
    list_adapted: list[str] = []  # 本轮完成适配的依赖 id

    # remote_status 决定 adapt 是否能写入 remote_ssh.py 和 defaults.json 的绝对路径。
    dict_remote_status = next(  # adaptations.remote 的 helper/settings 路径来源
        (dict_item for dict_item in dict_report["required"] if dict_item["id"] == "erie-remote-ssh"),  # 可提供远程 helper 的依赖状态候选
        None,  # 报告缺少远程依赖项时保持未适配状态
    )

    # remote-ssh present 时才写入 helper 和 settings 路径。
    if dict_remote_status and dict_remote_status.get("present"):

        # skill_paths 中保存 erie-remote-ssh 的真实安装目录。
        path_skill = Path(dict_remote_status["skill_paths"]["erie-remote-ssh"])  # 已安装 erie-remote-ssh skill 目录

        # remote_ssh.py 是远程操作 helper。
        path_helper = path_skill / "scripts" / "remote_ssh.py"  # remote-ssh helper 脚本路径

        # 兼容 config/defaults.json 和 assets/defaults.json 两种布局。
        list_remote_settings_candidates = [  # remote-ssh settings 候选路径
            path_skill / "config" / "defaults.json",  # 新版 remote-ssh 默认配置位置
            path_skill / "assets" / "defaults.json",  # 旧版 remote-ssh 默认配置位置
        ]

        # 选择第一个存在的 remote settings 文件。
        path_remote_settings = next(  # remote-ssh settings 实际路径
            (path_candidate for path_candidate in list_remote_settings_candidates if path_candidate.is_file()),  # 首个存在的 defaults 候选
            None,  # 两种布局都缺失时保持阻塞
        )

        # helper 和 settings 同时存在时才能适配 remote。
        if path_helper.is_file() and path_remote_settings is not None:

            # remote adaptation 写入绝对路径，避免后续工作目录变化影响调用。
            dict_adaptations["remote"] = {
                "helper": str(path_helper.resolve()),  # remote_ssh.py 绝对路径
                "settings": str(path_remote_settings.resolve()),  # remote 默认配置的绝对路径
            }

            # 记录 remote 依赖已适配。
            list_adapted.append("erie-remote-ssh")

        # 已安装但布局不完整时必须阻塞适配。
        else:

            # 返回缺失 helper 或 settings 的明确原因。
            return {
                "adapted": [],  # 未写入任何适配项
                "blocked": ["erie-remote-ssh"],  # 因 remote-ssh 布局不完整而阻塞
                "reason": (
                    "Installed erie-remote-ssh is missing scripts/remote_ssh.py "
                    "or a supported defaults.json under config/ or assets."
                ),  # 布局缺失原因
                "state_path": str(path_state),  # 本次读取的状态路径
            }

    # 适配写回时补齐状态 schema，兼容旧状态文件。
    dict_state.setdefault("version", 1)

    # 记录适配写入时间。
    dict_state["updated_at"] = utc_now()  # adaptations 最近写入时间

    # 远程 helper 路径确认后写回同一个状态文件。
    write_state(path_state, dict_state)

    # 返回适配摘要。
    return {"adapted": list_adapted, "blocked": [], "state_path": str(path_state)}

# fpga_developer_status 检查 vendor developer skill 的安装和选择状态。
def fpga_developer_status(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """汇总 FPGA developer skill 的 vendor 可用性和用户选择状态。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 vendor、available_vendors、selection_required 等路由字段；数组 shape/dtype/unit 不适用。
    """

    # routing 包含 vendor 到 skill id 的映射。
    dict_routing = read_fpga_developer_routing_settings(settings)  # FPGA developer 路由配置

    # 该根目录决定每个厂商开发技能是否被视为本地已安装。
    path_skills_root = (skills_root or default_skills_root()).expanduser()  # 厂商开发技能可用性判定的本地搜索根

    # plugin_cache 用于查找插件内 developer skill。
    path_plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()  # 插件随附 vendor skill 搜索根

    # state_path 用于读取用户选择的 vendor。
    path_state = (state_path or dict_routing["state_path"]).expanduser()  # vendor 选择状态读取路径

    # state 中可能保存 fpga_developer_selection。
    dict_state = read_state(path_state)  # 包含已保存 vendor 选择的依赖状态

    # selected 只在结构为 dict 时有效。
    dict_selected = _fpga_selection_from_state(dict_state)  # 已保存的 FPGA vendor 选择

    # vendors 保存每个 vendor 的安装状态。
    dict_vendors: dict[str, dict[str, Any]] = {}  # vendor 到状态报告的映射

    # available_vendors 按配置顺序记录可用 vendor。
    list_available_vendors: list[str] = []  # 已安装 developer skill 的 vendor id 列表

    # vendor 循环按配置顺序构造可用厂商清单。
    for str_vendor_id, dict_vendor in dict_routing["vendors"].items():

        # skill_paths 保存该 vendor 已发现的 skill 路径。
        dict_skill_paths: dict[str, str] = {}  # 当前 vendor 已安装 skill 路径

        # 一个 vendor 可配置多个等价 developer skill。
        for str_skill in dict_vendor["skills"]:

            # find_skill 同时查找 skills_root 和 plugin_cache。
            path_found = find_skill(str_skill, path_skills_root, path_plugin_cache)  # developer skill 发现路径

            # 找到即记录路径。
            if path_found:

                # skill path 以字符串形式进入 JSON 报告。
                dict_skill_paths[str_skill] = str(path_found)  # 记录该 vendor skill 的发现路径

        # selected_skill 取该 vendor 中第一个已安装 skill。
        str_selected_skill = next(  # 当前 vendor 选中的 developer skill
            (str_skill for str_skill in dict_vendor["skills"] if str_skill in dict_skill_paths),  # 按配置优先级选择 skill
            None,  # 当前 vendor 没有任何 developer skill
        )

        # present 表示该 vendor 至少一个 developer skill 可用。
        bool_present = str_selected_skill is not None  # 当前 vendor 是否可用

        # 可用 vendor 进入选择候选。
        if bool_present:

            # 保持配置顺序，便于稳定提示。
            list_available_vendors.append(str_vendor_id)

        # vendor 状态保留安装证据、首选 skill 和路径映射，供 route 决定是否 ready。
        dict_vendors[str_vendor_id] = {  # 决定厂商是否可选以及最终调用哪个开发技能
            "label": dict_vendor["label"],  # 提示用户选择厂商时展示的名称
            "skills": dict_vendor["skills"],  # 按优先级寻找开发技能的候选名称
            "present": bool_present,  # 该厂商至少发现一个开发技能时为真
            "selected_skill": str_selected_skill,  # 当前厂商实际路由到的开发技能名称
            "skill_paths": dict_skill_paths,  # 已发现开发技能到安装目录文本的映射
        }

    # selected_vendor 只有 state 中结构正确时读取。
    str_selected_vendor = dict_selected.get("vendor") if isinstance(dict_selected, dict) else None  # 状态文件保存的 vendor id

    # selection_valid 要求保存的 vendor 当前仍然可用。
    bool_selection_valid = bool(str_selected_vendor in list_available_vendors)  # 已保存 vendor 是否仍有效

    # selection_stale 表示用户选过 vendor，但当前环境不再可用。
    bool_selection_stale = bool(str_selected_vendor and not bool_selection_valid)  # 已保存选择是否失效

    # 多个 vendor 可用且没有有效选择时需要用户选择。
    bool_selection_required = len(list_available_vendors) > 1 and not bool_selection_valid  # 是否需要用户选择 vendor

    # 返回 FPGA developer 路由状态。
    return {
        "state_path": str(path_state),  # 状态文件路径
        "available_vendors": list_available_vendors,  # 可用 vendor 列表
        "vendors": dict_vendors,  # vendor 状态映射
        "selected_vendor": str_selected_vendor if bool_selection_valid else None,  # 当前有效 vendor
        "selection_required": bool_selection_required,  # 多 vendor 可用但未选择时为 True
        "selection_stale": bool_selection_stale,  # 已保存 vendor 当前不可用时为 True
        "fpga_agent_required_when_developer_present": dict_routing["fpga_agent_required_when_developer_present"],  # fallback 策略
    }

# select_fpga_vendor 持久化用户选择的 FPGA vendor。
def select_fpga_vendor(
    settings: dict,
    vendor_id: str,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """保存用户选择的 FPGA developer vendor。

    :param settings: 已加载的 defaults.json 配置。
    :param vendor_id: 用户选择的 vendor id。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 selected_vendor、selected_skill 和 state_path。
    :raises ValueError: vendor 未知或当前未安装对应 developer skill 时抛出。
    数组合同：本函数只更新 JSON 状态字段；shape、dtype 和 unit 不适用。
    """

    # 先读取当前 vendor 可用性，避免写入不可用选择。
    dict_status = fpga_developer_status(  # 写入选择前的 vendor 可用性报告
        settings,  # 当前配置用于解析 vendor 到 skill 的映射
        skills_root=skills_root,  # 可选覆盖的 skills 搜索根
        plugin_cache=plugin_cache,  # 可选覆盖的插件缓存根
        state_path=state_path,  # 可选覆盖的状态文件
    )

    # vendor 状态从报告中按 id 提取。
    dict_vendor = dict_status["vendors"].get(vendor_id)  # 用户指定 vendor 状态

    # 未知 vendor 不能写入状态。
    if not dict_vendor:

        # 明确报告未知 vendor id。
        raise ValueError(f"> ERR: [Python] Unknown FPGA vendor: {vendor_id}")

    # 未安装 developer skill 的 vendor 不能作为选择。
    if not dict_vendor["present"]:

        # 明确报告该 vendor 当前不可用。
        raise ValueError(f"> ERR: [Python] FPGA vendor {vendor_id} has no installed developer skill.")

    # state_path 显式参数优先，否则使用状态报告里的路径。
    path_state = Path(state_path or dict_status["state_path"]).expanduser()  # vendor 选择写入目标状态文件

    # 读取原状态以保留其他分区。
    dict_state = read_state(path_state)  # 保留 skip/adaptation 分区的原状态

    # fpga_developer_selection 保存 vendor 和实际 skill。
    dict_state["fpga_developer_selection"] = {
        "vendor": vendor_id,  # 用户选择的 vendor id
        "skill": dict_vendor["selected_skill"],  # 当前 vendor 对应的 developer skill
        "updated_at": utc_now(),  # vendor 选择更新时间
    }

    # vendor 选择写回前补齐状态 schema。
    dict_state.setdefault("version", 1)

    # 总状态更新时间同步刷新。
    dict_state["updated_at"] = utc_now()  # vendor 选择后的总状态更新时间

    # 写回用户选择。
    write_state(path_state, dict_state)

    # 返回选择结果供 CLI 输出或测试断言。
    return {
        "selected_vendor": vendor_id,  # 已保存 vendor id
        "selected_skill": dict_vendor["selected_skill"],  # 已保存 vendor 对应的 developer skill
        "state_path": str(path_state),  # 本次写入的状态文件路径
    }

# fpga_route 根据安装和选择状态给出本次 FPGA workflow 路由。
def fpga_route(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """报告当前 FPGA workflow 应使用的 developer skill 或 fallback 状态。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 ready、selection_required、selection_stale 或 manual_fallback_available 状态；数组 shape/dtype/unit 不适用。
    """

    # 读取当前 developer skill 安装和选择状态。
    dict_status = fpga_developer_status(  # 当前 FPGA developer 安装和选择状态
        settings,  # 当前配置用于解析 developer vendor
        skills_root=skills_root,  # 路由查询使用的 skills 根
        plugin_cache=plugin_cache,  # route 查找插件内 developer skill
        state_path=state_path,  # 路由查询读取的状态文件
    )

    # available_vendors 是可路由的 vendor 候选。
    list_available = dict_status["available_vendors"]  # 可直接路由的 vendor id 列表

    # 没有 developer skill 时回落到手动 FPGA-Agent fallback。
    if not list_available:

        # 依赖配置中保存 FPGA-Agent fallback 的完整 skill 列表。
        dict_dependency_settings = read_skill_dependency_settings(settings)  # FPGA-Agent fallback 依赖配置

        # fpga-agent-skills 是唯一 FPGA 手动 fallback 聚合依赖。
        dict_fpga = next(  # 手动 fallback 的 FPGA-Agent 聚合依赖配置
            (
                dict_item  # manual_fallback 候选依赖条目
                for dict_item in dict_dependency_settings.get("manual_fallback", [])  # 遍历手动 fallback 依赖配置
                if dict_item["id"] == "fpga-agent-skills"  # 只取旧 FPGA-Agent 聚合依赖
            ),  # manual_fallback 中的 FPGA-Agent 配置项
        )

        # 返回手动 fallback 可用但需要用户批准的状态。
        return {
            "status": "manual_fallback_available",  # 没有 developer skill 时的路由状态
            "fallback_skills": dict_fpga["skills"],  # fallback 需要的 skill 列表
            "requires_explicit_approval": True,  # fallback 必须显式批准
        }

    # 已保存 vendor 失效时要求用户重新选择。
    if dict_status["selection_stale"]:

        # stale 状态只报告当前可用 vendor。
        return {"status": "selection_stale", "available_vendors": list_available}

    # 多 vendor 可用且未选择时要求用户选择。
    if dict_status["selection_required"]:

        # selection_required 状态只报告候选 vendor。
        return {"status": "selection_required", "available_vendors": list_available}

    # 单 vendor 可用时可自动选择，否则使用已保存 vendor。
    str_selected_vendor = dict_status["selected_vendor"] or list_available[0]  # 当前生效 vendor id

    # vendor 状态中包含 selected_skill 和 skill_paths。
    dict_vendor = dict_status["vendors"][str_selected_vendor]  # 当前生效 vendor 状态

    # selected_skill 是实际路由目标。
    str_selected_skill = dict_vendor["selected_skill"]  # 实际交给 workflow 使用的 developer skill id

    # ready 状态返回可直接使用的 skill 路径。
    return {
        "status": "ready",  # developer skill 可直接使用的路由状态
        "selected_vendor": str_selected_vendor,  # 当前 vendor id
        "selected_skill": str_selected_skill,  # workflow 应调用的 developer skill id
        "skill_path": dict_vendor["skill_paths"][str_selected_skill],  # developer skill 安装路径
    }

# install_missing 调用 skill-installer 安装缺失依赖。
def install_missing(
    settings: dict,
    report: dict,
    dependency_id: str | None = None,
    *,
    installer: Path | None = None,
    allow_fpga_agent_fallback: bool = False,
) -> dict:
    """安装报告中缺失的依赖 skill。

    :param settings: 已加载的 defaults.json 配置。
    :param report: check_dependencies 生成的依赖报告。
    :param dependency_id: 可选依赖 id；为空时安装全部缺失 required/recommended。
    :param installer: 可选 skill-installer helper 路径。
    :param allow_fpga_agent_fallback: 是否允许安装 FPGA-Agent 手动 fallback。
    :return: 返回 installed、skipped 和 restart_required 等安装摘要。
    :raises FileNotFoundError: installer helper 不存在时抛出。
    :raises ValueError: 依赖缺少 install spec 时抛出。
    """

    # 依赖配置提供安装 spec 和 fallback 集合。
    dict_dependency_settings = read_skill_dependency_settings(settings)  # 安装命令依赖配置

    # dependencies 建立 id 到依赖配置的索引。
    dict_dependencies = {  # 全部可安装依赖配置索引
        dict_item["id"]: dict_item  # 依赖 id 到 defaults 条目的映射
        for dict_item in [  # 合并 required、recommended 和 fallback 配置
            *dict_dependency_settings["required"],  # required 依赖配置项
            *dict_dependency_settings["recommended"],  # 可由用户跳过的推荐依赖配置
            *dict_dependency_settings.get("manual_fallback", []),  # 显式允许的 fallback 配置项
        ]
    }

    # missing 默认包含 required 和 recommended 缺失项。
    list_missing = [  # 默认参与安装的缺失依赖状态
        *report.get("missing_required", []),  # 必须安装的缺失依赖
        *report.get("missing_recommended", []),  # 用户未跳过的推荐依赖
    ]

    # 显式请求 FPGA-Agent 时才纳入 manual_fallback 缺失项。
    if dependency_id == "fpga-agent-skills":

        # 只追加当前未 present 的 fallback 状态。
        list_missing.extend(
            [
                dict_item  # 尚未安装的 FPGA-Agent fallback 状态
                for dict_item in report.get("manual_fallback", [])
                if not dict_item.get("present")
            ]
        )

    # dependency_id 非空时只安装指定依赖。
    if dependency_id:

        # 保留指定 id 的缺失项。
        list_missing = [dict_item for dict_item in list_missing if dict_item["id"] == dependency_id]  # 指定依赖 id 的缺失项

    # 没有选中的缺失项时返回 no-op 摘要。
    if not list_missing:

        # developer skill 覆盖 FPGA-Agent 时明确说明跳过原因。
        if dependency_id == "fpga-agent-skills" and report.get("fpga_agent_skipped_by_developer_skill"):

            # 返回旧字段合同中的 skipped 和 restart_required。
            return {
                "installed": [],  # 未安装任何 skill
                "skipped": [{"dependency_id": "fpga-agent-skills", "reason": "developer skill is installed"}],  # developer skill 覆盖原因
                "restart_required": False,  # 无安装动作不需要重启
            }

        # 普通 no-op 保持旧 message。
        return {"installed": [], "message": "No missing dependencies selected."}

    # installer 默认指向系统 skill-installer helper。
    path_installer = installer or default_installer_script()  # 实际调用的 skill-installer helper

    # installer 缺失时不能继续安装。
    if not path_installer.is_file():

        # 明确报告 installer 路径缺失。
        raise FileNotFoundError(f"> ERR: [Python] Missing skill installer helper: {path_installer}")

    # installed 记录本轮成功安装的 skill id。
    list_installed: list[str] = []  # 已安装 skill id 列表

    # skipped 记录未安装但被策略跳过的依赖。
    list_skipped: list[dict[str, str]] = []  # 安装跳过原因列表

    # 逐个缺失依赖执行安装或策略跳过。
    for dict_status in list_missing:

        # developer skill 存在时不再安装 FPGA-Agent fallback。
        if dict_status["id"] == "fpga-agent-skills" and report.get("fpga_agent_skipped_by_developer_skill"):

            # 记录 developer skill 覆盖原因。
            list_skipped.append({"dependency_id": "fpga-agent-skills", "reason": "developer skill is installed"})

            # 当前依赖已处理，继续下一个缺失项。
            continue

        # FPGA-Agent fallback 需要额外显式批准。
        if dict_status["id"] == "fpga-agent-skills" and not allow_fpga_agent_fallback:

            # 记录缺少 fallback 批准。
            list_skipped.append({"dependency_id": "fpga-agent-skills", "reason": "manual fallback approval required"})

            # 缺少 fallback 批准时跳过当前依赖，继续处理其他缺失项。
            continue

        # dependency 配置提供 GitHub URL 和 install_specs。
        dict_dependency = dict_dependencies[dict_status["id"]]  # 待安装依赖配置

        # repo slug 是 installer 接收的 owner/repo 形式。
        str_repo = github_repo_slug(dict_dependency["url"])  # 传给 installer --repo 的仓库 slug

        # selected_specs 只包含缺失 skill 对应的安装规格。
        list_selected_specs = _selected_install_specs(dict_dependency, dict_status["missing_skills"])  # 本依赖需要安装的 spec 列表

        # 按 spec 调用 installer helper。
        for dict_spec in list_selected_specs:

            # installer 命令把依赖 URL 和 source_path 转换为 skill-installer CLI 参数。
            list_command = [  # 安装器启动时使用的解释器、仓库和来源目录清单
                sys.executable,  # 保持安装器使用当前 Python 解释器运行
                str(path_installer),  # 实际执行的 skill-installer 脚本路径
                "--repo",  # 后续字符串解释为仓库标识的选项名
                str_repo,  # 从依赖 URL 解析出的 GitHub 仓库标识
                "--path",  # 后续字符串解释为仓库子目录的选项名
                str(dict_spec["source_path"]),  # 依赖 skill 在仓库中的来源子目录
            ]

            # dest_name 存在时通过 --name 覆盖安装名。
            if dict_spec.get("dest_name"):

                # installer 的 --name 用于聚合仓库中的子 skill。
                list_command.extend(["--name", str(dict_spec["dest_name"])])

            # 调用 installer，失败时让 CalledProcessError 直接暴露。
            subprocess.run(list_command, check=True)

            # 成功后记录 skill 名称。
            list_installed.append(str(dict_spec["skill"]))

    # 返回安装摘要，restart_required 只在实际安装后为真。
    return {"installed": list_installed, "skipped": list_skipped, "restart_required": bool(list_installed)}

# cleanup_fpga_agent_skills 将旧 FPGA-Agent 子技能移动到备份目录。
def cleanup_fpga_agent_skills(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    backup_root: Path | None = None,
    yes: bool = False,
) -> dict:
    """在 developer skill 可用后迁移旧 FPGA-Agent 子技能目录。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param backup_root: 可选备份根目录。
    :param yes: 是否确认执行移动操作。
    :return: 返回 moved、backup_dir、skills_root、plugin_cache 和 developer_vendors。
    :raises ValueError: 缺少确认、没有 developer skill 或路径安全检查失败时抛出。
    """

    # cleanup 会移动目录，必须由调用方显式确认。
    if not yes:

        # 缺少确认时立即阻塞。
        raise ValueError("> ERR: [Python] cleanup-fpga-agent-skills requires --yes.")

    # skills_root 优先使用调用方覆盖。
    path_skills_root = (skills_root or default_skills_root()).expanduser()  # 旧 FPGA-Agent 子目录所在根

    # plugin_cache 传给 developer 状态检查。
    path_plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()  # developer 替代 skill 的插件搜索根

    # backup_root 默认放在 skills 根目录同级 skill-backups。
    path_backup_root = (backup_root or (default_skills_root().parent / "skill-backups")).expanduser()  # 备份根目录

    # 只有 developer skill 可用时才迁移旧 FPGA-Agent 子技能。
    dict_developer_status = fpga_developer_status(  # 清理前确认 developer skill 已可替代旧集合
        settings,  # 当前配置中的 vendor 路由策略
        skills_root=path_skills_root,  # 本地 developer skill 搜索根
        plugin_cache=path_plugin_cache,  # 插件 developer skill 搜索根
    )

    # 没有 developer vendor 时拒绝清理。
    if not dict_developer_status["available_vendors"]:

        # 阻止用户在无替代 skill 时移走 FPGA-Agent。
        raise ValueError("> ERR: [Python] Refusing cleanup because no FPGA developer skill is installed.")

    # 解析 skills 根目录用于 relative_to 安全检查。
    path_resolved_skills_root = path_skills_root.resolve()  # 规范化 skills 根目录

    # 解析备份根目录用于目标边界检查。
    path_resolved_backup_root = path_backup_root.resolve()  # 规范化备份根目录

    # 备份目录带时间戳，避免覆盖历史迁移。
    path_backup_dir = path_backup_root / f"fpga-agent-skills.bak.{time.strftime('%Y%m%dT%H%M%S')}"  # 本轮备份目录

    # moved 记录实际迁移的子 skill。
    list_moved: list[str] = []  # 已移动 FPGA-Agent 子 skill 列表

    # 遍历旧 FPGA-Agent 子 skill 集合。
    for str_skill in TUPLE_FPGA_AGENT_CHILD_SKILLS:

        # source 是可能存在的旧子 skill 目录。
        path_source = path_skills_root / str_skill  # 旧子 skill 源目录

        # 源目录不存在时无需移动。
        if not path_source.exists():

            # 当前子 skill 未安装，继续检查下一个。
            continue

        # 源路径必须仍在 skills_root 内。
        path_source.resolve().relative_to(path_resolved_skills_root)

        # 缺少 SKILL.md 的目录不按 skill 迁移。
        if not (path_source / "SKILL.md").is_file():

            # 阻止误移动非 skill 目录。
            raise ValueError(
                f"> ERR: [Python] Refusing to move unexpected skill directory without SKILL.md: {path_source}"
            )

        # 需要移动时才创建备份目录。
        path_backup_dir.mkdir(parents=True, exist_ok=True)

        # target 是备份目录中的同名子目录。
        path_target = path_backup_dir / str_skill  # 子 skill 备份目标目录

        # 目标路径必须位于备份根目录内。
        path_target.parent.resolve().relative_to(path_resolved_backup_root)

        # 目标已存在时拒绝覆盖历史备份。
        if path_target.exists():

            # 阻止覆盖已存在的备份目录。
            raise ValueError(f"> ERR: [Python] Backup target already exists: {path_target}")

        # 使用 rename 保持同一文件系统内的快速移动。
        path_source.rename(path_target)

        # 记录已移动的 skill id。
        list_moved.append(str_skill)

    # 返回清理摘要。
    return {
        "moved": list_moved,  # 实际移动的子 skill id
        "backup_dir": str(path_backup_dir),  # 备份目录路径
        "skills_root": str(path_skills_root),  # 旧 FPGA-Agent 子 skill 来源根
        "plugin_cache": str(path_plugin_cache),  # 本次确认 developer skill 的插件缓存
        "developer_vendors": dict_developer_status["available_vendors"],  # 允许清理的已安装 vendor
    }

# _fpga_selection_from_state 读取状态文件中的 FPGA vendor 选择。
def _fpga_selection_from_state(state: dict) -> dict:
    """从依赖状态中提取 FPGA developer 选择。

    :param state: read_state 返回的依赖状态字典。
    :return: 返回 selection 字典；字段不存在或类型不对时返回空字典。
    """

    # selection 旧状态中可能不存在或被用户手动写错。
    dict_selection = state.get("fpga_developer_selection", {})  # 原始 FPGA 选择字段

    # 只有 dict 结构可继续作为选择状态。
    if isinstance(dict_selection, dict):

        # 返回结构正确的 selection。
        return dict_selection

    # 非 dict selection 视为无选择。
    return {}

# _install_specs_by_skill 将 install_specs 转成 skill 名索引。
def _install_specs_by_skill(dependency: dict) -> dict[str, dict]:
    """按 skill 名索引依赖配置中的安装规格。

    :param dependency: defaults.json 中的单个依赖配置。
    :return: 返回 skill 名到 install spec 的映射。
    """

    # install_specs 可能缺省，缺省时视为空列表。
    list_specs = dependency.get("install_specs", [])  # 原始安装规格列表

    # 只保留 dict 且含 skill 字段的安装规格。
    dict_specs = {  # skill 名到安装规格的映射
        str(dict_item["skill"]): dict_item  # 单个 skill 对应的 installer spec
        for dict_item in list_specs  # 逐项读取 defaults 的 installer spec
        if isinstance(dict_item, dict) and dict_item.get("skill")  # 只接受结构正确的 spec
    }

    # 返回安装规格索引供选择缺失 skill 时使用。
    return dict_specs

# _selected_install_specs 根据缺失 skill 选择需要执行的 install spec。
def _selected_install_specs(dependency: dict, missing_skills: list[str]) -> list[dict]:
    """选择本次安装需要执行的 install_specs。

    :param dependency: defaults.json 中的单个依赖配置。
    :param missing_skills: check_dependencies 报告的缺失 skill 名称列表。
    :return: 返回待安装 spec 列表。
    :raises ValueError: 缺失 skill 没有对应 install spec 时抛出。
    """

    # specs 用 skill 名快速定位 install spec。
    dict_specs = _install_specs_by_skill(dependency)  # 当前依赖可用的 skill 安装规格索引

    # 没有缺失 skill 时无需安装。
    if not missing_skills:

        # 返回空列表表示 no-op。
        return []

    # 所有缺失 skill 都有直接 spec 时按缺失列表顺序安装。
    if all(str_skill in dict_specs for str_skill in missing_skills):

        # 返回与 missing_skills 顺序一致的 spec。
        return [dict_specs[str_skill] for str_skill in missing_skills]

    # alternative_skill_sets 表示该依赖可能需要安装完整替代集合。
    if dependency.get("alternative_skill_sets"):

        # 旧行为是返回全部 specs。
        return list(dict_specs.values())

    # missing 记录没有 install spec 的缺失 skill。
    list_missing = [str_skill for str_skill in missing_skills if str_skill not in dict_specs]  # 缺少安装规格的 skill

    # 缺少 install spec 时无法安全安装。
    raise ValueError(
        f"> ERR: [Python] Missing install spec for {', '.join(list_missing)} in dependency {dependency['id']!r}."
    )

# _dependency_status 生成单个依赖配置的安装状态。
def _dependency_status(item: dict, kind: str, skills_root: Path, plugin_cache: Path) -> dict:
    """检查单个依赖配置中的 skill 集合是否已安装。

    :param item: defaults.json 中的单个依赖配置。
    :param kind: 依赖分组名称，如 required、recommended 或 manual_fallback。
    :param skills_root: Codex skills 根目录。
    :param plugin_cache: Codex plugin cache 根目录。
    :return: 返回包含 present、missing_skills、skill_paths 和 selected_skill_set 的状态字典。
    """

    # 默认先检查主 skill 集合。
    tuple_resolved = _resolve_skill_set(item["skills"], skills_root, plugin_cache)  # 主 skill 集合解析结果

    # skill_paths 是已发现 skill 的路径映射。
    dict_skill_paths, list_missing = tuple_resolved  # 主 skill 集合的路径映射和缺失清单

    # selected_skill_set 记录当前用于判定的 skill 集合。
    list_selected_skill_set = item["skills"]  # 当前选中的 skill 集合

    # 主集合缺失时尝试 alternative_skill_sets。
    if list_missing:

        # 逐个替代集合寻找完整满足或缺失更少的组合。
        for list_alternative in item.get("alternative_skill_sets", []):

            # 解析替代 skill 集合。
            tuple_alt_paths, tuple_alt_missing = _resolve_skill_set(  # 替代集合的路径映射和缺口
                list_alternative,  # 当前正在比较的替代 skill 集合
                skills_root,  # 替代集合查找时复用当前 skills 根
                plugin_cache,  # 替代集合查找时复用当前插件缓存
            )

            # 替代集合完整满足时直接采用。
            if not tuple_alt_missing:

                # 完整替代集合成为当前状态。
                dict_skill_paths = tuple_alt_paths  # 完整替代集合的路径映射

                # 完整替代集合没有缺失项。
                list_missing = []  # 完整替代集合满足该依赖

                # 记录最终选中的替代集合。
                list_selected_skill_set = list_alternative  # 当前依赖采用的完整替代集合

                # 已找到完整替代集合，无需继续比较。
                break

            # 缺失更少或已有部分路径时采用更接近满足的替代集合。
            if len(tuple_alt_missing) < len(list_missing) or (
                tuple_alt_paths and len(tuple_alt_missing) == len(list_missing)
            ):

                # 替代集合提供更好的安装状态展示。
                list_missing = tuple_alt_missing  # 缺失更少的替代集合缺口

                # 保存替代集合已发现路径。
                dict_skill_paths = tuple_alt_paths  # 缺失更少的替代集合路径

                # 记录当前较优替代集合。
                list_selected_skill_set = list_alternative  # 当前最优替代集合

    # 返回单个依赖的状态报告。
    return {
        "id": item["id"],  # 依赖 id
        "kind": kind,  # 依赖分组
        "url": item["url"],  # 依赖来源 URL
        "purpose": item.get("purpose", ""),  # 依赖用途说明
        "present": not list_missing,  # 是否完整满足
        "skills": item["skills"],  # 主 skill 集合
        "selected_skill_set": list_selected_skill_set,  # 当前用于判定的 skill 集合
        "missing_skills": list_missing,  # 缺失 skill 列表
        "skill_paths": dict_skill_paths,  # 已发现 skill 路径
    }

# _resolve_skill_set 检查一组 skill 名是否能在本地找到。
def _resolve_skill_set(skills: list[str], skills_root: Path, plugin_cache: Path) -> tuple[dict[str, str], list[str]]:
    """解析一组 skill 的安装路径和缺失项。

    :param skills: 需要检查的 skill 名称列表。
    :param skills_root: Codex skills 根目录。
    :param plugin_cache: Codex plugin cache 根目录。
    :return: 返回已发现路径映射和缺失 skill 列表。
    """

    # skill_paths 保存每个已找到 skill 的绝对路径。
    dict_skill_paths: dict[str, str] = {}  # 已发现 skill 路径映射

    # missing 保存当前环境未找到的 skill。
    list_missing: list[str] = []  # 缺失 skill 名称列表

    # 逐个 skill 查找安装位置。
    for str_skill in skills:

        # find_skill 同时覆盖普通 skills 根和插件缓存。
        path_found = find_skill(str_skill, skills_root, plugin_cache)  # 当前 skill 的本地发现路径

        # 找到则记录路径，否则记录缺失。
        if path_found:

            # JSON 报告中路径使用字符串。
            dict_skill_paths[str_skill] = str(path_found)  # JSON 报告中的 skill 路径文本

        # 未找到时进入缺失列表。
        else:

            # 缺失列表保留原 skill 名称。
            list_missing.append(str_skill)

    # 返回路径映射和缺失列表。
    return dict_skill_paths, list_missing

# find_skill 在普通 skills 根和插件缓存中寻找指定 skill。
def find_skill(skill: str, skills_root: Path, plugin_cache: Path) -> Path | None:
    """查找 skill 的本地安装目录。

    :param skill: skill 目录名。
    :param skills_root: Codex skills 根目录。
    :param plugin_cache: Codex plugin cache 根目录。
    :return: 找到时返回包含 SKILL.md 的目录；否则返回 None。
    """

    # 直接安装的 skill 位于 skills_root 下同名目录。
    path_direct = skills_root / skill  # 直接安装 skill 候选目录

    # SKILL.md 存在说明该目录是 skill 主体。
    if (path_direct / "SKILL.md").is_file():

        # 返回解析后的直接安装路径。
        return path_direct.resolve()

    # 插件缓存不存在时无法继续查找。
    if plugin_cache.exists():

        # 插件缓存内可能有 openai-bundled/plugin/version/skills/<skill> 结构。
        for path_candidate in plugin_cache.rglob(skill):

            # 候选目录必须位于 skills 目录下且包含 SKILL.md。
            if (
                path_candidate.is_dir()
                and (path_candidate / "SKILL.md").is_file()
                and path_candidate.parent.name == "skills"
            ):

                # 返回解析后的插件 skill 路径。
                return path_candidate.resolve()

    # 未找到任何可用 skill。
    return None

# read_state 读取依赖状态文件并补齐默认字段。
def read_state(path: Path) -> dict:
    """读取依赖治理状态 JSON。

    :param path: 状态 JSON 文件路径。
    :return: 返回带默认 version、skipped_recommended、fingerprints 和 adaptations 的状态字典。
    :raises ValueError: 状态文件不是 JSON object 时抛出。
    """

    # 状态文件不存在时返回默认空状态。
    if not path.exists():

        # 默认状态保留所有已知分区。
        return {"version": 1, "skipped_recommended": [], "skipped_recommended_fingerprints": {}, "adaptations": {}}

    # 读取 JSON 文本并解析为 Python 对象。
    dict_data = json.loads(path.read_text(encoding="utf-8"))  # 状态 JSON 解析结果

    # 状态文件必须是 object。
    if not isinstance(dict_data, dict):

        # 非 object 会破坏后续 setdefault 逻辑。
        raise ValueError(f"> ERR: [Python] Dependency state must be a JSON object: {path}")

    # version 缺省时补齐为 1。
    dict_data.setdefault("version", 1)

    # skipped_recommended 缺省时补齐为空列表。
    dict_data.setdefault("skipped_recommended", [])

    # fingerprints 缺省时补齐为空字典。
    dict_data.setdefault("skipped_recommended_fingerprints", {})

    # adaptations 缺省时补齐为空字典，供 adapt 子命令写入。
    dict_data.setdefault("adaptations", {})

    # skipped_recommended 类型错误时回退为空列表。
    if not isinstance(dict_data["skipped_recommended"], list):

        # 用户手写错误不应让检查流程崩溃。
        dict_data["skipped_recommended"] = []  # 修复手写错误的 skipped 列表

    # fingerprints 类型错误时回退为空字典。
    if not isinstance(dict_data["skipped_recommended_fingerprints"], dict):

        # 用户手写错误不应让指纹检查崩溃。
        dict_data["skipped_recommended_fingerprints"] = {}  # 修复手写错误的指纹映射

    # 返回规范化后的状态对象。
    return dict_data

# write_state 将依赖状态以稳定 JSON 格式写回磁盘。
def write_state(path: Path, state: dict) -> None:
    """写入依赖治理状态 JSON。

    :param path: 状态 JSON 文件路径。
    :param state: 要写入的状态字典。
    :return: 不返回业务值；写入完成即表示状态已落盘。
    数组合同：本函数写入 JSON object；shape、dtype 和 unit 不适用。
    """

    # 状态文件父目录可能尚不存在。
    path.parent.mkdir(parents=True, exist_ok=True)

    # sort_keys 保持状态文件 diff 稳定。
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

# default_skills_root 计算 Codex skills 默认根目录。
def default_skills_root() -> Path:
    """返回默认 Codex skills 根目录。

    :param: 本函数不接收业务参数；只读取 CODEX_HOME 环境变量。
    :return: CODEX_HOME/skills 或 ~/.codex/skills。
    """

    # CODEX_HOME 存在时优先使用它。
    str_codex_home = os.environ.get("CODEX_HOME")  # Codex home 环境变量

    # CODEX_HOME 可让测试或便携安装覆盖默认目录。
    if str_codex_home:

        # 返回 CODEX_HOME 下的 skills 目录。
        return Path(str_codex_home).expanduser() / "skills"

    # 未配置 CODEX_HOME 时使用用户主目录。
    return Path.home() / ".codex" / "skills"

# default_plugin_cache 计算 Codex 插件缓存默认根目录。
def default_plugin_cache() -> Path:
    """返回默认 Codex plugin cache 根目录。

    :param: 本函数不接收业务参数；只读取 CODEX_HOME 环境变量。
    :return: CODEX_HOME/plugins/cache 或 ~/.codex/plugins/cache。
    """

    # CODEX_HOME 存在时插件缓存跟随便携安装根。
    str_codex_home = os.environ.get("CODEX_HOME")  # 插件缓存根的环境覆盖值

    # 配置了 CODEX_HOME 时优先返回其 plugins/cache 子目录。
    if str_codex_home:

        # 返回 CODEX_HOME 下的插件缓存目录。
        return Path(str_codex_home).expanduser() / "plugins" / "cache"

    # 未配置 CODEX_HOME 时回落到用户配置目录。
    return Path.home() / ".codex" / "plugins" / "cache"

# default_installer_script 定位系统 skill-installer helper。
def default_installer_script() -> Path:
    """返回默认 GitHub skill 安装 helper 路径。

    :param: 本函数不接收业务参数；路径从 default_skills_root 推导。
    :return: install-skill-from-github.py 的默认路径。
    """

    # helper 位于系统 skill-installer 的 scripts 目录。
    path_installer = default_skills_root() / ".system" / "skill-installer" / "scripts" / "install-skill-from-github.py"  # 默认 installer 路径

    # 返回 helper 路径，存在性由 install_missing 检查。
    return path_installer

# github_repo_slug 把 GitHub URL 转成 installer 接受的 owner/repo。
def github_repo_slug(url: str) -> str:
    """从 GitHub URL 中提取 owner/repo slug。

    :param url: 依赖配置中的 GitHub URL。
    :return: owner/repo 形式的仓库标识。
    :raises ValueError: URL 不是 github.com/<owner>/<repo> 时抛出。
    """

    # urlparse 保留 netloc 和 path，便于校验 GitHub 来源。
    parse_result_object_parsed: ParseResult = urlparse(url)  # GitHub URL 解析对象

    # path 拆分后前两段应为 owner 和 repo。
    list_parts = [str_part for str_part in parse_result_object_parsed.path.strip("/").split("/") if str_part]  # URL path 中的非空段

    # 只支持 GitHub 仓库 URL。
    if len(list_parts) < 2 or parse_result_object_parsed.netloc.lower() != "github.com":

        # 非 GitHub URL 无法传给当前 installer。
        raise ValueError(f"> ERR: [Python] Unsupported GitHub dependency URL: {url}")

    # repo 允许以 .git 结尾。
    str_repo = list_parts[1]  # 仓库名原始片段

    # 去掉 .git 后缀以匹配 installer 的 owner/repo 输入。
    if str_repo.endswith(".git"):

        # 截掉末尾 .git，保持 owner/repo slug 不带传输后缀。
        str_repo = str_repo[:-4]  # installer 接收的不带 .git 仓库名

    # 返回 owner/repo。
    return f"{list_parts[0]}/{str_repo}"

# _active_skipped_recommended 筛出当前配置版本仍有效的 skip 记录。
def _active_skipped_recommended(state: dict, dependency_settings: dict, settings_version: object) -> set[str]:
    """计算当前版本仍有效的 skipped recommended 依赖。

    :param state: read_state 返回的依赖状态。
    :param dependency_settings: skill_dependency_settings 返回的依赖配置。
    :param settings_version: defaults.json 的版本字段。
    :return: 返回指纹与当前配置匹配的 skipped recommended id 集合。
    """

    # skipped 来自状态文件，可能包含过期依赖 id。
    set_skipped = set(state.get("skipped_recommended", []))  # 状态文件中的 skipped id 集合

    # fingerprints 绑定 skip 记录和依赖配置版本。
    dict_fingerprints = state.get("skipped_recommended_fingerprints", {})  # skipped id 到指纹的映射

    # active 只保留当前配置仍匹配的 skip。
    set_active: set[str] = set()  # 当前配置仍认可的 skipped recommended id

    # 遍历当前 recommended 配置以计算新指纹。
    for dict_item in dependency_settings["recommended"]:

        # dependency_id 连接当前配置项、状态文件 skip 列表和指纹映射。
        str_dependency_id = dict_item["id"]  # recommended skip 校验使用的依赖 id

        # 指纹一致才表示用户跳过选择仍适用。
        if (
            str_dependency_id in set_skipped
            and dict_fingerprints.get(str_dependency_id) == _dependency_fingerprint(dict_item, settings_version)
        ):

            # 添加当前有效 skip。
            set_active.add(str_dependency_id)

    # 返回当前有效 skip 集合。
    return set_active

# _dependency_fingerprint 为 skip 记录生成配置指纹。
def _dependency_fingerprint(item: dict, settings_version: object) -> str:
    """生成 recommended 依赖的版本化指纹。

    :param item: defaults.json 中的单个依赖配置。
    :param settings_version: defaults.json 的版本字段。
    :return: 返回 sha256 十六进制摘要。
    """

    # payload 只包含会改变 recommended 安装含义的字段，用于判定旧 skip 是否过期。
    dict_payload = {  # 用户跳过推荐依赖后用于检测配置漂移的指纹材料
        "settings_version": settings_version,  # 配置版本变化时让旧跳过记录失效
        "id": item.get("id"),  # 跳过记录绑定的依赖标识
        "url": item.get("url"),  # 跳过记录绑定的仓库来源
        "skills": item.get("skills"),  # 跳过记录绑定的主技能集合
        "alternative_skill_sets": item.get("alternative_skill_sets", []),  # 可替代满足条件的技能集合
        "install_specs": item.get("install_specs", []),  # 影响安装动作的规格集合
    }

    # separators 固定为紧凑 JSON，确保相同配置产生相同摘要。
    str_serialized = json.dumps(dict_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))  # 指纹 JSON 文本

    # sha256 摘要用于状态文件中的稳定比较。
    return hashlib.sha256(str_serialized.encode("utf-8")).hexdigest()

# utc_now 生成状态文件使用的 UTC 时间戳。
def utc_now() -> str:
    """返回当前 UTC 时间戳。

    :param: 本函数不接收业务参数；时间来自系统时钟。
    :return: ISO-like UTC 时间文本，格式为 YYYY-MM-DDTHH:MM:SSZ。
    """

    # gmtime 避免本地时区影响状态文件 diff。
    str_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())  # 当前 UTC 时间文本

    # 返回状态文件使用的时间戳。
    return str_timestamp

# print_json 按 CLI 显式 stdout 协议输出 JSON object。
def print_json(payload: dict) -> None:
    """向 stdout 写出机器可读 JSON object。

    :param payload: 需要输出给上层程序解析的 JSON object。
    :return: 不返回业务值；JSON 文本写入 stdout。
    """

    # JSON 文本保持缩进和非 ASCII 原样，延续旧 CLI 输出合同。
    str_payload = json.dumps(payload, indent=2, ensure_ascii=False)  # print_json 中 str_payload 的当前用途

    # sys.stdout.write 避免把机器协议误判为人类可读 print。
    sys.stdout.write(str_payload + "\n")

# 脚本直运行时进入 CLI main。
if __name__ == "__main__":

    # SystemExit 使用 main 的退出码。
    raise SystemExit(main())
