"""工作流执行阶段使用的可插拔模型 provider 适配层。"""

# 允许在注解中直接使用当前模块稍后定义的类型名
from __future__ import annotations

# 标准库依赖
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence, TypedDict, cast

# 向量契约标签
from .formatter_backend.banners import display_width
from .vectors import VECTOR_HASH_TAG

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

# 通过子进程执行命令的 provider
class CommandModelProvider:
    """调用外部命令并收集 stdout 或响应文件的 provider。"""

    # provider 名称，固定标识命令行执行模式
    name = "command"  # 命令行 provider 注册名

    # 子进程 provider 声称支持流式接口
    supports_streaming = True  # 通过统一接口向工作流暴露流式能力

    # 初始化命令行 provider
    def __init__(self, command: str | Sequence[str], *, timeout_s: int = 120) -> None:
        """
        归一化命令模板与超时配置。

        :param command: 原始命令字符串或参数序列。
        :param timeout_s: 子进程执行超时，单位秒。
        :return: 无业务返回值。
        """

        # 规范化命令参数序列
        self._command = _normalize_command(command)  # provider 启动命令参数

        # 保存命令执行超时
        self._timeout_s = timeout_s  # provider 子进程超时秒数

    # 执行命令并收集完整响应
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        调用外部命令并返回完整响应。

        :param prompt: 发送到子进程 stdin 的提示词文本。
        :param context: 提供 cwd、环境变量与响应回读路径的阶段上下文。
        :return: 子进程 stdout 或 response_path 中的完整文本。
        :raises ModelProviderError: 当子进程启动失败、超时或返回非零状态时抛出。
        """

        # 构造 provider 子进程环境变量
        dict_env = _command_env(context)  # 子进程继承并扩展后的环境

        # 展开命令模板占位符
        list_command = _expanded_command(self._command, context)  # 最终执行的命令数组

        # 执行 provider 子进程
        try:

            # 同步运行命令并收集标准输出
            completed_process = subprocess.run(  # 本次 provider 调用的完整子进程返回对象
                list_command,  # 当前要执行的 provider 命令数组
                cwd=context.run_dir,  # provider 子进程的工作目录
                input=prompt,  # 发送到 stdin 的提示词正文
                capture_output=True,  # 同时捕获 stdout 与 stderr

                # 下半段参数约束 provider 调用的文本模式、超时和环境边界。
                text=True,  # 用文本模式读写子进程 IO
                timeout=self._timeout_s,  # provider 调用的超时上限
                check=False,  # 非零退出码交给后续统一报错
                env=dict_env,  # 本次调用注入后的环境变量
            )

        # 报告 provider 子进程超时
        except subprocess.TimeoutExpired as exc:

            # 包装成统一 provider 错误
            raise ModelProviderError(
                f"> ERR: [Python] command provider 执行超时，超过 {self._timeout_s}s"
            ) from exc

        # 报告 provider 子进程启动失败
        except OSError as exc:

            # 包装系统级启动错误
            raise ModelProviderError(f"> ERR: [Python] command provider 启动失败: {exc}") from exc

        # 报告 provider 命令执行失败
        if completed_process.returncode != 0:

            # 提取 stderr 或 stdout 的首行摘要
            str_output_text = (completed_process.stderr or completed_process.stdout).strip()  # 可见错误摘要文本

            # 整理用于报错的首行细节
            if str_output_text:

                # 从 stderr/stdout 中提取首行，作为更易读的失败摘要。
                str_error_detail = str_output_text.splitlines()[0]  # 可直接显示给调用方的失败首行

            # 当外部命令没有输出文本时，退化到 exit code 摘要分支。
            else:

                # 缺少输出文本时退化为 exit code，避免错误消息为空。
                str_error_detail = f"exit code {completed_process.returncode}"  # 无输出场景下的失败摘要

            # 报告外部命令返回非零状态
            raise ModelProviderError(f"> ERR: [Python] command provider 执行失败: {str_error_detail}")

        # 优先返回非空 stdout 响应
        if completed_process.stdout.strip():

            # 直接返回 provider 标准输出
            return completed_process.stdout

        # 回退读取 response_path 文件
        if context.response_path.exists():

            # 读取 provider 侧写回的响应文件
            return context.response_path.read_text(encoding="utf-8")

        # 报告 stdout 与响应文件同时缺失
        raise ModelProviderError(
            "> ERR: [Python] command provider 既未输出 stdout，也未写入预期响应文件"
        )

    # 以单次 generate 兼容流式接口
    def generate_stream(self, prompt: str, context: GenerationContext) -> Iterator[str]:
        """
        用单块输出兼容流式调用方。

        :param prompt: 发送到子进程 stdin 的提示词文本。
        :param context: 提供 cwd、环境变量与响应路径的阶段上下文。
        :return: 只产出一个完整响应块的迭代器。
        """

        # 继续复用完整 generate 逻辑，以单块产出兼容流式调用方。
        yield self.generate(prompt, context)

# 用于本地回归的确定性 mock provider
class MockModelProvider:
    """生成可预测 mock 清单、Python、RTL 与 review 产物的 provider。"""

    # provider 名称，固定标识本地确定性 mock 模式
    name = "mock"  # 本地确定性 mock provider 的注册名

    # mock provider 支持伪流式输出
    supports_streaming = True  # 通过拆块文本模拟流式

    # 保存 mock 行为配置
    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        """
        初始化 mock provider 配置。

        :param config: mock 行为覆盖配置。
        :return: 无业务返回值。
        """

        # 保存 mock 行为配置字典
        self._config = config or {}  # mock provider 配置快照

    # 生成确定性 mock 响应
    def generate(self, prompt: str, context: GenerationContext) -> str:
        """
        生成包含 manifest 与文件块的 mock 响应。

        :param prompt: 当前提示词文本；mock provider 只保留接口兼容，不直接解析。
        :param context: 提供 stage、spec 与 manifest 的阶段上下文。
        :return: fenced-block 格式的 mock 响应文本。
        """

        # mock provider 不直接消费 prompt 正文
        del prompt

        # 解析当前 stage 的 mock 行为模式
        str_mode = _mock_mode(context, self._config)  # 当前阶段的 mock 模式

        # 返回故意无效的响应文本
        if str_mode == "invalid_response":

            # 输出非 fenced-block 响应，供错误路径回归使用
            return "This is not a fenced response.\n"

        # 提取 manifest 中声明的目标文件
        list_files = [
            entry  # manifest 中保留的单个文件条目
            for entry in context.manifest.get("files", [])  # 遍历 manifest 声明的全部候选文件
            if isinstance(entry, dict) and entry.get("path")  # 只保留带 path 的合法文件声明
        ]  # 当前 stage 需要返回的文件清单

        # 在 spec_issue 模式下故意丢掉一个测试台文件
        if str_mode == "spec_issue" and len(list_files) > 1:

            # 选择优先丢弃的测试台路径
            str_dropped_path = next(  # spec_issue 场景下故意移除的文件路径
                (
                    str(entry["path"])  # 命中的 testbench 路径文本
                    for entry in list_files  # 在候选输出里查找最适合故意删掉的测试台文件
                    if entry.get("kind") == "testbench" or "_tb." in str(entry["path"]).lower()  # 优先选择测试台文件
                ),
                str(list_files[-1]["path"]),  # 没有测试台时回退到最后一个文件
            )  # 当前要剔除的错误样例文件路径

            # 从返回文件清单中移除目标路径
            list_files = [
                entry  # 保留未被故意剔除的文件条目
                for entry in list_files  # 遍历剔除前的文件清单
                if str(entry["path"]) != str_dropped_path  # 移除故意制造 spec 缺口的目标文件
            ]  # 触发 spec 缺口后的文件清单

        # 组装返回给调用方的 manifest 正文，描述本次 mock 响应包含哪些文件。
        dict_response_manifest = _build_mock_response_manifest(context, list_files)  # 描述 mock 响应交付文件集合的 manifest 正文

        # 构造各文件的文本内容映射
        dict_file_map = _mock_file_contents(context, list_files)  # 路径到内容的映射

        # 初始化回复首段的 manifest 代码块序列，保证解析器先看到文件清单再消费正文块。
        list_blocks = _mock_manifest_block_lines(dict_response_manifest)  # 回复头部先输出的 manifest 代码块行序列

        # 追加每个文件的 fenced-block 内容
        for dict_file_entry in list_files:

            # 提取当前文件的相对路径
            str_relative_path = str(dict_file_entry["path"])  # 响应块头使用的相对路径

            # 提取当前文件的语言标签
            str_language = str(dict_file_entry.get("language") or "text")  # 当前文件块 header 中声明的语法高亮标签

            # 追加当前文件块的头、正文与结束标记
            list_blocks.extend(
                [
                    f"```{str_language} path={str_relative_path}",
                    dict_file_map[str_relative_path].rstrip(),
                    "```",
                ]
            )

        # 返回拼接后的最终响应文本
        return "\n".join(list_blocks) + "\n"

    # 把完整响应切成有限文本块
    def generate_stream(self, prompt: str, context: GenerationContext) -> Iterator[str]:
        """
        把完整响应拆成数个文本块。

        :param prompt: 当前阶段提示词文本。
        :param context: 提供 stage、spec 与 manifest 的阶段上下文。
        :return: mock 响应文本块迭代器。
        """

        # 先构造完整 mock 响应
        str_response = self.generate(prompt, context)  # 完整 mock 文本

        # 对极短响应直接单块返回
        if len(str_response) <= 3:

            # 返回唯一响应块
            yield str_response

            # 在极短响应场景下立刻结束迭代。
            return

        # 计算三段式切块大小
        int_chunk_size = max(1, len(str_response) // 3)  # 每个流式分块的大致字符数

        # 按固定分块大小迭代切片
        for int_start in range(0, len(str_response), int_chunk_size):

            # 返回当前分块
            yield str_response[int_start : int_start + int_chunk_size]

# 归一化命令模板
def _normalize_command(command: str | Sequence[str]) -> list[str]:
    """
    把命令字符串或序列整理为参数列表。

    :param command: 原始命令字符串或参数序列。
    :return: 规范化后的命令参数列表。
    :raises ModelProviderError: 当命令为空时抛出。
    """

    # 解析字符串命令
    if isinstance(command, str):

        # 使用 Windows 友好的切词规则拆分命令
        list_parts = shlex.split(command, posix=False)  # 归一化后的命令参数

    # 复制已有参数序列
    else:

        # 转成字符串列表，统一后续处理
        list_parts = [str(item) for item in command]  # provider 命令参数列表

    # 阻止空命令进入执行阶段
    if not list_parts:

        # 报告命令模板为空
        raise ModelProviderError("> ERR: [Python] 模型命令不能为空")

    # 返回归一化后的命令数组
    return list_parts

# 构造 command provider 环境变量
def _command_env(context: GenerationContext) -> dict[str, str]:
    """
    生成 command provider 需要的环境变量字典。

    :param context: 提供 prompt/response 路径与 manifest 的阶段上下文。
    :return: 继承当前进程并注入工作流键值后的环境字典。
    """

    # 复制当前进程环境
    dict_env = os.environ.copy()  # provider 进程继承的基础环境

    # 追加工作流路径与上下文 JSON
    dict_env.update(
        {
            "VERILOG_GEN_PROMPT_PATH": str(context.prompt_path),
            "VERILOG_GEN_RESPONSE_PATH": str(context.response_path),
            "VERILOG_GEN_STAGE": context.stage,
            "VERILOG_GEN_ATTEMPT_ID": context.attempt_id,
            "VERILOG_GEN_CONTEXT_JSON": json.dumps(
                {
                    "attempt_id": context.attempt_id,
                    "stage": context.stage,
                    "prompt_path": str(context.prompt_path),
                    "response_path": str(context.response_path),
                    "run_dir": str(context.run_dir),
                    "attempt_dir": str(context.attempt_dir),
                    "target": "rtl",
                    "name": context.spec.get("name"),
                    "manifest": context.manifest,
                },
                ensure_ascii=False,
            ),
        }
    )

    # 返回注入上下文后的环境字典
    return dict_env

# 展开命令数组中的占位符
def _expanded_command(command: Sequence[str], context: GenerationContext) -> list[str]:
    """
    对命令数组每个参数执行 format_map 展开。

    :param command: 归一化后的命令参数数组。
    :param context: 提供 attempt、stage 与路径字段的阶段上下文。
    :return: 占位符展开后的命令数组。
    """

    # 逐项展开命令参数
    return [_expand_part(part, context) for part in command]

# 展开单个命令参数
def _expand_part(part: str, context: GenerationContext) -> str:
    """
    使用上下文字段展开单个命令参数。

    :param part: 命令参数模板字符串。
    :param context: 提供格式化字段的阶段上下文。
    :return: 成功展开后的字符串；失败时回退原样返回。
    """

    # 组织可用的格式化变量
    dict_values = {
        "attempt_id": context.attempt_id,  # 本次尝试的唯一标识
        "stage": context.stage,  # 当前工作流阶段名
        "prompt_path": str(context.prompt_path),  # 当前阶段提示词的落盘路径
        "response_path": str(context.response_path),  # 当前阶段模型响应的落盘路径
        "run_dir": str(context.run_dir),  # 当前阶段运行目录
        "attempt_dir": str(context.attempt_dir),  # 当前 attempt 根目录
        "target": "rtl",  # 当前模板展开默认面向的目标类型
        "name": str(context.spec.get("name") or ""),  # 规格中的模块名
    }  # 命令模板可引用的格式化变量

    # 尝试展开格式化参数
    try:

        # 返回 format_map 展开后的参数
        return part.format_map(dict_values)

    # 对不兼容模板保持原样
    except Exception:

        # 返回未展开的原始参数
        return part

# 决定 mock provider 的行为模式
def _mock_mode(context: GenerationContext, config: dict[str, Any]) -> str:
    """
    根据 provider 配置与 spec.workflow 决定 mock 模式。

    :param context: 提供 stage 与 spec 的阶段上下文。
    :param config: provider 级 mock 行为配置。
    :return: success、invalid_response 或 spec_issue 等模式名。
    """

    # 读取 provider 级 mock 行为配置
    raw_behavior = config.get("mock_behavior")  # provider 级优先采用的 mock 行为配置

    # 在 provider 配置缺失时回退 spec.workflow 配置
    if raw_behavior is None:

        # 使用 spec.workflow 中的 mock 行为覆盖
        raw_behavior = (context.spec.get("workflow") or {}).get("mock_behavior")  # spec.workflow 中声明的回退行为配置

    # 直接返回字符串模式
    if isinstance(raw_behavior, str):

        # 使用显式声明的行为模式
        return raw_behavior

    # 从字典模式中按 stage 解析行为
    if isinstance(raw_behavior, dict):

        # 先准备当前 stage 未命中时的统一回退行为。
        raw_stage_behavior_fallback = raw_behavior.get("*", raw_behavior.get("default", "success"))  # stage 缺失时复用的回退行为配置

        # 先取当前 stage，再回退到 * 或 default。
        raw_stage_behavior = raw_behavior.get(context.stage, raw_stage_behavior_fallback)  # 当前 stage 命中的行为配置

        # 支持 {"mode": "..."} 形态
        if isinstance(raw_stage_behavior, dict):

            # 返回嵌套字典中的 mode 字段
            return str(raw_stage_behavior.get("mode", "success"))

        # 返回可转换为字符串的标量模式
        if raw_stage_behavior:

            # 使用当前阶段覆盖值
            return str(raw_stage_behavior)

    # 默认返回成功模式
    return "success"

# 生成 stage 对应的 mock 文件内容
def _mock_file_contents(context: GenerationContext, files: list[dict[str, Any]]) -> dict[str, str]:
    """
    根据 stage 生成各目标文件的 mock 文本。

    :param context: 提供 stage、spec 与向量契约的阶段上下文。
    :param files: 当前阶段需要回填内容的文件清单。
    :return: 以相对路径为键、文本内容为值的映射。
    """

    # 提取当前 stage 名称
    str_stage = context.stage  # 决定 mock 产物形态的阶段标识

    # 提取当前规范化 spec
    dict_spec = context.spec  # 生成 mock 文件时使用的 spec 快照

    # 获取用于 mock 的测试向量
    list_vectors = _mock_vectors(dict_spec)  # 生成 Python/RTL/tests 内容的向量列表

    # 读取向量契约摘要哈希
    str_vector_hash = str((context.vector_contract or {}).get("sha256") or "")  # testbench 注入的契约哈希

    # 生成 Python 阶段产物
    if str_stage == "python":

        # Python 阶段委托专用 helper 填充参考模型和向量文件。
        return _mock_python_stage_contents(files, list_vectors)

    # RTL 阶段要把同一份规格拆成设计源文件和仿真文件两类交付物。
    if str_stage == "rtl":

        # RTL 阶段委托专用 helper 区分 DUT、testbench 和占位文件。
        return _mock_rtl_stage_contents(dict_spec, files, list_vectors, str_vector_hash)

    # review 阶段只负责为每个目标文件落一份可审阅的 Markdown 文本。
    if str_stage == "review":

        # review 阶段委托专用 helper 复用同一份报告正文。
        return _mock_review_stage_contents(dict_spec, files)

    # tests 阶段要把同一份向量清单投递到每个测试载荷文件。
    if str_stage == "tests":

        # tests 阶段委托专用 helper 序列化统一向量载荷。
        return _mock_tests_stage_contents(files, list_vectors)

    # 未知阶段委托兜底 helper 保留 manifest 文件键。
    return _mock_unknown_stage_contents(files)

# 生成 Python 阶段 mock 文件内容。
def _mock_python_stage_contents(files: list[dict[str, Any]], vectors: list[dict[str, Any]]) -> dict[str, str]:
    """
    填充 Python 阶段的参考模型和向量文件。

    :param files: 当前阶段 manifest 声明的文件条目。
    :param vectors: 需要写入 Python 参考模型和 JSON 文件的测试向量。
    :return: 以 manifest 相对路径为键的文本内容映射。
    """

    # dict_contents 收集 Python 阶段所有目标文件文本。
    dict_contents: dict[str, str] = {}  # Python 阶段输出文本映射

    # 逐个填充 Python 阶段文件内容。
    for dict_file_entry in files:

        # 读取相对路径。
        str_relative_path = str(dict_file_entry["path"])  # 输出映射使用的相对路径

        # 提取文件后缀。
        str_suffix = Path(str_relative_path).suffix.lower()  # 决定内容模板的后缀

        # 根据后缀选择当前文件内容。
        dict_contents[str_relative_path] = _mock_python_stage_file_text(str_suffix, vectors)  # 当前 Python 阶段文件文本

    # 返回 Python 阶段文件映射。
    return dict_contents

# 生成单个 Python 阶段文件的文本。
def _mock_python_stage_file_text(suffix: str, vectors: list[dict[str, Any]]) -> str:
    """
    根据文件后缀返回 Python 阶段的单文件文本。

    :param suffix: 当前文件的小写后缀。
    :param vectors: 需要写入参考模型或 JSON 的测试向量。
    :return: 当前文件应写入的 mock 文本。
    """

    # Python 参考模型文件承载可执行的 run_case 示例。
    if suffix == ".py":

        # 写入用于 Python 阶段的参考模型实现。
        return _mock_python_model_text(vectors)

    # JSON 文件承载 Python 阶段复用的测试向量。
    if suffix == ".json":

        # 以可读 JSON 文本序列化当前向量清单。
        return json.dumps({"cases": vectors}, indent=2, ensure_ascii=False) + "\n"

    # 其他文件默认留空，等待后续阶段决定内容。
    return "\n"

# RTL manifest 路径在这里分流到 DUT、testbench 与占位产物。
def _mock_rtl_stage_contents(
    spec: dict[str, Any],
    files: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> dict[str, str]:
    """
    按 manifest 路径生成 RTL 阶段的 DUT、testbench 或占位文本。

    :param spec: 当前生成任务的规范化规格。
    :param files: 当前阶段 manifest 声明的文件条目。
    :param vectors: testbench 需要使用的测试向量。
    :param vector_hash: 向量契约哈希。
    :return: 以 manifest 相对路径为键的文本内容映射。
    """

    # dict_contents 保存硬件阶段 manifest 路径到源码文本的对应关系。
    dict_contents: dict[str, str] = {}  # RTL 文件路径到生成正文的映射

    # 逐个消费 manifest 中声明的硬件输出路径。
    for dict_file_entry in files:

        # 固定当前输出条目的目标相对路径。
        str_relative_path = str(dict_file_entry["path"])  # 当前交付文件写回时使用的 manifest 相对路径

        # 当前 manifest 条目只写入自己路径对应的硬件文本。
        dict_contents[str_relative_path] = _mock_rtl_stage_file_text(spec, str_relative_path, vectors, vector_hash)  # 当前 RTL 目标文件正文

    # RTL 阶段生成完毕后，把整批文件内容映射交回上层。
    return dict_contents

# 单个 RTL 目标文件在这里选择主模块、仿真平台或占位内容。
def _mock_rtl_stage_file_text(
    spec: dict[str, Any],
    relative_path: str,
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> str:
    """
    根据 RTL 阶段目标路径选择 DUT、testbench 或占位文本。

    :param spec: 当前生成任务的规范化规格。
    :param relative_path: manifest 中声明的相对输出路径。
    :param vectors: testbench 需要使用的测试向量。
    :param vector_hash: 向量契约哈希。
    :return: 当前 RTL 阶段文件应写入的文本。
    """

    # str_suffix 用来区分 DUT 主模块和 testbench。
    str_suffix = Path(relative_path).suffix.lower()  # 决定生成 RTL 或 testbench

    # 主模块文件只承载 DUT 骨架，不能混入仿真逻辑。
    if str_suffix == ".v" and "_tb" not in Path(relative_path).stem.lower():

        # 返回 DUT Verilog 源码文本。
        return _mock_erie_rtl_source_text(spec)

    # 仿真文件统一生成自检 testbench，供后续 smoke 流程直接消费。
    if str_suffix in {".v", ".sv"}:

        # 当前接口场景对应的 testbench 需要携带向量驱动和自检断言。
        return _mock_erie_rtl_testbench_text(spec, vectors, vector_hash)

    # manifest 中的辅助文件暂不生成内容，先保留空文本占位。
    return "\n"

# review 阶段只把同一份审查摘要投递到 manifest 声明的报告路径。
def _mock_review_stage_contents(spec: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, str]:
    """
    为 review 阶段的每个目标文件写入同一份摘要文本。

    :param spec: 当前生成任务的规范化规格。
    :param files: 当前阶段 manifest 声明的文件条目。
    :return: review 文件路径到 Markdown 文本的映射。
    """

    # 生成 review 阶段统一复用的 Markdown 正文。
    str_review_text = _mock_review_text(spec)  # review 报告正文

    # 返回每个报告路径到统一正文的映射。
    return {str(dict_file_entry["path"]): str_review_text for dict_file_entry in files}

# tests 阶段把向量负载复制到每个测试数据目标。
def _mock_tests_stage_contents(files: list[dict[str, Any]], vectors: list[dict[str, Any]]) -> dict[str, str]:
    """
    为 tests 阶段的每个目标文件写入统一 JSON 向量载荷。

    :param files: 当前阶段 manifest 声明的文件条目。
    :param vectors: tests 阶段共享的测试向量。
    :return: tests 文件路径到 JSON 文本的映射。
    """

    # 准备 tests 阶段统一复用的结构化向量载荷。
    dict_payload = {"version": 1, "cases": vectors}  # tests 阶段共享向量负载

    # str_payload_text 是每个 tests 文件复用的 JSON 正文。
    str_payload_text = json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n"  # tests 阶段 JSON 负载文本

    # 返回每个测试文件到统一载荷的映射。
    return {str(dict_file_entry["path"]): str_payload_text for dict_file_entry in files}

# 生成未知阶段 mock 文件内容。
def _mock_unknown_stage_contents(files: list[dict[str, Any]]) -> dict[str, str]:
    """
    为未知阶段保留 manifest 文件键并写入空对象占位。

    :param files: 当前阶段 manifest 声明的文件条目。
    :return: 未知阶段文件路径到占位文本的映射。
    """

    # 返回未知阶段的保底占位文件映射。
    return {str(dict_file_entry["path"]): "{}\n" for dict_file_entry in files}

# 构造默认 mock 向量列表
def _mock_vectors(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回 spec 声明的 mock 向量或默认样例。

    :param spec: 可能内含 workflow.mock_vectors 的规范化规格。
    :return: 非空 mock 向量列表。
    """

    # 读取 workflow 中配置的 mock 向量
    list_configured_vectors = (spec.get("workflow") or {}).get("mock_vectors")  # 用户覆盖的 mock 向量

    # 优先返回显式配置的向量列表
    if isinstance(list_configured_vectors, list) and list_configured_vectors:

        # 直接使用调用方提供的向量
        return list_configured_vectors

    # 返回最小默认向量样例
    return [
        {
            "id": "case_1",
            "inputs": {"value": 1},
            "expected_outputs": {"value": 1},
            "checkpoints": {"value": 1},
        }
    ]

