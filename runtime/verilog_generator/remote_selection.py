"""读取和写入项目本地远程运行选择状态。"""

# 延迟注解解析，避免导入期求值复杂类型。
from __future__ import annotations

# 标准库依赖负责 JSON 状态文件和 UTC 时间戳。
import json
import time
from pathlib import Path
from typing import Any

# config 模块集中维护远程配置路径和 settings 字段读取规则。
from .config import local_remote_selection_path, remote_runtime_settings_relpath, remote_setting

# 公开入口返回远端工作目录内 runtime 配置的相对路径。
def remote_runtime_config_relpath(settings: dict[str, Any] | None = None) -> str:
    """返回远端工作目录下的 runtime 配置相对路径。

    参数:
        settings: 可选项目 settings；若包含 remote_runtime_config 则优先使用。

    返回:
        远端 workdir 内的 `.settings/verilog.remote.json` 相对路径或配置覆盖值。
    """

    # settings 中的显式配置优先，便于项目自定义远端布局。
    if settings is not None:

        # remote_setting 会在字段缺失时抛 KeyError，缺失则回退默认路径。
        try:

            # 返回项目显式声明的远端 runtime 配置路径。
            return remote_setting(settings, "remote_runtime_config")

        # 缺失覆盖字段时回到仓库约定路径。
        except KeyError:

            # 没有覆盖配置时继续使用默认路径。
            pass

    # 默认路径由 config 模块统一维护。
    return remote_runtime_settings_relpath()

# 公开读取器从本地选择文件加载用户确认过的远程服务器。
def load_confirmed_remote_server(path: Path) -> dict[str, Any] | None:
    """读取 `.settings/remote-selection.local.json` 中的 server_id。

    参数:
        path: 本地远程选择状态文件路径。

    返回:
        规范化后的服务器选择字典；文件缺失或 server_id 为空时返回 None。

    异常:
        ValueError: 选择状态文件存在但不是 JSON 对象。
    """

    # 选择文件不存在表示用户尚未确认远程服务器。
    if not path.exists():

        # None 让上游保持本地执行或提示用户选择。
        return None

    # 状态文件必须是 JSON 对象，避免把数组或字符串误读成配置。
    payload = json.loads(path.read_text(encoding="utf-8"))  # 本地服务器选择状态载荷

    # 非对象配置直接报错，提示本地状态文件损坏。
    if not isinstance(payload, dict):

        # ValueError 保持配置加载入口的既有异常类型。
        raise ValueError(f"> ERR: [Python] Remote selection settings must be a JSON object: {path}")

    # server_id 是唯一必需字段，空字符串视为未选择。
    str_server_id: Any = payload.get("server_id")  # 原始服务器 id

    # 缺失或空白 server_id 不算已确认选择。
    if not isinstance(str_server_id, str) or not str_server_id.strip():

        # 返回 None 而不是异常，兼容空白本地选择文件。
        return None

    # 返回字段形状由调用方和报告逻辑消费，保持旧版兼容。
    return {
        "server_id": str_server_id.strip(),  # 规范化服务器 id
        "confirmed_by_user": True,  # 表示该选择来自本地确认状态
        "updated_at": payload.get("updated_at"),  # 选择更新时间
        "source": str(path),  # 状态文件来源路径
    }

# 公开解析器按 settings 优先级找到当前本地确认的远程服务器。
def resolve_confirmed_remote_server(settings: dict[str, Any]) -> dict[str, Any] | None:
    """按项目 settings 解析已确认远程服务器。

    参数:
        settings: 项目配置字典，可能包含 selection_path 或元信息路径。

    返回:
        已确认服务器选择；找不到或默认路径读取失败时返回 None。
    """

    # 显式 selection_path 拥有最高优先级。
    try:

        # 从 project settings 读取本地远程选择文件的显式路径。
        str_selection_path = remote_setting(settings, "selection_path")  # settings 中的显式选择文件路径

    # selection_path 缺失时继续尝试 settings 元信息和默认路径。
    except KeyError:

        # 未配置 selection_path 时继续查 settings 元信息。
        str_selection_path = ""  # 表示没有显式 selection_path 配置

    # settings 中提供的 selection_path 非空时直接读取。
    if isinstance(str_selection_path, str) and str_selection_path:

        # 调用底层读取器统一验证 JSON 形状。
        return load_confirmed_remote_server(Path(str_selection_path))

    # config 加载器可能把本地选择文件路径放在元信息里。
    dict_meta: Any = settings.get("__verilog_settings_meta__", {})  # config 注入的本地路径元信息

    # 只有 dict 元信息才可信。
    local_path = dict_meta.get("local_selection_path") if isinstance(dict_meta, dict) else None  # 本地选择路径

    # 元信息路径存在时使用该路径。
    if isinstance(local_path, str) and local_path:

        # 元信息路径同样走统一读取逻辑。
        return load_confirmed_remote_server(Path(local_path))

    # 最后回退仓库默认本地选择文件；默认路径读取异常按未选择处理。
    try:

        # 使用仓库默认 `.settings/remote-selection.local.json` 作为兜底来源。
        return load_confirmed_remote_server(local_remote_selection_path())

    # 默认状态文件损坏不应阻断本地无远端路径。
    except Exception:

        # 默认路径异常通常来自缺失工作区状态，保持宽容返回 None。
        return None

