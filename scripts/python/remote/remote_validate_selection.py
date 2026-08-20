"""远端验证的选择、配置和路径解析辅助函数。"""

# 标准库负责 CLI 参数、JSON 配置、时间戳和导入路径修正。
import argparse
import json
import sys
import time

# dataclass 将跨步骤共享的远端上下文和执行配置收束为稳定对象。
from dataclasses import dataclass

# pathlib 负责本地路径，PurePosixPath 负责远端 POSIX 路径校验。
from pathlib import Path, PurePosixPath

# Any 只用于 runtime 配置与 JSON 载荷这类异构字典。
from typing import Any

# skill 主体根目录供脚本直运行时定位 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 包含 runtime、scripts 与 config 的 skill 根目录

@dataclass(frozen=True)
class RemoteHelperContext:
    """保存一次 erie-remote-ssh helper 调用所需的稳定参数。

    :param path_helper: erie-remote-ssh 的本地 helper 脚本路径。
    :param path_remote_settings: erie-remote-ssh 的 settings 文件路径。
    :param path_server_list: 本地私有 server_list.local.json 路径。
    :param str_server: 已由用户显式指定或项目确认的远程服务器标识。
    :param int_timeout: run-request 执行远程请求时使用的秒级超时。
    """

    # helper 脚本必须存在且是普通文件。
    path_helper: Path  # 创建和执行远端 request 的 helper 脚本路径

    # remote settings 由 erie-remote-ssh 解释，不复制到远端。
    path_remote_settings: Path  # helper 读取服务器策略的 settings 文件路径

    # server list 保存本地私有连接信息，禁止进入远端包。
    path_server_list: Path  # 本地私有服务器清单路径

    # server id/name 来自用户确认或命令行覆盖。
    str_server: str  # 本轮远端验证目标服务器

    # timeout 只用于执行已创建的远端请求。
    int_timeout: int  # 远端请求执行超时时间

# 执行配置需要在 upload request 完成后补全 source manifest 和 receipt。
@dataclass
class RemoteValidationRunConfig:
    """保存一次远端 retained run 的上传和执行参数。

    :param path_package_root: 本地 staging 包根目录。
    :param str_remote_parent: 本次远端 retained run 父目录。
    :param str_remote_skill: 远端上传后的 skill 工作区。
    :param str_remote_python: 远端 Python 命令。
    :param bool_cleanup_outputs: 是否清理远端 smoke 输出。
    :param dict_toolchain_selection: 已确认的远端工具链选择。
    :param str_remote_runtime_config_path: 远端 runtime 配置相对路径。
    :param str_run_id: 本次 outer retained run 的唯一标识。
    :param str_source_digest: 上传 staging 内容的 SHA-256 摘要。
    :param str_remote_server: 本次远端验证使用的服务器标识。
    :param path_upload_archive: 历史归档字段；非空时执行层必须拒绝。
    :param str_remote_archive_name: 历史归档字段；非空时执行层必须拒绝。
    :param str_upload_receipt_relative: erie-remote-ssh uploaded_verified receipt 的相对引用。

    上传 request 完成后才知道 canonical source manifest 和 receipt，因此该执行配置允许
    在 request 阶段补全这两个晚期身份；helper context 仍保持不可变。
    """

    # staging 包是 request-upload 的本地源目录。
    path_package_root: Path  # 本地 staging 包根目录

    # retained 父目录承载本次远端运行证据。
    str_remote_parent: str  # 本次远端 retained run 父目录

    # skill 工作区是远端 bash gate 的执行根。
    str_remote_skill: str  # 远端上传后的 skill 工作区

    # 远端 Python 命令来自 skill settings。
    str_remote_python: str  # 远端 Python 命令

    # cleanup_outputs 与 --cleanup-remote 共享用户清理意图。
    bool_cleanup_outputs: bool  # 是否清理远端 smoke 输出

    # 工具链选择来自远端 workdir 中已确认的 runtime 配置。
    dict_toolchain_selection: dict[str, Any]  # 已确认的远端工具链选择

    # runtime 配置路径用于多 Vivado 候选提示和审计。
    str_remote_runtime_config_path: str  # 远端 runtime 配置相对路径

    # outer run 标识把执行输出和后续 report-runs 查询绑定到同一目录。
    str_run_id: str  # 本次 outer retained run 的唯一标识

    # staging 摘要用于阻止其他源码包复用本轮完成证据。
    str_source_digest: str  # 上传 staging 内容的 SHA-256 摘要

    # 服务器标识进入远端环境指纹和测试收据，但不携带连接凭据。
    str_remote_server: str  # 本轮远端验证目标服务器标识

    # 新布局把 reports 固定放在 runs/<run-id>/ 下，避免嵌套 smoke_runs 目录。
    str_remote_reports: str = ""  # 本次 run 的直接报告目录

    # 远端 workspace 项目目录由 validation authority 声明，避免绑定当前仓库名称。
    str_project_directory: str = ""  # 远端 workspace 中的项目相对目录

    # 历史归档字段只保留兼容形状，默认值为空且执行层禁止非空值。
    path_upload_archive: Path | None = None  # 本地 tar.gz 上传源

    # 历史归档目标只保留兼容形状，默认值为空且执行层禁止非空值。
    str_remote_archive_name: str = ""  # 远端 tar.gz 文件名

    # 上传 request 的 verified receipt 用相对引用写入最终机器协议。
    str_upload_receipt_relative: str = ""  # uploaded_verified receipt 相对路径

