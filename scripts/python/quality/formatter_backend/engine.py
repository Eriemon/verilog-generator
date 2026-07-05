"""提供内置 Verilog formatter 管线的 facade 入口。"""

# 延迟类型注解求值，避免 mixin 之间的前向类型在导入期互相解析。
from __future__ import annotations
# difflib 用于 CLI diff 模式生成统一差异文本。
import difflib
# dataclass 用于表达单 module 格式化中间状态，不改变 formatter 行为。
from dataclasses import dataclass
# Path 类型承载输入、输出和工作区解析边界。
from pathlib import Path
# 常量模块集中保存协议分组、端口排序和渲染版面规则。
from . import constants
# 分析 mixin 负责端口、声明和 assign 之间的布局关系推导。
from .analysis_mixin import AnalysisMixin
# 控制流解析 mixin 负责 always、case、if 等过程结构拆解。
from .control_parse_mixin import ControlParseMixin
# 路由结果类型和轻量格式化入口服务写回策略。
from .format_routing import FormatRouteResult, micro_format_text
# 文件头 mixin 负责 Vivado 风格头部元数据提取与渲染。
from .header_mixin import HeaderMixin
# 版面 mixin 负责端口、参数和内部信号的分组排序。
from .layout_mixin import LayoutMixin
# 左值 mixin 负责过程赋值和 continuous assign 目标提取。
from .lvalue_mixin import LValueMixin
# 数据模型统一承载 parser、renderer 和对外导出的 formatter 类型。
from .models import (
    # 过程块模型覆盖 always、initial、function 和 task。
    AlwaysBlock,
    CaseItem,
    ControlNode,
    FunctionBlock,
    GenerateBlock,
    InitialBlock,
    InstanceBlock,
    TaskBlock,
    # 声明和赋值模型描述参数、端口、信号与 continuous assign。
    AssignStmt,
    BodyBlock,
    LValueRef,
    ParamDecl,
    ParamRenderCluster,
    PortDecl,
    RawBlock,
    SignalDecl,
    # 布局模型把输出端口、assign 来源和实例连接映射到渲染区域。
    AssignSourceLayout,
    HeaderMetadata,
    InstanceSignalLayout,
    OutputSignalLayout,
    PortLayoutInfo,
    PreprocessorConditional,
    # 统一异常类型贯穿 CLI、检查和结构化渲染路径。
    VerilogFormatterError,
)
# 语法解析 mixin 负责把 module 文本拆成 header、端口和 body。
from .parse_mixin import ParseMixin
# rename mixin 负责安全重命名端口、声明和过程块引用。
from .rename_mixin import RenameMixin
# renderer mixin 负责把结构化模型重新输出为 Verilog 文本。
from .render_mixin import ModuleRenderContext, RenderMixin
# 评分模块在写回前判断是否允许结构化重排。
from .scoring import ScoreReport, score_verilog_source
# statement renderer mixin 负责 always、assign、instance 等语句渲染。
from .statement_render_mixin import StatementRenderMixin
# 语法工具 mixin 提供注释切分、括号扫描和严格错误辅助。
from .syntax_utils import SyntaxUtilsMixin
# 文本 IO 入口统一处理 Verilog 文件读取编码。
from .textio import read_verilog_text

# 单 module 初始状态把 parser 输出集中起来，避免主流水线持有过多散变量。
@dataclass
class InitialModuleState:
    """保存单个 module 完成解析和基础归一化后的中间状态。"""

    # module 名最终进入 module 声明和诊断文本。
    module_name: str  # 当前 module 名称。

    # 原始 header 参数用于和 body localparam 一起建立重命名映射。
    raw_header_params: list[ParamDecl]  # module header 原始参数。

    # 原始端口列表用于端口归一化和 output 派生信号判断。
    ports: list[PortDecl]  # module 端口声明。

    # body 前导块在渲染阶段需要放回 module 内。
    body_preamble_blocks: list[str]  # module body 前置文本块。

    # body_items 保存 parser 归一化后的声明、过程块和保底片段。
    body_items: dict[str, object]  # module body 分桶状态。

    # raw_local_params 参与 header 参数和 body 参数之间的来源匹配。
    raw_local_params: list[ParamDecl]  # body 中待和 header 参数合并分析的 localparam。

    # header_params 是带状态标签的可渲染 header 参数。
    header_params: list[ParamDecl]  # 归一化 header 参数。

    # local_params 是 body 中归一化后的 localparam。
    local_params: list[ParamDecl]  # 完成状态标记后的 body localparam。

    # decls 汇总当前 module 内全部内部信号声明。
    decls: list[SignalDecl]  # 内部信号声明列表。

    # assigns 汇总当前 module 内全部 continuous assign。
    assigns: list[AssignStmt]  # 用于 output 亲和分析的连续赋值集合。

    # always_blocks 保存过程块，用于声明重命名和状态机检查。
    always_blocks: list[AlwaysBlock]  # always 过程块列表。

    # generate_blocks 可能包含 output 写入，需参与端口判定。
    generate_blocks: list[GenerateBlock]  # generate 区域列表。

    # body_blocks 保留原始 body 块顺序，用于结构验证。
    body_blocks: list[BodyBlock]  # 顶层 body 块列表。

    # state_param_names 标记需要进入状态机参数区的配置名称。
    state_param_names: set[str]  # 状态机相关参数名。

# 最终渲染上下文把 renderer 所需状态集中起来，避免 helper 参数列表膨胀。
@dataclass
class RenderModuleContext:
    """保存单 module 进入 renderer 前的最终结构状态。"""

    # initial_state 保留 module 名和 body 前置块，避免渲染阶段重新传散变量。
    initial_state: InitialModuleState  # 单 module 初始解析状态。

    # version 控制本次输出文件头中的版本文本。
    version: str  # formatter 文件头版本号。

    # include_header 标记多 module 场景中是否由当前片段承担文件头。
    include_header: bool  # 当前 module 是否渲染文件头。

    # leading_comments 保存多 module 切片前导注释。
    leading_comments: list[str] | None  # 当前 module 前导注释列表。

    # header_metadata 保留 Vivado 模板头部字段。
    header_metadata: HeaderMetadata | None  # 当前 module 可用的文件头元数据。

    # header_params 是最终会渲染到 module 参数区的参数集合。
    header_params: list[ParamDecl]  # 最终 header 参数集合。

    # normalized_ports 已完成重命名、布局信息准备和渲染前清理。
    normalized_ports: list[PortDecl]  # 最终可渲染端口集合。

    # body_items 是完成重命名和 always 分裂后的 renderer 输入树。
    body_items: dict[str, object]  # renderer 直接消费的 module body 分桶树。

    # top_level_local_params 是参数提升后仍留在 body 的 localparam。
    top_level_local_params: list[ParamDecl]  # body 内保留的顶层 localparam。

    # output_map 保存 output 端口对应的内部信号名集合。
    output_map: dict[str, object]  # output 内部信号映射。

    # output_affinity 保存 output 相关声明和 assign 的布局归属。
    output_affinity: dict[str, object]  # output 信号布局亲和状态。

    # all_decls 支撑 renderer 位宽类别缓存的重建。
    all_decls: list[SignalDecl]  # 当前 module 全局声明列表。

# 重命名阶段上下文承载 render 前仍会继续分组和校验的中间结果。
@dataclass
class RenameModuleContext:
    """保存单 module 完成端口、参数和 body 重命名后的状态。"""

    # body_items 已应用最终 rename_map，并完成 always 分裂。
    body_items: dict[str, object]  # 后续分组校验使用的重命名 body tree。

    # header_params 已合并最终参数名称，稍后追加 body 提升参数。
    header_params: list[ParamDecl]  # 渲染前的 header 参数基础集合。

    # normalized_ports 已完成最终命名和渲染准备。
    normalized_ports: list[PortDecl]  # output 校验和 renderer 共享的端口列表。

    # assigns 已替换为最终信号名，供 output 亲和分析使用。
    assigns: list[AssignStmt]  # 最终名称下的 continuous assign 集合。

    # output_map 保存 output 派生内部信号和原始端口映射。
    output_map: dict[str, object]  # output 端口内部信号命名关系。

    # output_affinity 保存 output 内部信号声明和 assign 的布局归属。
    output_affinity: dict[str, object]  # output 相关信号的最终布局信息。

