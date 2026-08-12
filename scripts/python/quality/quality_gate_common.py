"""承载 quality gate 拆分后复用的常量与通用 helper。"""

# 延迟类型注解求值，避免质量门模块导入时解析复杂联合类型。
from __future__ import annotations

# 标准库依赖保持质量门可在无第三方包环境运行。
import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 双语 header 锚点直接复用共享字面合同。
from ..header_contract import HEADER_CHINESE_SEPARATOR, HEADER_ENGLISH_SEPARATOR

# formatter_ast 是唯一结构化 Verilog 解析入口，正则只承担行级样式判断。
from .formatter_backend.banners import display_width
from .formatter_ast import build_ast_report_for_path, iter_verilog_sources, read_verilog_source
from scripts.python.validation.rulebook import load_verilog_rulebook

# common 兼容路径复用统一低有效复位名称语义。
from .reset_name_roles import is_low_active_reset_name

# ensure_runtime_visible_target_path 在 CLI 写报告前校验目标路径对当前运行宿主可见。
def ensure_runtime_visible_target_path(path_target: Path) -> Path:
    """
    确认 CLI 目标路径对当前 Python 运行宿主可见。

    :param path_target: 用户传入的目标文件或目录路径。
    :return: 规范化后的可见目标路径。
    :raises FileNotFoundError: 当目标不存在或当前运行宿主不可见时抛出。
    """

    # expanduser 先展开用户目录语法，避免后续存在性检查漏判。
    path_candidate = path_target.expanduser()  # 当前 CLI 目标的展开后路径

    # 目标真实存在时返回规范化后的路径对象，供后续 gate 复用。
    if path_candidate.exists():

        # resolve 统一 root 字段和后续错误消息里的路径形态。
        return path_candidate.resolve()

    # 绝对路径缺失时，额外提示不要依赖跨宿主路径映射。
    if path_candidate.is_absolute():

        # 缺失的绝对路径常见于把另一台宿主的盘符路径直接传给当前解释器。
        raise FileNotFoundError(
            "> ERR: [Python] Target path is not visible to the current Python runtime: "
            f"{path_target}. Please use a path visible to the current Python runtime."
        )

    # 相对路径缺失时直接说明目标不存在，避免误导成跨宿主问题。
    raise FileNotFoundError(f"> ERR: [Python] Target path does not exist: {path_target}.")

# _lines 去掉多行常量中的空白行，降低常量表维护噪音。
def _lines(text: str) -> tuple[str, ...]:
    """
    把多行配置文本转换成稳定的字符串元组。

    :param text: 多行配置文本。
    :return: 去除空白后的不可变字符串元组。
    """

    # list_items 保留非空条目的原始顺序，供规则表直接复用。
    list_items = [str_item.strip() for str_item in text.splitlines() if str_item.strip()]  # 非空配置条目

    # 返回不可变元组，避免运行期规则表被调用方误改。
    return tuple(list_items)

# _regex_lines 把规则文本编译为正则元组，保留原先的匹配口径。
def _regex_lines(text: str, *, flags: int = 0) -> tuple[re.Pattern[str], ...]:
    """
    把多行正则文本转换成编译后的正则元组。

    :param text: 多行配置文本。
    :param flags: 传递给 re.compile 的正则标志。
    :return: 编译后的正则表达式元组。
    """

    # tuple_patterns 逐项编译 Vitis 端口和占位注释等规则。
    tuple_patterns = tuple(re.compile(str_pattern, flags) for str_pattern in _lines(text))  # 编译后的规则模式

    # 返回编译结果，供高频规则检查复用。
    return tuple_patterns

# _configured_region_keywords 优先从 rulebook 读取区域顺序，失败时回退到本地兜底表。
def _configured_region_keywords() -> tuple[str, ...]:
    """
    返回当前质量门应使用的区域横幅顺序。

    参数:
        无业务参数；函数直接读取 rulebook 或内置兜底顺序。
    返回:
        先使用 rulebook 的区域顺序；读取失败时回退到内置兜底顺序。
    """

    # 优先使用机器真源中的区域标签顺序。
    try:

        # tuple_region_keywords 直接复用 rulebook 提供的区域标签。
        tuple_region_keywords = tuple(load_verilog_rulebook().region_labels)  # rulebook 区域横幅顺序

        # rulebook 非空时直接作为当前区域顺序。
        if tuple_region_keywords:

            # 返回 rulebook 中声明的最终区域顺序。
            return tuple_region_keywords

    # rulebook 异常时质量门改走兜底顺序，并由 VG059 单独报告。
    except (AttributeError, OSError, TypeError, ValueError):

        # rulebook 读取失败时改走本地兜底顺序，具体漂移由 VG059 单独报告。
        return REGION_KEYWORDS

    # 规则源不可用时，回退到本地保守顺序。
    return REGION_KEYWORDS

