"""工作流 command provider 实现与子进程辅助逻辑。"""

# future annotations 让跨模块类型标注保持延迟求值
from __future__ import annotations

# 标准库依赖
import json
import os
import shlex
import subprocess
from typing import Any, Iterator, Sequence, cast

# 共享 provider 合同
from .model_provider import GenerationContext, ModelProviderError

# 命令行 provider 负责调用外部进程并回收响应
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

# 把命令字符串或序列整理为参数列表。
def _normalize_command(command: str | Sequence[str]) -> list[str]:
    """
    把命令字符串或序列整理为参数列表。

    :param command: 原始命令字符串或参数序列。
    :return: 规范化后的命令参数列表。
    :raises ModelProviderError: 当命令为空时抛出。
    """

    # 解析字符串命令。
    if isinstance(command, str):

        # 使用 Windows 友好的切词规则拆分命令。
        list_parts = shlex.split(command, posix=False)  # 归一化后的命令参数

    # 复制已有参数序列。
    else:

        # 转成字符串列表，统一后续处理。
        list_parts = [str(item) for item in command]  # provider 命令参数列表

    # 阻止空命令进入执行阶段。
    if not list_parts:

        # 报告命令模板为空。
        raise ModelProviderError("> ERR: [Python] 模型命令不能为空")

    # 返回归一化后的命令数组。
    return list_parts

# 生成 command provider 需要的环境变量字典。
def _command_env(context: GenerationContext) -> dict[str, str]:
    """
    生成 command provider 需要的环境变量字典。

    :param context: 提供 prompt/response 路径与 manifest 的阶段上下文。
    :return: 继承当前进程并注入工作流键值后的环境字典。
    """

    # 复制当前进程环境。
    dict_env = os.environ.copy()  # provider 进程继承的基础环境

    # 追加工作流路径与上下文 JSON。
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

    # 返回注入上下文后的环境字典。
    return dict_env

# 对命令数组每个参数执行 format_map 展开。
def _expanded_command(command: Sequence[str], context: GenerationContext) -> list[str]:
    """
    对命令数组每个参数执行 format_map 展开。

    :param command: 归一化后的命令参数数组。
    :param context: 提供 attempt、stage 与路径字段的阶段上下文。
    :return: 占位符展开后的命令数组。
    """

    # 逐项展开命令参数。
    return [_expand_part(part, context) for part in command]

# 使用上下文字段展开单个命令参数。
def _expand_part(part: str, context: GenerationContext) -> str:
    """
    使用上下文字段展开单个命令参数。

    :param part: 命令参数模板字符串。
    :param context: 提供格式化字段的阶段上下文。
    :return: 成功展开后的字符串；失败时回退原样返回。
    """

    # 组织可用的格式化变量。
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

    # 尝试展开格式化参数。
    try:

        # 返回 format_map 展开后的参数。
        return part.format_map(dict_values)

    # 对不兼容模板保持原样。
    except Exception:

        # 返回未展开的原始参数。
        return part