# VerilogFormatterEngine 聚合所有 formatter mixin，保持旧版 facade 入口不变。
@dataclass(init=False, repr=False, eq=False)
class VerilogFormatterEngine(
    # 文件头、语法解析和控制流解析组成 formatter 前半段。
    HeaderMixin,
    ParseMixin,
    ControlParseMixin,
    # 左值、分析、布局和重命名组成结构化整理阶段。
    LValueMixin,
    AnalysisMixin,
    LayoutMixin,
    RenameMixin,
    # 渲染、语句渲染和语法工具组成输出阶段。
    RenderMixin,
    StatementRenderMixin,
    SyntaxUtilsMixin,
):
    """聚合内置 Verilog formatter 的解析、分析、重命名和渲染阶段。"""

    # __init__ 保存配置和跨阶段缓存，实例级状态只在单次格式化内复用。
    def __init__(self, config: dict):
        """初始化 formatter facade 的配置和运行态缓存。

        :param config: formatter 配置字典，包含缩进、备份、header 和 rewrite_policy 等字段。
        :return: 构造函数无业务返回值。
        """

        # 保存调用方传入的配置对象，所有 mixin 共享同一份策略。
        self.config = config  # formatter 运行配置。

        # 根据配置选择 tab 或固定空格缩进，供 renderer 和 micro-format 复用。
        self.indent_unit = (
            "\t"  # tab 模式直接使用单个制表符。
            if config["formatter"]["indent_style"] == "tab"  # 缩进风格配置。
            else " " * config["formatter"]["indent_size"]  # 空格模式按配置宽度生成。
        )  # 单级缩进文本。

        # violations 保存 check_text 本次检查发现的格式问题。
        self.violations: list[str] = []  # 当前检查诊断列表。

        # 当前源路径用于错误定位和相对工作区推断。
        self._current_source_path: Path | None = None  # 当前格式化源文件路径。

        # 当前工作区根用于跨文件 module 接口解析。
        self._current_workspace_root: Path | None = None  # 当前格式化工作区根路径。

        # 模块接口缓存避免同一文件在一次格式化内重复解析。
        self._module_interface_cache: dict[tuple[str, str], dict[str, object] | None] = {}  # module 接口缓存。

        # 模块解析缓存保存路径、模块名和工作区组合的查找结果。
        self._module_resolution_cache: dict[tuple[str, str, str], dict[str, object] | None] = {}  # module 解析缓存。

        # preamble 指令在解析阶段剥离，在渲染阶段需要重新贴回文件头。
        self._current_preamble_directives: list[str] = []  # 当前文件前置编译指令。

        # 信号位宽类别让渲染阶段保持同类信号的声明风格一致。
        self._current_signal_width_classes: dict[str, str] = {}  # 当前模块信号位宽类别。

    # format_path 是 CLI 路径级入口，负责读文件、写回、diff 和检查模式分流。
    def format_path(
        self,
        input_path: Path,
        output_path: Path | None = None,
        inplace: bool = False,
        check: bool = False,
        diff: bool = False,
    ) -> tuple[int, str]:
        """格式化或检查指定 Verilog 文件路径。

        :param input_path: 输入 RTL 文件路径。
        :param output_path: 可选输出文件路径。
        :param inplace: 是否原地改写输入文件。
        :param check: 是否仅检查格式差异。
        :param diff: 是否返回 unified diff 文本。
        :return: 类 CLI 状态码和用户可读消息或格式化文本。
        :raises VerilogFormatterError: 源文本无法通过 formatter 严格规则时抛出。
        """

        # 先读取源文件文本，后续所有模式都共享同一份输入。
        str_source = read_verilog_text(input_path)  # 输入文件原始文本。

        # 格式化失败时把路径补进错误消息，便于 CLI 定位问题文件。
        try:

            # 文本级 formatter 是所有路径级模式的共同核心。
            str_formatted = self.format_text(str_source, input_path)  # formatter 候选输出文本。

        # 将 formatter 内部错误转换为带路径的统一异常文本。
        except VerilogFormatterError as exc:

            # 路径级异常必须带 current-project 错误前缀。
            raise VerilogFormatterError(
                f"> ERR: [Python] formatter failed for {input_path}: {exc}"
            ) from exc

        # check 模式只比较文本，不写文件。
        if check:

            # 格式相同返回 0，否则返回 1 供 CI 或 CLI 使用。
            return (
                0 if str_source == str_formatted else 1,
                "No formatting needed." if str_source == str_formatted else "Formatting required.",
            )

        # diff 模式返回 unified diff，调用方负责展示或写入。
        if diff:

            # difflib 需要保留换行，才能生成稳定的逐行差异。
            str_diff_text = "".join(  # diff 模式输出的 unified diff 正文。
                difflib.unified_diff(  # 输入文本和候选输出之间的逐行差异。
                    str_source.splitlines(keepends=True),  # 原始源码按行保留换行符。
                    str_formatted.splitlines(keepends=True),  # formatter 输出按行保留换行符。
                    fromfile=str(input_path),  # diff 左侧显示输入文件路径。
                    tofile="formatted",  # diff 右侧固定标记 formatter 输出。
                )
            )

            # diff 模式不写盘，只把差异交给调用方。
            return 0, str_diff_text

        # inplace 模式在可选备份后覆盖输入文件。
        if inplace:

            # 备份路径沿用配置后缀，保持历史 CLI 行为。
            path_backup = input_path.with_suffix(  # 原地写回前保存旧内容的备份路径。
                input_path.suffix + self.config["backup"]["suffix"]  # 沿用配置中的备份扩展后缀。
            )

            # 只有显式启用备份时才写出旧文件。
            if self.config["backup"]["enabled"]:

                # 备份保留格式化前文本，便于用户回滚。
                path_backup.write_text(str_source, encoding="utf-8")

            # 覆盖输入路径，完成原地格式化。
            input_path.write_text(str_formatted, encoding="utf-8")

            # 返回短消息供 CLI 汇总。
            return 0, f"Formatted in place: {input_path}"

        # output_path 模式写入调用者指定位置。
        if output_path:

            # 确保输出目录存在，避免 write_text 因父目录缺失失败。
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # 输出文件保存 formatter 候选文本。
            output_path.write_text(str_formatted, encoding="utf-8")

            # 返回写出路径摘要。
            return 0, f"Wrote formatted file: {output_path}"

        # 默认模式把格式化文本返回给调用方。
        return 0, str_formatted

    # format_text 是内存文本入口，负责把路由层失败转换为异常。
    def format_text(self, source: str, source_path: Path | None = None) -> str:
        """格式化内存中的 Verilog 文本。

        :param source: 待格式化的 Verilog/SystemVerilog 源文本。
        :param source_path: 可选来源路径，用于评分和诊断定位。
        :return: 格式化后的 Verilog 文本。
        :raises VerilogFormatterError: 路由层判定当前文本不能安全写回时抛出。
        """

        # 路由层先判断是否能结构化重排或只能保留文本。
        format_route_result_format_route_result: FormatRouteResult = self._route_format_text(source, source_path)  # 写回路由结果。

        # text 为 None 表示评分硬门禁阻止生成格式化输出。
        if format_route_result_format_route_result.text is None:

            # 失败消息使用统一错误前缀，保留路由层原始说明。
            raise VerilogFormatterError(
                f"> ERR: [Python] format route rejected source: {format_route_result_format_route_result.message}"
            )

        # 返回允许写回或保留的文本。
        return format_route_result_format_route_result.text

    # _route_format_text 根据 rewrite_policy 和评分结果选择写回动作。
    def _route_format_text(self, source: str, source_path: Path | None = None) -> FormatRouteResult:
        """选择当前文本的 formatter 写回路径。

        :param source: 待评分和格式化的 Verilog 源文本。
        :param source_path: 可选来源路径，用于评分上下文。
        :return: 包含决策、动作、评分报告和候选文本的路由结果。
        """

        # rewrite_policy.mode 控制 normalize、preserve、never 和 auto 行为。
        str_mode = self.config.get("rewrite_policy", {}).get("mode", "auto")  # 写回策略模式。

        # normalize 模式绕过评分分支，强制走结构化 renderer。
        if str_mode == "normalize":

            # 强制 normalize 仍记录评分报告，便于调用方追踪风险。
            return FormatRouteResult(
                decision="normalize_forced",
                action="normalize",
                report=self.score_text(source, source_path),
                text=self._format_text_normalize(source, source_path),
                message="rewrite_policy.mode=normalize: structural renderer selected.",
            )

        # 评分报告决定 auto/preserve/never 下是否允许重排。
        score_report = self.score_text(source, source_path)  # 当前源文本评分报告。

        # decision 字段来自评分报告，统一转字符串以防配置载入成非字符串。
        str_decision = str(score_report["decision"])  # 评分层路由决策。

        # 硬门禁失败时不产生输出文本。
        if str_decision == "fail_no_write":

            # hard_gates 展示具体阻断项，缺失时给出兜底说明。
            str_hard_gates = (  # 评分硬门禁摘要文本。
                ", ".join(str(obj_item) for obj_item in score_report.get("hard_gates", []))  # 评分器列出的硬阻断项。
                or "unknown hard gate"  # 评分器未返回细节时的兜底文本。
            )

            # 返回 no_write 结果，让上层决定是否抛异常或报告。
            return FormatRouteResult(
                decision=str_decision,
                action="no_write",
                report=score_report,
                text=None,
                message=f"Scoring hard gate failed; no formatted output was written: {str_hard_gates}",
            )

        # never 模式禁止 formatter 产生任何改写。
        if str_mode == "never":

            # 已符合标准时可以原样通过。
            if str_decision == "already_standard":

                # preserve 动作保留原文，避免无意义写回。
                return FormatRouteResult(
                    str_decision,
                    "preserve",
                    score_report,
                    source,
                    "rewrite_policy.mode=never: source is already standard.",
                )

            # 非标准文本在 never 模式下被明确阻断。
            return FormatRouteResult(
                decision=str_decision,
                action="no_write",
                report=score_report,
                text=None,
                message=(
                    "rewrite_policy.mode=never: formatting changes are blocked; "
                    "run score_verilog.py --json for the scoring report."
                ),
            )

        # preserve 模式只允许轻量微格式化或原样保留。
        if str_mode == "preserve":

            # 评分允许 micro-format 时仅整理局部空白。
            if str_decision == "preserve_micro_format":

                # micro_format_text 不做结构化重排或重命名。
                return FormatRouteResult(
                    str_decision,
                    "micro_format",
                    score_report,
                    micro_format_text(source, self.indent_unit),
                    "Micro-format only.",
                )

            # preserve 模式对其它决策保持原文。
            return FormatRouteResult(
                str_decision,
                "preserve",
                score_report,
                source,
                "Preserve mode selected; structural renderer blocked.",
            )

        # auto 模式允许受控候选进入结构化 renderer。
        if str_mode == "auto" and str_decision == "controlled_normalize_candidate":

            # 结构化 normalize 会执行完整解析、重命名和渲染流程。
            return FormatRouteResult(
                decision=str_decision,
                action="normalize",
                report=score_report,
                text=self._format_text_normalize(source, source_path),
                message="Auto route selected controlled normalize candidate.",
            )

        # auto 模式下的轻量候选只做 micro-format。
        if str_mode == "auto" and str_decision == "preserve_micro_format":

            # 保守整理空白，不触碰 module 结构。
            return FormatRouteResult(
                str_decision,
                "micro_format",
                score_report,
                micro_format_text(source, self.indent_unit),
                "Auto route selected micro-format.",
            )

        # 其它 auto 决策统一保留原文。
        return FormatRouteResult(
            str_decision,
            "preserve",
            score_report,
            source,
            "Auto route preserved source text.",
        )

    # _format_text_normalize 执行完整结构化格式化，并在结束时恢复运行态。
    def _format_text_normalize(self, source: str, source_path: Path | None = None) -> str:
        """把单个 Verilog 源文本按结构化 renderer 归一化。

        :param source: 待结构化格式化的 Verilog 文本。
        :param source_path: 可选来源路径，用于工作区和 include/module 推断。
        :return: 结构化 renderer 生成的 Verilog 文本。
        :raises VerilogFormatterError: 文件头、module 解析或结构化渲染阶段发现严格规则失败时抛出。
        """

        # 每次 normalize 都从空诊断列表开始。
        self.violations = []  # 当前 normalize 诊断列表。

        # 暂存原有路径状态，finally 中必须恢复。
        path_previous_source = self._current_source_path  # 调用前源路径状态。

        # 暂存原有工作区根，避免嵌套调用污染外层状态。
        path_previous_workspace_root = self._current_workspace_root  # 调用前工作区根。

        # preamble 指令需要复制一份，防止列表对象被后续阶段原地修改。
        list_previous_preamble_directives = list(self._current_preamble_directives)  # 调用前前置指令。

        # 当前路径统一解析为绝对路径，缺失路径时保持 None。
        self._current_source_path = source_path.resolve() if source_path is not None else None  # 本次 normalize 的绝对源路径。

        # 工作区根由当前源路径推断，服务跨文件 module 接口查找。
        self._current_workspace_root = self._resolve_workspace_root(self._current_source_path)  # 本次跨文件解析根目录。

        # preamble 指令先从原文提取，稍后 renderer 再放回文件开头。
        self._current_preamble_directives = self._extract_preamble_directives(source)  # 本次文件级前置指令。

        # normalize 过程需要确保临时状态总能恢复。
        try:

            # 文件头提取结果先保存为显式二元组，避免后续 module 解析重复扫描源码。
            tuple_header_metadata_source: tuple[HeaderMetadata | None, str] = (
                self._extract_header_metadata_and_source(source)  # 一次性解析文件头和剩余源码，供版本选择与 module parser 共用。
            )  # 文件头对象和去头源码的稳定二元组。

            # header_metadata_data 只承载已解析的 Vivado 风格文件头对象，缺失文件头时保持 None。
            header_metadata_data: HeaderMetadata | None = tuple_header_metadata_source[0]  # renderer 复用的文件头数据对象。

            # 去掉文件头后的源码用于 module 分段，避免注释头被误判为 Verilog 主体。
            str_clean_source = tuple_header_metadata_source[1]  # 去头后交给 module parser 的源码文本。

            # 优先使用头部版本，其次从原文提取，最后回落到配置默认值。
            str_version = (  # 当前输出文件版本号。
                header_metadata_data.version  # 文件头显式版本优先保留。
                if header_metadata_data is not None and header_metadata_data.version  # 只有有效头部版本才采用。
                else self._extract_version(source) or self.config["header"]["default_version"]  # 缺失时回落到源码或配置。
            )

            # 示例兼容模式允许一个文本中包含多个 module。
            if self._example_compat_enabled():

                # 多 module 文本先拆成独立片段，再逐段渲染。
                list_module_sections = self._split_module_sections(str_clean_source)  # module 片段列表。

                # 按原始顺序收集每个 module 的渲染结果。
                list_rendered_sections: list[str] = []  # 多 module 输出片段。

                # 首个 module 负责承载文件头，其余 module 只渲染主体。
                for int_index, dict_section in enumerate(list_module_sections):

                    # 每个 module section 都复用同一版本号，首段保留头部元数据。
                    str_rendered_section = self._format_single_module_text(  # 单个 module 渲染文本。
                        dict_section["module_text"],  # 当前 module 的完整源码片段。
                        str_version,  # 多 module 文件共享同一个版本号。
                        include_header=int_index == 0,  # 只允许首个 module 渲染文件头。
                        leading_comments=dict_section["leading_comments"],  # 当前片段前导注释。
                        header_metadata=header_metadata_data if int_index == 0 else None,  # 文件头元数据只交给首段。
                    ).rstrip()

                    # 多 module 输出之间使用一个空行分隔。
                    list_rendered_sections.append(str_rendered_section)

                # 多 module 模式要求最终文件仍以单个换行结尾。
                return "\n\n".join(list_rendered_sections) + "\n"

            # 单 module 模式直接渲染清理后的源码。
            return self._format_single_module_text(
                str_clean_source,
                str_version,
                include_header=True,
                header_metadata=header_metadata_data,
            )

        # 无论 normalize 成功或失败，都恢复调用前运行态。
        finally:

            # 恢复调用前源路径，避免下次格式化误用本次上下文。
            self._current_source_path = path_previous_source  # 恢复外层调用持有的源路径。

            # 恢复调用前工作区根。
            self._current_workspace_root = path_previous_workspace_root  # 恢复外层调用的工作区根。

            # 恢复调用前 preamble 指令列表，避免跨文件保留旧编译指令。
            self._current_preamble_directives = list_previous_preamble_directives  # 外层调用前的文件前置指令缓存。

    # check_text 对比格式化结果并返回诊断列表，不直接写回文本。
    def check_text(self, source: str, source_path: Path | None = None) -> list[str]:
        """检查内存中的 Verilog 文本是否符合 formatter 输出。

        :param source: 待检查的 Verilog/SystemVerilog 源文本。
        :param source_path: 可选来源路径，用于诊断定位。
        :return: formatter 发现的诊断消息列表。
        """

        # 每次检查重置诊断列表，避免复用旧结果。
        self.violations = []  # check_text 本轮重新收集的诊断容器。

        # formatter 失败时直接把异常文本作为诊断返回。
        try:

            # 复用文本级格式化入口得到候选输出。
            str_formatted = self.format_text(source, source_path)  # 格式化候选文本。

        # check_text 是诊断 API，错误转成列表而不是继续抛出。
        except VerilogFormatterError as exc:

            # 返回单条错误文本，保持历史接口形态。
            return [str(exc)]

        # 输出不一致代表源码需要格式化。
        if str_formatted != source:

            # 诊断文本维持既有英文消息，避免改变 CLI 断言。
            self.violations.append("Source does not match the enforced formatter template.")

        # 返回本次检查收集到的诊断列表。
        return self.violations

    # score_path 是文件级评分入口，只读源文件并返回结构化评分。
    def score_path(self, path: Path) -> ScoreReport:
        """对指定 RTL 文件执行格式评分但不渲染或写回。

        :param path: 待评分的 RTL 文件路径。
        :return: 评分模块生成的结构化报告。
        """

        # 先解析为绝对路径，评分报告中的定位信息保持稳定。
        path_resolved = path.resolve()  # 评分文件绝对路径。

        # 文件内容读取后交给文本级评分入口。
        return self.score_text(read_verilog_text(path_resolved), path_resolved)

    # score_text 是评分模块的 facade 包装，不触发 renderer。
    def score_text(self, source: str, source_path: Path | None = None) -> ScoreReport:
        """对内存中的 RTL 文本执行格式风险评分。

        :param source: 待评分的 Verilog/SystemVerilog 源文本。
        :param source_path: 可选来源路径，用于诊断定位。
        :return: 评分模块生成的结构化报告。
        """

        # score_verilog_source 是写回路由前唯一的评分口径。
        return score_verilog_source(source, source_path, self.config)

    # _collect_initial_module_state 负责单 module 的解析和基础归一化准备。
    def _collect_initial_module_state(self, source: str) -> InitialModuleState:
        """收集单 module 渲染前的初始结构化状态。

        :param source: 单个 module 的 Verilog 源文本。
        :return: 包含 parser 输出、参数归一化结果和基础 body 分桶的状态对象。
        :raises VerilogFormatterError: parser 或归一化阶段发现严格格式问题时抛出。
        """

        # parser 首先拆出 module 名、参数、端口和 body 原文。
        tuple_module_parts = self._parse_module(source)  # module 名、header 参数、端口和 body 的解析结果。

        # module 名用于最终 module 声明和错误定位。
        str_module_name = tuple_module_parts[0]  # 错误定位与 renderer 声明共用的名称。

        # header 参数保持原始声明顺序，稍后再归一化。
        list_raw_header_params = tuple_module_parts[1]  # 尚未打状态标签的 header 参数。

        # 端口声明列表是端口布局和 output 派生信号的输入。
        list_ports = tuple_module_parts[2]  # 端口归一化前的 parser 端口模型。

        # body 文本承载声明、assign、always、instance 等结构。
        str_body = tuple_module_parts[3]  # 等待剥离 preamble 的 body 原文。

        # body 前导 preamble 需要先剥离，渲染时再放回 module 内。
        tuple_body_preamble = self._extract_body_leading_preamble(str_body)  # body 内前置指令和主体文本。

        # body_preamble_blocks 保留 module 内前置指令和说明。
        list_body_preamble_blocks = tuple_body_preamble[0]  # renderer 需贴回 module 内的前置块。

        # 更新 body 文本为去掉前置块后的主体。
        str_body = tuple_body_preamble[1]  # 可交给 body parser 的主体文本。

        # body parser 输出按类别分桶的结构化条目。
        dict_body_items = self._parse_body(str_body)  # 声明、assign、过程块和保底块分桶树。

        # localparam 与 header 参数共同参与状态参数推断。
        list_raw_local_params = self._collect_body_items_recursive(  # 状态参数推断使用的原始 localparam。
            dict_body_items,  # 未应用状态标签的参数来源树。
            "localparams",  # header/body 参数合并分析的递归键。
        )

        # always 块用于识别状态参数名和后续状态机验证。
        list_raw_always_blocks = self._collect_body_items_recursive(  # 查找状态跳转引用的原始过程块。
            dict_body_items,  # 未拆分过程块的状态引用来源树。
            "always",  # 状态名推断阶段扫描的过程块类别。
        )

        # 状态参数名影响参数渲染分区和状态机一致性检查。
        set_state_param_names = self._infer_state_param_names(  # header 分区需要的状态参数名集合。
            list_raw_header_params + list_raw_local_params,  # 参与推断的参数声明。
            list_raw_always_blocks,  # 提供状态引用线索的 always 块。
        )

        # header 参数归一化后带有状态标签和渲染辅助信息。
        list_header_params = [  # 可渲染 header 参数模型列表。
            self._normalize_param(param, state=param.name in set_state_param_names)  # 单个 header 参数模型。
            for param in list_raw_header_params  # 原始 header 参数顺序。
        ]  # 归一化后的 header 参数。

        # body tree 需要先应用状态参数信息，后续重命名才能保持一致。
        dict_body_items = self._normalize_body_tree(  # 带状态标签的 body tree。
            dict_body_items,  # 仍保留 parser 名称的 body tree。
            set_state_param_names,  # 状态参数名集合。
        )

        # localparam、声明、assign 和过程块共同决定重命名映射。
        list_local_params = self._collect_body_items_recursive(  # rename 阶段读取的 body localparam。
            dict_body_items,  # 已带状态标签的参数分桶树。
            "localparams",  # rename 阶段读取的参数分桶键。
        )

        # 声明列表用于派生 output 内部信号和声明布局。
        list_decls = self._collect_body_items_recursive(  # rename 阶段读取的内部信号声明。
            dict_body_items,  # 已完成参数归一化的声明分桶树。
            "decls",  # 内部信号声明分桶键。
        )

        # assign 列表用于 output 亲和关系和 continuous assign 分区。
        list_assigns = self._collect_body_items_recursive(  # output 亲和分析读取的 continuous assign。
            dict_body_items,  # output 端口直连分析前的 assign 来源树。
            "assigns",  # direct-output 识别阶段扫描的连续赋值类别。
        )

        # always 列表用于声明重命名和状态机检查。
        list_always_blocks = self._collect_body_items_recursive(  # rename 和状态机检查读取的 always。
            dict_body_items,  # 已完成参数归一化的过程块树。
            "always",  # 声明 rename 参考的过程块分桶键。
        )

        # generate 块可能包含 output 赋值，需要参与直接 output 端口判断。
        list_generate_blocks = self._collect_body_items_recursive(  # output 端口判定读取的 generate 区域。
            dict_body_items,  # 可能隐藏 output 赋值的 generate 分桶树。
            "generates",  # 直接 output 判定读取的 generate 分桶键。
        )

        # 顶层 body blocks 保留 parser 原始分区，用于后续结构验证。
        list_body_blocks = dict_body_items["blocks"]  # 结构重排前的顶层块边界。

        # 返回聚合状态，主渲染函数继续执行重命名和布局阶段。
        return InitialModuleState(
            module_name=str_module_name,  # 渲染 module 声明使用的名称。
            raw_header_params=list_raw_header_params,  # 保留原始 header 参数供 rename 追踪。
            ports=list_ports,  # 保留 parser 端口模型供端口规范化。
            body_preamble_blocks=list_body_preamble_blocks,  # 渲染时贴回 module 内的前导块。

            # 参数来源和 body tree 会驱动后续 rename 映射生成。
            body_items=dict_body_items,  # 已应用状态参数归一化的 body tree。
            raw_local_params=list_raw_local_params,  # rename 阶段使用的原始 body 参数。
            header_params=list_header_params,  # 已归一化的 header 参数。
            local_params=list_local_params,  # body localparam 已携带状态参数标记。

            # 结构化 body 分桶提供布局、校验和渲染输入。
            decls=list_decls,  # 已归一化的内部信号声明。
            assigns=list_assigns,  # 已归一化的连续赋值语句。
            always_blocks=list_always_blocks,  # 已归一化的 always 过程块。
            generate_blocks=list_generate_blocks,  # 已归一化的 generate 区域。

            # 原始结构边界用于后续严格校验。
            body_blocks=list_body_blocks,  # 顶层 body 原始块顺序。
            state_param_names=set_state_param_names,  # 后续参数分区使用的状态名集合。
        )

    # _render_module_with_width_context 负责最终渲染和实例级位宽缓存恢复。
    def _render_module_with_width_context(
        self,
        render_context: RenderModuleContext,
    ) -> str:
        """在临时位宽上下文中渲染单个 module。

        :param render_context: renderer 需要的单 module 最终结构状态。
        :return: renderer 输出的单 module 文本。
        """

        # 渲染时需要临时设置位宽类别映射，结束后恢复。
        dict_previous_signal_width_classes = dict(  # 调用前信号位宽类别快照。
            self._current_signal_width_classes  # 外层或前一 module 的位宽类别缓存。
        )

        # 当前模块的位宽类别来自最终端口和全局声明。
        self._current_signal_width_classes = self._build_signal_width_class_map(  # renderer 本次使用的位宽分类表。
            render_context.normalized_ports,  # 当前 module 最终端口列表。
            render_context.all_decls,  # 用于补齐内部信号位宽类别的声明全集。
        )

        # 渲染过程必须恢复实例级位宽类别状态。
        try:

            # render_body_items 是 renderer 消费的最终 body tree 副本。
            dict_render_body_items = dict(render_context.body_items)  # 渲染阶段 body tree 副本。

            # 顶层 localparam 使用分区后保留在 body 的那一组。
            dict_render_body_items["localparams"] = render_context.top_level_local_params  # body 内最终 localparam 列表。

            # module_render_context 汇总 header、端口、声明、assign 和过程块渲染输入。
            module_render_context = ModuleRenderContext(  # 单 module 渲染上下文。
                module_name=render_context.initial_state.module_name,  # module 声明沿用 parser 识别名称。
                version=render_context.version,  # 文件头版本来自 normalize 阶段统一推断。
                params=render_context.header_params,  # header 参数包含 body 提升参数。
                ports=render_context.normalized_ports,  # 端口已完成 rename 与渲染准备。
                body_items=dict_render_body_items,  # body tree 带最终 localparam 分区。

                # 文件级附加信息决定 module 头部和前导注释输出。
                file_preamble_blocks=render_context.initial_state.body_preamble_blocks,  # module 内前置块贴回原位置。
                include_header=render_context.include_header,  # 多 module 首段才渲染文件头。
                leading_comments=render_context.leading_comments or [],  # 当前 module 前面保留的非文件头注释。
                header_metadata=render_context.header_metadata,  # Vivado 文件头字段继续交给 renderer。

                # output 派生信号布局决定端口旁内部信号的渲染位置。
                output_internal_names=render_context.output_map["internal_names"],  # output 端口对应的内部信号名。
                output_internal_layouts=render_context.output_affinity["layouts"],  # output 内部信号声明布局。
                output_target_layouts=render_context.output_affinity["target_layouts"],  # output 目标信号布局。
            )

            # renderer 根据上下文输出完整 module 文本。
            return self._render_module(module_render_context)

        # 结束单 module 渲染时恢复调用前的位宽类别映射。
        finally:

            # 恢复前一个 module 或外层调用的信号位宽类别状态。
            self._current_signal_width_classes = dict_previous_signal_width_classes  # 调用前位宽分类表。

    # _build_rename_module_context 完成端口、参数、声明和 body 的统一重命名。
    def _build_rename_module_context(self, initial_state: InitialModuleState) -> RenameModuleContext:
        """构建单 module 完成重命名后的中间上下文。

        :param initial_state: parser 和基础归一化阶段生成的单 module 状态。
        :return: 后续渲染上下文构建所需的重命名阶段产物。
        """

        # body tree 会在最终 rename 和 always 分裂阶段替换为新对象。
        dict_body_items = initial_state.body_items  # 单 module 重命名前 body tree。

        # header 参数后续会追加从 body 提升出来的参数。
        list_header_params = initial_state.header_params  # 当前 header 参数渲染候选。

        # body localparam 跟随 rename_map 更新，保证参数引用一致。
        list_local_params = initial_state.local_params  # 当前 body localparam 候选。

        # continuous assign 在 output 派生和最终重命名中都会被读取。
        list_assigns = initial_state.assigns  # output 直连判定和预览 rename 共用的 assign 输入。

        # direct output 端口决定是否需要生成内部信号。
        set_direct_output_ports = self._collect_direct_output_ports(  # output 端口直连判定结果。
            initial_state.ports,  # 原始端口用于识别 output 方向。
            list_assigns,  # assign 目标用于识别直接驱动端口。
            initial_state.generate_blocks,  # generate 内赋值也影响端口直连判断。
        )  # 直接驱动 output 的端口名集合。

        # 端口归一化会同时给出 output 内部信号和初始 rename_map。
        tuple_normalized_output = self._normalize_ports(  # 端口归一化和 output 映射二元组。
            initial_state.ports,  # parser 输出的原始端口模型。
            set_direct_output_ports,  # 需要保留直连形态的 output 名称。
        )  # 端口归一化与 output 派生结果。

        # normalized_ports 是最终端口重命名的基础模型。
        list_normalized_ports = tuple_normalized_output[0]  # 初始归一化端口列表。

        # output_map 保存 output 内部名和初始 rename_map。
        dict_output_map = tuple_normalized_output[1]  # output 派生信号映射。

        # base rename map 先收集 output、声明和参数三类重命名。
        dict_base_rename_map = dict(dict_output_map["rename_map"])  # output 端口初始重命名表。

        # 声明重命名补齐内部信号的规范名称。
        dict_base_rename_map.update(
            self._build_decl_rename_map(  # 内部声明与过程块共同决定信号规范名。
                initial_state.decls,  # parser 收集的内部信号声明。
                initial_state.always_blocks,  # 声明重命名需避开过程块目标引用。
            )
        )

        # 参数重命名保持 header 和 body 参数命名一致。
        dict_base_rename_map.update(
            self._build_param_rename_map(
                initial_state.raw_header_params + initial_state.raw_local_params,  # rename 来源参数。
                list_header_params + list_local_params,  # rename 目标参数。
            )
        )

        # 自映射不会改变文本，先移除以减少后续重命名噪声。
        dict_base_rename_map = {  # 清理后的基础 rename 表。
            str_old_name: str_new_name  # 旧名直接指向基础规范名。
            for str_old_name, str_new_name in dict_base_rename_map.items()  # 扫描基础 rename 候选。
            if str_old_name != str_new_name  # 过滤不会改变文本的自映射。
        }  # 去掉无效自映射后的基础 rename 表。

        # preview 端口用于计算 output 内部信号的最终亲和关系。
        list_preview_ports = self._rename_ports(  # output 内部信号命名使用的预览端口。
            list_normalized_ports,  # 初始归一化端口模型。
            dict_base_rename_map,  # 不含 output 内部补充项的基础映射。
        )  # 应用基础 rename 后的端口预览。

        # 预览端口先应用渲染准备，保证布局信息完整。
        list_preview_ports = self._prepare_ports_for_render(list_preview_ports)  # 带布局字段的预览端口。

        # 预览 assign 让 output 内部信号重命名使用同一套基础名称。
        list_preview_assigns = self._rename_assigns(  # output 内部信号命名使用的预览 assign。
            list_assigns,  # 初始 continuous assign 集合。
            dict_base_rename_map,  # 与预览端口相同的基础映射。
        )  # 应用基础 rename 后的 assign 预览。

        # 最终 rename_map 从基础映射开始，再追加 output 内部信号映射。
        dict_rename_map = dict(dict_base_rename_map)  # 将继续扩展的最终 rename 表。

        # output 内部信号映射依赖预览端口和预览 assign。
        dict_output_internal_rename_map = self._build_output_internal_rename_map(  # output 内部信号补充映射。
            list_preview_ports,  # 已具备基础布局信息的端口预览。
            initial_state.decls,  # 原始声明用于匹配 output 内部信号。
            list_preview_assigns,  # 预览 assign 用于反推 output 来源。
            dict_base_rename_map,  # 基础映射用于识别已改名信号。
            dict_output_map["internal_names"],  # output 端口到内部信号的初始映射。
        )  # output 内部信号专用 rename 表。

        # output 内部信号的补充映射必须进入最终 rename_map。
        dict_rename_map.update(dict_output_internal_rename_map)

        # terminal names 避免链式重命名把内部信号继续改回端口名。
        set_terminal_names = set(dict_output_map["internal_names"].values()) | set(  # 链式 rename 的终点名集合。
            dict_output_internal_rename_map.values()  # output 内部补充映射的目标名。
        )  # rename 链压缩时允许保留的终点名集合。

        # 解析链式 rename，保证每个旧名直接映射到最终名称。
        dict_rename_map = self._resolve_rename_map_chains(  # 压缩后的最终 rename 表。
            dict_rename_map,  # 合并 output、声明和参数后的 rename 表。
            set_terminal_names,  # 不应继续追溯的 output 内部信号名。
        )  # 压缩链路后的最终 rename 表。

        # 再次移除链路压缩后可能出现的自映射。
        dict_rename_map = {  # 移除自映射后的最终 rename 表。
            str_old_name: str_new_name  # 旧名映射到压缩后的最终名。
            for str_old_name, str_new_name in dict_rename_map.items()  # 扫描链路压缩结果。
            if str_old_name != str_new_name  # 丢弃最终仍未改名的条目。
        }  # 仅保留真实改名项的最终 rename 表。

        # header 参数应用最终 rename_map。
        list_header_params = self._rename_params(list_header_params, dict_rename_map)  # 最终 header 参数名称。

        # localparam 应用同一 rename_map，保持参数引用一致。
        list_local_params = self._rename_params(list_local_params, dict_rename_map)  # body 参数引用改写后的 localparam。

        # 端口应用最终 rename_map。
        list_normalized_ports = self._rename_ports(  # 应用最终 rename 的端口列表。
            list_normalized_ports,  # 尚未应用 output 补充映射的端口模型。
            dict_rename_map,  # 已含 output 内部信号补充项的最终映射。
        )  # 最终端口名称列表。

        # 最终端口进入渲染前准备阶段。
        list_normalized_ports = self._prepare_ports_for_render(list_normalized_ports)  # renderer 可直接使用的端口。

        # assign 必须在 output affinity 前改名，否则 output 内部信号会按旧名匹配失败。
        list_assigns = self._rename_assigns(list_assigns, dict_rename_map)  # 保证 output 内部信号校验看到的是改名后的 assign 目标。

        # output affinity 把 output 端口、内部信号和 assign 布局关联起来。
        dict_output_affinity = self._build_output_affinity(  # output 内部信号放置在端口附近的依据。
            list_normalized_ports,  # 最终端口列表提供 output 方向和布局。
            dict_output_map["internal_names"],  # output 端口到内部信号的最终命名关系。
            list_assigns,  # 最终 assign 列表提供 output 来源布局。
        )  # output 派生信号的布局亲和信息。

        # body tree 应用最终 rename_map，并可补齐缺失的 output 内部信号。
        dict_body_items = self._rename_body_tree(  # 应用最终 rename 的 body tree。
            dict_body_items,  # 初始 body tree 承载声明、assign 和过程块。
            dict_rename_map,  # 最终 rename 表覆盖端口、参数和内部信号。
            dict_output_map["internal_names"],  # 缺失 output 内部信号的补齐依据。
            dict_output_affinity["layouts"],  # output 派生声明插入声明区的位置。
            add_missing_output_internals=True,  # 缺失 output 内部信号时由 body rename 阶段补齐声明。
        )  # 最终名称下的 body tree。

        # always 分裂在重命名后执行，避免旧名称影响目标集合。
        dict_body_items = self._split_body_tree_always(dict_body_items)  # 已按目标拆分的 body tree。

        # 汇总重命名阶段产物，下一阶段只负责分组、校验和渲染上下文装配。
        return RenameModuleContext(
            body_items=dict_body_items,  # renderer 前仍可继续分组的 body tree。
            header_params=list_header_params,  # 参数引用已完成重命名的 header 参数。
            normalized_ports=list_normalized_ports,  # 端口名已符合输出规范的端口列表。

            # assign 和 output 映射共同支撑后续 output invariant 校验。
            assigns=list_assigns,  # 左右值已完成重命名的 assign 列表。
            output_map=dict_output_map,  # output 内部信号映射沿用端口归一化结果。
            output_affinity=dict_output_affinity,  # output 端口附近声明和赋值的布局归属。
        )

    # _build_render_module_context 负责分组、校验并装配 renderer 输入。
    def _build_render_module_context(
        self,
        # 初始状态提供 module 名、原始 body 边界和前置块。
        initial_state: InitialModuleState,
        # 重命名上下文提供已经改写过的端口、参数和 body tree。
        rename_context: RenameModuleContext,
        # 以下文件头参数只影响 renderer 输出头部，不参与结构分析。
        version: str,
        include_header: bool,
        leading_comments: list[str] | None,
        header_metadata: HeaderMetadata | None,
    ) -> RenderModuleContext:
        """根据重命名阶段产物构建最终 renderer 上下文。

        :param initial_state: parser 阶段生成的单 module 初始状态。
        :param rename_context: 端口、参数和 body 已完成重命名的中间状态。
        :param version: 当前输出文件头版本号。
        :param include_header: 是否由当前 module 渲染文件头。
        :param leading_comments: 多 module 模式下跟随当前 module 的前导注释。
        :param header_metadata: 可选文件头元数据，只有首个 module 使用。
        :return: 可直接交给 renderer 的上下文对象。
        """

        # 重命名阶段产物在本阶段继续分组和结构校验。
        dict_body_items = rename_context.body_items  # 待分组校验的最终 body tree。

        # header 参数后续追加从 body 提升出来的参数。
        list_header_params = rename_context.header_params  # rename 后的 header 参数基础集合。

        # 端口列表已经具备 renderer 所需布局字段。
        list_normalized_ports = rename_context.normalized_ports  # renderer 使用的最终端口集合。

        # output_map 继续为 assign 分区和 invariant 校验提供内部信号名。
        dict_output_map = rename_context.output_map  # output 内部信号命名关系。

        # output_affinity 继续为声明分组和 always 分组提供布局归属。
        dict_output_affinity = rename_context.output_affinity  # output 信号布局亲和信息。

        # 顶层 localparam 需要拆成 header 参数和保留在 body 中的 localparam。
        list_top_level_body_params = dict_body_items["localparams"]  # 顶层 body 参数候选。

        # body 参数分区输出非状态参数、状态参数和留在 body 的 localparam。
        tuple_top_level_params = self._partition_top_level_body_params(  # 顶层 body 参数分区结果。
            list_top_level_body_params  # 完成 rename 后仍位于 body 顶层的参数集合。
        )  # 顶层 body 参数分区三元组。

        # 非状态参数迁移到 header 参数块。
        list_top_level_body_nonstate_params = tuple_top_level_params[0]  # 待提升的普通参数。

        # 状态参数也迁移到 header，但使用独立展示区域。
        list_top_level_body_state_params = tuple_top_level_params[1]  # 待提升的状态参数。

        # 剩余 localparam 保留在 module body。
        list_top_level_local_params = tuple_top_level_params[2]  # renderer body 内保留参数。

        # body 顶层参数提升后必须接在原 header 参数之后，保证 module 声明集中展示配置。
        list_header_params = [  # 参数显示顺序按 parser header、body 普通参数、body 状态参数拼接。
            *list_header_params,  # parser header 参数保持原始阅读顺序。
            *list_top_level_body_nonstate_params,  # body 普通参数追加到 header 配置区。
            *list_top_level_body_state_params,  # body 状态参数追加到 header 状态区。
        ]  # renderer 后续按参数类别渲染的完整输入。

        # 顶层声明用于声明区分组。
        list_top_level_decls = dict_body_items["decls"]  # 顶层内部信号声明集合。

        # 顶层 assign 用于实例连接布局推断。
        list_top_level_assigns = dict_body_items["assigns"]  # 顶层 continuous assign 集合。

        # 顶层 always 用于过程块分组和状态机校验。
        list_top_level_always = dict_body_items["always"]  # 顶层 always 过程块集合。

        # 递归集合用于跨 generate/预处理结构的全局校验。
        list_all_local_params = self._collect_body_items_recursive(  # 状态机校验使用的全局 localparam。
            dict_body_items,  # 已完成重命名和 always 分裂的 body tree。
            "localparams",  # 参数分桶键。
        )  # 全 module localparam 集合。

        # 全局声明列表用于 output invariant 检查。
        list_all_decls = self._collect_body_items_recursive(  # output invariant 使用的全局声明。
            dict_body_items,  # 已完成重命名的声明分桶树。
            "decls",  # 声明分桶键。
        )  # 全 module 内部信号声明集合。

        # 全局 assign 列表覆盖 generate 或条件编译内的 output 写入。
        list_all_assigns = self._collect_body_items_recursive(  # output invariant 使用的跨层 assign。
            dict_body_items,  # generate 和条件编译内也可能含 assign。
            "assigns",  # 跨层 output 写入检查的赋值键。
        )  # output 成对声明检查覆盖的 continuous assign 集合。

        # 全局 always 列表用于 always header 和状态机校验。
        list_all_always_blocks = self._collect_body_items_recursive(  # always 头部校验使用的全局过程块。
            dict_body_items,  # 分裂后可检查目标信号的过程块树。
            "always",  # 状态机检查递归收集的过程块键。
        )  # 全 module always 过程块集合。

        # 校验 always 头部是否满足 formatter 可稳定处理的子集。
        self._validate_always_headers(list_all_always_blocks)

        # 校验 parser 原始 body 块结构是否仍可重排。
        self._validate_body_blocks(initial_state.body_blocks)

        # 声明分组综合端口亲和、output 派生和实例连接布局。
        list_grouped_decls = self._group_declarations(  # renderer 使用的声明分组。
            list_top_level_decls,  # 顶层声明保持 renderer 输出顺序。
            dict_output_affinity["signals"],  # output 内部信号集合参与声明归属。
            dict_output_affinity["layouts"],  # output 声明布局参与分组。
            self._collect_instance_signal_layouts(  # 实例连接布局参与声明靠近策略。
                list_top_level_decls,  # 实例布局分析读取顶层声明。
                list_top_level_assigns,  # assign 布局辅助识别连接信号。
                list_top_level_always,  # always 目标辅助识别时序信号归属。
                dict_body_items,  # generate/条件编译内连接也参与布局推断。
            ),
        )  # renderer 中声明区使用的分组声明。

        # always 分组让状态逻辑、组合逻辑和任务块按目标信号聚合。
        list_grouped_always = self._group_always(  # renderer 使用的 always 分组。
            list_top_level_always,  # 需要按目标信号聚类的顶层过程块。
            dict_output_affinity["targets"],  # output 目标信号用于靠近输出区域。
        )  # renderer 中过程块区域使用的分组 always。

        # 状态机校验依赖全局参数和分组后的 always 信息。
        self._validate_state_machine_blocks(
            list_all_local_params,  # 状态参数声明集合。
            list_grouped_always,  # 已按目标分组的 always 集合。
            list_all_always_blocks,  # 全局 always 用于检查跨块状态跳转。
        )

        # 全局 assign 分区用于最终 output 信号一致性校验。
        tuple_all_assigns = self._partition_assigns(  # output invariant 所需的 assign 分区。
            list_all_assigns,  # 可能写入 output 路径的所有 assign。
            list_normalized_ports,  # output 方向端口提供目标集合。
            dict_output_map["internal_names"],  # output 内部信号名也视为 output 相关。
        )  # 全 module assign 的 output 相关分区。

        # 全局 output assign 参与最终输出信号一致性校验。
        list_all_output_assigns = tuple_all_assigns[1]  # output 成对声明检查需要的写入语句。

        # output 声明、内部信号和 assign 必须满足成对关系。
        self._validate_output_signal_invariants(
            list_normalized_ports,  # 需要成对内部信号的 output 端口。
            list_all_decls,  # 可匹配 output 内部信号的声明全集。
            list_all_output_assigns,  # 写入 output 路径的连续赋值全集。
        )

        # renderer 使用分组后的声明和 always 替换顶层 body 对应分桶。
        dict_body_items["decls"] = list_grouped_decls  # 渲染时使用的顶层声明分组。

        # always 分组结果覆盖顶层过程块分桶，保持 renderer 只读最终结构。
        dict_body_items["always"] = list_grouped_always  # 渲染时使用的顶层 always 分组。

        # 最终上下文只保存 renderer 与位宽缓存必需字段。
        return RenderModuleContext(
            initial_state=initial_state,  # 渲染阶段复用 module 名和前置块。
            version=version,  # 文件头版本号由 normalize 阶段确定。
            include_header=include_header,  # 多 module 输出中的文件头开关。
            leading_comments=leading_comments,  # 当前片段的前导注释。
            header_metadata=header_metadata,  # 可选 Vivado 文件头字段。

            # renderer 主体输入包含参数、端口、body tree 和保留 localparam。
            header_params=list_header_params,  # 已合并提升参数的 header 参数表。
            normalized_ports=list_normalized_ports,  # 已完成渲染准备的端口列表。
            body_items=dict_body_items,  # 已完成分组和校验的 body tree。
            top_level_local_params=list_top_level_local_params,  # 留在 body 的顶层 localparam。

            # output 和位宽辅助数据只影响渲染布局，不改变 parser 结构。
            output_map=dict_output_map,  # output 内部信号映射表。
            output_affinity=dict_output_affinity,  # output 声明和 assign 的布局亲和表。
            all_decls=list_all_decls,  # 位宽类别缓存需要的全局声明。
        )

    # _format_single_module_text 串联单个 module 的解析、分析、重命名和渲染。
    def _format_single_module_text(
        self,
        # 源文本和版本号来自文件级 normalize 路由。
        source: str,
        version: str,
        *,
        # 头部开关和元数据只在多 module 首段启用。
        include_header: bool,
        leading_comments: list[str] | None = None,
        header_metadata: HeaderMetadata | None = None,
    ) -> str:
        """结构化格式化单个 Verilog module 文本。

        :param source: 单个 module 的 Verilog 源文本。
        :param version: 渲染文件头使用的版本号。
        :param include_header: 是否在本 module 输出前渲染文件头。
        :param leading_comments: 多 module 模式下跟随当前 module 的前导注释。
        :param header_metadata: 可选文件头元数据，只有首个 module 使用。
        :return: 渲染后的单个 module 文本。
        :raises VerilogFormatterError: 解析、验证或渲染阶段发现严格规则失败时抛出。
        """

        # 初始状态封装 parser、参数归一化和 body 分桶结果。
        initial_state = self._collect_initial_module_state(source)  # 单 module 初始状态。

        # 重命名阶段集中处理端口、参数、assign 和 body tree。
        rename_context = self._build_rename_module_context(initial_state)  # 单 module 重命名阶段产物。

        # 渲染上下文阶段负责分组、校验和 renderer 输入装配。
        render_context = self._build_render_module_context(  # renderer 所需最终上下文。
            initial_state,  # parser 和基础归一化阶段状态。
            rename_context,  # 完成 rename 的中间状态。
            version,  # 当前输出文件头版本号。
            include_header,  # 当前 module 是否输出文件头。
            leading_comments,  # 多 module 切片前导注释。
            header_metadata,  # 首个 module 可用的文件头元数据。
        )  # 单 module 最终渲染上下文。

        # 最终渲染阶段需要临时安装位宽分类缓存并保证退出时恢复。
        return self._render_module_with_width_context(render_context)

