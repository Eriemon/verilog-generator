"""
报告本地 Verilog 工具预检是否需要远程服务器选择。

stdout_protocol: json
本 CLI 向标准输出写入一个 JSON object，供 CI 或上层脚本直接解析。
"""

# future annotations 避免 argparse 类型提示在运行期求值。
from __future__ import annotations

# 标准库负责 CLI 参数、JSON 输出、工具探测和脚本导入路径。
import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# skill 根目录用于脚本直运行时定位 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # runtime 包和默认配置所在目录

@dataclass(frozen=True)
class PreflightContext:
    """保存一次工具链预检生成报告所需的上下文。"""

    # readiness 决定静态、编译或仿真级别的外部工具要求。
    str_readiness: str  # 调用方请求的验证准备级别

    # vivado 主命令用于判断本机是否具备综合入口。
    dict_vivado: dict[str, Any]  # vivado 命令探测结果

    # xsim 需要同时记录 xvlog、xelab 和 xsim 三个命令。
    dict_xsim_tools: dict[str, dict[str, Any]]  # xsim 三件套探测结果

    # 远程选择标记决定 required_action 的分支。
    bool_remote_selection_required: bool  # 是否必须先选择远程服务器

    # confirmed 为空代表本地尚未锁定远端执行目标。
    dict_confirmed: dict[str, Any] | None  # 已确认的远程服务器配置

    # server list 是 erie-remote-ssh 写入的本地私有清单。
    path_server_list: Path  # 本地服务器清单路径

    # runtime 配置路径用于提醒远端工作目录需要准备的文件。
    str_runtime_config: str  # 远端 runtime 配置相对路径

# _ensure_runtime_import_path 只在入口函数内调整导入路径。
def _ensure_runtime_import_path() -> None:
    """
    确保脚本从仓库任意位置运行时能导入 runtime 包。

    :param: 此辅助函数没有外部业务参数。
    :return: 不返回业务值；必要时只更新当前进程的 import 搜索路径。
    """

    # skill 根目录文本用于和 sys.path 中的已有项比较。
    str_skill_root = str(PATH_SKILL_ROOT)  # sys.path 需要的 skill 根目录文本

    # 仅在缺少 skill 根目录时插入，避免重复改变模块解析顺序。
    if str_skill_root not in sys.path:

        # 将 runtime 包所在根目录放到导入搜索路径最前。
        sys.path.insert(0, str_skill_root)

# create_parser 保持原有 preflight CLI 参数合同。
def create_parser() -> argparse.ArgumentParser:
    """
    创建本地 Verilog 工具预检脚本的参数解析器。

    :return: 已注册 settings 与 readiness 参数的 argparse 解析器。
    """

    # 描述文本保持英文，延续既有命令行帮助输出。
    str_description = "Preflight local Verilog validation tools."  # argparse 描述文本

    # parser 只注册参数，不执行任何文件或工具检查。
    parser = argparse.ArgumentParser(description=str_description)  # 预检命令解析器

    # 默认 settings 路径沿用 skill 默认配置文件。
    path_default_settings = PATH_SKILL_ROOT / "config" / "defaults.json"  # 默认配置文件路径

    # --settings 允许调用方指定治理配置来源。
    parser.add_argument("--settings", type=Path, default=path_default_settings)

    # runtime validation 模块提供 readiness 枚举，入口内延迟导入后再覆盖 choices。
    parser.add_argument("--readiness", default="static")

    # 返回注册了兼容参数名的解析器。
    return parser

# main 连接参数解析、runtime 配置加载和 JSON 输出。
def main(argv: list[str] | None = None) -> int:
    """
    执行本地 Verilog 工具链预检。

    :param argv: 可选命令行参数列表；为 None 时读取真实命令行。
    :return: 预检完成时返回 0；参数错误仍由 argparse 负责退出。
    """

    # 脚本入口阶段再准备 runtime 导入路径。
    _ensure_runtime_import_path()

    # runtime validation 提供 readiness 合法值与归一化函数。
    from scripts.python.validation.validation import READINESS_LEVELS, require_readiness

    # 解析命令行参数，保持 argparse 默认错误语义。
    parser = create_parser()  # 预检参数解析器

    # readiness choices 必须来自 runtime 枚举，避免脚本复制业务列表。
    parser._option_string_actions["--readiness"].choices = READINESS_LEVELS  # runtime 验证层 readiness 枚举

    # 解析调用方传入的命令行参数。
    args = parser.parse_args(argv)  # 预检命令行参数

    # runtime config 模块在路径准备后导入。
    from scripts.python.workflow.config import load_settings

    # 读取治理配置，包含远程优先和本地工具策略。
    dict_settings = load_settings(args.settings)  # Verilog skill 治理配置

    # readiness 统一走 runtime 校验，保持错误信息一致。
    str_readiness = require_readiness(args.readiness)  # 归一化验证准备级别

    # 根据本地工具、策略和远程选择生成预检报告。
    dict_report = build_report(dict_settings, str_readiness)  # 工具链预检报告

    # 输出机器可读 JSON，便于 CI 或上层脚本消费。
    sys.stdout.write(json.dumps(dict_report, indent=2, ensure_ascii=False) + "\n")

    # 预检脚本只报告状态，不因需要人工选择而失败。
    return 0