# _ensure_runtime_import_path 只在需要 runtime helper 时调整导入路径。
def _ensure_runtime_import_path() -> None:
    """确保脚本从仓库任意位置运行时可导入 runtime 包。

    :param: 此函数没有外部业务参数。
    :return: 不返回业务值；执行后当前进程可解析 skill-local runtime 包。
    """

    # 禁止验证脚本生成 pyc，避免污染 installable skill 目录。
    sys.dont_write_bytecode = True  # 当前进程是否写入 Python 字节码缓存

    # sys.path 用字符串比较，避免 Path 对象和字符串混用造成重复插入。
    str_skill_root = str(PATH_SKILL_ROOT)  # runtime 包所在目录的字符串形式

    # 仅在缺少 skill 根目录时补入导入路径，避免重复改变搜索顺序。
    if str_skill_root not in sys.path:

        # 脚本直运行时需要把 skill 根目录放在 import 搜索最前面。
        sys.path.insert(0, str_skill_root)

# load_settings 包装 runtime 配置加载器，避免模块导入阶段触碰 sys.path。
def load_settings(path_settings: Path) -> dict[str, Any]:
    """读取 Verilog skill settings 配置。

    :param path_settings: 本次远端验证使用的 settings JSON 路径。
    :return: 配置加载器解析后的 settings 字典。
    """

    # runtime 包在函数内延迟导入，消除 import-time sys.path 副作用。
    _ensure_runtime_import_path()

    # 配置加载器定义 skill-local settings 的合并与默认值规则。
    from scripts.python.workflow.config import load_settings as func_load_settings

    # 返回 runtime 配置加载器的原始字典结果，保持调用方可见字段不变。
    return func_load_settings(path_settings)

# remote_setting 包装配置读取器，统一处理 remote.integration 兼容字段。
def remote_setting(dict_settings: dict[str, Any], str_key: str) -> str:
    """读取 remote 配置中的指定字符串字段。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param str_key: remote 配置字段名。
    :return: 解析后的远程配置字符串。
    """

    # runtime 包在需要读取配置字段时再进入导入路径。
    _ensure_runtime_import_path()

    # remote_setting 负责兼容旧 remote 字段和 integration 字段。
    from scripts.python.workflow.config import remote_setting as func_remote_setting

    # 返回字符串配置，缺失和类型错误由 runtime helper 统一抛出。
    return func_remote_setting(dict_settings, str_key)