# 区域标签保留旧版类属性访问，供 renderer mixin 查询输出分区。
VerilogFormatterEngine.REGION_LABELS = constants.REGION_LABELS  # facade 兼容读取 banner 标签。

# 区域标题保留旧版类属性访问，供模块渲染生成中文分区标题。
VerilogFormatterEngine.REGION_TITLES = constants.REGION_TITLES  # facade 兼容读取 banner 标题。

# 行尾注释列宽由 renderer mixin 读取，保持端口和声明注释对齐。
VerilogFormatterEngine.TRAILING_COMMENT_COLUMN = constants.TRAILING_COMMENT_COLUMN  # renderer 对齐行尾说明的列号。

# 端口方向顺序控制 input、output、inout 的稳定排序。
VerilogFormatterEngine.PORT_DIRECTION_ORDER = constants.PORT_DIRECTION_ORDER  # input/output/inout 的排序权重。

# 已知协议族集合供端口分组推断识别 AXI、SPI 等接口。
VerilogFormatterEngine.KNOWN_PROTOCOL_KINDS = constants.KNOWN_PROTOCOL_KINDS  # 端口分组可识别的协议名集合。

# 配置参数标签帮助参数渲染区分通用配置和状态机配置。
VerilogFormatterEngine.KNOWN_CONFIG_PARAM_LABELS = constants.KNOWN_CONFIG_PARAM_LABELS  # 配置参数前缀到中文区域的表。

