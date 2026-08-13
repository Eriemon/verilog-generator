"""根据 formatter 配置创建具体后端实例。"""

# 延迟类型注解求值，避免工厂导入阶段解析复杂类型
from __future__ import annotations

# 本地内置后端是当前发行包唯一可用的 formatter 实现
from .builtin_backend import BuiltinFormatterBackend
from .engine import VerilogFormatterError

# 后端工厂保留 pyslang 分支的显式错误，方便未来扩展
def create_backend(config: dict) -> BuiltinFormatterBackend:
    """
    按 formatter 配置创建可用后端。

    :param config: formatter 配置字典，包含 formatter.backend 可选字段。
    :return: 当前环境可用的 formatter 后端实例。
    :raises VerilogFormatterError: 请求了不可用或未知后端时抛出。
    """

    # backend 名称默认使用随包内置实现，保持离线可运行
    backend_name = config.get("formatter", {}).get("backend", "builtin")  # formatter 后端名称

    # builtin 是当前 runtime 内置并可直接构造的后端
    if backend_name == "builtin":

        # 返回内置后端实例，配置对象继续由引擎解释
        return BuiltinFormatterBackend(config)

    # pyslang 分支保留明确错误，避免调用方误以为环境已安装该后端
    if backend_name == "pyslang":

        # 当前发行包不携带 pyslang，必须阻止静默降级
        raise VerilogFormatterError("> ERR: [Python] 当前环境不可使用 pyslang formatter 后端。")

    # 未知后端名称说明配置输入不受支持
    raise VerilogFormatterError(f"> ERR: [Python] 未知 formatter 后端: {backend_name}")