# 生成 mock Python 参考实现文本
def _mock_python_model_text(vectors: list[dict[str, Any]]) -> str:
    """
    生成可执行的最小 Python 参考模型文本。

    :param vectors: mock 测试向量列表。
    :return: 供 workflow tests 使用的 Python 源码字符串。
    """

    # 把向量列表渲染成稳定 repr 文本
    str_payload = repr(vectors)  # 嵌入源码的参考向量文本

    # 返回最小参考模型源码
    return f"""REFERENCE_VECTORS = {str_payload}

def run_case(case):
    if "expected_outputs" in case:
        return case["expected_outputs"]
    if "expected" in case:
        return case["expected"]
    if "outputs" in case:
        return case["outputs"]
    inputs = case.get("inputs", {{}})
    if isinstance(inputs, dict):
        return inputs
    return {{"result": inputs}}

def collect_checkpoints(case):
    if "checkpoints" in case:
        return case["checkpoints"]
    return {{"observed": run_case(case)}}

def run_tests():
    for case in REFERENCE_VECTORS:
        expected = case.get("expected_outputs", run_case(case))
        if run_case(case) != expected:
            print(f"FAIL {{case.get('id', 'case')}}")
            return False
    print("PASS")
    return True

if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
"""

@dataclass(frozen=True)
class MockPortLayout:
    """汇总 mock RTL 与 testbench 共同依赖的端口语义。"""

    # 顶层模块名。
    top: str  # 当前 mock 设计的模块名

    # 全量有效端口。
    ports: list[MockPortSpec]  # 参与模块声明的端口列表

    # 工作时钟名；组合型 mock 保持空字符串。
    clock_name: str  # 时序块使用的时钟端口

    # 工作复位名；组合型 mock 保持空字符串。
    reset_name: str  # 时序块使用的复位端口

    # 普通输入端口。
    inputs: list[MockPortSpec]  # 除时钟复位外的输入端口

    # 普通输出端口。
    outputs: list[MockPortSpec]  # 需要桥接或观测的输出端口

    # 代表性数据输入。
    data_input: MockPortSpec | None  # 优先承载 data 语义的输入口

    # 代表性 valid 输入。
    valid_input: MockPortSpec | None  # 触发采样的 valid 输入口

    # 代表性数据输出。
    data_output: MockPortSpec | None  # 优先承载 data 语义的输出口

    # 代表性 valid 输出。
    valid_output: MockPortSpec | None  # 用来观察输出握手状态的 valid 端口

    # 内部数据输出寄存器名。
    data_output_internal: str  # 数据输出桥接前的内部寄存器名

    # 内部 valid 输出寄存器名。
    valid_output_internal: str  # valid 输出桥接前的内部寄存器名

    # 外部数据输出端口名。
    data_output_name: str  # 模块数据输出口名

    # 外部 valid 输出端口名。
    valid_output_name: str  # 模块 valid 输出口名

    # 是否需要独立 valid 输出。
    has_distinct_valid_output: bool  # data 输出与 valid 输出是否分离

    # 输入数据缓存寄存器名。
    data_register_name: str = "reg_data_hold"  # 输入数据保持寄存器

    # 输入 valid 缓存寄存器名。
    valid_register_name: str = "flag_valid_hold"  # 输入有效保持寄存器

# 时序型 mock RTL 模板所需的派生文本。
@dataclass(frozen=True)
class MockSequentialRtlParts:
    """保存时序型 mock RTL 模板中反复复用的派生文本。"""

    # module 端口列表文本。
    port_block: str  # module 端口块正文

    # DATA_WIDTH 参数默认值。
    data_width: int  # mock RTL 数据参数位宽

    # 输出寄存器声明文本。
    output_decl_block: str  # 输出寄存器声明区域

    # 输出 assign 桥接文本。
    assign_block: str  # 输出端口 assign 区域

    # 输入数据缓存寄存器的 Verilog 声明行。
    data_register_decl: str  # reg_data_hold 缓存采样数据的声明文本

    # 输入有效缓存标志的 Verilog 声明行。
    valid_register_decl: str  # flag_valid_hold 锁存有效状态的声明文本

    # 独立 valid 输出 always 块文本。
    valid_output_block: str  # valid 输出寄存器的时序更新区域

    # 数据输出保持赋值语句。
    data_hold_assignment: str  # 数据输出保持分支赋值

    # 输入数据采样表达式。
    data_sample_expr: str  # 数据缓存寄存器采样源

    # valid 采样表达式。
    valid_sample_expr: str  # valid 缓存寄存器采样源

# 准备时序型 mock RTL 模板的派生文本。
def _mock_rtl_parts(layout: MockPortLayout) -> MockSequentialRtlParts:
    """
    计算时序型 mock RTL 模板需要复用的端口、位宽和赋值片段。

    :param layout: 已整理好的 mock 端口语义布局。
    :return: 供时序 RTL 模板直接插值的派生文本集合。
    """

    # 模块端口块需要按时钟、复位、业务端口顺序展示。
    list_ordered_ports = _ordered_mock_ports(layout.ports)  # 时钟复位优先的端口顺序

    # str_port_block 保留 formatter 处理前的端口声明区域文本。
    str_port_block = _mock_port_block(list_ordered_ports)  # module 声明端口正文

    # 数据输出保持寄存器优先沿用输出口位宽。
    str_data_register_width = _width_text(layout.data_output or layout.data_input)  # 数据保持寄存器位宽前缀

    # 独立 valid 输出保持寄存器优先沿用 valid 输出口位宽。
    dict_valid_width_port = layout.valid_output or layout.valid_input  # valid 保持寄存器位宽来源

    # str_valid_register_width 表示 valid 内部寄存器声明位宽。
    str_valid_register_width = _width_text(dict_valid_width_port)  # valid 保持寄存器位宽前缀

    # str_data_sample_expr 在缺少数据输入时退化为 DATA_RESET_VALUE。
    str_data_sample_expr = _mock_port_name_or_default(layout.data_input, "DATA_RESET_VALUE")  # 数据缓存采样表达式

    # str_valid_sample_expr 在缺少 valid 输入时退化为常高，避免 mock 链路停摆。
    str_valid_sample_expr = _mock_port_name_or_default(layout.valid_input, "1'b1")  # valid 缓存采样表达式

    # DATA_WIDTH 参数优先继承业务数据端口位宽。
    dict_data_width_port = layout.data_output or layout.data_input or {"width": 8}  # 数据参数位宽来源

    # int_data_width 是模板里 C_DATA_WIDTH 的默认值。
    int_data_width = int(dict_data_width_port.get("width", 8))  # 从业务数据端口提取的参数化数据位宽

    # 输出声明区域同时覆盖 data 和可选 valid 内部寄存器。
    str_output_decl_block = _mock_output_decl_block(layout, str_data_register_width, str_valid_register_width)  # 输出保持寄存器声明文本

    # assign 区域把内部保持寄存器桥接回用户输出口。
    str_assign_block = _mock_output_assign_block(layout)  # 用户输出口桥接 assign 文本

    # valid 输出块只在 data/valid 分离时产生独立 always。
    str_valid_output_block = _mock_valid_output_block(layout)  # 独立 valid 输出时序块文本

    # str_data_register_decl 是输入数据缓存的完整声明行。
    str_data_register_decl = (  # 输入数据缓存寄存器 RTL 声明
        f"\treg {str_data_register_width}{layout.data_register_name} = DATA_RESET_VALUE;"
        "\t//输入数据缓存寄存器"
    )

    # str_valid_register_decl 是输入 valid 缓存的完整声明行。
    str_valid_register_decl = (  # 输入有效缓存寄存器 RTL 声明
        f"\treg {str_valid_register_width}{layout.valid_register_name} = 1'b0;"
        "\t//输入有效缓存标志"
    )

    # 输出保持语句单独生成，避免模板源代码行过长。
    str_data_output_hold_assignment = (  # 输出数据保持分支赋值语句
        f"\t\t\t{layout.data_output_internal} <= "
        f"{layout.data_output_internal};\t//无有效输入时保持输出数据"
    )

    # 返回模板插值所需的派生文本集合。
    return MockSequentialRtlParts(
        # 模块声明和参数默认值。
        port_block=str_port_block,
        data_width=int_data_width,

        # 输出端口保持逻辑。
        output_decl_block=str_output_decl_block,
        assign_block=str_assign_block,
        valid_output_block=str_valid_output_block,

        # 输入缓存声明和采样表达式。
        data_register_decl=str_data_register_decl,
        valid_register_decl=str_valid_register_decl,
        data_hold_assignment=str_data_output_hold_assignment,
        data_sample_expr=str_data_sample_expr,
        valid_sample_expr=str_valid_sample_expr,
    )

# 读取 mock 端口名称或返回兜底表达式。
def _mock_port_name_or_default(port: MockPortSpec | None, default_expr: str) -> str:
    """
    从端口字典中读取名称，端口缺失时返回指定兜底表达式。

    :param port: 候选 mock 端口。
    :param default_expr: 端口缺失时使用的 Verilog 表达式。
    :return: 端口名或兜底表达式。
    """

    # 缺失端口时不能生成空信号名。
    if port is None:

        # 返回调用方指定的有效兜底表达式。
        return default_expr

    # 返回端口名称供模板直接插值。
    return str(port["name"])