# 公开读取器解析远端 workdir 内的 runtime 配置。
def load_remote_runtime_config(path: Path) -> dict[str, Any]:
    """读取并校验 `.settings/verilog.remote.json`。

    参数:
        path: 远端 runtime 配置文件路径，可能是下载到本地的副本。

    返回:
        包含 toolchain、env、tools 和 source 的规范化配置字典。

    异常:
        ValueError: 配置文件不是对象，或缺少必需的 remote.toolchain.simulator_backend。
    """

    # 远端配置文件必须是 JSON 对象。
    payload = json.loads(path.read_text(encoding="utf-8"))  # 远端 runtime 配置载荷

    # 顶层非对象说明配置文件格式错误。
    if not isinstance(payload, dict):

        # 错误消息带路径，便于定位远端下载副本。
        raise ValueError(f"> ERR: [Python] Remote runtime config must be a JSON object: {path}")

    # remote 对象承载远端执行环境信息。
    dict_remote: Any = payload.get("remote", {})  # remote 配置对象

    # remote 必须是对象，才能继续读取 toolchain/env/tools。
    if not isinstance(dict_remote, dict):

        # 明确指出 remote 字段不是对象，避免后续把非对象当环境配置读取。
        raise ValueError(f"> ERR: [Python] Remote runtime config missing remote object: {path}")

    # toolchain 对象声明远端模拟器和可选 Vivado settings64。
    dict_toolchain: Any = dict_remote.get("toolchain", {})  # 远端工具链配置

    # toolchain 必须是对象，避免静默忽略错误配置。
    if not isinstance(dict_toolchain, dict):

        # 明确指出 toolchain 字段不是对象，避免后续误读 simulator_backend。
        raise ValueError(f"> ERR: [Python] Remote runtime config toolchain must be an object: {path}")

    # simulator_backend 是远端验证最小必需字段。
    str_backend: Any = dict_toolchain.get("simulator_backend")  # 模拟器后端

    # 缺失或空白 backend 会使后续远端验证无法选择命令。
    if not isinstance(str_backend, str) or not str_backend.strip():

        # 明确指出 simulator_backend 缺失，远端验证无法选择模拟器后端。
        raise ValueError(f"> ERR: [Python] Remote runtime config missing remote.toolchain.simulator_backend: {path}")

    # 工具链输出只包含已验证字段，避免把未知字段误传给运行器。
    dict_resolved_toolchain = {
        "simulator_backend": str_backend.strip(),  # 远端模拟器后端
    }  # 规范化工具链配置

    # vivado_settings64 可选，只有非空字符串才透传。
    str_vivado_settings: Any = dict_toolchain.get("vivado_settings64")  # 可选 Vivado settings64 脚本路径

    # 可选 Vivado 环境脚本用于 xsim/vivado 后端。
    if isinstance(str_vivado_settings, str) and str_vivado_settings.strip():

        # 规范化首尾空白后写入工具链配置。
        dict_resolved_toolchain["vivado_settings64"] = str_vivado_settings.strip()  # 规范化后的 Vivado 环境脚本

    # env/tools 只接受对象；其它形态按空字典处理，保持旧版宽容语义。
    dict_env = dict_remote.get("env", {}) if isinstance(dict_remote.get("env", {}), dict) else {}  # 远端环境变量

    # tools 字段同样只透传对象形态。
    dict_tools = dict_remote.get("tools", {}) if isinstance(dict_remote.get("tools", {}), dict) else {}  # 远端工具路径

    # 返回规范化后的远端 runtime 配置。
    return {
        "toolchain": dict_resolved_toolchain,
        "env": dict_env,
        "tools": dict_tools,
        "source": str(path),
    }

# 公开写入器把用户确认的 server_id 持久化到本地选择文件。
def write_confirmed_remote_server(path: Path, server_id: str) -> Path:
    """写入 `.settings/remote-selection.local.json`。

    参数:
        path: 本地选择状态文件路径。
        server_id: 用户确认的远程服务器标识。

    返回:
        写入后的状态文件路径。

    异常:
        ValueError: server_id 为空，或已有选择文件不是 JSON 对象。
    """

    # server_id 去除首尾空白后作为唯一选择值。
    str_normalized_server_id = server_id.strip()  # 即将写入本地状态文件的服务器 id

    # 空 server_id 不允许写入，避免破坏已确认选择语义。
    if not str_normalized_server_id:

        # ValueError 与读取器的配置错误类型保持一致。
        raise ValueError("> ERR: [Python] Confirmed remote server id must not be empty.")

    # 已存在文件时保留其中其它字段，只更新 server_id 和 updated_at。
    if path.exists():

        # 读取原有 payload，避免覆盖用户或工具保留的附加字段。
        dict_payload = json.loads(path.read_text(encoding="utf-8"))  # 原本地选择 JSON 载荷

        # 本地选择文件必须是对象，否则拒绝继续写入。
        if not isinstance(dict_payload, dict):

            # 已存在文件结构损坏时拒绝覆盖，避免丢失人工字段。
            raise ValueError(f"> ERR: [Python] Remote selection settings must be a JSON object: {path}")

    # 选择文件不存在时创建版本化初始 payload。
    else:

        # 新文件从 version=1 起步。
        dict_payload = {"version": 1}  # 新建本地选择 JSON 载荷

    # 写入用户刚确认的服务器 id。
    dict_payload["server_id"] = str_normalized_server_id  # 本次确认的服务器 id

    # 更新时间戳使用 UTC Zulu 格式，便于跨机器比较。
    dict_payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())  # 选择更新时间戳

    # 确保 .settings 目录存在。
    path.parent.mkdir(parents=True, exist_ok=True)

    # ensure_ascii=False 保留未来可能出现的非 ASCII 元数据。
    path.write_text(json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 返回写入路径，便于 CLI 输出或测试断言。
    return path