# build_report 汇总本地工具状态和远程选择要求。
def build_report(dict_settings: dict[str, Any], str_readiness: str) -> dict[str, Any]:
    """
    根据治理配置和 readiness 生成工具链预检报告。

    :param dict_settings: 已加载的 Verilog skill 治理配置。
    :param str_readiness: 调用方请求的验证准备级别。
    :return: 保持旧 JSON 字段合同的工具链预检报告。
    """

    # runtime helper 延迟导入，避免模块导入时修改 sys.path 或读取配置。
    _ensure_runtime_import_path()

    # runtime config 提供策略读取和远程配置路径。
    from scripts.python.workflow.config import policy_setting, remote_setting
    from scripts.python.remote.remote_selection import (
        remote_runtime_config_relpath,
        resolve_confirmed_remote_server,
    )
    from scripts.python.validation.validation import readiness_at_least

    # Vivado 主命令用于判断本地综合工具是否可见。
    dict_vivado = _tool("vivado")  # vivado 可执行文件探测结果

    # xsim 编译链需要三个命令都可见才算完整。
    dict_xsim_tools = {
        "xvlog": _tool("xvlog"),  # xsim Verilog 编译前端探测结果
        "xelab": _tool("xelab"),  # elaboration 阶段命令探测结果
        "xsim": _tool("xsim"),  # 仿真运行器命令探测结果
    }  # xsim 完整工具链探测结果

    # compile 及以上 readiness 才会触发外部工具策略。
    bool_requires_external = readiness_at_least(str_readiness, "compile")  # 是否需要外部验证能力

    # policy 字段决定是否优先要求远程执行。
    bool_remote_first = bool(policy_setting(dict_settings, "prefer_remote_for_external_validation", True))  # 是否优先远程验证

    # 未配置远程时是否阻断外部验证。
    bool_remote_blocking = bool(policy_setting(dict_settings, "block_when_remote_unconfigured", True))  # 远程未配置是否阻断

    # 是否允许在未显式选择远程时直接启用本地外部工具。
    str_allow_local_key = "allow_implicit_local_external_validation"  # 隐式本地外部验证策略键

    # policy_setting 可能返回 JSON 原始值，先保留再转布尔。
    value_allow_implicit_local = policy_setting(dict_settings, str_allow_local_key, False)  # 隐式本地外部验证原始策略值

    # 布尔化后参与远程选择策略判断。
    bool_allow_implicit_local = bool(value_allow_implicit_local)  # 是否允许隐式本地外部验证

    # 外部验证、远程优先和隐式本地策略共同决定是否要求远程选择。
    bool_remote_policy_needs_selection = bool_remote_first or bool_remote_blocking  # 远程优先或未配置阻断是否生效

    # 禁止隐式本地外部验证时，也必须显式选择远程。
    bool_local_is_disallowed = not bool_allow_implicit_local  # 隐式本地验证是否被策略禁止

    # 合并远程优先、阻断和禁止隐式本地三类策略。
    bool_remote_policy_needs_selection = bool_remote_policy_needs_selection or bool_local_is_disallowed  # 最终远程选择策略判断

    # readiness 需要外部验证时才应用远程选择策略。
    bool_remote_selection_required = bool_requires_external and bool_remote_policy_needs_selection  # 当前 readiness 是否需要先确认远程服务器

    # 已确认服务器来自本地 remote selection 状态。
    dict_confirmed = resolve_confirmed_remote_server(dict_settings)  # 本次预检读取到的远程确认结果

    # server list 路径用于提示用户刷新远程清单。
    path_server_list = Path(remote_setting(dict_settings, "server_list"))  # 预期存在的服务器清单文件

    # 远程 runtime 配置路径用于提示远端工作目录准备工作。
    str_runtime_config = remote_runtime_config_relpath(dict_settings)  # 远程验证工作目录配置文件

    # settings meta 记录本地配置加载和旧状态迁移情况。
    dict_meta = dict_settings.get("__verilog_settings_meta__", {})  # settings 读取元信息

    # meta 只有是字典时才可信。
    bool_meta_is_dict = isinstance(dict_meta, dict)  # meta 是否可按字典读取

    # 旧远程状态用于给出迁移提示。
    # 非字典 meta 不参与旧状态迁移提示。
    value_legacy_state = dict_meta.get("legacy_remote_state", []) if bool_meta_is_dict else []  # 旧版远程状态原始值

    # 下游只需要判断是否存在旧状态条目。
    list_legacy_remote_state = value_legacy_state  # 旧版远程状态条目列表

    # local settings 是否已加载影响迁移提示优先级。
    value_local_settings_loaded = dict_meta.get("local_settings_loaded") if bool_meta_is_dict else False  # 本地配置加载原始标记

    # 布尔化后用于判断是否还需要迁移旧状态。
    bool_local_settings_loaded = bool(value_local_settings_loaded)  # 本地 settings 覆盖是否已加载

    # dataclass 避免在报告 helper 之间传递一串弱语义参数。
    preflight_context_preflight_context: PreflightContext = PreflightContext(  # 单次预检的报告上下文
        str_readiness=str_readiness,  # 报告沿用的 readiness

        # vivado 载荷来自本地 PATH 探测。
        dict_vivado=dict_vivado,  # 本地 vivado 命令状态

        # xsim 载荷保留三条命令的独立状态。
        dict_xsim_tools=dict_xsim_tools,  # xsim 命令集合

        # 远程选择标记决定后续 action 分支。
        bool_remote_selection_required=bool_remote_selection_required,  # 外部验证远程门禁

        # confirmed 载荷来自 remote selection facade。
        dict_confirmed=dict_confirmed,  # 当前远程确认结果

        # server list 路径用于缺清单提示。
        path_server_list=path_server_list,  # 本地私有服务器清单

        # 远端配置相对路径用于后续 required_action 文本拼接。
        str_runtime_config=str_runtime_config,  # required_action 中展示的远端配置文件名
    )

    # 汇总本地工具和远程选择状态。
    dict_report = _base_report(preflight_context_preflight_context)  # 本地工具状态和远程选择状态的基础报告

    # 根据是否需要远程选择补充原因和操作建议。
    _fill_remote_action(
        dict_report=dict_report,
        preflight_context=preflight_context_preflight_context,
        list_legacy_remote_state=list_legacy_remote_state,
        bool_local_settings_loaded=bool_local_settings_loaded,
    )

    # 返回完整预检报告给 CLI 输出。
    return dict_report