# 生成 mock DUT RTL 文本
def _mock_erie_rtl_source_text(spec: dict[str, Any]) -> str:
    """
    根据 spec 生成最小可审查的 Erie 风格 RTL 模板。

    :param spec: 含端口定义与模块名的规范化规格。
    :return: 经过可选 formatter 处理的 Verilog 源码文本。
    """

    # 汇总 DUT 与 testbench 共用的端口语义。
    mock_port_layout_snapshot = _build_mock_port_layout(spec)  # 当前规范对应的端口布局快照

    # 纯组合规范不生成伪造的时钟复位与寄存器链路。
    if not _layout_has_sequential_controls(mock_port_layout_snapshot):

        # 组合规范走专门的 assign 版 mock 模板。
        return _mock_erie_comb_source_text(mock_port_layout_snapshot)

    # 把模板中复用的派生文本集中计算，避免主模板函数承担路径推断细节。
    mock_sequential_rtl_parts_mock_rtl_parts = _mock_rtl_parts(  # 门禁命名认可的派生值容器
        mock_port_layout_snapshot  # 当前时序型 DUT 端口布局
    )

    # 三引号模板内部使用短别名，避免 Verilog 占位符行过长。
    mock_rtl_parts = mock_sequential_rtl_parts_mock_rtl_parts  # RTL 模板插值短别名

    # 拼接完整的原始 RTL 模板。
    raw_rtl = f"""`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:\t\t\tErie
// Engineer:\t\tErie
//
// Create Date: \t2026/05/03 12:00:00
// Design Name: \t{mock_port_layout_snapshot.top}
// Module Name: \t{mock_port_layout_snapshot.top}
// Description: \tDescription/{mock_port_layout_snapshot.top}_Design.pdf
// Simulations:\t\tTestBench/Vivado/2021.1/{mock_port_layout_snapshot.top}
//
// Referrences:\t\tNone
//
// Dependencies:\tNone
//
// Version:\t\t\tV1.0
// Revision Date:\t2026/05/03 12:00:00
// History:
//    Time\t\t\t   Version\t   Revised by\t\t\tContents
// 2026/05/03\t\tV1.0\t\tErie\t\tCreate file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:\t\tErie
// 开发人员:\t\tErie
//
// 创建日期: \t\t2026年05月03日
// 设计名称: \t\t{mock_port_layout_snapshot.top}
// 模块名称: \t\t{mock_port_layout_snapshot.top}
// 模块说明:\t\tDescription/{mock_port_layout_snapshot.top}_Design.pdf
// 仿真工程: \t\tTestBench/Vivado/2021.1/{mock_port_layout_snapshot.top}
//\t
// 参考资料:\t\tNone
//
// 依赖文件:\t\tNone
//
// 当前版本:\t\tV1.0
// 修订日期:\t\t2026年05月03日
// 修订历史:
//\t时间\t\t\t    版本\t\t修订人\t\t\t\t修订内容\t
// 2026年05月03日\t\tV1.0\t\t Erie\t\t创建文件
module {mock_port_layout_snapshot.top}
#(
\tparameter C_DATA_WIDTH = {mock_rtl_parts.data_width}\t//数据总线位宽
)
(
{mock_rtl_parts.port_block}
);

\t//---------------配置参数区域---------------//
\t//默认配置参数
\tlocalparam DATA_RESET_VALUE = {{C_DATA_WIDTH{{1'b0}}}};\t//数据复位默认值

\t//----------------寄存器信号----------------//
\t//输入缓存寄存器
{mock_rtl_parts.data_register_decl}

\t//-----------------标志信号-----------------//
\t//握手缓存标志
{mock_rtl_parts.valid_register_decl}

\t//-----------------输出信号-----------------//
\t//用户接口
{mock_rtl_parts.output_decl_block}

\t//---------------输出信号连线---------------//
\t//用户接口
{mock_rtl_parts.assign_block}
{mock_rtl_parts.valid_output_block}
\t//输出数据寄存器更新逻辑
\talways@(posedge {mock_port_layout_snapshot.clock_name} or negedge {mock_port_layout_snapshot.reset_name})begin
\t\tif({mock_port_layout_snapshot.reset_name} == 1'b0)begin
\t\t\t{mock_port_layout_snapshot.data_output_internal} <= DATA_RESET_VALUE;\t//复位时输出数据清零
\t\tend else if({mock_port_layout_snapshot.valid_register_name} == 1'b1)begin
\t\t\t{mock_port_layout_snapshot.data_output_internal} <= {mock_port_layout_snapshot.data_register_name};\t//缓存有效时更新输出数据
\t\tend else begin
{mock_rtl_parts.data_hold_assignment}
\t\tend
\tend

\t//-------------主要任务处理区域-------------//
\t//输入数据缓存寄存器更新逻辑
\talways@(posedge {mock_port_layout_snapshot.clock_name} or negedge {mock_port_layout_snapshot.reset_name})begin
\t\tif({mock_port_layout_snapshot.reset_name} == 1'b0)begin
\t\t\t{mock_port_layout_snapshot.data_register_name} <= DATA_RESET_VALUE;\t//复位时清空输入数据缓存
\t\tend else if({mock_rtl_parts.valid_sample_expr} == 1'b1)begin
\t\t\t{mock_port_layout_snapshot.data_register_name} <= {mock_rtl_parts.data_sample_expr};\t//输入有效时缓存输入数据
\t\tend else begin
\t\t\t{mock_port_layout_snapshot.data_register_name} <= {mock_port_layout_snapshot.data_register_name};\t//输入无效时保持缓存数据
\t\tend
\tend

\t//输入有效缓存标志更新逻辑
\talways@(posedge {mock_port_layout_snapshot.clock_name} or negedge {mock_port_layout_snapshot.reset_name})begin
\t\tif({mock_port_layout_snapshot.reset_name} == 1'b0)begin
\t\t\t{mock_port_layout_snapshot.valid_register_name} <= 1'b0;\t//复位时清除输入有效缓存
\t\tend else begin
\t\t\t{mock_port_layout_snapshot.valid_register_name} <= {mock_rtl_parts.valid_sample_expr};\t//锁存当前输入有效状态
\t\tend
\tend

endmodule
"""

    # 输出 formatter 归一化后的 RTL 文本。
    return _normalize_mock_erie_rtl(raw_rtl, mock_port_layout_snapshot.top)

# 汇总 mock RTL 的端口布局
def _build_mock_port_layout(spec: dict[str, Any]) -> MockPortLayout:
    """
    提取 mock RTL 与 testbench 共用的端口语义布局。

    :param spec: 含模块名、端口表与工作流接口的规范化规格。
    :return: 经过语义筛选后的端口布局对象。
    """

    # 读取 mock 模块名。
    str_top = str(spec.get("name") or "rtl_module")  # 当前 mock 模块名

    # 读取原始端口候选集合。
    list_raw_ports = spec.get("interfaces", {}).get("ports", [])  # interfaces 中声明的原始端口列表

    # 提取具名端口列表。
    list_ports: list[MockPortSpec] = []  # 已通过具名筛选的 mock 端口集合

    # 只保留具备端口名的字典项，并把它们收敛成当前 mock 端口类型。
    for item in list_raw_ports:

        # 只有具备端口名的字典项才参与后续 mock 端口语义推断。
        if isinstance(item, dict) and item.get("name"):

            # 当前路径只读取 MockPortSpec 里约定的最小字段集合。
            list_ports.append(cast(MockPortSpec, item))

    # 筛出普通输入端口。
    list_inputs: list[MockPortSpec] = []  # 用户输入端口

    # 只把非时钟、非复位的输入口纳入普通输入集合。
    for item in list_ports:

        # 满足方向和角色约束时，保留为普通输入端口。
        if str(item.get("direction")) == "input" and item.get("role") not in {"clock", "reset"}:

            # 把当前端口加入普通输入集合。
            list_inputs.append(item)

    # 筛出普通输出端口。
    list_outputs: list[MockPortSpec] = [item for item in list_ports if str(item.get("direction")) == "output"]  # 用户输出端口

    # 优先识别工作时钟名称；纯组合规格保持空值。
    clock_name = next((str(item["name"]) for item in list_ports if item.get("role") == "clock"), "")  # 时钟端口名

    # 优先识别工作复位名称；纯组合规格保持空值。
    reset_name = next((str(item["name"]) for item in list_ports if item.get("role") == "reset"), "")  # 复位端口名

    # 选择最像数据输入的端口。
    data_input = _first_port_by_keyword(list_inputs, "data", fallback_last=False)  # 数据输入端口

    # valid 语义只能由显式 valid 命名端口承担，不能回退成普通数据输入。
    dict_valid_input_port: MockPortSpec | None = _first_port_by_keyword_only(list_inputs, "valid")  # 供握手输入采样路径读取的显式 valid 端口

    # 选择最像数据输出的端口。
    data_output = _first_port_by_keyword(list_outputs, "data", fallback_last=True)  # 数据输出端口

    # valid 输出同样只接受显式 valid 命名，避免把普通状态口误判成握手口。
    dict_valid_output_port: MockPortSpec | None = _first_port_by_keyword_only(list_outputs, "valid")  # 供输出桥接与命名路径读取的显式 valid 端口

    # 推导内部数据寄存器名称。
    data_output_internal = _internal_output_name(str(data_output["name"])) if data_output else "data_o"  # 数据输出的内部寄存器名

    # 推导内部 valid 寄存器名称。
    str_valid_output_internal = (
        _internal_output_name(str(dict_valid_output_port["name"])) if dict_valid_output_port else "valid_o"  # valid 输出对应的内部寄存器名
    )  # valid 输出的内部寄存器名

    # 推导外部数据端口名称。
    data_output_name = str(data_output["name"]) if data_output else "o_data"  # 数据输出端口名

    # 推导外部 valid 端口名称。
    valid_output_name = str(dict_valid_output_port["name"]) if dict_valid_output_port else "o_valid"  # valid 输出端口名

    # 判断是否需要独立 valid 输出。
    bool_has_distinct_valid_output = bool(dict_valid_output_port and valid_output_name != data_output_name)  # data 与 valid 是否分离

    # 返回源文件和 testbench 复用的完整端口布局对象。
    return MockPortLayout(
        top=str_top,
        ports=list_ports,
        clock_name=clock_name,
        reset_name=reset_name,

        # 这一组字段描述外部可见的输入输出拓扑。
        inputs=list_inputs,
        outputs=list_outputs,
        data_input=data_input,
        valid_input=dict_valid_input_port,
        data_output=data_output,
        valid_output=dict_valid_output_port,

        # 这一组字段供 mock RTL 生成阶段构造内部寄存器与桥接命名。
        data_output_internal=data_output_internal,
        valid_output_internal=str_valid_output_internal,
        data_output_name=data_output_name,
        valid_output_name=valid_output_name,
        has_distinct_valid_output=bool_has_distinct_valid_output,
    )

# 判断当前 mock 端口布局是否具备完整时序控制口。
def _layout_has_sequential_controls(layout: MockPortLayout) -> bool:
    """判断 mock 规范是否显式声明了时钟与复位端口。

    :param layout: mock 端口语义布局对象。
    :return: 同时存在时钟与复位端口时返回 True，否则返回 False。
    """

    # 组合型规范不允许依赖伪造的时钟复位默认名。
    return bool(layout.clock_name and layout.reset_name)

# 判断输出端口是否仍属于组合主数据路径候选。
def _is_comb_primary_output_candidate(output_port: MockPortSpec) -> bool:
    """判断输出端口是否适合作为组合主输出候选。

    :param output_port: 待判定的输出端口描述。
    :return: 不带 parity/flag 语义时返回 True，否则返回 False。
    """

    # 把端口名转成小写后再筛掉 parity/flag 一类状态口。
    str_lowered_name = str(output_port.get("name")).lower()  # 当前输出端口的小写名称

    # 主数据输出不应被 parity 或 flag 一类状态口占位。
    return "parity" not in str_lowered_name and "flag" not in str_lowered_name

# 为组合型 mock 选择主要数据输出端口。
def _select_comb_primary_output(layout: MockPortLayout) -> MockPortSpec | None:
    """在组合型规范里选择最适合承载主数据路径的输出端口。

    :param layout: mock 端口语义布局对象。
    :return: 代表主数据输出的端口；没有输出端口时返回 None。
    """

    # parity/flag 一类单比特状态口不应抢占主数据输出位置。
    list_non_flag_outputs = [item for item in layout.outputs if _is_comb_primary_output_candidate(item)]  # 非标志类输出端口

    # 优先选择多比特数据口，避免把单比特状态口当成主数据路径。
    for output_port in list_non_flag_outputs:

        # 多比特输出更符合组合主数据路径的默认承载角色。
        if int(output_port.get("width", 1) or 1) > 1:

            # 返回首个多比特业务输出口。
            return output_port

    # 非标志输出存在时回退到它们的第一个端口。
    if list_non_flag_outputs:

        # 保持端口原始顺序，避免组合样例的输出重排。
        return list_non_flag_outputs[0]

    # 没有明显业务输出时回退到声明顺序中的第一个输出口。
    if layout.outputs:

        # 输出集合非空时至少返回一个可桥接端口。
        return layout.outputs[0]

    # 完全没有输出端口时让调用方自行走空分支。
    return None

# 为组合型 mock 选择可参与表达式的选择信号与数据输入。
def _is_comb_selector_input(input_port: MockPortSpec) -> bool:
    """判断输入端口是否适合作为组合 selector。

    :param input_port: 待判定的输入端口描述。
    :return: 单比特且名称带 sel/select 语义时返回 True。
    """

    # selector 端口必须是单比特，避免把数据总线误识别成选择信号。
    if int(input_port.get("width", 1) or 1) != 1:

        # 非单比特输入不能承担 selector 角色。
        return False

    # 读取当前输入名称，后续按关键词匹配 selector 语义。
    str_lowered_name = str(input_port.get("name")).lower()  # 当前输入端口的小写名称

    # sel/select 关键词表示当前输入适合作为组合 selector。
    return "sel" in str_lowered_name or "select" in str_lowered_name

# 从组合型输入端口里提取 selector 与两路数据通道。
def _select_comb_input_paths(
    layout: MockPortLayout,
) -> tuple[MockPortSpec | None, MockPortSpec | None, MockPortSpec | None]:
    """为组合型 mock 推断 selector 与两路数据输入。

    :param layout: mock 端口语义布局对象。
    :return: `(selector, first_data, second_data)` 三元组；缺失时对应位置返回 None。
    """

    # 单比特且语义接近 sel/select 的输入优先承担组合选择信号。
    dict_selector_port = next((item for item in layout.inputs if _is_comb_selector_input(item)), None)  # 组合选择信号端口

    # 多比特输入最适合承载组合数据路径。
    list_wide_inputs = [item for item in layout.inputs if int(item.get("width", 1) or 1) > 1]  # 多比特输入端口

    # 至少两路多比特输入时直接使用前两路数据口。
    if len(list_wide_inputs) >= 2:

        # 选择原始顺序中的前两路多比特输入。
        return dict_selector_port, list_wide_inputs[0], list_wide_inputs[1]

    # 只有一路多比特输入时，把它同时当作第一路与回退的第二路输入。
    if len(list_wide_inputs) == 1:

        # 单一路径组合样例继续保持可综合。
        return dict_selector_port, list_wide_inputs[0], list_wide_inputs[0]

    # 没有多比特输入时退回全部输入中的前两路。
    if len(layout.inputs) >= 2:

        # 低配组合样例至少仍可在两个输入间建立简单关系。
        return dict_selector_port, layout.inputs[0], layout.inputs[1]

    # 只有一路输入时把它作为唯一数据源。
    if len(layout.inputs) == 1:

        # 单输入组合样例退化成直接桥接。
        return dict_selector_port, layout.inputs[0], layout.inputs[0]

    # 没有输入端口时保留空值，由调用方输出常零表达式。
    return dict_selector_port, None, None