# remote_runtime_settings_relpath 包装 runtime 默认远端配置相对路径。
def remote_runtime_settings_relpath() -> str:
    """返回远端 workdir 中 verilog.remote.json 的默认相对路径。

    :param: 此函数没有外部业务参数。
    :return: 远端 runtime 配置文件的 POSIX 相对路径。
    """

    # 延迟导入避免脚本被测试加载时写入 import 路径。
    _ensure_runtime_import_path()

    # runtime 配置模块保存当前 skill 的远端 runtime 配置路径约定。
    from scripts.python.workflow.config import (
        remote_runtime_settings_relpath as func_remote_runtime_settings_relpath,
    )

    # 返回调用方用于提示用户持久化工具链选择的路径。
    return func_remote_runtime_settings_relpath()

# load_remote_runtime_config 作为公开包装函数供 smoke 测试和 CLI 复用。
def load_remote_runtime_config(path_config: Path) -> dict[str, Any]:
    """读取远端工作目录内的 runtime toolchain 配置副本。

    :param path_config: 本地下载或项目本地保存的 verilog.remote.json 路径。
    :return: 规范化后的 remote runtime 配置字典。
    """

    # remote_selection 模块实现字段校验和默认结构整理。
    _ensure_runtime_import_path()

    # 延迟导入避免 import-time 访问项目本地远程状态。
    from scripts.python.remote.remote_selection import (
        load_remote_runtime_config as func_load_remote_runtime_config,
    )

    # 直接返回 runtime helper 的解析结果，保持旧 API 形状。
    return func_load_remote_runtime_config(path_config)

# resolve_confirmed_remote_server 包装项目本地已确认远程服务器选择。
def resolve_confirmed_remote_server(dict_settings: dict[str, Any]) -> dict[str, Any] | None:
    """解析项目本地已确认的远程服务器选择。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :return: 选择载荷；未确认时返回 None。
    """

    # 远程选择读取仅在真正需要服务器时发生。
    _ensure_runtime_import_path()

    # remote_selection 负责读取 .settings/remote-selection.local.json。
    from scripts.python.remote.remote_selection import (
        resolve_confirmed_remote_server as func_resolve_confirmed_remote_server,
    )

    # 返回 None 表示用户尚未确认远程服务器。
    return func_resolve_confirmed_remote_server(dict_settings)

# require_workspace_root 包装项目根发现逻辑。
def require_workspace_root(*, purpose: str) -> Path:
    """查找当前工作区根目录。

    :param purpose: 调用方说明，用于工作区缺失时的错误文本。
    :return: 已确认的工作区根目录路径。
    """

    # workspace helper 只在解析项目本地配置文件位置时需要。
    _ensure_runtime_import_path()

    # runtime.workspace 承载 AGENTS/workspace marker 的根发现策略。
    from scripts.python.workflow.workspace import require_workspace_root as func_require_workspace_root

    # 返回 Path 对象供调用方继续拼接 .settings 路径。
    return func_require_workspace_root(purpose=purpose)

# resolve_server 读取显式服务器或项目已确认服务器。
def resolve_server(
    dict_settings: dict[str, Any],
    str_arg_server: str | None,
    parser: argparse.ArgumentParser,
) -> str:
    """解析本次远端验证使用的服务器标识。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param str_arg_server: CLI 显式传入的服务器标识。
    :param parser: 当前 CLI parser，用于报告缺少选择。
    :return: 远端服务器 id/name。
    :raises AssertionError: 当 parser.error 异常返回时抛出，标记不可达路径。
    """

    # 命令行显式服务器优先，不写回项目状态。
    if str_arg_server:

        # 返回本次 CLI 覆盖值。
        return str_arg_server

    # 没有 CLI 覆盖时读取项目已确认选择。
    dict_selection = resolve_server_from_selection(dict_settings)  # 项目本地远程选择

    # 已确认选择提供 server_id。
    if dict_selection:

        # server_id 转字符串传给 erie-remote-ssh。
        return str(dict_selection["server_id"])

    # 未确认服务器时要求用户选择，避免默认误连远端。
    parser.error(
        "> ERR: [Python] Remote server is not confirmed. Pass --server after the user selects a target."
    )

    # parser.error 已退出；该 raise 仅服务静态分析。
    raise AssertionError("> ERR: [Python] unreachable missing remote server branch.")