# 通用 token 让未知参数族仍能获得稳定的小节名称。
VerilogFormatterEngine.PARAM_FAMILY_GENERIC_TOKENS = constants.PARAM_FAMILY_GENERIC_TOKENS  # 参数族归类时忽略的泛化词。

# 通用端口小节标题用于非协议端口的渲染区域。
VerilogFormatterEngine.PORT_SECTION_LABELS = constants.PORT_SECTION_LABELS  # 非协议端口允许出现的中文分节名。

# 端口小节别名把常见英文或缩写归并到统一标题。
VerilogFormatterEngine.PORT_SECTION_ALIASES = constants.PORT_SECTION_ALIASES  # 英文端口分节标题的归一化别名。

# AXI 端口小节标题描述读写地址、数据和响应通道。
VerilogFormatterEngine.AXI_SECTION_LABELS = constants.AXI_SECTION_LABELS  # AXI 五通道到中文标题的映射。

# AXI 小节顺序保证地址、数据、响应通道按规范阅读顺序输出。
VerilogFormatterEngine.AXI_SECTION_ORDER = constants.AXI_SECTION_ORDER  # AXI AW/W/B/AR/R 的显示次序。

# AXI 成员顺序让 valid、ready、data 等信号在通道内稳定排列。
VerilogFormatterEngine.AXI_MEMBER_ORDER = constants.AXI_MEMBER_ORDER  # AXI 每个通道内部字段排位。

