"""生成和识别 formatter 输出中的区域横幅行。"""

# 延迟类型注解求值，保持工具模块导入轻量。
from __future__ import annotations

# dataclass 用于固定横幅样式参数。
from dataclasses import dataclass
import re
import unicodedata

# 声明区域标题来自共享分类策略，防止横幅目录维护第二份枚举。
from ..declaration_region_policy import DECLARATION_REGION_TITLES

# display_width 为横幅生成提供中英文混排宽度基准。
def display_width(text: str) -> int:
    """
    按中英文混排宽度计算横幅可见长度。

    :param text: 需要测量显示宽度的横幅片段。
    :return: 中文全宽字符按 2 计数后的显示宽度。
    """

    # 中英文字符宽度累计值用于横幅左右填充计算。
    int_total_width = 0  # 当前文本累计显示宽度

    # 逐字符识别东亚全宽字符，避免中文标题居中偏移。
    for char in text:

        # W/F 字符按两个半角宽度计算，其余字符按一个宽度计算。
        int_char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1  # 当前字符贡献的显示宽度

        # 累加字符宽度，最终值用于横幅填充长度计算。
        int_total_width += int_char_width  # 累计后的文本显示宽度

    # 返回 formatter 横幅布局需要的可见宽度。
    return int_total_width

# 默认横幅以“主要任务处理区域”为基准，保证所有区域标题长度一致。
BANNER_DISPLAY_WIDTH = display_width("//-------------主要任务处理区域-------------//")  # 区域横幅统一显示宽度

@dataclass(frozen=True)
class BannerStyle:
    """描述单类 formatter 横幅的边界和填充字符。"""

    # total_display_width 约束最终横幅的可见宽度。
    total_display_width: int  # 横幅完整显示宽度

    # prefix 保留 Verilog 单行注释起始标记。
    prefix: str = "//"  # 横幅左边界文本

    # suffix 保留 Verilog 单行注释结束标记。
    suffix: str = "//"  # 横幅右边界文本

    # fill 用于在中文标题两侧补齐横幅。
    fill: str = "-"  # 标题两侧填充字符

# 横幅样式按业务类型索引，当前区域和总线标题共享同一宽度。
BANNER_STYLES = {
    "region": BannerStyle(total_display_width=BANNER_DISPLAY_WIDTH),  # 普通区域横幅样式
    "bus": BannerStyle(total_display_width=BANNER_DISPLAY_WIDTH),  # 总线相关横幅样式
}  # 横幅类型到样式的映射

# formatter 内部区域键映射到用户可读中文标题。
REGION_LABELS = {
    "function_block": "函数定义区域",  # function/task 前的函数区域标题
    "task_block": "任务定义区域",  # task 声明区域标题
    "config_param": "配置参数区域",  # 配置类 parameter/localparam 区域标题
    "state_param": "状态参数区域",  # 状态编码参数区域标题
    **DECLARATION_REGION_TITLES,  # 声明区域标题与 formatter/门禁共享同一权威映射
    "other_assign": "其他信号连线",  # 未分类 assign 区域标题
    "output_assign": "输出信号连线",  # 输出 assign 区域标题
    "output_always": "输出信号处理区域",  # 输出 always 块区域标题
    "state_machine": "状态机区域",  # 状态机 always 块区域标题
    "state_task": "状态任务处理区域",  # 状态处理 task 区域标题
    "main_task": "主要任务处理区域",  # 主任务逻辑区域标题
    "generate_block": "生成块区域",  # generate 块区域标题
    "parameter_check": "参数检查区域",  # 参数检查 initial/assert 区域标题
    "initial_block": "初始化区域",  # initial 块启动赋值区域标题
    "instance_block": "模块实例化区域",  # 子模块实例化区域标题
}  # formatter 分类键到中文横幅标题的映射