# resolve_server_from_selection 隔离远程选择读取，便于测试替换。
def resolve_server_from_selection(dict_settings: dict[str, Any]) -> dict[str, Any] | None:
    """读取项目本地确认的远程服务器选择。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :return: 选择字典；未确认时返回 None。
    """

    # 直接委托 runtime 选择解析器。
    return resolve_confirmed_remote_server(dict_settings)

# resolve_local_remote_runtime_config 决定本地 verilog.remote.json 副本路径。
def resolve_local_remote_runtime_config(
    dict_settings: dict[str, Any],
    path_arg_config: Path | None = None,
) -> Path:
    """解析项目本地 remote runtime 配置文件路径。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param path_arg_config: CLI 显式传入的工具链配置路径。
    :return: 规范化后的本地 verilog.remote.json 路径。
    """

    # 显式路径用于兼容旧调用方。
    if path_arg_config:

        # 返回展开后的绝对路径，避免后续 cwd 改变含义。
        return path_arg_config.expanduser().resolve()

    # settings meta 可直接提供 workspace root。
    dict_meta = dict_settings.get("__verilog_settings_meta__", {})  # settings 加载器注入的元信息

    # workspace_root 缺失时稍后用 workspace marker 自动发现。
    path_workspace_root: Path | None = None  # 项目工作区根目录候选

    # settings meta 中的 workspace_root 优先于 cwd 自动发现。
    if isinstance(dict_meta, dict) and dict_meta.get("workspace_root"):

        # 将 meta 中的路径文本转为 Path 对象。
        path_workspace_root = Path(str(dict_meta.get("workspace_root")))  # settings meta 指定的项目根目录

    # 没有 meta 根目录时从当前 cwd 向上查找 AGENTS/workspace marker。
    if path_workspace_root is None:

        # workspace helper 会在失败时给出 purpose 说明。
        path_workspace_root = require_workspace_root(purpose="local remote runtime config")  # 自动发现的项目根目录

    # runtime 配置副本固定落在项目本地 .settings 下。
    return (path_workspace_root / remote_runtime_settings_relpath()).resolve()

# resolve_server_list_path 规范化 server list 路径但不做 fallback 猜测。
def resolve_server_list_path(path_configured: Path) -> Path:
    """规范化 erie-remote-ssh server list 路径。

    :param path_configured: settings 中配置的 server list 路径。
    :return: 展开用户目录后的绝对路径。
    """

    # server_list.local.json 是本地私有文件，只在本机解析。
    path_resolved = path_configured.expanduser().resolve()  # 规范化后的 server list 路径

    # 返回路径对象供 prerequisite 检查使用。
    return path_resolved

