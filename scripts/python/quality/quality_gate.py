"""对 Erie 风格生成 RTL 执行确定性的可读性质量门检查。"""

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

# formatter_ast 是唯一结构化 Verilog 解析入口，正则只承担行级样式判断。
from .formatter_backend.banners import display_width
from .formatter_ast import build_ast_report_for_path, iter_verilog_sources, read_verilog_source
from scripts.python.validation.rulebook import load_verilog_rulebook

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

# 中文检测正则用于 comment_language=zh 时确认注释不是纯英文兜底。
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")  # 中文字符检测模式

# Verilog 标识符基础格式检查保留旧质量门命名规则。
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")  # 小写 snake 风格标识符模式

# localparam 等常量名使用全大写格式。
UPPER_IDENTIFIER_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")  # 大写常量标识符模式

# 双语文件头横幅用拼接构造，避免可读性工具误判为文件路径。
HEADER_ENGLISH_SEPARATOR = "/" * 36 + "English"  # 标准头部英文段横幅文本

# 中文文件头横幅与英文横幅分开声明，便于头部规则分别报错。
HEADER_CHINESE_SEPARATOR = "/" * 35 + "Chinese"  # 标准头部中文段横幅文本

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

# REGION_KEYWORDS 记录 formatter 区域横幅的既定顺序。
REGION_KEYWORDS = (  # formatter 区域横幅顺序
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
    "参数检查区域",  # 参数防护代码区域横幅
    "初始化区域",  # initial 初始化区域横幅
    "模块实例化区域",  # 子模块实例化区域横幅
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

# 英文文件头字段使用 formatter 当前 References 拼写。
REQUIRED_ENGLISH_HEADER_FIELDS = (  # 标准英文文件头必填字段
    "Company",  # 英文版权归属字段
    "Engineer",  # 英文开发人员字段
    "Create Date",  # 英文创建日期字段
    "Design Name",  # 英文设计名称字段
    "Module Name",  # 英文模块名称字段
    "Description",  # 英文模块说明字段
    "Simulations",  # 英文仿真工程字段
    "References",  # formatter 兼容英文参考资料字段
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

@dataclass(frozen=True)
class QualityIssue:
    """描述单条 Verilog 质量门诊断。"""

    # code 对应 VGxxx 规则编号。
    code: str  # 质量门规则编号

    # severity 使用 error/warning，保持报告消费方兼容。
    severity: str  # 诊断严重级别

    # message 是面向用户展示的诊断正文。
    message: str  # 诊断说明文本

    # path 允许为空，支持聚合级规则。
    path: str | None = None  # 相对或绝对文件路径

    # line 允许为空，支持无精确行号的结构规则。
    line: int | None = None  # 诊断所在行号

    # rule 保留稳定的规则命名空间，便于外部统计。
    rule: str | None = None  # 规则命名空间

    # to_dict 输出 JSON 兼容字段。
    def to_dict(self) -> dict[str, Any]:
        """
        把诊断对象转换为 JSON 友好的字典。

        :param self: 当前质量门数据对象实例。
        :return: JSON 可序列化的报告或诊断字典。
        """

        # 返回字段名保持 v0.3.0 报告契约。
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
        }

@dataclass(frozen=True)
class QualityGateReport:
    """汇总一次 RTL 质量门运行的诊断、指标和 AST 报告。"""

    # root 是本次质量门检查的输入根。
    root: Path  # 检查入口路径

    # issues 保持不可变，防止报告生成后被外部追加。
    issues: tuple[QualityIssue, ...]  # 全部质量门诊断

    # metrics 汇总文本和结构统计，供验证流程展示。
    metrics: dict[str, Any]  # 聚合统计指标

    # ast_report 直接暴露 formatter AST 聚合结果。
    ast_report: dict[str, Any]  # formatter AST 聚合报告

    # strict 决定部分样式规则是 error 还是 warning。
    strict: bool  # 严格模式开关

    # errors 属性给 CLI 和报告摘要复用。
    @property

    # errors 统计阻断质量门的诊断数量。
    def errors(self) -> int:
        """
        返回 error 级诊断数量。

        :param self: 当前质量门数据对象实例。
        :return: error 级诊断数量。
        """

        # 只统计 severity 明确为 error 的诊断。
        return sum(1 for issue in self.issues if issue.severity == "error")

    # warnings 属性保留非阻断质量门的诊断数量。
    @property

    # warnings 统计仅提示但不阻断的诊断数量。
    def warnings(self) -> int:
        """
        返回 warning 级诊断数量。

        :param self: 当前质量门数据对象实例。
        :return: warning 级诊断数量。
        """

        # warning 统计用于 Markdown 摘要和 JSON 顶层字段。
        return sum(1 for issue in self.issues if issue.severity == "warning")

    # ok 维持旧 API 的方法形式。
    def ok(self) -> bool:
        """
        判断本次质量门是否没有 error 级问题。

        :param self: 当前质量门数据对象实例。
        :return: 没有 error 级诊断时返回 True。
        """

        # 只有 error 数量为零时质量门才通过。
        return self.errors == 0

    # to_dict 输出稳定 JSON 报告。
    def to_dict(self) -> dict[str, Any]:
        """
        转换为 v0.3.0 兼容的质量门 JSON 报告。

        :param self: 当前质量门数据对象实例。
        :return: JSON 可序列化的报告或诊断字典。
        """

        # dict_report 保持历史字段和嵌套结构。
        dict_report = {  # 质量门 JSON 报告主体
            "version": 1,  # 质量门报告结构版本
            "root": str(self.root),  # 检查入口路径文本
            "ok": self.ok(),  # error 诊断是否为零
            "strict": self.strict,  # 本次运行 strict 开关
            "errors": self.errors,  # error 级诊断数量
            "warnings": self.warnings,  # 非阻断诊断数量
            "issues": [issue.to_dict() for issue in self.issues],  # JSON 诊断列表
            "metrics": self.metrics,  # 文本和结构聚合指标
            "ast_report": self.ast_report,  # 原始结构解析聚合树
        }

        # 返回给 CLI、validation 和 integration 复用。
        return dict_report

    # to_markdown 输出用户可读表格。
    def to_markdown(self) -> str:
        """
        转换为 Markdown 格式的质量门报告。

        :param self: 当前质量门数据对象实例。
        :return: Markdown 格式的质量门报告文本。
        """

        # list_markdown_lines 先写入 Markdown 标题和概要字段。
        list_markdown_lines = [  # 每个元素是一行 Markdown，最终由换行 join 生成返回字符串
            "# Verilog quality gate",  # 报告首行固定标题
            "",  # 标题和 root 字段之间的 Markdown 段落间隔
            f"Root: `{self.root}`",  # 展示本次质量门检查入口路径
            f"Strict: `{self.strict}`",  # 展示本次质量门是否启用严格模式
            "",  # strict 字段和 summary 段之间的 Markdown 段落间隔
        ]

        # 摘要行对齐旧报告格式。
        list_markdown_lines.append(f"Summary: **{self.errors} error(s)**, **{self.warnings} warning(s)**")

        # 空行分隔摘要和诊断表格。
        list_markdown_lines.append("")

        # 无诊断时返回简短成功正文。
        if not self.issues:

            # 保留英文固定文案以兼容既有测试快照。
            list_markdown_lines.append("No quality-gate findings.")

            # Markdown 文件总是以换行结尾。
            return "\n".join(list_markdown_lines) + "\n"

        # 表头保持外部文档中的列顺序。
        list_markdown_lines.append("| Severity | Code | Path | Line | Message |")

        # 表格分隔行固定为 GitHub Markdown 格式。
        list_markdown_lines.append("|---|---|---|---:|---|")

        # 每条诊断转成 Markdown 表格行。
        for issue in self.issues:

            # str_path 为空字符串时表示聚合级诊断。
            str_path = issue.path or ""  # Markdown 表格中的路径文本

            # str_line 为空字符串时表示无精确行号。
            str_line = "" if issue.line is None else str(issue.line)  # Markdown 表格中的行号文本

            # str_message 转义竖线，避免破坏 Markdown 表格。
            str_message = issue.message.replace("|", "\\|")  # 表格安全诊断文本

            # 追加单条诊断行。
            list_markdown_lines.append(
                f"| {issue.severity} | {issue.code} | `{str_path}` | {str_line} | {str_message} |"
            )

        # Markdown 报告保持末尾换行。
        return "\n".join(list_markdown_lines) + "\n"

# QualityGateRunContext 保存单次质量门运行的共享选项。
@dataclass(frozen=True)
class QualityGateRunContext:
    """承载单次 Verilog 质量门运行的共享配置。"""

    # path_root 用于生成相对路径和聚合报告 root 字段。
    path_root: Path  # 质量门入口路径

    # strict 决定样式和注释问题是否升级为 error。
    strict: bool  # strict 交付模式开关

    # comment_language 控制中文注释规则是否启用。
    comment_language: str  # 注释语言策略

    # formatter_profile 控制 formatter_ast 的解析配置。
    formatter_profile: str  # formatter_ast 后端解析策略名称

    # vitis_wrapper 控制 Vitis wrapper 端口兼容例外。
    vitis_wrapper: bool  # Vitis wrapper 命名兼容开关

# ProtocolOrderIssueContext 收束 VG057 诊断需要的协议排序上下文。
@dataclass(frozen=True)
class ProtocolOrderIssueContext:
    """承载单个协议端口排序诊断的共享上下文。"""

    # dict_section_rank 用于判断端口 section 是否发生顺序回退。
    dict_section_rank: dict[str, int]  # 协议 section 到排序号的映射

    # tuple_sections 保留 rulebook 声明的合法 section 顺序。
    tuple_sections: tuple[str, ...]  # 协议 section 合法顺序

    # str_protocol 保存 axi/axis/apb 等协议 token。
    str_protocol: str  # 当前检查的协议名称

    # str_rel_path 保留调用方看到的 Verilog 相对路径。
    str_rel_path: str  # Verilog 诊断相对路径

    # strict 决定 VG057 是 error 还是 warning。
    strict: bool  # 协议端口顺序严格模式

# StructuredCommentContext 收束单条 AST 注释诊断字段。
@dataclass(frozen=True)
class StructuredCommentContext:
    """承载单个结构化条目的注释诊断上下文。"""

    # str_name 是端口、声明、parameter 或 assign 左值名称。
    str_name: str  # 被检查结构条目的名称

    # str_label 用于诊断文本展示结构类别。
    str_label: str  # AST 条目类别标签

    # str_rel_path 保留重复注释问题所在的源文件。
    str_rel_path: str  # 重复注释诊断源文件

    # int_line_no 把注释诊断定位到 AST 条目的实体行。
    int_line_no: int | None  # 注释诊断对应的源码实体行

    # str_severity 由 strict 模式决定。
    str_severity: str  # 覆盖类注释诊断级别

    # comment_language 控制中文优先规则是否参与注释深度检查。
    comment_language: str  # 中文优先检查策略

# SameLineCommentCheckContext 收束过程赋值和实例关联的注释检查字段。
@dataclass(frozen=True)
class SameLineCommentCheckContext:
    """承载单条同线注释检查需要的上下文。"""

    # str_rel_path 指向触发同线注释规则的 Verilog 文件。
    str_rel_path: str  # 同线注释诊断文件路径

    # str_severity 保存 VG062/VG064 的最终级别。
    str_severity: str  # 同线缺注释诊断级别

    # comment_language 传递给语义深度检查器。
    comment_language: str  # 同线注释语言策略

    # str_code 是缺注释时输出的 VG 规则编号。
    str_code: str  # 缺失同线注释规则编号

    # str_label 用于诊断文本展示行类别。
    str_label: str  # 同线注释条目类别

    # str_rule 保留稳定规则命名空间。
    str_rule: str  # 诊断规则命名空间

# CommentVerticalSpacingContext 收束前导/分组注释空行布局诊断字段。
@dataclass(frozen=True)
class CommentVerticalSpacingContext:
    """承载纯注释上方空行布局检查的诊断上下文。"""

    # str_rel_path 指向触发空行布局规则的 Verilog 文件。
    str_rel_path: str  # 空行布局诊断文件路径

    # str_severity 保存空行布局问题的门禁级别。
    str_severity: str  # 前导空行问题级别

    # str_code 是空行布局问题输出的 VG 规则编号。
    str_code: str  # 空行布局规则编号

    # str_label 用于诊断文本展示注释类别。
    str_label: str  # 空行布局注释类别

    # str_rule 标识前导或分组注释的布局规则来源。
    str_rule: str  # 空行布局规则来源

# CommentReuseCandidate 收束重复注释检测所需的实体信息。
@dataclass(frozen=True)
class CommentReuseCandidate:
    """承载一条可参与重复检测的实体注释。"""

    # str_comment 保留原始注释正文，用于诊断展示。
    str_comment: str  # 原始注释正文

    # str_normalized 是精确重复检测使用的规范化文本。
    str_normalized: str  # 精确重复检测键

    # str_similarity_key 是近似重复检测使用的低噪声文本。
    str_similarity_key: str  # 近似重复检测键

    # str_label 表示该注释绑定的 RTL 实体类别。
    str_label: str  # 注释实体类别

    # str_name 表示端口、信号、assign 或过程赋值名称。
    str_name: str  # 注释绑定的实体名称

    # str_rel_path 是诊断报告使用的文件路径。
    str_rel_path: str  # 报告中的相对文件路径

    # int_line_no 指向后出现的复用注释行。
    int_line_no: int | None  # 复用注释报告行号

# OutputAssignRegionContext 收束 VG052 区域归属判断字段。
@dataclass(frozen=True)
class OutputAssignRegionContext:
    """承载 output bridge assign 区域归属检查上下文。"""

    # set_output_ports 用于识别 assign 左值是否直接驱动顶层 output。
    set_output_ports: set[str]  # 顶层 output 端口集合

    # dict_region_by_line 支持从 assign 行号回溯最近区域横幅。
    dict_region_by_line: dict[int, str]  # 区域横幅行号映射

    # str_rel_path 是 VG052 诊断使用的报告路径。
    str_rel_path: str  # 区域归属诊断路径

    # strict 决定区域归属错误是否阻断交付。
    strict: bool  # 区域归属严格模式

# run_verilog_quality_gate 是 runtime 与 CLI 共用入口。
def run_verilog_quality_gate(
    root: Path,
    *,
    strict: bool = True,
    comment_language: str = "zh",
    formatter_profile: str = "formatter-normalize",
    include_testbench: bool = False,
    vitis_wrapper: bool = False,
) -> QualityGateReport:
    """
    运行确定性的 Verilog 可读性和风格质量门。

    :param root: 需要检查的 Verilog 源文件或目录入口。
    :param strict: 是否把样式和注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :param formatter_profile: formatter_ast 使用的解析 profile。
    :param include_testbench: 是否把 testbench 文件纳入质量门。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :return: 包含诊断、metrics 和 AST 汇总的质量门报告。
    """

    # path_root 统一绝对路径，保证报告 root 字段稳定。
    path_root = root.resolve()  # 本次检查的规范化入口路径

    # list_files 只包含需要进入质量门的 Verilog 文件。
    list_files = _quality_gate_source_files(path_root, include_testbench)  # 待检查 RTL 文件集合

    # list_issues 汇总文件发现、AST、文本和结构规则诊断。
    list_issues: list[QualityIssue] = []  # 本次质量门累计诊断

    # list_file_reports 保存逐文件 formatter AST 报告。
    list_file_reports: list[dict[str, Any]] = []  # 逐文件 AST 报告集合

    # dict_aggregate_metrics 统计行数、编码和 formatter 决策。
    dict_aggregate_metrics = _empty_metrics()  # 聚合质量指标

    # quality_context 统一携带单文件 helper 共享的质量门选项。
    quality_context = QualityGateRunContext(  # 单文件规则共享的质量门运行上下文
        path_root=path_root,  # 生成相对路径和报告 root 的绝对入口
        strict=strict,  # 控制样式和注释诊断 severity 的模式
        comment_language=comment_language,  # 注释语义检查使用的目标语言
        formatter_profile=formatter_profile,  # formatter_ast 后端解析配置名
        vitis_wrapper=vitis_wrapper,  # 放宽 Vitis wrapper ABI 端口命名的开关
    )

    # 没有源文件时直接登记文件发现错误。
    if not list_files:

        # 空输入会让质量门失败，避免误报成功。
        list_issues.append(
            QualityIssue(
                "VG000",
                "error",
                "No Verilog source files were found.",
                str(path_root),
                rule="file.discovery",
            )
        )

    # 逐文件执行唯一 formatter AST 解析入口和所有质量规则。
    for path_source in list_files:

        # 单文件 helper 负责读取、AST、规则和编码 metrics 合并。
        _append_file_quality_results(
            list_issues,
            list_file_reports,
            dict_aggregate_metrics,
            path_source,
            quality_context,
        )

    # dict_ast_tree_report 保持目录级 AST 聚合报告字段。
    dict_ast_tree_report = _build_ast_tree_report(  # 目录级 formatter AST 聚合报告
        path_root,  # 聚合报告使用的检查根路径
        formatter_profile,  # formatter_ast 使用的 profile 名称
        list_file_reports,  # 已成功解析的逐文件 AST 报告
    )  # 目录级 AST 报告

    # 聚合文件数来自成功构建 AST 的文件数。
    dict_aggregate_metrics["files"] = len(list_file_reports)  # 已完成 AST 报告的文件数

    # 聚合 module 数复用 AST summary，避免重复口径。
    dict_aggregate_metrics["modules"] = dict_ast_tree_report["summary"]["modules"]  # 已解析 module 总数

    # 返回不可变报告对象，供 CLI 或验证流程序列化。
    return QualityGateReport(
        path_root,
        tuple(list_issues),
        dict_aggregate_metrics,
        dict_ast_tree_report,
        strict,
    )

# _quality_gate_source_files 收集需要进入质量门的 Verilog 源。
def _quality_gate_source_files(path_root: Path, include_testbench: bool) -> list[Path]:
    """
    返回质量门需要扫描的 Verilog 文件列表。

    :param path_root: 质量门入口文件或目录。
    :param include_testbench: 是否把 testbench 文件纳入扫描。
    :return: 已按 testbench 策略过滤后的源文件路径列表。
    """

    # list_files 保留 iter_verilog_sources 的稳定遍历顺序。
    list_files = [  # 进入质量门的 RTL 源文件集合
        path_source  # 通过 include_testbench 过滤后的源文件
        for path_source in iter_verilog_sources(path_root)  # 遍历检查根下的 Verilog 源
        if include_testbench or not _is_testbench(path_source)  # 默认排除 testbench 文件
    ]

    # 返回过滤后的源文件集合。
    return list_files

# _append_file_quality_results 合并单个 Verilog 文件的全部质量门结果。
def _append_file_quality_results(
    list_issues: list[QualityIssue],
    list_file_reports: list[dict[str, Any]],
    dict_aggregate_metrics: dict[str, Any],
    path_source: Path,
    quality_context: QualityGateRunContext,
) -> None:
    """
    读取单个 Verilog 文件并合并 AST、文本、结构和注释诊断。

    :param list_issues: 质量门主报告正在累计的诊断列表。
    :param list_file_reports: 质量门主报告正在累计的逐文件 AST 报告。
    :param dict_aggregate_metrics: 质量门主报告正在累计的 metrics 字典。
    :param path_source: 当前待检查的 Verilog 源文件。
    :param quality_context: 单次质量门运行的共享配置。
    :return: 本函数原地合并诊断、AST 报告和 metrics。
    """

    # str_rel_path 是报告中展示的文件定位字段。
    str_rel_path = _rel_path(path_source, quality_context.path_root)  # 相对检查根的源文件路径

    # tuple_source 保存文本和编码；读取失败时已登记 VG000。
    tuple_source = _read_source_for_quality_gate(path_source, str_rel_path, list_issues)  # Verilog 源读取结果

    # 读取失败的文件没有可信文本，跳过后续规则。
    if tuple_source is None:

        # 当前文件已经产生文件读取诊断。
        return

    # str_text 是后续文本规则和 AST 规则共享的源码。
    str_text = tuple_source[0]  # 当前文件 Verilog 源文本

    # str_encoding 记录 read_verilog_source 实际采用的编码。
    str_encoding = tuple_source[1]  # 当前文件读取编码

    # include 片段默认不应进入完整 RTL strict normalize 交付门禁。
    _append_include_fragment_issue(
        list_issues,
        path_source,
        str_rel_path,
        quality_context.strict,
        quality_context.formatter_profile,
    )

    # dict_ast_report 使用 formatter_ast 的唯一 parser 后端构建。
    dict_ast_report = build_ast_report_for_path(path_source, profile=quality_context.formatter_profile)  # 当前文件 AST 报告

    # relative_path 帮助下游把 AST 报告和质量门诊断对齐。
    dict_ast_report["relative_path"] = str_rel_path  # AST 报告中的相对路径

    # 保存逐文件 AST 报告供最终聚合。
    list_file_reports.append(dict_ast_report)

    # 累计基础文本和 formatter 决策指标。
    _accumulate_metrics(dict_aggregate_metrics, str_text, dict_ast_report)

    # 单文件 AST、文本、结构和注释规则诊断按原顺序合并。
    _append_file_rule_issues(
        list_issues,
        dict_ast_report,
        str_text,
        str_rel_path,
        quality_context,
    )

    # 编码 metrics 记录混合编码工程风险。
    _record_encoding_metric(dict_aggregate_metrics, str_encoding)

# _read_source_for_quality_gate 读取源码并把读取异常转为 VG000。
def _read_source_for_quality_gate(
    path_source: Path,
    str_rel_path: str,
    list_issues: list[QualityIssue],
) -> tuple[str, str] | None:
    """
    返回 Verilog 源文本和编码，读取失败时登记质量门诊断。

    :param path_source: 当前待读取的 Verilog 源文件。
    :param str_rel_path: 报告中展示的相对路径。
    :param list_issues: 质量门主报告正在累计的诊断列表。
    :return: 读取成功时返回源码文本和编码，失败时返回 None。
    """

    # 读取源码时保留实际编码，用于最终 metrics。
    try:

        # tuple_source 保存文本和编码，避免重复读文件。
        tuple_source = read_verilog_source(path_source)  # Verilog 源文本及命中编码

    # 单个源文件读取失败不能阻断其他文件继续检查。
    except Exception as exc:

        # 文件读取失败时登记错误并让调用方跳过该文件。
        list_issues.append(
            QualityIssue(
                "VG000",
                "error",
                f"Unable to read Verilog source: {exc}",
                str_rel_path,
                rule="file.encoding",
            )
        )

        # None 明确表示当前文件无可信文本。
        return None

    # 返回读取成功的源码文本和编码。
    return tuple_source

# _append_include_fragment_issue 登记 include 片段误入 strict normalize 的风险。
def _append_include_fragment_issue(
    list_issues: list[QualityIssue],
    path_source: Path,
    str_rel_path: str,
    strict: bool,
    formatter_profile: str,
) -> None:
    """
    在 strict formatter-normalize 模式下阻断 include fragment。

    :param list_issues: 质量门主报告正在累计的诊断列表。
    :param path_source: 当前待检查的 Verilog 源文件。
    :param str_rel_path: 报告中展示的相对路径。
    :param strict: 是否按最终交付模式执行。
    :param formatter_profile: formatter_ast 使用的解析 profile。
    :return: 本函数原地追加 VG058 诊断。
    """

    # include_fragment 表示 .vh 片段误走完整 RTL normalize 路径。
    bool_include_fragment = path_source.suffix.lower() == ".vh"  # include 片段文件标志

    # 非 strict、非 include 或非 formatter-normalize 时无需阻断。
    if not strict or not bool_include_fragment or formatter_profile != "formatter-normalize":

        # 当前文件不触发 VG058。
        return

    # include fragment 需要 lint/preserve 路径或人工声明完整 module 边界。
    list_issues.append(
        QualityIssue(
            "VG058",
            "error",
            "Include fragments must not be treated as formatter-normalize delivery RTL by default.",
            str_rel_path,
            rule="profile.include_fragment",
        )
    )

# _append_file_rule_issues 合并单文件 AST、文本、结构和注释规则。
def _append_file_rule_issues(
    list_issues: list[QualityIssue],
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    quality_context: QualityGateRunContext,
) -> None:
    """
    合并单个文件的 AST 诊断、文本规则、结构规则和注释规则。

    :param list_issues: 质量门主报告正在累计的诊断列表。
    :param dict_ast_report: formatter_ast 生成的当前文件报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中展示的相对路径。
    :param quality_context: 单次质量门运行的共享配置。
    :return: 本函数原地追加单文件规则诊断。
    """

    # formatter AST 自身诊断先映射为质量门问题。
    list_issues.extend(_ast_diagnostics_to_issues(dict_ast_report, str_rel_path, strict=quality_context.strict))

    # 原始文本规则检查缩进、文件头和行级注释。
    list_issues.extend(
        _raw_text_rules(
            str_text,
            str_rel_path,
            strict=quality_context.strict,
            comment_language=quality_context.comment_language,
        )
    )

    # module 结构规则依赖 formatter AST 的 module/port/always 等模型。
    list_module_issues = _file_module_rule_issues(dict_ast_report, str_text, str_rel_path, quality_context)  # module 结构诊断

    # module 结构诊断保持在注释规则之前合入。
    list_issues.extend(list_module_issues)

    # 注释覆盖和语义规则复用 AST 中的声明、赋值和实例信息。
    list_comment_issues = _file_comment_rule_issues(dict_ast_report, str_text, str_rel_path, quality_context)  # 注释覆盖与语义诊断

    # 注释诊断最后合入，保持旧报告顺序。
    list_issues.extend(list_comment_issues)

# _file_module_rule_issues 包装单文件 module 结构规则。
def _file_module_rule_issues(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    quality_context: QualityGateRunContext,
) -> list[QualityIssue]:
    """
    返回单个文件的 module 结构规则诊断。

    :param dict_ast_report: formatter_ast 生成的当前文件报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中展示的相对路径。
    :param quality_context: 单次质量门运行的共享配置。
    :return: module 级结构、命名和区域规则诊断列表。
    """

    # 返回 module 规则输出列表，调用方负责合并到文件级报告。
    return _module_rules(
        dict_ast_report,  # module/port/always 等结构化节点来源
        str_text,  # module 区域和 header 规则使用的源码文本
        str_rel_path,  # 诊断报告中的相对路径
        strict=quality_context.strict,  # style severity 的 strict 开关
        vitis_wrapper=quality_context.vitis_wrapper,  # Vitis wrapper 命名例外开关
    )

# _file_comment_rule_issues 包装单文件注释规则。
def _file_comment_rule_issues(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    quality_context: QualityGateRunContext,
) -> list[QualityIssue]:
    """
    返回单个文件的注释覆盖和语义规则诊断。

    :param dict_ast_report: formatter_ast 生成的当前文件报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中展示的相对路径。
    :param quality_context: 单次质量门运行的共享配置。
    :return: 注释覆盖、注释深度和块级说明诊断列表。
    """

    # 返回注释规则输出列表，调用方负责合并到文件级报告。
    return _comment_rules(
        dict_ast_report,  # 提供声明、赋值和实例化的注释上下文
        str_text,  # 行注释密度和 header 注释扫描文本
        str_rel_path,  # 注释诊断报告中的相对路径
        strict=quality_context.strict,  # 注释 severity 的 strict 开关
        comment_language=quality_context.comment_language,  # 注释语言约束
    )

# _record_encoding_metric 累计源码编码使用次数。
def _record_encoding_metric(dict_aggregate_metrics: dict[str, Any], str_encoding: str) -> None:
    """
    累计单个源码文件的读取编码。

    :param dict_aggregate_metrics: 质量门主报告正在累计的 metrics 字典。
    :param str_encoding: 当前文件读取时命中的编码名称。
    :return: 本函数原地更新 encodings 计数。
    """

    # encodings 字段用于定位混合编码工程。
    dict_aggregate_metrics["encodings"].setdefault(str_encoding, 0)

    # 当前文件命中编码计数加一。
    dict_aggregate_metrics["encodings"][str_encoding] += 1  # 当前文件编码出现次数

# write_quality_gate_report 负责持久化可选 JSON 和 Markdown。
def write_quality_gate_report(
    report: QualityGateReport,
    *,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> None:
    """
    按调用方请求写出 JSON 和 Markdown 质量门报告。

    :param report: 需要写出的质量门报告对象。
    :param json_path: 可选 JSON 报告输出路径。
    :param markdown_path: 可选 Markdown 报告输出路径。
    :return: 无返回值，按需写出报告文件。
    """

    # JSON 路径存在时先创建父目录再写入 UTF-8 文本。
    if json_path is not None:

        # 报告目录可能来自 CLI 参数，需要按需创建。
        json_path.parent.mkdir(parents=True, exist_ok=True)

        # dict_report_text 保持中文不转义，便于人工审查。
        str_json_text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"  # JSON 报告文本

        # 写入 JSON 报告供机器读取。
        json_path.write_text(str_json_text, encoding="utf-8")

    # Markdown 路径存在时写出用户可读报告。
    if markdown_path is not None:

        # Markdown 报告目录同样按需创建。
        markdown_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入 Markdown 表格报告。
        markdown_path.write_text(report.to_markdown(), encoding="utf-8")

# _build_ast_tree_report 组装目录级 AST 汇总。
def _build_ast_tree_report(
    path_root: Path,
    str_formatter_profile: str,
    list_file_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构造与旧版本兼容的目录级 formatter AST 聚合报告。

    :param path_root: 质量门检查入口根路径。
    :param str_formatter_profile: formatter_ast 使用的解析 profile 名称。
    :param list_file_reports: 逐文件 formatter AST 报告列表。
    :return: 目录级 formatter AST 聚合报告。
    """

    # int_module_count 汇总所有文件中的 module 数量。
    int_module_count = sum(  # 目录 summary.modules 字段使用的 module 总数
        len(dict_report.get("modules", []))  # 单文件 AST module 条目数
        for dict_report in list_file_reports  # 遍历逐文件 AST 报告
    )  # AST summary.files 下所有 module 条目数量

    # int_parse_errors 汇总 formatter AST 报出的 error 诊断。
    int_parse_errors = sum(  # 目录 summary.parse_errors 字段使用的错误总数
        1  # 每条 error 诊断计为一次 parse error
        for dict_report in list_file_reports  # 逐文件扫描 parse 诊断
        for dict_item in dict_report.get("diagnostics", [])  # 单文件诊断对象
        if dict_item.get("severity") == "error"  # 只统计 error 级 parse 诊断
    )

    # 返回目录级 AST 报告，字段保持旧调用方兼容。
    return {
        "version": 1,
        "root": str(path_root),
        "profile": str_formatter_profile,
        "files": list_file_reports,
        "summary": {
            "files": len(list_file_reports),
            "modules": int_module_count,
            "parse_errors": int_parse_errors,
        },
    }

# _ast_diagnostics_to_issues 将 formatter AST 诊断转成 VG000。
def _ast_diagnostics_to_issues(
    dict_ast_report: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    把 formatter AST 诊断映射到质量门诊断列表。

    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 转换后的 QualityIssue 诊断列表。
    """

    # list_issues 存放当前文件的 AST 诊断转换结果。
    list_issues: list[QualityIssue] = []  # AST 转换后的质量门诊断

    # 遍历 formatter_ast 暴露的诊断条目。
    for dict_item in dict_ast_report.get("diagnostics", []):

        # str_severity 先读取 AST 原始级别，后续再按 strict 开关调整。
        str_severity = str(dict_item.get("severity") or "error")  # formatter_ast 原始级别文本

        # 非严格模式下 AST error 降级为 warning。
        if not strict and str_severity == "error":

            # 降级让调用方可在探索阶段查看完整报告。
            str_severity = "warning"  # 非严格模式诊断级别

        # str_message 保留 formatter AST 的具体原因。
        str_message = str(dict_item.get("message") or "Formatter AST diagnostic.")  # AST 诊断正文

        # str_rule 记录原始 formatter AST 诊断代码。
        str_rule = str(dict_item.get("code") or "formatter.ast")  # AST 诊断规则名

        # 追加统一 VG000 诊断。
        list_issues.append(
            QualityIssue(
                "VG000",
                str_severity,
                str_message,
                str_rel_path,
                _as_line(dict_item.get("line")),
                rule=str_rule,
            )
        )

    # 返回当前文件的 AST 诊断。
    return list_issues

# _raw_text_rules 处理不依赖 AST 的行级文本约束。
def _raw_text_rules(
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查文件头、缩进、块注释和行级占位注释。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :return: 原始文本规则产生的质量门诊断列表。
    """

    # list_issues 汇总当前文件的原始文本诊断。
    list_issues: list[QualityIssue] = []  # 行级文本规则诊断

    # list_lines 用 splitlines 保持原有行号口径。
    list_lines = str_text.splitlines()  # 源文件逐行文本

    # str_style_severity 决定格式规则的严重级别。
    str_style_severity = _style_severity(strict)  # 格式规则严重级别

    # 首行 timescale 是 Erie 模板约束。
    if not str_text.startswith("`timescale 1ns / 1ps"):

        # 缺少 timescale 时定位到第一行。
        list_issues.append(
            QualityIssue(
                "VG001",
                str_style_severity,
                "File must start with `timescale 1ns / 1ps`.",
                str_rel_path,
                1,
                "file.preamble",
            )
        )

    # 文件头规则单独拆分，保持本函数分支复杂度可控。
    list_issues.extend(_header_rules(str_text, str_rel_path, strict=strict))

    # 块注释规则单独扫描，避免原始文本入口承担过多分支。
    list_issues.extend(_block_comment_rules(str_text, list_lines, str_rel_path, strict=strict))

    # 区域内行尾注释必须从区域横幅右侧 // 的显示列开始尽量对齐。
    list_issues.extend(_region_comment_anchor_rules(list_lines, str_rel_path, strict=strict))

    # 行级缩进、控制结构和占位注释规则逐行检查。
    for int_line_no, str_line in enumerate(list_lines, start=1):

        # 当前行规则集中在辅助函数里，避免本函数继续膨胀。
        list_issues.extend(_line_text_rules(str_line, int_line_no, str_rel_path, str_style_severity, strict))

    # 文件末尾换行检查保证 formatter 输出可稳定拼接。
    list_issues.extend(_final_newline_rules(str_text, list_lines, str_rel_path, str_style_severity))

    # 全文件注释语言检查避免中文交付中残留纯英文说明。
    list_issues.extend(_file_comment_language_rules(list_lines, str_rel_path, strict, comment_language))

    # 返回原始文本规则诊断。
    return list_issues

# _block_comment_rules 定位禁止交付的 Verilog 块注释。
def _block_comment_rules(
    str_text: str,
    list_lines: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查源码是否残留块注释标记。

    :param str_text: 当前 Verilog 源码文本。
    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释问题升级为 error。
    :return: 块注释规则产生的诊断列表。
    """

    # 没有块注释标记时直接返回，避免逐行扫描。
    if "/*" not in str_text and "*/" not in str_text:

        # 文件没有触发 VG002 的候选文本。
        return []

    # list_issues 保存每个块注释标记所在行的诊断。
    list_issues: list[QualityIssue] = []  # 块注释行级诊断

    # 逐行定位块注释开始或结束标记。
    for int_line_no, str_line in enumerate(list_lines, start=1):

        # 当前行没有块注释标记时跳过。
        if "/*" not in str_line and "*/" not in str_line:

            # 保持行号扫描继续。
            continue

        # Erie 生成 RTL 只允许 // 行注释。
        list_issues.append(
            QualityIssue(
                "VG002",
                _comment_severity(strict),
                "Block comments are forbidden; use // line comments only.",
                str_rel_path,
                int_line_no,
                "comments.line_only",
            )
        )

    # 返回全部块注释诊断。
    return list_issues

# _region_comment_anchor_rules 检查区域内行尾注释是否按横幅右侧 // 对齐。
def _region_comment_anchor_rules(
    list_lines: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查区域横幅覆盖范围内的代码行尾注释起点。

    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释对齐问题升级为 error。
    :return: 区域注释锚点对齐诊断列表。
    """

    # list_issues 汇总每个区域内行尾注释偏离锚点的 VG060 结果。
    list_issues: list[QualityIssue] = []  # 区域锚点偏移诊断集合

    # int_anchor_column 记录当前区域横幅最右侧 // 的显示列。
    int_anchor_column: int | None = None  # 当前区域注释锚点显示列

    # 逐行扫描区域横幅和区域内代码注释。
    for int_line_no, str_line in enumerate(list_lines, start=1):

        # 区域横幅会刷新后续代码行的注释锚点。
        int_banner_anchor = _region_banner_anchor_column(str_line)  # 当前行横幅锚点列

        # 命中横幅时只更新锚点，不检查横幅自身。
        if int_banner_anchor is not None:

            # 当前区域后续行尾注释以该列为起点。
            int_anchor_column = int_banner_anchor  # 当前区域横幅右侧 // 显示列

            # 横幅行属于纯注释行，继续扫描下一行。
            continue

        # 没进入任何区域时不执行 VG060。
        if int_anchor_column is None:

            # 文件头和 module 参数区可能没有区域横幅。
            continue

        # 纯注释行和空行不属于“代码行尾注释”。
        if not _is_code_line(str_line):

            # always/assign/reg 等上方一行的前置语义注释自然在这里豁免。
            continue

        # 只检查真实 // 行尾注释。
        int_comment_index = _line_comment_start(str_line)  # 当前行注释起始下标

        # 没有行尾注释时交给注释覆盖规则处理。
        if int_comment_index < 0:

            # VG060 只处理已有注释的起点。
            continue

        # code_width 使用去掉注释和尾随空白后的显示宽度。
        int_code_width = _display_width_with_tabs(str_line[:int_comment_index].rstrip())  # 注释前代码显示宽度

        # 注释实际起点是 // 之前文本的显示宽度。
        int_actual_column = _display_width_with_tabs(str_line[:int_comment_index])  # 当前 // 实际显示列

        # 代码未越过区域锚点时，行尾注释必须落在横幅右侧 // 列。
        if int_code_width < int_anchor_column:

            # int_expected_column 是横幅锚点定义的统一注释起点。
            int_expected_column = int_anchor_column  # 区域横幅锚点显示列

        # 代码已经越过锚点时，只允许在代码后留一个显示列。
        else:

            # int_expected_column 是当前代码结束后的第一个合法注释列。
            int_expected_column = int_code_width + 1  # 长代码后的最早注释列

        # 当前注释已经位于最早可注释列时通过。
        if int_actual_column == int_expected_column:

            # 行尾注释满足尽可能对齐。
            continue

        # 任意随意右移或过早出现都需要报告。
        list_issues.append(
            QualityIssue(
                "VG060",
                _style_severity(strict),
                f"Inline comment must start at display column {int_expected_column}, "
                f"aligned from region anchor column {int_anchor_column}; got {int_actual_column}.",
                str_rel_path,
                int_line_no,
                "comments.region_anchor",
            )
        )

    # 返回区域锚点对齐诊断。
    return list_issues

# _region_banner_anchor_column 返回区域横幅最右侧 // 的显示列。
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

# _final_newline_rules 检查文件末尾换行约束。
def _final_newline_rules(
    str_text: str,
    list_lines: list[str],
    str_rel_path: str,
    str_style_severity: str,
) -> list[QualityIssue]:
    """
    检查文件是否以换行结束。

    :param str_text: 当前 Verilog 源码文本。
    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: 格式规则严重级别。
    :return: 最终换行诊断列表。
    """

    # 空文件由其他规则报告，这里只处理非空文件末尾。
    if not str_text or str_text.endswith("\n"):

        # 最终换行规则无需诊断。
        return []

    # len(list_lines) 对无末尾换行文件仍能定位最后一行。
    return [
        QualityIssue(
            "VG005",
            str_style_severity,
            "File must end with exactly one final newline.",
            str_rel_path,
            len(list_lines),
            "format.final_newline",
        )
    ]

# _file_comment_language_rules 检查整文件注释语言策略。
def _file_comment_language_rules(
    list_lines: list[str],
    str_rel_path: str,
    strict: bool,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查整文件说明注释是否符合中文优先策略。

    :param list_lines: 已按行切分的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :return: 注释语言诊断列表。
    """

    # 非中文交付模式不要求 CJK 注释覆盖。
    if comment_language != "zh":

        # 调用方可能显式选择英文注释。
        return []

    # str_comment_text 聚合所有行注释正文。
    str_comment_text = " ".join(_line_comment(str_line) for str_line in list_lines)  # 全文件行注释正文

    # 有注释且没有中文字符时登记文件级语言问题。
    if str_comment_text and not CJK_PATTERN.search(str_comment_text):

        # 文件级诊断不绑定具体源码行。
        return [
            QualityIssue(
                "VG040",
                _comment_severity(strict),
                "Generated explanatory comments should be Chinese-first.",
                str_rel_path,
                rule="comments.language",
            )
        ]

    # 中文注释要求已满足。
    return []

# _line_text_rules 处理单行缩进、控制结构和占位注释。
def _line_text_rules(
    str_line: str,
    int_line_no: int,
    str_rel_path: str,
    str_style_severity: str,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单行文本的缩进、控制结构和注释占位问题。

    :param str_line: 当前正在判断的单行文本。
    :param int_line_no: int_line_no 整数值，表示行号或计数。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: str_style_severity 文本值，供质量门规则匹配。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 单行文本规则产生的质量门诊断列表。
    """

    # str_indent_stripped 用于识别纯注释行。
    str_indent_stripped = str_line.lstrip()  # 去除左侧空白后的当前行

    # bool_pure_comment_line 表示该行没有 RTL 代码。
    bool_pure_comment_line = str_indent_stripped.startswith("//")  # 当前行是否为纯注释

    # str_code_line 去掉注释后用于控制结构 begin/end 检查。
    str_code_line = _strip_line_comment(str_line).strip()  # 当前行不含 // 注释的 RTL 代码

    # str_comment 保存当前行 // 后的注释正文。
    str_comment = _line_comment(str_line)  # 当前行行尾或整行注释

    # list_issues 保存当前行产生的诊断。
    list_issues: list[QualityIssue] = []  # 当前行文本规则诊断

    # 单行格式规则先报告空白和缩进问题。
    list_issues.extend(
        _line_format_issues(str_line, int_line_no, str_rel_path, str_style_severity, bool_pure_comment_line)
    )

    # 控制结构规则单独检查 if/case/for 等 begin/end。
    list_issues.extend(_line_control_issues(str_code_line, int_line_no, str_rel_path, str_style_severity))

    # 注释语义规则拦截模板占位文本。
    list_issues.extend(_placeholder_comment_issues(str_comment, int_line_no, str_rel_path, strict))

    # 返回当前行产生的诊断。
    return list_issues

# _line_format_issues 检查单行空白和缩进问题。
def _line_format_issues(
    str_line: str,
    int_line_no: int,
    str_rel_path: str,
    str_style_severity: str,
    bool_pure_comment_line: bool,
) -> list[QualityIssue]:
    """
    检查单行尾随空白和 Tab 缩进约束。

    :param str_line: 当前正在判断的单行文本。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: 格式规则严重级别。
    :param bool_pure_comment_line: 当前行是否为纯注释。
    :return: 单行格式诊断列表。
    """

    # list_issues 保存当前行空白类问题。
    list_issues: list[QualityIssue] = []  # 单行格式诊断

    # 行尾空白会破坏格式稳定性。
    if str_line.rstrip() != str_line:

        # 尾随空白定位到当前行。
        list_issues.append(
            QualityIssue(
                "VG003",
                str_style_severity,
                "Trailing whitespace is not allowed.",
                str_rel_path,
                int_line_no,
                "format.trailing_space",
            )
        )

    # 非注释行不允许使用空格缩进。
    if not bool_pure_comment_line and re.match(r" {2,}\S", str_line):

        # Erie RTL 缩进约定使用 Tab。
        list_issues.append(
            QualityIssue(
                "VG004",
                str_style_severity,
                "RTL indentation must use Tab characters, not space indentation.",
                str_rel_path,
                int_line_no,
                "format.tab_indent",
            )
        )

    # Tab 前混入空格时登记缩进问题。
    if _has_space_before_tab(str_line, bool_pure_comment_line):

        # 混合缩进会导致 formatter diff 不稳定。
        list_issues.append(
            QualityIssue(
                "VG004",
                str_style_severity,
                "Do not mix spaces before Tab indentation.",
                str_rel_path,
                int_line_no,
                "format.tab_indent",
            )
        )

    # 返回当前行空白类诊断。
    return list_issues

# _line_control_issues 检查控制语句 begin/end 风格。
def _line_control_issues(
    str_code_line: str,
    int_line_no: int,
    str_rel_path: str,
    str_style_severity: str,
) -> list[QualityIssue]:
    """
    检查单行控制语句是否显式使用 begin/end。

    :param str_code_line: 去掉行注释后的 RTL 代码。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_style_severity: 格式规则严重级别。
    :return: 控制语句诊断列表。
    """

    # 控制语句必须显式使用 begin/end。
    if not _control_line_requires_begin(str_code_line):

        # 当前行不是需要整改的控制语句。
        return []

    # 单行控制语句在 Erie 生成风格中不可接受。
    return [
        QualityIssue(
            "VG025",
            str_style_severity,
            "Control statements must use explicit begin/end blocks.",
            str_rel_path,
            int_line_no,
            "control.begin_end",
        )
    ]

# _placeholder_comment_issues 检查单行占位注释。
def _placeholder_comment_issues(
    str_comment: str,
    int_line_no: int,
    str_rel_path: str,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单行注释是否仍是模板占位文字。

    :param str_comment: 行注释正文。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把注释问题升级为 error。
    :return: 占位注释诊断列表。
    """

    # 没有占位注释时直接通过。
    if not str_comment or not _is_placeholder_comment(str_comment):

        # 当前行注释不是模板噪音。
        return []

    # 占位注释使用 comment severity，非 strict 时可降级。
    return [
        QualityIssue(
            "VG041",
            _comment_severity(strict),
            "Comments must describe real RTL intent, not template or placeholder text.",
            str_rel_path,
            int_line_no,
            "comments.semantic",
        )
    ]

# _module_rules 汇总所有依赖 formatter AST 的结构规则。
def _module_rules(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    vitis_wrapper: bool,
) -> list[QualityIssue]:
    """
    检查 module 级命名、结构、端口和控制块规则。

    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :return: module 级结构和命名诊断列表。
    """

    # list_issues 保存当前 AST 报告衍生出的结构诊断。
    list_issues: list[QualityIssue] = []  # module 结构规则诊断

    # str_style_severity 决定结构风格类诊断级别。
    str_style_severity = _style_severity(strict)  # 结构规则严重级别

    # list_modules 是 formatter AST 解析出的 module 集合。
    list_modules = dict_ast_report.get("modules", [])  # 当前文件 formatter AST module 条目

    # AST 没有 module 时由 formatter 诊断负责报告。
    if not list_modules:

        # 空 module 集合无需重复登记结构规则。
        return list_issues

    # 生成交付文件通常只包含一个综合 module。
    if len(list_modules) > 1:

        # 多 module 文件仍允许继续检查每个 module。
        list_issues.append(
            QualityIssue(
                "VG006",
                str_style_severity,
                "Generated delivery should normally contain one synthesizable RTL module per file.",
                str_rel_path,
                rule="file.single_module",
            )
        )

    # 每个 module 独立执行命名、端口、参数和控制块规则。
    for dict_module in list_modules:

        # str_module_name 用于命名诊断和 header 定位。
        str_module_name = str(dict_module.get("name") or "")  # 当前 module 名称

        # AST span 是后续区域和语义规则的可信边界。
        list_issues.extend(_span_rules(dict_module, str_rel_path, strict=strict))

        # 文件头中的 module 信息必须和真实 module 声明一致。
        list_issues.extend(_header_semantic_rules(str_text, str_rel_path, dict_module, strict=strict))

        # Verilog module 名称必须满足基础标识符格式。
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str_module_name):

            # 无效名称直接定位到 module 级。
            list_issues.append(
                QualityIssue(
                    "VG010",
                    str_style_severity,
                    f"Invalid module name `{str_module_name}`.",
                    str_rel_path,
                    rule="naming.module",
                )
            )

        # 先检查 module 声明头，后续端口规则依赖该边界定位。
        list_issues.extend(_module_header_rules(str_text, str_rel_path, dict_module, strict=strict))

        # 端口方向、前缀和输出桥接规则保持旧顺序。
        list_issues.extend(
            _port_rules(dict_module, str_text, str_rel_path, strict=strict, vitis_wrapper=vitis_wrapper)
        )

        # 协议端口顺序检查基于 rulebook 中的协议段定义。
        list_issues.extend(_protocol_port_order_rules(dict_module, str_rel_path, strict=strict))

        # 参数命名规则在信号检查前执行，便于报告顺序稳定。
        list_issues.extend(_parameter_rules(dict_module, str_rel_path, strict=strict))

        # 内部声明命名检查覆盖 reg/wire/logic。
        list_issues.extend(_signal_rules(dict_module, str_rel_path, strict=strict))

        # assign 规则检查连续赋值的输出桥接和命名。
        list_issues.extend(_assign_rules(dict_module, str_text, str_rel_path, strict=strict))

        # always 规则检查时序/组合块的目标和 reset 约束。
        list_issues.extend(_always_rules(dict_module, str_rel_path, strict=strict))

        # reset 深语义检查补充旧规则只看 header 的缺口。
        list_issues.extend(_reset_semantic_rules(dict_module, str_rel_path, strict=strict))

        # FSM 规则确认三段式状态机结构。
        list_issues.extend(_fsm_rules(dict_module, str_rel_path, strict=strict))

        # instance 规则检查例化命名和 wrapper 风格。
        list_issues.extend(_instance_rules(dict_module, str_rel_path, strict=strict))

        # 区域横幅规则最后执行，避免打乱旧报告顺序。
        list_issues.extend(_region_rules(str_text, str_rel_path, dict_module, strict=strict))

        # AST span 支撑的区域归属规则补充横幅存在性检查。
        list_issues.extend(_region_ownership_rules(str_text, str_rel_path, dict_module, strict=strict))

    # 规则源一致性检查只需要文件级执行一次。
    list_issues.extend(_rulebook_consistency_issues(str_rel_path, strict=strict))

    # 返回当前文件的 module 结构诊断。
    return list_issues

# _span_rules 检查 formatter AST 是否提供可信行号范围。
def _span_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 formatter AST 结构条目是否带有源码行号范围。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: AST span 可信度相关诊断列表。
    """

    # list_issues 保存缺失 span 的结构诊断。
    list_issues: list[QualityIssue] = []  # AST span 诊断集合

    # module 顶层行号必须存在。
    if not _has_line_span(dict_module):

        # 没有 module span 时区域和注释定位都不可信。
        list_issues.append(
            QualityIssue(
                "VG050",
                _style_severity(strict),
                f"Module `{dict_module.get('name')}` is missing trusted formatter AST line span.",
                str_rel_path,
                rule="formatter_ast.span",
            )
        )

    # tuple_required_collections 列出必须携带 formatter line_start/line_end 的 module 子结构。
    tuple_required_collections = (
        "params",  # 参数列表条目的源码 span
        "ports",  # 端口列表条目的源码 span
        "localparams",  # 模块体常量条目的源码 span
        "decls",  # wire/reg 声明条目的源码 span
        "assigns",  # 连续赋值语句的源码 span
        "always",  # always 过程块的源码 span
        "instances",  # 子模块实例化语句的源码 span
        "generates",  # generate 结构块源码范围
        "initials",  # initial 仿真块源码范围
        "functions",  # function 定义块源码范围
        "tasks",  # task 任务块源码范围
    )

    # 遍历需要 span 的结构集合。
    for str_collection_name in tuple_required_collections:

        # 当前集合中的每个条目都应可定位。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # 缺 span 时登记 VG050。
            if not _has_line_span(dict_item):

                # name/lhs/header 用于帮助定位具体条目。
                str_label = _span_item_label(dict_item)  # 当前缺失 span 的条目说明

                # 缺少条目级 span 会影响强门禁定位。
                list_issues.append(
                    QualityIssue(
                        "VG050",
                        _style_severity(strict),
                        f"{str_collection_name} item `{str_label}` is missing trusted formatter AST line span.",
                        str_rel_path,
                        rule="formatter_ast.span",
                    )
                )

    # 返回 span 可信度诊断。
    return list_issues

# _header_semantic_rules 检查文件头模块名和真实 module 名是否一致。
def _header_semantic_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 header 中声明的模块名是否匹配真实 module 名。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: header 语义一致性诊断列表。
    """

    # list_issues 保存 header 语义诊断。
    list_issues: list[QualityIssue] = []  # header 语义诊断集合

    # module_name 来自 formatter AST 的真实声明。
    str_module_name = str(dict_module.get("name") or "")  # 真实 module 声明名

    # 空 module 名由旧命名规则处理。
    if not str_module_name:

        # 没有真实 module 名时无法比较 header。
        return list_issues

    # 只读取 module 前文本作为文件头区域。
    str_pre_module = _pre_module_region(str_text)  # 待解析的文件头说明区

    # 英文和中文 header 均可提供 module 名。
    tuple_header_names = (  # header 中可比较的 module 名字段
        ("header.module_name.english", _extract_header_field(str_pre_module, "Module Name")),  # 英文头声明名
        ("header.module_name.chinese", _extract_header_field(str_pre_module, "模块名称")),  # 中文头声明名
    )

    # 逐个可用 header 字段检查一致性。
    for str_rule, str_header_name in tuple_header_names:

        # 缺失字段由 VG007 处理，这里只检查已存在的字段。
        if not str_header_name:

            # 跳过缺失字段。
            continue

        # header module 名必须和真实 module 名一致。
        if str_header_name != str_module_name:

            # 语义不一致会误导后续生成、注释和验证流程。
            list_issues.append(
                QualityIssue(
                    "VG051",
                    _style_severity(strict),
                    f"Header module name `{str_header_name}` does not match module declaration `{str_module_name}`.",
                    str_rel_path,
                    1,
                    str_rule,
                )
            )

    # 返回 header 语义诊断。
    return list_issues

# _protocol_port_order_rules 检查协议端口是否维持 rulebook section 顺序。
def _protocol_port_order_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查常见协议端口是否按 rulebook 声明的 section 顺序排列。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 协议端口顺序诊断列表。
    """

    # list_ports 保持 formatter AST 声明顺序和行号。
    list_ports = list(dict_module.get("ports", []) or [])  # 端口 AST 条目顺序

    # 空端口列表无需检查。
    if not list_ports:

        # 返回空诊断。
        return []

    # 当前规则覆盖 rulebook 声明的 AXI/AXIS/APB section 顺序。
    tuple_protocol_tokens = ("axi", "axis", "apb")  # 支持检查的协议 token

    # list_issues 保存协议端口诊断。
    list_issues: list[QualityIssue] = []  # 协议端口顺序诊断集合

    # 逐个协议 token 判断是否出现协议端口。
    for str_protocol in tuple_protocol_tokens:

        # 每个协议单独计算 section 序列和第一处回退诊断。
        list_issues.extend(_protocol_order_issues_for_protocol(list_ports, str_protocol, str_rel_path, strict=strict))

    # 返回协议端口顺序诊断。
    return list_issues

# _protocol_order_issues_for_protocol 检查单个协议的端口 section 顺序。
def _protocol_order_issues_for_protocol(
    list_ports: list[dict[str, Any]],
    str_protocol: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个协议端口是否按 section 顺序声明。

    :param list_ports: formatter AST 中保持源码顺序的端口列表。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把协议顺序问题升级为 error。
    :return: 当前协议的端口顺序诊断列表。
    """

    # tuple_sections 定义当前协议端口从时钟复位到数据/响应通道的期望顺序。
    tuple_sections = _protocol_sections(str_protocol)  # 协议端口分组期望顺序

    # dict_section_rank 用于判断声明顺序是否回退。
    dict_section_rank = {  # 协议 section 顺序比较表
        str_section: int_index  # section 名称到顺序号
        for int_index, str_section in enumerate(tuple_sections)  # 保留 rulebook 声明次序
    }

    # list_seen_sections 提供 VG057 判断 section 是否回退的端口序列。
    list_seen_sections = _protocol_seen_sections(list_ports, str_protocol, dict_section_rank)  # 协议端口 section 扫描结果

    # 没有当前协议端口时无需检查。
    if not list_seen_sections:

        # 当前 module 未使用该协议。
        return []

    # protocol_context 集中保存 VG057 报告需要的排序证据。
    protocol_context = ProtocolOrderIssueContext(  # 封装 VG057 的 rank 表、合法顺序、路径和 strict
        dict_section_rank=dict_section_rank,  # section 名称到 rank 的比较表
        tuple_sections=tuple_sections,  # rulebook 声明的合法 section 次序
        str_protocol=str_protocol,  # 正在检查的协议族标识
        str_rel_path=str_rel_path,  # 触发诊断的 Verilog 相对路径
        strict=strict,  # 是否按交付门禁升级为 error
    )

    # 返回第一处 section 顺序回退诊断。
    return _protocol_order_violation_issue(list_seen_sections, protocol_context)

# _protocol_seen_sections 收集端口声明顺序中的协议 section。
def _protocol_seen_sections(
    list_ports: list[dict[str, Any]],
    str_protocol: str,
    dict_section_rank: dict[str, int],
) -> list[tuple[str, str, int | None]]:
    """
    按源码顺序收集属于指定协议的端口 section。

    :param list_ports: formatter AST 中保持源码顺序的端口列表。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :param dict_section_rank: 当前协议 section 到顺序号的映射。
    :return: 端口名、section 和行号组成的扫描结果。
    """

    # list_seen_sections 承载后续 rank 回退检测所需的端口序列。
    list_seen_sections: list[tuple[str, str, int | None]] = []  # 协议端口声明顺序

    # 逐端口归类到 protocol section。
    for dict_port in list_ports:

        # str_port_name 是 formatter AST 解析出的端口名。
        str_port_name = str(dict_port.get("name") or "")  # 当前端口名

        # str_section 为空说明该端口不属于当前协议。
        str_section = _protocol_port_section(str_port_name, str_protocol)  # 当前端口所属协议 section

        # rulebook 未声明的 section 按协议 fallback 规则归一。
        str_section = _normalised_protocol_section(str_section, dict_section_rank)  # 可参与排序的 section

        # 非当前协议端口不参与排序。
        if not str_section:

            # 用户普通端口不等同于协议 other section。
            continue

        # 记录端口名、section 和 AST 行号。
        list_seen_sections.append((str_port_name, str_section, _as_line(dict_port.get("line_start"))))

    # 返回按声明顺序收集的协议端口。
    return list_seen_sections

# _normalised_protocol_section 处理 rulebook 没声明的协议 section。
def _normalised_protocol_section(str_section: str, dict_section_rank: dict[str, int]) -> str:
    """
    把协议分类结果规范到当前 rulebook 的 section 集合。

    :param str_section: 协议端口分类结果。
    :param dict_section_rank: 当前协议 section 到顺序号的映射。
    :return: 可参与排序的 section；不应参与时返回空字符串。
    """

    # 空 section 表示端口不属于当前协议。
    if not str_section:

        # 不参与协议排序。
        return ""

    # rulebook 已声明的 section 可直接使用。
    if str_section in dict_section_rank:

        # 保留原始分类结果。
        return str_section

    # AXI/AXIS 未识别协议端口可归入 other section。
    if "other" in dict_section_rank:

        # 使用 other 维持保守排序检查。
        return "other"

    # APB 等无 other section 的协议忽略未知专名端口。
    return ""

# _protocol_order_violation_issue 返回首个协议 section 回退诊断。
def _protocol_order_violation_issue(
    list_seen_sections: list[tuple[str, str, int | None]],
    protocol_context: ProtocolOrderIssueContext,
) -> list[QualityIssue]:
    """
    返回协议端口 section 顺序中的第一处回退诊断。

    :param list_seen_sections: 端口名、section 和行号组成的扫描结果。
    :param protocol_context: 协议端口排序诊断上下文。
    :return: 至多一条协议顺序诊断。
    """

    # int_last_rank 保存此前出现过的最大 section rank。
    int_last_rank = -1  # 当前扫描到的最高 section 排名

    # str_last_section 用于错误消息指出回退边界。
    str_last_section = ""  # 上一个最高 section 名称

    # 按端口声明顺序查找 section rank 回退。
    for str_port_name, str_section, int_line in list_seen_sections:

        # int_rank 是当前端口所属 section 的排序等级。
        int_rank = protocol_context.dict_section_rank[str_section]  # 当前端口 section 排名

        # rank 回退说明端口分组顺序违反 rulebook。
        if int_rank < int_last_rank:

            # 协议端口顺序错误影响接口扫描和 wrapper 适配。
            return [
                QualityIssue(
                    "VG057",
                    _style_severity(protocol_context.strict),
                    (
                        f"{protocol_context.str_protocol.upper()} port `{str_port_name}` is in section "
                        f"`{str_section}` after `{str_last_section}`; expected order is "
                        f"{' -> '.join(protocol_context.tuple_sections)}."
                    ),
                    protocol_context.str_rel_path,
                    int_line,
                    "protocol.port_order",
                )
            ]

        # 更新已见最大 rank 和对应 section。
        if int_rank > int_last_rank:

            # 同步最高 rank，后续低 rank 端口会据此判定回退。
            int_last_rank = int_rank  # 已见最大 section rank

            # 记录最高 rank 的 section 名称，便于 VG057 展示前序分组。
            str_last_section = str_section  # 已见最大 section 名称

    # 没有 section 回退时通过。
    return []

# _protocol_sections 返回指定协议在 rulebook 中的 section 顺序。
def _protocol_sections(str_protocol: str) -> tuple[str, ...]:
    """
    返回协议端口 section 的合法顺序。

    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :return: section 名称元组。
    """

    # dict_fallback_sections 保留 rulebook 读取失败时的内置保守顺序。
    dict_fallback_sections = {  # 协议 section fallback 顺序
        "axi": ("clock_reset", "aw", "w", "b", "ar", "r", "other"),  # AXI memory mapped 通道顺序
        "axis": ("clock_reset", "slave", "master", "control", "data", "other"),  # AXIS 端点先分组再归入通用信号
        "apb": ("clock_reset", "request", "response"),  # APB 请求先于响应
    }

    # 读取 rulebook 的 protocols 配置。
    try:

        # dict_protocols 是机器规则源中的协议配置。
        dict_protocols = load_verilog_rulebook().raw.get("protocols") or {}  # rulebook 协议配置

        # tuple_rulebook_sections 取出目标协议 section 列表。
        tuple_rulebook_sections = dict_protocols.get(f"{str_protocol}_sections") or ()  # 目标协议 section 配置

        # 非空配置优先使用 rulebook。
        if tuple_rulebook_sections:

            # 返回不可变元组，避免调用方修改规则表。
            return tuple(str(item) for item in tuple_rulebook_sections)

    # rulebook 失败时让 VG059 另行报告，这里只保持协议检查可运行。
    except Exception:

        # tuple_rulebook_sections 置空后会自然落回内置协议顺序。
        tuple_rulebook_sections = ()  # rulebook 异常后的协议 section 空配置

    # 返回保守 fallback，未知协议返回空元组。
    return dict_fallback_sections.get(str_protocol, ())

# _protocol_port_section 判断端口名属于指定协议的哪个 section。
def _protocol_port_section(str_port_name: str, str_protocol: str) -> str:
    """
    判断端口名在指定协议中的 section。

    :param str_port_name: Verilog 端口名。
    :param str_protocol: 协议 token，例如 axi、axis 或 apb。
    :return: section 名称；不属于该协议时返回空字符串。
    """

    # str_lower_name 用于大小写无关的协议名和信号名判断。
    str_lower_name = str_port_name.lower()  # 归一化端口名

    # AXIS 必须先判断，避免 axis 被 axi 子串误归类。
    if str_protocol == "axis":

        # 返回 AXIS 数据或控制 section。
        return _axis_port_section(str_lower_name)

    # AXI memory-mapped 端口不能误吞 AXIS。
    if str_protocol == "axi":

        # 返回 AXI channel section。
        return _axi_port_section(str_lower_name)

    # APB 端口按 request/response 分类。
    if str_protocol == "apb":

        # APB 标准端口名映射到 request/response section。
        return _apb_port_section(str_lower_name)

    # 未知协议不参与检查。
    return ""

# _axi_port_section 判断 AXI memory-mapped 端口 section。
def _axi_port_section(str_lower_name: str) -> str:
    """
    判断 AXI memory-mapped 端口 section。

    :param str_lower_name: 小写端口名。
    :return: AXI section；非 AXI 端口返回空字符串。
    """

    # AXIS 端口不属于 AXI memory-mapped。
    if "axis" in str_lower_name:

        # 避免 axis 中的 axi 子串误报。
        return ""

    # 只处理名字里明确包含 axi 的端口。
    if "axi" not in str_lower_name:

        # 普通用户端口不属于 AXI。
        return ""

    # 时钟和复位属于 clock_reset section。
    if _is_protocol_clock_or_reset(str_lower_name):

        # protocol clock/reset 必须排在最前。
        return "clock_reset"

    # tuple_parts 按下划线切分，用于识别 awaddr/wdata 等 channel 前缀。
    tuple_parts = tuple(part for part in re.split(r"[^a-z0-9]+", str_lower_name) if part)  # 端口名分段

    # AXI 通道前缀顺序和 rulebook sections 一致。
    for str_section in ("aw", "w", "b", "ar", "r"):

        # 任一段以通道名开头即归入该通道。
        if any(str_part.startswith(str_section) for str_part in tuple_parts):

            # 返回识别到的 AXI 通道 section。
            return str_section

    # 明确属于 AXI 但未归类的端口归入 other。
    return "other"

# _axis_port_section 把 AXIS 端口归入端点侧别或 data/control/clock_reset。
def _axis_port_section(str_lower_name: str) -> str:
    """
    判断 AXI Stream 端口 section。

    :param str_lower_name: 小写端口名。
    :return: AXIS section；非 AXIS 端口返回空字符串。
    """

    # AXIS 常见命名包含 axis 或 tdata/tvalid 等 stream 信号。
    bool_axis_named = "axis" in str_lower_name or _contains_any_token(str_lower_name, AXIS_PORT_TOKENS)  # AXIS 端口命名证据

    # 非 AXIS 端口不参与 AXIS 排序。
    if not bool_axis_named:

        # 当前端口不是 AXIS。
        return ""

    # AXIS 时钟复位必须排在最前。
    if _is_protocol_clock_or_reset(str_lower_name):

        # AXIS 时钟复位归入 clock_reset section。
        return "clock_reset"

    # str_endpoint_side 用于支持 s_axis/m_axis 端点成组的常见端口布局。
    str_endpoint_side = _axis_endpoint_side(str_lower_name)  # AXIS slave/master 端点侧别

    # 明确带 s_axis/m_axis 前缀的端口按端点侧别排序，不强制侧内 handshake/data 顺序。
    if str_endpoint_side:

        # 返回 slave 或 master section。
        return str_endpoint_side

    # tdata/tkeep/tstrb 是数据通道。
    if _contains_any_token(str_lower_name, AXIS_DATA_TOKENS):

        # AXIS payload 和 byte-enable 信号归入 data section。
        return "data"

    # 其余 AXIS 握手、帧尾和用户信息归入 control。
    if _contains_any_token(str_lower_name, AXIS_CONTROL_TOKENS):

        # AXIS 握手、帧尾和旁带信号归入 control section。
        return "control"

    # AXIS 专名端口未命中数据或控制 token 时归入 other。
    return "other"

# _axis_endpoint_side 识别 AXIS slave/master 端点命名。
def _axis_endpoint_side(str_lower_name: str) -> str:
    """
    返回 AXIS 端口名中的端点侧别。

    :param str_lower_name: 小写端口名。
    :return: slave、master 或空字符串。
    """

    # tuple_endpoint_rules 按 rulebook 顺序覆盖短前缀和长前缀两种端点写法。
    tuple_endpoint_rules = (  # AXIS 端点侧别识别规则
        ("slave", r"(^|_)s_axis(_|$)", "slave_axis"),  # slave 端点短前缀和长前缀
        ("master", r"(^|_)m_axis(_|$)", "master_axis"),  # 下游输出端点命名
    )

    # 按 slave、master 顺序识别显式端点分组。
    for str_section, str_short_pattern, str_long_token in tuple_endpoint_rules:

        # bool_short_endpoint_match 保证 s_axis 不会误命中普通字符串中间片段。
        bool_short_endpoint_match = re.search(str_short_pattern, str_lower_name) is not None  # 端点短前缀边界命中

        # bool_long_endpoint_match 兼容 slave_axis/master_axis 长前缀写法。
        bool_long_endpoint_match = str_long_token in str_lower_name  # 端点长前缀命中

        # bool_endpoint_match 汇总短前缀和长前缀两类端点命名。
        bool_endpoint_match = bool_short_endpoint_match or bool_long_endpoint_match  # 显式 AXIS 端点命中

        # 命中后返回端点级 section，侧内 tvalid/tdata 不再强排。
        if bool_endpoint_match:

            # 返回 rulebook 可排序的端点 section。
            return str_section

    # 不带端点侧别的 AXIS 端口交由 data/control fallback 分类。
    return ""

# APB 分类必须兼容显式 apb 前缀和裸 paddr/psel 风格信号名。
def _apb_port_section(str_lower_name: str) -> str:
    """
    判断 APB 端口 section。

    :param str_lower_name: 小写端口名。
    :return: APB section；非 APB 端口返回空字符串。
    """

    # APB 端口可能显式包含 apb，也可能只使用 paddr/psel 等标准名。
    bool_apb_named = "apb" in str_lower_name or _contains_any_token(str_lower_name, APB_PORT_TOKENS)  # APB 前缀或 P* 标准信号命中标志

    # 普通用户端口不进入 APB section 顺序检查。
    if not bool_apb_named:

        # 空 section 表示该端口不参与 APB 排序。
        return ""

    # APB clock/reset 归入最前置 section。
    if _is_protocol_clock_or_reset(str_lower_name):

        # pclk/preset/prst 类信号必须早于请求和响应通道。
        return "clock_reset"

    # request 信号由 master 发起。
    if _contains_any_token(str_lower_name, APB_REQUEST_TOKENS):

        # 地址、选择、写数据和保护信号归入 request section。
        return "request"

    # response 信号由 slave 返回。
    if _contains_any_token(str_lower_name, APB_RESPONSE_TOKENS):

        # 读数据、ready 和错误信号归入 response section。
        return "response"

    # 未识别的 APB 端口不参与排序。
    return ""

# _contains_any_token 判断名称是否包含任一协议 token。
def _contains_any_token(str_lower_name: str, tuple_tokens: tuple[str, ...]) -> bool:
    """
    判断小写端口名是否包含任一协议 token。

    :param str_lower_name: 已转成小写的 Verilog 端口名。
    :param tuple_tokens: 协议端口名 token 集合。
    :return: 任一 token 出现在端口名中时返回 True。
    """

    # bool_has_token 复用协议分类中的任一 token 命中语义。
    bool_has_token = any(str_token in str_lower_name for str_token in tuple_tokens)  # 协议 token 命中标志

    # 返回 token 命中结果。
    return bool_has_token

# _is_protocol_clock_or_reset 判断协议端口是否是 clock/reset。
def _is_protocol_clock_or_reset(str_lower_name: str) -> bool:
    """
    判断端口名是否表达协议 clock/reset。

    :param str_lower_name: 小写端口名。
    :return: clock 或 reset 端口返回 True。
    """

    # 常见 clock/reset token 覆盖 AXI、AXIS、APB、AHB 等命名。
    return any(str_token in str_lower_name for str_token in ("clk", "clock", "rst", "reset", "areset", "preset"))

# _has_line_span 判断 AST 条目是否有可信行号范围。
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

# _span_item_label 生成缺失 span 诊断中的条目说明。
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

# _extract_header_field 从双语文件头中读取字段值。
def _extract_header_field(str_pre_module: str, str_field: str) -> str:
    """
    从 module 前文件头中读取指定字段的首个值 token。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_field: 需要提取的文件头字段名。
    :return: 字段值的首个非空 token，未找到时返回空字符串。
    """

    # 字段行统一形如 // Field: value 或 // 字段: value。
    str_pattern = rf"(?m)^\s*//\s*{re.escape(str_field)}\s*:\s*(?P<value>.*?)\s*$"  # 文件头字段匹配正则

    # obj_match 定位字段行。
    obj_match = re.search(str_pattern, str_pre_module)  # 文件头字段匹配对象

    # 找不到字段时返回空字符串。
    if obj_match is None:

        # 缺字段由 VG007 负责报告。
        return ""

    # str_value 去掉外侧空白，便于从 tab 对齐字段中提取模块名。
    str_value = obj_match.group("value").strip()  # 字段原始值

    # 文件头字段可能保留制表对齐，只取第一个非空 token。
    list_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str_value)  # 字段中的 Verilog 标识符候选

    # 返回首个标识符候选。
    return list_tokens[0] if list_tokens else ""

# _reset_semantic_rules 检查复位分支条件是否符合低有效语义。
def _reset_semantic_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查时序 always 的 reset 条件和低有效命名是否一致。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: reset 深语义诊断列表。
    """

    # list_issues 保存 reset 条件诊断。
    list_issues: list[QualityIssue] = []  # reset 深语义诊断集合

    # 逐个时序 always 检查 reset 分支。
    for dict_always in dict_module.get("always", []) or []:

        # 单个 always 的 reset 极性和覆盖检查独立完成。
        list_issues.extend(_reset_semantic_issues_for_always(dict_always, str_rel_path, strict=strict))

    # 返回 reset 深语义诊断。
    return list_issues

# _reset_semantic_issues_for_always 检查一个 always 的低有效 reset 分支。
def _reset_semantic_issues_for_always(
    dict_always: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个时序 always 的低有效 reset 分支。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 reset 语义问题升级为 error。
    :return: 当前 always 的 reset 语义诊断列表。
    """

    # 只检查 formatter 确认为时序块的 always。
    if dict_always.get("trigger_kind") != "seq":

        # 组合块不涉及 reset 极性。
        return []

    # str_reset 是 formatter 从敏感列表和 if 分支推断出的复位名。
    str_reset = str(dict_always.get("reset") or "")  # 时序块复位信号名

    # 没有 reset 或不是低有效命名时交给旧 VG021 处理。
    if not str_reset or not _is_low_active_reset_name(str_reset):

        # 本规则只判断低有效 reset 的分支条件是否反向。
        return []

    # int_base_line 是 always 起始行，行内偏移用于精确报告 reset 条件位置。
    int_base_line = _as_line(dict_always.get("line_start")) or 1  # always 块起始行号

    # list_always_lines 保持 formatter AST 提供的 always 内部源码行。
    list_always_lines = list(dict_always.get("lines", []) or [])  # always 内部源码行

    # int_reset_offset 定位 reset 分支 if 所在的 always 内部行。
    int_reset_offset = _find_reset_condition_offset(list_always_lines, str_reset)  # reset 条件行偏移

    # 找不到 reset if 条件时，敏感列表和正文不一致。
    if int_reset_offset is None:

        # 复位敏感列表没有对应复位分支会导致寄存器覆盖缺失。
        return [
            QualityIssue(
                "VG053",
                _style_severity(strict),
                f"Sequential always declares reset `{str_reset}` but no matching reset branch was found.",
                str_rel_path,
                int_base_line,
                rule="reset.coverage",
            )
        ]

    # str_code_line 用于忽略注释中的 reset 名称。
    str_code_line = _strip_line_comment(str(list_always_lines[int_reset_offset]))  # 去注释后的 reset 条件行

    # 条件符合低有效语义时通过。
    if _active_low_reset_condition_is_correct(str_code_line, str_reset):

        # reset 条件极性与命名一致。
        return []

    # 错误极性会导致复位覆盖语义反转。
    return [
        QualityIssue(
            "VG053",
            _style_severity(strict),
            f"Reset `{str_reset}` is low-active, but the reset branch condition is not low-active.",
            str_rel_path,
            int_base_line + int_reset_offset,
            rule="reset.condition_polarity",
        )
    ]

# _find_reset_condition_offset 查找 always 内部 reset if 条件行。
def _find_reset_condition_offset(list_always_lines: list[Any], str_reset: str) -> int | None:
    """
    查找 always 内部首个 reset 条件行偏移。

    :param list_always_lines: always 内部源码行列表。
    :param str_reset: 低有效复位信号名。
    :return: reset 条件行偏移；未找到时返回 None。
    """

    # 遍历 always 内部行，查找 reset 分支 if。
    for int_offset, str_line in enumerate(list_always_lines):

        # 去注释文本避免注释中的 reset 名称触发覆盖判断。
        str_code_line = _strip_line_comment(str(str_line))  # 去注释后的 always 内部行

        # reset 条件通常在包含 if 和 reset 名的第一行。
        if str_reset in str_code_line and "if" in str_code_line:

            # 返回 reset 条件相对 always 起始行的偏移。
            return int_offset

    # 没有找到 reset 条件。
    return None

# _active_low_reset_condition_is_correct 判断 if 条件是否表达低有效复位。
def _active_low_reset_condition_is_correct(str_line: str, str_reset: str) -> bool:
    """
    判断 reset 条件行是否符合低有效复位约定。

    :param str_line: 去注释后的 Verilog 条件行。
    :param str_reset: 低有效复位信号名。
    :return: 条件表达低有效复位时返回 True。
    """

    # str_compact 去掉空白，统一比较不同代码风格。
    str_compact = re.sub(r"\s+", "", str_line)  # 去空白后的条件行

    # str_reset_pattern 是复位信号名的正则转义版本。
    str_reset_pattern = re.escape(str_reset)  # reset 名称正则

    # !rstn 或 ~rstn 是最直接的低有效判断。
    if re.search(rf"if\((?:!|~){str_reset_pattern}\)", str_compact):

        # 直接取反形式通过检查。
        return True

    # rstn == 0、rstn == 1'b0、rstn === 1'b0 等形式均表示低有效。
    tuple_low_patterns = (  # 可接受的低有效比较形式
        rf"{str_reset_pattern}={{2,3}}(?:1'b0|1'h0|1'd0|0)",  # reset 信号在比较左侧
        rf"(?:1'b0|1'h0|1'd0|0)={{2,3}}{str_reset_pattern}",  # 低电平常量在比较左侧
    )

    # 任一低有效比较形式命中即可通过。
    return any(re.search(str_pattern, str_compact, re.IGNORECASE) for str_pattern in tuple_low_patterns)

# _region_ownership_rules 检查 AST 节点是否放入正确区域横幅。
def _region_ownership_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查关键 AST 节点的源码行是否归属正确区域。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 区域归属诊断列表。
    """

    # list_issues 保存 AST 区域归属诊断。
    list_issues: list[QualityIssue] = []  # 区域归属诊断集合

    # dict_region_by_line 记录每个区域横幅出现的行号。
    dict_region_by_line = _line_region_titles(str_text)  # 源码中的区域横幅行

    # 没有任何横幅时由 VG031 负责报告。
    if not dict_region_by_line:

        # 无法判断具体 AST 归属。
        return list_issues

    # 输出端口集合用于识别 output bridge assign。
    set_output_ports = _module_output_ports(dict_module)  # output bridge 目标端口集合

    # VG052 仍只检查 output bridge，不混入新的 VG061 通用归属。
    tuple_output_bridge_args = (dict_module, set_output_ports, dict_region_by_line, str_rel_path)  # VG052 位置参数

    # 调用旧专项检查器，保留原有输出连线错误文案。
    list_output_bridge_issues = _output_assign_region_issues(*tuple_output_bridge_args, strict=strict)  # 输出桥接诊断

    # VG052 保持兼容，避免输出连线规则编号漂移。
    list_issues.extend(list_output_bridge_issues)

    # VG061 使用 module、区域索引和输出端口集合推导通用结构归属。
    tuple_general_region_args = (dict_module, dict_region_by_line, set_output_ports, str_rel_path)  # 通用归属位置参数

    # 调用新增通用检查器，补齐参数、声明和过程块区域归属。
    list_general_region_issues = _general_region_ownership_issues(*tuple_general_region_args, strict=strict)  # 通用归属诊断

    # VG061 覆盖参数、声明、过程块、实例化等通用归属。
    list_issues.extend(list_general_region_issues)

    # 返回区域归属诊断。
    return list_issues

# _general_region_ownership_issues 检查非 output-bridge 的通用区域归属。
def _general_region_ownership_issues(
    dict_module: dict[str, Any],
    dict_region_by_line: dict[int, str],
    set_output_ports: set[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查参数、声明、assign、过程块和实例化的区域归属。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param set_output_ports: 顶层 output 端口名集合。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :return: 通用区域归属诊断列表。
    """

    # list_issues 保存 VG061 通用区域归属诊断。
    list_issues: list[QualityIssue] = []  # 通用区域归属诊断集合

    # localparam、声明、assign 和 always 的期望区域由专门迭代器统一给出。
    for region_item in _iter_region_expectations(dict_module, set_output_ports):

        # 每个期望项只产生零条或一条 VG061。
        list_issues.extend(
            _region_owner_issue_for_item(
                region_item["item"],
                region_item["label"],
                region_item["regions"],
                dict_region_by_line,
                str_rel_path,
                strict=strict,
                str_rule=region_item["rule"],
            )
        )

    # generate、initial、function、task 和实例化使用固定区域。
    for region_item in _iter_fixed_region_expectations(dict_module):

        # 固定结构直接携带期望区域，避免在主循环里重复分支。
        list_issues.extend(
            _region_owner_issue_for_item(
                region_item["item"],
                region_item["label"],
                region_item["regions"],
                dict_region_by_line,
                str_rel_path,
                strict=strict,
                str_rule=region_item["rule"],
            )
        )

    # 返回通用区域归属诊断。
    return list_issues

# _iter_fixed_region_expectations 产出固定 AST 集合的区域期望项。
def _iter_fixed_region_expectations(dict_module: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回 generate、initial、function、task 和实例化的区域期望项。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 可直接传给区域归属检查的固定期望项列表。
    """

    # list_items 汇总不依赖命名推导的固定 AST 归属规则。
    list_items: list[dict[str, Any]] = []  # 固定 AST 区域期望项

    # generate 块只允许出现在生成块区域。
    tuple_generate_check = ("generates", ("生成块区域",), "regions.generate")  # generate 块区域规则

    # initial 块允许用于初始化或参数检查前置断言。
    tuple_initial_check = ("initials", ("初始化区域", "参数检查区域"), "regions.initial")  # initial 区域规则

    # function 定义兼容历史名称和当前规范区域。
    tuple_function_check = ("functions", ("函数区域", "函数定义区域"), "regions.function")  # 函数定义标题兼容映射

    # task 定义兼容普通任务和状态任务区域。
    tuple_task_check = ("tasks", ("任务区域", "任务定义区域", "状态任务处理区域"), "regions.task")  # task AST 归属映射

    # 子模块实例化必须留在实例化区域。
    tuple_instance_check = ("instances", ("模块实例化区域",), "regions.instance")  # 实例化区域规则

    # 固定结构检查先从 generate 规则开始。
    list_fixed_region_checks = [tuple_generate_check]  # 固定结构区域规则表

    # initial 规则保持在 generate 后，贴近规范区域顺序。
    list_fixed_region_checks += [tuple_initial_check]  # initial 结构区域规则

    # function 规则覆盖工具函数定义。
    list_fixed_region_checks += [tuple_function_check]  # function 固定检查入口

    # task 追加在 function 之后，保持工具过程定义的检查顺序。
    list_fixed_region_checks += [tuple_task_check]  # 任务定义检查入口

    # 实例化规则最后追加，便于和主要逻辑区域分离。
    list_fixed_region_checks += [tuple_instance_check]  # 补充实例化规则

    # 逐个 AST 集合生成统一结构，供 VG061 复用。
    for str_collection_name, tuple_expected_regions, str_rule in list_fixed_region_checks:

        # 当前集合的每个条目共享同一个规范区域集合。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # 固定结构的诊断标签优先使用 AST span 推导结果。
            list_items.append(
                {
                    "item": dict_item,
                    "label": _span_item_label(dict_item),
                    "regions": tuple_expected_regions,
                    "rule": str_rule,
                }
            )

    # 返回固定结构区域期望。
    return list_items

# _iter_region_expectations 产出需要检查的动态区域期望项。
def _iter_region_expectations(
    dict_module: dict[str, Any],
    set_output_ports: set[str],
) -> list[dict[str, Any]]:
    """
    返回 localparam、声明、assign 和 always 的区域期望项。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param set_output_ports: 顶层 output 端口名集合。
    :return: 可直接传给区域归属检查的期望项列表。
    """

    # list_items 保存动态结构的区域期望。
    list_items: list[dict[str, Any]] = []  # 动态 AST 区域期望项

    # localparam 是实际出现在 module body 区域中的参数实体。
    for dict_param in dict_module.get("localparams", []) or []:

        # 当前 localparam 可能是状态编码或普通配置常量。
        list_items.append(_localparam_region_expectation(dict_param))

    # 内部声明按命名语义放入对应信号区域。
    for dict_decl in dict_module.get("decls", []) or []:

        # 当前声明的期望区域由名称和声明类型共同决定。
        list_items.append(
            {
                "item": dict_decl,
                "label": _span_item_label(dict_decl),
                "regions": _expected_decl_regions(dict_decl),
                "rule": "regions.declaration",
            }
        )

    # assign 按 output bridge 和普通连线分流。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 当前 assign 的期望区域由左值决定。
        list_items.append(_assign_region_expectation(dict_assign, set_output_ports))

    # always 块根据目标信号和状态引用分配到输出、状态机或主任务区域。
    for dict_always in dict_module.get("always", []) or []:

        # 当前 always 的 header 用于诊断定位。
        list_items.append(
            {
                "item": dict_always,
                "label": str(dict_always.get("header") or "always"),
                "regions": _expected_always_regions(dict_always),
                "rule": "regions.always",
            }
        )

    # 返回所有动态区域期望。
    return list_items

# _localparam_region_expectation 构造 localparam 区域期望项。
def _localparam_region_expectation(dict_param: dict[str, Any]) -> dict[str, Any]:
    """
    构造单个 localparam 的区域期望项。

    :param dict_param: formatter AST localparam 条目。
    :return: 区域期望项字典。
    """

    # str_name 用于区分状态参数和普通局部常量。
    str_name = str(dict_param.get("name") or "")  # localparam 区域判定名称

    # tuple_expected_regions 表示该 localparam 允许出现的区域。
    tuple_expected_regions = ("状态参数区域",) if str_name.startswith("ST_") else ("配置参数区域",)  # localparam 期望区域

    # localparam 期望项交给 VG061 的统一定位逻辑处理。
    return {
        "item": dict_param,
        "label": _span_item_label(dict_param),
        "regions": tuple_expected_regions,
        "rule": "regions.localparam",
    }

# _assign_region_expectation 构造连续赋值的区域期望项。
def _assign_region_expectation(dict_assign: dict[str, Any], set_output_ports: set[str]) -> dict[str, Any]:
    """
    构造单条 assign 的区域期望项。

    :param dict_assign: formatter AST assign 条目。
    :param set_output_ports: 顶层 output 端口名集合。
    :return: 区域期望项字典。
    """

    # str_lhs 用于判断 assign 是否直接驱动顶层输出。
    str_lhs = str(dict_assign.get("lhs") or "")  # assign 输出桥接判定左值

    # 输出端口桥接必须在专门连线区域。
    if str_lhs in set_output_ports or str_lhs.startswith("o_"):

        # tuple_expected_regions 指向 output bridge 规范区域。
        tuple_expected_regions = ("输出信号连线",)  # 输出桥接 assign 期望区域

    # 普通组合连线落入其他信号连线区域。
    else:

        # tuple_expected_regions 指向非 output bridge 连线区域。
        tuple_expected_regions = ("其他信号连线",)  # 普通 assign 期望区域

    # 当前 assign 的区域归属由统一定位逻辑生成最终 VG061 诊断。
    return {
        "item": dict_assign,
        "label": str_lhs or "assign",
        "regions": tuple_expected_regions,
        "rule": "regions.assign",
    }

# _region_owner_issue_for_item 为单个 AST 条目生成区域归属诊断。
def _region_owner_issue_for_item(
    dict_item: dict[str, Any],
    str_label: str,
    tuple_expected_regions: tuple[str, ...],
    dict_region_by_line: dict[int, str],
    str_rel_path: str, *,
    strict: bool, str_rule: str,
) -> list[QualityIssue]:
    """
    检查单个 AST 条目所在区域是否属于允许集合。

    :param dict_item: formatter AST 条目。
    :param str_label: 诊断中展示的条目标签。
    :param tuple_expected_regions: 允许的区域标题集合。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :param str_rule: 规则子命名空间。
    :return: 当前条目的区域归属诊断列表。
    """

    # int_line_no 使用 AST 起始行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 区域归属定位行号

    # 缺少行号由 VG050 负责。
    if int_line_no is None:

        # 本规则无法定位无 span 条目。
        return []

    # str_region_title 是当前条目最近的区域横幅。
    str_region_title = _nearest_region_title(dict_region_by_line, int_line_no)  # 当前条目所属区域

    # 若当前行位于第一个区域前，保守跳过 module header 参数/端口等结构。
    if not str_region_title:

        # 没有区域上下文时不做归属判断。
        return []

    # 命中允许区域时通过。
    if str_region_title in tuple_expected_regions:

        # 区域归属符合预期。
        return []

    # 生成通用区域归属诊断。
    return [
        QualityIssue(
            "VG061",
            _style_severity(strict),
            f"Item `{str_label}` must be placed in {', '.join(tuple_expected_regions)}, "
            f"not `{str_region_title}`.",
            str_rel_path,
            int_line_no,
            rule=str_rule,
        )
    ]

# _expected_decl_regions 根据内部声明名称推导允许区域。
def _expected_decl_regions(dict_decl: dict[str, Any]) -> tuple[str, ...]:
    """
    返回内部声明允许出现的区域集合。

    :param dict_decl: formatter AST 内部声明条目。
    :return: 允许区域标题元组。
    """

    # str_name 用于按 Erie 命名前缀识别区域。
    str_name = str(dict_decl.get("name") or "")  # 内部声明名称

    # str_kind 表示 wire/reg/logic 等声明类型。
    str_kind = str(dict_decl.get("kind") or "")  # 内部声明类型

    # list_region_rules 按优先级保存声明名称与目标区域的映射。
    list_region_rules: list[tuple[bool, tuple[str, ...]]] = []  # 内部声明区域推断规则

    # 输出桥接内部信号必须进入输出信号区域。
    list_region_rules.append((str_name.endswith("_o"), ("输出信号",)))

    # 计数器前缀信号必须进入计数信号区域。
    list_region_rules.append((str_name.startswith("cnt_"), ("计数信号",)))

    # 状态寄存器前缀信号必须进入状态机信号区域。
    list_region_rules.append((str_name.startswith("state_"), ("状态机信号",)))

    # 握手、完成和请求类标志必须进入标志信号区域。
    list_region_rules.append((str_name.startswith("flag_"), ("标志信号",)))

    # 编码类命名或语义词命中时进入编码信号区域。
    list_region_rules.append((str_name.startswith("enc_") or _looks_encoder(str_name), ("编码信号",)))

    # 译码类命名或语义词命中时进入译码信号区域。
    list_region_rules.append((str_name.startswith("dec_") or _looks_decoder(str_name), ("译码信号",)))

    # 其他寄存器声明按寄存器信号区域处理。
    list_region_rules.append((str_name.startswith("reg_") or str_kind == "reg", ("寄存器信号",)))

    # 按优先级返回第一个命中的声明区域。
    for bool_matched, tuple_regions in list_region_rules:

        # 当前规则未命中时继续检查下一项。
        if not bool_matched:

            # 保持区域规则优先级顺序。
            continue

        # 返回当前命中的区域集合。
        return tuple_regions

    # 其他内部连线允许放入其他信号或实例化信号区。
    return ("其他信号", "模块实例化信号")

# _expected_always_regions 根据 always 目标推导允许区域。
def _expected_always_regions(dict_always: dict[str, Any]) -> tuple[str, ...]:
    """
    返回 always 块允许出现的区域集合。

    :param dict_always: formatter AST always 条目。
    :return: 允许区域标题元组。
    """

    # set_targets 保存 always 的赋值目标。
    set_targets = {str(item) for item in dict_always.get("targets", []) or []}  # always 赋值目标集合

    # 输出桥接内部寄存器属于输出信号处理区域。
    if any(str_target.endswith("_o") for str_target in set_targets):

        # 输出处理 always 应靠近输出信号处理区域。
        return ("输出信号处理区域",)

    # 状态寄存器和 next-state 组合块属于状态机区域。
    if "state_current" in set_targets or "state_next" in set_targets:

        # FSM 前两段归入状态机区域。
        return ("状态机区域",)

    # 引用状态但不更新状态寄存器的第三段逻辑属于状态任务处理区域。
    if _always_references_state_task(dict_always):

        # FSM 第三段归入状态任务处理区域。
        return ("状态任务处理区域",)

    # 其他 always 默认属于主要任务处理区域。
    return ("主要任务处理区域",)

# _module_output_ports 收集 module 顶层 output 端口名。
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

# _output_assign_region_issues 检查 output bridge assign 区域归属。
def _output_assign_region_issues(
    dict_module: dict[str, Any],
    set_output_ports: set[str],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 output bridge assign 是否位于输出信号连线区域。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param set_output_ports: 顶层 output 端口名集合。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把区域归属问题升级为 error。
    :return: output bridge 区域归属诊断列表。
    """

    # list_issues 保存 output bridge assign 的区域诊断。
    list_issues: list[QualityIssue] = []  # output bridge 区域诊断

    # region_context 保存单条 assign 区域判断所需的共享信息。
    region_context = OutputAssignRegionContext(  # VG052 output bridge 区域判定证据
        set_output_ports=set_output_ports,  # 用于识别 assign 是否驱动顶层输出
        dict_region_by_line=dict_region_by_line,  # 用于从 assign 行回溯最近横幅
        str_rel_path=str_rel_path,  # 写入 VG052 诊断的文件路径
        strict=strict,  # 控制 VG052 是否阻断交付
    )

    # 逐条 assign 判断 output bridge 归属，避免普通内部连线误报。
    for dict_assign in dict_module.get("assigns", []) or []:

        # 单条 assign helper 返回空列表或一条 VG052。
        list_issues.extend(_output_assign_region_issues_for_assign(dict_assign, region_context))

    # 返回 output bridge 区域诊断。
    return list_issues

# _output_assign_region_issues_for_assign 检查单条 assign 的区域归属。
def _output_assign_region_issues_for_assign(
    dict_assign: dict[str, Any],
    region_context: OutputAssignRegionContext,
) -> list[QualityIssue]:
    """
    检查单条 assign 是否违反 output bridge 区域归属。

    :param dict_assign: formatter AST 中的 assign 条目。
    :param region_context: output bridge assign 区域判断上下文。
    :return: 当前 assign 的区域归属诊断列表。
    """

    # str_lhs 标识当前 assign 是否正在驱动 output bridge。
    str_lhs = str(dict_assign.get("lhs") or "")  # output bridge 连续赋值左侧信号

    # 只检查 output bridge 语义的 assign。
    if str_lhs not in region_context.set_output_ports and not str_lhs.startswith("o_"):

        # 普通连线不属于输出桥接强规则。
        return []

    # int_line_no 用于把区域归属问题定位到 assign 起始行。
    int_line_no = _as_line(dict_assign.get("line_start"))  # output bridge assign 的源码起始行

    # 无行号时由 VG050 报告。
    if int_line_no is None:

        # 本规则依赖行号，缺失时跳过避免重复噪音。
        return []

    # str_region_title 是该 assign 前最近的区域横幅。
    str_region_title = _nearest_region_title(region_context.dict_region_by_line, int_line_no)  # assign 当前区域

    # 输出桥接位于正确区域时通过。
    if str_region_title == "输出信号连线":

        # assign 区域归属符合规范。
        return []

    # 区域归属错误会影响 formatter/审查对输出桥接的识别。
    return [
        QualityIssue(
            "VG052",
            _style_severity(region_context.strict),
            f"Output bridge assign `{str_lhs}` must be placed in 输出信号连线, "
            f"not `{str_region_title or 'unknown'}`.",
            region_context.str_rel_path,
            int_line_no,
            rule="regions.output_assign",
        )
    ]

# _rulebook_consistency_issues 检查 JSON 规则源和运行时代码是否漂移。
def _rulebook_consistency_issues(str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 rulebook JSON 是否仍是运行时规则的可信来源。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: 规则源一致性诊断列表。
    """

    # 读取规则源失败时必须转成 VG059，而不是让质量门崩溃。
    try:

        # rulebook_source 汇总区域、fallback 注释和 profile 规则。
        rulebook_source = load_verilog_rulebook()  # Verilog 风格规则源

    # 规则源不可用说明门禁无法可信执行。
    except Exception as exc:

        # 返回阻断诊断，提示维护者修复规则源。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                f"Verilog rulebook cannot be loaded: {exc}",
                str_rel_path,
                rule="rulebook.load",
            )
        ]

    # 区域横幅顺序必须和 JSON 中 regions 保持一致。
    if tuple(rulebook_source.region_labels) != tuple(REGION_KEYWORDS):

        # 硬编码表和 JSON 漂移时区域归属结论不可信。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Runtime region labels drifted from assets/verilog_style_rules.json.",
                str_rel_path,
                rule="rulebook.region_drift",
            )
        ]

    # fallback 注释列表缺失时 VG056 无法可信执行。
    if not rulebook_source.fallback_comments:

        # 空 fallback 配置代表规则源结构漂移。
        return [
            QualityIssue(
                "VG059",
                _style_severity(strict),
                "Rulebook must define comments.fallback_comments for deliverable gate.",
                str_rel_path,
                rule="rulebook.fallback_comments",
            )
        ]

    # 规则源一致时无诊断。
    return []

# _port_rules 检查端口命名和 ANSI header 风格。
def _port_rules(
    dict_module: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    vitis_wrapper: bool,
) -> list[QualityIssue]:
    """
    检查端口方向前缀和 top-level port 声明风格。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :return: 端口命名和注释诊断列表。
    """

    # list_issues 保存端口规则诊断。
    list_issues: list[QualityIssue] = []  # 端口规则诊断

    # 逐个端口应用方向前缀规则，Vitis 例外在循环内短路。
    for dict_port in dict_module.get("ports", []) or []:

        # 单个端口的方向前缀和重复前缀独立检查。
        list_issues.extend(_port_name_issues(dict_port, str_rel_path, strict=strict, vitis_wrapper=vitis_wrapper))

    # 文本 port 声明检查保留行号定位。
    list_issues.extend(_port_header_text_issues(str_text, str_rel_path, strict=strict))

    # 返回端口规则诊断。
    return list_issues

# _port_name_issues 检查单个端口的方向前缀。
def _port_name_issues(
    dict_port: dict[str, Any],
    str_rel_path: str,
    *,
    strict: bool,
    vitis_wrapper: bool,
) -> list[QualityIssue]:
    """
    检查单个端口是否符合方向前缀命名。

    :param dict_port: formatter AST 中的端口条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :param vitis_wrapper: 是否按 Vitis wrapper 端口规则放宽命名检查。
    :return: 当前端口命名诊断列表。
    """

    # str_direction 决定端口必须使用的 i_/o_/io_ 前缀。
    str_direction = str(dict_port.get("direction") or "")  # 前缀映射使用的端口方向

    # str_name 是当前端口命名规则的检查对象。
    str_name = str(dict_port.get("name") or "")  # 当前端口标识符

    # 空端口名由 AST/parser 诊断处理。
    if not str_name:

        # 当前端口无法执行命名规则。
        return []

    # Vitis wrapper 固定端口名不要求 Erie 前缀。
    if vitis_wrapper and _is_vitis_port(str_name):

        # 工具链固定端口直接跳过命名检查。
        return []

    # list_issues 保存单端口命名问题。
    list_issues: list[QualityIssue] = []  # 单端口命名诊断

    # str_expected_prefix 按方向映射 Erie 前缀。
    str_expected_prefix = {"input": "i_", "output": "o_", "inout": "io_"}.get(str_direction)  # 方向对应端口前缀

    # 已知方向端口必须带对应前缀。
    if str_expected_prefix and not str_name.startswith(str_expected_prefix):

        # 前缀错误会影响接口阅读和后续 formatter 分组。
        list_issues.append(
            QualityIssue(
                "VG010",
                _style_severity(strict),
                f"{str_direction} port `{str_name}` must use `{str_expected_prefix}` prefix.",
                str_rel_path,
                rule="naming.port_prefix",
            )
        )

    # 双重方向前缀通常来自生成器拼接错误。
    if str_name.startswith(("i_i_", "o_o_", "io_io_")):

        # 重复前缀登记为命名问题。
        list_issues.append(
            QualityIssue(
                "VG010",
                _style_severity(strict),
                f"Port `{str_name}` has duplicated direction prefix.",
                str_rel_path,
                rule="naming.no_duplicate_prefix",
            )
        )

    # 返回当前端口命名诊断。
    return list_issues

# _port_header_text_issues 检查端口声明行是否仍是旧式写法。
def _port_header_text_issues(str_text: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查端口声明行是否违反 ANSI header 风格。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把端口声明问题升级为 error。
    :return: 端口声明文本诊断列表。
    """

    # list_issues 保存端口声明行问题。
    list_issues: list[QualityIssue] = []  # 端口声明文本诊断

    # 逐行扫描文本 port 声明，定位遗留非 ANSI 写法。
    for int_line_no, str_line in enumerate(str_text.splitlines(), start=1):

        # 单行端口声明文本检查保持行号定位。
        list_issues.extend(_port_header_line_issues(str_line, int_line_no, str_rel_path, strict=strict))

    # 返回端口声明文本诊断。
    return list_issues

# _port_header_line_issues 检查单行端口声明文本。
def _port_header_line_issues(
    str_line: str,
    int_line_no: int,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单行端口声明是否仍含 wire/reg/logic 或 output reg。

    :param str_line: 当前源码行。
    :param int_line_no: 源码行号。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把端口声明问题升级为 error。
    :return: 当前端口声明行诊断列表。
    """

    # list_issues 保存当前行端口声明问题。
    list_issues: list[QualityIssue] = []  # 单行端口声明诊断

    # ANSI header 中端口声明不应显式带 wire/reg/logic。
    if re.search(r"^\s*(input|output|inout)\s+(wire|reg|logic)\b", str_line):

        # 端口声明类型关键字会破坏最终风格要求。
        list_issues.append(
            QualityIssue(
                "VG011",
                _style_severity(strict),
                "Port declarations must not include wire/reg/logic in final ANSI header style.",
                str_rel_path,
                int_line_no,
                "ports.no_kind_keyword",
            )
        )

    # output reg 端口应使用内部 _o bridge。
    if re.search(r"^\s*output\s+reg\b", str_line):

        # top-level output reg 会破坏输出桥接约束。
        list_issues.append(
            QualityIssue(
                "VG011",
                _style_severity(strict),
                "Top-level outputs must be driven through internal `_o` signals and "
                "assign bridges, not output reg ports.",
                str_rel_path,
                int_line_no,
                "ports.output_bridge",
            )
        )

    # 返回当前行端口声明诊断。
    return list_issues

# _parameter_rules 检查 parameter/localparam 命名。
def _parameter_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 module 参数和 localparam 命名约束。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 参数命名和注释诊断列表。
    """

    # list_issues 保存参数命名诊断。
    list_issues: list[QualityIssue] = []  # 参数规则诊断

    # module parameter 必须使用 C_ 大写命名。
    for dict_param in dict_module.get("params", []) or []:

        # 单个 parameter 的 C_ 前缀检查。
        list_issues.extend(_parameter_name_issues(dict_param, str_rel_path, strict=strict))

    # localparam 需要按状态参数和普通常量分流检查。
    for dict_param in dict_module.get("localparams", []) or []:

        # 单个 localparam 按 ST_ 和普通常量规则检查。
        list_issues.extend(_localparam_name_issues(dict_param, str_rel_path, strict=strict))

    # 返回参数命名诊断。
    return list_issues

# _parameter_name_issues 检查单个 module parameter 命名。
def _parameter_name_issues(dict_param: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查单个 parameter 是否使用 C_ 大写命名。

    :param dict_param: formatter AST 中的 parameter 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: parameter 命名诊断列表。
    """

    # str_name 是 C_ 参数命名规则的检查对象。
    str_name = str(dict_param.get("name") or "")  # 当前 parameter 标识符

    # 空名称不进入状态参数命名分支。
    if not str_name:

        # 无法生成稳定状态前缀诊断时跳过。
        return []

    # C_ 前缀只能出现一次，防止生成器重复拼接参数类别。
    if str_name.startswith(DUPLICATE_PARAMETER_PREFIXES[0]):

        # 重复 C_ 前缀仍归入 parameter 命名规则。
        return [
            QualityIssue(
                "VG012",
                _style_severity(strict),
                f"Module parameter `{str_name}` has duplicated `C_` prefix.",
                str_rel_path,
                rule="naming.no_duplicate_prefix",
            )
        ]

    # 合规 C_ 大写命名不产生诊断。
    if re.fullmatch(r"C_[A-Z0-9_]+", str_name):

        # parameter 命名已满足规则。
        return []

    # 参数命名问题登记为 VG012。
    return [
        QualityIssue(
            "VG012",
            _style_severity(strict),
            f"Module parameter `{str_name}` must use `C_` + uppercase naming.",
            str_rel_path,
            rule="naming.parameter",
        )
    ]

# _localparam_name_issues 区分 ST_ 状态枚举和普通局部常量。
def _localparam_name_issues(dict_param: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查单个 localparam 是否符合状态或常量命名。

    :param dict_param: formatter AST 中的 localparam 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: localparam 命名诊断列表。
    """

    # str_name 用于 localparam 命名规则和状态枚举识别。
    str_name = str(dict_param.get("name") or "")  # 状态枚举候选 localparam 名

    # 空名称由 AST 解析层负责。
    if not str_name:

        # 无名称时跳过命名规则。
        return []

    # 状态参数前缀只能出现一次。
    if str_name.startswith(DUPLICATE_PARAMETER_PREFIXES[1]):

        # 重复状态前缀通常来自生成器拼接错误。
        return [
            QualityIssue(
                "VG012",
                _style_severity(strict),
                f"State localparam `{str_name}` has duplicated `ST_` prefix.",
                str_rel_path,
                rule="naming.no_duplicate_prefix",
            )
        ]

    # ST_ 状态参数有更严格的状态命名规则。
    if str_name.startswith("ST_"):

        # 状态参数命名合规时直接通过。
        if re.fullmatch(r"ST_[A-Z0-9_]+", str_name):

            # ST_ 后接大写和数字。
            return []

        # 状态参数命名问题保持 VG012。
        return [
            QualityIssue(
                "VG012",
                _style_severity(strict),
                f"State localparam `{str_name}` must use `ST_` + uppercase naming.",
                str_rel_path,
                rule="naming.state_parameter",
            )
        ]

    # 普通 localparam 应为全大写。
    if UPPER_IDENTIFIER_PATTERN.fullmatch(str_name):

        # 普通常量命名合规。
        return []

    # 普通 localparam 大写约束。
    return [
        QualityIssue(
            "VG012",
            _style_severity(strict),
            f"localparam `{str_name}` should be uppercase.",
            str_rel_path,
            rule="naming.localparam",
        )
    ]

# _signal_rules 检查内部声明命名与输出桥接冲突。
def _signal_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查内部信号、寄存器、计数器和 flag 类命名。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 信号命名、区域和注释诊断列表。
    """

    # list_issues 保存信号命名诊断。
    list_issues: list[QualityIssue] = []  # 信号规则诊断

    # set_output_ports 建立内部声明重名 output 的判定基准。
    set_output_ports = _module_output_ports(dict_module)  # output 重声明检测使用的端口名集合

    # 遍历内部声明模型。
    for dict_decl in dict_module.get("decls", []) or []:

        # 单个内部声明的前缀、重声明和语义命名独立检查。
        list_issues.extend(_signal_decl_issues(dict_decl, set_output_ports, str_rel_path, strict=strict))

    # 返回信号命名诊断。
    return list_issues

# _signal_decl_issues 检查单个内部声明命名。
def _signal_decl_issues(
    dict_decl: dict[str, Any],
    set_output_ports: set[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查单个内部声明的前缀、重声明和语义命名。

    :param dict_decl: formatter AST 中的内部声明条目。
    :param set_output_ports: 顶层 output 端口名集合。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: 当前内部声明的命名诊断列表。
    """

    # str_name 是内部声明名。
    str_name = str(dict_decl.get("name") or "")  # 内部信号名称

    # 空名称由 parser 诊断负责。
    if not str_name:

        # 无名称声明跳过命名判断。
        return []

    # str_kind 是 wire/reg/logic 等声明类型。
    str_kind = str(dict_decl.get("kind") or "")  # 内部信号声明类型

    # list_issues 保存单个内部声明的命名问题。
    list_issues: list[QualityIssue] = []  # 单声明命名诊断

    # 内部分类前缀只能出现一次。
    if str_name.startswith(DUPLICATE_SIGNAL_PREFIXES):

        # 重复分类前缀说明命名拼接过程已经失控。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Internal signal `{name}` has duplicated semantic prefix.",
                "naming.no_duplicate_prefix",
                str_rel_path,
                strict=strict,
            )
        )

    # 内部信号不能抢占 top-level output 前缀。
    if str_name.startswith("o_"):

        # 输出桥接应使用 _o 后缀。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Internal signal `{name}` must not use output-port `o_` prefix; use `_o` suffix for output bridges.",
                "naming.internal_output",
                str_rel_path,
                strict=strict,
            )
        )

    # 内部声明不应重声明输出端口。
    if str_name in set_output_ports:

        # 输出端口重声明会造成驱动语义混乱。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Top-level output `{name}` is redeclared internally.",
                "naming.output_redecl",
                str_rel_path,
                strict=strict,
            )
        )

    # reg/logic 信号应使用项目约定前缀或输出桥接后缀。
    if str_kind in {"reg", "logic"} and not _expected_reg_name(str_name):

        # 寄存器类命名不符合 Erie 规则。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Register `{name}` should use reg_/cnt_/state_/flag_/enc_/dec_ prefix or `_o` output suffix.",
                "naming.register_signal",
                str_rel_path,
                strict=strict,
            )
        )

    # 追加计数、flag、编码和译码语义命名诊断。
    list_issues.extend(_signal_semantic_name_issues(str_name, str_rel_path, strict=strict))

    # 返回单个内部声明的诊断。
    return list_issues

