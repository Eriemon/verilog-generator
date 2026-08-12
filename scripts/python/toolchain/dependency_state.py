"""管理依赖发现、状态持久化和缺失提示逻辑。"""

# future annotations 避免运行期解析复杂类型标注。
from __future__ import annotations

# 标准库负责状态 JSON、路径发现、URL 解析和时间戳处理。
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, urlparse

# 当前模块位于 skill 主体 scripts 目录内。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 当前 skill 主体目录文本路径

# ensure_skill_root_on_path 只在显式调用阶段准备 skill 根目录导入路径。
def ensure_skill_root_on_path() -> None:
    """确保脚本直运行时可以导入 skill 内 runtime 包。

    :param: 本函数不接收业务参数；只检查当前进程的 import path。
    :return: 不返回业务值；必要时把 skill 根目录放入 sys.path 首位。
    """

    # str_skill_root 保存 skill 根目录的字符串形式，便于和 sys.path 比较。
    str_skill_root = str(PATH_SKILL_ROOT)  # skill 根目录文本路径

    # 缺失 skill 根目录时才修改 sys.path，避免重复插入同一路径。
    if str_skill_root not in sys.path:

        # 显式调用阶段把 skill 根目录放入导入搜索路径。
        sys.path.insert(0, str_skill_root)

# read_skill_dependency_settings 读取 skill_dependencies 配置分区。
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

# read_tool_dependency_settings 读取 npm/Node 外部工具配置分区。
def read_tool_dependency_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """读取外部工具依赖治理配置。

    :param settings: 已加载的 defaults.json 配置字典。
    :return: 返回 tool_dependencies 分区的规范化字典。
    """

    # 延迟准备 skill 根目录，保持模块导入没有路径副作用。
    ensure_skill_root_on_path()

    # workflow.config 负责固定 WaveDrom 包名和版本合同。
    from scripts.python.workflow.config import tool_dependency_settings

    # 直接返回规范化工具配置，调用方不需要感知配置模块。
    return tool_dependency_settings(settings)

# read_settings_file 延迟加载 defaults.json 配置文件。
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

# read_fpga_developer_routing_settings 读取 FPGA developer 路由配置。
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

