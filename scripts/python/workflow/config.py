"""加载 Verilog skill 配置并展开仓库内路径。"""

# future import 必须位于模块导入区最前，保证类型标注延迟求值。
from __future__ import annotations

# 标准库用于读取 JSON、展开环境变量与处理路径。
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

# 工作区工具负责把配置解析限制在当前仓库边界内。
from .workspace import find_workspace_root, require_workspace_root

# skill 内置配置目录固定跟随安装主体移动。
CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"  # skill 内置配置目录

# 默认配置文件提供没有项目覆盖时的基线参数。
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "defaults.json"  # 默认 settings JSON 路径

# 项目本地配置目录承载不进入发布包的覆盖文件。
PROJECT_SETTINGS_DIR = ".settings"  # 项目本地设置目录名

# 本地 Verilog 覆盖文件允许仓库按需调整默认配置。
LOCAL_VERILOG_SETTINGS_REL = ".settings/verilog.local.json"  # Verilog 本地覆盖相对路径

# 本地远程选择文件记录已确认的远程服务器选择。
LOCAL_REMOTE_SELECTION_REL = ".settings/remote-selection.local.json"  # 远程选择相对路径

# 本地服务器列表文件保存私有 SSH 目标清单。
LOCAL_SERVER_LIST_REL = ".settings/server_list.local.json"  # 私有服务器列表相对路径

# 远程运行时配置文件用于同步到远程工作目录。
REMOTE_RUNTIME_SETTINGS_REL = ".settings/verilog.remote.json"  # 远程运行时配置相对路径

# 旧版状态目录只用于兼容历史远程选择文件。
LEGACY_REMOTE_STATE_DIR = ".erie-verilog-generator-state"  # 旧版远程状态目录名

# 旧版远程选择文件继续进入迁移扫描。
LEGACY_REMOTE_SELECTION_REL = ".erie-verilog-generator-state/remote_server_selection.json"  # 旧版服务器选择路径

# 旧版工具链选择文件继续作为只读兼容来源。
LEGACY_REMOTE_TOOLCHAIN_REL = ".erie-verilog-generator-state/remote_toolchain_selection.json"  # 旧版工具链选择路径

# 旧版服务器列表文件只用于报告已有历史状态。
LEGACY_REMOTE_SERVER_LIST_REL = ".erie-verilog-generator-state/server_list.local.json"  # 旧版服务器列表路径

# 路径模板中的 ${name} 片段由配置加载器统一展开。
_token_re = re.compile(r"\$\{([^}]+)\}")  # settings 字符串模板占位符匹配器

# 顶层 settings 合同固定为当前默认配置暴露的八个键。
SETTINGS_TOP_LEVEL_KEYS = {  # 本轮强校验允许的顶层键集合
    "version",  # settings 版本号键
    "paths",  # 路径配置段键
    "policy",  # 策略配置段键
    "remote",  # 远程配置段键
    "skill_dependencies",  # 依赖路由配置段键
    "tool_dependencies",  # npm/Node 外部工具依赖配置段键
    "validation",  # 校验配置段键
    "workflow",  # 工作流配置段键
    "fpga_developer_routing",  # FPGA developer 路由配置段键
}

# 除 version 外，其余顶层段都应保持 JSON object 结构。
SETTINGS_TOP_LEVEL_OBJECT_KEYS = {  # 必须保持 object 结构的顶层段集合
    "paths",  # 路径配置段必须是 object
    "policy",  # 策略配置段必须是 object
    "remote",  # 远程配置段必须是 object
    "skill_dependencies",  # 依赖路由配置段必须是 object
    "tool_dependencies",  # 外部工具依赖配置段必须是 object
    "validation",  # 校验配置段必须是 object
    "workflow",  # 工作流配置段必须是 object
    "fpga_developer_routing",  # FPGA developer 路由配置段必须是 object
}

# 项目本地 settings 可附带不参与 runtime 合同的辅助块。
LOCAL_SETTINGS_HELPER_KEYS = {  # 项目本地辅助块键集合
    "local",  # 本地工具与环境辅助块
    "commands",  # 本地快捷命令辅助块
}

# skill 根目录定位入口。
def skill_root() -> Path:
    """
    返回当前 installable skill 的根目录。

    :param: 无外部参数。
    :return: `skills/readable-verilog-generator` 或安装副本根路径。
    """

    # 依据 workflow 配置模块位置回到 skill 主体根目录。
    return Path(__file__).resolve().parents[3]

# 源码仓库根目录探测入口。
def _source_repository_root() -> Path | None:
    """
    返回源码仓库布局下的项目根目录。

    :param: 无外部参数。
    :return: 命中 `<repo>/skills/<name>` 且治理标记完整时返回仓库根，否则返回 `None`。
    """

    # 复用 skill 根目录判断当前布局是否落在标准 skills 目录下。
    path_skill_root = skill_root()  # 当前 skill 主体根目录

    # 安装态可能也位于 `.../skills/<name>`，但非 skills 父目录直接排除源码布局判断。
    if path_skill_root.parent.name != "skills":

        # 非标准源码布局时返回空值，让调用方回退到安装态语义。
        return None

    # 按 `<repo>/skills/<name>` 结构反推出源码仓库根目录候选。
    path_candidate_root = path_skill_root.parent.parent  # 源码仓库根目录候选

    # 源码仓库必须把 skill 放在固定的 `skills/<name>` 位置。
    path_expected_skill = path_candidate_root / "skills" / path_skill_root.name  # 源码仓库预期的 skill 位置

    # docs、tests 和 tests/smoke 共同构成源码仓库治理标记。
    tuple_repo_markers = (
        path_candidate_root / "docs" / "handoff" / "HANDOFF.md",  # 源码仓库交接文档标记
        path_candidate_root / "tests" / "smoke" / "run_smoke.py",  # 源码仓库 smoke 包入口标记
        path_candidate_root / "tests",  # 源码仓库回归测试目录标记
    )  # 源码仓库布局所需的最小治理标记集合

    # 只有 skill 位置精确匹配标准布局时才继续认定源码仓库根。
    if path_expected_skill.resolve() != path_skill_root.resolve():

        # 路径不匹配时说明当前目录只是类似布局，不应越级回到上层目录。
        return None

    # docs/tests/tests-smoke 任一标记缺失都视为安装态或不完整副本。
    if not all(path_marker.exists() for path_marker in tuple_repo_markers):

        # 治理标记不完整时不把上层目录当成项目根。
        return None

    # 返回通过布局与治理标记双重校验的源码仓库根。
    return path_candidate_root

# 项目根目录定位入口。
def project_root() -> Path:
    """
    返回包含 skill 主体的项目根目录。

    :param: 无外部参数。
    :return: 标准仓库布局下返回工作区根，安装副本下返回 skill 根目录。
    """

    # 安装态默认把 skill 主体自身当作项目根，避免验证产物逃逸到上层目录。
    path_skill_root = skill_root()  # 安装态默认项目根目录

    # 源码仓库布局需要显式命中治理标记后才允许越过 skills 目录。
    path_source_root = _source_repository_root()  # 源码仓库根目录

    # 命中源码仓库布局时返回仓库根，供 tests、smoke 和 reports 路径复用。
    if path_source_root is not None:

        # 源码仓库允许把项目级验证产物放到 skill 体外的根目录。
        return path_source_root

    # 安装态或不完整副本统一把 skill 根当作项目根，避免产物越出当前 workspace。
    return path_skill_root