# _signal_semantic_name_issues 检查计数、flag、编码和译码语义前缀。
def _signal_semantic_name_issues(str_name: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查内部信号是否按语义使用 cnt_/flag_/enc_/dec_ 前缀。

    :param str_name: 内部信号名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: 语义前缀诊断列表。
    """

    # list_issues 保存语义命名问题。
    list_issues: list[QualityIssue] = []  # 语义命名诊断

    # 计数语义信号应使用 cnt_ 前缀。
    if _looks_counter(str_name) and not str_name.startswith("cnt_"):

        # 计数器命名问题登记为 VG013。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Counter-like signal `{name}` should use `cnt_` prefix.",
                "naming.counter",
                str_rel_path,
                strict=strict,
            )
        )

    # flag 类信号除端口和输出桥接外应使用 flag_ 前缀。
    if _flag_name_needs_prefix(str_name):

        # flag 命名问题登记为 VG013。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Flag-like signal `{name}` should use `flag_` prefix unless it is an output bridge.",
                "naming.flag",
                str_rel_path,
                strict=strict,
            )
        )

    # encoder 语义信号应使用 enc_ 前缀。
    if _looks_encoder(str_name) and not str_name.startswith("enc_"):

        # 编码信号命名问题。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Encoder-like signal `{name}` should use `enc_` prefix.",
                "naming.encoder",
                str_rel_path,
                strict=strict,
            )
        )

    # 译码语义信号缺少 dec_ 前缀时会污染区域归类。
    if _looks_decoder(str_name) and not str_name.startswith("dec_"):

        # 译码信号命名问题。
        list_issues.append(
            _signal_naming_issue(
                str_name,
                "Decoder-like signal `{name}` should use `dec_` prefix.",
                "naming.decoder",
                str_rel_path,
                strict=strict,
            )
        )

    # 返回语义前缀诊断。
    return list_issues

# _signal_naming_issue 构造内部信号 VG013 诊断。
def _signal_naming_issue(
    str_name: str,
    str_message_template: str,
    str_rule: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> QualityIssue:
    """
    构造内部信号命名诊断。

    :param str_name: 内部信号名称。
    :param str_message_template: 包含 {name} 占位符的诊断文本模板。
    :param str_rule: 命名子规则名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把命名问题升级为 error。
    :return: 内部信号命名诊断。
    """

    # str_message 使用真实信号名填充模板。
    str_message = str_message_template.format(name=str_name)  # 当前信号诊断文本

    # 返回统一 VG013 诊断对象。
    return QualityIssue("VG013", _style_severity(strict), str_message, str_rel_path, rule=str_rule)

# _assign_rules 检查连续赋值和输出桥接。
def _assign_rules(
    dict_module: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 assign 写法和 top-level output 桥接约束。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: assign 语句相关诊断列表。
    """

    # list_issues 保存 assign 相关诊断。
    list_issues: list[QualityIssue] = []  # assign 规则诊断

    # set_output_ports 是输出桥接规则需要覆盖的 top-level output 集合。
    set_output_ports = {  # 输出桥接规则覆盖的 top-level output 集合
        str(dict_port.get("name"))  # 待检查桥接关系的输出端口名
        for dict_port in dict_module.get("ports", [])  # 扫描端口 AST 条目
        if str(dict_port.get("direction")) == "output"  # 只保留 output 方向端口
    }

    # set_output_bridge_targets 记录 assign 直接驱动的 output。
    set_output_bridge_targets = {  # 已通过 assign 语句桥接的输出端口集合
        str(dict_assign.get("lhs"))  # assign 左值中的输出端口名
        for dict_assign in dict_module.get("assigns", [])  # 遍历连续赋值条目
        if str(dict_assign.get("lhs")) in set_output_ports  # assign 左值直接命中输出端口
    }

    # 文本扫描用于捕获 inline wire initialization。
    for int_line_no, str_line in enumerate(str_text.splitlines(), start=1):

        # wire 声明行中不应直接初始化。
        if re.search(r"\bwire\b[^;]*=", _strip_line_comment(str_line)):

            # inline wire 初始化应拆成声明和 assign。
            list_issues.append(
                QualityIssue(
                    "VG030",
                    _style_severity(strict),
                    "Inline wire initialization is forbidden; declare wire and use a separate assign.",
                    str_rel_path,
                    int_line_no,
                    "assign.inline_wire",
                )
            )

    # 每个输出端口检查 always 驱动和 assign bridge。
    for str_port in sorted(set_output_ports):

        # bool_driven_in_always 标记输出端口是否在 always 中被直接赋值。
        bool_driven_in_always = any(  # 当前输出端口是否被 always 块直接赋值
            str_port in dict_always.get("targets", [])  # always 目标是否包含该输出端口
            for dict_always in dict_module.get("always", [])  # 遍历 always 结构条目
        )

        # always 中直接驱动输出端口违反桥接规则。
        if bool_driven_in_always:

            # 输出端口应经由内部 _o 信号和 assign bridge。
            list_issues.append(
                QualityIssue(
                    "VG014",
                    _style_severity(strict),
                    f"Output port `{str_port}` is assigned in an always block; "
                    "drive an internal `_o` signal and bridge with assign.",
                    str_rel_path,
                    rule="output.bridge",
                )
            )

        # 未检测到 assign bridge 时保留 warning，允许直接组合输出人工确认。
        if str_port not in set_output_bridge_targets:

            # 该诊断历史上为 advisory warning，保持兼容。
            list_issues.append(
                QualityIssue(
                    "VG014",
                    "warning",
                    f"Output port `{str_port}` has no explicit assign bridge detected; "
                    "confirm direct output assignment is intentional.",
                    str_rel_path,
                    rule="output.bridge",
                )
            )

    # 返回 assign 规则诊断。
    return list_issues

# _always_rules 检查 always 块目标、复位和赋值类型。
def _always_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 always 块是否符合 Erie 的单目标和赋值类型约束。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: always 块相关诊断列表。
    """

    # list_issues 保存 always 规则诊断。
    list_issues: list[QualityIssue] = []  # always 块规则诊断

    # 逐个 always 检查单目标、reset 和赋值类型约束。
    for dict_always in dict_module.get("always", []) or []:

        # 单个 always 的目标数量、reset、赋值类型和复杂左值独立检查。
        list_issues.extend(_always_block_issues(dict_always, str_rel_path, strict=strict))

    # 返回复杂 lvalue、blocking 和复位风格等 always 诊断。
    return list_issues

# _always_block_issues 检查单个 always 块规则。
def _always_block_issues(dict_always: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查单个 always 块的目标数量、reset 和赋值类型。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 当前 always 块诊断列表。
    """

    # str_header 用于诊断定位具体 always 块。
    str_header = str(dict_always.get("header") or "")  # always 块头文本

    # list_targets 是 formatter 提取的赋值目标集合。
    list_targets = [
        str(item)  # 当前 always 块的赋值目标名
        for item in dict_always.get("targets", []) or []  # 遍历 formatter 提取目标
    ]  # 单目标 always 规则使用的赋值目标列表

    # list_issues 保存当前 always 块诊断。
    list_issues: list[QualityIssue] = []  # 单个 always 块诊断

    # 单目标约束先检查 always 是否需要拆块。
    list_issues.extend(_always_target_issues(str_header, list_targets, str_rel_path, strict=strict))

    # 时序 always 检查 reset 风格和阻塞赋值。
    list_issues.extend(_sequential_always_issues(dict_always, str_header, str_rel_path, strict=strict))

    # 组合 always 检查是否误用非阻塞赋值。
    list_issues.extend(_combinational_always_issues(dict_always, str_header, str_rel_path, strict=strict))

    # 复杂左值规则阻止 formatter 盲猜多目标拆分。
    list_issues.extend(
        _always_complex_lvalue_issues(dict_always, str_header, list_targets, str_rel_path, strict=strict)
    )

    # 返回当前 always 块全部诊断。
    return list_issues

# _always_target_issues 检查 always 是否只驱动一个目标。
def _always_target_issues(
    str_header: str,
    list_targets: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 always 块是否只驱动一个唯一目标。

    :param str_header: always 块头文本。
    :param list_targets: formatter 提取的赋值目标列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 单目标规则诊断列表。
    """

    # 多目标 always 块应拆分。
    if len(set(list_targets)) <= 1:

        # 当前 always 满足单目标约束。
        return []

    # 单目标约束方便人工审查和后续注释生成。
    return [
        QualityIssue(
            "VG020",
            _style_severity(strict),
            f"Always block `{str_header}` assigns multiple targets {sorted(set(list_targets))}; "
            "split to one target per always.",
            str_rel_path,
            rule="always.single_target",
        )
    ]

# _sequential_always_issues 检查时序 always 的 reset 和赋值类型。
def _sequential_always_issues(
    dict_always: dict[str, Any],
    str_header: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查时序 always 是否带低有效 reset 并使用非阻塞赋值。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_header: always 块头文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 时序 always 诊断列表。
    """

    # 非时序 always 不进入 reset 和非阻塞赋值规则。
    if dict_always.get("trigger_kind") != "seq":

        # 组合块由组合规则处理。
        return []

    # list_issues 保存时序 always 问题。
    list_issues: list[QualityIssue] = []  # 时序 always 诊断

    # 当前 always 的 reset 名称由 formatter AST 推断。
    str_reset = str(dict_always.get("reset") or "")  # 时序 always 复位信号名

    # reset 风格检查和赋值类型检查分别追加。
    list_issues.extend(_sequential_reset_style_issues(str_header, str_reset, str_rel_path, strict=strict))

    # 时序逻辑中出现阻塞赋值时登记问题。
    if any(_has_blocking_assignment(str_line) for str_line in dict_always.get("lines", []) or []):

        # 时序 always 只允许非阻塞赋值。
        list_issues.append(
            QualityIssue(
                "VG022",
                _style_severity(strict),
                f"Sequential always `{str_header}` must use nonblocking assignments only.",
                str_rel_path,
                rule="always.seq_nonblocking",
            )
        )

    # 返回时序 always 诊断。
    return list_issues

# _sequential_reset_style_issues 检查时序 always reset 声明风格。
def _sequential_reset_style_issues(
    str_header: str,
    str_reset: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查时序 always 是否声明低有效 reset。

    :param str_header: always 块头文本。
    :param str_reset: formatter 推断出的 reset 名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 reset 风格问题升级为 error。
    :return: reset 风格诊断列表。
    """

    # 时序 always 必须带复位。
    if not str_reset:

        # 缺复位直接登记 VG021。
        return [
            QualityIssue(
                "VG021",
                _style_severity(strict),
                f"Sequential always `{str_header}` must include an active-low reset in the sensitivity list.",
                str_rel_path,
                rule="always.reset",
            )
        ]

    # 复位名称和触发边沿需符合低有效约定。
    if not _bad_reset_style(str_header, str_reset):

        # reset 命名和 negedge 风格都符合规则。
        return []

    # 复位命名或边沿不符合 Erie 规则。
    return [
        QualityIssue(
            "VG021",
            _style_severity(strict),
            f"Sequential always `{str_header}` should use negedge active-low reset naming such as i_rstn/i_axis_arstn.",
            str_rel_path,
            rule="always.reset",
        )
    ]

# _combinational_always_issues 检查组合 always 赋值类型。
def _combinational_always_issues(
    dict_always: dict[str, Any],
    str_header: str,
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查组合 always 是否只使用阻塞赋值。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_header: always 块头文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 组合 always 诊断列表。
    """

    # 非组合 always 不进入该规则。
    if not dict_always.get("is_combinational"):

        # 时序块由时序规则处理。
        return []

    # bool_has_nonblocking 标记组合逻辑是否误用非阻塞赋值。
    bool_has_nonblocking = any(  # 组合 always 中是否存在非阻塞赋值
        _has_nonblocking_assignment(str(str_line))  # 当前源码行是否包含 <=
        for str_line in dict_always.get("lines", []) or []  # 遍历 always 内部源码行
    )

    # 组合 always 没有非阻塞赋值时通过。
    if not bool_has_nonblocking:

        # 赋值类型符合组合逻辑约束。
        return []

    # 组合 always 只允许阻塞赋值。
    return [
        QualityIssue(
            "VG022",
            _style_severity(strict),
            f"Combinational always `{str_header}` must use blocking assignments only.",
            str_rel_path,
            rule="always.comb_blocking",
        )
    ]

# _always_complex_lvalue_issues 检查复杂左值多目标 always。
def _always_complex_lvalue_issues(
    dict_always: dict[str, Any],
    str_header: str,
    list_targets: list[str],
    str_rel_path: str,
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查复杂左值 always 是否仍包含多个目标。

    :param dict_always: formatter AST 中的 always 条目。
    :param str_header: always 块头文本。
    :param list_targets: formatter 提取的赋值目标列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把 always 问题升级为 error。
    :return: 复杂左值诊断列表。
    """

    # 复杂左值和多目标组合时 formatter 不能安全猜测拆分。
    if not dict_always.get("has_complex_lvalues") or len(set(list_targets)) <= 1:

        # 当前 always 无需人工拆分复杂左值。
        return []

    # 复杂左值多目标 always 需要人工或生成器显式拆分。
    return [
        QualityIssue(
            "VG020",
            _style_severity(strict),
            f"Always block `{str_header}` has complex lvalues and multiple targets; formatter must not guess a split.",
            str_rel_path,
            rule="always.complex_lvalue",
        )
    ]

# _fsm_rules 检查三段式 FSM 约束。
def _fsm_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查状态参数、状态寄存器和三段式 FSM 结构。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: FSM 结构相关诊断列表。
    """

    # list_issues 保存三段式 FSM 结构、状态命名和状态任务诊断。
    list_issues: list[QualityIssue] = []  # FSM 结构诊断集合

    # list_state_params 收集 ST_ 状态参数。
    list_state_params = _fsm_state_params(dict_module)  # ST_ 状态枚举参数列表

    # list_state_signals 收集 FSM current/next 状态信号候选。
    list_state_signals = _fsm_state_signals(dict_module)  # state_ 状态信号列表

    # 没有状态参数和状态信号时不执行 FSM 规则。
    if not list_state_params and not list_state_signals:

        # 非 FSM 模块无需三段式约束。
        return list_issues

    # str_severity 让 FSM 结构问题跟随 strict 模式升级或降级。
    str_severity = "error" if strict else "warning"  # FSM gate 输出级别

    # 状态信号检查确认 state_current/state_next 声明齐备。
    list_issues.extend(_fsm_state_signal_issues(list_state_signals, str_rel_path, str_severity))

    # FSM 分段检查确认状态寄存器段和 next-state 段存在。
    list_issues.extend(_fsm_segment_issues(dict_module, list_state_params, str_rel_path, str_severity))

    # 状态枚举命名检查 ST_ 后缀是否保持大写。
    list_issues.extend(_fsm_state_name_issues(list_state_params, str_rel_path, strict=strict))

    # next-state 组合段必须包含 default 和默认保持。
    list_issues.extend(_fsm_next_state_rules(dict_module, str_rel_path, strict=strict))

    # 返回状态枚举、三段式分段和状态任务诊断。
    return list_issues

# _fsm_state_params 提取 localparam 中的 FSM 状态枚举名称。
def _fsm_state_params(dict_module: dict[str, Any]) -> list[str]:
    """
    收集 module 中的 ST_ 状态枚举参数。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: ST_ 状态参数名列表。
    """

    # 返回所有状态枚举参数名。
    return [
        str(dict_item.get("name"))  # ST_ 状态枚举名
        for dict_item in dict_module.get("localparams", [])  # 遍历 localparam 条目
        if str(dict_item.get("name", "")).startswith("ST_")  # 只保留状态枚举参数
    ]

# _fsm_state_signals 收集 state_ 状态信号。
def _fsm_state_signals(dict_module: dict[str, Any]) -> list[str]:
    """
    收集 module 中的 state_ 状态信号。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: state_ 状态信号名列表。
    """

    # 返回 current/next 等状态信号名。
    return [
        str(dict_item.get("name"))  # state_ 前缀状态信号名
        for dict_item in dict_module.get("decls", [])  # 遍历内部声明条目
        if str(dict_item.get("name", "")).startswith("state_")  # 只保留状态信号声明
    ]

# _fsm_state_signal_issues 检查 current/next 状态信号是否齐备。
def _fsm_state_signal_issues(
    list_state_signals: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 FSM 是否同时声明 state_current 和 state_next。

    :param list_state_signals: state_ 状态信号名列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: FSM 结构诊断级别。
    :return: 状态信号诊断列表。
    """

    # 必须存在 current 和 next 两个状态信号。
    if "state_current" in list_state_signals and "state_next" in list_state_signals:

        # 状态信号满足三段式约定。
        return []

    # 状态信号缺失时登记三段式问题。
    return [
        QualityIssue(
            "VG023",
            str_severity,
            "FSM must use `state_current` and `state_next` signals.",
            str_rel_path,
            rule="fsm.three_segment",
        )
    ]

# _fsm_segment_issues 检查 FSM 三段式 always 结构。
def _fsm_segment_issues(
    dict_module: dict[str, Any],
    list_state_params: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查 FSM 是否具备状态寄存器段、next-state 段和状态任务段。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_state_params: ST_ 状态参数名列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: FSM 结构诊断级别。
    :return: FSM 分段结构诊断列表。
    """

    # tuple_flags 表示状态寄存器、next-state 和状态任务段是否存在。
    tuple_flags = _fsm_segment_flags(dict_module)  # (状态寄存器段, next-state 段, 状态任务段)

    # list_issues 保存 FSM 分段问题。
    list_issues: list[QualityIssue] = []  # FSM 分段诊断

    # 状态寄存器段和下一状态段必须同时存在。
    if not (tuple_flags[0] and tuple_flags[1]):

        # 三段式至少需要 state register 和 next-state combinational 两段。
        list_issues.extend(_fsm_missing_core_segment_issues(str_rel_path, str_severity))

    # 有状态参数但没有独立状态任务块时给 warning。
    if list_state_params and not tuple_flags[2]:

        # 第三段可能是直接输出，保留人工确认空间。
        list_issues.append(
            QualityIssue(
                "VG023",
                "warning",
                "FSM has state parameters but no separate state task/output block was detected; "
                "confirm three-segment FSM intent.",
                str_rel_path,
                rule="fsm.three_segment",
            )
        )

    # 返回 FSM 分段诊断。
    return list_issues

# _fsm_segment_flags 返回 FSM 三个 always 段是否存在。
def _fsm_segment_flags(dict_module: dict[str, Any]) -> tuple[bool, bool, bool]:
    """
    判断 FSM 状态寄存器段、next-state 段和状态任务段是否存在。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 三个布尔值分别表示状态寄存器段、next-state 段和状态任务段。
    """

    # bool_seq_state 表示状态寄存器段存在。
    bool_seq_state = any(  # 三段式 FSM 的状态寄存器段是否存在
        dict_always.get("trigger_kind") == "seq"  # 时序段候选 always
        and "state_current" in dict_always.get("targets", [])  # 目标包含当前态寄存器
        for dict_always in dict_module.get("always", [])  # 扫描候选时序 always
    )

    # bool_comb_next 表示 next-state 组合逻辑段存在。
    bool_comb_next = any(  # 三段式 FSM 的 next-state 组合段是否存在
        dict_always.get("is_combinational")  # 组合段候选 always
        and "state_next" in dict_always.get("targets", [])  # 目标包含下一态信号
        for dict_always in dict_module.get("always", [])  # 扫描 next-state 候选块
    )

    # bool_state_task 表示独立状态输出/任务段存在。
    bool_state_task = any(  # 是否存在第三段状态输出/任务逻辑
        _always_references_state_task(dict_always)  # 状态输出/任务段候选 always
        for dict_always in dict_module.get("always", [])  # 扫描第三段候选块
    )

    # 返回三个分段是否存在。
    return bool_seq_state, bool_comb_next, bool_state_task

# _fsm_missing_core_segment_issues 构造缺失核心 FSM 分段诊断。
def _fsm_missing_core_segment_issues(str_rel_path: str, str_severity: str) -> list[QualityIssue]:
    """
    构造状态寄存器段或 next-state 段缺失诊断。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: FSM 结构诊断级别。
    :return: 缺失核心分段诊断列表。
    """

    # 新旧规则号同时保留，兼容已有测试和新交付门禁。
    return [
        QualityIssue(
            "VG023",
            str_severity,
            "FSM must be generated as at least state-register and next-state combinational blocks.",
            str_rel_path,
            rule="fsm.three_segment",
        ),
        QualityIssue(
            "VG054",
            str_severity,
            "FSM delivery must keep separate state-register and next-state combinational blocks.",
            str_rel_path,
            rule="fsm.strict_three_segment",
        ),
    ]

# _fsm_state_name_issues 检查 ST_ 状态参数命名。
def _fsm_state_name_issues(list_state_params: list[str], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 FSM 状态参数是否使用 ST_ 大写命名。

    :param list_state_params: ST_ 状态参数名列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把状态命名问题升级为 error。
    :return: 状态参数命名诊断列表。
    """

    # list_issues 保存状态参数命名问题。
    list_issues: list[QualityIssue] = []  # FSM 状态命名诊断

    # 状态参数命名再次按 FSM 语义校验。
    for str_param_name in list_state_params:

        # 合规状态名跳过。
        if re.fullmatch(r"ST_[A-Z0-9_]+", str_param_name):

            # ST_ 后为大写状态名。
            continue

        # 状态名问题归入 FSM 规则。
        list_issues.append(
            QualityIssue(
                "VG023",
                _style_severity(strict),
                f"State parameter `{str_param_name}` must use ST_ uppercase naming.",
                str_rel_path,
                rule="fsm.state_name",
            )
        )

    # 返回状态参数命名诊断。
    return list_issues

# _instance_rules 检查实例名是否具备语义。
def _instance_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查子模块实例名是否避免 u0/u1/inst 等泛化命名。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 模块实例化相关诊断列表。
    """

    # list_issues 保存实例命名诊断。
    list_issues: list[QualityIssue] = []  # 实例命名规则诊断

    # 逐个实例检查是否仍是生成器默认名。
    for dict_inst in dict_module.get("instances", []) or []:

        # str_instance_name 是当前实例名。
        str_instance_name = str(dict_inst.get("instance_name") or "")  # 子模块实例名

        # 空实例名由 parser 诊断处理。
        if not str_instance_name:

            # 无实例名时跳过命名规则。
            continue

        # 泛化实例名不可用于生成交付 RTL。
        if str_instance_name in {"u0", "u1", "inst", "inst0"} or re.fullmatch(r"u\d+", str_instance_name):

            # 泛化实例名缺少连接语义。
            list_issues.append(
                QualityIssue(
                    "VG024",
                    _style_severity(strict),
                    f"Instance `{str_instance_name}` should be semantic, not generic u0/u1/inst.",
                    str_rel_path,
                    rule="instance.naming",
                )
            )

        # 推荐实例名包含 _Inst 结构。
        if "_Inst" not in str_instance_name and not str_instance_name.endswith("_Inst"):

            # 命名建议保持 warning，避免破坏兼容模块。
            list_issues.append(
                QualityIssue(
                    "VG024",
                    "warning",
                    f"Instance `{str_instance_name}` should follow `<module>_Inst_<role>` naming when practical.",
                    str_rel_path,
                    rule="instance.naming",
                )
            )

    # 返回实例命名诊断。
    return list_issues

# _region_rules 检查 formatter 区域横幅是否存在且有序。
def _region_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查非平凡 RTL 是否带有固定区域横幅并保持顺序。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 区域横幅和顺序相关诊断列表。
    """

    # list_issues 保存区域横幅缺失、乱序和实例区域诊断。
    list_issues: list[QualityIssue] = []  # 区域横幅规则诊断

    # int_body_activity 粗略衡量模块主体复杂度。
    int_body_activity = sum(  # 判断是否需要强制区域横幅的结构条目数量
        dict_module.get("counts", {}).get(str_key, 0)  # 单类 module 主体结构数量
        for str_key in ("decls", "assigns", "always", "instances", "generates")  # 结构计数键
    )

    # 简单 wrapper 或空叶子模块不强制区域横幅。
    if int_body_activity < 3:

        # 轻量模块跳过区域规则。
        return list_issues

    # list_found 保存源码中出现的已知区域标题。
    list_found = [
        str_keyword  # 已命中的 Erie 区域标题
        for str_keyword in REGION_KEYWORDS  # 遍历既定区域顺序表
        if str_keyword in str_text  # 仅记录源码实际包含的区域标题
    ]  # 源码中命中的 Erie 区域横幅标题

    # 非平凡模块必须至少有区域横幅。
    if not list_found:

        # 缺区域横幅直接登记 VG031。
        list_issues.append(
            QualityIssue(
                "VG031",
                _style_severity(strict),
                "Non-trivial RTL must use fixed Erie region banners.",
                str_rel_path,
                rule="regions.banner",
            )
        )

        # 没有任何区域时无需继续检查顺序。
        return list_issues

    # list_positions 按既定顺序记录区域标题在源码中的位置。
    list_positions = [
        str_text.find(str_keyword)  # 区域横幅在源码中的字符偏移
        for str_keyword in REGION_KEYWORDS  # 按规范区域顺序扫描
        if str_keyword in str_text  # 仅记录已出现标题的位置
    ]  # 已命中区域横幅在源码中的出现位置

    # 区域出现顺序必须和 REGION_KEYWORDS 一致。
    if list_positions != sorted(list_positions):

        # 顺序错乱会影响生成 RTL 的可扫描性。
        list_issues.append(
            QualityIssue(
                "VG031",
                _style_severity(strict),
                "Region banners appear out of the required order.",
                str_rel_path,
                rule="regions.order",
            )
        )

    # 有实例化时必须放在最终实例化区域。
    if dict_module.get("instances") and "模块实例化区域" not in str_text:

        # 实例区域缺失时登记 VG031。
        list_issues.append(
            QualityIssue(
                "VG031",
                _style_severity(strict),
                "Module instances must be in the final 模块实例化区域 banner.",
                str_rel_path,
                rule="regions.instances_last",
            )
        )

    # 返回区域横幅诊断。
    return list_issues

# _comment_rules 汇总结构化注释覆盖和语义检查。
def _comment_rules(
    dict_ast_report: dict[str, Any],
    str_text: str,
    str_rel_path: str,
    *,
    strict: bool,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查声明、赋值、always 和实例的注释覆盖及语义。

    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :param comment_language: 注释语言策略。
    :return: 注释覆盖和语义相关诊断列表。
    """

    # list_issues 汇总所有注释相关诊断。
    list_issues: list[QualityIssue] = []  # 注释规则诊断

    # str_severity 根据 strict 决定缺注释问题级别。
    str_severity = _comment_severity(strict)  # 注释覆盖严重级别

    # list_lines 供需要精确空行和同线注释位置的规则复用。
    list_lines = str_text.splitlines()  # 当前 Verilog 源码行列表

    # dict_region_by_line 记录区域横幅位置，供分组注释和前导空行规则定位。
    dict_region_by_line = _line_region_titles(str_text)  # 源码区域横幅索引

    # 逐个 module 检查声明同 行注释与块级 leading comment。
    for dict_module in dict_ast_report.get("modules", []) or []:

        # tuple_structured_args 固定结构化注释 helper 的参数顺序。
        tuple_structured_args = (dict_module, str_rel_path, str_severity, comment_language)  # 声明注释检查参数

        # 结构化声明和赋值注释检查拆给 helper。
        list_structured_issues = _structured_comment_issues(*tuple_structured_args)  # 声明和连续赋值注释诊断

        # 合并结构化条目的注释诊断。
        list_issues.extend(list_structured_issues)

        # tuple_block_args 固定前导注释 helper 的参数顺序。
        tuple_block_args = (dict_module, list_lines, str_rel_path, str_severity)  # 块前导注释检查参数

        # always/function/task/generate/initial 和 instance 的前导注释单独检查。
        list_block_comment_issues = _block_comment_issues(*tuple_block_args)  # 过程块和实例前导注释诊断

        # 合并块级前导注释诊断。
        list_issues.extend(list_block_comment_issues)

        # tuple_procedural_args 固定过程赋值注释 helper 的参数顺序。
        tuple_procedural_args = (dict_module, list_lines, str_rel_path, str_severity, comment_language)  # 过程赋值参数

        # 过程块内部赋值必须具备同线语义注释。
        list_procedural_issues = _procedural_assignment_comment_issues(*tuple_procedural_args)  # 过程赋值注释诊断

        # 合并过程赋值注释诊断。
        list_issues.extend(list_procedural_issues)

        # tuple_instance_args 固定实例关联注释 helper 的参数顺序。
        tuple_instance_args = tuple_procedural_args  # 实例关联注释检查参数

        # 实例化参数和端口连线必须具备同线语义注释。
        list_instance_mapping_issues = _instance_mapping_comment_issues(*tuple_instance_args)  # 实例关联注释诊断

        # 合并实例关联注释诊断。
        list_issues.extend(list_instance_mapping_issues)

        # tuple_group_args 固定定义分组注释 helper 的参数顺序。
        tuple_group_args = (dict_module, list_lines, dict_region_by_line, str_rel_path, str_severity)  # 分组注释参数

        # 参数和信号定义区域必须有分组注释。
        list_group_comment_issues = _definition_group_comment_issues(*tuple_group_args)  # 参数和信号分组诊断

        # 合并定义分组注释诊断。
        list_issues.extend(list_group_comment_issues)

        # list_reuse_candidates 只收集绑定 RTL 实体的语义注释。
        list_reuse_candidates = _comment_reuse_candidates_for_module(  # 当前 module 的实体注释候选
            dict_module,  # 重复检测所在 module
            list_lines,  # 重复检测源码行
            str_rel_path,  # 重复诊断相对路径
        )  # VG066 重复注释候选集合

        # 重复或近似复用注释应在后出现的实体上报告。
        list_issues.extend(_comment_reuse_issues(list_reuse_candidates, str_severity))

    # 注释覆盖率按文本行统计。
    list_issues.extend(_comment_density_issues(str_text, str_rel_path, strict=strict))

    # 返回 AST 结构注释和文本密度合并后的诊断。
    return list_issues

# _structured_comment_issues 检查声明、端口、参数和 assign 注释。
def _structured_comment_issues(
    dict_module: dict[str, Any],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查 AST 结构条目的同 行语义注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: str_severity 文本值，供质量门规则匹配。
    :param comment_language: 注释语言策略。
    :return: 结构化声明和语句的注释诊断列表。
    """

    # list_issues 保存声明、端口、assign 的 same-line 注释诊断。
    list_issues: list[QualityIssue] = []  # 结构化注释覆盖诊断

    # tuple_collections 定义需要 same-line 注释的 AST 集合。
    tuple_collections = (  # 结构化注释覆盖规则需要扫描的 AST 集合
        ("params", "parameter"),  # parameter 条目及诊断标签
        ("localparams", "localparam"),  # 状态和普通常量条目
        ("ports", "port"),  # 端口条目及诊断标签
        ("decls", "signal"),  # 内部声明条目及诊断标签
        ("assigns", "assign"),  # 连线语句条目
    )

    # 遍历所有声明/赋值集合。
    for str_collection_name, str_label in tuple_collections:

        # 当前集合中每个条目都应有语义注释。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # 当前 AST 条目的注释诊断交给单项 helper，降低主循环嵌套。
            list_item_issues = _structured_comment_item_issues(  # 单个 AST 条目的注释质量诊断
                dict_item,  # 当前待检查的 AST 条目
                str_rel_path,  # 诊断报告中的相对文件路径
                str_severity,  # strict 模式决定的覆盖类严重级别
                comment_language,  # 当前注释语言策略
                str_label,  # 当前 AST 集合对应的诊断标签
            )

            # 单项诊断保持原始扫描顺序并入模块级列表。
            list_issues.extend(list_item_issues)

    # 返回结构化注释诊断。
    return list_issues

# _structured_comment_item_issues 检查单个 AST 条目的语义注释。
def _structured_comment_item_issues(
    dict_item: dict[str, Any],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
    str_label: str,
) -> list[QualityIssue]:
    """
    返回单个结构化条目的注释质量诊断。

    :param dict_item: formatter AST 中的参数、端口、声明或 assign 条目。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: strict 模式决定的覆盖类严重级别。
    :param comment_language: 注释语言策略。
    :param str_label: 诊断中展示的 AST 条目类别。
    :return: 当前条目的注释诊断列表。
    """

    # list_issues 保存当前条目产生的注释诊断。
    list_issues: list[QualityIssue] = []  # 单条 AST 注释诊断集合

    # str_name 优先使用 name，assign 使用 lhs。
    str_name = str(dict_item.get("name") or dict_item.get("lhs") or "")  # 被检查条目名称

    # str_comment 是 formatter AST 提取出的同 行注释。
    str_comment = str(dict_item.get("comment") or "").strip()  # 条目关联注释正文

    # int_line_no 把注释问题绑定回 formatter AST 的实体起始行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 注释诊断实体行号

    # structured_context 统一携带该条 AST 注释诊断所需字段。
    structured_context = StructuredCommentContext(  # 单条结构化注释诊断上下文
        str_name=str_name,  # 被检查结构条目名称
        str_label=str_label,  # 诊断文本使用的条目类别
        str_rel_path=str_rel_path,  # 报告中的文件路径
        int_line_no=int_line_no,  # 条目源码起始行
        str_severity=str_severity,  # strict 派生的严重级别
        comment_language=comment_language,  # 当前条目适用的中文优先策略
    )

    # 缺少注释时登记覆盖问题并结束后续语义检查。
    if not str_comment:

        # 缺注释时没有更多语义可检查。
        return [
            _missing_structured_comment_issue(structured_context)
        ]

    # fallback、空洞中文和语言策略按优先级检查。
    # 深度诊断覆盖 fallback、空洞中文和语言策略三类语义问题。
    list_issues.extend(_structured_comment_depth_issues(str_comment, structured_context))

    # 泛化占位注释单独登记 warning。
    # 泛化诊断单独保留 warning 语义，不和缺失注释合并。
    list_issues.extend(_generic_structured_comment_issues(str_comment, structured_context))

    # 返回当前条目的全部注释诊断。
    return list_issues

# _missing_structured_comment_issue 构造结构化条目缺注释诊断。
def _missing_structured_comment_issue(structured_context: StructuredCommentContext) -> QualityIssue:
    """
    把缺失 same-line 注释转换为 VG040 覆盖诊断。

    :param structured_context: 单个结构化条目的注释诊断上下文。
    :return: 缺注释诊断。
    """

    # VG040 覆盖诊断保留条目类别和源码实体行。
    return QualityIssue(
        "VG040",
        structured_context.str_severity,
        f"{structured_context.str_label} `{structured_context.str_name}` should have a same-line semantic comment.",
        structured_context.str_rel_path,
        structured_context.int_line_no,
        rule="comments.coverage",
    )

# _structured_comment_depth_issues 检查注释是否有真实 RTL 语义。
def _structured_comment_depth_issues(
    str_comment: str,
    structured_context: StructuredCommentContext,
) -> list[QualityIssue]:
    """
    检查结构化条目注释是否避免 fallback、空洞中文和纯英文兜底。

    :param str_comment: 条目关联注释正文。
    :param structured_context: 单个结构化条目的注释诊断上下文。
    :return: 注释语义深度诊断列表。
    """

    # formatter fallback 注释不能出现在最终交付代码中。
    if _is_fallback_comment(str_comment):

        # VG056 比旧 VG041 更明确地表达交付阻断原因。
        return [
            QualityIssue(
                "VG056",
                structured_context.str_severity,
                f"Comment for {structured_context.str_label} `{structured_context.str_name}` "
                "still uses formatter fallback text.",
                structured_context.str_rel_path,
                structured_context.int_line_no,
                rule="comments.no_fallback",
            )
        ]

    # 中文但空洞的注释不能满足实体级语义说明要求。
    if _is_hollow_chinese_comment(str_comment):

        # VG055 阻止“有中文字符但没有 RTL 意图”的注释放行。
        return [
            QualityIssue(
                "VG055",
                structured_context.str_severity,
                f"Comment for {structured_context.str_label} `{structured_context.str_name}` "
                "is hollow and must describe RTL intent.",
                structured_context.str_rel_path,
                structured_context.int_line_no,
                rule="comments.semantic_depth",
            )
        ]

    # 中文模式下要求注释包含具体中文语义。
    if structured_context.comment_language == "zh" and not _comment_has_meaningful_chinese(str_comment):

        # str_message 给纯英文注释问题生成稳定诊断文本。
        str_message = (  # 中文优先注释诊断文本
            f"Comment for {structured_context.str_label} `{structured_context.str_name}` should be "
            "Chinese-first and semantic, not only fallback text."
        )

        # 纯英文或兜底词注释只能作为 warning。
        return [
            QualityIssue(
                "VG041",
                "warning",
                str_message,
                structured_context.str_rel_path,
                structured_context.int_line_no,
                rule="comments.semantic",
            )
        ]

    # 当前注释满足实体级语义要求。
    return []

# _generic_structured_comment_issues 识别不能证明 RTL 意图的泛化注释。
def _generic_structured_comment_issues(
    str_comment: str,
    structured_context: StructuredCommentContext,
) -> list[QualityIssue]:
    """
    识别结构化条目注释是否仍是泛化说明。

    :param str_comment: 条目关联注释正文。
    :param structured_context: 单个结构化条目的注释诊断上下文。
    :return: 泛化注释诊断列表。
    """

    # 非泛化文本不需要额外登记 VG041。
    if not _is_generic_comment(str_comment):

        # 当前注释不属于泛化说明。
        return []

    # 泛化注释不能证明 RTL 意图。
    return [
        QualityIssue(
            "VG041",
            "warning",
            f"Comment for {structured_context.str_label} `{structured_context.str_name}` "
            f"looks generic: `{str_comment}`.",
            structured_context.str_rel_path,
            structured_context.int_line_no,
            rule="comments.semantic",
        )
    ]

# _block_comment_issues 检查过程块和 instance 之前的说明注释。
def _block_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查过程块和实例化是否有邻近说明注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: str_severity 文本值，供质量门规则匹配。
    :return: 块注释相关诊断列表。
    """

    # list_issues 保存块级注释诊断。
    list_issues: list[QualityIssue] = []  # 块级注释诊断

    # 逐类检查过程块上方的说明注释。
    for str_collection_name, str_label, str_rule in BLOCK_LEADING_COMMENT_COLLECTIONS:

        # 当前集合中每个块都必须有前导说明。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # str_block_name 优先展示 header，其次展示 span 标签。
            str_block_name = str(dict_block.get("header") or _span_item_label(dict_block))  # 过程块诊断展示名称

            # 缺少前导注释时登记覆盖问题。
            if not dict_block.get("leading_comments"):

                # 行为说明应紧贴过程块上方。
                list_issues.append(
                    QualityIssue(
                        "VG040",
                        str_severity,
                        f"{str_label} `{str_block_name}` should have a nearby leading comment explaining behavior.",
                        str_rel_path,
                        _as_line(dict_block.get("line_start")),
                        rule=str_rule,
                    )
                )

                # 没有说明注释时不再检查位置布局。
                continue

            # 已有前导注释时继续检查相邻行、空行和缩进。
            tuple_layout_args = (dict_block, list_lines, str_rel_path, str_severity, str_label, str_rule)  # 块布局参数

            # 当前过程块的前导注释布局诊断。
            list_layout_issues = _leading_comment_layout_issues(*tuple_layout_args)  # 过程块前导布局诊断

            # 合并当前过程块的布局诊断。
            list_issues.extend(list_layout_issues)

    # 子模块实例化前应有连接或功能说明。
    for dict_inst in dict_module.get("instances", []) or []:

        # 缺 leading comment 时登记实例注释问题。
        if not dict_inst.get("leading_comments"):

            # 实例说明有助于审查跨模块连接意图。
            list_issues.append(
                QualityIssue(
                    "VG040",
                    str_severity,
                    f"Instance `{dict_inst.get('instance_name')}` should have a leading function/connection comment.",
                    str_rel_path,
                    _as_line(dict_inst.get("line_start")),
                    rule="comments.instance",
                )
            )

            # 缺说明时不再检查位置布局。
            continue

        # 已有实例说明时检查其相邻行、空行和缩进。
        tuple_instance_layout_args = (  # 实例前导布局 helper 参数
            dict_inst,  # 当前实例 AST 条目
            list_lines,  # 前导注释所在源码行
            str_rel_path,  # 实例布局诊断路径
            str_severity,  # 实例布局问题严重级别
            "Instance",  # 实例布局诊断标签
            "comments.instance",  # 实例功能说明规则名
        )

        # 实例功能说明的前导布局诊断。
        list_instance_layout_issues = _leading_comment_layout_issues(*tuple_instance_layout_args)  # 实例前导布局诊断

        # 合并实例前导注释布局诊断。
        list_issues.extend(list_instance_layout_issues)

    # 返回块级注释诊断。
    return list_issues

# _leading_comment_layout_issues 检查前导注释相邻行、空行和缩进。
def _leading_comment_layout_issues(
    dict_item: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    str_label: str,
    str_rule: str,
) -> list[QualityIssue]:
    """
    检查块或实例前导注释是否紧贴目标结构并满足空行规则。

    :param dict_item: formatter AST 中的块或实例条目。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 注释布局问题的严重级别。
    :param str_label: 诊断中展示的结构类别。
    :param str_rule: 诊断规则命名空间。
    :return: 前导注释布局诊断列表。
    """

    # list_issues 保存当前结构的前导注释布局诊断。
    list_issues: list[QualityIssue] = []  # 前导注释布局诊断

    # int_line_no 是目标结构的起始行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 结构起始行号

    # 缺少行号时由 AST span 规则负责，本规则不伪造位置。
    if int_line_no is None or int_line_no <= 1 or int_line_no > len(list_lines):

        # 无法定位前一行时不追加布局诊断。
        return list_issues

    # str_target_line 是 always/initial/function/task/generate 或实例首行。
    str_target_line = list_lines[int_line_no - 1]  # 目标结构源码行

    # int_comment_line_no 是目标结构正上方一行。
    int_comment_line_no = int_line_no - 1  # 前导注释行号

    # str_comment_line 是目标结构正上方的源码行。
    str_comment_line = list_lines[int_comment_line_no - 1]  # 前导注释候选行

    # 前导说明必须恰好在结构上一行，不能隔空引用。
    if not _is_pure_line_comment(str_comment_line):

        # 已有 leading_comments 但源码上一行不是纯注释，说明位置不合规。
        list_issues.append(
            QualityIssue(
                "VG063",
                str_severity,
                f"{str_label} leading comment must be the pure comment line immediately above the block.",
                str_rel_path,
                int_line_no,
                rule=str_rule,
            )
        )

        # 无纯注释行时无法继续检查缩进和空行。
        return list_issues

    # 前导注释必须和目标结构的最左列对齐。
    if _line_indent(str_comment_line) != _line_indent(str_target_line):

        # 缩进不一致会破坏块归属的视觉锚点。
        list_issues.append(
            QualityIssue(
                "VG063",
                str_severity,
                f"{str_label} leading comment must align with the block start column.",
                str_rel_path,
                int_comment_line_no,
                rule=str_rule,
            )
        )

    # vertical_spacing_context 绑定 VG063 的空行布局诊断字段。
    vertical_spacing_context = CommentVerticalSpacingContext(  # VG063 空行布局上下文
        str_rel_path,  # 前导注释布局诊断路径
        str_severity,  # 前导注释布局严重级别
        "VG063",  # 块前导注释空行规则码
        str_label,  # 块前导注释诊断标签
        str_rule,  # 块前导注释规则路径
    )

    # 前导注释上方必须满足唯一空行或紧邻区域横幅规则。
    list_issues.extend(_comment_vertical_spacing_issues(list_lines, int_comment_line_no, vertical_spacing_context))

    # 返回当前结构的前导注释布局诊断。
    return list_issues

# _procedural_assignment_comment_issues 检查过程赋值同线注释。
def _procedural_assignment_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查 always/function/task/generate/initial 内赋值语句的同线注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: 过程赋值注释诊断列表。
    """

    # list_issues 跨所有过程块累计右侧注释缺失或失效的 VG062 结果。
    list_issues: list[QualityIssue] = []  # 跨过程块赋值注释问题集合

    # 逐类过程块扫描内部赋值语句。
    for str_collection_name in PROCEDURAL_ASSIGNMENT_COLLECTIONS:

        # 当前集合中每个块按源码 span 扫描。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # tuple_block_args 固定单块过程赋值 helper 的参数顺序。
            tuple_block_args = (dict_block, list_lines, str_rel_path, str_severity, comment_language)  # 单块赋值参数

            # 当前块的过程赋值检查拆给单块 helper。
            list_issues.extend(_procedural_assignment_issues_for_block(*tuple_block_args))

    # 返回过程赋值注释诊断。
    return list_issues

# _procedural_assignment_issues_for_block 检查单个过程块内的赋值注释。
def _procedural_assignment_issues_for_block(
    dict_block: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查单个过程块 span 内的赋值语句同线注释。

    :param dict_block: formatter AST 中的单个过程块。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: 当前过程块的赋值注释诊断。
    """

    # list_issues 保存当前块内的赋值注释诊断。
    list_issues: list[QualityIssue] = []  # 当前过程块赋值注释诊断

    # int_line_start 和 int_line_end 是当前块的源码范围。
    int_line_start = _as_line(dict_block.get("line_start"))  # 过程块起始行

    # int_line_end 是当前块扫描的结束边界。
    int_line_end = _as_line(dict_block.get("line_end"))  # 过程块结束行

    # 缺少 span 时无法精确扫描。
    if int_line_start is None or int_line_end is None:

        # AST span 可信度由 VG050 负责。
        return list_issues

    # 约束结束行不越过文件实际行数。
    int_last_line = min(int_line_end, len(list_lines))  # 实际可扫描结束行

    # same_line_comment_check_context_process 绑定过程赋值专用的规则码和实体标签。
    same_line_comment_check_context_process: SameLineCommentCheckContext = _procedural_assignment_comment_context(  # VG062 过程赋值检查上下文
        str_rel_path,  # 过程赋值诊断路径
        str_severity,  # 过程赋值缺注释严重级别
        comment_language,  # 过程赋值注释语言策略
    )

    # 逐行扫描当前过程块。
    for int_line_no in range(int_line_start, int_last_line + 1):

        # str_line 是当前源码行。
        str_line = list_lines[int_line_no - 1]  # 当前过程块源码行

        # 非过程赋值行不需要同线注释。
        if not _is_procedural_assignment_line(str_line):

            # 继续扫描后续行。
            continue

        # 当前赋值语句的注释诊断交给统一 helper。
        list_issues.extend(
            _same_line_assignment_comment_issues(
                str_line,
                int_line_no,
                same_line_comment_check_context_process,
            )
        )

    # 返回当前过程块的赋值注释诊断。
    return list_issues

# _instance_mapping_comment_issues 检查实例参数和端口关联的同线注释。
def _instance_mapping_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> list[QualityIssue]:
    """
    检查实例化参数和端口连线是否带同线语义注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: 实例关联注释诊断列表。
    """

    # list_issues 收集实例参数和端口映射的同线注释问题。
    list_issues: list[QualityIssue] = []  # 实例映射注释覆盖问题集合

    # same_line_comment_check_context_instance 绑定实例映射专用的规则码和实体标签。
    same_line_comment_check_context_instance: SameLineCommentCheckContext = _instance_mapping_comment_context(  # VG064 实例映射检查上下文
        str_rel_path,  # 实例映射诊断路径
        str_severity,  # 实例映射缺注释严重级别
        comment_language,  # 实例映射注释语言策略
    )

    # 遍历 module 内的每个实例化。
    for dict_inst in dict_module.get("instances", []) or []:

        # 当前实例的源码范围。
        int_line_start = _as_line(dict_inst.get("line_start"))  # 实例起始行

        # int_line_end 是当前实例连接列表结束边界。
        int_line_end = _as_line(dict_inst.get("line_end"))  # 实例结束行

        # 缺少 span 时不做文本扫描。
        if int_line_start is None or int_line_end is None:

            # VG050 会单独报告实例 span 缺失。
            continue

        # int_last_line 防止异常 span 越界。
        int_last_line = min(int_line_end, len(list_lines))  # 实例可扫描结束行

        # 逐行扫描实例关联。
        for int_line_no in range(int_line_start, int_last_line + 1):

            # str_line 是当前实例源码行。
            str_line = list_lines[int_line_no - 1]  # 当前实例行

            # 非 .formal(actual) 关联行不需要 VG064。
            if not _is_instance_association_line(str_line):

                # 继续扫描下一个实例行。
                continue

            # 实例关联同线注释必须存在且具备语义。
            list_issues.extend(
                _same_line_assignment_comment_issues(
                    str_line,
                    int_line_no,
                    same_line_comment_check_context_instance,
                )
            )

    # 返回实例关联注释诊断。
    return list_issues

# _definition_group_comment_issues 检查参数和信号区域的分组注释。
def _definition_group_comment_issues(
    dict_module: dict[str, Any],
    list_lines: list[str],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查参数和信号定义区域是否有分组说明注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :return: 参数和信号分组注释诊断列表。
    """

    # list_issues 保存分组注释诊断。
    list_issues: list[QualityIssue] = []  # 参数和信号分组注释诊断

    # list_definition_items 汇总需要检查分组说明的 module body 定义。
    list_definition_items = _definition_group_items(dict_module)  # 参数和信号定义条目

    # 逐个定义检查其所在区域和上方分组注释。
    for dict_item, str_label in list_definition_items:

        # tuple_item_args 保持 VG065 单条定义检查参数顺序。
        tuple_item_args = (
            dict_item,  # 待检查的参数或信号定义
            str_label,  # 定义类别诊断标签
            list_lines,  # 分组注释所在源码行
            dict_region_by_line,  # 区域横幅定位索引
            str_rel_path,  # VG065 诊断路径
            str_severity,  # VG065 严重级别
        )

        # 单个定义的分组注释检查拆给 helper。
        list_issues.extend(_definition_group_comment_issues_for_item(*tuple_item_args))

    # 返回参数和信号分组注释诊断。
    return list_issues

# _definition_group_items 收集参数和信号定义分组候选。
def _definition_group_items(dict_module: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """
    返回需要检查分组注释的参数和信号定义条目。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 按源码行排序的 `(item, label)` 条目列表。
    """

    # list_definition_items 承载参数和信号定义的统一扫描队列。
    list_definition_items: list[tuple[dict[str, Any], str]] = []  # 分组规则候选定义条目

    # localparam 属于参数定义区域。
    for dict_param in dict_module.get("localparams", []) or []:

        # 收集局部参数定义。
        list_definition_items.append((dict_param, "parameter"))

    # decls 属于信号定义区域。
    for dict_decl in dict_module.get("decls", []) or []:

        # 收集内部信号定义。
        list_definition_items.append((dict_decl, "signal"))

    # 按源码行号排序，确保连续定义分组判断稳定。
    list_definition_items.sort(key=lambda item: _as_line(item[0].get("line_start")) or 0)

    # 返回可供 VG065 检查的定义条目。
    return list_definition_items

# _definition_group_comment_issues_for_item 检查单条定义上方的分组注释。
def _definition_group_comment_issues_for_item(
    dict_item: dict[str, Any],
    str_label: str,
    list_lines: list[str],
    dict_region_by_line: dict[int, str],
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查单条参数或信号定义是否由分组注释引入。

    :param dict_item: formatter AST 中的参数或声明条目。
    :param str_label: 诊断中展示的定义类别。
    :param list_lines: 当前 Verilog 源码行列表。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :return: 单条定义的分组注释诊断。
    """

    # list_issues 保存当前定义的分组注释诊断。
    list_issues: list[QualityIssue] = []  # 当前定义分组注释诊断

    # tuple_context 保存定义行号和所在区域。
    tuple_context = _definition_group_item_context(dict_item, list_lines, dict_region_by_line)  # 定义分组上下文

    # 无需检查当前条目时直接返回。
    if tuple_context is None:

        # module header 或同组后续定义不需要本规则重复报告。
        return list_issues

    # 解包当前定义所在行和区域标题。
    int_line_no, str_region_title = tuple_context  # 定义分组检查定位信息

    # str_previous_line 是当前定义正上方一行。
    str_previous_line = list_lines[int_line_no - 2]  # 定义上方一行

    # 区域横幅只是导航锚点，不能替代分组注释。
    if _is_region_banner_line(str_previous_line):

        # 横幅后直接跟定义时说明缺少真正分组说明。
        quality_issue_missing_group_comment = _missing_definition_group_comment_issue(  # 横幅直连定义的 VG065 诊断
            dict_item,  # 横幅下方首个参数或信号 AST 条目
            str_label,  # 横幅直连场景的定义类别
            str_region_title,  # 当前定义所在的中文区域标题
            str_rel_path,  # 横幅直连缺分组说明的报告路径
            str_severity,  # strict 模式派生的横幅直连级别
            int_line_no,  # 横幅后首条定义的源码行号
        )

        # 返回当前定义的缺失分组注释诊断。
        return [
            quality_issue_missing_group_comment,
        ]

    # 分组注释必须紧贴定义上方。
    if not _is_pure_line_comment(str_previous_line):

        # 缺少分组说明会让参数/信号区域不可扫描。
        quality_issue_missing_group_comment = _missing_definition_group_comment_issue(  # 非注释前导行的 VG065 诊断
            dict_item,  # 前导行不是纯注释的定义 AST 条目
            str_label,  # 非注释前导场景的定义类别
            str_region_title,  # 需要分组说明的区域标题
            str_rel_path,  # 非注释前导缺分组说明的报告路径
            str_severity,  # strict 模式派生的非注释前导级别
            int_line_no,  # 缺少贴邻分组注释的定义行号
        )

        # 记录当前定义缺少分组说明。
        list_issues.append(quality_issue_missing_group_comment)

        # 没有分组注释时不继续检查布局。
        return list_issues

    # 分组注释必须和定义最左列对齐。
    if _line_indent(str_previous_line) != _line_indent(list_lines[int_line_no - 1]):

        # 缩进错位说明分组注释没有绑定当前定义组。
        list_issues.append(
            QualityIssue(
                "VG065",
                str_severity,
                f"{str_label} group comment must align with the definition start column.",
                str_rel_path,
                int_line_no - 1,
                rule="comments.definition_group",
            )
        )

    # vertical_spacing_context 绑定 VG065 的分组注释空行布局字段。
    vertical_spacing_context = CommentVerticalSpacingContext(  # 定义分组空行布局上下文
        str_rel_path,  # 分组注释布局诊断路径
        str_severity,  # 分组注释布局严重级别
        "VG065",  # 定义分组注释空行规则码
        f"{str_label} group",  # 定义分组注释诊断标签
        "comments.definition_group",  # 定义分组注释规则路径
    )

    # 分组注释上方同样遵循唯一空行或紧邻区域横幅规则。
    list_issues.extend(_comment_vertical_spacing_issues(list_lines, int_line_no - 1, vertical_spacing_context))

    # 返回当前定义的 VG065 布局诊断。
    return list_issues

# _definition_group_item_context 返回定义分组检查的定位上下文。
def _definition_group_item_context(
    dict_item: dict[str, Any],
    list_lines: list[str],
    dict_region_by_line: dict[int, str],
) -> tuple[int, str] | None:
    """
    返回单条定义需要检查分组注释时的行号和区域。

    :param dict_item: formatter AST 中的参数或声明条目。
    :param list_lines: 当前 Verilog 源码行列表。
    :param dict_region_by_line: 区域横幅行号到标题的映射。
    :return: 需要检查时返回 `(line_no, region_title)`，否则返回 None。
    """

    # int_line_no 是定义所在行。
    int_line_no = _as_line(dict_item.get("line_start"))  # 定义行号

    # 无法定位或越界时跳过。
    if int_line_no is None or int_line_no <= 1 or int_line_no > len(list_lines):

        # 无 span 的定义由 VG050 统一覆盖。
        return None

    # str_region_title 是定义所在的区域标题。
    str_region_title = _nearest_region_title(dict_region_by_line, int_line_no)  # 定义所在区域

    # module header 参数和端口由结构化注释规则负责。
    if str_region_title not in PARAM_SIGNAL_GROUP_REGIONS:

        # 当前条目不属于本规则负责的定义区域。
        return None

    # 连续定义行共享同一个上方分组注释，非首行不重复要求。
    if _previous_line_is_definition(list_lines, int_line_no):

        # 当前定义属于同一组的后续定义。
        return None

    # 返回分组注释检查所需上下文。
    return int_line_no, str_region_title

# _missing_definition_group_comment_issue 构造定义分组缺失诊断。
def _missing_definition_group_comment_issue(
    dict_item: dict[str, Any],
    str_label: str,
    str_region_title: str,
    str_rel_path: str,
    str_severity: str,
    int_line_no: int,
) -> QualityIssue:
    """
    构造参数或信号定义缺少分组注释的诊断。

    :param dict_item: formatter AST 中的参数或声明条目。
    :param str_label: 诊断中展示的定义类别。
    :param str_region_title: 当前定义所在区域标题。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param int_line_no: 定义源码行号。
    :return: 缺少分组注释的 VG065 诊断。
    """

    # str_name 用于把诊断绑定到具体参数或信号。
    str_name = str(dict_item.get("name") or "")  # 缺少分组注释的定义名

    # 返回统一的分组注释缺失诊断。
    return QualityIssue(
        "VG065",
        str_severity,
        f"{str_label} `{str_name}` must be introduced by a group comment in `{str_region_title}`.",
        str_rel_path,
        int_line_no,
        rule="comments.definition_group",
    )

# _procedural_assignment_comment_context 构造过程赋值同线注释上下文。
def _procedural_assignment_comment_context(
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> SameLineCommentCheckContext:
    """
    构造过程赋值同线注释检查上下文。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: VG062 同线注释检查上下文。
    """

    # tuple_context_args 按上下文字段顺序组装过程赋值检查配置。
    tuple_context_args = (
        str_rel_path,  # 过程赋值问题所在文件
        str_severity,  # 过程赋值缺尾注门禁等级
        comment_language,  # 过程赋值语义注释语言
        "VG062",  # 过程赋值尾注规则码
        "process assignment",  # 过程赋值报告实体名
        "comments.procedural_assignment",  # 过程赋值规则路径
    )  # 过程赋值检查上下文构造参数

    # 返回过程赋值专用规则码和标签。
    return SameLineCommentCheckContext(*tuple_context_args)

# _instance_mapping_comment_context 构造实例关联同线注释上下文。
def _instance_mapping_comment_context(
    str_rel_path: str,
    str_severity: str,
    comment_language: str,
) -> SameLineCommentCheckContext:
    """
    构造实例参数和端口关联同线注释检查上下文。

    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 缺注释问题的严重级别。
    :param comment_language: 注释语言策略。
    :return: VG064 同线注释检查上下文。
    """

    # tuple_context_args 按上下文字段顺序组装实例映射检查配置。
    tuple_context_args = (
        str_rel_path,  # 实例连线问题所在文件
        str_severity,  # 实例连线缺尾注门禁等级
        comment_language,  # 实例连线语义注释语言
        "VG064",  # 实例连线尾注规则码
        "instance mapping",  # 实例连线报告实体名
        "comments.instance_mapping",  # 实例连线规则路径
    )  # 实例映射检查上下文构造参数

    # 返回实例关联专用规则码和标签。
    return SameLineCommentCheckContext(*tuple_context_args)

# _same_line_assignment_comment_issues 检查赋值或关联行的同线注释质量。
def _same_line_assignment_comment_issues(
    str_line: str,
    int_line_no: int,
    same_line_comment_check_context: SameLineCommentCheckContext,
) -> list[QualityIssue]:
    """
    检查单行赋值或实例关联是否带有同线语义注释。

    :param str_line: 当前源码行。
    :param int_line_no: 当前源码行号。
    :param same_line_comment_check_context: 同线注释检查上下文。
    :return: 当前行同线注释诊断。
    """

    # str_name 是当前赋值或关联的展示名称。
    str_name = _assignment_lhs_label(str_line)  # 赋值左值或实例 formal 名称

    # str_comment 是当前行真实 // 注释正文。
    str_comment = _line_comment(str_line)  # 当前行同线注释正文

    # tuple_structured_context_args 把同线注释映射到结构化深度检查字段。
    tuple_structured_context_args = (
        str_name,  # 语义深度检查实体名
        same_line_comment_check_context.str_label,  # 语义深度检查实体类别
        same_line_comment_check_context.str_rel_path,  # 语义深度检查文件路径
        int_line_no,  # 语义深度检查源码行
        same_line_comment_check_context.str_severity,  # 空洞注释问题等级
        same_line_comment_check_context.comment_language,  # 空洞注释语言策略
    )  # 同线注释深度检查构造参数

    # structured_context 承接已有语义深度和泛化注释检查。
    structured_context = StructuredCommentContext(*tuple_structured_context_args)  # 同线注释语义上下文

    # 缺少同线注释时使用新增规则码。
    if not str_comment:

        # str_message 描述当前赋值或实例关联缺少同线注释。
        str_message = (
            f"{same_line_comment_check_context.str_label} `{str_name}` "
            "should have a same-line semantic comment."
        )  # 同线语义注释缺失诊断文本

        # 当前行不满足用户要求的同线右侧注释。
        quality_issue_missing_same_line_comment = QualityIssue(  # 赋值或实例映射缺少右侧说明
            same_line_comment_check_context.str_code,  # VG062 或 VG064 规则码
            same_line_comment_check_context.str_severity,  # 同线注释缺失严重级别
            str_message,  # 带实体名的缺注释诊断文本
            same_line_comment_check_context.str_rel_path,  # 当前 RTL 文件报告路径
            int_line_no,  # 缺少右侧说明的源码行号
            rule=same_line_comment_check_context.str_rule,  # 同线注释规则命名空间
        )

        # 返回当前行的缺失注释诊断。
        return [
            quality_issue_missing_same_line_comment,
        ]

    # 已有注释时继续复用语义深度检查。
    list_issues = _structured_comment_depth_issues(str_comment, structured_context)  # 当前同线注释深度诊断

    # 泛化占位注释同样不能满足新增同线规则。
    list_issues.extend(_generic_structured_comment_issues(str_comment, structured_context))

    # 返回当前行同线注释诊断。
    return list_issues

# _comment_vertical_spacing_issues 检查纯注释上方空行布局。
def _comment_vertical_spacing_issues(
    list_lines: list[str],  # 当前文件源码行
    int_comment_line_no: int,  # 被检查的纯注释行号
    vertical_spacing_context: CommentVerticalSpacingContext,  # 空行布局诊断上下文
) -> list[QualityIssue]:
    """
    检查前导或分组注释上方是否满足唯一空行规则。

    :param list_lines: 当前 Verilog 源码行列表。
    :param int_comment_line_no: 纯注释所在的一基行号。
    :param vertical_spacing_context: 空行布局诊断上下文。
    :return: 空行布局诊断列表。
    """

    # 第一行注释没有上方上下文，保持豁免。
    if int_comment_line_no <= 1:

        # 文件顶部注释由文件头规则处理。
        return []

    # int_anchor_line_no 回溯连续注释栈的首行，让区域横幅或空行规则绑定到整组注释。
    int_anchor_line_no = int_comment_line_no  # 连续纯注释栈的首行候选

    # 当前注释若只是多行说明中的后续行，应复用首条注释的空行上下文。
    while int_anchor_line_no > 1:

        # str_previous_comment_line 是当前注释栈上一行。
        str_previous_comment_line = list_lines[int_anchor_line_no - 2]  # 注释栈上一行源码

        # 只有连续纯注释才属于同一说明栈；区域横幅仍视作外层上下文。
        if not _is_pure_line_comment(str_previous_comment_line) or _is_region_banner_line(str_previous_comment_line):

            # 命中非纯注释或区域横幅时，当前栈首已确定。
            break

        # 继续向上回溯，直到到达连续注释栈首行。
        int_anchor_line_no -= 1  # 注释栈首行继续上移一行

    # str_previous_line 是注释栈首行上方一行。
    str_previous_line = list_lines[int_anchor_line_no - 2]  # 注释栈首行上一行

    # 紧邻区域横幅时不允许额外空行。
    if _is_region_banner_line(str_previous_line):

        # 当前注释直接绑定区域横幅后的首个结构。
        return []

    # 非区域上下文下，注释上方必须恰好一个空行。
    if str_previous_line.strip():

        # str_message 说明注释上方缺少唯一空行。
        str_message = (
            f"{vertical_spacing_context.str_label} comment must have exactly one blank line "
            "above unless it follows a region banner."
        )  # 缺少空行诊断文本

        # 上方不是空行也不是区域横幅，说明缺少唯一空行。
        return [_vertical_spacing_issue(vertical_spacing_context, int_anchor_line_no, str_message)]

    # 注释上方已有一个空行时，不能再多一个空行。
    if int_anchor_line_no > 2 and not list_lines[int_anchor_line_no - 3].strip():

        # str_message 说明注释上方存在多个连续空行。
        str_message = (
            f"{vertical_spacing_context.str_label} comment must have exactly one blank line "
            "above, not multiple blank lines."
        )  # 多余空行诊断文本

        # 连续空行违反“必须且只有 1 个空行”。
        return [_vertical_spacing_issue(vertical_spacing_context, int_anchor_line_no, str_message)]

    # 区域横幅之后如果插入空行再写注释，同样违反例外规则。
    if int_anchor_line_no > 2 and _is_region_banner_line(list_lines[int_anchor_line_no - 3]):

        # str_message 说明区域横幅和首个注释之间不能隔空。
        str_message = (
            f"{vertical_spacing_context.str_label} comment must directly follow the "
            "region banner without a blank line."
        )  # 横幅后空行诊断文本

        # 区域横幅和第一条前导/分组注释之间不能有空行。
        return [_vertical_spacing_issue(vertical_spacing_context, int_anchor_line_no, str_message)]

    # 空行布局满足规则。
    return []

# _vertical_spacing_issue 构造前导/分组注释空行布局诊断。
def _vertical_spacing_issue(
    vertical_spacing_context: CommentVerticalSpacingContext,
    int_comment_line_no: int,
    str_message: str,
) -> QualityIssue:
    """
    构造 VG063/VG065 空行布局诊断。

    :param vertical_spacing_context: 空行布局诊断上下文。
    :param int_comment_line_no: 当前纯注释的一基行号。
    :param str_message: 已生成的英文诊断文本。
    :return: 空行布局质量门诊断。
    """

    # 返回绑定上下文规则码和源码行的布局问题。
    return QualityIssue(
        vertical_spacing_context.str_code,
        vertical_spacing_context.str_severity,
        str_message,
        vertical_spacing_context.str_rel_path,
        int_comment_line_no,
        rule=vertical_spacing_context.str_rule,
    )

# _is_procedural_assignment_line 识别过程块内部的赋值语句。
def _is_procedural_assignment_line(str_line: str) -> bool:
    """
    判断源码行是否是 always/function/task/generate/initial 内的赋值语句。

    :param str_line: 当前源码行。
    :return: 是过程赋值语句时返回 True。
    """

    # str_code 去掉行尾注释后用于判断过程赋值语法形态。
    str_code = _strip_line_comment(str_line).strip()  # 过程赋值候选源码

    # 空行、控制结构和声明行不属于赋值语句。
    if not str_code or not str_code.endswith(";"):

        # 非完整语句不检查同线赋值注释。
        return False

    # 当前行属于忽略类别时跳过。
    if str_code.startswith(PROCEDURAL_ASSIGNMENT_IGNORED_PREFIXES):

        # 声明和连续赋值由其他规则检查。
        return False

    # bool_has_procedural_assignment 汇总过程块中两类赋值命中结果。
    bool_has_procedural_assignment = (
        _has_nonblocking_assignment(str_code)  # <= 非阻塞形式命中
        or _has_blocking_assignment(str_code)  # = 阻塞形式命中
    )  # 过程赋值语句判定结果

    # 非阻塞赋值或阻塞赋值均需要同线注释。
    return bool_has_procedural_assignment

# _is_instance_association_line 判断当前行是否是实例参数或端口关联。
def _is_instance_association_line(str_line: str) -> bool:
    """
    判断源码行是否是 `.formal(actual)` 形式的实例关联。

    :param str_line: 当前源码行。
    :return: 是实例关联行时返回 True。
    """

    # str_code 去掉行尾注释后用于识别 formal 连接。
    str_code = _strip_line_comment(str_line).strip()  # 去注释后的实例行

    # 实例参数和端口关联均以点号 formal 起始。
    return re.match(r"^\.[A-Za-z_][A-Za-z0-9_]*\s*\(", str_code) is not None

# _assignment_lhs_label 提取赋值左值或实例 formal 名称。
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

# _previous_line_is_definition 判断当前定义是否延续上一条定义组。
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

# _is_definition_statement_line 判断一行是否为参数或信号定义。
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

# _is_pure_line_comment 判断当前行是否是纯 // 注释。
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

# _comment_reuse_candidates_for_module 收集绑定具体 RTL 实体的注释。
def _comment_reuse_candidates_for_module(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集当前 module 中需要参与重复检测的实体注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 可参与 VG066 重复检测的注释候选列表。
    """

    # list_candidates 保持源码实体顺序，后续只在后出现的注释上报告。
    list_candidates: list[CommentReuseCandidate] = []  # module 内可比较的实体注释队列

    # 结构化参数、端口、信号和 assign 注释来自 formatter AST。
    list_candidates.extend(_structured_comment_reuse_candidates(dict_module, str_rel_path))

    # always、function、task、generate、initial 和 instance 前导注释绑定具体块。
    list_candidates.extend(_leading_comment_reuse_candidates(dict_module, str_rel_path))

    # 过程赋值右侧注释需要按源码 span 扫描。
    list_candidates.extend(_procedural_assignment_reuse_candidates(dict_module, list_lines, str_rel_path))

    # 实例化端口和参数映射注释同样属于实体说明。
    list_candidates.extend(_instance_mapping_reuse_candidates(dict_module, list_lines, str_rel_path))

    # 按行号排序，让重复诊断稳定落到后出现的注释。
    list_candidates.sort(key=lambda item: item.int_line_no or 0)

    # 返回全部实体级候选。
    return list_candidates

# _structured_comment_reuse_candidates 收集 AST 同线实体注释。
def _structured_comment_reuse_candidates(
    dict_module: dict[str, Any],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集参数、端口、声明和 assign 的同线注释候选。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 结构化实体注释候选列表。
    """

    # list_candidates 保存 formatter AST 已定位的实体注释。
    list_candidates: list[CommentReuseCandidate] = []  # 参数端口声明和 assign 候选队列

    # tuple_collections 与结构化注释覆盖规则保持一致。
    tuple_collections = (  # AST 实体集合与诊断标签映射
        ("params", "parameter"),  # module 头部参数
        ("localparams", "localparam"),  # module body 局部参数
        ("ports", "port"),  # module 头部端口
        ("decls", "signal"),  # module body 信号声明
        ("assigns", "assign"),  # 连续赋值语句
    )

    # 遍历每类 AST 实体。
    for str_collection_name, str_label in tuple_collections:

        # 当前集合中的条目按 formatter 顺序扫描。
        for dict_item in dict_module.get(str_collection_name, []) or []:

            # str_comment 是同线注释正文。
            str_comment = str(dict_item.get("comment") or "").strip()  # 当前实体注释正文

            # str_name 优先绑定实体名，assign 使用 lhs。
            str_name = str(dict_item.get("name") or dict_item.get("lhs") or "")  # 当前实体名称

            # int_line_no 定位到实体声明或 assign 行。
            int_line_no = _as_line(dict_item.get("line_start"))  # 当前实体源码行号

            # comment_reuse_candidate 由统一构造函数过滤短标签和空注释。
            comment_reuse_candidate: CommentReuseCandidate | None = _comment_reuse_candidate(  # 结构化实体 VG066 候选
                str_comment,  # AST 条目右侧注释正文
                str_label,  # 参数端口声明或 assign 类别
                str_name,  # 报告中展示的结构化实体名
                str_rel_path,  # 结构化实体所在 RTL 路径
                int_line_no,  # formatter AST 提供的实体起始行
            )

            # 空注释或过短信号标签不会进入 VG066。
            if comment_reuse_candidate is None:

                # 当前注释不具备重复检测价值。
                continue

            # 收集可检测的实体注释。
            list_candidates.append(comment_reuse_candidate)

    # 返回结构化注释候选。
    return list_candidates

# _leading_comment_reuse_candidates 收集块级前导语义注释。
def _leading_comment_reuse_candidates(
    dict_module: dict[str, Any],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集过程块和实例上方的实体说明注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 前导实体注释候选列表。
    """

    # list_candidates 保存 always/instance 等块级说明注释。
    list_candidates: list[CommentReuseCandidate] = []  # always 实例等前导说明队列

    # 过程块集合沿用注释覆盖规则表。
    for str_collection_name, str_label, _str_rule in BLOCK_LEADING_COMMENT_COLLECTIONS:

        # 每个过程块可能带一条或多条紧邻前导注释。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # str_name 展示过程块 header 或 span 标签。
            str_name = str(dict_block.get("header") or _span_item_label(dict_block))  # 过程块名称

            # 当前块的前导注释逐条收集。
            list_candidates.extend(
                _leading_comment_candidates_for_item(dict_block, str_label, str_name, str_rel_path)
            )

    # 实例级前导说明同样绑定具体 instance。
    for dict_inst in dict_module.get("instances", []) or []:

        # str_name 使用实例名定位注释对象。
        str_name = str(dict_inst.get("instance_name") or "")  # 实例名称

        # 收集实例功能或连接说明。
        list_candidates.extend(
            _leading_comment_candidates_for_item(dict_inst, "instance", str_name, str_rel_path)
        )

    # 返回块级实体注释候选。
    return list_candidates

# _leading_comment_candidates_for_item 把 AST 前导注释转换为候选。
def _leading_comment_candidates_for_item(
    dict_item: dict[str, Any],
    str_label: str,
    str_name: str,
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    转换单个 AST 条目的前导注释候选。

    :param dict_item: 带 leading_comments 的 formatter AST 条目。
    :param str_label: 注释绑定的实体类别。
    :param str_name: 注释绑定的实体名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 当前条目的前导注释候选列表。
    """

    # list_candidates 保存当前条目的前导注释候选。
    list_candidates: list[CommentReuseCandidate] = []  # 单个块或实例的前导说明队列

    # list_comments 是 formatter AST 记录的紧邻前导注释。
    list_comments = list(dict_item.get("leading_comments") or [])  # 前导注释原始文本列表

    # int_line_start 用于推断前导注释行号。
    int_line_start = _as_line(dict_item.get("line_start"))  # 目标实体起始行

    # 前导注释缺少目标行号时仍可参与文件级重复检测。
    int_first_comment_line = (int_line_start - len(list_comments)) if int_line_start is not None else None  # 第一条前导注释行号

    # 逐条转换前导注释。
    for int_index, str_raw_comment in enumerate(list_comments):

        # str_comment 去掉 // 后只保留语义正文。
        str_comment = _comment_body_from_raw(str(str_raw_comment))  # 前导注释正文

        # int_line_no 尽量定位到真实注释行。
        int_line_no = None if int_first_comment_line is None else int_first_comment_line + int_index  # 前导注释真实行号

        # 当前前导注释可能只是短导航，统一构造函数会过滤。
        comment_reuse_candidate: CommentReuseCandidate | None = _comment_reuse_candidate(  # 前导块说明 VG066 候选
            str_comment,  # 去掉注释符后的前导说明
            str_label,  # always/initial/instance 等块类别
            str_name,  # 前导注释绑定的块或实例名
            str_rel_path,  # 前导说明所在 RTL 路径
            int_line_no,  # 推算得到的前导注释行号
        )

        # 不具备检测价值的前导注释跳过。
        if comment_reuse_candidate is None:

            # 短标签或空文本不参与重复检测。
            continue

        # 收集当前前导注释。
        list_candidates.append(comment_reuse_candidate)

    # 返回当前条目的候选列表。
    return list_candidates

# _procedural_assignment_reuse_candidates 收集过程赋值同线注释。
def _procedural_assignment_reuse_candidates(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集 always/function/task 等过程赋值右侧注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 过程赋值注释候选列表。
    """

    # list_candidates 保存过程赋值注释候选。
    list_candidates: list[CommentReuseCandidate] = []  # 过程赋值右侧说明集合

    # 遍历所有过程块集合。
    for str_collection_name in PROCEDURAL_ASSIGNMENT_COLLECTIONS:

        # 当前集合中的块按 AST 顺序扫描。
        for dict_block in dict_module.get(str_collection_name, []) or []:

            # 当前块内候选由 span 扫描 helper 处理。
            list_candidates.extend(_comment_reuse_candidates_for_assignment_block(dict_block, list_lines, str_rel_path))

    # 返回过程赋值注释候选。
    return list_candidates

# _comment_reuse_candidates_for_assignment_block 扫描单个过程块。
def _comment_reuse_candidates_for_assignment_block(
    dict_block: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集单个过程块 span 内的赋值注释候选。

    :param dict_block: formatter AST 中的单个过程块。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 单个过程块内的注释候选列表。
    """

    # list_candidates 保存当前过程块中的赋值注释。
    list_candidates: list[CommentReuseCandidate] = []  # 当前过程块赋值说明队列

    # int_line_start 是过程块起始行。
    int_line_start = _as_line(dict_block.get("line_start"))  # 过程块起始行号

    # int_line_end 是过程块结束行。
    int_line_end = _as_line(dict_block.get("line_end"))  # 过程块结束行号

    # 缺少 span 时不进行文本扫描。
    if int_line_start is None or int_line_end is None:

        # VG050 会报告缺失 span，本规则跳过。
        return list_candidates

    # int_last_line 防止 AST span 越过文件尾。
    int_last_line = min(int_line_end, len(list_lines))  # 实际扫描结束行

    # 逐行扫描过程块内部赋值。
    for int_line_no in range(int_line_start, int_last_line + 1):

        # str_line 是当前过程块行。
        str_line = list_lines[int_line_no - 1]  # 当前源码行

        # 只收集真正的过程赋值语句。
        if not _is_procedural_assignment_line(str_line):

            # 非赋值语句继续扫描。
            continue

        # 当前赋值行同线注释转换为候选。
        comment_reuse_candidate: CommentReuseCandidate | None = _line_comment_reuse_candidate(  # 过程赋值右侧说明候选
            str_line,  # 当前过程块内的赋值源码行
            int_line_no,  # 过程赋值语句的一基行号
            "process assignment",  # VG066 报告中的过程赋值类别
            str_rel_path,  # 过程赋值所在 RTL 路径
        )

        # 没有有效注释时跳过。
        if comment_reuse_candidate is None:

            # 缺注释由 VG062 负责。
            continue

        # 收集过程赋值注释候选。
        list_candidates.append(comment_reuse_candidate)

    # 返回当前块候选。
    return list_candidates

# _instance_mapping_reuse_candidates 收集实例映射同线注释。
def _instance_mapping_reuse_candidates(
    dict_module: dict[str, Any],
    list_lines: list[str],
    str_rel_path: str,
) -> list[CommentReuseCandidate]:
    """
    收集实例参数和端口关联右侧注释。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param list_lines: 当前 Verilog 源码行列表。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 实例映射注释候选列表。
    """

    # list_candidates 保存实例映射注释候选。
    list_candidates: list[CommentReuseCandidate] = []  # 实例 formal 连接说明队列

    # 遍历所有子模块实例。
    for dict_inst in dict_module.get("instances", []) or []:

        # int_line_start 是实例起始行。
        int_line_start = _as_line(dict_inst.get("line_start"))  # 实例起始行号

        # int_line_end 是实例结束行。
        int_line_end = _as_line(dict_inst.get("line_end"))  # 实例结束行号

        # 无实例 span 时无法定位端口映射行。
        if int_line_start is None or int_line_end is None:

            # VG050 会覆盖缺失 span 情况。
            continue

        # int_last_line 防止越过源码行数。
        int_last_line = min(int_line_end, len(list_lines))  # 实例扫描结束行

        # 扫描实例连接列表。
        for int_line_no in range(int_line_start, int_last_line + 1):

            # str_line 是当前实例行。
            str_line = list_lines[int_line_no - 1]  # 当前实例源码行

            # 非 .formal(actual) 关联不属于本候选集合。
            if not _is_instance_association_line(str_line):

                # 继续扫描实例下一行。
                continue

            # 实例映射行同线注释转换为候选。
            comment_reuse_candidate: CommentReuseCandidate | None = _line_comment_reuse_candidate(  # 实例 formal 说明候选
                str_line,  # 当前实例参数或端口映射行
                int_line_no,  # 实例映射语句的一基行号
                "instance mapping",  # VG066 报告中的实例映射类别
                str_rel_path,  # 实例映射所在 RTL 路径
            )

            # 缺少同线注释时由 VG064 负责。
            if comment_reuse_candidate is None:

                # 当前行没有可复用注释文本。
                continue

            # 收集实例映射候选。
            list_candidates.append(comment_reuse_candidate)

    # 返回实例映射候选。
    return list_candidates

# _line_comment_reuse_candidate 把同线注释转换为重复检测候选。
def _line_comment_reuse_candidate(
    str_line: str,
    int_line_no: int,
    str_label: str,
    str_rel_path: str,
) -> CommentReuseCandidate | None:
    """
    从一行 RTL 中提取实体注释候选。

    :param str_line: 当前源码行。
    :param int_line_no: 当前源码行号。
    :param str_label: 注释绑定的实体类别。
    :param str_rel_path: 报告中使用的相对文件路径。
    :return: 可检测候选；没有有效注释时返回 None。
    """

    # str_comment 是当前行真实 // 后的正文。
    str_comment = _line_comment(str_line)  # 当前实体右侧说明正文

    # str_name 展示赋值左值或实例 formal 名。
    str_name = _assignment_lhs_label(str_line)  # 当前行绑定的实体名称

    # 构造候选并执行统一过滤。
    return _comment_reuse_candidate(str_comment, str_label, str_name, str_rel_path, int_line_no)

# _comment_reuse_candidate 构造单条候选并过滤短注释。
def _comment_reuse_candidate(
    str_comment: str,
    str_label: str,
    str_name: str,
    str_rel_path: str,
    int_line_no: int | None,
) -> CommentReuseCandidate | None:
    """
    构造可参与 VG066 的注释候选。

    :param str_comment: 注释正文。
    :param str_label: 注释绑定的实体类别。
    :param str_name: 注释绑定的实体名称。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param int_line_no: 注释行号。
    :return: 可检测候选；空注释或过短中文返回 None。
    """

    # str_body 去掉可能残留的 // 标记。
    str_body = _comment_body_from_raw(str_comment)  # 规范化前的注释正文

    # 空注释不能参与重复检测。
    if not str_body:

        # 覆盖类缺注释规则会单独报告。
        return None

    # int_cjk_chars 控制短标签不进入相似度比较。
    int_cjk_chars = len(CJK_PATTERN.findall(str_body))  # 注释中的中文字符数量

    # 中文字符太少时可能只是协议短标签或纯英文工具词。
    if int_cjk_chars < COMMENT_REUSE_MIN_CJK_CHARS:

        # 短注释交给语义深度规则判断。
        return None

    # str_normalized 是精确重复检测键。
    str_normalized = _normalized_comment_reuse_text(str_body)  # 去噪后的精确复用键

    # 规范化后太短的文本不稳定。
    if len(str_normalized) < COMMENT_REUSE_MIN_CJK_CHARS:

        # 过短键不参与重复检测。
        return None

    # str_similarity_key 进一步去掉标识符噪声。
    str_similarity_key = _comment_similarity_key(str_body)  # 近似复用比较键

    # 返回不可变候选对象。
    comment_reuse_candidate = CommentReuseCandidate(  # 完成归一化后的 VG066 候选对象
        str_comment=str_body,  # 展示给诊断的人读注释正文
        str_normalized=str_normalized,  # 精确重复检测使用的去噪键
        str_similarity_key=str_similarity_key,  # 近似复用检测使用的低噪声键
        str_label=str_label, str_name=str_name,  # 候选绑定的 RTL 实体类别和名称
        str_rel_path=str_rel_path, int_line_no=int_line_no,  # 候选所在路径和 VG066 落点行号
    )

    # 返回经过长度和语义噪声过滤的候选。
    return comment_reuse_candidate

# _comment_reuse_issues 检查精确重复和近似复用注释。
def _comment_reuse_issues(
    list_candidates: list[CommentReuseCandidate],
    str_severity: str,
) -> list[QualityIssue]:
    """
    把实体注释重复或近似复用转换为 VG066 诊断。

    :param list_candidates: 已按源码顺序排序的实体注释候选。
    :param str_severity: strict 派生的注释问题级别。
    :return: VG066 诊断列表。
    """

    # list_issues 保存后出现候选上的重复诊断。
    list_issues: list[QualityIssue] = []  # 后出现实体上的 VG066 诊断集合

    # dict_seen_by_key 记录每个规范化文本第一次出现的实体。
    dict_seen_by_key: dict[str, CommentReuseCandidate] = {}  # 规范化注释到首次实体的索引

    # set_reported_lines 避免同一注释同时报 exact 和 near duplicate。
    set_reported_lines: set[int | None] = set()  # 已经落点到 VG066 的源码行

    # 先做精确重复和数字噪声重复检测。
    for comment_reuse_candidate in list_candidates:

        # 第一次出现的规范化文本只登记不报错。
        if comment_reuse_candidate.str_normalized not in dict_seen_by_key:

            # 保存首个候选，后续复用指向它。
            dict_seen_by_key[comment_reuse_candidate.str_normalized] = comment_reuse_candidate  # 首次出现实体缓存

            # 继续检查下一个候选。
            continue

        # str_reuse_key 指向当前候选的精确重复键。
        str_reuse_key = comment_reuse_candidate.str_normalized  # 当前候选规范化文本

        # comment_reuse_candidate_previous 是相同注释键的首次出现位置。
        comment_reuse_candidate_previous = dict_seen_by_key[str_reuse_key]  # 精确重复首次实体候选

        # exact 重复登记到当前候选行。
        list_issues.append(
            _comment_reuse_issue(
                comment_reuse_candidate,
                comment_reuse_candidate_previous,
                str_severity,
            )
        )

        # 记录当前行，避免后续近似比较重复报告。
        set_reported_lines.add(comment_reuse_candidate.int_line_no)

    # 近似检测只在未被 exact 覆盖的后续候选上执行。
    for int_index, comment_reuse_candidate in enumerate(list_candidates):

        # 已报告 exact 的注释不重复报告 near。
        if comment_reuse_candidate.int_line_no in set_reported_lines:

            # 当前行已命中 VG066。
            continue

        # 与所有更早候选比较。
        for previous_comment_reuse_candidate in list_candidates[:int_index]:

            # 当前候选若近似复用更早注释则报告一次即可。
            if _comments_are_near_duplicate(comment_reuse_candidate, previous_comment_reuse_candidate):

                # 近似复用登记为 VG066。
                list_issues.append(
                    _comment_reuse_issue(
                        comment_reuse_candidate,
                        previous_comment_reuse_candidate,
                        str_severity,
                    )
                )

                # 当前行已报告，跳出更早候选循环。
                set_reported_lines.add(comment_reuse_candidate.int_line_no)

                # 当前候选已登记近似复用诊断，停止比较更早候选。
                break

    # 返回全部重复注释诊断。
    return list_issues

# _comments_are_near_duplicate 判断两条实体注释是否高度相似。
def _comments_are_near_duplicate(
    candidate: CommentReuseCandidate,
    previous_candidate: CommentReuseCandidate,
) -> bool:
    """
    判断两条注释是否属于近似复用。

    :param candidate: 后出现的候选注释。
    :param previous_candidate: 更早出现的候选注释。
    :return: 两条注释高度相似时返回 True。
    """

    # 两条注释的低噪声比较键都必须足够长。
    if len(candidate.str_similarity_key) < COMMENT_REUSE_MIN_CJK_CHARS:

        # 当前候选过短，不做近似判定。
        return False

    # 更早候选过短也跳过。
    if len(previous_candidate.str_similarity_key) < COMMENT_REUSE_MIN_CJK_CHARS:

        # 避免短标签误报。
        return False

    # 完全相同已由 exact 阶段报告。
    if candidate.str_normalized == previous_candidate.str_normalized:

        # exact 阶段负责该情况。
        return False

    # float_ratio 使用标准库序列相似度，避免引入第三方依赖。
    float_ratio = difflib.SequenceMatcher(  # 两条注释低噪声文本相似度
        None,  # 使用默认元素比较函数
        candidate.str_similarity_key,  # 后出现候选的低噪声文本
        previous_candidate.str_similarity_key,  # 更早候选的低噪声文本
    ).ratio()

    # 达到阈值时视作近似复用。
    return float_ratio >= COMMENT_REUSE_SIMILARITY_THRESHOLD

# _comment_reuse_issue 构造 VG066 诊断。
def _comment_reuse_issue(
    candidate: CommentReuseCandidate,
    previous_candidate: CommentReuseCandidate,
    str_severity: str,
) -> QualityIssue:
    """
    构造注释重复或近似复用诊断。

    :param candidate: 后出现并触发诊断的注释候选。
    :param previous_candidate: 被复用的更早候选。
    :param str_severity: 注释问题级别。
    :return: VG066 诊断。
    """

    # str_message 展示当前实体和首次出现实体，便于人工重写注释。
    str_message = (  # VG066 诊断文本
        f"Comment for {candidate.str_label} `{candidate.str_name}` repeats or closely reuses "
        f"comment from {previous_candidate.str_label} `{previous_candidate.str_name}`; "
        "write entity-specific RTL intent."
    )

    # 返回新增 VG066 诊断。
    return QualityIssue(
        "VG066",
        str_severity,
        str_message,
        candidate.str_rel_path,
        candidate.int_line_no,
        rule="comments.repeated_semantic",
    )

# _comment_body_from_raw 去掉注释标记和空白。
def _comment_body_from_raw(str_comment: str) -> str:
    """
    返回去掉 // 标记后的注释正文。

    :param str_comment: 原始注释文本。
    :return: 只保留语义正文的注释文本。
    """

    # str_body 先去除外围空白。
    str_body = str_comment.strip()  # 待剥离注释标记的原始正文

    # 前导注释可能保留了 // 标记。
    if str_body.startswith("//"):

        # 去掉 Verilog 单行注释符。
        str_body = str_body[2:].strip()  # 去注释符后的正文

    # 返回最终正文。
    return str_body

# _normalized_comment_reuse_text 生成重复检测键。
def _normalized_comment_reuse_text(str_comment: str) -> str:
    """
    生成忽略编号、空白和标点的注释重复检测键。

    :param str_comment: 原始注释正文。
    :return: 去噪后的重复检测文本。
    """

    # str_normalized 先执行 Unicode 兼容归一化。
    str_normalized = unicodedata.normalize("NFKC", str_comment)  # Unicode 归一化文本

    # 去掉零宽字符，防止不可见字符绕过重复检查。
    str_normalized = re.sub(r"[\u200b-\u200f\ufeff]", "", str_normalized)  # 去零宽字符文本

    # 数字编号不应让模板复用通过。
    str_normalized = re.sub(r"\d+", "", str_normalized)  # 去数字编号文本

    # 常见标点和空白都不参与重复比较。
    str_normalized = re.sub(r"[\s,，.。;；:：、/\\|()[\]{}<>《》\"'`~!！?？+=*_#-]+", "", str_normalized)  # 去标点空白文本

    # 小写化让 ASCII 标识符大小写差异不能绕过。
    return str_normalized.lower()

# _comment_similarity_key 生成近似比较键。
def _comment_similarity_key(str_comment: str) -> str:
    """
    生成近似重复检测使用的低噪声文本。

    :param str_comment: 原始注释正文。
    :return: 去掉 ASCII 标识符后的相似度比较文本。
    """

    # str_key 先复用精确重复归一化。
    str_key = _normalized_comment_reuse_text(str_comment)  # 归一化注释文本

    # ASCII 标识符通常是信号名噪声，不应让同模板句逃逸。
    str_key = re.sub(r"[a-z_][a-z0-9_]*", "", str_key)  # 去 ASCII 标识符文本

    # 返回近似比较键。
    return str_key

# _is_region_banner_line 判断当前纯注释是否是区域横幅。
def _is_region_banner_line(str_line: str) -> bool:
    """
    判断当前行是否是标准区域横幅注释。

    :param str_line: 当前源码行。
    :return: 是区域横幅时返回 True。
    """

    # 区域横幅必须形成左右 // 边界。
    return _region_banner_anchor_column(str_line) is not None

# _line_indent 返回行首缩进字符串。
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

# _comment_density_issues 检查全文件注释覆盖率。
def _comment_density_issues(str_text: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    按代码行比例检查注释覆盖密度。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 注释密度相关诊断列表。
    """

    # list_issues 保存注释密度诊断。
    list_issues: list[QualityIssue] = []  # 注释密度诊断

    # list_code_lines 只统计非空且非纯注释的 RTL 行。
    list_code_lines = [
        str_line  # 注释覆盖率分母中的 RTL 代码行
        for str_line in str_text.splitlines()  # 遍历文件全部文本行
        if _is_code_line(str_line)  # 排除空行和纯注释行
    ]  # 参与注释密度计算的 RTL 代码行

    # list_commented_code_lines 统计参与覆盖率分子的注释代码行。
    list_commented_code_lines = [
        str_line  # 覆盖率分子中带行注释的 RTL 代码行
        for str_line in list_code_lines  # 遍历已确认的 RTL 代码行
        if _line_comment(str_line)  # 只保留带真实行注释的代码行
    ]  # 注释密度分子使用的带行注释代码行

    # 没有代码行时不计算密度。
    if not list_code_lines:

        # 空文件或纯注释文件由其他规则处理。
        return list_issues

    # float_density 是带注释代码行占比。
    float_density = len(list_commented_code_lines) / len(list_code_lines)  # 代码行注释覆盖率

    # strict 模式下覆盖率低于 20% 为 error。
    if strict and float_density < 0.20:

        # 生成 RTL 需要足够语义注释支撑审查。
        list_issues.append(
            QualityIssue(
                "VG042",
                "error",
                f"Comment coverage is too low for generated RTL ({float_density:.2%}); "
                "add semantic comments near declarations, assigns, always blocks, FSM, and instances.",
                str_rel_path,
                rule="comments.coverage",
            )
        )

    # 非 strict 或轻微不足时保留 warning。
    elif float_density < 0.15:

        # 低注释覆盖率仍需提示维护风险。
        list_issues.append(
            QualityIssue(
                "VG042",
                "warning",
                f"Comment coverage is low ({float_density:.2%}).",
                str_rel_path,
                rule="comments.coverage",
            )
        )

    # 返回注释密度诊断。
    return list_issues

# _header_rules 检查双语文件头字段。
def _header_rules(str_text: str, str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查标准双语文件头和 formatter 兼容字段拼写。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 文件头规则诊断列表。
    """

    # list_issues 保存文件头诊断。
    list_issues: list[QualityIssue] = []  # 文件头规则诊断

    # str_severity 使用格式规则 severity。
    str_severity = _style_severity(strict)  # 文件头规则严重级别

    # str_pre_module 是 module 声明之前的文件头区域。
    str_pre_module = _pre_module_region(str_text)  # module 前文本区域

    # 双语分隔标记必须同时存在。
    if (
        HEADER_ENGLISH_SEPARATOR not in str_pre_module
        or HEADER_CHINESE_SEPARATOR not in str_pre_module
    ):

        # 双语头缺失时定位到第一行。
        list_issues.append(
            QualityIssue(
                "VG007",
                str_severity,
                "Standard bilingual header with English/Chinese sections is required.",
                str_rel_path,
                1,
                "header.bilingual",
            )
        )

    # list_missing_english 记录缺失的英文头字段。
    list_missing_english = [  # 双语文件头中缺失的英文模板字段
        str_field  # 未在英文文件头区域出现的字段名
        for str_field in REQUIRED_ENGLISH_HEADER_FIELDS  # 遍历英文必填字段
        if not re.search(rf"//\s*{re.escape(str_field)}\s*:", str_pre_module)  # 文件头未命中该字段
    ]

    # list_missing_chinese 记录中文文件头模板字段缺口。
    list_missing_chinese = [  # 中文文件头区域仍缺失的必填字段
        str_field  # 未在中文文件头区域出现的中文字段名
        for str_field in REQUIRED_CHINESE_HEADER_FIELDS  # 遍历中文必填字段
        if not re.search(rf"//\s*{re.escape(str_field)}\s*:", str_pre_module)  # 中文头未命中该字段
    ]

    # 英文字段缺失时聚合成一条诊断。
    if list_missing_english:

        # str_message 保持英文头字段诊断的旧文案前缀。
        str_message = (
            "English header is missing required field(s): "  # 英文头缺失字段诊断前缀
            + ", ".join(list_missing_english)  # 附加英文缺失字段名
        )  # 英文头缺失字段诊断文本

        # 追加英文头字段诊断。
        list_issues.append(
            QualityIssue("VG007", str_severity, str_message, str_rel_path, 1, "header.english_fields")
        )

    # 中文头字段缺失时聚合成一条诊断。
    if list_missing_chinese:

        # str_message 保持中文头字段诊断的旧文案前缀和字段列表。
        str_message = (
            "Chinese header is missing required field(s): "  # 中文头缺失字段诊断前缀
            + ", ".join(list_missing_chinese)  # 附加中文缺失字段名
        )  # 中文头缺失字段诊断文本

        # 追加中文头字段诊断。
        list_issues.append(
            QualityIssue("VG007", str_severity, str_message, str_rel_path, 1, "header.chinese_fields")
        )

    # References 拼写必须拒绝旧模板的多字母错误写法。
    str_legacy_references_field = "Refer" + "rences:"  # 拆分避免公开源码重新暴露旧拼写

    # 发现旧字段名时给出明确诊断。
    if str_legacy_references_field in str_pre_module:

        # 该拼写规则保护现有 formatter renderer 契约。
        list_issues.append(
            QualityIssue(
                "VG007",
                str_severity,
                "Header must use formatter-compatible `References` spelling.",
                str_rel_path,
                1,
                "header.references_spelling",
            )
        )

    # 当前版本和历史记录必须具备真实可追溯内容。
    list_issues.extend(_header_version_history_issues(str_pre_module, str_rel_path, str_severity))

    # 返回文件头诊断。
    return list_issues

# _header_version_history_issues 检查版本格式和历史记录正文。
def _header_version_history_issues(
    str_pre_module: str,
    str_rel_path: str,
    str_severity: str,
) -> list[QualityIssue]:
    """
    检查双语文件头中的版本号和修订历史记录。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param str_severity: 当前 header 规则严重级别。
    :return: 版本和历史记录诊断列表。
    """

    # list_issues 保存版本和历史记录诊断。
    list_issues: list[QualityIssue] = []  # 版本历史诊断集合

    # 英文 Version 字段沿用历史模板命名。
    tuple_english_version_field = ("Version", "header.version.english")  # 英文版本字段规则

    # 中文当前版本字段用于双语文件头一致性检查。
    tuple_chinese_version_field = ("当前版本", "header.version.chinese")  # 中文版本字段规则

    # 先检查英文版本字段，保持现有报告顺序。
    list_version_fields = [tuple_english_version_field]  # 版本字段格式检查表

    # 再检查中文当前版本字段。
    list_version_fields += [tuple_chinese_version_field]  # 补充中文当前版本字段

    # 逐个检查已存在的版本字段。
    for str_field, str_rule in list_version_fields:

        # str_version_value 保留字段原始值，避免 _extract_header_field 只取标识符。
        str_version_value = _extract_header_field_value(str_pre_module, str_field)  # 当前版本字段值

        # 缺失字段由 VG007 必填字段检查负责。
        if not str_version_value:

            # 当前字段不存在，跳过格式检查。
            continue

        # 版本号必须是 Vx.y。
        if not HEADER_VERSION_PATTERN.fullmatch(str_version_value):

            # 非标准版本会破坏生成历史和人工追踪。
            list_issues.append(
                QualityIssue(
                    "VG007",
                    str_severity,
                    f"Header field `{str_field}` must use Vx.y version format, got `{str_version_value}`.",
                    str_rel_path,
                    1,
                    str_rule,
                )
            )

    # 修订历史区必须至少包含一条带日期和版本号的记录。
    if not _header_has_history_record(str_pre_module):

        # 只有表头没有记录时不能视为可追溯历史。
        list_issues.append(
            QualityIssue(
                "VG007",
                str_severity,
                "Header history must contain at least one dated record with a Vx.y version.",
                str_rel_path,
                1,
                "header.history_records",
            )
        )

    # 返回版本历史诊断。
    return list_issues

# _extract_header_field_value 返回 header 字段的完整值。
def _extract_header_field_value(str_pre_module: str, str_field: str) -> str:
    """
    从文件头中读取指定字段的完整值。

    :param str_pre_module: module 声明之前的源码文本。
    :param str_field: 需要提取的文件头字段名。
    :return: 字段值文本，未找到时返回空字符串。
    """

    # 版本字段行统一形如 // Field: value 或 // 字段: value。
    str_pattern = rf"(?m)^\s*//\s*{re.escape(str_field)}\s*:\s*(?P<value>.*?)\s*$"  # 指定头字段整行正则

    # 正则匹配结果保留 value 分组，避免只抽取版本标识符。
    obj_match = re.search(str_pattern, str_pre_module)  # 指定头字段匹配结果

    # 找不到目标字段时交给必填字段规则报告。
    if obj_match is None:

        # 缺字段由必填字段规则处理。
        return ""

    # 返回完整字段值，供版本格式精确检查。
    return obj_match.group("value").strip()

# _header_has_history_record 判断 header 是否包含真实修订历史。
def _header_has_history_record(str_pre_module: str) -> bool:
    """
    判断文件头修订历史区是否至少有一条真实记录。

    :param str_pre_module: module 声明之前的源码文本。
    :return: 存在日期和 Vx.y 版本号记录时返回 True。
    """

    # 逐行扫描注释正文，避开 History 表头。
    for str_line in str_pre_module.splitlines():

        # 非注释行不是 header 历史记录。
        if _line_comment_start(str_line) < 0:

            # 跳过空行或横幅线。
            continue

        # str_body 是去掉 // 后的 header 行正文。
        str_body = _line_comment(str_line)  # header 注释正文

        # 表头和字段名不算历史记录。
        if _is_history_heading_line(str_body):

            # 当前行只是 History 标题或表头。
            continue

        # 历史正文必须同时出现年份和 Vx.y 版本号。
        if re.search(r"\b\d{4}(?:/|年|-)\d{1,2}", str_body) and re.search(r"\bV\d+\.\d+\b", str_body):

            # 找到真实历史记录。
            return True

    # 未找到真实历史正文。
    return False

# _is_history_heading_line 判断注释正文是否只是历史区表头。
def _is_history_heading_line(str_body: str) -> bool:
    """
    判断一行 header 注释是否属于历史字段或表头。

    :param str_body: 去掉 // 后的注释正文。
    :return: 该行只是历史标题或表头时返回 True。
    """

    # str_normalized 去掉外侧空白用于表头判断。
    str_normalized = str_body.strip()  # 历史行表头判定文本

    # 空行和字段标题都不是历史记录。
    if not str_normalized:

        # 空注释行跳过。
        return True

    # 英文/中文历史字段标题。
    if str_normalized in {"History:", "修订历史:"}:

        # 字段标题不是记录。
        return True

    # 表格表头不算记录。
    return str_normalized.lower().startswith("time") or str_normalized.startswith("时间")

# _module_header_rules 检查 module header 的 ANSI 和分组注释。
def _module_header_rules(
    str_text: str,
    str_rel_path: str,
    dict_module: dict[str, Any],
    *,
    strict: bool,
) -> list[QualityIssue]:
    """
    检查 module header 是否使用 ANSI 端口和分组注释。

    :param str_text: 当前 Verilog 源码文本。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param dict_module: formatter AST 中的单个 module 描述。
    :param strict: 是否把样式和注释问题升级为 error。
    :return: 模块头部说明诊断列表。
    """

    # list_issues 保存 ANSI header、端口分组和 compact header 违规诊断。
    list_issues: list[QualityIssue] = []  # module header 合同诊断

    # str_severity 按 strict 控制 module header 风格违规级别。
    str_severity = _style_severity(strict)  # module header 规则严重级别

    # str_module_name 供 header 正则定位模块声明边界。
    str_module_name = str(dict_module.get("name") or "")  # header 正则匹配目标名

    # list_ports 是 ANSI header 规则需要检查的端口集合。
    list_ports = dict_module.get("ports", []) or []  # 当前 module 端口 AST 条目

    # 有端口但缺方向说明时不是 ANSI header。
    if list_ports and any(not str(dict_port.get("direction") or "") for dict_port in list_ports):

        # ANSI 端口声明要求方向写在 header。
        list_issues.append(
            QualityIssue(
                "VG008",
                str_severity,
                f"Module `{str_module_name}` must use ANSI-style port declarations "
                "with direction in the module header.",
                str_rel_path,
                rule="module.ansi_header",
            )
        )

    # str_header_text 截取当前 module header 区域。
    str_header_text = _module_header_region(str_text, str_module_name)  # 分组注释检查使用的 header 文本

    # 三个以上端口应有中文或协议分组注释。
    if len(list_ports) >= 3 and str_header_text and not _has_port_group_comment(str_header_text):

        # 分组注释帮助审查大型接口。
        list_issues.append(
            QualityIssue(
                "VG009",
                str_severity,
                f"Module `{str_module_name}` port list should use Chinese group comments "
                "such as 全局信号, 用户接口, or protocol 接口 groups.",
                str_rel_path,
                rule="ports.group_comments",
            )
        )

    # 旧式单行 module header 不适合生成交付 RTL。
    if re.search(r"^\s*module\s+\w+\s*\([^\n]*\);", str_text, re.MULTILINE) and list_ports:

        # compact header 会降低端口注释可读性。
        list_issues.append(
            QualityIssue(
                "VG008",
                str_severity,
                f"Module `{str_module_name}` should not use a compact legacy one-line header "
                "for generated delivery RTL.",
                str_rel_path,
                rule="module.ansi_header",
            )
        )

    # 返回 module header 诊断。
    return list_issues

# _pre_module_region 截取第一个 module 声明之前的文本。
def _pre_module_region(str_text: str) -> str:
    """
    返回首个 module 声明之前的文件头区域。

    :param str_text: 当前 Verilog 源码文本。
    :return: 模块声明前的源码区域文本。
    """

    # str_module_pattern 捕获文件中最早出现的 module 声明。
    str_module_pattern = r"(?m)^\s*module\s+[A-Za-z_][A-Za-z0-9_]*\b"  # 文件头截断锚点正则

    # obj_match 定位文件头和首个 module 主体的分界。
    obj_match = re.search(str_module_pattern, str_text)  # 首个 module 声明匹配对象

    # 找到 module 时返回前缀，否则返回全文。
    return str_text[: obj_match.start()] if obj_match else str_text

# _module_header_region 截取指定 module 的 ANSI header。
def _module_header_region(str_text: str, str_module_name: str) -> str:
    """
    返回指定 module 的 header 文本区域。

    :param str_text: 当前 Verilog 源码文本。
    :param str_module_name: str_module_name 文本值，供质量门规则匹配。
    :return: 模块头部区域文本。
    """

    # 空 module 名称无法构造安全正则。
    if not str_module_name:

        # 无 module 名称时返回空 header。
        return ""

    # str_header_pattern 跨行捕获目标 module 的完整端口头。
    str_header_pattern = rf"(?ms)^\s*module\s+{re.escape(str_module_name)}\b.*?^\s*\);"  # header 截取正则

    # obj_match 定位指定 module ANSI header 的文本范围。
    obj_match = re.search(str_header_pattern, str_text)  # 指定 module header 匹配对象

    # 返回匹配到的 header 文本。
    return obj_match.group(0) if obj_match else ""

# _has_port_group_comment 判断 module header 中是否有接口分组注释。
def _has_port_group_comment(str_header_text: str) -> bool:
    """
    判断端口 header 是否包含中文或协议接口分组注释。

    :param str_header_text: str_header_text 文本值，供质量门规则匹配。
    :return: 找到端口分组注释时返回 True。
    """

    # tuple_group_patterns 覆盖中文分组和常见协议接口分组。
    tuple_group_patterns = (  # module header 分组注释允许的模式
        re.compile(r"//[-\s]*全局信号[-\s]*//"),  # 全局信号分组横幅
        re.compile(r"//[-\s]*用户接口[-\s]*//"),  # 用户接口分组横幅
        PORT_GROUP_PROTOCOL_PATTERN,  # 协议接口分组
        PORT_GROUP_GENERIC_PATTERN,  # 通用接口分组说明
    )

    # 任一分组模式命中即可认为 header 有分组注释。
    return any(obj_pattern.search(str_header_text) for obj_pattern in tuple_group_patterns)

# _is_placeholder_comment 判断注释是否命中模板占位模式。
def _is_placeholder_comment(str_comment: str) -> bool:
    """
    判断注释正文是否像模板占位或示例残留。

    :param str_comment: 当前正在判断的注释正文。
    :return: 命中模板占位注释时返回 True。
    """

    # 任一占位模式命中即视为无效语义注释。
    return any(obj_pattern.search(str_comment) for obj_pattern in GENERIC_COMMENT_PATTERNS)

# _control_line_requires_begin 判断控制语句是否缺少 begin。
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

# _empty_metrics 初始化质量门聚合指标。
def _empty_metrics() -> dict[str, Any]:
    """
    返回质量门聚合指标的初始结构。

    参数:
        无外部业务参数。

    :return: 质量门 metrics 初始字典。
    """

    # dict_metrics 字段名保持质量门 JSON 消费方的历史契约。
    dict_metrics = {  # 单次质量门运行的聚合指标初值
        "files": 0,  # 成功生成 AST 报告的文件数
        "modules": 0,  # AST summary 中的 module 总数
        "lines": 0,  # 源码总行数
        "code_lines": 0,  # 非空非纯注释代码行数
        "line_comments": 0,  # 含双斜线的文本行数
        "commented_code_lines": 0,  # 带真实行注释的代码行数
        "block_comment_markers": 0,  # 块注释边界标记数
        "formatter_decisions": {},  # formatter 路由决策分布
        "encodings": {},  # 源文件编码分布
    }

    # 返回新字典，避免跨次运行共享状态。
    return dict_metrics

# _accumulate_metrics 将单文件指标累加到聚合字典。
def _accumulate_metrics(dict_metrics: dict[str, Any], str_text: str, dict_ast_report: dict[str, Any]) -> None:
    """
    把单个 Verilog 文件的行数、注释和 formatter 决策累计到 metrics。

    :param dict_metrics: 待累计更新的质量门 metrics 字典。
    :param str_text: 当前 Verilog 源码文本。
    :param dict_ast_report: 当前文件的 formatter AST 报告。
    :return: 无返回值，直接累计更新 metrics 字典。
    """

    # list_lines 保存单文件的原始行序列，供多项 metrics 复用。
    list_lines = str_text.splitlines()  # metrics 累加使用的源码行序列

    # list_code_lines 只统计 metrics 口径下的真实 RTL 代码行。
    list_code_lines = [  # 单文件 code_lines 指标的源码行集合
        str_line  # 将计入 code_lines 的 RTL 源码行
        for str_line in list_lines  # 遍历单文件源码行
        if _is_code_line(str_line)  # 只保留会影响 RTL 语义的代码行
    ]  # metrics.code_lines 使用的非空非纯注释行

    # int_line_comment_count 统计包含 // 的文本行，保持旧 metrics 口径。
    int_line_comment_count = sum(  # metrics.line_comments 本文件增量
        1  # 每个包含 // 的文本行计一次
        for str_line in list_lines  # 扫描原始文本行
        if "//" in str_line  # 按旧 metrics 口径统计所有双斜线行
    )

    # int_commented_code_line_count 统计既是代码又带行注释的行。
    int_commented_code_line_count = sum(  # 本文件带注释代码行增量
        1  # 每个带行注释的代码行计一次
        for str_line in list_code_lines  # 扫描已过滤的 RTL 代码行
        if _line_comment(str_line)  # 只统计代码行上的真实行注释
    )

    # int_block_marker_count 统计块注释开始和结束标记。
    int_block_marker_count = (
        str_text.count("/*") + str_text.count("*/")  # 开始和结束标记都计入 metrics
    )  # 本文件块注释边界增量

    # 累计基础行数和注释指标。
    dict_metrics["lines"] += len(list_lines)  # 累计源码总行数

    # 累计真实 RTL 代码行数。
    dict_metrics["code_lines"] += len(list_code_lines)  # 累计可执行 RTL 行数

    # 累计带双斜线的文本行数。
    dict_metrics["line_comments"] += int_line_comment_count  # 累计含行注释标记的行数

    # 累计带行注释的代码行数。
    dict_metrics["commented_code_lines"] += int_commented_code_line_count  # 累计带注释的 RTL 行数

    # 累计块注释边界标记数。
    dict_metrics["block_comment_markers"] += int_block_marker_count  # 累计块注释边界数

    # 仅在 AST 报告仍显式提供 formatter 路由决策时累计该统计。
    obj_score_payload = dict_ast_report.get("score")  # 兼容旧报告结构的 formatter 路由载荷

    # 旧报告会提供包含 decision 字段的字典；新报告缺失时不再伪造 unknown。
    if isinstance(obj_score_payload, dict):

        # 提取旧报告中的 formatter 路由决策名。
        str_decision = str(obj_score_payload.get("decision") or "").strip()  # 旧报告中的决策标签

        # 只有真实决策标签存在时才累计统计，避免把缺失字段误报成 unknown。
        if str_decision:

            # 初始化当前 formatter 决策计数。
            dict_metrics["formatter_decisions"].setdefault(str_decision, 0)

            # 当前文件对应决策计数加一。
            dict_metrics["formatter_decisions"][str_decision] += 1  # 当前 formatter 决策出现次数

# _rel_path 生成报告中使用的相对路径。
def _rel_path(path_source: Path, path_root: Path) -> str:
    """
    根据检查入口生成稳定的报告路径。

    :param path_source: 当前正在检查的 Verilog 源文件路径。
    :param path_root: 质量门检查入口根路径。
    :return: 用于报告展示的稳定路径文本。
    """

    # 单文件入口时直接展示文件名。
    if path_root.is_file():

        # 文件名保持旧行为。
        return path_source.name

    # 尝试生成相对目录路径。
    try:

        # 成功时使用 POSIX 风格，便于跨平台报告比较。
        return path_source.relative_to(path_root).as_posix()

    # 不在检查根目录下时使用完整路径兜底。
    except ValueError:

        # 不在 root 下时退回完整 POSIX 路径。
        return path_source.as_posix()

# _style_severity 将 strict 开关映射为格式规则级别。
def _style_severity(strict: bool) -> str:
    """
    返回格式和结构规则在当前 strict 模式下的严重级别。

    :param strict: 是否把样式和注释问题升级为 error。
    :return: 当前 strict 模式下的样式诊断级别。
    """

    # strict 模式下风格问题阻断质量门。
    return "error" if strict else "warning"

# _comment_severity 将 strict 开关映射为注释规则级别。
def _comment_severity(strict: bool) -> str:
    """
    返回注释覆盖规则在当前 strict 模式下的严重级别。

    :param strict: 是否把样式和注释问题升级为 error。
    :return: 当前 strict 模式下的注释诊断级别。
    """

    # strict 模式下缺失语义注释阻断质量门。
    return "error" if strict else "warning"

# _is_testbench 判断源文件是否属于测试平台。
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

# _is_vitis_port 判断端口名是否属于 Vitis wrapper 固定端口。
def _is_vitis_port(str_name: str) -> bool:
    """
    判断端口名是否命中 Vitis wrapper 固定命名模式。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 端口名命中 Vitis 固定模式时返回 True。
    """

    # 任一 Vitis 固定端口模式命中即可豁免 Erie 前缀规则。
    return any(obj_pattern.search(str_name) for obj_pattern in VITIS_PORT_PATTERNS)

# _expected_reg_name 判断 reg/logic 是否符合内部信号命名约定。
def _expected_reg_name(str_name: str) -> bool:
    """
    判断寄存器类信号名是否符合 Erie 前缀或输出桥接后缀。

    :param str_name: 当前正在判断的 Verilog 标识符名称。
    :return: 寄存器类信号名符合约定时返回 True。
    """

    # reg/cnt/state/flag/enc/dec 前缀或 _o 后缀均可接受。
    return str_name.startswith(("reg_", "cnt_", "state_", "flag_", "enc_", "dec_")) or str_name.endswith("_o")

# _looks_counter 捕获计数语义信号名。
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

# _looks_flag 捕获握手、完成和标志语义信号名。
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

# _flag_name_needs_prefix 判断 flag 语义信号是否缺少 flag_ 前缀。
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

# _looks_encoder 捕获编码语义信号名。
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

# _looks_decoder 捕获译码语义信号名。
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

# _line_comment 提取未被字符串字面量包裹的 // 注释。
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

# _line_comment_start 返回未被字符串字面量包裹的 // 起始下标。
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

        # 双引号切换字符串状态。
        if str_char == '"':

            # 更新字符串内外状态。
            bool_in_string = not bool_in_string  # 字符串扫描状态

            # 引号只改变扫描状态，不参与后续斜杠匹配。
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

# _strip_line_comment 去除当前行的真实 // 注释部分。
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

# _display_width_with_tabs 计算质量门使用的显示宽度。
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

# _is_code_line 判断一行是否包含 RTL 代码。
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

# _fsm_next_state_rules 检查 next-state 组合段的 default 和默认保持。
def _fsm_next_state_rules(dict_module: dict[str, Any], str_rel_path: str, *, strict: bool) -> list[QualityIssue]:
    """
    检查 FSM next-state 组合段是否包含 default 和默认保持。

    :param dict_module: formatter AST 中的单个 module 描述。
    :param str_rel_path: 报告中使用的相对文件路径。
    :param strict: 是否把样式和结构问题升级为 error。
    :return: FSM next-state 深语义诊断列表。
    """

    # list_issues 保存 next-state 默认覆盖相关的 VG054 诊断。
    list_issues: list[QualityIssue] = []  # next-state 组合段诊断集合

    # FSM next-state 组合块以 state_next 作为赋值目标。
    list_next_blocks = _next_state_always_blocks(dict_module)  # state_next 组合 always 块集合

    # 没有 next-state 块时由三段式规则报告。
    if not list_next_blocks:

        # 无需重复登记。
        return list_issues

    # 逐个 next-state 组合块检查 case/default 和默认保持。
    for dict_always in list_next_blocks:

        # str_block_text 用于跨行查找 case/default 和 state_next 赋值。
        str_block_text = _always_block_text(dict_always)  # next-state always 正文文本

        # int_line_no 定位 VG054 到 next-state always 起始行。
        int_line_no = _as_line(dict_always.get("line_start"))  # VG054 报告使用的 next-state always 起始行

        # case 型 next-state 必须有 default 分支。
        if _has_case_without_default(str_block_text):

            # 缺 default 会导致未覆盖状态锁存或不可预测跳转。
            list_issues.append(
                QualityIssue(
                    "VG054",
                    _style_severity(strict),
                    "FSM next-state case block must include a default branch.",
                    str_rel_path,
                    int_line_no,
                    rule="fsm.next_state_default",
                )
            )

        # next-state 组合段应先默认保持当前态，再按条件覆盖。
        if re.search(r"\bstate_next\s*=", str_block_text) and not re.search(
            r"\bstate_next\s*=\s*state_current\s*;", str_block_text
        ):

            # 默认保持是 Erie 三段式 FSM 的可读性和锁存防护要求。
            list_issues.append(
                QualityIssue(
                    "VG054",
                    _style_severity(strict),
                    "FSM next-state block must default `state_next = state_current;` before overrides.",
                    str_rel_path,
                    int_line_no,
                    rule="fsm.next_state_hold",
                )
            )

    # 返回 next-state 默认覆盖诊断。
    return list_issues

# _next_state_always_blocks 提取 state_next 组合 always 块。
def _next_state_always_blocks(dict_module: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回驱动 state_next 的组合 always 块。

    :param dict_module: formatter AST 中的单个 module 描述。
    :return: 驱动 state_next 的组合 always 条目列表。
    """

    # list_blocks 保留后续 VG054 需要检查的 next-state 组合块。
    list_blocks: list[dict[str, Any]] = []  # next-state 组合 always 候选集合

    # 逐个 always 条目筛选组合逻辑中的 state_next 目标。
    for dict_always in dict_module.get("always", []) or []:

        # 非组合 always 不属于三段式 next-state 逻辑。
        if not dict_always.get("is_combinational"):

            # 时序块和无类型块由旧 FSM 规则处理。
            continue

        # targets 表示 formatter 识别到的赋值左值集合。
        if "state_next" not in (dict_always.get("targets", []) or []):

            # 组合块未驱动 state_next 时跳过。
            continue

        # 收集 next-state 组合段。
        list_blocks.append(dict_always)

    # 返回筛选出的 next-state 组合块。
    return list_blocks

# _always_block_text 合并 always AST 行文本。
def _always_block_text(dict_always: dict[str, Any]) -> str:
    """
    返回 always 块的源码文本。

    :param dict_always: formatter AST 中的 always 条目。
    :return: 用换行合并后的 always 块文本。
    """

    # str_block_text 保留跨行 case/default 搜索所需的换行边界。
    str_block_text = "\n".join(str(item) for item in dict_always.get("lines", []) or [])  # always 原始行拼接文本

    # 返回 always 正文文本。
    return str_block_text

# _has_case_without_default 判断 case 结构是否缺少 default。
def _has_case_without_default(str_block_text: str) -> bool:
    """
    判断 next-state case 块是否缺少 default 分支。

    :param str_block_text: next-state always 块源码文本。
    :return: 存在 case 但没有 default 分支时返回 True。
    """

    # bool_has_case 表示该组合段采用 case 分派状态。
    bool_has_case = FSM_CASE_KEYWORD_PATTERN.search(str_block_text) is not None  # next-state case 结构存在性

    # bool_has_default 表示 case 至少覆盖默认兜底路径。
    bool_has_default = FSM_DEFAULT_BRANCH_PATTERN.search(str_block_text) is not None  # next-state default 分支存在性

    # case 存在但 default 缺失时触发 VG054。
    return bool_has_case and not bool_has_default

# _line_region_titles 提取源码中的区域横幅行号。
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

# _nearest_region_title 查找目标行之前最近的区域横幅。
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

# _is_low_active_reset_name 判断复位名是否表达低有效。
def _is_low_active_reset_name(str_reset: str) -> bool:
    """
    判断 reset 信号名是否符合低有效约定。

    :param str_reset: reset 信号名称。
    :return: reset 名称表达低有效时返回 True。
    """

    # set_allowed_resets 与旧 reset 风格规则保持一致。
    set_allowed_resets = {  # 低有效 reset 命名白名单
        "i_rstn",  # 通用输入复位约定
        "i_axis_arstn",  # AXIS 流接口异步低有效复位
        "i_axi_arstn",  # AXI memory-mapped 异步低有效复位
        "i_apb_prstn",  # APB 低有效外设复位约定
        "i_ahb_hrstn",  # AHB 低有效总线复位约定
    }

    # rstn/arstn 后缀和白名单都视为低有效。
    return str_reset.endswith("rstn") or str_reset.endswith("arstn") or str_reset in set_allowed_resets

# _is_fallback_comment 判断注释是否是 formatter fallback。
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

# _is_hollow_chinese_comment 判断中文注释是否缺少实体语义。
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

# _comment_has_meaningful_chinese 判断注释是否包含中文语义。
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

# _is_generic_comment 判断注释是否过于泛化。
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

# _has_blocking_assignment 判断代码行是否含阻塞赋值。
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

# _has_nonblocking_assignment 判断代码行是否含非阻塞赋值。
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

# _as_line 将外部诊断行号安全转换为 int。
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

# _has_space_before_tab 判断缩进中是否在 Tab 前混入空格。
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

# _bad_reset_style 判断时序 always 复位边沿和命名是否异常。
def _bad_reset_style(str_header: str, str_reset: str) -> bool:
    """
    判断时序 always 的复位触发和命名是否不符合低有效约定。

    :param str_header: 当前 always 或模块头部文本。
    :param str_reset: 当前复位信号名称。
    :return: 复位边沿或命名不符合低有效约定时返回 True。
    """

    # set_allowed_resets 保留旧质量门允许的常见低有效复位名。
    set_allowed_resets = {  # reset 规则保留的低有效复位名白名单
        "i_rstn",  # 通用低有效复位端口名
        "i_axis_arstn",  # AXIS 低有效复位端口名
        "i_axi_arstn",  # AXI memory/control 异步复位名
        "i_apb_prstn",  # APB peripheral reset 低有效名
        "i_ahb_hrstn",  # AHB HRESETn 风格低有效名
    }

    # bool_has_negedge 表示敏感列表中包含低有效边沿。
    bool_has_negedge = "negedge" in str_header  # 是否包含 negedge 触发

    # bool_named_low_active 表示复位名符合低有效后缀或白名单。
    bool_named_low_active = (
        str_reset.endswith("rstn")  # rstn 后缀表示低有效复位
        or str_reset.endswith("arstn")  # arstn 后缀表示异步低有效复位
        or str_reset in set_allowed_resets  # 历史白名单复位名
    )  # 复位名是否符合低有效命名约束

    # 缺少 negedge 或复位名不低有效都视作异常。
    return not bool_has_negedge or not bool_named_low_active

# _always_references_state_task 判断 always 是否像第三段状态输出/任务块。
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