# AXIS 小节标题区分流数据和控制侧带信号。
VerilogFormatterEngine.AXIS_SECTION_LABELS = constants.AXIS_SECTION_LABELS  # AXIS payload 与握手控制分区。

# AXIS 小节顺序确保数据通道先于可选控制信号。
VerilogFormatterEngine.AXIS_SECTION_ORDER = constants.AXIS_SECTION_ORDER  # AXIS 时序、数据和控制的阅读顺序。

# AXIS 数据顺序让 tdata、tkeep、tlast 等成员保持阅读惯例。
VerilogFormatterEngine.AXIS_DATA_ORDER = constants.AXIS_DATA_ORDER  # AXIS tdata/tkeep/tlast 一类数据侧字段次序。

# AXIS 控制顺序覆盖 tvalid、tready 等握手信号。
VerilogFormatterEngine.AXIS_CONTROL_ORDER = constants.AXIS_CONTROL_ORDER  # AXIS valid/ready 等控制侧字段次序。

# APB 小节标题覆盖地址、写数据、读数据和响应信号。
VerilogFormatterEngine.APB_SECTION_LABELS = constants.APB_SECTION_LABELS  # APB 地址、数据和响应区域标题。

# APB 小节顺序保持低速总线端口的规范展示顺序。
VerilogFormatterEngine.APB_SECTION_ORDER = constants.APB_SECTION_ORDER  # APB setup/access 相关端口展示权重。

