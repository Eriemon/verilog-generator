"""定义 Verilog formatter 后端需要实现的抽象接口。"""

# 延迟类型注解求值，降低接口模块导入成本
from __future__ import annotations

# 抽象基类用于约束具体后端必须实现的能力
from abc import ABC, abstractmethod
from pathlib import Path

# 评分报告类型由 scoring 模块统一定义
from .scoring import ScoreReport

# FormatterBackend 是 formatter CLI 和 runtime AST 检查之间的稳定协议
class FormatterBackend(ABC):
    """约束 formatter 后端的格式化、检查和评分接口。"""

    # 路径级格式化接口同时覆盖写文件、检查和 diff 三类 CLI 模式
    @abstractmethod

    # 路径级入口承载 CLI 写回策略，具体后端必须明确实现
    def format_path(
        self,
        input_path: Path,
        output_path: Path | None = None,

        # 以下布尔参数直接映射 CLI 行为开关，保持接口层显式可读
        inplace: bool = False,
        check: bool = False,
        diff: bool = False,
    ) -> tuple[int, str]:
        """
        格式化或检查指定路径的 RTL 文件。

        :param input_path: 输入 RTL 文件路径。
        :param output_path: 可选输出文件路径。
        :param inplace: 是否原地改写输入文件。
        :param check: 是否只检查格式而不写出。
        :param diff: 是否输出格式差异。
        :return: 进程式状态码和用户可读消息。
        :raises NotImplementedError: 具体后端未提供路径级格式化实现时抛出。
        """

        # 路径格式化涉及写盘策略，必须由具体后端按自身能力实现
        raise NotImplementedError("> ERR: [Python] formatter 后端未实现路径级格式化接口。")

    # 文本级格式化接口供 runtime AST 检查和单测直接复用
    @abstractmethod

    # 文本级入口服务内存中的 generated RTL，不应触碰文件系统
    def format_text(self, source: str, source_path: Path | None = None) -> str:
        """
        格式化内存中的 RTL 文本。

        :param source: 待格式化的 Verilog/SystemVerilog 文本。
        :param source_path: 可选来源路径，用于错误消息和规则上下文。
        :return: 格式化后的 RTL 文本。
        :raises NotImplementedError: 具体后端未提供文本格式化实现时抛出。
        """

        # 文本格式化不能在协议层猜测语法策略，交给后端实现
        raise NotImplementedError("> ERR: [Python] formatter 后端未实现文本格式化接口。")

    # 文本级检查接口只返回诊断，不改写输入文本
    @abstractmethod

    # 检查入口只报告格式问题，调用方据此决定是否阻断写回
    def check_text(self, source: str, source_path: Path | None = None) -> list[str]:
        """
        检查内存中的 RTL 文本并返回诊断列表。

        :param source: 待检查的 Verilog/SystemVerilog 文本。
        :param source_path: 可选来源路径，用于诊断定位。
        :return: formatter 检查发现的诊断消息列表。
        :raises NotImplementedError: 具体后端未提供文本检查实现时抛出。
        """

        # 诊断生成依赖后端规则集，协议层只固定返回类型
        raise NotImplementedError("> ERR: [Python] formatter 后端未实现文本检查接口。")

    # 路径级评分接口负责读取文件并产出结构化评分
    @abstractmethod

    # 文件评分入口负责把磁盘 RTL 转换为统一评分报告
    def score_path(self, path: Path) -> ScoreReport:
        """
        对指定 RTL 文件执行格式质量评分。

        :param path: 待评分的 RTL 源文件路径。
        :return: 结构化格式评分报告。
        :raises NotImplementedError: 具体后端未提供路径评分实现时抛出。
        """

        # 文件评分需要后端读取并解释 RTL 内容，基类不兜底
        raise NotImplementedError("> ERR: [Python] formatter 后端未实现路径评分接口。")

    # 文本级评分接口用于尚未落盘的 generated RTL
    @abstractmethod

    # 文本评分入口用于生成链路在写盘前做质量判定
    def score_text(self, source: str, source_path: Path | None = None) -> ScoreReport:
        """
        对内存中的 RTL 文本执行格式质量评分。

        :param source: 待评分的 Verilog/SystemVerilog 文本。
        :param source_path: 可选来源路径，用于诊断定位。
        :return: 结构化格式评分报告。
        :raises NotImplementedError: 具体后端未提供文本评分实现时抛出。
        """

        # 文本评分的指标口径由后端统一维护，基类只声明契约
        raise NotImplementedError("> ERR: [Python] formatter 后端未实现文本评分接口。")