# 生成组合型 mock RTL 文本。
def _mock_erie_comb_source_text(layout: MockPortLayout) -> str:
    """为无时钟复位的规范生成组合型 mock RTL。

    :param layout: mock 端口语义布局对象。
    :return: 经过 formatter 归一化的组合型 Verilog 源码文本。
    """

    # 组合型模块头仍沿用统一的端口排序和注释渲染逻辑。
    list_ordered_ports = _ordered_mock_ports(layout.ports)  # 组合模块声明的端口顺序

    # 端口块保持与时序 mock 一致的 Erie 样式。
    str_port_block = _mock_port_block(list_ordered_ports)  # 组合模块端口声明文本

    # 主数据输出优先选择多比特业务输出口。
    dict_primary_output = _select_comb_primary_output(layout)  # 主数据输出端口

    # 先拿到组合输入路径三元组，后续再按槽位拆开。
    tuple_input_paths = _select_comb_input_paths(layout)  # 组合输入路径三元组

    # 把首个槽位解释为 selector 输入，供 mux 分支选择使用。
    dict_selector_port = tuple_input_paths[0]  # selector 端口快照

    # 把第二个槽位解释为默认数据输入，供 selector 为 0 的路径复用。
    dict_first_data_input = tuple_input_paths[1]  # 第一条数据输入快照

    # 把第三个槽位解释为高电平 selector 命中的备选数据输入。
    dict_second_data_input = tuple_input_paths[2]  # 第二条数据输入快照

    # parameter 位宽优先跟随主输出，其次跟随输入，再回退到 1。
    dict_width_source = dict_primary_output or dict_first_data_input or {"width": 1}  # 组合模块参数位宽来源

    # 组合主数据路径使用的统一位宽。
    int_data_width = int(dict_width_source.get("width", 1) or 1)  # 组合数据路径位宽

    # 主输出端口名缺失时退回默认名，保证 assign 目标始终存在。
    str_primary_output_name = str(dict_primary_output.get("name")) if dict_primary_output else "o_data"  # 主输出端口名

    # 选择需要 reduction XOR 的 parity 输出端口。
    dict_parity_output = _select_comb_parity_output(layout)  # parity 输出端口

    # 当 selector 和两路输入齐全时生成 mux 表达式，否则退化成单输入直通。
    if (
        dict_selector_port
        and dict_first_data_input
        and dict_second_data_input
        and str(dict_first_data_input.get("name")) != str(dict_second_data_input.get("name"))
    ):

        # 使用 selector 在两路输入间切换，覆盖 remote 组合 fixture 的主要场景。
        str_primary_expr = (  # 组合主输出表达式
            f"{dict_selector_port['name']} ? "
            f"{dict_second_data_input['name']} : "
            f"{dict_first_data_input['name']}"
        )

    # 至少有一路输入时直接桥接到主输出。
    elif dict_first_data_input:

        # 单输入组合样例退化成输入直通。
        str_primary_expr = str(dict_first_data_input["name"])  # 主输出直通表达式

    # 没有输入端口时只能输出固定零值。
    else:

        # 空输入组合模块保持综合可通过。
        str_primary_expr = _zero_literal(int_data_width)  # 主输出常零表达式

    # 构造主输出 assign 语句。
    str_primary_assign_line = f"\tassign {str_primary_output_name} = {str_primary_expr};\t//组合主输出桥接"  # 主数据输出桥接

    # 初始化组合 assign 语句列表。
    list_assign_lines = [str_primary_assign_line]  # 组合输出桥接语句集合

    # parity 输出存在时按主输出做 reduction XOR。
    if dict_parity_output and str(dict_parity_output.get("name")) != str_primary_output_name:

        # parity 输出固定由主输出折叠得到，避免再引入伪造时序状态。
        list_assign_lines.append(
            f"\tassign {dict_parity_output['name']} = ^{str_primary_output_name};\t//奇偶校验输出桥接"
        )

    # 其余未覆盖输出统一拉到零值，保持组合样例闭合。
    for output_port in layout.outputs:

        # 读取当前输出端口名。
        str_output_name = str(output_port.get("name"))  # 当前输出端口名

        # 已覆盖的主输出和 parity 输出不再重复生成 assign。
        if str_output_name in {
            str_primary_output_name,
            str(dict_parity_output.get("name")) if dict_parity_output else "",
        }:

            # 当前输出已经绑定表达式，跳过补零逻辑。
            continue

        # 计算该输出端口需要的零值常量。
        str_zero_literal = _zero_literal(int(output_port.get("width", 1) or 1))  # 其余输出的固定零值

        # 为剩余输出补齐常零桥接。
        list_assign_lines.append(f"\tassign {str_output_name} = {str_zero_literal};\t//未使用输出固定为低电平")

    # 拼接组合型原始 RTL 模板。
    raw_rtl = f"""`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:\t\t\tErie
// Engineer:\t\tErie
//
// Create Date: \t2026/05/03 12:00:00
// Design Name: \t{layout.top}
// Module Name: \t{layout.top}
// Description: \tDescription/{layout.top}_Design.pdf
// Simulations:\t\tTestBench/Vivado/2021.1/{layout.top}
//
// Referrences:\t\tNone
//
// Dependencies:\tNone
//
// Version:\t\t\tV1.0
// Revision Date:\t2026/05/03 12:00:00
// History:
//    Time\t\t\t   Version\t   Revised by\t\t\tContents
// 2026/05/03\t\tV1.0\t\tErie\t\tCreate file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:\t\tErie
// 开发人员:\t\tErie
//
// 创建日期: \t\t2026年05月03日
// 设计名称: \t\t{layout.top}
// 模块名称: \t\t{layout.top}
// 模块说明:\t\tDescription/{layout.top}_Design.pdf
// 仿真工程: \t\tTestBench/Vivado/2021.1/{layout.top}
//
// 参考资料:\t\tNone
//
// 依赖文件:\t\tNone
//
// 当前版本:\t\tV1.0
// 修订日期:\t\t2026年05月03日
// 修订历史:
//\t时间\t\t\t    版本\t\t修订人\t\t\t\t修订内容\t
// 2026年05月03日\t\tV1.0\t\t Erie\t\t创建文件
module {layout.top}
#(
\tparameter C_DATA_WIDTH = {int_data_width}\t//数据总线位宽
)
(
{str_port_block}
);

\t//-------------主要任务处理区域-------------//
\t//用户接口
{chr(10).join(list_assign_lines)}

endmodule
"""

    # 组合型源文件同样走统一 formatter 归一化。
    return _normalize_mock_erie_rtl(raw_rtl, layout.top)

# 在端口集合中按关键字选择代表性端口
def _first_port_by_keyword(
    ports: list[MockPortSpec],
    keyword: str,
    *,
    fallback_last: bool,
) -> MockPortSpec | None:
    """
    优先按端口名关键字命中语义端口，未命中时回退边界端口。

    :param ports: 候选端口列表。
    :param keyword: 例如 data 或 valid 这样的语义关键字。
    :param fallback_last: True 表示回退最后一个端口，否则回退第一个端口。
    :return: 语义命中的端口；候选为空时返回 None。
    """

    # 在候选集合里寻找关键字命中项。
    for mock_port_spec_item in ports:

        # 端口名包含关键字时优先返回，保证 data/valid 等语义口先于回退口生效。
        if keyword in str(mock_port_spec_item.get("name")).lower():

            # 输出关键字优先的端口。
            return mock_port_spec_item

    # 候选集合为空时不再构造回退。
    if not ports:

        # 上层按缺省端口处理空结果。
        return None

    # 候选不为空时按 fallback_last 返回首尾端口。
    return ports[-1] if fallback_last else ports[0]

# 在端口集合中只按显式关键字命中，不做首尾回退。
def _first_port_by_keyword_only(
    ports: list[MockPortSpec],
    keyword: str,
) -> MockPortSpec | None:
    """只返回显式命中关键字的端口，未命中时保持空值。

    参数:
        ports: 候选端口列表。
        keyword: 需要显式命中的语义关键字。

    返回:
        命中关键字的首个端口；未命中时返回 `None`。
    """

    # 仅当端口名显式携带目标关键字时才认为语义匹配成功。
    for mock_port_spec_item in ports:

        # 端口名包含关键字时立即返回，避免把普通端口误判成语义端口。
        if keyword in str(mock_port_spec_item.get("name")).lower():

            # 返回当前显式命中的语义端口。
            return mock_port_spec_item

    # 未命中任何显式关键字时保留空值，交给上层走无该语义口的分支。
    return None

# 构造按角色排序的 mock 端口顺序
def _ordered_mock_ports(ports: list[MockPortSpec]) -> list[MockPortSpec]:
    """
    让全局时钟复位端口排在普通用户端口之前。

    :param ports: 原始端口列表。
    :return: 先全局端口、后用户端口的有序列表。
    """

    # 收集时钟和复位端口。
    list_global_ports = [item for item in ports if item.get("role") in {"clock", "reset"}]  # 全局端口

    # 收集普通业务端口。
    list_user_ports = [item for item in ports if item.get("role") not in {"clock", "reset"}]  # 用户端口

    # 返回拼接后的展示顺序。
    return list_global_ports + list_user_ports

# 选择端口注释规则中首个命中项
def _first_mock_comment_match(comment_rules: tuple[tuple[bool, str], ...]) -> str | None:
    """
    从端口注释规则表中取首个命中的说明。

    :param comment_rules: 按优先级排列的命中状态和注释文本。
    :return: 首个命中的注释文本；没有命中时返回 None。
    """

    # 规则表已经按语义优先级排列，首个命中项就是最终说明。
    return next((str_comment for bool_matched, str_comment in comment_rules if bool_matched), None)

# 生成 SPI 相关端口的业务职责说明
def _mock_spi_port_comment(port_name: str, direction_word: str) -> str | None:
    """
    根据端口名生成 SPI 专属职责短语。

    :param port_name: 原始端口名称。
    :param direction_word: 已翻译后的方向词。
    :return: 命中 SPI 语义时返回说明，否则返回 None。
    """

    # SPI 规则需要先识别串行时钟，避免被全局时钟规则覆盖。
    str_lowered_name = port_name.lower()  # SPI 端口关键词扫描文本

    # 按 SPI 专用规则优先返回首个命中说明，避免回落到通用接口注释。
    return _first_mock_comment_match(
        (
            ("sclk" in str_lowered_name or "spi_clk" in str_lowered_name, f"SPI串行时钟{direction_word}"),  # SPI 时钟脚
            ("sdo" in str_lowered_name, f"SPI串行数据{direction_word}"),  # SPI 数据脚
            ("cnv" in str_lowered_name or "conv" in str_lowered_name, f"SPI转换启动{direction_word}"),  # ADC 转换脚
            ("sync" in str_lowered_name and "tdd" not in str_lowered_name, f"SPI帧同步{direction_word}"),  # DAC 同步脚
        )
    )

# 生成状态类侧带端口的业务职责说明
def _mock_status_sideband_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成故障、触发和忙状态职责短语。

    :param port_name: 原始端口名称。
    :return: 命中状态侧带语义时返回说明，否则返回 None。
    """

    # 状态侧带规则覆盖动作触发、外设故障和转换忙状态。
    str_lowered_name = port_name.lower()  # 状态侧带关键词扫描文本

    # 状态规则的文本刻意避开“用户接口信号”这类兜底模板。
    tuple_status_comment_rules = (  # 状态侧带注释规则表
        ("fault" in str_lowered_name, "外设故障状态输入"),  # 故障状态脚
        ("trigger" in str_lowered_name or "trig" in str_lowered_name, "采集触发输入"),  # 采集触发脚
        ("busy" in str_lowered_name, "转换忙状态输入"),  # 忙状态脚
    )

    # 按状态侧带规则返回端口职责。
    return _first_mock_comment_match(tuple_status_comment_rules)

# 生成 TDD 侧带端口的业务职责说明
def _mock_tdd_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成 TDD 同步和使能职责短语。

    :param port_name: 原始端口名称。
    :return: 命中 TDD 语义时返回说明，否则返回 None。
    """

    # TDD 规则只处理含 tdd 的侧带端口，不抢占 SPI sync。
    str_lowered_name = port_name.lower()  # TDD helper 使用的规范化端口名

    # 按 TDD 专用规则区分同步输入与使能输出，避免近似重复。
    return _first_mock_comment_match(
        (
            ("tdd" in str_lowered_name and "sync" in str_lowered_name, "TDD同步节拍输入"),  # TDD 节拍脚
            (
                "tdd" in str_lowered_name and ("enable" in str_lowered_name or "enabled" in str_lowered_name),  # TDD 使能条件
                "TDD侧带使能输出",  # TDD 使能文本
            ),  # TDD 使能脚
        )
    )