# 本地 Verilog 覆盖路径入口。
def local_verilog_settings_path(*, start: Path | None = None) -> Path:
    """
    返回项目本地 Verilog 覆盖配置路径。

    :param start: 用于向上查找工作区根的起始路径。
    :return: 当前工作区内 `.settings/verilog.local.json` 的绝对路径。
    """

    # 将本地覆盖文件固定限制在当前工作区内。
    return require_workspace_root(purpose="local Verilog settings", start=start) / LOCAL_VERILOG_SETTINGS_REL

# 本地远程选择路径入口。
def local_remote_selection_path(*, start: Path | None = None) -> Path:
    """
    返回项目本地远程服务器选择文件路径。

    :param start: 用于向上查找工作区根的起始路径。
    :return: 当前工作区内远程选择文件的绝对路径。
    """

    # 将远程选择状态定位到当前工作区的私有配置目录。
    return require_workspace_root(purpose="remote selection", start=start) / LOCAL_REMOTE_SELECTION_REL

# 本地服务器列表路径入口。
def local_server_list_path(*, start: Path | None = None) -> Path:
    """
    返回项目本地远程服务器列表文件路径。

    :param start: 用于向上查找工作区根的起始路径。
    :return: 当前工作区内服务器列表文件的绝对路径。
    """

    # 服务器列表包含本地私有信息，只在工作区内解析。
    return require_workspace_root(purpose="remote server list", start=start) / LOCAL_SERVER_LIST_REL

# 远程运行时配置相对路径入口。
def remote_runtime_settings_relpath() -> str:
    """
    返回远程工作目录内固定的运行时配置相对路径。

    :param: 无外部参数。
    :return: 远程工作区下 `.settings/verilog.remote.json`。
    """

    # 远程运行脚本只需要相对路径，避免携带本机绝对路径。
    return REMOTE_RUNTIME_SETTINGS_REL

# 旧版远程状态路径兼容入口。
def legacy_remote_state_paths(*, start: Path | None = None) -> list[Path]:
    """
    返回当前工作区内旧版远程状态文件路径。

    :param start: 用于向上查找工作区根的起始路径。
    :return: 旧版 selection、toolchain 与 server list 的候选绝对路径列表。
    """

    # 找到承载旧版状态目录的当前工作区根。
    path_workspace_root = require_workspace_root(purpose="legacy remote state", start=start)  # 历史状态所属工作区根

    # 返回固定历史文件顺序，便于报告层稳定展示。
    return [
        path_workspace_root / LEGACY_REMOTE_SELECTION_REL,
        path_workspace_root / LEGACY_REMOTE_TOOLCHAIN_REL,
        path_workspace_root / LEGACY_REMOTE_SERVER_LIST_REL,
    ]

# 顶层 settings 强校验入口。
def _validate_top_level_settings_dict(dict_raw: dict[str, Any], settings_path: Path) -> None:
    """
    校验 settings 顶层键集合和浅层类型合同。

    :param dict_raw: JSON 解析后的原始 settings 对象。
    :param settings_path: 当前 settings 文件路径，用于错误定位。
    :return: 校验通过时不返回业务值。
    :raises ValueError: 当出现未知顶层键或已知顶层段类型错误时抛出。
    """

    # 未知顶层键会让运行时和测试夹具脱离当前真实合同。
    set_unknown_keys = set(dict_raw) - SETTINGS_TOP_LEVEL_KEYS  # 不在当前顶层合同中的键集合

    # 发现未知键时直接阻断，避免旧 schema 静默混入当前配置。
    if set_unknown_keys:

        # 错误消息保留排序后的未知键名，方便测试和用户定位。
        raise ValueError(
            "> ERR: [Python] unknown top-level settings key(s) in "
            f"{settings_path}: {', '.join(sorted(set_unknown_keys))}"
        )

    # version 是唯一允许的非 object 顶层键。
    if "version" in dict_raw and not isinstance(dict_raw["version"], int):

        # 非整数版本号会让配置版本判断失去稳定语义。
        raise ValueError(f"> ERR: [Python] settings.version must be an integer: {settings_path}")

    # 逐个检查必须保持 object 结构的顶层段。
    for section_key in sorted(SETTINGS_TOP_LEVEL_OBJECT_KEYS):

        # 未声明的可选段允许缺席。
        if section_key not in dict_raw:

            # 缺席段继续由 defaults 或调用方决定是否需要。
            continue

        # 顶层段存在时必须是 JSON object。
        if not isinstance(dict_raw[section_key], dict):

            # 错误消息保持现有 settings.<section> 形式，方便测试精确断言。
            raise ValueError(f"> ERR: [Python] settings.{section_key} must be an object: {settings_path}")

# 判断 settings 路径是否对应项目本地覆盖文件。
def _is_project_local_settings_file(settings_path: Path) -> bool:
    """
    判断给定 settings 路径是否是项目本地覆盖文件。

    :param settings_path: 当前正在读取的 settings 文件路径。
    :return: 命中 `.settings/verilog.local.json` 文件名时返回 `True`。
    """

    # verilog.local.json 是固定命名的项目本地覆盖文件。
    return settings_path.name == Path(LOCAL_VERILOG_SETTINGS_REL).name

# 提取 runtime 合同真正消费的顶层 settings 视图。
def _runtime_settings_view(dict_raw: dict[str, Any], settings_path: Path) -> dict[str, Any]:
    """
    返回参与 runtime 合同校验与模板展开的 settings 顶层视图。

    :param dict_raw: 从 JSON 文件直接解析得到的原始 settings 对象。
    :param settings_path: 当前正在读取的 settings 文件路径。
    :return: 对普通 settings 返回原对象；对项目本地覆盖文件剥离 helper-only 顶层块后返回副本。
    """

    # 非项目本地覆盖文件必须按完整顶层合同校验，不允许跳过任何键。
    if not _is_project_local_settings_file(settings_path):

        # 普通 settings 直接复用原始对象进入后续强校验。
        return dict_raw

    # dict_runtime_view 仅保留 runtime 真正消费的顶层键。
    dict_runtime_view: dict[str, Any] = {}  # 项目本地覆盖文件的 runtime 顶层视图

    # 逐个复制原始顶层键，同时剥离 local/commands 这类辅助块。
    for settings_key, settings_value in dict_raw.items():

        # helper-only 顶层块供其他本地治理工具使用，不进入 runtime settings 合同。
        if settings_key in LOCAL_SETTINGS_HELPER_KEYS:

            # 跳过 local/commands，避免把本地辅助块误当成 runtime 配置键。
            continue

        # 保留当前 runtime 合同相关的顶层键和值。
        dict_runtime_view[settings_key] = settings_value  # 当前 helper-filter 之后仍需参与 runtime 合同的顶层键值

    # 返回剥离 helper-only 顶层块后的 runtime 视图。
    return dict_runtime_view