# APB 成员顺序用于 psel、penable、pready 等信号排序。
VerilogFormatterEngine.APB_MEMBER_ORDER = constants.APB_MEMBER_ORDER  # APB paddr/pwrite/pready 等成员权重。

# Wishbone 小节标题覆盖 classic 和 pipelined 接口信号族。
VerilogFormatterEngine.WISHBONE_SECTION_LABELS = constants.WISHBONE_SECTION_LABELS  # Wishbone classic/pipelined 分区标题。

# Wishbone 小节顺序让控制、地址、数据和响应段稳定输出。
VerilogFormatterEngine.WISHBONE_SECTION_ORDER = constants.WISHBONE_SECTION_ORDER  # Wishbone 控制、地址、数据段排位。

# Wishbone 成员顺序维护 cyc、stb、ack 等握手信号排列。
VerilogFormatterEngine.WISHBONE_MEMBER_ORDER = constants.WISHBONE_MEMBER_ORDER  # Wishbone cyc/stb/ack 等信号排位。

# UART 小节标题把串口收发、控制和状态信号分开。
VerilogFormatterEngine.UART_SECTION_LABELS = constants.UART_SECTION_LABELS  # UART 收发、配置和状态区域标题。

# UART 小节顺序让收发数据路径先于配置状态信号。
VerilogFormatterEngine.UART_SECTION_ORDER = constants.UART_SECTION_ORDER  # UART tx/rx/control 的展示权重。

