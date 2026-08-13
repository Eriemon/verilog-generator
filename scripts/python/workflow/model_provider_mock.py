"""工作流 mock provider 包装器。"""

# future annotations 让 provider 包装器保持轻量导入
from __future__ import annotations

# mock 包装器只保留 provider 类和内容组装入口
from typing import Any, Iterator

# 共享上下文与 mock 内容组装 helper
from .model_provider import GenerationContext
from .model_provider_mock_content import (
    _build_mock_response_manifest,
    _mock_file_contents,
    _mock_manifest_block_lines,
    _mock_mode,
)

# mock provider 本体通过内容 helper 生成确定性响应
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
                    if entry.get("kind") == "testbench"  # 优先接受 manifest 显式测试台类型
                    or str(entry["path"]).lower().replace("\\", "/").rsplit("/", 1)[-1].startswith("tb_")  # 识别规范前缀文件名
                    or "_tb." in str(entry["path"]).lower()  # 优先选择测试台文件
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