# settings 加载与本地覆盖合并入口。
def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    """
    读取默认 settings，并在存在本地覆盖时合并项目配置。

    :param path: 显式 settings 文件；为 `None` 时使用 skill 内置默认配置。
    :return: 已展开路径模板并附带 `__verilog_settings_meta__` 的配置字典。
    :raises ValueError: 当 settings 顶层键集合或顶层段类型不符合当前合同。
    """

    # 解析调用方指定或默认的 settings 文件路径。
    path_settings = _resolve_settings_path(path)  # 本次加载的主 settings 文件

    # 读取主配置，后续会叠加项目本地覆盖。
    dict_payload = _load_one_settings(path_settings)  # 已展开模板的主配置内容

    # 从 settings 所在目录或当前目录向上寻找项目工作区。
    path_workspace_root = find_workspace_root(path_settings.parent) or find_workspace_root(Path.cwd())  # 配置所属工作区根

    # 本地覆盖文件只有在找到工作区后才可能存在。
    dict_local_settings = None  # 项目本地覆盖配置内容

    # 找到工作区时尝试读取项目级 Verilog 覆盖配置。
    if path_workspace_root is not None:

        # 拼出项目本地覆盖文件候选路径。
        path_candidate = path_workspace_root / LOCAL_VERILOG_SETTINGS_REL  # 本地覆盖配置候选路径

        # 防止主配置文件本身就是本地覆盖文件时重复合并。
        if path_candidate.exists() and path_candidate.resolve() != path_settings.resolve():

            # 读取本地覆盖并保持与主配置相同的模板展开语义。
            dict_local_settings = _load_one_settings(path_candidate)  # 项目本地覆盖配置

            # 本地配置优先级高于默认配置，但保留未覆盖字段。
            dict_payload = _deep_merge(dict_payload, dict_local_settings)  # 合并后的有效配置

    # 缺省版本号保证旧配置仍能通过版本字段检查。
    dict_payload.setdefault("version", 1)

    # settings 元数据供诊断、远程同步和 smoke 报告复用。
    dict_payload["__verilog_settings_meta__"] = {
        "settings_path": str(path_settings),  # 主 settings 文件路径
        "workspace_root": str(path_workspace_root) if path_workspace_root is not None else None,  # 工作区根路径
        "local_settings_path": (  # 本地覆盖文件路径
            str((path_workspace_root / LOCAL_VERILOG_SETTINGS_REL).resolve())  # 工作区内本地覆盖文件绝对路径
            if path_workspace_root is not None  # 找到工作区时才提供覆盖路径
            else None  # 未找到工作区时不暴露本地覆盖路径
        ),
        "local_selection_path": (  # 本地远程选择路径
            str((path_workspace_root / LOCAL_REMOTE_SELECTION_REL).resolve())  # 工作区内远程选择文件绝对路径
            if path_workspace_root is not None  # 找到工作区时才提供远程选择路径
            else None  # 未找到工作区时不暴露远程选择路径
        ),
        "server_list_path": (  # 本地服务器列表路径
            str((path_workspace_root / LOCAL_SERVER_LIST_REL).resolve())  # 工作区内服务器列表绝对路径
            if path_workspace_root is not None  # 找到工作区时才提供服务器列表路径
            else None  # 未找到工作区时不暴露服务器列表路径
        ),
        "remote_runtime_config": REMOTE_RUNTIME_SETTINGS_REL,  # 同步到远程工作目录的配置文件名
        "legacy_remote_state": [  # 仍存在的旧版远程状态文件清单
            str(path_legacy_state.resolve())  # 已存在的旧版状态文件路径
            for path_legacy_state in _legacy_paths_for_root(path_workspace_root)  # 遍历当前工作区的旧版状态候选
            if path_legacy_state.exists()  # 只报告磁盘上实际存在的历史状态文件
        ],  # 当前仍能发现的旧版远程状态文件
        "local_settings_loaded": dict_local_settings is not None,  # 是否应用了项目本地覆盖
    }

    # 返回合并本地覆盖和诊断元数据后的完整配置对象。
    return dict_payload