# UART 成员顺序覆盖 tx、rx、baud 等常见串口信号。
VerilogFormatterEngine.UART_MEMBER_ORDER = constants.UART_MEMBER_ORDER  # UART tx、rx、baud 字段排位。

# SPI 小节标题区分片选、时钟和 MOSI/MISO 数据线。
VerilogFormatterEngine.SPI_SECTION_LABELS = constants.SPI_SECTION_LABELS  # SPI 片选、时钟和数据线区域标题。

# SPI 小节顺序保持片选、时钟和数据通路的阅读顺序。
VerilogFormatterEngine.SPI_SECTION_ORDER = constants.SPI_SECTION_ORDER  # SPI cs、sclk、mosi/miso 展示权重。

# SPI 成员顺序覆盖 sclk、mosi、miso、cs 等信号。
VerilogFormatterEngine.SPI_MEMBER_ORDER = constants.SPI_MEMBER_ORDER  # SPI 端口后缀在组内的稳定排位。

# I2C 小节标题区分串行数据、串行时钟和控制信号。
VerilogFormatterEngine.I2C_SECTION_LABELS = constants.I2C_SECTION_LABELS  # I2C scl/sda 与控制线区域标题。

# I2C 小节顺序让 scl/sda 相关端口聚在固定区域。
VerilogFormatterEngine.I2C_SECTION_ORDER = constants.I2C_SECTION_ORDER  # I2C 时钟、数据和控制段权重。

