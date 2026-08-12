"""提供控制流节点类别与终止形态判定。"""

# annotations 延后解析 mixin 方法的类型标注。
from __future__ import annotations

# 正则工具用于提取 begin 块标签。
import re

# ControlNodeClassifierMixin 仅承载无状态的控制流形态分类逻辑。
class ControlNodeClassifierMixin:
    """集中维护不依赖解析状态的控制流形态分类。"""

    # 按上下文推导 strict error 的主类别。
    def _control_shape_category(self, context: str) -> str:
        """
        根据控制流上下文返回默认异常类别。

        参数:
            context: 当前控制流所属的 formatter 语义上下文。
        返回:
            str: procedural 路径使用 unsupported_shape，generate 路径使用 generate_normalization_violation。
        """

        # generate 分支需要保留更精确的归一化异常类别。
        if context == "generate":

            # 返回 generate 专用的 strict 分类名。
            return "generate_normalization_violation"

        # 其余上下文统一按普通结构异常处理。
        return "unsupported_shape"

    # 判断当前逻辑行是否命中了调用方指定的终止关键字。
    def _matches_terminator(self, line: str, terminators: set[str]) -> bool:
        """
        判断当前逻辑行是否属于指定的终止关键字集合。

        参数:
            line: 已规范化的当前逻辑行文本。
            terminators: 当前递归层允许匹配的终止关键字集合。
        返回:
            bool: 命中任何终止关键字时返回 True，否则返回 False。
        """

        # 空终止集合表示当前层没有显式结束条件。
        if not terminators:

            # 没有候选终止关键字时一定不命中。
            return False

        # 允许精确命中关键字，也允许关键字后跟空格或标签。
        return any(
            line == token or line.startswith(f"{token} ") or line.startswith(f"{token}:")
            for token in terminators
        )

    # 提取 begin:label 形态里的块标签。
    def _extract_block_label(self, text: str) -> str:
        """
        从 begin:label 头部文本中提取可选块标签。

        参数:
            text: 可能包含 begin:label 的头部文本。
        返回:
            str: 命中 label 时返回标签名，否则返回空字符串。
        """

        # 在 begin 头部里搜索命名块标签。
        match_label = re.search(r"begin\s*:\s*(\w+)", text)  # begin 标签匹配结果

        # 命中时返回标签名，未命中则返回空字符串。
        return match_label.group(1) if match_label else ""