# workflow 默认值读取入口。
def workflow_defaults(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    返回 workflow 默认配置。

    :param settings: 可选的已加载 settings；为空时重新加载默认 settings。
    :return: workflow 配置的深拷贝，避免调用方修改原始 settings。
    :raises ValueError: 当 `settings.workflow` 不是 JSON object 时抛出。
    """

    # 允许调用方传入已加载配置，减少重复读取文件。
    dict_payload = settings or load_settings()  # workflow 来源配置

    # 提取 workflow 段并确认其结构。
    dict_workflow = dict_payload.get("workflow", {})  # workflow 默认配置段

    # workflow 必须保持 JSON object，便于调用方按键读取。
    if not isinstance(dict_workflow, dict):

        # 阻止错误类型继续流入工作流入口。
        raise ValueError("> ERR: [Python] settings.workflow must be an object.")

    # 深拷贝防止调用方修改全局配置对象。
    return deepcopy(dict_workflow)

# smoke 运行目录命名入口。
def build_smoke_run_path(
    path_reports_root: Path,
    *,
    datetime_current: datetime | None = None,
    int_process_id: int | None = None,
) -> Path:
    """
    构造一个位于 reports 根目录下的唯一 smoke 运行目录路径。

    :param path_reports_root: smoke 运行目录所属的 reports 根目录。
    :param datetime_current: 用于测试注入的当前本地时间，默认读取系统时间。
    :param int_process_id: 用于测试注入的进程号，默认读取当前进程号。
    :return: 尚未创建的 `smoke_runs_<时间戳>_<进程号>` 路径。
    """

    # 微秒时间戳避免同一进程短时间内连续运行时复用旧目录。
    datetime_run: datetime = datetime_current or datetime.now()  # 当前 smoke 运行使用的本地时间

    # 进程号补充并发隔离，避免多个验证进程在同一微秒写入相同目录。
    int_run_process_id = int_process_id if int_process_id is not None else os.getpid()  # 当前 smoke 运行进程号

    # 固定目录名格式，供本地、远程与 retained 结果发现逻辑共同识别。
    str_run_name = (
        f"smoke_runs_{datetime_run.strftime('%Y%m%d-%H%M%S-%f')}"
        f"_{int_run_process_id}"
    )  # 当前 smoke 运行目录名

    # 只负责构造路径，目录创建由调用方在明确的运行边界内完成。
    return path_reports_root / str_run_name

# settings.paths 单项读取入口。
def path_setting(settings: dict[str, Any], key: str) -> Path:
    """
    从 `settings.paths` 读取一个路径配置。

    :param settings: 已加载的 settings 字典。
    :param key: `paths` 段中的路径键名。
    :return: 对应路径转换得到的 `Path`。
    :raises KeyError: 当 `settings.paths.<key>` 不存在时抛出。
    """

    # 提取路径配置段，保持旧配置缺失时的 KeyError 语义。
    dict_paths = settings.get("paths", {})  # 工具脚本路径键值映射

    # 缺失路径配置时向调用方报告具体键名。
    if not isinstance(dict_paths, dict) or key not in dict_paths:

        # 错误消息保留原路径键，便于测试和用户提示定位。
        raise KeyError(f"> ERR: [Python] Missing settings.paths.{key}")

    # 将 JSON 中的路径值统一交给 pathlib 处理。
    return Path(str(dict_paths[key]))

# settings.policy 策略读取入口。
def policy_setting(settings: dict[str, Any], key: str, default: Any = None) -> Any:
    """
    从 `settings.policy` 读取一个策略值。

    :param settings: 已加载的 settings 字典。
    :param key: `policy` 段中的策略键名。
    :param default: policy 段不存在或键缺失时返回的默认值。
    :return: 策略值或调用方提供的默认值。
    """

    # 提取 policy 段，非 object 时按缺省策略处理。
    dict_policy = settings.get("policy", {})  # 生成与验证策略键值映射

    # policy 段类型不合法时返回调用方指定的默认值。
    if not isinstance(dict_policy, dict):

        # 调用方负责决定缺省策略的业务含义。
        return default

    # 返回策略键值，缺失时保持原来的 default 行为。
    return dict_policy.get(key, default)

# settings.remote 兼容读取入口。
def remote_setting(settings: dict[str, Any], key: str) -> str:
    """
    读取远程验证相关配置。

    :param settings: 已加载的 settings 字典。
    :param key: 远程配置键名，例如 `helper`、`settings` 或 `server_list`。
    :return: 字符串形式的远程配置值。
    :raises KeyError: 当目标远程配置缺失或为空时抛出。
    """

    # 依赖适配记录优先于静态 defaults，用于迁移后的 helper/settings 路径。
    dict_adapted_remote = _adapted_remote_settings(settings)  # 依赖适配后的远程配置

    # 适配配置命中时直接返回，避免旧 defaults 覆盖已确认的新路径。
    if key in dict_adapted_remote:

        # 返回适配记录中的远程配置值。
        return str(dict_adapted_remote[key])

    # 读取静态 remote 配置段。
    dict_remote = settings.get("remote", {})  # 远程验证运行参数映射

    # remote 段必须是 JSON object 才能继续读取键值。
    if not isinstance(dict_remote, dict):

        # 保留 KeyError 语义，便于调用方统一处理缺失配置。
        raise KeyError(f"> ERR: [Python] Missing settings.remote.{key}")

    # integration 子段承载 remote-ssh helper 的新式配置。
    dict_integration = dict_remote.get("integration", {})  # remote-ssh 集成参数映射

    # 非 object 的 integration 视为空配置。
    if not isinstance(dict_integration, dict):

        # 回退为空字典，让下面的映射继续使用 remote 顶层键。
        dict_integration = {}  # 无效 integration 视为空映射

    # 统一新旧 remote 配置键，保持兼容 defaults 的读取优先级。
    dict_mapping: dict[str, Any] = {
        "helper": dict_integration.get("remote_ssh_helper", dict_remote.get("helper")),  # SSH helper 本地路径
        "settings": dict_integration.get("remote_ssh_settings", dict_remote.get("settings")),  # SSH 参数文件本地路径
        "selection_path": dict_integration.get(  # 已确认远程选择文件优先级
            "selection_file",  # remote-ssh 集成使用的新选择文件键
            dict_remote.get("selection_path", LOCAL_REMOTE_SELECTION_REL),  # 旧式选择文件键或默认路径
        ),  # 已确认远程选择文件
        "server_list": dict_integration.get(  # 服务器列表文件优先级
            "server_list",  # remote-ssh 集成和旧式 remote 共用的服务器列表键
            dict_remote.get("server_list", LOCAL_SERVER_LIST_REL),  # 旧式服务器列表键或默认路径
        ),  # 私有服务器列表文件
        "remote_runtime_config": dict_integration.get(  # 远程运行时配置文件优先级
            "remote_runtime_config",  # remote-ssh 集成使用的运行时配置键
            dict_remote.get("remote_runtime_config", REMOTE_RUNTIME_SETTINGS_REL),  # 旧式运行时配置键或默认路径
        ),  # 远程工作区配置相对路径
        "python": dict_remote.get("python"),  # 远程 Python 命令
        "remote_root": dict_remote.get("remote_root"),  # 远程验证根目录
        "timeout_s": dict_remote.get("timeout_s"),  # 远程命令超时时间
    }  # 新旧 remote 键的兼容映射

    # 缺失或空字符串都视为不可用配置。
    if key not in dict_mapping or dict_mapping[key] in (None, ""):

        # 错误消息保留 remote 键名，方便上层提示缺失配置。
        raise KeyError(f"> ERR: [Python] Missing settings.remote.{key}")

    # 提取命中的配置值，后续按路径类或普通值分别处理。
    selected_value = dict_mapping[key]  # 命中的 remote 配置值

    # 本地路径类配置需要解析到当前工作区，避免相对路径受 cwd 影响。
    if key in {"helper", "settings", "selection_path", "server_list"}:

        # 返回解析后的本地路径字符串。
        return str(_resolve_project_local_path(selected_value, purpose=f"settings.remote.{key}"))

    # 普通远程参数保持字符串化返回。
    return str(selected_value)

# skill 依赖治理配置读取入口。
def skill_dependency_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    返回并校验 skill 依赖治理配置。

    :param settings: 可选的已加载 settings；为空时重新加载默认 settings。
    :return: 规范化后的 `skill_dependencies` 配置副本。
    :raises ValueError: 当依赖配置结构、路径或策略不满足约束时抛出。
    """

    # 使用调用方配置或重新加载默认配置。
    dict_payload = settings or load_settings()  # 依赖配置来源 settings

    # 读取缺失 skill 时用于提示安装来源的治理配置。
    dict_dependencies = dict_payload.get("skill_dependencies")  # 缺失依赖时用于生成安装提示的治理映射

    # 依赖治理配置必须是 object。
    if not isinstance(dict_dependencies, dict):

        # 阻止缺失依赖配置时继续执行安装治理。
        raise ValueError("> ERR: [Python] settings.skill_dependencies must be an object.")

    # 深拷贝后规范化，避免污染调用方传入的 settings。
    dict_result = deepcopy(dict_dependencies)  # 待返回的依赖配置副本

    # state_path 用于记录依赖适配结果。
    state_path_value = dict_result.get("state_path")  # 依赖状态文件原始配置值

    # state_path 必须是非空路径。
    if not isinstance(state_path_value, (str, Path)) or not str(state_path_value):

        # 缺少状态文件会导致远程 helper 适配结果无法持久化。
        raise ValueError("> ERR: [Python] settings.skill_dependencies.state_path must be a non-empty path.")

    # 将依赖状态路径解析到项目本地。
    dict_result["state_path"] = _resolve_project_local_path(  # 依赖适配状态绝对路径
        state_path_value,  # settings 中声明的依赖状态文件路径
        purpose="settings.skill_dependencies.state_path",  # 工作区根查找用途
    )

    # manual_fallback 允许 fpga-agent-skills 等依赖改由人工处理。
    list_manual_fallback = dict_result.get("manual_fallback", [])  # 手动 fallback 依赖列表

    # 配置为 null 时按空列表兼容旧 defaults。
    if list_manual_fallback is None:

        # 空 fallback 表示没有额外人工安装项。
        list_manual_fallback = []  # 无手动兜底依赖时使用空列表

    # manual_fallback 必须是列表，便于后续逐项校验。
    if not isinstance(list_manual_fallback, list):

        # 非列表结构会破坏依赖项顺序和校验逻辑。
        raise ValueError(
            "> ERR: [Python] settings.skill_dependencies.manual_fallback must be a list when present."
        )

    # required 中的 fpga-agent-skills 迁移到 manual_fallback，保持老配置兼容。
    list_normalized_required = []  # 自动安装 required 依赖列表

    # 遍历 required 依赖，将需要人工处理的项拆出。
    for dependency_item in dict_result.get("required", []):

        # fpga-agent-skills 由用户选择路径，不走自动安装。
        if isinstance(dependency_item, dict) and dependency_item.get("id") == "fpga-agent-skills":

            # 保留该依赖在 fallback 列表中，供提示和审计使用。
            list_manual_fallback.append(dependency_item)

            # 当前依赖已转入 fallback，不再加入 required。
            continue

        # 普通 required 依赖继续走自动安装治理。
        list_normalized_required.append(dependency_item)

    # 写回规范化后的 required 依赖列表。
    dict_result["required"] = list_normalized_required  # 已剔除人工 fallback 项的 required 列表

    # 写回包含迁移项的 manual_fallback 列表。
    dict_result["manual_fallback"] = list_manual_fallback  # 人工处理依赖列表

    # required 与 recommended 都必须是非空列表。
    for list_name in ("required", "recommended"):

        # 提取当前依赖列表。
        list_items = dict_result.get(list_name)  # 当前待校验依赖列表

        # 依赖列表必须存在且非空。
        if not isinstance(list_items, list) or not list_items:

            # 缺少依赖列表会让安装提示失去依据。
            raise ValueError(f"> ERR: [Python] settings.skill_dependencies.{list_name} must be a non-empty list.")

        # 逐项校验依赖条目结构。
        for dependency_item in list_items:

            # 校验 id、url、skills 与安装规格。
            _validate_dependency_item(dependency_item, list_name)

    # manual_fallback 同样需要保持依赖条目结构完整。
    for dependency_item in dict_result["manual_fallback"]:

        # fallback 依赖不自动安装，但仍要能清楚展示来源和技能名。
        _validate_dependency_item(dependency_item, "manual_fallback")

    # 安装策略固定为每个缺失依赖都询问用户。
    if dict_result.get("install_policy") != "ask_each_missing":

        # 避免静默安装或跳过缺失依赖。
        raise ValueError("> ERR: [Python] settings.skill_dependencies.install_policy must be ask_each_missing.")

    # 适配策略必须显式要求执行，确保 helper 路径可追踪。
    if dict_result.get("adaptation_policy") != "required":

        # 缺少适配会使远程 helper/settings 路径不可靠。
        raise ValueError("> ERR: [Python] settings.skill_dependencies.adaptation_policy must be required.")

    # 返回规范化后的依赖治理配置。
    return dict_result

# WaveDrom npm 依赖通过此 facade 读取并校验。
def tool_dependency_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    返回并校验外部工具依赖配置。

    :param settings: 可选的已加载 settings；为空时重新加载默认 settings。
    :return: 规范化后的 ``tool_dependencies`` 配置副本。
    :raises ValueError: 当工具依赖缺失或 WaveDrom 合同漂移时抛出。
    """

    # 使用调用方 settings 或默认 settings 作为工具配置来源。
    dict_payload = settings or load_settings()  # 工具依赖配置来源

    # 工具依赖段必须是对象并包含 required 列表。
    dict_tools = dict_payload.get("tool_dependencies")  # 外部工具依赖对象

    # 缺少对象时立即阻断配置读取。
    if not isinstance(dict_tools, dict):

        # 错误信息明确指出 defaults.json 的结构位置。
        raise ValueError("> ERR: [Python] settings.tool_dependencies must be an object.")

    # required 列表提供 WaveDrom 固定版本的唯一来源。
    list_required = dict_tools.get("required")  # required 工具配置列表

    # 空列表和错误类型都不允许绕过工具依赖检查。
    if not isinstance(list_required, list) or not list_required:

        # 工具依赖缺失时阻断后续安装和检查。
        raise ValueError("> ERR: [Python] settings.tool_dependencies.required must be a non-empty list.")

    # 深拷贝避免调用方在运行中修改版本锁定。
    dict_result = deepcopy(dict_tools)  # 返回前隔离工具配置副本

    # 逐项验证 required 工具的固定字段。
    for int_index, dict_item in enumerate(list_required):

        # 每个工具条目必须是对象，才能读取字段合同。
        if not isinstance(dict_item, dict):

            # 错误消息携带条目索引，便于定位配置。
            raise ValueError(f"> ERR: [Python] tool_dependencies.required[{int_index}] must be an object.")

        # 固定字段必须存在且为非空字符串。
        for str_key in ("id", "package_manager", "package", "version", "executable", "minimum_node_version"):

            # 当前字段为空时阻断安装器参数生成。
            if not isinstance(dict_item.get(str_key), str) or not dict_item[str_key].strip():

                # 错误消息携带字段名，避免静默回退默认值。
                raise ValueError(f"> ERR: [Python] tool_dependencies.required[{int_index}].{str_key} is required.")

        # WaveDrom 条目必须保持 npm、包名、版本和可执行入口固定。
        if dict_item["id"] == "wavedrom" and (
            dict_item["package_manager"] != "npm"  # npm 是唯一支持的安装器
            or dict_item["package"] != "wavedrom"  # 包名必须保持官方名称
            or dict_item["version"] != "3.6.1"  # 版本必须锁定为当前合同
            or dict_item["executable"] != "wavedrom"  # CLI 入口必须可发现
        ):

            # 合同漂移时阻断，避免生成无法复现的 SVG。
            raise ValueError("> ERR: [Python] wavedrom tool dependency must remain npm wavedrom@3.6.1.")

    # 返回经字段校验的外部工具配置。
    return dict_result

# FPGA developer 路由配置读取入口。
def fpga_developer_routing_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    返回并校验 FPGA developer skill 路由配置。

    :param settings: 可选的已加载 settings；为空时重新加载默认 settings。
    :return: 规范化后的 `fpga_developer_routing` 配置副本。
    :raises ValueError: 当路由配置结构或策略不满足约束时抛出。
    """

    # 使用传入 settings 或重新加载默认配置作为路由来源。
    dict_payload = settings or load_settings()  # 路由配置来源 settings

    # 提取 FPGA developer 路由配置段。
    dict_routing = dict_payload.get("fpga_developer_routing")  # FPGA developer 路由配置段

    # 路由配置必须是 object。
    if not isinstance(dict_routing, dict):

        # 缺失路由配置时无法决定 FPGA 工作流技能选择策略。
        raise ValueError("> ERR: [Python] settings.fpga_developer_routing must be an object.")

    # 深拷贝后解析 state_path，避免修改传入 settings。
    dict_result = deepcopy(dict_routing)  # 待返回的路由配置副本

    # state_path 记录用户确认过的 developer 选择。
    state_path_value = dict_result.get("state_path")  # 路由状态文件原始配置值

    # 路由状态文件路径必须可解析为非空字符串或 Path。
    if not isinstance(state_path_value, (str, Path)) or not str(state_path_value):

        # 缺少状态路径会导致用户路由选择无法持久化。
        raise ValueError("> ERR: [Python] settings.fpga_developer_routing.state_path must be a non-empty path.")

    # 将路由状态文件解析到当前项目本地。
    dict_result["state_path"] = _resolve_project_local_path(  # developer 选择状态绝对路径
        state_path_value,  # settings 中声明的 developer 路由状态文件路径
        purpose="settings.fpga_developer_routing.state_path",  # 定位 developer 选择文件时的用途说明
    )

    # 首次 FPGA 工作流时询问用户，避免提前绑定特定 vendor。
    if dict_result.get("selection_policy") != "ask_on_first_fpga_workflow":

        # 路由策略变化会影响用户选择时机。
        raise ValueError(
            "> ERR: [Python] settings.fpga_developer_routing.selection_policy must be ask_on_first_fpga_workflow."
        )

    # 提取持久化开关，避免布尔字面量比较写法污染可读性门禁。
    bool_persist_selection = dict_result.get("persist_selection")  # 是否持久化用户路由选择

    # 用户选择必须持久化，便于后续 workflow 复用。
    if bool_persist_selection not in (True,):

        # 不持久化会导致每轮重复询问 developer skill。
        raise ValueError("> ERR: [Python] settings.fpga_developer_routing.persist_selection must be true.")

    # 提取 fpga-agent 强制开关，保持严格 False 语义。
    bool_agent_required = dict_result.get("fpga_agent_required_when_developer_present")  # developer 存在时是否强制 fpga-agent

    # 当前策略允许 developer skill 存在时不强制 fpga-agent。
    if bool_agent_required not in (False,):

        # 保持 v0.3.0 路由兼容语义。
        raise ValueError(
            "> ERR: [Python] settings.fpga_developer_routing."
            "fpga_agent_required_when_developer_present must be false."
        )

    # vendors 段描述可选 FPGA vendor 与对应技能集合。
    dict_vendors = dict_result.get("vendors")  # vendor 到技能集合的映射

    # vendors 必须是非空 object。
    if not isinstance(dict_vendors, dict) or not dict_vendors:

        # 没有 vendor 列表时无法向用户呈现路由选项。
        raise ValueError("> ERR: [Python] settings.fpga_developer_routing.vendors must be a non-empty object.")

    # 逐个 vendor 校验标签和技能列表。
    for vendor_id, dict_vendor in dict_vendors.items():

        # vendor id 作为配置键必须是非空字符串。
        if not isinstance(vendor_id, str) or not vendor_id:

            # 空 vendor id 无法在状态文件中稳定记录选择。
            raise ValueError("> ERR: [Python] settings.fpga_developer_routing vendor ids must be non-empty strings.")

        # 每个 vendor 配置必须是 object。
        if not isinstance(dict_vendor, dict):

            # 非 object 结构无法承载 label 和 skills。
            raise ValueError(f"> ERR: [Python] settings.fpga_developer_routing.vendors.{vendor_id} must be an object.")

        # label 是呈现给用户看的 vendor 名称。
        str_label = dict_vendor.get("label")  # vendor 显示名称

        # label 必须是非空字符串。
        if not isinstance(str_label, str) or not str_label:

            # 空 label 会让交互选项不可读。
            raise ValueError(
                f"> ERR: [Python] settings.fpga_developer_routing.vendors.{vendor_id}.label must be non-empty."
            )

        # skills 是该 vendor 对应的候选技能名列表。
        list_skills = dict_vendor.get("skills")  # vendor 对应技能名列表

        # skills 必须由非空字符串构成。
        if not isinstance(list_skills, list) or not list_skills or not all(
            isinstance(skill_name, str) and skill_name for skill_name in list_skills
        ):

            # 空技能列表会让路由结果无法执行。
            raise ValueError(
                f"> ERR: [Python] settings.fpga_developer_routing.vendors.{vendor_id}."
                "skills must be non-empty strings."
            )

    # 返回规范化后的路由配置。
    return dict_result

# skill 依赖条目基础结构校验。
def _validate_dependency_item(item: Any, list_name: str) -> None:
    """
    校验一个 skill 依赖条目的基础结构。

    :param item: 待校验的依赖条目。
    :param list_name: 条目所属列表名，用于错误消息定位。
    :return: 校验通过时不返回业务值。
    :raises ValueError: 当条目缺少必需字段或字段类型不合法时抛出。
    """

    # 依赖条目必须是 object，才能承载 id、url 和 skills。
    if not isinstance(item, dict):

        # 保留列表名，方便用户定位坏配置。
        raise ValueError(f"> ERR: [Python] settings.skill_dependencies.{list_name} entries must be objects.")

    # 每个依赖条目都必须声明 id、url 与 skills。
    for required_key in ("id", "url", "skills"):

        # 缺少任一字段都会让安装提示不完整。
        if required_key not in item:

            # 报告缺失字段名，便于直接修配置。
            raise ValueError(f"> ERR: [Python] settings.skill_dependencies.{list_name} entry missing {required_key}.")

    # 依赖 id 用于日志、状态和用户提示。
    if not isinstance(item["id"], str) or not item["id"]:

        # 空 id 无法作为稳定依赖标识。
        raise ValueError(f"> ERR: [Python] settings.skill_dependencies.{list_name} id must be non-empty.")

    # 依赖来源限制为 GitHub HTTPS URL。
    if not isinstance(item["url"], str) or not item["url"].startswith("https://github.com/"):

        # 非 GitHub HTTPS URL 不进入当前自动安装治理。
        raise ValueError(
            f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
            "url must be a GitHub HTTPS URL."
        )

    # skills 列表说明该依赖提供哪些可用 skill。
    if not isinstance(item["skills"], list) or not item["skills"] or not all(
        isinstance(skill_name, str) and skill_name for skill_name in item["skills"]
    ):

        # 空技能列表无法验证依赖是否满足当前工作流。
        raise ValueError(
            f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
            "skills must be non-empty strings."
        )

    # install_specs 描述实际安装源路径和目标名称。
    _validate_install_specs(item, list_name)

    # alternative_skill_sets 描述可替代的技能组合。
    list_alternatives = item.get("alternative_skill_sets", [])  # 可替代技能组合列表

    # 非空 alternatives 必须是二维非空字符串列表。
    # 将 alternatives 结构校验拆成命名条件，避免长条件块难读。
    bool_alternatives_valid = isinstance(list_alternatives, list) and all(  # alternatives 整体结构是否可用于依赖满足性判断
        isinstance(skill_group, list)  # 每组替代技能必须以列表表达
        and skill_group  # 每组至少提供一个候选技能
        and all(isinstance(skill_name, str) and skill_name for skill_name in skill_group)  # 技能名必须是非空字符串
        for skill_group in list_alternatives  # 逐组校验替代技能集合
    )  # 可替代技能组合结构是否有效

    # 声明了替代组合时才检查二维非空字符串列表结构。
    if list_alternatives and not bool_alternatives_valid:

        # alternatives 结构错误会误导依赖满足性判断。
        raise ValueError(
            f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
            "alternative_skill_sets must contain non-empty string lists."
        )

# 依赖安装规格校验。
def _validate_install_specs(item: dict[str, Any], list_name: str) -> None:
    """
    校验依赖条目的安装规格列表。

    :param item: 已通过基础字段校验的依赖条目。
    :param list_name: 条目所属列表名，用于错误消息定位。
    :return: 校验通过时不返回业务值。
    :raises ValueError: 当安装规格缺失或字段类型不合法时抛出。
    """

    # install_specs 描述从依赖仓库复制哪些 skill。
    list_specs = item.get("install_specs")  # 安装规格列表

    # 安装规格必须是非空列表。
    if not isinstance(list_specs, list) or not list_specs:

        # 没有安装规格时无法执行依赖安装。
        raise ValueError(
            f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
            "install_specs must be a non-empty list."
        )

    # 逐个校验安装规格对象。
    for dict_spec in list_specs:

        # 每条安装规格必须是 object。
        if not isinstance(dict_spec, dict):

            # 非 object 规格无法承载 skill 与 source_path。
            raise ValueError(
                f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
                "install_specs entries must be objects."
            )

        # skill 字段表示源 skill 名。
        if not isinstance(dict_spec.get("skill"), str) or not dict_spec["skill"]:

            # 空 skill 名无法定位依赖仓库内的源目录。
            raise ValueError(
                f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
                "install_specs.skill must be non-empty."
            )

        # source_path 表示依赖仓库中的相对路径。
        if not isinstance(dict_spec.get("source_path"), str) or not dict_spec["source_path"]:

            # 空 source_path 无法执行复制。
            raise ValueError(
                f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
                "install_specs.source_path must be non-empty."
            )

        # dest_name 可选，用于覆盖安装目标名。
        str_dest_name = dict_spec.get("dest_name")  # 可选安装目标名称

        # dest_name 存在时必须是非空字符串。
        if str_dest_name is not None and (not isinstance(str_dest_name, str) or not str_dest_name):

            # 空目标名会生成不可安装的 skill 目录。
            raise ValueError(
                f"> ERR: [Python] settings.skill_dependencies.{list_name}.{item['id']} "
                "install_specs.dest_name must be non-empty when present."
            )

# 依赖适配后的远程配置读取。
def _adapted_remote_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """
    读取依赖适配状态中的远程 helper/settings 覆盖。

    :param settings: 已加载 settings 字典。
    :return: 通过校验的远程适配配置；无可用适配时返回空字典。
    """

    # 依赖配置不可用时不阻断普通 remote settings 读取。
    try:

        # 解析依赖治理配置以获取适配状态路径。
        dict_dependency_settings = skill_dependency_settings(settings)  # 依赖治理配置

    # 依赖治理缺失或无效时退回静态 remote 配置。
    except (ValueError, KeyError):

        # 无可用适配状态时返回空覆盖。
        return {}

    # 适配状态文件记录依赖安装后可用的 helper/settings 路径。
    path_state = dict_dependency_settings["state_path"]  # 依赖适配状态路径

    # 状态文件必须存在才可能提供覆盖值。
    if not isinstance(path_state, Path) or not path_state.exists():

        # 没有适配状态时继续使用 defaults 中的 remote 设置。
        return {}

    # 状态文件可能缺失或损坏，读取失败时退回静态配置。
    try:

        # 读取依赖适配状态 JSON。
        dict_state_data = json.loads(path_state.read_text(encoding="utf-8"))  # 依赖适配状态内容

    # 状态文件不可读或 JSON 损坏时继续使用静态配置。
    except (OSError, json.JSONDecodeError):

        # 损坏状态不能覆盖 defaults。
        return {}

    # adaptations.remote 承载远程 helper/settings 覆盖。
    dict_adaptations = dict_state_data.get("adaptations", {}) if isinstance(dict_state_data, dict) else {}  # 适配总表

    # remote 适配段只在 adaptations 是 object 时读取。
    dict_remote = dict_adaptations.get("remote", {}) if isinstance(dict_adaptations, dict) else {}  # 远程适配配置段

    # remote 适配段必须是 object。
    if not isinstance(dict_remote, dict):

        # 非 object 适配记录不可信。
        return {}

    # 收集通过路径存在性校验的适配项。
    dict_normalized: dict[str, Any] = {}  # 可用远程适配配置

    # helper 是远程 SSH 辅助脚本路径。
    str_helper = dict_remote.get("helper")  # 适配后的 helper 路径

    # settings 项指向 remote-ssh 使用的参数文件。
    str_settings_path = dict_remote.get("settings")  # 适配记录中的 SSH 参数文件

    # helper 必须存在且是文件。
    if isinstance(str_helper, str) and _adapted_remote_path_valid("helper", str_helper):

        # 记录可用 helper 覆盖。
        dict_normalized["helper"] = str_helper  # 可用 SSH helper 覆盖路径

    # 参数文件同样要通过本地文件存在性检查。
    if isinstance(str_settings_path, str) and _adapted_remote_path_valid("settings", str_settings_path):

        # 写入 remote_setting 会优先读取的 settings 覆盖。
        dict_normalized["settings"] = str_settings_path  # 已确认存在的 SSH 参数文件路径

    # 返回通过校验的适配覆盖。
    return dict_normalized

# 项目本地路径解析工具。
def _resolve_project_local_path(value: str | Path, *, purpose: str) -> Path:
    """
    将项目本地路径配置解析为绝对路径。

    :param value: 绝对路径、用户路径或相对工作区路径。
    :param purpose: 查找工作区根时用于错误提示的用途说明。
    :return: 解析后的绝对路径。
    """

    # 先展开用户目录，保留绝对路径原样解析。
    path_value = Path(value).expanduser()  # 待解析的路径值

    # 绝对路径不依赖当前工作区。
    if path_value.is_absolute():

        # 返回规范化后的绝对路径。
        return path_value.resolve()

    # 相对路径固定拼到当前工作区根，避免 cwd 漂移。
    return (require_workspace_root(purpose=purpose) / path_value).resolve()

# 远程适配路径存在性检查。
def _adapted_remote_path_valid(key: str, value: str) -> bool:
    """
    检查远程适配路径是否满足对应键的存在性要求。

    :param key: 适配配置键。
    :param value: 适配配置中的路径字符串。
    :return: 路径满足该键要求时为 `True`。
    """

    # helper 与 settings 必须是实际文件。
    if key in {"helper", "settings"}:

        # 文件存在性用于避免损坏适配记录覆盖 defaults。
        return Path(value).expanduser().is_file()

    # server_list 只要求路径存在，兼容目录或文件形式。
    if key == "server_list":

        # 服务器列表路径存在即可交给上层读取。
        return Path(value).expanduser().exists()

    # 其他键没有额外本地路径校验。
    return True

# settings 文件路径解析工具。
def _resolve_settings_path(path: str | Path | None) -> Path:
    """
    解析 settings 文件路径。

    :param path: 调用方提供的 settings 路径；为空时使用默认配置。
    :return: 展开用户目录并规范化后的 settings 绝对路径。
    """

    # 为空时使用 skill 内置 defaults。
    path_settings = Path(path) if path is not None else DEFAULT_SETTINGS_PATH  # 待解析 settings 路径

    # 支持用户目录写法。
    path_settings = path_settings.expanduser()  # 展开后的 settings 路径

    # 相对 settings 路径按当前工作目录解析，保持 CLI 兼容行为。
    if not path_settings.is_absolute():

        # 将相对路径转为绝对路径。
        path_settings = (Path.cwd() / path_settings).resolve()  # 相对路径转绝对路径

    # 返回最终 settings 文件路径。
    return path_settings

# 单个 settings 文件读取工具。
def _load_one_settings(settings_path: Path) -> dict[str, Any]:
    """
    读取并展开一个 settings JSON 文件。

    :param settings_path: 待读取的 settings JSON 文件路径。
    :return: 已展开模板变量的 settings 字典。
    :raises ValueError: 当文件内容不是 JSON object，或顶层键集合/浅层类型不符合当前合同时抛出。
    """

    # 读取 settings 文件内容。
    dict_raw = json.loads(settings_path.read_text(encoding="utf-8"))  # settings JSON 原始内容

    # settings 根必须是 JSON object。
    if not isinstance(dict_raw, dict):

        # 保留 settings 路径，方便定位坏文件。
        raise ValueError(f"> ERR: [Python] Settings must be a JSON object: {settings_path}")

    # dict_runtime_view 只保留 runtime settings 合同真正消费的顶层键。
    dict_runtime_view = _runtime_settings_view(dict_raw, settings_path)  # 参与 runtime 合同校验的 settings 视图

    # 顶层强校验只覆盖当前真实 schema 的键集合和浅层类型。
    _validate_top_level_settings_dict(dict_runtime_view, settings_path)

    # 旧配置缺少版本号时补齐默认版本。
    dict_runtime_view.setdefault("version", 1)

    # 模板展开上下文覆盖 skill、项目、settings 目录和用户主目录。
    dict_context = {
        "skill_dir": skill_root(),  # 模板变量 `${skill_dir}` 的替换值
        "project_root": project_root(),  # 当前项目根目录
        "settings_dir": settings_path.parent,  # settings 文件所在目录
        "home": Path.home(),  # 当前用户主目录
    }  # 路径模板展开上下文

    # 返回递归展开后的配置内容。
    return _expand_value(dict_runtime_view, dict_context)

# settings 字典深度合并工具。
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    深度合并两个 settings 字典。

    :param base: 默认配置字典。
    :param override: 项目本地覆盖配置字典。
    :return: 合并后的新字典；嵌套 object 递归合并，其他值整体覆盖。
    """

    # 从默认配置深拷贝起步，避免修改输入对象。
    dict_merged = deepcopy(base)  # 合并结果配置

    # 逐个覆盖键处理本地配置。
    for merge_key, merge_value in override.items():

        # 两边都是 object 时递归合并。
        if merge_key in dict_merged and isinstance(dict_merged[merge_key], dict) and isinstance(merge_value, dict):

            # 保留默认子键，同时应用本地子键覆盖。
            dict_merged[merge_key] = _deep_merge(  # 递归合并后的子配置
                dict_merged[merge_key],  # 默认配置中的同名子配置
                merge_value,  # 本地覆盖中的同名子配置
            )

        # 非 object 或缺失键按本地配置整体替换。
        else:

            # 深拷贝覆盖值，避免调用方共享可变对象。
            dict_merged[merge_key] = deepcopy(merge_value)  # 本地覆盖值副本

    # 返回合并后的配置。
    return dict_merged

# 旧版状态候选路径构造工具。
def _legacy_paths_for_root(root: Path | None) -> list[Path]:
    """
    按工作区根构造旧版远程状态候选路径。

    :param root: 当前工作区根；为 `None` 时无法构造路径。
    :return: 固定顺序的旧版状态候选路径列表。
    """

    # 没有工作区根时无法定位旧版项目状态目录。
    if root is None:

        # 返回空列表，供调用方安全迭代。
        return []

    # 返回旧版远程状态文件候选路径。
    return [
        root / LEGACY_REMOTE_SELECTION_REL,
        root / LEGACY_REMOTE_TOOLCHAIN_REL,
        root / LEGACY_REMOTE_SERVER_LIST_REL,
    ]

# settings 任意值递归展开工具。
def _expand_value(value: Any, context: dict[str, Path]) -> Any:
    """
    递归展开 settings 中的路径模板。

    :param value: 任意 JSON 值。
    :param context: `${skill_dir}` 等模板变量的路径上下文。
    :return: 展开后的 JSON 值。
    """

    # 字典值需要逐键递归展开。
    if isinstance(value, dict):

        # 保留原键名，只展开每个键对应的值。
        return {
            dict_key: _expand_value(dict_item, context)
            for dict_key, dict_item in value.items()
        }

    # 列表值需要逐项递归展开。
    if isinstance(value, list):

        # 保留列表顺序，只展开每个元素。
        return [_expand_value(list_item, context) for list_item in value]

    # 字符串可能包含模板或路径。
    if isinstance(value, str):

        # 交给字符串展开函数处理模板替换和路径规范化。
        return _expand_string(value, context)

    # 其他 JSON 标量保持原值。
    return value

# settings 字符串模板展开工具。
def _expand_string(value: str, context: dict[str, Path]) -> str:
    """
    展开单个 settings 字符串中的模板变量。

    :param value: settings 中的原始字符串。
    :param context: 模板变量到路径的映射。
    :return: 展开后的字符串；路径样式字符串会额外展开用户目录。
    """

    # 内部替换函数只处理受支持的模板变量。
    def replace(match: re.Match[str]) -> str:
        """
        将单个模板匹配替换为环境变量或上下文路径。

        :param match: `${...}` 模板片段的正则匹配对象。
        :return: 替换后的字符串；未知模板保持原样。
        """

        # 提取 `${...}` 中的变量名。
        str_token = match.group(1)  # 模板变量名

        # env: 前缀从环境变量读取，缺失时保持空字符串兼容旧行为。
        if str_token.startswith("env:"):

            # 返回环境变量值或空字符串。
            return os.environ.get(str_token[4:], "")

        # 上下文变量来自 settings 文件路径和 skill 位置。
        if str_token in context:

            # 返回上下文路径字符串。
            return str(context[str_token])

        # 未知模板保持原样，避免破坏用户自定义语法。
        return match.group(0)

    # 执行模板替换。
    str_expanded = _token_re.sub(replace, value)  # 模板替换后的字符串

    # 路径样式字符串继续展开用户目录。
    if _looks_like_path(str_expanded, original=value):

        # 返回规范化后的用户路径字符串。
        return str(Path(str_expanded).expanduser())

    # 非路径字符串保持模板替换结果。
    return str_expanded

# 本地路径字符串判定工具。
def _looks_like_path(value: str, *, original: str | None = None) -> bool:
    """
    判断字符串是否应按路径处理。

    :param value: 模板展开后的字符串。
    :param original: 模板展开前的原始字符串。
    :return: 看起来像本地路径时为 `True`。
    """

    # 原始字符串用于识别模板开头的路径。
    str_raw = original or value  # 原始或当前字符串

    # URL 不应被当作本地路径展开。
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):

        # 保持 URL 原样返回给调用方。
        return False

    # 原始值是 URL 时同样不做路径处理。
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", str_raw):

        # 避免模板前缀误判 URL。
        return False

    # 本地路径模板、环境路径、绝对路径和用户目录都视为路径。
    return (
        re.match(r"^\$\{(?:skill_dir|project_root|settings_dir|home)\}([/\\]|$)", str_raw) is not None
        or (str_raw.startswith("${env:") and _absolute_or_user_path(value))
        or _absolute_or_user_path(str_raw)
        or _absolute_or_user_path(value)
    )

# 绝对路径和用户路径判定工具。
def _absolute_or_user_path(value: str) -> bool:
    """
    判断字符串是否是绝对路径或用户目录路径。

    :param value: 待判断的字符串。
    :return: POSIX 绝对路径、Windows 路径、用户目录或带反斜杠路径时为 `True`。
    """

    # POSIX 绝对路径直接视为本地路径。
    if value.startswith("/"):

        # `/tmp` 等路径需要支持用户目录展开逻辑。
        return True

    # 反斜杠通常意味着 Windows 本地路径。
    if "\\" in value:

        # 相对 Windows 路径也需要按路径处理。
        return True

    # 用户目录写法需要交给 Path.expanduser。
    if value.startswith("~"):

        # `~/...` 或 `~user/...` 都按路径处理。
        return True

    # Windows 盘符路径需要识别为绝对路径。
    return re.match(r"^[A-Za-z]:", value) is not None