# I2C 成员顺序覆盖 scl、sda、oe 等双向总线信号。
VerilogFormatterEngine.I2C_MEMBER_ORDER = constants.I2C_MEMBER_ORDER  # I2C scl、sda、oe 成员权重。

# GMII 小节标题区分收发数据、控制和管理接口。
VerilogFormatterEngine.GMII_SECTION_LABELS = constants.GMII_SECTION_LABELS  # GMII 收发数据和管理接口标题。

# GMII 小节顺序保持以太网收发通道成组输出。
VerilogFormatterEngine.GMII_SECTION_ORDER = constants.GMII_SECTION_ORDER  # GMII tx/rx/management 分区权重。

# GMII 成员顺序把数据、使能和错误标志固定到常见阅读顺序。
VerilogFormatterEngine.GMII_MEMBER_ORDER = constants.GMII_MEMBER_ORDER  # GMII 数据和控制脚后缀排序表。

# RGMII 小节标题覆盖降引脚以太网收发通道。
VerilogFormatterEngine.RGMII_SECTION_LABELS = constants.RGMII_SECTION_LABELS  # RGMII 降引脚收发通道标题。

# RGMII 小节顺序让 DDR 收发控制段与数据段相邻展示。
VerilogFormatterEngine.RGMII_SECTION_ORDER = constants.RGMII_SECTION_ORDER  # RGMII DDR 端口分节显示顺序。

# RGMII 成员顺序把时钟、控制和 DDR 数据脚稳定排列。
VerilogFormatterEngine.RGMII_MEMBER_ORDER = constants.RGMII_MEMBER_ORDER  # RGMII DDR 端口成员排序表。

# 未知端口后缀帮助 fallback 聚类避免散乱排序。
VerilogFormatterEngine.UNKNOWN_CLUSTER_SUFFIXES = constants.UNKNOWN_CLUSTER_SUFFIXES  # fallback 聚类识别的常见信号后缀。

# 子分组阈值防止成员太少时生成噪声小节。
VerilogFormatterEngine.PORT_SUBGROUP_MIN_MEMBERS = constants.PORT_SUBGROUP_MIN_MEMBERS  # 端口子分组启用的最小成员数。

# tuple_public_engine_exports 固定 engine.py 对外承诺的 formatter 入口、异常和解析数据类，防止私有 helper 进入通配导入。
tuple_public_engine_exports: tuple[str, ...] = (  # 这些名称会原样复制到模块公开符号表，保持 import 星号语义只覆盖稳定 formatter API。
    "VerilogFormatterEngine",  # 格式化器门面入口。
    "VerilogFormatterError",  # formatter 统一异常类型。
    "ParamDecl",  # Verilog parameter/localparam 声明结构。
    "ParamRenderCluster",  # 参数区中文小节聚类结构。
    "PortDecl",  # Verilog input/output/inout 端口结构。
    "PortLayoutInfo",  # 端口分节和协议归属信息。
    "OutputSignalLayout",  # output 派生内部信号的摆放信息。
    "AssignSourceLayout",  # continuous assign 来源归属信息。
    "InstanceSignalLayout",  # 实例连接信号靠近策略信息。
    "SignalDecl",  # 内部 wire/reg 声明结构。
    "AssignStmt",  # continuous assign 语句结构。
    "BodyBlock",  # body 顶层块边界结构。
    "LValueRef",  # 过程赋值左值引用结构。
    "CaseItem",  # case 分支内容结构。
    "ControlNode",  # if/case 控制流树节点。
    "AlwaysBlock",  # always 块解析结果。
    "InstanceBlock",  # module 实例化解析结果。
    "GenerateBlock",  # generate/endgenerate 区域结构。
    "InitialBlock",  # 仿真初始化过程块结构。
    "FunctionBlock",  # Verilog function 声明体结构。
    "TaskBlock",  # 可含时序语句的 Verilog task 块结构。
    "RawBlock",  # parser 保留但 renderer 不改写的原文片段。
    "PreprocessorConditional",  # ifdef/ifndef 条件编译结构。
    "HeaderMetadata",  # Vivado 风格文件头字段容器。
)

# __all__ 使用 list 形态，保持旧版模块导出协议不变。
__all__ = list(tuple_public_engine_exports)  # Python wildcard import 的实际公开符号表。
