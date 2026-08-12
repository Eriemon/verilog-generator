"""工作流执行阶段使用的可插拔模型 provider 适配层。"""

# 允许在注解中直接使用当前模块稍后定义的类型名
from __future__ import annotations

# 标准库依赖
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence, TypedDict

# provider 通用错误
class ModelProviderError(ValueError):
    """表示模型 provider 无法返回有效响应。"""

# 手工响应缺失错误
class ManualResponseRequired(ModelProviderError):
    """表示手工 provider 缺少预先准备好的响应文件。"""

# 单次生成阶段的稳定上下文
@dataclass(frozen=True)
class GenerationContext:
    """
    描述单个生成阶段对 provider 暴露的稳定上下文。

    该结构固定 prompt、response、run 目录与 manifest 快照，
    避免 provider 在运行时自行推断路径或工作流状态。
    """

    # 当前尝试编号
    attempt_id: str  # 当前 attempt 的稳定标识

    # 当前工作流阶段名称
    stage: str  # requirements/codegen/python/rtl/review 等阶段名

    # 本阶段 prompt 文件路径
    prompt_path: Path  # provider 读取的提示词文件

    # 本阶段响应文件路径
    response_path: Path  # provider 需要写入或回读的响应文件

    # 当前运行目录
    run_dir: Path  # 子进程或手工文件默认工作目录

    # 当前 attempt 目录，用于归档单次生成的阶段产物与日志
    attempt_dir: Path  # 工作流 attempt 的归档根目录

    # 规格快照，固定 provider 本轮面对的规范化设计输入
    spec: dict[str, Any]  # 本轮生成使用的规范化 spec

    # 阶段 manifest，声明当前阶段允许读写的文件与检查边界
    manifest: dict[str, Any]  # 当前阶段应产出的文件与检查约束

    # 工作流配置快照
    workflow_config: dict[str, Any]  # provider 可见的 workflow 配置

    # 可选向量契约
    vector_contract: dict[str, Any] | None = None  # 向量校验摘要与哈希

    # 期望注释语言
    comment_language: str = "zh"  # mock 产物默认使用中文语义注释

# mock 端口在本模块里会读取到的最小字段集合
class MockPortSpec(TypedDict, total=False):
    """
    描述 mock 端口条目在当前模块里会读取到的最小字段集合。

    :param: 无输入参数；字段集合由当前 mock 端口解析路径固定定义。
    """

    # 端口原始名称。
    name: str  # 当前 mock 路径用来匹配 data、valid、clock、reset 语义的端口名

    # 方向字段决定当前端口会进入输入采样、输出桥接还是双向占位路径。
    direction: str  # 当前端口在模块声明里的 input/output/inout 方向标签

    # 角色字段决定当前端口是否会被识别成时钟、复位或普通业务信号。
    role: str  # 当前端口在规格里声明的 clock、reset 或普通业务角色

    # 位宽字段驱动 reg/wire 声明宽度与缺省字面量的位数推导。
    width: int  # 当前端口总线位宽，供 reg/wire 声明和默认值推导复用

# provider 基础协议
class ModelProvider(Protocol):
    """约束工作流可调用的 provider 接口。"""

    # provider 名称，用于工厂分发和运行日志标识
    name: str  # 注册名与日志展示名

    # 是否支持流式输出
    supports_streaming: bool  # workflow 是否可逐块消费响应

    # 同步生成接口
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        生成单次完整响应。

        :param prompt: 传给 provider 的完整提示词文本。
        :param context: provider 可见的只读阶段上下文。
        :return: 原始 fenced-block 格式响应文本。
        """

    # 流式生成接口
    def generate_stream(self, prompt: str, context: GenerationContext) -> Iterator[str]:
        """
        逐块生成响应。

        :param prompt: 传给 provider 的完整提示词文本。
        :param context: provider 可见的只读阶段上下文。
        :return: 依次产出响应文本分块的迭代器。
        """

# 根据名称构造 provider
def build_model_provider(
    provider_name: str,
    *,
    command: str | Sequence[str] | None = None,
    timeout_s: int = 120,
    config: dict[str, Any] | None = None,
) -> ModelProvider:
    """
    根据配置名称创建具体 provider 实例。

    :param provider_name: provider 注册名，例如 mock、manual 或 command。
    :param command: command provider 需要执行的命令行模板。
    :param timeout_s: command provider 的超时时间，单位秒。
    :param config: provider 私有配置字典。
    :return: 满足 ModelProvider 协议的 provider 实例。
    :raises ModelProviderError: 当 provider 名称未知或 command 缺失时抛出。
    """

    # 归一化 provider 名称
    str_provider_name = provider_name.lower()  # 统一的小写 provider 选择键

    # 分发到 mock provider
    if str_provider_name == "mock":

        # 避免 mock 文本生成辅助逻辑拖大主模块导入体积。
        from .model_provider_mock import MockModelProvider

        # 命中 mock 模式时返回确定性响应实现。
        return MockModelProvider(config=config)

    # 分发到手工 provider
    if str_provider_name == "manual":

        # 命中 manual 模式时返回依赖响应文件的手工实现。
        return ManualModelProvider()

    # 分发到命令行 provider
    if str_provider_name == "command":

        # command provider 必须拿到可执行命令模板。
        if not command:

            # 报告 command provider 缺少模型命令配置。
            raise ModelProviderError("> ERR: [Python] command provider 缺少模型命令配置")

        # 命令行 provider 实现拆到独立模块，主模块仅保留工厂与合同。
        from .model_provider_command import CommandModelProvider

        # 返回基于子进程执行的 provider
        return CommandModelProvider(command, timeout_s=timeout_s)

    # 拒绝未知 provider 名称
    raise ModelProviderError(f"> ERR: [Python] 未知模型 provider: {provider_name!r}")

# 依赖外部预置响应文件的 provider
class ManualModelProvider:
    """从既有响应文件读取内容的手工 provider。"""

    # provider 名称，固定标识手工响应读取模式
    name = "manual"  # 手工 provider 注册名

    # 手工 provider 不支持真实流式
    supports_streaming = False  # 仅通过单次读取模拟流式

    # 读取手工响应文件
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        读取预先准备好的响应文件。

        :param prompt: 当前阶段提示词文本；手工 provider 不直接使用它。
        :param context: 提供响应文件路径的阶段上下文。
        :return: UTF-8 读取出的完整响应文本。
        :raises ManualResponseRequired: 当响应文件尚未准备好时抛出。
        """

        # 手工 provider 不直接消费 prompt 正文
        del prompt

        # 阻止缺失响应文件的手工执行
        if not context.response_path.exists():

            # 报告需要人工准备响应文件
            raise ManualResponseRequired(
                f"> ERR: [Python] manual provider 缺少响应文件: {context.response_path}"
            )

        # 读取人工准备好的 UTF-8 响应内容
        return context.response_path.read_text(encoding="utf-8")

    # 用单块结果兼容流式接口
    def generate_stream(self, prompt: str, context: GenerationContext) -> Iterator[str]:
        """
        以单块形式暴露手工响应结果。

        :param prompt: 当前阶段提示词文本。
        :param context: 提供响应文件路径的阶段上下文。
        :return: 仅产出一个文本块的迭代器。
        """

        # 产出完整响应，兼容流式调用方
        yield self.generate(prompt, context)
