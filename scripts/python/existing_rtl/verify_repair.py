"""existing RTL verify-repair 的兼容入口。"""

# 延迟注解让 facade 只承担导出职责。
from __future__ import annotations

# 路径和宽松 JSON 类型用于复刻旧 facade 签名。
from pathlib import Path
from typing import Any

# 主流程模块承载 verify_existing 的完整编排。
from . import verify_repair_core as _core

# support 模块承载入口校验、日志诊断和稳定常量。
from . import verify_repair_support as _support

# automation mode 常量保持旧导出路径。
AUTOMATION_MODES: tuple[str, ...] = _support.AUTOMATION_MODES  # verify-repair 自动化模式集合

# TB language 集合在旧调用方里常用于 CLI choices。
TB_LANGUAGES: tuple[str, ...] = _support.TB_LANGUAGES  # testbench 语言集合

# TB mode 集合保持 generate/augment 分支的旧读取位置。
TB_MODES: tuple[str, ...] = _support.TB_MODES  # testbench 处理模式集合

# 日志诊断入口保留旧模块路径，便于外部脚本继续导入。
def diagnose_log_texts(*, compile_log: str, simulation_log: str, executed: bool) -> dict[str, Any]:
    """分类编译和仿真日志中的 verify-repair 诊断信息。

    Args:
        compile_log: 编译阶段输出文本。
        simulation_log: 仿真阶段输出文本。
        executed: 外部验证流程是否已经实际执行。

    Returns:
        可写入报告的日志诊断字典。
    """

    # 真实规则集中在 support 模块，facade 只保留旧入口。
    return _support.diagnose_log_texts(
        compile_log=compile_log,
        simulation_log=simulation_log,
        executed=executed,
    )

# automation mode 校验是写回权限进入 core 前的兼容 guard。
def require_automation_mode(str_value: str) -> str:
    """校验并规范化 verify-repair 自动化模式。

    Args:
        str_value: 调用方传入的自动化模式文本。

    Returns:
        support 模块认可的标准模式文本。
    """

    # 委托 support 保持错误消息和允许值完全一致。
    return _support.require_automation_mode(str_value)

# TB language 校验继续服务直接从旧模块导入的脚本。
def require_tb_language(str_value: str) -> str:
    """校验并规范化 testbench 语言。

    Args:
        str_value: 调用方传入的 testbench 语言文本。

    Returns:
        support 模块认可的标准语言文本。
    """

    # 委托 support 保持语言集合和大小写规整逻辑一致。
    return _support.require_tb_language(str_value)

# TB mode 校验继续隔离 testbench 生成与增强策略。
def require_tb_mode(str_value: str) -> str:
    """校验并规范化 testbench 处理模式。

    Args:
        str_value: 调用方传入的 testbench 模式文本。

    Returns:
        support 模块认可的标准模式文本。
    """

    # 委托 support 保持 generate/augment 分支边界一致。
    return _support.require_tb_mode(str_value)

# verify_existing 是对外主入口，真实实现位于 core helper。
def verify_existing(source_paths: list[Path], **dict_verify_options: Any) -> dict[str, Any]:
    """运行 existing RTL verify-repair，并保留旧 facade 入口。

    Args:
        source_paths: 待分析和验证的 RTL/TB 源文件路径。
        dict_verify_options: 旧调用方传入 core verify_existing 的关键字参数。

    Returns:
        verify-repair 运行报告和工件路径字典。
    """

    # core 模块承载真实编排，facade 不复制业务逻辑。
    return _core.verify_existing(source_paths, **dict_verify_options)

# 私有元组集中保存旧模块允许导入的稳定符号名。
tuple_public_verify_repair_exports: tuple[str, ...] = (  # verify_repair 旧导入语句允许解析的符号顺序
    "AUTOMATION_MODES",  # 旧调用方读取的自动化模式枚举
    "TB_LANGUAGES",  # 旧调用方读取的 testbench 语言枚举
    "TB_MODES",  # 旧调用方读取的 testbench 处理策略枚举
    "diagnose_log_texts",  # 旧调用方分析编译与仿真 transcript 的入口
    "require_automation_mode",  # 旧调用方校验 automation-mode 取值的入口
    "require_tb_language",  # 旧调用方校验 testbench 语言取值的入口
    "require_tb_mode",  # 旧调用方校验 testbench 模式取值的入口
    "verify_existing",  # 旧调用方启动 existing RTL verify-repair 的入口
)

# __all__ 保持 list 形态，避免旧测试或调用方依赖可变序列时失配。
__all__: list[str] = list(tuple_public_verify_repair_exports)  # 旧导入清单的 list 兼容副本