# make_banner 按固定宽度生成与旧 formatter 输出兼容的注释横幅。
def make_banner(title: str, kind: str = "region") -> str:
    """
    根据区域标题和横幅类型生成居中的 Verilog 注释横幅。

    :param title: 需要显示在横幅中间的中文区域标题。
    :param kind: 横幅样式类型，当前支持 region 和 bus。
    :return: 与 formatter 既有输出兼容的单行 Verilog 注释横幅。
    :raises ValueError: 标题过长或横幅类型不存在时抛出。
    """

    # BannerStyle 类型承载总宽度、边界和填充字符。
    banner_style_banner_style: BannerStyle = BANNER_STYLES[kind]  # 当前横幅类型对应的样式参数

    # 内部宽度扣除左右注释边界，仅用于放置标题和填充线。
    int_inner_width = (
        banner_style_banner_style.total_display_width  # 横幅目标总宽度
        - display_width(banner_style_banner_style.prefix)  # 左侧注释边界占用宽度
        - display_width(banner_style_banner_style.suffix)  # 右侧注释边界占用宽度
    )  # 标题和填充可用宽度

    # 标题宽度按中英文混排宽度计算。
    int_title_width = display_width(title)  # 当前横幅标题显示宽度

    # 标题过长时拒绝生成错位横幅。
    if int_title_width > int_inner_width:

        # 报错包含 kind 和标题，方便调用方定位是哪类横幅溢出。
        raise ValueError(f"> ERR: [Python] 横幅标题超过 {kind} 样式允许宽度: {title}")

    # padding 是标题两侧一共需要补齐的显示宽度。
    int_padding_width = int_inner_width - int_title_width  # 标题两侧总填充宽度

    # 左侧填充向下取整，奇数宽度差交给右侧补齐。
    int_left_padding = int_padding_width // 2  # 标题左侧填充宽度

    # 右侧填充补足剩余宽度，保证总宽度稳定。
    int_right_padding = int_padding_width - int_left_padding  # 标题右侧填充宽度

    # 返回 Verilog 注释形式的完整横幅行。
    return (
        f"{banner_style_banner_style.prefix}"  # 左侧 Verilog 注释边界
        f"{banner_style_banner_style.fill * int_left_padding}"  # 标题左侧填充线
        f"{title}"  # 中间区域标题
        f"{banner_style_banner_style.fill * int_right_padding}"  # 标题右侧填充线
        f"{banner_style_banner_style.suffix}"  # 右侧 Verilog 注释边界
    )

# 预生成区域横幅，避免渲染阶段重复计算标题宽度。
REGION_TITLES = {key: make_banner(label, "region") for key, label in REGION_LABELS.items()}  # 区域键到完整横幅行的映射

# is_banner_line 用于渲染和清理阶段识别既有横幅行。
def is_banner_line(line: str) -> bool:
    """
    判断一行文本是否是 formatter 生成的区域横幅。

    :param line: 待识别的单行 RTL 文本。
    :return: 行文本具备 formatter 横幅边界和填充线时返回 True。
    """

    # 判断前先去除外侧空白，兼容缩进后的注释横幅。
    str_stripped_line = line.strip()  # 去除缩进后的候选横幅行

    # 横幅必须是注释包裹且包含填充线。
    return (
        str_stripped_line.startswith("//")  # 横幅左边界匹配结果
        and str_stripped_line.endswith("//")  # 横幅右边界匹配结果
        and "-" in str_stripped_line  # 横幅填充线存在性
    )

# extract_banner_title 在重排区域时复用已有横幅标题。
def extract_banner_title(line: str) -> str:
    """
    从 formatter 横幅或普通注释中提取标题文本。

    :param line: formatter 横幅行、普通 Verilog 注释行或裸标题文本。
    :return: 去除注释边界和填充线后的区域标题。
    """

    # 先标准化空白，避免缩进影响横幅边界判断。
    str_stripped_line = line.strip()  # 去除外侧空白后的输入行

    # 非标准横幅只剥离可选注释前缀，保留原始说明文本。
    if not is_banner_line(str_stripped_line):

        # 普通注释行返回注释体，裸文本按原样返回。
        return str_stripped_line[2:].strip() if str_stripped_line.startswith("//") else str_stripped_line

    # inner 是去掉左右注释边界后的横幅主体。
    str_inner_title = str_stripped_line[2:-2]  # 带填充线的横幅标题主体

    # 移除左侧填充线，只保留标题和右侧填充线。
    str_inner_title = re.sub(r"^-+", "", str_inner_title)  # 去除左侧横幅填充线后的标题主体

    # 清掉标题后的尾部横线，使返回值只包含区域名称。
    str_inner_title = re.sub(r"-+$", "", str_inner_title)  # 可直接参与区域匹配的标题文本

    # 返回清理后的中文区域标题。
    return str_inner_title.strip()