# 生成收发链路端口的业务职责说明
def _mock_rx_tx_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成 RX/TX ready 和 enable 职责短语。

    :param port_name: 原始端口名称。
    :return: 命中 RX/TX 语义时返回说明，否则返回 None。
    """

    # 收发链路规则使用中文接收/发送，保留重复检测需要的真实语义差异。
    str_lowered_name = port_name.lower()  # 收发端口关键词扫描文本

    # ready 和 enable 分别描述链路状态与控制方向。
    tuple_rx_tx_comment_rules = (  # 收发链路注释规则表
        ("rx" in str_lowered_name and "ready" in str_lowered_name, "接收链路就绪输入"),  # 接收就绪脚
        ("tx" in str_lowered_name and "ready" in str_lowered_name, "发送链路就绪输入"),  # 发送就绪脚
        ("rx" in str_lowered_name and ("enable" in str_lowered_name or "enabled" in str_lowered_name), "接收链路使能输出"),  # 接收使能脚
        ("tx" in str_lowered_name and ("enable" in str_lowered_name or "enabled" in str_lowered_name), "发送链路使能输出"),  # 发送使能脚
    )

    # 按收发链路规则返回端口职责。
    return _first_mock_comment_match(tuple_rx_tx_comment_rules)

# 生成侧带控制端口的业务职责说明
def _mock_sideband_port_comment(port_name: str) -> str | None:
    """
    根据端口名生成 TDD、RX/TX 和状态类职责短语。

    :param port_name: 原始端口名称。
    :return: 命中侧带控制语义时返回说明，否则返回 None。
    """

    # 侧带分类按特化程度排序，避免通用状态说明覆盖链路方向。
    tuple_sideband_comments = (  # 侧带分类候选说明
        _mock_status_sideband_port_comment(port_name),  # 状态侧带候选
        _mock_tdd_port_comment(port_name),  # TDD 侧带候选
        _mock_rx_tx_port_comment(port_name),  # 收发链路候选
    )

    # 取第一个非空侧带说明。
    return next((str_comment for str_comment in tuple_sideband_comments if str_comment), None)

# 生成通用握手和数据端口的业务职责说明
def _mock_data_handshake_port_comment(port_name: str, direction_word: str, width: int) -> str | None:
    """
    根据端口名生成数据、valid、ready 等通用职责短语。

    :param port_name: 原始端口名称。
    :param direction_word: 已翻译后的方向词。
    :param width: 端口位宽。
    :return: 命中通用语义时返回说明，否则返回 None。
    """

    # 通用规则只在专用 SPI/侧带规则没有命中后使用。
    str_lowered_name = port_name.lower()  # 通用端口关键词扫描文本

    # sample data/valid 语义优先于普通 data/valid 语义。
    str_sample_comment = _mock_sample_port_comment(str_lowered_name, direction_word, width)  # 采样端口职责

    # 命中采样语义时直接返回。
    if str_sample_comment:

        # 返回采样数据或采样有效职责。
        return str_sample_comment

    # 握手控制词会覆盖普通数据兜底，避免 ready/valid 被误解释成载荷。
    str_handshake_comment = _mock_handshake_control_port_comment(str_lowered_name, direction_word)  # 握手控制职责

    # 命中握手控制语义时直接返回。
    if str_handshake_comment:

        # 返回握手或控制端口职责。
        return str_handshake_comment

    # 数据端口作为通用兜底语义。
    return _mock_plain_data_port_comment(str_lowered_name, direction_word, width)

# 生成 sample 类端口职责说明。
def _mock_sample_port_comment(lowered_name: str, direction_word: str, width: int) -> str | None:
    """
    根据小写端口名生成采样数据或采样有效说明。

    :param lowered_name: 已转成小写的端口名。
    :param direction_word: 已翻译后的方向词。
    :param width: 端口位宽。
    :return: 命中 sample 语义时返回说明，否则返回 None。
    """

    # sample valid 表示采样路径的有效标志。
    if "sample" in lowered_name and "valid" in lowered_name:

        # 返回采样有效职责。
        return f"采样{direction_word}有效标志"

    # sample data 表示采样路径的数据载荷。
    if "sample" in lowered_name and "data" in lowered_name:

        # 多比特采样数据保留总线宽度说明。
        return f"{width}位采样{direction_word}数据总线" if width > 1 else f"采样{direction_word}数据位"

    # 非 sample 端口交给后续规则。
    return None

# ready、valid、start、done 这类控制脚在这里统一转成人读说明。
def _mock_handshake_control_port_comment(lowered_name: str, direction_word: str) -> str | None:
    """
    根据小写端口名生成常见握手或控制说明。

    :param lowered_name: 已转成小写的端口名。
    :param direction_word: 已翻译后的方向词。
    :return: 命中握手或控制语义时返回说明，否则返回 None。
    """

    # tuple_handshake_rules 保留从特化到通用的命中优先级。
    tuple_handshake_rules = (  # 通用握手控制注释规则
        ("push" in lowered_name and "ready" in lowered_name, "推送通道就绪输出"),  # 推送 ready 优先使用通道语义
        ("valid" in lowered_name, f"{direction_word}有效标志"),  # 普通 valid 表示方向相关有效状态
        ("ready" in lowered_name, f"{direction_word}就绪标志"),  # 普通 ready 表示方向相关就绪状态
        ("start" in lowered_name, "启动控制信号"),  # start 归入启动控制口
        ("done" in lowered_name, "完成状态标志"),  # done 归入完成状态口
    )  # 握手控制候选职责

    # 返回第一个命中的握手控制职责。
    return _first_mock_comment_match(tuple_handshake_rules)

# 生成普通 data 类端口职责说明。
def _mock_plain_data_port_comment(lowered_name: str, direction_word: str, width: int) -> str | None:
    """
    根据小写端口名生成普通数据端口说明。

    :param lowered_name: 已转成小写的端口名。
    :param direction_word: 已翻译后的方向词。
    :param width: 端口位宽。
    :return: 命中 data 语义时返回说明，否则返回 None。
    """

    # 非数据端口不在这里兜底。
    if "data" not in lowered_name:

        # 交回上层使用默认接口语义。
        return None

    # 多比特数据总线保留位宽说明。
    return f"{width}位{direction_word}数据总线" if width > 1 else f"{direction_word}数据位"

# 生成端口或输出桥接的业务职责说明
def _mock_port_intent_comment(port_name: str, direction_word: str, width: int) -> str:
    """
    根据端口名生成可复用的业务职责短语。

    :param port_name: 原始端口名称。
    :param direction_word: 已翻译后的输入、输出或双向方向词。
    :param width: 端口位宽。
    :return: 适合写入实体注释的中文职责说明。
    """

    # SPI 端口先按外设接口语义解析。
    str_spi_comment = _mock_spi_port_comment(port_name, direction_word)  # SPI 端口职责

    # 命中 SPI 角色时直接返回。
    if str_spi_comment:

        # 返回 SPI 语义说明。
        return str_spi_comment

    # TDD、RX/TX、fault 等侧带控制端口单独解析。
    str_sideband_comment = _mock_sideband_port_comment(port_name)  # 侧带控制端口职责

    # 命中侧带控制角色时直接返回。
    if str_sideband_comment:

        # 返回侧带控制语义说明。
        return str_sideband_comment

    # 数据、valid、ready 等常见端口最后解析。
    str_data_handshake_comment = _mock_data_handshake_port_comment(port_name, direction_word, width)  # 通用数据握手职责

    # 命中常见数据握手角色时直接返回。
    if str_data_handshake_comment:

        # 返回通用数据握手说明。
        return str_data_handshake_comment

    # 当前名称未命中特殊角色时，保留端口方向和接口语义。
    return f"{direction_word}用户接口信号"

# 生成单个端口的中文说明
def _mock_port_comment(port: MockPortSpec) -> str:
    """
    根据端口名称、方向与角色生成中文说明。

    :param port: 单个端口描述字典。
    :return: 适合写入 Verilog 右侧注释的中文文本。
    """

    # 读取端口名、方向和角色。
    str_name = str(port.get("name") or "")  # 端口名原文

    # 读取端口方向，后续用于拼接“输入/输出/双向”等中文语义。
    str_direction = str(port.get("direction") or "")  # 当前端口的原始方向字段

    # 读取端口角色。
    str_role = str(port.get("role") or "").lower()  # role 字段的小写文本

    # 读取端口位宽。
    int_width = int(port.get("width", 1) or 1)  # 端口总线位宽

    # 把端口名降成小写，便于匹配 clock/reset/valid 等关键词。
    str_lowered_name = str_name.lower()  # 名称关键字扫描文本

    # 把方向字段翻译成中文词，后面可直接参与注释文本拼接。
    str_direction_word = {
        "input": "输入",  # input 方向统一翻译成“输入”
        "output": "输出",  # output 方向统一翻译成“输出”
        "inout": "双向",  # inout 方向统一翻译成“双向”
    }.get(str_direction, "接口")  # 端口方向对应的中文词

    # 时钟角色或全局时钟名优先返回时序语义，避免误把 SPI sclk 当主时钟。
    if str_role == "clock" or str_lowered_name in {"i_clk", "clk", "clock", "i_clock"}:

        # 标记驱动时序的主时钟。
        return "工作时钟"

    # 复位端口优先返回复位语义。
    if str_role == "reset" or "rst" in str_lowered_name or "reset" in str_lowered_name:

        # 标记低有效复位输入。
        return "低有效复位"

    # 业务端口由名称关键词生成实体专属说明。
    return _mock_port_intent_comment(str_name, str_direction_word, int_width)

# 生成内部输出寄存器说明
def _mock_internal_output_comment(port_name: str) -> str:
    """
    根据端口名生成内部输出寄存器的中文说明。

    :param port_name: 外部输出端口名。
    :return: 内部输出寄存器的中文用途说明。
    """

    # 把输出端口名降成小写，后续根据业务关键词挑选说明文本。
    str_lowered_name = port_name.lower()  # 用于判断桥接语句语义类别的小写端口名

    # valid 输出返回握手缓存语义。
    if "valid" in str_lowered_name:

        # 说明该寄存器保存 valid 状态。
        return "输出有效缓存寄存器"

    # data 输出返回数据缓存语义。
    if "data" in str_lowered_name:

        # 说明该寄存器保存输出数据。
        return "输出数据缓存寄存器"

    # done 输出返回完成状态语义。
    if "done" in str_lowered_name:

        # 说明该寄存器保存完成标志。
        return "完成状态缓存寄存器"

    # 当名称无法推断出更细语义时，统一回退到通用输出缓存说明。
    return "输出端口缓存寄存器"

# 生成未使用输出补零说明
def _mock_unused_output_comment(port_name: str, width: int) -> str:
    """
    为未使用输出生成包含端口职责的补零说明。

    :param port_name: 外部输出端口名。
    :param width: 输出端口位宽。
    :return: 行尾补零注释文本。
    """

    # 复用端口职责短语，避免多个补零 assign 使用同一句模板。
    str_port_intent = _mock_port_intent_comment(port_name, "输出", width)  # 输出端口职责

    # 补零行为要说明该端口当前未参与 mock 数据路径。
    return f"{str_port_intent}未接入时固定低电平"

# 生成输出桥接说明
def _mock_output_bridge_comment(port_name: str) -> str:
    """
    根据端口名生成 assign 桥接语句的中文说明。

    :param port_name: 外部输出端口名。
    :return: 输出桥接语句的中文用途说明。
    """

    # 把输出端口名降成小写，便于识别桥接语句承载的是哪类输出语义。
    str_lowered_name = port_name.lower()  # 输出端口名的小写文本

    # valid 输出桥接强调握手状态传播。
    if "valid" in str_lowered_name:

        # 说明内部 valid 到端口的连通关系。
        return "输出有效标志桥接"

    # data 输出桥接强调数据总线传播。
    if "data" in str_lowered_name:

        # 说明这是内部数据总线到外部输出端口的桥接语句。
        return "输出数据总线桥接"

    # done 输出桥接强调完成标志传播。
    if "done" in str_lowered_name:

        # 说明完成标志到端口的连通关系。
        return "完成状态标志桥接"

    # 当名称没有显式语义时，统一视为普通输出端口桥接。
    return "输出端口桥接"

# 统一 mock RTL 的格式化收尾
def _normalize_mock_erie_rtl(raw: str, module_name: str) -> str:
    """
    优先走 formatter AST，对失败场景回退原始文本。

    :param raw: 尚未规范化的 Verilog 文本。
    :param module_name: 仅用于构造伪 source_path 的模块名。
    :return: 去除尾随空白后的 RTL 文本。
    """

    # 默认保留原始 RTL 作为保底结果。
    str_formatted_rtl = raw  # formatter 失败时的回退文本

    # 尝试调用本地 formatter AST。
    try:
        # 延迟导入 formatter，避免普通路径引入额外开销。
        from .formatter_ast import normalize_text_with_formatter_ast

        # 先构造 formatter 使用的伪 source_path，便于报告里显示模块名。
        path_formatter_source = Path(f"{module_name}.v")  # formatter 用来标识当前模块的伪路径

        # 调用 formatter 规范化当前 RTL 文本。
        str_formatted_rtl, dict_report = normalize_text_with_formatter_ast(raw, source_path=path_formatter_source)  # formatter 返回的文本与执行报告

        # formatter 成功时优先采用规范化结果。
        if dict_report.get("ok"):

            # 返回格式化后的去尾空白版本。
            return _align_mock_region_comments(str_formatted_rtl)

    # formatter 抛异常时继续使用原始文本。
    except Exception:

        # 出错后回退到传入的原始 RTL。
        str_formatted_rtl = raw  # formatter 抛异常时继续保留原始 RTL

    # formatter 未成功给出可用结果时，使用原始 RTL 的区域注释对齐版本。
    return _align_mock_region_comments(str_formatted_rtl)

# _align_mock_region_comments 按区域横幅锚点对齐 mock RTL 行尾注释。
def _align_mock_region_comments(text: str) -> str:
    """
    对齐 mock RTL 中区域覆盖范围内的行尾注释。

    :param text: formatter 或原始 mock RTL 文本。
    :return: 区域内行尾注释尽量贴合横幅右侧锚点的 RTL 文本。
    """

    # list_aligned_lines 保存逐行对齐后的文本。
    list_aligned_lines: list[str] = []  # 对齐后的 RTL 行集合

    # int_anchor_column 记录当前区域横幅右侧 // 的显示列。
    int_anchor_column: int | None = None  # 当前区域行尾注释锚点

    # 逐行处理文本。
    for str_line in text.splitlines():

        # 区域横幅刷新后续代码行的锚点。
        int_banner_anchor = _mock_region_banner_anchor_column(str_line)  # 当前行区域横幅锚点

        # 横幅行只刷新锚点，不重排横幅自身。
        if int_banner_anchor is not None:

            # 记录当前区域锚点。
            int_anchor_column = int_banner_anchor  # 当前区域注释锚点显示列

            # 横幅自身不做行尾注释重排。
            list_aligned_lines.append(str_line.rstrip())

        # 不在区域内时只去尾空白。
        elif int_anchor_column is None:

            # 文件头和 module 声明前部不参与区域对齐。
            list_aligned_lines.append(str_line.rstrip())

        # 已进入区域后，对代码行尝试对齐行尾注释。
        else:

            # 当前行按最近区域锚点处理。
            list_aligned_lines.append(_align_mock_line_comment(str_line, int_anchor_column))

    # list_compact_lines 在对齐后恢复 mock 模块体的既有 `//注释` 合同风格。
    list_compact_lines = [_compact_mock_semantic_comment_spacing(str_line) for str_line in list_aligned_lines]  # 恢复 mock 语义注释紧凑前缀后的文本行

    # 统一补末尾换行。
    return "\n".join(list_compact_lines) + "\n"

# _compact_mock_semantic_comment_spacing 恢复 mock 模块体语义注释的既有 `//注释` 风格。
def _compact_mock_semantic_comment_spacing(str_line: str) -> str:
    """
    压缩 mock 模块体纯注释行和右侧注释的 `// ` 前缀。

    :param str_line: 已完成对齐的单行 mock RTL 文本。
    :return: 恢复既有语义注释风格后的单行文本。
    """

    # str_compact_line 保存逐步压缩后的行文本。
    str_compact_line = str_line  # 待恢复注释前缀合同的 mock RTL 行

    # 行尾语义注释保留代码前空格，但注释标记回到 `//注释` 形式。
    if " // " in str_compact_line and not str_compact_line.lstrip().startswith("//"):

        # 只压缩首个行尾语义注释前缀，避免误改注释正文。
        str_compact_line = str_compact_line.replace(" // ", " //", 1)  # 行尾语义注释恢复紧凑前缀

    # 缩进后的纯注释行属于模块体语义说明，恢复 `//注释` 形式。
    str_lstripped = str_compact_line.lstrip()  # 去掉缩进后的注释候选文本

    # int_indent_len 区分文件头无缩进行和模块体缩进注释行。
    int_indent_len = len(str_compact_line) - len(str_lstripped)  # 当前行的缩进字符数

    # 只压缩带缩进的纯注释行，文件头 `// 字段` 风格保持不变。
    if int_indent_len > 0 and str_lstripped.startswith("// "):

        # 缩进保留原样，只把注释前缀恢复为紧凑形式。
        str_compact_line = f"{str_compact_line[:int_indent_len]}//{str_lstripped[3:]}"  # 纯注释语义说明恢复紧凑前缀

    # 返回恢复既有 mock 注释风格后的行文本。
    return str_compact_line

# _align_mock_line_comment 对齐单行代码注释。
def _align_mock_line_comment(str_line: str, int_anchor_column: int) -> str:
    """
    对齐一行 mock RTL 的行尾注释。

    :param str_line: 当前 RTL 行。
    :param int_anchor_column: 当前区域注释锚点显示列。
    :return: 对齐后的 RTL 行。
    """

    # int_comment_index 定位真实 // 注释起点。
    int_comment_index = _mock_line_comment_start(str_line)  # 行尾注释起点

    # 无行尾注释、纯注释行或空行不参与对齐。
    if int_comment_index < 0 or not str_line.strip() or str_line.lstrip().startswith("//"):

        # 原样去除尾随空白。
        return str_line.rstrip()

    # str_code 保留需要补齐到锚点前的 Verilog 代码。
    str_code = str_line[:int_comment_index].rstrip()  # 去尾空白后的代码片段

    # str_comment 保留含 // 的完整行尾说明。
    str_comment = str_line[int_comment_index:].strip()  # 行尾注释片段

    # int_code_width 计算代码片段显示宽度。
    int_code_width = _mock_display_width_with_tabs(str_code)  # 注释前代码显示宽度

    # 注释能落在横幅锚点时补齐到锚点。
    if int_code_width < int_anchor_column:

        # str_padding 把当前代码行推到区域横幅锚点列。
        str_padding = " " * (int_anchor_column - int_code_width)  # 区域锚点补齐空白

    # 代码越过锚点时，注释只能紧跟代码后一个空格。
    else:

        # str_padding 保留 Verilog 代码和注释之间的最小间隔。
        str_padding = " "  # 越过锚点后的最小注释间隔

    # 返回对齐后的代码行。
    return f"{str_code}{str_padding}{str_comment}"

# _mock_region_banner_anchor_column 返回 mock 区域横幅右侧 // 的显示列。
def _mock_region_banner_anchor_column(str_line: str) -> int | None:
    """
    返回区域横幅右侧 // 的显示列。

    :param str_line: 当前 RTL 行。
    :return: 横幅右侧 // 显示列；非横幅时返回 None。
    """

    # str_stripped 用于识别标准区域横幅。
    str_stripped = str_line.strip()  # 去缩进后的横幅候选

    # 标准区域横幅必须是双 // 边界并含横线。
    if not (str_stripped.startswith("//") and str_stripped.endswith("//") and "-" in str_stripped):

        # 普通注释不提供区域锚点。
        return None

    # int_anchor_index 是最右侧 // 的原始下标。
    int_anchor_index = str_line.rfind("//")  # 横幅右侧注释边界

    # 没有右侧边界时不作为横幅处理。
    if int_anchor_index <= str_line.find("//"):

        # 非标准横幅不参与对齐。
        return None

    # 返回右侧边界前文本显示宽度。
    return _mock_display_width_with_tabs(str_line[:int_anchor_index])

# _mock_line_comment_start 返回 mock RTL 行中真实 // 起点。
def _mock_line_comment_start(str_line: str) -> int:
    """
    返回未被字符串包裹的 // 起点。

    :param str_line: 当前 RTL 行。
    :return: 真实注释起点；不存在时返回 -1。
    """

    # bool_in_string 表示扫描是否在双引号字符串内。
    bool_in_string = False  # 当前字符串扫描状态

    # bool_escaped 表示当前字符是否被转义。
    bool_escaped = False  # 字符串转义状态

    # 逐字符扫描。
    for int_index, str_char in enumerate(str_line):

        # 被转义字符不参与状态切换。
        if bool_escaped:

            # 转义状态只影响一个字符。
            bool_escaped = False  # 当前转义字符已经消费

            # 已消费转义字面量，后续判断从下一个字符重新开始。
            continue

        # 字符串内反斜杠开启转义。
        if str_char == "\\" and bool_in_string:

            # 下一字符被视为字面量。
            bool_escaped = True  # 下一字符按普通字符处理

            # 转义标记已经建立，当前反斜杠不再参与其他判断。
            continue

        # 双引号切换字符串状态。
        if str_char == '"':

            # 更新字符串状态。
            bool_in_string = not bool_in_string  # 字符串内外状态

            # 引号只改变扫描状态，不会同时作为注释起点。
            continue

        # 字符串内部的普通字符不参与注释判断。
        if bool_in_string:

            # 继续扫描直到离开字符串字面量。
            continue

        # 字符串外的 // 是行注释。
        if str_line.startswith("//", int_index):

            # 返回真实注释起点。
            return int_index

    # 没有真实注释。
    return -1

# _mock_display_width_with_tabs 计算 mock RTL 显示宽度。
def _mock_display_width_with_tabs(str_text: str) -> int:
    """
    计算包含 Tab 的源码片段显示宽度。

    :param str_text: 需要计算宽度的源码片段。
    :return: Tab 按四列展开后的显示宽度。
    """

    # int_width 累计字符显示宽度。
    int_width = 0  # 当前片段显示宽度

    # 逐字符累计。
    for str_char in str_text:

        # Tab 按 formatter 约定展开为四列。
        if str_char == "\t":

            # 累加 Tab 显示宽度。
            int_width += 4  # Tab 展开后的列宽

        # 非 Tab 字符按中英文显示宽度累计。
        else:

            # 中文宽字符由 banner 工具处理。
            int_width += display_width(str_char)  # 普通字符显示宽度

    # 返回累计宽度。
    return int_width

# 清理每行尾随空白
def _strip_line_trailing_space(text: str) -> str:
    """
    去掉每一行末尾的空白字符并补一个结尾换行。

    :param text: 任意多行文本。
    :return: 行尾无多余空白且以换行结束的文本。
    """

    # 统一规范输出文本结尾，避免后续拼接时丢失末尾换行。
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"

# 根据规格与向量拼接自检 testbench RTL 文本
def _mock_erie_rtl_testbench_text(
    spec: dict[str, Any],
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> str:
    """
    根据 spec 与向量生成自检式 testbench。

    :param spec: 含模块名与端口定义的规范化规格。
    :param vectors: 用于构造期望值的测试向量。
    :param vector_hash: 向量契约哈希，可写入注释供回归核对。
    :return: 带行尾语义注释的 Verilog testbench 文本。
    """

    # 复用与 DUT 一致的端口语义布局。
    mock_port_layout_snapshot = _build_mock_port_layout(spec)  # testbench 生成阶段使用的端口布局快照

    # 纯组合规范使用无时钟复位的专用 testbench 模板。
    if not _layout_has_sequential_controls(mock_port_layout_snapshot):

        # 组合型 mock testbench 直接验证 mux 与 parity 的组合传播。
        return _mock_erie_comb_testbench_text(mock_port_layout_snapshot, vectors, vector_hash)

    # 计算 testbench 中使用的整数期望值。
    int_expected_value = _mock_expected_value(vectors)  # 自检比较使用的期望值

    # 计算统一的数据位宽。
    dict_width_source = mock_port_layout_snapshot.data_output or mock_port_layout_snapshot.data_input or {"width": 8}  # EXPECTED_VALUE 与 value 共用位宽的来源端口

    # 把位宽来源对象折算成 EXPECTED_VALUE 和 value 共用的数据总线宽度。
    int_data_width = int(dict_width_source.get("width", 8) or 8)  # EXPECTED_VALUE 与 value 两条总线共用的数据位宽

    # 选择自检时优先观察的数据输出、valid 输出或兜底常量端口。
    dict_primary_observed_port = mock_port_layout_snapshot.data_output or mock_port_layout_snapshot.valid_output  # 自检优先观察的数据或 valid 端口候选

    # 在候选缺失时回退到 EXPECTED_VALUE 常量，保证观测信号名始终存在。
    dict_observed_port = dict_primary_observed_port or {"name": "EXPECTED_VALUE"}  # 自检最终使用的观测端口

    # 选择自检时实际观测的信号名。
    str_observed_signal = str(dict_observed_port["name"])  # 自检 value 导线实际观测到的 DUT 信号名

    # 时序 testbench 从声明段开始积累源码行。
    list_lines: list[str] = []  # 时序 testbench 完整源码行缓存

    # 声明段包含驱动寄存器、观测导线和 DUT 例化头。
    list_lines.extend(
        _mock_sequential_tb_declaration_lines(
            mock_port_layout_snapshot,  # 时序 testbench 端口布局
            int_expected_value,  # EXPECTED_VALUE 初始化值
            int_data_width,  # value 与 EXPECTED_VALUE 共用位宽
            str_observed_signal,  # value 导线观测目标
        )
    )

    # 组合 DUT 映射保持用户端口声明顺序，便于 testbench 同名连接。
    list_lines.extend(_mock_instance_port_mapping_lines(mock_port_layout_snapshot.ports))  # DUT 端口映射行

    # initial 激励段负责复位、输入驱动和自检退出路径。
    list_initial_lines = _mock_sequential_tb_initial_lines(  # initial 块源码行集合
        mock_port_layout_snapshot,  # initial 阶段使用的 DUT 端口快照
        vectors,  # 写入测试意图说明的向量清单
        vector_hash,  # 回归日志中的向量契约摘要
        int_data_width,  # 数据激励总线宽度
        int_expected_value,  # 最终比较的整数期望值
    )

    # 把 initial 块拼到 DUT 例化之后。
    list_lines.extend(list_initial_lines)

    # 返回带行尾语义注释的 testbench 文本。
    return _add_mock_line_comments("\n".join(list_lines) + "\n")

# 生成时序 testbench 的声明区行。
def _mock_sequential_tb_declaration_lines(
    layout: MockPortLayout,
    expected_value: int,
    data_width: int,
    observed_signal: str,
) -> list[str]:
    """
    生成时序 testbench 的寄存器、导线和 DUT 例化头。

    :param layout: mock 端口语义布局。
    :param expected_value: EXPECTED_VALUE 初始化值。
    :param data_width: EXPECTED_VALUE 和 value 共用的数据位宽。
    :param observed_signal: value 导线观测的 DUT 输出信号名。
    :return: testbench 声明区源码行。
    """

    # 组合 TB 的第一行只声明无时钟模块壳。
    list_lines: list[str] = []  # 时序 testbench 声明区缓存

    # 写入 testbench module 头。
    list_lines.append(f"module {layout.top}_tb;")

    # 时钟寄存器负责驱动 DUT 的 posedge 采样。
    list_lines.append(f"\treg {layout.clock_name} = 1'b0;")

    # 复位寄存器负责驱动 DUT 的低有效复位流程。
    list_lines.append(f"\treg {layout.reset_name} = 1'b0;")

    # 普通输入端口在 testbench 中生成为可驱动 reg。
    list_lines.extend(_mock_input_register_lines(layout.inputs))  # 输入驱动寄存器声明行

    # 输出端口在 testbench 中生成为观测 wire。
    list_lines.extend(_mock_output_wire_lines(layout.outputs))  # 输出观测导线声明行

    # EXPECTED_VALUE 保存最终比较用的期望数据。
    list_lines.append(f"\treg [{data_width - 1}:0] EXPECTED_VALUE = {data_width}'d{expected_value};")

    # value 统一绑定到当前场景最主要的 DUT 观测信号。
    list_lines.append(f"\twire [{data_width - 1}:0] value = {observed_signal};")

    # 固定周期时钟让时序 DUT 能完成采样。
    list_lines.append(f"\talways #5 {layout.clock_name} = ~{layout.clock_name};")

    # DUT 例化头等待后续端口映射行补齐。
    list_lines.append(f"\t{layout.top} dut (")

    # 返回完整声明区。
    return list_lines

# 生成 testbench 输入驱动寄存器声明行。
def _mock_input_register_lines(input_ports: list[MockPortSpec]) -> list[str]:
    """
    将输入端口列表渲染为 testbench 驱动寄存器声明。

    :param input_ports: 普通输入端口列表。
    :return: 输入驱动寄存器声明行。
    """

    # list_lines 保存输入端口对应的 reg 声明。
    list_lines: list[str] = []  # 输入寄存器声明行

    # 为每个普通输入生成驱动寄存器声明。
    for input_port in input_ports:

        # 计算当前输入的位宽前缀。
        str_width_text = _width_text(input_port)  # reg 声明位宽

        # 生成当前输入的上电初始值。
        str_init_value = _mock_zero_init_literal(int(input_port.get("width", 1) or 1))  # 输入寄存器上电初值

        # 追加当前输入寄存器声明。
        list_lines.append(f"\treg {str_width_text}{input_port['name']} = {str_init_value};")

    # 返回所有输入驱动寄存器声明。
    return list_lines

# 生成 testbench 输出观测导线声明行。
def _mock_output_wire_lines(output_ports: list[MockPortSpec]) -> list[str]:
    """
    将输出端口列表渲染为 testbench 观测导线声明。

    :param output_ports: 普通输出端口列表。
    :return: 输出观测导线声明行。
    """

    # list_lines 保存输出端口对应的 wire 声明。
    list_lines: list[str] = []  # 待返回的输出观测声明缓存

    # 为每个输出生成观测导线声明。
    for output_port in output_ports:

        # 计算当前输出的位宽前缀。
        str_width_text = _width_text(output_port)  # 当前观测导线沿用的输出位宽前缀

        # 追加当前输出导线声明。
        list_lines.append(f"\twire {str_width_text}{output_port['name']};")

    # 返回所有输出观测导线声明。
    return list_lines

# 生成 testbench 端口映射行。
def _mock_instance_port_mapping_lines(ports: list[MockPortSpec]) -> list[str]:
    """
    生成 DUT 例化中的逐项端口映射行。

    :param ports: DUT 端口声明顺序。
    :return: 端口映射源码行。
    """

    # list_lines 保存例化端口映射。
    list_lines: list[str] = []  # DUT 连接表逐行缓存

    # 例化映射保持 DUT 端口声明顺序。
    for index, port in enumerate(ports):

        # 末尾端口映射不追加逗号。
        str_trailing_comma = "," if index < len(ports) - 1 else ""  # 当前映射行尾随逗号

        # 将 DUT 当前端口接到同名 testbench 信号。
        list_lines.append(f"\t\t.{port['name']}({port['name']}){str_trailing_comma}")

    # 返回全部端口映射行。
    return list_lines

# 生成时序 testbench initial 激励和自检行。
def _mock_sequential_tb_initial_lines(
    layout: MockPortLayout,
    vectors: list[dict[str, Any]],
    vector_hash: str,
    data_width: int,
    expected_value: int,
) -> list[str]:
    """
    生成时序 testbench 的 initial 块主体和收尾。

    :param layout: mock 端口语义布局。
    :param vectors: 用于写入可审查说明的测试向量。
    :param vector_hash: 向量契约哈希。
    :param data_width: 数据激励使用的位宽。
    :param expected_value: 数据输入激励使用的值。
    :return: DUT 例化闭合、initial 主体和 endmodule 行。
    """

    # list_lines 先闭合 DUT 例化。
    list_lines = ["\t);"]  # DUT 例化闭合行

    # initial 块承载复位、激励和自检流程。
    list_lines.append("\tinitial begin")

    # 向需要追踪向量版本的场景写入契约哈希。
    list_lines.extend(_mock_vector_comment_lines(vectors, vector_hash, "checkpoint value against EXPECTED_VALUE"))  # 向量说明注释行

    # 低有效复位先拉低，确保 DUT 进入确定状态。
    list_lines.append(f"\t\t{layout.reset_name} = 1'b0;")

    # 复位等待覆盖一个完整时钟周期。
    list_lines.append("\t\t#12;")

    # 释放复位后再写入业务激励。
    list_lines.append(f"\t\t{layout.reset_name} = 1'b1;")

    # 数据输入和 valid 输入根据端口是否存在分别驱动。
    list_lines.extend(_mock_sequential_input_stimulus_lines(layout, data_width, expected_value))  # 输入激励行

    # 追加传播等待、valid 检查、最终值比较和仿真收尾。
    list_lines.extend(_mock_sequential_check_lines(layout))  # 时序自检与收尾行

    # 返回完整 initial 区域。
    return list_lines

# 生成向量契约和样例说明注释行。
def _mock_vector_comment_lines(vectors: list[dict[str, Any]], vector_hash: str, comparison_text: str) -> list[str]:
    """
    生成 testbench initial 块中的向量哈希和样例说明注释。

    :param vectors: 用于写入可审查说明的测试向量。
    :param vector_hash: 向量契约哈希。
    :param comparison_text: 每条向量说明中的比较对象文本。
    :return: 可直接插入 initial 块的注释行。
    """

    # list_lines 保存 initial 块开头的可审查说明。
    list_lines: list[str] = []  # 向量契约和样例说明行

    # 向量契约哈希存在时写入固定标签。
    if vector_hash:

        # 追加向量契约哈希注释。
        list_lines.append(f"\t\t// {VECTOR_HASH_TAG} {vector_hash}")

    # 为每个向量写入一行自检说明。
    for case in vectors:

        # 追加当前向量的检查摘要。
        list_lines.append(f'\t\t// {case["id"]} compares {comparison_text} and reports PASS/FAIL')

    # 返回所有向量说明注释行。
    return list_lines

# 生成时序 testbench 输入激励行。
def _mock_sequential_input_stimulus_lines(layout: MockPortLayout, data_width: int, expected_value: int) -> list[str]:
    """
    根据端口是否存在生成 data 和 valid 输入激励行。

    :param layout: mock 端口语义布局。
    :param data_width: 数据激励使用的位宽。
    :param expected_value: 数据输入激励使用的值。
    :return: 输入激励源码行。
    """

    # list_lines 保存可选输入激励行。
    list_lines: list[str] = []  # 输入激励源码行

    # 数据输入存在时写入一次样例激励。
    if layout.data_input:

        # 追加数据输入激励语句。
        list_lines.append(f"\t\t{layout.data_input['name']} = {data_width}'d{expected_value};")

    # valid 输入存在时写入一次握手激励。
    if layout.valid_input:

        # 追加 valid 输入激励语句。
        list_lines.append(f"\t\t{layout.valid_input['name']} = 1'b1;")

    # 返回所有输入激励行。
    return list_lines

# 生成时序 testbench 自检和收尾行。
def _mock_sequential_check_lines(layout: MockPortLayout) -> list[str]:
    """
    生成时序 testbench 的传播等待、可选 valid 检查和值比较。

    :param layout: mock 端口语义布局。
    :return: 自检与仿真结束源码行。
    """

    # list_lines 先给 DUT 一段时间完成输出传播。
    list_lines = ["\t\t#20;"]  # 输出传播等待行

    # valid 输出存在时先检查握手是否拉高。
    if layout.valid_output:

        # valid 输出检查入口绑定到实际 valid 端口名。
        list_lines.append(f"\t\tif ({layout.valid_output['name']} !== 1'b1) begin")

        # valid 未拉高时直接终止仿真。
        list_lines.append('\t\t\t$fatal(1, "FAIL: valid output did not assert");')

        # 关闭 valid 输出检查分支。
        list_lines.append("\t\tend")

    # 追加最终值比较和仿真结束语句。
    list_lines.extend(_mock_common_tb_tail_lines())  # 最终自检和仿真收尾

    # 返回完整自检区域。
    return list_lines

# 生成 testbench 通用收尾行。
def _mock_common_tb_tail_lines() -> list[str]:
    """
    生成 PASS 日志、失败比较和仿真退出语句。

    :param: 无输入参数；收尾结构由 mock testbench 协议固定。
    :return: testbench initial 块末尾与 endmodule 行。
    """

    # list_lines 保存时序 testbench 共用的末尾自检结构。
    list_lines = ["\t\tif (value !== EXPECTED_VALUE) begin"]  # 最终值比较入口

    # 期望值不匹配时立即报错退出。
    list_lines.append('\t\t\t$fatal(1, "FAIL: value checkpoint mismatch");')

    # 关闭最终值比较分支。
    list_lines.append("\t\tend")

    # 自检全部通过时打印 PASS 摘要。
    list_lines.append('\t\t$display("PASS: self-checking mock testbench completed");')

    # 主动结束仿真。
    list_lines.append("\t\t$finish;")

    # 关闭 initial 块。
    list_lines.append("\tend")

    # 关闭 testbench 模块。
    list_lines.append("endmodule")

    # 返回通用收尾源码行。
    return list_lines

# 生成 testbench 寄存器初始值字面量。
def _mock_zero_init_literal(width: int) -> str:
    """
    根据端口位宽返回 testbench 寄存器上电清零字面量。

    :param width: 目标端口位宽。
    :return: 单比特或多比特清零字面量。
    """

    # 多比特输入上电时清零整个总线。
    if width > 1:

        # 返回带位宽的十进制零值。
        return f"{width}'d0"

    # 单比特输入统一回到低电平。
    return "1'b0"

# 为组合型 mock 生成稳定的非零测试激励字面量。
def _mock_sample_literal(width: int, *, alternate: bool) -> str:
    """按位宽返回组合 testbench 使用的稳定字面量。

    :param width: 目标信号位宽。
    :param alternate: 是否选择第二组测试花纹。
    :return: 与位宽匹配的 Verilog 字面量文本。
    """

    # 单比特激励只需要两种互补电平即可。
    if width <= 1:

        # 第二组激励返回高电平，第一组返回低电平。
        return "1'b1" if alternate else "1'b0"

    # 多比特激励用固定十六进制花纹，确保两组样例明显可区分。
    str_seed = "A5" if alternate else "3C"  # 组合样例使用的固定十六进制花纹

    # 十六进制位数按目标位宽向上取整。
    int_hex_digits = max(1, (width + 3) // 4)  # 目标位宽所需的最小十六进制字符数

    # 把固定花纹重复到足够长度，再截断到目标位数。
    str_hex_value = (str_seed * ((int_hex_digits + len(str_seed) - 1) // len(str_seed)))[:int_hex_digits]  # 宽度对齐后的十六进制字面量正文

    # 返回与位宽匹配的十六进制字面量。
    return f"{width}'h{str_hex_value}"

# 选择组合型 parity 输出端口。
def _select_comb_parity_output(layout: MockPortLayout) -> MockPortSpec | None:
    """从组合型输出里选择 parity 语义端口。

    :param layout: mock 端口语义布局对象。
    :return: 显式带 parity 语义的输出端口；缺失时返回 None。
    """

    # parity 输出只接受显式命名，避免把普通单比特状态口误判成奇偶校验口。
    return next(
        (item for item in layout.outputs if "parity" in str(item.get("name")).lower()),
        None,
    )

# 生成组合型 mux 场景的自检语句。
def _mock_comb_mux_check_lines(
    dict_primary_output: MockPortSpec,
    dict_selector_port: MockPortSpec,
    dict_first_data_input: MockPortSpec,
    dict_second_data_input: MockPortSpec,
    dict_parity_output: MockPortSpec | None,
) -> list[str]:
    """生成 selector 驱动双路输入切换的组合检查语句。

    :param dict_primary_output: 主数据输出端口。
    :param dict_selector_port: selector 输入端口。
    :param dict_first_data_input: selector 为 0 时应命中的第一路输入。
    :param dict_second_data_input: selector 为 1 时应命中的第二路输入。
    :param dict_parity_output: 可选 parity 输出端口。
    :return: 可直接拼接进 initial 块的自检语句列表。
    """

    # 初始化 mux 场景的检查语句缓冲。
    list_check_lines: list[str] = []  # mux 场景检查语句缓冲

    # 生成 selector 低电平场景的第一组数据激励。
    str_first_value = _mock_sample_literal(int(dict_first_data_input.get("width", 1) or 1), alternate=False)  # selector 为 0 的数据激励值

    # 再准备一组互补花纹，确保 selector 翻转后主输出确实切到另一条数据通路。
    str_second_value = _mock_sample_literal(int(dict_second_data_input.get("width", 1) or 1), alternate=True)  # 第二路输入花纹值

    # 先写入第一路输入的样例值。
    list_check_lines.append(f"\t\t{dict_first_data_input['name']} = {str_first_value};")

    # 再写入第二路输入的对照样例值。
    list_check_lines.append(f"\t\t{dict_second_data_input['name']} = {str_second_value};")

    # 让 selector 先选择第一路输入。
    list_check_lines.append(f"\t\t{dict_selector_port['name']} = 1'b0;")

    # 为组合传播预留一个最小等待周期。
    list_check_lines.append("\t\t#1;")

    # 检查 selector 为 0 时主输出是否命中第一路输入。
    list_check_lines.append(
        f"\t\tif ({dict_primary_output['name']} !== {dict_first_data_input['name']}) begin"
    )

    # 当 selector 为 0 的主输出不匹配时立即终止仿真。
    list_check_lines.append(
        '\t\t\t$fatal(1, "FAIL: combinational primary output mismatch when selector is 0");'
    )

    # 关闭 selector 为 0 的失败分支。
    list_check_lines.append("\t\tend")

    # parity 输出存在时，需要确认第一条路径的奇偶校验同步成立。
    if dict_parity_output:

        # 检查 selector 为 0 时 parity 是否跟随第一条数据路径同步折叠。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== ^{dict_primary_output['name']}) begin"
        )

        # 当 selector 为 0 的 parity 不匹配时立即终止仿真。
        list_check_lines.append('\t\t\t$fatal(1, "FAIL: parity output mismatch when selector is 0");')

        # 收束第一轮 parity 校验对应的 begin-end 失败块。
        list_check_lines.append("\t\tend")

    # selector 切到第二路后，应把主输出切换到第二个数据端口。
    list_check_lines.append(f"\t\t{dict_selector_port['name']} = 1'b1;")

    # 给 selector 翻转后的输出留一次新的组合传播窗口。
    list_check_lines.append("\t\t#1;")

    # 检查 selector 为 1 时主输出是否切到第二路输入。
    list_check_lines.append(
        f"\t\tif ({dict_primary_output['name']} !== {dict_second_data_input['name']}) begin"
    )

    # 用第二轮主输出专属的报错信息标记高电平 selector 失败。
    list_check_lines.append(
        '\t\t\t$fatal(1, "FAIL: combinational primary output mismatch when selector is 1");'
    )

    # 收束第二轮主输出校验对应的 begin-end 失败块。
    list_check_lines.append("\t\tend")

    # parity 输出存在时，也要覆盖 selector 为 1 的同步奇偶校验。
    if dict_parity_output:

        # 检查高电平 selector 场景下的 parity 是否切到第二条数据路径。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== ^{dict_primary_output['name']}) begin"
        )

        # 这里保留 selector 为 1 的 parity 专属报错文本，便于定位第二轮切换失败。
        list_check_lines.append('\t\t\t$fatal(1, "FAIL: parity output mismatch when selector is 1");')

        # 用 end 收束这一次高电平 parity 校验对应的失败块。
        list_check_lines.append("\t\tend")

    # 返回双路 mux 场景的完整自检语句。
    return list_check_lines

# 生成组合型直通场景的自检语句。
def _mock_comb_passthrough_check_lines(
    dict_primary_output: MockPortSpec,
    dict_first_data_input: MockPortSpec,
    dict_parity_output: MockPortSpec | None,
) -> list[str]:
    """生成单输入直通型组合路径的检查语句。

    :param dict_primary_output: 主数据输出端口。
    :param dict_first_data_input: 唯一数据输入端口。
    :param dict_parity_output: 可选 parity 输出端口。
    :return: 可直接拼接进 initial 块的自检语句列表。
    """

    # 初始化直通场景的检查语句缓冲。
    list_check_lines: list[str] = []  # 直通场景检查语句缓冲

    # 生成直通场景唯一输入使用的激励值。
    str_first_value = _mock_sample_literal(int(dict_first_data_input.get("width", 1) or 1), alternate=False)  # 直通路径的数据激励值

    # 把样例值写入唯一数据输入。
    list_check_lines.append(f"\t\t{dict_first_data_input['name']} = {str_first_value};")

    # 给直通路径一次 delta 周期，让输出完成传播。
    list_check_lines.append("\t\t#1;")

    # 检查直通路径的主输出是否跟随唯一输入变化。
    list_check_lines.append(
        f"\t\tif ({dict_primary_output['name']} !== {dict_first_data_input['name']}) begin"
    )

    # 当直通路径的主输出不匹配时立即终止仿真。
    list_check_lines.append('\t\t\t$fatal(1, "FAIL: combinational primary output mismatch");')

    # 关闭直通路径的失败分支。
    list_check_lines.append("\t\tend")

    # parity 输出存在时，同步补齐组合折叠检查。
    if dict_parity_output:

        # 检查直通路径的 parity 是否与主输出保持同拍组合一致。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== ^{dict_primary_output['name']}) begin"
        )

        # 当直通路径的 parity 不匹配时立即终止仿真。
        list_check_lines.append('\t\t\t$fatal(1, "FAIL: parity output mismatch");')

        # 收束直通场景 parity 校验对应的 begin-end 失败块。
        list_check_lines.append("\t\tend")

    # 返回直通场景的完整自检语句。
    return list_check_lines

# 生成组合型零值兜底场景的自检语句。
def _mock_comb_zero_check_lines(
    dict_primary_output: MockPortSpec | None,
    dict_parity_output: MockPortSpec | None,
) -> list[str]:
    """生成无输入场景下的常零检查语句。

    :param dict_primary_output: 可选主数据输出端口。
    :param dict_parity_output: 可选 parity 输出端口。
    :return: 可直接拼接进 initial 块的自检语句列表。
    """

    # 初始化零值兜底场景的检查语句缓冲。
    list_check_lines: list[str] = []  # 零值兜底场景检查语句缓冲

    # 无输入组合场景统一先等待一个组合传播周期。
    list_check_lines.append("\t\t#1;")

    # 主输出存在时，应保持为固定零值。
    if dict_primary_output:

        # 主数据口在无输入激励时只能输出该位宽下的零字面量。
        list_check_lines.append(
            f"\t\tif ({dict_primary_output['name']} !== "
            f"{_zero_literal(int(dict_primary_output.get('width', 1) or 1))}) begin"
        )

        # 主数据口偏离零值说明兜底组合路径不可用。
        list_check_lines.append(
            '\t\t\t$fatal(1, "FAIL: combinational primary output should stay zero");'
        )

        # 收束主数据口零值断言分支。
        list_check_lines.append("\t\tend")

    # parity 输出存在时，同样应保持为零值。
    if dict_parity_output:

        # parity 口无输入时也必须落在零值，避免孤立校验位漂移。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== "
            f"{_zero_literal(int(dict_parity_output.get('width', 1) or 1))}) begin"
        )

        # parity 口非零表示组合兜底路径产生了额外状态。
        list_check_lines.append(
            '\t\t\t$fatal(1, "FAIL: combinational parity output should stay zero");'
        )

        # 收束 parity 零值断言分支。
        list_check_lines.append("\t\tend")

    # 返回零值兜底场景的完整自检语句。
    return list_check_lines

# 为组合型 mock 生成无时钟复位的自检 testbench。
def _mock_erie_comb_testbench_text(
    layout: MockPortLayout,
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> str:
    """根据组合型端口布局生成自检式 testbench。

    :param layout: mock 端口语义布局对象。
    :param vectors: 用于写入说明注释的测试向量。
    :param vector_hash: 向量契约哈希，可写入注释供回归核对。
    :return: 带行尾语义注释的组合 testbench 文本。
    """

    # 记录组合 testbench 需要观测的主数据输出端口。
    dict_primary_output = _select_comb_primary_output(layout)  # mux 或直通检查的主输出

    # parity 输出存在时会追加异或校验场景。
    dict_parity_output = _select_comb_parity_output(layout)  # parity 自检候选输出

    # 组合 testbench 声明区包含输入 reg、输出 wire 与 DUT 例化头。
    list_lines = _mock_comb_tb_declaration_lines(layout)  # 组合用声明和例化头行

    # 组合 DUT 例化保持接口声明顺序。
    list_lines.extend(_mock_instance_port_mapping_lines(layout.ports))  # 组合 DUT 同名连接行

    # 关闭 DUT 例化参数和端口映射列表。
    list_lines.append("\t);")

    # initial 块写入向量说明并执行组合路径检查。
    list_lines.append("\tinitial begin")

    # 向量说明注释保留组合检查的人读回归线索。
    list_lines.extend(_mock_vector_comment_lines(vectors, vector_hash, "combinational checkpoints"))  # 组合向量说明行

    # 根据端口布局选择 mux、直通或零值兜底检查。
    list_lines.extend(_mock_comb_selected_check_lines(layout, dict_primary_output, dict_parity_output))  # 组合场景自检行

    # 追加组合 testbench 原有的 PASS 日志和仿真结束语句。
    list_lines.extend(_mock_comb_tb_tail_lines())  # 组合 testbench 统一收尾

    # 返回带行尾语义注释的组合 testbench 文本。
    return _add_mock_line_comments("\n".join(list_lines) + "\n")

# 生成组合 testbench 的声明区行。
def _mock_comb_tb_declaration_lines(layout: MockPortLayout) -> list[str]:
    """
    生成组合 testbench 的输入寄存器、输出导线和 DUT 例化头。

    :param layout: mock 端口语义布局对象。
    :return: 组合 testbench 声明区源码行。
    """

    # list_lines 先保存组合 testbench module 头。
    list_lines: list[str] = []  # 组合声明区行缓存

    # 写入组合 testbench module 声明。
    list_lines.append(f"module {layout.top}_tb;")

    # 组合输入同样用 testbench reg 驱动。
    list_lines.extend(_mock_input_register_lines(layout.inputs))  # 组合输入激励寄存器

    # 组合输出统一生成为观测 wire。
    list_lines.extend(_mock_output_wire_lines(layout.outputs))  # 组合输出观测导线

    # 写入 DUT 例化头，后续逐项补齐端口映射。
    list_lines.append(f"\t{layout.top} dut (")

    # 返回声明区和 DUT 例化头。
    return list_lines

# 选择组合 testbench 的自检场景行。
def _mock_comb_selected_check_lines(
    layout: MockPortLayout,
    primary_output: MockPortSpec | None,
    parity_output: MockPortSpec | None,
) -> list[str]:
    """
    根据组合输入输出形态选择 mux、直通或零值兜底检查。

    :param layout: mock 端口语义布局对象。
    :param primary_output: 可选主数据输出端口。
    :param parity_output: 可选 parity 输出端口。
    :return: 组合场景自检源码行。
    """

    # tuple_input_paths 保存 selector、低电平输入和高电平输入候选。
    tuple_input_paths = _select_comb_input_paths(layout)  # selector/数据输入候选三元组

    # dict_selector_port 控制 mux 双输入检查路径。
    dict_selector_port = tuple_input_paths[0]  # 控制路径端口快照

    # dict_first_data_input 是直通场景的默认输入路径。
    dict_first_data_input = tuple_input_paths[1]  # 低电平路径端口快照

    # dict_second_data_input 是 mux 场景的备选输入路径。
    dict_second_data_input = tuple_input_paths[2]  # 高电平路径端口快照

    # 双路 mux 场景需要 selector 与两路不同的数据输入。
    if _mock_has_comb_mux_case(primary_output, dict_selector_port, dict_first_data_input, dict_second_data_input):

        # 双路 mux 检查需要同时驱动 selector 和两路数据样例。
        return _mock_comb_mux_check_lines(
            primary_output,  # mux 主数据输出
            dict_selector_port,  # mux 路径选择输入
            dict_first_data_input,  # selector 低电平路径输入
            dict_second_data_input,  # selector 高电平路径输入
            parity_output,  # 可选 parity 输出
        )

    # 只有一路数据输入时，退化成直通型组合检查。
    if primary_output and dict_first_data_input:

        # 单输入场景只需要检查主输出是否直通该输入。
        return _mock_comb_passthrough_check_lines(primary_output, dict_first_data_input, parity_output)

    # 没有输入端口时，退化成常零输出检查。
    return _mock_comb_zero_check_lines(primary_output, parity_output)

# 判断组合 testbench 是否具备双路 mux 检查条件。
def _mock_has_comb_mux_case(
    primary_output: MockPortSpec | None,
    selector_port: MockPortSpec | None,
    first_data_input: MockPortSpec | None,
    second_data_input: MockPortSpec | None,
) -> bool:
    """
    判断组合布局是否足够执行双路 mux 检查。

    :param primary_output: 可选主数据输出端口。
    :param selector_port: 可选 selector 控制端口。
    :param first_data_input: selector 拉低时的候选输入端口。
    :param second_data_input: selector 拉高时的候选输入端口。
    :return: 端口齐全且两路输入不同则返回 True。
    """

    # 端口缺失时不能执行双路 mux 检查。
    if not (primary_output and selector_port and first_data_input and second_data_input):

        # 返回 False，调用方继续尝试直通或零值兜底检查。
        return False

    # 两个数据输入必须是不同信号，否则 mux 检查没有区分度。
    return str(first_data_input.get("name")) != str(second_data_input.get("name"))

# 生成组合 testbench 原有收尾行。
def _mock_comb_tb_tail_lines() -> list[str]:
    """
    生成组合 testbench 的 PASS 日志和仿真退出语句。

    :param: 无输入参数；组合场景失败检查由上游 helper 先行生成。
    :return: initial 块末尾和 endmodule 行。
    """

    # list_lines 只保存组合路径全部断言通过后的退出脚本。
    list_lines = ['\t\t$display("PASS: self-checking mock testbench completed");']  # 组合自检通过日志

    # 组合检查没有时钟收尾，PASS 后立即结束仿真。
    list_lines.append("\t\t$finish;")

    # initial 块到这里已经完成所有组合断言。
    list_lines.append("\tend")

    # 组合 testbench 模块在 PASS 路径后闭合。
    list_lines.append("endmodule")

    # 返回组合 testbench 通用收尾源码行。
    return list_lines

# 从向量中提取期望值
def _mock_expected_value(vectors: list[dict[str, Any]]) -> int:
    """
    从 mock 向量里抽取一个稳定的整数期望值。

    :param vectors: 供 testbench 使用的 mock 向量列表。
    :return: 非负整数期望值，缺失时回退 1。
    """

    # 空向量场景直接回退到最小默认值。
    if not vectors:

        # 用 1 保持 testbench 示例可运行。
        return 1

    # 只从首个向量中抽取示例期望值。
    first_case = vectors[0]  # 期望值的主来源向量

    # 依次扫描最常见的输出字段分组。
    for group_name in ("expected_outputs", "checkpoints", "outputs", "expected"):

        # 读取当前候选输出组。
        dict_group_values = first_case.get(group_name) if isinstance(first_case.get(group_name), dict) else None  # 当前输出候选集合

        # 从当前输出组里提取第一个可用标量。
        int_group_value = _mock_first_scalar_value(dict_group_values)  # 当前输出组提取到的标量值

        # 命中输出标量时直接返回。
        if int_group_value is not None:

            # 返回当前输出字段组里的稳定期望值。
            return int_group_value

    # 输入字典在输出缺失时承担兜底角色。
    dict_inputs = first_case.get("inputs") if isinstance(first_case.get("inputs"), dict) else None  # 兜底推断使用的输入集合

    # 从输入集合里提取第一个可用标量。
    int_input_value = _mock_first_scalar_value(dict_inputs)  # 输入兜底路径提取到的标量值

    # 命中输入标量时返回兜底期望值。
    if int_input_value is not None:

        # 返回从输入样例里推导出的稳定值。
        return int_input_value

    # 所有字段都未命中时回退默认值。
    return 1

# 从映射值里提取第一个可用于 testbench 的标量。
def _mock_first_scalar_value(values: dict[str, Any] | None) -> int | None:
    """
    从字典值中提取第一个布尔或整数标量。

    :param values: 候选输出或输入映射。
    :return: 命中标量时返回非负整数，否则返回 None。
    """

    # 空映射没有可用的标量候选。
    if not values:

        # 返回 None 让调用方继续尝试其他来源。
        return None

    # 在映射值里寻找可转换的布尔或整数。
    for candidate in values.values():

        # 布尔值转换成单比特整数。
        if isinstance(candidate, bool):

            # 布尔样例按高低电平折成 0 或 1。
            return int(candidate)

        # 整数值回收到非负范围。
        if isinstance(candidate, int):

            # 整数样例统一裁剪到非负范围。
            return max(candidate, 0)

    # 当前映射没有布尔或整数标量。
    return None

# 生成 review 阶段使用的 mock 摘要文本
def _mock_review_text(spec: dict[str, Any]) -> str:
    """
    生成供 review 阶段使用的 Markdown 摘要。

    :param spec: 提供顶层模块名的规范化规格。
    :return: Markdown 格式的 mock review 文本。
    """

    # 读取 review 标题中使用的模块名。
    str_top = str(spec.get("name") or "rtl_module")  # review 标题模块名

    # 返回固定 review 模板。
    return f"""# {str_top} Plan Review