# _runtime_message_prefixes 读取 `$display` 人类可读前缀与机器 transcript 豁免前缀。
def _runtime_message_prefixes() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    返回 Verilog 运行时 `$display` 前缀合同。

    参数:
        无业务参数；函数直接返回人类可读前缀与机器 transcript 豁免前缀。
    返回:
        二元组，依次为人类可读前缀集合与机器 transcript 前缀集合。
    """

    # 先准备本地兜底前缀，rulebook 缺失时仍能稳定执行。
    tuple_human_prefixes, tuple_machine_prefixes = (HUMAN_READABLE_VERILOG_PREFIXES, MACHINE_RUNTIME_MESSAGE_PREFIXES)  # 人类可读前缀与机器 transcript 豁免前缀的本地兜底值。

    # 读取 rulebook 中的 runtime_messages 配置。
    try:

        # dict_runtime_messages 汇总运行时消息相关的机器真源配置。
        dict_runtime_messages = load_verilog_rulebook().raw.get("runtime_messages") or {}  # runtime_messages 规则分区

        # 先取出 rulebook 声明的人类可读前缀原始列表。
        list_rulebook_human = dict_runtime_messages.get("human_readable_display_prefixes") or []  # rulebook 中声明的人类可读前缀原始列表。

        # 再把原始列表归一化为字符串元组，供后续合同覆盖逻辑复用。
        tuple_rulebook_human = tuple(str(item) for item in list_rulebook_human)  # rulebook 中声明的人类可读 display 前缀。

        # 先取出 rulebook 声明的机器 transcript 前缀原始列表。
        list_rulebook_machine = dict_runtime_messages.get("machine_transcript_prefixes") or []  # rulebook 中声明的机器 transcript 前缀原始列表。

        # 再把原始列表归一化为字符串元组，供机器输出豁免逻辑复用。
        tuple_rulebook_machine = tuple(str(item) for item in list_rulebook_machine)  # rulebook 中声明的机器 transcript 前缀。

        # 非空人类可读配置优先作为当前合同。
        if tuple_rulebook_human:

            # 使用 rulebook 中的人类可读 display 前缀。
            tuple_human_prefixes = tuple_rulebook_human  # 用 rulebook 中的人类可读 display 前缀覆盖本地兜底集合。

        # 非空机器 transcript 配置优先作为当前豁免合同。
        if tuple_rulebook_machine:

            # 使用 rulebook 中的机器 transcript 前缀。
            tuple_machine_prefixes = tuple_rulebook_machine  # 用 rulebook 中的机器 transcript 前缀覆盖本地豁免集合。

    # rulebook 读取失败时继续使用兜底前缀，并由 VG059 单独记录。
    except (AttributeError, OSError, TypeError, ValueError):

        # 运行时消息规则本身不在此处重复上报 VG059，直接保留兜底前缀。
        return tuple_human_prefixes, tuple_machine_prefixes

    # 返回当前生效的人类可读与机器 transcript 前缀合同。
    return tuple_human_prefixes, tuple_machine_prefixes

# 中文检测正则用于 comment_language=zh 时确认注释不是纯英文兜底。
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")  # 中文字符检测模式

# Verilog 标识符基础格式检查保留旧质量门命名规则。
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")  # 小写 snake 风格标识符模式

# localparam 等常量名使用全大写格式。
UPPER_IDENTIFIER_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")  # 大写常量标识符模式

# 协议分组 token 集合用于构造 header 分组正则。
PORT_GROUP_PROTOCOL_NAMES = "AXI|AXIS|APB|AHB|Wishbone|UART|SPI|I2C|GMII|RGMII"  # 已知协议接口名称集合

# 协议接口分组正则文本覆盖常见总线或外设接口横幅。
PORT_GROUP_PROTOCOL_REGEX = "/" + "/" + f".*(?:{PORT_GROUP_PROTOCOL_NAMES}).*接口"  # 协议接口分组正则文本

# 编译后的协议接口分组正则供 module header 扫描复用。
PORT_GROUP_PROTOCOL_PATTERN = re.compile(PORT_GROUP_PROTOCOL_REGEX, re.IGNORECASE)  # 协议接口分组注释模式

# 通用接口分组正则覆盖非协议专名的接口横幅。
PORT_GROUP_GENERIC_PATTERN = re.compile("/" + "/" + ".*接口", re.IGNORECASE)  # 通用接口分组注释模式

# FSM next-state 深语义规则复用已编译正则，避免字符串规则漂移。
FSM_CASE_KEYWORD_PATTERN = re.compile(r"\b" + "case" + r"\b")  # case 关键字识别模式

# default 分支正则用于确认 next-state case 存在兜底迁移。
FSM_DEFAULT_BRANCH_PATTERN = re.compile(r"\b" + "default" + r"\s*:")  # default 分支识别模式

# next-state 默认保持允许兼容 <= 与历史 = 两种赋值写法。
FSM_STATE_NEXT_ASSIGN_PATTERN = re.compile(r"\bstate_next\s*(?:<=|=)")  # state_next 赋值识别模式

# 三段式默认保持允许 <= 或 = 把 state_current 复制到 state_next。
FSM_STATE_NEXT_HOLD_PATTERN = re.compile(r"\bstate_next\s*(?:<=|=)\s*state_current\s*;")  # 默认保持赋值识别模式

# next-state 主状态分派固定使用 case(state_current)。
FSM_STATE_CASE_PATTERN = re.compile(r"^\s*case\s*\(\s*state_current\s*\)\s*$")  # 状态分派 case 模式

# 状态标签与 default 分支必须使用 LABEL:begin 结构。
FSM_CASE_BRANCH_BEGIN_PATTERN = re.compile(r"^\s*(?:ST_[A-Za-z0-9_]+|default)\s*:\s*begin\b")  # FSM 状态分支标签模式

# 条件分支链在 Erie 紧凑风格下以 begin/end 成对出现。
FSM_IF_BEGIN_PATTERN = re.compile(r"^\s*if\s*\(.*\)\s*begin\s*$")  # if 分支起始模式

# 兼容 Erie 紧凑风格里的同一行续接 else-if 写法与独立 else-if 写法。
FSM_ELSE_IF_BEGIN_PATTERN = re.compile(r"^\s*(?:end\s+)?else\s+if\s*\(.*\)\s*begin\s*$")  # else-if 分支延续模式

# 终止 else 同样兼容 Erie 紧凑写法与独立 else begin 写法。
FSM_ELSE_BEGIN_PATTERN = re.compile(r"^\s*(?:end\s+)?else\s+begin\s*$")  # 终止 else 分支模式

# 紧凑 begin/end 风格下的普通 end 行用于关闭 if/case item 块。
FSM_PLAIN_END_PATTERN = re.compile(r"^\s*end\s*$")  # 单独 end 行模式

# AXIS_PORT_TOKENS 覆盖 AXI Stream 常见端口名片段。
AXIS_PORT_TOKENS = _lines(  # AXIS 端口判定 token 集合
    """
    tdata
    tkeep
    tstrb
    tvalid
    tready
    tlast
    tuser
    tid
    tdest
    """
)

# AXIS_DATA_TOKENS 标识 AXI Stream 数据承载信号。
AXIS_DATA_TOKENS = _lines(  # AXIS 数据拍载荷与字节使能 token 集合
    """
    tdata
    tkeep
    tstrb
    """
)

# AXIS_CONTROL_TOKENS 标识 AXI Stream 握手、帧尾和用户信号。
AXIS_CONTROL_TOKENS = _lines(  # AXIS 握手和旁带 token 集合
    """
    tvalid
    tready
    tlast
    tuser
    tid
    tdest
    """
)

# APB_PORT_TOKENS 覆盖 APB 标准端口和复位时钟端口名片段。
APB_PORT_TOKENS = _lines(  # APB 标准端口判定 token 集合
    """
    pclk
    preset
    prst
    paddr
    psel
    penable
    pwrite
    pwdata
    pstrb
    pprot
    prdata
    pready
    pslverr
    """
)

# APB_REQUEST_TOKENS 标识 APB master 发起的请求侧信号。
APB_REQUEST_TOKENS = _lines(  # APB master 请求侧 token 集合
    """
    paddr
    psel
    penable
    pwrite
    pwdata
    pstrb
    pprot
    """
)

# APB_RESPONSE_TOKENS 标识 APB slave 返回的响应侧信号。
APB_RESPONSE_TOKENS = _lines(  # APB slave 响应侧 token 集合
    """
    prdata
    pready
    pslverr
    """
)

# REGION_KEYWORDS 保留 rulebook 读取失败时的区域横幅兜底顺序。
REGION_KEYWORDS = (  # formatter 区域横幅兜底顺序
    "函数区域",  # function 声明区域横幅
    "任务区域",  # task 过程定义区域横幅
    "配置参数区域",  # 参数声明区域横幅
    "状态参数区域",  # FSM 状态 localparam 区域横幅
    "模块实例化信号",  # 子模块连接信号声明区域横幅
    "计数信号",  # cnt_ 信号声明区域横幅
    "状态机信号",  # state_current/state_next 声明区域
    "寄存器信号",  # reg_ 数据保持信号区域
    "标志信号",  # flag_ 握手和完成标记区域
    "编码信号",  # enc_ 编码中间量区域
    "译码信号",  # dec_ 译码中间量区域
    "其他信号",  # 未归类内部信号区域横幅
    "输出信号",  # 输出桥接内部信号区域横幅
    "其他信号连线",  # 普通 assign 连线区域横幅
    "输出信号连线",  # 输出 assign bridge 区域横幅
    "输出信号处理区域",  # 输出寄存处理 always 区域横幅
    "状态机区域",  # FSM 主状态转移区域横幅
    "状态任务处理区域",  # FSM 状态输出任务区域横幅
    "主要任务处理区域",  # 主业务逻辑 always 区域横幅
    "生成块区域",  # generate 结构区域横幅
    "初始化区域",  # initial 初始化区域横幅
    "模块实例化区域",  # 子模块实例化区域横幅
    "参数检查区域",  # 参数防护代码区域横幅
)

# HUMAN_READABLE_VERILOG_PREFIXES 是 rulebook 缺失时的人类可读 `$display` 前缀兜底集合。
HUMAN_READABLE_VERILOG_PREFIXES = (" > INFO: [Verilog]", " > WARNING: [Verilog]", " > ERR: [Verilog]")  # `$display` 人类可读输出前缀兜底值。

# MACHINE_RUNTIME_MESSAGE_PREFIXES 是 rulebook 缺失时的机器 transcript 前缀兜底集合。
MACHINE_RUNTIME_MESSAGE_PREFIXES = ("VERILOG-GEN-RESULT",)  # 机器协议输出前缀兜底值

# DISPLAY_STRING_PATTERN 抓取 `$display("...")` 中第一个字符串字面量，供运行时消息合同检查复用。
DISPLAY_STRING_PATTERN = re.compile(  # `$display` 首字符串字面量匹配模式
    r"\$display\s*\(\s*\"((?:[^\"\\]|\\.)*)\"",  # 捕获 display 的首个双引号字符串参数
    re.DOTALL,  # 允许 display 调用跨多行排版
)

# PARAM_SIGNAL_GROUP_REGIONS 只覆盖需要显式小分组注释的聚合定义区域。
PARAM_SIGNAL_GROUP_REGIONS = (  # 分组注释强制区域
    "其他信号",  # 泛化信号区通常混合多个语义簇
    "输出信号",  # 输出桥接区需要按接口或功能显式分组
)

# BLOCK_LEADING_COMMENT_COLLECTIONS 定义需要前导说明的过程块集合。
BLOCK_LEADING_COMMENT_COLLECTIONS = (  # 过程块前导注释规则表
    ("always", "Always block", "comments.always"),  # always 语句块说明入口
    ("initials", "Initial block", "comments.initial"),  # initial 初始化说明入口
    ("functions", "Function block", "comments.function"),  # function 定义说明入口
    ("tasks", "Task block", "comments.task"),  # task 过程说明入口
    ("generates", "Generate block", "comments.generate"),  # generate 结构说明入口
)

# PROCEDURAL_ASSIGNMENT_COLLECTIONS 定义需要扫描内部赋值的过程块集合。
PROCEDURAL_ASSIGNMENT_COLLECTIONS = (  # 过程赋值覆盖的 AST 集合
    "always",  # 时序和组合 always 内赋值
    "initials",  # initial 测试或初始化赋值
    "functions",  # function 局部计算赋值
    "tasks",  # task 步骤式过程赋值
    "generates",  # generate 展开结构内赋值
)

# PROCEDURAL_ASSIGNMENT_IGNORED_PREFIXES 定义过程赋值扫描时跳过的语句前缀。
PROCEDURAL_ASSIGNMENT_IGNORED_PREFIXES = (  # 非过程赋值起始关键字
    "assign ",  # 连续赋值由 assign 规则负责
    "parameter ",  # 参数定义由结构化注释规则负责
    "localparam ",  # 局部参数定义由结构化注释规则负责
    "reg ",  # 寄存器声明由信号注释规则负责
    "wire ",  # wire 网络声明走结构化信号规则
    "logic ",  # logic 兼容声明走结构化信号规则
    "integer ",  # integer 计数变量声明不按赋值处理
    "real ",  # real 仿真变量声明不按赋值处理
    "input ",  # input 方向声明由端口区域规则负责
    "output ",  # output 方向声明交给端口桥接规则
    "inout ",  # inout 双向声明交给端口区域规则
    "for(",  # 紧凑 for 初始化不是独立赋值语句
    "for (",  # 空格 for 初始化同样不按赋值处理
)

# Vitis wrapper 端口由工具链固定命名，质量门默认豁免方向前缀。
VITIS_PORT_PATTERNS = (  # Vitis wrapper 固定端口名模式
    re.compile(r"^ap_clk$"),  # Vitis 控制时钟端口
    re.compile(r"^ap_rst_n$"),  # Vitis 控制复位端口
    re.compile(r"^interrupt$"),  # Vitis 中断输出端口
    re.compile(r"^s_axi_control_"),  # Vitis AXI-Lite 控制端口前缀
    re.compile(r"^m_axi_.*_"),  # Vitis m_axi bundle 端口前缀
)

# 英文文件头字段必须使用用户样例中的 Referrences 拼写。
REQUIRED_ENGLISH_HEADER_FIELDS = (  # 标准英文文件头必填字段
    "Company",  # 英文版权归属字段
    "Engineer",  # 英文开发人员字段
    "Create Date",  # 英文创建日期字段
    "Design Name",  # 英文设计名称字段
    "Module Name",  # 英文模块名称字段
    "Description",  # 英文模块说明字段
    "Simulations",  # 英文仿真工程字段
    "Referrences",  # 英文参考资料字段固定拼写
    "Dependencies",  # 英文依赖文件字段
    "Version",  # 英文当前版本字段
    "Revision Date",  # 英文修订日期字段
    "History",  # 英文修订历史字段
)

# 中文文件头字段用于确认双语头部完整。
REQUIRED_CHINESE_HEADER_FIELDS = (  # 标准中文文件头必填字段
    "版权归属",  # 中文版权归属字段
    "开发人员",  # 中文开发人员字段
    "创建日期",  # 中文创建日期字段
    "设计名称",  # 中文设计名称字段
    "模块名称",  # 中文模块名称字段
    "模块说明",  # 中文模块说明字段
    "仿真工程",  # 中文仿真工程字段
    "参考资料",  # 中文参考资料字段
    "依赖文件",  # 中文依赖文件字段
    "当前版本",  # 中文当前版本字段
    "修订日期",  # 中文修订日期字段
    "修订历史",  # 中文修订历史字段
)

# Verilog 文件头版本必须保留 Vx.y 形式，禁止交付时退化为裸数字或多段版本。
HEADER_VERSION_PATTERN = re.compile(r"^V\d+\.\d+$")  # 文件头当前版本格式

# GENERIC_COMMENT_PATTERNS 捕获模板占位或示例残留注释。
GENERIC_COMMENT_PATTERNS = (  # 占位注释识别模式
    re.compile("端口信号注释", re.IGNORECASE),  # 模板端口注释占位词
    re.compile("参数解释说明中文", re.IGNORECASE),  # 模板参数注释占位词
    re.compile("默认值,?参数解释", re.IGNORECASE),  # 模板默认值说明占位词
    re.compile("必须要有的注释", re.IGNORECASE),  # 强制注释提示残留词
    re.compile("此模板未使用", re.IGNORECASE),  # 未使用模板残留词
    re.compile("占位", re.IGNORECASE),  # 中文占位词
    re.compile("placeholder", re.IGNORECASE),  # 英文占位词
)

# COMMENT_REUSE_MIN_CJK_CHARS 避免短标签式注释误入重复检测。
COMMENT_REUSE_MIN_CJK_CHARS = 4  # 实体注释参与 VG066 所需的最小中文字符数

# COMMENT_REUSE_SIMILARITY_THRESHOLD 对明显换皮注释给出保守阻断。
COMMENT_REUSE_SIMILARITY_THRESHOLD = 0.94  # 近似重复实体注释相似度阈值

# DUPLICATE_SIGNAL_PREFIXES 捕获生成器重复拼接的常见信号前缀。
DUPLICATE_SIGNAL_PREFIXES = (  # Verilog 信号命名中禁止重复出现的前缀片段
    "reg_reg_",  # 寄存器前缀重复
    "cnt_cnt_",  # 计数器前缀重复
    "flag_flag_",  # 标志前缀重复
    "enc_enc_",  # 编码前缀重复
    "dec_dec_",  # 译码前缀重复
    "state_state_",  # 状态机前缀重复
)

# DUPLICATE_PARAMETER_PREFIXES 捕获参数命名中的重复分类前缀。
DUPLICATE_PARAMETER_PREFIXES = (  # 参数命名不允许的重复前缀
    "C_C_",  # module parameter 前缀重复
    "ST_ST_",  # 状态参数前缀重复
)

# 供 `_region_banner_anchor_column` 复用的拆分 helper，专门处理区域横幅右侧注释边界的显示列。
def _region_banner_anchor_column(str_line: str) -> int | None:
    """
    返回区域横幅右侧注释边界的显示列。

    :param str_line: 当前源码行。
    :return: 横幅右侧 // 的显示列；非横幅时返回 None。
    """

    # str_stripped 用于识别标准区域横幅。
    str_stripped = str_line.strip()  # 去除缩进后的横幅候选

    # 区域横幅必须是 //...// 包裹并含有横线填充。
    if not (str_stripped.startswith("//") and str_stripped.endswith("//") and "-" in str_stripped):

        # 普通前置注释或文件头不提供区域锚点。
        return None

    # 横幅必须存在左右两个 //，否则不能形成右侧锚点。
    int_anchor_index = str_line.rfind("//")  # 原始行中最右侧 // 起点

    # rfind 命中左侧 // 时说明没有右侧边界。
    if int_anchor_index <= str_line.find("//"):

        # 非双边界横幅不参与区域注释对齐。
        return None

    # 返回右侧 // 之前文本的显示宽度。
    return _display_width_with_tabs(str_line[:int_anchor_index])

# 供 `_has_line_span` 复用的拆分 helper，专门处理formatter AST 条目是否包含可信源码行号范围。
def _has_line_span(dict_item: dict[str, Any]) -> bool:
    """
    判断 formatter AST 条目是否包含可信源码行号范围。

    :param dict_item: formatter AST 中的结构条目。
    :return: 行号范围存在且顺序可信时返回 True。
    """

    # int_line_start 和 int_line_end 使用安全转换，避免外部数据污染。
    int_line_start = _as_line(dict_item.get("line_start"))  # AST 条目起始行

    # int_line_end 保留条目结束行，用于确认 span 顺序。
    int_line_end = _as_line(dict_item.get("line_end"))  # AST 条目结束行

    # bool_span_present 确认起止行都来自 formatter AST。
    bool_span_present = int_line_start is not None and int_line_end is not None  # span 行号存在标志

    # bool_span_ordered 确认源码行号范围可用于区域归属判断。
    bool_span_ordered = bool_span_present and int_line_start >= 1 and int_line_end >= int_line_start  # span 顺序可信标志

    # 返回最终可信性判断。
    return bool_span_ordered

# 供 `_span_item_label` 复用的拆分 helper，专门处理AST 条目可读标签。
def _span_item_label(dict_item: dict[str, Any]) -> str:
    """
    返回 AST 条目可读标签。

    :param dict_item: formatter AST 中的结构条目。
    :return: 用于诊断展示的条目标签。
    """

    # 常见结构使用不同字段保存实体名，按稳定优先级读取。
    for str_key in ("name", "lhs", "instance_name", "header", "module_name"):

        # 读取第一个非空标签。
        str_value = str(dict_item.get(str_key) or "").strip()  # 候选标签文本

        # 非空标签直接返回。
        if str_value:

            # 返回用于诊断展示的结构标签。
            return str_value

    # 没有可读字段时给出固定兜底标签。
    return "unknown"

# 供 `_module_output_ports` 复用的拆分 helper，专门处理收集 formatter AST 中的顶层 output 端口名。
def _module_output_ports(dict_module: dict[str, Any]) -> set[str]:
    """
    收集 formatter AST 中的顶层 output 端口名。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: output 端口名集合。
    """

    # set_output_ports 提供 output bridge 与重声明检查的共同判定基准。
    set_output_ports = {  # module 级 output bridge 候选端口集合
        str(dict_port.get("name") or "")  # 作为 bridge 左值候选的端口名
        for dict_port in dict_module.get("ports", []) or []  # formatter 解析出的 module 端口条目
        if str(dict_port.get("direction") or "") == "output"  # output 方向端口才需要桥接区域检查
    }

    # 返回 output 端口集合。
    return set_output_ports

# 供 `_assignment_lhs_label` 复用的拆分 helper，专门处理提取当前赋值或实例关联行的展示名称。
def _assignment_lhs_label(str_line: str) -> str:
    """
    提取当前赋值或实例关联行的展示名称。

    :param str_line: 当前源码行。
    :return: 赋值左值或实例 formal 名称。
    """

    # str_code 去掉同线注释并剥离收尾空白。
    str_code = _strip_line_comment(str_line).strip()  # 赋值或实例关联主体

    # 实例关联优先返回 formal 名称。
    match_association = re.match(  # 实例 formal 名称匹配结果
        r"^\.(?P<formal>[A-Za-z_][A-Za-z0-9_]*)\s*\(",  # 点名关联 formal 正则
        str_code,  # 实例关联候选主体
    )

    # 命中实例关联时返回 .formal 展示。
    if match_association:

        # formal 名称能直接指向缺注释的连接。
        return "." + match_association.group("formal")

    # 非阻塞赋值优先切分 <=。
    if "<=" in str_code:

        # 返回非阻塞赋值左值。
        return str_code.split("<=", 1)[0].strip()

    # 阻塞赋值切分首个单等号。
    match_blocking = re.match(  # 阻塞赋值左值匹配结果
        r"^(?P<lhs>.+?)(?<![<>=!])=(?!=)",  # 阻塞赋值左值正则
        str_code,  # 赋值语句主体
    )

    # 命中阻塞赋值时返回左值。
    if match_blocking:

        # 返回阻塞赋值左侧表达式。
        return match_blocking.group("lhs").strip()

    # 兜底返回去注释源码，便于诊断定位。
    return str_code

# 供 `_previous_line_is_definition` 复用的拆分 helper，专门处理当前定义上一行是否仍是参数或信号定义。
def _previous_line_is_definition(list_lines: list[str], int_line_no: int) -> bool:
    """
    判断当前定义上一行是否仍是参数或信号定义。

    :param list_lines: 当前 Verilog 源码行列表。
    :param int_line_no: 当前定义的一基行号。
    :return: 上一行是同组定义时返回 True。
    """

    # 第一行不可能有前一条定义。
    if int_line_no <= 1:

        # 没有上一行。
        return False

    # str_previous_line 用来判断当前定义是否延续上一组。
    str_previous_line = list_lines[int_line_no - 2]  # 定义分组连续性锚点

    # 空行表示新分组开始，不能继承上一组。
    if not str_previous_line.strip():

        # 空行断开分组。
        return False

    # 判断上一行是否也是参数或信号定义。
    return _is_definition_statement_line(str_previous_line)

# 识别 parameter、localparam、reg、wire 等定义行，供分组规则复用。
def _is_definition_statement_line(str_line: str) -> bool:
    """
    判断当前源码行是否是参数或信号定义。

    :param str_line: 当前源码行。
    :return: 是参数或信号定义时返回 True。
    """

    # str_code 去掉同线注释后识别声明关键字。
    str_code = _strip_line_comment(str_line).strip()  # 定义关键字判定源码片段

    # 参数和信号定义关键字覆盖用户列出的类型。
    return re.match(
        r"^(?:localparam|parameter|reg|wire|logic|integer|real|input|output|inout|genvar)\b",
        str_code,
    ) is not None

# 供 `_is_pure_line_comment` 复用的拆分 helper，专门处理当前行是否只有缩进和 // 注释。
def _is_pure_line_comment(str_line: str) -> bool:
    """
    判断当前行是否只有缩进和 // 注释。

    :param str_line: 当前源码行。
    :return: 是纯行注释时返回 True。
    """

    # str_stripped 去除左右空白后判断行注释起始。
    str_stripped = str_line.strip()  # 去空白后的行文本

    # 纯注释行必须以 // 开始。
    return str_stripped.startswith("//")

# 供 `_is_region_banner_line` 复用的拆分 helper，专门处理当前行是否是标准区域横幅注释。
def _is_region_banner_line(str_line: str) -> bool:
    """
    判断当前行是否是标准区域横幅注释。

    :param str_line: 当前源码行。
    :return: 是区域横幅时返回 True。
    """

    # 区域横幅必须形成左右 // 边界。
    return _region_banner_anchor_column(str_line) is not None

# 供 `_line_indent` 复用的拆分 helper，专门处理源码行的行首缩进。
def _line_indent(str_line: str) -> str:
    """
    返回源码行的行首缩进。

    :param str_line: 当前源码行。
    :return: 行首空格和 Tab 构成的缩进字符串。
    """

    # int_index 逐字符定位第一个非空白字符。
    int_index = 0  # 当前扫描位置

    # 消费空格和 Tab。
    while int_index < len(str_line) and str_line[int_index] in {" ", "\t"}:

        # 继续向右扫描缩进。
        int_index += 1  # 缩进扫描位置右移

    # 返回行首缩进片段。
    return str_line[:int_index]

# 供 `_is_placeholder_comment` 复用的拆分 helper，专门处理注释正文是否像模板占位或示例残留。
def _is_placeholder_comment(str_comment: str) -> bool:
    """
    判断注释正文是否像模板占位或示例残留。

    :param str_comment: 当前正在判断的注释正文。
    :return: 命中模板占位注释时返回 True。
    """

    # 任一占位模式命中即视为无效语义注释。
    return any(obj_pattern.search(str_comment) for obj_pattern in GENERIC_COMMENT_PATTERNS)

# 供 `_control_line_requires_begin` 复用的拆分 helper，专门处理单行 Verilog 控制语句是否需要显式 begin/end。
def _control_line_requires_begin(str_code: str) -> bool:
    """
    判断单行 Verilog 控制语句是否需要显式 begin/end。

    :param str_code: 去掉注释后的当前 Verilog 代码片段。
    :return: 控制语句需要 begin/end 包裹时返回 True。
    """

    # 空代码行无需检查。
    if not str_code:

        # 空白或纯注释行不触发控制结构规则。
        return False

    # 条件和循环控制语句必须显式使用 begin。
    if re.match(r"^(if|else\s+if|for|while)\s*\(", str_code):

        # 已包含 begin 时通过检查。
        return "begin" not in str_code

    # else 分支不能是单行裸语句。
    if re.match(r"^else\b", str_code) and "begin" not in str_code and not str_code.startswith("endmodule"):

        # 裸 else 需要 begin/end 包裹。
        return True

    # always 块必须有 begin。
    if re.match(r"^always\s*@", str_code):

        # 单行 always 不符合生成风格。
        return "begin" not in str_code

    # case label 下方也需要 begin，避免多语句歧义。
    if re.match(r"^[A-Z][A-Z0-9_]*\s*:", str_code):

        # 未见 begin 时登记问题。
        return "begin" not in str_code

    # 其他行不需要 begin/end 规则。
    return False

# 供 `_style_severity` 复用的拆分 helper，专门处理格式和结构规则在当前 strict 模式下的严重级别。
def _style_severity(strict: bool) -> str:
    """
    返回格式和结构规则在当前 strict 模式下的严重级别。

    :param strict: 是否把样式和注释问题升级为 error。
    :return: 当前 strict 模式下的样式诊断级别。
    """

    # strict 模式下风格问题阻断质量门。
    return "error" if strict else "warning"

# 供 `_comment_severity` 复用的拆分 helper，专门处理注释覆盖规则在当前 strict 模式下的严重级别。
def _comment_severity(strict: bool) -> str:
    """
    返回注释覆盖规则在当前 strict 模式下的严重级别。

    :param strict: 是否把样式和注释问题升级为 error。
    :return: 当前 strict 模式下的注释诊断级别。
    """

    # strict 模式下缺失语义注释阻断质量门。
    return "error" if strict else "warning"

# 供 `_is_testbench` 复用的拆分 helper，专门处理文件名是否匹配常见 testbench 命名。
def _is_testbench(path_source: Path) -> bool:
    """
    判断文件名是否匹配常见 testbench 命名。

    :param path_source: 当前正在检查的 Verilog 源文件路径。
    :return: 文件名命中 testbench 命名时返回 True。
    """

    # str_stem 使用小写文件名主体做匹配。
    str_stem = path_source.stem.lower()  # 小写文件名主体

    # tb_、_tb 和 testbench 命名都视作测试平台。
    return str_stem.startswith("tb_") or str_stem.endswith("_tb") or "testbench" in str_stem

# 供 `_is_vitis_port` 复用的拆分 helper，专门处理端口名是否命中 Vitis wrapper 固定命名模式。
def _is_vitis_port(str_name: str) -> bool:
    """
    判断端口名是否命中 Vitis wrapper 固定命名模式。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 端口名命中 Vitis 固定模式时返回 True。
    """

    # 任一 Vitis 固定端口模式命中即可豁免 Erie 前缀规则。
    return any(obj_pattern.search(str_name) for obj_pattern in VITIS_PORT_PATTERNS)

# 供 `_expected_reg_name` 复用的拆分 helper，专门处理寄存器类信号名是否符合 Erie 前缀或输出桥接后缀。
def _expected_reg_name(str_name: str) -> bool:
    """
    判断寄存器类信号名是否符合 Erie 前缀或输出桥接后缀。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 寄存器类信号名符合约定时返回 True。
    """

    # reg/cnt/state/flag/enc/dec 前缀或 _o 后缀均可接受。
    return str_name.startswith(("reg_", "cnt_", "state_", "flag_", "enc_", "dec_")) or str_name.endswith("_o")

# 供 `_looks_counter` 复用的拆分 helper，专门处理信号名是否包含计数器语义。
def _looks_counter(str_name: str) -> bool:
    """
    判断信号名是否包含计数器语义。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 信号名包含计数语义时返回 True。
    """

    # str_lowered 用于识别 cnt/count/counter 计数 token。
    str_lowered = str_name.lower()  # 计数语义匹配用小写信号名

    # cnt/count/counter 任一出现都视为计数语义。
    return any(str_token in str_lowered for str_token in ("cnt", "count", "counter"))

# 供 `_looks_flag` 复用的拆分 helper，专门处理信号名是否包含 flag/握手/完成语义。
def _looks_flag(str_name: str) -> bool:
    """
    判断信号名是否包含 flag/握手/完成语义。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 信号名包含 flag 或握手语义时返回 True。
    """

    # str_lowered 用于识别握手、完成和 flag token。
    str_lowered = str_name.lower()  # flag 语义匹配用小写信号名

    # flag 类 token 覆盖常见握手与结束信号。
    return any(str_token in str_lowered for str_token in ("flag", "flg", "done", "valid", "ready", "req", "ack", "end"))

# 供 `_flag_name_needs_prefix` 复用的拆分 helper，专门处理flag 类内部信号是否需要补充 flag_ 前缀。
def _flag_name_needs_prefix(str_name: str) -> bool:
    """
    判断 flag 类内部信号是否需要补充 flag_ 前缀。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: flag 语义内部信号缺少前缀时返回 True。
    """

    # bool_has_flag_semantics 判断名称是否有 flag 类语义。
    bool_has_flag_semantics = _looks_flag(str_name)  # flag 前缀规则是否适用于该名称

    # bool_has_allowed_boundary 判断是否为端口或输出桥接例外。
    bool_has_allowed_boundary = (
        str_name.startswith("flag_")  # 已符合 flag_ 内部命名前缀
        or str_name.endswith("_o")  # 输出桥接后缀例外
        or str_name.startswith("i_")  # input 端口前缀例外
        or str_name.startswith("o_")  # 输出端口可直接携带 flag 语义
    )  # flag 前缀豁免条件

    # 有 flag 语义且不满足例外时需要报告。
    return bool_has_flag_semantics and not bool_has_allowed_boundary

# 通过 enc/encode 词根识别编码语义命名。
def _looks_encoder(str_name: str) -> bool:
    """
    判断信号名是否包含 encoder 语义。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 信号名包含编码语义时返回 True。
    """

    # str_lowered 用于识别 enc/encode 编码 token。
    str_lowered = str_name.lower()  # 编码语义匹配用小写信号名

    # enc 或 encode 表示编码相关信号。
    return "enc" in str_lowered or "encode" in str_lowered

# 通过 dec/decode 词根识别译码语义命名。
def _looks_decoder(str_name: str) -> bool:
    """
    判断信号名是否包含 decoder 语义。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 信号名包含译码语义时返回 True。
    """

    # str_lowered 用于识别 dec/decode 译码 token。
    str_lowered = str_name.lower()  # 译码语义匹配用小写信号名

    # dec 或 decode 表示译码相关信号。
    return "dec" in str_lowered or "decode" in str_lowered

# 供 `_line_comment` 复用的拆分 helper，专门处理当前行中真实 // 注释正文，忽略字符串字面量内部的 //。
def _line_comment(str_line: str) -> str:
    """
    返回当前行中真实 // 注释正文，忽略字符串字面量内部的 //。

    :param str_line: 当前正在判断的单行文本。
    :return: 当前行中真实 // 注释正文。
    """

    # int_comment_start 定位真实注释起点。
    int_comment_start = _line_comment_start(str_line)  # 当前行真实 // 注释起点

    # 未发现真实 // 注释时返回空字符串。
    if int_comment_start < 0:

        # 当前行没有行注释。
        return ""

    # 返回注释符之后的正文。
    return str_line[int_comment_start + 2 :].strip()

# 供 `_line_comment_start` 复用的拆分 helper，专门处理当前行真实 // 注释的字符串下标。
def _line_comment_start(str_line: str) -> int:
    """
    返回当前行真实 // 注释的字符串下标。

    :param str_line: 当前正在判断的单行文本。
    :return: 找到真实 // 时返回下标；不存在时返回 -1。
    """

    # bool_in_string 表示扫描位置是否处于双引号字符串中。
    bool_in_string = False  # 当前扫描是否在字符串内

    # bool_escaped 表示前一个字符是否为字符串转义符。
    bool_escaped = False  # 当前字符是否被反斜杠转义

    # 逐字符扫描，确保字符串中的 // 不被误判为注释。
    for int_index, str_char in enumerate(str_line):

        # 转义后的字符不参与引号和注释判断。
        if bool_escaped:

            # 转义只影响当前字符。
            bool_escaped = False  # 清除已消费的转义状态

            # 当前字符只是转义载荷，不能再参与注释识别。
            continue

        # 字符串内反斜杠开启转义状态。
        if str_char == "\\" and bool_in_string:

            # 下一字符被视为字面量。
            bool_escaped = True  # 标记下一字符被转义

            # 反斜杠本身不参与注释起点判断。
            continue

        # 双引号切换字符串状态，引号本身不参与后续斜杠匹配。
        if str_char == '"':

            # 记录引号翻转后的字符串内外状态。
            bool_in_string = not bool_in_string  # 记录后续斜杠是否仍位于字符串内部。

            # 当前字符只负责翻转字符串状态，不再参与后续注释判断。
            continue

        # 字符串内部的普通字符不会触发注释判断。
        if bool_in_string:

            # 跳过字符串内部字符，避免误判为行注释起点。
            continue

        # 字符串外的 // 才是 Verilog 行注释。
        if str_line.startswith("//", int_index):

            # 返回真实注释的起点下标。
            return int_index

    # 未发现真实 // 注释时返回哨兵值。
    return -1

# 供 `_strip_line_comment` 复用的拆分 helper，专门处理去掉 // 注释后的代码片段。
def _strip_line_comment(str_line: str) -> str:
    """
    返回去掉 // 注释后的代码片段。

    :param str_line: 当前正在判断的单行文本。
    :return: 去掉真实 // 注释后的代码片段。
    """

    # int_comment_start 提取当前行注释起点。
    int_comment_start = _line_comment_start(str_line)  # 待剥离行注释起点

    # 没有注释时原样返回。
    if int_comment_start < 0:

        # 原始行没有可剥离注释。
        return str_line

    # 返回 // 之前的代码片段。
    return str_line[:int_comment_start]

# 供 `_display_width_with_tabs` 复用的拆分 helper，专门处理计算包含 Tab 的中英文混排显示宽度。
def _display_width_with_tabs(str_text: str) -> int:
    """
    计算包含 Tab 的中英文混排显示宽度。

    :param str_text: 需要计算的源码片段。
    :return: Tab 按 4 列展开后的显示宽度。
    """

    # int_width 按 formatter 视觉列规则累计源码片段宽度。
    int_width = 0  # Tab 展开后的累计显示列

    # 逐字符处理 Tab 和宽字符。
    for str_char in str_text:

        # Tab 在本项目 formatter 中按四列显示。
        if str_char == "\t":

            # Tab 固定按四列累计，才能和 formatter 的区域锚点一致。
            int_width += 4  # 区域锚点计算中的 Tab 展开列宽

        # 普通字符复用横幅宽度算法。
        else:

            # 中文全宽字符按两个半角宽度计算。
            int_width += display_width(str_char)  # 普通字符显示宽度

    # 返回累计宽度。
    return int_width

# 供 `_is_code_line` 复用的拆分 helper，专门处理一行文本是否是非空且非纯注释的代码行。
def _is_code_line(str_line: str) -> bool:
    """
    判断一行文本是否是非空且非纯注释的代码行。

    :param str_line: 当前正在判断的单行文本。
    :return: 当前行包含 RTL 代码时返回 True。
    """

    # str_stripped 用于忽略外侧空白。
    str_stripped = str_line.strip()  # 去除空白后的当前行

    # 非空且不以 // 开头才算代码行。
    return bool(str_stripped and not str_stripped.startswith("//"))

# 供 `_line_region_titles` 复用的拆分 helper，专门处理源码中区域横幅所在行号到区域标题的映射。
def _line_region_titles(str_text: str) -> dict[int, str]:
    """
    返回源码中区域横幅所在行号到区域标题的映射。

    :param str_text: 当前 Verilog 源码文本。
    :return: 区域横幅行号到中文标题的映射。
    """

    # dict_titles 保存可反查最近区域的横幅行号。
    dict_titles: dict[int, str] = {}  # 最近区域回溯索引

    # tuple_region_aliases 兼容 formatter 新旧函数/任务区域标题。
    tuple_region_aliases = ("函数定义区域", "任务定义区域")  # 仅用于源码横幅识别的别名

    # list_region_labels 按长度降序，避免“输出信号”误吞“输出信号连线”。
    list_region_labels = sorted((*REGION_KEYWORDS, *tuple_region_aliases), key=len, reverse=True)  # 区域标题匹配顺序

    # 逐行扫描源码横幅。
    for int_line_no, str_line in enumerate(str_text.splitlines(), start=1):

        # 只在注释横幅行中判断区域标题，降低正文误报。
        if "//" not in str_line:

            # 非注释行不视为区域横幅。
            continue

        # 找出当前行包含的第一个最长区域标题。
        for str_region in list_region_labels:

            # 命中区域标题后记录。
            if str_region in str_line:

                # 当前横幅行归属最长匹配到的区域标题。
                dict_titles[int_line_no] = str_region  # 行号到区域标题的映射条目

                # 一行只记录一个最具体区域。
                break

    # 返回横幅行映射。
    return dict_titles

# 供 `_nearest_region_title` 复用的拆分 helper，专门处理目标行之前最近的区域标题。
def _nearest_region_title(dict_region_by_line: dict[int, str], int_line_no: int) -> str:
    """
    返回目标行之前最近的区域标题。

    :param dict_region_by_line: 区域横幅行号映射。
    :param int_line_no: 需要定位归属的源码行号。
    :return: 最近区域标题；未找到时返回空字符串。
    """

    # list_previous_lines 只保留目标行之前或当前行的横幅。
    list_previous_lines = [
        int_region_line  # 候选横幅行号
        for int_region_line in dict_region_by_line  # 遍历已知横幅行
        if int_region_line <= int_line_no  # 只考虑当前位置之前的区域
    ]  # 目标行之前的区域横幅行号

    # 未找到任何横幅时返回空字符串。
    if not list_previous_lines:

        # 调用方会展示 unknown。
        return ""

    # 取最近的横幅行号。
    int_nearest_line = max(list_previous_lines)  # 最近横幅行号

    # 返回对应区域标题。
    return dict_region_by_line[int_nearest_line]

# 供 `_is_fallback_comment` 复用的拆分 helper，专门处理注释是否等于规则源中的 formatter fallback 文本。
def _is_fallback_comment(str_comment: str) -> bool:
    """
    判断注释是否等于规则源中的 formatter fallback 文本。

    :param str_comment: 当前注释正文。
    :return: 注释命中 fallback 文本时返回 True。
    """

    # str_normalized 使用小写精确匹配，避免把真实中文注释误判。
    str_normalized = str_comment.strip().lower()  # fallback 精确匹配键

    # 读取 rulebook 失败时使用历史 fallback 集合兜底，VG059 会另行报告。
    try:

        # tuple_fallbacks 来自 JSON 规则源。
        tuple_fallbacks = load_verilog_rulebook().fallback_comments  # 规则源 fallback 注释

    # rulebook 异常时保留历史兜底表，避免注释检查直接崩溃。
    except Exception:

        # 历史固定值只作为异常路径下的保守兜底。
        tuple_fallbacks = (
            "parameter",  # 参数占位说明
            "port signal",  # 端口占位说明
            "signal",  # 内部信号占位说明
            "assign",  # 连续赋值占位说明
            "output bridge",  # 输出桥接 fallback 注释
            "internal output signal",  # 内部输出寄存 fallback 注释
        )

    # 精确命中 fallback 文本时返回 True。
    return str_normalized in {str(item).strip().lower() for item in tuple_fallbacks}

# 供 `_is_hollow_chinese_comment` 复用的拆分 helper，专门处理中文注释是否属于空洞、机械或占位式描述。
def _is_hollow_chinese_comment(str_comment: str) -> bool:
    """
    判断中文注释是否属于空洞、机械或占位式描述。

    :param str_comment: 当前注释正文。
    :return: 注释是空洞中文说明时返回 True。
    """

    # str_normalized 去掉常见标点和空白后做精确判定。
    str_normalized = re.sub(r"[\s，。,.;；:：]+", "", str_comment.strip())  # 规范化中文注释

    # set_hollow_comments 收集不携带具体 RTL 语义的常见中文短语。
    set_hollow_comments = {  # 缺少实体 RTL 意图的中文短语
        "中文注释",  # 仅声明语言而没有说明对象
        "信号注释",  # 仅复述注释类别
        "逻辑处理",  # 未说明具体控制或数据路径
        "模块逻辑",  # 未指出模块职责边界
        "数据处理",  # 未说明数据来源或去向
        "端口说明",  # 未说明端口方向或协议角色
        "输出信号",  # 只复述信号方向
        "输入信号",  # 只复述输入方向
        "内部信号",  # 只复述声明位置
        "参数说明",  # 未说明参数控制的硬件语义
        "状态处理",  # 未说明状态迁移含义
        "复位处理",  # 未说明复位覆盖对象
        "主逻辑",  # 泛指主体逻辑
        "连线逻辑",  # 未说明连接关系
    }

    # 精确命中空洞短语时返回 True。
    if str_normalized in set_hollow_comments:

        # 空洞注释不能通过最终交付门禁。
        return True

    # “这里...逻辑”这类模板句也视为机械注释。
    return "这里" in str_comment and "逻辑" in str_comment

# 供 `_comment_has_meaningful_chinese` 复用的拆分 helper，专门处理注释是否不是常见英文兜底词且包含中文。
def _comment_has_meaningful_chinese(str_comment: str) -> bool:
    """
    判断注释是否不是常见英文兜底词且包含中文。

    :param str_comment: 当前正在判断的注释正文。
    :return: 注释包含有效中文语义时返回 True。
    """

    # 英文兜底短语不能视作有效中文语义。
    if str_comment in {"parameter", "port signal", "signal", "assign", "output bridge", "internal output signal"}:

        # 这些短语来自旧模板或 fallback。
        return False

    # 含中文字符时认为具备中文语义基础。
    return CJK_PATTERN.search(str_comment) is not None

# 供 `_is_generic_comment` 复用的拆分 helper，专门处理注释是否属于泛化词、TODO 或模板占位残留。
def _is_generic_comment(str_comment: str) -> bool:
    """
    判断注释是否属于泛化词、TODO 或模板占位残留。

    :param str_comment: 当前正在判断的注释正文。
    :return: 注释过于泛化或为模板占位时返回 True。
    """

    # str_lowered 统一小写后匹配英文泛化词。
    str_lowered = str_comment.strip().lower()  # 规范化注释文本

    # set_generic 收集不具备 RTL 语义的固定兜底注释。
    set_generic = {  # 会触发 VG041 的固定泛化注释文本
        "parameter",  # 参数模板兜底词
        "port signal",  # 端口模板兜底词
        "signal",  # 信号模板兜底词
        "assign",  # 连线模板兜底词
        "output bridge",  # 输出桥接模板词
        "internal output signal",  # 内部输出模板词
        "todo",  # TODO 未落实标记
        "fixme",  # FIXME 待修复标记
        "reset",  # 复位泛化词
        "state task",  # 状态任务泛化词
        "main logic",  # 主逻辑泛化词
        "端口信号注释",  # 中文端口模板占位词
        "输出端口连接",  # 中文输出连接泛化词
        "输出信号内部寄存器",  # 中文输出寄存泛化词
        "数据复位值参数说明",  # 中文复位参数模板词
    }

    # bool_template_comment 捕获更复杂的占位句式。
    bool_template_comment = _is_placeholder_comment(str_comment)  # 是否命中占位正则

    # bool_here_logic 捕获“这里...逻辑”这类空泛中文注释。
    bool_here_logic = "这里" in str_comment and "逻辑" in str_comment  # 是否为泛化说明句

    # 泛化集合、占位模式或空泛“这里逻辑”任一命中即可报告。
    return str_lowered in set_generic or bool_template_comment or "逐行中文注释" in str_comment or bool_here_logic

# 供 `_has_blocking_assignment` 复用的拆分 helper，专门处理Verilog 行中是否存在阻塞赋值符号。
def _has_blocking_assignment(str_line: str) -> bool:
    """
    判断 Verilog 行中是否存在阻塞赋值符号。

    :param str_line: 当前正在判断的单行文本。
    :return: 代码行含阻塞赋值时返回 True。
    """

    # str_code 去掉行注释，避免注释中的等号干扰判断。
    str_code = _strip_line_comment(str(str_line))  # 非阻塞赋值检测使用的去注释代码行

    # str_code_without_compare 去掉比较符，保留真正赋值候选。
    str_code_without_compare = re.sub(r"(==|!=|<=|>=)", "", str_code)  # 去掉比较运算符后的代码

    # 匹配不属于比较符的单等号。
    return bool(re.search(r"(?<![<>=!])=(?!=)", str_code_without_compare))

# 检查单行代码是否命中 `<=` 形式的非阻塞赋值。
def _has_nonblocking_assignment(str_line: str) -> bool:
    """
    判断 Verilog 行中是否存在非阻塞赋值。

    :param str_line: 当前正在判断的单行文本。
    :return: 代码行含非阻塞赋值时返回 True。
    """

    # str_code 去掉行注释，避免注释中的 <= 干扰判断。
    str_code = _strip_line_comment(str(str_line))  # 去注释后的 Verilog 行

    # 匹配普通标识符、位选或拼接左值后的 <=。
    return bool(
        re.search(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*(?:\s*\[[^\]]+\])?|\{[^}]+\})\s*<=", str_code)
    )

# 供 `_as_line` 复用的拆分 helper，专门处理formatter AST 行号字段转换为可选整数。
def _as_line(value: Any) -> int | None:
    """
    把 formatter AST 行号字段转换为可选整数。

    :param value: 需要安全转换的外部值。
    :return: 转换后的整数行号；无法转换时返回 None。
    """

    # 尝试按 int 转换，失败时保留 None。
    try:

        # None 表示没有行号。
        return int(value) if value is not None else None

    # 外部诊断可能给出非数字行号，失败时回退为 None。
    except (TypeError, ValueError):

        # 非数字行号不能进入报告 line 字段。
        return None

# 供 `_has_space_before_tab` 复用的拆分 helper，专门处理非注释行在首个 Tab 前是否混入空格。
def _has_space_before_tab(str_line: str, bool_pure_comment_line: bool) -> bool:
    """
    判断非注释行在首个 Tab 前是否混入空格。

    :param str_line: 当前正在判断的单行文本。
    :param bool_pure_comment_line: 当前行是否为纯注释行。
    :return: Tab 前混入空格时返回 True。
    """

    # 纯注释行不参与 RTL 缩进检查。
    if bool_pure_comment_line:

        # 注释缩进允许更自由。
        return False

    # 没有 Tab 时不存在 Tab 前空格问题。
    if "\t" not in str_line:

        # 当前行无需混合缩进判断。
        return False

    # int_tab_index 定位缩进混用检查的首个 Tab。
    int_tab_index = str_line.find("\t")  # Tab 前空格切片的右边界

    # 检查 Tab 之前是否有四空格缩进，保持旧质量门判定口径。
    return "    " in str_line[: int_tab_index + 1]

# 供 `_bad_reset_style` 复用的拆分 helper，专门处理时序 always 的复位触发和命名是否不符合低有效约定。
def _bad_reset_style(str_header: str, str_reset: str) -> bool:
    """
    判断时序 always 的复位触发和命名是否不符合低有效约定。

    :param str_header: 当前 always 或模块头部文本。
    :param str_reset: 当前复位信号名称。
    :return: 复位边沿或命名不符合低有效约定时返回 True。
    """

    # bool_has_negedge 表示敏感列表中包含低有效边沿。
    bool_has_negedge = "negedge" in str_header  # 是否包含 negedge 触发

    # 低有效身份由统一语义段规则证明，不再维护结尾白名单。
    bool_named_low_active = is_low_active_reset_name(str_reset)  # 复位名是否符合低有效命名约束

    # 缺少 negedge 或复位名不低有效都视作异常。
    return not bool_has_negedge or not bool_named_low_active

# 供 `_always_references_state_task` 复用的拆分 helper，专门处理always 块是否引用状态但不直接更新 state_current/state_next。
def _always_references_state_task(dict_always: dict[str, Any]) -> bool:
    """
    判断 always 块是否引用状态但不直接更新 state_current/state_next。

    :param dict_always: formatter AST 中的单个 always 块描述。
    :return: always 像状态任务块时返回 True。
    """

    # bool_references_state 来自 formatter AST 的状态引用标记。
    bool_references_state = bool(dict_always.get("references_state"))  # always 是否引用状态

    # set_targets 保存 always 赋值目标。
    set_targets = set(dict_always.get("targets", []) or [])  # 状态任务判定使用的 always 赋值目标

    # 第三段状态任务不应直接赋值 state_current 或 state_next。
    bool_updates_state_registers = (
        "state_current" in set_targets  # 状态寄存器当前态被赋值
        or "state_next" in set_targets  # 状态寄存器下一态被赋值
    )  # always 是否直接更新 FSM 状态寄存器

    # 引用状态且不更新状态寄存器时认为是状态任务块。
    return bool_references_state and not bool_updates_state_registers

# 返回当前模块需要公开的兼容导出名称清单。
def _export_names() -> list[str]:
    """
    返回当前模块对外继续公开的兼容符号名。

    参数:
        无外部业务参数。

    :return: 稳定的兼容导出名称列表。
    """

    # str_exports_source 按旧测试与调用方依赖顺序保留兼容导出名原文。
    str_exports_source = """
    _lines
    _regex_lines
    _configured_region_keywords
    _runtime_message_prefixes
    CJK_PATTERN
    IDENTIFIER_PATTERN
    UPPER_IDENTIFIER_PATTERN
    HEADER_ENGLISH_SEPARATOR
    HEADER_CHINESE_SEPARATOR
    PORT_GROUP_PROTOCOL_NAMES
    PORT_GROUP_PROTOCOL_REGEX
    PORT_GROUP_PROTOCOL_PATTERN
    PORT_GROUP_GENERIC_PATTERN
    FSM_CASE_KEYWORD_PATTERN
    FSM_DEFAULT_BRANCH_PATTERN
    FSM_STATE_NEXT_ASSIGN_PATTERN
    FSM_STATE_NEXT_HOLD_PATTERN
    FSM_STATE_CASE_PATTERN
    FSM_CASE_BRANCH_BEGIN_PATTERN
    FSM_IF_BEGIN_PATTERN
    FSM_ELSE_IF_BEGIN_PATTERN
    FSM_ELSE_BEGIN_PATTERN
    FSM_PLAIN_END_PATTERN
    AXIS_PORT_TOKENS
    AXIS_DATA_TOKENS
    AXIS_CONTROL_TOKENS
    APB_PORT_TOKENS
    APB_REQUEST_TOKENS
    APB_RESPONSE_TOKENS
    REGION_KEYWORDS
    HUMAN_READABLE_VERILOG_PREFIXES
    MACHINE_RUNTIME_MESSAGE_PREFIXES
    DISPLAY_STRING_PATTERN
    PARAM_SIGNAL_GROUP_REGIONS
    BLOCK_LEADING_COMMENT_COLLECTIONS
    PROCEDURAL_ASSIGNMENT_COLLECTIONS
    PROCEDURAL_ASSIGNMENT_IGNORED_PREFIXES
    VITIS_PORT_PATTERNS
    REQUIRED_ENGLISH_HEADER_FIELDS
    REQUIRED_CHINESE_HEADER_FIELDS
    HEADER_VERSION_PATTERN
    GENERIC_COMMENT_PATTERNS
    COMMENT_REUSE_MIN_CJK_CHARS
    COMMENT_REUSE_SIMILARITY_THRESHOLD
    DUPLICATE_SIGNAL_PREFIXES
    DUPLICATE_PARAMETER_PREFIXES
    _region_banner_anchor_column
    _has_line_span
    _span_item_label
    _module_output_ports
    _assignment_lhs_label
    _previous_line_is_definition
    _is_definition_statement_line
    _is_pure_line_comment
    _is_region_banner_line
    _line_indent
    _is_placeholder_comment
    _control_line_requires_begin
    _style_severity
    _comment_severity
    _is_testbench
    _is_vitis_port
    _expected_reg_name
    _looks_counter
    _looks_flag
    _flag_name_needs_prefix
    _looks_encoder
    _looks_decoder
    _line_comment
    _line_comment_start
    _strip_line_comment
    _display_width_with_tabs
    _is_code_line
    _line_region_titles
    _nearest_region_title
    _is_fallback_comment
    _is_hollow_chinese_comment
    _comment_has_meaningful_chinese
    _is_generic_comment
    _has_blocking_assignment
    _has_nonblocking_assignment
    _as_line
    _has_space_before_tab
    _bad_reset_style
    _always_references_state_task
    __all__
    """  # 兼容导出名文本清单

    # list_exports 过滤空行并恢复成稳定的导出名列表。
    list_exports = [str_name.strip() for str_name in str_exports_source.splitlines() if str_name.strip()]  # 兼容导出名按声明顺序恢复成列表

    # 返回新的导出名称列表，避免调用方修改模块级常量源。
    return list_exports

# 保持当前模块的兼容导出面。
__all__ = _export_names()  # 模块对外兼容导出列表