# required_tool_status 把 WaveDrom runtime 诊断映射成旧工具状态合同。
def _required_tool_status(
    tool_settings: dict[str, Any],
    wavedrom_runtime: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """生成 required 外部工具状态及其缺失子集。

    参数:
        tool_settings: ``tool_dependencies`` 规范化配置。
        wavedrom_runtime: ``check_runtime`` 返回的 WaveDrom 诊断。

    返回:
        ``(required_tools, missing_required_tools)`` 两个稳定列表。
    """

    # 先创建外部工具状态列表，再按配置顺序填充。
    list_required_tools: list[dict[str, Any]] = []  # 外部工具状态列表

    # 每个工具都复用同一份 runtime 诊断，避免版本读取漂移。
    for dict_tool in tool_settings["required"]:

        # 只有 wavedrom 条目可以由当前 runtime 报告证明存在。
        bool_present = (  # 当前工具是否满足 required 可用性
            bool(wavedrom_runtime.get("ok"))  # WaveDrom runtime 的整体可用标志
            if dict_tool["id"] == "wavedrom"  # 只把 wavedrom 配置映射到 runtime
            else False  # 未知工具默认视为未满足
        )  # 工具存在性判定

        # 单独提取版本，避免在报告字典中重复长表达式。
        str_tool_version = (  # 当前工具的机器可读版本文本
            wavedrom_runtime.get("wavedrom", {}).get("version")  # 读取 WaveDrom 精确版本
            if dict_tool["id"] == "wavedrom"  # 只为 wavedrom 提供版本证据
            else None  # 未知工具没有可推断版本
        )  # 工具版本证据

        # 追加保持旧字段合同的外部工具状态记录。
        list_required_tools.append(
            {
                "id": dict_tool["id"],
                "kind": "tool",
                "present": bool_present,
                "required": True,
                "version": str_tool_version,
                "details": wavedrom_runtime,
            }
        )

    # 只保留 present 为 False 的工具，供 required gate 和 prompt 使用。
    list_missing_required_tools = [  # 缺失 required 外部工具清单
        dict_item  # 保留完整工具状态供安装命令定位
        for dict_item in list_required_tools  # 遍历已归一化工具记录
        if not dict_item["present"]  # 过滤未满足工具合同的记录
    ]  # required 工具缺失结果

    # 返回完整工具状态和缺失子集，调用方负责写入总报告。
    return list_required_tools, list_missing_required_tools

# collect_dependency_statuses 扫描 skill 依赖组并计算缺失集合。
def _collect_dependency_statuses(
    dependency_settings: dict[str, Any],
    skills_root: Path,
    plugin_cache: Path,
    skipped_ids: set[str],
    fpga_agent_skipped: bool,
) -> dict[str, list[dict[str, Any]]]:
    """收集 required、fallback、recommended 及其缺失子集。

    参数:
        dependency_settings: skill 依赖分组配置。
        skills_root: skill 搜索根目录。
        plugin_cache: 插件缓存搜索根目录。
        skipped_ids: 当前配置版本仍有效的跳过 id 集合。
        fpga_agent_skipped: 是否由 developer skill 覆盖旧 fallback。

    返回:
        按固定键名组织的依赖状态和缺失列表。
    """

    # required 依赖缺失会阻塞相关远程/Vivado 工作流。
    list_required: list[dict[str, Any]] = []  # required 依赖状态列表

    # 逐项检查 required 依赖组的 skill 集合。
    for dict_dependency in dependency_settings["required"]:

        # 单个依赖状态保留 id、kind、url、missing_skills 和 skill_paths。
        dict_status = _dependency_status(dict_dependency, "required", skills_root, plugin_cache)  # required 依赖状态

        # required 状态按 defaults.json 顺序输出。
        list_required.append(dict_status)

    # manual_fallback 仅在显式请求时安装，但 check 报告仍展示可用性。
    list_manual_fallback: list[dict[str, Any]] = []  # 手动 fallback 依赖状态列表

    # 逐项检查手动 fallback 依赖组。
    for dict_dependency in dependency_settings.get("manual_fallback", []):

        # fallback 依赖也复用通用依赖状态解析。
        dict_status = _dependency_status(dict_dependency, "manual_fallback", skills_root, plugin_cache)  # 手动 fallback 依赖安装状态

        # developer skill 已安装时把 FPGA-Agent 视为逻辑满足。
        if dict_dependency["id"] == "fpga-agent-skills" and fpga_agent_skipped:

            # 覆盖 present 字段，避免提示用户再装旧集合。
            dict_status["present"] = True  # developer skill 已覆盖旧 FPGA-Agent 集合

            # developer skill 覆盖后不再报告缺失的 FPGA-Agent 子技能。
            dict_status["missing_skills"] = []  # 覆盖后不再提示旧子技能缺失

            # 报告中显式记录跳过原因，便于上层解释。
            dict_status["skipped_by_developer_skill"] = True  # 报告中保留 developer 覆盖证据

        # fallback 状态按配置顺序追加，便于 prompt 保持稳定输出。
        list_manual_fallback.append(dict_status)

    # recommended_all 保留所有状态，报告可展示被跳过项。
    list_recommended_all = [  # 全量 recommended 依赖状态
        _dependency_status(dict_dependency, "recommended", skills_root, plugin_cache)  # 单个 recommended 依赖状态
        for dict_dependency in dependency_settings["recommended"]  # 按配置顺序遍历 recommended 依赖
    ]  # recommended 状态结果

    # recommended 过滤掉当前版本仍有效的 skip 记录。
    list_recommended = [  # 参与缺失判定的 recommended 依赖
        dict_item  # 保留未被跳过的推荐状态
        for dict_item in list_recommended_all  # 遍历全量推荐依赖
        if dict_item["id"] not in skipped_ids  # 排除当前配置版本有效 skip 项
    ]  # 当前生效的 recommended 集合

    # required 缺失集合决定 required_ok。
    list_missing_required = [  # 缺失 required 依赖列表
        dict_item  # 保留完整依赖诊断供 prompt 使用
        for dict_item in list_required  # 遍历 required 状态
        if not dict_item["present"]  # 仅保留仍缺少 required skill 的记录
    ]  # required 缺失结果

    # missing_recommended 只影响推荐依赖提示，不绕过 required gate。
    list_missing_recommended = [  # 未被 skip 且仍缺失的推荐依赖
        dict_item  # 保留缺失推荐状态
        for dict_item in list_recommended  # 遍历当前生效推荐集合
        if not dict_item["present"]  # recommended 未满足时才进入用户提示清单
    ]  # 形成 prompt 使用的推荐缺口集合

    # 返回总报告需要的所有依赖列表，主函数只负责组装外部工具状态。
    return {
        "required": list_required,
        "manual_fallback": list_manual_fallback,
        "recommended_all": list_recommended_all,
        "missing_required": list_missing_required,
        "missing_recommended": list_missing_recommended,
    }

# read_wavedrom_runtime 延迟加载 bundled runtime 并读取工具状态。
def _read_wavedrom_runtime() -> dict[str, Any]:
    """读取当前机器的 WaveDrom 版本和入口诊断。

    参数:
        本函数不接收业务参数；配置由 bundled runtime 固定提供。

    返回:
        ``check_runtime`` 产生的机器可读 runtime 报告。
    """

    # 运行期才准备 skill 根目录，避免模块导入阶段修改 sys.path。
    ensure_skill_root_on_path()

    # 从 skill 内 runtime 导入固定版本检查入口。
    from scripts.python.toolchain.wavedrom_runtime import check_runtime

    # 返回当前机器的 WaveDrom 版本状态。
    return check_runtime(smoke=False)

# read_required_tool_status 将 runtime 报告转换为外部工具列表。
def _read_required_tool_status(
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读取 WaveDrom runtime 并生成 required 工具状态。

    参数:
        settings: 已加载的 defaults.json 配置。

    返回:
        required 工具列表及缺失工具列表。
    """

    # 外部工具配置单独读取，避免把 npm 包伪装成可跳过的 skill。
    dict_tool_settings = read_tool_dependency_settings(settings)  # 外部工具依赖治理配置

    # 读取固定版本 runtime 诊断。
    dict_runtime = _read_wavedrom_runtime()  # 当前机器的 WaveDrom 运行状态

    # 将诊断映射到旧字段合同。
    return _required_tool_status(dict_tool_settings, dict_runtime)

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

    # 运行期加载 FPGA 路由 helper，避免顶层形成 toolchain 子模块循环导入。
    ensure_skill_root_on_path()
    from scripts.python.toolchain import dependency_fpga_route as module_type_dependency_fpga_route

    # FPGA developer 状态决定是否跳过旧 FPGA-Agent fallback。
    dict_developer_skills = module_type_dependency_fpga_route.fpga_developer_status(  # FPGA developer 覆盖旧 fallback 的判定依据
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

    # 收集 skill 依赖状态，把循环和 fallback 覆盖逻辑封装在 helper 内。
    dict_dependency_status = _collect_dependency_statuses(  # skill 依赖状态汇总
        dict_dependency_settings,  # 传递规范化依赖分组
        path_skills_root,  # 传递 skill 搜索根目录
        path_plugin_cache,  # 传递插件缓存根目录
        set_skipped,  # 传递当前配置版本有效 skip 集合
        bool_fpga_agent_skipped,  # 传递 developer 对 fallback 的覆盖判定
    )

    # 接受 helper 返回的 required 分组状态。
    list_required = dict_dependency_status["required"]  # required 依赖扫描结果

    # 接受 helper 返回的手动 fallback 分组状态。
    list_manual_fallback = dict_dependency_status["manual_fallback"]  # fallback 安装候选结果

    # 接受 helper 返回的全量 recommended 分组状态。
    list_recommended_all = dict_dependency_status["recommended_all"]  # 全量推荐扫描结果

    # 接受 helper 返回的 required 缺口明细。
    list_missing_required = dict_dependency_status["missing_required"]  # required 安装阻断明细

    # 将推荐缺口单独存放，供 prompt 决定是否询问用户。
    list_missing_recommended = dict_dependency_status["missing_recommended"]  # recommended 可选安装明细

    # WaveDrom runtime 检查固定通过 bundled wrapper，报告保留完整诊断。
    tuple_required_tools, tuple_missing_required_tools = _read_required_tool_status(settings)  # 取得外部工具状态与缺失子集

    # 组装旧 JSON 字段合同，避免上层 smoke 和用户脚本破坏。
    dict_report: dict[str, Any] = {  # 依赖检查总报告
        "version": 2,  # 报告 schema 版本，新增外部工具状态
        "ok": not list_missing_required and not list_missing_recommended and not tuple_missing_required_tools,  # 全部强制、推荐和工具依赖是否满足
        "required_ok": not list_missing_required and not tuple_missing_required_tools,  # required skill 与工具依赖是否满足
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
        "missing_required": list_missing_required,  # 报告需要的 required 缺口明细
        "missing_recommended": list_missing_recommended,  # prompt 需要询问安装或跳过的推荐依赖
        "required_tools": tuple_required_tools,  # required 外部工具逐项状态
        "missing_required_tools": tuple_missing_required_tools,  # 缺失 required 外部工具
        "skipped_recommended": sorted(set_skipped),  # 当前版本有效的 skipped recommended id
    }

    # 返回完整依赖报告供 CLI、smoke 和测试使用。
    return dict_report

# append_required_tool_prompt 将外部工具缺失提示追加到现有行缓冲。
def _append_required_tool_prompt(
    lines: list[str],
    missing_tools: list[dict[str, Any]],
) -> None:
    """追加 WaveDrom 等外部工具的固定安装命令提示。

    参数:
        lines: 当前 prompt 的可变行缓冲。
        missing_tools: required 外部工具缺失状态列表。

    返回:
        不返回业务值；直接扩展传入的行缓冲。
    """

    # 没有缺失工具时不改变已有 prompt 段落。
    if not missing_tools:

        # 空工具清单无需写标题或空行。
        return

    # 标题明确外部工具缺失会阻断 spec bundle 渲染。
    lines.append("Missing required external tools. These block spec bundle rendering:")

    # 每个工具都给出固定 runtime 的可复制安装入口。
    for dict_item in missing_tools:

        # 组合安装命令，避免在主 prompt 函数中重复长字符串。
        str_install_command = (  # 当前工具的可复制安装命令
            f"- {dict_item['id']}: install with `python -m "
            "scripts.python.toolchain.manage_skill_dependencies install "
            f"--dependency-id {dict_item['id']} --yes`"
        )  # 外部工具安装提示行

        # 追加当前工具的精确安装提示。
        lines.append(str_install_command)

    # 工具段落结束后留空，避免与 fallback 文本粘连。
    lines.append("")

# prompt_for_missing 把依赖报告渲染成用户可读提示文本。
def prompt_for_missing(report: dict) -> str:
    """根据依赖报告渲染用户可读提示文本。

    :param report: check_dependencies 生成的依赖报告。
    :return: 返回可直接展示给用户的提示文本；不写状态文件。
    """

    # required 缺失会阻塞远程和 Vivado 相关能力。
    list_missing_required = report.get("missing_required", [])  # prompt 中必须先展示的阻塞依赖

    # recommended 缺失需要询问用户安装或跳过。
    list_missing_recommended = report.get("missing_recommended", [])  # prompt 中需要用户选择的推荐依赖

    # required_tools 缺失会直接阻断 WaveDrom spec 伴随包发布。
    list_missing_required_tools = report.get("missing_required_tools", [])  # 缺失外部工具依赖

    # developer_skills 决定是否需要让用户选择 FPGA vendor。
    dict_developer_skills = report.get("developer_skills", {})  # FPGA developer 状态报告

    # selection_required 表示多个 vendor 同时可用且还未选择。
    bool_selection_required = bool(dict_developer_skills.get("selection_required"))  # 是否需要选择 FPGA vendor

    # manual_fallback 用于提示显式 fallback 路径。
    list_manual_fallback = report.get("manual_fallback", [])  # 手动 fallback 状态列表

    # 没有缺失也无需选择 vendor 时给出简短成功提示。
    bool_dependency_state_clear = (  # 汇总所有缺失和 vendor 选择条件
        not list_missing_required  # required skill 组全部满足
        and not list_missing_recommended  # recommended skill 组没有未跳过缺失
        and not list_missing_required_tools  # WaveDrom 等外部工具全部满足
        and not bool_selection_required  # 不需要用户选择 vendor
    )  # 当前依赖状态是否无需用户动作

    # 清单为空时给出简短成功提示。
    if bool_dependency_state_clear:

        # 成功提示保留 adapt 操作建议。
        return (
            "All readable-verilog-generator skill dependencies are installed. "
            "Run adapt after a fresh install to refresh project-local helper paths."
        )

    # lines 逐行拼接，保持原 prompt 文本合同。
    list_lines = [  # prompt 输出行集合
        "readable-verilog-generator dependency check found missing skills or tools.",  # prompt 首行摘要
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

    # 外部工具缺失必须给出精确版本和安装命令，不允许推荐 wavedrom-cli。
    _append_required_tool_prompt(list_lines, list_missing_required_tools)

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

# record_skip 记录当前版本下 recommended 依赖的 skip 状态。
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

# _install_specs_by_skill 建立 install_specs 的技能索引。
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

# _selected_install_specs 选择本轮实际需要执行的安装规格。
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

# _dependency_status 计算单个依赖配置的安装状态。
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
        "kind": kind,  # 记录该依赖来自 required、recommended 或 fallback 分组
        "url": item["url"],  # 依赖来源 URL
        "purpose": item.get("purpose", ""),  # 依赖用途说明
        "present": not list_missing,  # 是否完整满足
        "skills": item["skills"],  # 主 skill 集合
        "selected_skill_set": list_selected_skill_set,  # 当前用于判定的 skill 集合
        "missing_skills": list_missing,  # 记录该依赖未覆盖的 skill 名称
        "skill_paths": dict_skill_paths,  # 已发现 skill 路径
    }

# _resolve_skill_set 解析一组 skill 的本地安装状态。
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

# find_skill 在 skills 根目录和插件缓存中查找指定 skill。
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

# read_state 读取并规范化依赖状态 JSON。
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

# write_state 以稳定格式写回依赖状态 JSON。
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

# default_skills_root 计算默认的 Codex skills 根目录。
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

# default_plugin_cache 返回用于发现插件随附 skill 的本地缓存目录。
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

# default_installer_script 定位默认 skill-installer helper。
def default_installer_script() -> Path:
    """返回默认 GitHub skill 安装 helper 路径。

    :param: 本函数不接收业务参数；路径从 default_skills_root 推导。
    :return: install-skill-from-github.py 的默认路径。
    """

    # helper 位于系统 skill-installer 的 scripts 目录。
    path_installer = default_skills_root() / ".system" / "skill-installer" / "scripts" / "install-skill-from-github.py"  # 默认 installer 路径

    # 返回 helper 路径，存在性由 install_missing 检查。
    return path_installer

# github_repo_slug 从 GitHub URL 提取 owner/repo slug。
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

# _active_skipped_recommended 过滤仍和当前配置匹配的 skip 记录。
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

# _dependency_fingerprint 为 recommended 依赖生成版本化指纹。
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

# print_json 向 stdout 输出机器可读 JSON object。
def print_json(payload: dict) -> None:
    """向 stdout 写出机器可读 JSON object。

    :param payload: 需要输出给上层程序解析的 JSON object。
    :return: 不返回业务值；JSON 文本写入 stdout。
    """

    # JSON 文本保持缩进和非 ASCII 原样，延续旧 CLI 输出合同。
    str_payload = json.dumps(payload, indent=2, ensure_ascii=False)  # print_json 中 str_payload 的当前用途

    # sys.stdout.write 避免把机器协议误判为人类可读 print。
    sys.stdout.write(str_payload + "\n")