## Interface
The interface maps the declared clock, reset, data, and valid ports directly into the generated RTL and testbench.

## Reset
The reset path drives all state-holding registers to deterministic zero values before stimulus begins.

## Timing And Pipeline
The timing structure uses one visible sequential sampling stage and names the data/valid pipeline registers for review.

## Handshake And FSM
The ready/valid handshake is represented without a hidden FSM; valid propagation is checked in the testbench.

## Width
The data width is parameterized from the spec and compared against the expected value in the testbench checkpoint.

## Synthesis
The RTL avoids simulation-only constructs in synthesizable source files.
It keeps testbench constructs isolated under tb/.

## Testbench Coverage
The testbench instantiates the DUT, drives reset and input stimulus.
It compares value against EXPECTED_VALUE and has $fatal failure paths.

## Risk
The remaining risk is mock-level functional simplicity.
Any real release must compile and simulate the generated artifact when claiming execute readiness.
"""

# 为 mock 文本追加行级语义注释
def _add_mock_line_comments(text: str) -> str:
    """
    为非注释行自动补充 Verilog 行尾说明。

    :param text: 原始 Verilog 文本。
    :return: 带 mock 行尾语义注释的文本。
    """

    # 初始化补注释后的行容器。
    list_rendered_lines: list[str] = []  # 追加语义注释后的源码行

    # 逐行扫描输入文本。
    for line in text.splitlines():

        # 读取去空白后的语义内容。
        stripped_line = line.strip()  # 当前行的语义文本

        # 普通代码行需要追加行尾说明。
        if stripped_line and not stripped_line.startswith("//") and "//" not in line:

            # 追加带语义标签的代码行。
            list_rendered_lines.append(f"{line}\t//{_mock_semantic_comment(stripped_line)}")

        # 注释行和空白行保持原样。
        else:

            # 原样保留当前行。
            list_rendered_lines.append(line)

    # 输出逐行补注释后的文本。
    return "\n".join(list_rendered_lines) + "\n"

# 返回 mock Verilog 常见前缀到中文语义说明的固定映射表。
def _mock_prefix_comments() -> tuple[tuple[str, str], ...]:
    """
    汇总 mock Verilog 常见语句前缀到中文说明文本的映射。

    :param: 无输入参数；映射内容由函数内部固定维护。
    :return: 供逐行补注释流程复用的前缀与中文语义说明元组。
    """

    # 返回最常见 Verilog 前缀及其中文语义说明。
    return (
        ("module ", "模块声明，定义当前 mock 设计单元。"),
        ("endmodule", "结束当前模块，收束当前 mock 设计单元。"),
        ("input", "输入端口声明，接收测试平台或上游驱动。"),
        ("output", "输出端口声明，对外暴露当前设计结果。"),
        ("parameter", "参数声明，约束当前 mock 的位宽或配置。"),
        ("localparam", "局部常量声明，固定内部复位或状态值。"),
        ("reg ", "寄存器声明，保存时序路径中的中间状态。"),
        ("wire ", "导线声明，连接组合结果与观测节点。"),
        ("assign ", "连续赋值语句，把内部信号桥接到目标端口。"),
        ("always@(*)", "组合逻辑块，生成无时钟依赖的结果。"),
        ("always @(*)", "组合逻辑块，生成无时钟依赖的结果。"),
        ("always", "时序逻辑块，驱动寄存器在时钟边沿更新。"),
        ("case", "多路分支选择，根据状态或条件切换路径。"),
        ("endcase", "结束当前 case 分支选择。"),
        ("if", "条件判断语句，区分复位或运行路径。"),
        ("else", "条件兜底分支，承接未命中的运行路径。"),
        ("end", "结束当前 begin-end 代码块。"),
        ("$display", "成功摘要输出，向仿真日志报告结果。"),
        (".", "端口映射语句，把 TB 信号接到 DUT 端口。"),
        ("#", "延时控制语句，给信号传播预留稳定时间。"),
    )

# 生成 mock 行的中文语义标签
def _mock_semantic_comment(stripped: str) -> str:
    """
    为 mock Verilog 行生成简短语义说明。

    :param stripped: 去掉首尾空白后的单行 Verilog 文本。
    :return: 行尾中文语义注释。
    """

    # 读取 mock Verilog 常见前缀与中文语义说明的映射表。
    tuple_prefix_comments = _mock_prefix_comments()  # 逐项匹配的语义前缀表

    # 先按前缀表匹配最常见的源码结构。
    for prefix, comment_text in tuple_prefix_comments:

        # 当前前缀命中时直接返回对应语义。
        if stripped.startswith(prefix):

            # 输出前缀规则定义的语义说明。
            return comment_text

    # 例化头行带括号结尾时补充模块例化语义。
    if stripped.endswith("(") and "_Inst" in stripped:

        # 说明下方即将展开待测模块实例。
        return "模块例化入口，开始连接待测设计。"

    # 未命中已知规则时回退到通用说明。
    return "普通语句，保持 mock 示例的结构可审查。"

# 生成内部输出名
def _internal_output_name(port_name: str) -> str:
    """
    为外部输出端口派生内部寄存器名。

    :param port_name: 外部输出端口名。
    :return: 内部输出寄存器名。
    """

    # 遇到 o_ 前缀时，先剥离对外端口前缀再生成内部寄存器名。
    if port_name.startswith("o_"):

        # 生成更紧凑的内部寄存器名。
        return port_name[2:] + "_o"

    # 其他输出端口直接在原名后追加内部寄存器后缀。
    return port_name + "_o"

# 生成给定宽度的零字面量
def _zero_literal(width: int) -> str:
    """
    根据位宽返回 Verilog 零值字面量。

    :param width: 信号位宽。
    :return: 宽度匹配的零字面量文本。
    """

    # 单比特宽度直接使用 1'b0。
    if width <= 1:

        # 返回单比特零字面量。
        return "1'b0"

    # 多比特宽度返回显式位宽零值。
    return f"{width}'d0"

# 生成可选位宽前缀
def _width_text(signal: MockPortSpec | None) -> str:
    """
    根据端口或信号描述生成位宽文本。

    :param signal: 含 width 字段的信号描述字典。
    :return: 空串或形如 [n:0] 的位宽前缀。
    """

    # 读取信号位宽并做 1 位兜底。
    int_width = int((signal or {}).get("width", 1) or 1)  # 信号位宽

    # 单比特信号无需显式位宽前缀。
    if int_width <= 1:

        # 返回空位宽前缀。
        return ""

    # 多比特信号返回标准位宽文本。
    return f"[{int_width - 1}:0] "

# 构造 mock 响应 manifest
def _build_mock_response_manifest(
    context: GenerationContext,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    生成 mock 响应顶层 manifest。

    :param context: 提供 stage 与 manifest 的阶段上下文。
    :param files: 当前阶段准备返回的文件清单。
    :return: 带 checks 结构的 manifest 字典。
    """

    # 返回带 checks 的 manifest 副本。
    return {
        **context.manifest,
        "files": files,
        "checks": {
            "spec_coverage": [f"Mock provider generated stage {context.stage} artifacts."],
            "verification_plan": ["Mock response includes deterministic verification hooks."],
            "execution_plan": ["Mock response is intended for local workflow tests."],
            "implementation_assessment": ["Mock artifacts satisfy structural contracts for the workflow runner."],
            "reviewability_assessment": ["Mock artifacts keep comments and markers for validation."],
            "assumptions": [],
            "known_limitations": ["Mock provider prioritizes workflow determinism over hardware fidelity."],
        },
    }