# selection_from_args 将 CLI 选择转换成持久化字段。
def selection_from_args(namespace_args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, str | bool]:
    """从 CLI 参数构造远端工具链选择。

    :param namespace_args: CLI 参数解析结果。
    :param parser: 当前 CLI parser，用于报告缺少后端或 settings64.sh。
    :return: 包含 simulator_backend、确认标记、时间戳和可选 settings64.sh 的字典。
    """

    # simulator_backend 是写配置模式的必填字段。
    str_backend = namespace_args.simulator_backend  # 用户为远端验证确认的仿真后端

    # 缺少后端时无法写出有效 runtime 配置。
    if not str_backend:

        # parser.error 使用退出码 2 报告用户输入错误。
        parser.error("> ERR: [Python] --write-toolchain-selection requires --simulator-backend.")

    # 记录用户确认时间，便于审计工具链选择来源。
    dict_selection: dict[str, str | bool] = {  # 工具链选择载荷
        "simulator_backend": str(str_backend),  # 已确认仿真后端
        "confirmed_by_user": True,  # 该配置来自用户明确选择
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),  # UTC 更新时间
    }

    # xsim 必须指定 Vivado settings64.sh，否则远端多版本时不可判定。
    if str_backend == "xsim":

        # xsim 没有 settings64.sh 时立即阻断。
        if not namespace_args.vivado_settings:

            # 提示用户补充远端绝对路径。
            parser.error("> ERR: [Python] --simulator-backend xsim requires --vivado-settings.")

        # 校验并保存 settings64.sh 绝对 POSIX 路径。
        dict_selection["vivado_settings64"] = require_remote_absolute_file_path(  # xsim 的 Vivado 激活脚本路径
            namespace_args.vivado_settings,  # 用户确认的远端 settings64.sh 路径
            "--vivado-settings",  # 非 xsim 附带 Vivado 路径的 CLI 错误标签
        )

    # 非 xsim 后端也允许额外保存 Vivado 路径，供后续切回 xsim 使用。
    elif namespace_args.vivado_settings:

        # 额外路径同样必须满足远端绝对 POSIX 文件路径规则。
        dict_selection["vivado_settings64"] = require_remote_absolute_file_path(  # 非 xsim 后端附带记录的 Vivado 路径
            namespace_args.vivado_settings,  # 非 xsim 后端附带保存的 Vivado 路径
            "--vivado-settings",  # CLI 参数名用于错误提示
        )

    # 返回可直接交给 build_remote_runtime_config_payload 的选择字典。
    return dict_selection

# build_remote_runtime_config_payload 生成 verilog.remote.json 标准结构。
def build_remote_runtime_config_payload(dict_selection: dict[str, Any]) -> dict[str, Any]:
    """把工具链选择封装为 remote runtime 配置载荷。

    :param dict_selection: 用户确认的工具链选择字段。
    :return: 可写入 verilog.remote.json 的标准配置字典。
    """

    # 基础结构保留 env/tools 空字典，方便未来远端运行时扩展。
    dict_payload: dict[str, Any] = {  # runtime 配置 JSON 根对象
        "version": 1,  # runtime 配置结构版本
        "remote": {  # 远端运行时配置分组
            "toolchain": {  # 仿真工具链选择分组
                "simulator_backend": dict_selection["simulator_backend"],  # validation 后端优先级使用的确认值
            },
            "env": {},  # 预留远端环境变量覆盖
            "tools": {},  # 预留远端工具路径覆盖
        },
    }

    # Vivado settings64.sh 只有存在且非空时写入。
    str_vivado_settings = str(dict_selection.get("vivado_settings64") or "")  # 可选 Vivado settings64.sh 路径

    # 空字符串不进入配置，避免误导非 xsim 后端。
    if str_vivado_settings.strip():

        # 保存去除首尾空白后的远端绝对路径。
        dict_payload["remote"]["toolchain"]["vivado_settings64"] = str_vivado_settings.strip()  # xsim 激活脚本路径

    # 返回标准结构供本地写入和远端上传共用。
    return dict_payload