# _base_report 组装不含 required_action 的稳定 JSON 字段。
def _base_report(preflight_context: PreflightContext) -> dict[str, Any]:
    """
    组装预检报告中的稳定字段。

    :param preflight_context: 本次预检已经收集好的本地与远程状态。
    :return: 不含 required_action 分支解释的基础报告。
    """

    # xsim 三个命令全部存在才认为本地 xsim 可用。
    bool_xsim_available = all(dict_item["found"] for dict_item in preflight_context.dict_xsim_tools.values())  # xsim 三件套是否全部可见

    # 服务器清单存在性用于提示是否需要 erie-remote-ssh 刷新。
    bool_server_list_exists = preflight_context.path_server_list.exists()  # 本地服务器清单是否存在

    # confirmed 为空时推荐服务器字段保持 None，保留旧 JSON 合同。
    str_recommended_server = _recommended_server_id(preflight_context.dict_confirmed)  # 预检报告中的推荐服务器 ID

    # local 字段保留 vivado/xsim 两组探测结果。
    dict_local = _local_report(  # 本地外部验证工具状态
        dict_vivado=preflight_context.dict_vivado,  # vivado 命令探测载荷
        dict_xsim_tools=preflight_context.dict_xsim_tools,  # xsim 命令集合载荷
        bool_xsim_available=bool_xsim_available,  # xsim 是否整体可用
    )

    # remote 字段保留远程选择和配置路径状态。
    dict_remote = _remote_report(  # 远程选择与配置状态
        dict_confirmed=preflight_context.dict_confirmed,  # 远程确认状态
        str_recommended_server=str_recommended_server,  # 推荐服务器 ID
        path_server_list=preflight_context.path_server_list,  # 服务器清单路径
        bool_server_list_exists=bool_server_list_exists,  # 服务器清单存在性
        str_runtime_config=preflight_context.str_runtime_config,  # 远端执行前需要存在的配置文件
    )

    # 返回与旧脚本一致的报告字段。
    return {
        "version": 1,  # 预检报告格式版本
        "readiness": preflight_context.str_readiness,  # 报告记录的验证准备级别
        "local": dict_local,  # 本地 vivado/xsim 可用性载荷
        "remote_selection_required": preflight_context.bool_remote_selection_required,  # 外部验证前是否缺少远程选择
        "remote": dict_remote,  # 远程服务器选择和配置状态载荷
    }

