"""为 formatter backend 提供 Verilog 控制流和 body 区域解析辅助。"""

# 延迟解析类型注解，避免 mixin 之间的类型引用在导入期互相牵制。
from __future__ import annotations

# 标准库依赖只承担文本匹配、路径和时间字段兼容。
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

# formatter backend 内部工具保持横幅宽度和注释标题的既有算法。
from .banners import display_width, extract_banner_title, is_banner_line, make_banner

# 规则异常和 header 声明模型服务于 module 头部与参数端口解析。
from .models import (
    VerilogFormatterError,
    # 参数声明模型服务于 parameter/localparam 渲染。
    ParamDecl,
    ParamRenderCluster,
    # 端口布局模型服务于 module header 渲染。
    PortDecl,
    PortLayoutInfo,
    # 输出和实例布局模型由 render/analysis mixin 复用。
    OutputSignalLayout,
    AssignSourceLayout,
    InstanceSignalLayout,
)

# body 声明与赋值模型保持 formatter 后端对外字段不变。
from .models import (
    SignalDecl,
    AssignStmt,
    BodyBlock,
    LValueRef,
)

# 控制流块模型承载过程语句、generate 和预处理条件结构。
from .models import (
    CaseItem,
    ControlNode,
    # 过程块模型覆盖 always、initial、function 和 task。
    AlwaysBlock,
    InstanceBlock,
    GenerateBlock,
    InitialBlock,
    FunctionBlock,
    TaskBlock,
    # raw/preprocessor 模型保留无法结构化重写的 body 片段。
    RawBlock,
    PreprocessorConditional,
)

# header 元数据模型由顶层解析结果继续复用。
from .models import (
    HeaderMetadata,
)

# 源码读取入口复用 backend textio，避免在 mixin 内新增文件读取口径。
from .textio import read_verilog_text

# 控制节点解析继续委托现有 mixin，不引入第二套 Verilog parser。
from .control_node_parse_mixin import ControlNodeParseMixin