# write_remote_runtime_config 按稳定格式写入本地 runtime 配置副本。
def write_remote_runtime_config(path_config: Path, dict_payload: dict[str, Any]) -> None:
    """写入 verilog.remote.json 配置文件。

    :param path_config: 本地目标配置路径。
    :param dict_payload: 待写入的 runtime 配置字典。
    :return: 不返回业务值；文件写入完成即表示配置已落盘。
    """

    # 确保项目本地 .settings 目录存在。
    path_config.parent.mkdir(parents=True, exist_ok=True)

    # 以 UTF-8 和稳定缩进写出，便于审计 diff。
    path_config.write_text(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# require_remote_relative_path 校验远端 workdir 内相对路径。
def require_remote_relative_path(str_value: str, str_label: str) -> str:
    """校验远端相对 POSIX 路径。

    :param str_value: 待校验路径文本。
    :param str_label: 错误消息中的配置字段名。
    :return: 归一化后的 POSIX 相对路径。
    :raises ValueError: 路径为空、绝对、含反斜杠或含父目录时抛出。
    """

    # 去除首尾空白后检查远端相对路径空值。
    str_raw = str_value.strip()  # 待校验的远端相对路径文本

    # 空路径会导致远端 helper 目标不明确。
    if not str_raw:

        # 空相对路径无法映射到远端 workdir 内的稳定目标。
        raise ValueError("> ERR: [Python] remote relative path must not be empty.")

    # 远端路径必须使用 POSIX 分隔符。
    if "\\" in str_raw:

        # 反斜杠可能表示本地 Windows 路径，远端 helper 不能安全解释。
        raise ValueError("> ERR: [Python] remote relative path must use POSIX separators.")

    # PurePosixPath 负责归一化 POSIX 片段。
    path_remote = PurePosixPath(str_raw)  # POSIX 路径对象

    # retained run 和配置路径必须相对远端 workdir。
    if path_remote.is_absolute() or str_raw.startswith("~"):

        # retained run 只能落在配置的远端 workdir 内部。
        raise ValueError(
            "> ERR: [Python] remote path must be relative to the configured remote workdir."
        )

    # 路径片段决定 retained run 是否会逃出配置的远端工作区。
    tuple_parts = path_remote.parts  # 防止相对路径含当前目录或父目录穿越的片段序列

    # 空片段、当前目录和父目录都不允许。
    if not tuple_parts or any(str_part in {"", ".", ".."} for str_part in tuple_parts):

        # 父目录穿越会突破远端 workdir 约束。
        raise ValueError(
            "> ERR: [Python] remote relative path must be normalized without parent traversal."
        )

    # 返回 POSIX 字符串供 shell 和 helper 使用。
    return path_remote.as_posix()

# require_remote_absolute_file_path 校验远端绝对文件路径。
def require_remote_absolute_file_path(str_value: str, str_label: str) -> str:
    """校验远端绝对 POSIX 文件路径。

    :param str_value: 待校验路径文本。
    :param str_label: 错误消息中的参数名。
    :return: 归一化后的绝对 POSIX 路径。
    :raises ValueError: 路径为空、非绝对、含反斜杠或含父目录时抛出。
    """

    # 去除首尾空白后检查远端绝对路径空值。
    str_raw = str_value.strip()  # 待校验的远端绝对文件路径文本

    # settings64.sh 路径不能为空。
    if not str_raw:

        # 空 settings64.sh 无法用于远端工具链激活。
        raise ValueError("> ERR: [Python] remote absolute file path must not be empty.")

    # 绝对 settings64.sh 值不能混入 Windows 分隔符。
    if "\\" in str_raw:

        # settings64.sh 参数同样禁止 Windows 反斜杠。
        raise ValueError("> ERR: [Python] remote absolute file path must use POSIX separators.")

    # 绝对文件路径使用 PurePosixPath 拆解 root 与剩余片段。
    path_remote = PurePosixPath(str_raw)  # 绝对路径对象用于检查 root 和后续片段

    # settings64.sh 必须是远端绝对路径，不能依赖 shell 展开。
    if not path_remote.is_absolute() or str_raw.startswith("~"):

        # settings64.sh 需要绝对路径，避免远端 cwd 或 shell 展开影响结果。
        raise ValueError(
            "> ERR: [Python] remote file path must be absolute on the remote server."
        )

    # 根目录后的片段不能包含当前目录或父目录。
    if any(str_part in {"", ".", ".."} for str_part in path_remote.parts[1:]):

        # settings64.sh 路径不得通过父目录片段绕过用户确认值。
        raise ValueError(
            "> ERR: [Python] remote absolute file path must be normalized without parent traversal."
        )

    # 返回 POSIX 字符串供配置写入和 shell 比较。
    return path_remote.as_posix()