# _recommended_server_id 保持旧报告中 recommended_server 的 None 语义。
def _recommended_server_id(dict_confirmed: dict[str, Any] | None) -> str | None:
    """
    从已确认远程配置中提取报告使用的服务器 ID。

    :param dict_confirmed: remote selection facade 返回的已确认服务器配置。
    :return: 已确认服务器 ID；未确认时返回 None。
    """

    # confirmed 为空时，旧报告合同要求 recommended_server 为 None。
    if dict_confirmed is None:

        # 没有确认服务器时不推荐具体目标。
        return None

    # server_id 是 remote_selection facade 写入的稳定字段。
    return dict_confirmed["server_id"]  # 已确认远程服务器 ID

# _local_report 将本地工具探测结果压回旧 JSON 结构。
def _local_report(
    *,
    dict_vivado: dict[str, Any],
    dict_xsim_tools: dict[str, dict[str, Any]],
    bool_xsim_available: bool,
) -> dict[str, Any]:
    """
    组装报告中的 local 字段。

    :param dict_vivado: vivado 命令探测结果。
    :param dict_xsim_tools: xvlog、xelab、xsim 三个命令的探测结果。
    :param bool_xsim_available: xsim 三件套是否全部可见。
    :return: 与旧预检报告兼容的 local 子对象。
    """

    # xsim 嵌套结构保持旧 JSON 字段名。
    dict_xsim = {
        "available": bool_xsim_available,  # 本地 xsim 工具链完整性
        "tools": dict_xsim_tools,  # xvlog/xelab/xsim 三个命令探测结果
    }  # xsim 预检状态

    # local 层继续区分 vivado 主命令和 xsim 工具链。
    return {
        "vivado": dict_vivado,  # 本地 vivado 命令探测结果
        "xsim": dict_xsim,  # 本地 xsim 命令集合状态
    }

# _remote_report 将远程治理状态压回旧 JSON 结构。
def _remote_report(
    *,
    dict_confirmed: dict[str, Any] | None,
    str_recommended_server: str | None,
    path_server_list: Path,
    bool_server_list_exists: bool,
    str_runtime_config: str,
) -> dict[str, Any]:
    """
    组装报告中的 remote 字段。

    :param dict_confirmed: 当前已确认的远程服务器配置。
    :param str_recommended_server: 报告展示用的服务器 ID。
    :param path_server_list: 本地私有服务器清单路径。
    :param bool_server_list_exists: 服务器清单文件是否存在。
    :param str_runtime_config: 远端工作目录中的 runtime 配置相对路径。
    :return: 与旧预检报告兼容的 remote 子对象。
    """

    # remote 层字段用于上层提示远程选择和配置准备动作。
    return {
        "recommended_server": str_recommended_server,  # 已确认服务器 ID
        "recommended_server_name": None,  # 旧报告合同保留的名称字段
        "server_confirmed": dict_confirmed is not None,  # 是否已有确认服务器
        "server_list_path": str(path_server_list),  # 本地 server list 文件路径
        "server_list_exists": bool_server_list_exists,  # server list 文件是否存在
        "remote_runtime_config": str_runtime_config,  # 远端工作目录内的配置文件
    }