# ControlParseMixin 是 formatter backend 的 body 解析拼装层。
class ControlParseMixin(ControlNodeParseMixin):
    """解析 module body 中的声明、assign、always、generate 和实例化结构。"""

    # 控制解析错误统一在本 mixin 内补齐 current-project 错误前缀。
    def _raise_control_parse_error(self, category: str, statement: str, suggestion: str) -> None:
        """
        抛出带 current-project 前缀的 Verilog formatter 严格模式错误。

        :param category: 严格模式错误分类，保持原 formatter 诊断 code。
        :param statement: 触发错误的 Verilog 语句或源码片段。
        :param suggestion: 面向用户的修复建议文本。
        :return: 无业务返回值，本函数总是抛出异常。
        :raises VerilogFormatterError: 始终抛出带分类、摘要和建议的 formatter 错误。
        """

        # str_summary 保留 syntax_utils 中的源码摘要口径。
        str_summary = self._summarize_statement(statement)  # 严格模式错误中的 Verilog 片段摘要

        # 错误文本保留原 Strict mode 结构，同时满足 current-project 终端错误前缀。
        raise VerilogFormatterError(
            f"> ERR: [Python] Strict mode [{category}]: {str_summary}. Suggestion: {suggestion}"
        )

    # 条件分支识别需要兼容 `else if(` 和 `else if (` 两种源格式。
    def _is_else_if_header(self, text: str) -> bool:
        """
        判断一行文本是否为 else-if 控制头。

        :param text: 已截取的 Verilog 控制语句首行。
        :return: 文本表示 else-if 控制头时为 `True`。
        """

        # 条件分支 header 允许关键字后接空格或左括号，兼容压缩过的 Verilog 写法。
        return re.match(r"^else\s+if(?:\s|\()", text.strip()) is not None

    # 单个控制节点解析入口保持 case、if、for 和普通语句的优先级。
    def _parse_single_control_node(self, lines: list[str], start: int, context: str) -> tuple[list[ControlNode], int]:
        """
        从指定行开始解析一个过程控制节点。

        :param lines: 已按 Verilog 语句片段切分的源码行。
        :param start: 当前控制节点起始行下标。
        :param context: 调用方所在块类型，例如 `always` 或 `generate`。
        :return: 解析出的控制节点列表和本次消耗的行数。
        :raises VerilogFormatterError: 当单语句缺少顶层分号时抛出。
        """

        # str_stripped 是控制语句分发前的规范化首行。
        str_stripped = self._normalize_statement_line(lines[start].strip())  # 控制节点首行文本

        # case 节点由专用 parser 保留 selector 和 case item 层级。
        if str_stripped.startswith("case"):

            # tuple_case 保存解析出的 case 控制节点和消耗行数。
            tuple_case = self._parse_case_node(lines, start, context)  # case 分支树与消耗行数

            # 返回单节点列表，保持 _parse_control_nodes 的聚合接口不变。
            return [tuple_case[0]], tuple_case[1]

        # 条件分支节点由专用 parser 保留 children 和 alternate。
        if str_stripped.startswith("if ") or str_stripped.startswith("if(") or self._is_else_if_header(str_stripped):

            # tuple_if 保存解析出的条件节点和消耗行数。
            tuple_if = self._parse_if_node(lines, start, context)  # 条件分支节点与消耗行数

            # 返回单节点列表，供调用方继续顺序拼接。
            return [tuple_if[0]], tuple_if[1]

        # 循环节点需要保留 header 和内部控制节点。
        if str_stripped.startswith("for"):

            # tuple_loop 保存解析出的循环节点和消耗行数。
            tuple_loop = self._parse_loop_node(lines, start, context)  # for 循环节点与消耗行数

            # 返回单节点列表，保持控制节点列表 shape。
            return [tuple_loop[0]], tuple_loop[1]

        # str_statement 收集普通过程语句，可能跨多行直到顶层分号。
        str_statement, int_consumed = self._collect_statement_text(lines, start)  # 普通过程语句文本及消耗行数

        # 单语句控制节点必须以顶层分号闭合，避免吞掉后续结构。
        if not self._statement_has_top_level_semicolon(str_statement):

            # 缺少分号时沿用严格错误路径，不尝试猜测语句边界。
            self._raise_control_parse_error(
                "unsupported_shape",
                str_statement,
                "> ERR: [Python] Terminate each single statement with ';' before formatting.",
            )

        # 返回 statement 节点，供 always/initial/generate 统一渲染。
        return [ControlNode(kind="statement", text=str_statement)], int_consumed

    # always payload 构造集中保留 lvalue 分析与块模型分析顺序。
    def _build_always_payload(
        self,
        header: str,
        content_lines: list[str],
        pending_comments: list[str],
    ) -> AlwaysBlock:
        """
        根据 always 头和正文构造结构化 AlwaysBlock。

        :param header: always 触发头文本。
        :param content_lines: always 块内部的规范化正文行。
        :param pending_comments: 当前 always 前方收集到的 Verilog 注释。
        :return: 带触发信息、目标信号和控制节点的 AlwaysBlock。
        """

        # str_raw_block 把 header 和正文恢复成分析器需要的原始块文本。
        str_raw_block = "\n".join([header, *content_lines])  # always 分析使用的完整文本

        # list_lvalues 只通过现有 lvalue parser 提取赋值目标。
        list_lvalues = self._extract_lvalues_from_text(str_raw_block, "lvalue_normalization_violation")  # always 内左值集合

        # list_targets 保留唯一左值基名，维持 AlwaysBlock.targets 的既有含义。
        list_targets = self._collect_unique_lvalue_bases(list_lvalues)  # always 赋值目标基名列表

        # always_block_obj_payload 汇总触发沿、赋值目标和控制树，作为渲染阶段的过程块模型。
        always_block_obj_payload = self._analyze_always_block(  # 构造写入 always 与 blocks 的过程块对象
            header,  # always 触发头文本
            content_lines,  # always 控制节点解析正文行
            list_targets,  # always 目标信号基名
            str_raw_block,  # 完整块文本
            lvalues=list_lvalues,  # 解析后的左值对象
        )

        # leading comments 只挂到当前 always，不跨块泄漏。
        always_block_obj_payload.leading_comments = list(pending_comments)  # 当前 always 块前导注释

        # 返回保持原 AlwaysBlock wire shape 的 payload。
        return always_block_obj_payload

    # always 结构需要同时写入分类列表和 body 顺序列表。
    def _append_always_block(self, items: dict[str, list], payload: AlwaysBlock, raw_block: str) -> None:
        """
        把 always payload 同步追加到 body 分类桶和顺序块列表。

        :param items: `_parse_body` 正在构建的 body 分类字典。
        :param payload: 已解析的 AlwaysBlock。
        :param raw_block: 用于保持原始块顺序和文本的 always 源片段。
        :return: 无业务返回值，直接修改 `items`。
        """

        # always 列表保存结构化 payload，供后续分组和渲染复用。
        items["always"].append(payload)

        # blocks 列表保存 body 顺序，确保 formatter 输出顺序兼容旧后端。
        # str_block_kind 把内部 main_task 分类折回 formatter 需要的组合/时序类型。
        str_block_kind = (  # 顺序块里区分 always_comb、always_seq 或原始 block_kind
            payload.block_kind  # 非 main_task 时沿用分析器给出的块类型
            if payload.block_kind != "main_task"  # 只有 main_task 需要兼容旧分类
            else ("always_comb" if payload.is_combinational else "always_seq")  # main_task 映射为组合或时序 always
        )

        # obj_body_block 统一包装源码片段、payload 和调度元数据。
        obj_body_block = BodyBlock(  # 写入 blocks 队列的 always 顺序项
            str_block_kind,  # blocks 序列中使用的过程块分类
            raw_block,  # 用于保持源码相对顺序的原始片段
            payload,  # 渲染阶段复用的 always payload
            targets=payload.targets,  # 分组渲染需要的写目标集合
            trigger_kind=payload.trigger_kind,  # 区分组合与时序触发来源
            references_state=payload.references_state,  # 判断组合块是否读取状态
        )

        # blocks 顺序列表保留 module body 内的原始出现顺序。
        items["blocks"].append(obj_body_block)

    # body 主循环只负责顺序调度，具体 Verilog 形态由小 helper 处理。
    def _parse_body(self, body: str) -> dict[str, list]:
        """
        解析 module body 中的声明、过程块、实例和预处理结构。

        :param body: module body 原始文本，不包含 module header 与 endmodule。
        :return: 按类型分桶且保留源码顺序的 body 结构字典。
        """

        # dict_body_items 保存 formatter 后续阶段需要的全部 body 分类桶。
        dict_body_items = self._new_body_items()  # body 分类桶和顺序块列表

        # list_body_lines 保留原始行顺序，部分声明 handler 会向其中回插延迟语句。
        list_body_lines = body.splitlines()  # module body 原始行列表

        # int_line_index 指向当前待解析的 body 行。
        int_line_index = 0  # 当前扫描行下标

        # list_pending_comments 挂载到紧随其后的结构化 body block。
        list_pending_comments: list[str] = []  # 当前结构前导 Verilog 注释

        # 主循环按源码顺序调度每一类 body 行。
        while int_line_index < len(list_body_lines):

            # str_stripped 是当前行的轻量规范化入口键。
            str_stripped = list_body_lines[int_line_index].strip()  # 当前 body 行文本

            # tuple_ignored 处理空行、普通注释和块注释。
            tuple_ignored = self._consume_body_comment_or_blank(  # 空白注释消费状态
                list_body_lines,  # 待检查的 body 行序列
                int_line_index,  # 空白或注释候选行下标
                list_pending_comments,  # 已累计的行注释前缀链
            )  # 空白注释消费器状态三元组

            # 已消费的注释/空白行不再进入 Verilog 结构分发。
            if tuple_ignored[0]:

                # int_line_index 跳到注释消费器报告的下一个实质候选行。
                int_line_index = tuple_ignored[1]  # 注释/空白之后的下一行

                # list_pending_comments 继承或清空与注释边界相关的前导注释。
                list_pending_comments = tuple_ignored[2]  # 更新后的前导注释

                # 当前行已完成处理，主循环继续扫描后续 body 行。
                continue

            # tuple_handler 按固定优先级尝试处理当前 Verilog 行。
            tuple_handler = self._dispatch_body_line(  # body 结构分派状态
                dict_body_items,  # 正在填充的 module body 分类桶
                list_body_lines,  # 保留原顺序的 body 行序列
                int_line_index,  # 待分派的源码行下标
                str_stripped,  # 分派器使用的去空白行文本
                list_pending_comments,  # 等待绑定到结构块的注释链
            )  # body 行分派器状态三元组

            # 已识别的结构行会更新扫描位置和前导注释。
            if tuple_handler[0]:

                # int_line_index 采用命中 handler 已消费后的返回下标。
                int_line_index = tuple_handler[1]  # handler 返回的下一行下标

                # list_pending_comments 同步 handler 对结构前导注释的清理结果。
                list_pending_comments = tuple_handler[2]  # handler 清理或保留后的注释

                # 当前结构已经入桶，主循环不再走未知语句兜底。
                continue

            # strict 模式下未知 body 行必须显式报错，避免静默丢源码。
            if self.config["strict_mode"]["fail_on_unsupported_syntax"]:

                # 抛出包含源码片段的严格模式错误，提示用户改写为受支持结构。
                self._raise_control_parse_error(
                    "unsupported_construct",
                    str_stripped,
                    "> ERR: [Python] Move this statement into a supported declaration, assign, always block, or "
                    "instance block.",
                )

            # 非 strict 模式跳过未知行，同时清理前导注释避免误挂到后续结构。
            list_pending_comments = []  # 未识别行之后不保留悬挂注释

            # 扫描继续推进到下一行。
            int_line_index += 1  # 未识别 body 行后的下一行下标

        # 返回完整 body 分桶结果给 module parser。
        return dict_body_items

    # 空白行、行注释和块注释由主循环最先消费。
    def _consume_body_comment_or_blank(
        self,
        list_body_lines: list[str],
        int_line_index: int,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        消费 body 扫描中的空行、单行注释和块注释。

        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已消费、下一行下标和更新后的前导注释。
        """

        # str_stripped 提供空白、行注释和块注释识别的当前行文本。
        str_stripped = list_body_lines[int_line_index].strip()  # 注释/空行预处理使用的候选文本

        # 空行切断前导注释链，防止跨结构误挂。
        if not str_stripped:

            # 空行不产生结构节点，返回清空后的前导注释状态。
            return True, int_line_index + 1, []

        # 普通行注释在遇到非 banner 时挂到下一条结构上。
        if str_stripped.startswith("//"):

            # banner 注释表示视觉分隔，不作为结构前导注释继承。
            if is_banner_line(str_stripped):

                # banner 仅作为源码分隔符消费，不绑定到后续 Verilog 结构。
                return True, int_line_index + 1, []

            # list_next_comments 复制后追加，避免调用方列表被隐式共享。
            list_next_comments = [*list_pending_comments, str_stripped]  # 更新后的前导注释链

            # 返回已消费状态，并把该行注释挂到下一条结构前。
            return True, int_line_index + 1, list_next_comments

        # 块注释整体跳过，当前 formatter 暂不将其绑定到结构模型。
        if str_stripped.startswith("/*"):

            # int_next_index 从块注释下一行开始查找闭合标记。
            int_next_index = int_line_index + 1  # 块注释扫描下标

            # 扫描直到找到 `*/` 或到达 body 末尾。
            while int_next_index < len(list_body_lines):

                # 发现块注释闭合后消费到闭合行之后。
                if "*/" in list_body_lines[int_next_index]:

                    # 块注释整体跳过，不形成 body 结构。
                    return True, int_next_index + 1, []

                # 块注释未闭合前继续向后扫描。
                int_next_index += 1  # 块注释继续扫描的下一行下标

            # 未闭合块注释消费到文件末尾，保持旧逻辑的宽容行为。
            return True, int_next_index, []

        # 当前行不是注释或空白，交给结构 handler。
        return False, int_line_index, list_pending_comments

    # body 行调度按原 `_parse_body` 分支顺序逐类尝试。
    def _dispatch_body_line(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        按原解析优先级分派一行 Verilog body 文本。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param str_stripped: 当前行去除首尾空白后的文本。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已处理、下一行下标和更新后的前导注释。
        """

        # tuple_raw_result 处理 specify 与预处理相关原始块。
        tuple_raw_result = self._handle_raw_or_preprocessor_body_line(  # 原始块分支状态
            dict_body_items,  # raw/preprocessor 写入的 body 分类桶
            list_body_lines,  # specify 或反引号块所在源码行
            int_line_index,  # raw/preprocessor 候选起始下标
            str_stripped,  # raw/preprocessor 候选文本
            list_pending_comments,  # raw block 可能继承的前导注释
        )  # 原始块或预处理行处理结果

        # 命中原始块或预处理行后直接返回。
        if tuple_raw_result[0]:

            # raw/preprocessor handler 已写入 body 桶，返回它给出的扫描出口。
            return tuple_raw_result

        # tuple_structural_result 先处理会跨多行保存源码边界的结构块。
        tuple_structural_result = self._handle_structural_body_line(  # 返回命中状态、下一下标和注释链
            dict_body_items,  # 结构分支写入的 body 分类桶
            list_body_lines,  # 结构块收集时使用的源码行
            int_line_index,  # 结构关键字所在行下标
            str_stripped,  # 结构关键字候选文本
            list_pending_comments,  # 结构块前导注释链
        )  # generate/function/task/initial 分支返回状态

        # 命中结构化过程块后直接返回。
        if tuple_structural_result[0]:

            # generate/function/task/initial 已完成入桶，返回对应边界。
            return tuple_structural_result

        # tuple_declaration_result 处理 parameter、genvar 和信号声明。
        tuple_declaration_result = self._handle_declaration_body_line(  # 声明分支状态
            dict_body_items,  # 参数和信号声明写入桶
            list_body_lines,  # 声明收集器读取的源码行
            int_line_index,  # 声明候选行下标
            str_stripped,  # 声明候选文本
            list_pending_comments,  # 声明前导注释链
        )  # 声明类 body 行处理结果

        # 命中声明行后直接返回。
        if tuple_declaration_result[0]:

            # 声明 handler 可能消费多行声明，返回其推进后的行号。
            return tuple_declaration_result

        # tuple_assign_result 捕获 continuous assign 并保留 delay、lhs、rhs。
        tuple_assign_result = self._handle_assign_body_line(  # continuous assign 分支状态
            dict_body_items,  # assign 语句写入桶
            list_body_lines,  # assign 收集器读取的源码行
            int_line_index,  # assign 候选行下标
            str_stripped,  # continuous assign 入口候选文本
            list_pending_comments,  # continuous assign 专属前导注释链
        )  # continuous assign 的处理状态和出口

        # 命中 assign 行后直接返回。
        if tuple_assign_result[0]:

            # assign handler 已保留左右值与原始文本，返回消费边界。
            return tuple_assign_result

        # tuple_always_result 处理 always 过程块。
        tuple_always_result = self._handle_always_body_line(  # 过程块分支状态
            dict_body_items,  # always 过程块写入桶
            list_body_lines,  # 过程块收集器读取的源码行
            int_line_index,  # always 关键字候选下标
            str_stripped,  # always 关键字所在行文本
            list_pending_comments,  # 过程块前导注释链
        )  # always 过程块的处理状态和出口

        # 命中 always 块后直接返回。
        if tuple_always_result[0]:

            # always handler 已同步分类桶与顺序块，返回块尾之后的位置。
            return tuple_always_result

        # 实例声明放在最后，避免误吞控制语句或声明语句。
        return self._handle_instance_body_line(
            dict_body_items,
            list_body_lines,
            int_line_index,
            str_stripped,
            list_pending_comments,
        )

    # 原始块和预处理行保持旧解析顺序，避免影响后续声明识别。
    def _handle_raw_or_preprocessor_body_line(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 specify、include/define、条件编译和其它反引号行。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param str_stripped: 当前行去除首尾空白后的文本。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已处理、下一行下标和更新后的前导注释。
        """

        # specify 块作为 raw block 保留，不参与声明或过程块重写。
        if str_stripped.startswith("specify"):

            # tuple_raw_block 保存 specify 块 payload 和下一行下标。
            tuple_raw_block = self._parse_raw_block(  # specify 块范围状态
                list_body_lines,  # specify 所在 body 行序列
                int_line_index,  # specify 起始行下标
                "endspecify",  # specify 终止关键字
                "specify block",  # specify 错误标签
            )  # specify 原始块解析结果

            # payload_raw_block 承载 specify 的原始行和前导注释。
            payload_raw_block = tuple_raw_block[0]  # specify 原始块模型

            # specify 前导注释只附着到当前 raw block。
            payload_raw_block.leading_comments = list(list_pending_comments)  # specify 前导注释副本

            # raw_blocks 分类保留 specify 结构供报告使用。
            dict_body_items["raw_blocks"].append(payload_raw_block)

            # blocks 顺序列表保留 specify 在 body 中的位置。
            dict_body_items["blocks"].append(
                BodyBlock("specify_block", "\n".join(payload_raw_block.lines), payload_raw_block)
            )

            # specify 之后清理前导注释。
            return True, tuple_raw_block[1], []

        # include/define 属于 module body 前置预处理行。
        if str_stripped.startswith(("`include", "`define")):

            # payload_preprocessor 记录单行预处理指令和前导注释。
            payload_preprocessor = RawBlock(  # include/define 单行原始块
                lines=[str_stripped],  # include/define 指令文本
                leading_comments=list(list_pending_comments),  # 预处理指令前导注释
            )  # include/define 原始行模型

            # preprocessor_prologue 保留这些指令在渲染前导区。
            dict_body_items["preprocessor_prologue"].append(payload_preprocessor)

            # 单行预处理指令消费当前行。
            return True, int_line_index + 1, []

        # 条件编译块需要递归解析 true/false body。
        if str_stripped.startswith(("`ifdef", "`ifndef")):

            # tuple_conditional 保存条件编译模型和下一行下标。
            tuple_conditional = self._parse_preprocessor_conditional(  # 条件编译分支状态
                list_body_lines,  # 条件编译所在 body 行序列
                int_line_index,  # 条件编译起始行下标
                list_pending_comments,  # 条件编译前导注释
            )  # 条件编译解析结果

            # conditionals 分类保留两侧分支结构。
            dict_body_items["conditionals"].append(tuple_conditional[0])

            # 条件编译块消费到 endif 后一行。
            return True, tuple_conditional[1], []

        # 其它反引号行无法结构化解析，作为 raw preprocessor block 保留。
        if str_stripped.startswith("`"):

            # payload_raw_preprocessor 保存未知预处理行的原始文本。
            payload_raw_preprocessor = RawBlock(  # 未知反引号指令原始块
                lines=[str_stripped],  # 未知反引号指令文本
                leading_comments=list(list_pending_comments),  # 未知指令前导注释
            )  # 未分类预处理行模型

            # raw_blocks 记录原始预处理行，便于后续分析报告展示。
            dict_body_items["raw_blocks"].append(payload_raw_preprocessor)

            # blocks 顺序列表保留该预处理行的位置。
            dict_body_items["blocks"].append(
                BodyBlock("raw_preprocessor", str_stripped, payload_raw_preprocessor)
            )

            # 单行预处理结构消费当前行。
            return True, int_line_index + 1, []

        # 未命中 raw/preprocessor 类别，交给下一类 handler。
        return False, int_line_index, list_pending_comments

    # generate、function、task 和 initial 都属于结构化过程块。
    def _handle_structural_body_line(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 generate、function、task、initial 和不支持的结构关键字。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param str_stripped: 当前行去除首尾空白后的文本。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已处理、下一行下标和更新后的前导注释。
        """

        # 非结构关键字行继续交给声明或过程块 handler。
        if not str_stripped.startswith(
            ("generate", "endgenerate", "initial", "task ", "function ", "endtask", "endfunction")
        ):

            # 当前行不是结构关键字，交给声明、assign、always 或实例分支继续判断。
            return False, int_line_index, list_pending_comments

        # generate 块需要先抽出内部语句再交给控制节点 parser。
        if str_stripped.startswith("generate"):

            # tuple_generate_block 锁定 generate/endgenerate 的源码范围和扫描出口。
            tuple_generate_block = self._collect_generate_block(list_body_lines, int_line_index)  # generate 外壳源码边界

            # list_inner_lines 是去掉外层 generate/endgenerate 后的正文。
            list_inner_lines = self._extract_generate_inner_lines(tuple_generate_block[0])  # generate 内部源码行

            # tuple_nodes 保存 generate 内部控制树和已消费源码行数。
            tuple_nodes = self._parse_control_nodes(list_inner_lines, 0, set(), "generate")  # generate 内控制树和消费数

            # 消费行数必须覆盖全部 generate 内部行。
            if tuple_nodes[1] != len(list_inner_lines):

                # generate 内仍有未消费语句时，拒绝输出不完整控制树。
                self._raise_control_parse_error(
                    "unsupported_generate_shape",
                    "\n".join(tuple_generate_block[0]),
                    "> ERR: [Python] Simplify the generate body so the builtin backend can normalize it safely.",
                )

            # payload_generate 承载 generate 内部源码、控制节点和前导注释。
            payload_generate = GenerateBlock(  # generate 控制块模型
                lines=list_inner_lines,  # 去掉外壳后的 generate 源码行
                nodes=tuple_nodes[0],  # generate 内控制节点列表
                leading_comments=list(list_pending_comments),  # generate 前导 Verilog 注释
            )  # generate 块渲染模型

            # generates 分类用于后续 generate 专门渲染。
            dict_body_items["generates"].append(payload_generate)

            # blocks 顺序列表保留 generate 块原始位置。
            dict_body_items["blocks"].append(
                BodyBlock("generate_block", "\n".join(tuple_generate_block[0]), payload_generate)
            )

            # generate 块消费到 endgenerate 后一行。
            return True, tuple_generate_block[1], []

        # function 声明保持原始源码行，避免改写用户函数体内部格式。
        if str_stripped.startswith("function "):

            # tuple_function 返回保真函数源码容器和 endfunction 后的继续扫描位置。
            tuple_function = self._parse_function_block(list_body_lines, int_line_index)  # function 保真容器与出口下标

            # payload_function 只承载 function 声明体文本，不展开内部控制语句。
            payload_function = tuple_function[0]  # function 保真源码容器

            # function 前导注释用于描述函数声明，不能漂移到后续 task 或实例。
            payload_function.leading_comments = list(list_pending_comments)  # function 声明前注释

            # functions 分类让渲染层按 function 语法原样回放。
            dict_body_items["functions"].append(payload_function)

            # blocks 顺序列表记录 function 在 module body 中的出现位置。
            dict_body_items["blocks"].append(
                BodyBlock("function_block", "\n".join(payload_function.lines), payload_function)
            )

            # function 分支消费到 endfunction 后一行，并清空已使用的前导注释。
            return True, tuple_function[1], []

        # task 声明同样以源码行容器保存，避免破坏用户任务体。
        if str_stripped.startswith("task "):

            # tuple_task 返回 task 回放容器和 endtask 后的继续扫描位置。
            tuple_task = self._parse_task_block(list_body_lines, int_line_index)  # task 回放容器与出口下标

            # payload_task 保存 task 声明体文本，避免重排任务内部时序语句。
            payload_task = tuple_task[0]  # task 任务体回放容器

            # task 前导注释说明任务用途，不能挂到随后实例声明。
            payload_task.leading_comments = list(list_pending_comments)  # 仅绑定当前 task 的说明注释

            # tasks 分类保留任务块，输出阶段按 task/endtask 回放。
            dict_body_items["tasks"].append(payload_task)

            # task 顺序块保留任务体在 module body 中的回放位置。
            dict_body_items["blocks"].append(BodyBlock("task_block", "\n".join(payload_task.lines), payload_task))

            # 任务体源码已经完整收集，扫描器跳到 endtask 之后。
            return True, tuple_task[1], []

        # initial 块会解析出控制节点或 legacy 参数检查块。
        if str_stripped.startswith("initial"):

            # tuple_initial 返回控制节点化 initial 或 legacy 参数检查块。
            tuple_initial = self._parse_initial_block(  # initial 解析状态
                list_body_lines,  # initial 块边界扫描使用的源码行
                int_line_index,  # initial 关键字行下标
                allow_parameter_check=self._example_compat_enabled(),  # legacy 参数检查兼容开关
            )  # initial 模型和闭合后的扫描出口

            # payload_initial 保存 initial 头、正文和参数检查兼容分类。
            payload_initial = tuple_initial[0]  # initial 过程块模型

            # initial 前导注释描述当前过程块，不传递到后续声明。
            payload_initial.leading_comments = list(list_pending_comments)  # initial 块前注释

            # initials 分类让后续渲染器识别参数检查块和普通 initial。
            dict_body_items["initials"].append(payload_initial)

            # str_initial_raw_text 组合 header 和正文，维持旧 blocks 文本形态。
            str_initial_raw_text = "\n".join([payload_initial.header, *payload_initial.lines])  # initial 完整源码文本

            # blocks 顺序列表保留 initial 块位置和分类。
            dict_body_items["blocks"].append(
                BodyBlock(f"{payload_initial.block_kind}_block", str_initial_raw_text, payload_initial)
            )

            # initial 分支消费到解析函数报告的块尾后一行。
            return True, tuple_initial[1], []

        # strict 模式下裸 endtask/endfunction/endgenerate 等结构关键字需要报错。
        if self.config["strict_mode"]["fail_on_unsupported_syntax"]:

            # 裸闭合关键字说明 body 结构不平衡，严格模式直接阻断。
            self._raise_control_parse_error(
                "unsupported_construct",
                str_stripped,
                "> ERR: [Python] Remove the unsupported construct or handle this file manually.",
            )

        # 非 strict 模式跳过不支持结构关键字。
        return True, int_line_index + 1, []

    # 声明类 body 行包括 parameter/localparam、genvar 和信号声明。
    def _handle_declaration_body_line(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 body 中的参数声明、genvar 和信号声明。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param str_stripped: 当前行去除首尾空白后的文本。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已处理、下一行下标和更新后的前导注释。
        """

        # parameter/localparam 可能跨多行，需要先收集完整声明语句。
        if str_stripped.startswith(("parameter", "localparam")):

            # tuple_declaration 保存声明原文、尾注释和下一行下标。
            tuple_declaration = self._collect_declaration_statement(  # 返回参数源码、尾注和下一行下标
                list_body_lines,  # body 声明收集源码行
                int_line_index,  # 参数声明起始行下标
            )  # 参数声明收集结果

            # list_parsed_params 是拆分后的参数声明模型。
            list_parsed_params = self._parse_param_decls(  # 拆分后的参数声明模型列表
                tuple_declaration[0],  # 参数声明完整源码
                tuple_declaration[1],  # 参数声明行尾注释
                allow_trailing_comma=False,  # body 参数声明不接受尾逗号
                allow_trailing_semicolon=True,  # body 参数声明保留尾分号兼容
            )  # 参数声明解析结果

            # 每个参数声明都写入 localparams 和顺序 blocks。
            for int_param_index, parsed_param in enumerate(list_parsed_params):

                # payload_param 复制参数字段并只给第一项挂前导注释。
                payload_param = ParamDecl(  # 保存当前参数关键字、名称、右值和注释
                    parsed_param.keyword,  # 参数声明关键字
                    parsed_param.name,  # 参数声明名称
                    parsed_param.value,  # 参数右值表达式
                    parsed_param.decl_spec,  # 参数类型或位宽声明片段
                    parsed_param.comment,  # 参数行尾 Verilog 注释
                    list(list_pending_comments) if int_param_index == 0 else [],  # 首个参数继承前导注释
                )  # 参数声明 body 模型

                # localparams 分类沿用旧命名，兼容 parameter 与 localparam。
                dict_body_items["localparams"].append(payload_param)

                # blocks 顺序列表保留参数声明位置。
                dict_body_items["blocks"].append(BodyBlock("declaration", tuple_declaration[0], payload_param))

            # 参数声明消费到收集器返回的下一行。
            return True, tuple_declaration[2], []

        # genvar 声明使用 SignalDecl 表示，便于声明渲染路径复用。
        if re.match(r"^genvar\b", str_stripped):

            # tuple_genvar_parts 分离 genvar 声明和行尾注释。
            tuple_genvar_parts = self._split_comment(str_stripped)  # genvar 声明主体与行尾注释

            # match_genvar 捕获单个 genvar 名称。
            match_genvar = re.search(r"genvar\s+(\w+)\s*;$", tuple_genvar_parts[0])  # genvar 单名称匹配结果

            # 只有稳定单声明形态才写入结构化声明。
            if match_genvar:

                # payload_genvar 使用 SignalDecl 保存 genvar 名称和前导注释。
                payload_genvar = SignalDecl(  # 保存当前 genvar 名称并复用声明输出
                    "genvar",  # 声明类别固定为 genvar
                    "",  # genvar 不携带 packed 位宽
                    match_genvar.group(1),  # genvar 声明名称
                    "",  # genvar 不携带初始化表达式
                    tuple_genvar_parts[1],  # genvar 行尾注释
                    False,  # genvar 不参与 signed 标记
                    "",  # genvar 没有方向修饰
                    "",  # genvar 没有属性前缀
                    "",  # genvar 没有额外声明前缀
                    list(list_pending_comments),  # 仅附加到当前 genvar 的注释副本
                )  # genvar 声明模型

                # decls 分类保留 genvar 声明。
                dict_body_items["decls"].append(payload_genvar)

                # blocks 顺序列表保留 genvar 声明位置。
                dict_body_items["blocks"].append(BodyBlock("declaration", str_stripped, payload_genvar))

            # genvar 行无论是否成功结构化都消费当前行。
            return True, int_line_index + 1, []

        # 普通信号声明和属性声明交给声明 parser。
        if re.match(r"^(?:wire|reg|logic|integer|real)\b", str_stripped) or str_stripped.startswith("(*"):

            # tuple_signal_declaration 保存完整声明原文、注释和下一行下标。
            tuple_signal_declaration = self._collect_declaration_statement(  # 信号声明完整文本、尾注和扫描出口
                list_body_lines,  # 声明可能跨行时读取的 body 行
                int_line_index,  # 信号声明起始行下标
            )  # 信号声明收集结果

            # list_deferred_statements 保存从属性拆分出的非声明语句。
            list_deferred_statements: list[str] = []  # 需要回插到 body 扫描队列的语句

            # 拆分多声明语句，逐项解析真正的信号声明。
            for int_statement_index, str_statement in enumerate(
                self._split_declaration_statements(tuple_signal_declaration[0])
            ):

                # tuple_attribute_parts 去掉声明前属性后判断实际声明类型。
                tuple_attribute_parts = self._extract_leading_attributes(str_statement)  # 属性前缀与裸声明主体

                # 非信号声明回插到后续 body 扫描中。
                if not re.match(r"^(?:wire|reg|logic|integer|real)\b", tuple_attribute_parts[1]):

                    # 属性包裹的非声明语句留给后续 body 分支重新识别。
                    list_deferred_statements.append(str_statement)

                    # 当前拆分项已移交延迟队列，不再尝试声明解析。
                    continue

                # list_signal_payloads 是当前声明语句拆出的一个或多个信号。
                list_signal_payloads = self._parse_signal_decl(  # 当前声明语句拆出的信号模型
                    str_statement,  # 当前拆分出的声明语句
                    tuple_signal_declaration[1] if int_statement_index == 0 else "",  # 首个语句继承尾注释
                )  # 信号声明解析结果

                # 每个信号声明都写入 decls 和 blocks。
                for int_payload_index, payload_signal in enumerate(list_signal_payloads):

                    # 第一条声明继承 Verilog 前导注释，其余同语句声明不重复挂载。
                    payload_signal.leading_comments = (
                        list(list_pending_comments)  # 第一条结构化声明继承前导注释
                        if int_statement_index == 0 and int_payload_index == 0  # 仅首个拆分语句的首个信号使用
                        else []  # 同语句后续信号不重复挂注释
                    )  # 信号声明前导注释

                    # decls 分类保留信号声明模型。
                    dict_body_items["decls"].append(payload_signal)

                    # str_block_type 标记 inline wire 初始化是否需要后续重写。
                    str_block_type = (
                        "inline_wire_assign_rewrite"  # inline wire 初始化需要 assign 重写路径
                        if payload_signal.kind == "wire" and payload_signal.init  # wire 声明内含初始化表达式
                        else "declaration"  # 普通声明保持 declaration 顺序块
                    )  # 信号声明在顺序块中的类型

                    # list_targets 仅对 inline 初始化声明暴露目标信号。
                    list_targets = [payload_signal.name] if payload_signal.init else []  # inline 初始化目标信号列表

                    # blocks 顺序列表保留声明语句位置。
                    dict_body_items["blocks"].append(
                        BodyBlock(str_block_type, str_statement, payload_signal, targets=list_targets)
                    )

            # 延迟语句插回扫描队列，保持原来处理顺序。
            if list_deferred_statements:

                # 延迟语句插入到当前声明之后，让主循环继续按原顺序识别。
                list_body_lines[tuple_signal_declaration[2] : tuple_signal_declaration[2]] = list_deferred_statements  # 回插延迟语句

            # 信号声明消费到收集器返回的下一行。
            return True, tuple_signal_declaration[2], []

        # 当前行不是声明类 body 行。
        return False, int_line_index, list_pending_comments

    # continuous assign 单独处理，避免被实例识别误吞。
    def _handle_assign_body_line(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 continuous assign 语句。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param str_stripped: 当前行去除首尾空白后的文本。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已处理、下一行下标和更新后的前导注释。
        """

        # 非 assign 行继续交给 always 或实例 handler。
        if not str_stripped.startswith("assign "):

            # 当前行不是 continuous assign，保留原扫描位置给后续 handler。
            return False, int_line_index, list_pending_comments

        # tuple_assign_declaration 保存完整 assign 原文、注释和下一行下标。
        tuple_assign_declaration = self._collect_declaration_statement(  # continuous assign 完整文本、尾注和出口
            list_body_lines,  # 赋值语句可能跨行时读取的 body 行
            int_line_index,  # assign 关键字所在行
        )  # continuous assign 收集结果

        # match_assign 捕获 formatter 需要拆开的 delay、lhs 和 rhs 片段。
        match_assign = re.search(  # continuous assign 字段捕获结果
            r"assign\s+(?P<delay>#\s*(?:\([^)]*\)|\S+)\s+)?(?P<lhs>.+?)\s*=\s*(?P<rhs>.+?);$",  # delay/lhs/rhs 提取表达式
            tuple_assign_declaration[0],  # continuous assign 完整源码
        )  # continuous assign 正则匹配

        # 可解析 assign 才进入结构化 assign 列表。
        if match_assign:

            # str_lhs 保留 assign 左值文本，稍后用于目标信号提取。
            str_lhs = match_assign.group("lhs").strip()  # continuous assign 左侧表达式

            # l_value_ref_assign 解析左值，用于提取 target 基名并校验 concat 写法。
            l_value_ref_assign: LValueRef = self._parse_lvalue(  # continuous assign 左值结构
                str_lhs,  # 待解析的 assign 左侧文本
                "lvalue_normalization_violation",  # 左值解析失败分类
                "Use a stable assign left-hand side such as foo, foo[idx], foo[msb:lsb], or {foo, bar}.",  # 左值修复建议
                allow_concat=True,  # continuous assign 允许拼接左值
            )  # continuous assign 左值模型

            # payload_assign 承载 assign 左右值、注释和 delay。
            payload_assign = AssignStmt(  # continuous assign 语义模型
                str_lhs,  # 写目标表达式文本
                match_assign.group("rhs").strip(),  # 需要保持表达式顺序的驱动源
                tuple_assign_declaration[1],  # 原 assign 行尾说明
                list(list_pending_comments),  # 仅附着到当前 assign 的前导注释
                delay=(match_assign.group("delay") or "").strip(),  # assign delay 控制片段
            )  # continuous assign 结构模型

            # assigns 分类保留 continuous assign。
            dict_body_items["assigns"].append(payload_assign)

            # blocks 顺序列表保留 assign 的原始位置和目标信号。
            dict_body_items["blocks"].append(
                BodyBlock(
                    "continuous_assign",
                    tuple_assign_declaration[0],
                    payload_assign,
                    targets=self._extract_lvalue_bases(l_value_ref_assign),
                )
            )

        # assign 行消费到声明收集器返回的下一行。
        return True, tuple_assign_declaration[2], []

    # always 块解析保持原先 single-statement、lookahead 和 begin/end 三条路径。
    def _handle_always_body_line(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 always 过程块并写入 body 分类字典。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param str_stripped: 当前行去除首尾空白后的文本。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已处理、下一行下标和更新后的前导注释。
        """

        # 非 always 行继续交给实例 handler。
        if not str_stripped.startswith("always"):

            # 当前行不是过程块入口，调度器继续尝试后续 handler。
            return False, int_line_index, list_pending_comments

        # tuple_header_parts 拆出 always 头和同行剩余正文。
        tuple_header_parts = self._split_always_header(str_stripped)  # always 头与同行余量

        # str_normalized_remainder 是同行 always 单语句或 begin 片段。
        str_normalized_remainder = (
            self._normalize_statement_line(tuple_header_parts[1])  # always 同行余量的规范化文本
            if tuple_header_parts[1]  # always 头后存在正文片段
            else ""  # always 头后无正文时等待下一实质行
        )  # always 头后的规范化正文

        # 同行正文不是 begin 时走单控制节点路径。
        if str_normalized_remainder and not self._is_begin_header(str_normalized_remainder):

            # tuple_inline_result 保存同行 always handler 的消费结果。
            tuple_inline_result = self._handle_inline_always_body(  # 同行 always 单语句状态
                dict_body_items,  # 同行 always 写入的 body 分类桶
                list_body_lines,  # 同行 always 后续候选源码行
                int_line_index,  # 同行 always 起始下标
                tuple_header_parts[0],  # 同行 always 触发头
                str_normalized_remainder,  # 同行 always 单语句正文
                list_pending_comments,  # 同行 always 前导注释
            )  # 同行 always 解析结果

            # 返回同行 always 已消费的行号区间。
            return tuple_inline_result

        # int_lookahead 跳过 always 头之后的空行或注释。
        int_lookahead = self._skip_ignorable_control_lines(list_body_lines, int_line_index + 1)  # always 后首个正文候选下标

        # str_next_control 是下一条实质控制语句。
        str_next_control = (
            self._normalize_statement_line(list_body_lines[int_lookahead].strip())  # lookahead 行的规范化控制文本
            if int_lookahead < len(list_body_lines)  # lookahead 仍在 body 范围内
            else ""  # always 头后没有可用正文行
        )  # always 下一条控制语句

        # always 头下一行若是单语句控制节点，则不需要 begin/end 收集。
        if not str_normalized_remainder and str_next_control and not self._is_begin_header(str_next_control):

            # tuple_following_result 保存下一行单语句 always 的消费结果。
            tuple_following_result = self._handle_following_single_always_body(  # 跨行 always 单语句状态
                dict_body_items,  # 跨行单语句 always 写入桶
                list_body_lines,  # always 头之后的源码行序列
                int_lookahead,  # always 正文首个实质行
                tuple_header_parts[0],  # 跨行 always 触发头
                list_pending_comments,  # 跨行 always 前导注释
            )  # 下一行单语句 always 解析结果

            # 返回跨行单语句 always 的解析边界。
            return tuple_following_result

        # begin/end 形态 always 需要按深度收集完整块。
        tuple_block_result = self._handle_block_always_body(  # 返回块状 always 的命中状态和闭合下标
            dict_body_items,  # 块状 always 写入的 body 分类桶

            # 源码边界参数决定 begin/end 深度扫描范围。
            list_body_lines,  # 深度扫描使用的 body 源码行
            int_line_index,  # begin/end always 起始下标
            str_stripped,  # begin/end always 首行文本

            # always 语义参数用于构造最终 AlwaysBlock。
            tuple_header_parts[0],  # 当前块状 always 的触发头
            str_normalized_remainder,  # always 头后的 begin 片段
            list_pending_comments,  # 仅附着到块状 always 的说明注释
        )  # begin/end always 解析结果

        # 返回 begin/end always 的闭合行边界。
        return tuple_block_result

    # 同行 always 单语句路径复用单节点解析。
    def _handle_inline_always_body(
        self,
        dict_body_items: dict[str, list],

        # 源码位置参数用于把合成消费量映射回 body 行号。
        list_body_lines: list[str],
        int_line_index: int,

        # always 语义参数用于生成结构化过程块。
        str_header: str,
        str_normalized_remainder: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 `always ... statement;` 形态的同行正文。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: always 起始行下标。
        :param str_header: always 触发头文本。
        :param str_normalized_remainder: always 头后的单语句正文。
        :param list_pending_comments: 当前 always 前导注释。
        :return: 已处理标志、下一行下标和清理后的前导注释。
        :raises VerilogFormatterError: 单语句无法结构化解析且不属于 raw case 兼容形态时重新抛出。
        """

        # list_tail_lines 保留 always 同行语句后面的原始续行。
        list_tail_lines = list_body_lines[int_line_index + 1 :]  # always 后续源码行

        # list_synthetic_lines 把同行正文放到首位，复用单控制节点 parser。
        list_synthetic_lines = [str_normalized_remainder, *list_tail_lines]  # 同行 always 合成语句流

        # tuple_consumed 记录单语句消耗行数。
        try:

            # tuple_consumed 记录同行 always 单控制语句解析出的消费范围。
            tuple_consumed = self._parse_single_control_node(list_synthetic_lines, 0, "always")  # 同行 always 单节点边界

        # 带预处理指令的 case 语句需要退回原始块收集路径。
        except VerilogFormatterError:

            # bool_has_preprocessor_directive 标记 case 正文里是否含有宏条件行。
            bool_has_preprocessor_directive = any(  # case 正文是否需要 raw 兼容路径
                line.strip().startswith("`") for line in list_synthetic_lines[1:]  # case 正文候选行
            )  # case 正文是否含有预处理指令

            # 只有 case 头且后续包含预处理行时沿用旧兼容路径。
            if str_normalized_remainder.startswith("case") and bool_has_preprocessor_directive:

                # tuple_raw_case 保存预处理 case 的原始正文和消耗行数。
                tuple_raw_case = self._collect_raw_case_statement_from_fragments(  # 预处理 case 原始片段状态
                    list_synthetic_lines,  # 预处理 case 合成源码片段
                    0,  # case 片段起始下标
                )  # 预处理 case 的原始解析结果

                # str_raw_block 组合 always 头和未重写的 case 正文。
                str_raw_block = "\n".join([str_header, *tuple_raw_case[0]])  # always 预处理 case 完整文本

                # payload_always 保留预处理 case 的正文行，避免结构化 parser 改写宏条件。
                payload_always = self._build_always_payload(  # 预处理 case always 载荷
                    str_header,  # 预处理 case 所属 always 触发头
                    tuple_raw_case[0],  # 未结构化改写的 case 正文
                    list_pending_comments,  # 预处理 case 前导注释
                )  # always 预处理 case 模型

                # 分类桶和顺序块同步记录该带宏条件的 always 片段。
                self._append_always_block(dict_body_items, payload_always, str_raw_block)

                # 预处理 case 消费合成行中对应的行数。
                return True, int_line_index + tuple_raw_case[1], []

            # 非预处理 case 兼容形态继续抛出原错误。
            raise

        # list_content_lines 是单语句 always 的正文行。
        list_content_lines = list_synthetic_lines[: tuple_consumed[1]]  # always 单语句正文

        # str_raw_block 组合 header 和正文，供左值提取器分析赋值目标。
        str_raw_block = "\n".join([str_header, *list_content_lines])  # always 完整文本

        # payload_always 携带同行单语句的触发头、左值和控制节点。
        payload_always = self._build_always_payload(  # 同行 always 载荷
            str_header,  # 同行语句所属的触发头
            list_content_lines,  # 同行 always 控制语句正文
            list_pending_comments,  # 只用于当前同行 always 的前导注释
        )  # always 单语句结构模型

        # 分类桶和顺序块同步记录该 always 单语句。
        self._append_always_block(dict_body_items, payload_always, str_raw_block)

        # 同行 always 消费单节点 parser 返回的行数。
        return True, int_line_index + tuple_consumed[1], []

    # always 头之后下一行是单语句时走独立处理路径。
    def _handle_following_single_always_body(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_lookahead: int,
        str_header: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 always 头下一行直接跟单条控制语句的形态。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_lookahead: always 正文首行下标。
        :param str_header: always 触发头文本。
        :param list_pending_comments: 当前 always 前导注释。
        :return: 已处理标志、下一行下标和清理后的前导注释。
        """

        # tuple_single_node 保存 always 下一行单控制节点的消耗范围。
        tuple_single_node = self._parse_single_control_node(list_body_lines, int_lookahead, "always")  # always 下一行单节点

        # list_content_lines 只保留非空正文行并做语句规范化。
        list_content_lines = [
            self._normalize_statement_line(line.strip())  # 保留单语句正文的规范化文本
            for line in list_body_lines[int_lookahead : tuple_single_node[1]]  # 单节点消费范围内的源码行
            if line.strip()  # 跳过正文范围内的空行
        ]  # always 单语句正文行

        # str_raw_block 组合 always 头与下一行控制语句，供左值分析复用。
        str_raw_block = "\n".join([str_header, *list_content_lines])  # 跨行单语句 always 的分析全文

        # payload_always 记录跨行单语句 always 的结构化节点。
        payload_always = self._build_always_payload(  # 跨行单语句 always 载荷
            str_header,  # 跨行单语句 always 触发头
            list_content_lines,  # 下一行控制语句正文
            list_pending_comments,  # 只用于当前跨行 always 的前导注释
        )  # always 跨行单语句结构模型

        # 分类桶和顺序块同步记录该跨行 always。
        self._append_always_block(dict_body_items, payload_always, str_raw_block)

        # 后续单语句路径消费到单节点 parser 返回的下标。
        return True, tuple_single_node[1], []

    # begin/end 形态 always 需要按深度收集完整过程块。
    def _handle_block_always_body(
        self,
        dict_body_items: dict[str, list],

        # 源码位置参数决定块收集起点和错误上下文。
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,

        # always 语义参数决定 payload 的触发头和前导注释。
        str_header: str,
        str_normalized_remainder: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理带 begin/end 的 always 块。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: always 起始行下标。
        :param str_stripped: always 起始行文本。
        :param str_header: always 触发头文本。
        :param str_normalized_remainder: always 头后的 begin 片段。
        :param list_pending_comments: 当前 always 前导注释。
        :return: 已处理标志、下一行下标和清理后的前导注释。
        """

        # list_block_lines 保存 begin/end 块内的规范化源码行。
        list_block_lines = [str_normalized_remainder] if str_normalized_remainder else []  # 外层 begin/end 块缓存

        # 没有同行 begin 时需要从 lookahead 行启动块收集。
        if not str_normalized_remainder:

            # int_lookahead 定位 always 头之后首个非空非注释正文行。
            int_lookahead = self._skip_ignorable_control_lines(  # always 头后首个 begin 候选位置
                list_body_lines,  # lookahead 需要跳过的 body 源码行
                int_line_index + 1,  # always 头下一行
            )  # always 后首个实质行下标

            # lookahead 越界表示 always 没有正文。
            if int_lookahead >= len(list_body_lines):

                # 缺少正文时直接报告无法闭合的 always 块。
                self._raise_control_parse_error(
                    "unsupported_shape",
                    f"unclosed always block near '{str_stripped}'",
                    "> ERR: [Python] Balance begin/end pairs or provide a stable single-statement "
                    "always body before formatting.",
                )

            # list_prelude_lines 保留 always 与 begin 之间的非空注释/中间行。
            list_prelude_lines = (  # always 头与 begin 之间保留下来的过渡行
                [line.strip() for line in list_body_lines[int_line_index + 1 : int_lookahead] if line.strip()]  # begin 前过渡行
                if int_lookahead > int_line_index + 1  # always 头和 begin 之间存在内容
                else []  # always 头后直接进入 begin
            )  # always 头和 begin 之间的行

            # prelude 行先进入块缓存，再追加真正 begin 行。
            list_block_lines.extend(list_prelude_lines)

            # begin 行作为块首个结构边界参与深度计算。
            list_block_lines.append(list_body_lines[int_lookahead].strip())

            # int_scan_index 从 begin 后一行继续扫描。
            int_scan_index = int_lookahead + 1  # always 块正文扫描下标

        # 同行 begin 形态从 always 后一行继续扫描。
        else:

            # int_scan_index 对齐到 always 下一行，避免重复收集同行 begin。
            int_scan_index = int_line_index + 1  # always 同行 begin 后的扫描下标

        # tuple_depth_state 计算初始 begin/end 深度。
        tuple_depth_state = self._initial_always_depth(list_block_lines)  # 初始深度和闭合标志

        # int_depth 跟踪 begin/end 嵌套层级。
        int_depth = tuple_depth_state[0]  # always 块当前 begin/end 深度

        # bool_closed 记录是否发现闭合 end。
        bool_closed = tuple_depth_state[1]  # always 块是否已闭合

        # 扫描后续行直到 begin/end 平衡。
        while int_scan_index < len(list_body_lines):

            # str_next_line 保留当前扫描行原始缩进去除后的文本。
            str_next_line = list_body_lines[int_scan_index].strip()  # always 块候选行

            # 块缓存保留当前行，便于闭合后恢复原始正文。
            list_block_lines.append(str_next_line)

            # str_normalized_line 用于 begin/end 深度计算。
            str_normalized_line = self._normalize_statement_line(str_next_line)  # 深度计算文本

            # 非注释行才参与 begin/end 深度计算。
            if not str_normalized_line.startswith("//"):

                # int_depth 累加当前语句引入或关闭的嵌套层级。
                int_depth += len(re.findall(r"\bbegin\b", str_normalized_line)) - len(  # 当前行造成的深度变化
                    re.findall(r"\bend\b", str_normalized_line)  # 当前行 end 数量
                )  # always 块更新后的嵌套深度

            # 深度归零且当前行包含 end 时确认闭合。
            if int_depth <= 0 and re.search(r"\bend\b", str_normalized_line):

                # bool_closed 标记当前行已提供最终闭合 end。
                bool_closed = True  # always 块已找到闭合行

                # 已找到闭合边界，停止向后吞并 module body。
                break

            # 块尚未闭合时继续扫描下一行。
            int_scan_index += 1  # always 块下一候选行下标

        # 未闭合 always 块不能进入 formatter 重写。
        if int_depth > 0 or not bool_closed:

            # 深度状态异常时保持严格模式失败，避免输出破坏语义的重排。
            self._raise_control_parse_error(
                "unsupported_shape",
                f"unclosed always block near '{str_stripped}'",
                "> ERR: [Python] Balance begin/end pairs or reduce nested control flow before formatting.",
            )

        # list_content_lines 去掉最后闭合 end，保留 always 正文。
        list_content_lines = list_block_lines[:-1]  # 去除最终 end 后的过程正文候选

        # begin header 不属于控制节点正文。
        if list_content_lines:

            # str_first_control 用于判断首行是否只是 begin header。
            str_first_control = self._normalize_statement_line(list_content_lines[0].strip())  # always 正文首行

            # 去掉 begin header 后再构造 AlwaysBlock。
            if self._is_begin_header(str_first_control):

                # list_content_lines 删除块头，只把过程语句交给 AlwaysBlock。
                list_content_lines = list_content_lines[1:]  # 去掉 begin 头后的 always 正文

        # str_raw_block 组合 always 头和正文，供左值提取器识别目标信号。
        str_raw_block = "\n".join([str_header, *list_content_lines])  # 左值提取器看到的过程块全文

        # payload_always 保存 begin/end always 的控制节点与目标信号。
        payload_always = self._build_always_payload(  # begin/end always 的结构化过程块模型
            str_header,  # 触发列表或组合敏感列表
            list_content_lines,  # 不含外层 begin/end 的控制语句
            list_pending_comments,  # 只附着到当前过程块的前导说明
        )  # 外层 begin/end always 的结构化结果

        # 分类桶和顺序块同步记录该 begin/end always。
        self._append_always_block(dict_body_items, payload_always, str_raw_block)

        # begin/end always 消费到闭合 end 后一行。
        return True, int_scan_index + 1, []

    # always 初始深度计算忽略 Verilog 注释行。
    def _initial_always_depth(self, list_block_lines: list[str]) -> tuple[int, bool]:
        """
        计算 always 块已收集行的初始 begin/end 深度。

        :param list_block_lines: 已收集的 always 块起始行列表。
        :return: 当前深度和是否已闭合的布尔标志。
        """

        # int_depth 汇总已收集行的 begin/end 差值。
        int_depth = 0  # 已收集 always 行的 begin/end 深度

        # 已收集行逐行参与深度计算。
        for str_line in list_block_lines:

            # str_normalized_line 是去注释前缀判断后的深度计算文本。
            str_normalized_line = self._normalize_statement_line(str_line.strip())  # 深度计算行文本

            # 注释行不参与 begin/end 深度计算。
            if str_normalized_line.startswith("//"):

                # 注释不会改变 begin/end 平衡，继续检查下一行。
                continue

            # int_depth 累计已收集头部行带来的 begin/end 差值。
            int_depth += len(re.findall(r"\bbegin\b", str_normalized_line)) - len(  # 初始行造成的深度变化
                re.findall(r"\bend\b", str_normalized_line)  # 当前行 end 关键字数量
            )  # 初始深度累计值

        # bool_closed 表示当前已收集行是否已经平衡。
        bool_closed = int_depth <= 0  # always 块是否已经闭合

        # 返回深度状态给 begin/end 扫描器。
        return int_depth, bool_closed

    # 实例声明识别放在 body 分发末尾，避免误吞其它结构。
    def _handle_instance_body_line(
        self,
        dict_body_items: dict[str, list],
        list_body_lines: list[str],
        int_line_index: int,
        str_stripped: str,
        list_pending_comments: list[str],
    ) -> tuple[bool, int, list[str]]:
        """
        处理 module instance 声明。

        :param dict_body_items: 正在构建的 body 分类字典。
        :param list_body_lines: module body 原始行列表。
        :param int_line_index: 当前扫描行下标。
        :param str_stripped: 当前行去除首尾空白后的文本。
        :param list_pending_comments: 当前结构前导 Verilog 注释。
        :return: 是否已处理、下一行下标和更新后的前导注释。
        """

        # str_next_stripped 用于识别跨行参数化实例。
        str_next_stripped = (
            self._normalize_statement_line(list_body_lines[int_line_index + 1].strip())  # 下一行实例续接文本
            if int_line_index + 1 < len(list_body_lines)  # 当前行后仍有源码
            else ""  # 文件末尾没有下一行可辅助判断
        )  # 下一行规范化文本

        # 非实例起点交回主循环处理未知语句。
        if not self._is_instance_start_line(str_stripped, str_next_stripped):

            # 当前行不符合实例声明起点，保持原位置给未知语句兜底。
            return False, int_line_index, list_pending_comments

        # list_block_lines 收集完整实例声明文本。
        list_block_lines = [list_body_lines[int_line_index]]  # 实例声明源码行

        # bool_closed 记录是否已经遇到实例 `);`。
        bool_closed = ");" in list_body_lines[int_line_index]  # 实例声明是否闭合

        # int_scan_index 指向实例声明当前扫描行。
        int_scan_index = int_line_index  # 实例声明扫描下标

        # 多行实例一直收集到 `);`。
        while int_scan_index + 1 < len(list_body_lines) and ");" not in list_body_lines[int_scan_index]:

            # 扫描推进到实例声明下一行。
            int_scan_index += 1  # 实例声明继续扫描的下一行下标

            # 当前实例行追加到声明文本。
            list_block_lines.append(list_body_lines[int_scan_index])

            # 命中闭合符号后结束收集。
            if ");" in list_body_lines[int_scan_index]:

                # bool_closed 标记当前实例已经找到 `);`。
                bool_closed = True  # 实例声明已闭合

                # 已拿到完整实例声明，停止吞并后续 body 行。
                break

        # 未闭合实例声明不能进入结构化渲染。
        if not bool_closed:

            # 实例端口或参数列表不完整时，严格模式拒绝猜测闭合位置。
            self._raise_control_parse_error(
                "unsupported_shape",
                f"unclosed instance block near '{str_stripped}'",
                "> ERR: [Python] Use a complete module instance with balanced parameter and port lists.",
            )

        # str_instance_text 保留完整实例声明文本。
        str_instance_text = "\n".join(list_block_lines).strip()  # 完整实例声明文本

        # payload_instance 提取模块名、实例名和参数化标志。
        payload_instance = self._parse_instance_block(str_instance_text)  # 实例结构模型

        # 实例前导注释只挂到当前实例，不传给后续声明。
        payload_instance.leading_comments = list(list_pending_comments)  # 实例前导注释副本

        # instances 分类保留实例结构。
        dict_body_items["instances"].append(payload_instance)

        # blocks 顺序列表保留实例声明位置。
        dict_body_items["blocks"].append(BodyBlock("instance_block", str_instance_text, payload_instance))

        # 实例声明消费到闭合行之后。
        return True, int_scan_index + 1, []

    # 实例识别前先排除明显属于控制流或声明的 Verilog 行。
    def _is_disallowed_instance_start(self, stripped: str) -> bool:
        """
        判断一行文本是否不应被当作模块实例起点。

        :param stripped: 已去除首尾空白的 Verilog 源码行。
        :return: 当前行属于控制、声明或预处理结构时为 `True`。
        """

        # 空行、注释和预处理行不会是实例声明起点。
        if not stripped or stripped.startswith("//") or stripped.startswith("`"):

            # 这些行交给上游注释或预处理分支处理。
            return True

        # str_lowered 用于大小写不敏感地排除 Verilog 关键结构。
        str_lowered = stripped.lower()  # 小写化后的实例候选行

        # tuple_disallowed_patterns 收录必须让位给专用 parser 的行首模式。
        tuple_disallowed_patterns = (  # 排除控制/声明关键字避免实例分支抢占
            r"^else\s+if\b",  # 条件分支延续语句
            r"^always\b",  # always 过程块入口
            r"^assign\b",  # continuous assign 声明入口
            r"^initial\b",  # 仿真初始化过程块
            r"^begin\b",  # 过程块 begin 头
            r"^end\b",  # 过程块 end 结束行
            r"^else\b",  # 条件链的兜底分支
            r"^if\b",  # 条件控制入口
            r"^case(?:x|z)?\b",  # 多路选择控制入口
            r"^for\b",  # 过程块循环入口
            r"^while\b",  # 条件循环入口
            r"^parameter\b",  # 模块参数声明入口
            r"^localparam\b",  # 局部参数声明入口
            r"^(?:wire|reg|logic|integer|genvar)\b",  # 信号或 genvar 声明
            r"^(?:input|output|inout)\b",  # 端口方向声明
            r"^function\b",  # 函数定义块入口
            r"^task\b",  # 任务定义块入口
            r"^generate\b",  # 生成语句区域入口
            r"^module\b",  # module 声明头入口
            r"^endmodule\b",  # module 结束关键字
        )

        # 返回实例识别应跳过的关键结构命中状态。
        return any(re.match(str_pattern, str_lowered) for str_pattern in tuple_disallowed_patterns)

    # 实例起点识别兼容普通实例、带参数实例和跨行 `#(` 形式。
    def _is_instance_start_line(self, stripped: str, next_stripped: str = "") -> bool:
        """
        判断当前行和下一行是否组成模块实例声明起点。

        :param stripped: 当前源码行的规范化文本。
        :param next_stripped: 下一源码行的规范化文本，用于跨行参数实例判断。
        :return: 当前上下文可作为实例声明起点时为 `True`。
        """

        # 保留关键结构给专用 parser，避免实例识别误吞控制语句。
        if self._is_disallowed_instance_start(stripped):

            # 关键结构不能作为实例起点。
            return False

        # 普通 `module_name instance_name (` 形态可直接识别为实例。
        if re.match(r"^[A-Za-z_]\w+\s+[A-Za-z_]\w+(?:\s*\[[^\]]+\])?\s*\(", stripped):

            # 当前行已经包含模块名、实例名和连接起始括号。
            return True

        # 参数化实例的 `module #(` 形态允许实例名出现在后续行。
        if re.match(r"^[A-Za-z_]\w+\s*#\s*\(", stripped):

            # 当前行是跨行参数列表入口。
            return True

        # 单独的 `module #` 需要下一行以参数括号继续。
        if re.match(r"^[A-Za-z_]\w+\s*#\s*$", stripped):

            # 下一行若打开参数列表，则当前行是实例声明起点。
            return next_stripped.startswith("(")

        # 单独模块名需要结合下一行判断参数表或实例名是否开始。
        if re.match(r"^[A-Za-z_]\w+$", stripped):

            # 下一行承接参数列表或实例名时，当前行视为实例起点。
            return next_stripped.startswith("#(") or bool(
                re.match(r"^[A-Za-z_]\w+(?:\s*\[[^\]]+\])?\s*\($", next_stripped)
            )

        # 其它行形态不满足稳定实例声明入口。
        return False

    # body 分类桶集中定义，确保递归解析和渲染阶段使用同一套键。
    def _new_body_items(self) -> dict[str, list]:
        """
        创建 `_parse_body` 使用的空 body 分类字典。

        :param: 无外部参数。
        :return: 包含声明、过程块、实例、预处理分支和顺序块的空列表映射。
        """

        # dict_body_items 为每类 Verilog body 结构准备独立列表，递归分支复用同一键集合。
        return {
            "functions": [],  # function/endfunction 原样回放块
            "tasks": [],  # task 块按源码顺序保存
            "localparams": [],  # parameter/localparam 声明列表
            "decls": [],  # wire/reg/logic 等信号声明列表
            "assigns": [],  # continuous assign 语句列表
            "always": [],  # always 过程块列表
            "initials": [],  # initial 块列表
            "instances": [],  # module 实例化列表
            "raw_blocks": [],  # 无法结构化重写的原始块
            "preprocessor_prologue": [],  # body 前导预处理或注释原始块
            "conditionals": [],  # `ifdef/`ifndef 双分支模型
            "generates": [],  # 结构生成语句模型
            "blocks": [],  # 按源码顺序排列的统一 body block
        }

    # 空 body 判断用于保留只有注释的条件编译分支。
    def _body_items_are_empty(self, items: dict[str, list]) -> bool:
        """
        判断 body 分类字典中是否没有任何结构化条目。

        :param items: 待检查的 body 分类字典。
        :return: 所有分类桶都为空时为 `True`。
        """

        # 所有已知分类键都为空时，该分支没有结构化 Verilog 条目。
        return all(not items.get(key) for key in self._new_body_items())

    # 只有注释的预处理分支需要保留为 prologue，避免格式化后丢注释。
    def _preserve_comment_only_conditional_branch(self, items: dict[str, list], raw_lines: list[str]) -> None:
        """
        把只有注释的条件编译分支转成可渲染的 RawBlock。

        :param items: 条件分支解析得到的 body 分类字典。
        :param raw_lines: 条件分支原始源码行。
        :return: 无业务返回值，必要时直接更新 `items`。
        """

        # 只有原始分支存在且结构桶为空时才需要转存注释。
        if not raw_lines or not self._body_items_are_empty(items):

            # 已含结构化条目时保留正常解析结果。
            return

        # list_comment_lines 保留条件分支中可见的注释行文本。
        list_comment_lines = [line.strip() for line in raw_lines if line.strip()]  # 条件分支非空源码行

        # 仅当所有可见行都是行注释时，才降级为 prologue raw block。
        if list_comment_lines and all(line.startswith("//") for line in list_comment_lines):

            # 注释-only 条件分支通过 preprocessor_prologue 保留输出。
            items["preprocessor_prologue"].append(RawBlock(lines=list_comment_lines))

    # 递归收集让顶层分析能看到条件编译分支内的同类条目。
    def _collect_body_items_recursive(self, items: dict[str, list], key: str) -> list:
        """
        按 key 收集 body 分类字典及其条件分支内的条目。

        :param items: 当前层级的 body 分类字典。
        :param key: 需要收集的分类键。
        :return: 当前层级和条件分支内按出现顺序拼接的条目列表。
        """

        # list_collected 先放入当前层级的目标分类条目。
        list_collected = list(items.get(key, []))  # 当前层级目标条目副本

        # 条件编译分支内的同类条目按 true 分支再 false 分支追加。
        for conditional in items.get("conditionals", []):

            # true 分支内的目标条目保持递归出现顺序。
            list_collected.extend(self._collect_body_items_recursive(conditional.true_items, key))

            # false 分支内的目标条目接在 true 分支之后。
            list_collected.extend(self._collect_body_items_recursive(conditional.false_items, key))

        # 返回当前层级与条件分支拼接后的条目列表。
        return list_collected

    # raw block 收集用于 specify 等不参与结构化重写的区域。
    def _parse_raw_block(self, lines: list[str], start: int, terminator: str, label: str) -> tuple[RawBlock, int]:
        """
        收集直到指定终止关键字闭合的原始块。

        :param lines: module body 的源码行列表。
        :param start: 原始块起始行下标。
        :param terminator: 终止关键字，例如 `endspecify`。
        :param label: 错误消息中展示的块类型说明。
        :return: 原始块 payload 和下一行下标。
        :raises VerilogFormatterError: 当终止关键字缺失时抛出。
        """

        # list_block_lines 保留 raw block 中每一行的去首尾空白文本。
        list_block_lines: list[str] = []  # raw block 源码行缓存

        # int_index 从 raw block 起始行开始寻找终止关键字。
        int_index = start  # raw block 扫描下标

        # raw block 内部不做结构化解析，只寻找 terminator。
        while int_index < len(lines):

            # str_stripped 是当前 raw block 行的可比较文本。
            str_stripped = lines[int_index].strip()  # terminator 匹配使用的 raw 行

            # 当前 raw 行按原相对顺序进入 payload。
            list_block_lines.append(str_stripped)

            # 命中终止关键字时返回 raw payload 和下一行下标。
            if self._normalize_statement_line(str_stripped).startswith(terminator):

                # raw block 已完整闭合，可以交给 caller 入桶。
                return RawBlock(lines=list_block_lines), int_index + 1

            # int_index 推进到下一行继续查找终止关键字。
            int_index += 1  # raw block 继续扫描的下一行下标

        # 未找到终止关键字时按严格模式错误处理。
        self._raise_control_parse_error(
            "unsupported_shape",
            lines[start].strip(),
            f"> ERR: [Python] Close each {label} with '{terminator}' before formatting.",
        )

    # 条件编译解析保留 true/false 两侧 body 分类，供 formatter 后续分别渲染。
    def _parse_preprocessor_conditional(
        self,
        lines: list[str],
        start: int,
        pending_comments: list[str],
    ) -> tuple[PreprocessorConditional, int]:
        """
        解析 `ifdef` 或 `ifndef` 条件编译块。

        :param lines: module body 的源码行列表。
        :param start: 条件编译头所在下标。
        :param pending_comments: 条件编译块前导注释。
        :return: 条件编译模型和下一行下标。
        :raises VerilogFormatterError: 当条件编译头或闭合结构不受支持时抛出。
        """

        # str_header 保存条件编译指令原文，错误报告需要展示它。
        str_header = lines[start].strip()  # 条件编译头行文本

        # match_header 解析指令类型和宏符号。
        match_header = re.match(r"^`(?P<directive>ifdef|ifndef)\s+(?P<symbol>\w+)\s*$", str_header)  # 条件编译头匹配结果

        # 头部不是稳定的 ifdef/ifndef 时停止解析。
        if not match_header:

            # 严格模式要求条件编译头可静态拆分。
            self._raise_control_parse_error(
                "unsupported_construct",
                str_header,
                "> ERR: [Python] Use a stable `ifdef/`ifndef directive before formatting.",
            )

        # str_directive 保留 ifdef/ifndef 类型，渲染时仍需输出原指令。
        str_directive = match_header.group("directive")  # 条件编译指令类型

        # str_symbol 是条件编译依赖的宏名。
        str_symbol = match_header.group("symbol")  # 条件编译宏符号

        # list_true_lines 收集 `else` 之前的源码行。
        list_true_lines: list[str] = []  # 条件成立分支源码

        # list_false_lines 只接收顶层 `else` 之后、配对 `endif` 之前的源码。
        list_false_lines: list[str] = []  # 条件编译 else 分支源码

        # list_active_lines 指向当前正在填充的分支。
        list_active_lines = list_true_lines  # 当前条件编译分支缓存

        # bool_saw_else 标记是否出现顶层 `else`。
        bool_saw_else = False  # 条件编译是否含 else 分支

        # int_depth 跟踪嵌套条件编译深度。
        int_depth = 1  # 条件编译嵌套深度

        # int_index 从条件编译头下一行开始扫描。
        int_index = start + 1  # 条件编译扫描下标

        # 扫描到配对 `endif` 前，按当前分支累积源码。
        while int_index < len(lines):

            # str_stripped 保存当前预处理行的去空白文本。
            str_stripped = lines[int_index].strip()  # 当前条件编译行文本

            # 嵌套条件编译会提高深度，避免误把内部 endif 当作闭合。
            if str_stripped.startswith(("`ifdef", "`ifndef")):

                # int_depth 上升表示后续 endif 先闭合内部条件块。
                int_depth += 1  # 进入嵌套条件编译层级

            # endif 让嵌套深度回退。
            elif str_stripped.startswith("`endif"):

                # int_depth 下降后才能判断当前 endif 是否闭合本块。
                int_depth -= 1  # 离开一层条件编译层级

                # 顶层 endif 触发两个分支的递归 body 解析。
                if int_depth == 0:

                    # dict_true_items 是条件成立分支解析后的 body 字典。
                    dict_true_items = (
                        self._parse_body("\n".join(list_true_lines))  # 条件成立分支递归解析
                        if list_true_lines  # 条件成立分支存在源码
                        else self._new_body_items()  # 条件成立分支为空时提供空桶
                    )  # 条件成立分支 body

                    # dict_false_items 保存 else 分支递归解析出的结构桶。
                    dict_false_items = (
                        self._parse_body("\n".join(list_false_lines))  # else 分支源码递归解析
                        if list_false_lines  # else 分支存在可解析源码
                        else self._new_body_items()  # 无 else 正文时保留空结构桶
                    )  # 条件编译 else 分支 body

                    # 只含注释的 true 分支需要显式保留为 raw block。
                    self._preserve_comment_only_conditional_branch(dict_true_items, list_true_lines)

                    # else 侧纯注释也要保留，避免条件编译说明被 formatter 丢弃。
                    self._preserve_comment_only_conditional_branch(dict_false_items, list_false_lines)

                    # payload_conditional 汇总指令、宏符号和两侧分支 body。
                    payload_conditional = PreprocessorConditional(  # 渲染 `ifdef/`ifndef 所需的完整分支模型
                        directive=str_directive,  # 原始条件编译指令类型
                        symbol=str_symbol,  # 条件编译宏名
                        true_items=dict_true_items,  # 条件成立侧的递归 body
                        false_items=dict_false_items,  # else 侧或空分支 body
                        leading_comments=list(pending_comments),  # 条件编译块前的说明注释
                        has_else=bool_saw_else,  # 输出阶段是否需要恢复 `else`
                    )  # 条件编译结构化模型

                    # tuple_conditional_result 对齐解析函数的模型加下一行下标协议。
                    tuple_conditional_result = (  # 条件编译 parser 的 payload 与扫描出口
                        payload_conditional,  # 已结构化的条件编译块
                        int_index + 1,  # `endif` 后的下一行
                    )  # 条件编译解析返回值

                    # 返回条件编译块和 endif 后的继续扫描位置。
                    return tuple_conditional_result

            # 顶层 else 切换到 false 分支收集。
            elif int_depth == 1 and str_stripped.startswith("`else"):

                # list_active_lines 后续写入 false 分支。
                list_active_lines = list_false_lines  # 后续源码写入条件不成立分支

                # bool_saw_else 记录输出模型需要渲染 else。
                bool_saw_else = True  # 条件编译块包含显式 else

                # int_index 跳过 else 指令自身。
                int_index += 1  # 条件编译扫描移动到 else 后首行

                # else 行不属于任一分支正文。
                continue

            # 当前行归入 active 分支缓存。
            list_active_lines.append(lines[int_index])

            # int_index 推进到下一行继续扫描条件编译块。
            int_index += 1  # 条件编译扫描推进到下一行

        # 条件编译扫描耗尽仍未闭合时报告结构错误。
        self._raise_control_parse_error(
            "unsupported_shape",
            str_header,
            "> ERR: [Python] Close each conditional preprocessor block with '`endif' before formatting.",
        )

    # 实例块解析只提取模块名、实例名和参数化标志，不重写端口文本。
    def _parse_instance_block(self, text: str) -> InstanceBlock:
        """
        从完整实例声明文本中提取实例元数据。

        :param text: 已收集完整的 Verilog 实例声明文本。
        :return: 保留原始文本并带模块名、实例名和参数化标志的 InstanceBlock。
        """

        # str_first_line 提供实例模块名和简单实例名的首行候选。
        str_first_line = text.splitlines()[0].strip()  # 实例声明首行

        # match_module 提取模块名和是否存在参数列表。
        match_module = re.match(r"(?P<module>\w+)\s*(?P<params>#\s*\()?", str_first_line)  # 模块名匹配结果

        # str_module_name 是实例所属模块类型。
        str_module_name = match_module.group("module") if match_module else ""  # 实例模块名

        # bool_has_params 标记实例是否带参数化 `#(...)`。
        bool_has_params = bool(match_module and match_module.group("params"))  # 实例参数化标志

        # str_instance_name 后续会被简单实例名或参数化实例名覆盖。
        str_instance_name = ""  # 实例名候选

        # match_simple_name 处理非参数化实例的常见首行形态。
        match_simple_name = re.match(r"^\w+\s+(\w+)(?:\s*\[[^\]]+\])?\s*\(", str_first_line)  # 简单实例名匹配

        # 简单实例声明可直接从首行得到实例名。
        if match_simple_name:

            # str_instance_name 保存首行匹配到的实例标识符。
            str_instance_name = match_simple_name.group(1)  # 简单实例名

        # str_compact_text 压缩多行实例声明，便于参数化实例名匹配。
        str_compact_text = " ".join(text.split())  # 压缩后的实例声明文本

        # match_param_name 在 `#(...) instance (` 后提取实例名。
        match_param_name = re.search(r"\)\s*(\w+)(?:\s*\[[^\]]+\])?\s*\(", str_compact_text)  # 参数化实例名匹配

        # 参数化实例名优先覆盖简单候选。
        if match_param_name:

            # str_instance_name 保存参数列表之后的实例标识符。
            str_instance_name = match_param_name.group(1)  # 参数化实例名

        # 返回实例模型，保留原始声明文本供渲染阶段直接输出。
        return InstanceBlock(
            text=text,
            module_name=str_module_name,
            instance_name=str_instance_name,
            has_params=bool_has_params,
        )

    # generate 块需要按嵌套深度收集到匹配的 endgenerate。
    def _collect_generate_block(self, lines: list[str], start: int) -> tuple[list[str], int]:
        """
        收集完整 generate/endgenerate 块。

        :param lines: module body 的源码行列表。
        :param start: generate 起始行下标。
        :return: generate 块源码行和下一行下标。
        :raises VerilogFormatterError: 当 generate 块未闭合时抛出。
        """

        # list_block_lines 从 generate 头开始保留完整外层源码。
        list_block_lines = [lines[start]]  # generate 块源码行缓存

        # int_depth 记录嵌套 generate/endgenerate 的配对层级。
        int_depth = 1  # generate 嵌套深度

        # int_index 从 generate 头下一行继续扫描。
        int_index = start + 1  # generate 头之后的扫描下标

        # 扫描直到外层 endgenerate 被匹配。
        while int_index < len(lines):

            # 当前源码行先进入块缓存，保证错误前的原顺序可恢复。
            list_block_lines.append(lines[int_index])

            # str_stripped 用于识别 generate 边界关键字。
            str_stripped = self._normalize_statement_line(lines[int_index].strip())  # generate 当前规范化行

            # 内层 generate 会增加等待闭合的层级。
            if str_stripped.startswith("generate"):

                # int_depth 上升后当前 endgenerate 不会误闭合外层。
                int_depth += 1  # 进入内层 generate 后的待闭合层数

            # endgenerate 让最近一层 generate 闭合。
            if re.search(r"\bendgenerate\b$", str_stripped):

                # int_depth 回退后判断是否已经闭合外层块。
                int_depth -= 1  # 当前 endgenerate 闭合后的剩余层数

                # 外层块闭合时返回源码行和下一行下标。
                if int_depth == 0:

                    # 返回完整 generate 块和调用方继续扫描的位置。
                    return list_block_lines, int_index + 1

            # int_index 推进到下一行继续查找闭合关键字。
            int_index += 1  # generate 块继续扫描的下一行下标

        # 扫描结束仍未闭合时报告 generate 结构错误。
        self._raise_control_parse_error(
            "generate_normalization_violation",
            lines[start].strip(),
            "> ERR: [Python] Close every generate block with a matching endgenerate before formatting.",
        )

    # generate 内部语句提取会去掉外层关键字，同时保留同一行的剩余片段。
    def _extract_generate_inner_lines(self, block_lines: list[str]) -> list[str]:
        """
        提取 generate 块内部用于控制节点解析的源码行。

        :param block_lines: 完整 generate/endgenerate 块源码行。
        :return: 去掉外层 generate/endgenerate 后的内部源码行。
        :raises VerilogFormatterError: 当块首行不是 generate 时抛出。
        """

        # 空块没有内部语句，直接返回空列表。
        if not block_lines:

            # 返回空列表，调用方会生成空 generate 节点。
            return []

        # str_first_line 保存外层 generate 头的规范化文本。
        str_first_line = self._normalize_statement_line(block_lines[0].strip())  # 外层 generate 头部规范化文本

        # 非 generate 起始说明调用方传入了不匹配的块。
        if not str_first_line.startswith("generate"):

            # 严格模式要求 generate 收集器只处理 generate 块。
            self._raise_control_parse_error(
                "unsupported_generate_shape",
                block_lines[0].strip(),
                "> ERR: [Python] Start each generate block with the generate keyword.",
            )

        # list_inner_lines 保存去掉外层关键字后的可解析正文。
        list_inner_lines: list[str] = []  # 去掉外层关键字后的 generate 内部行

        # str_first_remainder 保留 `generate` 同行后续内容。
        str_first_remainder = str_first_line[len("generate") :].strip()  # generate 首行剩余语句

        # 同行剩余内容属于 generate 内部第一条语句。
        if str_first_remainder:

            # 内部行列表先写入首行剩余片段。
            list_inner_lines.append(str_first_remainder)

        # list_middle_lines 是外层首尾关键字之间的主体行。
        list_middle_lines = block_lines[1:-1]  # generate 中间源码行

        # 中间源码行保持原顺序追加，交给控制节点 parser 继续解析。
        list_inner_lines.extend(list_middle_lines)

        # 末行可能同时包含 endgenerate 前的尾随语句。
        if len(block_lines) > 1:

            # str_last_line 保存外层块末行的规范化文本。
            str_last_line = self._normalize_statement_line(block_lines[-1].strip())  # generate 末行文本

            # 末行存在 endgenerate 时需要剥离关键字。
            if re.search(r"\bendgenerate\b$", str_last_line):

                # str_last_remainder 是 endgenerate 前的尾随正文。
                str_last_remainder = re.sub(r"\bendgenerate\b$", "", str_last_line).strip()  # generate 末行剩余语句

                # 尾随正文存在时补回内部语句列表。
                if str_last_remainder:

                    # 内部行列表追加末行剥离 endgenerate 后的内容。
                    list_inner_lines.append(str_last_remainder)

        # 返回去掉外层 generate/endgenerate 后的内部语句。
        return list_inner_lines

    # function 块按 endfunction 闭合，内部语句暂时作为原始行保留。
    def _parse_function_block(self, lines: list[str], start: int) -> tuple[FunctionBlock, int]:
        """
        收集完整 function/endfunction 块。

        :param lines: module body 的源码行列表。
        :param start: function 起始行下标。
        :return: function 块模型和下一行下标。
        :raises VerilogFormatterError: 当 function 未闭合时抛出。
        """

        # list_block_lines 保存 Verilog function 从声明到 endfunction 的可回放文本。
        list_block_lines: list[str] = []  # function 声明体源码缓存

        # int_index 指向正在检查的 function 声明体行。
        int_index = start  # function 闭合扫描下标

        # function 需要完整闭合后才能交给渲染层原样输出。
        while int_index < len(lines):

            # str_normalized_line 去除首尾空白，便于识别 endfunction 边界。
            str_normalized_line = self._normalize_statement_line(lines[int_index].strip())  # function 边界判定行

            # function 模型只保存可见源码，空行由 formatter 统一重建。
            if str_normalized_line:

                # 当前非空行属于 function 声明体，需要保持相对顺序。
                list_block_lines.append(str_normalized_line)

            # endfunction 说明函数体边界已闭合，可以返回容器模型。
            if str_normalized_line.startswith("endfunction"):

                # payload_function 封装已闭合函数源码，供 body 分类桶引用。
                payload_function = FunctionBlock(lines=list_block_lines)  # 完整 function 源码模型

                # 返回函数模型和 endfunction 后的继续扫描位置。
                return payload_function, int_index + 1

            # int_index 推进到下一行，继续寻找 function 闭合关键字。
            int_index += 1  # function 下一候选行下标

        # 未遇到 endfunction 时报告结构错误。
        self._raise_control_parse_error(
            "unsupported_shape",
            lines[start].strip(),
            "> ERR: [Python] Close each function block with endfunction before formatting.",
        )

    # task 块按 endtask 闭合，保持原始顺序交给渲染层。
    def _parse_task_block(self, lines: list[str], start: int) -> tuple[TaskBlock, int]:
        """
        收集完整 task/endtask 块。

        :param lines: module body 的源码行列表。
        :param start: task 起始行下标。
        :return: task 块模型和下一行下标。
        :raises VerilogFormatterError: 当 task 未闭合时抛出。
        """

        # list_block_lines 记录 task 任务体的可回放源码，渲染时不拆内部语句。
        list_block_lines: list[str] = []  # task 任务体源码缓存

        # int_index 指向 task 扫描窗口中的当前源码行。
        int_index = start  # task 任务体扫描下标

        # task 以 endtask 作为唯一闭合边界，内部控制语句暂不展开。
        while int_index < len(lines):

            # str_normalized_line 是当前 task 行的边界判定文本。
            str_normalized_line = self._normalize_statement_line(lines[int_index].strip())  # task 闭合判定文本

            # task 容器只记录可见语句，空行由输出阶段统一安排。
            if str_normalized_line:

                # 当前非空行属于任务声明体，保留其相对位置。
                list_block_lines.append(str_normalized_line)

            # endtask 表示任务体收集完成，可以生成 TaskBlock。
            if str_normalized_line.startswith("endtask"):

                # payload_task 封装任务源码，保持 task 渲染入口独立。
                payload_task = TaskBlock(lines=list_block_lines)  # 完整 task 回放模型

                # 返回任务模型和 endtask 后的继续扫描位置。
                return payload_task, int_index + 1

            # int_index 推进到下一行，继续寻找任务闭合标记。
            int_index += 1  # 任务体继续寻找 endtask 的下一行

        # 缺失 endtask 时报告任务块结构错误。
        self._raise_control_parse_error(
            "unsupported_shape",
            lines[start].strip(),
            "> ERR: [Python] Close each task block with endtask before formatting.",
        )

    # initial 块解析兼容单语句、同行 begin 和下一行 begin 三种写法。
    def _parse_initial_block(
        self,
        lines: list[str],
        start: int,
        *,
        allow_parameter_check: bool = False,
    ) -> tuple[InitialBlock, int]:
        """
        解析 initial 块并返回标准化 InitialBlock。

        :param lines: module body 的源码行列表。
        :param start: initial 起始行下标。
        :param allow_parameter_check: 是否允许 legacy 参数检查 initial 降级保留。
        :return: initial 块模型和下一行下标。
        :raises VerilogFormatterError: 当 initial 形态不受支持或未闭合时抛出。
        """

        # str_header 保存 initial 起始行的规范化文本。
        str_header = self._normalize_statement_line(lines[start].strip())  # initial 分支的入口行文本

        # initial parser 只接受 initial 起始行。
        if not str_header.startswith("initial"):

            # 非 initial 行进入该函数说明上游调度错误，需要严格失败。
            self._raise_control_parse_error(
                "unsupported_construct",
                lines[start].strip(),
                "> ERR: [Python] Use a stable initial block before formatting.",
            )

        # str_inline_body 是 initial 关键字后的同行正文。
        str_inline_body = str_header[len("initial") :].strip()  # initial 同行正文

        # 同行单语句 initial 走 statement 收集路径。
        if str_inline_body and not str_inline_body.startswith("begin"):

            # tuple_statement_parts 保存单语句正文和消耗行数。
            tuple_statement_parts = self._collect_statement_text_from_fragments(  # 同行 initial 语句文本和消耗行数
                [str_inline_body, *lines[start + 1 :]],  # initial 同行正文加后续候选行
                0,  # 合成片段从首项开始收集
            )  # initial 同行单语句收集结果

            # str_statement 是完整 initial 单语句正文。
            str_statement = tuple_statement_parts[0]  # initial 单语句文本

            # int_consumed 是合成片段中被单语句消耗的行数。
            int_consumed = tuple_statement_parts[1]  # initial 单语句消耗行数

            # 单语句 initial 必须以顶层分号闭合。
            if not self._statement_has_top_level_semicolon(str_statement):

                # 缺少分号时拒绝继续猜测语句边界。
                self._raise_control_parse_error(
                    "unsupported_shape",
                    str_header,
                    "> ERR: [Python] Terminate each single-statement initial body with ';' before formatting.",
                )

            # payload_initial 把裸 initial 语句包装成统一的 begin/end 模型。
            payload_initial = self._build_initial_block(  # 单语句 initial 包装后的统一块模型
                "initial begin",  # formatter 内部统一使用 begin 形态头
                str_statement.splitlines(),  # 单语句拆分出的正文行
                start + int_consumed,  # 映射回原 body 的继续扫描下标
                allow_parameter_check,  # 同行单语句是否允许参数检查降级
            )  # 同行单语句 initial 模型

            # 返回同行单语句 initial 的解析结果。
            return payload_initial

        # 同行 begin initial 直接进入 begin/end 收集器。
        if str_inline_body.startswith("begin"):

            # payload_inline_begin 覆盖 `initial begin` 写在同一行的源码形态。
            payload_inline_begin = self._parse_initial_begin_block(  # 同行 begin/end initial 的收集结果
                lines,  # initial 所在 body 源码行
                start + 1,  # 同行 begin 后第一条候选正文
                str_inline_body,  # 与 initial 同行的 begin 头片段
                allow_parameter_check,  # 同行 begin 路径的参数检查兼容位
            )  # 同行 begin initial 的模型和出口

            # 返回同行 begin initial 的解析边界。
            return payload_inline_begin

        # int_lookahead 跳过 initial 头后的空白或注释行。
        int_lookahead = self._skip_ignorable_control_lines(lines, start + 1)  # initial 下一实质行下标

        # 下一实质行存在时继续识别 begin 或单语句形态。
        if int_lookahead < len(lines):

            # str_next_stripped 是 initial 后第一条实质语句。
            str_next_stripped = self._normalize_statement_line(lines[int_lookahead].strip())  # initial 下一行文本

            # 下一行 begin 形态交给 begin/end 收集器。
            if str_next_stripped.startswith("begin"):

                # payload_next_begin 保存下一行 begin initial 的解析结果。
                payload_next_begin = self._parse_initial_begin_block(  # 下一行 begin/end initial 的收集结果
                    lines,  # 下一行 begin 路径使用的 body 源码行
                    int_lookahead + 1,  # begin 行后一行作为扫描起点
                    str_next_stripped,  # initial 后首条实质 begin 头
                    allow_parameter_check,  # 下一行 begin 路径的参数检查兼容位
                )  # 下一行 begin initial 解析结果

                # 返回下一行 begin initial 的解析边界。
                return payload_next_begin

            # tuple_statement_parts 保存下一行单语句 initial 的文本与结束下标。
            tuple_statement_parts = self._collect_statement_text(lines, int_lookahead)  # 下一行 initial 单语句收集结果

            # str_statement 是下一行开始的完整 initial 单语句。
            str_statement = tuple_statement_parts[0]  # 下一行 initial 单语句文本

            # int_next_index 是单语句之后的继续扫描下标。
            int_next_index = tuple_statement_parts[1]  # 下一行 initial 单语句结束下标

            # 单语句 initial 必须有顶层分号。
            if not self._statement_has_top_level_semicolon(str_statement):

                # 缺少分号时不能进入 initial 控制节点解析。
                self._raise_control_parse_error(
                    "unsupported_shape",
                    str_header,
                    "> ERR: [Python] Terminate each single-statement initial body with ';' before formatting.",
                )

            # payload_initial 复用 begin 形态构造器承载单语句 initial。
            payload_initial = self._build_initial_block(  # 跨行单语句 initial 的统一块模型
                "initial begin",  # 跨行裸语句统一包装后的内部头
                str_statement.splitlines(),  # 下一行单语句拆分出的正文
                int_next_index,  # 单语句后继续扫描的位置
                allow_parameter_check,  # 跨行裸语句路径的参数检查兼容位
            )  # 单语句 initial 模型

            # 返回单语句 initial 的结构化模型。
            return payload_initial

        # initial 后没有可解析正文时报告结构错误。
        self._raise_control_parse_error(
            "unsupported_shape",
            str_header,
            "> ERR: [Python] Provide a body for each initial block before formatting.",
        )

    # begin 形态 initial 需要按 begin/end 深度收集正文。
    def _parse_initial_begin_block(
        self,
        lines: list[str],
        next_index: int,
        begin_line: str,
        allow_parameter_check: bool,
    ) -> tuple[InitialBlock, int]:
        """
        解析 `initial begin` 形态的过程块。

        :param lines: module body 的源码行列表。
        :param next_index: begin 行之后的首个候选正文下标。
        :param begin_line: 已规范化的 begin 行文本。
        :param allow_parameter_check: 是否允许 legacy 参数检查 initial 降级保留。
        :return: initial 块模型和下一行下标。
        :raises VerilogFormatterError: 当 begin/end 不成对时抛出。
        """

        # match_begin 提取可选 begin 标签和同行剩余正文。
        match_begin = re.match(r"^begin(?:\s*:\s*(\w+))?(.*)$", begin_line)  # initial begin 头匹配结果

        # begin 行不匹配时，不能安全收集 initial 块。
        if not match_begin:

            # 严格模式要求 initial begin 头可以静态拆分。
            self._raise_control_parse_error(
                "unsupported_shape",
                begin_line,
                "> ERR: [Python] Use 'initial begin ... end' or 'initial statement;' before formatting.",
            )

        # str_label 保留 Verilog 命名 begin 块标签。
        str_label = match_begin.group(1) or ""  # 命名 begin 块标签文本

        # str_header 作为 InitialBlock.header 的标准化头文本。
        str_header = f"initial begin:{str_label}" if str_label else "initial begin"  # initial 标准化头

        # str_remainder 是 begin 同行 end 之前可能存在的正文。
        str_remainder = match_begin.group(2).strip()  # begin 头后尚未入正文的片段

        # list_content_lines 收集 begin/end 内部过程语句。
        list_content_lines: list[str] = []  # initial begin/end 内部语句缓存

        # begin 同行存在正文时，先处理同行内容。
        if str_remainder:

            # 同行已经出现 end 时，当前 initial 块在本行闭合。
            if re.search(r"\bend\b\s*$", str_remainder):

                # str_before_end 保留 end 之前的有效正文。
                str_before_end = re.sub(r"\bend\b\s*$", "", str_remainder).strip()  # 同行 end 前正文

                # end 前有正文时补入 initial 内容。
                if str_before_end:

                    # initial 正文加入同行 end 前的语句。
                    list_content_lines.append(str_before_end)

                # payload_initial 保存同行闭合 initial 的结构化模型。
                payload_initial = self._build_initial_block(  # 同行闭合 initial 的统一块模型
                    str_header,  # 含可选标签的 initial begin 头
                    list_content_lines,  # end 之前的同行正文
                    next_index,  # 调用方传入的继续扫描下标
                    allow_parameter_check,  # 同行闭合 begin 路径的参数检查兼容位
                )  # 同行闭合 initial 模型

                # 返回同行闭合 initial 的解析结果。
                return payload_initial

            # 同行剩余内容尚未闭合，作为第一条正文保留。
            list_content_lines.append(str_remainder)

        # int_depth 跟踪 initial begin/end 的嵌套深度。
        int_depth = 1  # 外层 initial begin 已打开的深度

        # 同行正文中的 begin/end 也会影响初始深度。
        if str_remainder:

            # int_depth 叠加同行正文里的嵌套变化。
            int_depth += len(re.findall(r"\bbegin\b", str_remainder)) - len(  # 同行正文造成的深度变化
                re.findall(r"\bend\b", str_remainder)  # 同行正文中的 end 数量
            )  # 同行正文更新后的深度

        # int_index 从 begin 后的第一条候选正文开始扫描。
        int_index = next_index  # initial begin 正文扫描下标

        # 扫描后续行直到匹配外层 end。
        while int_index < len(lines):

            # str_normalized_line 是当前 initial 正文候选行。
            str_normalized_line = self._normalize_statement_line(lines[int_index].strip())  # initial 当前正文行

            # 空行不进入 initial 控制节点解析。
            if not str_normalized_line:

                # int_index 跳过空行继续寻找有效正文或 end。
                int_index += 1  # 跳过空行后的扫描下标

                # 空行没有语义内容，不改变 begin/end 深度。
                continue

            # 单独 end 在外层深度处闭合 initial 块。
            if not str_normalized_line.startswith("//") and int_depth == 1 and str_normalized_line == "end":

                # payload_initial 保存遇到独立 end 后构造出的 initial。
                payload_initial = self._build_initial_block(  # 独立 end 闭合后的 initial 模型
                    str_header,  # 独立 end 对应的 initial 头
                    list_content_lines,  # end 前已收集的正文
                    int_index + 1,  # 独立 end 之后的继续扫描下标
                    allow_parameter_check,  # 独立 end 路径的参数检查兼容位
                )  # 独立 end 闭合 initial 模型

                # 返回 initial 模型和 end 后的扫描位置。
                return payload_initial

            # 当前非空行先加入正文缓存。
            list_content_lines.append(str_normalized_line)

            # 注释行不影响 begin/end 深度。
            if not str_normalized_line.startswith("//"):

                # int_depth 按当前行的 begin/end 关键字更新。
                int_depth += len(re.findall(r"\bbegin\b", str_normalized_line)) - len(  # 当前正文行造成的深度变化
                    re.findall(r"\bend\b", str_normalized_line)  # 当前正文行中的 end 数量
                )  # 当前行更新后的 initial 深度

                # 深度归零表示当前行包含外层闭合 end。
                if int_depth <= 0:

                    # str_closing_remainder 保留闭合 end 前的尾随正文。
                    str_closing_remainder = re.sub(r"\bend\b\s*$", "", str_normalized_line).strip()  # 闭合 end 前正文

                    # 闭合行还有正文时替换掉含 end 的原行。
                    if str_closing_remainder:

                        # 最后一行只保留 end 之前的过程语句。
                        list_content_lines[-1] = str_closing_remainder  # 用 end 前正文替换闭合行

                    # 闭合行只有 end 时，不把 end 放入正文列表。
                    else:

                        # 移除刚追加的纯 end 行。
                        list_content_lines.pop()

                    # payload_initial 保存闭合后的 initial 结构。
                    payload_initial = self._build_initial_block(  # begin/end 闭合后的 initial 模型
                        str_header,  # 当前闭合 end 对应的 initial 头
                        list_content_lines,  # 去除外层 end 后的正文
                        int_index + 1,  # 闭合行之后的继续扫描下标
                        allow_parameter_check,  # 普通 begin/end 路径的参数检查兼容位
                    )  # begin/end initial 模型

                    # 返回闭合 initial 的解析结果。
                    return payload_initial

            # int_index 推进到下一行继续扫描 begin/end 平衡。
            int_index += 1  # initial 块体继续扫描的下一行下标

        # 未遇到匹配 end 时报告 initial begin/end 不平衡。
        self._raise_control_parse_error(
            "unsupported_shape",
            str_header,
            "> ERR: [Python] Close each initial block with a matching end before formatting.",
        )

    # initial block 构造阶段把正文交给控制节点 parser，并保留参数检查降级策略。
    def _build_initial_block(
        self,
        header: str,
        content_lines: list[str],
        next_index: int,
        allow_parameter_check: bool,
    ) -> tuple[InitialBlock, int]:
        """
        构造 InitialBlock 并解析其内部控制节点。

        :param header: 标准化后的 initial 头文本。
        :param content_lines: initial 块内部源码行。
        :param next_index: initial 块之后的下一行下标。
        :param allow_parameter_check: 是否允许 legacy 参数检查 initial 降级保留。
        :return: initial 块模型和下一行下标。
        :raises VerilogFormatterError: 当普通 initial 块内部语句不受支持时抛出。
        """

        # list_normalized_lines 保存 initial 正文的非空规范化语句。
        list_normalized_lines = [
            self._normalize_statement_line(line.strip())  # initial 正文规范化行
            for line in content_lines  # initial 原始正文行
            if line.strip()  # 去掉 initial 正文里的空白占位行
        ]  # initial 规范化正文行

        # str_block_kind 区分 legacy 参数检查 initial 和普通 initial。
        str_block_kind = (  # 根据参数检查兼容性选择 initial 输出类别
            "parameter_check"  # legacy 参数检查 initial 可降级保留
            if allow_parameter_check and self._is_parameter_check_initial_text(list_normalized_lines)  # 允许兼容且命中参数检查
            else "initial_block"  # 普通过程块类别
        )  # initial 块分类

        # 控制节点解析失败时，只有 parameter_check 允许降级。
        try:

            # tuple_parse_result 保存控制节点列表和已消费行数。
            tuple_parse_result = self._parse_control_nodes(list_normalized_lines, 0, set(), "initial")  # initial 控制节点解析结果

            # list_nodes 是 initial 内部可结构化渲染的控制节点。
            list_nodes = tuple_parse_result[0]  # initial 控制节点列表

            # int_consumed 校验 parser 是否完整消费 initial 正文。
            int_consumed = tuple_parse_result[1]  # initial 正文已消费行数

            # 未完整消费说明 initial 内部存在不受支持的形态。
            if int_consumed != len(list_normalized_lines):

                # 严格模式阻止部分解析后的危险重排。
                self._raise_control_parse_error(
                    "unsupported_shape",
                    header,
                    "> ERR: [Python] Simplify the initial block so it can be normalized safely.",
                )

            # 参数检查 initial 需要额外验证节点白名单。
            if str_block_kind == "parameter_check":

                # legacy 参数检查只能包含安全的检查节点。
                self._validate_parameter_check_nodes(list_nodes)

            # payload_initial 保存结构化 initial 结果。
            payload_initial = InitialBlock(  # 普通 initial 的结构化输出模型
                header=header,  # 普通 initial 输出头
                lines=list_normalized_lines,  # 控制节点解析过的正文行
                nodes=list_nodes,  # formatter 可重排的控制节点树
                block_kind=str_block_kind,  # 普通 initial 或参数检查分类
            )  # initial 结构化模型

            # 返回 initial 模型和调用方继续扫描的位置。
            return payload_initial, next_index

        # 普通 initial 解析失败时继续暴露原错误。
        except VerilogFormatterError:

            # 非参数检查 initial 不允许降级成空节点。
            if str_block_kind != "parameter_check":

                # 保留原始解析错误，便于调用方看到真实不支持形态。
                raise

            # payload_initial 保存参数检查降级后的 raw-compatible initial。
            payload_initial = InitialBlock(  # 参数检查降级保留的 initial 模型
                header=header,  # 参数检查 initial 输出头
                lines=list_normalized_lines,  # 原样回放的参数检查源码行
                nodes=[],  # 降级路径不提供结构化控制节点
                block_kind=str_block_kind,  # 固定为 parameter_check 分类
            )  # 参数检查降级 initial 模型

            # 返回降级模型和 initial 后的继续扫描位置。
            return payload_initial, next_index

    # generate 内嵌 always 需要转成 control node，供 generate 渲染器统一处理。
    def _parse_nested_always_nodes(self, lines: list[str], start: int) -> tuple[list[ControlNode], int]:
        """
        解析 generate 内部嵌套的 always 块为控制节点列表。

        :param lines: generate 内部源码行列表。
        :param start: nested always 起始行下标。
        :return: always 控制节点列表和下一行下标。
        :raises VerilogFormatterError: 当 nested always 未闭合时抛出。
        """

        # tuple_header_parts 从 generate 内 always 行拆出触发头和可能的同行正文。
        tuple_header_parts = self._split_always_header(  # generate 内 always 的头部拆分结果
            self._normalize_statement_line(lines[start].strip())  # generate 子块中的 always 入口行
        )  # generate 内 always 头与正文片段

        # str_header 保存后续构造 AlwaysBlock 所需的触发头文本。
        str_header = tuple_header_parts[0]  # generate 内 always 触发头

        # str_remainder 是 always 头后仍在同一行的控制语句或 begin 片段。
        str_remainder = tuple_header_parts[1]  # generate 内 always 同行正文片段

        # str_normalized_remainder 用于区分单语句 always 和 begin/end always。
        str_normalized_remainder = (
            self._normalize_statement_line(str_remainder)  # 同行余量规范化文本
            if str_remainder  # nested always 头后存在正文
            else ""  # nested always 头后无同行正文
        )  # generate 内 always 同行正文

        # 同行单语句 always 不需要深度扫描，直接拆成 generate 子节点。
        if str_normalized_remainder and not self._is_begin_header(str_normalized_remainder):

            # list_synthetic_lines 把同行正文放到首位，后续行作为单语句续行候选。
            list_synthetic_lines = [str_normalized_remainder, *lines[start + 1 :]]  # generate always 单语句输入

            # tuple_single_node 返回 generate 场景下单条控制语句的消费范围。
            tuple_single_node = self._parse_single_control_node(list_synthetic_lines, 0, "generate")  # 单语句控制节点边界

            # int_consumed 需要映射回原始 generate 行号的消费数量。
            int_consumed = tuple_single_node[1]  # 同行 generate always 消费行数

            # list_content_lines 是构造 AlwaysBlock 时使用的单语句过程正文。
            list_content_lines = list_synthetic_lines[:int_consumed]  # 单语句 always 真实消费的正文

            # payload_always 复用普通 always 分析，获得触发信息和赋值目标。
            payload_always = self._build_always_payload(str_header, list_content_lines, [])  # 同行 generate always 模型

            # list_split_blocks 把同行 always payload 拆成 generate 内可渲染子块。
            list_split_blocks = self._split_single_always_block(  # 同行 always 拆分后的 generate 子块
                payload_always,  # 同行 always 分析模型
                inside_generate=True,  # 按 generate 内部渲染规则拆分
            )  # 同行 always 子块列表

            # list_control_nodes 把同行 always 子块包装成 generate 控制节点。
            list_control_nodes = [
                ControlNode(kind="always_block", header=block.header, children=block.nodes)  # 同行 always 控制节点
                for block in list_split_blocks  # 同行 always 拆分子块
            ]  # 同行 always 控制节点列表

            # 返回控制节点和映射回 generate 源码的下一行边界。
            return list_control_nodes, start + int_consumed

        # list_block_lines 保存 begin/end 形态 generate always 的完整源码行。
        list_block_lines = (
            [f"{str_header} {str_normalized_remainder}"]  # 同行 begin 还原为完整 always 头
            if str_normalized_remainder  # always 头后带 begin 片段
            else [lines[start]]  # always 头独占一行
        )  # 块状 generate always 源码行

        # str_header 重新以重建后的块首行为准，覆盖同行 begin 拼接差异。
        str_header = self._normalize_statement_line(list_block_lines[0].strip())  # 块状 generate always 头行

        # int_depth 初始值来自首行 begin/end 差值，用于寻找匹配闭合。
        int_depth = len(re.findall(r"\bbegin\b", str_header)) - len(  # 首行 begin/end 造成的初始深度
            re.findall(r"\bend\b", str_header)  # 首行 end 关键字数量
        )  # 块状 always 首行嵌套深度

        # int_index 从 always 头下一行继续收集块体。
        int_index = start + 1  # 块状 always 扫描下标

        # bool_closed 标记首行是否已经完成 begin/end 配对。
        bool_closed = int_depth <= 0  # 块状 always 是否已闭合

        # begin/end 深度归零前，所有行都属于当前 generate always。
        while int_index < len(lines):

            # 当前行先加入源码缓存，保持原顺序。
            list_block_lines.append(lines[int_index])

            # str_normalized_line 用于深度计算和注释跳过。
            str_normalized_line = self._normalize_statement_line(lines[int_index].strip())  # generate always 深度计算行

            # generate 内注释行只保留文本，不改变 always 闭合深度。
            if str_normalized_line.startswith("//"):

                # int_index 跳过注释行继续寻找真正的结构边界。
                int_index += 1  # 注释行后的块扫描下标

                # 注释行只保留文本，不改变闭合状态。
                continue

            # int_depth 按当前行 begin/end 关键字更新嵌套层级。
            int_depth += len(re.findall(r"\bbegin\b", str_normalized_line)) - len(  # 当前块行造成的深度变化
                re.findall(r"\bend\b", str_normalized_line)  # 当前块行中的 end 数量
            )  # 块状 always 当前嵌套深度

            # 深度归零且当前行含 end 时确认闭合。
            if int_depth <= 0 and re.search(r"\bend\b", str_normalized_line):

                # bool_closed 标记已经找到当前 always 的匹配闭合 end。
                bool_closed = True  # 块状 always 已闭合

                # 闭合后停止继续吞并 generate 后续语句。
                break

            # int_index 推进到下一行，继续收集未闭合 always 正文。
            int_index += 1  # 块状 always 下一候选行下标

        # 未闭合的 nested always 不能继续拆分控制节点。
        if int_depth > 0 or not bool_closed:

            # 严格模式报告 generate 内 always 的 begin/end 不平衡。
            self._raise_control_parse_error(
                "generate_normalization_violation",
                str_header,
                "> ERR: [Python] Close each always block inside generate with a matching end before formatting.",
            )

        # list_content_lines 去掉 always 头，保留供控制节点 parser 使用的正文。
        list_content_lines = list_block_lines[1:]  # 块状 generate always 正文行

        # 末行只有 end 时不进入控制节点正文。
        if list_content_lines and self._normalize_statement_line(list_content_lines[-1].strip()) == "end":

            # list_content_lines 删除闭合 end 行，只保留过程语句。
            list_content_lines = list_content_lines[:-1]  # 块状 always 去尾正文

        # str_raw_block 组合 always 头和正文，供左值分析器提取目标信号。
        str_raw_block = "\n".join([str_header, *list_content_lines])  # 块状 generate always 完整文本

        # list_lvalues 保存 generate always 中的赋值左值对象。
        list_lvalues = self._extract_lvalues_from_text(str_raw_block, "lvalue_normalization_violation")  # 块状 always 左值对象

        # payload_always 保存 begin/end generate always 的触发信息和控制节点。
        payload_always = self._analyze_always_block(  # 拆成 generate 控制节点前的 always payload
            str_header,  # 嵌套过程块的触发头
            list_content_lines,  # 去掉外层 end 后的 always 正文
            self._collect_unique_lvalue_bases(list_lvalues),  # 该 always 写入的目标信号基名
            str_raw_block,  # 左值和控制节点分析使用的完整文本
            lvalues=list_lvalues,  # 已解析的赋值左值对象
        )  # 块状 generate always 分析模型

        # list_split_blocks 保存 generate 内部需要渲染的 always 控制块。
        list_split_blocks = self._split_single_always_block(payload_always, inside_generate=True)  # 块状 always 子块

        # list_control_nodes 把块状 always 拆分结果映射成 generate 控制节点。
        list_control_nodes = [
            ControlNode(kind="always_block", header=block.header, children=block.nodes)  # 块状 always 控制节点
            for block in list_split_blocks  # 块状 always 拆分子块
        ]  # 块状 always 控制节点列表

        # 返回 generate always 控制节点和闭合 end 后一行。
        return list_control_nodes, int_index + 1