# 把 manifest 渲染成 mock 回复头部的 fenced block 行序列。
def _mock_manifest_block_lines(response_manifest: dict[str, Any]) -> list[str]:
    """
    把 manifest 字典渲染成 mock 回复头部的 fenced block 行序列。

    :param response_manifest: 当前阶段准备返回给解析器的 manifest 字典。
    :return: 供最终响应文本直接拼接的 manifest 代码块行列表。
    """

    # 返回 mock 回复头部固定使用的 manifest fenced block。
    return [
        "```json",  # manifest 代码块起始标记
        json.dumps(response_manifest, indent=2, ensure_ascii=False),  # manifest 的 JSON 正文
        "```",  # manifest 代码块结束标记
    ]

# 构造 mock 端口块文本
def _mock_port_block(ordered_ports: list[MockPortSpec]) -> str:
    """
    把排序后的端口列表渲染为模块端口声明块。

    :param ordered_ports: 已按显示顺序排序的端口列表。
    :return: 可直接嵌入模块声明的端口块文本。
    """

    # 初始化模块端口块的文本行。
    list_port_lines: list[str] = []  # 模块端口块中的输出行

    # 全局时钟复位存在时先写分组标题。
    if any(item.get("role") in {"clock", "reset"} for item in ordered_ports):

        # 写入全局端口组标题。
        list_port_lines.append("\t//-----------------全局信号-----------------//")

    # 记录用户接口分组是否已经打开。
    bool_user_group_started = False  # 避免重复写入用户端口标题

    # 逐项渲染排序后的端口声明。
    for index, port in enumerate(ordered_ports):

        # 首次遇到普通业务端口时切换到用户接口分组。
        if port.get("role") not in {"clock", "reset"} and not bool_user_group_started:

            # 已有全局端口时先插入分组空行。
            if list_port_lines:

                # 用一个空行隔开端口分组。
                list_port_lines.append("")

            # 写入用户接口组标题。
            list_port_lines.append("\t//-----------------用户接口-----------------//")

            # 标记用户端口分组已经开始。
            bool_user_group_started = True  # 后续普通端口不再重复写组标题

        # 计算当前端口的位宽前缀。
        str_width_text = _width_text(port)  # 端口声明位宽

        # 计算当前端口行的尾随逗号。
        str_trailing_comma = "," if index < len(ordered_ports) - 1 else ""  # 端口尾随逗号

        # 追加当前端口声明行。
        list_port_lines.append(
            f"\t{port['direction']} {str_width_text}{port['name']}{str_trailing_comma}\t//{_mock_port_comment(port)}"
        )

    # 返回拼接后的端口块文本。
    return "\n".join(list_port_lines)