# _fill_remote_action 根据远程策略补充人工操作提示。
def _fill_remote_action(
    *,
    dict_report: dict[str, Any],
    preflight_context: PreflightContext,
    list_legacy_remote_state: list[Any],
    bool_local_settings_loaded: bool,
) -> None:
    """
    向预检报告补充 reason 和 required_action 字段。

    :param dict_report: 正在原地补充的预检报告。
    :param preflight_context: 本次预检已经收集好的本地与远程状态。
    :param list_legacy_remote_state: 旧版远程状态条目，用于提示迁移。
    :param bool_local_settings_loaded: 本地 settings 覆盖是否已加载。
    :return: 不返回业务值；函数会原地更新 dict_report。
    """

    # 不需要远程选择时，说明当前 readiness 可以直接继续。
    if not preflight_context.bool_remote_selection_required:

        # 本地静态验证或已有工具足以满足当前 readiness。
        str_local_reason = "Local static validation does not require Vivado/xsim, or local Vivado/xsim is available."  # 本地即可继续的原因文本

        # reason 字段给上层 CLI 或 CI 解释当前预检状态。
        dict_report["reason"] = str_local_reason  # 当前预检分支的原因说明

        # 无需额外人工动作。
        dict_report["required_action"] = None  # 静态或本地可用场景不需要操作提示

        # 当前分支已经补齐报告字段。
        return

    # 远程优先策略要求先确认远程服务器。
    str_remote_first_reason = (
        "External readiness uses a remote-first policy; "  # 说明外部验证进入远程优先策略
        "local Vivado/xsim availability does not auto-enable local external validation."  # 说明本地工具不会自动放行
    )

    # 外部 readiness 进入远程优先策略分支。
    dict_report["reason"] = str_remote_first_reason  # 远程优先分支的原因说明

    # 旧状态存在且本地配置未加载时，优先提示迁移。
    if list_legacy_remote_state and not bool_local_settings_loaded:

        # 迁移提示保留旧脚本的用户可见文本。
        str_legacy_action = (
            "Legacy .erie-verilog-generator-state remote settings were detected. "  # 提醒存在旧状态
            "Migrate the selected server into .settings/remote-selection.local.json and regenerate "  # 提醒迁移选择文件
            ".settings/server_list.local.json before remote validation."  # 提醒刷新服务器清单
        )

        # required_action 在旧状态分支中给出迁移步骤。
        dict_report["required_action"] = str_legacy_action  # 旧状态迁移的操作建议

        # 迁移提示优先级最高。
        return

    # 没有服务器清单时，需要先刷新本地 server list。
    if not preflight_context.path_server_list.exists():

        # 提示 erie-remote-ssh 负责生成本地私有 server list。
        str_server_list_action = (
            "Create or refresh .settings/server_list.local.json through erie-remote-ssh before remote validation. "  # 提醒刷新清单
            f"Expected path: {preflight_context.path_server_list}"  # 给出治理配置中的期望路径
        )

        # required_action 在缺清单分支中指向 erie-remote-ssh。
        dict_report["required_action"] = str_server_list_action  # 缺少 server list 的操作建议

        # 缺少 server list 时无法继续推荐具体服务器。
        return

    # 已确认服务器时，提示远端 runtime 配置准备。
    if dict_report["remote"]["server_confirmed"]:

        # server_id 已由前面的 confirmed 判断保证存在。
        str_server_id = preflight_context.dict_confirmed["server_id"]  # 操作提示中展示的服务器标识

        # 使用已确认服务器并检查远端配置文件。
        str_confirmed_action = (
            f"Use the selected remote server {str_server_id} and ensure the remote workdir "  # 指明已选服务器
            f"contains {preflight_context.str_runtime_config} before remote validation."  # 提醒远端配置文件
        )

        # required_action 在已确认服务器分支中提示远端文件准备。
        dict_report["required_action"] = str_confirmed_action  # 已确认服务器的操作建议

        # 已确认服务器分支完成。
        return

    # server list 存在但未选择服务器时，提示写入 remote selection。
    str_select_action = (
        "Select a remote server in .settings/remote-selection.local.json "  # 提醒写入本地选择
        f"and ensure {preflight_context.str_runtime_config} "  # 指明远端配置文件
        "exists on the remote workdir before remote validation."  # 提醒远端配置文件存在
    )

    # required_action 在未选择服务器分支中提示写入 remote selection。
    dict_report["required_action"] = str_select_action  # 尚未选择服务器的操作建议

# _tool 探测单个可执行文件在 PATH 中是否可见。
def _tool(str_name: str) -> dict[str, str | bool | None]:
    """
    返回单个工具的 PATH 探测结果。

    :param str_name: 需要在 PATH 中查找的命令名称。
    :return: 包含 found 和 path 字段的旧报告兼容结构。
    """

    # shutil.which 保留平台自身的 PATH 搜索规则。
    str_path = shutil.which(str_name)  # 工具可执行文件路径

    # 返回旧 JSON 字段，供调用方继续读取 found/path。
    return {
        "found": str_path is not None,  # 工具是否在 PATH 中可见
        "path": str_path,  # 平台 PATH 搜索得到的可执行文件路径
    }

# 脚本直运行时将 main 返回值交给 shell。
if __name__ == "__main__":

    # SystemExit 保留命令行退出码语义。
    raise SystemExit(main())