# 构造输出声明块文本
def _mock_output_decl_block(
    layout: MockPortLayout,
    data_register_width: str,
    valid_register_width: str,
) -> str:
    """
    生成 mock DUT 输出寄存器声明块。

    :param layout: 端口布局对象。
    :param data_register_width: 数据输出寄存器位宽前缀。
    :param valid_register_width: valid 输出寄存器位宽前缀。
    :return: 输出寄存器声明块文本。
    """

    # 先写数据输出寄存器的默认声明。
    str_data_decl_line = (
        f"\treg {data_register_width}{layout.data_output_internal} = DATA_RESET_VALUE;\t//"
        f"{_mock_internal_output_comment(layout.data_output_name)}"
    )  # 数据输出寄存器声明

    # 初始化输出寄存器声明列表。
    list_output_lines = [str_data_decl_line]  # 输出寄存器声明行

    # valid 输出独立时把它插到声明块最前面。
    if layout.has_distinct_valid_output:

        # 生成独立 valid 输出的寄存器声明。
        str_valid_decl_line = (
            f"\treg {valid_register_width}{layout.valid_output_internal} = 1'b0;\t//"
            f"{_mock_internal_output_comment(layout.valid_output_name)}"
        )  # valid 输出寄存器声明

        # 补充 valid 输出寄存器声明。
        list_output_lines.insert(
            0,
            str_valid_decl_line,
        )

    # 返回输出寄存器声明块文本。
    return "\n".join(list_output_lines)

# 构造输出 assign 块文本
def _mock_output_assign_block(layout: MockPortLayout) -> str:
    """
    生成 mock DUT 输出桥接 assign 区域。

    :param layout: 端口布局对象。
    :return: 输出 assign 文本块。
    """

    # 先写数据输出的桥接语句。
    str_data_assign_line = (
        f"\tassign {layout.data_output_name} = {layout.data_output_internal};\t//"
        f"{_mock_output_bridge_comment(layout.data_output_name)}"
    )  # 数据输出桥接语句

    # 初始化输出桥接语句列表。
    list_assign_lines = [str_data_assign_line]  # 输出桥接语句行

    # 记录已经完成桥接的输出端口。
    set_handled_outputs = {layout.data_output_name}  # 已经处理的输出集合

    # valid 输出独立时把它桥接到最前面。
    if layout.has_distinct_valid_output:

        # 生成独立 valid 输出的桥接语句。
        str_valid_assign_line = (
            f"\tassign {layout.valid_output_name} = {layout.valid_output_internal};\t//"
            f"{_mock_output_bridge_comment(layout.valid_output_name)}"
        )  # valid 输出桥接语句

        # 追加 valid 输出桥接语句。
        list_assign_lines.insert(
            0,
            str_valid_assign_line,
        )

        # 把 valid 输出加入已处理集合。
        set_handled_outputs.add(layout.valid_output_name)

    # 对剩余输出补齐固定零值桥接。
    for output_port in layout.outputs:

        # 先抽取输出口名字，便于判断该端口是否已经完成桥接。
        str_output_name = str(output_port.get("name"))  # 剩余输出的名字快照

        # 未处理的输出统一补零。
        if str_output_name and str_output_name not in set_handled_outputs:

            # 计算当前未使用输出对应的零值字面量。
            str_zero_literal = _zero_literal(int(output_port.get("width", 1) or 1))  # 未使用输出复位值

            # 缓存当前输出位宽，供补零注释复用。
            int_output_width = int(output_port.get("width", 1) or 1)  # 当前输出位宽

            # 生成包含端口职责的补零说明，避免多路输出复用模板注释。
            str_unused_output_comment = _mock_unused_output_comment(str_output_name, int_output_width)  # 当前补零输出的行尾说明

            # 追加当前输出的补零桥接语句。
            list_assign_lines.append(
                f"\tassign {str_output_name} = {str_zero_literal};\t//{str_unused_output_comment}"
            )

            # 记录该输出已经处理完成。
            set_handled_outputs.add(str_output_name)

    # 返回输出桥接块文本。
    return "\n".join(list_assign_lines)

# 构造输出处理区域前缀
def _mock_valid_output_block(layout: MockPortLayout) -> str:
    """
    生成输出处理区域横幅，并在需要时插入独立 valid 输出时序块。

    :param layout: 端口布局对象。
    :return: 可嵌入 RTL 的输出处理区域前缀。
    """

    # valid 不独立时，数据输出 always 的说明由主模板紧贴横幅写出。
    if not layout.has_distinct_valid_output:

        # 返回无尾随空行的输出处理区横幅。
        return "\n\t//-------------输出信号处理区域-------------//"

    # 返回完整的 valid 输出时序块，并给下一段 always 说明留出一个空行。
    return f"""
\t//-------------输出信号处理区域-------------//
\t//输出有效标志寄存器更新逻辑
\talways@(posedge {layout.clock_name} or negedge {layout.reset_name})begin
\t\tif({layout.reset_name} == 1'b0)begin
\t\t\t{layout.valid_output_internal} <= 1'b0;\t//复位时清除输出有效标志
\t\tend else if({layout.valid_register_name} == 1'b1)begin
\t\t\t{layout.valid_output_internal} <= 1'b1;\t//输入缓存有效时拉高输出有效
\t\tend else begin
\t\t\t{layout.valid_output_internal} <= 1'b0;\t//无有效输入时拉低输出有效
\t\tend
\tend
"""
